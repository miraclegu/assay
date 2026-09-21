# -*- coding: utf-8 -*-
"""symbols.py —— **「代码 -> 类别 / 名称 / 日线」的唯一正本。**

🔴🔴 **本模块存在的全部理由：这套逻辑此前有五份实现、三种行为。**
2026-09-21 用户报「首页显示 513120.XSHG 的编码但没有中文名，点进去也没有
日 K，而实盘-模拟盘页可以」—— 查下来不是"找不到"，是**三处分叉**：

    ① 代码口径   stock.kline('sh513120')    -> 10 根
                 stock.kline('513120.XSHG') -> 取不到（而账本记的就是这个口径）
    ② 取名       watchlist.valued() 只查【股票面板】-> name='' -> 页面回落成代码
                 （实测自选里 5 只 ETF 全都没名字）
    ③ 名称清洗   lv/tdx.names   -> '港股创新药ETF广'      去掉了 U+FFFD
                 stock._alt_name -> '港股创新药ETF广\ufffd'  没去 -> 屏幕上一个乱码方块

★ 第 ① 条 **CLAUDE.md 自己预言过**：「个股页那份 alt_panel 按 symbol 认标的，
  而实盘账本记的是聚宽口径，所以这一层多的只有两种口径之间的换算」——
  换算写进了 `lv/tdx.to_symbol`，**但 stock.py 没用它**。当时只补了 lv/ 那一半。

🔴 **为什么从 `lv/` 搬出来**：它是「面板之外的标的」这个**通用**能力，
  却落在**实盘域**里。于是 `stock.py` / `watchlist.py` 想用得跨域 import，
  结果各自又写了一份 —— 那正是分叉的一半原因。
  `lv/tdx.py` 保留为**纯转发门面**，实盘那边的 import 一个都不用改。

**对外契约**（新增的统一层在文件末尾）：

    to_symbol(code)        聚宽/symbol -> tdx symbol        `513120.XSHG` -> `sh513120`
    to_jq(symbol)          反向                              `sh513120` -> `513120.XSHG`
    kind_of(code, root)    'etf' / 'index' / None（None = 当股票走原路）
    clean_name(s)          **唯一**一处名称清洗（U+FFFD + strip）
    names(codes, ...)      面板优先 + tdx 回落 + 统一清洗
    last_close / day_ohlc / daily_close / name_snap          （从 lv/tdx 原样搬）

🔴 **只兜 ETF 与指数，不兜股票。** 股票本来就在面板里，而
  `raw/tdx/kline/stock_*.parquet` 是 1600 万行 —— 拿它当兜底既慢又没必要。
★ 价格一律**不复权**：算的是市值，对的是券商 App 里那个数。
"""
import csv
import io
import os
import re
from assay import paths as _paths   # datalake 根的唯一解析

# ★ 与 `lv/perf.py` 的 `_KIND_FILE` 同一份含义；这里只列会用到的两类。
KIND_FILE = {'etf': 'etf_*', 'index': 'index_*'}
_MK2PFX = {'XSHG': 'sh', 'XSHE': 'sz'}


def to_symbol(code):
    """聚宽口径 -> tdx symbol：`513120.XSHG` -> `sh513120`。认不出给 None。

    ★ **不走 `normalize_code`**：那条按**股票**前缀规则校验市场
      （60/68/90 沪、00/30/20 深），而 ETF 是 51/15/16/56… 开头 ——
      它对股票是对的（市场决定过户费收不收），对 ETF 不适用
      （同「指数按 symbol 认，不去动 normalize_code」那条）。
    """
    s = str(code or '').strip()
    if '.' not in s:
        return None
    num, _, mk = s.partition('.')
    pfx = _MK2PFX.get(mk.upper())
    if not pfx or not num.isdigit() or len(num) != 6:
        return None
    return pfx + num


def name_snap(root):
    """最新那份 symbol_name 快照（PIT 目录里挑 snap_date 最大的）。

    ★ 照 `manifest.csv` 找，不去 glob 目录按文件名猜 —— 文件名是内容哈希，
      新旧看不出来（同"判据要对上为什么保留"那条）。
    """
    mf = os.path.join(root, 'raw/tdx/snapshots/manifest.csv')
    if not os.path.exists(mf):
        return None
    best = None
    with io.open(mf, encoding='utf-8') as f:
        for r in csv.DictReader(f):
            if r.get('dataset') != 'symbol_name':
                continue
            if best is None or (r.get('snap_date') or '') > (best.get('snap_date') or ''):
                best = r
    if not best:
        return None
    fp = os.path.join(root, 'raw/tdx/snapshots', best['version_file'])
    return fp if os.path.exists(fp) else None


def _files(root):
    out = []
    for pat in KIND_FILE.values():
        p = os.path.join(root, 'raw/tdx/kline', pat + '.parquet')
        import glob as _g
        if _g.glob(p):
            out.append(p)
    return out


def last_close(root, codes, day):
    """{聚宽代码: (收盘, 那天的日期, 昨收)} —— 形状与 `perf._last_px` 一致。

    ★ kline 里**没有 preclose 这一列**，用 `lag(close)` 现推 ——
      而那正是"上一个有行情的日子的收盘"，停牌/未上市自然跳过，
      与面板那一列的语义相同。
    """
    sym = {}
    for c in (codes or []):
        s = to_symbol(c)
        if s:
            sym[s] = c
    files = _files(root) if sym else []
    if not files:
        return {}
    import duckdb
    q = "','".join(sym)
    src = ' UNION ALL '.join(
        "SELECT symbol, date, close FROM read_parquet('%s')" % f for f in files)
    rows = duckdb.connect().execute("""
        SELECT symbol, close, date, prev FROM (
          SELECT symbol, close, date,
                 lag(close) OVER (PARTITION BY symbol ORDER BY date) AS prev,
                 row_number() OVER (PARTITION BY symbol ORDER BY date DESC) rn
          FROM (%s)
          WHERE symbol IN ('%s') AND date <= DATE '%s'
            AND date > DATE '%s' - INTERVAL 400 DAY
        ) WHERE rn = 1""" % (src, q, day, day)).fetchall()
    return {sym[r[0]]: (r[1], r[2], r[3]) for r in rows if r[0] in sym}


def day_ohlc(root, code, date):
    """某只 ETF / 指数某天的 (open, high, low, close)，不复权。取不到给 None。

    ★ 与 `last_close` 分开：那个是"≤ 某天的最后一条"（停牌按最后已知价挂账），
      这个是**就那一天**（录成交时要的是当天的价，没有就该说没有）。
    """
    sym = to_symbol(code)
    files = _files(root) if sym else []
    if not files:
        return None
    import duckdb
    src = ' UNION ALL '.join(
        "SELECT symbol, date, open, high, low, close FROM read_parquet('%s')" % f
        for f in files)
    row = duckdb.connect().execute(
        "SELECT open, high, low, close FROM (%s) WHERE symbol = ? AND date = DATE '%s'"
        % (src, date), [sym]).fetchone()
    if not row or row[3] is None:
        return None
    return tuple(None if v is None else float(v) for v in row)


def daily_close(root, codes, since):
    """{(聚宽代码, 日期): 收盘} —— `since` 起的整段，不复权。

    ★ 与 `last_close` 的区别：那个是"每只一行（≤ 某天的最后一条）"，
      这个是**整段逐日**，权益曲线与每日持仓要的就是它。
    """
    sym = {}
    for c in (codes or []):
        t = to_symbol(c)
        if t:
            sym[t] = c
    files = _files(root) if sym else []
    if not files:
        return {}
    import duckdb
    q = "','".join(sym)
    src = ' UNION ALL '.join(
        "SELECT symbol, date, close FROM read_parquet('%s')" % f for f in files)
    rows = duckdb.connect().execute(
        "SELECT symbol, date, close FROM (%s) "
        "WHERE symbol IN ('%s') AND date >= DATE '%s'" % (src, q, since)).fetchall()
    return {(sym[r[0]], r[1]): r[2] for r in rows if r[0] in sym}


def alt_names(root, codes):
    """{聚宽代码: 名称} —— **只查 ETF/指数的名称快照**。

    ★ 改名自 `lv/tdx.names`：`names` 这个名字让给下面那个**统一入口**
      （面板优先 + 本函数回落）。`lv/tdx.py` 的门面仍叫 names，对外契约没变。
    ★ 取不到就不给这个键 —— **不填代码当名字**（那会让"没查到"和
      "它就叫这个"分不出来）。
    """
    sym = {}
    for c in (codes or []):
        s = to_symbol(c)
        if s:
            sym[s] = c
    fp = name_snap(root) if sym else None
    if not fp:
        return {}
    import duckdb
    q = "','".join(sym)
    rows = duckdb.connect().execute(
        "SELECT symbol, name FROM read_parquet('%s') WHERE symbol IN ('%s')"
        % (fp, q)).fetchall()
    # ★ tdx 的名称字段是**定长 16 字节**，截断处可能劈开一个汉字 ——
    #   读出来末尾就是个 U+FFFD（实测「港股创新药ETF广<?>」）。
    #   把它去掉：那个字符不携带任何信息，而屏幕上是个乱码方块。
    #   **只修显示，不动数据**（快照是 PIT 证据）。
    return {sym[r[0]]: clean_name(r[1])
            for r in rows if r[0] in sym and clean_name(r[1])}


# ==================== 统一层：这以下是「唯一正本」的对外入口 ====================
#
# 🔴 上面那些是从 `lv/tdx.py` 原样搬来的实现（只兜 ETF/指数）。
#   下面这一层才是给**所有域**用的入口：它不关心标的是股票还是 ETF，
#   调用方也不该关心 —— 那正是此前五处各写一遍的东西。

_RE_SYM = re.compile(r'^(sh|sz)\d{6}$')
_PFX2MK = {'sh': 'XSHG', 'sz': 'XSHE'}
_KMAP = {'at': None, 'map': None}


def default_root(root=None):
    """datalake 根目录。与 `lv/px._lake` / `stock._lake` 同一套规则。"""
    return _paths.datalake(root)


def clean_name(s):
    """**唯一**一处名称清洗。

    🔴 tdx 的名称字段是**定长 16 字节**，截断处会劈开一个汉字 ——
      读出来末尾是个 U+FFFD（实测「港股创新药ETF广<?>」）。
      2026-09-21 实测：`lv/tdx` 去了、`stock._alt_name` 没去 ——
      **同一只票在两个页面上两个名字**。所以清洗必须只有一处。
    ★ 只修显示，**不动快照**（那是 PIT 证据）。
    """
    return str(s or '').replace(u'\ufffd', '').strip()


def to_jq(symbol):
    """tdx symbol -> 聚宽口径：`sh513120` -> `513120.XSHG`。认不出给 None。"""
    t = str(symbol or '').strip().lower()
    if not _RE_SYM.match(t):
        return None
    return '%s.%s' % (t[2:], _PFX2MK[t[:2]])


def as_symbol(code):
    """把**任意**写法折成 tdx symbol：聚宽口径 / 已经是 symbol 都认。

    🔴 与 `to_symbol` 的区别：那个**只**认聚宽口径（带点的）。
      分叉就出在这里 —— `stock.alt_kind` 只认 symbol、账本只给聚宽口径，
      两边谁都不肯多走一步。**这个函数就是那"一步"。**
    ★ 裸六位（`513120`）**不认**：沪深都可能有，猜错不报错。
    """
    t = str(code or '').strip().lower()
    if _RE_SYM.match(t):
        return t
    return to_symbol(code)


def _kind_map(root=None):
    """{symbol: class}，只收 etf / index。按快照文件缓存。"""
    fp = name_snap(default_root(root))
    if not fp:
        return {}
    if _KMAP['at'] == fp and _KMAP['map'] is not None:
        return _KMAP['map']
    import duckdb
    rows = duckdb.connect(':memory:').execute(
        "SELECT symbol, class FROM read_parquet('%s') "
        "WHERE class IN ('index','etf')" % fp).fetchall()
    m = {r[0]: r[1] for r in rows}
    _KMAP['at'], _KMAP['map'] = fp, m
    return m


def kind_of(code, root=None):
    """`'etf'` / `'index'` / `None`。None = 当股票走面板那条原路。

    ★ 按**快照里的 class** 判，不按代码前缀猜 —— 前缀规则是会变的
      （科创 688、北交 8 开头都出现过），而快照是事实。
    """
    sym = as_symbol(code)
    return _kind_map(root).get(sym) if sym else None


def resolve(code, root=None):
    """任意写法 -> `{'symbol':…, 'jq':…, 'kind': 'etf'|'index'|None}`。

    `kind is None` 表示"按股票处理"（可能是股票，也可能是认不出来的东西 ——
    那由调用方的原有逻辑去报错，本函数不替它判）。
    """
    sym = as_symbol(code)
    return {'symbol': sym, 'jq': to_jq(sym) if sym else None,
            'kind': (_kind_map(root).get(sym) if sym else None)}


def names(codes, day=None, root=None):
    """**代码 -> 中文名的唯一入口**：面板优先、ETF/指数回落、统一清洗。

    ★ 取 <= day 的最后一个非空 `sec_name`（400 天内）：退市/改名的票也能
      显示，而不是留个空白让人对着代码猜（原 `lv/px.names_of` 的规矩）。
    🔴 **取不到就不给这个键**，绝不拿代码当名字填进去 —— 那会让
      "查不到"和"它就叫这个"在页面上分不出来，而调用方通常写
      `name || code`，回落是它的事、不是这里的事。
    """
    root = default_root(root)
    want = []
    for c in (codes or []):
        t = str(c or '').strip()
        if not t:
            continue
        jq = to_jq(t) if _RE_SYM.match(t.lower()) else t.upper()
        want.append(jq)
    want = sorted(set(want))
    if not want:
        return {}
    out = {}
    import duckdb
    pan = _panel_sql(root)
    try:
        con = duckdb.connect(':memory:')
        d = day or con.execute('SELECT max(date) FROM %s' % pan).fetchone()[0]
        if d:
            rows = con.execute("""
                SELECT code, sec_name FROM (
                  SELECT jq_code AS code, sec_name,
                         row_number() OVER (PARTITION BY jq_code ORDER BY date DESC) rn
                  FROM %s
                  WHERE jq_code IN ('%s') AND date <= DATE '%s'
                    AND date > DATE '%s' - INTERVAL 400 DAY
                ) WHERE rn = 1""" % (pan, "','".join(want), d, d)).fetchall()
            out = {c: clean_name(n) for c, n in rows if clean_name(n)}
    except Exception:
        out = {}          # 面板读不动不该让取名整个失败 —— ETF 那条腿还在
    miss = [c for c in want if not out.get(c)]
    if miss:
        out.update(alt_names(root, miss))
    return out


# ==================== 取价：面板优先 + ETF/指数回落 ====================
#
# 🔴🔴 **这一层存在的理由与上面取名那层完全相同：知识不该散在四处。**
#   2026-09-21 普查发现「查面板 -> 查不到 -> 回落 tdx」这个**模式**在四个
#   取价函数里各写了一遍（`perf._last_px` / `px.daily_close_map` /
#   `px.day_price` / `px.day_range`）。它已经出过事，而且出过两次：
#   2026-09-18「ETF 持仓市值 0.00 / 浮盈 −100%」，修了 4 处之后
#   **又发现还漏了 3 处**（权益曲线 / 每日持仓 / 出信号）。
#   每加一个标的类别就要记得改 N 处 —— 而漏掉的那处**不报错**。
#
# ★ 抽的不是样板代码（四者的 SQL 与返回形状差得远），抽的是
#   **「谁知道 ETF/指数住在别处」这件事**。收进这里之后，将来加第三类标的
#   只改这一个文件。
# 🔴 **报错措辞留给调用方**：这里取不到一律给 None / 不给那个键。
#   `px.day_price` 那句"是不是没同步"的判断是**实盘录入的 UX**，
#   与"取不到"是两件事（同「报错必须指向真正的原因」那条）。


def _panel_sql(root):
    return _paths.panel_sql(root)


def _con(con=None):
    if con is not None:
        return con
    import duckdb
    return duckdb.connect(':memory:')


def last_px(root, codes, day, con=None):
    """{聚宽代码: (收盘, 那天的日期, 那天的昨收)}，按 <= day 取最近一条。

    停牌股拿最后已知价 —— 与 broker「按最后已知价挂账」一致。
    ★ 一并带回 `preclose` 是为了算当日涨跌：面板里有这一列，不用自己
      回去找上一个交易日（停牌股的"上一个交易日"还得逐只算）。
    🔴 面板查不到的回落到 tdx —— 不回落的话价格 None -> 市值 0 ->
      浮盈 −100%，**而它不报错**，看着就像"这个账户把钱亏光了"。
    """
    codes = list(codes or [])
    if not codes:
        return {}
    rows = _con(con).execute("""
        SELECT code, close_bfq, date, preclose FROM (
          SELECT jq_code AS code, close_bfq, date, preclose,
                 row_number() OVER (PARTITION BY jq_code ORDER BY date DESC) rn
          FROM %s
          WHERE jq_code IN ('%s') AND date <= DATE '%s'
            AND date > DATE '%s' - INTERVAL 400 DAY
        ) WHERE rn = 1""" % (_panel_sql(root), "','".join(codes), day, day)).fetchall()
    out = {r[0]: (r[1], r[2], r[3]) for r in rows}
    miss = [c for c in codes if c not in out]
    if miss:
        out.update(last_close(root, miss, day))
    return out


def daily_close_map(root, codes, since, con=None):
    """{(聚宽代码, 日期): 不复权收盘} —— 整段逐日。

    🔴 权益曲线与每日持仓**共用这一处**：它们本来是抄了两份的，
      于是给 ETF 加回落时只改了一处，**两页对同一天给出不同的总资产**。
    """
    codes = list(codes or [])
    if not codes:
        return {}
    out = {}
    for c, dd, p in _con(con).execute("""
        SELECT jq_code, date, close_bfq FROM %s
        WHERE jq_code IN ('%s') AND date >= DATE '%s'
    """ % (_panel_sql(root), "','".join(codes), since)).fetchall():
        out[(c, dd)] = p
    miss = [c for c in codes if not any(k[0] == c for k in out)]
    if miss:
        out.update(daily_close(root, miss, since))
    return out


def day_px(root, code, date, which='open', con=None):
    """某只票某天的【不复权】价 -> `(值, 来源)`，取不到给 `(None, None)`。

    `which` = 'open' / 'close'；`来源` = 'panel' | 'alt'。
    🔴 **不报错是刻意的** —— "取不到"与"该怎么跟人解释"是两件事，
      后者（是不是没同步 / 是不是停牌 / 是不是代码写错）属于调用方。
    🔴 **不在这里 round，把来源一并返回** —— 因为 `px.day_price` 里
      **面板那条路 round 到 3 位、tdx 那条路 round 到 4 位**（既有的不一致，
      2026-09-21 抽正本时发现）。重构阶段先**逐位保真**，把这个不一致
      暴露到签名上，要不要统一是另一个决定 —— 悄悄改精度不算重构。
    """
    col = 'open' if which == 'open' else 'close_bfq'
    c = _con(con)
    row = c.execute("SELECT %s FROM %s WHERE jq_code = ? AND date = DATE '%s'"
                    % (col, _panel_sql(root), date), [code]).fetchone()
    if row is not None and row[0] is not None:
        return (float(row[0]), 'panel')
    alt = day_ohlc(root, code, date)          # (open, high, low, close)
    if alt is None:
        return (None, None)
    v = alt[0] if which == 'open' else alt[3]
    return ((None, None) if v is None else (float(v), 'alt'))


def panel_last_day(root, con=None):
    """面板最新到哪天（`datetime.date`，没有就 None）。

    ★ 「本地行情最新到哪天」是判断"信号新不新"的第一依据，而它此前在
      `px.latest_data_day` 与 `px.day_price` 的报错分支里**各查了一遍**。
    """
    c = _con(con)
    row = c.execute('SELECT MAX(date) FROM %s' % _panel_sql(root)).fetchone()
    return row[0] if row and row[0] else None


def panel_probe(root, code, date, con=None):
    """给【报错措辞】用的两个事实 -> `(面板最新日, 这天这只票有没有行)`。

    🔴 它不取价，只回答"是不是没同步" —— 而这两件事要做的动作完全不同：
      面板最新日 < 请求日 = 等晚上同步；>= 还查不到 = 停牌或代码写错。
    ★ 放这里是因为**「怎么查面板」只许有一份**：留在 `px.day_price` 里的话，
      那个文件就又有了自己的一条面板 SQL（守卫抓到过一次）。
    """
    c = _con(con)
    p = _panel_sql(root)
    last = panel_last_day(root, con=c)
    has = c.execute("SELECT count(*) FROM %s WHERE date = DATE '%s' "
                    "AND jq_code = ?" % (p, date), [code]).fetchone()[0]
    return (last, bool(has))


def day_hl(root, code, date, con=None):
    """某只票某天的 (最低, 最高)，不复权。取不到给 **None**。"""
    c = _con(con)
    row = c.execute("SELECT low, high FROM %s WHERE jq_code = ? AND date = DATE '%s'"
                    % (_panel_sql(root), date), [code]).fetchone()
    if row and row[0] is not None and row[1] is not None:
        return (round(float(row[0]), 3), round(float(row[1]), 3))
    alt = day_ohlc(root, code, date)
    if alt is None or alt[1] is None or alt[2] is None:
        return None
    return (round(float(alt[2]), 3), round(float(alt[1]), 3))


# ======================= 面板之外的标的：拼成面板的样子 =======================
# 🔴 这一层 2026-09-21 从 `stock.py` **并过来**。此前它一半在这里
#   （`kind_of` / `alt_names` / `name_snap`）、一半在那边（`alt_panel` /
#   `_alt_index` / `_alt_name` / `_ALT_FILE` / `_alt_map`），而 `_alt_map`
#   与这里的 `_kind_map` 是**逐字相同的两份**（并完才发现它已经没人调了 ——
#   上一轮把 `alt_kind` 改成转发时留下的尸体）。


def kind_map(root=None):
    """{symbol: class}，只收 etf / index。按快照文件缓存。"""
    return _kind_map(root)


def alt_name(sym, root=None):
    """ETF / 指数的中文名。取不到才退回 symbol（页面总得显示点什么）。"""
    jq = to_jq(sym)
    got = alt_names(default_root(root), [jq]) if jq else {}
    return got.get(jq) or sym


def alt_panel(kind, root=None):
    """把 ETF / 指数的日线**拼成与面板同形**的子查询（列名一字不差）。

    ★ 同形是关键：于是 K 线 / 全部指标 / 翻页 / 复权 / 框选**一个字都不用改**
      （另写一套的话，「同一个指标在股票上和在 ETF 上算出来不一样」迟早发生，
      而它不报错）。
    🔴 **一律给出后复权列**（`close × coalesce(hfq_factor, 1)`）：
      ETF 会分红（红利 ETF 的因子 1.0 -> 1.81，那 81% 全是分红），
      不复权跨除权日有假跌幅 —— 与「对比页一律后复权」同一条纪律。
      指数不除权（因子表里没有它的行），`coalesce` 到 1 正好。
    ★ 面板有而这里没有的（换手率、涨跌停价、估值、财务）一律 NULL ——
      **不猜一个数填上去**。
    """
    lake = default_root(root)
    K = "read_parquet('%s/raw/tdx/kline/%s.parquet')" % (lake, KIND_FILE[kind])
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


def alt_rows(con, root=None):
    """本地**真有日线**的 ETF / 指数，带名称与最新价 —— 给搜索用。

    🔴 **只收本地真有日线的**：名称快照里有 166 只 ETF / 352 只股票在 kline
      里根本没有行（退市、未上市、北交所新股）。列出来点了什么都不出来，
      比不给这个选项更糟（同 backLink 那条）。做法是拿 kline 与快照
      **INNER JOIN**，没有日线的天然进不来。
    ★ `floatmv` 给 None：ETF / 指数没有流通市值。搜索排序里它是"同分再按
      市值降序"的那一档，None 当 0 —— 于是同分时股票排在前面，
      而人搜 6 位数字时要的通常就是股票。
    """
    fp = name_snap(default_root(root))
    if not fp:
        return []
    lake = default_root(root)
    out = []
    for kind, pat in (('index', KIND_FILE['index']), ('etf', KIND_FILE['etf'])):
        K = "read_parquet('%s/raw/tdx/kline/%s.parquet')" % (lake, pat)
        try:
            rows = con.execute("""
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
