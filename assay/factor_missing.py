# -*- coding: utf-8 -*-
"""手工往「算不出来的因子」清单里加的那些 —— append-only 账本。

    live/factor_missing.jsonl    {ts, act:'add'|'remove', name, reason, detail}

## 🔴 与注册表那份是【两个来源，一份清单】

    注册表 datalake/build/factors/MISSING   照 factors.xlsx 逐条定案的 117 个
                                            —— 代码里的正本，页面删不掉
    这本账  live/factor_missing.jsonl        人自己想到的：原清单里没有、
                                            或者原清单有但当时没归进去的

两边在 `srv/factors.py` 里**合成一份**给页面，每条带 `source` 说清自己
是哪来的。不说的话人会去页面上删一条注册表里的，然后发现删不掉
（同「给一个点了没反应的按钮比不给更糟」）。

🔴 **注册表那份的覆盖自证不受这本账影响**：`covered_by` 只拿注册表两半
去对 `factors.xlsx`。手工加的多半**不在**原清单里（那正是加它的理由），
混进去的话那条自证会天天报"MISSING 里有而原清单没有"，
而**天天报的告警等于没有告警**。

## 🔴 append-only，当前清单由【重放】得出

同 `alerts.jsonl` / `watchlist.jsonl` 那套：改一行追加一条 `add`（整行覆盖
语义），删一行追加 `remove`。不单独存一份"当前清单" —— 存两份就会分叉，
而"页面显示的和日志对不上"没法复盘。
"""
import datetime
import json
import os
import re

LIVE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    'live')
FILE = 'factor_missing.jsonl'
#: 名字上限 —— 它要进表格的一列，太长会把"为什么"那列挤出屏幕
MAX_NAME = 40
MAX_DETAIL = 300


class MissError(Exception):
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
            if not ln:
                continue
            try:
                out.append(json.loads(ln))
            except Exception:                               # noqa: BLE001
                pass          # 坏行跳过，不让一行坏 JSON 废掉整张表
    return out


def _append(rec):
    os.makedirs(LIVE, exist_ok=True)
    with open(_path(), 'a', encoding='utf-8') as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + '\n')
    return rec


def log():
    return _read()


def current():
    """重放出当前这本账。顺序按**第一次加进来**的时间 —— 改一次说明就把
    那行跳到最后的话，人每次都要重新找刚改的那条（同 alerts.current）。"""
    rows, order = {}, []
    for r in _read():
        n = (r.get('name') or '').strip()
        if not n:
            continue
        if r.get('act') == 'remove':
            rows.pop(n, None)
            continue
        if r.get('act') != 'add':
            continue
        if n not in rows:
            order.append(n)
        rows[n] = {'name_cn': n, 'reason_key': r.get('reason') or 'todo',
                   'detail': r.get('detail') or '', 'source': 'manual',
                   'ts': r.get('ts'),
                   'added': rows.get(n, {}).get('added') or r.get('ts')}
    return [rows[n] for n in order if n in rows]


def _clean(s, limit, what):
    s = re.sub(r'\s+', ' ', (s or '')).strip()
    if len(s) > limit:
        raise MissError('%s 太长了（%d 字，上限 %d）' % (what, len(s), limit))
    # 🔴 这些字会进页面的 title 属性，而属性里连 <b> 都用不了 ——
    #   markdown 星号会原样显示成一串星号（本项目为此踩过四次）。
    #   在**入口**直接拒，不是写在文档里提醒。
    if '**' in s:
        raise MissError('%s 里不要用 markdown 星号（页面上会原样显示），用【】' % what)
    return s


def add(name, reason, detail='', known=(), implemented=()):
    """加一条。`known` / `implemented` 由调用方给 —— 撞名要**说清撞在哪一半**。

    🔴 撞名不能静默覆盖：
      - 已经实现了 -> 它在广场上有值，加进"算不出来"是自相矛盾
      - 注册表里已经有 -> 那条带着逐条定案的原因，手工这条会把它盖住
    两种都直接拒并指路，而不是"加上去了但看不出是哪一条"。
    """
    name = _clean(name, MAX_NAME, '因子名')
    detail = _clean(detail, MAX_DETAIL, '说明')
    if not name:
        raise MissError('因子名不能为空')
    if not reason:
        raise MissError('要选一个原因 —— 只有名字的话，这份清单等于没说为什么')
    if name in set(implemented):
        raise MissError('「%s」已经算得出来了 —— 它就在因子广场那张表里，'
                        '不该列进"算不出来"' % name)
    if name in set(known):
        raise MissError('「%s」已经在注册表那份清单里了（带着逐条定案的原因）'
                        '—— 不用再加一条' % name)
    return _append({'ts': datetime.datetime.now().isoformat(timespec='seconds'),
                    'act': 'add', 'name': name,
                    'reason': reason, 'detail': detail})


def remove(name):
    """删一条 —— 只删得掉**这本账**里的。注册表那份是代码，页面删不动。"""
    name = (name or '').strip()
    if not any(r['name_cn'] == name for r in current()):
        raise MissError('「%s」不在手工清单里 —— 注册表里那些是代码，'
                        '要它消失只有去 datalake/build/factors/ 写一条 Spec' % name)
    return _append({'ts': datetime.datetime.now().isoformat(timespec='seconds'),
                    'act': 'remove', 'name': name})
