#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FROEC-PB-CAP-HL：低 PB + 单季 ROE 增长 + 小市值。

对标聚宽实测：2016-01-01~2026-08-07、初始资金 ￥100,000
             年化 36.80%、最大回撤 47.24%。
⚠️ 初始资金必须是 10 万：每仓仅 1 万，整手取整与 5 元最低佣金在这个规模上
   是实质影响，而且会让回测路径变混沌（±0.3pp 不可解释噪声）。

写这个策略验证了引擎的「策略无关性」：它比 v0b 多了三层筛选和一个跨表查询，
但**没有动引擎一行代码** —— 自定义取数走 context.data.query()。
"""
from assay.api import *          # noqa: F401,F403

EXCL_IND = ('钢铁I', '煤炭I', '石油石化I', '采掘I', '银行I', '非银金融I',
            '金融服务I', '交运设备I', '交通运输I', '传媒I', '环保I')

# 5 期单季 ROE 增长 + PB 半区 + ROE 十分位 + 行业过滤 + 市值升序。
# ★ roe 用聚宽权威 indicator.roe（std/fin_indicator_q），不本地推算 ——
#   实测它的分母是【平均净资产 (期初+期末)/2】，本地曾用期末，
#   257,525 条比对只有 41.22% 吻合，选股命中率因此长期卡在 72.1%。
# ★ PB 半区与 ROE 十分位是【向下取整截断】，不是 ntile：
#   聚宽源码是 list(df.code)[:int(0.5*len(df.code))]，
#   ntile 会把余数分给前桶(等价 ceil)，多取一只就会推移下游边界。
SQL = """
WITH q AS (
  SELECT code, roe, row_number() OVER (PARTITION BY code ORDER BY report_date DESC) rn
  FROM read_parquet('{root}/std/fin_indicator_q.parquet')
  WHERE pub_date <= DATE '{sd}' AND roe IS NOT NULL
), roec AS (
  SELECT code, 4*max(CASE WHEN rn=1 THEN roe END)
         - max(CASE WHEN rn=2 THEN roe END) - max(CASE WHEN rn=3 THEN roe END)
         - max(CASE WHEN rn=4 THEN roe END) - max(CASE WHEN rn=5 THEN roe END) AS increase
  FROM q WHERE rn <= 5 GROUP BY 1 HAVING count(*) = 5
), base AS (
  SELECT jq_code, floatmv, pb, sw_l1_name FROM {panel}
  WHERE date = DATE '{sd}' AND NOT is_risk_warned AND listed_days > {listed}
    AND list_date IS NOT NULL AND symbol NOT LIKE 'sh68%'
    AND eps_q > 0 AND pb > 0 AND floatmv > 0
), pb_half AS (
  SELECT * FROM (SELECT *, row_number() OVER (ORDER BY pb ASC) rn,
                        count(*) OVER () AS n FROM base)
  WHERE rn <= floor(0.5 * n)
), roe_top AS (
  SELECT * FROM (
    SELECT b.jq_code, b.floatmv, b.sw_l1_name,
           row_number() OVER (ORDER BY r.increase DESC) rn2,
           count(*) OVER () AS n2
    FROM pb_half b JOIN roec r ON r.code = b.jq_code
  ) WHERE rn2 <= floor(0.1 * n2)
)
SELECT jq_code FROM roe_top
WHERE sw_l1_name IS NULL OR sw_l1_name NOT IN ({excl})
ORDER BY floatmv ASC LIMIT {cand}
"""


def initialize(context):
    g.stock_num = 10
    g.candidate_num = 10        # 原版 get_stock_list()[:10]：先截再过滤、不补位
    g.listed_days = 250
    g.limit_days = 20
    g.hold_history = []
    g.high_limit = set()

    set_benchmark('000905.XSHG')      # 与聚宽原版一致：中证 500

    run_daily(prepare, time='09:05')
    run_weekly(rebalance, weekday=1, time='09:30')
    run_daily(check_limit_up, time='14:00')


def prepare(context):
    g.hold_history.append(set(context.portfolio.positions))
    if len(g.hold_history) > g.limit_days:
        g.hold_history = g.hold_history[-g.limit_days:]
    g.high_limit = set()
    held = list(context.portfolio.positions)
    if held and context.previous_date:
        bars = context.data.bars(context.previous_date, held)
        g.high_limit = {c for c, b in bars.items() if b.limit_up}


def rebalance(context):
    d = context.previous_date
    if d is None:
        return
    df = context.data.query(
        SQL, sd=d, listed=g.listed_days, cand=g.candidate_num,
        excl=','.join("'%s'" % x for x in EXCL_IND))
    cand = df['jq_code'].tolist()
    if not cand:
        return
    # 顺序与聚宽 FROEC 原版一致：
    #   get_stock_list(context)[:10] -> filter_paused/limitup/limitdown -> 黑名单 -> 截断
    # 注意 FROEC 是【先截到 10 再过滤、不补位】(candidate_num=10)，
    # 而 v0b 是【候选 15 -> 过滤 -> 黑名单 -> 最后截到 10】(candidate_num=15)。
    # 两条线的原版写法不同，不能互相照抄。
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
    if not g.high_limit:
        return
    codes = [c for c in g.high_limit if c in context.portfolio.positions]
    if not codes:
        return
    # 今天的状态走 context.current() —— 历史接口取不到今天（PIT 防火墙）。
    # 14:00 属 INTRADAY 阶段，limit_up 可见（与「盘中成交价用收盘价代理」的约定一致）。
    cur = context.current(codes)
    for code in codes:
        d = cur.get(code)
        if d is not None and not d.get('limit_up'):
            order_target_value(code, 0)
