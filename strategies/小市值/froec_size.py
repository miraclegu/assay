# -*- coding: utf-8 -*-
"""froec_traded + 【仓位管理】三个方案（A/B/C），用一个开关切。

🔴 用户 2026-09-22 报的现象：「有时候仓位会发生比较重大的漂移，特别是中间有
  涨停炸板卖出之后，突然留下大笔现金……买入的单只票会占总仓位的 30%……
  再调仓时卖出一个 10% 的票需要买入 3 个，这 3 个全部为 3.3% 左右」。

根因就是 froec.py `_do_buy` 的一行：

    per = context.portfolio.cash / len(need)

**分母是"今天要买几只"，与"现有持仓每只多大"毫无关系。** 于是两个方向都会崩：
现金多而要买的少 -> 超重；现金少而要买的多 -> 过轻。

实测（2020-01-01~2026-09-21，实盘那套参数，40 万，默认成本）：
    547 笔新建仓里  一进来就 >20% 的 44 笔(8.0%)、一进来就 <5% 的 35 笔(6.4%)
    当天只新买 1 只时（103 次）那一只平均 16.4%、最大 55.3%
    持仓层面：单只 >20% 的交易日占 34.2%、>30% 占 15.5%、峰值 57.3%
🔴 **超重是【买出来的】不是涨出来的**：27 个 >25% 的持仓里 26 个建仓当天就 >20%
  （均 31.7%）。这条决定了 A 就能治根，不必去削赢家。

三个方案（`bal_mode`）：

    off  原版，逐位等价（自证用）
    A    新买的每只按【目标每只 = 权益 / stock_num】下单，现金不够就**等比缩**；
         多余现金留着。零额外交易，只治"超重"。
    B    A + 剩余现金【补进已持有且仍在目标池里的低配持仓】（按缺口从大到小）。
         治"超重"且不降仓位，代价是多几笔买入。
    C    B + 【先削】权重 > bal_cap × 目标 的持仓减回目标，腾出的钱进买入腿。
         唯一能治"过轻"的，也是唯一能压掉存量超重的。
    D    等权**满仓**：分母不是 stock_num，而是【实际会持有几只】
         (持仓数 + 新买数)，买完把剩余现金按缺口补进全部持仓。
    E    **买入时的带宽约束 + 定向再平衡**（用户 2026-09-22 设计）：
         新买的每只必须落在 [lo x per, up x per]；越界才动，而且只动
         **权重最多/最少的 bal_n 只**。带内一个字都不动（走原版）。

E 的完整设计与边界见本文件末尾的 `_do_buy_balanced` 注释。三条定位：
  🔴 **只在买入腿检查，不做日常再平衡** —— 实测 27 个 >25% 的超重持仓里
    26 个是建仓当天就 >20% 的，超重是**买出来的**，在买入那一刻拦住就够。
    存量漂移做成默认关的 `bal_drift`（证据说它只占 1/27）。
  🔴 **调整目标是回到【带内边界】而不是正中** —— 调到正中的话每个调仓日
    都会动，那就退化成 D（笔数 538 -> 1069）。
  ⚠ **它大概率测不出收益差异**：动得比 D 少，而 D 已经是 -3.52pp / t=-1.07，
    而 froec 自己的扰动噪声极差是 3.00pp。所以判据要落在**最大权重分布与
    回撤**，采纳的条件是"排除了显著变差"，不是"更好"。

🔴🔴 **D 是 A/B/C 跑完之后才看出来必须要有的那一档。** A/B/C 都把仓位做平了
  （建仓 >20% 8.0%->0.0%、建仓 <5% 6.4%->0.0%、单只 >20% 的交易日 34.2%->0.0%、
  最大权重均值 19.8%->10.7%），**而代价全在仓位上**：平均仓位 93.1% -> 82.8%，
  全程年化 52.47% -> 37.7%。也就是说 A/B/C 的分母写死成 `stock_num`，
  而黑名单/炸板让实际持仓常年只有 8.55 只 —— 剩下那 1.45 个名额的钱
  **就一直躺着**。D 把分母换成"实际持有几只"，于是**既平衡又满仓**。
  ★ 所以 A/B/C 那 −14.7pp **不能读成"平衡有害"** —— 它量的是"少投 10pp 仓位"
    的代价（同 P1 那条「降仓是削回撤的，不是提收益的」）。两件事要分开，
    而 D 就是把它们分开的那一档。

🔴 **C 不削【前一日涨停】的票**（用户 2026-09-22 明确要求）。判据取
  `g.high_limit` —— froec.py 的 `prepare` 里用**前一交易日**的 bar 算的
  `{c for c, b in bars.items() if b.limit_up}`，正是"前一日收盘封住"。
  理由也站得住：froec 自己的 `check_limit_up` 就把涨停持仓豁免在卖出之外
  （`code not in g.high_limit`），削它等于在同一件事上两套口径。
  ★ 而且涨停价上本来也卖不掉（broker 有「涨跌停无对手盘」），
    硬下单只会换来一条拒单 —— 显式跳过比让它去撞墙干净。

★ **froec.py / froec_traded.py 一个字节没改**：`importlib` 私有加载
  froec_traded，再换它私有实例里的 `_do_buy` 这一个模块属性。
  `rebalance` / `rebalance_buy` 里写的是 `_do_buy(...)`，那是**模块全局查找**，
  换得掉；而 `run_daily` 注册的是函数对象、事后换模块属性对它无效 ——
  这两件事**别记反**（本项目为此踩过两次）。

参数：
    bal_mode   默认 'off'   off / A / B / C
    bal_cap    默认 1.5     C 的触发带：权重 > cap × 目标才削
    bal_min    默认 0.25    B 的最小补仓：缺口 > min × 目标 才补
                            （否则撒胡椒面，而单笔最低佣金 5 元对每笔都 binding）
  --- 以下只对 E ---
    bal_base   默认 'held'  per 的分母：held=实际会持有几只 / num=stock_num
                            🔴 'num' 已被**显著否证**（A/B/C，t=-4.51，
                            劣化全部来自少投 10pp 仓位）
    bal_up     默认 1.6     上限 = up x per（per=10% 时即 16%）
    bal_lo     默认 0.5     下限 = lo x per（即 5%）
    bal_n      默认 2       再平衡只动权重最多/最少的 n 只
    bal_amt    默认 0.15    单笔调整金额下限 = amt x per（低于它不动）
    bal_drift  默认 0       是否也检查【存量漂移】超过 up 的持仓
"""
import importlib.util
import os

from assay.api import g, log, order_target_value

_HERE = os.path.dirname(os.path.abspath(__file__))


def _private(fn):
    """私有实例：module_from_spec 每次都是新对象，不影响该文件的其它使用者。"""
    p = os.path.join(_HERE, fn)
    spec = importlib.util.spec_from_file_location('_priv_' + fn[:-3], p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


_traded = _private('froec_traded.py')
_fb = _traded._base                 # froec_traded 私有加载的那个 froec 实例
_orig_do_buy = _fb._do_buy

SQL = _fb.SQL
EXCL_IND = _fb.EXCL_IND
prepare = _traded.prepare
rebalance = _traded.rebalance
check_limit_up = _traded.check_limit_up
stop_check = _traded.stop_check
stop_filter = _traded.stop_filter
pf_check = _traded.pf_check
rebalance_buy = _traded.rebalance_buy


def _bal_trim(pf, need_cash, per, lo, up, n, amt):
    """从权重【最多的 n 只】削出 need_cash，返回实际削出多少。

    🔴 三道剔除，每一道对应一种"不处理也不报错"的坏法：
      · `code in g.high_limit`（**前一日涨停**）—— 用户 2026-09-22 明确要求。
        判据取 froec.prepare 里用**前一交易日** bar 算的那个集合，
        与 froec 自己的 `check_limit_up` 豁免同一口径（那边写的是
        `code not in g.high_limit` 才卖）。★ 涨停价上本来也卖不掉
        （broker 有「涨跌停无对手盘」），硬下单只换来一条拒单。
      · `value <= per` —— 削带内的票会**制造新的下限违规**，下个调仓日
        又得补回来，**来回付两次费**。
      · 单笔 < amt —— froec 单笔 4~5 万而**最低佣金 5 元对每笔都 binding**
        （实测真实费率万1.5 不是万0.86），撒胡椒面是纯付费。
    ★ 削到 `per` 为止，不削到下限 —— 目标是"回到带内"不是"拉平"。
    """
    got, hl = 0.0, g.high_limit
    # ★ 单独计一笔"本来该削、因为前一日涨停而跳过"的 —— 否则这条豁免
    #   有没有被用到**根本看不出来**（mode C 那一轮就是：B/C 指纹逐位相同，
    #   削一次都没触发，而我当时差点说成"涨停豁免起作用了"）。
    for c in sorted(pf.positions, key=lambda x: -pf.positions[x].value)[:int(n)]:
        if c in hl and pf.positions[c].value > per:
            g.bal_log['hl_skip'] += 1
    cand = [c for c in sorted(pf.positions,
                              key=lambda x: -pf.positions[x].value)
            if c not in hl and pf.positions[c].value > per][:int(n)]
    for code in cand:
        if got >= need_cash:
            break
        p = pf.positions[code]
        cut = min(p.value - per, need_cash - got)
        if cut < per * amt:
            continue
        order_target_value(code, p.value - cut)
        got += cut
        g.bal_log['trim'] += 1
    return got


def _bal_spread(pf, per, lo, up, n, amt):
    """把剩余现金摊给权重【最少的 n 只】，每只最多补到 up。"""
    cand = sorted(pf.positions, key=lambda x: pf.positions[x].value)[:int(n)]
    for code in cand:
        if pf.cash <= 0:
            break
        p = pf.positions[code]
        add = min(up - p.value, pf.cash)
        if add < per * amt:
            continue
        order_target_value(code, p.value + add)
        g.bal_log['spread'] += 1


def _do_buy_E(context, target):
    """E：买入时的带宽约束 + 只动最多/最少的 n 只。

    顺序是 **先削 -> 再买 -> 后摊**，不能反：`order_target_value` 立即撮合并
    更新 cash，先买会因为现金不够被等比缩，削出来的钱就白削了。
    """
    pf = context.portfolio
    need = [c for c in target if c not in pf.positions]
    need = need[:max(0, len(target) - len(pf.positions))]

    # ★ per 的分母【只算一次】：含全部当前持仓（涨停豁免 / 黑名单 keep 的
    #   也确实持有，漏掉它们会让 per 偏大、所有人都"过轻"），加上本来打算买
    #   几只。后面"少买一只"时**不重算** —— 少一只就是少投一点，那正是
    #   ②「宁可少买一只也不要一只票低于下限」这个取舍本身。
    base = (len(pf.positions) + len(need)) if g.bal_base == 'held' \
        else int(g.stock_num)
    per = pf.total_value / max(1, base)
    lo, up = per * float(g.bal_lo), per * float(g.bal_up)
    n, amt = int(g.bal_n), float(g.bal_amt)

    # ---- 存量漂移（默认关）----
    if int(g.bal_drift):
        for code in sorted(pf.positions,
                           key=lambda x: -pf.positions[x].value):
            if code in g.high_limit:
                continue
            p = pf.positions[code]
            if p.value > up:
                order_target_value(code, per)
                g.bal_log['drift'] += 1

    # 🔴 没有要买的就**什么都不做** —— 原版此时也什么都不做。
    #   第一版这里调了 `_bal_spread`，于是 `up=999/lo=0`（本该等价）那一跑
    #   把现金全倒进了最小的 2 只，**等价性自证当场报不同**
    #   （f75eb0f80824 vs 17eb7bcb4fd1）。E 的触发点是**买入**，
    #   没有买入就没有触发；攒下的现金会在下一个调仓日走「超重」那条支线。
    if not need:
        return

    raw = pf.cash / len(need)
    if lo <= raw <= up:
        # ---- 带内：一个字都不动，与原版逐字相同 ----
        for code in need:
            order_target_value(code, raw)
        g.bal_log['inband'] += 1
        return

    if raw < lo:
        # ---- 过轻：从最多的 n 只削 ----
        g.bal_log['light'] += 1
        _bal_trim(pf, lo * len(need) - pf.cash, per, lo, up, n, amt)
        # 🔴 削不够 -> **少买一只**（等比缩就是又回到「3.3% 三只」那个原问题：
        #   一只 3% 的仓位既不影响组合又占着一个名额）。必须留痕 ——
        #   静默少买一只比超重更难查。
        while len(need) > 1 and pf.cash < lo * len(need):
            drop = need.pop()          # need 按流通市值升序 -> 丢排名最后那只
            g.bal_log['skip'] += 1
            log.info('[BAL] 现金只够 %d 只到下限，少买 %s（削不够）',
                     len(need), drop)
        if pf.cash < lo:
            g.bal_log['skip'] += len(need)
            log.info('[BAL] 现金 %.0f 连一只的下限 %.0f 都不到，本轮不买',
                     pf.cash, lo)
            return
        each = min(up, pf.cash / len(need))
        for code in need:
            order_target_value(code, each)
        log.info('[BALCNT] %s', dict(sorted(g.bal_log.items())))
        return

    # ---- 超重：每只只买 up，多出来的摊给最少的 n 只 ----
    g.bal_log['heavy'] += 1
    for code in need:
        order_target_value(code, up)
    _bal_spread(pf, per, lo, up, n, amt)
    log.info('[BALCNT] %s', dict(sorted(g.bal_log.items())))


def _do_buy_balanced(context, target):
    mode = str(getattr(g, 'bal_mode', 'off')).upper()
    if mode not in ('A', 'B', 'C', 'D', 'E'):
        return _orig_do_buy(context, target)
    if mode == 'E':
        return _do_buy_E(context, target)

    pf = context.portfolio
    _need0 = [c for c in target if c not in pf.positions]
    _need0 = _need0[:max(0, len(target) - len(pf.positions))]
    if mode == 'D':
        # 🔴 分母是【实际会持有几只】，不是 stock_num —— 黑名单/炸板让持仓
        #   常年只有 8.55 只，按 10 分母的话剩下那 1.45 份的钱一直躺着。
        n = max(1, len(pf.positions) + len(_need0))
    else:
        n = max(1, int(g.stock_num))
    per = pf.total_value / n            # 目标每只
    cap = per * float(g.bal_cap)
    lo = per * (1.0 - float(g.bal_min))

    # ---- C：先削超重（前一日涨停的不削）----
    if mode == 'C':
        for code in sorted(pf.positions):
            if code in g.high_limit:    # 🔴 前一日涨停 -> 不削
                continue
            p = pf.positions[code]
            if p.value > cap:
                log.info('[BAL] 削 %s %.1f%% -> %.1f%%', code,
                         p.value / pf.total_value * 100, 100.0 / n)
                order_target_value(code, per)

    # ---- A：新买的按定额，现金不够【等比缩】----
    need = _need0
    if need:
        want = per * len(need)
        k = min(1.0, pf.cash / want) if want > 0 else 0.0
        for code in need:
            order_target_value(code, per * k)

    # ---- B/C/D：剩余现金补进低配持仓 ----
    #   B/C 只补【仍在目标池里的】；D 补**全部持仓**（留下来的都是有意留的：
    #   涨停豁免 / 黑名单 keep），否则现金又躺回去了。
    if mode in ('B', 'C', 'D'):
        held = (sorted(pf.positions) if mode == 'D'
                else [c for c in target if c in pf.positions])
        floor_ = 0.0 if mode == 'D' else per * float(g.bal_min)
        gaps = sorted(((per - pf.positions[c].value, c) for c in held),
                      reverse=True)
        for gap, code in gaps:
            if gap <= floor_ or pf.cash <= 0:
                break
            order_target_value(code, pf.positions[code].value
                               + min(gap, pf.cash))


def initialize(context):
    g.bal_mode = getattr(g, 'bal_mode', 'off')
    g.bal_cap = getattr(g, 'bal_cap', 1.5)
    g.bal_min = getattr(g, 'bal_min', 0.25)
    g.bal_base = getattr(g, 'bal_base', 'held')
    g.bal_up = getattr(g, 'bal_up', 1.6)
    g.bal_lo = getattr(g, 'bal_lo', 0.5)
    g.bal_n = getattr(g, 'bal_n', 2)
    g.bal_amt = getattr(g, 'bal_amt', 0.15)
    g.bal_drift = getattr(g, 'bal_drift', 0)
    g.bal_log = {k: 0 for k in
                 ('inband', 'light', 'heavy', 'trim', 'spread', 'skip',
                  'drift', 'hl_skip')}
    _traded.initialize(context)
    # 边界：带宽必须真的是一条带（lo 在 1 下、up 在 1 上），否则"回到带内"
    # 这个动作本身没有定义。**启动时就拒**，不要跑出一份看着正常的回测。
    # 🔴 这里原来还有一条 `bal_lo x stock_num > 1 就拒` —— **那条是错的**，
    #   而且它拦掉的正是默认配置（0.5 x 10 = 5 > 1）。`bal_lo` 是相对 `per`
    #   的比例，而 `per = 权益 / N`，所以 N 个下限之和 = lo x N x per
    #   = lo x 权益 <= 权益，**恒成立**，根本不需要这道检查。
    #   ★ 更值得记的是：我当时还拿 `lo=0.2 被拒` "验证"过它 ——
    #     **那验的是一个错的守卫**，它拒掉的是合法配置（下限 2% 权益）。
    #     判据比要证的事宽时，"变异被抓到"什么都不证明。
    if str(g.bal_mode).upper() == 'E':
        if not (0.0 <= float(g.bal_lo) < 1.0 < float(g.bal_up)):
            raise SystemExit('bal_lo/bal_up 必须满足 0 <= lo < 1 < up，'
                             '实得 lo=%s up=%s' % (g.bal_lo, g.bal_up))
    _fb._do_buy = _do_buy_balanced      # ← 换的是【被调用方】，不是注册的函数
