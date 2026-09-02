"""盘面（某天的全市场）与行业 / 板块。

看板「🌡 盘面」和「🏭 行业板块」两页的数据层。**只读**，来自
`mart/panel_daily` + `tdx.db` 的板块表。

## 为什么盘面要能【回看任意一天】

"今天涨了多少家"只在盘后有意义，而复盘要看的往往是上一个调仓日、
或者某次大跌那天。所以所有接口都带 `date` 参数，默认最新数据日。

## 板块从哪来

- **申万一级**：`panel_daily.sw_l1_code/name` —— 每日随面板同步，带 PIT
- **通达信板块**：`tdx.db raw_tdx_blocks_info/member` —— 926 个
  （concept 269 / style 158 / region 32 / tdx_research 467），每日同步
  🔴 `raw/hf/` 下那份同花顺概念**不用** —— `last_fetched` 停在 2026-04-28、
    没接进 `sync_daily.sh`，用它会给出四个月前的成分而页面上看着像今天的。
"""
import datetime
import os

import duckdb


class MarketError(Exception):
    pass


def _root(root=None):
    r = root or os.environ.get('ASSAY_DATALAKE') or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        '..', 'datalake')
    r = os.path.normpath(r)
    if not os.path.isdir(r):
        raise MarketError('找不到 datalake：%s' % r)
    return r


def panel(root=None):
    return "read_parquet('%s/mart/panel_daily/panel_*.parquet')" % _root(root)


def tdx_db(root=None):
    return os.path.join(_root(root), 'raw', 'tdx', '_ingest', 'tdx.db')


def con():
    return duckdb.connect(':memory:')


# 🔴 "算哪些票"的判据【只有这一处】。板块榜的家数与成分表的行数必须同一套
#   筛选 —— 分两处写就会出现"榜上写 25 只、成分表 26 只"（实测踩过：
#   榜单过滤了 public_status，成分表没过滤，多出一只退市整理期的）。
#   ST 仍然算在内 —— 它照样交易，只是限幅 5%。
TRADEABLE = ("public_status IN ('正常上市','ST','*ST')"
             ' AND close_bfq IS NOT NULL')


def latest_day(root=None):
    return con().execute('SELECT max(date) FROM %s' % panel(root)).fetchone()[0]


def _day(c, p, date):
    """把 date 解析成【实际有数据的那一天】。

    ★ 传进来的可能是周末或节假日。直接按等号查会返回空，而"空"看起来像
      "那天全市场没成交"。所以取 <= date 的最后一个有数据日，并把
      实际用的日期回给页面 —— 页面上必须显示它，否则人以为看的是自己选的那天。
    """
    if not date:
        return c.execute('SELECT max(date) FROM %s' % p).fetchone()[0]
    d = datetime.date.fromisoformat(str(date)[:10])
    r = c.execute("SELECT max(date) FROM %s WHERE date <= DATE '%s'" % (p, d)).fetchone()
    if not r or r[0] is None:
        raise MarketError('%s 之前没有任何行情数据' % d)
    return r[0]


# ---------------------------------------------------------------- 盘面
# 榜单：(键, 标题, 排序列, 方向, 说明)
RANKS = [
    ('gainers', '涨幅榜', 'change_pct', 'DESC', ''),
    ('losers', '跌幅榜', 'change_pct', 'ASC', ''),
    ('turnover', '换手榜', 'turnover', 'DESC', '换手 = 量×收盘价/流通市值×100（百分数）'),
    ('amount', '成交额榜', 'amount', 'DESC', ''),
    ('amplitude', '振幅榜', 'amplitude', 'DESC', ''),
]
# 涨跌幅分档（百分数）。★ 用 change_pct 的原始百分数，不是小数
BUCKETS = [(-100, -9.5, '跌停附近'), (-9.5, -7, '−7~−9.5'), (-7, -5, '−5~−7'),
           (-5, -3, '−3~−5'), (-3, -1, '−1~−3'), (-1, 0, '−1~0'),
           (0, 1, '0~1'), (1, 3, '1~3'), (3, 5, '3~5'), (5, 7, '5~7'),
           (7, 9.5, '7~9.5'), (9.5, 100, '涨停附近')]


def overview(date=None, top=15, root=None):
    """某天的全市场。

    ★ 只算【正常上市】的 A 股（`public_status` 是字符串 '正常上市' / 'ST' /
      '*ST' / NULL）。把退市整理期和 NULL 混进来，涨跌家数就不对。
      ST 仍然算在内 —— 它照样交易，只是限幅 5%。
    """
    c = con()
    p = panel(root)
    d = _day(c, p, date)
    top = max(3, min(int(top or 15), 50))
    base = "%s WHERE date = DATE '%s' AND %s" % (p, d, TRADEABLE)
    agg = c.execute("""
        SELECT count(*), sum(amount),
               sum(CASE WHEN change_pct > 0 THEN 1 ELSE 0 END),
               sum(CASE WHEN change_pct < 0 THEN 1 ELSE 0 END),
               sum(CASE WHEN change_pct = 0 THEN 1 ELSE 0 END),
               sum(CASE WHEN is_limit_up THEN 1 ELSE 0 END),
               sum(CASE WHEN is_limit_down THEN 1 ELSE 0 END),
               sum(CASE WHEN is_open_limit_up THEN 1 ELSE 0 END),
               median(change_pct), avg(change_pct), median(turnover)
        FROM %s""" % base).fetchone()
    out = {'date': str(d), 'asked': str(date)[:10] if date else None,
           'n': agg[0], 'amount': agg[1],
           'up': agg[2], 'down': agg[3], 'flat': agg[4],
           'limit_up': agg[5], 'limit_down': agg[6], 'open_limit_up': agg[7],
           'median_change': agg[8], 'avg_change': agg[9],
           'median_turnover': agg[10]}
    # 涨跌分布
    cases = ' '.join(
        "WHEN change_pct > %s AND change_pct <= %s THEN '%s'" % (a, b, lab)
        for a, b, lab in BUCKETS)
    rows = c.execute("""
        SELECT CASE %s ELSE '其它' END AS b, count(*)
        FROM %s GROUP BY 1""" % (cases, base)).fetchall()
    got = dict(rows)
    out['buckets'] = [{'label': lab, 'n': got.get(lab, 0)}
                      for _a, _b, lab in BUCKETS]
    # 行业榜（申万一级）：等权平均涨幅 + 家数 + 成交额
    out['industries'] = [
        {'name': r[0], 'n': r[1], 'avg_change': r[2], 'median_change': r[3],
         'amount': r[4], 'up': r[5], 'limit_up': r[6]}
        for r in c.execute("""
            SELECT sw_l1_name, count(*), avg(change_pct), median(change_pct),
                   sum(amount), sum(CASE WHEN change_pct>0 THEN 1 ELSE 0 END),
                   sum(CASE WHEN is_limit_up THEN 1 ELSE 0 END)
            FROM %s AND sw_l1_name IS NOT NULL
            GROUP BY 1 ORDER BY 3 DESC""" % base).fetchall()]
    # 各类榜单
    out['ranks'] = {}
    for key, title, col, dirn, note in RANKS:
        out['ranks'][key] = {
            'title': title, 'note': note,
            'rows': [{'code': r[0], 'name': r[1], 'close': r[2],
                      'change_pct': r[3], 'turnover': r[4], 'amount': r[5],
                      'amplitude': r[6], 'industry': r[7],
                      'limit_up': bool(r[8]), 'limit_down': bool(r[9]),
                      'floatmv': r[10]}
                     for r in c.execute("""
                        SELECT jq_code, sec_name, close_bfq, change_pct,
                               turnover, amount, amplitude, sw_l1_name,
                               is_limit_up, is_limit_down, floatmv
                        FROM %s AND %s IS NOT NULL
                        ORDER BY %s %s LIMIT %d"""
                        % (base, col, col, dirn, top)).fetchall()]}
    # 指数当天涨幅（成分等权，仅作参考 —— 不是真指数点位）
    out['index_equal'] = [
        {'name': nm, 'n': r[0], 'avg_change': r[1]}
        for k, nm in (('in_sz50', '上证50'), ('in_hs300', '沪深300'),
                      ('in_zz500', '中证500'), ('in_zz1000', '中证1000'),
                      ('in_cyb', '创业板'), ('in_kc50', '科创50'))
        for r in [c.execute('SELECT count(*), avg(change_pct) FROM %s AND %s'
                            % (base, k)).fetchone()]]
    return out


# ---------------------------------------------------------------- 板块
BLOCK_TYPE = {'concept': '概念', 'style': '风格', 'region': '地区',
              'tdx_research': '研究'}


def _tdx_blocks(root=None):
    """通达信板块 -> {block_code: (name, type, [symbol...])}。

    ★ tdx.db 是 duckdb 文件（不是 sqlite），直接 ATTACH READ_ONLY。
    """
    db = tdx_db(root)
    if not os.path.isfile(db):
        return {}
    c = con()
    c.execute("ATTACH '%s' AS t (READ_ONLY)" % db)
    info = {r[0]: (r[1], r[2]) for r in c.execute(
        'SELECT block_code, block_name, block_type FROM t.raw_tdx_blocks_info'
    ).fetchall()}
    mem = {}
    for code, sym in c.execute(
            'SELECT block_code, stock_symbol FROM t.raw_tdx_blocks_member'
    ).fetchall():
        mem.setdefault(code, []).append(sym)
    return {k: (v[0], v[1], mem.get(k, [])) for k, v in info.items()}


_BLK = {'at': None, 'data': None}


def blocks(root=None):
    """板块表，进程内缓存（926 个板块 + 8.9 万成分，每次读几百毫秒）。"""
    db = tdx_db(root)
    mt = os.path.getmtime(db) if os.path.isfile(db) else 0
    if _BLK['at'] == mt and _BLK['data'] is not None:
        return _BLK['data']
    data = _tdx_blocks(root)
    _BLK.update(at=mt, data=data)
    return data


def sector_list(date=None, kind='sw', root=None):
    """板块涨幅榜。`kind`：'sw' 申万一级 / 'concept' / 'style' / 'region'
    / 'tdx_research'。"""
    c = con()
    p = panel(root)
    d = _day(c, p, date)
    base = "%s WHERE date = DATE '%s' AND %s" % (p, d, TRADEABLE)
    if kind == 'sw':
        rows = c.execute("""
            SELECT sw_l1_code, sw_l1_name, count(*), avg(change_pct),
                   median(change_pct), sum(amount),
                   sum(CASE WHEN change_pct>0 THEN 1 ELSE 0 END),
                   sum(CASE WHEN is_limit_up THEN 1 ELSE 0 END)
            FROM %s AND sw_l1_name IS NOT NULL
            GROUP BY 1,2 ORDER BY 4 DESC""" % base).fetchall()
        return {'date': str(d), 'kind': 'sw', 'kind_name': '申万一级',
                'rows': [{'code': r[0], 'name': r[1], 'n': r[2],
                          'avg_change': r[3], 'median_change': r[4],
                          'amount': r[5], 'up': r[6], 'limit_up': r[7]}
                         for r in rows]}
    blk = blocks(root)
    want = [(k, v) for k, v in blk.items() if v[1] == kind]
    if not want:
        raise MarketError('没有 kind=%r 的板块（可选：sw / %s）'
                          % (kind, ' / '.join(sorted(BLOCK_TYPE))))
    # 用 symbol 关联（tdx 板块成分是 tdx symbol，如 sh601857）
    day = c.execute("""
        SELECT symbol, change_pct, amount, is_limit_up FROM %s""" % base).fetchall()
    px = {r[0]: r for r in day}
    out = []
    for code, (name, _t, syms) in want:
        vals = [px[s] for s in syms if s in px]
        if not vals:
            continue
        cps = [v[1] for v in vals if v[1] is not None]
        out.append({'code': code, 'name': name, 'n': len(vals),
                    'avg_change': (sum(cps) / len(cps)) if cps else None,
                    'median_change': (sorted(cps)[len(cps) // 2] if cps else None),
                    'amount': sum(v[2] or 0 for v in vals),
                    'up': sum(1 for v in vals if (v[1] or 0) > 0),
                    'limit_up': sum(1 for v in vals if v[3])})
    out.sort(key=lambda x: -(x['avg_change'] if x['avg_change'] is not None else -99))
    return {'date': str(d), 'kind': kind,
            'kind_name': BLOCK_TYPE.get(kind, kind), 'rows': out}


def sector_members(code, date=None, kind='sw', limit=300, root=None):
    """一个板块的成分股 + 当天行情。"""
    c = con()
    p = panel(root)
    d = _day(c, p, date)
    # ★ 与 sector_list 用【同一套】筛选，否则家数对不上（见 TRADEABLE）
    base = "%s WHERE date = DATE '%s' AND %s" % (p, d, TRADEABLE)
    cols = ("jq_code, sec_name, close_bfq, change_pct, turnover, amount,"
            " amplitude, floatmv, pe_ttm, pb, sw_l1_name,"
            " is_limit_up, is_limit_down")
    keys = ['code', 'name', 'close', 'change_pct', 'turnover', 'amount',
            'amplitude', 'floatmv', 'pe_ttm', 'pb', 'industry',
            'limit_up', 'limit_down']
    lim = max(1, min(int(limit or 300), 2000))
    if kind == 'sw':
        rows = c.execute(
            'SELECT %s FROM %s AND sw_l1_code = ? ORDER BY change_pct DESC '
            'LIMIT %d' % (cols, base, lim), [str(code)]).fetchall()
        nm = c.execute('SELECT DISTINCT sw_l1_name FROM %s AND sw_l1_code = ?'
                       % (base,), [str(code)]).fetchone()
        name = nm[0] if nm else str(code)
    else:
        blk = blocks(root).get(str(code))
        if not blk:
            raise MarketError('没有这个板块：%r' % code)
        name = blk[0]
        syms = blk[2]
        if not syms:
            return {'date': str(d), 'code': code, 'name': name, 'rows': []}
        q = "','".join(syms)
        rows = c.execute(
            "SELECT %s FROM %s AND symbol IN ('%s') ORDER BY change_pct DESC "
            'LIMIT %d' % (cols, base, q, lim)).fetchall()
    out = [dict(zip(keys, r)) for r in rows]
    for x in out:
        x['limit_up'] = bool(x['limit_up'])
        x['limit_down'] = bool(x['limit_down'])
    cps = [x['change_pct'] for x in out if x['change_pct'] is not None]
    return {'date': str(d), 'code': str(code), 'name': name, 'kind': kind,
            'n': len(out), 'rows': out,
            'avg_change': (sum(cps) / len(cps)) if cps else None}


def stock_sectors(code, root=None):
    """这只票属于哪些板块（通达信）+ 申万一级。个股页「所属板块」用。

    ★ 代码先归一化 —— 页面可能传 `601857` / `601857.SH`，不归一就直接
      `WHERE jq_code = '601857'` 查不到，而报的是"面板里没有 601857"，
      看着像这只票不存在。
    """
    from assay import stock as stk
    jc = stk.norm_code(code)
    if not jc:
        raise MarketError('认不出代码：%r' % code)
    c = con()
    p = panel(root)
    d = _day(c, p, None)
    row = c.execute(
        'SELECT symbol, sw_l1_code, sw_l1_name FROM %s '
        "WHERE jq_code = ? ORDER BY date DESC LIMIT 1" % p, [jc]).fetchone()
    if not row:
        raise MarketError('面板里没有 %s' % jc)
    sym = row[0]
    out = {'code': jc, 'date': str(d),
           'sw': ({'code': row[1], 'name': row[2]} if row[2] else None),
           'blocks': []}
    for bc, (name, kind, syms) in blocks(root).items():
        if sym in syms:
            out['blocks'].append({'code': bc, 'name': name, 'kind': kind,
                                  'kind_name': BLOCK_TYPE.get(kind, kind),
                                  'n': len(syms)})
    out['blocks'].sort(key=lambda x: (x['kind'] != 'concept', x['n']))
    return out
