#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""【红利指数增强】双 sleeve 并集：红利低波(A) + 红利价值(B)

★★ 实际要跑的配置是 **div_method=fiscal_year**（= JQ v7），不是默认值：

      python3 run.py strategies/红利/红利指数增强.py \
        --start 2016-01-01 --end 2026-06-30 --cash 1000000 \
        --param div_method=fiscal_year

   默认之所以留 rolling365，只是为了和 JQ 版本谱对齐（默认 = v4/v5，
   显式选 fiscal_year = v7），这样 §三 版本映射与 §四 参数敏感性两张表
   的参照点不变。**不是**因为 rolling365 更好。

   fiscal_year 是【正确性修复】(A-1) 而不是参数调优：rolling365 按股权
   登记日滚动 365 天求和，实施日在年度间漂移就会让窗口内出现两次或零次
   年度分红；公司从年派改半年派时（2024 年后大量银行/央企如此）还会混入
   不同归属期的分红。JQ 实测工商银行 2025-06-01 虚高 +46%、
   2026-06-01 虚高 +53%（真实年度分红 3.035→3.064→3.080→3.103 很稳定）。

   本地实测（2016-01-01~2026-06-30，100 万，引擎默认成本）**五指标全胜**：
      rolling365   年化 17.43%  回撤 22.04%  夏普 1.15  索提诺 1.82  卡玛 0.79
      fiscal_year  年化 20.07%  回撤 17.95%  夏普 1.30  索提诺 2.11  卡玛 1.12
   收益提升是副产物，采纳理由是"另一个算法是错的"。

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
NOTE = '低波(A)+价值(B) 双袖并集去重（A 优先）· 默认 [10,5]（非 JQ 原版 [5,3]，实测五项全胜）· 仓位只把腾出的现金分给新票'

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
    AND p.pe_ttm BETWEEN {pe_lo} AND {pe_hi}
    -- ★ std 层比率存【小数】，所以阈值 ÷100（原版是百分数 between 5 and 100）
    AND i.inc_return BETWEEN {roe_lo} AND {roe_hi}
    AND i.inc_rev BETWEEN {rev_lo} AND {rev_hi}
    AND i.inc_np BETWEEN {np_lo} AND {np_hi}
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
    # ★★ 默认 [10,5]，**不是 JQ 原版的 [5,3]** —— 这是本项目实测后的选择。
    #   2016-2025 · warmup=1 · v7 口径 · 11 组配比扫描，[10,5] 五项全胜：
    #       年化 +2.49pp / 回撤 -3.53pp / 夏普 +0.20 / IR +0.47 / 换手 -0.17
    #   且 [10,5] 是 11 组中的第一名（24.41%），num_a=10 的三组占据前三。
    #   规律：num_a 太小(5/8) 回撤恶化到 19.5~21.2%、单票峰值冲到 30~37%；
    #         num_a 太大(15/20) 收益递减（20.8 -> 18.4%），过度分散。
    #         num_b 在 3~8 间几乎无差别（极差 0.64pp）—— 价值袖池子常给不满，
    #         加大 num_b 也买不到更多票（这也解释了 v6b 与 v6 差异极小）。
    #   与 JQ「修复与优化记录.md §35」方向一致：它论证 [5,3]->[10,5] 使调仓日
    #   极差 12.10pp -> 3.32pp（稳健性），代价 0.93pp 年化；我们这边年化不但
    #   没损失反而 +2.49pp，差别应来自 v7 分红口径（JQ 那个基线用 rolling365）。
    #
    # ⚠️ 因此【默认配置不再等于 JQ 原版】。要复现 JQ 原版/对标，显式传参：
    #       --param num_a=5 --param num_b=3 --param beta_pct=0.50 \
    #       --param div_top_pct=0.10 --param div_method=rolling365
    g.num_a = getattr(g, 'num_a', 10)  # 原版 5；实测 10 更优（见上）
    g.num_b = getattr(g, 'num_b', 5)   # 原版 3；实测 5 更优（3~8 差异 <0.7pp）
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
    # ---- B-1：Sleeve B 的 8 个 between 阈值 ----
    # JQ 把这 8 个数标为「拍的、有过拟合风险」，但任何地方都没给细节，也从未修过。
    # 参数化只为做敏感性 —— 不是修 bug，默认值严格等于原版：
    #   pe_ratio                        between 5   and 50
    #   inc_return（扣非ROE, %）          between 5   and 100
    #   inc_total_revenue_year_on_year  between 5   and 100
    #   inc_net_profit_year_on_year     between 10  and 100
    # ★ 本地 std 层比率存【小数】，所以 inc_* 三项阈值要 ÷100
    #   （曾照抄 5/100 选出 0 只票才发现）。pe 不用除。
    g.pe_lo = getattr(g, 'pe_lo', 5.0)
    g.pe_hi = getattr(g, 'pe_hi', 50.0)
    g.roe_lo = getattr(g, 'roe_lo', 0.05)
    g.roe_hi = getattr(g, 'roe_hi', 1.00)
    g.rev_lo = getattr(g, 'rev_lo', 0.05)
    g.rev_hi = getattr(g, 'rev_hi', 1.00)
    g.np_lo = getattr(g, 'np_lo', 0.10)
    g.np_hi = getattr(g, 'np_hi', 1.00)
    # ---- B-1 的三种口径（g.b1_mode）----
    # JQ「修复与优化记录.md §216」把这 8 个阈值标为 🔴 最大过拟合源，并给了修法：
    #   · 删所有上界：三个 100 完全相同（复制粘贴痕迹），扣非ROE>100% 近乎不存在
    #     -> 基本不 binding，纯粹是「剔除样本内异常值」的过拟合手法
    #   · 统一下界：5/5/10 无理论依据，是逐个手调的痕迹，统一成「只要正增长」
    #   · 8 个参数 -> 1~2 个
    # ★ 验收判据是文档指定的：**极差不变（<=3.5pp）+ 参数变少 = 通过**，
    #   不看收益。文档明言「年化大概率低于当前，这是预期内的也是正确的 ——
    #   现在的数字里包含多余参数在样本内的拟合收益」。
    #   'orig'    8 参数，严格等于 JQ 原版（默认）
    #   'no_hi'   删掉三个上界（100/100/100），保留下界 5/5/10 —— 6 -> 5 个参数
    #   'simple'  删上界 + 下界统一为「正增长」-> 只剩 pe 两个界，8 -> 2 个参数
    g.b1_mode = getattr(g, 'b1_mode', 'orig')
    # ---- A-4：buy_list 为空时的现金闲置（g.idle_fix）----
    # JQ「修复与优化记录.md §212」列为待做项，但 JQ 自己【从未修】：
    #   trade 里 buy_list 为空而 sell_list 非空时，卖出的钱闲置到下月。
    #   与已修的「炸板卖出后现金闲置」同类（那个在 再入场.txt 里修了）。
    # 0 = 原版行为（默认，保真）；1 = 把闲置现金按等权补进已持有的目标票。
    # ★ 只有在「本月无新票可买、但卖掉了非目标票」时才有差别 —— 频率不高，
    #   所以预期效应很小；做成参数是为了能【测出来】而不是猜。
    g.idle_fix = getattr(g, 'idle_fix', 0)
    # B-1 口径：把上界/下界按 b1_mode 覆写（默认 orig 时保持原值不动）
    _BIG = 1e9
    if g.b1_mode == 'no_hi':
        g.roe_hi = g.rev_hi = g.np_hi = _BIG
    elif g.b1_mode == 'simple':
        g.roe_hi = g.rev_hi = g.np_hi = _BIG
        g.roe_lo = g.rev_lo = g.np_lo = 0.0   # 只要正增长

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
    kw = dict(t1=d, uni=UNIVERSE, dmin=g.div_min, dtop=g.div_top_pct,
              pe_lo=g.pe_lo, pe_hi=g.pe_hi, roe_lo=g.roe_lo, roe_hi=g.roe_hi,
              rev_lo=g.rev_lo, rev_hi=g.rev_hi, np_lo=g.np_lo, np_hi=g.np_hi)
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
        # A-4：原版在此直接 return，卖出所得闲置到下月。
        if g.idle_fix and g.target_list:
            held_tgt = [s for s in g.target_list if s in context.portfolio.positions]
            if held_tgt and context.portfolio.cash > 0:
                add = context.portfolio.cash / len(held_tgt)
                for s in held_tgt:
                    order_target_value(s, context.portfolio.positions[s].value + add)
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
