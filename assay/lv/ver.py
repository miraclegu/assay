"""lv/ver.py —— 版本绑定：源码快照（主文件 + 全部依赖）/ 参数 /
append-only 版本历史。

🔴 快照不依赖 `runs/`（那是 gitignore 的产物目录）——
  账户绑的版本是决策证据，放在会被清掉的地方等于没留痕。"""
import hashlib
import importlib.util
import json
import os
import re

from . import base as _base


UNSUPPORTED = {
    'trail_stop': '移动止损依赖持仓期内的峰值，成交流水里没有',
    'chand_k': '吊灯止损依赖持仓期内的 ATR/最高价序列，成交流水里没有',
    'rebal_every': '固定间隔调仓的相位依赖回测起点的绝对序号，预览窗口里推不出',
}



def _plain(t):
    """docstring 首行 -> 能直接摆进下拉框的纯文本。

    🔴 **剥掉 markdown 的 `**粗体**` 标记。** 这一行会进 `<option>` 的文字
      与 `title` 属性，而**属性里连 `<b>` 都用不了** —— 星号会原样显示成
      一串 `**`（同 indicators 的 `desc` 进 title 那条，记过两次了）。
    ★ 处理在**展示层**，不去改策略：实测 29 个策略里 8 个的首行用了
      markdown，而 docstring 本来就是写给人读的、用 markdown 完全合理。
      要求二十多个作者改写作风格是修错了地方。
    ★ 只剥标记、**保留内容**（`**成交时点**` -> `成交时点`）——
      直接删掉那段字会把话说不完整。
    """
    t = re.sub(r'\*\*(.+?)\*\*', r'\1', (t or '').strip())
    return re.sub(r'`([^`]+)`', r'\1', t)


def list_strategies(current=None):
    """可以绑的策略清单（`strategies/` 下扫出来），按目录分组。

    🔴 **判据是"有没有顶层 `initialize`"，不按文件名/目录名猜** ——
      那正是 `run.py` 加载策略时认的东西（缺了它直接
      `SystemExit('策略缺少 initialize(context)')`），所以这里与它**同源**。
      共享层 `strategies/ETF/_etf_core.py`（docstring 第一句就写着
      "本文件不是策略"）因此自动挡在外面，而不是靠 `_` 前缀那种约定 ——
      约定会过期，而"能不能跑"是可证的事实（同「判类别按快照里的 class，
      不按代码前缀猜」）。

    ★ 用 `ast` **静态**解析，不 import：import 会执行模块顶层代码
      （建面板、连 duckdb、注册任务），"列一个清单"不该有这些副作用，
      而且 29 个文件逐个 import 会很慢。

    🔴 **当前绑着的那个即使已经不在磁盘上，也要回一条并标 `missing`。**
      静默从清单里消失的话，页面上那个选择器会自动落到别的策略，
      人一保存就把账户**悄悄改绑**了 —— 而它不报错
      （同「面板里查不到的不静默丢掉，那一行留着并标 missing」）。

    ★ `_` 开头的目录（`_demo`）排在最后：项目里 `_` 开头一律是
      实验/演示（同 `prune_runs.py` 拿 `_` 开头的分组判实验）。
    """
    import ast

    root = os.path.join(_base.ROOT, 'strategies')
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d != '__pycache__')
        for fn in sorted(filenames):
            if not fn.endswith('.py') or fn.startswith('.'):
                continue
            full = os.path.join(dirpath, fn)
            try:
                tree = ast.parse(open(full, encoding='utf-8').read())
            except Exception:                               # noqa: BLE001
                # 语法坏了的文件不列 —— 它本来也绑不上（run.py 会崩）
                continue
            if not any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                       and n.name == 'initialize' for n in tree.body):
                continue
            rel = os.path.relpath(full, _base.ROOT)
            grp = os.path.relpath(dirpath, root)
            doc = (ast.get_docstring(tree) or '').strip().splitlines()
            out.append({
                'path': rel,
                'group': '' if grp == '.' else grp,
                'name': fn[:-3],
                'desc': _plain(doc[0] if doc else ''),
            })
    out.sort(key=lambda r: (r['group'].startswith('_'), r['group'], r['name']))

    cur = (current or '').strip()
    if cur and not any(r['path'] == cur for r in out):
        # 绑着一个清单里没有的路径：可能文件被删/改名，也可能它本来就
        # 不在 `strategies/` 下（`bind_version` 接受任意路径）。
        # 两种都要**如实说**，不许从清单里静默抹掉。
        exists = os.path.isfile(cur if os.path.isabs(cur)
                                else os.path.join(_base.ROOT, cur))
        out.insert(0, {
            'path': cur, 'group': '当前绑定',
            'name': os.path.basename(cur)[:-3],
            'desc': ('当前绑定（不在 strategies/ 下）' if exists
                     else '当前绑定 —— 但这个文件现在不在磁盘上了'),
            'missing': not exists, 'outside': exists,
        })
    return {'items': out, 'current': cur or None,
            'root': 'strategies'}


def _load_with_deps(path):
    """执行策略文件，并记下它【从仓库里另外加载的 .py】。

    ★ 为什么要抓依赖：`strategies/小市值/froec_traded.py` 用
      `spec_from_file_location(..., 同目录/froec.py)` 复用基线实现。
      只快照主文件会有两个后果，都很糟：
        1. 快照挪到 live/<id>/code/ 后相对路径断了，根本跑不起来
        2. 更糟 —— 改 froec.py 【不会】改变账户的版本哈希，
           于是账户悄悄换了行为而版本号纹丝不动。**版本漂移无声发生。**
      所以依赖也进快照，且版本哈希覆盖【全部文件】。

    做法是包一层 `importlib.util.spec_from_file_location` 把 location 记下来
    —— 策略调的就是这个函数对象，包住它是精确的，不靠猜。
    """
    seen = []
    orig = importlib.util.spec_from_file_location

    def _tap(name, location=None, *a, **kw):
        if location and str(location).endswith('.py'):
            ap = os.path.abspath(str(location))
            if ap.startswith(_base.ROOT + os.sep) and ap != os.path.abspath(path):
                seen.append(ap)
        return orig(name, location, *a, **kw)

    importlib.util.spec_from_file_location = _tap
    try:
        spec = orig('_live_probe_%d' % abs(hash(path)), path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        importlib.util.spec_from_file_location = orig
    return [os.path.abspath(path)] + sorted(set(seen))



def _bundle_sha(files):
    """版本哈希覆盖【主文件 + 全部依赖】—— 少算一个就是给版本漂移开后门。"""
    h = hashlib.sha256()
    for f in sorted(files, key=os.path.basename):
        h.update(os.path.basename(f).encode())
        h.update(b'\0')
        h.update(open(f, 'rb').read())
        h.update(b'\0')
    return h.hexdigest()



def bind_version(aid, strategy_path, params=None, reason=''):
    """把账户绑到【磁盘当前文件】的这个版本，并永久留痕。"""
    _base.get_account(aid)
    p = strategy_path if os.path.isabs(strategy_path) \
        else os.path.join(_base.ROOT, strategy_path)
    if not os.path.isfile(p):
        raise _base.LiveError('策略文件不存在：%s' % strategy_path)
    raw = open(p, 'rb').read()
    params = dict(params or {})
    bad = [(k, why) for k, why in UNSUPPORTED.items()
           if float(params.get(k) or 0)]
    if bad:
        raise _base.LiveError('这些参数实盘模块无法如实重建，拒绝绑定：\n' +
                        '\n'.join('  %s = %s —— %s' % (k, params[k], why)
                                  for k, why in bad))
    files = _load_with_deps(p)
    sha = _bundle_sha(files)
    d = os.path.join(_base.acct_dir(aid), 'code', sha[:8])
    for f in files:
        dst = os.path.join(d, os.path.basename(f))
        if not os.path.exists(dst):
            _base._atomic_write(dst, open(f, encoding='utf-8').read())
    _base._append_jsonl(os.path.join(_base.acct_dir(aid), 'versions.jsonl'), {
        'ts': _base._now(), 'code_sha256': sha, 'code_sha': sha[:8],
        'strategy_path': strategy_path, 'params': params, 'reason': reason,
        'main': os.path.basename(p),
        # ★ 主文件【自己】的哈希 —— 归档的 meta.code_sha256 就是这个口径
        #   （单文件字节哈希）。账户的 code_sha256 是**打包哈希**（主文件+依赖），
        #   两者不可比。想把账户版本关联到历史回测，必须另存这一个。
        'main_sha256': hashlib.sha256(raw).hexdigest(),
        'files': [os.path.relpath(f, _base.ROOT) for f in files],
    })
    lst = _base.load_accounts()
    for a in lst:
        if a['id'] == aid:
            a['code_sha256'] = sha
            a['strategy_path'] = strategy_path
            a['params'] = params
    _base._save_accounts(lst)
    return _base.get_account(aid)



def versions(aid):
    return _base._read_jsonl(os.path.join(_base.acct_dir(aid), 'versions.jsonl'))



def _main_sha(aid, v):
    """主文件自身哈希。老记录没存这个字段，从快照现算 —— 不迁移文件，
    因为 versions.jsonl 是 append-only 的账本，改写它本身就违背设计。"""
    if v.get('main_sha256'):
        return v['main_sha256']
    p = os.path.join(_base.acct_dir(aid), 'code', v['code_sha256'][:8],
                     v.get('main') or '')
    if os.path.isfile(p):
        return hashlib.sha256(open(p, 'rb').read()).hexdigest()
    return None



def _version_row(aid, sha):
    hit = [v for v in versions(aid)
           if v['code_sha256'] == sha or v['code_sha256'].startswith(sha)]
    if not hit:
        raise _base.LiveError('该账户没有绑定过版本 %s' % sha)
    return hit[-1]



def version_code(aid, sha, which=None):
    """读某个历史版本的源码快照。★ 只接受 versions.jsonl 里出现过的 sha ——
    唯一的路径来源，杜绝目录穿越。which 指定看哪个文件（默认主文件）。"""
    v = _version_row(aid, sha)
    d = os.path.join(_base.acct_dir(aid), 'code', v['code_sha256'][:8])
    names = [os.path.basename(x) for x in v.get('files') or [v.get('main')]]
    name = which or v.get('main') or names[0]
    if name not in names:
        raise _base.LiveError('版本 %s 里没有文件 %s（有：%s）'
                        % (v['code_sha'], name, ', '.join(names)))
    p = os.path.join(d, name)
    if not os.path.exists(p):
        raise _base.LiveError('版本快照文件丢失：%s —— 这不该发生，'
                        'live/ 应在版本控制里' % os.path.relpath(p, _base.ROOT))
    return open(p, encoding='utf-8').read(), v['code_sha256'], names


# ============================ 成交流水 ============================
