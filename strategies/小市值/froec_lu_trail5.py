# -*- coding: utf-8 -*-
"""FROEC-LU-TRAIL5：炸板分两档处理 —— 弱的立刻卖，强的转 5 日线跟踪。

用户的规则：

    炸板（盘中摸到涨停、收盘没封住）
      ├─ 当日涨幅 **< 5%**  -> **立刻卖**（弱，说明抛压重）
      └─ 当日涨幅 **>= 5%** -> **不卖**，转入「5 日线跟踪」
                               此后每日收盘 < MA5 -> 卖出
    两条路径卖出后**都进黑名单**（`limit_days` = 20 交易日）

★ 与前两个变体的关系（三者判据都不同，别记混）：

    froec_traded（现状）   昨天**收盘封住** + 今天没封住 -> 卖
    froec_lu_sameday       今天盘中摸到、尾盘没封住 -> **一律**当天卖
    froec_lu_trail5（本版） 同上，但**按当日涨幅分档**，强的留下用 MA5 跟踪

🔴 **为什么要分档**：`froec_lu_sameday` 的结论是「炸板一律卖」证不出好坏
  （均差 +0.47pp、中位 −0.26、t=+0.08，见那个文件的结论块）。一个可能的
  原因是它**不分强弱**：炸板后仍涨 8% 的票与炸板后倒跌的票被同等对待。
  本版就是检验这个假设。

🔴 **卖出后必须进黑名单，这是 froec_lu_sameday 那一轮的教训。**
  卖出用 `touch_up`（盘中摸到涨停）而原版黑名单用 `had_limit_up`（**收盘封住**）
  —— 两个判据**互斥**，所以不补抑制器的话被卖掉的票次日就能买回：
  实测那一版全程 269 笔炸板卖出里 **37 笔 10 日内又买回**（现状 0 笔），
  5 笔是「周一卖、周二买」而周二正好是调仓日。
  ★ 包装 `blacklist` 本身而不是改调用点：froec 里它被调用 **2 次**
    （`rebalance` 与 `hold_buffer_strict`），改一处漏一处不报错、
    只是两条路径对同一只票给出不同结论。

参数：
    lu5_gain    默认 0.05   分档阈值（当日涨幅）
    lu5_ma      默认 5      跟踪用几日线（目前只支持 5 —— Bar.ma5）
    lu5_ban     默认 1      卖出后进黑名单（0 = 不进，用来单独量这一条的贡献）

数据依赖（都是 `feed.py` 的**投影**改动，原策略行为一行未变）：
    `Bar.change_pct`  面板现成的当日涨幅
      🔴 **不要拿后复权价自己算** —— 实测 2024 全年 123 万行里，
        `change_pct` 与 `close_bfq/preclose − 1` **零偏差**，而后复权比值
        在**低价股**上差很多（27 万行偏差 >0.02pp、最大 1.9pp）：不复权价
        只有 2 位小数，0.33 -> 0.34 是 +3.03%，后复权算出 +1.75%。
        两个都"看着像涨幅"，而错的那个在小盘低价股上系统性偏小 ——
        正是这个策略的持仓所在。
    `Bar.ma5`         后复权收盘的 5 日移动平均（含当日，SQL 窗口函数算）
      🔴 区间头几天**不足 5 根**时它给的是**部分均值**，所以本策略自己记
        `g.lu5_since`（进入跟踪的日期），只在**跟踪开始之后**才用 MA5 判卖出
        —— 直接信 ma5 的话建仓头几天会拿一个 2 根的"均线"去比。

## 🔴 结论（2026-09-13 定案）：**不采纳**

基线 = `froec.py` 带 `lu_buy_only=1`（本版也带这个参数，必须同参数才可比）。
**逐年独立回测 2016~2026**（每年重置本金，这是本项目比较规则的唯一口径）：

    均差 +3.80pp   中位 +1.47   胜 6/11   sd 27.41   **t=+0.46**
    去掉 2026：+9.63pp (t=+1.49)      去掉 2016+2026：+4.69pp (t=+1.00)

**全程 2016-01-01 ~ 2026-09-11**（同一份数据、同参数基线）：

                年化      回撤     夏普    笔数   换手      费用
      基线     38.74%   47.22%   1.264   886   9.06   368,913
      本版     39.07%   47.53%   1.324   881   9.18   **482,615**

★ 全程只多 +0.33pp，而**费用多付 11.4 万（+31%）** —— 收益不确定、成本确定。
  逐年平均夏普(1.453 vs 1.326)与平均回撤(19.32% vs 19.71%)确实略优，
  但 t=0.46 撑不住任何结论。
🔴 效应几乎全来自两个互相抵消的极端年：**2016 +54.09pp / 2026 −54.48pp**，
  绝对值都远大于均差本身。

### 🔴 `lu5_ban=0` 看着更好（41.91%），而那是【卖了又买回】

    ban=1   intraday 卖出 252 笔，10 日内买回 **0** 笔（与基线一致）
    ban=0   intraday 卖出 263 笔，10 日内买回 **39** 笔（14.8%），间隔中位 5 天

与 `froec_lu_sameday` 那次「+1.43pp 里相当一部分来自 37 笔卖了又买回」
**完全同形**：卖在炸板价、几天后买回，实质是在同一只票上做了一次日内低买高卖，
而回测「14:00 用收盘价代理」的偏差正好让这种操作**系统性偏乐观**。
所以 ban=0 的那 +2.84pp 不能当成收益 —— 这也再次印证「卖出判据与再买抑制器
必须是同一个」那条纪律。

### ★ 那个"分强弱"的假设本身没有被证伪，只是证不出来

本版的出发点是「`froec_lu_sameday` 不显著也许因为它不分强弱」。数据的回答是：
分了强弱之后**仍然不显著**（t=+0.46 vs 那版的 t=+0.08）。两条路都试过之后
仍然撑不起结论 -> 这条规则**没有可靠的机制**，同「炸板离场放宽」那一轮的结论。
"""

import importlib.util
import os

_HERE = os.path.dirname(os.path.abspath(__file__))


def _private(name):
    """froec_traded.py 的**私有实例**。

    🔴 加载 froec_traded 而**不是自己拼参数** —— 前一轮三个文件都栽在这里：
      凭印象写的 `_TRADED` 与它真实设的参数不同（它设的是 `paused_in_pool` /
      `kcb_688_only` / `weekday`），导致建仓日买的股数就不一样、整条路径不同，
      那几轮结论全部作废。
    """
    spec = importlib.util.spec_from_file_location(
        'froec_lu_trail5__traded', os.path.join(_HERE, name))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


_traded = _private('froec_traded.py')
_base = _traded._base


def _sd_ban(context, cand, d):
    """原版黑名单 + 「被本规则卖掉的票」。窗口同 `g.limit_days`。"""
    g = _base.g
    out = set(_base_blacklist(context, cand, d))
    if not g.lu5_ban or not getattr(g, 'lu5_banned', None):
        return out
    # 🔴 窗口按**交易日**算（`nth_prev_day` 是引擎口径）——
    #   自己按 timedelta 减天数会在长假处多算/少算。
    lo = context.data.nth_prev_day(context.current_date, g.limit_days)
    cs = set(cand)
    out |= {c for c, day in g.lu5_banned.items() if c in cs and day > lo}
    return out


def _check_limit_up(context):
    """原版炸板离场 + 本版的分档处理。

    ★ 先跑原版（管 `g.high_limit`：昨天封住、今天没封住），再叠本版。
      `order_target_value(code, 0)` 幂等，两者命中同一只票不会卖两次。
    """
    g = _base.g
    _base_check_limit_up(context)
    if not g.limit_up_exit:
        return
    held = list(context.portfolio.positions)
    if not held:
        return
    cur = context.current(held)
    d = context.current_date

    def sell(code, why):
        _base.order_target_value(code, 0)
        if g.lu5_ban:
            g.lu5_banned[code] = d
        g.lu5_since.pop(code, None)
        _base.log.info('[LU5] %s %s -> 卖出（%s）', d, code, why)

    for code in held:
        row = cur.get(code)
        if row is None:
            continue
        # ---- ① 已在跟踪中：收盘 < MA5 就卖 ----
        if code in g.lu5_since:
            # 🔴 只在**跟踪开始之后**才用 MA5：区间头几天 ma5 是部分均值。
            #   而跟踪一定是从某个炸板日开始的，那天之前必有 >= 5 根 K 线
            #   （建仓需要 listed_days >= 250），所以这个判据是安全的。
            if d <= g.lu5_since[code]:
                continue                     # 进入跟踪那天本身不判
            cl, ma = row.get('close_hfq'), row.get('ma5')
            if cl is not None and ma is not None and cl < ma:
                sell(code, '跟踪中收盘 %.4f < MA5 %.4f' % (cl, ma))
            continue
        # ---- ② 今天炸板吗 ----
        if not (row.get('touch_up') and not row.get('limit_up')):
            continue
        # ---- ③ 分档 ----
        chg = row.get('change_pct')
        if chg is None:
            continue                         # 拿不到涨幅：不猜，也不动
        if chg < g.lu5_gain * 100.0:         # change_pct 是**百分数**（2.54 = 2.54%）
            sell(code, '炸板且涨幅 %.2f%% < %.1f%%' % (chg, g.lu5_gain * 100))
        else:
            g.lu5_since[code] = d
            _base.log.info('[LU5] %s %s 炸板但涨幅 %.2f%% >= %.1f%% -> 转 MA5 跟踪',
                           d, code, chg, g.lu5_gain * 100)


_base_check_limit_up = _base.check_limit_up
_base.check_limit_up = _check_limit_up
_base_blacklist = _base.blacklist
_base.blacklist = _sd_ban


def initialize(context):
    g = _base.g
    g.lu5_gain = getattr(g, 'lu5_gain', 0.05)
    g.lu5_ma = getattr(g, 'lu5_ma', 5)
    g.lu5_ban = getattr(g, 'lu5_ban', 1)
    if int(g.lu5_ma) != 5:
        raise ValueError('目前只支持 5 日线（Bar.ma5）；要别的周期得先在 '
                         'feed.py 的 _BAR_COLS 里加那一列')
    g.lu5_banned = {}
    g.lu5_since = {}
    _traded.initialize(context)   # 🔴 参数口径由 froec_traded 定，不自己拼


prepare = _base.prepare
rebalance = _base.rebalance
rebalance_buy = _base.rebalance_buy
check_limit_up = _check_limit_up
stop_check = _base.stop_check
pf_check = _base.pf_check
