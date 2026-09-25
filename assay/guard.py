#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PIT 防火墙：让未来函数在结构上写不出来。

## 为什么需要它

backtrader / zipline / rqalpha 用「bar 迭代式」喂数据，策略只能拿到当前 bar，
**未来函数在结构上就不可能写出来**。我们选了「SQL over 宽表」——
表达力强得多，但把那个免费保障丢掉了。这是设计债，不是缺功能。

实测代价（strategies/_demo/lookahead.py，只把 previous_date 换成 current_date、
按当天 change_pct 排序）：**年化 20133%、夏普 7.20**。三行代码作弊成功。

## 设计：模仿聚宽的两套 API 分离

聚宽里「历史/财务数据」走 `get_fundamentals(date=yesterday)`，
「当前时点状态」走 `get_current_data()` —— 两个入口，天然分开。这里照此：

    context.data.{query,codes_at,panel_at}   只能看【严格早于今天】的数据
    context.current(codes)                  今天的、且【该阶段已知】的字段

`{panel}` 展开成 `date < 今天` 的子查询 —— 未来行根本不在关系里。
再加一道：SQL 里出现 >= 今天的日期字面量直接抛错。

**不掩码成 NULL** —— 那会让策略静默拿到空结果，正是本项目反复踩的失败模式。
违规一律抛 LookAheadError。

## 各阶段「今天已知」的字段

    PRE_OPEN   无（当天还没有 bar）
    OPEN       open / preclose / 涨跌停价 / is_open_limit_up|down / hfq_factor
    INTRADAY   全部（与「盘中成交价用收盘价代理」的约定一致，见 engine.py）
    CLOSE      全部
"""
import datetime
import re

from .broker import CLOSE, INTRADAY, OPEN, PRE_OPEN


class LookAheadError(RuntimeError):
    """策略试图读取当前时点还不知道的数据。"""


# 开盘时已知：由昨收与今开算得，不含任何收盘/最高/最低/成交量派生量。
# ⚠️ floatmv / totalmv / pb / pe_ttm 都用当日收盘价算 -> 开盘时【未知】。
#    这也是聚宽策略一律用 date=yesterday 取市值的原因。
KNOWN_AT_OPEN = frozenset("""
date jq_code symbol open preclose hfq_factor
limit_pct limit_up limit_down is_open_limit_up is_open_limit_down
fin_report_date fin_pub_date revenue net_profit_parent eps_basic
roe_parent bps total_assets equities np_ttm rev_ttm rev_yoy np_yoy
np_q rev_q eps_q np_q_yoy rev_q_yoy roe_q roe_ttm
eps_q_derived roe_q_derived adjusted_profit_q ind_report_date ind_pub_date
sw_l1_code sw_l1_name is_st public_status list_date listed_days
in_hs300 in_zz500 in_zz1000 in_sz50 in_cyb in_kc50 in_zz800
""".split())

_DATE_LIT = re.compile(r"DATE\s*'(\d{4}-\d{2}-\d{2})'", re.I)


class GuardedFeed:
    """给策略用的 feed 代理。broker 持有的是未加固的原始 feed —— 撮合
    本来就该看到当日行情，那不是未来函数。"""

    def __init__(self, feed):
        self._f = feed
        self._date = None
        self._phase = None

    def set_clock(self, date, phase):
        self._date, self._phase = date, phase

    # ---------- 透传：日历类，不涉及行情 ----------
    @property
    def root(self):
        return self._f.root

    @property
    def trading_days(self):
        return self._f.trading_days

    def prev_trading_day(self, d):
        return self._f.prev_trading_day(d)

    def nth_prev_day(self, d, n):
        return self._f.nth_prev_day(d, n)

    @property
    def delist(self):
        return self._f.delist

    def benchmark(self, code):
        return self._f.benchmark(code)

    def fingerprint(self):
        return self._f.fingerprint()

    # ---------- 加固 ----------
    def _check_date(self, d, who):
        if self._date is None:
            return
        # ★ 允许 'YYYY-MM-DD' 字符串：策略里 `context.previous_date` 是 date，
        #   但新的取数 API 也接受字符串。不归一化的话这里会抛
        #   `'>=' not supported between str and date` ——
        #   那个报错**指不到真正的原因**（同 day_price 那条纪律）。
        if isinstance(d, str):
            try:
                d = datetime.date.fromisoformat(d[:10])
            except ValueError:
                raise LookAheadError(
                    '%s 的日期参数解析不出来：%r（要 YYYY-MM-DD 或 date）'
                    % (who, d))
        if d is not None and d >= self._date:
            raise LookAheadError(
                '%s 请求 %s，而当前回测时点是 %s —— 历史接口只能取【严格早于今天】'
                '的数据。今天的状态用 context.current(codes)。' % (who, d, self._date))

    def _scan_sql(self, sql):
        if self._date is None:
            return
        for m in _DATE_LIT.finditer(sql):
            if m.group(1) >= str(self._date):
                raise LookAheadError(
                    "SQL 里出现日期字面量 DATE '%s'，不早于当前回测时点 %s。"
                    % (m.group(1), self._date))

    def _panel_expr(self):
        """`{panel}` 展开为「严格早于今天」的子查询 —— 未来行不在关系里。"""
        if self._date is None:
            return self._f.panel
        return "(SELECT * FROM %s WHERE date < DATE '%s')" % (self._f.panel, self._date)

    # ---------- 策略数据接口 ----------
    def query(self, sql, **kw):
        from .feed import std_tables
        t = std_tables(self._f.root)
        scan = dict(panel='', root='', **t); scan.update(kw)
        self._scan_sql(sql.format(**scan))
        f = dict(panel=self._panel_expr(), root=self._f.root, **t); f.update(kw)
        return self._f.con.execute(sql.format(**f)).df()

    def panel_at(self, date, cols='*', where='1=1', order=None, limit=None):
        self._check_date(date, 'panel_at(%s)' % date)
        return self._f.panel_at(date, cols, where, order, limit)

    def codes_at(self, date, where='1=1', order=None, limit=None):
        self._check_date(date, 'codes_at(%s)' % date)
        return self._f.codes_at(date, where, order, limit)

    def had_limit_up(self, codes, start, end):
        self._check_date(end, 'had_limit_up(end=%s)' % end)
        return self._f.had_limit_up(codes, start, end)

    def limit_up_days(self, codes, start, end):
        """明细版。🔴 PIT 检查不能少 —— 它和 had_limit_up 看同一张表，
        少一道防火墙就是给未来函数开了个后门（而它不报错）。"""
        self._check_date(end, 'limit_up_days(end=%s)' % end)
        return self._f.limit_up_days(codes, start, end)

    # ---------- 策略数据 API（见 feed.py 的同名方法）----------
    # 🔴 PIT 检查一个都不能少 —— 这三个方法直接读 std/ 与面板，
    #   少一道防火墙就是给未来函数开了个后门，而它**不报错**，
    #   只是回测结果好得可疑（同 lookahead 那条用例记的教训）。

    def universe(self, date, **kw):
        self._check_date(date, 'universe(%s)' % date)
        return self._f.universe(date, **kw)

    def snapshot(self, date, cols, **kw):
        self._check_date(date, 'snapshot(%s)' % date)
        return self._f.snapshot(date, cols, **kw)

    def fundamentals(self, date, fields, **kw):
        self._check_date(date, 'fundamentals(%s)' % date)
        return self._f.fundamentals(date, fields, **kw)

    def factors(self, date, ids, **kw):
        """因子值（见 `feed.factors`）。

        🔴 这道 PIT 检查是**因子这条路上唯一的防火墙**：面板自己保证了
          「(code, date) 那一行只用 <= date 的信息」，而"别问到今天"只有
          这里知道。少了它，策略在 T 日读 T 日的因子 —— 那一行含**当日收盘**
          派生量（ma / rsi / 换手），于是回测拿今天的收盘去决定今天的买卖，
          **不报错，只是结果好得可疑**（同 lookahead 那条用例记的教训）。
        """
        self._check_date(date, 'factors(%s)' % date)
        return self._f.factors(date, ids, **kw)

    def factor_meta(self, ids=None):
        """因子目录（单位 / 横截面可不可比 / 预热多少根）。

        ★ **不做 PIT 检查** —— 它没有日期维度，是"这个因子是什么"的静态
          描述。给它加一道日期参数反而是在暗示它随时间变（而它不）。
        """
        return self._f.factor_meta(ids)

    def factor_ids(self):
        return self._f.factor_ids()

    def bars(self, date, codes):
        """历史 bar。今天的 bar 走 context.current()，不从这里拿。"""
        self._check_date(date, 'bars(%s)' % date)
        return self._f.bars(date, codes)

    def bar_range(self, codes, start, end):
        """历史区间 bar（含 high/low）。end 同样受 PIT 防火墙约束。"""
        self._check_date(end, 'bar_range(end=%s)' % end)
        return self._f.bar_range(codes, start, end)

    # ---------- 今天：只给该阶段已知的字段 ----------
    def current(self, codes):
        """对应聚宽 get_current_data()。返回 {code: dict}，
        字段随阶段收紧 —— 开盘时拿不到收盘价派生量。"""
        if self._phase == PRE_OPEN:
            raise LookAheadError('盘前(PRE_OPEN)还没有当日 bar，无法取 current()')
        bars = self._f.bars(self._date, list(codes))
        full = self._phase in (INTRADAY, CLOSE)
        out = {}
        for c, b in bars.items():
            d = {'open_hfq': b.open_hfq, 'open_raw': b.open_raw, 'factor': b.factor,
                 'open_limit_up': b.open_limit_up, 'open_limit_down': b.open_limit_down}
            if full:
                # ★ `touch_up`（盘中摸到过涨停）与 `sealed`/`high_hfq` 同一批：
                #   都是**收盘**派生量，所以只在 INTRADAY/CLOSE 给。
                #   🔴 它是**全天**最高价派生的 —— 在 INTRADAY（14:00）取它，
                #     严格说用到了 14:00 之后的信息。这与 `limit_up`
                #     （= 收盘封住）是**同一个近似**：引擎无分时线，
                #     盘中相位一律用收盘价代理（froec.py 的既有约定）。
                #     用它的策略必须在自己的 docstring 里把这一条写明。
                d.update(close_hfq=b.close_hfq, limit_up=b.limit_up,
                         limit_down=b.limit_down, sealed=b.sealed,
                         touch_up=b.touch_up,
                         # 🔴 `change_pct` 与 `ma5` 也是**收盘**派生量，同一批。
                         #   上次加 `touch_up` 时改了这里，这次加这两列**忘了** ——
                         #   表现是策略里 `row.get('change_pct')` 永远是 None ->
                         #   规则整段空转、**一个事件都不触发，而且不报错**。
                         #   ★ 教训：给 `Bar` 加列必须同时改这里（两处要一起动），
                         #     判据是「策略侧真的收到了那个字段」而不是「Bar 有了」。
                         change_pct=b.change_pct, ma5=b.ma5,
                         ma20=b.ma20,
                         high_hfq=b.high_hfq, low_hfq=b.low_hfq)
            out[c] = d
        return out
