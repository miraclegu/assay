#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""撮合与账务。**A 股的全部交易规则集中在这里，策略不需要知道任何一条。**

这个文件是整个项目的价值所在 —— 下面每一条都是拿聚宽实测结果逐年对账
反推出来的，每一条都曾经以【静默】的方式让回测失真（不报错、不为空、
结果看着完全合理）：

  1. 后复权计价 —— 用不复权价算权益会让除权日凭空蒸发、分红送转不入账
  2. 停牌持仓估值 —— 缺行时若按 0 计，停牌持仓市值直接消失
  3. 停牌不能交易 —— 面板缺行即当日无法成交
  4. 退市 vs 停牌 —— 判据必须是权威 delist_date，不能拿「缺行 N 天」当代理；
     误判会把停牌股按陈价卖掉、复牌后再没买回
  5. 整手约束按【真实股数】—— 套在后复权价上等价于要求后复权价 < 单仓资金/100，
     老股票(因子 20~30)会被整只静默跳过
  6. 开盘涨停买不进 / 开盘跌停卖不掉 —— 一字板无对手盘
  7. T+1 —— 当日买入不可卖出
  8. 红利税按持有期在【卖出时】补缴（财税[2015]101号 20%/10%/免）
  9. 印花税卖出千一 + 佣金双边万三【最低 5 元/笔】(min-5 买卖都算)
 10. 滑点双边（PriceRelatedSlippage 语义：买 +x/2、卖 -x/2）

★ 唯一的成交出口是 _fill_sell / _fill_buy。卖出有 6 件副作用（平仓、滑点、
  佣金、印花税、红利税结算、状态清理），分散写会漏 —— 旧引擎三处各写一遍，
  交易日志只挂在其中一处，直接导致一次归因诊断全错、白查一轮。
"""
from collections import namedtuple
from datetime import date as _date

from .context import Lot, Position

PRE_OPEN, OPEN, INTRADAY, CLOSE = 'pre_open', 'open', 'intraday', 'close'

Order = namedtuple('Order', 'code side shares amount ok reason')

# 股息红利差别化个税（财税[2015]101号）
DIV_TIERS = ((30, 0.20), (365, 0.10))      # (天数上限, 税率)；超出则免征


# 证券交易印花税：2023-08-28 起【减半】，千分之一 -> 千分之零点五。
# 跨越这个日期的回测用单一税率必然错，所以默认按日期分段。
STAMP_HALVED_FROM = _date(2023, 8, 28)
STAMP_BEFORE, STAMP_AFTER = 0.001, 0.0005


class Cost:
    """交易成本。**这是【回测参数】不是【策略参数】** ——
    同一份策略在不同成本假设下是两次不同的回测，所以由命令行给，
    并完整记进归档的 meta.json。

    默认值取业内常用：
        滑点        0.0015 双边（50~100 万资金规模下的实测标定；
                    每仓 5~10 万时冲击成本近似为 0，集合竞价单一价无买卖价差）
        佣金        万分之 2.5，最低 5 元/笔（当前主流零售费率）
        印花税      卖出千分之一 / 千分之零点五（2023-08-28 起减半，自动分段）
        买入印花税  0（A 股买入不征）
        红利税      开启（聚宽会扣；财税[2015]101号 20%/10%/免）

    `locked` 记录哪些字段由命令行显式指定 —— 被锁定的字段策略无法覆盖，
    且策略尝试覆盖时会**告警**，不静默忽略。
    """

    def __init__(self, slippage=0.0015, commission=0.00025, min_commission=5.0,
                 close_tax='auto', open_tax=0.0, dividend_tax=True,
                 volume_ratio=0.25, buy_slippage=0.0, locked=()):
        self.slippage = slippage          # 双边，买 +s/2 卖 -s/2
        # ★ 买入侧【额外】单边滑点，叠加在 slippage/2 之上（默认 0）。
        #   用途：调仓是「先卖后买」，买入腿必然晚于卖出腿几十秒到几分钟，
        #   那段时间价格会漂。引擎没有分时线（INTRADAY 相位是拿收盘价代理），
        #   没法用「9:31 成交」来表达，只能把这段漂移折成买入侧多付的滑点。
        #   它同时进成交价【和】lot 的 entry_price —— 所以成交记录里的
        #   收益率也会体现，不像 open_tax 那样只是笔外的费用。
        self.buy_slippage = buy_slippage
        self.commission = commission
        self.min_commission = min_commission
        self.close_tax = close_tax        # 'auto' = 按日期分段
        self.open_tax = open_tax
        self.dividend_tax = dividend_tax
        # 单笔委托最多吃掉当日成交额的这个比例（聚宽 order_volume_ratio 默认 0.25）。
        # ★ 没有它，任何资金容量分析都是假的 —— 拿 5000 万跑流通市值 4 亿的
        #   微盘股，引擎会告诉你能全额成交。设 0 表示不限制。
        self.volume_ratio = volume_ratio
        self.locked = set(locked)

    def close_tax_at(self, d):
        if self.close_tax != 'auto':
            return self.close_tax
        return STAMP_AFTER if d >= STAMP_HALVED_FROM else STAMP_BEFORE

    def describe(self):
        st = ('按日期分段(≤2023-08-27 %.4f / ≥2023-08-28 %.4f)'
              % (STAMP_BEFORE, STAMP_AFTER)) if self.close_tax == 'auto' \
             else '%.5f' % self.close_tax
        return ('滑点 %.4f 双边%s | 佣金 %.5f 最低 %.0f 元 | 印花税 %s | '
                '买入印花税 %.5f | 红利税 %s | 成交量上限 %s'
                % (self.slippage,
                   ('（买入额外 +%.4f 单边）' % self.buy_slippage)
                   if self.buy_slippage else '',
                   self.commission, self.min_commission, st,
                   self.open_tax, '计' if self.dividend_tax else '不计',
                   ('当日成交额 %.0f%%' % (self.volume_ratio * 100))
                   if self.volume_ratio else '不限'))

    def to_dict(self):
        return {'slippage': self.slippage, 'buy_slippage': self.buy_slippage,
                'commission': self.commission,
                'min_commission': self.min_commission,
                'close_tax': self.close_tax, 'open_tax': self.open_tax,
                'dividend_tax': self.dividend_tax,
                'volume_ratio': self.volume_ratio,
                'locked_by_cli': sorted(self.locked),
                'stamp_halved_from': str(STAMP_HALVED_FROM)}


class Broker:
    def __init__(self, portfolio, feed, cost):
        self.pf = portfolio
        self.feed = feed
        self.cost = cost
        self.date = None
        self.phase = None
        self.bars = {}
        self._bought_today = set()
        self._prev_factor = {}
        self.rejects = []            # (date, code, side, reason) —— 拒单必须可见，不静默
        self.trades = []             # (entry_date, exit_date, code, ret)
        # 🔴 **逐笔成交流水**（2026-09-14 加，模拟盘要用）。
        #   `trades` 记的是**往返**（FIFO 批次，卖出时才写），回答"这一笔赚了
        #   多少"；`fills` 记的是**每一次真的买/卖**，回答"哪天做了什么" ——
        #   模拟盘要把它写进实盘账本，而账本是逐笔的。
        #   ★ 纯**投影**：只记录，不参与任何判定，一行行为都没改
        #     （等价性回归对过：froec / 红利 2024 全年报告逐行相同）。
        #   🔴 **股数与价格一律记【真实/不复权】口径** —— 实盘账本存的就是
        #     不复权成交价（`live.add_fill`），而引擎内部用后复权记账。
        #     搞反了不报错，只是模拟盘的成本价与实盘对不上一个复权因子。
        self.fills = []              # {date, code, side, shares, price, amount, fee, reason}
        # 🔴 **分红到账也要逐笔留痕**（同上，纯投影）。引擎把分红计进现金
        #   （`pf.cash += cash_in`）并按后复权口径缩减记账股数；而实盘账本的
        #   现金是"初始 − 买入 + 卖出"算出来的 —— 不把分红作为**现金流**
        #   补进去，模拟盘的现金就会**少掉所有分红**，而它不报错，
        #   只是权益一路偏低（红利那种策略一年能差几个点）。
        self.dividends = []          # {date, code, cash}
        self.div_tax_paid = 0.0
        self.n_adds = 0              # 加仓次数（往已有持仓追加批次）
        self.n_vol_capped = 0        # 因成交量上限被削减的委托笔数
        self.n_limit_unreliable = 0  # 涨跌停价算不准、跳过涨跌停判定的次数
        # 停牌持仓：按最后已知价挂账。★ 不加折价 —— 实测复牌日中位只跌 3~5%%，
        # 而且那笔损失在复牌当天本来就会计入，再折价就是双重计算。
        # 真正的风险是【永不复牌又没有退市日】：那会永久按陈价挂账、
        # 把权益虚高地钉在那里。所以这里只求【可见】+ 期末响亮告警。
        self._susp_run = {}          # code -> 当前连续停牌天数
        self.frozen_days = 0         # 累计「持仓但当日无行情」的持仓日
        self.max_frozen_run = 0
        self.max_frozen_code = None
        self.vol_capped_amount = 0.0 # 被削掉的金额（衡量资金容量的直接指标）
        self.div_cash_received = 0.0 # 累计收到的现金分红（毛额）
        self.buy_amount = 0.0        # 累计买入额，算换手率用
        self.sell_amount = 0.0       # 累计卖出额
        self.fee_paid = 0.0          # 累计佣金+印花税
        self.delisted = []
        self.lu_sold = 0

    # ================= 每日生命周期 =================
    def start_day(self, date, extra_codes=()):
        self.date = date
        self._bought_today.clear()
        codes = set(self.pf.positions) | set(extra_codes)
        self.bars = self.feed.bars(date, codes)

        # ---- 现金分红：真实入账 ----
        # ★ 必须在刷新 last_price 【之前】做：这里要用【前一交易日】的收盘价与因子。
        #
        # 后复权价隐含「分红自动再投资」，如果只记税不动仓位，
        # cash 与持仓市值的拆分就和现实不同 —— 现实里分红是真现金。
        # 解法：除权日把仓位的一小部分「卖成现金」：
        #     frac   = dps / close_raw(t-1)
        #     cash  += shares × close_hfq(t-1) × frac        = 真实股数 × dps
        #     shares = shares × (1 - frac)
        # 恰好等于真实分红金额；而且【真实股数不变】——
        #     real_new = shares(1-frac) × factor(t)
        #              = shares(1-frac) × factor(t-1)/(1-frac) = shares × factor(t-1)
        # 现金分红本来就不改变股数，所以这个模型是精确的，不是近似。
        #
        # 这样既拿到真实现金，又不必像「改用不复权」那样重新推导公司行动。
        # （曾用「1.4% 因子跳变在分红表里查不到」来论证这一点 —— 那个证据已撤回，
        #  实为检测伪影：送转发生在停牌期间，lag(factor) 把变化归到了复牌日。
        #  真正的理由见 README「为什么还不改用不复权」。）
        for c, p in list(self.pf.positions.items()):
            dps = self.feed.div.get((c, date))
            if not dps:
                continue
            f = self._prev_factor.get(c)
            if not f or not p.last_price:
                continue
            close_raw_prev = p.last_price / f
            if close_raw_prev <= dps:            # 派息超过股价，数据异常，跳过并留痕
                self.rejects.append((date, c, 'dividend', '每股派息 >= 前收盘价，跳过'))
                continue
            frac = dps / close_raw_prev
            cash_in = p.shares * p.last_price * frac      # == 真实股数 × dps
            for lot in p.lots:
                # 逐批记毛额 -> 卖出时按【该批】持有期精确定档（20%/10%/免），
                # 不必「按卖出比例摊」那种近似
                lot.div_gross += lot.shares * f * dps
                lot.shares *= (1 - frac)
                # ★ entry_price 【不缩】。缩了会把分红错记成价差收益：
                #   买 100 单位@10、分红 frac=2%（现金 +20、股数 98）、股价未动@10 卖出，
                #   不缩 -> pnl = 98×(10−10) = 0（价格贡献 0、分红贡献 20，归因干净）
                #   缩到 9.8 -> pnl = 98×(10−9.8) = 19.6（凭空出现价格涨幅）
                #   总额都对，但归因错了。trades 里的 ret/pnl 只该反映【价差】。
            self.pf.cash += cash_in
            self.div_cash_received += cash_in
            self.dividends.append({'date': date, 'code': c,
                                   'cash': float(cash_in)})

        for c, b in self.bars.items():
            p = self.pf.positions.get(c)
            if p is not None and b.close_hfq:
                p.last_price = b.close_hfq          # 停牌期间就用最后这个值估值
            if p is not None and b.factor:
                self._prev_factor[c] = b.factor

        # ---- 停牌计数（先于退市清算，退市那笔也算冻结过）----
        for c in self.pf.positions:
            if c in self.bars:
                self._susp_run[c] = 0
            else:
                r = self._susp_run.get(c, 0) + 1
                self._susp_run[c] = r
                self.frozen_days += 1
                if r > self.max_frozen_run:
                    self.max_frozen_run, self.max_frozen_code = r, c

        # ---- 退市清算：判据是权威 delist_date，不是「缺行」 ----
        for c in list(self.pf.positions):
            if c in self.bars:
                continue
            dl = self.feed.delist.get(c)
            if dl is not None and date >= dl:
                # 面板已覆盖退市整理期（末 30 日 -43%~-93% 的暴跌都在数据里），
                # 所以按最后已知价出清，无需额外折价 —— 崩盘已经计入。
                self._fill_sell(c, self.pf.positions[c].last_price,
                                slip=False, reason='delist')
                self.delisted.append((date, c))

    def refresh(self, codes):
        """策略临时要看某些票的当日行情（下单前）。"""
        missing = [c for c in codes if c not in self.bars]
        if missing:
            self.bars.update(self.feed.bars(self.date, missing))

    def mark_to_market(self):
        return self.pf.total_value

    # ================= 下单 =================
    def order_target_value(self, code, value):
        """仿聚宽 order_target_value：把该标的调到目标市值。

        ★ 加减仓【内置在这里】，不另开路径 —— 与「唯一卖出出口」同一原则。
          买卖各自只有一个成交函数（_buy / _fill_sell）：加仓是往 lots 追加一批，
          减仓是 FIFO 消耗若干批。副作用（滑点/佣金/印花税/红利税/日志）
          因此天然只写一遍。
        """
        self.refresh([code])          # 下单前确保有当日行情
        p = self.pf.positions.get(code)
        if value <= 0:
            return self._sell(code) if p else self._reject(code, 'sell', '无持仓')
        if p is None:
            return self._buy(code, value)
        ok, why, px = self.can_trade(code, 'sell')
        if px is None:
            # 拿不到当日价就无法算差额 —— 不做任何近似，直接拒单
            return self._reject(code, 'adjust', why or '无有效成交价')
        cur = p.shares * px
        if abs(value - cur) < px * 100:        # 差额不足一手，不动
            return Order(code, 'hold', 0, 0.0, True, '差额不足一手')
        if value > cur:
            return self._buy(code, value - cur)          # 加仓
        return self._sell(code, target_value=value)      # 减仓

    def order_target_percent(self, code, pct):
        """按总权益百分比调仓。pct=0 清仓。"""
        return self.order_target_value(code, self.pf.total_value * max(pct, 0.0))

    # ---------- 可交易性：撮合与策略预筛共用的【唯一】判据 ----------
    def can_trade(self, code, side):
        """返回 (可否成交, 原因, 成交价)。

        ★ 这是规则的唯一实现处。撮合走它，策略预筛也走它 ——
          聚宽在策略里写 filter_paused/limitup/limitdown 是【选股策略】
          （涨停的候选要被下一名替换，而不是丢掉名额），
          而能不能成交是【撮合规则】。两者需求不同但判据必须同一份，
          否则就会像旧引擎那样：买入侧过滤了、卖出侧漏了。
        """
        b = self.bars.get(code)
        if b is None:
            return False, '停牌/无行情', None
        # ★ 涨跌停价算不准时（上市首日 / 科创创业前 5 日 / 退市整理期首日等，
        #   真实规则分别是 +44%/-36%、无限制、无限制），面板会把 limit_ok 置 false。
        #   拿一个已知算错的涨跌停去拦交易比不拦更糟 —— 这些日子本就多是「无限制」，
        #   即不存在涨跌停。所以此时跳过涨跌停判定，但计数以保持可见。
        lim_ok = b.limit_ok is not False
        if not lim_ok:
            self.n_limit_unreliable += 1
        if side == 'sell':
            p = self.pf.positions.get(code)
            if p is not None and p.sellable(self.date) <= 0:
                return False, 'T+1，当日买入不可卖', None
            if self.phase == OPEN:
                if lim_ok and b.open_limit_down:
                    return False, '开盘跌停，无对手盘', None
                return (True, '', b.open_hfq) if b.open_hfq else (False, '无有效成交价', None)
            if lim_ok and b.limit_down:
                return False, '收盘跌停，无对手盘', None
            return (True, '', b.close_hfq) if b.close_hfq else (False, '无有效成交价', None)
        # buy
        if self.phase == OPEN:
            if lim_ok and b.open_limit_up:
                return False, '开盘涨停，买不进', None
            return (True, '', b.open_hfq) if b.open_hfq else (False, '无有效成交价', None)
        if lim_ok and b.limit_up:
            return False, '收盘涨停，买不进', None
        return (True, '', b.close_hfq) if b.close_hfq else (False, '无有效成交价', None)

    def filter_tradable(self, codes, side):
        """策略预筛用。会自动加载所需行情。"""
        self.refresh(codes)
        return [c for c in codes if self.can_trade(c, side)[0]]

    # ---------- 卖出 ----------
    def _sell(self, code, target_value=None):
        """target_value=None -> 清仓；否则减到该市值。"""
        ok, why, px = self.can_trade(code, 'sell')
        if not ok:
            return self._reject(code, 'sell', why)
        p = self.pf.positions[code]
        avail = p.sellable(self.date)
        # 卖出原因由撮合阶段推断：9:30 是调仓，盘中是「涨停打开」类的日内规则
        reason = 'rebalance' if self.phase == OPEN else 'intraday'
        if target_value is None:
            if avail < p.shares:
                # 部分被 T+1 锁住 —— 必须留痕，不能静默少卖
                self._reject(code, 'sell', 'T+1 锁仓，本次只能部分卖出')
            return self._fill_sell(code, px, shares=avail, reason=reason)
        want = p.shares - target_value / px
        fac = self.bars[code].factor or 1.0
        lots100 = int(min(want, avail) * fac / 100)      # 部分卖出按 100 真实股取整
        if lots100 <= 0:
            return self._reject(code, 'sell', '减仓量不足一手')
        return self._fill_sell(code, px, shares=lots100 * 100 / fac, reason=reason)

    def order_stop_sell(self, code, price=None):
        """止损专用清仓出口。与普通卖出的两点不同：

        1) **成交价可以指定**，但必须落在当日实际 [low_hfq, high_hfq] 区间内 ——
           区间外的价格当天根本没成交过，允许它等于让策略凭空造出成交。
           传 None 则退回常规逻辑（按相位取开盘/收盘价），即【日频止损】。
        2) **一字跌停才算无对手盘**。常规 sell 用「收盘跌停」拦单，对止损单
           不合适：只要当日 high > low，说明盘中在更高价位成交过，
           挂在那之上的止损单是打得掉的。一字板（high == low）才真卖不掉。

        reason 一律记 'stop'，与调仓卖出、炸板离场区分开 ——
        之前用 reason=='intraday' 的笔数差当触发次数是错的，
        那个口径把炸板离场也算了进去（2022 年出现 -1 就是这么来的）。
        """
        b = self.bars.get(code)
        if b is None or not b.close_hfq:
            return self._reject(code, 'sell', '停牌/无行情')
        p = self.pf.positions.get(code)
        if p is None or p.sellable(self.date) <= 0:
            return self._reject(code, 'sell', 'T+1，当日买入不可卖')
        if price is None:
            ok, why, px = self.can_trade(code, 'sell')
            if not ok:
                return self._reject(code, 'sell', why)
            return self._fill_sell(code, px, shares=p.sellable(self.date), reason='stop')
        lo, hi = b.low_hfq, b.high_hfq
        if lo is None or hi is None:
            return self._reject(code, 'sell', '无高低价')
        if b.limit_ok is not False and b.limit_down and hi <= lo:
            return self._reject(code, 'sell', '一字跌停，无对手盘')
        px = min(max(price, lo), hi)          # 夹到当日真实成交区间
        return self._fill_sell(code, px, shares=p.sellable(self.date), reason='stop')

    def _fill_sell(self, code, price, shares=None, slip=True, reason='rebalance'):
        """★ 唯一的卖出成交出口。清仓与减仓走同一条路。

        FIFO 消耗批次：最早买入的先卖。这样
          · 红利税能按【该批】的实际持有期定档（20%/10%/免），不用按比例摊
          · T+1 天然生效（当日买入的批在最后，且被 sellable() 排除在外）
        手续费按【整笔委托】算一次（min-5 是每笔而非每批），红利税按批加总。
        """
        p = self.pf.positions[code]
        eff = price * (1 - self.cost.slippage / 2) if slip else price
        want = p.shares if shares is None else min(shares, p.sellable(self.date))
        if want <= 0:
            return self._reject(code, 'sell', '无可卖股数（T+1 或已清仓）')
        # 成交量约束：卖出同样受限 -> 部分成交，剩余仓位留到下次。
        # 必须留痕：静默少卖会让「清仓失败」看起来像「策略选择继续持有」。
        # ⚠️ 退市清算（slip=False）走的票已不在面板里，没有当日成交额，
        #    而且那不是真成交 —— 不施加约束。
        _b = self.bars.get(code)
        cap = (self.cost.volume_ratio * (_b.amount or 0.0)) if (_b and slip) else 0.0
        if cap > 0 and want * eff > cap:
            capped = cap / eff
            self.n_vol_capped += 1
            self.vol_capped_amount += (want - capped) * eff
            self._reject(code, 'sell', '成交量上限，仅部分成交')
            want = capped
            if want <= 0:
                return self._reject(code, 'sell', '成交量上限不足成交')

        # 先按 FIFO 切出要卖的批次
        taken, left = [], want
        for lot in list(p.lots):
            if left <= 1e-9:
                break
            if lot.entry_date >= self.date:      # T+1：当日买入的批跳过
                continue
            use = min(lot.shares, left)
            taken.append((lot, use))
            left -= use

        sold = sum(u for _, u in taken)
        amt = sold * eff
        # 手续费按整笔委托算一次 —— min-5 是每笔而非每批
        fee = amt * self.cost.close_tax_at(self.date) + \
            max(amt * self.cost.commission, self.cost.min_commission)
        dtax = 0.0
        for lot, use in taken:
            frac = use / lot.shares
            dg = lot.div_gross * frac
            dtax += self._lot_div_tax(lot, dg)
            self.trades.append({
                'code': code, 'entry_date': lot.entry_date, 'exit_date': self.date,
                'holding_days': (self.date - lot.entry_date).days,
                'shares': use, 'entry_price': lot.entry_price, 'exit_price': eff,
                'ret': eff / lot.entry_price - 1,
                'gross_amount': use * eff, 'fee': fee * (use / sold) if sold else 0.0,
                'div_gross': dg, 'div_tax': self._lot_div_tax(lot, dg, count=False),
                'pnl': use * (eff - lot.entry_price),
                'reason': reason,
            })
            lot.shares -= use
            lot.div_gross -= dg
        p.lots = [l for l in p.lots if l.shares > 1e-9]

        # 逐笔流水：换回真实口径。★ `sold`/`eff` 是后复权记账单位，
        #   除以/乘以 factor 才是券商对账单上的股数与价格。
        _fac = self.bars[code].factor if code in self.bars else None
        # 🔴 **股数取整** —— 真实股数本来就是整数，而 `sold * _fac` 会带出
        #   浮点噪声（实测 3899.9999999999995）。那个数摆到页面上看着像
        #   "份额还有小数"，而它只是二进制表示的残渣。
        self.fills.append({
            'date': self.date, 'code': code, 'side': 'sell',
            'shares': int(round(sold * _fac)) if _fac else sold,
            'price': round(eff / _fac, 6) if _fac else eff,
            'amount': amt, 'fee': fee + dtax, 'reason': reason})
        self.pf.cash += amt - fee - dtax
        self.sell_amount += amt
        self.fee_paid += fee
        if not p.lots:
            self.pf.positions.pop(code, None)
            self._prev_factor.pop(code, None)
        return Order(code, 'sell', sold, amt, True, '')

    def _lot_div_tax(self, lot, div_gross, count=True):
        """按【该批】持有期定档。财税[2015]101号：≤1月 20% / 1月~1年 10% / >1年 免。"""
        if not div_gross or not self.cost.dividend_tax:
            return 0.0
        held = (self.date - lot.entry_date).days
        rate = 0.0
        for lim, r in DIV_TIERS:
            if held <= lim:
                rate = r
                break
        t = div_gross * rate
        if count:
            self.div_tax_paid += t
        return t

    # ---------- 买入 ----------
    def _buy(self, code, value):
        ok, why, px = self.can_trade(code, 'buy')
        if not ok:
            return self._reject(code, 'buy', why)
        b = self.bars[code]
        raw = b.open_raw if self.phase == OPEN else (
            b.close_hfq / b.factor if b.factor else None)
        if not raw or not b.factor:
            return self._reject(code, 'buy', '无有效成交价')

        # ★ 整手约束是对【真实股数】的，不是对后复权记账单位。
        #   套在后复权价上等价于要求后复权价 < value/100，
        #   老股票(因子 20~30、后复权价上百元)会被整只静默跳过。
        raw_eff = raw * (1 + self.cost.slippage / 2 + self.cost.buy_slippage)
        # ★ 成交量约束：单笔最多吃掉当日成交额的 volume_ratio。
        #   聚宽 order_volume_ratio 默认 0.25。没有它，资金容量分析全是假的。
        want = value
        cap = self.cost.volume_ratio * (b.amount or 0.0)
        if cap > 0 and value > cap:
            value = cap
        lots = int(value / (raw_eff * 100))
        if lots <= 0:
            return self._reject(code, 'buy',
                                '成交量上限不足一手' if value < want else '资金不足一手')
        if value < want:
            self.n_vol_capped += 1
            self.vol_capped_amount += want - lots * 100 * raw_eff
            # 部分成交也要留痕：静默少买会让「资金容量不足」看不见
            self._reject(code, 'buy', '成交量上限，仅部分成交')
        cost_amt = lots * 100 * raw_eff
        fee = cost_amt * self.cost.open_tax + max(cost_amt * self.cost.commission,
                                                  self.cost.min_commission)
        if cost_amt + fee > self.pf.cash:
            return self._reject(code, 'buy', '现金不足')
        self.pf.cash -= cost_amt + fee
        self.buy_amount += cost_amt
        self.fee_paid += fee
        shares = lots * 100 / b.factor          # 换算回后复权记账单位
        # 逐笔流水：`lots*100` 是真实股数、`raw_eff` 是不复权成交价（含滑点）
        self.fills.append({'date': self.date, 'code': code, 'side': 'buy',
                           'shares': int(lots * 100), 'price': round(raw_eff, 6),
                           'amount': cost_amt, 'fee': fee, 'reason': 'open'})
        lot = Lot(shares=shares, entry_date=self.date,
                  entry_price=px * (1 + self.cost.slippage / 2
                                    + self.cost.buy_slippage))
        p = self.pf.positions.get(code)
        if p is None:
            self.pf.positions[code] = Position(
                code=code, lots=[lot], last_price=b.close_hfq or px)
        else:
            p.lots.append(lot)                  # 加仓 = 追加一批，不合并成平均成本
            self.n_adds += 1
            p.last_price = b.close_hfq or p.last_price
        self._prev_factor[code] = b.factor
        self._bought_today.add(code)
        return Order(code, 'buy', shares, cost_amt, True, '')

    def frozen_now(self):
        """当前仍在停牌（按陈价挂账）的持仓。期末若非空，最终权益不可验证。"""
        return {c: self._susp_run.get(c, 0) for c in self.pf.positions
                if self._susp_run.get(c, 0) > 0}

    def _reject(self, code, side, reason):
        self.rejects.append((self.date, code, side, reason))
        return Order(code, side, 0, 0.0, False, reason)


class RecordingBroker(Broker):
    """**只记录委托、不撮合成交** —— 实盘模块用它捕获策略「想做什么」。

    ## 为什么是换 broker，而不是在实盘模块里重写规则

    止损（`stop_check`）、炸板离场（`check_limit_up`）、调仓（`rebalance`/`trade`）
    三条规则全都依赖引擎内部状态：`g.pos_state`、`g.high_limit`、
    `pos[code].entry_price`、`context.portfolio.total_value`。在实盘模块里
    照抄一遍就是**第二份实现** —— 本项目已经因为「同一件事两处写」吃过亏
    （旧引擎三处各写一遍卖出，交易日志只挂在其中一处，一次归因诊断全错）。

    换掉**成交出口**是唯一不复制规则的办法：策略照常跑它自己的代码路径，
    我们只是把最后那一下「真的买/卖」换成「记下来」。

    ## 记什么

    `orders` 每项：`{code, kind, target_value, price, phase, date}`
      kind: 'target'（order_target_value/percent）/ 'stop'（order_stop_sell）
      target_value == 0 -> 清仓；> 0 -> 目标市值

    ★ 不填单意味着 portfolio 保持播种时的状态，后续任务看到的持仓仍是
      **你的真实持仓** —— 这正是预览要的语义（"如果现在执行，会发生什么"）。
      副作用：同一天内策略若先卖后买、且买入依赖卖出腾出的现金，
      记到的 target_value 会偏小。所以**下单股数由实盘模块按真实现金重算**，
      这里只取「它想动哪些票、方向是什么」。
    """

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.orders = []

    def _rec(self, code, kind, value, price=None):
        self.orders.append({
            'code': code, 'kind': kind, 'target_value': float(value),
            'price': price, 'phase': self.phase, 'date': self.date,
        })
        return Order(code, 'sell' if not value else 'buy', 0, 0.0, True, 'recorded')

    def order_target_value(self, code, value):
        return self._rec(code, 'target', value)

    def order_target_percent(self, code, pct):
        return self._rec(code, 'target', self.pf.total_value * pct)

    def order_stop_sell(self, code, price=None):
        return self._rec(code, 'stop', 0.0, price)
