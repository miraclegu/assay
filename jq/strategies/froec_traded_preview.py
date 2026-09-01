# ============================================================================
# FROEC-TRADED + 盘中 -35% 止损 · 调仓预览（研究环境，盘前跑）
#
# 用途：在【开盘前】就把当天该买该卖算出来，不用等 09:30。顺带查持仓有没有该止损的。
#
# 为什么可以提前算 —— 这条要想清楚，否则就是未来函数：
#   选股全部用 context.previous_date 的数据（PB / eps / 5 期单季 ROE / 行业 /
#   流通市值 / ST / 次新，全部 as-of T-1）。所以对交易日 T 来说，
#   所有输入在 T 的 00:00 就已确定，不依赖 T 当天的任何信息。
#   [!] 唯一用到 T 当天信息的是【价格】—— 限价按 T-1 收盘 x1.05 估。
#
# 与回测版 jq/strategies/froec_traded.py 的关系：选股逻辑逐段搬来，没有改动。
#   差异只有两处，都是「研究环境没有 context」导致的：
#     · context.previous_date  -> 手动用 get_trade_days 算
#     · context.portfolio      -> 手动填 HOLDINGS / CASH / HOLD_COST
#
# 🔴 移植前必读：`traded` 这条规则【样本外已否证】——
#   2006-2015 输 JQ 原版口径 8.86pp，符号翻转。详见回测版文件头。
#   本文件默认 TRADED_UNIVERSE=True 是为了对标标星那次，不是因为它更好。
#
# 用法：聚宽 -> 研究环境（Jupyter）-> 新建 notebook -> 粘贴全文 -> 运行
#       改下面几处：TRADE_DATE / HOLDINGS / CASH /（可选）HOLD_COST
# ============================================================================
import datetime
import pandas as pd
from jqdata import *
from jqfactor import get_factor_values

# ============================== 改这几处 ====================================
TRADE_DATE = '2026-09-01'      # 要预览哪个交易日。留 None = 今天

# 当前持仓：{股票 或 代码: 股数}。三种写法都认，可混写：
#     '招商银行'      -> 按名称（用 T-1 那天的名称）
#     '600036'        -> 六位代码，自动补后缀
#     '600036.XSHG'   -> 完整聚宽代码
HOLDINGS = {
    # '九号公司': 500,
    # '002812': 1200,
}

CASH = 100000.0                # 可用现金（元）。只填能用来买股票的

# 【可选】持仓成本价 {股票 或 代码: 成本价}。填了才能查止损。
# 不填则跳过止损检查（脚本会提示）。
HOLD_COST = {
    # '002812': 45.60,
}

BUY_ALLOC = 'twopass'          # 'twopass'（默认）| 'fixed' | 'seq'，说明见红利那版
# ============================================================================

# ------------- 以下与回测版 froec_traded.py 一致（对齐见 LOCAL_PORT） -------
STOCK_NUM       = 10           # 最大持仓数
CANDIDATE_NUM   = 10           # 先截 10 只再过滤，不补位（有意保留的设计）
LISTED_DAYS     = 250
LIMIT_DAYS      = 20           # 近 N 个调用日涨停过的不再买（预览侧无法完全复现，见下）
TRADED_UNIVERSE = True         # 宇宙 = 决策日实际有成交的票
KCB_688_ONLY    = False        # 按 68* 排科创板（含 689 CDR）
INDUSTRY_CONTROL = True
INDUSTRY_FILTER = ['钢铁I', '煤炭I', '石油石化I', '采掘I',
                   '银行I', '非银金融I', '金融服务I',
                   '交运设备I', '交通运输I', '传媒I', '环保I']
REBAL_WEEKDAY   = 2            # 每周第几个交易日调仓（★2026-09-01 起 1 -> 2）
STOP_LOSS       = 0.35
FLOATMV_SANITY  = 200.0        # 领域自检阈值（亿元）：目标里最大流通市值超过它就报警

LOCAL_PORT = {
    'strategy': 'strategies/小市值/froec_traded.py',
    'run_id':   '20260829-170945-4926f5',
    'params':   {'stop_loss': 0.35, 'stop_intraday': 1},
    'metrics':  {'annual': 40.87, 'max_drawdown': 38.54, 'sharpe': 1.33},
    'backtest': {'start': '2016-01-04', 'end': '2026-08-07', 'cash': 100000,
                 'benchmark': '000905.XSHG', 'freq': 'day'},
    'aligned': {'preview': '本文件是 froec_traded.py 的盘前预览版，'
                           '选股逻辑逐段搬来未改动；数量为估算'},
}


# ============================== 持仓解析 ====================================
def _norm(key, name2code, valid):
    k = str(key).strip()
    if '.XSH' in k.upper():
        return k if k in valid else None
    if k.isdigit() and len(k) == 6:
        c = k + ('.XSHE' if k[0] in '013' else '.XSHG')
        return c if c in valid else None
    return name2code.get(k)


def resolve_holdings(prev):
    """把 HOLDINGS / HOLD_COST 的键统一成聚宽代码。名称、六位、完整代码都认。

    [!] 解析不了的【明确报出来】不静默丢 —— 静默丢一只等于那只永远不会被卖。
    """
    allsec = get_all_securities('stock', date=prev)
    valid = set(allsec.index)
    name2code = {}
    for code in allsec.index:
        name2code.setdefault(allsec.loc[code, 'display_name'], code)

    out, cost, bad = {}, {}, []
    for key, sh in HOLDINGS.items():
        c = _norm(key, name2code, valid)
        if c is None:
            bad.append(('持仓', key))
            continue
        out[c] = out.get(c, 0) + int(sh)
    for key, cp in HOLD_COST.items():
        c = _norm(key, name2code, valid)
        if c is None:
            bad.append(('成本价', key))
            continue
        cost[c] = float(cp)
    if bad:
        print('[!] 这些解析不了，已【忽略】，请核对：')
        for kind, k in bad:
            print('      [%s] %s' % (kind, k))
        print('    名称要用 %s 那天的名称；代码用六位或完整聚宽格式。' % prev)
    return out, cost


# ============================== 基准日 / 调仓日 =============================
def resolve_dates(trade_date):
    if trade_date is None:
        trade_date = str(datetime.date.today())
    t = pd.Timestamp(trade_date).date()
    days = get_trade_days(end_date=t, count=2)
    days = [d.date() if hasattr(d, 'date') else d for d in days]
    if not days:
        raise RuntimeError('取不到交易日历')
    if days[-1] != t:
        nxt = get_trade_days(start_date=t, end_date=t + datetime.timedelta(days=40))
        nxt = [d.date() if hasattr(d, 'date') else d for d in nxt]
        raise RuntimeError('%s 不是交易日。下一个交易日是 %s，把 TRADE_DATE 改成它。'
                           % (t, nxt[0] if nxt else '（取不到）'))
    if len(days) < 2:
        raise RuntimeError('取不到 %s 的上一个交易日' % t)
    return t, days[-2]


def is_rebalance_day(t):
    """回测里是 run_weekly(weekday=2) = 每周第 2 个交易日（2026-09-01 起）。

    [!] 用【区间】取本周首个交易日，不用 count —— 聚宽的 count 是从 end_date
        往前数的，get_trade_days(start_date=X, count=1) 语义不明。
    [!] run_weekly 锚在【自然周】上：假期短周会让实际间隔在 1~13 个交易日之间跳
        （本地实测 weekday=1 有 15 次间隔 <= 2 天）。这是原版行为，不是 bug。
    [!] REBAL_WEEKDAY > 1 时，本周交易日还不够 REBAL_WEEKDAY 个就【不是】调仓日；
        原来的 else days[0] 兜底会在短周把首日误判成调仓日 —— 已去掉。
    """
    week_start = t - datetime.timedelta(days=t.weekday())
    days = get_trade_days(start_date=week_start, end_date=t)
    days = [d.date() if hasattr(d, 'date') else d for d in days]
    if not days:
        return False, None
    if len(days) < REBAL_WEEKDAY:
        return False, None
    first = days[REBAL_WEEKDAY - 1]
    return first == t, first


# ============================== 过滤器（搬自回测版）==========================
def filter_new_stock(stock_list, prev):
    return [s for s in stock_list
            if not prev - get_security_info(s).start_date
            < datetime.timedelta(days=LISTED_DAYS)]


def filter_kcb_stock(stock_list):
    if KCB_688_ONLY:
        return [s for s in stock_list if s[0:3] != '688']
    return [s for s in stock_list if s[0:2] != '68']


def filter_st_stock(stock_list, prev):
    """ST / 退市 / 停牌 一次过滤。用 T-1 的 get_extras + 名称前缀双查。"""
    out = []
    ex = None
    try:
        ex = get_extras('is_st', stock_list, end_date=prev, count=1)
    except Exception:
        ex = None
    for s in stock_list:
        st = False
        if ex is not None:
            try:
                st = bool(ex[s].iloc[-1])
            except Exception:
                st = False
        nm = get_security_info(s).display_name
        if st or 'ST' in nm or '退' in nm:
            continue
        out.append(s)
    return out


# ============================== 选股（搬自回测版 v3）========================
def pick(prev):
    """FROEC：PB 半区 -> 5 期单季 ROE 的 increase 取前十分位 -> 行业过滤 -> 市值升序。

    ★ increase = 4*roe(最新) - roe(前4期之和)，与回测版
      `4*df.iloc[:,4] - df.iloc[:,0..3]` 逐字一致。
    ★ 两级切点都是【向下取整】：pb_list[:int(0.5*n)]、temp_list[:int(0.1*n2)]。
      切点位移一位就会换掉边界上的票（本地实测 689009 一只值 1.67pp 年化），
      所以这里照抄 int() 不用 round/max。
    """
    initial = get_all_securities('stock', date=prev).index.tolist()
    if TRADED_UNIVERSE:
        vol = get_price(initial, end_date=prev, frequency='1d', count=1,
                        fields=['volume'], panel=False, skip_paused=False,
                        fill_paused=False)
        traded = set(vol[vol['volume'] > 0]['code'])
        initial = [s for s in initial if s in traded]
        print('  宇宙（当日有成交）%d 只' % len(initial))
    else:
        print('  宇宙（JQ 原版，含停牌）%d 只' % len(initial))
    initial = filter_new_stock(initial, prev)
    initial = filter_kcb_stock(initial)
    initial = filter_st_stock(initial, prev)
    print('  过滤次新/科创/ST 后 %d 只' % len(initial))

    # ---- PB 半区（eps>0 且 pb>0）----
    q = query(valuation.code, valuation.day, valuation.pb_ratio,
              indicator.eps).filter(
        valuation.code.in_(initial)).order_by(valuation.pb_ratio.asc())
    df = get_fundamentals(q, date=prev)
    assert_asof(df, prev, 'PB/eps 查询')      # ★ 自证基准日，见函数注释
    df = df[df['eps'] > 0]
    df = df[df['pb_ratio'] > 0]
    pb_list = list(df.code)
    pb_list = pb_list[:int(0.5 * len(pb_list))]
    print('  PB 半区 %d 只（切点 int(0.5*%d)）' % (len(pb_list), len(df)))

    # ---- 5 期单季 ROE 的 increase，取前十分位 ----
    step = 625
    parts = []
    for i in range(0, len(pb_list), step):
        parts.append(get_history_fundamentals(
            pb_list[i:i + step], fields=[indicator.code, indicator.roe],
            watch_date=prev, count=5, interval='1q'))
    if not parts:
        return [], pd.DataFrame()
    r = pd.concat(parts)
    r = r.groupby('code').apply(lambda x: x.reset_index()).roe.unstack()
    r['increase'] = 4 * r.iloc[:, 4] - r.iloc[:, 0] - r.iloc[:, 1] \
        - r.iloc[:, 2] - r.iloc[:, 3]
    r = r.dropna().sort_values(by='increase', ascending=False)
    temp = list(r.index)
    roe_list = temp[:int(0.1 * len(temp))]
    print('  ROE increase 前十分位 %d 只（切点 int(0.1*%d)）' % (len(roe_list), len(temp)))

    # ---- 行业过滤 ----
    if INDUSTRY_CONTROL and roe_list:
        ind = get_industry(roe_list, date=prev)
        keep = []
        for s in roe_list:
            nm = (ind.get(s, {}).get('sw_l1', {}) or {}).get('industry_name')
            if nm not in INDUSTRY_FILTER:
                keep.append(s)
        print('  行业过滤后 %d 只（剔除 %d 只）' % (len(keep), len(roe_list) - len(keep)))
        roe_list = keep

    # ---- 流通市值升序，取前 CANDIDATE_NUM ----
    if not roe_list:
        return [], pd.DataFrame()
    q = query(valuation.code, valuation.day,
              valuation.circulating_market_cap).filter(
        valuation.code.in_(roe_list)).order_by(
        valuation.circulating_market_cap.asc())
    cap = get_fundamentals(q, date=prev)
    assert_asof(cap, prev, '流通市值查询')
    # ★ 领域自检：froec 是【小市值】线。目标里出现几百亿的票 = 基准日错配的信号。
    #   实测踩过：香农芯创（2018 年名聚隆科技）2018 年 10.6 亿、2026 年 688.6 亿，
    #   混合日期时它会出现在「市值升序前 10」里。
    if len(cap):
        mx = float(cap['circulating_market_cap'].head(CANDIDATE_NUM).max())
        if mx > FLOATMV_SANITY:
            print('[!!] 目标组合里最大流通市值 %.1f 亿 > 阈值 %.0f 亿 —— 很可能是'
                  % (mx, FLOATMV_SANITY))
            print('     【基准日错配】（选股用了一个日期、价格用了另一个）。')
            print('     froec 是小市值线，正常目标应该都在几十亿以内。请核对运行环境。')
    target = list(cap.code)[:CANDIDATE_NUM]
    return target, cap.set_index('code')


# ============================== 止损检查 ====================================
def check_stop(holds, cost, px, prev):
    print()
    print('-' * 76)
    print('止损检查（阈值 -%.0f%%，相对建仓成本）' % (STOP_LOSS * 100))
    print('-' * 76)
    if not cost:
        print('  （跳过：HOLD_COST 没填。填了成本价才能查）')
        return []
    hit = []
    for s in holds:
        c = cost.get(s)
        if not c or c <= 0:
            print('  %-12s %-8s 没填成本价，跳过' % (s, get_security_info(s).display_name))
            continue
        last = float(px.loc[s, 'close']) if s in px.index else 0.0
        ret = last / c - 1.0
        flag = '  <== 触发止损' if ret <= -STOP_LOSS else ''
        print('  %-12s %-8s 成本 %8.3f  T-1收盘 %8.3f  %+7.2f%%%s'
              % (s, get_security_info(s).display_name, c, last, ret * 100, flag))
        if ret <= -STOP_LOSS:
            hit.append(s)
    if hit:
        print()
        print('  [!] 上面 %d 只按 T-1 收盘已触发。但本地是用【当日最低价】判定的 ——' % len(hit))
        print('      也就是说 T 当天盘中还可能有新的触发，这里看不到。')
    return hit


# ============================== 主流程 ======================================
ALLOW_BACKTEST_ENV = False   # 只有在你完全清楚后果时才改 True


def warn_if_backtest():
    """在回测环境里【直接拒绝运行】，不是打个警告了事。

    为什么必须硬拒 —— 实测踩过：在回测（起点 2019-01-01）里跑本脚本，
        get_fundamentals(q, date=prev)  被 avoid_future_data 压到回测当前日期
                                        -> 拿到 2018-12-28 的 PB/ROE/市值
        get_price(end_date=prev)        没被拦住 -> 拿到 2026-08-31 的价格
    结果是一份【看起来完全合理】的下单清单，实际是「8 年前的选股 + 今天的价格」：
    香农芯创（2018 年名为聚隆科技）2018 年流通市值 10.6 亿、PB 1.35，
    选它没错；但 2026 年它是 688.6 亿、PB 14.78，按市值升序排全市场第 4971 位。
    这种输出比报错危险得多，所以宁可不跑。
    """
    for name in ('context', 'g'):
        if name in globals():
            print('=' * 76)
            print('[!!] 检测到全局 %s —— 你在【回测/模拟环境】里跑本脚本。' % name)
            print('     本脚本只该在【研究环境（Jupyter）】跑。在回测里跑的话：')
            print('       · get_price(end_date=TRADE_DATE) 会取到回测日期之后的数据')
            print('         = 未来函数，结果不可用于任何评估')
            print('       · 日志时间戳是【回测当前时间】而不是 TRADE_DATE，容易看错')
            print('       · 最坏的情况：基本面被压到回测日期、价格却是 TRADE_DATE 的')
            print('         -> 「8 年前的选股 + 今天的价格」，看着合理其实全错')
            print('     请改到【研究环境（Jupyter）】跑。')
            print('=' * 76)
            if not ALLOW_BACKTEST_ENV:
                raise RuntimeError('本脚本只能在研究环境跑。确实要在回测里跑，'
                                   '把 ALLOW_BACKTEST_ENV 改成 True 并自行承担后果。')
            return True
    return False


def assert_asof(df, prev, what):
    """自证：get_fundamentals 返回的 day 列必须等于 prev。

    这是比「检测全局 context」更直接的判据 —— 它直接问「你给我的到底是哪天的数据」。
    回测环境里 avoid_future_data 会把 date 压到回测当前日期而【不报错】，
    只有核对返回的 day 才看得出来。
    """
    if df is None or 'day' not in getattr(df, 'columns', []):
        return
    days = set(str(x)[:10] for x in df['day'].dropna().unique())
    if not days:
        return
    if days != {str(prev)}:
        raise RuntimeError(
            '[基准日不一致] %s 请求的是 %s，但 get_fundamentals 返回的是 %s。\n'
            '    回测环境里 avoid_future_data 会把 date 压到回测当前日期而不报错，\n'
            '    结果是「那一天的选股 + TRADE_DATE 的价格」—— 全错。\n'
            '    请在【研究环境（Jupyter）】跑。' % (what, prev, sorted(days)))


def main():
    warn_if_backtest()
    t, prev = resolve_dates(TRADE_DATE)
    rebal, first = is_rebalance_day(t)

    print('=' * 76)
    print('FROEC-TRADED + 盘中 -%.0f%% 止损 · 调仓预览' % (STOP_LOSS * 100))
    print('  交易日 T        = %s' % t)
    print('  基准日 T-1      = %s   <- 选股全部用这一天的数据' % prev)
    print('  本周第%d个交易日  = %s' % (REBAL_WEEKDAY, first))
    print('  今天是调仓日吗  = %s' % ('是' if rebal else '否'))
    print('  关联本地归档    = %s  (%s)' % (LOCAL_PORT['run_id'], LOCAL_PORT['params']))
    print('  [!] traded 宇宙【样本外已否证】：2006-2015 输原版口径 8.86pp')
    print('=' * 76)

    holds, cost = resolve_holdings(prev)
    print('  当前持仓 %d 只，可用现金 %.2f，分配口径 %s'
          % (len(holds), CASH, BUY_ALLOC))

    if not rebal:
        print()
        print('[!] %s 不是本周第 %d 个交易日 —— 策略当天【不调仓】。' % (t, REBAL_WEEKDAY))
        print('    本周调仓日是 %s。下面仍把「若今天调仓会选出什么」打出来供参考。' % first)

    target, cap = pick(prev)

    codes = list(dict.fromkeys(list(target) + list(holds)))
    px = get_price(codes, end_date=prev, frequency='1d', count=1,
                   fields=['close'], fq='pre', panel=False,
                   skip_paused=False, fill_paused=True).set_index('code')
    px = px[~px.index.duplicated(keep='last')]

    print()
    print('-' * 76)
    print('目标组合 %d 只（流通市值升序）' % len(target))
    print('-' * 76)
    for i, s in enumerate(target):
        mc = float(cap.loc[s, 'circulating_market_cap']) if s in cap.index else 0.0
        print('  %2d. %-12s %-8s  T-1收盘 %8.2f   流通市值 %8.2f 亿'
              % (i + 1, s, get_security_info(s).display_name,
                 float(px.loc[s, 'close']), mc))

    check_stop(holds, cost, px, prev)

    # ---- 卖出 ----
    print()
    print('-' * 76)
    print('需要卖出（不在目标组合里的持仓）')
    print('-' * 76)
    sells = [s for s in holds if s not in target]
    est_cash = CASH
    if not sells:
        print('  （无）')
    for s in sells:
        sh = holds[s]
        p = float(px.loc[s, 'close']) if s in px.index else 0.0
        est_cash += sh * p
        print('  卖出 %-12s %-8s %8d 股   估算金额 %12.2f （按 T-1 收盘 %.2f）'
              % (s, get_security_info(s).display_name, sh, sh * p, p))
    print('  [!] 回测里【昨日涨停的持仓豁免本次卖出】（让利润奔跑，有意为之），')
    print('      这里没法判 T 当天是否仍封板，所以上面清单可能多卖 —— 实盘自行豁免。')

    # ---- 买入 ----
    print()
    print('-' * 76)
    print('需要买入   分配口径 %s' % BUY_ALLOC)
    print('           [!] 数量是【估算】：限价按 T-1 收盘 x1.05。')
    print('               实盘按开盘价成交，以下单当时的可用现金重算为准。')
    print('-' * 76)
    buys = [s for s in target if s not in holds]
    if not buys:
        print('  （无，目标全部已持有）')

    def lot(code, money):
        limit = round(float(px.loc[code, 'close']) * 1.05, 2)
        return limit, (100 * int(money / limit / 100) if limit > 0 else 0)

    plan, skipped, spent = {}, [], 0.0
    n = len(buys)
    if n:
        if BUY_ALLOC == 'seq':
            cash = est_cash
            for i, code in enumerate(buys):
                per = cash / (n - i)
                limit, amt = lot(code, per)
                if amt <= 0:
                    skipped.append((code, limit, per)); continue
                plan[code] = (amt, limit); cash -= amt * limit; spent += amt * limit
        else:
            per = est_cash / n
            for code in buys:
                limit, amt = lot(code, per)
                if amt <= 0:
                    skipped.append((code, limit, per)); continue
                plan[code] = (amt, limit); spent += amt * limit
            if BUY_ALLOC == 'twopass' and plan:
                left = est_cash - spent
                add = left / len(plan)
                n_top = 0
                for code in list(plan):
                    amt, limit = plan[code]
                    _, more = lot(code, add)
                    if more > 0:
                        plan[code] = (amt + more, limit)
                        spent += more * limit
                        n_top += 1
                if n_top:
                    print('  [第二轮] 剩余 %.0f 元平分给 %d 只（每只 %.0f），%d 只补上了手数'
                          % (left, len(plan), add, n_top))

    for code in buys:
        if code not in plan:
            continue
        amt, limit = plan[code]
        print('  买入 %-12s %-8s %8d 股   限价 %8.2f   预计占用 %12.2f'
              % (code, get_security_info(code).display_name, amt, limit, amt * limit))
    for code, limit, per in skipped:
        print('  [买不进] %-12s %-8s 一手 100 股需 %.0f 元，而分到 %.0f 元'
              % (code, get_security_info(code).display_name, limit * 100, per))
    if skipped and BUY_ALLOC == 'fixed':
        idle = sum(p for _, _, p in skipped)
        print('        ^ fixed 口径下这些份额【闲置】，共 %.0f 元（%.1f%% 仓位拖累）'
              % (idle, 100.0 * idle / est_cash if est_cash else 0))

    print()
    print('-' * 76)
    print('  可用现金（含卖出估算） %14.2f' % est_cash)
    print('  买入预计占用           %14.2f' % spent)
    print('  剩余                   %14.2f' % (est_cash - spent))
    print('  持仓不动的 %d 只：%s'
          % (len([s for s in holds if s in target]),
             ', '.join(s for s in holds if s in target) or '（无）'))
    print('-' * 76)
    print('[!] 与回测的已知差异，看结果时算进去：')
    print('    1. traded 宇宙样本外已否证（2006-2015 输原版 8.86pp）')
    print('    2. 昨日涨停豁免卖出：本脚本判不了，卖出清单可能多卖')
    print('    3. 近 %d 个调用日涨停过的不再买（not_buy_again）：' % LIMIT_DAYS)
    print('       本脚本无历史状态，判不了 -> 目标里可能含本该被拉黑的票')
    print('    4. 止损用 T-1 收盘判，本地用【当日最低价】-> T 当天盘中可能有新触发')
    print('    5. 股数随开盘价变，这里是 T-1 收盘 x1.05 的估算')


main()
