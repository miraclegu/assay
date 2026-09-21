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
_ehs()
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
    out = []
    for pid in dict.fromkeys(pids):          # 去重、保序
        try:
            if platform.system() == 'Windows':
                c = subprocess.run(['wmic', 'process', 'where',
                                    'ProcessId=%s' % pid, 'get',
                                    'CommandLine'], capture_output=True,
                                   text=True).stdout
            else:
                c = subprocess.run(['ps', '-o', 'command=', '-p', pid],
                                   capture_output=True, text=True).stdout
            out.append((int(pid), ' '.join(c.split())))
        except Exception:                                       # noqa: BLE001
            out.append((int(pid), '?'))
    return out


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


def do_stop(port, wait=10.0):
    """停掉并**确认真的停了**。"""
    hit = _who(port)
    if not hit:
        print('本来就没在跑（端口 %d 没有 LISTEN）' % port)
        return 0
    mine = [(p, c) for p, c in hit if _ours(c)]
    other = [(p, c) for p, c in hit if not _ours(c)]
    for pid, cmd in other:
        print('🔴 端口 %d 被别的进程占着，**不动它**：' % port)
        print('   PID %-7s %s'
              % (pid, cmd if len(cmd) <= 88 else '…' + cmd[-87:]))
    if not mine:
        return 1
    for pid, cmd in mine:
        print('停 PID %s …' % pid, end=' ', flush=True)
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            print('（已经没了）')
            continue
        # ★ 轮询确认 —— "发了 SIGTERM"不等于"停了"
        t0 = time.time()
        while time.time() - t0 < wait:
            time.sleep(0.2)
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                print('已停（%.1fs）' % (time.time() - t0))
                break
        else:
            print('%.0fs 还没退，升级 SIGKILL …' % wait, end=' ', flush=True)
            try:
                os.kill(pid, signal.SIGKILL)
                time.sleep(0.5)
            except ProcessLookupError:
                pass
            try:
                os.kill(pid, 0)
                print('🔴 还在（PID %s）—— 手工处理' % pid)
                return 1
            except ProcessLookupError:
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
