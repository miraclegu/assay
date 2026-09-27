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
#   默认 cp936 编不出来；输出一被重定向（`install.bat > log.txt`）就
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

    用户 2026-09-27：「点击 install.bat 后出来命令行框，确认后直接消失了，
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


def _serve(py):
    """起看板并打开浏览器 —— `--serve` 那条路。

    ★ 用 Popen 起子进程而不是 `os.execv`：execv 会把进程映像整个换掉，
      "等端口起来再开浏览器"那个线程**当场就没了**（而它不报错，
      只是浏览器永远不弹）。父进程留着当个薄薄的看门人。
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
    proc = subprocess.Popen([py, srv])
    threading.Thread(target=_open_when_up, args=(port, proc),
                     daemon=True).start()
    try:
        return proc.wait()
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
    """用**那个解释器**真 import 一遍 -> (行不行, 缺了谁)。

    🔴 这才是判据。`pip install` 返回 0 只说明 pip 没报错 —— 它可能装到了
      另一个解释器上、或者装了个架构不符的 wheel，两种都返回 0，
      而"装好了没有"这件事要问**现在 import 得到吗**。
    """
    code = ('import importlib.util as u,sys;'
            'print(",".join(p for p in %r if u.find_spec(p) is None))' % pkgs)
    try:
        r = subprocess.run([py, '-c', code], capture_output=True, text=True,
                           timeout=180)
    except (OSError, subprocess.SubprocessError) as e:
        return False, ['(跑不起来: %s)' % e]
    if r.returncode != 0:
        return False, ['(解释器报错: %s)' % (r.stderr or '').strip()[-120:]]
    miss = [x for x in (r.stdout or '').strip().split(',') if x]
    return (not miss), miss


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
                           capture_output=True, text=True)
        if r.returncode != 0 or not os.path.isfile(py):
            say(BAD + '建不起来：%s' % (r.stderr or r.stdout)[-300:])
            if os.name != 'nt':
                say('     Debian/Ubuntu 上可能要先 apt install python3-venv')
            return 2
        say(OK + '虚拟环境已建')

    # ---- ④ 装包（缺了才装）----
    have, miss = (False, pkgs) if not os.path.isfile(py) else importable(py, pkgs)
    if have:
        say(OK + '%d 个包都在' % len(pkgs))
    elif a.check:
        say(WARN + '缺 %s' % ' '.join(miss))
        bad.append('packages')
    else:
        say('  · pip install -r %s …' % os.path.relpath(REQ, ROOT))
        r = subprocess.run([py, '-m', 'pip', 'install', '-r', REQ],
                           capture_output=False)
        # 🔴 **不看 r.returncode 就下结论** —— 见文件头第 2 条
        have, miss = importable(py, pkgs)
        if not have:
            say(BAD + '装完仍然 import 不到：%s' % ' '.join(miss))
            say('     pip 返回 %d，但判据是"现在 import 得到吗" —— '
                '多半是装到了别的解释器上，或者 wheel 与本机架构不符'
                % r.returncode)
            say('     手工看一眼：%s -m pip -V' % py)
            return 2
        say(OK + '%d 个包装好并复查通过' % len(pkgs))

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
    if a.serve:
        # 🔴 起看板这件事**排在结论之后**（见 main()）—— serve.py 会一直
        #   阻塞着，夹在中间的话那句"装好了没有"要等到服务停掉才打出来。
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
    if _LOGF is not None:
        try:
            _LOGF.close()
        except Exception:                                # noqa: BLE001
            pass
    # 🔴 起看板排在**结论与日志之后**：serve.py 一起来就阻塞在这里，
    #   夹在中间的话「装好了没有」要等服务停掉才打出来，而那正是
    #   人这一刻最想知道的（同「结论必须是最后一行」那条的另一半 ——
    #   结论仍然是最后一行，只是后面接着干活）。
    if rc == 0 and _LAUNCH.get('py'):
        return _serve(_LAUNCH['py'])
    return rc

if __name__ == '__main__':
    sys.exit(main())
