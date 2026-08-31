# ============================================================================
# FROEC-TRADED + 盘中 -35% 止损 · 聚宽版（对标本地标星配置）
#
# 本文件 = JQ/小市值/FROEC-PB-CAP-HL-v3.txt 的最小改动版。
#   ★ 祖本选 v3 不是 v6：v6 把 ROE 因子换成【扣非 TTM ROE 同比增长】，
#     而本地标星用的是【5 期单季 ROE 的 increase 公式】
#         increase = 4*roe(最新) - roe(前4期之和)
#     与 v3 的 `4*df.iloc[:,4] - df.iloc[:,0..3]` 逐字一致。
#     v6 changelog 自己写着 v4(单季同比) 26.24%、输 v3 4.41pp。
#
# 改了四处，选股主干一行没动：
#   [1] 顶部加 LOCAL_PORT 关联块，由 assay/jq/check.py 断言校验
#   [2] TRADED_UNIVERSE：候选宇宙 = 决策日【实际有成交】的票（froec_traded 的定义）
#   [3] KCB_688_ONLY=False：按 68* 排除科创板，把 689 开头的 CDR 也排掉
#   [4] 成本与止损对齐本地标星那次回测
#
# ============================================================================
# 🔴🔴 移植前必读：`traded` 这条规则【样本外已否证】
#
#   本地实测 2006-01~2015-12（默认成本，10 万）：
#       A（JQ 原版口径，宇宙含停牌股）  83.06% / 回撤 51.94% / 夏普 1.91
#       B（本版 traded 宇宙）           74.20% / 回撤 54.35% / 夏普 1.76
#                                       -> 【-8.86pp】
#   B 在 8/10 年落后（2007 差 -83.8pp、2006 -23.2pp、2014 -15.0pp），
#   且 B 仓位更高（91.7% vs 89.5%）却赚更少，不是仓位造成的。
#   对照样本内 2016-2026 的 +1.87pp —— 【符号翻转】。
#
#   所以：本文件的 39.50%/40.87% 是【样本内】数字。TRADED_UNIVERSE=False
#   就退回 JQ 原版口径，值得一起跑做对照。这条不要在移植时丢掉。
# ============================================================================
#
# 用法：聚宽 -> 回测 -> 新建 -> 粘贴全文 -> 按 LOCAL_PORT['backtest'] 设区间与本金

# ---------------------------- 与本地策略的关联 ------------------------------
LOCAL_PORT = {
    'strategy':  'strategies/小市值/froec_traded.py',
    'run_id':    '20260829-170945-4926f5',
    'params':    {'stop_loss': 0.35, 'stop_intraday': 1},
    'metrics':   {'annual': 40.87, 'max_drawdown': 38.54, 'sharpe': 1.33},
    'backtest': {
        'start': '2016-01-04', 'end': '2026-08-07', 'cash': 100000,
        'benchmark': '000905.XSHG', 'freq': 'day',
    },
    'aligned': {
        'stock_num':        '10        <-> g.stock_num = 10',
        'candidate_num':    '10        <-> roe_list[:10]（先截 10 只再过滤，不补位）',
        'listed_days':      '250       <-> filter_new_stock 的 250 天',
        'limit_days':       '20        <-> g.limit_days = 20',
        'weekday':          '1         <-> run_weekly(..., weekday=1)',
        'rebal_time':       '09:30     <-> run_weekly(..., time=9:30)',
        'exit_time':        '14:00     <-> run_daily(check_limit_up, 14:00)',
        'limit_up_exit':    '1         <-> check_limit_up 开启',
        'roe 因子':          'increase = 4*roe(最新)-roe(前4期)  <-> 4*iloc[:,4]-iloc[:,0..3]',
        'pb 半区':           'floor(0.5*n)   <-> pb_list[:int(0.5*len)]',
        'roe 十分位':        'floor(0.1*n2)  <-> temp_list[:int(0.1*temp_len)]',
        'industry_control': 'True      <-> g.industry_control = True（11 个行业黑名单）',
        'paused_in_pool':   '0         <-> TRADED_UNIVERSE = True（宇宙=当日有成交）',
        'kcb_688_only':     '0         <-> KCB_688_ONLY = False（按 68* 排，含 689 CDR）',
        'stop_loss':        '0.35      <-> STOP_LOSS = 0.35',
        'stop_intraday':    '1         <-> STOP_INTRADAY = True（用当日最低价判定）',
        'stop_time':        '14:00     <-> STOP_TIME = 14:00',
    },
    'known_diff': [
        'traded 宇宙【样本外已否证】：2006-2015 输 JQ 原版口径 8.86pp，符号翻转。'
        '本文件默认 TRADED_UNIVERSE=True 是为了对标标星那次，不是因为它更好',
        '盘中止损：本地用【当日最低价】判定触发（有日线 high/low），'
        '聚宽版用 run_daily 在 STOP_TIME 取当时价判定 —— 触发时点与成交价都会有差',
        '股本生效时点：tdx 比聚宽晚 1 个交易日，边界日选股可能差一只，本地无法消除',
        '印花税分段：本地 close_tax=auto（2023-08-28 起千1->万5），'
        '聚宽要靠 run_daily 每日重设 set_order_cost 才能复现',
    ],
}
# ----------------------------------------------------------------------------

# ---------------------------- 可调参数（本文件新增） ------------------------
TRADED_UNIVERSE = True     # True = 宇宙只含【决策日有成交】的票（froec_traded）
                           # False = JQ 原版：get_all_securities()，含当日停牌股
                           # 🔴 样本外 2006-2015：True 输 False 8.86pp，见文件头
KCB_688_ONLY = False       # False = 按 68* 排除科创板（含 689 开头的 CDR）
                           # True  = JQ 原版：只排 688（漏掉 689009 九号公司）

STOP_LOSS     = 0.35       # 固定止损线，0 = 关。标星配置 0.35
STOP_INTRADAY = True       # True = 盘中触发即卖（本地用当日最低价判定）
STOP_TIME     = '14:00'    # 止损检查时点

# 成本：align_local = 与本地标星那次逐项一致
COST_MODE = 'align_local'  # 'align_local' | 'jq_v3'
if COST_MODE == 'align_local':
    COMMISSION        = 0.00025    # 万2.5（本地默认，v3 原文是万3）
    MIN_COMMISSION    = 5
    SLIPPAGE          = 0.0015
    STAMP_AUTO        = True       # 印花税分段，见 _sync_stamp_tax
else:
    COMMISSION        = 0.0003     # v3 原文
    MIN_COMMISSION    = 5
    SLIPPAGE          = 0.0015
    STAMP_AUTO        = False
CLOSE_TAX         = 0.001
STAMP_HALVED_FROM = '2023-08-28'
STAMP_TAX_AFTER   = 0.0005
# ----------------------------------------------------------------------------

# ============================================================
# FROEC-PB-CAP-HL  v3
#   基线: FROEC-PB-CAP-HL.txt / v2 (同目录)
#   改动记录:
#     [v2-1] 修复 filter_limitup_stock / filter_limitdown_stock 在 9:30 调仓时点
#            完全空转的 bug (详见 2-4 处注释)。10 年回测年化无差异, 保留。
#     [v3-1] set_order_cost 的 type 由 'fund' 改为 'stock' —— 原设置套不到股票交易上。
#     [v3-2] 滑点由 FixedSlippage(0) 改为 PriceRelatedSlippage(g.slippage)。
#   v3 只改「交易成本假设」这一类, 不触碰选股与持仓逻辑。
#
#   有意保留、不视为缺陷的设计 (经确认):
#     - 先截 10 只再过滤, 不补位  -> 被动集中是设计取向, 另开版本对比
#     - 昨日涨停股留仓挤掉目标股  -> 让利润奔跑, 有意为之
#     - 只在建仓时等权, 持有期不再平衡 -> 避免无谓换手
#     - 不做大盘择时              -> 择时参数过拟合风险高
# ============================================================

#导入函数库
from jqdata import *
from jqlib.technical_analysis import *
from jqfactor import get_factor_values
import numpy as np
import pandas as pd
import statsmodels.api as sm
import datetime as dt

#初始化函数 
def initialize(context):

    # 设定基准
    set_benchmark('000905.XSHG')
    # 用真实价格交易
    set_option('use_real_price', True)
    # 打开防未来函数
    set_option("avoid_future_data", True)
    #【滑点】按 50~100 万资金规模标定
    # 聚宽语义: PriceRelatedSlippage(x) -> 买入 +x/2、卖出 -x/2, x 是【双边】总成本。
    # 用百分比而非 FixedSlippage(绝对金额): 组合内个股价格横跨 5~50 元, 固定 0.02 元
    #   对 5 元股是 0.4%、对 50 元股只有 0.04%, 在横截面上不是同一口径。
    #
    # 这个规模下滑点需要覆盖的两块成本:
    #   1) 冲击成本 ~ 0: 10 只等权, 每只 5~10 万; 最小流通市值组日成交额通常
    #      2000 万~1 亿, 单笔占比 0.05%~0.5%, 砸不动价格。
    #   2) 买卖价差 ~ 0: 调仓在 9:30, 回测按当日【开盘价】成交, 而开盘价由 9:25
    #      集合竞价产生 —— 竞价是单一价格集中撮合,【不存在买卖价差】。只要实盘也在
    #      9:25 前把单子挂进竞价, 这块成本同样趋零。
    #      需要留余量的是「没赶上竞价、盘中吃一档价差」的部分: 小微盘股价多在
    #      5~20 元, 一档 0.01 元 = 单边 0.05%~0.2%, 折双边 0.001~0.004。
    #
    # 取 0.0015(单边 0.075%, 约半档) = 「多数走竞价、少数盘中吃一档」的混合假设。
    # 低于聚宽平台默认 0.00246 是合理的 —— 平台默认面向通用规模, 不是 50~100 万。
    #
    # 敏感度(已实测): FROEC 系列换手约 12.75 次全仓/年,
    #   故【双边滑点每 +0.1% ≈ 年化 -1.3pp】。若要看资金容量上限,
    #   按 0.001 / 0.0015 / 0.002 / 0.003 扫一遍看衰减斜率。
    g.slippage = SLIPPAGE
    set_slippage(PriceRelatedSlippage(g.slippage))
    #【v3-1】交易费率
    # 原为 type='fund', 股票交易套不上这份费率而走了平台默认值, 导致成本被低估。
    # 买入无印花税; 卖出印花税千一; 双边佣金万三; 单笔最低 5 元。
    set_order_cost(OrderCost(open_tax=0, close_tax=CLOSE_TAX,
                             open_commission=COMMISSION, close_commission=COMMISSION,
                             close_today_commission=0,
                             min_commission=MIN_COMMISSION), type='stock')
    # ★ 与本地对齐：单笔成交不超过当日成交量的 25%（本地 volume_ratio=0.25）
    if COST_MODE == 'align_local':
        try:
            set_option('order_volume_ratio', 0.25)
        except Exception as _e:
            log.warn('set_option order_volume_ratio 失败: %s' % _e)
    # 过滤order中低于error级别的日志
    log.set_level('order', 'error')
    
    #初始化全局变量
    g.stock_num = 10 #最大持仓数
    #【换手埋点】纯观测, 不影响任何交易决策
    g.total_trade_value = 0.0 #累计成交金额(买+卖)
    g.sum_total_value = 0.0 #总资产逐日累加, 用于求平均
    g.trade_days = 0 #已统计的交易日数
    #每日持仓明细开关: 长区间回测保持 False(否则日志会被截断),
    #  只在短区间诊断(如核对成交价是否等于当日开盘价)时改 True
    g.verbose_daily_log = False
    g.limit_up_list = [] #记录持仓中涨停的股票
    g.hold_list = [] #当前持仓的全部股票
    g.history_hold_list = [] #过去一段时间内持仓过的股票
    g.not_buy_again_list = [] #最近买过且涨停过的股票一段时间内不再买入
    g.limit_days = 20 #不再买入的时间段天数
    g.target_list = [] #开盘前预操作股票池
    g.industry_control = True #过滤掉不看好的行业
    g.industry_filter_list = ['钢铁I','煤炭I','石油石化I','采掘I', #重资产
    '银行I','非银金融I','金融服务I', #高负债
    '交运设备I','交通运输I','传媒I','环保I'] #盈利差
    #列表中的行业选择为主观判断结果，如果g.industry_control为False，则上述列表不影响选股
    
    # 设置交易运行时间
    run_daily(prepare_stock_list, time='9:05', reference_security='000300.XSHG') #准备预操作股票池
    run_weekly(weekly_adjustment, weekday=1, time='9:30', reference_security='000300.XSHG') #默认周一开盘调仓，收益最高
    run_daily(check_limit_up, time='14:00', reference_security='000300.XSHG') #检查持仓中的涨停股是否需要卖出
    run_daily(print_position_info, time='15:10', reference_security='000300.XSHG') #打印复盘信息
    # ★ 本文件新增：固定止损 + 印花税分段
    if STOP_LOSS > 0:
        run_daily(stop_check, time=STOP_TIME, reference_security='000300.XSHG')
    if STAMP_AUTO:
        run_daily(_sync_stamp_tax, time='8:55', reference_security='000300.XSHG')



def _sync_stamp_tax(context):
    """按当天日期重设印花税，复现本地 close_tax='auto' 的分段。

    本地 Cost.close_tax_at(d)：d < 2023-08-28 -> 0.001，否则 0.0005。
    聚宽的 set_order_cost 是一次性的，只能每日盘前重设。
    [!] 不做这一步，2023-08-28 之后本地卖出税只有聚宽的一半。froec 线年换手
        远高于红利（周频 vs 月频），这个差比红利那边更贵。
    """
    d = str(context.current_dt.date())
    tax = STAMP_TAX_AFTER if d >= STAMP_HALVED_FROM else CLOSE_TAX
    if getattr(g, '_stamp_now', None) == tax:
        return
    g._stamp_now = tax
    set_order_cost(OrderCost(open_tax=0, close_tax=tax,
                             open_commission=COMMISSION, close_commission=COMMISSION,
                             close_today_commission=0,
                             min_commission=MIN_COMMISSION), type='stock')
    log.info('[cost] %s 起印花税 -> %.4f' % (d, tax))


def stop_check(context):
    """固定止损：相对【建仓成本】跌破 STOP_LOSS 就清仓。

    [!] 与本地的差异（写在 LOCAL_PORT['known_diff'] 里）：
        本地有日线 high/low，STOP_INTRADAY=1 时用【当日最低价】判定触发，
        成交价按触发价算。聚宽这里是在 STOP_TIME 取【当时价】判定 ——
        · 触发时点不同：本地看的是全天最低，这里只看 14:00 那一刻
        · 所以本地触发次数会 >= 这里，且成交价更差（按更低的价成交）
        要更接近本地，可以把 STOP_TIME 改成多个时点分别 run_daily，
        或用 get_price(frequency='1m') 取当日至今的最低价 —— 见下方 INTRADAY_MIN。
    """
    if STOP_LOSS <= 0:
        return
    for s in list(context.portfolio.positions):
        pos = context.portfolio.positions[s]
        cost = pos.avg_cost
        if not cost or cost <= 0:
            continue
        px = pos.price
        if STOP_INTRADAY:
            # 用当日至今的最低价判定，比只看 STOP_TIME 那一刻更接近本地口径
            try:
                m = get_price(s, end_date=context.current_dt, frequency='1m',
                              count=240, fields=['low'], panel=False,
                              skip_paused=False)
                if m is not None and len(m):
                    px = min(px, float(m['low'].min()))
            except Exception:
                pass
        if px / cost - 1.0 <= -STOP_LOSS:
            log.info('[STOP] %s 成本 %.3f 现价/最低 %.3f 跌幅 %.1f%% -> 清仓'
                     % (s, cost, px, (px / cost - 1.0) * 100))
            order_target_value(s, 0)


#1-1 选股模块
def get_stock_list(context):
    yesterday = str(context.previous_date)
    initial_list = get_all_securities(date=context.previous_date).index.tolist()
    # ★ froec_traded 的定义：候选宇宙 = 决策日【实际有成交】的票。
    #   JQ 原版用 get_all_securities()，含当日停牌股 —— 停牌股会参与 PB 半区与
    #   ROE 十分位的【切点计算】，还能先占掉 [:10] 的名额再被 filter_paused_stock
    #   删掉，导致目标池常常只有 8~9 只。
    #   这条规则【不含前视】：某票当日有没有成交，在决策时点（前一交易日收盘后）
    #   是完全已知的。
    #   🔴 但它【样本外已否证】：2006-2015 输原版口径 8.86pp（见文件头）。
    if TRADED_UNIVERSE:
        vol = get_price(initial_list, end_date=context.previous_date, frequency='1d',
                        count=1, fields=['volume'], panel=False,
                        skip_paused=False, fill_paused=False)
        traded = set(vol[vol['volume'] > 0]['code'])
        initial_list = [x for x in initial_list if x in traded]
    initial_list = filter_new_stock(context,initial_list)
    initial_list = filter_kcb_stock(context, initial_list)
    initial_list = filter_st_stock(initial_list)
    #PB过滤
    q = query(valuation.code, valuation.pb_ratio, indicator.eps).filter(valuation.code.in_(initial_list)).order_by(valuation.pb_ratio.asc())
    df = get_fundamentals(q)
    df = df[df['eps']>0]
    df = df[df['pb_ratio']>0]
    pb_list = list(df.code)[:int(0.5*len(df.code))]
    #ROEC过滤
    #因为get_history_fundamentals有返回数据限制最多5000行，需要把pb_list拆分后查询再组合
    interval = 1000 #count=5时，一组最多1000个，组数向下取整
    pb_len = len(pb_list)
    if pb_len <= interval:
        df = get_history_fundamentals(pb_list, fields=[indicator.code, indicator.roe], watch_date=yesterday, count=5, interval='1q')
    else:
        df_num = pb_len // interval
        df = get_history_fundamentals(pb_list[:interval], fields=[indicator.code, indicator.roe], watch_date=yesterday, count=5, interval='1q')
        for i in range(df_num):
            dfi = get_history_fundamentals(pb_list[interval*(i+1):min(pb_len,interval*(i+2))], fields=[indicator.code, indicator.roe], watch_date=yesterday, count=5, interval='1q')
            df = df.append(dfi)
    df = df.groupby('code').apply(lambda x:x.reset_index()).roe.unstack()
    df['increase'] = 4*df.iloc[:,4] - df.iloc[:,0] - df.iloc[:,1] - df.iloc[:,2] - df.iloc[:,3]
    df.dropna(inplace=True)
    df.sort_values(by='increase',ascending=False, inplace=True)
    temp_list = list(df.index)
    temp_len = len(temp_list)
    roe_list = temp_list[:int(0.1*temp_len)]
    #行业过滤
    if g.industry_control == True:
        industry_df = get_stock_industry(roe_list, yesterday)
        ROE_list = filter_industry(industry_df, g.industry_filter_list)
    else:
        ROE_list = roe_list
    #市值排序
    q = query(valuation.code,valuation.circulating_market_cap).filter(valuation.code.in_(ROE_list)).order_by(valuation.circulating_market_cap.asc())
    df = get_fundamentals(q)
    ROEC_list = list(df.code)

    return ROEC_list


#1-2 行业过滤函数
def get_stock_industry(securities, watch_date, level='sw_l1', method='industry_name'): 
    industry_dict = get_industry(securities, watch_date)
    industry_ser = pd.Series({k: v.get(level, {method: np.nan})[method] for k, v in industry_dict.items()})
    industry_df = industry_ser.to_frame('industry')
    return industry_df

def filter_industry(industry_df, select_industry, level='sw_l1', method='industry_name'):
    filter_df = industry_df.query('industry != @select_industry')
    filter_list = filter_df.index.tolist()
    return filter_list


#1-3 准备股票池
def prepare_stock_list(context):
    #1...2
    #获取已持有列表
    g.hold_list= []
    for position in list(context.portfolio.positions.values()):
        stock = position.security
        g.hold_list.append(stock)
    #获取最近一段时间持有过的股票列表
    g.history_hold_list.append(g.hold_list)
    if len(g.history_hold_list) >= g.limit_days:
        g.history_hold_list = g.history_hold_list[-g.limit_days:]
    temp_set = set()
    for hold_list in g.history_hold_list:
        for stock in hold_list:
            temp_set.add(stock)
    g.not_buy_again_list = list(temp_set)
    #获取昨日涨停列表
    if g.hold_list != []:
        df = get_price(g.hold_list, end_date=context.previous_date, frequency='daily', fields=['close','high_limit'], count=1, panel=False, fill_paused=False)
        df = df[df['close'] == df['high_limit']]
        g.high_limit_list = list(df.code)
    else:
        g.high_limit_list = []


#1-4 整体调整持仓
def weekly_adjustment(context):
    #1 #获取应买入列表 
    g.target_list = get_stock_list(context)[:10] #2
    g.target_list = filter_paused_stock(g.target_list)
    g.target_list = filter_limitup_stock(context, g.target_list)
    g.target_list = filter_limitdown_stock(context, g.target_list)
    #过滤最近买过且涨停过的股票
    recent_limit_up_list = get_recent_limit_up_stock(context, g.target_list, g.limit_days)
    black_list = list(set(g.not_buy_again_list).intersection(set(recent_limit_up_list)))
    g.target_list = [stock for stock in g.target_list if stock not in black_list]
    #截取不超过最大持仓数的股票量
    g.target_list = g.target_list[:min(g.stock_num, len(g.target_list))]
    #调仓卖出
    for stock in g.hold_list:
        if (stock not in g.target_list) and (stock not in g.high_limit_list):
            log.info("卖出[%s]" % (stock))
            position = context.portfolio.positions[stock]
            close_position(position)
        else:
            log.info("已持有[%s]" % (stock))
    #调仓买入
    position_count = len(context.portfolio.positions)
    target_num = len(g.target_list)
    if target_num > position_count:
        value = context.portfolio.cash / (target_num - position_count)
        for stock in g.target_list:
            if context.portfolio.positions[stock].total_amount == 0:
                if open_position(stock, value):
                    if len(context.portfolio.positions) == target_num:
                        break


#1-5 调整昨日涨停股票
def check_limit_up(context):
    now_time = context.current_dt
    if g.high_limit_list != []:
        #对昨日涨停股票观察到尾盘如不涨停则提前卖出，如果涨停即使不在应买入列表仍暂时持有
        for stock in g.high_limit_list:
            current_data = get_price(stock, end_date=now_time, frequency='1m', fields=['close','high_limit'], skip_paused=False, fq='pre', count=1, panel=False, fill_paused=True)
            if current_data.iloc[0,0] < current_data.iloc[0,1]:
                log.info("[%s]涨停打开，卖出" % (stock))
                position = context.portfolio.positions[stock]
                close_position(position)
            else:
                log.info("[%s]涨停，继续持有" % (stock))



#2-1 过滤停牌股票
def filter_paused_stock(stock_list):
	current_data = get_current_data()
	return [stock for stock in stock_list if not current_data[stock].paused]

#2-2 过滤ST及其他具有退市标签的股票
def filter_st_stock(stock_list):
	current_data = get_current_data()
	return [stock for stock in stock_list
			if not current_data[stock].is_st
			and 'ST' not in current_data[stock].name
			and '*' not in current_data[stock].name
			and '退' not in current_data[stock].name]

#2-3 获取最近N个交易日内有涨停的股票
def get_recent_limit_up_stock(context, stock_list, recent_days):
    stat_date = context.previous_date
    new_list = []
    for stock in stock_list:
        df = get_price(stock, end_date=stat_date, frequency='daily', fields=['close','high_limit'], count=recent_days, panel=False, fill_paused=False)
        df = df[df['close'] == df['high_limit']]
        if len(df) > 0:
            new_list.append(stock)
    return new_list

#2-4 过滤开盘即涨停的股票
#【v2-1 修复】原实现: last_prices = history(1, unit='1m', field='close', ...)
#   9:30 调仓时点今日第一根分钟线(09:31)尚未收盘, history 只返回已完成的 bar,
#   因此取到的是【上一交易日 15:00】的收盘价; 而 current_data.high_limit 是按昨收
#   算出的【今日】涨停价, 于是 昨收 < 昨收*1.1 恒成立 —— 该过滤器从不排除任何股票。
#   后果: 开盘一字涨停(实盘买不进)的股票照样下单, 回测以涨停价成交产生虚假收益。
#   修正: 改用 day_open(今日开盘价)。开盘价由 9:25 集合竞价确定, 在 9:30 已是既定
#   事实, 不构成未来数据; 集合竞价成交不单独成分钟线, 而是并入首根分钟线的 open,
#   所以 9:30 只能从 day_open 拿, 拿不到分钟线。
#   验证方法: 回测日志中若出现 "开盘涨停，本次不买入", 即证明过滤器已生效。
def filter_limitup_stock(context, stock_list):
	current_data = get_current_data()
	filtered_list = []
	for stock in stock_list:
		#已持仓的无需新买入, 涨停与否不影响, 直接放行
		if stock in context.portfolio.positions:
			filtered_list.append(stock)
			continue
		open_price = current_data[stock].day_open
		#无开盘价(停牌/数据异常)视为不可买入
		if open_price <= 0:
			log.info("[%s] 无开盘价，本次不买入" % stock)
			continue
		if open_price < current_data[stock].high_limit:
			filtered_list.append(stock)
		else:
			log.info("[%s] 开盘涨停，本次不买入" % stock)
	return filtered_list

#2-5 过滤开盘即跌停的股票 (与 2-4 同一个取值 bug, 一并修复)
def filter_limitdown_stock(context, stock_list):
	current_data = get_current_data()
	filtered_list = []
	for stock in stock_list:
		if stock in context.portfolio.positions:
			filtered_list.append(stock)
			continue
		open_price = current_data[stock].day_open
		if open_price <= 0:
			continue
		if open_price > current_data[stock].low_limit:
			filtered_list.append(stock)
		else:
			log.info("[%s] 开盘跌停，本次不买入" % stock)
	return filtered_list

#2-6 过滤科创板
def filter_kcb_stock(context, stock_list):
    # ★ JQ 原版写的是 stock[0:3] != '688'，漏掉 689 开头的科创板存托凭证
    #   —— 全样本里就是 689009.XSHG（九号公司）。这一只值 1.67pp 年化，
    #   不是因为它本身赚赔多少，而是它让 base 从 3434 变 3433，
    #   floor(0.5*n) 与 floor(0.1*n2) 两级边界同时位移，换掉了边界上的票。
    if KCB_688_ONLY:
        return [stock for stock in stock_list if stock[0:3] != '688']
    return [stock for stock in stock_list if stock[0:2] != '68']

#2-7 过滤次新股
def filter_new_stock(context,stock_list):
    yesterday = context.previous_date
    return [stock for stock in stock_list if not yesterday - get_security_info(stock).start_date < datetime.timedelta(days=250)]

#3-1 交易模块-自定义下单
def order_target_value_(security, value):
	if value == 0:
		log.debug("Selling out %s" % (security))
	else:
		log.debug("Order %s to value %f" % (security, value))
	return order_target_value(security, value)

#3-2 交易模块-开仓
def open_position(security, value):
	order = order_target_value_(security, value)
	if order != None and order.filled > 0:
		return True
	return False

#3-3 交易模块-平仓
def close_position(position):
	security = position.security
	order = order_target_value_(security, 0)  # 可能会因停牌失败
	if order != None:
		if order.status == OrderStatus.held and order.filled == order.amount:
			return True
	return False



#4-0 换手率统计（纯观测，不参与任何交易决策）
# 为什么要直接测而不是从成本拖累反推: 反推需要知道「每次往返的成本」,
# 而滑点与费率同时改动时无法拆开(例: 年化拖累 2.27% 既可能是
# 15.1 次往返 x 0.15% 纯滑点, 也可能是 7.3 次 x 0.31% 滑点+费率, 差一倍)。
# 直接累加成交额则不存在这个歧义。
#
# 定义: 一次「全仓往返」= 清空持仓 + 重新建满, 成交额 = 2 x 总资产。
#   年化全仓往返次数 = 累计成交额 / (2 x 平均总资产) / 年数
# 用法: 该次数 x 双边滑点 = 年化成本拖累。例如 15 次 x 0.15% = 2.25%/年,
#   即【双边滑点每 0.1% 约拖累年化 1.5pp】—— 这个斜率是降换手优化的定价依据。
#
# 输出方式: 主要靠 record() 画到【回测结果图】上, 不占日志。
#   聚宽日志有条数上限, 长区间回测下逐日 log 会被截断而看不到,
#   所以只在每满一年补打一行日志做备份, 主读数看图上的曲线。
#   图上两条曲线:
#     round_trips  = 年化全仓往返次数(累计到当日的估计值, 越到后期越稳定)
#     cost_per_10bp = 双边滑点每 0.1% 对应的年化拖累(pp) = round_trips * 0.1
def update_turnover_stat(context):
    for trade in get_trades().values():
        g.total_trade_value += abs(trade.price * trade.amount)
    g.trade_days += 1
    g.sum_total_value += context.portfolio.total_value
    #前 60 个交易日样本太少, 估计值会剧烈抖动, 不输出
    if g.trade_days < 60:
        return
    avg_value = g.sum_total_value / g.trade_days
    years = g.trade_days / 244.0
    if avg_value <= 0 or years <= 0:
        return
    round_trips = g.total_trade_value / (2.0 * avg_value) / years
    #画到回测图上(不受日志条数限制, 这是主要读数方式)
    record(round_trips=round_trips, cost_per_10bp=round_trips * 0.1)
    #每满一年补一行日志做备份
    if g.trade_days % 244 == 0:
        log.info("换手统计: %.2f年 | 累计成交额 %.0f | 平均总资产 %.0f | "
                 "年化全仓往返 %.2f 次 | 双边滑点每0.1%%约拖累年化 %.2f pp"
                 % (years, g.total_trade_value, avg_value, round_trips, round_trips * 0.1))


#4-1 打印每日持仓信息
def print_position_info(context):
    #换手埋点(纯观测), 必须每日执行
    update_turnover_stat(context)
    #【日志预算】每日持仓明细在长区间回测下会撑爆聚宽的日志条数上限
    #  (10 个持仓 x 6 行 + 成交记录, 逐日累积十万行以上, 前面的内容会被截断)。
    #  长区间跑默认关闭; 需要核对成交价/成交明细时, 把 g.verbose_daily_log 改 True
    #  并把回测区间缩到几周即可。
    if not g.verbose_daily_log:
        return
    #打印当天成交记录
    trades = get_trades()
    for _trade in trades.values():
        print('成交记录：'+str(_trade))
    #打印账户信息
    for position in list(context.portfolio.positions.values()):
        securities=position.security
        cost=position.avg_cost
        price=position.price
        ret=100*(price/cost-1)
        value=position.value
        amount=position.total_amount    
        print('代码:{}'.format(securities))
        print('成本价:{}'.format(format(cost,'.2f')))
        print('现价:{}'.format(price))
        print('收益率:{}%'.format(format(ret,'.2f')))
        print('持仓(股):{}'.format(amount))
        print('市值:{}'.format(format(value,'.2f')))
        print('———————————————————————————————————')
    print('———————————————————————————————————————分割线————————————————————————————————————————')