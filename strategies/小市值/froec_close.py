#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FROEC-CLOSE：把调仓的**成交时点**从开盘挪到尾盘，其余一字不改。

    python3 run.py strategies/小市值/froec_close.py --param weekday=1 ...
    python3 run.py strategies/小市值/froec_close.py --param weekday=2 ...

与 `froec_traded.py`（实盘 FROEC 账户绑的那份）**只差一个时刻**：

    froec_traded   run_weekly(rebalance, weekday=N, time='09:30')   -> OPEN 相位
    本版           run_weekly(rebalance, weekday=N, time='14:55')   -> INTRADAY 相位

🔴 **在这个引擎里，"尾盘"的实质是【用当日收盘价成交】。**
  引擎没有分时线，`_phase()` 把 `09:30 < t < 15:00` 全归 INTRADAY，
  该相位一律用**当日收盘价**撮合；而 `09:30` 属 OPEN 相位，用**开盘价**。
  所以 14:00 与 14:55 之间没有任何区别（CLAUDE.md 记过一次，别再试），
  真正被改变的是 **开盘价 -> 收盘价** 这一件事。
  ★ 选它 14:55 而不是 14:00 的唯一理由是**任务顺序**：同一天里
    `check_limit_up`（exit_time，默认 14:00）与 `stop_check` 先跑，
    调仓排在它们之后 —— 那才是"尾盘"该有的先后。

★ **不含前视**：`rebalance` 里取的是 `context.previous_date`，
  选股仍然只用**前一交易日**收盘的数据，改的只是这一篮子在哪个时点成交。

★ **一个字都没有复述 `froec_traded` 的参数** —— 它被 `importlib` 私有加载，
  `initialize` 原样转发（同 froec_traded 加载 froec 的做法）。
  凭印象拼一份 `_TRADED = {...}` 是本项目栽过的坑：实际设的是
  `paused_in_pool=0 / kcb_688_only=0 / weekday=2`，而我曾拼成
  `stop_loss/stop_intraday/weekday` —— 参数不同 -> 建仓股数就不同 ->
  整条路径不同，那一批结论全部作废。

🔴 **等价性自证**：`--param rebal_time=09:30` 时本版必须与 `froec_traded`
  **逐位一致**（权益指纹相同）。这一步不能省 —— 它证明"差异只来自时点"，
  而不是转发时漏了什么。实测指纹 `42e8994fe108` 两版相同 ✅

🔴🔴 **实测结论：尾盘买入【明显更差】，不采纳。**（2026-09-19）

全程 2016-01-01~2026-09-18，本金 10 万，默认成本，实盘那套参数
（lu_buy_only=1 lu_since_start=1 stop_loss=0.35 stop_intraday=1）：

    配置        年化      回撤     夏普    笔数   年换手    期末
    开盘 wd1  41.17%   39.86%   1.342    903    9.65   400.7 万
    尾盘 wd1  31.72%   43.12%   1.106    907    9.62   191.0 万   -9.45pp
    开盘 wd2  43.93%   40.55%   1.433    885    9.16   493.3 万
    尾盘 wd2  34.88%   42.32%   1.200    897    9.92   246.0 万   -9.06pp

逐年独立（每年重置 10 万，11 年）：

    wd1   均差 -9.37pp  中位 -3.89pp  胜 4/11  sd 20.62  t=-1.51
    wd2   均差 -7.45pp  中位 -8.91pp  胜 2/11  sd 14.16  t=-1.74
    去掉最好最差：wd1 -4.52pp / wd2 -8.21pp（**符号不翻**）

★ **两个相位档方向一致**（全程与逐年都是负），这比单个 t 值更硬 ——
  同「三个市值档上重复出现」那条的逻辑。
🔴 **不是换手变了**：笔数 903/907、885/897，年换手 9.65/9.62、9.16/9.92
  —— 几乎相同。费用看着少了（9.5 万 -> 6.7 万）是因为**资金少**
  （期末 191 万 vs 401 万，费用按成交额收），不是交易少了。

**机制是可量化的**：小市值票在周内前两个交易日**日内上涨**，
尾盘买等于每次买贵。面板实测（2016 起，每日流通市值最小 300 只的
`close/open - 1`）：

    周内第 1 个交易日   均值 +0.129%   中位 +0.380%
    周内第 2 个交易日   均值 +0.235%   中位 +0.328%
    周内第 3 / 4 / 5 日 +0.049% / -0.075% / -0.093%

—— 恰好**第 1、2 日最贵**，而那正是这个策略调仓的两天。
每次贵 0.13~0.24%、年调仓约 50 次，再经十年复利，就是这 7~9pp。

⚠ 这个引擎**没有分时线**：`09:30 < t < 15:00` 全归 INTRADAY 相位、
  一律用**当日收盘价**撮合。所以本版测的严格来说是「用收盘价成交」，
  而真实尾盘（14:55 的分时价）与收盘价之间还有差 —— 不过方向不会变，
  因为日内上涨是**全天累积**的。
"""
import importlib.util
import os

from assay.api import *          # noqa: F401,F403

_BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     'froec_traded.py')
_spec = importlib.util.spec_from_file_location('_froec_traded_close', _BASE)
_traded = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_traded)

# 复用实现，避免两份代码分叉（froec.py / froec_traded.py 修了本版自动跟随）
SQL = _traded.SQL
EXCL_IND = _traded.EXCL_IND
prepare = _traded.prepare
rebalance = _traded.rebalance
check_limit_up = _traded.check_limit_up
stop_check = _traded.stop_check
stop_filter = _traded.stop_filter
pf_check = _traded.pf_check
_bench_up = _traded._bench_up
_do_buy = _traded._do_buy
rebalance_buy = _traded.rebalance_buy

def initialize(context):
    # ---- 调仓时刻：尾盘 ----
    # 14:55 与 14:00 的**成交价完全相同**（同一 INTRADAY 相位、都用当日
    # 收盘价），取 14:55 的唯一理由是**任务顺序**：排在 exit_time(14:00)
    # 的炸板离场与 stop_time 的止损之后，那才是"尾盘"该有的先后。
    # ★ 默认值写成**字面量**而不是模块级常量 —— 看板的参数解析只认
    #   `getattr(g,'x',字面量)`，用常量的话这一项在回测页上**看不见**
    #   （而看不见的参数没人会去改）。
    g.rebal_time = getattr(g, 'rebal_time', '14:55')
    _traded.initialize(context)
