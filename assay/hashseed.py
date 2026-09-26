# -*- coding: utf-8 -*-
"""🔴🔴 **回测必须跨进程可复现 —— 为此要固定 Python 的 hash 种子。**

2026-09-18 实测：`strategies/ETF/etf_p1_rotation.py` 同一份代码、同一份数据，
**10 个独立进程跑出两种结果各 5 次**；固定 `PYTHONHASHSEED` 之后 10/10 相同。

成因：策略里 `sorted([s for s in in_trend ...], key=...)` 的 `in_trend` 是个
**`set`**，而排序键**并列时没有 tie-break** —— 并列项的相对顺序就是 set 的
迭代顺序，而 Python 默认对 str 的 hash **每个进程随机**（PEP 456）。
于是选出来的标的、权重、股数都跟着变。

**为什么这件事比"模拟盘推不动"大得多**：

    模拟盘  每次 `advance` 从起点重放再与账本逐笔对账 -> 一半概率报
            「对账不一致」，而它给的原因是"数据被修正过" —— **指不到真正的原因**
    归档    同一个 run 重跑一遍结果就不同 -> 等价性回归、与聚宽对数、
            「同参数重复跑必然逐位一致」那条去重判据**全部失效**，
            **而它不报错**

★ **不去改策略**：那是移植件，改它的排序会改变与聚宽正本的对数
  （同「没有去"修" froec.py 的排序 —— 那会改变它全部历史归档的结果，
  这个不确定性值得单独记一笔，但不该顺手改掉」那条先例）。
  固定种子是**在外面**把不确定性消掉，一个策略文件都不用动。

⚠ `PYTHONHASHSEED=0` 关掉的是**哈希随机化**（它本是防 web 服务被构造大量
  碰撞键 DoS 的）。本项目是本地自用的研究工具，不接受外部输入，
  这个取舍是明知的。

🔴 **只能在解释器【启动前】设**（`sys.flags.hash_randomization` 在启动时就
  定死了），所以做法是**带着环境变量重启自己一次**。判据也因此必须是
  `sys.flags.hash_randomization == 0` 这个**可证的事实** ——
  只查"环境变量设了没"的话，在一个没 re-exec 成功的进程里照样是真。
"""
import os
import sys


def ensure_utf8_console():
    """把输出编码钉成 UTF-8 —— **Windows 上不做这件事整条链会崩**。

    本项目的脚本满屏 `✓ ✗ ⚠ ★ 🔴`，而中文 Windows 的默认编码是 cp936，
    **编不出这些字符**。交互控制台没事（PEP 528 起用 UTF-8），坑在
    **输出被重定向**的时候（schtasks 写日志、subprocess 抓输出）——
    那时 Python 退回 locale 编码，第一个 `✓` 就 `UnicodeEncodeError`、
    **退出码 1**，而报出来指不到"是编码不是逻辑"。实测验过。

    做两件事，缺一件都不够：自己的 stdout 重配（env 对已建好的流无效）、
    给子进程设 `PYTHONUTF8=1`（PEP 540；这条链上几乎每步都是子进程）。
    ★ `errors='replace'` 兜底 —— 宁可显示成 `?` 也不许整个进程崩掉。
    ★ 同一份实现在 `datalake/console.py` 也有一份（那边是 datalake 各入口
      的家）。两个仓库，跨仓共享要引依赖，这是明知的取舍；改一处要
      **两处一起改**。
    """
    if os.environ.get('PYTHONUTF8') != '1':
        os.environ['PYTHONUTF8'] = '1'
    for _n in ('stdout', 'stderr'):
        _f = getattr(sys, _n, None)
        _e = (getattr(_f, 'encoding', '') or '').lower().replace('-', '')
        if _f is None or _e.startswith('utf8'):
            continue
        try:
            _f.reconfigure(encoding='utf-8', errors='replace')
        except (AttributeError, OSError, ValueError):
            pass
    if os.name == 'nt':
        try:
            import ctypes
            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
            ctypes.windll.kernel32.SetConsoleCP(65001)
        except Exception:                                   # noqa: BLE001
            pass

SEED = '0'
_ENV = 'PYTHONHASHSEED'


def fixed():
    """这个进程的 hash 是不是已经固定了（可证的事实，不是看环境变量）。"""
    return sys.flags.hash_randomization == 0


def ensure_fixed_hash_seed(argv=None):
    """没固定就带上 `PYTHONHASHSEED` 把自己重启一次；已经固定就什么都不做。

    🔴 **必须是可执行入口的第一件事** —— exec 会把整个进程映像换掉，
      在它之前做的任何事（开文件、起线程、写盘）都白做，更糟的是
      **做了一半的副作用会重来一遍**。
    ★ 幂等：重启后的进程里 `fixed()` 为真，直接返回，**不会无限循环**。
      判据取解释器标志而不是环境变量 —— 后者在"设了但没生效"时会骗人。
    """
    if fixed():
        return False
    env = dict(os.environ)
    env[_ENV] = SEED
    # 🔴 **用 `sys.orig_argv` 不是 `sys.argv`。** 后者不含解释器本身，
    #   更要命的是 `python -c '...'` 时它是 `['-c']` —— 代码那一段没了，
    #   exec 出来的进程直接 `Argument expected for the -c option`
    #   （我第一版就是这么写的，当场被自证脚本抓到）。
    #   `sys.orig_argv`（3.10+）是**原始完整**的 argv，连 `-X` 之类的
    #   解释器选项一起带上。
    args = list(argv) if argv is not None else list(sys.orig_argv)
    os.execve(args[0] if os.path.isabs(args[0]) else sys.executable,
              args, env)                      # 不返回
