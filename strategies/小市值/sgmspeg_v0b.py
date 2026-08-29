#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SG-MS-PEG-HL 线的 v0b：预过滤后按流通市值升序取 N 只（因子层消融基准）。

⚠️ **文件名是版本号，不是策略名。** `SG-MS-PEG-HL` 才是策略线的名字，
   `v0b` 是这条线里的一个版本 —— 而且是把三条因子支线【整个删掉】的消融
   基准。本地**没有实现 SG-MS-PEG-HL 本体**，三条支线一条都没做：

   ★ jqfactor 口径反推（探针 JQ/小市值/PROBE-因子口径验证*.txt + 离线拟合）
     ── 已定案 4 个，全部精确匹配 ──────────────────────────────────
     turnover_volatility = 【20 个交易日换手率(valuation.turnover_ratio)的
         标准差 ddof=1】，量纲小数。10/11 只比值精确 100.0000。
     total_profit_growth_rate = 【TTM 利润总额同比】。TTM = 单季值滚动四季度
         求和；分母取【原值不取绝对值】—— 300028 连亏两年，负÷负=正，
         取 abs 会得 -2.8885 而聚宽是 +0.8885。6/6。
     net_profit_growth_rate = 【TTM 全口径净利润(income.net_profit，含少数
         股东)同比】，分母同上取原值。★ 用归母是错的（对照组 0/6）。6/6。
     PEG = pe_ratio / (归母净利 TTM 同比 × 100)。4/4 精确。
         且 pe_ratio 本身 = market_cap / 归母净利TTM（000800 实测
         107.74亿/1.251亿=86.12 vs 聚宽 86.1043）。
         ⚠ 本地面板现有的 peg=(totalmv/np_ttm)/(np_yoy*100) 与此【不同】，
           np_yoy 的口径不对，要按上式重算。

     ── 关键发现: get_history_fundamentals 返回【单季值】不是累计值 ──────
     证据1: 000800 的 total_operating_revenue 2018Q1=7.15e9 > Q2=6.33e9
            > Q3=5.55e9 —— 累计值不可能递减。
     证据2: 300028 的 2016-12-31 营收 = -2.91e5 —— 累计年营收不可能为负。
     且 interval='1y' 是「每年同一报告期的单季值」(2012-09-30..2018-09-30)
     而不是年报(12-31)。最初按「累计+年报」算，所有候选全不中就是因为这个。

     ── 未定案 ────────────────────────────────────────────────
     operating_revenue_growth_rate: TTM 营收同比只中 4/6。疑似该用
         income.operating_revenue(营业收入) 而非 total_operating_revenue
         (营业总收入) —— 002377 的 inc_revenue_year_on_year(26.12)
         ≠ inc_total_revenue_year_on_year(26.35)，证明两口径对它确实不同。
     sales_growth / earnings_growth: ❌ 不是营收/利润序列的任何简单回归或比率。
         离线穷举 300 个组合（TTM/单季 × 3~6 点 × 步长1/4 × 5种偏移 ×
         取不取abs × 对不对数）最小误差 0.0226，且最优解是「用 2016Q3 结束的
         数据」这种无意义组合。与 CAGR5 皮尔逊 +0.92 但秩相关仅 +0.49、
         符号 5/10 —— 高相关低秩相关 = 同源但被某种变换处理过。
         => 主假设: 它们是【标准化风格因子】(去极值+标准化+市值行业中性化)。
            v3 探针用 800 只算横截面 mean/std 做判决性检验：
            若 mean≈0 且 std≈1 则确认，那就【无法从原始财务数据复现】，
            SG/MS 两路必须换实现路径（例如自定义等价因子并重新标定锚）。

     SG  sales_growth（5 年营业收入增长率）取最高 10%
     MS  四因子复合成长打分，取前 10%
         0.10×营业收入增长率 + 0.35×利润总额增长率
       + 0.15×净利润增长率   + 0.40×earnings_growth(5年盈利增长)
     PEG PEG 最低 0~0.2 分位，再叠 turnover_volatility 最低 50%
     HL  HighLimit：涨停留仓 / 炸板离场（这一层本文件【有】）

   三路各自按流通市值升序取若干只，取并集（去重后约 13~15 只）再截到 10。

## 为什么搬的是 v0b 而不是本体 —— JQ 自己已经量过因子层值多少

| 对比 | 前段 | 后段 | 全区间 |
|---|---|---|---|
| 因子层整体（v3d − v0b） | **+22.2pp** | **+1.9pp** | +9.3pp ⚠️ 几乎消失 |

JQ 该线版本谱（其区间约 7.3 年、基准年化 11.2%，**不可与 FROEC 线跨线比**）：
    原始基线 51.89% | v2 48.52% | v3a 48.88% | v3b 49.70%
    v3c 49.70% | v3d 48.27% | v4 待跑
    消融：去 MS 37.29% | 去 SG 41.87% | 去 PEG 47.22% | 三路全删 = v0a/v0b

两条 JQ 记录下来的判断：
  · 2024 上半年崩盘 v0b **-0.2%** vs v3d **-15.3%**
    -> 「因子层让崩盘更糟，不是更好」
  · v0b 在最近 4 个时段里赢 3 个 -> 「不是一次事件，是持续性的落后」
  · v0a -> v0b（只加 eps>0）：年化 +1.14pp、回撤 -1.33pp，三项同向改善

即：这条线的因子层价值几乎全在 2019-2021，近年归零。所以 v0b 才是这条线里
值得跟踪的那一版。

★ 文件名 `sgmspeg_v0b.py` 刻意带上【线名 + 版本号】：与 JQ 侧的
  `SG-MS-PEG-HL-v0b.txt` 一一对应，日后若实现本体（sgmspeg.py）或其它消融版
  （sgmspeg_drop_ms.py 等），命名自然成体系。
  ⚠️ 改名前的归档 strategy_path 仍是 strategies/小市值/v0b.py，所以面板里
  会看到【两个文件节点】—— 这是历史事实不是 bug，旧归档确实是那个路径跑的。

另有 strategies/小市值/mincap.py 是更彻底的零机制基准（连 eps>0 / 次新 /
候选截断 / 20 日黑名单 / 涨停留仓都没有），用于量出本文件那堆机制值多少：
同窗口同成本 mincap 16.31% vs 本文件 31.85%。

对标聚宽 SG-MS-PEG-HL-v0b：2019-01-01~2026-06-30 年化 38.98%、最大回撤 51.86%。

✅ 同口径残差已归因并收敛到 **-0.82pp**（原 -2.84pp）：
      本地 38.16% / 回撤 52.25%    聚宽 38.98% / 51.86%
   成因是【share_change 的「定期报告」行会把流通股回退到已被解禁事件超越的
   旧值】—— 源数据问题。实测 603536.XSHG：
       2018-06-13 限售股份上市  流通 6025.41万
       2018-06-30 定期报告      流通 4200.00万   <- 回退
       2018-12-31 定期报告      流通 6025.41万   <- 又回来（自证前一行是错的）
   双键 as-of 按 change_date 取最大，就在 2018-06~12 期间取到那个错的 4200万，
   流通市值算成 3.46 亿（真值 4.97 亿），于是它在「市值升序取最小 N 只」里被
   顶到【第 1 名】—— 而聚宽同期根本没买它。
   规模：全表 7.74% 的行「流通降而总股本未降」，其中 77% 是定期报告。
   已在 build_panel_daily.py 的 _shr 加护栏：定期报告不得把流通股压到低于
   最近一个【事件行】的值（回购/承诺限售是事件行，仍可正常下调）。
   修正后 603536 在 2018-12-28 从第 1 名移到第 13 名，与聚宽一致。

   首个调仓日（2018-12-28 决策）现为 **9/10 吻合**。唯一剩下的分歧
   603617.XSHG【是聚宽用了未来数据】：它用 3043.70万（5.04 亿），
   而该值来自 2019-04-25 才披露的年报，在 2019-01-04 不可见；
   我们用 2931.20万（4.85 亿）才是 PIT 正确的。这一只不改。
   ⚠️ 此前 docstring 隐含的"很接近"是【口径错配】造成的假象：
      拿滑点 0 的 38.11% 去比聚宽含滑点 0.0015 的 38.98%，看着只差 0.87pp。
      按同一口径实际差 -2.84pp。selftest 的模块级 JQ 字典是 FROEC 的口径
      (FixedSlippage(0))，跑 v0b 必须改成 slippage=0.0015。

逐步修复拆解（2016-01-01~2025-12-31，本金 100 万，聚宽 v0b 成本口径，
              **定期报告护栏之后**的面板）：

| 配置 | 年化 | 回撤 | 夏普 | 增量 |
|---|---|---|---|---|
| 全0 基线（四开关全复现旧行为） | 34.23% | 52.19% | 1.12 | — |
| ① 只修宇宙（含停牌股） | 33.17% | 51.50% | 1.09 | **-1.06pp** |
| ② 只修科创板（保留 689） | 34.23% | 52.19% | 1.12 | **+0.00pp** |
| ③ 只修次新边界（>=375） | 33.84% | 52.28% | 1.11 | -0.39pp |
| ④ 只修 eps 源 | 34.23% | 52.19% | 1.12 | **+0.00pp** |
| ①+④（宇宙 + eps 源） | 33.69% | 51.02% | 1.11 | -0.54pp |
| **全修（默认）** | **32.68%** | 52.26% | 1.08 | **-1.55pp** |

★ ② 与 ④ 单独打开都【一字不差】= +0.00pp，原因不同：
    ② 689009 流通市值 2.2~40.7 亿，永远进不了最小 15 只；而同样的修正在
      FROEC 上值 -1.67pp —— 结构性区别：FROEC 有 floor() 分位截断，宇宙
      少一只票就推移边界；v0b 是绝对 top-N，池外的票不影响结果。
    ④ 面板 eps_q 与 indicator.eps 对【有行情】的票数值 100% 相同
      （2015-12-31 实测 2512 只全同），所以 ① 关着时它没有作用面。
★ ④ 的真实效果只在 ① 之后显现：①+④ 的 -0.54pp 比 ① 单独的 -1.06pp
  高 **+0.52pp**。机制：停牌股的结转 eps 是【过期报告】
  （002379.XSHE@2015-12-31 结转得 2015-06-30 的 -0.0286，而当日最新是
  2015-09-30 的 +0.08），被 eps>0 误剔；改成按决策日 as-of 后它们回到池子里。
  -> 【纠缠的开关必须两两测，单独测会得出"这条修复没用"的错误结论。】
★ 可加性：①-1.06 + ②0.00 + ③-0.39 + ④0.00 = -1.45pp，全修 -1.55pp，
  交互项仅 **-0.10pp**（定期报告护栏之前是 0.44pp）—— 护栏也让拆解变线性了，
  合理：回退 bug 本身在制造虚假的名次错乱，会和每一条修复纠缠。
★ ③ 次新边界在护栏前是 +0.21pp、护栏后是 -0.39pp（换向 0.6pp）——
  边界类参数本就落在刀刃噪声里，不要按符号解读。

逐年（全0 vs 全修）：
    2016 -2.13  2017 -5.51  2018 +0.67  2019 +0.09  2020 -2.55
    2021 -0.50  2022 -2.48  2023 -3.74  2024 +0.54  2025 +2.64  (pp)
  均值 -1.30pp、标准差 2.42pp、n=10、**t = -1.69 不显著**，修复后更高 4/10。
  差异散开而非集中于某年。

★★ 修复的验收标准是【对标残差】而不是收益：残差 -2.84pp -> -0.82pp。
   收益降 1.55pp 且统计上不显著，正是一个正确性修复该有的样子 ——
   若某条"修复"大幅提高收益，反而该怀疑它是不是把 bug 修反了。

★ 成本口径：聚宽原版【自己设了滑点 0.0015】(PriceRelatedSlippage，双边)
  + 佣金万3 + 最低 5 元 + 印花税千一固定，type='stock'。
  所以对标本策略【不要用 --jq-cost】（那会把滑点设成 0，是 FROEC 的口径）：
      --slippage 0.0015 --commission 0.0003 --min-commission 5 --stamp-tax 0.001
  已验证成交价语义一致：首个调仓日 (2019-01-02) 10 只逐一吻合，
  聚宽交易详情里的成交价 = 当日开盘价 x (1 + 0.0015/2)，例如
  300417 开盘 15.23 x 1.00075 = 15.24 = 聚宽显示价。10/10 精确命中，
  说明开盘价数据与滑点方向（买 +s/2）两边都对得上。

★ 策略里【没有】任何涨跌停 / T+1 / 停牌 / 整手 / 税费的代码 —— 全部由 broker
  负责。旧引擎把涨停过滤写在选股里，那是错的位置：既重复了撮合职责，
  又因为只做了买入侧、漏了卖出侧而静默失真。
"""
from assay.api import *          # noqa: F401,F403

# 候选宇宙。★ 与 froec.py 同源的四条修正，每条一个单变量开关（见 initialize）：
#   1) 宇宙取【权威在册表】而不是面板当日行 —— 面板是 K 线驱动的，
#      停牌股当日无 K 线即无行，会整体缺席候选池。聚宽
#      get_all_securities() 含停牌股：它们会占掉 candidate_num 个候选名额，
#      之后才被 filter_paused_stock 删掉。
#   2) 科创板按 688% 排除（聚宽是 stock[0:3] != '688'，【保留】689 开头的
#      科创板 CDR）。原实现按 tdx symbol 'sh68%' 排，把 689009 也排掉了。
#   3) 次新按 >= {listed} 日历日（聚宽：not yesterday - start_date < 375 天）。
#      原实现写 > 375，差一天。
#   4) eps 取 fin_indicator_q（聚宽 indicator.eps）按【决策日】as-of，不从面板
#      结转 —— 面板 eps_q 的值与 indicator.eps 完全一致（实测 2015-12-31
#      2512 只有行情票 100.00% 相同，名字带 _q 但不是单季），所以对有行情的
#      票没差别；差别只在停牌股：结转会拿到该股【最后交易日】那天的基本面，
#      可能是过期报告。聚宽 get_fundamentals(q, date=yesterday) 无论停牌与否
#      都给决策日已披露的最新报告。
#
# floatmv > 0 是必需的护栏：300114.XSHE 有真实价格与成交量却 floatmv=0，
# 按市值【升序】选股时 0 永远排第一，实测它从 2016 年起 2056 个交易日霸占首位。
SQL = """
WITH univ AS (
  SELECT code FROM read_parquet('{root}/std/security_universe.parquet')
  WHERE sec_type='stock' AND list_date <= DATE '{sd}'
    AND (delist_date IS NULL OR delist_date > DATE '{sd}')
    AND date_diff('day', list_date::DATE, DATE '{sd}') {lop} {listed}
    AND code NOT LIKE '{kcb}'
), today AS (
  SELECT jq_code, floatmv, is_risk_warned, eps_q
  FROM {panel} WHERE date = DATE '{sd}'
), miss AS (
  -- 当日无 K 线（停牌）：只结转【价格派生量】(floatmv)，与聚宽一致 ——
  -- 停牌股的 valuation 按最后收盘价算。基本面走 eps1，不结转。
  SELECT u.code AS jq_code, p.floatmv, p.is_risk_warned, p.eps_q
  FROM (SELECT code, DATE '{sd}' AS d FROM univ
        WHERE code NOT IN (SELECT jq_code FROM today)) u
  ASOF LEFT JOIN (
      SELECT jq_code, date, floatmv, is_risk_warned, eps_q
      FROM {panel} WHERE date > DATE '{sd}' - INTERVAL 400 DAY
  ) p ON p.jq_code = u.code AND p.date <= u.d
), eps1 AS (
  SELECT code, eps FROM (
    SELECT code, eps, row_number() OVER (PARTITION BY code
             ORDER BY report_date DESC, pub_date DESC) rn
    FROM read_parquet('{root}/std/fin_indicator_q.parquet')
    WHERE pub_date <= DATE '{sd}' AND eps IS NOT NULL
  ) WHERE rn = 1
)
SELECT r.jq_code FROM (
  SELECT * FROM today WHERE jq_code IN (SELECT code FROM univ)
  UNION ALL SELECT * FROM miss WHERE {pin}
) r LEFT JOIN eps1 e ON e.code = r.jq_code
WHERE NOT COALESCE(r.is_risk_warned, FALSE)
  -- eps_carry=1 复现旧行为：用面板 eps_q（停牌股是【结转】值，可能是过期报告）
  -- eps_carry=0 修正后：用 fin_indicator_q 按决策日 as-of
  AND (CASE WHEN {epsc} THEN r.eps_q ELSE e.eps END) > 0
  AND r.floatmv > 0
ORDER BY r.floatmv ASC LIMIT {cand}
"""


def initialize(context):
    g.stock_num = getattr(g, 'stock_num', 10)
    g.candidate_num = getattr(g, 'candidate_num', 15)
    g.listed_days = getattr(g, 'listed_days', 375)
    g.limit_days = getattr(g, 'limit_days', 20)
    g.weekday = getattr(g, 'weekday', 1)
    g.exit_time = getattr(g, 'exit_time', '14:00')
    g.limit_up_exit = getattr(g, 'limit_up_exit', 1)
    # ---- 四个单变量开关：1 = 修正后（与聚宽一致），0 = 复现修复前的行为 ----
    #   ★ 保留 0 的取值不是为了"可以选"，是为了能【逐条量出每个 bug 值多少】。
    #     不能一次改四处再看总差 —— 那样无法归因，也无法发现互相抵消。
    g.paused_in_pool = getattr(g, 'paused_in_pool', 1)   # 宇宙含停牌股
    g.kcb_688_only = getattr(g, 'kcb_688_only', 1)       # 只排 688（保留 689 CDR）
    g.listed_ge = getattr(g, 'listed_ge', 1)             # >= 375 而非 > 375
    # eps 源：0 = fin_indicator_q 按决策日 as-of（修正后）
    #         1 = 面板 eps_q（复现旧行为；对有行情票两者数值 100% 相同，
    #             差别只在停牌股 —— 结转会拿到该股最后交易日那天的过期报告）
    #   ★ 所以本开关只在 paused_in_pool=1 时才可能有效果，单独打开预期 0.00pp。
    g.eps_carry = getattr(g, 'eps_carry', 0)
    # ---- 固定止损（g.stop_loss）----
    # 0 = 关（默认，保真）；0.15 = 持仓相对建仓价回撤到 -15% 就清仓。
    # ★ 价格两端同为【后复权】：Position.entry_price 与 close_hfq 都是 hfq，
    #   除权除息不会造成假触发，衡量的是【含息总收益】。
    # ★ 判定时点 g.stop_time（默认 14:00，INTRADAY 相位），成交价按本项目
    #   约定用收盘价代理 —— 与炸板离场同一套约定，不引入新的价格假设。
    # ⚠️ 这是【日频】止损不是盘中触发，两个方向都偏离真实：
    #     · 日内穿到 -18% 又拉回收盘 -12% 的，本实现【不触发】（真实会）
    #     · 跳空低开的按当日收盘价成交，实际止损点可能远低于名义位
    #   所以结果应读作「日线级别止损的效果」，不是「精确止损位的效果」。
    # 🔴 实测结论：**这四条标星策略上止损无效，默认保持 0（关）**。
    #   -15/-20/-25 三档 × 4 条策略的完整结果、逐年回撤归因、配对 t 检验见
    #   strategies/止损方案实测-固定_移动_吊灯.md。三句话版本：
    #     1) 收益非单调（-15/-25 常胜 -20），四档极差 < 已知扰动噪声带 3.00pp
    #     2) 全周期回撤那 -7pp 里 2024 一次事件占 77%；剔掉 2024 后效应塌到
    #        ±0.5pp、|t|<0.8；"回撤更小的年份"计数与掷硬币无异（3~6 / 11）
    #     3) 代价却是确定的：换手 +7~19%、胜率 -1.1~3.7pp
    #   保留参数是为了将来能复测，不是推荐启用。
    # 🔴 移动止损（trail_stop）实测同样【中性偏负】：12 格里 9 格收益为负、
    #   3 格显著负、0 格显著正；且形状是"越松越接近 off"的单调逼近而非倒 U
    #   —— 不存在最优移动止损位。唯一亮点 froec 移动25% 在其孪生策略
    #   froec_traded 上符号翻转（+2.53pp vs -2.36pp），是噪声指纹。
    # 🔴 吊灯止损（chand_k）是【显著有害】，性质与前两套不同：16/16 格收益
    #   为负、14 格 |t|>2.2；夏普 15 低/1 平/0 高（符号检验 p≈3e-5）。
    #   机制：ATR 阈值在小市值日波动下天天触发（持有 33 天 -> 3.8~18.6 天），
    #   离场后现金闲置到下次调仓，平均仓位崩到 28~85%；且 13/16 格的实际
    #   回撤【高于】按仓位朴素折算的预期 —— 同样是少拿仓位，它比无脑减半仓更差。
    #   详见 strategies/止损方案实测-固定_移动_吊灯.md
    g.stop_loss = getattr(g, 'stop_loss', 0.0)
    g.stop_time = getattr(g, 'stop_time', '14:00')
    # 止损后 N 个交易日内不再买回（0 = 不禁，下次调仓即可原价重入）
    g.stop_ban = getattr(g, 'stop_ban', 0)
    g.stop_banned = {}
    # ---- 移动止损 g.trail_stop（0 = 关）----
    # 从【持有期内最高收盘价】回撤 x 即清仓。与固定止损的区别：锚点会往上抬，
    # 所以它保护的是浮盈，固定止损保护的是本金。两者可同时开（任一触发即卖）。
    g.trail_stop = getattr(g, 'trail_stop', 0.0)
    # ---- 吊灯止损 g.chand_k（0 = 关）----
    # Chandelier Exit：close < HH(n) - k * ATR(n) 即清仓。n=22/k=3 是原始设定。
    # ★ ATR 用【真实波幅】TR = max(H-L, |H-PC|, |L-PC|) 的简单均值（不是 Wilder
    #   平滑）—— 窗口只有 22 根时两者差别很小，简单均值可精确复算、便于核对。
    # ★ 建仓时用 context.data.bar_range 取【入场前】n 根 K 线来起窗口，
    #   否则吊灯在建仓后头十几天完全不设防（那等于只测了"持有满 22 天的票"）。
    # ★ 全部用后复权价：H/L/C 与 ATR 同口径，除权除息不会造成假触发。
    g.chand_k = getattr(g, 'chand_k', 0.0)
    g.chand_n = getattr(g, 'chand_n', 22)
    # ---- 盘中触发 g.stop_intraday（0 = 日频，默认）----
    # 0：14:00 用【当日收盘价】判定与成交（引擎无分时线时的默认约定）。
    #    日内穿到 -40% 又拉回收盘 -30% 的【不触发】；跳空低开的按收盘价成交。
    # 1：用【当日最低价】判定 —— low <= 建仓价×(1-stop) 即认为止损单被打掉。
    #    成交价 = 跳空低开(open <= 止损价)则取 open，否则取止损价；
    #    再由 broker 夹进当日真实 [low, high]，策略无法凭空造价。
    #    一字跌停仍然卖不掉（无对手盘），会记成拒单。
    g.stop_intraday = getattr(g, 'stop_intraday', 0)
    # ---- 组合级止损 g.pf_stop（0 = 关）----
    # 个股止损防的是【离散风险】。持仓同向的策略（红利：10~15 只票一起阴跌）
    # 没有单只跌得够快去打掉个股止损 —— 实测红利各档回撤差全在
    # -0.33 ~ +0.04pp，工具与风险不匹配。组合级止损直接盯【总权益回撤】。
    # 🔴 两条必须写对，否则会静默变成"一次性永久空仓"：
    #   1) 清仓后权益变常数，**自身曲线不能再给复位信号** ——
    #      恢复条件只能来自外部（冷静期 + 基准指数均线）。
    #   2) 恢复时**必须把峰值重置为当前权益**，否则"回撤仍 <= -X"当天就再触发。
    # ★ 基准序列只取【前一交易日及更早】，不碰当天（PIT）。
    g.pf_stop = getattr(g, 'pf_stop', 0.0)     # 峰值回撤阈值，如 0.10
    g.pf_wait = getattr(g, 'pf_wait', 20)      # 触发后至少空仓 N 个交易日
    g.pf_ma = getattr(g, 'pf_ma', 0)           # 基准需重回 N 日均线上方（0=不要求）
    g.pf_cut = getattr(g, 'pf_cut', 1.0)       # 削减比例，1.0=全清、0.5=砍一半
    g.pf_bench = getattr(g, 'pf_bench', '000905.XSHG')
    g.pf_halt = False
    g.pf_wait_left = 0
    g.pf_peak = 0.0
    g.n_pf_stop = 0
    g._bench_px = None
    g.pos_state = {}
    g.n_stop = 0
    g.hold_history = []          # 最近 N 日持仓并集，配合「涨停过」构成黑名单
    g.high_limit = set()         # 昨收涨停的持仓：调仓不卖，14:00 再看是否打开

    set_benchmark('000905.XSHG')      # 与聚宽原版一致：中证 500

    run_daily(prepare, time='09:05')
    run_weekly(rebalance, weekday=g.weekday, time='09:30')
    run_daily(check_limit_up, time=g.exit_time)
    run_daily(stop_check, time=g.stop_time)
    run_daily(pf_check, time=g.stop_time)


def prepare(context):
    """盘前：滚动持仓历史 + 找出昨收涨停的持仓。"""
    held = list(context.portfolio.positions)
    g.hold_history.append(set(held))
    if len(g.hold_history) > g.limit_days:
        g.hold_history = g.hold_history[-g.limit_days:]

    g.high_limit = set()
    if held and context.previous_date:
        bars = context.data.bars(context.previous_date, held)
        g.high_limit = {c for c, b in bars.items() if b.limit_up}


def rebalance(context):
    d = context.previous_date
    if d is None or g.pf_halt:
        return
    df = context.data.query(
        SQL, sd=d, listed=g.listed_days, cand=g.candidate_num,
        lop='>=' if g.listed_ge else '>',
        kcb='688%' if g.kcb_688_only else '68%',
        pin='TRUE' if g.paused_in_pool else 'FALSE',
        epsc='TRUE' if g.eps_carry else 'FALSE')
    cand = df['jq_code'].tolist()
    if not cand:
        return

    # 顺序必须与聚宽一致：候选 -> 可交易过滤 -> 黑名单 -> **最后**截断。
    # 旧引擎在 select 内部就 [:10]，再剔黑名单 -> 名额被丢掉、下一名不补位；
    # 聚宽是先过滤再截断 -> 涨停/黑名单的候选会被下一名替换。
    # 实测这一处顺序差异让两个引擎从 2019-04-15 起持仓分歧。
    cand = stop_filter(context, context.tradable(cand, 'buy'))

    # 20 日黑名单：最近 20 日持有过 且 最近 20 日涨停过 -> 不再买入
    if g.hold_history:
        recent = context.data.had_limit_up(
            cand, context.data.nth_prev_day(context.current_date, g.limit_days), d)
        seen = set().union(*g.hold_history)
        cand = [c for c in cand if c not in (seen & recent)]
    target = cand[:g.stock_num]

    # 卖出：不在目标、且不是昨收涨停的持仓
    # （卖不掉的情况 —— 停牌、跌停、T+1 —— 由 broker 拒单，策略不必判断）
    for code in list(context.portfolio.positions):
        if code not in target and code not in g.high_limit:
            order_target_value(code, 0)

    # 等权买入缺的部分
    # ★ 买入名额的分母是【目标池实际长度】，不是 g.stock_num。
    #   聚宽原版：value = cash / (target_num - position_count)，
    #   其中 target_num = len(g.target_list) —— 20 日黑名单剔除后可能 < 10。
    #   写成 g.stock_num 会把钱多分一份，实测 2019-04-15 起持仓即分歧。
    need = [c for c in target if c not in context.portfolio.positions]
    need = need[:max(0, len(target) - len(context.portfolio.positions))]
    if need:
        per = context.portfolio.cash / len(need)
        for code in need:
            order_target_value(code, per)


def check_limit_up(context):
    """昨收涨停的持仓，今日尾盘若已打开则卖出；仍封住则继续持有。"""
    if not g.limit_up_exit or not g.high_limit:
        return
    codes = [c for c in g.high_limit if c in context.portfolio.positions]
    if not codes:
        return
    # 今天的状态走 context.current() —— 历史接口取不到今天（PIT 防火墙）。
    # 14:00 属 INTRADAY 阶段，limit_up 可见（与「盘中成交价用收盘价代理」的约定一致）。
    cur = context.current(codes)
    for code in codes:
        dd = cur.get(code)
        if dd is None:
            continue
        # ★ 按相位取字段：OPEN 阶段没有 limit_up（那是收盘派生量），
        #   直接取会得 None -> `not None` 为真 -> 把持仓全卖掉。
        sealed = dd.get('limit_up') if 'limit_up' in dd else dd.get('open_limit_up')
        if not sealed:
            order_target_value(code, 0)


# ============================ 固定止损 ============================
def stop_check(context):
    """三套离场规则合一：固定止损 / 移动止损 / 吊灯止损，任一触发即清仓。

    停牌（当日无 K 线）取不到价 -> 跳过；卖不掉，也就无所谓触发。
    涨停封板的即使触发也会被 broker 的可成交判定挡下，这里不特判。
    ★ 注册在 check_limit_up 之后：engine 按时间字符串**稳定**排序，
      同一时刻的任务保持注册顺序 —— 先炸板离场，再判止损。
    """
    if not (g.stop_loss or g.trail_stop or g.chand_k):
        return
    pos = context.portfolio.positions
    st = g.pos_state
    # 状态作废重来的两种情形：已清仓 / 建仓日变了（卖出后又买回，峰值要重置）。
    for c in list(st):
        if c not in pos or st[c]['entry'] != pos[c].entry_date:
            del st[c]
    codes = list(pos)
    if not codes:
        return
    # 新建仓的票：用【入场前】n 根 K 线把吊灯窗口先喂满（PIT 安全，取到昨天为止）。
    fresh = [c for c in codes if c not in st]
    seed = {}
    if fresh and g.chand_k and context.previous_date:
        seed = context.data.bar_range(
            fresh, context.data.nth_prev_day(context.current_date, g.chand_n),
            context.previous_date)
    for code in fresh:
        s = st[code] = {'entry': pos[code].entry_date,
                        'peak': pos[code].entry_price,
                        'highs': [], 'trs': [], 'pc': None}
        for _d, h, l, cl in seed.get(code, [])[-g.chand_n:]:
            if h is None or l is None:
                continue
            s['trs'].append(h - l if s['pc'] is None
                            else max(h - l, abs(h - s['pc']), abs(l - s['pc'])))
            s['highs'].append(h)
            s['pc'] = cl

    cur = context.current(codes)
    for code in codes:
        d = cur.get(code)
        if d is None:
            continue
        # 按相位取字段：OPEN 阶段没有 close/high/low（都是收盘派生量）。
        px = d.get('close_hfq') if 'close_hfq' in d else d.get('open_hfq')
        if not px:
            continue
        s = st[code]
        s['peak'] = max(s['peak'], px)
        hi, lo = d.get('high_hfq'), d.get('low_hfq')
        if hi is not None and lo is not None:
            s['trs'].append(hi - lo if s['pc'] is None
                            else max(hi - lo, abs(hi - s['pc']), abs(lo - s['pc'])))
            s['highs'].append(hi)
            s['pc'] = px
            if len(s['highs']) > g.chand_n:
                s['highs'] = s['highs'][-g.chand_n:]
                s['trs'] = s['trs'][-g.chand_n:]

        cost = pos[code].entry_price
        fill = None                      # None = 日频，按相位价成交
        hit = False
        if g.stop_loss and cost:
            if g.stop_intraday:
                # ★ 盘中模式用【最低价】判定：只要当日探到止损位，单子就被打掉了。
                trig = cost * (1.0 - g.stop_loss)
                if lo is not None and lo <= trig:
                    hit = True
                    op = d.get('open_hfq')
                    # 跳空低开 -> 开盘即成交（拿不到止损价）；否则按止损价成交。
                    fill = op if (op is not None and op <= trig) else trig
            elif px / cost - 1.0 <= -g.stop_loss:
                hit = True
        if not hit and g.trail_stop and s['peak']:
            if g.stop_intraday:
                trig = s['peak'] * (1.0 - g.trail_stop)
                if lo is not None and lo <= trig:
                    hit = True
                    op = d.get('open_hfq')
                    fill = op if (op is not None and op <= trig) else trig
            elif px / s['peak'] - 1.0 <= -g.trail_stop:
                hit = True
        if not hit and g.chand_k and s['trs']:
            lvl = max(s['highs']) - g.chand_k * (sum(s['trs']) / len(s['trs']))
            if g.stop_intraday and lo is not None and lo <= lvl:
                hit = True
                op = d.get('open_hfq')
                fill = op if (op is not None and op <= lvl) else lvl
            elif not g.stop_intraday and px < lvl:
                hit = True
        if hit:
            order_stop_sell(code, fill)
            g.n_stop += 1
            if g.stop_ban:
                g.stop_banned[code] = context.current_date

def stop_filter(context, codes):
    """止损冷静期：g.stop_ban 个交易日内不把刚止损掉的票买回来。"""
    if not g.stop_ban or not g.stop_banned:
        return codes
    cut = context.data.nth_prev_day(context.current_date, g.stop_ban)
    return [c for c in codes if g.stop_banned.get(c, cut) <= cut]


# ======================== 组合级止损 ========================
def _bench_up(context, n):
    """基准指数收盘是否站上 N 日均线。只用前一交易日及更早的数据（PIT）。"""
    if g._bench_px is None:
        g._bench_px = context.data.benchmark(g.pf_bench)[0]
    d = context.previous_date
    if d is None:
        return True
    px = [v for k, v in sorted(g._bench_px.items()) if k <= d]
    if len(px) < n:
        return True                    # 历史不够，不拿它当拦阻条件
    return px[-1] >= sum(px[-n:]) / n


def pf_check(context):
    """总权益从峰值回撤到 -g.pf_stop 就削仓；冷静期 + 基准转强后恢复。"""
    if not g.pf_stop:
        return
    tv = context.portfolio.total_value
    if g.pf_halt:
        if g.pf_wait_left > 0:
            g.pf_wait_left -= 1
            return
        if g.pf_ma and not _bench_up(context, g.pf_ma):
            return
        g.pf_halt = False
        g.pf_peak = tv                 # ★ 复位峰值，否则当天立刻再触发
        return
    if tv > g.pf_peak:
        g.pf_peak = tv
    if g.pf_peak <= 0 or tv / g.pf_peak - 1.0 > -g.pf_stop:
        return
    keep = 1.0 - g.pf_cut
    for code in list(context.portfolio.positions):
        order_target_value(code, context.portfolio.positions[code].value * keep)
    g.pf_halt = True
    g.pf_wait_left = g.pf_wait
    g.n_pf_stop += 1
