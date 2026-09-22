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
from . import openbar as _ob
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


def build_engine(aid, datalake=None, intraday=False):
    """按【这个账户】的绑定版本 + 参数 + 费率建一个引擎，**还没跑**。

    返回 `(eng, meta)`；`meta` 含 start/end/cash/sha/fp/fee 等。
    出错时返回 `(None, {'error': ...})` —— 调用方照原样把原因给页面。

    🔴 **抽出来是为了让【策略曲线】与【模拟盘】共用同一次构建。**
      模拟盘的成交必须与业绩页那条策略曲线来自**同一个引擎配置**，
      各建一份的话迟早分叉（费率、滑点、gated 起点任何一处不同，
      就会出现"模拟盘的成交与策略曲线对不上"，而那不报错）。
      这也是本项目反复吃过亏的模式：同一件事两处写。
    """
    a = next((x for x in _base.load_accounts() if x['id'] == aid), None)
    if a is None:
        raise _base.LiveError('没有这个账户：%s' % aid)
    sha = a.get('code_sha256')
    if not sha:
        return None, {'error': '账户还没绑定策略 —— 没有"策略曲线"可算'}
    # 🔴 **起点分两种，别混**：
    #     实盘   -> 开户日。「策略曲线」要与实际那条**对齐起点**，
    #               否则差异里混进"起点差"（同「两条的基点都必须是本金」）。
    #     模拟盘 -> `paper_start`（人设的推演起点），没设才退回开户日。
    #               它就是"这个模拟盘从哪天开始跑"，业绩页那条权益曲线
    #               的起点是**账本第一笔**，所以自动跟着走、不会分家。
    start = ((a.get('paper_start') or '') if _base.is_paper(a) else '')[:10] \
        or (a.get('created') or '')[:10]
    if not start:
        return None, {'error': '账户没有开户日 —— 两条曲线没法对齐起点'}
    cash = float(a.get('init_cash') or 0)
    if cash <= 0:
        return None, {'error': '账户没有初始资金'}
    params = a.get('params') or {}

    # 🔴 版本快照才是"绑定的那份代码"；磁盘上的文件可能已经改了。
    row = _ver._version_row(aid, sha)
    if not row:
        return None, {'error': '找不到版本 %s 的快照' % sha[:8]}
    snap = os.path.join(_base.acct_dir(aid), 'code', sha[:8])
    main = row.get('strategy_path') or ''
    entry = os.path.join(snap, os.path.basename(main))
    if not os.path.isfile(entry):
        return None, {'error': '版本快照缺主文件：%s' % entry}

    sys.path.insert(0, _repo())
    try:
        from assay.broker import Cost
        from assay.engine import Engine
        from assay.feed import PanelFeed
        from run import load as _load
        from run import default_lake as _default_lake
        # 🔴🔴 **策略声明的数据源要在这里解析，`run.py` 里那一份管不到。**
        #   2026-09-13 修过一次「靠人记得传 --datalake 的都会漏」，做法是
        #   策略模块级 `DATALAKE = 'etf_lake'` + `run.py` 的 `resolve_lake`。
        #   但**建引擎的路径不止 run.py 一条** —— 模拟盘与业绩页那条
        #   「策略曲线」走的是这里，而这里一直没接上：
        #     实测 a3（ETF 轮动模拟盘）点「推进」-> `etf_master.parquet`
        #     不存在，IOException 直接冒到 500。
        #   ★ 而**崩掉还算好的**：`etf_trend_momentum` 在股票面板上
        #     候选池恒空 -> 全程空仓、一条平线、**不报任何错**
        #     （那正是当初那条纪律要挡的）。
        #   ★ 复用 `run.resolve_lake` 而不是在这儿再写一遍：两处实现迟早分叉，
        #     而分叉的表现是"命令行跑得对、模拟盘跑在另一个 lake 上"。
        mod = _load(entry)
        # ★ 解析 + 把 SystemExit 翻成人话都在 `base.resolve_strategy_lake`
        #   **一处** —— 出信号那条链（`sig.make_signal`）也要用它。
        root = _base.resolve_strategy_lake(mod, datalake)
        feed_probe = PanelFeed(start, '2100-01-01', root=root)
        end = feed_probe.trading_days[-1]
        fp = feed_probe.fingerprint()
        k = _key(aid, sha, params, start, end, fp)

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
        # 🔴 **`close_tax` 不锁** —— 锁的那几项是"券商收多少"（账户知道），
        #   而印花税是**标的属性**：ETF 无印花税是**事实不是偏好**
        #   （所以它写在策略文件里，见「策略要自己声明数据源与费率」那条）。
        #   锁死成 'auto' 的话，ETF 每笔卖出都被按股票收万5 ——
        #   年换手 9 次就是 **0.45%/年** 凭空扣掉，**而它不报错**，
        #   只是模拟盘的净值系统性偏低。
        #   ★ 分工：佣金 / 最低佣金 = 券商的事（用账户的，锁）；
        #     印花税 = 标的的事（策略声明了就听它，没声明时 'auto' 仍是股票口径）；
        #     滑点 = 这条对照线的统一口径（锁，否则两条曲线不可比）。
        cost = Cost(slippage=BENCH_SLIP, commission=comm,
                    min_commission=float(
                        _fee.fee_model_at(aid, end).get('min_commission') or 0),
                    close_tax='auto',
                    locked=['slippage', 'commission', 'min_commission'])
        # 🔴🔴 **feed 给长区间，但开户日之前【一个委托都不下】。**
        #   两件事必须同时满足，而它们互相拉扯：
        #     ① 周内序号要对 —— `run_weekly(weekday=N)` 判「本周第 N 个交易日」，
        #        序号按 **feed 的交易日表**算。区间从开户日起会切掉那一周的
        #        前半段：2026-09-01 在完整面板里是 (2,-4)，截断后成 (1,-4)
        #        -> weekday=2 判不中 -> **第一天不建仓**、净值恰好 1.0000
        #        （用户一眼看出的「刚好 0%，一点波动都没有」）。
        #     ② 起点资金要和实盘**一样** —— 不然买的股数、遇到的「资金不足
        #        一手」约束都不同，两条线不可比（用户第二次指出的那条）。
        #   ★ 挂点是 **`Engine._due`**，不是替换策略模块上的函数：
        #     `run_daily(prepare, ...)` 是在 froec.py 的 `initialize` **内部**
        #     注册的，传进引擎的是那个模块自己的函数对象 —— 在
        #     `froec_traded` 模块上换属性对已注册的引用毫无影响（实测：
        #     warmup 期照样交易、首日权益 52.6 万）。
        #   ★ 也不能改 `_tasks`：它在 `run()` -> `_boot()` 里才被填充，
        #     那时已经开始跑了。`_due` 是每个任务每天都要过的那道门。
        class _GatedEngine(Engine):
            """warmup 期只推进日历，不触发任何任务 —— 资金停在 init_cash、
            持仓为空，与实盘开户那一刻的状态完全一致。
            ★ 副作用是 warmup 期不累积 `g.hold_history` / `g.pos_state`，
              **这是对的**：实盘 09-01 也是空仓开始，那时它们本来就是空。"""

            def _due(self, i, d, freq, wd, md, ev, off):
                if str(d) < start:
                    return False
                return Engine._due(self, i, d, freq, wd, md, ev, off)

            def _boot(self, verbose=False):
                Engine._boot(self, verbose)
                # 🔴🔴 **盘中那一天只跑到 OPEN 相位为止。**
                #   引擎没有分时线：`09:31~14:59` 全归 INTRADAY 并**一律用
                #   当日收盘价代理**，而收盘还没发生。froec_traded 的
                #   `check_limit_up`(14:00 炸板离场) 与 `stop_check`(14:00 止损)
                #   读的是**收盘派生量** —— 09:31 根本不存在。
                #   让它们跑 = 拿一个不存在的收盘去做判定，**而它不报错**。
                # ★ 为什么在这里包 `_tasks` 而不是在 `_due` 里判：
                #   `_due(i, d, freq, wd, md, ev, off)` **拿不到 `t`**，
                #   映射不回"这个任务是几点的"。
                # ★ `_boot` 可重入（`run()` 里还会调一次），所以要门闩 ——
                #   不然任务被包两层，第二层的 `t` 还是对的但纯属浪费。
                if not _iday or getattr(self, '_ob_cut', False):
                    return
                self._ob_cut = True
                cut, day = _ob.PHASE_CUTOFF, _iday

                # ★ 被推迟的任务要**记下来**：人该知道"今天 14:00 的止损/
                #   炸板还没跑，留到日终" —— 不说的话盘中那份持仓看着就是
                #   最终结果（同「拒单必须可见，不静默」那条）。
                #   它同时是"截断真的生效了"的**可观察判据**：包在外面数
                #   调用次数是量不出来的（被跳过的任务照样被调一次）。
                self._ob_deferred = {}

                def _wrap(t, func):
                    def _f(ctx):
                        if str(ctx.current_date)[:10] == day and t > cut:
                            self._ob_deferred[t] = \
                                self._ob_deferred.get(t, 0) + 1
                            return
                        return func(ctx)
                    return _f

                self._tasks = [(t, fq, _wrap(t, fn), wd2, md2, ev2, of2)
                               for (t, fq, fn, wd2, md2, ev2, of2) in self._tasks]

        feed = PanelFeed(
            (datetime.date.fromisoformat(start)
             - datetime.timedelta(days=40)).isoformat(), end, root=root)
        # ---- 盘中推进：在面板之外**追加今天这一天** ----
        # ★ `end` 仍然是面板最新日（权威），今天是**多出来**的那一格；
        #   `meta['end']` 因此改成实际推到的最后一天，而 `panel_end`
        #   单独带出去 —— 页面要能说清"权威到哪天、盘中多推了哪天"。
        # ★ `intraday` 可以是 True（= 今天）或一个日期字符串（点名哪一天）。
        #   后者给复盘与判据构造用：拿**过去某一天**当"盘中那一天"，
        #   就能把盘中拼出来的 bar 与**日终权威值**逐位对照 ——
        #   而靠"今天恰好是交易日且过了 09:31"的判据是空转的
        #   （同「真实数据触发不到的上限，判据必须能构造出来」那条）。
        _iday, _iwhy = None, None
        if intraday:
            _named = not isinstance(intraday, bool)
            _d, _iwhy = _ob.can_advance_intraday(
                end, day=(intraday if _named else None), root=root,
                explicit=_named)
            if _iwhy is None:
                _iday = _d
                feed = _ob.IntradayFeed(feed, _iday, end, root=root)
        # ★ `mod` 上面已经加载过（为了读 DATALAKE）—— 不要再 `_load` 一次：
        #   那会把策略文件的顶层代码**执行两遍**。
        eng = _GatedEngine(mod, feed, cash=cash, cost=cost,
                           params=params)
        return eng, {'start': start,
                     'end': (_iday or end), 'panel_end': end,
                     'intraday_day': _iday, 'intraday_why': _iwhy,
                     'intraday_feed': (feed if _iday else None),
                     'engine': eng,
                     'cash': cash, 'sha': sha,
                     'main_sha256': row.get('main_sha256'), 'params': params,
                     'data_fingerprint': fp, 'key': k,
                     # ★ 数据源与成本一样**直接决定结果**，所以要带出去
                     #   （同 run.py 抬头把「数据 <root>（策略声明）」打出来
                     #   那条）。`root` 为 None 表示走默认 lake —— 这里解成
                     #   实际路径，页面才说得出"跑在哪份数据上"。
                     'datalake': root or _default_lake(),
                     'datalake_declared': bool(getattr(mod, 'DATALAKE', None)),
                     'fee': {'buy': eff.get('buy_rate'),
                             'sell': eff.get('sell_rate'), 'commission': comm,
                             'note': 'sell_rate 含印花税；引擎另按日期分段加，'
                                     '所以 commission 只取 buy_rate'}}
    finally:
        if sys.path and sys.path[0] == _repo():
            sys.path.pop(0)


def compute(aid, force=False, datalake=None):
    """跑一次「完全照做」的回测，返回 {dates, nav, ...}。

    ★ 三层缓存同 explain：进程内 -> 旁挂文件 -> 真跑。
    """
    eng, meta = build_engine(aid, datalake=datalake)
    if eng is None:
        return meta
    start, end, cash = meta['start'], meta['end'], meta['cash']
    k, fp = meta['key'], meta['data_fingerprint']
    if not force and k in _MEM:
        return _MEM[k]
    side = os.path.join(_bench_dir(aid), k + '.json')
    if not force and os.path.isfile(side):
        try:
            with open(side, encoding='utf-8') as f:
                _MEM[k] = json.load(f)
            return _MEM[k]
        except Exception:                                   # noqa: BLE001
            pass                        # 坏了就重算，不让它打挂整页
    try:
        curve = eng.run(verbose=False)
        # 从开户日截断。★ 基点用 `cash`：warmup 期没有任何交易，所以开户日
        #   前一天的权益就是 init_cash —— 与实盘 TWR 的起点（开户那一刻的
        #   资金）同口径，两条线这才能直接比。
        full = [(str(d), float(v)) for d, v in curve]
        idx = next((i for i, (d, _v) in enumerate(full) if d >= start), None)
        if idx is None:
            return {'error': '回测区间里没有 >= 开户日的交易日'}
        base0 = cash
        dates = [d for d, _v in full[idx:]]
        tv = [v for _d, v in full[idx:]]
        out = {
            'dates': dates,
            'nav': [None if v is None else float(v) / base0 for v in tv],
            'equity': [None if v is None else float(v) for v in tv],
            'start': start, 'end': end, 'cash': cash,
            'sha': meta['sha'], 'main_sha256': meta['main_sha256'],
            'params': meta['params'], 'data_fingerprint': fp,
            'fee': meta['fee'],
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

# 🔴 **回测的滑点假设** —— 与 `compute()` 里 `Cost(slippage=...)` 必须是同一个数。
#   写死两处迟早分叉，而分叉的表现是"执行差异凭空多出/少掉一截"。
BENCH_SLIP = 0.0015


def _strat_px(code, day, side, datalake=None):
    """策略在回测里的成交价 = 当日**开盘价** × (1 ± 滑点/2)。

    ★ 为什么用开盘价：froec/红利都是 `rebal_time='09:30'` 下单，引擎按当日
      开盘价撮合，而 A 股开盘价就是集合竞价成交价 —— 实盘挂竞价成交的也是它。
    🔴 **这才是"执行差异"该比的基准。** 头一版拿信号里的 `ref_price`
      （= T-1 **收盘**价）去比，量出来的是**隔夜跳空** —— 那既不是执行质量、
      也不是用户能控制的事（用户原话：「我为什么要关心隔夜跳空？这是我需要
      关心、能解决的事情吗？」）。ref_price 只是信号 T-1 晚估股数用的中间量，
      不该出现在这张表里。
    """
    from . import px as _px
    try:
        op = _px.day_price(code, day, 'open', datalake=datalake)
    except Exception:                                       # noqa: BLE001
        return None                 # 当天行情还没同步 -> 这一行不给策略价
    if not op:
        return None
    k = 1 + BENCH_SLIP / 2 if side == 'buy' else 1 - BENCH_SLIP / 2
    return op * k


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


def _cand_rank(aid, day):
    """那一期候选池里每只票的名次 -> {code: (rank, 池子总数, 组名, status)}。

    ★ 给「提示外买入」用：策略没让买它，但**它在候选池里排第几**才是有信息
      量的那一半 —— 排第 11 名（池子 20、策略取前 10）说明只差一名，
      而压根不在池里说明策略完全没考虑它。
    🔴 读**旁挂的选股理由**（`signals/_explain/<date>.json`），不重新算 ——
      重算一遍就是第二份口径，而"当时的候选池"是既成事实（同选股理由那条：
      理由是**捕获**来的，不是照策略再算一遍）。
    ★ 拿不到就返回空 dict，不报错：老信号（这个功能之前的那些期）没有旁挂，
      那时候只能显示"—"，不该让整块打不开。
    """
    p = os.path.join(_base.acct_dir(aid), 'signals', '_explain',
                     '%s.json' % day)
    if not os.path.isfile(p):
        return {}
    try:
        with open(p, encoding='utf-8') as f:
            d = json.load(f)
    except Exception:                                       # noqa: BLE001
        return {}
    out = {}
    for gi, g in enumerate((d.get('explain') or {}).get('groups') or [], 1):
        rows = g.get('rows') or []
        n = len(rows)
        gname = g.get('sql_head') or ('第 %d 组' % gi)
        for r in rows:
            c = _base.normalize_code(r.get('code') or '')
            if not c or c in out:
                continue        # 一只票在多组里时留**第一组**的名次（同选股理由页）
            out[c] = {'rank': r.get('rank'), 'of': n, 'group': gname,
                      'status': r.get('status'),
                      'dropped_by': r.get('dropped_by')}
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
    used = None
    if sig is None:
        # 🔴🔴 比的必须是【你下单时手上那一版】，不是"现在这份" ——
        #   主文件是执行完之后重算出来的，照着做完策略当然说"无事可做"，
        #   于是清单变空、**照做的每一笔都被判成"提示外"**（用户 2026-09-22
        #   报的就是这个：09-15 与 09-22 两期全变成提示外，而账本与当时那一版
        #   精确一致）。见 `sig.signal_as_of`。
        sig, used = _sig.signal_as_of(aid, day)
    if not sig:
        return {'date': day, 'error': '这一天没有信号'}
    got = _fill_map(aid, day)
    ranks = _cand_rank(aid, day)
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
    # ⚠ **已知偏差，2026-09-22 决定【不改】**（用户："8% 的容差已经很大了"）：
    #   `amount` 是按**限价**算的（`shares x limit`，limit = ref x 1.05），
    #   而 `got_amt` 是**实际成交额**（按成交价）—— 于是即使一股不差地照做，
    #   `scale` 也会系统性偏小约 5%，把人判成"超量 5%"。
    #   5% 在 8% 容差内不报错，但它**吃掉 5/8 的容差预算**（2026-09-15 那次
    #   正是它叠加取整才越线的，见下面 `want_cmp` 那段）。
    #   ★ 改法是让两边同口径（`shares x ref_price`），但那会改变**所有历史期**
    #     的 scale —— 属于口径变更不是修 bug，而取整那条修好之后容差够用。
    #     记在这里，别下次又当成"漏了"。
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
             'strat_px': _strat_px(
                 c, day, 'buy' if wb else 'sell', datalake=None),
             'got_buy': bs or None, 'got_sell': ss or None,
             'got_px': (g or {}).get('buy_px') if bs else (g or {}).get('sell_px'),
             'n_fills': (g or {}).get('n') or 0,
             # ★ 候选名次：`extra`（提示外买入）最需要它 —— 「第 11/20 名」
             #   说明只差一名，「不在候选池」说明策略完全没考虑过它。
             'cand': ranks.get(c)}
        # 🔴 `want_shares` 直接给**按实际本金折算后**的股数 —— 那才是
        #   「当时该买多少」。原始值（按信号里那个 cash 算的）与这个账户
        #   无关，摆出来只是噪声（用户指出：「后面加个『原』多少的意义是
        #   什么」）。缩放比例本身在 `scale_why` 里说清，不用逐行重复。
        # ★ 折算后按 100 股取整 —— A 股一手 100 股，给个 3,522 这种数
        #   照着下不了单（引擎内部也是 `int(value / (价 * 100))` 取整手）。
        want = float(r['want_shares'] or 0) * (scale if wb else 1.0)
        # 🔴 **取整只用于显示，比容差要用未取整的那个值。**
        #   `int(want/100)*100` 是**向下**取整：2600 x 0.843 = 2191.5 -> 2100，
        #   丢掉 4.2%，而 TOL 的注释写的正是"一手 100 股 + 归一化后的凑整
        #   误差" —— 先取整再比，等于把这份误差从容差里扣掉一遍。
        #   实测 2026-09-15：实际 2300 对未取整的 2191.5 只差 **4.95%**
        #   （在 8% 容差内 = 照做了），对取整后的 2100 却是 9.5% -> 判成
        #   `over`。而且向下取整是**单向**偏差，系统性地更容易报"超量"。
        want_cmp = want
        want = float(int(want / 100) * 100) if want else 0.0
        if wb or ws:
            r['want_shares'] = round(want) or None
        if wb:
            if not bs:
                r['kind'] = 'missed'
            elif want_cmp and abs(bs - want_cmp) / want_cmp <= TOL:
                r['kind'] = 'ok'
            elif bs < want_cmp:
                r['kind'] = 'short'
            else:
                r['kind'] = 'over'
        elif ws:
            r['kind'] = 'sell_miss' if not ss else 'ok'
        else:
            r['kind'] = 'sell_extra' if ss else 'extra'
        # 🔴 判据是**策略的成交价**，不是信号里的 ref_price（T-1 收盘）——
        #   后者量的是隔夜跳空，与执行无关。
        # 🔴 只有**策略确实点过名**的票才有"执行差异"可言。
        #   `extra`（提示外买入）在策略侧没有基准 —— 硬拿开盘价当基准会
        #   算出一个方向相反的数（实测 300375 给出 +0.0751%），而它读起来
        #   像"这一笔买贵了"，其实策略根本没打算买它。
        if r['strat_px'] and r['got_px'] and (wb or ws):
            r['px_diff'] = r['got_px'] / float(r['strat_px']) - 1.0
        elif not (wb or ws):
            r['strat_px'] = None          # 策略没让买/卖，没有基准价
        rows.append(r)

    n = {}
    for r in rows:
        n[r['kind']] = n.get(r['kind'], 0) + 1
    pxs = [r['px_diff'] for r in rows if r.get('px_diff') is not None]
    # 🔴 **补名字**：批量粘贴的成交（`source=bulk`）`name` 一律是空 ——
    #   其余行的名字来自**信号**，而「提示外买入」压根不在信号里，所以
    #   那一行就成了光秃秃的代码（用户指出的第一条）。服务端有 `names_of`，
    #   前端没有面板、补不了。
    miss = [r['code'] for r in rows if not (r.get('name') or '').strip()]
    if miss:
        # ★ `names_of` 在 **lv/px.py**，不在 base —— 头一版写 `_base.names_of`
        #   而 base 上没有这个名字，那个 `except Exception: pass` 把
        #   AttributeError 吞了，于是名字**照样是空的、还不报错**
        #   （项目纪律里「禁止吞异常」那条，我自己犯了一次）。
        from . import px as _px
        got = _px.names_of(miss) or {}
        for r in rows:
            if not (r.get('name') or '').strip():
                r['name'] = got.get(r['code']) or ''
    return {
        'date': day, 'rows': rows, 'counts': n,
        'scale': scale, 'scale_why': scale_why,
        'sig_cash': sig_cash or None, 'want_amt': want_amt or None,
        'got_amt': got_amt or None,
        'is_rebalance_day': sig.get('is_rebalance_day'),
        'n_want_buy': len(want_b), 'n_want_sell': len(want_s),
        # ★ 用的是哪一版**必须带出去** —— 静默换一版去比，页面上就是个
        #   说不清的差异（同「悄悄截断比查不出来更糟」）。
        'used': used,
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
        # 🔴 这里**不能把 `sig` 传下去** —— 传了就等于让 `diff_one` 拿
        #   "现在这份"去比，而它可能是执行完之后重算出来的空清单
        #   （用户 2026-09-22：照做的两期全被判成"提示外"）。
        #   让 `diff_one` 自己去挑【下单时手上那一版】。
        #   ★ 这里读主文件只为**筛掉空段**（既没提示也没成交的日子）。
        if not ((sig.get('buy') or sig.get('sell')) or _fill_map(aid, day)):
            continue
        out.append(diff_one(aid, day))
        if len(out) >= limit:
            break
    return out
