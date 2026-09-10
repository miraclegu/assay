# -*- coding: utf-8 -*-
"""FROEC-CUTGRID：把 PB 与 ROE 加速度的**切点比例**做成参数，用来跑网格。

原版写死两个比例（`froec.py` 的 SQL 参数）：

    pbcut  = floor(0.5 * n)     PB 升序取前 **50%**
    roecut = floor(0.1 * n2)    ROE 加速度降序取前 **10%**

★ 已有的 `pb_fixed` / `roe_fixed` 是**固定名次**（取前 N 名），不是比例 ——
  它们回答的是另一个问题（"池子大小固定"vs"按比例走"），已在 froec.py
  的注释里测过。本文件要网格的是**比例**本身。

参数（都用 `--param` 传）：

    pb_pct   默认 0.5    PB 前多少比例
    roe_pct  默认 0.1    ROE 加速度前多少比例

🔴 **默认值 = 原版**，所以不带参数跑出来必须与 froec_traded **逐位一致**
  （selftest 里该钉这条）。
🔴 **加载 froec_traded 而不是自己拼参数** —— 上一轮我在 froec_yearly_ew 里
  凭印象写了一份 `_TRADED`，结果两版参数不同、建仓日买的股数就不一样，
  整批对比结论作废。要"在 X 上只改一处"，就必须**加载 X 本身**。

★ 实现只动 SQL 的两个模板参数：`context.data.query(...)` 的 `pbcut` /
  `roecut` 两个字符串。WHERE / ORDER BY / LIMIT 一个字不动。

🔴🔴 **回测结论（2026-09-10，逐年独立 2016~2026，每年重置 50 万）：全不显著。**

5×5 网格（pb 0.3~0.7 × roe 0.05~0.20）的 24 个非原版格子，**没有一个 |t|>2.23**。
链乘年化最高的 `pb0.7/roe0.075 = 49.68%`（原版 41.84%），但逐年配对
均差 +6.63pp、中位 +7.50、胜 8/11、**sd 20.91、t=+1.05**；去掉 2025 只剩 +3.55pp。
2025 +37.46pp 与 2026 −41.60pp 一正一负几乎抵消。
🔴 **曲面是噪声图**（无单调性）；25 格里挑最大值，t 本来就虚高。

PB 单独看（固定 roe=0.10，10 档）：
    0.3  25.34%（−18.14）   0.4  32.19%   **0.5  41.84%（原版）**   0.6  35.33%
    0.7  45.93%（+2.66）    0.8  36.34%   0.9  41.91%   0.95 39.40%
    0.98 45.87%             1.0  45.03%（+2.34，t=+0.19）
★ 收紧一致有害、放松无益；**放松到 0.9 以上 sd 从 15 跳到 34~46**（翻 3 倍）。
★ 原版 0.5 恰在**回撤最小（19.6%）、夏普最高（1.47）**处。
★ 「取前 95%/98% 剔掉极端差公司」实测**剔不出价值** —— 后面两道筛子
  （ROE 前 10% + 市值最小）已经把那些票挡掉了。

ROE 单独看（固定 pb=1.0，10 档）：**0.10 是单峰最优**（45.03%、夏普 1.39），
两侧都下滑；**完全不筛（1.0）只有 33.07%，低 12pp** —— 证明 ROE 筛选有贡献。

★ 结论：**原版 0.5 / 0.10 不用改。**详见 CLAUDE.md「froec 三层结构的因子归因」。
"""

import importlib.util
import os

_HERE = os.path.dirname(os.path.abspath(__file__))


def _private(name):
    spec = importlib.util.spec_from_file_location(
        'froec_cutgrid__traded', os.path.join(_HERE, name))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


_traded = _private('froec_traded.py')
_base = _traded._base


def _patch_query():
    """把 froec 的 `context.data.query` 换成会改写 pbcut/roecut 的版本。

    ★ 挂在**数据层**而不是复制 `rebalance`：那个函数 100 多行、含黑名单与
      补位逻辑，抄一份出来必然分叉（而分叉的那份看着完全正常）。
      这里只在参数流过的最后一刻改两个字符串。
    🔴 判据用 `kw` 里**有没有 pbcut** —— froec 只有候选池那条 SQL 带它，
      其它 query 调用（比如取涨停日）不受影响。
    """
    g = _base.g

    def wrap(orig):
        def q(sql, **kw):
            if 'pbcut' in kw and 'roecut' in kw:
                pp = float(getattr(g, 'pb_pct', 0.5))
                rp = float(getattr(g, 'roe_pct', 0.1))
                # ★ 只在**用比例**时改写；`pb_fixed`/`roe_fixed` 显式给了
                #   固定名次时不动它们（那是另一个开关，不该被这里覆盖）。
                if not g.pb_fixed:
                    kw['pbcut'] = 'floor(%r * n)' % pp
                if not g.roe_fixed:
                    kw['roecut'] = 'floor(%r * n2)' % rp
            return orig(sql, **kw)
        return q
    return wrap


def initialize(context):
    g = _base.g
    g.pb_pct = getattr(g, 'pb_pct', 0.5)
    g.roe_pct = getattr(g, 'roe_pct', 0.1)
    _traded.initialize(context)
    # ★ 在 initialize **之后**包装：那时 context.data 已经是 GuardedFeed。
    d = context.data
    if not getattr(d, '_cutgrid_wrapped', False):
        d.query = _patch_query()(d.query)
        d._cutgrid_wrapped = True


prepare = _base.prepare
rebalance = _base.rebalance
rebalance_buy = _base.rebalance_buy
check_limit_up = _base.check_limit_up
stop_check = _base.stop_check
pf_check = _base.pf_check
