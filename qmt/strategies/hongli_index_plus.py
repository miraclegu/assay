#coding:gbk
#==============================================================================
# 信号执行器 —— 读本地 assay 导出的目标持仓 CSV，在 QMT 里调仓
#
# 配套：assay/export_signal.py
#   python3 export_signal.py strategies/红利/红利指数增强.py \
#       --param div_method=fiscal_year --cash 1000000 -o signal.csv
#   把 signal.csv 拷到 Windows，改下面的 SIGNAL_PATH。
#
# 为什么是这个形态，而不是把策略整条移植过来：
#   红利指数增强要分红历史 + 252 日 beta 回归；sgmspeg_v0b 要 jqfactor 近似
#   （Barra 式 5 年回归斜率）。在 QMT 上重新推导这些因子，等于用一套没核对过
#   的字段名和 as-of 语义重造因子链 —— 而本地那套结论全建立在已验证的
#   「双键 as-of(pub_date + change_date)」口径上。重造一遍最可能的结果是
#   【看着能跑、数字悄悄是错的】。所以选股留在本地，QMT 只做执行。
#   （froec 线不同：它的因子简单且已完整移植，见 roec_weekly_rotation.py）
#
# 用法：QMT -> 策略 -> 新建 Python 策略 -> 粘贴 -> 周期 1d -> 主图 000905.SH
#==============================================================================
import csv
import datetime as dt
import os

#============================== 与本地策略的关联 ==============================
# [!] 这不是注释，是【可校验的声明】：python3 qmt/check.py 会拿它去核对
#     本地策略在不在、run_id 在不在归档、参数对不对得上、
#     基线指标与 stats.json 是否一致、以及本文件是否与模板逐字节同步。
#
# 本文件 = 红利指数增强（信号执行：选股在本地，QMT 只下单）
# 本地   : strategies/红利/红利指数增强.py
# 归档   : 20260828-210010-adcde3   年化 20.07% / 回撤 17.95% / 夏普 1.30
# 参数   : {'div_method': 'fiscal_year'}
#
# 用前先在本地导出信号：
#   python3 export_signal.py strategies/红利/红利指数增强.py --param div_method=fiscal_year --cash 1000000 -o signal_hongli.csv
#
# [!] 本文件由 qmt/gen.py 从 qmt/_tpl/signal_executor.py 生成，**不要手工改**。
#     要改逻辑改模板，要改参数改 gen.py 的 PROFILES，然后重跑 gen。
LOCAL_PORT = {
    'kind': 'executor',
    'generated_from': '_tpl/signal_executor.py',
    'profiles': {
        'hongli_index_plus': {
            'strategy': 'strategies/红利/红利指数增强.py',
            'run_id': '20260828-210010-adcde3',
            'local': {'div_method': 'fiscal_year'},
            'metrics': {'annual': 20.07, 'max_drawdown': 17.95, 'sharpe': 1.3},
        },
    },
    'exporter': 'export_signal.py',
}

SIGNAL_PATH = r'D:\work\finacial\signal_hongli.csv'  # <- 改成你的路径
ACCOUNT      = ''            # 留空则用平台注入的全局 account
ACCOUNT_TYPE = 'STOCK'

MAX_STALE_DAYS   = 5         # 信号超过 N 个自然日就拒绝交易（见下）
GROSS            = 0.98      # 目标总仓位上限，留一点缓冲防废单
MIN_LOT          = 100
ORDER_PRICE_TYPE = 5         # 5=最新价
REBAL_TOL        = 0.005     # 权重偏离小于这个就不动，省手续费
VERBOSE          = True


class _G:
    pass


g = _G()


def init(C):
    g.acct = ACCOUNT or globals().get('account', '')
    g.acct_type = ACCOUNT_TYPE
    g.last_asof = None
    g.said = set()               # 同一条提示只打一次（回测里会走几千根 bar）
    print('[init] 信号执行器  account=%s  signal=%s' % (g.acct or '(空)', SIGNAL_PATH))
    if not g.acct:
        print('[!] 账号为空 —— 平台没注入全局 account，读持仓/读资金都会失败，')
        print('    因而【不会下任何单】。把本文件顶部的 ACCOUNT 改成你的资金账号。')
    print('[!] 本文件是【信号执行器】，只在实盘/模拟盘用。')
    print('    回测模式下它会拒绝交易 —— 信号是「今天该拿什么」，')
    print('    拿它去回放历史就是标准的未来函数（见 handlebar 的 asof 守卫）。')


def _once(key, msg):
    """同一条提示只打一次。回测/实盘每根 bar 都会走到这里，
    不去重的话日志会被同一句话刷满几千行（实测就是这样）。"""
    if key not in g.said:
        g.said.add(key)
        print(msg)


def _read_signal():
    """返回 (asof, {qmt_code: weight})；读不到就返回 (None, {})。"""
    if not os.path.exists(SIGNAL_PATH):
        _once('nofile', '[!] 信号文件不存在: %s\n'
                        '    先在本地跑 export_signal.py 生成，再拷到这个路径。'
              % SIGNAL_PATH)
        return None, {}
    asof, w = None, {}
    try:
        with open(SIGNAL_PATH, 'r') as f:
            for row in csv.DictReader(f):
                asof = row.get('asof') or asof
                code = (row.get('qmt_code') or '').strip()
                if code:
                    w[code] = float(row['weight'])
    except Exception as e:
        print('[!] 信号文件解析失败: %s' % e)
        return None, {}
    return asof, w


def _days_ahead(asof, today):
    """信号日期比当前 bar 新多少天（>0 就是未来信号）。"""
    try:
        a = dt.datetime.strptime(str(asof)[:10].replace('-', ''), '%Y%m%d').date()
        t = dt.datetime.strptime(str(today)[:8], '%Y%m%d').date()
    except Exception:
        return 0
    return (a - t).days


def _stale(asof, today):
    """信号过期就【不交易】，而不是照着旧名单下单。

    这条是本文件最重要的安全阀：本地忘了重跑导出、或拷贝没覆盖，
    执行器会拿着上周的名单继续调仓，而且完全不报错 —— 那比不交易糟得多。
    """
    if not asof:
        return True
    try:
        a = dt.datetime.strptime(str(asof)[:10].replace('-', ''), '%Y%m%d').date()
        t = dt.datetime.strptime(str(today)[:8], '%Y%m%d').date()
    except Exception:
        return True
    return (t - a).days > MAX_STALE_DAYS


def _positions(C):
    out = {}
    try:
        objs = get_trade_detail_data(g.acct, g.acct_type, 'POSITION')
    except Exception as e:
        print('[warn] 读持仓失败: %s' % e)
        return out
    for o in objs or []:
        vol = int(getattr(o, 'm_nVolume', 0) or 0)
        if vol <= 0:
            continue
        code = '%s.%s' % (o.m_strInstrumentID, o.m_strExchangeID)
        out[code] = {'code': code, 'volume': vol,
                     'can_use': int(getattr(o, 'm_nCanUseVolume', 0) or 0),
                     'price': float(getattr(o, 'm_dLastPrice', 0) or 0),
                     'value': float(getattr(o, 'm_dMarketValue', 0) or 0)}
    return out


def _total_asset(C):
    try:
        accs = get_trade_detail_data(g.acct, g.acct_type, 'ACCOUNT')
        if accs:
            return float(accs[0].m_dBalance)
    except Exception as e:
        print('[warn] 读账户失败: %s' % e)
    return 0.0


def handlebar(C):
    idx = C.barpos
    if idx < 1:
        return
    today = C.get_bar_timetag(idx)
    today = dt.datetime.fromtimestamp(today / 1000.0).strftime('%Y%m%d')

    asof, target = _read_signal()
    if not target:
        return
    ahead = _days_ahead(asof, today)
    if ahead > 0:
        # ★ 信号比当前 bar 还新 = 拿未来的持仓名单去交易历史，标准未来函数。
        #   回测模式下必然命中这一条，所以回测里本策略【什么都不做】——
        #   这是对的：信号执行器只在实盘/模拟盘有意义。
        _once('ahead', '[!] 信号 asof=%s 比当前 bar %s 还新 %d 天，拒绝交易。\n'
                       '    回测模式下这是预期行为（用未来名单回放历史 = 未来函数）；\n'
                       '    实盘请确认系统日期与信号日期。'
              % (asof, today, ahead))
        return
    if _stale(asof, today):
        _once('stale', '[!] 信号 asof=%s 距今超过 %d 天，不交易（请重跑 export_signal.py）'
              % (asof, MAX_STALE_DAYS))
        return
    if asof != g.last_asof:
        g.last_asof = asof
        print('[信号] asof=%s  %d 只' % (asof, len(target)))

    total = _total_asset(C)
    if total <= 0:
        _once('nocash', '[!] 读到的总资产为 0 —— 账号未配置或未连接交易端，不下单。')
        return
    pos = _positions(C)

    # 先卖：不在目标里的清掉
    for code, p in pos.items():
        if code not in target:
            vol = p['can_use'] if p['can_use'] > 0 else 0
            if vol > 0:
                if VERBOSE:
                    print('  卖出[%s] %d股（已不在目标）' % (code, vol))
                passorder(24, 1101, g.acct, code, ORDER_PRICE_TYPE, -1, vol, C)

    # 再按权重调：偏离超过阈值才动
    scale = GROSS / max(sum(target.values()), 1e-9) if sum(target.values()) > GROSS else 1.0
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
        if px <= 0:
            continue
        if want_val > cur_val:
            vol = int((want_val - cur_val) / (px * 1.02 * MIN_LOT)) * MIN_LOT
            if vol >= MIN_LOT:
                if VERBOSE:
                    print('  买入[%s] %d股 -> 目标%.2f%%' % (code, vol, w * 100))
                passorder(23, 1101, g.acct, code, ORDER_PRICE_TYPE, -1, vol, C)
        else:
            vol = int((cur_val - want_val) / (px * MIN_LOT)) * MIN_LOT
            vol = min(vol, cur['can_use'] if cur else 0)
            if vol >= MIN_LOT:
                if VERBOSE:
                    print('  减仓[%s] %d股 -> 目标%.2f%%' % (code, vol, w * 100))
                passorder(24, 1101, g.acct, code, ORDER_PRICE_TYPE, -1, vol, C)
