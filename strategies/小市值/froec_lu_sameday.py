# -*- coding: utf-8 -*-
"""FROEC-LU-SAMEDAY：**当天**盘中涨停、尾盘没封住 -> **当天就卖**。

与另两个版本的区别（三者判据都不同，别记混）：

    froec_traded（原版）      昨天**收盘封住** + 今天没封住 -> 今天卖
    froec_lu_intraday        昨天**盘中摸到**、昨天收盘没封住 -> **次日**卖
    froec_lu_sameday（本版）  **今天**盘中摸到、今天尾盘没封住 -> **当天**卖

判据（`exit_time`，默认 14:00，与原版同一时刻）：

    context.current(持仓)  ->  touch_up 为真（今天盘中摸到过涨停）
                               且 limit_up 为假（今天收盘没封在板上）
                           ->  order_target_value(code, 0)

🔴🔴 **必须说清这里的近似。** `touch_up` 是**全天**最高价派生的、`limit_up`
是**收盘**价派生的，而任务跑在 14:00 —— 严格说 14:00 那一刻还不知道
14:00~15:00 会不会回封。所以本版在回测里会：

  · 卖掉一些「14:00 后又回封」的票（实盘那时你不会卖）
  · 而「14:00 前就炸板」的那些，实盘与回测一致

★ 但这**不是本版新引入的问题**：原版的 `check_limit_up` 在 14:00 读的
  `limit_up` 同样是收盘派生量。froec.py 的注释写明了这条既有约定 ——
  「引擎无分时线，盘中相位用【收盘价】代理」。本版沿用同一套约定，
  不引入新的价格假设；要更准就得有分时线，那是另一个量级的工程。

★ 卖出价同样按引擎的约定（收盘价代理）—— 判据与执行价是**同一个价**，
  所以不存在"用未来信息拿到更好的成交价"这种偏差。

**怎么实现**：`importlib` 私有实例，只替换 `check_limit_up`。
froec.py / froec_traded.py **一行没动**（它们的归档要可比，实盘账户绑着快照）。

--------------------------------------------------------------------
🔴🔴 **回测结论（2026-09-10 重跑，参数口径已修正）：不采纳。**

★ **第一批数字全部作废** —— 那时 `_TRADED` 是凭印象拼的
  （`stop_intraday`/`stop_loss`/`weekday`），而 froec_traded 实际设的是
  `paused_in_pool`/`kcb_688_only`/`weekday`。两版参数不同 -> 止损行为不同
  -> 建仓日买的股数就不一样 -> 整条路径不同。现在改成加载 froec_traded 本身，
  并自证「`limit_up_exit=0` 时三版逐位一致」。

逐年独立（每年重置 50 万；2026 到 09-08），vs 现状 froec_traded：

    无抑制器   均 +1.43pp  中位 +0.44  更好 6/11  sd  7.24  t=+0.65
    补齐后     均 +0.47pp  中位 -0.26  更好 5/11  sd 20.62  t=+0.08

🔴🔴 **补齐「再买抑制器」之后反而更差 —— 这是关键发现。**

无抑制器版有个**规则不自洽**：卖出用 `touch_up`（盘中摸到涨停）、
而黑名单用 `had_limit_up()`（= 面板 `is_limit_up` = **收盘封住**）——
两个判据**互斥**，所以被本规则卖掉的票黑名单永远查不到。实测全程
炸板卖出 269 笔里 **37 笔在 10 个自然日内又买回来**（现状是 **0 笔**），
其中 5 笔是「周一卖、周二买」—— 周二正好是调仓日（weekday=2）。

补齐之后（`g.sd_banned`，窗口同 `limit_days`=20 交易日，包装 `blacklist`
所以两个调用点都覆盖）：10 日内买回 37 -> **0 笔**，规则自洽了。但：

    均差 +1.43 -> +0.47pp     中位 +0.44 -> **-0.26**（由正转负）
    sd   7.24  -> 20.62       2024 那年 +16.31 -> **+0.40pp**

★ **所以那 +1.43pp 相当一部分恰恰来自那 37 次「卖了又买回」** —— 不是
  「躲开下跌」的收益，而是在同一只票上做了一次日内低买高卖（卖在炸板价、
  次日买回）。而回测里「14:00 用收盘价代理」的偏差正好让这种操作系统性偏乐观。
★ 补齐后更差的机制也清楚：抑制器把票锁在场外 20 个交易日 ≈ **4 个调仓周期**，
  即使它排名一直在前 10 —— 那是在主动偏离目标池。原版黑名单没这问题，
  因为它锁的是「收盘封住过」的票，那种票次日往往高开、本来就不该追。

★ 结论：**两个版本都不采纳**。无抑制器的不自洽、补齐的更差 ——
  说明这条规则**没有可靠的机制**。
"""

import importlib.util
import os

_HERE = os.path.dirname(os.path.abspath(__file__))


def _private(name):
    """froec.py 的**私有实例** —— `module_from_spec` 每次都是新对象，
    在它上面 monkeypatch 不会污染 froec.py 的其它使用者。"""
    spec = importlib.util.spec_from_file_location(
        'froec_lu_sameday__base', os.path.join(_HERE, name))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


_traded = _private('froec_traded.py')
_base = _traded._base          # froec_traded 自己加载的 froec 私有实例



# ============ 卖出之后的【再买抑制器】============
#
# 🔴🔴 **没有它，这条规则不自洽。** 实测（全程 2016~2026）：炸板卖出 269 笔里
#   有 **37 笔在 10 个自然日内又被买回来**，其中 5 笔是「周一卖、周二买」——
#   周二正好是调仓日（weekday=2），那只票排名还够，于是立刻买回。
#   而现状那条规则「10 日内买回 = 0 笔」。
#
# 根因是**两个判据互斥**：
#     卖出用      `touch_up`（盘中摸到涨停）且 `limit_up=false`
#     黑名单用    `had_limit_up()` -> SQL 的 `AND limit_up` = 面板 is_limit_up
#                 = **收盘封住**
#   被本规则卖掉的票 `is_limit_up` 一定是 false，所以 `had_limit_up` 永远
#   查不到它 —— 卖出完全没有对应的买入抑制。
#
# ★ 这是 CLAUDE.md 那条纪律的直接应用：
#     「卖出问的是『它还够好吗』，**黑名单是『再买』的抑制器**」
#   现在的实现只有卖出、没有抑制器，于是「今天卖、明天买」，白付两次费用
#   （froec 实测万1.5，5 元最低佣金对每笔都 binding）。
#
# ★ 包装 `blacklist` 本身而不是改两个调用点：froec 里它被调用 **2 次**
#   （`rebalance` 与 `hold_buffer_strict` 那条路径），改一处漏一处的表现是
#   「缓冲区那条路径还是旧口径」—— 而它不报错，只是两条路径对同一只票
#   给出不同结论（froec.py 抽出这个函数时记过同样的教训）。

def _sd_ban(context, cand, d):
    """原版黑名单 + 「被本规则卖掉的票」。

    ★ 窗口用同一个 `g.limit_days`（20 日）—— 与原版黑名单同一口径，
      不引入第二个要记的数字。
    """
    g = _base.g
    out = set(_base_blacklist(context, cand, d))
    if not g.limit_up_exit or not getattr(g, 'sd_banned', None):
        return out
    # 🔴 窗口按**交易日**算，不是自然日：`nth_prev_day` 是引擎的口径，
    #   自己按 timedelta 减天数会在长假处多算/少算（同 rebal_every 那条）。
    lo = context.data.nth_prev_day(context.current_date, g.limit_days)
    cs = set(cand)
    out |= {c for c, day in g.sd_banned.items() if c in cs and day > lo}
    return out


def _check_limit_up(context):
    """原版的炸板离场 + 「**今天**盘中炸板」这一类。

    ★ 先跑原版（它管 `g.high_limit`：昨天封住、今天没封住），再补本版。
      两者可能命中同一只票（昨天封住、今天又摸了一次涨停还是没封住）——
      `order_target_value(code, 0)` 幂等，重复下单不会卖两次。
    """
    g = _base.g
    _base_check_limit_up(context)
    if not g.limit_up_exit:
        return
    held = [c for c in context.portfolio.positions]
    if not held:
        return
    cur = context.current(held)
    for code in held:
        d = cur.get(code)
        if d is None:
            continue
        # 🔴 `touch_up` 只在 INTRADAY/CLOSE 相位存在（收盘派生量）。
        #   `d.get('touch_up')` 在 OPEN 相位是 None -> 不触发，正确。
        #   ★ 不能写 `not d.get('limit_up')` 就卖 —— 那会把**没涨停过**的
        #     全卖掉（原版踩过同款：OPEN 阶段取不到 limit_up 得 None）。
        if d.get('touch_up') and not d.get('limit_up'):
            _base.order_target_value(code, 0)
            # ★ 记进抑制器：判据与卖出**同一个**，这样"卖了又立刻买回"
            #   不会再发生（见上面 _sd_ban 的注释）。
            g.sd_banned[code] = context.current_date


_base_check_limit_up = _base.check_limit_up
_base.check_limit_up = _check_limit_up
_base_blacklist = _base.blacklist
_base.blacklist = _sd_ban


def initialize(context):
    _base.g.sd_banned = {}
    _traded.initialize(context)   # 🔴 参数口径由 froec_traded 定，不自己拼


prepare = _base.prepare
rebalance = _base.rebalance
rebalance_buy = _base.rebalance_buy
check_limit_up = _check_limit_up
stop_check = _base.stop_check
pf_check = _base.pf_check
