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
    ('[定案] dir(C) = 115 个成员，方法清单已列全', '2026-08-31',
     'get_sector_list 确实不存在，但同族有：create_sector / get_sector / '
     'get_stock_list_in_sector / get_industry / get_raw_financial_data / '
     'get_financial_data / get_finance / get_his_st_data / get_divid_factors / '
     'get_instrumentdetail / get_float_caps / get_total_share / get_turn_over_rate / '
     'get_weight_in_index / get_top10_share_holder / get_holder_num / get_factor_data / '
     'get_smallcap / get_midcap / get_largecap / is_suspended_stock / get_trading_dates。'
     '★ 教训：先 dir 再谈有没有。前几轮「取不到」全是猜错名字。'),
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
     '多股票多字段返回 pandas Panel：items=股票 / major_axis=【交易日】/ minor_axis=字段。'
     '★ report_type 是【第 5 个位置参数且必须是整数】：传 1 或 0 都返回数据，'
     "传 'announce'/'report'/'1'/'0' 一律返回 None（静默，不报错）—— "
     '硬写字符串形态的风险是财务整片为空而策略不报错。移植里 _call_fin 已改成'
     '降级阶梯并缓存首个可用形态。'),
    ('[**] 公告日 m_anntime / 报告期 m_timetag —— 一直都有', '2026-08-31',
     '★ 前几轮说「公告日拿不到」是错的：我只请求了 4 个业务字段，当然只返回 4 列。'
     '把它当【字段】显式请求就有：ASHAREINCOME.m_anntime（也存在于 ASHAREBALANCESHEET）'
     '和 ASHAREINCOME.m_timetag，都是【毫秒时间戳】。实测 601398 与本地逐字段吻合：'
     '  m_timetag=1.703952e12 -> 2023-12-31 = 报告期（本地 report_date 同）；'
     '  m_anntime=1.7115552e12 -> 2024-03-28 = 公告日（本地 pub_date 同）。'
     '且可与业务字段同批请求，返回 columns=[net_profit_excl_min_int_inc, m_anntime]。'
     '而 ann_dt / announce_date / anndate / report_date / first_ann_dt 全 NaN（名字不对）。'
     '-> 移植已改用真实公告日，删掉「法定披露截止日」兜底（原本会晚 0~32 天）。'),
    ('[定案] get_instrumentdetail 30 个键里【没有行业】', '2026-08-31',
     '全量键已打：CreateDate/DownStopPrice/ExchangeCode/ExchangeID/ExpireDate/'
     'FloatVolume/FloatVolumn/HSGTFlag/InstrumentID/InstrumentName/InstrumentStatus/'
     'IsRecent/IsTrading/LastVolume/LongMarginRatio/MainContract/OpenDate/PreClose/'
     'PriceTick/ProductID/ProductName/RzrkCode/SettlementPrice/ShortMarginRatio/'
     'TotalVolume/TotalVolumn/TradingDay/UniCode/UpStopPrice/VolumeMultiple。'
     'ProductID 和 ProductName 对 601398/601088/300750 【全是空字符串】-> 行业只能另找。'
     '★ 但捡到一个：TotalVolume=总股本、FloatVolume=流通股本，不查财务表也能算市值。'),
    ('[?] get_industry / get_sector 存在，之前【参数传反了】', '2026-08-31',
     "两者都报 missing 1 required positional argument（'indu…' / 'sector'），"
     '说明要的是【板块/行业名】不是股票代码 —— 我传了 601398.SH 所以返回空 list []。'
     "get_stock_type('601398.SH') -> int 0。"
     '通达信中文行业名走 get_stock_list_in_sector 基本不通（16 个名字里只有'
     '「水泥」返回 31 只且没命中参考票，其余全 0）。★ 正确参数见 OPEN 的 G1。'),
    ('[定案] report_type 的真正含义 —— 未来函数的根因', '2026-08-31',
     '官方文档（迅投知识库 innerApi/data_function）：'
     "report_type='announce_time' 按【公告期】取数（发布日之后到下个财报发布日之间"
     "给的都是该期财报的值，这是默认值、时点正确）；'report_time' 按【报告期】取数。"
     '★ 移植原来硬写的正是 report_type=\'report_time\' —— 主动要了未来函数那一版，'
     '这解释了 601398 在 20241231 就给出 2025-03-29 才公告的年报。'
     "已改为 'announce_time' 优先的降级阶梯。注意传不认识的字符串是【静默返回 None】。"),
    ('[定案] 板块名 = 前缀 + 行业名（不加分隔符）', '2026-08-31',
     "官方与社区一致：如 'SW1汽车' / 'CSRC1采矿业'。前缀族："
     'SW1/SW2=申万一二级，CSRC1/CSRC2=证监会，THY1/THY2=通达信行业，'
     'TGN/GN=概念，DY1=地域。★ 上一轮试的是光秃秃的「银行」，当然返回 0 —— '
     '不是取不到，是名字少了前缀。'),
    ('[定案] get_raw_financial_data 可用，且【不做日度插值】', '2026-08-31',
     "C.get_raw_financial_data([field], codes, s, e) -> {code: dict}，实测两只票都有值。"
     '官方说明：与 get_financial_data 同参数，但只返回原始报告期行、不按交易日插值 —— '
     '这正是我们要的形态。★ 上一轮只打了 type 没打内层键，见 OPEN 的 H2。'),
    ('[定案] 这几个 C 方法直接可用（免去查财务表）', '2026-08-31',
     "get_total_share('601398.SH') -> int 356406257089（= detail 的 TotalVolume）；"
     "get_float_caps('601398.SH') -> int 269612212539（= FloatVolume）；"
     "is_suspended_stock('601398.SH') -> bool False；"
     "get_weight_in_index('000300.SH','601398.SH') -> float 0.992（沪深300 权重%）。"
     "get_turn_over_rate('601398.SH') -> nan（要么要传日期，要么要先下数据）。"),
    ('[定案] get_his_st_data 接【单个字符串】不接 list', '2026-08-31',
     "C.get_his_st_data('601398.SH') -> dict（工行从没 ST 所以是空 dict，不是不可用）；"
     "传 list 报错泄露了内部名 _PyContextInfo.get_st_status(list)；传 (code, s, e) 报"
     "'takes 2 positional arguments but 4 were given' -> 只接 1 个参数。"
     '★ 需要拿一只真 ST 股验证返回结构，见 OPEN 的 H5。'),
    ('[定案] 扣非净利 8 个候选全 NaN；但 EPS 有着落', '2026-08-31',
     'ASHAREFINANCIALINDICATOR 的 s_fa_deductedprofit / deductedprofit / '
     's_fa_roe_deducted / np_cut、ASHAREINCOME 的 np_cut / '
     'net_profit_excl_min_int_inc_ded_nr、PERSHAREINDEX.s_fa_epsdeducted 全 NaN。'
     '★ 但 PERSHAREINDEX.s_fa_eps_diluted -> 0.98 有值（601398 2024）。'
     '停止逐个猜名字 —— 用 get_raw_financial_data 把整表字段清单拉出来找，见 H2。'),
    ('[定案] 两个方法的缺参已知', '2026-08-31',
     "get_factor_data() 报 missing 3 required positional arguments: 'stock_list…' -> "
     '至少 (stock_list, ?, ?)；'
     "get_trading_dates('SH','20240101','20240131') 报 missing 1 required "
     "positional argument: 'count' -> 第 4 个参数是 count。"),
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


# ============================== 待确认 H：下一轮 ==============================
#
# ★ 方法变了：这一轮不再穷举猜参数 —— 先查官方文档拿到用法，探针只负责【验证】。
#   G 轮花了一整轮试 18 个行业名全 0，而文档一句话就说清了「板块名 = 前缀+行业名」。
#   凡是能查到文档的，不要用探针去发现。


def _doc(C, fn):
    f = getattr(C, fn, None)
    if f is None:
        print('   C.%-24s 不存在' % fn)
        return None
    d = (getattr(f, '__doc__', None) or '').strip()
    print('   C.%-24s %s' % (fn, ('__doc__: ' + d.splitlines()[0][:70]) if d else '存在，无 __doc__'))
    return f


def _try(C, label, call):
    try:
        r = _call_timeout(call)
    except Exception as e:
        print('   %-46s -> %s' % (label, str(e)[:70]))
        return None
    t = type(r).__name__
    if isinstance(r, (list, tuple)):
        print('   %-46s -> %s len=%d %s' % (label, t, len(r), str(list(r)[:6])[:100]))
    elif isinstance(r, dict):
        print('   %-46s -> dict len=%d keys=%s' % (label, len(r), str(list(r)[:8])[:95]))
    else:
        print('   %-46s -> %s %s' % (label, t, _peek(r)))
    return r


# 申万一级 31 个行业（2021 版）。文档说板块名 = 'SW1' + 行业名，本节就是验证这一条。
SW1 = ['农林牧渔', '基础化工', '钢铁', '有色金属', '电子', '家用电器', '食品饮料',
       '纺织服饰', '轻工制造', '医药生物', '公用事业', '交通运输', '房地产',
       '商贸零售', '社会服务', '综合', '建筑材料', '建筑装饰', '电力设备',
       '国防军工', '计算机', '传媒', '通信', '银行', '非银金融', '汽车',
       '机械设备', '煤炭', '石油石化', '环保', '美容护理']


def h3_announce_time(C):
    """★ 本轮最要紧的一节：直接验证 report_type 能不能消掉未来函数。

    已知事实（上一轮实测）：601398 用 report_type='report_time' 时
        20241225~20241230  净利 2690.3 亿（2024Q3，公告 2024-10-31）
        20241231           净利 3658.6 亿（2024 年报，公告 2025-03-29）  <- 提前 88 天
    若 'announce_time' 口径正确，20241231 那天【应该还是 2690.3 亿】，
    要到 2025-03-29 之后才变。这一节就看这一个数。
    """
    fld = ['ASHAREINCOME.net_profit_excl_min_int_inc']
    code = '601398.SH'
    for label, call in (
        ("report_type='announce_time' ★",
         lambda: C.get_financial_data(fld, [code], '20241220', '20250430',
                                      report_type='announce_time')),
        ('不传 report_type（文档称默认即 announce_time）',
         lambda: C.get_financial_data(fld, [code], '20241220', '20250430')),
        ("report_type='report_time'（已知的未来函数口径，做对照）",
         lambda: C.get_financial_data(fld, [code], '20241220', '20250430',
                                      report_type='report_time')),
    ):
        print('   -- %s --' % label)
        try:
            r = _call_timeout(call)
        except Exception as e:
            print('      异常 %s' % str(e)[:80])
            continue
        _show_np(r, code)
    print('   [判读] 20241231 那天：2690.3e8 = 时点正确；3658.6e8 = 仍是未来函数。')


def _show_np(r, code):
    """把返回里 601398 的净利序列按日期打出来，只挑关键几天。"""
    try:
        df = r
        if isinstance(r, dict):
            df = r.get(code) or list(r.values())[0]
        if hasattr(r, 'items') and hasattr(r, 'major_axis'):     # Panel
            df = r[code]
        if df is None:
            print('      返回里没有 %s' % code)
            return
        for d in ('20241226', '20241230', '20241231', '20250102',
                  '20250328', '20250331', '20250429'):
            v = None
            for idx in df.index:
                if str(idx).replace('-', '')[:8] == d:
                    row = df.loc[idx]
                    v = row.iloc[0] if hasattr(row, 'iloc') else row
                    break
            print('      %s  %s' % (d, ('%.4e' % float(v)) if v is not None else '（无此日）'))
    except Exception as e:
        print('      解析失败 %s' % str(e)[:70])


def h1_sector_name(C):
    """验证「板块名 = 前缀 + 行业名」。601398 应落在 SW1银行 / CSRC1金融业。"""
    print('   文档说：板块名 = 前缀 + 行业名，不加分隔符（如 SW1汽车 / CSRC1采矿业）')
    print('   -- 前缀族抽样，看哪个前缀通 --')
    for nm in ('SW1银行', 'SW2股份制银行', 'CSRC1金融业', 'THY1银行', 'TGN银行',
               'SW1汽车', 'CSRC1采矿业', 'SW1煤炭', 'SW1电力设备'):
        for fn in ('get_stock_list_in_sector', 'get_industry'):
            f = getattr(C, fn, None)
            if f is None:
                continue
            try:
                lst = _call_timeout(lambda f=f, nm=nm: f(nm)) or []
            except Exception as e:
                print('   %-24s %-24s -> %s' % (fn, nm, str(e)[:40]))
                continue
            hit = [c for c in lst if c in ('601398.SH', '601088.SH', '300750.SZ')]
            print('   %-24s %-16s -> %5d 只 %s'
                  % (fn, nm, len(lst), ('命中 %s' % hit) if hit else ''))
    print('   -- 若上面通了，就跑全部 31 个申万一级，建 code->行业 映射 --')
    f = getattr(C, 'get_stock_list_in_sector', None)
    if f is None:
        return
    total, ok = 0, 0
    for nm in SW1:
        try:
            lst = _call_timeout(lambda nm=nm: f('SW1' + nm)) or []
        except Exception:
            lst = []
        if lst:
            ok += 1
            total += len(lst)
    print('   SW1 全 31 个行业：%d 个有成分股，合计 %d 只（全市场约 5200 只可比对）'
          % (ok, total))


def h2_raw_fields(C):
    """get_raw_financial_data 的【内层结构】—— 上一轮只打了 type 没打键。"""
    f = getattr(C, 'get_raw_financial_data', None)
    if f is None:
        print('   不存在')
        return
    r = None
    try:
        r = _call_timeout(lambda: f(['ASHAREINCOME.net_profit_excl_min_int_inc'],
                                    ['601398.SH'], '20230101', '20241231'))
    except Exception as e:
        print('   异常 %s' % str(e)[:70])
    _dig(r, '601398.SH')
    print('   -- 试整表：只给表名不给字段，看能不能一次拿到全字段清单 --')
    for req in (['ASHAREINCOME'], 'ASHAREINCOME',
                ['ASHAREINCOME.*'], ['ASHAREFINANCIALINDICATOR']):
        try:
            r2 = _call_timeout(lambda req=req: f(req, ['601398.SH'],
                                                 '20230101', '20241231'))
        except Exception as e:
            print('   %-28s -> %s' % (str(req)[:26], str(e)[:60]))
            continue
        print('   %-28s ->' % str(req)[:26])
        _dig(r2, '601398.SH')


def _dig(r, code, depth=0):
    """把嵌套返回逐层拆开打印，目标是拿到【字段名清单】。"""
    pad = '      ' + '  ' * depth
    if r is None:
        print('%sNone' % pad)
        return
    if isinstance(r, dict):
        ks = list(r)
        print('%sdict len=%d keys=%s' % (pad, len(ks), str(ks[:12])[:110]))
        if depth < 2 and ks:
            k = code if code in r else ks[0]
            print('%s-> 展开 [%s]' % (pad, k))
            _dig(r[k], code, depth + 1)
        return
    cols = getattr(r, 'columns', None)
    if cols is not None:
        print('%s%s shape=%s' % (pad, type(r).__name__, getattr(r, 'shape', '?')))
        print('%s字段 %d 个: %s' % (pad, len(cols), list(cols)[:40]))
        try:
            print('%s尾 2 行:\n%s' % (pad, r.tail(2).to_string()[:600]))
        except Exception:
            pass
        return
    print('%s%s %s' % (pad, type(r).__name__, str(r)[:200]))


def h4_detail_complete(C):
    """get_instrument_detail 的 iscomplete 扩展字段 —— 文档提到有这个参数。"""
    for fn in ('get_instrumentdetail', 'get_instrument_detail'):
        f = getattr(C, fn, None)
        if f is None:
            continue
        base = None
        try:
            base = _call_timeout(lambda f=f: f('601398.SH')) or {}
        except Exception:
            base = {}
        for arg in (True, 1):
            try:
                r = _call_timeout(lambda f=f, arg=arg: f('601398.SH', arg))
            except Exception as e:
                print('   %s(code, %r) -> %s' % (fn, arg, str(e)[:70]))
                continue
            if not isinstance(r, dict):
                print('   %s(code, %r) -> %s' % (fn, arg, type(r).__name__))
                continue
            extra = [k for k in r if k not in base]
            print('   %s(code, %r) -> %d 键，比默认多 %d 个: %s'
                  % (fn, arg, len(r), len(extra), str(extra)[:200]))
            for k in extra:
                print('       %-24s = %s' % (k, str(r[k])[:60]))


def h5_st_and_rest(C):
    """真 ST 股验 get_his_st_data；补齐 get_factor_data / get_trading_dates 的参数。"""
    print('   -- get_his_st_data：换真 ST 股（工行从没 ST 所以上一轮是空 dict）--')
    for code in ('000005.SZ', '600870.SH', '000561.SZ', '601258.SH'):
        _try(C, "get_his_st_data(%r)" % code,
             lambda code=code: C.get_his_st_data(code))
    print('   -- get_trading_dates：第 4 个参数是 count --')
    for args in (('SH', '20240101', '20240131', 100),
                 ('SH', '20240101', '20240131', 0),
                 ('SH', '', '', 10)):
        _try(C, 'get_trading_dates%s' % (args,),
             lambda args=args: C.get_trading_dates(*args))
    print('   -- get_factor_data：缺 3 个位置参数，首个是 stock_list --')
    for args in ((['601398.SH'], '20240101', '20241231'),
                 (['601398.SH'], 'pe', '20240101'),
                 (['601398.SH'], ['pe'], '20240101', '20241231')):
        _try(C, 'get_factor_data(%s…)' % str(args[1])[:20],
             lambda args=args: C.get_factor_data(*args))
    print('   -- get_turn_over_rate：上一轮返回 nan，试带日期 --')
    for args in (('601398.SH', '20240102'), ('601398.SH', 20240102)):
        _try(C, 'get_turn_over_rate%s' % (args,),
             lambda args=args: C.get_turn_over_rate(*args))


# [!] 顺序有讲究：【不碰网络】的先跑。
# 本探针只走 ContextInfo —— 完整版 QMT 里策略就是这么跑的。
# xtdata 那套（connect 127.0.0.1:58610）已从 OPEN 移除，理由见 CONFIRMED
# 里「xtdata 不是 miniQMT 专有，但策略用不上」那条。
OPEN = [
    ("H3 ★★ report_type='announce_time' 能否消掉未来函数（就看 20241231 一个数）",
     h3_announce_time),
    ('H1 板块名 = 前缀+行业名（验证文档说法，顺带建 SW1 映射）', h1_sector_name),
    ('H2 get_raw_financial_data 内层结构 -> 整表字段清单（找扣非）', h2_raw_fields),
    ('H4 get_instrument_detail(code, iscomplete) 扩展字段', h4_detail_complete),
    ('H5 真 ST 股验 get_his_st_data + 补齐三个方法的参数', h5_st_and_rest),
]
