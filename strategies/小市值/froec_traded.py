#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FROEC-TRADED：在 FROEC 基础上换掉「候选宇宙」的定义。

与 JQ 原版（strategies/小市值/froec.py 默认配置）只差两条，其余完全相同：

1) **候选宇宙 = 决策日【实际有成交】的股票**（`paused_in_pool=0`）
   聚宽原版用 `get_all_securities()`，含当日停牌股；停牌股会参与 PB 半区
   与 ROE 十分位的【切点计算】，还能先占掉 `[:10]` 的名额再被
   `filter_paused_stock` 删掉，导致目标池常常只有 8~9 只。
   本版改为只把当日有 K 线的票放进宇宙。
   ★ 这条规则【不含前视】：某只票当日有没有成交，在决策时点（前一交易日
     收盘后）是完全已知的，可实现、可交易。它最初是面板按 K 线驱动带来的
     意外行为，但作为一条显式规则它是成立的。

2) **准确排除科创板，含 689 开头的 CDR**（`kcb_688_only=0`）
   聚宽原版 `filter_kcb_stock` 写的是 `stock[0:3] != '688'`，
   漏掉了 689 开头的科创板存托凭证 —— 全样本里就是 689009.XSHG（九号公司）。
   本版按 `68*` 排除，把科创板排干净。
   ★ 这一只票值 1.67pp 年化（42.69% -> 41.02%），不是因为它本身赚赔多少，
     而是它让 base 从 3434 变 3433，`floor(0.5*n)` 与 `floor(0.1*n2)`
     两级边界同时位移，换掉了边界上的票。

⚠️ **不要用回测收益来论证本版更优。** 实测：从 4484 只宇宙里随机剔掉
   3~10 只（0.07%~0.22%）无关的票，13 组 salt 全部让年化上升，
   均值 +2.73pp、区间 +0.40~+4.93pp、13/13 为正（p=2^-13）。
   完整宇宙（37.41%）是所有配置里最低的一个。本版相对 JQ 原版的 +3.61pp
   落在同一区间内 —— 那是分位截断的边界位移，不是选股逻辑的胜利。
   保留本版的理由是「它是一条独立且合法的宇宙定义，值得单独跟踪」，
   不是「它更赚钱」。

⚠️ **优势全部来自最后 1.6 年**（同口径逐年比对，A=JQ 原版口径 / B=本版）：
    2016-01 ~ 2024-12（9 年）    A 31.76%  B 32.28%  差 **+0.51pp**  ← 打平
    2025-01 ~ 2026-08（1.6 年）  A 85.79%  B 115.75% 差 **+29.95pp**
    全段                          A 38.60%  B 42.21%  差 +3.61pp
   前 9 年逐年差累加只有 +0.38pp（-5.10/+6.44/+1.42/-9.56/+4.72/-4.64/
   -0.86/+6.28/+1.68），2025+2026 两段就贡献 +39.63pp。
   逐年差 t=1.25 不显著（均值 +3.64pp、标准差 9.66pp、n=11、B 胜 7/11）。
   **回撤两版几乎相同**：全段 47.38% vs 47.60%，逐年差多数在 ±0.2pp 内，
   2024 那次 46.7% 的大回撤两版分毫不差 —— 承担的是同一份风险。

对标基线（2016-01-01~2026-08-07，本金 ￥100,000，--jq-cost）：
    本版        年化 41.02%  回撤 47.60%  夏普 1.31  平仓 962  胜率 61.75%
    JQ 原版口径 年化 37.41%  回撤 47.38%  夏普 1.23  平仓 963  胜率 62.31%
    聚宽实测    年化 36.80%  回撤 47.24%  夏普 1.083 平仓 962  胜率 61.9%
"""
import importlib.util
import os

from assay.api import *          # noqa: F401,F403

_BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'froec.py')
_spec = importlib.util.spec_from_file_location('_froec_base', _BASE)
_base = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_base)

# 复用基线实现，避免两份代码分叉：froec.py 修 bug 时本版自动跟随。
SQL = _base.SQL
EXCL_IND = _base.EXCL_IND
prepare = _base.prepare
rebalance = _base.rebalance
check_limit_up = _base.check_limit_up


def initialize(context):
    # getattr 保底：--param 显式指定时仍然生效，不被本版默认值覆盖。
    g.paused_in_pool = getattr(g, 'paused_in_pool', 0)
    g.kcb_688_only = getattr(g, 'kcb_688_only', 0)
    _base.initialize(context)
