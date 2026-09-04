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



def build_signal(aid, datalake=None):
    """算出【下一个交易日】的待办。返回可直接落盘的 dict。"""
    acct = _base.get_account(aid)
    if not acct.get('code_sha256'):
        raise _base.LiveError('账户 %s 还没绑定策略版本' % aid)
    book = _pos.fifo_lots(_base.fills(aid))
    money = _pos.cash(aid)

    today = datetime.date.today().isoformat()
    feed = PanelFeed(acct.get('warmup_start') or _base.DEFAULT_WARMUP_START,
                     today, root=datalake)
    t1 = feed.trading_days[-1]                    # 最新数据日
    t0 = feed.prev_trading_day(t1)
    t = _base.next_trading_day(t1)                      # 下一个交易日；拿不到就报错

    mod, full_sha = _load_snapshot(aid, acct['code_sha256'])
    eng = Engine(mod, feed, cash=money, cost=Cost(), params=acct.get('params') or {})
    rb = RecordingBroker(eng.pf, feed, eng.cost)
    eng.broker = rb
    # ★ Context 里存的字段名是 `_broker`。写 ctx.broker 只会凭空多出一个
    #   属性，上下文仍指着原来那个 broker —— 而它 date=None，
    #   于是 context.tradable() 会拿 DATE 'None' 去查行情。
    #   这类"设了个没人读的属性"是静默失败，必须直接写 _broker。
    eng.ctx._broker = rb
    eng._boot()
    try:
        _extend_ordinals(eng, feed, _base.calendar_days())
        # --- 1) 重放 warmup：建起逐日累积的路径状态（黑名单/冷静期/峰值）---
        rows = _base.fills(aid)
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
        exit_orders = _run_tasks(eng, feed, t1, {'d'})
        # --- 3) 调仓检查：下一个交易日是不是调仓日 ---
        rebal_orders = _run_tasks(eng, feed, t, {'w', 'm'}, data_day=t1)
        is_rebal = bool(rebal_orders)
        # --- 4) 未来调仓日：纯日历，可以提前很久算出来 ---
        cal_days = _base.calendar_days()
        upcoming = upcoming_rebalance(eng, feed, t, cal_days)
        held_after = dict(eng.pf.positions)        # RecordingBroker 不成交，等于真实持仓
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
    # ★ 非调仓日 targets 是空的 —— 这时"持有不动"必须是【全部持仓减去要卖的】，
    #   照 targets 算会显示成 0 只，看着像空仓。
    keep = ([c for c in targets if c in book and c not in sells] if is_rebal
            else [c for c in book if c not in sells])
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
        'params': acct.get('params') or {},
        'is_rebalance_day': is_rebal,
        'cash': round(money, 2), 'freed_est': round(freed, 2),
        'left_est': round(left, 2),
        'sell': [{'code': c, 'name': names.get(c, ''), 'shares': sells[c],
                  'reason': reason[c],
                  'ref_price': round(px.get(c, (0, 0, 0))[2] or 0, 2)}
                 for c in sorted(sells)],
        'buy': [{'code': c, 'name': names.get(c, ''), 'shares': plan[c][0],
                 'limit': plan[c][1], 'amount': round(plan[c][0] * plan[c][1], 2),
                 'ref_price': round(buy_px[c], 2)}
                for c in sorted(plan, key=lambda x: -plan[x][0] * plan[x][1])],
        'hold': [{'code': c, 'name': names.get(c, ''),
                  'shares': sum(l['shares'] for l in book[c])} for c in sorted(keep)],
        'no_price': bad_px,
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



def latest_signal(aid):
    d = os.path.join(_base.acct_dir(aid), 'signals')
    if not os.path.isdir(d):
        return None
    fs = sorted(x for x in os.listdir(d) if x.endswith('.json'))
    return _base._read_json(os.path.join(d, fs[-1]), None) if fs else None



def make_signal(aid, datalake=None, force=False):
    """算并落盘。已经算过就直接返回，除非 force。"""
    try:
        sig = build_signal(aid, datalake=datalake)
    except _base.LiveError as e:
        return {'account': aid, 'error': str(e), 'built_at': _base._now()}
    p = signal_path(aid, sig['for_date'])
    if os.path.exists(p) and not force:
        old = _base._read_json(p, None)
        if old:
            return old
    _base._atomic_write(p, json.dumps(sig, ensure_ascii=False, indent=1, sort_keys=True))
    return sig



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
