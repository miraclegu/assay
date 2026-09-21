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
import io
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


from assay import symbols as _SYM          # 「代码->类别/名称」的唯一正本


class StockError(Exception):
    pass


def panel(root=None):
    return "read_parquet('%s/mart/panel_daily/panel_*.parquet')" % _lake(root)


def con():
    return duckdb.connect(':memory:')


# ------------------------------------------------- ETF / 指数（不在面板里）
#
# 🔴🔴 用户 2026-09-16："个股功能应该也要支持 ETF、指数。"
#
# 面板（`mart/panel_daily`）是**股票宽表** —— ETF 与指数一行都没有。
# 它们的日线在 `raw/tdx/kline/{index,etf}_*.parquet`（与实盘业绩页那个
# 「自定义基准」同一份数据源，见 `lv/perf.py` 的 `bench_curves`）。
#
# ★ 做法是**拼一个与面板同形的子查询**，而不是在 `kline()` / `profile()`
#   里分叉出第二套取数逻辑：下游（K 线、全部指标、翻页、复权）**一个字
#   都不用改**，股票那条路也一个字没动。
#   —— 另写一套的话，"同一个指标在股票上和在 ETF 上算出来不一样"
#   迟早发生，而它不报错（同「两处实现必然分叉」那条）。
#
# 🔴 **标识用 tdx symbol（`sh000001`），不走 `jq_code`。**
#   `normalize_code('sh000001')` 会**直接拒绝** —— 它是按**股票**的前缀
#   规则写的（"00 开头的是深市股票，而标记写的是沪市，有一个是错的"），
#   而上证指数恰好是 `sh000001`。那条规则对股票是对的（市场决定过户费），
#   对指数根本不适用。所以这一层按 symbol 认，**不去动 normalize_code**。
_ALT_FILE = {'index': 'index_*', 'etf': 'etf_*'}
_RE_SYM = re.compile(r'^(sh|sz)\d{6}$')
_ALT = {'at': None, 'map': None}


def _snap_path(root=None):
    """`symbol_name` 快照里 snap_date 最大的那一份。

    🔴 **正本在 `lv/perf.py` 的 `_name_snap`**（自定义基准也要按名字搜，
      用的就是它）—— 这里 import 而不是再写一遍：我第一版自己解析
      `manifest.csv`，**把列序记反了**（第 0 列是 snap_date 不是文件名），
      于是一条都匹配不上、ETF/指数全都认不出来，而它不报错，
      只是"搜什么都没有"（同「同一件事两处实现必然分叉」那条）。
    """
    from assay.lv.perf import _name_snap
    return _name_snap(_lake(root))


def alt_kind(code, root=None):
    """这个 code 是 ETF / 指数吗？是就返回 `(kind, symbol)`，否则 None。

    🔴 **聚宽口径也要认**（2026-09-21 修）。原来这里写的是"只认 tdx symbol
      形状（`sh510880`）；别的写法一律当股票走原路" —— 而**实盘账本与自选
      记的就是聚宽口径** `513120.XSHG`。于是首页点一只 ETF：
      `alt_kind` 返回 None -> 走股票面板 -> 面板里没有 ETF -> 「取不到日线」，
      而同一只票在实盘页好好的（那条路有 `to_symbol` 换算）。
      换算一直都有（`symbols.to_symbol`），**只是这里没调**。
    ★ 判类别按**快照里的 class**，不按代码前缀猜 —— 前缀规则是会变的
      （科创 688、北交 8 开头都出现过），而快照是事实。
    ★ 判据与取名统一在 `assay/symbols.py`，本函数只转发（别在这里再写一份）。
    """
    k = _SYM.kind_of(code, root)
    return (k, _SYM.as_symbol(code)) if k in _ALT_FILE else None


def _alt_map(root=None):
    """{symbol: class} —— 只收 ETF / 指数，按快照文件缓存。"""
    fp = _snap_path(root)
    if not fp:
        return {}
    if _ALT['at'] == fp and _ALT['map'] is not None:
        return _ALT['map']
    rows = con().execute(
        "SELECT symbol, class FROM read_parquet('%s') "
        "WHERE class IN ('index','etf')" % fp).fetchall()
    m = {r[0]: r[1] for r in rows}
    _ALT.update(at=fp, map=m)
    return m


def alt_panel(kind, root=None):
    """把 ETF / 指数的日线**拼成与面板同形**的表（列名一字不差）。

    🔴 **一律给出后复权列**（`close × coalesce(hfq_factor, 1)`）：
      ETF 会分红（红利 ETF 的因子 1.0 -> 1.81，那 81% 全是分红），
      不复权跨除权日有假跌幅 —— 与「对比页一律后复权」同一条纪律。
      指数不除权（因子表里没有它的行），`coalesce` 到 1 正好。
    ★ 面板有而这里没有的（换手率、涨跌停价、估值、财务）一律 NULL ——
      **不猜一个数填上去**。页面那几格会显示"—"，并另有一句话说明
      "ETF / 指数没有这项"（空着会被读成"数据没取到"）。
    """
    lake = _lake(root)
    K = "read_parquet('%s/raw/tdx/kline/%s.parquet')" % (lake, _ALT_FILE[kind])
    F = "read_parquet('%s/raw/tdx/adjust_factor.parquet')" % lake
    return ("""(SELECT k.symbol AS jq_code, k.symbol AS symbol, k.date,
        k.open, k.high, k.low,
        k.close AS close_bfq,
        k.close * coalesce(f.hfq_factor, 1) AS close_hfq,
        coalesce(f.hfq_factor, 1) AS hfq_factor,
        k.volume AS volume_shares, k.amount,
        (k.close / lag(k.close) OVER (PARTITION BY k.symbol ORDER BY k.date)
         - 1) * 100 AS change_pct,
        CAST(NULL AS DOUBLE) AS turnover,
        false AS is_limit_up, false AS is_limit_down,
        lag(k.close) OVER (PARTITION BY k.symbol ORDER BY k.date) AS preclose
      FROM %s k LEFT JOIN %s f
        ON f.symbol = k.symbol AND f.date = k.date)""" % (K, F))


# ---------------------------------------------------------------- 代码归一
_PREFIX = {'60': 'XSHG', '68': 'XSHG', '90': 'XSHG',
           '00': 'XSHE', '30': 'XSHE', '20': 'XSHE'}
_TOKEN = {'SH': 'XSHG', 'SS': 'XSHG', 'XSHG': 'XSHG',
          'SZ': 'XSHE', 'XSHE': 'XSHE'}
_RE_D6 = re.compile(r'\d{6}')
# 搜索结果同分时的类别次序：股票 -> 指数 -> ETF。
# ★ 人在个股页搜名称，要的通常是股票；ETF 与指数是后来才支持的，
#   排在后面但**找得到**（同「合并的风险不是少两个按钮，是把功能藏起来」）。
_KIND_RANK = {'stock': 0, 'index': 1, 'etf': 2}


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
            'change_pct': r[7], 'floatmv': r[8], 'kind': 'stock'} for r in rows]
    out += _alt_index(c, root)
    _IDX.update(at=day, rows=out)
    return out, day


def _alt_index(c, root=None):
    """ETF / 指数也进这张搜索表 —— 否则个股页**搜都搜不到**它们。

    🔴 **只收本地真有日线的**：名称快照里有 166 只 ETF / 352 只股票在 kline
      里根本没有行（退市、未上市、北交所新股）。列出来点了什么都不出来，
      比不给这个选项更糟（同自定义基准那条 `bench_search`）。
      —— 做法是拿 kline 与快照 **INNER JOIN**，没有日线的天然进不来。
    ★ `floatmv` 给 None：ETF / 指数没有流通市值。搜索排序里它是
      "同分再按市值降序"的那一档，None 当 0 —— 于是同分时股票排在前面，
      而人搜 6 位数字时要的通常就是股票。
    """
    fp = _snap_path(root)
    if not fp:
        return []
    lake = _lake(root)
    out = []
    for kind, pat in (('index', 'index_*'), ('etf', 'etf_*')):
        K = "read_parquet('%s/raw/tdx/kline/%s.parquet')" % (lake, pat)
        try:
            rows = c.execute("""
                WITH last AS (
                  SELECT symbol, max(date) AS d FROM %s GROUP BY 1),
                px AS (
                  SELECT k.symbol, k.close, k.date,
                         lag(k.close) OVER (PARTITION BY k.symbol ORDER BY k.date) AS pc
                  FROM %s k)
                SELECT s.symbol, s.name, px.close,
                       CASE WHEN px.pc IS NULL OR px.pc = 0 THEN NULL
                            ELSE (px.close / px.pc - 1) * 100 END
                FROM read_parquet('%s') s
                JOIN last ON last.symbol = s.symbol
                JOIN px ON px.symbol = s.symbol AND px.date = last.d
                WHERE s.class = '%s'""" % (K, K, fp, kind)).fetchall()
        except Exception:                                   # noqa: BLE001
            continue          # 这一类的文件不在 -> 就当没有，别拖垮搜索
        out += [{'code': r[0], 'symbol': r[0], 'name': r[1] or '',
                 'industry': '', 'status': '正常上市', 'is_st': False,
                 'close': r[2], 'change_pct': r[3], 'floatmv': None,
                 'kind': kind} for r in rows]
    return out


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
    tl = t.lower()
    for r in rows:
        n6 = r['code'][:6]
        score = None
        # 🔴 **完整 symbol 精确命中排最前**：人打 `sh000001` 指的就是上证
        #   指数，而那 6 位数字同时是平安银行（`000001.XSHE`）—— 只按
        #   "6 位数字前缀命中"打分的话，第一条给的是平安银行，
        #   而它看着完全正常（同「代码写法各家不同」那条：形状一样、
        #   含义不同的代码，要拿**完整那一个**去比）。
        if r['symbol'] and tl == str(r['symbol']).lower():
            score = -1
        elif num and n6 == num:
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
            # 排序键三层，顺序是**试出来的**，每一层都对应一种搜不对的情形：
            #   ① 完整代码精确命中最优先 —— `sh000001` 要给上证指数，
            #      而那 6 位数字同时是平安银行。
            #   ② 其次**股票优先**：搜"银行"要的是银行股，而"银行ETF银华"
            #      是**名称前缀**命中（score 2）、"招商银行"只是包含
            #      （score 3）—— 光按 score 排，一屏全是银行 ETF
            #      （既有那条"同类匹配按市值降序"的断言当场抓到）。
            #      股票里没有匹配时（"红利ETF" / "上证指数"）自然轮到它们。
            #   ③ 最后才是 score 与市值。
            out.append((0 if score < 0 else 1,
                        _KIND_RANK.get(r.get('kind'), 9),
                        score, -(r['floatmv'] or 0), r))
    out.sort(key=lambda x: x[:4])
    return {'results': [x[4] for x in out[:max(1, min(int(limit or 20), 50))]],
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


_EVENT_KINDS = {'xr': '除权除息', 'fin': '财报公告',
                'unlock': '解禁', 'share': '股本变动'}


def _na(alt, base):
    """ETF / 指数**本来就没有**这一块 —— 明说，不要返回一个静默的空。

    🔴 空结果与"本来就没有"**必须分得出来**（同 `prune_runs` 那条：
      删了要留痕，否则"空"与"本来就没有"分不出来）。页面拿到
      `not_applicable` 就写一句"ETF / 指数没有财务数据"，
      而不是给一张空表让人以为数据没取到。
    """
    d = dict(base or {})
    d.update({'code': alt[1], 'asset_kind': alt[0], 'not_applicable': True,
              'why': '%s 没有这一项（它不是股票）'
                     % ('ETF' if alt[0] == 'etf' else '指数')})
    return d


def _alt_name(sym, root=None):
    """ETF / 指数的中文名。取不到才退回 symbol（页面总得显示点什么）。

    🔴 **清洗只有一处**（`symbols.clean_name`）。原来这里直接返回快照原文，
      而 tdx 的名称字段是定长 16 字节、截断处会劈开一个汉字 ——
      实测 `stock._alt_name('sh513120')` 给出 '港股创新药ETF广\ufffd'，
      而 `lv/tdx.names` 给的是 '港股创新药ETF广'：**同一只票两个名字**。
    """
    jq = _SYM.to_jq(sym)
    got = _SYM.alt_names(_lake(root), [jq]) if jq else {}
    return got.get(jq) or sym


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
            'preclose': _r3(r[13]),
            # 这一天的**换算系数**（当前坐标价 ÷ 不复权价），bfq 恒 1。
            # 🔴 给出来是为了让页面能把**买卖点**（成交价是不复权实际价）
            #   画到同一套坐标上 —— 前端自己再存一份不复权 bars 去比的话，
            #   就是同一份信息两处算（而且翻页、换区间都要各自跟着变）。
            'fqk': (round(f / qf, 8) if fq == 'qfq'
                    else (round(f, 8) if fq == 'hfq' else 1.0)),
        })
    for w in (5, 10, 20, 60):
        _ma(out, w)
    # 这只票总共有多少根 —— 前端据此知道还能不能往左翻（到头了要说，
    # 不能让「←」点了没反应：同「给一个点了没反应的按钮比不给更糟」）。
    total = c.execute('SELECT count(*) FROM %s WHERE %s' % (p, where),
                      args).fetchone()[0]
    return {'code': jc, 'fq': fq, 'bars': out[-n:], 'n': min(len(out), n),
            'off': off, 'total': int(total),
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


# ================================ 指标 ================================
# 🔴 **算法与定义全在 `assay/indicators.py`（唯一正本）** —— 这里只管
#   "取多少根、预热多少、平铺成行"。原来 MACD/KDJ/RSI/BOLL 的公式写死在
#   这个文件里，而前端 kchart.js 又硬编码了一份"画哪几条线"，于是加一个
#   指标要改三处、漏了画的那处**不报错**（选了它副图一片空白）。
def parse_inds(spec_str):
    """把 URL 上的 `inds=` 解析成 [{'id','params'}]。

    两种写法都认：`macd,kdj`（默认参数）与 JSON
    `[{"id":"kdj","params":{"n":9}}]`（要改参数时用）。
    ★ 认两种是因为**大多数时候不改参数**，而让人在 URL 里手写一段 JSON
      才能换个副图，等于把这个功能藏起来。
    """
    import json
    t = (spec_str or '').strip()
    if not t:
        return None
    if t[0] == '[':
        try:
            arr = json.loads(t)
        except Exception as e:                              # noqa: BLE001
            raise StockError('inds 不是合法 JSON：%s' % e)
        out = []
        for x in arr:
            if isinstance(x, str):
                out.append({'id': x, 'params': None})
            else:
                out.append({'id': x.get('id'), 'params': x.get('params')})
        return out
    return [{'id': x.strip(), 'params': None} for x in t.split(',') if x.strip()]


# 🔴 预热有个【下限】而不只是"按需要取"：RSI/ATR 这类 Wilder 递推
#   **永远记着起点**（多喂 24 根，末尾的值就会差一点）。下限钉在 120
#   是为了让默认那几个指标与改造前**逐位一致** —— 少喂一点不报错，
#   只是页面上的 RSI 悄悄变了几厘。
IND_WARM = 120
IND_DEFAULT = ['macd', 'kdj', 'rsi', 'boll']


def indicators(code, n=250, fq=None, end=None, off=0, root=None, inds=None):
    """按需算一组指标。定义全在 `assay/indicators.py`（唯一正本）。

    `inds` 不给时算 `IND_DEFAULT`（MACD/KDJ/RSI/BOLL）——
    那是改造前的行为，个股页与浮层的老链接照旧能用。

    返回 `rows` 是**一行一天、所有指标的键平铺在一起**（`dif`/`k`/`rsi6`…），
    与改造前同形；`panels` 是每个指标画哪几条线、什么样式、什么颜色，
    **页面照它渲染，不认识任何指标名**。
    """
    from assay import indicators as _I
    # ★ n 可能是 URL 里来的字符串 —— 在【入口】转 int，不要指望调用方转。
    n = max(10, min(int(n or 250), 3000))
    want = inds if inds is not None else [{'id': x, 'params': None}
                                          for x in IND_DEFAULT]
    if isinstance(want, str):
        want = parse_inds(want)
    specs = []
    try:
        for w in (want or []):
            sp = _I.spec(w.get('id'))
            specs.append((sp, sp.merge(w.get('params'))))
    except _I.IndError as e:
        raise StockError(str(e))
    warm = max([IND_WARM] + [sp.warm(p) for sp, p in specs])
    # 🔴 `off` 必须一路传下去 —— 不传的话翻页后副图画的还是最新那一段，
    #   与主图**上下对不上**，而它不报错，看着像"指标和 K 线不同步"。
    k = kline(code, n=n + warm, fq=fq, end=end, off=off, root=root)
    fq = k['fq']        # 服务端解析后的真实口径（不给时按类别定）
    bars = k['bars']
    m = len(bars)
    out = [{'date': b['date'], 'close': b['close']} for b in bars]
    panels = []
    for sp, p in specs:
        try:
            col = sp.calc(bars, p)
        except _I.IndError as e:
            raise StockError(str(e))
        for key, vals in col.items():
            for i in range(m):
                out[i][key] = vals[i]
        panels.append({'id': sp.id, 'label': sp.label, 'short': sp.short,
                       'panel': sp.panel,
                       'unit': sp.unit, 'desc': sp.desc,
                       'params': p, 'series': sp.series(p)})
    return {'code': k['code'], 'fq': fq, 'n': min(n, m),
            'warmup_dropped': max(0, m - n), 'rows': out[-n:],
            'panels': panels,
            # ★ 老字段留着：改造前的调用方（浮层/用例）读的是它
            'params': dict((sp.id, p) for sp, p in specs)}


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
    alt = alt_kind(code, root)
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
def peers(code, n=20, root=None):
    """同申万一级行业的票，按流通市值降序，并标出这只在其中的位置。"""
    alt = alt_kind(code, root)
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


# ========================= 与实盘 / 回测联动 =========================
def links(code, root=None):
    """这只票和我的实盘、回测有什么关系。

    ★ 数据全是现成的，只是原来没连起来：看个股时最想知道的两件事就是
      "我持有它吗、成本多少" 和 "我哪次回测选过它、当时什么参数"。
    ★ 任何一半取不到都不该拖垮整页 —— 各自 try，缺的那半标 error。
    """
    # 🔴 **只有指数**标不适用：ETF 是**能买的**（项目里就有 ETF 轮动策略），
    #   实盘持有过、回测选过它都讲得通，这一块对它有意义。
    #   指数买不了，查"我持有多少上证指数"本身就没有意义。
    alt = alt_kind(code, root)
    if alt and alt[0] == 'index':
        return _na(alt, {'positions': [], 'runs': [], 'watch': None})
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
    # 🔴 归档目录要走 `registry.RUNS`，不能自己拼 `<repo>/runs` ——
    #   serve.py 的 `--runs` / `ASSAY_RUNS` 会改它（`registry.set_runs()`），
    #   自己拼的话指定了别的归档盘时这里**静默返回空**，
    #   页面上看着就是"这只票没被任何回测选过"。
    #   ★ 必须 `registry.RUNS` **属性访问**：set_runs() 是重新赋值，
    #     `from .registry import RUNS` 拿到的是副本。
    from . import registry
    runs = registry.RUNS
    if not os.path.isdir(runs):
        # 不静默返回 [] —— 那和"真的没有回测选过它"长得一模一样。
        # 外层 links() 会把它转成 runs_error 给页面。
        raise RuntimeError('归档目录不存在：%s'
                           '（用 --runs 或 ASSAY_RUNS 指定）' % runs)
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
