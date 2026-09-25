# -*- coding: utf-8 -*-
"""模拟盘【盘中推进】：「今天那根 bar」的唯一正本（2026-09-22）。

用户："这种模拟盘数据，如果应该按照交易计划的具体分时去推进，如果是开盘
竞价，应该调接口拿开盘数据自动推进。"

在此之前 `paper.advance` 的终点是**最新数据日**，而今天的日线要等收盘后
16:00~20:00 那个窗口才落地 —— 于是页面上同时摆着「今天要卖 1 买 3」和
「推进到 09-21」，**两个数看着自相矛盾，而没有任何地方说为什么**
（用户就是这么问出来的）。

━━ 为什么只做【开盘竞价】那一档，不做"任意分时" ━━

引擎**没有分时线**：`_phase()` 把 `09:26~09:30` 归 OPEN（成交价 = 后复权
开盘价）、`09:31~14:59` 全归 INTRADAY 并**一律用当日收盘价代理**。
所以"14:00 推进"在这个引擎里拿不到 14:00 的价 —— 它会用当日收盘价，
而收盘还没发生。**唯一有真实数据支撑的时点就是开盘竞价**，
而 froec_traded 的 `rebal_time` 默认正是 `09:30`。

★ 所以这里的规则是：**只把今天推到 OPEN 相位为止**。当天 14:00 的两个任务
  （`check_limit_up` 炸板离场 / `stop_check` 止损）读的是**收盘派生量**，
  09:31 根本不存在 —— 它们留到日终权威数据落地后再跑。

━━ 判据都是实测的，不是推的（2026-09-22） ━━

| 要证的 | 实测 |
|---|---|
| 腾讯今开 == 面板 open | 09-18 / 09-21 各 35 只，**100% 逐位相同**，最大差 0.0 |
| OPEN 相位能看到什么 | `guard.current()` **只给** open_hfq/open_raw/factor/open_limit_up/open_limit_down；close_hfq/limit_up/limit_down/sealed/touch_up 要到 INTRADAY 才 update。**PIT 纪律本来就写在 guard 里** |
| broker 在 OPEN 读什么 | 只读 limit_ok / open_limit_up|down / open_hfq（`limit_up`/`close_hfq` 都在非 OPEN 分支，前面已 return） |
| 今日涨跌停价可重建 | 昨收 × (1 ± 昨天 `limit_pct`)，09-01 起 72880 行**对上 99.67%** |
| 那 0.33% 是什么 | **241 行全是除权日，非除权日 0 行** |
| 「今天除权了没有」怎么判 | 本地数据**前瞻不了**（`adjust_factor` 与 `feed.div` 都只到 09-21 / 09-09）。判据改用**快照自带的 `preclose`**：正常日它 == 面板昨天 `close_bfq`，除权日它就是**除权后**价。实测今天 41 只里对上 36、**对不上 0** |

━━ 🔴 不可知的字段放【哨兵】，不给 None ━━

`Bar` 有 17 个字段，而今天只有 open/high/low/现价/成交额是真实的。
给 None 是最坏的做法 —— 本项目栽过：「`guard.current()` 漏一列 ->
策略侧永远收到 None -> 规则整段空转，**且不报错**」。
所以收盘派生量一律放 `UNKNOWN`：**读到就当场抛错**。
★ 这与「宁可报错，也不给一个看着正常的 False」是同一条
  （`is_rebalance_day` 对表外日期抛错那次）。
★ 而它同时是一道**PIT 自证**：OPEN 相位读收盘派生量本来就是未来函数，
  哨兵一响就是抓到了真问题，不是误报。
"""

import datetime

from . import base as _base

# 引擎里 `09:26 ~ 09:30` 属 OPEN。盘中推进只跑到这一档为止。
PHASE_CUTOFF = '09:30'

# ★ 竞价那一分钟的快照要先落盘，所以最早从 09:31 起才推得动。
EARLIEST = '09:31'


class Unknown(object):
    """「今天还不知道」的哨兵 —— 任何使用都抛错，不静默当 None/False。"""

    __slots__ = ('field',)

    def __init__(self, field='?'):
        self.field = field

    def _boom(self, *_a, **_k):
        raise _base.LiveError(
            '盘中推进：`%s` 是【收盘派生量】，开盘时不存在 —— '
            '读它就是未来函数。这一档应当留到日终权威数据落地后再跑'
            '（同「宁可报错，也不给一个看着正常的 False」那条）。' % self.field)

    __bool__ = __nonzero__ = _boom
    __float__ = __int__ = _boom
    __lt__ = __le__ = __gt__ = __ge__ = _boom
    __add__ = __sub__ = __mul__ = __truediv__ = _boom
    __radd__ = __rsub__ = __rmul__ = __rtruediv__ = _boom

    def __repr__(self):
        return '<今天还不知道: %s>' % self.field


def _unk(*names):
    return {n: Unknown(n) for n in names}


# 收盘派生量：今天一个都不可知
CLOSE_DERIVED = ('limit_up', 'limit_down', 'sealed', 'touch_up',
                 'change_pct', 'ma5', 'ma20')


def can_advance_intraday(panel_last, day=None, now=None, root=None,
                         explicit=False):
    """今天能不能盘中推进 -> `(day, why_not)`；能推时 `why_not` 是 None。

    🔴 判据一条都不许猜，全部可证：
      ① 今天必须是交易日（`tdx.raw_holidays` 推的那份，含未来日）
      ② 必须过了 09:31 —— 竞价那一分钟的快照要先落盘
      ③ 面板最新日必须**早于**今天 —— 等于今天说明日线已经落地了，
         那就走正常推进（权威数据永远优先于盘中近似）
    """
    d = str(day or datetime.date.today())[:10]
    # ★ 交易日历用 `calendar_days()`（tdx.raw_holidays 推的那份，**含未来日**）
    #   —— 不能用 `std/trading_calendar.parquet`，它只到最后一个**有数据**的
    #   交易日，拿它判"今天"会天天判成"不是交易日"、于是永远不推进
    #   （那条坑 `is_stale.py` 记过）。
    # 🔴 取不到日历时**不推进**并说出来 —— "不知道"不等于"确定是交易日"。
    try:
        days = set(str(x)[:10] for x in _base.calendar_days())
    except Exception as e:                                  # noqa: BLE001
        return d, '交易日历读不出来（%s）—— 盘中不猜' % str(e)[:60]
    if not days:
        return d, '交易日历是空的 —— 盘中不猜'
    if d not in days:
        return d, '%s 不是交易日' % d
    # ★ `explicit=True` 表示调用方**点名了哪一天**（复盘 / 判据构造）——
    #   那时"现在几点"无关：那一天的快照早就落盘了。
    #   其余三条判据照旧生效，**一条都不放过**。
    if not explicit:
        hhmm = (now or datetime.datetime.now()).strftime('%H:%M')
        if hhmm < EARLIEST:
            return d, '还没到 %s —— 开盘竞价那一分钟的快照要先落盘' % EARLIEST
    if str(panel_last)[:10] >= d:
        return d, ('面板已经有 %s 的日线了 —— 走正常推进，'
                   '权威数据优先于盘中近似' % d)
    return d, None


def today_bars(day, prev_day, codes, root=None):
    """今天那根【盘中】bar。返回 `(bars, skipped)`。

    `bars` 是 `{code: feed.Bar}`，`skipped` 是 `[{code, why}]` ——
    🔴 **取不到的整只剔除、并把原因带出来**，不猜一个数填上去
    （同「拿不到分红那一格标『查不到』」「停牌/取不到的整只剔除，不算 0%」）。
    """
    from assay.feed import Bar
    from assay import realtime as _rt
    from assay import paths as _paths
    import duckdb

    codes = sorted(set(codes or ()))
    if not codes:
        return {}, []
    # 🔴 **快照库只有一个**（`datalake/rt/`，顶层独立目录）——
    #   `root` 只用于**面板**。传给 `latest` 的话 ETF 策略会去
    #   `etf_lake/rt/` 找，那个目录根本不存在 -> 每一只都"没有快照"、
    #   全被跳过，**而它不报错**（实测 40/40 全跳）。
    snap = _rt.latest(codes, day=day) or {}

    con = duckdb.connect(':memory:')
    rows = con.execute(
        "SELECT jq_code, close_bfq, preclose, limit_up, limit_pct, hfq_factor,"
        " limit_rule_ok FROM %s WHERE date = DATE '%s' AND jq_code IN ('%s')"
        % (_paths.panel_sql(root), str(prev_day)[:10], "','".join(codes))
    ).fetchall()
    pan = {r[0]: r for r in rows}

    out, skipped = {}, []

    def _skip(c, why):
        skipped.append({'code': c, 'why': why})

    for c in codes:
        s, p = snap.get(c), pan.get(c)
        if not s or not s.get('open') or not s.get('preclose'):
            _skip(c, '今天还没有快照（停牌 / 没抓到 / 未开盘）')
            continue
        if not p:
            _skip(c, '面板里没有 %s 那天的行 —— 基准取不到' % str(prev_day)[:10])
            continue
        _code, close_bfq, pre_y, up_y, pct, fac, rule_ok = p
        if not fac or not close_bfq:
            _skip(c, '昨天那行缺复权因子或收盘价')
            continue
        # 🔴 **除权守卫**：快照的 `preclose` 是交易所给的今日基准价 ——
        #   正常日它 == 面板昨天的 `close_bfq`，**除权日它是除权后价**。
        #   而今天的复权因子本地**前瞻不了**（`adjust_factor` 也只到昨天），
        #   所以一旦不等就**跳过这只**：拿昨天的因子去算除权后的价，
        #   股数与成交价会一起错，**而它不报错**。
        pre = float(s['preclose'])
        if abs(pre - float(close_bfq)) > 0.005:
            _skip(c, ('今日基准价 %.3f 与面板昨收 %.3f 不一致 —— 多半今天除权了，'
                      '而今天的复权因子本地还没有。这只留到日终权威数据再算'
                      % (pre, float(close_bfq))))
            continue
        # ★ 涨跌停的**口径与舍入位数逐只自证**：用昨天那一行反推。
        # 🔴🔴 两个 lake 的 `limit_pct` **量纲不一样**（实测 2026-09-22）：
        #     主面板（股票）  0.20   <- 小数
        #     etf_lake       10.0   <- **百分数**
        #   照小数口径算 ETF 会得到 `pre × 11` 的涨停价 —— 那不是"差一点"，
        #   是**十一倍**。而它在 broker 里只表现为"开盘没涨停、照常成交"，
        #   **一个字都不报**（同「标错量纲页面会显示 0.05%」那条）。
        # ★ 所以不写死任何一种：把 (口径, 舍入位数) 四种组合拿去**复现昨天
        #   那一行的权威 `limit_up`**，对上哪个用哪个；一个都对不上就
        #   **跳过这只**（不猜）。舍入位数也顺带解决了 ETF 是 0.001 这件事。
        pctf, nd = None, None
        if pct and pre_y and up_y:
            for _c in (float(pct), float(pct) / 100.0):
                for r in (2, 3):
                    if abs(round(float(pre_y) * (1 + _c), r)
                           - float(up_y)) < 1e-9:
                        pctf, nd = _c, r
                        break
                if nd is not None:
                    break
        if nd is None:
            _skip(c, '昨天那行反推不出涨跌停的口径/舍入 —— 盘中不猜涨跌停')
            continue
        fac = float(fac)
        up = round(pre * (1 + pctf), nd)
        dn = round(pre * (1 - pctf), nd)
        o = float(s['open'])
        px = float(s.get('price') or o)
        hi = float(s.get('high') or o)
        lo = float(s.get('low') or o)
        real = {
            'open_raw': o,
            'factor': fac,
            'open_hfq': round(o * fac, 4),
            # ★ `close_hfq` 只用于**盘中估值**（引擎日末 mark-to-market），
            #   OPEN 相位的成交价走 `open_hfq` —— 不拿现价当"收盘"去做判定。
            'close_hfq': round(px * fac, 4),
            'high_hfq': round(hi * fac, 4),
            'low_hfq': round(lo * fac, 4),
            'amount': float(s.get('amount') or 0.0),
            'limit_ok': bool(rule_ok),
            'open_limit_up': o >= up - (0.5 * 10 ** -nd),
            'open_limit_down': o <= dn + (0.5 * 10 ** -nd),
        }
        real.update(_unk(*CLOSE_DERIVED))
        out[c] = Bar(**real)
    return out, skipped


class IntradayFeed(object):
    """在 `PanelFeed` 外面**追加今天这一天**，其余一律原样转发。

    🔴 只接管跟"今天"有关的那 5 个成员（`trading_days` / `prev_trading_day`
      / `bars` / `div` / `delist`）—— `query` / `universe` / `snapshot` /
      `fundamentals` / `nth_prev_day` 那些**必须转发**：面板里没有今天的行，
      as-of 天然落到昨天，**而那正是这份计划的口径**（信号里的
      `data_asof` 就是昨天）。自己另写一套取数的话，"盘中算出来的候选池
      与昨晚那份信号不一样"迟早发生，**而它不报错**。

    ★ `div` / `delist` 是**普通 dict 属性**（`{(code,date): dps}` /
      `{code: date}`），转发过去今天天然没有条目 —— 而"今天除权"那一档
      已经在 `today_bars` 里逐只挡掉了。
    ★ `trading_days` 必须在**建引擎之前**就带上今天：`_build_ordinals`
      在 `_boot` 里跑，它只覆盖 `feed.trading_days` —— 今天不在表里的话
      `_due` 对它取 (0,0)、与任何 weekday 都不等，**静默说"今天不是调仓日"**
      （那个坑 2026-09-07 修过一次）。
    """

    def __init__(self, inner, day, prev_day, root=None, fetch=True):
        self._inner = inner
        self._day = str(day)[:10]
        self._prev = str(prev_day)[:10]
        self._root = root
        self._fetch = fetch
        self._cache = {}          # code -> Bar 或 None（取不到）
        self.skipped = []         # [{code, why}]，**要带给页面**
        self._days = list(inner.trading_days) + [
            datetime.date.fromisoformat(self._day)]

    def __getattr__(self, k):
        return getattr(self._inner, k)          # 其余一律转发

    @property
    def trading_days(self):
        return self._days

    def prev_trading_day(self, d):
        if str(d)[:10] == self._day:
            return self._inner.trading_days[-1]
        return self._inner.prev_trading_day(d)

    def bars(self, date, codes):
        """今天那一天**惰性**取，其余转发。

        🔴 为什么必须惰性：`today_bars` 要代码清单，而清单是**策略运行时**
          才问出来的（`filter_tradable` 一次传整个候选池）。先算好一份的话
          就得预知策略要问谁 —— 而 `bars(date, codes)` 本来就把 codes
          递过来了，照它取就对。
        🔴 **缺的要当场补抓一次**（腾讯批量一次能拼 900 个，所以是 1 个请求）：
          一只票今天没有快照 -> `can_trade` 返回「停牌/无行情」->
          策略把它从候选池里**丢掉** -> 盘中选出来的清单与昨晚那份信号
          不一样，**而它不报错**。这与 `_rt_ensure` 是同一条思路
          （"这几只今天一条数据都没有 -> 立刻补一次"）。
        """
        if str(date)[:10] != self._day:
            return self._inner.bars(date, codes)
        want = [c for c in (codes or ()) if c not in self._cache]
        if want:
            if self._fetch:
                try:
                    from assay import realtime as _rt
                    # 🔴🔴 **不传 root** —— 快照库只有一个（`datalake/rt/`），
                    #   与下面 `today_bars` 的 `latest(codes, day=day)` **必须
                    #   是同一个库**。传 `self._root` 的话 ETF 策略会去
                    #   `etf_lake/rt/` 查与写，而读的是主库 -> **两个库**：
                    #   补抓"成功"了、写进了一个没人读的地方，于是那几只
                    #   永远没有 bar。更糟的是 `missing()` 的收敛设计
                    #   （抓到了就不再 missing）让它**只错一次就再也不重试** ——
                    #   实测 2026-09-24 的 a4/a5：8 个目标只买进 3 只，
                    #   另外 5 只每 5 分钟推一次、推了一整天都买不进，
                    #   **而它不报错**（拒单只写"停牌/无行情"）。
                    #   ★ `today_bars` 那边的注释早就把这条写出来了，
                    #     修的却只有"读"那一侧 —— 这是同一件事的另一半。
                    miss = _rt.missing(want, day=self._day)
                    if miss:
                        _rt.ensure_codes(miss, max_bars=0, day=self._day)
                except Exception as e:                      # noqa: BLE001
                    self.skipped.append({'code': '(补抓)', 'why': str(e)[:120]})
            got, sk = today_bars(self._day, self._prev, want, self._root)
            for c in want:
                self._cache[c] = got.get(c)
            self.skipped.extend(sk)
        return {c: self._cache[c] for c in (codes or ()) if self._cache.get(c)}
