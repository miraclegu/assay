#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SG-MS-PEG-HL 本体：三路成长因子并集 + 小市值。

对标聚宽 SG-MS-PEG-HL-v3d.txt。三条支线各自筛完后按流通市值升序取若干只，
取并集再按流通市值升序、过滤、截到 stock_num。

一个文件覆盖聚宽侧全部消融版本 —— 只改三个切片数字（与聚宽 solo/drop 版的
做法完全一致，那些文件之间的差异也就是这三个数字）：

    slice_sg / slice_ms / slice_peg
      (5,5,5)    = v3d 三路
      (15,0,0)   = solo-SG      (0,15,0) = solo-MS     (0,0,15) = solo-PEG
      (0,5,5)    = drop-SG      (5,0,5)  = drop-MS     (5,5,0)  = drop-PEG

## 因子口径（详见 datalake/docs/jqfactor-口径.md）

已定案、本地精确复现（6/6）：
    total_profit_growth_rate = TTM 利润总额同比，分母取【原值】不取 abs
    net_profit_growth_rate   = TTM【全口径】净利润同比（含少数股东）
    PEG                      = (totalmv / 归母净利TTM) / (归母净利TTM同比 × 100)
    turnover_volatility      = 20 个交易日 turnover 的标准差(ddof=1) / 100
                               （已验证 panel.turnover 与聚宽 turnover_ratio 比值 1.0000）
4/6：
    operating_revenue_growth_rate = TTM 营业总收入同比（剩余缺口见文档）

🔴 sales_growth / earnings_growth 口径【未定案】，这里用 Barra SGRO/EGRO 式
   近似（5 个年度间隔 TTM 点对时间回归，斜率 ÷ |均值|）。与聚宽值皮尔逊 +0.92
   但秩相关仅 +0.49。=> **SG 路与 MS 路不能声称复现聚宽**，它们的锚只能作参考。
   PEG 路的三个输入全部已定案，所以 **solo-PEG 是唯一可以严格对标的支线**。

## get_factor_filter_list 的语义（必须逐字复刻）

    df.dropna() -> [positive_only 时再筛 >0] -> sort_values(ascending=sort)
                -> index[int(p1*len(df)) : int(p2*len(df))]
  · 分位分母是【dropna 之后】的数量
  · SG:  ('sales_growth', False, 0, 0.1)          降序取前 10%
  · PEG: ('PEG', True, 0, 0.2, positive_only=True) 先剔非正，升序取前 20%
  · 换手:('turnover_volatility', True, 0, 0.5)     在 peg_list 内升序取前 50%

★ eps>0 过滤在【分位切完之后】才做 —— 聚宽是先 get_factor_filter_list（在未
  过滤 eps 的全池上算分位），再 query+filter(eps>0)。顺序颠倒会改变分位分母。

## MS 路的归一化开关（ms_norm）

聚宽原版把四个【原始】增长率直接加权求和，未做任何归一化：
    0.10*营收增长 + 0.35*利润总额增长 + 0.15*净利润增长 + 0.40*5年盈利增长
实测（2019-09-30 横截面）名义权重与实际影响力严重脱节：营收因子名义占 10%、
实际只贡献 1.4% 的分数离散度；总分排序与「利润总额增长率」排序秩相关 0.9825。
更糟的是被极端值支配 —— 前 10% 里 19.6% 是增长率 >1000% 的「去年基数接近零」
的除法产物。聚宽作者自己在 v3b 注释里把这个问题留给了至今未跑的 v4。

    ms_norm=0  原版：原始值加权（默认，用于对标）
    ms_norm=1  分位归一化：每个因子先转横截面 percent_rank 再加权（= v4 的
               rank(pct=True)）。四个因子量纲被拉平，名义权重才等于实际权重。

## 实测结果

### ① 对标验证（聚宽窗口 2019-01-01~2026-06-30 + 聚宽成本 滑点0.0015/佣金万3/最低5/印花税千一）

| 配置 | 本地年化 | 聚宽锚 | 差 | 本地交易/换手 | 聚宽交易/换手 |
|---|---|---|---|---|---|
| 基准 v0b | 38.16% | 38.98% | −0.82pp | 574 / 7.48 | 556 / 7.4 |
| **solo-PEG**（口径全定案） | **24.50%** | **23.08%** | **+1.42pp** | 1035 / **14.43** | 1102 / **14.7** |
| solo-SG（近似因子） | 38.85% | 41.95% | −3.10pp | **571 / 7.68** | **576 / 7.7** |
| solo-MS（近似因子） | 24.68% | 42.49% | **−17.81pp** | 592 / 8.20 | 630 / 8.4 |
| v3d 三路 | 36.76% | 48.27% | −11.51pp | 764 / 10.09 | 817 / 10.9 |

**框架已验证**：口径全定案的 solo-PEG 只差 +1.42pp，换手 14.43 vs 14.7、交易 1035 vs 1102
—— 连"PEG 路换手是基准两倍"这个特征都复现了。基准 v0b 差 −0.82pp。
**误差恰好集中在两个近似因子上**：solo-SG 交易 571 vs 576（差 0.9%）说明候选池
规模对了、成员不同；solo-MS 差 17.81pp，因为 earnings_growth 占权重 0.40 最大，
且原版未归一化时被极端值放大，近似误差被成倍放大。
=> 结论：并集逻辑 / 切片 / eps 顺序 / 市值排序 / get_factor_filter_list 语义全部正确，
   剩余残差 = sales_growth 与 earnings_growth 的口径未定案。

### ② 主结果（2016-01-01~2026-08-20，100 万，引擎默认成本）

| 配置 | 年化 | 回撤 | 夏普 | 交易 | 年换手 |
|---|---|---|---|---|---|
| 基准 v0b（无因子层） | **31.19%** | 52.11% | 1.05 | 803 | 7.33 |
| solo-SG | 26.79% | 47.72% | 1.00 | 754 | 7.74 |
| solo-MS | 18.36% | 48.72% | 0.74 | 808 | 7.92 |
| solo-PEG | 14.50% | 49.34% | 0.66 | 1458 | 14.30 |
| v3d 三路 | 27.79% | 40.24% | 0.99 | 1066 | 10.18 |
| **v3d + 归一化** | **30.53%** | **40.03%** | **1.08** | 1055 | 10.42 |

**三条支线在这个区间【全部跑输】无因子层的基准**（26.79 / 18.36 / 14.50 vs 31.19），
v3d 也跑输（27.79）。这与聚宽在 2019-2026 窗口的结论（v3d 48.27 > v0b 38.98）相反
—— 与聚宽自己记录的「因子层价值在后段几乎消失（+1.9pp）」方向一致，本区间多了
2016-2018 又延到 2026-08，因子层的正贡献被摊掉了。

**但因子层买到了风险下降**：v3d 回撤 40.24% vs 基准 52.11%（−11.87pp）。
归一化后 **30.53% / 40.03% / 夏普 1.08** vs 基准 **31.19% / 52.11% / 1.05**
—— 收益基本持平、回撤低 12pp、夏普更高。风险调整后是实质改善。

**PEG 路是破坏性的**：14.50% 比基准低 16.7pp，换手却是两倍（14.30 vs 7.33）。
这与聚宽实测（低 15.9pp、换手 14.7 vs 7.4）在幅度和特征上都吻合。

### ③ 归一化的效果（任务 3）

| | 原版（原始值加权） | 归一化（percent_rank 加权） | 差 |
|---|---|---|---|
| solo-MS 年化 | 18.36% | **23.20%** | **+4.84pp** |
| solo-MS 夏普 | 0.74 | 0.88 | +0.14 |
| solo-MS 胜率 | 57.67% | 60.18% | +2.51pp |
| v3d 年化 | 27.79% | **30.53%** | **+2.74pp** |
| v3d 回撤 | 40.24% | 40.03% | −0.21pp |
| v3d 夏普 | 0.99 | **1.08** | +0.09 |

**归一化在两个层面都是改善，且五个指标同向。** 这符合机制预期：原版把四个量纲
差 17 倍的原始增长率直接加权，实际由「利润总额增长率」的极端值支配（名义占 10%
的营收因子实际只贡献 1.4% 离散度）；换成分位秩后名义权重才等于实际权重。

### ④ 多参数扫描（全部 ms_norm=1，2016-2026.08，默认成本）

| 配置 | 年化 | 回撤 | 夏普 | 仓位<80% |
|---|---|---|---|---|
| 切片 (2,2,2) | 28.07% | 40.44% | 0.99 | 20.6% |
| 切片 (3,3,3) | 33.80% | 37.05% | 1.17 | 15.8% |
| 切片 (4,4,4) | 26.25% | 38.72% | 0.97 | 6.5% |
| 切片 (5,5,5) | 30.53% | 40.03% | 1.08 | 4.3% |
| 切片 (6,6,6) | 30.82% | 39.86% | 1.09 | 4.1% |
| 切片 (8,8,8) | 30.51% | 39.88% | 1.08 | 3.9% |
| drop-PEG (5,5,0) + 归一化 | 28.37% | 40.81% | 1.00 | — |
| 关炸板离场 | 21.21% | 42.24% | 0.81 | — |
| 调仓改周三 | 28.40% | 40.00% | 1.03 | — |

★ **(3,3,3) 的 33.80% 是噪声尖峰不是最优** —— 切片-年化【非单调】
  （28.07 → 33.80 → 26.25 → 30.53 → 30.82 → 30.51），且小切片的仓位明显退化
  （<80% 的日子 20.6% / 15.8%，因为并集只有 6~9 只、填不满 10 个仓位）。
  稳定平台是 (5,5,5)~(8,8,8) 的 30.5~30.8%，仓位干净（<80% 约 4%）。
  **别按单点最高值选参数** —— 这条线上已多次证明效应小于区间/参数噪声。
★ 炸板离场值 +9.3pp（30.53 vs 21.21），与 mincap 上测到的 +5.5pp（t=3.42 显著）同向。
★ 调仓日改周三 −2.13pp，落在刀刃噪声带内，不按符号解读。
★ stock_num 调到 15/20 与 10 结果【完全相同】—— 切片 (5,5,5) 的并集只有 13~15 只，
  截断不生效。要真的加仓位数必须同时放大切片。

"""
from assay.api import *          # noqa: F401,F403

SQL = """
WITH univ AS (
  SELECT code FROM read_parquet('{root}/std/security_universe.parquet')
  WHERE sec_type='stock' AND list_date <= DATE '{sd}'
    AND (delist_date IS NULL OR delist_date > DATE '{sd}')
    AND date_diff('day', list_date::DATE, DATE '{sd}') >= {listed}
    AND code NOT LIKE '688%'
), today AS (
  SELECT jq_code, floatmv, totalmv, is_risk_warned FROM {panel} WHERE date = DATE '{sd}'
), miss AS (
  -- 停牌：只结转价格派生量，基本面走 as-of（见 sgmspeg_v0b.py 的说明）
  SELECT u.code AS jq_code, p.floatmv, p.totalmv, p.is_risk_warned
  FROM (SELECT code, DATE '{sd}' AS d FROM univ
        WHERE code NOT IN (SELECT jq_code FROM today)) u
  ASOF LEFT JOIN (
      SELECT jq_code, date, floatmv, totalmv, is_risk_warned
      FROM {panel} WHERE date > DATE '{sd}' - INTERVAL 400 DAY
  ) p ON p.jq_code = u.code AND p.date <= u.d
), base AS (
  SELECT * FROM (
    SELECT * FROM today WHERE jq_code IN (SELECT code FROM univ)
    UNION ALL SELECT * FROM miss
  ) WHERE NOT COALESCE(is_risk_warned, FALSE) AND floatmv > 0
), fq AS (
  SELECT code, g_rev, g_tp, g_np, g_npp, ttm_npp, sg_approx, eg_approx FROM (
    SELECT *, row_number() OVER (PARTITION BY code
             ORDER BY report_date DESC, pub_date DESC) rn
    FROM read_parquet('{root}/std/jqfactor_q.parquet') WHERE pub_date <= DATE '{sd}'
  ) WHERE rn = 1
), eps1 AS (
  SELECT code, eps FROM (
    SELECT code, eps, row_number() OVER (PARTITION BY code
             ORDER BY report_date DESC, pub_date DESC) rn
    FROM read_parquet('{root}/std/fin_indicator_q.parquet')
    WHERE pub_date <= DATE '{sd}' AND eps IS NOT NULL
  ) WHERE rn = 1
), tv AS (
  -- turnover_volatility = 20 个交易日 turnover 标准差 / 100
  SELECT jq_code, stddev_samp(turnover) / 100.0 AS tvol
  FROM (SELECT jq_code, turnover, row_number() OVER (PARTITION BY jq_code
                 ORDER BY date DESC) rn
        FROM {panel} WHERE date <= DATE '{sd}'
          AND date > DATE '{sd}' - INTERVAL 70 DAY AND turnover IS NOT NULL)
  WHERE rn <= 20 GROUP BY 1 HAVING count(*) >= 10
), pool AS (
  -- ★ 这里【不】过滤 eps —— 分位分母必须是未过滤 eps 的全池
  SELECT b.jq_code, b.floatmv, f.g_rev, f.g_tp, f.g_np,
         f.sg_approx, f.eg_approx, t.tvol,
         CASE WHEN f.ttm_npp > 0 AND f.g_npp IS NOT NULL AND f.g_npp <> 0
              THEN (b.totalmv / f.ttm_npp) / (f.g_npp * 100.0) END AS peg
  FROM base b
  LEFT JOIN fq f ON f.code = b.jq_code
  LEFT JOIN tv t ON t.jq_code = b.jq_code
),
-- ---------------- SG 路 ----------------
sg_r AS (SELECT jq_code, floatmv,
            row_number() OVER (ORDER BY sg_approx DESC, jq_code) rn,
            count(*) OVER () n FROM pool WHERE sg_approx IS NOT NULL),
sg AS (SELECT jq_code, floatmv FROM sg_r WHERE rn <= floor(0.1 * n)),
-- ---------------- MS 路 ----------------
ms_p AS (SELECT jq_code, floatmv, g_rev, g_tp, g_np, eg_approx,
            percent_rank() OVER (ORDER BY g_rev)     pr_rev,
            percent_rank() OVER (ORDER BY g_tp)      pr_tp,
            percent_rank() OVER (ORDER BY g_np)      pr_np,
            percent_rank() OVER (ORDER BY eg_approx) pr_eg
         FROM pool
         WHERE g_rev IS NOT NULL AND g_tp IS NOT NULL
           AND g_np IS NOT NULL AND eg_approx IS NOT NULL),
ms_s AS (SELECT jq_code, floatmv,
            CASE WHEN {msnorm}
                 THEN 0.10*pr_rev + 0.35*pr_tp + 0.15*pr_np + 0.40*pr_eg
                 ELSE 0.10*g_rev  + 0.35*g_tp  + 0.15*g_np  + 0.40*eg_approx
            END AS score FROM ms_p),
ms_r AS (SELECT jq_code, floatmv,
            row_number() OVER (ORDER BY score DESC, jq_code) rn,
            count(*) OVER () n FROM ms_s),
ms AS (SELECT jq_code, floatmv FROM ms_r WHERE rn <= floor(0.1 * n)),
-- ---------------- PEG 路 ----------------
peg_r AS (SELECT jq_code, floatmv, tvol,
            row_number() OVER (ORDER BY peg ASC, jq_code) rn,
            count(*) OVER () n FROM pool WHERE peg IS NOT NULL AND peg > 0),
peg_1 AS (SELECT jq_code, floatmv, tvol FROM peg_r WHERE rn <= floor(0.2 * n)),
peg_t AS (SELECT jq_code, floatmv,
            row_number() OVER (ORDER BY tvol ASC, jq_code) rn,
            count(*) OVER () n FROM peg_1 WHERE tvol IS NOT NULL),
peg AS (SELECT jq_code, floatmv FROM peg_t WHERE rn <= floor(0.5 * n)),
-- ---------------- 三路各取 slice（eps>0 在此才生效），并集 ----------------
pick AS (
  SELECT jq_code, floatmv FROM (
      SELECT s.jq_code, s.floatmv, row_number() OVER (ORDER BY s.floatmv ASC, s.jq_code) k
      FROM sg s JOIN eps1 e ON e.code = s.jq_code AND e.eps > 0
  ) WHERE k <= {ssg}
  UNION
  SELECT jq_code, floatmv FROM (
      SELECT m.jq_code, m.floatmv, row_number() OVER (ORDER BY m.floatmv ASC, m.jq_code) k
      FROM ms m JOIN eps1 e ON e.code = m.jq_code AND e.eps > 0
  ) WHERE k <= {sms}
  UNION
  SELECT jq_code, floatmv FROM (
      SELECT g.jq_code, g.floatmv, row_number() OVER (ORDER BY g.floatmv ASC, g.jq_code) k
      FROM peg g JOIN eps1 e ON e.code = g.jq_code AND e.eps > 0
  ) WHERE k <= {speg}
)
SELECT jq_code FROM pick ORDER BY floatmv ASC, jq_code
"""


def initialize(context):
    g.stock_num = getattr(g, 'stock_num', 10)
    g.listed_days = getattr(g, 'listed_days', 375)
    g.limit_days = getattr(g, 'limit_days', 20)
    g.weekday = getattr(g, 'weekday', 1)
    g.exit_time = getattr(g, 'exit_time', '14:00')
    g.limit_up_exit = getattr(g, 'limit_up_exit', 1)
    # 三路切片 —— 见文件头的组合表
    g.slice_sg = getattr(g, 'slice_sg', 5)
    g.slice_ms = getattr(g, 'slice_ms', 5)
    g.slice_peg = getattr(g, 'slice_peg', 5)
    g.ms_norm = getattr(g, 'ms_norm', 0)
    g.hold_history = []
    g.high_limit = set()

    set_benchmark('000905.XSHG')

    run_daily(prepare, time='09:05')
    run_weekly(rebalance, weekday=g.weekday, time='09:30')
    run_daily(check_limit_up, time=g.exit_time)


def prepare(context):
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
    df = context.data.query(
        SQL, sd=d, listed=g.listed_days,
        ssg=g.slice_sg, sms=g.slice_ms, speg=g.slice_peg,
        msnorm='TRUE' if g.ms_norm else 'FALSE')
    cand = df['jq_code'].tolist()
    if not cand:
        return

    # 顺序与聚宽 weekly_adjustment 一致：并集 -> 可交易 -> 黑名单 -> 最后截断
    cand = context.tradable(cand, 'buy')
    if g.hold_history:
        recent = context.data.had_limit_up(
            cand, context.data.nth_prev_day(context.current_date, g.limit_days), d)
        seen = set().union(*g.hold_history)
        cand = [c for c in cand if c not in (seen & recent)]
    target = cand[:g.stock_num]

    for code in list(context.portfolio.positions):
        if code not in target and code not in g.high_limit:
            order_target_value(code, 0)

    need = [c for c in target if c not in context.portfolio.positions]
    need = need[:max(0, len(target) - len(context.portfolio.positions))]
    if need:
        per = context.portfolio.cash / len(need)
        for code in need:
            order_target_value(code, per)


def check_limit_up(context):
    if not g.limit_up_exit or not g.high_limit:
        return
    codes = [c for c in g.high_limit if c in context.portfolio.positions]
    if not codes:
        return
    cur = context.current(codes)
    for code in codes:
        dd = cur.get(code)
        if dd is None:
            continue
        sealed = dd.get('limit_up') if 'limit_up' in dd else dd.get('open_limit_up')
        if not sealed:
            order_target_value(code, 0)
