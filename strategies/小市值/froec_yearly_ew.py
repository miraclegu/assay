# -*- coding: utf-8 -*-
"""FROEC-YEARLY-EW：每年第一个调仓日把持仓【重新等权】一次。

**要解决什么。** 原版 `_do_buy` 只给**新买入**的票分钱：

    need = [目标池里还没持有的]
    per  = 现金 / len(need)          <- 只在新票之间分
    已持有的（keep）一分不动

所以已持有的票权重完全靠涨跌漂移。实测原版 2024 年：
**最大权重均值 20.4%、峰值 45.5%**（目标 10 只本该各 10%）—— 一只票涨到
接近半个组合，之后它自己的波动就决定了整个组合的波动，而"小市值分散"
这个前提已经不成立了。

**本版加什么。** 每年**第一个调仓日**，对目标池里的**所有**票（含已持有的）
下 `order_target_value(code, 总权益/len(target))` —— 涨多的减仓、涨少的加仓，
拉回等权。其余调仓日一切照旧（原版行为）。

🔴 **判据是「年份变了」而不是「1 月」**：调仓日是周频（weekday=2），某年
  第一个调仓日可能落在 1-02 也可能 1-08；写死 `month == 1` 会让一月里
  **每个**调仓日都再平衡一次（4~5 次），那不是年度再平衡。
★ 用 `g.ew_year` 记住上次做过的年份 —— 判据是"这一年做过没有"，
  与具体日期无关（同「判据要问日历，不要问策略做了什么」那条）。

🔴 **再平衡的分母用【总权益】不是【现金】**：现金那时通常很少（钱都在票里），
  按现金分会让减仓的票卖不动、加仓的票买不进。`order_target_value` 是
  "调到这个市值"，引擎会自动算买多少卖多少。

🔴 **成本不可忽略，而且这里必须诚实**：拉回等权要**双向**交易（涨多的卖、
  涨少的买），一次再平衡最多动 10 只票。froec 的实测费率是万1.5（小额多笔，
  5 元最低佣金对每笔都 binding），加上千五印花税与滑点 —— 这笔钱是确定支出，
  而"分散度改善"能不能赚回来是要测的。

--------------------------------------------------------------------
🔴 **回测结论见文件末尾的 RESULT 块**（逐年独立 2016~2026，每年重置 50 万）。
"""

import importlib.util
import os

_HERE = os.path.dirname(os.path.abspath(__file__))


def _private(name):
    """把 froec_traded.py 加载成一个**私有实例**。

    🔴 **加载 froec_traded 而不是自己拼参数。** 头一版我写了
      `_TRADED = {'stop_intraday': 1, 'stop_loss': 0.35, 'weekday': 2}`
      然后 `setattr` 无条件覆盖 —— 那三个值是我凭印象写的：froec_traded
      实际设的是 `paused_in_pool=0` / `kcb_688_only=0` / `weekday=2`，
      根本没有 stop_intraday 与 stop_loss。于是两版**参数不同**、
      建仓日买的股数就不一样（600054：829 股 vs 1010 股），
      **2016 建仓年凭空差出 +6.03pp**，而那一年根本没触发再平衡。
      整批对比结论因此全部作废。
    ★ 教训：要"在 X 上只加一条规则"，就必须**加载 X 本身**，
      不能照印象复述它的参数 —— 复述出来的那份看着一样、实际不是。
    ★ froec_traded 用的是 `getattr(g, k, 默认)` 保底写法（`--param` 仍生效），
      直接加载它就自动继承这个性质，不必自己实现一遍。
    """
    spec = importlib.util.spec_from_file_location(
        'froec_yearly_ew__traded', os.path.join(_HERE, name))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


_traded = _private('froec_traded.py')
_base = _traded._base          # froec_traded 自己加载的 froec 私有实例


def _do_buy(context, target):
    """原版的买入腿 + 【年度再平衡】。

    ★ 挂在 `_do_buy` 而不是 `rebalance`：买入腿可能被 `buy_delay` 推到
      别的时刻（`rebalance_buy` 也调它），挂在这里两条路径都覆盖到 ——
      挂在 rebalance 里的话开了 buy_delay 就静默失效。
    """
    g = _base.g
    y = str(context.current_date)[:4]
    done = (getattr(g, 'ew_year', None) == y)
    # 🔴 **`ew_year` 无条件先记**，与「那天有没有持仓」无关。
    #   头一版把两件事揉进一个条件（`未做过 and target and positions`）——
    #   于是建仓那年第一个调仓日 positions 为空、条件不成立、`ew_year`
    #   也没被记上，结果**第二个**调仓日触发了「等权」。那不是年度再平衡，
    #   是「建仓后立刻推平一次」，实测让 2016 年凭空多出 +8.46pp。
    #   ★ 判据是「这一年做过没有」，与「有没有持仓」是两件事。
    g.ew_year = y
    # 空仓时（建仓年的第一个调仓日）**天然就是等权**：原版 `_do_buy` 把现金
    # 平均分给新票，与再平衡是同一件事 —— 所以不需要特例分支，直接落到原版。
    if done or not (target and context.portfolio.positions):
        _base_do_buy(context, target)
        return
    # 🔴 分母用**总权益**：现金那时通常很少，按现金分会让减仓的卖不动。
    per = context.portfolio.total_value / len(target)
    for code in target:
        _base.order_target_value(code, per)
    _base.log.info('[EW] %s 第一个调仓日：%d 只重新等权，每只 %.0f 元',
                   y, len(target), per)


_base_do_buy = _base._do_buy
_base._do_buy = _do_buy


def initialize(context):
    _base.g.ew_year = None
    _traded.initialize(context)       # 🔴 转发：参数口径由 froec_traded 定


prepare = _base.prepare
rebalance = _base.rebalance
rebalance_buy = _base.rebalance_buy
check_limit_up = _base.check_limit_up
stop_check = _base.stop_check
pf_check = _base.pf_check
