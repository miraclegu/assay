#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FROEC-API：与 froec.py 的选股【逐位等价】，但**不写一行 SQL**。

存在的理由
----------
理想中策略只该用引擎层提供的 API 取数 —— **只有 feed 知道 datalake 的存在**。
而 froec.py / 红利那批策略把 9 层 CTE 的 SQL 写在策略文件里：口径（as-of、
停牌结转、多期去重）与规则（阈值、分位、排序、截断）混在同一段文本里，
换数据源、改口径、复用某一层都无从下手。

本文件用 `assay/feed.py` 新增的三个取数 API 重写同一套选股：

    ctx.data.universe(...)      PIT 在册股票（**不是**面板当日行）
    ctx.data.snapshot(...)      面板当日行 + 停牌股的价格派生量结转
    ctx.data.fundamentals(...)  按 pub_date as-of 的财务，可取最近 N 期

筛选、分位、排名、截断全部用 Python/pandas 写在下面 `_pick()` 里 ——
那些是**规则**，本来就该看得见、改得动。

🔴 **froec.py 一行都没动。** 它的归档要保持可比，而且实盘账户绑的是它的
   快照。本文件通过 `importlib` 独立加载 froec.py 的**私有实例**，只在
   这个实例上把 `rebalance` 换成本文件的版本 —— 不污染任何其它使用者
   （`module_from_spec` 每次都是新对象，同 froec_traded.py 的做法）。

与 SQL 的已知差异（**唯一一处，而且根因在原 SQL**）
--------------------------------------------------
原 SQL 的 `ORDER BY r.increase DESC`（ROE 加速度）**没有 tie-break**。
而 `roe_inc` 只有 4 位小数，并列很常见 —— 当并列**恰好跨过切点**
`floor(0.1 * n2)` 时，谁进谁出取决于 DuckDB 的扫描顺序，
**原 SQL 自己就不保证跨机器/跨版本稳定**。

实测（2016~2026 全部 539 个调仓日，抽样 77 个）：
    ROE 切点并列          3 个 = 3.9%   <- 原 SQL 在这些日子结果不确定
    切点无并列           74 个 —— 与 SQL **逐位相同 74 / 不同 0**

★ 所以本文件在三处排序都加了**显式 tie-break（按代码）**：至少它自己
  可复现、跨机器一致。代价是并列日与 SQL 取舍可能不同（各约 1 只票）。
🔴 **没有去"修" froec.py 的排序** —— 那会改变它全部历史归档的结果，
  而归档要保持可比。这个不确定性值得单独记一笔，但不该顺手改掉。

第一次诊断这处差异时踩了个坑：tie 检测写在**行业排除之后**的结果上，
而被排除的那一行不在末层输出里 -> `roe_rn == cut+1` 查不到 -> 漏判成
"无并列"。检测这类"切点两侧是否相等"必须在**截断与排除之前**做。

等价性
------
`_pick()` 与 SQL 的每一层逐一对应，selftest 里钉了两条判据：
  ① 同一天、同一批参数，两者返回的 `jq_code` 列表**逐位相同**（多个采样日）
  ② 全历史回测（2016-01~2026-09）逐日权益数值指纹 + 成交流水指纹相同
★ 能做到逐位等价的前提是 **pb / floatmv 没有并列**（实测四个采样日
  0 并列）—— 原 SQL 的 `ORDER BY pb ASC` 没有 tie-break，真有并列时
  连它自己都不保证顺序稳定。所以这不是"pandas 排序恰好一样"，
  而是"排序键本身唯一"。
"""
import importlib.util
import os

from assay.api import *          # noqa: F401,F403

_BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'froec.py')
_spec = importlib.util.spec_from_file_location('_froec_api_base', _BASE)
_base = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_base)

EXCL_IND = _base.EXCL_IND
prepare = _base.prepare
check_limit_up = _base.check_limit_up
stop_check = _base.stop_check
stop_filter = _base.stop_filter
blacklist = _base.blacklist
pf_check = _base.pf_check
_bench_up = _base._bench_up
_do_buy = _base._do_buy
rebalance_buy = _base.rebalance_buy


def _pick(context, d, ask):
    """候选池 -> DataFrame，与 froec.py 那条 SQL 的末层**同列同序**。

    每一步都对着 SQL 的一层 CTE，注释里标了对应关系。
    """
    D = context.data
    # ---- SQL: univ ----
    # 🔴 权威在册股票，**不能用面板当日行** —— 停牌股当日无 K 线、无行，
    #   而它们要参与下面两次分位切点的计算（聚宽 get_all_securities 含停牌）。
    u = D.universe(d, listed_days=g.listed_days,
                   exclude_like='688%' if g.kcb_688_only else '68%',
                   perturb=g.pert, salt=g.pert_salt)
    # ---- SQL: today + miss ----
    # 停牌股只结转【价格派生量】；基本面走 fundamentals()，不结转
    #   （结转会拿到该股最后交易日那天的过期报告，实测被 eps>0 误剔）。
    cols = ['pb', 'floatmv', 'is_risk_warned', 'sw_l1_name']
    snap = D.snapshot(d, cols, codes=u,
                      carry_days=400 if g.paused_in_pool else 0)
    # ---- SQL: eps1 ----
    eps = D.fundamentals(d, ['eps'], codes=u, periods=1)
    eps = eps.loc[eps['seq'] == 1, ['code', 'eps']]
    # ---- SQL: base（非ST / eps>0 / pb>0 / floatmv>0）----
    base = snap.merge(eps, left_on='jq_code', right_on='code', how='inner')
    st = base['is_risk_warned'].fillna(False).astype(bool)
    base = base.loc[(~st) & (base['eps'] > 0) & (base['pb'] > 0)
                    & (base['floatmv'] > 0)]
    if base.empty:
        return base
    # ---- SQL: pb_half（按 pb 升序取前 floor(0.5*n)，n 是过滤后的行数）----
    n = len(base)
    # 🔴 **显式 tie-break（按代码）**：原 SQL 的 `ORDER BY pb ASC` 没有
    #   tie-break，并列时 DuckDB 的顺序由扫描顺序决定、**不保证稳定**。
    #   API 版宁可给一个确定的顺序 —— 至少它自己可复现、跨机器一致。
    #   见文件末尾「与 SQL 的已知差异」。
    base = base.sort_values(['pb', 'jq_code'],
                            kind='mergesort').reset_index(drop=True)
    base['pb_rn'] = base.index + 1
    base['pb_n'] = n
    cut = int(g.pb_fixed) if g.pb_fixed else int(0.5 * n)     # SQL 的 floor()
    half = base.loc[base['pb_rn'] <= cut]
    # ---- SQL: roec（4*roe1 - roe2..roe5，且必须恰好 5 期）----
    roe = D.fundamentals(d, ['roe'], codes=u, periods=5, require_all=True)
    if roe.empty:
        return roe
    piv = roe.pivot(index='code', columns='seq', values='roe')
    if not set([1, 2, 3, 4, 5]).issubset(piv.columns):
        return piv.iloc[0:0]
    inc = (4 * piv[1] - piv[2] - piv[3] - piv[4] - piv[5]).rename('roe_inc')
    # ---- SQL: roe_top（按 increase 降序取前 floor(0.1*n2)）----
    top = half.merge(inc, left_on='jq_code', right_index=True, how='inner')
    if top.empty:
        return top
    n2 = len(top)
    top = top.sort_values(['roe_inc', 'jq_code'], ascending=[False, True],
                          kind='mergesort').reset_index(drop=True)
    top['roe_rn'] = top.index + 1
    top['roe_n'] = n2
    cut2 = int(g.roe_fixed) if g.roe_fixed else int(0.1 * n2)
    top = top.loc[top['roe_rn'] <= cut2]
    # ---- SQL 末层：排除行业 -> 按 floatmv 升序 -> LIMIT/OFFSET ----
    keep = top['sw_l1_name'].isna() | ~top['sw_l1_name'].isin(EXCL_IND)
    top = top.loc[keep].sort_values(['floatmv', 'jq_code'],
                                    kind='mergesort')
    return top.iloc[g.skip_n:g.skip_n + ask].reset_index(drop=True)


def rebalance(context):
    """与 froec.py 的 rebalance **逐行同构**，只把两处取数换成 API。

    ★ 刻意保持结构一致（含注释）而不是"重写得更漂亮"：等价性出问题时
      要能逐行对读。froec.py 改了逻辑，这里跟着改同一处即可。
    """
    d = context.previous_date
    if d is None or g.pf_halt:
        return
    lim = (g.fill_pool if (g.fill_paused or g.fill_blacklist)
           else g.candidate_num)
    _ask = max(lim, g.explain_pool) if g.explain_pool else lim
    df = _pick(context, d, _ask)                    # ← 唯一的差别在这里
    if df.empty:
        return
    cand = df['jq_code'].tolist()[:lim]
    if not cand:
        return
    cand = stop_filter(context, context.tradable(cand, 'buy'))
    _drop = blacklist(context, cand, d)
    _rank_pool = set(cand[:g.stock_num]) if g.lu_buy_only else None
    if _drop:
        cand = [c for c in cand if c not in _drop]
    target = cand[:g.stock_num]

    keep = set()
    if g.hold_buffer and context.portfolio.positions:
        wide = _pick(context, d, g.stock_num + g.hold_buffer)   # ← 同上
        wide = wide['jq_code'].tolist() if not wide.empty else []
        if g.hold_buffer_strict:
            wide = stop_filter(context, wide)
            _wdrop = blacklist(context, wide, d)
            if _wdrop:
                wide = [c for c in wide if c not in _wdrop]
        buf = set(wide[:g.stock_num + g.hold_buffer])
        keep = {c for c in context.portfolio.positions
                if c in buf and c not in target}
        if keep:
            log.info('[BUF] 缓冲区留下 %d 只：%s', len(keep), sorted(keep))

    if g.lu_buy_only and _rank_pool:
        _bk = {c for c in context.portfolio.positions
               if c in _rank_pool and c not in target}
        if _bk:
            keep = set(keep) | _bk
            log.info('[LU] 黑名单只挡买入，留下 %d 只：%s', len(_bk), sorted(_bk))
    for code in list(context.portfolio.positions):
        if (code not in target and code not in g.high_limit
                and code not in keep):
            order_target_value(code, 0)

    if g.buy_delay:
        g.pending = target
        return
    _do_buy(context, target)


# 🔴 只改**本实例**的 rebalance：`_base.initialize` 里写的是
#   `run_weekly(rebalance, ...)`，那是对 _base 模块全局的查找，
#   所以换掉它就等于换掉注册进引擎的那个函数。
#   而 `module_from_spec` 每次都是新对象 —— froec.py 的其它使用者
#   （froec_traded.py、实盘快照）完全不受影响。
_base.rebalance = rebalance


def initialize(context):
    g.paused_in_pool = getattr(g, 'paused_in_pool', 0)
    g.kcb_688_only = getattr(g, 'kcb_688_only', 0)
    g.weekday = getattr(g, 'weekday', 2)
    _base.initialize(context)
