#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""纯小市值（SG-MS-PEG-HL 的因子层消融基准线）。

对标聚宽实测：2019-01-01~2026-06-30 年化 38.98%、最大回撤 51.86%。

★ 注意策略里【没有】任何涨跌停 / T+1 / 停牌 / 整手 / 税费的代码 ——
  全部由 broker 负责。旧引擎把涨停过滤写在选股里，那是错的位置：
  它既重复了撮合职责，又因为只做了买入侧、漏了卖出侧而静默失真。
"""
from assay.api import *          # noqa: F401,F403

# 预过滤：非 ST、上市超 375 天、非科创板、单季 EPS 为正、流通市值有效
# floatmv > 0 是必需的护栏：300114.XSHE 有真实价格与成交量却 floatmv=0，
# 按市值【升序】选股时 0 永远排第一，实测它从 2016 年起 2056 个交易日霸占首位。
UNIVERSE = ("NOT is_risk_warned AND listed_days > 375 AND list_date IS NOT NULL "
            "AND symbol NOT LIKE 'sh68%' AND eps_q > 0 AND floatmv > 0")


def initialize(context):
    g.stock_num = 10
    g.candidate_num = 15
    g.limit_days = 20
    g.hold_history = []          # 最近 N 日持仓并集，配合「涨停过」构成黑名单
    g.high_limit = set()         # 昨收涨停的持仓：调仓不卖，14:00 再看是否打开

    set_benchmark('000905.XSHG')      # 与聚宽原版一致：中证 500

    run_daily(prepare, time='09:05')
    run_weekly(rebalance, weekday=1, time='09:30')
    run_daily(check_limit_up, time='14:00')


def prepare(context):
    """盘前：滚动持仓历史 + 找出昨收涨停的持仓。"""
    held = list(context.portfolio.positions)
    g.hold_history.append(set(held))
    if len(g.hold_history) > g.limit_days:
        g.hold_history = g.hold_history[-g.limit_days:]

    g.high_limit = set()
    if held and context.previous_date:
        bars = context.data.bars(context.previous_date, held)
        g.high_limit = {c for c, b in bars.items() if b.limit_up}


def rebalance(context):
    d = context.previous_date
    if d is None:
        return
    # 选股：预过滤后按流通市值升序取候选
    cand = context.data.codes_at(d, where=UNIVERSE, order='floatmv ASC',
                                 limit=g.candidate_num)
    if not cand:
        return

    # 顺序必须与聚宽一致：候选 -> 可交易过滤 -> 黑名单 -> **最后**截断。
    # 旧引擎在 select 内部就 [:10]，再剔黑名单 -> 名额被丢掉、下一名不补位；
    # 聚宽是先过滤再截断 -> 涨停/黑名单的候选会被下一名替换。
    # 实测这一处顺序差异让两个引擎从 2019-04-15 起持仓分歧。
    cand = context.tradable(cand, 'buy')

    # 20 日黑名单：最近 20 日持有过 且 最近 20 日涨停过 -> 不再买入
    if g.hold_history:
        recent = context.data.had_limit_up(
            cand, context.data.nth_prev_day(context.current_date, g.limit_days), d)
        seen = set().union(*g.hold_history)
        cand = [c for c in cand if c not in (seen & recent)]
    target = cand[:g.stock_num]

    # 卖出：不在目标、且不是昨收涨停的持仓
    # （卖不掉的情况 —— 停牌、跌停、T+1 —— 由 broker 拒单，策略不必判断）
    for code in list(context.portfolio.positions):
        if code not in target and code not in g.high_limit:
            order_target_value(code, 0)

    # 等权买入缺的部分
    # ★ 买入名额的分母是【目标池实际长度】，不是 g.stock_num。
    #   聚宽原版：value = cash / (target_num - position_count)，
    #   其中 target_num = len(g.target_list) —— 20 日黑名单剔除后可能 < 10。
    #   写成 g.stock_num 会把钱多分一份，实测 2019-04-15 起持仓即分歧。
    need = [c for c in target if c not in context.portfolio.positions]
    need = need[:max(0, len(target) - len(context.portfolio.positions))]
    if need:
        per = context.portfolio.cash / len(need)
        for code in need:
            order_target_value(code, per)


def check_limit_up(context):
    """昨收涨停的持仓，今日尾盘若已打开则卖出；仍封住则继续持有。"""
    if not g.high_limit:
        return
    codes = [c for c in g.high_limit if c in context.portfolio.positions]
    if not codes:
        return
    # 今天的状态走 context.current() —— 历史接口取不到今天（PIT 防火墙）。
    # 14:00 属 INTRADAY 阶段，limit_up 可见（与「盘中成交价用收盘价代理」的约定一致）。
    cur = context.current(codes)
    for code in codes:
        d = cur.get(code)
        if d is not None and not d.get('limit_up'):
            order_target_value(code, 0)
