# -*- coding: utf-8 -*-
"""谁在用 —— 路由分私有/公共，私有的把四个数据根指到那个人身上。

用户 2026-09-28 定的三件事：
  ① 回测归档算**公共**（它是可复现的产物；"哪几次值得记住"才是私有的）
  ② 未登录**只拦私有** —— 行情/因子/数据字典本来就是公开信息
  ③ **一个人用** -> 请求级锁串行化私有请求（见 `as_user` 那条 ⚠）

🔴🔴 **默认私有，公共要显式登记。** 两种漏标的代价完全不对称：

    漏标公共 -> 那条路由要登录     **响亮**（当场看得见，一秒修好）
    漏标私有 -> 两个人的数据串了   **静默**（而且可能几天后才发现）

  所以判据写成"不在公共清单里就是私有"，新加的路由自动受保护
  （同「照清单拼会漏掉新文件的全部组合」—— 这里把默认方向选在安全那侧）。
"""

import os
import threading
from contextlib import contextmanager

from .. import users

# ---- 公共：整段前缀 ------------------------------------------------
#   行情与研究数据（个股/盘面/板块/因子/指标定义）、数据字典、
#   数据同步与装配（那是机器的状态，不是谁的数据）、认证本身。
PUBLIC_PREFIX = (
    '/api/stock/', '/api/market/', '/api/sector/', '/api/factor',
    '/api/indicators/', '/api/doc', '/api/sync', '/api/setup',
    '/api/auth/',
)

# ---- 公共：逐条登记 ------------------------------------------------
#   回测归档那一族（用户定：归档公共）+ 机器状态。
PUBLIC_EXACT = frozenset((
    '/api/runs', '/api/run', '/api/run/trades_of', '/api/equity',
    '/api/holdings', '/api/day', '/api/rejects', '/api/code', '/api/log',
    '/api/job', '/api/compare', '/api/backtest', '/api/runs/delete',
    '/api/progress', '/api/datafp',
    # 指数带子 / 行情库状态：挂在每个页面上，且与持仓无关
    '/api/rt/indices', '/api/rt/status', '/api/rt/bars',
))

# ---- 前缀是公共、内容却是私有的那几条 --------------------------------
# 🔴 `/api/stock/links` 在**个股页**（公共页面）上，但它答的是
#   「我持有多少 / 哪几次回测买过它」—— 那是我的持仓。
#   所以它从 `/api/stock/` 这个公共前缀里**挖出来**，页面侧未登录时
#   那一块要优雅降级（说一句"登录后可见"），而不是整页报错。
PRIVATE_EXCEPT = frozenset(('/api/stock/links',))


_EN = {'at': 0, 'v': False}


def enabled():
    """启用多用户了吗 —— 判据是**有没有建过账号**，不是配置开关。

    🔴🔴 **一个账号都没有 = 一切照旧**（不拦、数据仍在 `live/` 老位置）。
      理由有三条，每条单独都成立：

        ① 向后兼容：加了认证就把现有功能全锁上，等于"升级一下，系统
           不能用了" —— 而用户要的是**区分数据**，不是上锁
        ② 迁移因此是**可选的**：不建账号就永远是现在这样，
           建第一个账号那一刻才搬（同「硬拒必须配一个逃生口」）
        ③ selftest 与 launchd 那条链不用动 —— 它们本来就没有"谁在用"
           这个概念（同「命令行是产品契约」：别为了新功能改掉旧入口）

    ★ 判据是**现在磁盘上有没有用户**，不是记一个 `enabled: true`
      —— 后者会在"用户表被删/换了台机器"时说谎（同 launchd 那条）。
    ⚠ 按 mtime 缓存：每个请求都读一次 json 太浪费，而这个文件极少变。
    """
    f = users.users_file()
    try:
        m = os.path.getmtime(f)
    except OSError:
        _EN.update(at=0, v=False)
        return False
    if m != _EN['at']:
        _EN.update(at=m, v=bool(users.load_users()))
    return _EN['v']


def is_public(path):
    if path in PRIVATE_EXCEPT:
        return False
    if path in PUBLIC_EXACT:
        return True
    return path.startswith(PUBLIC_PREFIX)


# ---------------------------------------------------------------- 隔离
_LOCK = threading.RLock()


def _targets():
    """四个私有数据根 —— 延迟 import（避免与 srv.base 循环）。

    ★ 它们**本来就是可重定向的模块属性**（selftest 一直靠这个不写生产
      数据）—— 所以这一层几乎是白捡的：不用改任何路径拼接。
    🔴 `lv` 是 ModuleType 子类门面，`lv.LIVE = x` 会**转发**到
      `lv.base.LIVE`；而 `watchlist` / `alerts` 是普通模块。
      `MARKS_FILE` 要改 `srv.runs` 那份 —— runs.py 是
      `from .base import MARKS_FILE`（**值副本**），改 base 那份没用。
    """
    from assay import alerts as _al
    from assay import live as _lv
    from assay import watchlist as _wl
    from . import runs as _rn
    return (
        (_lv, 'LIVE', lambda root: root),
        (_wl, 'LIVE', lambda root: root),
        (_al, 'LIVE', lambda root: root),
        (_rn, 'MARKS_FILE', lambda root: os.path.join(root, 'picks.json')),
    )


@contextmanager
def as_user(name):
    """把四个数据根指到这个用户，出去时**一定**还原。

    ⚠ **这把锁是"一个人用"这个前提的实现。** 那四个根是**模块级全局**，
      而 serve.py 是多线程 —— 两个不同的用户同时发请求会互相踩
      （A 的请求改了全局根，B 读到 A 的数据，**而它不报错**）。
      锁把私有请求串行化，于是"踩"变成"排队"。
    ★ 用户 2026-09-28 明确选了「基本不会，我一个人用」。真要多人同时用，
      正确的解法是把这四个根改成 thread-local，而不是把锁做得更细
      —— 记在这里，别下次当成漏掉了。
    """
    root = users.user_root(name)
    if not os.path.isdir(root):
        os.makedirs(root, exist_ok=True)
    with _LOCK:
        olds = []
        try:
            for mod, attr, fn in _targets():
                olds.append((mod, attr, getattr(mod, attr)))
                setattr(mod, attr, fn(root))
            yield root
        finally:
            for mod, attr, old in olds:
                setattr(mod, attr, old)


# ---------------------------------------------------------------- 接口
def _pub(u):
    return {'name': u['name'], 'admin': bool(u.get('admin')),
            'created': u.get('created')}


def api_me(q, _who=None):
    """我是谁 + 要不要先建第一个账号。

    ★ `need_signup` 让登录页**自己**变成"创建第一个账号" —— 新机器上
      没有用户表时，摆一个登不进去的登录框等于死路（同 backLink 那条）。
    """
    return {'user': _pub(_who) if _who else None,
            'need_signup': not users.load_users(),
            # 🔴 前端拿它决定**要不要渲染用户区** —— 没启用多用户时顶栏
            #   多一个"登录 ›"是纯噪声（同「没任务时整条不渲染」），
            #   而且它改变了顶栏布局：实测一排按钮被挤得换行，
            #   web 用例大批 `Locator.click` 超时。
            'enabled': enabled()}


def api_login(_q, body):
    name = (body or {}).get('name') or ''
    tok = users.login(name, (body or {}).get('pw') or '')
    if not tok:
        # ★ 不分"没这个人"与"密码错" —— 但理由**不是**安全（本地自用），
        #   是那两句话对使用者没有区别，多一句反而要多读一次。
        return {'error': '用户名或密码不对'}
    return {'ok': 1, 'token': tok, 'user': _pub(users.find(name))}


def api_logout(_q, body):
    users.logout((body or {}).get('token') or '')
    return {'ok': 1}


def api_signup(_q, body):
    """建第一个账号 —— **只在一个用户都没有时**开放。

    🔴 之后再想加人走管理员那条路；这里不设防的话，任何人打一次这个
      接口就能给自己开一个号（而本地自用的前提是"这台机器只有我"，
      不是"这个接口没人知道"）。
    ★ 建完**顺手把老数据迁过去** —— 否则第一次登录进去是空的，
      人会以为账户全没了（而它不报错）。
    """
    if users.load_users():
        return {'error': '已经有用户了 —— 加人请用管理员账号'}
    name = (body or {}).get('name') or ''
    pw = (body or {}).get('pw') or ''
    try:
        u = users.add_user(name, pw, admin=True)
        moved = users.migrate_legacy(name)
    except Exception as e:                                  # noqa: BLE001
        return {'error': str(e)}
    tok = users.login(name, pw)
    return {'ok': 1, 'token': tok, 'user': _pub(u),
            'moved': [os.path.basename(s) for s, _ in moved]}


def api_users(_q, _who=None):
    """管理员看所有用户**与密码** —— 用户明确要求的。

    🔴 明文见 `users.py` 模块头那条取舍：谁能读到 `_users.json` 谁就知道
      所有人的密码。这个接口只是把那个事实摆到页面上，**它不是新的泄露**
      —— 但也正因如此，这个服务不要暴露到公网。
    """
    if not (_who and _who.get('admin')):
        return {'error': '只有管理员能看'}
    return {'users': [{'name': u['name'], 'pw': u.get('pw'),
                       'admin': bool(u.get('admin')),
                       'created': u.get('created')}
                      for u in users.load_users()]}


def api_user_add(_q, body, _who=None):
    if not (_who and _who.get('admin')):
        return {'error': '只有管理员能加人'}
    try:
        u = users.add_user((body or {}).get('name') or '',
                           (body or {}).get('pw') or '',
                           admin=bool((body or {}).get('admin')))
    except Exception as e:                                  # noqa: BLE001
        return {'error': str(e)}
    return {'ok': 1, 'user': _pub(u)}


def api_user_pw(_q, body, _who=None):
    """改密码 —— 管理员能改任何人的，普通用户只能改自己的。"""
    name = (body or {}).get('name') or ''
    if not _who:
        return {'error': '请先登录'}
    if not _who.get('admin') and name != _who['name']:
        return {'error': '只能改自己的密码'}
    try:
        users.set_pw(name, (body or {}).get('pw') or '')
    except Exception as e:                                  # noqa: BLE001
        return {'error': str(e)}
    return {'ok': 1}


# ---------------------------------------------------- Handler 用的三件
def who_of(cookie_header):
    """这个请求是谁发的 —— cookie 里那个 token 说了算。"""
    return users.whoami(users.cookie_token(cookie_header))


def need_login(path):
    return {'error': '请先登录', 'need_login': 1, 'path': path}


def _ck(tok):
    """登录/登出的 Set-Cookie。180 天 —— 会话 cookie 的话浏览器一关就要
    重登，而这一页是天天开着看盘的。"""
    if tok is None:
        return (('Set-Cookie', '%s=; Path=/; Max-Age=0' % users.COOKIE),)
    return (('Set-Cookie', '%s=%s; Path=/; Max-Age=%d; SameSite=Lax'
             % (users.COOKIE, tok, 180 * 86400)),)


def dispatch(path, q, body, who, cookie_header):
    """`/api/auth/*` 单开一条分派 —— 这几条要拿到 who 与 cookie，签名与
    别处不同，硬塞进 ROUTES/POSTS 就得给所有路由都加一个用不上的参数。
    返回 `(结果, 额外响应头)`；不认识的 path 返回 `(None, ())`。
    """
    if path == '/api/auth/me':
        return api_me(q, who), ()
    if path == '/api/auth/users':
        return api_users(q, who), ()
    if path == '/api/auth/login':
        r = api_login(q, body)
        return r, (_ck(r['token']) if r.get('token') else ())
    if path == '/api/auth/signup':
        r = api_signup(q, body)
        return r, (_ck(r['token']) if r.get('token') else ())
    if path == '/api/auth/logout':
        users.logout(users.cookie_token(cookie_header))
        return {'ok': 1}, _ck(None)
    if path == '/api/auth/user_add':
        return api_user_add(q, body, who), ()
    if path == '/api/auth/user_pw':
        return api_user_pw(q, body, who), ()
    return None, ()


class _Pass(object):
    """公共路由用的空上下文 —— 省掉调用方一个 if。"""

    def __enter__(self):
        return None

    def __exit__(self, *a):
        return False


def ctx(path, who):
    """这条请求该怎么跑：返回一个**上下文管理器**；`None` = 要先登录。

    ★ 把三种情形（没启用 / 公共 / 私有）收敛成一个返回值 —— 调用方
      `with ctx: ...` 一句话，不必在 server.py 里铺三个分支
      （守卫「server.py 又长回去了」钉的就是这个：骨架里别放域逻辑）。
    """
    if not enabled() or is_public(path):
        return _Pass()
    if not who:
        return None
    return as_user(who['name'])


def try_auth(h, path, q, body):
    """`/api/auth/*` 自己应答 —— 返回 True 表示**响应已经发出去了**。

    ★ 传 handler 进来而不是把结果传回去：这几条要读 Cookie 头、要下发
      Set-Cookie，回传的话 server.py 又得铺一段拼头的代码。
    """
    if not path.startswith('/api/auth/'):
        return False
    from ..server import _err_500          # 延迟 import：server 反过来引 auth
    import json as _j
    try:
        r, extra = dispatch(path, q, body, who_of(h.headers.get('Cookie')),
                            h.headers.get('Cookie'))
    except Exception as e:                                  # noqa: BLE001
        h._send(500, _err_500(path, e))
        return True
    h._send(404 if r is None else 200,
            _j.dumps(r if r is not None else {'error': 'no such endpoint'},
                     ensure_ascii=False, default=str), extra=extra)
    return True
