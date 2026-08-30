#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""策略可见的函数。策略文件只需 `from assay.api import *`，
写法与聚宽基本一致：initialize + run_daily/run_weekly + order_target_value + g。

为什么用模块级代理而不是把 engine 传给策略：
聚宽的手感就是裸函数名（`order_target_value(...)`、`g.xxx`），
照抄这个手感能让已有策略几乎零改动移植过来。代价是全局状态，
所以 Engine.run() 里会显式 bind/unbind，禁止并发跑两个回测。
"""
from .context import G

__all__ = ['g', 'run_daily', 'run_weekly', 'run_monthly', 'set_benchmark',
           'set_slippage', 'set_order_cost', 'set_dividend_tax', 'set_volume_ratio',
           'run_every',
           'order_target_value', 'order_target_percent', 'order_stop_sell', 'log']

g = G()
_E = None          # 当前 Engine


def _bind(engine):
    """★ 必须【原地复用】同一个 g 对象，不能重新赋值。

    策略文件写 `from assay.api import *`，在【模块导入时】就把 api.g 这个对象
    绑进了自己的命名空间。之后再 `api.g = engine.g` 只改了 api 模块里的名字，
    策略手上那个引用不会跟着变 —— 结果 engine.g / context.g 是另一个
    【永远为空】的对象。这个 bug 一直静默存在，直到参数扫描要读 engine.g 才暴露。
    """
    global _E
    _E = engine
    g.__dict__.clear()          # 上一轮回测的残留必须清掉
    engine.g = g                # engine / context 与策略共用同一个对象


def _unbind():
    global _E
    _E = None


def _need():
    if _E is None:
        raise RuntimeError('策略函数只能在 Engine.run() 期间调用')
    return _E


# ---------- 调度 ----------
def run_daily(func, time='09:30'):
    _need().schedule(func, time, freq='d')


def run_weekly(func, weekday=1, time='09:30'):
    """本周的第 weekday 个【交易日】。负数从周末数起（-1 = 本周最后一个交易日）。
    与聚宽同义：遇到假期自动顺延，不是「固定星期几」。"""
    _need().schedule(func, time, freq='w', weekday=weekday)


def run_every(func, ndays=5, offset=0, time='09:30'):
    """每 ndays 个【交易日】跑一次，与自然周无关。

    与 run_weekly 的区别：run_weekly 锚在自然周上，遇到假期短周
    间隔会在 1~13 个交易日之间跳；run_every 的间隔恒定，代价是
    星期几会随假期一路漂移。
    offset 是相位（0..ndays-1），决定从第几个交易日起算。
    """
    _need().schedule(func, time, freq='n', every=ndays, offset=offset)


def run_monthly(func, monthday=1, time='09:30'):
    """本月的第 monthday 个【交易日】。负数从月末数起（-1 = 本月最后一个交易日）。"""
    _need().schedule(func, time, freq='m', monthday=monthday)


# ---------- 基准 ----------
def set_benchmark(code):
    """基准指数，用于超额收益 / alpha / beta / 信息比率 / 超额回撤。
    接受聚宽代码（'000905.XSHG'）或 tdx 代码（'sh000905'）。"""
    _need().set_benchmark(code)


# ---------- 成本设定 ----------
def _set_cost(field, value):
    """优先级：命令行显式指定 > 策略内设定 > 默认值。

    成本本质是【回测参数】不是【策略参数】—— 同一策略在不同成本下是两次
    不同的回测。所以策略里最好不写成本；写了也会被命令行覆盖，
    并且**会告警**，不静默忽略（静默覆盖就是下一个「结果对不上却查不到原因」）。
    """
    c = _need().cost
    if field in c.locked:
        log.warn('命令行已指定 %s，忽略策略里的设定（策略值 %s）', field, value)
        return
    setattr(c, field, value)


def set_slippage(x):
    """双边滑点。聚宽 PriceRelatedSlippage(x) 语义：买 +x/2、卖 -x/2。"""
    _set_cost('slippage', x)


def set_order_cost(commission=None, min_commission=None,
                   close_tax=None, open_tax=None):
    for k, v in (('commission', commission), ('min_commission', min_commission),
                 ('close_tax', close_tax), ('open_tax', open_tax)):
        if v is not None:
            _set_cost(k, v)


def set_volume_ratio(x):
    """单笔委托最多吃掉当日成交额的比例（聚宽 order_volume_ratio，默认 0.25）。
    设 0 表示不限制 —— 但那样做出的资金容量结论是假的。"""
    _set_cost('volume_ratio', x)


def set_dividend_tax(on=True):
    """红利税。聚宽会扣，默认开启。关掉只用于量化这笔税本身的影响。"""
    _set_cost('dividend_tax', on)


# ---------- 下单 ----------
def order_target_value(code, value):
    """调到目标市值。加减仓由 broker 内置处理（FIFO 分批），策略不必关心。"""
    return _need().broker.order_target_value(code, value)


def order_stop_sell(code, price=None):
    """止损清仓。price 为盘中触发价（会被夹到当日真实 [low, high] 内）；
    None = 日频模式，按相位价成交。成交记 reason='stop'。"""
    return _need().broker.order_stop_sell(code, price)


def order_target_percent(code, pct):
    """调到总权益的目标占比。pct=0 清仓。"""
    return _need().broker.order_target_percent(code, pct)


# ---------- 日志 ----------
class _Log:
    def __init__(self):
        self.verbose = False

    def info(self, msg, *a):
        if self.verbose:
            print('  ' + (msg % a if a else msg))

    def warn(self, msg, *a):
        print('  ⚠ ' + (msg % a if a else msg))


log = _Log()
