#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ETF 轮动策略的共享层：时点池 + 多周期动量打分。**本文件不是策略**。

移植自 `AlphaMiner/src/alpha_miner/strategies/etf/`（`_momentum_utils.py` +
`universe/etf_universe.py` 的 `eligible`），规格见
`AlphaMiner/docs/strategies/etf/DESIGN.md` §2~§3。

## 为什么抽成一个共享文件

`etf_trend_momentum` 与 `etf_dual_momentum` 的**选池与打分完全相同**，
只有"要不要持有风险资产"的闸门不同。各写一份的话，改了打分口径却漏改另一边
**不报错**，只是两个策略从此对同一天给出不同的候选池（同 froec 的 `blacklist`
被调用两次那条教训）。

## 🔴 一条 SQL 同时算完「时点池 + 动量」，而不是先取池再逐只取价

AlphaMiner 原版是 `eligible()` 取池 -> `load_closes()` 拉宽表 -> pandas 算分。
这里合成一条：**三百多个调仓日 × 每日几百只**，来回搬运的开销远大于多算几列。
口径一字不差地保留：

  时点池（全部只用 <= sd 的数据）
    ① 累计交易日 >= min_listed_days      —— 上市够久
    ② 最后一根 K 线在 [sd-gap, sd] 内     —— 当时仍在交易（不是今天还在！）
    ③ 近 liq_window 日均成交额 >= min_amount —— 用【当时】的流动性

  动量
    score = Σ_w (P0 / P_w − 1)，可选 ÷ 近 vol_window 日收益率标准差

## 🔴 坏数据守卫：窗口内单日 |收益| > 50% 的标的整只剔除

tdx 的 ETF 行情出过两次**集体 10 倍错位**（2026-05-25 修好后，2026-09-01 又来，
1566 只真 ETF 中招，实测见 `datalake/build/build_etf_lake.py` 文件头）。
这类坏数据不剔除的话，那只票会因为"一天跌 90%"而在动量榜上垫底、或反弹时冲顶，
**而回测不会报错**。AlphaMiner 原版同款规则（`MAX_DAILY_MOVE = 0.5`）。
★ 守卫看的是**窗口内**，所以它对"回测区间恰好跨过损坏日"也有效 ——
  不能只靠"把 end 截在损坏日之前"，那是靠人记住。

## 🔴 PIT：`sd` 一律传 `context.previous_date`

`context.data.query()` 把 `{panel}` 展开成 `date < 当前回测日` 的子查询
（`guard.py` 的 `_panel_expr`，与相位无关）。调仓挂在 09:30（OPEN 相位），
按 T-1 收盘选股、T 日开盘成交 —— 与 DESIGN.md §4「信号用 T 日收盘计算，
T+1 成交」一致。
"""

# 🔴 AlphaMiner 的「真 ETF」代码段（taxonomy.py 的 ETF_CODE，再去掉已核实的例外）。
#   lake 现在存的是**原始全集**（沪市 5 开头 + 深市 15/16 开头，含 LOF 与分级），
#   所以标的池规则必须写在这里 —— 不写就会把 LOF、分级基金、2005~07 的权证
#   一起当成 ETF 打分（实测混进 288 只分级基金，它们是 2 倍杠杆且已强制折算退市）。
# ★ 与 lake 层那份判据的分工：lake 只答"这是不是场内基金代码段"，
#   "哪些算真 ETF"是策略的规则（同 CLAUDE.md「feed 管取数、策略管筛选」）。
UNIV_OK = """(
     (substr(jq_code, 8) = 'XSHG' AND (
          substr(jq_code, 1, 3) BETWEEN '510' AND '518'
       OR substr(jq_code, 1, 2) IN ('52', '53', '56')
       OR substr(jq_code, 1, 3) IN ('588', '589')))
  OR (substr(jq_code, 8) = 'XSHE' AND substr(jq_code, 1, 3) IN ('158', '159'))
)"""


# 一条 SQL 出「候选池 + 打分所需的全部中间量」。
# `{p_terms}` 由 `score_sql()` 按动量窗口生成。
POOL_SQL = """
-- ETF 时点池 + 多周期动量（口径见 _etf_core.py 文件头）
WITH win AS (
  SELECT jq_code, date, close_hfq, amount,
         row_number() OVER (PARTITION BY jq_code ORDER BY date DESC) AS rn
  FROM {panel}
  WHERE date <= DATE '{sd}' AND date > DATE '{sd}' - INTERVAL {lb} DAY
    AND {univ}
), ret AS (
  -- rn 升序 = 时间倒序，所以 lead(rn) 是"再往前一天"
  SELECT w.*, close_hfq / nullif(
           lead(close_hfq) OVER (PARTITION BY jq_code ORDER BY rn), 0) - 1 AS r
  FROM win w
), listed AS (
  -- ① 上市够久：**累计交易日**，不能用自然日推
  SELECT jq_code, count(*) AS nd FROM {panel}
  WHERE date <= DATE '{sd}' AND {univ} GROUP BY 1
), agg AS (
  SELECT r.jq_code,
         max(CASE WHEN rn = 1 THEN close_hfq END) AS p0,
         max(CASE WHEN rn = 1 THEN date END)      AS last_seen,
         {p_terms}
         stddev_samp(CASE WHEN rn <= {volw} THEN r END) AS vol,
         -- 坏数据守卫（见文件头）
         max(abs(r))                                    AS max_move,
         avg(CASE WHEN date > DATE '{sd}' - INTERVAL {liqw} DAY
                  THEN amount END)                      AS avg_amt
  FROM ret r GROUP BY 1
)
SELECT a.jq_code, a.p0, a.vol, a.avg_amt, {p_cols}
FROM agg a JOIN listed l ON l.jq_code = a.jq_code
WHERE l.nd >= {min_listed}
  AND a.last_seen > DATE '{sd}' - INTERVAL {gap} DAY      -- ② 当时仍在交易
  AND a.avg_amt >= {min_amount}                           -- ③ 当时的流动性
  AND a.max_move <= {max_move}                            -- 坏数据守卫
  AND a.p0 > 0
  -- 类现金标的过滤（年化波动下限）。**默认 0 = 关**，保持与 AlphaMiner
  -- 原规格一致；P1 那套用的是 0.03。见 `min_vol_ann` 的说明。
  AND (({min_vol}) <= 0 OR a.vol * sqrt(244) >= ({min_vol}))
"""


def candidates(context, sd, windows, *, min_listed=60, min_amount=5e7,
               liq_window=90, gap=10, vol_window=60, max_move=0.5,
               min_vol_ann=0.0):
    """返回 DataFrame：jq_code / p0 / vol / avg_amt / p_<w> 各一列。

    🔴 `min_vol_ann`（年化波动下限，默认 **0 = 关**）是这两套策略与 P1 的
      关键差异。AlphaMiner 的 trend_momentum / dual_momentum **没有**类现金
      过滤，于是货币 ETF（年化波动 ~0.3%）与股票 ETF 同池竞争；而打分里一旦
      开 `risk_adjusted`（除以波动率），分母趋近 0 会把它们的得分顶到 100+，
      横截面排名永远是它们 —— 实测 trend_momentum 927 个持仓日里 760 天
      拿着银华日利 ETF。
    ★ P1 那套用 `MIN_VOL_ANN = 0.03` + 名称剔「货币/债」把它们挡在池外，
      这正是 P1 能跑出 17~19% 而这两套只有 8.8% / −2.9% 的主要原因之一。
    ★ 默认保持 0 是为了**忠实于原规格**；要看"挡掉类现金之后是什么样"，
      传 `--param min_vol_ann=0.03`。
    """
    mw = max(windows)
    # 回看要够长：最长动量窗口 + 波动窗口，再乘 1.6 折成自然日（约 1.45 已足够，
    # 留余量给长假）。取不够的票会在 p_<w> 上得到 NULL，打分时自然出局。
    lb = int((mw + vol_window + 10) * 1.6)
    p_terms = ''.join(
        "max(CASE WHEN rn = %d THEN close_hfq END) AS p_%d,\n         " % (w + 1, w)
        for w in windows)
    p_cols = ', '.join('a.p_%d' % w for w in windows)
    return context.data.query(
        POOL_SQL, sd=sd, lb=lb, volw=vol_window, liqw=liq_window, univ=UNIV_OK,
        p_terms=p_terms, p_cols=p_cols, min_listed=int(min_listed),
        min_vol=float(min_vol_ann),
        gap=int(gap), min_amount=float(min_amount), max_move=float(max_move))


def score(df, windows, risk_adjusted):
    """多周期收益率等权相加；risk_adjusted 时按波动率归一。返回 {code: score}。"""
    out = {}
    for row in df.itertuples(index=False):
        s, ok = 0.0, True
        for w in windows:
            pw = getattr(row, 'p_%d' % w)
            if pw is None or pw != pw or pw <= 0:      # NULL / NaN -> 数据不够
                ok = False
                break
            s += row.p0 / pw - 1.0
        if not ok:
            continue
        if risk_adjusted:
            v = row.vol
            if v is None or v != v or v <= 0:
                continue
            s /= v
        out[row.jq_code] = s
    return out


def trend_long(context, sd, bench, ma):
    """趋势闸门：基准收盘 > MA(ma) 为多头。数据不足一律判空仓（保守）。"""
    df = context.data.query("""
        SELECT close_hfq FROM {panel}
        WHERE jq_code = '{bench}' AND date <= DATE '{sd}'
        ORDER BY date DESC LIMIT {ma}
    """, sd=sd, bench=bench, ma=int(ma))
    if len(df) < ma:
        return False
    return float(df['close_hfq'].iloc[0]) > float(df['close_hfq'].mean())


def place(context, picks, top_n):
    """等权下单。**权重分母固定 top_n**，不足则总仓位 < 1（缺口留现金）。

    🔴 与 AlphaMiner 原版一致：分母写成 len(picks) 会在"只选出 2 只"时
      把仓位顶到 100%，那是另一条规则（集中度暴增），不是同一个策略。
    """
    keep = set(picks)
    for code in list(context.portfolio.positions):
        if code not in keep:
            order_target_value(code, 0)                      # noqa: F821
    if not picks:
        return
    per = context.portfolio.total_value / float(top_n)
    for code in picks:
        order_target_value(code, per)                        # noqa: F821
