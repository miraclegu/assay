#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ETF 趋势闸门 + 横截面动量轮动（移植自 AlphaMiner `etf.trend_momentum`）。

    大盘趋势闸门（宽基跌破 MA 全撤成现金） + 多头时对时点池算多周期动量、
    Top-N 等权持有 + 绝对动量过滤（动量 <= 0 不买）。

规格出处：`AlphaMiner/docs/strategies/etf/DESIGN.md` §3，
默认参数取自 `strategies/etf/trend_momentum/grid_full.yml` 的 ParamSpec 默认值。

## 数据源与费率都【在文件里声明】，裸命令就是对的口径

    python3 run.py strategies/ETF/etf_trend_momentum.py \
        --start 2020-01-01 --end 2026-08-31 --benchmark 000300.XSHG

模块级 `DATALAKE = 'etf_lake'` + `initialize` 里的 `set_order_cost`，
`run.py` 会自己落到对的 lake、用对的费率。
ETF lake 由 `datalake/build/build_etf_lake.py` 生成。

🔴 **2026-09-13 之前这两件事都靠人记得传参，而漏了不报错：**
主面板 `mart/panel_daily/` 是**纯股票**的（实测 0 行 ETF），不传 `--datalake`
的话候选池恒空、策略全程空仓 —— 只给出一条平线；不传 `--stamp-tax 0`
则按股票口径多扣一笔卖出印花税，而这个策略换手不低，拖累是系统性的。
**这两个失效模式当初就写在这段 docstring 里，却没有任何东西强制它** ——
同「靠人记得跑的步骤 = 迟早不跑」。现在判据在 `run.py` 与 selftest 里。

## 成本口径

ETF **无印花税**、佣金约万 0.5 —— 这是**事实不是偏好**，所以写在
`initialize` 的 `set_order_cost` 里。命令行仍然优先（会覆盖并告警）。

★ 引擎默认是**股票**的口径（佣金万 2.5 + 卖出印花税），直接套到 ETF 上会
  系统性低估收益。DESIGN.md §4 记的是「佣金约万 0.5（双边），无印花税」。
★ 滑点仍用引擎默认 0.0015（DESIGN.md 写 5bps 单边 ≈ 0.001 双边，同量级；
  本项目纪律是"滑点用默认值，只有对标时才改"）。

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

# 共享层走【私有实例】加载，与 froec_traded.py 同一手法：
# `module_from_spec` 每次都是新对象，两个 ETF 策略互不污染。
_spec = _ilu.spec_from_file_location(
    '_etf_core_tm', _os.path.join(_os.path.dirname(_os.path.abspath(__file__)),
                                  '_etf_core.py'))
_core = _ilu.module_from_spec(_spec)
_core.order_target_value = order_target_value       # noqa: F405  共享层要下单
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
    g.momentum_windows = getattr(g, 'momentum_windows', '20,60,120')
    g.trend_ma = getattr(g, 'trend_ma', 200)
    g.risk_adjusted = getattr(g, 'risk_adjusted', 1)
    g.abs_momentum_filter = getattr(g, 'abs_momentum_filter', 1)
    # 趋势闸门代理：沪深300 ETF（AlphaMiner 原版 sh510300）
    g.bench_etf = getattr(g, 'bench_etf', '510300.XSHG')
    # 时点池
    g.min_listed = getattr(g, 'min_listed', 60)
    g.min_amount = getattr(g, 'min_amount', 5e7)
    g.liq_window = getattr(g, 'liq_window', 90)
    # 年化波动下限（0=关，忠实原规格；P1 那套用 0.03 挡类现金）
    g.min_vol_ann = getattr(g, 'min_vol_ann', 0.0)
    g.freq = getattr(g, 'freq', 'weekly')        # weekly / monthly
    g.day = getattr(g, 'day', 1)                 # 周几 / 月内第几个交易日
    g.n_gate_off = 0                             # 闸门空仓的调仓次数，报告里要看

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
    # ---- 趋势闸门：跌破就全撤 ----
    if not _core.trend_long(context, d, g.bench_etf, g.trend_ma):
        g.n_gate_off += 1
        for code in list(context.portfolio.positions):
            order_target_value(code, 0)                          # noqa: F405
        return

    wins = _windows()
    df = _core.candidates(context, d, wins, min_listed=g.min_listed,
                          min_amount=g.min_amount, liq_window=g.liq_window,
                          min_vol_ann=float(g.min_vol_ann))
    if df is None or not len(df):
        return
    sc = _core.score(df, wins, bool(int(g.risk_adjusted)))
    if g.abs_momentum_filter:
        # 绝对动量过滤：得分 <= 0 不买（那一份额留现金）。
        # ★ risk_adjusted 只除以正的波动率，不改符号，所以这条在两种模式下等价。
        sc = {k: v for k, v in sc.items() if v > 0}
    if not sc:
        for code in list(context.portfolio.positions):
            order_target_value(code, 0)                          # noqa: F405
        return
    # 排序带 tie-break（按代码），保证跨机器可复现
    picks = [c for c, _ in sorted(sc.items(), key=lambda kv: (-kv[1], kv[0]))][:g.top_n]
    picks = context.tradable(picks, 'buy')
    _core.place(context, picks, g.top_n)
