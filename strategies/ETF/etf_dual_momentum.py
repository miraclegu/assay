#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ETF 双动量轮动（移植自 AlphaMiner `etf.dual_momentum`）。

    绝对动量（近 abs_window 日收益 > 0）先过滤，再在通过者里按相对动量
    Top-N 等权持有。**没有大盘趋势闸门** —— 择时由绝对动量自己完成。

与 `etf_trend_momentum` 的区别只有一处：那个用宽基 MA 做统一闸门（一刀切，
要么全持有要么全空仓），这个是**逐只**判绝对动量（可以只剩两三只通过）。
选池与打分完全相同，共享 `_etf_core.py`。

默认参数取自 AlphaMiner 的 ParamSpec：
    top_n=5, momentum_windows=[60,120], abs_window=60, risk_adjusted=False

跑法与成本口径见 `etf_trend_momentum.py` 文件头（同一套，必须传
lake 与费率都由文件自己声明，裸命令即可 ——
见 `DATALAKE` 与 `initialize` 里的 `set_order_cost`）。

## 实测（2020-01-02 ~ 2026-08-31，本金 100 万，佣金万0.5 / 无印花税 / 滑点默认 0.0015）

    策略                     risk_adj  年化      回撤     夏普   平均仓位  空仓天数
    trend_momentum(默认)        ✓     8.82%   19.77%   0.64    52.5%    42.6%
    trend_momentum              ✗     5.90%   48.32%   0.35    51.5%    42.5%
    dual_momentum               ✓     4.82%   35.45%   0.38    91.9%     0%
    dual_momentum(默认)         ✗    -2.85%   72.25%   0.08    91.5%     0.2%
    ── 基准 沪深300                    1.84%（全期 +12.90%）

## 🔴🔴 `risk_adjusted=True`（文档里的默认）会【退化成货币基金】

打分是 `Σ(P0/Pw − 1) ÷ 近60日波动率`。货币 ETF 的波动率**趋近 0**，
于是分母把它的得分顶到 100+，横截面排名永远是它。实测 trend_momentum
927 个持仓日里：

    511880 银华日利ETF   760 天      511360 短融ETF      577 天
    159650 国开债ETF     157 天      512690 酒ETF        138 天

也就是说那 8.82% **不是 ETF 轮动的结果**，是「货币/债券停车 + 偶尔冲一把
行业」——19.77% 的回撤正来自后者（酒 / 新能源车）。
★ AlphaMiner 原版是同样的写法（`score / vol.replace(0, pd.NA)` 只挡 vol 恰好
  等于 0），所以这不是移植走样，是**原规格本身的性质**。
★ 要当真正的行业轮动跑，得先把货币/债券类排除出候选池（`taxonomy.py` 里
  「货币现金」「债券」两个赛道现成），或给波动率设下限。本次**没有替它改** ——
  用户要的是"重建这几套策略"，改判据就不是同一个策略了。

## 🔴 trend_momentum 有 42.6% 的交易日是空仓（最长连续 261 日）

引擎会为此打「平均仓位过低，结论不可用」的双告警。**这次是设计如此、不是数据 bug**：
趋势闸门要求 `510300 > MA200`，而 2021-12-20 ~ 2023-01-13 沪深300 一直在 MA200
之下。DESIGN.md §3.1 写的是"跌破 → 全部切换到债券/货币 ETF"，而 AlphaMiner
的实现返回空权重 = **持币**，本移植与代码保持一致（不是与那句话保持一致）。
★ 所以它的夏普/回撤**不能**与满仓策略直接比 —— 引擎那条告警的措辞是对的。

## 🔴 为什么这两套的年化远低于 P1（17~19%）—— 主因是【没有类现金过滤】

同一个 lake、同一个引擎，P1 移植版对上了聚宽的已知结果（见
`etf_p1_rotation.py` 的对数表：基准/贝塔/波动率三项精确一致）。所以低年化
**不是引擎或数据的问题**，是这两套规格本身缺一道过滤：

    P1        MIN_VOL_ANN=0.03 + 名称剔「货币/债」-> 类现金标的进不了池
    这两套    没有任何类现金过滤 -> 货币 ETF 与股票 ETF 同池竞争

实测加上同样的过滤（`--param min_vol_ann=0.03`，2020-01-02~2026-08-31）：

    trend_momentum   8.82%  ->  14.22%   (+5.40pp)  回撤 19.77% -> 28.75%
    dual_momentum   −2.85%  ->  −3.04%   (几乎不变)

★ trend_momentum 提升明显，因为它默认 `risk_adjusted=True`，货币 ETF 被
  "除以趋近 0 的波动率"顶上榜首；挡掉之后才真的在做行业轮动。
★ dual_momentum 几乎没变 —— 它默认 `risk_adjusted=False`，货币 ETF 的**原始
  动量**本来就排不上。它的 −3% / 72% 回撤是另一个原因：**完全没有大盘闸门**，
  一路追动量买在顶部（峰值恰是 2021-01-07 核心资产见顶那天，单笔最大亏损
  才 −22%，是累积失血不是某一笔爆掉）。
★ **默认值没有改**（`min_vol_ann=0.0`）—— 改了就不是原规格那套策略了。
  这个开关是给"想知道差在哪"用的。
"""
# 🔴 **这个策略只能跑在 ETF lake 上**（`run.py` 的 `resolve_lake` 读它）。
#   不声明的话主面板是纯股票的，候选池恒空、全程空仓，
#   **一条平线且不报任何错**
#   —— 声明之后不传 --datalake 也会自动落到对的 lake，传错了则直接报错。
DATALAKE = 'etf_lake'

import importlib.util as _ilu
import os as _os

from assay.api import *          # noqa: F401,F403

_spec = _ilu.spec_from_file_location(
    '_etf_core_dm', _os.path.join(_os.path.dirname(_os.path.abspath(__file__)),
                                  '_etf_core.py'))
_core = _ilu.module_from_spec(_spec)
_core.order_target_value = order_target_value       # noqa: F405
_spec.loader.exec_module(_core)


def initialize(context):
    # 🔴 **ETF 的费率是【事实】不是偏好，所以写在策略里，不靠人记得传参。**
    #   ETF **不征印花税**（A 股股票卖出千一/万五），佣金约万 0.5、无过户费。
    #   用股票默认值（含印花税）跑 ETF 会凭空多扣一笔卖出税，而这个策略
    #   年换手 9 次以上 —— 拖累是系统性的，**且不报错**。
    #   ★ 命令行仍然优先（`--commission` 等会覆盖并告警），对标时照样能压平。
    set_order_cost(commission=0.00005, min_commission=5,      # noqa: F405
                   close_tax=0.0, open_tax=0.0)
    g.top_n = getattr(g, 'top_n', 5)
    g.momentum_windows = getattr(g, 'momentum_windows', '60,120')
    g.abs_window = getattr(g, 'abs_window', 60)
    g.risk_adjusted = getattr(g, 'risk_adjusted', 0)
    g.min_listed = getattr(g, 'min_listed', 60)
    g.min_amount = getattr(g, 'min_amount', 5e7)
    g.liq_window = getattr(g, 'liq_window', 90)
    # 年化波动下限（0=关，忠实原规格；P1 那套用 0.03 挡类现金）
    g.min_vol_ann = getattr(g, 'min_vol_ann', 0.0)
    g.freq = getattr(g, 'freq', 'weekly')
    g.day = getattr(g, 'day', 1)
    g.n_all_fail = 0                 # 全部标的绝对动量为负的调仓次数

    set_benchmark('000300.XSHG')                 # noqa: F405

    if g.freq == 'monthly':
        run_monthly(rebalance, monthday=g.day, time='09:30')     # noqa: F405
    else:
        run_weekly(rebalance, weekday=g.day, time='09:30')       # noqa: F405


def _windows():
    return [int(x) for x in str(g.momentum_windows).split(',') if x.strip()]


def rebalance(context):
    d = context.previous_date
    if d is None:
        return
    wins = _windows()
    aw = int(g.abs_window)
    # 🔴 绝对动量窗口要一起取回来，否则得再查一次。把它并进 windows 去查、
    #   但**打分时只用 momentum_windows** —— 两者是不同的事（一个择时、一个排序）。
    need = sorted(set(wins) | {aw})
    df = _core.candidates(context, d, need, min_listed=g.min_listed,
                          min_amount=g.min_amount, liq_window=g.liq_window,
                          min_vol_ann=float(g.min_vol_ann))
    if df is None or not len(df):
        return

    # ---- 绝对动量择时：近 abs_window 日收益 > 0 ----
    passed = []
    for row in df.itertuples(index=False):
        pw = getattr(row, 'p_%d' % aw)
        if pw is None or pw != pw or pw <= 0:
            continue
        if row.p0 / pw - 1.0 > 0:
            passed.append(row.jq_code)
    if not passed:
        g.n_all_fail += 1
        for code in list(context.portfolio.positions):
            order_target_value(code, 0)                          # noqa: F405
        return

    sc = _core.score(df[df['jq_code'].isin(passed)], wins,
                     bool(int(g.risk_adjusted)))
    if not sc:
        g.n_all_fail += 1
        for code in list(context.portfolio.positions):
            order_target_value(code, 0)                          # noqa: F405
        return
    picks = [c for c, _ in sorted(sc.items(), key=lambda kv: (-kv[1], kv[0]))][:g.top_n]
    picks = context.tradable(picks, 'buy')
    _core.place(context, picks, g.top_n)
