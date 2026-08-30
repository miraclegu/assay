#coding:gbk
#==============================================================================
# QMT 接口探针 —— 唯一的一个，不要再新建
#==============================================================================
# 工作方式：
#   已确认的写进 CONFIRMED，只打印结论、【不再调接口】；
#   未确定的放 OPEN，每跑一轮把确认下来的从 OPEN 挪进 CONFIRMED。
#   文件会随着核对推进自然变短，而且随时能看到「已知什么、还差什么」的全貌。
#
# 跑法：QMT -> 新建 Python 策略 -> 粘贴 -> 周期 1d -> 区间含 2025-06-30 -> 贴回日志
#
# 三条纪律（前几轮踩出来的）：
#   1) 每节独立 try/except —— 曾经一节抛异常把整轮结果打断了一半
#   2) 不用 `not r` 判空 —— 返回可能是 pandas Panel，会抛
#      "The truth value of a Panel is ambiguous"
#   3) 凡本地有标准答案的一律嵌进来自判，不靠人肉比对
#==============================================================================

REF_DATE = '20250630'
CODE  = '601398.SH'
CODES = ['601398.SH', '601088.SH']

# ---------------------------------------------------------------- 已确认
# 格式: 项目 -> (确认日期, 结论)
CONFIRMED = [
    ('行情 get_market_data_ex', '2026-08-30',
     "字段 open/close/high/low/volume/amount/preClose 全有；dividend_type='front' 可用。"
     '601398 与 601088 的 OHLC+成交额与本地【逐项精确吻合】。可放心用。'),
    ('合约详情 C.get_instrumentdetail(code)', '2026-08-30',
     '可用，返回 dict。OpenDate=上市日(5/5 对)、InstrumentName=名称、'
     'FloatVolume/FloatVolumn=流通股本(两种拼写都有)、PreClose、IsTrading、InstrumentStatus。'),
    ('分红 C.get_divid_factors(code)', '2026-08-30',
     '只接受 1~2 个参数(传 start/end 报 TypeError)。返回 {毫秒时间戳: [7个数]}；'
     'key=除权日【北京时间00:00】(用 utcfromtimestamp 会差一天)；'
     '[0]=每股税前现金分红(元/股)，与本地 bonus_ratio_rmb/10 【11/11 精确吻合】；'
     '[3][4]=配股比例/配股价；[6]=复权因子。'),
    ('指数日线', '2026-08-30',
     "000300.SH 可取(399300.SZ 返回空)。-> beta 对沪深300 自己回归即可，不需要外部数据。"),
    ('账户 get_trade_detail_data', '2026-08-30',
     "模块级可调；空账号 '' 也能读到 ACCOUNT 1 条(回测用默认账户)，"
     'm_dBalance/m_dAvailable/m_dAssetBalance 等字段齐全。POSITION 空仓时 0 条。'
     'passorder 全局可见。'),
    ('全A股票池', '2026-08-30',
     "C.get_stock_list_in_sector('沪深A股') -> 5216 只(本地同日在市 5152，量级对)；"
     "'沪深京A股' 5555；'沪深300' 300。'A股'/'全部A股' 等名字为 0。"),
    ('[!] 涨跌停价不能用 UpStopPrice/DownStopPrice', '2026-08-30',
     '它们是【实时值不是历史值】：探针跑于 2026-08-30 而参考日 2025-06-30，'
     '601398 返回 8.60/7.04 而实际 8.25/6.75；300750 返回 447.60 而实际 301.19。'
     '必须自己按 preClose×(1±涨跌幅) 算 —— 移植里 _limit_price 本来就是这么做的。'),
    ('[!] C.get_sector_list 不存在', '2026-08-30',
     "报 '_PyContext' object has no attribute。板块清单只能走 xtdata 模块。"),
    ('xtquant 可 import，六个函数都在', '2026-08-30',
     'get_sector_list / get_stock_list_in_sector / download_sector_data / '
     'get_financial_data / download_financial_data / get_instrument_detail 全部存在。'),
    ('[!] 但直接调 xtdata 会报「无法连接行情服务」', '2026-08-30',
     'xtdata.py line 134 get_client() 抛 Exception。原因是【没有先 connect】—— '
     '本仓库 QMT/data/connection.py 用的是 xtdata.connect(ip, port=58610)。'
     '所以调任何 xtdata 函数前必须先连接；若连接本身失败，需在 QMT 客户端'
     '「设置-接口配置」里开启该端口。见下面 S0b。'),
]

# 本地标准答案（2025-06-30）
REF = {
    'st_count': 175,
    'st_head': ['000004.SZ', '000070.SZ', '000430.SZ', '000488.SZ', '000504.SZ'],
    'industry_n': {'电子': 599, '电气设备': 453, '家用电器': 132, '银行': 42, '煤炭': 38},
    'industry_of': {'601398.SH': '银行', '601088.SH': '煤炭',
                    '000651.SZ': '家用电器', '300750.SZ': '电气设备'},
    'fin_601398_2024': {'归母净资产_亿': 34946.0, '归母净利_亿': 3658.6,
                        '总股本_亿股': 3564.1, '流通股本_亿股': 2696.1},
}

_done = [False]


def init(C):
    print('[探针] 等第一根 bar；参考日 %s' % REF_DATE)


def handlebar(C):
    if _done[0]:
        return
    _done[0] = True
    print('=' * 78)
    print('已确认 %d 项（只打印结论，不再调接口）' % len(CONFIRMED))
    print('=' * 78)
    for name, when, concl in CONFIRMED:
        print('  v %s   [%s]' % (name, when))
        for ln in _wrap(concl, 72):
            print('      ' + ln)
    for name, fn in OPEN:
        print('')
        print('=' * 78)
        print('[待确认] %s' % name)
        print('=' * 78)
        try:
            fn(C)
        except Exception as e:
            import traceback
            print('   !! %s: %s' % (type(e).__name__, str(e)[:120]))
            for ln in traceback.format_exc().splitlines()[-3:]:
                print('      ' + ln)
    print('')
    print('=' * 78)
    print('[探针] 完。把整段贴回 —— 确认下来的会被挪进 CONFIRMED，不再重跑。')
    print('=' * 78)


def _wrap(s, n):
    out, cur = [], ''
    for ch in s:
        cur += ch
        if len(cur) >= n:
            out.append(cur); cur = ''
    if cur:
        out.append(cur)
    return out


_XT = [None]
QMT_PORT = 58610
QMT_IPS = ('127.0.0.1', '192.168.0.103')


def _xt():
    """拿到【已连接】的 xtdata。

    [!] 直接 import 完就调函数会报「无法连接行情服务」——
        xtdata 是个客户端，必须先 connect 到 QMT 的数据服务端口。
        本仓库 QMT/data/connection.py 就是这么做的，我第一版探针漏了这步。
    """
    if _XT[0] is not None:
        return _XT[0]
    import xtquant.xtdata as xtdata
    last = None
    for ip in QMT_IPS:
        try:
            c = xtdata.connect(ip=ip, port=QMT_PORT, remember_if_success=True)
            if c is not None:
                _XT[0] = xtdata
                return xtdata
        except Exception as e:
            last = e
    try:                                  # 无参形态（有的版本自己找）
        xtdata.connect()
        _XT[0] = xtdata
        return xtdata
    except Exception as e:
        last = e
    raise RuntimeError('xtdata 连接失败（端口 %d，试过 %s）：%s'
                       % (QMT_PORT, QMT_IPS, str(last)[:90]))


def _peek(r):
    """挑【非 NaN 的值】出来 —— 只看结构会漏掉「结构对但全空」。"""
    try:
        import numpy as np
        if r is None:
            return 'None'
        if hasattr(r, 'values'):
            v = np.asarray(r.values, dtype='float64').ravel()
            ok = v[~np.isnan(v)]
            return ('非空 %d 个，样例 %s' % (len(ok), ok[:4])) if len(ok) else '全 NaN'
        if isinstance(r, dict):
            return 'dict 键 %s' % list(r)[:3]
        return str(r)[:80]
    except Exception as e:
        return '看不了(%s)' % str(e)[:40]


# ================================ 待确认 S：板块 ==============================
def s0_connect(C):
    print('   上一轮：import 成功但一调就报「无法连接行情服务」—— 漏了 connect。')
    print('   逐个试连接方式，看哪个能通（端口 %d）：' % QMT_PORT)
    import xtquant.xtdata as xtdata
    for name, fn in (
            ("connect(ip='127.0.0.1', port=%d)" % QMT_PORT,
             lambda: xtdata.connect(ip='127.0.0.1', port=QMT_PORT, remember_if_success=True)),
            ("connect(ip='192.168.0.103', port=%d)" % QMT_PORT,
             lambda: xtdata.connect(ip='192.168.0.103', port=QMT_PORT, remember_if_success=True)),
            ('connect() 无参', lambda: xtdata.connect()),
    ):
        try:
            c = fn()
            ok = None
            try:
                ok = c.is_connected()
            except Exception:
                pass
            print('   %-42s -> %s  is_connected=%s' % (name, type(c).__name__, ok))
        except Exception as e:
            print('   %-42s -> %s: %s' % (name, type(e).__name__, str(e)[:60]))
    print('   连不上时的排查顺序：')
    print('     1) QMT 客户端「设置 - 接口配置」里开启端口 %d' % QMT_PORT)
    print('     2) 确认 QMT 已登录且行情已连接（右下角状态）')
    print('     3) 防火墙放行该端口')
    print('   [!] 若始终连不上，ST 与行业过滤只能走 InstrumentName 名称兜底，')
    print('       行业黑名单则【无法实现】—— 这是相对本地回测的实质差异。')


def s1_sector_names(C):
    print('   把真实板块名【全部列出来】，不再一个个猜')
    try:
        x = _xt()
    except Exception:
        print('   跳过（xtdata 不可用）'); return
    try:
        x.download_sector_data(); print('   download_sector_data() 已调用')
    except Exception as e:
        print('   download_sector_data 异常: %s' % str(e)[:70])
    secs = x.get_sector_list() or []
    print('   板块总数 %d' % len(secs))
    print('   含 ST 的      : %s' % ([s for s in secs if 'ST' in str(s).upper()][:20] or '（无）'))
    print('   含风险/警示/退 : %s' % [s for s in secs if any(k in str(s) for k in ('风险', '警示', '退'))][:20])
    print('   含「申万」     : %s' % [s for s in secs if '申万' in str(s)][:30])
    print('   与本地行业名重合: %s' % [s for s in secs if s in REF['industry_n']][:20])
    print('   -- 前 120 个（看命名风格）--')
    for i in range(0, min(len(secs), 120), 6):
        print('      %s' % secs[i:i + 6])


def s2_st(C):
    print('   本地 %s 当日 ST %d 只，前几只 %s' % (REF_DATE, REF['st_count'], REF['st_head']))
    try:
        x = _xt()
    except Exception:
        print('   跳过'); return
    secs = x.get_sector_list() or []
    cands = [s for s in secs if 'ST' in str(s).upper()] or \
            [s for s in secs if any(k in str(s) for k in ('风险', '警示'))]
    if not cands:
        print('   无 ST 板块 -> 只能走名称兜底（InstrumentName 含 ST）'); return
    for s in cands[:5]:
        try:
            lst = x.get_stock_list_in_sector(s) or []
            print('   %-16s -> %4d 只   命中本地前几只: %s'
                  % (s, len(lst), [c for c in lst if c in REF['st_head']]))
        except Exception as e:
            print('   %-16s -> 异常 %s' % (s, str(e)[:50]))


def s3_industry(C):
    print('   本地各行业只数: %s' % REF['industry_n'])
    print('   本地归属: %s' % REF['industry_of'])
    try:
        x = _xt()
    except Exception:
        print('   跳过'); return
    secs = x.get_sector_list() or []
    cands = [s for s in secs if '申万' in str(s)][:12]
    cands += [s for s in secs if s in REF['industry_n']]
    if not cands:
        cands = [s for s in secs if any(k in str(s) for k in ('银行', '煤炭', '家用电器'))][:12]
    if not cands:
        print('   没找到行业板块 -> froec 的 11 个行业黑名单在 QMT 上【无法实现】'); return
    for s in cands[:12]:
        try:
            lst = x.get_stock_list_in_sector(s) or []
            print('   %-22s -> %4d 只   命中参考票: %s'
                  % (s, len(lst), [c for c in lst if c in REF['industry_of']]))
        except Exception as e:
            print('   %-22s -> 异常 %s' % (s, str(e)[:50]))


def s4_timetag(C):
    print('   [!] 板块成分随时间变，回测必须取【时点】成分。')
    print('       若不支持 real_timetag，拿今天的 ST 名单过滤 2016 年历史 = 未来函数。')
    try:
        x = _xt()
    except Exception:
        print('   跳过'); return
    secs = x.get_sector_list() or []
    s = ([t for t in secs if 'ST' in str(t).upper()] or ['沪深A股'])[0]
    print('   用板块 %r 试三种时间参数写法：' % s)
    for arg in (REF_DATE, int(REF_DATE), REF_DATE + '000000'):
        try:
            lst = x.get_stock_list_in_sector(s, arg) or []
            print('   传 %-16r -> %d 只' % (arg, len(lst)))
        except Exception as e:
            print('   传 %-16r -> %s: %s' % (arg, type(e).__name__, str(e)[:60]))


# ================================ 待确认 F：财务 ==============================
def f1_downloaded(C):
    print('   [!] froec 三条全靠财务算 PB 与单季 ROE，这是当前唯一硬阻塞。')
    print('   本地参考 601398 2024年报: %s' % REF['fin_601398_2024'])
    try:
        x = _xt()
    except Exception:
        print('   xtdata 不可用，跳过下载检查'); return
    try:
        x.download_financial_data([CODE], ['Balance', 'Income', 'CapitalStructure'])
        print('   download_financial_data 调用成功 —— 之前很可能就是【没下载】')
    except Exception as e:
        print('   download 异常 %s: %s' % (type(e).__name__, str(e)[:100]))
    try:
        r = x.get_financial_data([CODE], ['Balance', 'Income'], '20240101', '20241231')
        print('   xtdata.get_financial_data -> %s' % type(r).__name__)
        print('   %s' % str(r)[:700])
    except Exception as e:
        print('   xtdata.get_financial_data 异常 %s: %s' % (type(e).__name__, str(e)[:100]))


def f2_fields(C):
    print('   换表名/字段写法，看有没有哪个能出值')
    for f in ('ASHAREBALANCESHEET.tot_shrhldr_eqy_excl_min_int',
              'Balance.tot_shrhldr_eqy_excl_min_int',
              'BALANCESHEET.tot_shrhldr_eqy_excl_min_int',
              'ASHAREINCOME.net_profit_excl_min_int_inc',
              'Income.net_profit_excl_min_int_inc',
              'CAPITALSTRUCTURE.total_capital',
              'CapitalStructure.total_capital',
              'ASHARECAPITALIZATION.tot_shr',
              'PERSHAREINDEX.s_fa_eps_basic',
              # 红利要的扣非净利
              'ASHAREFINANCIALINDICATOR.net_profit_after_ded_nr_lp',
              'ASHAREFINANCIALINDICATOR.deducted_profit'):
        try:
            r = C.get_financial_data([f], [CODE], '20240101', '20241231')
            print('   %-52s -> %-10s %s' % (f, type(r).__name__, _peek(r)))
        except Exception as e:
            print('   %-52s 异常 %s' % (f, str(e)[:50]))


def f3_signature(C):
    F = 'ASHAREINCOME.net_profit_excl_min_int_inc'
    for name, fn in (
            ('单股票一整年', lambda: C.get_financial_data([F], [CODE], '20240101', '20241231')),
            ('三年跨度', lambda: C.get_financial_data([F], [CODE], '20220101', '20241231')),
            ('report_type=report', lambda: C.get_financial_data(
                [F], [CODE], '20240101', '20241231', report_type='report')),
            ('report_type=announce', lambda: C.get_financial_data(
                [F], [CODE], '20240101', '20241231', report_type='announce')),
            ('日期带横杠', lambda: C.get_financial_data([F], [CODE], '2024-01-01', '2024-12-31')),
            ('不传日期', lambda: C.get_financial_data([F], [CODE])),
    ):
        try:
            r = fn()
            print('   %-22s -> %-10s %s' % (name, type(r).__name__, _peek(r)))
        except Exception as e:
            print('   %-22s -> %s: %s' % (name, type(e).__name__, str(e)[:60]))


def f4_panel_values(C):
    print('   上一轮只打了 Panel 结构没打值 —— 这次把值挖出来')
    flds = ['ASHAREBALANCESHEET.tot_shrhldr_eqy_excl_min_int',
            'ASHAREINCOME.net_profit_excl_min_int_inc',
            'CAPITALSTRUCTURE.total_capital',
            'CAPITALSTRUCTURE.circulating_capital']
    try:
        r = C.get_financial_data(flds, CODES, '20240101', '20241231')
    except Exception as e:
        print('   异常 %s' % str(e)[:90]); return
    print('   类型 %s   %s' % (type(r).__name__, _peek(r)))
    for attr in ('items', 'major_axis', 'minor_axis'):
        if hasattr(r, attr):
            ax = list(getattr(r, attr))
            print('   %-11s %d 个: %s ... %s' % (attr, len(ax), ax[:3], ax[-2:]))
    try:
        if hasattr(r, 'items'):
            it = list(r.items)[0]
            print('   r[%s] 尾 5 行:' % it)
            print('%s' % str(r[it].tail(5)))
    except Exception as e:
        print('   取值失败 %s' % str(e)[:70])


def f5_fallback(C):
    print('   万一财务始终取不到，三条策略各自的处境：')
    print('   · froec / froec_traded / froec_traded_stop35')
    print('       要 PB(净资产) + 单季ROE -> 【无法实现】，只能等财务数据')
    print('   · sgmspeg_v0b')
    print('       只要 流通市值 + 累计EPS>0。流通股本已确认可从 FloatVolume 取；')
    print('       若 EPS 也取不到，换近似就是【另一条策略】，不能再声称与本地一致')
    print('   · 红利指数增强')
    print('       股息率(已通) + beta(已通) + PE/ROE/增长率(要财务) -> 同样等财务')


OPEN = [
    ('S0b xtdata 连接（上一轮就卡在这）', s0_connect),
    ('S1 真实板块名清单', s1_sector_names),
    ('S2 ST 板块对账', s2_st),
    ('S3 行业板块对账', s3_industry),
    ('S4 历史时点成分 real_timetag', s4_timetag),
    ('F1 财务数据下没下载', f1_downloaded),
    ('F2 表名/字段名', f2_fields),
    ('F3 调用签名', f3_signature),
    ('F4 Panel 里到底有没有值', f4_panel_values),
    ('F5 退路', f5_fallback),
]
