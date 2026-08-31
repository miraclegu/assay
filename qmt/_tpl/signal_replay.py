#coding:gbk
#==============================================================================
# 信号回放器 —— 在 QMT 里【回测】本地 assay 的历史选股结果
#
# 与 signal_executor.py 的分工：
#   signal_executor.py   实盘/模拟盘。读「今天该拿什么」的单日快照。
#                        回测模式下它【拒绝交易】（单日名单回放历史 = 未来函数）。
#   signal_replay.py     回测。读【全历史调仓事件序列】，每天只用 date==当日 的行。
#                        天然不可能取到未来 —— 它从不查看未来日期的行。
#
# 配套：assay/export_signal.py --series
#   python3 export_signal.py strategies/红利/红利指数增强.py \
#       --param div_method=fiscal_year --start 2016-01-01 --end 2026-06-30 \
#       --cash 1000000 --series -o signal_series.csv
#   CSV 列：date, jq_code, qmt_code, weight   （date 是 YYYYMMDD）
#
# 这个形态能测什么、不能测什么 —— 想清楚再看结果：
#   能测：QMT 的撮合与费用模型、真实涨跌停成交、分钟级择时（本地只有日线，
#         盘中止损的真实触发价、9:31 买入这类问题只有在这里才测得了）。
#   不能测：选股逻辑的独立性。选股完全是本地那套已验证口径的结果，
#         QMT 侧只是执行。要独立复现选股得走原生移植（红利受分红口径限制，
#         见 qmt/PROBE_FINDINGS.md 的 I5）。
#
# 为什么按【调仓事件】而不是每日持仓：每日权重随价格漂移，
#   照每日权重天天调仓会造出大量虚假换手，测出来的滑点/费用全是假的。
#   导出侧用「逐日持仓股数是否变化」识别真实成交日（股数不随价格漂移）。
#
# 用法：QMT -> 策略 -> 新建 Python 策略 -> 粘贴 -> 周期 1d -> 主图 000905.SH
#       回测区间要【落在 CSV 的日期范围内】，否则前面一直空仓。
#==============================================================================
import csv
import datetime as dt
import os

{LOCAL_PORT}

#============================== 可调参数 ======================================
SIGNAL_PATH = r'D:\work\finacial\signal_series.csv'   # <- 改成你的路径

ACCOUNT      = ''            # 留空则用平台注入的全局 account
ACCOUNT_TYPE = 'STOCK'

GROSS            = 0.98      # 目标总仓位上限，留一点缓冲防废单
MIN_LOT          = 100
ORDER_PRICE_TYPE = 5         # 5=最新价
REBAL_TOL        = 0.005     # 权重偏离小于这个就不动，省手续费
CATCH_UP         = True      # 回测起点晚于首个事件时，第一根 bar 补建到最近的事件
VERBOSE          = False     # 回测事件多（红利 10 年 354 次），默认关掉逐笔日志


class _G:
    pass


try:
    g
except NameError:
    g = _G()


def init(C):
    g.acct = ACCOUNT or globals().get('account', '')
    g.acct_type = ACCOUNT_TYPE
    g.said = set()
    g.events = None          # {YYYYMMDD: {qmt_code: weight}}
    g.dates = []             # 有序事件日
    g.applied = None         # 最近应用的事件日
    g.n_applied = 0
    _load(C)


def _once(key, msg):
    """同一条提示只打一次 —— 回测会走几千根 bar，不去重日志会被刷满。"""
    if key not in g.said:
        g.said.add(key)
        print(msg)


def _load(C):
    """读全历史事件序列。格式不对要【明确报错】，不能静默当空。"""
    if not os.path.exists(SIGNAL_PATH):
        print('[!] 信号文件不存在: %s' % SIGNAL_PATH)
        print('    先在本地跑：export_signal.py <策略> --series -o signal_series.csv')
        g.events, g.dates = {}, []
        return
    ev = {}
    n_row, cols = 0, None
    try:
        with open(SIGNAL_PATH, 'r') as f:
            rd = csv.DictReader(f)
            cols = rd.fieldnames or []
            for row in rd:
                d = (row.get('date') or '').strip().replace('-', '')
                code = (row.get('qmt_code') or '').strip()
                if not d or not code:
                    continue
                n_row += 1
                if code.upper() == 'CASH':          # 清仓事件
                    ev.setdefault(d, {})
                    continue
                ev.setdefault(d, {})[code] = float(row['weight'])
    except Exception as e:
        print('[!] 信号文件解析失败: %s' % e)
        g.events, g.dates = {}, []
        return
    if 'date' not in (cols or []):
        # ★ 拿单日格式（asof 列）来回放 = 用未来名单交易历史，必须拦住
        print('[!] 这个 CSV 是【单日格式】（列 %s），不是事件序列。' % cols)
        print('    单日名单回放历史就是未来函数。请用 --series 重新导出，')
        print('    或改用 signal_executor.py（实盘用）。')
        g.events, g.dates = {}, []
        return
    g.events = ev
    g.dates = sorted(ev)
    print('[init] 信号回放器  account=%s' % (g.acct or '(空)'))
    print('[init] %s' % SIGNAL_PATH)
    print('[init] 事件 %d 次，%d 行，区间 %s ~ %s'
          % (len(g.dates), n_row, g.dates[0] if g.dates else '-',
             g.dates[-1] if g.dates else '-'))


def _bar_date(C, idx):
    return dt.datetime.fromtimestamp(
        C.get_bar_timetag(idx) / 1000.0).strftime('%Y%m%d')


def _positions(C):
    out = {}
    try:
        objs = get_trade_detail_data(g.acct, g.acct_type, 'POSITION')
    except Exception:
        return out
    for o in objs or []:
        code = '%s.%s' % (o.m_strInstrumentID, o.m_strExchangeID)
        out[code] = {'vol': o.m_nVolume, 'can_use': o.m_nCanUseVolume,
                     'value': o.m_dMarketValue, 'price': o.m_dLastPrice}
    return out


def _total_asset(C):
    try:
        objs = get_trade_detail_data(g.acct, g.acct_type, 'ACCOUNT')
    except Exception:
        return 0.0
    for o in objs or []:
        v = getattr(o, 'm_dAssetBalance', 0) or getattr(o, 'm_dBalance', 0)
        if v:
            return float(v)
    return 0.0


def _due_event(today):
    """今天该应用哪个事件。

    正常情况：date == today 的那个。
    CATCH_UP：回测起点晚于首个事件日时，第一根 bar 要补建到【最近一个不晚于今天】
              的事件 —— 否则会一直空仓到下一次事件（红利 p90 间隔 27 天，
              最坏能空一个多月，指标会被现金整段稀释）。
    ★ 无论哪种情况都【只看不晚于今天的日期】，所以不可能取到未来。
    """
    if today in g.events:
        return today
    if CATCH_UP and g.applied is None:
        past = [d for d in g.dates if d <= today]
        return past[-1] if past else None
    return None


def handlebar(C):
    idx = C.barpos
    # [!] 不能像 signal_executor 那样 skip idx<1 —— 那是为了保证有前一根 bar，
    #     回放不需要。skip 掉第一根就会丢一次事件（干跑实测 353/354）。
    if idx < 0 or not g.dates:
        return
    today = _bar_date(C, idx)

    day = _due_event(today)
    if day is None:
        return                            # 无事件 -> 保持不动，这是对的

    target = g.events[day]
    total = _total_asset(C)
    if total <= 0:
        _once('nocash', '[!] 总资产为 0 —— 账号未配置或未连接，不下单。')
        return
    pos = _positions(C)

    if day != g.applied:
        g.applied = day
        g.n_applied += 1
        tag = '（补建）' if day != today else ''
        if VERBOSE or g.n_applied <= 3:
            print('[%s] 应用事件 %s%s  %d 只' % (today, day, tag, len(target)))

    # 先卖：不在目标里的清掉
    for code, p in pos.items():
        if code not in target:
            vol = p['can_use'] if p['can_use'] > 0 else 0
            if vol > 0:
                if VERBOSE:
                    print('  卖出[%s] %d股' % (code, vol))
                passorder(24, 1101, g.acct, code, ORDER_PRICE_TYPE, -1, vol, C)

    # 再按权重调
    ssum = sum(target.values())
    scale = (GROSS / ssum) if ssum > GROSS else 1.0
    for code, w in sorted(target.items(), key=lambda kv: -kv[1]):
        want_val = total * w * scale
        cur = pos.get(code)
        cur_val = cur['value'] if cur else 0.0
        if abs(cur_val - want_val) <= total * REBAL_TOL:
            continue
        px = cur['price'] if cur and cur['price'] > 0 else None
        if px is None:
            try:
                d = C.get_market_data_ex(['close'], [code], period='1d',
                                         end_time=today, count=1,
                                         dividend_type='none', subscribe=False)
                px = float(d[code]['close'].iloc[-1])
            except Exception:
                continue
        if not px or px <= 0:
            continue
        if want_val > cur_val:
            vol = int((want_val - cur_val) / (px * 1.02 * MIN_LOT)) * MIN_LOT
            if vol >= MIN_LOT:
                if VERBOSE:
                    print('  买入[%s] %d股 -> %.2f%%' % (code, vol, w * 100))
                passorder(23, 1101, g.acct, code, ORDER_PRICE_TYPE, -1, vol, C)
        else:
            vol = int((cur_val - want_val) / (px * MIN_LOT)) * MIN_LOT
            vol = min(vol, cur['can_use'] if cur else 0)
            if vol >= MIN_LOT:
                if VERBOSE:
                    print('  减仓[%s] %d股 -> %.2f%%' % (code, vol, w * 100))
                passorder(24, 1101, g.acct, code, ORDER_PRICE_TYPE, -1, vol, C)
