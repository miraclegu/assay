# -*- coding: utf-8 -*-
"""因子广场的服务端：列表 / 详情 / 可选项。

    /api/factors          列表：code / 名称 / 分位超额年化 / 分位换手 / IC / IR
    /api/factor           详情：介绍 / 公式 / 依赖 / 逐年表现 / 各区间对比
    /api/factors/meta     可选项：时间段、前瞻窗口、族、口径说明

## 🔴 可选清单由服务端给，页面不认识任何一个因子名

加一个因子，广场上**自动就有** —— 前端硬编码一份的话，新因子页面上不会出现，
**而那不报错**（同「加一个指标，广场上自动就有」「`/api/sector/kinds` 说有几类，
盘面就得链得到几类」）。时间段与前瞻窗口同理：它们定义在
`assay/factor_eval.py` 的 `WINDOWS` / `HORIZONS`，页面照着渲染。

## 🔴 口径也由服务端给，且必须显示

这一页的每个数都带着三条口径（股票池 / 超额基准 / 换手怎么算），而
**`factors.xlsx` 用什么基准是未知的** —— 不写出来的话，人会拿这一页的
"最大分位超额年化 −19.86%" 去跟那份表比大小，而那是在比两个不同的量
（同「别人的『股息率』里藏着别人的窗口与去重规则」）。

## ⚠ 这一页的数**不是可实现收益**

前瞻收益是 close-to-close（隐含在收盘价成交），分位组也没有交易成本。
它回答的是"这个因子有没有预测力"，不是"照它做能赚多少" ——
后者要去回测。这句话同样由服务端给，页面原样印出来。
"""
import os

from .base import _repo_root


def _eval():
    """延迟导入 —— `factor_eval` 会 import duckdb 并读 parquet，
    而服务端启动时不该为一个低频页面付这个成本。"""
    import sys
    r = _repo_root()
    if r not in sys.path:
        sys.path.insert(0, r)
    from assay import factor_eval as fe
    return fe


def _cat(fe):
    import pandas as pd
    _, _, cat = fe._paths()
    return pd.read_parquet(cat)


def api_factors_meta(_q):
    """页面渲染要的那几份清单 + 口径说明。"""
    fe = _eval()
    cat = _cat(fe)
    grp = (cat.groupby(['group_key', 'group_cn']).size()
              .reset_index(name='n').to_dict('records'))
    return {
        'windows': [{'k': k, 'label': t} for k, t, _ in fe.WINDOWS],
        'horizons': list(fe.HORIZONS),
        'groups': grp,
        'n_factors': int(len(cat)),
        'n_abs': int((~cat['xs_comparable']).sum()),
        'pool': fe.POOL,
        'excess_base': '当日横截面【等权平均】收益（不是指数）',
        'turnover': '每 h 个交易日调一次仓，相邻两次该分位成分变了百分之几',
        'fwd': 'close_hfq[t+h] / close_hfq[t] − 1（后复权 close-to-close）',
        # 🔴 这三句必须原样显示在页面上，见模块 docstring
        'caveats': [
            '这一页的数【不是可实现收益】：前瞻是 close-to-close（隐含在收盘价'
            '成交）、分位组没有交易成本。它回答"有没有预测力"，不是"能赚多少"。',
            '超额的基准是当日横截面等权平均，而 factors.xlsx 用什么基准未知 —— '
            '两边【只能比符号与序，不能比数值大小】。',
            'fwd5 / fwd20 的前瞻窗口是重叠的，所以 t 值会虚高约 √h 倍；'
            '这里给的 t_adj 已按 N/h 折过，但它仍偏乐观。',
        ],
    }


_SUM = {}


def _summary(fe, h, win):
    """按 (h, win, 文件 mtime) 缓存 —— 一次 0.4 秒，而详情页要 6 个区间。

    🔴 键里带 mtime 而不是"算过没有"：`factor_eval.py` 重算之后这份缓存
      必须自己失效，否则页面上是旧数而它不报错
      （同「判据永远是现在的状态，不是记录」）。
    """
    import os as _os
    mt = _os.path.getmtime(_os.path.join(fe.OUT, 'factor_ic.parquet'))
    k = (h, win, mt)
    if k not in _SUM:
        _SUM.clear()          # 换了文件就整份丢掉，不留旧 mtime 的残骸
        _SUM[k] = fe.summary(h, win)
    return _SUM[k]


def api_factors(q):
    """列表。参数：`win`（时间段）/ `h`（前瞻）/ `group` / `abs`（含不含量纲）。"""
    import pandas as pd
    fe = _eval()
    win = (q.get('win') or 'all')
    h = int(q.get('h') or 20)
    if win not in [k for k, _, _ in fe.WINDOWS] or h not in fe.HORIZONS:
        return {'error': '不认识的 win/h'}
    s = _summary(fe, h, win)
    cat = _cat(fe)
    m = s.merge(cat, on='factor_id', how='left')
    if q.get('group'):
        m = m[m['group_key'] == q['group']]
    # 🔴 默认不列横截面不可比的（前 12 名里有 7 个是量纲伪信号，而
    #   "常驻告警等于教人忽略这个位置"）；个数与开关都给页面，不是藏起来。
    n_abs = int((~m['xs_comparable']).sum())
    if not q.get('abs'):
        m = m[m['xs_comparable']]
    m = m.reindex(m['t_adj'].abs().sort_values(ascending=False).index)
    cols = ['factor_id', 'name_cn', 'group_key', 'group_cn', 'unit', 'tier',
            'tier_cn', 'xs_comparable', 'src_dup', 'ic', 'ir', 'pos',
            'q_lo_ex_ann', 'q_hi_ex_ann', 'to_lo', 'to_hi', 'spread',
            't_adj', 't_naive', 'nday', 'nreb']
    have = [c for c in cols if c in m.columns]
    rows = m[have].where(pd.notna(m[have]), None).to_dict('records')
    return {'win': win, 'h': h, 'n': len(rows), 'n_abs_hidden': 0 if q.get('abs')
            else n_abs, 'rows': rows}


def api_factor(q):
    """详情：目录那一行 + 各区间对比 + 逐年表现。"""
    import pandas as pd
    fe = _eval()
    fid = q.get('id') or ''
    cat = _cat(fe)
    row = cat[cat['factor_id'] == fid]
    if row.empty:
        return None
    h = int(q.get('h') or 20)
    info = row.iloc[0].where(pd.notna(row.iloc[0]), None).to_dict()

    # 各区间横向对比 —— 「近 3 月好、全程不好」这种事一眼要看得见
    wins = []
    for k, label, _ in fe.WINDOWS:
        s = _summary(fe, h, k)
        r = s[s['factor_id'] == fid]
        if r.empty:
            continue
        r = r.iloc[0]
        wins.append({'k': k, 'label': label,
                     'ic': _f(r.get('ic')), 'ir': _f(r.get('ir')),
                     'q_lo_ex_ann': _f(r.get('q_lo_ex_ann')),
                     'q_hi_ex_ann': _f(r.get('q_hi_ex_ann')),
                     't_adj': _f(r.get('t_adj')),
                     'to_lo': _f(r.get('to_lo')), 'to_hi': _f(r.get('to_hi')),
                     'nday': int(r['nday']) if pd.notna(r.get('nday')) else None})

    # 逐年 —— 「优势几乎全来自单年」这种事只有逐年看得出来
    ic = pd.read_parquet(os.path.join(fe.OUT, 'factor_ic.parquet'))
    ic = ic[(ic['factor_id'] == fid) & (ic['h'] == h)]
    yrs = []
    if not ic.empty:
        g = ic.assign(y=pd.to_datetime(ic['date']).dt.year).groupby('y')
        for y, sub in g:
            yrs.append({'year': int(y), 'nday': int(len(sub)),
                        'ic': _f(sub['ic'].mean()),
                        'pos': _f((sub['ic'] > 0).mean()),
                        'ex_lo': _f((sub['q_lo'] - sub['mkt']).mean()),
                        'ex_hi': _f((sub['q_hi'] - sub['mkt']).mean())})
    return {'info': info, 'h': h, 'windows': wins, 'years': yrs,
            'meta': api_factors_meta({})}


def _f(v):
    import math
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) or math.isinf(v) else v
