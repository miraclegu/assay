"""srv/stock.py —— 个股：搜索 / 面板 / K 线 / 财务 / 事件 / 同业 / 对比。"""
import re

from .base import (_market, _market_err)


def _stock():
    from assay import stock as m
    return m



def _root(q):
    """按 `run=<run_id>` 解析该次回测用的 datalake 根；没传就是 None（主 lake）。

    🔴 **只接受 run_id，绝不接受路径。** `_dl_root` 里的 `_dir()` 会把 run_id
      过 `RUN_ID_RE` 并要求它在归档索引里 —— 那是唯一的路径来源，杜绝目录穿越。
      直接收一个 `root=` 查询参数等于给了任意文件读。
    ★ 存在的理由：ETF 回测跑在**平行的 `etf_lake`** 上，而那次回测的口径
      （成分、复权、费率）只有它自己的 lake 说得准。
    ⚠ 这里原来写着「实测 `510300.XSHG` 直接抛 StockError」—— **那句已经
      过期**：2026-09-21 起 `symbols.kind_of` 会按快照里的 class 自动把
      ETF/指数路由到 `KIND_FILE` 那几张同形表，主 lake 上照样读得到
      （实测 `profile/kline/links/sectors/peers/events` 六个入口全通）。
      留着会让人以为"不带 run 就一定坏"，而那是**代码和注释打架**
      —— 以站得住的那个为准。
    """
    rid = q.get('run') or ''
    if not rid:
        return None
    from .runs import _dl_root
    return _dl_root(rid)



def _kind(q):
    """`?kind=stock|etf|index` —— **显式指定只从那个池取**，不传就自动判。

    🔴 ETF 与股票的代码长得一模一样（`510300.XSHG` vs `600000.XSHG`），
      所以默认**必须**自动路由 —— 靠调用方每次记得传的都会漏。
    🔴 而指定了就**不许回落**：回落等于"指定了却没生效"，拿到的是另一个池
      的数，**看着完全正常**。对不上时 `symbols.route` 抛 `KindMismatch`，
      由 `_stock_err` 变成 `{'error': …}` 说清楚。
    """
    return (q.get('kind') or '').strip().lower() or None


def _stock_err(fn):
    m = _stock()
    #   🔴 `KindMismatch` 不是 `StockError`（`symbols` 在 `stk` 之下，继承会
    #     绕成环），接不住的话它会变成 HTTP 500 —— 而 500 指不到"你指定的
    #     池和这个代码对不上"这件事，正是这条要说清的内容。
    from assay import symbols as _SYM
    try:
        return fn()
    except (m.StockError, _SYM.KindMismatch) as e:
        return {'error': str(e)}



def api_stock_search(q):
    """GET /api/stock/search?q=&limit= —— 代码或名称模糊搜。"""
    m = _stock()
    return _stock_err(lambda: m.search((q.get('q') or ''),
                                       q.get('limit') or 20))



def api_stock_profile(q):
    """GET /api/stock/profile?code= —— 最新一天的全部关键字段 + 区间涨幅。"""
    m = _stock()
    return _stock_err(lambda: dict(m.profile(q.get('code') or '',
                                             root=_root(q), kind=_kind(q)),
                                   units=m.FIELD_UNIT))



def api_stock_kline(q):
    """GET /api/stock/kline?code=&n=&fq=&end=[&run=] —— 日 K（含服务端算好的均线）。

    `run` 见 `_root()`：从回测详情页打开浮层时带上，才能读到那次回测用的 lake。
    """
    m = _stock()
    return _stock_err(lambda: m.kline(q.get('code') or '',
                                      n=q.get('n') or 250,
                                      # 不给 fq = 让服务端按标的类别定（ETF 前复权、其余不复权）
                                      fq=q.get('fq'),
                                      end=q.get('end'),
                                      off=q.get('off') or 0,
                                      root=_root(q), kind=_kind(q)))



def api_stock_finance(q):
    """GET /api/stock/finance?code=&n= —— 按报告期的财务时序。"""
    m = _stock()
    return _stock_err(lambda: m.finance(q.get('code') or '',
                                        n=q.get('n') or 16, kind=_kind(q)))



def api_stock_indicators(q):
    """GET /api/stock/indicators?code=&n=&fq=&off=[&inds=] —— 按需算一组指标。

    `inds` 不传 = 老行为（MACD/KDJ/RSI/BOLL），老链接照旧能用。
    传 `macd,kdj` 或一段 JSON（要改参数时）—— 定义全在
    `assay/indicators.py`，这里一个指标名都不认识。
    """
    m = _stock()
    return _stock_err(lambda: m.indicators(q.get('code') or '',
                                           n=q.get('n') or 250,
                                           # 不给 fq = 让服务端按标的类别定（ETF 前复权、其余不复权）
                                      fq=q.get('fq'),
                                           off=q.get('off') or 0,
                                           inds=m.parse_inds(q.get('inds'))))


def api_indicator_defs(q):
    """GET /api/indicators/defs —— 有哪些指标、各带什么参数、画哪几条线。

    🔴 **清单由服务端给**（同「可选基准由服务端给」「口径解释在服务端」）：
      页面硬编码一份的话，加一个指标要改两处，而漏改那处的表现是
      **新指标在页面上根本不出现** —— 不报错，只是没人知道它存在。
    """
    from assay import indicators as I
    return {'inds': I.defs(), 'signals': I.signal_defs()}



def api_stock_events(q):
    """GET /api/stock/events?code=&since= —— 除权/财报/解禁/股本变动。"""
    m = _stock()
    return _stock_err(lambda: m.events(q.get('code') or '', q.get('since'),
                                       kind=_kind(q)))



def api_stock_peers(q):
    """GET /api/stock/peers?code=&n= —— 同申万一级行业。"""
    m = _stock()
    return _stock_err(lambda: m.peers(q.get('code') or '', n=q.get('n') or 20,
                                      kind=_kind(q)))



def api_stock_links(q):
    """GET /api/stock/links?code= —— 实盘持仓 / 自选 / 回测选过它。"""
    m = _stock()
    return _stock_err(lambda: m.links(q.get('code') or '', kind=_kind(q)))



def api_stock_sectors(q):
    """GET /api/stock/sectors?code= —— 所属申万行业与通达信板块。"""
    mk = _market()
    return _market_err(lambda: mk.stock_sectors(q.get('code') or ''))



def api_compare(q):
    """GET /api/compare?codes=a,b,c&n= —— 多股【后复权】归一涨幅。"""
    m = _stock()
    cs = [x for x in (q.get('codes') or '').replace(' ', ',').split(',') if x]
    return _stock_err(lambda: m.compare(cs, n=q.get('n') or 250))


# ==================== 盘面 / 行业板块 ====================
