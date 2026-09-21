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

import pandas as _pd
from collections import namedtuple

import duckdb
from assay import paths as _paths   # datalake 根的唯一解析
from assay import symbols as _SYM     # 代码换算的唯一正本

# 撮合与风控需要的字段。策略要别的列走 query()/panel()，不必挤在这里。
Bar = namedtuple('Bar', 'open_hfq close_hfq open_raw factor '
                        'open_limit_up open_limit_down limit_up limit_down sealed '
                        'touch_up change_pct ma5 ma20 '
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
    -- 「盘中**摸到过**涨停」。与 `limit_up`（= 面板 is_limit_up = **收盘**封在
    -- 板上）是两件事：实测 2026-09 以来「盘中摸到、收盘没封住」有 183 个，
    -- 它们的 is_limit_up 全是 false。
    -- ★ 纯**投影**改动：只多返回一列，WHERE / ORDER BY 一个字没动，
    --   原策略不读它 —— 行为一行不变（同「末层 SELECT 多返回几列」那条）。
    (high >= limit_up - 0.005)       AS touch_up,
    -- 🔴 **当日涨幅直接取面板的 `change_pct`**，不要自己拿后复权价算。
    --   实测（2024 全年 123 万行）：`change_pct` 与 `close_bfq/preclose − 1`
    --   **零偏差**；而拿后复权比值算会在**低价股**上差很多（27 万行偏差
    --   > 0.02pp、最大 1.9pp）—— 不复权价只有 2 位小数，0.33 -> 0.34 是
    --   +3.03%，而后复权价保留更多有效位、算出 +1.75%。
    --   ★ 两个都"看着像涨幅"，而错的那个在小盘低价股上系统性偏小 ——
    --     正是这个策略的持仓所在。
    change_pct,
    -- 5 日均线（后复权收盘的 5 日移动平均，含当日）。
    -- ★ 在 SQL 里算而不是让策略自己 `bar_range` 取 5 天再平均：
    --   窗口函数一次算完，而策略侧循环取数在 500+ 调仓日上很慢；
    --   更重要的是**口径只有一份**（同「K 线的均线在服务端算」那条）。
    -- 🔴 `ROWS BETWEEN 4 PRECEDING AND CURRENT ROW` 不足 5 根时给部分均值，
    --   策略侧要自己判「够不够 5 根」—— 给 NULL 的话建仓头几天会静默不跟踪。
    avg(close_hfq) OVER (PARTITION BY jq_code ORDER BY date
                         ROWS BETWEEN 4 PRECEDING AND CURRENT ROW) AS ma5,
    -- MA20：给「价格偏离均线的程度」当基准（现价 / MA20 − 1）。
    -- 🔴 同 ma5：不足 20 根时给的是**部分均值**，用它的策略必须自己判
    --   够不够 20 根 —— 直接信的话建仓头几天会拿一个 3 根的"均线"去比。
    avg(close_hfq) OVER (PARTITION BY jq_code ORDER BY date
                         ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS ma20,
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
    # 指数成分。两张表是【同一份数据的两种形状】，按问法选：
    #   t_index_member       区间表(valid_from/last_seen/valid_to)——
    #                        问「某日谁在指数里」用它，一次比较搞定
    #   t_index_member_asof  原始时点快照(as_of/index_code/stock_code)——
    #                        问「某个采样时点的花名册」或要自己做区间逻辑时用
    # 🔴 覆盖 28 个指数，由 raw/jq/_ingest/extract_jq_index_members.py 的清单
    #   决定。**不在清单里的指数本地没有成分数据，而查询只会返回空集、不报错。**
    # 🔴 采样是月频(定期调整型)/周频(中小板综、创业板综)，所以区间边界有
    #   ~1 个月 / ~1 周的分辨率 —— 不是公告驱动的精确生效日。
    't_index_member':      'index_member',
    't_index_member_asof': 'index_member_asof',
}


def std_tables(root):
    """{t_xxx: read_parquet(...)}，供 SQL format 展开。"""
    return {k: "read_parquet('%s/std/%s.parquet')" % (root, v)
            for k, v in _STD_TABLES.items()}


class PanelFeed:
    """datalake 的 mart/panel_daily 适配器。"""

    def __init__(self, start, end, root=None):
        self.root = _paths.datalake(root)
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
        self.panel = _paths.panel_sql(self.root)
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
        # 🔴 这个 glob 必须与 `paths.panel_sql` 读的那批**是同一批** ——
        #   指纹盯的文件比实际读的少一个，就会出现「数据变了而指纹没变」，
        #   于是 tick 判「不用重算」、模拟盘判「不用推进」，**都不报错**。
        #   所以 glob 用正本导出的那个常量，不在这里再写一遍。
        ('panel', _paths.PANEL_GLOB),
        ('std',   'std/*.parquet'),
        ('index', 'raw/tdx/kline/' + _paths.TDX_KLINE['index'] + '.parquet'),
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
        # 🔴 代码换算走唯一正本（`symbols.to_symbol`）—— 这里原来内联了
        #   第四份"聚宽口径 -> tdx symbol"，而它**不校验位数**：
        #   `abc.XSHG` 会被拼成 `shabc` 再去查，报的是"没有数据"
        #   而不是"这个代码不对"。正本认不出就给 None，退回原样。
        sym = _SYM.to_symbol(code) or code
        rows = self.con.execute(
            "SELECT date, close FROM %s "
            "WHERE symbol = '%s' AND date BETWEEN DATE '%s' AND DATE '%s' ORDER BY 1"
            % (_paths.tdx_kline_sql('index', self.root), sym,
               self.start, self.end)).fetchall()
        if not rows:
            raise SystemExit('基准 %s (tdx=%s) 没有数据' % (code, sym))
        # ★ 基点必须取回测首日的【前一交易日】收盘，不是首日收盘。
        #   实测：聚宽 FROEC 报的基准收益 4.76% = 2015-12-31 -> 2026-08-07，
        #   而用首日(2016-01-04)收盘做基点得 14.27% —— 差 9.5pp。
        #   因为 2016-01-04 是熔断日(-8.99%)，用首日收盘做基点等于把它排除在基准之外。
        #   这与「回测首日不调仓」是同一类【边界差一天】的错。
        base = self.con.execute(
            "SELECT close FROM %s "
            "WHERE symbol = '%s' AND date < DATE '%s' ORDER BY date DESC LIMIT 1"
            % (_paths.tdx_kline_sql('index', self.root), sym,
               self.start)).fetchone()
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

    def limit_up_days(self, codes, start, end):
        """`had_limit_up` 的**明细版**：{code: {收盘涨停的日期}}。

        ★ 存在的理由：黑名单要能区分"涨停发生在持有期间"还是"买入之前" ——
          只有集合的话分不出来。区间口径与 `had_limit_up` **完全一致**
          (`date > start AND date <= end`)，两处不一致的表现是
          "同一个窗口两种答案"，而它不报错。
        """
        if not codes:
            return {}
        q = "','".join(codes)
        out = {}
        for c, dt in self.con.execute(
            "SELECT code, date FROM bars WHERE date > DATE '%s' AND date <= DATE '%s' "
            "AND code IN ('%s') AND limit_up" % (start, end, q)).fetchall():
            out.setdefault(c, set()).add(dt)
        return out

    # ==================== 策略数据 API ====================
    # 🔴 **策略不该直接写 SQL —— 只有 feed 知道 datalake 的存在。**
    #   下面三个方法把「取什么数据」表达成参数，把「怎么筛、怎么排、取几个」
    #   留给策略用 Python 做。职责边界：
    #     feed   —— 从哪张表取、as-of 怎么算、停牌怎么结转、多期怎么去重
    #     策略   —— 阈值、分位、排序、截断（也就是"规则"本身）
    #   ★ 现有策略（froec.py / 红利）仍然直接写 SQL，**一行都没动** ——
    #     它们的归档要保持可比。新策略用这套 API，见
    #     strategies/小市值/froec_api.py（与 froec.py 逐位等价，selftest 钉着）。

    def universe(self, date, listed_days=0, exclude_like=None,
                 perturb=0, salt='', sec_type='stock'):
        """PIT 在册股票代码（list）。

        🔴 **不是面板当日行。** 面板是 K 线驱动的：停牌股当日无 K 线 -> 无行
          -> 根本不进漏斗。而聚宽 `get_all_securities()` 含停牌股，它们会参与
          分位切点的计算、还能先占掉名额再被过滤掉。实测 2015-12-31：
          面板 2542 行 vs 权威在市 2811 只，缺的 267 只就是停牌股。

        listed_days  上市满多少【自然日】（聚宽口径）
        exclude_like 排除代码前缀，SQL LIKE 模式，如 '68%' 排科创板
        perturb/salt 扰动抽样：hash(code||salt) %% 10000 >= perturb 才留下。
                     换 salt = 换一条独立路径，用来量噪声。
        """
        t = std_tables(self.root)
        w = ["sec_type = '%s'" % sec_type,
             "list_date <= DATE '%s'" % date,
             "(delist_date IS NULL OR delist_date > DATE '%s')" % date]
        if listed_days:
            w.append("date_diff('day', list_date::DATE, DATE '%s') >= %d"
                     % (date, int(listed_days)))
        if exclude_like:
            w.append("code NOT LIKE '%s'" % exclude_like)
        if perturb:
            w.append("hash(code || '%s') %% 10000 >= %d" % (salt, int(perturb)))
        return [r[0] for r in self.con.execute(
            'SELECT code FROM %s WHERE %s' % (t['t_universe'], ' AND '.join(w))
        ).fetchall()]

    def snapshot(self, date, cols, codes=None, carry_days=0):
        """面板当日行 -> DataFrame（第一列 jq_code）。

        carry_days > 0 时，对**当日没有行**（停牌）的 codes 做 ASOF 结转：
        取该票 <= date 的最近一行，回看窗口 carry_days 天。

        🔴 **只用它结转价格派生量**（pb / floatmv / 分类 / 风险警示）——
          基本面**不能**结转：聚宽的 get_fundamentals 无论是否停牌都给当前
          最新报告，结转会拿到该股【最后交易日】那天的过期报告。
          实测 002379.XSHE@2015-12-31：结转得 2015-06-30 的 eps=-0.0286 被
          eps>0 误剔，而当日最新是 2015-09-30 的 eps=+0.08。
          基本面走 `fundamentals()`。
        """
        cl = ', '.join(cols)
        cur = ("SELECT jq_code, %s FROM %s WHERE date = DATE '%s'"
               % (cl, self.panel, date))
        if not carry_days or codes is None:
            df = self.con.execute(cur).df()
            if codes is not None:
                df = df[df['jq_code'].isin(set(codes))].reset_index(drop=True)
            return df
        self.con.register('_snap_codes', _pd.DataFrame({'code': list(codes)}))
        try:
            return self.con.execute("""
                WITH today AS (%s),
                miss AS (
                  SELECT u.code AS jq_code, %s
                  FROM (SELECT code, DATE '%s' AS d FROM _snap_codes
                        WHERE code NOT IN (SELECT jq_code FROM today)) u
                  ASOF LEFT JOIN (
                      SELECT jq_code, date, %s FROM %s
                      WHERE date > DATE '%s' - INTERVAL %d DAY
                  ) p ON p.jq_code = u.code AND p.date <= u.d
                )
                SELECT * FROM today WHERE jq_code IN (SELECT code FROM _snap_codes)
                UNION ALL SELECT * FROM miss
            """ % (cur, ', '.join('p.%s' % c for c in cols), date,
                   cl, self.panel, date, int(carry_days))).df()
        finally:
            self.con.unregister('_snap_codes')

    def fundamentals(self, date, fields, codes=None, periods=1,
                     table='t_indicator', require_all=False):
        """按 **pub_date as-of** 的财务指标 -> 长表 DataFrame(code, seq, …)。

        seq = 1 是最新一期，2 是上一期……最多 periods 期。

        🔴 as-of 用 `pub_date <= date` —— 用 report_date 就是未来函数
          （报告期在前、公告在后）。同一 report_date 有多条时按 pub_date
          倒序留最后公告的那条（修正稿）。
        require_all=True 时只保留**恰好凑满 periods 期**的 code ——
          ROE 加速度那类"要 5 期才算得出来"的指标必须这样，否则
          缺期的票会算出一个偏小的值而**不报错**。
        """
        t = std_tables(self.root)
        tbl = t.get(table, table)
        fl = ', '.join(fields)
        notnull = ' AND '.join('%s IS NOT NULL' % f for f in fields)
        sql = """
            SELECT code, seq, %s FROM (
              SELECT code, %s, row_number() OVER (PARTITION BY code
                       ORDER BY report_date DESC, pub_date DESC) seq
              FROM %s WHERE pub_date <= DATE '%s' AND %s
            ) WHERE seq <= %d
        """ % (fl, fl, tbl, date, notnull, int(periods))
        df = self.con.execute(sql).df()
        if codes is not None:
            df = df[df['code'].isin(set(codes))]
        if require_all and periods > 1:
            keep = df.groupby('code')['seq'].count()
            df = df[df['code'].isin(set(keep[keep == periods].index))]
        return df.reset_index(drop=True)

    def nth_prev_day(self, d, n):
        """往前数 n 个交易日（不足则返回最早一天）。

        🔴 `d` **不在交易日表里**时要按位置插值，不能落到 0。
        原来写的是 `self._idx.get(d, 0)` —— 回测里 d 永远是真实交易日，
        取不到的情况根本不会发生；但**实盘预览**问的是【下一个交易日】，
        它还没有行情、不在表里，于是 `.get(d, 0)` 返回 0，
        `nth_prev_day(下个交易日, 20)` 给出的是**面板的第一天**（实测 2016-01-04）。

        后果是静默的：froec 的 20 日涨停黑名单
        `had_limit_up(cand, nth_prev_day(today, 20), d)` 会变成
        「2016 年以来涨停过吗」—— 几乎所有票都命中（实测 10/10），
        于是「最近 20 日持有过 且 涨停过」这条规则退化成「最近持有过」，
        持仓被整片剔出目标池。**实测代价**：拿真实的 10 只持仓在调仓日出信号，
        修好前说「卖出 10 只」（清空），修好后是「卖出 2 只」——
        **不报错，只是让你把整个组合平掉**。
        （这个 bug 是加"选股理由"时被捕获到的窗口 `start=2016-01-04` 暴露的：
         页面上要显示"哪段窗口内涨停过"，那个 2016 一眼就不对。）

        bisect 对**在表里**的日期与 `_idx` 完全等价（selftest 里逐日钉住），
        所以回测行为一行不变。
        """
        i = self._idx.get(d)
        if i is None:
            import bisect
            i = bisect.bisect_left(self._days, d)
        return self._days[max(0, i - n)]

    def codes_at(self, date, where='1=1', order=None, limit=None):
        """选股最常用的形态：返回代码 list。"""
        df = self.panel_at(date, 'jq_code', where, order, limit)
        return df['jq_code'].tolist()
