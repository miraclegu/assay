"""实盘模块的【门面】—— 实现按产品域拆进了 `lv/`。

    lv/base.py  异常 / 常量 / LIVE / 原子读写 / 账户 / 交易日历 / 代码归一化
    lv/fee.py   费率模型        lv/px.py   取价与名称
    lv/pos.py   账本写入与重放   lv/ver.py  版本绑定
    lv/sig.py   信号生成与落盘   lv/perf.py 权益与收益（TWR）

依赖是干净的 DAG：`base → fee → px → pos → ver → sig → perf`。
切点由依赖分析定：原有段落标记 + 把「费率模型」那 909 行按实际内容细分成
4 类 —— `perf`/`sig` 从那段调的是 `cash`/`cashflows`/`fifo_lots`，
**那些是账务不是费率**，混在一个段里才显得它像个大杂烩。

## 核心原则（没变）：不重写任何交易规则

止损、炸板离场、调仓都埋在策略 + 引擎里，依赖 `g.pos_state` /
`g.high_limit` / `pos.entry_price` 这些内部状态。这里只做两件事：
用成交流水重建 Portfolio（FIFO lots，真实成本价），把 broker 换成
`RecordingBroker`（只记不成交），让策略跑自己的代码路径。
所以实盘提示与回测行为天然同源。

## 🔴 门面是 ModuleType 子类，读【和写】都转发

`assay.live` 是对外契约：selftest 用 44 个 `lv.*` 名字，其中
**`lv.LIVE = 临时目录`** 是那条纪律的实现手段 —— 用例靠它把写操作重定向掉、
不污染真实账本（有断言强制）。

PEP 562 的模块级 `__getattr__` **只拦读**：一旦有人 `lv.LIVE = tmp`，
就在本模块 `__dict__` 里建了副本，之后 `lv/base.py` 里的
`acct_dir` / `load_accounts` 读的还是 base 自己的 `LIVE` ——
**重定向静默失效，用例把数据写进真账本，而它不报错**。
所以真值只有一份（在 `lv.base`），`__setattr__` 把它写回去。
"""
import sys
import types

from .lv import base, fee, paper, perf, pos, px, sig, ver

_FACADE = (base, fee, px, pos, ver, sig, perf, paper)


class _Facade(types.ModuleType):
    """让 `assay.live` 成为对 `lv/` 各域的透明门面（读写双向）。

    ★ 白名单只放**会被外部重新赋值**的名字。目前只有 `LIVE`（重定向账本
      根目录）—— 见模块头那条。其余属性照常设在本模块上。
    """

    _FWD = ('LIVE',)

    def __getattr__(self, name):
        for _m in _FACADE:
            try:
                return getattr(_m, name)
            except AttributeError:
                pass
        raise AttributeError('module %r has no attribute %r'
                             % (__name__, name))

    def __setattr__(self, name, value):
        if name in _Facade._FWD:
            setattr(base, name, value)          # 真值只有一份，在 lv.base
        else:
            super().__setattr__(name, value)


sys.modules[__name__].__class__ = _Facade
