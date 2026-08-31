#coding:gbk
#==============================================================================
# ROEC 周度轮动策略（低 PB + 单季 ROE 改善 + 小流通市值）
# 聚宽(JoinQuant) -> QMT 内置 Python 回测  移植版
#
# 用法：
#   QMT 客户端 -> 策略 -> 新建「Python 策略」-> 粘贴本文件 -> 编译 -> 回测
#   回测周期：1d（推荐）。主图标的建议设为 000905.SH，保证 bar 与交易日对齐。
#   若要完整复现聚宽的「14:00 检查涨停是否打开」，把周期设为 1m（慢很多，本文件已支持）。
#
# 依赖的 QMT 接口（不同版本字段名可能有差异，见文件末尾「上线前必做的 3 项核对」）：
#   C.get_stock_list_in_sector / C.get_market_data_ex / C.get_instrumentdetail
#   C.get_financial_data / get_trade_detail_data / passorder
#==============================================================================

import datetime as dt
from decimal import Decimal, ROUND_HALF_UP

import numpy as np
import pandas as pd


#============================== 与本地策略的关联 ==============================
# [!] 这不是注释，是【可校验的声明】：python3 qmt/check.py 会拿它去核对
#     本地策略在不在、run_id 在不在归档、参数对不对得上、
#     基线指标与 stats.json 是否一致、以及本文件是否与模板逐字节同步。
#
# 本文件 = FROEC（聚宽原版口径 + 修掉 689 CDR 漏网）
# 本地   : strategies/小市值/froec.py
# 归档   : 20260828-205911-486094   年化 36.38% / 回撤 46.84% / 夏普 1.21
# 参数   : {'kcb_688_only': 0}
#
# [!] 本文件由 qmt/gen.py 从 qmt/_tpl/froec.py 生成，**不要手工改**。
#     要改逻辑改模板，要改参数改 gen.py 的 PROFILES，然后重跑 gen。
LOCAL_PORT = {
    'kind': 'port',
    'generated_from': '_tpl/froec.py',
    'profiles': {
        'froec': {
            'strategy': 'strategies/小市值/froec.py',
            'run_id': '20260828-205911-486094',
            'local': {'kcb_688_only': 0},
            'metrics': {'annual': 36.38, 'max_drawdown': 46.84, 'sharpe': 1.21},
            'qmt': {'TRADED_UNIVERSE': False, 'STOP_LOSS': 0.0, 'STOP_INTRADAY': True},
        },
    },
}

#============================== 参数区 ========================================
ACCOUNT      = ''          # 资金账号；留空则用平台注入的全局变量 account
ACCOUNT_TYPE = 'STOCK'

BENCHMARK    = '000905.SH' # 基准（同时用它的日线索引当交易日历）
POOL_SECTOR  = '沪深A股'   # 选股母池板块

STOCK_NUM      = 10        # 最大持仓数
CANDIDATE_NUM  = 10        # 选股池截断长度 —— froec 与 v0b 【唯一的流程差别】。
                           # 两者的后处理顺序完全相同：
                           #   候选 -> 停牌/涨跌停过滤 -> 20日涨停黑名单 -> 截到 STOCK_NUM
                           # froec=10：候选就只有 10，过滤完可能不足 10 且【不补位】
                           #           （聚宽原版 get_stock_list()[:10] 的行为）
                           # v0b=15  ：候选留 15 的余量，过滤完通常仍能凑满 10
LIMIT_DAYS     = 20        # 近 N 日买过且涨停过 -> 拉黑
PB_TOP_RATIO   = 0.5       # PB 由低到高取前 50%
ROE_TOP_RATIO  = 0.1       # ROE 改善度取前 10%
NEW_STOCK_DAYS = 250       # 上市不足 N 个自然日 -> 次新股剔除

INDUSTRY_CONTROL = True    # 是否启用行业黑名单
# [!] 口径差异，必须知道：本地策略用的是聚宽 sw_l1_name = 申万【2014 版】
#     （'钢铁I' / '采掘I' / '交运设备I' / '金融服务I' 这套带 I 后缀的名字），
#     而 QMT 的 SW1 板块是申万【2021 版】31 个一级行业。两版不是一一对应：
#         采掘   (2014) -> 2021 版拆成 煤炭 + 石油石化（已被下面两项覆盖）
#         金融服务(2014) -> 2021 版拆成 银行 + 非银金融（已被下面两项覆盖）
#         交运设备(2014) -> 2021 版散入 汽车 / 机械设备 / 国防军工（★ 无法对应，只能放弃）
#     所以 QMT 版的行业过滤与本地【不完全一致】。可接受的理由：逐年独立回测里
#     这个过滤 t=+1.56、11 年中 7 年为正，本就不显著；但差异必须写在这里而不是藏着。
INDUSTRY_FILTER  = ['钢铁', '煤炭', '石油石化',                  # 重资产（含 2014 版「采掘」）
                    '银行', '非银金融',                          # 高负债（含 2014 版「金融服务」）
                    '交通运输', '传媒', '环保']                   # 盈利差

SW1_PREFIX       = 'SW1'   # 申万一级板块名前缀，见 _resolve_industry_sectors

FIN_REFRESH_DAYS = 7       # 财报矩阵缓存天数；调大(如 30)可显著提速，代价是财报采纳滞后
FIN_BATCH        = 200     # get_financial_data 每批股票数
ORDER_PRICE_TYPE = 5       # passorder 报价方式：5=最新价（1d 周期下即当根 bar 收盘价）
MIN_LOT          = 100     # 最小交易单位

#--- 与本地回测(assay)标星配置的对应关系 --------------------------------------
# 下面三个参数的默认值 = assay 里【最新标星】的那条配置：
#   froec_traded + 盘中 -35% 固定止损   run_id 20260829-170945-4926f5
#   2016-01-04~2026-08-07 / 本金 10 万 / 默认成本(滑点 0.0015 双边、佣金万 2.5、印花税分段)
#   年化 40.87%  最大回撤 38.54%  夏普 1.33   （不加止损的基线 39.50% / 47.10% / 1.28）
#
# 要切到另外两条标星配置，只改这两行：
#   froec (kcb_688_only=0)  TRADED_UNIVERSE=False, STOP_LOSS=0.0  -> 36.38% / 46.84%
#   froec_traded            TRADED_UNIVERSE=True,  STOP_LOSS=0.0  -> 39.50% / 47.10%
# （EXCLUDE_PREFIX 已含 '689'，即 assay 里的 kcb_688_only=0：聚宽原版
#   filter_kcb_stock 写的是 stock[0:3]!='688'，漏掉 689 开头的科创板 CDR。）
#
# [!] 止损的采纳依据是「保费≈0 + 赔付方向一致」，不是「它更赚钱」：
#   逐年独立口径下保费 -0.00pp（8/11 年差额精确为 0），赔付是 2024 回撤 -6.16pp。
#   赔付证据 n=1（样本内只有 2024-02 一次尾部事件）。别指望它提高收益。
# 选股模式：本模板的下单/过滤/炸板/止损那套基础设施是共用的，只有【选股】不同。
#   'roec'      PB 升序前 50% -> 单季 ROE 改善前 10% -> 行业过滤 -> 流通市值升序
#               对应本地 strategies/小市值/froec.py
#   'mincap_eps' 累计 EPS>0 -> 流通市值升序（不看 PB、不看 ROE、不做行业过滤）
#               对应本地 strategies/小市值/sgmspeg_v0b.py
#               [!] 它比 roec 【更简单】：只要 floatmv + eps + ST + 宇宙四样。
SELECT_MODE     = 'roec'

TRADED_UNIVERSE = False  # 候选宇宙 = 决策日【实际有成交】的股票（assay: paused_in_pool=0）
STOP_LOSS       = 0.0  # 固定止损：相对建仓价回撤到 -35% 清仓；0 = 关
STOP_INTRADAY   = True  # True=当日最低价判定(盘中触发) / False=收盘价判定(日频)

VERBOSE          = True    # 打印每日复盘信息
VERIFY_FIELDS    = True    # init 时打印一次财务字段自检结果（首次接入务必看）

# 剔除的代码前缀：688/689 科创板，4/8 北交所，9/2 B 股
EXCLUDE_PREFIX = ('688', '689', '4', '8', '9', '2')

# 财务字段（QMT 财务表.字段名）。若你的 QMT 版本报字段不存在，只改这里 4 行即可
FIELD_EQUITY  = 'ASHAREBALANCESHEET.tot_shrhldr_eqy_excl_min_int'  # 归母股东权益(净资产)
FIELD_NETPROF = 'ASHAREINCOME.net_profit_excl_min_int_inc'         # 归母净利润(累计)
FIELD_TOTCAP  = 'CAPITALSTRUCTURE.total_capital'                   # 总股本
FIELD_CIRCAP  = 'CAPITALSTRUCTURE.circulating_capital'             # 流通股本

# 报告期 / 公告日。QMT 一直都有，只是必须【显式当字段请求】才会返回该列。
# 实测 601398（探针 E3，与本地 lake 逐字段核对）：
#     m_timetag = 1.703952e+12  -> 2023-12-31  报告期（本地 report_date 2023-12-31）
#     m_anntime = 1.7115552e+12 -> 2024-03-28  公告日（本地 pub_date    2024-03-28）
# 有了 m_anntime 就不必再用「法定披露截止日」兜底 —— 那会让本策略采纳财报
# 比真实公告晚 0~32 天，与本地 PIT 口径系统性偏离。
FIELD_REPTIME = 'ASHAREINCOME.m_timetag'
FIELD_ANNTIME = 'ASHAREINCOME.m_anntime'


class _G:
    pass


g = _G()


#============================== 0 框架入口 ====================================
def init(C):
    g.acct       = _resolve_account()
    g.acct_type  = ACCOUNT_TYPE
    g.intraday   = _is_intraday(C.period)

    g.hold_list         = []   # 当前持仓
    g.history_hold_list = []   # 近 LIMIT_DAYS 个调用日持仓过的股票（列表的列表）
    g.not_buy_again     = []   # 上者摊平后的集合
    g.high_limit_list   = []   # 昨日涨停的持仓股
    g.limit_price_map   = {}   # 上述股票今日涨停价
    g.target_list       = []

    g.last_week_key   = None   # 已调仓的 ISO 周标识
    g.fin_cache       = None   # 财报矩阵 DataFrame
    g.fin_call        = None   # get_financial_data 已定型的调用形态（见 _call_fin）
    g.fin_cache_date  = None
    g.detail_cache    = {}
    g.industry_sectors = None
    g.st_sector        = None
    g.verified         = False
    g.entry_date       = {}    # code -> 建仓日 YYYYMMDD，止损要用（见 check_stop_loss）
    g.stopped          = set() # 本轮已止损的票，避免同一天重复下单

    print('[init] account=%s period=%s intraday=%s' % (g.acct, C.period, g.intraday))
    print('[init] 单季ROE改善 = 4*最新单季ROE - 前4个单季ROE之和')
    print('[init] 宇宙=%s  止损=%s%s'
          % ('决策日有成交' if TRADED_UNIVERSE else '全部在市(含停牌)',
             ('-%.0f%%' % (STOP_LOSS * 100)) if STOP_LOSS else '关',
             ('(盘中最低价触发)' if STOP_INTRADAY else '(收盘价触发)') if STOP_LOSS else ''))


def handlebar(C):
    now = _bar_datetime(C)
    today = now.strftime('%Y%m%d')
    if getattr(g, 'today', None) != today:
        g.today = today
        g.stopped = set()                        # 止损去重按天重置

    if g.intraday:
        hm = now.hour * 100 + now.minute
        if hm == 931:                       # 对应聚宽 9:05 prepare + 9:30 调仓
            prepare_stock_list(C, today)
            weekly_adjustment(C, today)
        elif hm == 1400:                    # 对应聚宽 14:00 涨停打开检查
            check_limit_up(C, today, now)
        # ★ 止损【每根分钟 bar 都查】—— 盘中触发的意义就在于不必等到某个时点。
        #   放在涨停检查之后：先炸板离场，再判止损（与 assay 的注册顺序一致）。
        if hm >= 931:
            check_stop_loss(C, today, now)
        elif hm == 1455 and VERBOSE:        # 对应聚宽 15:10 复盘
            print_position_info(C, today)
        return

    # 日线模式：一根 bar 内顺序执行当天的 4 个动作
    prepare_stock_list(C, today)
    weekly_adjustment(C, today)
    check_limit_up(C, today, now)
    check_stop_loss(C, today, now)
    if VERBOSE:
        print_position_info(C, today)


#============================== 1-1 选股模块 ==================================
def get_stock_list(C, ref_date):
    """ref_date = 上一交易日(YYYYMMDD)，全部选股数据都截到该日，避免未来函数。"""
    pool = _base_pool(C, ref_date)
    if not pool:
        return []
    if SELECT_MODE == 'mincap_eps':
        return _select_mincap_eps(C, pool, ref_date)

    price = _price_frame(C, pool, ref_date)          # close / preClose / volume
    if price.empty:
        return []

    # ★ TRADED_UNIVERSE：宇宙 = 决策日【实际有成交】的票。
    #   必须在 PB / ROE 分位切点【之前】过滤 —— 停牌股若留在宇宙里会参与
    #   切点计算（int(0.5*len) 的 len 变了），从而换掉边界上的票。
    #   这不是「顺手清理」，是一条会改变选股结果的规则。
    #   v 不含前视：某只票当日有没有成交，在决策时点（前一交易日收盘后）已知。
    if TRADED_UNIVERSE:
        price = price[price['volume'] > 0]
        if price.empty:
            return []

    fin = _fin_matrix(C, list(price.index), ref_date)
    if fin.empty:
        return []

    df = price.join(fin, how='inner')
    df = df[df['equity_0'] > 0]                      # 净资产为正
    if df.empty:
        return []

    # --- PB 过滤（PB>0 且 EPS>0，PB 升序取前 50%）---
    df['pb'] = df['close'] * df['total_capital'] / df['equity_0']
    df['eps'] = df['netprofit_0'] / df['total_capital']
    df = df[(df['pb'] > 0) & (df['eps'] > 0)]
    if df.empty:
        return []
    df = df.sort_values('pb', ascending=True)
    df = df.iloc[:int(PB_TOP_RATIO * len(df))]
    if df.empty:
        return []

    # --- 单季 ROE 改善度过滤（取前 10%）---
    roe = pd.DataFrame(index=df.index)
    for i in range(5):
        eq = df['equity_%d' % i]
        roe['roe_%d' % i] = np.where(eq > 0, df['single_np_%d' % i] / eq * 100.0, np.nan)
    roe['increase'] = (4.0 * roe['roe_0']
                       - roe['roe_1'] - roe['roe_2'] - roe['roe_3'] - roe['roe_4'])
    roe = roe.dropna(subset=['increase'])
    if roe.empty:
        return []
    roe = roe.sort_values('increase', ascending=False)
    roe_list = list(roe.index[:int(ROE_TOP_RATIO * len(roe))])
    if not roe_list:
        return []

    # --- 行业过滤 ---
    if INDUSTRY_CONTROL:
        roe_list = filter_industry(C, roe_list, ref_date)
        if not roe_list:
            return []

    # --- 流通市值升序 ---
    sub = df.loc[roe_list].copy()
    sub['circ_cap_value'] = sub['close'] * sub['circulating_capital']
    sub = sub.sort_values('circ_cap_value', ascending=True)
    return list(sub.index)


def _select_mincap_eps(C, pool, ref_date):
    """v0b 口径：累计 EPS>0 的票，按流通市值升序取前 CANDIDATE_NUM。

    与 roec 模式的差别只在这里 —— 不看 PB、不看 ROE 改善、不做行业过滤。
    ST / 次新 / 科创 / 退市 已在 _base_pool 里剔过。

    [!] EPS 用【累计】不是单季：本地 v0b 取 fin_indicator_q 的 eps 按决策日 as-of，
        对应 QMT 的 ASHAREINCOME 累计归母净利 / 总股本。用单季会选出不同的票。
    """
    price = _price_frame(C, pool, ref_date)
    if price.empty:
        return []
    if TRADED_UNIVERSE:
        price = price[price['volume'] > 0]
        if price.empty:
            return []
    fin = _fin_matrix(C, list(price.index), ref_date)
    if fin.empty:
        return []
    df = price.join(fin, how='inner')
    df = df[df['total_capital'] > 0]
    if df.empty:
        return []
    df['eps'] = df['netprofit_0'] / df['total_capital']
    df = df[df['eps'] > 0]
    if df.empty:
        return []
    df['circ_cap_value'] = df['close'] * df['circulating_capital']
    df = df.sort_values('circ_cap_value', ascending=True)
    # 不在这里截断：截断统一由 weekly_adjustment 用 CANDIDATE_NUM 做，
    # 两个地方都截会让「截多少」这件事分散在两处，改一处漏一处。
    return list(df.index)


#============================== 1-2 行业过滤 ==================================
def filter_industry(C, stock_list, ref_date):
    """剔除属于 INDUSTRY_FILTER 行业的股票（按 ref_date 时点的板块成分）。"""
    sectors = _resolve_industry_sectors(C)
    if not sectors:
        return stock_list
    banned = set()
    for name in sectors:
        banned |= set(_sector_members(C, name, ref_date))
    return [s for s in stock_list if s not in banned]


#============================== 1-3 准备股票池 ================================
def prepare_stock_list(C, today):
    prev = _prev_trade_date(C, today)
    if prev is None:
        return

    if not g.verified:                 # 首根 bar 做一次环境自检（此时数据接口已就绪）
        g.verified = True
        _resolve_st_sector(C)
        _resolve_industry_sectors(C)
        if VERIFY_FIELDS:
            _verify_fin_fields(C, prev)

    # 当前持仓
    positions = _positions(C)
    g.hold_list = list(positions.keys())

    # 近 LIMIT_DAYS 次持仓过的股票
    g.history_hold_list.append(list(g.hold_list))
    if len(g.history_hold_list) >= LIMIT_DAYS:
        g.history_hold_list = g.history_hold_list[-LIMIT_DAYS:]
    temp = set()
    for hl in g.history_hold_list:
        temp |= set(hl)
    g.not_buy_again = list(temp)

    # 昨日涨停的持仓股 + 今日涨停价
    g.high_limit_list = []
    g.limit_price_map = {}
    if not g.hold_list:
        return
    px = _get_daily(C, g.hold_list, prev, 1, ['close', 'preClose'])
    for stock in g.hold_list:
        row = px.get(stock)
        if row is None:
            continue
        pre, close = row['preClose'], row['close']
        if pre <= 0 or close <= 0:
            continue
        if close >= _limit_price(C, stock, pre, ref_date=prev, up=True):
            g.high_limit_list.append(stock)
            g.limit_price_map[stock] = _limit_price(C, stock, close, ref_date=today, up=True)


#============================== 1-4 整体调仓 ==================================
def weekly_adjustment(C, today):
    week_key = _week_key(today)
    if g.last_week_key == week_key:      # 每周仅第一个交易日调仓（对应 run_weekly weekday=1）
        return
    prev = _prev_trade_date(C, today)
    if prev is None:
        return
    g.last_week_key = week_key

    target = get_stock_list(C, prev)[:CANDIDATE_NUM]
    target = filter_paused_stock(C, target, prev)
    target = filter_limitup_stock(C, target, prev, today)
    target = filter_limitdown_stock(C, target, prev, today)

    # 剔除「最近买过且近 LIMIT_DAYS 日涨停过」的股票
    recent = get_recent_limit_up_stock(C, target, prev, LIMIT_DAYS)
    black = set(g.not_buy_again) & set(recent)
    target = [s for s in target if s not in black]
    target = target[:min(STOCK_NUM, len(target))]
    g.target_list = target
    print('[%s] 目标持仓(%d): %s' % (today, len(target), target))

    positions = _positions(C)
    cash_before = _available_cash(C)
    est_proceeds = 0.0

    # 卖出：不在目标池、且昨日没涨停
    for stock, pos in list(positions.items()):
        if stock not in target and stock not in g.high_limit_list:
            print('  卖出[%s]' % stock)
            if close_position(C, pos):
                est_proceeds += pos['volume'] * (pos['price'] or pos['cost'])
        else:
            print('  已持有[%s]' % stock)

    # 买入：等分可用资金。
    # 保留下来的仓位 = 目标池内的 + 昨日涨停暂留的，据此推出本次要新建的仓位，
    # 不依赖「卖单是否已即时结算」，避免回测撮合延迟导致买不进。
    kept = [s for s in positions if s in target or s in g.high_limit_list]
    to_buy = [s for s in target if s not in kept]
    if not to_buy:
        return

    cash_after = _available_cash(C)
    if est_proceeds > 0 and cash_after < cash_before + est_proceeds * 0.9:
        cash = cash_before + est_proceeds        # 卖出资金尚未回笼，按估算值分配
    else:
        cash = cash_after
    if cash <= 0:
        print('  可用资金为 0，跳过买入')
        return

    value = cash / float(len(to_buy))
    px = _get_daily(C, to_buy, prev, 1, ['close'])
    for stock in to_buy:
        row = px.get(stock)
        if row is None or row['close'] <= 0:
            continue
        open_position(C, stock, value, row['close'])


#============================== 1-5 涨停打开检查 ==============================
def check_limit_up(C, today, now):
    if not g.high_limit_list:
        return
    positions = _positions(C)
    for stock in list(g.high_limit_list):
        if stock not in positions:
            continue
        limit = g.limit_price_map.get(stock)
        if not limit:
            continue
        last = _last_close(C, stock, today, now)
        if last is None:
            continue
        if last < limit:
            print('  [%s]涨停打开(%.2f<%.2f)，卖出' % (stock, last, limit))
            close_position(C, positions[stock])
            g.high_limit_list.remove(stock)
        else:
            print('  [%s]涨停，继续持有' % stock)


#============================== 1-6 固定止损 ==================================
def check_stop_loss(C, today, now):
    """固定止损：持仓相对建仓价跌破 -STOP_LOSS 即清仓。

    [!] 复权口径是这段代码最容易错的地方：
        QMT 持仓里的 m_dOpenPrice 是【不复权】成交价，而行情默认取【前复权】。
        两者直接相比，持有期内一次分红除权就会造成【假触发】——
        股价没跌，只是除权除息把价格台阶式压下去了。
        所以这里改成：记下建仓【日期】(g.entry_date)，用【同一次】
        dividend_type='front' 的取数同时拿到「建仓日收盘」和「今日最低/收盘」，
        复权基准相同、自动抵消。
        策略中途重启会丢 g.entry_date，此时退回 m_dOpenPrice 并打印告警 ——
        宁可让你看见口径降级，也不要静默用错基准。

    STOP_INTRADAY=True 用当日【最低价】判定（盘中触发，与 assay 的
    stop_intraday=1 对应）；False 用收盘价（日频）。
    """
    if not STOP_LOSS:
        return
    positions = _positions(C)
    if not positions:
        return
    codes = [c for c in positions if c not in g.stopped]
    if not codes:
        return
    # 取到「最早建仓日」为止的窗口，一次拿全，避免逐票取数
    need = 5
    for c in codes:
        ed = g.entry_date.get(c)
        if ed:
            need = max(need, _days_between(ed, today) + 3)
    need = min(need, 400)
    px = _hfq_frames(C, codes, today, need)
    for code in codes:
        df = px.get(code)
        if df is None or len(df) == 0:
            continue                                  # 停牌，卖不掉
        ed = g.entry_date.get(code)
        base = None
        if ed:
            idx = [str(i)[:10].replace('-', '') for i in df.index]
            hit = [k for k, v in enumerate(idx) if v <= ed]
            if hit:
                base = float(df['close'].iloc[hit[-1]])
        if base is None or base <= 0:
            base = positions[code]['cost']            # 降级：不复权成本价
            if base > 0:
                print('  [%s] 止损缺建仓日记录，退回不复权成本价 %.2f'
                      '（除权除息期间可能假触发）' % (code, base))
        if not base or base <= 0:
            continue
        trig = base * (1.0 - STOP_LOSS)
        last_row = df.iloc[-1]
        cur = float(last_row['low'] if STOP_INTRADAY else last_row['close'])
        if cur > 0 and cur <= trig:
            print('  [%s] 触发固定止损：%.2f <= %.2f（建仓 %.2f，-%.0f%%）'
                  % (code, cur, trig, base, STOP_LOSS * 100))
            close_position(C, positions[code])
            g.stopped.add(code)


#============================== 2-x 过滤函数 ==================================
def filter_paused_stock(C, stock_list, ref_date):
    """停牌过滤：参考日无成交量视为停牌。"""
    if not stock_list:
        return []
    px = _get_daily(C, stock_list, ref_date, 1, ['close', 'volume'])
    return [s for s in stock_list
            if px.get(s) is not None and px[s]['volume'] > 0 and px[s]['close'] > 0]


def filter_st_stock(C, stock_list, ref_date):
    """ST / 退市整理过滤：优先用 ST 板块的时点成分，兜底用名称。"""
    if not stock_list:
        return []
    st_set = set()
    name = _resolve_st_sector(C)
    if name:
        st_set = set(_sector_members(C, name, ref_date))
    out = []
    for s in stock_list:
        if s in st_set:
            continue
        nm = _detail(C, s).get('InstrumentName') or ''
        if 'ST' in nm.upper() or '*' in nm or '退' in nm:
            continue
        out.append(s)
    return out


def get_recent_limit_up_stock(C, stock_list, ref_date, recent_days):
    """近 recent_days 个交易日内出现过涨停的股票。"""
    if not stock_list:
        return []
    data = C.get_market_data_ex(['close', 'preClose'], stock_list, period='1d',
                                end_time=ref_date, count=recent_days,
                                dividend_type='none', fill_data=False, subscribe=False)
    out = []
    for stock in stock_list:
        df = data.get(stock)
        if df is None or df.empty:
            continue
        hit = False
        for _, r in df.iterrows():
            pre, close = float(r['preClose']), float(r['close'])
            if pre <= 0 or close <= 0:
                continue
            if close >= _limit_price(C, stock, pre, ref_date, up=True):
                hit = True
                break
        if hit:
            out.append(stock)
    return out


def filter_limitup_stock(C, stock_list, ref_date, today):
    """参考日收盘涨停的不买（持仓的除外）。涨停价按参考日 preClose 算。"""
    return _filter_by_limit(C, stock_list, ref_date, up=True)


def filter_limitdown_stock(C, stock_list, ref_date, today):
    """参考日收盘跌停的不买（持仓的除外）。"""
    return _filter_by_limit(C, stock_list, ref_date, up=False)


def _filter_by_limit(C, stock_list, ref_date, up):
    if not stock_list:
        return []
    positions = _positions(C)
    px = _get_daily(C, stock_list, ref_date, 1, ['close', 'preClose'])
    out = []
    for s in stock_list:
        if s in positions:
            out.append(s)
            continue
        row = px.get(s)
        if row is None or row['close'] <= 0 or row['preClose'] <= 0:
            continue
        limit = _limit_price(C, s, row['preClose'], ref_date, up=up)
        if (up and row['close'] < limit) or ((not up) and row['close'] > limit):
            out.append(s)
    return out


def filter_kcb_stock(stock_list):
    return [s for s in stock_list if not s.split('.')[0].startswith(EXCLUDE_PREFIX)]


def filter_new_stock(C, stock_list, ref_date):
    """上市不足 NEW_STOCK_DAYS 个自然日的次新股剔除。"""
    ref = dt.datetime.strptime(ref_date, '%Y%m%d')
    out = []
    for s in stock_list:
        d = _detail(C, s)
        open_date = str(d.get('OpenDate') or '')
        if len(open_date) != 8 or open_date == '00000000':
            continue
        try:
            listed = dt.datetime.strptime(open_date, '%Y%m%d')
        except ValueError:
            continue
        if (ref - listed).days >= NEW_STOCK_DAYS:
            out.append(s)
    return out


def _base_pool(C, ref_date):
    pool = _sector_members(C, POOL_SECTOR, ref_date)
    pool = filter_kcb_stock(pool)
    pool = filter_new_stock(C, pool, ref_date)
    pool = filter_st_stock(C, pool, ref_date)
    pool = _filter_delisted(C, pool, ref_date)
    return pool


def _filter_delisted(C, stock_list, ref_date):
    out = []
    for s in stock_list:
        expire = str(_detail(C, s).get('ExpireDate') or '')
        if len(expire) == 8 and expire != '00000000' and expire <= ref_date:
            continue
        out.append(s)
    return out


#============================== 3-x 交易模块 ==================================
def open_position(C, stock, value, ref_price):
    """按金额买入，自行取整到 100 股。
    ref_price 是参考日收盘价，实际成交在当根 bar，留 2% 缓冲防止跳空后资金不足被废单。
    """
    volume = int(value / (ref_price * 1.02 * MIN_LOT)) * MIN_LOT
    if volume < MIN_LOT:
        return False
    print('  买入[%s] 约%.0f元 / %d股' % (stock, value, volume))
    passorder(23, 1101, g.acct, stock, ORDER_PRICE_TYPE, -1, volume, C)
    g.entry_date[stock] = g.today                # 止损要用，见 check_stop_loss
    g.stopped.discard(stock)
    return True


def close_position(C, position):
    volume = position['can_use'] if position['can_use'] > 0 else position['volume']
    if volume <= 0:
        return False
    passorder(24, 1101, g.acct, position['code'], ORDER_PRICE_TYPE, -1, volume, C)
    g.entry_date.pop(position['code'], None)
    return True


def _positions(C):
    """返回 {code: {code, volume, can_use, cost, price, value}}"""
    out = {}
    try:
        objs = get_trade_detail_data(g.acct, g.acct_type, 'POSITION')
    except Exception as e:
        print('[warn] 读取持仓失败: %s' % e)
        return out
    for o in objs or []:
        volume = int(getattr(o, 'm_nVolume', 0) or 0)
        if volume <= 0:
            continue
        code = '%s.%s' % (o.m_strInstrumentID, o.m_strExchangeID)
        out[code] = {
            'code': code,
            'volume': volume,
            'can_use': int(getattr(o, 'm_nCanUseVolume', 0) or 0),
            'cost': float(getattr(o, 'm_dOpenPrice', 0) or 0),
            'price': float(getattr(o, 'm_dLastPrice', 0) or 0),
            'value': float(getattr(o, 'm_dMarketValue', 0) or 0),
        }
    return out


def _available_cash(C):
    try:
        accs = get_trade_detail_data(g.acct, g.acct_type, 'ACCOUNT')
        if accs:
            return float(accs[0].m_dAvailable)
    except Exception as e:
        print('[warn] 读取账户资金失败: %s' % e)
    return 0.0


#============================== 4-1 复盘打印 ==================================
def print_position_info(C, today):
    positions = _positions(C)
    if not positions:
        print('[%s] 空仓' % today)
        return
    print('[%s] 持仓明细：' % today)
    for p in positions.values():
        ret = (p['price'] / p['cost'] - 1) * 100 if p['cost'] > 0 else 0.0
        print('  %s 成本:%.2f 现价:%.2f 收益:%.2f%% 数量:%d 市值:%.2f'
              % (p['code'], p['cost'], p['price'], ret, p['volume'], p['value']))
    print('  ' + '-' * 60)


#============================== 财务数据层 ====================================
def _fin_matrix(C, stocks, ref_date):
    """返回 DataFrame(index=code)，列：
       equity_0..4    最近 5 个报告期的期末归母净资产（0 为最新）
       netprofit_0..4 对应报告期的累计归母净利润
       single_np_0..4 换算出的单季归母净利润
       total_capital / circulating_capital
       所有报告都按「公告日 <= ref_date」筛过，无未来函数。
    """
    if (g.fin_cache is not None and g.fin_cache_date is not None
            and _days_between(g.fin_cache_date, ref_date) < FIN_REFRESH_DAYS):
        return g.fin_cache.reindex([s for s in stocks if s in g.fin_cache.index])

    # 需要 5 个【已公告】的季报。按季末+法定截止日算，最坏要回看约 2.5 年：
    #   5 期 = 15 个月，再加最长 4 个月的公告滞后，再留一季冗余。
    start = _shift_days(ref_date, -900)
    rows = {}
    for batch in _chunks(stocks, FIN_BATCH):
        _f = [FIELD_EQUITY, FIELD_NETPROF, FIELD_REPTIME, FIELD_ANNTIME]
        raw = _call_fin(C, _f, batch, start, ref_date)
        per_stock = _normalize_fin(raw, batch, _f)
        for code, df in per_stock.items():
            rec = _build_quarter_record(df, FIELD_EQUITY, FIELD_NETPROF, ref_date)
            if rec:
                rows[code] = rec

    if not rows:
        print('[warn] 财务数据为空，请核对字段名 %s / %s' % (FIELD_EQUITY, FIELD_NETPROF))
        g.fin_cache, g.fin_cache_date = pd.DataFrame(), ref_date
        return g.fin_cache

    cap = _capital_map(C, list(rows.keys()), ref_date)
    for code in list(rows.keys()):
        c = cap.get(code)
        if not c or c[0] <= 0 or c[1] <= 0:
            del rows[code]
            continue
        rows[code]['total_capital'] = c[0]
        rows[code]['circulating_capital'] = c[1]

    g.fin_cache = pd.DataFrame.from_dict(rows, orient='index')
    g.fin_cache_date = ref_date
    print('[%s] 财报矩阵刷新完成，覆盖 %d 只' % (ref_date, len(g.fin_cache)))
    return g.fin_cache.reindex([s for s in stocks if s in g.fin_cache.index])


def _build_quarter_record(df, eq_field, np_field, ref_date):
    """把单只股票的报告序列整理成 5 期净资产 + 5 期单季净利润。"""
    if df is None or df.empty:
        return None
    eq_col = _match_col(df, eq_field)
    np_col = _match_col(df, np_field)
    if eq_col is None or np_col is None:
        return None

    df = df[df['report'] > 0].copy()
    df = df[df['anndate'] <= int(ref_date)]            # 时点对齐：只用已公告的报告
    if df.empty:
        return None
    df = df.drop_duplicates(subset=['report'], keep='last').sort_values('report')

    cum = dict(zip(df['report'].tolist(), df[np_col].tolist()))
    eq = dict(zip(df['report'].tolist(), df[eq_col].tolist()))
    reports = sorted(cum.keys(), reverse=True)[:5]     # 最近 5 期
    if len(reports) < 5:
        return None

    rec = {}
    for i, rep in enumerate(reports):
        single = _single_quarter(rep, cum)
        if single is None or pd.isna(single):
            return None
        e = eq.get(rep)
        if e is None or pd.isna(e):
            return None
        rec['report_%d' % i] = rep
        rec['equity_%d' % i] = float(e)
        rec['netprofit_%d' % i] = float(cum[rep])
        rec['single_np_%d' % i] = float(single)
    return rec


def _single_quarter(rep, cum):
    """累计口径 -> 单季口径。一季报直接用累计值。"""
    q = _quarter_of(rep)
    if q is None:
        return None
    if q == 1:
        return cum.get(rep)
    prev = _prev_report(rep)
    if prev not in cum or rep not in cum:
        return None
    a, b = cum[rep], cum[prev]
    if pd.isna(a) or pd.isna(b):
        return None
    return a - b


def _capital_map(C, stocks, ref_date):
    """{code: (总股本, 流通股本)}；股本表取不到时退回 get_instrumentdetail。"""
    out = {}
    start = _shift_days(ref_date, -2000)
    for batch in _chunks(stocks, FIN_BATCH):
        try:
            raw = _call_fin(C, [FIELD_TOTCAP, FIELD_CIRCAP], batch, start, ref_date)
            per_stock = _normalize_fin(raw, batch, [FIELD_TOTCAP, FIELD_CIRCAP])
        except Exception:
            per_stock = {}
        for code, df in per_stock.items():
            if df is None or df.empty:
                continue
            tc = _match_col(df, FIELD_TOTCAP)
            cc = _match_col(df, FIELD_CIRCAP)
            if tc is None or cc is None:
                continue
            df = df[df['anndate'] <= int(ref_date)].sort_values('anndate')
            if df.empty:
                continue
            last = df.iloc[-1]
            try:
                out[code] = (float(last[tc]), float(last[cc]))
            except (TypeError, ValueError):
                continue

    missing = [s for s in stocks if s not in out]
    for code in missing:                       # 兜底：合约详情里的股本（注意是最新值）
        d = _detail(C, code)
        try:
            tv = float(d.get('TotalVolume') or 0)
            fv = float(d.get('FloatVolume') or 0)
        except (TypeError, ValueError):
            continue
        if tv > 0 and fv > 0:
            out[code] = (tv, fv)
    if missing:
        print('[warn] %d 只股本取自 get_instrumentdetail（最新值，存在轻微前视）' % len(missing))
    return out


# [!!] report_type 就是未来函数的根因，这里是全文件最要紧的一行。
#
# 官方文档（迅投知识库 innerApi/data_function）：report_type 取
#     'announce_time'  按【公告期】取数 —— 发布日之后到下个财报发布日之间，
#                      给的都是该期财报的值。这是默认值，也是【时点正确】的口径。
#     'report_time'    按【报告期】取数 —— 上年 Q4 的值就落在上年报告期上。
#
# 而移植原来硬写的正是 report_type='report_time'，等于【主动要了未来函数那一版】：
# 实测 601398 在 20241231 当天就给出 2025-03-29 才公告的年报，提前 88 天。
# 换成 'announce_time' 是一个词的修复，比在下游拿 m_anntime 事后过滤更干净
# （m_anntime 仍然照请求，用作交叉校验与 raw 口径的兜底）。
#
# 另一个坑：传不认识的字符串（'announce'/'report'/'1'/'0'）是【静默返回 None】不报错，
# 结果是财务整片为空、策略照跑、只是一只票都选不出来。故走降级阶梯并缓存首个可用形态。
_FIN_CALLS = (
    ("report_type='announce_time' ★按公告期",
     lambda C, f, s, a, b: C.get_financial_data(f, s, a, b, report_type='announce_time')),
    ('report_type=1（形态兜底，语义未验证）',
     lambda C, f, s, a, b: C.get_financial_data(f, s, a, b, 1)),
)

# [!] 这两种【绝对不要用】，留在这里是为了别再有人「顺手简化一下」：
#       C.get_financial_data(f, s, a, b)                        # 不传
#       C.get_financial_data(f, s, a, b, report_type='report_time')
#     实测三列对照（601398，净利，单位元）：
#         日期        announce_time   不传          report_time
#         20241230    2.6902e+11      2.6902e+11    2.6902e+11
#         20241231    2.6902e+11      3.6586e+11    3.6586e+11   <- 提前 88 天
#         20250328    2.6902e+11      3.6586e+11    3.6586e+11
#         20250331    3.6586e+11      8.4156e+10    8.4156e+10
#     【不传 == report_time】—— 文档写的「默认即 announce_time」与实测不符。
#     所以必须显式传，不能靠默认。


def _fin_nonempty(r):
    """注意不能写 `if not r:` —— Panel/DataFrame 的真值判断会抛异常。"""
    if r is None:
        return False
    if isinstance(r, dict):
        return len(r) > 0
    if isinstance(r, pd.DataFrame):
        return not r.empty
    return True                                   # Panel 等其它形态交给下游 normalize


def _call_fin(C, fields, stocks, start_date, end_date):
    order = list(_FIN_CALLS)
    if getattr(g, 'fin_call', None) is not None:  # 已定型：只走选中的那种
        order = [g.fin_call] + [c for c in order if c is not g.fin_call]
    for entry in order:
        name, fn = entry
        try:
            r = fn(C, fields, stocks, start_date, end_date)
        except Exception as e:
            if g.fin_call is None:
                print('[fin] %s -> 异常 %s' % (name, e))
            continue
        if _fin_nonempty(r):
            if g.fin_call is not entry:
                print('[fin] get_financial_data 采用 %s' % name)
                g.fin_call = entry
            return r
    print('[warn] get_financial_data 四种形态全部无数据，请核对字段名与数据下载')
    return None


def _normalize_fin(raw, stocks, fields):
    """把 get_financial_data 的多种返回形态统一成 {code: DataFrame(含 report/anndate 列)}"""
    result = {}
    if raw is None:
        return result

    if isinstance(raw, dict):
        keys = list(raw.keys())
        if keys and keys[0] in set(stocks):                 # {code: DataFrame}
            for code, df in raw.items():
                fixed = _attach_report_cols(df)
                if fixed is not None:
                    result[code] = fixed
            return result
        # {field: DataFrame(index=date, columns=code)} —— 按字段拼回每只股票
        frames = {}
        for field, df in raw.items():
            if not isinstance(df, pd.DataFrame):
                continue
            for code in df.columns:
                frames.setdefault(code, {})[_short(field)] = df[code]
        for code, cols in frames.items():
            df = pd.DataFrame(cols)
            fixed = _attach_report_cols(df)
            if fixed is not None:
                result[code] = fixed
        return result

    if isinstance(raw, pd.DataFrame) and len(stocks) == 1:
        fixed = _attach_report_cols(raw)
        if fixed is not None:
            result[stocks[0]] = fixed
    return result


def _quarter_ends(start, end):
    """[start, end] 之间的所有季末 YYYYMMDD。"""
    out = []
    for y in range(start // 10000, end // 10000 + 1):
        for md in (331, 630, 930, 1231):
            r = y * 10000 + md
            if start <= r <= end:
                out.append(r)
    return out


def _attach_report_cols(df):
    """把【按交易日前向填充】的财务序列还原成【按报告期】的表。

    [!] 这里曾经错得很隐蔽，值得写清楚：

        QMT 的 get_financial_data 返回的 index 是【交易日】不是报告期，
        而且它在【报告期当天】就切到该期的值。实测 601398：
            20241225~20241230  净利 2690.3 亿（2024Q3，公告日 2024-10-31）
            20241231           净利 3658.6 亿（2024 年报，公告日 2025-03-29）
        也就是说 2024-12-31 那天就能看到 88 天后才公告的年报 ——
        标准未来函数。数值本身是对的（两个数都与本地精确吻合），
        错的是【时间对齐】。

        原实现直接 `report = index`，把交易日当报告期。加上 _quarter_of
        只看月份∈{3,6,9,12}，于是 20240315 这种日子也被当成 Q1 报告，
        再配上「累计值相减求单季」—— 产出的不是空值而是【静默的垃圾】。

    正确做法（本函数）：
      1. 按季末枚举报告期 R
      2. 报告期 R 的值 = index 中【第一个 >= R 的交易日】上的值
         （季末常是周末，比如 2024-03-31 是周日，所以不能直接查 R）
      3. 公告日取不到时用法定披露截止日兜底（Q1/年报 4-30、中报 8-31、
         三季报 10-31），由调用方按 anndate <= ref_date 过滤
    """
    if not isinstance(df, pd.DataFrame) or df.empty:
        return None
    df = df.copy()
    df.columns = [_short(c) for c in df.columns]

    # 若 QMT 直接给了报告期/公告日列，优先用它们（不同版本可能有）
    ann_col = None
    for cand in ('m_anntime', 'm_anndate', 'ann_dt', 'anndate'):
        if cand in df.columns:
            ann_col = cand
            break
    if 'm_timetag' in df.columns or ann_col is not None:
        if 'm_timetag' in df.columns:
            df['report'] = df['m_timetag'].map(_to_yyyymmdd)
        else:                                   # 只有公告日：报告期退回按季末推断
            df['report'] = [_enclosing_report(_to_yyyymmdd(i)) for i in df.index]
        if ann_col is not None:
            df['anndate'] = df[ann_col].map(_to_yyyymmdd)
            bad = df['anndate'] <= 0
            if bad.any():                       # 个别缺失才退回法定截止日
                df.loc[bad, 'anndate'] = df.loc[bad, 'report'].map(_deadline_anndate)
        else:
            df['anndate'] = df['report'].map(_deadline_anndate)
        df = df[df['report'] > 0]
        if df.empty:
            return None
        return df.drop_duplicates(subset=['report'], keep='last')

    # 常规形态：index 是交易日 -> 按季末重建
    idx = sorted(_to_yyyymmdd(i) for i in df.index)
    idx = [i for i in idx if i > 0]
    if not idx:
        return None
    pos = {}
    for k, i in enumerate(sorted(df.index, key=lambda x: _to_yyyymmdd(x))):
        pos[idx[k]] = i
    # [!] 季末到了不等于财报发了。若某期未发布，前向填充的值仍是上一期的 ——
    #     无条件为每个季末建一行，会造出【幽灵报告期】（比如 2025Q1 还没发，
    #     却记成「2025Q1 净利 = 2024年报的 3658.6 亿」），后面按累计值相减求
    #     单季就会得到 0 或负数，静默错。
    #     判据：值在该季末【发生跳变】才算真有新报告。
    cols = [c for c in df.columns if c not in ('report', 'anndate')]
    rows, prev = [], None
    for rep in _quarter_ends(idx[0], idx[-1]):
        later = [i for i in idx if i >= rep]
        if not later:
            continue
        row = df.loc[pos[later[0]]].to_dict()
        sig = tuple(row.get(c) for c in cols)
        if prev is not None and sig == prev:
            continue                       # 值没变 -> 该期未发布，跳过
        prev = sig
        row['report'] = rep
        row['anndate'] = _deadline_anndate(rep)
        rows.append(row)
    if not rows:
        return None
    return pd.DataFrame(rows)


def _match_col(df, field):
    short = _short(field)
    if short in df.columns:
        return short
    for c in df.columns:
        if c.lower() == short.lower():
            return c
    return None


def _short(field):
    return str(field).split('.')[-1]


def _to_yyyymmdd(v):
    """把报告期/公告日的各种形态（int、'20240331'、'2024-03-31'、毫秒时间戳）统一成 int。"""
    if v is None:
        return 0
    if isinstance(v, (pd.Timestamp, dt.datetime, dt.date)):
        return int(v.strftime('%Y%m%d'))
    s = str(v).strip().replace('-', '').replace('/', '')
    if not s or s == 'nan':
        return 0
    if '.' in s:
        s = s.split('.')[0]
    if len(s) > 8:                     # 毫秒时间戳
        try:
            return int(dt.datetime.fromtimestamp(int(s) / 1000.0).strftime('%Y%m%d'))
        except (ValueError, OSError):
            return 0
    if len(s) >= 8:
        s = s[:8]
    try:
        return int(s)
    except ValueError:
        return 0


def _enclosing_report(d):
    """交易日 d 所处（或刚过）的季末报告期。仅在拿到公告日但没拿到 m_timetag 时用。"""
    if not d:
        return 0
    y, md = d // 10000, d % 10000
    for cut in (1231, 930, 630, 331):
        if md >= cut:
            return y * 10000 + cut
    return (y - 1) * 10000 + 1231


def _quarter_of(rep):
    m = (rep // 100) % 100
    return {3: 1, 6: 2, 9: 3, 12: 4}.get(m)


def _prev_report(rep):
    y, q = rep // 10000, _quarter_of(rep)
    if q is None:
        return 0
    return {2: y * 10000 + 331, 3: y * 10000 + 630, 4: y * 10000 + 930}.get(q, 0)


def _deadline_anndate(rep):
    """无公告日时按法定披露截止日兜底：Q1/年报 4-30，中报 8-31，三季报 10-31。"""
    y, q = rep // 10000, _quarter_of(rep)
    if q is None:
        return 99999999
    if q == 4:
        return (y + 1) * 10000 + 430
    return y * 10000 + {1: 430, 2: 831, 3: 1031}[q]


def _verify_fin_fields(C, ref_date):
    """首次接入自检：拿一只股票打印财务返回结构，确认字段名可用。"""
    probe = '600000.SH'
    try:
        raw = _call_fin(C, [FIELD_EQUITY, FIELD_NETPROF], [probe],
                        _shift_days(ref_date, -800), ref_date)
        per = _normalize_fin(raw, [probe], [FIELD_EQUITY, FIELD_NETPROF])
        df = per.get(probe)
        if df is None or df.empty:
            print('[自检][失败] %s 无财务数据，请核对 FIELD_EQUITY / FIELD_NETPROF' % probe)
        else:
            print('[自检][通过] 列=%s' % list(df.columns))
            print(df[['report', 'anndate']].tail(3).to_string())
    except Exception as e:
        print('[自检][异常] get_financial_data 调用失败: %s' % e)


#============================== 行情 / 板块 / 日历 ============================
def _get_daily(C, stocks, end_date, count, fields):
    """返回 {code: {field: float}}，取参考日那一根日线（不复权）。"""
    out = {}
    if not stocks:
        return out
    need = list(set(fields) | set(['close']))
    data = C.get_market_data_ex(need, list(stocks), period='1d', end_time=end_date,
                                count=count, dividend_type='none',
                                fill_data=False, subscribe=False)
    for code in stocks:
        df = data.get(code)
        if df is None or df.empty:
            continue
        row = df.iloc[-1]
        rec = {}
        ok = True
        for f in need:
            try:
                rec[f] = float(row[f])
            except (KeyError, TypeError, ValueError):
                ok = False
                break
        if ok:
            out[code] = rec
    return out


def _hfq_frames(C, stocks, end_date, count):
    """返回 {code: DataFrame(index=日期, 含 close/low)}，**前复权**、整段。

    与 _get_daily 的两点不同，都是止损需要的：
      1) 要整段不是最后一根 —— 要在同一个 frame 里同时读到建仓日和今日
      2) dividend_type='front' 而不是 'none' —— 建仓价与现价必须同一复权基准，
         否则持有期内一次分红除权就会把止损打成假触发
    """
    out = {}
    if not stocks:
        return out
    try:
        data = C.get_market_data_ex(['close', 'low'], list(stocks), period='1d',
                                    end_time=end_date, count=count,
                                    dividend_type='front',
                                    fill_data=False, subscribe=False)
    except Exception as e:
        print('[warn] 止损取数失败: %s' % e)
        return out
    for code in stocks:
        df = data.get(code)
        if df is not None and len(df) > 0:
            out[code] = df
    return out


def _price_frame(C, stocks, ref_date):
    """选股用的价格面板：close / preClose / volume，剔除停牌与无效数据。"""
    rows = {}
    for batch in _chunks(stocks, 500):
        px = _get_daily(C, batch, ref_date, 1, ['close', 'preClose', 'volume'])
        for code, rec in px.items():
            if rec['close'] > 0 and rec['volume'] > 0:
                rows[code] = rec
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame.from_dict(rows, orient='index')


def _last_close(C, stock, today, now):
    """当前最新价：日线模式取当根日线收盘；分钟模式取当前分钟收盘。"""
    if g.intraday:
        end = now.strftime('%Y%m%d%H%M%S')
        data = C.get_market_data_ex(['close'], [stock], period=C.period, end_time=end,
                                    count=1, dividend_type='none',
                                    fill_data=True, subscribe=False)
    else:
        data = C.get_market_data_ex(['close'], [stock], period='1d', end_time=today,
                                    count=1, dividend_type='none',
                                    fill_data=False, subscribe=False)
    df = data.get(stock)
    if df is None or df.empty:
        return None
    try:
        v = float(df.iloc[-1]['close'])
    except (KeyError, TypeError, ValueError):
        return None
    return v if v > 0 else None


def _limit_price(C, stock, pre_close, ref_date, up=True):
    """按交易所规则算涨跌停价（QMT 无历史涨跌停价字段，这里自算）。"""
    code = stock.split('.')[0]
    if code.startswith(('300', '301', '688', '689')):
        ratio = 0.20
    elif code.startswith(('4', '8')):
        ratio = 0.30
    elif stock in _st_set_cached(C, ref_date):
        ratio = 0.05
    else:
        ratio = 0.10
    raw = pre_close * (1 + ratio) if up else pre_close * (1 - ratio)
    return float(Decimal(str(raw)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))


def _st_set_cached(C, ref_date):
    key = ('st', ref_date)
    if getattr(g, '_st_key', None) == key:
        return g._st_val
    name = _resolve_st_sector(C)
    val = set(_sector_members(C, name, ref_date)) if name else set()
    g._st_key, g._st_val = key, val
    return val


def _sector_members(C, sector, ref_date):
    """取板块成分；优先带时点参数（时点成分），失败则退回最新成分。"""
    if not sector:
        return []
    key = (sector, ref_date)
    cache = getattr(g, '_sector_cache', None)
    if cache is None:
        cache = g._sector_cache = {}
    if key in cache:
        return cache[key]
    members = []
    try:
        members = C.get_stock_list_in_sector(sector, ref_date) or []
    except Exception:
        try:
            members = C.get_stock_list_in_sector(sector) or []
        except Exception as e:
            print('[warn] 取板块[%s]失败: %s' % (sector, e))
    if len(cache) > 40:
        cache.clear()
    cache[key] = list(members)
    return cache[key]


def _sector_list(C):
    try:
        return list(C.get_sector_list() or [])
    except Exception:
        return []


def _resolve_st_sector(C):
    if g.st_sector is not None:
        return g.st_sector or None
    all_sectors = _sector_list(C)
    # [已实测 2026-08-31] 十个候选里只有 '沪深风险警示' 有成分（206 只），
    # ST板块/ST/风险警示/*ST/ST股票/沪深ST/两市ST/ST及*ST 全部 0 只。
    # 把它放第一位，其余留作不同版本的兜底。
    for cand in ('沪深风险警示', 'ST板块', '风险警示', 'ST', '风险警示板块'):
        if not all_sectors or cand in all_sectors:
            if _sector_members(C, cand, ''):        # 空时点 = 取最新成分，仅用于探测板块名
                g.st_sector = cand
                print('[init] ST 板块 = %s' % cand)
                return cand
    g.st_sector = ''
    print('[init] 未找到 ST 板块，ST 过滤退化为按名称判断（存在时点偏差）')
    return None


def _resolve_industry_sectors(C):
    """把 INDUSTRY_FILTER 的行业名映射成 QMT 板块名。

    [已实测] QMT 板块名 = 【前缀 + 行业名，不加分隔符】，不是「申万XX」也不是光秃秃的行业名。
        get_stock_list_in_sector('SW1银行')     -> 42 只，含 601398.SH   [OK]
        get_stock_list_in_sector('银行')        ->  0 只                 <- 上一轮就栽在这
        get_industry('SW1银行')                 -> 与上者等价
        SW1 全 31 个一级行业逐个跑：31 个都有成分股，合计 5551 只（全市场约 5200，量级对）
    前缀族：SW1/SW2 申万一二级、CSRC1/CSRC2 证监会、THY1/THY2 通达信、TGN/GN 概念、DY1 地域。
    实测 THY1银行 / TGN银行 都是 0 只 —— 通达信那套在本环境没有成分数据，别用。
    """
    if g.industry_sectors is not None:
        return g.industry_sectors
    resolved, missing = [], []
    for kw in INDUSTRY_FILTER:
        name = SW1_PREFIX + kw
        if _sector_members(C, name, ''):
            resolved.append(name)
        else:
            missing.append(kw)
    g.industry_sectors = resolved
    print('[init] 行业黑名单板块(%d/%d): %s'
          % (len(resolved), len(INDUSTRY_FILTER), resolved))
    if missing:
        # 不静默：黑名单少一个行业 = 选股池多一批本该剔除的票，结果会悄悄漂移
        print('[init][warn] 这些行业没匹配到 %s 板块，过滤【未生效】: %s'
              % (SW1_PREFIX, missing))
    return resolved


def _trade_dates(C, end_date, count):
    data = C.get_market_data_ex(['close'], [BENCHMARK], period='1d', end_time=end_date,
                                count=count, dividend_type='none',
                                fill_data=False, subscribe=False)
    df = data.get(BENCHMARK)
    if df is None or df.empty:
        return []
    return [str(_to_yyyymmdd(i)) for i in df.index]


def _prev_trade_date(C, today):
    dates = _trade_dates(C, today, 3)
    dates = [d for d in dates if d < today]
    return dates[-1] if dates else None


#============================== 通用小工具 ====================================
def _resolve_account():
    if ACCOUNT:
        return ACCOUNT
    try:
        return account            # QMT 在模型交易/回测时注入的全局变量
    except NameError:
        raise Exception('请在参数区填写 ACCOUNT，或在 QMT 回测界面绑定资金账号')


def _is_intraday(period):
    p = str(period).lower()
    if p in ('1d', '1w', '1mon', '1q', '1hy', '1y'):
        return False
    return True


def _bar_datetime(C):
    return dt.datetime.fromtimestamp(C.get_bar_timetag(C.barpos) / 1000.0)


def _week_key(date_str):
    d = dt.datetime.strptime(date_str, '%Y%m%d').date()
    iso = d.isocalendar()
    return '%d-%02d' % (iso[0], iso[1])


def _days_between(d1, d2):
    a = dt.datetime.strptime(str(d1), '%Y%m%d')
    b = dt.datetime.strptime(str(d2), '%Y%m%d')
    return abs((b - a).days)


def _shift_days(date_str, delta):
    d = dt.datetime.strptime(str(date_str), '%Y%m%d') + dt.timedelta(days=delta)
    return d.strftime('%Y%m%d')


def _detail(C, stock):
    if stock in g.detail_cache:
        return g.detail_cache[stock]
    try:
        d = C.get_instrumentdetail(stock) or {}
    except Exception:
        d = {}
    g.detail_cache[stock] = d
    return d


def _chunks(seq, size):
    seq = list(seq)
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


#==============================================================================
# 移植说明（聚宽 -> QMT 的对应关系与差异）
#------------------------------------------------------------------------------
# 1) 框架
#    initialize(context)            -> init(C)
#    run_daily / run_weekly         -> handlebar(C) 内按 bar 时间自行分发
#                                      （日线：一根 bar 顺序跑完当日 4 个动作；
#                                        分钟：9:31 调仓 / 14:00 涨停检查 / 14:55 复盘）
#    run_weekly(weekday=1)          -> 用 ISO 周号变化判定「每周第一个交易日」
#    order_target_value             -> passorder(23/24, 1101, ...)，自行取整 100 股
#    context.portfolio.positions    -> get_trade_detail_data(acct,'STOCK','POSITION')
#    context.portfolio.cash         -> get_trade_detail_data(...,'ACCOUNT').m_dAvailable
#    log.info                       -> print
#
# 2) 数据
#    get_all_securities             -> C.get_stock_list_in_sector('沪深A股', 时点)
#    get_security_info().start_date -> C.get_instrumentdetail()['OpenDate']
#    current_data[s].is_st / name   -> ST 板块时点成分 + 名称兜底
#    high_limit / low_limit         -> QMT 无历史涨跌停价，按 preClose × 涨跌幅规则自算
#                                      （主板 10%、创业板/科创板 20%、北交所 30%、ST 5%）
#    get_industry(sw_l1)            -> 申万行业板块的时点成分（板块名在 init 里自动解析）
#    valuation.pb_ratio             -> close×总股本 / 归母净资产（自算）
#    indicator.eps                  -> 累计归母净利润 / 总股本（只用了 >0 这一条件）
#    indicator.roe（单季）          -> 单季归母净利润 / 期末归母净资产
#                                      累计口径按季差分还原单季，一季报直接取累计
#    valuation.circulating_market_cap-> close×流通股本
#    避免未来函数                    -> 所有选股数据截到「上一交易日」；财报按公告日
#                                      过滤（无公告日时用法定披露截止日兜底）
#
# 3) 已知差异（会造成与聚宽回测曲线不完全一致，属预期）
#    - 成交价：聚宽在 9:30 开盘价成交；本版日线模式用「最新价」= 当根日线收盘价成交。
#      若要贴近开盘成交，把回测周期设为 1m（分钟模式在 9:31 下单）。
#    - 手续费/印花税/滑点在 QMT 回测面板里设置（聚宽是 set_order_cost/set_slippage）：
#      买入佣金 0.03%、卖出佣金 0.03%、印花税 0.1%（卖出）、最低佣金 5 元、滑点 0。
#    - 昨日涨停判定用 preClose 自算，除权日可能与实盘涨停价有 1 分钱级差异。
#    - 买入金额分母：聚宽用 (目标数 - 卖出后持仓数)，当「昨日涨停暂留但不在目标池」的
#      股票存在时该分母偏小会超额分配、末位买单废单；本版改用「实际待建仓数」，更稳。
#    - 卖出资金回笼：若 QMT 撮合未即时释放资金，本版用「卖出前可用 + 估算卖出金额」
#      兜底分配，避免调仓日买不进（见 weekly_adjustment）。
#    - 股本表取不到时退回 get_instrumentdetail 的最新股本，该部分标的存在轻微前视。
#    - FIN_REFRESH_DAYS 控制财报缓存；设为 7 与聚宽最接近，设大会让财报采纳滞后。
#
# 4) 上线前必做的 3 项核对（不同 QMT 版本接口命名有差异）
#    自检在「第一根 bar」打印，不是在 init（init 时数据接口可能还没就绪）。
#    a. 回测跑起来看第一根 bar 的「[自检]」输出：财务字段名是否有效。若失败，改参数区
#       FIELD_EQUITY / FIELD_NETPROF / FIELD_TOTCAP / FIELD_CIRCAP 四行。
#    b. 看 init 里「行业黑名单板块」「ST 板块」两行解析结果；若为空，先在客户端里
#       确认真实板块名（或用 C.get_sector_list() 打印）后改 INDUSTRY_FILTER。
#    c. 确认 passorder 的 orderType=1101（按股数）与 prType=5（最新价）在你的版本可用；
#       若要按金额下单可改成 1102 并直接传 value。
#==============================================================================
