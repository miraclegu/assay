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
import sys
import threading
import time
from datetime import date, datetime, timedelta
from datetime import time as dtime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pandas as pd

from . import registry

HERE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(os.path.dirname(HERE), 'web')
RUN_ID_RE = re.compile(r'^[0-9]{8}-[0-9]{6}-[0-9a-f]{6}(-[0-9]+)?$')

_BOOT_TS = __import__('time').time()
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
    ('need',    '1 · 按需求',      '数据字典（索引）', 'datalake/docs/数据字典/1-按需求索引.md'),
    ('api',     '2 · 按接口',      '数据字典（索引）', 'datalake/docs/数据字典/2-按接口索引.md'),
    ('field',   '3 · 按字段',      '数据字典（索引）', 'datalake/docs/数据字典/3-按字段索引.md'),
    ('trap',    '4 · 按陷阱',      '数据字典（索引）', 'datalake/docs/数据字典/4-按陷阱索引.md'),
    ('extapi',  '5 · 外部行情接口', '数据字典（索引）', 'datalake/docs/数据字典/5-外部行情接口.md'),
    ('dictix',  '索引说明',        '数据字典（索引）', 'datalake/docs/数据字典/README.md'),
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


# ★ 代码指纹：**web/index.html 每次请求都从磁盘读，而 Python 模块只在进程
#   启动时加载一次**。所以长时间开着的 serve.py 会出现「新页面 + 旧 API」——
#   页面去读 API 还没有的字段，渲染出一堆 undefined，而没有任何报错。
#   实测踩到：11:49 启动的服务配 12:53 改的页面，账户设置里全是 undefined。
#   这里把服务端代码的 mtime 暴露出去，页面自己比对并明说"请重启"。
def _code_stamp():
    out = []
    for f in ('server.py', 'live.py'):
        p = os.path.join(HERE, f)
        try:
            out.append(int(os.path.getmtime(p)))
        except OSError:
            out.append(0)
    return {'loaded_at': int(_BOOT_TS), 'code_mtime': max(out)}


def api_live_accounts(_q):
    """GET /api/live/accounts[?all=1] —— 默认隐去已归档的账户。"""
    m = _live()
    show_all = (_q or {}).get('all') in ('1', 'true')
    out = []
    for a in m.load_accounts():
        if a.get('archived') and not show_all:
            continue
        pos = m.positions(a['id'])
        # ★ 红点用【盘上最新那份信号】判，不重算 —— 重算要重放 30 天 warmup，
        #   而这是每次打开实盘页都会跑的列表接口。
        alert, why = m.signal_alert(m.latest_signal(a['id']))
        out.append(dict(a, n_positions=len(pos), cash=round(m.cash(a['id']), 2),
                        n_fills=len(m.fills(a['id'])),
                        n_versions=len(m.versions(a['id'])),
                        alert=alert, alert_why=why))
    cal = m.calendar_meta()
    # ★ 「权不权威」由服务端判 —— 名单只存在 live.AUTHORITATIVE_CAL 一处。
    #   前端原来硬编码 `source !== 'jq.get_all_trade_days'`，于是把
    #   tdx.raw_holidays（同级可信、每次生成都跑对数）误报成不权威，
    #   每次打开实盘页都弹一条假告警。假告警看多了就不看告警了。
    cal = dict(cal, authoritative=(cal.get('source') in m.AUTHORITATIVE_CAL))
    return {'accounts': out, 'readonly': not ALLOW_LIVE,
            'code': _code_stamp(),
            # ★ 两个开关是独立的：--live 管账户/成交/信号，--allow-backtest 管
            #   起子进程跑回测。前端要分别置灰，否则按钮点了才知道被拒。
            'can_backtest': bool(ALLOW_BACKTEST and ALLOW_LIVE),
            'next_id': m.new_account_id(),
            'calendar': {'source': cal.get('source'), 'max': cal.get('max'),
                         'authoritative': cal.get('authoritative'),
                         'authoritative_until': cal.get('authoritative_until'),
                         'warn': cal.get('warn'), 'error': cal.get('error')}}


def api_live_account(q):
    m = _live()
    aid = (q.get('id') or '').strip()
    # ★ 主视图【不再整包带流水】—— 成交多了之后这个响应会越来越大，
    #   而主视图根本不显示流水（它有独立页面 + 分页）。
    def _go():
        sig = m.latest_signal(aid)
        alert, why = m.signal_alert(sig)
        # ★ 用户明确要的：「如果发现缺失最新的数据则立即调用一次」。
        #   页面来问持仓的时候顺手看一眼实时库落后没有，落后就补抓。
        #   只在交易时段生效（stale_minutes 非时段返回 None），
        #   而且只有 --live 才抓 —— 只读模式不该往外发请求。
        if ALLOW_LIVE:
            _rt_catch_up()
        rows = m.fills(aid)
        return {
            'account': m.get_account(aid),
            # ★ 前端不要自己维护一份费率默认值 —— 那是第二份实现，
            #   会和后端 FEE_DEFAULT 漂移（实测：前端漏了 regulatory，
            #   输入框读出 undefined，保存直接被后端拒）。
            'fee_effective': m.fee_model(m.get_account(aid)),
            'fee_default': dict(m.FEE_DEFAULT),
            'fee_history': m.fee_rates(aid),
            # ★ 选项表由服务端给 —— 前端抄过一次费率默认值，抄漏一个键就
            #   读成 undefined、保存被拒而错误藏在浮层里（见 fee_effective）
            'transfer_extra_opts': m.TRANSFER_EXTRA,
            # ★ 行情最新数据日【单独给】—— 判断信号新不新的第一依据，
            #   页面上要有自己的位置，不是塞在括号里当脚注
            'data_day': _live_quiet(m.latest_data_day),
            # 当前配置折成的总费率（按 10 万一笔算；最低佣金 binding 时会更高）
            'fee_rates': m.effective_rates(m.fee_model(m.get_account(aid))),
            'pos': m.positions_valued(aid),
            'cash': round(m.cash(aid), 2),
            'versions': m.versions(aid),
            'cashflows': list(reversed(m.cashflows(aid))),
            'n_fills': len(rows),
            'fee_total': round(sum(float(f.get('fee') or 0) for f in rows), 2),
            'fee_estimated_n': sum(1 for f in rows if f.get('fee_estimated')),
            'signal': sig,
            # ★ 待办要不要默认展开、账户列表的红点，同一个判据
            #   （live.signal_alert）—— 判据分两处写就一定会分叉
            'alert': alert, 'alert_why': why,
        }
    return _live_err(_go)


def _live_quiet(fn, *a):
    """取不到就返回 None —— 这类"锦上添花"的字段不该拖垮整个账户页。"""
    try:
        return fn(*a)
    except Exception:                                       # noqa: BLE001
        return None


def api_live_fills(q):
    """GET /api/live/fills?id=&offset=&limit= —— 成交流水，倒序分页。

    ★ 分页在服务端做。成交攒到几千笔时整包发过去，前端渲染几千个 DOM
      会明显卡 —— 归档的持仓表当初就是这么踩过的（见 api_holdings）。
    """
    m = _live()
    aid = (q.get('id') or '').strip()

    def _go():
        rows = list(reversed(m.fills(aid)))
        total = len(rows)
        try:
            off = max(0, int(q.get('offset') or 0))
        except Exception:                                   # noqa: BLE001
            off = 0
        try:
            lim = int(q.get('limit') or 50)
        except Exception:                                   # noqa: BLE001
            lim = 50
        lim = max(1, min(500, lim))
        if off >= total:
            off = max(0, (total - 1) // lim * lim)          # 越界收敛到最后一页
        # 已被冲正的原记录：前端要划掉它，服务端顺手标出来，
        # 免得前端为了判断这个把全量流水都拉一遍
        reved = {f.get('reverse_of') for f in m.fills(aid) if f.get('reverse_of')}
        page = []
        # ★ 名称在【服务端】补：批量粘贴的成交只有代码。前端补不了 ——
        #   它没有面板。留空的话流水页只剩代码，得对着代码猜是哪只票。
        pg_rows = rows[off:off + lim]
        try:
            nm = m.names_of([f['code'] for f in pg_rows])
        except Exception:                                   # noqa: BLE001
            nm = {}
        for f in pg_rows:
            page.append(dict(f, _dead=((f.get('uid') or f['ts']) in reved),
                             name=(f.get('name') or nm.get(f['code'], ''))))
        return {'total': total, 'offset': off, 'limit': lim, 'rows': page,
                'fee_total': round(sum(float(f.get('fee') or 0)
                                       for f in rows), 2),
                'fee_estimated_n': sum(1 for f in rows if f.get('fee_estimated'))}
    return _live_err(_go)


def api_live_equity(q):
    """GET /api/live/equity?id= —— 逐日权益曲线 + 时间加权收益。"""
    m = _live()
    aid = (q.get('id') or '').strip()
    return _live_err(lambda: m.equity_curve(aid))


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


def api_live_strategy(q):
    """GET /api/live/strategy?id=&sha= —— 账户绑的这个版本：
    源码快照 + 参数表 + **用同一版本跑过的历史回测**。

    ★ 关联的键是主文件【自身】哈希，不是账户的打包哈希 ——
      账户的 code_sha256 覆盖「主文件 + 依赖」（否则改 froec.py 不会改变
      账户版本号 = 版本悄悄漂移），而归档的 meta.code_sha256 是单文件哈希。
      两者口径不同，直接比会永远匹配不上。
    """
    m = _live()
    aid = (q.get('id') or '').strip()
    sha = (q.get('sha') or '').strip().lower()

    def _go():
        v = m._version_row(aid, sha)
        code, full, names = m.version_code(aid, v['code_sha256'])
        main_sha = m._main_sha(aid, v)
        out = {'version': v, 'code': code, 'files': names,
               'main_sha256': main_sha,
               'params_declared': _parse_params(code),
               'note': _parse_note(code)[0]}
        # 归档里同一主文件版本跑过的回测
        same, other = [], []
        for rid, d in _scan().items():
            try:
                meta = json.load(open(os.path.join(d, 'meta.json'), encoding='utf-8'))
            except Exception:                               # noqa: BLE001
                continue
            if not main_sha or meta.get('code_sha256') != main_sha:
                continue
            try:
                stats = json.load(open(os.path.join(d, 'stats.json'), encoding='utf-8'))
            except Exception:                               # noqa: BLE001
                stats = {}
            row = {'run_id': rid, 'params': meta.get('params') or {},
                   'start': meta.get('start'), 'end': meta.get('end'),
                   'cash': meta.get('cash'),
                   'annual': stats.get('annual'), 'total': stats.get('total_return'),
                   'max_drawdown': stats.get('max_drawdown'),
                   'sharpe': stats.get('sharpe'),
                   'stale': _staleness(meta)[0]}
            (same if row['params'] == (v.get('params') or {}) else other).append(row)
        key = lambda r: (r['start'] or '', r['end'] or '')
        out['runs_same_params'] = sorted(same, key=key, reverse=True)
        out['runs_other_params'] = sorted(other, key=key, reverse=True)
        return out
    return _live_err(_go)


def api_live_backtest(_q, body):
    """POST /api/live/backtest —— 用账户绑的【那个版本 + 那组参数】跑一次回测。

    ★ 只有磁盘文件仍等于绑定版本时才允许。否则跑出来的是**另一个版本**，
      而它会被归档成「这个版本回测过」—— 与 api_version 里那条同样的陷阱：
      「文件改过之后，那个版本就是历史快照，拿现在的文件去跑得到的是另一个
      版本，静默照跑会让归档里出现一条挂错版本的记录」。
    """
    bad = _live_guard()
    if bad:
        return bad
    if not ALLOW_BACKTEST:
        return {'error': '触发回测未开启 —— 加 --allow-backtest'}
    m = _live()
    b = body or {}
    aid = (b.get('id') or '').strip()
    try:
        acct = m.get_account(aid)
        v = m._version_row(aid, (b.get('sha') or acct.get('code_sha256') or ''))
    except m.LiveError as e:
        return {'error': str(e)}
    rel = v.get('strategy_path') or ''
    disk = os.path.join(registry.ROOT, rel)
    if not os.path.isfile(disk):
        return {'error': '策略文件已不在原路径：%s' % rel}
    want = m._main_sha(aid, v)
    cur = hashlib.sha256(open(disk, 'rb').read()).hexdigest()
    if want and cur != want:
        return {'error': '磁盘上的 %s 已改动（当前 %s ≠ 绑定版本 %s）。'
                         '现在跑会归档成【另一个版本】，看起来像"这个版本回测过"。'
                         '要么先把账户重新绑到当前版本，要么用命令行显式跑。'
                         % (rel, cur[:8], want[:8])}
    cmd = ['python3', 'run.py', rel]
    for k, val in (v.get('params') or {}).items():
        cmd += ['--param', '%s=%s' % (k, val)]
    for key, flag in (('start', '--start'), ('end', '--end')):
        sv = str(b.get(key) or '').strip()
        if sv:
            if not _DATE_RE.match(sv):
                return {'error': '%s 应为 YYYY-MM-DD，收到 %r' % (key, sv)}
            cmd += [flag, sv]
    csh = str(b.get('cash') or '').strip()
    if csh:
        try:
            c = float(csh)
            assert c > 0
        except Exception:                                   # noqa: BLE001
            return {'error': '本金必须是正数'}
        cmd += ['--cash', repr(c)]
    job_id = 'lvbt-%s' % datetime.now().strftime('%H%M%S')
    _JOBS[job_id] = {'state': 'running', 'lines': [], 'cmd': cmd,
                     'sha': want, 'run_id': None, 'rc': None}
    threading.Thread(target=_run_job, args=(job_id, cmd, registry.ROOT),
                     daemon=True).start()
    return {'job_id': job_id, 'cmd': ' '.join(cmd)}


def api_live_fee_add(_q, body):
    """POST /api/live/fee_rate —— **新增**一档费率（append-only）。

    ★ 刻意【没有】改已有费率的接口。改了会让已经按它算过的成交无从解释；
      想改就新增一档更晚生效的把它盖掉，历史仍然留着、看得见改过。
    """
    bad = _live_guard()
    if bad:
        return bad
    m = _live()
    b = body or {}
    aid = (b.get('id') or '').strip()

    def _go():
        m.migrate_fee(aid)          # 老数据先落成第一档，再追加
        r = m.add_fee_rate(aid, b.get('from'), b.get('fee') or {},
                           note=b.get('note') or '',
                           supersede=bool(b.get('supersede')))
        return {'added': r, 'history': m.fee_rates(aid)}
    return _live_err(_go)


def api_live_fee_infer(_q, body):
    """POST /api/live/fee_infer —— 从一笔真实成交的账单反推费率。

    ★ 让用户【照账单填】而不是自己拆算：券商 App 显示的"佣金费率"通常是
      **含规费**的口径（实测红利账户万0.8 = 净佣金万0.26 + 规费万0.54），
      自己拆开去配会少收规费那部分，且不报错。
    """
    m = _live()
    b = body or {}

    def _go():
        mod = m.infer_fee_model(
            b.get('amount'), b.get('commission') or 0,
            regulatory=b.get('regulatory') or 0,
            transfer=b.get('transfer') or 0,
            stamp=b.get('stamp') or 0,
            min_commission=b.get('min_commission') or 0)
        amt = float(b.get('amount'))
        # 自证：用反推出的费率复算这一笔，应与账单合计一致
        n = int(b.get('shares') or 0) or 100
        full = dict(m.FEE_DEFAULT)
        full.update(mod)
        # ★ 复算要带【代码】：过户费是否另收分市场。账单是哪只票就用哪只，
        #   没给就按沪市样板（另收，保守方向）。
        code = (b.get('code') or '600000.XSHG').strip().upper()
        bd = m.fee_breakdown('buy', n, amt / n,
                             b.get('date') or '2026-01-01', full, code)
        want = round(float(b.get('commission') or 0)
                     + float(b.get('regulatory') or 0)
                     + float(b.get('transfer') or 0), 2)
        # ★ 最低佣金是否含规费，**这一笔定不出来** —— 要一笔小额成交
        #   （金额 < 2 万，最低会 binding）。不提醒的话用户会以为全配好了。
        floor_binds = (amt * float(mod['commission'])
                       < float(mod['min_commission'] or 0))
        return {'fee': mod, 'recompute': bd['total'], 'breakdown': bd,
                'bill_total': want, 'match': abs(bd['total'] - want) <= 0.02,
                'incl_reg': mod['commission_incl_reg'], 'code': code,
                'market': m.market_of(code),
                'rates': m.effective_rates(full, amt),
                'need_small_bill': bool(mod['min_commission']) and not floor_binds}
    return _live_err(_go)


def api_live_cash(_q, body):
    """POST /api/live/cash —— 入金 / 出金 / 分红到账 / 手工调整。append-only。"""
    bad = _live_guard()
    if bad:
        return bad
    m = _live()
    b = body or {}
    aid = (b.get('id') or '').strip()

    def _go():
        m.add_cashflow(aid, b.get('date'), b.get('amount'),
                       kind=(b.get('kind') or 'deposit'), note=b.get('note') or '')
        return {'cash': round(m.cash(aid), 2), 'cashflows': list(reversed(m.cashflows(aid)))}
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
    aid = (b.get('id') or '').strip() or m.new_account_id()

    def _go():
        m.upsert_account(aid, name=b.get('name'), init_cash=b.get('init_cash'),
                         broker_note=b.get('broker_note'),
                         tick_time=b.get('tick_time'),
                         warmup_start=b.get('warmup_start'),
                         fee=b.get('fee'))
        if b.get('strategy_path'):
            m.bind_version(aid, b['strategy_path'], b.get('params') or {},
                           b.get('reason') or '')
        if 'archived' in b:
            m.archive_account(aid, bool(b['archived']))
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
            # ★ price / fee 都【原样透传】（含 None/''）—— 不要 `or 0`。
            #   `r.get('fee') or 0` 会把"没填"变成"明确说 0 元"；
            #   price 同理，留空的语义是"按当日开盘价取"，不是 0。
            ok.append(m.add_fill(
                aid, r.get('trade_date'), (r.get('code') or '').strip().upper(),
                (r.get('side') or '').strip(), r.get('shares'),
                price=r.get('price'), fee=r.get('fee'),
                name=r.get('name') or '',
                source=r.get('source') or 'manual', note=r.get('note') or '',
                reverse_of=r.get('reverse_of'),
                fee_estimated=bool(r.get('fee_estimated')),
                price_from=r.get('price_from'),
                force_price=bool(r.get('force_price') or b.get('force_price'))))
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


# ==================== 盘中 1 分钟线 ====================
# ★ 只有 server 开着才跑（用户明确要的）。这件事**漏了不要紧** ——
#   trends2 每次给全天、快照下一分钟就补上，而实时盈亏只在看页面时才有意义。
#   （对比 daily_snapshot.py 那种"漏一天永久丢失"的，才必须挂 launchd。）
_RT = {'thread': None, 'last': None, 'rounds': 0, 'err': None,
       'catch_at': 0}


def _rt():
    from assay import realtime as m
    return m


def _rt_codes():
    """要抓哪些 —— 所有未归档账户的当前持仓。

    ★ 只抓持仓，不抓自选/全市场：用户要的是"持仓的实时盈亏"。
      范围越小越不容易触发限流，而限流是这条链上唯一的风险。
    """
    m = _live()
    out = set()
    for a in m.load_accounts():
        if a.get('archived'):
            continue
        try:
            out.update(m.positions(a['id']))
        except Exception:                                   # noqa: BLE001
            pass
    return sorted(out)


def _rt_loop():
    """守护线程：交易时段内每 60s 一轮。

    ★ 时段与交易日都在这里判：
      · 9:30~11:31 / 13:00~15:01（收盘那根要到 15:01 才拿得到）
      · 非交易日整天不抓 —— 日历取不到（None）时**照抓**：
        "不知道是不是交易日"和"确定不是"是两件事，前者宁可多抓一次。
    """
    import time as _t
    rt = _rt()
    while True:
        try:
            now = datetime.now()
            day_ok = rt.is_trading_day()
            if rt.in_session(now) and day_ok is not False:
                codes = _rt_codes()
                if codes:
                    # 收盘那一轮对全部持仓强抓，保证当天 bar 完整
                    force = now.time() >= dtime(15, 0)
                    r = rt.poll_once(codes, force_all=force)
                    _RT['last'] = r
                    _RT['rounds'] += 1
                    _RT['err'] = r.get('fail') or None
        except Exception as e:                              # noqa: BLE001
            _RT['err'] = [{'stage': 'loop', 'error': '%s: %s' % (type(e).__name__, e)}]
            print('[rt] 异常: %s: %s' % (type(e).__name__, e), flush=True)
        _t.sleep(60)


# 距上次"补抓"至少隔这么久，避免页面一刷新就打一串请求
_RT_MIN_GAP = 20.0


def _rt_catch_up(max_stale=2):
    """落后就补一次。**有节流**：多个标签页同时刷新不该变成一串请求。"""
    rt = _rt()
    now = time.time()
    if now - (_RT.get('catch_at') or 0) < _RT_MIN_GAP:
        return None
    try:
        st = rt.stale_minutes()
        if st is None or st <= max_stale:
            return None
        codes = _rt_codes()
        if not codes:
            return None
        _RT['catch_at'] = now
        r = rt.poll_once(codes)
        _RT['last'] = r
        _RT['rounds'] += 1
        return r
    except Exception as e:                                  # noqa: BLE001
        _RT['err'] = [{'stage': 'catch_up', 'error': '%s: %s' % (type(e).__name__, e)}]
        return None


def api_rt_status(_q):
    """GET /api/rt/status —— 1 分钟线库的状态 + 轮询情况。"""
    rt = _rt()
    try:
        st = rt.status()
        st['poll'] = {'rounds': _RT['rounds'], 'last': _RT['last'],
                      'err': _RT['err'], 'running': bool(_RT['thread'])}
        st['stale_minutes'] = rt.stale_minutes()
        st['codes_watched'] = _rt_codes() if ALLOW_LIVE else []
        return st
    except Exception as e:                                  # noqa: BLE001
        return {'error': '%s: %s' % (type(e).__name__, e)}


def api_rt_bars(q):
    """GET /api/rt/bars?code=&day= —— 某只票当日 1 分钟线（画分时用）。"""
    rt = _rt()
    try:
        return {'code': q.get('code'), 'bars': rt.bars(q.get('code') or '',
                                                       q.get('day'))}
    except Exception as e:                                  # noqa: BLE001
        return {'error': '%s: %s' % (type(e).__name__, e)}


def api_rt_poll(_q, body):
    """POST /api/rt/poll —— 立刻抓一次（页面上的"刷新实时价"）。"""
    if not ALLOW_LIVE:
        return {'error': '服务以只读模式启动 —— 用 python3 serve.py --live 开启'}
    rt = _rt()
    try:
        codes = _rt_codes()
        if not codes:
            return {'error': '没有持仓，没什么可抓的'}
        r = rt.poll_once(codes, force_all=bool((body or {}).get('all')))
        _RT['last'] = r
        _RT['rounds'] += 1
        return r
    except Exception as e:                                  # noqa: BLE001
        return {'error': '%s: %s' % (type(e).__name__, e)}


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
        if out['status'] and out['status'].get('calendar'):
            c = out['status']['calendar']
            c['authoritative'] = c.get('source') in _live().AUTHORITATIVE_CAL
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
    # ★ 自动同步状态跟着一起给 —— 按钮的文字要照它显示（"开启"还是"关闭"），
    #   前端不能自己猜。也不该为这一个字段再打一次请求。
    out['auto'] = autosync_status()
    return out


# ============================ 自动同步（launchd） ============================
# ★ "自动同步"就是那个 launchd agent，没有第二套定时。serve.py 里【不做】
#   定时线程 —— daily_snapshot.py 是漏一天永久丢失的（tdx 的名称/分类/板块
#   成分是 type-1 覆盖写），挂在"看板恰好开着"上不可靠。launchd 还会在机器
#   睡过预定时刻后醒来补跑。所以这个开关只是 load / unload 那个 agent。
SYNC_LABEL = 'com.miraclegu.finacial.sync'
SYNC_PLIST = SYNC_LABEL + '.plist'


def _launch_agents_dir():
    return os.path.join(os.path.expanduser('~'), 'Library', 'LaunchAgents')


def _sync_plist_paths():
    """(仓库里的正本, 已安装的那份)。"""
    return (os.path.join(_datalake_dir(), '_manifest', SYNC_PLIST),
            os.path.join(_launch_agents_dir(), SYNC_PLIST))


def autosync_status():
    """自动同步开着还是关着 —— 按钮要照这个显示文字，不能瞎猜。"""
    import subprocess
    src, dst = _sync_plist_paths()
    out = {'label': SYNC_LABEL, 'plist': dst, 'src': src,
           'installed': os.path.isfile(dst), 'src_exists': os.path.isfile(src),
           'supported': sys.platform == 'darwin'}
    if not out['supported']:
        out['on'] = None
        out['note'] = 'launchd 只有 macOS 有；其它平台请自行接 cron'
        return out
    try:
        r = subprocess.run(['launchctl', 'list', SYNC_LABEL],
                           capture_output=True, text=True, timeout=15)
        out['loaded'] = (r.returncode == 0)
    except Exception as e:                                  # noqa: BLE001
        out['loaded'] = None
        out['note'] = '%s: %s' % (type(e).__name__, e)
    out['on'] = bool(out['installed'] and out['loaded'])
    # ★ 已安装那份与仓库正本不一致时要说出来：改了 plist 但没重新安装，
    #   跑的还是旧的（比如时间还停在旧的 18:10），而这**不会报错**。
    if out['installed'] and out['src_exists']:
        try:
            out['drift'] = (open(src, 'rb').read() != open(dst, 'rb').read())
        except Exception:                                   # noqa: BLE001
            out['drift'] = None
    # 计划时间从【已安装那份】里读 —— 显示正在生效的，不是仓库里的
    out['schedule'] = None
    try:
        txt = open(dst if out['installed'] else src, encoding='utf-8').read()
        h = re.search(r'<key>Hour</key>\s*<integer>(\d+)</integer>', txt)
        mi = re.search(r'<key>Minute</key>\s*<integer>(\d+)</integer>', txt)
        if h and mi:
            out['schedule'] = '%02d:%02d' % (int(h.group(1)), int(mi.group(1)))
    except Exception:                                       # noqa: BLE001
        pass
    return out


def api_sync_auto(_q):
    """GET /api/sync/auto —— 自动同步的当前状态。"""
    return autosync_status()


def api_sync_auto_set(_q, body):
    """POST /api/sync/auto —— 开/关自动同步（load / unload 那个 launchd agent）。

    ★ 用 `-w`：它同时写 Disabled 标记，重启后仍然生效。不带 -w 的话
      "关掉"只活到下次登录 —— 而那种"以为关了其实又开了"比开着更糟。
    ★ 开启时若 LaunchAgents 下没有或与仓库正本不一致，先复制过去 ——
      否则会 load 到一份旧的 plist，而这不会报错。
    """
    if not ALLOW_LIVE:
        return {'error': '服务以只读模式启动 —— 用 python3 serve.py --live 开启'}
    import shutil
    import subprocess
    on = bool((body or {}).get('on'))
    st = autosync_status()
    if not st.get('supported'):
        return {'error': st.get('note') or '当前平台不支持 launchd'}
    src, dst = _sync_plist_paths()
    if on:
        if not os.path.isfile(src):
            return {'error': '找不到 plist 正本：%s' % src}
        if not st['installed'] or st.get('drift'):
            os.makedirs(_launch_agents_dir(), exist_ok=True)
            shutil.copyfile(src, dst)
    elif not st['installed']:
        return dict(autosync_status(), changed=False,
                    note='本来就没安装，无需关闭')
    cmd = ['launchctl', 'load' if on else 'unload', '-w', dst]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    st2 = autosync_status()
    # ★ 以【复查到的状态】为准，不以命令返回码为准：launchctl 对"已经是这个
    #   状态"会报错退出，而那不是失败。判据永远是"现在到底开着没"。
    if st2.get('on') != on:
        return dict(st2, changed=False,
                    error='%s 失败：%s' % ('开启' if on else '关闭',
                                          (r.stderr or r.stdout or
                                           '返回码 %d' % r.returncode).strip()[-300:]))
    return dict(st2, changed=True, cmd=' '.join(cmd))


# ==================== 个股（搜索 / 面板 / K 线 / 财务） ====================
# ★ 这一层【不做 SQL 输入】—— 那是「🔍 查数据」页的事。个股页给的是
#   固定的几个问题的答案（这只票现在什么样、K 线什么形态、财务什么趋势），
#   所以接口是固定形状的，不接受任意查询。


def _stock():
    from assay import stock as m
    return m


def _stock_err(fn):
    m = _stock()
    try:
        return fn()
    except m.StockError as e:
        return {'error': str(e)}


def api_stock_search(q):
    """GET /api/stock/search?q=&limit= —— 代码或名称模糊搜。"""
    m = _stock()
    return _stock_err(lambda: m.search((q.get('q') or ''),
                                       q.get('limit') or 20))


def api_stock_profile(q):
    """GET /api/stock/profile?code= —— 最新一天的全部关键字段 + 区间涨幅。"""
    m = _stock()
    return _stock_err(lambda: dict(m.profile(q.get('code') or ''),
                                   units=m.FIELD_UNIT))


def api_stock_kline(q):
    """GET /api/stock/kline?code=&n=&fq= —— 日 K（含服务端算好的均线）。"""
    m = _stock()
    return _stock_err(lambda: m.kline(q.get('code') or '',
                                      n=q.get('n') or 250,
                                      fq=(q.get('fq') or 'bfq'),
                                      end=q.get('end')))


def api_stock_finance(q):
    """GET /api/stock/finance?code=&n= —— 按报告期的财务时序。"""
    m = _stock()
    return _stock_err(lambda: m.finance(q.get('code') or '',
                                        n=q.get('n') or 16))


def api_stock_indicators(q):
    """GET /api/stock/indicators?code=&n=&fq= —— MACD/KDJ/RSI/BOLL。"""
    m = _stock()
    return _stock_err(lambda: m.indicators(q.get('code') or '',
                                           n=q.get('n') or 250,
                                           fq=(q.get('fq') or 'bfq')))


def api_stock_events(q):
    """GET /api/stock/events?code=&since= —— 除权/财报/解禁/股本变动。"""
    m = _stock()
    return _stock_err(lambda: m.events(q.get('code') or '', q.get('since')))


def api_stock_peers(q):
    """GET /api/stock/peers?code=&n= —— 同申万一级行业。"""
    m = _stock()
    return _stock_err(lambda: m.peers(q.get('code') or '', n=q.get('n') or 20))


def api_stock_links(q):
    """GET /api/stock/links?code= —— 实盘持仓 / 自选 / 回测选过它。"""
    m = _stock()
    return _stock_err(lambda: m.links(q.get('code') or ''))


def api_stock_sectors(q):
    """GET /api/stock/sectors?code= —— 所属申万行业与通达信板块。"""
    mk = _market()
    return _market_err(lambda: mk.stock_sectors(q.get('code') or ''))


def api_compare(q):
    """GET /api/compare?codes=a,b,c&n= —— 多股【后复权】归一涨幅。"""
    m = _stock()
    cs = [x for x in (q.get('codes') or '').replace(' ', ',').split(',') if x]
    return _stock_err(lambda: m.compare(cs, n=q.get('n') or 250))


# ==================== 盘面 / 行业板块 ====================
def _market():
    from assay import market as m
    return m


def _market_err(fn):
    mk = _market()
    try:
        return fn()
    except mk.MarketError as e:
        return {'error': str(e)}


def api_market_overview(q):
    """GET /api/market/overview?date=&top= —— 某天的全市场。"""
    mk = _market()
    return _market_err(lambda: mk.overview(q.get('date'), top=q.get('top') or 15))


def api_sector_list(q):
    """GET /api/sector/list?kind=sw|concept|style|region|tdx_research&date="""
    mk = _market()
    return _market_err(lambda: mk.sector_list(q.get('date'),
                                              kind=(q.get('kind') or 'sw')))


def api_sector_members(q):
    """GET /api/sector/members?code=&kind=&date="""
    mk = _market()
    return _market_err(lambda: mk.sector_members(q.get('code') or '',
                                                 q.get('date'),
                                                 kind=(q.get('kind') or 'sw'),
                                                 limit=q.get('limit') or 300))


def api_sector_kinds(_q):
    """GET /api/sector/kinds —— 有哪几类板块（含每类的个数）。"""
    mk = _market()

    def _go():
        blk = mk.blocks()
        cnt = {}
        for _c, (_n, kind, _s) in blk.items():
            cnt[kind] = cnt.get(kind, 0) + 1
        out = [{'kind': 'sw', 'name': '申万一级', 'n': None,
                'note': '随面板每日同步，带 PIT'}]
        for k, nm in mk.BLOCK_TYPE.items():
            if cnt.get(k):
                out.append({'kind': k, 'name': nm, 'n': cnt[k],
                            'note': '通达信，每日同步'})
        return {'kinds': out}
    return _market_err(_go)


# ==================== 自选 ====================
def _watch():
    from assay import watchlist as w
    return w


def api_watchlist(q):
    """GET /api/watchlist?group= —— 当前自选 + 行情。"""
    w = _watch()
    try:
        return w.valued(q.get('group'))
    except Exception as e:                                  # noqa: BLE001
        return {'error': '%s: %s' % (type(e).__name__, e)}


def api_watchlist_log(_q):
    """GET /api/watchlist/log —— 全部历史（含已移出的）。append-only。"""
    w = _watch()
    try:
        return {'rows': list(reversed(w.log()))}
    except Exception as e:                                  # noqa: BLE001
        return {'error': '%s: %s' % (type(e).__name__, e)}


def api_watchlist_act(_q, body):
    """POST /api/watchlist —— 追加一条（add / remove / group / note）。

    ★ 归 --live 管：它写 live/ 下的账本。只读模式下不许改。
    """
    if not ALLOW_LIVE:
        return {'error': '服务以只读模式启动 —— 用 python3 serve.py --live 开启'}
    w = _watch()
    b = body or {}
    try:
        r = w.act((b.get('act') or '').strip(), b.get('code') or '',
                  group=b.get('group'), note=b.get('note') or '')
        return {'ok': True, 'rec': r, 'rows': w.valued().get('rows'),
                'groups': w.groups()}
    except w.WatchError as e:
        return {'error': str(e)}


# ★ 只读 SQL 页（/#/query）已取消 —— 命令行工具仍在
#   `datalake/build/query.py`（`--sql-stdin` / `--json` / `--schema`），
#   三层只读保证也都在那里。页面上那一层不再暴露。

# ==================== 财务数据（聚宽）导入 ====================
# 闭环三步：① 页面给出可直接粘进研究环境的代码（SINCE/QUARTERS 按本地状态
# 预填）② 在聚宽跑完下载那**一个** tar ③ 页面上传，服务端 merge + 跑全部
# loader。原来第 ③ 步要人对着打印出来的命令手抄 5~8 条，抄漏一条就是
# raw 与 std 不一致 —— 而那不会报错。
JQ_SCRIPT = os.path.join('raw', 'jq', '_ingest', 'extract_jq_increment.py')
# 上传体积上限。财务增量实测几十 MB；给到 512MB 是为了万一整批重抽。
JQ_UPLOAD_MAX = 512 * 1024 * 1024
JQ_NAME_RE = re.compile(r'^[A-Za-z0-9._-]{1,80}$')


def _jq_overlap_days():
    """SINCE 往前留几天重叠。

    ★ 留重叠是刻意的：合并按自然键去重，**重叠不会重复，而缺口会静默丢数据**。
      这条写在 extract 脚本的文件头，这里只是把它变成默认值。
    """
    return 5


def jq_suggest():
    """按本地当前状态算出该填的 SINCE / QUARTERS。

    ★ 让人手填这两个值是最容易出错的一处：SINCE 填晚了就是**静默丢数据**
      （中间那几天的公告永远补不回来，除非重抽）。本地各表的最新 pub_date
      服务端知道，直接算给它。
    """
    import subprocess
    dl = _datalake_dir()
    out = {'since': None, 'quarters': None, 'b_max': {}, 'note': None}
    try:
        r = subprocess.run(['python3', os.path.join(dl, 'build', 'sync_status.py'),
                            '--json'], cwd=dl, capture_output=True, text=True,
                           timeout=120)
        items = json.loads(r.stdout).get('items') or []
    except Exception as e:                                  # noqa: BLE001
        out['note'] = '读不到本地状态（%s），SINCE 请自己填' % type(e).__name__
        return out
    mx = {}
    for it in items:
        if it.get('leg') == 'B' and it.get('max'):
            mx[it['key']] = it['max']
    out['b_max'] = mx
    if not mx:
        out['note'] = '本地还没有财务数据 —— 这是首次导入，SINCE 请自己定'
        return out
    oldest = min(mx.values())
    d = datetime.strptime(oldest, '%Y-%m-%d').date() - \
        timedelta(days=_jq_overlap_days())
    out['since'] = d.isoformat()
    out['oldest'] = oldest
    # 报告期：当前季与上一季（重抽顺带捡回财报重述）
    today = date.today()
    q = (today.month - 1) // 3 + 1
    cur = (today.year, q)
    prev = (today.year, q - 1) if q > 1 else (today.year - 1, 4)
    out['quarters'] = ['%dq%d' % cur, '%dq%d' % prev]
    return out


def api_sync_jq_code(_q):
    """GET /api/sync/jq_code —— 给出可直接粘进聚宽研究环境的代码。

    ★ 正本是磁盘上那个脚本（datalake/raw/jq/_ingest/extract_jq_increment.py），
      这里只把「改这两处」的 SINCE / QUARTERS 按本地状态替换掉。
      **不在这里另写一份** —— 两份一定会分叉，而分叉的那份跑出来的数据
      看着正常。
    """
    dl = _datalake_dir()
    p = os.path.join(dl, JQ_SCRIPT)
    out = {'path': p, 'suggest': jq_suggest()}
    if not os.path.isfile(p):
        return dict(out, error='找不到抽取脚本：%s' % p)
    src = open(p, encoding='utf-8').read()
    sug = out['suggest']
    subs = []
    if sug.get('since'):
        src, n = re.subn(r"^SINCE = '[^']*'",
                         "SINCE = '%s'" % sug['since'], src, count=1,
                         flags=re.M)
        if n:
            subs.append('SINCE=%s' % sug['since'])
    if sug.get('quarters'):
        q = ', '.join("'%s'" % x for x in sug['quarters'])
        src, n = re.subn(r'^QUARTERS = \[[^\]]*\]',
                         'QUARTERS = [%s]' % q, src, count=1, flags=re.M)
        if n:
            subs.append('QUARTERS=[%s]' % q)
    # ★ 替换失败要说出来，不能默默给一份没改的：那会让人以为已经按本地状态
    #   填好了，而 SINCE 停在几个月前就是静默丢数据。
    out['substituted'] = subs
    out['code'] = src
    out['bytes'] = len(src.encode('utf-8'))
    if sug.get('since') and 'SINCE=%s' % sug['since'] not in subs:
        out['warn'] = ('没能自动替换 SINCE（脚本里那一行的格式变了？）——'
                       '粘过去之后请手动把 SINCE 改成 %s' % sug['since'])
    return out


def api_sync_jq_upload(headers, raw):
    """POST /api/sync/jq_upload —— 上传聚宽导出的 tar，随即 merge + 跑全部 loader。

    ★ 走【原始字节】而不是 JSON+base64：几十 MB 的 base64 要多传 33%，
      而且 json.loads 会把整包再复制一遍。
    ★ 文件名来自请求头，**只接受 [A-Za-z0-9._-]** 且只落到 _ingest/downloads
      —— 不能拿它直接 join（路径穿越）。
    """
    if not ALLOW_LIVE:
        return {'error': '服务以只读模式启动 —— 用 python3 serve.py --live 开启'}
    name = (headers.get('X-Filename') or '').strip()
    if not JQ_NAME_RE.match(name):
        return {'error': '文件名不合法：%r（只接受字母数字 . _ -）' % name}
    if not (name.endswith('.tar') or name.endswith('.tar.gz')
            or name.endswith('.tgz')):
        return {'error': '要上传聚宽脚本打包出来的 .tar（收到 %s）' % name}
    if not raw:
        return {'error': '上传内容是空的'}
    dl = _datalake_dir()
    dst_dir = os.path.join(dl, 'raw', 'jq', '_ingest', 'downloads')
    os.makedirs(dst_dir, exist_ok=True)
    # 同名不覆盖：加时间戳。上传的包是**原始凭据**，覆盖了就没法复查
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    dst = os.path.join(dst_dir, '%s__%s' % (stamp, name))
    tmp = dst + '.part'
    with open(tmp, 'wb') as fh:
        fh.write(raw)
    os.replace(tmp, dst)                       # 先写 .part 再 rename，同 _save_marks
    # 立刻验一下是不是真的 tar —— 坏包早失败，别等 merge 跑一半
    import tarfile
    try:
        with tarfile.open(dst) as t:
            members = [m.name for m in t.getmembers()]
    except Exception as e:                                  # noqa: BLE001
        os.remove(dst)
        return {'error': '不是可读的 tar（%s: %s）—— 已删除' % (type(e).__name__, e)}
    if not members:
        os.remove(dst)
        return {'error': 'tar 是空的 —— 已删除。聚宽那边可能一张表都没抽到'}
    merge = os.path.join(dl, 'build', 'merge_jq_increment.py')
    if not os.path.isfile(merge):
        return {'error': '找不到 %s' % merge}
    cmd = ['python3', merge, dst, '--and-load']
    job_id = 'jq-%s' % datetime.now().strftime('%H%M%S')
    _JOBS[job_id] = {'state': 'running', 'lines': [], 'cmd': cmd,
                     'sha': None, 'run_id': None, 'rc': None}
    threading.Thread(target=_run_job, args=(job_id, cmd, dl),
                     daemon=True).start()
    return {'job_id': job_id, 'saved': dst, 'bytes': len(raw),
            'members': members, 'cmd': ' '.join(cmd)}


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
    '/api/live/strategy': api_live_strategy,
    '/api/live/equity': api_live_equity,
    '/api/live/fills': api_live_fills,
    '/api/sync': api_sync,
    '/api/sync/auto': api_sync_auto,
    '/api/sync/jq_code': api_sync_jq_code,
    '/api/stock/search': api_stock_search,
    '/api/stock/profile': api_stock_profile,
    '/api/stock/kline': api_stock_kline,
    '/api/stock/finance': api_stock_finance,
    '/api/rt/status': api_rt_status,
    '/api/rt/bars': api_rt_bars,
    '/api/stock/indicators': api_stock_indicators,
    '/api/stock/events': api_stock_events,
    '/api/stock/peers': api_stock_peers,
    '/api/stock/links': api_stock_links,
    '/api/stock/sectors': api_stock_sectors,
    '/api/compare': api_compare,
    '/api/market/overview': api_market_overview,
    '/api/sector/kinds': api_sector_kinds,
    '/api/sector/list': api_sector_list,
    '/api/sector/members': api_sector_members,
    '/api/watchlist': api_watchlist,
    '/api/watchlist/log': api_watchlist_log,
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
                 '/api/live/cash': api_live_cash,
                 '/api/live/fee_infer': api_live_fee_infer,
                 '/api/live/fee_rate': api_live_fee_add,
                 '/api/sync/auto': api_sync_auto_set,
                 '/api/watchlist': api_watchlist_act,
                 '/api/rt/poll': api_rt_poll,
                 '/api/live/backtest': api_live_backtest,
                 '/api/sync/run': api_sync_run}
        # ★ 上传走【原始字节】分支：几十 MB 的包不该先变成 base64 再
        #   json.loads（多传 33% + 整包再复制一遍）。所以它不能和下面的
        #   JSON 解析共用一条路。
        if u.path == '/api/sync/jq_upload':
            try:
                n = int(self.headers.get('Content-Length') or 0)
                if n <= 0:
                    return self._send(400, json.dumps(
                        {'error': '没有上传内容'}, ensure_ascii=False))
                if n > JQ_UPLOAD_MAX:
                    return self._send(413, json.dumps(
                        {'error': '文件太大（%.0f MB > 上限 %.0f MB）'
                                  % (n / 1e6, JQ_UPLOAD_MAX / 1e6)},
                        ensure_ascii=False))
                raw = self.rfile.read(n)
                r = api_sync_jq_upload(self.headers, raw)
            except Exception as e:                          # noqa: BLE001
                import traceback
                traceback.print_exc()
                return self._send(500, json.dumps(
                    {'error': '%s: %s' % (type(e).__name__, e)},
                    ensure_ascii=False))
            return self._send(200, json.dumps(r, ensure_ascii=False, default=str))
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
        # 盘中 1 分钟线：只在 server 开着时跑，只抓持仓，只在交易时段
        rt = _rt()
        _RT['thread'] = threading.Thread(target=_rt_loop, daemon=True)
        _RT['thread'].start()
        print('盘中 1 分钟线：开启  时段 %s  存 %s'
              % (' / '.join('%s~%s' % (a.strftime('%H:%M'), b.strftime('%H:%M'))
                            for a, b in rt.SESSIONS),
                 os.path.join(_datalake_dir(), 'rt')))
    else:
        print('实盘模块：关闭（--live 开启）')
        print('盘中 1 分钟线：关闭（跟随 --live）')
    print('Ctrl-C 退出', flush=True)
    ThreadingHTTPServer((host, port), Handler).serve_forever()
