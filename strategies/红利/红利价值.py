#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""【红利价值】基本面四条筛选 -> 股息率 top10% 且 >3%

复刻 JQ/红利/红利价值.txt。**不依赖 beta**，所以是第二个能立即验证的版本。

基本面四条（与原版逐条一致）：
    pe_ratio                        between 5   and 50
    inc_return（扣非 ROE 同比,%）      between 5   and 100
    inc_total_revenue_year_on_year   between 5   and 100
    inc_net_profit_year_on_year      between 10  and 100

★ pe 用本地 pe_ttm —— 已证明比聚宽 valuation.pe_ratio 更正确
  （后者含 6.58% 前视：用了当日未公告的财报）。
★ inc_* 三项取自 std/fin_indicator_q（聚宽 get_fundamentals 权威输出），
  覆盖率 98.0~99.7%，按 pub_date as-of。
★ ⚠️ std 层所有比率存【小数】，所以阈值要 ÷100（原版是百分数）。
  这条差点没发现 —— 第一版直接照抄 `between 5 and 100` 选出 0 只票才暴露。

股息率算法与傻瓜基准同（见那个文件的说明）。
"""
# 版本一句话简介 —— 看板在版本号右边展示它。
# ★ 改动策略行为时【必须同步改这句】：版本 = 代码哈希，
#   简介是人识别两个版本差异的唯一线索。
# ★ 默认值必须与 JQ/红利/红利价值.txt 的配置块一致（曾错配 3 处）：
#     TARGET_NUM=8 / DIV_TOP_PCT=0.10 / DIV_MIN=0.03 / BACKUP_NUM=5
#     ENABLE_LIMIT_UP_EXIT=True / ENABLE_REENTRY=True
#   我最初把炸板离场与再入场都设成关，导致对标虚高 3.17pp
#   （25.10% vs JQ 20.54%，实际应为 21.93%）。选股在 2019 逐月比对是
#   95.8% 相同的 —— 差距全在【行为开关】，不在选股。
NOTE = '基本面四条（PE 5~50 / 扣非ROE / 营收同比 / 净利同比）→ 股息率 top10%；合格池常只 3~4 只，不宜单独用'

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

# ---- 基本面两道增长过滤的口径（g.yoy_mode）----
# ★ 已实测判定（datalake/build/load_jq_indicator_q.py 曾标为「未实测」）：
#   `inc_*_year_on_year` 是【单季同比】，不是累计同比。判据是 Q1 反证 ——
#   Q1 的累计就是单季，两个候选口径在 Q1 上命中率都是 96.6%；到 Q2/Q3/年报，
#   累计口径塌到 4.4%/3.2%/2.4%，单季口径稳在 95%+。
#   （`inc_net_profit_year_on_year` 的分子是净利润【全部】，我们只有归母，
#     所以它只对上 28%；归母那个字段 `inc_net_profit_to_shareholders_*` 对上 94.8%。）
#
# JQ 原版用的就是单季同比字段，所以 'single_q' 才是忠实复刻。但单季同比噪声远大于
# 累计同比，很可能不是作者本意 —— 'cumulative' 用来量化这个选择值多少。
IND_SINGLE_Q = """
  SELECT code, inc_return, inc_total_revenue_year_on_year AS inc_rev,
         inc_net_profit_year_on_year AS inc_np,
         row_number() OVER (PARTITION BY code ORDER BY report_date DESC) rn
  FROM read_parquet('{root}/std/fin_indicator_q.parquet')
  WHERE pub_date <= DATE '{t1}'
"""

# 单季 + 归母口径：与 cumulative 的分子一致（都是归母），
# 用来把「期间口径」与「分子口径」两个变量分开看
IND_SINGLE_Q_PARENT = """
  SELECT code, inc_return, inc_total_revenue_year_on_year AS inc_rev,
         inc_net_profit_to_shareholders_year_on_year AS inc_np,
         row_number() OVER (PARTITION BY code ORDER BY report_date DESC) rn
  FROM read_parquet('{root}/std/fin_indicator_q.parquet')
  WHERE pub_date <= DATE '{t1}'
"""

# 累计同比：由 fin_quarterly 的 rev_cum / np_cum 自行构造（归母）
IND_CUMULATIVE = """
  SELECT y.code, i.inc_return, y.inc_rev, y.inc_np,
         row_number() OVER (PARTITION BY y.code ORDER BY y.report_date DESC) rn
  FROM (
    SELECT c.code, c.report_date,
           CASE WHEN p.rev_cum > 0 THEN c.rev_cum / p.rev_cum - 1 END AS inc_rev,
           CASE WHEN p.np_cum  > 0 THEN c.np_cum  / p.np_cum  - 1 END AS inc_np
    FROM read_parquet('{root}/std/fin_quarterly.parquet') c
    JOIN read_parquet('{root}/std/fin_quarterly.parquet') p
      ON p.code = c.code AND p.report_date = c.report_date - INTERVAL 1 YEAR
     AND p.pub_date <= DATE '{t1}'
    WHERE c.pub_date <= DATE '{t1}'
  ) y
  JOIN read_parquet('{root}/std/fin_indicator_q.parquet') i
    ON i.code = y.code AND i.report_date = y.report_date
   AND i.pub_date <= DATE '{t1}'
"""

IND_BY_MODE = {'single_q': IND_SINGLE_Q, 'single_q_parent': IND_SINGLE_Q_PARENT,
               'cumulative': IND_CUMULATIVE}

SQL = """
WITH div AS (
{div}
), ind AS (   -- 增长率两项 + 扣非ROE：按 pub_date as-of 取最近一期
{ind}
), fund AS (
  SELECT p.jq_code, p.totalmv FROM {panel} p JOIN ind i ON i.code = p.jq_code AND i.rn = 1
  WHERE p.date = DATE '{t1}' AND {uni}
    AND p.pe_ttm BETWEEN {pe_lo} AND {pe_hi}
    -- ★ 阈值除以 100：std 层所有比率都存【小数】(0.05 = 5%)，
    --   原版是百分数口径 between 5 and 100
    AND i.inc_return BETWEEN {roe_lo} AND {roe_hi}
    AND i.inc_rev BETWEEN {rev_lo} AND {rev_hi}
    AND i.inc_np BETWEEN {np_lo} AND {np_hi}
), r AS (
  -- ★ 顺序必须是【先按股息率降序截分位，再过 >3% 阈值】。
  --   原版 get_dividend_ratio_filter_list:
  --       df.sort_values(dividend_ratio, desc) -> df[int(p1*len):int(p2*len)] -> df[dy>threshold]
  --   我第一版写反了（先过阈值再算分位），分位是在【已过滤的小池子】上算的，
  --   目标池几乎空掉 —— 持仓集中度 86% 才暴露出来。
  SELECT f.jq_code, d.amt / f.totalmv AS dy,
         row_number() OVER (ORDER BY d.amt / f.totalmv DESC) AS rk,
         count(*) OVER () AS n
  FROM fund f JOIN div d ON d.code = f.jq_code
)
SELECT jq_code, dy FROM r
WHERE rk <= greatest(1, floor({dtop} * n)) AND dy > {dmin}
ORDER BY dy DESC
"""


def initialize(context):
    g.target_num = getattr(g, 'target_num', 8)
    g.div_min = getattr(g, 'div_min', 0.03)
    g.div_top_pct = getattr(g, 'div_top_pct', 0.10)
    g.backup_num = getattr(g, 'backup_num', 5)   # 原版 BACKUP_NUM = 5
    g.limit_up_exit = getattr(g, 'limit_up_exit', 1)  # 原版 ENABLE_LIMIT_UP_EXIT = True
    g.reentry = getattr(g, 'reentry', 1)  # 原版 ENABLE_REENTRY = True
    g.monthday = getattr(g, 'monthday', 1)  # 月内第几个交易日调仓
    # 炸板离场的检查时点。原版 10:00，但引擎无分时线，盘中相位用【收盘价】
    # 判定与成交 —— 等价于尾盘决策。实测炸板当日盘中是强负漂移
    # （开盘→收盘 均值 -1.579% / 中位 -1.565% / 63% 收低，全样本对照 +0.112%），
    # 所以按收盘价卖比按 10:00 价卖系统性地低约 1.5%/笔。
    # '09:31' 用开盘价成交，最接近原版的 10:00。
    g.exit_time = getattr(g, 'exit_time', '10:00')
    g.div_method = getattr(g, 'div_method', 'rolling365')
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
    g.yoy_mode = getattr(g, 'yoy_mode', 'single_q')  # single_q(=JQ原版) / single_q_parent / cumulative  # A-1/A-6: rolling365 / fiscal_year
    g.equal_weight = getattr(g, 'equal_weight', 0)  # 0=原版只分配新钱 / 1=等权再平衡

    g.target_list = getattr(g, 'target_list', [])
    g.backup_list = getattr(g, 'backup_list', [])
    g.high_limit = getattr(g, 'high_limit', set())
    g.blacklist = getattr(g, 'blacklist', set())
    g.n_exit = 0
    g.n_hold = 0
    g.n_reentry = 0
    g.pool_hist = []       # 每次调仓的合格池规模，收盘后统计欠配用
    set_benchmark('000015.XSHG')          # 上证红利，与原版一致

    run_daily(prepare, time='09:00')
    run_monthly(pick, monthday=g.monthday, time='09:01')
    run_monthly(trade, monthday=g.monthday, time='09:30')
    run_daily(check_limit_up, time=g.exit_time)


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
    ind_cte = IND_BY_MODE[g.yoy_mode]
    sql = SQL.replace("{div}", div_cte).replace("{ind}", ind_cte)
    df = context.data.query(sql, t1=d, uni=UNIVERSE, dmin=g.div_min,
                            dtop=g.div_top_pct,
              pe_lo=g.pe_lo, pe_hi=g.pe_hi, roe_lo=g.roe_lo, roe_hi=g.roe_hi,
              rev_lo=g.rev_lo, rev_hi=g.rev_hi, np_lo=g.np_lo, np_hi=g.np_hi)
    ranked = df['jq_code'].tolist()
    g.target_list = ranked[:g.target_num]
    g.backup_list = ranked[g.target_num:g.target_num + g.backup_num]
    g.blacklist = set()
    log.info('[PICK] %s 合格池 %d 只，取前 %d', d, len(ranked), len(g.target_list))
    g.pool_hist.append(len(ranked))


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

    # 1) 先减：只清仓非目标；★ 昨日涨停的持仓【豁免】卖出，与原版
    #    g.sell_list = [s for s in hold_list if s not in target and s not in high_limit_list] 一致
    for s in list(context.portfolio.positions):
        if s not in tgt and s not in g.high_limit:
            order_target_value(s, 0)

    # 2) 后加：★ 原版**不做等权再平衡** —— 只把腾出来的现金平分给【新买入】
    #    的票，已持有的仓位一律不动，所以反复入选的票权重会持续变大
    #    （赢家滚雪球），这是本策略收益的重要来源。
    #    我最初照抄了「傻瓜基准」的 per = total_value/N + 削超配写法，实测
    #    低估：低波 -0.5pp / 价值 -5.6pp / 组合 -8.6pp —— 缺口随袖内个股的
    #    趋势性单调递增，正是同一个根因的指纹。
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
        # ★ 必须按相位取字段：开盘相位拿不到 limit_up（那是收盘派生量），
        #   直接 d.get('limit_up') 会返回 None -> `not None` 为真 -> 把持仓全卖掉。
        sealed = d.get('limit_up') if 'limit_up' in d else d.get('open_limit_up')
        if not sealed:
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
