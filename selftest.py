#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""回归自检。改动引擎后先跑这个。

    python3 selftest.py

每一条都对应一个曾经真实发生过的失真，不是凑数的用例。
"""
import argparse
import os
import re
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


def case(name, tag='fast'):
    """tag 决定用例进哪一层，依据是【实测耗时】不是感觉：

      fast (22 个, 约 18s)  日常改代码跑这个
      slow (7 个,  约 189s) 跑长区间回测的，占全量 80% 的时间
      web  (3 个,  约 30s)  需要浏览器

    新增用例默认 fast；如果它要跑多年回测或起浏览器，记得显式标 tag，
    否则 --fast 会慢慢退化回全量。
    """
    def deco(fn):
        CASES.append((name, fn, tag))
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


@case('合法策略未被防火墙误伤', tag='slow')
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


@case('FROEC 全指标对标聚宽', tag='slow')
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


@case('参数覆盖确实生效', tag='slow')
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


@case('run_weekly/monthly 的序号参数真的生效', tag='slow')
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


@case('成交量约束在大资金下真的收紧', tag='slow')
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


@case('停牌挂账可见 + 期末守卫', tag='slow')
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


@case('并行扫描结果与串行逐位相同', tag='slow')
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


@case('数据字典：读磁盘 md 并渲染，改文件即生效', tag='fast')
def t_docs():
    """★ 关键是【不烤内容进前端】这个承诺要真成立。

    验三件事：
      1. _DOCS 里每一篇都能解到实际文件（路径写错了会静默变成「缺」）
      2. markdown 渲染后不残留裸标记（表格分隔行、裸 ** 漏出来最常见）
      3. 改文件不重启服务就生效 —— 这是整个设计的理由，必须有断言守着
    """
    import re
    from assay import server as sv

    d = sv.api_docs({})
    miss = [x['rel'] for x in d['docs'] if not x['exists']]
    assert not miss, '这些 _DOCS 路径解不到文件: %s' % miss

    n_tab = n_row = 0
    for x in d['docs']:
        r = sv.api_doc({'key': x['key']})
        h = r['html']
        assert h and '<' in h, '%s 渲染为空' % x['key']
        # 裸 markdown 漏出来 = 渲染器没覆盖到某种语法。
        # 先剥掉 code/pre —— 里面的 `**kw`（如 Feed.query(sql, **kw)）是真内容不是漏出。
        bare = re.sub(r'<pre>.*?</pre>|<code>.*?</code>', '', h, flags=re.S)
        assert not re.search(r'\|\s*-{3,}', bare), '%s 表格分隔行没吃掉' % x['key']
        assert '**' not in bare, '%s 裸 ** 漏出' % x['key']
        assert not re.search(r'^#{1,6} ', bare, re.M), '%s 裸 # 漏出' % x['key']
        n_tab += h.count('<table')
        n_row += h.count('<tr')
    assert sv.api_doc({'key': 'nope_不存在'}) is None, '未知 key 应返回 None -> 404'

    # 改文件立即生效（这是本功能存在的全部理由）
    path = sv._doc_path([x for x in sv._DOCS if x[0] == 'trap'][0][3])
    raw = open(path, encoding='utf-8').read()
    token = '__assay_selftest_%d__' % len(raw)
    try:
        open(path, 'w', encoding='utf-8').write(raw + '\n\n' + token + '\n')
        h2 = sv.api_doc({'key': 'trap'})['html']
        assert token in h2, '改了 md 但接口没反映 —— 说明内容被缓存或烤死了'
    finally:
        open(path, 'w', encoding='utf-8').write(raw)
    assert token not in sv.api_doc({'key': 'trap'})['html'], '还原失败'

    return '%d 篇全部解析到，共 %d 张表 %d 行；改 md 不重启即生效' % (
        len(d['docs']), n_tab, n_row)


@case('JQ 策略：与本地归档的关联', tag='fast')
def t_jq():
    """聚宽版的 LOCAL_PORT 必须是断言而不是注释。

    关联写成注释留不住：本地策略一改、标星一换，注释还在那儿，人却不会去改它。
    check.py 核 run_id 在不在归档、params/区间/本金/指标是否对得上 ——
    反向验证过四种改法（改 run_id / params / cash / metrics）都会红。
    """
    import subprocess
    root = os.path.dirname(os.path.abspath(__file__))
    if not os.path.isdir(os.path.join(root, 'jq', 'strategies')):
        return '跳过（jq/ 尚未建立）'
    r = subprocess.run([sys.executable, 'jq/check.py'],
                       capture_output=True, text=True, cwd=root)
    assert r.returncode == 0, 'jq/check.py 失败:\n%s' % (r.stdout + r.stderr)[-600:]
    ok = [l for l in r.stdout.splitlines() if l.strip().startswith('v ')]
    assert ok, 'check.py 没有输出通过行:\n%s' % r.stdout[-400:]
    n_align = sum(int(x) for l in ok for x in re.findall(r'对照 (\d+) 条', l))
    return '%d 个聚宽策略关联核对通过（选股参数逐项对照 %d 条）' % (len(ok), n_align)


@case('网页看板真实渲染（playwright）', tag='web')
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
            # ---- 权益曲线：区间选择，且口径必须与收益明细的年收益一致 ----
            # 曾用「区间内首日」当基点，2024 会显示 +6.52% 而热力图是 +8.68%
            # —— 差的就是首日那根。基点必须取【区间前一交易日】收盘。
            names0 = pg.locator('#tabs div').all_inner_texts()
            pg.locator('#tabs div').nth(names0.index('权益曲线')).click()
            pg.wait_for_timeout(500)
            rgs = pg.locator('#p1 .rg').all_inner_texts()
            assert '全部' in rgs and '近1年' in rgs, '区间条缺预设按钮: %s' % rgs
            ytest = [x for x in rgs if x.isdigit() and len(x) == 4]
            assert len(ytest) >= 2, '区间条没列出年份'
            yb = ytest[len(ytest) // 2]
            pg.locator('#p1 .rg', has_text=re.compile('^%s$' % yb)).first.click()
            pg.wait_for_timeout(600)
            nt = ' '.join(pg.locator('#p1 .note').first.inner_text().split())
            m2 = re.search(r'区间收益\s*([+-][\d.]+)%', nt)
            assert m2, '区间说明里没有区间收益: %s' % nt[:80]
            eq_ret = float(m2.group(1))
            # 用曲线自己算一遍该年收益（基点 = 前一交易日）
            eqj0 = pg.evaluate('({d:DATA.eq.dates, e:DATA.eq.equity})')
            ii = [k for k, d in enumerate(eqj0['d']) if d[:4] == yb]
            want = (eqj0['e'][ii[-1]] / eqj0['e'][ii[0] - 1] - 1) * 100
            assert abs(eq_ret - want) < 0.02, \
                '%s 区间收益 %.2f%% != 曲线算的 %.2f%%（基点取错？）' % (yb, eq_ret, want)
            rng = '区间条 %d 档，%s 区间收益 %+.2f%% 与曲线一致' % (len(rgs), yb, eq_ret)
            pg.locator('#p1 .rg', has_text=re.compile('^全部$')).first.click()
            pg.wait_for_timeout(400)

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
            # ★ 热力格必须【底色 vs 数字】有足够对比 —— 曾经底色和数字同色相
            #   （红底红字），实测对比度只有 1.28:1，等于看不见。
            #   这里量最强的那个格子，要求 >= 4.5:1（WCAG AA）。
            def _lin(c):
                c = c / 255.0
                return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
            def _lum(v):
                r, g, b = [_lin(x) for x in v]
                return 0.2126 * r + 0.7152 * g + 0.0722 * b
            def _rgb(css, under=(23, 27, 33)):
                n = [float(x) for x in re.findall(r'[\d.]+', css)]
                r, g, b = n[:3]; a = n[3] if len(n) > 3 else 1.0
                return tuple((r, g, b)[i] * a + under[i] * (1 - a) for i in range(3))
            st = pg.evaluate("""()=>{let best=null;
              document.querySelectorAll('#p2 .hm.y .hc').forEach(e=>{
                const s=getComputedStyle(e), v=e.querySelector('.v');
                const m=(s.backgroundColor.match(/[\\d.]+/g)||['0','0','0','1']);
                const a=parseFloat(m[3]===undefined?1:m[3]);
                if(!best||a>best.a) best={a,bg:s.backgroundColor,
                                          fg:getComputedStyle(v).color};});
              return best;}""")
            lb, lf = _lum(_rgb(st['bg'])), _lum(_rgb(st['fg']))
            cr = (max(lb, lf) + .05) / (min(lb, lf) + .05)
            assert cr >= 4.5, '热力格对比度只有 %.2f:1（底 %s / 字 %s），红底红字回来了？' \
                              % (cr, st['bg'], st['fg'])

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
            # 点某一天 -> 当日持仓/买卖（读 /api/day，仍是现有归档）
            cd0 = pg.locator('#cal .cd[data-d]').first
            cd0.scroll_into_view_if_needed(); cd0.click(); pg.wait_for_timeout(1200)
            dsel = pg.evaluate('DSEL')
            nh = pg.locator('#d_hold tbody tr').count()
            assert pg.locator('#day .cards .card').count() == 6, '当日概要卡不是 6 张'
            assert nh >= 0 and pg.locator('#d_hold .note').count() == 1, '当日持仓表没渲染'
            # 浏览器后退必须回到上一年详情页（而不是直接掉出详情）
            pg.go_back(); pg.wait_for_timeout(600)
            h = pg.evaluate('location.hash')
            assert h == base_hash or '/y/' in h, '后退没回到收益明细：%s' % h
            pg.locator('#tabs div').nth(names.index('收益明细')).click()
            pg.wait_for_timeout(400)
            drill = ('年热力 %d 格(%s 收益/回撤/交易日对齐曲线，对比度 %.1f:1)；'
                     '%s 年详情独立路由；%s 日历 %d 格(交易 %d/非交易 %d)；'
                     '%s 当日明细 持仓 %d 只；%s'
                     % (yc.count(), ys[0], cr, yr, mk, cal_days, real, cal_off, dsel, nh, rng))

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


@case('数据字典页：侧栏 / 切篇 / 过滤 / 跨篇命中数', tag='web')
def t_docs_ui():
    """服务端渲染对了不代表页面能用 —— 过滤是 DOM 操作，只有真浏览器能验。

    重点验【跨篇命中数】：过滤时侧栏要标出每篇的命中行数，
    否则你只看得到当前这篇，而「这个概念在哪一篇」恰恰是最常问的。
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return '跳过（无 playwright）'
    import threading
    from http.server import ThreadingHTTPServer

    from assay import server as sv
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), sv.Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    errs = []
    try:
        with sync_playwright() as pw:
            b = pw.chromium.launch()
            pg = b.new_page()
            pg.on('pageerror', lambda e: errs.append('PAGEERROR %s' % e))
            pg.on('console',
                  lambda m: errs.append(m.text) if m.type == 'error' else None)
            pg.goto('http://127.0.0.1:%d/#/docs/trap' % port)
            pg.wait_for_timeout(1500)
            n_side = pg.locator('.ditem').count()
            assert n_side >= 8, '侧栏只有 %d 条' % n_side
            src = pg.locator('#dc .dsrc').inner_text()
            assert '4-按陷阱' in src, '打开的不是指定那篇: %s' % src
            tot = pg.locator('#dc table.dt tbody tr').count()
            assert tot > 30, '表格只渲染出 %d 行' % tot

            pg.fill('#dq', 'report_type')
            pg.wait_for_timeout(2200)
            vis = pg.locator('#dc table.dt tbody tr:visible').count()
            assert 0 < vis < tot, '过滤没生效（%d/%d）' % (vis, tot)
            assert pg.locator('#dc .dhi').count() > 0, '命中处没高亮'
            hits = [t.inner_text() for t in pg.locator('.dmeta').all()]
            n_hit = len([x for x in hits if '命中' in x])
            assert n_hit >= 3, '跨篇命中数只标出 %d 篇' % n_hit
            assert pg.locator('.ditem.nohit').count() > 0, '无命中的篇没淡掉'

            pg.fill('#dq', '')
            pg.wait_for_timeout(700)
            back = pg.locator('#dc table.dt tbody tr:visible').count()
            assert back == tot, '清空过滤没恢复（%d != %d）' % (back, tot)

            pg.click('.ditem[href="#/docs/qmt"]')
            pg.wait_for_timeout(900)
            assert pg.locator('#dc table.dt').count() > 5, '切篇后表格没出来'
            pg.click('#back')
            pg.wait_for_timeout(700)
            assert pg.locator('.tree').count() >= 1, '返回目录后目录树没了'
            b.close()
    finally:
        httpd.shutdown()
    assert not errs, '控制台报错: %s' % errs[:3]
    return '侧栏 %d 篇 · %d 行 · 过滤 %d 行 · 跨篇命中标出 %d 篇 · 无控制台错误' % (
        n_side, tot, vis, n_hit)


@case('版本页：简介/源码/参数表单/触发回测', tag='web')
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
    # ★ 服务默认只读，网页触发回测是关的。本用例要测的就是「触发回测」
    #   这条链路，所以显式打开；关键是下面那两条「非法参数被挡住」的断言 ——
    #   只读模式下 api_backtest 本来就返回 error，不打开的话它们会
    #   【因为错误的原因通过】，比失败更危险。
    _prev_ab = sv.ALLOW_BACKTEST
    sv.ALLOW_BACKTEST = True
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

            # 只读模式（默认）必须挡住触发回测，且不能只靠前端置灰
            sv.ALLOW_BACKTEST = False
            r = sv.api_backtest({}, {'sha': sha, 'params': {}})
            assert 'error' in r and '只读' in r['error'], \
                '只读模式没挡住 /api/backtest: %s' % r
            v_ro = sv.api_version({'sha': sha})
            assert v_ro['readonly'] and not v_ro['runnable'], \
                '只读模式下 api_version 仍报 runnable'
            sv.ALLOW_BACKTEST = True

            pg.click('#back'); pg.wait_for_timeout(500)
            assert pg.is_visible('#cat'), '返回目录失败'
            br.close()
            assert not errs, 'JS 报错 %d 处: %s' % (len(errs), errs[:2])
    finally:
        httpd.shutdown()
        sv.ALLOW_BACKTEST = _prev_ab
    return ('简介 %d 条/旧统计已移除/源码 %d 字符/参数 %d 个与引擎一致/'
            '非法参数名与危险值均被拦/只读模式拦住触发回测'
            % (n_note, min(lens), n_param))


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
    # ★ 校验【契约】不校验拼写：策略侧只写占位符 {t_universe}，
    #   「它指向哪个文件」这条知识在 feed._STD_TABLES 里。
    #   两头都查，任一端改错都能逮住（以前只 grep 'security_universe'，
    #   策略改成占位符后就误报了）。
    from assay.feed import std_tables as _stdt
    assert '{t_universe}' in src, 'froec.py 未使用权威宇宙占位符，停牌股仍会缺席'
    assert 'security_universe' in _stdt('/x')['t_universe'], (
        'feed 的表目录里 t_universe 不再指向 security_universe')
    assert 'eps_q >' not in src and 'eps_q>' not in src, (
        'froec.py 的过滤条件仍在用单季 eps_q（聚宽用累计 indicator.eps）')
    assert 'e.eps > 0' in src, 'froec.py 未用 fin_indicator_q 的累计 eps 做过滤'
    return ('面板 %d 行 vs 权威在市 %d 只，缺 %d ≈ 当日停牌 %d；'
            'froec.py 用 {t_universe} 占位符 + 累计 eps' % (n_panel, n_univ, gap, n_paused))


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


@case('QMT 策略：编码 / 编译 / 与本地策略的关联')
def t_qmt():
    """QMT 侧文件必须能在 QMT 里编译，且与本地策略的对应关系可核对。

    关联写成注释是留不住的：本地策略一改、标星一换，注释还在那儿，
    人却不会去改它。所以 LOCAL_PORT 是断言 —— 核对 run_id 在不在归档、
    参数对不对得上、基线指标与 stats.json 是否吻合、QMT 侧常量是不是
    等于默认 profile（即「默认参数 = 回测参数」）。
    """
    import subprocess
    r = subprocess.run([sys.executable, 'qmt/check.py'],
                       capture_output=True, text=True,
                       cwd=os.path.dirname(os.path.abspath(__file__)))
    assert r.returncode == 0, 'qmt/check.py 失败:\n%s' % (r.stdout + r.stderr)[-600:]
    # check.py 的输出分两段：先是 gen --check 的同步校验，再是逐文件关联校验。
    # 只数带「关联」二字的行，否则会把同步那 5 行也算进来（一度报成 10 个文件）。
    ok = [l for l in r.stdout.splitlines() if '关联' in l and l.strip().startswith('v ')]
    n = sum(int(x) for l in ok for x in re.findall(r'关联 (\d+) 个', l))

    # ★ check.py 只管 strategies/ 下生成出来的文件，【不看 qmt/ 根目录的工具脚本】。
    #   实测代价：往 probe_all.py 里写进一个字符串未闭合的补丁，提交推送后
    #   selftest 依然全绿 —— 因为没人编译它。凡是要在 QMT 里粘贴运行的文件，
    #   都必须过「真 GBK + 能编译 + 无模块级 import + 无 emoji」这四条。
    import ast
    root = os.path.dirname(os.path.abspath(__file__))
    tools = [f for f in ('qmt/probe_all.py', 'qmt/gen.py', 'qmt/check.py')
             if os.path.exists(os.path.join(root, f))]
    n_tool = 0
    for rel in tools:
        fp = os.path.join(root, rel)
        raw = open(fp, 'rb').read()
        if rel == 'qmt/probe_all.py':          # 要粘进 QMT 的，编码有硬要求
            assert raw.startswith(b'#coding:gbk'), '%s 缺 #coding:gbk 声明' % rel
            try:
                src = raw.decode('gbk')
            except UnicodeDecodeError as e:
                raise AssertionError('%s 声明了 gbk 但实际不是: %s' % (rel, e))
            emo = [c for c in src if ord(c) > 0x1F000]
            assert not emo, '%s 含 emoji %s —— GBK 编不了，写文件时会截断' % (rel, emo[:3])
            mod_imports = [l for l in src.splitlines()
                           if l[:7] == 'import ' or l[:5] == 'from ']
            assert not mod_imports, \
                '%s 有模块级 import %s —— 本探针一律函数内导入' % (rel, mod_imports[:3])
        else:
            src = raw.decode('utf-8')
        try:
            ast.parse(src)
        except SyntaxError as e:
            raise AssertionError('%s 语法错误: 第 %s 行 %s' % (rel, e.lineno, e.msg))
        n_tool += 1
    syn = [l for l in r.stdout.splitlines() if '<- _tpl/' in l]
    return ('%d 个 QMT 策略文件：与模板同步 %d 个、编译通过、%d 个 profile 与归档一致；'
            '另核 %d 个工具脚本（探针的 GBK/emoji/模块级 import 一并查）'
            % (len(ok), len(syn), n, n_tool))


@case('选中标记：打星 / 冒泡 / 不误触发', tag='web')
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


@case('实盘：持仓重建 / 规则不重写 / 版本留痕', tag='fast')
def t_live_core():
    """四条自证，每条都对应一个【会静默出错】的地方。

    1) 持仓重建：FIFO 冲减顺序错了，成本价就错，止损判定跟着错 —— 不报错
    2) 规则不重写：止损/炸板走的必须是【策略自己的代码路径】。用一个
       刚好跌破阈值的成本价播种，断言捕获到 reason='stop'；再用一个
       刚好不跌破的，断言【不是】 stop。两侧都测，否则"总是返回 stop"
       也能通过
    3) 调仓日判定：面板日历不含未来交易日，序号必须用「历史+未来」重算。
       只补未来那几天的话，本周的桶是截断的，负序号（-1=本周最后一个
       交易日）会错
    4) 版本留痕：**删掉整个 runs/ 目录后仍读得到快照**。这是本模块的
       核心承诺 —— runs/ 在 .gitignore 里，绑到它等于没留痕
    """
    import datetime
    import shutil
    import tempfile

    import duckdb

    from assay import live as lv
    from assay import registry

    root = os.path.dirname(os.path.abspath(__file__))
    tmp = tempfile.mkdtemp(prefix='selftest_live_')
    old_live = lv.LIVE
    lv.LIVE = tmp
    try:
        cal = os.path.join(root, 'live', 'trade_calendar.json')
        if not os.path.exists(cal):
            return '跳过（没有 live/trade_calendar.json）'
        shutil.copy(cal, os.path.join(tmp, 'trade_calendar.json'))

        # ---- 1) FIFO 持仓重建 ----
        rows = [
            {'ts': '1', 'trade_date': '2026-01-05', 'code': 'X.XSHG',
             'side': 'buy', 'shares': 1000, 'price': 10.0, 'fee': 0},
            {'ts': '2', 'trade_date': '2026-02-05', 'code': 'X.XSHG',
             'side': 'buy', 'shares': 1000, 'price': 20.0, 'fee': 0},
            {'ts': '3', 'trade_date': '2026-03-05', 'code': 'X.XSHG',
             'side': 'sell', 'shares': 1500, 'price': 30.0, 'fee': 0},
        ]
        book = lv.fifo_lots(rows)
        got = book['X.XSHG']
        assert len(got) == 1 and got[0]['shares'] == 500, 'FIFO 剩余股数错: %s' % got
        assert got[0]['price'] == 20.0, \
            'FIFO 必须先冲减【最早】那批 —— 剩下的应是 20.0 那批，得到 %s' % got[0]['price']
        # as-of：2026-02-05 当天应是 2000 股
        assert sum(l['shares'] for l in lv.lots_asof(rows, '2026-02-05')['X.XSHG']) == 2000

        # ---- 2) 规则不重写：止损两侧 ----
        con = duckdb.connect()
        panel = os.path.join(os.path.dirname(root), 'datalake',
                             'mart', 'panel_daily', 'panel_2026.parquet')
        if not os.path.exists(panel):
            return '跳过（没有 2026 面板）'
        r = con.execute(
            "SELECT date, low FROM read_parquet('%s') WHERE jq_code='603506.XSHG' "
            "ORDER BY date DESC LIMIT 1" % panel).fetchone()
        day, low = r[0], float(r[1])
        hit, safe = round(low / 0.65 + 0.5, 2), round(low / 0.65 - 1.0, 2)
        seen = {}
        for tag, cost in (('hit', hit), ('safe', safe)):
            aid = 't_' + tag
            lv.upsert_account(aid, name=tag, init_cash=200000)
            lv.bind_version(aid, 'strategies/小市值/froec_traded.py',
                            params={'stop_loss': 0.35, 'stop_intraday': 1,
                                    'weekday': 2})
            lv.add_fill(aid, '2026-08-20', '603506.XSHG', 'buy', 1000, cost)
            sig = lv.build_signal(aid)
            seen[tag] = {s['code']: s['reason'] for s in sig['sell']}
        assert seen['hit'].get('603506.XSHG') == 'stop', \
            '成本较当日最低价跌 %.1f%% > 35%%，应捕获 stop，实得 %s' \
            % (100 * (1 - low / hit), seen['hit'])
        assert seen['safe'].get('603506.XSHG') != 'stop', \
            '成本只跌 %.1f%% < 35%%，不该是 stop，实得 %s' \
            % (100 * (1 - low / safe), seen['safe'])

        # ---- 3) 调仓日判定 ----
        nxt = lv.next_trading_day(day)
        assert nxt > day, 'next_trading_day 必须严格晚于 %s' % day
        lv.upsert_account('t_hl', name='hl', init_cash=1000000)
        lv.bind_version('t_hl', 'strategies/红利/红利指数增强.py',
                        params={'div_method': 'fiscal_year'})
        s_hl = lv.build_signal('t_hl')
        # 红利是 run_monthly(monthday=1)；froec_traded 是 run_weekly(weekday=2)
        assert s_hl['for_date'] == nxt.isoformat()
        # ★ 不能无条件断言 15 只 —— 下一个交易日是不是月内第 1 个交易日
        #   取决于【今天是哪天】。数据同步推进一天后这条就假失败了（实测）。
        #   正确的写法是把断言挂在 is_rebalance_day 上。
        if s_hl['is_rebalance_day']:
            assert len(s_hl['buy']) == 15, \
                '红利空仓且是调仓日，应给 15 只，实得 %d' % len(s_hl['buy'])
        else:
            assert not s_hl['buy'], '非调仓日不该有买入'

        # ---- 3b) 非调仓日：持有不动必须是【全部持仓】，不是 targets ----
        #     targets 在非调仓日是空的，照它算会显示成 0 只 —— 看着像空仓，
        #     而实际上你满仓。这类"显示成空"的错不会报错。
        lv.upsert_account('t_nr', name='nr', init_cash=200000)
        lv.bind_version('t_nr', 'strategies/小市值/froec_traded.py',
                        params={'stop_loss': 0.35, 'stop_intraday': 1,
                                'weekday': 4})       # 周四调仓 -> 下一交易日多半不是
        lv.add_fill('t_nr', '2026-08-20', '603506.XSHG', 'buy', 1000, 11.0)
        s_nr = lv.build_signal('t_nr')
        if not s_nr['is_rebalance_day']:
            assert len(s_nr['hold']) == 1, \
                '非调仓日"持有不动"应为全部持仓 1 只，实得 %d' % len(s_nr['hold'])
            assert not s_nr['buy'], '非调仓日不该有买入'

        # ---- 3c) 现金账务：初始资金 + 流水 − 买 + 卖 ----
        #     ★ 入金必须是【事件】。改 init_cash 会把它追溯到开户日，
        #       于是过去每一天的权益都变了，而且没有任何痕迹说明它变过。
        lv.upsert_account('t_cash', name='cash', init_cash=100000)
        assert abs(lv.cash('t_cash') - 100000) < 1e-6
        lv.add_cashflow('t_cash', '2026-08-10', 50000, 'deposit', '入金')
        assert abs(lv.cash('t_cash') - 150000) < 1e-6, '入金没进现金'
        lv.add_cashflow('t_cash', '2026-08-11', 20000, 'withdraw', '出金')
        assert abs(lv.cash('t_cash') - 130000) < 1e-6, '出金没扣'
        lv.bind_version('t_cash', 'strategies/小市值/froec_traded.py',
                        params={'stop_loss': 0.35, 'stop_intraday': 1, 'weekday': 2})
        lv.add_fill('t_cash', '2026-08-20', '603506.XSHG', 'buy', 1000, 11.0, fee=5)
        assert abs(lv.cash('t_cash') - (130000 - 11000 - 5)) < 1e-6, \
            '买入没扣现金/费用：%.2f' % lv.cash('t_cash')
        # 金额一律正数，方向由 kind 决定 —— 负数要被拒（避免"负的出金"双重否定）
        for bad_args, why in ((('2026-08-12', -100, 'deposit'), '负金额'),
                              (('2026-08-12', 999999, 'withdraw'), '出金超过现金'),
                              (('2026-08-12', 100, 'nope'), '未知类型')):
            try:
                lv.add_cashflow('t_cash', *bad_args)
                raise AssertionError('%s 应被拒' % why)
            except lv.LiveError:
                pass
        # as-of：8-10 那天还没入过 5 万之后的出金
        assert abs(lv.cash_asof(100000, [], '2026-08-10',
                                lv.cashflows('t_cash')) - 150000) < 1e-6, \
            'cash_asof 没按日期截断现金流水'

        # ---- 3d) 成交费用三态：留空=估算 / 填值=按填的 / 填 0=真的 0 ----
        #     ★ `fee=None`（没填）与 `fee=0`（明确说没有费用）是两件事。
        #       默认 0 会让现金越算越多，而"多出来的钱"不报错，只是让权益
        #       悄悄虚高 —— 年换手 4.5 次的话一年约 0.5%。
        from assay.broker import Cost as _C
        lv.upsert_account('t_fee', name='fee', init_cash=100000)
        lv.bind_version('t_fee', 'strategies/小市值/froec_traded.py',
                        params={'stop_loss': 0.35, 'stop_intraday': 1, 'weekday': 2})
        r1 = lv.add_fill('t_fee', '2026-08-20', '601857.XSHG', 'buy', 1000, 11.42)
        assert r1['fee_estimated'] is True, '不填费用应标 fee_estimated'
        assert r1['fee'] == lv.estimate_fee('buy', 1000, 11.42, '2026-08-20')
        r2 = lv.add_fill('t_fee', '2026-08-21', '601088.XSHG', 'buy', 100, 48.78,
                         fee=3.21)
        assert r2['fee'] == 3.21 and not r2.get('fee_estimated')
        r3 = lv.add_fill('t_fee', '2026-08-22', '600012.XSHG', 'buy', 200, 16.69,
                         fee=0)
        assert r3['fee'] == 0 and not r3.get('fee_estimated'), \
            '明确填 0 不该被当成"没填"而去估算'
        exp = 100000 - (1000 * 11.42 + r1['fee']) - (100 * 48.78 + 3.21) \
            - (200 * 16.69)
        assert abs(lv.cash('t_fee') - exp) < 1e-6, \
            '现金没把费用扣进去：%.2f vs %.2f' % (lv.cash('t_fee'), exp)
        # 估算口径必须与引擎一致（同一个 Cost，公式抄 broker 的两个成交出口）
        c = _C()
        for side, sh, px, d in (('buy', 1000, 11.42, '2023-08-25'),
                                ('sell', 1000, 11.42, '2023-08-25'),
                                ('sell', 1000, 11.42, '2023-08-28')):
            amt = sh * px
            want = max(amt * c.commission, c.min_commission) + (
                amt * c.close_tax_at(lv._d(d)) if side == 'sell'
                else amt * c.open_tax)
            assert abs(lv.estimate_fee(side, sh, px, d) - round(want, 2)) < 1e-9, \
                '%s %s 的估算与引擎口径不一致' % (side, d)
        # 印花税分段必须生效（2023-08-28 起 千1 -> 万5）
        assert lv.estimate_fee('sell', 1000, 11.42, '2023-08-25') > \
            lv.estimate_fee('sell', 1000, 11.42, '2023-08-28'), \
            '印花税没按日期分段'
        try:
            lv.add_fill('t_fee', '2026-08-23', '601857.XSHG', 'sell', 100,
                        11.0, fee=-1)
            raise AssertionError('非冲正记录的负费用应被拒')
        except lv.LiveError:
            pass

        # ---- 3e) 冲正必须【精确抵消】，含费用 ----
        #     冲正的语义是"这笔交易没发生"，所以原费用也要退掉。若冲正记录
        #     照常估一笔费用，一笔没发生的交易会净吃【两次】费用而不报错。
        lv.upsert_account('t_rev', name='rev', init_cash=100000)
        lv.bind_version('t_rev', 'strategies/小市值/froec_traded.py',
                        params={'stop_loss': 0.35, 'stop_intraday': 1, 'weekday': 2})
        c_before = lv.cash('t_rev')
        rb = lv.add_fill('t_rev', '2026-08-20', '601857.XSHG', 'buy', 1000, 11.42)
        assert rb['fee'] > 0, '买入应估出费用'
        lv.add_fill('t_rev', '2026-08-20', '601857.XSHG', 'sell', 1000, 11.42,
                    fee=-rb['fee'], reverse_of=rb['uid'])
        assert abs(lv.cash('t_rev') - c_before) < 1e-9, \
            '冲正后现金应精确回到 %.2f，实得 %.2f（差 %.2f = 多吃的费用）' \
            % (c_before, lv.cash('t_rev'), lv.cash('t_rev') - c_before)
        assert not lv.positions('t_rev'), '冲正后应空仓'
        # 冲正不填费用时不该去估算（估了就抵消不掉）
        rb2 = lv.add_fill('t_rev', '2026-08-21', '601088.XSHG', 'buy', 100, 48.78)
        rv2 = lv.add_fill('t_rev', '2026-08-21', '601088.XSHG', 'sell', 100, 48.78,
                          reverse_of=rb2['uid'])
        assert rv2['fee'] == 0 and not rv2.get('fee_estimated'), \
            '冲正不填费用时应记 0 而不是估算，实得 %s' % rv2['fee']

        # ---- 3f) 改记录的完整链路：ts 不能当主键 / 冲正要还原批次 / 重放校验 ----
        #     ★ 这一组是"录错了能不能改对"的全部保证。三个都曾经是 bug：
        #       1. `ts` 是秒精度，同一秒两笔撞成同一"身份"，冲正配对到错的
        #          记录 -> FIFO 批次不还原 -> 成本价与建仓日错 -> 止损判错
        #       2. 已冲正的记录仍参与 fifo_lots -> 批次变成
        #          [500@10 建仓08-10, 500@12 建仓08-20]，而实际什么都没发生
        #       3. 校验只看"当前持仓" -> 被后续记录依赖的那笔【永远改不了】，
        #          且补录更早日期的卖出会被放行
        lv.upsert_account('t_edit', name='edit', init_cash=100000)
        lv.bind_version('t_edit', 'strategies/小市值/froec_traded.py',
                        params={'stop_loss': 0.35, 'stop_intraday': 1, 'weekday': 2})
        eb = lv.add_fill('t_edit', '2026-08-10', '601857.XSHG', 'buy', 1000, 10.0, fee=5)
        es = lv.add_fill('t_edit', '2026-08-20', '601857.XSHG', 'sell', 500, 12.0, fee=8)
        assert eb['uid'] != es['uid'], 'uid 必须唯一（ts 是秒精度，会撞）'
        # 被依赖的那笔不能直接冲，但要给出下一步
        try:
            lv.add_fill('t_edit', '2026-08-10', '601857.XSHG', 'sell', 1000, 10.0,
                        fee=-5, reverse_of=eb['uid'])
            raise AssertionError('被后续记录依赖的冲正应被拒')
        except lv.LiveError as e:
            assert '负持仓' in str(e), '拒绝理由要说清是负持仓：%s' % e
        # 先冲后续那笔 -> 批次必须【完全还原】
        lv.add_fill('t_edit', '2026-08-20', '601857.XSHG', 'buy', 500, 12.0,
                    fee=-8, reverse_of=es['uid'])
        # positions() 是接口层，date 已转 ISO 字符串（见其 docstring）；
        # 内部计算用 fifo_lots 才拿 date 对象。这里比字符串。
        lots = lv.positions('t_edit')['601857.XSHG']['lots']
        assert len(lots) == 1 and lots[0]['shares'] == 1000 \
            and abs(lots[0]['price'] - 10.0) < 1e-9 \
            and str(lots[0]['date'])[:10] == '2026-08-10', \
            ('冲正后批次没还原成 [1000@10.0 建仓 08-10]，实得 %s'
             ' —— 成本价/建仓日直接喂给止损判定' % lots)
        # 再冲原始那笔 -> 现金精确回到初始、空仓
        lv.add_fill('t_edit', '2026-08-10', '601857.XSHG', 'sell', 1000, 10.0,
                    fee=-5, reverse_of=eb['uid'])
        assert not lv.positions('t_edit'), '全部冲正后应空仓'
        assert abs(lv.cash('t_edit') - 100000) < 1e-9, \
            '全部冲正后现金应精确回到 100000，实得 %.2f' % lv.cash('t_edit')
        # 补录一笔日期在所有买入【之前】的卖出 —— 只看当前持仓会放行
        lv.add_fill('t_edit', '2026-09-01', '601088.XSHG', 'buy', 1000, 48.0, fee=5)
        try:
            lv.add_fill('t_edit', '2026-08-01', '601088.XSHG', 'sell', 1000, 48.0,
                        fee=5)
            raise AssertionError('补录更早日期的卖出应被拒（重放时持仓为负）')
        except lv.LiveError:
            pass
        assert len(lv.fills('t_edit')) == 5 and \
            len(lv.active_fills(lv.fills('t_edit'))) == 1, \
            '账本应留全部 5 条、有效 1 条（冲正只追加不删）'

        # ---- 3g) 收益必须是【时间加权】—— 入金不算收益 ----
        lv.upsert_account('t_twr', name='twr', init_cash=100000)
        lv.bind_version('t_twr', 'strategies/小市值/froec_traded.py',
                        params={'stop_loss': 0.35, 'stop_intraday': 1, 'weekday': 2})
        lv.add_fill('t_twr', '2026-08-10', '601857.XSHG', 'buy', 5000, 11.0, fee=15)
        e1 = lv.equity_curve('t_twr')
        assert e1['stats'] and e1['dates'], '权益曲线为空'
        assert len(e1['dates']) == len(e1['equity']), '日期与权益长度不一致'
        t1 = e1['stats']['twr']
        lv.add_cashflow('t_twr', '2026-08-25', 500000, 'deposit', '测试入金')
        e2 = lv.equity_curve('t_twr')
        naive = e2['equity'][-1] / 100000 - 1
        assert naive > 4, '构造有误：入金后简单相除应远大于真实收益'
        assert abs(e2['stats']['twr']) < 0.5, \
            ('TWR 把入金算成收益了：%.4f（简单相除 %.4f）'
             % (e2['stats']['twr'], naive))
        assert e2['stats']['net_deposit'] == 500000, '净入金没记对'

        # ---- 4) 版本留痕：删掉 runs/ 也读得到 ----
        v = lv.versions('t_hit')
        assert len(v) == 1 and v[0]['files'], 'versions.jsonl 没记文件清单'
        assert len(v[0]['files']) >= 2, \
            'froec_traded 依赖 froec.py，快照必须包含两者，实得 %s' % v[0]['files']
        code, full, names = lv.version_code('t_hit', v[0]['code_sha256'])
        assert 'froec.py' in names, '依赖没进快照: %s' % names
        # 把归档目录整个从视野里拿掉，再读一次
        old_runs = registry.RUNS
        try:
            registry.RUNS = os.path.join(tmp, '_no_such_runs')
            code2, _f, _n = lv.version_code('t_hit', v[0]['code_sha256'])
            assert code2 == code, 'runs/ 不可见时快照读出来的内容变了'
        finally:
            registry.RUNS = old_runs
        # 改磁盘文件 -> 版本哈希必须变（否则版本会悄悄漂移）
        p = os.path.join(root, 'strategies', '小市值', 'froec.py')
        raw = open(p, 'rb').read()
        try:
            open(p, 'wb').write(raw + b'\n# selftest\n')
            lv.bind_version('t_hit', 'strategies/小市值/froec_traded.py',
                            params={'stop_loss': 0.35, 'stop_intraday': 1,
                                    'weekday': 2}, reason='改了依赖')
        finally:
            open(p, 'wb').write(raw)
        v2 = lv.versions('t_hit')
        assert len(v2) == 2, '换版本必须【追加】一行，不是改写'
        assert v2[1]['code_sha256'] != v2[0]['code_sha256'], \
            '只改了依赖 froec.py，版本哈希却没变 —— 版本会悄悄漂移'
        for row in v2:
            c, _f, _n = lv.version_code('t_hit', row['code_sha256'])
            assert c, '历史版本 %s 读不出来' % row['code_sha']

        return ('FIFO 剩 500@20.0；现金账务(入/出金+成交+费用三态+as-of)对；'
                'uid 唯一 + 冲正还原批次 + 重放拦负持仓 + TWR 排除入金；'
                '止损两侧 %.1f%%→stop / %.1f%%→非stop；'
                '红利 %s %s 买入 %d 只；快照含 %d 个文件、删 runs/ 仍可读、'
                '改依赖后哈希变 %s→%s'
                % (100 * (1 - low / hit), 100 * (1 - low / safe),
                   s_hl['for_date'],
                   '调仓日' if s_hl['is_rebalance_day'] else '非调仓日',
                   len(s_hl['buy']), len(v[0]['files']),
                   v2[0]['code_sha'], v2[1]['code_sha']))
    finally:
        lv.LIVE = old_live
        shutil.rmtree(tmp, ignore_errors=True)


@case('实盘页面真实渲染（playwright）', tag='web')
def t_live_ui():
    """★ JS 语法过 ≠ 能渲染。运行时错误在终端里看不到，页面只是空白。

    跑一遍真流程：建账户 -> 绑版本 -> 出信号 -> 待办表 -> 批量录入解析 ->
    版本历史。并断言【告警确实显示出来了】—— 日历不是权威来源这件事
    如果只存在于 JSON 里没渲染到页面上，等于没有。
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return '跳过（无 playwright）'
    import datetime
    import shutil
    import tempfile
    import threading
    from http.server import ThreadingHTTPServer

    from assay import live as lv
    from assay import server as sv

    root = os.path.dirname(os.path.abspath(__file__))
    cal = os.path.join(root, 'live', 'trade_calendar.json')
    if not os.path.exists(cal):
        return '跳过（没有 live/trade_calendar.json）'
    tmp = tempfile.mkdtemp(prefix='selftest_liveui_')
    old_live, old_allow = lv.LIVE, sv.ALLOW_LIVE
    lv.LIVE = tmp
    sv.ALLOW_LIVE = True
    shutil.copy(cal, os.path.join(tmp, 'trade_calendar.json'))
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
            base = 'http://127.0.0.1:%d/' % port
            pg.goto(base + '#/live', wait_until='networkidle')
            pg.wait_for_timeout(500)
            assert '还没有账户' in pg.content(), '空态没显示'
            # 日历告警必须渲染到页面上，不能只躺在 JSON 里
            assert pg.locator('.lvwarn').count() >= 1, '日历非权威来源的告警没渲染'

            # 建账户
            # id 自动分配 —— 用户只填名称
            assert pg.locator('#na').count() == 0, '新建表单不该再要用户填 id'
            pg.fill('#nn', 'UI 测试')
            pg.fill('#nc', '1000000')
            pg.click('#nb')
            pg.wait_for_timeout(600)
            assert pg.locator('.ditem.on').count() == 1, '新账户没被选中'

            # 绑版本 -> 出信号
            # ★ weekday 按【下一个交易日在本周的序号】动态算，保证它一定是
            #   调仓日 —— 否则没有买入表，用例会随日期时好时坏（实测：
            #   数据同步推进到 09-01 后，下一个交易日 09-02 不是红利的
            #   月频调仓日，硬等 table.lvbuy 直接超时）。
            # ★ 基准日要从【数据】取，不是从 today 取 —— 引擎用的是
            #   feed.trading_days[-1]（最新有行情的一天）。实测踩到：
            #   今天 09-02 而数据到 09-01，按 today 算出的 weekday 是给
            #   09-03 的，于是 09-02 不是调仓日、买入表永远等不出来。
            from assay.feed import PanelFeed
            days = lv.calendar_days()
            t1 = PanelFeed('2026-01-01', datetime.date.today().isoformat()
                           ).trading_days[-1]
            nxt = min(d for d in days if d > t1)
            wk = [d for d in days if d.isocalendar()[:2] == nxt.isocalendar()[:2]]
            wd = wk.index(nxt) + 1
            pg.fill('#bp', 'strategies/小市值/froec_traded.py')
            pg.fill('#bj', '{"stop_loss":0.35,"stop_intraday":1,"weekday":%d}' % wd)
            pg.click('#bb')
            pg.wait_for_timeout(800)
            pg.click('#lvtick')
            pg.wait_for_selector('table.lvbuy', timeout=90000)
            nbuy = pg.locator('table.lvbuy tr').count() - 1
            assert nbuy == 10, '待办买入应 10 行（froec 满仓 10 只），实得 %d' % nbuy
            assert '· 调仓日' in pg.content(), \
                'weekday=%d 应让 %s 成为调仓日，页面却没标出' % (wd, nxt)

            # 批量粘贴解析（走前端 parseBulk -> 后端校验）
            first = pg.locator('table.lvbuy tr').nth(1).locator('td').first.inner_text()
            code = first.split()[0]
            pg.fill('#ft', '2026-09-01 %s 买 100 10.00' % code)
            pg.click('#fbulk')
            pg.wait_for_timeout(900)
            assert '100' in pg.locator('.lvfill').inner_text(), '成交流水里没出现刚录的那笔'

            # 版本历史可点开
            assert pg.locator('a.lvver').count() >= 1, '版本历史没渲染'

            # ---- 账户名与策略解耦：改名 / 改初始资金 ----
            # ★ 原来只有"新建"没有"编辑"，等于把「一段时间用策略 A、之后换 B」
            #   这种正常演化逼成「新建一个账户」，流水与版本历史就断了。
            def _cash():
                t = pg.locator('.lvtag').first.inner_text()
                return float(t.replace('现金', '').replace(',', '').strip())

            # ★ 断言【差额】不是绝对值 —— 前面已经录过一笔买入，现金里扣掉了
            #   那笔成交额。写死 888,888 会假失败（实测 887,888）。
            pg.fill('#ename', '改过的名字')
            pg.fill('#ecash', '888888')
            pg.click('#esave')
            pg.wait_for_timeout(900)
            assert '改过的名字' in pg.locator('.lvhead h2').first.inner_text(), '改名没生效'
            c1 = _cash()
            assert 880000 < c1 < 888888, \
                '改初始资金没生效或算错：现金 %.2f（应为 888888 减去已录成交额）' % c1

            # ---- 现金流水（入金）----
            # init_cash 是【开户那一刻】的余额；后来的入金必须是事件，
            # 改 init_cash 会把它追溯到开户日、且不留痕迹。
            pg.fill('#cfa', '20000')
            pg.click('#cfb')
            pg.wait_for_timeout(900)
            c2 = _cash()
            assert abs((c2 - c1) - 20000) < 0.01, \
                '入金 20000 后现金应 +20000，实得 %.2f -> %.2f' % (c1, c2)

            # ---- 版本表参数列不能溢出容器 ----
            bx = pg.locator('table.lvvt').bounding_box()
            cw = pg.locator('table.lvvt').evaluate(
                "e=>e.closest('.lvsec').getBoundingClientRect().width")
            assert bx['width'] <= cw + 1, \
                '版本表 %.0f 超出容器 %.0f —— 参数列又顶出去了' % (bx['width'], cw)
            assert pg.locator('.lvkv').count() >= 3, \
                '参数应拆成 chip 显示，实得 %d 个' % pg.locator('.lvkv').count()

            # ---- 策略详情：源码 + 参数表 + 关联的历史回测 ----
            pg.click('#lvstrat')
            pg.wait_for_selector('.stbox', timeout=20000)
            pg.wait_for_timeout(400)
            box = pg.locator('.stbox').inner_text()
            assert len(pg.locator('.stcode').inner_text()) > 1000, '源码快照没渲染'
            assert pg.locator('.stbox table.lvt tr').count() > 1, '参数表没渲染'
            # 关联键是【主文件自身哈希】，不是账户的打包哈希。两者口径不同，
            # 用错会永远匹配不上 —— 所以这里核"要么列出回测，要么明说没有"。
            assert ('这个版本从没回测过' in box) or ('同参数' in box) or ('其它参数' in box), \
                '既没列出关联回测、也没说明没有：%s' % box[:200]
            pg.click('#stclose')
            pg.wait_for_timeout(300)
            assert pg.locator('.stbox').count() == 0, '策略详情关不掉'

            # ---- 冲正：追加反向记录，原记录保留并划掉 ----
            pg.on('dialog', lambda d: d.accept())
            pg.locator('a.lvrv').first.click()
            pg.wait_for_timeout(1200)
            nrev = pg.locator('.lvfill tr.lvrev').count()
            assert nrev >= 2, '冲正后原记录与冲正记录都该划掉，实得 %d 行' % nrev
            assert pg.locator('a.lvrv').count() == 0, '已冲正的记录不该再有冲正入口'

            # ---- 费用：三个录入路径都要能填，且"留空"≠"0" ----
            assert pg.locator('#ff').count() == 1, '逐笔表单缺费用输入框'
            assert pg.input_value('#fd'), \
                '日期框不该是空的 —— 空了会让第二笔起静默失败（实测踩到）'

            def _c():
                t = pg.locator('.lvtag').first.inner_text()
                return float(t.replace('现金', '').replace(',', '').strip())

            for code, q, px, fv, amt, want in (
                    ('601857.XSHG', '1000', '11.42', '', 11420.0, None),
                    ('601088.XSHG', '100', '48.78', '3.21', 4878.0, 3.21),
                    ('600012.XSHG', '200', '16.69', '0', 3338.0, 0.0)):
                b4 = _c()
                pg.fill('#fc', code)
                pg.fill('#fq', q)
                pg.fill('#fp', px)
                pg.fill('#ff', fv)
                pg.click('#fb')
                pg.wait_for_timeout(1000)
                msg = (pg.locator('#fmsg').inner_text()
                       if pg.locator('#fmsg').count() else '')
                assert not msg, '录入 %s 失败：%s' % (code, msg)
                got = round(b4 - _c() - amt, 2)
                if want is None:
                    assert got > 0, '费用留空应估算出正数，实得 %.2f' % got
                else:
                    assert abs(got - want) < 0.011, \
                        '费用应为 %.2f，实得 %.2f' % (want, got)
            # 流水标题要报费用合计与估算笔数
            h = pg.locator('.lvsec.wide h3').last.inner_text()
            assert '费用合计' in h and '估算' in h, '流水标题没报费用合计/估算笔数：%s' % h
            assert pg.locator('.lvfill').inner_text().count('估') >= 1, \
                '估算的那笔没标「估」'

            # ---- 归档：从列表隐去，但数据必须还在 ----
            # ★ 刻意没有「删除账户」—— 实盘流水与版本快照是决策证据。
            aid = pg.locator('.ditem.on').get_attribute('href').split('/')[-1]
            pg.click('#earch')
            pg.wait_for_timeout(900)
            assert pg.locator('.ditem').count() == 0, '归档后仍出现在默认列表里'
            pg.click('#lvall')
            pg.wait_for_timeout(700)
            assert pg.locator('.ditem.miss').count() == 1, '「显示已归档」没把它列出来'
            assert lv.fills(aid), '归档不该动数据，流水却空了'
            assert lv.versions(aid), '归档不该动数据，版本历史却空了'

            # 卖出校验：超过持仓必须被拒（后端把关，不能只靠前端）
            pg.fill('#ft', '2026-09-01 %s 卖 900 10.00' % code)
            pg.click('#fbulk')
            pg.wait_for_timeout(700)
            # ★ 断言"被拒"这件事本身 + 理由里提到持仓，不去匹配具体措辞 ——
            #   校验从「只看当前持仓」改成「重放整个账本」后措辞就变了，
            #   而规则并没有变。断言绑死文案会在这种时候假失败。
            _m = pg.locator('#fmsg').inner_text()
            assert '录入 0 笔' in _m and '持仓' in _m, '超卖没被拒：%s' % _m

            br.close()
            assert not errs, '页面有运行时错误：%s' % errs[:3]
            return ('建账户(id 自动)/绑版本(weekday=%d 使 %s 成调仓日)/出信号(买入 %d 行)/'
                    '批量录入/超卖被拒/改名/改初始资金/入金/费用三态/版本表不溢出/'
                    '策略详情(源码+参数+关联回测)/冲正/归档(数据仍在) 均正常，0 个 JS 错误'
                    % (wd, nxt, nbuy))
    finally:
        httpd.shutdown()
        lv.LIVE, sv.ALLOW_LIVE = old_live, old_allow
        shutil.rmtree(tmp, ignore_errors=True)


@case('数据同步：日历对数 / 两条腿语义 / 脚本可执行', tag='fast')
def t_sync():
    """三条自证，都对应一个会静默出错的地方。

    1) **交易日历规则**：交易日 = 工作日 − tdx.raw_holidays。这条规则要能
       与权威日历（std/trading_calendar.parquet，从行情反推）**逐日一致**，
       否则外推到未来的调仓日就是错的 —— 而错的表现是「该调仓的日子没提示」
       或「休市日发一堆单」，两者都不报错。生成脚本每次都重跑对数、
       不一致就拒绝写出，这里核它确实还成立。
    2) **两条腿的落后语义不能混**：A 腿（行情）每个交易日必然有新数据，
       缺了就是同步没跑；B 腿（财务）是**事件驱动**，没公告的日子本来就
       没有新 pub_date，按交易日算落后是【必然误报】。混用会让页面天天
       标红，然后你就不看红字了 —— 告警失效比没有告警更糟。
    3) **同步脚本语法可执行**：它由 launchd 跑，坏了没人看得见。
    """
    import datetime
    import json
    import subprocess

    import duckdb

    root = os.path.dirname(os.path.abspath(__file__))
    dl = os.path.join(os.path.dirname(root), 'datalake')
    tdx = os.path.join(os.path.dirname(root), 'tdx2db', 'tdx.db')
    if not os.path.exists(tdx):
        return '跳过（没有 tdx.db）'

    # ---- 1) 日历规则对数 ----
    con = duckdb.connect()
    con.execute("ATTACH '%s' AS t (READ_ONLY)" % tdx)
    hol = {r[0] for r in con.execute('SELECT date FROM t.raw_holidays').fetchall()}
    truth = [r[0] for r in con.execute(
        "SELECT date FROM read_parquet('%s/std/trading_calendar.parquet') ORDER BY date"
        % dl).fetchall()]
    assert truth, '权威日历为空'
    lo, hi = truth[0], truth[-1]
    gen, d = [], lo
    while d <= hi:
        if d.weekday() < 5 and d not in hol:
            gen.append(d)
        d += datetime.timedelta(days=1)
    ts, gs = set(truth), set(gen)
    assert ts == gs, ('「工作日 − raw_holidays」不再等于权威日历：'
                      '漏判休市 %d 天 %s / 多判交易 %d 天 %s'
                      % (len(ts - gs), sorted(ts - gs)[:5],
                         len(gs - ts), sorted(gs - ts)[:5]))

    # 生成出来的 live 日历要有未来交易日，且来源可信
    calp = os.path.join(root, 'live', 'trade_calendar.json')
    assert os.path.exists(calp), '缺 live/trade_calendar.json —— 实盘拿不到下一个交易日'
    cal = json.load(open(calp, encoding='utf-8'))
    from assay import live as lv
    assert cal['source'] in lv.AUTHORITATIVE_CAL, \
        '日历来源 %r 不在可信名单里' % cal['source']
    nfut = len([x for x in cal['days'] if x > hi.isoformat()])
    assert nfut > 100, '未来交易日只有 %d 天，太少 —— 外推窗口不够' % nfut

    # ---- 2) 两条腿语义 ----
    ss = os.path.join(dl, 'build', 'sync_status.py')
    assert os.path.isfile(ss), '缺 %s' % ss
    r = subprocess.run(['python3', ss, '--json'], cwd=dl,
                       capture_output=True, text=True, timeout=180)
    assert r.returncode == 0, 'sync_status.py 挂了：%s' % (r.stderr or '')[-300:]
    st = json.loads(r.stdout)
    a = [i for i in st['items'] if i['leg'] == 'A']
    b = [i for i in st['items'] if i['leg'] == 'B']
    assert a and b, '两条腿都要有检查项'
    assert all(i.get('lag_days') is not None or i.get('error') for i in a), \
        'A 腿必须报「落后几个交易日」'
    assert all(i.get('lag_days') is None for i in b), \
        'B 腿不该报交易日落后（财务是事件驱动，按交易日算是必然误报）：%s' \
        % [i for i in b if i.get('lag_days') is not None]
    assert all('days_since' in i or i.get('error') for i in b), \
        'B 腿必须报「距今几天」'

    # ---- 3) 脚本可执行 ----
    sh = os.path.join(dl, 'sync_daily.sh')
    assert os.path.isfile(sh), '缺 sync_daily.sh'
    r = subprocess.run(['bash', '-n', sh], capture_output=True, text=True)
    assert r.returncode == 0, 'sync_daily.sh 语法错误：%s' % r.stderr[-300:]
    src = open(sh, encoding='utf-8').read()
    # ★ 只看【真命令行】，剥掉注释 —— 本会话已经三次栽在"断言匹配到自己写的
    #   注释文本"上（sync_daily.sh 的文件头正解释了为什么不调那两个坏脚本，
    #   于是 `'scripts/update.sh' not in src` 必然失败）。
    #   注释是给人看的说明，断言必须针对会被执行的东西。
    code = '\n'.join(ln.split('#', 1)[0] for ln in src.splitlines())
    for bad in ('scripts/update.sh', 'scripts/full_update.sh'):
        assert bad not in code, \
            'sync_daily.sh 不该调 %s —— 它引用的 fast_update_indicators.py 不存在' % bad
    assert 'daily_snapshot.py' in code, 'PIT 快照那步不能少（漏一天永久丢失）'
    plist = os.path.join(dl, '_manifest', 'com.miraclegu.finacial.sync.plist')
    assert os.path.isfile(plist), '缺 launchd plist'
    r = subprocess.run(['plutil', '-lint', plist], capture_output=True, text=True)
    assert r.returncode == 0, 'plist 格式错误：%s' % r.stdout

    la = max((i['lag_days'] for i in a if i['lag_days'] is not None), default=0)
    return ('日历 %d 天逐日一致（%s~%s）+ 未来 %d 天；A 腿落后 %d 交易日；'
            'B 腿距今 %s 天；脚本/plist 均可执行'
            % (len(truth), lo, hi, nfut, la,
               '/'.join(str(i.get('days_since')) for i in b)))


@case('数据同步页面真实渲染（playwright）', tag='web')
def t_sync_ui():
    """两条腿的表格要显示【不同语义】，并且告警必须真的渲染到页面上。

    ★ 判据在服务端（datalake/build/sync_status.py），页面只负责显示。
      这里核的是「显示没把两条腿混起来」—— A 腿报落后几个交易日，
      B 腿报距今几天。混了会让页面天天标红，然后你就不看红字了，
      告警失效比没有告警更糟。
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return '跳过（无 playwright）'
    import threading
    from http.server import ThreadingHTTPServer

    from assay import server as sv
    old = sv.ALLOW_LIVE
    sv.ALLOW_LIVE = False          # 只读：断言手动同步按钮被置灰
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
            pg.goto('http://127.0.0.1:%d/#/sync' % port, wait_until='networkidle')
            pg.wait_for_selector('.lvsec', timeout=90000)
            pg.wait_for_timeout(400)
            txt = pg.content()
            assert 'A 腿' in txt and 'B 腿' in txt, '两条腿的分区没渲染'
            # ★ 只取【表格单元格】，不取整节文本 —— 本会话第四次栽在
            #   "断言匹配到自己写的说明文案"上：B 腿那节的说明里正写着
            #   「B 腿的"落后"不按交易日算」，于是 `'落后' not in b_txt` 必然失败。
            #   要断言渲染结果就只看渲染结果，别把旁边的散文一起吃进来。
            secs = pg.locator('.lvsec')
            a_cells = secs.nth(0).locator('table.lvt td').all_inner_texts()
            b_cells = secs.nth(1).locator('table.lvt td').all_inner_texts()
            a_txt, b_txt = ' | '.join(a_cells), ' | '.join(b_cells)
            assert a_cells and b_cells, '两条腿的表格都要有行'
            assert '落后' in a_txt or '最新' in a_txt, \
                'A 腿该显示「落后 N 个交易日」或「最新」，实得: %s' % a_txt[:120]
            assert '距今' in b_txt, \
                'B 腿该显示「距今 N 天」，实得: %s' % b_txt[:120]
            assert '落后' not in b_txt, \
                'B 腿的表格里不该出现「落后」—— 财务是事件驱动，'\
                '按交易日算是必然误报。实得: %s' % b_txt[:120]
            # 日历来源要显示，且是可信来源（不然实盘会拿不到下一个交易日）
            from assay import live as lv
            head = pg.locator('.lvhead').inner_text()
            assert '日历' in head, '没显示日历来源'
            assert any(x in head for x in lv.AUTHORITATIVE_CAL), \
                '日历来源不在可信名单里: %s' % head
            # 只读模式下手动同步必须置灰（不能只靠前端 —— 接口也会拒，见 api_sync_run）
            assert pg.locator('#syrun').is_disabled(), '只读模式下「立即同步」应置灰'
            # 日志可点开
            n_log = pg.locator('a.sylog').count()
            if n_log:
                pg.locator('a.sylog').first.click()
                pg.wait_for_timeout(600)
                out = pg.locator('#syout').inner_text()
                assert '每日数据同步' in out, '日志内容没渲染出来: %s' % out[:120]
            br.close()
            assert not errs, '页面有运行时错误：%s' % errs[:3]
            return ('两条腿语义分离（A 报交易日落后 / B 报距今天数）；'
                    '日历来源可信；只读拦住手动同步；%d 份日志可点开' % n_log)
    finally:
        httpd.shutdown()
        sv.ALLOW_LIVE = old


@case('业绩预告 CSV：修读 / 内容校验 / 多解析器分歧', tag='fast')
def t_forcast_csv():
    """★ 这条用例存在的理由：三个解析器给三个答案，其中**两个不报错**。

    `stk_fin_forcast.csv` 的 content 是大段中文正文，含换行、含千分位逗号，
    且有 10 条记录引号未闭合（上游写坏的）：
        csv.reader   50,650 行（从未闭合处开始串行）
        pandas      124,094 行（不报错，含 11 行垃圾）
        DuckDB      直接报 state machine invalid
        修读        124,083 行（权威）

    那 11 行垃圾曾静默进 parquet 并存活一周 —— 因为当初只校验
    「落盘行数 == 读入行数」，**两边一样错，检查照样通过**。
    """
    import sys as _s
    dl = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), 'datalake')
    csvp = os.path.join(dl, 'raw', 'jq', '_ingest', 'downloads',
                        'stk_fin_forcast.csv')
    if not os.path.exists(csvp):
        return '跳过（没有 stk_fin_forcast.csv）'
    _s.path.insert(0, os.path.join(dl, 'build'))
    from csv_repair import read_forcast_df, read_repaired, split_records
    import duckdb

    # ---- 1) 修读：内容校验在函数内，读得出来就说明全过 ----
    df, st = read_forcast_df(csvp, verbose=False)
    n_fix = len(df)
    assert n_fix > 100000, '修读只得到 %d 行，太少' % n_fix
    assert df['id'].str.fullmatch(r'\d+').all(), 'id 有非数字'
    assert df['code'].str.fullmatch(r'\d{6}\.XSH[EG]').all(), 'code 有不合法'

    # ---- 2) 与裸 pandas 对照：必须【不一样】，且差值全是垃圾行 ----
    import pandas as pd
    naive = pd.read_csv(csvp, dtype=str, encoding='utf-8-sig')
    n_bad = int((~naive['id'].fillna('').str.fullmatch(r'\d+')).sum())
    assert n_bad > 0, \
        '裸 pandas 居然没产生垃圾行 —— 上游可能修好了 CSV，那这层修读可以简化'
    assert len(naive) - n_bad == n_fix, \
        ('裸读 %d 行 − 垃圾 %d 行 应等于修读 %d 行，实际不等 —— '
         '说明修读多吞或少吞了记录' % (len(naive), n_bad, n_fix))

    # ---- 3) DuckDB 必须仍然读不了（如果它能读了，这层修读就该退役）----
    try:
        duckdb.connect().execute(
            "SELECT count(*) FROM read_csv_auto('%s', all_varchar=true)" % csvp
        ).fetchone()
        duck_ok = True
    except Exception:                                       # noqa: BLE001
        duck_ok = False
    assert not duck_ok, \
        'DuckDB 现在能直接读这个 CSV 了 —— 上游修好了？那 csv_repair 可以简化'

    # ---- 4) 记录头判据不能退化成 ^\d+, ----
    #     content 里有千分位逗号（633,969.04元），松判据会把续行当新记录
    import re as _re
    _, recs_ok, _ = split_records(csvp)
    _, recs_loose, _ = split_records(csvp, start_re=_re.compile(r'^\d+,'))
    assert len(recs_loose) > len(recs_ok), \
        ('松判据 ^\\d+, 应该切出【更多】记录（把千分位续行误判成新记录），'
         '实得 %d vs 严判据 %d —— 如果一样，说明这个文件里已经没有'
         '千分位逗号续行，判据可以放松' % (len(recs_loose), len(recs_ok)))

    # ---- 5) 落盘的 parquet 必须没有垃圾行 ----
    pq = os.path.join(dl, 'raw', 'jq', 'stk_fin_forcast.parquet')
    if os.path.exists(pq):
        con = duckdb.connect()
        n_pq, n_junk = con.execute(
            "SELECT count(*), count(*) FILTER ("
            "  try_cast(id AS BIGINT) IS NULL"
            "  OR NOT regexp_matches(code, '^[0-9]{6}[.]XSH[EG]$'))"
            " FROM read_parquet('%s')" % pq).fetchone()
        assert n_junk == 0, 'parquet 里还有 %d 行垃圾' % n_junk
        assert n_pq == n_fix, 'parquet %d 行 != 修读 %d 行' % (n_pq, n_fix)

    return ('修读 %s 行（裸 pandas %s 行含 %d 行垃圾，DuckDB 仍报错，'
            '松判据多切 %d 条）；parquet 无垃圾行'
            % (format(n_fix, ','), format(len(naive), ','), n_bad,
               len(recs_loose) - len(recs_ok)))


def main():
    import time as _t
    ap = argparse.ArgumentParser(description='assay 自检')
    g = ap.add_mutually_exclusive_group()
    g.add_argument('--fast', action='store_true', help='只跑 fast 层（约 18s，无浏览器）')
    g.add_argument('--slow', action='store_true', help='只跑 slow 层（长区间回测）')
    g.add_argument('--web', action='store_true', help='只跑 web 层（需要浏览器）')
    g.add_argument('--all', action='store_true', help='全部（默认）')
    ap.add_argument('-k', default=None, help='按用例名关键字过滤（可与分层叠加）')
    ap.add_argument('--list', action='store_true', help='只列用例与分层，不执行')
    a = ap.parse_args()
    want = ({'fast'} if a.fast else {'slow'} if a.slow else
            {'web'} if a.web else {'fast', 'slow', 'web'})
    sel = [(n_, f_, t_) for n_, f_, t_ in CASES
           if t_ in want and (not a.k or a.k in n_)]
    if a.list:
        for t_ in ('fast', 'slow', 'web'):
            names = [n_ for n_, _, tt in CASES if tt == t_]
            print('%-5s %2d 个: %s' % (t_, len(names), '、'.join(names)))
        return
    if not sel:
        print('没有匹配的用例'); sys.exit(1)
    print('层 %s%s —— %d/%d 个用例\n'
          % ('+'.join(sorted(want)), (' 关键字 %r' % a.k) if a.k else '',
             len(sel), len(CASES)))
    ok = fail = 0
    times = []
    for name, fn, _tag in sel:
        t0 = _t.time()
        try:
            msg = fn()
            dt = _t.time() - t0
            times.append((dt, name))
            print('  ✓ %-28s %6.1fs  %s' % (name, dt, msg or ''))
            ok += 1
        except Exception as e:                              # noqa: BLE001
            dt = _t.time() - t0
            times.append((dt, name))
            print('  ✗ %-28s %6.1fs  %s: %s' % (name, dt, type(e).__name__, e))
            if os.environ.get('SELFTEST_TRACE'):
                traceback.print_exc()
            fail += 1
    print('\n%d 通过 / %d 失败   总耗时 %.1fs（共 %d 个用例，本次跑 %d 个）'
          % (ok, fail, sum(d for d, _ in times), len(CASES), len(sel)))
    if os.environ.get('SELFTEST_TIMING'):
        print('\n耗时排序:')
        for d, n in sorted(times, reverse=True):
            print('  %6.1fs  %s' % (d, n))
    sys.exit(1 if fail else 0)


if __name__ == '__main__':
    main()
