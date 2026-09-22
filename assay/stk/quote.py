# -*- coding: utf-8 -*-
"""行情与基本面：K 线、报价估值、按报告期的财务。"""

import datetime
import io
import json
import os
import re
import duckdb
from assay import symbols as _SYM          # 「代码->类别/名称」的唯一正本
from assay import paths as _paths   # datalake 根的唯一解析

from .base import StockError, _lake, _na, alt_kind, con, norm_code, panel


def alt_panel(kind, root=None):
    """ETF / 指数的日线拼成与面板同形的子查询。**只转发**给正本。"""
    return _SYM.alt_panel(kind, _lake(root))

# ---------------------------------------------------------------- 个股面板
_PROFILE_COLS = [
    'date', 'jq_code', 'symbol', 'sec_name', 'close_bfq', 'open', 'high', 'low',
    'preclose', 'change_pct', 'amplitude', 'turnover', 'volume_shares', 'amount',
    'hfq_factor', 'floatmv', 'totalmv', 'pb', 'pe_ttm', 'ps_ttm', 'roe_ttm',
    'peg', 'np_ttm', 'rev_ttm', 'rev_yoy', 'np_yoy', 'np_q', 'rev_q',
    'np_q_yoy', 'rev_q_yoy', 'eps_q', 'roe_q', 'eps_basic', 'roe_parent',
    'bps', 'revenue', 'net_profit_parent', 'adjusted_profit_q',
    'fin_report_date', 'fin_pub_date', 'sw_l1_code', 'sw_l1_name',
    'is_st', 'is_risk_warned', 'public_status', 'list_date', 'listed_days',
    'limit_pct', 'limit_up', 'limit_down', 'is_limit_up', 'is_limit_down',
    'in_hs300', 'in_zz500', 'in_zz1000', 'in_sz50', 'in_cyb', 'in_kc50',
    'in_zz800',
]

_INDEX_TAG = [('in_sz50', '上证50'), ('in_hs300', '沪深300'),
              ('in_zz500', '中证500'), ('in_zz800', '中证800'),
              ('in_zz1000', '中证1000'), ('in_cyb', '创业板指'),
              ('in_kc50', '科创50')]

# ETF / 指数在面板里没有的那一大片（估值、财务、行业、涨跌停、指数成分）。
# ★ 一律给 None 而**不是省略**：页面按固定的键取值，少一个键是 `undefined`，
#   而 `undefined` 在页面上和"这只票没有这项"长得一模一样 —— 前者是 bug、
#   后者是事实，混在一起就再也分不出来了。
_ALT_PROFILE_NULL = [c for c in _PROFILE_COLS if c not in (
    'date', 'jq_code', 'symbol', 'sec_name', 'close_bfq', 'open', 'high', 'low',
    'preclose', 'change_pct', 'amplitude', 'volume_shares', 'amount',
    'hfq_factor')]

def profile(code, root=None):
    """最新一天的全部关键字段 + 几个区间统计。"""
    c = con()
    alt = alt_kind(code, root)
    if alt:
        return _alt_profile(alt[0], alt[1], c, root)
    jc = norm_code(code)
    if not jc:
        raise StockError('认不出代码：%r' % code)
    p = panel(root)
    row = c.execute(
        'SELECT %s FROM %s WHERE jq_code = ? ORDER BY date DESC LIMIT 1'
        % (', '.join(_PROFILE_COLS), p), [jc]).fetchone()
    if row is None:
        raise StockError('面板里没有 %s —— 可能没上市 / 已退市 / 代码写错' % jc)
    d = dict(zip(_PROFILE_COLS, row))
    d['indexes'] = [nm for k, nm in _INDEX_TAG if d.get(k)]
    # 区间涨幅一律用【后复权】—— 不复权跨除权日会有假跌幅
    stats = c.execute("""
        WITH h AS (SELECT date, close_hfq, high, low, close_bfq
                   FROM %s WHERE jq_code = ? ORDER BY date DESC LIMIT 250)
        SELECT max(high), min(low),
               (SELECT close_hfq FROM h ORDER BY date DESC LIMIT 1),
               (SELECT close_hfq FROM h ORDER BY date LIMIT 1),
               count(*) FROM h""" % p, [jc]).fetchone()
    d['high_52w'], d['low_52w'] = stats[0], stats[1]
    d['ret_250d'] = (round(stats[2] / stats[3] - 1, 6)
                     if stats[2] and stats[3] else None)
    d['n_bars'] = stats[4]
    for n in (5, 20, 60):
        r = c.execute("""
            WITH h AS (SELECT date, close_hfq FROM %s WHERE jq_code = ?
                       ORDER BY date DESC LIMIT %d)
            SELECT (SELECT close_hfq FROM h ORDER BY date DESC LIMIT 1),
                   (SELECT close_hfq FROM h ORDER BY date LIMIT 1)""" % (p, n + 1),
            [jc]).fetchone()
        d['ret_%dd' % n] = (round(r[0] / r[1] - 1, 6) if r and r[0] and r[1]
                            else None)
    return d

def _alt_profile(kind, sym, c, root=None):
    """ETF / 指数的 profile —— 与股票**同一份键**，没有的那些一律 None。

    🔴 `kind` 要给页面（`asset_kind`）：这一页有一半的块（财务、所属板块、
      同行业）对 ETF / 指数**本来就不存在**，页面要**明说"没有这项"**
      而不是留白 —— 留白会被读成"数据没取到"（同「删了必须留痕，
      否则'空'与'本来就没有'分不出来」那条）。
    """
    p = alt_panel(kind, root)
    row = c.execute(
        """SELECT date, jq_code, symbol, date, close_bfq, open, high, low,
                  preclose, change_pct, volume_shares, amount, hfq_factor
           FROM %s WHERE jq_code = ? ORDER BY date DESC LIMIT 1""" % p,
        [sym]).fetchone()
    if row is None:
        raise StockError('取不到 %s 的日线 —— 代码写错 / 本地没有它' % sym)
    nm = _alt_name(sym, root)
    d = dict((k, None) for k in _ALT_PROFILE_NULL)
    d.update({
        'date': row[0], 'jq_code': row[1], 'symbol': row[2], 'sec_name': nm,
        'close_bfq': row[4], 'open': row[5], 'high': row[6], 'low': row[7],
        'preclose': row[8], 'change_pct': row[9], 'volume_shares': row[10],
        'amount': row[11], 'hfq_factor': row[12],
        'amplitude': (round((row[6] - row[7]) / row[8] * 100, 4)
                      if row[6] is not None and row[7] is not None and row[8]
                      else None),
    })
    d['indexes'] = []
    d['asset_kind'] = kind
    stats = c.execute("""
        WITH h AS (SELECT date, close_hfq, high, low FROM %s
                   WHERE jq_code = ? ORDER BY date DESC LIMIT 250)
        SELECT max(high), min(low),
               (SELECT close_hfq FROM h ORDER BY date DESC LIMIT 1),
               (SELECT close_hfq FROM h ORDER BY date LIMIT 1),
               count(*) FROM h""" % p, [sym]).fetchone()
    d['high_52w'], d['low_52w'] = stats[0], stats[1]
    d['ret_250d'] = (round(stats[2] / stats[3] - 1, 6)
                     if stats[2] and stats[3] else None)
    d['n_bars'] = stats[4]
    for n in (5, 20, 60):
        r = c.execute("""
            WITH h AS (SELECT date, close_hfq FROM %s WHERE jq_code = ?
                       ORDER BY date DESC LIMIT %d)
            SELECT (SELECT close_hfq FROM h ORDER BY date DESC LIMIT 1),
                   (SELECT close_hfq FROM h ORDER BY date LIMIT 1)""" % (p, n + 1),
            [sym]).fetchone()
        d['ret_%dd' % n] = (round(r[0] / r[1] - 1, 6) if r and r[0] and r[1]
                            else None)
    return d

def _alt_name(sym, root=None):
    """ETF / 指数的中文名。**只转发**给正本（名称清洗也在那边）。"""
    return _SYM.alt_name(sym, _lake(root))

# ---------------------------------------------------------------- K 线
def kline(code, n=250, fq=None, end=None, off=0, root=None):
    """日 K。`fq`：'bfq' 不复权 / 'qfq' 前复权 / 'hfq' 后复权。

    **不给 `fq` = 让服务端按标的类别定**：ETF 前复权、其余不复权（见下）。

    `off` = **往回翻几根**（跳过最新的 off 根）。K 线图左右移动用它。
    🔴 **不用「把 end 往前挪」来实现翻页** —— 那要前端自己算交易日，
      而前端没有交易日历（本项目为此栽过：硬编码判据误报了一整页假告警）。
      按**根数**偏移是纯索引运算，服务端一句 OFFSET 就够。
    ★ 预热那 60 根仍然取在窗口**之外**（DESC + OFFSET 之后再多取 warm 根），
      所以翻到哪一页，那一页的 ma60 都是对的 —— 否则每翻一页头部均线就缺一截，
      而它不报错，只是曲线不对。

    ★ 返回里带 ma5/10/20/60 —— 均线在**服务端**算：前端要算就得多取 60 根
      预热数据，而"少取了 60 根导致头部均线是错的"不会报错，只是曲线不对。
    ★ 后复权 OHLC 由不复权 × hfq_factor 推得（面板只存 close_hfq）。
    """
    # 🔴 **默认口径：一律前复权**（不给 `fq` 时）。
    #   用户 2026-09-16 分两次定下来的：先是"ETF 应该默认前复权"，
    #   然后"股票也要默认前复权"。理由是同一条 —— **主流行情软件的默认
    #   就是它**，而前复权两头都占：最新一根等于当前实际价（与券商对得上），
    #   历史按分红往下调、跨除权日没有假跌幅。
    #   ★ 被这条取代的旧理由值得记一笔：原来默认不复权是因为"对着券商软件
    #     看的是它，且 `open` 就是当日集合竞价成交价"。后半句在前复权下
    #     **不再成立**（open 也被缩放了）—— 要照竞价价下单时得手动切回
    #     不复权。这是明知的取舍，不是漏掉。
    #   ★ 指数走这条也无妨：它不除权、因子表里没有它的行，
    #     `coalesce(..., 1)` 让 qfq 与 bfq **逐位相同**。
    #   🔴 默认放在**服务端**而不是页面：页面要先拿到 profile 才知道标的
    #     类别，那就得把 K 线那一发排到它后面（现在十个接口是并发的）——
    #     而"多等一个往返"是看得见的（同「可选清单由服务端给」那条）。
    if not fq:
        fq = 'qfq'
    if fq not in ('bfq', 'qfq', 'hfq'):
        raise StockError("fq 只能是 bfq / qfq / hfq，收到 %r" % fq)
    n = max(10, min(int(n or 250), 3000))
    off = max(0, int(off or 0))
    c = con()
    # ETF / 指数不在面板里 —— 换一张**同形**的表，下面一个字都不用改。
    alt = alt_kind(code, root)
    if alt:
        jc, p = alt[1], alt_panel(alt[0], root)
    else:
        jc = norm_code(code)
        if not jc:
            raise StockError('认不出代码：%r' % code)
        p = panel(root)
    # 多取 max(MA) 根用来预热均线，返回时切掉 —— 否则头几根的长均线是空的。
    # 🔴 **不许写死**（原来是 `warm = 60`）：2026-09-21 加 MA120 时，
    #   写死 60 会让 MA120 在**每个窗口的头 60 根**都是 null，
    #   而它不报错 —— 图上只是那条线短了一截，没人会注意。
    #   浮层默认只取 120 根，那条线会整条为空。跟着 `MA_PERIODS` 走就对了。
    warm = max(MA_PERIODS)
    where = 'jq_code = ?'
    args = [jc]
    if end:
        where += " AND date <= DATE '%s'" % datetime.date.fromisoformat(str(end))
    rows = c.execute("""
        SELECT date, open, high, low, close_bfq, close_hfq, hfq_factor,
               volume_shares, amount, change_pct, turnover,
               is_limit_up, is_limit_down, preclose
        FROM %s WHERE %s ORDER BY date DESC LIMIT %d OFFSET %d"""
        % (p, where, n + warm, off), args).fetchall()
    if not rows:
        raise StockError('取不到 %s 的日线' % jc)
    rows = list(reversed(rows))
    # 🔴🔴 **前复权的基准是"今天"，不是"这一屏的最后一根"。**
    #   `qfq = 不复权 × 因子 ÷ 【全局最新】因子` —— 拿窗口内最后一根的因子
    #   当分母的话，往回翻页时同一天的价格会**跟着翻页变**，
    #   而它不报错（图看着一切正常，只是纵轴悄悄换了一套刻度）。
    #   所以这里单独查一次全局最新因子，不复用上面那批行。
    qf = 1.0
    if fq == 'qfq':
        r0 = c.execute(
            'SELECT hfq_factor FROM %s WHERE jq_code = ? AND hfq_factor IS NOT NULL'
            ' ORDER BY date DESC LIMIT 1' % p, [jc]).fetchone()
        qf = (r0[0] if r0 and r0[0] else 1.0) or 1.0
    out = []
    for r in rows:
        f = r[6] or 1.0
        o, h, l_, cb = r[1], r[2], r[3], r[4]
        k = 1.0
        if fq in ('hfq', 'qfq'):
            k = f / qf if fq == 'qfq' else f      # qfq 时 qf 是全局最新因子
            o = o * k if o is not None else None
            h = h * k if h is not None else None
            l_ = l_ * k if l_ is not None else None
            # 🔴 前复权的 close **跟 OHLC 走同一条路**（`不复权 × k`），
            #   不要拿面板的 `close_hfq` 再除一次：那两条路**各自舍入**，
            #   于是会出现 `close 10.666 > high 10.664` 这种不自洽的行 ——
            #   既有那条「OHLC 自洽」的断言当场抓到。
            #   ★ 后复权仍用 `close_hfq`：它是面板的**权威值**，而且改了会动
            #     既有行为（等价性对过 21 项指纹）。
            #   ⚠ 已知的代价：后复权下 OHLC 会有极少数行不自洽
            #     （实测 601857 全程 250 根里 4 行，close 比 high 大 0.004）——
            #     因为 close 用权威值、OHLC 用 `close_bfq × hfq_factor` 推，
            #     两者各自舍入（文件头那条"差 <0.004"说的就是它）。
            #     不去"修"它：权威值优先，而偏差小于一个最小价位变动。
            cl = (r[5] if (fq == 'hfq' and r[5] is not None)
                  else (cb * k if cb is not None else None))
        else:
            cl = cb
        out.append({
            'date': r[0].isoformat(), 'open': _r3(o), 'high': _r3(h),
            'low': _r3(l_), 'close': _r3(cl),
            'volume': r[7], 'amount': r[8], 'change_pct': r[9],
            'turnover': r[10], 'limit_up': bool(r[11]), 'limit_down': bool(r[12]),
            # 🔴 昨收也要跟着 fq 缩放 —— 原来是**原样透传**，于是后复权图上
            #   「昨收 10.5、收 25.4」自相矛盾（2026-09-22 查 ETF 涨跌幅时
            #   顺带发现，**所有标的都有**）。
            #   ★ 缩放系数就是今天这个 `k`：面板的 preclose 是**除权后**昨收，
            #     `preclose × f(t) = close_bfq(t-1) × f(t-1)` 正好是昨日后复权收。
            #     于是 `close / preclose - 1 == change_pct` 在**三种口径下都成立**。
            'preclose': _r3(r[13] * k if r[13] is not None else None),
            # 这一天的**换算系数**（当前坐标价 ÷ 不复权价），bfq 恒 1。
            # 🔴 给出来是为了让页面能把**买卖点**（成交价是不复权实际价）
            #   画到同一套坐标上 —— 前端自己再存一份不复权 bars 去比的话，
            #   就是同一份信息两处算（而且翻页、换区间都要各自跟着变）。
            'fqk': (round(f / qf, 8) if fq == 'qfq'
                    else (round(f, 8) if fq == 'hfq' else 1.0)),
        })
    for w in MA_PERIODS:
        _ma(out, w)
    # 这只票总共有多少根 —— 前端据此知道还能不能往左翻（到头了要说，
    # 不能让「←」点了没反应：同「给一个点了没反应的按钮比不给更糟」）。
    total = c.execute('SELECT count(*) FROM %s WHERE %s' % (p, where),
                      args).fetchone()[0]
    return {'code': jc, 'fq': fq, 'bars': out[-n:], 'n': min(len(out), n),
            'off': off, 'total': int(total),
            'warmup_dropped': max(0, len(out) - n)}

# 主图均线的周期。🔴 **预热（`warm`）跟着它走**，别再写死一个数。
# ⚠ 这一份与 `assay/indicators.py` 的 `ma` Spec 是**两份实现** ——
#   主图均线由 `kline` 直接算、配色与画法在 `kchart.js` 里也写死一份，
#   而 CLAUDE.md 说的「加一个指标 = 一条 Spec，全在一处声明」对 MA
#   **并不成立**。2026-09-21 加 40/120 时发现，记在这儿；
#   把它并进 Spec 是另一轮的事，这一轮只按用户要的加两条。
MA_PERIODS = (5, 10, 20, 40, 60, 120)


def _r3(x):
    return None if x is None else round(float(x), 3)

def _ma(bars, w):
    k = 'ma%d' % w
    s = 0.0
    for i, b in enumerate(bars):
        s += b['close'] or 0
        if i >= w:
            s -= bars[i - w]['close'] or 0
        b[k] = round(s / w, 3) if i >= w - 1 else None

# ---------------------------------------------------------------- 财务时序
def finance(code, n=16, root=None):
    """按报告期的财务时序，一个报告期一行。

    ★ 同时给 `pub_date` —— 报告期 ≠ 公告日，而"用报告期当可见日"
      就是未来函数。页面上两个都显示。

    ★ 去重用「每个报告期取**最新那天面板行**」，不用 SELECT DISTINCT ——
      面板每个交易日都会重复一遍当期财务，而 DISTINCT 只要有一列差一点
      （财报重述改了某个值）就会给出两行同报告期的记录，页面上看着像
      重复渲染。取最新那行还顺带拿到**重述后**的值。
    """
    alt = alt_kind(code, root)
    if alt:
        return _na(alt, {'rows': []})
    jc = norm_code(code)
    if not jc:
        raise StockError('认不出代码：%r' % code)
    c = con()
    rows = c.execute("""
        SELECT fin_report_date, fin_pub_date, revenue,
               net_profit_parent, eps_basic, roe_parent, bps,
               np_q, rev_q, np_q_yoy, rev_q_yoy, adjusted_profit_q
        FROM (
          SELECT *, row_number() OVER (PARTITION BY fin_report_date
                                       ORDER BY date DESC) rn
          FROM %s WHERE jq_code = ? AND fin_report_date IS NOT NULL
        ) WHERE rn = 1
        ORDER BY fin_report_date DESC LIMIT %d""" % (panel(root), int(n)),
        [jc]).fetchall()
    keys = ['report_date', 'pub_date', 'revenue', 'net_profit_parent',
            'eps_basic', 'roe_parent', 'bps', 'np_q', 'rev_q', 'np_q_yoy',
            'rev_q_yoy', 'adjusted_profit_q']
    return {'code': jc, 'rows': [dict(zip(keys, r)) for r in rows]}
