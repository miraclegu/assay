"""个股查询：搜索 / 基本面板 / K 线 / 财务时序。

看板「📈 个股」那一页的数据层。**只读**，全部来自 `mart/panel_daily`。

## 为什么不复用 live.py 里的取价函数

那些是给实盘算钱用的（`day_price` 拿不到就报错、`positions_valued` 要
FIFO 批次）。这里是【浏览】：拿不到就说"没有"，不该拦住整页。
两种语义混在一个函数里迟早互相拖累。

## 复权

面板只存不复权 OHLC + `close_hfq` + `hfq_factor`。实测
`close_bfq × hfq_factor == close_hfq`（差 <0.004，四舍五入），
所以后复权 OHLC 用 `× hfq_factor` 推得出来。

★ 默认给**不复权**：看盘的人对着券商软件看的就是它，而且
  `open` 正好是当日集合竞价成交价。
★ 但**跨除权日比较涨幅必须用后复权** —— 不复权在除权日会有个假跌幅。
  所以两种都给，页面上能切，并且把这条写在提示里。

## 单位（实测核过，别猜）

    change_pct   百分数    -1.49 = 跌 1.49%
    amplitude    百分数    3.5   = 振幅 3.5%
    turnover     百分数    0.088342 = 换手 0.0883%
                          （= 量×收盘价/流通市值×100，与字段精确吻合；
                            全市场中位数 2.13% —— 若当小数用会差 100 倍）
    volume_shares 股        聚宽的 volume 是"股×100"，面板这一列已换成股
    amount        元
    floatmv/totalmv 元
    roe_ttm / rev_yoy 等比率  **小数**（0.10 = 10%）

★ 同一张面板里"百分数"和"小数"是**混着**的 —— 所以 `FIELD_UNIT` 明确列出
  每个字段的单位，页面照它渲染，不靠"看数值大小猜"。猜错 100 倍不报错。
"""

# 🔴 **对外契约仍然是 `assay.stock`** —— `srv/stock.py` / alerts /
#   watchlist / realtime / 用例都按这些名字取，一个都不许少。
#   实现按产品域拆进了 `assay/stk/`（依赖单向：域 -> base，ind -> quote）。
# ★ 这里是**普通 re-export**，不是 ModuleType 子类 —— 查过了，
#   外部没有任何一处【写】`stock.X`（那才是 `lv.LIVE` 必须转发的理由）。

from .stk.base import (  # noqa: F401
    FIELD_UNIT,
    StockError,
    _PREFIX,
    _RE_D6,
    _TOKEN,
    _lake,
    _na,
    _snap_path,
    alt_kind,
    con,
    norm_code,
    panel,
)
from .stk.find import (  # noqa: F401
    _IDX,
    _KIND_RANK,
    _alt_index,
    _index,
    search,
)
from .stk.quote import (  # noqa: F401
    _ALT_PROFILE_NULL,
    _INDEX_TAG,
    _PROFILE_COLS,
    _alt_name,
    _alt_profile,
    _ma,
    _r3,
    alt_panel,
    finance,
    kline,
    profile,
)
from .stk.ind import (  # noqa: F401
    IND_DEFAULT,
    IND_WARM,
    indicators,
    parse_inds,
)
from .stk.ctx import (  # noqa: F401
    _EVENT_KINDS,
    _names_map,
    _wan,
    compare,
    events,
    peers,
)
from .stk.link import (  # noqa: F401
    _runs_with,
    links,
)
