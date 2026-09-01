# finacial —— 量化研究工作区

两个仓库：`assay`（回测引擎 + 策略 + Web 看板 + QMT 移植）、`datalake`（本地数据平台）。

## 🔴 查数据接口/口径之前，先读数据字典 —— 不要重新探测

`datalake/docs/数据字典/` 四维索引，**每条都是实测定案**（有依据、有出处）：

| 索引 | 什么时候用 |
|---|---|
| `1-按需求索引.md` | 「我要股息率 / 行业 / 公告日」→ 聚宽 / QMT / 本地三源逐项对照 |
| `2-按接口索引.md` | 有函数名 → 签名、参数、返回、坑 |
| `3-按字段索引.md` | 有字段名 → 口径、单位、跨源等价物 |
| `4-按陷阱索引.md` | 「结果不对但不报错」→ 按**失效模式**倒查 |

也可以在 Web 看板里看：`python3 assay/serve.py` → 顶栏「📖 数据字典」
（服务端每次请求重读磁盘上的 md，改文件刷新即生效；新增文档在
`assay/assay/server.py` 的 `_DOCS` 加一行）。

推导原文：`assay/qmt/PROBE_FINDINGS.md`（QMT 探针四轮）、
`datalake/build/load_jq_*.py` 模块 docstring（聚宽：样本量、命中率、逐只对账）、
`datalake/docs/jqfactor-口径.md`（含**已排除的候选清单**）。

**新结论先写进代码注释 / loader docstring，再回到索引加一行。**
反过来只写索引不写代码会让两边分叉，而分叉的文档比没有文档更危险。

## 探索第三方接口的顺序（踩出来的）

1. `dir(obj)` / `__doc__` **先枚举** —— 猜不中接口名 ≠ 该能力不存在
2. 再查官方文档（迅投知识库 `dict.thinktrader.net`、聚宽 API 文档）
3. 探针只做**验证**，且带已知真值**当场对数** —— 「有值但对不上」比「没有值」更危险
4. **文档也会错**：QMT 文档称 `report_type` 默认即 `'announce_time'`，
   实测不传 == `'report_time'` == 未来函数。所以第 3 步不能省

QMT 探针只有一个文件 `assay/qmt/probe_all.py`（已定案的进 `CONFIRMED`，
未定案的进 `OPEN`）—— **不要新建探测脚本**。

## 回测纪律

- **逐年独立回测**（每年重置本金）才能比较规则；全程回测切片比对逐年差异测的是
  路径混沌不是规则 —— 零触发年份也能差 8.42pp
- 滑点**默认值**，只有对标聚宽时才设 0
- 改了引擎必须跑等价性回归（equity 曲线 SHA256）—— `ast.parse` 通过不代表逻辑没坏
- `python3 assay/selftest.py --fast`（约 20s）/ `--all`

## 每日数据同步

**唯一入口 `datalake/sync_daily.sh`**，六步：tdx2db cron → PIT 快照 →
load_tdx_kline → 交易日历 → 面板（本年增量）→ beta。跑完**直接触发实盘出信号**。
实测全程约 100 秒。幂等，中途失败重跑整条即可。

**定时用 launchd 不用 serve.py 的线程**（`_manifest/com.miraclegu.finacial.sync.plist`，
每日 18:10）。理由：`daily_snapshot.py` 是**漏一天永久丢失**的（tdx 的名称/分类/
板块成分是 type-1 覆盖写），不能挂在「看板恰好开着」上。launchd 还有个关键属性：
机器在预定时刻睡着，醒来会补跑。

**为什么跑完直接调 `live.tick()` 而不让 live 自己定时**：信号必须用最新数据。
靠两个时间常量隔开，同步一慢就错位，而错位的表现是**信号静默用了昨天的数据**。
依赖写进调用顺序比写进常量可靠 —— live 的 `tick_time` 已降级为 22:00 兜底。

**两条腿的落后语义不能混**（`build/sync_status.py` 是唯一判据，看板只显示）：
A 腿（行情）每个交易日必然有新数据，缺了就是没同步；B 腿（聚宽财务）是
**事件驱动**，没公告的日子本来就没有新 pub_date，按交易日算落后是必然误报 ——
天天标红你就不看红字了，告警失效比没告警更糟。

**别再调 `tdx2db/scripts/update.sh` 和 `full_update.sh`** —— 两者都引用不存在的
`fast_update_indicators.py`，是坏的。而且 datalake 只用 tdx.db 的
`raw_kline_daily / raw_adjust_factor / raw_basic_daily / raw_symbol_class`
四张表，技术指标那步与本链无关。

**交易日历本地就能算**：`tdx.db` 的 `raw_holidays`（休市日，1991~2030）→
交易日 = 工作日 − 休市日。这条规则与 `std/trading_calendar.parquet` 逐日对数
一致 5746 天，`build_trade_calendar.py` **每次生成都重跑对数、不一致就拒绝写出**。
所以不需要从聚宽抽日历（`grab_calendar` 留着当交叉校验）。

## 实盘模块（live）

`python3 serve.py --live` → 顶栏「💰 实盘」。默认**关闭**（与 `--allow-backtest`
同理由：它会起常驻定时线程并写盘）。

**核心原则：不重写任何交易规则。** 止损、炸板离场、调仓都埋在策略 + 引擎里，
依赖 `g.pos_state` / `g.high_limit` / `pos.entry_price` 这些内部状态。
实盘模块只做两件事：用成交流水重建 Portfolio（FIFO lots，真实成本价），
把 broker 换成 `RecordingBroker`（只记不成交），让策略跑自己的代码路径。
所以实盘提示与回测行为天然同源。

三个已经踩过的坑，改这块之前先读：

- **warmup 必须逐日重放**（`live._replay`，30 天）。`g.hold_history`（20 日
  涨停黑名单）、`g.stop_banned`（止损冷静期）、`g.pos_state`（吊灯窗口）
  都是逐日累积的。只跑一天这些全是空的 —— **不报错**，只是多买几只
  本不该买的票
- **快照是目录不是单文件**。`froec_traded.py` 加载同目录的 `froec.py`；
  只快照主文件的话，改 `froec.py` 不会改变账户版本哈希 = 版本悄悄漂移。
  版本哈希覆盖主文件 + 全部依赖
- **`live/` 入版本控制**，不要绑到 `runs/`（gitignore 的产物目录）。
  账户绑的版本是决策证据，放在会被清掉的地方等于没留痕

`live/trade_calendar.json` 现在是**临时值**（工作日推的，不含春节/国庆）。
跑一次聚宽增量抽取（`extract_jq_increment.py` 的 `grab_calendar`）会覆盖成
权威日历；在那之前信号里会带告警。

## QMT 移植

`assay/qmt/` 下 5 个策略文件由 `_tpl/` + `PROFILES` 生成：改模板后跑
`python3 gen.py` 再 `python3 check.py`（校验 GBK 编码、编译、与本地策略/归档参数一致）。
文件必须是**真 GBK**，且 `io.open(p,'wb')` 会先截断再 encode —— 一律先 encode 再写。

---

**本文件的正本在 `assay/CLAUDE.md`（受版本控制）。**
`finacial/CLAUDE.md` 是指向它的符号链接 —— 这样在 `datalake/` 下工作时也会加载。
链接本身不在任何仓库里，丢了就重建：

```sh
ln -sf assay/CLAUDE.md /Users/guhao/finacial/CLAUDE.md
```
