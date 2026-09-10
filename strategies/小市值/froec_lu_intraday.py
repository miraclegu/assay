# -*- coding: utf-8 -*-
"""FROEC-LU-INTRADAY：炸板离场放宽到【盘中板】—— 昨天摸到涨停但收盘没封住，次日也卖。

原版（froec_traded）的炸板离场只管**一类**票：

    昨天**收盘封在板上**（面板 `is_limit_up`：close == 涨停价）
    → 今天 14:00 没封住 → 清仓

也就是说「昨天盘中摸到涨停、尾盘炸了」的票**完全不管** —— 它昨天
`is_limit_up = false`，进不了 `g.high_limit`。实测 2026-09 以来
「盘中摸到、收盘没封住」有 183 个交易日·票，它们的 `is_limit_up` 全是 false。

本版加的是那一类：

    昨天**盘中摸到过涨停**（`Bar.touch_up`：high >= 涨停价）
    且昨天**收盘没封住** → 次日 `exit_time` 清仓

★ 为什么是"次日"而不是"当天尾盘"：引擎是**日频**的，"尾盘没封住"这件事
  要到收盘才知道。当天就卖等于用当天收盘信息做当天的决策 —— 那是未来函数。
  所以时点只能是次日（与原版同一个 `exit_time`）。
🔴 两条规则是**互补**的，不是替换：原版管"封住的第二天开板"，本版多管
  "昨天就炸了的"。所以本版 = 原版 + 多卖一类，卖出只会变多不会变少。

**怎么实现**：`importlib` 加载 froec.py 的**私有实例**，只替换 `prepare`
（多收集一个集合）与 `check_limit_up`（多判一类）。`froec.py` 与
`froec_traded.py` 一行没动 —— 它们的归档要可比，而且实盘账户绑着快照。
（同 froec_api.py / froec_roe_yoy.py 的做法。）

--------------------------------------------------------------------
🔴🔴 **回测结论（2026-09-10 重跑，参数口径已修正）：不采纳。**

★ **第一批数字作废**（同 froec_lu_sameday）：`_TRADED` 曾是凭印象拼的，
  与 froec_traded 的真实参数不同。现在加载 froec_traded 本身。

逐年独立（每年重置 50 万；2026 到 09-08），vs 现状 froec_traded：

    均 +0.43pp   中位 +0.60   更好 6/11   sd 6.12   t=+0.23   不显著
    链乘年化 42.42%（现状 41.84%）  平均回撤 19.3%（19.6%）  夏普 1.50（1.47）

★ 三项都朝好的方向但幅度极小，t=+0.23 —— 证不出任何东西。
★ 而「当天卖」那个变体（froec_lu_sameday）测得更透：它暴露了这一类规则的
  共同缺陷 —— **卖出判据与黑名单判据互斥，卖了会立刻买回**；而补齐抑制器
  之后效果反而更差。本版有同一个问题（`touch_up` 卖出、`is_limit_up` 抑制），
  只是幅度更小所以不明显。
★ 结论：**维持现状。** 详见 froec_lu_sameday.py 的结论块。
"""

import importlib.util
import os

_HERE = os.path.dirname(os.path.abspath(__file__))


def _private(name):
    """把 froec.py 加载成一个**私有实例** —— `module_from_spec` 每次都是新对象，
    所以在它上面 monkeypatch 不会污染 froec.py 的其它使用者
    （froec_traded.py、实盘快照、其它变体策略）。"""
    spec = importlib.util.spec_from_file_location(
        'froec_lu_intraday__base', os.path.join(_HERE, name))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


_traded = _private('froec_traded.py')
_base = _traded._base          # froec_traded 自己加载的 froec 私有实例



def _prepare(context):
    """原版 prepare + 多收集一个集合 `g.lu_touched`。

    🔴 **不能只在 check_limit_up 里现算**：那时 `context.previous_date` 的
      bar 要再查一次，而 `prepare`（09:05）本来就查了 —— 两处查同一份数据
      迟早口径分叉（同「一处定义」那条）。
    """
    g = _base.g
    _base_prepare(context)              # 原版那一套（含 g.high_limit）
    g.lu_touched = set()
    held = list(context.portfolio.positions)
    if held and context.previous_date:
        bars = context.data.bars(context.previous_date, held)
        # 盘中摸到过涨停，**但收盘没封住** —— 收盘封住的那些归原版规则管
        g.lu_touched = {c for c, b in bars.items()
                        if getattr(b, 'touch_up', False) and not b.limit_up}


def _check_limit_up(context):
    """原版的炸板离场 + 「昨天盘中炸板」这一类。

    ★ 先跑原版：它管 `g.high_limit`（昨天封住、今天没封住）。
    ★ 再补本版：`g.lu_touched`（昨天盘中摸到、收盘没封住）—— 这一类
      **不需要看今天**，昨天尾盘没封住这件事已经确定了。
    """
    g = _base.g
    _base_check_limit_up(context)
    if not g.limit_up_exit:
        return
    for code in list(getattr(g, 'lu_touched', ())):
        if code in context.portfolio.positions:
            _base.order_target_value(code, 0)


_base_prepare = _base.prepare
_base_check_limit_up = _base.check_limit_up
_base.prepare = _prepare
_base.check_limit_up = _check_limit_up


def initialize(context):
    _traded.initialize(context)   # 🔴 参数口径由 froec_traded 定，不自己拼


# 其余全部转发给私有实例（引擎按名字取这些）
prepare = _prepare
rebalance = _base.rebalance
rebalance_buy = _base.rebalance_buy
check_limit_up = _check_limit_up
stop_check = _base.stop_check
pf_check = _base.pf_check
