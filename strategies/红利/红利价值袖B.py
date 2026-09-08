#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""【红利价值·袖B】= 红利指数增强 的 B 腿单独跑，用作**基准**。

    全池 -> PE 5~50 -> 扣非ROE/营收同比/净利同比 三条 between
         -> 该池内股息率前 10% 且 >3% -> 取前 5

★ 存在的理由是**可比性**，不是"再做一条策略"：袖 B 的阈值有 8 个，
  而它在组合里只出 5 只票（袖 A 出 10 只）。改袖 B 的阈值却看组合年化，
  测到的一大半是袖 A 的噪声。要判断阈值改动的效果，必须看这条单腿曲线。

🔴 **这不是一份新实现，是 `红利指数增强.py` + `sleeve='b'`。**
  同 `strategies/小市值/froec_traded.py` 的写法：加载基线模块、只覆盖差异。

⚠️ **这条腿不适合单独当策略用**（已知，不是本文件的缺陷）：
  四条基本面过滤后池子只剩几十只，再取股息率前 10% 常常只有 3~6 只，
  `num_b=5` 于是近乎"有多少要多少"（实盘 2026-09-01：池子 6 只取 5 只）。
  单腿跑会出现高集中度与阶段性欠配 —— **看基准时要连平均仓位一起看**。

★ 与 `strategies/红利/红利价值.py` **不是同一个东西**，别混用：
  那个是 JQ 独立策略「红利价值」的复刻（`target_num=8`、多了 `yoy_mode`
  与 `exit_time` 参数）。本文件是**组合策略里那条腿**（`num_b=5`，与生产同源同参）。

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

NOTE = '袖B 单腿基准：PE带+基本面三条 -> 池内股息率前10%且>3% 取 5（= 红利指数增强 sleeve=b）'
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
    g.sleeve = getattr(g, 'sleeve', 'b')
    _base.initialize(context)
