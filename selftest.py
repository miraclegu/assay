#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""回归自检。改动引擎后先跑这个。

    python3 selftest.py            # 全部
    python3 selftest.py --fast     # 日常改代码跑这个
    python3 selftest.py --web      # 需要浏览器的那层
    python3 selftest.py -k 关键字   # 按用例名过滤

每一条都对应一个曾经真实发生过的失真，不是凑数的用例。

★ 2026-09-17 拆分：用例本身搬进了 `tests/test_*.py`（按**产品域**分，
  与 `srv/` / `lv/` / `views/` 同一判据），这里只剩入口。
  🔴 **命令行一个字都没变** —— `python3 selftest.py --fast` 写在 CLAUDE.md、
    launchd、以及所有人的肌肉记忆里，那是产品契约（同「.html 一律留在
    根目录」「`--live` 参数保留」那两条）。
  🔴 import 的顺序**就是用例的执行顺序**。拆之前验过没有顺序依赖
    （三个随机种子打乱跑 fast/slow 全绿），但仍按原文件里的先后排，
    让 `--list` 的输出与拆分前尽量接近。
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
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tests._base import main                      # noqa: E402
# 🔴 这几行**不是**无用 import：`@case` 在模块加载时把用例注册进 CASES，
#   少一行就少一整域的用例，而那**不报错** —— 报告只会显示"少了几条"，
#   而没人记得本来有几条（同「断言直接扫目录而不是照清单拼」那条）。
from tests import test_engine    # noqa: E402,F401
from tests import test_data      # noqa: E402,F401
from tests import test_live      # noqa: E402,F401
from tests import test_market    # noqa: E402,F401
from tests import test_plat      # noqa: E402,F401

if __name__ == '__main__':
    main()
