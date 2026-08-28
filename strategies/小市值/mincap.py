#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""纯小市值基准：排除 ST / 退市整理，等权买入流通市值最小的 N 只。

这是整条小市值线的【零机制基准】。v0b 与 FROEC 在它之上叠了一堆东西 ——
eps>0 过滤、次新 375 天、科创板过滤、候选 15 截 10、20 日「持有过且涨停过」
黑名单、昨收涨停留仓、14:00 炸板离场。本文件把这些【全部去掉】，只留：

    非 ST / 非退市整理  ->  流通市值升序  ->  取最小 N 只  ->  等权

用途是量出「那堆机制合计值多少」。单独看 v0b 的年化说明不了机制有没有用，
必须有一个不含机制的同口径参照物。

★ 「排除 ST / 待退市」用 is_risk_warned，它复刻聚宽 filter_st_stock 的【四条】
  检查（is_st 或名称含 ST / * / 退），不只是 is_st。这一条对小市值策略是
  必需而非可选：实测 149 只【进入退市整理期】的票 is_st 原为 0，而它们价格
  已崩塌、流通市值极小，恰好排在市值升序最前 —— 不滤掉就是暴露最大化。

★ 候选宇宙取【权威在册表】+ 停牌股价格结转，不用面板当日行。面板是 K 线
  驱动的，停牌股当日无 K 线即无行、会整体缺席横截面（2015-12-31 实测面板
  2542 行 vs 权威在市 2811 只，缺的 267 只≈当日停牌 266 只）。停牌股买不到，
  但它【确实是当日市值最小的十只之一】—— 让它占掉名额、被 broker 拒单，
  比假装它不存在、悄悄换成第 11 名更诚实。想要补位就把 candidate_num 调大。

★ 策略里【没有】涨跌停 / T+1 / 停牌 / 整手 / 税费的代码，全部由 broker 负责。

成本口径：本策略没有对标源，内部比较一律用【引擎默认】（滑点 0.0015 双边）。
  不要用 --jq-cost —— 那是 FROEC 的口径（FixedSlippage(0)），拿它跑内部
  比较会系统性抬高收益。本项目已因此三次得出错误结论。

## 实测（2016-01-01~2025-12-31，本金 100 万，引擎默认成本）

🔴 **字面"纯"版（candidate_num=stock_num，不补位、不过滤次新）不可用**：
   年化 18.77% / 回撤 45.60%，但 **平均仓位仅 81.8%、仓位<80% 的日子 19.3%**，
   引擎已打低仓位告警 —— 这个数字受污染，不能当基准。
   成因（已定位到逐日）：**2016-2017 平均只持 3.4 / 3.6 只，仓位 33% / 36%**。
   新股只有约 25% 流通，上市初期流通市值极小，**必然霸占"最小市值"榜首**
   —— 2016-03-04 那天最小 12 只里前 10 只全是次新（上市 1~315 天）。
   而 2016-2017 是 IPO 高峰 + 次新连续涨停期，「开盘涨停，买不进」拒单
   2016 年 289 笔、2017 年 337 笔，占全期涨停拒单的 90%。
   -> v0b/FROEC 的 375 天 / 250 天次新过滤不是随意设的，是**必需**的。

✅ **可用基准：candidate_num=30（补位）** —— 年化 **16.31%** / 回撤 46.56% /
   夏普 0.64，平均仓位 94.7%、仓位<80% 的日子 **0.0%**，无告警。

⚠️ 次新阈值扫描（均 candidate_num=30，仓位都 ~95% 干净）**非单调、极差 9.09pp**：
       0天 16.31%   120天 14.38%   250天 9.03%   375天 17.23%
     500天 18.12%   730天 15.27%
   125 天的阈值变化摆动 8.2pp —— 与 FROEC 的刀刃效应同源：结果由「底部 10 只
   具体是哪几只」支配，任何单个数字都是一个 9pp 宽分布里的一次抽样。

## 与叠了机制的版本对照（同窗口、同成本）

| 策略 | 年化 | 回撤 | 夏普 | 平均仓位 |
|---|---|---|---|---|
| 本文件（纯基准，候选30） | 16.31% | 46.56% | 0.64 | 94.7% |
| v0b（全套机制） | **31.85%** | 52.60% | 1.06 | 97.7% |
| froec（+PB+ROE增速） | **33.11%** | 47.72% | 1.14 | 93.6% |

**机制合计值约 +15.5pp**（纯基准 -> v0b），froec 再加 1.3pp。
即便取次新扫描里最好的一档（18.12%），v0b 仍高出 13.7pp ——
**这个差距大于纯基准自身的 9pp 参数噪声**，所以「那堆机制确实有用」这个
结论是站得住的（这在本项目里少见：多数效应都小于噪声）。
尚未拆解的是 15.5pp 里各层各占多少：eps>0 / 次新 375 / 候选15截10 /
20 日黑名单 / 涨停留仓+炸板离场。
"""
from assay.api import *          # noqa: F401,F403

SQL = """
WITH univ AS (
  SELECT code FROM read_parquet('{root}/std/security_universe.parquet')
  WHERE sec_type='stock' AND list_date <= DATE '{sd}'
    AND (delist_date IS NULL OR delist_date > DATE '{sd}')
    AND date_diff('day', list_date::DATE, DATE '{sd}') >= {listed}
    AND code NOT LIKE '{kcb}'
), today AS (
  SELECT jq_code, floatmv, is_risk_warned FROM {panel} WHERE date = DATE '{sd}'
), miss AS (
  -- 停牌：只结转价格派生量（floatmv 按最后收盘价算），与聚宽 valuation 一致
  SELECT u.code AS jq_code, p.floatmv, p.is_risk_warned
  FROM (SELECT code, DATE '{sd}' AS d FROM univ
        WHERE code NOT IN (SELECT jq_code FROM today)) u
  ASOF LEFT JOIN (
      SELECT jq_code, date, floatmv, is_risk_warned
      FROM {panel} WHERE date > DATE '{sd}' - INTERVAL 400 DAY
  ) p ON p.jq_code = u.code AND p.date <= u.d
)
SELECT jq_code FROM (
  SELECT * FROM today WHERE jq_code IN (SELECT code FROM univ)
  UNION ALL SELECT * FROM miss
)
WHERE NOT COALESCE(is_risk_warned, FALSE) AND floatmv > 0
ORDER BY floatmv ASC LIMIT {cand}
"""


def initialize(context):
    g.stock_num = getattr(g, 'stock_num', 10)
    # ★ 默认 candidate_num == stock_num：就是【最小的 10 只】，买不到就空着，
    #   不用第 11 名替补。这是「等权买入 10 个市值最小的股」的字面实现。
    #   调大它 = 补位变体：先剔掉当日买不进的（停牌/涨停/跌停），再截到
    #   stock_num，所以能填满 10 只。这是【另一条规则】，不再是"纯"基准。
    #   ⚠ 只放宽 candidate_num 而不加过滤是【无效的】：查询后立刻截到
    #     stock_num，多取的候选在任何过滤之前就被丢掉，参数活着但不起作用。
    #     实测过一次：candidate_num=30 与默认给出完全相同的数字。
    g.candidate_num = getattr(g, 'candidate_num', g.stock_num)
    if g.candidate_num < g.stock_num:
        raise ValueError('candidate_num(%d) 不能小于 stock_num(%d)'
                         % (g.candidate_num, g.stock_num))
    # 以下三条默认【不过滤】，保持"纯"。想量某一层值多少就单独打开它。
    g.listed_days = getattr(g, 'listed_days', 0)       # 0 = 不过滤次新
    g.excl_kcb = getattr(g, 'excl_kcb', 1)             # 科创板 688/689
    g.freq = getattr(g, 'freq', 'weekly')              # weekly / monthly
    g.day = getattr(g, 'day', 1)                       # 周几 / 月内第几个交易日
    # 'equal' = 每期把全部持仓拉回等权（教科书等权，换手更高）
    # 'fill'  = 只用现金补齐缺的那几只（聚宽原版做法，不动已有仓位）
    g.rebal_mode = getattr(g, 'rebal_mode', 'equal')

    set_benchmark('000905.XSHG')      # 与同线其它策略一致：中证 500

    if g.freq == 'monthly':
        run_monthly(rebalance, monthday=g.day, time='09:30')
    else:
        run_weekly(rebalance, weekday=g.day, time='09:30')


def rebalance(context):
    d = context.previous_date
    if d is None:
        return
    df = context.data.query(
        SQL, sd=d, listed=g.listed_days, cand=g.candidate_num,
        kcb='688%' if g.excl_kcb else '__never_match__')
    cand = df['jq_code'].tolist()
    # 补位变体才需要过滤：候选多于持仓数时，先剔掉当日买不进的再截断，
    # 否则多取的候选毫无作用（见 initialize 里的说明）。
    if g.candidate_num > g.stock_num:
        cand = context.tradable(cand, 'buy')
    target = cand[:g.stock_num]
    if not target:
        return

    # 先清仓不在目标里的 —— 卖出所得当日即可用于买入，所以顺序必须是先卖后买
    for code in list(context.portfolio.positions):
        if code not in target:
            order_target_value(code, 0)

    if g.rebal_mode == 'equal':
        # ★ 分母用 stock_num 而不是 len(target)：目标就是"10 只各 1/10"。
        #   若某只买不到，剩下的仍是 1/10，差额留现金 —— 不悄悄加杠杆到别的票上。
        per = context.portfolio.total_value / g.stock_num
        for code in target:
            order_target_value(code, per)
    else:
        need = [c for c in target if c not in context.portfolio.positions]
        if need:
            per = context.portfolio.cash / len(need)
            for code in need:
                order_target_value(code, per)
