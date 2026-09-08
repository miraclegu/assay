#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""lv/explain.py —— 「为什么选这只票」：**捕获**策略自己跑出来的过程，不重算。

## 🔴 判据：一行规则都不许在这里重写

实盘模块的核心原则是「不重写任何交易规则」（见 `assay/live.py` 头）。
选股理由最容易破这条：想说清"它为什么入选"，最省事的写法就是照策略再写一遍
筛选条件 —— 然后两份逻辑开始分叉，**而分叉的那份看着完全正常**，
只是某天开始解释错了。本项目在别处栽过同一个坑（f133 那个股息率、
前端硬编码"权不权威"的判据）。

所以这里的做法与 `RecordingBroker` 同构：**把数据入口换成会记账的代理**，
策略跑自己的代码路径，我们只在旁边记下

    ① 它查了什么（SQL 的入参 = 策略当时用的阈值）
    ② 查回来什么（候选池 + 指标列，带原始顺序 = 排名）
    ③ 每一步过滤前后的集合差（谁被筛掉了）
    ④ 过滤的理由**问规则本身要**（`broker.can_trade` 返回的 why），不自己判

于是"理由"永远等于策略当时真实发生的事。策略改了规则，解释自动跟着变；
解释不到的地方**明说不知道**（`未识别`），不猜一个看着合理的出来。

## 能解释到什么程度，取决于策略把什么暴露出来

| 层次 | 来源 | 需要改策略吗 |
|---|---|---|
| 候选池、排名、每一步筛掉了谁 | 捕获 `data.query` / `tradable` / `had_limit_up` | **不用** |
| 用了哪些阈值 | 捕获到的 SQL 入参（`listed=250` `pbcut=floor(0.5*n)` …） | **不用** |
| 策略声明的参数与取值 | `Engine._declared` + `g` | **不用** |
| 策略状态里的代码集合（目标池/备选/缓冲/黑名单） | 扫 `g` 里"看着像代码集合"的属性 | **不用** |
| **每只票的指标**（PB / 流通市值 / ROE 加速度 / 股息率…） | 策略 SQL 的返回列 | 🔴 **要**：末层多 `SELECT` 几列 |

最后一行是唯一需要动策略的地方，而它是**投影**改动：`WHERE` / `ORDER BY` /
`LIMIT` 一个字不动，返回的行与行序完全相同，`df['jq_code'].tolist()` 拿到的
还是同一个列表。没改的策略照样能解释，只是没有指标列（会在 `notes` 里说明）。

★ 不用「另写一条取指标的 SQL」把指标补上 —— 那就是第二份口径。它算出来的
  PB 与策略当时用的 PB **看着一样**，直到某天 as-of 规则差一天（停牌股结转、
  报告期切换）才分叉，而那时没人会怀疑一个"显示用"的数字。

## 只在实盘预览里挂，回测一行不受影响

`attach()` 由 `lv/sig.py` 在 `build_signal` 里调用，包的是那一次预览用的
`Engine`。回测（`run.py`）根本不经过这里。
"""
import re

CODE_RE = re.compile(r'^\d{6}\.[A-Z]{4}$')

# 候选池**落盘**的行数上限（每组各自算）。信号是每天一个文件，红利 A 池 332 行，
# 全存只会让 JSON 变大而没人看；但存少了那几行就**永久没了**，所以留点余量。
# 页面默认只显示前 20 名（`views/live-why.js` 的 LVWHYN），要看更多再展开 ——
# 存得多一点、显示得少一点，两边各自最合适。
MAX_POOL = 60

# 这些名字在 `g` 里装的是代码集合（目标池/备选/缓冲/涨停豁免/止损冷静期…）。
# 不写死名单：凡是「list/set/dict 且元素看着像证券代码」的属性都收 ——
# 写死的话新策略换个名字就静默丢失，而那看着像"这个策略没有目标池"。
_G_SKIP = ('hold_history',)          # 逐日累积的历史，量大且不是"当期"信息


# 指标列的中文标签与量纲。**放在服务端**：页面只负责画，不负责知道
# `roe_inc` 是什么意思 —— 前端硬编码业务判据这条本项目栽过
# （"权不权威"那次，每次打开实盘页都弹一条假告警）。
#
# ★ 对不上的列**不丢，按原名显示** —— 策略新加一列时最坏是标签丑，
#   而不是那一列凭空消失（消失了没人会发现）。
# 🔴 `dy` / `inc_*` 这些比率在 std 层是**小数**（0.05 = 5%），不是百分数。
#   标成 pct 的由页面 ×100 显示；标错的话页面会显示 "0.05%"，
#   而那看着像个正常的小数字。判据见 datalake/build/load_jq_indicator_q.py。
METRICS = {
    'floatmv':    ('流通市值', 'money'),
    'totalmv':    ('总市值', 'money'),
    'pb':         ('市净率 PB', 'num'),
    'eps':        ('每股收益', 'num'),
    'roe_inc':    ('ROE 加速度', 'pct'),      # 4×最近一期 − 前四期之和
    'sw_l1_name': ('申万一级', 'text'),
    'dy':         ('股息率', 'pct'),
    'beta':       ('beta', 'num'),
    'inc_return': ('净资产收益率', 'pct'),
    'inc_rev':    ('营收同比', 'pct'),
    'inc_np':     ('净利同比', 'pct'),
}

# (名次列, 总数列, 这个名次是按什么排的)。合成一格显示「第 X / Y」——
# 分成两列的话表会宽一倍，而「1305」和「3270」分开看没有意义。
# ★ 一个名次列可以有**多个**总数候选：红利两条腿的总数列一个叫 `bn`、
#   一个叫 `n`，只配一个的话另一个会变成没人认领的孤儿列（页面上就是一列
#   叫 "bn" 的裸数字）。所以第二项是候选**列表**，取那一行里存在的那个。
RANK_PAIRS = (
    ('pb_rn', ('pb_n',), 'PB 名次（升序，取前半区）'),
    ('roe_rn', ('roe_n',), 'ROE 加速度名次（降序，取前 10%）'),
    ('rk', ('bn', 'n'), '名次'),
)


# `g` 里那些代码集合的中文名。★ 与 METRICS 同一条纪律：**放服务端**，
# 页面只管画。对不上的按原名显示（策略新起一个名字最坏是标签丑，不是消失）。
SET_LABEL = {
    'target_list': '目标池', 'backup_list': '备选', 'buf_list': '缓冲区',
    'pending': '待买', 'high_limit': '昨日涨停豁免', 'stop_banned': '止损冷静期',
    'pos_state': '持仓状态', 'blacklist': '黑名单',
}


def set_label(name):
    return SET_LABEL.get(name, name)


# SQL 入参的中文名。★ 与 METRICS 同一条纪律：放服务端、对不上按原名显示。
#   它只是让那排 chip 好读；**真正的条件以 SQL 为准**（页面上可展开）。
ARG_LABEL = {
    'sd': '决策日', 't1': '决策日', 'listed': '上市满(天)', 'cand': '取候选数',
    'pin': '停牌股进池', 'kcb': '科创板排除', 'pert': '扰动', 'salt': '扰动盐',
    'skip': '跳过前几名', 'pbcut': 'PB 截断', 'roecut': 'ROE 加速度截断',
    'excl': '排除行业', 'dmin': '股息率下限', 'dtop': '股息率取前',
    'pe_lo': 'PE 下限', 'pe_hi': 'PE 上限', 'roe_lo': 'ROE 下限',
    'roe_hi': 'ROE 上限', 'rev_lo': '营收增速下限', 'rev_hi': '营收增速上限',
    'np_lo': '净利增速下限', 'np_hi': '净利增速上限', 'bmode': 'B袖阈值模式',
    'broe': 'B袖ROE分位', 'brev': 'B袖营收分位', 'bnp': 'B袖净利分位',
    'bw': 'beta 窗口', 'bpct': 'beta 取前', 'uni': '宇宙条件',
}


def arg_label(k):
    return ARG_LABEL.get(k, k)


def metric_meta(pool):
    """池子里出现过的指标列 -> [{key, label, kind, rank_of}]，保持出现顺序。

    页面照这个渲染表头与格式，**自己不认识任何一个列名**。
    """
    keys = []
    for e in pool:
        for k in e.get('metrics') or {}:
            if k not in keys:
                keys.append(k)
    pairs = {}
    for a, bs, lab in RANK_PAIRS:
        got = [b for b in bs if b in keys]
        if a in keys and got:
            pairs[a] = (got, lab)
    skip = {b for _a, (bs, _lab) in pairs.items() for b in bs}
    out = []
    for k in keys:
        if k in skip:
            continue
        if k in pairs:
            out.append({'key': k, 'of': pairs[k][0], 'kind': 'rank',
                        'label': pairs[k][1]})
            continue
        lab, kind = METRICS.get(k, (k, 'num'))
        out.append({'key': k, 'label': lab, 'kind': kind})
    return out


def _py(v):
    """numpy / pandas 标量 -> 原生类型。**不 round** —— 展示层再决定精度。

    🔴 不转的话 `json.dumps` 直接抛 `Object of type int64 is not JSON
    serializable`，而那发生在信号落盘的最后一步，前面几十秒的取数全白跑。
    """
    if v is None:
        return None
    if isinstance(v, (bool, int, float, str)):
        return v
    for attr in ('item',):                       # numpy 标量
        if hasattr(v, attr):
            try:
                return getattr(v, attr)()
            except Exception:                    # noqa: BLE001
                pass
    try:
        import math
        f = float(v)
        return None if math.isnan(f) else f
    except Exception:                            # noqa: BLE001
        return str(v)


def _is_codes(v):
    """看着像不像一堆证券代码。"""
    if isinstance(v, dict):
        v = list(v)
    if not isinstance(v, (list, tuple, set, frozenset)):
        return False
    xs = [x for x in v if isinstance(x, str)]
    return bool(xs) and len(xs) == len(v) and all(CODE_RE.match(x) for x in xs)


class Recorder(object):
    """记账本身。`arm(phase)` 之后才记 —— warmup 要重放 30 天，
    每天都记的话步骤表里会有几百条无关的调用，而真正要看的是最后那次调仓。"""

    def __init__(self):
        self.steps = []
        self.phase = None
        self._on = False

    def arm(self, phase):
        self.phase, self._on = phase, True

    def disarm(self):
        self._on = False

    def add(self, **kw):
        if not self._on:
            return
        kw['i'] = len(self.steps) + 1
        kw['phase'] = self.phase
        self.steps.append(kw)

    def of_phase(self, phase):
        return [s for s in self.steps if s.get('phase') == phase]


class RecordingFeed(object):
    """`context.data` 的记账代理。**透传一切**，只在 query / had_limit_up /
    codes_at 上多记一笔。

    ★ 包的是 `Engine.guard`（`GuardedFeed`），不是原始 feed —— 策略拿到的就是
      它，而 PIT 防火墙必须仍在链路上。包错层会把防火墙绕过去，
      **而那不报错**，只是实盘预览允许了回测里禁止的未来函数。
    """

    def __init__(self, inner, rec):
        # 用 object.__setattr__ 免得触发自己的 __getattr__ 递归
        object.__setattr__(self, '_inner', inner)
        object.__setattr__(self, '_rec', rec)

    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, '_inner'), name)

    def __setattr__(self, name, value):
        setattr(object.__getattribute__(self, '_inner'), name, value)

    def query(self, sql, **kw):
        df = self._inner.query(sql, **kw)
        self._rec.add(kind='query', where=_caller(), rows=_rows(df),
                      args={k: _py(v) for k, v in kw.items()},
                      sql_head=_sql_head(sql), sql=_sql_display(sql, kw))
        return df

    def codes_at(self, date, where='1=1', order=None, limit=None):
        out = self._inner.codes_at(date, where, order, limit)
        self._rec.add(kind='codes_at', where=_caller(),
                      rows=[{'jq_code': c} for c in out],
                      args={'date': str(date), 'where': where,
                            'order': order, 'limit': limit})
        return out

    def had_limit_up(self, codes, start, end):
        out = self._inner.had_limit_up(codes, start, end)
        self._rec.add(kind='had_limit_up', where=_caller(),
                      args={'start': str(start), 'end': str(end)},
                      codes_in=list(codes), codes_out=sorted(out))
        return out


def _caller(depth=2):
    """哪一行调的（`rebalance:352`）。做步骤的名字用。

    ★ 用调用点而不是自己编号：策略里同一个 SQL 会被调两次
      （froec 的候选池 + 缓冲区那次 `wide`），只有行号能分开它们。
    """
    import sys
    try:
        f = sys._getframe(depth)
        return '%s:%d' % (f.f_code.co_name, f.f_lineno)
    except Exception:                            # noqa: BLE001
        return '?'


def _sql_head(sql):
    """这条查询的【名字】= SQL 的首行注释。没有注释就返回空。

    ★ 让策略在 SQL 里自己起名（`-- 袖A｜红利低波：…`）——
      注释改不了任何行为，却让页面能说清"这一组是哪一袖"。
    🔴 **没有注释时返回空串，不要退回"SQL 的前 60 个字符"** ——
      那会把 `WITH div AS ( WITH raw AS ( SELECT code, report_date,` 当成组名
      印在标题上，比没有名字更糟（看着像乱码，还占一行）。
    """
    for ln in (sql or '').strip().splitlines():
        t = ln.strip()
        if t.startswith('--'):
            return t.lstrip('- ').strip()[:80]
        if t:
            break
    return ''


def _sql_display(sql, kw):
    """把调用方传的参数代进 SQL 里，供页面展示。

    🔴 **这才是"策略的条件有哪些"的正本。** 只列几个参数 chip 是不够的：
      条件长在 SQL 的 WHERE / 排序 / 截断里（`dy > 0.03`、
      `rk <= floor(0.1 * n)`、`ORDER BY beta ASC`），而那些**没有别处可抄**
      —— 另写一份"条件说明"就是第二份口径，策略改了它不会跟着变。
    ★ 只替换调用方传的键；`{panel}` / `{t_*}` 这些留给 feed 层，
      原样留在文本里（它们是表名，不是条件）。
    """
    out = sql or ''
    for k, v in (kw or {}).items():
        out = out.replace('{%s}' % k, str(v))
    return out


def _rows(df):
    """DataFrame -> [{列: 值}]。列名原样保留（它们是策略自己起的名字）。"""
    try:
        cols = list(df.columns)
    except Exception:                            # noqa: BLE001
        return []
    out = []
    for t in df.itertuples(index=False, name=None):
        out.append({c: _py(v) for c, v in zip(cols, t)})
    return out


def attach(eng, broker, rec=None):
    """把记账代理挂到引擎上。返回 `Recorder`。

    两处替换，**都不改变任何判定**：
      1. `ctx.data` -> RecordingFeed（透传 GuardedFeed）
      2. `broker.filter_tradable` -> 记账包装（实例属性遮蔽类方法）

    🔴 第 2 条里要问「为什么不能交易」，用的是 `broker.can_trade` 本人 ——
      规则的唯一实现处（`broker.py` 里那句注释写明了：撮合走它、策略预筛也走它）。
      自己照着 bar 判一遍停牌/涨跌停就是第二份规则，迟早与撮合分叉。
      ★ 但 `can_trade` 有个副作用：涨跌停价不可靠时会 `n_limit_unreliable += 1`。
        多问一次就多记一次，所以这里**问完把计数恢复原值** —— 记账不许改状态，
        哪怕改的只是一个统计量。
    """
    rec = rec or Recorder()
    eng.ctx.data = RecordingFeed(eng.guard, rec)

    inner = broker.filter_tradable

    def filter_tradable(codes, side):
        codes = list(codes)
        out = inner(codes, side)
        keep = set(out)
        drop = [c for c in codes if c not in keep]
        why = {}
        if drop:
            n0 = getattr(broker, 'n_limit_unreliable', 0)
            for c in drop:
                try:
                    why[c] = broker.can_trade(c, side)[1] or '不可交易'
                except Exception as e:           # noqa: BLE001
                    why[c] = '判定失败：%s' % e
            broker.n_limit_unreliable = n0       # 记账不许改状态
        # depth=3：0=_caller 1=filter_tradable 2=Context.tradable 3=策略函数
        rec.add(kind='tradable', where=_caller(3), args={'side': side},
                codes_in=codes, codes_out=out, drop_why=why)
        return out

    broker.filter_tradable = filter_tradable
    return rec


def g_state(eng):
    """`g` 里那些「看着像代码集合」的属性 + 策略声明过的参数。

    ★ 参数取 `Engine._declared`（initialize 期间真的被写过的名字），
      不是整个 `g.__dict__` —— 后者混着 `pos_state` / `target_list` 这些
      **运行期状态**，把它们叫"参数"会让人以为可以调。
    """
    g = getattr(eng, 'g', None)
    if g is None:
        return {}, {}
    declared = getattr(eng, '_declared', None)
    d = dict(vars(g))
    params = {}
    for k in sorted(declared or ()):
        if k.startswith('_') or k not in d:
            continue
        v = d[k]
        if isinstance(v, (bool, int, float, str)) or v is None:
            params[k] = _py(v)
    sets = {}
    for k in sorted(d):
        if k.startswith('_') or k in _G_SKIP:
            continue
        v = d[k]
        if _is_codes(v):
            sets[k] = sorted(v)
    return params, sets


# ------------------------------------------------------------------ 组装
def _code_of(row):
    for k in ('jq_code', 'code', 'MARKET_CODE'):
        if k in row:
            return row[k]
    return None


def assemble(rec, phase, picked, held, sold, names=None,
             params=None, g_sets=None):
    """把记下来的步骤拼成「候选池 + 每只票的理由」。

    `picked` 是最终要买的，`held` 是本来就持有的，`sold` 是要卖的 ——
    **状态只从这三个事实推**，不从"排名够不够"之类的规则推。
    一只票通过了所有能观察到的过滤却没被买，只说「没轮到（第 k 名）」，
    不说"因为只取前 10 名" —— 那是策略的规则，这里不复述。
    """
    names = names or {}
    params, g_sets = params or {}, g_sets or {}
    steps = rec.of_phase(phase)
    picked, held, sold = set(picked or ()), set(held or ()), set(sold or ())

    # ---- 1) 候选池：**一条查询 = 一组**，不合并 ----
    #   🔴 原来把所有查询的结果并成一张表并按代码去重 —— 对红利那种
    #     「低波(A) + 价值(B) 两袖并集」的策略是错的，实测三条都坏了：
    #       · B 袖 6 只全被并进 A 袖 -> B 那一组在页面上 **0 行**
    #       · B 袖选中的票显示成 A 袖的名次（第 233/332 名），看着莫名其妙
    #       · 两袖的指标列不同（A 是 dy/beta，B 是 totalmv/inc_*），
    #         并成一张表后**整列空白**
    #     两条腿是两套规则、两套指标、两套排名 —— 本来就该是两块。
    #   ★ 一只票同时出现在两组里就**两组各出现一次**，各带那一组的名次与指标。
    #     那是事实：它在 A 里排 245、在 B 里排 1。
    pool, by_code = [], {}
    groups = []
    for st in steps:
        if st['kind'] not in ('query', 'codes_at'):
            continue
        rows = st.get('rows') or []
        g_rows = []
        for rank, row in enumerate(rows, 1):
            c = _code_of(row)
            if not c:
                continue
            e = {'code': c, 'name': names.get(c, ''), 'step': st['i'],
                 'rank': rank, 'of': len(rows),
                 'metrics': {k: v for k, v in row.items()
                             if k not in ('jq_code', 'code', 'MARKET_CODE')},
                 'passed': [], 'facts': [], 'status': '', 'dropped_by': None}
            pool.append(e)
            g_rows.append(e)
            by_code.setdefault(c, []).append(e)
        groups.append({'step': st['i'], 'where': st.get('where'),
                       'sql_head': st.get('sql_head'), 'sql': st.get('sql'),
                       'args': st.get('args') or {},
                       'arg_labels': {k: arg_label(k) for k in (st.get('args') or {})},
                       'n_rows': len(g_rows), '_rows': g_rows})

    # ---- 2) 逐步过滤：谁在这一步被筛掉、为什么（理由问规则本身要）----
    for st in steps:
        if st['kind'] == 'tradable':
            kept = set(st.get('codes_out') or ())
            for c in (st.get('codes_in') or ()):
                for e in by_code.get(c, ()):     # 一只票可能在多组里各有一行
                    if c in kept:
                        # ★ 去重：同一步可能被调用两次（froec 的缓冲区那次会
                        #   再过一遍），不去重的话理由里会出现"可买 · 可买"
                        if '可买' not in e['passed']:
                            e['passed'].append('可买')
                    elif e['dropped_by'] is None:
                        e['dropped_by'] = {
                            'step': st['i'], 'kind': 'tradable',
                            'why': (st.get('drop_why') or {}).get(c, '不可交易')}
        elif st['kind'] == 'had_limit_up':
            hit = set(st.get('codes_out') or ())
            for c in (st.get('codes_in') or ()):
                for e in by_code.get(c, ()):
                    if c not in hit:
                        continue
                    # ★ 只陈述事实，**不放进 `passed`**：涨停过不是"通过了过滤"，
                    #   它是一条中性观察。会不会因此被剔还要看策略怎么用
                    #   （froec 是「持有过 且 涨停过」才剔）—— 那条组合规则
                    #   不在这里复述，看它最终在不在 picked 里。
                    # ★ 短标签：具体窗口就在同一页的「步骤」里（start/end），
                    #   写进这一格只会被列宽截断成 "2026-08-04~2026-…"，
                    #   那比不写更糟 —— 看着像个日期，读不出含义。
                    _f = '窗口内涨停过'
                    if _f not in e['facts']:
                        e['facts'].append(_f)

    # ---- 3) 定状态。只认三个事实：买了 / 持有着 / 卖了 ----
    for e in pool:
        c = e['code']
        if c in picked:
            e['status'] = 'buy'
        elif c in sold:
            e['status'] = 'sell'
        elif c in held:
            e['status'] = 'hold'
        elif e['dropped_by']:
            e['status'] = 'drop'
        else:
            e['status'] = 'not_taken'

    # ---- 3b) 它在策略自己的哪几个集合里（目标池 / 备选 / 缓冲区…）----
    #      ★ "备选"不是我们判的，是**策略自己在 g 里声明的**（红利的
    #        `g.backup_list`）。这里只做归属标注，不复述任何入选规则。
    for e in pool:
        e['in_sets'] = sorted(k for k, v in (g_sets or {}).items()
                              if e['code'] in set(v))
        # ★ 它还出现在哪几组里。红利的 B 袖选中的票在 A 袖排第 233 ——
        #   A 那张表里看到"第 233 名 · 选中·买入"会以为是 A 选的，
        #   标一句"也在第 2 组"就不会误读了。这是事实，不是推断。
        e['also_in'] = sorted({x['step'] for x in by_code.get(e['code'], ())
                               if x['step'] != e['step']})

    # ---- 4) 每组各自截断（不是全局截断）----
    #   ★ 全局截断会让**小的那一组整个消失**：红利 A 袖 332 行、B 袖 6 行，
    #     一个 60 行的全局上限先把 A 填满，B 就一行都不剩了（实测 B 组 0 行）。
    #   ★ 买入/持有/卖出的那些**一定留**，它们正是要解释的。
    # ★ 「一定保留」的不只是买卖持有：**策略自己点过名的**（备选 / 缓冲区 /
    #   目标池）和**被规则剔掉的**也必须留 —— 前者是"差一点就选上"，
    #   后者带着剔除原因，这两类正是有信息量的那部分。
    #   只按名次截断会把排在 40 名的备选切掉，而那恰恰是要看的。
    def _must_keep(e):
        return (e['status'] in ('buy', 'sell', 'hold')
                or e.get('in_sets') or e.get('dropped_by'))

    for g in groups:
        rows = g.pop('_rows')
        keep = [e for e in rows if _must_keep(e)]
        rest = [e for e in rows if not _must_keep(e)]
        show = keep + rest[:max(0, MAX_POOL - len(keep))]
        show.sort(key=lambda e: e['rank'])
        g['rows'] = show
        g['shown'] = len(show)
        # 每组**自己的指标列**：两袖的指标不同，用并集会让两张表各空一半
        g['metric_meta'] = metric_meta(show)

    notes = []
    if not steps:
        notes.append('这一轮策略没有查数据 —— 多半不是调仓日，所以没有候选池。')
    if pool and not any(e['metrics'] for e in pool):
        notes.append('策略的查询只返回了代码、没有指标列，所以这里只有排名没有指标。'
                     '把末层 SELECT 多返回几列（不动 WHERE/ORDER/LIMIT）就有了 —— '
                     '见 lv/explain.py 的说明。')
    miss = [e['code'] for e in pool
            if e['status'] == 'buy' and not e['passed'] and not e['metrics']]
    if miss:
        notes.append('这些票在候选池里，但没有捕获到任何过滤步骤：%s' % ', '.join(miss[:6]))

    return {
        'captured': bool(steps),
        'steps': [_step_brief(st) for st in steps],
        # 一条查询 = 一组，各带自己的排名、指标列与条件
        'groups': groups,
        'n_groups': len(groups),
        'pool_total': len(pool),
        'pool_shown': sum(g['shown'] for g in groups),
        'codes_total': len({e['code'] for e in pool}),
        'params': params,
        'g_sets': {k: v for k, v in g_sets.items() if len(v) <= 200},
        'set_labels': {k: set_label(k) for k in g_sets},
        'notes': notes,
    }


def _step_brief(st):
    """步骤摘要：只留"做了什么、进多少出多少"，不带整份结果集。

    ★ 结果集另有 `pool` 那一份（已按上限截断）。两处都存整份的话，
      红利的 A 池几百行会在同一个 JSON 里出现两次。
    """
    out = {k: v for k, v in st.items()
           if k not in ('rows', 'codes_in', 'codes_out', 'drop_why')}
    if 'codes_in' in st:
        out['n_in'] = len(st['codes_in'])
    if 'codes_out' in st:
        out['n_out'] = len(st['codes_out'])
    elif 'rows' in st:
        out['n_out'] = len(st['rows'])
    if st.get('drop_why'):
        out['dropped'] = st['drop_why']
    return out


def why_line(entries, n_groups=1):
    """一句话理由。页面用结构化的那份，这句给导出 / 命令行 / 悬浮提示。

    ★ 入参是**这只票在各组里的那几行**（红利一只票可能同时在 A、B 两组里）——
      只报第一组的名次会给出"第 245/332 名"这种看着莫名其妙的理由，
      而它其实是被 B 组第 1 名选中的。
    """
    entries = [e for e in (entries or []) if e]
    if not entries:
        return ''
    bits = []
    for e in entries:
        grp = ('第 %d 组' % e['step']) if n_groups > 1 else '候选池'
        bits.append('%s第 %d/%d 名' % (grp, e['rank'], e['of']))
    for k in ('passed', 'facts'):
        for e in entries:
            for x in (e.get(k) or []):
                if x not in bits:
                    bits.append(x)
    d = next((e['dropped_by'] for e in entries if e.get('dropped_by')), None)
    if d:
        bits.append('被剔除：%s' % d.get('why'))
    elif all(e.get('status') == 'not_taken' for e in entries):
        bits.append('本次没轮到')
    return ' · '.join(bits)
