#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P1-MASKBOTH：两个清仓信号取【或】—— 在势只数 与 最终目标仓位。

    python3 run.py strategies/ETF/etf_p1_maskboth.py --param mask_below=12 --param mask_pos=0.70

    len(in_trend) < mask_below   或   sum(w) < mask_pos   ->  清仓

**为什么要合起来测**：两者看的不是同一件事 ——
  在势只数  = 池子里有多少票还在趋势上（**数量**）
  目标仓位  = 这些票经过 RS 闸 / 簇上限 / W_CAP 之后**配得出多少仓位**
所以「在势够多、但都挤在一个簇里，实际只配得出 40% 仓位」这种情形
只有后者看得见；反过来「在势很少、但恰好分散在不同簇所以仓位不低」
只有前者看得见。**二选一是在丢信息。**
🔴 真正要看的是 **sd 会不会降** —— P1-MASKPOS 单用时逐年差的 sd 是 10.70
  （P1-MASK 只有 4.64），那是它最大的短板：赢得多也输得多。
  组合如果只是把均值再抬高、sd 不降，那它就不值得多一个参数。

**本文件是【超集】，所以有三条等价性自证**（任一不过，结论全部作废）：

    mask_below=0,  mask_pos=0     ==  正本 etf_p1_rotation
    mask_below=12, mask_pos=0     ==  etf_p1_mask       (mask_below=12)
    mask_below=0,  mask_pos=0.70  ==  etf_p1_maskpos    (mask_pos=0.70)

🔴 摘除一律「保留 keys、权重置 0」，不是 `w = {}` —— 后者触发
  `_is_reselect_day` 的 `or (not g.target)`，空仓期逐日重选（589 -> 1414 次），
  副作用吃掉 4.68pp（2026-09-20 连测六个变体都栽在这上面）。
🔴 仓位那道判在广度闸缩放【之后】且不只在缩放分支里 —— 簇上限可以在满广度
  时就把仓位压低，那正是仓位信号相对在势信号唯一的信息增量。
"""
import os
import importlib.util

from assay.api import *          # noqa: F401,F403

DATALAKE = 'etf_lake'

_BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     'etf_p1_rotation.py')
_spec = importlib.util.spec_from_file_location('_p1_base_maskboth', _BASE)
_p1 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_p1)

for _n in dir(_p1):
    if not _n.startswith('__'):
        globals().setdefault(_n, getattr(_p1, _n))


def _reselect_both(context, sd):
    """与正本逐行相同，末尾两道清仓信号取【或】。"""
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
    hit_n = False
    if scale < 1.0:
        g.n_breadth += 1
        hit_n = g.mask_below > 0 and len(in_trend) < g.mask_below
        w = {s2: x * scale for s2, x in w.items()}
    hit_p = g.mask_pos > 0 and bool(w) and sum(w.values()) < g.mask_pos
    if hit_n or hit_p:
        # 记一下各自/共同触发，用来判"两个信号是不是同一件事"
        g.n_fire += 1
        g.n_fire_n += int(hit_n and not hit_p)
        g.n_fire_p += int(hit_p and not hit_n)
        g.n_fire_both += int(hit_n and hit_p)
        w = {s2: 0.0 for s2 in w}
    g.target = w


def initialize(context):
    g.mask_below = int(getattr(g, 'mask_below', 12))     # 在势少于它就清仓；0 = 关
    g.mask_pos = float(getattr(g, 'mask_pos', 0.70))     # 目标仓位低于它就清仓；0 = 关
    g.n_fire = g.n_fire_n = g.n_fire_p = g.n_fire_both = 0
    _p1.initialize(context)
    _p1._reselect = _reselect_both
