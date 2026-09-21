"""lv/perf.py —— 权益与收益：TWR / 年化 / 回撤 / 当日盈亏 / 费用拖累。

🔴 TWR 的起点是【开户那一刻的资金】，不是第一天的收盘；
  当日现金流按【区间】取，不是"正好落在那天"（入金落在周末会被漏掉，
  而漏掉的入金会被当成收益）。"""
import datetime
import io
import os
import re
from ..feed import PanelFeed

from . import base as _base
from . import tdx as _tdx
from . import px as _px
from . import fee as _fee
from . import pos as _pos
from . import sig as _sig

# 基准 symbol 白名单：sh/sz + 6 位数字。**只认这个形状** ——
# 它会拼进 SQL 字符串，放开就是注入口（同 query.py 那三层防线）。
_SYM_RE = __import__('re').compile(r'^(sh|sz)\d{6}$')


from assay import symbols as _SYM   # 取价/取名的唯一正本


def _last_px(feed, codes, day):
    """{code: (最新收盘, 那天的日期, 那天的昨收)}，按 <= day 取最近一条。

    🔴 **只转发**给唯一正本 `symbols.last_px`（2026-09-21）。此前
      「查面板 -> 缺的回落 tdx」这个模式在四个取价函数里各写了一遍，
      而它出过两次事（ETF 市值 0 / 浮盈 −100%，修 4 处还漏 3 处）——
      每加一个标的类别就要记得改 N 处，**漏掉的那处不报错**。
    """
    if not codes:
        return {}
    return _SYM.last_px(feed.root, codes, day, con=feed.con)



def positions_valued(aid, datalake=None):
    """持仓 + 逐只估值 + 汇总。**页面主视图用的就是它。**

    ★ 价格不再取自"今天的信号" —— 信号只覆盖它当天关心的票，
      不在信号里的持仓就没有价格（原页面那一版正是这样，浮盈显示 —）。
      这里按最新数据日独立取价，覆盖全部持仓。

    ★ 成本给【三个】，因为它们回答的是三个不同问题：
        · `cost`       成交均价 —— 喂给引擎 entry_price 的那个数（止损/吊灯
                       /红利税档位读它）。也是能与回测 trades.ret 对照的口径
                       （引擎的 ret 只含滑点、不含佣金）。
        · `cost_net`   **摊薄成本** = (成交额 + 买入费) / 股数。
                       券商 App 说的"摊薄成本价"就是这个，**浮盈按它算** ——
                       费用是真金白银出去了，不算进成本等于把浮盈报高。
        · `breakeven`  保本价 = 摊薄成本 / (1 − 卖出费率)。卖到这个价才不亏。

    ★ 浮盈（`pnl`）按**摊薄成本**算，含买入费；再扣估算卖出费就是
      `pnl_net`（全平落袋）。两者相减不会重复计费 —— 买入费只在成本里出现
      一次，卖出费只在 pnl_net 里出现一次。

    ★ 都不含【已收分红】：broker 里 entry_price 在除权时不缩，就是为了让
      价差与分红分开记。分红到账走 cashflows。
    """
    book = _pos.fifo_lots(_base.fills(aid))
    money = _pos.cash(aid)
    out = {'items': [], 'cash': round(money, 2), 'market_value': 0.0,
            'pnl': 0.0, 'cost': 0.0, 'equity': round(money, 2), 'asof': None}
    if not book:
        return out
    feed = PanelFeed('2026-01-01', datetime.date.today().isoformat(),
                     root=datalake)
    day = feed.trading_days[-1]
    out['asof'] = day.isoformat()
    px = _last_px(feed, list(book), day)
    nm = _sig._names(feed, list(book), day)
    # ★ 盘中用【实时价】覆盖日线收盘 —— 这样持仓盈亏是活的。
    #   取不到就用日线收盘（不是留空）：实时是锦上添花，
    #   而"没有实时价"不该让整个持仓表变成 —。
    #   来源逐只标出来（rt_src / rt_at），页面必须显示 —— 人分不清
    #   "实时"和"昨收"的话，那是两个数量级的误解。
    rtp = {}
    try:
        from assay import realtime as _rt
        rtp = _rt.latest(list(book), root=datalake)
    except Exception:                                       # noqa: BLE001
        rtp = {}
    mv = cst = cst_net = 0.0
    for c, lots in sorted(book.items()):
        sh = sum(l['shares'] for l in lots)
        avg = sum(l['shares'] * l['price'] for l in lots) / sh
        buy_fee = sum(l.get('fee') or 0 for l in lots)
        avg_net = (sh * avg + buy_fee) / sh          # 摊薄成本
        p, pd_, pc = px.get(c, (None, None, None))
        rt_src = rt_at = None
        r = rtp.get(c)
        if r and r.get('price'):
            p, rt_src, rt_at = r['price'], r.get('src') or 'rt', r.get('at')
            # 🔴 盘中的「昨收」不能用面板那一行的 preclose —— 那是**面板
            #   那一天**的昨收（面板到 09-02 时它是 09-01 收盘），而实时价
            #   是 09-03 的，基准差一天。正确基准：快照自带的 preclose，
            #   退一步用面板最新那天的**收盘**（= 今天的昨收）。
            pc = r.get('preclose') or (px.get(c, (None,))[0])
        v = (sh * p) if p else None
        pnl = (v - sh * avg_net) if v is not None else None
        # ★ 当日盈亏 = Σ 每一批 × (现价 − 基准)，基准分两种：
        #     今天买的那批 -> 它自己的成交价（今天开盘时还没持有它，
        #       拿昨收当基准会把"买入那一刻到现在"之外的涨幅也算进当日）
        #     以前买的     -> 昨收
        #   这是券商 App 的算法。按整只票统一用昨收算的话，建仓当天
        #   那只票的当日盈亏会凭空多出一个开盘涨幅，而它不报错。
        today = (rt_at[:10] if rt_at else (pd_.isoformat() if pd_ else None))
        pnl_day = None
        if p:
            acc, ok = 0.0, True
            for l in lots:
                base = (l['price'] if (today and l['date'].isoformat() == today)
                        else pc)
                if base is None:
                    ok = False
                    break
                acc += l['shares'] * (p - base)
            pnl_day = round(acc, 2) if ok else None
        out['items'].append({
            'rt_src': rt_src, 'rt_at': rt_at,
            'preclose': (round(pc, 3) if pc else None),
            'chg_day': (round(p / pc - 1, 6) if p and pc else None),
            'pnl_day': pnl_day,
            'code': c, 'name': nm.get(c, ''), 'shares': sh,
            'cost': round(avg, 4),                  # 成交均价（引擎口径）
            'cost_net': round(avg_net, 4),          # 摊薄成本（含买入费）
            'buy_fee': round(buy_fee, 2),
            'price': (round(p, 3) if p else None),
            'px_date': (pd_.isoformat() if pd_ else None),
            'stale': bool(pd_ and pd_ != day),          # 停牌：价格不是当日的
            'value': (round(v, 2) if v is not None else None),
            'pnl': (round(pnl, 2) if pnl is not None else None),
            'pnl_pct': (round(p / avg_net - 1, 6) if p and avg_net else None),
            # 与回测 trades.ret 对照用的那个（只含滑点、不含佣金）
            'ret_gross': (round(p / avg - 1, 6) if p and avg else None),
            'entry': min(l['date'] for l in lots).isoformat(),
        })
        cst += sh * avg
        cst_net += sh * avg_net
        if v is not None:
            mv += v
    for it in out['items']:
        it['weight'] = (round(it['value'] / (mv + money), 6)
                        if it['value'] is not None and (mv + money) else None)
    # ★ 浮盈是【纯价差】：成本取成交价，不含买入费；也没预留卖出费。
    #   这是刻意的 —— 与引擎 trades 的 ret 口径一致（broker 里 entry_price
    #   在除权时不缩，就为了让 pnl 只反映价差）。改了它，实盘与回测就没法比。
    #   但"现在全平能落袋多少"是个真问题，所以另算一个【估算卖出费】：
    #   按当前价、账户当期费率、卖出方向（含印花税）逐只估。
    #   ⚠ 已付的买入费不在这里 —— 它已经从现金里扣过、已体现在权益和
    #     账户累计收益（TWR）里。再减一次就是**重复计费**。
    fee_m = _fee.fee_model_at(aid, day)
    exit_fee = 0.0
    for it in out['items']:
        if it['price'] is None:
            it['exit_fee_est'] = it['breakeven'] = None
            continue
        try:
            f = _fee.estimate_fee('sell', it['shares'], it['price'], day, fee_m,
                             it['code'])
        except _base.LiveError:
            f = None
        it['exit_fee_est'] = (round(f, 2) if f is not None else None)
        # 保本价：卖出费率按【当前市值】折算（最低佣金 binding 时费率更高，
        # 所以这个数随价格变 —— 它是估算，不是常数）
        rate = (f / (it['shares'] * it['price'])) if f else 0.0
        it['breakeven'] = (round(it['cost_net'] / (1 - rate), 3)
                           if rate < 0.5 else None)
        if f:
            exit_fee += f
    out['exit_fee_est'] = round(exit_fee, 2)
    # 当日盈亏汇总：有一只算不出来（无价 / 无昨收）就整体给 None ——
    # 少了一只的合计看着像个正常数字，而它是错的。
    dv = [x['pnl_day'] for x in out['items']]
    out['pnl_day'] = (round(sum(dv), 2) if dv and all(x is not None for x in dv)
                      else None)
    out['market_value'] = round(mv, 2)
    out['cost'] = round(cst, 2)                 # 成交额（不含费）
    out['cost_net'] = round(cst_net, 2)         # 摊薄成本额（含买入费）
    out['buy_fee'] = round(cst_net - cst, 2)
    # ★ 浮盈按【摊薄成本】算 —— 含买入费。这是券商 App 的口径，也是
    #   "我到底赚没赚"的口径。原来按成交额算，等于把浮盈报高了买入费那么多。
    out['pnl'] = round(mv - cst_net, 2)
    out['pnl_pct'] = round(mv / cst_net - 1, 6) if cst_net else None
    # 与回测 trades.ret 对照用的（只含滑点、不含佣金）
    out['pnl_gross'] = round(mv - cst, 2)
    # 全平落袋 = 浮盈 − 估算卖出费。买入费已在浮盈里，不会重复。
    out['pnl_net'] = round(mv - cst_net - exit_fee, 2)
    # 买入费里有多少是【估算】的 —— 摊薄成本跟着就是估算，要标出来
    out['fee_estimated_n'] = sum(
        1 for r in _pos.active_fills(_base.fills(aid))
        if r['side'] == 'buy' and r.get('fee_estimated'))
    # 汇总层也要说清"这份估值用的是什么价"
    n_rt = sum(1 for x in out['items'] if x.get('rt_src'))
    out['rt_n'] = n_rt
    out['rt_at'] = max([x['rt_at'] for x in out['items'] if x.get('rt_at')] or
                       [None])
    out['price_src'] = ('实时' if n_rt == len(out['items']) and n_rt
                        else ('部分实时' if n_rt else '收盘'))
    # ★ asof 是"这份估值用的是哪天/哪一刻的价"。全部实时时要写实时那一刻 ——
    #   还写日线日的话，页面上会出现"估值日 09-02"配着 09-03 的实时价，
    #   而人会以为看的是昨天的数。
    out['asof_close'] = out['asof']
    if n_rt and out['rt_at']:
        out['asof'] = out['rt_at']
    out['equity'] = round(mv + money, 2)
    return out



def equity_curve(aid, datalake=None):
    """逐日权益曲线 + 收益统计。

    权益 = 现金(as-of) + 持仓按当日**不复权收盘价**估值。
    停牌当日无行情时按最后已知价挂账 —— 与引擎 broker 的做法一致。

    ★ 收益用**时间加权（TWR）**，不是 `期末/期初 - 1`。
      有入金出金时后者是错的：入金 5 万会让"收益"凭空变大，
      而那不是你赚的。TWR 在每个有外部现金流的日子把区间切开：
          r_t = (E_t − F_t) / E_{t−1} − 1        F_t = 当日净入金
      再连乘。这也是能和回测年化直接比的那个口径。

    ★ 已冲正的成对记录不参与（走 active_fills）—— 否则一笔"没发生"的交易
      会在曲线上留下一个凭空的台阶。
    """
    acct = _base.get_account(aid)
    rows = _pos.active_fills(_base.fills(aid))
    flows = _pos.cashflows(aid)
    init = float(acct.get('init_cash') or 0)
    if not rows and not flows:
        return {'dates': [], 'equity': [], 'stats': None,
                'note': '还没有成交或现金流水'}
    d0 = min([_base._d(r['trade_date']) for r in rows] +
             [_base._d(f['date']) for f in flows])
    feed = PanelFeed((d0 - datetime.timedelta(days=10)).isoformat(),
                     datetime.date.today().isoformat(), root=datalake)
    days = [d for d in feed.trading_days if d >= d0]
    if not days:
        return {'dates': [], 'equity': [], 'stats': None,
                'note': '首笔成交日晚于最新行情日'}
    codes = sorted({r['code'] for r in rows})
    # ★ 取价走 `px.daily_close_map` **一处** —— 与「每日持仓」共用
    #   （它们必须逐日给出同一个总资产，而原来是抄的两份）。
    px = _px.daily_close_map(feed, codes, days[0])
    last = {}
    # 🔴🔴 **分红【不是】外部现金流 —— 它是投资收益。**（2026-09-14 修）
    #   TWR 的做法是"在每个有外部现金流的日子把区间切开"，目的是让
    #   入金/出金不被当成收益。而分红到账走的也是 `cashflows.jsonl`
    #   （账本设计如此：它是个事件，不该改 init_cash），于是原来这里把它
    #   一并当成了外部资金 ——
    #
    #     除权日：股价掉下去 -> 权益跌 -> TWR 记一笔【负收益】
    #     到账日：现金加回来 -> 被当成"入金" -> **不计入收益**
    #
    #   一来一回，分红那部分收益被**扣掉两次**。实测模拟盘红利账户：
    #   期末/起点−1 = 5.83%，而 TWR 只有 4.39% —— 差 1.44pp，
    #   恰好是分红 7,876 / 500,000 = 1.575% 那个量级。
    #   ★ 这是**实盘也有的 bug**，只是当前两个真实账户还没收到过分红，
    #     所以一直没暴露；是模拟盘按引擎跑出分红之后才把它逼出来的。
    #   ★ `adjust`（手工调整）仍算外部：它的语义就是"这笔钱不是交易来的"。
    FLOW_KINDS = ('deposit', 'withdraw', 'adjust')
    flow_by_day = {}
    for f in flows:
        if (f.get('kind') or 'deposit') not in FLOW_KINDS:
            continue                    # 分红：进现金，但不当外部资金
        flow_by_day[_base._d(f['date'])] = flow_by_day.get(_base._d(f['date']), 0.0) \
            + float(f.get('signed') or 0)

    dates, eq, twr = [], [], []
    # 🔴 起点是【开户那一刻的资金】，不是第一天的收盘。
    #   原来 prev_e 从 None 起，于是**第一天的收益整个丢了**：红利 09-01 建仓
    #   当天收盘 1,011,598（+1.16%），TWR 却从 09-02 才开始连乘，
    #   算出 −0.78%，而实际净值是 1,003,698（+0.37%）——
    #   页面上就成了"持仓浮盈 +3,698 / 累计收益 −0.78%"自相矛盾，**且不报错**。
    prev_e = init
    # ★ 当日现金流 F_t 取【上一条曲线日之后、到今天为止】的所有流水，
    #   不是"正好落在今天那天"的 —— 入金落在周末/节假日时，
    #   按精确日期取会漏掉它，而漏掉的入金会被当成收益（TWR 就白做了）。
    prev_d = None
    for d in days:
        book = _pos.lots_asof(rows, d)
        mv = 0.0
        for c, lots in book.items():
            p = px.get((c, d))
            if p is None:
                p = last.get(c)            # 停牌：按最后已知价挂账
            else:
                last[c] = p
            if p is None:
                continue
            mv += sum(l['shares'] for l in lots) * p
        e = _pos.cash_asof(init, rows, d, flows) + mv
        f = sum(v for fd, v in flow_by_day.items()
                if fd <= d and (prev_d is None or fd > prev_d))
        if prev_e is not None and prev_e > 0:
            twr.append((e - f) / prev_e - 1.0)
        prev_e, prev_d = e, d
        dates.append(d.isoformat())
        eq.append(round(e, 2))

    # ---- 盘中：给曲线补【今天】这一点 -------------------------------------
    # 🔴 不补的话整块业绩指标停在昨收，而上面的总资产/持仓浮盈是实时的 ——
    #    同一屏里两个口径，"累计收益 +0.74%" 配 "当日盈亏 +1,711" 对不上,
    #    而这不报错。
    # ★ 判据是"确实有实时价"（n_rt >= 1）：没有就不补 —— 补一个与昨收相同的
    #   点等于凭空多出一个 0% 交易日，会把年化和回撤都稀释掉。
    # ★ 面板已经有今天了（收盘后同步过）就不补：那一点是权威的日线，
    #   实时价只是它的近似。
    intraday = None
    try:
        from assay import realtime as _rt
        rtp = _rt.latest(codes, root=datalake) if codes else {}
        rtp = {c: v for c, v in rtp.items() if (v or {}).get('price')}
        at = max([v.get('at') for v in rtp.values() if v.get('at')] or [None])
        d_now = (datetime.date.fromisoformat(at[:10]) if at else None)
        if rtp and d_now and days and d_now > days[-1]:
            book = _pos.lots_asof(rows, d_now)

            mv, n_rt = 0.0, 0
            for c, lots in book.items():
                r = rtp.get(c)
                p = (r or {}).get('price') or last.get(c) or px.get((c, days[-1]))
                if r and r.get('price'):
                    n_rt += 1
                if p is None:
                    continue
                mv += sum(l['shares'] for l in lots) * p
            if n_rt:
                e = _pos.cash_asof(init, rows, d_now, flows) + mv
                f = sum(v for fd, v in flow_by_day.items()
                        if fd <= d_now and (prev_d is None or fd > prev_d))
                if prev_e is not None and prev_e > 0:
                    twr.append((e - f) / prev_e - 1.0)
                dates.append(d_now.isoformat())
                eq.append(round(e, 2))
                intraday = {'at': at, 'n_rt': n_rt, 'n_pos': len(book)}
    except Exception:                                       # noqa: BLE001
        intraday = None                 # 实时补点失败不该让整块业绩打不开

    cum = 1.0
    for r in twr:
        cum *= (1.0 + r)
    n = len(dates)
    # ★ 峰值从【起点资金】起算 —— 第一天就跌的话，回撤该从起点量,
    #   只看曲线上的点会把第一天的下跌算成"没有回撤"。
    peak, mdd, mdd_at = init, 0.0, None
    for d, v in zip(dates, eq):
        peak = max(peak, v)
        if peak > 0 and 1.0 - v / peak > mdd:
            mdd, mdd_at = 1.0 - v / peak, d
    yrs = n / 244.0
    stats = {
        'days': n,
        'start': dates[0] if dates else None,
        'end': dates[-1] if dates else None,
        'equity_end': eq[-1] if eq else None,
        'twr': round(cum - 1.0, 6),
        # ★ 不到 20 个交易日不给年化：把两周的收益乘 12 倍是**误导**，
        #   而那个数会被拿去跟回测年化比。宁可显示"—"。
        'twr_annual': round(cum ** (1.0 / yrs) - 1.0, 6) if yrs > 0.08 else None,
        'max_drawdown': round(mdd, 6),
        'max_drawdown_at': mdd_at,
        # 当前回撤：距历史最高还差多少。最大回撤是历史，这个是现在。
        'drawdown_now': round(1.0 - eq[-1] / peak, 6) if peak > 0 else None,
        # 最近一个交易日的涨跌（已剔除当日现金流，与 TWR 同口径）
        'day_ret': round(twr[-1], 6) if twr else None,
        'day_pnl': (round(eq[-1] - eq[-2] - flow_by_day.get(_base._d(dates[-1]), 0.0), 2)
                    if len(eq) >= 2 else None),
        'net_deposit': round(sum(flow_by_day.values()), 2),
        'init_cash': init,
        # 起点资金（开户那一刻）—— TWR 的第一个分母，页面上"累计收益"
        # 那一格的金额就是 期末 − 起点 − 净入金。
        'equity_start': round(init, 2),
        # 已付手续费。**折成年化拖累要够长的样本才有意义** —— 见下。
        'fee_paid': round(sum(float(r.get('fee') or 0) for r in rows), 2),
        # 最后一点是不是盘中估的（None = 全是收盘价）。页面必须标出来：
        # "累计收益"含不含今天的浮动，是两个不同的数。
        'intraday': intraday,
    }
    # 费用占净投入的比例（不用权益做分母：权益含浮盈，会低估拖累）
    base = init + stats['net_deposit']
    stats['fee_pct'] = (round(stats['fee_paid'] / base, 6) if base > 0 else None)
    # 🔴 年化拖累 = 按【当前交易频率】外推一年，费用会吃掉年化几个点。
    #   与 twr_annual 同一条纪律：不到 20 个交易日不给 —— 开户两天就把
    #   两笔建仓的费用乘 122 倍，得到的"年化拖累 1.72%"纯属外推，
    #   而它会被拿去跟真实费率比。宁可只报绝对值。
    # ★ 累计收益【也要给金额】：百分比是 TWR（入金不算收益），金额是
    #   期末 − 起点 − 净入金 = 真正多出来的钱。只给百分比的话，
    #   "累计收益 −0.78%" 旁边就没有能和"持仓浮盈 +3,698"对上的数。
    #   🔴 两者分母不同是**正常的**：TWR 按整段资金的时间加权算，
    #     而 浮盈% 的分母是投出去的那部分（闲置现金会摊薄前者）。
    stats['pnl_total'] = round(eq[-1] - init - stats['net_deposit'], 2) if eq \
        else None
    stats['fee_drag_annual'] = (
        round(stats['fee_pct'] * (244.0 / n), 6)
        if stats['fee_pct'] is not None and yrs > 0.08 else None)
    # ★ 逐日 TWR 净值 + 逐日收益率 —— 业绩页要画"收益曲线"与年月日收益表。
    #   🔴 **不能拿 `equity` 当收益曲线**：它是总资产（含入金），
    #     入金那天会凭空跳一截，看着像赚了一笔（TWR 存在的全部理由）。
    #     所以净值单独给一条，与 `twr` 同口径、逐日累乘。
    #   ★ 在服务端算：现金流在这里、切区间的规则也在这里。
    #     前端拿 equity 自己推的话就是第二份 TWR 实现，迟早分叉。
    nav, _c = [], 1.0
    for r in twr:
        _c *= (1.0 + r)
        nav.append(round(_c, 8))
    # ★ 逐日**金额**收益：Δ权益 − 当日净现金流。
    #   🔴 不能直接用 `eq[i] - eq[i-1]` —— 入金那天会多出一大截，
    #     那不是赚的（与 twr 剔除现金流是同一条纪律，只是这里保留金额）。
    #   ★ 第一天的基准是**起点资金**（开户那一刻），不是它自己的收盘 ——
    #     否则建仓日的盈亏又被丢掉（CLAUDE.md 记过的同一个坑）。
    #   🔴 循环 **dates/eq**（最终序列），不是 `days` ——
    #     盘中补点那一支会给 dates/eq/twr 各加一个点，
    #     循环 days 会少一个，于是页面上最后一天没有金额（对齐断言抓到过）。
    pnls, _pe, _pd = [], init, None
    for i, ds in enumerate(dates):
        d = _base._d(ds)
        f = sum(v for fd, v in flow_by_day.items()
                if fd <= d and (_pd is None or fd > _pd))
        pnls.append(round(eq[i] - _pe - f, 2))
        _pe, _pd = eq[i], d
    return {'dates': dates, 'equity': eq, 'nav': nav,
            'day_rets': [round(r, 8) for r in twr],
            'day_pnls': pnls,
            'stats': stats, 'note': None}


# ============================ 交易日历 ============================

# ============================ 基准指数 ============================
# ★ 可选的基准。**只列本地真有日线的** —— 列一个取不到数据的选项，
#   人点了什么都不出来，那比不给这个选项更糟（同 backLink 那条）。
# 🔴 中证2000（932000）与万得微盘股本地**没有**：
#     · 中证2000 —— tdx 只有跟踪它的 ETF，没有指数本身
#     · 微盘股   —— 万得专有指数，tdx 不提供
#   替代是**国证2000**（sz399303，2010 起），同为"小市值 2000 只"口径。
#   ★ 不拿 ETF 的价格当指数用：ETF 有折溢价、有管理费损耗、成立日晚，
#     拿它当基准会把跟踪误差算成策略的超额，而那不报错。
BENCHMARKS = [
    {'code': 'sh000001', 'name': '上证指数', 'from': '2003'},
    {'code': 'sz399006', 'name': '创业板指', 'from': '2010-06'},
    {'code': 'sh000688', 'name': '科创50', 'from': '2019-12'},
    {'code': 'sh000905', 'name': '中证500', 'from': '2007'},
    {'code': 'sh000852', 'name': '中证1000', 'from': '2005'},
    # ---- 下面几个是"更小市值"那一端的替代 ----
    # 🔴 中证2000（932000）本地**没有**：tdx 只有跟踪它的 8 只 ETF。
    #   国证2000 是同一层级（小市值 2000 只）的口径。
    {'code': 'sz399303', 'name': '国证2000', 'from': '2010',
     'note': '中证2000 本地没有（只有 ETF）—— 这是同层级口径'},
    # 🔴 万得微盘股（8841431.WI）是**万得专有**，tdx 不提供，
    #   本地一个"微盘"口径都没有。下面三个是最接近的小盘指数，
    #   但**都比微盘股大一档** —— 拿它们当微盘的替身会低估策略的
    #   风格暴露，所以名字里不写"微盘"，只写它本来的名字。
    {'code': 'sz399101', 'name': '中小综指', 'from': '2005-06',
     'note': '深市中小板全体，偏小盘；比微盘股大一档'},
    {'code': 'sz399316', 'name': '巨潮小盘', 'from': '2005-02',
     'note': '本地最接近"小盘"的宽基；比微盘股大一档'},
    {'code': 'sz399634', 'name': '中小等权', 'from': '2006-12',
     'note': '等权，风格上更接近等权小市值组合'},
]


# ---- 自定义基准：名称/代码手填 ----------------------------------------
# 🔴 用户 2026-09-16："实盘策略中的基准除了预设的这些，还可以自己手填
#   名称/代码比对，比如填某个红利 ETF 作为基准比对。"
#
# 本地凑得齐这件事，而且**全是 parquet**（不碰 tdx.db 的写锁）：
#     raw/tdx/kline/{index,etf,stock,block}_*.parquet   日线
#     raw/tdx/adjust_factor.parquet                     后复权因子
#     raw/tdx/snapshots/symbol_name_*.parquet           名称 + 类别
#
# 🔴🔴 **必须用后复权**。红利 ETF 的 hfq_factor 从 1.0 涨到 1.81
#   （sh510880，2007~2026）—— 那 81% 全是分红。拿不复权的收盘当基准，
#   等于把一个年化 ~5% 股息的东西按"股价涨了多少"来比，**系统性低估**，
#   而它不报错，只是那条线一直偏低。指数没有除权（因子表里没有它的行），
#   `coalesce(f, 1)` 正好。同「区间涨幅一律用后复权」那条。
_KIND_FILE = {'index': 'index_*', 'etf': 'etf_*', 'stock': 'stock_*',
              'block': 'block_*'}


# ★ 实现搬到了 `lv/tdx.py`（取价那条链也要用它，而 px 在 perf **之前**，
#   放这儿会成循环）。这里保留这个名字：`assay/stock.py` 的 `_snap_path`
#   import 的就是它 —— **同一份实现，两个入口**，不是两份。
_name_snap = _tdx.name_snap
def bench_meta(codes, datalake=None):
    """这些 symbol 叫什么、是哪一类 —— {symbol: {'name','kind'}}。

    ★ 页面只存 symbol（一个 localStorage 槽），名字每次由服务端给：
      存一份名字在前端的话，改过名的票会一直显示旧名，而它不报错。
    """
    codes = [c for c in (codes or []) if _SYM_RE.match(c or '')]
    if not codes:
        return {}
    root = _px._lake(datalake)
    fp = _name_snap(root)
    if not fp:
        return {}
    import duckdb
    con = duckdb.connect(':memory:')
    try:
        rows = con.execute(
            "SELECT symbol, name, class FROM read_parquet('%s') "
            "WHERE symbol IN ('%s')" % (fp, "','".join(codes))).fetchall()
    finally:
        con.close()
    return dict((r[0], {'name': r[1], 'kind': r[2]}) for r in rows)


def bench_search(q, datalake=None, limit=12):
    """按**名称或代码**找可以当基准的东西（指数 / ETF / 股票）。

    🔴 **只返回真有日线的**：列出来点了什么都不出来，比不给这个选项更糟
      （同 backLink 那条）。所以候选先按名字命中，再逐类去 kline 里
      查一次 max(date) —— 查的是**候选那几个**（≤ limit），不是全表。
    ★ 板块（block_*）不进候选：它们是通达信的板块指数，口径与这一页
      要比的"能买到的东西"不是一回事。
    """
    q = (q or '').strip()
    if not q:
        return []
    root = _px._lake(datalake)
    fp = _name_snap(root)
    if not fp:
        return []
    import duckdb
    con = duckdb.connect(':memory:')
    out = []
    try:
        like = q.replace("'", "").replace('%', '')[:24]
        rows = con.execute(
            "SELECT symbol, name, class FROM read_parquet('%s') "
            "WHERE class IN ('index','etf','stock') "
            "AND (name LIKE '%%%s%%' OR symbol LIKE '%%%s%%') "
            "ORDER BY CASE class WHEN 'index' THEN 0 WHEN 'etf' THEN 1 ELSE 2 END, "
            "symbol LIMIT %d" % (fp, like, like, int(limit))).fetchall()
        by_kind = {}
        for sym, name, kind in rows:
            if not _SYM_RE.match(sym or ''):
                continue
            by_kind.setdefault(kind, []).append((sym, name))
        for kind, items in by_kind.items():
            pat = _KIND_FILE.get(kind)
            if not pat:
                continue
            got = dict(con.execute(
                "SELECT symbol, max(date) FROM read_parquet('%s/raw/tdx/kline/%s.parquet') "
                "WHERE symbol IN ('%s') GROUP BY 1"
                % (root, pat, "','".join(s for s, _ in items))).fetchall())
            for sym, name in items:
                if sym in got:
                    out.append({'code': sym, 'name': name, 'kind': kind,
                                'last': str(got[sym])[:10]})
    finally:
        con.close()
    order = {'index': 0, 'etf': 1, 'stock': 2}
    out.sort(key=lambda x: (order.get(x['kind'], 9), x['code']))
    return out


def bench_curves(dates, codes, datalake=None):
    """把基准对齐到给定的交易日轴 -> {code: [归一化净值]}。

    🔴 **基点取第一天的【前一交易日】收盘**，不是第一天收盘 ——
      与 `feed.benchmark` 同一条纪律：用首日收盘做基点等于把首日的
      涨跌排除在基准之外（实测差 9.5pp）。这里的"第一天"是账户曲线的
      第一天，所以基准与账户从**同一起点**出发，叠加起来才可比。

    🔴🔴 **一律后复权**（`close × coalesce(hfq_factor, 1)`）。
      2026-09-16 开放"手填任意代码当基准"之后这条才真正咬人：
      红利 ETF（sh510880）的因子从 1.0 涨到 1.81，那 81% 全是**分红**。
      拿不复权收盘当基准，等于把一个年化 ~5% 股息的东西按"股价涨了多少"
      来比 —— **系统性低估**，而它不报错，只是那条线一直偏低。
      指数不除权（因子表里没有它的行），`coalesce` 到 1 正好，
      所以预设那几个指数的曲线**一个数都没变**。

    ★ 数据在 `raw/tdx/kline/{index,etf,stock}_*.parquet`，不在面板里 ——
      面板是「(date, code) 股票宽表」，塞指数/ETF 进去会让 as-of 语义变浑。
    ★ 先按**类别**挑文件（`symbol_name` 快照里的 class），挑不中再按
      index -> etf -> stock 试一遍：股票那组有 1600 万行，无差别全扫既慢
      又没必要。
    ★ 某天停牌/缺失时**留 None**（前端断开画），不做前值填充：
      填出来的平线看着像"那几天没动"，而实际是没有数据。
    """
    if not dates or not codes:
        return {}
    import duckdb
    root = _px._lake(datalake)
    meta = bench_meta(codes, datalake)
    F = "read_parquet('%s/raw/tdx/adjust_factor.parquet')" % root
    con = duckdb.connect(':memory:')
    out = {}
    try:
        for sym in codes:
            if not _SYM_RE.match(sym or ''):
                continue                     # 只认 sh/sz + 6 位，防注入
            kind = (meta.get(sym) or {}).get('kind')
            pats = [_KIND_FILE[kind]] if kind in _KIND_FILE else []
            pats += [v for k, v in (('index', 'index_*'), ('etf', 'etf_*'),
                                    ('stock', 'stock_*')) if v not in pats]
            for pat in pats:
                T = "read_parquet('%s/raw/tdx/kline/%s.parquet')" % (root, pat)
                # 后复权收盘：close × 因子（指数没有因子行 -> 1）
                PX = ("SELECT k.date AS d, k.close * coalesce(f.hfq_factor, 1) AS c "
                      "FROM %s k LEFT JOIN %s f "
                      "ON f.symbol = k.symbol AND f.date = k.date "
                      "WHERE k.symbol = '%s'" % (T, F, sym))
                try:
                    base = con.execute(
                        "SELECT c FROM (%s) WHERE d < DATE '%s' "
                        "ORDER BY d DESC LIMIT 1" % (PX, dates[0])).fetchone()
                    rows = dict(con.execute(
                        "SELECT d, c FROM (%s) WHERE d BETWEEN DATE '%s' AND DATE '%s'"
                        % (PX, dates[0], dates[-1])).fetchall())
                except Exception:                           # noqa: BLE001
                    continue                 # 这一类没有这个文件/这只票
                if not rows:
                    continue
                b = base[0] if base else rows.get(_base._d(dates[0]))
                if not b:
                    continue
                out[sym] = [(round(rows[_base._d(d)] / b, 8)
                             if _base._d(d) in rows else None) for d in dates]
                break
    finally:
        con.close()
    return out
