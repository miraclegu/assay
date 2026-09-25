# -*- coding: utf-8 -*-
"""因子评价：IC / IR / 分位收益 —— 回答「这个因子好不好」。

    python3 assay/factor_eval.py                 # 全量重算 + 打印排行
    python3 assay/factor_eval.py --top 20 --h 20 # 只看 fwd20 的前 20
    python3 assay/factor_eval.py --factor cci20  # 看一个因子的逐年表现

## 为什么它在 assay 不在 datalake

因子**值**是数据的派生量（datalake，与 `build_beta_daily` 同类），而因子
**评价**要 forward return + 分组 + 调仓假设 —— 那是**回测语义**，
产品域上与「★ 选中的规则」同一块。见 `datalake/docs/因子模块设计.md` 第二节。
依赖方向仍是单向的：assay 读 datalake 的 parquet，反过来没有。

## 🔴🔴 重叠窗口：`t = IR × √N` 会把显著性**虚高 4.5 倍**

fwd20 的前瞻窗口是**重叠**的 —— 今天与明天的 IC 共用 19 天的未来收益，
高度自相关。按 242 个交易日当独立样本算，有效样本其实只有 242/20 ≈ 12，
`t` 被放大约 **√20 = 4.5 倍**。

所以这里**两个 t 都给**，而且**排序用 `t_adj`**：

    t_naive = IR × √N            <- 看着很显著，别信
    t_adj   = IR × √(N / h)      <- 有效样本按 N/h 折，粗但方向对

⚠ `N/h` 是**粗校正**不是 Newey-West：它假设 h 天之外完全不相关。
  真实自相关结构更复杂，所以 `t_adj` 仍然偏乐观 —— 它只用来**排除**
  那些连粗校正都过不了的，不用来"证明"什么
  （同「不显著不等于维持现状」「t 看着权威，会把路径混沌坐实成规则证据」）。
★ fwd1 不重叠，两个 t 相同。

## 🔴🔴 「横截面不可比」的因子，它的 IC 是在量纲上算出来的

目录表里有 `xs_comparable`，判据是 `factors/__init__.py` 的 **`UNITS` 表**
（每个单位显式声明可不可比）。真正不可比的是**标度由个股自己决定**的那些：
`元(价格)` / `股` / `元/天` —— `hfq_factor` 跨票从 1.00 到 **5899.9**，
于是 `ma20` 的"排第几"排的是「股价 × 上市以来分红拆细」。实测它与不复权
股价秩相关 +0.536、与复权因子 +0.421，两个混淆项加起来就是它的全部内容。

🔴 **2026-09-24 修过一次**：原来写成 `ABS_UNITS = ('元','股','元/天')`
  一刀切，把 35 个【规模 / 流动性 / 财务绝对额】一起判成不可比 ——
  `元(金额)` 里元是**全市场共同标度**，"10 亿 vs 1 亿"是真实差别。
  铁证是 `ln_mv = log(mv)` 判为可比而 `mv` 判为不可比，**而 log 单调、
  这里算的是秩相关** —— 同一个排序判成两档。**判据自相矛盾 -> 判据错。**
  ⚠ 两者的 13 项指标是 6 项逐位相同、7 项差 1e-9~3e-6（float64 把极近的两个
    `totalmv` 的 log 压成相等，在并列处翻了几下）—— **不是**完全相同。

🔴 **而它不报错** —— IC 照样算得出来。本地 98 个因子与 `factors.xlsx` 的 IC
符号有 19 个对不上，**全部**落在这几种单位里（聚宽那边必然做过标准化/中性化）。
所以排行榜上它们**单独标出来**，别混进"这个因子好"里读。

★ 不是说这些因子没用：它们**时序上**有意义（这只票今天的 MA20 比上月高）。
  要拿去横截面排序得先除以价格/成交额，或者做市值中性化 —— 那是下一层的事。

## 🔴 口径写进产物，换口径必须重算

    股票池   public_status IN ('正常上市','ST','*ST') AND close_bfq IS NOT NULL
             AND NOT is_risk_warned AND listed_days >= 120
    前瞻     close_hfq[t+h] / close_hfq[t] − 1   （**后复权** close-to-close）
    IC       每个交易日的横截面 **秩相关**（Spearman），当日有效样本 < 100 的丢掉

⚠ close-to-close 隐含"在收盘价成交"，**不可实现** —— 它是因子研究的通行
  口径，不是可交易的收益。要问"能赚多少"得去回测（那是引擎的事）。
⚠ 剔 ST 与剔次新都是**选择**：它们会改变 IC。口径记在 `_meta.json` 里，
  换了就必须全量重算 —— 拿两套口径的 IC 比大小是在比两个不同的量。
"""
import argparse
import hashlib
import io
import json
import os
import sys
import time
import uuid

import duckdb

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)                    # assay 仓库根
#: 🔴 `factors.xlsx` 在**工作区根**（assay 的再上一层）—— 而 `__file__`
#:   在 `assay/assay/` 里比直觉深一层，剥两次 dirname 只到 assay 仓库根。
#:   这个坑本项目记过三次（拆 srv/ 时 `/api/marks` 返回 {}、拆 lv/ 时账本
#:   写进真目录、`lv/px.py` 那句注释就是物证）。★ 判据是**文件在不在**，
#:   不是数层数 —— 所以下面那句 `isfile` 检查不能省。
WS = os.path.dirname(REPO)                      # 工作区根（datalake 与 assay 的父目录）
sys.path.insert(0, REPO)

from assay import paths                                     # noqa: E402

OUT = os.path.join(REPO, 'factors')
META = os.path.join(OUT, '_meta.json')
HORIZONS = (1, 5, 20)
#: 🔴 当日横截面有效样本少于这个数就丢掉那一天 —— 早年只有几百只票时
#:   横截面相关的噪声极大，混进来会把 IC 均值带偏，而它不报错。
MIN_XS = 100
#: 分几组看单调性。10 组是通行做法；组数改了历史结果不可比，所以记进元数据。
NQ = 10

#: 股票池 —— 🔴 与 `assay/market.TRADEABLE` **同源**那两条，
#:   另加剔 ST 与剔次新。分两处写就会出现"评价用的池子与盘面榜单不是一个"。
POOL = ("public_status IN ('正常上市','ST','*ST') AND close_bfq IS NOT NULL"
        ' AND NOT is_risk_warned AND listed_days >= 120')


def _paths(root=None):
    dl = paths.datalake(root)
    # 🔴 前三个走 `paths` 的常量，不在这里再拼一遍 —— 原来这里自己拼了
    #   `panel_daily/panel_*.parquet` 与因子那两条，而正本早就在 `paths.py`。
    #   下面那句「路径只在这一处拼」当时已经不成立了（**代码在和自己的
    #   注释打架**，同 ddGap / lprSlice 那两次）。
    return (os.path.join(dl, paths.PANEL_GLOB),
            os.path.join(dl, paths.FACTOR_GLOB),
            os.path.join(dl, paths.FACTOR_CATALOG),
            # 🔴 目录表的【另一半】：原清单里算不出来的那些，带原因。
            #   路径只在这一处拼 —— 8 处各数一遍 dirname 那种事已经栽过
            #   （同 assay/paths.py 那一轮）。
            os.path.join(dl, 'mart', 'factor_missing.parquet'),
            os.path.join(dl, 'mart', 'factor_reasons.json'))


def _con(threads=8):
    c = duckdb.connect(':memory:')
    c.execute('SET threads=%d' % threads)
    return c


# ============================ 分池评价（2026-09-24） ============================
#: 🔴 **池子清单是正本** —— 页面照它渲染，前端不写死任何一个池子名
#:   （同「整页照服务端清单渲染」：加一个池子广场上自动就有，而前端硬编码
#:   的话新池子**不会出现，且不报错**）。
#:
#: 每项：key / 中文名 / 面板成分列 / 分几组 NQ / 当日最少几只 MIN_XS
#:
#: 🔴 `col` 取的是 `datalake/build/build_panel_daily.INDEXES` 的**值** ——
#:   成分的 PIT 语义（**两步 ASOF**：先 ASOF 出当日适用的快照日、再按
#:   `(code, as_of)` 精确 join）只有面板那一处实现。在这里照
#:   `std/index_member_asof` 自己再 ASOF 一遍就是第二份实现，而**按 code
#:   各自 ASOF 会让"退出成分的票只进不出"**（面板那段注释实测：沪深300
#:   变 316 只、中证1000 变 2097 只），**且不报错**。
#: 🔴 **`all`（全市场）必须留着**：它是现状那一份，也是其它池子唯一的对照
#:   基准 —— 没有它，「沪深300 里这个因子 IC 0.02」既说不出是高是低。
#: 🔴 小池子**单独给分位数**（用户 2026-09-24 定「做，但给它们单独的分位数」）：
#:   实测当日成分数（POOL 过滤后）上证50 最少 36 / 科创50 48 / 创业板指 24
#:   —— 照 10 组切每组只有 3~5 只，那个"分位收益"是噪声而不是信号。
#:   所以 NQ=5 / MIN_XS=30。**页面必须把口径不同标出来**：不标的话两个池子
#:   的 `q_lo` 摆在一起看着可比，而它们不是（同「口径不一致要响亮地说出来」）。
POOLS = [
    ('all',    '全市场',   None,        10, 100),
    ('hs300',  '沪深300',  'in_hs300',  10, 100),
    ('zz500',  '中证500',  'in_zz500',  10, 100),
    ('zz800',  '中证800',  'in_zz800',  10, 100),
    ('zz1000', '中证1000', 'in_zz1000', 10, 100),
    ('gz2000', '国证2000', 'in_gz2000', 10, 100),
    ('sz50',   '上证50',   'in_sz50',    5,  30),
    ('kc50',   '科创50',   'in_kc50',    5,  30),
    ('cyb',    '创业板指', 'in_cyb',     5,  30),
]
POOL_KEYS = [p[0] for p in POOLS]
#: ⚠ 本地**没有**中证全指（`000985`）的成分 —— `std/index_member_asof` 里
#:   一行都没有（实测）。所以那一档给不了，不在清单里假装有
#:   （同「不拿 ETF 当指数用」「近似物要叫自己的名字」）。


def pool_meta(key):
    for k, label, col, nq, mn in POOLS:
        if k == key:
            return {'key': k, 'label': label, 'col': col, 'nq': nq, 'min_xs': mn}
    raise KeyError('没有这个池子：%s（有的是 %s）' % (key, '/'.join(POOL_KEYS)))


# ---------------------------- 分片与增量判据 ----------------------------
#: 🔴 **按 (池, 年) 分片**，不是一个大文件。理由是增量：
#:   `IC(因子, D, h)` 一旦 `D+h` 那天过去就**不再变**（`lead` 有值之后是死的），
#:   所以每天真正要算的只有新到期的那一两天。而单文件全量重写的话
#:   **只能整份重算**（实测全量 33 分钟，其中换手 fwd1 一项就 2103 秒）。
#: 🔴 **分片格式版本**，进依赖指纹。加一列（这一轮加了 `nuniq`）、
#:   换一处 SQL 口径（这一轮给 `ntile` 加了 tie-break）之后，旧分片按
#:   panel/spec 判据仍然"干净" —— 于是页面读到的是**旧口径**，**而它不报错**
#:   （同「改了单位而忘了重建目录表，页面上还是旧分档」那条）。
SCHEMA_VER = 2


def _shard(kind, pool, year):
    return os.path.join(OUT, kind, pool, '%d.parquet' % year)


def _panel_years(panel_glob):
    """每个面板年文件的 `size|mtime_ns` —— 增量的判据挂在【面板】上。

    🔴 **不能挂在因子面板上**：`build_factor_daily` 是 all-or-nothing，
      `panel_fp` 一变就把 24 个年文件**全部重写**（实测 mtime 全落在同一分钟）。
      拿因子文件的 mtime 当判据等于天天说"全脏"，增量就退化成全量
      —— 而它不报错，只是每天白烧半小时。
    ★ 面板的过去年份是稳定的（本机 2003~2025 都停在 09-04，只有 2026 每天变），
      所以这个判据真的分得出干净与脏。
    """
    import glob as _g
    import re
    out = {}
    for f in _g.glob(panel_glob):
        m = re.search(r'(\d{4})\.parquet$', f)
        if not m:
            continue
        st = os.stat(f)
        out[int(m.group(1))] = '%d|%d' % (st.st_size, st.st_mtime_ns)
    return out


def _spec_sig(root=None):
    """因子**口径**指纹（`build_factor_daily` 写的 `spec_sig`）。

    🔴 少了它，改一条 `Spec` 的公式之后评价**不会重算** —— 页面上还是旧数，
      而它不报错。读不到就返回 None，`build` 会退回"按因子文件指纹判"
      （= 每天全量）并**明说**，不静默。
    """
    p = os.path.join(paths.datalake(root), 'mart', 'factor_daily', '_meta.json')
    try:
        with io.open(p, encoding='utf-8') as f:
            return json.load(f).get('spec_sig')
    except Exception:
        return None


def _dep_sig(py, year, pm, spec, kind):
    """年 Y 这一片的依赖指纹。**IC 与换手盯的年份不同，别记反：**

        IC    Y-1 / Y / **Y+1**   前瞻收益的 `lead` 要往后看 max(HORIZONS) 天
        换手  Y-1 / Y             只比**相邻两个调仓日**的成分，不看未来

    🔴 IC 少盯 Y+1 的话，年末那 20 天的 IC 在次年数据到齐之后**不会被重算**
      —— 那几天永远缺，**而它不报错**。
    ⚠ 代价是**保守**：稳态下 `panel_{Y+1}` 每天变，于是 IC 的"上一年"那片
      每天陪跑一次（实测全市场一片约 67 秒）。换成"只在年初 20 个交易日内
      盯 Y+1"能省掉，但**数据被修正时就漏了**（本项目修过 volume 74,952 行 /
      复权因子 / ETF 价格刻度）—— 这个取舍选了正确的那一边。
    ★ 而换手**不需要**那份保守，因为它结构上就不依赖未来 —— 所以它每天
      只重建当年那一片。两者的分工是可证的事实，不是省一点是一点。
    ★ 池口径（成分列 / NQ / MIN_XS）与全局口径（POOL / HORIZONS）也进指纹：
      改了分位数而分片不重算的话，页面上新旧两套口径混在一起，**而它不报错**。
    """
    parts = [kind, 'v%d' % SCHEMA_VER, str(year), pm['key'], str(pm['col']),
             str(pm['nq']), str(pm['min_xs']),
             ','.join(str(h) for h in HORIZONS), POOL, spec or '?']
    ys = (year - 1, year, year + 1) if kind == 'ic' else (year - 1, year)
    for y in ys:
        parts.append('%d=%s' % (y, py.get(y, '-')))
    return hashlib.sha256('|'.join(parts).encode()).hexdigest()[:12]


def _load_meta():
    try:
        with io.open(META, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def _member_cols(con, panel):
    """面板里**真的有**哪几个成分列 —— 缺的要响亮地说，不静默跳过。"""
    have = set(d[0] for d in con.execute(
        "SELECT * FROM read_parquet('%s') LIMIT 0" % panel).description)
    return have


def _fwd_base(con, panel, have):
    """前瞻收益（**不按池过滤**）+ 成分列 —— 一次算好，所有池共用。

    🔴🔴 **`lead` 必须在成分过滤【之前】算。** 成分是会变的（半年调一次、
      每次换掉约一成），先按成分过滤再 `lead` 的话，一只票退出指数两年后又
      回来，它那天的"20 日后收益"会**跨过那两年** —— 而它不报错，只是那几行
      的前瞻收益是个毫无意义的数。
    ★ 所以分工是：`f_h` 在**全市场基础池**上算一次（与分哪个池无关），
      而**池内排名**按池再排一遍 —— Spearman 的两个秩必须排在同一个集合里
      （拿全市场的收益名次去配池内的因子名次，算出来的不是那个池的 IC）。
    🔴🔴 **不许切年窗。** 我第一版为了省时间只读 [Y-1, Y+1]，实测当场把
      2024 年 261 行的 `f20` 变成 NULL —— 因为 `lead` 是**按行**不按日历走，
      而 `POOL` 过滤（停牌 / ST / 次新）会在一只票的序列里挖出断档：
      600165 的面板行是 2024-03-06…04-02，**下一行直接跳到 2026-08-26**。
      切了窗那一跳落在窗外 -> NULL；不切窗 -> 算出 +71.4% 并标成"20 日收益"。
      **两种都不对，而"对不对"居然取决于窗口边界** —— 那比任何一种一致的
      做法都糟。所以全史算一次（实测只要 1.6 秒），把断档那件事单独记着。
    ⚠ **断档跨期那条既有缺陷【没有在这一轮修】**：它会让极少数行的"h 日
      收益"其实是几年的收益。修它要改所有历史数值，是单独一个决定
      （同「没有去修 froec.py 的排序」）。这里只保证分池不引入新的不一致。
    """
    lead = ',\n        '.join(
        'lead(close_hfq,%d) OVER w / close_hfq - 1 AS f%d' % (h, h)
        for h in HORIZONS)
    mc = [c for c in (p[2] for p in POOLS) if c and c in have]
    sel = (', ' + ', '.join(mc)) if mc else ''
    con.execute("""
        CREATE OR REPLACE TABLE fwd_base AS
          SELECT jq_code, date, %s%s
          FROM read_parquet('%s') WHERE %s
          WINDOW w AS (PARTITION BY jq_code ORDER BY date)
    """ % (lead, sel, panel, POOL))
    return mc


def _fwd(con, col):
    """从 `fwd_base` 切出这个池，并**在池内**重算横截面排名。"""
    rk = ',\n      '.join(
        'rank() OVER (PARTITION BY date ORDER BY f%d) AS r%d' % (h, h)
        for h in HORIZONS)
    fs = ', '.join('f%d' % h for h in HORIZONS)
    con.execute("CREATE OR REPLACE TABLE fwd AS SELECT jq_code, date, %s, %s"
                " FROM fwd_base%s" % (fs, rk, (' WHERE ' + col) if col else ''))


def _rbd(con, panel):
    """调仓日历 —— **全局、全史、与池无关**。

    🔴 两条都不能省：
      ① **不按年切**：按年切的话每年头一次调仓找不到上一次，于是每年少一个
        点**而它不报错**。
      ② **不按池切**：换手问的是"每 h 个交易日调一次仓要换掉百分之几"，
        那个 h 是交易日不是"这个池有行情的日子"。按池各算一套的话，
        两个池的第 k 次调仓落在不同的日子上，横向比就没意义了。
    """
    con.execute("""
        CREATE OR REPLACE TABLE rbd AS
        SELECT date, row_number() OVER (ORDER BY date) - 1 AS i
        FROM (SELECT DISTINCT date FROM read_parquet('%s') WHERE %s)
    """ % (panel, POOL))


def _long(con, fpaths):
    """一年的因子面板 -> 长表。★ 宽表做这一步实测慢 58 倍。

    ★ 与池无关，所以外层循环是**年**、内层才是池 —— 反过来的话这一步
      （实测 2~2.5 秒、要解一个 500~760 MB 的文件）会被重复 9 遍。
    """
    one = fpaths[0] if isinstance(fpaths, (list, tuple)) else fpaths
    cols = [d[0] for d in con.execute(
        "SELECT * FROM read_parquet('%s') LIMIT 0" % one).description]
    fac = [c for c in cols if c not in ('jq_code', 'date')]
    lst = ', '.join(fac)
    src = ("read_parquet(['%s'])" % "','".join(fpaths)
           if isinstance(fpaths, (list, tuple)) else "read_parquet('%s')" % fpaths)
    con.execute("""
        CREATE OR REPLACE TABLE lng AS
        SELECT jq_code, date, factor_id, val FROM (
          SELECT jq_code, date, UNPIVOT_NAME AS factor_id, UNPIVOT_VAL AS val
          FROM (SELECT jq_code, date, %s FROM %s)
          UNPIVOT (UNPIVOT_VAL FOR UNPIVOT_NAME IN (%s))
        ) WHERE val IS NOT NULL
    """ % (lst, src, lst))
    return fac


def _year_ic(con, h, nq, mn):
    """一年一个 horizon 的 (factor_id, date, ic, n, q_lo, q_hi, mkt)。"""
    return con.execute("""
        WITH j AS (
          SELECT l.jq_code, l.factor_id, l.date, l.val,
                 f.r%(h)d AS rf, f.f%(h)d AS fw
          FROM lng l JOIN fwd f USING (jq_code, date)
          WHERE f.f%(h)d IS NOT NULL),
        rk AS (
          SELECT factor_id, date, rf, fw, val,
                 rank() OVER (PARTITION BY date, factor_id ORDER BY val) AS rv,
                 -- 🔴 **并列必须有 tie-break**（`, jq_code`）。`ntile` 是按
                 --   排序后的**行位置**切桶的，并列项谁在前取决于喂进窗口
                 --   算子的**物理行序** —— 换一种取数写法（这一轮把 fwd 从
                 --   "直扫 parquet"改成"扫 fwd_base 这张表"）行序就变了，
                 --   分位成分跟着变，**而它不报错**。实测 `aroon_up`
                 --   2024-02-29：5092 行只有 26 个不同取值、56%% 都是 0.0，
                 --   换手因此差到 0.415。同 `search('红利')` 那次：
                 --   让它可复现没有任何代价，所以加第二个排序键。
                 ntile(%(nq)d) OVER (PARTITION BY date, factor_id
                                     ORDER BY val, jq_code) AS q
          FROM j)
        SELECT factor_id, date, corr(rv, rf) AS ic, count(*) AS n,
               -- 🔴 **并列有多严重**要给出来：取值只有几个的因子（aroon/
               --   涨跌停标记那类），分位切点整个落在并列里 —— 那时
               --   "最小分位收益"与"换手"**不是有意义的数**，页面要说。
               count(DISTINCT val) AS nuniq,
               avg(CASE WHEN q = 1 THEN fw END) AS q_lo,
               avg(CASE WHEN q = %(nq)d THEN fw END) AS q_hi,
               -- 🔴 当日横截面**等权平均**收益 —— 超额的基准。
               --   分位组本身是等权的，拿等权比才是同口径；拿指数比会混进
               --   市值加权与成分差异（同「不拿 ETF 当指数用」）。
               --   ★ 分池之后这个基准也是**池内**的等权平均 —— 问的是
               --     "在这个池里选，比闭着眼买这个池强多少"。
               avg(fw) AS mkt
        FROM rk GROUP BY 1, 2 HAVING count(*) >= %(mn)d
    """ % {'h': h, 'nq': nq, 'mn': mn}).df()


# ============================ 分位换手率 ============================
#: 换手按【调仓日】采样：每 h 个交易日一次。
#: 🔴 逐日算出来的是**另一个量**（"今天的 top 组与昨天差多少"），
#:   而「换手率」问的是"每次调仓要换掉百分之几" —— 两者差一个数量级。
def _turn_lg(con, fpaths):
    """调仓日那几天的因子长表 —— **与池无关，各池共用**。

    🔴 这一步是换手的大头（h=1 时每个交易日都是调仓日，等于把整年的因子
      文件解一遍）。实测一年 h=1 约 1.9 秒、而每池的 `mem` 是 11.4 秒 ——
      **把它提到池循环外面**，否则 9 个池要重复解 9 遍。
    """
    one = fpaths[0]
    cols = [d[0] for d in con.execute(
        "SELECT * FROM read_parquet('%s') LIMIT 0" % one).description]
    fac = [c for c in cols if c not in ('jq_code', 'date')]
    lst = ', '.join(fac)
    con.execute("""
        CREATE OR REPLACE TABLE lg AS
        SELECT jq_code, date, factor_id, val FROM (
          SELECT jq_code, date, UNPIVOT_NAME AS factor_id, UNPIVOT_VAL AS val
          FROM (SELECT jq_code, date, %s FROM read_parquet(['%s'])
                WHERE date IN (SELECT date FROM rb))
          UNPIVOT (UNPIVOT_VAL FOR UNPIVOT_NAME IN (%s))
        ) WHERE val IS NOT NULL
    """ % (lst, "','".join(fpaths), lst))


def _turn_rb(con, h, year):
    """这一年的调仓日 + **紧邻的上一个**（第一次换手要拿它当对照）。"""
    con.execute("""
        CREATE OR REPLACE TABLE rb AS
        WITH a AS (SELECT date, i / %(h)d AS k FROM rbd WHERE i %% %(h)d = 0)
        SELECT * FROM a WHERE year(date) = %(y)d
        UNION ALL
        SELECT * FROM (SELECT * FROM a WHERE date < DATE '%(y)d-01-01'
                       ORDER BY date DESC LIMIT 1)
    """ % {'h': h, 'y': year})
    return con.execute('SELECT count(*) FROM rb').fetchone()[0]


def _turnover(con, nq, year):
    """(factor_id, date, q, n, turn) —— 相邻两个调仓日的分位成分变化率。

    换手 = 1 − |A ∩ B| / |A|，A 是本次调仓日的该分位成分、B 是上一次的。
    ★ 只输出 `year(date) == year` 那些行 —— 多带进来的那个上一年的调仓日
      是**对照**，它自己的 turn 算不出来（它的上一个不在 `rb` 里），
      留着就是一行凭空的 1.0。
    """
    con.execute("""
        CREATE OR REPLACE TABLE mem AS
        WITH j AS (SELECT l.* FROM lg l JOIN fwd f USING (jq_code, date)),
        q AS (SELECT factor_id, date, jq_code,
                     -- 🔴 与 `_year_ic` **同一条 tie-break** —— 两处不一致的话
                     --   "分位收益"与"分位换手"说的就不是同一批票了。
                     ntile(%d) OVER (PARTITION BY date, factor_id
                                     ORDER BY val, jq_code) AS q
              FROM j)
        SELECT q.factor_id, rb.k, q.jq_code, q.q
        FROM q JOIN rb ON rb.date = q.date
        WHERE q.q IN (1, %d)
    """ % (nq, nq))
    return con.execute("""
        WITH cur AS (SELECT factor_id, k, q, count(*) n FROM mem GROUP BY 1,2,3),
        keep AS (
          SELECT a.factor_id, a.k, a.q, count(*) AS same
          FROM mem a JOIN mem b
            ON b.factor_id = a.factor_id AND b.q = a.q
           AND b.k = a.k - 1 AND b.jq_code = a.jq_code
          GROUP BY 1,2,3)
        SELECT c.factor_id, rb.date, c.q, c.n,
               1.0 - coalesce(k.same, 0) * 1.0 / c.n AS turn
        FROM cur c
        LEFT JOIN keep k USING (factor_id, k, q)
        JOIN (SELECT DISTINCT k, date FROM rb) rb ON rb.k = c.k
        WHERE c.k > 0 AND year(rb.date) = %d
    """ % year).df()


def _write(df, path):
    """写一片分片。**tmp 名必须唯一（pid + uuid），不能用固定的 `path+'.tmp'`。**

    🔴 这条链上**没有任何锁**，而写同一批分片的进程有两个来源：
      ① `sync_daily.sh` 的 13/13 每天自动跑；
      ② 人手工跑一次 `--build-only` / `--pool xxx`（诊断时很自然就会这么干）。
    固定 tmp 名时两个进程会**交错写进同一个 tmp**，然后把一个头尾都像
    parquet、中间是垃圾的文件 `os.replace` 就位 —— 与 2026-09-07 realtime
    那次落地 2078 字节坏 parquet 是**同一种坏法**，而且它**不报错**：
    下一次读那片才抛 `TProtocolException`，那时已经查不回是谁写的。

    ★ 与 realtime 那条的**分工不同，别记反**：那边「全局写锁是根本修复、
      唯一 tmp 名只防跨进程（冗余）」；这里**没有锁**，所以唯一 tmp 名
      就是**唯一**的保护。两个进程最后 rename 谁赢都行 —— 各自那份都是
      完整的，赢的那份要么与输的相同、要么更新。
    """
    d = os.path.dirname(path)
    if not os.path.isdir(d):
        os.makedirs(d)
    tmp = '%s.%d.%s.tmp' % (path, os.getpid(), uuid.uuid4().hex[:8])
    try:
        df.to_parquet(tmp, index=False)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)          # 中断/报错不留垃圾（.tmp 不会被任何人读）
        raise


def plan(root=None, pools=None, years=None, force=False):
    """要重建哪几片 —— 纯函数，`--plan` 就是打印它。

    返回 `(need, diag)`：`need` 是 `[(pool, year, {'ic': sig, 'turn': sig})]`，
    只含**真的要重建**的 kind（都干净就不出现在列表里）。
    """
    import glob as _g
    import re
    panel, fglob, _, _, _ = _paths(root)
    fy = {}
    for f in _g.glob(fglob):
        m = re.search(r'(\d{4})\.parquet$', f)
        if m:
            fy[int(m.group(1))] = f
    py = _panel_years(panel)
    spec = _spec_sig(root)
    old = _load_meta().get('shards', {})
    need, want = [], {}
    for k in (pools or POOL_KEYS):
        pm = pool_meta(k)
        for y in sorted(fy):
            if years and y not in years:
                continue
            todo = {}
            for kind in ('ic', 'turn'):
                sg = _dep_sig(py, y, pm, spec, kind)
                want['%s/%s/%d' % (kind, k, y)] = sg
                fresh = (old.get('%s/%s/%d' % (kind, k, y)) == sg
                         and os.path.isfile(_shard(kind, k, y)))
                if force or not fresh:
                    todo[kind] = sg
            if todo:
                need.append((k, y, todo))
    return need, {'factor_years': fy, 'spec_sig': spec, 'want': want,
                  'panel_years': py, 'panel': panel}


def build(root=None, pools=None, years=None, force=False, quiet=False):
    """增量重建分片。★ 外层是**年**、内层是**池** —— `lng` 与换手的 `lg`
    与池无关（解一个 500~760 MB 的因子文件要 2~3 秒），反过来循环的话
    这两步要重复 9 遍。
    """
    import pandas as pd
    need, diag = plan(root, pools, years, force)
    fy, spec, panel = diag['factor_years'], diag['spec_sig'], diag['panel']
    if not fy:
        raise SystemExit('没有因子面板 —— 先跑 datalake/build/build_factor_daily.py')
    if spec is None and not quiet:
        print('⚠ 读不到因子面板的 spec_sig（mart/factor_daily/_meta.json）—— '
              '改了因子公式不会触发重算，请先跑一次 build_factor_daily.py')
    if not need:
        if not quiet:
            print('全部是最新的（%d 池 × %d 年）—— 无事可做' %
                  (len(pools or POOL_KEYS), len(fy)))
        return 0
    con = _con()
    have = _member_cols(con, panel)
    # 🔴 面板里没有那个成分列的池子要**响亮地说**，不静默跳过 ——
    #   静默的话页面上少一个池子而没人知道为什么（同「算不出来的因子也要有名有姓」）。
    miss = sorted(set(k for k, y, _ in need
                      if pool_meta(k)['col'] and pool_meta(k)['col'] not in have))
    if miss:
        print('⚠ 面板里没有这几个池子的成分列，跳过：%s\n'
              '   要它们的话在 datalake/build/build_panel_daily.py 的 INDEXES 里\n'
              '   加上对应指数，然后重建面板（全量，约 6 分钟）' %
              ' / '.join('%s(%s)' % (k, pool_meta(k)['col']) for k in miss))
        need = [n for n in need if n[0] not in miss]
        if not need:
            return 0
    t0 = time.time()
    _rbd(con, panel)
    # ★ `fwd_base` 与**年、池都无关**（全史一次 1.6 秒），提到循环外面：
    #   放进年循环里既慢、又会诱人去切年窗，而切年窗会让 `lead` 的结果
    #   依赖窗口边界（见 `_fwd_base` 的注释）。
    _fwd_base(con, panel, have)
    by_year = {}
    for k, y, todo in need:
        by_year.setdefault(y, []).append((k, todo))
    m = _load_meta()
    shards = dict(m.get('shards', {}))
    nsh = 0
    for y in sorted(by_year):
        ty = time.time()
        fps = [fy[x] for x in (y - 1, y) if x in fy]
        _long(con, fy[y])                      # IC 用：只要本年
        need_turn = [(k, t) for k, t in by_year[y] if 'turn' in t]
        lg_done = set()
        for k, todo in by_year[y]:
            pm = pool_meta(k)
            _fwd(con, pm['col'])
            if 'ic' in todo:
                fr = []
                for h in HORIZONS:
                    d = _year_ic(con, h, pm['nq'], pm['min_xs'])
                    d['h'] = h
                    fr.append(d)
                _write(pd.concat(fr, ignore_index=True), _shard('ic', k, y))
                shards['ic/%s/%d' % (k, y)] = todo['ic']
                nsh += 1
        # 换手：`lg` 按 h 变（调仓日不同），所以 h 在外、池在内
        if need_turn:
            tos = dict((k, []) for k, _ in need_turn)
            for h in HORIZONS:
                _turn_rb(con, h, y)
                _turn_lg(con, fps)       # 各池共用
                for k, _ in need_turn:
                    pm = pool_meta(k)
                    _fwd(con, pm['col'])
                    d = _turnover(con, pm['nq'], y)
                    d['h'] = h
                    tos[k].append(d)
            for k, todo in need_turn:
                _write(pd.concat(tos[k], ignore_index=True), _shard('turn', k, y))
                shards['turn/%s/%d' % (k, y)] = todo['turn']
                nsh += 1
        if not quiet:
            print('  %d  %d 池  %5.0fs' % (y, len(by_year[y]), time.time() - ty),
                  flush=True)
    m.update({'built_at': time.strftime('%Y-%m-%d %H:%M:%S'),
              'spec_sig': spec, 'pool': POOL, 'horizons': list(HORIZONS),
              'pools': [{'key': k, 'label': la, 'col': c, 'nq': nq, 'min_xs': mn}
                        for k, la, c, nq, mn in POOLS],
              'shards': shards,
              'excess_base': '当日横截面等权平均收益（【池内】，不是指数）',
              'annualize': '(1 + 区间内 h 日超额均值) ^ (244/h) − 1',
              'fwd': 'close_hfq[t+h] / close_hfq[t] - 1  (后复权 close-to-close)'})
    if not os.path.isdir(OUT):
        os.makedirs(OUT)
    with io.open(META, 'w', encoding='utf-8') as f:
        f.write(json.dumps(m, ensure_ascii=False, indent=1))
    if not quiet:
        print('\n-> %d 片  %.0f 秒' % (nsh, time.time() - t0))
    return nsh


#: 页面上那几个时间段。★ 存的是**逐日**的 IC 与逐次调仓的换手，
#: 所以区间聚合是查询时做的 —— 换一个区间不用重算。
WINDOWS = [('3m', '近 3 月', 63), ('6m', '近 6 月', 122), ('1y', '近 1 年', 244),
           ('3y', '近 3 年', 732), ('5y', '近 5 年', 1220), ('all', '全部', None)]
TRADING_DAYS = 244


def _win_start(dates, n):
    """区间起点：按**交易日**倒数 n 天，不是按自然日。

    ★ 按自然日推的话"近 3 月"会随节假日漂（春节那个月只有 15 个交易日）——
      同实盘业绩页那条，只是方向相反：那边要按自然日、这边要按交易日，
      因为这里的样本单位就是交易日。

    🔴 `sorted(set(dates))` 是**逐元素 Python 迭代** —— 对 90 万行的
      datetime 列，profile 显示 `datetimes.__iter__` 被调 450 万次，
      占整个 `summary` 的 **85%**（2.20s / 2.58s）。`pd.unique` 是 C 实现。
      ★ 去重 + 排序的**语义一个字没变**，等价性靠 18 项指纹逐位比。
    """
    import numpy as np
    import pandas as pd
    if n is None:
        return None
    u = np.sort(pd.unique(np.asarray(dates)))
    return u[-n] if len(u) > n else u[0]


_PQ = {}


def _load_shards(kind, pool):
    """读一个池的全部年分片并拼起来。

    🔴 **每个分片各自记 mtime**（同 `_load`）—— 「miss 就 clear()」那个写法
      本项目已经犯过**三次**（`srv/factors._SUM` / `factor_eval._PQ` /
      `lv/corp.actions` 差点），这里逐键各记各的。
    ★ 缓存键带**文件清单**：重建之后多出/少掉一年也要失效，
      只盯 mtime 的话"新增了 2027 那一片"读不到，**而它不报错**。
    """
    import glob as _g
    import pandas as pd
    d = os.path.join(OUT, kind, pool)
    fs = sorted(_g.glob(os.path.join(d, '*.parquet')))
    if not fs:
        return None
    key = (kind, pool)
    sig = tuple((os.path.basename(f), os.path.getmtime(f)) for f in fs)
    hit = _PQ.get(key)
    if hit is None or hit[0] != sig:
        _PQ[key] = (sig, pd.concat([pd.read_parquet(f) for f in fs],
                                   ignore_index=True))
    return _PQ[key][1]


def pools_ready():
    """哪几个池**真的算过了** —— 页面照它渲染。

    🔴 没算过的**不藏起来**，带一句 why 给页面（同「算不出来的因子也要
      有名有姓」）：藏起来的话人分不出"这个池不支持"与"还没算"。
    """
    import glob as _g
    out = []
    for k, label, col, nq, mn in POOLS:
        fs = _g.glob(os.path.join(OUT, 'ic', k, '*.parquet'))
        why = None
        if not fs:
            why = '还没算过 —— 跑一次 python3 assay/factor_eval.py --pool %s' % k
        out.append({'key': k, 'label': label, 'nq': nq, 'min_xs': mn,
                    'years': len(fs), 'ready': bool(fs), 'why': why})
    return out


def summary(h=20, win='all', root=None, pool='all'):
    """按区间聚合 -> 页面要的那几列。

    返回列（与 `factors.xlsx` 那 7 列对齐）：
      ic / ir / q_lo_ex_ann / q_hi_ex_ann / to_lo / to_hi / spread / nday / npos

    🔴 **超额的基准是「当日横截面等权平均」，不是指数** —— 而
      `factors.xlsx` 用什么基准**未知**，所以两边的数值**不可比大小**，
      只能比符号与序（同「别人的股息率里藏着别人的窗口」）。页面要写出来。
    🔴 **年化**：`(1 + r̄)^(244/h) − 1`，r̄ 是区间内 h 日超额的均值。
      ⚠ h 日窗口是**重叠**的，所以 r̄ 无偏但样本不独立 —— 这个年化是
        "平均每 h 天赚这么多，折成一年"，**不是一条可实现的净值曲线**。
    """
    import numpy as np
    import pandas as pd
    pm = pool_meta(pool)
    ic = _load_shards('ic', pool)
    if ic is None:
        raise SystemExit('「%s」这个池还没算过 —— 跑一次 '
                         'python3 assay/factor_eval.py --pool %s' % (pm['label'], pool))
    ic = ic[ic['h'] == h]
    n = dict((k, d) for k, _, d in WINDOWS).get(win, None)
    d0 = _win_start(ic['date'], n)
    if d0 is not None:
        ic = ic[ic['date'] >= d0]
    g = ic.groupby('factor_id')
    # 🔴 这三个原来是 `groupby.apply(lambda …)` —— **逐组 Python 回调**，
    #   实测占 summary 的一半（0.33s / 0.86s，profile 定位，不是猜）。
    #   向量化成「先算逐行的量、再按 factor_id 求均值」，结果逐位相同
    #   （改前存了 18 项指纹，改后全部对上）。
    _fid = ic['factor_id']
    ex_lo = (ic['q_lo'] - ic['mkt']).groupby(_fid).mean()
    ex_hi = (ic['q_hi'] - ic['mkt']).groupby(_fid).mean()
    k = TRADING_DAYS / float(h)
    out = pd.DataFrame({
        'nday': g['ic'].size(),
        'ic': g['ic'].mean(),
        'ic_sd': g['ic'].std(),
        'pos': (ic['ic'] > 0).groupby(_fid).mean(),
        'spread': g['q_hi'].mean() - g['q_lo'].mean(),
        # 🔴 **当日有几个不同取值**（中位数）。< 分位数的话，分位切点整个
        #   落在并列里 —— 那时"最小分位超额"与"换手"**不是有意义的数**
        #   （实测 aroon_up 某日 5092 行只有 26 个取值、56%% 是 0.0）。
        #   页面要照它标出来，不标的话那几行看着和别的一样可信。
        'nuniq': g['nuniq'].median() if 'nuniq' in ic.columns else np.nan,
        # 🔴 年化用 (1+r)^k −1 而不是 r×k：h=1 时 k=244，线性折算会把
        #   日均 0.1% 说成 24.4% 而复利是 27.6% —— 差得看得见。
        'q_lo_ex_ann': (1 + ex_lo) ** k - 1,
        'q_hi_ex_ann': (1 + ex_hi) ** k - 1,
    })
    out['ir'] = out['ic'] / out['ic_sd']
    out['t_naive'] = out['ir'] * np.sqrt(out['nday'])
    # 🔴 重叠窗口：有效样本按 N/h 折。粗，但方向对；不折的话 t 虚高 √h 倍。
    out['t_adj'] = out['ir'] * np.sqrt(out['nday'] / float(h))

    # ---- 换手（逐次调仓，单独一张表）----
    to = _load_shards('turn', pool)
    if to is not None:
        to = to[to['h'] == h]
        if d0 is not None:
            to = to[to['date'] >= d0]
        piv = to.pivot_table(index='factor_id', columns='q', values='turn',
                             aggfunc='mean')
        out['to_lo'] = piv.get(1)
        out['to_hi'] = piv.get(pm['nq'])
        out['nreb'] = to[to['q'] == 1].groupby('factor_id')['turn'].size()
    return out.reset_index()


def vs_xlsx(h, cat):
    """与 `factors.xlsx` 的 IC 做**符号**对数 —— 唯一的外部参照。

    🔴 **只比符号与序，不比绝对值**：那份表的股票池、持有期、是不是超额
      全都未知，绝对值不可比（同「别人的『股息率』里藏着别人的窗口」）。
    🔴 **必须按 `xs_comparable` 拆开报**。混在一起是 80%，拆开是
      **可比 91% / 量纲 65%** —— 混着报会同时掩盖两件事：
      前者其实是个很强的验证（说明计算口径对），
      后者其实是个警报（说明那批因子的 IC 算的是量纲）。
    """
    import numpy as np
    import pandas as pd
    s = summary(h)
    c = pd.read_parquet(cat, columns=['factor_id', 'name_cn', 'src_dup',
                                      'xs_comparable'])
    s = s.merge(c, on='factor_id')
    xl = os.path.join(WS, 'factors.xlsx')
    if not os.path.isfile(xl):
        raise SystemExit('没找到 %s' % xl)
    x = pd.read_excel(xl)
    ic_col = [k for k in x.columns if 'IC' in str(k)][0]
    x = (x[[x.columns[0], ic_col]]
         .rename(columns={x.columns[0]: 'name_cn', ic_col: 'src_ic'})
         .drop_duplicates('name_cn'))
    m = s.merge(x, on='name_cn', how='inner')
    # 🔴 重名待定的剔掉：`factors.xlsx` 里同一个中文名出现多次时，
    #   对上的那一行未必是同一个因子（同「光看中文名分不出来」）。
    m = m[m['src_ic'].notna() & (~m['src_dup'])]
    print('与 factors.xlsx 的 IC 符号对数（fwd%d）—— 只比符号与序，'
          '【绝对值不可比】\n' % h)
    print('%-22s %5s %9s %9s' % ('', '个数', '符号一致', '秩相关'))
    for lab, sub in [('全部', m),
                     ('✓ 横截面可比', m[m['xs_comparable']]),
                     ('🔴 量纲(元(价格)/股/元每天)', m[~m['xs_comparable']])]:
        if not len(sub):
            continue
        ag = (np.sign(sub['ic']) == np.sign(sub['src_ic'])).mean()
        rc = sub['ic'].rank().corr(sub['src_ic'].rank())
        print('%-22s %5d %8.0f%% %9.3f' % (lab, len(sub), 100 * ag, rc))
    print('\n🔴 必须拆开看：混在一起那个数会同时掩盖两件事 —— '
          '可比的那批一致率高\n   是「计算口径对」的强验证；'
          '量纲那批一致率低是「它们的 IC 算的是量纲」的警报。')
    return 0


# ======================= 详情页那几张图（2026-09-25） =======================
#
# 🔴🔴 **这三样【按需算】，不落分片。** 我第一版的设计是"新增一类分片、全量
#   建一次（估计 40~60 分钟）"—— **量过之后错了两个数量级**：详情页一次只看
#   **一个**因子，而单因子全史 10 个分位的逐日净值只要 **1.7 秒**、行业 IC
#   0.8 秒、衰减 0.9 秒。预算全部 162 个因子 × 9 池才需要那 4 GB 与几小时，
#   而那份数据**没有人会全看**。
#   ★ 所以它们不进 `SCHEMA_VER`、不进 `_meta.json['shards']`、
#     `sync_daily.sh` 也不用陪跑 —— 缓存由调用方（`srv/factors.py`）按
#     **面板指纹** 持有（同「点进详情卡顿」那轮：键里带指纹而不是"算过没有"）。
#
# ⚠ **`lead` 跨断档那条既有缺陷在这里同样成立**（`POOL` 过滤会在一只票的
#   序列里挖出断档，于是极少数行的"k 个窗口之后"其实跨了几年）。没修 ——
#   修它要改所有历史数值，是单独一个决定（同分片那边记的）。


def _chart_base(con, fid, pm, cols, start=None):
    """图表的公共基表：面板 × 因子，按池过滤。

    🔴 **`fid` 会拼进 SQL 的列名**，所以必须先对着目录表白名单校验 ——
      同 `lv/perf.bench_curves` 那条 symbol 白名单（变异放开那道防线时
      注入**真的被执行了**）。这里拒的措辞也要指得到原因。
    """
    import pandas as pd
    _, _, cat, _, _ = _paths()
    if fid not in set(pd.read_parquet(cat)['factor_id']):
        raise KeyError(fid)
    # ★ 读法走 `paths` 的两个正本，不在这里再拼一遍 read_parquet(...)
    panel, fglob = paths.panel_sql(), paths.factor_sql()
    where = [POOL]
    if pm['col']:
        where.append(pm['col'])
    if start is not None:
        where.append("date >= DATE '%s'" % str(start)[:10])
    sel = ', '.join(['jq_code', 'date'] + list(cols))
    con.execute("""CREATE OR REPLACE TABLE cb AS
        SELECT p.*, f."%s" AS val
        FROM (SELECT %s FROM %s WHERE %s) p
        JOIN %s f ON f.jq_code = p.jq_code AND f.date = p.date"""
                % (fid, sel, panel, ' AND '.join(where), fglob))


def nav_curves(fid, pool='all', h=20, win='all'):
    """分位组合的**逐日净值** + 同池等权基准。

    口径（页面上必须原样印出来 —— 同「口径不一致要响亮地说出来」）：
      · 每 **h 个交易日**调一次仓，分位内**等权**，持有到下一个调仓日
      · 收益用面板的 `ret_1d`（**后复权**），逐日链乘
      · **一分钱费用都没扣**，也没有滑点 -> **不是可实现收益**
      · 基准是**同一个池子的全样本等权**，不是沪深300 ——
        这一页别处的超额基准就是"池内横截面等权均值"，
        换成指数的话同一页两套基准，而那不报错

    ★ 分位 `ntile` 带 **tie-break（按代码）**，与 `_year_ic` 同一条 ——
      不带的话并列项谁在前取决于物理行序，曲线每次跑都不一样。
    """
    import pandas as pd
    pm = pool_meta(pool)
    con = _con()
    _chart_base(con, fid, pm, ('ret_1d',))
    con.execute('DELETE FROM cb WHERE ret_1d IS NULL')
    d0 = _win_start(con.execute('SELECT date FROM cb').df()['date'],
                    dict((k, n) for k, _, n in WINDOWS).get(win))
    con.execute("""CREATE OR REPLACE TABLE rb AS
        SELECT date FROM (SELECT date, row_number() OVER (ORDER BY date) - 1 i
                          FROM (SELECT DISTINCT date FROM cb)) WHERE i %% %d = 0""" % h)
    con.execute("""CREATE OR REPLACE TABLE qa AS
        SELECT jq_code, date AS reb,
               ntile(%d) OVER (PARTITION BY date ORDER BY val, jq_code) AS q
        FROM cb WHERE val IS NOT NULL AND date IN (SELECT date FROM rb)""" % pm['nq'])
    # ASOF：每一天归到**最近一次调仓**（= 持有到下一个调仓日）
    dr = con.execute("""SELECT b.date, qa.q, avg(b.ret_1d) r, count(*) n
        FROM cb b ASOF JOIN qa ON b.jq_code = qa.jq_code AND b.date >= qa.reb
        GROUP BY 1, 2 ORDER BY 1, 2""").df()
    bm = con.execute('SELECT date, avg(ret_1d) r FROM cb GROUP BY 1 ORDER BY 1').df()
    con.close()
    if dr.empty:
        return {'dates': [], 'series': [], 'nq': pm['nq']}
    if d0 is not None:
        dr = dr[dr['date'] >= d0]
        bm = bm[bm['date'] >= d0]
    piv = dr.pivot(index='date', columns='q', values='r').sort_index()
    dates = [str(x)[:10] for x in piv.index]
    out = []
    # ★ 给的是**净值**（起点 1.0）不是"净值 − 1" —— `lineChart` 的
    #   `pctAxis` 就按净值显示成 %，`log + ratioAxis` 按倍数显示；
    #   给收益率的话对数轴那条路要对负值做 clamp，**而图看着完全正常**。
    for q in sorted(piv.columns):
        nav = (1 + piv[q].fillna(0)).cumprod()
        out.append({'q': int(q), 'nav': [round(float(x), 6) for x in nav]})
    b = bm.set_index('date')['r'].reindex(piv.index).fillna(0)
    out.append({'q': 0, 'nav': [round(float(x), 6) for x in (1 + b).cumprod()]})
    return {'dates': dates, 'series': out, 'nq': pm['nq'], 'h': h, 'win': win}


def industry_ic(fid, pool='all', h=20, win='all', min_n=10):
    """**行业内** IC（申万一级 31 个）。

    ★ 用面板的 `sw_l1_name` —— 它本来就是 as-of 的（PIT）。
    🔴 **本地只有申万一级**：常见看板画的是 11 个大类（GICS/中证口径），
      而把 31 个归成 11 个必然掺进"谁定的口径"，页面上又看不出来
      （同「别人的股息率里藏着别人的窗口」）。所以这里就画 31 个，
      并把口径写在图上。
    ★ 当日行业内**不足 `min_n` 只就不算那一天** —— 3 只票的秩相关是噪声
      （同小池子单独给分位数那条）。
    """
    pm = pool_meta(pool)
    con = _con()
    _chart_base(con, fid, pm, ('close_hfq', 'sw_l1_name'))
    con.execute("""CREATE OR REPLACE TABLE iw AS SELECT jq_code, date, sw_l1_name, val,
        lead(close_hfq, %d) OVER (PARTITION BY jq_code ORDER BY date)
          / close_hfq - 1 AS f1 FROM cb""" % h)
    d0 = _win_start(con.execute('SELECT date FROM iw').df()['date'],
                    dict((k, n) for k, _, n in WINDOWS).get(win))
    w = "" if d0 is None else " AND date >= DATE '%s'" % str(d0)[:10]
    d = con.execute("""SELECT sw_l1_name ind, count(*) nday, avg(ic) ic FROM (
          SELECT date, sw_l1_name, corr(rv, rf) ic FROM (
            SELECT date, sw_l1_name,
                   rank() OVER (PARTITION BY date, sw_l1_name ORDER BY val, jq_code) rv,
                   rank() OVER (PARTITION BY date, sw_l1_name ORDER BY f1, jq_code) rf
            FROM iw WHERE val IS NOT NULL AND f1 IS NOT NULL
                      AND sw_l1_name IS NOT NULL%s)
          GROUP BY 1, 2 HAVING count(*) >= %d)
        GROUP BY 1 ORDER BY ic""" % (w, min_n)).df()
    con.close()
    return [{'ind': r['ind'], 'nday': int(r['nday']), 'ic': float(r['ic'])}
            for _, r in d.iterrows() if r['ic'] == r['ic']]


def decay(fid, pool='all', h=20, win='all', k=10):
    """**分位收益衰减**：t 期形成分位，持有第 1..k 个 **h 日窗口**的超额。

    定义（2026-09-25 用户定）：第 j 段的收益是
    `close_hfq[t + j*h] / close_hfq[t + (j-1)*h] - 1`，
    减去**当日横截面**同一段的等权均值 —— 与这一页别处的超额基准同一个。

    🔴 **量级远小于 1**（实测 mv 的最小分位 lag1 +0.47%、lag10 +0.30%）。
      有些看板那张"衰减图"画的是 **0.93~0.99** 的曲线，那是**持仓重合度**
      （1 − 累计换手），**不是这个**。两者都叫"衰减"，混着读会得出相反的
      结论，所以图上要把口径写死。
    ★ 只画两端分位（与换手率图同一套）—— 中间分位的衰减没有可读的意义。
    """
    pm = pool_meta(pool)
    con = _con()
    _chart_base(con, fid, pm, ('close_hfq',))
    seg = ',\n '.join(
        '%s / %s - 1 AS s%d'
        % (_lead('close_hfq', j * h), _lead('close_hfq', (j - 1) * h), j)
        for j in range(1, k + 1))
    con.execute('CREATE OR REPLACE TABLE sg AS SELECT jq_code, date, val, %s FROM cb' % seg)
    d0 = _win_start(con.execute('SELECT date FROM sg').df()['date'],
                    dict((kk, n) for kk, _, n in WINDOWS).get(win))
    w = "" if d0 is None else " AND date >= DATE '%s'" % str(d0)[:10]
    mm = ', '.join('avg(s%d) OVER (PARTITION BY date) m%d' % (j, j) for j in range(1, k + 1))
    ex = ', '.join('avg(s%d - m%d) d%d' % (j, j, j) for j in range(1, k + 1))
    d = con.execute("""SELECT q, count(*) nrow, %s FROM (
          SELECT ntile(%d) OVER (PARTITION BY date ORDER BY val, jq_code) q, %s, *
          FROM sg WHERE val IS NOT NULL%s
            AND date IN (SELECT date FROM (
                  SELECT date, row_number() OVER (ORDER BY date) - 1 i
                  FROM (SELECT DISTINCT date FROM sg)) WHERE i %% %d = 0))
        WHERE q IN (1, %d) GROUP BY 1 ORDER BY 1"""
                    % (ex, pm['nq'], mm, w, h, pm['nq'])).df()
    con.close()
    out = []
    for _, r in d.iterrows():
        out.append({'q': int(r['q']), 'nrow': int(r['nrow']),
                    'ex': [(None if r['d%d' % j] != r['d%d' % j] else float(r['d%d' % j]))
                           for j in range(1, k + 1)]})
    return {'lags': list(range(1, k + 1)), 'series': out, 'nq': pm['nq']}


def _lead(col, n):
    """`lead(col, 0)` 在 duckdb 里是合法的，但写成 `col` 更省一个窗口算子。"""
    return (col if n == 0 else
            'lead(%s, %d) OVER (PARTITION BY jq_code ORDER BY date)' % (col, n))


def top_bottom(fid, pool='all', n=20):
    """最新一天因子值最大 / 最小的 n 只（带中文名）。

    ★ 名称取**面板**的 `sec_name`（PIT），不去快照里再找一遍 ——
      那是另一份实现（同「名称在服务端补」那条）。
    """
    pm = pool_meta(pool)
    con = _con()
    _chart_base(con, fid, pm, ('sec_name',))
    day = con.execute('SELECT max(date) FROM cb WHERE val IS NOT NULL').fetchone()[0]
    if day is None:
        con.close()
        return {'date': None, 'top': [], 'bottom': []}
    d = con.execute("""SELECT jq_code, sec_name, val FROM cb
        WHERE date = ? AND val IS NOT NULL ORDER BY val DESC""", [day]).df()
    con.close()
    rows = [{'code': r['jq_code'], 'name': r['sec_name'], 'val': float(r['val'])}
            for _, r in d.iterrows()]
    return {'date': str(day)[:10], 'n_all': len(rows),
            'top': rows[:n], 'bottom': rows[::-1][:n]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--build', action='store_true', help='强制重算（忽略增量判据）')
    ap.add_argument('--pool', default='all',
                    help='看哪个池：%s' % '/'.join(POOL_KEYS))
    ap.add_argument('--pools', help='只重建这几个池（逗号分隔），默认全部')
    ap.add_argument('--year', type=int, action='append',
                    help='只重建这一年（可给多次），调试用')
    ap.add_argument('--plan', action='store_true',
                    help='只打印要重建哪几片，一个文件都不动')
    ap.add_argument('--build-only', action='store_true', dest='build_only',
                    help='只重建、不打排行榜（sync_daily 用）')
    ap.add_argument('--h', type=int, default=20, choices=HORIZONS)
    ap.add_argument('--win', default='all',
                    choices=[k for k, _, _ in WINDOWS], help='时间段')
    ap.add_argument('--top', type=int, default=15)
    ap.add_argument('--factor', help='看一个因子的逐年表现')
    ap.add_argument('--vs-xlsx', action='store_true',
                    help='与 factors.xlsx 的 IC 做【符号】对数（唯一的外部参照）')
    ap.add_argument('--with-abs', action='store_true',
                    help='连横截面不可比的（单位 元/股/元每天）一起列')
    a = ap.parse_args()

    _, fglob, cat, _, _ = _paths()
    if a.pool not in POOL_KEYS:
        raise SystemExit('没有这个池：%s（有的是 %s）' % (a.pool, '/'.join(POOL_KEYS)))
    pools = a.pools.split(',') if a.pools else None
    if a.plan:
        need, diag = plan(pools=pools, years=a.year, force=a.build)
        print('spec_sig=%s  面板 %d 年  因子面板 %d 年'
              % (diag['spec_sig'], len(diag['panel_years']), len(diag['factor_years'])))
        if not need:
            print('全部是最新的 —— 无事可做')
        for k, y, todo in need:
            print('  %-8s %d  %s' % (k, y, ' + '.join(sorted(todo))))
        print('\n合计 %d 片（%d 个分片文件）'
              % (len(need), sum(len(t) for _, _, t in need)))
        return 0
    # 🔴 **增量**：判据挂在【面板】年文件与因子口径 spec_sig 上，不挂在因子
    #   文件的 mtime 上 —— `build_factor_daily` 是 all-or-nothing，24 个年文件
    #   每天全被重写，拿它当判据等于天天全量（见 `_panel_years`）。
    n = build(pools=pools, years=a.year, force=a.build)
    if a.build_only:
        # ★ 定时链里只要"建没建、建了几片"，不要那张 15 行的排行榜 ——
        #   日志里每天多 20 行噪声，真有内容的那几行就被冲掉了。
        return 0
    del n

    import pandas as pd
    if a.factor:
        ic = _load_shards('ic', a.pool)
        if ic is None:
            raise SystemExit('「%s」这个池还没算过' % pool_meta(a.pool)['label'])
        ic = ic[(ic['factor_id'] == a.factor) & (ic['h'] == a.h)]
        if ic.empty:
            raise SystemExit('没有 %s（h=%d）' % (a.factor, a.h))
        y = ic.assign(year=pd.to_datetime(ic['date']).dt.year).groupby('year')
        print('%s  fwd%d  逐年' % (a.factor, a.h))
        print('%6s %6s %9s %9s %9s' % ('年', '天数', 'IC均值', '正比例', '多空差'))
        for k, s in y:
            print('%6d %6d %9.4f %9.1f%% %9.4f'
                  % (k, len(s), s['ic'].mean(), 100 * (s['ic'] > 0).mean(),
                     s['q_hi'].mean() - s['q_lo'].mean()))
        return 0

    if a.vs_xlsx:
        return vs_xlsx(a.h, cat)

    s = summary(a.h, a.win, pool=a.pool)
    cn = pd.read_parquet(cat, columns=['factor_id', 'name_cn', 'group_cn',
                                   'unit', 'xs_comparable'])
    s = s.merge(cn, on='factor_id', how='left')
    n_abs = int((~s['xs_comparable']).sum())
    if not a.with_abs:
        # 🔴 **默认排除**横截面不可比的（标度由个股自己决定：元(价格)/股/元每天）。
        #   不是藏起来 —— 下面把个数与入口都说出来（同「删了要留痕」）。
        # ⚠ 这里原来写的是"前 12 名里有 7 个是量纲伪信号"，2026-09-24 重新
        #   量过：拆细单位之后是 **3 个**，而且是【成交量(股)】不是股价那一族。
        #   那句话当年就**两头都不准** —— 它把成交金额（可比）也算了进去，
        #   还统一描述成"股价 × 分红拆细"（那只对 ma/ema 那一族成立）。
        s = s[s['xs_comparable']]
    s = s.reindex(s['t_adj'].abs().sort_values(ascending=False).index)
    wl = dict((k, t) for k, t, _ in WINDOWS)[a.win]
    pm = pool_meta(a.pool)
    print('\n【%s】%d 分位 · 当日最少 %d 只' % (pm['label'], pm['nq'], pm['min_xs']))
    print('fwd%d · %s  按 |t_adj| 排序（🔴 t_adj 已按 N/h 折重叠窗口；'
          't_naive 虚高约 √%d = %.1f 倍）\n' % (a.h, wl, a.h, a.h ** 0.5))
    print('%-16s %-12s %7s %7s %9s %9s %7s %7s %8s'
          % ('因子', '中文名', 'IC均值', 'IR',
             '最小分位', '最大分位', '低换手', '高换手', 't_adj'))
    print('%-16s %-12s %7s %7s %9s %9s %7s %7s %8s'
          % ('', '', '', '', '超额年化', '超额年化', '', '', ''))
    for _, r in s.head(a.top).iterrows():
        # 🔴 横截面不可比的单独标出来 —— 它的 IC 是在量纲上算出来的
        mk = '' if r.get('xs_comparable', True) else ' 🔴量纲'
        f = lambda v: '—' if pd.isna(v) else '%+8.2f%%' % (100 * v)
        g = lambda v: '—' if pd.isna(v) else '%6.1f%%' % (100 * v)
        print('%-16s %-12s %+7.4f %+7.3f %9s %9s %7s %7s %+8.2f%s'
              % (r['factor_id'], str(r['name_cn'])[:12], r['ic'], r['ir'],
                 f(r.get('q_lo_ex_ann')), f(r.get('q_hi_ex_ann')),
                 g(r.get('to_lo')), g(r.get('to_hi')), r['t_adj'], mk))
    if not a.with_abs and n_abs:
        print('\n🔴 另有 %d 个【没有列出来】：单位是 元(价格)/股/元每天 —— '
              '标度由个股自己决定，横截面不可比。' % n_abs)
        print('   价格那族：hfq_factor 跨票 1.00~5899.9，"排第几"排的是'
              '「股价 × 上市以来分红拆细」；')
        print('   成交量那族：标度是股本，大盘股天然成交几亿股。')
        print('   ★ 元(金额)【不在此列】—— 元是全市场共同标度，市值 / 成交额 /'
              '财务绝对额都可比。')
        print('   要看就 --with-abs（会标「🔴量纲」）；要拿它们做横截面，')
        print('   得先除以价格/成交额或做市值中性化 —— 那是下一层的事。')
    print('\n口径：%s' % POOL)
    if pm['col']:
        print('池子：%s（面板 %s 列，成分按两步 ASOF 的 PIT 语义）' % (pm['label'], pm['col']))
    if pm['nq'] != 10:
        print('🔴 这个池分 %d 组（不是 10 组）—— 成分太少，照 10 组切每组只有'
              '几只，分位收益是噪声。【与别的池的分位数不可直接比大小】' % pm['nq'])
    print('超额基准：当日横截面【等权平均】收益（不是指数）—— '
          'factors.xlsx 用什么基准未知，两边数值【不可比大小】，只能比符号与序')
    print('换手：每 %d 个交易日调一次仓，相邻两次该分位成分变了百分之几' % a.h)
    print('前瞻：close_hfq[t+%d]/close_hfq[t] − 1（后复权 close-to-close，'
          '【不可实现】，要问能赚多少得去回测）' % a.h)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
