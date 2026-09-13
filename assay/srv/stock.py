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
    ★ 存在的理由：ETF 回测跑在**平行的 `etf_lake`** 上，那些代码在主面板里
      一行都没有（实测 `510300.XSHG` 直接抛 StockError）。不带 lake 的话，
      从 ETF 回测页点开浮层就是一片空白 —— 而"点了没反应"是最难查的那种坏。
    """
    rid = q.get('run') or ''
    if not rid:
        return None
    from .runs import _dl_root
    return _dl_root(rid)



def _stock_err(fn):
    m = _stock()
    try:
        return fn()
    except m.StockError as e:
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
                                             root=_root(q)),
                                   units=m.FIELD_UNIT))



def api_stock_kline(q):
    """GET /api/stock/kline?code=&n=&fq=&end=[&run=] —— 日 K（含服务端算好的均线）。

    `run` 见 `_root()`：从回测详情页打开浮层时带上，才能读到那次回测用的 lake。
    """
    m = _stock()
    return _stock_err(lambda: m.kline(q.get('code') or '',
                                      n=q.get('n') or 250,
                                      fq=(q.get('fq') or 'bfq'),
                                      end=q.get('end'),
                                      root=_root(q)))



def api_stock_finance(q):
    """GET /api/stock/finance?code=&n= —— 按报告期的财务时序。"""
    m = _stock()
    return _stock_err(lambda: m.finance(q.get('code') or '',
                                        n=q.get('n') or 16))



def api_stock_indicators(q):
    """GET /api/stock/indicators?code=&n=&fq= —— MACD/KDJ/RSI/BOLL。"""
    m = _stock()
    return _stock_err(lambda: m.indicators(q.get('code') or '',
                                           n=q.get('n') or 250,
                                           fq=(q.get('fq') or 'bfq')))



def api_stock_events(q):
    """GET /api/stock/events?code=&since= —— 除权/财报/解禁/股本变动。"""
    m = _stock()
    return _stock_err(lambda: m.events(q.get('code') or '', q.get('since')))



def api_stock_peers(q):
    """GET /api/stock/peers?code=&n= —— 同申万一级行业。"""
    m = _stock()
    return _stock_err(lambda: m.peers(q.get('code') or '', n=q.get('n') or 20))



def api_stock_links(q):
    """GET /api/stock/links?code= —— 实盘持仓 / 自选 / 回测选过它。"""
    m = _stock()
    return _stock_err(lambda: m.links(q.get('code') or ''))



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
