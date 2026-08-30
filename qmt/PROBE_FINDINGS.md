# QMT 接口核对结果（探针实测）

参考日 `2025-06-30`，探针 **`qmt/probe_all.py`**（唯一的一个，不新建）。

## 工作方式

**已确认的写进探针的 `CONFIRMED` 列表，只打印结论、不再调接口；
未确定的放 `OPEN`。每跑一轮把确认下来的从 `OPEN` 挪进 `CONFIRMED`。**

这样一个文件随时能看到「已知什么、还差什么」的全貌，文件也会随核对推进
自然变短。本 md 与探针里的 `CONFIRMED` 保持一致 —— 探针是可执行的那份。

⚠️ 不要为新问题新建探针脚本。往 `OPEN` 里加一节即可。
（之前一轮一个脚本：probe_dividend / probe_hongli_fields / probe_fin /
probe_round2 —— 结果就是每轮都要重问一遍已经确认过的东西。）

## 运行环境：完整版 QMT 交易端（**不是 miniQMT**）

这决定了两条路要分开看：

| 用途 | 走哪条 | 现状 |
|---|---|---|
| **策略移植**（五条标星） | `ContextInfo` | 行情 / 合约详情 / 分红 / 账户 / 沪深A股池 **已确认可用** |
| 批量导出数据（给 datalake） | `xtdata` + 58610 端口 | 与策略移植无关，**已从探针移除** |

### `xtdata` 不是 miniQMT 专有

报错路径 `D:\国金证券QMT交易端\bin.x64\lib\site-packages\xtquant\xtdata.py`
就在**完整版 QMT 目录里** —— `xtquant` 随 QMT 一起装，`import` 成功，六个函数都在。
报的是**连接错误不是导入错误**：`xtdata` 是独立客户端，要连数据服务端口 58610，
且调用前必须先 `xtdata.connect(ip, port)`。完整版里该接口默认不一定开。

**但策略移植用不到它。** 探针已移除全部 xtdata 相关节，现在只剩 5 节、全走
`ContextInfo`，最坏情况（接口全阻塞）24 秒跑完。

**真正卡住的只有一条**：`C.get_financial_data` 返回全 NaN。

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

### 1.5 🔴 xtdata 调用前必须先 `connect()`

`import xtquant.xtdata` **成功**，六个函数（`get_sector_list` /
`get_stock_list_in_sector` / `download_sector_data` / `get_financial_data` /
`download_financial_data` / `get_instrument_detail`）**全部存在**。

但直接调用一律抛：

```
File "D:\国金证券QMT交易端\bin.x64\lib\site-packages\xtquant\xtdata.py", line 134, in get_client
    raise Exception("无法连接行情服务!")
```

**原因是漏了 connect** —— xtdata 是个客户端，必须先连到 QMT 的数据服务端口
（默认 58610）。本仓库 `QMT/data/connection.py` 本来就是这么做的：
`xtdata.connect(ip, port=58610, remember_if_success=True)`，候选 IP
`127.0.0.1` / `192.168.0.103`。探针第一版漏了这步。

**S1~S4 四节全被这一个根因挡住**，不是板块名的问题。

连不上时的排查顺序：
1. QMT 客户端「设置 - 接口配置」开启端口 58610
2. 确认 QMT 已登录、行情已连接
3. 防火墙放行

⚠️ 若始终连不上：ST 只能走 `InstrumentName` 名称兜底，
**行业黑名单则无法实现** —— 这是相对本地回测的实质差异。

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

## 待确认 —— `qmt/probe_round2.py`（十节）

**关键线索**：本仓库 `QMT/data/st_status.py` 用的是 `xtdata.get_sector_list()`，
而探针里 `C.get_sector_list()` 报 `'_PyContext' object has no attribute` ——
说明这个函数**在 xtdata 模块上有、在 ContextInfo 上没有**。
所以 ST 与行业板块返回 0 很可能只是**名字猜错了**，而不是没有。
第二轮先把真实板块名**全部列出来**，不再猜。

| 节 | 问什么 |
|---|---|
| S0 | 策略环境里能不能 `import xtquant.xtdata` |
| S1 | `xtdata.get_sector_list()` 真实板块名全量（含 ST / 申万 / 行业） |
| S2 | ST 板块对账（本地同日 175 只，含 000004/000070/000430...） |
| S3 | 行业板块对账（本地 银行42 / 煤炭38 / 家用电器132 / 电子599 / 电气设备453） |
| S4 | `get_stock_list_in_sector(name, real_timetag)` 取**历史时点**成分 |
| F1 | 财务数据下没下载（直接调 `download_financial_data` 试） |
| F2 | 表名/字段名（9 个变体） |
| F3 | 调用签名（report_type / 日期格式 / 不传日期） |
| F4 | Panel 里到底有没有值（上一轮只打了结构没打值） |
| F5 | 退路：财务始终取不到时各策略的处境 |

⚠️ S4 那条很要紧：**板块成分随时间变，回测必须取时点成分而不是最新成分**。
若 `real_timetag` 不支持，用最新 ST 名单去过滤 2016 年的历史就是未来函数。
