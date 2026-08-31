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
