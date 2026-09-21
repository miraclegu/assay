# -*- coding: utf-8 -*-
"""lv/tdx.py —— **转发门面**，实现已搬到 `assay/symbols.py`。

🔴 **为什么搬走**（2026-09-21）：这一层是「面板之外的标的」这个**通用**能力，
  却落在**实盘域**里。于是 `stock.py` / `watchlist.py` / `alerts.py` 想用它
  得跨域 import —— 结果它们各自又写了一份，五处实现、三种行为：

      stock.kline('513120.XSHG')  取不到（它只认 tdx symbol，而账本给聚宽口径）
      watchlist.valued()          只查股票面板 -> ETF 没名字 -> 页面显示成代码
      stock._alt_name             没去 U+FFFD -> 同一只票在两页两个名字

  正本现在是 `assay/symbols.py`。**本文件只保留转发**，这样
  `lv/px.py` / `lv/sig.py` / `lv/perf.py` / `lv/hist.py` 里的 import 一个都不用改
  （同「`.html` 一律留在根目录」那条：已有的调用地址是契约）。

★ 不要往这里加新实现。要加就加在 `assay/symbols.py`，这里只 re-export。
"""
from assay.symbols import (            # noqa: F401
    KIND_FILE,
    to_symbol,
    name_snap,
    last_close,
    day_ohlc,
    daily_close,
)
from assay import symbols as _S


def names(root, codes):
    """{聚宽代码: 名称}（只查 ETF/指数名称快照）。

    ★ 正本叫 `symbols.alt_names` —— `names` 那个名字在正本里让给了
      **统一入口**（面板优先 + 本函数回落）。这里保留旧名旧签名不变。
    """
    return _S.alt_names(root, codes)


def _files(root):
    """内部用，`lv/perf.py` 之外没人调。"""
    return _S._files(root)
