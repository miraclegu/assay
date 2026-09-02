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
import json
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


# ================================ 副图指标 ================================
# ★ 全部在【服务端】算并预热，同 MA 的理由：前端自己算就得多取预热数据，
#   而"少取了导致头部值是错的"**不报错**，只是曲线不对。
#   预热长度按最长的那个指标定（MACD 的 EMA26 + DEA9，取 120 根够）。
IND_WARM = 120


def _ema(xs, n):
    """EMA。第一个值用 SMA 起步（而不是直接拿首值），少一点起步偏差。"""
    out = [None] * len(xs)
    if len(xs) < n:
        return out
    s = sum(xs[:n]) / n
    out[n - 1] = s
    k = 2.0 / (n + 1)
    for i in range(n, len(xs)):
        s = xs[i] * k + s * (1 - k)
        out[i] = s
    return out


def indicators(code, n=250, fq='bfq', end=None, root=None,
               macd=(12, 26, 9), kdj=(9, 3, 3), rsi=(6, 12, 24), boll=(20, 2)):
    """MACD / KDJ / RSI / BOLL。

    参数默认值取通用口径（MACD 12/26/9、KDJ 9/3/3、RSI 6/12/24、BOLL 20±2σ）。

    ★ KDJ 用**通用平滑**（K = 2/3·前K + 1/3·RSV，即 SMA(3) 的递推形式），
      与通达信/同花顺一致。用简单移动平均会和券商软件对不上，
      而"对不上"会让人以为数据错了。
    ★ BOLL 的 σ 用**总体标准差**（除 N，不是 N−1）—— 同上，与行情软件一致。
    """
    # ★ n 可能是 URL 里来的字符串 —— 在【入口】转 int，不要指望调用方转。
    #   直接 n + IND_WARM 会抛 "can only concatenate str"，
    #   而那个报错完全指不到"参数没转型"这件事上。
    n = max(10, min(int(n or 250), 3000))
    k = kline(code, n=n + IND_WARM, fq=fq, end=end, root=root)
    bars = k['bars']
    cl = [b['close'] for b in bars]
    hi = [b['high'] for b in bars]
    lo = [b['low'] for b in bars]
    m = len(bars)
    out = [{'date': b['date'], 'close': b['close']} for b in bars]

    # ---- MACD ----
    f, sl, sg = macd
    ef, es = _ema(cl, f), _ema(cl, sl)
    dif = [(ef[i] - es[i]) if (ef[i] is not None and es[i] is not None) else None
           for i in range(m)]
    got = [x for x in dif if x is not None]
    dea_raw = _ema(got, sg)
    off = m - len(got)
    for i in range(m):
        d = dif[i]
        e = dea_raw[i - off] if i >= off else None
        out[i]['dif'] = _r3(d)
        out[i]['dea'] = _r3(e)
        out[i]['macd'] = _r3((d - e) * 2) if (d is not None and e is not None) else None

    # ---- KDJ ----
    pk, a1, a2 = kdj
    kk = dd = 50.0
    for i in range(m):
        if i < pk - 1:
            out[i]['k'] = out[i]['d'] = out[i]['jj'] = None
            continue
        h = max(x for x in hi[i - pk + 1:i + 1] if x is not None)
        l_ = min(x for x in lo[i - pk + 1:i + 1] if x is not None)
        rsv = 50.0 if h == l_ else (cl[i] - l_) / (h - l_) * 100
        kk = ((a1 - 1) * kk + rsv) / a1
        dd = ((a2 - 1) * dd + kk) / a2
        out[i]['k'] = round(kk, 2)
        out[i]['d'] = round(dd, 2)
        out[i]['jj'] = round(3 * kk - 2 * dd, 2)

    # ---- RSI ----
    for w in rsi:
        key = 'rsi%d' % w
        up = dn = 0.0
        for i in range(m):
            if i == 0:
                out[i][key] = None
                continue
            ch = cl[i] - cl[i - 1]
            u, v = max(ch, 0.0), max(-ch, 0.0)
            if i <= w:
                up += u / w
                dn += v / w
                out[i][key] = (round(up / (up + dn) * 100, 2)
                               if i == w and (up + dn) else None)
            else:
                up = (up * (w - 1) + u) / w
                dn = (dn * (w - 1) + v) / w
                out[i][key] = round(up / (up + dn) * 100, 2) if (up + dn) else None

    # ---- BOLL ----
    bw, bk = boll
    for i in range(m):
        if i < bw - 1:
            out[i]['mb'] = out[i]['ub'] = out[i]['lb'] = None
            continue
        seg = cl[i - bw + 1:i + 1]
        mu = sum(seg) / bw
        sd = (sum((x - mu) ** 2 for x in seg) / bw) ** 0.5   # 总体标准差
        out[i]['mb'] = round(mu, 3)
        out[i]['ub'] = round(mu + bk * sd, 3)
        out[i]['lb'] = round(mu - bk * sd, 3)

    return {'code': k['code'], 'fq': fq, 'n': min(n, m),
            'warmup_dropped': max(0, m - n), 'rows': out[-n:],
            'params': {'macd': list(macd), 'kdj': list(kdj),
                       'rsi': list(rsi), 'boll': list(boll)}}


# ================================ 事件 ================================
def events(code, since=None, root=None):
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
    return {'code': jc, 'events': out,
            'kinds': {'xr': '除权除息', 'fin': '财报公告',
                      'unlock': '解禁', 'share': '股本变动'}}


# ================================ 同业 ================================
def peers(code, n=20, root=None):
    """同申万一级行业的票，按流通市值降序，并标出这只在其中的位置。"""
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
    rows = c.execute("""
        SELECT code, sec_name FROM (
          SELECT jq_code AS code, sec_name,
                 row_number() OVER (PARTITION BY jq_code ORDER BY date DESC) rn
          FROM %s WHERE jq_code IN ('%s') AND date <= DATE '%s'
        ) WHERE rn = 1""" % (panel(root), "','".join(codes), day)).fetchall()
    return {a: b for a, b in rows if b}


# ========================= 与实盘 / 回测联动 =========================
def links(code, root=None):
    """这只票和我的实盘、回测有什么关系。

    ★ 数据全是现成的，只是原来没连起来：看个股时最想知道的两件事就是
      "我持有它吗、成本多少" 和 "我哪次回测选过它、当时什么参数"。
    ★ 任何一半取不到都不该拖垮整页 —— 各自 try，缺的那半标 error。
    """
    jc = norm_code(code)
    if not jc:
        raise StockError('认不出代码：%r' % code)
    out = {'code': jc, 'positions': [], 'runs': [], 'watch': None}

    # ---- 实盘持仓（逐账户）----
    try:
        from assay import live as lv
        for a in lv.load_accounts():
            if a.get('archived'):
                continue
            P = lv.positions_valued(a['id'])
            for it in P.get('items') or []:
                if it['code'] == jc:
                    out['positions'].append({
                        'account': a['id'], 'account_name': a.get('name') or a['id'],
                        'shares': it['shares'], 'cost': it['cost'],
                        'cost_net': it.get('cost_net'), 'price': it['price'],
                        'value': it['value'], 'pnl': it['pnl'],
                        'pnl_pct': it['pnl_pct'], 'weight': it['weight'],
                        'entry': it['entry'],
                        'breakeven': it.get('breakeven'),
                        'exit_fee_est': it.get('exit_fee_est')})
    except Exception as e:                                  # noqa: BLE001
        out['positions_error'] = '%s: %s' % (type(e).__name__, e)

    # ---- 自选 ----
    try:
        from assay import watchlist as wl
        hit = [x for x in wl.current() if x['code'] == jc]
        out['watch'] = hit[0] if hit else None
    except Exception as e:                                  # noqa: BLE001
        out['watch_error'] = '%s: %s' % (type(e).__name__, e)

    # ---- 回测里买过它的那些 run ----
    #   ★ 只看归档里的 trades —— 不重跑回测。重跑要几十秒，而这是页面上
    #     顺手看一眼的东西。
    try:
        out['runs'] = _runs_with(jc)
    except Exception as e:                                  # noqa: BLE001
        out['runs_error'] = '%s: %s' % (type(e).__name__, e)
    return out


def _runs_with(jc, limit=30):
    """哪些归档回测买过这只票。

    ★ 归档是**嵌套**目录 `runs/<组>/<策略>/<run_id>/`，不是平铺 ——
      用 `os.walk` 找 `meta.json` 定位，与 `server._scan()` 同一套判据。
      按平铺 `listdir` 找会一条都找不到，而那看起来像"这只票没被任何回测
      选过"（我第一版就是这么错的）。
    ★ 只读归档里的 trades，**不重跑回测** —— 重跑要几十秒，
      而这是页面上顺手看一眼的东西。
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    runs = os.path.join(root, 'runs')
    if not os.path.isdir(runs):
        return []
    dirs = []
    for dp, _dns, fns in os.walk(runs):
        if 'meta.json' in fns and 'trades.parquet' in fns:
            dirs.append(dp)
    dirs.sort(key=lambda x: os.path.basename(x), reverse=True)
    c = con()
    out = []
    for d in dirs:
        tp = os.path.join(d, 'trades.parquet')
        mp = os.path.join(d, 'meta.json')
        try:
            r = c.execute("""
                SELECT count(*), min(entry_date), max(exit_date),
                       avg(ret), sum(pnl)
                FROM read_parquet('%s') WHERE code = ?""" % tp, [jc]).fetchone()
        except Exception:                                   # noqa: BLE001
            continue
        if not r or not r[0]:
            continue
        try:
            meta = json.load(open(mp, encoding='utf-8'))
        except Exception:                                   # noqa: BLE001
            meta = {}
        out.append({'run_id': os.path.basename(d),
                    'strategy': meta.get('strategy') or '',
                    'group': meta.get('group') or '',
                    'params': meta.get('params') or {},
                    'start': meta.get('start'), 'end': meta.get('end'),
                    'n_trades': r[0], 'first': str(r[1])[:10],
                    'last': str(r[2])[:10],
                    'avg_ret': r[3], 'pnl': r[4]})
        if len(out) >= limit:
            break
    return out
