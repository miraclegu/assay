#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""【红利低波】股息率 top10% 且 >3% -> beta 升序取前 N

复刻 JQ/红利/红利低波.txt（= 组合版的 Sleeve A）。
JQ 实测（分析结论.md 表 G「红利低波单跑」）：年化 11.27% / 夏普 0.475 / 回撤 27.98%

## ★ beta 是本地算的，不是聚宽的因子值

聚宽 `jqfactor.get_factor_values(..., 'beta')` 本地没有，但 beta 不是专有因子 ——
`std/beta_daily.parquet` 用个股 `ret_1d`（后复权）对沪深300日收益做滚动回归，
同时落了 60/120/252 三个窗口（中位 0.926/0.931/0.939）。

**策略按 beta 排序取前 N，排序对定义差异敏感**，所以窗口做成参数
`g.beta_win`，可以 sweep 出哪个更接近聚宽 —— 而不是猜一个就用。

## 股息率的分位/阈值顺序

原版 `get_dividend_ratio_filter_list` 是【先按股息率降序截分位，再过 >3% 阈值】。
写反（先过阈值再算分位）会让目标池几乎空掉 —— 在红利价值上踩过一次。
"""
# 版本一句话简介 —— 看板在版本号右边展示它。
# ★ 改动策略行为时【必须同步改这句】：版本 = 代码哈希，
#   简介是人识别两个版本差异的唯一线索。
# ★ 本策略依赖 beta_252 —— **回测起点不要早于 2006-01-01**。
#   沪深300 首个发布值在 2005-01-04（该指数在那之前不存在），
#   252 日满窗口要到 2005-08 才有 98% 覆盖（2005-01~06 是 0%）。
#   起点更早会静默欠配：实测 2005 年平均仓位仅 74.3%，指标被现金稀释。
#   报告里的「平均仓位」一栏会暴露这个问题，引用结论前先看它。
NOTE = '股息率 top10% 且 >3% → beta 升序取前 8；beta 窗口 252 天（换 60/120 会掉 3~7pp）'

from assay.api import *

UNIVERSE = ("NOT is_risk_warned AND listed_days >= 250 AND list_date IS NOT NULL "
            "AND jq_code NOT LIKE '68%' AND jq_code NOT LIKE '4%' "
            "AND jq_code NOT LIKE '8%' AND totalmv > 0")

DIV_ROLLING365 = """
  SELECT code, sum(bonus_amount_rmb) * 1e4 AS amt
  FROM read_parquet('{root}/std/dividend.parquet')
  WHERE a_registration_date >= DATE '{t1}' - INTERVAL 365 DAY
    AND a_registration_date <= DATE '{t1}' AND bonus_amount_rmb > 0
  GROUP BY 1
"""

# v7 / A-1+A-6：按【分红归属会计年度】汇总，而不是按登记日滚动 365 天。
#   原实现两个缺陷：① 实施日在年度间漂移几周 -> 窗口里出现两次或零次年度分红；
#   ② 公司从年派改半年派时（2024 后大量银行/央企）窗口混入不同归属期。
#   去重：同一 (code, report_date, bonus_type) 只留流程最靠后的一条 —— JQ 的
#   「董事会预案」记录数是实际事件数的 8.3 倍，不去重会让股息率虚高，且 99.8%
#   集中在中期分红（2024 后的银行/央企），正是策略重仓处。
#   可见性用 board_plan_pub_date（董事会预案即公开信息，非未来函数）。
DIV_FISCAL_YEAR = """
  WITH raw AS (
    SELECT code, report_date, bonus_amount_rmb,
           row_number() OVER (
             PARTITION BY code, report_date, bonus_type
             ORDER BY CASE plan_progress
                        WHEN '实施方案'     THEN 3
                        WHEN '股东大会预案' THEN 2
                        WHEN '董事会预案'   THEN 1
                        ELSE 0 END DESC,
                      board_plan_pub_date DESC) AS r
    FROM read_parquet('{root}/std/dividend.parquet')
    WHERE board_plan_pub_date <= DATE '{t1}'
      AND board_plan_pub_date >= DATE '{t1}' - INTERVAL 800 DAY
      AND bonus_cancel_pub_date IS NULL
      AND (plan_progress IS NULL OR plan_progress NOT IN ('终止', '取消分红'))
      AND bonus_amount_rmb > 0
  ), dedup AS (
    SELECT * FROM raw WHERE r = 1
  ), fy AS (
    -- 年度锚定用 report_date 月份==12，不依赖 bonus_type 文本标签
    SELECT code, max(year(report_date)) AS y
    FROM dedup WHERE month(report_date) = 12 GROUP BY 1
  )
  SELECT d.code, sum(d.bonus_amount_rmb) * 1e4 AS amt
  FROM dedup d JOIN fy ON fy.code = d.code AND year(d.report_date) = fy.y
  GROUP BY 1
"""

SQL = """
WITH div AS (
{div}
), base AS (
  SELECT p.jq_code, d.amt / p.totalmv AS dy,
         row_number() OVER (ORDER BY d.amt / p.totalmv DESC) AS rk,
         count(*) OVER () AS n
  FROM {panel} p JOIN div d ON d.code = p.jq_code
  WHERE p.date = DATE '{t1}' AND {uni}
), hi AS (   -- 先截分位，再过阈值（顺序与原版一致）
  SELECT jq_code, dy FROM base
  WHERE rk <= greatest(1, floor({dtop} * n)) AND dy > {dmin}
)
SELECT h.jq_code, h.dy, b.beta_{bw} AS beta
FROM hi h JOIN read_parquet('{root}/std/beta_daily.parquet') b
  ON b.code = h.jq_code AND b.date = DATE '{t1}'
WHERE b.beta_{bw} IS NOT NULL
ORDER BY beta ASC
"""


def initialize(context):
    g.target_num = getattr(g, 'target_num', 8)
    g.div_min = getattr(g, 'div_min', 0.03)
    g.div_top_pct = getattr(g, 'div_top_pct', 0.10)
    g.beta_win = getattr(g, 'beta_win', 252)  # 60 / 120 / 252
    g.backup_num = getattr(g, 'backup_num', 5)
    g.limit_up_exit = getattr(g, 'limit_up_exit', 1)
    g.reentry = getattr(g, 'reentry', 1)

    g.monthday = getattr(g, 'monthday', 1)  # 月内第几个交易日调仓
    g.div_method = getattr(g, 'div_method', 'rolling365')  # A-1/A-6: rolling365 / fiscal_year
    g.equal_weight = getattr(g, 'equal_weight', 0)  # 0=原版只分配新钱 / 1=等权再平衡

    g.target_list = getattr(g, 'target_list', [])
    g.backup_list = getattr(g, 'backup_list', [])
    g.high_limit = getattr(g, 'high_limit', set())
    g.blacklist = getattr(g, 'blacklist', set())
    g.n_exit = 0
    g.n_reentry = 0
    set_benchmark('000015.XSHG')
    run_daily(prepare, time='09:00')
    run_monthly(pick, monthday=g.monthday, time='09:01')
    run_monthly(trade, monthday=g.monthday, time='09:30')
    run_daily(check_limit_up, time='10:00')


def prepare(context):
    g.high_limit = set()
    held = list(context.portfolio.positions)
    if held and context.previous_date:
        bars = context.data.bars(context.previous_date, held)
        g.high_limit = {c for c, b in bars.items() if b.limit_up}


def pick(context):
    d = context.previous_date
    if d is None:
        return
    div_cte = DIV_FISCAL_YEAR if g.div_method == 'fiscal_year' else DIV_ROLLING365
    df = context.data.query(SQL.replace("{div}", div_cte), t1=d, uni=UNIVERSE, dmin=g.div_min,
                            dtop=g.div_top_pct, bw=g.beta_win)
    ranked = df['jq_code'].tolist()
    g.target_list = ranked[:g.target_num]
    g.backup_list = ranked[g.target_num:g.target_num + g.backup_num]
    g.blacklist = set()
    log.info('[PICK] %s 高股息+低beta池 %d 只，取 %d', d, len(ranked), len(g.target_list))


def trade(context):
    if not g.target_list:
        log.warn('目标池为空，跳过调仓')
        return
    tgt = set(g.target_list)
    held = set(context.portfolio.positions)
    # ---- 仓位口径（g.equal_weight）----
    # 0 = 原版：只把腾出来的现金平分给【新买入】的票，已持有仓位一律不动
    #     → 反复入选的票权重持续变大（赢家滚雪球）
    # 1 = 等权再平衡：每次调仓把所有目标拉回 total_value/N（会削超配）
    if g.equal_weight:
        per = context.portfolio.total_value / len(g.target_list)
        for s in list(context.portfolio.positions):
            if s not in tgt and s not in g.high_limit:
                order_target_value(s, 0)
            elif s in tgt and context.portfolio.positions[s].value > per:
                order_target_value(s, per)
        for s in g.target_list:
            order_target_value(s, per)
        return
    # ★ 与原版一致：昨日涨停的持仓【豁免】本次调仓卖出
    #   （原版 g.sell_list = [s for s in hold_list if s not in target and s not in high_limit_list]）
    for s in list(context.portfolio.positions):
        if s not in tgt and s not in g.high_limit:
            order_target_value(s, 0)
    # ★ 原版**不做等权再平衡**：只把腾出来的现金平分给【新买入】的票，
    #   已持有的仓位一律不动。反复入选的票权重会持续变大（赢家滚雪球），
    #   这是本策略收益的重要来源。
    #   我最初照抄了「傻瓜基准」的 per = total_value/N + 削超配写法，
    #   实测低估：低波 -0.5pp / 价值 -5.6pp / 组合 -8.6pp —— 缺口大小
    #   正好随袖内个股的趋势性递增，这就是同一个根因的指纹。
    buy = [s for s in g.target_list if s not in held]
    if not buy:
        return
    per = context.portfolio.cash / len(buy)
    for s in buy:
        order_target_value(s, per)


def check_limit_up(context):
    if not g.limit_up_exit:
        return
    codes = [c for c in g.high_limit if c in context.portfolio.positions]
    if not codes:
        return
    cur = context.current(codes)
    sold = 0
    for s in codes:
        d = cur.get(s)
        if d is not None and not d.get('limit_up'):
            order_target_value(s, 0)
            g.blacklist.add(s)
            g.n_exit += 1
            sold += 1
    if g.reentry and sold:
        held = set(context.portfolio.positions)
        cands = [s for s in g.target_list if s not in held and s not in g.blacklist]
        cands += [s for s in g.backup_list
                  if s not in held and s not in g.blacklist and s not in cands]
        cands = context.tradable(cands, 'buy')[:sold]
        if cands:
            per = context.portfolio.cash / len(cands)
            for s in cands:
                order_target_value(s, per)
                g.n_reentry += 1
