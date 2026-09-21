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
import uuid
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

_lock = threading.Lock()          # 只保护 host 轮询下标（下面那 3 行）
_host_i = 0

# 🔴 **落盘要串行**。写当日文件的有【三个来源】，其中两个在 HTTP 请求线程上：
#      ① 轮询线程 `_rt_loop` -> poll_once
#      ② 请求线程 `_rt_ensure`   -> ensure_codes   （加自选 / 打开自选页 / 实盘接口）
#      ③ 请求线程 `_rt_catch_up` -> ensure_fresh
#   `save()` 是"读当日文件 -> 合并 -> COPY 到 tmp -> rename"，没有锁的话：
#     · 两个线程 COPY 到**同一个 tmp 路径**，先 rename 的那个拿到对方写了一半的
#       文件 -> 落地一个**头尾都是 PAR1、中间元数据是垃圾**的 parquet。
#       实测 2026-09-07 09:43 就这么坏了一次（2078 字节，duckdb 报
#       TProtocolException），此后 bar 腿每轮失败、连续 3.5 小时，
#       **而轮询线程活着、只是 rounds 不再增长** —— 页面上就是"实时不刷新"。
#     · 即使 tmp 名唯一，两个线程各自读到旧文件、各写自己那份合并结果，
#       后 rename 的覆盖前一个 -> 前者抓到的行**凭空消失且不报错**。
#   ★ 两条的分工是**变异测试量出来的**，别记反：
#     · **写锁是根本修复。** 去掉它跑 200 线程并发写：最终只剩 **1 行**
#       （应有 200），且日志里刷几十次"隔离重建" —— 精确复现生产事故。
#     · **唯一 tmp 名在有锁的前提下是冗余的**（改回固定名、200 行完好，
#       变异测试抓不到）。保留它防的是**跨进程**：锁只在进程内有效，
#       而重启时 `pkill` 没杀干净就会有两个 serve.py 同时跑
#       （CLAUDE.md 已记"改了抓取范围必须重启"这条纪律）。
#   锁只圈住落盘（毫秒级），网络请求在锁外，不会互相拖住。
_wlock = threading.Lock()


def _tmp_path(p):
    """唯一的临时文件名 —— 固定用 `p + '.tmp'` 会被并发写者互相踩。"""
    return '%s.%d.%s.tmp' % (p, os.getpid(), uuid.uuid4().hex[:8])


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


def snapshot_syms(syms):
    """腾讯批量快照，**按 tdx symbol 取**（`sh000001` / `sz399006` / `bj899050`）。

    🔴 **指数不能走 `snapshot()`** —— 它用 `stock.norm_code` 归一化，
      而那是按**股票**的前缀规则写的（60/68/90 沪、00/30/20 深），
      上证指数恰好是 `sh000001`、会被直接拒（同「指数按 symbol 认，
      不去动 normalize_code」那条）。所以这一层按 symbol 收发，
      `snapshot()` 改成在它外面做代码映射 —— **解析只有一份**。

    ★ 返回 `{symbol: {...}}`；取不到的**不在字典里**（不要拿旧价顶上）。
    """
    syms = [x for x in (syms or []) if x]
    out = {}
    for i in range(0, len(syms), TX_BATCH):
        chunk = syms[i:i + TX_BATCH]
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
            if len(f) <= TX_F['change_pct']:
                continue
            tag = line.split('v_')[1].split('=')[0] if 'v_' in line else ''
            if tag not in chunk:
                continue

            def _n(k, d=None, _f=f):
                try:
                    return float(_f[TX_F[k]])
                except Exception:                           # noqa: BLE001
                    return d
            px = _n('price')
            if not px:
                continue                # 停牌/无成交 -> 不给，不要塞 0
            out[tag] = {'symbol': tag, 'name': f[TX_F['name']], 'price': px,
                        'preclose': _n('preclose'), 'open': _n('open'),
                        'high': _n('high'), 'low': _n('low'),
                        'change_pct': _n('change_pct'),
                        'turnover_pct': _n('turnover_pct'),
                        'volume': _n('volume'),
                        'amount': (_n('amount_wan') or 0) * 1e4,
                        'ts': f[TX_F['ts']]}
    return out


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
    raw = snapshot_syms(list(m))
    out = {}
    for tag, d in raw.items():
        jc = m.get(tag)
        if jc:
            out[jc] = dict(d, code=jc)
            out[jc].pop('symbol', None)
    return out


# ---------------------------------------------------------------- 主要指数
# 🔴 **清单一处定义**（同「可选清单由服务端给」那条）：前端硬编码的话，
#   加一个指数页面上不会出现、删一个则会显示一条取不到的空行。
# ★ 每个都**实测取得到**（2026-09-19 逐个打过腾讯批量接口）。
# 🔴 **中证2000（sh932000）与万得微盘股（8841431.WI）腾讯都【没有】** ——
#   后者是万得专有，tdx 与腾讯都不提供（CLAUDE.md 记过）。
#   用户说的"微盘股"这里给的是**国证2000**：同为"小市值 2000 只"口径，
#   但它**比微盘股大一档** —— 所以名字里**不写"微盘"**，只写它本来的名字
#   （同「不拿 ETF 当指数用」那条：近似物要叫自己的名字）。
# ★ **顺序按"这个人盯什么"排**，不按市值从大到小：
#   先三个看大势的（上证/深成/创业板），紧接着是**小市值那一档**
#   —— 本机跑的是小市值与红利策略，微盘/国证2000/中证1000 才是天天要看的；
#   沪深300、科创50、北证50 放后面。
#   🔴 排在后面的在窄屏上会被挤到横滚区（带子自己滚，不撑 body）——
#     所以**最该看的必须在前面**，否则默认就看不见（实测 10 格 1609px，
#     1500px 视口下最后一格要滚出来才见得到）。
INDICES = [
    ('sh000001', '上证'), ('sz399001', '深成'), ('sz399006', '创业板'),
    ('sz399303', '国证2000'), ('sh000852', '中证1000'),
    ('sh000905', '中证500'), ('sh000300', '沪深300'),
    ('sh000688', '科创50'), ('bj899050', '北证50'),
]
_IDX_TTL = 20          # 秒。多个页面同时开着时共享这一份，别各打各的
_IDX_CACHE = {'at': 0.0, 'rows': [], 'asof': None}

# ---- 自建「微盘400」：全市场总市值最小 400 只、等权 ----
# 🔴 **万得微盘股（8841431.WI）拿不到**，而三个公开小盘指数都**代表不了它**：
#   2026-09-19 量过（自建月度调仓代理 2016~2026 当基准）——
#     国证2000 相关性 0.864 / beta 0.97 / 跟踪误差 **13.7%/年**（三项里最好）
#     但 2024-01 微盘踩踏那段它 -20.1% 而代理 -39.1%（**少跌 19pp**）
#   更要命的是**日频方向**：最小400等权 vs 国证2000，2024-01 起 658 个交易日
#     日差 sd 1.39pp、|差|>1pp 占 38.6%、**方向相反占 19.6%**
#   —— 五天里有一天国证2000 会把方向说反。所以这一格自己算。
#
# ★ 名字叫**「微盘400」**不叫"微盘股"：它是我们自建的近似物，
#   名字里就说清"400 只"，不冒充万得那个（同「不拿 ETF 当指数用」）。
# ★ 只给**涨跌幅、不给点位**：等权组合没有"点位"这回事，
#   编一个基点出来就是在造一个看着像指数的数（同「不猜一个数填上去」）。
# 🔴 成分**按日缓存**：面板每天才变一次，日内重算是白费；
#   而且它与 9 个指数**拼在同一个请求里**发出去（上限 900，实测 409 只
#   0.22 秒）—— 分两次就是把请求数翻倍，而限流是这条链上唯一的风险
#   （同「抓取范围合并去重」那条）。
MICRO_N = 400
MICRO_LABEL = '微盘400'
_MICRO = {'day': None, 'syms': []}


def micro_members(root=None):
    """最近一个面板日、总市值最小 `MICRO_N` 只的 tdx symbol。按日缓存。

    ★ 用**总市值**（万得微盘股的口径），不是流通市值。
    ★ 与历史代理（月度调仓）的区别：这里是**日更**——盘中要回答的是
      "今天这批最小的票怎么样"，而不是复现一条可回测的指数。
      两者的口径差要在文档里说清，别混用。
    """
    import datetime as _dt
    today = _dt.date.today().isoformat()
    if _MICRO['day'] == today and _MICRO['syms']:
        return _MICRO['syms']
    try:
        import duckdb
        lake = _lake(root)
        P = "read_parquet('%s/mart/panel_daily/panel_*.parquet')" % lake
        rows = duckdb.connect().execute(
            "SELECT jq_code FROM %s WHERE date = (SELECT max(date) FROM %s) "
            "AND totalmv > 0 AND public_status IN ('正常上市','ST','*ST') "
            "ORDER BY totalmv LIMIT %d" % (P, P, MICRO_N)).fetchall()
    except Exception:                                       # noqa: BLE001
        return _MICRO['syms']            # 取不到就沿用上一次，别让这一格消失
    syms = [('sh' if r[0].endswith('XSHG') else 'sz') + r[0][:6] for r in rows]
    if syms:
        _MICRO.update(day=today, syms=syms)
    return _MICRO['syms']


def indices(force=False):
    """主要指数的实时快照。**所有页面底部那条常驻带子**读它。

    ★ **缓存 %d 秒**：这条带子挂在 `common.js` 上、每个打开的页面都会轮询，
      不缓存的话 N 个标签页就是 N 倍请求 —— 而限流是这条链上唯一的风险
      （同 `_rt_catch_up` 的 20 秒节流）。
    ★ **收盘后不刷新也没关系**：返回里带 `session`，页面据此停掉轮询
      （同「收盘后不轮」那条）；值仍然给最后一次的，所以带子不会空掉。
    """ % _IDX_TTL
    now = time.time()
    if not force and _IDX_CACHE['rows'] and now - _IDX_CACHE['at'] < _IDX_TTL:
        return {'items': _IDX_CACHE['rows'], 'asof': _IDX_CACHE['asof'],
                'session': in_session(), 'cached': True}
    mem = micro_members()
    # ★ 指数与微盘成分**拼在同一个请求里**（409 个，上限 900）
    got = snapshot_syms([c for c, _ in INDICES] + mem)
    rows = []
    for sym, short in INDICES:
        d = got.get(sym)
        if not d:
            continue            # 取不到就不显示这一格，**不要塞 0 或旧值**
        rows.append({'symbol': sym, 'short': short, 'name': d['name'],
                     'price': d['price'], 'change_pct': d['change_pct'],
                     'ts': d['ts']})
    # ---- 微盘400：等权平均当日涨跌 ----
    # 🔴 **停牌/取不到的整只剔除，不算成 0%** —— 算成 0 会把一批没交易的
    #   票当成"今天平盘"，等权平均被系统性拉向 0（同「空结果一律当失败」）。
    mr = [got[x]['change_pct'] for x in mem
          if x in got and got[x].get('change_pct') is not None]
    if mem and len(mr) >= len(mem) * 0.6:
        # ★ 插在「国证2000」**前面** —— 它是这一档里最贴切的那个，
        #   而国证2000 每 5 天就有 1 天把方向说反（见上面那段实测）。
        _at = next((i for i, r in enumerate(rows)
                    if r['symbol'] == 'sz399303'), len(rows))
        rows.insert(_at, {'symbol': 'micro%d' % MICRO_N, 'short': MICRO_LABEL,
                     'name': '自建：全市场总市值最小 %d 只等权（无点位，'
                             '只给当日涨跌）' % MICRO_N,
                     'price': None, 'change_pct': round(sum(mr) / len(mr), 2),
                     'n': len(mr),
                     'ts': max((got[x]['ts'] for x in mem if x in got),
                               default='')})
    if rows:                    # 一条都没取到时保留上一份，别让带子闪成空
        _IDX_CACHE.update(at=now, rows=rows,
                          asof=max((r['ts'] or '') for r in rows))
    return {'items': _IDX_CACHE['rows'], 'asof': _IDX_CACHE['asof'],
            'session': in_session(), 'cached': False}


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


def quarantine(p):
    """把读不动的文件挪进 `_corrupt/`，返回新路径（挪不动就返回 None）。

    ★ **不删** —— 坏文件是证据（这次就是靠那 2078 字节确认了"头尾都是 PAR1、
      中间是垃圾"，从而定位到并发写同一个 tmp）。删了就只剩"读不出来"这一个
      症状，分不清是撕裂写还是磁盘坏。
    """
    try:
        d = os.path.join(os.path.dirname(p), '_corrupt')
        os.makedirs(d, exist_ok=True)
        q = os.path.join(d, '%s.%s.bad' % (os.path.basename(p),
                                           time.strftime('%H%M%S')))
        os.replace(p, q)
        return q
    except Exception:                                       # noqa: BLE001
        return None


def _merge_existing(con, p):
    """把已有的当日文件读进 `all_`。返回 True = 读到了，False = 从零开始。

    🔴 **读不动就隔离掉、当作没有**，不要把异常抛出去。理由是这条链的
      失败模式：一个坏文件会让**此后每一轮**都在同一处抛异常，而
      `_rt_loop` catch 住之后 `rounds` / `last` 不再更新 —— 线程活着、
      每 60 秒失败一次，页面上只表现为"实时不刷新"。实测这样连续失败了
      3.5 小时（2026-09-07 09:47 -> 13:20）。
    ★ 自愈是安全的：trends2 **每次返回当天全量**，快照也是按分钟去重覆盖写，
      所以重建一遍就补回来了（这也是 bar 腿"漏了不要紧"的同一个理由）。
      唯一代价是丢掉隔离前那一段还没被这一轮覆盖到的行 —— 对 bar 是 0
      （全量重取），对快照是当天已过去的分钟。
    """
    if not os.path.isfile(p):
        return False
    try:
        con.execute("CREATE TABLE all_ AS SELECT * FROM read_parquet('%s')" % p)
        return True
    except Exception as e:                                  # noqa: BLE001
        q = quarantine(p)
        print('[rt] 当日文件读不动，已隔离并重建：%s -> %s（%s: %s）'
              % (p, q, type(e).__name__, str(e)[:80]), flush=True)
        try:                       # 隔离后 all_ 可能已被半建出来
            con.execute('DROP TABLE IF EXISTS all_')
        except Exception:                                   # noqa: BLE001
            pass
        return False


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
    with _wlock:                       # 见 _wlock 的注释：三个线程会同时写这个文件
        con = duckdb.connect(':memory:')
        con.register('new', _to_arrow(rows))
        if _merge_existing(con, p):
            con.execute('INSERT INTO all_ SELECT * FROM new')
        else:
            con.execute('CREATE TABLE all_ AS SELECT * FROM new')
        tmp = _tmp_path(p)
        con.execute("""
            COPY (SELECT code, datetime, open, close, high, low, volume, amount, avg
                  FROM (SELECT *, row_number() OVER
                          (PARTITION BY code, datetime ORDER BY volume DESC) rn
                        FROM all_) WHERE rn = 1
                  ORDER BY code, datetime)
            TO '%s' (FORMAT PARQUET)""" % tmp)
        os.replace(tmp, p)      # 先写 tmp 再 rename，同 _save_marks
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
    with _wlock:                       # 同 save()：请求线程与轮询线程会撞
        con = duckdb.connect(':memory:')
        con.register('new', pa.table({k: [r[k] for r in out] for k in SNAP_COLS}))
        if _merge_existing(con, p):
            con.execute('INSERT INTO all_ SELECT * FROM new')
        else:
            con.execute('CREATE TABLE all_ AS SELECT * FROM new')
        tmp = _tmp_path(p)
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
        # 🔴 **一条腿坏了不能把整个接口打挂。** 实测 2026-09-07：bar 库当日
        #   文件被并发写坏，这个查询直接抛 TProtocolException，`/api/rt/status`
        #   整体返回 error —— 于是页面连**健康的快照腿**都看不到，
        #   表现成"实时数据全没了"，而真实情况是只有 bar 腿坏了。
        # ★ 所以逐腿 try，坏的那条标 `err` 让页面**说出来**，
        #   而不是让两条腿一起消失。
        try:
            r = con.execute("SELECT count(DISTINCT code), count(*), max(%s) "
                            "FROM read_parquet('%s')" % (col, p)).fetchone()
        except Exception as e:                              # noqa: BLE001
            out[key]['err'] = '%s: %s' % (type(e).__name__, str(e)[:120])
            continue
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


def missing(codes, root=None, day=None):
    """这些代码里，**今天还一条实时数据都没有**的是哪些。"""
    from assay import stock as st
    want = [c for c in (st.norm_code(x) for x in (codes or [])) if c]
    if not want:
        return []
    have = latest(want, day=day, root=root)
    return [c for c in want if not (have.get(c) or {}).get('price')]


def ensure_codes(codes, root=None, max_bars=3, day=None):
    """**刚加进来的票立刻抓一次** —— 它不在上一轮的抓取范围里。

    为什么需要它：轮询是每 60s 重算一次范围（持仓 ∪ 自选），所以新加的票
    最快也要等下一轮；bar 是轮转抓的（~42 分钟一圈），更久。而人加完自选
    是**马上**要看的 —— 那一刻页面上一片"收盘价"，看着像功能没生效。

    ★ 只抓【真的没有数据】的那几只（`missing`），所以天然收敛：抓到了就
      不再是 missing，下次页面刷新不会重复打请求。这也是"不要重复调用"。
    ★ 不在交易时段/非交易日就什么都不做并说明原因 —— 收盘后本来就没有
      盘中数据可抓，硬抓一次只会拿到空结果（**空结果一律当失败**）。
    ★ bar 每次最多 `max_bars` 只：加自选是一只一只加的，这里不该变成
      一个能被点出几十个请求的入口（限流是这条链上唯一的风险）。
    """
    out = {'missing': [], 'snap': 0, 'bars': 0, 'skipped': None, 'fail': []}
    miss = missing(codes, root=root, day=day)
    out['missing'] = miss
    if not miss:
        return out
    now = datetime.datetime.now()
    if is_trading_day() is False:
        out['skipped'] = '非交易日'
        return out
    if not in_session(now):
        out['skipped'] = '不在交易时段'
        return out
    try:
        snap = snapshot(miss, root)
        out['snap'] = len(snap)
        if snap:
            save_snap(list(snap.values()), root)
    except Exception as e:                                  # noqa: BLE001
        out['fail'].append({'stage': 'snapshot', 'error': str(e)[:120]})
    # bar 只在没退避时抓（退避中说明 trends2 正在限流，硬抓只会加深）
    if _backoff['until'] and now < _backoff['until']:
        out['bar_skipped'] = '退避中，到 %s' % _backoff['until'].strftime('%H:%M:%S')
    else:
        r = fetch(miss[:max_bars], root)
        out['bars'] = r['bars']
        out['fail'].extend(r['fail'])
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
