#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""【演示】策略直接用【量化因子】选股 —— 一行 SQL 都没有。

取数全走 `context.data` 的四个方法（`assay/feed.py`，`guard` 逐个加 PIT 检查）：

    universe(d, listed_days=)      PIT 在册股票（🔴 含停牌股，不是面板当日行）
    snapshot(d, cols, codes=)      面板当日行（ST / 流通市值这些）
    factors(d, ids, codes=)        **因子值**（mart/factor_daily 的 162 个）
    factor_meta(ids)              因子是什么（单位 / 横截面可不可比 / 预热）

职责边界与 `froec_api.py` 那条一样：**feed 管「从哪取、as-of 怎么算」，
这个文件管「阈值、分位、排序、截断」** —— 后者是规则，本来就该看得见、改得动。

★ 本文件是**用法样板与管道自证**，不是一个提出来要采用的策略：
  它的参数没有做过逐年独立回测，别拿它的年化去跟 froec / 红利比
  （同「对数表里不许留只有数、没有判断的行」—— 这里干脆一个数都不给）。

🔴🔴 **横截面排序之前先问 `xs_comparable`。** 因子表里有两类东西：

    mv / ep / turnover_20   标度是全市场共同的（元、倍、百分数） -> 排得
    ma20 / ema12 / atr14    标度是**这只票自己的股价与拆股史**
                            （跨票 1.00~5899.9） -> **横截面排它排的是股价**

  而同一个 `ma20` 拿来判**这一只票**「close > ma20」（在势）完全正确 ——
  两种用法只有策略自己知道，所以 feed 不拦（拦了会误伤后一种）。
  下面 `_xs_guard()` 就是这条纪律的可执行版：把要**横截面排序**的因子
  报给它，换成一个不可比的当场失败，而不是静默排出一份按股价排的名单。
"""
from assay.api import *

#: 横截面排序用到的因子 —— 改这里就改了规则，而 `_xs_guard` 会跟着校验
RANK_BY = 'mv_float'      # 按流通市值【升序】取最小的那批
SCREEN_BY = 'ep'          # 先用利润市值比（= 1/PE）粗筛掉亏损与高估的


def initialize(context):
    # ★ 默认值写**字面量**不写模块级常量：看板的参数解析只认
    #   `getattr(g, 'x', 字面量)`，用常量的话这一项在回测页上**看不见**。
    g.stock_num = int(getattr(g, 'stock_num', 10))
    g.ep_pct = float(getattr(g, 'ep_pct', 0.5))      # ep 前 50% 才进下一层
    g.listed_days = int(getattr(g, 'listed_days', 375))
    set_benchmark('000905.XSHG')
    run_monthly(rebalance, time='09:30')


def _xs_guard(context, ids):
    """要横截面排序的因子必须 `xs_comparable` —— 不可比的当场拒。

    🔴 判据取**服务端目录表**给的那一位，不在这里抄一份清单 ——
      抄一份的话加一个因子它不会跟着变，而那不报错，只是有一天
      悄悄按股价排了一次序（同「照清单拼会漏掉新文件的全部组合」那条）。
    """
    m = context.data.factor_meta(ids).set_index('factor_id')
    miss = [i for i in ids if i not in m.index]
    if miss:
        raise ValueError('因子目录里没有：%s' % '、'.join(miss))
    bad = [i for i in ids if not bool(m.loc[i, 'xs_comparable'])]
    if bad:
        raise ValueError(
            '这几个因子【横截面不可比】，不能拿来跨票排序：%s\n'
            '它们的标度是个股自己的股价/股本（单位 %s），排出来的是'
            '"股价 × 上市以来分红拆细"。要按它判【单只票】的形态是对的，'
            '比如 close > ma20。详见看板「🧪 因子广场」。'
            % ('、'.join(bad), '/'.join(str(m.loc[i, 'unit']) for i in bad)))


def rebalance(context):
    d = context.previous_date          # 🔴 用前一交易日：今天的因子行含当日收盘
    if d is None:
        return
    _xs_guard(context, [RANK_BY, SCREEN_BY])

    # ① PIT 在册（含停牌股 —— 它们要参与分位切点的计算）
    pool = context.data.universe(d, listed_days=g.listed_days)

    # ② 面板当日行：把 ST / 风险警示 / 没有流通市值的剔掉
    snap = context.data.snapshot(d, ['is_st', 'is_risk_warned', 'floatmv'],
                                 codes=pool, carry_days=7)
    # 🔴 结转窗口内**一行都没有**的票（退市 / 长停）这三列是 NA ——
    #   `.astype(bool)` 会当场抛 `cannot convert float NaN to bool`。
    #   这里一律**当成不可买**（保守侧）而不是填 0 放行：
    #   "查不到" 与 "查到了是 0" 是两件事，混起来就是静默把不该买的买进来。
    snap = snap.dropna(subset=['is_st', 'is_risk_warned', 'floatmv'])
    snap = snap[(snap['is_st'] == 0) & (~snap['is_risk_warned'].astype(bool))
                & (snap['floatmv'] > 0)]
    cand = snap['jq_code'].tolist()
    if not cand:
        return

    # ③ 因子值 —— 这一步就是本文件存在的理由
    fac = context.data.factors(d, [SCREEN_BY, RANK_BY], codes=cand)
    fac = fac.dropna(subset=[SCREEN_BY, RANK_BY])
    if fac.empty:
        return

    # ④ 规则（阈值 / 分位 / 排序 / 截断）—— 全在策略这一侧，看得见改得动
    cut = fac[SCREEN_BY].quantile(1.0 - g.ep_pct)     # ep 越大越好，取上半
    fac = fac[fac[SCREEN_BY] >= cut]
    # 🔴 排序要**显式 tie-break**（按代码）：`mv_float` 并列时谁进谁出否则
    #   取决于扫描顺序 —— 同 froec 那条 `ORDER BY r.increase DESC` 没有
    #   tie-break 的老问题，它跨进程/跨版本都不保证稳定。
    fac = fac.sort_values([RANK_BY, 'jq_code'], ascending=[True, True])
    target = context.tradable(fac['jq_code'].tolist()[:g.stock_num * 3], 'buy')
    target = target[:g.stock_num]
    if not target:
        return

    # ⑤ 等权（先减后加，理由见 _demo/rebalance_weights.py）
    per = context.portfolio.total_value * 0.995 / len(target)
    for code in list(context.portfolio.positions):
        if code not in target:
            order_target_value(code, 0)
    for code in target:
        order_target_value(code, per)
