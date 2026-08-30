#coding:gbk
#==============================================================================
# 探针：核对 QMT 的分红接口 —— 红利指数增强能不能整条移植，就卡在这一项
#==============================================================================
# 跑法：QMT -> 策略 -> 新建 Python 策略 -> 粘贴 -> 周期 1d
#       区间随便选两天（本探针只在第一根 bar 跑一次），看日志。
#
# 为什么要探针而不是直接写：
#   红利需要的六类数据里，五类 QMT 都有或可算（总市值、PE、扣非ROE、
#   营收/净利同比、beta 对沪深300 —— beta 是纯价格回归，不需要外部数据）。
#   只有【每股分红 + 除权日】这一项，接口名与字段语义我没核对过。
#   凭印象写出来的最可能结果是「看着能跑、数字悄悄是错的」，所以先探。
#
# [已确认 2026-08-30] C.get_divid_factors(code) 可用，只接受 1~2 个参数。
#   返回 {毫秒时间戳: [7个数]}；key = 除权日【北京时间 00:00】
#   （用 utcfromtimestamp 会差一天）；[0] = 每股税前现金分红（元/股），
#   与本地 bonus_ratio_rmb/10 逐条吻合 11/11；[3][4] = 配股比例/配股价；[6] = 复权因子。
#
# 下面的 EXPECT 是本地 std/dividend.parquet 的实际值（每 10 股派息，税前 RMB），
# 探针会拿 QMT 返回的值去对，对上了才算这条路通。
#==============================================================================
import datetime as dt

# code -> [(除权日 YYYYMMDD, 每10股派息元)]
EXPECT = {
    '601398.SH': [('20240716', 3.064), ('20250107', 1.434),
                  ('20250714', 1.646), ('20251215', 1.414)],
    '601088.SH': [('20240708', 22.600), ('20250707', 22.600), ('20251110', 9.800)],
    '600028.SH': [('20240716', 2.000), ('20240913', 1.460),
                  ('20250618', 1.400), ('20250912', 0.880)],
    '601857.SH': [('20240626', 2.300), ('20240919', 2.200),
                  ('20250625', 2.500), ('20250917', 2.200)],
    '000651.SZ': [('20240828', 23.800), ('20250515', 10.000), ('20250829', 20.000)],
}


def init(C):
    g_done[0] = False
    print('[探针] 分红接口核对，等第一根 bar')


g_done = [False]


def handlebar(C):
    if g_done[0]:
        return
    g_done[0] = True
    print('=' * 78)
    print('[探针] 逐个试候选接口，谁能返回数据就用谁')
    print('=' * 78)

    codes = list(EXPECT)
    # ---- 候选 1：ContextInfo.get_divid_factors ----
    _try('C.get_divid_factors(code)',
         lambda c: C.get_divid_factors(c), codes)
    # ---- 候选 2：ContextInfo.get_divid_factors(code, start, end) ----
    _try("C.get_divid_factors(code,'20240101','20251231')",
         lambda c: C.get_divid_factors(c, '20240101', '20251231'), codes)
    # ---- 候选 3：模块级 get_divid_factors ----
    _try('get_divid_factors(code)',
         lambda c: get_divid_factors(c), codes)          # noqa: F821
    # ---- 候选 4：财务表里的分红字段 ----
    for fld in ('ASHAREDIVIDEND.cash_dvd_per_sh_pre_tax',
                'ASHAREDIVIDEND.cash_dvd_per_sh_after_tax',
                'PERSHAREINDEX.dividend_per_share'):
        _try_fin(C, fld, codes)
    print('=' * 78)
    print('[探针] 把上面整段贴回。重点看：')
    print('  1) 哪个候选返回了非空数据')
    print('  2) 返回结构里除权日和每股/每10股派息分别是哪个键')
    print('  3) 金额是【每股】还是【每10股】、【税前】还是【税后】')
    print('     —— EXPECT 里的数字是【每10股、税前】，对不上就是口径不同')


def _try(name, fn, codes):
    print('-' * 78)
    print('[候选] %s' % name)
    ok = 0
    for c in codes[:2]:
        try:
            r = fn(c)
        except Exception as e:
            print('   %s -> 异常 %s: %s' % (c, type(e).__name__, str(e)[:70]))
            continue
        if not r:
            print('   %s -> 空' % c)
            continue
        ok += 1
        print('   %s -> 类型 %s，长度 %s' % (c, type(r).__name__, _len(r)))
        print('      原样: %s' % str(r)[:400])
    if ok:
        print('   >>> 这个候选有数据，对照 EXPECT[%s] = %s' % (codes[0], EXPECT[codes[0]]))


def _try_fin(C, field, codes):
    print('-' * 78)
    print('[候选] get_financial_data([%s])' % field)
    try:
        r = C.get_financial_data([field], codes[:2], '20240101', '20251231')
    except Exception as e:
        print('   异常 %s: %s' % (type(e).__name__, str(e)[:90]))
        return
    # [!] 不要用 `not r` —— r 可能是 pandas Panel/DataFrame，会抛
    #     "The truth value of ... is ambiguous"，把整个探针打断（实测就是这样）。
    if r is None:
        print('   空')
        return
    print('   类型 %s' % type(r).__name__)
    print('   原样: %s' % str(r)[:500])


def _len(r):
    try:
        return len(r)
    except Exception:
        return '?'
