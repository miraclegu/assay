# -*- coding: utf-8 -*-
"""仓库根与 datalake 根的【唯一】解析。

🔴 **为什么值得单独一个模块**：这条规则此前在 **8 处**各写了一遍
  （`feed.PanelFeed` / `run.default_lake` / `srv.base._datalake_dir` /
  `stock._lake` / `market._root` / `realtime._lake` / `lv.px._lake` /
  `symbols.default_root`），而每一处都**自己数 `dirname` 层数** ——
  层数是跟着「这个文件放在哪」变的。`lv/px.py` 里那句注释就是证据：

      🔴 `__file__` 在 `lv/` 里比原来深一层，所以要多剥一层 dirname

  也就是说**搬一次文件就要改一次**，而改漏了**不报错** —— 只是那个模块
  从此解到一个不存在的路径。拆 `srv/` 时实测踩过：`/api/marks` 返回 `{}`
  （标记全丢）、7 个实盘接口 500。
  这里只数一次，而 `paths.py` 自己的位置是固定的。

★ **不做存在性检查、不抛异常** —— 各调用方的异常类型与措辞是它们自己的
  UX（CLI 要 `SystemExit`、HTTP 要 `LiveError`、看盘要 `StockError`），
  混成一个反而让报错指不到地方（同「报错必须指向真正的原因」那条）。
  所以这里只回答"路径是哪个"，"在不在、怎么说"留给调用方。

★ 依赖为零（只 `import os`）—— 它被 `run.py` / `srv/` / `feed` 都用到，
  引进任何东西都可能兜出循环。
"""
import os

# assay/assay/paths.py -> assay/assay -> assay（仓库根）
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def datalake(root=None):
    """datalake 根目录（**不保证存在**）。

    优先级：显式传的 > `ASSAY_DATALAKE` > 仓库同级的 `../datalake`。
    """
    return os.path.normpath(
        root or os.environ.get('ASSAY_DATALAKE')
        or os.path.join(os.path.dirname(REPO), 'datalake'))


# 面板文件的相对 glob。🔴 `feed.FINGERPRINT_PARTS` 盯的必须是**同一批**
#   文件 —— 指纹盯的比实际读的少一个，就会出现「数据变了而指纹没变」，
#   于是 tick 判「不用重算」、模拟盘判「不用推进」，**两个都不报错**。
PANEL_GLOB = 'mart/panel_daily/panel_*.parquet'


def panel_sql(root=None):
    """面板宽表的 duckdb 读法（一段 `read_parquet(...)`）——**唯一**一处。

    🔴 此前 **7 处**各拼一遍这个字符串，而 `lv/intraday.py` 拼的 glob
      **是另一个**（`panel_daily/**/*.parquet` 而不是 `panel_*.parquet`）。
      今天两者恰好是同一批 24 个文件、同 1631 万行（量过），所以看不出来 ——
      但 `**` 会把 `panel_daily/` 下**任何**子目录里的 parquet 也读进来，
      哪天那里多出一个中间产物，它读到的就是另一张表，**而它不报错**。
    ★ 以 `feed.PanelFeed` 为准（它才是引擎那条路的权威），即 `panel_*.parquet`。
    ★ `root` 可以是已经解析好的绝对路径（`datalake()` 对它是幂等的），
      也可以不传 —— 归档回放那条路要按 run 自己的 lake 取。
    """
    return "read_parquet('%s/%s')" % (datalake(root), PANEL_GLOB)


# 因子值面板（`datalake/build/build_factor_daily.py` 的产物，按年分片的宽表：
# `jq_code` / `date` + 162 个因子列）与它的目录表（`build_factor_catalog.py`）。
#
# 🔴 **与 `PANEL_GLOB` 同一条纪律**：`feed.FINGERPRINT_PARTS` 盯的必须与实际
#   读的是**同一批**文件 —— 少盯一个就会出现「数据变了而指纹没变」，于是
#   tick 判「不用重算」、模拟盘判「不用推进」、归档去重把两次不同数据的回测
#   当成重复删掉一个，**三个都不报错**。所以两边取同一个常量。
# ★ 立这个常量的直接理由：`assay/factor_eval.py` 已经内联拼了一份
#   （连 `panel_daily/panel_*.parquet` 也自己拼了一遍，而它的注释写着
#   「路径只在这一处拼」）—— 那正是"两处实现必然分叉"的现场。
FACTOR_GLOB = 'mart/factor_daily/factor_*.parquet'
FACTOR_CATALOG = 'mart/factor_catalog.parquet'


def factor_sql(root=None):
    """因子面板宽表的 duckdb 读法 —— **唯一**一处（同 `panel_sql`）。"""
    return "read_parquet('%s/%s')" % (datalake(root), FACTOR_GLOB)


def factor_catalog_sql(root=None):
    """因子目录表的 duckdb 读法（一个因子一行：单位 / 横截面可不可比 / 预热）。"""
    return "read_parquet('%s/%s')" % (datalake(root), FACTOR_CATALOG)


# tdx 原始日线按类别分文件。🔴 这是**路径表**，不是"要兜哪几类"的策略 ——
#   两者此前混在一起：`lv/perf._KIND_FILE`（4 类）与 `symbols.KIND_FILE`
#   （2 类）各写一份，而后者被 `stock.alt_kind` 当策略用
#   （「只兜 ETF 与指数，不兜股票」）。把 stock 并进去就会让股票也走回落，
#   **而它不报错**，只是 1600 万行的表被无谓地扫。所以路径表在这里、
#   策略留在 `symbols.ALT_KINDS`。
TDX_KLINE = {'index': 'index_*', 'etf': 'etf_*',
             'stock': 'stock_*', 'block': 'block_*'}


def tdx_kline_glob(kind, root=None):
    """tdx 日线文件的 glob。**路径只在这里拼一次** —— SQL 读法与
    "这些文件现在有多新"（`symbols._kind_map` 的缓存键要用）都走它。
    """
    pat = TDX_KLINE.get(kind, kind)
    return '%s/raw/tdx/kline/%s.parquet' % (datalake(root), pat)


def tdx_kline_sql(kind, root=None):
    """tdx 日线的 duckdb 读法。`kind` 是类别名或直接给 glob（`etf_*`）。"""
    return "read_parquet('%s')" % tdx_kline_glob(kind, root)


# 公司行动（除权除息 / 送转 / 配股）。由 `datalake/build/load_tdx_gbbq.py`
# 从 tdx 的 `raw_gbbq` 导出（`sync_daily.sh` 10/12）。
# 🔴 **assay 侧一律读这份 parquet，不连 tdx.db** —— 那个库会被 cron 持有写锁。
GBBQ_REL = 'raw/tdx/gbbq.parquet'


def tdx_gbbq_sql(root=None):
    """公司行动表的 duckdb 读法。与因子表**成对**：因子是"价格缩了多少"，
    gbbq 是"为什么缩" —— 前者用来复权，后者用来给实盘账本调成本与股数。"""
    return "read_parquet('%s/%s')" % (datalake(root), GBBQ_REL)


def tdx_factor_sql(root=None):
    """复权因子表。★ 与日线**成对**出现（后复权 = close × coalesce(f,1)），
    所以放一起 —— 分开两处写，下次加一类就会漏掉其中一处。"""
    return "read_parquet('%s/raw/tdx/adjust_factor.parquet')" % datalake(root)
