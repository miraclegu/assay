# -*- coding: utf-8 -*-
"""通用件：路径 / 连接 / 代码归一化 / 面板之外的标的 / 单位表。

★ 判据同 `srv/base.py`：**被 2 个以上域调用**的才进这里。"""

import datetime
import io
import json
import os
import re
import duckdb
from assay import symbols as _SYM          # 「代码->类别/名称」的唯一正本
from assay import paths as _paths   # datalake 根的唯一解析


def _lake(root=None):
    r = _paths.datalake(root)
    if not os.path.isdir(r):
        raise StockError('找不到 datalake：%s（用 ASSAY_DATALAKE 指定）' % r)
    return r

class StockError(Exception):
    pass

def panel(root=None):
    return _paths.panel_sql(_lake(root))

def con():
    return duckdb.connect(':memory:')

def _snap_path(root=None):
    """`symbol_name` 快照里 snap_date 最大的那一份。**只转发**给正本。"""
    return _SYM.name_snap(_lake(root))

def alt_kind(code, root=None, kind=None):
    """这个 code 是 ETF / 指数吗？是就返回 `(kind, symbol)`，否则 None。

    `kind`：**显式指定只要某一个池**（`'stock'` / `'etf'` / `'index'`），
      不传就是自动判。指定了而对不上时 `symbols.route` 会抛 `KindMismatch`
      —— **不回落**（回落等于"指定了却没生效"，而它不报错）。

    🔴 **聚宽口径也要认**（2026-09-21 修）。原来这里写的是"只认 tdx symbol
      形状（`sh510880`）；别的写法一律当股票走原路" —— 而**实盘账本与自选
      记的就是聚宽口径** `513120.XSHG`。于是首页点一只 ETF：
      `alt_kind` 返回 None -> 走股票面板 -> 面板里没有 ETF -> 「取不到日线」，
      而同一只票在实盘页好好的（那条路有 `to_symbol` 换算）。
      换算一直都有（`symbols.to_symbol`），**只是这里没调**。
    ★ 判类别按**快照里的 class**，不按代码前缀猜 —— 前缀规则是会变的
      （科创 688、北交 8 开头都出现过），而快照是事实。
    ★ 判据与取名统一在 `assay/symbols.py`，本函数只转发（别在这里再写一份）。
    """
    k = _SYM.route(code, root, kind)
    return (k, _SYM.as_symbol(code)) if k in _SYM.KIND_FILE else None

# ---------------------------------------------------------------- 代码归一
_PREFIX = {'60': 'XSHG', '68': 'XSHG', '90': 'XSHG',
           '00': 'XSHE', '30': 'XSHE', '20': 'XSHE'}

_TOKEN = {'SH': 'XSHG', 'SS': 'XSHG', 'XSHG': 'XSHG',
          'SZ': 'XSHE', 'XSHE': 'XSHE'}

_RE_D6 = re.compile(r'\d{6}')

def norm_code(s):
    """`601857` / `601857.SH` / `sh601857` / `601857.XSHG` -> `601857.XSHG`。

    ★ 与 live.normalize_code 同一套规则，但这里**判不出来就返回 None**
      而不是抛错 —— 搜索框里每敲一个字都会调它，半个代码不是错误。
    """
    t = str(s or '').strip().upper()
    m = _RE_D6.search(t)
    if not m:
        return None
    num = m.group(0)
    rest = (t[:m.start()] + t[m.end():]).strip(' .-_')
    mk = _TOKEN.get(rest) if rest else None
    if rest and not mk:
        return None
    mk = mk or _PREFIX.get(num[:2])
    return '%s.%s' % (num, mk) if mk else None

# ---------------------------------------------------------------- 搜索
# ★ 字段单位表：页面照它渲染，不靠"看数值大小猜"。
#   同一张面板里百分数和小数是混着的，猜错 100 倍不报错。
FIELD_UNIT = {
    'change_pct': 'pct', 'amplitude': 'pct', 'turnover': 'pct',
    'limit_pct': 'pct',
    'rev_yoy': 'ratio', 'np_yoy': 'ratio', 'np_q_yoy': 'ratio',
    'rev_q_yoy': 'ratio', 'roe_ttm': 'ratio', 'roe_q': 'ratio',
    'roe_parent': 'ratio',
    'ret_5d': 'ratio', 'ret_20d': 'ratio', 'ret_60d': 'ratio',
    'ret_250d': 'ratio',
    'floatmv': 'yuan', 'totalmv': 'yuan', 'amount': 'yuan',
    'revenue': 'yuan', 'net_profit_parent': 'yuan', 'np_ttm': 'yuan',
    'rev_ttm': 'yuan', 'np_q': 'yuan', 'rev_q': 'yuan',
    'adjusted_profit_q': 'yuan',
    'volume_shares': 'shares',
}

def _na(alt, base):
    """ETF / 指数**本来就没有**这一块 —— 明说，不要返回一个静默的空。

    🔴 空结果与"本来就没有"**必须分得出来**（同 `prune_runs` 那条：
      删了要留痕，否则"空"与"本来就没有"分不出来）。页面拿到
      `not_applicable` 就写一句"ETF / 指数没有财务数据"，
      而不是给一张空表让人以为数据没取到。
    """
    d = dict(base or {})
    d.update({'code': alt[1], 'asset_kind': alt[0], 'not_applicable': True,
              'why': '%s 没有这一项（它不是股票）'
                     % ('ETF' if alt[0] == 'etf' else '指数')})
    return d
