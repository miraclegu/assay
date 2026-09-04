"""srv/base.py —— 各路由域共用的地基。

这里只放**两类**东西：
  ① 可变的模块级状态（ALLOW_BACKTEST / ALLOW_LIVE / _index / _cache…）
  ② 被【2 个以上域】调用的通用件 —— 延迟导入封装 _live/_rt/_watch/_alerts/
     _market、路径 _datalake_dir/_repo_root、归档索引 _scan、
     策略源码解析 _parse_note/_parse_params、数据新鲜度 _staleness

★ 成员不是拍脑袋定的：先按作者原有的段落标记分域，再取「被 2 个以上段调用」
  的定义（12 个），再算传递闭包（只多带出 _current_fp 一个）。

🔴 **ALLOW_BACKTEST / ALLOW_LIVE 必须用 `base.XXX` 属性访问，不许
  `from .base import ALLOW_LIVE`。** 它们是 bool（不可变），serve() 用赋值
  改它们 —— 一次性 import 拿到的是**副本**，serve() 改了值调用方看不到，
  **而这不报错**：表现是"明明开了实盘，接口还说功能没开"。
  （dict/list 那些原地修改的容器没有这个问题，可以直接 import。）"""
import ast
import json
import os
import re
import threading
import time

from .. import registry


# 🔴 `__file__` 现在在 `srv/` 里，比原来深一层 —— HERE 必须**仍指 assay 包目录**。
#   照抄 `dirname(__file__)` 的话 HERE 会变成 `.../assay/srv`，于是
#   picks.json / live/ / web/ 全部解到不存在的路径。
#   **而这不报错**：实测 /api/marks 返回 {}（标记全丢）、7 个实盘接口 500。
#   这是"把文件搬进子目录"这类重构的头号陷阱 —— 路径基准跟着文件走了。
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

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



_names = {}          # datalake root -> [(code, valid_from, valid_to, name)]



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



ALLOW_BACKTEST = False

# 这个端点会起子进程跑回测。三条约束：
#   1) 服务只监听 127.0.0.1（见 serve()）
#   2) 参数名必须在该版本解析出的参数表里；参数值走白名单正则
#   3) 用 subprocess 列表参数，**不经过 shell**

_cur_fp = None



def _current_fp():
    global _cur_fp
    if _cur_fp is None:
        try:
            from ..feed import PanelFeed
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



def _repo_root():
    """assay 与 datalake 的公共父目录。ASSAY_DOCS_ROOT 可覆盖。"""
    env = os.environ.get('ASSAY_DOCS_ROOT')
    if env:
        return os.path.abspath(os.path.expanduser(env))
    return os.path.dirname(registry.ROOT)



ALLOW_LIVE = False

_live_thread = None



def _live():
    from .. import live as _m
    return _m



def _rt():
    from assay import realtime as m
    return m



def _datalake_dir():
    """datalake 目录。与 PanelFeed 同一套解析。

    ★ 名字不能叫 `_dl_root` —— 本文件第 ~168 行已有一个 `_dl_root(run_id)`
      （按归档 meta 解 datalake 路径）。Python 对重复定义**不告警**，
      后定义的直接覆盖前面的，于是 api_equity/trades/holdings/rejects
      四个调用点全部 TypeError -> 500。实测就是这么炸的。
    """
    return os.environ.get('ASSAY_DATALAKE') or \
        os.path.join(os.path.dirname(registry.ROOT), 'datalake')



def _market():
    from assay import market as m
    return m



def _market_err(fn):
    mk = _market()
    try:
        return fn()
    except mk.MarketError as e:
        return {'error': str(e)}



def _watch():
    from assay import watchlist as w
    return w



def _alerts():
    from assay import alerts as a
    return a
