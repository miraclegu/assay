"""盘中 1 分钟线：抓取 / 落盘 / 供持仓算实时盈亏。

## 两个源，各干各的（这是稳定性的关键）

    腾讯 qt.gtimg.cn   **批量**快照   一次请求拿全部持仓的现价  -> 实时盈亏
    东财 trends2       单只            一次拿当天全部 1 分钟     -> 落盘的 bar

🔴 **不要用"每分钟给每只都打一次 trends2"** —— 25 只持仓就是 25 请求/分钟、
   一个交易日 6000 次，**必被限流**（实测十几次就把 push2his 打到连 TCP 都不给，
   见索引 5 §3.1）。

改成：
  · 现价走腾讯批量 —— **1 次请求覆盖全部持仓**（实测 25 只 340ms，
    20 连打全过）。实时盈亏只需要现价，这一条就够。
  · bar 走 trends2，但**每轮只抓几只、轮着来**。因为 trends2 每次返回
    当天**全量**，抓到谁谁就补齐了，漏几轮完全没关系 —— 这个幂等性
    正是选它的理由。25 只按每轮 3 只算，25 分钟轮一遍，请求速率
    降到 ~3 次/分钟。
  · 收盘那一轮（15:00 之后）对**全部**持仓强抓一遍，保证当天 bar 完整。

## 为什么 bar 用东财 trends2 而不是新浪

索引 5（`datalake/docs/数据字典/5-外部行情接口.md`）里两个源都能给 1 分钟：

    新浪 quotes.sina.cn  scale=1  一次 1023 条 ≈ 5 个交易日
    东财 push2his        trends2  一次【当天全部】分钟

轮询要的是**幂等**：`trends2` 每次返回的是当天从 09:30 到此刻的**全量**，
所以漏几次不要紧 —— 下一次自动把缺口补齐。用"只取最新一根"的接口就得
自己记断点、自己补洞，而补洞逻辑写错了不会报错，只会让某几分钟凭空消失。

新浪那条留给"回填历史"（本模块不做）。

## 东财会静默限流（见索引 5 §3.1）

连打十几次之后 `push2his` **连 TCP 都不给**（`RemoteDisconnected`，不是 HTTP
错误码），编号子域 `33./63.` 同一套限流一起挂，只有 `push2delay` 还通。
所以这里：

  · 两个主机**轮换 + 互为回退**
  · 每只之间 `SLEEP` 限速（持仓十几只，一分钟内跑完绰绰有余）
  · **空结果一律当失败**（东财限流时会返回 `data: null` 而不是报错，
    当成"这只票没数据"就会把持仓价悄悄清空）

## 存哪

`datalake/rt/minute_1m/<YYYY-MM-DD>.parquet`，**顶层独立目录**。

🔴 刻意不放 `mart/` —— `build_panel_daily.py` 那条链一个脚本都不碰 `rt/`，
   回测也读不到它。分钟数据进了日频回测链就是灾难：引擎是日频的，
   多出来的行会改变"一天"的语义，而这**不会报错**。
"""
import datetime
import json
import os
import threading
import time
import urllib.request

import duckdb

# ---- 交易时段（用户指定；收盘那分钟要留到 15:01 才能拿到 15:00 那根）----
SESSIONS = ((datetime.time(9, 30), datetime.time(11, 31)),
            (datetime.time(13, 0), datetime.time(15, 1)))
HOSTS = ('push2his.eastmoney.com', 'push2delay.eastmoney.com')
SLEEP = 0.35              # 每只之间的间隔，防限流
TIMEOUT = 12
HDR = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://quote.eastmoney.com/'}
# trends2 的 fields2 顺序（**实测对准过，别照抄别处**）：
#   f51=时间 f52=开 f53=收 f54=高 f55=低 f56=量(手) f57=额(元) f58=均价
# 🔴 少请求一个字段就【整行错位】而不报错：漏掉 f52 时返回 7 段，
#    解析器按 8 段读会把"收"当成"开"、把均价读成越界 —— 踩过。
COLS = ('datetime', 'open', 'close', 'high', 'low', 'volume', 'amount', 'avg')

_lock = threading.Lock()
_host_i = 0


class RTError(Exception):
    pass


def _lake(root=None):
    r = root or os.environ.get('ASSAY_DATALAKE') or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        '..', 'datalake')
    r = os.path.normpath(r)
    if not os.path.isdir(r):
        raise RTError('找不到 datalake：%s' % r)
    return r


# 两个库，**刻意分开**：
#   minute_1m  东财 trends2 的真 OHLCV bar（权威，但抓取会被限流、可能缺）
#   snap_1m    腾讯批量快照按分钟留一行（每分钟每只都有，实时盈亏用它）
# 🔴 不合成一张表：两种来源的 high/low 语义不同（bar 是那一分钟真实高低，
#    快照只是采样时点）。混在一列里"不一致但不报错"，而这个项目最怕这个。
def store_dir(kind='minute_1m', root=None):
    d = os.path.join(_lake(root), 'rt', kind)
    os.makedirs(d, exist_ok=True)
    return d


def day_file(day=None, root=None, kind='minute_1m'):
    d = day or datetime.date.today()
    return os.path.join(store_dir(kind, root), '%s.parquet' % d)


# ---------------------------------------------------------------- 时段
def in_session(now=None):
    """现在是不是抓取时段。**只判时刻，不判交易日** —— 交易日由调用方给。"""
    t = (now or datetime.datetime.now()).time()
    return any(a <= t <= b for a, b in SESSIONS)


def is_trading_day(day=None, root=None):
    """交易日历来自本地（tdx.raw_holidays 推的那份，见 live.calendar_meta）。

    ★ 取不到就返回 None（不是 False）—— "不知道"和"确定不是"是两件事，
      前者应该让调用方决定要不要照抓，后者才该跳过。
    """
    try:
        from assay import live as lv
        d = day or datetime.date.today()
        # ★ calendar_days() 返回的是 datetime.date 对象，不是字符串 ——
        #   拿 isoformat() 去 in 会永远是 False，而那表现为"今天不是交易日"、
        #   于是整个轮询静默不跑。
        return d in set(lv.calendar_days())
    except Exception:                                       # noqa: BLE001
        return None


# ---------------------------------------------------------------- 抓取
def _secid(jq_code):
    """`601857.XSHG` -> `1.601857`（东财 secid：1=沪，0=深/北）。"""
    from assay import stock as st
    jc = st.norm_code(jq_code)
    if not jc:
        raise RTError('认不出代码：%r' % jq_code)
    num, mk = jc.split('.')
    return ('1.' if mk == 'XSHG' else '0.') + num, jc


def _get(url):
    r = urllib.request.Request(url, headers=HDR)
    return urllib.request.urlopen(r, timeout=TIMEOUT).read().decode('utf-8', 'replace')


def fetch_one(jq_code, ndays=1):
    """抓一只票【当天全部】1 分钟线。返回 (jq_code, [dict,...])。

    ★ 两个主机轮换：不是"主用一个坏了才换"，而是**每次换着用** ——
      把请求摊到两个主机上，本来就不容易触发单主机的限流。
    """
    global _host_i
    secid, jc = _secid(jq_code)
    q = ('/api/qt/stock/trends2/get?secid=%s&fields1=f1,f2,f3,f4,f5,f6,f7,f8'
         '&fields2=f51,f52,f53,f54,f55,f56,f57,f58&iscr=0&ndays=%d' % (secid, ndays))
    last = None
    for k in range(len(HOSTS)):
        with _lock:
            host = HOSTS[(_host_i + k) % len(HOSTS)]
            if k == 0:
                _host_i = (_host_i + 1) % len(HOSTS)
        try:
            d = (json.loads(_get('https://' + host + q)) or {}).get('data')
        except Exception as e:                              # noqa: BLE001
            last = '%s: %s' % (type(e).__name__, str(e)[:60])
            continue
        # 🔴 限流时会返回 data:null 或空 trends —— 一律当失败，不能当
        #    "这只票没数据"（那会把持仓价悄悄清空）
        if not d or not d.get('trends'):
            last = '%s 返回空（多半是限流）' % host
            continue
        out = []
        for line in d['trends']:
            p = str(line).split(',')
            if len(p) < 8:
                continue
            out.append({'code': jc, 'datetime': p[0],
                        'open': float(p[1]), 'close': float(p[2]),
                        'high': float(p[3]), 'low': float(p[4]),
                        'volume': float(p[5]), 'amount': float(p[6]),
                        'avg': float(p[7])})
        if out:
            return jc, out, d.get('preClose')
        last = '%s 解析出 0 根' % host
    raise RTError('取不到 %s 的分钟线：%s' % (jq_code, last))


# ---------------------------------------------------------------- 批量快照
TX_URL = 'http://qt.gtimg.cn/q='
# 单次批量上限。实测 900 ✓ / 950 ❌ HTTP 414（URL ~6.3KB 是界限）。
# 取 400 留足余量，且避开大批量时十几秒的长尾。
TX_BATCH = 400
# 腾讯 qt 的字段位。★ 逐位对过真值（用同日分钟线聚合 + 本地面板消歧），
#   见索引 5 §1.4。别照抄网上流传的表 —— f39 不是"最低"。
TX_F = {'name': 1, 'code': 2, 'price': 3, 'preclose': 4, 'open': 5,
        'ts': 30, 'change': 31, 'change_pct': 32, 'high': 33, 'low': 34,
        'volume': 36, 'amount_wan': 37, 'turnover_pct': 43}


def snapshot(codes, root=None):
    """腾讯批量快照。**一次请求拿全部**，实时盈亏用它。

    ★ 返回 {jq_code: {...}}。取不到就不在字典里 —— 调用方据此显示
      "这只没有实时价"，而不是拿旧价顶上。
    """
    from assay import stock as st
    m = {}
    for c in codes:
        jc = st.norm_code(c)
        if jc:
            m[('sh' if jc.endswith('XSHG') else 'sz') + jc[:6]] = jc
    if not m:
        return {}
    out = {}
    # ★ 一次能拼多少：**实测 900 只可以、950 只起 HTTP 414 (URI Too Long)**
    #   （URL 约 6.3KB 是界限；不是截断，是直接 414）。
    #   但耗时随批量非线性抖：25 只中位 667ms、最大 8.6s；800 只 10~13s。
    #   取 400 —— 持仓规模（几十只）一批就够，而万一拿它扫大批量时
    #   单批也不会卡到十几秒。
    keys = list(m)
    for i in range(0, len(keys), TX_BATCH):
        chunk = keys[i:i + TX_BATCH]
        try:
            txt = urllib.request.urlopen(
                urllib.request.Request(TX_URL + ','.join(chunk),
                                       headers={'User-Agent': HDR['User-Agent']}),
                timeout=TIMEOUT).read().decode('gbk', 'replace')
        except Exception:                                   # noqa: BLE001
            continue                    # 这一批没拿到；不影响别的批
        for line in txt.strip().split('\n'):
            if '="' not in line:
                continue
            f = line.split('"')[1].split('~')
            if len(f) <= TX_F['turnover_pct']:
                continue
            tag = line.split('v_')[1].split('=')[0] if 'v_' in line else ''
            jc = m.get(tag)
            if not jc:
                continue
            def _n(k, d=None):
                try:
                    return float(f[TX_F[k]])
                except Exception:                           # noqa: BLE001
                    return d
            px = _n('price')
            if not px:
                continue                # 停牌/无成交 -> 不给，不要塞 0
            out[jc] = {'code': jc, 'name': f[TX_F['name']], 'price': px,
                       'preclose': _n('preclose'), 'open': _n('open'),
                       'high': _n('high'), 'low': _n('low'),
                       'change_pct': _n('change_pct'),
                       'turnover_pct': _n('turnover_pct'),
                       'volume': _n('volume'),
                       'amount': (_n('amount_wan') or 0) * 1e4,
                       'ts': f[TX_F['ts']]}
    return out


def fetch(codes, root=None, sleep=SLEEP, on_progress=None):
    """抓一批并落盘。返回 {'ok': n, 'fail': [...], 'bars': n, 'day': ...}。

    ★ 单只失败不打断整批 —— 十几只里一只被限流，不该让另外十几只也没有价。
    """
    rows, fails, pre = [], [], {}
    for i, c in enumerate(codes):
        try:
            jc, bars, pc = fetch_one(c)
            rows.extend(bars)
            if pc:
                pre[jc] = float(pc)
        except Exception as e:                              # noqa: BLE001
            fails.append({'code': c, 'error': str(e)[:120]})
        if on_progress:
            on_progress(i + 1, len(codes))
        if sleep and i + 1 < len(codes):
            time.sleep(sleep)
    day = None
    if rows:
        day = rows[0]['datetime'][:10]
        save(rows, day, root)
    return {'ok': len(codes) - len(fails), 'fail': fails,
            'bars': len(rows), 'day': day, 'preclose': pre}


# ---------------------------------------------------------------- 落盘
def save(rows, day, root=None):
    """写当日 parquet。**按 (code, datetime) 去重**，同一天可以反复写。

    ★ trends2 每次给全天，所以这里是"整天覆盖"而不是"追加" ——
      追加会让同一分钟出现多行，而多出来的行不会报错，只会让
      成交量凭空翻倍。
    """
    if not rows:
        return None
    p = day_file(datetime.date.fromisoformat(day), root)
    con = duckdb.connect(':memory:')
    con.register('new', _to_arrow(rows))
    if os.path.isfile(p):
        con.execute("CREATE TABLE all_ AS SELECT * FROM read_parquet('%s')" % p)
        con.execute('INSERT INTO all_ SELECT * FROM new')
    else:
        con.execute('CREATE TABLE all_ AS SELECT * FROM new')
    tmp = p + '.tmp'
    con.execute("""
        COPY (SELECT code, datetime, open, close, high, low, volume, amount, avg
              FROM (SELECT *, row_number() OVER
                      (PARTITION BY code, datetime ORDER BY volume DESC) rn
                    FROM all_) WHERE rn = 1
              ORDER BY code, datetime)
        TO '%s' (FORMAT PARQUET)""" % tmp)
    os.replace(tmp, p)          # 先写 tmp 再 rename，同 _save_marks
    return p


SNAP_COLS = ('code', 'minute', 'price', 'preclose', 'open', 'high', 'low',
             'change_pct', 'turnover_pct', 'volume', 'amount', 'ts')


def save_snap(rows, root=None):
    """快照按【分钟】留一行（每只每分钟最后一次采样）。

    ★ 按分钟去重而不是按秒：一分钟内可能轮询多次，留最后一次即可 ——
      逐秒全留会让文件涨十几倍，而看盘用不到那个精度。
    """
    if not rows:
        return None
    import pyarrow as pa
    out = []
    for r in rows:
        ts = str(r.get('ts') or '')
        if len(ts) < 12:
            continue
        # 20260903120539 -> 2026-09-03 12:05
        minute = '%s-%s-%s %s:%s' % (ts[0:4], ts[4:6], ts[6:8], ts[8:10], ts[10:12])
        out.append({'code': r['code'], 'minute': minute, 'price': r['price'],
                    'preclose': r.get('preclose'), 'open': r.get('open'),
                    'high': r.get('high'), 'low': r.get('low'),
                    'change_pct': r.get('change_pct'),
                    'turnover_pct': r.get('turnover_pct'),
                    'volume': r.get('volume'), 'amount': r.get('amount'),
                    'ts': ts})
    if not out:
        return None
    day = out[0]['minute'][:10]
    p = day_file(datetime.date.fromisoformat(day), root, kind='snap_1m')
    con = duckdb.connect(':memory:')
    con.register('new', pa.table({k: [r[k] for r in out] for k in SNAP_COLS}))
    if os.path.isfile(p):
        con.execute("CREATE TABLE all_ AS SELECT * FROM read_parquet('%s')" % p)
        con.execute('INSERT INTO all_ SELECT * FROM new')
    else:
        con.execute('CREATE TABLE all_ AS SELECT * FROM new')
    tmp = p + '.tmp'
    con.execute("""
        COPY (SELECT %s FROM (SELECT *, row_number() OVER
                  (PARTITION BY code, minute ORDER BY ts DESC) rn FROM all_)
              WHERE rn = 1 ORDER BY code, minute)
        TO '%s' (FORMAT PARQUET)""" % (', '.join(SNAP_COLS), tmp))
    os.replace(tmp, p)
    return p


def _to_arrow(rows):
    import pyarrow as pa
    keys = ['code'] + list(COLS)
    return pa.table({k: [r[k] for r in rows] for k in keys})


# ---------------------------------------------------------------- 读取
def latest(codes=None, day=None, root=None):
    """每只票当日最新价。**优先用快照库**（每分钟每只都有），bar 库兜底。

    ★ 为什么优先快照：它是 1 次请求覆盖全部持仓，所以最全最新；
      bar 库是轮转抓的，某只可能还没轮到。实时盈亏要的就是"最新价"。
    ★ 返回里带 `src`（`snap` / `bar`）与 `at` —— 页面必须显示价来自哪、
      是几点的。不显示的话人分不清"实时"和"昨收"，而那是两个数量级的误解。
    ★ 不做"取不到就退回昨天"（同 `live.day_price` 的理由）：没有就是没有。
    """
    from assay import stock as st
    d = day or datetime.date.today()
    want = None
    if codes:
        want = [c for c in (st.norm_code(x) for x in codes) if c]
        if not want:
            return {}
    out = {}
    con = duckdb.connect(':memory:')

    def _where(col='code'):
        return ("WHERE %s IN ('%s')" % (col, "','".join(want))) if want else ''

    # ① 快照库
    sp = day_file(d, root, kind='snap_1m')
    if os.path.isfile(sp):
        for r in con.execute("""
            SELECT code, minute, price, preclose, open, high, low, change_pct,
                   turnover_pct, volume, amount
            FROM (SELECT *, row_number() OVER
                    (PARTITION BY code ORDER BY minute DESC) rn
                  FROM read_parquet('%s') %s) WHERE rn = 1"""
                % (sp, _where())).fetchall():
            out[r[0]] = {'code': r[0], 'at': r[1], 'price': r[2],
                         'preclose': r[3], 'open': r[4], 'high': r[5],
                         'low': r[6], 'change_pct': r[7],
                         'turnover_pct': r[8], 'volume': r[9], 'amount': r[10],
                         'src': 'snap'}
    # ② bar 库补快照没有的
    bp = day_file(d, root, kind='minute_1m')
    if os.path.isfile(bp):
        for r in con.execute("""
            SELECT code, datetime, close, high, low, volume, amount, avg
            FROM (SELECT *, row_number() OVER
                    (PARTITION BY code ORDER BY datetime DESC) rn
                  FROM read_parquet('%s') %s) WHERE rn = 1"""
                % (bp, _where())).fetchall():
            if r[0] in out:
                out[r[0]]['avg'] = r[7]        # 均价只有 bar 库有
                continue
            out[r[0]] = {'code': r[0], 'at': r[1], 'price': r[2],
                         'high': r[3], 'low': r[4], 'volume': r[5],
                         'amount': r[6], 'avg': r[7], 'src': 'bar'}
    return out


def bars(code, day=None, root=None):
    """一只票当日全部 1 分钟线（画分时图用）。"""
    from assay import stock as st
    jc = st.norm_code(code)
    if not jc:
        raise RTError('认不出代码：%r' % code)
    p = day_file(day or datetime.date.today(), root, kind='minute_1m')
    if not os.path.isfile(p):
        return []
    con = duckdb.connect(':memory:')
    rows = con.execute(
        "SELECT datetime, open, close, high, low, volume, amount, avg "
        "FROM read_parquet('%s') WHERE code = ? ORDER BY datetime" % p,
        [jc]).fetchall()
    keys = ['datetime', 'open', 'close', 'high', 'low', 'volume', 'amount', 'avg']
    return [dict(zip(keys, r)) for r in rows]


def status(day=None, root=None):
    """当日两个库里各有什么。页面显示"数据到几点"用。

    ★ `last` 取【两个库里更新的那个】—— 判断"要不要立刻补"要看最新价，
      而最新价通常来自快照库。只看 bar 库会以为一直落后。
    """
    d = day or datetime.date.today()
    out = {'day': str(d), 'in_session': in_session(),
           'trading_day': is_trading_day(d, root), 'last': None,
           'bar': {'codes': 0, 'rows': 0, 'last': None},
           'snap': {'codes': 0, 'rows': 0, 'last': None}}
    con = duckdb.connect(':memory:')
    for kind, col in (('minute_1m', 'datetime'), ('snap_1m', 'minute')):
        p = day_file(d, root, kind=kind)
        key = 'bar' if kind == 'minute_1m' else 'snap'
        out[key]['file'] = p
        out[key]['exists'] = os.path.isfile(p)
        if not out[key]['exists']:
            continue
        r = con.execute("SELECT count(DISTINCT code), count(*), max(%s) "
                        "FROM read_parquet('%s')" % (col, p)).fetchone()
        out[key].update(codes=r[0], rows=r[1], last=r[2])
        if r[2] and (out['last'] is None or r[2] > out['last']):
            out['last'] = r[2]
    # 兼容旧调用
    out['codes'] = out['bar']['codes']
    out['bars'] = out['bar']['rows']
    out['exists'] = out['bar']['exists'] or out['snap']['exists']
    return out


def stale_minutes(day=None, root=None, now=None):
    """最新一根距现在几分钟。用来判断"要不要立刻补一次"。

    ★ 非交易时段返回 None —— 收盘后当然"落后"，那不是缺数据。
    """
    now = now or datetime.datetime.now()
    if not in_session(now):
        return None
    st = status(day, root)
    if not st['last']:
        return 9999                     # 一根都没有 = 肯定要抓
    try:
        last = datetime.datetime.strptime(st['last'], '%Y-%m-%d %H:%M')
    except Exception:                                       # noqa: BLE001
        return 9999
    return max(0, int((now - last).total_seconds() // 60))


# ============================ 轮询（只在 server 开着时跑）============================
# ★ 不用 launchd：这件事**漏了不要紧** —— trends2 每次给全天，
#   下次开着的时候一抓就补齐；而实时盈亏本身只在看页面时才有意义。
#   （对比 daily_snapshot.py 那种"漏一天永久丢失"的，才必须挂 launchd。）
# ★ bar 抓取【降频】：trends2 每次给全天，所以不需要每分钟都抓。
#   25 只 × 每 5 轮抓 3 只 -> 约 0.6 请求/分钟、42 分钟轮一遍，
#   而任何一轮抓到谁谁就补齐到此刻 —— 幂等性让降频没有代价。
BAR_PER_ROUND = 3
BAR_EVERY = 5              # 每几轮抓一次 bar
_backoff = {'until': None, 'fails': 0}
_rr = {'i': 0}             # 轮转游标
_last = {'snap': None, 'bar': None, 'err': None, 'rounds': 0}


def poll_once(codes, root=None, force_all=False):
    """一轮：批量快照（1 请求）+ 轮转补几只的 bar。

    `force_all=True` 时对全部持仓抓 bar —— 收盘那一轮用，保证当天完整。
    """
    codes = list(codes or [])
    out = {'at': datetime.datetime.now().replace(microsecond=0).isoformat(),
           'n_codes': len(codes), 'snap': 0, 'bars': 0, 'bar_codes': [],
           'fail': []}
    if not codes:
        return out
    try:
        snap = snapshot(codes, root)
        out['snap'] = len(snap)
        if snap:
            # ★ 快照也落盘：这样【每分钟每只都有价】，哪怕 trends2 全被限流；
            #   而且实时盈亏用了哪个价有留痕，事后能复盘。
            out['snap_file'] = save_snap(list(snap.values()), root)
        _last['snap'] = out['at']
    except Exception as e:                                  # noqa: BLE001
        out['fail'].append({'stage': 'snapshot', 'error': str(e)[:120]})
    # ---- bar：降频 + 轮转 + 失败退避 ----
    now = datetime.datetime.now()
    if _backoff['until'] and now < _backoff['until']:
        out['bar_skipped'] = '退避中，到 %s' % _backoff['until'].strftime('%H:%M:%S')
    elif not force_all and _last['rounds'] % BAR_EVERY:
        out['bar_skipped'] = '本轮不抓 bar（每 %d 轮一次）' % BAR_EVERY
    else:
        if force_all:
            pick = codes
        else:
            i = _rr['i'] % max(1, len(codes))
            pick = [codes[(i + k) % len(codes)]
                    for k in range(min(BAR_PER_ROUND, len(codes)))]
            _rr['i'] = (i + len(pick)) % max(1, len(codes))
        r = fetch(pick, root)
        out['bars'] = r['bars']
        out['bar_codes'] = pick
        out['fail'].extend(r['fail'])
        if r['bars']:
            _last['bar'] = out['at']
        # ★ 全军覆没才退避（部分失败是常态：轮到被限流的主机就会失败，
        #   而 trends2 幂等，下轮补上）。退避 5→10→20 分钟，上限 30。
        if pick and not r['bars']:
            _backoff['fails'] += 1
            mins = min(30, 5 * (2 ** (_backoff['fails'] - 1)))
            _backoff['until'] = now + datetime.timedelta(minutes=mins)
            out['backoff'] = '%d 分钟（连续第 %d 次全失败）' % (mins, _backoff['fails'])
        elif r['bars']:
            _backoff['fails'] = 0
            _backoff['until'] = None
    _last['rounds'] += 1
    _last['err'] = out['fail'][:3] or None
    return out


def last_poll():
    return dict(_last)


def ensure_fresh(codes, root=None, max_stale=2):
    """页面来问的时候：如果最新 bar 落后超过 max_stale 分钟就【立刻补一次】。

    ★ 这条是用户明确要的"发现缺失最新的数据则立即调用一次"。
      只在交易时段生效 —— 收盘后当然"落后"，那不是缺数据。
    """
    st = stale_minutes(root=root)
    if st is None or st <= max_stale:
        return {'triggered': False, 'stale': st}
    r = poll_once(codes, root)
    return {'triggered': True, 'stale': st, 'result': r}
