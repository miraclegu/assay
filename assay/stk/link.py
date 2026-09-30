# -*- coding: utf-8 -*-
"""与实盘 / 回测的联动：我持有多少、哪几次回测买过它。"""

import datetime
import io
import json
import os
import re
import duckdb
from assay import symbols as _SYM          # 「代码->类别/名称」的唯一正本
from assay import paths as _paths   # datalake 根的唯一解析

from .base import StockError, _na, alt_kind, con, norm_code


# ========================= 与实盘 / 回测联动 =========================
def links(code, root=None, kind=None):
    """这只票和我的实盘、回测有什么关系。

    ★ 数据全是现成的，只是原来没连起来：看个股时最想知道的两件事就是
      "我持有它吗、成本多少" 和 "我哪次回测选过它、当时什么参数"。
    ★ 任何一半取不到都不该拖垮整页 —— 各自 try，缺的那半标 error。
    """
    # 🔴 **只有指数**标不适用：ETF 是**能买的**（项目里就有 ETF 轮动策略），
    #   实盘持有过、回测选过它都讲得通，这一块对它有意义。
    #   指数买不了，查"我持有多少上证指数"本身就没有意义。
    alt = alt_kind(code, root, kind)
    if alt and alt[0] == 'index':
        return _na(alt, {'positions': [], 'runs': [], 'watch': None})
    jc = norm_code(code)
    if not jc:
        raise StockError('认不出代码：%r' % code)
    out = {'code': jc, 'positions': [], 'runs': [], 'watch': None}

    # ---- 实盘持仓（逐账户）----
    try:
        from assay import live as lv
        for a in lv.load_accounts():
            if a.get('archived'):
                continue
            P = lv.positions_valued(a['id'])
            for it in P.get('items') or []:
                if it['code'] == jc:
                    out['positions'].append({
                        'account': a['id'], 'account_name': a.get('name') or a['id'],
                        'shares': it['shares'], 'cost': it['cost'],
                        'cost_net': it.get('cost_net'), 'price': it['price'],
                        'value': it['value'], 'pnl': it['pnl'],
                        'pnl_pct': it['pnl_pct'], 'weight': it['weight'],
                        'entry': it['entry'],
                        'breakeven': it.get('breakeven'),
                        'exit_fee_est': it.get('exit_fee_est')})
    except Exception as e:                                  # noqa: BLE001
        out['positions_error'] = '%s: %s' % (type(e).__name__, e)

    # ---- 自选 ----
    try:
        from assay import watchlist as wl
        hit = [x for x in wl.current() if x['code'] == jc]
        out['watch'] = hit[0] if hit else None
    except Exception as e:                                  # noqa: BLE001
        out['watch_error'] = '%s: %s' % (type(e).__name__, e)

    # ---- 回测里买过它的那些 run ----
    #   ★ 只看归档里的 trades —— 不重跑回测。重跑要几十秒，而这是页面上
    #     顺手看一眼的东西。
    try:
        out['runs'] = _runs_with(jc)
    except Exception as e:                                  # noqa: BLE001
        out['runs_error'] = '%s: %s' % (type(e).__name__, e)
    return out

def _runs_with(jc, limit=30):
    """哪些归档回测买过这只票。

    ★ 归档是**嵌套**目录 `runs/<组>/<策略>/<run_id>/`，不是平铺 ——
      用 `os.walk` 找 `meta.json` 定位，与 `server._scan()` 同一套判据。
      按平铺 `listdir` 找会一条都找不到，而那看起来像"这只票没被任何回测
      选过"（我第一版就是这么错的）。
    ★ 只读归档里的 trades，**不重跑回测** —— 重跑要几十秒，
      而这是页面上顺手看一眼的东西。
    """
    # 🔴 归档目录要走 `registry.RUNS`，不能自己拼 `<repo>/runs` ——
    #   serve.py 的 `--runs` / `ASSAY_RUNS` 会改它（`registry.set_runs()`），
    #   自己拼的话指定了别的归档盘时这里**静默返回空**，
    #   页面上看着就是"这只票没被任何回测选过"。
    #   ★ 必须 `registry.RUNS` **属性访问**：set_runs() 是重新赋值，
    #     `from .registry import RUNS` 拿到的是副本。
    # 🔴 **绝对导入** —— 搬进 `assay/stk/` 之后 `.` 是 `assay.stk` 而不是
    #   `assay`，写相对的话这里拿到的是子包（同拆 `srv/` 时那个坑）。
    #   而它**不报错**：外面那层 try/except 把它记成 `runs_error`、
    #   `runs` 返回空 —— 页面上就成了「这个版本从没回测过」。
    from assay import registry
    runs = registry.RUNS
    if not os.path.isdir(runs):
        # 不静默返回 [] —— 那和"真的没有回测选过它"长得一模一样。
        # 外层 links() 会把它转成 runs_error 给页面。
        raise RuntimeError('归档目录不存在：%s'
                           '（用 --runs 或 ASSAY_RUNS 指定）' % runs)
    dirs = []
    for dp, _dns, fns in os.walk(runs):
        if 'meta.json' in fns and 'trades.parquet' in fns:
            dirs.append(dp)
    dirs.sort(key=lambda x: os.path.basename(x), reverse=True)
    c = con()
    out = []
    for d in dirs:
        tp = os.path.join(d, 'trades.parquet')
        mp = os.path.join(d, 'meta.json')
        try:
            r = c.execute("""
                SELECT count(*), min(entry_date), max(exit_date),
                       avg(ret), sum(pnl)
                FROM read_parquet('%s') WHERE code = ?""" % tp, [jc]).fetchone()
        except Exception:                                   # noqa: BLE001
            continue
        if not r or not r[0]:
            continue
        try:
            meta = json.load(open(mp, encoding='utf-8'))
        except Exception:                                   # noqa: BLE001
            meta = {}
        out.append({'run_id': os.path.basename(d),
                    'strategy': meta.get('strategy') or '',
                    'group': meta.get('group') or '',
                    'params': meta.get('params') or {},
                    'start': meta.get('start'), 'end': meta.get('end'),
                    'n_trades': r[0], 'first': str(r[1])[:10],
                    'last': str(r[2])[:10],
                    'avg_ret': r[3], 'pnl': r[4]})
        if len(out) >= limit:
            break
    return out
