# ============================================================================
# 红利指数增强 · 聚宽版（对标本地标星配置）
#
# 本文件 = JQ/红利/红利指数增强_v7.txt 的【最小改动版】，只改两处：
#   [1] 顶部加 LOCAL_PORT 关联块（下方），由 assay/jq/check.py 断言校验
#   [2] 成本口径从「JQ 平台默认」改为「与本地标星那次回测逐项一致」
#       —— 否则两边数字不可比，差出来的是费率不是策略
# 选股逻辑一行没动，逐条比对过（见 LOCAL_PORT['aligned']）。
#
# 用法：聚宽 -> 策略研究/回测 -> 新建 -> 粘贴全文 -> 按 LOCAL_PORT['backtest'] 设区间与本金
# ============================================================================

# ---------------------------- 与本地策略的关联 ------------------------------
# ★ 关联写成注释是留不住的：本地策略一改、标星一换，注释还在那儿，人却不会去改。
#   所以这是【断言】—— assay/jq/check.py 会核对 run_id 在不在归档、参数对不对得上、
#   基线指标与 stats.json 是否吻合。selftest 里有一条用例跑它。
LOCAL_PORT = {
    'strategy':  'strategies/红利/红利指数增强.py',
    'run_id':    '20260828-210010-adcde3',
    'params':    {'div_method': 'fiscal_year'},      # 其余全部走本地默认值
    'metrics':   {'annual': 20.07, 'max_drawdown': 17.95, 'sharpe': 1.30},
    'backtest': {
        'start': '2016-01-04', 'end': '2026-06-30', 'cash': 1000000,
        'benchmark': '000015.XSHG', 'freq': 'day',
    },
    # 选股参数逐项对齐（本地默认值 <-> 本文件常量），核对于 2026-09-01
    'aligned': {
        'num_a,num_b':   '10,5        <-> TARGET_NUM = [10, 5]',
        'backup_a,b':    '5,5         <-> BACKUP_NUM = [5, 5]',
        'div_min':       '0.03        <-> 股息率过滤 threshold = 0.03',
        'div_top_pct':   '0.10        <-> 股息率过滤 p2 = 0.10',
        'beta_pct':      '1.0         <-> B-3 已删（v7 无 beta 分位截断）',
        'monthday':      '1           <-> REBALANCE_DAY = 1',
        'reentry':       '1           <-> ENABLE_REENTRY = True',
        'limit_up_exit': '1           <-> TIME_LIMIT_UP = 10:00',
        'div_method':    'fiscal_year <-> DIV_METHOD = fiscal_year',
        'div_lookback':  '800         <-> DIV_LOOKBACK_DAYS = 800',
        'pe':            '5.0~50.0    <-> valuation.pe_ratio.between(5, 50)',
        'roe(inc_return)': '0.05~1.00 <-> indicator.inc_return.between(5, 100)',
        'rev':           '0.05~1.00   <-> inc_total_revenue_year_on_year.between(5, 100)',
        'np':            '0.10~1.00   <-> inc_net_profit_year_on_year.between(10, 100)',
        'b_mode':        'abs         <-> 绝对阈值（非分位）',
    },
    # 已知的、无法消除的实现差异 —— 对账时要把它们算进容差
    'known_diff': [
        'beta：本地自算（std/beta_daily.parquet，沪深300 252 日滚动回归），'
        'JQ 用 get_factor_values。对齐区间本金后 红利低波 本地 11.32% vs JQ 11.27% = +0.05pp',
        '炸板离场：JQ 在 10:00 取那一刻的分钟价，本地只有日线，'
        '用「今日是否仍收在涨停价」代替（尾盘决策）。小市值线实测这个代理值 0.3~1.0pp',
        '股本生效时点：tdx 比聚宽晚 1 个交易日，边界日选股可能差一只，本地无法消除',
    ],
}
# ----------------------------------------------------------------------------

# ============================================================================
# 红利指数增强 v2                                              2026-08-17
# 基线: 红利指数增强_再入场.txt (Run D) + target_num=[10,5]
#
# 本版改动（对应 分析结论.md §六 的 A-2 / A-3，均为正确性修复，不是调参）：
#
#   A-2  get_factor_filter_list 改用 Series 自带 index 对齐 code 与 score
#        原实现 df['code']=stock_list; df['score']=score_list 靠【位置】对齐，
#        假设 get_factor_values 返回列序 == 输入列表序。一旦 JQ 改变顺序，
#        beta 会全部张冠李戴且不报错（静默错配）。
#
#   A-3  显式声明 set_order_cost / set_slippage，消除对平台默认值的依赖。
#        本文件的默认取值 = 从原始交易记录反推出的 JQ 默认值（已 4/4 验证）：
#            fee = max(成交额 × 0.0003, 5) + 卖出时 成交额 × 0.001
#        滑点用 JQ 文档默认 PriceRelatedSlippage(0.00246)。
#
#   B-3  删掉 Sleeve A 的 beta 分位截断（原 p2=0.50）
#        原实现: get_factor_filter_list(..., 'beta', True, 0.00, 0.50) 先砍掉 beta 高的
#        一半，再取前 N 只。
#        · target_num[0]=5  时：池子 >=10 只就不 binding —— 是个【死参数】
#        · target_num[0]=10 时：池子需 >=20 只才不 binding，否则给不满 -> 欠配
#          （v3 实测早期持仓 2016:12.6 / 2017:13.6 / 2018:14.7，低于目标 15）
#        该截断对"假低 beta（Dimson 效应/流动性差）"毫无防护作用 —— 它是
#        "从最低的一半里选"，不是"排除最低的那些"。池子够大时完全不起作用，
#        池子小时只造成欠配。
#        本版把 p1/p2 两个参数【从函数签名里删除】，改名 get_factor_sorted_list，
#        只做排序。自由度 -2。
#
#   A-5  买入资金分配：由"盘前一次性预估"改为"卖出完成后逐笔按实际可用现金分配"
#        原实现在 09:01 用【昨收市值】预估卖出所得，一次性算好全部买单股数：
#            value = available_cash + Σ(sell_list 昨收市值)
#            amount = 100 * int(1.05*value / price / 100)      # 还故意超配 5%
#        四个漏损叠加 -> 循环里后面的买单没钱，直接委托失败：
#          (1) 用昨收市值预估，实际卖价可能更低
#          (2) 卖出要扣手续费(约0.13%)
#          (3) 涨停股卖不掉(trade 里 if last_price < high_limit 跳过)，钱没到账
#          (4) 1.05*value 故意超配 5%，成交价高于昨收就超支
#        实例(v4 日志): 2017-05-08 603868 需 54.88x100=5488 元，
#                       实际可用只够 5 股(约274元) -> "开仓数量不能小于100" 委托失败
#        更糟: target_list = list(set(...)) 的迭代顺序【不保证】，
#              所以哪几只票被牺牲是随机的 —— 这是一个额外的路径噪声源。
#
#        本版改法:
#          · target_list 改为确定性去重保序(HLDB 优先)，消除 set 的随机性
#          · buy_df 只预备 name/price，【不再预先算 amount】
#          · trade() 中【先完成全部卖出】，再逐笔用当时的 available_cash 分配:
#                per = available_cash / 剩余待买只数
#                amt = 100 * int(per / 限价 / 100)
#            按【限价】(昨收x1.05)做预算 -> 天然不可能透支；
#            自适应: 前面买少了后面自动多买，前面买多了后面自动少买
#          · 去掉 1.05 超配
#          · 新增 [BUY-SKIP] 日志与计数器，可量化本修复的效果
#
#   A-6  分红记录去重 + 年度锚定改用日期（v7，修 A-1 的实现缺陷）
#        (1) 去重: 同一 (code, report_date, bonus_type) 只留流程最靠后的一条
#            —— 原实现重复相加，虚高集中在中期分红(2024后的银行/央企)
#        (2) 剔除 bonus_cancel_pub_date 非空、plan_progress 为终止/取消分红的记录
#        (3) 年度锚定由 bonus_type=='年度分红' 改为 report_date 月份==12
#            —— 不依赖文本标签，顺带救回 36 只(0.83%)只发季度/特别分红而被
#               静默剔除的股票
#        可见性仍是 board_plan_pub_date：年度分红一公告即采用，与市场同步，无滞后。
#        唯一的真实滞后：中期/季度分红要等该 FY 年度分红公告才计入（最长约 7 个月）,
#        这是"按完整会计年度"定义的固有代价，待用 fiscal_year_annualized 变体量化。
#
#   同时把 target_num 定为 [10,5]（15 只），这是 分析结论.md §三 的结论：
#        调仓日极差 12.10pp → 3.32pp，超额回撤均值改善 8.80pp，
#        IR 均值 +0.117，组合年换手 −25%，代价仅 0.93pp 年化均值。
#
# ---------------------------------------------------------------------------
# 【第一次跑：验收用，不是敏感性测试】
#   保持 SLIPPAGE = 0.00246（JQ 默认）跑第 1 个交易日，
#   结果应当【精确复现】此前 [10,5] 第 1 天的 409.31% / 年化 17.09%。
#
#   ✅ 完全一致  → A-2 无影响（JQ 确实保序），此前所有回测的 beta 排序有效；
#                  A-3 的费率取值正确。可以放心继续后续改动。
#   ⚠️ 不一致    → 说明 get_factor_values 并非保序，
#                  【此前所有依赖 beta 排序的结论都需重新审视】。先停下来查。
#
# 【第二次起：滑点敏感性】
#   只改 SLIPPAGE 一个数：0.005 → 0.01，各跑第 1 天 + 第 5 天。
#   判据看「均值 / 极差」两个数，不看峰值收益。
# ---------------------------------------------------------------------------
#
# 【刻意未改】留给后续步骤，避免一次动太多变量：
#   A-1 分红滚动 365 天窗口（重复计数/漏计）
#   A-4 月度调仓 buy_list 为空时的现金闲置
#   B-1 Sleeve B 的 8 个 between 边界
#   B-2 股息率 top10% 与 >3% 的冗余约束
#   B-3 beta p2=0.50 截断（target_num[0]=10 后会导致欠配）
#   B-4 创业板 *1.20 死分支
# ============================================================================

import pandas as pd
from jqdata import *
from jqfactor import get_factor_values


# ============================================================================
#                              配 置 区
#   所有可调参数集中于此。括号内为已实测的敏感性，详见 分析结论.md §二
# ============================================================================

# ---------------- 组合 ----------------
TARGET_NUM = [10, 5]      # [红利低波, 红利价值] 各取几只，并集去重后约 15 只
                          # 🔴 高敏感：[5,3]→[10,5] 使调仓日极差 12.10pp→3.32pp、
                          #    超额回撤均值改善 8.80pp、IR 均值 +0.117、年换手 −25%，
                          #    代价仅 0.93pp 年化均值。不要改回 [5,3]
BACKUP_NUM = [5, 5]       # 各留几只备选，供炸板后再入场替补

# ---------------- 调仓与执行时点 ----------------
REBALANCE_DAY = 1         # 每月第几个交易日调仓
                          # 🔴 全策略最敏感的参数
                          #    第1/2/3/5/10/15 天 → 22.41/18.19/12.30/10.31/12.54/17.07%
                          #    极差 12.10pp（8只）；15只后收窄到 3.32pp
                          #    第1天胜出 9/11 年(p=0.033)，实盘用第 1 或第 2 天
                          #    进阶：可拆两天各半仓，降低单点依赖

TIME_PREPARE  = '09:00'   # 每日盘前：统计昨日涨停的持仓
TIME_PICK     = '09:01'   # 调仓日：选股
TIME_TRADE    = '09:30'   # 调仓日：下单
                          # ✅ 不敏感：09:30/09:31/09:35 极差仅 0.62pp（< 噪声底 0.76pp）
                          #    实盘可开盘前挂好限价单
TIME_LIMIT_UP = '10:00'   # 每日：炸板离场检查 + 再入场
                          # ✅ 不敏感：09:35~14:55 跨 5 小时 20 分，极差 0.76pp = 噪声底
                          #    覆盖开盘与尾盘两个波动尖端，完全非单调
                          #    平仓次数 481~483（决策一致性 99.7%）
                          #    实盘建议改 '14:30'：每天收盘前看一眼即可，手工可行，
                          #    代价仅 0.4pp（噪声内）。为与历史回测可比，此处保持 10:00

# ---------------- 股息率算法（A-1 修复） ----------------
DIV_METHOD = 'fiscal_year'  # 'fiscal_year' = 按分红归属会计年度汇总（新，推荐）
                            # 'rolling365'  = 原实现：按股权登记日滚动 365 天求和
                            #
                            # 原实现两个缺陷：
                            #  ① 实施日在年度间漂移几周 → 窗口内出现两次或零次年度分红
                            #  ② 公司从年派改半年派时（2024 年后大量银行/央企如此）
                            #     窗口混入不同归属期的分红
                            # 实测工商银行：2025-06-01 虚高 +46%，2026-06-01 虚高 +53%
                            # （真实年度分红 3.035→3.064→3.080→3.103 其实很稳定）
                            #
                            # 新算法：取 board_plan_pub_date <= T 的记录，按 report_date
                            # 分会计年度，找最新的【年度分红已公告】的 FY，汇总该 FY 全部
                            # 分红（年度+中期+季度）。用预案公告日判可见性 —— 公开信息，
                            # 非未来函数，滞后仅约 3 个月（若改用登记日判可见性会滞后 19 个月）

DIV_LOOKBACK_DAYS = 800     # 分红记录回溯天数（约 26 个月）
                            # 需覆盖"最新已公告年度分红"最坏情况：T 在 1~3 月时，
                            # 最新已公告的是 T-2 年度，其中期分红公告更早
DIV_CHUNK = 500             # 单次 finance.run_query 的股票数
                            # v7 下调 600->500：去重前一只股票 2.2 年内可能有
                            # 多条阶段记录（实测全市场约 5.3 行/股/2.2年），
                            # 500x5.3=2650 对 4000 上限留出余量
                            # 该接口最多返回 4000 行；一只股票 2.2 年内可有 2~5 条
                            # 记录，600×5=3000 留出余量。命中上限会打 warn

# ---------------- 分红记录去重（v7 / A-6）----------------
# 实测问题：plan_progress 有三种状态，而「董事会预案 / 股东大会预案」状态的
#   20774 条记录【100% 没有股权登记日】，「实施方案」16387 条 100% 有。
#   交叉验证：实施方案里中期分红 1704 条 ≈ tdx.db 统计的实际实施数(~1700)，
#   而董事会预案里中期分红 14194 条 = 实际事件数的 8.3 倍 -> 记录层面有重复。
#   原实现只按 board_plan_pub_date 取记录、不去重 -> 同一事件被重复相加 -> 股息率虚高，
#   且 99.8% 集中在中期分红，即 2024 年后的银行/央企 —— 正是策略重仓。
#
# 修法：同一 (code, report_date, bonus_type) 只保留流程最靠后的一条。
#   可见性【仍用 board_plan_pub_date】—— 董事会预案是公开信息、非未来函数，
#   且年度分红一经公告即被采用，与市场同步，【没有滞后】。
STAGE_ORDER   = {'实施方案': 3, '股东大会预案': 2, '董事会预案': 1, '公司预案': 0}
CANCEL_STATES = ['终止', '取消分红']

# ---------------- 行为开关 ----------------
ENABLE_REENTRY = True     # 炸板卖出后立即用释放现金补仓（值 +0.92pp，t=2.70）
                          # 关掉炸板离场本身则 −3.04pp，见 分析结论.md §一

# ---------------- 基准 ----------------
BENCHMARK = '000015.XSHG' # 仅供聚宽页面展示（价格指数，不含股息）
                          # 真实全收益基准用 510880 后复权离线算，年化 5.72%

# ---------------- A-3 成本参数 ----------------
# VERIFY_MODE = True  : 用 JQ 平台默认费率跑，用来验收 A-2（见文件头）
#                       此时第 1 交易日结果应【精确复现】409.31% / 年化 17.09%
# VERIFY_MODE = False : 用账户真实费率（万0.88，无最低）+ 指定滑点，做实盘口径回测
# ★ 本文件新增第三档 'align_local'：与本地标星那次回测【逐项一致】。
#   要和本地对账就用它 —— 成本不一致的话，差出来的是费率不是策略。
COST_MODE = 'align_local'      # 'align_local' | 'jq_default' | 'account_real'

if COST_MODE == 'align_local':
    # 本地 run 20260828-210010-adcde3 的 cost 块，逐字段搬过来：
    #   slippage 0.0015 / commission 0.00025 / min_commission 5.0
    #   close_tax 'auto'（2023-08-28 起 千1 -> 万5）/ open_tax 0 / dividend_tax True
    #   volume_ratio 0.25
    COMMISSION     = 0.00025   # 万2.5（本地默认，非 JQ 的万3）
    MIN_COMMISSION = 5
    SLIPPAGE       = 0.0015    # PriceRelatedSlippage 语义：买 +0.075%、卖 -0.075%
    STAMP_AUTO     = True      # 印花税分段，见 _sync_stamp_tax
elif COST_MODE == 'jq_default':
    COMMISSION     = 0.0003    # JQ 默认：万3 双边
    MIN_COMMISSION = 5         # JQ 默认：最低 5 元
    SLIPPAGE       = 0.00246   # JQ 默认
    STAMP_AUTO     = False
else:                          # account_real
    COMMISSION     = 0.000088  # 账户实际：万0.88
    MIN_COMMISSION = 0         # 无最低
    SLIPPAGE       = 0.002     # 见下方滑点档位说明
    STAMP_AUTO     = False

CLOSE_TAX = 0.001              # 印花税千1，仅卖出
# ★ 2023-08-28 起证券交易印花税减半为万5。本地 close_tax='auto' 就是分段的，
#   JQ 侧要靠每日重设 set_order_cost 才能复现（见 _sync_stamp_tax）。
#   不做这一步的话，2023-08-28 之后本地税率只有 JQ 的一半，卖出成本差一倍。
STAMP_HALVED_FROM = '2023-08-28'
STAMP_TAX_AFTER   = 0.0005

# 滑点档位（PriceRelatedSlippage(x) 表示单边偏离 x/2）
#   0.001  单边0.05%  过于乐观，仅作下界参考
#   0.002  单边0.10%  基线。持仓中位市值 446 亿、单笔占成交额 P90 仅 0.076%，
#                     成本主要来自买卖价差而非冲击，这个档位对大盘高股息股合理
#   0.004  单边0.20%  保守。低价银行股(3-8元)一个 tick 就是 0.13%~0.33%，
#                     若成交多在对手价，实际接近这一档
#   0.008  单边0.40%  压力测试
# 年双边换手约 8.4×，滑点年成本 ≈ 4.2 × SLIPPAGE
#   0.002 → 0.84%/年    0.004 → 1.68%/年    0.008 → 3.36%/年
# ✅ 成本不是主要风险：万3→万0.88 约 +0.18pp，滑点 0.00246→0.004 约 −0.65pp，
#    均在噪声底 0.76pp 之内。只有 0.008 那档才会明显吃掉收益
# ============================================================================


def _sync_stamp_tax(context):
    """按当天日期重设印花税，复现本地 close_tax='auto' 的分段。

    本地 Cost.close_tax_at(d)：d < 2023-08-28 -> 0.001，否则 0.0005。
    JQ 的 set_order_cost 是一次性的，所以只能每日盘前重设一次。
    [!] 不做这一步，2023-08-28 之后本地卖出税只有 JQ 的一半 —— 换手 8.4 次/年，
        千1 vs 万5 的差 = 约 0.42%/年，正好落在噪声底附近，容易被误读成策略差异。
    """
    d = str(context.current_dt.date())
    tax = STAMP_TAX_AFTER if d >= STAMP_HALVED_FROM else CLOSE_TAX
    if getattr(g, '_stamp_now', None) == tax:
        return
    g._stamp_now = tax
    set_order_cost(OrderCost(
        open_tax=0, close_tax=tax,
        open_commission=COMMISSION, close_commission=COMMISSION,
        close_today_commission=0, min_commission=MIN_COMMISSION), type='stock')
    log.info('[cost] %s 起印花税 -> %.4f' % (d, tax))


def initialize(context):

    set_option("avoid_future_data", True)
    set_option('use_real_price', True)
    set_benchmark(BENCHMARK)

    # ---- A-3: 显式成本，不再依赖平台默认 ----
    set_order_cost(OrderCost(
        open_tax=0,
        close_tax=CLOSE_TAX,
        open_commission=COMMISSION,
        close_commission=COMMISSION,
        close_today_commission=0,
        min_commission=MIN_COMMISSION), type='stock')
    set_slippage(PriceRelatedSlippage(SLIPPAGE))

    # ★ 与本地对齐的两件事（COST_MODE='align_local' 时）
    if COST_MODE == 'align_local':
        # 本地 volume_ratio=0.25：单笔成交不超过当日成交量的 25%
        try:
            set_option('order_volume_ratio', 0.25)
        except Exception as _e:
            log.warn('set_option order_volume_ratio 失败: %s' % _e)
    if STAMP_AUTO:
        # 印花税分段：每日盘前按当天日期重设，跨过 2023-08-28 自动切到万5
        run_daily(_sync_stamp_tax, time='08:55')

    log.set_level('order', 'error')
    log.set_level('history', 'error')
    log.set_level('system', 'error')

    g.sell_list = []
    g.buy_df = []
    g.target_num = TARGET_NUM
    g.high_limit_list = []
    g.hold_list = []
    g.target_list = []
    g.backup_list = []
    g.blacklist = set()

    g.stat_exit = 0
    g.stat_hold = 0
    g.stat_reentry = 0
    g.stat_nocand = 0
    g.stat_underfill = 0
    g.stat_buyskip = 0
    g.stat_dedup = 0

    log.info('[CONFIG] v2 | target=%s backup=%s | 第%d个交易日调仓 '
             'pick=%s trade=%s limitup=%s | verify=%s slippage=%.5f '
             'commission=%.6f/min%s tax=%.4f'
             % (TARGET_NUM, BACKUP_NUM, REBALANCE_DAY,
                TIME_PICK, TIME_TRADE, TIME_LIMIT_UP,
                VERIFY_MODE, SLIPPAGE, COMMISSION, MIN_COMMISSION, CLOSE_TAX))

    run_daily(prepare_stock_list, TIME_PREPARE)
    run_monthly(get_stock_list, REBALANCE_DAY, TIME_PICK)
    run_monthly(trade, REBALANCE_DAY, TIME_TRADE)
    run_daily(check_limit_up, TIME_LIMIT_UP)
    run_daily(log_stat, 'after_close')


def prepare_stock_list(context):
    g.high_limit_list = []
    g.hold_list = list(context.portfolio.positions)
    if len(g.hold_list) != 0:
        df = get_price(g.hold_list, end_date=context.previous_date, frequency='daily',
                       fields=['close','high_limit'], count=1, panel=False,
                       fill_paused=False, skip_paused=False).dropna()
        df = df[df['close'] == df['high_limit']]
        g.high_limit_list = list(df.code)


def get_stock_list(context):

    g.buy_df = pd.DataFrame(index=[], columns=[ 'name', 'price', 'amount', 'value'])
    yesterday = str(context.previous_date)
    today = context.current_dt

    initial_list = get_all_securities('stock', today).index.tolist()
    initial_list = filter_new_stock(context,initial_list)
    initial_list = filter_kcb_stock(initial_list)
    initial_list = filter_st_stock(initial_list)
    initial_list = filter_paused_stock(initial_list)

    # 红利低波
    stock_list = initial_list
    stock_list = get_dividend_ratio_filter_list(context, stock_list, False, 0.00, 0.10, 0.03) #股息最高10%且最近一年不低于3%
    stock_list = get_factor_sorted_list(context, stock_list, 'beta', True) #按 beta 升序，取前 N 只即"受市场影响最小的"（B-3: 已删分位截断）
    HLDB_list = stock_list[:min(g.target_num[0], len(stock_list))]
    HLDB_backup = stock_list[g.target_num[0] : g.target_num[0] + BACKUP_NUM[0]]

    # 红利价值
    stock_list = initial_list
    df = get_fundamentals(query(
            valuation.code,
        ).filter(
            valuation.code.in_(stock_list),
            #合理的财务指标，既避免价值陷阱，也防止畸高收益
            valuation.pe_ratio.between(5, 50), #市盈率
            indicator.inc_return.between(5, 100), #净资产收益率(扣除非经常损益)(%)
            indicator.inc_total_revenue_year_on_year.between(5, 100), #营业总收入同比增长率(%)
            indicator.inc_net_profit_year_on_year.between(10, 100), #净利润同比增长率(%)
        ))
    stock_list = list(df.code)
    stock_list = get_dividend_ratio_filter_list(context, stock_list, False, 0.00, 0.10, 0.03) #股息率最高的几只
    HLJZ_list = stock_list[:min(g.target_num[1], len(stock_list))]
    HLJZ_backup = stock_list[g.target_num[1] : g.target_num[1] + BACKUP_NUM[1]]

    # A-5: 确定性去重保序（HLDB 优先），替代 list(set(...)) 的不确定顺序
    target_list = []
    for _s in list(HLDB_list) + list(HLJZ_list):
        if _s not in target_list:
            target_list.append(_s)
    g.target_list = target_list

    seen = set(target_list); backup = []
    for s in list(HLDB_backup) + list(HLJZ_backup):
        if s not in seen:
            seen.add(s); backup.append(s)
    g.backup_list = backup
    g.blacklist = set()

    # 欠配诊断：HLDB / HLJZ 各自实际交付几只
    if len(HLDB_list) < g.target_num[0] or len(HLJZ_list) < g.target_num[1]:
        g.stat_underfill += 1
    log.info('[PICK] HLDB=%d/%d HLJZ=%d/%d 并集=%d 备选=%d (累计欠配 %d 次)'
             % (len(HLDB_list), g.target_num[0], len(HLJZ_list), g.target_num[1],
                len(target_list), len(g.backup_list), g.stat_underfill))

    g.sell_list = [s for s in g.hold_list if s not in target_list and s not in g.high_limit_list]
    buy_list = [s for s in target_list if s not in g.hold_list]

    # A-5: 不再预先估算资金与股数，只备好限价；股数在 trade() 里按实际现金逐笔算
    if len(buy_list) > 0:
        df = get_price(buy_list, end_date=yesterday, frequency='1d', count=1, fields=['close'], fq='pre', panel=False, skip_paused=False, fill_paused=True).set_index('code')
        g.buy_df = pd.DataFrame(index=buy_list, columns=['name', 'price'])
        g.buy_df['name']  = [get_security_info(s, yesterday).display_name for s in buy_list]
        g.buy_df['price'] = [round(df.loc[s, 'close'] * 1.05, 2) for s in buy_list]


def trade(context):
    current_data = get_current_data()

    # 1) 先完成全部卖出
    for s in g.sell_list:
        if current_data[s].last_price < current_data[s].high_limit:
            order_target_value(s, 0)

    # 2) A-5: 卖出到账后，逐笔用【当时的实际可用现金】分配
    #    预算按限价(昨收x1.05)计 -> 不可能透支；剩余现金自动流给后面的买单
    df = g.buy_df
    if df is None or len(df) == 0:
        return
    pending = list(df.index)
    n = len(pending)
    for i, s in enumerate(pending):
        per = context.portfolio.available_cash / (n - i)
        px  = df.loc[s, 'price']
        if px <= 0:
            continue
        amt = 100 * int(per / px / 100)
        if amt <= 0:
            g.stat_buyskip += 1
            log.info('[BUY-SKIP] %s %s 资金不足: 可分配 %.0f, 100股需 %.0f (累计跳过 %d)'
                     % (s, df.loc[s, 'name'], per, px * 100, g.stat_buyskip))
            continue
        order(s, amt, LimitOrderStyle(px))


# 炸板离场：昨日涨停、今日开板即清仓；仍封住则继续持有
def check_limit_up(context):
    current_data = get_current_data()

    sold = []
    if g.high_limit_list != []:
        for s in g.high_limit_list:
            if s not in context.portfolio.positions:
                continue
            if current_data[s].last_price < current_data[s].high_limit:
                order_target_value(s, 0)
                g.stat_exit += 1
                sold.append(s)
                g.blacklist.add(s)
            else:
                g.stat_hold += 1

    if ENABLE_REENTRY and len(sold) > 0:
        do_reentry(context, len(sold))


# 炸板后再入场：目标池缺口 → 备选池；刚卖出的票拉黑到下次调仓
def do_reentry(context, n_slot):
    current_data = get_current_data()
    held = set(context.portfolio.positions)

    cands = [s for s in g.target_list if s not in held and s not in g.blacklist]
    for s in g.backup_list:
        if s not in held and s not in g.blacklist and s not in cands:
            cands.append(s)
    cands = [s for s in cands
             if (not current_data[s].paused)
             and (current_data[s].last_price < current_data[s].high_limit)]

    if len(cands) == 0:
        g.stat_nocand += 1
        return

    picks = cands[:n_slot]
    cash = context.portfolio.available_cash
    if cash <= 0:
        return
    per = cash / len(picks)

    df = get_price(picks, end_date=context.previous_date, frequency='1d', count=1,
                   fields=['close'], fq='pre', panel=False, skip_paused=False,
                   fill_paused=True).set_index('code')
    for s in picks:
        if s not in df.index:
            continue
        px = round(df.loc[s, 'close'] * 1.05, 2)
        if px <= 0:
            continue
        amt = 100 * int(1.05 * per / px / 100)
        if amt <= 0:
            continue
        order(s, amt, LimitOrderStyle(px))
        g.stat_reentry += 1


def log_stat(context):
    tv = context.portfolio.total_value
    log.info('[STAT] exit=%d hold=%d reentry=%d nocand=%d underfill=%d buyskip=%d dedup=%d | 持仓 %d 只 cash=%.1f%% total=%.2f'
             % (g.stat_exit, g.stat_hold, g.stat_reentry, g.stat_nocand, g.stat_underfill, g.stat_buyskip, g.stat_dedup,
                len(context.portfolio.positions),
                context.portfolio.available_cash / tv * 100 if tv > 0 else 0, tv))


############################################################################################################################################################################

def filter_paused_stock(stock_list):
    current_data = get_current_data()
    return [stock for stock in stock_list if not current_data[stock].paused]

def filter_st_stock(stock_list):
    current_data = get_current_data()
    return [stock for stock in stock_list
            if not current_data[stock].is_st
            and 'ST' not in current_data[stock].name
            and '*' not in current_data[stock].name
            and '退' not in current_data[stock].name]

def filter_kcb_stock(stock_list):
    return [stock for stock in stock_list  if ((stock[0] != '4') and (stock[0] != '8') and (stock[0:2] != '68'))]

def filter_new_stock(context, stock_list):
    yesterday = context.previous_date
    return [stock for stock in stock_list if not yesterday - get_security_info(stock).start_date < datetime.timedelta(days=250)]

# ============================================================================
# 股息率计算（A-1 修复，见配置区 DIV_METHOD）
# ============================================================================

def _query_dividends(stock_list, date_field, time0, time1, extra_fields):
    """分块查询分红记录并拼接。finance.run_query 单次最多 4000 行，超限会静默截断。"""
    fields = [finance.STK_XR_XD.code, finance.STK_XR_XD.bonus_amount_rmb] + extra_fields
    parts = []
    for i in range(0, len(stock_list), DIV_CHUNK):
        chunk = stock_list[i:i + DIV_CHUNK]
        q = query(*fields).filter(
            date_field >= time0,
            date_field <= time1,
            finance.STK_XR_XD.code.in_(chunk))
        part = finance.run_query(q)
        if len(part) >= 3900:
            log.warn('[DIV] 单次查询返回 %d 行，接近 4000 上限，可能已截断。'
                     '请降低 DIV_CHUNK 或缩短 DIV_LOOKBACK_DAYS' % len(part))
        parts.append(part)
    if not parts:
        return pd.DataFrame(columns=['code', 'bonus_amount_rmb'])
    return pd.concat(parts, ignore_index=True) if len(parts) > 1 else parts[0]


def _dividend_by_fiscal_year(context, stock_list):
    """按分红归属会计年度汇总（v7：加入去重、剔除取消、日期锚定）。

    可见性用 board_plan_pub_date：预案公告即公开信息，非未来函数；
    年度分红一经公告即被采用，与市场同步。
    """
    time1 = context.previous_date
    time0 = time1 - datetime.timedelta(days=DIV_LOOKBACK_DAYS)
    df = _query_dividends(
        stock_list, finance.STK_XR_XD.board_plan_pub_date, time0, time1,
        [finance.STK_XR_XD.report_date, finance.STK_XR_XD.bonus_type,
         finance.STK_XR_XD.plan_progress, finance.STK_XR_XD.bonus_cancel_pub_date])
    if len(df) == 0:
        return pd.Series([], dtype='float64')

    # (1) 剔除已取消/终止、以及无金额的记录（如重整转增）
    df = df[df.bonus_cancel_pub_date.isna()]
    df = df[~df.plan_progress.isin(CANCEL_STATES)]
    df = df[df.bonus_amount_rmb.notna() & (df.bonus_amount_rmb > 0)]
    if len(df) == 0:
        return pd.Series([], dtype='float64')

    # (2) 去重：同一 (code, report_date, bonus_type) 只保留流程最靠后的一条
    df = df.copy()
    df['_stage'] = df.plan_progress.map(STAGE_ORDER).fillna(0)
    n0 = len(df)
    df = (df.sort_values('_stage')
            .drop_duplicates(subset=['code', 'report_date', 'bonus_type'], keep='last'))
    if n0 > len(df):
        g.stat_dedup += (n0 - len(df))

    # (3) 会计年度；用 report_date 月份==12 锚定"年度分红"，不依赖文本标签
    rd = pd.to_datetime(df['report_date'])
    df['fy'] = rd.dt.year
    annual = df[rd.dt.month == 12]
    if len(annual) == 0:
        return pd.Series([], dtype='float64')
    latest_fy = annual.groupby('code').fy.max()

    df = df.join(latest_fy.rename('latest_fy'), on='code')
    df = df[df.fy == df.latest_fy]
    return df.groupby('code').bonus_amount_rmb.sum()


def _dividend_by_rolling365(context, stock_list):
    """原实现：按股权登记日滚动 365 天求和。保留以便 A/B 对照。"""
    time1 = context.previous_date
    time0 = time1 - datetime.timedelta(days=365)
    df = _query_dividends(
        stock_list, finance.STK_XR_XD.a_registration_date, time0, time1,
        [finance.STK_XR_XD.a_registration_date])
    df = df.fillna(0)
    return df.groupby('code').bonus_amount_rmb.sum()


def get_dividend_ratio_filter_list(context, stock_list, sort, p1, p2, threshold):
    time1 = context.previous_date

    if DIV_METHOD == 'fiscal_year':
        dividend = _dividend_by_fiscal_year(context, stock_list)
    elif DIV_METHOD == 'rolling365':
        dividend = _dividend_by_rolling365(context, stock_list)
    else:
        raise ValueError('DIV_METHOD 必须是 fiscal_year 或 rolling365')

    if len(dividend) == 0:
        return []

    # 股息率 = 分红总额(万元)/10000 / 总市值(亿元)  —— 口径与原实现一致
    cap = get_fundamentals(
        query(valuation.code, valuation.market_cap).filter(
            valuation.code.in_(list(dividend.index))),
        date=time1).set_index('code')
    df = pd.concat([dividend.rename('bonus_amount_rmb'), cap], axis=1, sort=False)
    df = df[df.market_cap.notna() & (df.market_cap > 0)]
    df['dividend_ratio'] = (df['bonus_amount_rmb'] / 10000) / df['market_cap']

    df = df.sort_values(by=['dividend_ratio'], ascending=sort)
    df = df[int(p1 * len(df)):int(p2 * len(df))]
    df = df[df['dividend_ratio'] > threshold]
    return list(df.index)


# 因子排序（A-2 修正 + B-3 删分位截断）
# ---- A-2 ----
# 原实现：
#     score_list = get_factor_values(...)[jqfactor].iloc[0].tolist()
#     df['code'] = stock_list          # 靠位置对齐，假设返回列序 == 输入序
#     df['score'] = score_list
# 现改为直接用返回 Series 自带的 index（股票代码），无论 JQ 返回什么顺序都正确。
#
# ---- B-3 ----
# 原实现还有 p1/p2 分位截断（beta 用 0.00~0.50）。该截断：
#   · 池子够大时完全不 binding（后面反正只取前 N 只）
#   · 池子 <2N 时给不满 N 只 -> 欠配
#   · 对"假低 beta"无任何防护（是"从最低一半里选"，非"排除最低的"）
# 故直接删除 p1/p2 两个参数，函数只负责排序。自由度 -2。
def get_factor_sorted_list(context, stock_list, jqfactor, ascending):
    yesterday = context.previous_date
    s = get_factor_values(stock_list, jqfactor, end_date=yesterday, count=1)[jqfactor].iloc[0]
    df = s.rename('score').rename_axis('code').reset_index()
    df = df.dropna()
    df.sort_values(by='score', ascending=ascending, inplace=True)
    return list(df.code)
