#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P1-MASK-CLOSE：把 P1-MASK 的成交时点从【开盘】改到【尾盘】。

    python3 run.py strategies/ETF/etf_p1_mask_close.py --start 2016-01-01
    python3 run.py strategies/ETF/etf_p1_mask_close.py --param rebal_time=09:30   # 等价性自证

🔴 **在这个引擎里，「尾盘」的实质是【用当日收盘价成交】。**
没有分时线，`_phase()` 把 `09:31 ~ 14:59` 全归 INTRADAY、一律用**当日收盘价**；
而 `09:30` 属 OPEN、用**开盘价**。所以 14:00 与 14:55 的成交价完全相同
（本项目记过一次，别再试）—— 真正被改变的只有 **开盘价 -> 收盘价** 这一件事。

★ 一个字都没复述 P1-MASK / 正本的参数：`importlib` 私有加载 + `initialize` 转发
  （同 froec_close.py / etf_p1_mask.py 的做法）。唯一的改动是**把 `_p1` 模块里那个
  `run_daily` 临时换掉**，让正本 `initialize` 注册任务时用我们的时点 ——
  `run_daily` 注册的是**函数对象**，注册完再改属性是无效的，所以必须在注册【当时】拦。

⚠ **决策仍然用 T-1 的数据**（`my_trade` 里 `d = context.previous_date` 没动）：
  现实中 14:55 下单时当天的收盘价还不知道，所以「用当日收盘价成交」本身就已经
  偏乐观了。再把决策也挪到当日数据上就是未来函数。

**结论：两个旋钮都不采纳**（2026-09-20，2x2：调仓日 x 成交时点）。

三条自证（任一不过下面全作废）：
    (1) rebal_time=09:30 + rebal_weekday=3  指纹 f4de11b86d75 == P1-MASK  逐位一致
    (2) 14:00 与 14:55 指纹相同（40f6e72e7de0）—— 印证「尾盘 = 当日收盘价」
    (3) 换成周三指纹必须变（03960a78ced3）—— 否则参数没接上，而那不报错

全程 2016-01-01~2026-09-18（10 万，策略自声明成本）
    配置          年化      回撤     夏普   笔数  平均仓位     期末
    周四·开盘(现状) 11.89%  22.54%  0.709  2342  52.3%  33.30万
    周四·尾盘      13.42%  24.08%  0.782  2352  52.3%  38.52万
    周三·开盘      10.86%  21.83%  0.658  2390  52.6%  30.15万
    周三·尾盘      11.94%  22.25%  0.713  2412  52.6%  33.46万
后段 2021-2026   周四开盘 20.51/1.017  周四尾盘 20.25/1.006
                 周三开盘 17.54/0.894  周三尾盘 16.77/0.864

逐年独立（每年重置 10 万，11 年）—— 判规则的唯一口径
    周四：尾盘−开盘  均差 +1.01pp 中位 +0.09 胜 6/11 sd 5.27 t=+0.63 去掉最好最差 +1.15pp
    周三：尾盘−开盘  均差 -0.01pp 中位 -0.10 胜 5/11 sd 4.09 t=-0.01
    开盘：周三−周四  均差 -1.91pp 中位 -0.99 胜 3/11 sd 7.61 t=-0.83 去掉最好最差 -1.46pp
    周三尾盘−现状    均差 -1.93pp 中位 -0.37 胜 5/11 sd 10.36 t=-0.62

🔴 **尾盘效应是真的，但它没有方向。** 两组「尾盘−开盘」的逐年符号 **10/11 同号**
（纯随机约 1.2%）—— 「今年收盘价好还是开盘价好」是个**市场层面的年度共同因子**。
但那个因子**逐年翻符号**（周四组 6 正 5 负），而且幅度不相关（r=0.29）：
均差那 +1.01pp 一多半来自 2025 单年，而 2025 恰恰是两组唯一分歧的一年
（周四 +8.15 / 周三 -5.72）。**有效应 != 有 edge。**
★ 别把这条写成「两组反号所以是噪声」—— 那是读错了证据（我第一版就这么写的）。

🔴 而且本版天生**偏乐观**：回测按当日收盘价成交，而 14:55 下单时那个价还不知道。
   开盘版没有这个问题（09:30 成交在集合竞价价上，那是真能下单的价）。
★ 回撤对这两个旋钮几乎不敏感（四档平均年内回撤 12.91~13.92%，极差 <1pp）——
  不存在「收益换回撤」的取舍空间。
★ 与 froec_close（小市值，周一/周二调仓，尾盘 -9.45/-9.06pp）**结论不同但不矛盾**：
  那次两个档位方向一致、机制在面板上量得出来（周内前两日开盘系统性便宜）；
  这次两个档位的均值反号，正是「没有机制」的样子。

★ 文件保留不删（同「已否证的规则留成开关，不要留成注释」）——
  下次再想起「要不要尾盘买 / 换个调仓日」，跑一行就看得到这张表。
"""
import os
import importlib.util

from assay.api import *          # noqa: F401,F403

DATALAKE = 'etf_lake'            # 与正本同一个 lake（策略自己声明）

_BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     'etf_p1_mask.py')
_spec = importlib.util.spec_from_file_location('_p1_mask_close_base', _BASE)
_mask = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mask)
_p1 = _mask._p1                  # 正本（P1-MASK 自己私有加载的那一份）

# 复用 P1-MASK 的全部实现（下面的 initialize 会覆盖掉同名那个）
for _n in dir(_mask):
    if not _n.startswith('__'):
        globals().setdefault(_n, getattr(_mask, _n))


def initialize(context):
    # ★ 默认值写成**字面量**：看板的参数解析只认 getattr(g,'x',字面量)，
    #   用模块级常量的话这一项在回测页上看不见。
    g.rebal_time = str(getattr(g, 'rebal_time', '14:55'))
    # 重选在周内第几个交易日（0=周一 .. 3=周四）。正本是 3。
    # ★ `_is_reselect_day` 按【模块全局】查 REBAL_WEEKDAY，所以换得掉；
    #   这与「run_daily 注册的是函数对象」是两回事，别混。
    g.rebal_weekday = int(getattr(g, 'rebal_weekday', 3))
    _p1.REBAL_WEEKDAY = g.rebal_weekday
    _orig = _p1.run_daily

    def _rd(func, time='09:30'):
        return _orig(func, time=g.rebal_time)

    _p1.run_daily = _rd
    try:
        _mask.initialize(context)     # 它再转发给正本；mask_below 等参数原样继承
    finally:
        _p1.run_daily = _orig         # 还原，别污染同进程里别的加载
