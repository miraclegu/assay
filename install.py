#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一条命令把【运行环境】装好 —— 检查、补齐、复查、告诉你下一步。

    python3 install.py          # 缺什么补什么（Windows: python install.py）
    python3 install.py --check  # 只报不动手
    python3 install.py --venv D:\\venvs\\finacial    # 指定虚拟环境位置

用户 2026-09-26："是否能用一个脚本自动把安装执行完呢？自动检测本地环境，
如果环境不满足则下载相应的包"

## 🔴 它管【环境】，不管【数据】—— 两件事不要混

    环境（这个脚本）   Python 版本 / 虚拟环境 / 4 个第三方包
    数据（页面上那个） tdx2db、全量日线、面板、因子…… 共七个阶段

数据那半**已经有一个入口**了：起 `serve.py` 之后横条上的「▷ 开始建本地
数据」，一个按钮跑到底、每步重查状态、失败就停在那里并点名。
在这里再做一遍就是**第二个入口**，而两个入口迟早一个能用一个不能
（同「两个『绑定策略』入口走同一条链」那条）。所以这里只把环境备好，
然后把人交给那个页面。

## 🔴 三条纪律，都是项目里已有的

1. **不自己再列一份包名** —— 读 `datalake/requirements.txt`。
   照抄一份的话，加一个依赖时改漏一处，新机器上就是一句裸
   `ModuleNotFoundError`，指不到该装什么。
2. **复查取"现在的状态"，不看 pip 的返回码** —— pip 装到另一个解释器上、
   或者装了个架构不符的 wheel，**都返回 0**。所以装完要用那个虚拟环境的
   python **真 import 一遍**（同「判据永远是现在的状态，不是记录」）。
3. **装不了的要说清下一步，不猜、不假装成功** —— Python 本身装不了
   （那是系统级的），就明说去哪下、要什么版本。

## ⚠ 不做的两件事

· **不装 tdx2db**：它是页面上的阶段 ①（25 MB 下载），一键建库会做。
· **不装定时任务**：那是往系统里写东西（launchd / schtasks），
  不该在人没表态时替他做 —— 横条上有入口（同「配股不自动执行」那条）。
"""
import argparse
import ast
import datetime
import io
import os
import socket
import subprocess
import sys
import threading
import time
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))        # …/assay
# 🔴 UTF-8 要在【任何打印】之前 —— 这个脚本本身满屏 ✓✗⚠，而中文 Windows
#   默认 cp936 编不出来；输出一被重定向（`start.bat > log.txt`）就
#   UnicodeEncodeError、退出码 1。见 assay/hashseed.ensure_utf8_console
sys.path.insert(0, HERE)
try:
    from assay.hashseed import ensure_utf8_console as _euc   # noqa: E402
    _euc()
except Exception:                                            # noqa: BLE001
    pass            # 装机脚本不许因为"输出好看"而起不来
ROOT = os.path.dirname(HERE)                             # 放着 assay/ 与 datalake/
DL = os.path.join(ROOT, 'datalake')
REQ = os.path.join(DL, 'requirements.txt')
MIN_PY = (3, 10)          # assay/hashseed.py 用了 sys.orig_argv（3.10+）

OK, BAD, WARN = '  ✓ ', '  ✗ ', '  ⚠ '


# 窗口关掉之后还看得到。🔴 环境变量可覆盖 —— 守卫要验「结论落盘了没有」
#   而又不许写生产目录（同 `lv.LIVE` / `progress.DIR` 那套重定向）
LOG = os.environ.get('ASSAY_INSTALL_LOG') or os.path.join(HERE, 'install.log')
_LOGF = None


def _logopen():
    """🔴 **结果必须落盘。**

    用户 2026-09-27：「点击 start.bat 后出来命令行框，确认后直接消失了，
    这样我不知道是安装完成了还是没安装完成」——`pause` 只在窗口还开着时
    有用，**人一按键那次的输出就永远没了**（同「刚跑完的 90 秒仍显示：
    任务一结束横条当场消失的话，人走开一分钟就完全不知道到底成没成」）。

    ★ **逐行 flush**：崩在半路时那半截也要留下来，否则最需要日志的那次
      恰恰是空的。
    ★ 写不出来（只读目录 / 没权限）**不许把装机搞挂** —— 静默退回只打屏幕。
    """
    global _LOGF
    try:
        _LOGF = io.open(LOG, 'w', encoding='utf-8', errors='replace')
        _LOGF.write('# %s  %s\n' % (
            datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            ' '.join(sys.argv)))
        _LOGF.flush()
    except Exception:                                    # noqa: BLE001
        _LOGF = None


def say(s=''):
    print(s, flush=True)
    if _LOGF is not None:
        try:
            _LOGF.write(s + '\n')
            _LOGF.flush()
        except Exception:                                # noqa: BLE001
            pass


def serve_port():
    """看板端口 —— 🔴 **从 serve.py 的 argparse 默认值读，不在这里再写一遍**。

    写死一个 8770 就是第二份口径：改了 serve.py 的默认端口之后，这里印出来
    的地址、以及自动打开的那个浏览器标签都会指到一个没人监听的端口，
    **而它不报错**（同「列定义写一处」「清单在服务端」那两条）。
    """
    src = io.open(os.path.join(HERE, 'serve.py'), encoding='utf-8').read()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if not (isinstance(f, ast.Attribute) and f.attr == 'add_argument'):
            continue
        if not (node.args and isinstance(node.args[0], ast.Constant)
                and node.args[0].value == '--port'):
            continue
        for kw in node.keywords:
            if kw.arg == 'default' and isinstance(kw.value, ast.Constant):
                return int(kw.value.value)
    # 找不到就响亮失败 —— 静默退回一个猜的端口正是这条注释要防的事
    raise RuntimeError('serve.py 里找不到 --port 的默认值')


def _open_when_up(port, proc):
    """等端口真的起来了再开浏览器。

    🔴 不能一上来就开：serve.py 要建面板、扫归档，端口还没 bind，
    浏览器会打在一个 Connection refused 上 —— 而那看着就像"起失败了"。
    🔴 serve.py 半路崩掉就**不开** —— 开一个打不开的标签页比不开更糟
    （同 backLink 那条）。
    """
    for _ in range(120):                 # 最多等 60 秒
        if proc.poll() is not None:
            return                       # 它已经退了，别开
        try:
            socket.create_connection(('127.0.0.1', port), 0.4).close()
            break
        except OSError:
            time.sleep(0.5)
    else:
        return
    try:
        webbrowser.open('http://127.0.0.1:%d' % port)
    except Exception:                                        # noqa: BLE001
        pass


def _hash_seed():
    """固定 hash 种子取的那个值 —— **从 `assay/hashseed.py` 读，不再写一遍**。

    在这里写死一个 `'0'` 就是第二份口径：那边改了之后这边还按旧值起子进程，
    于是子进程**照样会 re-exec**（见 `_serve`），而它不报错。
    ★ 按文件路径单独加载，不走 `import assay.hashseed` —— 那会执行
      `assay/__init__.py`，而**此刻依赖装没装好还不一定**（系统 python 跑的）。
    """
    import importlib.util as _u
    p = os.path.join(HERE, 'assay', 'hashseed.py')
    spec = _u.spec_from_file_location('_assay_hashseed', p)
    mod = _u.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return str(mod.SEED)



# 复查子进程的输出哨兵 —— 见 importable 里那段。
_SENT = '<<assay-check-done>>'

# 看板的输出往日志里最多抄这么多行 —— 启动失败都在头几十行里。
_SERVE_LOG_LINES = 400


def _kid_env():
    """给"要读它输出"的子进程用的 env —— **两头都钉成 UTF-8**。

    🔴🔴 这是 2026-09-27 真机那次「报 ✅ 装好了、而看板崩在 duckdb」的
      **根因**。`subprocess.run(..., text=True)` 不带 `encoding=` 时，
      Python 用 **locale 编码**解码子进程的输出 —— 中文 Windows 上那是
      **GBK**。子进程一吐非 ASCII 字节，读取线程当场死掉：

          Exception in thread Thread-1 (_readerthread):
            File "subprocess.py", line 1599, in _readerthread
              buffer.append(fh.read())
          UnicodeDecodeError: 'gbk' codec can't decode byte 0x80 …

      而那是**后台线程**，`subprocess.run` 这边只看到 stdout 是空的 ——
      于是"缺了哪几个包"的清单变成空清单，**复查报「都在」**，
      装机照样说 ✅，然后 serve.py 崩在 `import duckdb`。
      **空输出被当成"没有缺的"**，正是「空结果一律当失败」那条的反面。
    ★ 两件事要一起做：这边 `encoding='utf-8'` 解码，那边
      `PYTHONUTF8/PYTHONIOENCODING` 保证它真的吐 UTF-8 ——
      只钉一头的话，换个 locale 又会对不上。
    """
    e = dict(os.environ)
    e['PYTHONUTF8'] = '1'
    e['PYTHONIOENCODING'] = 'utf-8'
    e['PYTHONUNBUFFERED'] = '1'
    return e



def _tee(proc, cap=None):
    """把子进程的输出**同时**喂给屏幕和日志，返回它的退出码。

    🔴 用户 2026-09-27：「错误信息只在控制台，没有打印到日志文件中」。
      子进程原来直接继承控制台 —— `say()` 碰都碰不到它，于是
      「看板起没起来、为什么没起来」窗口一关就没了。上一轮把日志
      **留到跑完再关**只修了一半，这一半是"内容压根没喂进来"。
    ★ `cap` 是给**长跑**的（看板会一直打）：超过之后只打屏幕，并**说一句**
      —— 静默截断比截断更糟。pip 那条是有限的，不设上限。
    ★ 子进程那侧的 `PYTHONUNBUFFERED`（在 `_kid_env` 里）不能省：stdout
      变管道之后是**块缓冲**，不设就要攒够 4 KB 才出来，看着像卡住了。
    """
    n = 0
    for line in proc.stdout:
        line = line.rstrip('\n')
        if cap is None or n < cap:
            say(line)
        else:
            if n == cap:
                say('… 还在跑：往下的输出只打屏幕，不再写日志'
                    '（日志是装机用的，不该被一天的运行日志撑爆）。')
            print(line)
        n += 1
    return proc.wait()


def _serve(py):
    """起看板并打开浏览器 —— `--serve` 那条路。

    ★ 用 Popen 起子进程而不是 `os.execv`：execv 会把进程映像整个换掉，
      "等端口起来再开浏览器"那个线程**当场就没了**（而它不报错，
      只是浏览器永远不弹）。父进程留着当个薄薄的看门人。

    🔴🔴 **子进程的 env 里必须先把 `PYTHONHASHSEED` 钉上** —— 否则
      `serve.py` 的第一件事 `ensure_fixed_hash_seed()` 会带着环境变量
      **把自己 exec 一次**，而 **Windows 上 `os.exec*` 不是"原地替换"**：
      MSVCRT 的 `_wexecve` 是「**新建一个进程 + 把当前这个结束掉**」，
      于是站在这里看：

          proc.wait()      立刻返回 **0**（结束掉的是 exec 之前那个壳）
          _open_when_up    `proc.poll()` 当场非 None -> **浏览器永远不开**
          真正的 serve.py  变成一个没人管的孤儿，崩了也没人知道

      2026-09-27 真机实测就是这个现场：窗口先打「[OK] finished, exit code 0」
      和 `Press any key to continue`，**之后**才吐出 serve.py 的
      `ModuleNotFoundError` —— 顺序本身就是物证。
      ★ POSIX 上 exec 是原地替换、PID 不变，所以 `wait()` 拿到的是真退出码
        —— 本机怎么跑都对，**这条只在 Windows 上现形**
        （同「`os.kill(pid, 0)` 在 Windows 上会杀进程」那条，同一族）。
      ★ 钉上之后 `fixed()` 为真、`ensure_fixed_hash_seed()` 直接返回，
        **一次 exec 都不发生**，而"回测跨进程可复现"那条纪律分毫未动
        （种子就是它自己声明的那个）。
    """
    port = serve_port()
    srv = os.path.join(HERE, 'serve.py')
    say('')
    say('=' * 62)
    say(' ▶ 起看板中 —— 起来之后浏览器会自己打开')
    say('=' * 62)
    say('')
    say('    %s %s' % (py, srv))
    say('    http://127.0.0.1:%d' % port)
    say('')
    say('★ 要停：在这个窗口按 Ctrl-C，或者直接关掉它。')
    say('★ 下次再用，还是双击 start.bat —— 环境已经好了，它会直接起看板。')
    say('')
    env = _kid_env()
    env['PYTHONHASHSEED'] = _hash_seed()
    # 🔴 **看板的输出要进日志。** 原来是直接继承控制台 —— 于是
    #   「看板起没起来、为什么没起来」全程只在窗口里，**窗口一关就没了**，
    #   而 `install.log` 里只剩一个退出码（用户 2026-09-27 原话：
    #   「错误信息只在控制台，没有打印到日志文件中」）。
    #   上一轮把日志**留到跑完再关**修了一半，这一半是"内容压根没喂进来"。
    # ★ `PYTHONUNBUFFERED`（在 `_kid_env` 里）不能省：stdout 变成管道之后
    #   是**块缓冲**，不设的话启动横幅要攒够 4 KB 才出来，看着像卡住了
    #   （同「`--all` 重定向到文件时 tail -f 看到的永远是几十条之前的」）。
    proc = subprocess.Popen([py, srv], env=env, cwd=HERE,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT,
                            text=True, encoding='utf-8', errors='replace',
                            bufsize=1)
    threading.Thread(target=_open_when_up, args=(port, proc),
                     daemon=True).start()
    try:
        return _tee(proc, _SERVE_LOG_LINES)
    except KeyboardInterrupt:
        proc.terminate()
        return 0


def venv_python(venv):
    """虚拟环境里那个解释器 —— Windows 是 Scripts\\python.exe。"""
    if os.name == 'nt':
        return os.path.join(venv, 'Scripts', 'python.exe')
    return os.path.join(venv, 'bin', 'python')


def wanted_packages():
    """要装哪几个包 —— **只从 requirements.txt 读**，见文件头第 1 条。"""
    if not os.path.isfile(REQ):
        return None
    out = []
    for ln in io.open(REQ, encoding='utf-8').read().splitlines():
        ln = ln.strip()
        if ln and not ln.startswith('#'):
            out.append(ln.split('>')[0].split('=')[0].split('[')[0].strip())
    return out


def importable(py, pkgs):
    """用**那个解释器**真 import 一遍 -> (行不行, 谁不行 + 为什么)。

    🔴 这才是判据。`pip install` 返回 0 只说明 pip 没报错 —— 它可能装到了
      另一个解释器上、或者装了个架构不符的 wheel，两种都返回 0，
      而"装好了没有"这件事要问**现在 import 得到吗**。

    🔴🔴 **必须真 `import`，不能用 `find_spec`。** 2026-09-27 之前这里写的是
      `importlib.util.find_spec(p) is None` —— 而 find_spec **只回答"找不找得
      到"，不执行那个包**。于是「目录在而 `__init__.py` 半截」「扩展模块的
      DLL 加载不上」「包里第一句 import 就炸」这几种坏法它一律报"在"，
      而真跑起来是一句 ModuleNotFoundError。
      ★ 这个函数**自己的 docstring 当时写的就是「真 import 一遍」** ——
        代码在和自己的注释打架（同 `ddGap` / `lprSlice` / `_PQ` 那几次）。

    ★ `cwd=HERE`：`-c` 的 `sys.path[0]` 是**当前目录**，而 `serve.py` 的是
      **脚本所在目录**。不对齐的话复查与真跑看到的是两条 sys.path，
      **而那不报错**（只是复查说"在"、真跑说"没有"）。
    """
    code = ('import sys\n'
            'bad = []\n'
            'for p in ' + repr(list(pkgs)) + ':\n'
            '    try:\n'
            '        __import__(p)\n'
            '    except BaseException as e:\n'
            '        bad.append(p + " (" + type(e).__name__ + ": "\n'
            '                   + str(e)[:120] + ")")\n'
            # 🔴 **末尾必须打一个哨兵**：`subprocess` 的读取线程死掉时
            #   （GBK 解不开 UTF-8 那次就是）stdout 回来是**空的**，
            #   而空的 `bad` 清单恰好等于"一个都不缺" —— 于是复查报
            #   「都在」、装机说 ✅、serve.py 再崩在 import 上。
            #   有哨兵才分得出「真的一个都不缺」与「我根本没读到」
            #   （同「空结果一律当失败」）。
            'sys.stdout.write("\\u0001".join(bad) + "%s")\n' % _SENT)
    try:
        r = subprocess.run([py, '-c', code], capture_output=True, text=True,
                           encoding='utf-8', errors='replace',
                           env=_kid_env(),
                           timeout=300, cwd=HERE)
    except (OSError, subprocess.SubprocessError) as e:
        return False, ['(跑不起来: %s)' % e]
    if r.returncode != 0:
        return False, ['(解释器报错: %s)' % (r.stderr or '').strip()[-160:]]
    out = r.stdout or ''
    if _SENT not in out:
        # 读不到哨兵 = 这次复查**没有结论**，不是"没有缺的"。
        return False, ['(读不到复查结果 —— 子进程输出没收全：%r)'
                       % (out[-80:] or r.stderr[-80:] if r.stderr else out)]
    miss = [x for x in out.split(_SENT)[0].split('\u0001') if x.strip()]
    return (not miss), miss


def app_imports(py):
    """**真正要跑的那件事**：`import assay.server` 在那个解释器上成不成。

    🔴 四个包都 import 得到 **≠** 看板起得来。`requirements.txt` 自己的注释
      就写着「缺了 duckdb 的 `serve.py` 起不来」—— 而"要哪几个包"那份清单是
      人维护的，会过期（新加一个 import 忘了写进去就不报，同「照清单拼会漏
      掉新文件的全部组合」）。直接 import 那个模块**不依赖任何清单**。

    ★ 2026-09-27 真机踩到：Windows 上复查说 4 个包都在、装机报
      「✅ 装好了 · exit code 0」，而 serve.py 第一句
      `from assay.server import serve` 就 `ModuleNotFoundError: No module
      named 'duckdb'` —— 两句话直接矛盾，而**没有任何地方报错**。
      这一层就是不让那种矛盾再悄悄发生。
    """
    code = ('import sys\n'
            'sys.path.insert(0, ' + repr(HERE) + ')\n'
            'import assay.server\n')
    try:
        r = subprocess.run([py, '-c', code], capture_output=True, text=True,
                           encoding='utf-8', errors='replace',
                           env=_kid_env(),
                           timeout=600, cwd=HERE)
    except (OSError, subprocess.SubprocessError) as e:
        return False, '跑不起来: %s' % e
    if r.returncode == 0:
        return True, ''
    tail = [x for x in (r.stderr or '').strip().splitlines() if x.strip()]
    return False, (tail[-1].strip() if tail else '退出码 %d' % r.returncode)


def _run():
    ap = argparse.ArgumentParser(description='装好运行环境（不碰数据）')
    ap.add_argument('--check', action='store_true', help='只报不动手')
    ap.add_argument('--serve', action='store_true',
                    help='装好之后直接起看板并打开浏览器')
    ap.add_argument('--venv', default=None,
                    help='虚拟环境位置（默认 <父目录>/.venv）')
    a = ap.parse_args()
    bad = []

    say('=' * 62)
    say(' 环境装配   %s %s' % (sys.platform, '.'.join(map(str, sys.version_info[:3]))))
    say('=' * 62)

    # ---- ① Python 版本（装不了就明说，不猜）----
    if sys.version_info[:2] < MIN_PY:
        say(BAD + 'Python %s，需要 %s 以上'
            % ('.'.join(map(str, sys.version_info[:3])),
               '.'.join(map(str, MIN_PY))))
        say('     assay/hashseed.py 用了 sys.orig_argv（3.10 才有），'
            '而那是固定 hash 种子、保证回测跨进程可复现的入口。')
        if os.name == 'nt':
            say('     去 https://www.python.org/downloads/windows/ 下 3.10+，'
                '安装时【务必勾上 Add python.exe to PATH】')
        else:
            say('     brew install python@3.13   或去 python.org 下安装包')
        say('\n🔴 Python 本身这个脚本装不了（那是系统级的）—— 装好再跑一次。')
        return 2
    say(OK + 'Python %s' % '.'.join(map(str, sys.version_info[:3])))

    # ---- ② 两个仓库并排吗 ----
    if not os.path.isdir(DL):
        say(BAD + '找不到 %s' % DL)
        say('     两个仓库要【并排】放：<父目录>/assay 与 <父目录>/datalake')
        say('     现在 assay 在 %s' % HERE)
        return 2
    say(OK + '两个仓库并排（%s）' % ROOT)

    pkgs = wanted_packages()
    if not pkgs:
        say(BAD + '读不到依赖清单 %s' % REQ)
        say('     它在 datalake 仓库里 —— 多半是那个仓库没拉全，或者'
            '分支不对（clone 默认拿的是 main）')
        return 2
    say(OK + '依赖清单 %d 个包：%s' % (len(pkgs), ' '.join(pkgs)))

    # ---- ③ 虚拟环境 ----
    # ★ 已经在某个 venv 里跑这个脚本 -> 就用它，不再另建一个
    in_venv = sys.prefix != getattr(sys, 'base_prefix', sys.prefix)
    if a.venv:
        venv = os.path.abspath(a.venv)
    elif in_venv:
        venv = sys.prefix
    else:
        venv = os.path.join(ROOT, '.venv')
    py = venv_python(venv)
    if in_venv and not a.venv:
        say(OK + '已在虚拟环境里：%s' % venv)
    elif os.path.isfile(py):
        say(OK + '虚拟环境已有：%s' % venv)
    elif a.check:
        say(WARN + '还没有虚拟环境（%s）' % venv)
        bad.append('venv')
    else:
        say('  · 建虚拟环境 %s …' % venv)
        r = subprocess.run([sys.executable, '-m', 'venv', venv],
                           capture_output=True, text=True,
                           encoding='utf-8', errors='replace',
                           env=_kid_env())
        if r.returncode != 0 or not os.path.isfile(py):
            say(BAD + '建不起来：%s' % (r.stderr or r.stdout)[-300:])
            if os.name != 'nt':
                say('     Debian/Ubuntu 上可能要先 apt install python3-venv')
            return 2
        say(OK + '虚拟环境已建')

    # ---- ④ 装包（缺了才装）----
    # 🔴 **这一步要先打一行**：它是起子进程真 import 那几个包，冷 venv 上
    #   Windows 第一次要几十秒（读几百 MB 的扩展模块）。不打的话日志里
    #   就是一段没有解释的静默 —— 人分不出"还在跑"与"卡死了"
    #   （同「`--all` 的进度行要排在用例【跑之前】」那条）。
    if os.path.isfile(py):
        say('  · 复查这 4 个包 import 得起来吗（第一次会慢几十秒）…')
    have, miss = (False, pkgs) if not os.path.isfile(py) else importable(py, pkgs)
    if have:
        say(OK + '%d 个包都在' % len(pkgs))
    elif a.check:
        say(WARN + '缺 %s' % ' '.join(miss))
        bad.append('packages')
    else:
        say('  · pip install -r %s …' % os.path.relpath(REQ, ROOT))
        # 🔴 pip 的记录**也要进日志** —— 2026-09-27 定位那次，决定性的
        #   证据正是它那几行 `Requirement already satisfied … (1.5.5)`，
        #   而它当时只在控制台里，窗口一关就没了。
        rc = _tee(subprocess.Popen(
            [py, '-m', 'pip', 'install', '-r', REQ], env=_kid_env(),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding='utf-8', errors='replace', bufsize=1))
        # 🔴 **不看 r.returncode 就下结论** —— 见文件头第 2 条
        have, miss = importable(py, pkgs)
        if not have:
            say(BAD + '装完仍然 import 不到：%s' % ' '.join(miss))
            say('     pip 返回 %d，但判据是"现在 import 得到吗" —— '
                '多半是装到了别的解释器上，或者 wheel 与本机架构不符'
                % rc)
            say('     手工看一眼：%s -m pip -V' % py)
            return 2
        say(OK + '%d 个包装好并复查通过' % len(pkgs))

    # ---- ④b 真正要跑的那件事：看板 import 得起来吗 ----
    # 🔴 上面那层问的是"清单里那 4 个在不在"，而清单是人维护的、会过期。
    #   这一层直接 import 要跑的那个模块 —— 它不依赖清单，也不依赖我对
    #   "缺了谁"的判断（同「判据永远是现在的状态，不是记录」）。
    if os.path.isfile(py):
        say('  · 复查看板 import 得起来吗（assay.server）…')
        _ok, _why = app_imports(py)
        if _ok:
            say(OK + '看板 import 得起来（assay.server）')
        elif a.check:
            say(WARN + '看板 import 不起来：%s' % _why)
            bad.append('看板起不来')
        else:
            say(BAD + '清单里那几个包都在，但【看板 import 不起来】：%s' % _why)
            say('     判据是"拿那个解释器真 import 一遍"，所以这不是误报。')
            say('     用的解释器：%s' % py)
            say('     手工看一眼装到哪儿去了：%s -m pip list' % py)
            return 2

    # ---- ⑤ tdx2db 只报不装（见文件头「不做的两件事」）----
    sys.path.insert(0, DL)
    try:
        import paths as _p                                    # noqa: E402
        binp = _p.tdx2db_bin()
        if os.path.isfile(binp):
            say(OK + 'tdx2db 已装')
        else:
            say(WARN + 'tdx2db 还没装 —— 起看板后在横条上点'
                       '「▷ 开始建本地数据」，它是第 ① 步')
    except Exception as e:                                    # noqa: BLE001
        say(WARN + '查不到 tdx2db 状态（%s）—— 不影响起看板' % e)

    # ---- 收尾：下一步说清楚，带绝对路径 ----
    say('')
    if bad:
        say('🔴 --check 只报不动手。去掉它再跑一次就会补上：%s' % ' / '.join(bad))
        return 1
    if a.serve and not a.check:
        # 🔴 起看板这件事**排在结论之后**（见 main()）—— serve.py 会一直
        #   阻塞着，夹在中间的话那句"装好了没有"要等到服务停掉才打出来。
        # 🔴 `--check` 压过 `--serve`：说了"只报不动手"却起一个服务进程，
        #   那是自相矛盾（而 start.bat 无条件带 --serve，所以
        #   `start.bat --check` 必然走到这一支）。
        _LAUNCH['py'] = py
        say(OK + '环境好了 —— 接着起看板')
        return 0
    say('=' * 62)
    say(' ✅ 环境好了。下一步 —— 起看板，然后在横条上点「▷ 开始建本地数据」')
    say('=' * 62)
    say('')
    say('    %s %s' % (py, os.path.join(HERE, 'serve.py')))
    say('')
    say('    然后打开 http://127.0.0.1:%d' % serve_port())
    say('')
    say('★ 上面那个是【虚拟环境里】的 python 绝对路径 —— 直接敲 `python`'
        ' 的话可能是系统那个（没装 duckdb，serve.py 起不来）。')
    say('★ 想一步到位：双击 start.bat（Windows）或 `python3 install.py'
        ' --serve` —— 装好之后直接起看板并打开浏览器。')
    say('★ Windows 上【只有 start.bat 一个入口】：它自己会把环境备好再起'
        '看板，不用先跑别的。')
    return 0


# ---- 收尾：一句明确的结论 + 落盘 ------------------------------------
#  🔴 **结论必须是最后一行。** 上面那几十行里有 ✓ 也有 ⚠，人扫一眼是看不出
#    "到底成没成"的；而窗口关掉之后连那几十行都没了。所以：
#      ① 一句话结论，排在最末（不是夹在中间）
#      ② 同时写进 install.log —— 窗口关了还查得到（同「任务一结束横条当场
#         消失的话，人走开一分钟就完全不知道到底成没成」）
_VERDICT = {
    0: '✅ 装好了 —— 照上面那行命令起看板',
    1: '⚠  只检查没动手（--check）—— 去掉 --check 再跑一次就会补上',
    2: '🔴 没装成 —— 原因见上面那条 ✗，修好再跑一次',
}


_LAUNCH = {}          # --serve 时装完要起看板，见 _run() 收尾


def _logclose():
    global _LOGF
    if _LOGF is not None:
        try:
            _LOGF.close()
        except Exception:                                # noqa: BLE001
            pass
        _LOGF = None


def main():
    _logopen()
    try:
        rc = _run()
    except BaseException as e:                           # noqa: BLE001
        # 🔴 连异常也要落盘：最需要日志的那一次恰恰是崩掉的那一次
        import traceback
        say('')
        say(BAD + '装机脚本自己崩了：%s: %s' % (type(e).__name__, e))
        say(traceback.format_exc())
        rc = 3
        _VERDICT[3] = '🔴 装机脚本自己崩了 —— 把 install.log 贴出来'
    say('')
    say('-' * 62)
    say(' %s' % _VERDICT.get(rc, '退出码 %s' % rc))
    if _LOGF is not None:
        say(' 这次的完整输出已写进：%s' % LOG)
    say('-' * 62)
    # 🔴 起看板排在**结论之后**：serve.py 一起来就阻塞在这里，夹在中间的话
    #   「装好了没有」要等服务停掉才打出来，而那正是人这一刻最想知道的。
    # 🔴🔴 **但日志【不能】在这里关掉。** 2026-09-27 之前是先 close 再起看板，
    #   于是「看板起没起来、为什么没起来」那一段**一个字都没进日志**
    #   —— 而那恰恰是出问题时唯一要查的东西（`say()` 对写不进去是静默
    #   兜底的，所以它连个错都不报）。现在留到真正跑完再关。
    if rc == 0 and _LAUNCH.get('py'):
        src = _serve(_LAUNCH['py'])
        if src != 0:
            say('')
            say('-' * 62)
            say(' 🔴 环境装好了，但【看板没起来】（退出码 %s）' % src)
            say('    上面那段报错就是原因；完整输出在 %s' % LOG)
            say('-' * 62)
        _logclose()
        # ★ 退出码分得开：装机本身成了（所以不是 2），但看板没起来
        #   —— 报成 0 的话 start.bat 会写「[OK] finished」，那是在说谎。
        return 0 if src == 0 else 4
    _logclose()
    return rc


if __name__ == '__main__':
    sys.exit(main())
