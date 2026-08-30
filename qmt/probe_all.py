#coding:gbk
#==============================================================================
# 探针（总）：把五条 QMT 策略需要的【全部未核对项】一次问完
#==============================================================================
# 跑法：QMT -> 新建 Python 策略 -> 粘贴 -> 周期【1d】-> 区间含 2025-06-30
#       -> 运行 -> 把整段日志贴回。只在第一根 bar 跑一次。
#
# 设计原则（前两轮探针的教训）：
#   1) 每一节都独立 try/except —— v1 探针在最后一个候选上抛异常，
#      把整轮结果打断了一半。这轮任何一节炸掉都不影响其余。
#   2) 不用 `not r` 判空 —— r 可能是 pandas Panel/DataFrame，会抛
#      "The truth value of ... is ambiguous"。一律先看类型。
#   3) 凡是本地有标准答案的，直接嵌进来自判对错，不靠人肉比对。
#
# 已确认、不再验（探针 v1 结论）：
#   C.get_divid_factors(code)  只接受 1~2 个参数
#   -> {毫秒时间戳: [7个数]}；key = 除权日【北京时间 00:00】
#      （utcfromtimestamp 会差一天）；[0] = 每股税前现金分红（元/股），
#      与本地 bonus_ratio_rmb/10 逐条吻合 11/11；[3][4] = 配股比例/配股价；
#      [6] = 复权因子
#==============================================================================
import datetime as dt

REF_DATE = '20250630'          # 参考日：下面所有标准答案都取自这一天

# ---- 本地标准答案（取自 datalake，用来判 QMT 返回的口径对不对）----
REF = {
    'a_share_count': 5152,     # 该日在市 A 股只数
    'st_count': 175,           # 该日风险警示只数
    'listed': {                # 上市日
        '601398.SH': '20061027', '601088.SH': '20071009',
        '000651.SZ': '19961118', '300750.SZ': '20180611',
        '688981.SH': '20200716',
    },
    'industry': {              # 申万一级（判板块命名用）
        '000651.SZ': '家用电器', '300750.SZ': '电气设备',
        '601088.SH': '煤炭', '601398.SH': '银行', '688981.SH': '电子',
    },
    'limit': {                 # 该日涨停价 / 跌停价 / 涨跌幅
        '601398.SH': (8.25, 6.75, 0.10),
        '000651.SZ': (49.74, 40.70, 0.10),
        '300750.SZ': (301.19, 200.79, 0.20),
    },
    'bar': {                   # 该日 不复权 OHLC + 成交额(亿)
        '601398.SH': (7.48, 7.589, 7.61, 7.42, 23.080),
        '601088.SH': (39.93, 40.541, 40.71, 39.89, 11.815),
    },
    'mv': {                    # 总市值(亿) / 流通市值(亿) / PE(TTM)
        '601398.SH': (27051.2, 20463.6, 7.465),
        '601088.SH': (8054.7, 6685.5, 14.716),
    },
    'beta252': {'601398.SH': 0.2043, '601088.SH': 0.3877, '300750.SZ': 1.5895},
    'fin': {                   # report_date -> (扣非ROE, 营收同比, 净利同比, 扣非净利/亿)
        '601398.SH': {'20241231': (0.0246, 0.0187, 0.0135, 966.60)},
        '601088.SH': {'20241231': (0.0338, -0.0677, 0.1322, 140.91)},
        '600028.SH': {'20241231': (0.0050, -0.0461, -0.1861, 40.90)},
    },
}

CODES = ['601398.SH', '601088.SH', '000651.SZ', '300750.SZ', '688981.SH']
_done = [False]


def init(C):
    print('[探针总] 等第一根 bar；参考日 %s' % REF_DATE)


def handlebar(C):
    if _done[0]:
        return
    _done[0] = True
    for name, fn in (
            ('A 板块/股票池', _a_sectors),
            ('B 合约详情（上市日/名称/涨跌停）', _b_detail),
            ('C 行情 get_market_data_ex', _c_market),
            ('D 财务 get_financial_data', _d_fin),
            ('E 扣非净利 与 增长率', _e_deducted),
            ('F 市值/PE 能不能凑出来', _f_valuation),
            ('G beta 对沪深300', _g_beta),
            ('H 账户/持仓/下单接口', _h_trade),
    ):
        print('')
        print('=' * 78)
        print('[%s]' % name)
        print('=' * 78)
        try:
            fn(C)
        except Exception as e:
            import traceback
            print('   !! 本节异常 %s: %s' % (type(e).__name__, str(e)[:120]))
            for ln in traceback.format_exc().splitlines()[-4:]:
                print('      ' + ln)
    print('')
    print('=' * 78)
    print('[探针总] 完了。把整段贴回。')
    print('=' * 78)


# ------------------------------------------------------------------ A
def _a_sectors(C):
    print('-- A1 全 A 股池：本地该日在市 %d 只，看哪个板块名能取到接近的数量'
          % REF['a_share_count'])
    for sec in ('沪深A股', '沪深京A股', 'A股', '沪深Ａ股', '全部A股', '沪深300'):
        try:
            lst = C.get_stock_list_in_sector(sec) or []
            print('   %-10s -> %5d 只  %s' % (sec, len(lst), lst[:3]))
        except Exception as e:
            print('   %-10s -> 异常 %s' % (sec, str(e)[:50]))
    print('-- A2 ST 板块：本地该日 %d 只' % REF['st_count'])
    for sec in ('ST板块', 'ST', '风险警示', '*ST', 'ST股票'):
        try:
            lst = C.get_stock_list_in_sector(sec) or []
            print('   %-10s -> %5d 只  %s' % (sec, len(lst), lst[:3]))
        except Exception as e:
            print('   %-10s -> 异常 %s' % (sec, str(e)[:50]))
    print('-- A3 行业板块命名（本地是申万一级，如 %s）'
          % list(REF['industry'].values())[:3])
    for sec in ('银行', '煤炭', '家用电器', '申万一级行业', '证监会行业'):
        try:
            lst = C.get_stock_list_in_sector(sec) or []
            hit = [c for c in lst if c in REF['industry']]
            print('   %-14s -> %5d 只   命中参考票 %s' % (sec, len(lst), hit))
        except Exception as e:
            print('   %-14s -> 异常 %s' % (sec, str(e)[:50]))
    print('-- A4 板块清单前 40 个（看命名风格）')
    for fn in ('get_sector_list', 'get_stock_list_in_sector'):
        try:
            r = getattr(C, fn)()
            print('   C.%s() -> %s' % (fn, str(r)[:400]))
            break
        except Exception as e:
            print('   C.%s() -> %s' % (fn, str(e)[:60]))


# ------------------------------------------------------------------ B
def _b_detail(C):
    print('-- B1 接口名：get_instrumentdetail vs get_instrument_detail')
    got = None
    for fn in ('get_instrumentdetail', 'get_instrument_detail'):
        try:
            d = getattr(C, fn)('601398.SH')
            print('   C.%s 可用，返回 %s' % (fn, type(d).__name__))
            print('      键: %s' % (sorted(d)[:30] if isinstance(d, dict) else str(d)[:200]))
            got = fn
            break
        except Exception as e:
            print('   C.%s -> %s: %s' % (fn, type(e).__name__, str(e)[:60]))
    if not got:
        print('   两个都不行，试模块级')
        try:
            d = get_instrumentdetail('601398.SH')          # noqa: F821
            print('   模块级 get_instrumentdetail 可用: %s' % (sorted(d)[:30],))
            got = 'MOD'
        except Exception as e:
            print('   模块级也不行: %s' % str(e)[:60])
    if not got:
        return
    print('-- B2 上市日 / 名称 / 涨跌停价 对账')
    for c in CODES:
        try:
            d = getattr(C, got)(c) if got != 'MOD' else get_instrumentdetail(c)  # noqa: F821
        except Exception as e:
            print('   %-11s 异常 %s' % (c, str(e)[:50]))
            continue
        if not isinstance(d, dict):
            print('   %-11s 返回不是 dict: %s' % (c, str(d)[:80]))
            continue
        ld = d.get('OpenDate') or d.get('ListedDate') or d.get('list_date')
        nm = d.get('InstrumentName') or d.get('name')
        up = d.get('UpStopPrice') or d.get('up_stop_price')
        dn = d.get('DownStopPrice') or d.get('down_stop_price')
        want_ld = REF['listed'].get(c)
        ok = 'v' if (want_ld and str(ld).replace('-', '')[:8] == want_ld) else '?'
        print('   %-11s 上市=%-10s(本地%s)%s  名称=%-8s 涨停=%s 跌停=%s'
              % (c, ld, want_ld, ok, nm, up, dn))
    print('   本地该日涨跌停参考: %s' % REF['limit'])


# ------------------------------------------------------------------ C
def _c_market(C):
    print('-- C1 字段可得性（不复权），对账本地该日 OHLC+成交额(亿)')
    flds = ['open', 'close', 'high', 'low', 'volume', 'amount', 'preClose']
    try:
        r = C.get_market_data_ex(flds, ['601398.SH', '601088.SH'], period='1d',
                                 end_time=REF_DATE, count=1,
                                 dividend_type='none', fill_data=False)
    except Exception as e:
        print('   异常 %s: %s' % (type(e).__name__, str(e)[:90]))
        return
    print('   返回类型 %s' % type(r).__name__)
    for c in ('601398.SH', '601088.SH'):
        df = r.get(c) if isinstance(r, dict) else None
        if df is None:
            print('   %s -> 无' % c)
            continue
        try:
            print('   %s 列: %s' % (c, list(df.columns)))
            row = df.iloc[-1]
            o, cl, h, l, amt = REF['bar'][c]
            print('      QMT  o=%s c=%s h=%s l=%s amount=%s'
                  % (row.get('open'), row.get('close'), row.get('high'),
                     row.get('low'), row.get('amount')))
            print('      本地 o=%s c=%s h=%s l=%s amount=%.3f亿' % (o, cl, h, l, amt))
        except Exception as e:
            print('      取值失败 %s；原样 %s' % (str(e)[:50], str(df)[:200]))
    print('-- C2 前复权是否可用（止损要靠它做同基准比较）')
    try:
        r2 = C.get_market_data_ex(['close', 'low'], ['601398.SH'], period='1d',
                                  end_time=REF_DATE, count=3,
                                  dividend_type='front', fill_data=False)
        df = r2.get('601398.SH')
        print('   front 3 根: %s' % str(df)[:250])
    except Exception as e:
        print('   异常 %s' % str(e)[:80])


# ------------------------------------------------------------------ D
def _d_fin(C):
    print('-- D1 froec 已在用的 4 个字段能不能取到')
    flds = ['ASHAREBALANCESHEET.tot_shrhldr_eqy_excl_min_int',
            'ASHAREINCOME.net_profit_excl_min_int_inc',
            'CAPITALSTRUCTURE.total_capital',
            'CAPITALSTRUCTURE.circulating_capital']
    for f in flds:
        _fin_one(C, f, ['601398.SH'])
    print('-- D2 一次多字段多股票的返回结构（froec 就是这么调的）')
    try:
        r = C.get_financial_data(flds, ['601398.SH', '601088.SH'],
                                 '20231231', '20241231')
        print('   类型 %s' % type(r).__name__)
        if isinstance(r, dict):
            print('   键: %s' % list(r)[:5])
            k = list(r)[0]
            print('   r[%s] 类型 %s' % (k, type(r[k]).__name__))
            print('   %s' % str(r[k])[:500])
        else:
            print('   %s' % str(r)[:500])
    except Exception as e:
        print('   异常 %s: %s' % (type(e).__name__, str(e)[:100]))


def _fin_one(C, field, codes):
    try:
        r = C.get_financial_data([field], codes, '20241231', '20241231')
        if r is None:
            print('   %-52s -> None' % field)
            return
        print('   %-52s -> %s  %s' % (field, type(r).__name__, str(r)[:150]))
    except Exception as e:
        print('   %-52s -> %s: %s' % (field, type(e).__name__, str(e)[:60]))


# ------------------------------------------------------------------ E
def _e_deducted(C):
    print('-- E1 扣非净利润：红利 B 袖的 inc_return 要它。候选逐个试')
    print('   本地参考(2024年报, 亿元): %s'
          % dict((c, v['20241231'][3]) for c, v in REF['fin'].items()))
    for f in ('ASHAREFINANCIALINDICATOR.net_profit_after_ded_nr_lp',
              'ASHAREFINANCIALINDICATOR.deducted_profit',
              'ASHAREINCOME.net_profit_after_ded_nr_lp',
              'PERSHAREINDEX.net_profit_after_ded_nr_lp',
              'ASHAREFINANCIALINDICATOR.s_fa_deductedprofit'):
        _fin_one(C, f, ['601398.SH', '601088.SH', '600028.SH'])
    print('-- E2 增长率：QMT 直接给，还是要自己取相邻年度算')
    print('   本地参考(2024年报): 营收同比/净利同比 %s'
          % dict((c, (v['20241231'][1], v['20241231'][2])) for c, v in REF['fin'].items()))
    for f in ('ASHAREFINANCIALINDICATOR.yoy_or',
              'ASHAREFINANCIALINDICATOR.yoynetprofit',
              'ASHAREFINANCIALINDICATOR.yoyop',
              'ASHAREINCOME.tot_oper_rev'):
        _fin_one(C, f, ['601398.SH'])


# ------------------------------------------------------------------ F
def _f_valuation(C):
    print('-- F 市值/PE：QMT 有没有现成的，没有就用 close*总股本 自己凑')
    print('   本地该日 总市值(亿)/流通市值(亿)/PE(TTM): %s' % REF['mv'])
    for f in ('ASHAREEODDERIVATIVEINDICATOR.s_val_mv',
              'ASHAREEODDERIVATIVEINDICATOR.s_dq_mv',
              'ASHAREEODDERIVATIVEINDICATOR.s_val_pe_ttm',
              'PERSHAREINDEX.s_val_pe_ttm'):
        _fin_one(C, f, ['601398.SH'])
    print('   （取不到也不要紧：close × CAPITALSTRUCTURE.total_capital 即总市值，')
    print('     PE = 总市值 / TTM 归母净利，froec 移植里已经在这么算）')


# ------------------------------------------------------------------ G
def _g_beta(C):
    print('-- G beta 不需要外部数据，只要能取到指数日线就能自己回归')
    print('   本地该日 beta_252: %s' % REF['beta252'])
    for idx in ('000300.SH', '399300.SZ'):
        try:
            r = C.get_market_data_ex(['close'], [idx], period='1d',
                                     end_time=REF_DATE, count=5,
                                     dividend_type='none', fill_data=False)
            df = r.get(idx)
            print('   %s -> %s' % (idx, str(df)[:200] if df is not None else '无'))
        except Exception as e:
            print('   %s -> 异常 %s' % (idx, str(e)[:60]))


# ------------------------------------------------------------------ H
def _h_trade(C):
    print('-- H1 平台注入的全局 account 在不在（信号执行器截图里它是空的）')
    print('   globals().get("account") = %r' % globals().get('account'))
    print('-- H2 get_trade_detail_data 能不能调、返回什么')
    acct = globals().get('account') or ''
    for typ in ('ACCOUNT', 'POSITION'):
        for caller in ('模块级', 'C.'):
            try:
                f = get_trade_detail_data if caller == '模块级' else C.get_trade_detail_data  # noqa: F821
                r = f(acct, 'STOCK', typ)
                n = len(r) if r is not None else 0
                print('   %s get_trade_detail_data(%r,STOCK,%s) -> %d 条' % (caller, acct, typ, n))
                if n:
                    o = r[0]
                    ks = [k for k in dir(o) if k.startswith('m_')]
                    print('      对象字段: %s' % ks[:25])
                break
            except Exception as e:
                print('   %s %s -> %s: %s' % (caller, typ, type(e).__name__, str(e)[:60]))
    print('-- H3 passorder 在不在（只看有没有，不下单）')
    print('   passorder 可见: %s' % ('passorder' in globals()))
    print('   [!] froec 移植用的是 passorder(23=买/24=卖, 1101=按股数, acct, code,')
    print('       prtype, -1, volume, C)，prtype=5 取最新价。若你的版本签名不同，')
    print('       改移植文件里 open_position / close_position 两处即可。')
