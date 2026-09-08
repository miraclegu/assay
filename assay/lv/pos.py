"""lv/pos.py —— 账本的写入与重放：录一笔成交 / FIFO 批次 /
现金流水 / 冲正剔除 / 负持仓拦截。

🔴 账本 **append-only**：录错了写一条反向冲正，不物理删除 ——
  实盘账本一旦能被改写就没法复盘。改一笔 = 冲正 + 重录。"""
import datetime
import json
import os
import re

from . import base as _base
from . import fee as _fee
from . import px as _px


def add_fill(aid, trade_date, code, side, shares, price=None, fee=None,
             name='', source='manual', note='', reverse_of=None,
             fee_estimated=False, price_from=None, datalake=None,
             force_price=False):
    """录一笔成交。**只追加**。

    校验在这里做而不是页面上 —— 页面能绕过，这里是唯一入口。
    """
    if side not in _base.SIDES:
        raise _base.LiveError('side 只能是 buy/sell，收到 %r' % side)
    try:
        d = _base._d(trade_date)
    except Exception:                                       # noqa: BLE001
        raise _base.LiveError('成交日期格式应为 YYYY-MM-DD，收到 %r' % trade_date)
    try:
        shares = int(shares)
    except Exception:                                       # noqa: BLE001
        raise _base.LiveError('数量必须是整数')
    # ★ 先把代码归一到聚宽口径（券商/QMT 导出的是 301126.SZ），再做别的 ——
    #   归一化本身就是格式校验，且失败信息指向【代码】而不是"取不到行情"。
    #   便宜的校验一律排在取行情之前。
    code = _base.normalize_code(code)
    if shares <= 0:
        raise _base.LiveError('数量必须为正（撤销请用反向冲正，不要填负数）')
    if side == 'buy' and shares % _base.LOT_SIZE:
        raise _base.LiveError('买入数量必须是 %d 的整数倍，收到 %d' % (_base.LOT_SIZE, shares))
    # ★ 价格留空 = 按当日【开盘价】—— 很多策略是集合竞价买入，
    #   而开盘价就是 09:15-09:25 集合竞价的成交价。
    #   与"费用留空则估算"同一个模式：留空不等于 0，而是"照当日行情取"。
    #   取不到就报错，**不退回最近一个交易日** —— 那会静默给出错的价格。
    if price is None or price == '' or str(price).strip() == '-':
        which = price_from or 'open'
        if which not in _px.PRICE_FIELDS:
            raise _base.LiveError('price_from 只能是 %s' % '/'.join(_px.PRICE_FIELDS))
        price = _px.day_price(code, d, which, datalake)
        price_src = which
    else:
        try:
            price = float(price)
        except Exception:                                   # noqa: BLE001
            raise _base.LiveError('价格必须是数字（留空或填 - 表示按当日开盘价）')
        price_src = None
        # ★ 手填的价才需要校验；取的开盘价本来就来自当日行情。
        #   取不到行情时跳过（不能因为没同步就不让人录成交）。
        #   force_price=True 是**必须显式声明**的逃生口（同 rebuild_lake_db
        #   的 --allow-shrink）：大宗交易可以成交在区间外，面板本身也可能有
        #   问题 —— 硬拒会让人没有出路，而"没有出路"最后会变成绕过整个入口。
        if price > 0 and not force_price:
            _px.check_price_in_range(code, d, price, datalake)
    # ★ fee=None（没填）与 fee=0（明确说没有费用）是**两件事**。
    #   没填就按引擎口径估一个并标 fee_estimated —— 默认 0 会让现金越算越多，
    #   而"多出来的钱"不会报错，只会让权益悄悄虚高（年换手 4.5 次的话，
    #   一年约 0.5% 的费用凭空消失）。
    if fee is None or fee == '':
        # 冲正不估费用 —— 见下面对负费用的说明。调用方应传 -原费用。
        # ★ 按【成交日】取费率，不是"当前费率" —— 补录三个月前那笔时，
        #   拿今天的费率去算，数字看着很正常，只是错的。
        fee = 0.0 if reverse_of else _fee.estimate_fee(
            side, shares, price, d, _fee.fee_model_at(aid, d), code)
        fee_estimated = not reverse_of
    else:
        try:
            fee = float(fee)
        except Exception:                                   # noqa: BLE001
            raise _base.LiveError('费用必须是数字（不填则按引擎口径估算）')
    # ★ 只有【冲正】记录允许负费用。
    #   冲正的语义是"这笔交易没发生"，所以原记录的费用也要退掉：
    #     原买入   cash -= 11420 + 5
    #     冲正卖出 cash += 11420 - (-5)      -> 净影响 0，精确抵消
    #   若冲正记录照常估一笔费用（+10.71），净影响就是 -15.71 —— 一笔
    #   没发生的交易凭空吃掉两次费用，而且不报错。
    if fee < 0 and not reverse_of:
        raise _base.LiveError('费用不能为负（只有冲正记录可以，用来退掉原费用）')
    if price <= 0:
        raise _base.LiveError('价格必须为正')
    rec = {'uid': _base._uid(), 'ts': _base._now(), 'trade_date': d.isoformat(),
           'code': code, 'name': name, 'side': side, 'shares': shares,
           'price': price, 'fee': fee, 'source': source, 'note': note}
    if price_src:
        # 记下"这个价是取的当日开盘价"，不是券商回报 —— 与 fee_estimated 同理。
        # 部分成交、或你实际成交在别的价位时，可以「冲正 + 重录」填实际价。
        rec['price_from'] = price_src
    if fee_estimated:
        rec['fee_estimated'] = True
    if reverse_of:
        rec['reverse_of'] = reverse_of
    # ★ 把新记录放进账本【重放一遍】再决定收不收 —— 不是只看当前持仓。
    #   这样冲正、补录历史日期都能正确判断（见 replay_violation 的注释）。
    v = replay_violation(_base.fills(aid) + [rec])
    if v:
        c, vd, cur, want, bad = v
        if bad is rec:
            raise _base.LiveError('这笔卖出会让 %s 在 %s 的持仓变负：当时只有 %d 股，'
                            '要卖 %d 股' % (c, vd, cur, want))
        raise _base.LiveError(
            '加了这条之后，账本会在 %s 出现负持仓：%s 当时只有 %d 股，'
            '而那天有一笔卖 %d 股。\n'
            '—— 多半是你想冲正的这笔被【后面的记录】依赖了。'
            '先冲正那笔后续卖出，再冲正这一笔。'
            % (vd, c, cur, want))
    _base._append_jsonl(os.path.join(_base.acct_dir(aid), 'fills.jsonl'), rec)
    return rec


# ============================ 费率模型 ============================
# ★ 只有【净佣金】是可谈的，其余全是法定、所有人一样、券商只是代收代缴。
#   所以法定部分有默认值 —— **不配也能用**，配置界面只需要问你两件事：
#     · 佣金率是多少
#     · 报的这个率是「净佣金」还是「已含规费」
#
#   法定费率（2026-09 现行）：
#     证管费  万0.2    证监会    双边
#     经手费  万0.341  交易所    双边
#     ---------------- 规费合计 万0.541
#     过户费  万0.1    中登      双边（沪深都收）
#     印花税  万5      税务      **仅卖出**（2023-08-28 起千1减半为万5）
#
# ★ `commission_incl_reg`（报价是否含规费）是**必须问清的一条**，因为两家
#   券商可能都说"万2.5 最低 5 元"，而实付差很多：
#     含规费口径：最低 5 元【覆盖】规费   -> 5 万成交付 13.00
#     规费另收  ：规费加在 5 元【之外】   -> 5 万成交付 15.71
#   实测用户账户是【含规费】口径：App 说万0.8，账单是佣金1.72 + 规费3.57，
#   而 66110 × 万0.8 = 5.2888 ≈ 5.29 —— 单看净佣金万0.26 与 App 对不上。
#
# ★ 怎么定"最低佣金含不含规费"：需要一笔【小额】成交（金额 < 2 万，最低会
#   binding）。看账单佣金那行是 5.00 且规费另列（另收口径），
#   还是 佣金+规费 = 5.00（含规费口径）。大额单测不出来。

def fifo_lots(rows):
    """成交流水 -> {code: [ {shares, date, price, fee} ]}，FIFO 冲减。

    ★ 用 FIFO 而不是平均成本：引擎本身就是分批 FIFO（红利税按持有期分档、
      T+1 只锁当日买入那批），平均成本会让重建出来的持仓与引擎语义不一致。

    ★ `fee` 是这一批**还没卖掉那部分**摊到的买入费 —— 部分卖出时按股数
      比例消耗。摊薄成本要用它。
      卖出的费用**不进这里**：那是已实现成本，已经从现金里扣了，
      算进持仓成本会重复。

    ★ `price` 仍是**成交价**（不含费）—— 它要喂给引擎的 `entry_price`
      （止损、吊灯、红利税档位都读它）。**这个不能动**：动了实盘走的就
      不是策略自己的代码路径了。摊薄成本是另算一个数，不是改这个。
    """
    book = {}
    # ★ 同一天同一秒的多笔要有确定顺序，否则 FIFO 批次不可复现。
    #   tiebreak 用【文件里的插入顺序】，**不能用 uid** —— uid 是随机 hex，
    #   拿它排序会让同秒的买卖顺序随机翻转（实测：卖排到买前面，
    #   于是重放报"持仓变负"，而账本本身没问题）。
    for _i, r in sorted(_indexed(active_fills(rows)),
                        key=lambda t: (t[1]['trade_date'], t[1]['ts'], t[0])):
        c = r['code']
        lots = book.setdefault(c, [])
        if r['side'] == 'buy':
            lots.append({'shares': int(r['shares']), 'date': _base._d(r['trade_date']),
                         'price': float(r['price']),
                         'fee': float(r.get('fee') or 0)})
        else:
            left = int(r['shares'])
            while left > 0 and lots:
                if lots[0]['shares'] <= left:
                    left -= lots[0]['shares']
                    lots.pop(0)
                else:
                    # ★ 买入费按【剩余股数比例】消耗：卖掉一半，这批的
                    #   买入费也只剩一半算在成本里。整批卖掉时连费用一起
                    #   出账（pop）—— 那部分已经变成已实现盈亏的一部分。
                    keep = lots[0]['shares'] - left
                    lots[0]['fee'] = lots[0].get('fee', 0.0) * keep / lots[0]['shares']
                    lots[0]['shares'] = keep
                    left = 0
    return {c: v for c, v in book.items() if v}



def lots_asof(rows, day):
    """截至 day（含）的真实持仓。重放 warmup 时每天都要用。

    ★ 先按日期切、再交给 fifo_lots（它内部会剔掉已冲正的成对记录）。
      顺序不能反 —— 冲正记录可能晚于 day，那时原记录仍然有效。
    """
    day = _base._d(day)
    return fifo_lots([r for r in rows if _base._d(r['trade_date']) <= day])



def cash_asof(init_cash, rows, day, flows=()):
    day = _base._d(day)
    v = float(init_cash or 0)
    for r in flows:
        if _base._d(r['date']) <= day:
            v += float(r.get('signed') or 0)
    for r in active_fills(rows):
        if _base._d(r['trade_date']) > day:
            continue
        amt = r['shares'] * r['price']
        v += (-amt if r['side'] == 'buy' else amt) - float(r.get('fee') or 0)
    return v



def _indexed(rows):
    """(插入序号, 记录)。序号来自 fills.jsonl 的行序 —— append-only 的账本里
    行序就是时间序，是唯一可靠的同秒 tiebreak。"""
    return list(enumerate(rows))



def active_fills(rows):
    """剔掉【已冲正的成对记录】—— 原记录和它的冲正记录都不算。

    ★ 为什么必须成对剔掉，而不是"让它们在账上互相抵消"：

      1. **FIFO 批次会被搞错。** 买 1000@10（08-10）、卖 500@12（08-20）、
         再冲正那笔卖出（买 500@12，08-20）。若照单全收，批次变成
         [500@10 建仓08-10, 500@12 建仓08-20] —— 而实际上什么都没发生，
         应该是 [1000@10 建仓08-10]。成本价和建仓日都错了，
         **而这两个值直接喂给止损判定和红利税档位**。
      2. **重放会出现假的负持仓。** 冲正 08-10 那笔买入时，反向记录也记在
         08-10，于是排序后它落在 08-20 那笔卖出【之前】，那一刻持仓是负的
         —— 尽管那笔卖出本身早已被冲正掉。

      3. 现金也顺带更稳：成对剔掉后，冲正记录的费用符号填错也不会影响现金。

    只有【股数一致】才视为成对冲正 —— 手工写的部分冲正不能整条剔掉。
    """
    key = lambda r: r.get('uid') or r['ts']
    by_id = {key(r): r for r in rows}
    dead = set()
    for r in rows:
        src = r.get('reverse_of')
        if not src:
            continue
        o = by_id.get(src)
        if o is not None and int(o['shares']) == int(r['shares']) \
                and o['side'] != r['side']:
            dead.add(src)
            dead.add(key(r))
    return [r for r in rows if key(r) not in dead]



def replay_violation(rows):
    """重放整个账本，返回第一个「持仓变负」的违规，没有则返回 None。

    ★ 为什么不能只看「当前持仓」（原实现就是这么做的，有两个洞）：

      1. **冲正被后面的记录锁死。** 买 1000（08-10）→ 卖 500（08-20）后想
         冲正那笔买入，反向记录是"卖 1000"，而当前只持 500 —— 于是校验
         把它拒了，那条错记录**永远改不了**。正确的判断是：冲正之后
         08-20 那笔卖出会无股可卖，所以要先冲正 08-20 那笔。
      2. **补录历史日期的卖出。** 只看当前持仓，一笔日期在所有买入【之前】
         的卖出会被放行，而重放时那一刻持仓是负的。

    返回 (code, date, 该时点持仓, 想卖的股数, 违规记录)。
    """
    held = {}
    # 排序键带 uid：同一天同一秒的多笔要有确定顺序，否则 FIFO 批次不可复现
    for _i, r in sorted(_indexed(active_fills(rows)),
                        key=lambda t: (t[1]['trade_date'], t[1]['ts'], t[0])):
        c = r['code']
        n = int(r['shares'])
        if r['side'] == 'buy':
            held[c] = held.get(c, 0) + n
        else:
            cur = held.get(c, 0)
            if n > cur:
                return (c, r['trade_date'], cur, n, r)
            held[c] = cur - n
    return None



def positions(aid):
    """{code: {shares, cost, lots}}，cost 为股数加权平均成本（仅展示用）。

    ★ lots 里的日期转成 ISO 字符串 —— 这个函数是【接口层】的返回值，
      带 datetime.date 会让 json.dumps 直接抛 TypeError。
      内部计算一律用 fifo_lots（保留 date 对象），不要混用。
    """
    book = fifo_lots(_base.fills(aid))
    out = {}
    for c, lots in book.items():
        sh = sum(l['shares'] for l in lots)
        out[c] = {'shares': sh,
                  'cost': sum(l['shares'] * l['price'] for l in lots) / sh,
                  'lots': [dict(l, date=l['date'].isoformat()) for l in lots]}
    return out



def cashflows(aid):
    """入金 / 出金 / 手工调整。**append-only**，与成交流水同一原则。

    ★ 为什么不是「让 init_cash 可改」：init_cash 是【开户那一刻】的余额。
      后来入金 5 万，改 init_cash 会把这 5 万追溯到开户日，于是过去每一天的
      权益都变了 —— 而回看历史时没有任何痕迹说明它变过。
      入金是个**事件**，就该按事件记。分红到账、利息、手续费返还同理。
    """
    return _base._read_jsonl(os.path.join(_base.acct_dir(aid), 'cashflows.jsonl'))



CASH_KINDS = ('deposit', 'withdraw', 'dividend', 'adjust')



def add_cashflow(aid, date, amount, kind='deposit', note=''):
    """amount 一律填【正数】，方向由 kind 决定 —— 避免"负的出金"这种双重否定。"""
    _base.get_account(aid)
    if kind not in CASH_KINDS:
        raise _base.LiveError('kind 只能是 %s，收到 %r' % ('/'.join(CASH_KINDS), kind))
    try:
        d = _base._d(date)
        amount = float(amount)
    except Exception:                                       # noqa: BLE001
        raise _base.LiveError('日期或金额格式不对')
    if amount <= 0:
        raise _base.LiveError('金额填正数，方向由类型决定（出金选 withdraw）')
    signed = -amount if kind == 'withdraw' else amount
    if kind == 'withdraw' and cash(aid) < amount:
        raise _base.LiveError('出金 %.2f 超过当前现金 %.2f' % (amount, cash(aid)))
    rec = {'ts': _base._now(), 'date': d.isoformat(), 'kind': kind,
           'amount': amount, 'signed': signed, 'note': note}
    _base._append_jsonl(os.path.join(_base.acct_dir(aid), 'cashflows.jsonl'), rec)
    return rec



def cash(aid, asof=None):
    """现金 = 初始资金 + 现金流水 − 买入额 − 费用 + 卖出额。

    分红**按你录的 dividend 流水计**，不自动推 —— 没有数据源能确认到账日
    与实际税后金额。不录就是不计，此时现金是【下界】。
    """
    acct = _base.get_account(aid)
    v = float(acct.get('init_cash') or 0)
    lim = _base._d(asof) if asof else None
    for r in cashflows(aid):
        if lim and _base._d(r['date']) > lim:
            continue
        v += float(r.get('signed') or 0)
    for r in active_fills(_base.fills(aid)):
        if lim and _base._d(r['trade_date']) > lim:
            continue
        amt = r['shares'] * r['price']
        v += (-amt if r['side'] == 'buy' else amt) - float(r.get('fee') or 0)
    return v


# ============================ 权益与收益 ============================


def trades_of(code, aids=None):
    """某只票在【全部账户】的成交（给个股浮层画买卖点用）。

    ★ 不带账户参数也能用：浮层会在盘面/自选/买点页上被打开，那时"当前是
      哪个账户"是不存在的概念 —— 而"我在这只票上买卖过没有"恰恰是那时
      最想知道的事。所以默认扫全部非归档账户，逐笔标出自哪个账户。
    🔴 **冲正过的成对记录必须剔掉**（`active_fills`）：照单全收的话图上会
      出现一对"买了又卖了"的标记，而实际什么都没发生 —— 同 fifo_lots
      那条（成本价和建仓日会直接喂给止损判定）。
    """
    code = _base.normalize_code(code)
    out = []
    for a in _base.load_accounts():
        if a.get('archived'):
            continue
        if aids and a['id'] not in aids:
            continue
        rows = active_fills(_base.fills(a['id']))
        for i, r in enumerate(rows):
            if _base.normalize_code(r.get('code') or '') != code:
                continue
            out.append({
                'account': a['id'], 'account_name': a.get('name'),
                'date': r.get('trade_date'), 'side': r.get('side'),
                'shares': r.get('shares'), 'price': r.get('price'),
                'fee': r.get('fee'), 'note': r.get('note') or '',
                'seq': i,          # 账本行序 —— 同日多笔的先后（不能用 ts，秒精度会撞）
            })
    out.sort(key=lambda x: (x['date'] or '', x['account'], x['seq']))
    return out
