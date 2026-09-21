#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P1-MASK：广度不足时**清仓**，而不是按比例降仓。

    python3 run.py strategies/ETF/etf_p1_mask.py --start 2020-01-01
    python3 run.py strategies/ETF/etf_p1_mask.py --param mask_below=10

与正本 `etf_p1_rotation.py` 的**唯一**差别在 `_reselect` 末尾那个广度闸门：

    正本   scale = min(1, len(in_trend)/15);  w = {s: x*scale}   # 按比例降仓
    本版   len(in_trend) < mask_below  ->  w = {s: 0.0 ...}       # 清仓
           否则照正本降仓

🔴🔴 **关键在"怎么清"，而不是"清不清"。** 写成 `w = {}` 会同时触发两件
与清仓无关的事（本项目 2026-09-20 连测六个变体都栽在这上面）：

    ① `_is_reselect_day` 里有 `or (not g.target)` -> 空仓期**逐日重选**
       （重选次数 589 -> 1133~1414，恢复时点从"下周四"变成"达标当天"）
    ② `_select(list(g.target), ...)` 拿不到上期 keys -> **缓冲汰换记忆被清空**

所以本版**保留 keys、权重全置 0**：字典非空 -> 两条副作用都不触发，
而 `_place` 在重选日照样 `order_target_value(code, 0)` 真的清仓。
★ 判据：**重选次数必须与正本逐位相同（589 次）** —— 那是"只改了仓位、
  没改节奏"的唯一硬证据。写回 `w = {}` 立刻变成 1414，当场可见。

**代价有多大**（全程 2016-01-01~2026-09-18，10 万，默认成本）：

    理论上界（摘掉 n<15 那 1200 天，零副作用）      年化 11.48%
    本版 mask_below=15（纯摘除）                   10.96% / 回撤 21.82% / 重选  589
    `w = {}` 那版（= flat）                         6.28% / 回撤 32.79% / 重选 1414
                                                    ↑ 副作用吃掉 4.68pp

    mask_below=10   11.90% / 25.06% / 2448 笔 / 平均仓位 54.0%
    mask_below=12   11.89% / 22.54% / 2342 笔 / 平均仓位 52.3%   <- 默认
    mask_below=15   10.96% / 21.82% / 2161 笔 / 平均仓位 48.3%
    正本            10.12% / 26.74% / 3167 笔 / 平均仓位 62.5%

**逐年独立（每年重置 10 万，11 年，判规则的唯一口径）**：

    mask10  均差 +2.29pp  中位 +2.02  胜 7/11  sd 4.96  t=+1.53  去掉最好的一年 +1.29pp  年内回撤均 -0.75pp 更深 4/11
    mask12  均差 +2.08pp  中位 +0.68  胜 7/11  sd 4.64  t=+1.49  去掉最好的一年 +1.23pp  年内回撤均 -0.98pp 更深 4/11
    mask15  均差 +0.45pp  中位 -0.29  胜 5/11  sd 6.15  t=+0.24  去掉最好的一年 -0.55pp  年内回撤均 -2.03pp 更深 4/11

★ **10 与 12 是本项目少见的"四项判据同向"**：均差 / 中位 / 胜率 /
  去掉最好的一年**全为正**，且回撤平均更浅。此前测过的 off / cap / fill /
  cut / zero **无一例外中位数为负或回撤更深**。
★ **机制自洽，在逐年明细里看得见**：低 in_trend 的日子集中在坏市况，
  摘掉它们就该在熊市年受益 —— 2018 (-22.59% -> -19.67%) 与
  2022 (-9.24% -> **-0.30%**) 两个独立的熊市年都改善。
🔴 **但 t 只有 1.53 / 1.49，没到 2.23** —— 按本项目纪律这仍是"不显著"。
  而且阈值"平台"只有 10 与 12 两个点支撑（15 就掉到 +0.45pp）。
🔴 **默认取 12 不是因为它年化最高**（10 更高 0.01pp、逐年均差高 0.21pp），
  而是**回撤明显更浅**（全程 22.54% vs 25.06%、逐年 -0.98 vs -0.75pp）——
  收益几乎相同时选风险小的那个。两档都可用 `--param mask_below=` 调。

★ **语义上这是个设计选择，不是唯一解**：本版是「清仓，但 `g.target` 保留
  keys 当选股记忆」。另一种同样讲得通的做法是「持仓不动、只是不再调仓」，
  那是完全不同的东西，**还没测**。
🔴 **等价性自证**：`mask_below=0` 时条件恒假，必须与正本**逐位相同**。

★ 推导与六个被否证的变体全在 `etf_p1_gate.py` 的 docstring 里
  （gate = scale/off/flat/floor/cap/fill/cut/zero/mask）——
  同「已否证的规则留成开关，不要留成注释」。
"""
import importlib.util
import os

from assay.api import *          # noqa: F401,F403

DATALAKE = 'etf_lake'            # 与正本同一个 lake（策略自己声明）

_BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     'etf_p1_rotation.py')
_spec = importlib.util.spec_from_file_location('_p1_base_mask', _BASE)
_p1 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_p1)

# 复用正本的全部实现（一个字都不复述它的参数）
for _n in dir(_p1):
    if not _n.startswith('__'):
        globals().setdefault(_n, getattr(_p1, _n))


def _reselect_mask(context, sd):
    """与正本逐行相同，只有最后那个广度闸门改成「阈值清仓」。"""
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
        if len(in_trend) < g.mask_below:
            # 🔴 保留 keys、权重置 0 —— 不是 `w = {}`（见文件头那两条副作用）
            w = {s2: 0.0 for s2 in w}
        else:
            w = {s2: x * scale for s2, x in w.items()}
    g.target = w


def initialize(context):
    g.mask_below = int(getattr(g, 'mask_below', 12))   # 在势少于它就清仓；0 = 关闭（等价正本）
    _p1.initialize(context)
    # ★ 替换模块属性 —— `my_trade` 里是全局查找 `_reselect(...)`，换得掉
    #   （与「run_daily 注册的是函数对象、事后换属性无效」是两回事）
    _p1._reselect = _reselect_mask
