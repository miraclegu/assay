"""lv/bench.py —— 「绑定策略的理论曲线」：完全按策略做会怎样。

实盘的实际操作与绑定策略**必然有差异**（漏单、价格不同、手工调整、
提示没照做），所以业绩页要**两条曲线**：

    实际  = TWR 净值（`lv/perf.py`，用真实成交流水算）
    策略  = 同期同本金、按绑定版本+参数**完全照做**的回测净值

🔴 **两条必须同起点、同本金、同费率**，否则差异里混进了"起点不同"这种
  与执行无关的东西。费率取账户自己的（`effective_rates`）—— 用引擎默认
  的万2.5/最低5元会让策略曲线凭空少赚或多赚（froec 那种小额多笔的差得多）。

🔴 **不归档。** 它是派生物：每天都变（多一个交易日）、绑定版本一换就作废。
  塞进 `runs/` 会让「★ 选中的规则」和等价性回归里混进几百个一次性结果。
  落 `live/<id>/_bench/<key>.json`，不入 git（同 signals/_explain/）。

★ 缓存键含 `data_fingerprint`：数据修正过（本项目修过 volume 74,952 行、
  复权因子）之后必须重算，否则给的是按旧数据算的曲线**而它看着很正常**。
"""

import datetime
import hashlib
import json
import os
import sys

from . import base as _base
from . import fee as _fee
from . import ver as _ver

_MEM = {}          # 进程内缓存：{key: payload}
# ★ 自己定义 —— 实测 `lv/base` 里**没有** `_DATE_RE`（我按印象引用了
#   一次，它只在 diff_history 里走到，所以 import 与其它调用全绿）。
import re as _re
_DATE_RE = _re.compile(r'^\d{4}-\d{2}-\d{2}$')


def _bench_dir(aid):
    return os.path.join(_base.acct_dir(aid), '_bench')


def _key(aid, sha, params, start, end, fp):
    # ★ `default=str`：params 里可能有 date 对象（策略参数就有），
    #   而这里只是要一个**稳定的**指纹，不需要能反序列化回来。
    raw = json.dumps([aid, sha, params, start, end, fp], sort_keys=True,
                     ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]


def _repo():
    return os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))


def compute(aid, force=False, datalake=None):
    """跑一次「完全照做」的回测，返回 {dates, nav, ...}。

    ★ 三层缓存同 explain：进程内 -> 旁挂文件 -> 真跑。
    """
    a = next((x for x in _base.load_accounts() if x['id'] == aid), None)
    if a is None:
        raise _base.LiveError('没有这个账户：%s' % aid)
    sha = a.get('code_sha256')
    if not sha:
        return {'error': '账户还没绑定策略 —— 没有"策略曲线"可算'}
    start = (a.get('created') or '')[:10]
    if not start:
        return {'error': '账户没有开户日 —— 两条曲线没法对齐起点'}
    cash = float(a.get('init_cash') or 0)
    if cash <= 0:
        return {'error': '账户没有初始资金'}
    params = a.get('params') or {}

    # 🔴 版本快照才是"绑定的那份代码"；磁盘上的文件可能已经改了。
    row = _ver._version_row(aid, sha)
    if not row:
        return {'error': '找不到版本 %s 的快照' % sha[:8]}
    snap = os.path.join(_base.acct_dir(aid), 'code', sha[:8])
    main = row.get('strategy_path') or ''
    entry = os.path.join(snap, os.path.basename(main))
    if not os.path.isfile(entry):
        return {'error': '版本快照缺主文件：%s' % entry}

    sys.path.insert(0, _repo())
    try:
        from assay.broker import Cost
        from assay.engine import Engine
        from assay.feed import PanelFeed
        from run import load as _load
        feed_probe = PanelFeed(start, '2100-01-01', root=datalake)
        end = feed_probe.trading_days[-1]
        fp = feed_probe.fingerprint()
        k = _key(aid, sha, params, start, end, fp)
        if not force and k in _MEM:
            return _MEM[k]
        side = os.path.join(_bench_dir(aid), k + '.json')
        if not force and os.path.isfile(side):
            try:
                with open(side, encoding='utf-8') as f:
                    _MEM[k] = json.load(f)
                return _MEM[k]
            except Exception:                               # noqa: BLE001
                pass                    # 坏了就重算，不让它打挂整页

        # ---- 费率：用**这个账户**的，不是引擎默认 ----
        # 🔴 三个接口形状都被我猜错过一次，逐个说清：
        #   · `fee_rates(aid)` 返回的是**版本历史列表**；要某天生效的那档
        #     得用 `fee_model_at(aid, date)`
        #   · `effective_rates()` 的字段叫 `buy_rate`/`sell_rate`，不是 buy/sell
        #   · `sell_rate` **含印花税**，而引擎的 `Cost` 会按日期分段另加 ——
        #     直接拿它当 commission 等于印花税收两遍（万5 是最大的一项）
        eff = _fee.effective_rates(_fee.fee_model_at(aid, end),
                                   amount=100000.0, trade_date=end)
        comm = float(eff.get('buy_rate') or 0.0009)
        # ★ `min_commission=0`：这两个账户实测**没有 5 元最低**（净佣金 1.72
        #   没被抬到 5）。引擎默认的 5 元会让 froec 那种小额多笔凭空多付费用
        #   —— 而"策略曲线比实际差"就成了费率假设的产物，不是执行差异。
        # ★ `Cost` 的印花税参数叫 **close_tax**（`stamp_tax` 只是命令行的名字）。
        cost = Cost(slippage=0.0015, commission=comm,
                    min_commission=float(
                        _fee.fee_model_at(aid, end).get('min_commission') or 0),
                    close_tax='auto',
                    locked=['slippage', 'commission', 'min_commission',
                            'close_tax'])
        feed = PanelFeed(start, end, root=datalake)
        eng = Engine(_load(entry), feed, cash=cash, cost=cost, params=params)
        curve = eng.run(verbose=False)
        # 🔴 `Engine.run()` 返回的是 **[(date, total_value)] 的列表**，
        #   不是 DataFrame —— 我头一版按 DataFrame 写（`curve['date']`），
        #   那会抛 TypeError。凭印象假设接口形状，这一轮第五次了。
        dates = [str(d) for d, _v in curve]
        tv = [v for _d, v in curve]
        # 🔴 基点取**本金**，不是首日收盘权益。
        #   实盘那条是 TWR，起点是「开户那一刻的资金」（CLAUDE.md 里修过
        #   一次：从 None 起会把第一个交易日的盈亏整段丢掉）。策略这条若
        #   拿首日**收盘**做基点，就等于把策略首日的涨跌也排除在外 ——
        #   两条线基点不同口径，差异里混进了「起点差」而不是执行差。
        #   ★ froec 恰好 tv[0] == cash（周频策略，09-01 周一空仓），
        #     所以这个错**在当前数据上看不出来** —— 但换一个首日就建仓的
        #     账户立刻就是错的。
        base0 = cash
        out = {
            'dates': dates,
            'nav': [None if v is None else float(v) / base0 for v in tv],
            'equity': [None if v is None else float(v) for v in tv],
            'start': start, 'end': end, 'cash': cash,
            'sha': sha, 'main_sha256': row.get('main_sha256'),
            'params': params, 'data_fingerprint': fp,
            'fee': {'buy': eff.get('buy_rate'), 'sell': eff.get('sell_rate'),
                    'commission': comm,
                    'note': 'sell_rate 含印花税；引擎另按日期分段加，所以 commission 只取 buy_rate'},
            'computed_at': _base._now(),
        }
        os.makedirs(_bench_dir(aid), exist_ok=True)
        _base._atomic_write(side, json.dumps(out, ensure_ascii=False,
                                     default=str))
        _MEM[k] = out
        return out
    finally:
        if sys.path and sys.path[0] == _repo():
            sys.path.pop(0)


# ============ 每期「策略说什么 vs 实际做了什么」 ============
#
# 🔴 **调仓提示本来就以实际持仓为准**（`lv/sig.py` 的 `_seed` 把
#   `fifo_lots(真实成交流水)` 播种进引擎），所以这里比的不是"提示对不对"，
#   而是**执行**：提示给了 10 只，你买了几只、价格差多少。
#
# ★ 归属规则：信号的 `for_date` 是 T（下一个交易日），人照它下单也在 T 日
#   成交。所以拿 `trade_date == for_date` 的成交与那份信号比。
#   🔴 **不按"信号建好的那一刻之后的第一笔"来归属** —— 一天可能有多份
#     revision，而补录三个月前的成交也会被算进最近那期。判据用日期。

def _fill_map(aid, day):
    """那一天的实际成交，按代码聚合（同日多笔合并：股数相加、价格按金额加权）。

    ★ 合并是对的：最低佣金按成交笔收，但"这只票那天买了多少、均价多少"
      才是要和信号比的东西（002910 同日两笔 3100+600 = 3700 股）。
    """
    from . import pos as _pos
    out = {}
    for r in _pos.active_fills(_base.fills(aid)):
        if (r.get('trade_date') or '') != day:
            continue
        c = _base.normalize_code(r.get('code') or '')
        sh = float(r.get('shares') or 0)
        px = float(r.get('price') or 0)
        sgn = 1 if r.get('side') == 'buy' else -1
        e = out.setdefault(c, {'code': c, 'name': r.get('name') or '',
                               'buy_shares': 0.0, 'sell_shares': 0.0,
                               'buy_amt': 0.0, 'sell_amt': 0.0, 'n': 0})
        e['n'] += 1
        if sgn > 0:
            e['buy_shares'] += sh
            e['buy_amt'] += sh * px
        else:
            e['sell_shares'] += sh
            e['sell_amt'] += sh * px
        if not e['name'] and r.get('name'):
            e['name'] = r['name']
    for e in out.values():
        e['buy_px'] = (e['buy_amt'] / e['buy_shares']) if e['buy_shares'] else None
        e['sell_px'] = (e['sell_amt'] / e['sell_shares']) if e['sell_shares'] else None
    return out


def diff_one(aid, day, sig=None):
    """一期的执行差异。返回 {date, rows, summary}。

    每一行是一只票，`kind` 是差异类型：

      ok        照做了（股数在容差内）
      missed    **提示买了但没买** —— 最常见（钱不够、手动跳过、忘了）
      extra     **买了提示里没有的** —— 手工加的
      short     买了但**股数不足**
      over      买了但**超量**
      sell_miss 提示卖但没卖
      sell_extra 卖了提示没让卖的

    ★ 价差单列（`px_diff`）：即使股数照做，成交价与信号里的 `ref_price`
      也几乎总有差 —— 那是滑点与下单时机，与"照没照做"是两件事。
    """
    from . import sig as _sig
    if sig is None:
        sig = _sig.load_signal(aid, day)
    if not sig:
        return {'date': day, 'error': '这一天没有信号'}
    got = _fill_map(aid, day)
    want_b = {_base.normalize_code(x['code']): x for x in (sig.get('buy') or [])}
    want_s = {_base.normalize_code(x['code']): x for x in (sig.get('sell') or [])}
    rows = []
    TOL = 0.08          # 股数容差 8%：100 股一手 + 归一化后的凑整误差

    # 🔴🔴 **必须按本金归一化再比股数，否则整页都是假差异。**
    #   实测 froec 的 2026-09-01：那份信号是按 **100 万** 算的（账户后来
    #   改成 40 万），实际投 37.75 万 —— 于是 10 只票里 9 只被判成
    #   "买少了"（实际都是提示的约 38%），而那**根本不是执行差异**。
    #   信号里存着当时的 `cash`，拿它和"那天实际动用的资金"对齐比例。
    # ★ 只在**比股数**时用这个比例；`extra`/`missed` 这种"有没有"的判定
    #   不受它影响（买了没买是事实，与规模无关）。
    sig_cash = float(sig.get('cash') or 0)
    want_amt = sum(float(x.get('amount') or 0) for x in (sig.get('buy') or []))
    got_amt = sum((g.get('buy_amt') or 0) for g in got.values())
    scale = 1.0
    scale_why = None
    if want_amt > 0 and got_amt > 0:
        r0 = got_amt / want_amt
        # 只在**明显不同规模**时才缩放（>5%），否则正常的零头差会被当成缩放
        if abs(r0 - 1.0) > 0.05:
            scale = r0
            scale_why = ('信号按 %s 算、实际动用 %s（%.0f%%）—— 股数按这个'
                         '比例归一化后再比，否则整期都会被判成"买少了"'
                         % (format(int(sig_cash or want_amt), ','),
                            format(int(got_amt), ','), r0 * 100))

    for c in sorted(set(want_b) | set(want_s) | set(got)):
        wb, ws, g = want_b.get(c), want_s.get(c), got.get(c)
        nm = ((wb or {}).get('name') or (ws or {}).get('name')
              or (g or {}).get('name') or '')
        bs = (g or {}).get('buy_shares') or 0
        ss = (g or {}).get('sell_shares') or 0
        r = {'code': c, 'name': nm,
             'want_side': 'buy' if wb else ('sell' if ws else None),
             'want_shares': (wb or ws or {}).get('shares'),
             'ref_price': (wb or ws or {}).get('ref_price'),
             'got_buy': bs or None, 'got_sell': ss or None,
             'got_px': (g or {}).get('buy_px') if bs else (g or {}).get('sell_px'),
             'n_fills': (g or {}).get('n') or 0}
        want = float(r['want_shares'] or 0) * (scale if wb else 1.0)
        r['want_shares_scaled'] = round(want) if scale != 1.0 else None
        if wb:
            if not bs:
                r['kind'] = 'missed'
            elif want and abs(bs - want) / want <= TOL:
                r['kind'] = 'ok'
            elif bs < want:
                r['kind'] = 'short'
            else:
                r['kind'] = 'over'
        elif ws:
            r['kind'] = 'sell_miss' if not ss else 'ok'
        else:
            r['kind'] = 'sell_extra' if ss else 'extra'
        if r['ref_price'] and r['got_px']:
            r['px_diff'] = r['got_px'] / float(r['ref_price']) - 1.0
        rows.append(r)

    n = {}
    for r in rows:
        n[r['kind']] = n.get(r['kind'], 0) + 1
    pxs = [r['px_diff'] for r in rows if r.get('px_diff') is not None]
    return {
        'date': day, 'rows': rows, 'counts': n,
        'scale': scale, 'scale_why': scale_why,
        'sig_cash': sig_cash or None, 'want_amt': want_amt or None,
        'got_amt': got_amt or None,
        'is_rebalance_day': sig.get('is_rebalance_day'),
        'n_want_buy': len(want_b), 'n_want_sell': len(want_s),
        'px_diff_avg': (sum(pxs) / len(pxs)) if pxs else None,
        'clean': not any(r['kind'] != 'ok' for r in rows),
    }


def diff_history(aid, limit=60):
    """所有有信号的日子的执行差异（新到旧）。

    ★ 只列**有信号且那天有成交或有提示**的日子：非调仓日既不选股也不下单，
      列出来是一串空段（同选股理由页那条：froec 最近三期全是非调仓日，
      第一页什么都没有）。
    """
    import glob
    d = os.path.join(_base.acct_dir(aid), 'signals')
    out = []
    for f in sorted(glob.glob(os.path.join(d, '*.json')), reverse=True):
        day = os.path.basename(f)[:-5]
        if not _DATE_RE.match(day):
            continue
        try:
            with open(f, encoding='utf-8') as fh:
                sig = json.load(fh)
        except Exception:                                   # noqa: BLE001
            continue
        if not ((sig.get('buy') or sig.get('sell')) or _fill_map(aid, day)):
            continue
        out.append(diff_one(aid, day, sig))
        if len(out) >= limit:
            break
    return out
