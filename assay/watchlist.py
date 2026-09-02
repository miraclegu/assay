"""自选股。**append-only**，与实盘账本同一套纪律。

## 为什么是 append-only

自选池的变化本身是决策记录 —— "我什么时候把它加进来的、为什么"。
删一条就把这个信息抹掉了。所以：

- 加 / 移出 / 改分组 / 改备注 都是**追加一条**
- 当前状态由**重放**得出（`current()`），不单独存
- 与 `live/fills.jsonl` 同一个模式（见 live.py 的 `_write_jsonl` docstring）

## 存哪

`live/watchlist.jsonl` —— 跟 `live/` 一起入版本控制。
它是决策证据，不能放 `runs/`（gitignore 的产物目录）。
"""
import datetime
import json
import os
import uuid

LIVE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    'live')
FILE = 'watchlist.jsonl'
ACTS = ('add', 'remove', 'group', 'note')
DEFAULT_GROUP = '默认'


class WatchError(Exception):
    pass


def _path():
    return os.path.join(LIVE, FILE)


def _read():
    p = _path()
    if not os.path.isfile(p):
        return []
    out = []
    with open(p, encoding='utf-8') as fh:
        for ln in fh:
            ln = ln.strip()
            if ln:
                try:
                    out.append(json.loads(ln))
                except Exception:                           # noqa: BLE001
                    pass          # 坏行跳过，不让一行坏 JSON 废掉整个自选池
    return out


def _append(rec):
    os.makedirs(LIVE, exist_ok=True)
    p = _path()
    # 先写临时再 rename 不适用于 append —— 这里用 'a' + flush，
    # 单进程追加一行是原子的（< PIPE_BUF）
    with open(p, 'a', encoding='utf-8') as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + '\n')
    return rec


def log():
    """全部历史（含已移出的）—— 页面上"什么时候加的、为什么"要看它。"""
    return _read()


def current(group=None):
    """重放出当前自选池。

    ★ 由日志重放而不是单独存一份 —— 存两份就会分叉，
      而"页面显示的和日志对不上"没法复盘。
    """
    st = {}
    for r in _read():
        c = r.get('code')
        if not c:
            continue
        a = r.get('act')
        if a == 'add':
            st.setdefault(c, {'code': c, 'group': DEFAULT_GROUP, 'note': '',
                              'added': r.get('ts')})
            st[c]['removed'] = None
            st[c]['added'] = r.get('ts')
            if r.get('group'):
                st[c]['group'] = r['group']
            if r.get('note'):
                st[c]['note'] = r['note']
        elif a == 'remove':
            st.pop(c, None)
        elif a == 'group' and c in st:
            st[c]['group'] = r.get('group') or DEFAULT_GROUP
        elif a == 'note' and c in st:
            st[c]['note'] = r.get('note') or ''
    out = list(st.values())
    if group:
        out = [x for x in out if x['group'] == group]
    out.sort(key=lambda x: (x['group'], x['added'] or ''))
    return out


def groups():
    gs = {}
    for x in current():
        gs[x['group']] = gs.get(x['group'], 0) + 1
    return [{'name': k, 'n': v} for k, v in sorted(gs.items())]


def act(action, code, group=None, note=''):
    """追加一条。`action` ∈ add / remove / group / note。"""
    from assay import stock as st
    if action not in ACTS:
        raise WatchError('action 只能是 %s，收到 %r' % ('/'.join(ACTS), action))
    jc = st.norm_code(code)
    if not jc:
        raise WatchError('认不出代码：%r' % code)
    rec = {'uid': uuid.uuid4().hex[:12],
           'ts': datetime.datetime.now().replace(microsecond=0).isoformat(),
           'act': action, 'code': jc}
    if group:
        rec['group'] = str(group)[:24]
    if note:
        rec['note'] = str(note)[:200]
    if action == 'add':
        rec.setdefault('group', DEFAULT_GROUP)
        if any(x['code'] == jc for x in current()):
            # 已在池子里：不报错（页面上那个星是幂等的），但也不重复追加
            return {'skipped': True, 'code': jc, 'reason': '已在自选里'}
    if action in ('remove', 'group', 'note'):
        if not any(x['code'] == jc for x in current()):
            raise WatchError('%s 不在自选里' % jc)
    return _append(rec)


def valued(group=None, root=None):
    """当前自选 + 最新一天行情。自选页主视图用的就是它。"""
    from assay import stock as stk
    rows = current(group)
    if not rows:
        return {'rows': [], 'groups': groups(), 'date': None}
    c = stk.con()
    p = stk.panel(root)
    d = c.execute('SELECT max(date) FROM %s' % p).fetchone()[0]
    q = "','".join(x['code'] for x in rows)
    px = {r[0]: r for r in c.execute("""
        SELECT jq_code, sec_name, close_bfq, change_pct, turnover, amount,
               floatmv, pe_ttm, pb, roe_ttm, sw_l1_name, is_limit_up,
               is_limit_down, is_st
        FROM %s WHERE date = DATE '%s' AND jq_code IN ('%s')"""
        % (p, d, q)).fetchall()}
    out = []
    for x in rows:
        r = px.get(x['code'])
        it = dict(x)
        if r:
            it.update({'name': r[1], 'close': r[2], 'change_pct': r[3],
                       'turnover': r[4], 'amount': r[5], 'floatmv': r[6],
                       'pe_ttm': r[7], 'pb': r[8], 'roe_ttm': r[9],
                       'industry': r[10], 'limit_up': bool(r[11]),
                       'limit_down': bool(r[12]), 'is_st': bool(r[13])})
        else:
            # ★ 面板里没有 -> 退市/未上市。**不静默丢掉** ——
            #   自选里那一行必须还在，并标出来，否则"我加过的票不见了"。
            it.update({'name': '', 'missing': True})
        out.append(it)
    return {'rows': out, 'groups': groups(), 'date': str(d)}
