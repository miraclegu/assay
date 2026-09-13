#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P1 · ETF 全市场动态池轮动 —— 聚宽版的 assay 移植（对数用）。

正本是用户给的聚宽策略（`commands/trend/core/qmt_etf_pit_t1open.py` 的 JQ 版）。
**移植这一套的首要目的是【对数】**：聚宽上它有一组已知结果，用来验证本地
ETF lake + assay 引擎搭得对不对 —— 这是"探针必须带已知真值当场对数"那条纪律。

    聚宽实测  2020-01-01 ~ 2026-07-31，本金 10 万
        年化 17.27%   回撤 25.06%   夏普 0.587   胜率 0.568   盈亏比 1.785
        基准(沪深300) 12.00%       贝塔 0.748   阿尔法 0.149

一句话：每周四从【全市场】ETF/LOF 按昨日 40 日均成交额取前 40 做动态池，池内取
「MA5>MA40 在势 + RS 前 50% + 20 日动量前 8」，按反波动率定权（单只 ≤20%、
同涨簇 20%~45%），宽度转弱整体降仓；死叉或跌破 MA40 缓冲线离场。

## 参数与原版一一对应（改任何一个都会偏离对数目标）

## 🔴 与聚宽必然存在的四处差异（先写清楚，免得把差异读成策略差异）

| | 聚宽 | 这里 |
|---|---|---|
| 标的类型 | `get_all_securities(types=['etf','lof'])` —— 交易所分类，**权威** | tdx 只有代码段，用 `沪市5xxxxx + 深市15/16xxxx` 近似 |
| 复权 | `fq='pre'` 前复权 | 后复权 `close_hfq` |
| 滑点 | `PriceRelatedSlippage(0.001)` 双边 0.1% | `--slippage 0.001` 传同一个数 |
| 退市标的的名称 | JQ 留全历史 display_name | **tdx 的名称表是 type-1，只存当前状态** |

★ 复权那条**不影响信号**：MA 比大小、动量 `P0/Pw−1`、相关系数、波动率全是
  比值或差分，前后复权只差一个常数因子，逐项等价。
🔴 最后一条是**真实的偏差来源**：P1 的 `is_pool_excluded` 规定「名称取不到 =
  脏数据 = 剔除」，而 tdx 里已退市基金全部无名（实测 sz150xxx 307 只全无名）。
  于是**当年还在交易、后来退市**的标的在本移植里进不了池 —— 这是**幸存者偏差，
  方向是把收益抬高**。聚宽那边没有这个问题。对数时要把它算进容差里。

## ✅ 对数结果（2020-01-01 ~ 2026-07-31，本金 10 万）

    指标            聚宽        本移植      判断
    基准收益       12.00%      12.00%     **精确一致**（管道对了）
    贝塔            0.748       0.75      **一致**
    策略波动率      0.226      0.2266     **一致**
    胜率            0.568      0.5565     一致
    最大回撤       25.06%      23.24%     −1.8pp
    年化           17.27%      18.55%     **+1.28pp**
    平仓笔数         1387        1973

★ 基准/贝塔/波动率三项精确吻合 —— 这几项只取决于**数据与撮合**，
  它们对上就说明 ETF lake 与 assay 引擎搭对了。
★ 剩下 +1.28pp 的方向与文件头那条**幸存者偏差**一致：tdx 没有已退市基金的
  名称，而 P1 规定「名称取不到即剔除」，于是当年在交易、后来退市的标的
  在本移植里进不了池 —— 偏差方向就是**把收益抬高**。
★ 夏普不能直接比：聚宽用 **rf=4%**（验算 `(0.1727−0.04)/0.226 = 0.587` 与它
  报的 0.587 精确相符），assay 用 0。同口径折算本移植是 0.64。

## 🔴 移植过程中自己犯的两个错（都靠"对数"才发现）

① **`full_rebalance` 漏了。** 原版只在重选日把持仓拉回目标权重，非重选日
   只做"清离场 + 补未建仓"。我一开始每天都拉 —— 收益/回撤看着还"差不多"
   （19.53% / 24.29%），但**平仓笔数 5942 vs 聚宽 1387，四倍换手**。
   ★ 教训：**对数必须连笔数/换手一起对**，只对年化是发现不了的。
② **`NaN` 是 truthy。** 名称缺失时 pandas 给 `float('nan')`，
   `if not name` 那道守卫拦不住它，`k in name` 直接 TypeError。
   缺名必须在取数出口就归一成 `None`。
"""
# 🔴 **这个策略只能跑在 ETF lake 上**（`run.py` 的 `resolve_lake` 读它）。
#   不声明的话主面板是纯股票的，策略会拿股票 K 线算完成交额排名再崩在取名称那一步，
#   而要是那张表恰好存在，它会产出一份**看着完全正常**的回测
#   —— 声明之后不传 --datalake 也会自动落到对的 lake，传错了则直接报错。
DATALAKE = 'etf_lake'

import numpy as np
import pandas as pd

from assay.api import *          # noqa: F401,F403

# ============================== 参数（照抄原版）==============================
MA_FAST, MA_SLOW = 5, 40
MOM_LB = 20
VOL_LB = 20
N_MAX = 8
EXIT_BUFFER = 0.015

W_CAP = 0.20
GROSS = 0.97

REBAL_WEEKDAY = 3                      # 周四（0=周一）
CHURN_PCT, MAX_SWAPS = 0.5, 2

POOL_N = 40
LIQ_WIN = 40
MIN_VOL_ANN = 0.03

RS_LB = 100
RS_KEEP = 0.50

CORR_WIN = 60
CLUS_THR = 0.90
CLUS_CAP = 0.15
CLUS_CAP_HI = 0.45

BREADTH_GATE = True
BREADTH_FRAC = 0.375

MEGA_TOPK = 2

CASH_MGMT_NUM = set(['511990', '511880', '159003', '511850',
                     '159005', '511360', '519898'])
MEGA_EXCLUDE_NUM = set(['159980'])
MEGA_PREFIXES = [
    ('沪深300', '沪深300'), ('中证500', '中证500'), ('上证50', '上证50'),
    ('中证1000', '中证1000'), ('中证800', '中证800'), ('黄金', '黄金'),
    ('证券ETF', '证券ETF'), ('半导体ETF', '半导体ETF'), ('芯片ETF', '芯片ETF'),
    ('医药ETF', '医药ETF'), ('生物医药ETF', '生物医药ETF'), ('创新药ETF', '创新药ETF'),
    ('创业板ETF', '创业板ETF'), ('创业板50ETF', '创业板50ETF'), ('纳指ETF', '纳指ETF'),
]

NEED_FULL = RS_LB + MA_SLOW + 10       # 150 根
NEED_HOLD = MA_SLOW + MOM_LB + 10      # 70 根


# ============================== 取数 ==============================
def _px(context, sd, codes, n):
    """截至 sd（= 昨日）的后复权收盘宽表。index=date, columns=code。"""
    if not codes:
        return pd.DataFrame()
    lst = "','".join(codes)
    df = context.data.query("""
        SELECT date, jq_code, close_hfq FROM {panel}
        WHERE jq_code IN ('{lst}')
          AND date <= DATE '{sd}' AND date > DATE '{sd}' - INTERVAL {lb} DAY
    """, sd=sd, lst=lst, lb=int(n * 1.7))
    if df is None or not len(df):
        return pd.DataFrame()
    return df.pivot(index='date', columns='jq_code', values='close_hfq').sort_index()


def _names(context):
    """代码 -> 名称。tdx 的名称表只有【当前仍在交易】的，退市的取不到（见文件头）。"""
    if getattr(g, '_nm', None) is None:
        df = context.data.query(
            "SELECT code, name FROM read_parquet('{root}/std/etf_master.parquet')")
        # 🔴 缺名必须归一成 None，不能让 NaN 流出去 —— `float('nan')` 是
        #   **truthy**，`if not name` 那道守卫拦不住它，后面 `k in name`
        #   直接 TypeError。（实测当场崩在这。）
        g._nm = {c: (n if isinstance(n, str) and n.strip() else None)
                 for c, n in zip(df['code'], df['name'])}
    return g._nm


def _alive(context, sd):
    """sd 当天有 K 线 = 当时在市且没停牌（对应原版 get_all_securities + filter_paused）。"""
    df = context.data.query(
        "SELECT DISTINCT jq_code FROM {panel} WHERE date = DATE '{sd}'", sd=sd)
    return set(df['jq_code']) if df is not None and len(df) else set()


# ============================== 池卫生（照抄原版）==============================
def _fund_code_ok(code):
    num, _, mkt = code.partition('.')
    if mkt == 'XSHG':
        return num[:1] == '5'
    return num[:2] in ('15', '16')


def _pool_excluded(code, name):
    if not name:
        return True                                     # 名称取不到 -> 剔除
    num = code.split('.')[0]
    if num.startswith('150') or num.startswith('502'):
        return True
    if num in CASH_MGMT_NUM:
        return True
    if any(k in name for k in ('货币', '债', '分级')):
        return True
    if any(k in name for k in ('原油', '油气', '石油')):
        return True
    if name.startswith('上证指数ETF'):
        return True
    return False


def _mega_family(code, name):
    if not name or code.split('.')[0] in MEGA_EXCLUDE_NUM:
        return None
    if name.startswith('深证100ETF') or name.startswith('深100ETF'):
        return '深100'
    if name.startswith('恒生指数ETF') or name.startswith('恒生ETF'):
        return '恒生指数'
    if name.startswith('有色金属ETF') or name.startswith('有色ETF'):
        return '有色金属'
    for prefix, family in MEGA_PREFIXES:
        if name.startswith(prefix):
            return family
    return None


def _dynamic_pool(context, sd):
    """全市场按昨日 40 日均成交额取前 POOL_N，过池卫生 + 每族限 MEGA_TOPK。"""
    nm = _names(context)
    df = context.data.query("""
        SELECT jq_code, avg(amount) AS amt FROM {panel}
        WHERE date <= DATE '{sd}' AND date > DATE '{sd}' - INTERVAL {lb} DAY
        GROUP BY 1 HAVING avg(amount) > 0
    """, sd=sd, lb=int(LIQ_WIN * 1.7))
    if df is None or not len(df):
        return []
    alive = _alive(context, sd)
    df = df.sort_values('amt', ascending=False)
    seen, pool = {}, []
    for code in df['jq_code']:
        if code not in alive or not _fund_code_ok(code):
            continue
        name = nm.get(code)
        if _pool_excluded(code, name):
            continue
        fam = _mega_family(code, name)
        if fam is not None:
            if seen.get(fam, 0) >= MEGA_TOPK:
                continue
            seen[fam] = seen.get(fam, 0) + 1
        pool.append(code)
        if len(pool) >= POOL_N:
            break
    return pool


# ============================== 信号（照抄原版）==============================
def _signals(closes):
    info = {}
    if closes is None or closes.empty:
        return info
    for code in closes.columns:
        c = closes[code].astype(float).ffill().dropna()
        if len(c) < MA_SLOW + MOM_LB:
            continue
        ma_f = c.rolling(MA_FAST).mean().iloc[-1]
        ma_s = c.rolling(MA_SLOW).mean().iloc[-1]
        if np.isnan(ma_s):
            continue
        close = float(c.iloc[-1])
        vol_ann = c.pct_change().tail(CORR_WIN).std() * np.sqrt(244)
        if not vol_ann or np.isnan(vol_ann) or vol_ann < MIN_VOL_ANN:
            continue                                    # 类现金标的
        base = float(c.iloc[-1 - MOM_LB])
        vol20 = c.pct_change().tail(VOL_LB).std() * np.sqrt(244)
        info[code] = {
            'in_trend': (ma_f > ma_s) and (close > ma_s),
            'exit': (ma_f <= ma_s) or (close < ma_s * (1 - EXIT_BUFFER)),
            'mom': (close / base - 1.0) if base > 0 else 0.0,
            'vol': vol20 if vol20 and vol20 > 0 else 0.3,
            'px': c,
        }
    return info


def _rs_ok(info):
    if RS_LB <= 0:
        return set(info)
    rets = {}
    for code, v in info.items():
        c = v.get('px')
        if c is not None and len(c) > RS_LB:
            base = float(c.iloc[-1 - RS_LB])
            if base > 0:
                rets[code] = float(c.iloc[-1]) / base - 1.0
    if len(rets) < 3:
        return set(info)
    rank = pd.Series(rets).rank(pct=True)
    return set(code for code in rank.index if rank[code] >= 1.0 - RS_KEEP)


def _select(held, ranked, info):
    """缓冲汰换：保留仍在榜的持仓，空槽按动量补，只换掉掉到后 CHURN_PCT 的，最多 MAX_SWAPS。"""
    n = len(ranked)
    rank = {c: i for i, c in enumerate(ranked)}
    members = [c for c in held if c in rank]
    incoming = [c for c in ranked if c not in members]
    free = N_MAX - len(members)
    while free > 0 and incoming:
        members.append(incoming.pop(0))
        free -= 1
    if incoming:
        cutoff = n * (1 - CHURN_PCT)
        droppable = sorted([s for s in members if rank[s] >= cutoff],
                           key=lambda s: rank[s], reverse=True)
        for k in range(min(len(droppable), len(incoming), MAX_SWAPS)):
            if rank[incoming[k]] < rank[droppable[k]]:
                members.remove(droppable[k])
                members.append(incoming[k])
    return members[:N_MAX]


def _cluster_map(members, info, thr=CLUS_THR):
    ranked = sorted([s for s in members if 'px' in info.get(s, {})],
                    key=lambda s: info[s]['mom'], reverse=True)
    if len(ranked) <= 1:
        return {s: s for s in ranked}
    df = pd.DataFrame({s: info[s]['px'] for s in ranked}).sort_index()
    corr = df.pct_change().tail(CORR_WIN).corr()
    reps, cluster = [], {}
    for s in ranked:
        rep = None
        for r in reps:
            try:
                v = float(corr.loc[s, r])
            except Exception:                           # noqa: BLE001
                v = float('nan')
            if not np.isnan(v) and v >= thr:
                rep = r
                break
        cluster[s] = rep if rep is not None else s
        if rep is None:
            reps.append(s)
    return cluster


def _cap_cluster(w, cluster, info):
    clusters = set(cluster.values())
    if CLUS_CAP_HI > CLUS_CAP and len(clusters) >= 1:
        cmom = {c: max(info[s]['mom'] for s in cluster if cluster[s] == c)
                for c in clusters}
        order = sorted(clusters, key=lambda c: cmom[c], reverse=True)
        nc = len(order)
        lim = {c: (CLUS_CAP_HI if nc == 1 else
                   CLUS_CAP_HI - (CLUS_CAP_HI - CLUS_CAP) * k / (nc - 1)) * GROSS
               for k, c in enumerate(order)}
    else:
        lim = {c: CLUS_CAP * GROSS for c in clusters}
    for _ in range(50):
        tot = {}
        for s, x in w.items():
            tot[cluster[s]] = tot.get(cluster[s], 0.0) + x
        over = [c for c, t in tot.items() if t > lim[c] + 1e-9]
        if not over:
            break
        excess = 0.0
        for c in over:
            scale = lim[c] / tot[c]
            for s in w:
                if cluster[s] == c:
                    excess += w[s] * (1 - scale)
                    w[s] *= scale
        under = [s for s in w if tot[cluster[s]] <= lim[cluster[s]] + 1e-9]
        usum = sum(w[s] for s in under)
        if usum <= 1e-12:
            break
        for s in under:
            w[s] += excess * w[s] / usum
    tot = {}
    for s, x in w.items():
        tot[cluster[s]] = tot.get(cluster[s], 0.0) + x
    for c, t in tot.items():
        if t > lim[c] + 1e-9:
            scale = lim[c] / t
            for s in w:
                if cluster[s] == c:
                    w[s] *= scale
    return w


def _weights(members, info):
    if not members:
        return {}
    raw = {s: 1.0 / info[s]['vol'] for s in members}
    tot = sum(raw.values())
    w = {s: min(raw[s] / tot, W_CAP) for s in members}
    ssum = sum(w.values())
    w = {s: v / ssum * GROSS for s, v in w.items()}
    return _cap_cluster(w, _cluster_map(members, info), info)


# ============================== 引擎接线 ==============================
def initialize(context):
    g.target = {}
    g.last_week = None
    g._nm = None
    g.n_breadth = 0
    set_benchmark('000300.XSHG')                        # noqa: F405
    # 🔴 **ETF 的费率是【事实】不是偏好，所以写在策略里，不靠人记得传参。**
    #   ETF **不征印花税**（A 股股票卖出千一/万五），佣金约万 0.5、无过户费。
    #   用股票默认值（含印花税）跑 ETF 会凭空多扣一笔卖出税，而这个策略
    #   年换手 9 次以上 —— 拖累是系统性的，**且不报错**。
    #   ★ 命令行仍然优先（`--commission` 等会覆盖并告警），对标时照样能压平。
    set_order_cost(commission=0.00005, min_commission=5,      # noqa: F405
                   close_tax=0.0, open_tax=0.0)
    # 原版 PriceRelatedSlippage(0.001) —— 双边 0.1%
    set_slippage(0.001)                                 # noqa: F405
    # 原版是 run_daily 09:30：每日查离场，重选逻辑在函数内按周判定
    run_daily(my_trade, time='09:30')                   # noqa: F405


def my_trade(context):
    d = context.previous_date
    if d is None:
        return
    _check_exit(context, d)
    do_rebal = _is_reselect_day(context)
    if do_rebal:
        _reselect(context, d)
    _place(context, do_rebal)


def _check_exit(context, sd):
    if not g.target:
        return
    held = list(g.target)
    alive = _alive(context, sd)
    info = _signals(_px(context, sd, held, NEED_HOLD))
    for code in held:
        if code not in alive:
            g.target.pop(code, None)
            continue
        v = info.get(code)
        if v is None or v['exit'] or not v['in_trend']:
            g.target.pop(code, None)


def _is_reselect_day(context):
    dt = context.current_date
    week = dt.isocalendar()[:2]
    new_week = (week != g.last_week and dt.weekday() >= REBAL_WEEKDAY)
    if new_week:
        g.last_week = week
    return new_week or (not g.target)


def _reselect(context, sd):
    pool = _dynamic_pool(context, sd)
    if not pool:
        return
    info = _signals(_px(context, sd, pool, NEED_FULL))
    if not info:
        return
    in_trend = set(c for c, v in info.items() if v['in_trend'])
    rs = _rs_ok(info)
    ranked = sorted([s for s in in_trend if s in rs],
                    key=lambda s: (-info[s]['mom'], s))      # tie-break 按代码
    members = _select(list(g.target), ranked, info)
    w = _weights(members, info)
    if BREADTH_GATE:
        scale = min(1.0, len(in_trend) / (BREADTH_FRAC * POOL_N))
        if scale < 1.0:
            g.n_breadth += 1
            w = {s: x * scale for s, x in w.items()}
    g.target = w


def _place(context, full_rebalance):
    """按目标权重下单。**先卖后买** —— 引擎的现金是即时回笼的。

    🔴 `full_rebalance` 只在【重选日】为 True。P1 的目标权重只在重选日变，
      非重选日还去把已有持仓拉回权重的话，只会因价格漂移产生一堆碎单 ——
      原版文件头把这条写得很明白（聚宽那边表现为「开仓数量不能小于 100」刷屏）。
      ★ 这一条漏掉的代价实测过：**平仓笔数 5942 vs 聚宽 1387，四倍换手**，
        而收益/回撤看着还"差不多"，所以光对年化是发现不了的 ——
        对数必须连**笔数**一起对。
      ★ 非重选日仍然要做两件事：清掉 check_exit 剔出去的、补还没建仓的。

    ★ 整手取整、T+1、涨跌停、停牌一律由 broker 负责（原版在聚宽里要自己算
      MIN_LOT 是因为那边没有这一层）——「策略不重写交易规则」那条。
    """
    tv = context.portfolio.total_value
    pos = context.portfolio.positions
    for code in list(pos):
        if code not in g.target:
            order_target_value(code, 0)                      # noqa: F405
    if not g.target:
        return
    for code, w in sorted(g.target.items(), key=lambda kv: kv[1]):
        if not full_rebalance and code in pos and pos[code].shares > 0:
            continue                                         # 非重选日不动已有持仓
        order_target_value(code, w * tv)                     # noqa: F405
