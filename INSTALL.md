# 安装指南（macOS / Windows）

假设你**什么都没装过**，刚把这两个仓库下下来。照着从上往下做即可。

```
随便一个目录/            <- 两个仓库必须是【同级】，名字不能改
  assay/                 回测引擎 + 看板（你平时跑的是这个）
  datalake/              数据平台（assay 从这里取数，它自己不依赖 assay）
```

> 目录名与层级是**约定**：`assay` 默认在 `../datalake` 找数据。
> 放别处的话要设环境变量 `ASSAY_DATALAKE=/绝对/路径/到/datalake`。

---

## 0. 先知道这三件事（省得装到一半才发现）

| | |
|---|---|
| **要多少磁盘** | 全部建完约 **8 GB**。其中一次性要下一个 **548 MB** 的通达信全量日线包 |
| **要多久** | 装环境 5 分钟；**建数据约 2 小时**，一次性。分布（各阶段自己声明的估计）：下全量日线 30~60 分钟 · 规范层 2 分钟 · 面板 6 分钟 · 因子面板 30 分钟 · 因子评价 44 分钟 |
| **有一步必须人工** | 财务数据（利润表/资产负债表/分红…）来自**聚宽**，要你自己有聚宽账号、在它的研究环境里跑一段代码再把包传回来。**不做这一步也能用**：行情、K 线、盘面、看板、ETF 策略都不依赖它；只有用到财务字段的策略（比如小市值那套要 PB/ROE）跑不了 |

🔴 **Intel 芯片的 Mac 装不了现成的 tdx2db**：上游只发布 `Darwin_arm64`（Apple
芯片）/ `Linux_arm64` / `Linux_x86_64` / `Windows_x86_64` 四个包，**没有
`Darwin_x86_64`**。Intel Mac 需要自己用 Go 编译（见文末）。装配脚本遇到这种
情况会**直接报错**，不会给你装一个跑不了的包。

---

## 0.5 想省事：一条命令把【环境】装好

下面第 1、2 节是**手工步骤**，一步一步写清楚了。嫌麻烦的话有个脚本替你做完
那几步（检查 Python 版本 / 建虚拟环境 / 装依赖 / **复查**），然后告诉你下一步：

```bash
# macOS / Linux
cd assay && python3 install.py

:: Windows —— 双击 assay\install.bat，或在终端里：
cd assay
install.bat
```

加 `--check` 只报不动手，一个字节都不改。

★ **它只管【环境】，不管【数据】。** 建数据（tdx2db、全量日线、面板、因子…）
在看板上有一个按钮跑到底 —— 见 1.4 / 2.4。两个入口做成一个的话，
迟早一个能用一个不能。
★ 它**不装 tdx2db、也不装定时任务**：后者是往系统里写东西（launchd /
schtasks），不该在你没表态时替你做。看板顶上那条横条里有入口。
★ 手工那几节**不会因此作废** —— 某一步单独失败时要能照着重来。

---

## 1. macOS

### 1.1 Python

要 **Python 3.10 或更高**（代码里用到 `sys.orig_argv`，那是 3.10 才有的）。

```bash
python3 --version          # 看一下现在是多少
```

不够新就装一个（二选一）：

```bash
brew install python@3.12                     # 有 Homebrew 的话
# 没有 Homebrew 就去 https://www.python.org/downloads/macos/ 下安装包
```

### 1.2 建一个虚拟环境，装 4 个包

```bash
cd 随便一个目录            # 就是上面那个放着 assay/ 和 datalake/ 的目录
python3 -m venv .venv
source .venv/bin/activate

pip install -r datalake/requirements.txt
```

**只要这 4 个**（duckdb / pandas / numpy / pyarrow）。
🔴 **包名只在 `datalake/requirements.txt` 一处列** —— 这里再抄一遍的话，加一个依赖时改漏一处，新机器上就是一句裸 `ModuleNotFoundError`。
这个清单是扫两个仓库的全部 `import` 得出的（不是照某个
现成的 venv 抄——那里面还有一堆别的项目留下的包），并且**实测过**：
拿一个只装了这 4 个包的干净 venv 起了一次看板，首页与
`/api/setup`、`/api/progress`、`/api/sync` 都正常。

想跑自检（可选）的话再加一个：

```bash
pip install playwright && playwright install chromium
```

### 1.3 装 tdx2db（抓行情的程序）

```bash
cd datalake
python3 setup_tdx.py --install
```

它会按你的系统自动挑对应的包、装完验一遍。

🔴 **不要 `pip install tdx2db`** —— PyPI 上那个是**同名的另一个项目**，
不支持 DuckDB。装上去会"看着成功"，然后参数全对不上。

### 1.4 起看板，点一个按钮把数据建起来

```bash
cd ../assay
python3 serve.py
```

浏览器打开 **http://127.0.0.1:8770**

本地还没有数据时，**页面最上面会出现一条横条**：

```
📦 本地还没有数据 · 还差 6 步 · 约需 …（估） · 之后每天自动增量同步，不用再点
                                            [ ▷ 开始建本地数据 ]
```

点它。剩下的交给它跑（几十分钟），横条上一直显示**第几步 / 第几步、已用多久、
还要多久**。这期间你可以关掉这一页去干别的——横条在**任何页面**都看得到。

> 中途失败它会停下来并告诉你是哪一步（后面每步都吃前一步的产物，硬往下跑
> 会把坏数据传播开）。修好之后**再点一次**，它从没完成的那一步接着跑。
> 也可以去「🔄 数据」页看七个阶段的清单、单独重跑某一步。

### 1.5 让它每天自己更新

```bash
cd ../datalake
python3 setup_tdx.py --install-timer
```

装两个 launchd 定时：**数据同步**（16:00~20:00 每 10 分钟问一次"今天的数据
齐了没"，齐了就秒退）和**信号重算**（16:00~次日 09:20 每小时）。

```bash
python3 setup_tdx.py --schedule      # 看实际装上了哪些时间点
```

---

## 2. Windows

思路一样，差别在**四个地方**，下面逐条标了。

### 2.1 Python

去 https://www.python.org/downloads/windows/ 下 **3.10 以上**，安装时
**务必勾上 `Add python.exe to PATH`**。

🔴 **Windows 上没有 `python3` 这个命令**，只有 `python`。下面所有命令
因此用 `python`；你看到别处文档写 `python3` 的地方，在 Windows 上都换成
`python`。

```powershell
python --version
```

### 2.2 虚拟环境 + 4 个包（PowerShell）

```powershell
cd 随便一个目录
python -m venv .venv
.\.venv\Scripts\Activate.ps1

pip install -r datalake/requirements.txt
```

> `Activate.ps1` 被拦下来（提示"禁止运行脚本"）的话，先执行一次：
> `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`

### 2.3 装 tdx2db

```powershell
cd datalake
python setup_tdx.py --install
```

上游**有** Windows 版（`tdx2db_Windows_x86_64.zip`），脚本会自动选它。

### 2.4 起看板、点按钮

```powershell
cd ..\assay
python serve.py
```

打开 **http://127.0.0.1:8770**，与 macOS 完全一样：页面顶上那条横条里点
**「▷ 开始建本地数据」**。

> 数据链的正本是 **`datalake/sync_daily.py`**（纯 Python，跨平台）。
> 同目录下那个 `sync_daily.sh` 只是一行转发，给老的 launchd 配置用的——
> **Windows 上不需要它，也不需要 Git Bash 或 WSL**。

### 2.5 每天自动更新

```powershell
python setup_tdx.py --install-timer
```

装两个 **Windows 计划任务**（schtasks）：**数据同步**（16:00~20:00 每 10 分钟
问一次"今天的数据齐了没"，齐了秒退）和**信号重算**（16:00~次日 09:20 每小时）。

```powershell
python setup_tdx.py --schedule          # 看配置与【实际装上】的点位
schtasks /query /tn finacial-sync-1600 /v /fo list    # 看某个任务上次跑得怎么样
```

★ 任务里写的是**你这个虚拟环境的 `python.exe` 绝对路径** + `sync_daily.py`
（纯 Python 正本）—— 所以 Windows 上**不需要 Git Bash 或 WSL**，也不会
踩到"系统 python 没装 duckdb"那个坑。

> ⚠ **这一段没有 Windows 机器可实测**：代码路径是跨平台的（装/查/删三处
> 任务名走同一处定义，并有守卫构造了一次"装 44 个 -> 查出 44 个"的往返），
> 但真实的 `schtasks` 行为我没跑过。装完**回头看一眼上次运行结果是不是 0**，
> 别默认它成了。

## 3. 装完了怎么确认它是好的

```bash
# 1) 看板起得来、有数据
python3 serve.py            # Windows: python serve.py
#    打开 127.0.0.1:8770 —— 顶上那条"还没有数据"的横条应该【消失了】

# 2) 数据链跑得通（--dry 只打印要跑什么，不真跑）
cd datalake && python3 sync_daily.py --dry

# 3) 全量自检（可选，要 playwright，约 20 分钟）
cd assay && python3 selftest.py --fast     # 快的那层，约 1 分钟
```

---

## 4. 财务数据（可选，要聚宽账号）

用到 PB / ROE / 分红这些财务字段的策略才需要。

1. 看板「🔄 数据」页 → 「取聚宽代码」，复制那段代码
2. 到聚宽研究环境里跑，它会打成**一个 tar 包**
3. 回到同一个页面 → 「上传导出的包」

上传之后服务端会把该跑的几个 loader **全跑一遍**（以前这步要照着屏幕手抄
5~8 条命令，抄漏一条就是数据不一致**而且不报错**）。

---

## 5. 常见问题

| 症状 | 原因与处理 |
|---|---|
| 页面一堆 `500`、报"本地还没有这份数据" | 数据还没建。照 1.4 / 2.4 点那个按钮 |
| 改了 `assay/*.py` 之后页面上的数变成 `undefined` | Python 模块只在**进程启动时**加载一次。`python3 serve.py --restart` |
| `--install` 报 schema 版本不兼容 | 新版 tdx2db 要重建整个库。它**拒绝安装而不是写坏数据**；要升级就得重跑一次 `--bootstrap` |
| Intel Mac：`--install` 说没有对应的包 | 自己编：装 Go 之后 `go install github.com/jing2uo/tdx2db@latest`，把产物放进 `PATH` |
| 端口 8770 被占了 | `python3 serve.py --port 9000`；想知道是谁占着就 `python3 serve.py --status` |
| 想知道现在到底装到哪一步了 | 「🔄 数据」页有七个阶段的清单，**状态是现查磁盘得出的**（不是记一个"跑过了"的标记——那个会在文件被删、换台机器时说谎） |

---

## 6. 更详细的

| 看什么 | 去哪 |
|---|---|
| 数据接口、字段口径、踩过的坑 | `datalake/docs/数据字典/`（四维索引，**查数据先看这里**） |
| 数据平台的架构与各层语义 | `datalake/README.md` |
| 怎么写一个策略、回测怎么跑 | `assay/README.md` |
| 整个项目的设计决策与纪律 | `assay/CLAUDE.md` |
