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
    return root or os.environ.get('ASSAY_DATALAKE') or os.path.normpath(
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     '..', 'datalake'))


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
    pan = "read_parquet('%s/mart/panel_daily/panel_*.parquet')" % root
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
