# -*- coding: utf-8 -*-
"""公司行动（除权除息 / 送转 / 配股）—— 实盘账本侧的**唯一正本**。

## 🔴 要解决的问题：除权之后成本没跟着调，浮盈被系统性报低

用户 2026-09-23 报「分红后现金增加，对应的股的成本也应该降低」。
实测红利混合-M 三次除权合计 **3,560 元**（占权益 0.36%）——
股价除权当天真的掉下去，而账本记的成本价一分没动，于是逐只浮盈全部偏低，
**而总资产那一格看着完全正常**（现金那半在券商那边加回来了）。

🔴 **我第一版答成"不该动成本"，那是把两个口径搞混了**：

    后复权（回测）   除权日价**不掉**  -> 成本不动是对的（分红以"价格上涨"体现）
    不复权（账本）   除权日价**掉了**  -> 成本必须跟着调，否则两边不自洽

`broker.py` 那句"entry_price 在除权时不缩"是**后复权**语境下的规则，
搬到账本这边就反了。**两条的分工别记反。**

## 数据源：tdx `gbbq`，不用聚宽 `dividend`

`std/dividend.parquet` 走 B 腿（**手动导**）—— 缺一条就静默少一次除权。
而复权因子随 cron 每天到，于是会出现「因子跳了、分红表里没有那一条」
（实测 601318 差 486 元）与它的反面（002293 分红表有、因子没跳）。
**两个方向都有，且都不报错。**

gbbq 随 `tdx2db cron` 每天自动到（实测提前 4 天覆盖除权），且**带送转与配股**
—— 那两样聚宽那份根本没有。c1~c4 的含义与通用除权公式见
`datalake/docs/数据字典/3-按字段索引.md`「tdx `raw_gbbq` 的 c1~c4」。

## 🔴 只动【股数与成本】，不动 `lot['price']`

`lot['price']` 是**原始成交价**，`lv/sig._seed` 会拿它 × **建仓日**因子
喂给引擎的 `entry_price`（止损 / 吊灯 / 红利税档位都读它）。
动了它实盘走的就不是策略自己的代码路径了（同 `fifo_lots` 的 docstring）。

    lot['shares']  ← 送转会改它（真实股数）。而 `_seed` 是 `shares / factor(今天)`，
                     factor 同步放大，**两者抵消** —— 引擎那边一个字不用改
    lot['paid']    ← 该批剩余部分的**实付本金**。派现减它、送转不动它
    lot['price']   ← **原样不动**

于是摊薄成本 `(paid + fee) / shares` 天然对：
派现 -> paid 降；送转 -> shares 涨。

## ⚠ 三条明知的取舍

1. **税不自动扣。** A 股红利税是**卖出时**由券商按持有期补扣
   （财税[2015]101 号，≤1月 20% / 1月~1年 10% / >1年 免）——
   而那笔钱会**出现在卖出成交的费用里**，用户照账单录进去就已经扣过了。
   这里再扣一次就是双计。所以现金按**税前**加，与券商到账口径一致。
   ★ 每批累计的 `div_gross` 仍然记着（排查时要用），只是不参与现金。
2. **到账日按【除权日】算。** gbbq 只有除权日；沪市红利发放日通常就是它，
   深市一般 T 日到账。差一两天对成本与浮盈没有影响（两边同一天调）。
3. 🔴 **配股（c4）不自动执行。** 送转与派现是**强制**的（到日子就发生），
   而配股**要掏钱、而且可以放弃** —— 替人决定就是错的。
   所以它只**留痕 + 提示**，`applied=False`（同「报出来，别替人决定」）。
"""
import datetime
import os

from . import base as _base


# 单条公司行动的字段（页面与账本共用一份形状）
#   ex_date / code / cash（每股派现，税前）/ split（每股送转）/
#   rights（每股配股数）/ rights_price（配股价）
_COLS = ('ex_date', 'code', 'cash', 'split', 'rights', 'rights_price')

# 🔴 哨兵行：`raw_gbbq` 里有 2 条 1991 年之前的（1899-12-30 / 1990-03-01），
#   不是真事件。消费侧必须滤掉 —— 留着的话 `since=None` 的全量查询会把它们
#   算进来，而它不报错。
_MIN_EX = datetime.date(1991, 1, 1)

# 新鲜度：落后这么多天就在页面上说出来。gbbq 随 cron 每天到，
# 🔴 落后的表现是「今天除权的那几只不会被调」—— 又一个不报错的坑，
#   所以**必须可感知**（同「拒单必须可见」）。
STALE_DAYS = 5


# 🔴 按 (文件 mtime, 代码集合, 区间) 缓存查询结果。
#   `fifo_lots` 是重放的唯一入口，而 `lots_asof` 每天调一次它 —— 不缓存的话
#   光渲染一次账户列表就是 **20 条 duckdb 查询、每条还新建一个连接**
#   （实测 0.75 秒）。那点开销自己不报错，但它把「侧栏整块重渲染」那个
#   既有竞态的窗口撑宽了，表现是页面上刚填的表单被清空 —— 实测打挂了
#   「推进完 0 笔成交」那条用例。
#   ★ 键里带 **mtime** 而不是"查过没有"：gbbq 每天重写，缓存必须自己失效，
#     否则页面上是旧数**而它不报错**（同 `factor_eval._PQ` 那条）。
#   ★ 🔴 **逐键各记各的 mtime**，不是"miss 就 clear()" —— 后者会让缓存
#     最多只留一项（同一轮渲染里每个账户的代码集合都不同，等于没有缓存）。
#     这个错本项目两轮里犯过两次，别再犯第三次。
_CACHE = {}
_CACHE_MAX = 64


def _con():
    import duckdb
    return duckdb.connect()


def _sql(root=None):
    from assay import paths
    return paths.tdx_gbbq_sql(root)


def available(root=None):
    """这份 parquet 在不在。**不抛错** —— "有没有"与"该怎么跟人解释"是两件事
    （同 `symbols.day_px` 那条）。"""
    from assay import paths
    return os.path.exists(os.path.join(paths.datalake(root), paths.GBBQ_REL))


def status(root=None):
    """给页面看的一行：有没有、覆盖到哪天、落后几天。

    🔴 判据用**数据自己的 `max(ex_date)`**（去掉未来那些已公告未除权的），
      不用文件 mtime —— mtime 只说明"跑过了"，不说明"里面有今天"
      （同「判据永远是现在的状态，不是记录」）。
    """
    from assay import paths
    p = os.path.join(paths.datalake(root), paths.GBBQ_REL)
    out = {'path': p, 'exists': os.path.exists(p), 'max_ex_date': None,
           'n': 0, 'stale_days': None, 'stale': False, 'future_n': 0}
    if not out['exists']:
        return out
    today = datetime.date.today()
    row = _con().execute(
        "SELECT count(*), max(ex_date) FILTER (WHERE ex_date <= ?), "
        "       count(*) FILTER (WHERE ex_date > ?) "
        "FROM %s WHERE ex_date >= ?" % _sql(root), [today, today, _MIN_EX]).fetchone()
    out['n'] = int(row[0] or 0)
    out['future_n'] = int(row[2] or 0)
    if row[1]:
        mx = _base._d(row[1])
        out['max_ex_date'] = mx.isoformat()
        # ★ 用自然日算落后即可 —— 这里只是"要不要提醒人"，不是判交易日。
        #   而除权日本来就只落在交易日上，所以周末不会误报到 5 天以上。
        out['stale_days'] = (today - mx).days
        out['stale'] = out['stale_days'] > STALE_DAYS
    return out


def actions(codes, since=None, until=None, root=None):
    """这些票在 [since, until] 内的公司行动，按 (除权日, 代码) 排序。

    ★ `until` 一律要传实际的 as-of 日 —— 表里有 **129 条已公告未除权**的
      （最远 15 天后）。对"今天的持仓"它们是**未来函数**；
      拿来提前预警是另一回事（见 `upcoming`），两种用法别混。
    """
    codes = sorted({c for c in codes if c})
    if not codes or not available(root):
        return []
    from assay import paths as _paths
    _p = os.path.join(_paths.datalake(root), _paths.GBBQ_REL)
    key = (tuple(codes), str(since or ''), str(until or ''), root or '')
    hit = _CACHE.get(key)
    mt = os.path.getmtime(_p)
    if hit is not None and hit[0] == mt:
        return hit[1]
    w = ["jq_code IN (%s)" % ','.join('?' * len(codes)), 'ex_date >= ?']
    args = list(codes) + [max(_MIN_EX, _base._d(since)) if since else _MIN_EX]
    if until:
        w.append('ex_date <= ?')
        args.append(_base._d(until))
    rows = _con().execute(
        "SELECT ex_date, jq_code, cash_per_share, split_per_share, "
        "       rights_per_share, rights_price FROM %s WHERE %s "
        "ORDER BY ex_date, jq_code" % (_sql(root), ' AND '.join(w)), args).fetchall()
    out = []
    for r in rows:
        a = dict(zip(_COLS, (_base._d(r[0]), r[1]) + tuple(float(x or 0) for x in r[2:])))
        # ★ 三项全是 0 的行不算事件（gbbq 里有登记性质的空行）——
        #   列出来的话页面上是一条"什么都没发生"的记录，那是噪声。
        if a['cash'] or a['split'] or a['rights']:
            out.append(a)
    if len(_CACHE) >= _CACHE_MAX:
        _CACHE.clear()          # 满了整份丢，不做 LRU —— 这里只是省往返
    _CACHE[key] = (mt, out)
    return out


def upcoming(codes, days=15, root=None):
    """**还没除权**的那些（已公告）—— 提前预警用，不参与记账。"""
    today = datetime.date.today()
    return [a for a in actions(codes, since=today, root=root)
            if a['ex_date'] > today
            and (a['ex_date'] - today).days <= days]


def apply_lot(lot, act):
    """把一条公司行动作用到一个 FIFO 批次上，返回这一批发生了什么。

    🔴 顺序是**先派现、后送转** —— 派现按**除权前**的股数算
      （每股派现 × 当时持有的股数），送转之后股数才变。
      反过来的话一次 10 送 10 会让分红**翻倍**，**而它不报错**。
    """
    sh = lot['shares']
    ev = {'shares_before': sh, 'cash': 0.0, 'shares_after': sh, 'tax_base': 0.0}
    if act['cash']:
        gross = sh * act['cash']
        ev['cash'] = gross
        ev['tax_base'] = gross
        # 实付本金按到手的现金往下调 —— 摊薄成本因此正好降 c1/股
        lot['paid'] = lot.get('paid', sh * lot['price']) - gross
        lot['div_gross'] = lot.get('div_gross', 0.0) + gross
    if act['split']:
        # 送转：股数按比例涨，`paid` **不动**（没有新的钱投进去）
        lot['shares'] = int(round(sh * (1.0 + act['split'])))
        ev['shares_after'] = lot['shares']
        lot['split_mul'] = lot.get('split_mul', 1.0) * (1.0 + act['split'])
    return ev


def apply_book(book, since, until, root=None, events=None):
    """把 [since, until] 内的公司行动作用到整本持仓上（**原地改 book**）。

    调用方是 `pos.fifo_lots` 的重放循环 —— 它按日期把成交与公司行动交错处理，
    所以这里只管"某一天这一批行动怎么作用"。
    返回这段里发生的现金增量。

    ⚠ 配股（`rights`）**只记不做**：它要掏钱且可以放弃。
    """
    got = 0.0
    for a in actions(list(book), since, until, root=root):
        lots = book.get(a['code'])
        if not lots:
            continue
        per = {'act': a, 'lots': []}
        for l in lots:
            ev = apply_lot(l, a)
            got += ev['cash']
            per['lots'].append(ev)
        if events is not None and (a['cash'] or a['split'] or a['rights']):
            events.append(per)
    return got


def describe(a):
    """一条行动的人话描述（页面与日志共用一份措辞）。"""
    t = []
    if a.get('cash'):
        t.append('每股派现 %.4f 元（税前）' % a['cash'])
    if a.get('split'):
        t.append('每 10 股送转 %.2f 股' % (a['split'] * 10))
    if a.get('rights'):
        t.append('每 10 股配 %.2f 股 @ %.2f 元（需自己决定参不参与）'
                 % (a['rights'] * 10, a.get('rights_price') or 0))
    return ' · '.join(t) or '（无实际影响）'
