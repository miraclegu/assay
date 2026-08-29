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
    return dict(info, runnable=bool(cur), same_version=(cur == info['code_sha256']),
                current_sha256=cur, current_params=cur_params, not_runnable_why=why)


# ---------------- 触发回测 ----------------
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
    """POST /api/backtest —— 用指定版本 + 指定参数跑一次回测。"""
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


ROUTES = {
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
        POSTS = {'/api/backtest': api_backtest, '/api/mark': api_mark}
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
        return self._send(200, json.dumps(r, ensure_ascii=False))

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


def serve(host='127.0.0.1', port=8770):
    n = len(_scan())
    print('assay 归档查看服务  http://%s:%d   (%d 次回测)' % (host, port, n))
    print('Ctrl-C 退出')
    ThreadingHTTPServer((host, port), Handler).serve_forever()
