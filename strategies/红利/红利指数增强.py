#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""【红利指数增强】双 sleeve 并集：红利低波(A) + 红利价值(B)

★★ 实际要跑的配置是 **div_method=fiscal_year**（= JQ v7），不是默认值：

      python3 run.py strategies/红利/红利指数增强.py \
        --start 2016-01-01 --end 2026-06-30 --cash 1000000 \
        --param div_method=fiscal_year

   默认之所以留 rolling365，只是为了和 JQ 版本谱对齐（默认 = v4/v5，
   显式选 fiscal_year = v7），这样 §三 版本映射与 §四 参数敏感性两张表
   的参照点不变。**不是**因为 rolling365 更好。

   fiscal_year 是【正确性修复】(A-1) 而不是参数调优：rolling365 按股权
   登记日滚动 365 天求和，实施日在年度间漂移就会让窗口内出现两次或零次
   年度分红；公司从年派改半年派时（2024 年后大量银行/央企如此）还会混入
   不同归属期的分红。JQ 实测工商银行 2025-06-01 虚高 +46%、
   2026-06-01 虚高 +53%（真实年度分红 3.035→3.064→3.080→3.103 很稳定）。

   本地实测（2016-01-01~2026-06-30，100 万，引擎默认成本）**五指标全胜**：
      rolling365   年化 17.43%  回撤 22.04%  夏普 1.15  索提诺 1.82  卡玛 0.79
      fiscal_year  年化 20.07%  回撤 17.95%  夏普 1.30  索提诺 2.11  卡玛 1.12
   收益提升是副产物，采纳理由是"另一个算法是错的"。

复刻 JQ/红利/红利指数增强_v*.txt 的选股结构。JQ 实测（分析结论.md）：

| 版本 | 持仓 | 炸板 | 年化 | 夏普 | IR | 回撤 |
|---|---|---|---|---|---|---|
| A 原版 | 8 | 有 | 21.49% | 1.084 | 1.790 | 27.00% |
| B 关闭炸板 | 8 | 无 | 19.37% | 0.904 | 1.529 | 27.00% |
| D 再入场 | 8 | 有+再入场 | 22.41% | 1.112 | 1.866 | 27.00% |

## 结构

    Sleeve A（红利低波）: 全池 -> 股息率 top10% 且 >3% -> beta 升序 -> 取 g.num_a
    Sleeve B（红利价值）: 全池 -> 基本面四条 -> 股息率 top10% 且 >3% -> 取 g.num_b
    并集去重，**A 优先**（原版 A-5 明确要求确定性保序，替代 list(set()) 的不确定顺序）

原版 TARGET_NUM=[5,3]（合 8 只）；v3 起改 [10,5]（约 15 只）。
两者都做成参数，可直接对比 —— 分析结论.md 说 15 只的预期年化 15.4% 而非回测的 22.41%。

## 已知与原版的实现差异（都已量化，不藏）

1. **beta 本地算**（`std/beta_daily.parquet`，沪深300 滚动回归）。
   sweep 实测 252 天窗口最接近聚宽：红利低波单跑 10.75% vs JQ 11.27%（差 0.52pp）；
   60/120 天分别是 1.81%/6.47%，差很远 —— 所以窗口是敏感参数，默认 252。
2. **炸板离场用日线代理**：原版 10:00 取那一刻的分钟价，我们只有日线，
   用「今日是否仍收在涨停价」代替。小市值线实测这个代理值 0.3~1.0pp。
3. **v7 的 `DIV_METHOD='fiscal_year'`（按会计年度汇总 + 800 天回溯）未实现** ——
   本文件用的是 365 天滚动窗口（原版/v3~v5 的口径）。v7 那个改动会改变股息率排序，
   属于另一个口径，不在这里混进来。
"""
# 版本一句话简介 —— 看板在版本号右边展示它。
# ★ 改动策略行为时【必须同步改这句】：版本 = 代码哈希，
#   简介是人识别两个版本差异的唯一线索。
# ★ 本策略依赖 beta_252 —— **回测起点不要早于 2006-01-01**。
#   沪深300 首个发布值在 2005-01-04（该指数在那之前不存在），
#   252 日满窗口要到 2005-08 才有 98% 覆盖（2005-01~06 是 0%）。
#   起点更早会静默欠配：实测 2005 年平均仓位仅 74.3%，指标被现金稀释。
#   报告里的「平均仓位」一栏会暴露这个问题，引用结论前先看它。
NOTE = '低波(A)+价值(B) 双袖并集去重（A 优先）· 默认 [10,5]（非 JQ 原版 [5,3]，实测五项全胜）· 仓位只把腾出的现金分给新票'

from assay.api import *

UNIVERSE = ("NOT is_risk_warned AND listed_days >= 250 AND list_date IS NOT NULL "
            "AND jq_code NOT LIKE '68%' AND jq_code NOT LIKE '4%' "
            "AND jq_code NOT LIKE '8%' AND totalmv > 0")

# Sleeve A：高股息 -> 低 beta
DIV_ROLLING365 = """
  SELECT code, sum(bonus_amount_rmb) * 1e4 AS amt
  FROM read_parquet('{root}/std/dividend.parquet')
  WHERE a_registration_date >= DATE '{t1}' - INTERVAL 365 DAY
    AND a_registration_date <= DATE '{t1}' AND bonus_amount_rmb > 0
  GROUP BY 1
"""

# v7 / A-1+A-6：按【分红归属会计年度】汇总，而不是按登记日滚动 365 天。
#   原实现两个缺陷：① 实施日在年度间漂移几周 -> 窗口里出现两次或零次年度分红；
#   ② 公司从年派改半年派时（2024 后大量银行/央企）窗口混入不同归属期。
#   去重：同一 (code, report_date, bonus_type) 只留流程最靠后的一条 —— JQ 的
#   「董事会预案」记录数是实际事件数的 8.3 倍，不去重会让股息率虚高，且 99.8%
#   集中在中期分红（2024 后的银行/央企），正是策略重仓处。
#   可见性用 board_plan_pub_date（董事会预案即公开信息，非未来函数）。
DIV_FISCAL_YEAR = """
  WITH raw AS (
    SELECT code, report_date, bonus_amount_rmb,
           row_number() OVER (
             PARTITION BY code, report_date, bonus_type
             ORDER BY CASE plan_progress
                        WHEN '实施方案'     THEN 3
                        WHEN '股东大会预案' THEN 2
                        WHEN '董事会预案'   THEN 1
                        ELSE 0 END DESC,
                      board_plan_pub_date DESC) AS r
    FROM read_parquet('{root}/std/dividend.parquet')
    WHERE board_plan_pub_date <= DATE '{t1}'
      AND board_plan_pub_date >= DATE '{t1}' - INTERVAL 800 DAY
      AND bonus_cancel_pub_date IS NULL
      AND (plan_progress IS NULL OR plan_progress NOT IN ('终止', '取消分红'))
      AND bonus_amount_rmb > 0
  ), dedup AS (
    SELECT * FROM raw WHERE r = 1
  ), fy AS (
    -- 年度锚定用 report_date 月份==12，不依赖 bonus_type 文本标签
    SELECT code, max(year(report_date)) AS y
    FROM dedup WHERE month(report_date) = 12 GROUP BY 1
  )
  SELECT d.code, sum(d.bonus_amount_rmb) * 1e4 AS amt
  FROM dedup d JOIN fy ON fy.code = d.code AND year(d.report_date) = fy.y
  GROUP BY 1
"""

SQL_A = """
WITH div AS ({div}
), base AS (
  SELECT p.jq_code, d.amt / p.totalmv AS dy,
         row_number() OVER (ORDER BY d.amt / p.totalmv DESC) AS rk,
         count(*) OVER () AS n
  FROM {panel} p JOIN div d ON d.code = p.jq_code
  WHERE p.date = DATE '{t1}' AND {uni}
), hi AS (
  SELECT jq_code, dy FROM base
  WHERE rk <= greatest(1, floor({dtop} * n)) AND dy > {dmin}
), ranked AS (
  SELECT h.jq_code,
         row_number() OVER (ORDER BY b.beta_{bw} ASC) AS rk,
         count(*) OVER ()                            AS bn
  FROM hi h
  JOIN read_parquet('{root}/std/beta_daily.parquet') b
    ON b.code = h.jq_code AND b.date = DATE '{t1}'
  WHERE b.beta_{bw} IS NOT NULL
)
-- B-3：原版先砍掉 beta 高的一半（p2=0.50）再取前 N。num_a=5 时池子>=10 就
-- 不 binding（死参数）；num_a=10 时需池子>=20，否则欠配。v4 起删除该截断。
SELECT jq_code FROM ranked
WHERE rk <= greatest(1, floor({bpct} * bn))
ORDER BY rk
"""

# Sleeve B：基本面四条 -> 高股息
SQL_B = """
WITH div AS ({div}
), ind AS (
  SELECT code, inc_return, inc_total_revenue_year_on_year AS inc_rev,
         inc_net_profit_year_on_year AS inc_np,
         row_number() OVER (PARTITION BY code ORDER BY report_date DESC) rn
  FROM read_parquet('{root}/std/fin_indicator_q.parquet')
  WHERE pub_date <= DATE '{t1}'
), fund0 AS (
  -- PE 带保持绝对值：它是【有效性】过滤（剔负 PE 与泡沫），不是排序因子
  SELECT p.jq_code, p.totalmv, i.inc_return, i.inc_rev, i.inc_np FROM {panel} p
  JOIN ind i ON i.code = p.jq_code AND i.rn = 1
  WHERE p.date = DATE '{t1}' AND {uni}
    AND p.pe_ttm BETWEEN {pe_lo} AND {pe_hi}
), fund AS (
  -- b_mode='abs' 原版：绝对阈值。
  --   ★ std 层比率存【小数】，所以阈值 ÷100（原版是百分数 between 5 and 100）
  --   实测问题：inc_return 中位数只有约 1%，>=5% 这个阈值本身就相当于取前 10%。
  --   四条过滤把池子从约 2000 压到 55 只，再取股息率 top10% 只剩 5 只 ——
  --   而 num_b=5，所以 B 袖选择率 83%（A 袖 3.3%），num_b 近乎死参数。
  -- b_mode='pct' 变体：三条质量/成长条件改成【横截面分位】，各取前 b_*_pct。
  --   PE 带不动（见上）。分位在 PE 过滤【之后】的池内计算。
  --
  -- 🔴 实测结论：分位版【确实修好了供给问题，但收益更差】——
  --   （2016-01-01~2026-08-20，100 万，默认成本，div_method=fiscal_year）
  --     abs 原版    21.34% / 回撤 17.95% / 夏普 1.37   B池中位 6  选择率 71.7%
  --     pct 前30%   17.76% / 21.27% / 1.14            B池中位 12 选择率 40.0%
  --     pct 前20/40/50%  13.76 / 16.43 / 14.23%（全部落后，且非单调）
  --     pct30 + num_b=8/12  15.14 / 15.71%（放大 B 份额只会更差）
  --   => 绝对阈值在【质量类因子】上是特性不是缺陷：inc_return >= 5% 强制的是
  --      「绝对盈利水平下限」，而分位版只看相对排名 —— 全市场盈利普遍恶化时，
  --      分位版照样放进前 30%（可能只有 1% 的 ROE），绝对版则正确地把池子收紧。
  --      【池子在坏年份变小是过滤器在工作，不是失灵。】
  --   ★ 我最初把「池子规模随市场漂移」当成毛病去修，方向就错了。
  --      供给端指标（池子大小、选择率）改善 ≠ 策略改善 —— 这两件事要分开看。
  SELECT jq_code, totalmv FROM (
    SELECT *,
           percent_rank() OVER (ORDER BY inc_return) pr_roe,
           percent_rank() OVER (ORDER BY inc_rev)    pr_rev,
           percent_rank() OVER (ORDER BY inc_np)     pr_np
    FROM fund0
  ) WHERE CASE WHEN '{bmode}' = 'pct'
               THEN pr_roe >= 1 - {broe} AND pr_rev >= 1 - {brev} AND pr_np >= 1 - {bnp}
               ELSE inc_return BETWEEN {roe_lo} AND {roe_hi}
                    AND inc_rev BETWEEN {rev_lo} AND {rev_hi}
                    AND inc_np BETWEEN {np_lo} AND {np_hi}
          END
), base AS (
  SELECT f.jq_code, d.amt / f.totalmv AS dy,
         row_number() OVER (ORDER BY d.amt / f.totalmv DESC) AS rk,
         count(*) OVER () AS n
  FROM fund f JOIN div d ON d.code = f.jq_code
)
SELECT jq_code FROM base
WHERE rk <= greatest(1, floor({dtop} * n)) AND dy > {dmin}
ORDER BY dy DESC
"""


def initialize(context):
    # ★★ 默认 [10,5]，**不是 JQ 原版的 [5,3]** —— 这是本项目实测后的选择。
    #   2016-2025 · warmup=1 · v7 口径 · 11 组配比扫描，[10,5] 五项全胜：
    #       年化 +2.49pp / 回撤 -3.53pp / 夏普 +0.20 / IR +0.47 / 换手 -0.17
    #   且 [10,5] 是 11 组中的第一名（24.41%），num_a=10 的三组占据前三。
    #   规律：num_a 太小(5/8) 回撤恶化到 19.5~21.2%、单票峰值冲到 30~37%；
    #         num_a 太大(15/20) 收益递减（20.8 -> 18.4%），过度分散。
    #         num_b 在 3~8 间几乎无差别（极差 0.64pp）—— 价值袖池子常给不满，
    #         加大 num_b 也买不到更多票（这也解释了 v6b 与 v6 差异极小）。
    #   与 JQ「修复与优化记录.md §35」方向一致：它论证 [5,3]->[10,5] 使调仓日
    #   极差 12.10pp -> 3.32pp（稳健性），代价 0.93pp 年化；我们这边年化不但
    #   没损失反而 +2.49pp，差别应来自 v7 分红口径（JQ 那个基线用 rolling365）。
    #
    # ⚠️ 因此【默认配置不再等于 JQ 原版】。要复现 JQ 原版/对标，显式传参：
    #       --param num_a=5 --param num_b=3 --param beta_pct=0.50 \
    #       --param div_top_pct=0.10 --param div_method=rolling365
    g.num_a = getattr(g, 'num_a', 10)  # 原版 5；实测 10 更优（见上）
    g.num_b = getattr(g, 'num_b', 5)   # 原版 3；实测 5 更优（3~8 差异 <0.7pp）
    g.backup_a = getattr(g, 'backup_a', 5)
    g.backup_b = getattr(g, 'backup_b', 5)
    g.div_min = getattr(g, 'div_min', 0.03)
    g.div_top_pct = getattr(g, 'div_top_pct', 0.10)
    g.beta_win = getattr(g, 'beta_win', 252)
    g.limit_up_exit = getattr(g, 'limit_up_exit', 1)
    g.reentry = getattr(g, 'reentry', 1)
    g.monthday = getattr(g, 'monthday', 1)  # 月内第几个交易日调仓
    g.equal_weight = getattr(g, 'equal_weight', 0)
    g.beta_pct = getattr(g, 'beta_pct', 1.0)          # B-3: 0.50=原版/v2/v3, 1.0=v4 起
    g.div_method = getattr(g, 'div_method', 'rolling365')  # A-1/A-6: rolling365 / fiscal_year
    # ---- B-1：Sleeve B 的 8 个 between 阈值 ----
    # JQ 把这 8 个数标为「拍的、有过拟合风险」，但任何地方都没给细节，也从未修过。
    # 参数化只为做敏感性 —— 不是修 bug，默认值严格等于原版：
    #   pe_ratio                        between 5   and 50
    #   inc_return（扣非ROE, %）          between 5   and 100
    #   inc_total_revenue_year_on_year  between 5   and 100
    #   inc_net_profit_year_on_year     between 10  and 100
    # ★ 本地 std 层比率存【小数】，所以 inc_* 三项阈值要 ÷100
    #   （曾照抄 5/100 选出 0 只票才发现）。pe 不用除。
    g.pe_lo = getattr(g, 'pe_lo', 5.0)
    g.pe_hi = getattr(g, 'pe_hi', 50.0)
    g.roe_lo = getattr(g, 'roe_lo', 0.05)
    g.roe_hi = getattr(g, 'roe_hi', 1.00)
    g.rev_lo = getattr(g, 'rev_lo', 0.05)
    g.rev_hi = getattr(g, 'rev_hi', 1.00)
    g.np_lo = getattr(g, 'np_lo', 0.10)
    g.np_hi = getattr(g, 'np_hi', 1.00)
    # B 袖阈值口径：'abs' 绝对阈值（原版，用于对标）/ 'pct' 横截面分位
    #   分位版保留各条件的【相对严格度】而不锁死绝对水平，池子规模不随
    #   全市场盈利水平漂移。三条各取前 b_*_pct（默认 0.30，三条独立叠加后
    #   池子规模与原版量级相当）。
    g.b_mode = getattr(g, 'b_mode', 'abs')
    g.b_roe_pct = getattr(g, 'b_roe_pct', 0.30)
    g.b_rev_pct = getattr(g, 'b_rev_pct', 0.30)
    g.b_np_pct = getattr(g, 'b_np_pct', 0.30)
    # ---- B-1 的三种口径（g.b1_mode）----
    # JQ「修复与优化记录.md §216」把这 8 个阈值标为 🔴 最大过拟合源，并给了修法：
    #   · 删所有上界：三个 100 完全相同（复制粘贴痕迹），扣非ROE>100% 近乎不存在
    #     -> 基本不 binding，纯粹是「剔除样本内异常值」的过拟合手法
    #   · 统一下界：5/5/10 无理论依据，是逐个手调的痕迹，统一成「只要正增长」
    #   · 8 个参数 -> 1~2 个
    # ★ 验收判据是文档指定的：**极差不变（<=3.5pp）+ 参数变少 = 通过**，
    #   不看收益。文档明言「年化大概率低于当前，这是预期内的也是正确的 ——
    #   现在的数字里包含多余参数在样本内的拟合收益」。
    #   'orig'    8 参数，严格等于 JQ 原版（默认）
    #   'no_hi'   删掉三个上界（100/100/100），保留下界 5/5/10 —— 6 -> 5 个参数
    #   'simple'  删上界 + 下界统一为「正增长」-> 只剩 pe 两个界，8 -> 2 个参数
    g.b1_mode = getattr(g, 'b1_mode', 'orig')
    # ---- A-4：buy_list 为空时的现金闲置（g.idle_fix）----
    # JQ「修复与优化记录.md §212」列为待做项，但 JQ 自己【从未修】：
    #   trade 里 buy_list 为空而 sell_list 非空时，卖出的钱闲置到下月。
    #   与已修的「炸板卖出后现金闲置」同类（那个在 再入场.txt 里修了）。
    # 0 = 原版行为（默认，保真）；1 = 把闲置现金按等权补进已持有的目标票。
    # ★ 只有在「本月无新票可买、但卖掉了非目标票」时才有差别 —— 频率不高，
    #   所以预期效应很小；做成参数是为了能【测出来】而不是猜。
    g.idle_fix = getattr(g, 'idle_fix', 0)
    # B-1 口径：把上界/下界按 b1_mode 覆写（默认 orig 时保持原值不动）
    _BIG = 1e9
    if g.b1_mode == 'no_hi':
        g.roe_hi = g.rev_hi = g.np_hi = _BIG
    elif g.b1_mode == 'simple':
        g.roe_hi = g.rev_hi = g.np_hi = _BIG
        g.roe_lo = g.rev_lo = g.np_lo = 0.0   # 只要正增长

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
    g.pf_bench = getattr(g, 'pf_bench', '000015.XSHG')
    g.pf_halt = False
    g.pf_wait_left = 0
    g.pf_peak = 0.0
    g.n_pf_stop = 0
    g._bench_px = None
    g.pos_state = {}
    g.n_stop = 0
    g.target_list = getattr(g, 'target_list', [])
    g.backup_list = getattr(g, 'backup_list', [])
    g.high_limit = getattr(g, 'high_limit', set())
    g.blacklist = getattr(g, 'blacklist', set())
    g.n_exit = 0
    g.n_reentry = 0
    set_benchmark('000015.XSHG')
    run_daily(prepare, time='09:00')
    run_monthly(pick, monthday=g.monthday, time='09:01')
    run_monthly(trade, monthday=g.monthday, time='09:30')
    run_daily(check_limit_up, time='10:00')
    run_daily(stop_check, time=g.stop_time)
    run_daily(pf_check, time=g.stop_time)


def prepare(context):
    g.high_limit = set()
    held = list(context.portfolio.positions)
    if held and context.previous_date:
        bars = context.data.bars(context.previous_date, held)
        g.high_limit = {c for c, b in bars.items() if b.limit_up}


def pick(context):
    d = context.previous_date
    if d is None:
        return
    div_cte = DIV_FISCAL_YEAR if g.div_method == 'fiscal_year' else DIV_ROLLING365
    # ★ 用字面 replace 而不是 format 注入 CTE：CTE 里还有 {root}/{t1}，
    #   必须留给 query() 在同一次 substitute 里解析（format 只走一遍）。
    sql_a = SQL_A.replace('{div}', div_cte)
    sql_b = SQL_B.replace('{div}', div_cte)
    kw = dict(t1=d, uni=UNIVERSE, dmin=g.div_min, dtop=g.div_top_pct,
              pe_lo=g.pe_lo, pe_hi=g.pe_hi, roe_lo=g.roe_lo, roe_hi=g.roe_hi,
              rev_lo=g.rev_lo, rev_hi=g.rev_hi, np_lo=g.np_lo, np_hi=g.np_hi,
              bmode=g.b_mode, broe=g.b_roe_pct, brev=g.b_rev_pct, bnp=g.b_np_pct)
    a = context.data.query(sql_a, bw=g.beta_win, bpct=g.beta_pct, **kw)['jq_code'].tolist()
    b = context.data.query(sql_b, **kw)['jq_code'].tolist()
    la, lb = a[:g.num_a], b[:g.num_b]
    # 确定性去重保序，A 优先（原版 A-5：替代 list(set()) 的不确定顺序）
    tgt = []
    for s in la + lb:
        if s not in tgt:
            tgt.append(s)
    bk = []
    for s in a[g.num_a:g.num_a + g.backup_a] + b[g.num_b:g.num_b + g.backup_b]:
        if s not in tgt and s not in bk:
            bk.append(s)
    g.target_list, g.backup_list, g.blacklist = tgt, bk, set()
    log.info('[PICK] %s A池%d取%d / B池%d取%d -> 并集 %d 只',
             d, len(a), len(la), len(b), len(lb), len(tgt))


def trade(context):
    if g.pf_halt or not g.target_list:
        log.warn('目标池为空，跳过调仓')
        return
    tgt = set(stop_filter(context, g.target_list))
    held = set(context.portfolio.positions)
    # ---- 仓位口径（g.equal_weight）----
    # 0 = 原版：只把腾出来的现金平分给【新买入】的票，已持有仓位一律不动
    #     → 反复入选的票权重持续变大（赢家滚雪球）
    # 1 = 等权再平衡：每次调仓把所有目标拉回 total_value/N（会削超配）
    if g.equal_weight:
        per = context.portfolio.total_value / len(g.target_list)
        for s in list(context.portfolio.positions):
            if s not in tgt and s not in g.high_limit:
                order_target_value(s, 0)
            elif s in tgt and context.portfolio.positions[s].value > per:
                order_target_value(s, per)
        for s in g.target_list:
            order_target_value(s, per)
        return
    # ★ 昨日涨停的持仓【豁免】本次调仓卖出，与原版
    #   g.sell_list = [s for s in hold_list if s not in target and s not in high_limit_list] 一致
    for s in list(context.portfolio.positions):
        if s not in tgt and s not in g.high_limit:
            order_target_value(s, 0)
    # ★ 原版**不做等权再平衡**：只把腾出来的现金平分给【新买入】的票，
    #   已持有的仓位一律不动，反复入选的票权重会持续变大（赢家滚雪球）。
    #   照抄「傻瓜基准」的 total_value/N + 削超配会低估 8.6pp。
    buy = [s for s in g.target_list if s not in held]
    if not buy:
        # A-4：原版在此直接 return，卖出所得闲置到下月。
        if g.idle_fix and g.target_list:
            held_tgt = [s for s in g.target_list if s in context.portfolio.positions]
            if held_tgt and context.portfolio.cash > 0:
                add = context.portfolio.cash / len(held_tgt)
                for s in held_tgt:
                    order_target_value(s, context.portfolio.positions[s].value + add)
        return
    per = context.portfolio.cash / len(buy)
    for s in buy:
        order_target_value(s, per)


def check_limit_up(context):
    if not g.limit_up_exit:
        return
    codes = [c for c in g.high_limit if c in context.portfolio.positions]
    if not codes:
        return
    cur = context.current(codes)
    sold = 0
    for s in codes:
        d = cur.get(s)
        if d is not None and not d.get('limit_up'):
            order_target_value(s, 0)
            g.blacklist.add(s)
            g.n_exit += 1
            sold += 1
    if g.reentry and sold:
        held = set(context.portfolio.positions)
        cands = [s for s in g.target_list if s not in held and s not in g.blacklist]
        cands += [s for s in g.backup_list
                  if s not in held and s not in g.blacklist and s not in cands]
        cands = context.tradable(cands, 'buy')[:sold]
        if cands:
            per = context.portfolio.cash / len(cands)
            for s in cands:
                order_target_value(s, per)
                g.n_reentry += 1


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
