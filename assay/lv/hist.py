"""lv/hist.py —— 实盘的【每日持仓】与【交易记录】。

用户 2026-09-15："发现实盘功能反而没有交易记录、每日持仓，实盘的信息
不应该比回测少。" —— 是。对照下来实盘缺两块：

    回测「持仓」页      每日快照、逐日回看        实盘只有**当前**持仓
    回测「交易记录」页  一笔平仓的收益率/盈亏/原因  实盘只有成交流水（录入视角）

🔴 **两块数据本来就有，只是没人把它展开。**
  `equity_curve` 已经在逐日 `lots_asof(rows, d)` 重放持仓了，只是把逐只
  明细扔掉、只留了合计。成交流水也一直在账本里，缺的是"按 FIFO 配对成
  一笔往返"这一层。所以这里**一个新数据源都不引**。

## 🔴 与回测的口径差异，必须在页面上说出来

| | 回测 | 实盘 |
|---|---|---|
| 成交价 | 引擎撮合价（开盘价 ± 滑点） | **你真实成交的价** |
| 费用 | 按 `Cost` 模型算 | **账单上的真实费用**（或按账户费率估） |
| 份额/价格 | 后复权记账 -> 展示层换算 | 本来就是真实股数 + 不复权价 |
| 持仓估值 | 面板收盘 | 面板收盘（盘中那天用实时价，页面标出来） |

★ 所以两边的"收益率"不是同一个数，**不该硬凑成一样**：回测的 `ret` 只含
  滑点不含佣金（与引擎 `entry_price` 同口径），实盘这边给的是**含费**的
  真实收益率。页面各自标清楚，比强行对齐更有用。

## 🔴 FIFO 配对只做【展示】，不碰账本

`pos.fifo_lots` 是实盘持仓的权威实现（冲正、部分卖出、T+1 都在里面）。
这里**复用它的批次结构**再按卖出切分，绝不另写一套 FIFO ——
两份 FIFO 分叉的表现是"成本价对不上"，而那会一路错到止损判定。
"""

import datetime

from . import base as _base
from . import pos as _pos
from . import px as _px


def _feed(rows, flows, datalake=None):
    """与 `perf.equity_curve` 同一套：从首笔成交日起的交易日轴 + 收盘价表。

    ★ 抽出来是为了让「每日持仓」与「权益曲线」**逐日对得上** ——
      各建一份日期轴的话，某一天多一根少一根都不会报错，
      只是两页对同一天给出不同的持仓（同「同一件事两处写」那条）。
    """
    from ..feed import PanelFeed
    if not rows and not flows:
        return None, [], {}
    d0 = min([_base._d(r['trade_date']) for r in rows] +
             [_base._d(f['date']) for f in flows])
    feed = PanelFeed((d0 - datetime.timedelta(days=10)).isoformat(),
                     datetime.date.today().isoformat(), root=datalake)
    days = [d for d in feed.trading_days if d >= d0]
    codes = sorted({r['code'] for r in rows})
    px = {}
    if codes and days:
        q = "','".join(codes)
        for c, dd, p in feed.con.execute("""
            SELECT jq_code, date, close_bfq
            FROM read_parquet('%s/mart/panel_daily/panel_*.parquet')
            WHERE jq_code IN ('%s') AND date >= DATE '%s'
        """ % (feed.root, q, days[0])).fetchall():
            px[(c, dd)] = p
    return feed, days, px


def daily_holdings(aid, datalake=None, offset=0, limit=100):
    """每日持仓快照（逐只）。**日期倒序**，与回测「持仓」页一致。

    🔴 停牌按**最后已知价**挂账（同 `equity_curve`）—— 直接跳过的话那一天
      的市值凭空少一块，而它不报错，只是权益曲线上出现一个坑。
    ★ `weight` 的分母是**当日总权益**（持仓市值 + 现金），不是持仓市值 ——
      后者会让满仓与半仓看起来一样（都 100%），而仓位正是要看的东西。
    """
    acct = _base.get_account(aid)
    rows = _base.fills(aid)
    flows = _pos.cashflows(aid)
    init = float(acct.get('init_cash') or 0)
    feed, days, px = _feed(rows, flows, datalake)
    if not days:
        return {'total': 0, 'offset': 0, 'limit': 0, 'rows': [], 'n_days': 0,
                'note': '还没有成交'}
    # ★ `names_of` 在 `lv/px.py`（不是 base）；参数是 `datalake` 不是 `root`
    #   —— 这两个我都按印象写错过一次，而错了不报错、只是整列名称是空的
    #   （同「凭印象假设接口形状之前先确认」那条）。
    names = _px.names_of(sorted({r['code'] for r in rows}), datalake=datalake)
    out, last = [], {}
    for d in days:
        book = _pos.lots_asof(rows, d)
        if not book:
            continue
        cash = _pos.cash_asof(init, rows, d, flows)
        day_rows, mv = [], 0.0
        for c, lots in sorted(book.items()):
            p = px.get((c, d))
            stale = p is None
            if stale:
                p = last.get(c)
            else:
                last[c] = p
            if p is None:
                continue
            sh = sum(l['shares'] for l in lots)
            # 成本：**含买入费的摊薄成本**（与实盘持仓页同口径）——
            # 页面上那个"浮盈"就是按它算的，两处不一致会自相矛盾。
            cost_amt = sum(l['shares'] * l['price'] + (l.get('fee') or 0)
                           for l in lots)
            val = sh * p
            mv += val
            day_rows.append({
                'date': d.isoformat(), 'code': c, 'name': names.get(c),
                'shares': sh, 'last_price': round(p, 4), 'value': round(val, 2),
                'cost': round(cost_amt / sh, 4) if sh else None,
                'entry_date': min(str(l['date'])[:10] for l in lots),
                'unrealized_pnl': round(val - cost_amt, 2),
                'unrealized_ret': (round(val / cost_amt - 1, 6)
                                   if cost_amt else None),
                # 🔴 停牌那天要**标出来**：价格是上一个交易日的，不标的话
                #   人会以为"这只票今天没动"，而事实是没有数据。
                'stale_price': stale,
            })
        eq = mv + cash
        for r in day_rows:
            r['weight'] = round(r['value'] / eq, 6) if eq else None
            r['cash'] = round(cash, 2)
            r['equity'] = round(eq, 2)
        out.extend(day_rows)
    # 日期倒序（最近的先看）+ 同日内权重降序 —— 与回测「持仓」页一致
    out.sort(key=lambda r: (r['date'], r.get('weight') or 0), reverse=True)
    total = len(out)
    limit = max(10, min(500, int(limit or 100)))
    offset = max(0, min(max(total - 1, 0), int(offset or 0)))
    return {'total': total, 'offset': offset, 'limit': limit,
            'rows': out[offset:offset + limit],
            'n_days': len({r['date'] for r in out})}


def round_trips(aid, datalake=None, offset=0, limit=100):
    """交易记录：按 **FIFO 把买入与卖出配成一笔往返**，带收益率/盈亏/持有天。

    🔴 **与「成交流水」是两回事，不是换个样子。**

        成交流水  录入视角：那天我买了/卖了什么、多少钱、费用多少
        交易记录  往返视角：这一笔**赚了多少**、持有多久、为什么卖

      流水回答"做了什么"，往返回答"做得怎么样"。回测有后者（`trades.parquet`），
      实盘一直只有前者 —— 这正是用户说的"实盘的信息比回测少"。

    🔴 **收益率含费**（与回测那边不同，页面上要标）：
      回测的 `ret` 只含滑点不含佣金（为了与引擎 `entry_price` 同口径），
      而实盘这边买卖两侧的真实费用都在账本里，不含进去就是**报高**。
      两边不该硬凑成一样，各自标清楚更有用。

    ★ FIFO 的批次结构复用 `pos.fifo_lots`（冲正、部分卖出都在里面），
      这里只按卖出把它切开 —— 绝不另写一套 FIFO。
    """
    # 🔴 排序键**照抄 `pos.fifo_lots`**：`(trade_date, ts, 行序)`。
    #   同一天同一秒的多笔要有确定顺序，否则 FIFO 配对不可复现；
    #   tiebreak 用文件里的插入顺序，**不能用 uid**（随机 hex，同秒的买卖
    #   会随机翻转）。两处 FIFO 用不同的序 = 同一本账配出两套往返。
    rows = [r for _i, r in sorted(enumerate(_pos.active_fills(_base.fills(aid))),
                                  key=lambda t: (t[1]['trade_date'],
                                                 t[1].get('ts') or '', t[0]))]
    names = {}
    lots, trips = {}, []
    for r in rows:
        c = r['code']
        if r.get('name'):
            names.setdefault(c, r['name'])
        sh = float(r.get('shares') or 0)
        px = float(r.get('price') or 0)
        fee = float(r.get('fee') or 0)
        if sh <= 0:
            continue
        if r['side'] == 'buy':
            lots.setdefault(c, []).append(
                {'shares': sh, 'price': px, 'fee': fee,
                 'date': str(r['trade_date'])[:10]})
            continue
        # 卖出：按 FIFO 逐批切
        left, sell_fee_left = sh, fee
        book = lots.get(c) or []
        while left > 1e-9 and book:
            lot = book[0]
            use = min(lot['shares'], left)
            frac_l = use / lot['shares'] if lot['shares'] else 0
            frac_s = use / sh if sh else 0
            buy_fee = lot['fee'] * frac_l
            sell_fee = fee * frac_s
            gross = use * px
            cost = use * lot['price']
            trips.append({
                'code': c, 'name': names.get(c),
                'entry_date': lot['date'],
                'exit_date': str(r['trade_date'])[:10],
                'shares': round(use, 4),
                'entry_price': round(lot['price'], 4),
                'exit_price': round(px, 4),
                'gross_amount': round(gross, 2),
                'fee': round(buy_fee + sell_fee, 2),
                # 🔴 **含费**：买入费摊进成本、卖出费从收入里扣。
                #   不含的话是报高，而"报高"在实盘上是最危险的方向。
                'pnl': round(gross - sell_fee - (cost + buy_fee), 2),
                'ret': (round((gross - sell_fee) / (cost + buy_fee) - 1, 6)
                        if (cost + buy_fee) else None),
                'holding_days': (_base._d(str(r['trade_date'])[:10])
                                 - _base._d(lot['date'])).days,
                'reason': r.get('note') or r.get('source') or '',
            })
            lot['shares'] -= use
            lot['fee'] -= buy_fee
            left -= use
            if lot['shares'] <= 1e-9:
                book.pop(0)
        # ★ 卖得比账本里的多：**不静默吞掉**。正常情况下 add_fill 的重放校验
        #   已经挡住了负持仓，走到这里说明账本被别的路径写过。
        if left > 1e-9:
            trips.append({
                'code': c, 'name': names.get(c), 'entry_date': None,
                'exit_date': str(r['trade_date'])[:10], 'shares': round(left, 4),
                'entry_price': None, 'exit_price': round(px, 4),
                'gross_amount': round(left * px, 2), 'fee': None, 'pnl': None,
                'ret': None, 'holding_days': None,
                'reason': '🔴 没有对应的买入批次（账本可能被绕过写入）',
            })
    # 🔴 名称**从面板补**，不只依赖账本里那一列 —— 批量粘贴录进来的成交
    #   只有代码（`live.names_of` 本来就是为这个而在），不补的话整列是空的。
    #   ★ 按**平仓日**解析：同一只票改过名（如变 *ST）时显示当时那个名字。
    miss = sorted({t['code'] for t in trips if not t.get('name')}
                  | {o['code'] for o in []})
    if miss:
        got = _px.names_of(miss, datalake=datalake)
        for t in trips:
            if not t.get('name'):
                t['name'] = got.get(t['code'])
    trips.sort(key=lambda t: (t['exit_date'], t['code']), reverse=True)
    total = len(trips)
    limit = max(10, min(500, int(limit or 100)))
    offset = max(0, min(max(total - 1, 0), int(offset or 0)))
    # 未平仓的那些**也要给**（回测的 trades.parquet 恰恰没有它们）——
    # 「我现在拿着什么、浮盈多少」在实盘里是天天要看的。
    open_pos = []
    for c, book in lots.items():
        for lot in book:
            if lot['shares'] > 1e-9:
                open_pos.append({'code': c,
                                 'name': names.get(c) or (
                                     _px.names_of([c], datalake=datalake) or {}
                                 ).get(c),
                                 'entry_date': lot['date'],
                                 'shares': round(lot['shares'], 4),
                                 'entry_price': round(lot['price'], 4)})
    return {'total': total, 'offset': offset, 'limit': limit,
            'rows': trips[offset:offset + limit],
            'n_open': len(open_pos), 'open': open_pos}
