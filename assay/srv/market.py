"""srv/market.py —— 盘面与行业板块。"""
import re
from datetime import date

from .base import (_market, _market_err)


def api_market_overview(q):
    """GET /api/market/overview?date=&top= —— 某天的全市场。"""
    mk = _market()
    return _market_err(lambda: mk.overview(q.get('date'), top=q.get('top') or 15))



def api_sector_list(q):
    """GET /api/sector/list?kind=sw|concept|style|region|tdx_research&date="""
    mk = _market()
    return _market_err(lambda: mk.sector_list(q.get('date'),
                                              kind=(q.get('kind') or 'sw')))



def api_sector_members(q):
    """GET /api/sector/members?code=&kind=&date="""
    mk = _market()
    return _market_err(lambda: mk.sector_members(q.get('code') or '',
                                                 q.get('date'),
                                                 kind=(q.get('kind') or 'sw'),
                                                 limit=q.get('limit') or 300))



def api_sector_kinds(_q):
    """GET /api/sector/kinds —— 有哪几类板块（含每类的个数）。"""
    mk = _market()

    def _go():
        blk = mk.blocks()
        cnt = {}
        for _c, (_n, kind, _s) in blk.items():
            cnt[kind] = cnt.get(kind, 0) + 1
        out = [{'kind': 'sw', 'name': '申万一级', 'n': None,
                'note': '随面板每日同步，带 PIT'}]
        for k, nm in mk.BLOCK_TYPE.items():
            if cnt.get(k):
                out.append({'kind': k, 'name': nm, 'n': cnt[k],
                            'note': '通达信，每日同步'})
        return {'kinds': out}
    return _market_err(_go)


# ==================== 自选 ====================
