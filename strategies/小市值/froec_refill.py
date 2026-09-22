# -*- coding: utf-8 -*-
"""froec_traded + 【按卖出原因区分补不补位】（用户 2026-09-22）。

问题：「现在炸板和止损导致的卖出是不是都会不补位？如果逻辑改成炸板卖出补位、
       止损卖出不补位，会如何？」

🔴 **先把现状核实清楚了，因为"都不补位"这个前提不准确**（实测 2016-01-01~
  2026-09-21，实盘那套参数）：

    名额空出之后多久被填回来        n     中位   均值   p90   最长
      炸板离场 (reason=intraday)  190    5 天  11.8   28   126 天
      止损     (reason=stop)        8    2 天  12.5   29    84 天
      调仓换出 (reason=rebalance)  355   10 天  15.6   35   122 天

    调仓日买完之后的持仓数：10 只 39.3% / 9 只 36.4% / 8 只 16.3% / <=7 只 8.1%
    -> **买完仍不足 10 只的占 60.7%**

  所以现状不是"不补位"，是**「只在调仓日补、而且六成补不满」**。
  炸板与止损都在 **14:00** 卖（`run_daily(check_limit_up, '14:00')` /
  `run_daily(stop_check, '14:00')`），而买入腿只在调仓日 09:30 ——
  中间那段现金就一直躺着（实测最极端一次攒了 11 个交易日、66% 仓位，
  然后一个调仓日全倒给一只票，那只占到 74.3%）。

🔴🔴 **`reason` 是按【相位】记的，不是按规则** —— 这一点极容易读反，
  我就读反过一次：

      broker.py:338   reason = 'rebalance' if phase == OPEN else 'intraday'
      broker.py:375   order_stop_sell -> reason='stop'

  `check_limit_up` 跑在 14:00 = INTRADAY，所以**炸板离场记的是 `intraday`**，
  而 `stop` 才是止损。已逐笔验证：**203 笔 `intraday` 有 203 笔是前一日涨停的
  票（100%）**，`stop` 与 `rebalance` 都是 0%。

本策略做的事：把 14:00 卖出腾出的名额记下来，**次日 09:30（开盘价）补**，
并且**按卖出原因分别开关**：

    refill_lu    默认 1   炸板离场腾出的名额 -> 次日开盘补
    refill_stop  默认 0   止损腾出的名额     -> 不补（维持现状，等调仓日）

★ **两个都设 0 就等价于原版** —— 等价性自证用它。
★ 买多少**沿用原版口径**（`现金 / 要买几只`），不动分配方式。
🔴 **补位的候选来自【未截断的候选池】，不是 target**（见 `_spy_query`）——
  第一版拿 target 找替补，实测 **52% 的炸板卖出次日根本没票可补**，
  于是整条改动几乎空转（买入笔数只从 880 增到 909）。

🔴 **为什么补位要放在次日 09:30 而不是当天 14:00**：引擎没有分时线，
  `_phase()` 把 09:30 之后到 15:00 全归 INTRADAY 并**一律用当日收盘价成交**。
  当天 14:00 补位等于"用收盘价买"，那是**系统性偏乐观**的（本项目为
  `froec_lu_sameday` 那轮记过：+1.43pp 里相当一部分来自这种日内往返）。
  次日 09:30 属 OPEN 相位、用**开盘价**，是可实盘执行的口径。

★ **froec.py / froec_traded.py 一个字节没改**：`importlib` 私有加载，
  并且在 `_base.initialize` 跑**之前**把 `check_limit_up` / `stop_check`
  换成包装版 —— `run_daily(check_limit_up, ...)` 那一行是在 initialize 里
  执行的，它取的是**那一刻**的模块属性，所以预先替换就能被注册进去。
  🔴 反过来说：**initialize 跑完之后再换模块属性对已注册的任务无效**
  （CLAUDE.md 为这条踩过两次）。两者的分工别记反。
"""
import importlib.util
import os

from assay.api import g, log, order_target_value, run_daily

_HERE = os.path.dirname(os.path.abspath(__file__))


def _private(fn):
    p = os.path.join(_HERE, fn)
    spec = importlib.util.spec_from_file_location('_priv_' + fn[:-3], p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


_traded = _private('froec_traded.py')
_fb = _traded._base

SQL = _fb.SQL
EXCL_IND = _fb.EXCL_IND
prepare = _traded.prepare
rebalance = _traded.rebalance
stop_filter = _traded.stop_filter
pf_check = _traded.pf_check
rebalance_buy = _traded.rebalance_buy
check_limit_up = _traded.check_limit_up
stop_check = _traded.stop_check

_orig_lu = _fb.check_limit_up
_orig_stop = _fb.stop_check


def _spy_query(orig):
    """包住 `context.data.query`，把末层选股 SQL 返回的【未截断】清单记下来。

    🔴🔴 **补位必须用候选池，不能拿【已截断的 target】去找替补**（用户
      2026-09-22 指出）。第一版我用的就是 target，结果实测
      **52% 的炸板卖出次日根本没票可补** —— 调仓刚买完，target 里没持有的
      本来就所剩无几。

    ★ 而候选池里**本来就有现成的富余**，不用新查一次：

          _ask = max(lim, g.explain_pool)      # = max(10, 20) = 20
          df   = context.data.query(SQL, cand=_ask, ...)
          cand = df['jq_code'].tolist()[:lim]  # ← 这一刀截到 10

      SQL 每次返回 **20** 只（多出来的原本只给"选股理由"看），截断扔掉 10 只。
      这里只是把那 10 只记下来，**纯观察，一行行为都没改**。

    ⚠ 必须说清：**用这些富余的票补位，本质上就是 CLAUDE.md 警告过的"补位"**
      （`fill_paused` / `fill_blacklist`，实测 −0.81pp）。区别在**触发条件**：
      那两个开关是**每个调仓日无条件补满**，而这里只在【炸板离场腾出名额】时、
      【只补那几个名额】、【次日开盘】补。是不是同一回事要靠实测分开，
      不能因为"看着像"就套用那条结论。
    """
    def _q(sql, **kw):
        df = orig(sql, **kw)
        try:
            if sql == _fb.SQL:
                g.reserve = list(df['jq_code'])
        except Exception:                                   # noqa: BLE001
            pass
        return df
    return _q


def _wrap_sell(orig, flag):
    """包住一个 14:00 的卖出任务，数它卖掉了几只 -> 记成待补的名额。"""
    def _f(context):
        n0 = len(context.portfolio.positions)
        orig(context)
        if int(getattr(g, flag)):
            g.refill_slots += max(0, n0 - len(context.portfolio.positions))
    _f.__name__ = orig.__name__
    return _f


def _refill(context):
    """次日 09:30（开盘价）把昨天 14:00 腾出的名额补上。

    ★ 注册在 `_traded.initialize` **之后**，而引擎 `self._tasks.sort(key=t[0])`
      只按时间排且 Python 的 sort 是**稳定**的 —— 所以同在 09:30 时它排在
      `rebalance` 后面。调仓日 rebalance 已经补过，这里自然就没什么可买了。
    ★ 无论买没买成，**待补名额都清零**：它的语义是"昨天腾出来的"，
      攒着会让一次行情把好几天的名额一起倒出去，那正是要治的毛病。
    """
    slots, g.refill_slots = int(g.refill_slots), 0
    if slots <= 0:
        return
    pf = context.portfolio
    room = int(g.stock_num) - len(pf.positions)
    if room <= 0 or pf.cash <= 0:
        return
    # ★ 从【未截断的候选池】取，不是从 target 取（见 `_spy_query`）。
    #   `refill_pool` 限制往下挖多深：0 = 用全部富余。
    res = list(getattr(g, 'reserve', None) or [])
    if int(g.refill_pool) > 0:
        res = res[:int(g.refill_pool)]
    cand = [c for c in res if c not in pf.positions]
    if not cand:
        return
    # 🔴 补位要走【和买入腿同样的三道过滤】，否则会把刚炸板卖掉的那只
    #   当场买回来（它就在目标池里），等于把炸板离场这条规则废掉。
    #   20 日涨停黑名单本来就挡得住它 —— 但必须显式走一遍，不能指望。
    cand = _fb.stop_filter(context, context.tradable(cand, 'buy'))
    drop = _fb.blacklist(context, cand, context.previous_date)
    if drop:
        cand = [c for c in cand if c not in drop]
    k = min(slots, room, len(cand))
    if k <= 0:
        return
    per = pf.cash / k          # ★ 沿用原版口径：唯一的变量是【时点】
    for code in cand[:k]:
        order_target_value(code, per)
    g.n_refill += k
    log.info('[REFILL] 补 %d 只（待补 %d / 空位 %d / 候选 %d）：%s',
             k, slots, room, len(cand), cand[:k])


def initialize(context):
    g.refill_lu = getattr(g, 'refill_lu', 1)
    g.refill_stop = getattr(g, 'refill_stop', 0)
    g.refill_pool = getattr(g, 'refill_pool', 0)   # 0 = 用全部候选池富余
    g.refill_slots = 0
    g.n_refill = 0
    g.reserve = []
    # 纯观察：记下末层 SQL 的未截断清单（不改任何行为）
    context.data.query = _spy_query(context.data.query)
    # 🔴 **必须在 `_traded.initialize` 之前**换掉这两个 —— 里面那两行
    #   `run_daily(check_limit_up, ...)` / `run_daily(stop_check, ...)`
    #   注册的是【执行到那一行时】的模块属性值。
    _fb.check_limit_up = _wrap_sell(_orig_lu, 'refill_lu')
    _fb.stop_check = _wrap_sell(_orig_stop, 'refill_stop')
    _traded.initialize(context)
    # 反过来，`run_daily` 本身要在 initialize **之后**调，才排在 rebalance 后。
    run_daily(_refill, time=g.rebal_time)
