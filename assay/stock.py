"""个股查询：搜索 / 基本面板 / K 线 / 财务时序。

看板「📈 个股」那一页的数据层。**只读**，全部来自 `mart/panel_daily`。

## 为什么不复用 live.py 里的取价函数

那些是给实盘算钱用的（`day_price` 拿不到就报错、`positions_valued` 要
FIFO 批次）。这里是【浏览】：拿不到就说"没有"，不该拦住整页。
两种语义混在一个函数里迟早互相拖累。

## 复权

面板只存不复权 OHLC + `close_hfq` + `hfq_factor`。实测
`close_bfq × hfq_factor == close_hfq`（差 <0.004，四舍五入），
所以后复权 OHLC 用 `× hfq_factor` 推得出来。

★ 默认给**不复权**：看盘的人对着券商软件看的就是它，而且
  `open` 正好是当日集合竞价成交价。
★ 但**跨除权日比较涨幅必须用后复权** —— 不复权在除权日会有个假跌幅。
  所以两种都给，页面上能切，并且把这条写在提示里。

## 单位（实测核过，别猜）

    change_pct   百分数    -1.49 = 跌 1.49%
    amplitude    百分数    3.5   = 振幅 3.5%
    turnover     百分数    0.088342 = 换手 0.0883%
                          （= 量×收盘价/流通市值×100，与字段精确吻合；
                            全市场中位数 2.13% —— 若当小数用会差 100 倍）
    volume_shares 股        聚宽的 volume 是"股×100"，面板这一列已换成股
    amount        元
    floatmv/totalmv 元
    roe_ttm / rev_yoy 等比率  **小数**（0.10 = 10%）

★ 同一张面板里"百分数"和"小数"是**混着**的 —— 所以 `FIELD_UNIT` 明确列出
  每个字段的单位，页面照它渲染，不靠"看数值大小猜"。猜错 100 倍不报错。
"""
import datetime
import os
import re

import duckdb

_PANEL = None


def _lake(root=None):
    r = root or os.environ.get('ASSAY_DATALAKE') or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        '..', 'datalake')
    r = os.path.normpath(r)
    if not os.path.isdir(r):
        raise StockError('找不到 datalake：%s（用 ASSAY_DATALAKE 指定）' % r)
    return r


class StockError(Exception):
    pass


def panel(root=None):
    return "read_parquet('%s/mart/panel_daily/panel_*.parquet')" % _lake(root)


def con():
    return duckdb.connect(':memory:')


# ---------------------------------------------------------------- 代码归一
_PREFIX = {'60': 'XSHG', '68': 'XSHG', '90': 'XSHG',
           '00': 'XSHE', '30': 'XSHE', '20': 'XSHE'}
_TOKEN = {'SH': 'XSHG', 'SS': 'XSHG', 'XSHG': 'XSHG',
          'SZ': 'XSHE', 'XSHE': 'XSHE'}
_RE_D6 = re.compile(r'\d{6}')


def norm_code(s):
    """`601857` / `601857.SH` / `sh601857` / `601857.XSHG` -> `601857.XSHG`。

    ★ 与 live.normalize_code 同一套规则，但这里**判不出来就返回 None**
      而不是抛错 —— 搜索框里每敲一个字都会调它，半个代码不是错误。
    """
    t = str(s or '').strip().upper()
    m = _RE_D6.search(t)
    if not m:
        return None
    num = m.group(0)
    rest = (t[:m.start()] + t[m.end():]).strip(' .-_')
    mk = _TOKEN.get(rest) if rest else None
    if rest and not mk:
        return None
    mk = mk or _PREFIX.get(num[:2])
    return '%s.%s' % (num, mk) if mk else None


# ---------------------------------------------------------------- 搜索
# ★ 字段单位表：页面照它渲染，不靠"看数值大小猜"。
#   同一张面板里百分数和小数是混着的，猜错 100 倍不报错。
FIELD_UNIT = {
    'change_pct': 'pct', 'amplitude': 'pct', 'turnover': 'pct',
    'limit_pct': 'pct',
    'rev_yoy': 'ratio', 'np_yoy': 'ratio', 'np_q_yoy': 'ratio',
    'rev_q_yoy': 'ratio', 'roe_ttm': 'ratio', 'roe_q': 'ratio',
    'roe_parent': 'ratio',
    'ret_5d': 'ratio', 'ret_20d': 'ratio', 'ret_60d': 'ratio',
    'ret_250d': 'ratio',
    'floatmv': 'yuan', 'totalmv': 'yuan', 'amount': 'yuan',
    'revenue': 'yuan', 'net_profit_parent': 'yuan', 'np_ttm': 'yuan',
    'rev_ttm': 'yuan', 'np_q': 'yuan', 'rev_q': 'yuan',
    'adjusted_profit_q': 'yuan',
    'volume_shares': 'shares',
}

_IDX = {'at': None, 'rows': None}


def _index(root=None):
    """(jq_code, symbol, sec_name, 行业, 状态) 全表。

    ★ 取【最新数据日】那一天的全市场 —— 5200 行，几毫秒，缓存在进程里。
      按数据日缓存而不是按时间：同步跑完换了新数据日就自动失效，
      不用猜"缓存该放多久"。
    """
    c = con()
    p = panel(root)
    day = c.execute('SELECT max(date) FROM %s' % p).fetchone()[0]
    if _IDX['at'] == day and _IDX['rows']:
        return _IDX['rows'], day
    rows = c.execute("""
        SELECT jq_code, symbol, sec_name, sw_l1_name, public_status, is_st,
               close_bfq, change_pct, floatmv
        FROM %s WHERE date = DATE '%s'""" % (p, day)).fetchall()
    out = [{'code': r[0], 'symbol': r[1], 'name': r[2] or '', 'industry': r[3] or '',
            'status': r[4] or '', 'is_st': bool(r[5]), 'close': r[6],
            'change_pct': r[7], 'floatmv': r[8]} for r in rows]
    _IDX.update(at=day, rows=out)
    return out, day


def search(q, limit=20, root=None):
    """按代码或名称搜。

    ★ 排序不是按字典序，而是按【匹配得分】：代码前缀命中 > 名称前缀命中 >
      名称包含 > 代码包含，同分再按流通市值降序。
      不排序的话搜"银行"第一条是随机的某只小银行，而人要的通常是大的那个。
    """
    rows, day = _index(root)
    t = str(q or '').strip().upper()
    if not t:
        return {'results': [], 'asof': str(day), 'total': len(rows)}
    d6 = _RE_D6.search(t)
    num = d6.group(0) if d6 else None
    out = []
    for r in rows:
        n6 = r['code'][:6]
        score = None
        if num and n6 == num:
            score = 0
        elif num and n6.startswith(num):
            score = 1
        elif r['name'] and r['name'].startswith(t):
            score = 2
        elif r['name'] and t in r['name']:
            score = 3
        elif t.isdigit() and t in n6:
            score = 4
        elif r['symbol'] and t.lower() in r['symbol'].lower():
            score = 5
        if score is not None:
            out.append((score, -(r['floatmv'] or 0), r))
    out.sort(key=lambda x: (x[0], x[1]))
    return {'results': [x[2] for x in out[:max(1, min(int(limit or 20), 50))]],
            'asof': str(day), 'total': len(rows), 'matched': len(out)}


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


def profile(code, root=None):
    """最新一天的全部关键字段 + 几个区间统计。"""
    jc = norm_code(code)
    if not jc:
        raise StockError('认不出代码：%r' % code)
    c = con()
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


# ---------------------------------------------------------------- K 线
def kline(code, n=250, fq='bfq', end=None, root=None):
    """日 K。`fq`：'bfq' 不复权（默认）/ 'hfq' 后复权。

    ★ 返回里带 ma5/10/20/60 —— 均线在**服务端**算：前端要算就得多取 60 根
      预热数据，而"少取了 60 根导致头部均线是错的"不会报错，只是曲线不对。
    ★ 后复权 OHLC 由不复权 × hfq_factor 推得（面板只存 close_hfq）。
    """
    jc = norm_code(code)
    if not jc:
        raise StockError('认不出代码：%r' % code)
    if fq not in ('bfq', 'hfq'):
        raise StockError("fq 只能是 bfq / hfq，收到 %r" % fq)
    n = max(10, min(int(n or 250), 3000))
    c = con()
    p = panel(root)
    # 多取 60 根用来预热均线，返回时切掉 —— 否则头 60 根的 ma60 是空的
    warm = 60
    where = 'jq_code = ?'
    args = [jc]
    if end:
        where += " AND date <= DATE '%s'" % datetime.date.fromisoformat(str(end))
    rows = c.execute("""
        SELECT date, open, high, low, close_bfq, close_hfq, hfq_factor,
               volume_shares, amount, change_pct, turnover,
               is_limit_up, is_limit_down, preclose
        FROM %s WHERE %s ORDER BY date DESC LIMIT %d""" % (p, where, n + warm),
        args).fetchall()
    if not rows:
        raise StockError('取不到 %s 的日线' % jc)
    rows = list(reversed(rows))
    out = []
    for r in rows:
        f = r[6] or 1.0
        o, h, l_, cb = r[1], r[2], r[3], r[4]
        if fq == 'hfq':
            o = o * f if o is not None else None
            h = h * f if h is not None else None
            l_ = l_ * f if l_ is not None else None
            cl = r[5] if r[5] is not None else (cb * f if cb is not None else None)
        else:
            cl = cb
        out.append({
            'date': r[0].isoformat(), 'open': _r3(o), 'high': _r3(h),
            'low': _r3(l_), 'close': _r3(cl),
            'volume': r[7], 'amount': r[8], 'change_pct': r[9],
            'turnover': r[10], 'limit_up': bool(r[11]), 'limit_down': bool(r[12]),
            'preclose': _r3(r[13]),
        })
    for w in (5, 10, 20, 60):
        _ma(out, w)
    return {'code': jc, 'fq': fq, 'bars': out[-n:], 'n': min(len(out), n),
            'warmup_dropped': max(0, len(out) - n)}


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
