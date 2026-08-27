#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""【演示】月频调到目标等权 —— 用来验证加减仓路径。

与 v0b/froec 的区别：那两个是「买入时等权、之后不再平衡」（聚宽原版就这样），
这个每月把每只票都调回 1/N，所以会同时产生【加仓】和【减仓】。
红利策略那类「按股息率加权、季度调权」的写法就依赖这条路径。

策略里照样不写任何涨跌停 / T+1 / 整手 / 税费 —— broker 负责。
减仓时 FIFO 消耗批次，红利税按各批实际持有期定档。
"""
from assay.api import *

UNIVERSE = ("is_st = 0 AND listed_days > 375 AND list_date IS NOT NULL "
            "AND symbol NOT LIKE 'sh68%' AND eps_q > 0 AND floatmv > 0")


def initialize(context):
    g.stock_num = 10
    set_benchmark('000905.XSHG')
    run_monthly(rebalance, time='09:30')


def rebalance(context):
    d = context.previous_date
    if d is None:
        return
    target = context.data.codes_at(d, where=UNIVERSE, order='floatmv ASC',
                                   limit=g.stock_num)
    target = context.tradable(target, 'buy')
    if not target:
        return
    # ★ 必须【先减后加】：同一循环里边加边减，靠前的票会先把现金花光，
    #   靠后的票还没减仓回收 —— 实测这样写会产生大量「现金不足」拒单。
    #   目标市值按调仓前的总权益算一次，避免边交易边漂移。
    # ★ 留 0.5% 现金缓冲：按 100% 权益分配目标市值，手续费与滑点又要从权益里出，
    #   最后一只必然差钱 —— 实测会产生 69 笔「现金不足」拒单。
    #   这是「目标权重类策略」的固有约束，不是引擎问题。
    per = context.portfolio.total_value * 0.995 / len(target)

    # 第一遍：清仓 + 减仓，回收现金
    for code in list(context.portfolio.positions):
        if code not in target:
            order_target_value(code, 0)
        elif context.portfolio.positions[code].value > per:
            order_target_value(code, per)

    # 第二遍：建仓 + 加仓
    for code in target:
        order_target_value(code, per)
