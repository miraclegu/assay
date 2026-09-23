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

目录表里有 `xs_comparable`（由单位推出，定义在 `factors/__init__.py` 的
`ABS_UNITS`）。单位是 **元 / 股 / 元/天** 的因子**不能直接做横截面排序** ——
`hfq_factor` 跨票从 1.00 到 **5899.9**，于是 `ma20` 的"排第几"排的是
「股价 × 上市以来分红拆细」。实测它与不复权股价秩相关 +0.536、与复权因子
+0.421，两个混淆项加起来就是它的全部内容。

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
import json
import os
import sys
import time

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
    return (os.path.join(dl, 'mart', 'panel_daily', 'panel_*.parquet'),
            os.path.join(dl, 'mart', 'factor_daily', 'factor_*.parquet'),
            os.path.join(dl, 'mart', 'factor_catalog.parquet'))


def _sig(fglob):
    """因子面板指纹 —— 面板重建过就必须重算评价。"""
    import glob
    s = []
    for f in sorted(glob.glob(fglob)):
        st = os.stat(f)
        s.append('%s|%d|%d' % (os.path.basename(f), st.st_size, st.st_mtime_ns))
    return hashlib.sha256('\n'.join(s).encode()).hexdigest()[:12]


def _con(threads=8):
    c = duckdb.connect(':memory:')
    c.execute('SET threads=%d' % threads)
    return c


def _fwd(con, panel):
    """前瞻收益 + 当日横截面排名（排名算一次，98 个因子共用）。"""
    lead = ',\n        '.join(
        'lead(close_hfq,%d) OVER w / close_hfq - 1 AS f%d' % (h, h)
        for h in HORIZONS)
    rk = ',\n      '.join(
        'rank() OVER (PARTITION BY date ORDER BY f%d) AS r%d' % (h, h)
        for h in HORIZONS)
    con.execute("""
        CREATE OR REPLACE TABLE fwd AS
        SELECT jq_code, date, %s, %s FROM (
          SELECT jq_code, date, %s
          FROM read_parquet('%s') WHERE %s
          WINDOW w AS (PARTITION BY jq_code ORDER BY date))
    """ % (', '.join('f%d' % h for h in HORIZONS), rk, lead, panel, POOL))


def _long(con, fpath):
    """一年的因子面板 -> 长表。★ 宽表做这一步实测慢 58 倍。"""
    cols = [d[0] for d in con.execute(
        "SELECT * FROM read_parquet('%s') LIMIT 0" % fpath).description]
    fac = [c for c in cols if c not in ('jq_code', 'date')]
    lst = ', '.join(fac)
    con.execute("""
        CREATE OR REPLACE TABLE lng AS
        SELECT jq_code, date, factor_id, val FROM (
          SELECT jq_code, date, UNPIVOT_NAME AS factor_id, UNPIVOT_VAL AS val
          FROM (SELECT jq_code, date, %s FROM read_parquet('%s'))
          UNPIVOT (UNPIVOT_VAL FOR UNPIVOT_NAME IN (%s))
        ) WHERE val IS NOT NULL
    """ % (lst, fpath, lst))
    return fac


def _year_ic(con, h):
    """一年一个 horizon 的 (factor_id, date, ic, n, q1, q10)。"""
    return con.execute("""
        WITH j AS (
          SELECT l.factor_id, l.date, l.val, f.r%(h)d AS rf, f.f%(h)d AS fw
          FROM lng l JOIN fwd f USING (jq_code, date)
          WHERE f.f%(h)d IS NOT NULL),
        rk AS (
          SELECT factor_id, date, rf, fw,
                 rank() OVER (PARTITION BY date, factor_id ORDER BY val) AS rv,
                 ntile(%(nq)d) OVER (PARTITION BY date, factor_id ORDER BY val) AS q
          FROM j)
        SELECT factor_id, date, corr(rv, rf) AS ic, count(*) AS n,
               avg(CASE WHEN q = 1 THEN fw END) AS q_lo,
               avg(CASE WHEN q = %(nq)d THEN fw END) AS q_hi,
               -- 🔴 当日横截面**等权平均**收益 —— 超额的基准。
               --   分位组本身是等权的，拿等权全市场比才是同口径；
               --   拿指数比会混进市值加权与成分差异（同「不拿 ETF 当指数用」）。
               avg(fw) AS mkt
        FROM rk GROUP BY 1, 2 HAVING count(*) >= %(mn)d
    """ % {'h': h, 'nq': NQ, 'mn': MIN_XS}).df()


# ============================ 分位换手率 ============================
#: 换手按【调仓日】采样：每 h 个交易日一次。
#: 🔴 逐日算出来的是**另一个量**（"今天的 top 组与昨天差多少"），
#:   而「换手率」问的是"每次调仓要换掉百分之几" —— 两者差一个数量级。
def _turnover(con, h, fglob):
    """(factor_id, date, to_lo, to_hi) —— 相邻两个调仓日的分位成分变化率。

    换手 = 1 − |A ∩ B| / |A|，A 是本次调仓日的该分位成分、B 是上一次的。
    ★ 只读**调仓日**那几天的面板（5761 个交易日里每 h 天一个），
      所以这一步很便宜 —— 逐日读的话要多算 h 倍而结果并不更准。
    🔴 调仓日历是**全局**的、不按年切：按年切的话每年头一次调仓找不到
      上一次，于是每年少一个点**而它不报错**。
    """
    con.execute("""
        CREATE OR REPLACE TABLE rbd AS
        SELECT date, row_number() OVER (ORDER BY date) - 1 AS i
        FROM (SELECT DISTINCT date FROM fwd) ORDER BY date""")
    con.execute("CREATE OR REPLACE TABLE rb AS "
                "SELECT date, i / %d AS k FROM rbd WHERE i %% %d = 0" % (h, h))
    cols = [d[0] for d in con.execute(
        "SELECT * FROM read_parquet('%s') LIMIT 0" % fglob).description]
    fac = [c for c in cols if c not in ('jq_code', 'date')]
    lst = ', '.join(fac)
    con.execute("""
        CREATE OR REPLACE TABLE mem AS
        WITH lg AS (
          SELECT jq_code, date, factor_id, val FROM (
            SELECT jq_code, date, UNPIVOT_NAME AS factor_id, UNPIVOT_VAL AS val
            FROM (SELECT jq_code, date, %s FROM read_parquet('%s')
                  WHERE date IN (SELECT date FROM rb))
            UNPIVOT (UNPIVOT_VAL FOR UNPIVOT_NAME IN (%s))
          ) WHERE val IS NOT NULL),
        j AS (SELECT l.* FROM lg l JOIN fwd f USING (jq_code, date)),
        q AS (SELECT factor_id, date, jq_code,
                     ntile(%d) OVER (PARTITION BY date, factor_id ORDER BY val) AS q
              FROM j)
        SELECT q.factor_id, rb.k, q.jq_code, q.q
        FROM q JOIN rb ON rb.date = q.date
        WHERE q.q IN (1, %d)
    """ % (lst, fglob, lst, NQ, NQ))
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
        WHERE c.k > 0
    """).df()


def build(root=None, quiet=False):
    panel, fglob, _ = _paths(root)
    import glob as _g
    years = sorted(_g.glob(fglob))
    if not years:
        raise SystemExit('没有因子面板 —— 先跑 datalake/build/build_factor_daily.py')
    con = _con()
    t0 = time.time()
    _fwd(con, panel)
    if not quiet:
        print('前瞻收益 + 横截面排名  %.0fs' % (time.time() - t0), flush=True)
    frames = []
    import pandas as pd
    for i, fp in enumerate(years, 1):
        t = time.time()
        _long(con, fp)
        for h in HORIZONS:
            d = _year_ic(con, h)
            d['h'] = h
            frames.append(d)
        if not quiet:
            print('  %s  %4.0fs' % (os.path.basename(fp)[7:11], time.time() - t),
                  flush=True)
    ic = pd.concat(frames, ignore_index=True)
    os.makedirs(OUT, exist_ok=True)
    p = os.path.join(OUT, 'factor_ic.parquet')
    tmp = p + '.tmp'
    ic.to_parquet(tmp, index=False)
    os.replace(tmp, p)

    # ---- 分位换手：全局调仓日历，不按年切 ----
    # 🔴 **换手这一步是大头，而且极不均匀**：实测 fwd1 2103s / fwd5 237s /
    #   fwd20 46s，fwd1 一个就占全程 70%%。原因是 h=1 -> 每个交易日都是调仓日
    #   （5761 个），自连接的规模比 fwd20 大 20 倍。
    #   ★ 所以进度行要**逐个 h 打**，不然人看到的是"卡了 35 分钟"。
    tos = []
    for h in HORIZONS:
        t = time.time()
        d = _turnover(con, h, fglob)
        d['h'] = h
        tos.append(d)
        if not quiet:
            print('  换手 fwd%-3d %6d 行  %4.0fs' % (h, len(d), time.time() - t),
                  flush=True)
    to = pd.concat(tos, ignore_index=True)
    pt = os.path.join(OUT, 'factor_turnover.parquet')
    to.to_parquet(pt + '.tmp', index=False)
    os.replace(pt + '.tmp', pt)
    m = {'built_at': time.strftime('%Y-%m-%d %H:%M:%S'),
         'factor_panel_sig': _sig(fglob), 'pool': POOL, 'horizons': list(HORIZONS),
         'nq': NQ, 'min_xs': MIN_XS, 'rows': len(ic), 'turnover_rows': len(to),
         'excess_base': '当日横截面等权平均收益（不是指数）',
         'annualize': '(1 + 区间内 h 日超额均值) ^ (244/h) − 1',
         'fwd': 'close_hfq[t+h] / close_hfq[t] - 1  (后复权 close-to-close)'}
    with open(META, 'w') as f:
        json.dump(m, f, ensure_ascii=False, indent=1)
    if not quiet:
        print('\n-> %s  %d 行  %.0f 秒' % (p, len(ic), time.time() - t0))
    return ic


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
    """
    if n is None:
        return None
    u = sorted(set(dates))
    return u[-n] if len(u) > n else u[0]


def summary(h=20, win='all', root=None):
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
    p = os.path.join(OUT, 'factor_ic.parquet')
    if not os.path.isfile(p):
        raise SystemExit('还没算过 —— 先跑一次 python3 assay/factor_eval.py')
    ic = pd.read_parquet(p)
    ic = ic[ic['h'] == h]
    n = dict((k, d) for k, _, d in WINDOWS).get(win, None)
    d0 = _win_start(ic['date'], n)
    if d0 is not None:
        ic = ic[ic['date'] >= d0]
    g = ic.groupby('factor_id')
    ex_lo = g.apply(lambda s: (s['q_lo'] - s['mkt']).mean(), include_groups=False)
    ex_hi = g.apply(lambda s: (s['q_hi'] - s['mkt']).mean(), include_groups=False)
    k = TRADING_DAYS / float(h)
    out = pd.DataFrame({
        'nday': g['ic'].size(),
        'ic': g['ic'].mean(),
        'ic_sd': g['ic'].std(),
        'pos': g['ic'].apply(lambda s: (s > 0).mean()),
        'spread': g['q_hi'].mean() - g['q_lo'].mean(),
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
    pt = os.path.join(OUT, 'factor_turnover.parquet')
    if os.path.isfile(pt):
        to = pd.read_parquet(pt)
        to = to[to['h'] == h]
        if d0 is not None:
            to = to[to['date'] >= d0]
        piv = to.pivot_table(index='factor_id', columns='q', values='turn',
                             aggfunc='mean')
        out['to_lo'] = piv.get(1)
        out['to_hi'] = piv.get(NQ)
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
                     ('🔴 量纲(元/股/元每天)', m[~m['xs_comparable']])]:
        if not len(sub):
            continue
        ag = (np.sign(sub['ic']) == np.sign(sub['src_ic'])).mean()
        rc = sub['ic'].rank().corr(sub['src_ic'].rank())
        print('%-22s %5d %8.0f%% %9.3f' % (lab, len(sub), 100 * ag, rc))
    print('\n🔴 必须拆开看：混在一起那个数会同时掩盖两件事 —— '
          '可比的那批一致率高\n   是「计算口径对」的强验证；'
          '量纲那批一致率低是「它们的 IC 算的是量纲」的警报。')
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--build', action='store_true', help='强制重算')
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

    _, fglob, cat = _paths()
    need = a.build
    if not need:
        try:
            with open(META) as f:
                need = json.load(f).get('factor_panel_sig') != _sig(fglob)
        except Exception:                                   # noqa: BLE001
            need = True
    if need:
        build()

    import pandas as pd
    if a.factor:
        ic = pd.read_parquet(os.path.join(OUT, 'factor_ic.parquet'))
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

    s = summary(a.h, a.win)
    cn = pd.read_parquet(cat, columns=['factor_id', 'name_cn', 'group_cn',
                                   'unit', 'xs_comparable'])
    s = s.merge(cn, on='factor_id', how='left')
    n_abs = int((~s['xs_comparable']).sum())
    if not a.with_abs:
        # 🔴 **默认排除**横截面不可比的：前 12 名里有 7 个是量纲伪信号，
        #   而"常驻一条告警等于教人忽略这个位置"。不是藏起来 ——
        #   下面把个数与入口都说出来（同「删了要留痕」）。
        s = s[s['xs_comparable']]
    s = s.reindex(s['t_adj'].abs().sort_values(ascending=False).index)
    wl = dict((k, t) for k, t, _ in WINDOWS)[a.win]
    print('\nfwd%d · %s  按 |t_adj| 排序（🔴 t_adj 已按 N/h 折重叠窗口；'
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
        print('\n🔴 另有 %d 个【没有列出来】：单位是 元/股/元每天，横截面不可比。'
              % n_abs)
        print('   hfq_factor 跨票 1.00~5899.9，它们的"排第几"排的是'
              '「股价 × 上市以来分红拆细」而不是信号；实测与 factors.xlsx')
        print('   符号对不上的 19 个【全部】落在这几种单位里。')
        print('   要看就 --with-abs（会标「🔴量纲」）；要拿它们做横截面，')
        print('   得先除以价格/成交额或做市值中性化 —— 那是下一层的事。')
    print('\n口径：%s' % POOL)
    print('超额基准：当日横截面【等权平均】收益（不是指数）—— '
          'factors.xlsx 用什么基准未知，两边数值【不可比大小】，只能比符号与序')
    print('换手：每 %d 个交易日调一次仓，相邻两次该分位成分变了百分之几' % a.h)
    print('前瞻：close_hfq[t+%d]/close_hfq[t] − 1（后复权 close-to-close，'
          '【不可实现】，要问能赚多少得去回测）' % a.h)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
