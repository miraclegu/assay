# -*- coding: utf-8 -*-
"""指标：解析 `sub=macd,kdj(19,3,3)` 并算出来。

★ 公式不在这里 —— 唯一正本是 `assay/indicators.py`。"""

import datetime
import io
import json
import os
import re
import duckdb
from assay import symbols as _SYM          # 「代码->类别/名称」的唯一正本
from assay import paths as _paths   # datalake 根的唯一解析

from .base import StockError
from .quote import kline


# ================================ 指标 ================================
# 🔴 **算法与定义全在 `assay/indicators.py`（唯一正本）** —— 这里只管
#   "取多少根、预热多少、平铺成行"。原来 MACD/KDJ/RSI/BOLL 的公式写死在
#   这个文件里，而前端 kchart.js 又硬编码了一份"画哪几条线"，于是加一个
#   指标要改三处、漏了画的那处**不报错**（选了它副图一片空白）。
def parse_inds(spec_str):
    """把 URL 上的 `inds=` 解析成 [{'id','params'}]。

    两种写法都认：`macd,kdj`（默认参数）与 JSON
    `[{"id":"kdj","params":{"n":9}}]`（要改参数时用）。
    ★ 认两种是因为**大多数时候不改参数**，而让人在 URL 里手写一段 JSON
      才能换个副图，等于把这个功能藏起来。
    """
    import json
    t = (spec_str or '').strip()
    if not t:
        return None
    if t[0] == '[':
        try:
            arr = json.loads(t)
        except Exception as e:                              # noqa: BLE001
            raise StockError('inds 不是合法 JSON：%s' % e)
        out = []
        for x in arr:
            if isinstance(x, str):
                out.append({'id': x, 'params': None})
            else:
                out.append({'id': x.get('id'), 'params': x.get('params')})
        return out
    return [{'id': x.strip(), 'params': None} for x in t.split(',') if x.strip()]

# 🔴 预热有个【下限】而不只是"按需要取"：RSI/ATR 这类 Wilder 递推
#   **永远记着起点**（多喂 24 根，末尾的值就会差一点）。下限钉在 120
#   是为了让默认那几个指标与改造前**逐位一致** —— 少喂一点不报错，
#   只是页面上的 RSI 悄悄变了几厘。
IND_WARM = 120

IND_DEFAULT = ['macd', 'kdj', 'rsi', 'boll']

def indicators(code, n=250, fq=None, end=None, off=0, root=None, inds=None):
    """按需算一组指标。定义全在 `assay/indicators.py`（唯一正本）。

    `inds` 不给时算 `IND_DEFAULT`（MACD/KDJ/RSI/BOLL）——
    那是改造前的行为，个股页与浮层的老链接照旧能用。

    返回 `rows` 是**一行一天、所有指标的键平铺在一起**（`dif`/`k`/`rsi6`…），
    与改造前同形；`panels` 是每个指标画哪几条线、什么样式、什么颜色，
    **页面照它渲染，不认识任何指标名**。
    """
    from assay import indicators as _I
    # ★ n 可能是 URL 里来的字符串 —— 在【入口】转 int，不要指望调用方转。
    n = max(10, min(int(n or 250), 3000))
    want = inds if inds is not None else [{'id': x, 'params': None}
                                          for x in IND_DEFAULT]
    if isinstance(want, str):
        want = parse_inds(want)
    specs = []
    try:
        for w in (want or []):
            sp = _I.spec(w.get('id'))
            specs.append((sp, sp.merge(w.get('params'))))
    except _I.IndError as e:
        raise StockError(str(e))
    warm = max([IND_WARM] + [sp.warm(p) for sp, p in specs])
    # 🔴 `off` 必须一路传下去 —— 不传的话翻页后副图画的还是最新那一段，
    #   与主图**上下对不上**，而它不报错，看着像"指标和 K 线不同步"。
    k = kline(code, n=n + warm, fq=fq, end=end, off=off, root=root)
    fq = k['fq']        # 服务端解析后的真实口径（不给时按类别定）
    bars = k['bars']
    m = len(bars)
    out = [{'date': b['date'], 'close': b['close']} for b in bars]
    panels = []
    for sp, p in specs:
        try:
            col = sp.calc(bars, p)
        except _I.IndError as e:
            raise StockError(str(e))
        for key, vals in col.items():
            for i in range(m):
                out[i][key] = vals[i]
        panels.append({'id': sp.id, 'label': sp.label, 'short': sp.short,
                       'panel': sp.panel,
                       'unit': sp.unit, 'desc': sp.desc,
                       'params': p, 'series': sp.series(p)})
    return {'code': k['code'], 'fq': fq, 'n': min(n, m),
            'warmup_dropped': max(0, m - n), 'rows': out[-n:],
            'panels': panels,
            # ★ 老字段留着：改造前的调用方（浮层/用例）读的是它
            'params': dict((sp.id, p) for sp, p in specs)}
