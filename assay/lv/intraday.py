#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""lv/intraday.py —— 盘中提示：炸板离场。

为什么要单独做这件事
--------------------
`check_limit_up`（炸板离场）在引擎里是 **14:00 的日频任务**，而实盘的信号是
**前一晚**算的：它问的是"昨天涨停、**昨天那一天**没封住"。所以

    · 信号里确实有 `limit_up_exit`（页面显示「炸板离场」）——
      但那是"昨天开的板"，今天开盘就该卖。
    · **今天盘中**开的板，信号里一个字都没有 —— 要等今晚重算才出现，
      而那时已经是"明天开盘卖"，白丢一天。

以前这件事做不了（本地只有日线、收盘后更新）。现在有分钟级实时快照
（`datalake/rt/`，腾讯 1 次请求覆盖全部持仓），所以**可以**在盘中判：

    判据 = 「前一交易日收盘涨停」的持仓，现价 **< 涨停价**

🔴 两个都必须来自服务端，不能让前端自己判：
  · "哪些持仓昨天涨停" 要读面板的 `limit_up`（前端没有面板）
  · "现在是不是盘中" 见 `srv/live.py` 的 `_rt_live()`
  （同「权不权威这类判据由服务端给」那条：前端硬编码过一次，弹了一整页假告警）

★ 判据用**涨停价**而不是"涨幅 < 9.9%"：涨跌幅限制有 10% / 20%（创业板、
  科创板）/ 5%（ST）三档，还会因为 ST 状态变化而变 —— 用固定阈值必然错。
  面板的 `high_limit` 是权威值。
"""
import datetime
import os

from . import base as _base
from . import pos as _pos
from . import px as _px

# 🔴 从 10:00 起扫 —— 09:30~10:00 那半小时开板/回封频繁，
#   早于 10:00 报出来的多半会自己回去，而**假告警看多了就不看告警了**。
SCAN_FROM = datetime.time(10, 0)
#   同一只票一天只报一次（与买点清单的 check_fire 同一条纪律）。
FIRED = os.path.join('_intraday_fired.jsonl')


def _fired_path(aid):
    return os.path.join(_base.acct_dir(aid), '_intraday_fired.jsonl')


def limit_up_holdings(aid, datalake=None):
    """持仓中【前一交易日收盘涨停】的票 -> {code: (涨停价, 名称)}。

    ★ 与引擎的 `prepare` 同一个判据（`bars(previous_date).limit_up`）——
      那边填的是 `g.high_limit`，炸板离场和"卖出豁免"都读它。
      两处不一致的表现是"页面提示的票与策略要卖的票不是同一批"。
    """
    #   ★ `positions(aid)` 取的是**当前持仓**（由 fills 重放得出，
    #     已冲正的成对记录已剔掉）—— 不要自己去拼 fifo_lots(rows)，
    #     那是同一件事的第二份实现。
    codes = list(_pos.positions(aid))
    if not codes:
        return {}
    root = _px._lake(datalake)
    import duckdb
    con = duckdb.connect(':memory:')
    try:
        P = "read_parquet('%s/mart/panel_daily/**/*.parquet')" % root
        last = con.execute("SELECT max(date) FROM %s" % P).fetchone()[0]
        #   ★ 面板里 `limit_up` 是**涨停价**、`is_limit_up` 是**布尔**
        #     （还有 `limit_pct` 是那一天的涨跌幅限制 0.10/0.20/0.05）——
        #     直接用面板算好的这两个，不要自己按"前收 ×1.1"推：
        #     限制有三档，而且会因 ST 状态变化而变，自己推必然错。
        rows = con.execute("""
            SELECT jq_code, limit_up, limit_pct
            FROM %s WHERE date = DATE '%s' AND jq_code IN (%s)
              AND is_limit_up AND limit_up IS NOT NULL
        """ % (P, last, ','.join("'%s'" % c for c in codes))).fetchall()
    finally:
        con.close()
    names = _px.names_of([r[0] for r in rows]) if rows else {}
    return {r[0]: (float(r[1]), names.get(r[0], ''),
                   (float(r[2]) if r[2] is not None else None)) for r in rows}


def scan(aid, now=None, datalake=None):
    """扫一遍 -> {'at','session','items':[…],'skipped':…}。

    每个 item：{code,name,limit,px,at,pct,broken}
    broken=True 表示**已经开板**（现价 < 涨停价）。
    ★ 没开板的也返回（broken=False）—— 页面上可以显示"还封着"，
      而"一条都没有"分不出"没有涨停持仓"和"链条没工作"。
    """
    now = now or datetime.datetime.now()
    out = {'at': now.replace(microsecond=0).isoformat(), 'items': [],
           'session': None, 'skipped': None}
    try:
        rt = _base._rt() if hasattr(_base, '_rt') else None
    except Exception:                                       # noqa: BLE001
        rt = None
    if rt is None:
        import importlib
        rt = importlib.import_module('assay.realtime')
    out['session'] = bool(rt.in_session(now) and rt.is_trading_day())
    if not out['session']:
        out['skipped'] = '不在交易时段'
        return out
    if now.time() < SCAN_FROM:
        out['skipped'] = '还没到 %s —— 开盘半小时内开板/回封频繁，' \
                         '早报多半是假告警' % SCAN_FROM.strftime('%H:%M')
        return out
    hl = limit_up_holdings(aid, datalake)
    if not hl:
        out['skipped'] = '持仓里没有昨日涨停的票'
        return out
    live = rt.latest(list(hl), root=datalake) or {}
    for code, (lim, nm, lpct) in sorted(hl.items()):
        q = live.get(code) or {}
        p = q.get('price')
        if p is None:
            continue                    # 取不到实时价：不猜，也不报
        out['items'].append({
            'code': code, 'name': nm, 'limit': round(lim, 2),
            'px': round(float(p), 2), 'at': q.get('at'),
            'pct': round(float(p) / lim - 1.0, 6),
            'limit_pct': lpct,          # 那一天的涨跌幅限制（10%/20%/5%）
            # 🔴 判据是**涨停价**，不是"涨幅 < 9.9%" —— 涨跌幅限制有
            #   10% / 20% / 5% 三档，用固定阈值必然错。
            'broken': float(p) < lim - 0.001,
        })
    return out


def fire(aid, items, day=None):
    """把"今天已经报过"的记下来 -> 返回**本次新增**的那些。

    🔴 去重落盘而不是只放内存：重启一次就把今天报过的又报一遍，
      而**通知一多就没人看了**（同买点清单的 check_fire）。
      它同时是"今天报过什么"的历史。
    """
    day = day or datetime.date.today().isoformat()
    p = _fired_path(aid)
    seen = set()
    for r in _base._read_jsonl(p):
        if r.get('day') == day:
            seen.add(r.get('code'))
    fresh = [x for x in items if x.get('broken') and x['code'] not in seen]
    for x in fresh:
        _base._append_jsonl(p, {'day': day, 'code': x['code'],
                                'name': x.get('name'), 'px': x.get('px'),
                                'limit': x.get('limit'),
                                'ts': _base._now()})
    return fresh
