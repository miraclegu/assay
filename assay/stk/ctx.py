# -*- coding: utf-8 -*-
"""这只票的上下文：事件、同行业、多股对比。"""

import datetime
import io
import json
import os
import re
import duckdb
from assay import symbols as _SYM          # 「代码->类别/名称」的唯一正本
from assay import paths as _paths   # datalake 根的唯一解析

from .base import StockError, _lake, _na, alt_kind, con, norm_code, panel


_EVENT_KINDS = {'xr': '除权除息', 'fin': '财报公告',
                'unlock': '解禁', 'share': '股本变动'}

def _wan(v):
    """股数转成人看得懂的量级。缺值给"—"，不给 0。"""
    if v is None:
        return '—'
    v = float(v)
    if abs(v) >= 1e8:
        return '%.2f 亿' % (v / 1e8)
    if abs(v) >= 1e4:
        return '%.0f 万' % (v / 1e4)
    return '%.0f' % v

# ================================ 事件 ================================
def events(code, since=None, root=None, kind=None):
    """打到 K 线上的事件：除权除息 / 财报公告 / 解禁 / 股本变动。

    ★ 分红用 **`a_xr_date`（除权日）** 定位到 K 线上 —— 那天价格才跳。
      用公告日标会标错位置（公告到除权常隔一两个月）。
    ★ 财报用 **`fin_pub_date`（公告日）**，不是报告期 —— 报告期那天市场
      还不知道这份财报。

    ## 两个单位陷阱（实测核过）

    · `share_change.share_total` 单位是 **万股**。601088 那行 2168943.4304
      × 1e4 = 216.9 亿股，与面板 `totalmv/close_bfq` **精确吻合**。
      直接显示会变成"总股本 216 万"，看着像个小公司。
    · `share_unlock.expected_unlimited_ratio` **常为 NULL**（601088 三条全空）。
      `round(None or 0, 2)` 会显示成"占 0%" —— 那不是"占比很小"，
      是**根本没有这个数**。缺值必须显示"—"。
    """
    alt = alt_kind(code, root, kind)
    if alt:
        return _na(alt, {'events': [], 'kinds': _EVENT_KINDS})
    jc = norm_code(code)
    if not jc:
        raise StockError('认不出代码：%r' % code)
    c = con()
    r = _lake(root)
    lo = "AND %s >= DATE '%s'"
    since = str(since)[:10] if since else None
    out = []

    def _q(sql, args=()):
        try:
            return c.execute(sql, list(args)).fetchall()
        except Exception:                                   # noqa: BLE001
            return []                       # 某张表缺了不该拖垮整页

    div = "read_parquet('%s/std/dividend.parquet')" % r
    for row in _q("""
            SELECT a_xr_date, bonus_ratio_rmb, board_plan_pub_date,
                   plan_progress, transfer_ratio
            FROM %s WHERE code = ? AND a_xr_date IS NOT NULL %s
            ORDER BY a_xr_date DESC LIMIT 40"""
            % (div, (lo % ('a_xr_date', since)) if since else ''), [jc]):
        # bonus_ratio_rmb 是【每 10 股派息】—— ÷10 才是每股
        ps = (row[1] / 10.0) if row[1] is not None else None
        out.append({'date': str(row[0])[:10], 'kind': 'xr', 'label': '除权除息',
                    'detail': ('每股派 %.4f 元' % ps if ps else '')
                              + ('　送转 %s' % row[4] if row[4] else ''),
                    'pub_date': str(row[2])[:10] if row[2] else None,
                    'note': row[3] or ''})
    for row in _q("""
            SELECT DISTINCT fin_pub_date, fin_report_date
            FROM %s WHERE jq_code = ? AND fin_pub_date IS NOT NULL %s
            ORDER BY fin_pub_date DESC LIMIT 24"""
            % (panel(root), (lo % ('fin_pub_date', since)) if since else ''), [jc]):
        out.append({'date': str(row[0])[:10], 'kind': 'fin', 'label': '财报公告',
                    'detail': '报告期 %s' % str(row[1])[:10]})
    unl = "read_parquet('%s/std/share_unlock.parquet')" % r
    for row in _q("""
            SELECT expected_unlimited_date, expected_unlimited_number,
                   expected_unlimited_ratio, shareholder_name
            FROM %s WHERE code = ? AND expected_unlimited_date IS NOT NULL %s
            ORDER BY expected_unlimited_date DESC LIMIT 20"""
            % (unl, (lo % ('expected_unlimited_date', since)) if since else ''), [jc]):
        # ★ ratio 常为 NULL —— 显示"—"而不是"占 0%"（后者不是"占比很小"，
        #   是根本没有这个数）
        out.append({'date': str(row[0])[:10], 'kind': 'unlock', 'label': '解禁',
                    'shares': row[1], 'ratio': row[2],
                    'detail': '%s 股%s　%s'
                              % (_wan(row[1]),
                                 ('（占 %.2f%%）' % row[2]) if row[2] else '',
                                 row[3] or '')})
    chg = "read_parquet('%s/std/share_change.parquet')" % r
    for row in _q("""
            SELECT change_date, change_reason, share_total
            FROM %s WHERE code = ? AND change_date IS NOT NULL %s
            ORDER BY change_date DESC LIMIT 20"""
            % (chg, (lo % ('change_date', since)) if since else ''), [jc]):
        # ★ share_total 单位是【万股】—— ×1e4 才是股。直接显示会变成
        #   "总股本 216 万"，看着像个小公司
        out.append({'date': str(row[0])[:10], 'kind': 'share', 'label': '股本变动',
                    'share_total': (row[2] * 1e4) if row[2] is not None else None,
                    'detail': '%s　总股本 %s股'
                              % (row[1] or '',
                                 _wan((row[2] * 1e4) if row[2] is not None else None))})
    out.sort(key=lambda x: x['date'], reverse=True)
    return {'code': jc, 'events': out, 'kinds': _EVENT_KINDS}

# ================================ 同业 ================================
def peers(code, n=20, root=None, kind=None):
    """同申万一级行业的票，按流通市值降序，并标出这只在其中的位置。"""
    alt = alt_kind(code, root, kind)
    if alt:
        return _na(alt, {'industry': None, 'rows': []})
    jc = norm_code(code)
    if not jc:
        raise StockError('认不出代码：%r' % code)
    c = con()
    p = panel(root)
    d = c.execute('SELECT max(date) FROM %s' % p).fetchone()[0]
    ind = c.execute("SELECT sw_l1_code, sw_l1_name FROM %s WHERE jq_code = ? "
                    'ORDER BY date DESC LIMIT 1' % p, [jc]).fetchone()
    if not ind or not ind[0]:
        return {'code': jc, 'industry': None, 'rows': []}
    rows = c.execute("""
        SELECT jq_code, sec_name, close_bfq, change_pct, turnover, floatmv,
               pe_ttm, pb, roe_ttm
        FROM %s WHERE date = DATE '%s' AND sw_l1_code = ?
          AND public_status IN ('正常上市','ST','*ST')
        ORDER BY floatmv DESC""" % (p, d), [ind[0]]).fetchall()
    keys = ['code', 'name', 'close', 'change_pct', 'turnover', 'floatmv',
            'pe_ttm', 'pb', 'roe_ttm']
    allr = [dict(zip(keys, r)) for r in rows]
    rank = next((i + 1 for i, x in enumerate(allr) if x['code'] == jc), None)
    return {'code': jc, 'date': str(d),
            'industry': {'code': ind[0], 'name': ind[1]},
            'total': len(allr), 'rank': rank,
            'rows': allr[:max(1, min(int(n or 20), 100))]}

# ================================ 多股对比 ================================
def compare(codes, n=250, root=None):
    """多只票的【后复权】归一化涨幅曲线。

    🔴 一律后复权：不复权跨除权日有**假跌幅**，对比图上会看成"这只票那天崩了"。
    ★ 交易日对齐用【并集】而不是交集：某只票停牌几天，用交集会把所有票
      那几天一起丢掉，曲线长度变短且看不出是谁停的。缺的那几天补 null，
      画图时断开。
    """
    cs = []
    for x in (codes or []):
        jc = norm_code(x)
        if not jc:
            raise StockError('认不出代码：%r' % x)
        if jc not in cs:
            cs.append(jc)
    if not cs:
        raise StockError('至少给一个代码')
    if len(cs) > 6:
        raise StockError('一次最多 6 只（再多曲线就分不清了），收到 %d' % len(cs))
    n = max(10, min(int(n or 250), 3000))
    c = con()
    p = panel(root)
    q = "','".join(cs)
    days = [r[0] for r in c.execute("""
        SELECT DISTINCT date FROM %s WHERE jq_code IN ('%s')
        ORDER BY date DESC LIMIT %d""" % (p, q, n)).fetchall()]
    days.sort()
    if not days:
        raise StockError('取不到行情')
    px = {}
    for cd, dt, v in c.execute("""
            SELECT jq_code, date, close_hfq FROM %s
            WHERE jq_code IN ('%s') AND date >= DATE '%s'"""
            % (p, q, days[0])).fetchall():
        px[(cd, dt)] = v
    nm = _names_map(c, cs, days[-1], root)
    series = []
    for cd in cs:
        base = next((px.get((cd, d)) for d in days if px.get((cd, d))), None)
        series.append({
            'code': cd, 'name': nm.get(cd, ''),
            'base': base,
            'ret': [None if (base in (None, 0) or px.get((cd, d)) is None)
                    else round(px[(cd, d)] / base - 1, 6) for d in days],
        })
    return {'dates': [d.isoformat() for d in days], 'series': series,
            'fq': 'hfq', 'n': len(days)}

def _names_map(c, codes, day, root=None):
    """代码 -> 名称。🔴 **只转发**给唯一正本 `symbols.names`（2026-09-21）。

    原来这里自己写了一遍「面板取最近非空 sec_name」那套 SQL，而
    `lv/px.names_of` / `lv/sig._names` 各有一份、`watchlist`/`alerts` 又各有
    一份 —— 五份实现、三种行为（谁回落 ETF、谁清洗 U+FFFD 都不一样）。
    ★ `c` 这个连接参数保留：调用点不用改，而正本自己开连接（取名不在热路径上）。
    """
    return _SYM.names(codes, day=day, root=_lake(root))
