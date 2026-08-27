#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""策略可见的状态对象。仿聚宽的 context / g 语义，但只保留真正被用到的部分。

设计原则：**策略只描述「买什么、什么时候换」，不描述「能不能成交」。**
涨跌停、T+1、停牌、退市、整手、税费全部由 broker 负责 ——
策略里写这些不但重复，还会因为漏写而静默失真（旧引擎就是把涨停过滤写在选股里）。
"""
from dataclasses import dataclass, field


class G:
    """策略的全局变量袋，等价于聚宽的 g。

    多一个 `_rec` 钩子：打开后记录所有被赋值的属性名。引擎用它判定
    `--param xxx=1` 里的 xxx 是不是策略真的声明过的参数 —— 参数必须在
    initialize 之前就写进 g（否则 run_monthly(monthday=g.monthday) 这类
    调度读不到），可那样一来拼错的名字也会凭空存在，事后 hasattr 就永远
    是 True。只有「initialize 期间有没有写过这个名字」才分得清。
    """
    def __setattr__(self, k, v):
        rec = self.__dict__.get('_rec')
        if rec is not None:
            rec.add(k)
        object.__setattr__(self, k, v)


@dataclass
class Lot:
    """一笔建仓。**分批（FIFO）而不是合并成一个平均成本** ——

    红利税按【持有期】分档（≤1月 20% / 1月~1年 10% / >1年 免），
    T+1 也只锁当日买入的那批。若把加仓合并成一个平均建仓日，这两件事都会失真，
    而且是静默失真：金额算得出来、只是错的。分批之后可以【精确】计征，
    不需要「按比例摊」这类近似。
    """
    shares: float          # 后复权记账单位；真实股数 = shares * hfq_factor
    entry_date: object
    entry_price: float
    div_gross: float = 0.0 # 这一批在持有期内已收现金分红的毛额


@dataclass
class Position:
    code: str
    lots: list = field(default_factory=list)
    last_price: float = 0.0    # 最后已知后复权收盘价，停牌期间用它估值

    @property
    def shares(self):
        return sum(l.shares for l in self.lots)

    @property
    def value(self):
        return self.shares * self.last_price

    @property
    def entry_date(self):
        """最早一批的建仓日。FIFO 下它就是「下一个被卖掉的那批」的建仓日。"""
        return self.lots[0].entry_date if self.lots else None

    @property
    def entry_price(self):
        """按股数加权的平均建仓价 —— 只用于展示与浮盈计算，
        计税与 T+1 一律走 lots，不用这个平均值。"""
        sh = self.shares
        return sum(l.shares * l.entry_price for l in self.lots) / sh if sh else 0.0

    @property
    def div_gross(self):
        return sum(l.div_gross for l in self.lots)

    def sellable(self, today):
        """可卖股数：T+1 —— 当日买入的那批不可卖。"""
        return sum(l.shares for l in self.lots if l.entry_date < today)


@dataclass
class Portfolio:
    starting_cash: float
    cash: float
    positions: dict = field(default_factory=dict)

    @property
    def positions_value(self):
        return sum(p.value for p in self.positions.values())

    @property
    def total_value(self):
        return self.cash + self.positions_value

    @property
    def returns(self):
        return self.total_value / self.starting_cash - 1


class Context:
    """策略在每个调度点拿到的上下文。

    与聚宽的对应关系：
        context.portfolio        同名同义
        context.current_dt       当前调度时点（date + phase）
        context.previous_date    前一交易日 —— 选股取数几乎总该用它
        context.data             数据访问入口（本项目自有，不仿 JQ 的 ORM，见 feed.py）
    """

    def __init__(self, portfolio, feed, g, broker=None):
        self._broker = broker
        self.portfolio = portfolio
        self.data = feed
        self.g = g
        self.current_date = None
        self.current_phase = None
        self.previous_date = None

    @property
    def current_dt(self):
        return self.current_date

    def current(self, codes):
        """今天的、且【当前阶段已知】的字段。对应聚宽 get_current_data()。

        开盘时拿不到收盘价及其派生量（floatmv / pb / is_limit_up 等）——
        那是未来信息。历史数据走 context.data.*，两条路分开走。
        """
        return self.data.current(codes)

    def tradable(self, codes, side='buy'):
        """筛掉当日不可成交的（停牌 / 涨跌停 / T+1）。

        对应聚宽的 filter_paused_stock + filter_limitup_stock + filter_limitdown_stock。
        **判据来自 broker，策略不重新实现** —— 策略决定「要不要用下一名补位」，
        引擎决定「能不能成交」，职责分开但规则同一份。
        """
        return self._broker.filter_tradable(list(codes), side)
