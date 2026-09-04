#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""启动看板服务。

    python3 serve.py                  # http://127.0.0.1:8770  全功能
    python3 serve.py --port 9000
    python3 serve.py --runs /vol/runs # 归档在别的盘（也可用 ASSAY_RUNS）
    python3 serve.py --readonly       # 只看不写：不起实盘线程、网页不能触发回测

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
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from assay.server import serve       # noqa: E402
from assay import registry           # noqa: E402

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
    a = ap.parse_args()
    print('归档目录 %s' % registry.set_runs(a.runs))
    full = not a.readonly
    serve(a.host, a.port, allow_backtest=full, allow_live=full)
