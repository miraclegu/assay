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
    """当前自选 + 最新一天行情 + **盘中实时价**。自选页主视图用的就是它。

    ★ 实时价读的是 `datalake/rt/` 那个**共享库**，不自己去打接口 ——
      抓取由 server 的 `_rt_loop` 统一做（范围 = 持仓 ∪ 自选，去重）。
      页面各自去抓的话，同一只票会被抓好几遍，而限流是这条链上唯一的风险。
      （思路同 table-data-viewer 的「全局共享行情缓存，跨所有页签复用」。）
    """
    from assay import stock as stk
    rows = current(group)
    if not rows:
        return {'rows': [], 'groups': groups(), 'date': None,
                'price_src': '收盘', 'rt_n': 0, 'rt_at': None}
    c = stk.con()
    p = stk.panel(root)
    d = c.execute('SELECT max(date) FROM %s' % p).fetchone()[0]
    q = "','".join(x['code'] for x in rows)
    px = {r[0]: r for r in c.execute("""
        SELECT jq_code, sec_name, close_bfq, change_pct, turnover, amount,
               floatmv, pe_ttm, pb, roe_ttm, sw_l1_name, is_limit_up,
               is_limit_down, is_st, preclose
        FROM %s WHERE date = DATE '%s' AND jq_code IN ('%s')"""
        % (p, d, q)).fetchall()}
    rt = {}
    try:
        from assay import realtime as _rt
        rt = _rt.latest([x['code'] for x in rows], root=root)
    except Exception:                                       # noqa: BLE001
        rt = {}
    out, n_rt, at = [], 0, None
    for x in rows:
        r = px.get(x['code'])
        it = dict(x)
        if r:
            it.update({'name': r[1], 'close': r[2], 'change_pct': r[3],
                       'turnover': r[4], 'amount': r[5], 'floatmv': r[6],
                       'pe_ttm': r[7], 'pb': r[8], 'roe_ttm': r[9],
                       'industry': r[10], 'limit_up': bool(r[11]),
                       'limit_down': bool(r[12]), 'is_st': bool(r[13]),
                       'preclose': r[14]})
        else:
            # ★ 面板里没有 -> 退市/未上市。**不静默丢掉** ——
            #   自选里那一行必须还在，并标出来，否则"我加过的票不见了"。
            it.update({'name': '', 'missing': True})
        q2 = rt.get(x['code'])
        if q2 and q2.get('price'):
            # 涨跌幅按【实时价 / 昨收】重算 —— 面板里那个是收盘的，
            # 盘中拿它配实时价会前后矛盾（价变了、涨跌幅没变）。
            #
            # 🔴 「昨收」不能用面板的 `preclose` —— 那是**面板那一天**的昨收
            #   （面板到 09-02，它的 preclose 是 09-01 收盘），而实时价是
            #   09-03 的，基准差一天。实测被断言抓到：−0.47% vs 应为 +0.23%。
            #   正确基准：快照自带的 preclose（实时数据，最准）；
            #   退一步用**面板最新那天的收盘**（09-02 收盘 = 09-03 的昨收）。
            pc = q2.get('preclose') or (r[2] if r else None)
            it['close'] = q2['price']
            it['preclose'] = pc          # 让页面/断言用的是同一个基准
            if pc:
                it['change_pct'] = round((q2['price'] / pc - 1) * 100, 2)
            it['rt_src'] = q2.get('src')
            it['rt_at'] = q2.get('at')
            n_rt += 1
            if q2.get('at') and (at is None or q2['at'] > at):
                at = q2['at']
        out.append(it)
    return {'rows': out, 'groups': groups(), 'date': str(d),
            'rt_n': n_rt, 'rt_at': at,
            'price_src': ('实时' if n_rt == len(out) and n_rt
                          else ('部分实时' if n_rt else '收盘'))}


# ==================== 实盘持仓自动进自选 ====================
# ★ 为什么要自动：持仓本来就是最需要盯的那些票，手工再加一遍是重复劳动，
#   而且漏加一只就少盯一只（不报错）。
# ★ 分组名 = 账户名 —— 按账户分开，一眼看出"这些是哪个账户在持的"。
AUTO_PREFIX = '实盘·'


def auto_group(account_name):
    return AUTO_PREFIX + str(account_name or '').strip()


def is_auto_group(name):
    return str(name or '').startswith(AUTO_PREFIX)


def sync_live(root=None):
    """把各实盘账户的当前持仓同步进自选。返回做了什么。

    🔴 **只加不自动移。** 卖光了就把那条标成"已清仓"，但**留在自选里** ——
      自动移出会把手工写的备注一起抹掉，而 append-only 的账本里删不掉的是
      记录、丢掉的是上下文。要清就自己点「移出」。

    ★ 幂等：已在自选里的不重复追加（`act('add')` 自己会 skip），
      但**分组会纠正** —— 一只票从 A 账户换到 B 账户时，分组要跟着走。

    ★ 一只票同时在两个账户里：分组归**先遍历到的那个**，并在备注里标出
      另一个账户。不复制成两条 —— 自选是"我要盯哪些票"，同一只票盯一次。
    """
    from assay import live as lv
    cur = {x['code']: x for x in current()}
    holders = {}                      # code -> [账户名, ...]
    for a in lv.load_accounts():
        if a.get('archived'):
            continue
        nm = a.get('name') or a['id']
        try:
            pos = lv.positions(a['id'])
        except Exception:                                   # noqa: BLE001
            continue
        for c in pos:
            holders.setdefault(c, []).append(nm)
    out = {'added': [], 'regrouped': [], 'cleared': [], 'kept': 0}
    for c, names in sorted(holders.items()):
        want = auto_group(names[0])
        note = ('，另在 ' + '、'.join(names[1:])) if len(names) > 1 else ''
        note = ('持仓自动加入' + note)
        if c not in cur:
            act('add', c, group=want, note=note)
            out['added'].append({'code': c, 'group': want})
        elif cur[c]['group'] != want and is_auto_group(cur[c]['group']):
            # ★ 只纠正【自动组之间】的漂移。用户手工分到别的组的不动 ——
            #   那是他的选择，自动同步不该覆盖。
            act('group', c, group=want)
            out['regrouped'].append({'code': c, 'from': cur[c]['group'],
                                     'to': want})
        else:
            out['kept'] += 1
    # 自动组里已经不在任何持仓里的 -> 标"已清仓"，不移出
    for c, x in cur.items():
        if is_auto_group(x['group']) and c not in holders:
            if '已清仓' not in (x.get('note') or ''):
                act('note', c, note='已清仓（曾在%s）' % x['group'])
                out['cleared'].append({'code': c, 'group': x['group']})
    return out
