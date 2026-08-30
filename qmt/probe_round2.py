#coding:gbk
#==============================================================================
# 探针 第二轮：板块名 + 财务数据 —— 上一轮剩下的两个阻塞，一次问完
#==============================================================================
# 上一轮（qmt/probe_all.py）结论：行情、合约详情、分红、指数、账户全部可用，
# 见 qmt/PROBE_FINDINGS.md。剩下两件挡路的：
#
#   (甲) 板块名取不到：ST 与申万行业的候选名全返回 0 只，
#        C.get_sector_list() 不存在（'_PyContext' object has no attribute）。
#        ★ 线索：本仓库 QMT/data/st_status.py 用的是 xtdata.get_sector_list()，
#          说明这个函数在【xtdata 模块】上有、在 ContextInfo 上没有。
#          所以本轮先把真实板块名【全部列出来】，不再猜。
#
#   (乙) get_financial_data 全 NaN：froec 三条全靠它算 PB 与单季 ROE。
#        区分三种原因：没下载 / 字段名不对 / 调用签名不对。
#
# 跑法：QMT -> 新建 Python 策略 -> 粘贴 -> 周期 1d -> 区间含 2025-06-30 -> 看日志
#==============================================================================

REF_DATE = '20250630'
CODE  = '601398.SH'
CODES = ['601398.SH', '601088.SH']

# 本地标准答案（2025-06-30）
REF = {
    'st_count': 175,
    'st_head': ['000004.SZ', '000070.SZ', '000430.SZ', '000488.SZ', '000504.SZ'],
    'industry_n': {'电子': 599, '电气设备': 453, '家用电器': 132, '银行': 42, '煤炭': 38},
    'industry_of': {'601398.SH': '银行', '601088.SH': '煤炭',
                    '000651.SZ': '家用电器', '300750.SZ': '电气设备'},
    # 601398 2024 年报
    'fin': {'归母净资产_亿': 34946.0, '归母净利_亿': 3658.6,
            '总股本_亿股': 3564.1, '流通股本_亿股': 2696.1},
}

_done = [False]


def init(C):
    print('[探针2] 等第一根 bar；参考日 %s' % REF_DATE)


def handlebar(C):
    if _done[0]:
        return
    _done[0] = True
    for name, fn in (('S0 xtdata 能不能 import', _s0),
                     ('S1 真实板块名清单', _s1),
                     ('S2 ST 板块对账', _s2),
                     ('S3 行业板块对账', _s3),
                     ('S4 历史时点成分 real_timetag', _s4),
                     ('F1 财务数据下没下载', _f1),
                     ('F2 表名/字段名', _f2),
                     ('F3 调用签名', _f3),
                     ('F4 Panel 里到底有没有值', _f4),
                     ('F5 退路', _f5)):
        print('')
        print('=' * 78)
        print('[%s]' % name)
        print('=' * 78)
        try:
            fn(C)
        except Exception as e:
            import traceback
            print('   !! %s: %s' % (type(e).__name__, str(e)[:120]))
            for ln in traceback.format_exc().splitlines()[-3:]:
                print('      ' + ln)
    print('')
    print('[探针2] 完了，整段贴回')


def _xt():
    import xtquant.xtdata as xtdata
    return xtdata


# ------------------------------------------------------------------ S0
def _s0(C):
    try:
        x = _xt()
        print('   import xtquant.xtdata 成功: %s' % x)
        for fn in ('get_sector_list', 'get_stock_list_in_sector',
                   'download_sector_data', 'get_financial_data',
                   'download_financial_data', 'get_instrument_detail'):
            print('   xtdata.%-26s %s' % (fn, '有' if hasattr(x, fn) else '无'))
    except Exception as e:
        print('   import 失败 %s: %s' % (type(e).__name__, str(e)[:90]))
        print('   -> 若策略环境里没有 xtquant，板块名只能靠 ContextInfo 猜，')
        print('      那 ST 与行业过滤就得走「按名称判断」的兜底路径。')


# ------------------------------------------------------------------ S1
def _s1(C):
    try:
        x = _xt()
    except Exception:
        print('   跳过（xtdata 不可用）')
        return
    try:
        x.download_sector_data()
        print('   download_sector_data() 已调用')
    except Exception as e:
        print('   download_sector_data 异常: %s' % str(e)[:80])
    secs = x.get_sector_list() or []
    print('   板块总数 %d' % len(secs))
    print('')
    print('   -- 含 ST 的板块名 --')
    hits = [s for s in secs if 'ST' in str(s).upper()]
    print('      %s' % (hits[:20] if hits else '（一个都没有）'))
    print('   -- 含「风险/警示/退」的 --')
    print('      %s' % [s for s in secs if any(k in str(s) for k in ('风险', '警示', '退'))][:20])
    print('   -- 含「申万」的 --')
    print('      %s' % [s for s in secs if '申万' in str(s)][:30])
    print('   -- 与本地行业名重合的 --')
    print('      %s' % [s for s in secs if s in REF['industry_n']][:20])
    print('')
    print('   -- 全部板块名（前 120 个，看命名风格）--')
    for i in range(0, min(len(secs), 120), 6):
        print('      %s' % secs[i:i + 6])


# ------------------------------------------------------------------ S2
def _s2(C):
    print('   本地 %s 当日 ST %d 只，前几只 %s'
          % (REF_DATE, REF['st_count'], REF['st_head']))
    try:
        x = _xt()
    except Exception:
        print('   跳过')
        return
    secs = x.get_sector_list() or []
    cands = [s for s in secs if 'ST' in str(s).upper()] or \
            [s for s in secs if any(k in str(s) for k in ('风险', '警示'))]
    if not cands:
        print('   没有 ST 相关板块 -> 只能走名称兜底（InstrumentName 含 ST）')
        return
    for s in cands[:5]:
        try:
            lst = x.get_stock_list_in_sector(s) or []
            hit = [c for c in lst if c in REF['st_head']]
            print('   %-16s -> %4d 只   命中本地前几只: %s' % (s, len(lst), hit))
        except Exception as e:
            print('   %-16s -> 异常 %s' % (s, str(e)[:50]))


# ------------------------------------------------------------------ S3
def _s3(C):
    print('   本地各行业只数: %s' % REF['industry_n'])
    print('   本地归属: %s' % REF['industry_of'])
    try:
        x = _xt()
    except Exception:
        print('   跳过')
        return
    secs = x.get_sector_list() or []
    # 优先申万，其次名字直接等于行业名
    cands = [s for s in secs if '申万' in str(s)][:12]
    cands += [s for s in secs if s in REF['industry_n']]
    if not cands:
        cands = [s for s in secs if any(k in str(s) for k in ('银行', '煤炭', '家用电器'))][:12]
    if not cands:
        print('   没找到行业板块 -> froec 的 11 个行业黑名单在 QMT 上【无法实现】')
        return
    for s in cands[:12]:
        try:
            lst = x.get_stock_list_in_sector(s) or []
            hit = [c for c in lst if c in REF['industry_of']]
            print('   %-22s -> %4d 只   命中参考票: %s' % (s, len(lst), hit))
        except Exception as e:
            print('   %-22s -> 异常 %s' % (s, str(e)[:50]))


# ------------------------------------------------------------------ S4
def _s4(C):
    print('   板块成分随时间变，回测必须取【时点】成分而不是最新成分。')
    print('   QMT/data/st_status.py 的注释说 get_stock_list_in_sector 支持 real_timetag。')
    try:
        x = _xt()
    except Exception:
        print('   跳过')
        return
    secs = x.get_sector_list() or []
    s = ([t for t in secs if 'ST' in str(t).upper()] or ['沪深A股'])[0]
    for arg in (REF_DATE, int(REF_DATE), REF_DATE + '000000'):
        try:
            lst = x.get_stock_list_in_sector(s, arg) or []
            print('   get_stock_list_in_sector(%r, %r) -> %d 只' % (s, arg, len(lst)))
        except Exception as e:
            print('   传 %r -> %s: %s' % (arg, type(e).__name__, str(e)[:60]))


# ------------------------------------------------------------------ F1
def _f1(C):
    print('   本地参考 601398 2024年报: %s' % REF['fin'])
    try:
        x = _xt()
    except Exception:
        print('   xtdata 不可用，跳过下载检查')
        return
    for fn in ('download_financial_data', 'download_financial_data2'):
        print('   xtdata.%-26s %s' % (fn, '有' if hasattr(x, fn) else '无'))
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


# ------------------------------------------------------------------ F2
def _f2(C):
    for f in ('ASHAREBALANCESHEET.tot_shrhldr_eqy_excl_min_int',
              'Balance.tot_shrhldr_eqy_excl_min_int',
              'BALANCESHEET.tot_shrhldr_eqy_excl_min_int',
              'ASHAREINCOME.net_profit_excl_min_int_inc',
              'Income.net_profit_excl_min_int_inc',
              'CAPITALSTRUCTURE.total_capital',
              'CapitalStructure.total_capital',
              'ASHARECAPITALIZATION.tot_shr',
              'PERSHAREINDEX.s_fa_eps_basic'):
        _one(C, f)


def _one(C, field):
    try:
        r = C.get_financial_data([field], [CODE], '20240101', '20241231')
    except Exception as e:
        print('   %-52s 异常 %s' % (field, str(e)[:50]))
        return
    print('   %-52s -> %-10s %s' % (field, type(r).__name__, _peek(r)))


def _peek(r):
    """挑【非 NaN 的值】出来看 —— 只看结构会漏掉「结构对但全空」这种情况。"""
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


# ------------------------------------------------------------------ F3
def _f3(C):
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


# ------------------------------------------------------------------ F4
def _f4(C):
    flds = ['ASHAREBALANCESHEET.tot_shrhldr_eqy_excl_min_int',
            'ASHAREINCOME.net_profit_excl_min_int_inc',
            'CAPITALSTRUCTURE.total_capital',
            'CAPITALSTRUCTURE.circulating_capital']
    try:
        r = C.get_financial_data(flds, CODES, '20240101', '20241231')
    except Exception as e:
        print('   异常 %s' % str(e)[:90])
        return
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


# ------------------------------------------------------------------ F5
def _f5(C):
    print('   万一财务数据始终取不到，各策略的处境：')
    try:
        d = C.get_instrumentdetail(CODE)
        print('   get_instrumentdetail 可用键: %s' % sorted(d)[:24])
        print('   FloatVolume=%s  PreClose=%s' % (d.get('FloatVolume'), d.get('PreClose')))
    except Exception as e:
        print('   异常 %s' % str(e)[:70])
    print('   · froec / froec_traded / froec_traded_stop35')
    print('       需要 PB（净资产）+ 单季 ROE -> 没有财务数据【无法实现】，只能等')
    print('   · sgmspeg_v0b')
    print('       只需要 流通市值 + 累计EPS>0。流通股本已确认可从 FloatVolume 取，')
    print('       若 EPS 也取不到，用别的近似就是【另一条策略】，不能再声称与本地一致')
    print('   · 红利指数增强')
    print('       股息率(已通) + beta(已通) + PE/ROE/增长率(要财务) -> 同样等财务')
