"""lv/sig.py —— 信号生成与落盘：重建 Portfolio -> 跑真引擎 ->
捕获委托 -> 落盘 / 定时。

🔴 **不重写任何交易规则**：止损、炸板离场、调仓都埋在策略 + 引擎里，
  这里只把 broker 换成 RecordingBroker（只记不成交），
  让策略跑自己的代码路径。所以实盘提示与回测行为天然同源。"""
import datetime
import importlib.util
import json
import os
import re
from .. import api
from ..broker import Cost, RecordingBroker
from ..context import Lot, Position
from ..engine import Engine
from ..feed import PanelFeed

from . import base as _base
from . import explain as _explain
from . import pos as _pos
from . import ver as _ver


def _load_snapshot(aid, sha):
    """从版本快照加载策略模块。**不读磁盘当前文件** ——
    账户绑的是那个快照，磁盘改了是另一个版本。

    ★ 快照是【目录】不是单文件：依赖（如 froec_traded -> froec）与主文件
      放在同一目录下，所以主文件里那句
      `spec_from_file_location(..., 同目录/froec.py)` 解析到的是
      **快照里的依赖**，不是磁盘上的当前版本。
    """
    v = _ver._version_row(aid, sha)
    full = v['code_sha256']
    p = os.path.join(_base.acct_dir(aid), 'code', full[:8], v.get('main') or '')
    if not os.path.exists(p):
        raise _base.LiveError('版本快照丢失：%s' % os.path.relpath(p, _base.ROOT))
    spec = importlib.util.spec_from_file_location('_live_%s_%s' % (aid, full[:8]), p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod, full



def _asof_factor(feed, codes, day):
    """{code: (hfq_factor, close_hfq, close_bfq)}，按 <= day 取最近一条。
    停牌股取最后已知值 —— 与引擎「按最后已知价挂账」一致。"""
    if not codes:
        return {}
    q = "','".join(codes)
    rows = feed.con.execute("""
        SELECT code, hfq_factor, close_hfq, close_bfq FROM (
          SELECT jq_code AS code, hfq_factor, close_hfq, close_bfq,
                 row_number() OVER (PARTITION BY jq_code ORDER BY date DESC) rn
          FROM read_parquet('%s/mart/panel_daily/panel_*.parquet')
          WHERE jq_code IN ('%s') AND date <= DATE '%s'
            AND date > DATE '%s' - INTERVAL 400 DAY
        ) WHERE rn = 1""" % (feed.root, q, day, day)).fetchall()
    return {r[0]: (r[1], r[2], r[3]) for r in rows}



def _seed(eng, book, feed, day_before):
    """把真实持仓播种进引擎的 Portfolio，状态等价于【day_before 收盘后】。

    ★ 单位换算。`Lot.shares` 是**后复权记账单位**，真实股数 = shares × factor。
      引擎的不变量是 `真实股数 == lot.shares × factor(当日)`：除权日
      broker.start_day 把 shares 缩 (1-frac)、factor 同步放大 1/(1-frac)，
      两者抵消（推导见 broker.py start_day 的注释）。所以：

          lot.shares      = 真实股数 / factor(day_before)
          lot.entry_price = 真实成交价 × factor(建仓日)

      entry_price 用**建仓日**因子是刻意的 —— 引擎在除权时【不缩 entry_price】
      （缩了会把分红错记成价差收益，见 broker.py 的推导）。播种要复现的是
      引擎的状态，不是"更合理的成本价"。

    ★ 播到 day_before 而不是当日：start_day(当日) 会用 _prev_factor 处理
      当日除权。若直接按当日因子播种，当天正好除权的票会被**缩两次**。
    """
    need = list(book)
    fac_now = _asof_factor(feed, need, day_before)
    entry_days = sorted({l['date'] for lots in book.values() for l in lots})
    fac_entry = {}
    for ed in entry_days:
        fac_entry[ed] = _asof_factor(
            feed, [c for c, lots in book.items()
                   if any(l['date'] == ed for l in lots)], ed)
    missing = [c for c in need if c not in fac_now]
    if missing:
        raise _base.LiveError('这些持仓在面板里查不到行情，无法估值：%s' % ', '.join(missing))
    for c, lots in book.items():
        f_now, close_hfq, _bfq = fac_now[c]
        f_now = f_now or 1.0
        pos = Position(code=c, lots=[], last_price=close_hfq or 0.0)
        for l in lots:
            f_e = (fac_entry.get(l['date'], {}).get(c) or (1.0,))[0] or 1.0
            pos.lots.append(Lot(shares=l['shares'] / f_now,
                                entry_date=l['date'],
                                entry_price=l['price'] * f_e))
        eng.pf.positions[c] = pos
        eng.broker._prev_factor[c] = f_now



def _extend_ordinals(eng, feed, future_days):
    """把未来交易日并进周/月序号表，让 `_due` 能判断【下一个交易日】。

    ★ 必须用「历史 + 未来」的完整日历重算，不能只补未来那几天：
      本周/本月的桶在面板侧是**截断**的（面板只到最新数据日），
      截断的桶算出来的负序号（-1 = 本周最后一个交易日）是错的。
    """
    days = list(feed.trading_days) + [d for d in future_days
                                      if d > feed.trading_days[-1]]
    for key_fn, store in ((lambda x: x.isocalendar()[:2], eng._wk_ord),
                          (lambda x: (x.year, x.month), eng._mo_ord)):
        buckets = {}
        for d in days:
            buckets.setdefault(key_fn(d), []).append(d)
        for grp in buckets.values():
            n = len(grp)
            for j, d in enumerate(grp):
                store[d] = (j + 1, j - n)



def _run_tasks(eng, feed, day, freqs, data_day=None):
    """在 day 上执行指定频率的任务，返回这一轮记录到的委托。

    ★ `data_day` 是【行情所在的那一天】。判断调仓时 day 是**下一个交易日**，
      它还没有行情，而策略会调 `context.tradable(...)` 查当日停牌/涨跌停。
      这里让 broker 停留在最新数据日 —— 等价于假设
      「明天的可交易状态与今天相同」。这是**明确的近似**，不是 bug：
      明天谁停牌、谁一字板，今天物理上不可知。信号里会带这条告警。
    """
    if data_day is not None:
        eng.broker.date = data_day
    from ..engine import _phase
    eng.ctx.current_date = day
    prev = feed.prev_trading_day(day)
    if prev is None and feed.trading_days:
        prev = feed.trading_days[-1] if day > feed.trading_days[-1] else None
    eng.ctx.previous_date = prev
    n0 = len(eng.broker.orders)
    for t, freq, func, wd, md, ev, off in eng._tasks:
        if freq not in freqs:
            continue
        if freq == 'n':
            raise _base.LiveError('该策略用了 rebal_every（固定间隔调仓），'
                            '相位依赖回测起点的绝对序号，预览推不出 —— 拒绝出信号')
        if freq in ('w', 'm') and not eng._due(0, day, freq, wd, md, ev, off):
            continue
        ph = _phase(t)
        eng.broker.phase = ph
        eng.ctx.current_phase = ph
        eng.guard.set_clock(day, ph)
        func(eng.ctx)
    return eng.broker.orders[n0:]



WARMUP_DAYS = 30      # 重放窗口，要盖住 froec 的 limit_days=20



def _replay(eng, feed, rows, init_cash, days, flows=()):
    """在 warmup 窗口上**逐日重放** run_daily 任务，让策略自己把路径状态建起来。

    ★ 为什么不能只跑最后一天。froec 有三处【逐日累积】的状态：
        g.hold_history  20 日内持有过的票（配 had_limit_up 做涨停黑名单）
        g.stop_banned   止损冷静期
        g.pos_state     吊灯/移动止损的峰值与 ATR 窗口
      只跑一天，这些全是空的 —— 黑名单会漏、冷静期会失效。而且
      **不报错**，只是多买几只本不该买的票。实测就踩到了：账户持有
      603506 时，只跑一天的版本把它当成"没持有过"，与真实规则不符。

    重放的每一天都把持仓/现金**按成交流水复原到那天的真实状态**，
    所以 hold_history 记下的是你真实持有过的票，不是引擎自己模拟出来的。
    """
    for d in days:
        book = _pos.lots_asof(rows, d)
        eng.pf.positions.clear()
        _seed(eng, book, feed, feed.prev_trading_day(d) or d)
        eng.pf.cash = _pos.cash_asof(init_cash, rows, d, flows)
        eng.broker.start_day(d)
        _run_tasks(eng, feed, d, {'d'})



UPCOMING_DAYS = 15          # 日历条显示几个交易日（约三周）

LOOKAHEAD_DAYS = 70         # ★ 找"下次调仓"要看得更远：月频策略（红利是
                            #   run_monthly monthday=1）的下一次可能在 20+
                            #   个交易日后，只看 15 天会返回"没有下次调仓"
                            #   —— 那比不显示更糟，看着像策略不调仓了。



def upcoming_rebalance(eng, feed, from_day, days, n=LOOKAHEAD_DAYS):
    """未来 n 个交易日里哪几天是调仓日。

    ★ 这件事【不需要数据】—— 调仓日由日历序号决定（run_monthly 的"月内第 k
      个交易日"、run_weekly 的"周内第 k 个交易日"），而日历已经有到 2030。
      所以可以提前很久告诉你"哪天要调仓"，只是**清单**得等 T-1 收盘。

    为什么要提前提示：调仓日当天早上才打开看板就已经晚了 —— 09:30 开盘调仓，
    而信号是前一晚算的。提前几天知道日期才好安排。

    返回 [{date, wday, is_rebal}]，只含 from_day 之后的交易日。
    """
    fut = [d for d in days if d > from_day][:n]
    out = []
    for d in fut:
        due = False
        for t, freq, func, wd, md, ev, off in eng._tasks:
            if freq in ('w', 'm') and eng._due(0, d, freq, wd, md, ev, off):
                due = True
                break
        out.append({'date': d.isoformat(), 'wday': d.isoweekday(),
                    'is_rebal': due})
    return out



def _twopass(codes, px, money):
    """先按 money/n 预分配，买不进的不占份额，剩余再平分给买得进的。

    与 jq/strategies/hongli_preview.py 同算法 —— 实测 max/min 权重 1.015、
    闲置现金 0.4%（fixed 闲置 20.6%、seq 权重漂 1.34 倍）。
    """
    n = len(codes)
    if not n:
        return {}, money
    lim = {c: round(px[c] * _base.PRICE_BUFFER, 2) for c in codes}
    plan, spent = {}, 0.0
    per = money / n
    for c in codes:
        a = _base.LOT_SIZE * int(per / lim[c] / _base.LOT_SIZE) if lim[c] > 0 else 0
        if a <= 0:
            continue
        plan[c] = a
        spent += a * lim[c]
    if plan:
        add = (money - spent) / len(plan)
        for c in list(plan):
            m = _base.LOT_SIZE * int(add / lim[c] / _base.LOT_SIZE) if lim[c] > 0 else 0
            if m > 0:
                plan[c] += m
                spent += m * lim[c]
    return {c: (plan[c], lim[c]) for c in plan}, money - spent



def build_signal(aid, datalake=None, asof=None, code_sha=None, params=None):
    """算出【下一个交易日】的待办。返回可直接落盘的 dict。

    ## 三个可选参数只为【复算历史某一期】（`explain_recompute`）

    `asof` = 用哪一天的收盘数据（把面板截到那天）；`code_sha` / `params` =
    用当时那个版本与参数。三个都从那一期的信号文件里取 —— 信号里本来就
    存着 `data_asof` / `code_sha256` / `params`，所以复算是**照当时的账**重跑，
    不是照今天的账。

    🔴 复算出来的东西**不许写回信号文件**。信号是 append-only 的证据：
      「当时说了什么」。把今天算的东西塞进去就是在篡改记录 ——
      而面板会被修正（本项目修过 volume 74,952 行、复权因子），
      所以复算结果**可能与当时不同**。判据不靠猜：`explain_recompute` 会把
      复算出的买/卖/持有与信号里存的逐个比，不一致就明说"这不是当时那份"。

    ★ 持仓与现金也要按 `t1` 收盘后的状态取（`lots_asof` / `cash_asof`），
      不能用今天的 —— 09-01 那期是建仓日，当天买入的票在**发信号那一刻
      还不存在**。用今天的持仓复算，会得出"它当时就持有这 10 只"的荒唐结论。
    """
    acct = _base.get_account(aid)
    sha = code_sha or acct.get('code_sha256')
    if not sha:
        raise _base.LiveError('账户 %s 还没绑定策略版本' % aid)
    rows0 = _base.fills(aid)

    end = _base._d(asof).isoformat() if asof else datetime.date.today().isoformat()
    feed = PanelFeed(acct.get('warmup_start') or _base.DEFAULT_WARMUP_START,
                     end, root=datalake)
    t1 = feed.trading_days[-1]                    # 最新数据日
    t0 = feed.prev_trading_day(t1)
    t = _base.next_trading_day(t1)                      # 下一个交易日；拿不到就报错

    if asof:
        book = _pos.lots_asof(rows0, t1)
        money = _pos.cash_asof(float(acct.get('init_cash') or 0), rows0, t1,
                               _pos.cashflows(aid))
    else:
        book = _pos.fifo_lots(rows0)
        money = _pos.cash(aid)

    mod, full_sha = _load_snapshot(aid, sha)
    eng = Engine(mod, feed, cash=money, cost=Cost(),
                 params=(acct.get('params') if params is None else params) or {})
    rb = RecordingBroker(eng.pf, feed, eng.cost)
    eng.broker = rb
    # ★ Context 里存的字段名是 `_broker`。写 ctx.broker 只会凭空多出一个
    #   属性，上下文仍指着原来那个 broker —— 而它 date=None，
    #   于是 context.tradable() 会拿 DATE 'None' 去查行情。
    #   这类"设了个没人读的属性"是静默失败，必须直接写 _broker。
    eng.ctx._broker = rb
    # 选股理由的记账代理。**只记不改**：ctx.data 换成透传的 RecordingFeed、
    # filter_tradable 换成记账包装，判定一律还是原来那套（见 lv/explain.py）。
    rec = _explain.attach(eng, rb)
    eng._boot()
    try:
        _extend_ordinals(eng, feed, _base.calendar_days())
        # --- 1) 重放 warmup：建起逐日累积的路径状态（黑名单/冷静期/峰值）---
        rows = rows0
        init = float(acct.get('init_cash') or 0)
        warm = feed.trading_days[-WARMUP_DAYS:]
        _replay(eng, feed, rows, init, warm[:-1], _pos.cashflows(aid))
        # --- 2) 离场检查：在【最新数据日】跑 run_daily 类任务 ---
        #     语义是「今天收盘触发 -> 明天开盘卖」。本地只有日线，
        #     盘中实时判定物理上做不到，这是能做到的最早时点。
        eng.pf.positions.clear()
        _seed(eng, book, feed, t0 or t1)
        eng.pf.cash = money
        rb.start_day(t1)                          # 刷新 last_price / 处理当日除权
        n0 = len(rb.orders)
        # ★ 记账从这里才开始 —— 前面 warmup 重放了 30 天，每天都记的话步骤表里
        #   会有几百条无关调用，而要看的是最后这两轮（离场检查 + 调仓）。
        rec.arm('exit')
        exit_orders = _run_tasks(eng, feed, t1, {'d'})
        # --- 3) 调仓检查：下一个交易日是不是调仓日 ---
        rec.arm('rebalance')
        rebal_orders = _run_tasks(eng, feed, t, {'w', 'm'}, data_day=t1)
        rec.disarm()
        is_rebal = bool(rebal_orders)
        # --- 4) 未来调仓日：纯日历，可以提前很久算出来 ---
        cal_days = _base.calendar_days()
        upcoming = upcoming_rebalance(eng, feed, t, cal_days)
        held_after = dict(eng.pf.positions)        # RecordingBroker 不成交，等于真实持仓
        g_params, g_sets = _explain.g_state(eng)
    finally:
        api._unbind()

    # ---- 汇总成买卖清单 ----
    reason = {}
    for o in exit_orders:
        if o['target_value'] == 0:
            reason[o['code']] = 'stop' if o['kind'] == 'stop' else 'limit_up_exit'
    sells, targets = {}, []
    for o in rebal_orders:
        if o['target_value'] == 0:
            reason.setdefault(o['code'], 'rebalance_out')
        elif o['code'] not in targets:
            targets.append(o['code'])
    for c in reason:
        if c in book:
            sells[c] = sum(l['shares'] for l in book[c])

    px = _asof_factor(feed, list(set(targets) | set(book)), t1)
    names = _names(feed, list(set(targets) | set(book)), t1)

    # 卖出腾出的现金按 T-1 收盘估（真实成交价当然不同，这里只为算买入股数）
    freed = sum(sells[c] * (px.get(c, (0, 0, 0))[2] or 0) for c in sells)
    # 🔴 "持有不动"就是【全部持仓 − 要卖的】，**两种日子同一个定义**。
    #   原来调仓日走的是「targets ∩ 持仓」那一支，而 `targets` 是从**委托**
    #   反推的 —— 策略对"留着不动"的持仓根本不下委托（froec 的 _do_buy 只买
    #   `need` = 目标 − 已持有），于是那些票在 targets 里没有，
    #   在页面上**既不在卖出也不在持有不动**，凭空消失。
    #   实测（真实 froec 的 10 只持仓 + 调仓日）：卖 2 只，另外 8 只哪儿都不在。
    #   ★ 非调仓日那一支当初已经因为同一个原因修过一次（targets 是空的，
    #     照它算会显示成空仓）—— 修的是同一件事的另一半，这次一起收干净。
    keep = [c for c in book if c not in sells]
    buys = [c for c in targets if c not in book or c in sells]
    buy_px = {c: (px.get(c, (0, 0, 0))[2] or 0) for c in buys}
    bad_px = [c for c in buys if not buy_px[c]]
    plan, left = _twopass([c for c in buys if buy_px[c]], buy_px, money + freed)

    warn = []
    cm = _base.calendar_meta()
    if (cm.get('source') or '') not in _base.AUTHORITATIVE_CAL:
        warn.append('交易日历来源是 %r，不是聚宽权威日历 —— 休市安排可能不准。'
                    '跑一次 datalake 的聚宽增量抽取即可覆盖。' % cm.get('source'))
    if t1 < (feed.trading_days[-1] if feed.trading_days else t1):
        warn.append('面板数据不是最新的')
    if bad_px:
        warn.append('这些目标票取不到 T-1 价格，已从买入清单剔除：%s' % ', '.join(bad_px))
    if (datetime.date.today() - t1).days > 4:
        warn.append('最新数据日是 %s，距今 %d 天 —— datalake 可能没刷新'
                    % (t1, (datetime.date.today() - t1).days))

    # ---- 选股理由：把记下来的过程拼成「候选池 + 每只票为什么」----
    # ★ 状态只从三个**事实**推：买了 / 持有着 / 卖了。不从"排名够不够"推
    #   —— 那是策略的规则，复述一遍就等于又抄了一份（见 lv/explain.py）。
    expl = _explain.assemble(rec, 'rebalance', picked=list(plan), held=keep,
                             sold=list(sells), names=names,
                             params=g_params, g_sets=g_sets)
    # ★ 候选池里大部分票不在买卖持有清单里，`names` 里没有它们 —— 不补的话
    #   表上从第 11 行起「名称」列全是代码，看着像数据缺失。一次查完（几百个
    #   代码一条 SQL），不要每行去查。
    _need = sorted({e['code'] for g in (expl.get('groups') or [])
                    for e in (g.get('rows') or []) if not e.get('name')})
    if _need:
        _nm = _names(feed, _need, t1)
        for g in expl['groups']:
            for e in g['rows']:
                if not e.get('name'):
                    e['name'] = _nm.get(e['code'], '')
    # ★ 一只票可能在多组里各有一行（红利 A/B 两袖）—— 理由要把两组的名次
    #   都带上，只报第一组会给出"第 245/332 名"这种看着莫名其妙的解释。
    why_of = {}
    for _g in (expl.get('groups') or []):
        for _e in _g.get('rows') or []:
            why_of.setdefault(_e['code'], []).append(_e)

    def _why(c):
        e = why_of.get(c)
        if e:
            return _explain.why_line(e, expl.get('n_groups') or 1)
        # ★ "不在候选池里"本身就是理由（调仓换出的票几乎都是这种）——
        #   返回空串会让页面上那一格空着，看着像没算出来。
        if expl.get('captured') and expl.get('pool_total'):
            return '不在本期候选池'
        return ''

    nxt_rb = next((x for x in upcoming if x['is_rebal']), None)
    if is_rebal:
        nxt_rb = {'date': t.isoformat(), 'wday': t.isoweekday(), 'is_rebal': True}
    days_until = None
    if nxt_rb:
        days_until = 0 if is_rebal else \
            (1 + next(i for i, x in enumerate(upcoming) if x['is_rebal']))

    fp = feed.fingerprint() if hasattr(feed, 'fingerprint') else {}
    return {
        'warnings': warn, 'calendar_source': cm.get('source'),
        # ★ 调仓日是【纯日历】的，所以提前算得出来；清单不是（要 T-1 数据）。
        #   页面上要把这两件事分清楚，别让人以为提前几天就能看到买什么。
        'upcoming': upcoming[:UPCOMING_DAYS],
        'next_rebalance': (nxt_rb or {}).get('date'),
        'days_until_rebalance': days_until,
        'account': aid, 'for_date': t.isoformat(), 'data_asof': t1.isoformat(),
        'built_at': _base._now(), 'code_sha256': full_sha, 'code_sha': full_sha[:8],
        'params': (acct.get('params') if params is None else params) or {},
        # ★ 复算出来的东西必须**自己标出来**：它与当时那份可能不同
        #   （面板被修正过），而两份长得一模一样。
        'recomputed': bool(asof),
        'is_rebalance_day': is_rebal,
        'cash': round(money, 2), 'freed_est': round(freed, 2),
        'left_est': round(left, 2),
        'sell': [{'code': c, 'name': names.get(c, ''), 'shares': sells[c],
                  'reason': reason[c], 'why': _why(c),
                  'ref_price': round(px.get(c, (0, 0, 0))[2] or 0, 2)}
                 for c in sorted(sells)],
        'buy': [{'code': c, 'name': names.get(c, ''), 'shares': plan[c][0],
                 'limit': plan[c][1], 'amount': round(plan[c][0] * plan[c][1], 2),
                 'ref_price': round(buy_px[c], 2), 'why': _why(c)}
                for c in sorted(plan, key=lambda x: -plan[x][0] * plan[x][1])],
        'hold': [{'code': c, 'name': names.get(c, ''),
                  'shares': sum(l['shares'] for l in book[c]),
                  'why': _why(c)} for c in sorted(keep)],
        'no_price': bad_px,
        # ★ 选股理由**与信号存在一起**：信号是每天一个 JSON、append-only 的证据，
        #   理由不跟着存的话，事后复盘只能看到"当时买了这几只"，
        #   而"当时为什么"要重跑策略才知道 —— 那时数据已经变了，重跑得出的
        #   理由**不是当时那个**（面板每天在长，as-of 的口径也会变）。
        'explain': expl,
        'data_fingerprint': fp,
        'held_codes': sorted(held_after),
    }



def _names(feed, codes, day):
    if not codes:
        return {}
    q = "','".join(codes)
    rows = feed.con.execute("""
        SELECT code, sec_name FROM (
          SELECT jq_code AS code, sec_name,
                 row_number() OVER (PARTITION BY jq_code ORDER BY date DESC) rn
          FROM read_parquet('%s/mart/panel_daily/panel_*.parquet')
          WHERE jq_code IN ('%s') AND date <= DATE '%s'
            AND date > DATE '%s' - INTERVAL 400 DAY
        ) WHERE rn = 1""" % (feed.root, q, day, day)).fetchall()
    return dict(rows)


# ============================ 落盘 / 定时 ============================


def signal_path(aid, for_date):
    return os.path.join(_base.acct_dir(aid), 'signals', '%s.json' % for_date)



def load_signal(aid, for_date):
    return _base._read_json(signal_path(aid, for_date), None)



def explain_side_path(aid, for_date):
    """事后复算出来的选股理由，落在 `signals/_explain/<date>.json`。

    🔴 **不写回信号文件**：那是 append-only 的证据（当时说了什么）。
      复算是**派生物**（现在照当时的账重算一遍），两者必须分开存 ——
      混在一起之后"这份理由是当时留下的还是后来算的"就分不出来了。
    ★ 但也**必须落盘**：只放进程内缓存的话，重启 serve.py 就没了，
      人每次打开页面都要点一次"复算"，而**页面上看着就是"没有理由"**
      （实测用户反馈：「我直接看不到上一期选股」）。
    🔴 **放【子目录】而不是与信号同名同层**（`<date>.explain.json`）：
      有三处在扫 `signals/` 下的 `*.json`（`sig.latest_signal` /
      `px.latest_signal` / `explain_history`），同层的话它们会把这份派生物
      当成信号读进去 —— `latest_signal` 取 `sorted(...)[-1]`，
      **哪个排最后取决于日期字符串**，也就是说它会不会出错**看运气**。
      挪进子目录之后 `os.listdir` 只看一层，结构上就撞不上。
    """
    return os.path.join(_base.acct_dir(aid), 'signals', '_explain', '%s.json' % for_date)



def load_explain_side(aid, for_date, sha=None):
    """读旁挂的复算结果。**绑定版本变了就当没有**。

    复算里"指标"那一半可能来自账户**当前绑定**的版本（`metrics_from`），
    所以版本一换，这份就不是当前该显示的那份了 —— 返回 None 让它重算。
    不校验的话页面上是**旧版本算出来的指标**，而它不报错。
    """
    d = _base._read_json(explain_side_path(aid, for_date), None)
    if not d:
        return None
    sha = sha if sha is not None else (_base.get_account(aid) or {}).get('code_sha256')
    return d if d.get('for_sha') == sha else None



def latest_signal(aid):
    d = os.path.join(_base.acct_dir(aid), 'signals')
    if not os.path.isdir(d):
        return None
    fs = sorted(x for x in os.listdir(d) if x.endswith('.json'))
    return _base._read_json(os.path.join(d, fs[-1]), None) if fs else None



def rev_path(aid, for_date, n):
    """被覆盖掉的那一版存这儿。**append-only：已有的 rev 永不重写。**"""
    return os.path.join(_base.acct_dir(aid), 'signals',
                        '%s.rev%d.json' % (for_date, n))


def _lst(sig, k):
    """把 buy/sell/hold 归成可比较的集合 —— 只看【决策】，不看时间戳。"""
    out = set()
    for x in (sig.get(k) or []):
        if isinstance(x, dict):
            out.add((x.get('code'), x.get('shares')))
        else:
            out.add((str(x), None))
    return out


def signal_diff(old, new):
    """两版信号的差异。`changed` 只看决策清单，不看指纹与时间。

    ★ 数据变了但决策没变是**常态**（多数公告不影响选股结果），
      那种情况不该产生"重算过"的噪声 —— 见 make_signal 里的分支。
    """
    d = {'changed': False, 'data_fp_changed': False}
    for k in ('buy', 'sell', 'hold'):
        a, b = _lst(old, k), _lst(new, k)
        add = sorted(c for c, _s in b - a)
        rem = sorted(c for c, _s in a - b)
        if add or rem:
            d['changed'] = True
        d[k + '_added'] = add
        d[k + '_removed'] = rem
    fa = (old.get('data_fingerprint') or {})
    fb = (new.get('data_fingerprint') or {})
    fa = fa.get('overall') if isinstance(fa, dict) else fa
    fb = fb.get('overall') if isinstance(fb, dict) else fb
    d['data_fp_changed'] = (fa != fb)
    d['data_fp_old'], d['data_fp_new'] = fa, fb
    return d


def make_signal(aid, datalake=None, force=False):
    """算并落盘。已经算过就直接返回，除非 force。

    🔴 **force 重算时，结果与旧版不同就必须留痕再覆盖。**
      A 股公告集中在 16:00~22:00，而其中「实施风险警示(ST)」「停牌」这类是
      **次日就生效**的 —— T-1 晚 20:00 公告，T 日开盘简称就变 ST、
      涨跌幅限制变 5%。所以早上重算出来的清单可能与昨晚那份不一样。
      而人**可能已经按昨晚那份准备好委托了** —— 静默覆盖等于让他拿着
      一份已经作废的清单去下单。所以：
        · 旧版归档成 `<date>.rev<N>.json`（append-only，已有的 rev 不重写）
        · 主文件记 `revisions`，页面显红说清差异
      ★ 数据变了但**决策没变**时只记一次 `recomputed_at`，不产生 revision
        —— 否则每天几条"重算过"的噪声，人就不看这个提示了
        （同"假告警看多了就不看告警"）。
    """
    try:
        sig = build_signal(aid, datalake=datalake)
    except _base.LiveError as e:
        return {'account': aid, 'error': str(e), 'built_at': _base._now()}
    p = signal_path(aid, sig['for_date'])
    old = _base._read_json(p, None) if os.path.exists(p) else None
    if old and not force:
        return old
    if old:
        d = signal_diff(old, sig)
        revs = list(old.get('revisions') or [])
        if d['changed']:
            n = 1 + max([r.get('rev', 0) for r in revs] or [0])
            rp = rev_path(aid, sig['for_date'], n)
            if not os.path.exists(rp):          # append-only
                _base._atomic_write(rp, json.dumps(
                    old, ensure_ascii=False, indent=1, sort_keys=True))
            revs.append({'rev': n, 'built_at': old.get('built_at'),
                         'replaced_at': _base._now(),
                         'archived': os.path.basename(rp), 'diff': d})
        else:
            sig['recomputed_at'] = list(old.get('recomputed_at') or []) \
                + [_base._now()]
        sig['revisions'] = revs
    _base._atomic_write(p, json.dumps(sig, ensure_ascii=False, indent=1, sort_keys=True))
    return sig



def explain_history(aid):
    """这个账户有哪些期的信号、哪些期有选股理由。

    页面用它做期数切换。**老信号没有 `explain`** —— 那个功能是 2026-09-05 才加的，
    在那之前落盘的信号里没有理由。这里如实标出 `has_explain`，
    让页面能给出"这一期没有留下理由，可以事后复算"这句话，
    而不是显示一个空浮层（空白会被读成"功能坏了"）。
    """
    d = os.path.join(_base.acct_dir(aid), 'signals')
    out = []
    if not os.path.isdir(d):
        return out
    for f in sorted(x for x in os.listdir(d) if x.endswith('.json')):
        sg = _base._read_json(os.path.join(d, f), None) or {}
        if not sg.get('for_date'):
            continue
        e = sg.get('explain') or {}
        src = 'signal' if e.get('captured') else None
        if not src:
            side = load_explain_side(aid, sg['for_date'])
            if side:
                e, src = (side.get('explain') or {}), 'recomputed'
        out.append({
            'date': sg['for_date'], 'data_asof': sg.get('data_asof'),
            'is_rebalance': bool(sg.get('is_rebalance_day')),
            'has_explain': bool(src),
            'explain_from': src,
            'pool_total': e.get('pool_total') or 0,
            'n_buy': len(sg.get('buy') or []), 'n_sell': len(sg.get('sell') or []),
            'n_hold': len(sg.get('hold') or []),
            'code_sha': sg.get('code_sha'),
        })
    return out


def entry_signals(aid, book=None):
    """每只持仓是【哪一期】买进来的：{code: 那一期的 for_date}。

    ★ 判据是**建仓日**（FIFO 第一批的成交日），而信号的 `for_date` 就是
      那个交易日 —— 信号说的是"下一个交易日的待办"，你照着它在那天成交。
      所以直接按日期对上即可，不用再猜。
    ★ 对不上的（手工买的、信号之外的成交）返回 None，**不硬凑一期** ——
      给一个错的出处比没有出处更糟。
    """
    book = book if book is not None else _pos.fifo_lots(_base.fills(aid))
    have = {x['date'] for x in explain_history(aid)}
    out = {}
    for c, lots in book.items():
        if not lots:
            continue
        d = min(_base._d(l['date']) for l in lots).isoformat()
        out[c] = d if d in have else None
    return out


_RECOMP = {}          # 进程内缓存：(aid, date) -> 复算结果。**不落盘**


def explain_recompute(aid, date, datalake=None, force=False):
    """事后复算某一期的选股理由，并**自证它是不是当时那一份**。

    做法：从那一期的信号里取回 `data_asof` / `code_sha256` / `params`，
    照当时的账重跑一遍（面板截到 `data_asof`、持仓与现金按那天收盘后的状态、
    版本与参数用当时那套）。

    🔴 **然后必须验一次**：复算出的买/卖/持有与信号里存的逐个比 ——
      一致（`same`）才说明面板没被动过、这份理由就是当时那份；
      不一致（`differs`）说明面板被修正过或别的什么变了，
      **那这份理由不是当时的**，页面要照实说，不能拿它当历史证据。
      本项目修过 volume 74,952 行、复权因子 —— "数据后来变了"不是假想。

    ★ 结果**不写回信号文件**（那是 append-only 的证据），而是落在旁边的
      `<date>.explain.json`（见 `explain_side_path`）：进程内缓存活不过重启，
      而这一期的理由是**看历史必看的东西**，不该每次打开页面都重算一遍。
    """
    # 🔴 缓存键必须带**账户当前绑的版本**：复算的"指标"那一半会用当前版本
    #   （见下面的 metrics_from），重新绑定之后结果就变了 —— 键里不带版本的话
    #   页面上还是旧的那份，**而它不报错**（实测：给 SQL 加了组名注释、重绑之后，
    #   页面上组名死活不出来，就是这个缓存）。
    sha = (_base.get_account(aid) or {}).get('code_sha256')
    key = (aid, str(date), sha)
    if not force:
        if key in _RECOMP:
            return _RECOMP[key]
        side = load_explain_side(aid, str(date), sha)
        if side:
            _RECOMP[key] = side
            return side
    old = load_signal(aid, str(date))
    if not old:
        raise _base.LiveError('没有 %s 这一期的信号' % date)
    if not old.get('data_asof'):
        raise _base.LiveError('%s 这一期的信号里没有 data_asof，没法复算' % date)
    got = build_signal(aid, datalake=datalake, asof=old['data_asof'],
                       code_sha=old.get('code_sha256'), params=old.get('params'))

    def _codes(sg, k):
        return sorted(x['code'] for x in (sg.get(k) or []))

    def _cmp(sg):
        d = {}
        for k in ('buy', 'sell', 'hold'):
            a, b = _codes(old, k), _codes(sg, k)
            if a != b:
                d[k] = {'当时': a, '复算': b,
                        '多出': sorted(set(b) - set(a)),
                        '少了': sorted(set(a) - set(b))}
        return d

    diff = _cmp(got)
    metrics_from = 'as-bound'
    # ★ 老版本的策略 SQL 只返回代码，复算出来只有排名没有指标 —— 而"这只票
    #   当时凭什么被选中"恰恰要看指标。这时**再用磁盘上的当前版本复算一遍**：
    #   它与当时那版只差「末层多 SELECT 几列」（投影改动，selftest 每次都验
    #   行与行序逐位相同），所以选出来的票必然一样。
    #   🔴 但不靠"必然"，靠**对账**：两次都要与当时那份信号逐个相同，
    #     才用带指标的那一份，并且明确标出指标是哪个版本给的。
    #     对不上就退回忠实版（宁可没有指标，不给一份可能是别的规则算出来的）。
    def _pool_of(sg):
        # explain 现在是**按查询分组**的（一条查询一组），没有扁平的 pool 了
        return [e for g in ((sg.get('explain') or {}).get('groups') or [])
                for e in (g.get('rows') or [])]

    if not diff:
        pool = _pool_of(got)
        if pool and not any(x.get('metrics') for x in pool):
            cur = _base.get_account(aid).get('code_sha256')
            if cur and cur != old.get('code_sha256'):
                try:
                    got2 = build_signal(aid, datalake=datalake,
                                        asof=old['data_asof'], code_sha=cur,
                                        params=old.get('params'))
                    if not _cmp(got2):
                        got, metrics_from = got2, 'current'
                except Exception:                          # noqa: BLE001
                    pass
    # 指标缺席时把**该怎么办**说清楚。默认那句 notes 说的是"改策略 SQL"，
    # 而这一期的真正原因是"当时绑的那个版本不返回指标列" —— 出路是重新绑定，
    # 不是再改一次 SQL。说错出路比不说更浪费时间。
    _e = got.get('explain') or {}
    if metrics_from == 'as-bound' and _pool_of(got) \
            and not any(x.get('metrics') for x in _pool_of(got)):
        _e.setdefault('notes', []).append(
            '这一期用的是当时绑定的版本（%s），它的查询只返回代码、不返回指标列。'
            '在策略浮层里重新绑定一次新版本之后：以后的信号自带指标，'
            '这一期再复算也会自动用新版本补上指标（并与当时那份对账）。'
            % (old.get('code_sha') or ''))
    res = {
        'date': old['for_date'], 'data_asof': old['data_asof'],
        'recomputed': True,
        'verified': 'same' if not diff else 'differs',
        'diff': diff,
        # 指标是哪个版本给的：as-bound = 当时绑的那版；
        # current = 当时那版不返回指标列，改用磁盘上的当前版本复算
        # （与当时那份逐个对账一致才用）
        'metrics_from': metrics_from,
        'explain': got.get('explain') or {},
        'buy': got.get('buy') or [], 'sell': got.get('sell') or [],
        'hold': got.get('hold') or [],
        'code_sha': old.get('code_sha'), 'params': old.get('params') or {},
        # 这份是**哪个绑定版本**下算出来的。版本一换，`load_explain_side`
        # 就当它不存在（指标那一半可能来自当前版本）
        'for_sha': sha,
        'computed_at': _base._now(),
    }
    _RECOMP[key] = res
    try:
        _base._atomic_write(explain_side_path(aid, res['date']),
                            json.dumps(res, ensure_ascii=False, indent=1, sort_keys=True))
    except Exception:                                       # noqa: BLE001
        pass          # 落盘只是省下次的时间，失败不该让这次的结果作废
    return res


def due_now(acct, now=None):
    """到点了吗。tick_time 是 'HH:MM'。"""
    now = now or datetime.datetime.now()
    try:
        hh, mm = (acct.get('tick_time') or _base.DEFAULT_TICK_TIME).split(':')
        return (now.hour, now.minute) >= (int(hh), int(mm))
    except Exception:                                       # noqa: BLE001
        return False



def tick(datalake=None, now=None, force=False):
    """守护线程每轮做的事：到点、且【下一个交易日】还没算过的账户，算一次。

    ★ 幂等：信号按 for_date 落盘，已存在就跳过 —— 所以重启服务不会重复跑，
      也不会因为每分钟轮询就每分钟算一遍。
    """
    out = []
    try:
        nxt = _base.next_trading_day(datetime.date.today() - datetime.timedelta(days=1))
    except _base.LiveError as e:
        return [{'account': None, 'error': str(e), 'built_at': _base._now()}]
    for a in _base.load_accounts():
        if not a.get('code_sha256'):
            continue
        if not force and not due_now(a, now):
            continue
        if not force and os.path.exists(signal_path(a['id'], nxt.isoformat())):
            continue
        try:
            out.append(make_signal(a['id'], datalake=datalake, force=force))
        except Exception as e:                              # noqa: BLE001
            out.append({'account': a['id'],
                        'error': '%s: %s' % (type(e).__name__, e),
                        'built_at': _base._now()})
    return out
