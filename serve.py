#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""启动看板服务。

    python3 serve.py                  # http://127.0.0.1:8770  全功能
    python3 serve.py --port 9000
    python3 serve.py --runs /vol/runs # 归档在别的盘（也可用 ASSAY_RUNS）
    python3 serve.py --readonly       # 只看不写：不起实盘线程、网页不能触发回测
    python3 serve.py --status         # 在跑吗？代码比进程新吗（该不该重启）
    python3 serve.py --stop           # 停掉，并【确认真的停了】
    python3 serve.py --restart        # 停掉旧的、确认端口空了、再前台起新的

## 🔴 为什么用「谁占着端口」判断，而不是 PID 文件

PID 文件会**陈旧**：`kill -9`、机器重启、进程崩掉，文件都还在。
更糟的是 **PID 会被复用** —— 只看"文件里那个号还活着"就 kill，
有可能杀掉一个刚好复用了这个号的无关进程。
"现在谁占着这个端口"才是真正要回答的问题
（同 CLAUDE.md 里 launchd 那条：判据永远是"现在到底开着没"，不是记录）。
★ 而且找到之后**必须验它是不是我们的 serve.py** —— 端口被别的东西占着时
  要说清那是谁，而不是照 kill。
★ `--stop` 发完 SIGTERM 会**轮询确认进程真的退出**，超时才升级到 SIGKILL。
  "发了信号"不等于"停了"。

## 为什么默认全开（2026-09-04 改）

原来是 `--live` 与 `--allow-backtest` 两个开关、都默认关，理由是"有副作用的
部分放在开关后面"。**自用项目下这个理由不成立**，而它制造的问题更大：

  · 🔴 实测踩过：忘了加 `--allow-backtest`，网页上「用这个版本跑一次」渲染成
    disabled —— **点了没反应，而 disabled 的元素连 title 提示都不触发**，
    完全无法自查（同 backLink 那条：给一个点了没反应的按钮比不给更糟）。
  · 实盘是这个看板的主要功能，`--live` 事实上每次都要加；两个开关里
    有一个总是忘（而"忘了"的表现是功能静默消失，不是报错）。
  · 所谓的副作用其实很轻：定时线程只在交易时段抓行情，写的是 append-only
    的账本；网页触发回测是**人点的**，起个子进程正是他要的。

所以反过来：**默认全功能，`--readonly` 用于"我只想翻翻历史归档"**。
安全侧仍然守得住 —— 真正危险的东西（改账本、删数据）从来就不在这两个
开关后面，它们本来就有各自的确认。

★ 旧的 `--live` / `--allow-backtest` 仍然接受（不报错、无副作用），
  这样老的启动命令与 launchd/说明文档不会突然失效。
"""

# 🔴🔴 **第一件事：固定 hash 种子，否则回测跨进程不可复现。**
#   实测 `etf_p1_rotation` 同数据同代码 10 个进程跑出两种结果各 5 次 ——
#   策略里对一个 `set` 排序、并列时没有 tie-break，而 str 的 hash 每进程随机。
#   只能在解释器启动前设，所以这里带着环境变量把自己重启一次（幂等）。
#   详见 `assay/hashseed.py`。
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from assay.hashseed import ensure_fixed_hash_seed as _ehs   # noqa: E402
from assay.hashseed import ensure_utf8_console as _euc      # noqa: E402
# 🔴 UTF-8 要在【任何打印与任何子进程】之前 —— 中文 Windows 默认 cp936，
#   编不出满屏的 ✓✗⚠，输出一被重定向（schtasks 写日志 / subprocess 抓
#   输出）就 UnicodeEncodeError、退出码 1。见 hashseed.ensure_utf8_console
# 🔴 **顺序不能反**：`_ehs()` 要固定 hash 种子，没固定就 `os.execve` 把自己
#   重启一次 —— 在它之前做的事白做、有副作用的会重来一遍，所以它必须是
#   第一条可执行语句（守卫用 ast 钉着）。而 `_euc()` 放在后面不影响 UTF-8：
#   `_ehs` 在 exec 之前一个字都不打印，当前进程靠 `reconfigure()`、
#   子进程靠 `PYTHONUTF8=1`，两条路都不要求"在 exec 之前设"。
_ehs()
_euc()

# 🔴🔴 **日志要在【任何重依赖 import 之前】接上。**
#   用户真机上那次崩的是 `from assay.server import serve` 里面的
#   `import duckdb`（见 install.py 那节）—— 接在 `__main__` 里的话，
#   那段 traceback 一个字都进不了文件，窗口一关就没了。
#   而"程序不在本机执行、要能事后排查"正是这份日志存在的理由。
# ★ tee 不是重定向：前台跑时终端照样看得见（同 install.py 的 `_tee`）。
_daylog = None
try:
    from assay import paths as _dlp                          # noqa: E402
    _dlroot = _dlp.datalake()
    if _dlroot not in _sys.path:
        _sys.path.insert(0, _dlroot)
    import logs as _dllogs                                   # noqa: E402
    # ★ 看板单独一个文件（`web-<天>.log`）—— 原来它和同步链、信号重算
    #   混在 `daily` 里，而「看板报了个错」与「昨晚同步失败了」是两件
    #   不同的事，混在一起查哪一件都要先把另一件滤掉。
    _daylog, _ = _dllogs.tee_stdio(_dllogs.KIND_WEB, _dlroot, 'web')
except Exception as _e:                                      # noqa: BLE001
    # 🔴 不许静默：日志没接上时，人事后翻不到任何东西，而屏幕上一切正常
    #   （同「保护分支不该静默跳过」）。这里不抛 —— 日志坏了不该让看板起不来。
    _sys.stderr.write('⚠ 日志没接上（%s: %s）—— 输出只在屏幕上\n'
                      % (type(_e).__name__, _e))

import argparse
import json
import os
import platform
import signal
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from assay.server import serve       # noqa: E402
from assay import registry           # noqa: E402



def _cmdline(pid):
    """这个 PID 的命令行 —— 取不到返回 `''`（**不是 `'?'`**，见 do_stop）。

    🔴🔴 **Windows 上不能只靠 `wmic`：它在 Win11 24H2 / Server 2025 上
      默认已经【移除】了。** 取不到命令行时 `_ours()` 判 False，于是
      `--stop` 会说「端口被别的进程占着，不动它」并拒绝重启 ——
      **而那个"别的进程"其实就是它自己**，报错指到了错的原因
      （同「报错必须指向真正的原因」那条）。
    ★ 退路取 PowerShell 的 CIM：`Get-CimInstance Win32_Process` 与 wmic
      问的是同一个 WMI 提供程序，而 PowerShell 不会被移除。
    ⚠ 这两条分支**都没在真机上跑过** —— 逻辑与命令核对过，如实记
      （同 junction / OpenProcess / schtasks 那几条）。
    """
    if platform.system() != 'Windows':
        try:
            return ' '.join(subprocess.run(
                ['ps', '-o', 'command=', '-p', str(pid)],
                capture_output=True, text=True).stdout.split())
        except Exception:                                       # noqa: BLE001
            return ''
    for cmd in (['wmic', 'process', 'where', 'ProcessId=%s' % pid,
                 'get', 'CommandLine'],
                ['powershell', '-NoProfile', '-Command',
                 '(Get-CimInstance Win32_Process -Filter '
                 '"ProcessId=%s").CommandLine' % pid]):
        try:
            out = subprocess.run(cmd, capture_output=True, text=True,
                                 timeout=20).stdout or ''
        except Exception:                                       # noqa: BLE001
            continue        # 这条路没有（wmic 被移除）—— 试下一条
        # wmic 会把表头 `CommandLine` 也打出来，去掉它再判空
        out = ' '.join(x for x in out.split() if x != 'CommandLine')
        if out:
            return out
    return ''


def _who(port):
    """谁占着这个端口 -> [(pid, 命令行)]。见模块头「为什么不用 PID 文件」。"""
    pids = []
    if platform.system() == 'Windows':
        r = subprocess.run(['netstat', '-ano'], capture_output=True, text=True)
        for ln in (r.stdout or '').split('\n'):
            if (':%d' % port) in ln and 'LISTEN' in ln.upper():
                pids.append(ln.split()[-1])
    else:
        r = subprocess.run(['lsof', '-ti', 'tcp:%d' % port, '-sTCP:LISTEN'],
                           capture_output=True, text=True)
        pids = (r.stdout or '').split()
    return [(int(pid), _cmdline(pid)) for pid in dict.fromkeys(pids)]


def _ours(cmd):
    """是不是我们的看板进程。

    🔴 端口被别的东西占着时**不许照 kill** —— 那会停掉别人的服务。
      判据放宽一点（serve.py 或 assay.server 都算），但绝不放宽到"任何进程"。
    """
    return 'serve.py' in cmd or 'assay.server' in cmd


def _probe(host, port, timeout=3):
    """问服务自己要状态。拿不到返回 None —— 端口占着但不响应也是一种情况。"""
    try:
        u = 'http://%s:%d/api/live/accounts' % (host, port)
        return json.loads(urllib.request.urlopen(u, timeout=timeout).read())
    except Exception:                                           # noqa: BLE001
        return None


def do_status(host, port):
    hit = _who(port)
    if not hit:
        print('未在运行（端口 %d 没有 LISTEN）' % port)
        return 1
    for pid, cmd in hit:
        tag = '' if _ours(cmd) else '   ⚠️ 不是我们的 serve.py'
        # ★ 长路径从**头**截会把 `serve.py --port …` 这段切掉，
        #   而那正是人要看的（是不是我们的进程、什么参数）。所以留尾部。
        show = cmd if len(cmd) <= 88 else '…' + cmd[-87:]
        print('PID %-7s %s%s' % (pid, show, tag))
    d = _probe(host, port)
    if d is None:
        print('  端口占着但 /api 不响应 —— 可能刚起、或卡住了')
        return 2
    print('  模式      %s' % ('只读（--readonly）' if d.get('readonly')
                              else '全功能'))
    # ★ 字段在 `code` 子对象里，不在顶层（第一版取错了，表现是三行关键信息
    #   静默不打印 —— 而"该不该重启"正是 --status 存在的理由）。
    code = d.get('code') or {}
    boot, mt = code.get('loaded_at'), code.get('code_mtime')
    if not (boot and mt):
        print('  ⚠️ 服务端没报代码指纹（%s）—— 判断不了该不该重启'
              % (sorted(code) or '没有 code 字段'))
        return 0
    if boot and mt:
        f = lambda t: time.strftime('%m-%d %H:%M:%S', time.localtime(t))
        print('  进程启动  %s' % f(boot))
        print('  代码改于  %s' % f(mt))
        # 🔴 这正是 CLAUDE.md 那条「改了 assay/*.py 必须重启」的判据 ——
        #   代码比进程新时会出现「新页面 + 旧 API」，满屏 undefined 且不报错。
        if mt > boot:
            print('  🔴 代码比进程新 —— 请 `python3 serve.py --restart`')
            return 3
        print('  ✓ 进程加载的是当前代码')
    return 0


def _alive(pid):
    """那个进程还在吗 —— **跨平台**。

    🔴 **不能直接用 `os.kill(pid, 0)`。** POSIX 上那是"只探测不发信号"的
      惯用法；而 **Windows 上 `os.kill` 对任何非 `CTRL_*` 的 sig 都走
      `TerminateProcess`** —— 于是这句"探测"会把进程**真的杀掉**，
      而它不报错（`--status` 看一眼就把服务停了）。
    ★ 同一份实现在 `datalake/progress.py` 也有一份（横条读进度要判
      "跑的那个进程还在吗"）—— 两个仓库，跨仓共享要引依赖，
      这是明知的取舍。改一处要**两处一起改**。
    """
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if os.name == 'nt':
        import ctypes
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, False, pid)   # QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = k.GetExitCodeProcess(h, ctypes.byref(code))
        k.CloseHandle(h)
        return bool(ok) and code.value == 259   # STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        return False


def do_stop(port, wait=10.0):
    """停掉并**确认真的停了**。"""
    hit = _who(port)
    if not hit:
        print('本来就没在跑（端口 %d 没有 LISTEN）' % port)
        return 0
    mine = [(p, c) for p, c in hit if _ours(c)]
    # 🔴 **"取不到命令行"与"是别人的"是两件事，不许混成一句。**
    #   wmic 在 Win11 24H2 起默认被移除，PowerShell 那条退路也可能被策略
    #   挡掉 —— 那时说「被别的进程占着」是在**指错原因**：那个进程八成
    #   就是它自己。照实说不知道，并给下一步（同「报错必须指向真正的原因」）。
    unknown = [(p, c) for p, c in hit if not c]
    other = [(p, c) for p, c in hit if c and not _ours(c)]
    for pid, cmd in other:
        print('🔴 端口 %d 被别的进程占着，【不动它】：' % port)
        print('   PID %-7s %s'
              % (pid, cmd if len(cmd) <= 88 else '…' + cmd[-87:]))
    for pid, _ in unknown:
        print('🔴 端口 %d 上有 PID %s，但【取不到它的命令行】，'
              '无法确认是不是我们的看板 —— 不动它。' % (port, pid))
        if platform.system() == 'Windows':
            print('   多半是 wmic 被移除了（Win11 24H2 起）而 PowerShell '
                  '也没跑成。手工确认一下再停：')
            print('   powershell "Get-CimInstance Win32_Process -Filter '
                  '\'ProcessId=%s\' | fl ProcessId,CommandLine"' % pid)
            print('   确认是看板就： taskkill /PID %s /F' % pid)
        else:
            print('   手工确认： ps -p %s -o command=' % pid)
    if not mine:
        return 1
    for pid, cmd in mine:
        print('停 PID %s …' % pid, end=' ', flush=True)
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:          # Windows 上进程没了不是 ProcessLookupError
            print('（已经没了）')
            continue
        # ★ 轮询确认 —— "发了 SIGTERM"不等于"停了"
        t0 = time.time()
        while time.time() - t0 < wait:
            time.sleep(0.2)
            if not _alive(pid):
                print('已停（%.1fs）' % (time.time() - t0))
                break
        else:
            print('%.0fs 还没退，升级强杀 …' % wait, end=' ', flush=True)
            # 🔴 **Windows 上没有 `signal.SIGKILL`** —— 直接引用是
            #   AttributeError。而那边 `os.kill` 对任何非 CTRL_* 的 sig
            #   本来就是 TerminateProcess，所以退回 SIGTERM 就是强杀。
            try:
                os.kill(pid, getattr(signal, 'SIGKILL', signal.SIGTERM))
                time.sleep(0.5)
            except (ProcessLookupError, PermissionError, OSError):
                pass
            if _alive(pid):
                print('🔴 还在（PID %s）—— 手工处理' % pid)
                return 1
            print('已强杀')
    # 🔴 最后再确认端口真的空了：进程没了但端口处于 TIME_WAIT 也会让
    #   新进程 bind 失败，而那时的报错是 "Address already in use"，
    #   指不到"上一个还没退干净"这个原因。
    for _ in range(25):
        if not _who(port):
            return 0
        time.sleep(0.2)
    print('⚠️ 进程已停，但端口 %d 仍被占用（TIME_WAIT?）—— 稍等再起' % port)
    return 1


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--host', default='127.0.0.1')
    ap.add_argument('--port', type=int, default=8770)
    ap.add_argument('--runs', default=None,
                    help='归档目录，默认 ASSAY_RUNS 环境变量或 <repo>/runs')
    ap.add_argument('--readonly', action='store_true',
                    help='只看不写：不起实盘定时线程、网页不能触发回测')
    # 兼容旧命令：默认已经全开，这两个加了也只是"再确认一次"。
    # ★ 不删是因为 launchd/文档/肌肉记忆里都有它们，而"参数不认"会让
    #   服务直接起不来 —— 那是最糟的失败方式。
    ap.add_argument('--live', action='store_true',
                    help='（已默认开启，保留兼容）')
    ap.add_argument('--allow-backtest', action='store_true',
                    help='（已默认开启，保留兼容）')
    # ★ 用 flag 而不是子命令：子命令会让裸 `python3 serve.py` 不再合法，
    #   而"参数不认"是最糟的失败方式（同下面那两个兼容开关的理由）。
    ap.add_argument('--status', action='store_true',
                    help='在跑吗？代码比进程新吗（该不该重启）')
    ap.add_argument('--stop', action='store_true',
                    help='停掉，并确认真的停了')
    ap.add_argument('--restart', action='store_true',
                    help='停掉旧的、确认端口空了、再前台起新的')
    a = ap.parse_args()
    if a.status:
        sys.exit(do_status(a.host, a.port))
    if a.stop:
        sys.exit(do_stop(a.port))
    if a.restart:
        rc = do_stop(a.port)
        if rc:
            sys.exit(rc)                 # 没停干净就不起 —— 否则 bind 失败
        print('---- 重新启动 ----')
    print('归档目录 %s' % registry.set_runs(a.runs))
    full = not a.readonly
    serve(a.host, a.port, allow_backtest=full, allow_live=full)
