#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FROEC-PB-CAP-HL：低 PB + 单季 ROE 增长 + 小市值。

对标聚宽实测：2016-01-01~2026-08-07、初始资金 ￥100,000
             年化 36.80%、最大回撤 47.24%。
⚠️ 初始资金必须是 10 万：每仓仅 1 万，整手取整与 5 元最低佣金在这个规模上
   是实质影响，而且会让回测路径变混沌（±0.3pp 不可解释噪声）。

写这个策略验证了引擎的「策略无关性」：它比 v0b 多了三层筛选和一个跨表查询，
但**没有动引擎一行代码** —— 自定义取数走 context.data.query()。
"""
from assay.api import *          # noqa: F401,F403

EXCL_IND = ('钢铁I', '煤炭I', '石油石化I', '采掘I', '银行I', '非银金融I',
            '金融服务I', '交运设备I', '交通运输I', '传媒I', '环保I')

# 5 期单季 ROE 增长 + PB 半区 + ROE 十分位 + 行业过滤 + 市值升序。
# ★ roe 用聚宽权威 indicator.roe（std/fin_indicator_q），不本地推算 ——
#   实测它的分母是【平均净资产 (期初+期末)/2】，本地曾用期末，
#   257,525 条比对只有 41.22% 吻合，选股命中率因此长期卡在 72.1%。
# ★ PB 半区与 ROE 十分位是【向下取整截断】，不是 ntile：
#   聚宽源码是 list(df.code)[:int(0.5*len(df.code))]，
#   ntile 会把余数分给前桶(等价 ceil)，多取一只就会推移下游边界。
SQL = """
-- FROEC 候选池：在册宇宙 -> eps>0 / 非ST -> PB 低半区 -> ROE 加速度前 10%
--              -> 排除周期与金融行业 -> 按流通市值升序取前 N
-- ★ 首行注释是这条查询的【名字】，实盘的"选股理由"页拿它当组名
--   （lv/explain.py 的 _sql_head）。注释改不了任何行为。
WITH univ AS (
  -- ★★ 候选宇宙必须是【权威在册股票】，不能用面板当日行。
  --   面板是 K 线驱动的：停牌股当日无 K 线 -> 无行 -> 根本不进漏斗。
  --   实测 2015-12-31：面板 2542 行 vs 权威在市 2811 只，缺 267 只，
  --   而 paused_daily 当日停牌 266 只 —— 缺的就是停牌股。
  --   聚宽 get_all_securities() 含停牌股，它们会参与 PB 半区与 ROE 十分位的
  --   【切点计算】，还能先占掉 [:10] 的名额再被 filter_paused_stock 删掉。
  --   漏掉它们的后果（2015-12-31 实测）：
  --     L5 eps>0   1863 -> 2054  (JQ 2050)
  --     L7 pb半区   931 -> 1027  (JQ 1025)
  --     L10/L11    数量从对不上到【精确等于 101 / 94】
  SELECT code FROM {t_universe}
  WHERE sec_type='stock' AND list_date <= DATE '{sd}'
    AND (delist_date IS NULL OR delist_date > DATE '{sd}')
    AND date_diff('day', list_date::DATE, DATE '{sd}') >= {listed}
    AND code NOT LIKE '{kcb}'
    AND hash(code || '{salt}') % 10000 >= {pert}
), today AS (
  SELECT jq_code, pb, floatmv, is_risk_warned, sw_l1_name
  FROM {panel} WHERE date = DATE '{sd}'
), miss AS (
  -- 当日无 K 线（停牌）：只结转【价格派生量】(pb/floatmv)，与聚宽一致 ——
  -- 停牌股的 valuation 按最后收盘价算。★ 基本面【不结转】：聚宽的
  -- get_fundamentals 无论是否停牌都给当前最新报告，结转会拿到过期报告。
  -- 实测 002379.XSHE@2015-12-31：结转基本面得 2015-06-30 的 eps=-0.0286
  -- 被 eps>0 误剔，而当日最新是 2015-09-30 的 eps=+0.08（聚宽选中了它）。
  SELECT u.code AS jq_code, p.pb, p.floatmv, p.is_risk_warned, p.sw_l1_name
  FROM (SELECT code, DATE '{sd}' AS d FROM univ
        WHERE code NOT IN (SELECT jq_code FROM today)) u
  ASOF LEFT JOIN (
      SELECT jq_code, date, pb, floatmv, is_risk_warned, sw_l1_name
      FROM {panel} WHERE date > DATE '{sd}' - INTERVAL 400 DAY
  ) p ON p.jq_code = u.code AND p.date <= u.d
), ind AS (
  SELECT code, roe, report_date,
         row_number() OVER (PARTITION BY code ORDER BY report_date DESC) rn
  FROM {t_indicator}
  WHERE pub_date <= DATE '{sd}' AND roe IS NOT NULL
), eps1 AS (
  -- ★ eps 取 fin_indicator_q（聚宽 indicator.eps）按【决策日】as-of，
  --   不从面板结转。面板的 eps_q 值与 indicator.eps 完全一致
  --   （2015-12-31 实测 2512 只有行情票 100.00% 数值相同，名字带 _q 但不是单季），
  --   所以对【有行情】的票用哪边都一样；差别只出在停牌股：
  --   结转面板会拿到该股【最后交易日】那天的基本面，可能是过期报告。
  --   实测 002379.XSHE@2015-12-31：结转得 2015-06-30 的 eps=-0.0286 被 eps>0
  --   误剔，而当日最新是 2015-09-30 的 eps=+0.08（聚宽正是选中了它）。
  --   聚宽 get_fundamentals 无论是否停牌都返回当前最新报告 —— 基本面不结转。
  SELECT code, eps FROM (
    SELECT code, eps, row_number() OVER (PARTITION BY code
             ORDER BY report_date DESC, pub_date DESC) rn
    FROM {t_indicator}
    WHERE pub_date <= DATE '{sd}' AND eps IS NOT NULL
  ) WHERE rn = 1
), roec AS (
  SELECT code, 4*max(CASE WHEN rn=1 THEN roe END)
         - max(CASE WHEN rn=2 THEN roe END) - max(CASE WHEN rn=3 THEN roe END)
         - max(CASE WHEN rn=4 THEN roe END) - max(CASE WHEN rn=5 THEN roe END) AS increase
  FROM ind WHERE rn <= 5 GROUP BY 1 HAVING count(*) = 5
), base AS (
  -- ★ `e.eps` 只是【多带一列出去】给实盘的"选股理由"用（见 lv/explain.py）：
  --   WHERE 一个字没动，行与行序完全不变。指标必须从**策略自己这条查询**里出，
  --   另写一条"取指标的 SQL"就是第二份口径，迟早与它分叉而没人发现。
  SELECT r.*, e.eps FROM (
    SELECT * FROM today WHERE jq_code IN (SELECT code FROM univ)
    UNION ALL SELECT * FROM miss WHERE {pin}
  ) r JOIN eps1 e ON e.code = r.jq_code
  WHERE NOT COALESCE(r.is_risk_warned, FALSE)
    AND e.eps > 0 AND r.pb > 0 AND r.floatmv > 0
), pb_half AS (
  SELECT * FROM (SELECT *, row_number() OVER (ORDER BY pb ASC) rn,
                        count(*) OVER () AS n FROM base)
  WHERE rn <= {pbcut}
), roe_top AS (
  SELECT * FROM (
    SELECT b.jq_code, b.floatmv, b.sw_l1_name,
           -- ↓ 这四列只是【带出去解释用】：pb 与它在 PB 半区里的名次、
           --   ROE 加速度、EPS。参与筛选的仍然只有下面那个 rn2。
           b.pb, b.rn AS pb_rn, b.n AS pb_n, b.eps, r.increase AS roe_inc,
           row_number() OVER (ORDER BY r.increase DESC) rn2,
           count(*) OVER () AS n2
    FROM pb_half b JOIN roec r ON r.code = b.jq_code
  ) WHERE rn2 <= {roecut}
)
-- 🔴 末层多返回几列 = **投影**改动：WHERE / ORDER BY / LIMIT / OFFSET 一个字没动，
--    所以行与行序完全相同，`df['jq_code'].tolist()` 拿到的还是同一个列表。
--    这几列给实盘的"为什么选它"用（lv/explain.py），回测一行行为都不变：
--      · selftest 每次都跑：同一天把宽投影与"只返回 jq_code"对跑，代码列表逐位相同
--      · 加这几列时另跑过一次全历史（2016-01~2026-08）：逐日权益指纹 +
--        成交流水指纹（进出场日/代码/股数/价/原因）**逐位相同**
SELECT jq_code, floatmv, pb, eps, roe_inc, sw_l1_name,
       pb_rn, pb_n, rn2 AS roe_rn, n2 AS roe_n
FROM roe_top
WHERE sw_l1_name IS NULL OR sw_l1_name NOT IN ({excl})
ORDER BY floatmv ASC LIMIT {cand} OFFSET {skip}
"""


def initialize(context):
    g.stock_num = getattr(g, 'stock_num', 10)
    # 原版 get_stock_list()[:10]：先截再过滤、不补位
    g.candidate_num = getattr(g, 'candidate_num', 10)
    g.listed_days = getattr(g, 'listed_days', 250)
    g.limit_days = getattr(g, 'limit_days', 20)
    g.weekday = getattr(g, 'weekday', 1)
    # ---- 用于定位对标残差的两个开关（默认严格等于原版）----
    # 本地 41.24% vs JQ 36.80%（2016-01-01~2026-08-07，本金 10 万），差 +4.44pp。
    # 折算每笔约高 0.44%（967 笔 / 10 仓 ≈ 97 轮），量级像【成交价差异】
    # 而非选股差异 —— 胜率 61.94% 与 JQ 61.9% 几乎精确吻合。
    # 主要嫌疑：14:00 的炸板卖出。引擎无分时线，盘中相位用【收盘价】
    # 判定与成交，而 JQ 用 14:00 的分钟线价格。
    #   limit_up_exit  0 = 整条炸板规则关掉（量化这条规则值多少）
    #   exit_time      '14:00' 收盘价代理（原实现）/ '09:30' 开盘价成交
    #                  注意引擎相位边界是 t<='09:30' 才算 OPEN，'09:31' 已是 INTRADAY
    g.limit_up_exit = getattr(g, 'limit_up_exit', 1)
    g.exit_time = getattr(g, 'exit_time', '14:00')
    # ---- 补位开关（单变量隔离「停牌股占名额」这一条）----
    # 0 = 聚宽原版：get_stock_list()[:10] 先截断，再 filter_paused_stock，
    #     停牌股占掉的名额【不补】—— 目标池可能只有 8、9 只。
    # 1 = 补位：先剔停牌/涨跌停，再截到 stock_num，永远尽量填满 10 只。
    #     池子要相应放宽（fill_pool），否则过滤完不够 10 只，等于没补。
    # ★ 两种模式共用同一个候选宇宙和同一个 eps 口径，所以【分位切点完全相同】，
    #   差异只来自"名额补不补"这一件事 —— 这是和旧版对比学不到的：
    #   旧版同时改了宇宙和 eps，两版平均持仓其实一样（8.60 vs 8.67），
    #   3.99pp 的差来自选股不同，不是补位。
    g.fill_paused = getattr(g, 'fill_paused', 0)
    g.fill_pool = getattr(g, 'fill_pool', 40)
    # ---- 持有缓冲区（单变量隔离「卖出条件放宽」这一件事）----
    # 0 = 原版：不在 target（前 stock_num）里就卖。
    # N>0 = 已持有的票只要还在【前 stock_num + N】里就**继续持有**，
    #       买入只补到 stock_num 只。目的：降低无谓换手 ——
    #       第 11 名与第 10 名的差别通常小于一次往返的成本。
    # 🔴 用【另一次更宽的查询】判"还在不在缓冲区里"，不是把 candidate_num 调大：
    #   candidate_num 是"先截再过滤、不补位"的那个截断点，调大它会连**买入腿**
    #   一起改（过滤掉的名额会被后面的票补上），那就同时动了两个变量。
    #   多查一次的代价是每周一条 SQL。
    g.hold_buffer = getattr(g, 'hold_buffer', 0)
    # 缓冲区判据要不要也过【20 日涨停黑名单】：
    #   0 = 只看排名（卖出问的是"它还够好吗"，黑名单是"再买"的抑制器）
    #   1 = 也过黑名单与止损冷静期 —— 这样"唯一变的就是截断点 10 -> 10+N"
    # ★ 存在的理由：0 那一版顺带把黑名单的**卖出**副作用也关掉了，
    #   而涨停过的票正是刚涨完的票。不测这一版的话，"缓冲区变差"可能
    #   是被"多留了刚涨完的票"这件事解释掉的 —— 那就归因错了。
    g.hold_buffer_strict = getattr(g, 'hold_buffer_strict', 0)
    # ---- 还原「面板缺停牌股」这个历史 bug（单变量）----
    # 1 = 正确：候选宇宙含停牌股（聚宽 get_all_securities 的语义）
    # 0 = 复现 bug：宇宙只取面板当日有 K 线的行，停牌股整体缺席。
    #     ★ 这一条和 fill_paused 是【两个不同的变量】，不要混：
    #       paused_in_pool=0 -> 停牌股不参与【分位切点计算】(PB 半区/ROE 十分位)
    #                           2015-12-31 实测 L5 2050->1863、L7 1025->931，
    #                           切点位移 -> 选到完全不同的票
    #       fill_paused=1    -> 宇宙正确，只改「名额补不补」
    #     旧版之所以年化偏高 3.99pp，主因是前者（切点位移），不是后者：
    #     旧版与新版平均持仓几乎相同（8.67 vs 8.60）、持仓重合 97.7%。
    g.paused_in_pool = getattr(g, 'paused_in_pool', 1)
    # ---- 科创板过滤口径（另一个单变量）----
    # 1 = 正确：只排 688*。聚宽原版 filter_kcb_stock 是 stock[0:3] != '688'，
    #     【保留】689 开头的科创板 CDR。
    # 0 = 复现旧实现：按 tdx symbol 'sh68%' 排除，把 689009.XSHG（九号公司，
    #     全样本唯一的 689 代码）也一起排掉了。
    #     一只票就能让 base 从 3434 变 3433 -> floor(0.5*n) 与 floor(0.1*n2)
    #     两级边界同时位移 -> 边界票被换掉。实测 2024-09-06 就是这样把
    #     603955.XSHG 挤出 roe_top 的。10.5 年复利放大成 1.67pp 年化差 ——
    #     ★ 分位截断对宇宙大小【不是线性不敏感】，差一只票也会改变选股。
    g.kcb_688_only = getattr(g, 'kcb_688_only', 1)
    # ---- 刀刃敏感度探针（不是策略参数，是稳健性度量）----
    # 从候选宇宙里【确定性地】剔掉万分之 pert 的票（salt 换一批）。
    # 用途：分位截断 floor(0.5*n)/floor(0.1*n2) 会随宇宙大小推移边界，
    # 若剔掉几只无关的票就能让年化动几个 pp，那么"两种宇宙定义谁更好"
    # 这个问题本身就没有意义 —— 差异是边界位移，不是经济信号。
    # 实测（2016-01-01~2026-08-07，10万，13 组 salt，每组从 4484 只宇宙里
    #       剔掉 3~10 只，占 0.07%~0.22%）。★ 成本口径决定结论，必须写清楚：
    #
    #   【默认成本 滑点0.0015】基准 37.59%，扰动均值 37.87%，
    #       区间 36.43%~39.43%，极差 3.00pp，为正 8/13，相对基准均值 +0.28pp
    #       -> 对称噪声。刀刃效应存在（±3pp），但【没有系统性方向】。
    #
    #   【滑点0 即 --jq-cost】基准 37.41%，扰动均值 40.14%，
    #       区间 37.81%~42.34%，极差 4.53pp，为正 13/13（p=2^-13）
    #       -> 看着像"缩小宇宙系统性抬高收益"，那是【滑点0 的假象】，
    #          加上真实滑点后符号立刻变混合。不要引用这一组数字下结论。
    #
    # 结论（按默认成本）：
    #   ★ 配置之间的差距普遍小于 3.00pp 的扰动噪声，【不能用回测收益来选】
    #     "含不含停牌股"/"补不补位"/"截断方式"。
    #   ★ 对标聚宽必须让宇宙【完全一致】：0.15% 的宇宙差异就能动 3pp，
    #     远大于当前 +0.61pp 的残差。
    #   ★ 刀刃的根源不是 floor() 截断：固定池子(roe_fixed=130)极差 3.46pp、
    #     标准差 0.87pp，比比例截断的 3.00/0.81 还略高，且收益掉 6.50pp；
    #     加分散度(stock_num=15/20/30)收益掉 7~13pp、夏普也跌。
    #     真正的根源是【收益极度集中在最小的几只】—— skip_n=5 就损失
    #     13.72pp，所以任何让底部 5 只换人的扰动都有巨大杠杆。
    g.pert = getattr(g, 'pert', 0)
    g.pert_salt = getattr(g, 'pert_salt', 'a')
    # ---- 硬比例截断 -> 固定池子大小（曾试图用它压刀刃，实测无效）----
    # 0 = 原版比例截断 floor(0.5*n) / floor(0.1*n2)；>0 = 固定条数。
    # 实测（默认成本，13 组 salt）：固定 130 只的扰动极差 3.46pp、标准差
    # 0.87pp，比比例截断的 3.00pp / 0.81pp 还略高，且基准收益从 37.59%
    # 掉到 31.09%。=> 刀刃的根源不是 floor() 截断，保留参数仅供复核。
    # 历史池子规模参考：pb 半区 1025(2015)~1717(2024)，ROE 十分位 101~170。
    g.pb_fixed = getattr(g, 'pb_fixed', 0)
    g.roe_fixed = getattr(g, 'roe_fixed', 0)
    # ---- 跳过最小的 k 只（检验「极端微盘更差」假设）----
    # 扰动测试 13/13 全为正，混沌放大只能解释【量级】不能解释【方向】。
    # 若极端微盘系统性更差，则任何剔除都会把持仓推向稍大的票 -> 系统性变好。
    # skip_n>0 直接跳过市值最小的 k 只，取第 k+1 ~ k+10 只。
    g.skip_n = getattr(g, 'skip_n', 0)
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
    # ---- 买入腿延迟 g.buy_delay（'' = 关，与卖出同在 09:30）----
    # 引擎里调仓的买卖【两条腿用同一相位的同一个参考价】（09:30 开盘价），
    # 滑点对称施加，所以「先卖后买」的时间差在模型里是 0。
    # 现实中卖完到买进有几十秒到几分钟，买价会漂。这个开关把买入腿推到
    # 指定时刻（INTRADAY 相位 = 按当日【收盘价】成交），
    # 即约 4 小时的延迟 —— 日线数据能表达的最极端情形，用作【上界】。
    # ⚠️ 它衡量的是「延迟造成的价格漂移」，不是买卖价差；后者已在滑点里。
    g.buy_delay = getattr(g, 'buy_delay', '')
    g.pending = []
    # 整个调仓（含卖出腿）改到什么时刻。用来把「两腿错位」和「整体延迟」拆开：
    #   buy_delay 只推买入 -> 量的是【错位】
    #   rebal_time 两腿一起推 -> 量的是【整体晚于开盘价】
    g.rebal_time = getattr(g, 'rebal_time', '09:30')
    # ---- 固定交易日间隔调仓 g.rebal_every（0 = 关，用 run_weekly）----
    # run_weekly 锚在自然周上，假期短周会让间隔在 1~13 个交易日之间跳
    # （实测：weekday=1 有 15 次间隔 <=2 天，weekday=5 有 74 周整周不触发）。
    # 本开关改成【每 N 个交易日】一次，间隔恒定，代价是星期几会随假期漂移。
    # rebal_phase 是相位 0..N-1，换相位 = 换一条独立路径，正好用来量噪声。
    g.rebal_every = getattr(g, 'rebal_every', 0)
    g.rebal_phase = getattr(g, 'rebal_phase', 0)
    g.hold_history = []
    g.high_limit = set()
    # ---- 20 日涨停黑名单的四个口径（单变量隔离，默认值全是原版行为）----
    # 原版是 `最近 limit_days 天持有过` ∩ `窗口内涨停过` -> 剔除。
    # 🔴 这个组合有个**不对称**：建仓日 g.hold_history 为空 -> seen 是空集 ->
    #   交集恒空 -> 黑名单整段空转，于是一只"买入前刚涨停过"的票能买进来；
    #   而下一期 seen 里有它了、涨停日还在 20 日窗口内 -> 必然被剔 ->
    #   **只持有一期**。实测全历史 923 笔交易里 112 笔是这样（中位持有 7 天
    #   = 一个调仓周期，均收益 −0.82%，是四组里唯一负收益的那组）。
    # ★★ 2026-09-07 实测结论：**四个方案都没有统计显著的改善**，默认不动。
    #   逐年独立回测（2016~2026，每年重置 50 万）与原版的配对差：
    #       方案1 涨停即剔      +0.44pp  中位 +1.35  胜 6/11  sd  8.25  t=+0.18
    #       方案2 持有期间      +3.18pp  中位 +4.47  胜 6/11  sd 13.30  t=+0.79
    #       原版+补位          -0.81pp  中位 +1.11  胜 6/11  sd 11.30  t=-0.24
    #       方案1+补位         -1.55pp  中位 -3.49  胜 5/11  sd 10.89  t=-0.47
    #       方案2+补位         +1.03pp  中位 +3.88  胜 7/11  sd  9.93  t=+0.34
    #   🔴 **全程回测会给出相反的排序**（方案1 +2.58pp 看着最好）——
    #     逐年里它掉到 +0.44pp，因为 2024 单年 -18.9pp。sd 8~13pp 而效应
    #     只有 0~3pp，11 年样本撑不起任何结论。（同 CLAUDE.md：全程切片
    #     测的是路径混沌不是规则。）
    #   ★ 事件研究（N 大得多）也不支持改：被剔除卖出的 92 次里，
    #     被卖那只后 20 日 +1.86%，而**当天买的替代票 +4.65%** ——
    #     配对差 -2.78pp（t=-1.15，不显著），方向反而是"卖对了"。
    #   🔴 中间踩过一次基准错误：先算"被卖的票卖出后 20 日 +2.77%，t=+2.10
    #     显著" —— 那检验的是"≠0"，而这十年小市值整体年化 37%，
    #     任何一篮子 20 日后涨 2% 都正常（其余卖出也是 +2.06%）。
    #     基准换成"同期同策略的替代票"之后 t 就掉到 -1.15 了。
    # ★ 四个开关各自只动一件事，可以组合，默认全 0/1 = 原版：
    g.lu_need_held = getattr(g, 'lu_need_held', 1)
    #   1 = 原版：要求"最近持有过"（所以建仓日空转）
    #   0 = 窗口内涨停过就剔，不问持有过没有 —— "在黑名单里第一期就不该买"
    g.lu_hold_only = getattr(g, 'lu_hold_only', 0)
    #   1 = 涨停必须发生在**持有那几天**（买入前的涨停不算）。
    #       用 limit_up_days 拿涨停日明细，与 g.hold_days 逐日比对。
    g.lu_since_start = getattr(g, 'lu_since_start', 0)
    #   1 = 窗口起点不早于**策略起点**（回测的 --start / 实盘 warmup 起点）。
    #       "策略开始之前发生的涨停"不该影响第一期之后的决策。
    g.lu_buy_only = getattr(g, 'lu_buy_only', 0)
    #   1 = 黑名单**只抑制买入**，不导致卖出：已持有的票只问"它还在前
    #       stock_num 名里吗"，在就继续持有。
    #   ★★ 这一条不是又一个待测的主意，它修的是**规则自相矛盾**：
    #     同一个事实（这只票 20 日内涨停过）在建仓那期被放行、在下一期
    #     被用来卖出 —— 两种相反的处理。而本项目早已认定黑名单的定位
    #     （见上面 hold_buffer_strict 那段的注释）：
    #         「卖出问的是"它还够好吗"，黑名单是"再买"的抑制器」
    #         「0 那一版顺带把黑名单的**卖出副作用**也关掉了」
    #     也就是说卖出副作用一直被当成副作用，只是那条修复路径挂在
    #     `hold_buffer > 0` 后面，而 hold_buffer 默认 0 且已被否证。
    #   ★ 实盘 2026-09-08 的例子最能说明：被剔掉的两只排名是**第 3 和第 7**，
    #     稳稳在前 10 名内 —— 按"它还够好吗"根本不该卖。
    #   ★★ 回测结论：全程 39.14% vs 37.00%（夏普 1.274 vs 1.235，换手
    #     10.32 -> 9.05）；逐年 11 年均差 +5.29pp（t=+0.98）——
    #     🔴 但那几乎全来自 **2026 单点**（+56.30pp，只 8 个月还年化外推），
    #     去掉它只剩 +0.19pp、中位 -1.01pp、胜 4/10。**收益证不出好坏。**
    #     采用它的理由是①修矛盾②与本项目认定的定位一致③排除了显著变差
    #     且换手 11 年里 10 年更低（少付的费用是确定的）。
    g.fill_blacklist = getattr(g, 'fill_blacklist', 0)
    #   1 = 黑名单剔除后**补足** stock_num（复用 fill_pool 那个更宽的池子：
    #       过滤发生在截断之前，所以自然补满）。0 = 原版不补位。
    # ---- 「选股理由」的候选池深度 ----
    # 🔴 froec 原版 `LIMIT 10`，于是"选股理由"页只能列到第 10 名 ——
    #   看不到"差一点选上的是谁"（红利那条 SQL 返回几百行，所以能列到 20）。
    # ★ 做法是**多取几名但策略只用前 lim 个**（下面 `[:lim]` 那一刀）：
    #   WHERE / ORDER BY / OFFSET 一个字没动，只是 LIMIT 放大 ——
    #   而 `ORDER BY floatmv ASC` 是确定序，所以前 lim 行逐位不变。
    # 🔴 **绝不能直接把 LIMIT 调大而不截断** —— 那等于开了补位
    #   （过滤发生在截断之前，名额会被后面的票补上），
    #   而补位是 `fill_paused` / `fill_blacklist` 那个已测为负的开关。
    #   0 = 不多取（完全回到原版那一次查询）。
    g.explain_pool = getattr(g, 'explain_pool', 20)
    g.hold_days = []        # [(日期, 那天收盘时的持仓)] —— lu_hold_only 用
    g.start_date = None     # 策略起点，第一次 prepare 时钉住

    set_benchmark('000905.XSHG')      # 与聚宽原版一致：中证 500

    run_daily(prepare, time='09:05')
    if g.rebal_every:
        run_every(rebalance, ndays=g.rebal_every, offset=g.rebal_phase, time=g.rebal_time)
    else:
        run_weekly(rebalance, weekday=g.weekday, time=g.rebal_time)
    run_daily(check_limit_up, time=g.exit_time)
    run_daily(stop_check, time=g.stop_time)
    if g.buy_delay:
        if g.rebal_every:
            run_every(rebalance_buy, ndays=g.rebal_every, offset=g.rebal_phase,
                      time=g.buy_delay)
        else:
            run_weekly(rebalance_buy, weekday=g.weekday, time=g.buy_delay)
    run_daily(pf_check, time=g.stop_time)


def prepare(context):
    g.hold_history.append(set(context.portfolio.positions))
    if len(g.hold_history) > g.limit_days:
        g.hold_history = g.hold_history[-g.limit_days:]
    if g.start_date is None:
        g.start_date = context.current_date
    # ★ 逐日持仓要配**上一个交易日**：prepare 在 09:05 跑，此刻的 positions
    #   就是"昨收之后"的持仓；而涨停日说的是那天**收盘**涨停。
    #   配 current_date 的话整体错一天，那种错不报错、只是命中率不对。
    if context.previous_date is not None:
        g.hold_days.append((context.previous_date,
                            set(context.portfolio.positions)))
        if len(g.hold_days) > g.limit_days + 2:
            g.hold_days = g.hold_days[-(g.limit_days + 2):]
    g.high_limit = set()
    held = list(context.portfolio.positions)
    if held and context.previous_date:
        bars = context.data.bars(context.previous_date, held)
        g.high_limit = {c for c, b in bars.items() if b.limit_up}


def blacklist(context, cand, d):
    """20 日涨停黑名单 -> **要剔除的**代码集合。

    🔴 抽成一处：原来 `rebalance` 与 `hold_buffer_strict` 各写了一遍同样的
      四行，加开关时改一处漏一处的表现是"缓冲区那条路径还是旧口径"——
      而它不报错，只是两条路径对同一只票给出不同结论。
    """
    if not cand:
        return set()
    lo = context.data.nth_prev_day(context.current_date, g.limit_days)
    if g.lu_since_start and g.start_date is not None and lo < g.start_date:
        lo = g.start_date
    if g.lu_hold_only:
        # 涨停必须落在持有那几天。★ 这一支**与 hold_history 无关** ——
        #   "持有期间"本身就已经蕴含"持有过"了。
        lud = context.data.limit_up_days(cand, lo, d)
        if not lud:
            return set()
        held_on = {}
        for dt, s in g.hold_days:
            for c in s:
                held_on.setdefault(c, set()).add(dt)
        return {c for c, days in lud.items()
                if days & held_on.get(c, frozenset())}
    if g.lu_need_held and not g.hold_history:
        # 原版：没有持仓历史（建仓日）时交集恒空 —— 连查询都不必发
        return set()
    recent = context.data.had_limit_up(cand, lo, d)
    if not g.lu_need_held:
        return set(recent)
    return set().union(*g.hold_history) & recent


def rebalance(context):
    d = context.previous_date
    if d is None or g.pf_halt:
        return
    # 补位模式要更宽的池子：过滤发生在截断【之前】，池子不够就补不满。
    # ★ fill_blacklist 与 fill_paused 走同一条路：把池子放宽到 fill_pool，
    #   于是过滤（含黑名单）发生在**截断之前**，target 自然补满 stock_num。
    lim = (g.fill_pool if (g.fill_paused or g.fill_blacklist)
           else g.candidate_num)
    # ★ 查得比用得多几名，多出来的只给"选股理由"看（见 g.explain_pool）
    _ask = max(lim, g.explain_pool) if g.explain_pool else lim
    df = context.data.query(
        SQL, sd=d, listed=g.listed_days, cand=_ask,
        pin='TRUE' if g.paused_in_pool else 'FALSE',
        kcb='688%' if g.kcb_688_only else '68%',
        pert=g.pert, salt=g.pert_salt, skip=g.skip_n,
        pbcut=(str(int(g.pb_fixed)) if g.pb_fixed else 'floor(0.5 * n)'),
        roecut=(str(int(g.roe_fixed)) if g.roe_fixed else 'floor(0.1 * n2)'),
        excl=','.join("'%s'" % x for x in EXCL_IND))
    # 🔴 **策略只用前 lim 个** —— 多取的那几名只是给"选股理由"看的。
    #   少了这一刀就等于开了补位（过滤在截断之前），行为会变。
    cand = df['jq_code'].tolist()[:lim]
    if not cand:
        return
    # 顺序与聚宽 FROEC 原版一致：
    #   get_stock_list(context)[:10] -> filter_paused/limitup/limitdown -> 黑名单 -> 截断
    # 注意 FROEC 是【先截到 10 再过滤、不补位】(candidate_num=10)，
    # 而 v0b 是【候选 15 -> 过滤 -> 黑名单 -> 最后截到 10】(candidate_num=15)。
    # 两条线的原版写法不同，不能互相照抄。
    cand = stop_filter(context, context.tradable(cand, 'buy'))
    _drop = blacklist(context, cand, d)
    # ★ 两个判据分开取，因为它们问的是两件事（见 g.lu_buy_only 那段注释）：
    #     卖出问"它还在前 stock_num 名里吗"   -> 不过黑名单
    #     买入问"它是否被黑名单挡住"           -> 过黑名单
    #   lu_buy_only=0（原版）时 _rank_pool 不参与任何判断，行为一行不变。
    _rank_pool = set(cand[:g.stock_num]) if g.lu_buy_only else None
    if _drop:
        cand = [c for c in cand if c not in _drop]
    target = cand[:g.stock_num]

    # ---- 缓冲区：已持有的票排名还够就不卖 ----
    # ★ 判据用【原始排名】（不过 tradable/stop_filter）—— 卖出问的是
    #   "它还够好吗"，不是"今天能不能买"。涨停豁免仍走 g.high_limit。
    keep = set()
    if g.hold_buffer and context.portfolio.positions:
        wide = context.data.query(
            SQL, sd=d, listed=g.listed_days,
            cand=g.stock_num + g.hold_buffer,
            pin='TRUE' if g.paused_in_pool else 'FALSE',
            kcb='688%' if g.kcb_688_only else '68%',
            pert=g.pert, salt=g.pert_salt, skip=g.skip_n,
            pbcut=(str(int(g.pb_fixed)) if g.pb_fixed else 'floor(0.5 * n)'),
            roecut=(str(int(g.roe_fixed)) if g.roe_fixed else 'floor(0.1 * n2)'),
            excl=','.join("'%s'" % x for x in EXCL_IND))['jq_code'].tolist()
        if g.hold_buffer_strict:
            wide = stop_filter(context, wide)
            _wdrop = blacklist(context, wide, d)
            if _wdrop:
                wide = [c for c in wide if c not in _wdrop]
        buf = set(wide[:g.stock_num + g.hold_buffer])
        keep = {c for c in context.portfolio.positions
                if c in buf and c not in target}
        if keep:
            log.info('[BUF] 缓冲区留下 %d 只：%s', len(keep), sorted(keep))

    if g.lu_buy_only and _rank_pool:
        # 排名还够、只是被黑名单挡住的持仓 -> 继续持有（黑名单不卖票）
        _bk = {c for c in context.portfolio.positions
               if c in _rank_pool and c not in target}
        if _bk:
            keep = set(keep) | _bk
            log.info('[LU] 黑名单只挡买入，留下 %d 只：%s', len(_bk), sorted(_bk))
    for code in list(context.portfolio.positions):
        if (code not in target and code not in g.high_limit
                and code not in keep):
            order_target_value(code, 0)

    # ★ 买入名额的分母是【目标池实际长度】，不是 g.stock_num。
    #   聚宽原版：value = cash / (target_num - position_count)，
    #   其中 target_num = len(g.target_list) —— 20 日黑名单剔除后可能 < 10。
    #   写成 g.stock_num 会把钱多分一份，实测 2019-04-15 起持仓即分歧。
    if g.buy_delay:
        g.pending = target          # 买入腿推迟，见 rebalance_buy
        return
    _do_buy(context, target)


def _do_buy(context, target):
    need = [c for c in target if c not in context.portfolio.positions]
    need = need[:max(0, len(target) - len(context.portfolio.positions))]
    if need:
        per = context.portfolio.cash / len(need)
        for code in need:
            order_target_value(code, per)


def rebalance_buy(context):
    """延迟的买入腿。目标池是【09:30 决策时】定下的，不重新选股 ——
    只把成交推后，这样测出来的差异纯粹来自价格漂移。"""
    if not g.buy_delay or not g.pending:
        return
    _do_buy(context, g.pending)
    g.pending = []


def check_limit_up(context):
    if not g.limit_up_exit or not g.high_limit:
        return
    codes = [c for c in g.high_limit if c in context.portfolio.positions]
    if not codes:
        return
    # 今天的状态走 context.current() —— 历史接口取不到今天（PIT 防火墙）。
    # 14:00 属 INTRADAY 阶段，limit_up 可见（与「盘中成交价用收盘价代理」的约定一致）。
    cur = context.current(codes)
    for code in codes:
        d = cur.get(code)
        if d is None:
            continue
        # ★ 按相位取字段：OPEN 阶段没有 limit_up（那是收盘派生量），
        #   直接取会得 None -> `not None` 为真 -> 把持仓全卖掉。
        sealed = d.get('limit_up') if 'limit_up' in d else d.get('open_limit_up')
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
