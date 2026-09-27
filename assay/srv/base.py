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
from assay import paths as _paths   # datalake 根的唯一解析


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
    r"[^\S\n]*(?:\#[^\S\n]*(?P<comment>[^\n]*))?$", re.M)



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
        out.append({'name': name, 'default': val, 'line': ln,
                    'type': 'bool' if isinstance(val, bool) else
                            ('int' if isinstance(val, int) else
                             ('float' if isinstance(val, float) else 'str')),
                    'comment': (m.group('comment') or '').strip()})
    _attach_docs(code, out, lo)
    return out


# `# ---- 段落标题 ----` —— 策略里用它把参数分组（froec 有 6 段）
_SECT_RE = re.compile(r'^#\s*-{2,}\s*(?P<t>.+?)\s*-{2,}\s*$')


def _attach_docs(code, params, lo=1):
    r"""给每个参数补上【上方注释块】与【所属段落】。

    🔴🔴 **原来这两样一个都没有，而"行内注释"还抓错了行。**
      `_PARAM_RE` 里那个 `\s*(?:#...)` 的 `\s` **匹配换行** ——
      于是行内没注释时它会吃掉换行、抓到**下一行**的注释。
      实测 froec：`g.weekday` 拿到的是下一行那句
      `# ---- 用于定位对标残差的两个开关 ----`（段落标题！），
      于是回测页上**每个参数配的是别人的说明** —— 比没有说明更糟
      （同「分叉的文档比没有文档更危险」）。

    ★ 归属按 **Python 惯例**：注释写在它说明的那一行**上面**。
      实测 froec 40 个参数里行内注释只有 4 个，而上方注释块有 25 处 ——
      "没有详细解释"不是因为没人写，是因为没去读那个地方。
    ★ `group` 要**跨过别的参数行**往上找（一段标题下面往往跟着好几个
      参数），而 `doc` 只取**紧邻**的那一块 —— 两者的查找范围不同。
    """
    lines = code.splitlines()
    for p in params:
        p_line = p.pop('line')
        i = p_line - 2                        # 参数行的上一行（0-based）
        doc = []
        while i >= 0:
            t = lines[i].strip()
            if not t.startswith('#'):
                break
            if _SECT_RE.match(t):             # 段落标题不算这个参数的说明
                break
            doc.append(t.lstrip('#').strip())
            i -= 1
        doc.reverse()
        p['doc'] = [x for x in doc if x]
        # 段落标题：一路往上找，**跨过别的参数行**，遇到函数定义就停
        # 🔴 **往上找要有边界**：只在 `initialize` 函数体内找。
        #   不设界的话找不到就一路翻到文件开头，给它安一个**别处**的
        #   段落标题 —— 实测红利那次 `div_method` 被安上了 froec 风格的
        #   「持有缓冲区…」，而那与它毫无关系。**错的说明比没有说明更糟**。
        # ★ 边界只有一道：**不许出 `initialize`**（参数本来就只在它里面取）。
        #   ⚠ 我原来还写了一道 `def`/`class` 护栏 —— 变异测试证明**两道
        #     互相遮蔽、测不出哪道在起作用**，所以删掉弱的那道，
        #     让判据与实现一一对应（同「别把冗余说成抓到了」）。
        #   ⚠ 另外：加这道边界**并没有**修好红利那个
        #     `div_method -> 「持有缓冲区…」`——那个标题本来就在 initialize 内
        #     （249~465 里的第 284 行），按"最近的上方标题"就是它。
        #     **那是源码排版的事实，不是解析错**，所以页面上标一句
        #     "段落是排版不是语义保证"，不去猜作者的意图。
        g, j = '', p_line - 2
        while j >= lo - 1:
            t = lines[j].strip()
            mm = _SECT_RE.match(t)
            if mm:
                g = mm.group('t')
                break
            j -= 1
        p['group'] = g
    return params



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
    return _paths.datalake()



def explain_err(e):
    """把「本地还没有这份数据」翻译成一句**可执行**的话。

    🔴🔴 **这是唯一的正本。** 它原来只挂在 `server.py` 的 HTTP 500 分支上，
      而 `api_alerts` / `api_watchlist` / `api_live_bench` / `api_live_exec_diff`
      / `api_alerts_suggest` 这五处是 `except Exception` **自己吞掉、用
      HTTP 200 返回 `{'error': 裸异常}`** —— 于是整条翻译被绕过，
      空 lake 上页面给出的是
      `IOException: IO Error: No files found ... LINE 1: SELECT max(date) …`。
      实测：空 lake 全路由普查，21 个翻译到位、**5 个吐裸 SQL 报错**
      （同「两处实现必然分叉」）。所以翻译器进 base，谁吞异常谁调它。

    🔴 **只翻译落在 datalake 根下面的缺文件**：别的异常原样透传 ——
      把真 bug 一律说成"还没装数据"是更糟的静默（同「判据比要证的事宽」）。
    🔴 **两道护栏会互相遮蔽，缺一条就误伤**：① 看异常消息是不是"缺文件"
      （挡住**坏文件** —— 路径就在 lake 里，但那是并发写坏的 parquet）；
      ② 看路径在不在 lake 根下（挡住 KeyError 之类的**真 bug**）。
    ★ **不在这里维护一张 glob -> 阶段名 的表** —— 那就是第二份阶段清单，
      加一个阶段它不会跟着变。
    ★ 话里**不写那个 glob 路径**：页面上它会被原样渲染成一长串，把"我该
      做什么"挤没了。路径进 `missing`，由页面放进 tooltip
      （同「能进 tooltip 的就别占列」）。
    """
    msg = '%s: %s' % (type(e).__name__, e)
    low = str(e)
    if ('No files found that match the pattern' not in low
            and 'No such file or directory' not in low):
        return {'error': msg}
    try:
        root = os.path.abspath(_datalake_dir())
    except Exception:                                   # noqa: BLE001
        return {'error': msg}
    m = re.search(r'["\']([^"\']*)["\']', low)
    path = m.group(1) if m else ''
    if not path or not os.path.abspath(path).startswith(root):
        return {'error': msg}
    return {'error': '本地还没有数据 —— 用页面顶上那条横条的'
                     '「▷ 开始建本地数据」开始装配（可中断、可续跑）。',
            'no_data': True, 'missing': path, 'next': '#/sync'}


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
