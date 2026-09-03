"""股息率买点清单 + 价格提醒。**手工维护的那张表**。

## 这是什么

复刻用户手里那张 Excel：一只票一行，写一个「预计分红（每股）」，再写几档
想买的价格；每档旁边是那个价对应的股息率。价格跌到某一档就提醒。

    000423 东阿阿胶   2.7   2.7/49=5.5% 49买   2.7/45=6% 45买   2.7/43=6.2% 43买
    600900 长江电力   1     1/27=3.7%   27买   1/26=3.85% 26买  1/25=4%   25买

所以它不是"策略"（没有回测、没有引擎、不产生委托），是**手工的挂单计划表**。
刻意与实盘模块分开：实盘那边一条规则都不许重写，而这里根本没有规则 ——
就是"我自己定的价，到了叫我"。

## 两种输入方式，存的是【你填的那个】

每一档可以填**目标价**，也可以填**目标股息率**，另一个由分红换算：

    填价 49  -> 股息率 = 2.7/49 = 5.51%
    填率 6%  -> 目标价 = 2.7/0.06 = 45.00

🔴 存 `by`（填的是哪个）+ `v`（填的那个数），另一个**永远由分红现算** ——
两个都存下来的话，改了分红之后另一个就是过期的，而它看着仍然像个正常数字。

## 分红只是【你的预计】，但给一个有依据的预填

`suggest_div()` 给近 12 个月**已公告**的每股分红合计（口径与红利策略的
`DIV_*` CTE 同源：按 `board_plan_pub_date` 可见、同一
`(code, report_date, bonus_type)` 去重留流程最靠后那条）。
实测与用户手填的一致：长江电力 0.79+0.21 = 1.00（他写 1）、
东阿阿胶 1.4355+1.3448 = 2.78（他写 2.7）。
🔴 但它**只是预填**，不覆盖你填的值 —— "预计分红"是判断，不是数据。
换算出来的股息率也要跟着标出这个分红是**谁填的**（`div_src`）。

## 存哪 / 怎么存

`live/alerts.jsonl`，**append-only**，与自选/成交流水同一套纪律：
改一行是追加一条 `set`（整行覆盖语义），删是追加一条 `remove`，
当前清单由**重放**得出（`current()`）。
`live/alerts_fired.jsonl` 记每次触发 —— 它同时是**去重依据**：
同一 (code, 档, 类型) 一天只提醒一次，否则每分钟一条通知没人受得了。
"""
import datetime
import json
import os
import uuid

LIVE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    'live')
FILE = 'alerts.jsonl'
FIRED = 'alerts_fired.jsonl'
ACTS = ('set', 'remove')
# 「接近」的默认阈值：现价在目标价上方 3% 以内就算到门口了。
# 每行可以自己带 near 覆盖它。
NEAR = 0.03
MAX_TIERS = 6


class AlertError(Exception):
    pass


def _path(name=FILE):
    return os.path.join(LIVE, name)


def _read(name=FILE):
    p = _path(name)
    if not os.path.isfile(p):
        return []
    out = []
    with open(p, encoding='utf-8') as fh:
        for ln in fh:
            ln = ln.strip()
            if ln:
                try:
                    out.append(json.loads(ln))
                except Exception:                           # noqa: BLE001
                    pass          # 坏行跳过，不让一行坏 JSON 废掉整张表
    return out


def _append(rec, name=FILE):
    os.makedirs(LIVE, exist_ok=True)
    with open(_path(name), 'a', encoding='utf-8') as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + '\n')
    return rec


def log():
    return _read()


# ------------------------------------------------------------------ 当前清单
def current():
    """重放出当前清单。`set` 是**整行覆盖**，`remove` 删掉。

    ★ 顺序按第一次加入的时间 —— 表格的行序是用户自己排的，
      改一次价格就把行跳到最后，会让人每次都找不到刚改的那行。
    """
    rows, order = {}, []
    for r in _read():
        c = r.get('code')
        if not c:
            continue
        if r.get('act') == 'remove':
            rows.pop(c, None)
            continue
        if r.get('act') != 'set':
            continue
        if c not in rows:
            order.append(c)
        rows[c] = {'code': c, 'div': r.get('div'), 'tiers': r.get('tiers') or [],
                   'note': r.get('note') or '', 'near': r.get('near'),
                   'div_src': r.get('div_src') or '手填',
                   'ts': r.get('ts'), 'added': rows.get(c, {}).get('added') or r.get('ts')}
    return [rows[c] for c in order if c in rows]


def _tier(t):
    """一档：{by:'price'|'yield', v:数}。填价存价、填率存率，另一个现算。"""
    by = (t.get('by') or '').strip()
    if by not in ('price', 'yield'):
        raise AlertError("每档的 by 只能是 price / yield，收到 %r" % by)
    try:
        v = float(t.get('v'))
    except Exception:                                       # noqa: BLE001
        raise AlertError('第 %s 档填的不是数字：%r' % (t.get('i', '?'), t.get('v')))
    if not (v > 0):
        raise AlertError('%s 要大于 0，收到 %s' % ('目标价' if by == 'price'
                                                  else '目标股息率', v))
    if by == 'yield' and v >= 1:
        # 6 与 0.06 都当 6% —— 表格里人写的是 6%
        v = v / 100.0
    if by == 'yield' and v > 0.5:
        raise AlertError('目标股息率 %.1f%% 不像是真的' % (v * 100))
    out = {'by': by, 'v': round(v, 6)}
    if t.get('note'):
        out['note'] = str(t['note'])[:40]
    return out


def set_row(code, div, tiers, note='', near=None, div_src='手填'):
    """加一行 / 改一行（整行覆盖语义，追加一条 `set`）。"""
    from assay import stock as st
    jc = st.norm_code(code)
    if not jc:
        raise AlertError('认不出代码：%r' % code)
    try:
        div = float(div)
    except Exception:                                       # noqa: BLE001
        raise AlertError('预计分红填的不是数字：%r' % div)
    if not (div > 0):
        raise AlertError('预计分红要大于 0（每股多少元），收到 %s' % div)
    ts = [_tier(dict(t, i=i + 1)) for i, t in enumerate(tiers or [])]
    if not ts:
        raise AlertError('至少要填一档目标价或目标股息率 —— '
                         '一行没有任何一档的话，它不会提醒任何东西')
    if len(ts) > MAX_TIERS:
        raise AlertError('最多 %d 档' % MAX_TIERS)
    if near is not None:
        near = float(near)
        if near / (100.0 if near >= 1 else 1.0) > 0.5:
            raise AlertError('「接近」阈值 %s 太大了' % near)
        near = near / 100.0 if near >= 1 else near
    return _append({'uid': uuid.uuid4().hex[:12],
                    'ts': datetime.datetime.now().replace(
                        microsecond=0).isoformat(),
                    'act': 'set', 'code': jc, 'div': round(div, 6),
                    'div_src': div_src, 'tiers': ts,
                    'note': str(note or '')[:200], 'near': near})


def remove(code):
    from assay import stock as st
    jc = st.norm_code(code)
    if not jc:
        raise AlertError('认不出代码：%r' % code)
    if not any(x['code'] == jc for x in current()):
        raise AlertError('%s 不在清单里' % jc)
    return _append({'uid': uuid.uuid4().hex[:12],
                    'ts': datetime.datetime.now().replace(
                        microsecond=0).isoformat(),
                    'act': 'remove', 'code': jc})


def codes():
    return [x['code'] for x in current()]


# --------------------------------------------------------------- 分红预填
# 口径与红利策略的 DIV_FISCAL_YEAR 同源：按【董事会预案公告日】可见
# （预案就是公开信息，不是未来函数），同一 (code, report_date, bonus_type)
# 只留流程最靠后的那条 —— JQ 的「董事会预案」记录数是实际事件数的 8.3 倍，
# 不去重会让股息率虚高。
#
# 🔴 `bonus_ratio_rmb` 是**每 10 股派现（元）**，不是每股 —— 要除 10。
#   实测：长江电力 7.9 = 每股 0.79；东阿阿胶 13.4481 = 每股 1.3448。
#   当成每股用会让"预计分红"大 10 倍，而它不报错，只是目标价高得离谱。
_SQL_DIV = """
WITH raw AS (
  SELECT code, report_date, bonus_type, bonus_ratio_rmb, board_plan_pub_date,
         row_number() OVER (
           PARTITION BY code, report_date, bonus_type
           ORDER BY CASE plan_progress
                      WHEN '实施方案'     THEN 3
                      WHEN '股东大会预案' THEN 2
                      WHEN '董事会预案'   THEN 1
                      ELSE 0 END DESC, board_plan_pub_date DESC) AS r
  FROM read_parquet('%s/std/dividend.parquet')
  WHERE code IN ('%s')
    AND board_plan_pub_date <= DATE '%s'
    AND board_plan_pub_date >= DATE '%s' - INTERVAL 365 DAY
    AND bonus_cancel_pub_date IS NULL
    AND (plan_progress IS NULL OR plan_progress NOT IN ('终止', '取消分红'))
    AND bonus_ratio_rmb > 0
)
SELECT code, sum(bonus_ratio_rmb) / 10.0 AS per_share, count(*) AS n,
       max(board_plan_pub_date) AS last_pub
FROM raw WHERE r = 1 GROUP BY 1
"""


def suggest_div(codes_in, root=None, day=None):
    """近 12 个月**已公告**的每股分红合计 —— 只是预填，不是"预计"。"""
    from assay import stock as st
    want = [c for c in (st.norm_code(x) for x in (codes_in or [])) if c]
    if not want:
        return {}
    d = (day or datetime.date.today()).isoformat() if not isinstance(
        day, str) else day
    con = st.con()
    lake = st._lake(root)
    rows = con.execute(_SQL_DIV % (lake, "','".join(want), d, d)).fetchall()
    return {r[0]: {'per_share': round(r[1], 4), 'n': r[2],
                   'last_pub': str(r[3])[:10],
                   'src': '近 12 个月已公告合计'} for r in rows}


# ------------------------------------------------------------------- 估值
def _derive(row, div):
    """把每一档展开成 {目标价, 目标股息率}。另一个**现算**，不存。"""
    out = []
    for t in row.get('tiers') or []:
        if t['by'] == 'price':
            price, yld = t['v'], (div / t['v'] if t['v'] else None)
        else:
            yld, price = t['v'], (div / t['v'] if t['v'] else None)
        out.append({'by': t['by'], 'price': (round(price, 3) if price else None),
                    'yield': (round(yld, 6) if yld else None),
                    'note': t.get('note') or ''})
    # 目标价【从高到低】：价高的那档先触发，与表格里的排法一致
    out.sort(key=lambda x: -(x['price'] or 0))
    return out


def valued(root=None):
    """清单 + 现价 + 每档的状态。页面主视图用的就是它。

    ★ 现价读 `datalake/rt/` 那个**共享库**（与持仓页、自选页同一份），
      不自己去打接口 —— 抓取由 server 统一做，范围里已经包含这张清单。
    ★ 盘中的涨跌幅按实时价重算，"昨收"取快照自带的 preclose，
      退一步用面板最新那天的收盘（同自选：面板那行的 preclose 差一天）。
    """
    from assay import stock as st
    rows = current()
    out = {'rows': [], 'date': None, 'rt_at': None, 'rt_n': 0,
           'n_hit': 0, 'n_near': 0, 'near_default': NEAR}
    if not rows:
        return out
    con = st.con()
    p = st.panel(root)
    d = con.execute('SELECT max(date) FROM %s' % p).fetchone()[0]
    out['date'] = str(d)[:10] if d else None
    q = "','".join(x['code'] for x in rows)
    px = {r[0]: r for r in con.execute("""
        SELECT jq_code, sec_name, close_bfq, preclose, pe_ttm, pb, sw_l1_name,
               is_st, public_status
        FROM %s WHERE date = DATE '%s' AND jq_code IN ('%s')"""
        % (p, d, q)).fetchall()}
    rt = {}
    try:
        from assay import realtime as _rt
        rt = _rt.latest([x['code'] for x in rows], root=root)
    except Exception:                                       # noqa: BLE001
        rt = {}
    at = None
    for x in rows:
        it = dict(x)
        r = px.get(x['code'])
        if r:
            it.update({'name': r[1], 'price': r[2], 'preclose': r[3],
                       'pe_ttm': r[4], 'pb': r[5], 'industry': r[6],
                       'is_st': bool(r[7]), 'status': r[8]})
        else:
            # 面板里查不到 -> 代码填错 / 退市 / 未上市。**不静默丢掉**，
            # 那一行留着并标出来 —— 用户那张表里就有写错的代码。
            it.update({'name': '', 'price': None, 'missing': True})
        q2 = rt.get(x['code'])
        if q2 and q2.get('price'):
            pc = q2.get('preclose') or (r[2] if r else None)
            it['price'] = q2['price']
            it['preclose'] = pc
            it['rt_src'] = q2.get('src')
            it['rt_at'] = q2.get('at')
            out['rt_n'] += 1
            if q2.get('at') and (at is None or q2['at'] > at):
                at = q2['at']
        pr, pc = it.get('price'), it.get('preclose')
        it['change_pct'] = (round((pr / pc - 1) * 100, 2)
                            if pr and pc else None)
        div = it.get('div') or 0
        it['yield_now'] = (round(div / pr, 6) if pr and div else None)
        near = it.get('near') if it.get('near') is not None else NEAR
        it['near_used'] = near
        tiers = _derive(it, div)
        hit_i = near_i = None
        for i, t in enumerate(tiers):
            tp = t['price']
            if not (tp and pr):
                t['state'], t['gap'] = 'na', None
                continue
            # gap = 现价还要跌多少才到这一档（负数 = 已经跌破了）
            t['gap'] = round(tp / pr - 1, 6)
            if pr <= tp:
                t['state'] = 'hit'
                hit_i = i               # 继续往下找：取跌破的【最深】那档
            elif pr <= tp * (1 + near):
                t['state'] = 'near'
                if near_i is None:
                    near_i = i
            else:
                t['state'] = 'far'
        it['tiers'] = tiers
        it['hit'] = hit_i
        it['near'] = near_i if hit_i is None else None
        it['state'] = ('hit' if hit_i is not None
                       else ('near' if near_i is not None else 'far'))
        # 下一档（还没触发的里最靠近的那个）—— 页面上"还差多少"要有个主角
        nxt = [t for t in tiers if t['state'] in ('near', 'far')]
        it['next'] = nxt[0] if nxt else None
        out['n_hit'] += 1 if hit_i is not None else 0
        out['n_near'] += 1 if (hit_i is None and near_i is not None) else 0
        out['rows'].append(it)
    out['rt_at'] = at
    out['price_src'] = ('实时' if out['rt_n'] == len(rows) and out['rt_n']
                        else ('部分实时' if out['rt_n'] else '收盘'))
    return out


# ------------------------------------------------------------------- 触发
def fired_log():
    return _read(FIRED)


def check_fire(v=None, root=None, day=None):
    """哪些是【刚】触发的 —— 用来发通知。

    🔴 同一 (code, 档, 类型) **一天只发一次**：轮询是每分钟一轮，
      不去重的话价格在阈值上下抖一抖就是几十条通知，而通知一多就没人看了
      （同"假告警看多了就不看告警"那条）。
    ★ 去重依据落盘（`alerts_fired.jsonl`）而不是只放内存 —— 重启后不该
      把今天已经提醒过的又提醒一遍。它同时是"今天都提醒过什么"的历史。
    """
    v = v or valued(root=root)
    today = (day or datetime.date.today()).isoformat()
    seen = {(r.get('code'), r.get('tier'), r.get('kind'))
            for r in fired_log() if str(r.get('ts', ''))[:10] == today}
    new = []
    for x in v['rows']:
        for i, t in enumerate(x['tiers']):
            if t['state'] not in ('hit', 'near'):
                continue
            key = (x['code'], i, t['state'])
            if key in seen:
                continue
            rec = {'ts': datetime.datetime.now().replace(
                       microsecond=0).isoformat(),
                   'code': x['code'], 'name': x.get('name') or '',
                   'tier': i, 'kind': t['state'], 'target': t['price'],
                   'yield': t['yield'], 'price': x.get('price'),
                   'src': x.get('rt_src') or 'close'}
            _append(rec, FIRED)
            seen.add(key)
            new.append(rec)
    return new


def notify_text(rec):
    """一条通知的正文。**要把判据写进去** —— "东阿阿胶到了"没法据此下单。"""
    kind = '跌破' if rec['kind'] == 'hit' else '接近'
    return '%s %s：现价 %s，%s目标 %s（股息率 %.2f%%）' % (
        (rec.get('name') or rec['code']), kind,
        rec.get('price'), '' if rec['kind'] == 'hit' else '',
        rec.get('target'), (rec.get('yield') or 0) * 100)
