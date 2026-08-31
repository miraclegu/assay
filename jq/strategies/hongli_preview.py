# ============================================================================
# 红利指数增强 · 调仓预览（研究环境，盘前跑）
#
# 用途：在【开盘前】就把当天该买该卖算出来，不用等 09:00。
#
# 为什么可以提前算 —— 这条要想清楚，否则就是未来函数：
#   策略的选股全部用 context.previous_date 的数据（financial as-of、分红
#   board_plan_pub_date <= T-1、beta 到 T-1）。所以对交易日 T 来说，
#   所有输入在 T 的 00:00 就已经全部确定，不依赖 T 当天的任何信息。
#   本脚本把 T 的 previous_date 显式算出来当基准日，与回测口径【逐字一致】。
#
#   [!] 唯一用到 T 当天信息的地方是【价格】—— 限价按 T-1 收盘 x1.05 估，
#       与回测里 buy_df['price'] 的算法相同。但股数会随开盘价变，
#       所以下面打印的数量是【估算】，实盘以当时可用现金重算为准。
#
# 与回测版的关系：选股逻辑从 jq/strategies/hongli_index_plus.py 逐段搬过来，
#   没有改动。差异只有两处，都是「研究环境没有 context」导致的：
#     · context.previous_date  -> 手动用 get_trade_days 算
#     · context.portfolio      -> 手动填 HOLDINGS / CASH
#
# 用法：聚宽 -> 研究环境（Jupyter）-> 新建 notebook -> 粘贴全文 -> 运行
#       （不是回测！回测环境没有本脚本要的自由取数方式）
#       改下面三处：TRADE_DATE / HOLDINGS / CASH
# ============================================================================
import datetime
import pandas as pd
from jqdata import *
from jqfactor import get_factor_values

# ============================== 改这三处 ====================================
# 要预览哪个交易日的调仓。留 None = 今天（若今天不是交易日会提示）
TRADE_DATE = '2026-09-01'

# 当前持仓：{股票 或 代码: 股数}。三种写法都认，可以混着写：
#     '招商银行'      -> 按名称查（用 T-1 那天的名称，改过名的股票用当时的名字）
#     '600036'        -> 六位代码，自动补 .XSHG / .XSHE
#     '600036.XSHG'   -> 完整聚宽代码
# 空仓就写 {}
HOLDINGS = {
    # '招商银行': 2000,
    # '601398': 10000,
    # '600519.XSHG': 100,
}

# 可用现金（元）。只填【能用来买股票的现金】，不含已被持仓占用的市值
CASH = 1000000.0

# 买入资金分配口径
#   'twopass' 两轮（默认，推荐）：先按 cash/n 预分配，买不进的不占份额，
#             剩余现金（含买不进的那份 + 取整残差）平分给已建仓的补仓
#   'fixed'   本地标星那次回测用的：per = cash/n 固定，买不进的那份【闲置】
#   'seq'     JQ v7 的逐笔：per = cash/(n-i)，买不进的份额【流给后面】-> 后面超配
# 三者本地回测差异 <= 0.03pp 年化（10.5 年，见 分析结论-本地复现.md），
# 但实盘上 twopass 明显更合理：既不闲置现金，也不让权重向后漂。
BUY_ALLOC = 'twopass'

# 昨日涨停的持仓股（炸板离场用）。留空则脚本自己按 T-1 行情判定
YESTERDAY_LIMIT_UP = None
# ============================================================================

# ---------------- 以下与回测版 hongli_index_plus.py 完全一致 ----------------
TARGET_NUM = [10, 5]
BACKUP_NUM = [5, 5]
DIV_METHOD = 'fiscal_year'
DIV_LOOKBACK_DAYS = 800
DIV_CHUNK = 500
STAGE_ORDER = {'实施方案': 3, '股东大会预案': 2, '董事会预案': 1, '公司预案': 0}
CANCEL_STATES = ['终止', '取消分红']
NEW_STOCK_DAYS = 250
# 与回测版 LOCAL_PORT 对应的本地归档
LOCAL_PORT = {
    'strategy': 'strategies/红利/红利指数增强.py',
    'run_id':   '20260828-210010-adcde3',
    'params':   {'div_method': 'fiscal_year'},
    'metrics':  {'annual': 20.07, 'max_drawdown': 17.95, 'sharpe': 1.30},
    'backtest': {'start': '2016-01-04', 'end': '2026-06-30', 'cash': 1000000,
                 'benchmark': '000015.XSHG', 'freq': 'day'},
    'aligned':  {'preview': '本文件是 hongli_index_plus.py 的盘前预览版，'
                            '选股逻辑逐段搬来未改动；数量为估算'},
}


# ============================== 持仓解析 ====================================
def resolve_holdings(prev):
    """把 HOLDINGS 的键统一成聚宽代码。名称、六位代码、完整代码都认。

    [!] 名称用【T-1 那天的名称】查 —— 股票改过名的话，用当时的名字。
        解析不了的会明确报出来，不会静默丢掉（静默丢一只 = 那只不会被卖）。
    """
    allsec = get_all_securities('stock', date=prev)
    name2code, bad = {}, []
    for code in allsec.index:
        nm = allsec.loc[code, 'display_name']
        name2code.setdefault(nm, code)

    out = {}
    for key, sh in HOLDINGS.items():
        k = str(key).strip()
        code = None
        if '.XSH' in k.upper():
            code = k
        elif k.isdigit() and len(k) == 6:
            code = k + ('.XSHE' if k[0] in '013' else '.XSHG')
        elif k in name2code:
            code = name2code[k]
        if code is None or code not in set(allsec.index):
            bad.append(k)
            continue
        out[code] = out.get(code, 0) + int(sh)
    if bad:
        print('[!] 这些持仓解析不了，已【忽略】—— 它们不会出现在卖出清单里，请核对：')
        for k in bad:
            print('      %s' % k)
        print('    名称要用 %s 那天的名称；代码用六位或完整聚宽格式。' % prev)
    return out


# ============================== 基准日 ======================================
def resolve_dates(trade_date):
    """把 T 与 T-1 定下来。这是全脚本最要紧的一步 —— 基准日错一天，选股全错。"""
    if trade_date is None:
        trade_date = str(datetime.date.today())
    t = pd.Timestamp(trade_date).date()
    days = get_trade_days(end_date=t, count=2)
    days = [d.date() if hasattr(d, 'date') else d for d in days]
    if not days:
        raise RuntimeError('取不到交易日历')
    if days[-1] != t:
        # T 不是交易日：给出下一个交易日，让人自己决定
        # [!] 不能用 get_trade_days(start_date=t, count=1) —— 聚宽的 count 是
        #     【从 end_date 往前数】，与 start_date 混用语义不明。用区间取首个。
        nxt = get_trade_days(start_date=t, end_date=t + datetime.timedelta(days=40))
        nxt = [d.date() if hasattr(d, 'date') else d for d in nxt]
        raise RuntimeError('%s 不是交易日。下一个交易日是 %s，把 TRADE_DATE 改成它。'
                           % (t, nxt[0] if nxt else '（取不到）'))
    if len(days) < 2:
        raise RuntimeError('取不到 %s 的上一个交易日' % t)
    return t, days[-2]


def is_rebalance_day(t):
    """回测里是 run_monthly(..., 1) = 每月第 1 个交易日。这里要复现这个判定。

    [!] 用【区间】取首个交易日，不用 count —— 聚宽的 count 是从 end_date 往前数的，
        get_trade_days(start_date=X, count=1) 语义不明（打桩时就在这里翻车，
        返回的是区间最后一天而不是第一天，把调仓日判成了「否」）。
    """
    month_start = datetime.date(t.year, t.month, 1)
    days = get_trade_days(start_date=month_start, end_date=t)
    days = [d.date() if hasattr(d, 'date') else d for d in days]
    if not days:
        return False, None
    return days[0] == t, days[0]


# ============================== 过滤器（搬自回测版）==========================
def filter_paused_stock(stock_list, d):
    cd = get_price(stock_list, end_date=d, frequency='1d', count=1,
                   fields=['paused'], panel=False, skip_paused=False,
                   fill_paused=True)
    bad = set(cd[cd['paused'] > 0]['code'])
    return [s for s in stock_list if s not in bad]


def filter_st_stock(stock_list, d):
    ex = get_extras('is_st', stock_list, end_date=d, count=1)
    out = []
    for s in stock_list:
        try:
            if not bool(ex[s].iloc[-1]):
                out.append(s)
        except Exception:
            out.append(s)
    return out


def filter_kcb_stock(stock_list):
    return [s for s in stock_list if s[0] != '6' or s[:3] != '688']


def filter_new_stock(stock_list, d):
    lim = d - datetime.timedelta(days=NEW_STOCK_DAYS)
    return [s for s in stock_list
            if get_security_info(s).start_date < lim]


# ============================== 股息率（A-6，搬自回测版）====================
def _query_dividends(stock_list, date_field, time0, time1, extra_fields):
    acc = []
    F = [finance.STK_XR_XD.code, finance.STK_XR_XD.bonus_amount_rmb] + extra_fields
    for i in range(0, len(stock_list), DIV_CHUNK):
        part = stock_list[i:i + DIV_CHUNK]
        acc.append(finance.run_query(query(*F).filter(
            finance.STK_XR_XD.code.in_(part),
            date_field >= time0, date_field <= time1)))
    return pd.concat(acc) if acc else pd.DataFrame()


def dividend_by_fiscal_year(stock_list, time1):
    """与回测版 _dividend_by_fiscal_year 逐条一致：
    可见性 board_plan_pub_date <= T-1、回溯 800 天、
    去重键 (code, report_date, bonus_type) 取流程最靠后、
    剔除取消/终止、年度锚定 report_date 月份==12 取最新 FY、汇总该 FY 全部分红。"""
    time0 = time1 - datetime.timedelta(days=DIV_LOOKBACK_DAYS)
    df = _query_dividends(
        stock_list, finance.STK_XR_XD.board_plan_pub_date, time0, time1,
        [finance.STK_XR_XD.report_date, finance.STK_XR_XD.bonus_type,
         finance.STK_XR_XD.plan_progress, finance.STK_XR_XD.bonus_cancel_pub_date])
    if len(df) == 0:
        return pd.Series([], dtype='float64')
    df = df[df.bonus_cancel_pub_date.isna()]
    df = df[~df.plan_progress.isin(CANCEL_STATES)]
    df = df[df.bonus_amount_rmb.notna() & (df.bonus_amount_rmb > 0)]
    if len(df) == 0:
        return pd.Series([], dtype='float64')
    df = df.copy()
    df['_stage'] = df.plan_progress.map(STAGE_ORDER).fillna(0)
    df = (df.sort_values('_stage')
            .drop_duplicates(subset=['code', 'report_date', 'bonus_type'], keep='last'))
    rd = pd.to_datetime(df['report_date'])
    df['fy'] = rd.dt.year
    annual = df[rd.dt.month == 12]
    if len(annual) == 0:
        return pd.Series([], dtype='float64')
    latest_fy = annual.groupby('code').fy.max()
    df = df.join(latest_fy.rename('latest_fy'), on='code')
    df = df[df.fy == df.latest_fy]
    return df.groupby('code').bonus_amount_rmb.sum()


def dividend_ratio_sorted(stock_list, time1, p1, p2, threshold):
    """股息率降序取 [p1, p2) 分位、且 > threshold。

    ★ 与回测版 get_dividend_ratio_filter_list 【逐字一致】，三处细节都要照抄，
      否则边界上会差票：
        [1] 市值走 get_fundamentals(query(valuation.market_cap), date=time1)
        [2] 分位截断是 df[int(p1*len(df)) : int(p2*len(df))] —— 【没有 max(...,1)】，
            池子小于 10 只时前 10% 会取到 0 只。这是原版行为，不要"顺手修好"
        [3] 阈值是【严格大于】 threshold，不是 >=
      股息率口径：(分红万元 / 10000) / 总市值亿元 —— 亿元/亿元，无量纲。
    """
    div = dividend_by_fiscal_year(stock_list, time1)
    if len(div) == 0:
        return [], pd.DataFrame()
    _cap = get_fundamentals(
        query(valuation.code, valuation.day, valuation.market_cap).filter(
            valuation.code.in_(list(div.index))),
        date=time1)
    assert_asof(_cap, time1, '总市值查询')     # ★ 自证基准日，见函数注释
    cap = _cap[['code', 'market_cap']].set_index('code')
    df = pd.concat([div.rename('bonus_amount_rmb'), cap], axis=1, sort=False)
    df = df[df.market_cap.notna() & (df.market_cap > 0)]
    df['dividend_ratio'] = (df['bonus_amount_rmb'] / 10000) / df['market_cap']
    df = df.sort_values(by=['dividend_ratio'], ascending=False)
    df = df[int(p1 * len(df)):int(p2 * len(df))]
    df = df[df['dividend_ratio'] > threshold]
    return list(df.index), df


# ============================== 选股（搬自回测版）============================
def pick(t, prev):
    initial = get_all_securities('stock', date=prev).index.tolist()
    initial = filter_kcb_stock(initial)
    initial = [s for s in initial if not s.startswith(('4', '8', '9', '2'))]
    initial = filter_new_stock(initial, prev)
    initial = filter_st_stock(initial, prev)
    initial = filter_paused_stock(initial, prev)
    print('  股票池 %d 只（%s 时点）' % (len(initial), prev))

    # ---- Sleeve A 红利低波：高股息 -> beta 升序 ----
    hi_div, dy = dividend_ratio_sorted(initial, prev, 0.00, 0.10, 0.03)
    print('  股息率 top10%% 且 >=3%%：%d 只' % len(hi_div))
    if hi_div:
        fv = get_factor_values(hi_div, ['beta'], end_date=prev, count=1)['beta']
        beta = fv.iloc[-1].dropna().sort_values()          # 升序 = 低波优先
        a_all = list(beta.index)
    else:
        a_all = []
    A = a_all[:TARGET_NUM[0]]
    A_bak = a_all[TARGET_NUM[0]:TARGET_NUM[0] + BACKUP_NUM[0]]

    # ---- Sleeve B 红利价值：基本面四条 -> 高股息 ----
    df = get_fundamentals(query(valuation.code, valuation.day).filter(
        valuation.code.in_(initial),
        valuation.pe_ratio.between(5, 50),
        indicator.inc_return.between(5, 100),
        indicator.inc_total_revenue_year_on_year.between(5, 100),
        indicator.inc_net_profit_year_on_year.between(10, 100),
    ), date=prev)
    assert_asof(df, prev, '基本面四条查询')    # ★ 这条最该核 —— 四个财务阈值全在这
    b_pool = list(df.code)
    print('  基本面四条过滤后：%d 只' % len(b_pool))
    b_sorted, dy_b = dividend_ratio_sorted(b_pool, prev, 0.00, 0.10, 0.03)
    B = b_sorted[:TARGET_NUM[1]]
    B_bak = b_sorted[TARGET_NUM[1]:TARGET_NUM[1] + BACKUP_NUM[1]]

    # ---- 并集去重，A 优先（确定性保序，A-5）----
    target, backup = [], []
    for s in list(A) + list(B):
        if s not in target:
            target.append(s)
    for s in list(A_bak) + list(B_bak):
        if s not in target and s not in backup:
            backup.append(s)
    return target, backup, A, B, dy, dy_b


# ============================== 主流程 ======================================
def _dr(dy, dy_b, s):
    """取某只股票的股息率用于展示。A 袖与 B 袖各自算过一次，哪个有就用哪个。"""
    for d in (dy, dy_b):
        if len(d) and s in d.index:
            return float(d.loc[s, 'dividend_ratio'])
    return 0.0


ALLOW_BACKTEST_ENV = False   # 只有在你完全清楚后果时才改 True


def warn_if_backtest():
    """在回测环境里【直接拒绝运行】，不是打个警告了事。

    为什么必须硬拒 —— 实测踩过：在回测（起点 2019-01-01）里跑预览脚本，
        get_fundamentals(q, date=prev)  被 avoid_future_data 压到回测当前日期
        get_price(end_date=prev)        没被拦住，拿到 TRADE_DATE 的价格
    结果是一份【看起来完全合理】的下单清单，实际是「8 年前的选股 + 今天的价格」。
    这种输出比报错危险得多。
    """
    """本脚本是给【研究环境】的。在回测里跑会取到回测日期【之后】的数据 ——
    那是未来函数，而且日志时间戳会是回测当前时间，很容易看错。

    判据：回测/模拟环境里有全局 context / g（平台注入），研究环境里没有。
    """
    for name in ('context', 'g'):
        if name in globals():
            print('=' * 76)
            print('[!!] 检测到全局 %s —— 你可能在【回测/模拟环境】里跑本脚本。' % name)
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

    比「检测全局 context」更直接 —— 它直接问「你给我的到底是哪天的数据」。
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
    rebal, first_day = is_rebalance_day(t)

    print('=' * 76)
    print('红利指数增强 · 调仓预览')
    print('  交易日 T        = %s' % t)
    print('  基准日 T-1      = %s   <- 选股全部用这一天的数据' % prev)
    print('  本月第1交易日   = %s' % first_day)
    print('  今天是调仓日吗  = %s' % ('是' if rebal else '否'))
    print('  关联本地归档    = %s  (%s)'
          % (LOCAL_PORT['run_id'], LOCAL_PORT['params']))
    print('=' * 76)

    if not rebal:
        print()
        print('[!] %s 不是本月第 1 个交易日 —— 策略当天【不调仓】，'
              '只做炸板离场检查（10:00）。' % t)
        print('    要看下次调仓，把 TRADE_DATE 改成 %s。' % first_day)
        print('    下面仍然把「若今天调仓会选出什么」打出来，供参考。')
        print()

    holds = resolve_holdings(prev)
    print('  当前持仓 %d 只，可用现金 %.2f，分配口径 %s'
          % (len(holds), CASH, BUY_ALLOC))

    target, backup, A, B, dy, dy_b = pick(t, prev)

    print()
    print('-' * 76)
    print('目标组合 %d 只（Sleeve A 低波 %d + Sleeve B 价值 %d，去重后）'
          % (len(target), len(A), len(B)))
    print('-' * 76)
    # [!] 必须去重：某票既在目标又在持仓时，target + HOLDINGS 会有重复代码，
    #     get_price 返回重复行 -> set_index('code') 索引重复 ->
    #     px.loc[s,'close'] 返回 Series 而不是数字 -> 后面格式化直接 TypeError。
    #     打桩时就是在这里炸的。dict.fromkeys 保序去重。
    codes = list(dict.fromkeys(list(target) + list(holds)))
    px = get_price(codes, end_date=prev, frequency='1d',
                   count=1, fields=['close'], fq='pre', panel=False,
                   skip_paused=False, fill_paused=True).set_index('code')
    px = px[~px.index.duplicated(keep='last')]        # 双保险
    for i, s in enumerate(target):
        tag = 'A' if s in A else 'B'
        nm = get_security_info(s).display_name
        print('  %2d. [%s] %-12s %-8s  T-1收盘 %8.2f   股息率 %.2f%%'
              % (i + 1, tag, s, nm, px.loc[s, 'close'],
                 _dr(dy, dy_b, s) * 100))
    if backup:
        print('  备选 %d 只：%s' % (len(backup), ', '.join(backup)))

    # ---- 卖出：持仓里不在目标的 ----
    print()
    print('-' * 76)
    print('需要卖出')
    print('-' * 76)
    sells = [s for s in holds if s not in target]
    est_cash = CASH
    if not sells:
        print('  （无）')
    for s in sells:
        sh = holds[s]
        p = float(px.loc[s, 'close']) if s in px.index else 0.0
        val = sh * p
        est_cash += val
        print('  卖出 %-12s %-8s %8d 股   估算金额 %12.2f （按 T-1 收盘 %.2f）'
              % (s, get_security_info(s).display_name, sh, val, p))

    # ---- 买入：目标里没持有的。数量算法与回测版 trade() 逐笔一致 ----
    print()
    print('-' * 76)
    print('需要买入   分配口径 %s' % BUY_ALLOC)
    print('           [!] 数量是【估算】：限价按 T-1 收盘 x1.05（与回测版同算法）。')
    print('               实盘按开盘价成交，股数会变，以下单当时的可用现金重算为准。')
    print('-' * 76)
    buys = [s for s in target if s not in holds]
    if not buys:
        print('  （无，目标全部已持有）')

    def lot(code, cash_for_one):
        """一手 100 股的限价成本 + 这笔钱能买几手（向下取整到 100 股）。"""
        limit = round(float(px.loc[code, 'close']) * 1.05, 2)
        return limit, 100 * int(cash_for_one / limit / 100) if limit > 0 else 0

    plan, skipped, spent = {}, [], 0.0
    n = len(buys)
    if n:
        if BUY_ALLOC == 'seq':
            # JQ v7：逐笔按当时剩余现金分配，跳过的份额流给后面 -> 后面超配
            cash = est_cash
            for i, code in enumerate(buys):
                per = cash / (n - i)
                limit, amt = lot(code, per)
                if amt <= 0:
                    skipped.append((code, limit, per))
                    continue
                plan[code] = (amt, limit)
                cash -= amt * limit
                spent += amt * limit
        else:
            # 第一轮：固定 per = 现金/只数
            per = est_cash / n
            for code in buys:
                limit, amt = lot(code, per)
                if amt <= 0:
                    skipped.append((code, limit, per))
                    continue
                plan[code] = (amt, limit)
                spent += amt * limit
            if BUY_ALLOC == 'twopass' and plan:
                # 第二轮：剩余现金（含买不进的那份 + 取整残差）平分给已建仓的
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
        print('  [买不进] %-12s %-8s 一手 %d 股需 %.0f 元，而分到 %.0f 元'
              % (code, get_security_info(code).display_name, 100, limit * 100, per))
    if skipped and BUY_ALLOC == 'fixed':
        print('        ^ fixed 口径下这些份额【闲置】，共 %.0f 元（%.1f%% 仓位拖累）'
              % (sum(p for _, _, p in skipped),
                 100.0 * sum(p for _, _, p in skipped) / est_cash if est_cash else 0))
        print('        改 BUY_ALLOC=\'twopass\' 可把它分给买得进的那些')
    total_cost = spent

    print()
    print('-' * 76)
    print('  可用现金（含卖出估算） %14.2f' % est_cash)
    print('  买入预计占用           %14.2f' % total_cost)
    print('  剩余                   %14.2f' % (est_cash - total_cost))
    print('  持仓不动的 %d 只：%s'
          % (len([s for s in holds if s in target]),
             ', '.join(s for s in holds if s in target) or '（无）'))
    print('-' * 76)
    print('[!] 三条与回测的已知差异，看结果时算进去：')
    print('    1. beta 用 JQ get_factor_values（与本地自算差约 +0.05pp）')
    print('    2. 炸板离场本脚本【不处理】—— 那是每日 10:00 的事，不在调仓日预览里')
    print('    3. 股数随开盘价变，这里是 T-1 收盘 x1.05 的估算')


main()
