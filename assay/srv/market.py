"""srv/market.py —— 盘面与行业板块。"""
import re
from datetime import date

from .base import (_market, _market_err)


def api_market_overview(q):
    """GET /api/market/overview?date=&top= —— 某天的全市场。"""
    mk = _market()
    return _market_err(lambda: mk.overview(q.get('date'), top=q.get('top') or 15))



def api_sector_list(q):
    """GET /api/sector/list?kind=&date=&parent=

    `kind`：`sw` 申万一级 / `sw_l2` 二级 / `sw_l3` 三级 /
            `concept` / `style` / `region` / `tdx_research`
    `parent`：下钻用 —— `sw_l2` 传一级 code、`sw_l3` 传二级 code。
      不传就是**全量**（175 个二级 / 449 个三级），也允许。
    """
    mk = _market()
    return _market_err(lambda: mk.sector_list(q.get('date'),
                                              kind=(q.get('kind') or 'sw'),
                                              parent=q.get('parent') or None))



def api_sector_members(q):
    """GET /api/sector/members?code=&kind=&date="""
    mk = _market()
    return _market_err(lambda: mk.sector_members(q.get('code') or '',
                                                 q.get('date'),
                                                 kind=(q.get('kind') or 'sw'),
                                                 limit=q.get('limit') or 2000))



def api_sector_kinds(_q):
    """GET /api/sector/kinds —— 有哪几类板块（含每类的个数）。"""
    mk = _market()

    def _go():
        # 🔴 通达信板块库被每日同步占着时**不拦**：申万那一类照样给。
        #   但缺的四类要**说出来** —— 不说的话页面上只剩一个「申万一级」，
        #   看着像本来就只有这一类（未知不是缺）。
        locked = ''
        try:
            blk = mk.blocks()
        except mk.BlocksLocked as e:
            blk, locked = {}, str(e)
        cnt = {}
        for _c, (_n, kind, _s) in blk.items():
            cnt[kind] = cnt.get(kind, 0) + 1
        # ★ 申万一级的个数也给出来 —— 原来是 None，页签上就只有名字，
        #   而旁边几类都带数字，看着像"这一类查不到"。
        n_sw = len(mk.sector_list(None, kind='sw').get('rows') or [])
        out = [{'kind': 'sw', 'name': '申万一级', 'n': n_sw,
                'note': '随面板每日同步，带 PIT；可逐级下钻到二级/三级'}]
        for k, nm in mk.BLOCK_TYPE.items():
            if cnt.get(k):
                out.append({'kind': k, 'name': nm, 'n': cnt[k],
                            'note': '通达信，每日同步'})
        return {'kinds': out, 'blocks_error': locked} if locked \
            else {'kinds': out}
    return _market_err(_go)


# ==================== 自选 ====================
