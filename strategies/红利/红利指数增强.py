#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""【红利指数增强】双 sleeve 并集：红利低波(A) + 红利价值(B)

复刻 JQ/红利/红利指数增强_v*.txt 的选股结构。JQ 实测（分析结论.md）：

| 版本 | 持仓 | 炸板 | 年化 | 夏普 | IR | 回撤 |
|---|---|---|---|---|---|---|
| A 原版 | 8 | 有 | 21.49% | 1.084 | 1.790 | 27.00% |
| B 关闭炸板 | 8 | 无 | 19.37% | 0.904 | 1.529 | 27.00% |
| D 再入场 | 8 | 有+再入场 | 22.41% | 1.112 | 1.866 | 27.00% |

## 结构

    Sleeve A（红利低波）: 全池 -> 股息率 top10% 且 >3% -> beta 升序 -> 取 g.num_a
    Sleeve B（红利价值）: 全池 -> 基本面四条 -> 股息率 top10% 且 >3% -> 取 g.num_b
    并集去重，**A 优先**（原版 A-5 明确要求确定性保序，替代 list(set()) 的不确定顺序）

原版 TARGET_NUM=[5,3]（合 8 只）；v3 起改 [10,5]（约 15 只）。
两者都做成参数，可直接对比 —— 分析结论.md 说 15 只的预期年化 15.4% 而非回测的 22.41%。

## 已知与原版的实现差异（都已量化，不藏）

1. **beta 本地算**（`std/beta_daily.parquet`，沪深300 滚动回归）。
   sweep 实测 252 天窗口最接近聚宽：红利低波单跑 10.75% vs JQ 11.27%（差 0.52pp）；
   60/120 天分别是 1.81%/6.47%，差很远 —— 所以窗口是敏感参数，默认 252。
2. **炸板离场用日线代理**：原版 10:00 取那一刻的分钟价，我们只有日线，
   用「今日是否仍收在涨停价」代替。小市值线实测这个代理值 0.3~1.0pp。
3. **v7 的 `DIV_METHOD='fiscal_year'`（按会计年度汇总 + 800 天回溯）未实现** ——
   本文件用的是 365 天滚动窗口（原版/v3~v5 的口径）。v7 那个改动会改变股息率排序，
   属于另一个口径，不在这里混进来。
"""
# 版本一句话简介 —— 看板在版本号右边展示它。
# ★ 改动策略行为时【必须同步改这句】：版本 = 代码哈希，
#   简介是人识别两个版本差异的唯一线索。
# ★ 本策略依赖 beta_252 —— **回测起点不要早于 2006-01-01**。
#   沪深300 首个发布值在 2005-01-04（该指数在那之前不存在），
#   252 日满窗口要到 2005-08 才有 98% 覆盖（2005-01~06 是 0%）。
#   起点更早会静默欠配：实测 2005 年平均仓位仅 74.3%，指标被现金稀释。
#   报告里的「平均仓位」一栏会暴露这个问题，引用结论前先看它。
NOTE = '低波(A) + 价值(B) 双袖并集去重（A 优先）；仓位只把腾出的现金分给新票、老仓位不动'

from assay.api import *

UNIVERSE = ("NOT is_risk_warned AND listed_days >= 250 AND list_date IS NOT NULL "
            "AND jq_code NOT LIKE '68%' AND jq_code NOT LIKE '4%' "
            "AND jq_code NOT LIKE '8%' AND totalmv > 0")

# Sleeve A：高股息 -> 低 beta
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

SQL_A = """
WITH div AS ({div}
), base AS (
  SELECT p.jq_code, d.amt / p.totalmv AS dy,
         row_number() OVER (ORDER BY d.amt / p.totalmv DESC) AS rk,
         count(*) OVER () AS n
  FROM {panel} p JOIN div d ON d.code = p.jq_code
  WHERE p.date = DATE '{t1}' AND {uni}
), hi AS (
  SELECT jq_code, dy FROM base
  WHERE rk <= greatest(1, floor({dtop} * n)) AND dy > {dmin}
), ranked AS (
  SELECT h.jq_code,
         row_number() OVER (ORDER BY b.beta_{bw} ASC) AS rk,
         count(*) OVER ()                            AS bn
  FROM hi h
  JOIN read_parquet('{root}/std/beta_daily.parquet') b
    ON b.code = h.jq_code AND b.date = DATE '{t1}'
  WHERE b.beta_{bw} IS NOT NULL
)
-- B-3：原版先砍掉 beta 高的一半（p2=0.50）再取前 N。num_a=5 时池子>=10 就
-- 不 binding（死参数）；num_a=10 时需池子>=20，否则欠配。v4 起删除该截断。
SELECT jq_code FROM ranked
WHERE rk <= greatest(1, floor({bpct} * bn))
ORDER BY rk
"""

# Sleeve B：基本面四条 -> 高股息
SQL_B = """
WITH div AS ({div}
), ind AS (
  SELECT code, inc_return, inc_total_revenue_year_on_year AS inc_rev,
         inc_net_profit_year_on_year AS inc_np,
         row_number() OVER (PARTITION BY code ORDER BY report_date DESC) rn
  FROM read_parquet('{root}/std/fin_indicator_q.parquet')
  WHERE pub_date <= DATE '{t1}'
), fund AS (
  SELECT p.jq_code, p.totalmv FROM {panel} p
  JOIN ind i ON i.code = p.jq_code AND i.rn = 1
  WHERE p.date = DATE '{t1}' AND {uni}
    AND p.pe_ttm BETWEEN 5 AND 50
    -- ★ std 层比率存【小数】，所以阈值 ÷100（原版是百分数 between 5 and 100）
    AND i.inc_return BETWEEN 0.05 AND 1.00
    AND i.inc_rev BETWEEN 0.05 AND 1.00
    AND i.inc_np BETWEEN 0.10 AND 1.00
), base AS (
  SELECT f.jq_code, d.amt / f.totalmv AS dy,
         row_number() OVER (ORDER BY d.amt / f.totalmv DESC) AS rk,
         count(*) OVER () AS n
  FROM fund f JOIN div d ON d.code = f.jq_code
)
SELECT jq_code FROM base
WHERE rk <= greatest(1, floor({dtop} * n)) AND dy > {dmin}
ORDER BY dy DESC
"""


def initialize(context):
    g.num_a = getattr(g, 'num_a', 5)  # Sleeve A 取几只（原版 5，v3 起 10）
    g.num_b = getattr(g, 'num_b', 3)  # Sleeve B 取几只（原版 3，v3 起 5）
    g.backup_a = getattr(g, 'backup_a', 5)
    g.backup_b = getattr(g, 'backup_b', 5)
    g.div_min = getattr(g, 'div_min', 0.03)
    g.div_top_pct = getattr(g, 'div_top_pct', 0.10)
    g.beta_win = getattr(g, 'beta_win', 252)
    g.limit_up_exit = getattr(g, 'limit_up_exit', 1)
    g.reentry = getattr(g, 'reentry', 1)
    g.monthday = getattr(g, 'monthday', 1)  # 月内第几个交易日调仓
    g.equal_weight = getattr(g, 'equal_weight', 0)
    g.beta_pct = getattr(g, 'beta_pct', 1.0)          # B-3: 0.50=原版/v2/v3, 1.0=v4 起
    g.div_method = getattr(g, 'div_method', 'rolling365')  # A-1/A-6: rolling365 / fiscal_year

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
    # ★ 用字面 replace 而不是 format 注入 CTE：CTE 里还有 {root}/{t1}，
    #   必须留给 query() 在同一次 substitute 里解析（format 只走一遍）。
    sql_a = SQL_A.replace('{div}', div_cte)
    sql_b = SQL_B.replace('{div}', div_cte)
    kw = dict(t1=d, uni=UNIVERSE, dmin=g.div_min, dtop=g.div_top_pct)
    a = context.data.query(sql_a, bw=g.beta_win, bpct=g.beta_pct, **kw)['jq_code'].tolist()
    b = context.data.query(sql_b, **kw)['jq_code'].tolist()
    la, lb = a[:g.num_a], b[:g.num_b]
    # 确定性去重保序，A 优先（原版 A-5：替代 list(set()) 的不确定顺序）
    tgt = []
    for s in la + lb:
        if s not in tgt:
            tgt.append(s)
    bk = []
    for s in a[g.num_a:g.num_a + g.backup_a] + b[g.num_b:g.num_b + g.backup_b]:
        if s not in tgt and s not in bk:
            bk.append(s)
    g.target_list, g.backup_list, g.blacklist = tgt, bk, set()
    log.info('[PICK] %s A池%d取%d / B池%d取%d -> 并集 %d 只',
             d, len(a), len(la), len(b), len(lb), len(tgt))


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
    # ★ 昨日涨停的持仓【豁免】本次调仓卖出，与原版
    #   g.sell_list = [s for s in hold_list if s not in target and s not in high_limit_list] 一致
    for s in list(context.portfolio.positions):
        if s not in tgt and s not in g.high_limit:
            order_target_value(s, 0)
    # ★ 原版**不做等权再平衡**：只把腾出来的现金平分给【新买入】的票，
    #   已持有的仓位一律不动，反复入选的票权重会持续变大（赢家滚雪球）。
    #   照抄「傻瓜基准」的 total_value/N + 削超配会低估 8.6pp。
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
