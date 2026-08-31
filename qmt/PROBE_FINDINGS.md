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

## ⚠️ 方法论纠正（2026-08-31）

前几轮有个系统性错误：**在猜接口名，而不是枚举**。猜不中就下结论说「没有」——
可 QMT 是成熟系统，不可能没有公告日、没有行业分类。

三处推理过头，已降级为待核实：

| 当时的结论 | 实际只能证明 |
|---|---|
| 「板块清单只能走 xtdata」 | `C.get_sector_list` 这个**名字**不存在。没做过 `dir(C)` |
| 「行业板块取不到」 | 试的 9 个**名字**不对。`get_instrumentdetail` 里有 `ProductID`/`ProductName` 从没查过 |
| 「扣非净利取不到」 | 试的 2 个**字段名**不对 |
| 「公告日拿不到」 | **只请求了 4 个业务字段，当然只返回 4 列** —— 从没把公告日当字段去请求过 |

探针已改成**先枚举再验证**：

| 节 | 做什么 |
|---|---|
| **E1** | `dir(C)` 把 ContextInfo 的方法**全列出来** ← 本该最先做 |
| **E2** | `get_instrumentdetail` 的**全量键值**（上轮只打了前 24 个就截断） |
| **E3** | 公告日显式当字段请求（`m_anntime`/`ann_dt`/…）+ `report_type` 作第 5 位置参数再试 |
| **E4** | 行业：先看 E1 列出的方法，再反查通达信风格名 |

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

## 2026-08-31 更新：补完数据后的结论

### ✅ 财务数据可用了

补充数据后，froec 需要的四个字段**全部有值**：

| 字段 | 结果 |
|---|---|
| `ASHAREBALANCESHEET.tot_shrhldr_eqy_excl_min_int` | 非空 242 |
| `ASHAREINCOME.net_profit_excl_min_int_inc` | 非空 242 |
| `CAPITALSTRUCTURE.total_capital` | 非空 242 |
| `CAPITALSTRUCTURE.circulating_capital` | 非空（F4 minor_axis 里）|
| `PERSHAREINDEX.s_fa_eps_basic` | 非空（601398 = 0.98）→ v0b 的累计 EPS 有着落 |

**表名必须全大写**：`Balance.` / `Income.` / `CapitalStructure.` / `BALANCESHEET.` 全 NaN。

调用签名：必须传 `start`/`end`，格式 `YYYYMMDD`（带横杠全 NaN、不传报 TypeError）；
`report_type` 不支持（传了返回 `None`）。多股票多字段返回 pandas `Panel`：
`items=股票 / major_axis=交易日 / minor_axis=字段`。

仍取不到：扣非净利（`net_profit_after_ded_nr_lp` / `deducted_profit` 都全 NaN）
—— **红利 B 袖的 `inc_return` 还没着落**。

### 🔴 QMT 财务数据按【报告期】前向填充 = 未来函数

实测 601398：

| 交易日 | QMT 净利 | 对应报告期 | **实际公告日** |
|---|---|---|---|
| 20241225~1230 | 2690.3 亿 | 2024Q3 | 2024-10-31 ✓ |
| **20241231** | **3658.6 亿** | **2024 年报** | **2025-03-29** ❌ |

**2024-12-31 那天就能看到 88 天后才公告的年报。** 数值本身与本地精确吻合，
错的是**时间对齐**：它按报告期切换，不按公告日。**直接按 ref_date 取值就是前视。**

移植里已修（`_attach_report_cols` 重写）：
1. 按季末识别报告期（原来直接 `report = index`，把**交易日**当报告期；
   而 `_quarter_of` 只看月份∈{3,6,9,12}，于是 `20240315` 也被当成 Q1 报告
   —— 再配上「累计值相减求单季」，产出的不是空值而是**静默的垃圾**）
2. 报告期 R 的值 = index 中第一个 ≥ R 的交易日（季末常是周末，如 2024-03-31 是周日）
3. **值跳变才算真发布** —— 否则会造出「2025Q1 净利 = 2024年报数」这种幽灵报告期
4. 公告日取法定截止日兜底（Q1/年报 4-30、中报 8-31、三季报 10-31）

⚠️ 第 4 条带来一个**已知的系统性差异**：本地用真实 `pub_date`（601398 年报
3-29 就可见），QMT 版要等到 4-30。**采纳财报比本地晚 0~32 天。**

### ✅ ST 板块名 = `沪深风险警示`

十个候选里只有它命中（206 只；本地 2025-06-30 为 175 只，探针取最新时点，量级对）。
已写进移植的候选列表首位。

### ⚠️ 行业板块用的是通达信命名，不是申万

9 种申万写法全 0 只。但 QMT 界面「热门板块」里是：农产品加工 / 酒店及餐饮 /
物流 / 光学光电子 / 造纸 / 化工新材料 / 石油矿业开 / 建筑材料 / 环保工程 /
农业服务 / 机场航运 / 视听器材 / 通信设备 / 交运设备服。

**本地量化过这个差异值多少**（froec，2016-2026）：

| | 累计 | 年化 | 回撤 |
|---|---|---|---|
| 行业过滤 开（11 个申万行业）| 2574% | 37.95% | 46.84% |
| 行业过滤 关 | 3941% | **43.64%** | 46.81% |

关掉反而高 5.69pp。但**逐年独立**检验：年均差 **+6.89pp、σ 14.62、t=+1.56、
关掉更好 7/11 年** —— 未达 5% 显著线（≈2.2）。逐年差从 −8.80pp 到 +40.74pp，
跨度极大。

**结论：行业过滤在收益上读不出信号。** 所以 QMT 上映射不出行业黑名单时，
**关掉它不构成阻塞** —— 但要明说这是与本地口径的一处差异，不是等价实现。

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

---

## 2026-08-31 第二轮：先枚举后验证，四条「取不到」全部翻案

上一轮的方法论纠正落地后，`dir(C)` 一次就把问题解决了大半。

### 定案

**`dir(C)` = 115 个成员**。此前断言「不存在」的能力，方法其实都在：
`get_industry` / `get_sector` / `create_sector` / `get_raw_financial_data` /
`get_his_st_data` / `get_float_caps` / `get_total_share` / `get_weight_in_index` /
`get_factor_data` / `get_smallcap|midcap|largecap` / `is_suspended_stock` …

**公告日一直都有** —— 之前说「拿不到」，实情是**我只请求了 4 个业务字段，所以只返回 4 列**。
把日期当字段显式请求即可，且能与业务字段同批返回：

| 字段 | 探针值 | 解释 | 本地 601398 |
|---|---|---|---|
| `ASHAREINCOME.m_timetag` | 1.703952e+12 | 报告期 **2023-12-31** | `report_date` 2023-12-31 ✔ |
| `ASHAREINCOME.m_anntime` | 1.7115552e+12 | 公告日 **2024-03-28** | `pub_date` 2024-03-28 ✔ |

毫秒时间戳，**北京时间**（用 `utcfromtimestamp` 会差一天）。
`ann_dt` / `announce_date` / `anndate` / `report_date` / `first_ann_dt` 全 NaN —— 只是名字不对。

同一批请求返回 `columns: ['net_profit_excl_min_int_inc', 'm_anntime']`，
20240102 一行就是净利 3639.9 亿 + 公告日 2024-03-28 ——
**未来函数的直接物证，也是根治它的钥匙。**

> 已改 `_tpl/froec.py`：`_attach_report_cols` 认 `m_anntime`，ROE 取数时把两个日期字段
> 一起请求。**删掉「法定披露截止日」兜底**（它原本会让 QMT 版比本地晚采纳财报 0~32 天），
> 只在个别行缺公告日时才退回。至此 QMT 版与本地 PIT 口径对齐。

**`report_type` 是第 5 个位置参数且必须是整数**：`1`/`0` 返回数据，
`'announce'`/`'report'`/`'1'`/`'0'` 一律返回 `None`。
原代码硬写 `report_type='report_time'`，一旦该形态不被接受就是**财务整片为空且不报错**。
已改成降级阶梯 `_FIN_CALLS` + 缓存首个可用形态。

**`get_instrumentdetail` 30 个键里没有行业**：`ProductID`/`ProductName`
对 601398/601088/300750 全是空字符串。但捡到 `TotalVolume`（总股本）和
`FloatVolume`（流通股本）—— 不查财务表也能算市值。

### 仍未决 → 进 `probe_all.py` 的 `OPEN`

- **G1 行业**：`get_industry` / `get_sector` 都报 `missing 1 required positional argument`
  （`'indu…'` / `'sector'`），说明要的是**行业名**不是股票代码 —— 上一轮我传了
  `601398.SH` 所以返回 `[]`，这不是「取不到」。下一轮先打 `__doc__` 再试候选名。
- **G2** `get_raw_financial_data` —— 新发现，名字像原始整表，可能一次给全字段清单
- **G3** 扣非净利（红利 B 腿 `inc_return` 依赖）—— 停止逐个猜，等 G2 的字段清单
- **G4** `get_his_st_data` —— 历史 ST，比现在用「沪深风险警示」板块（当前时点）准
- **G5** 指数权重 / 换手 / 股本 / 因子 / 交易日 / 停牌

**纪律**：G1~G5 每节都先 `_doc()` 打 `__doc__` 再调用。__doc__ 里通常直接写着参数名，
比试十个参数便宜得多。这一轮的教训就是：**能枚举的东西不要猜。**
