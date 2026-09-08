#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FROEC-ROE-YOY：把「ROE 加速度」换成【单季同比】，其余与 froec_traded 完全相同。

为什么要换（2026-09-08 实测）
--------------------------------
原版的 ROE 加速度是

    increase = 4×roe₁ − (roe₂ + roe₃ + roe₄ + roe₅)
             = 4 × [ roe₁ − mean(roe₂..roe₅) ]

roe 是**单季**、分母是平均净资产，所以它的含义是「最新一季的盈利能力比前四季
平均高出多少」。看着合理，但 5 期窗口**必然包含 1~2 个 Q4**，而 A 股的 Q4 单季
有一条很长的左尾（年报计提减值、奖金、坏账都在这一季）：

    单季 ROE 全样本（2015 起）  中位数     均值      为负占比
      Q1                       +0.0124   +0.0114    21.0%
      Q2                       +0.0173   +0.0213    17.8%
      Q3                       +0.0154   +0.0121    18.6%
      Q4                       +0.0136   **−0.0242**  **28.5%**

中位数没差多少，**均值是负的** —— 而这个指标用的是和/均值，正好吃这条尾。

按加速度分组（决策日 2026-09-07，全市场凑满 5 期的 5430 只）：

    分组              有 Q4 单季亏>5%   Q4 最差中位   最新季 ROE 中位
    前 10%（入选）      **63.9%**        −0.1453       +0.0356
    10~30%              24.3%           −0.0036       +0.0233
    30~60%               4.8%           +0.0069       +0.0146
    后 40%              10.5%           +0.0108       +0.0034

**入选组 64% 的票"某个 Q4 单季亏损超 5%"，而 30~60% 那组只有 4.8% —— 13 倍。**
也就是说这个指标主要在选「去年 Q4 大额亏损、今年恢复正常」的公司，
而不是「盈利能力持续改善」的公司。（它不是纯噪声：入选组的最新季 ROE 中位
也确实最高。但主要驱动力是基准里的 Q4 坏数据。）

本版怎么改
----------
    increase = roe₁ − roe₅          （最新季 vs **去年同一季**）

同比天然消除季节性：Q2 比 Q2、Q4 比 Q4，Q4 的减值出现在**两边**，相互抵消。
只需要 5 期（与原版一样），`HAVING count(*) = 5` 也照旧。

🔴 **只改 roec 这一层。** `univ` / `today` / `miss` / `eps1` / `base` /
  `pb_half` 与末层的 WHERE / ORDER BY / LIMIT 一个字没动 ——
  所以两版的差异**只来自 ROE 那一层**，是干净的单变量隔离。
🔴 **froec.py 与 froec_traded.py 一行都没动。** 它们的归档要保持可比，
  而实盘账户绑的是 froec_traded 的快照。本文件通过 importlib 加载
  froec.py 的**私有实例**，只在这个实例上换掉 `SQL`
  （`module_from_spec` 每次都是新对象，同 froec_traded.py 的做法）。
★ 也没有再加开关：`lu_*` / `fill_*` 那一批已经四五个了，
  开关叠开关到后面没人说得清哪个组合跑过。要比就复制一份策略。

🔴🔴 **已否证（2026-09-08，逐年独立回测 2016~2026，每年重置 50 万）**
--------------------------------------------------------------------
    版本                     全程年化   夏普    逐年均差    t       胜
    原版 4×roe₁−Σ            41.43%    1.387     —        —       —
    同比 roe₁−roe₅           24.12%    0.933   −23.10pp  −2.80   0/11
    TTM Σ₁₋₄−Σ₅₋₈           21.13%    0.829   −28.40pp  −4.75   0/11

去掉 2026（只 8 个月）仍然显著：−15.33pp (t=−5.08) / −23.52pp (t=−6.15)。
**0/11 年胜** —— 没有一年跑赢原版。这是本项目第一个 |t| > 2.23 的结论
（此前测过的所有开关都不显著）。

★ **事实没错，因果judgment错了。** "入选组 63.9%% 的票有 Q4 单季亏损 >5%%，
  而 30~60%% 那组只有 4.8%%" 是可验证的；但那**不是缺陷，正是 alpha 的来源**。
  一个说得通的解释：大额减值常常是"一次性洗澡"，洗完之后基本面改善而市场
  还没反应过来 —— 小市值 + 低 PB + 刚洗过澡，是典型的深度价值反转组合。
  而"同比改善"选的是平稳增长的公司，在小市值池里那不是 alpha。

★ 所以这个文件**保留不删**（同「已否证的规则留成开关，不要留成注释」）：
  下次有人再想起"用同比消除季节性"这个主意，跑一行就能看到结论。
🔴 **不要拿"指标看起来更合理"当采用理由。** 这次的教训很贵：
  从口径分析（单季 vs 累计、Q4 左尾、63.9%% vs 4.8%%）到"应该用同比"
  每一步推理都成立，而回测给出的是 0/11。
"""
import importlib.util
import os

from assay.api import *          # noqa: F401,F403

_BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'froec.py')
_spec = importlib.util.spec_from_file_location('_froec_yoy_base', _BASE)
_base = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_base)

# ---- 只替换 roec 那一层：4×roe₁ − Σ(roe₂..roe₅)  ->  roe₁ − roe₅ ----
_OLD = """), roec AS (
  SELECT code, 4*max(CASE WHEN rn=1 THEN roe END)
         - max(CASE WHEN rn=2 THEN roe END) - max(CASE WHEN rn=3 THEN roe END)
         - max(CASE WHEN rn=4 THEN roe END) - max(CASE WHEN rn=5 THEN roe END) AS increase
  FROM ind WHERE rn <= 5 GROUP BY 1 HAVING count(*) = 5
), base AS ("""
_NEW = """), roec AS (
  -- 【本版唯一的改动】单季**同比**：最新季 vs 去年同一季。
  -- 原版是 4×roe1 − Σ(roe2..roe5)，等价于"最新季 vs 前四季均值"——
  -- 而前四季必然含 1~2 个 Q4，Q4 的减值左尾把均值拉低，于是
  -- 指标变成了"去年 Q4 亏得多"的代理（见文件头的 63.9% vs 4.8%）。
  -- 同比让 Q4 出现在**两边**、相互抵消。
  SELECT code, max(CASE WHEN rn=1 THEN roe END)
              - max(CASE WHEN rn=5 THEN roe END) AS increase
  FROM ind WHERE rn <= 5 GROUP BY 1 HAVING count(*) = 5
), base AS ("""
assert _OLD in _base.SQL, 'froec.py 的 roec 那一层变了 —— 本版的替换锚点要跟着更新'
# 首行注释是"选股理由"页拿来当组名的，跟着改掉，否则页面上写着原版的名字
SQL = (_base.SQL.replace(_OLD, _NEW, 1)
       .replace('-- FROEC 候选池：在册宇宙',
                '-- FROEC-YOY 候选池（ROE 同比）：在册宇宙', 1))
_base.SQL = SQL                  # 🔴 只改**本实例**，不污染 froec.py

EXCL_IND = _base.EXCL_IND
prepare = _base.prepare
rebalance = _base.rebalance
blacklist = _base.blacklist
check_limit_up = _base.check_limit_up
stop_check = _base.stop_check
stop_filter = _base.stop_filter
pf_check = _base.pf_check
_bench_up = _base._bench_up
_do_buy = _base._do_buy
rebalance_buy = _base.rebalance_buy


def initialize(context):
    # 与 froec_traded 对齐：候选宇宙只含决策日实际有成交的票、按 68* 排科创板
    g.paused_in_pool = getattr(g, 'paused_in_pool', 0)
    g.kcb_688_only = getattr(g, 'kcb_688_only', 0)
    g.weekday = getattr(g, 'weekday', 2)
    _base.initialize(context)
