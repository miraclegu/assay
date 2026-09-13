#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""399101(中小板综) 日频最小市值 5 选 5 —— 用户原始需求（逐条对应）：

    1. 股票池锁定 399101.XSHE（中小板指数）成分股。
    2. 每个交易日 14:40 运行一次：按流通市值从小到大排序，取前 15 只
       （buy_stock_count3），依次过滤掉停牌、涨停、跌停、ST/*ST/退市的股票，
       剩下的取前 5 只作为目标持仓。
    3. 卖出不在目标名单里的持仓，把现金平均分给目标股票里还没买的，
       等权重买入，直到凑够 5 只。
    4. 没有任何止损、止盈、择时或季节性空仓逻辑 —— 纯粹是"每天选最小市值
       的 5 只，涨跌不管"。

## 399101 成分股：现在用【真实成分】（2026-09-13 换的）

`std/index_member.parquet` 的 `399101.XSHE`，**周频**采样（1061 个时点，
2005-12 起），来自 `raw/jq/_ingest/extract_jq_index_members.py`。

此前本地没有这份数据，只能用「代码前缀 002」做静态代理。换掉之后实测两者差多少：

    2016-06-30  真实 790 / 代理 791   漏 0  多 1   重合 99.9%
    2020-12-31  真实 990 / 代理 965   漏 29 多 4   重合 96.7%
    2026-08-31  真实 958 / 代理 919   漏 39 多 0   重合 95.9%

🔴 **代理漏掉的是 003 前缀那批** —— 深交所 2021-04 中小板并入主板后，新股
改用 001/003 段，但仍算进中小板综。2016 年几乎无误，2021 年之后稳定漏 39 只。

★ 采样是**周频**，所以成分区间的边界有 ~1 周的分辨率，不是公告驱动的精确
  生效日。对日频回测来说这个误差远小于本策略自身的扰动敏感度（见下）。

## 🔴🔴 换成真实成分后年化掉了 8.4pp —— 这【不能】读成"真实数据更差"

    代理（002 前缀）  年化 35.94%  夏普 1.13  平均仓位 96.0%  低仓位天数 12.6%
    真实成分          年化 27.54%  夏普 0.92  平均仓位 98.4%  低仓位天数  4.8%

两版对照（同期同参数）：**交易过的票重合 81%**（111 vs 115 只），
**收益最高的前 4 笔完全相同**（002830 +94.5% / 002718 +45% / 002836 +43.4% /
002830 +40.2%）。真实版第 5 名是 `003023.XSHE` +39.4% —— 正是代理漏掉的
那批 003 票，说明新数据确实把它们纳进来了。

🔴 所以差异**不是"漏掉了某几只暴涨股"**，是底部 5 只的人选被整体扰动了。
  CLAUDE.md 记着 froec 的同源现象：「收益极度集中在最小的几只，任何让底部
  5 只换人的扰动都有巨大杠杆」—— 那里**剔掉 0.07%~0.22% 的宇宙**就能让年化
  摆动 ±3pp（13 组 salt 实测）。这里换掉的是**约 4% 的宇宙**，扰动量级大
  一两个数量级，8.4pp 的摆动完全在这个机制的解释范围内。
★ **哪个数更高不是采用理由**：真实成分版按构造就是对的，代理版只是在没有
  数据时的近似。两个数字都该读作"一个很宽的分布里的一次抽样"。

## 🔴 架构约束：14:40 能看到"今天"的字段，只有停牌/涨停/跌停，不含市值

`context.data.query()`（取 floatmv/ST 状态用的接口）对 `{panel}` 的展开是
**`WHERE date < 当前回测日`，与运行时刻(相位)无关**（`assay/guard.py`
`GuardedFeed._panel_expr`）——不像 `context.current()` 那样按 OPEN/INTRADAY/
CLOSE 分层放开。也就是说哪怕策略挂在 14:40（INTRADAY 相位）跑，
**流通市值排名依然只能用【前一交易日】收盘算出的 floatmv**（与
froec.py / mincap.py 完全一致的约束，不是本文件独有）。

★ 能在 14:40 看到"今天"的，只有 `context.current(codes)` 给的、按 Bar
  派生的字段（open/close/limit_up/limit_down/…），这正是本文件用来判定
  "今天有没有停牌 / 涨停 / 跌停"的入口 —— 与用户要求的"依次过滤"里
  第①②③条时间点严格对齐（今天下午 2:40 的真实盘口状态）。
  第④条 ST/*ST/退市 只能用【昨收】数据判（`is_risk_warned`，架构上没有
  更晚的入口），与froec/mincap 同一处理。

## 排序细节：先截 15、按当日盘口过滤、再截 5（不补位）

`买入 15 只候选` 是【先按昨收流通市值升序截断】，"过滤"发生在截断之后 ——
如果这一天恰好有几只在候选 15 里停牌/涨停/跌停，剩下的候选可能不足 15，
最终目标也可能不足 5 只（不会去找第 16、17 名来补位）。
这是对用户"取前 15 只，依次过滤，剩下的取前 5"这句话【最字面】的实现：
不做 froec/mincap 那种"放宽候选池再补位"的变体。

## ⚠️ 已知风险（mincap.py 实测过的同类坑，这里没有关掉）

`g.listed_days` 默认 **0**（不过滤次新股），照用户字面要求。
`strategies/小市值/mincap.py` 的 docstring 记录过：不过滤次新时，
2016~2017 年小市值榜单会被刚上市、流通盘极小的次新股霸占，
它们频繁"开盘涨停买不进"，导致平均仓位跌到 33%~36%（引擎会打
"平均仓位过低"告警）。本策略每天都重新排一次（不是每周），
这个风险只会更明显，不会更轻。若回测报告出现该告警，把
`--param listed_days=250` 这类参数加上即可验证是不是同一个成因 ——
默认值保持 0 是为了先给出对用户原始描述【最忠实】的一版结果。

## ✅ 已实测：上面那个风险担心的没有发生（2026-09-13，2016-01-04~2026-09-11）

    期末权益 13,302,089（本金 50 万）  年化 35.94%  最大回撤 47.83%
    夏普 1.13  年换手 18.10 次/年  平均持有 16.8 天  平仓 1166 笔 胜率 59.52%
    平均仓位 96.0%  最低 59.1%  仓位<80% 的日子 12.6%  —— 引擎【没有】打
    "平均仓位过低"告警（阈值 <90% 才提示 ⚠，<70% 才是 ⚠⚠，见 metrics.py）
    拒单 1087 笔：停牌/无行情 521、现金不足 427（正常，daily 换手高）、
    收盘跌停无对手盘 78、资金不足一手 60
    基准 399101.XSHE 全期只涨 5.32%（年化 0.49%），超额年化 35.28%

★ 推测原因：mincap.py 的次新霸榜问题在**全市场**候选池上更严重，
  而本策略候选池限定在 002 前缀 —— 深交所 2021-04 中小板并入主板后
  **再没有发过新的 002 开头代码**（新股改用 001/003 段），所以这个
  子宇宙里"次新股扎堆"的窗口只存在于 2016~2021 这前 5 年多，
  之后天然没有次新可言，稀释了整个 10.7 年样本上的影响。
  2016~2017 那两年有没有被次新拖累没有单独拆出来看（本次没有再跑
  逐年细分，需要的话可以补）。

## 换手 / 成本提示

调仓频率是【每个交易日】，且不做"已持仓在目标里就不动"之外的任何平滑 ——
只要昨收市值榜单换了一位、或今天有人涨跌停/停牌，目标名单就可能变，
换手会显著高于 froec/红利那类周频策略。本文件没有对标源，成本一律用
【引擎默认】（滑点 0.0015 双边、佣金万 2.5、最低 5 元）——不要传 --jq-cost，
那是对标聚宽用的口径，用在这里会系统性抬高收益。
"""
from assay.api import *          # noqa: F401,F403

SQL = """
-- 399101(中小板综) 候选池代理：本地无该指数成分股名单，
-- 用 SZSE 中小板历史代码段 002xxx 做静态近似（见文件头说明）。
-- 停牌股结转【前一交易日】的流通市值，与聚宽 valuation 停牌处理一致。
WITH univ AS (
  -- 🔴 **真实成分**（2026-09-13 起）。此前本地没有 399101 的成分数据，
  --   只能用「代码前缀 002」做静态代理 —— 实测那个代理 2016 年几乎无误
  --   (99.9%)，但 2021 年深交所中小板并入主板后**稳定漏掉 39 只 003 前缀**
  --   的票（重合度掉到 96%）。现在走 std/index_member。
  -- ★ 仍然要叠 security_universe 的在册判断：成分表里可能有本地没有行情的
  --   票（北交所），而它们在面板里一行都没有。
  SELECT m.stock_code AS code FROM {t_index_member} m
  JOIN {t_universe} u ON u.code = m.stock_code
  WHERE m.index_code = '399101.XSHE'
    AND m.valid_from <= DATE '{sd}'
    AND (m.valid_to IS NULL OR m.valid_to >= DATE '{sd}')
    AND u.sec_type = 'stock' AND u.list_date <= DATE '{sd}'
    AND (u.delist_date IS NULL OR u.delist_date > DATE '{sd}')
    AND date_diff('day', u.list_date::DATE, DATE '{sd}') >= {listed}
), today AS (
  SELECT jq_code, floatmv, is_risk_warned FROM {panel} WHERE date = DATE '{sd}'
), miss AS (
  SELECT u.code AS jq_code, p.floatmv, p.is_risk_warned
  FROM (SELECT code, DATE '{sd}' AS d FROM univ
        WHERE code NOT IN (SELECT jq_code FROM today)) u
  ASOF LEFT JOIN (
      SELECT jq_code, date, floatmv, is_risk_warned
      FROM {panel} WHERE date > DATE '{sd}' - INTERVAL 400 DAY
  ) p ON p.jq_code = u.code AND p.date <= u.d
)
-- ST/*ST/退市（is_risk_warned）只能用【昨收】数据判 —— 见文件头架构说明。
SELECT jq_code FROM (
  SELECT * FROM today WHERE jq_code IN (SELECT code FROM univ)
  UNION ALL SELECT * FROM miss
)
WHERE NOT COALESCE(is_risk_warned, FALSE) AND floatmv > 0
ORDER BY floatmv ASC LIMIT {cand}
"""


def initialize(context):
    # 用户原话的参数名："取前 15 只（buy_stock_count3）"
    g.buy_stock_count3 = getattr(g, 'buy_stock_count3', 15)
    g.stock_num = getattr(g, 'stock_num', 5)
    if g.buy_stock_count3 < g.stock_num:
        raise ValueError('buy_stock_count3(%d) 不能小于 stock_num(%d)'
                         % (g.buy_stock_count3, g.stock_num))
    # 0 = 不过滤次新（字面实现用户要求）。见文件头「已知风险」。
    g.listed_days = getattr(g, 'listed_days', 0)

    set_benchmark('399101.XSHE')      # 本地有点位数据（tdx sz399101，2005-06 起）

    run_daily(rebalance, time='14:40')


def rebalance(context):
    # 🔴 只能用昨收数据排名 —— 14:40 这个时刻在【面板查询接口】里
    #   仍然只放开到「严格早于今天」，与相位无关（见文件头架构说明）。
    d = context.previous_date
    if d is None:
        return
    df = context.data.query(SQL, sd=d, listed=g.listed_days,
                            cand=g.buy_stock_count3)
    # 已经 ORDER BY floatmv ASC LIMIT buy_stock_count3 —— 这就是「取前 15 只」。
    cand = df['jq_code'].tolist()
    if not cand:
        return

    # 依次过滤：停牌 -> 涨停 -> 跌停。
    # 用【今天 14:40】这一时点已知的状态（INTRADAY 相位下 context.current()
    # 全部字段可见，是"盘中成交价用收盘价代理"这条既有约定的自然延伸——
    # 见 guard.py/engine.py 的注释，与 froec.py 的 check_limit_up 同一处理）。
    # ST/*ST/退市已经在上面的 SQL 里用【昨收】数据剔除过了（架构限制，
    # 14:40 这个入口拿不到"今天"的 ST 状态，见文件头说明）。
    cur = context.current(cand)

    def _tradable_today(code):
        b = cur.get(code)
        if b is None:
            return False              # 停牌：今天没有 bar
        if b.get('limit_up'):
            return False              # 涨停
        if b.get('limit_down'):
            return False              # 跌停
        return True

    cand = [c for c in cand if _tradable_today(c)]

    # 剩下的取前 stock_num(5) 只 —— 不补位（不足 5 只就是不足 5 只）。
    target = cand[:g.stock_num]
    if not target:
        return

    # 卖出不在目标名单里的持仓。
    for code in list(context.portfolio.positions):
        if code not in target:
            order_target_value(code, 0)

    # 现金平均分给目标股票里还没买的，等权重买入，直到凑够 stock_num 只。
    need = [c for c in target if c not in context.portfolio.positions]
    if need:
        per = context.portfolio.cash / len(need)
        for code in need:
            order_target_value(code, per)
