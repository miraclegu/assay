#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""【红利低波·袖A】= 红利指数增强 的 A 腿单独跑，用作**基准**。

    高股息池（股息率前 10% 且 >3%）-> 按 beta_252 升序 -> 取前 10

★ 存在的理由是**可比性**，不是"再做一条策略"：
  要衡量"改了某个阈值有什么效果"，得先有一条干净的单腿曲线。
  组合曲线里两袖的效应混在一起 —— 改袖 B 的阈值却看组合年化，
  测到的一大半是袖 A 的噪声（袖 A 出 10 只、袖 B 出 5 只）。

🔴 **这不是一份新实现，是 `红利指数增强.py` + `sleeve='a'`。**
  同 `strategies/小市值/froec_traded.py` 的写法：加载基线模块、只覆盖差异。
  复制成独立实现的话，以后修基线的 bug 这里不会跟随，
  而"基准和生产跑的不是同一套代码"是最坏的一种基准。

★ 与 `strategies/红利/红利低波.py` **不是同一个东西**，别混用：
  那个是 JQ 独立策略「红利低波」的复刻（`target_num=8`、
  `div_method` 默认 rolling365、SQL 里没有 beta 分位截断那层）。
  本文件是**组合策略里那条腿**（`num_a=10`，与生产同源同参）。

【基准值】2016-01-01~2025-12-31 · 100 万 · 默认成本 · div_method=fiscal_year
  见 `参数合理性审计-2026-09.md` §七，run_id 记在那里。
"""
import importlib.util
import os

from assay.api import *          # noqa: F401,F403

_BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '红利指数增强.py')
_spec = importlib.util.spec_from_file_location('_hongli_base', _BASE)
_base = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_base)

# 复用基线实现，避免两份代码分叉。
NOTE = '袖A 单腿基准：高股息前10%且>3% -> beta_252 升序取 10（= 红利指数增强 sleeve=a）'
SQL_A = _base.SQL_A
SQL_B = _base.SQL_B
DIV_ROLLING365 = _base.DIV_ROLLING365
DIV_FISCAL_YEAR = _base.DIV_FISCAL_YEAR
UNIVERSE = _base.UNIVERSE
prepare = _base.prepare
pick = _base.pick
trade = _base.trade
check_limit_up = _base.check_limit_up
stop_check = _base.stop_check
stop_filter = _base.stop_filter
pf_check = _base.pf_check


def initialize(context):
    # getattr 保底：--param 显式指定时仍然生效，不被本版默认值覆盖。
    g.sleeve = getattr(g, 'sleeve', 'a')
    _base.initialize(context)
