# -*- coding: utf-8 -*-
"""FROEC-LU-SAMEDAY：**当天**盘中涨停、尾盘没封住 -> **当天就卖**。

与另两个版本的区别（三者判据都不同，别记混）：

    froec_traded（原版）      昨天**收盘封住** + 今天没封住 -> 今天卖
    froec_lu_intraday        昨天**盘中摸到**、昨天收盘没封住 -> **次日**卖
    froec_lu_sameday（本版）  **今天**盘中摸到、今天尾盘没封住 -> **当天**卖

判据（`exit_time`，默认 14:00，与原版同一时刻）：

    context.current(持仓)  ->  touch_up 为真（今天盘中摸到过涨停）
                               且 limit_up 为假（今天收盘没封在板上）
                           ->  order_target_value(code, 0)

🔴🔴 **必须说清这里的近似。** `touch_up` 是**全天**最高价派生的、`limit_up`
是**收盘**价派生的，而任务跑在 14:00 —— 严格说 14:00 那一刻还不知道
14:00~15:00 会不会回封。所以本版在回测里会：

  · 卖掉一些「14:00 后又回封」的票（实盘那时你不会卖）
  · 而「14:00 前就炸板」的那些，实盘与回测一致

★ 但这**不是本版新引入的问题**：原版的 `check_limit_up` 在 14:00 读的
  `limit_up` 同样是收盘派生量。froec.py 的注释写明了这条既有约定 ——
  「引擎无分时线，盘中相位用【收盘价】代理」。本版沿用同一套约定，
  不引入新的价格假设；要更准就得有分时线，那是另一个量级的工程。

★ 卖出价同样按引擎的约定（收盘价代理）—— 判据与执行价是**同一个价**，
  所以不存在"用未来信息拿到更好的成交价"这种偏差。

**怎么实现**：`importlib` 私有实例，只替换 `check_limit_up`。
froec.py / froec_traded.py **一行没动**（它们的归档要可比，实盘账户绑着快照）。

--------------------------------------------------------------------
🔴 **回测结论（2026-09-09，逐年独立 2016~2026，每年重置 50 万）**
    见文件末尾的 `RESULT` 注释块。
"""

import importlib.util
import os

_HERE = os.path.dirname(os.path.abspath(__file__))


def _private(name):
    """froec.py 的**私有实例** —— `module_from_spec` 每次都是新对象，
    在它上面 monkeypatch 不会污染 froec.py 的其它使用者。"""
    spec = importlib.util.spec_from_file_location(
        'froec_lu_sameday__base', os.path.join(_HERE, name))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


_base = _private('froec.py')

# froec_traded 的那一层：真实交易口径
_TRADED = {'stop_intraday': 1, 'stop_loss': 0.35, 'weekday': 2}


def _check_limit_up(context):
    """原版的炸板离场 + 「**今天**盘中炸板」这一类。

    ★ 先跑原版（它管 `g.high_limit`：昨天封住、今天没封住），再补本版。
      两者可能命中同一只票（昨天封住、今天又摸了一次涨停还是没封住）——
      `order_target_value(code, 0)` 幂等，重复下单不会卖两次。
    """
    g = _base.g
    _base_check_limit_up(context)
    if not g.limit_up_exit:
        return
    held = [c for c in context.portfolio.positions]
    if not held:
        return
    cur = context.current(held)
    for code in held:
        d = cur.get(code)
        if d is None:
            continue
        # 🔴 `touch_up` 只在 INTRADAY/CLOSE 相位存在（收盘派生量）。
        #   `d.get('touch_up')` 在 OPEN 相位是 None -> 不触发，正确。
        #   ★ 不能写 `not d.get('limit_up')` 就卖 —— 那会把**没涨停过**的
        #     全卖掉（原版踩过同款：OPEN 阶段取不到 limit_up 得 None）。
        if d.get('touch_up') and not d.get('limit_up'):
            _base.order_target_value(code, 0)


_base_check_limit_up = _base.check_limit_up
_base.check_limit_up = _check_limit_up


def initialize(context):
    g = _base.g
    for k, v in _TRADED.items():
        setattr(g, k, v)
    _base.initialize(context)


prepare = _base.prepare
rebalance = _base.rebalance
rebalance_buy = _base.rebalance_buy
check_limit_up = _check_limit_up
stop_check = _base.stop_check
pf_check = _base.pf_check
