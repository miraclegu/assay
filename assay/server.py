#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""归档查看服务。仅用标准库 —— 不新增依赖。

    python3 serve.py            # 默认 http://127.0.0.1:8770

## 两个设计决定

1. **stdlib-only**（`ThreadingHTTPServer`）。装 Flask 只为一个本地看板不值得，
   而且多一个依赖就多一处「环境不一致导致跑不起来」。
2. **图表手写 SVG，不用 CDN**。本项目已被静默失败坑过多次 ——
   CDN 加载失败就是白屏，而且离线时完全不可用。
   权益曲线本质是一条 polyline，手写足够。

## 安全

`run_id` 来自 URL，**必须先在已知归档集合里查到才用于拼路径**，
绝不把用户输入直接 join 进文件路径（目录穿越）。
服务默认只监听 127.0.0.1。
"""
import ast
import hashlib
import json
import mimetypes
import os
import re
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pandas as pd

from . import registry

HERE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(os.path.dirname(HERE), 'web')
RUN_ID_RE = re.compile(r'^[0-9]{8}-[0-9]{6}-[0-9a-f]{6}(-[0-9]+)?$')

_lock = threading.Lock()
_index = {}          # run_id -> 归档目录（唯一可信的路径来源）
_cache = {}          # (run_id, what) -> DataFrame，避免每次请求都读 parquet


def _scan():
    """扫描归档目录，建立 run_id -> path 索引。"""
    idx = {}
    if os.path.isdir(registry.RUNS):
        for dp, dns, fns in os.walk(registry.RUNS):
            if 'meta.json' in fns:
                idx[os.path.basename(dp)] = dp
    with _lock:
        _index.clear()
        _index.update(idx)
    return idx


# ★ 放【仓库根】而不是 runs/ 下：runs/ 是 gitignore 的二进制产物目录，
#   而「选中了哪条规则、为什么」是**决策**，连同理由应该入版本控制。
#   run_id 在别的机器上可能不存在（归档不入库）—— api_marks 会过滤掉
#   找不到的，备注文字仍然保留，决策记录不丢。
MARKS_FILE = os.path.join(os.path.dirname(HERE), 'picks.json')
# 允许的标记类型。★ 白名单而非自由字符串：标记会进 HTML，也会进文件名无关的
#   JSON key，收窄取值范围比事后转义可靠。
MARK_KINDS = ('star',)
MARK_NOTE_MAX = 200


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
_names = {}          # datalake root -> [(code, valid_from, valid_to, name)]


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
    df = _read(q.get('id'), 'trades')
    if df is None:
        return None
    df = df.sort_values('exit_date')
    rows = _records(df)
    root = _dl_root(q.get('id'))
    if root:
        # 用【建仓日】的名称 —— 一笔交易的身份以建仓时为准；
        # 若持有期内改名（如变成 *ST），平仓日名称会不同，那属于另一个信息。
        _resolve_names(rows, root, 'entry_date')
    return {'total': len(df), 'rows': rows}


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
        return {'total': 0, 'offset': 0, 'limit': 0, 'rows': [], 'n_days': 0}
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
    out = {'date': d, 'holdings': [], 'buys': [], 'sells': []}
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
_ver_cache = {}


def _parse_note(code):
    """一句话简介：优先模块级 NOTE，回退 docstring 首行。

    回退是必要的 —— NOTE 是后加的，已有归档的快照里没有这个常量，
    但它们都有 docstring，这样旧版本立刻也有描述，不必重跑回测。
    """
    try:
        mod = ast.parse(code)
    except SyntaxError:
        return '', ''
    for node in mod.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name) \
                and node.targets[0].id == 'NOTE' \
                and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, str):
            return node.value.value.strip(), 'NOTE'
    doc = ast.get_docstring(mod) or ''
    return doc.strip().split('\n')[0].strip(), 'docstring'


_PARAM_RE = re.compile(
    r"^\s*g\.(?P<name>[A-Za-z_]\w*)\s*=\s*getattr\(\s*g\s*,\s*"
    r"['\"](?P<key>[A-Za-z_]\w*)['\"]\s*,\s*(?P<default>.+?)\s*\)"
    r"\s*(?:#\s*(?P<comment>.*))?$", re.M)


def _parse_params(code):
    """可填参数 = initialize 里 `g.x = getattr(g, 'x', 默认)` 声明的那些。

    ★ 只取 initialize 函数体内的 —— 函数体外/其它函数里的同形赋值不是参数
      （引擎的拼错防护也是按「initialize 期间是否写入」判定的，两边必须一致，
      否则看板列出的参数填进去会被引擎拒掉）。
    ★ 只保留默认值是**字面量**的：非字面量（如 set()、列表推导）是运行期状态，
      不是可填参数。
    """
    out = []
    try:
        mod = ast.parse(code)
    except SyntaxError:
        return out
    init = next((n for n in mod.body
                 if isinstance(n, ast.FunctionDef) and n.name == 'initialize'), None)
    if init is None:
        return out
    lo, hi = init.lineno, init.end_lineno
    seen = set()
    for m in _PARAM_RE.finditer(code):
        ln = code[:m.start()].count('\n') + 1
        if not (lo <= ln <= hi):
            continue
        name, dflt = m.group('name'), m.group('default').strip()
        if name != m.group('key') or name in seen:
            continue
        try:
            val = ast.literal_eval(dflt)
        except Exception:                                   # noqa: BLE001
            continue                                        # 运行期状态，不是参数
        if not isinstance(val, (int, float, str, bool)):
            continue
        seen.add(name)
        out.append({'name': name, 'default': val,
                    'type': 'bool' if isinstance(val, bool) else
                            ('int' if isinstance(val, int) else
                             ('float' if isinstance(val, float) else 'str')),
                    'comment': (m.group('comment') or '').strip()})
    return out


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
    if not ALLOW_BACKTEST:
        why = '服务以【只读模式】启动，网页触发回测已关闭。'\
              '需要的话用 python3 serve.py --allow-backtest，'\
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
#   要用网页触发回测：python3 serve.py --allow-backtest
ALLOW_BACKTEST = False

# 这个端点会起子进程跑回测。三条约束：
#   1) 服务只监听 127.0.0.1（见 serve()）
#   2) 参数名必须在该版本解析出的参数表里；参数值走白名单正则
#   3) 用 subprocess 列表参数，**不经过 shell**
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
    if not ALLOW_BACKTEST:
        return {'error': '服务以只读模式启动，网页触发回测已关闭。'
                         '用 python3 serve.py --allow-backtest 开启，'
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
_cur_fp = None


def _current_fp():
    global _cur_fp
    if _cur_fp is None:
        try:
            from .feed import PanelFeed
            _cur_fp = PanelFeed('2024-01-01', '2024-01-31').fingerprint()
        except Exception:                                   # noqa: BLE001
            _cur_fp = {'overall': None, 'parts': {}}
    return _cur_fp


def _staleness(meta):
    """返回 (是否失效, 变了哪些部件)。逐部件比对 —— 只说「变了」没用，
    要能指出是 panel 还是 std 变的，才知道影响哪些字段。"""
    cur = _current_fp()
    if not cur.get('overall'):
        return False, []
    fp = meta.get('data_fingerprint') or {}
    if not fp.get('overall'):
        return True, ['(归档时无指纹)']
    if fp['overall'] == cur['overall']:
        return False, []
    changed = []
    for name, c in (cur.get('parts') or {}).items():
        old = (fp.get('parts') or {}).get(name) or {}
        if old.get('hash') != c.get('hash'):
            changed.append(name)
    return True, changed or ['(部件未记录)']


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

_DOCS = [
    # (key, 侧栏标题, 分组, 相对仓库根的路径)
    ('need',    '1 · 按需求',      '数据字典（四维索引）', 'datalake/docs/数据字典/1-按需求索引.md'),
    ('api',     '2 · 按接口',      '数据字典（四维索引）', 'datalake/docs/数据字典/2-按接口索引.md'),
    ('field',   '3 · 按字段',      '数据字典（四维索引）', 'datalake/docs/数据字典/3-按字段索引.md'),
    ('trap',    '4 · 按陷阱',      '数据字典（四维索引）', 'datalake/docs/数据字典/4-按陷阱索引.md'),
    ('dictix',  '索引说明',        '数据字典（四维索引）', 'datalake/docs/数据字典/README.md'),
    ('jq',      '聚宽接口备忘',    '原文出处',             'datalake/docs/聚宽接口备忘.md'),
    ('jqfactor', '聚宽因子口径',   '原文出处',             'datalake/docs/jqfactor-口径.md'),
    ('qmt',     'QMT 探针实测',    '原文出处',             'assay/qmt/PROBE_FINDINGS.md'),
    ('qmtread', 'QMT 移植说明',    '原文出处',             'assay/qmt/README.md'),
    ('lake',    'datalake 说明',   '原文出处',             'datalake/README.md'),
    ('stop',    '止损方案实测',    '实测记录',             'assay/strategies/止损方案实测-固定_移动_吊灯.md'),
    ('exec',    '调仓时点与滑点',  '实测记录',             'assay/strategies/调仓执行时点-滑点与延迟实测.md'),
]


def _repo_root():
    """assay 与 datalake 的公共父目录。ASSAY_DOCS_ROOT 可覆盖。"""
    env = os.environ.get('ASSAY_DOCS_ROOT')
    if env:
        return os.path.abspath(os.path.expanduser(env))
    return os.path.dirname(registry.ROOT)


def _doc_path(rel):
    """把 _DOCS 的相对路径解到绝对路径，并确认没跑出仓库根（防目录穿越）。"""
    root = _repo_root()
    p = os.path.normpath(os.path.join(root, rel))
    return p if p.startswith(root) else None


def api_docs(_q):
    """侧栏清单。带 exists/mtime，缺文件在界面上直接显示为灰的，不静默消失。"""
    out = []
    for key, title, group, rel in _DOCS:
        p = _doc_path(rel)
        ok = bool(p) and os.path.isfile(p)
        out.append({'key': key, 'title': title, 'group': group, 'rel': rel,
                    'exists': ok,
                    'mtime': (datetime.fromtimestamp(os.path.getmtime(p))
                              .strftime('%Y-%m-%d %H:%M') if ok else None),
                    'bytes': os.path.getsize(p) if ok else 0})
    return {'docs': out, 'root': _repo_root()}


def api_doc(q):
    key = q.get('key')
    hit = [d for d in _DOCS if d[0] == key]
    if not hit:
        return None
    _, title, group, rel = hit[0]
    p = _doc_path(rel)
    if not p or not os.path.isfile(p):
        return {'key': key, 'title': title, 'group': group, 'rel': rel,
                'missing': True,
                'html': '<p class="dim">文件不存在：<code>%s</code></p>' % _esc(rel)}
    txt = open(p, encoding='utf-8', errors='replace').read()
    return {'key': key, 'title': title, 'group': group, 'rel': rel,
            'mtime': datetime.fromtimestamp(os.path.getmtime(p))
                             .strftime('%Y-%m-%d %H:%M'),
            'html': _md(txt)}


def _esc(t):
    return (t.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'))


def _md_inline(t):
    """行内标记：`code` / **bold** / [text](url)。

    ★ 顺序与隔离都有讲究，两个坑都踩过：
      1. 必须先把 code 段抠成占位符再处理加粗 —— 否则 `Feed.query(sql, **kw)`
         里的 ** 会和段落后面真正的 **加粗** 配成一对，把结构吃坏。
      2. 加粗用非贪婪 + 负向预查 (?!\\*)，因为加粗内容本身可能含单个星号：
         `**B 股 sh90*/sz20***` —— 用 [^*]+ 匹配不到，会把裸 ** 漏到页面上。
    """
    t = _esc(t)
    holes = []

    def _stash(m):
        holes.append('<code>%s</code>' % m.group(1))
        return '\x00%d\x00' % (len(holes) - 1)
    t = re.sub(r'`([^`]+)`', _stash, t)

    t = re.sub(r'\*\*(.+?)\*\*(?!\*)', lambda m: '<b>%s</b>' % m.group(1), t)

    def _link(m):
        label, href = m.group(1), m.group(2)
        if href.startswith('http'):
            return '<a href="%s" target="_blank" rel="noopener">%s</a>' % (href, label)
        # 站内 md 相对链接：能映射到 _DOCS 的就跳同页，否则只保留文字（不留死链）
        base = os.path.basename(href.split('#')[0])
        for key, _t, _g, rel in _DOCS:
            if os.path.basename(rel) == base:
                return '<a href="#/docs/%s">%s</a>' % (key, label)
        return label
    t = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', _link, t)

    return re.sub(r'\x00(\d+)\x00', lambda m: holes[int(m.group(1))], t)


def _md(text):
    """够用就好的 markdown -> html：标题 / 表格 / 代码块 / 列表 / 引用 / 分隔线。

    不引第三方库：这份 server 是零依赖的单文件（除 pandas），
    为一个查看页装 markdown 依赖不值得。文档里实际用到的语法就这几种。
    """
    out, i = [], 0
    lines = text.replace('\r\n', '\n').split('\n')
    n = len(lines)
    while i < n:
        ln = lines[i]

        if ln.startswith('```'):                       # 代码块
            i += 1
            buf = []
            while i < n and not lines[i].startswith('```'):
                buf.append(lines[i]); i += 1
            i += 1
            out.append('<pre><code>%s</code></pre>' % _esc('\n'.join(buf)))
            continue

        if re.match(r'^\s*\|.*\|\s*$', ln) and i + 1 < n \
                and re.match(r'^\s*\|[\s:|-]+\|\s*$', lines[i + 1]):
            head = [c.strip() for c in ln.strip().strip('|').split('|')]
            i += 2
            body = []
            while i < n and re.match(r'^\s*\|.*\|\s*$', lines[i]):
                body.append([c.strip() for c in lines[i].strip().strip('|').split('|')])
                i += 1
            t = ['<div class="dtw"><table class="dt"><thead><tr>']
            t += ['<th>%s</th>' % _md_inline(c) for c in head]
            t.append('</tr></thead><tbody>')
            for row in body:
                t.append('<tr>')
                for k in range(len(head)):
                    t.append('<td>%s</td>' % _md_inline(row[k] if k < len(row) else ''))
                t.append('</tr>')
            t.append('</tbody></table></div>')
            out.append(''.join(t))
            continue

        m = re.match(r'^(#{1,6})\s+(.*)$', ln)
        if m:
            lv = len(m.group(1))
            out.append('<h%d class="dh dh%d">%s</h%d>'
                       % (min(lv + 1, 6), lv, _md_inline(m.group(2)), min(lv + 1, 6)))
            i += 1
            continue

        if re.match(r'^\s*(-{3,}|\*{3,})\s*$', ln):
            out.append('<hr>'); i += 1; continue

        if ln.startswith('>'):
            # ★ 引用块里可能嵌标题、表格、列表（datalake/README 就有），
            #   所以剥掉 '> ' 之后【递归渲染】，不能把整块拼成一个段落 ——
            #   拼了的话表格的 |---|---| 会原样漏到页面上。
            buf = []
            while i < n and lines[i].startswith('>'):
                buf.append(re.sub(r'^>\s?', '', lines[i])); i += 1
            out.append('<blockquote>%s</blockquote>' % _md('\n'.join(buf)))
            continue

        if re.match(r'^\s*[-*]\s+', ln) or re.match(r'^\s*\d+\.\s+', ln):
            ordered = bool(re.match(r'^\s*\d+\.\s+', ln))
            items = []
            while i < n and (re.match(r'^\s*[-*]\s+', lines[i])
                             or re.match(r'^\s*\d+\.\s+', lines[i])
                             or (items and lines[i].startswith('  ') and lines[i].strip())):
                if re.match(r'^\s*[-*]\s+', lines[i]) or re.match(r'^\s*\d+\.\s+', lines[i]):
                    items.append(re.sub(r'^\s*(?:[-*]|\d+\.)\s+', '', lines[i]))
                else:
                    items[-1] += ' ' + lines[i].strip()       # 续行并进上一条
                i += 1
            tag = 'ol' if ordered else 'ul'
            out.append('<%s>%s</%s>' % (tag, ''.join(
                '<li>%s</li>' % _md_inline(x) for x in items), tag))
            continue

        if not ln.strip():
            i += 1; continue

        buf = [ln]                                      # 段落：连续非空行合并
        i += 1
        while i < n and lines[i].strip() and not re.match(
                r'^(#{1,6}\s|```|>|\s*\||\s*[-*]\s|\s*\d+\.\s|-{3,}\s*$)', lines[i]):
            buf.append(lines[i]); i += 1
        out.append('<p>%s</p>' % _md_inline(' '.join(x.strip() for x in buf)))
    return '\n'.join(out)


# ==================== 实盘模块（live）====================
# ★ 与 /api/backtest 同哲学：**默认关闭**。它会起后台线程定时跑策略，
#   而看板本身是纯读的、随时重启无代价。把「读」和「有副作用的写」
#   用一个开关分开，重启服务就不会打断正在跑的东西。
ALLOW_LIVE = False
_live_thread = None


def _live():
    from . import live as _m
    return _m


def _live_err(fn, *a, **kw):
    """统一把 LiveError 翻成 {'error': ...} —— 这些是【给用户看的】提示，
    不是 500。其余异常照常冒泡到 Handler 的 500 分支。"""
    m = _live()
    try:
        return fn(*a, **kw)
    except m.LiveError as e:
        return {'error': str(e)}


def api_live_accounts(_q):
    m = _live()
    out = []
    for a in m.load_accounts():
        pos = m.positions(a['id'])
        out.append(dict(a, n_positions=len(pos), cash=round(m.cash(a['id']), 2),
                        n_fills=len(m.fills(a['id'])),
                        n_versions=len(m.versions(a['id']))))
    cal = m.calendar_meta()
    return {'accounts': out, 'readonly': not ALLOW_LIVE,
            'calendar': {'source': cal.get('source'), 'max': cal.get('max'),
                         'authoritative_until': cal.get('authoritative_until'),
                         'warn': cal.get('warn'), 'error': cal.get('error')}}


def api_live_account(q):
    m = _live()
    aid = (q.get('id') or '').strip()
    return _live_err(lambda: {
        'account': m.get_account(aid),
        'positions': m.positions(aid),
        'cash': round(m.cash(aid), 2),
        'versions': m.versions(aid),
        'fills': list(reversed(m.fills(aid))),
        'signal': m.latest_signal(aid),
    })


def api_live_signal(q):
    m = _live()
    aid = (q.get('id') or '').strip()
    d = (q.get('date') or '').strip()
    return _live_err(lambda: (m.load_signal(aid, d) if d else m.latest_signal(aid))
                     or {'error': '还没有信号 —— 点「立即重算」'})


def api_live_code(q):
    m = _live()
    aid = (q.get('id') or '').strip()
    sha = (q.get('sha') or '').strip().lower()
    which = (q.get('file') or '').strip() or None

    def _go():
        code, full, names = m.version_code(aid, sha, which)
        return {'code': code, 'code_sha256': full, 'files': names}
    return _live_err(_go)


def _live_guard():
    if not ALLOW_LIVE:
        return {'error': '服务以只读模式启动，实盘模块已关闭。'
                         '用 python3 serve.py --live 开启。'}
    return None


def api_live_save(_q, body):
    """建/改账户；带 strategy_path 时顺便绑版本（append-only 留痕）。"""
    bad = _live_guard()
    if bad:
        return bad
    m = _live()
    b = body or {}
    aid = (b.get('id') or '').strip()

    def _go():
        m.upsert_account(aid, name=b.get('name'), init_cash=b.get('init_cash'),
                         broker_note=b.get('broker_note'),
                         tick_time=b.get('tick_time'),
                         warmup_start=b.get('warmup_start'))
        if b.get('strategy_path'):
            m.bind_version(aid, b['strategy_path'], b.get('params') or {},
                           b.get('reason') or '')
        return {'account': m.get_account(aid), 'versions': m.versions(aid)}
    return _live_err(_go)


def api_live_fill(_q, body):
    bad = _live_guard()
    if bad:
        return bad
    m = _live()
    b = body or {}
    aid = (b.get('id') or '').strip()
    rows = b.get('rows')
    if rows is None:
        rows = [b]
    if not isinstance(rows, list):
        return {'error': 'rows 必须是数组'}
    if len(rows) > 200:
        return {'error': '一次最多 200 笔'}
    ok, errs = [], []
    for i, r in enumerate(rows):
        try:
            ok.append(m.add_fill(
                aid, r.get('trade_date'), (r.get('code') or '').strip().upper(),
                (r.get('side') or '').strip(), r.get('shares'), r.get('price'),
                fee=r.get('fee') or 0, name=r.get('name') or '',
                source=r.get('source') or 'manual', note=r.get('note') or '',
                reverse_of=r.get('reverse_of')))
        except m.LiveError as e:
            errs.append('第 %d 行：%s' % (i + 1, e))
    return {'added': len(ok), 'errors': errs,
            'positions': m.positions(aid), 'cash': round(m.cash(aid), 2)}


def api_live_tick(_q, body):
    bad = _live_guard()
    if bad:
        return bad
    m = _live()
    b = body or {}
    aid = (b.get('id') or '').strip()
    if aid:
        return _live_err(lambda: m.make_signal(aid, force=True))
    return {'results': m.tick(force=True)}


def _live_loop():
    """守护线程：每 60s 看一次表。★ 幂等由 live.tick 保证（信号按
    for_date 落盘，已存在就跳过），所以轮询频率高不会重复算。"""
    import time
    m = _live()
    while True:
        try:
            for r in m.tick():
                if r.get('error'):
                    print('[live] %s: %s' % (r.get('account'), r['error']), flush=True)
                else:
                    print('[live] %s -> %s  卖%d 买%d'
                          % (r['account'], r['for_date'],
                             len(r.get('sell') or []), len(r.get('buy') or [])),
                          flush=True)
        except Exception as e:                              # noqa: BLE001
            print('[live] tick 异常: %s: %s' % (type(e).__name__, e), flush=True)
        time.sleep(60)


# ==================== 数据同步状态（只读）====================
# ★ 判据【不在这里实现】—— 调 datalake/build/sync_status.py。
#   新鲜度写两遍必然漂移（脚本说没问题、页面说落后 3 天）。
#   同步本身也不在这里跑：调度是 launchd 的事（daily_snapshot.py 漏一天
#   永久丢失，不能挂在「看板恰好开着」上）。这里只提供【看】+【手动触发一次】。

def _datalake_dir():
    """datalake 目录。与 PanelFeed 同一套解析。

    ★ 名字不能叫 `_dl_root` —— 本文件第 ~168 行已有一个 `_dl_root(run_id)`
      （按归档 meta 解 datalake 路径）。Python 对重复定义**不告警**，
      后定义的直接覆盖前面的，于是 api_equity/trades/holdings/rejects
      四个调用点全部 TypeError -> 500。实测就是这么炸的。
    """
    return os.environ.get('ASSAY_DATALAKE') or \
        os.path.join(os.path.dirname(registry.ROOT), 'datalake')


def api_sync(_q):
    """GET /api/sync —— 两条腿的新鲜度 + 上次同步日志摘要。"""
    dl = _datalake_dir()
    out = {'datalake': dl, 'readonly': not ALLOW_LIVE}
    script = os.path.join(dl, 'build', 'sync_status.py')
    if not os.path.isfile(script):
        return dict(out, error='找不到 %s' % script)
    import subprocess
    try:
        r = subprocess.run(['python3', script, '--json'], cwd=dl,
                           capture_output=True, text=True, timeout=120)
        out['status'] = json.loads(r.stdout) if r.returncode == 0 else None
        if out['status'] is None:
            out['error'] = (r.stderr or r.stdout or '')[-400:]
    except Exception as e:                                  # noqa: BLE001
        out['error'] = '%s: %s' % (type(e).__name__, e)
    # 最近几次同步日志（只列文件名与大小，内容按需取）
    ld = os.path.join(dl, '_manifest', 'sync_logs')
    logs = []
    if os.path.isdir(ld):
        for fn in sorted(os.listdir(ld), reverse=True)[:12]:
            if not fn.endswith('.log'):
                continue
            p = os.path.join(ld, fn)
            logs.append({'name': fn, 'bytes': os.path.getsize(p),
                         'mtime': datetime.fromtimestamp(
                             os.path.getmtime(p)).replace(microsecond=0).isoformat()})
    out['logs'] = logs
    return out


_LOG_RE = re.compile(r'^[0-9]{8}-[0-9]{6}\.log$')


def api_sync_log(q):
    """GET /api/sync/log?name=... —— ★ 只接受 8位日期-6位时间.log 这个形状，
    且只在 _manifest/sync_logs 下找。文件名来自 URL，不能直接 join。"""
    name = (q.get('name') or '').strip()
    if not _LOG_RE.match(name):
        return {'error': '日志名格式不对：%r' % name}
    p = os.path.join(_datalake_dir(), '_manifest', 'sync_logs', name)
    if not os.path.isfile(p):
        return {'error': '日志不存在：%s' % name}
    txt = open(p, encoding='utf-8', errors='replace').read()
    return {'name': name, 'text': txt[-200000:], 'bytes': os.path.getsize(p)}


def api_sync_run(_q, body):
    """POST /api/sync/run —— 手动触发一次同步（后台子进程，复用 _JOBS）。

    ★ 只有【手动】走这里；定时永远是 launchd。理由见 sync_daily.sh 文件头。
    """
    if not ALLOW_LIVE:
        return {'error': '服务以只读模式启动，手动同步已关闭。'
                         '用 python3 serve.py --live 开启。'}
    dl = _datalake_dir()
    sh = os.path.join(dl, 'sync_daily.sh')
    if not os.path.isfile(sh):
        return {'error': '找不到 %s' % sh}
    cmd = ['bash', sh]
    if (body or {}).get('no_live'):
        cmd.append('--no-live')
    job_id = 'sync-%s' % datetime.now().strftime('%H%M%S')
    _JOBS[job_id] = {'state': 'running', 'lines': [], 'cmd': cmd,
                     'sha': None, 'run_id': None, 'rc': None}
    threading.Thread(target=_run_job, args=(job_id, cmd, dl),
                     daemon=True).start()
    return {'job_id': job_id, 'cmd': ' '.join(cmd)}


ROUTES = {
    '/api/docs': api_docs,
    '/api/doc': api_doc,
    '/api/runs': api_runs,
    '/api/run': api_run,
    '/api/equity': api_equity,
    '/api/trades': api_trades,
    '/api/holdings': api_holdings,
    '/api/day': api_day,
    '/api/rejects': api_rejects,
    '/api/code': lambda q: api_text(q, 'strategy.py'),
    '/api/log': lambda q: api_text(q, 'run.log'),
    '/api/version': api_version,
    '/api/datafp': api_datafp,
    '/api/job': api_job,
    '/api/marks': api_marks,
    '/api/live/accounts': api_live_accounts,
    '/api/live/account': api_live_account,
    '/api/live/signal': api_live_signal,
    '/api/live/code': api_live_code,
    '/api/sync': api_sync,
    '/api/sync/log': api_sync_log,
}


class Handler(BaseHTTPRequestHandler):
    server_version = 'assay'

    def log_message(self, fmt, *args):       # 静音访问日志，别淹没终端
        pass

    def _send(self, code, body, ctype='application/json; charset=utf-8'):
        data = body if isinstance(body, bytes) else body.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):                       # noqa: N802
        u = urlparse(self.path)
        POSTS = {'/api/backtest': api_backtest, '/api/mark': api_mark,
                 '/api/live/save': api_live_save,
                 '/api/live/fill': api_live_fill,
                 '/api/live/tick': api_live_tick,
                 '/api/sync/run': api_sync_run}
        fn = POSTS.get(u.path)
        if fn is None:
            return self._send(404, json.dumps({'error': 'no such endpoint'}))
        try:
            n = int(self.headers.get('Content-Length') or 0)
            body = json.loads(self.rfile.read(n).decode('utf-8')) if n else {}
        except Exception as e:                   # noqa: BLE001
            return self._send(400, json.dumps({'error': '请求体不是合法 JSON: %s' % e},
                                              ensure_ascii=False))
        try:
            r = fn({}, body)
        except Exception as e:                   # noqa: BLE001
            import traceback
            traceback.print_exc()
            return self._send(500, json.dumps({'error': '%s: %s' % (type(e).__name__, e)},
                                              ensure_ascii=False))
        # ★ default=str 与 GET 分支保持一致 —— POST 的返回里也可能带
        #   date/Timestamp（实盘持仓的建仓日就是），少这一个参数就是 500。
        return self._send(200, json.dumps(r, ensure_ascii=False, default=str))

    def do_GET(self):                        # noqa: N802
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if u.path in ROUTES:
            try:
                r = ROUTES[u.path](q)
            except Exception as e:           # noqa: BLE001
                # 错误要显式返回，不能静默给空 —— 前端才好判断是没数据还是出错了
                import traceback
                traceback.print_exc()
                return self._send(500, json.dumps(
                    {'error': '%s: %s' % (type(e).__name__, e)}, ensure_ascii=False))
            if r is None:
                return self._send(404, json.dumps({'error': 'run_id 不存在或无效'},
                                                  ensure_ascii=False))
            return self._send(200, json.dumps(r, ensure_ascii=False, default=str))
        # 静态文件
        rel = u.path.lstrip('/') or 'index.html'
        p = os.path.normpath(os.path.join(WEB, rel))
        if not p.startswith(WEB) or not os.path.isfile(p):
            return self._send(404, 'not found', 'text/plain; charset=utf-8')
        ctype = mimetypes.guess_type(p)[0] or 'application/octet-stream'
        if ctype.startswith('text/') or ctype.endswith('javascript'):
            ctype += '; charset=utf-8'
        self._send(200, open(p, 'rb').read(), ctype)


def serve(host='127.0.0.1', port=8770, allow_backtest=False, allow_live=False):
    global ALLOW_BACKTEST, ALLOW_LIVE, _live_thread
    ALLOW_BACKTEST = bool(allow_backtest)
    ALLOW_LIVE = bool(allow_live)
    n = len(_scan())
    print('assay 归档查看服务  http://%s:%d   (%d 次回测)' % (host, port, n))
    print('模式：%s' % ('可触发回测（--allow-backtest）'
                      if ALLOW_BACKTEST else '只读（网页触发回测已关闭）'))
    # flush：重定向到文件时 stdout 是块缓冲，SIGTERM 不会刷新，
    # 启动横幅会看着像根本没打印。
    if ALLOW_LIVE:
        m = _live()
        accts = m.load_accounts()
        cal = m.calendar_meta()
        print('实盘模块：开启  %d 个账户  日历来源 %s'
              % (len(accts), cal.get('source')))
        _live_thread = threading.Thread(target=_live_loop, daemon=True)
        _live_thread.start()
    else:
        print('实盘模块：关闭（--live 开启）')
    print('Ctrl-C 退出', flush=True)
    ThreadingHTTPServer((host, port), Handler).serve_forever()
