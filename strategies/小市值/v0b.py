#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SG-MS-PEG-HL 线的 v0b：预过滤后按流通市值升序取 N 只（因子层消融基准）。

⚠️ **文件名是版本号，不是策略名。** `SG-MS-PEG-HL` 才是策略线的名字，
   `v0b` 是这条线里的一个版本 —— 而且是把三条因子支线【整个删掉】的消融
   基准。本地**没有实现 SG-MS-PEG-HL 本体**，三条支线一条都没做：

     SG  sales_growth（5 年营业收入增长率）取最高 10%
     MS  四因子复合成长打分，取前 10%
         0.10×营业收入增长率 + 0.35×利润总额增长率
       + 0.15×净利润增长率   + 0.40×earnings_growth(5年盈利增长)
     PEG PEG 最低 0~0.2 分位，再叠 turnover_volatility 最低 50%
     HL  HighLimit：涨停留仓 / 炸板离场（这一层本文件【有】）

   三路各自按流通市值升序取若干只，取并集（去重后约 13~15 只）再截到 10。

## 为什么搬的是 v0b 而不是本体 —— JQ 自己已经量过因子层值多少

| 对比 | 前段 | 后段 | 全区间 |
|---|---|---|---|
| 因子层整体（v3d − v0b） | **+22.2pp** | **+1.9pp** | +9.3pp ⚠️ 几乎消失 |

JQ 该线版本谱（其区间约 7.3 年、基准年化 11.2%，**不可与 FROEC 线跨线比**）：
    原始基线 51.89% | v2 48.52% | v3a 48.88% | v3b 49.70%
    v3c 49.70% | v3d 48.27% | v4 待跑
    消融：去 MS 37.29% | 去 SG 41.87% | 去 PEG 47.22% | 三路全删 = v0a/v0b

两条 JQ 记录下来的判断：
  · 2024 上半年崩盘 v0b **-0.2%** vs v3d **-15.3%**
    -> 「因子层让崩盘更糟，不是更好」
  · v0b 在最近 4 个时段里赢 3 个 -> 「不是一次事件，是持续性的落后」
  · v0a -> v0b（只加 eps>0）：年化 +1.14pp、回撤 -1.33pp，三项同向改善

即：这条线的因子层价值几乎全在 2019-2021，近年归零。所以 v0b 才是这条线里
值得跟踪的那一版 —— 但**文件名应该反映策略而不是版本号**（待改名）。

另有 strategies/小市值/mincap.py 是更彻底的零机制基准（连 eps>0 / 次新 /
候选截断 / 20 日黑名单 / 涨停留仓都没有），用于量出本文件那堆机制值多少：
同窗口同成本 mincap 16.31% vs 本文件 31.85%。

对标聚宽 SG-MS-PEG-HL-v0b：2019-01-01~2026-06-30 年化 38.98%、最大回撤 51.86%。

🔴 同口径下我们【低于】聚宽 2.8pp，尚未归因：
      本地(全修) 36.14% / 回撤 52.79%    聚宽 38.98% / 51.86%
   ⚠️ 此前 docstring 隐含的"很接近"是【口径错配】造成的假象：
      拿滑点 0 的 38.11% 去比聚宽含滑点 0.0015 的 38.98%，看着只差 0.87pp。
      按同一口径实际差 -2.84pp。selftest 的模块级 JQ 字典是 FROEC 的口径
      (FixedSlippage(0))，跑 v0b 必须改成 slippage=0.0015。

逐步修复拆解（2016-01-01~2025-12-31，本金 100 万，聚宽 v0b 成本口径）：
      基线(三个开关全 0，复现修复前)   32.56% / 回撤 52.62% / 夏普 1.08
      ① 只修宇宙(含停牌股)            31.18% / 50.94% / 1.04   -1.38pp
      ② 只修科创板(保留 689 CDR)      32.56% / 52.62% / 1.08    0.00pp
      ③ 只修次新边界(>=375)           32.77% / 51.92% / 1.08   +0.21pp
      ④ 全修（默认）                  30.95% / 52.62% / 1.04   -1.61pp
   ★ ② 对本策略【完全零影响】(数字一字不差)，而同样的修正在 FROEC 上值
     -1.67pp —— 结构性区别：FROEC 有 floor() 分位截断，宇宙少一只票就推移
     边界；v0b 是绝对 top-N 排序，池子外的票不影响结果。689009 流通市值
     2.2~40.7 亿，永远进不了最小 15 只。
   ★ ①+③ 相加是 -1.17pp 而全修是 -1.61pp，差 0.44pp 是交互项，不线性相加。
   ★ 修复量级【期间依赖】：2016-2025 是 -1.61pp，2019-2026.06 只有 -0.20pp。

★ 成本口径：聚宽原版【自己设了滑点 0.0015】(PriceRelatedSlippage，双边)
  + 佣金万3 + 最低 5 元 + 印花税千一固定，type='stock'。
  所以对标本策略【不要用 --jq-cost】（那会把滑点设成 0，是 FROEC 的口径）：
      --slippage 0.0015 --commission 0.0003 --min-commission 5 --stamp-tax 0.001
  已验证成交价语义一致：首个调仓日 (2019-01-02) 10 只逐一吻合，
  聚宽交易详情里的成交价 = 当日开盘价 x (1 + 0.0015/2)，例如
  300417 开盘 15.23 x 1.00075 = 15.24 = 聚宽显示价。10/10 精确命中，
  说明开盘价数据与滑点方向（买 +s/2）两边都对得上。

★ 策略里【没有】任何涨跌停 / T+1 / 停牌 / 整手 / 税费的代码 —— 全部由 broker
  负责。旧引擎把涨停过滤写在选股里，那是错的位置：既重复了撮合职责，
  又因为只做了买入侧、漏了卖出侧而静默失真。
"""
from assay.api import *          # noqa: F401,F403

# 候选宇宙。★ 与 froec.py 同源的四条修正，每条一个单变量开关（见 initialize）：
#   1) 宇宙取【权威在册表】而不是面板当日行 —— 面板是 K 线驱动的，
#      停牌股当日无 K 线即无行，会整体缺席候选池。聚宽
#      get_all_securities() 含停牌股：它们会占掉 candidate_num 个候选名额，
#      之后才被 filter_paused_stock 删掉。
#   2) 科创板按 688% 排除（聚宽是 stock[0:3] != '688'，【保留】689 开头的
#      科创板 CDR）。原实现按 tdx symbol 'sh68%' 排，把 689009 也排掉了。
#   3) 次新按 >= {listed} 日历日（聚宽：not yesterday - start_date < 375 天）。
#      原实现写 > 375，差一天。
#   4) eps 取 fin_indicator_q（聚宽 indicator.eps）按【决策日】as-of，不从面板
#      结转 —— 面板 eps_q 的值与 indicator.eps 完全一致（实测 2015-12-31
#      2512 只有行情票 100.00% 相同，名字带 _q 但不是单季），所以对有行情的
#      票没差别；差别只在停牌股：结转会拿到该股【最后交易日】那天的基本面，
#      可能是过期报告。聚宽 get_fundamentals(q, date=yesterday) 无论停牌与否
#      都给决策日已披露的最新报告。
#
# floatmv > 0 是必需的护栏：300114.XSHE 有真实价格与成交量却 floatmv=0，
# 按市值【升序】选股时 0 永远排第一，实测它从 2016 年起 2056 个交易日霸占首位。
SQL = """
WITH univ AS (
  SELECT code FROM read_parquet('{root}/std/security_universe.parquet')
  WHERE sec_type='stock' AND list_date <= DATE '{sd}'
    AND (delist_date IS NULL OR delist_date > DATE '{sd}')
    AND date_diff('day', list_date::DATE, DATE '{sd}') {lop} {listed}
    AND code NOT LIKE '{kcb}'
), today AS (
  SELECT jq_code, floatmv, is_risk_warned
  FROM {panel} WHERE date = DATE '{sd}'
), miss AS (
  -- 当日无 K 线（停牌）：只结转【价格派生量】(floatmv)，与聚宽一致 ——
  -- 停牌股的 valuation 按最后收盘价算。基本面走 eps1，不结转。
  SELECT u.code AS jq_code, p.floatmv, p.is_risk_warned
  FROM (SELECT code, DATE '{sd}' AS d FROM univ
        WHERE code NOT IN (SELECT jq_code FROM today)) u
  ASOF LEFT JOIN (
      SELECT jq_code, date, floatmv, is_risk_warned
      FROM {panel} WHERE date > DATE '{sd}' - INTERVAL 400 DAY
  ) p ON p.jq_code = u.code AND p.date <= u.d
), eps1 AS (
  SELECT code, eps FROM (
    SELECT code, eps, row_number() OVER (PARTITION BY code
             ORDER BY report_date DESC, pub_date DESC) rn
    FROM read_parquet('{root}/std/fin_indicator_q.parquet')
    WHERE pub_date <= DATE '{sd}' AND eps IS NOT NULL
  ) WHERE rn = 1
)
SELECT r.jq_code FROM (
  SELECT * FROM today WHERE jq_code IN (SELECT code FROM univ)
  UNION ALL SELECT * FROM miss WHERE {pin}
) r JOIN eps1 e ON e.code = r.jq_code
WHERE NOT COALESCE(r.is_risk_warned, FALSE)
  AND e.eps > 0 AND r.floatmv > 0
ORDER BY r.floatmv ASC LIMIT {cand}
"""


def initialize(context):
    g.stock_num = getattr(g, 'stock_num', 10)
    g.candidate_num = getattr(g, 'candidate_num', 15)
    g.listed_days = getattr(g, 'listed_days', 375)
    g.limit_days = getattr(g, 'limit_days', 20)
    g.weekday = getattr(g, 'weekday', 1)
    g.exit_time = getattr(g, 'exit_time', '14:00')
    g.limit_up_exit = getattr(g, 'limit_up_exit', 1)
    # ---- 四个单变量开关：1 = 修正后（与聚宽一致），0 = 复现修复前的行为 ----
    #   ★ 保留 0 的取值不是为了"可以选"，是为了能【逐条量出每个 bug 值多少】。
    #     不能一次改四处再看总差 —— 那样无法归因，也无法发现互相抵消。
    g.paused_in_pool = getattr(g, 'paused_in_pool', 1)   # 宇宙含停牌股
    g.kcb_688_only = getattr(g, 'kcb_688_only', 1)       # 只排 688（保留 689 CDR）
    g.listed_ge = getattr(g, 'listed_ge', 1)             # >= 375 而非 > 375
    g.hold_history = []          # 最近 N 日持仓并集，配合「涨停过」构成黑名单
    g.high_limit = set()         # 昨收涨停的持仓：调仓不卖，14:00 再看是否打开

    set_benchmark('000905.XSHG')      # 与聚宽原版一致：中证 500

    run_daily(prepare, time='09:05')
    run_weekly(rebalance, weekday=g.weekday, time='09:30')
    run_daily(check_limit_up, time=g.exit_time)


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
    df = context.data.query(
        SQL, sd=d, listed=g.listed_days, cand=g.candidate_num,
        lop='>=' if g.listed_ge else '>',
        kcb='688%' if g.kcb_688_only else '68%',
        pin='TRUE' if g.paused_in_pool else 'FALSE')
    cand = df['jq_code'].tolist()
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
    if not g.limit_up_exit or not g.high_limit:
        return
    codes = [c for c in g.high_limit if c in context.portfolio.positions]
    if not codes:
        return
    # 今天的状态走 context.current() —— 历史接口取不到今天（PIT 防火墙）。
    # 14:00 属 INTRADAY 阶段，limit_up 可见（与「盘中成交价用收盘价代理」的约定一致）。
    cur = context.current(codes)
    for code in codes:
        dd = cur.get(code)
        if dd is None:
            continue
        # ★ 按相位取字段：OPEN 阶段没有 limit_up（那是收盘派生量），
        #   直接取会得 None -> `not None` 为真 -> 把持仓全卖掉。
        sealed = dd.get('limit_up') if 'limit_up' in dd else dd.get('open_limit_up')
        if not sealed:
            order_target_value(code, 0)
