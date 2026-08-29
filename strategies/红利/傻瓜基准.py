#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""【傻瓜基准】股息率 top30 等权 · 月频调仓

复刻 JQ/红利/红利_傻瓜基准_股息率top30等权.txt。
它不是拿来用的策略，是一把尺子 —— 标定「复杂选股 + 交易纪律」值多少。

JQ 实测（分析结论.md）：Run E（无炸板规则）年化 5.06% / 夏普 0.055 / 回撤 29.72%

## 选股：只做一件事

股息率 = 近 365 天（股权登记日在 [prev-365, prev]）分红总额 / 当前总市值，
降序取前 N，门槛 > DIV_MIN。

★ 单位：`bonus_amount_rmb` 实测是【万元】，`totalmv` 是元 ——
  所以 dy = bonus_amount_rmb * 1e4 / totalmv。
  实测中位 1.43% / P90 4.21%，落在 A 股合理区间。

★ 不需要 plan_progress 过滤：实测【预案行没有股权登记日】（0 行），
  按登记日过滤天然只取到「实施方案」。原版也没过滤，行为一致。

## 本金必须 100 万

30 只票 10 万本金 = 单票 3333 元，股价 >33 元的买不进 100 股 ——
权重畸变 + 现金闲置，结果会被取整噪声污染，当不了尺子。
"""
# 版本一句话简介 —— 看板在版本号右边展示它。
# ★ 改动策略行为时【必须同步改这句】：版本 = 代码哈希，
#   简介是人识别两个版本差异的唯一线索。
NOTE = '股息率 top30 等权 · 月频 —— 本线**唯一做真等权再平衡**的版本（会把超配仓位削回 1/N）'

from assay.api import *

# 排除：ST/风险警示、次新(<250天)、科创板(68)、北交所(4/8 开头)
UNIVERSE = ("NOT is_risk_warned AND listed_days >= 250 AND list_date IS NOT NULL "
            "AND jq_code NOT LIKE '68%' AND jq_code NOT LIKE '4%' "
            "AND jq_code NOT LIKE '8%' AND totalmv > 0")

DIV_ROLLING365 = """
  SELECT code, sum(bonus_amount_rmb) * 1e4 AS amt
  FROM {t_dividend}
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
    FROM {t_dividend}
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
WITH div AS ({div}
)
SELECT p.jq_code, d.amt / p.totalmv AS dy
FROM {panel} p JOIN div d ON d.code = p.jq_code
WHERE p.date = DATE '{t1}' AND {uni} AND d.amt / p.totalmv > {dmin}
ORDER BY dy DESC
"""


def initialize(context):
    g.target_num = getattr(g, 'target_num', 30)
    g.div_min = getattr(g, 'div_min', 0.03)
    g.backup_num = getattr(g, 'backup_num', 10)
    g.limit_up_exit = getattr(g, 'limit_up_exit', 0)  # 0/1 —— Run E / Run F
    g.reentry = getattr(g, 'reentry', 0)  # 需 limit_up_exit=1 才有意义
    g.monthday = getattr(g, 'monthday', 1)  # 月内第几个交易日调仓
    # 仓位口径。★ 本策略【原版就是真等权再平衡】（JQ 文件里 per = total_value/N
    #   且会把超配仓位削回 per），这是它与其余三个策略的关键差异之一。
    #   加这个开关只为把「选股」与「仓位口径」两个变量分开做归因 ——
    #   否则「傻瓜基准跑赢」同时占了「30 只分散」和「真等权」两个优势，
    #   无法判断赢在哪一个。默认 1 = 保持原版。
    g.equal_weight = getattr(g, 'equal_weight', 1)
    g.div_method = getattr(g, 'div_method', 'rolling365')  # A-1/A-6: rolling365 / fiscal_year

    g.target_list = getattr(g, 'target_list', [])
    g.backup_list = getattr(g, 'backup_list', [])
    g.high_limit = getattr(g, 'high_limit', set())
    g.blacklist = getattr(g, 'blacklist', set())
    g.n_exit = 0
    g.n_hold = 0
    g.n_reentry = 0
    set_benchmark('000015.XSHG')          # 上证红利，与原版一致

    run_daily(prepare, time='09:00')
    run_monthly(pick, monthday=g.monthday, time='09:01')
    run_monthly(trade, monthday=g.monthday, time='09:30')
    run_daily(check_limit_up, time='10:00')


def prepare(context):
    """昨收涨停的持仓 —— 炸板离场规则的候选。"""
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
    df = context.data.query(SQL.replace("{div}", div_cte), t1=d, uni=UNIVERSE, dmin=g.div_min)
    ranked = df['jq_code'].tolist()
    g.target_list = ranked[:g.target_num]
    g.backup_list = ranked[g.target_num:g.target_num + g.backup_num]
    g.blacklist = set()
    log.info('[PICK] %s 合格池 %d 只，取前 %d', d, len(ranked), len(g.target_list))


def trade(context):
    if not g.target_list:
        log.warn('目标池为空，跳过调仓')
        return
    tgt = set(g.target_list)
    held = set(context.portfolio.positions)

    if not g.equal_weight:
        # 对照口径（= 另三个策略/JQ 原版的写法）：只把腾出的现金分给新买入的票，
        # 已持有仓位一律不动 -> 反复入选的票权重滚雪球，且权重永久携带历史路径。
        for s in list(context.portfolio.positions):
            if s not in tgt:
                order_target_value(s, 0)
        buy = [s for s in g.target_list if s not in held]
        if not buy:
            return
        per = context.portfolio.cash / len(buy)
        for s in buy:
            order_target_value(s, per)
        return

    # 目标市值按【调仓前】总权益算一次，避免边交易边漂移。
    # 与原版一致用 total_value / N（不留缓冲）—— 因此会有少量「现金不足」拒单，
    # 那是等权再平衡的固有约束，如实留痕而不是靠调参数掩盖。
    per = context.portfolio.total_value / len(g.target_list)

    # 1) 先减：清仓非目标 + 减仓超配（卖不掉的由 broker 拒单并留痕）
    for s in list(context.portfolio.positions):
        if s not in tgt:
            order_target_value(s, 0)
        elif context.portfolio.positions[s].value > per:
            order_target_value(s, per)

    # 2) 后加：建仓/加仓到等权
    for s in g.target_list:
        order_target_value(s, per)


def check_limit_up(context):
    """炸板离场：昨收涨停的持仓，今日若打开则卖出并拉黑。"""
    if not g.limit_up_exit:
        return
    codes = [c for c in g.high_limit if c in context.portfolio.positions]
    if not codes:
        return
    cur = context.current(codes)
    sold = 0
    for s in codes:
        d = cur.get(s)
        if d is None:
            continue
        if not d.get('limit_up'):
            order_target_value(s, 0)
            g.blacklist.add(s)
            g.n_exit += 1
            sold += 1
        else:
            g.n_hold += 1
    if g.reentry and sold:
        reentry(context, sold)


def reentry(context, n_slot):
    """炸板卖出后立刻补仓：目标池缺口 -> 备选池。"""
    held = set(context.portfolio.positions)
    cands = [s for s in g.target_list if s not in held and s not in g.blacklist]
    cands += [s for s in g.backup_list
              if s not in held and s not in g.blacklist and s not in cands]
    cands = context.tradable(cands, 'buy')[:n_slot]
    if not cands:
        return
    per = context.portfolio.cash / len(cands)
    for s in cands:
        order_target_value(s, per)
        g.n_reentry += 1
