#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""主循环与调度。

日频 bar 只有 OHLC，所以「时刻」是**逻辑阶段**而不是真实时钟，
成交价按阶段确定 —— 这一点必须对策略作者透明，否则会误以为有分钟级精度：

    time <= 09:25    PRE_OPEN   不可交易（对应聚宽的盘前 9:05 之类）
    09:26 ~ 09:30    OPEN       成交价 = 后复权【开盘价】
    09:31 ~ 14:59    INTRADAY   成交价 = 后复权【收盘价】（日线代理，见下）
    >= 15:00         CLOSE      不可交易，只记权益

⚠️ INTRADAY 用收盘价代理是有偏的：聚宽在 14:00 取的是那一刻的分钟价。
   实测这个代理让「盘中打开又回封的涨停股」被留在手里（JQ 已卖），
   影响 0.3~1.0pp。要消除得接分钟数据。
"""
from . import api
from .broker import Broker, Cost, PRE_OPEN, OPEN, INTRADAY, CLOSE
from .context import Context, G, Portfolio
from .guard import GuardedFeed


def _phase(t):
    if t <= '09:25':
        return PRE_OPEN
    if t <= '09:30':
        return OPEN
    if t < '15:00':
        return INTRADAY
    return CLOSE


class Engine:
    def __init__(self, strategy, feed, cash=1e6, cost=None, params=None):
        self.params = dict(params or {})
        self.strategy = strategy
        self.feed = feed
        self.cost = cost or Cost()
        self.g = G()
        self.pf = Portfolio(starting_cash=cash, cash=cash)
        self.broker = Broker(self.pf, feed, self.cost)
        # ★ broker 拿【原始 feed】（撮合本来就该看到当日行情，那不是未来函数），
        #   策略拿【加固代理】—— 只能看严格早于今天的数据。
        self.guard = GuardedFeed(feed)
        self.ctx = Context(self.pf, self.guard, self.g, broker=self.broker)
        self._tasks = []          # (time, freq, func, weekday, monthday)
        self.curve = []           # [(date, total_value)]
        self.bench_code = None
        self.bench = []           # [(date, index_close)]
        self._bench_px = None
        self.bench_base = None
        self.daily = []           # 每日账户快照
        self.holdings = []        # 每日逐持仓快照（复盘时要看当时到底拿着什么）

    _bench_cli = False

    def set_benchmark(self, code):
        if self._bench_cli and self.bench_code:
            from . import api
            api.log.warn('命令行已指定基准 %s，忽略策略里的 set_benchmark(%s)',
                         self.bench_code, code)
            return
        self.bench_code = code
        self._bench_px, self.bench_base = self.feed.benchmark(code)

    def schedule(self, func, time, freq='d', weekday=1, monthday=1):
        self._tasks.append((time, freq, func, weekday, monthday))

    # ---------- 该不该在这一天跑 ----------
    def _build_ordinals(self):
        """预计算每个交易日在【本周 / 本月】内的序号（正数从头数，负数从尾数）。

        ★ 修的是一个静默 bug：原来 _due 收了 weekday/monthday 却从不使用，
          `run_weekly(f, weekday=3)` 会静默按周一执行、
          `run_monthly(f, monthday=15)` 静默按月初执行 —— 策略作者无从察觉。
          聚宽的语义是「本周/本月的第 n 个交易日」，负数从末尾数。
        """
        days = self.feed.trading_days
        self._wk_ord, self._mo_ord = {}, {}
        for key_fn, store in ((lambda x: x.isocalendar()[:2], self._wk_ord),
                              (lambda x: (x.year, x.month), self._mo_ord)):
            buckets = {}
            for d in days:
                buckets.setdefault(key_fn(d), []).append(d)
            for grp in buckets.values():
                n = len(grp)
                for j, d in enumerate(grp):
                    store[d] = (j + 1, j - n)      # (正序号, 负序号: -1 是最后一个)

    def _due(self, i, d, freq, weekday, monthday):
        if freq == 'd':
            return True
        if freq == 'w':
            pos, neg = self._wk_ord.get(d, (0, 0))
            return weekday == pos or weekday == neg
        if freq == 'm':
            pos, neg = self._mo_ord.get(d, (0, 0))
            return monthday == pos or monthday == neg
        return False

    def run(self, verbose=False):
        api._bind(self)          # 之后 self.g is api.g
        self.ctx.g = self.g
        api.log.verbose = verbose
        try:
            # ★ 参数要在 initialize 【前后各应用一次】：
            #   前：initialize 里 run_monthly(monthday=g.monthday) 这类调度
            #       在 initialize 内就注册完了，只有「后」的话覆盖值永远来不及。
            #   后：确保覆盖值最终胜过策略 initialize 里的默认赋值。
            #   配合策略侧 `g.x = getattr(g, 'x', 默认)` 的写法，两次都必要。
            for _k, _v in self.params.items():
                setattr(self.g, _k, _v)
            # 「前」这一步会把拼错的名字也凭空建成属性，于是事后的 hasattr
            # 检查必然通过。改成录 initialize 期间的属性【写入】：策略声明参数
            # 的那行 `g.x = getattr(g, 'x', 默认)` 一定会写一次，拼错的名字
            # 不会被写到 —— 这才是可判定的信号。
            _declared = set()
            self.g._rec = _declared
            try:
                self.strategy.initialize(self.ctx)
            finally:
                del self.g._rec
            _declared.discard('_rec')
            # ★ **拼错的参数名必须立即抛错** —— 静默无效会让整个扫描的结论变成
            #   「这个参数没影响」，那是最危险的一类假结论。
            for _k, _v in self.params.items():
                if _k not in _declared:
                    _avail = ', '.join(sorted(x for x in _declared
                                              if not x.startswith('_')))
                    raise KeyError('策略里没有参数 g.%s（可用: %s）'
                                   % (_k, _avail))
                setattr(self.g, _k, _v)
            self._tasks.sort(key=lambda t: t[0])
            self._build_ordinals()
            days = self.feed.trading_days
            for i, d in enumerate(days):
                self.ctx.current_date = d
                self.ctx.previous_date = self.feed.prev_trading_day(d)
                self.broker.start_day(d)
                for t, freq, func, wd, md in self._tasks:
                    if not self._due(i, d, freq, wd, md):
                        continue
                    ph = _phase(t)
                    self.broker.phase = ph
                    self.ctx.current_phase = ph
                    self.guard.set_clock(d, ph)      # 防火墙跟着阶段收紧
                    func(self.ctx)
                tv = self.pf.total_value
                self.curve.append((d, tv))
                if self._bench_px is not None and d in self._bench_px:
                    self.bench.append((d, self._bench_px[d]))
                mv = self.pf.positions_value
                self.daily.append({
                    'date': d, 'cash': self.pf.cash, 'positions_value': mv,
                    'total_value': tv, 'n_positions': len(self.pf.positions),
                    'cash_pct': self.pf.cash / tv if tv else 0.0,
                })
                for c, p in self.pf.positions.items():
                    self.holdings.append({
                        'date': d, 'code': c, 'shares': p.shares,
                        'last_price': p.last_price, 'value': p.value,
                        'weight': p.value / tv if tv else 0.0,
                        'entry_date': p.entry_date, 'entry_price': p.entry_price,
                        'unrealized_pnl': p.shares * (p.last_price - p.entry_price),
                        'unrealized_ret': (p.last_price / p.entry_price - 1)
                                          if p.entry_price else 0.0,
                        'div_gross': p.div_gross,
                    })
        finally:
            api._unbind()
        return self.curve
