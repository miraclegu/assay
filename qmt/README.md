# QMT 策略

本地研究（assay）→ 实盘执行（迅投 QMT）的对接层。**放在仓库内**，因为它们
和本地策略是一一对应的：本地改了、标星换了，这里必须跟着改，放在仓库外
迟早失联。

| 文件 | 形态 | 对应本地 |
|---|---|---|
| `froec.py` | 整条移植 | `strategies/小市值/froec.py` / `froec_traded.py`（3 个 profile）|
| `signal_executor.py` | 只执行本地信号 | `export_signal.py` 导出的 CSV（红利 / sgmspeg_v0b）|

## 为什么分两种形态

**froec 线整条移植**：因子简单（PB 分位 + 单季 ROE 改善 + 流通市值），
QMT 的 `get_financial_data` 够用，能自包含。

**红利 / sgmspeg_v0b 走信号导出**：红利要分红历史 + 252 日 beta 回归，
v0b 要 jqfactor 近似（Barra 式 5 年回归斜率）。把这些搬到 QMT，等于用一套
没核对过的字段名和 as-of 语义重造因子链 —— 而本仓库的全部结论都建立在
已验证的「双键 as-of（pub_date + change_date）」口径上。重造一遍最可能的
结果是**看着能跑、数字悄悄是错的**。所以选股留在本地，QMT 只做执行：

```bash
python3 export_signal.py strategies/红利/红利指数增强.py \
    --param div_method=fiscal_year --cash 1000000 -o signal.csv
# 把 signal.csv 拷到 Windows，改 signal_executor.py 的 SIGNAL_PATH
```

信号带 `asof`，执行器超过 `MAX_STALE_DAYS` 天就**拒绝交易**——忘了重跑导出
时，拿着旧名单继续调仓比不交易糟得多。

## 关联是可校验的，不是注释

每个文件顶部有 `LOCAL_PORT` 声明。`python3 qmt/check.py` 会核对：

- 声明的本地策略文件存在
- 声明的 `run_id` 在归档里，且归档记的策略名、参数与声明一致
- 声明的基线指标（年化/回撤/夏普）与 `stats.json` 吻合（容差 0.015）
- **QMT 侧常量等于默认 profile 声明的值** —— 即「默认参数 = 回测参数」

任一条不成立就报错退出 1。selftest 里也跑这条。

## 编码

QMT 内置编辑器要 **GBK**，文件头 `#coding:gbk`，磁盘字节也必须真是 GBK。
`check.py` 按各文件声明的编码解码再编译 —— 曾经有文件磁盘上是 UTF-8 却
声明 gbk，粘进 QMT 直接 SyntaxError，而在 Mac 上打开一切正常，肉眼看不出来。

⚠️ 编辑这些文件时注意：GBK 表示不了 `⚠ ✔ ✅` 等符号，用 `[!] v [OK]` 代替。

## 上线前仍需在你的 QMT 版本上核对

不同 QMT 版本接口有差异，至少确认这几处：`get_stock_list_in_sector` 的板块名、
`get_financial_data` 的字段名（`froec.py` 参数区那 4 行）、`passorder` 与
`get_trade_detail_data` 的签名。
