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
               avg(CASE WHEN q = %(nq)d THEN fw END) AS q_hi
        FROM rk GROUP BY 1, 2 HAVING count(*) >= %(mn)d
    """ % {'h': h, 'nq': NQ, 'mn': MIN_XS}).df()


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
        print('前瞻收益 + 横截面排名  %.0fs' % (time.time() - t0))
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
            print('  %s  %4.0fs' % (os.path.basename(fp)[7:11], time.time() - t))
    ic = pd.concat(frames, ignore_index=True)
    os.makedirs(OUT, exist_ok=True)
    p = os.path.join(OUT, 'factor_ic.parquet')
    tmp = p + '.tmp'
    ic.to_parquet(tmp, index=False)
    os.replace(tmp, p)
    m = {'built_at': time.strftime('%Y-%m-%d %H:%M:%S'),
         'factor_panel_sig': _sig(fglob), 'pool': POOL, 'horizons': list(HORIZONS),
         'nq': NQ, 'min_xs': MIN_XS, 'rows': len(ic),
         'fwd': 'close_hfq[t+h] / close_hfq[t] - 1  (后复权 close-to-close)'}
    with open(META, 'w') as f:
        json.dump(m, f, ensure_ascii=False, indent=1)
    if not quiet:
        print('\n-> %s  %d 行  %.0f 秒' % (p, len(ic), time.time() - t0))
    return ic


def summary(h=20, root=None):
    import numpy as np
    import pandas as pd
    p = os.path.join(OUT, 'factor_ic.parquet')
    if not os.path.isfile(p):
        raise SystemExit('还没算过 —— 先跑一次 python3 assay/factor_eval.py')
    ic = pd.read_parquet(p)
    ic = ic[ic['h'] == h]
    g = ic.groupby('factor_id')
    out = pd.DataFrame({
        'nday': g['ic'].size(),
        'ic': g['ic'].mean(),
        'ic_sd': g['ic'].std(),
        'pos': g['ic'].apply(lambda s: (s > 0).mean()),
        'spread': (g['q_hi'].mean() - g['q_lo'].mean()),
    })
    out['ir'] = out['ic'] / out['ic_sd']
    out['t_naive'] = out['ir'] * np.sqrt(out['nday'])
    # 🔴 重叠窗口：有效样本按 N/h 折。粗，但方向对；不折的话 t 虚高 √h 倍。
    out['t_adj'] = out['ir'] * np.sqrt(out['nday'] / float(h))
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

    s = summary(a.h)
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
    print('\nfwd%d  按 |t_adj| 排序（🔴 t_adj 已按 N/h 折重叠窗口；'
          't_naive 虚高约 √%d = %.1f 倍）\n' % (a.h, a.h, a.h ** 0.5))
    print('%-16s %-14s %7s %7s %7s %8s %8s %9s'
          % ('因子', '中文名', 'IC均值', 'IR', '正比例', 't_adj', 't_naive', '多空差'))
    for _, r in s.head(a.top).iterrows():
        # 🔴 横截面不可比的单独标出来 —— 它的 IC 是在量纲上算出来的
        mk = '' if r.get('xs_comparable', True) else '  🔴量纲'
        print('%-16s %-14s %+7.4f %+7.3f %6.1f%% %+8.2f %+8.2f %+9.4f%s'
              % (r['factor_id'], str(r['name_cn'])[:14], r['ic'], r['ir'],
                 100 * r['pos'], r['t_adj'], r['t_naive'], r['spread'], mk))
    if not a.with_abs and n_abs:
        print('\n🔴 另有 %d 个【没有列出来】：单位是 元/股/元每天，横截面不可比。'
              % n_abs)
        print('   hfq_factor 跨票 1.00~5899.9，它们的"排第几"排的是'
              '「股价 × 上市以来分红拆细」而不是信号；实测与 factors.xlsx')
        print('   符号对不上的 19 个【全部】落在这几种单位里。')
        print('   要看就 --with-abs（会标「🔴量纲」）；要拿它们做横截面，')
        print('   得先除以价格/成交额或做市值中性化 —— 那是下一层的事。')
    print('\n口径：%s' % POOL)
    print('前瞻：close_hfq[t+%d]/close_hfq[t] − 1（后复权 close-to-close，'
          '【不可实现】，要问能赚多少得去回测）' % a.h)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
