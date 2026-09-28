# -*- coding: utf-8 -*-
"""用户与会话 —— 这个系统里唯一一处"谁在用"的正本。

用户 2026-09-28：「做个简单的用户认证……只是个人使用的小项目，无需做的
特别严密，**用户更多是为了方便区分收藏、账户等信息**。」

★ 所以这一层的目的是**数据隔离**，不是防攻击。它要回答的是
  "这份自选/这个账户是谁的"，而不是"怎么挡住坏人"。

🔴🔴 **密码是明文存的** —— 用户要求「管理员可以查看所有用户的用户密码」，
  那就只能明文（或可逆），哈希做不到。代价说清楚，别装作没有：

      · 谁能读到 `live/_users.json` 谁就知道所有人的密码
      · 所以**不要把这个服务暴露到公网**，也不要拿这里的密码去别处用
      · 真要多人/联网用，第一件事就是把它换成哈希 + 去掉"看密码"那个功能

  （同「ETF 无印花税是事实不是偏好」的反面：这是**偏好不是事实**，
    是明知的取舍，写在这里免得下次有人以为是漏掉了。）

🔴 **用户表与会话不随用户重定向** —— 它们是公共的（要先知道你是谁，
  才谈得上把数据根指到哪）。所以走自己的 `LIVE`，不走 `lv.LIVE`。
"""

import io
import json
import os
import re
import secrets
import shutil
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIVE = os.path.join(ROOT, 'live')

# 🔴 名字要能当**目录名**用 —— 它会拼进 `live/u/<name>/`。
#   不校验的话 `../` 就能把数据根指到仓库外面（这是本模块唯一一处
#   真正与安全有关的地方，所以判据写死、不给绕）。
NAME_RE = re.compile(r'^[A-Za-z0-9_-]{1,32}$')

COOKIE = 'assay_u'


def users_file():
    return os.path.join(LIVE, '_users.json')


def sess_file():
    return os.path.join(LIVE, '_sessions.json')


def _load(path, dft):
    try:
        with io.open(path, encoding='utf-8') as f:
            return json.load(f)
    except Exception:                                       # noqa: BLE001
        return dft


def _save(path, obj):
    """原子写 —— 与项目里别处同一套（先 tmp 再 replace）。"""
    d = os.path.dirname(path)
    if d and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
    tmp = '%s.%d.tmp' % (path, os.getpid())
    with io.open(tmp, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def load_users():
    return _load(users_file(), [])


def user_root(name):
    """这个用户的私有数据根 —— `live/u/<name>/`。

    🔴 **一定要先过 `NAME_RE`**：名字直接拼进路径，`..` 能穿越出去。
    """
    if not NAME_RE.match(name or ''):
        raise ValueError('用户名只能是字母/数字/下划线/连字符，1~32 位：%r'
                         % (name,))
    return os.path.join(LIVE, 'u', name)


def find(name):
    for u in load_users():
        if u.get('name') == name:
            return u
    return None


def add_user(name, pw, admin=False):
    """建一个用户。**第一个自动是管理员** —— 否则没人能管别人。"""
    if not NAME_RE.match(name or ''):
        raise ValueError('用户名只能是字母/数字/下划线/连字符，1~32 位')
    if not (pw or '').strip():
        raise ValueError('密码不能为空')
    us = load_users()
    if any(u.get('name') == name for u in us):
        raise ValueError('用户名 %r 已经有了' % name)
    u = {'name': name, 'pw': pw, 'admin': bool(admin) or not us,
         'created': time.strftime('%Y-%m-%d %H:%M:%S')}
    us.append(u)
    _save(users_file(), us)
    os.makedirs(user_root(name), exist_ok=True)
    return u


def set_pw(name, pw):
    if not (pw or '').strip():
        raise ValueError('密码不能为空')
    us = load_users()
    for u in us:
        if u.get('name') == name:
            u['pw'] = pw
            _save(users_file(), us)
            return u
    raise ValueError('没有这个用户：%r' % name)


def check(name, pw):
    u = find(name)
    # ⚠ 明文比对（见模块头那条取舍）。不做 constant-time —— 本地自用，
    #   而且能读到这个进程的人早就能直接读 `_users.json` 了。
    return u if (u and u.get('pw') == pw) else None


def login(name, pw):
    """验证通过就发一个 token，并**落盘** —— serve.py 改代码就要重启，
    token 只放内存的话每次重启都得重登（同「刚跑完的 90 秒仍显示」那条：
    别让人为了工具的实现细节付代价）。
    """
    u = check(name, pw)
    if not u:
        return None
    tok = secrets.token_urlsafe(24)
    s = _load(sess_file(), {})
    s[tok] = {'name': name, 'at': time.time()}
    _save(sess_file(), s)
    return tok


def whoami(tok):
    if not tok:
        return None
    ent = (_load(sess_file(), {}) or {}).get(tok)
    return find(ent['name']) if ent else None


def logout(tok):
    s = _load(sess_file(), {})
    if tok in s:
        s.pop(tok, None)
        _save(sess_file(), s)


def cookie_token(header):
    """从 Cookie 头里抠出 token —— 不引 http.cookies，一行正则够了。"""
    m = re.search(r'(?:^|;\s*)%s=([^;]+)' % COOKIE, header or '')
    return m.group(1) if m else None


# ---------------------------------------------------------------- 迁移
LEGACY = ('accounts.json', 'watchlist.jsonl', 'alerts.jsonl',
          'alerts_fired.jsonl')


def legacy_items():
    """老布局里【属于某个人】的那些东西 —— 账户目录 + 四个账本 + picks。

    ⚠ `trade_calendar.json` **不在此列**：它是交易日历，全站一份
      （它一直混在 `live/` 里，但它不是谁的数据）。
    """
    out = []
    for n in LEGACY:
        p = os.path.join(LIVE, n)
        if os.path.exists(p):
            out.append(p)
    # 账户目录：`live/` 下除了 u/ 与那几个公共文件之外的目录
    for n in sorted(os.listdir(LIVE)) if os.path.isdir(LIVE) else []:
        p = os.path.join(LIVE, n)
        if os.path.isdir(p) and n != 'u' and not n.startswith('_'):
            out.append(p)
    pk = os.path.join(ROOT, 'picks.json')
    if os.path.isfile(pk):
        out.append(pk)
    return out


def migrate_legacy(name, dry=False):
    """把老布局里的私有数据搬进 `live/u/<name>/`。

    🔴 **搬不是拷** —— 拷的话老位置还留着一份，而"页面上改的是哪一份"
      从此说不清（同「两处实现必然分叉」）。
    🔴 **一个都不覆盖**：目标已经有同名的就整个中止并说清楚 ——
      宁可什么都不做，也不要合出一个说不清来历的账本。
    """
    dst = user_root(name)
    items = legacy_items()
    plan = []
    for src in items:
        base = os.path.basename(src)
        # picks.json 在仓库根，搬进用户目录之后仍叫这个名字
        plan.append((src, os.path.join(dst, base)))
    clash = [d for _, d in plan if os.path.exists(d)]
    if clash:
        raise RuntimeError('目标已经有这些了，怕盖掉就没动：%s'
                           % '、'.join(os.path.basename(x) for x in clash))
    if dry:
        return plan
    os.makedirs(dst, exist_ok=True)
    for src, d in plan:
        shutil.move(src, d)
    return plan
