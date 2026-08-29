#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""数据访问层。**这是本项目唯一知道 datalake 在哪、表长什么样的地方。**

依赖方向（不可反向）：
    assay ---依赖--> datalake
    datalake 对 assay 一无所知。

所以 datalake 的路径不硬编码在引擎里，由 ASSAY_DATALAKE 环境变量或构造参数给出。
换数据源只需另写一个实现同样接口的 Feed，引擎与策略都不用动。

## 为什么不仿聚宽的 get_fundamentals(query(...)) ORM

我们的 gold 层已经是「(date, code) 宽表、每列 as-of 正确」的形态，
直接给策略 SQL 或 DataFrame 比重建一套 ORM 更简单也更诚实 ——
仿 ORM 只会把聚宽的口径坑（单季/累计、pubDate 语义）再抄一遍。
策略拿到的是同一张 panel，想怎么筛就怎么筛。
"""
import os
from collections import namedtuple

import duckdb

# 撮合与风控需要的字段。策略要别的列走 query()/panel()，不必挤在这里。
Bar = namedtuple('Bar', 'open_hfq close_hfq open_raw factor '
                        'open_limit_up open_limit_down limit_up limit_down sealed '
                        'amount limit_ok '
                        'high_hfq low_hfq')

_BAR_COLS = """
    round(open * hfq_factor, 4)      AS open_hfq,
    close_hfq,
    open                             AS open_raw,
    hfq_factor                       AS factor,
    is_open_limit_up                 AS open_limit_up,
    is_open_limit_down               AS open_limit_down,
    is_limit_up                      AS limit_up,
    is_limit_down                    AS limit_down,
    (low >= limit_up - 0.005)        AS sealed,
    -- ★ 成交量约束用 amount(元) 而非 volume：实测 volume 的单位是【股 × 100】，
    --   除以 100 才是股数（交叉验证：volume/100 算换手率得 0.4731，
    --   面板 turnover 列 0.473107，精确吻合）。用 amount 元对元，绕开单位陷阱。
    amount,
    -- 面板自带的「本行涨跌停价算得准不准」标记。0.0229% 的行为 false，
    -- 集中在【上市首日 1474 行】【上市 1-5 日 496 行】【退市整理期首日 105 行】——
    -- 这些日子的真实规则是「主板 IPO 首日 +44%/-36%」「科创创业前 5 日无限制」
    -- 「退市整理期首日无限制」，而面板按常规 10%/20% 算，必然算错。
    -- ★ 引擎必须尊重这个标记：拿一个已知算错的涨跌停去拦交易，比不拦更糟。
    limit_rule_ok                    AS limit_ok,
    -- 盘中最高/最低（后复权）。移动止损要 peak，吊灯止损要 ATR 的真实波幅。
    -- ★ 与 close_hfq 同属【收盘后】才知道的量，guard.current() 里只在
    --   INTRADAY/CLOSE 相位放出，开盘相位取不到（那是未来信息）。
    round(high * hfq_factor, 4)      AS high_hfq,
    round(low  * hfq_factor, 4)      AS low_hfq
"""


# ---------- datalake 表目录 ----------
# ★ 这是【全项目唯一】写着 std/ 下文件名的地方。策略在 SQL 里用 {t_xxx} 占位，
#   不拼路径 —— datalake 改文件名 / 改目录只需要动这张表。
#
#   为什么是占位符而不是「返回 DataFrame 的取数方法」：这些表在策略里是
#   【嵌在 SQL 里跟面板做 JOIN 的】（如 JOIN {t_beta} b ON b.code=...），
#   换成 Python 方法会把一条 SQL 拆成多次取数 + 手工合并，既慢又容易错。
#   占位符保持 SQL 组合能力不变，只把「文件名叫什么」这条知识收回来。
_STD_TABLES = {
    't_universe':  'security_universe',
    't_indicator': 'fin_indicator_q',
    't_quarterly': 'fin_quarterly',
    't_dividend':  'dividend',
    't_beta':      'beta_daily',
    't_jqfactor':  'jqfactor_q',
}


def std_tables(root):
    """{t_xxx: read_parquet(...)}，供 SQL format 展开。"""
    return {k: "read_parquet('%s/std/%s.parquet')" % (root, v)
            for k, v in _STD_TABLES.items()}


class PanelFeed:
    """datalake 的 mart/panel_daily 适配器。"""

    def __init__(self, start, end, root=None):
        self.root = root or os.environ.get('ASSAY_DATALAKE') or \
            os.path.join(os.path.dirname(os.path.dirname(
                os.path.dirname(os.path.abspath(__file__)))), 'datalake')
        if not os.path.isdir(self.root):
            raise SystemExit('找不到 datalake：%s\n用 ASSAY_DATALAKE 指定' % self.root)
        # ★ 面板构建是先写盘后校验的，失败时坏数据已在磁盘上。
        #   构建脚本会落 _FAILED 标记，这里直接拒绝加载 ——
        #   宁可跑不起来，也不要在坏面板上产出「看着正常」的结果。
        _mk = os.path.join(self.root, 'mart', 'panel_daily', '_FAILED')
        if os.path.exists(_mk):
            raise SystemExit(
                '面板校验未通过，拒绝加载：\n%s\n修复后重跑 '
                'datalake/build/build_panel_daily.py'
                % open(_mk, encoding='utf-8').read().strip())
        self.start, self.end = str(start), str(end)
        self.panel = "read_parquet('%s/mart/panel_daily/panel_*.parquet')" % self.root
        self.con = duckdb.connect(':memory:')

        # 物化并按 date 排序：zone map 让「按日取数」变成小范围扫描，
        # 否则日频循环要对 8.5M 行的 parquet 全扫 2000+ 次。
        self.con.execute("""CREATE TABLE bars AS
            SELECT date, jq_code AS code, %s
            FROM %s WHERE date BETWEEN DATE '%s' AND DATE '%s'
            ORDER BY date""" % (_BAR_COLS, self.panel, self.start, self.end))

        self._days = [r[0] for r in self.con.execute(
            'SELECT DISTINCT date FROM bars ORDER BY 1').fetchall()]
        self._idx = {d: i for i, d in enumerate(self._days)}

        # 回测首日也要能选股 —— 它需要【前一交易日】的数据，而那天在区间之外。
        # 旧引擎在这里静默跳过了首次建仓，2016 年因此空仓躲过熔断、凭空多出十几个点。
        self._day_before = self.con.execute(
            "SELECT max(date) FROM %s WHERE date < DATE '%s'"
            % (self.panel, self._days[0])).fetchone()[0] if self._days else None

        # 权威退市日。2200-01-01 是「未退市」哨兵，不剔除会把在市股票判成已退市。
        self.delist = {r[0]: r[1] for r in self.con.execute(
            "SELECT code, delist_date::DATE FROM read_parquet('%s/std/security_universe.parquet') "
            "WHERE delist_date IS NOT NULL AND delist_date < DATE '2100-01-01'"
            % self.root).fetchall()}

        # 现金分红除权事件。★ bonus_ratio_rmb 实测是【每 10 股派息】——
        # 用复权因子在除权日的跳变反推，30,693 个纯现金分红事件比值精确为 1.0。
        # 送股/转增不产生现金，不在此列。
        self.div = {}
        for c, d, dps in self.con.execute(
                "SELECT code, a_xr_date::DATE, bonus_ratio_rmb / 10.0 "
                "FROM read_parquet('%s/std/dividend.parquet') "
                "WHERE plan_progress = '实施方案' AND a_xr_date IS NOT NULL "
                "AND bonus_ratio_rmb > 0" % self.root).fetchall():
            self.div[(c, d)] = self.div.get((c, d), 0.0) + dps

    # ---------- 数据版本指纹 ----------
    # 归档记了 datalake 路径，但没记【数据内容】—— panel 重建一次，
    # 同一份策略结果就会变，而归档看不出来。这是「代码哈希」在数据侧的对应缺口。
    #
    # ⚠️ 取舍：用 (相对路径, 字节数, mtime) 而不是内容哈希。panel 有 4GB+，
    #    每次回测算内容哈希不可接受。代价是「重建出内容完全相同的文件」会被
    #    误报为变了 —— 但这个方向是安全的：宁可误报，不可漏报。
    #    真要逐字节确认时再单独跑内容哈希。
    FINGERPRINT_PARTS = (
        ('panel', 'mart/panel_daily/panel_*.parquet'),
        ('std',   'std/*.parquet'),
        ('index', 'raw/tdx/kline/index_*.parquet'),
    )

    def fingerprint(self):
        """返回 {'overall': sha12, 'parts': {name: {...}}}。
        分部件给哈希，这样 diff 能指出【哪一部分】变了，而不只是「变了」。"""
        import glob
        import hashlib
        parts, allsig = {}, []
        for name, pat in self.FINGERPRINT_PARTS:
            files = sorted(glob.glob(os.path.join(self.root, pat)))
            sig, total, newest = [], 0, 0
            for f in files:
                st = os.stat(f)
                rel = os.path.relpath(f, self.root)
                sig.append('%s|%d|%d' % (rel, st.st_size, st.st_mtime_ns))
                total += st.st_size
                newest = max(newest, st.st_mtime_ns)
            blob = '\n'.join(sig)
            h = hashlib.sha256(blob.encode()).hexdigest()[:12]
            parts[name] = {'hash': h, 'n_files': len(files),
                           'total_bytes': total,
                           'newest_mtime': (
                               __import__('datetime').datetime.fromtimestamp(
                                   newest / 1e9).isoformat(timespec='seconds')
                               if newest else None)}
            allsig.append('%s=%s' % (name, h))
        overall = hashlib.sha256('|'.join(allsig).encode()).hexdigest()[:12]
        return {'overall': overall, 'parts': parts}

    # ---------- 基准指数 ----------
    def benchmark(self, code):
        """取基准指数的日线收盘。接受聚宽代码（000905.XSHG）或 tdx 代码（sh000905）。

        指数在 raw/tdx/kline/index_*.parquet，不在 panel 里 ——
        panel 是「(date, code) 股票宽表」，塞指数进去会让 as-of 语义变浑。
        """
        sym = code
        if '.' in code:
            num, mkt = code.split('.')
            sym = ('sh' if mkt.upper() == 'XSHG' else 'sz') + num
        rows = self.con.execute(
            "SELECT date, close FROM read_parquet('%s/raw/tdx/kline/index_*.parquet') "
            "WHERE symbol = '%s' AND date BETWEEN DATE '%s' AND DATE '%s' ORDER BY 1"
            % (self.root, sym, self.start, self.end)).fetchall()
        if not rows:
            raise SystemExit('基准 %s (tdx=%s) 没有数据' % (code, sym))
        # ★ 基点必须取回测首日的【前一交易日】收盘，不是首日收盘。
        #   实测：聚宽 FROEC 报的基准收益 4.76% = 2015-12-31 -> 2026-08-07，
        #   而用首日(2016-01-04)收盘做基点得 14.27% —— 差 9.5pp。
        #   因为 2016-01-04 是熔断日(-8.99%)，用首日收盘做基点等于把它排除在基准之外。
        #   这与「回测首日不调仓」是同一类【边界差一天】的错。
        base = self.con.execute(
            "SELECT close FROM read_parquet('%s/raw/tdx/kline/index_*.parquet') "
            "WHERE symbol = '%s' AND date < DATE '%s' ORDER BY date DESC LIMIT 1"
            % (self.root, sym, self.start)).fetchone()
        return {r[0]: r[1] for r in rows}, (base[0] if base else rows[0][1])

    # ---------- 日历 ----------
    @property
    def trading_days(self):
        return self._days

    def prev_trading_day(self, d):
        i = self._idx.get(d)
        if i is None:
            return None
        return self._days[i - 1] if i > 0 else self._day_before

    # ---------- 撮合用行情 ----------
    def bars(self, date, codes):
        """只取需要的股票，避免每日全表扫。"""
        if not codes:
            return {}
        q = "','".join(codes)
        rows = self.con.execute(
            "SELECT code, %s FROM bars WHERE date = DATE '%s' AND code IN ('%s')"
            % (','.join(Bar._fields), date, q)).fetchall()
        return {r[0]: Bar(*r[1:]) for r in rows}

    def bar_range(self, codes, start, end):
        """[start, end] 区间的 (date, high_hfq, low_hfq, close_hfq) 序列。

        吊灯止损建仓时要用【入场前】的 N 根 K 线来起 ATR 与 HH ——
        只用持有期内的数据会让吊灯在建仓后头十几天完全不设防。
        bars 是内存物化表且按 date 排序，区间取数很便宜。
        """
        if not codes:
            return {}
        q = "','".join(codes)
        rows = self.con.execute(
            "SELECT code, date, high_hfq, low_hfq, close_hfq FROM bars "
            "WHERE date >= DATE '%s' AND date <= DATE '%s' AND code IN ('%s') "
            "ORDER BY code, date" % (start, end, q)).fetchall()
        out = {}
        for c, d, h, l, cl in rows:
            out.setdefault(c, []).append((d, h, l, cl))
        return out

    # ---------- 策略用数据接口 ----------
    def query(self, sql, **kw):
        """对 datalake 直接跑 SQL。`{panel}` / `{root}` / `{t_*}` 会被替换。"""
        f = dict(panel=self.panel, root=self.root, **std_tables(self.root))
        f.update(kw)          # 调用方传的同名键优先
        return self.con.execute(sql.format(**f)).df()

    def panel_at(self, date, cols='*', where='1=1', order=None, limit=None):
        sql = "SELECT %s FROM %s WHERE date = DATE '%s' AND (%s)" % (cols, self.panel, date, where)
        if order:
            sql += ' ORDER BY ' + order
        if limit:
            sql += ' LIMIT %d' % limit
        return self.con.execute(sql).df()

    def had_limit_up(self, codes, start, end):
        """[start, end] 区间内曾经收盘涨停过的代码集合（20 日黑名单用）。"""
        if not codes:
            return set()
        q = "','".join(codes)
        return {r[0] for r in self.con.execute(
            "SELECT DISTINCT code FROM bars WHERE date > DATE '%s' AND date <= DATE '%s' "
            "AND code IN ('%s') AND limit_up" % (start, end, q)).fetchall()}

    def nth_prev_day(self, d, n):
        """往前数 n 个交易日（不足则返回最早一天）。"""
        i = self._idx.get(d, 0)
        return self._days[max(0, i - n)]

    def codes_at(self, date, where='1=1', order=None, limit=None):
        """选股最常用的形态：返回代码 list。"""
        df = self.panel_at(date, 'jq_code', where, order, limit)
        return df['jq_code'].tolist()
