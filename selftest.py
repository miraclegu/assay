#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""回归自检。改动引擎后先跑这个。

    python3 selftest.py

每一条都对应一个曾经真实发生过的失真，不是凑数的用例。
"""
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from assay.broker import Cost                     # noqa: E402
from assay.engine import Engine                   # noqa: E402
from assay.feed import PanelFeed                  # noqa: E402
from assay.guard import LookAheadError            # noqa: E402
from assay.metrics import summarize               # noqa: E402
from run import load                              # noqa: E402

JQ = dict(slippage=0.0, commission=0.0003, min_commission=5.0, close_tax=0.001)
CASES = []


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


def _run(path, start, end, cash, params=None, **cost):
    feed = PanelFeed(start, end)
    eng = Engine(load(path), feed, cash=cash, cost=Cost(**cost), params=params)
    curve = eng.run()
    # ★ 必须传 engine：空仓指标挂在 engine 上，不传就拿不到（而缺失是静默的）
    return summarize(curve, cash, broker=eng.broker, engine=eng), eng


@case('PIT 防火墙拦住未来函数')
def t_lookahead():
    """曾实测：只把 previous_date 换成 current_date、按当天 change_pct 排序，
    就能跑出年化 20133% / 夏普 7.20。防火墙必须让它抛错而不是给出漂亮结果。"""
    try:
        _run('strategies/_demo/lookahead.py', '2019-01-01', '2019-06-30', 5e5, **JQ)
    except LookAheadError:
        return '按预期抛出 LookAheadError'
    raise AssertionError('未来函数没被拦住 —— 防火墙失效')


@case('合法策略未被防火墙误伤')
def t_baseline():
    """基线数字：改动引擎后必须仍然复现，否则说明引入了行为变化。

    ★ 成本口径用 v0b【自己】的：聚宽 SG-MS-PEG-HL-v0b 里写的是
      PriceRelatedSlippage(0.0015)，不是 FROEC 那套 FixedSlippage(0)。
      模块级的 JQ 字典是 FROEC 的口径（滑点 0），拿它跑 v0b 会得 38.11%，
      再去和聚宽 v0b 实测 38.98% 比就成了苹果比橘子（看着只差 0.87pp）。
      同口径下实际是 36.14% vs 38.98% = -2.84pp。口径必须跟着策略走。
    """
    cost = dict(JQ, slippage=0.0015)
    m, _ = _run('strategies/小市值/sgmspeg_v0b.py', '2019-01-01', '2026-06-30', 1e6, **cost)
    got = round(m['annual_return'] * 100, 2)
    assert abs(got - 38.16) < 0.01, 'v0b 年化 %.2f%%，基线 38.16%%' % got
    mdd = round(m['max_drawdown'] * 100, 2)
    assert abs(mdd - 52.25) < 0.01, 'v0b 回撤 %.2f%%，基线 52.25%%' % mdd
    return 'v0b 年化 %.2f%% / 回撤 %.2f%%（聚宽 v0b 自身口径：滑点 0.0015）' % (got, mdd)


@case('印花税按 2023-08-28 分段')
def t_stamp():
    """2023-08-28 起证券交易印花税减半。写死单一税率会让跨期回测系统性错。"""
    import datetime
    c = Cost()
    a = c.close_tax_at(datetime.date(2023, 8, 27))
    b = c.close_tax_at(datetime.date(2023, 8, 28))
    assert (a, b) == (0.001, 0.0005), '分段错: %s / %s' % (a, b)
    return '减半前 %.4f -> 减半后 %.4f' % (a, b)


@case('成本默认值 = 业内常用')
def t_defaults():
    c = Cost()
    assert c.slippage == 0.0015 and c.commission == 0.00025
    assert c.min_commission == 5.0 and c.close_tax == 'auto'
    assert c.open_tax == 0.0 and c.dividend_tax is True
    return c.describe()


@case('唯一卖出出口')
def t_single_sell_exit():
    """卖出有 6 件副作用；分散写会漏。旧引擎三处各写一遍，
    交易日志只挂在其中一处，直接导致一次归因诊断全错。"""
    src = open('assay/broker.py', encoding='utf-8').read()
    n = src.count('self.pf.positions.pop(')
    assert n == 1, '发现 %d 处直接平仓，必须收敛到 _fill_sell 一处' % n
    return 'positions.pop 仅 1 处'


@case('基准基点取首日前一交易日')
def t_bench_base():
    """聚宽 FROEC 报的基准收益 4.76% = 2015-12-31 -> 2026-08-07。
    用首日(2016-01-04)收盘做基点会得 14.27% —— 差 9.5pp，
    因为 2016-01-04 是熔断日(-8.99%)，用首日收盘等于把它排除在基准之外。
    这与「回测首日不调仓」是同一类边界差一天的错。"""
    feed = PanelFeed('2016-01-01', '2026-08-07')
    px, base = feed.benchmark('000905.XSHG')
    last = px[max(px)]
    got = last / base - 1
    assert abs(got * 100 - 4.76) < 0.02, '基准收益 %.2f%%，聚宽 4.76%%' % (got * 100)
    return '基准收益 %.2f%% (聚宽 4.76%%)' % (got * 100)


@case('FROEC 全指标对标聚宽')
def t_froec_metrics():
    """聚宽实测(交易记录表头)：年化 36.80 / 回撤 47.24 / 基准 4.76 /
    beta 0.899 / alpha 0.360 / 胜率 0.619 / 超额回撤 44.54"""
    m, eng = _run('strategies/小市值/froec.py', '2016-01-01', '2026-08-07', 1e5, **JQ)
    from assay.metrics import summarize
    m = summarize(eng.curve, 1e5, bench=eng.bench, broker=eng.broker,
                  bench_base=eng.bench_base)
    checks = [('基准收益', m['benchmark_return'] * 100, 4.76, 0.02),
              ('beta', m['beta'], 0.899, 0.02),
              ('年化(回归基线)', m['annual_return'] * 100, 39.01, 0.02),
              ('回撤(回归基线)', m['max_drawdown'] * 100, 46.84, 0.02)]
    bad = ['%s %.2f≠%.2f' % (n, g, w) for n, g, w, tol in checks if abs(g - w) > tol]
    assert not bad, '; '.join(bad)
    return '基准 %.2f%% / beta %.3f / 年化 %.2f%% / 回撤 %.2f%%' % (
        m['benchmark_return'] * 100, m['beta'], m['annual_return'] * 100,
        m['max_drawdown'] * 100)


@case('g 是同一个对象（策略/engine/context）')
def t_g_identity():
    """策略写 `from assay.api import *`，在导入时就把 api.g 绑进了自己的命名空间。
    若 _bind 里重新赋值 api.g，策略手上的引用不会变 -> engine.g / context.g
    会是另一个【永远为空】的对象。这个 bug 静默存在过，直到参数扫描要读 engine.g 才暴露。"""
    from assay import api
    feed = PanelFeed('2024-01-01', '2024-03-31')
    eng = Engine(load('strategies/小市值/sgmspeg_v0b.py'), feed, cash=5e5, cost=Cost(**JQ))
    eng.run()
    assert eng.g is api.g, 'engine.g 与 api.g 不是同一个对象'
    assert eng.ctx.g is api.g, 'context.g 与 api.g 不是同一个对象'
    assert hasattr(eng.g, 'stock_num'), 'engine.g 拿不到策略设的参数'
    return 'engine.g is context.g is api.g，且能读到 stock_num=%s' % eng.g.stock_num


@case('参数名拼错立即抛错')
def t_param_typo():
    """静默无效会让扫描结论变成「这个参数没影响」——最危险的一类假结论。"""
    feed = PanelFeed('2024-01-01', '2024-03-31')
    eng = Engine(load('strategies/小市值/sgmspeg_v0b.py'), feed, cash=5e5,
                 cost=Cost(**JQ), params={'candidat_num': 20})
    try:
        eng.run()
    except KeyError as e:
        assert 'candidat_num' in str(e)
        return '按预期抛出 KeyError 并列出可用参数'
    raise AssertionError('拼错的参数名没被拦住')


@case('参数覆盖确实生效')
def t_param_effective():
    a, _ = _run('strategies/小市值/sgmspeg_v0b.py', '2024-01-01', '2026-06-30', 5e5, **JQ)
    feed = PanelFeed('2024-01-01', '2026-06-30')
    eng = Engine(load('strategies/小市值/sgmspeg_v0b.py'), feed, cash=5e5,
                 cost=Cost(**JQ), params={'stock_num': 5})
    from assay.metrics import summarize
    b = summarize(eng.run(), 5e5)
    assert abs(a['annual_return'] - b['annual_return']) > 0.02, \
        '改 stock_num 后结果没变 —— 覆盖没生效'
    return 'stock_num 10 -> 5: 年化 %.2f%% -> %.2f%%' % (
        a['annual_return'] * 100, b['annual_return'] * 100)


@case('加减仓走 FIFO 分批，且不另开成交路径')
def t_fifo():
    """加仓 = 往 lots 追加一批（不合并成平均成本），减仓 = FIFO 消耗若干批。
    这样红利税能按【该批】实际持有期定档（20%/10%/免）、T+1 只锁当日买入的批，
    两者都不需要「按比例摊」这类近似。"""
    import collections
    feed = PanelFeed('2019-01-01', '2022-12-31')
    eng = Engine(load('strategies/_demo/rebalance_weights.py'), feed, cash=1e6,
                 cost=Cost())
    eng.run()
    b = eng.broker
    assert b.n_adds > 0, '没有发生任何加仓 —— 加减仓路径未被走到'
    per = collections.Counter((t['code'], t['exit_date']) for t in b.trades)
    multi = sum(1 for v in per.values() if v > 1)
    assert multi > 0, '没有出现同日跨批平仓 —— FIFO 多批消耗未被走到'
    # 每笔平仓记录都必须带自己那批的建仓日与持有期
    assert all(t['holding_days'] >= 0 and t['entry_date'] is not None
               for t in b.trades), '平仓记录缺建仓日/持有期'
    return '加仓 %d 次 / 同日跨批平仓 %d 次 / 平仓 %d 笔' % (
        b.n_adds, multi, len(b.trades))


@case('红利税按各批持有期分档')
def t_div_tiers():
    """财税[2015]101号：≤1月 20% / 1月~1年 10% / >1年 免。
    若把加仓合并成一个平均建仓日，分档会静默算错。"""
    import datetime
    from assay.context import Lot
    feed = PanelFeed('2024-01-01', '2024-03-31')
    eng = Engine(load('strategies/小市值/sgmspeg_v0b.py'), feed, cash=5e5, cost=Cost())
    b = eng.broker
    b.date = datetime.date(2024, 6, 30)
    got = []
    for days, want in ((10, 0.20), (200, 0.10), (500, 0.0)):
        lot = Lot(shares=100, entry_date=b.date - datetime.timedelta(days=days),
                  entry_price=10.0, div_gross=1000.0)
        t = b._lot_div_tax(lot, lot.div_gross, count=False)
        assert abs(t - 1000.0 * want) < 1e-6, '持有 %d 天税率错: %.4f' % (days, t / 1000)
        got.append('%d天=%.0f%%' % (days, want * 100))
    return ' / '.join(got)


@case('数据版本指纹可用且稳定')
def t_fingerprint():
    """归档只记 datalake 路径的话，panel 重建一次同一策略结果就会变而看不出来。
    指纹用 (相对路径, 字节数, mtime) 而非内容哈希（panel 有 4GB+），
    代价是「重建出内容相同的文件」会误报 —— 方向安全：宁可误报不可漏报。"""
    feed = PanelFeed('2024-01-01', '2024-03-31')
    a = feed.fingerprint()
    b = feed.fingerprint()
    assert a == b, '同一份数据两次指纹不同 —— 不稳定'
    assert set(a['parts']) == {'panel', 'std', 'index'}, '缺部件: %s' % list(a['parts'])
    for k, v in a['parts'].items():
        assert v['n_files'] > 0, '%s 部件没有文件' % k
        assert v['hash'], '%s 缺哈希' % k
    return 'overall=%s (panel %d / std %d / index %d 文件)' % (
        a['overall'], a['parts']['panel']['n_files'],
        a['parts']['std']['n_files'], a['parts']['index']['n_files'])


@case('run_weekly/monthly 的序号参数真的生效')
def t_schedule_ordinal():
    """曾是静默 bug：_due 收了 weekday/monthday 却从不使用，
    `run_weekly(f, weekday=3)` 会静默按周一执行。参数静默失效是最难发现的一类。"""
    # ★ 原先靠 src.replace("weekday=1, time='09:30'", ...) 做字符串手术改源码。
    #   后来 v0b 把它参数化成 weekday=g.weekday，那个字面量消失，替换变成
    #   空操作 -> 两个变体完全一样 -> 用例如实报「参数静默失效」。
    #   改走 params：策略参数化之后就不该再靠改源码来构造变体。
    feed = PanelFeed('2019-01-01', '2021-12-31')
    got = {}
    for w in (1, 3):
        eng = Engine(load('strategies/小市值/sgmspeg_v0b.py'), feed, cash=1e6,
                     cost=Cost(**JQ), params={'weekday': w})
        got[w] = summarize(eng.run(), 1e6)['annual_return']
    assert abs(got[1] - got[3]) > 0.01, \
        'weekday=1 与 weekday=3 结果相同 —— 参数静默失效'
    return 'weekday 1 -> %.2f%% / 3 -> %.2f%%' % (got[1] * 100, got[3] * 100)


@case('分红现金真实入账且股数守恒')
def t_div_cash():
    """后复权隐含「分红自动再投资」。只记税不动仓位，cash 与持仓市值的拆分就和
    现实不同。解法：除权日把仓位一小部分「卖成现金」——
        frac = dps/close_raw(t-1)；cash += shares×close_hfq(t-1)×frac
    这个金额恰等于【真实股数×dps】，而且真实股数不变（现金分红本不改变股数），
    所以是精确模型不是近似。"""
    import datetime
    from assay.context import Lot, Position
    feed = PanelFeed('2024-01-01', '2024-03-31')
    eng = Engine(load('strategies/小市值/sgmspeg_v0b.py'), feed, cash=5e5, cost=Cost(**JQ))
    b = eng.broker
    code = '_TEST_'
    d0, d1 = feed.trading_days[0], feed.trading_days[1]
    b.pf.positions[code] = Position(
        code=code, lots=[Lot(shares=1000.0, entry_date=d0, entry_price=10.0)],
        last_price=20.0)                       # close_hfq(t-1)=20
    b._prev_factor[code] = 2.0                 # -> close_raw(t-1)=10
    feed.div[(code, d1)] = 0.5                 # 每股派息 0.5
    cash0, ep0 = b.pf.cash, b.pf.positions[code].lots[0].entry_price
    b.start_day(d1)
    p = b.pf.positions[code]
    real_before = 1000.0 * 2.0                 # 真实股数 2000
    want_cash = real_before * 0.5              # 分红 1000 元
    got_cash = b.pf.cash - cash0
    assert abs(got_cash - want_cash) < 1e-6, '入账 %.4f，应为 %.4f' % (got_cash, want_cash)
    frac = 0.5 / 10.0
    assert abs(p.shares - 1000.0 * (1 - frac)) < 1e-9, '股数缩减不对: %.4f' % p.shares
    assert abs(p.lots[0].entry_price - ep0) < 1e-12, \
        'entry_price 被缩了 —— 会把分红错记成价差收益'
    assert abs(p.lots[0].div_gross - want_cash) < 1e-6, '计税毛额不等于分红金额'
    return '现金 +%.0f = 真实股数 %.0f × dps 0.5，股数 1000 -> %.1f，成本价不变' % (
        got_cash, real_before, p.shares)


@case('成交量约束在大资金下真的收紧')
def t_volume_cap():
    """★ 没有它，回测会在【所有资金规模】上都报 35~38%，等于宣称策略可无限扩容。
    曾用「委托占成交额中位 0.02~0.44%、P90 1.15%」判断「永不触发」而未实现 ——
    那个判断错在只看中位与 P90：约束这种东西**只有尾部重要**。
    实测 v0b 全区间只有 6 笔被削，却值 1.23pp 年化。"""
    feed = PanelFeed('2019-01-01', '2026-06-30')
    got = {}
    for cash in (1e6, 5e7):
        row = []
        for vr in (0.0, 0.25):
            eng = Engine(load('strategies/小市值/sgmspeg_v0b.py'), feed, cash=cash,
                         cost=Cost(volume_ratio=vr, **JQ))
            m = summarize(eng.run(), cash, broker=eng.broker)
            row.append((m['annual_return'] * 100, eng.broker.n_vol_capped))
        got[cash] = row
    small = got[1e6][1][0] - got[1e6][0][0]        # 100万：轻微收紧
    big = got[5e7][1][0] - got[5e7][0][0]          # 5000万：大幅收紧
    assert got[5e7][1][1] > got[1e6][1][1], '大资金被削笔数没有更多'
    assert big < small - 5, \
        '资金放大 50 倍后约束没有明显更紧（小 %+.2fpp / 大 %+.2fpp）' % (small, big)
    return '100万 %+.2fpp(削%d笔) / 5000万 %+.2fpp(削%d笔)' % (
        small, got[1e6][1][1], big, got[5e7][1][1])


@case('风险警示股按聚宽四条检查排除')
def t_risk_warned():
    """聚宽 filter_st_stock 是【四条】：
        not is_st and 'ST' not in name and '*' not in name and '退' not in name
    我们原先只用 is_st，而 149 只「进入退市整理期」的票 is_st 是 0 ——
    它们价格已崩塌、流通市值极小，恰好排在市值升序最前，暴露最大化
    （实测整理期期间中位 -17.2%、均值 -20.2%）。
    另有约 0.1% 的「正常上市」行名称含 ST/*/退，只有名称检查抓得到。"""
    feed = PanelFeed('2016-01-01', '2026-08-07')
    sql = ("SELECT sum((public_status='进入退市整理期' AND NOT is_st)::INT), "
           "sum((NOT is_risk_warned AND (sec_name LIKE '%ST%' OR sec_name LIKE '%*%' "
           "OR sec_name LIKE '%退%'))::INT), "
           "sum((public_status='进入退市整理期')::INT), "
           "round(avg(CASE WHEN public_status='进入退市整理期' THEN limit_pct END),4) "
           "FROM " + feed.panel + " WHERE date >= DATE '2016-01-01'")
    a, b, n, pct = feed.con.execute(sql).fetchone()
    assert a == 0, '仍有 %d 行「进入退市整理期」被判为非 ST' % a
    assert b == 0, '仍有 %d 行名称含 ST/*/退 却未标记风险' % b
    # ★ 关键：修 is_st 不能把退市整理期的涨跌停幅度改成 5%。
    #   只查【主板】—— 全样本均值 0.1182 是主板 10% 与创业板 20% 的混合，
    #   用混合值做断言会把「改错成 5%」和「创业板占比变化」混为一谈。
    main = feed.con.execute(
        "SELECT round(avg(limit_pct),4) FROM " + feed.panel +
        " WHERE date >= DATE '2016-01-01' AND public_status='进入退市整理期'"
        " AND symbol NOT LIKE 'sz30%' AND symbol NOT LIKE 'sh68%'").fetchone()[0]
    assert abs(main - 0.10) < 1e-6, '主板整理期涨跌幅被改错: %.4f（应为 0.10）' % main
    return '整理期 %d 行全部标记，主板幅度 %.2f（未误改成 5%%）' % (n, main)


@case('面板构建必须确定 + 失败面板拒绝加载')
def t_deterministic():
    """★ 曾是不确定的：同一份源数据三次重建，FROEC 年化得到
    41.31% / 40.73% / 42.52%，跨度 1.8pp —— 意味着我们做过的每一次对比
    都含 ±0.9pp 不可归因的噪声，比任何一个确定性 bug 都更根本。

    根因：ASOF JOIN 右表键不唯一（补披露/延迟披露会在同一天公布多个报告期）。

    ★★ 2026-08-27 断言方向【反转】—— 原先要求 std/fin_quarterly 键唯一，
       那个要求本身是错的，且造成了更严重的缺陷：
       去重「同日多期取最新那期」会丢年报（A 股常在 4 月同日披露年报+一季报，
       23,398 组），而 np_ttm = 本期累计 + 【上年年报】 - 上年同期累计，
       于是 np_ttm/pe_ttm 大面积 NULL（面板 pe_ttm NULL 一度达 60.8%），
       策略被静默剔掉一半股票（`pe BETWEEN 5 AND 50` 遇 NULL 得 NULL）。

       所以现在：
         · std/fin_quarterly **必须**保留全部报告期 -> (code,pub_date) 必然重复
         · 去重只加在 ASOF 右表 _fin3_asof 上 -> 确定性仍然由它保证
       本用例改为核对这个新契约，并**直接验证年报覆盖**（缺失率是那次缺陷的
       直接指标），而不是核对一个已知有害的「键唯一」。
    """
    import os
    feed = PanelFeed('2024-01-01', '2024-03-31')
    dups = {}
    for tbl in ('fin_quarterly', 'fin_indicator_q'):
        f = os.path.join(feed.root, 'std', tbl + '.parquet')
        dups[tbl] = feed.con.execute(
            "SELECT count(*) - count(DISTINCT (code, pub_date)) FROM read_parquet('%s')"
            % f).fetchone()[0]
    # 两张 std 表都【应当】保留真实的「同日多期披露」重复 —— 它们是推导表，
    # np_ttm 之类的计算需要完整报告期。键唯一只是 ASOF 右表的要求。
    assert dups['fin_indicator_q'] > 0, \
        'fin_indicator_q 没有重复键，用例失去意义（源数据变了？）'
    assert dups['fin_quarterly'] > 0, \
        'fin_quarterly 键唯一 —— 说明去重又被加回推导链了，年报会被丢掉'

    # ★ 年报覆盖：上一次缺陷的直接指标。缺年报 -> np_ttm 断链 -> pe_ttm NULL
    fq = os.path.join(feed.root, 'std', 'fin_quarterly.parquet')
    miss_q3, miss_pct = feed.con.execute("""
        WITH y AS (SELECT code, year(report_date) yr,
             max(CASE WHEN month(report_date)=12 THEN 1 ELSE 0 END) a,
             max(CASE WHEN month(report_date)=9  THEN 1 ELSE 0 END) q
           FROM read_parquet('%s') GROUP BY 1,2)
        SELECT sum(CASE WHEN q=1 AND a=0 THEN 1 ELSE 0 END),
               100.0*sum(CASE WHEN q=1 AND a=0 THEN 1 ELSE 0 END)/nullif(sum(q),0)
        FROM y""" % fq).fetchone()
    assert miss_pct < 5.0, \
        '有 Q3 却缺年报 %d 组 (%.1f%%) —— 去重又在丢年报了（修复前是 34.6%%）' % (
            miss_q3, miss_pct)
    ttm_null = feed.con.execute(
        "SELECT 100.0*sum(CASE WHEN pe_ttm IS NULL THEN 1 ELSE 0 END)/count(*) "
        'FROM %s' % feed.panel).fetchone()[0]
    assert ttm_null < 45.0, \
        'pe_ttm NULL 率 %.1f%% 过高 —— 年报链条可能又断了（修复前 2025 年是 60.7%%）' % ttm_null

    pk = feed.con.execute(
        'SELECT count(*) - count(DISTINCT (date, jq_code)) FROM ' + feed.panel).fetchone()[0]
    assert pk == 0, '面板主键有 %d 个重复 —— ASOF 去重失效' % pk
    src = open(os.path.join(feed.root, 'build', 'build_panel_daily.py'),
               encoding='utf-8').read()
    # 确定性仍必须由 ASOF 右表的去重保证 —— 换了位置，不是取消
    assert '_fin3_asof' in src, 'ASOF 专用右表 _fin3_asof 不见了 —— 确定性保证丢失'
    assert 'PARTITION BY t.code, t.pub_date' in src, \
        '_fin3_asof 里没有按 (code, pub_date) 去重 —— ASOF 键可能不唯一'
    assert '_FAILED' in src, '构建脚本缺少 _FAILED 标记机制'
    assert '_FAILED' in open('assay/feed.py', encoding='utf-8').read(), \
        'PanelFeed 缺少拒绝加载失败面板的守卫'
    return ('两张 std 表都保留同日多期(fin_quarterly %d / fin_indicator_q %d) / '
            '有Q3缺年报 %.1f%% / pe_ttm NULL %.1f%% / 面板主键唯一 / '
            '_fin3_asof 去重与 _FAILED 守卫就位'
            % (dups['fin_quarterly'], dups['fin_indicator_q'], miss_pct, ttm_null))


@case('停牌挂账可见 + 期末守卫')
def t_frozen():
    """停牌持仓按最后已知价挂账。**不加折价** —— 实测复牌日收益中位只有
    −3%~−5%（11-30天甚至 +0.14%），而且那笔损失在复牌当天本来就会计入，
    再折价就是双重计算。任务描述里我原本假设「常大幅下跌、需要折价」，
    数据把这个前提否掉了。

    真正的风险是【永不复牌又没有退市日】：会永久按陈价挂账、把权益虚高钉住。
    所以做法是「可见 + 期末响亮告警」，而不是折价。
    """
    import datetime
    from assay.context import Lot, Position
    # 1) 可见性：FROEC 实测有 360 个冻结持仓日、最长 143 天
    m, eng = _run('strategies/小市值/froec.py', '2016-01-01', '2026-08-07', 1e5, **JQ)
    assert eng.broker.frozen_days > 0, '冻结持仓日统计为 0 —— 可见性失效'
    assert eng.broker.max_frozen_run > 30, '最长连续停牌 %d 天，预期 >30' % eng.broker.max_frozen_run
    assert len(eng.broker.frozen_now()) == 0, '期末不该有停牌持仓（实测 0）'
    # 2) 期末守卫：造一个当日无行情的持仓，frozen_now 必须报出来
    feed = PanelFeed('2024-01-01', '2024-03-31')
    e2 = Engine(load('strategies/小市值/sgmspeg_v0b.py'), feed, cash=5e5, cost=Cost(**JQ))
    b = e2.broker
    d = feed.trading_days[5]
    b.pf.positions['_HALTED_'] = Position(
        code='_HALTED_', lots=[Lot(shares=100.0, entry_date=feed.trading_days[0],
                                   entry_price=10.0)], last_price=10.0)
    b.start_day(d)          # _HALTED_ 在 bars 里必然没有
    fz = b.frozen_now()
    assert '_HALTED_' in fz and fz['_HALTED_'] == 1, 'frozen_now 没报出停牌持仓: %s' % fz
    return 'FROEC 冻结 %d 持仓日/最长 %d 天/期末 0 只；合成停牌持仓被正确报出' % (
        eng.broker.frozen_days, eng.broker.max_frozen_run)


@case('涨跌停算不准时不拿它拦交易')
def t_limit_unreliable():
    """面板自带 limit_rule_ok，标出「本行涨跌停价算得准不准」。
    0.0229% 的行为 false，集中在【上市首日 1474 行】【上市 1-5 日 496 行】
    【退市整理期首日 105 行】—— 真实规则分别是「主板 IPO 首日 +44%/-36%」
    「科创创业前 5 日无限制」「退市整理期首日无限制」，面板按常规 10%/20% 算必然错。

    ⚠️ 诚实说明：实测这些行里涨跌停标记**从未为真**（0 行同时满足
    limit_ok=false 且 open_limit_*=true），因为 is_open_limit_up 要求开盘价
    精确等于算错的涨停价，本来就撞不上。所以这个守卫**当前不改变任何结果**，
    是纯防御 —— 一旦面板补上 IPO 首日等规则、标记可能变真，它就会生效。
    这里用合成 bar 验证守卫本身有效。
    """
    from assay.feed import Bar
    feed = PanelFeed('2024-01-01', '2024-03-31')
    eng = Engine(load('strategies/小市值/sgmspeg_v0b.py'), feed, cash=5e5, cost=Cost(**JQ))
    b = eng.broker
    b.date, b.phase = feed.trading_days[0], 'open'
    mk = lambda ok: Bar(open_hfq=10.0, close_hfq=10.0, open_raw=10.0, factor=1.0,
                        open_limit_up=True, open_limit_down=False, limit_up=True,
                        limit_down=False, sealed=True, amount=1e8, limit_ok=ok,
                        high_hfq=10.0, low_hfq=10.0)
    b.bars = {'_T_': mk(True)}
    ok1, why1, _ = b.can_trade('_T_', 'buy')
    b.bars = {'_T_': mk(False)}
    ok2, why2, _ = b.can_trade('_T_', 'buy')
    assert not ok1 and '涨停' in why1, 'limit_ok=true 时应拦住涨停买入，实际 %s' % why1
    assert ok2, 'limit_ok=false 时不该拿算错的涨跌停拦交易，实际被拦: %s' % why2
    n_bad = feed.con.execute(
        'SELECT count(*) FROM bars WHERE limit_ok = false '
        'AND (open_limit_up OR open_limit_down)').fetchone()[0]
    return 'limit_ok=true 拦住 / false 放行；历史上同时成立的行 %d 个（故当前无影响）' % n_bad


@case('并行扫描结果与串行逐位相同')
def t_sweep_parallel():
    """并行化唯一必须保证的不变量：结果与串行一致，且网格顺序不乱。
    api 的模块级状态是每进程一份，所以子进程互不污染；
    但只要 worker 复用 feed、或结果按完成顺序返回，都可能引入差异 —— 必须验。
    """
    import re
    import subprocess
    out = {}
    for jobs in (1, 2):
        r = subprocess.run(
            [sys.executable, 'sweep.py', 'strategies/小市值/sgmspeg_v0b.py',
             '--start', '2024-01-01', '--end', '2024-12-31', '--cash', '1000000',
             '--jq-cost', '--grid', 'stock_num=5,10', '--no-archive',
             '--jobs', str(jobs)],
            capture_output=True, text=True)
        assert r.returncode == 0, 'sweep --jobs %d 失败:\n%s' % (jobs, r.stderr[-800:])
        # 取最终表格里 stock_num 与 annual_return 两列
        rows = []
        for ln in r.stdout.splitlines():
            m = re.match(r'^\s*(\d+)\s+(-?\d+\.\d+)\s', ln)
            if m:
                rows.append((int(m.group(1)), float(m.group(2))))
        assert len(rows) == 2, '--jobs %d 解析到 %d 行，预期 2' % (jobs, len(rows))
        out[jobs] = rows
    assert out[1] == out[2], '串行 %s != 并行 %s' % (out[1], out[2])
    # 网格顺序必须保持（5 在 10 之前）
    assert [x[0] for x in out[2]] == [5, 10], '并行后网格顺序乱了: %s' % out[2]
    return '串行/并行均为 %s，顺序保持' % out[1]


@case('查看服务 API 契约与安全')
def t_server():
    """归档查看服务只用标准库。两件必须验的事：
      1. 每个端点的返回结构（前端靠它渲染，静默改结构就是白屏）
      2. run_id 来自 URL —— 必须只接受索引里存在的值，绝不直接拼路径
    """
    import json as _json
    import threading
    import urllib.error
    import urllib.parse
    import urllib.request
    from http.server import ThreadingHTTPServer

    from assay import server as sv

    idx = sv._scan()
    assert idx, '归档为空，无法验证 API（先跑一次 run.py）'
    rid = sorted(idx)[-1]

    httpd = ThreadingHTTPServer(('127.0.0.1', 0), sv.Handler)
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    base = 'http://127.0.0.1:%d' % port

    def get(path):
        with urllib.request.urlopen(base + path, timeout=20) as r:
            return r.status, _json.loads(r.read().decode())

    try:
        # 1) 契约：前端依赖的字段必须在
        _, runs = get('/api/runs')
        assert isinstance(runs, list) and runs, '/api/runs 返回空'
        for k in ('run_id', 'group', 'strategy', 'annual_return', 'max_drawdown'):
            assert k in runs[0], '/api/runs 缺字段 %s' % k
        _, run = get('/api/run?id=' + rid)
        assert 'meta' in run and 'stats' in run
        for k in ('code_sha256', 'cost', 'data_fingerprint', 'trading_days'):
            assert k in run['meta'], 'meta 缺 %s' % k
        _, eq = get('/api/equity?id=' + rid)
        assert len(eq['dates']) == len(eq['equity']) > 0, '权益曲线长度不一致'
        assert abs(eq['equity'][0] - 1.0) < 0.5, '权益未归一化到起点 1x'
        _, tr = get('/api/trades?id=' + rid)
        assert 'rows' in tr and 'total' in tr
        # 持仓是【分页】的：全量 22k 行不能一次发（前端渲染 22k 个 DOM 会卡死）
        _, hd = get('/api/holdings?id=' + rid + '&offset=0&limit=50')
        for k in ('total', 'offset', 'limit', 'n_days', 'rows'):
            assert k in hd, '/api/holdings 缺字段 %s' % k
        assert len(hd['rows']) <= 50, '分页未生效，返回 %d 行' % len(hd['rows'])
        # 日期必须【倒序且同日相邻】—— 前端按日期变化插分隔块，靠的就是这个不变量
        ds = [r['date'] for r in hd['rows']]
        assert ds == sorted(ds, reverse=True), '持仓未按日期倒序，日期分块会错乱'
        assert len(set(ds)) == len([1 for i, d in enumerate(ds)
                                    if i == 0 or ds[i - 1] != d]), '同一天的行不相邻'
        # 越界/超限必须收敛，不能报错
        _, hd2 = get('/api/holdings?id=' + rid + '&offset=99999999&limit=9999')
        assert hd2['offset'] < hd2['total'] and hd2['limit'] <= 500, \
            '越界参数未收敛: offset=%s limit=%s' % (hd2['offset'], hd2['limit'])
        _, rj = get('/api/rejects?id=' + rid)
        assert 'by_reason' in rj
        for ep in ('/api/code?id=', '/api/log?id='):
            _, tx = get(ep + rid)
            assert 'text' in tx, '%s 缺 text' % ep

        # 2) 安全：非法 / 穿越型 run_id 一律 404，不能读到任何文件
        bad = 0
        for x in ('../../../etc/passwd', '../../assay/server.py', "'; DROP--",
                  '20260101-000000-aaaaaa', ''):
            try:
                get('/api/code?id=' + urllib.parse.quote(x))
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    bad += 1
                    continue
                raise AssertionError('非法 run_id %r 返回 %d，预期 404' % (x, e.code))
            else:
                raise AssertionError('非法 run_id %r 竟然成功了' % x)
        assert bad == 5, '只有 %d/5 个非法 run_id 被拒' % bad
    finally:
        httpd.shutdown()
    return '8 个端点契约通过（持仓分页/倒序/越界收敛）；5 类非法 run_id 全部 404'


@case('网页看板真实渲染（playwright）')
def t_ui():
    """★ JS 语法检查过不代表能渲染 —— 运行时错误在终端里看不到。
    用真实浏览器跑一遍：目录树、展开、点进详情、九个页签、返回、浏览器后退，
    并**量**表头吸顶位置（原先 top:52px 把表头往下推了 52px，
    而日期分隔块的偏移我一开始估 23px、实测是 27px —— 估值都会错）。
    playwright 或浏览器不可用时跳过，不让环境差异变成假失败。
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return '跳过（无 playwright）'
    import threading
    from http.server import ThreadingHTTPServer

    from assay import server as sv
    if not sv._scan():
        return '跳过（归档为空）'
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), sv.Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as p:
            try:
                br = p.chromium.launch()
            except Exception as e:                          # noqa: BLE001
                return '跳过（浏览器不可用: %s）' % type(e).__name__
            pg = br.new_page(viewport={'width': 1500, 'height': 900})
            errs = []
            pg.on('pageerror', lambda e: errs.append(str(e)))
            pg.on('console',
                  lambda m: errs.append('console: ' + m.text) if m.type == 'error' else None)
            pg.goto('http://127.0.0.1:%d/' % port, wait_until='networkidle')
            pg.wait_for_timeout(900)

            # 目录树：任意深度文件夹 -> 策略文件 -> 代码版本 -> 回测
            # ★ 策略身份 = 文件内容哈希（同一路径改一个字符就是另一个策略），
            #   所以版本是独立一层；只有一个版本时跳过这层。
            assert pg.locator('.nd.d0').count() > 0, '目录页没有顶层节点'
            assert pg.locator('.nd.op').count() == 0, '节点默认应【全部收起】'
            # ★ 目录树深度要有上界。诊断脚本放在 scratchpad 时 strategy_path 是
            #   逃逸出仓库的相对路径（../../../../private/tmp/.../froec_oldsql.py），
            #   若按目录逐层展开会铺出 8 层每层一个子节点的空壳。仓库内最深是
            #   strategies/<组>/<文件> -> 目录层只有 1 层（d0），版本层 d1、
            #   回测表在 d1/d2 内，所以 d5 及更深一定是路径没被压平。
            deep = pg.locator('.nd.d5, .nd.d6, .nd.d7, .nd.d8').count()
            assert deep == 0, ('目录树出现 %d 个 d5+ 深层节点 —— 仓库外路径没被'
                               '压成一层？（见 buildTree 的 _仓库外 分支）' % deep)
            pg.locator('.nd.d0').first.click(); pg.wait_for_timeout(250)
            assert pg.locator('.nd.op').count() == 1, '点击文件夹未展开'
            # 逐层点到有回测表为止（深度不定，最多试 5 层）
            rows = pg.locator('.runs:visible tbody tr')
            for _ in range(5):
                if rows.count():
                    break
                nxt = pg.locator('.nd:visible:not(.op)')
                if not nxt.count():
                    break
                nxt.last.click(); pg.wait_for_timeout(300)
            assert rows.count() > 0, '逐层展开后仍没有回测行'
            # 回测必须按【运行时间】倒序
            # ★ 列下标【按表头文字算】，不写死数字 —— 表格加一列（如后来加的
            #   ★ 选中标记列）就会把所有下标推移一位，写死数字会误断成
            #   「未按时间倒序」，而实际读到的是胜率那一列。
            tb = pg.locator('.runs:visible').first
            ths = tb.locator('thead th')
            ci = next((i for i in range(ths.count())
                       if ths.nth(i).inner_text().strip() == '跑于'), None)
            assert ci is not None, '回测表里找不到「跑于」列'
            ra = [rows.nth(i).locator('td').nth(ci).inner_text()
                  for i in range(min(rows.count(), 6))]
            assert ra == sorted(ra, reverse=True), '回测未按运行时间倒序: %s' % ra
            rows.first.click(); pg.wait_for_timeout(1500)
            assert '#/run/' in pg.evaluate('location.hash'), '未跳转到详情'
            assert pg.is_visible('#main') and not pg.is_visible('#cat'), '视图未切换'
            assert pg.locator('#p0 .card').count() > 20, '概览指标卡过少'

            # 九个页签逐个渲染，任一抛错都会进 errs
            for i in range(9):
                pg.locator('#tabs div').nth(i).click(); pg.wait_for_timeout(600)
            # 量表头吸顶：滚动后 th 应贴 .tw 顶部（容差含 1px 边框）
            gap = pg.evaluate("""()=>{const tw=document.querySelector('.pane.on .tw');
              if(!tw) return 0; tw.scrollTop=250;
              return new Promise(r=>requestAnimationFrame(()=>{
                const T=tw.getBoundingClientRect(),H=tw.querySelector('th').getBoundingClientRect();
                r(Math.round(H.top-T.top));}));}""")
            assert abs(gap) <= 2, '滚动后表头距容器顶 %dpx，应贴顶（sticky top 写错？）' % gap

            # ---- 收益明细：年热力 → 年详情页 → 月内日热力 ----
            # ★ 全部由 /api/equity 的 dates+equity 现算，【不依赖新表】。
            #   这里对着曲线逐项校，防止前端口径悄悄漂掉；并校年详情页是
            #   独立路由（浏览器后退必须能用）。
            base_hash = pg.evaluate('location.hash')   # 形如 #/run/<id>
            names = pg.locator('#tabs div').all_inner_texts()
            pg.locator('#tabs div').nth(names.index('收益明细')).click()
            pg.wait_for_timeout(500)
            eqj = pg.evaluate('({d:DATA.eq.dates, e:DATA.eq.equity})')
            import collections, calendar as _cal
            byy = collections.OrderedDict()
            for d, v in zip(eqj['d'], eqj['e']):
                byy.setdefault(d[:4], []).append(v)
            ys = list(byy)
            yc = pg.locator('#p2 .hm.y .hc')
            assert yc.count() == len(ys), '年热力 %d 格 != %d 年' % (yc.count(), len(ys))
            def _dd(vals):
                pk = 0.0; mx = 0.0
                for v in vals:
                    pk = max(pk, v)
                    if pk: mx = max(mx, 1 - v / pk)
                return mx
            t0 = yc.first.inner_text().split('\n')
            v0 = byy[ys[0]]
            assert t0[0] == ys[0], '首格年份 %s != %s' % (t0[0], ys[0])
            assert abs(float(t0[1].rstrip('%')) - (v0[-1] / v0[0] - 1) * 100) < 0.06, \
                '年收益 %s vs %.2f%%' % (t0[1], (v0[-1] / v0[0] - 1) * 100)
            assert ('%d 日' % len(v0)) in t0[2], '交易日数不符: %s vs %d' % (t0[2], len(v0))
            assert abs(float(t0[2].split('回撤 ')[1].split('%')[0]) - _dd(v0) * 100) < 0.06, \
                '年内回撤 %s vs %.2f%%' % (t0[2], _dd(v0) * 100)
            # 点年 -> 独立路由的年详情页
            yc.nth(1).click(); pg.wait_for_timeout(600)
            yr = ys[1]
            assert pg.evaluate('location.hash').endswith('/y/' + yr), \
                '年详情没有独立 hash: %s' % pg.evaluate('location.hash')
            assert pg.locator('#p2 .hm.m .hc').count() == 12, '月热力不是 12 格'
            # 点月 -> 日热力；日历格数=当月天数，交易日数=曲线里的实际条数
            pg.locator('#p2 .hm.m .hc[data-m]').first.click(); pg.wait_for_timeout(500)
            mk = pg.evaluate('MSEL')
            nd = _cal.monthrange(int(mk[:4]), int(mk[5:7]))[1]
            cal_days = pg.locator('#cal .cd:not(.pad)').count()
            cal_off = pg.locator('#cal .cd.off').count()
            real = sum(1 for d in eqj['d'] if d[:7] == mk)
            assert cal_days == nd, '日历格 %d != 当月天数 %d' % (cal_days, nd)
            assert cal_days - cal_off == real, \
                '日历交易日 %d != 曲线里的 %d' % (cal_days - cal_off, real)
            # 浏览器后退必须回到上一年详情页（而不是直接掉出详情）
            pg.go_back(); pg.wait_for_timeout(600)
            h = pg.evaluate('location.hash')
            assert h == base_hash or '/y/' in h, '后退没回到收益明细：%s' % h
            pg.locator('#tabs div').nth(names.index('收益明细')).click()
            pg.wait_for_timeout(400)
            drill = ('年热力 %d 格(%s 收益/回撤/交易日对齐曲线)；%s 年详情独立路由；'
                     '%s 日历 %d 格(交易 %d/非交易 %d)'
                     % (yc.count(), ys[0], yr, mk, cal_days, real, cal_off))

            pg.click('#back'); pg.wait_for_timeout(400)
            assert pg.is_visible('#cat'), '返回目录失败'
            pg.go_back(); pg.wait_for_timeout(900)
            assert pg.is_visible('#main'), '浏览器后退未回到详情'
            br.close()
            assert not errs, 'JS 报错 %d 处: %s' % (len(errs), errs[:2])
    finally:
        httpd.shutdown()
    return ('目录树(任意深度/默认收起/按运行时间倒序)/详情/9 页签/返回/后退 全通，'
            '0 JS 错误，表头 gap %dpx；下钻: %s' % (gap, drill))


@case('版本页：简介/源码/参数表单/触发回测')
def t_version_page():
    """版本（代码哈希）这一层要能看代码、看可填参数、按参数直接回测。

    ★ 断言源码时【必须排除 #joblog】—— 版本页里日志 pre 排在代码 pre 前面且
      初始隐藏，`#vp pre` 会匹配到它、inner_text 返回空串，于是「有元素」的
      断言假通过。实测踩过一次：报「源码 0 字符」却判为通过。
    """
    try:
        from playwright.sync_api import sync_playwright
    except Exception:                                       # noqa: BLE001
        return '跳过（无 playwright）'
    import threading
    from http.server import ThreadingHTTPServer
    from assay import server as sv
    sv._scan()
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), sv.Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as p:
            try:
                br = p.chromium.launch()
            except Exception as e:                          # noqa: BLE001
                return '跳过（浏览器不可用: %s）' % type(e).__name__
            pg = br.new_page(viewport={'width': 1500, 'height': 900})
            errs = []
            pg.on('pageerror', lambda e: errs.append(str(e)))
            pg.on('console',
                  lambda m: errs.append('console: ' + m.text) if m.type == 'error' else None)
            pg.goto('http://127.0.0.1:%d/' % port, wait_until='networkidle')
            pg.wait_for_timeout(800)
            # 过滤会自动展开，直接抵达版本层
            pg.fill('#filter', '红利'); pg.wait_for_timeout(800)

            n_note = pg.locator('.vnote').count()
            assert n_note > 0, '版本行没有一句话简介'
            note0 = pg.locator('.vnote').first.inner_text().strip()
            assert note0 and note0 != '（未填简介）', '简介为空: %r' % note0

            # 版本行不该再有「N 次回测 / 年化 x~y / 最近」
            bad = pg.evaluate("""()=>[...document.querySelectorAll('.nd')]
              .filter(r=>{const ic=r.querySelector('.ic');
                          return ic && ic.textContent.trim()==='\u2317';})
              .map(r=>r.innerText).filter(t=>/次回测|最近|年化/.test(t))""")
            assert not bad, '版本行仍有旧统计: %s' % bad[:1]

            assert pg.locator('.vopen[data-ver]').count() > 0, '没有版本入口'
            pg.locator('.vopen[data-ver]').first.click(); pg.wait_for_timeout(1500)
            assert '#/ver/' in pg.evaluate('location.hash'), '未进入版本页'
            assert pg.is_visible('#vp') and not pg.is_visible('#cat'), '视图未切换'

            # ★ 排除 joblog 后再断言源码非空
            lens = pg.eval_on_selector_all(
                '#vp pre', "els=>els.filter(e=>e.id!=='joblog').map(e=>e.textContent.length)")
            assert lens and min(lens) > 500, '版本页源码为空: %s' % lens
            n_param = pg.locator('#vp input[data-p]').count()
            assert n_param > 0, '没有渲染可填参数'
            for sel in ('#f_start', '#f_end', '#f_cash', '#f_run'):
                assert pg.locator(sel).count() == 1, '缺少 %s' % sel

            # 参数表单必须和引擎认可的参数一致 —— 否则填了会被引擎拒
            names = set(pg.eval_on_selector_all(
                '#vp input[data-p]', 'els=>els.map(e=>e.dataset.p)'))
            sha = pg.evaluate('location.hash').split('/')[-1]
            info = sv.api_version({'sha': sha})
            eng = {x['name'] for x in (info.get('current_params') or [])}
            assert names == eng, '表单参数与解析结果不一致: %s' % (names ^ eng)

            # 非法参数必须被服务端挡住（不经 shell，且名字白名单）
            r = sv.api_backtest({}, {'sha': sha, 'params': {'__nope__': 1}})
            assert 'error' in r, '未知参数名没被挡住'
            r = sv.api_backtest({}, {'sha': sha, 'params':
                                     {sorted(eng)[0]: '1; rm -rf /'}})
            assert 'error' in r, '危险参数值没被挡住'

            pg.click('#back'); pg.wait_for_timeout(500)
            assert pg.is_visible('#cat'), '返回目录失败'
            br.close()
            assert not errs, 'JS 报错 %d 处: %s' % (len(errs), errs[:2])
    finally:
        httpd.shutdown()
    return ('简介 %d 条/旧统计已移除/源码 %d 字符/参数 %d 个与引擎一致/'
            '非法参数名与危险值均被拦' % (n_note, min(lens), n_param))


@case('版本身份：改注释不算新版本，改行为才算')
def t_semantic_version():
    """字节哈希把「改一个注释」也算成新版本，版本节点会爆炸。

    ★ 本用例锁的是判别边界，两侧都要测：
      · 注释 / 排版 / docstring / NOTE 改动 -> 必须【同】版本
      · 任何影响回测结果的改动         -> 必须【新】版本
      只测一侧会漏 —— 一个恒返回同值的哈希也能通过「改注释同版本」。

    ★ 刻意不对 SQL 字符串做归一化：那需要 SQL 解析器，弄错会把真实逻辑改动
      误判成同一版本，比多几个版本节点危险得多。所以 SQL 内改注释【算】新版本，
      这条也在下面显式断言。
    """
    import hashlib as _hl
    import io as _io
    from assay import registry as reg

    f = 'strategies/红利/傻瓜基准.py'
    base = _io.open(f, encoding='utf-8').read()
    s0 = reg.semantic_sha256(base)
    b0 = _hl.sha256(base.encode('utf-8')).hexdigest()

    def mut(old, new):
        assert old in base, '样本代码里找不到 %r，用例需要更新' % old[:40]
        return base.replace(old, new, 1)

    # ---- 应当【同】版本 ----
    same = [
        ('改行内注释', mut('# 月内第几个交易日调仓', '# 每月第几个交易日调仓')),
        ('加注释行', mut('def pick(context):', '# 选股入口\ndef pick(context):')),
        ('加空行', mut('def trade(context):', 'def trade(context):\n')),
        ('改 NOTE 简介', mut("NOTE = '", "NOTE = '【改过】")),
        ('改 docstring', mut('复刻 JQ/红利', '复刻（已核对）JQ/红利')),
    ]
    # ---- 应当【新】版本 ----
    diff = [
        ('改股息率门槛', mut("'div_min', 0.03", "'div_min', 0.04")),
        ('改持仓数', mut("'target_num', 30", "'target_num', 20")),
        ('改 SQL 里的注释（刻意不归一化）',
         mut('-- 近 365 天已实施的现金分红总额（登记日在窗口内）',
             '-- 近一年分红')
         if '-- 近 365 天已实施的现金分红总额（登记日在窗口内）' in base
         else mut('ORDER BY dy DESC', 'ORDER BY dy DESC  -- 降序')),
    ]

    for name, code in same:
        assert _hl.sha256(code.encode('utf-8')).hexdigest() != b0, \
            '%s：样本没真的改动，用例失效' % name
        assert reg.semantic_sha256(code) == s0, '%s：应为同一版本，实际变了' % name
    for name, code in diff:
        assert reg.semantic_sha256(code) != s0, '%s：应为新版本，实际没变' % name

    # ---- 归档与展示链路：新归档写入 semantic_sha256；旧归档能现算 ----
    import json as _json
    from assay import server as sv
    metas = 0
    for rid, d in sv._scan().items():
        m = _json.load(open(os.path.join(d, 'meta.json'), encoding='utf-8'))
        if m.get('semantic_sha256'):
            metas += 1
    rows = sv.api_runs({})
    assert rows and all(r.get('sem_sha') for r in rows), \
        '有回测缺 sem_sha（旧归档现算失败？）'
    nb = len({r['code_sha'] for r in rows})
    ns = len({r['sem_sha'] for r in rows})
    assert ns <= nb, '语义版本数不该多于字节版本数（%d > %d）' % (ns, nb)

    return ('同版本 %d 例 / 新版本 %d 例 全部符合；'
            '归档 %d 条中 %d 条 meta 已含语义哈希，其余现算；'
            '字节版本 %d -> 语义版本 %d'
            % (len(same), len(diff), len(rows), metas, nb, ns))


@case('长期空仓必须响亮告警')
def t_empty_guard():
    """数据缺失会让选股 SQL 返空集 -> 策略长期空仓，而年化/夏普照常输出。

    ★ 真实事故：beta_daily 的 START 错设成 '2013-01-01'（当时只为对标 JQ 的
      2016 起回测往前留 3 年缓冲）。红利低波把该表做【内连接】，2005-2012
      匹配不到行 -> 候选池清空 -> 连续 8 年空仓（占 38% 时间），
      报告给出「年化 10.85%」看着完全合理，只有逐年拆表才发现。
      修正 START 后同区间是 20.31%（差 9.46pp）。

    ★ 两侧都测：正常区间【不该】告警，全空仓【必须】告警且标注结论不可用。
      只测「空仓会告警」会漏 —— 一个永远告警的实现也能通过。
    """
    # ① 正常：不该有空仓
    m, eng = _run('strategies/红利/红利低波.py', '2016-01-01', '2020-12-31', 1e6, **JQ)
    assert eng.empty_days == 0, '正常区间却有 %d 个空仓日' % eng.empty_days
    assert not m.get('empty_days'), '正常区间不该产出空仓指标'

    # ② 造必然空仓：股息率门槛 99%，没有票满足
    m2, eng2 = _run('strategies/红利/红利低波.py', '2016-01-01', '2018-12-31', 1e6,
                    params=dict(div_min=0.99), **JQ)
    n = len(eng2.curve)
    assert eng2.empty_days == n, '应全程空仓，实际 %d/%d' % (eng2.empty_days, n)
    assert m2['empty_pct'] >= 99.9, 'empty_pct 应约 100%%，实际 %.1f%%' % m2['empty_pct']
    assert m2['max_empty_run'] == n, '最长连续空仓应等于全期'
    assert m2['empty_span'] is not None, '缺少空仓起止区间'

    # ③ beta_daily 必须覆盖到 2005 —— 这是事故的直接成因
    import duckdb, os
    feed = PanelFeed('2024-01-01', '2024-01-31')
    bp = os.path.join(feed.root, 'std', 'beta_daily.parquet')
    d0 = duckdb.sql("SELECT min(date) FROM read_parquet('%s')" % bp).fetchone()[0]
    assert str(d0)[:4] <= '2005', \
        'beta_daily 起点 %s 晚于 2005 —— 红利低波会在早期年份静默空仓' % d0

    return ('正常区间 0 空仓日；全空仓场景 %d/%d 日(%.0f%%) 被捕获并标注不可用；'
            'beta_daily 起点 %s' % (eng2.empty_days, n, m2['empty_pct'], d0))


@case('归档的数据版本失效必须可见')
def t_data_staleness():
    """归档是自包含的历史，但它的【数字】依赖当时的数据。
    面板/std 重建后旧归档就不可比了，而报告本身看着完全正常。

    ★ 真实代价：面板修复年报缺失后 175 条归档全部失效，
      而我在之后几轮里仍拿它们跨表对照，得出过错误结论
      （「红利低波最差」「傻瓜基准跑赢」都源于此类混用）。

    ★ 只标记不删除 —— 删了就没法复盘。本用例同时断言「不删」：
      归档目录数在检查前后不变。
    """
    import json as _json
    from assay import server as sv
    from assay.feed import PanelFeed

    idx = sv._scan()
    n_before = len(idx)
    cur = PanelFeed('2024-01-01', '2024-01-31').fingerprint()
    assert cur.get('overall'), '当前数据指纹取不到'

    # 逐部件比对必须能指出【哪一部分】变了，只说「变了」没有价值
    rows = sv.api_runs({})
    assert rows, '没有归档可检查'
    for r in rows:
        assert 'stale' in r and 'stale_parts' in r, 'api_runs 缺失效字段'
    stale = [r for r in rows if r['stale']]
    if stale:
        assert any(r['stale_parts'] for r in stale), '标了失效却说不出变化部件'

    # 构造一个「指纹与当前一致」的 meta -> 必须判定为未失效
    ok, parts = sv._staleness({'data_fingerprint': cur})
    assert ok is False and parts == [], '指纹一致却被判失效'
    # 构造一个明显不同的 -> 必须判失效且列出部件
    bad = {'overall': 'deadbeefdead',
           'parts': {k: dict(v, hash='0' * 12) for k, v in cur['parts'].items()}}
    ok2, parts2 = sv._staleness({'data_fingerprint': bad})
    assert ok2 is True and parts2, '指纹不同却未判失效'
    assert set(parts2) == set(cur['parts']), '应列出全部变化部件，实际 %s' % parts2
    # 归档时无指纹（早期归档）也必须判失效
    ok3, parts3 = sv._staleness({})
    assert ok3 is True, '无指纹的归档应判失效'

    # ★ 只标记不删除
    assert len(sv._scan()) == n_before, '归档数变了 —— 失效检查不该删除任何东西'

    d = sv.api_datafp({})
    return ('当前指纹 %s；%d/%d 条归档失效且均能指出变化部件；'
            '指纹一致/不一致/无指纹三种判定正确；归档数 %d 未变'
            % ((d['current'] or '')[:12], d['n_stale'],
               d['n_stale'] + d['n_current'], n_before))


@case('平均仓位是比空仓日更本质的检查项')
def t_exposure():
    """★ 空仓守卫（n_positions == 0）有盲区：目标 8 只只买到 3 只时
      n_positions=3>0，守卫完全沉默，而仓位其实只有 ~37%。

    实测红利价值 2005-2025：空仓日 9.0%，但仓位<80% 的日子 22.8% ——
    中间那 13.8% 全是「有持仓但严重欠配」，此前完全不可见。
    低仓位意味着收益被现金稀释，与满仓策略【不可直接比】。

    ★ 两侧都测：满仓策略仓位应接近 100% 且无欠配日；
      欠配策略必须被检出。只测一侧会漏（恒返回低仓位的实现也能通过）。
    """
    # ① 满仓：傻瓜基准 30 只等权，仓位应 >95%、无 <80% 的日子
    m, eng = _run('strategies/红利/傻瓜基准.py', '2016-01-01', '2020-12-31', 1e6, **JQ)
    assert m.get('exposure_avg') is not None, '缺 exposure_avg 指标'
    assert m['exposure_avg'] > 95, '满仓策略平均仓位仅 %.1f%%' % m['exposure_avg']
    assert m['exposure_lt80_pct'] < 1.0, \
        '满仓策略却有 %.1f%% 的日子仓位<80%%' % m['exposure_lt80_pct']
    assert m.get('exposure_yearly'), '缺逐年仓位'
    assert all(v > 90 for v in m['exposure_yearly'].values()), \
        '逐年仓位有异常低值: %s' % dict(m['exposure_yearly'])

    # ② 欠配：红利价值 2005 起，池子长期给不满 -> 必须检出
    m2, eng2 = _run('strategies/红利/红利价值.py', '2005-01-01', '2012-12-31', 1e6, **JQ)
    assert m2['exposure_avg'] < 95, \
        '欠配区间平均仓位却有 %.1f%%（用例样本失效？）' % m2['exposure_avg']
    # 关键：仓位低于 80% 的日子必须【多于】空仓日 —— 这就是守卫的盲区
    empty_pct = m2.get('empty_pct') or 0.0
    assert m2['exposure_lt80_pct'] > empty_pct, \
        ('欠配未被检出：仓位<80%% 占 %.1f%%，空仓占 %.1f%% —— '
         '仓位指标应严格覆盖空仓' % (m2['exposure_lt80_pct'], empty_pct))

    return ('满仓：平均仓位 %.1f%%、<80%% 日 %.1f%%、逐年 %d 年全 >90%%；'
            '欠配：平均仓位 %.1f%%、<80%% 日 %.1f%% > 空仓 %.1f%%（盲区被覆盖）'
            % (m['exposure_avg'], m['exposure_lt80_pct'], len(m['exposure_yearly']),
               m2['exposure_avg'], m2['exposure_lt80_pct'], empty_pct))


@case('股票名称按【当时】解析且源正确')
def t_asof_name():
    """★ 名称必须取自 std/security_name（专用名称历史表），
    不能用 security_status.name —— 后者只在【状态】变化时才有新行，
    公司改名而状态不变时它就停在旧值。实测 2024-06-28 有 888/5088 行（17.4%）不符
    （688109 品茗股份->品茗科技、600929 湖南盐业->雪天盐业，全是「正常上市」）。

    这不只影响显示：is_risk_warned 的名称检查也用它 ——
    过期名称漏判了 **欣泰电气(*欣泰)** 与 **金亚科技(*金亚)** 两只欺诈退市股，
    而它们正是小市值策略会买的（暴跌后市值极小、排在市值升序最前）。
    已核对聚宽真实成交：这两只它从未买入，即我们的排除与聚宽一致。
    """
    feed = PanelFeed('2016-01-01', '2026-08-07')
    root = feed.root
    q = ("SELECT count(*), "
         "sum((p.sec_name IS DISTINCT FROM n.name)::INT), "
         "sum((NOT p.is_risk_warned AND (n.name LIKE '%ST%' OR n.name LIKE '%*%' "
         "OR n.name LIKE '%退%'))::INT) "
         "FROM " + feed.panel + " p JOIN read_parquet('" + root +
         "/std/security_name.parquet') n ON n.code=p.jq_code "
         "AND n.valid_from<=p.date AND (n.valid_to IS NULL OR n.valid_to>p.date) "
         "WHERE p.date>=DATE '2016-01-01'")
    total, bad_name, missed = feed.con.execute(q).fetchone()
    assert bad_name == 0, 'panel.sec_name 有 %d 行与权威名称不符' % bad_name
    assert missed == 0, '有 %d 行名称含 ST/*/退 却未标记风险' % missed

    # 服务端按行日期解析（002711 走过 �022浦钢网->欧浦智网->ST欧浦->*ST欧浦->欧浦退）
    from assay import server as sv
    rows = [{'code': '002711.XSHE', 'date': d} for d in
            ('2016-06-01', '2019-04-24', '2019-05-06', '2021-06-02')]
    sv._resolve_names(rows, root, 'date')
    got = [r.get('name') for r in rows]
    want = ['欧浦智网', 'ST欧浦', '*ST欧浦', '欧浦退']
    assert got == want, '当时名称解析错: %s，应为 %s' % (got, want)
    return '%s 行名称与权威源一致；002711 改名轨迹 %s' % (
        format(total, ','), ' -> '.join(want))


@case('候选宇宙不能用面板当日行')
def _():
    """面板是 K 线驱动的：停牌股当日无 K 线 -> 无行 -> 不进任何横截面统计。

    这不是小数点问题：FROEC 对标聚宽长期差 +4.44pp，归因过程里我先怀疑过
    盈亏比定义、持仓不满、14:00 成交价代理、候选池基数、流通股本 look-ahead
    —— 全部被实测打掉。真正的成因是这一条：2015-12-31 面板 2542 行，
    而权威在市股票 2811 只，缺的 267 只≈当日停牌 266 只。聚宽
    get_all_securities() 含停牌股，它们参与【PB 半区 / ROE 十分位的切点计算】，
    还能先占掉 [:10] 的名额再被 filter_paused_stock 删掉。
    修正后 2015-12-31 选股 top15 从「同集合 11/15、同序 2/15」变成
    【15/15 精确同序】，年化 41.02% -> 37.41%（聚宽 36.80%）。

    教训：任何做横截面分位数/排名的策略，候选宇宙都必须来自权威在册表，
    不能来自价格面板。分位切点对宇宙缺失【不是线性不敏感】的 —— 缺 9.5%
    的样本会把 pb 半区切点、ROE 十分位边界一起推移。
    """
    import duckdb
    root = '/Users/guhao/finacial/datalake'
    con = duckdb.connect()
    sd = '2015-12-31'
    n_panel = con.execute(
        "SELECT count(*) FROM read_parquet('%s/mart/panel_daily/panel_2015.parquet')"
        " WHERE date = DATE '%s'" % (root, sd)).fetchone()[0]
    n_univ = con.execute(
        "SELECT count(*) FROM read_parquet('%s/std/security_universe.parquet')"
        " WHERE sec_type='stock' AND list_date <= DATE '%s'"
        "   AND (delist_date IS NULL OR delist_date > DATE '%s')" % (root, sd, sd)
    ).fetchone()[0]
    n_paused = con.execute(
        "SELECT count(*) FROM read_parquet('%s/mart/paused_daily/*.parquet')"
        " WHERE date = DATE '%s'" % (root, sd)).fetchone()[0]
    gap = n_univ - n_panel
    assert gap > 200, (
        '面板与权威宇宙的差额只有 %d —— 若面板已含停牌股行，本用例的前提变了，'
        '请改判并同步修正 froec.py 的 univ CTE' % gap)
    assert abs(gap - n_paused) < 0.15 * n_paused, (
        '缺额 %d 与当日停牌数 %d 差太多，说明缺的不只是停牌股，需重新归因'
        % (gap, n_paused))
    # froec.py 必须已经改用权威宇宙，且不再用单季 eps
    import io as _io2
    src = _io2.open('strategies/小市值/froec.py', encoding='utf-8').read()
    assert 'security_universe' in src, 'froec.py 未使用权威宇宙，停牌股仍会缺席'
    assert 'eps_q >' not in src and 'eps_q>' not in src, (
        'froec.py 的过滤条件仍在用单季 eps_q（聚宽用累计 indicator.eps）')
    assert 'e.eps > 0' in src, 'froec.py 未用 fin_indicator_q 的累计 eps 做过滤'
    return ('面板 %d 行 vs 权威在市 %d 只，缺 %d ≈ 当日停牌 %d；'
            'froec.py 已改用权威宇宙 + 累计 eps' % (n_panel, n_univ, gap, n_paused))


@case('流通A股扣除 B/H 股')
def _():
    """share_trade_total 是「全部无限售流通股」，含 B/H 股；聚宽
    circulating_market_cap 只算 A 股。321 个代码有 B/H，其中 16% 的行
    B/H 字段为 NULL，必须前向结转 —— 直接 COALESCE(...,0) 会把这些行算大。

    四个独立佐证（2015-12-31，与聚宽实测值比）：
      600054 黄山旅游  含B 64.602亿 / 扣B 27.770亿 = 聚宽 27.770亿
      000756 新华制药  含H 61.326亿 / 扣H 41.211亿 = 聚宽 41.211亿
      002705 / 300317  无 B/H，扣不扣都等于聚宽
    """
    import duckdb
    root = '/Users/guhao/finacial/datalake'
    con = duckdb.connect()
    want = {'600054.XSHG': 27.770, '000756.XSHE': 41.211,
            '002705.XSHE': 22.871, '300317.XSHE': 41.258}
    got = {}
    for code, jqv in want.items():
        r = con.execute(
            "SELECT floatmv/1e8 FROM read_parquet('%s/mart/panel_daily/panel_2015.parquet')"
            " WHERE jq_code='%s' AND date=DATE '2015-12-31'" % (root, code)).fetchone()
        assert r and r[0], '%s 当日无 floatmv' % code
        got[code] = r[0]
        assert abs(r[0] - jqv) < 0.01, (
            '%s 流通市值 %.3f 亿，聚宽 %.3f 亿 —— 差这么多通常是 B/H 股没扣，'
            '或 B/H 字段 NULL 未前向结转' % (code, r[0], jqv))
    return '4 只逐个吻合聚宽（含 1 只B股 1 只H股）: ' + ', '.join(
        '%s %.3f亿' % (k.split('.')[0], v) for k, v in got.items())


@case('选中标记：打星 / 冒泡 / 不误触发')
def _():
    """★ 打在【单次回测】上，因为 run 才记录了策略+参数+区间+成本+数据指纹，
    构成一条完整的「规则」；策略文件或代码版本都不够 —— 同一版本换个参数
    就是另一条规则（froec 的 kcb_688_only=0 与默认值是两条）。

    三个容易错的点，都要真浏览器才测得出来：
      1) 星标要【向上冒泡】到版本/文件/目录三层，否则折叠状态下看不见，
         「打标记方便找」的目的就落空了。
      2) 点星不能连带打开回测详情页 —— 行本身有 onclick，必须 stopPropagation。
      3) 标记文件里会残留【已删除归档】的 run_id（清理归档不同步删标记），
         /api/marks 必须过滤掉，否则前端渲染出点不开的空行。
    ★ 用完恢复标记文件：测试不该改动用户真实的选中状态。
    """
    import json
    import shutil
    import threading
    from http.server import ThreadingHTTPServer

    from assay import server as sv
    runs = sv.api_runs({})
    if not runs:
        return '跳过（归档为空）'

    bak = sv.MARKS_FILE + '.selftest-bak'
    had = os.path.isfile(sv.MARKS_FILE)
    if had:
        shutil.copy2(sv.MARKS_FILE, bak)
    try:
        # --- 服务端：非法输入必须拒掉，不能静默写进去 ---
        rid = runs[0]['run_id']
        assert sv.api_mark({}, {'run_id': '../../etc/passwd', 'mark': 'star'}).get('error'), \
            '目录穿越的 run_id 未被拒绝'
        assert sv.api_mark({}, {'run_id': rid, 'mark': '<script>'}).get('error'), \
            '未知标记类型未被拒绝'
        assert sv.api_mark({}, {'run_id': '99999999-000000-000000',
                                'mark': 'star'}).get('error'), '不存在的 run_id 未被拒绝'
        # 控制字符要被剥掉（备注直接渲染进页面）
        r = sv.api_mark({}, {'run_id': rid, 'mark': 'star', 'note': 'a\x00\x07b'})
        assert r.get('ok') and '\x00' not in r['note'] and '\x07' not in r['note'], \
            '备注里的控制字符没被剥掉: %r' % r.get('note')
        # 残留 run_id 要被 /api/marks 过滤
        d = sv._load_marks()
        d['20200101-000000-deadbe'] = {'mark': 'star', 'note': '已删归档', 'ts': ''}
        sv._save_marks(d)
        ids = {m['run_id'] for m in sv.api_marks({})}
        assert '20200101-000000-deadbe' not in ids, '已删归档的残留标记未被过滤'
        assert rid in ids, '刚打的标记没出现在 /api/marks'
        d.pop('20200101-000000-deadbe'); sv._save_marks(d)

        # --- 浏览器：渲染 + 冒泡 + 点击语义 ---
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return '服务端 5 项通过；浏览器部分跳过（无 playwright）'
        httpd = ThreadingHTTPServer(('127.0.0.1', 0), sv.Handler)
        port = httpd.server_address[1]
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            with sync_playwright() as pw:
                try:
                    br = pw.chromium.launch()
                except Exception as e:                      # noqa: BLE001
                    return '服务端 5 项通过；浏览器不可用(%s)' % type(e).__name__
                pg = br.new_page(viewport={'width': 1500, 'height': 900})
                errs = []
                pg.on('pageerror', lambda e: errs.append(str(e)))
                pg.on('console', lambda m: errs.append('console: ' + m.text)
                      if m.type == 'error' else None)
                pg.on('dialog', lambda dl: dl.accept('selftest 备注'))
                pg.goto('http://127.0.0.1:%d/' % port, wait_until='networkidle')
                pg.wait_for_timeout(900)

                assert pg.locator('#pick').count() == 1, '顶部「选中的规则」面板没渲染'
                npick = pg.locator('#pick tr[data-go]').count()
                assert npick >= 1, '「选中的规则」面板里没有行'
                # 冒泡：至少有一个收起的顶层节点带 ★ 计数
                assert pg.locator('.nd.d0 .stcnt').count() >= 1, \
                    '星标没有冒泡到顶层目录节点（折叠时将看不见）'

                # 用「全部展开」把所有回测露出来 —— 逐层点会停在某个只含
                # 已打星行的子树上，导致找不到可点的目标（实测踩过）。
                pg.click('#expand'); pg.wait_for_timeout(900)
                st = pg.locator('.runs tbody tr:not(.marked) .st').first
                assert st.count(), '找不到未打星的回测行'
                before = pg.locator('#pick tr[data-go]').count()
                st.click(); pg.wait_for_timeout(700)
                # 点星不能跳转
                assert not pg.url.rstrip('/').endswith('#/run') and '#/run/' not in pg.url, \
                    '点击星标误触发了跳转到回测详情页（stopPropagation 失效）'
                after = pg.locator('#pick tr[data-go]').count()
                assert after == before + 1, \
                    '打星后「选中的规则」没增加（%d -> %d）' % (before, after)

                # --- 独立索引页 #/picks ---
                # 目录页顶部那条只列关键几列；这一页是完整视图，且必须显式列出
                # 【成本口径】—— 本项目两次因为拿滑点 0 的数字去比含滑点的基准
                # 而得出错误结论（FROEC 与 v0b 各一次）。
                assert pg.locator('#gopicks').count() == 1, '顶栏缺少「★ 选中的规则」入口'
                pg.click('#gopicks'); pg.wait_for_timeout(600)
                assert pg.url.endswith('#/picks'), '点入口没进 #/picks: %s' % pg.url
                assert pg.locator('#pk').count() == 1, '索引页没渲染'
                ncard = pg.locator('#pk .card2').count()
                assert ncard == after, \
                    '索引页卡片数 %d 与标星数 %d 不一致' % (ncard, after)
                assert not pg.is_visible('#cat'), '进索引页后目录页应隐藏'
                txt = pg.locator('#pk').inner_text()
                assert '滑点' in txt, '索引页没有列出成本口径（滑点）'
                # 点卡片进详情，再后退回索引页
                pg.locator('#pk .card2').first.click(); pg.wait_for_timeout(700)
                assert '#/run/' in pg.url, '点卡片没进回测详情: %s' % pg.url
                pg.go_back(); pg.wait_for_timeout(600)
                assert pg.url.endswith('#/picks'), '后退没回到索引页: %s' % pg.url

                assert not errs, '页面报错: %s' % errs[:3]
                br.close()
        finally:
            httpd.shutdown()
        return ('服务端 5 项 + 浏览器 11 项通过；%d 条选中规则，星标冒泡到顶层，'
                '独立索引页 #/picks 正常' % npick)
    finally:
        if had:
            shutil.move(bak, sv.MARKS_FILE)
        elif os.path.isfile(sv.MARKS_FILE):
            os.remove(sv.MARKS_FILE)


def main():
    ok = fail = 0
    for name, fn in CASES:
        try:
            msg = fn()
            print('  ✓ %-28s %s' % (name, msg or ''))
            ok += 1
        except Exception as e:                              # noqa: BLE001
            print('  ✗ %-28s %s: %s' % (name, type(e).__name__, e))
            if os.environ.get('SELFTEST_TRACE'):
                traceback.print_exc()
            fail += 1
    print('\n%d 通过 / %d 失败' % (ok, fail))
    sys.exit(1 if fail else 0)


if __name__ == '__main__':
    main()
