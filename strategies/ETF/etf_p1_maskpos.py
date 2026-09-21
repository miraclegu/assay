#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P1-MASKPOS：按【最终目标仓位】清仓，而不是按「在势只数」。

    python3 run.py strategies/ETF/etf_p1_maskpos.py --param mask_pos=0.6
    python3 run.py strategies/ETF/etf_p1_maskpos.py --param mask_pos=0      # 等价性自证

与 `etf_p1_mask.py` 的差别只有【看哪个量】：

    P1-MASK      len(in_trend) < mask_below      -> 清仓      在势只数
    本版         sum(w.values()) < mask_pos      -> 清仓      最终目标仓位

🔴🔴 **为什么值得单独测 —— 旧的「仓位 cut」结论作废了。**
2026-09-20 测过一轮按仓位阈值清仓（`etf_p1_gate` 的 `cut` 模式）：
cut50 9.59% / cut60 9.53% / cut70 9.56% / cut80 8.96%，四档全负。
**但那一轮用的是坏的摘除方式** `w = {}` —— 它触发 `_is_reselect_day` 里
`or (not g.target)`，让空仓期**逐日重选**（589 -> 1414 次），副作用吃掉 4.68pp。
「保留 keys、权重置 0」这个正确摘法是**之后**才发现的，所以按仓位 mask
**从来没有被正确测过**。本文件就是补这一次。

★ 两者高度相关但**不等价**：仓位 ~= GROSS x scale = 0.97 x min(1, 在势数/15)，
  所以 50/60/70/80% 大致对应在势 8/9/11/12 只。而最终仓位还受**簇上限**
  与 `_select` 影响 —— 「在势的票够多、但全挤在一个簇里，实际只配得出 40%
  仓位」这种情形**在势 mask 看不见，仓位 mask 看得见**。那是它可能更好的
  唯一机制来源；测不出差别的话，说明簇上限那一层基本不咬。

🔴 **阈值判在广度闸缩放【之后】、且不只在缩放分支里判** ——
  簇上限可以在满广度时就把仓位压下去，那恰恰是本版想抓的情形。
  写进 `if scale < 1` 里面的话，那一类就永远看不到（而它不报错）。
★ `mask_pos` 是**绝对仓位**（满仓 = GROSS = 0.97），不是"占 GROSS 的比例"。

---------------------------------------------------------------------------
**结论：按【削回撤】采纳，按【提收益】证不出来。**（2026-09-21，全程 2016-2026）

    配置          全程 年化/回撤/夏普      后段2021- 年化/回撤   逐年独立 vs 正本
                                                            均差/中位/胜/sd/t/去掉最好的一年
    正本 P1      10.12/26.74/0.609     16.59/27.01      —
    在势<12      11.89/22.54/0.709     20.51/19.94      +2.08/+0.68/7-11/ 4.64/+1.49/+1.23
    仓位<50%     10.53/27.80/0.635     19.08/24.01      +0.96/+0.14/6-11/ 2.74/+1.16/+0.53
    仓位<60%     12.45/24.34/0.737     22.37/20.42      +3.49/+2.02/7-11/ 8.34/+1.39/+1.20
    仓位<70%     13.51/21.83/0.801     24.14/21.59      +5.29/+3.68/7-11/10.70/+1.64/+2.36
    仓位<80%     13.00/21.46/0.782     22.78/21.76      +4.77/+2.90/6-11/11.05/+1.43/+1.79
    自证：mask_pos=0 与正本逐日权益指纹逐位相同（afaf7a4b374d）

🔴 **收益侧证不出来**：t=1.64 < 2.23；那 +5.29pp 里 **2026 单年就占 +34.59pp**
  （而 2026 只有 8 个半月、还做了年化外推），去掉它只剩 +2.36pp；
  **中位数 8.47% 比正本的 11.03% 还低** —— 多数年份它并不更好。

🔴🔴 **回撤侧才是它的价值，而且这是本轮唯一过线的统计量**：

    回撤差（仓位<70 − 正本）  平均少 1.94pp ｜ 11 年里 7 年更浅
                            **最坏的一年只多 0.97pp** ｜ t = −2.25（过 2.23）
    四档单调：正本 14.10 -> <50 13.94 -> <60 12.63 -> <70 12.16 -> <80 11.96

  ★ **单调关系比"从 4 个格子里挑最大值"硬** —— 后者 t 值天生虚高
    （本项目栽过），前者是趋势。多重比较校正后 2.25 够不着 2.9，
    **所以这条结论靠的是单调性，不是那个 t 值**。如实记下来。
  ★ 同「降仓是削回撤的，不是提收益的 —— 一个规则可以不提高收益但仍然
    值得留，判据是风险不是年化」与「lu_buy_only 采用它的理由不是收益」。

★ 「保险」这个说法**部分成立**：正本亏损的 4 年里 3 年改善（均 +3.55pp），
  而保费交在好年（2020 −4.87 / 2019 −2.81 / 2017 −1.77，那三年正本
  分别赚 23.66/11.03/10.24%）。🔴 但**最大的那笔不是赔付** ——
  2026 那 +34.59pp 发生在正本也赚 49.42% 的大牛年，与保险无关。

**70 还是 80 —— 选 70，而且理由不是收益**：
    回撤 80 只比 70 好 **0.20pp**（11.96 vs 12.16），几乎无差；
    而 80 在 **2021 单年比 70 少 9.42pp**（12.34 vs 21.76）。
    拿 0.20pp 的回撤去换一个 9.4pp 的单年坑，不划算。
---------------------------------------------------------------------------
"""
import os
import importlib.util

from assay.api import *          # noqa: F401,F403

DATALAKE = 'etf_lake'            # 与正本同一个 lake（策略自己声明）

_BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     'etf_p1_rotation.py')
_spec = importlib.util.spec_from_file_location('_p1_base_maskpos', _BASE)
_p1 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_p1)

for _n in dir(_p1):
    if not _n.startswith('__'):
        globals().setdefault(_n, getattr(_p1, _n))


def _reselect_maskpos(context, sd):
    """与正本逐行相同，只在末尾多一道「仓位低于阈值就清仓」。"""
    pool = _p1._dynamic_pool(context, sd)
    if not pool:
        return
    info = _p1._signals(_p1._px(context, sd, pool, _p1.NEED_FULL))
    if not info:
        return
    in_trend = set(c for c, v in info.items() if v['in_trend'])
    rs = _p1._rs_ok(info)
    ranked = sorted([s for s in in_trend if s in rs],
                    key=lambda s: (-info[s]['mom'], s))
    members = _p1._select(list(g.target), ranked, info)
    w = _p1._weights(members, info)
    scale = min(1.0, len(in_trend) / (_p1.BREADTH_FRAC * _p1.POOL_N))
    if scale < 1.0:
        g.n_breadth += 1
        w = {s2: x * scale for s2, x in w.items()}
    # 🔴 在这里判，不在上面那个分支里 —— 簇上限可以在满广度时就把仓位压低，
    #   而那正是本版相对「在势 mask」唯一的信息增量。
    if g.mask_pos > 0 and w and sum(w.values()) < g.mask_pos:
        g.n_maskpos += 1
        # 🔴 保留 keys、权重置 0（不是 `w = {}`，见文件头）
        w = {s2: 0.0 for s2 in w}
    g.target = w


def initialize(context):
    # 目标仓位低于它就清仓；0 = 关闭（等价正本）。绝对仓位，满仓 = GROSS = 0.97
    # ★ 默认 0.70 的理由见文件头「70 还是 80」那节 —— **不是因为它年化最高**。
    g.mask_pos = float(getattr(g, 'mask_pos', 0.70))
    g.n_maskpos = 0
    _p1.initialize(context)
    # ★ 换模块属性 —— `my_trade` 里是全局查找 `_reselect(...)`，换得掉
    _p1._reselect = _reselect_maskpos
