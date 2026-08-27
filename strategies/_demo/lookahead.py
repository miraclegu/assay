"""【反面示例】演示当前 API 有多容易写出未来函数。不要照抄。

只把 previous_date 改成 current_date、条件用当天的 change_pct ——
在 09:30 这是当天收盘涨幅，根本还不知道。三行之内就作弊成功。
"""
from assay.api import *


def initialize(context):
    g.n = 10
    run_weekly(rebalance, weekday=1, time='09:30')


def rebalance(context):
    # ★ 作弊点：用【当天】的 change_pct 选股。09:30 时当天收盘涨幅是未来信息。
    codes = context.data.codes_at(
        context.current_date,
        where="is_st = 0 AND listed_days > 375 AND floatmv > 0",
        order='change_pct DESC', limit=g.n)
    for c in list(context.portfolio.positions):
        if c not in codes:
            order_target_value(c, 0)
    need = [c for c in codes if c not in context.portfolio.positions]
    if need:
        per = context.portfolio.cash / len(need)
        for c in need:
            order_target_value(c, per)
