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
