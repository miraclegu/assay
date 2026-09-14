"""srv/runs.py —— 回测归档：目录 / 详情 / 权益 / 成交 / 版本 / 触发回测。"""
import ast
import hashlib
import json
import os
import re
import threading
from datetime import date, datetime
import pandas as pd

from .. import registry
from . import base
from .base import (MARKS_FILE, MARK_KINDS, MARK_NOTE_MAX, RUN_ID_RE, _cache, _current_fp, _index, _lock, _names, _parse_note, _parse_params, _scan, _staleness, _ver_cache)


def _load_marks():
    """run_id -> {'mark': ..., 'note': ..., 'ts': ...}。文件不存在或坏了都返回空。"""
    try:
        with open(MARKS_FILE, encoding='utf-8') as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:                                       # noqa: BLE001
        return {}



def _save_marks(d):
    """先写临时文件再 rename —— 半截文件会让整个面板的标记消失。"""
    os.makedirs(os.path.dirname(MARKS_FILE), exist_ok=True)
    tmp = MARKS_FILE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(d, f, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, MARKS_FILE)



def _dir(run_id):
    """★ 只接受索引里存在的 run_id —— 这是唯一的路径来源，杜绝目录穿越。"""
    if not RUN_ID_RE.match(run_id or ''):
        return None
    with _lock:
        d = _index.get(run_id)
    if d is None:
        _scan()
        with _lock:
            d = _index.get(run_id)
    return d



def _read(run_id, what):
    key = (run_id, what)
    with _lock:
        if key in _cache:
            return _cache[key]
    d = _dir(run_id)
    if d is None:
        return None
    p = os.path.join(d, what + '.parquet')
    if not os.path.exists(p):
        return pd.DataFrame()
    df = pd.read_parquet(p)
    with _lock:
        if len(_cache) > 24:            # 简单 LRU 上限，别把内存吃满
            _cache.pop(next(iter(_cache)))
        _cache[key] = df
    return df


# ---------------- 股票名称：按【当时】的名称解析 ----------------
# ★ 名称在归档时【不】写入 parquet，而在服务端按需 join。这样 24 次已有归档
#   立刻都能显示名称、不必重跑回测；归档文件也不必为一个展示字段膨胀。
# ★ 必须用【当时】的名称，不是当前名称 —— 002711 走过
#   欧浦钢网 -> 欧浦智网 -> ST欧浦 -> *ST欧浦 -> 欧浦退，
#   拿当前名称去标一笔 2016 年的交易是误导。
# ★ 数据源是 std/security_name（专用名称历史表），不是 security_status.name ——
#   后者只在【状态】变化时才有新行，公司改名而状态不变时它就停在旧值
#   （实测 2024-06-28 有 17.4% 的行不符）。

def _name_table(root):
    with _lock:
        if root in _names:
            return _names[root]
    f = os.path.join(root, 'std', 'security_name.parquet')
    tbl = {}
    if os.path.exists(f):
        df = pd.read_parquet(f)[['code', 'name', 'valid_from', 'valid_to']]
        for code, name, vf, vt in df.itertuples(index=False):
            tbl.setdefault(code, []).append(
                (str(vf)[:10], (str(vt)[:10] if vt is not None and str(vt) != 'NaT'
                                else '9999-12-31'), name))
        for v in tbl.values():
            v.sort()
    with _lock:
        _names[root] = tbl
    return tbl



def _resolve_names(rows, root, date_key='date'):
    """就地给每行补 name（该行日期当时的名称）。区间数很少（平均 2.5 段/只），
    线性扫描足够，不必上索引。"""
    tbl = _name_table(root)
    for r in rows:
        segs = tbl.get(r.get('code'))
        if not segs:
            continue
        d = str(r.get(date_key) or '')[:10]
        if not d:
            continue
        for vf, vt, nm in segs:
            if vf <= d < vt:
                r['name'] = nm
                break



def _factors(root, pairs):
    """取 `{(code, 'YYYY-MM-DD'): hfq_factor}`。

    🔴 **不能按 (code, 当日) 直接查** —— 停牌那天面板里根本没有行，
      于是查不到因子、那一格就保持后复权原样，页面上混出一个
      `1051.4037819988175` 这种"份额"（实测 603137 停牌日就是这样，
      而它**不报错**，只是那一行看着像别的量纲）。
      持仓是**逐日快照**，而引擎对停牌持仓是按最后已知价挂账的 ——
      快照里必然出现没有面板行的日子。

    ★ 复权因子是**阶梯函数**（只在除权日跳），所以取「≤ 该日的最后一个
      变更点」就是对的，而且只要查**变更点**（`GROUP BY code, factor`）——
      比逐日取整段小两个数量级。
    """
    if not pairs or not root:
        return {}
    codes = sorted(set(c for c, _d in pairs))
    days = sorted(set(d for _c, d in pairs if d))
    if not codes or not days:
        return {}
    import bisect
    import duckdb
    con = duckdb.connect(':memory:')
    try:
        q = ("SELECT jq_code, CAST(min(date) AS VARCHAR) AS d0, hfq_factor FROM "
             "read_parquet('%s/mart/panel_daily/panel_*.parquet') "
             "WHERE jq_code IN ('%s') AND date <= DATE '%s' "
             "GROUP BY jq_code, hfq_factor ORDER BY jq_code, d0"
             % (root, "','".join(codes), days[-1]))
        seg = {}
        for c, d0, f in con.execute(q).fetchall():
            if f:
                seg.setdefault(c, []).append((str(d0)[:10], float(f)))
    except Exception:                                       # noqa: BLE001
        return {}                       # 取不到就不换算，不让整页打不开
    finally:
        con.close()
    for v in seg.values():
        v.sort()
    out = {}
    for c, d in pairs:
        v = seg.get(c)
        if not v:
            continue
        i = bisect.bisect_right([x[0] for x in v], d) - 1
        # ★ 早于第一个变更点（新股上市前不该有持仓，但归档里出现过
        #   退市清算这类边角）—— 退回最早那个，不要静默跳过。
        out[(c, d)] = v[i][1] if i >= 0 else v[0][1]
    return out


def _to_raw(rows, root, specs):
    """把**后复权**的股数/价格就地换算成【不复权】（当时真实的那个数）。

    🔴🔴 **这是用户 2026-09-14 指出的：「交易记录的价格为什么是后复权的？
      展示出来的应该是当时不复权的价格。份额也同样。持仓的份额也有相同的
      问题。」—— 他是对的，而且这不只是标签问题。**

    引擎全程用后复权记账（那是对的：跨除权日比价必须复权），于是归档里
    `shares` 是**后复权记账单位**、`entry_price`/`exit_price` 是后复权价。
    直接摆到页面上的后果是：

        份额 774.835189  ← 券商那边根本没有这种数，真实是 **5700 股**
        价格 25.3986     ← 当时实际成交在 **3.4526**

    换算只要一个因子：`真实股数 = 后复权股数 × factor`、
    `不复权价 = 后复权价 ÷ factor`。实测三笔换出来全是**整手**
    （5700 / 600 / 3100），不复权价与面板 `open` 对得上。

    ★ **金额不用换** —— 因子在 `hfq股 × hfq价` 里天然约掉了，
      归档的 `gross_amount` 本来就是真实金额（实测三笔逐笔相同）。
      所以只动股数与价格，`pnl` / `ret` / `fee` 一律不碰。

    🔴 已知偏差：**引擎不知道的那些分红**（复权因子里有、而 `dividend` 表里
      没有那一条）会让 `hfq股 × factor` 偏大 —— 实测红利账户 601318
      偏 1.77%。这种偏差在模拟盘的 `recon` 里有量化，这里不静默取整掩盖它：
      **算出来多少就是多少**，只做四舍五入到整数（真实股数本来就是整数）。

    `specs`：`[(日期字段, 股数字段, 价格字段…)]`，见调用处。
    """
    if not root:
        return
    pairs = set()
    for r in rows:
        for dk, _sk, _pks in specs:
            d = str(r.get(dk) or '')[:10]
            if d and r.get('code'):
                pairs.add((r['code'], d))
    fac = _factors(root, pairs)
    if not fac:
        return
    for r in rows:
        for dk, sk, pks in specs:
            d = str(r.get(dk) or '')[:10]
            f = fac.get((r.get('code'), d))
            if not f:
                continue
            if sk and r.get(sk) is not None:
                r[sk + '_hfq'] = r[sk]          # 后复权口径留着（tooltip 用）
                r[sk] = int(round(float(r[sk]) * f))
            for pk in pks:
                if r.get(pk) is not None:
                    r[pk + '_hfq'] = r[pk]
                    r[pk] = round(float(r[pk]) / f, 4)


def _dl_root(run_id):
    d = _dir(run_id)
    if d is None:
        return None
    try:
        return json.load(open(os.path.join(d, 'meta.json'),
                              encoding='utf-8')).get('datalake')
    except Exception:                                       # noqa: BLE001
        return None



def _jsonable(v):
    if isinstance(v, float) and (v != v or v in (float('inf'), float('-inf'))):
        return None
    if hasattr(v, 'isoformat'):
        return v.isoformat()[:10]
    if hasattr(v, 'item'):
        try:
            return _jsonable(v.item())
        except Exception:                                   # noqa: BLE001
            return str(v)
    return v



def _records(df, cols=None):
    if df is None or df.empty:
        return []
    if cols:
        df = df[[c for c in cols if c in df.columns]]
    return [{k: _jsonable(v) for k, v in r.items()} for r in df.to_dict('records')]


# ---------------- API ----------------

def api_runs(_q):
    idx = _scan()
    shameta = _sha_meta()
    marks = _load_marks()
    out = []
    for rid, d in idx.items():
        try:
            meta = json.load(open(os.path.join(d, 'meta.json'), encoding='utf-8'))
            st = json.load(open(os.path.join(d, 'stats.json'), encoding='utf-8'))
        except Exception:                                   # noqa: BLE001
            continue
        out.append({k: _jsonable(v) for k, v in {
            'run_id': rid, 'group': meta.get('group'), 'strategy': meta.get('strategy'),
            'start': meta.get('first_day'), 'end': meta.get('last_day'),
            'cash': meta.get('cash'), 'ran_at': meta.get('ran_at'),
            # ★ 策略身份 = 文件内容哈希：同一路径改一个字符就是另一个策略。
            #   所以前端建树用 strategy_path（任意深度目录）+ code_sha 两层。
            'strategy_path': meta.get('strategy_path'),
            'code_sha': (meta.get('code_sha256') or '')[:8],
            'code_sha256': meta.get('code_sha256'),
            'note': (shameta.get(meta.get('code_sha256') or '') or ('', '', ''))[0],
            'note_src': (shameta.get(meta.get('code_sha256') or '') or ('', '', ''))[1],
            # ★ 版本分组用【语义哈希】：改注释/排版/简介不算新版本
            'sem_sha256': (shameta.get(meta.get('code_sha256') or '') or ('', '', ''))[2],
            'sem_sha': ((shameta.get(meta.get('code_sha256') or '') or ('', '', ''))[2] or '')[:8],
            'elapsed_sec': meta.get('elapsed_sec'),
            'trading_days': meta.get('trading_days'),
            'data_fp': ((meta.get('data_fingerprint') or {}).get('overall') or '')[:8],
            'stale': _staleness(meta)[0],
            'stale_parts': ','.join(_staleness(meta)[1]),
            'params': meta.get('params') or {},
            # ★ 成本口径必须带出来：本项目两次因为「拿滑点 0 的数字去比含滑点的
            #   基准」得出错误结论（FROEC 与 v0b 各一次）。选中的规则并列展示时
            #   必须能一眼看出口径是否一致，否则跨行对比又会是苹果比橘子。
            'slippage': (meta.get('cost') or {}).get('slippage'),
            'commission': (meta.get('cost') or {}).get('commission'),
            'close_tax': (meta.get('cost') or {}).get('close_tax'),
            'mark': (marks.get(rid) or {}).get('mark') or '',
            'mark_note': (marks.get(rid) or {}).get('note') or '',
            'annual_return': st.get('annual_return'),
            'max_drawdown': st.get('max_drawdown'),
            'excess_annual': st.get('excess_annual'),
            'info_ratio': st.get('info_ratio'), 'sharpe': st.get('sharpe'),
            'n_trades': st.get('n_trades'), 'win_rate': st.get('win_rate'),
            'turnover_per_year': st.get('turnover_per_year'),
        }.items()})
    out.sort(key=lambda x: x['run_id'], reverse=True)
    return out



def api_run(q):
    d = _dir(q.get('id'))
    if d is None:
        return None
    meta = json.load(open(os.path.join(d, 'meta.json'), encoding='utf-8'))
    st = json.load(open(os.path.join(d, 'stats.json'), encoding='utf-8'))
    return {'meta': {k: _jsonable(v) if not isinstance(v, (dict, list)) else v
                     for k, v in meta.items()},
            'stats': {k: _jsonable(v) for k, v in st.items()}}



def api_equity(q):
    """权益曲线 + 基准，归一化到同一起点便于叠加对比。"""
    rid = q.get('id')
    eq = _read(rid, 'equity')
    if eq is None:
        return None
    st = json.load(open(os.path.join(_dir(rid), 'stats.json'), encoding='utf-8'))
    cash0 = st.get('cash') or (eq['total_value'].iloc[0] if len(eq) else 1)
    bench = _read(rid, 'benchmark')
    out = {'dates': [str(x)[:10] for x in eq['date']],
           'equity': [round(float(v) / cash0, 6) for v in eq['total_value']],
           'cash_pct': [round(float(v), 6) for v in eq['cash_pct']],
           'n_positions': [int(v) for v in eq['n_positions']]}
    if bench is not None and not bench.empty:
        b0 = float(bench['close'].iloc[0])
        # 基准基点应为回测首日【前一交易日】收盘 —— stats 里已按此算过收益，
        # 这里为叠加显示用首日归一，两者口径不同故在前端标注
        bmap = {str(d)[:10]: float(c) / b0 for d, c in
                zip(bench['date'], bench['close'])}
        out['benchmark'] = [bmap.get(d) for d in out['dates']]
        out['benchmark_code'] = st.get('benchmark')
    return out



def api_trades(q):
    """成交流水：**一笔平仓拆成「买入」「卖出」两行**，按日期倒序分页。

    🔴 归档里 `trades.parquet` 是**一批一行**（FIFO 批次，在卖出时记录），
      同时带着建仓日与平仓日 —— 那是"往返"视角。但人看流水是按**时间**看的：
      "这天我买了什么、卖了什么"。所以展示层拆成两行，各自挂在自己的日期上。

    ★ **拆分必须在服务端做**，不能让前端拿到分页结果再拆 ——
      那样页边界会错（一页 100 条拆完变 200 条，且跨页的买卖会被切开）。

    字段落在哪一行，以**这件事什么时候发生**为准：
        买入行  日期/价格/份额/金额
        卖出行  日期/价格/份额/金额 + 持有天 / 收益率 / 盈亏 / 费用 /
                分红 / 红利税 / 卖出原因
    🔴 买入行**留空而不是填 0**：那些量在买入那一刻根本不存在
      （同实盘「费用留空 ≠ 填 0」那条：默认 0 会被读成"确实没有费用"）。

    ⚠️ 两条口径限制，页面上要说明：
      1. `fee` 只含**卖出侧**费用 —— 引擎在成交时按整笔委托算一次费，
         买入侧的费用没有逐笔落进 `trades.parquet`（只进了现金流与 `fee_paid` 合计）。
      2. **未平仓持仓的买入不在本表** —— `trades.parquet` 只在平仓时才写行。
         期末还持有的那几只，去「持仓」页看。
    """
    import pandas as pd
    rid = q.get('id')
    root = _dl_root(rid)
    # ---- 优先用【逐笔成交】：真实股数 + 不复权价，撮合当场记的 ----
    # 🔴 `trades.parquet` 的股数/价格是**后复权记账单位**，换算回真实值要靠
    #   `hfq_factor`，而它与 `dividend` 表并不总是对齐（实测两个方向都有），
    #   约 4% 的行换出来不是整手。`fills.parquet`（2026-09-14 起归档）是
    #   **不需要换算**的那份，还顺带含未平仓持仓的买入。
    fl = _read(rid, 'fills')
    if fl is not None and not fl.empty:
        return _trades_from_fills(rid, fl, root, q)
    df = _read(rid, 'trades')
    if df is None:
        return None
    if df.empty:
        return {'total': 0, 'offset': 0, 'limit': 0, 'rows': [], 'n_buy': 0,
                'n_sell': 0}

    buy = pd.DataFrame({
        'date': df['entry_date'], 'side': 'buy', 'code': df['code'],
        'shares': df['shares'], 'price': df['entry_price'],
        'amount': df['shares'] * df['entry_price'],
    })
    sell = pd.DataFrame({
        'date': df['exit_date'], 'side': 'sell', 'code': df['code'],
        'shares': df['shares'], 'price': df['exit_price'],
        'amount': df['gross_amount'], 'holding_days': df['holding_days'],
        'ret': df['ret'], 'pnl': df['pnl'], 'fee': df['fee'],
        'div_gross': df['div_gross'], 'div_tax': df['div_tax'],
        'reason': df['reason'],
    })
    out = pd.concat([buy, sell], ignore_index=True)
    # 倒序（最近的先看，与「持仓」页一致）；同日**先卖后买** —— 与引擎的
    # 撮合顺序一致（卖出先回笼现金），这样同一天的两组读起来是因果顺序。
    out['_s'] = (out['side'] == 'buy').astype(int)
    out = out.sort_values(['date', '_s', 'code'],
                          ascending=[False, True, True]).drop(columns=['_s'])
    total = len(out)
    try:
        limit = max(10, min(500, int(q.get('limit', 100))))
        offset = max(0, min(max(total - 1, 0), int(q.get('offset', 0))))
    except ValueError:
        limit, offset = 100, 0
    rows = _records(out.iloc[offset:offset + limit])
    if root:
        # 🔴 名称按**这一行自己的日期**解析，不再统一用建仓日 ——
        #   拆开之后买入行与卖出行是两个时点，持有期内改过名（如变 *ST）时
        #   各自显示当时的名字才是事实。
        _resolve_names(rows, root, 'date')
        # 🔴 **展示的是当时【不复权】的股数与价格** —— 券商对账单上的那个数。
        #   拆成买/卖两行之后每行只有一个日期一个价，正好按这一行自己的
        #   日期换算（买行用建仓日的因子、卖行用平仓日的）。
        _to_raw(rows, root, [('date', 'shares', ('price',))])
    return {'total': total, 'offset': offset, 'limit': limit, 'rows': rows,
            'n_buy': int(len(buy)), 'n_sell': int(len(sell))}




def _trades_from_fills(rid, fl, root, q):
    """用 `fills.parquet` 出成交流水 —— **一行就是一笔真成交**，不用拆也不用换算。

    与 `trades.parquet` 那条路的差别，页面上要说得出来：

        份额 / 价格   真实股数 + 不复权价（那条路要按因子换算，约 4% 不准）
        未平仓的买入  **在表里**（那条路只在平仓时才写行，所以看不到）
        收益率/盈亏   只有卖出行有，而且来自 `trades`（往返口径）—— 这里没有，
                      所以逐笔这条路的卖出行**不带 ret/pnl**：宁可留空，
                      也不拿"这一笔卖了多少钱"去冒充"这一笔赚了多少"
    """
    import pandas as pd
    out = fl.copy()
    out['date'] = out['date'].astype(str).str[:10]
    out['_s'] = (out['side'] == 'buy').astype(int)
    out = out.sort_values(['date', '_s', 'code'],
                          ascending=[False, True, True]).drop(columns=['_s'])
    total = len(out)
    try:
        limit = max(10, min(500, int(q.get('limit', 100))))
        offset = max(0, min(max(total - 1, 0), int(q.get('offset', 0))))
    except ValueError:
        limit, offset = 100, 0
    rows = _records(out.iloc[offset:offset + limit])
    # 卖出行补上往返口径的收益率/盈亏（按 code+日期 聚合到那一笔卖出上）
    tdf = _read(rid, 'trades')
    if tdf is not None and not tdf.empty:
        agg = {}
        for r in tdf.itertuples(index=False):
            k = (r.code, str(r.exit_date)[:10])
            a = agg.setdefault(k, {'pnl': 0.0, 'div_gross': 0.0,
                                   'div_tax': 0.0, 'hd': 0, 'n': 0,
                                   'reason': r.reason, 'cost': 0.0, 'amt': 0.0})
            a['pnl'] += float(r.pnl or 0)
            a['div_gross'] += float(r.div_gross or 0)
            a['div_tax'] += float(r.div_tax or 0)
            a['hd'] = max(a['hd'], int(r.holding_days or 0))
            a['cost'] += float(r.shares or 0) * float(r.entry_price or 0)
            a['amt'] += float(r.gross_amount or 0)
            a['n'] += 1
        for r in rows:
            if r.get('side') != 'sell':
                continue
            a = agg.get((r.get('code'), str(r.get('date'))[:10]))
            if not a:
                continue
            r['pnl'] = round(a['pnl'], 2)
            r['div_gross'] = round(a['div_gross'], 2)
            r['div_tax'] = round(a['div_tax'], 2)
            r['holding_days'] = a['hd']
            r['reason'] = a['reason']
            # ★ 收益率按**金额**算（Σ卖出额 / Σ成本 − 1），不是把各批的
            #   ret 平均 —— 批次大小不同，算术平均会给出一个谁都没拿到的数。
            r['ret'] = round(a['amt'] / a['cost'] - 1, 6) if a['cost'] else None
    if root:
        _resolve_names(rows, root, 'date')
    n_buy = int((fl['side'] == 'buy').sum())
    return {'total': total, 'offset': offset, 'limit': limit, 'rows': rows,
            'n_buy': n_buy, 'n_sell': int(total - n_buy), 'src': 'fills'}


def api_run_trades_of(q):
    """GET /api/run/trades_of?id=<run_id>&code= —— 这次回测在这只票上的买卖点。

    返回形状与 `/api/live/trades_of` **一致**（date/side/shares/price/fee），
    个股浮层用同一段代码画 B/S 标记 —— 两处形状不一样的话，浮层就得按来源
    分支渲染，而那种分支迟早只维护其中一条。

    🔴 **价格是【后复权】的。** 回测全程用后复权价记账（`entry_price` /
      `exit_price` 都是 hfq），所以浮层从回测打开时必须切到后复权 K 线，
      否则标记会整体飘走 —— **而它不报错**，看着像"买在了那根阴线上面"
      （同 `api_live_trades_of` 那条，只是方向相反：实盘成交价是不复权的）。
    """
    rid, code = q.get('id') or '', (q.get('code') or '')
    if not rid or not code:
        return {'error': '缺 id 或 code'}
    df = _read(rid, 'trades')
    if df is None:
        return None
    out = []
    if not df.empty:
        sub = df[df['code'] == code]
        for i, r in enumerate(sub.itertuples(index=False)):
            out.append({'account': rid, 'account_name': '回测',
                        'date': str(r.entry_date)[:10], 'side': 'buy',
                        'shares': float(r.shares), 'price': float(r.entry_price),
                        'fee': None, 'note': '', 'seq': i * 2})
            out.append({'account': rid, 'account_name': '回测',
                        'date': str(r.exit_date)[:10], 'side': 'sell',
                        'shares': float(r.shares), 'price': float(r.exit_price),
                        'fee': float(r.fee) if r.fee == r.fee else None,
                        'note': str(r.reason or ''), 'seq': i * 2 + 1})
    out.sort(key=lambda x: (x['date'], x['seq']))
    # 🔴 换成【不复权】，与页面上的成交记录同一口径（2026-09-14）。
    #   之前这里返回后复权价、浮层跟着切后复权 K 线 —— 那是**将就归档的
    #   存储口径**。现在归档展示层统一换算回不复权了，浮层也就回到 bfq：
    #   **规则只有一条 —— 成交价一律不复权**，实盘与回测再没有分支。
    _to_raw(out, _dl_root(rid), [('date', 'shares', ('price',))])
    return {'code': code, 'trades': out, 'fq': 'bfq'}


def _pruned(run_id, what):
    """这份归档的 `what` 明细是不是被 prune_runs.py 清掉了。

    🔴 **不写这个标记的话，"清理过"和"本来就没有"在页面上长得一模一样**
      —— `_read()` 读不到 parquet 时返回空 DataFrame（不报错），
      于是持仓页一片空白，人会以为"这次回测没持仓"。
      所以 prune_runs.py 往 meta.json 写 `pruned: {'holdings': '日期'}`，
      接口带出去，页面明说"明细已清理（重跑该回测可再生成）"。
    """
    d = _dir(run_id)
    if d is None:
        return None
    try:
        m = json.load(open(os.path.join(d, 'meta.json'), encoding='utf-8'))
    except Exception:                                           # noqa: BLE001
        return None
    return (m.get('pruned') or {}).get(what)


def api_holdings(q):
    """★ 分页返回，不按日筛选。全量 22k 行不能一次发 ——
    那是「能跑但很慢」的典型（前端渲染 22k 个 DOM 节点会卡死）。

    排序：日期【倒序】（最近的先看）+ 同日内权重降序。
    前端按日期变化插入分隔块，所以同一天的行必须连续 —— 排序保证了这一点。
    """
    rid = q.get('id')
    df = _read(rid, 'holdings')
    if df is None:
        return None
    if df.empty:
        return {'total': 0, 'offset': 0, 'limit': 0, 'rows': [], 'n_days': 0,
                'pruned': _pruned(rid, 'holdings')}
    df = df.sort_values(['date', 'weight'], ascending=[False, False])
    total = len(df)
    try:
        limit = max(10, min(500, int(q.get('limit', 100))))
        offset = max(0, min(total - 1, int(q.get('offset', 0))))
    except ValueError:
        limit, offset = 100, 0
    rows = _records(df.iloc[offset:offset + limit])
    root = _dl_root(rid)
    if root:
        _resolve_names(rows, root, 'date')      # 持仓用【快照当日】的名称
        # 🔴 份额与价格都换回【不复权】—— 同 api_trades 那条。
        #   `shares`/`last_price` 按**快照当日**换；`entry_price` 是建仓那天
        #   的价，必须按**建仓日**的因子换（拿当日因子去除会算出一个
        #   当时根本不存在的价，而它看着完全正常）。
        #   ★ `value` / `unrealized_pnl` / `weight` 不动：因子已经约掉了。
        _to_raw(rows, root, [('date', 'shares', ('last_price',)),
                             ('entry_date', None, ('entry_price',))])
    return {'total': total, 'offset': offset, 'limit': limit,
            'n_days': int(df['date'].nunique()), 'rows': rows}



def api_day(q):
    """某一天的持仓快照 + 当日买卖。**只读现有归档，不新增表**：
      持仓 <- holdings.parquet 的当日快照（它本来就是日频的）
      买入 <- trades.parquet 里 entry_date == 当日的那些笔
      卖出 <- trades.parquet 里 exit_date == 当日的那些笔（带 ret/pnl/reason）
    名称按【当日】解析：同一只票改过名，看哪天就显示哪天的名字。
    """
    rid = q.get('id')
    d = (q.get('d') or '')[:10]
    if not d:
        return None
    out = {'date': d, 'holdings': [], 'buys': [], 'sells': [],
           'holdings_pruned': _pruned(rid, 'holdings')}
    hd = _read(rid, 'holdings')
    if hd is not None and not hd.empty:
        sub = hd[hd['date'].astype(str).str[:10] == d]
        out['holdings'] = _records(sub.sort_values('weight', ascending=False))
    tr = _read(rid, 'trades')
    if tr is not None and not tr.empty:
        b = tr[tr['entry_date'].astype(str).str[:10] == d]
        sl = tr[tr['exit_date'].astype(str).str[:10] == d]
        out['buys'] = _records(b.sort_values('gross_amount', ascending=False))
        out['sells'] = _records(sl.sort_values('pnl'))
    root = _dl_root(rid)
    if root:
        _resolve_names(out['holdings'], root, 'date')
        _resolve_names(out['buys'], root, 'entry_date')
        _resolve_names(out['sells'], root, 'exit_date')
    return out



def api_rejects(q):
    df = _read(q.get('id'), 'rejects')
    if df is None:
        return None
    by = {}
    if not df.empty and 'reason' in df.columns:
        by = {str(k): int(v) for k, v in df['reason'].value_counts().items()}
    rows = _records(df)
    root = _dl_root(q.get('id'))
    if root:
        _resolve_names(rows, root, 'date')      # 拒单用【下单当日】的名称
    return {'total': len(df), 'by_reason': by, 'rows': rows}



def api_text(q, fname):
    d = _dir(q.get('id'))
    if d is None:
        return None
    p = os.path.join(d, fname)
    if not os.path.exists(p):
        return {'text': ''}
    return {'text': open(p, encoding='utf-8', errors='replace').read()}


# ---------------- 版本（代码哈希）层：简介 / 可填参数 / 源码 ----------------
# ★ 一律从【归档的代码快照】(strategy.py) 里解析，不读磁盘上的当前文件 ——
#   版本就是当时那份代码，磁盘上的文件可能早就改过了。

def _version_info(sha):
    """sha(全长或前缀) -> 该版本的快照代码 + 简介 + 参数表。"""
    if sha in _ver_cache:
        return _ver_cache[sha]
    idx = _scan()
    hit = None
    for rid, d in idx.items():
        try:
            meta = json.load(open(os.path.join(d, 'meta.json'), encoding='utf-8'))
        except Exception:                                   # noqa: BLE001
            continue
        full = meta.get('code_sha256') or ''
        if full == sha or full.startswith(sha):
            hit = (d, meta, full)
            break
    if hit is None:
        return None
    d, meta, full = hit
    cp = os.path.join(d, 'strategy.py')
    code = open(cp, encoding='utf-8').read() if os.path.exists(cp) else ''
    note, nsrc = _parse_note(code)
    sem = meta.get('semantic_sha256') or registry.safe_semantic_sha256(code)
    info = {'code_sha256': full, 'code_sha': full[:8], 'code': code,
            'sem_sha256': sem, 'sem_sha': sem[:8],
            'note': note, 'note_src': nsrc, 'params': _parse_params(code),
            'strategy_path': meta.get('strategy_path'),
            'group': meta.get('group'), 'strategy': meta.get('strategy')}
    _ver_cache[full] = info
    _ver_cache[sha] = info
    return info



def _sha_meta():
    """字节哈希 -> (简介, 简介来源, 语义哈希)。目录树一次性取用。

    ★ 语义哈希对**旧归档现算**（从归档的 strategy.py 快照解析），
      所以 200 条已有归档立刻都能按行为分组，不必重跑回测。
      meta.json 里有记录的直接用，没有的才算 —— 新归档不重复解析。
    """
    out = {}
    for rid, d in _scan().items():
        try:
            meta = json.load(open(os.path.join(d, 'meta.json'), encoding='utf-8'))
        except Exception:                                   # noqa: BLE001
            continue
        full = meta.get('code_sha256') or ''
        if not full or full in out:
            continue
        cp = os.path.join(d, 'strategy.py')
        if not os.path.exists(cp):
            continue
        code = open(cp, encoding='utf-8').read()
        note, src = _parse_note(code)
        sem = meta.get('semantic_sha256') or registry.safe_semantic_sha256(code)
        out[full] = (note, src, sem)
    return out



def api_version(q):
    info = _version_info((q.get('sha') or '').strip().lower())
    if info is None:
        return None
    # 能不能用这个版本重跑？—— 只有磁盘上的当前文件内容哈希【正好等于】该版本时才行。
    # 文件改过之后，那个版本就是历史快照，拿现在的文件去跑得到的是【另一个版本】，
    # 静默照跑会让归档里出现一条挂错版本的记录。
    p = os.path.join(registry.ROOT, info['strategy_path'] or '')
    cur, why = None, ''
    if not info['strategy_path'] or not os.path.isfile(p):
        why = '策略文件已不在原路径：%s —— 无法回测' % (info['strategy_path'] or '(未记录)')
    else:
        cur = hashlib.sha256(open(p, 'rb').read()).hexdigest()
        if cur != info['code_sha256']:
            # 能跑，但跑的是**磁盘上的当前代码**，不是正在看的这个历史快照。
            # 必须说清楚 —— 否则结果会归档成另一个版本，看起来像「这个版本又跑了一次」。
            why = ('磁盘上的文件已改动：当前 %s ≠ 本版本 %s。'
                   '点回测跑的是**当前版本**，结果会归档到 %s 那个版本下。'
                   % (cur[:8], info['code_sha256'][:8], cur[:8]))
    # 当前版本的可填参数按磁盘文件解析 —— 参数表要和真正会被执行的代码一致
    cur_params = info['params']
    if cur and cur != info['code_sha256']:
        cur_params = _parse_params(open(p, encoding='utf-8').read())
    # 只读模式直接反映在 runnable 上 —— 前端已有「置灰按钮 + 显示原因」的通路，
    # 复用它，不另造一套提示。
    if not base.ALLOW_BACKTEST:
        why = '服务以【只读模式】启动，网页触发回测已关闭。'\
              '这个服务是 --readonly 起的；去掉它重启即可，'\
              '或直接用命令行 python3 run.py <策略>'
        return dict(info, runnable=False, same_version=(cur == info['code_sha256']),
                    current_sha256=cur, current_params=cur_params,
                    not_runnable_why=why, readonly=True)
    return dict(info, runnable=bool(cur), same_version=(cur == info['code_sha256']),
                current_sha256=cur, current_params=cur_params,
                not_runnable_why=why, readonly=False)


# ---------------- 触发回测 ----------------
# ★ 默认【关闭】。看板本身是纯读的（读归档文件 + 原子写 picks.json），
#   随时重启无代价；而 /api/backtest 会拉起 subprocess 跑回测，
#   一旦开着，重启服务就等于打断正在跑的任务。把「读」和「会起进程的写」
#   分开之后，日常看板可以随便重启（比如加了新接口之后）。
#   要用网页触发回测：python3 serve.py（默认全功能；--readonly 才关）

_JOBS = {}

_VAL_RE = re.compile(r'^[A-Za-z0-9_.+-]{1,32}$')

_DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')



def _run_job(job_id, cmd, cwd):
    import subprocess
    j = _JOBS[job_id]
    try:
        pr = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, bufsize=1,
                              universal_newlines=True, encoding='utf-8',
                              errors='replace')
        j['pid'] = pr.pid
        for line in pr.stdout:
            j['lines'].append(line.rstrip('\n'))
            if len(j['lines']) > 4000:
                del j['lines'][:2000]
        rc = pr.wait()
        j['rc'] = rc
        j['state'] = 'done' if rc == 0 else 'failed'
        # 找出新归档的 run_id：回测输出里会打归档路径
        for line in reversed(j['lines']):
            m = re.search(r'([0-9]{8}-[0-9]{6}-[0-9a-f]{6}(?:-[0-9]+)?)', line)
            if m:
                j['run_id'] = m.group(1)
                break
        _scan()
    except Exception as e:                                  # noqa: BLE001
        j['state'] = 'failed'
        j['lines'].append('启动失败: %s: %s' % (type(e).__name__, e))



def api_backtest(q, body):
    """POST /api/backtest —— 用指定版本 + 指定参数跑一次回测。

    只读模式下直接拒绝：前端会置灰按钮，但接口不能只靠前端把关。
    """
    if not base.ALLOW_BACKTEST:
        return {'error': '服务以只读模式启动，网页触发回测已关闭。'
                         '这个服务是 --readonly 起的，去掉它重启即可，'
                         '或直接用命令行 python3 run.py <策略>'}
    sha = (body.get('sha') or '').strip().lower()
    info = _version_info(sha)
    if info is None:
        return {'error': '版本不存在'}
    v = api_version({'sha': sha})
    if not v.get('runnable'):
        return {'error': v.get('not_runnable_why') or '该版本无法回测'}

    # ★ 参数白名单取【磁盘当前代码】解析出的那份 —— 真正被执行的是它。
    #   用历史快照的参数表校验会放过当前代码里已删掉的参数，引擎再报错，
    #   错误就出现在离原因很远的地方。
    allowed = {p['name'] for p in (v.get('current_params') or info['params'])}
    params = body.get('params') or {}
    bad = [k for k in params if k not in allowed]
    if bad:
        return {'error': '未知参数：%s（该版本可填：%s）'
                         % (', '.join(bad), ', '.join(sorted(allowed)) or '无')}

    cmd = ['python3', 'run.py', info['strategy_path']]
    for key, flag in (('start', '--start'), ('end', '--end')):
        val = str(body.get(key) or '').strip()
        if val:
            if not _DATE_RE.match(val):
                return {'error': '%s 日期格式应为 YYYY-MM-DD，收到 %r' % (key, val)}
            cmd += [flag, val]
    cash = str(body.get('cash') or '').strip()
    if cash:
        try:
            c = float(cash)
            assert c > 0
        except Exception:                                   # noqa: BLE001
            return {'error': '本金必须是正数，收到 %r' % cash}
        cmd += ['--cash', repr(c)]
    if body.get('jq_cost'):
        cmd.append('--jq-cost')
    for k, val in params.items():
        sv = str(val).strip()
        if sv == '':
            continue                                        # 空 = 用该版本默认值
        if not _VAL_RE.match(sv):
            return {'error': '参数 %s 的值 %r 不合法（只允许字母数字 . _ + -）' % (k, sv)}
        cmd += ['--param', '%s=%s' % (k, sv)]

    job_id = '%s-%d' % (datetime.now().strftime('%H%M%S'), len(_JOBS) + 1)
    _JOBS[job_id] = {'state': 'running', 'lines': [], 'cmd': cmd,
                     'sha': info['code_sha256'], 'run_id': None, 'rc': None}
    threading.Thread(target=_run_job, args=(job_id, cmd, registry.ROOT),
                     daemon=True).start()
    return {'job_id': job_id, 'cmd': ' '.join(cmd)}



def api_job(q):
    j = _JOBS.get(q.get('id') or '')
    if j is None:
        return None
    return {'state': j['state'], 'rc': j['rc'], 'run_id': j['run_id'],
            'cmd': ' '.join(j['cmd']), 'lines': j['lines'][-200:]}



# ---------------- 数据版本：归档是否仍与当前数据一致 ----------------
# ★ 面板/std 重建后，旧归档的数字就是【旧数据】算出来的，但报告看着完全正常。
#   实测过一次代价：面板修复年报缺失后，175 条归档的收益全部失效，
#   而我在之后几轮里还在拿它们跨表对照 —— 必须让失效在看板上可见。
#   **只标记不删除**：归档是自包含的历史记录，删了就没法复盘。

def api_mark(_q, body):
    """给某次回测打/清标记。★ 只接受索引里存在的 run_id（_dir 已防目录穿越）。"""
    rid = (body or {}).get('run_id') or ''
    if _dir(rid) is None:
        return {'error': 'run_id 不存在: %s' % rid}
    mark = (body.get('mark') or '').strip()
    if mark and mark not in MARK_KINDS:
        return {'error': '未知标记类型 %r（可用: %s）' % (mark, ', '.join(MARK_KINDS))}
    note = (body.get('note') or '')
    if not isinstance(note, str):
        return {'error': 'note 必须是字符串'}
    # 去掉控制字符再截断：备注会直接渲染进页面
    note = ''.join(c for c in note if c >= ' ' or c == '\t')[:MARK_NOTE_MAX].strip()
    with _lock:
        d = _load_marks()
        if mark:
            d[rid] = {'mark': mark, 'note': note,
                      'ts': datetime.now().isoformat(timespec='seconds')}
        else:
            d.pop(rid, None)          # 空 mark = 取消标记
        _save_marks(d)
    return {'ok': True, 'run_id': rid, 'mark': mark, 'note': note}



def api_marks(_q):
    """列出所有被标记的回测（含关键指标），供面板顶部「选中的规则」用。

    ★ 标记文件里可能残留【已删除归档】的 run_id（清理归档时不会同步删标记），
      所以这里要过滤掉找不到目录的，否则前端会渲染出点不开的空行。
    """
    marks = _load_marks()
    if not marks:
        return []
    byid = {r['run_id']: r for r in api_runs({})}
    out = []
    for rid, m in marks.items():
        r = byid.get(rid)
        if r is None:
            continue
        out.append(dict(r, mark=m.get('mark') or '', mark_note=m.get('note') or '',
                        mark_ts=m.get('ts') or ''))
    out.sort(key=lambda x: (x.get('group') or '', x.get('strategy') or '',
                            x.get('run_id') or ''))
    return out



def api_datafp(_q):
    cur = _current_fp()
    n_stale = n_ok = 0
    for rid, d in _scan().items():
        try:
            meta = json.load(open(os.path.join(d, 'meta.json'), encoding='utf-8'))
        except Exception:                                   # noqa: BLE001
            continue
        st, _ = _staleness(meta)
        if st:
            n_stale += 1
        else:
            n_ok += 1
    return {'current': cur.get('overall'), 'parts': cur.get('parts'),
            'n_stale': n_stale, 'n_current': n_ok}


# ================= 数据字典：直接渲染磁盘上的 md =================
# ★ 为什么不把内容烤进前端：这批口径结论会持续补充。烤进 html 就等于开了第二份真相，
#   改 md 忘了改 html 就分叉 —— 而分叉的文档比没有文档更危险。
#   这里【每次请求重读文件】，改 md 刷新页面就生效，前端一行都不用动。
#   新增一份文档只需在 _DOCS 加一行。
