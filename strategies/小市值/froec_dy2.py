# -*- coding: utf-8 -*-
"""FROEC-DY2：**连续两年**股息率 > 2%（用户 2026-09-22 的「策略二」）。

它就是 `froec_dy.py` 的 `dy_years=2` —— 私有加载 + 只改一个默认值，
**一行 SQL 都没有抄**。两个文件各写一份分红 CTE 的话，去重规则 / 可见性
字段 / 年度锚定迟早分叉，而分叉的那份看着完全正常（同
`froec_traded` 加载 `froec`、`froec_dy2` 加载 `froec_dy` 这条链）。

    策略一 froec_dy   最近一个完整会计年度       股息率 > 2%
    策略二 本文件     最近一个 **和它的上一个**   股息率都 > 2%

口径（分母是同一个今天的总市值、会计年度不许过期、去重与可见性规则）
全部见 `froec_dy.py` 的 docstring —— **只有一份，不在这里复述**。

🔴🔴 **结论：不采纳**（2026-09-22）。全程 26.52% vs 基线 45.00%；
逐年独立 11 年 均差 -23.20pp 中位 -16.75 **胜 0/11** t=-3.88。
与策略一的差（多加一年约束）均差 +4.25pp t=+0.90 —— **两者分不开**。
完整的对照组拆解（纯小市值 / 有分红即可）与机制量化同样在
`froec_dy.py` 的 docstring 里，**不在这里复述**。
"""

import importlib.util
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location(
    'froec_dy2__dy', os.path.join(_HERE, 'froec_dy.py'))
_dy = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_dy)
_base = _dy._base


def initialize(context):
    # 🔴 必须写成 `g.x = getattr(g, 'x', 字面量)` 这个**标准形状**：
    #   看板的参数解析（`srv/base._PARAM_RE`）只认它，写成 `_base.g.x = ...`
    #   的话这一项在回测页的「参数」页签上**看不见** —— 而那正是"这次跑在
    #   什么配置上"的唯一出处（同「默认值写字面量不写模块级常量」那条）。
    # ★ getattr 保底：--param 显式指定时仍然生效，不被本版默认值覆盖
    #   （同 froec_traded 的 paused_in_pool / weekday）。
    g = _base.g
    g.dy_years = getattr(g, 'dy_years', 2)
    _dy.initialize(context)


prepare = _base.prepare
rebalance = _base.rebalance
rebalance_buy = _base.rebalance_buy
check_limit_up = _base.check_limit_up
stop_check = _base.stop_check
pf_check = _base.pf_check
