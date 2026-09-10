# -*- coding: utf-8 -*-
"""FROEC-DSORT：**双重排序** —— 锁定市值暴露后，看 PB 还有没有边际贡献。

**要回答什么。** 网格显示"PB 收得越紧越差、完全不筛 PB 反而略好"，
但那里有个**混杂**：放松 PB 会让选中的票市值更小（固定 roe=0.10 时，
中位流通市值 23.0 亿 -> 16.1 亿），而这十年小市值本身年化 37% ——
所以"放松 PB 更好"里混着"市值更小更好"，网格分不开这两件事。

**怎么分开。** 标准的 double sort：

    第一重（锁定市值）  不筛 PB，按 floatmv 升序取**最小 N 只**（`ds_pool`）
    第二重（比因子）    在这 N 只里取 10 只：
                        ds_by='mv'  按市值再取前 10   <- 对照（≈ 现状）
                        ds_by='pb'  按 PB 升序取 10    <- 实验
                        ds_by='pbd' 按 PB **降序**取 10 <- 反向对照

★ 两组的市值都落在"全市场最小 N 只"里，暴露接近；差异主要来自 PB。
🔴 **必须有反向对照(`pbd`)**：只比 `pb` vs `mv` 的话，赢了也说不清是
  "低 PB 有效"还是"任何非市值排序都行"（换个排序就打散了市值集中度，
  那本身可能就是效果）。低 PB 若真有信息量，`pb` 应当 > `mv` > `pbd`。

参数：
    ds_pool  默认 50   第一重取最小多少只（N）
    ds_by    默认 'mv' 第二重按什么排（mv / pb / pbd）
    pb_pct   默认 1.0  这里默认**不筛 PB**（与 froec_cutgrid 不同）
    roe_pct  默认 0.1

🔴 `ds_by='mv'` + `ds_pool >= cand` 时必须与 `froec_cutgrid(pb_pct=1.0)`
  **逐位一致** —— 那是这个文件的自证（selftest 该钉）。

🔴🔴 **回测结论（2026-09-10）：锁定市值后 PB 完全没有边际贡献。**

池子完全相同（不筛 PB、ROE 前 10%、全市场最小 50 只），只差取哪 10 只：

    按市值（对照）  链乘年化 45.03%   平均回撤 21.0%   夏普 1.39
    按低 PB（实验）           25.68%            21.7%        1.07
    按高 PB（反向）           20.04%            26.1%        0.77

低PB vs 市值 **−17.68pp**（中位 −4.50，胜 5/11，t=−1.36）；
高PB vs 市值 −24.74pp（胜 **2/11**，t=−1.90）。
★ 方向自洽（低 PB > 高 PB）说明 PB 有一点信息量，但**远不如市值**。
🔴 这条实验的必要性：网格显示「放松 PB 更好」，而那里混着市值 ——
  放松 PB → 池子变大 → 选到更小的票（中位流通市值 23.0 亿 → 16.1 亿）。
  网格分不开这两件事，双重排序才能。
"""

import importlib.util
import os

_HERE = os.path.dirname(os.path.abspath(__file__))


def _private(name):
    spec = importlib.util.spec_from_file_location(
        'froec_dsort__traded', os.path.join(_HERE, name))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


_traded = _private('froec_traded.py')
_base = _traded._base

_ORDER = {
    'mv': 'ORDER BY floatmv ASC',
    'pb': 'ORDER BY pb ASC, jq_code',        # 显式 tie-break，见 froec_api 那条
    'pbd': 'ORDER BY pb DESC, jq_code',
}


def _patch_query():
    """改写候选池 SQL 的**末层排序与截断**，做成两重。

    ★ 只改末层的 `ORDER BY ... LIMIT`：前面所有 CTE（在册/EPS/ROE/PB）
      一个字不动，所以"池子"与原版同源，差异只在最后取哪 10 只。
    """
    g = _base.g

    def wrap(orig):
        def q(sql, **kw):
            if 'pbcut' not in kw or 'roecut' not in kw:
                return orig(sql, **kw)
            pp = float(getattr(g, 'pb_pct', 1.0))
            rp = float(getattr(g, 'roe_pct', 0.1))
            if not g.pb_fixed:
                kw['pbcut'] = 'floor(%r * n)' % pp
            if not g.roe_fixed:
                kw['roecut'] = 'floor(%r * n2)' % rp
            by = str(getattr(g, 'ds_by', 'mv'))
            npool = int(getattr(g, 'ds_pool', 50))
            tail = 'ORDER BY floatmv ASC LIMIT {cand} OFFSET {skip}'
            if tail not in sql:
                raise RuntimeError(
                    'froec 的末层排序变了 —— 本策略的替换锚点要跟着更新：%r'
                    % sql[-120:])
            # 🔴 第一重必须取到 `ds_pool` 只，第二重再取 `cand` 只。
            #   `OFFSET {skip}` 留在第二重（它是"跳过前 k 名"那个开关）。
            new = ('ORDER BY floatmv ASC LIMIT %d) '
                   'SELECT * FROM (SELECT * FROM _dspool %s '
                   'LIMIT {cand} OFFSET {skip})' % (npool, _ORDER[by]))
            sql2 = sql.replace(tail, new, 1)
            # 把末层包成 CTE：`SELECT ... FROM roe_top WHERE ...` -> `_dspool`
            head = sql2.rindex('SELECT jq_code, floatmv, pb, eps, roe_inc')
            sql2 = (sql2[:head] + ', _dspool AS (' + sql2[head:]) \
                if ' AS (' in sql2[:head] else sql2
            return orig(sql2, **kw)
        return q
    return wrap


def initialize(context):
    g = _base.g
    g.pb_pct = getattr(g, 'pb_pct', 1.0)       # ★ 这里默认**不筛** PB
    g.roe_pct = getattr(g, 'roe_pct', 0.1)
    g.ds_pool = getattr(g, 'ds_pool', 50)
    g.ds_by = getattr(g, 'ds_by', 'mv')
    if g.ds_by not in _ORDER:
        raise ValueError('ds_by 只能是 %s' % '/'.join(_ORDER))
    _traded.initialize(context)
    d = context.data
    if not getattr(d, '_dsort_wrapped', False):
        d.query = _patch_query()(d.query)
        d._dsort_wrapped = True


prepare = _base.prepare
rebalance = _base.rebalance
rebalance_buy = _base.rebalance_buy
check_limit_up = _base.check_limit_up
stop_check = _base.stop_check
pf_check = _base.pf_check
