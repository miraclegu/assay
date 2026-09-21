"""股息率买点清单 + 价格提醒。**手工维护的那张表**。

## 这是什么

复刻用户手里那张 Excel：一只票一行，一个「实际分红（每股）」，再写几档
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

## 🔴 分红【不存、不手填】—— 每次读时从数据解析

`live/alerts.jsonl` 里**没有 div 这个字段**。分红在 `valued()` 里现算：
本地 `std/dividend.parquet`（聚宽）+ 东财 `RPT_SHAREBONUS_DET` 合并后取
**最近一个完整会计年度的合计**。

为什么不存、也不让手填：

- **"预计分红"没有价值**。目标价要么按已公告的实际分红算，要么就是在猜；
  猜出来的目标价看着和真的一样，而它错在一个你不会再回头检查的地方。
- **存下来就会过期**。分红是每天可能变的（新公告），存一份的话
  "股息率 6% 对应的目标价"会停在录入那天 —— 与"每档只存你填的那个、
  另一个现算"完全同一个理由。
- 所以新公告一到，这张表**所有档位的目标价自动跟着变**，不用去改任何一行。

★ 拿不到分红的票（从不分红 / 数据还没到）：`by='yield'` 那种档**算不出
目标价**，状态给 `na` 并在页面上说明白；`by='price'` 的档照常工作
（只是那一格没有股息率）。**不猜一个数填上去。**

`suggest_div()` 给**两个**口径（都与红利策略的 `DIV_*` CTE 同源：按
`board_plan_pub_date` 可见、同一 `(code, report_date, bonus_type)` 去重
留流程最靠后那条）：

| 口径 | 是什么 | 用户手填的 8 只对得上吗 |
|---|---|---|
| `fy`（**默认**） | **最近一个完整会计年度**的分红合计（年度 + 中期 + 季度，按 `report_date` 的年份归拢） | **8/8 吻合** |
| `r365` | 近 365 天**已公告**的合计（按 `board_plan_pub_date`） | 6/8 |

🔴 **默认必须是 `fy`。** `r365` 的窗口会**混入不同归属期**：公司从年派改半年派
（2024 年后大量银行/央企）时，窗口里可能出现两次或零次年度分红。实测对账：

```
            用户手填   FY2025 合计            近 365 天已公告
海尔智家     1.15      1.1607 (0.2692+0.8915)  0.8915   ← 少了中期，差 26%
国电电力     0.24      0.241  (0.100 +0.141)   0.141    ← 少了中期，差 41%
东阿阿胶     2.7       2.7056 (1.2701+1.4355)  2.7803   ← 混进了 FY2026 中期
招商银行     2.016     2.016  (1.013 +1.003)   2.016
```

★ **两个都给页面**，让人挑 —— 半年派的公司刚公告完中期时，"最近完整年度"
是保守的、"近 12 个月"是激进的，哪个算"预计"是判断。
★ 默认口径是 `fy`。`r365` 仍然算出来给页面**当参考显示**（半年派的票刚
公告完中期时两者差得多），但**不参与目标价** —— 一个口径就够，
两个口径都能当基准的话，"这一档的目标价是按哪个算的"就成了要记的事。

## 分红也能从接口拿【最新的】—— 但要拿对那一个

| 接口 | 给什么 | 能不能用 |
|---|---|---|
| 东财 `push2/ulist.np` `f133` | 一个现成的"股息率" | 🔴 **不能当口径**。实测 8 只里 7 只 = `r365/现价`，**招商银行 2.44% vs 应为 4.91%（差一倍）** —— 它只算了年度那一次、漏掉中期。而漏中期恰恰发生在银行/央企（红利策略重仓处）。"有值但对不上"比"没有值"更危险 |
| 东财 `datacenter-web` `RPT_SHAREBONUS_DET` | **分红方案明细**：`REPORT_DATE`(归属报告期) / `PRETAX_BONUS_RMB`(每 10 股税前派现) / `PLAN_NOTICE_DATE` / `ASSIGN_PROGRESS` | ✅ **就用它**。字段与本地 `dividend` 一一对应，**口径仍是我们自己算的** |

★ **关键是取【原始方案明细】而不是取现成的股息率** —— 别人的"股息率"里
藏着别人的窗口与去重规则（f133 就是这样错的）。拿回 (报告期, 每股派现,
公告日, 进度) 四个字段，套本地那套 `fy` / `r365` 聚合，口径就还是自己的。
★ 它带的 `REPORT_DATE` 正是 **QMT 拿不到、导致红利指数增强不能原生移植**
的那个字段（见数据字典索引 1「分红的归属报告期」）。
实测 2026-09-03 逐只对数：8 只的 FY 合计与本地 **8/8 精确吻合到 0.0001**。

🔴 **一天只调一次,而且已经有今天的就不调**（`ext_div`）：
按**每只票**记 `fetched` 日期,当天抓过就跳过;没抓过的那几只**合并成一次
批量请求**（`(SECURITY_CODE in (...))`，8 只 1 个请求）。
新加进清单的票立刻抓一次(它一条都没有),同 `realtime._rt_ensure` 的思路。
★ 缓存落 `datalake/rt/div_ext.json` —— 外部抓来的东西**不写进
`std/dividend.parquet`**：那张表是 loader 链的产物,被外部数据污染之后
"面板与 std 不一致"不报错。`rt/` 是既有的"外部抓来的、构建链一个脚本都不碰"
那个目录。
★ 合并按 `(code, 报告期)` 去重,**取进度更靠后的那条**（实施 > 股东大会 >
董事会 > 预披露）;并把"外部比本地新的那几条"标出来给页面
（`ext_new`）—— 数字从哪来必须看得见。

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
        # ★ 老记录里可能还有 div/div_src（手填时代留下的）—— **刻意不读**：
        #   分红一律现算，读了就等于让一个过期的数悄悄活下来。
        rows[c] = {'code': c, 'tiers': r.get('tiers') or [],
                   'note': r.get('note') or '', 'near': r.get('near'),
                   'ts': r.get('ts'),
                   'added': rows.get(c, {}).get('added') or r.get('ts')}
    return [rows[c] for c in order if c in rows]


def _tier(t):
    """一档：填价 / 填股息率 / **填一个指标条件**，另一个永远现算。

    `by='ind'` 那种长这样：`{by:'ind', sig:'ma_dist', args:{'w':20}, v:-3}`
    —— 条件定义（有哪些、各带什么参数）在 `assay/indicators.py`，
    这里只管存**你填的那个**（同「每档存的是你填的那个，另一个现算」）：
    触发价每天都不一样（均线在动），存下来第二天就是错的。
    """
    by = (t.get('by') or '').strip()
    if by == 'ind':
        from assay import indicators as I
        sid = (t.get('sig') or '').strip()
        try:
            args = I.sig_args(sid, t.get('args'))
        except I.IndError as e:
            raise AlertError(str(e))
        try:
            v = float(t.get('v'))
        except Exception:                                   # noqa: BLE001
            raise AlertError('第 %s 档的阈值不是数字：%r' % (t.get('i', '?'), t.get('v')))
        if not (abs(v) < 1e4):
            raise AlertError('阈值 %s 不像是真的' % v)
        out = {'by': 'ind', 'sig': sid, 'args': args, 'v': round(v, 6)}
        if t.get('note'):
            out['note'] = str(t['note'])[:40]
        return out
    if by not in ('price', 'yield'):
        raise AlertError("每档的 by 只能是 price / yield / ind，收到 %r" % by)
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


def set_row(code, tiers, note='', near=None):
    """加一行 / 改一行（整行覆盖语义，追加一条 `set`）。

    🔴 **没有 div 参数** —— 分红不存也不手填，`valued()` 每次从数据解析
      （见模块 docstring）。存一份的话它会过期，而过期的目标价看着完全正常。
    """
    from assay import stock as st
    jc = st.norm_code(code)
    if not jc:
        raise AlertError('认不出代码：%r' % code)
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
                    'act': 'set', 'code': jc, 'tiers': ts,
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


# ------------------------------------------------------------- 分红（实际）
# 口径与红利策略的 DIV_FISCAL_YEAR 同源：按【董事会预案公告日】可见
# （预案就是公开信息，不是未来函数），同一 (code, report_date, bonus_type)
# 只留流程最靠后的那条 —— JQ 的「董事会预案」记录数是实际事件数的 8.3 倍，
# 不去重会让股息率虚高。
#
# 🔴 `bonus_ratio_rmb` 是**每 10 股派现（元）**，不是每股 —— 要除 10。
#   实测：长江电力 7.9 = 每股 0.79；东阿阿胶 13.4481 = 每股 1.3448。
#   当成每股用会让分红大 10 倍，而它不报错，只是目标价高得离谱。
# ------------------------------------------------------- 分红：本地 + 外部
# 本地那份的口径与红利策略的 DIV_* CTE 同源：按【董事会预案公告日】可见
# （预案就是公开信息，不是未来函数），同一 (code, report_date) 只留流程最
# 靠后的那条 —— JQ 的「董事会预案」记录数是实际事件数的 8.3 倍，不去重会让
# 股息率虚高。
#
# 🔴 `bonus_ratio_rmb` / `PRETAX_BONUS_RMB` 都是**每 10 股派现（元）**，
#   不是每股 —— 要除 10。实测：长江电力 7.9 = 每股 0.79。当成每股用会让
#   分红大 10 倍、目标价小 10 倍，而它不报错，只是那一行永远不会触发。
_SQL_RAW = """
SELECT code, report_date, bonus_type, bonus_ratio_rmb / 10.0 AS per_share,
       board_plan_pub_date, plan_progress
FROM read_parquet('%s/std/dividend.parquet')
WHERE code IN ('%s')
  AND board_plan_pub_date <= DATE '%s'
  AND board_plan_pub_date >= DATE '%s' - INTERVAL %d DAY
  AND bonus_cancel_pub_date IS NULL
  AND (plan_progress IS NULL OR plan_progress NOT IN ('终止', '取消分红'))
  AND bonus_ratio_rmb > 0
"""
WINDOW_DAYS = 800
# 进度排序。★ 两个源的用词不一样（本地"实施方案" / 东财"实施分配"），
#   两套都要认 —— 只认一套的话外部那条会被当成最低优先级，
#   于是"更靠后的进度"选错，而它不报错。
_RANK = {'实施方案': 3, '实施分配': 3, '股东大会预案': 2, '董事会预案': 1,
         '预披露': 0}


def _rank(p):
    return _RANK.get((p or '').strip(), 0)


def _bonus_type(report_date):
    """归属报告期的月份决定它是年度/中期/季度 —— 不依赖文本标签。"""
    m = int(str(report_date)[5:7])
    return {12: '年度分红', 6: '中期分红'}.get(m, '季度分红')


def _local_rows(codes_in, root=None, day=None):
    """{code: [row]}，row = (报告期, 每股, 公告日, 进度, 来源)。"""
    from assay import stock as st
    d = _day(day)
    con = st.con()
    out = {}
    for r in con.execute(_SQL_RAW % (st._lake(root), "','".join(codes_in), d, d,
                                     WINDOW_DAYS)).fetchall():
        out.setdefault(r[0], []).append(
            {'report_date': str(r[1])[:10], 'per_share': round(r[3], 4),
             'plan_pub': str(r[4])[:10], 'progress': r[5], 'src': '本地'})
    return out


def _merge_rows(local, ext):
    """合并两个源。**同一次分红的身份是【预案公告日】，不是归属报告期。**

    🔴 这条是踩出来的：同一次分红，两个源标的 `report_date` 可能**不一样**。
      实测长江电力那次 0.21 元（公告 2024-12-14）：
          本地（聚宽）report_date = 2024-09-30（季度分红）
          东财        REPORT_DATE = 2024-06-30
      按报告期去重的话它会变成**两条**，FY2024 从 0.943 变 1.153
      （+22%），**而这不报错** —— 只是那一行的目标价被算高、永远不触发。

    🔴 **公告日也可能不一样**：东阿阿胶 2026 中期那笔 1.3448，
      本地（聚宽 `board_plan_pub_date`）是 **2026-08-21**（中期董事会预案），
      东财 `PLAN_NOTICE_DATE` 是 **2026-04-25**（年报里先披露的分红计划）——
      差了 4 个月。只按公告日去重的话它也会变成两条。

    所以"同一次分红"的判据是**三条任一命中**（按这个顺序）：
      ① `(报告期, 每股金额)` 相同 —— 挡公告日标注不同（东阿阿胶那种）
      ② `(公告日, 每股金额)` 相同 —— 挡报告期标注不同（长江电力那种）
      ③ `公告日` 相同但金额不同 —— 同一次，金额在实施时被修正了：
         取**进度更靠后**那条的金额
    剩下的才是本地真没有的新公告，加进来并标 `new=True`。

    ★ 两边都有时**留本地的 `report_date`** —— 本地那份的口径对过账
      （与 QMT `get_divid_factors` 12/12、与用户手填 8/8），
      外部的标注只在"本地根本没有这条"时才用。
    ★ 会不会把两笔真不同的分红并成一笔？要求金额也相同，
      而"同一报告期两次金额完全相同的现金分红"实际上不存在。
    """
    out = {}
    for code in set(list(local) + list(ext)):
        rows = [dict(r) for r in (local.get(code) or [])]
        by_rd, by_pub_amt, by_pub = {}, {}, {}

        def _index(r):
            by_rd.setdefault((r['report_date'], r['per_share']), r)
            by_pub_amt.setdefault((r['plan_pub'], r['per_share']), r)
            by_pub.setdefault(r['plan_pub'], r)

        for r in rows:
            _index(r)
        for r in (ext.get(code) or []):
            cur = (by_rd.get((r['report_date'], r['per_share']))
                   or by_pub_amt.get((r['plan_pub'], r['per_share'])))
            if cur is not None:                 # ①②同一次、同金额
                if _rank(r['progress']) > _rank(cur['progress']):
                    cur['progress'] = r['progress']
                continue
            cur = by_pub.get(r['plan_pub'])
            if cur is not None:                 # ③同一次，金额被修正
                if _rank(r['progress']) >= _rank(cur['progress']):
                    cur['per_share'] = r['per_share']
                    cur['progress'] = r['progress']
                    cur['new'] = True
                continue
            nr = dict(r, new=True)              # 本地真没有的新公告
            rows.append(nr)
            _index(nr)
        out[code] = sorted(rows, key=lambda x: x['report_date'], reverse=True)
    return out


def _agg(rows, day=None):
    """两个口径都从**同一份行**上算 —— 分两套实现迟早分叉。

    🔴 先按 `day` 砍掉**公告日晚于 as-of 的那些行**：外部缓存里可能有比
      as-of 更新的公告（缓存是"今天"抓的，而 as-of 可以往回问），
      不砍就是未来函数 —— 而它算出来的数看着完全正常。
    """
    d = _day(day)
    rows = [x for x in rows if (x.get('plan_pub') or '') <= d]
    fy = r365 = None
    ys = [int(x['report_date'][:4]) for x in rows
          if x['report_date'][5:7] == '12']
    if ys:
        y = max(ys)
        pick = [x for x in rows if x['report_date'][:4] == str(y)]
        fy = {'per_share': round(sum(x['per_share'] for x in pick), 4),
              'year': y, 'n': len(pick),
              'last_pub': max(x['plan_pub'] or '' for x in pick),
              'src': '%d 年度合计' % y,
              'detail': ' + '.join(
                  '%s %s=%s%s' % (_bonus_type(x['report_date']),
                                  x['report_date'][:7], x['per_share'],
                                  '（东财新）' if x.get('new') else '')
                  for x in sorted(pick, key=lambda x: x['report_date']))}
    lo = (datetime.date.fromisoformat(d) - datetime.timedelta(days=365)
          ).isoformat()
    pick = [x for x in rows if (x['plan_pub'] or '') >= lo]
    if pick:
        r365 = {'per_share': round(sum(x['per_share'] for x in pick), 4),
                'n': len(pick),
                'last_pub': max(x['plan_pub'] or '' for x in pick),
                'src': '近 12 个月已公告合计'}
    return fy, r365


def _day(day=None):
    if isinstance(day, str):
        return day
    return (day or datetime.date.today()).isoformat()


# ------------------------------------------------- 外部分红（东财，一天一次）
# 🔴 取的是【原始方案明细】而不是别人算好的股息率：f133 那个"股息率"里藏着
#   别人的窗口与去重规则，实测招商银行差一倍（见模块 docstring）。
EXT_URL = ('https://datacenter-web.eastmoney.com/api/data/v1/get?'
           'sortColumns=PLAN_NOTICE_DATE&sortTypes=-1&pageSize=200'
           '&pageNumber=1&reportName=RPT_SHAREBONUS_DET'
           '&columns=SECURITY_CODE,REPORT_DATE,PLAN_NOTICE_DATE,'
           'ASSIGN_PROGRESS,PRETAX_BONUS_RMB&quoteColumns=&source=WEB'
           '&client=WEB&filter=%s')
EXT_FILE = 'div_ext.json'
EXT_TIMEOUT = 12


def ext_path(root=None):
    """缓存落 `datalake/rt/`。

    🔴 **不写进 `std/dividend.parquet`** —— 那张表是 loader 链的产物，
      被外部数据污染之后"面板与 std 不一致"不报错。`rt/` 就是既有的
      "外部抓来的、构建链一个脚本都不碰"那个目录（同 1 分钟线）。
    """
    from assay import stock as st
    return os.path.join(st._lake(root), 'rt', EXT_FILE)


def _ext_cache(root=None):
    p = ext_path(root)
    if not os.path.isfile(p):
        return {'fetched': {}, 'rows': {}}
    try:
        with open(p, encoding='utf-8') as fh:
            d = json.load(fh)
        d.setdefault('fetched', {})
        d.setdefault('rows', {})
        return d
    except Exception:                                       # noqa: BLE001
        return {'fetched': {}, 'rows': {}}


def _ext_save(d, root=None):
    p = ext_path(root)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(d, fh, ensure_ascii=False)
    os.replace(tmp, p)


def _ext_fetch(codes_in, day=None):
    """一次批量请求覆盖多只（`SECURITY_CODE in (...)`）。"""
    import urllib.parse
    import urllib.request
    d = _day(day)
    since = (datetime.date.fromisoformat(d)
             - datetime.timedelta(days=WINDOW_DAYS)).isoformat()
    six = [c[:6] for c in codes_in]
    flt = ('(SECURITY_CODE in ("%s"))(PLAN_NOTICE_DATE>=\'%s\')'
           % ('","'.join(six), since))
    req = urllib.request.Request(
        EXT_URL % urllib.parse.quote(flt),
        headers={'User-Agent': 'Mozilla/5.0',
                 'Referer': 'https://data.eastmoney.com/'})
    raw = urllib.request.urlopen(req, timeout=EXT_TIMEOUT).read()
    o = json.loads(raw.decode('utf-8', 'replace'))
    if not o.get('success'):
        raise AlertError('东财分红接口返回失败：%s' % o.get('message'))
    res = o.get('result') or {}
    if (res.get('pages') or 1) > 1:
        # ★ 分页了就明说 —— 悄悄只取第一页会让老的那几档消失，
        #   而"少了一笔中期分红"的表现是目标价偏低，不报错。
        raise AlertError('东财分红接口分页了（%s 页 / %s 条），'
                         '把窗口或票数调小' % (res.get('pages'), res.get('count')))
    out = {}
    for r in (res.get('data') or []):
        if not r.get('PRETAX_BONUS_RMB'):
            continue          # 预披露只给比例、没有金额 —— 不是"已公告分红"
        pub = (r.get('PLAN_NOTICE_DATE') or '')[:10]
        if not pub or pub > d:
            continue          # 🔴 公告日晚于今天的一律不要（未来函数）
        jc = _jq(r['SECURITY_CODE'])
        out.setdefault(jc, []).append({
            'report_date': (r.get('REPORT_DATE') or '')[:10],
            'per_share': round(float(r['PRETAX_BONUS_RMB']) / 10.0, 4),
            'plan_pub': pub, 'progress': r.get('ASSIGN_PROGRESS'),
            'src': '东财'})
    return out


def _jq(six):
    from assay import stock as st
    return st.norm_code(six)


def ext_div(codes_in, root=None, day=None, force=False):
    """外部分红明细，**一天最多一次**，已经有今天的就不再调用。

    ★ 判据按【每只票】记（`fetched[code]`），所以：
      · 当天抓过的跳过 —— 一天一次，多开几个标签页也不会变成一串请求
      · 新加进清单的票**立刻抓**（它一条都没有）—— 同 `_rt_ensure` 的思路
      · 没抓过的那几只**合并成一次批量请求**
    ★ 抓失败不抛给页面：外部源挂了应该退回本地那份，而不是让整页打不开。
      失败也记 `err` 并把 `fetched` 留空（下次还会试）。
    """
    d = _day(day)
    cache = _ext_cache(root)
    want = [c for c in (_jq(x) for x in (codes_in or [])) if c]
    todo = [c for c in want
            if force or cache['fetched'].get(c) != d]
    out = {'rows': {c: cache['rows'].get(c) or [] for c in want},
           'fetched': {c: cache['fetched'].get(c) for c in want},
           'called': False, 'n_new': 0, 'err': None, 'day': d}
    if not todo:
        return out
    try:
        got = _ext_fetch(todo, day=d)
        out['called'] = True
        for c in todo:
            cache['rows'][c] = got.get(c) or []
            cache['fetched'][c] = d
            out['rows'][c] = cache['rows'][c]
            out['fetched'][c] = d
        out['n_new'] = sum(len(v) for v in got.values())
        _ext_save(cache, root)
    except Exception as e:                                  # noqa: BLE001
        out['err'] = '%s: %s' % (type(e).__name__, str(e)[:120])
    return out


def suggest_div(codes_in, root=None, day=None, ext=True):
    """每股分红的**两个**口径 —— 都是**已公告的实际分红**，不是预测。

    返回 `{code: {per_share, src, fy: {...}, r365: {...}, ext: {...}}}`，
    `per_share` / `src` 取**默认口径**（`fy` = 最近一个完整会计年度合计）。

    🔴 默认不能用 `r365`：半年派的公司窗口会混入不同归属期，实测海尔智家
      少 26%、国电电力少 41%（都是漏掉中期分红），而它不报错。
    ★ `ext=True` 时把外部（东财）明细并进来，**口径仍是这里算的** ——
      外部只提供更新的原始行。`ext_div` 自己带"一天一次"的节流。
    ★ 每只票带 `ext`：`{fetched, n_ext, new: [...]}` —— 哪几条是外部比本地
      新的必须能看见（"数字从哪来"是这一页能不能信的前提）。
    """
    from assay import stock as st
    want = [c for c in (st.norm_code(x) for x in (codes_in or [])) if c]
    if not want:
        return {}
    d = _day(day)
    loc = _local_rows(want, root=root, day=d)
    ex = {'rows': {}, 'fetched': {}, 'err': None, 'called': False}
    if ext:
        ex = ext_div(want, root=root, day=d)
    merged = _merge_rows(loc, ex.get('rows') or {})
    out = {}
    for c in want:
        rows = merged.get(c) or []
        if not rows:
            continue
        fy, r365 = _agg(rows, day=d)
        new = [x for x in rows if x.get('new')]
        v = {'fy': fy, 'r365': r365,
             'ext': {'fetched': (ex.get('fetched') or {}).get(c),
                     'called': ex.get('called'), 'err': ex.get('err'),
                     'n_new': len(new),
                     'new': [{'report_date': x['report_date'],
                              'per_share': x['per_share'],
                              'plan_pub': x['plan_pub'],
                              'progress': x['progress']} for x in new[:4]]}}
        pick = fy or r365 or {}
        v['per_share'] = pick.get('per_share')
        v['src'] = pick.get('src')
        v['n'] = pick.get('n')
        v['last_pub'] = pick.get('last_pub')
        out[c] = v
    return out


# ------------------------------------------------------------------- 估值
# 指标条件要日线：取多少根由**条件自己说**（`sig_warm`），再留一截余量。
IND_BARS_MIN = 120
IND_BARS_MAX = 600
_BARS_CACHE = {}


def _bars_for(codes, day, n, root=None):
    """一次取全部票的近 n 根日线（**不复权**）。

    🔴 **一次 SQL 取全部**，不逐只查 —— 这份数据每分钟轮询都要用，
      逐只查就是 N 次 duckdb 往返（同「抓取范围合并去重」那条）。
    🔴 **不复权**：这一页的目标价是**照着下单用的**，而下单看的是券商
      软件里那个价（同 K 线图默认给不复权那条）。用后复权算出来的触发价
      看着正常，却和你挂单要填的数字对不上。
    ★ 按「面板最新日 + 根数」缓存：面板一天才动一次，而轮询每分钟一轮。
    """
    from assay import stock as st
    key = (str(day), int(n), str(root), tuple(sorted(codes)))
    if key in _BARS_CACHE:
        return _BARS_CACHE[key]
    if not codes:
        return {}
    c = st.con()
    pn = st.panel(root)
    q = "','".join(codes)
    rows = c.execute("""
        SELECT jq_code, date, open, high, low, close_bfq, volume_shares
        FROM %s WHERE jq_code IN ('%s') AND date <= DATE '%s'
        QUALIFY row_number() OVER (PARTITION BY jq_code ORDER BY date DESC) <= %d
        ORDER BY jq_code, date""" % (pn, q, day, int(n))).fetchall()
    out = {}
    for r in rows:
        if None in (r[2], r[3], r[4], r[5]):
            continue          # 停牌那天没有 OHLC —— 跳过，别拿 None 去算
        out.setdefault(r[0], []).append(
            {'date': r[1].isoformat(), 'open': r[2], 'high': r[3],
             'low': r[4], 'close': r[5], 'volume': r[6] or 0})
    _BARS_CACHE.clear()       # 只留最近一份：键里含日期，旧的永远不会再命中
    _BARS_CACHE[key] = out
    return out


def _bars_now(bars, price, today):
    """把**今天的实时价**接到日线末尾，再拿去算指标。

    🔴 不接的话，「距 20 日线」算的是**昨天**离昨天那根均线多远 ——
      而这一页问的是"现在离它多远"，盘中差一整天的行情，
      **而它不报错**（那个数看着完全正常）。
    ★ 面板已经有今天了（收盘后同步过）就**替换**最后一根，不再追加一根
      ——否则同一天会被算成两天，均线的分母就错了。
    """
    if not bars or not price:
        return bars
    last = bars[-1]
    if last['date'] >= today:
        b = dict(last)
        b['close'] = price
        b['high'] = max(b['high'], price)
        b['low'] = min(b['low'], price)
        return bars[:-1] + [b]
    return bars + [{'date': today, 'open': price, 'high': price,
                    'low': price, 'close': price, 'volume': 0}]


def _derive(row, div, bars=None):
    """把每一档展开成 {目标价, 目标股息率}。另一个**现算**，不存。"""
    from assay import indicators as I
    out, ind = [], []
    for t in row.get('tiers') or []:
        if t['by'] == 'ind':
            # 指标档：**触发价现算**（"今天收在什么价位这条件刚好成立"）。
            # 于是它和价格档落在同一个口径上，页面那套「到价/接近/还差多少」
            # 一行都不用改。解不出来时给 why，**不猜一个数**。
            px, why, now = None, '没有行情', None
            if bars:
                try:
                    now = I.sig_value(bars, t['sig'], t['args'])
                    px, why = I.trigger_price(bars, t['sig'], t['args'], t['v'])
                except I.IndError as e:
                    why = str(e)
            ind.append({'by': 'ind', 'sig': t['sig'], 'args': t['args'],
                        'v': t['v'], 'price': px, 'now': now, 'why': why,
                        'dir': I.sig_dir(t['sig']), 'unit': I.sig(t['sig'])['unit'],
                        'text': I.sig_text(t['sig'], t['args'], t['v']),
                        'yield': None, 'note': t.get('note') or ''})
            continue
        if t['by'] == 'price':
            price, yld = t['v'], (div / t['v'] if t['v'] else None)
        else:
            yld, price = t['v'], (div / t['v'] if t['v'] else None)
        out.append({'by': t['by'], 'price': (round(price, 3) if price else None),
                    'yield': (round(yld, 6) if yld else None),
                    'note': t.get('note') or ''})
    # 目标价【从高到低】：价高的那档先触发，与表格里的排法一致。
    # ★ 指标档**排在价格档后面、保持原序**：它们的触发价有涨有跌
    #   （金叉是涨上去才成立），混进"从高到低"里那句表头就是假的。
    out.sort(key=lambda x: -(x['price'] or 0))
    return out + ind


def alerts_sig_warm(t):
    from assay import indicators as I
    return I.sig_warm(t.get('sig'), t.get('args'))


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
    # 🔴 分红在这里**现算**（不是账本里的字段）：新公告一到，
    #   所有档位的目标价自动跟着变。`suggest_div` 自带"一天一次"的节流。
    try:
        sug = suggest_div([x['code'] for x in rows], root=root)
    except Exception as e:                                  # noqa: BLE001
        sug, out['div_err'] = {}, '%s: %s' % (type(e).__name__, e)
    out['suggest'] = sug
    con = st.con()
    p = st.panel(root)
    d = con.execute('SELECT max(date) FROM %s' % p).fetchone()[0]
    out['date'] = str(d)[:10] if d else None
    # ---- 指标档要日线：**只在真有指标档时才取**（多数清单只有价格档）----
    today = _day()   # 已经是 'YYYY-MM-DD'
    need, warm = [], 0
    for x in rows:
        for t in (x.get('tiers') or []):
            if t.get('by') == 'ind':
                need.append(x['code'])
                try:
                    warm = max(warm, alerts_sig_warm(t))
                except Exception:                           # noqa: BLE001
                    pass
    bars = {}
    if need:
        nb = max(IND_BARS_MIN, min(IND_BARS_MAX, warm + 40))
        try:
            bars = _bars_for(sorted(set(need)), out['date'], nb, root=root)
        except Exception as e:                              # noqa: BLE001
            out['ind_err'] = '%s: %s' % (type(e).__name__, e)
    q = "','".join(x['code'] for x in rows)
    px = {r[0]: r for r in con.execute("""
        SELECT jq_code, sec_name, close_bfq, preclose, pe_ttm, pb, sw_l1_name,
               is_st, public_status
        FROM %s WHERE date = DATE '%s' AND jq_code IN ('%s')"""
        % (p, d, q)).fetchall()}
    # ★ 面板里没有的那些，批量问一次正本（循环里逐只查 = N 次往返）
    from assay import symbols as _SYM     # 「代码->类别/名称」的唯一正本
    alt_nm = _SYM.alt_names(_SYM.default_root(root),
                            [x['code'] for x in rows if x['code'] not in px])
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
            # 🔴 ETF/指数**本来就不在面板里**（面板是股票宽表）——
            #   买点清单加 ETF 是讲得通的（项目里就有 ETF 轮动策略），
            #   不兜的话那一行会显示成代码并被误标成"查不到"
            #   （2026-09-21 与自选那处是同一个根因，一起修）。
            #   取名走**唯一正本** `assay/symbols.py`。
            # ★ 只对 ETF/指数回落：股票查不到就是真的查不到（用户那张表里
            #   就有写错的代码，那一行必须仍然标 missing）。
            k = _SYM.kind_of(x['code'], root)
            nm = alt_nm.get(x['code']) if k else None
            if nm:
                it.update({'name': nm, 'kind': k, 'price': None,
                           'missing': False})
            else:
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
        # ---- 分红：实际已公告的，现算 ----
        sg = sug.get(x['code']) or {}
        fy, alt = sg.get('fy') or {}, sg.get('r365') or {}
        div = fy.get('per_share') or 0
        it['div'] = (round(div, 4) if div else None)
        it['div_src'] = fy.get('src')
        it['div_detail'] = fy.get('detail')
        it['div_last_pub'] = fy.get('last_pub')
        it['div_alt'] = alt.get('per_share')
        it['div_alt_src'] = alt.get('src')
        it['div_ext'] = sg.get('ext')
        if not div:
            # ★ 拿不到分红就说清楚，不猜一个数填上去 ——
            #   `by='yield'` 的档算不出目标价，页面要给理由。
            it['div_missing'] = True
        it['yield_now'] = (round(div / pr, 6) if pr and div else None)
        near = it.get('near') if it.get('near') is not None else NEAR
        it['near_used'] = near
        tiers = _derive(it, div, _bars_now(bars.get(x['code']) or [], pr, today))
        hit_i = near_i = None
        for i, t in enumerate(tiers):
            tp = t['price']
            if not (tp and pr):
                t['state'], t['gap'] = 'na', None
                continue
            # gap = 现价要变动多少才到这一档（负数 = 还要跌，正数 = 还要涨）
            t['gap'] = round(tp / pr - 1, 6)
            # 🔴 **方向不是都朝下**：价格档与「距均线 / 超卖」是跌到才算，
            #   而「金叉」是**涨上去**才成立。方向记反的话，一只已经金叉的
            #   票会被显示成"还差 3%"，**而它不报错**。
            if t.get('dir') == 'up':
                if pr >= tp:
                    t['state'] = 'hit'
                    hit_i = i
                elif pr >= tp * (1 - near):
                    t['state'] = 'near'
                    if near_i is None:
                        near_i = i
                else:
                    t['state'] = 'far'
                continue
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
                   # ★ 指标档把**条件本身**记进去（"距 MA20 ≤ -3%"）——
                   #   只记一个触发价的话，事后翻这条记录根本看不出
                   #   当时是因为什么提醒的，而那正是要复盘的东西。
                   'cond': t.get('text'), 'dir': t.get('dir') or 'down',
                   'src': x.get('rt_src') or 'close'}
            _append(rec, FIRED)
            seen.add(key)
            new.append(rec)
    return new


def notify_text(rec):
    """一条通知的正文。**要把判据写进去** —— "东阿阿胶到了"没法据此下单。"""
    up = rec.get('dir') == 'up'
    kind = ('站上' if up else '跌破') if rec['kind'] == 'hit' else '接近'
    if rec.get('cond'):
        # 指标档：写清是**哪个条件**触发的，只给个价没法复盘
        return '%s %s：现价 %s，触发价 %s（%s）' % (
            (rec.get('name') or rec['code']), kind, rec.get('price'),
            rec.get('target'), rec['cond'])
    return '%s %s：现价 %s，目标 %s（股息率 %.2f%%）' % (
        (rec.get('name') or rec['code']), kind,
        rec.get('price'), rec.get('target'), (rec.get('yield') or 0) * 100)
