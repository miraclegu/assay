# -*- coding: utf-8 -*-
"""因子广场的服务端：列表 / 详情 / 可选项。

    /api/factors          列表：code / 名称 / 分位超额年化 / 分位换手 / IC / IR
    /api/factor           详情：介绍 / 公式 / 依赖 / 逐年表现 / 各区间对比
    /api/factors/meta     可选项：时间段、前瞻窗口、族、口径说明
    /api/factors/missing  算不出来的那些：名字 + 为什么（POST 同址：手工加/删）

## 🔴 「算不出来的」也要有名有姓 —— 否则"查不到"与"本来就没有"分不开

原清单 278 个名字，这一页只列得出实现了的那 161 个。剩下 117 个此前
**在广场上根本不存在** —— 人搜一个名字搜不到，分不出是"名字打错了"、
"被量纲开关藏起来了"还是"本地根本算不了"（同「删了要留痕」
「拿不到分红那一格标『查不到』，不猜一个数」那两条）。

★ 清单与原因的**正本在 `datalake/build/factors/__init__.py` 的 `MISSING`**，
  与 `FACTORS` 是同一份名单的两半，建目录表时有覆盖自证（两半必须正好分完
  原清单，不过就拒绝写出）。这边只负责读出来给页面。
🔴 `blocked` 那一位必须带到页面上：`todo` 那 65 个**不是算不出来**，
  只是还没写 —— 混成一句"暂时无法计算"就是在说谎。

## 🔴 清单有【两个来源】，每条都要说清自己是哪来的

    registry  注册表那 117 个（代码里的正本，对着 factors.xlsx 逐条定案）
    manual    人在页面上加的（`live/factor_missing.jsonl`，append-only）

合成一份给页面，但 `source` 必须带出去 —— 页面上只有 manual 那些给删除
按钮。不分的话人会去删一条注册表里的，**而那删不掉**
（同「给一个点了没反应的按钮比不给更糟」）。

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

from . import base
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
    _, _, cat, _, _ = fe._paths()
    return pd.read_parquet(cat)


_MCOLS = ['name_cn', 'reason_key', 'reason_cn', 'reason_why', 'detail',
          'blocked', 'src_row', 'source']


def _reg_miss(fe):
    """注册表那一半。表还没建出来时返回空表 —— 只是这一块不显示，
    不该让整个因子广场打不开（同「外部挂了退回本地那份，不让整页打不开」）。"""
    import os as _os

    import pandas as pd
    _, _, _, mp, _ = fe._paths()
    if not _os.path.isfile(mp):
        return pd.DataFrame(columns=_MCOLS)
    d = pd.read_parquet(mp)
    d['source'] = 'registry'
    return d


def _reasons(fe):
    """全量原因表（含一条因子都没有的那些档）。

    🔴 不照"清单里出现过的 key"去拼 —— 某一档被实现光了它就**静默从
      下拉里消失**，而那不报错（同「存的是隐藏哪些、不是显示哪些」）。
    ★ 文件缺了就退回按行去重，并在返回里说一句 —— 不让整页打不开。
    """
    import json
    import os as _os
    _, _, _, _, rp = fe._paths()
    if _os.path.isfile(rp):
        with open(rp, encoding='utf-8') as fh:
            return json.load(fh), None
    return None, ('没有 mart/factor_reasons.json（跑一次 '
                  'python3 datalake/build/build_factor_catalog.py）'
                  ' —— 原因清单退回按已有的行去重，'
                  '一条因子都没有的那些档这次不会出现在下拉里')


def _manual():
    import sys
    r = _repo_root()
    if r not in sys.path:
        sys.path.insert(0, r)
    from assay import factor_missing as fm
    return fm


def _miss(fe):
    """两个来源合成一份：注册表那 117 个 + 人在页面上加的。

    🔴 原因文案（label / why / blocked）只有注册表那份有 —— 手工那条只存
      `reason_key`，文案在这里**按 key join 回去**。两边各存一份文案的话，
      改一句 `MISS_REASONS` 之后手工那些还挂着旧话，而它不报错。
    ★ 手工那条的 key 在注册表里已经不用了（比如那一档被清空了）也**不丢**：
      照实显示 key 并说一句"这个原因注册表里已经没有了"，
      不静默改成别的（同「拿不到分红那一格标查不到，不猜一个数」）。
    """
    import pandas as pd
    reg = _reg_miss(fe)
    txt = {}
    for _, r in reg.iterrows():
        txt.setdefault(r['reason_key'],
                       (r['reason_cn'], r['reason_why'], bool(r['blocked'])))
    rows = []
    for m in _manual().current():
        lab, why, blk = txt.get(m['reason_key'], (
            m['reason_key'],
            '这个原因在注册表的 MISS_REASONS 里已经没有了 —— '
            '这一条是它还在的时候加的。', True))
        rows.append({'name_cn': m['name_cn'], 'reason_key': m['reason_key'],
                     'reason_cn': lab, 'reason_why': why, 'detail': m['detail'],
                     'blocked': blk, 'src_row': None, 'source': 'manual'})
    man = pd.DataFrame(rows, columns=_MCOLS)
    if not len(reg):
        return man
    if not len(man):
        return reg
    return pd.concat([reg, man], ignore_index=True)


def api_factors_meta(_q):
    """页面渲染要的那几份清单 + 口径说明。"""
    fe = _eval()
    cat = _cat(fe)
    miss = _miss(fe)
    grp = (cat.groupby(['group_key', 'group_cn']).size()
              .reset_index(name='n').to_dict('records'))
    return {
        'windows': [{'k': k, 'label': t} for k, t, _ in fe.WINDOWS],
        'horizons': list(fe.HORIZONS),
        'groups': grp,
        'n_factors': int(len(cat)),
        'n_abs': int((~cat['xs_comparable']).sum()),
        # 🔴 名字也给 —— 页面搜不到时要能说出「你搜的这个在【算不出来】那份
        #   清单里」。只给计数的话，"没有匹配"与"算不出来"在屏幕上一个样。
        #   117 个名字约 1.5 KB，而这一页本来就要拉一张上百行的表。
        'n_missing': int(len(miss)),
        'missing_names': [str(x) for x in miss['name_cn']],
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
    # 🔴 只在【文件变了】时整份丢掉。第一版写成"每次 miss 就 clear()" ——
    #   于是缓存**最多只留 1 项**，而详情页要 6 个区间、逐个 miss 逐个清空，
    #   等于没有缓存（实测每次进详情都重算 6 遍 = 2.7 秒，用户报"卡顿"）。
    #   ⚠ 当时的注释写的是"换了**文件**就丢掉"，而代码写的是"换了 **key**
    #     就丢掉" —— **注释是对的，代码不是**（同「代码在和自己的注释打架」）。
    if _SUM.get('_mt') != mt:
        _SUM.clear()
        _SUM['_mt'] = mt
    k = (h, win)
    if k not in _SUM:
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
            # 🔴 IR 与主表**同一个口径**（`summary` 里 ir = ic / ic_sd，
            #   pandas std 默认 ddof=1）—— 另写一套的话"主表 IR 与逐年 IR
            #   对不上"，而它不报错（同「两处实现必然分叉」）。
            yrs.append({'year': int(y), 'nday': int(len(sub)),
                        'ic': _f(sub['ic'].mean()),
                        'ir': _f(sub['ic'].mean() / sub['ic'].std()),
                        'pos': _f((sub['ic'] > 0).mean()),
                        'ex_lo': _f((sub['q_lo'] - sub['mkt']).mean()),
                        'ex_hi': _f((sub['q_hi'] - sub['mkt']).mean())})
    return {'info': info, 'h': h, 'windows': wins, 'years': yrs,
            'meta': api_factors_meta({})}


def api_factors_missing(_q):
    """算不出来的那些：按原因分组，每组带「为什么」，每条带细节。

    ★ 分组顺序照注册表里 `MISS_REASONS` 的声明顺序（做不了的在前、
      "还没写"的在最后）—— 页面不认识任何一个原因 key，照给的顺序渲染。
    """
    import pandas as pd
    fe = _eval()
    m = _miss(fe)
    if not len(m):
        return {'n': 0, 'reasons': [], 'rows': [],
                'why_empty': '还没建 mart/factor_missing.parquet —— '
                             '跑一次 python3 datalake/build/build_factor_catalog.py'}
    full, warn = _reasons(fe)
    if full is None:
        full, seen = [], []
        for k in m['reason_key']:
            if k in seen:
                continue
            seen.append(k)
            r0 = m[m['reason_key'] == k].iloc[0]
            full.append({'key': k, 'label': str(r0['reason_cn']),
                         'why': str(r0['reason_why']),
                         'blocked': bool(r0['blocked'])})
    used = list(m['reason_key'])
    reasons, opts = [], []
    for r in full:
        n = int(sum(1 for k in used if k == r['key']))
        opts.append(dict(r, n=n))
        if n:
            reasons.append(dict(r, n=n))
    # 注册表里已经没有、而手工那条还挂着的 key：照实列出来，不静默丢
    for k in used:
        if k not in [r['key'] for r in full] and k not in [r['key'] for r in reasons]:
            r0 = m[m['reason_key'] == k].iloc[0]
            reasons.append({'key': k, 'label': str(r0['reason_cn']),
                            'why': str(r0['reason_why']),
                            'blocked': bool(r0['blocked']),
                            'n': int(sum(1 for x in used if x == k))})
    rows = m.where(pd.notna(m), None).to_dict('records')
    for r in rows:
        r['blocked'] = bool(r['blocked'])
        r['src_row'] = None if r['src_row'] is None else int(r['src_row'])
    return {
        'n': int(len(m)),
        'n_blocked': int(m['blocked'].sum()),
        'n_todo': int((~m['blocked'].astype(bool)).sum()),
        'n_manual': int((m['source'] == 'manual').sum()),
        'reasons': reasons,
        # 🔴 「手工加一条」那个下拉照它渲染 —— 含 n=0 的档，见 `_reasons`
        'options': opts,
        'can_write': bool(base.ALLOW_LIVE),
        'warn': warn,
        'rows': rows,
        # 这两句与列表页那三条 caveats 同一条纪律：口径由服务端给、页面原样印
        'note': '原清单（factors.xlsx）有 278 个不同的名字，这一页列的是其中'
                '【本地还没有值】的那些。它与因子广场那张表是同一份名单的两半，'
                '建目录表时有覆盖自证：两半必须正好分完原清单，不重不漏。',
        'todo_note': '🔴 最后那一档【不是算不出来】—— 数据齐、公式也确定，'
                     '只是注册表里还没有这一条。别把它读成"这东西算不了"。',
    }


def api_factors_missing_act(_q, body):
    """POST /api/factors/missing —— 手工加一条 / 删一条。

    ★ 归 --live 管：它写 `live/` 下的账本（同 alerts / watchlist 那两条）。
    🔴 **只删得掉手工那一半**。注册表里的是代码，页面删不动 —— 所以那边
      根本不给删除按钮，而这里再挡一道：前端漏拦的话，报错也要说得出
      "去写一条 Spec"这个下一步，不是一句 500（同「说了不能做就得给下一步」）。
    """
    if not base.ALLOW_LIVE:
        return {'error': '服务以只读模式启动 —— 去掉 --readonly 再起一次'}
    fe = _eval()
    fm = _manual()
    b = body or {}
    act = (b.get('act') or 'add').strip()
    try:
        if act == 'remove':
            fm.remove(b.get('name') or '')
        elif act == 'add':
            reg = _reg_miss(fe)
            cat = _cat(fe)
            fm.add(b.get('name') or '', (b.get('reason') or '').strip(),
                   b.get('detail') or '',
                   known=list(reg['name_cn']), implemented=list(cat['name_cn']))
        else:
            return {'error': 'act 只能是 add / remove，收到 %r' % act}
    except fm.MissError as e:
        return {'error': str(e)}
    return dict(api_factors_missing({}), ok=True)


def _f(v):
    import math
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) or math.isinf(v) else v
