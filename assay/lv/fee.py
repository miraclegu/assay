"""lv/fee.py —— 费率模型（append-only 版本历史 / 逐项明细 / 反推）。

细则见 CLAUDE.md「费率有三层」。这一块是全项目最容易填错的地方，
所以刻意做成「照一张真实账单逐项抄」。"""
import datetime
import json
import math
import os
import re
from ..broker import Cost

from . import base as _base


def _merge_fee(raw):
    m = dict(_base.FEE_DEFAULT)
    m.update({k: v for k, v in (raw or {}).items() if k in _base.FEE_FIELDS})
    return m



def fee_rates(aid):
    """费率版本历史，按生效日升序，并**推导出结束日**。

    ★ 与策略版本、成交流水同一个模式：**append-only**。
      "新费率生效时原费率立刻结束"这件事【不靠改写上一条】实现 ——
      每条只记 `from`（生效日），`to` 由下一条的 from 减一天推出来。
      改写账本是这个模块从头到尾都在避免的事：一旦能改写就没法复盘
      "当时用的是哪个费率"。

    ★ 为什么费率必须按日期分版本：**补录一笔历史成交时，要用那笔成交
      当时生效的费率**。换过券商/谈过费率之后，拿今天的费率去算三个月前
      那笔，数字看着很正常，只是错的。
    """
    rows = sorted(_base._read_jsonl(os.path.join(_base.acct_dir(aid), 'fee_rates.jsonl')),
                  key=lambda r: (r['from'], r['ts']))
    out = []
    for i, r in enumerate(rows):
        to = None
        if i + 1 < len(rows):
            to = (_base._d(rows[i + 1]['from']) - datetime.timedelta(days=1)).isoformat()
        # to < from 说明有一条【同日】的记录把它盖掉了（更正），
        # 有效区间零长度 —— 标成已作废，页面上照样列出来。
        dead = bool(to and _base._d(to) < _base._d(r['from']))
        # ★ 每档带上【总费率】—— 页面要显示的是总费率而不是佣金率：
        #   佣金只是其中一项，看佣金万0.26 完全说明不了实付万0.9。
        #   印花税按【该档生效日】算分段，不是今天。
        full = _merge_fee(r.get('fee'))
        out.append(dict(r, to=(None if dead else to),
                        active=(to is None), superseded=dead,
                        model=full,
                        rates=effective_rates(full, 100000.0, r['from'])))
    return out



def fee_model_at(aid, date):
    """`date` 那天生效的费率。没有任何配置、或早于第一条生效日 -> FEE_DEFAULT。

    ★ 早于第一条时【不往前延伸】—— 那是猜。用默认值（偏保守），
      并且这件事在页面上看得见（历史表第一行的生效日就是分界）。
    """
    d = _base._d(date)
    hit = None
    for r in fee_rates(aid):
        if _base._d(r['from']) <= d:
            hit = r
        else:
            break
    return _merge_fee((hit or {}).get('fee'))



def fee_model(acct):
    """兼容入口：账户【当前】生效的费率。"""
    aid = acct['id'] if isinstance(acct, dict) else acct
    rows = fee_rates(aid)
    if rows:
        return fee_model_at(aid, datetime.date.today())
    # 老数据：单份 acct['fee']，没有生效日
    return _merge_fee((acct.get('fee') or {}) if isinstance(acct, dict) else {})



def add_fee_rate(aid, from_date, model, note='', supersede=False):
    """新增一档费率。**只能追加，不能改写已有的记录。**

    两种追加：
      · 默认（费率变了）：生效日必须【严格晚于】上一档，上一档自动在前一天结束
      · `supersede=True`（**上一档填错了**）：允许与上一档【同一生效日】，
        追加一条把它盖掉。上一档的有效区间变成零长度，在历史里显示为"已作废"。

    ★ 为什么用"同日追加"而不是改写那条记录：改写就没法回答"当时用的是哪档"。
      同日追加之后，`fee_model_at` 取到的是**后写入的那条**（fee_rates 按
      (from, ts) 排序），而错的那条仍然看得见 —— 这正是账本该有的样子。

    ★ 但这条通路**只解决填错**，不解决"改历史费率"：如果已经按错的费率
      算过成交，那些成交的费用已经落在 fills 里了。要修得去流水页
      「冲正 + 重录」，让它按更正后的费率重算。
    """
    _base.get_account(aid)
    try:
        d = _base._d(from_date)
    except Exception:                                       # noqa: BLE001
        raise _base.LiveError('生效日格式应为 YYYY-MM-DD，收到 %r' % from_date)
    m = {k: v for k, v in (model or {}).items() if k in _base.FEE_FIELDS}
    if not m:
        raise _base.LiveError('没有可保存的费率字段')
    if m.get('mode') and m['mode'] not in _base.FEE_MODES:
        raise _base.LiveError('mode 只能是 %s' % '/'.join(_base.FEE_MODES))
    for k in ('commission', 'min_commission', 'regulatory', 'transfer',
              'buy_rate', 'sell_rate', 'flat_min'):
        if k in m:
            try:
                m[k] = float(m[k])
            except Exception:                               # noqa: BLE001
                raise _base.LiveError('%s 必须是数字' % k)
            if m[k] < 0:
                raise _base.LiveError('%s 不能为负' % k)
    if 'stamp' in m and m['stamp'] != 'auto':
        try:
            m['stamp'] = float(m['stamp'])
        except Exception:                                   # noqa: BLE001
            raise _base.LiveError('stamp 必须是数字或 "auto"')
    if 'commission_incl_reg' in m:
        m['commission_incl_reg'] = bool(m['commission_incl_reg'])
    if 'transfer_extra' in m and m['transfer_extra'] not in _base.TRANSFER_EXTRA:
        raise _base.LiveError('transfer_extra 只能是 %s' % '/'.join(_base.TRANSFER_EXTRA))
    cur = fee_rates(aid)
    if cur:
        last = _base._d(cur[-1]['from'])
        if d < last or (d == last and not supersede):
            raise _base.LiveError(
                '生效日必须晚于上一档（%s）。费率是按时间线追加的：'
                '新的一档生效，上一档就在前一天结束。\n'
                '如果是【上一档填错了】，勾上「更正上一档」——'
                '那会用同一个生效日追加一条把它盖掉，错的那条仍然看得见。'
                % cur[-1]['from'])
    rec = {'ts': _base._now(), 'uid': _base._uid(), 'from': d.isoformat(),
           'fee': m, 'note': note or ''}
    path = os.path.join(_base.acct_dir(aid), 'fee_rates.jsonl')
    if supersede:
        # ★ 被同日盖掉的那档【物理删除】，不留"已作废"的行。
        #   它的有效区间是零长度 —— 从来没有任何成交按它算过，
        #   留着只是噪声。见 _write_jsonl 的说明。
        keep = [r for r in _base._read_jsonl(path) if r.get('from') != d.isoformat()]
        n = len(_base._read_jsonl(path)) - len(keep)
        _base._write_jsonl(path, keep + [rec])
        rec['replaced'] = n
    else:
        _base._append_jsonl(path, rec)
    return dict(rec, to=None, active=True)



def migrate_fee(aid):
    """老数据迁移：acct['fee'] 单份配置 -> 费率历史的第一条。

    生效日取账户创建日 —— 那是它唯一可能生效的起点。
    """
    a = _base.get_account(aid)
    if not a.get('fee') or fee_rates(aid):
        return None
    d = (a.get('created') or '2000-01-01')[:10]
    return add_fee_rate(aid, d, a['fee'], note='从单份配置迁移')



def _stamp_rate(m, d):
    v = m.get('stamp', 'auto')
    if v == 'auto':
        return Cost().close_tax_at(_base._d(d))
    return float(v or 0)



def _round2(x, how='round'):
    """分位舍入。券商各项的舍入规则**不确定**（实测既有像截尾的、也有像
    进位的），所以这里可切换 —— 对账时把三种都试一遍，见 fee_recheck。"""
    if how == 'floor':
        return math.floor(x * 100 + 1e-9) / 100.0
    if how == 'ceil':
        return math.ceil(x * 100 - 1e-9) / 100.0
    return round(x, 2)



def fee_breakdown(side, shares, price, trade_date, model=None, code=None,
                  rounding='round'):
    """算一笔的费用明细。

    mode='flat'：`max(额 × 买/卖总费率, 最低)` —— **不拆项，填多少算多少**。
    mode='parts'（默认）：逐项拆开，见下。

        佣金部分 = max(额 × 佣金率, 最低佣金)
                   若报价【不含】规费，再加 额 × 规费率
        + 过户费 = 额 × 过户费率  ← 仅当这个市场【另收】（见 transfer_extra）
        + 印花税 = 额 × 印花税率（仅卖出）

    ★ 最低佣金是压在【佣金那一行】上的，而那一行按券商口径可能已含规费、
      甚至已含过户费。所以"最低5元"到底盖住多少东西，取决于 
      commission_incl_reg 与 transfer_extra —— 银河的 5.00 在深市盖住了
      净佣金+经手费+证管费+过户费四项，在沪市只盖住前三项。

    ★ 印花税分段规则复用 `broker.Cost.close_tax_at`，不在这里重写 ——
      写两份的话，实盘现金与回测成本会在 2023-08-28 前后分叉。
    """
    m = model or dict(_base.FEE_DEFAULT)
    amt = float(shares) * float(price)
    if m.get('mode') == 'flat':
        rate = float(m['sell_rate'] if side == 'sell' else m['buy_rate'])
        tot = max(amt * rate, float(m.get('flat_min') or 0))
        tot = _round2(tot, rounding)
        return {'mode': 'flat', 'amount': round(amt, 2), 'rate': rate,
                'commission': None, 'regulatory': None, 'transfer': None,
                'transfer_extra': False, 'market': _base.market_of(code) if code else None,
                'stamp': None, 'total': tot}
    comm = max(amt * float(m['commission']), float(m['min_commission'] or 0))
    reg = 0.0
    if not m.get('commission_incl_reg'):
        reg = amt * float(m.get('regulatory') or 0)
    extra = _base.transfer_is_extra(m, code)
    trf = amt * float(m.get('transfer') or 0) if extra else 0.0
    stamp = amt * _stamp_rate(m, trade_date) if side == 'sell' else 0.0
    # ★ 逐项舍入后相加（券商就是这么算的），不是先加总再舍一次 ——
    #   两者能差 1 分，而 1 分过户费 ≈ 1,000 元成交金额。
    comm, reg, trf, stamp = (_round2(x, rounding) for x in (comm, reg, trf, stamp))
    out = {'mode': 'parts', 'amount': round(amt, 2), 'commission': comm,
           'regulatory': reg, 'transfer': trf,
           'transfer_extra': extra, 'market': _base.market_of(code) if code else None,
           'stamp': stamp}
    out['total'] = round(comm + reg + trf + stamp, 2)
    return out



def estimate_fee(side, shares, price, trade_date, model=None, code=None,
                 rounding='round'):
    """一笔成交的估算费用（分）。明细见 fee_breakdown。

    这是**估算**：券商是逐项截尾/进位后相加，可能差 ±0.01。
    对完账单用「冲正 + 重录」填实际值即可（入账已标 fee_estimated）。

    ★ 要 code：过户费是否另收分市场（见 transfer_is_extra）。
    """
    return fee_breakdown(side, shares, price, trade_date, model, code,
                         rounding)['total']


# 折算总费率时用的样板代码，只为了给出市场（后缀）

_SAMPLE = {'XSHG': '600000.XSHG', 'XSHE': '000001.XSHE'}



def effective_rates(model, amount=100000.0, trade_date=None):
    """把当前配置折成【买入/卖出总费率】。

    parts 模式下总费率随金额变（最低佣金 binding 时更高），所以要给金额。
    页面用它做两件事：显示"当前 ≈ 买入万X"，以及一键把它填进 flat 模式。

    ★ 过户费可能只有一个市场另收（银河：沪市另收、深市已含），这时**沪深
      的总费率不同**（万0.96 vs 万0.86）。所以这里按市场各给一套，放在
      `by_market` 里；顶层那对取【两者中高的那个】并在 `worst_market`
      里标出是哪个市场 —— 取平均会在两边都错，而取高的至少方向保守。
    """
    d = trade_date or datetime.date.today().isoformat()
    n = 100
    px = float(amount) / n
    by = {}
    for mk, sample in _SAMPLE.items():
        b = fee_breakdown('buy', n, px, d, model, sample)['total']
        s_ = fee_breakdown('sell', n, px, d, model, sample)['total']
        by[mk] = {'buy_rate': round(b / float(amount), 8),
                  'sell_rate': round(s_ / float(amount), 8),
                  'buy_fee': b, 'sell_fee': s_}
    worst = max(by, key=lambda k: (by[k]['buy_rate'], by[k]['sell_rate']))
    out = {'amount': float(amount), 'by_market': by,
           'worst_market': worst,
           # 沪深是否同价 —— 页面据此决定显示一个数还是两个
           'same': by['XSHG'] == by['XSHE']}
    out.update(by[worst])
    return out



def infer_fee_model(amount, commission, regulatory=0.0, transfer=0.0,
                    stamp=0.0, min_commission=0.0):
    """从一笔真实账单反推费率，并判断券商用的是哪种口径。

    ★ 判据：账单同时列了「佣金」与「规费」两行时，(佣金+规费)/金额 才是
      App 上那个"佣金费率"。所以返回 commission_incl_reg=True 并把两行
      之和折成率 —— 这样复算能与账单**精确一致**。
      若只给了佣金（没有规费行），按"规费另收"处理。

    ★ 这里定不出「最低佣金含不含规费」—— 那需要一笔【小额】成交
      （最低会 binding）。返回里带 need_small_bill 提醒。

    🔴 **不能拿触及最低佣金的那笔账单来反推费率。** 那笔的佣金是被最低
      顶上去的常数，除以金额得到的"费率"与真实费率无关。实测银河：
      南都物业 37,455 佣金 5.00（最低 binding）→ 反推万1.34；
      大唐发电 106,856 佣金 9.19（未 binding）→ 反推万0.86（真值）。
      差 56%，而两个都是"能算出来的数"。所以这里**直接拒绝**并告诉你
      需要多大金额的账单。
    """
    amount = float(amount)
    if amount <= 0:
        raise _base.LiveError('成交金额必须为正')
    commission = float(commission or 0)
    regulatory = float(regulatory or 0)
    incl = regulatory > 0
    mc = float(min_commission or 0)
    # 佣金那行恰好等于最低 -> 这笔被最低支配，费率无从反推
    if mc > 0 and abs(commission + (regulatory if incl else 0) - mc) < 0.005:
        raise _base.LiveError(
            '这笔的佣金正好等于最低佣金 %.2f 元 —— 说明它被【最低】顶住了，'
            '反推不出真实费率（除出来的 万%.2f 只是 最低/金额）。\n'
            '请换一笔【金额更大】的账单：按常见的万0.86 算，要大于约 %s 元'
            '最低才不 binding。'
            % (mc, (commission + (regulatory if incl else 0)) / amount * 1e4,
               format(int(mc / 0.000086), ',')))
    out = {
        'commission': round(rate_of(commission, regulatory, amount, incl), 8),
        'min_commission': mc,
        'commission_incl_reg': incl,
        # 🔴 `regulatory` 必须【显式给出】，不能省。约定是"规费填 0 表示
        #   已含在佣金里"，而省掉这个键会让 _merge_fee 用默认 REG_RATE
        #   万0.541 顶回来、再加一遍：实测大唐发电那笔 10.25 会算成 16.03
        #   （高 56%）。省一个键的代价是整档费率错，且复算才看得出来。
        'regulatory': round(float(regulatory) / amount, 8) if regulatory else 0.0,
        'transfer': (round(float(transfer) / amount, 8) if transfer
                     else _base.TRANSFER_RATE),
    }
    if stamp:
        out['stamp'] = round(float(stamp) / amount, 8)
    return out



def rate_of(commission, regulatory, amount, incl):
    """账单上那两行折成佣金率。incl=True 时(佣金+规费)才是 App 上的那个率。"""
    return ((commission + regulatory) / amount) if incl else (commission / amount)
