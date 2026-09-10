# -*- coding: utf-8 -*-
"""FROEC-MVROE：市值取最小 x%，再按 **ROE 增速排序**取 10 只。

**与原版的根本区别 —— ROE 增速从「筛子」变成「排序因子」。**

    原版      PB前50% -> ROE增速前10%（筛） -> 按【市值】升序取 10
    本版      不筛 PB -> 按市值取最小 x%    -> 按【ROE 增速】降序取 10

★ 为什么这个对比有意义：原版里 ROE 只决定"进不进池子"，进了之后完全
  不影响排序 —— 也就是说它**没有强度信息**（ROE 加速度第 1 名和第 162 名
  在后面一视同仁）。本版反过来：市值只锁定范围，ROE 决定挑谁。
  两者哪个更好，直接回答"ROE 增速到底是筛子还是因子"。

参数：
    mv_pct   默认 0.10   第一重：按流通市值取最小多少比例
    mv_by    默认 'roe'  第二重：'roe' 按 ROE 增速降序 | 'mv' 按市值升序（对照）
    roe_pct  默认 1.0    ★ 默认**不筛** ROE（它现在是排序因子，不该再当筛子）
    pb_pct   默认 1.0    ★ 默认**不筛** PB（三轮实验已证它无边际贡献）

🔴 `mv_by='mv'` 时必须与 `froec_cutgrid(pb_pct=1.0, roe_pct=1.0)` 在
  `mv_pct=1.0` 下**逐位一致** —— 那是这个文件的自证。
🔴 **第一重用比例而不是固定只数**：全市场只数天天微变（实测 5202~5208），
  固定只数会让"最小 10%"的含义随时间漂移（同 is_stale 那条：
  固定阈值迟早过时，而过时的表现是静默的）。

🔴🔴 **回测结论（2026-09-10）：ROE 增速的【方向性】是本项目最扎实的一个结论。**

降序 vs 升序（同一个池子，只反转排序方向；逐年独立 2016~2026）：

    市值最小 5%    +31.02pp   中位 +27.80   胜 10/11   **t=+3.56**
    市值最小 10%   +35.46pp   中位 +43.96   胜 10/11   **t=+4.56**
    市值最小 15%   +28.10pp   中位 +27.25   胜 **11/11**  **t=+4.75**
    市值最小 20%   +19.26pp   中位 +10.60   胜  7/11     t=+1.77

三档 |t|>2.23、方向与幅度都一致 —— 比 ROE 同比那次（t=−4.75，单点）更扎实，
因为它在**四个独立的市值档上重复出现**。
🔴 **反向对照（`mv_by='roed'`）是这条结论成立的关键**：只比「ROE 排序 vs
  市值排序」的话，赢了也说不清是「ROE 有效」还是「任何非市值排序都行」。

★ 但**把 ROE 当排序因子并不比当筛子好**：`mv10%+ROE排序` vs 原版
  均差 −1.93pp（t=−0.23），四档全为负（−5.13 / −1.93 / −6.73 / −16.87）。
  同池内 ROE 排序 vs 市值排序也只有 +4~9pp、t 都不到 1。
★ 所以**原版的分工是对的**：ROE 当筛子（有强度阈值）、市值当排序（有单调关系）。
"""

import importlib.util
import os

_HERE = os.path.dirname(os.path.abspath(__file__))


def _private(name):
    spec = importlib.util.spec_from_file_location(
        'froec_mvroe__traded', os.path.join(_HERE, name))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


_traded = _private('froec_traded.py')
_base = _traded._base

_ORDER2 = {
    # ★ 全部带显式 tie-break（按代码）—— froec 原 SQL 的 `ORDER BY increase
    #   DESC` 没有 tie-break，实测 3.9% 的调仓日切点并列，结果不可复现。
    'roe': 'ORDER BY roe_inc DESC, jq_code',
    'mv': 'ORDER BY floatmv ASC, jq_code',
    'roed': 'ORDER BY roe_inc ASC, jq_code',      # 反向对照
}

_TAIL = 'ORDER BY floatmv ASC LIMIT {cand} OFFSET {skip}'


def _patch_query():
    g = _base.g

    def wrap(orig):
        def q(sql, **kw):
            if 'pbcut' not in kw or 'roecut' not in kw:
                return orig(sql, **kw)
            if not g.pb_fixed:
                kw['pbcut'] = 'floor(%r * n)' % float(getattr(g, 'pb_pct', 1.0))
            if not g.roe_fixed:
                kw['roecut'] = 'floor(%r * n2)' % float(getattr(g, 'roe_pct', 1.0))
            if _TAIL not in sql:
                raise RuntimeError('froec 末层排序变了，替换锚点要更新：%r'
                                   % sql[-140:])
            pct = float(getattr(g, 'mv_pct', 0.10))
            by = str(getattr(g, 'mv_by', 'roe'))
            # 第一重：按市值升序打名次，取最小 pct 比例；第二重：按 by 排序取 cand
            new = ('), _mvpool AS (\n'
                   '  SELECT * FROM (SELECT *, row_number() OVER '
                   '(ORDER BY floatmv ASC) mv_rn, count(*) OVER () mv_n '
                   'FROM _dsbase)\n'
                   '  WHERE mv_rn <= greatest(1, floor(%r * mv_n))\n'
                   ')\n'
                   'SELECT * FROM _mvpool %s LIMIT {cand} OFFSET {skip}'
                   % (pct, _ORDER2[by]))
            head = sql.rindex('SELECT jq_code, floatmv, pb, eps, roe_inc')
            sql2 = (sql[:head] + ', _dsbase AS (\n' + sql[head:]).replace(
                _TAIL, new, 1)
            return orig(sql2, **kw)
        return q
    return wrap


def initialize(context):
    g = _base.g
    g.pb_pct = getattr(g, 'pb_pct', 1.0)
    g.roe_pct = getattr(g, 'roe_pct', 1.0)
    g.mv_pct = getattr(g, 'mv_pct', 0.10)
    g.mv_by = getattr(g, 'mv_by', 'roe')
    if g.mv_by not in _ORDER2:
        raise ValueError('mv_by 只能是 %s' % '/'.join(_ORDER2))
    _traded.initialize(context)
    d = context.data
    if not getattr(d, '_mvroe_wrapped', False):
        d.query = _patch_query()(d.query)
        d._mvroe_wrapped = True


prepare = _base.prepare
rebalance = _base.rebalance
rebalance_buy = _base.rebalance_buy
check_limit_up = _base.check_limit_up
stop_check = _base.stop_check
pf_check = _base.pf_check
