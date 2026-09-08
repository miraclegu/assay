#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FROEC-ROE-TTM：把「ROE 加速度」换成【滚动四季同比】，其余与 froec_traded 相同。

与 froec_roe_yoy.py 是同一个问题的两种解法（问题本身见那个文件的头部：
原版 5 期窗口必然含 1~2 个 Q4，而 Q4 单季 ROE 均值 −0.0242、为负 28.5%，
于是"最新季 vs 前四季均值"变成了"去年 Q4 亏得多"的代理 ——
入选组 63.9% 的票有 Q4 单季亏损 >5%，而 30~60% 那组只有 4.8%）。

两种解法的取舍
--------------
    yoy  increase = roe₁ − roe₅                      需 5 期
    ttm  increase = Σ(roe₁..roe₄) − Σ(roe₅..roe₈)    需 8 期

· `yoy` 只看一个季度对一个季度 —— 干净，但**单季噪声大**：一次性损益、
  季度间的收入确认节奏都会让它跳。
· `ttm` 比的是**两个完整年度**（滚动四季 vs 上一个滚动四季）：季节性天然
  抵消（每一边都恰好含一个 Q1..Q4），噪声也被平滑掉。
  代价是**要 8 期**（两年）—— 上市不足两年的票进不来，候选池会小一些。
  🔴 这个代价要算进对比里：候选池变小本身就会改变 `floor(0.5*n)` 与
    `floor(0.1*n2)` 两级切点，所以 ttm 与原版的差异**不只是 ROE 那一层**。
    （froec_traded 的 docstring 里记过同类：一只票让 base 从 3434 变 3433，
     两级边界同时位移，就值 1.67pp 年化。）

🔴 只改 roec 这一层；froec.py 与 froec_traded.py 一行没动（见 yoy 那个文件）。

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
_spec = importlib.util.spec_from_file_location('_froec_ttm_base', _BASE)
_base = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_base)

_OLD = """), roec AS (
  SELECT code, 4*max(CASE WHEN rn=1 THEN roe END)
         - max(CASE WHEN rn=2 THEN roe END) - max(CASE WHEN rn=3 THEN roe END)
         - max(CASE WHEN rn=4 THEN roe END) - max(CASE WHEN rn=5 THEN roe END) AS increase
  FROM ind WHERE rn <= 5 GROUP BY 1 HAVING count(*) = 5
), base AS ("""
_NEW = """), roec AS (
  -- 【本版唯一的改动】滚动四季**同比**：最近四个季度 vs 上一个四个季度。
  -- 每一边都恰好含一个 Q1..Q4，所以 Q4 的减值左尾在两边同时出现、
  -- 相互抵消（原版是"最新季 vs 前四季均值"，Q4 只出现在基准那一边）。
  -- 🔴 代价：要 8 期。上市不足两年的票进不来，候选池会小一些，
  --   而 base 的行数变了会让 floor(0.5*n) / floor(0.1*n2) 两级切点位移 ——
  --   所以本版与原版的差异不只是 ROE 那一层（见文件头）。
  SELECT code,
        (max(CASE WHEN rn=1 THEN roe END) + max(CASE WHEN rn=2 THEN roe END)
       + max(CASE WHEN rn=3 THEN roe END) + max(CASE WHEN rn=4 THEN roe END))
      - (max(CASE WHEN rn=5 THEN roe END) + max(CASE WHEN rn=6 THEN roe END)
       + max(CASE WHEN rn=7 THEN roe END) + max(CASE WHEN rn=8 THEN roe END))
        AS increase
  FROM ind WHERE rn <= 8 GROUP BY 1 HAVING count(*) = 8
), base AS ("""
assert _OLD in _base.SQL, 'froec.py 的 roec 那一层变了 —— 本版的替换锚点要跟着更新'
SQL = (_base.SQL.replace(_OLD, _NEW, 1)
       .replace('-- FROEC 候选池：在册宇宙',
                '-- FROEC-TTM 候选池（ROE 滚动四季同比）：在册宇宙', 1))
_base.SQL = SQL                  # 🔴 只改**本实例**

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
    g.paused_in_pool = getattr(g, 'paused_in_pool', 0)
    g.kcb_688_only = getattr(g, 'kcb_688_only', 0)
    g.weekday = getattr(g, 'weekday', 2)
    _base.initialize(context)
