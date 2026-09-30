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
from assay import paths as _paths   # datalake 根的唯一解析


class MarketError(Exception):
    pass


class BlocksLocked(MarketError):
    """通达信板块库读不动（每日同步正在写它）。

    🔴 **「未知不是缺」** —— 读不到和"本来就没有"是两回事。原来这里让
      duckdb 的 `IOException` 一路穿到 `do_GET` -> HTTP 500，页面拿到的是
      一个裸报错；更糟的是若改成"返回空板块表"，个股页会显示「没有板块
      归属」、板块页会只剩申万一级 —— **看着像本来就这样，而且不报错**。
    ★ 所以做成 `MarketError` 的子类：`_market_err` 已经会把它变成
      `{'error': …}`（200），而**认得出它是哪一种**的调用方（个股归属、
      板块分类清单）可以只降级这一块、把原因写出来，其余照常。
    """


def _root(root=None):
    r = _paths.datalake(root)
    if not os.path.isdir(r):
        raise MarketError('找不到 datalake：%s' % r)
    return r


def panel(root=None):
    return _paths.panel_sql(_root(root))


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
    # 🔴 **必须带 `code`**：盘面上「银行I」与「看成分 ›」两个入口拼的都是
    #   `sector.html?kind=sw&code=<code>`，而这里原来只 SELECT 了名字 ——
    #   于是 `code` 是 None、链接拼成 `&code=`（空），点过去落在板块页却
    #   一个板块都没选中，看起来就是"跳到了另一个面板"（2026-09-29 实测）。
    #   名字能显示、链接也能点，所以**它不报错** —— 只是永远到不了成分股。
    out['industries'] = [
        {'code': r[0], 'name': r[1], 'n': r[2], 'avg_change': r[3],
         'median_change': r[4], 'amount': r[5], 'up': r[6], 'limit_up': r[7]}
        for r in c.execute("""
            SELECT sw_l1_code, sw_l1_name, count(*), avg(change_pct),
                   median(change_pct), sum(amount),
                   sum(CASE WHEN change_pct>0 THEN 1 ELSE 0 END),
                   sum(CASE WHEN is_limit_up THEN 1 ELSE 0 END)
            FROM %s AND sw_l1_name IS NOT NULL
            GROUP BY 1,2 ORDER BY 4 DESC""" % base).fetchall()]
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
    try:
        c.execute("ATTACH '%s' AS t (READ_ONLY)" % db)
    except Exception as e:                                  # noqa: BLE001
        # 🔴 **报错要指向真正的原因**：duckdb 给的是
        #   `IO Error: Could not set lock on file …`，那句话指不到
        #   "每日同步正在写这个库"这件事，而那正是唯一会发生的情况。
        if 'lock' in str(e).lower():
            raise BlocksLocked(
                '通达信板块库正被另一个进程占用（每日同步在写 tdx.db），'
                '这一刻读不到 —— 是「读不到」，不是「没有板块」；'
                '同步跑完就恢复') from e
        raise MarketError('打不开通达信板块库 %s：%s' % (db, e)) from e
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
    #   ★ `_tdx_blocks` 抛异常时**不写缓存** —— 写了的话锁解开之后
    #     还会拿着那份空表，而 mtime 没变就永远不重读。
    data = _tdx_blocks(root)
    _BLK.update(at=mt, data=data)
    return data


# 申万三级的列名映射 —— 一处定义，list / members / kinds 共用。
#   🔴 不许在三个函数里各写一份 `sw_l2_code` —— 加一级或改列名时必然漏掉
#     其中一处，而漏了不报错（那一级只是查不出东西）。
SW_LEVELS = {
    'sw':    ('sw_l1_code', 'sw_l1_name', '申万一级', None),
    'sw_l2': ('sw_l2_code', 'sw_l2_name', '申万二级', 'sw_l1_code'),
    'sw_l3': ('sw_l3_code', 'sw_l3_name', '申万三级', 'sw_l2_code'),
}

def _sw_agg(c, base, code_col, name_col, parent_col=None, parent=None):
    """某一级申万的板块涨幅榜。

    🔴 **涨跌幅用流通市值加权**（`w_change`）—— 这是业内口径（申万行业指数、
      东财/同花顺板块涨幅都是市值加权），本地对得上外面看到的数才有意义。
      同时保留等权平均与中位：等权更能反映"这个板块里多数票怎么样"，
      两个口径**一起给**，页面上并列，不用猜看的是哪个。
    ★ 加权分母用 `floatmv`（流通市值），不是总市值 —— 与申万指数一致。
      `floatmv` 为空/为 0 的票不进加权（但仍进等权与家数），否则整段变 NULL。
    ★ 领涨股：业内标配的一列，直接给代码 + 名字 + 涨幅，省一次点击。
    """
    where = " AND %s IS NOT NULL" % name_col
    if parent_col and parent:
        where += " AND %s = '%s'" % (parent_col, str(parent).replace("'", "''"))
    rows = c.execute("""
        WITH d AS (SELECT * FROM %s %s),
        g AS (
          SELECT %s AS code, %s AS name, count(*) AS n,
                 sum(CASE WHEN floatmv > 0 THEN change_pct * floatmv END)
                   / nullif(sum(CASE WHEN floatmv > 0 THEN floatmv END), 0) AS w_change,
                 avg(change_pct) AS avg_change,
                 median(change_pct) AS median_change,
                 sum(amount) AS amount,
                 sum(CASE WHEN change_pct > 0 THEN 1 ELSE 0 END) AS up,
                 sum(CASE WHEN change_pct < 0 THEN 1 ELSE 0 END) AS down,
                 sum(CASE WHEN is_limit_up THEN 1 ELSE 0 END) AS limit_up,
                 sum(CASE WHEN is_limit_down THEN 1 ELSE 0 END) AS limit_down
          FROM d GROUP BY 1, 2),
        t AS (          -- 领涨股：每个板块涨幅最大的那只
          SELECT code, jq_code, sec_name, change_pct FROM (
            SELECT %s AS code, jq_code, sec_name, change_pct,
                   row_number() OVER (PARTITION BY %s ORDER BY change_pct DESC) rn
            FROM d) WHERE rn = 1)
        SELECT g.*, t.jq_code, t.sec_name, t.change_pct
        FROM g LEFT JOIN t USING (code)
        ORDER BY g.w_change DESC NULLS LAST
    """ % (base, where, code_col, name_col, code_col, code_col)).fetchall()
    return [{'code': r[0], 'name': r[1], 'n': r[2], 'w_change': r[3],
             'avg_change': r[4], 'median_change': r[5], 'amount': r[6],
             'up': r[7], 'down': r[8], 'limit_up': r[9], 'limit_down': r[10],
             'top_code': r[11], 'top_name': r[12], 'top_change': r[13]}
            for r in rows]


def sector_list(date=None, kind='sw', root=None, parent=None):
    """板块涨幅榜。

    `kind`：`sw` 申万一级 / `sw_l2` 二级 / `sw_l3` 三级 /
            `concept` / `style` / `region` / `tdx_research`。
    `parent`：下钻用 —— `kind='sw_l2'` 时传一级的 code，只列它下属的二级。

    🔴 申万三级走**同一条**聚合（`_sw_agg`），列名从 `SW_LEVELS` 取 ——
      三处各写一份 SQL 的话，加一级或改口径时必然漏掉其中一处，
      而漏了不报错（那一级只是数字悄悄不一致）。
    ★ 通达信板块（概念/风格/地区/研究）天然只有一层，没有 parent。
    """
    c = con()
    p = panel(root)
    d = _day(c, p, date)
    base = "%s WHERE date = DATE '%s' AND %s" % (p, d, TRADEABLE)
    if kind in SW_LEVELS:
        code_col, name_col, kname, parent_col = SW_LEVELS[kind]
        rows = _sw_agg(c, base, code_col, name_col, parent_col, parent)
        out = {'date': str(d), 'kind': kind, 'kind_name': kname,
               'rows': rows, 'parent': parent, 'has_children': kind != 'sw_l3'}
        if parent and parent_col:
            # ★ 面包屑要显示父级的**名字**，不能只给 code —— 页面拿 code
            #   去猜名字就是又抄了一份映射。
            pn = c.execute(
                "SELECT DISTINCT %s FROM %s AND %s = ? LIMIT 1"
                % (name_col.replace('l2', 'l1').replace('l3', 'l2'),
                   base, parent_col), [parent]).fetchone()
            out['parent_name'] = pn[0] if pn else None
        return out
    blk = blocks(root)
    want = [(k, v) for k, v in blk.items() if v[1] == kind]
    if not want:
        raise MarketError('没有 kind=%r 的板块（可选：sw / %s）'
                          % (kind, ' / '.join(sorted(BLOCK_TYPE))))
    # 用 symbol 关联（tdx 板块成分是 tdx symbol，如 sh601857）
    # 🔴 与申万那条路**同一套口径**：流通市值加权为主、等权与中位并列、
    #   带领涨股。两边给的字段名必须一样，否则页面要为两类板块各写一套
    #   渲染（而那两套迟早分叉）。
    day = c.execute("""
        SELECT symbol, change_pct, amount, is_limit_up, is_limit_down,
               floatmv, jq_code, sec_name FROM %s""" % base).fetchall()
    px = {r[0]: r for r in day}
    out = []
    for code, (name, _t, syms) in want:
        vals = [px[s] for s in syms if s in px]
        if not vals:
            continue
        cps = [v[1] for v in vals if v[1] is not None]
        wv = [(v[1], v[5]) for v in vals if v[1] is not None and (v[5] or 0) > 0]
        wsum = sum(w for _x, w in wv)
        top = max(vals, key=lambda v: (v[1] if v[1] is not None else -99))
        out.append({'code': code, 'name': name, 'n': len(vals),
                    'w_change': (sum(x * w for x, w in wv) / wsum) if wsum else None,
                    'avg_change': (sum(cps) / len(cps)) if cps else None,
                    'median_change': (sorted(cps)[len(cps) // 2] if cps else None),
                    'amount': sum(v[2] or 0 for v in vals),
                    'up': sum(1 for v in vals if (v[1] or 0) > 0),
                    'down': sum(1 for v in vals if (v[1] or 0) < 0),
                    'limit_up': sum(1 for v in vals if v[3]),
                    'limit_down': sum(1 for v in vals if v[4]),
                    'top_code': top[6], 'top_name': top[7], 'top_change': top[1]})
    out.sort(key=lambda x: -(x['w_change'] if x['w_change'] is not None else -99))
    return {'date': str(d), 'kind': kind, 'parent': None, 'has_children': False,
            'kind_name': BLOCK_TYPE.get(kind, kind), 'rows': out}


def sector_members(code, date=None, kind='sw', limit=2000, root=None):
    """一个板块的成分股 + 当天行情。

    🔴🔴 **默认上限原来是 300，而它会【悄悄截断】。**（2026-09-14 修）
      `n` 取的是返回行数，于是行业涨过 300 只之后：榜上写 478、成分表
      给 300、`n` 也报 300 —— **三处自洽**，没有任何地方说得出少了 178 只。
      同「没写 LIMIT 会自动补，并在返回里带 truncated，页面必须显示」那条：
      **悄悄截断比查不出来更糟** —— 少的那部分你不知道，而结论已经下了。

      是 selftest 抓到的，而且是**数据长出来**才抓到的（面板更新到
      2026-09-14 之后某个申万一级到了 478 只）—— 写死的阈值就是这样过期的。

    ★ 两条一起改：默认拉到硬上限 2000（申万一级最大也就几百只），
      并且**总数单独查**、真截断时给 `truncated` —— 这样即使哪天又撞上限，
      页面也说得出来，而不是再来一次"三处自洽的谎"。
    """
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
    if kind in SW_LEVELS:
        # 🔴 列名从 `SW_LEVELS` 取，不在这里再写一遍 `sw_l1_code` ——
        #   加一级时漏掉这处的话，那一级点进去是空的，而且不报错。
        code_col, name_col, _kn, _pc = SW_LEVELS[kind]
        rows = c.execute(
            'SELECT %s FROM %s AND %s = ? ORDER BY change_pct DESC '
            'LIMIT %d' % (cols, base, code_col, lim), [str(code)]).fetchall()
        nm = c.execute('SELECT DISTINCT %s FROM %s AND %s = ?'
                       % (name_col, base, code_col), [str(code)]).fetchone()
        name = nm[0] if nm else str(code)
        # ★ 总数**单独查**，不拿 len(rows) 充数 —— 那正是上面那个谎的来源。
        total = c.execute('SELECT count(*) FROM %s AND %s = ?'
                          % (base, code_col), [str(code)]).fetchone()[0]
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
        total = c.execute("SELECT count(*) FROM %s AND symbol IN ('%s')"
                          % (base, q)).fetchone()[0]
    out = [dict(zip(keys, r)) for r in rows]
    for x in out:
        x['limit_up'] = bool(x['limit_up'])
        x['limit_down'] = bool(x['limit_down'])
    cps = [x['change_pct'] for x in out if x['change_pct'] is not None]
    return {'date': str(d), 'code': str(code), 'name': name, 'kind': kind,
            # 🔴 `n` 是**真实成分数**（单独 count 出来的），不是返回了几行。
            'n': int(total), 'rows': out, 'truncated': len(out) < int(total),
            'avg_change': (sum(cps) / len(cps)) if cps else None}


def stock_sectors(code, root=None):
    """这只票属于哪些板块（通达信）+ 申万**三级**。个股页「所属板块」用。

    ★ 代码先归一化 —— 页面可能传 `601857` / `601857.SH`，不归一就直接
      `WHERE jq_code = '601857'` 查不到，而报的是"面板里没有 601857"，
      看着像这只票不存在。
    """
    from assay import stock as stk
    # ETF / 指数不属于任何申万行业或通达信板块 —— **明说**，不要返回一个
    # 静默的空（空会被读成"数据没取到"，同 `_na` 那条）。
    alt = stk.alt_kind(code, root)
    if alt:
        return stk._na(alt, {'sw_levels': [], 'blocks': []})
    jc = stk.norm_code(code)
    if not jc:
        raise MarketError('认不出代码：%r' % code)
    c = con()
    p = panel(root)
    d = _day(c, p, None)
    # 🔴 三级申万一起给：列名从 `SW_LEVELS` 取，**不在这里再手写一遍**
    #   `sw_l1_code` —— 那就是第二份实现（板块页的下钻已经在用它，两边
    #   分叉的话是「看着完全正常、只是某天开始给错答案」）。
    lv = [(k,) + SW_LEVELS[k] for k in ('sw', 'sw_l2', 'sw_l3')]
    sel = ', '.join('%s, %s' % (cc, nc) for _k, cc, nc, _kn, _pc in lv)
    row = c.execute(
        'SELECT symbol, %s FROM %s '
        "WHERE jq_code = ? ORDER BY date DESC LIMIT 1" % (sel, p), [jc]).fetchone()
    if not row:
        raise MarketError('面板里没有 %s' % jc)
    sym = row[0]
    # ★ 某一级为空就**不给这一行**（而不是给一个空 chip）——「未知不是缺」
    #   的另一面：面板里 5211 只有 5200 只三级齐全，剩下那些是真的没有分类。
    sw_levels = [{'kind': k, 'kind_name': kn, 'code': row[1 + i * 2],
                  'name': row[2 + i * 2]}
                 for i, (k, _cc, _nc, kn, _pc) in enumerate(lv) if row[2 + i * 2]]
    out = {'code': jc, 'date': str(d), 'sw_levels': sw_levels, 'blocks': []}
    # 🔴 通达信板块读不动时**不拦**：申万归属来自面板，照样给得出来。
    #   但要把原因带上去（`blocks_error`），否则页面显示「没有板块归属」
    #   —— 那是在替这只票下一个它没资格下的结论。
    try:
        for bc, (name, kind, syms) in blocks(root).items():
            if sym in syms:
                out['blocks'].append({'code': bc, 'name': name, 'kind': kind,
                                      'kind_name': BLOCK_TYPE.get(kind, kind),
                                      'n': len(syms)})
    except BlocksLocked as e:
        out['blocks_error'] = str(e)
    out['blocks'].sort(key=lambda x: (x['kind'] != 'concept', x['n']))
    return out
