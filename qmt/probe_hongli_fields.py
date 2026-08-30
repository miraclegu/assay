#coding:gbk
#==============================================================================
# 探针 v2：红利指数增强移植前的字段核对（v1 已确认分红，本轮收尾）
#==============================================================================
# 跑法：QMT -> 新建 Python 策略 -> 粘贴 -> 周期 1d -> 随便两天 -> 看日志
#
# v1 的结论（已确认，不再验）：
#   C.get_divid_factors(code) 可用，签名只接受 1~2 个参数（传 start/end 会 TypeError）
#   返回 {毫秒时间戳: [7个数]}
#     key  = 除权日【北京时间 00:00】的毫秒时间戳
#            [!] 用 utcfromtimestamp 会得到前一天 16:00，必须按 UTC+8 转
#     [0]  = 每股税前现金分红（元/股）—— 与本地 std/dividend.parquet 的
#            bonus_ratio_rmb/10 逐条吻合（11/11 精确）
#     [3][4] = 配股比例 / 配股价（如 601398 的 2010-11-23: 0.045 股 @ 2.99 元）
#     [6]  = 复权因子
#
# 本轮要问的三件事：
#   A) 扣非净利润有没有 —— 红利的 B 袖用 inc_return(扣非ROE) 做过滤，
#      口径是【扣非净利 / 净资产】。没有扣非就只能退回普通净利，那是另一个口径。
#   B) get_financial_data 的返回结构到底是什么（v1 探针在这里崩了，
#      报 "The truth value of a Panel is ambiguous" —— 那是探针自己的 bug，
#      对 Panel 用了 `not r`。这轮改成先看类型再取值。）
#   C) 营收/净利同比要不要自己算 —— 若 QMT 只给绝对值，就得取相邻年度自己算。
#==============================================================================
import datetime as dt

# 本地 std/fin_indicator_q.parquet 的实际值，用来判 QMT 返回的口径对不对
# code -> report_date -> (扣非ROE, 营收同比, 净利同比, 扣非净利/亿元)
EXPECT = {
    '601398.SH': {'20241231': (0.0246, 0.0187, 0.0135, 966.60),
                  '20231231': (0.0255, -0.0716, 0.0070, 945.38)},
    '601088.SH': {'20241231': (0.0338, -0.0677, 0.1322, 140.91),
                  '20231231': (0.0374, -0.0365, 0.0616, 150.57)},
    '600028.SH': {'20241231': (0.0050, -0.0461, -0.1861, 40.90),
                  '20231231': (0.0130, -0.1417, -0.3546, 104.13)},
}

# 候选字段：不同 QMT 版本命名不同，全试一遍
CAND = [
    # 扣非净利（A）
    'ASHAREINCOME.net_profit_excl_min_int_inc',        # 归母净利（对照基准，froec 已在用）
    'ASHAREFINANCIALINDICATOR.net_profit_after_ded_nr_lp',
    'ASHAREFINANCIALINDICATOR.deducted_profit',
    'ASHAREINCOME.net_profit_after_ded_nr_lp',
    'PERSHAREINDEX.net_profit_after_ded_nr_lp',
    # 增长率（C）
    'ASHAREFINANCIALINDICATOR.yoyop',
    'ASHAREFINANCIALINDICATOR.yoy_or',
    'ASHAREFINANCIALINDICATOR.yoynetprofit',
    # 净资产（算 ROE 的分母，froec 已在用）
    'ASHAREBALANCESHEET.tot_shrhldr_eqy_excl_min_int',
]

_done = [False]


def init(C):
    print('[探针v2] 红利字段核对，等第一根 bar')


def handlebar(C):
    if _done[0]:
        return
    _done[0] = True
    codes = list(EXPECT)
    print('=' * 80)
    print('[探针v2] 逐个字段试 get_financial_data，看谁返回数据、结构是什么')
    print('=' * 80)
    for f in CAND:
        _probe(C, f, codes)
    print('=' * 80)
    print('[探针v2] 对照下面这张表判断口径：')
    for c, d in EXPECT.items():
        for rd, v in sorted(d.items(), reverse=True):
            print('   %s %s  扣非ROE=%.4f  营收同比=%+.4f  净利同比=%+.4f  扣非净利=%.2f亿'
                  % (c, rd, v[0], v[1], v[2], v[3]))
    print('   [!] 扣非ROE 的分母是【归母净资产】，值是小数不是百分数')
    print('[探针v2] 把整段贴回')


def _probe(C, field, codes):
    print('-' * 80)
    print('[字段] %s' % field)
    try:
        r = C.get_financial_data([field], codes, '20221231', '20241231')
    except Exception as e:
        print('   异常 %s: %s' % (type(e).__name__, str(e)[:100]))
        return
    # [!] 不要用 `not r` —— r 可能是 pandas Panel/DataFrame，会抛
    #     "The truth value of ... is ambiguous"。v1 探针就是这么崩的。
    print('   返回类型: %s' % type(r).__name__)
    try:
        if isinstance(r, dict):
            print('   dict 键: %s' % list(r)[:5])
            for k in list(r)[:1]:
                v = r[k]
                print('   %s -> %s' % (k, type(v).__name__))
                print('      %s' % str(v)[:400])
        else:
            print('   %s' % str(r)[:600])
    except Exception as e:
        print('   打印失败: %s' % e)
