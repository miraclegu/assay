# QMT 接口核对结果（探针实测）

参考日 `2025-06-30`，探针 `qmt/probe_all.py`，跑于 2026-08-30。
**本文件是结论存档** —— 日志会丢，结论不能丢。

## ✅ 确认可用

| 接口 | 结论 |
|---|---|
| `C.get_market_data_ex(fields, codes, period='1d', end_time, count, dividend_type, fill_data)` | 完全可靠。字段 `open/close/high/low/volume/amount/preClose` 全有；`dividend_type='front'` 可用。601398/601088 的 OHLC+成交额与本地**逐项精确吻合** |
| `C.get_instrumentdetail(code)` | 返回 dict。可用键：`OpenDate`(上市日，5/5 对)、`InstrumentName`、`FloatVolume`/`FloatVolumn`(流通股本，两个拼写都有)、`PreClose`、`ExpireDate`、`IsTrading`、`InstrumentStatus` |
| `C.get_divid_factors(code)` | 只接受 1~2 个参数。`{毫秒时间戳: [7个数]}`，key = 除权日**北京时间 00:00**（`utcfromtimestamp` 会差一天），`[0]` = 每股税前现金分红（元/股），与本地 `bonus_ratio_rmb/10` **11/11 精确吻合**；`[3][4]` = 配股比例/配股价；`[6]` = 复权因子 |
| `000300.SH` 日线 | 可取（`399300.SZ` 返回空）。**beta 自己回归即可，不需要外部数据** |
| `get_trade_detail_data('', 'STOCK', 'ACCOUNT')` | **空账号也能读到 1 条**（回测用默认账户）。字段 `m_dBalance/m_dAvailable/m_dAssetBalance/...` 齐全 |
| `passorder` | 全局可见 |
| `C.get_stock_list_in_sector('沪深A股')` | 5216 只（本地同日在市 5152，量级对）。`沪深京A股` 5555、`沪深300` 300 |

## ❌ 不可用 / 需绕开

### 1. 🔴 `get_financial_data` 返回全 NaN

froec 在用的四个字段（`tot_shrhldr_eqy_excl_min_int` / `net_profit_excl_min_int_inc` /
`total_capital` / `circulating_capital`）单独调全 NaN；多字段调返回 Panel
（2 items × 242 major × 4 minor，**major_axis 是交易日** 20240102~20241231），值仍是 NaN。

扣非净利的 5 个候选、增长率的 4 个候选、市值/PE 的 4 个候选 —— **全部 NaN**。

**影响**：froec / froec_traded / froec_traded_stop35 三条全靠它算 PB 与单季 ROE，
取不到值就跑不起来。**这是当前唯一的硬阻塞。**

最可能的原因：**QMT 客户端未下载财务数据**（行情是单独一块，回测能跑不代表财务有）。
见 `qmt/probe_fin.py` —— 它专门区分「没下载 / 字段名不对 / 调用方式不对」。

### 2. ST 板块名全部取不到

`ST板块` / `ST` / `风险警示` / `*ST` / `ST股票` 全返回 0 只。
`C.get_sector_list()` 不存在（`'_PyContext' object has no attribute`）。

**绕开**：`get_instrumentdetail(code)['InstrumentName']` 含 `ST` 即判定。
移植里 `filter_st_stock` 本来就有这条兜底路径，把它变成主路径即可。

### 3. 行业板块名全部取不到

`银行` / `煤炭` / `家用电器` / `申万一级行业` / `证监会行业` 全 0 只。

**影响**：froec 的行业黑名单（钢铁/煤炭/石油石化/银行/非银金融/交运/传媒/环保 等 11 个）
失效，这是相对本地回测的**实质差异**，不是可以忽略的细节。
本地测过：行业过滤开关对结果有影响。上线前必须解决或显式接受这个差异。

### 4. ⚠️ `UpStopPrice` / `DownStopPrice` 是【实时值】不是历史值

探针跑于 2026-08-30，参考日是 2025-06-30，结果：

| 代码 | QMT 返回 | 本地 2025-06-30 实际 |
|---|---|---|
| 601398.SH | 8.60 / 7.04 | **8.25 / 6.75** |
| 000651.SZ | 43.20 / 35.34 | **49.74 / 40.70** |
| 300750.SZ | 447.60 / 298.40 | **301.19 / 200.79** |

**绝对不能用它做历史涨跌停判断**，必须自己按 `preClose × (1 ± 涨跌幅)` 算
（移植里 `_limit_price` 本来就是这么做的，正确，不用改）。

## 待确认

见 `qmt/probe_fin.py`：财务数据到底是没下载、字段名不对、还是调用方式不对。
