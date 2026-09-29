# ============================================================================
# FROEC-TRADED 选股漏斗探针（聚宽研究环境）—— 用来和本地移植【逐层对数】
#
# 用途：2026-09-28 实测，本地与聚宽在同一决策日选出了不同的票
#   （本地买天瑞仪器 300165，聚宽买汉商集团 600774）。原因压在
#   **PB 半区的切点**上：本地算出天瑞仪器排第 1634 名，而门槛是
#   int(0.5*3273)=1636 —— 只差 2 名。任何一点口径差异都会翻转。
#
#   已知的口径差异（刻意的，不是 bug）：
#     · 聚宽原版用【现成的】valuation.pb_ratio 与 circulating_market_cap
#     · 本地自己算：pb = totalmv / equities（当日可见财报，严格 PIT）
#                   floatmv = share_change 的流通股本 x 收盘价
#   本探针的作用是量出**分歧面有多大**：只有边界上一两只，还是一片。
#
# 🔴 选股那段【逐字照搬】 jq/strategies/froec_traded.py 的 select_stocks，
#   只在每层加打印。不许凭印象重写 —— 重写出来的分歧分不清是口径差异
#   还是我抄错了。
#
# 用法：聚宽 -> 研究环境（Jupyter）-> 新建 notebook -> 粘贴全文 -> 运行
#       只改 TRADE_DATE 一处。
# ============================================================================
import datetime

import numpy as np
import pandas as pd
from jqdata import *

# ============================== 改这一处 ====================================
TRADE_DATE = '2026-09-29'      # 要对数的交易日（决策日 = 它的前一个交易日）
# ===========================================================================

# ---- 与 jq/strategies/froec_traded.py 完全一致的开关与名单 ----
TRADED_UNIVERSE = True         # 宇宙只含【决策日有成交】的票
KCB_688_ONLY = False           # 按 68* 排除科创板（含 689 开头的 CDR）
STOCK_NUM = 10                 # g.stock_num
INDUSTRY_CONTROL = True        # g.industry_control
INDUSTRY_FILTER_LIST = ['钢铁I', '煤炭I', '石油石化I', '采掘I',
                        '银行I', '非银金融I', '金融服务I',
                        '交运设备I', '交通运输I', '传媒I', '环保I']
# ---- 关联声明（jq/check.py 扫目录逐个核，缺了就报）----
# 🔴 探针也要声明它对标谁 —— 否则「照搬的是哪一版」无从查证，
#   而这正是它存在的全部理由。参数与回测版逐项一致，见上面那几个常量。
LOCAL_PORT = {
    'strategy': 'strategies/小市值/froec_traded.py',
    'run_id':   '20260829-170945-4926f5',
    'params':   {'stop_loss': 0.35, 'stop_intraday': 1},
    'metrics':  {'annual': 40.87, 'max_drawdown': 38.54, 'sharpe': 1.33},
    'backtest': {'start': '2016-01-04', 'end': '2026-08-07', 'cash': 100000,
                 'benchmark': '000905.XSHG', 'freq': 'day'},
    'aligned': {'probe': '本文件是 froec_traded.py 的【选股漏斗探针】：'
                         '选股那段逐字照搬 select_stocks，只在每层加打印，'
                         '不下单、不涉及止损与仓位，所以只对齐选股参数'},
}

# 想单独盯的票（本地与聚宽分歧的那两只）
WATCH = ['300165.XSHE', '600774.XSHG']


def _prev_trade_day(d):
    days = get_trade_days(end_date=d, count=2)
    assert len(days) == 2, '取不到 %s 的前一个交易日' % d
    assert str(days[-1]) == str(d), '%s 不是交易日（最近的是 %s）' % (d, days[-1])
    return days[0]


def filter_st_stock(stock_list, prev):
    """ST / 退市 / 停牌 一次过滤。用 T-1 的 get_extras + 名称前缀双查。

    🔴 **照搬 jq/strategies/froec_traded_preview.py 里那份**，不自己写一套。
      原版用 `get_current_data()`（研究环境里它只给"今天"，对历史日期是错的），
      preview 当初就为此定过案 —— 两处各写一份必然分叉，而分叉会改变 `base`
      的只数，进而移动 `int(0.5*n)` 这个切点。本次要查的分歧就压在切点上
      （本地 1634 名 vs 门槛 1636），差几只票就能翻转结论。
    """
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


def filter_kcb_stock(stock_list):
    if KCB_688_ONLY:
        return [s for s in stock_list if s[0:3] != '688']
    return [s for s in stock_list if s[0:2] != '68']


def filter_new_stock(stock_list, yesterday):
    return [s for s in stock_list
            if not yesterday - get_security_info(s).start_date
            < datetime.timedelta(days=250)]


def get_stock_industry(securities, watch_date, level='sw_l1',
                       method='industry_name'):
    industry_dict = get_industry(securities, watch_date)
    industry_ser = pd.Series({k: v.get(level, {method: np.nan})[method]
                              for k, v in industry_dict.items()})
    return industry_ser.to_frame('industry')


def filter_industry(industry_df, select_industry):
    return industry_df.query('industry != @select_industry').index.tolist()


def _mark(lst, tag):
    """把 WATCH 里的票在这一层的存活情况打出来 —— 掉在哪一层一眼看见。"""
    for c in WATCH:
        print('        [%s] %s %s' % (tag, c, '在' if c in lst else '✗ 已掉出'))


def main():
    t = TRADE_DATE
    yesterday = _prev_trade_day(t)
    print('=' * 78)
    print(' FROEC-TRADED 漏斗探针   交易日 %s   决策日(T-1) %s' % (t, yesterday))
    print('=' * 78)

    initial_list = get_all_securities(date=yesterday).index.tolist()
    print('  L1 全部在册                      %5d 只' % len(initial_list))

    if TRADED_UNIVERSE:
        vol = get_price(initial_list, end_date=yesterday, frequency='1d',
                        count=1, fields=['volume'], panel=False,
                        skip_paused=False, fill_paused=False)
        traded = set(vol[vol['volume'] > 0]['code'])
        initial_list = [x for x in initial_list if x in traded]
        print('  L2 当日有成交（TRADED_UNIVERSE） %5d 只' % len(initial_list))

    initial_list = filter_new_stock(initial_list, yesterday)
    print('  L3 上市满 250 天                 %5d 只' % len(initial_list))
    initial_list = filter_kcb_stock(initial_list)
    print('  L4 排除科创（KCB_688_ONLY=%s）  %5d 只' % (KCB_688_ONLY, len(initial_list)))
    initial_list = filter_st_stock(initial_list, yesterday)
    print('  L5 排除 ST/退                    %5d 只' % len(initial_list))
    _mark(initial_list, 'L5')

    # ---- PB 过滤（逐字照搬原版）----
    q = query(valuation.code, valuation.pb_ratio, indicator.eps).filter(
        valuation.code.in_(initial_list)).order_by(valuation.pb_ratio.asc())
    df = get_fundamentals(q, date=yesterday)
    print('  L6 取到估值                      %5d 只' % len(df))
    df = df[df['eps'] > 0]
    print('  L7 eps > 0                       %5d 只' % len(df))
    df = df[df['pb_ratio'] > 0]
    n = len(df.code)
    print('  L8 pb_ratio > 0                  %5d 只   <- base（本地对应 pb_n）' % n)
    pb_list = list(df.code)[:int(0.5 * n)]
    print('  L9 PB 半区 int(0.5*%d)           %5d 只' % (n, len(pb_list)))
    _mark(pb_list, 'L9')

    # 🔴 关键对照：WATCH 那两只的 pb 与**名次**（本地是 1634 / 838，门槛 1636）
    order = list(df.code)
    print()
    print('  ---- PB 名次（按 pb_ratio 升序；门槛 = 第 %d 名）----' % int(0.5 * n))
    for c in WATCH:
        if c in order:
            print('     %-13s pb_ratio=%9.4f   第 %5d / %d 名   %s'
                  % (c, float(df.loc[df.code == c, 'pb_ratio'].iloc[0]),
                     order.index(c) + 1, n,
                     '进半区' if order.index(c) + 1 <= int(0.5 * n) else '✗ 被挡在半区外'))
        else:
            print('     %-13s 不在 L8 里（eps<=0 / pb<=0 / 更早就被过滤）' % c)

    # ---- ROEC 过滤（逐字照搬原版，含 1000 只一组的分页）----
    interval = 1000
    pb_len = len(pb_list)
    if pb_len <= interval:
        dfr = get_history_fundamentals(
            pb_list, fields=[indicator.code, indicator.roe],
            watch_date=yesterday, count=5, interval='1q')
    else:
        df_num = pb_len // interval
        dfr = get_history_fundamentals(
            pb_list[:interval], fields=[indicator.code, indicator.roe],
            watch_date=yesterday, count=5, interval='1q')
        for i in range(df_num):
            dfi = get_history_fundamentals(
                pb_list[interval * (i + 1):min(pb_len, interval * (i + 2))],
                fields=[indicator.code, indicator.roe],
                watch_date=yesterday, count=5, interval='1q')
            dfr = dfr.append(dfi)
    dfr = dfr.groupby('code').apply(lambda x: x.reset_index()).roe.unstack()
    dfr['increase'] = (4 * dfr.iloc[:, 4] - dfr.iloc[:, 0] - dfr.iloc[:, 1]
                       - dfr.iloc[:, 2] - dfr.iloc[:, 3])
    dfr.dropna(inplace=True)
    dfr.sort_values(by='increase', ascending=False, inplace=True)
    temp_list = list(dfr.index)
    n2 = len(temp_list)
    print()
    print('  L10 有 5 期 ROE                  %5d 只   <- 本地对应 roe_n' % n2)
    roe_list = temp_list[:int(0.1 * n2)]
    print('  L11 ROE 加速度前十分位 int(0.1*%d) %4d 只' % (n2, len(roe_list)))
    _mark(roe_list, 'L11')
    print()
    print('  ---- ROE 加速度名次（本地：300165 第 29 / 600774 第 71，共 1627）----')
    for c in WATCH:
        if c in temp_list:
            print('     %-13s increase=%9.4f   第 %5d / %d 名   %s'
                  % (c, float(dfr.loc[c, 'increase']), temp_list.index(c) + 1, n2,
                     '进十分位' if temp_list.index(c) + 1 <= int(0.1 * n2) else '✗ 未进'))
        else:
            print('     %-13s 不在 L10 里（PB 半区没进，或缺 5 期 ROE）' % c)

    # ---- 行业过滤 ----
    if INDUSTRY_CONTROL:
        industry_df = get_stock_industry(roe_list, yesterday)
        ROE_list = filter_industry(industry_df, INDUSTRY_FILTER_LIST)
        print()
        print('  L12 行业过滤后                   %5d 只（剔除 %d）'
              % (len(ROE_list), len(roe_list) - len(ROE_list)))
    else:
        ROE_list = roe_list

    # ---- 市值排序（逐字照搬原版）----
    q = query(valuation.code, valuation.circulating_market_cap).filter(
        valuation.code.in_(ROE_list)).order_by(
        valuation.circulating_market_cap.asc())
    dfc = get_fundamentals(q, date=yesterday)
    ROEC_list = list(dfc.code)

    # ---- 前 20 只：把对数需要的列全打出来 ----
    ind = get_stock_industry(ROEC_list[:20], yesterday)
    pbm = df.set_index('code')
    print()
    print('=' * 78)
    print(' 前 20 只（按流通市值升序）—— 前 %d 只就是目标组合' % STOCK_NUM)
    print('=' * 78)
    print(' %-3s %-13s %-10s %10s %10s %8s %10s %8s  %s'
          % ('#', '代码', '名称', '流通市值亿', 'pb_ratio', 'pb名次',
             'roe_inc', 'roe名次', '行业'))
    for i, c in enumerate(ROEC_list[:20], 1):
        cap = float(dfc.loc[dfc.code == c, 'circulating_market_cap'].iloc[0])
        pb = float(pbm.loc[c, 'pb_ratio']) if c in pbm.index else float('nan')
        prk = (order.index(c) + 1) if c in order else -1
        inc = float(dfr.loc[c, 'increase']) if c in dfr.index else float('nan')
        rrk = (temp_list.index(c) + 1) if c in temp_list else -1
        print(' %-3d %-13s %-10s %10.2f %10.4f %8d %10.4f %8d  %s'
              % (i, c, get_security_info(c).display_name, cap, pb, prk, inc, rrk,
                 ind.loc[c, 'industry'] if c in ind.index else '?'))

    print()
    print(' 目标组合（前 %d）：%s' % (STOCK_NUM, ROEC_list[:STOCK_NUM]))
    print()
    print(' ---- 本地那边同一天的数，照着对 ----')
    print('   base(pb>0) 3273 · PB 半区切点 1636 · 有 5 期 ROE 1627 · 十分位切点 162')
    print('   300165.XSHE 天瑞仪器  流通 20.73 亿  pb=2.2971  pb 第 1634 名  roe_inc=0.2175 第 29 名  机械设备I')
    print('   600774.XSHG 汉商集团  流通 22.44 亿  pb=1.5057  pb 第  838 名  roe_inc=0.1421 第 71 名  医药生物I')
    print('   本地前 12：002910 301126 301152 300500 300165 300575 605122 301062 300375 603168 600774 002942')


main()
