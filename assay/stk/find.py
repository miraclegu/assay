# -*- coding: utf-8 -*-
"""搜一只票（代码 / 名称 / 拼音都认）。"""

import datetime
import io
import json
import os
import re
import duckdb
from assay import symbols as _SYM          # 「代码->类别/名称」的唯一正本
from assay import paths as _paths   # datalake 根的唯一解析

from .base import _RE_D6, _lake, con, panel


# 搜索结果同分时的类别次序：股票 -> 指数 -> ETF。
# ★ 人在个股页搜名称，要的通常是股票；ETF 与指数是后来才支持的，
#   排在后面但**找得到**（同「合并的风险不是少两个按钮，是把功能藏起来」）。
_KIND_RANK = {'stock': 0, 'index': 1, 'etf': 2}

_IDX = {'at': None, 'rows': None}

def _index(root=None):
    """(jq_code, symbol, sec_name, 行业, 状态) 全表。

    ★ 取【最新数据日】那一天的全市场 —— 5200 行，几毫秒，缓存在进程里。
      按数据日缓存而不是按时间：同步跑完换了新数据日就自动失效，
      不用猜"缓存该放多久"。
    """
    c = con()
    p = panel(root)
    day = c.execute('SELECT max(date) FROM %s' % p).fetchone()[0]
    if _IDX['at'] == day and _IDX['rows']:
        return _IDX['rows'], day
    rows = c.execute("""
        SELECT jq_code, symbol, sec_name, sw_l1_name, public_status, is_st,
               close_bfq, change_pct, floatmv
        FROM %s WHERE date = DATE '%s'""" % (p, day)).fetchall()
    out = [{'code': r[0], 'symbol': r[1], 'name': r[2] or '', 'industry': r[3] or '',
            'status': r[4] or '', 'is_st': bool(r[5]), 'close': r[6],
            'change_pct': r[7], 'floatmv': r[8], 'kind': 'stock'} for r in rows]
    out += _alt_index(c, root)
    _IDX.update(at=day, rows=out)
    return out, day

def _alt_index(c, root=None):
    """ETF / 指数也进搜索表。**只转发**给正本（`symbols.alt_rows`）。"""
    return _SYM.alt_rows(c, _lake(root))

def search(q, limit=20, root=None):
    """按代码或名称搜。

    ★ 排序不是按字典序，而是按【匹配得分】：代码前缀命中 > 名称前缀命中 >
      名称包含 > 代码包含，同分再按流通市值降序。
      不排序的话搜"银行"第一条是随机的某只小银行，而人要的通常是大的那个。
    """
    rows, day = _index(root)
    t = str(q or '').strip().upper()
    if not t:
        return {'results': [], 'asof': str(day), 'total': len(rows)}
    d6 = _RE_D6.search(t)
    num = d6.group(0) if d6 else None
    out = []
    tl = t.lower()
    for r in rows:
        n6 = r['code'][:6]
        score = None
        # 🔴 **完整 symbol 精确命中排最前**：人打 `sh000001` 指的就是上证
        #   指数，而那 6 位数字同时是平安银行（`000001.XSHE`）—— 只按
        #   "6 位数字前缀命中"打分的话，第一条给的是平安银行，
        #   而它看着完全正常（同「代码写法各家不同」那条：形状一样、
        #   含义不同的代码，要拿**完整那一个**去比）。
        if r['symbol'] and tl == str(r['symbol']).lower():
            score = -1
        elif num and n6 == num:
            score = 0
        elif num and n6.startswith(num):
            score = 1
        elif r['name'] and r['name'].startswith(t):
            score = 2
        elif r['name'] and t in r['name']:
            score = 3
        elif t.isdigit() and t in n6:
            score = 4
        elif r['symbol'] and t.lower() in r['symbol'].lower():
            score = 5
        if score is not None:
            # 排序键三层，顺序是**试出来的**，每一层都对应一种搜不对的情形：
            #   ① 完整代码精确命中最优先 —— `sh000001` 要给上证指数，
            #      而那 6 位数字同时是平安银行。
            #   ② 其次**股票优先**：搜"银行"要的是银行股，而"银行ETF银华"
            #      是**名称前缀**命中（score 2）、"招商银行"只是包含
            #      （score 3）—— 光按 score 排，一屏全是银行 ETF
            #      （既有那条"同类匹配按市值降序"的断言当场抓到）。
            #      股票里没有匹配时（"红利ETF" / "上证指数"）自然轮到它们。
            #   ③ 最后才是 score 与市值。
            #   🔴 ④ **最后再按代码 tie-break** —— 前三个键并列时（"红利"
            #      一搜就是十几个同分的指数/ETF），谁在前取决于 duckdb 的
            #      扫描顺序，而那是**跨进程随机**的：固定 hash 种子之后
            #      连跑 5 次给出 5 个不同的结果（2026-09-21 实测）。
            #      与 froec 那条 `ORDER BY r.increase DESC` 没有 tie-break
            #      是**同一类**——只是那边不敢修（会改变全部历史归档），
            #      而这里是看盘页的展示，让它可复现没有任何代价。
            out.append((0 if score < 0 else 1,
                        _KIND_RANK.get(r.get('kind'), 9),
                        score, -(r['floatmv'] or 0), r['code'], r))
    out.sort(key=lambda x: x[:5])
    return {'results': [x[5] for x in out[:max(1, min(int(limit or 20), 50))]],
            'asof': str(day), 'total': len(rows), 'matched': len(out)}
