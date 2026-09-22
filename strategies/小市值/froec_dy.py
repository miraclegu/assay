# -*- coding: utf-8 -*-
"""FROEC-DY：把 FROEC 的「PB 低半区 + ROE 加速度前 10%」**整段换成股息率筛选**。

用户 2026-09-22：「写几个新策略，除筛选规则外（替换掉现有的 pb+roe 的筛选
规则），其他规则都同现有的实盘 froec_traded 一致。策略一：股息率大于 2%
作为筛选规则；策略二：连续两年股息率大于 2%。」

    FROEC      在册宇宙 -> eps>0/非ST -> **PB 低半区 -> ROE 加速度前 10%**
                        -> 排除周期与金融 -> 按流通市值升序取前 10
    本版       在册宇宙 -> eps>0/非ST -> **股息率 > dy_min**
                        -> 排除周期与金融 -> 按流通市值升序取前 10

其余一个字都没动：调仓相位（周内第 2 个交易日）、20 日涨停黑名单、
炸板离场、止损、买入腿的资金分配、不补位 —— 全部继承 `froec_traded`。

参数（`--param` 传）：

    dy_min    默认 0.02    股息率阈值
    dy_years  默认 1       1 = 只看最近一个完整会计年度；**2 = 连续两年都要过**
    dy_win    默认 1200    分红可见性回溯窗口（天）

★ `froec_dy2.py` 就是本文件 `dy_years=2`（私有加载 + 只改一个默认值）——
  两个文件抄同一段 SQL 的话迟早分叉，而分叉的那份看着完全正常。

--------------------------------------------------------------------------
🔴 **实现：不复制 froec 的 SQL，在【参数流过的最后一刻】改它**

复制那 50 行宇宙 SQL（univ / today / miss / eps1 / base）是最省事的做法，
然后 froec 修一次 bug 本版不会跟随，**而那不报错**。所以走
`froec_cutgrid` 那条路：包住 `context.data.query`，只做三件事 ——

  ① `pbcut='n'` / `roecut='n2'`  -> `rn <= n` / `rn2 <= n2` **恒真**，
     两道筛子变成空操作（它们本来就是字符串模板参数）
  ② `roec` 那个 **INNER JOIN 改成 LEFT JOIN**
     🔴 这一步不能省：内连接**顺带要求"有 5 期 ROE 数据"**，而那正是
       要被换掉的规则的一部分。实测它剔掉 0.8%~3.1% 的票，
       且被剔的多是次新股 —— 而本版按流通市值升序取，次新股正排在前面。
       只改两个 cut 而留着这个 join，等于"ROE 规则删了一半"，**且不报错**。
     ★ 锚点必须**命中恰好一次**，否则直接抛 —— froec 改写法时要响亮失败，
       而不是静默退回旧语义（同「保护分支不该静默跳过」那条）。
  ③ 拿回**全量**候选（`cand` 放到极大、`skip` 留到筛完再切），
     用股息率筛一遍，再按原来的 `skip`/`cand` 切 ——
     顺序仍是「先筛、再按市值升序截断、不补位」，与原版一致。

--------------------------------------------------------------------------
🔴 股息率的口径（三条，每条都踩过坑）

**① 用【最近一个完整会计年度】合计，不用近 365 天。**
  与 `alerts.py` 的 `suggest_div` 默认值、红利策略的 `DIV_FISCAL_YEAR`
  同一条：`r365` 在半年派的公司上会**漏掉中期分红**（2024 年后大量银行/
  央企改半年派），实测少 26%~41%，**而它不报错**，只是股息率算低了。
  去重也照抄那条：同一 `(code, report_date, bonus_type)` 只留流程最靠后的
  一条（「董事会预案」记录数是实际事件数的 8.3 倍）。
  可见性用 `board_plan_pub_date`（董事会预案即公开信息，非未来函数）。

**② 🔴🔴 必须挡掉【过期的会计年度】。**
  `max(year(report_date))` 取的是"最近一个有年度分红的会计年度" ——
  一家 2020 年之后再没分过红的公司，2026 年查出来仍然是 FY2020，
  于是「FY2020 的分红 ÷ 今天的市值」被当成"股息率 2%"用。
  **实测这类占通过阈值那批的 10%~15%**，不是边角料。
  判据按月份给（年报分红集中在 4~6 月公告）：

      决策日 7 月及以后 -> 只认 y >= 当年 - 1
      决策日 7 月以前   -> 只认 y >= 当年 - 2（FY(Y-1) 可能还没公告）

  加了这条之后 2026-09-01 的通过数 999 -> 891。

**③ 分母用【总市值】不是流通市值。** 分红是按总股本派的，
  拿 floatmv 当分母会让限售股多的公司股息率虚高，**而它不报错**。
  停牌股当天没有面板行，所以 totalmv 走 ASOF join 结转
  （与 froec 的 `miss` 同一手法）。

⚠ **两年那档的分母是【同一个】今天的总市值**，不是各自年份的市值。
  也就是说它问的是"按今天的市值算，这两年的分红各自都值 2% 以上"。
  换成各年自己的市值的话，这条筛选会掺进**两年前的价格**（那时跌得多的
  公司更容易"通过"）—— 那是价格因子，不是分红持续性。这是明知的取舍。

--------------------------------------------------------------------------
🔴🔴 结论：**不采纳**（2026-09-22 实测，全程 + 逐年独立 + 两个对照组）

全程连跑 2016-01-01 ~ 2026-09-22，10 万，默认成本，实盘那套参数：

    配置                        年化     回撤    夏普   平仓  年换手      期末
    基线 froec_traded        45.00%  47.29%  1.416   958  10.68  536.2 万
    对照A 纯小市值（两道都不筛）  31.45%  47.82%  1.072   736   7.36  187.4 万
    对照B 有分红即可（无阈值）    21.44%  49.59%  0.793   834   7.95   80.2 万
    策略一 dy>2%             24.54%  45.30%  0.937   785   8.45  105.0 万
    策略二 连续两年>2%         26.52%  42.26%  1.037   799   8.02  124.3 万

    逐年独立（每年重置 10 万，11 年）—— 与基线的配对差
    策略一  均差 -27.45pp 中位 -23.28 胜 1/11 sd 24.14 t=-3.77
    策略二  均差 -23.20pp 中位 -16.75 胜 0/11 sd 19.82 t=-3.88
    亏损年  基线 0/11 ｜ 一 3/11 ｜ 二 2/11        平均年内回撤 19.01 / 21.27 / 19.89%

**|t| 都 > 2.23 且胜 1/11、0/11 —— 这是本项目少见的"显著更差"。**
负向结论比正向结论更可信（同「尾盘买入」那轮）：正向要防幸存者偏差，
而这里是"换掉之后一致变差"。

#### 🔴 两个对照组把损失拆成两层，而**单层都不显著、合起来才显著**

    基线 45.00 ──拆掉 PB+ROE──> A 31.45 ──加"必须有分红"──> B 21.44
                                      └──加 dy>2%──> 24.54  └─连两年─> 26.52

    逐年独立配对差（11 年）
      A 纯小市值 − 基线        均差 -12.82pp  胜 3/11  t=-1.14   <- 不显著
      B 有分红即可 − A         均差  -7.86pp  胜 3/11  t=-1.11   <- 不显著
      一 dy>2% − A            均差 -14.63pp  胜 5/11  t=-1.67   <- 不显著
      二 连两年>2% − A         均差 -10.38pp  胜 5/11  t=-1.32   <- 不显著

★ **两层各自都撑不起结论，合起来（一/二 vs 基线）才显著** ——
  11 年样本对 10pp 级的效应无能为力，而 25pp 级的够了。
★ 但**方向在两个口径上一致**：任何形式的股息率筛选都落在 A（纯小市值）
  之下，全程与逐年都是。所以能说的是「在小市值池里加股息率筛选是负贡献」，
  不能说「哪一层贡献了多少」。

🔴 **"阈值越严越好"是全程口径的假象，逐年独立方向相反** ——
  全程 B 21.44 < 一 24.54 < 二 26.52（看着阈值有正贡献），而逐年独立
  `一 − B = -6.77pp (t=-0.69)`、`二 − B = -2.52pp (t=-0.24)`，**符号翻了**。
  又一个「全程排序与逐年排序给出相反答案」的实例（同 pb=0.7 那轮），
  所以**不采信"阈值本身有用"这个说法**。
★ `二 − 一`（多加一年约束）均差 +4.25pp t=+0.90，同样不显著。

#### 机制（量过的，不是推的）

    与基线的 (票, 建仓日) 重叠   3~4 笔 = 0.3~0.5%   —— 几乎是两个完全不同的组合
    建仓时流通市值中位          基线 16.84 亿 ｜ 一 7.83 亿 ｜ 二 11.78 亿
    平均仓位 / 最大权重均值      94.9/20.8% ｜ 96.8/21.9% ｜ 96.9/21.2%（各空仓 1 天）
    胜率                      60.96% ｜ 62.17% ｜ 60.45%   —— **相当**
    盈亏比                    1.55  ｜ 1.20  ｜ 1.32     —— **赢的幅度不够**

★ dy 版买的票**更小**（7.83 亿 < 16.84 亿），所以不是"被迫买大票"。
  规则确实在起作用（不是空转）：候选池 2026-09-01 实测
  全市场 5207 -> 有分红记录 3386 -> dy>2% 891 -> 连两年>2% 707。
★ **胜率相当而盈亏比掉了 1.55 -> 1.20/1.32** —— 选得一样准，赢的时候赢得少。
  与「小市值的收益极度集中在少数暴涨股」一致：高股息的公司按定义就不是
  那批票。
⚠ **「费用少了」是假象**（125,682 -> 38,615 / 42,559）：那是**资金少**
  （期末 536 万 vs 105/124 万，费用按成交额收），换手只从 10.68 降到
  8.45/8.02。同「尾盘买入」那轮同一个陷阱。

★ 文件保留不删（同「已否证的规则留成开关，不要留成注释」）——
  下次有人再想起"小市值 + 高股息"时，跑一行就能看到这张表。
"""

import importlib.util
import os

_HERE = os.path.dirname(os.path.abspath(__file__))


def _private(name, tag):
    spec = importlib.util.spec_from_file_location(
        tag, os.path.join(_HERE, name))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# 🔴 **加载 froec_traded 本身，不复述它的参数。** 凭印象拼一份
#   `_TRADED = {...}` 是本项目作废过一整批结论的坑（froec_yearly_ew 那次）。
_traded = _private('froec_traded.py', 'froec_dy__traded')
_base = _traded._base

# roec 那个内连接 —— 见上面 ② 。锚点要精确到整句。
_JOIN_IN = 'FROM pb_half b JOIN roec r ON r.code = b.jq_code'
_JOIN_LEFT = 'FROM pb_half b LEFT JOIN roec r ON r.code = b.jq_code'

# 分红：口径与 `strategies/红利/红利低波.py` 的 DIV_FISCAL_YEAR 同源
# （去重规则、可见性字段、`month(report_date)=12` 的年度锚定一字不差），
# 多出来的只有「每个会计年度各自合计」与「上一年」两列。
DY_SQL = """
-- 股息率（最近一个完整会计年度 / 及其上一年） —— FROEC-DY 的筛选依据
WITH raw AS (
  SELECT code, report_date, bonus_amount_rmb,
         row_number() OVER (PARTITION BY code, report_date, bonus_type
           ORDER BY CASE plan_progress WHEN '实施方案'     THEN 3
                                       WHEN '股东大会预案' THEN 2
                                       WHEN '董事会预案'   THEN 1
                                       ELSE 0 END DESC,
                    board_plan_pub_date DESC) AS r
  FROM {t_dividend}
  WHERE board_plan_pub_date <= DATE '{sd}'
    AND board_plan_pub_date >= DATE '{sd}' - INTERVAL {win} DAY
    AND bonus_cancel_pub_date IS NULL
    AND (plan_progress IS NULL OR plan_progress NOT IN ('终止', '取消分红'))
    AND bonus_amount_rmb > 0
), dedup AS (
  SELECT * FROM raw WHERE r = 1
), fy AS (
  -- 年度锚定用 report_date 月份==12，不依赖 bonus_type 的文本标签
  SELECT code, max(year(report_date)) AS y
  FROM dedup WHERE month(report_date) = 12 GROUP BY 1
), amt AS (
  SELECT code, year(report_date) AS y, sum(bonus_amount_rmb) * 1e4 AS amt
  FROM dedup GROUP BY 1, 2
), mv AS (
  -- 🔴 停牌股当日没有面板行 -> ASOF 结转最近一个交易日的总市值
  --   （与 froec 的 miss 同一手法；不结转的话停牌股一律算不出股息率）
  SELECT u.code, p.totalmv FROM (
    SELECT DISTINCT code, DATE '{sd}' AS d FROM dedup) u
  ASOF LEFT JOIN (SELECT jq_code, date, totalmv FROM {panel}
                  WHERE date > DATE '{sd}' - INTERVAL 400 DAY) p
    ON p.jq_code = u.code AND p.date <= u.d
)
SELECT f.code, f.y AS dy_fy,
       COALESCE(a0.amt, 0) / m.totalmv AS dy,
       COALESCE(a1.amt, 0) / m.totalmv AS dy_prev
FROM fy f JOIN mv m ON m.code = f.code
LEFT JOIN amt a0 ON a0.code = f.code AND a0.y = f.y
LEFT JOIN amt a1 ON a1.code = f.code AND a1.y = f.y - 1
WHERE m.totalmv > 0 AND f.y >= {ymin}
"""


def _fresh_ymin(sd):
    """允许的最老会计年度 —— 见口径 ②。

    ★ 按**月份**给而不是写死"最近两年"：年报分红集中在 4~6 月公告，
      7 月之前 FY(Y-1) 可能还没出来，一刀切会把正常公司误杀。
    """
    y, m = int(str(sd)[:4]), int(str(sd)[5:7])
    return y - (1 if m >= 7 else 2)


def _dy_table(orig_query, sd, win):
    """{code: (dy, dy_prev, fy)} —— 按决策日缓存（调仓每周一次，别重复查）。"""
    key = (str(sd), int(win))
    hit = _DY_CACHE.get(key)
    if hit is None:
        df = orig_query(DY_SQL, sd=sd, win=int(win), ymin=_fresh_ymin(sd))
        hit = {r.code: (float(r.dy), float(r.dy_prev), int(r.dy_fy))
               for r in df.itertuples()}
        _DY_CACHE.clear()          # 只留最近一天：回测逐日推进，旧的再也用不到
        _DY_CACHE[key] = hit
    return hit


_DY_CACHE = {}


def _wrap(orig):
    g = _base.g

    def q(sql, **kw):
        # 🔴 判据是 `pbcut`/`roecut` 在不在 —— 只有 froec 的候选池那条 SQL
        #   带它们，取涨停日之类的 query 不受影响（同 froec_cutgrid 那条）。
        if 'pbcut' not in kw or 'roecut' not in kw:
            return orig(sql, **kw)
        n_join = sql.count(_JOIN_IN)
        if n_join != 1:
            raise RuntimeError(
                'froec 的 SQL 里找不到唯一的 roec 内连接锚点（命中 %d 次）——'
                ' 它改写法了。本版靠把它改成 LEFT JOIN 来去掉"要有 5 期 ROE"'
                '这条要求，锚点失效就会静默退回旧语义。' % n_join)
        sql = sql.replace(_JOIN_IN, _JOIN_LEFT)
        want = int(kw.get('cand') or 0)
        skip = int(kw.get('skip') or 0)
        kw2 = dict(kw, pbcut='n', roecut='n2', cand=10 ** 9, skip=0)
        df = orig(sql, **kw2)
        dy = _dy_table(orig, kw['sd'], getattr(g, 'dy_win', 1200))
        lo = float(getattr(g, 'dy_min', 0.02))
        yrs = int(getattr(g, 'dy_years', 1))

        def ok(c):
            v = dy.get(c)
            if not v:
                return False
            return v[0] > lo and (yrs < 2 or v[1] > lo)

        df = df[df['jq_code'].map(ok)].copy()
        # 多带三列出去给「选股理由」用（同 froec 末层那几列：**投影**改动）
        df['dy'] = df['jq_code'].map(lambda c: dy[c][0])
        df['dy_prev'] = df['jq_code'].map(lambda c: dy[c][1])
        df['dy_fy'] = df['jq_code'].map(lambda c: dy[c][2])
        # 🔴 `skip` 留到**筛完**再切：原版是"筛完之后按市值升序跳过前 skip 个"，
        #   放在 SQL 里会变成"在没筛过的全量上跳"，两者完全不是一回事。
        return df.iloc[skip:skip + want] if want else df.iloc[skip:]
    return q


def initialize(context):
    g = _base.g
    g.dy_min = getattr(g, 'dy_min', 0.02)
    g.dy_years = getattr(g, 'dy_years', 1)
    g.dy_win = getattr(g, 'dy_win', 1200)
    _traded.initialize(context)
    # ★ 在 initialize **之后**包装：那时 context.data 已经是 GuardedFeed
    #   （同 froec_cutgrid）。幂等标记防止重复包两层。
    d = context.data
    if not getattr(d, '_dy_wrapped', False):
        d.query = _wrap(d.query)
        d._dy_wrapped = True


prepare = _base.prepare
rebalance = _base.rebalance
rebalance_buy = _base.rebalance_buy
check_limit_up = _base.check_limit_up
stop_check = _base.stop_check
pf_check = _base.pf_check
