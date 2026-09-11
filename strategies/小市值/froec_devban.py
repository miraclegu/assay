# -*- coding: utf-8 -*-
"""FROEC-DEVBAN：黑名单换判据 —— 从「20 日内涨停过」改成「价格偏离 MA20 太多」。

    现状（froec）   20 日内涨停过（`is_limit_up`）-> 剔出候选池
    本版           现价 / MA20 − 1 >= `dev_pct` -> 剔出候选池

★ **为什么这个换法讲得通**：黑名单的本意是「别追已经涨上去的」。而
  「涨停过没有」是个**离散、粗糙**的代理 —— 涨停后又跌回去的票照样被剔
  20 天，而慢慢阴涨 40% 的票一次都没涨停、完全不受限。
  「离 MA20 多远」直接量的就是「涨上去了多少」，是连续的。

参数：
    dev_pct    默认 0.20   现价高于 MA20 多少就剔（0.20 = 高 20%）
    dev_days   默认 0      **只看当日**偏离；> 0 则看最近 N 个交易日的
                           最大偏离（更"记仇"，与原版 20 日窗口可比）
    dev_keep   默认 1      沿用 `lu_buy_only` 的语义：黑名单**只挡买入**、
                           不导致卖出（CLAUDE.md 那条已定案的纪律）

🔴 **`limit_days=0` 就等价于关掉原版黑名单**（实测：窗口 `(d, d]` 为空）——
  所以本策略把它设成 0，避免两套黑名单叠着跑。基线对照也用这个参数，
  不必另写文件。

🔴 **MA20 不足 20 根时是【部分均值】**（`Bar.ma20` 用 SQL 窗口函数算，
  `ROWS BETWEEN 19 PRECEDING`）—— 建仓需要 `listed_days >= 250`，
  所以候选池里的票一定有 >= 20 根 K 线；但**回测区间的头 20 天**不然。
  本策略自己判 `g.dev_ready`（区间第 20 个交易日之后才启用），
  否则头几天会拿一个 3 根的"均线"去比，而那不报错。

★ 数据依赖是 `feed.py` 的**投影**改动（`Bar.ma20` + `guard.current` 透出），
  原策略行为一行未变 —— 加列时必须**两处一起改**：上一轮加 `change_pct`/`ma5`
  时漏了 `guard.current`，表现是策略侧永远收到 None、**规则整段空转且不报错**。
"""

import importlib.util
import os

_HERE = os.path.dirname(os.path.abspath(__file__))


def _private(name):
    spec = importlib.util.spec_from_file_location(
        'froec_devban__traded', os.path.join(_HERE, name))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


_traded = _private('froec_traded.py')
_base = _traded._base


def _dev_blacklist(context, cand, d):
    """**替换**原版黑名单：按「价格偏离 MA20」判，不看涨停。

    ★ 返回要剔除的代码集合（与原版 `blacklist` 同一契约）。
    🔴🔴 **必须用 T-1 的数据算偏离度，不能用 `current()`。**
      `rebalance` 在 **09:30（OPEN 相位）** 跑，那时 `current()` 只给
      开盘派生量 —— `close_hfq` / `ma20` **都没有**（PIT 防火墙的**正确**
      行为：调仓时刻不该知道当天收盘价）。头一版拿 `current()` 取，于是
      `r.get('ma20')` 永远是 None -> **规则整段空转**：四档 dev_pct 跑出
      **完全相同**的结果、且等于「完全关掉黑名单」，而它**不报错**。
      ★ 这个空转是靠「四档结果一模一样」这个反常现象发现的 —— 单看一档
        的数字完全正常。
    ★ 用 T-1 也与原版黑名单同口径：`had_limit_up` 查的是 `date <= 昨天`。
    """
    g = _base.g
    if not cand or not g.dev_ready:
        return set()
    cs = list(cand)
    t1 = context.previous_date
    if t1 is None:
        return set()
    # 取截至 T-1 的最近 N 根，自己算 MA 与偏离 —— `bar_range` 给的是
    # (date, high_hfq, low_hfq, close_hfq) 序列，不含 ma20 那一列。
    n = 20 if g.dev_days <= 0 else int(g.dev_days)
    lo = context.data.nth_prev_day(t1, n)
    rng = context.data.bar_range(cs, lo, t1) or {}
    out = set()
    for c, rows in rng.items():
        cl = [r[3] for r in rows if r[3]]
        # 🔴 不足 20 根**不判**（宁可不剔，也不拿一个 3 根的"均线"去比）
        if len(cl) < n:
            continue
        ma = sum(cl) / len(cl)
        if ma <= 0:
            continue
        # `dev_days<=0`：只看 T-1 当天偏离；否则看窗口内**最大**偏离
        dev = (cl[-1] / ma - 1.0) if g.dev_days <= 0 else (max(cl) / ma - 1.0)
        if dev >= g.dev_pct:
            out.add(c)
    return out


def _prepare(context):
    """原版 prepare + 记住「MA20 够不够 20 根」。"""
    g = _base.g
    _base_prepare(context)
    if not g.dev_ready:
        g.dev_n = getattr(g, 'dev_n', 0) + 1
        # 🔴 区间第 20 个交易日之后才启用 —— 之前 ma20 是部分均值。
        if g.dev_n >= 20:
            g.dev_ready = True
            _base.log.info('[DEV] %s 起启用偏离度黑名单（MA20 已满 20 根）',
                           context.current_date)


_base_prepare = _base.prepare
_base.prepare = _prepare
_base_blacklist = _base.blacklist          # 留着供对照，本版不叠加
_base.blacklist = _dev_blacklist


def initialize(context):
    g = _base.g
    g.dev_pct = getattr(g, 'dev_pct', 0.20)
    g.dev_days = getattr(g, 'dev_days', 0)
    g.dev_ready = False
    g.dev_n = 0
    _traded.initialize(context)   # 🔴 参数口径由 froec_traded 定，不自己拼
    # ★ 关掉原版黑名单（窗口 `(d, d]` 为空）—— 两套叠着跑测的是并集。
    g.limit_days = 0


prepare = _prepare
rebalance = _base.rebalance
rebalance_buy = _base.rebalance_buy
check_limit_up = _base.check_limit_up
stop_check = _base.stop_check
pf_check = _base.pf_check
