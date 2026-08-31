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
    ('[?] C.get_sector_list 不存在（但这不等于「没有办法」）', '2026-08-30',
     "报 '_PyContext' object has no attribute。★ 当时据此断言「板块清单只能走 "
     'xtdata」是【推理过头】—— 正确做法是 dir(C) 把方法列全再说，见 OPEN 的 E1。'),
    ('xtquant 可 import，六个函数都在', '2026-08-30',
     'get_sector_list / get_stock_list_in_sector / download_sector_data / '
     'get_financial_data / download_financial_data / get_instrument_detail 全部存在。'),
    ('[环境] 用的是完整版 QMT 交易端，不是 miniQMT', '2026-08-31',
     '所以【策略移植一律走 ContextInfo】—— 行情/合约详情/分红/账户/沪深A股池 '
     '都已确认可用。xtdata 那套 connect(127.0.0.1:58610) 是 miniQMT/极简模式的形态，'
     '它只影响「批量导出数据给 datalake 用」，不影响策略能不能跑。'
     '所以下面 S 系列即使全挂，froec/v0b/红利 的移植也不受阻 —— '
     '真正卡住的只有 C.get_financial_data 返回 NaN 这一条。'),
    ('[!] xtdata 不是 miniQMT 专有，但策略用不上', '2026-08-31',
     '报错路径 D:\\国金证券QMT交易端\\bin.x64\\lib\\site-packages\\xtquant\\xtdata.py '
     '就在【完整版 QMT】目录里 —— xtquant 是随 QMT 一起装的，import 也成功，'
     '六个函数都在。报的是【连接错误不是导入错误】：xtdata 是独立客户端，'
     '要连 QMT 数据服务端口(58610)，完整版里该接口默认不一定开。'
     '（且调用前必须先 xtdata.connect(ip, port=58610)，直接调会抛 get_client 异常。）'
     '★ 策略移植只走 ContextInfo，用不到它；xtdata 只在「批量导出数据给 datalake」'
     '时才需要 —— 那是另一件事，本探针已不再测它。'),
    ('[**] 财务数据补完后可用 —— 四个字段全有值', '2026-08-31',
     'ASHAREBALANCESHEET.tot_shrhldr_eqy_excl_min_int / '
     'ASHAREINCOME.net_profit_excl_min_int_inc / CAPITALSTRUCTURE.total_capital / '
     'CAPITALSTRUCTURE.circulating_capital 全部非空。'
     'PERSHAREINDEX.s_fa_eps_basic 也有值(601398=0.98)，v0b 的累计 EPS 有着落。'
     '表名必须【全大写】：Balance. / Income. / CapitalStructure. / BALANCESHEET. 全 NaN。'
     '扣非净利两个候选(ASHAREFINANCIALINDICATOR.net_profit_after_ded_nr_lp / '
     'deducted_profit) 全 NaN —— ★ 只说明这两个名字不对，不说明没有。'),
    ('财务调用签名', '2026-08-31',
     '必须传 start/end 且格式 YYYYMMDD（带横杠全 NaN、不传报 TypeError）。'
     'report_type 参数不支持（传了返回 None）。多股票多字段返回 pandas Panel：'
     'items=股票 / major_axis=【交易日】/ minor_axis=字段。'),
    ('[!!] QMT 财务【按报告期前向填充】= 未来函数', '2026-08-31',
     '实测 601398：20241225~1230 净利 2690.3 亿(2024Q3)，20241231 变成 3658.6 亿'
     '(2024年报) —— 而 2024 年报公告日是 2025-03-29，提前 88 天。'
     '数值本身与本地精确吻合，错的是【时间对齐】：按报告期切换、不按公告日。'
     '直接按 ref_date 取值就是前视。移植已修：_attach_report_cols 重写为'
     '「按季末识别报告期 + 值跳变才算真发布 + 法定截止日兜底」。'),
    ('ST 板块名 = 沪深风险警示', '2026-08-31',
     "十个候选里只有 '沪深风险警示' 命中，206 只（本地 2025-06-30 是 175 只，"
     '探针取最新时点，量级对）。ST板块 / ST / 风险警示 / *ST / ST股票 / 沪深ST / '
     '两市ST / ST及*ST 全部 0 只。'),
    ('[!] 行业板块不是申万命名', '2026-08-31',
     '银行 / SW银行 / 申万银行 / 申万一级-银行 / 行业-银行 / 银行I / 银行(申万) / '
     '证监会行业-金融业 / 金融业 —— 全部 0 只（★ 这只说明这几个名字不对，'
     '不说明取不到行业；get_instrumentdetail 里就有 ProductID/ProductName 没查过）。'
     '但 QMT 界面「热门板块」里是：'
     '农产品加工 / 酒店及餐饮 / 物流 / 光学光电子 / 造纸 / 化工新材料 / 石油矿业开 / '
     '建筑材料 / 环保工程 / 农业服务 / 机场航运 / 视听器材 / 通信设备 / 交运设备服 '
     '—— 通达信风格，不是申万一级。froec 的 11 个行业黑名单要做映射，见 S6。'),
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
    print('>>>>>> 探针已加载 <<<<<<  参考日 %s' % REF_DATE)
    print('完整版 QMT：本探针只走 ContextInfo（策略就是这么跑的），共 5 节。')
    print('所有接口调用（含 ContextInfo）均已套 %d 秒超时，不会静默卡死。'
          % CALL_TIMEOUT)
    print('[!] QMT 正在补充数据时不要跑本探针 —— 即使不卡，探到的也是')
    print('    半完成状态的数据，一部分有值一部分 NaN，会误判。等补完再跑。')


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


CALL_TIMEOUT = 6                  # 单次接口调用上限（秒），ContextInfo 也套
STALL_MAX = 4                     # 连续超时几次后熔断
_STALL = [0]


def _call_timeout(fn, sec=None):
    """给可能阻塞的调用套超时。

    [!] QMT 的 print 是【缓冲】的：策略线程一旦阻塞，之前打印的内容也刷不出来
        —— 表现就是「点回测什么日志都没有」。实测被 xtdata.connect 卡过一次。

    [!] ContextInfo 的调用【同样要套】：QMT 客户端在补充数据时，
        C.get_financial_data / C.get_stock_list_in_sector 都可能阻塞等数据。
        一开始只套了 xtdata 是漏的 —— 而 F2 恰好排第一节，一卡又是全无输出。
    """
    import threading
    sec = CALL_TIMEOUT if sec is None else sec
    # ★ 熔断：连续超时 STALL_MAX 次后直接放弃，不再逐个等。
    #   没有熔断时，「QMT 正在补数据」这种场景下 44 个调用点各等 8 秒 = 352 秒，
    #   人会以为又卡死了。
    if _STALL[0] >= STALL_MAX:
        raise RuntimeError('接口持续阻塞，已熔断（QMT 是不是正在补充数据？'
                           '补完再跑本探针）')
    box = {}

    def _run():
        try:
            box['r'] = fn()
        except Exception as e:                      # noqa: BLE001
            box['e'] = e

    t = threading.Thread(target=_run)
    t.daemon = True
    t.start()
    t.join(sec)
    if t.is_alive():
        _STALL[0] += 1
        raise RuntimeError('超时 %ds（第 %d 次；连续 %d 次就熔断）'
                           % (sec, _STALL[0], STALL_MAX))
    _STALL[0] = 0                      # 有一次成功就复位
    if 'e' in box:
        raise box['e']
    return box.get('r')



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





def e1_dir(C):
    """把 ContextInfo 的方法全列出来 —— 不再猜名字。

    [!] 这一节本该最先做。前几轮我一直在猜 get_sector_list / get_industry /
        get_instrumentdetail 之类的名字，猜不中就下结论说「没有」——
        而成熟系统不可能没有。列一遍就知道了。
    """
    names = [x for x in dir(C) if not x.startswith('__')]
    print('   ContextInfo 共 %d 个可见成员' % len(names))
    kw = ('sector', 'industry', 'financial', 'fin', 'instrument', 'detail',
          'divid', 'report', 'ann', 'stock', 'list', 'market', 'trade')
    for k in kw:
        hit = [n for n in names if k in n.lower()]
        if hit:
            print('   含 %-11s : %s' % (k, hit))
    print('   -- 全量（每行 5 个）--')
    for i in range(0, len(names), 5):
        print('      %s' % names[i:i + 5])


def e2_detail_full(C):
    """get_instrumentdetail 的【全部】键值 —— 行业信息很可能就在里面。

    [!] 上一轮我只打了 sorted(d)[:24] 就截断了，而截图里已经能看到
        ProductID / ProductName 这种像是分类的键，后面还有没显示的。
    """
    for code in ('601398.SH', '601088.SH', '300750.SZ'):
        try:
            d = _call_timeout(lambda: C.get_instrumentdetail(code))
        except Exception as e:
            print('   %s 异常 %s' % (code, str(e)[:60]))
            continue
        if not isinstance(d, dict):
            print('   %s 返回 %s: %s' % (code, type(d).__name__, str(d)[:120]))
            continue
        print('   -- %s（%d 个键，全量）--' % (code, len(d)))
        for k in sorted(d):
            v = d[k]
            sv = str(v)
            if len(sv) > 60:
                sv = sv[:60] + '...'
            print('      %-22s = %s' % (k, sv))
        break                       # 一只票就够看键名，其余只看行业相关
    print('   -- 另两只只看疑似行业/分类的键 --')
    for code in ('601088.SH', '300750.SZ'):
        try:
            d = _call_timeout(lambda: C.get_instrumentdetail(code)) or {}
            sub = dict((k, d[k]) for k in d
                       if any(t in k.lower() for t in
                              ('product', 'industry', 'sector', 'type', 'class')))
            print('      %s %s' % (code, sub))
        except Exception as e:
            print('      %s 异常 %s' % (code, str(e)[:50]))
    print('   本地行业参考: 601398=银行 601088=煤炭 300750=电气设备')


def e3_anndate(C):
    """公告日：显式把它当【字段】去请求。

    [!] 上一轮我只请求了 4 个业务字段，当然只返回 4 列 —— 就此断言
        「拿不到公告日」是错的推理。这里逐个试公告日字段名。
    """
    base = 'ASHAREINCOME.net_profit_excl_min_int_inc'
    for f in ('ASHAREINCOME.m_anntime', 'ASHAREINCOME.m_timetag',
              'ASHAREINCOME.ann_dt', 'ASHAREINCOME.announce_date',
              'ASHAREINCOME.anndate', 'ASHAREINCOME.report_date',
              'ASHAREBALANCESHEET.m_anntime', 'ASHAREINCOME.first_ann_dt'):
        try:
            r = _call_timeout(lambda: C.get_financial_data(
                [f], [CODE], '20240101', '20241231'))
            print('   %-46s -> %-10s %s' % (f, type(r).__name__, _peek(r)))
        except Exception as e:
            print('   %-46s 异常 %s' % (f, str(e)[:45]))
    print('   -- 与业务字段一起取，看返回里会不会多出公告日列 --')
    try:
        r = _call_timeout(lambda: C.get_financial_data(
            [base, 'ASHAREINCOME.m_anntime'], [CODE], '20240101', '20241231'))
        print('   两字段一起 -> %s' % type(r).__name__)
        if hasattr(r, 'minor_axis'):
            print('      minor_axis: %s' % list(r.minor_axis))
        elif hasattr(r, 'columns'):
            print('      columns: %s' % list(r.columns))
        print('      %s' % str(r)[:300])
    except Exception as e:
        print('   异常 %s: %s' % (type(e).__name__, str(e)[:70]))
    print('   -- report_type 再试（上轮传 report/announce 都返回 None）--')
    for rt in ('announce', 'report', 1, 0, '1', '0'):
        try:
            r = _call_timeout(lambda: C.get_financial_data(
                [base], [CODE], '20240101', '20241231', rt))
            print('   第5个位置参数 %-10r -> %-10s %s'
                  % (rt, type(r).__name__, _peek(r)))
        except Exception as e:
            print('   第5个位置参数 %-10r -> %s: %s' % (rt, type(e).__name__, str(e)[:50]))


def e4_sector_probe(C):
    """板块：先看 E1 列出的方法里有没有能【枚举板块】的，再谈映射。"""
    print('   ST 已定案 = 沪深风险警示。本节只解决行业。')
    print('   -- 先看有没有能列板块的方法（E1 已列全，这里挑名字像的试）--')
    for fn in ('get_sector_list', 'get_industry', 'get_stock_industry',
               'get_sector', 'sector_list', 'get_stock_type'):
        f = getattr(C, fn, None)
        if f is None:
            print('   C.%-22s 无' % fn)
            continue
        for args in ((), ('601398.SH',)):
            try:
                r = _call_timeout(lambda: f(*args))
                print('   C.%s(%s) -> %s  %s'
                      % (fn, ','.join(map(repr, args)), type(r).__name__, str(r)[:200]))
                break
            except Exception as e:
                print('   C.%s(%s) -> %s' % (fn, ','.join(map(repr, args)), str(e)[:60]))
    print('   -- 通达信风格行业名反查（界面「热门板块」里看到的）--')
    for name in ('银行', '证券', '保险', '煤炭开采', '石油矿业开', '钢铁行业',
                 '水泥', '公路桥梁', '铁路', '航空', '物流', '环保工程',
                 '交运设备服', '通信设备', '造纸', '建筑材料'):
        _sec(C, name, ['601398.SH', '601088.SH', '600028.SH'])


def _sec(C, name, expect):
    try:
        lst = _call_timeout(lambda: C.get_stock_list_in_sector(name)) or []
    except Exception as e:
        print('   %-22s 异常 %s' % (name, str(e)[:45]))
        return
    hit = [c for c in lst if c in expect]
    flag = '  <== 命中!' if (lst and hit) else ('  (有票但没命中参考)' if lst else '')
    print('   %-22s -> %5d 只%s' % (name, len(lst), flag))


# ================================ 待确认 F：财务 ==============================

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
            r = _call_timeout(lambda: C.get_financial_data(
                [f], [CODE], '20240101', '20241231'))
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
            r = _call_timeout(fn)
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
        r = _call_timeout(lambda: C.get_financial_data(
            flds, CODES, '20240101', '20241231'))
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


# [!] 顺序有讲究：【不碰网络】的先跑。
# 本探针只走 ContextInfo —— 完整版 QMT 里策略就是这么跑的。
# xtdata 那套（connect 127.0.0.1:58610）已从 OPEN 移除，理由见 CONFIRMED
# 里「xtdata 不是 miniQMT 专有，但策略用不上」那条。
OPEN = [
    ('E1 dir(C) 全量方法清单  ★ 先枚举再谈有没有', e1_dir),
    ('E2 get_instrumentdetail 全量键值（找行业）', e2_detail_full),
    ('E3 公告日（显式当字段请求 + report_type 再试）', e3_anndate),
    ('E4 行业板块', e4_sector_probe),
    ('F4 财务 Panel 结构复核', f4_panel_values),
    ('F5 退路（本地判断，不调接口）', f5_fallback),
]
