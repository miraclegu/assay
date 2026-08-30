# QMT 策略

本地研究（assay）→ 实盘执行（迅投 QMT）的对接层。**放在仓库内**，因为它们
和本地策略一一对应：本地改了、标星换了，这里必须跟着改，放仓库外迟早失联。

```
qmt/
  strategies/     <- 粘进 QMT 的就是这些，一个标星配置一个文件
  _tpl/           <- 模板（唯一的逻辑来源）
  gen.py          <- 由模板 + PROFILES 生成 strategies/
  check.py        <- 编码 / 编译 / 与本地策略的关联 / 与模板同步
```

## 五个文件对应五个标星配置

| QMT 文件 | 形态 | 本地策略 | 归档 run_id | 年化/回撤/夏普 |
|---|---|---|---|---|
| `froec.py` | 整条移植 | `小市值/froec.py`（`kcb_688_only=0`）| `20260828-205911-486094` | 36.38 / 46.84 / 1.21 |
| `froec_traded.py` | 整条移植 | `小市值/froec_traded.py` | `20260828-205938-4926f5` | 39.50 / 47.10 / 1.28 |
| `froec_traded_stop35.py` | 整条移植 | 同上 + `stop_loss=0.35, stop_intraday=1` | `20260829-170945-4926f5` | **40.87 / 38.54 / 1.33** |
| `sgmspeg_v0b.py` | 整条移植 | `小市值/sgmspeg_v0b.py` | `20260828-205350-a76665` | 32.68 / 52.26 / 1.08 |
| `hongli_index_plus.py` | 信号执行（暂时）| `红利/红利指数增强.py`（`div_method=fiscal_year`）| `20260828-210010-adcde3` | 20.07 / 17.95 / 1.30 |

**一个配置一个文件，粘进 QMT 就能跑，不用手改任何开关。**
（早先是一个文件加开关，那样「默认参数 = 回测参数」只对其中一个成立，其余全靠人记。）

## 为什么红利暂时走信号执行

**先纠正一次误判**：早先把 `sgmspeg_v0b` 也划进「无法移植」，理由是它要
jqfactor 近似（Barra 式 5 年回归斜率）—— 那是 `sgmspeg.py`（三路本体）的
需求，**不是 v0b 的**。v0b 的 SQL 只用四样：宇宙、`floatmv`、`is_risk_warned`、
累计 `eps`，比 froec 还简单（froec 还要 PB 分位 + 5 个单季的 ROE 改善）。
已改为整条移植。

红利需要的六类数据，逐项判定：

| 需要什么 | QMT 对应 | 判定 |
|---|---|---|
| 总市值 | `close × CAPITALSTRUCTURE.total_capital` | ✅ froec 已在算 |
| PE(TTM) | 市值 / TTM 净利 | ✅ 同上 |
| 扣非ROE、营收/净利同比 | `ASHAREINCOME` + `ASHAREBALANCESHEET` | ✅ 同族，需核字段名 |
| beta_252 对沪深300 | 纯价格回归，`get_market_data_ex` 就够 | ✅ 不需要外部数据 |
| **每股分红 + 除权日** | 接口名与字段语义未核对 | ⚠️ **只剩这一项** |

所以红利**不是不能移植，是差一次核对**。`qmt/probe_dividend.py` 就是干这个的：
它把本地 `std/dividend.parquet` 的实际分红值（每 10 股、税前）嵌成 EXPECT，
逐个试候选接口，对上了才算这条路通。核对完就能把红利也改成整条移植。

在那之前，红利走信号执行 —— 选股留本地，QMT 只下单：

```bash
python3 export_signal.py strategies/红利/红利指数增强.py \
    --param div_method=fiscal_year --cash 1000000 -o signal_hongli.csv
```

导出的 CSV 带 `asof`，执行器超过 `MAX_STALE_DAYS` 天就**拒绝交易**——
忘了重跑导出时，拿着旧名单继续调仓比不交易糟得多。

## 不要手工改 strategies/ 下的文件

它们由 `gen.py` 生成。QMT 里一个策略粘一个文件、不能 import 共享模块，
所以 froec 那三份必然是三份 1200 行拷贝 —— 手工维护三份，改一处漏两处
只是时间问题。这里把重复变成**生成 + 校验**：

- 改**逻辑** → 改 `_tpl/`，然后 `python3 qmt/gen.py`
- 改**参数/新增配置** → 改 `gen.py` 的 `PROFILES`，然后重跑 gen
- 校验 → `python3 qmt/check.py`

## check.py 验什么

1. **与模板逐字节同步**（手工改过会被抓住）
2. **按各自声明的编码解码再编译** —— QMT 要 GBK。曾有文件磁盘上是 UTF-8
   却声明 `#coding:gbk`，粘进 QMT 直接 SyntaxError，而在 Mac 上打开一切正常
3. **`LOCAL_PORT` 声明的关联成立**：本地策略在不在、run_id 在不在归档、
   归档记的策略名与参数是否与声明一致、基线指标与 `stats.json` 是否吻合
4. **QMT 侧常量 == 该 profile 声明的值** —— 即「默认参数 = 回测参数」为真

任一条不成立就退出 1。selftest 里也跑这条（fast 层）。

## 编辑注意

GBK 表示不了 `⚠ ✔ ✅` 等符号，用 `[!] v [OK]` 代替。

## 上线前仍需在你的 QMT 版本上核对

不同版本接口有差异，至少确认：`get_stock_list_in_sector` 的板块名、
`get_financial_data` 的字段名（模板参数区那 4 行）、`passorder` 与
`get_trade_detail_data` 的签名。
