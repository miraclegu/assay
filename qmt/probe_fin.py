#coding:gbk
#==============================================================================
# 探针（财务专项）：get_financial_data 全 NaN 到底是哪一种原因
#==============================================================================
# 上一轮总探针的结论：行情、合约详情、分红、指数、账户全部可用，
# 只有 get_financial_data 返回全 NaN —— 而 froec 那三条移植全靠它算
# PB 与单季 ROE，这是唯一的硬阻塞。
#
# 本探针只做一件事：把「全 NaN」的三种可能一次分开
#   H1 数据没下载   -> 换个众所周知有数据的字段/时间也全 NaN，且 xtdata 报未下载
#   H2 字段名不对   -> 换表名/换写法能取到值
#   H3 调用方式不对 -> 换签名（report_type / 日期格式 / 单股票）能取到值
#
# 跑法：QMT -> 新建 Python 策略 -> 粘贴 -> 周期 1d -> 区间含 2025-06-30 -> 看日志
#==============================================================================
import datetime as dt

CODE = '601398.SH'                 # 工商银行，财务数据不可能缺
CODES = ['601398.SH', '601088.SH']
# 本地实际值（2024 年报）：归母净资产 / 归母净利 / 总股本 / 流通股本
REF = {'归母净资产_亿': 34946.0, '归母净利_亿': 3658.6,
       '总股本_亿股': 3564.1, '流通股本_亿股': 2696.1}

_done = [False]


def init(C):
    print('[财务探针] 等第一根 bar')


def handlebar(C):
    if _done[0]:
        return
    _done[0] = True
    for name, fn in (('H1 数据到底下没下载', _h1),
                     ('H2 表名/字段名', _h2),
                     ('H3 调用签名', _h3),
                     ('H4 Panel 里到底有没有值', _h4),
                     ('H5 退路：不用财务数据能不能凑出 PB/ROE', _h5)):
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
    print('[财务探针] 完了，整段贴回')
    print('本地参考(601398 2024年报): %s' % REF)


# ----------------------------------------------------------------- H1
def _h1(C):
    print('-- 若是「没下载」，下面这些查询接口会明说')
    for expr in ('xtdata.get_financial_data',
                 'xtdata.download_financial_data'):
        try:
            import xtquant.xtdata as xtdata
            f = getattr(xtdata, expr.split('.')[-1], None)
            print('   %s -> %s' % (expr, '存在' if f else '不存在'))
        except Exception as e:
            print('   import xtquant 失败: %s' % str(e)[:70])
            break
    print('-- 直接用 xtdata 取一次（绕过 ContextInfo）')
    try:
        import xtquant.xtdata as xtdata
        r = xtdata.get_financial_data([CODE], ['Balance', 'Income'],
                                      '20240101', '20241231')
        print('   xtdata.get_financial_data -> %s' % type(r).__name__)
        print('   %s' % str(r)[:600])
    except Exception as e:
        print('   异常 %s: %s' % (type(e).__name__, str(e)[:120]))
    print('-- 试着触发下载（只打印结果，不阻塞）')
    try:
        import xtquant.xtdata as xtdata
        xtdata.download_financial_data([CODE], ['Balance', 'Income'])
        print('   download_financial_data 调用成功 —— 说明之前很可能就是没下载')
    except Exception as e:
        print('   异常 %s: %s' % (type(e).__name__, str(e)[:120]))


# ----------------------------------------------------------------- H2
def _h2(C):
    print('-- 换表名/写法，看有没有哪个能出值')
    cands = [
        # 常见大小写/下划线变体
        'ASHAREBALANCESHEET.tot_shrhldr_eqy_excl_min_int',
        'ASHAREBALANCESHEET.TOT_SHRHLDR_EQY_EXCL_MIN_INT',
        'Balance.tot_shrhldr_eqy_excl_min_int',
        'Balance.total_equity',
        'BALANCESHEET.tot_shrhldr_eqy_excl_min_int',
        # 利润表
        'ASHAREINCOME.net_profit_excl_min_int_inc',
        'Income.net_profit_excl_min_int_inc',
        'Income.net_profit_incl_min_int_inc',
        # 股本
        'CAPITALSTRUCTURE.total_capital',
        'CapitalStructure.total_capital',
        'ASHARECAPITALIZATION.tot_shr',
        # 每股指标
        'PERSHAREINDEX.s_fa_eps_basic',
        'PerShareIndex.s_fa_eps_basic',
    ]
    for f in cands:
        _one(C, f)


def _one(C, field):
    try:
        r = C.get_financial_data([field], [CODE], '20240101', '20241231')
    except Exception as e:
        print('   %-52s 异常 %s' % (field, str(e)[:50]))
        return
    print('   %-52s -> %s  %s' % (field, type(r).__name__, _peek(r)))


def _peek(r):
    """把返回里的【非 NaN 值】挑出来看，别只看结构。"""
    try:
        import pandas as pd
        if r is None:
            return 'None'
        if hasattr(r, 'values'):
            import numpy as np
            v = np.asarray(r.values, dtype='float64').ravel()
            ok = v[~np.isnan(v)]
            return ('非空 %d 个，样例 %s' % (len(ok), ok[:4])) if len(ok) else '全 NaN'
        if isinstance(r, dict):
            return 'dict 键 %s' % list(r)[:3]
        return str(r)[:80]
    except Exception as e:
        return '看不了(%s) %s' % (str(e)[:30], str(r)[:60])


# ----------------------------------------------------------------- H3
def _h3(C):
    F = 'ASHAREINCOME.net_profit_excl_min_int_inc'
    print('-- 换签名/ 换日期范围')
    tries = [
        ('单股票 + 一整年', lambda: C.get_financial_data([F], [CODE], '20240101', '20241231')),
        ('多股票 + 一整年', lambda: C.get_financial_data([F], CODES, '20240101', '20241231')),
        ('三年跨度', lambda: C.get_financial_data([F], [CODE], '20220101', '20241231')),
        ('带 report_type=report', lambda: C.get_financial_data(
            [F], [CODE], '20240101', '20241231', report_type='report')),
        ('带 report_type=announce', lambda: C.get_financial_data(
            [F], [CODE], '20240101', '20241231', report_type='announce')),
        ('日期带横杠', lambda: C.get_financial_data([F], [CODE], '2024-01-01', '2024-12-31')),
        ('不传日期', lambda: C.get_financial_data([F], [CODE])),
    ]
    for name, fn in tries:
        try:
            r = fn()
            print('   %-22s -> %-10s %s' % (name, type(r).__name__, _peek(r)))
        except Exception as e:
            print('   %-22s -> %s: %s' % (name, type(e).__name__, str(e)[:60]))


# ----------------------------------------------------------------- H4
def _h4(C):
    print('-- 上一轮只打了 Panel 的结构，这次把值挖出来')
    flds = ['ASHAREBALANCESHEET.tot_shrhldr_eqy_excl_min_int',
            'ASHAREINCOME.net_profit_excl_min_int_inc',
            'CAPITALSTRUCTURE.total_capital',
            'CAPITALSTRUCTURE.circulating_capital']
    try:
        r = C.get_financial_data(flds, CODES, '20240101', '20241231')
    except Exception as e:
        print('   异常 %s' % str(e)[:90])
        return
    print('   类型 %s' % type(r).__name__)
    try:
        print('   %s' % _peek(r))
        # Panel: items=股票, major=日期, minor=字段
        for attr in ('items', 'major_axis', 'minor_axis'):
            if hasattr(r, attr):
                ax = list(getattr(r, attr))
                print('   %-11s %d 个: %s ... %s' % (attr, len(ax), ax[:3], ax[-2:]))
        if hasattr(r, 'items'):
            it = list(r.items)[0]
            df = r[it]
            print('   r[%s] 尾 3 行:' % it)
            print('%s' % str(df.tail(3)))
    except Exception as e:
        print('   挖值失败 %s' % str(e)[:80])


# ----------------------------------------------------------------- H5
def _h5(C):
    print('-- 万一财务数据真的没有：能不能只靠行情 + 合约详情跑')
    print('   已知可用：get_instrumentdetail 有 FloatVolume（流通股本）')
    try:
        d = C.get_instrumentdetail(CODE)
        fv = d.get('FloatVolume') or d.get('FloatVolumn')
        tv = d.get('TotalVolume') or d.get('TotalVolumn') or d.get('LastVolume')
        print('   %s FloatVolume=%s  Total?=%s' % (CODE, fv, tv))
        print('   全部键: %s' % sorted(d))
    except Exception as e:
        print('   异常 %s' % str(e)[:80])
    print('   >> 结论方向：')
    print('      流通股本有  -> sgmspeg_v0b 只差一个「累计 EPS>0」，')
    print('                     若 EPS 也取不到，可退而用「近一年有正的净利润」之类近似，')
    print('                     但那是【另一条策略】，不能再声称与本地一致。')
    print('      PB / 单季ROE -> 没有财务数据就【无法】实现，froec 三条只能等数据')
