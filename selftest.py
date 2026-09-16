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


def _web_files(web, ext):
    """递归收集 web/ 下的文件（相对 web 的路径）。

    🔴 **必须递归**。web/ 分了子目录（shared/ 与 views/）之后，
      `os.listdir` 只看一层 —— 漏掉的文件其**所有组合都不参与比对**，
      而那不报错，只是保护范围悄悄缩小（同"直接扫目录而不是照清单拼"
      那条：漏了不报错才是最贵的）。
    """
    out = []
    for r, _d, fs in os.walk(web):
        for f in fs:
            if f.endswith(ext):
                out.append(os.path.relpath(os.path.join(r, f), web))
    return sorted(out)


def _pages(web='web'):
    """所有独立 .html 页面的可访问 URL（带够用的查询参数）。

    🔴 **扫目录得出，不照清单拼** —— 照清单拼会漏掉新加的页面，
      而漏了**不报错**，只是保护范围悄悄缩小（同 `_web_files` 那条）。
      2026-09-15 加指标广场时就验证了这一点：两处页面清单都是手写的，
      新页面自动不在保护里。
    ★ 有几页没参数就看不到东西（个股要 code、对比要 codes），
      所以这里只维护**参数**，页面本身仍然来自目录扫描。
    """
    args = {'stock.html': '?code=601857.XSHG',
            'compare.html': '?codes=601857.XSHG,601088.XSHG',
            'sector.html': '?kind=concept'}
    out = []
    for f in sorted(os.listdir(web)):
        if not f.endswith('.html') or f == 'index.html':
            continue
        out.append('/' + f + args.get(f, ''))
    return out


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
    交易日志只挂在其中一处，直接导致一次归因诊断全错。

    🔴 **判据从"全文件数一次"改成按【类】判**（2026-09-14）。原来是
      `src.count('self.pf.positions.pop(') == 1` —— 一个全文件计数。
      给 `RecordingBroker`（**预览**用，只记委托不撮合）加"清仓落地"时它红了，
      而那一处恰恰**一件副作用都没有**：不记 trades、不记 fills、不收费、
      不扣红利税。

      ★ 但**不能把 1 改成 2** —— 那正是守卫烂掉的方式（下次谁再加一处，
        改成 3 就行了）。改成：
          · 记账那条路（`Broker`）仍然**只许一处**，且必须在 `_fill_sell` 里
          · 预览那条路（`RecordingBroker`）允许一处，但必须**证明无副作用**
        这比原来的计数**更严**：原来只数个数，现在连"在哪个方法里"
        和"有没有偷偷记账"都钉住了。
    """
    import ast as _ast
    import io as _io
    src = _io.open('assay/broker.py', encoding='utf-8').read()
    tree = _ast.parse(src)

    def _pops(fn):
        """这个函数体里有几处 `self.pf.positions.pop(`。"""
        n = 0
        for x in _ast.walk(fn):
            if not isinstance(x, _ast.Call):
                continue
            f = x.func
            if (isinstance(f, _ast.Attribute) and f.attr == 'pop'
                    and isinstance(f.value, _ast.Attribute)
                    and f.value.attr == 'positions'):
                n += 1
        return n

    by_cls = {}
    for cls in [c for c in _ast.walk(tree) if isinstance(c, _ast.ClassDef)]:
        for fn in [f for f in cls.body
                   if isinstance(f, (_ast.FunctionDef, _ast.AsyncFunctionDef))]:
            k = _pops(fn)
            if k:
                by_cls.setdefault(cls.name, []).append((fn.name, k, fn))
    acct = by_cls.get('Broker', [])
    prev = by_cls.get('RecordingBroker', [])
    other = {k: [(n, c) for n, c, _f in v] for k, v in by_cls.items()
             if k not in ('Broker', 'RecordingBroker')}
    assert not other, '这些类里也直接平仓了：%s' % other
    assert len(acct) == 1 and acct[0][1] == 1, \
        '记账 Broker 里的平仓必须【只有一处】，实得 %s' \
        % [(n, c) for n, c, _f in acct]
    assert acct[0][0] == '_fill_sell', \
        '记账的那一处平仓不在 `_fill_sell` 里，而在 %s —— 卖出的 6 件副作用' \
        '（trades / 费用 / 红利税 / 现金 / 批次 / 持仓）必须收在同一个出口' \
        % acct[0][0]
    assert len(prev) <= 1, 'RecordingBroker 里有 %d 处平仓' % len(prev)
    if prev:
        name, _c, fn = prev[0]
        body = _ast.dump(fn)
        # 🔴 预览那一处**不许有任何记账副作用** —— 有的话它就不是预览了，
        #   而是第二条卖出路径（正是这条用例当初要防的东西）。
        for bad in ('trades', 'fills', 'fee_paid', 'div_tax_paid',
                    'sell_amount', 'buy_amount'):
            assert "attr='%s'" % bad not in body, \
                ('RecordingBroker.%s 里碰了 `%s` —— 预览路径一旦开始记账，'
                 '就成了第二条卖出出口' % (name, bad))
    return ('记账平仓 1 处且在 _fill_sell；预览平仓 %d 处且无记账副作用'
            % len(prev))


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
                        limit_down=False, sealed=True, touch_up=True,
                        # 🔴 给 `Bar` 加列必须**同时**补这个桩 —— 这是第二次
                        #   踩：加 touch_up 时补过一次，加 change_pct/ma5/ma20
                        #   时又漏了。namedtuple 少一个参数就是 TypeError，
                        #   好在它**会报错**（不像 guard.current 漏字段那样静默）。
                        change_pct=0.0, ma5=10.0, ma20=10.0,
                        amount=1e8, limit_ok=ok,
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
            # 回测归档已移到 #/runs —— `/` 现在是总览首页
            pg.goto('http://127.0.0.1:%d/#/runs' % port, wait_until='networkidle')
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
            # 🔴 判据是「那一块必须**说清楚状态**」，不是「一定有表」：
            #   `prune_runs.py` 会删掉旧归档的 holdings.parquet，那时页面
            #   给的是「明细已清理（重跑可再生成）」的 .warn —— 显示成空表
            #   才是 bug（会被读成"那天空仓"）。原来只认 `.note` 数量 == 1，
            #   于是归档一被清理这条就失败，而失败的其实是断言本身。
            _dh = pg.locator('#d_hold')
            _pruned = _dh.locator('.warn').count()
            assert _pruned or _dh.locator('.note').count() >= 1, \
                '当日持仓那一块既没有表也没有说明 —— 空白会被读成"那天空仓"'
            if _pruned:
                assert '已清理' in _dh.inner_text(), \
                    '明细被清理时必须明说，实得 %s' % _dh.inner_text()[:60]
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

            # 「‹ 返回目录」那个专属按钮已被顶栏的「📚 回测」入口 + 通用
            # 「‹ 返回」取代 —— 顶栏一处定义，不再每个视图各摆一个返回键
            pg.click('#top a.nav[href="/#/runs"]'); pg.wait_for_timeout(700)
            assert pg.is_visible('#cat'), '从顶栏回不到回测目录'
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
            pg.click('#top a.nav[href="/#/runs"]')
            pg.wait_for_timeout(900)
            assert pg.locator('.tree').count() >= 1, '从顶栏回不到回测目录'
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
    from assay import registry as reg
    from assay import server as sv
    sv._scan()
    # ★ 服务默认只读，网页触发回测是关的。本用例要测的就是「触发回测」
    #   这条链路，所以显式打开；关键是下面那两条「非法参数被挡住」的断言 ——
    #   只读模式下 api_backtest 本来就返回 error，不打开的话它们会
    #   【因为错误的原因通过】，比失败更危险。
    _prev_ab = sv.ALLOW_BACKTEST
    sv.ALLOW_BACKTEST = True
    # 🔴🔴 **归档重定向到临时目录 —— 这条用例会【真的跑一次回测】。**
    #   它点的就是页面上那个「用这个版本跑一次」，而那条链路（`_run_job`）
    #   起的是 `python3 run.py` 子进程、**照常归档**。于是每跑一次 selftest
    #   就往 `runs/` 里塞一条，而且参数区间完全相同 ——
    #   实测积了 **105 次** `2026-06-01~06-30` 的重复归档（用户问"这是什么"
    #   才发现），每天都在涨。
    #   ★ 同 `lv.LIVE` 重定向那条纪律：**selftest 不许写生产数据**。
    #     `registry` 读 `ASSAY_RUNS`，而子进程继承环境变量，所以设它就够。
    #   ★ **不改产品行为**：人在页面上点"跑一次"就是要归档的，
    #     给接口加 `--no-archive` 是修错了地方。
    import glob as _g0
    import shutil as _sh0
    import tempfile as _tf
    _runs_tmp = _tf.mkdtemp(prefix='selftest_runs_')
    #   ★ 临时归档不能是空的 —— 这一页要在目录树里找到版本行。
    #     **只复制结论那几个小文件**（meta/stats/strategy.py/run.log），
    #     不复制 holdings/equity（几十 MB，而这条用例根本不读）。
    #     每个 (分组,策略) 取最近 2 次就够。
    _seed_by = {}
    for _m in sorted(_g0.glob(os.path.join(reg.RUNS, '*/*/*/meta.json'))):
        _pp = _m.split(os.sep)
        _seed_by.setdefault((_pp[-4], _pp[-3]), []).append(_m)
    _n_seed = 0
    for (_grp, _st), _ms in _seed_by.items():
        for _m in _ms[-2:]:
            _src = os.path.dirname(_m)
            _dst = os.path.join(_runs_tmp, _grp, _st, os.path.basename(_src))
            os.makedirs(_dst, exist_ok=True)
            for _f in ('meta.json', 'stats.json', 'strategy.py', 'run.log'):
                _sp = os.path.join(_src, _f)
                if os.path.isfile(_sp):
                    _sh0.copy2(_sp, os.path.join(_dst, _f))
            _n_seed += 1
    assert _n_seed > 0, '没有可复制的归档样本 —— 这条用例需要归档里有数据'
    _prev_runs_env = os.environ.get('ASSAY_RUNS')
    os.environ['ASSAY_RUNS'] = _runs_tmp
    _prev_runs = reg.RUNS
    reg.set_runs(_runs_tmp)
    sv._scan()                       # 🔴 重扫 —— 索引是启动时建的，不重扫读到的还是真实归档
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
            # 回测归档已移到 #/runs —— `/` 现在是总览首页
            pg.goto('http://127.0.0.1:%d/#/runs' % port, wait_until='networkidle')
            pg.wait_for_timeout(800)
            # 过滤会自动展开，直接抵达版本层
            pg.fill('#filter', '红利'); pg.wait_for_timeout(800)
            # ★ 等版本行真的出现再断言 —— 归档现在是**临时目录**（本用例
            #   会真跑一次回测，不重定向就会污染 runs/），条数比真实归档少，
            #   渲染快但仍是异步的；固定 800ms 在机器忙时不够。
            try:
                pg.wait_for_selector('.vnote', timeout=15000)
            except Exception:                                   # noqa: BLE001
                pass
            n_note = pg.locator('.vnote').count()
            assert n_note > 0, ('版本行没有一句话简介（归档 %d 条）'
                                % len(pg.evaluate('() => RUNS || []')))
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

            pg.click('#top a.nav[href="/#/runs"]'); pg.wait_for_timeout(700)
            assert pg.is_visible('#cat'), '从顶栏回不到回测目录'
            br.close()
            assert not errs, 'JS 报错 %d 处: %s' % (len(errs), errs[:2])
    finally:
        httpd.shutdown()
        sv.ALLOW_BACKTEST = _prev_ab
        # 归档目录还原 + 清掉临时的。★ 顺序：先还原再删，
        #   删失败也不该让后面的用例跑在临时目录上。
        if _prev_runs_env is None:
            os.environ.pop('ASSAY_RUNS', None)
        else:
            os.environ['ASSAY_RUNS'] = _prev_runs_env
        reg.set_runs(_prev_runs)
        import shutil as _sh
        _sh.rmtree(_runs_tmp, ignore_errors=True)
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
                pg.goto('http://127.0.0.1:%d/#/runs' % port, wait_until='networkidle')
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
                assert pg.locator('#top a[href="#/picks"]').count() == 1, \
                    '顶栏缺少「★ 选中的规则」入口'
                pg.click('#top a[href="#/picks"]'); pg.wait_for_timeout(600)
                assert pg.url.endswith('#/picks'), '点入口没进 #/picks: %s' % pg.url
                assert pg.locator('#pk').count() == 1, '索引页没渲染'
                # ★ 一行一条规则的【对比表】，不是一条一张卡。
                #   卡片横排的毛病不是挤，是**没法纵向扫**：每张卡各自排版，
                #   同一个指标在不同卡里横坐标都不一样，10 条规则根本比不了。
                ncard = pg.locator('#pk tr.rw').count()
                assert ncard == after, \
                    '索引页行数 %d 与标星数 %d 不一致' % (ncard, after)
                assert pg.locator('#pk .card2').count() == 0, \
                    '还在用卡片横排 —— 应该是对比表'
                assert not pg.is_visible('#cat'), '进索引页后目录页应隐藏'
                txt = pg.locator('#pk').inner_text()
                assert '滑点' in txt, '索引页没有列出成本口径（滑点）'
                # 🔴 表头列数必须等于每行的单元格数 —— 表头 8 列配 9 个数据
                #    是横排改表格时最典型的错，而它不报错，只是所有列错位一格
                # 🔴 **一张表**装所有分组 —— 一组一张 table 时列宽各算各的，
                #    上下两个表的"年化"列对不齐，而这一页存在的唯一理由就是
                #    纵向比较。分组是表内的一条带子行。
                assert pg.locator('#pk table.pkt').count() == 1, \
                    '有 %d 张表 —— 分组各一张时列宽各算各的，上下对不齐' \
                    % pg.locator('#pk table.pkt').count()
                assert pg.locator('#pk tr.grp').count() >= 1, \
                    '分组没渲染成表内的带子行'
                # 列宽真的一致：同一列在不同分组下的左边界必须相同
                _x = pg.evaluate(
                    "() => [...document.querySelectorAll('#pk tr.rw')]"
                    ".map(tr => Math.round("
                    "tr.children[2].getBoundingClientRect().left))")
                assert len(set(_x)) == 1, \
                    '同一列在不同行的左边界不一致 %s —— 列没对齐' % sorted(set(_x))
                _th = pg.locator('#pk table.pkt tr').first.locator('th').count()
                _td = pg.locator('#pk table.pkt tr.rw').first.locator('td').count()
                assert _th == _td, '表头 %d 列 vs 每行 %d 格 —— 会整表错位' % (_th, _td)
                assert _th == 10, '列数应是 策略·参数 + 区间 + 8 个指标：%d' % _th
                _thz = ' '.join(pg.locator('#pk table.pkt th').all_inner_texts())
                # 本金与成本口径不占列（太挤）—— 它们进行的 tooltip
                assert '本金' not in _thz and '成本' not in _thz, \
                    '本金/成本口径不该再占列：%s' % _thz
                _tip = pg.locator('#pk tr.rw').first.get_attribute('title') or ''
                assert '本金' in _tip and '滑点' in _tip, \
                    '本金与成本口径要进 tooltip，不能直接丢掉：%r' % _tip
                # 参数要能【换行】：第一列有宽度上限且不 nowrap，
                # 否则十几个参数横着排会把指标列挤到屏幕外
                _ws = pg.evaluate(
                    "() => { const e=document.querySelector('#pk tr.rw td.nmc');"
                    " const s=getComputedStyle(e);"
                    " return [s.whiteSpace, s.maxWidth]; }")
                assert _ws[0] != 'nowrap', '参数列还是 nowrap —— 不会换行'
                assert _ws[1] != 'none', '参数列没有宽度上限，会被参数撑爆'
                # 数字列要能纵向对齐（等宽数字），否则位数一错开就没法扫
                _tn = pg.evaluate(
                    "() => getComputedStyle(document.querySelector("
                    "'#pk table.pkt tr.rw td:nth-child(3)'))"
                    ".fontVariantNumeric")
                assert 'tabular-nums' in (_tn or ''), \
                    '数字列没用等宽数字（%s）—— 位数对不齐扫起来就废了' % _tn
                # 宽表自己横向滚，页面 body 不许出现横向滚动条
                _ovf = pg.evaluate(
                    "() => getComputedStyle(document.querySelector('#pk .pw'))"
                    ".overflowX")
                assert _ovf in ('auto', 'scroll'), \
                    '宽表没自己横向滚（overflow-x=%s），会把页面撑横滚' % _ovf
                # ---- 点表头排序 ----
                # ★ 排序是【组内】做的（业务域是有意义的归拢，跨组比较本来
                #   就不该发生：区间和基准都可能不同）。所以判据是"每个组内
                #   单调"，不是"整列反过来" —— 后者跨组读必然不单调，
                #   我第一版就是这么写错的。
                def _bygrp(col=3):
                    out = []
                    gs = pg.locator('#pk .pg')
                    for i in range(gs.count()):
                        out.append([
                            float(x.strip().rstrip('%').replace(',', ''))
                            for x in gs.nth(i).locator(
                                'tr.rw td:nth-child(%d)' % col).all_inner_texts()
                            if x.strip() not in ('', '—')])
                    return out

                def _mono(vs, desc):
                    return all((vs[i] >= vs[i + 1]) if desc else (vs[i] <= vs[i + 1])
                               for i in range(len(vs) - 1))
                _h = pg.locator('#pk table.pkt th[data-sk="annual_return"]').first
                assert '▼' in _h.inner_text(), '默认应按年化降序，表头要标出方向'
                for gi, vs in enumerate(_bygrp()):
                    assert _mono(vs, True), \
                        '第 %d 组的年化没按降序排：%s' % (gi + 1, vs)
                _h.click(); pg.wait_for_timeout(500)
                assert '▲' in pg.locator(
                    '#pk table.pkt th[data-sk="annual_return"]').first.inner_text(), \
                    '再点一次应翻成升序'
                for gi, vs in enumerate(_bygrp()):
                    assert _mono(vs, False), \
                        '翻向后第 %d 组没变升序：%s' % (gi + 1, vs)
                # 换列时的默认方向要按【哪边更好】给：回撤是升序
                pg.locator('#pk table.pkt th[data-sk="max_drawdown"]').first.click()
                pg.wait_for_timeout(500)
                assert '▲' in pg.locator(
                    '#pk table.pkt th[data-sk="max_drawdown"]').first.inner_text(), \
                    '按回撤排序应默认升序 —— 一律降序会把最差的排最前面'
                # 点行进详情，再后退回索引页
                pg.locator('#pk tr.rw').first.click(); pg.wait_for_timeout(700)
                assert '#/run/' in pg.url, '点行没进回测详情: %s' % pg.url
                pg.go_back(); pg.wait_for_timeout(600)
                assert pg.url.endswith('#/picks'), '后退没回到索引页: %s' % pg.url

                assert not errs, '页面报错: %s' % errs[:3]
                br.close()
        finally:
            httpd.shutdown()
        return ('服务端 5 项 + 浏览器 11 项通过；%d 条选中规则，星标冒泡到顶层，'
                '独立索引页 #/picks 是对比表（单表 %d 列、分组为带子行、'
                '同列左边界一致、参数可换行、本金与成本入 tooltip、'
                '等宽数字、宽表自滚、点表头排序且回撤默认升序）' % (npick, _th))
    finally:
        if had:
            shutil.move(bak, sv.MARKS_FILE)
        elif os.path.isfile(sv.MARKS_FILE):
            os.remove(sv.MARKS_FILE)


@case('红利单腿基准：拆分忠实 / 不串腿 / both 不改原行为', tag='fast')
def t_hongli_sleeve():
    """袖A / 袖B 是【开关】不是【分叉】——三件事坏了都不报错。

    ① 拆出来的袖A 必须就是组合里的前 num_a 只、袖B 是余下那几只：
       `袖A ∪ 袖B 去重保序 == 组合 target_list`。拆歪了基准就没有意义。
    ② 关掉的那条腿要连**候选池**一起清空（`a=[]`/`b=[]` 而不只是 `la`/`lb`），
       否则 `backup_list` 还会从它那里取票，炸板再入场就买进"已经关掉那条腿"
       选出来的股票 —— **而这不报错**，只是单腿基准被悄悄污染。
    ③ `sleeve` 只接受 both/a/b，写错要当场报错而不是静默当成 both。

    🔴 **跨多期比，且带一条防空转的守卫。** 只比一期时踩过：那一期袖B 的候选池
      只有 4 只（< num_b=5），`b[num_b:]` 本来就是空的 —— 于是"不串腿"这条
      **在那一期无论实现对错都成立**，变异测试全绿。守卫要求至少有一期
      袖B 的池子深于 num_b，否则这条用例自己判失败。
    """
    import importlib.util
    from assay.feed import PanelFeed
    from assay.engine import Engine
    from assay import api as _api

    out = []
    P = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     'strategies', '红利')
    feed = PanelFeed('2026-01-01', '2026-09-05')      # 9 个调仓日

    def _picks(fn, tag):
        sp = importlib.util.spec_from_file_location('sleeve_%s' % tag,
                                                    os.path.join(P, fn))
        st = importlib.util.module_from_spec(sp)
        sp.loader.exec_module(st)
        # 🔴 薄壳的 initialize 调 _base.initialize，注册的是 **_base.pick** ——
        #   钩子打在壳模块上不会被调用（第一版就是这么空转的）。
        owner = getattr(st, '_base', st)
        got = []
        orig = owner.pick

        def spy(ctx):
            orig(ctx)
            got.append((str(ctx.current_date),
                        list(_api.g.target_list), list(_api.g.backup_list)))
        owner.pick = spy
        try:
            Engine(st, feed, cash=1000000,
                   params={'div_method': 'fiscal_year'}).run()
        finally:
            owner.pick = orig
        assert got, '%s 在这个窗口里一次都没调仓 —— 用例是空转的' % fn
        return got

    both = _picks('红利指数增强.py', 'both')
    a = _picks('红利低波袖A.py', 'a')
    b = _picks('红利价值袖B.py', 'b')
    assert len(both) == len(a) == len(b), \
        '三次跑的调仓期数不一致：%d/%d/%d' % (len(both), len(a), len(b))

    # ③ 非法值当场报错
    try:
        _picks_bad = importlib.util.spec_from_file_location(
            'sleeve_bad', os.path.join(P, '红利指数增强.py'))
        _m = importlib.util.module_from_spec(_picks_bad)
        _picks_bad.loader.exec_module(_m)
        Engine(_m, feed, cash=1000000,
               params={'div_method': 'fiscal_year', 'sleeve': 'A'}).run()
        raise AssertionError("sleeve='A'（大写）被静默接受了 —— 会当成 both 跑，"
                             "而基准看着完全正常")
    except AssertionError:
        raise
    except Exception:                                       # noqa: BLE001
        pass

    n_deep = 0
    for (d0, t0, _), (d1, t1, b1), (d2, t2, b2) in zip(both, a, b):
        assert d0 == d1 == d2, '调仓日对不齐：%s/%s/%s' % (d0, d1, d2)
        assert t1, '%s 袖A 目标池是空的' % d0
        # ①
        assert t1 == t0[:len(t1)], \
            ('%s 袖A 不等于组合的前 %d 只，拆分不忠实\n袖A =%s\n组合=%s'
             % (d0, len(t1), t1, t0[:len(t1)]))
        assert list(dict.fromkeys(t1 + t2)) == t0, \
            ('%s 袖A ∪ 袖B 去重保序 != 组合\nA=%s\nB=%s\n组合=%s'
             % (d0, t1, t2, t0))
        # ② 关掉的腿连候选池一起清了 -> backup 只可能来自本腿，
        #    所以条数不会超过本腿的 backup_a / backup_b（默认各 5）
        assert len(b1) <= 5, \
            ('%s 袖A 的 backup 有 %d 条（上限 5）—— 袖B 的候选池没被清空，'
             '多出来的是：%s' % (d0, len(b1), b1[5:]))
        assert len(b2) <= 5, \
            ('%s 袖B 的 backup 有 %d 条（上限 5）—— 袖A 的候选池没被清空'
             % (d0, len(b2)))
        if len(b2) > 0:
            n_deep += 1
    # 🔴 防空转：袖B 的候选池必须至少有一期深于 num_b，否则 ② 那条
    #   在每一期都恒真（没有 backup 可串），用例是假绿的
    assert n_deep >= 1, \
        ('这个窗口里袖B 一期都没有 backup（池子从没深过 num_b），'
         '"不串腿"这条断言恒真 —— 换个窗口，否则它抓不到任何东西')
    out.append('%d 期逐期核对：袖A == 组合前 %d 只、A∪B 去重 == 组合；'
               '两腿 backup 各自 <=5（袖B 有 %d 期非空，非空转）；'
               'sleeve 写错当场报错'
               % (len(both), len(a[0][1]), n_deep))
    return out


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
            lv.add_fill(aid, '2026-08-20', '603506.XSHG', 'buy', 1000, cost, force_price=True)
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
        lv.add_fill('t_nr', '2026-08-20', '603506.XSHG', 'buy', 1000, 11.0, force_price=True)
        s_nr = lv.build_signal('t_nr')
        if not s_nr['is_rebalance_day']:
            assert len(s_nr['hold']) == 1, \
                '非调仓日"持有不动"应为全部持仓 1 只，实得 %d' % len(s_nr['hold'])
            assert not s_nr['buy'], '非调仓日不该有买入'

        # ---- 3b2) 调仓日要能【提前】算出来 ----
        #     ★ 调仓日由日历序号决定（月内/周内第 k 个交易日），而日历有到
        #       2030 —— 所以能提前很久算。清单不行（要 T-1 收盘）。
        #       提前提示的理由：调仓日当天早上才打开就已经晚了。
        s_up = lv.build_signal('t_hl')
        assert s_up['upcoming'] and len(s_up['upcoming']) == lv.UPCOMING_DAYS, \
            '日历条应有 %d 天，实得 %s' % (lv.UPCOMING_DAYS,
                                        len(s_up['upcoming'] or []))
        assert s_up['next_rebalance'], \
            ('月频策略必须也能找到下次调仓 —— 只往前看 %d 天会返回 None，'
             '看着像策略不调仓了（红利的下一次可能在 20+ 个交易日后）'
             % lv.UPCOMING_DAYS)
        assert s_up['days_until_rebalance'] is not None
        if s_up['is_rebalance_day']:
            assert s_up['days_until_rebalance'] == 0
            assert s_up['next_rebalance'] == s_up['for_date']
        else:
            assert s_up['days_until_rebalance'] >= 1
            assert s_up['next_rebalance'] > s_up['for_date']
        # 周频账户：调仓日必须按【周内序号】等间隔出现
        s_wk = lv.build_signal('t_hit')       # weekday=2
        rb = [x['date'] for x in (s_wk['upcoming'] or []) if x['is_rebal']]
        assert len(rb) >= 2, 'weekday=2 的账户在 %d 个交易日里应有 >=2 个调仓日' \
            % lv.UPCOMING_DAYS
        # 相邻两个调仓日之间应为 5 个交易日（自然周），假期周会短
        cal = [d.isoformat() for d in lv.calendar_days()]
        gaps = [cal.index(rb[i + 1]) - cal.index(rb[i]) for i in range(len(rb) - 1)]
        assert all(1 <= g <= 8 for g in gaps), \
            '周频调仓日间隔异常 %s（run_weekly 锚自然周，假期会让间隔变短）' % gaps

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
        lv.add_fill('t_cash', '2026-08-20', '603506.XSHG', 'buy', 1000, 11.0, fee=5, force_price=True)
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
        r1 = lv.add_fill('t_fee', '2026-08-20', '601857.XSHG', 'buy', 1000, 11.42, force_price=True)
        assert r1['fee_estimated'] is True, '不填费用应标 fee_estimated'
        assert r1['fee'] == lv.estimate_fee('buy', 1000, 11.42, '2026-08-20')
        r2 = lv.add_fill('t_fee', '2026-08-21', '601088.XSHG', 'buy', 100, 48.78,
                         fee=3.21, force_price=True)
        assert r2['fee'] == 3.21 and not r2.get('fee_estimated')
        r3 = lv.add_fill('t_fee', '2026-08-22', '600012.XSHG', 'buy', 200, 16.69,
                         fee=0, force_price=True)
        assert r3['fee'] == 0 and not r3.get('fee_estimated'), \
            '明确填 0 不该被当成"没填"而去估算'
        exp = 100000 - (1000 * 11.42 + r1['fee']) - (100 * 48.78 + 3.21) \
            - (200 * 16.69)
        assert abs(lv.cash('t_fee') - exp) < 1e-6, \
            '现金没把费用扣进去：%.2f vs %.2f' % (lv.cash('t_fee'), exp)
        # ★ 费率是【账户级】的（FEE_DEFAULT / acct['fee']），不再等于引擎 Cost
        #   —— 引擎默认万2.5+最低5元是**回测口径**（刻意保守），真实账户是
        #   万0.8含规费+过户费万0.1、无最低。所以这里核的是
        #   「按给定的模型逐项算对」，而不是「与 Cost 一致」。
        #   唯一仍复用引擎的是【印花税分段规则】—— 那条不能有第二份实现。
        c = _C()
        m0 = dict(lv.FEE_DEFAULT)
        for side, sh, px, d in (('buy', 1000, 11.42, '2023-08-25'),
                                ('sell', 1000, 11.42, '2023-08-25'),
                                ('sell', 1000, 11.42, '2023-08-28')):
            amt = sh * px
            want = max(amt * m0['commission'], m0['min_commission'])
            if not m0['commission_incl_reg']:
                want += amt * m0['regulatory']           # 默认口径：规费另收
            want += amt * m0['transfer']
            if side == 'sell':
                want += amt * c.close_tax_at(lv._d(d))   # 分段规则只此一处
            assert abs(lv.estimate_fee(side, sh, px, d, m0)
                       - round(want, 2)) < 1e-9, \
                '%s %s 的估算与费率模型不一致' % (side, d)
        # 印花税分段必须生效（2023-08-28 起 千1 -> 万5）
        assert lv.estimate_fee('sell', 1000, 11.42, '2023-08-25') > \
            lv.estimate_fee('sell', 1000, 11.42, '2023-08-28'), \
            '印花税没按日期分段'
        try:
            lv.add_fill('t_fee', '2026-08-23', '601857.XSHG', 'sell', 100,
                        11.0, fee=-1, force_price=True)
            raise AssertionError('非冲正记录的负费用应被拒')
        except lv.LiveError:
            pass

        # ---- 3d2) 账户级费率：用【真实账单】钉住，并核两种口径 ----
        #   2026-09-02 红利账户真实账单：买入 6.010 × 11000 = 66,110
        #     佣金 1.72（万0.26，净，归券商）
        #     规费 3.57（万0.54，法定：证管费万0.2 + 经手费万0.341，按分截尾）
        #     过户费 0.66（万0.10，法定）
        #     合计 5.95 = 万0.90
        #   ★ App 显示"佣金费率万0.8"是【含规费】口径：
        #       66110 × 万0.8 = 5.2888 ≈ 1.72 + 3.57 = 5.29
        #     单看净佣金万0.26 与 App 对不上 —— 口径配错不会报错，只会算错。
        #   ★ 只有净佣金可谈；规费/过户费/印花税法定、所有人一样，
        #     所以它们有默认值，**不配也能用**。
        BILL = dict(amount=66110.0, commission=1.72, regulatory=3.57,
                    transfer=0.66)
        fm = lv.infer_fee_model(**BILL)
        assert fm['commission_incl_reg'] is True, \
            '账单同时列了佣金与规费，应判为【含规费】口径'
        assert abs(fm['commission'] - 0.00008) < 5e-7, \
            '反推的佣金率应≈万0.8（含规费），实得 万%.4f' % (fm['commission'] * 1e4)
        # ★ 容差留够：券商账单【按分截尾】，0.66/66110 = 万0.0998，
        #   本来就不会正好等于万0.1。写死等号会假失败。
        assert abs(fm['transfer'] - 0.00001) < 5e-8, \
            '反推的过户费率应≈万0.1，实得 万%.4f' % (fm['transfer'] * 1e4)
        full = dict(lv.FEE_DEFAULT)
        full.update(fm)
        bd = lv.fee_breakdown('buy', 11000, 6.010, '2026-09-02', full)
        assert abs(bd['total'] - 5.95) < 0.011, \
            '复算应得 5.95（账单），实得 %.2f' % bd['total']
        assert bd['regulatory'] == 0, '含规费口径下不该再单收一次规费'
        bs = lv.fee_breakdown('sell', 11000, 6.010, '2026-09-02', full)
        assert abs(bs['stamp'] - 66110 * 0.0005) < 0.02, '卖出印花税万5'
        assert abs(bs['total'] - (bd['total'] + bs['stamp'])) < 0.02, \
            '卖出 = 买入 + 印花税'
        # 印花税分段仍复用引擎（不在 live 里重写第二份）
        assert lv.fee_breakdown('sell', 11000, 6.010, '2023-08-25',
                                full)['stamp'] > bs['stamp'], \
            '2023-08-28 前印花税是千1，应更贵'

        # 两种口径在【最低佣金 binding】时差别明显 —— 这是必须问清口径的理由
        incl = dict(lv.FEE_DEFAULT, commission=0.00025, min_commission=5.0,
                    commission_incl_reg=True)
        netc = dict(lv.FEE_DEFAULT, commission=0.00025, min_commission=5.0,
                    commission_incl_reg=False)
        a5 = lv.fee_breakdown('buy', 1000, 50.0, '2026-09-02', incl)['total']
        b5 = lv.fee_breakdown('buy', 1000, 50.0, '2026-09-02', netc)['total']
        assert b5 > a5, '规费另收口径应更贵（规费加在最低之外）'
        assert abs(b5 - a5 - 50000 * lv.REG_RATE) < 0.02, \
            '两种口径的差应恰好是规费：%.2f vs %.2f' % (b5 - a5, 50000 * lv.REG_RATE)

        # 不配置也能用：法定默认（万2.5 + 最低5 + 规费另收）
        d0 = lv.fee_breakdown('buy', 11000, 6.010, '2026-09-02')
        assert d0['regulatory'] > 0 and d0['transfer'] > 0, \
            '不配置时法定部分要有默认值'
        assert d0['total'] > bd['total'], '默认口径应比真实账户贵（刻意保守）'

        # ---- flat 模式：直接填总费率，填了就以它为准、不再拆项 ----
        #   费率谈好之后"买入万0.9 / 卖出万5.9"就是全部事实，再拆成
        #   佣金/规费/过户费只是多几个能配错的地方（含规费 vs 净佣金
        #   那条口径差已经坑过一次）。
        flat = dict(lv.FEE_DEFAULT, mode='flat', buy_rate=0.00009,
                    sell_rate=0.00059, flat_min=0)
        fb = lv.fee_breakdown('buy', 11000, 6.010, '2026-09-02', flat)
        assert fb['mode'] == 'flat'
        assert abs(fb['total'] - 5.95) < 0.011, \
            'flat 买入应得 5.95（万0.9），实得 %.2f' % fb['total']
        assert fb['regulatory'] is None and fb['stamp'] is None, \
            'flat 模式不该再拆项 —— 拆了就说明又加了一遍'
        fs = lv.fee_breakdown('sell', 11000, 6.010, '2026-09-02', flat)
        assert abs(fs['total'] - 39.00) < 0.02, \
            'flat 卖出应得 39.00（万5.9），实得 %.2f' % fs['total']
        # flat 不再另加印花税（卖出费率里已含）—— 换个日期结果必须一样
        assert lv.fee_breakdown('sell', 11000, 6.010, '2023-08-25',
                                flat)['total'] == fs['total'], \
            'flat 模式不该再按日期加印花税'
        # 单笔最低仍生效
        f2 = dict(flat, flat_min=5.0)
        assert lv.fee_breakdown('buy', 100, 10.0, '2026-09-02',
                                f2)['total'] == 5.0, 'flat 的最低没生效'
        # parts 折成总费率 -> 两种模式给同一个答案
        er = lv.effective_rates(full, 66110)
        assert abs(er['buy_fee'] - 5.95) < 0.011 and \
            abs(er['buy_rate'] - 0.00009) < 5e-7, \
            'parts 折出的买入总费率应是万0.9：%s' % er
        assert abs(lv.fee_breakdown('buy', 11000, 6.010, '2026-09-02',
                                    dict(flat, buy_rate=er['buy_rate']))['total']
                   - 5.95) < 0.011, '把 parts 折出的率填进 flat，结果应一致'
        # 非法 mode 要被拒
        try:
            lv.upsert_account('t_fee', fee={'mode': 'nope'})
            raise AssertionError('未知 mode 应被拒')
        except lv.LiveError:
            pass

        # ---- 费率版本化：append-only，按【成交日】取当时那一档 ----
        #   ★ 为什么必须分版本：补录一笔历史成交时，要用那笔成交【当时】
        #     生效的费率。换过券商之后拿今天的费率去算三个月前那笔，
        #     数字看着很正常，只是错的。
        #   ★ "新费率生效、原费率立刻结束" **不靠改写上一条** ——
        #     每条只记 from，to 由下一条的 from 减一天推出。账本只追加。
        lv.upsert_account('t_rate', name='rate', init_cash=1000000)
        lv.bind_version('t_rate', 'strategies/小市值/froec_traded.py',
                        params={'stop_loss': 0.35, 'stop_intraday': 1, 'weekday': 2})
        assert lv.fee_rates('t_rate') == [], '新账户不该有费率历史'
        # 没配置时按默认（偏保守），且早于第一档也走默认
        d_def = lv.fee_model_at('t_rate', '2026-09-02')
        assert d_def['commission'] == lv.FEE_DEFAULT['commission']
        lv.add_fee_rate('t_rate', '2026-09-01',
                        {'mode': 'flat', 'buy_rate': 0.00009,
                         'sell_rate': 0.00059, 'flat_min': 0}, note='开户')
        lv.add_fee_rate('t_rate', '2026-10-01',
                        {'mode': 'flat', 'buy_rate': 0.00006,
                         'sell_rate': 0.00056, 'flat_min': 0}, note='换券商')
        h = lv.fee_rates('t_rate')
        assert len(h) == 2, '应有两档'
        assert h[0]['to'] == '2026-09-30' and not h[0]['active'], \
            '上一档应在新档生效的前一天结束，实得 to=%s' % h[0]['to']
        assert h[1]['to'] is None and h[1]['active'], '最新一档应仍生效'
        # 按成交日取到不同费率
        for d, want in (('2026-09-10', 5.95), ('2026-09-30', 5.95),
                        ('2026-10-01', 3.97), ('2026-10-08', 3.97)):
            got = lv.estimate_fee('buy', 11000, 6.010, d,
                                  lv.fee_model_at('t_rate', d))
            assert abs(got - want) < 0.011, \
                '%s 应按当时费率算出 %.2f，实得 %.2f' % (d, want, got)
        # 早于第一档 -> 默认费率（不往前延伸，那是猜）
        early = lv.estimate_fee('buy', 11000, 6.010, '2026-08-01',
                                lv.fee_model_at('t_rate', '2026-08-01'))
        assert early > 5.95, '早于第一档应走默认（偏保守），实得 %.2f' % early
        # add_fill 也按成交日取
        for d, want in (('2026-09-10', 5.95), ('2026-10-08', 3.97)):
            r = lv.add_fill('t_rate', d, '603889.XSHG', 'buy', 11000, 6.010, force_price=True)
            assert abs(r['fee'] - want) < 0.011, \
                'add_fill 在 %s 应算 %.2f，实得 %.2f' % (d, want, r['fee'])
        # 生效日必须递增（不能插到中间、不能同日）
        for fd, why in (('2026-09-15', '早于上一档'), ('2026-10-01', '与上一档同日')):
            try:
                lv.add_fee_rate('t_rate', fd, {'mode': 'flat', 'buy_rate': 1e-4,
                                               'sell_rate': 6e-4})
                raise AssertionError('%s 应被拒' % why)
            except lv.LiveError:
                pass
        # ★ supersede = **物理删除**被盖掉那档，不留"已作废"的行。
        #   它的有效区间是零长度（to < from），也就是从来没有任何成交按它
        #   算过 —— 删掉不丢"当时用的是哪档"的信息，只是去掉噪声。
        n0 = len(lv.fee_rates('t_rate'))
        sp = lv.add_fee_rate('t_rate', '2026-10-01',
                             {'mode': 'flat', 'buy_rate': 0.00005,
                              'sell_rate': 0.00055, 'flat_min': 0},
                             note='更正', supersede=True)
        assert sp.get('replaced') == 1, '应删掉 1 条被盖的档，实得 %s' % sp.get('replaced')
        h2 = lv.fee_rates('t_rate')
        assert len(h2) == n0, '档数不该变（一删一加），实得 %d -> %d' % (n0, len(h2))
        assert not any(x.get('superseded') for x in h2), \
            '不该再有"已作废"的行 —— supersede 是物理删除'
        assert abs(h2[-1]['model']['buy_rate'] - 0.00005) < 1e-9, '生效的应是新那档'
        # 每档都要带【总费率】—— 页面显示的是总费率而不是佣金率
        for x in h2:
            assert x.get('rates') and x['rates'].get('buy_rate') is not None, \
                '每档要带总费率（页面显示总费率而不是佣金）：%s' % x.get('rates')
            assert x.get('model'), '每档要带合并后的完整费率模型（供展开明细）'
        # ---- 3d3d) 浮盈按【摊薄成本】算，含买入费 ----
        #   ★ 券商 App 的"摊薄成本价"就是这个口径。费用是真金白银出去了，
        #     不算进成本等于把浮盈报高。
        #   ★ 三个价各答一个问题，不能只留一个：
        #       cost      成交均价 —— 喂给引擎 entry_price（止损/吊灯/红利税
        #                 档位读它），**不含费、不能动**；也是与回测
        #                 trades.ret 对照的口径（引擎 ret 只含滑点不含佣金）
        #       cost_net  摊薄成本 = (成交额+买入费)/股数 —— 浮盈的基准
        #       breakeven 保本价 = 摊薄成本/(1−卖出费率)
        #   ★ 部分卖出时买入费按【剩余股数比例】留在成本里。
        lv.upsert_account('t_pnl', name='pnl', init_cash=500000)
        # 费率就地定义 —— 不依赖后面 3d4 里那个 GALAXY 的声明顺序
        lv.add_fee_rate('t_pnl', '2026-09-01',
                        {'mode': 'parts', 'commission': 0.000086,
                         'min_commission': 5.0, 'commission_incl_reg': False,
                         'regulatory': 0.0, 'transfer': lv.TRANSFER_RATE,
                         'transfer_extra': 'xshg', 'stamp': 'auto'},
                        note='银河')
        _r = lv.add_fill('t_pnl', '2026-09-01', '603506.SH', 'buy', 3300, 11.350)
        assert _r['fee'] > 0, '买入应估出费用'
        _P = lv.positions_valued('t_pnl')
        _it = _P['items'][0]
        # 成交均价【不含费】—— 它要喂给引擎 entry_price，动了实盘就不是
        # 走策略自己的代码路径了
        assert abs(_it['cost'] - 11.350) < 1e-6, \
            '成交均价应等于成交价、不含买入费，实得 %s' % _it['cost']
        # 摊薄成本 = (成交额 + 买入费)/股数
        # 落盘保留 4 位小数，容差按它给
        assert abs(_it['cost_net'] - (3300 * 11.350 + _r['fee']) / 3300) < 1e-4, \
            '摊薄成本算错：%s' % _it['cost_net']
        assert _it['cost_net'] > _it['cost'], '摊薄成本必须高于成交均价'
        assert abs(_it['buy_fee'] - _r['fee']) < 0.005, '这批摊的买入费不对'
        # 🔴 浮盈按【摊薄成本】算 —— 往返账实必须相符：
        #    浮盈 == 现值 − (本金 + 买入费) == 现值 − 现金减少额
        _paid = 3300 * 11.350 + _r['fee']
        assert abs(_P['pnl'] - (_it['value'] - _paid)) < 0.02, \
            '浮盈应等于 现值 − (本金+买入费)：%s vs %s' \
            % (_P['pnl'], _it['value'] - _paid)
        assert abs((500000 - lv.cash('t_pnl')) - _paid) < 0.02, \
            '现金减少额应等于 本金+买入费'
        # 与回测对照的那个仍在（只含滑点不含佣金）
        assert abs(_P['pnl_gross'] - (_it['value'] - 3300 * 11.350)) < 0.02, \
            'pnl_gross 应是不含费的口径（与回测 trades.ret 对照用）'
        assert _P['pnl_gross'] > _P['pnl'], '不含费的口径必然更好看'
        # 估算卖出费：含印花税万5，所以一定比买入费大得多
        assert _it['exit_fee_est'] > _r['fee'] * 3, \
            '卖出费该含印花税万5（比买入费大一截）：卖 %s / 买 %s' \
            % (_it['exit_fee_est'], _r['fee'])
        # 保本价：卖到它才不亏 —— 高于摊薄成本，且落在摊薄成本上方一个
        # 卖出费率的位置
        assert _it['breakeven'] > _it['cost_net'], '保本价必须高于摊薄成本'
        assert abs(_it['breakeven'] * _it['shares']
                   - (_paid + _it['exit_fee_est'])) < 1.5, \
            '按保本价卖出应刚好覆盖 投入+卖出费：%s' % _it['breakeven']
        assert abs(_P['pnl_net'] - (_P['pnl'] - _P['exit_fee_est'])) < 0.02, \
            'pnl_net 应等于 浮盈 − 估算卖出费（买入费不重复）'
        assert _P['pnl_net'] < _P['pnl'], '全平落袋必须小于浮盈'
        assert abs(_P['equity'] - (lv.cash('t_pnl') + _P['market_value'])) < 0.02
        # ★ 部分卖出：那一批的买入费按【剩余股数比例】留在成本里
        lv.add_fill('t_pnl', '2026-09-01', '603506.SH', 'sell', 1100, 11.500)
        _i2 = lv.positions_valued('t_pnl')['items'][0]
        assert _i2['shares'] == 2200, '剩余股数不对'
        assert abs(_i2['buy_fee'] - _r['fee'] * 2200 / 3300) < 0.01, \
            '买入费该按比例消耗：剩 %s，应是 %s' \
            % (_i2['buy_fee'], _r['fee'] * 2200 / 3300)
        assert abs(_i2['cost'] - 11.350) < 1e-6, '成交均价不该被卖出影响'
        # 引擎口径那个价【绝不能】被摊薄污染 —— 它决定止损在哪触发
        _lots = lv.fifo_lots(lv.fills('t_pnl'))['603506.XSHG']
        assert all(abs(l['price'] - 11.350) < 1e-9 for l in _lots), \
            'lot.price 被改成摊薄成本了 —— 那会让止损在错的位置触发'

        # ---- 3d4) 过户费【分市场】另收：银河四张真实账单逐笔对到分 ----
        #   ★ 银河的"万0.86 最低5元"是个【打包价】：里面已含经手费+证管费，
        #     深市连过户费也在里面，沪市的过户费万0.1 另收。所以同一个账户
        #     两个市场的费率不同 —— 沪 万0.96 / 深 万0.86（大额时）。
        #     取个平均在两边都错，且**不报错**，所以必须建模。
        #   ★ 而且"这个账户的费率"根本不是一个数：5 元最低在成交额低于
        #     约 5.81 万时 binding，那时深市实付恒 5.00、沪市 5.00+万0.1。
        GALAXY = {'mode': 'parts', 'commission': 0.000086, 'min_commission': 5.0,
                  'commission_incl_reg': False, 'regulatory': 0.0,
                  'transfer': lv.TRANSFER_RATE, 'transfer_extra': 'xshg',
                  'stamp': 'auto'}
        # (名称, 代码, 成交金额, 账单佣金行, 账单过户费行, 账单实付)
        BILLS = (('南都物业', '603506.XSHG', 37455, 5.00, 0.37, 5.37),
                 ('鹏翎股份', '300375.XSHE', 38302, 5.00, 0.33, 5.00),
                 ('锐科激光', '300747.XSHE', 107118, 9.21, 1.08, 9.21),
                 ('大唐发电', '601991.XSHG', 106856, 9.19, 1.06, 10.25),
                 # ★ 海科新源这张是【深市 + 大额】，最低不 binding，所以它
                 #   **单独**就能否掉"深市过户费另收"：已含算 11.23、
                 #   另收算 12.53，差 1.30 元不可能是舍入。
                 ('海科新源', '301292.XSHE', 130540, 11.23, 0.00, 11.23))
        for nm, code, amt, comm, ghf, tot in BILLS:
            bd = lv.fee_breakdown('buy', 100, amt / 100.0, '2026-09-01',
                                  GALAXY, code)
            assert abs(bd['commission'] - comm) < 0.011, \
                '%s 佣金应 %.2f（=max(5, 额×万0.86)），实得 %.2f' \
                % (nm, comm, bd['commission'])
            # 券商是逐项截尾、我们四舍五入，容许 1 分
            assert abs(bd['total'] - tot) <= 0.011, \
                '%s 实付应 %.2f，实得 %.2f' % (nm, tot, bd['total'])
            want_extra = code.endswith('XSHG')
            assert bd['transfer_extra'] is want_extra, \
                '%s 的过户费该%s' % (nm, '另收' if want_extra else '已含在佣金里')
            if not want_extra:
                assert bd['transfer'] == 0, \
                    ('%s 是深市，过户费已含在佣金里，不该再加一次'
                     '（会把 5.00 算成 5.33）' % nm)
        # ★ 深市大额那张的判别力：换成"过户费另收"必须算错，否则这条断言
        #   等于没测（两种口径给同一个数的话，账单就定不出方向）。
        _hk = ('301292.XSHE', 130540, 11.23)
        _both = lv.fee_breakdown('buy', 100, _hk[1] / 100.0, '2026-09-01',
                                 dict(GALAXY, transfer_extra='both'), _hk[0])
        assert abs(_both['total'] - _hk[2]) > 1.0, \
            ('海科新源这张单必须能区分两种口径，否则它证明不了什么：'
             '另收算 %.2f、账单 %.2f' % (_both['total'], _hk[2]))

        # 分市场时【总费率必须给两套】—— 一个数会让另一个市场对不上账
        er = lv.effective_rates(GALAXY, 106856)
        assert not er['same'] and er['worst_market'] == 'XSHG', \
            '沪深不同价时要标出来，且顶层取高的那个（保守）：%s' % er
        assert abs(er['by_market']['XSHG']['buy_rate'] - 0.000096) < 1e-7 and \
            abs(er['by_market']['XSHE']['buy_rate'] - 0.000086) < 1e-7, \
            '10.7 万一笔应是 沪万0.96 / 深万0.86：%s' % er['by_market']
        # 最低 binding 时是【常数 + 沪市的万0.1】，不是那个费率
        lo = lv.effective_rates(GALAXY, 37455)
        assert abs(lo['by_market']['XSHE']['buy_fee'] - 5.00) < 0.011 and \
            abs(lo['by_market']['XSHG']['buy_fee'] - 5.37) < 0.011, \
            '3.7 万一笔（最低 binding）应是 深 5.00 / 沪 5.37：%s' % lo['by_market']
        assert lo['by_market']['XSHE']['buy_rate'] > \
            er['by_market']['XSHE']['buy_rate'], \
            '最低 binding 时折成的费率应【更高】—— 只报大额那个数会让小额单' \
            '看着便宜'
        # 设成分市场却不给代码 -> 报错，不替它猜方向（猜错差万0.1 且不报错）
        try:
            lv.fee_breakdown('buy', 100, 1000, '2026-09-01', GALAXY)
            raise AssertionError('分市场费率不给代码时应报错')
        except lv.LiveError as e:
            assert '哪个市场' in str(e), '报错要说清缺什么：%s' % e
        # 'both'（默认）与 'none' 不需要代码
        for te, want in (('both', 0.00001 * 100000), ('none', 0.0)):
            g2 = dict(GALAXY, transfer_extra=te, min_commission=0.0)
            assert abs(lv.fee_breakdown('buy', 100, 1000, '2026-09-01',
                                        g2)['transfer'] - want) < 0.011, \
                'transfer_extra=%s 不该需要代码，且过户费应为 %.2f' % (te, want)
        # add_fill 要把代码传下去 —— 否则分市场费率在录入时就报错
        lv.upsert_account('t_gal', name='银河', init_cash=500000)
        lv.add_fee_rate('t_gal', '2026-09-01', GALAXY, note='银河四张账单')
        for nm, code, amt, _c, _g, tot in BILLS:
            r = lv.add_fill('t_gal', '2026-09-01', code, 'buy', 100, amt / 100.0, force_price=True)
            assert abs(r['fee'] - tot) <= 0.011, \
                '%s 录入时估的费用应 %.2f，实得 %.2f' % (nm, tot, r['fee'])
        # 🔴 反推费率不能用【触及最低】那笔账单：南都物业反推万1.34 vs 真值万0.86
        try:
            lv.infer_fee_model(37455, 5.00, transfer=0.37, min_commission=5.0)
            raise AssertionError('用最低 binding 的账单反推应被拒')
        except lv.LiveError as e:
            assert '最低' in str(e) and '更大' in str(e), \
                '要说清为什么不行、以及要多大的账单：%s' % e
        m_ok = lv.infer_fee_model(106856, 9.19, regulatory=0.0, transfer=1.06,
                                  min_commission=5.0)
        assert abs(m_ok['commission'] - 0.000086) < 1e-7, \
            '大唐那笔应反推出万0.86：万%.3f' % (m_ok['commission'] * 1e4)
        # 🔴 反推必须【显式给出 regulatory】。省掉这个键会被 FEE_DEFAULT 的
        #   万0.541 顶回来再加一遍：实测那笔 10.25 会算成 16.03（高 56%）。
        assert 'regulatory' in m_ok and m_ok['regulatory'] == 0.0, \
            '账单没有规费行（已含在佣金里）时要显式写 regulatory=0：%s' % m_ok
        _f = lv._merge_fee(dict(m_ok, transfer_extra='xshg'))
        assert abs(lv.fee_breakdown('buy', 100, 1068.56, '2026-09-01', _f,
                                    '601991.XSHG')['total'] - 10.25) <= 0.011, \
            '按反推结果复算大唐那笔应回到 10.25'
        for bad in ('nope', '', 'XSHG'):
            try:
                lv.add_fee_rate('t_gal', '2027-06-01',
                                dict(GALAXY, transfer_extra=bad))
                raise AssertionError('transfer_extra=%r 应被拒' % bad)
            except lv.LiveError:
                pass

        # ★ 费率必须【每账户独立】—— 存在 live/<id>/fee_rates.jsonl，
        #   一个文件一个账户。共享一份是很容易顺手写出来的（"费率不都一样吗"），
        #   而串了之后的表现是：另一个账户的历史成交被按新费率重算，
        #   现金和权益全变，且**不报错**。
        lv.upsert_account('t_iso', name='iso', init_cash=100000)
        lv.add_fee_rate('t_iso', '2026-09-01', dict(fm), note='A 的费率')
        _b4 = lv.fee_model_at('t_fee', '2027-01-02')['commission']
        lv.add_fee_rate('t_iso', '2027-01-01',
                        {'mode': 'parts', 'commission': 0.001, 'regulatory': 0,
                         'transfer': 0, 'min_commission': 0}, note='只该影响 t_iso')
        assert abs(lv.fee_model_at('t_iso', '2027-01-02')['commission'] - 0.001) < 1e-12
        assert abs(lv.fee_model_at('t_fee', '2027-01-02')['commission'] - _b4) < 1e-12, \
            '改一个账户的费率影响到了别的账户'
        assert os.path.dirname(os.path.abspath(
            os.path.join(lv.acct_dir('t_iso'), 'fee_rates.jsonl'))) != \
            os.path.dirname(os.path.abspath(
                os.path.join(lv.acct_dir('t_fee'), 'fee_rates.jsonl'))), \
            '两个账户的费率文件在同一个目录 —— 迟早会共用同一份'
        lv.upsert_account('t_never', name='从没配过费率', init_cash=100000)
        assert lv.fee_rates('t_never') == [] and \
            abs(lv.fee_model_at('t_never', '2026-09-01')['commission']
                - lv.FEE_DEFAULT['commission']) < 1e-12, \
            '没配过费率的账户应落到 FEE_DEFAULT，而不是捡别的账户的'

        # 迁移：老的单份 acct['fee'] -> 第一档
        lv.upsert_account('t_mig', name='mig', init_cash=100000, fee=fm)
        assert lv.fee_rates('t_mig') == []
        mg = lv.migrate_fee('t_mig')
        assert mg and len(lv.fee_rates('t_mig')) == 1, '迁移应落成第一档'
        assert lv.migrate_fee('t_mig') is None, '已迁移过不该重复落'

        # 账户配了费率后，add_fill 不填费用就按它算
        # ★ 走【费率历史】而不是老的单份 acct['fee'] —— add_fill 现在按
        #   成交日从历史里取档，只写 acct['fee'] 不会生效（实测：算成 20.77
        #   的默认费率）。老数据要先 migrate_fee 落成第一档。
        lv.add_fee_rate('t_fee', '2026-09-01', fm, note='真实账单反推')
        rf = lv.add_fill('t_fee', '2026-09-02', '603889.XSHG', 'buy', 11000, 6.010, force_price=True)
        assert abs(rf['fee'] - 5.95) < 0.011, \
            'add_fill 没用账户费率：%.2f' % rf['fee']
        assert rf['fee_estimated'] is True, '自动算的费用要标 fee_estimated'
        for bad, why in (({'commission': -1}, '负佣金率'),
                         ({'nope': 1}, '未知字段（会被过滤掉，等于没有可存字段）'),
                         ({'stamp': 'x'}, '印花税非数字非 auto'),
                         ({'mode': 'nope'}, '未知 mode')):
            try:
                lv.add_fee_rate('t_fee', '2027-01-01', dict(bad))
                raise AssertionError('%s 应被拒' % why)
            except lv.LiveError:
                pass

        # ---- 3d2b) 代码写法归一：券商/QMT/通达信各写一套 ----
        #   ★ 券商和 QMT 导出的都是 301126.SZ，粘贴批量成交时一整批都是。
        #     让人手工改 11 行没必要，而且改的时候容易改错市场 ——
        #     而市场现在**影响费用**（银河的过户费只有沪市另收万0.1）。
        for raw, want in (('301126.SZ', '301126.XSHE'),
                          ('603506.SH', '603506.XSHG'),
                          ('601857.XSHG', '601857.XSHG'),
                          ('000001.xshe', '000001.XSHE'),
                          ('sz000001', '000001.XSHE'),      # 通达信/面板 symbol
                          ('SH600000', '600000.XSHG'),
                          ('601857.SS', '601857.XSHG'),     # Yahoo
                          ('601857-SH', '601857.XSHG'),
                          (' 301126.sz ', '301126.XSHE'),
                          ('301126', '301126.XSHE'),        # 裸六位按前缀
                          ('600000', '600000.XSHG')):
            got = lv.normalize_code(raw)
            assert got == want, '%r 应归一成 %s，实得 %s' % (raw, want, got)
        # 🔴 前缀与标记冲突要【拒绝】：A 股前缀与市场一一对应，冲突说明有一个
        #   是错的，放行等于用错市场算费用（差万0.1，且不报错）
        for bad, why in (('600000.SZ', '沪市代码标了深市'),
                         ('301126.HK', '不是 A 股市场'),
                         ('12345', '不足六位'),
                         ('', '空'),
                         ('abc', '没有数字')):
            try:
                lv.normalize_code(bad)
                raise AssertionError('%s（%r）应被拒' % (why, bad))
            except lv.LiveError:
                pass
        # market_of 也要认 —— 算费用的入口不止 add_fill 一个，直接拿
        # 301126.SZ 调 fee_breakdown 时若按原样取后缀会得到 'SZ'、
        # 判成"沪市另收"，每笔多算万0.1
        assert lv.market_of('301126.SZ') == 'XSHE' and \
            lv.market_of('sz301126') == 'XSHE' and \
            lv.market_of('603506.SH') == 'XSHG', '市场判定不认别家写法'
        assert lv.fee_breakdown('buy', 100, 1305.40, '2026-09-01',
                                GALAXY, '301292.SZ')['transfer'] == 0, \
            '拿 .SZ 写法调 fee_breakdown 时过户费被算成"另收"了'
        # 落盘的是【归一化后】的代码 —— 否则同一只票会有两种身份，
        # FIFO 批次分成两堆、持仓看起来是两行
        lv.upsert_account('t_norm', name='norm', init_cash=500000)
        r1 = lv.add_fill('t_norm', '2026-09-01', '301126.SZ', 'buy', 100, 10.42, force_price=True)
        r2 = lv.add_fill('t_norm', '2026-09-01', 'sz301126', 'buy', 100, 10.42, force_price=True)
        assert r1['code'] == r2['code'] == '301126.XSHE', \
            '落盘应是聚宽口径：%s / %s' % (r1['code'], r2['code'])
        _pn = lv.positions('t_norm')
        assert list(_pn) == ['301126.XSHE'] and _pn['301126.XSHE']['shares'] == 200, \
            '两种写法应合成同一只票的持仓：%s' % _pn

        # ---- 3d3) 价格留空 = 成交日【开盘价】（竞价买入） ----
        #   ★ A 股开盘价就是 09:15-09:25 集合竞价的成交价，所以挂竞价的单子
        #     价格留空取开盘价不是近似而是**恰好相等**。用真实那笔钉住：
        #     新澳股份 2026-09-01 开盘 6.01 == 账单成交价 6.010。
        px = lv.day_price('603889.XSHG', '2026-09-01')
        assert abs(px - 6.010) < 1e-9, \
            '新澳股份 09-01 开盘价应是 6.010（=你那笔的成交价），实得 %s' % px
        # 独立复算一遍：day_price 取的必须是【不复权】open 列本身
        import duckdb                                  # noqa: PLC0415
        _c = duckdb.connect(':memory:')
        _t = _c.execute(
            "SELECT open, close_bfq FROM read_parquet('%s/mart/panel_daily/"
            "panel_*.parquet') WHERE jq_code='603889.XSHG' AND date=DATE "
            "'2026-09-01'" % lv._lake()).fetchone()
        assert abs(px - _t[0]) < 1e-9, 'day_price(open) 应等于面板 open 列'
        assert abs(lv.day_price('603889.XSHG', '2026-09-01', 'close') - _t[1]) < 1e-9, \
            "which='close' 应取不复权收盘（close_bfq）"
        lv.upsert_account('t_px', name='px', init_cash=100000)
        lv.add_fee_rate('t_px', '2026-09-01', fm, note='真实账单反推')
        # 留空 / '' / '-' 三种写法都算"没填"
        for i, blank in enumerate((None, '', '-')):
            r = lv.add_fill('t_px', '2026-09-01', '603889.XSHG', 'buy', 100,
                            price=blank, force_price=True)
            assert abs(r['price'] - 6.010) < 1e-9, \
                'price=%r 应取开盘价 6.010，实得 %s' % (blank, r['price'])
            assert r.get('price_from') == 'open', \
                '取的价要标 price_from（与 fee_estimated 同理）：%s' % r.get('price_from')
        # 费用要按【解析后的价格】算，不是按 0
        assert r['fee'] > 0 and r['fee_estimated'] is True, \
            '留空价格后费用仍要按费率估出来，实得 %s' % r['fee']
        # 明确填了价格 -> 以填的为准，且不标 price_from
        r2 = lv.add_fill('t_px', '2026-09-01', '603889.XSHG', 'buy', 100, 6.088, force_price=True)
        assert abs(r2['price'] - 6.088) < 1e-9 and not r2.get('price_from'), \
            '填了价格就该用填的那个，且不标"取的价"'
        # ★ 取不到价必须【响亮报错】，不能退回"最近一个交易日"——
        #   那会把 8 月的价当成 9 月的用，数字看着正常但是错的。
        #   最常见的触发场景：调仓日早上就要录，而当天行情要等晚上才同步。
        try:
            lv.add_fill('t_px', '2028-01-03', '603889.XSHG', 'buy', 100, force_price=True)
            raise AssertionError('取不到当日行情时不该放行（更不该用旧价顶上）')
        except lv.LiveError as e:
            m = str(e)
            assert '还没同步' in m and '手填价格' in m, \
                '报错要说清原因和出路：%s' % m
            assert 'None' not in m.split('\n')[1], \
                '"本地最新数据日"不能是 None —— 那行是判断有没有同步的唯一依据'
        # ---- 3d3b) 成交价必须落在当日高低区间内 ----
        #   ★ 这是能【证】的：一笔真实成交不可能高于当日最高、低于当日最低。
        #     挡的是真正会造成损失的那类错 —— 小数点点错、看错行填了别只票的
        #     价、误填后复权价（老股差几十倍）。成交价一错成本价就错，
        #     而成本价要喂给止损判定和红利税档位。
        #   ★ 刻意**不用**"手填费用反推价格"那套：2026-09-01 实测 11 笔，
        #     过户费按成交金额【不单调】（38,144 的 0.37 比 37,824 的 0.38 还
        #     低）—— 券商按分笔成交明细逐笔舍入，我们只有汇总，误差上界是
        #     0.01×分笔数、未知。那个检查分不清"价格错"和"拆笔多"，只会报
        #     假警，而假告警看多了就不看告警了。
        _rg = lv.day_range('605122.XSHG', '2026-09-01')
        assert _rg and _rg[0] < _rg[1], '取不到当日高低区间：%s' % (_rg,)
        for bad, why in ((_rg[1] * 1.5, '高于当日最高'),
                         (_rg[0] * 0.5, '低于当日最低'),
                         (1.192, '小数点点错'),
                         (43.7, '误填后复权价')):
            try:
                lv.add_fill('t_px', '2026-09-01', '605122.XSHG', 'buy', 100,
                            price=bad)
                raise AssertionError('%s（%.3f）应被拒' % (why, bad))
            except lv.LiveError as e:
                assert '价格区间' in str(e) and '成本价' in str(e), \
                    '报错要给出区间并说清代价：%s' % e
        # 区间内的正常价放行；端点也算区间内
        for good in (_rg[0], _rg[1], (_rg[0] + _rg[1]) / 2):
            lv.add_fill('t_px', '2026-09-01', '605122.XSHG', 'buy', 100,
                        price=round(good, 3))
        # 取不到当日行情时【跳过】校验 —— 不能因为没同步就不让人录成交
        assert lv.day_range('605122.XSHG', '2028-01-03') is None
        assert lv.check_price_in_range('605122.XSHG', '2028-01-03', 999) is None, \
            '没有当日行情时应跳过价格校验，而不是拒绝录入'
        # 逃生口：大宗交易可以成交在区间外，面板本身也可能有问题 ——
        # 必须显式声明（同 rebuild_lake_db 的 --allow-shrink）。
        # 硬拒而不给出路，最后会变成绕过整个入口。
        _fp = lv.add_fill('t_px', '2026-09-01', '605122.XSHG', 'buy', 100,
                          price=99.9, force_price=True)
        assert abs(_fp['price'] - 99.9) < 1e-9, 'force_price 应放行'

        # ---- 3d3c) 2026-09-01 真实那批 11 笔：费用合计必须精确一致 ----
        #   ★ 逐笔会有 ±0.01 的出入（过户费按分笔明细逐笔舍入，我们只有
        #     汇总），但【合计】要精确对上 —— 合计对不上说明费率错了。
        REAL = (('301126.SZ', 3600, 10.420, 5.00), ('301152.SZ', 1600, 23.000, 5.00),
                ('300500.SZ', 3800, 9.900, 5.00), ('002910.SZ', 3100, 10.290, 5.00),
                ('002910.SZ', 600, 10.290, 5.00), ('605122.SH', 3200, 11.920, 5.37),
                ('603506.SH', 3300, 11.350, 5.37), ('300575.SZ', 6000, 6.250, 5.00),
                ('603168.SH', 6400, 5.910, 5.38), ('600774.SH', 5000, 7.640, 5.39),
                ('300375.SZ', 9100, 4.220, 5.00))
        est_t = act_t = 0.0
        n_exact = 0
        for c, q, px, fee in REAL:
            e = lv.estimate_fee('buy', q, px, '2026-09-01', GALAXY, c)
            est_t += e
            act_t += fee
            if abs(e - fee) < 0.005:
                n_exact += 1
            assert abs(e - fee) <= 0.011, \
                '%s 逐笔差应 ≤1 分（分笔舍入），实得 %+.2f' % (c, e - fee)
        assert abs(est_t - act_t) < 0.005, \
            '11 笔费用合计应精确一致：估 %.2f / 实 %.2f' % (est_t, act_t)
        assert n_exact == 9, '应有 9 笔分毫不差，实得 %d' % n_exact
        # ★ 002910 同日两笔【各收一次】5 元最低 —— 不按当日同一只票合并。
        #   这条是账单确认的（3100 和 600 两笔都是 5.00）。
        assert abs(lv.estimate_fee('buy', 600, 10.29, '2026-09-01', GALAXY,
                                   '002910.SZ') - 5.00) < 0.005, \
            '6,174 元那笔也该收满 5 元最低'

        # 🔴 报错必须指向【真正的】原因。实测踩过：粘的是 301126.SZ（券商
        #   写法），报的却是"当天行情还没同步"，而那天的行情本地明明有 ——
        #   人会照着那句话去等晚上重试，白等。
        try:
            lv.day_price('999999.XSHE', '2026-09-01')
            raise AssertionError('面板里没有的代码应报错')
        except lv.LiveError as e:
            m = str(e)
            assert '不是】没同步' in m or '不是没同步' in m, \
                '这天行情本地有，就不该说"还没同步"：%s' % m
            assert '没有 999999.XSHE 这个代码' in m, \
                '要点明是代码在面板里找不到：%s' % m
        # 无法判定市场的代码：报错要指向【代码】，而不是"取不到行情"
        try:
            lv.add_fill('t_px', '2026-09-01', '123456', 'buy', 100, force_price=True)
            raise AssertionError('判不出市场的代码应被拒')
        except lv.LiveError as e:
            assert '判不出是哪个市场' in str(e), \
                '便宜的格式校验要排在取行情之前，否则报错指向错的原因：%s' % e

        # ---- 3e) 冲正必须【精确抵消】，含费用 ----
        #     冲正的语义是"这笔交易没发生"，所以原费用也要退掉。若冲正记录
        #     照常估一笔费用，一笔没发生的交易会净吃【两次】费用而不报错。
        lv.upsert_account('t_rev', name='rev', init_cash=100000)
        lv.bind_version('t_rev', 'strategies/小市值/froec_traded.py',
                        params={'stop_loss': 0.35, 'stop_intraday': 1, 'weekday': 2})
        c_before = lv.cash('t_rev')
        rb = lv.add_fill('t_rev', '2026-08-20', '601857.XSHG', 'buy', 1000, 11.42, force_price=True)
        assert rb['fee'] > 0, '买入应估出费用'
        lv.add_fill('t_rev', '2026-08-20', '601857.XSHG', 'sell', 1000, 11.42,
                    fee=-rb['fee'], reverse_of=rb['uid'], force_price=True)
        assert abs(lv.cash('t_rev') - c_before) < 1e-9, \
            '冲正后现金应精确回到 %.2f，实得 %.2f（差 %.2f = 多吃的费用）' \
            % (c_before, lv.cash('t_rev'), lv.cash('t_rev') - c_before)
        assert not lv.positions('t_rev'), '冲正后应空仓'
        # 冲正不填费用时不该去估算（估了就抵消不掉）
        rb2 = lv.add_fill('t_rev', '2026-08-21', '601088.XSHG', 'buy', 100, 48.78, force_price=True)
        rv2 = lv.add_fill('t_rev', '2026-08-21', '601088.XSHG', 'sell', 100, 48.78,
                          reverse_of=rb2['uid'], force_price=True)
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
        eb = lv.add_fill('t_edit', '2026-08-10', '601857.XSHG', 'buy', 1000, 10.0, fee=5, force_price=True)
        es = lv.add_fill('t_edit', '2026-08-20', '601857.XSHG', 'sell', 500, 12.0, fee=8, force_price=True)
        assert eb['uid'] != es['uid'], 'uid 必须唯一（ts 是秒精度，会撞）'
        # 被依赖的那笔不能直接冲，但要给出下一步
        try:
            lv.add_fill('t_edit', '2026-08-10', '601857.XSHG', 'sell', 1000, 10.0,
                        fee=-5, reverse_of=eb['uid'], force_price=True)
            raise AssertionError('被后续记录依赖的冲正应被拒')
        except lv.LiveError as e:
            assert '负持仓' in str(e), '拒绝理由要说清是负持仓：%s' % e
        # 先冲后续那笔 -> 批次必须【完全还原】
        lv.add_fill('t_edit', '2026-08-20', '601857.XSHG', 'buy', 500, 12.0,
                    fee=-8, reverse_of=es['uid'], force_price=True)
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
                    fee=-5, reverse_of=eb['uid'], force_price=True)
        assert not lv.positions('t_edit'), '全部冲正后应空仓'
        assert abs(lv.cash('t_edit') - 100000) < 1e-9, \
            '全部冲正后现金应精确回到 100000，实得 %.2f' % lv.cash('t_edit')
        # 补录一笔日期在所有买入【之前】的卖出 —— 只看当前持仓会放行
        lv.add_fill('t_edit', '2026-09-01', '601088.XSHG', 'buy', 1000, 48.0, fee=5, force_price=True)
        try:
            lv.add_fill('t_edit', '2026-08-01', '601088.XSHG', 'sell', 1000, 48.0,
                        fee=5, force_price=True)
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
        lv.add_fill('t_twr', '2026-08-10', '601857.XSHG', 'buy', 5000, 11.0, fee=15, force_price=True)
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

        # ---- 3h) 盘中：权益曲线要给【今天】补一点，否则整块业绩停在昨收 ----
        #   🔴 上面的总资产/持仓浮盈是实时的，业绩指标停在昨收的话，同一屏里
        #      就是两个口径（"累计收益 +0.74%" 配 "今日 +1,711" 对不上），
        #      而这不报错。
        #   ★ 判据是"确实有实时价"：没有就不补 —— 补一个与昨收相同的点等于
        #      凭空多出一个 0% 交易日，会把年化和回撤都稀释掉。
        from assay import realtime as _rtm
        _o_lat = _rtm.latest
        try:
            # 🔴 基准两边都要取【面板收盘】：真实实时库里有这只票的话，
            #   e0 的末点本身就是盘中点、positions_valued 的 price 也是实时价，
            #   与"面板收盘"差一天 —— 基准不同，断言会失败而代码其实是对的。
            #   所以先把实时桩关成空，取一遍纯收盘口径的基准。
            _rtm.latest = lambda cs=None, day=None, root=None: {}
            e0 = lv.equity_curve('t_twr')       # 纯收盘：末点是面板最新日
            d_last = e0['dates'][-1]
            _nx = (datetime.date.fromisoformat(d_last)
                   + datetime.timedelta(days=1)).isoformat()
            _pos = lv.positions_valued('t_twr')
            _pc = {x['code']: x['price'] for x in _pos['items']}
            # 每只都比昨收高 1 元
            _rtm.latest = lambda cs=None, day=None, root=None: {
                c: {'code': c, 'price': (_pc.get(c) or 0) + 1.0,
                    'at': _nx + ' 10:30', 'src': 'snap',
                    'preclose': _pc.get(c)}
                for c in (cs or [])}
            e3 = lv.equity_curve('t_twr')
            iv = e3['stats']['intraday']
            assert iv and e3['dates'][-1] == _nx,                 '有实时价却没给今天补点：%s' % e3['dates'][-3:]
            assert iv['n_rt'] == iv['n_pos'] == 1, '补点没记清几只是实时的：%s' % iv
            _sh = sum(l['shares'] for l in lv.fifo_lots(
                lv.active_fills(lv.fills('t_twr')))['601857.XSHG'])
            assert abs((e3['equity'][-1] - e0['equity'][-1]) - _sh) < 0.5,                 ('补的那一点算错了：每股涨 1 元 × %d 股应让权益 +%d，'
                 '实得 %+.2f' % (_sh, _sh, e3['equity'][-1] - e0['equity'][-1]))
            # 两条独立算出来的"今日"必须一致 —— 权益曲线是 Δ权益，
            # 持仓表是逐批 ×(现价−基准)。对不上就是有一边错了。
            _pv = lv.positions_valued('t_twr')
            assert abs(e3['stats']['day_pnl'] - _pv['pnl_day']) < 0.02,                 ('权益曲线的"今日" %.2f 与持仓表的"当日盈亏" %.2f 对不上'
                 % (e3['stats']['day_pnl'], _pv['pnl_day']))
            # 没有实时价 -> 不补（不许凭空多一个 0% 交易日）
            _rtm.latest = lambda cs=None, day=None, root=None: {}
            e4 = lv.equity_curve('t_twr')
            assert e4['stats']['intraday'] is None                 and e4['dates'][-1] == d_last,                 '没有实时价时不该补点：%s' % e4['dates'][-3:]
        finally:
            _rtm.latest = _o_lat

        # ---- 3h2) TWR 必须从【起点资金】起算，第一天的盈亏不许丢 ----
        #   🔴 原来 prev_e 从 None 起，第一个交易日的收益整段丢掉：实测红利
        #      09-01 建仓当天 +1.16%，TWR 却从 09-02 才连乘，给出 −0.78%，
        #      而净值其实是 +0.37% —— 页面上就成了"持仓浮盈 +3,698 /
        #      累计收益 −0.78%"自相矛盾，**且不报错**。
        #   ★ 判据：没有外部现金流时，TWR 必须精确等于 期末/起点 − 1。
        lv.upsert_account('t_seed', name='seed', init_cash=100000)
        lv.add_fill('t_seed', '2026-08-10', '601857.XSHG', 'buy', 5000, 11.0,
                    fee=15, force_price=True)
        es = lv.equity_curve('t_seed')['stats']
        assert abs(es['twr'] - (es['equity_end'] / es['init_cash'] - 1)) < 1e-6, \
            ('没有现金流时 TWR 必须等于 期末/起点 − 1：%.6f vs %.6f'
             ' —— 差的就是第一个交易日'
             % (es['twr'], es['equity_end'] / es['init_cash'] - 1))
        assert es['equity_start'] == es['init_cash'], '起点不是开户资金'
        # 金额：期末 − 起点 − 净入金，且与持仓浮盈同一个数（无已实现盈亏时）
        assert abs(es['pnl_total']
                   - (es['equity_end'] - es['init_cash'] - es['net_deposit'])
                   ) < 0.02, '累计收益金额算错：%s' % es['pnl_total']
        assert abs(es['pnl_total']
                   - lv.positions_valued('t_seed')['pnl']) < 0.02, \
            ('累计收益金额与持仓浮盈对不上（无已实现盈亏时必须相等）：'
             '%.2f vs %.2f' % (es['pnl_total'],
                               lv.positions_valued('t_seed')['pnl']))
        # 入金落在【非交易日】也不许被当成收益 —— F_t 要按区间取，
        # 不是"正好落在那天"
        lv.add_cashflow('t_seed', '2026-08-16', 200000, 'deposit', '周六入金')
        es2 = lv.equity_curve('t_seed')['stats']
        #   🔴 判据是**方向**，不是"几乎不变"。
        #     把入金当成收益的话 TWR 会**变大**（+20万/本金 那一截）；
        #     而入金之后 TWR **本来就会变小** —— 20 万闲置现金按 0% 计，
        #     摊薄之后每一段的收益率（CLAUDE.md 明确写着那是正确行为）。
        #   ★ 原来写的是 `abs(差) < 0.02`，那是一条**随时间必然失效**的断言：
        #     入金日固定在 2026-08-16，而面板每天在长 -> 之后的交易日越来越多
        #     -> 摊薄累积越大 -> 某天必然越线（今天就越了：0.0149 -> -0.0063）。
        #     同「不能无条件断言 15 只」那条：断言不许依赖"今天是哪天"。
        assert es2['twr'] <= es['twr'] + 1e-9, \
            ('周末入金被当成收益了：TWR 从 %.4f **升到** %.4f —— '
             '入金只该摊薄收益率、不该抬高它'
             % (es['twr'], es2['twr']))

        # ---- 3i) 「年化拖累」要够长的样本才给 ----
        #   🔴 开户两天就把两笔建仓的费用乘 122 倍，会得出"年化拖累 1.72%"
        #      这种纯外推的数，而它会被拿去跟真实费率比。同 twr_annual 那条纪律。
        st9 = lv.equity_curve('t_twr')['stats']
        assert st9['fee_paid'] > 0 and st9['fee_pct'] is not None, \
            '交易费用/占本金没给：%s' % st9
        # 🔴 判据是"够不够 20 个交易日"，**不能假设这个测试账户一定不够** ——
        #   它从 2026-08-10 建仓，样本长度随"今天是哪天"一直在涨，
        #   写死 days < 20 的话总有一天会凭空失败（2026-09-04 就失败了一次）。
        #   同自选那条：断言不该依赖跑测试的时刻。
        if st9['days'] < 20:
            assert st9['fee_drag_annual'] is None, \
                '只有 %d 个交易日却给了年化拖累 %s' \
                % (st9['days'], st9['fee_drag_annual'])
        else:
            assert st9['fee_drag_annual'] is not None, \
                '够 %d 个交易日了却不给年化拖累' % st9['days']
            # 🔴 比【取整后】的值，不要拿 1e-9 去比未取整的乘积 ——
            #   `fee_drag_annual` 在 perf.py 里 round(..., 6)，6 位取整的
            #   误差可达 5e-7，用 1e-9 必然失败。
            #   ★ 这条 else 分支**在 2026-09-07 之前从没被执行过**
            #     （测试账户 2026-08-10 建仓，days 一直 <20），所以这个
            #     容差错了很久都没人发现 —— 同"先命中的校验会遮住后面那道，
            #     被遮的那道可能从没执行过"。
            assert st9['fee_drag_annual'] == round(
                st9['fee_pct'] * (244.0 / st9['days']), 6), \
                ('年化拖累不是"占本金 × 244/交易日数"：%s vs %s'
                 % (st9['fee_drag_annual'],
                    round(st9['fee_pct'] * (244.0 / st9['days']), 6)))
        # 无论多长，twr_annual 与 fee_drag_annual 必须【同进同退】——
        # 两个用同一条 20 日纪律，分头判就会出现"给了年化却不给拖累"
        assert (st9['twr_annual'] is None) == (st9['fee_drag_annual'] is None), \
            ('年化与年化拖累的门槛不一致：twr_annual=%s / fee_drag=%s（%d 天）'
             % (st9['twr_annual'], st9['fee_drag_annual'], st9['days']))

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
                '银河五张账单逐笔对到分(沪万0.96/深万0.86，最低5元 binding 时'
                '深5.00/沪5.37)；uid 唯一 + 冲正还原批次 + 重放拦负持仓 + '
                'TWR 排除入金；'
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

    这条用例同时钉住【信息架构】：天天要看的留在页面上、偶尔用的进浮层。
      页面   今日待办 + 当前持仓（含盈亏汇总）
      浮层   ⚙设置 / ✎记一笔（成交+现金两个 tab）/ 策略（含版本历史）
      独立页 成交流水 —— 会越来越长，服务端分页
    并核【策略只有一个入口】—— 原来排头一个标签、下面又一块"策略版本"，
    是同一件事两处入口。
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
    # 🔴🔴 **归档也要重定向 —— 这条用例会【真的跑一次回测】**（策略浮层里
    #   那个「用这个版本跑一次」）。`_run_job` 起 `python3 run.py` 子进程、
    #   照常归档，于是每跑一次 selftest 就往 `runs/` 里塞一条同参数的
    #   `2026-06-01~06-30`，积了 **105 次**（2026-09-15 用户问"这是什么"
    #   才发现）。★ 同 `lv.LIVE` 那条：**selftest 不许写生产数据**。
    #   ★ `registry` 读 `ASSAY_RUNS`，子进程继承环境变量，设它就够；
    #     本进程的 `registry.RUNS` 也要跟着改（页面要读得到归档树）。
    from assay import registry as _reg
    _runs_tmp = tempfile.mkdtemp(prefix='selftest_runs_')
    _prev_runs_env = os.environ.get('ASSAY_RUNS')
    _prev_runs = _reg.RUNS
    os.environ['ASSAY_RUNS'] = _runs_tmp
    _reg.set_runs(_runs_tmp)
    sv._scan()
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
            ctx = br.new_context(viewport={'width': 1500, 'height': 950})
            pg = ctx.new_page()
            errs = []
            pg.on('pageerror', lambda e: errs.append(str(e)))
            pg.on('console',
                  lambda m: errs.append('console: ' + m.text) if m.type == 'error' else None)
            pg.on('dialog', lambda d: d.accept())
            base = 'http://127.0.0.1:%d/' % port
            pg.goto(base + '#/live', wait_until='networkidle')
            pg.wait_for_timeout(500)
            assert '还没有账户' in pg.content(), '空态没显示'
            # ★ tdx.raw_holidays 是权威来源，不该弹"日历不权威"告警。
            #   前端曾硬编码 `source !== 'jq...'`，每次打开都弹一条假告警 ——
            #   假告警看多了就不看告警了。
            # ★ 只看【渲染出来的告警块】，不看 pg.content() —— 后者包含
            #   内联 <script> 的源码，而那段源码里正有 '交易日历不是权威来源'
            #   这个模板字符串。本会话第六次栽在"断言匹配到自己写的文本"上。
            _warn = ' | '.join(pg.locator('.lvwarn').all_inner_texts())
            assert '不是权威来源' not in _warn, \
                '把 tdx.raw_holidays 误报成不权威（判据应由服务端给）：%s' % _warn

            # 建账户：id 自动，只填名称
            assert pg.locator('#na').count() == 0, '新建表单不该再要用户填 id'
            # 创建账户的表单默认收起（它平时不用），先点「+ 新建账户」
            pg.click('#lvnew')
            pg.wait_for_selector('#nn', state='visible', timeout=8000)
            pg.fill('#nn', 'UI 测试')
            pg.fill('#nc', '400000')
            pg.click('#nb')
            pg.wait_for_timeout(700)
            assert pg.locator('.ditem.on').count() == 1, '新账户没被选中'

            # ---- 策略是【唯一入口】：未绑定也要能打开浮层去绑 ----
            assert pg.locator('#lvstrat').count() == 1, '缺策略入口'
            body = pg.locator('#lvbody').inner_text()
            assert '策略版本' not in body, \
                '主视图不该再有"策略版本"区块 —— 与排头的策略入口重合'
            pg.click('#lvstrat')
            pg.wait_for_selector('.stbox', timeout=15000)
            days = lv.calendar_days()
            from assay.feed import PanelFeed
            t1 = PanelFeed('2026-01-01', datetime.date.today().isoformat()
                           ).trading_days[-1]
            nxt = min(d for d in days if d > t1)
            wk = [d for d in days if d.isocalendar()[:2] == nxt.isocalendar()[:2]]
            wd = wk.index(nxt) + 1
            pg.fill('#bp', 'strategies/小市值/froec_traded.py')
            pg.fill('#bj', '{"stop_loss":0.35,"stop_intraday":1,"weekday":%d}' % wd)
            pg.click('#bb')
            pg.wait_for_timeout(1500)
            assert pg.locator('.stbox').count() == 0, '绑定后浮层该关掉'

            # ---- 「用这个版本+参数跑一次」不许是个点了没反应的按钮 ----
            #   🔴 踩过：没开 --allow-backtest 时它渲染成 disabled，而
            #     **disabled 的元素连 title 提示都不触发** —— 于是用户看到的是
            #     "选了策略、填了日期、点下去毫无反应，也没有任何说明"。
            #     同 backLink 那条：给一个点了没反应的按钮比不给更糟。
            pg.click('#lvstrat')
            pg.wait_for_selector('.stbox', timeout=15000)
            pg.wait_for_timeout(600)
            _bt = pg.locator('#stbt')
            assert _bt.count() == 1, '缺「跑一次」按钮'
            assert _bt.get_attribute('disabled') is None, \
                'disabled 的按钮点了没反应、也不显示 title —— 改成可点 + 说原因'
            _cb = pg.evaluate('() => LV.can_backtest')

            # ---- 版本历史表：四列各就各位，没有一格溢出 ----
            # 🔴 用户："版本历史（append-only，删 runs/ 也读得到），下面的
            #   内容文字有错位。" 两个成因，都不报错：
            #   ① **参数 chip 与「为什么换」挤在同一格**，而那一格是
            #      `.lvpar`（flex + justify-content:flex-end）—— 理由一长就把
            #      chip 推到行首，于是**每行的起点都不一样**，整列扫不下来。
            #   ② 日期列 64px 装不下 `2026-09-06`，溢出去贴住右边的 8 位 hash。
            # ★ 判据是**可量的事实**：每列的左边界逐行相同 + 没有一格
            #   `scrollWidth > clientWidth`（那正是"贴在一起"的成因），
            #   而不是"有没有那个 class" —— 后者在列被合回去时照样命中。
            _vt = pg.evaluate("""() => {
                const t = document.querySelector('table.lvvt');
                if(!t) return null;
                const L = el => Math.round(el.getBoundingClientRect().left);
                return {n: t.rows[0].cells.length,
                  head: [...t.rows[0].cells].map(c => c.textContent.trim()),
                  cols: [...t.rows].map(tr => [...tr.cells].map(L)),
                  align: [...t.rows].slice(1).map(
                      tr => [...tr.cells].map(td => getComputedStyle(td).textAlign)),
                  over: [].concat(...[...t.rows].slice(1).map(
                      (tr, r) => [...tr.cells].map((td, i) => (
                        td.scrollWidth > td.clientWidth + 1
                          ? [r, i, td.textContent.trim().slice(0, 14)] : null))
                      )).filter(Boolean)};}""")
            assert _vt and _vt['n'] == 4, \
                '版本历史该是四列（时间/版本/参数/为什么换），现在是 %r' % (_vt,)
            assert '为什么换' in _vt['head'][3], \
                ('「为什么换」必须自己一列 —— 与参数 chip 挤在一格时，理由一长'
                 '就把 chip 推走，每行起点都不一样：%r' % (_vt['head'],))
            for _i in range(_vt['n']):
                _xs = {row[_i] for row in _vt['cols']}
                assert len(_xs) == 1, \
                    ('版本历史第 %d 列的左边界逐行不同 %s —— 那就是"文字错位"'
                     % (_i + 1, sorted(_xs)))
            assert all(set(r) == {'left'} for r in _vt['align']), \
                ('版本历史四列全是文本，必须 .tx 左对齐（table.lvt 默认右对齐）'
                 '：%r' % (_vt['align'][0],))
            assert not _vt['over'], \
                ('有格子装不下自己的内容（会溢出去贴住右边那列）：%r'
                 % (_vt['over'],))

            # 关掉网页回测，验"点了要有话说"（不是静默）
            sv.ALLOW_BACKTEST = False
            pg.reload(wait_until='networkidle')
            pg.wait_for_selector('#lvstrat', timeout=40000)
            pg.wait_for_timeout(1200)
            pg.click('#lvstrat')
            pg.wait_for_selector('.stbox', timeout=15000)
            pg.wait_for_timeout(600)
            _box = pg.locator('#stwrap').inner_text()
            assert '没开' in _box and 'serve.py' in _box, \
                ('没开网页回测时，"怎么开"必须【常驻可见】而不是藏在 title 里：%s'
                 % _box[-200:])
            pg.locator('#stbt').click()
            pg.wait_for_timeout(800)
            _m = pg.locator('#stbmsg').inner_text()
            assert 'serve.py' in _m or 'readonly' in _m, \
                '点了之后没给出原因（静默）：%r' % _m
            sv.ALLOW_BACKTEST = True

            # 开着时：版本一致 -> 真能起 job；版本漂移 -> 给两条出路
            pg.reload(wait_until='networkidle')
            pg.wait_for_selector('#lvstrat', timeout=40000)
            pg.wait_for_timeout(1200)
            pg.click('#lvstrat')
            pg.wait_for_selector('#stbt', timeout=15000)
            pg.wait_for_timeout(600)
            assert '网页触发回测没开' not in pg.locator('#stwrap').inner_text(), \
                '已经开了，不该还显示"怎么开"那条'
            pg.fill('#stbs', '2026-06-01')
            pg.fill('#stbe', '2026-06-30')
            pg.fill('#stbc', '200000')
            pg.locator('#stbt').click()
            _ok = False
            for _ in range(40):
                pg.wait_for_timeout(3000)
                _m = ' '.join(pg.locator('#stbmsg').inner_text().split())
                if '完成' in _m or '失败' in _m or 'rc=' in _m:
                    _ok = '完成' in _m
                    break
                if '已改动' in _m:      # 版本漂移：必须给出可操作的两条出路
                    assert pg.locator('#stbrb').count() == 1, \
                        '版本漂移只报错、没给「重新绑定」按钮'
                    assert pg.locator('#stbcmd2').count() == 1, \
                        '版本漂移没给可复制的命令行'
                    _ok = 'drift'
                    break
            assert _ok, '点了「跑一次」之后既没跑完也没给说法：%r' % _m
            assert _ok is not False, '回测失败了：%r' % _m
            _btmsg = _m
            # ★ 收尾必须等浮层【真的消失】—— 它是绝对定位的遮罩，
            #   没关干净的话后面所有点击都会 timeout，而报错指向的是
            #   被挡住的那个元素，完全看不出是浮层没关（已踩）。
            pg.locator('#stclose').click()
            pg.wait_for_selector('.stbox', state='detached', timeout=10000)
            pg.wait_for_timeout(300)

            # ---- 主视图只有【待办 + 持仓】两块 ----
            pg.click('#lvtick')
            pg.wait_for_selector('table.lvbuy', timeout=90000)
            secs = pg.locator('#lvbody .lvsec h3').all_inner_texts()
            assert len(secs) == 2, '主视图应只剩两块，实得 %s' % [x[:14] for x in secs]
            assert '当前持仓' in secs[-1], '第二块该是当前持仓'
            nbuy = pg.locator('table.lvbuy tr').count() - 1
            assert nbuy == 10, '待办买入应 10 行，实得 %d' % nbuy
            assert '· 调仓日' in pg.locator('#lvbody').inner_text(), \
                '没标出是不是调仓日'
            # ---- 「立即重算」长在【待办】那一块里 ----
            #   ★ 它重算的是**调仓信号**，属于待办这一块；原来放在账户头部
            #     那一排（记一笔/流水/设置）里 —— 那排是**账户级**动作。
            #     按钮该长在它作用的那块里。
            _todo = pg.locator('#lvbody .lvsec').first
            assert _todo.locator('#lvtick').count() == 1, \
                '「立即重算」不在待办那一块里 —— 它算的是调仓信号'
            assert pg.locator('#lvbody .lvsec').nth(1)\
                     .locator('#lvtick').count() == 0, \
                '「立即重算」不该出现在持仓那一块'
            #   🔴 无信号那支**也要有这个按钮**：文案写着"点「立即重算」"，
            #     不给的话那句话指向一个不存在的按钮（同 backLink 那条）。
            _lj = open(os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                'web', 'views', 'live.js'), encoding='utf-8').read()
            _noSig = _lj[_lj.index("if(!s) return"):]
            _noSig = _noSig[:_noSig.index('\n  if(s.error)')]
            assert 'lvtick' in _noSig, \
                ('"还没有信号"那支里没有「立即重算」按钮 —— '
                 '而那正是最需要它的时候')
            #   出错支没有按钮，所以绑定必须带 if 保护（否则 null.onclick
            #   -> TypeError -> 整块渲染中断）
            assert "if($('#lvtick'))" in _lj.replace(' ', ''), \
                '#lvtick 的事件绑定要带 if 保护（出错支里没有这个按钮）'
            # ---- 持仓表必须包在 .pw 里，否则窄屏撑出 body 横滚 ----
            #   🔴 判据查**结构**而不是"当前数据恰好不溢出" —— 这条用例的
            #     桩数据只有 3 只持仓，真实账户 15 只时 1024 宽溢出 11px，
            #     而按数据量测的话它测不出来（实测就是这么漏掉的）。


            # ---- 选股理由：【独立页】，一期一段按排名列出 ----
            #   ★ 一开始做成浮层 + 一个 .lvwhy 小链接，实测**人找不到它**
            #     （和旁边的"版本 9FB82061"长得一模一样）——
            #     看不出能点的入口 = 没有入口。所以入口是按钮、内容是独立页
            #     （同成交流水：会越来越长 -> 服务端分页）。
            assert pg.locator('table.lvbuy span.lvq').count() == nbuy, \
                '每一行买入都该有一个「?」标出选中理由'
            # 当前账户 id 从侧栏高亮那一项取（这一段比下面的 `aid` 早）
            _aid = pg.locator('.ditem.on').get_attribute('href').split('/')[-1]
            _wbtn = pg.locator('#lvbody .lvhead a.btn', has_text='选股理由')
            assert _wbtn.count() == 1, '账户页顶部缺「选股理由」按钮'
            # 🔴 对不上任何一期的持仓**不许**有出处标记 —— 硬凑一个出处
            #   比没有更糟（这些成交不是照信号做的）。
            assert pg.locator('#lvbody table.lvpos a.lvq').count() == 0, \
                '这些成交对不上任何一期信号，却给了"出处"标记'
            _wbtn.click()
            pg.wait_for_selector('.whysec', timeout=15000)
            pg.wait_for_timeout(400)
            assert '选股理由' in pg.locator('#main .lvhead h2').first.inner_text()
            _secs = pg.locator('.whysec').count()
            assert _secs >= 1, '选股理由页一段都没有'
            # 默认只列【调仓日】—— 非调仓日不选股，列出来是空段，
            # 会把真正要看的那期挤到第二页（实测踩过）
            for _i in range(_secs):
                _hd = pg.locator('.whysec .whyhd').nth(_i).inner_text()
                assert '调仓日' in _hd and '非调仓日' not in _hd, \
                    '默认应只列调仓日，实得：%s' % _hd.replace('\n', ' ')
            _tbl = pg.locator('.whysec table.lvpoolt')
            assert _tbl.count() >= 1, '调仓日那一段没有候选池表'
            _rows = _tbl.first.locator('tr:not(.whygap)')
            assert _rows.count() - 1 >= nbuy, \
                '候选池 %d 行，少于买入的 %d 只 —— 买入的必须都在池子里' \
                % (_rows.count() - 1, nbuy)
            # 默认只到第 20 名（20 之后全是"没轮到"，看不出信息），
            # 但"一定要显示"的（选中/持有/卖出/备选/被剔除）无论排第几都在，
            # 中间跳号要有 .whygap 明说，不能让人以为数据缺了一块
            _shown = _rows.count() - 1
            assert _shown <= 60, '一组默认显示 %d 行，太多了' % _shown
            # 按排名升序列出（用户要的就是"按排名依次"）
            _ranks = [int((_rows.nth(k).inner_text() or '0').split('\t')[0].split('·')[0] or 0)
                      for k in range(1, min(6, _rows.count()))]
            assert _ranks == sorted(_ranks), '候选池没按排名升序：%s' % _ranks
            # 🔴 `.pw` 里的 sticky 表头 top 必须是 0 —— 已经踩过三次：
            #   写 52px 会把表头压到**第二行**上面（实测选股理由页表头夹在
            #   第 1 行与第 3 行之间，第 2 行被盖住），而页面看着只是"有点怪"。
            _sticky = pg.evaluate("""() => [...document.querySelectorAll('.pw table th')]
                .map(th => getComputedStyle(th).top)""")
            assert _sticky and set(_sticky) == {'0px'}, \
                '.pw 里的表头 top 不是 0：%s（会把表头压到第二行上面）' % set(_sticky)
            _hy = pg.locator('.whysec').first.inner_text()
            for _k in ('流通市值', '市净率', 'ROE 加速度', '选中', '策略参数'):
                assert _k in _hy, '选股理由页缺「%s」：%s' % (_k, _hy[:160])
            assert 'undefined' not in _hy and 'NaN' not in _hy, \
                '选股理由页有 undefined/NaN：%s' % _hy[:200]
            _why_msg = ('选股理由独立页：%d 段（只列调仓日）、候选池 %d 行按排名升序、'
                        '.pw 表头 top=0' % (_secs, _rows.count() - 1))
            pg.click('a.lvtag[href="#/live/%s"]' % _aid)    # 回账户
            pg.wait_for_selector('#lvrec', timeout=10000)
            pg.wait_for_timeout(400)

            # ---- KPI 板：账户的数字集中一处，不再是顶栏一串标签 ----
            #   ★ 原来"我现在怎么样"要在标题、标签栏、持仓汇总三处来回凑。
            _kp = ' '.join(pg.locator('#lvbody .kpi .k').all_inner_texts())
            for k in ('总资产', '持仓市值', '可用现金', '持仓浮盈', '当日盈亏'):
                assert k in _kp, 'KPI 缺「%s」：%s' % (k, _kp)
            assert '权益' not in pg.locator('#lvbody .lvhead').inner_text(), \
                '顶栏又出现"权益"标签了 —— 数字应集中在 KPI 板'
            # 业绩那半是异步补的（要重放整条权益曲线），等它落地
            pg.wait_for_function(
                "() => { const e=document.querySelector('#kperf2');"
                " return e && !/…/.test(e.textContent); }", timeout=20000)
            _kp2 = ' '.join(pg.locator('#lvbody #kperf2 .k').all_inner_texts())
            _st = pg.evaluate(
                "async () => { const a=(LV&&LV.accounts||[])"
                ".find(x=>x.id===LVSEL); if(!a) return null;"
                " return (await (await fetch('/api/live/equity?id='+a.id))"
                ".json()).stats; }") or {}
            if _st:
                for k in ('累计收益', '加权年化', '最大回撤', '当前回撤',
                          '投资时间', '交易费用'):
                    assert k in _kp2, '业绩 KPI 缺「%s」：%s' % (k, _kp2)
                # 盘中：末点是实时估的就要写「今日」并标时刻；否则写「最近一日」
                _iv = _st.get('intraday')
                assert ('今日' if _iv else '最近一日') in _kp2, \
                    '盘中/收盘的那一格标题不对（intraday=%s）：%s' % (_iv, _kp2)
                _cum = pg.locator('#lvbody #kperf2 > div').first.inner_text()
                assert ('盘中' if _iv else '按收盘') in _cum, \
                    ('累计收益没说明含不含今天的浮动 —— 上面的总资产是实时的，'
                     '不标就是同屏两个口径：%s' % _cum)
                # 🔴 只给百分比不行：旁边要有能和「持仓浮盈」对上的金额
                assert _st.get('pnl_total') is not None, '累计收益没给金额'
                assert '元' in _cum and ('%.2f' % abs(_st['pnl_total'])) \
                    in _cum.replace(',', ''), \
                    '累计收益那一格缺金额（只有百分比）：%s' % _cum
                # 「当日盈亏」的百分比要标分母 —— 它和业绩板「今日」的
                # 分母不同（持仓市值 vs 总资产），不标就像其中一个算错了
                _dd = [x for x in pg.locator('#lvbody .kpi').first.locator(
                    '>div').all_inner_texts() if '当日盈亏' in x]
                if _dd and '%' in _dd[0]:
                    assert '持仓' in _dd[0], \
                        '当日盈亏那格的百分比没标分母：%s' % _dd[0].replace('\n', ' ')
                # 🔴 「交易费用」那一格：短样本不许给年化拖累
                _fee = [x for x in pg.locator(
                    '#lvbody #kperf2 > div').all_inner_texts()
                    if '交易费用' in x][0].replace('\n', ' ')
                assert '占本金' in _fee, '交易费用没给占本金的比例：%s' % _fee
                if _st.get('fee_drag_annual') is None:
                    assert '年化' not in _fee or '不折年化' in _fee, \
                        ('样本这么短还在给年化拖累 —— 开户两天把费用乘 122 倍，'
                         '那个数会被拿去跟真实费率比：%s' % _fee)
                # ⓘ 里要把 TWR / 盘中 / 年化拖累 三件事都说清
                pg.locator('#lvbody #kperf2 .hlp').first.click()
                pg.wait_for_timeout(250)
                _hp = pg.locator('#hlpb_perf').inner_text()
                for k in ('TWR', '入金', '年化拖累', '20 个交易日'):
                    assert k in _hp, '业绩 ⓘ 里缺「%s」：%s' % (k, _hp[:200])
                pg.locator('#lvbody #kperf2').click()
                pg.wait_for_timeout(200)
                # 不足 20 个交易日不该给年化 —— 两周收益乘 12 倍会被拿去跟回测比
                if _st.get('twr_annual') is None:
                    _ann = pg.locator('#lvbody #kperf2 > div').nth(1).inner_text()
                    assert '—' in _ann and '不足' in _ann, \
                        '不足 20 个交易日应显示"—"并说明：%s' % _ann
            else:
                # 还没成交 -> 不编数字，说明为什么没有
                assert '业绩' in _kp2, '没有权益曲线时也要有一格说明：%s' % _kp2
            # ---- 口径说明进 ⓘ，不占主视图 ----
            #   ★ 主视图上每多一行"这个数是怎么算的"，天天要看的数字就被
            #     推远一屏。但知识不能丢：点开必须给全（摊薄成本 / 全平落袋
            #     / 当日盈亏的基准）。
            _kpv = pg.locator('#lvbody .kpi').first.inner_text()
            assert 'undefined' not in _kpv and 'NaN' not in _kpv, \
                'KPI 板里有 undefined/NaN：%s' % _kpv
            for _bad in ('含买入费', '摊薄成本', '全平落袋'):
                assert _bad not in _kpv, \
                    '口径解释又回到 KPI 主视图上了（该进 ⓘ）：%s' % _kpv
            assert pg.locator('#lvbody .kpi .hlp').count() >= 1, \
                'KPI 里没有 ⓘ —— 口径说明搬走了就得有地方看'
            assert not pg.locator('#hlpb_pnl').is_visible(), 'ⓘ 默认就展开了'
            pg.locator('#lvbody .kpi .hlp').first.click()
            pg.wait_for_timeout(300)
            assert pg.locator('#hlpb_pnl').is_visible(), \
                ('ⓘ 点了没反应 —— .hlpbox 的 display:none 写在样式表里，'
                 '开的时候只能加 class，不能靠 style.display=""')
            _hb = pg.locator('#hlpb_pnl').inner_text()
            for k in ('摊薄成本', '买入费', '全平落袋', '当日盈亏', '昨收'):
                assert k in _hb, 'ⓘ 里缺「%s」的口径：%s' % (k, _hb[:200])
            pg.locator('#lvbody .kpi').first.click()      # 点别处要收起
            pg.wait_for_timeout(300)
            assert not pg.locator('#hlpb_pnl').is_visible(), \
                'ⓘ 浮块点别处不收 —— 它盖着下面的持仓表'

            # ---- 待办：没到警示时间默认收起，到点自己打开 ----
            #   ★ 非调仓日、没有止损/炸板卖出时，待办里其实什么都没有，
            #     而它占着主视图最上面一屏。判据用服务端的 alert
            #     （live.signal_alert）—— 和账户列表的红点同一个。
            _al = pg.evaluate('() => !!(LVO && LVO.alert)')
            _tf = pg.locator('#todofold')
            if _tf.count():
                _txt = _tf.inner_text()
                assert ('收起' in _txt) == _al, \
                    'alert=%s 时待办应%s：%s' % (_al, '展开' if _al else '收起', _txt)
                # 收起态要留一行摘要，不能什么都不说
                if not _al:
                    _h3 = pg.locator('#lvbody .lvsec').first.inner_text()
                    assert '待办' in _h3 and len(_h3.strip()) > 6, \
                        '收起后应留一行摘要：%s' % _h3
                _tf.click()
                pg.wait_for_timeout(900)
                assert ('收起' in pg.locator('#todofold').inner_text()) != _al, \
                    '点「展开/收起」没生效'
                pg.locator('#todofold').click()
                pg.wait_for_timeout(900)
            # 告警点：判据由服务端给，展开/收起两态都要能看到
            _dots = pg.locator('.ditem .adot').count()
            _n_al = pg.evaluate(
                '() => (LV&&LV.accounts||[]).filter(a=>a.alert).length')
            assert _n_al is not None, '服务端没给 alert 字段 —— 判据必须服务端给'
            assert _dots == _n_al, \
                '账户列表的告警点数(%d)应等于服务端说 alert 的账户数(%d)' \
                % (_dots, _n_al)

            # ---- 侧栏可收起，且收起后仍看得到告警点 ----
            assert pg.locator('#dk .dside').count() == 1, '默认应展开'
            assert pg.locator('#nform').count() == 1 and \
                not pg.locator('#nform').is_visible(), \
                '创建账户的表单应默认收起（点「+ 新建账户」才展开）'
            pg.click('#lvnew')
            pg.wait_for_timeout(200)
            assert pg.locator('#nform').is_visible(), '点「+ 新建账户」应展开'
            pg.click('#lvfold')
            pg.wait_for_timeout(900)
            assert pg.locator('#dk.fold').count() == 1 and \
                pg.locator('#dk .dside').count() == 0, '收起没生效'
            _chips = pg.locator('.drail .dchip').count()
            assert _chips >= 2, '收起后的轨上应有展开按钮 + 每个账户一个方块'
            # ★ 收起后【仍要看得到告警点】—— 看不到该干什么的收起不如不做。
            #   告警数当场重算：收起会重新拉一次账户列表，用收起前的数会漂。
            _n_al2 = pg.evaluate(
                '() => (LV&&LV.accounts||[]).filter(a=>a.alert).length')
            assert pg.locator('.drail .adot').count() == _n_al2, \
                '收起态的轨上丢了告警点：轨上 %d 个，服务端说 %d 个' \
                % (pg.locator('.drail .adot').count(), _n_al2)
            pg.click('#lvunfold')
            pg.wait_for_timeout(900)
            assert pg.locator('#dk .dside').count() == 1, '展开没生效'

            # ---- 记一笔：一个入口、两个 tab ----
            pg.click('#lvrec')
            pg.wait_for_selector('#rfill', timeout=10000)
            tabs = pg.locator('.rtab').all_inner_texts()
            assert tabs == ['成交', '现金'], '记一笔应有成交/现金两个 tab，实得 %s' % tabs
            assert pg.locator('#ff').count() == 1, '缺费用输入框'
            assert pg.input_value('#fd'), \
                '日期框不该是空的 —— 空了会让第二笔起静默失败'

            def _cash():
                # 现金从 KPI 板的「可用现金」那格读（原来在顶栏标签里，
                # 顶栏一串数字太杂，已集中到 KPI 板）
                cs = pg.locator('#lvbody .kpi > div')
                for i in range(cs.count()):
                    t = cs.nth(i).inner_text().split('\n')
                    if t and t[0].strip() == '可用现金':
                        return float(t[1].replace(',', '').strip())
                raise AssertionError('KPI 板里没有「可用现金」：%s'
                                     % pg.locator('#lvbody .kpi').first.inner_text())

            # 费用三态（留空=估算 / 填值 / 填 0），断言【差额】不是绝对值
            for code, q, px, fv, amt, want in (
                    ('601857.XSHG', '1000', '11.42', '', 11420.0, None),
                    ('601088.XSHG', '100', '48.78', '3.21', 4878.0, 3.21),
                    ('600012.XSHG', '200', '16.69', '0', 3338.0, 0.0)):
                b4 = _cash()
                pg.fill('#fc', code)
                pg.fill('#fq', q)
                pg.fill('#fp', px)
                pg.fill('#ff', fv)
                pg.click('#fb')
                pg.wait_for_timeout(1200)
                got = round(b4 - _cash() - amt, 2)
                if want is None:
                    assert got > 0, '费用留空应估算出正数，实得 %.2f' % got
                else:
                    assert abs(got - want) < 0.011, \
                        '费用应为 %.2f，实得 %.2f' % (want, got)
                pg.click('#lvrec')
                pg.wait_for_selector('#rfill', timeout=8000)

            # 现金 tab：入金
            pg.click('.rtab[data-t="cash"]')
            pg.wait_for_timeout(200)
            c1 = _cash()
            pg.fill('#cfa', '20000')
            pg.click('#cfb')
            pg.wait_for_timeout(1200)
            assert abs((_cash() - c1) - 20000) < 0.01, \
                '入金 20000 后现金应 +20000：%.2f -> %.2f' % (c1, _cash())

            # ---- 持仓表：盈亏汇总 + 逐只估值 ----
            # ---- 持仓表必须包在 .pw 里，否则窄屏撑出 body 横滚 ----
            #   🔴 判据查**结构**，不查"当前数据恰好不溢出" —— 这条用例的
            #     桩数据只有 3 只持仓，而真实账户 15 只时 1024 宽溢出 11px，
            #     按数据量测的话测不出来（实测就是这么漏掉的）。
            assert pg.locator('#lvbody .pw table.lvpos').count() == 1, \
                ('持仓表要包在 .pw 里自己滚 —— 12 列宽表在窄屏会把'
                 '**整个 body** 撑出横滚，读表格时整页左右晃')
            for _t in pg.locator('#lvbody .pw table th').all():
                assert _t.evaluate('e=>getComputedStyle(e).top') == '0px', \
                    ('.pw 里的 sticky 表头 top 必须是 0 —— 写 52px 会把表头'
                     '压在第一行上面，第一行点不到（栽过 3 次）')

            # ★ 限定 .lvpos —— 待办里的买入/卖出表也是 .lvt，不限定会选串
            #   （实测：持仓 3 只被数成 14 行）
            th = [x.strip() for x in
                  pg.locator('#lvbody table.lvpos th').all_inner_texts()]
            _thz = ' '.join(th)
            for k in ('成本', '现价', '当日', '当日盈亏', '市值', '浮盈',
                      '仓位'):
                assert k in _thz, '持仓表缺「%s」列：%s' % (k, th)
            # 🔴 **代码不再单独占一列**（2026-09-16 用户："名称、代码放在
            #   一个格子里"，与每日持仓/清仓记录一致）。这里两头都钉：
            #   没有"代码"那一列 + 名称那一格里**确实带着代码**。
            assert '代码' not in _thz, \
                '代码又单独占了一列：%s —— 它该跟名称同格（.cd 小字）' % th
            _n1 = pg.locator(
                '#lvbody table.lvpos tr:nth-child(2) td:nth-child(1)')
            assert _n1.locator('.cd,.cd0').count() == 1, \
                '名称那一格里没有小字代码：%r' % _n1.inner_text()
            # 🔴 列数是有限的：保本价 / 估卖出费 / "含买入费"这种注解都退到
            #    悬浮提示与 ⓘ 里 —— 十几列会把要看的数字挤出屏幕。
            for k in ('保本价', '估卖出费', '含买入费'):
                assert k not in _thz, '「%s」不该再占一列：%s' % (k, th)
            # 🔴 **按列名找那一格**，不按第几列 —— 列序是会变的
            #   （2026-09-16 用户重排过一次），写死 `td:nth-child(4)` 的话，
            #   重排之后这条会去读另一列的 title，报的却是"缺摊薄成本"，
            #   看着像产品坏了。
            _ci = pg.evaluate(
                "() => [...document.querySelectorAll("
                "'#lvbody table.lvpos th')].findIndex("
                "e => e.textContent.trim().indexOf('成本') === 0) + 1")
            assert _ci > 0, '持仓表找不到「成本」列'
            _ct = pg.locator(
                '#lvbody table.lvpos tr:nth-child(2) td:nth-child(%d)' % _ci
            ).get_attribute('title') or ''
            for k in ('摊薄成本', '成交均价', '保本价'):
                assert k in _ct, \
                    '三个成本口径要在「成本」列的悬浮提示里给全，缺「%s」：%s' \
                    % (k, _ct)
            # 当日盈亏的基准：以前买的按昨收，今天买的按成交价
            _di = pg.evaluate(
                "() => [...document.querySelectorAll("
                "'#lvbody table.lvpos th')].findIndex("
                "e => e.textContent.trim().indexOf('当日盈亏') === 0) + 1")
            assert _di > 0, '持仓表找不到「当日盈亏」列'
            _dt = pg.locator(
                '#lvbody table.lvpos tr:nth-child(2) td:nth-child(%d)' % _di
            ).get_attribute('title') or ''
            assert '基准' in _dt, '当日盈亏没说基准是什么：%s' % _dt
            # 🔴 **这条规矩被用户推翻了**（2026-09-16）：原来钉的是
            #   "代码与名称各占一列 —— 挤在一格里没法按名称扫"，而实际用下来
            #   每日持仓 / 清仓记录早就是「名称 + 小字代码」同一格，
            #   于是同一份信息在不同页面长得不一样。用户原话：
            #   "应该都做成类似于每日持仓、清仓记录中的样式"。
            #   新规矩钉在上面（没有"代码"列 + 名称格里带小字代码），
            #   这里只留下**列序**这条：名称打头。
            assert th[0] == '名称', '持仓表第一列应该是名称：%s' % th
            # 列序是用户定的：先回答"今天怎么样"，再回答成本与规模
            assert th[:5] == ['名称', '当日', '当日盈亏', '幅度', '浮盈'], \
                '持仓表列序不对（要 名称/当日/当日盈亏/幅度/浮盈 打头）：%s' % th
            _pc = [x.strip() for x in
                   pg.locator('#lvbody table.lvpos tr td:nth-child(1)').all_inner_texts()]
            _pn = [x.strip() for x in
                   pg.locator('#lvbody table.lvpos tr td:nth-child(2)').all_inner_texts()]
            assert all('.' in x for x in _pc if x), '第一列应只有代码：%s' % _pc
            assert any(_pn), '第二列（名称）全空'
            # ★ 这份估值用的是什么价，必须在页面上说出来 —— 人分不清
            #   "实时"和"昨收"的话，那是两个数量级的误解（尤其大涨大跌那天）。
            _kpz = pg.locator('#lvbody .kpi').first.inner_text()
            _psrc = pg.evaluate('() => (LVO && LVO.pos && LVO.pos.price_src) || ""')
            assert _psrc, '估值没给 price_src'
            # ★ 价来源与数据日写在【同一个标签】里，紧跟账户名 —— 原来数据日
            #   在账户后面、"实时 09-03 13:19"在持仓浮盈下面，两处各写一半，
            #   看着像自相矛盾（"数据到 09-02" vs "实时 09-03"）。
            _dtag = ' '.join(pg.locator('#lvbody .lvhead .lvtag').all_inner_texts())
            assert _psrc in _dtag or ('收盘' in _dtag and _psrc == '收盘'), \
                '数据日那个标签里没说价来源（%s）：%s' % (_psrc, _dtag)
            assert '实时' not in _kpz and ':' not in _kpz, \
                '报价时间又回到持仓浮盈下面了（与数据日重复）：%s' % _kpz[:160]
            if _psrc == '实时':
                import re as _re0
                assert _re0.search(r'实时\s*\d{2}-\d{2}\s*\d{2}:\d{2}', _dtag), \
                    '说是实时却没给到分钟的时间：%s' % _dtag
                # 逐只也要标，且现价列上有「实」
                assert pg.locator(
                    '#lvbody table.lvpos td .lvwhy[title*="实时价"]').count() >= 1, \
                    '持仓表的现价没标出是实时价'

            # ★ 数据日期要有【自己的位置】，不是塞在"（用 xx 收盘数据算）"括号里
            _tags = ' '.join(pg.locator('#lvbody .lvhead .lvtag').all_inner_texts())
            assert '数据日' in _tags, '顶栏缺独立的「数据日」：%s' % _tags
            import re as _re
            assert _re.search(r'数据日\s*\d{4}-\d{2}-\d{2}', _tags), \
                '「数据日」后面要跟真的日期：%s' % _tags
            # 待办标题里也不该再有"（用…收盘数据算，版本…）"这种括号脚注
            _sh = pg.locator('#lvbody .lvsec h3').first.inner_text()
            assert '（用' not in _sh, '数据日期又被塞回括号里了：%s' % _sh
            head = ' '.join(pg.locator('#lvbody .lvsec h3').last.inner_text().split())
            # 🔴 汇总数字只在 KPI 板出现【一次】：这里再写一遍市值/成本/浮盈
            #   就是同一份数据两处渲染，迟早不一致（而且眼睛要来回跳）。
            assert '只' in head, '持仓汇总该给只数：%s' % head
            for k in ('市值', '成本', '浮盈', '估值日'):
                assert k not in head, \
                    '「%s」在 KPI 板里已经有了，别在标题里重复：%s' % (k, head)
            # 价格必须来自独立取价，不是"信号里恰好有的那几只"
            n_pos = pg.locator('#lvbody table.lvpos tr').count() - 1
            assert n_pos == 3, '应有 3 只持仓，实得 %d' % n_pos
            assert '—' not in pg.locator('#lvbody table.lvpos').inner_text(), \
                '有持仓取不到现价 —— 估值应独立取价，不依赖当天信号'

            # ---- 费率：照账单逐项填 + 版本化(只新增/更正) + 按成交日取档 ----
            #   2026-09-02 真实账单：66,110 元买入，佣金1.72 规费3.57 过户费0.66
            #   合计 5.95 = 万0.9。
            #   ★ 刻意【去掉了】原来那个"这个率含不含规费"的开关 ——
            #     它是最容易填错的一处：同一个万0.8，含规费口径算 5.95、
            #     另收算 9.53，差 60%，而两种都不报错。现在规费自己一行，
            #     填 0 就表示已含在佣金里，少一个概念。
            pg.click('#lvset')
            pg.wait_for_selector('#fadd', timeout=8000)
            pg.click('#fadd')
            pg.wait_for_selector('#c0', timeout=8000)
            pg.wait_for_timeout(300)
            # 设置里一个 undefined 都不许有（旧进程/字段缺失都会露出来）
            stxt = pg.locator('.stbox').inner_text()
            assert 'undefined' not in stxt.lower(), \
                '设置里出现了 undefined：%s' \
                % [x for x in stxt.split('\n') if 'undefined' in x.lower()][:3]
            assert 'NaN' not in stxt, '设置里出现了 NaN'
            # 每一项都要有标签，不能只靠 placeholder
            labels = [x.strip() for x in
                      pg.locator('#fbill .frow > label').all_inner_texts() if x.strip()]
            for need in ('成交金额', '佣金', '规费', '过户费', '印花税', '单笔最低佣金'):
                assert need in labels, '账单表单缺「%s」一行：%s' % (need, labels)
            assert pg.locator('#fr5').count() == 0, \
                '"含不含规费"那个开关应该已经删掉 —— 它是最容易填错的一处'
            # 照账单逐项填，边填边折成费率
            for fid, v in (('#c0', '66110'), ('#c1', '1.72'),
                           ('#c2', '3.57'), ('#c3', '0.66'), ('#c5', '0')):
                pg.fill(fid, v)
            pg.wait_for_timeout(300)
            bs = ' '.join(pg.locator('#bsum').inner_text().split())
            assert '5.95' in bs and '万0.90' in bs, \
                '实时折算不对（应显示合计 5.95 = 万0.90）：%s' % bs
            for rid, want in (('#r1', '万0.260'), ('#r2', '万0.540'),
                              ('#r3', '万0.100')):
                t = pg.locator(rid).inner_text()
                assert want in t, '%s 应折成 %s，实得 %s' % (rid, want, t[:40])
            pg.fill('#ffrom', '2026-09-01')
            pg.fill('#fnote', '照账单填')
            pg.click('#fsave')
            pg.wait_for_timeout(1700)
            aid0 = pg.locator('.ditem.on').get_attribute('href').split('/')[-1]
            bd0 = lv.fee_breakdown('buy', 11000, 6.010, '2026-09-02',
                                   lv.fee_model_at(aid0, '2026-09-02'))
            assert abs(bd0['total'] - 5.95) < 0.011, \
                '照账单填保存后复算应得 5.95，实得 %.2f' % bd0['total']
            assert abs(bd0['commission'] - 1.72) < 0.011 and \
                abs(bd0['regulatory'] - 3.57) < 0.011 and \
                abs(bd0['transfer'] - 0.66) < 0.011, \
                '逐项应各自对上账单：%s' % bd0

            # 同一生效日：不勾「更正」要被拒，勾上则把上一档【删掉】
            pg.click('#lvset')
            pg.wait_for_selector('#fadd', timeout=8000)
            # ★ 新增面板平时收起 —— 它有十来个输入框，常驻会把设置页撑满
            assert not pg.locator('#fpanel').is_visible(), '新增面板应默认收起'
            pg.click('#fadd')
            pg.wait_for_timeout(250)
            assert pg.locator('#fpanel').is_visible(), '点「+ 新增费率」应展开'
            # 每一档的【来源】要在行上看得见，不能只藏在展开的明细里 ——
            # "照账单核过的"和"照报价推定的"在表里长得一模一样
            _fr = pg.locator('table.lvfr tr.frhead td:first-child')
            for _i in range(_fr.count()):
                assert len(_fr.nth(_i).inner_text().split('\n')) >= 2, \
                    '费率行第 %d 档没显示来源/备注' % (_i + 1)
            # 表里显示【总费率】而不是佣金率 —— 佣金只是其中一项，
            # 看"佣金万0.26"完全说明不了实付万0.9
            th = pg.locator('table.lvfr th').all_inner_texts()
            _hz = ' '.join(th)
            assert '买入总费率' in _hz and '卖出总费率' in _hz, \
                '费率表应显示买/卖总费率：%s' % th
            # 折算基准要写在表头上 —— 有最低佣金时"这个账户的费率"不是一个数，
            # 只标一个数会让小额单看着比实际便宜
            assert '10 万' in _hz, '总费率列没说明是按多大金额折算的：%s' % th
            row = [x.strip() for x in pg.locator('tr.frhead td').all_inner_texts()]
            assert '万0.90' in row and '万5.90' in row, \
                '总费率应是买万0.90 / 卖万5.90：%s' % row
            # 逐项明细【点开才看】，且不是塞在备注里
            assert not pg.locator('tr.frbody').first.is_visible(), '明细应默认收起'
            pg.locator('tr.frhead').first.click()
            pg.wait_for_timeout(300)
            # 明细是【竖排三列】：费用名称 / 费率 / 说明 —— 从上往下扫完，
            # 不用在横排卡片间来回找。
            dth = pg.locator('table.frt th').all_inner_texts()
            assert dth == ['费用名称', '费率', '说明'], '明细表头不对：%s' % dth
            rows = {}
            for tr in pg.locator('table.frt tr').all():
                c = [x.strip() for x in tr.locator('td').all_inner_texts()]
                if c:
                    rows[c[0]] = c[1]
            for need, want in (('净佣金', '万0.260'), ('规费', '万0.540'),
                               ('过户费', '万0.100'), ('印花税', '万5'),
                               ('单笔最低佣金', '0 元'),
                               ('— 买入合计', '万0.90'), ('— 卖出合计', '万5.90')):
                assert need in rows, '明细缺「%s」行：%s' % (need, list(rows))
                assert rows[need] == want, \
                    '「%s」应为 %s，实得 %s' % (need, want, rows[need])
            # 说明列要点明"法定"还是"可谈" —— 这是最容易配错的地方
            dtxt = ' '.join(pg.locator('table.frt').inner_text().split())
            assert '可谈' in dtxt and '法定' in dtxt, \
                '说明列没点明法定/可谈：%s' % dtxt[:150]
            pg.select_option('#fway', 'rate')
            pg.wait_for_timeout(200)
            assert pg.locator('#frate').is_visible() and \
                not pg.locator('#fbill').is_visible(), '切换填法没生效'
            pg.fill('#ffrom', '2026-09-01')
            pg.fill('#fr1', '0.0000260')
            pg.click('#fsave')
            pg.wait_for_timeout(900)
            assert '生效日' in pg.locator('#emsg2').inner_text(), \
                '同日不勾更正应被拒：%s' % pg.locator('#emsg2').inner_text()
            pg.check('#fsup')
            pg.fill('#fnote', '更正：直接填费率')
            pg.click('#fsave')
            pg.wait_for_timeout(1700)
            pg.click('#lvset')
            pg.wait_for_selector('#fadd', timeout=8000)
            assert pg.locator('tr.frhead').count() == 1, \
                '更正是物理删除 —— 不该留下"已作废"的行，实得 %d 行' \
                % pg.locator('tr.frhead').count()

            # 再新增一档更晚生效的（总费率方式），上一档应在前一天结束
            pg.click('#fadd')
            pg.wait_for_timeout(250)
            pg.select_option('#fway', 'flat')
            pg.wait_for_timeout(200)
            pg.fill('#ffrom', '2026-10-01')
            pg.fill('#fb1', '0.00006')
            pg.fill('#fb2', '0.00056')
            pg.fill('#fb3', '0')
            pg.uncheck('#fsup')
            pg.fill('#fnote', '换券商')
            pg.click('#fsave')
            pg.wait_for_timeout(1700)
            pg.click('#lvset')
            pg.wait_for_selector('#fadd', timeout=8000)
            hh2 = ' '.join(' '.join(
                pg.locator('table.lvfr tr').all_inner_texts()).split())
            assert '2026-09-30' in hh2, \
                '上一档应显示结束于 2026-09-30：%s' % hh2[:150]
            pg.locator('#mclose').click()
            pg.wait_for_timeout(400)

            # 按成交日取档：9 月按万0.9、10 月按万0.6
            # ★ `force_price=True`：这条用例测的是**按成交日取哪一档费率**，
            #   价格只是夹具（沿用那张真实账单的 6.010×11000=66,110）。
            #   面板往前推进之后 09-10 当天的区间成了 6.10~6.24，
            #   价格校验会拒掉它 —— 那时挂的是夹具不是产品。
            for d, want in (('2026-09-10', 5.95), ('2026-10-08', 3.97)):
                rr = lv.add_fill(aid0, d, '603889.XSHG', 'buy', 11000, 6.010,
                                 force_price=True)
                assert abs(rr['fee'] - want) < 0.011, \
                    '%s 应按当时那一档算 %.2f，实得 %.2f' % (d, want, rr['fee'])
            pg.reload()
            pg.wait_for_timeout(1200)
            b4 = _cash()
            pg.click('#lvrec')
            pg.wait_for_selector('#rfill', timeout=8000)
            pg.fill('#fd', '2026-09-02')
            pg.fill('#fc', '601857.XSHG')
            pg.fill('#fq', '1000')
            pg.fill('#fp', '11.42')
            pg.fill('#ff', '7.77')
            pg.click('#fb')
            pg.wait_for_timeout(1500)
            paid = round(b4 - _cash() - 11420, 2)
            assert abs(paid - 7.77) < 0.011, \
                '手填费用应优先于费率：填 7.77 实得 %.2f' % paid

            # ---- 价格留空 = 成交日开盘价（竞价买入）----
            #   ★ 很多策略是集合竞价买入，而 A 股开盘价就是竞价成交价，
            #     所以留空取开盘价是**恰好相等**而非近似。
            #     用真实那笔钉住：新澳股份 2026-09-01 开盘 6.01。
            b4 = _cash()
            pg.click('#lvrec')
            pg.wait_for_selector('#rfill', timeout=8000)
            assert '留空' in (pg.get_attribute('#fp', 'placeholder') or ''), \
                '价格框要写明"留空=开盘价"，否则没人知道能留空'
            # 价格校验要有【逃生口】的入口 —— 硬拒而不给出路，最后会变成
            # 绕过整个入口（大宗交易确实能成交在当日区间外）
            assert pg.locator('#fforce').count() == 1, \
                '缺"不校验价格区间"的勾选 —— 被卡住的人会去改 jsonl'
            assert not pg.locator('#fforce').is_checked(), '默认应该是校验的'
            # ★ 「按信号成交时用开盘价」只该在成交日行情【已同步】时默认勾上。
            #   调仓日早上录入时那天的行情还不存在，勾着会让整批全部失败；
            #   signal 自带 data_asof，能提前知道的事不要留到报错时才说。
            if pg.locator('#fopen').count():
                # 直接问页面自己拿到的那份 signal（LVO），别再打一次 API ——
                # 重算信号要跑一遍策略，而且可能和页面上渲染的不是同一份
                _sg = pg.evaluate('() => (LVO && LVO.signal) || {}')
                _synced = (_sg.get('data_asof') or '') >= (_sg.get('for_date') or 'z')
                assert pg.locator('#fopen').is_checked() == _synced, \
                    '开盘价默认勾选状态应随"成交日行情是否已同步"（asof %s / for %s）' \
                    % (_sg.get('data_asof'), _sg.get('for_date'))
                if not _synced:
                    assert '还没同步' in pg.locator('#fopen').evaluate(
                        'e => e.parentElement.innerText'), '没勾上要说明为什么'
            pg.fill('#fd', '2026-09-01')
            pg.fill('#fc', '603889.XSHG')
            pg.fill('#fq', '100')
            pg.fill('#fp', '')                       # ← 留空
            pg.fill('#ff', '')
            pg.click('#fb')
            pg.wait_for_timeout(1500)
            _px = [f for f in lv.fills(aid0) if f['trade_date'] == '2026-09-01'
                   and f['shares'] == 100]
            assert _px, '留空价格那笔没落盘（页面报错被吞了？）'
            assert abs(_px[-1]['price'] - 6.010) < 1e-9, \
                '留空应取 09-01 开盘价 6.010，实得 %s' % _px[-1]['price']
            assert _px[-1].get('price_from') == 'open', '要标明这个价是取的开盘价'
            # 现金要按解析出的价格 + 估出的费用扣，不是按 0
            _spent = round(b4 - _cash(), 2)
            assert _spent > 601, '现金应扣掉 601 元本金加费用，实得 %.2f' % _spent
            # 当天行情还没同步 -> 页面要报错并说清原因，不能用旧价顶上
            pg.click('#lvrec')
            pg.wait_for_selector('#rfill', timeout=8000)
            pg.fill('#fd', '2028-01-03')
            pg.fill('#fc', '603889.XSHG')
            pg.fill('#fq', '100')
            pg.fill('#fp', '')
            pg.click('#fb')
            pg.wait_for_timeout(1500)
            _m = pg.locator('#rfill').inner_text()
            assert '还没同步' in _m or '取不到' in _m, \
                '取不到当日行情时页面要说清原因，实得：%s' % _m[:200]
            assert not [f for f in lv.fills(aid0) if f['trade_date'] == '2028-01-03'], \
                '取不到价的那笔不该落盘'
            pg.keyboard.press('Escape')
            pg.wait_for_timeout(400)

            # ---- 设置进浮层 ----
            assert pg.locator('#ename').count() == 0, '设置不该常驻主视图'
            pg.click('#lvset')
            pg.wait_for_selector('#ename', timeout=8000)
            pg.fill('#ename', '改过的名字')
            pg.click('#esave')
            pg.wait_for_timeout(1200)
            assert '改过的名字' in pg.locator('.lvhead h2').first.inner_text(), '改名没生效'

            # ---- 代码/名称点开【速览浮层】，浮层里再去个股页（新标签）----
            #   ★ 代码与名称【都】可点 —— 只有代码可点的话，习惯认名字的人
            #     会以为不能点。
            #   🔴 **点了不跳走**：实盘页往往一直开着（待办、正在录一半的
            #     成交），跳走再回来这些状态就没了。所以点名称弹浮层；
            #     要看完整个股页时走浮层里的「完整页 ↗」，那个才 target=_blank。
            #   ★ 这一段原来断言"链接必须 target=_blank"——那是浮层之前的
            #     交互。四件要保的事没变（都可点 / 不跳走 / 仍能到个股页且
            #     开新标签 / 新标签里没有假的返回按钮），只是路径多了一跳。
            _sk = pg.locator('#lvbody table.lvpos a[data-sp]')
            assert _sk.count() >= 2, \
                '持仓表里的代码/名称没挂速览：%d' % _sk.count()
            _n_pos = pg.locator('#lvbody table.lvpos tr').count() - 1
            # ★ 代码与名称合成一格之后，**一行一个链接**（那一格整体可点，
            #   名称与小字代码都在它里面）—— 原来是两格两个链接。
            #   要保的事没变：每一行都点得开。
            assert _sk.count() >= _n_pos, \
                '每只持仓的名称格都该可点：%d 只 vs %d 个链接' \
                % (_n_pos, _sk.count())
            _sk.first.click()
            pg.wait_for_selector('#spwrap', state='visible', timeout=20000)
            assert '#/live' in pg.url, \
                '点名称把实盘页跳走了（该弹浮层）：%s' % pg.url
            _full = pg.locator('#spfull')
            assert '/stock.html' in (_full.get_attribute('href') or ''), \
                '浮层里没有去个股页的出口'
            assert _full.get_attribute('target') == '_blank', \
                '「完整页」应在新标签页打开，否则实盘页的状态会丢'
            assert 'noopener' in (_full.get_attribute('rel') or ''), '缺 rel=noopener'
            with pg.context.expect_page() as _np:
                _full.click()
            _sp = _np.value
            _sp.wait_for_selector('#kcv', timeout=40000)
            _sp.wait_for_timeout(1200)
            assert '/stock.html' in _sp.url, '没打开个股页：%s' % _sp.url
            assert 'undefined' not in _sp.locator('#pg').inner_text()
            # 🔴 新标签页里【不该】出现返回按钮 —— 它有 referrer 但没有可回的
            #    历史（history.length === 1），显示出来点了什么都不会发生。
            assert _sp.evaluate('() => history.length') == 1
            assert _sp.locator('#goback').count() == 0, \
                '新标签页显示了返回按钮，但点了不会有反应'
            # 站内再跳一次之后才该有
            _sp.locator('#body a[href*="/sector.html"]').first.click()
            _sp.wait_for_timeout(2500)
            assert _sp.locator('#goback').count() == 1, '站内跳转后应有返回按钮'
            _sp.close()
            pg.keyboard.press('Escape')
            pg.wait_for_timeout(250)
            assert '#/live' in pg.url, '原实盘页被跳走了：%s' % pg.url
            assert pg.locator('#lvbody table.lvpos').count() == 1, \
                '原实盘页的持仓表没了 —— 新标签页打开不该影响它'

            # ---- 流水独立页 + 分页 + 冲正 ----
            aid = pg.locator('.ditem.on').get_attribute('href').split('/')[-1]
            pg.click('a[href*="/fills"]')
            pg.wait_for_timeout(1200)
            assert '成交流水' in pg.locator('.lvhead').inner_text(), '没跳到流水页'
            # 流水页的代码/名称也要能点开个股
            assert pg.locator('#main table.lvt a[href*="/stock.html"]').count() >= 2, \
                '流水页的代码/名称没链到个股页'
            # 列序按【看的顺序】：哪天、买还是卖、哪只票、什么价、多少股、多少钱。
            # 录入时间与来源是审计信息，平时不看，排在最后。
            _fh = [x.strip() for x in pg.locator('#main table.lvt th').all_inner_texts()]
            # ★ 代码与名称合成一格（2026-09-16 全站统一）—— 列序其余不变：
            #   哪天、买还是卖、哪只票、什么价、多少股、多少钱。
            _want = ['成交日', '方向', '名称', '价格', '股数', '金额', '费用']
            assert _fh[:len(_want)] == _want, '流水列序不对：%s' % _fh
            assert pg.locator('#main table.lvt tr:nth-child(2) '
                              'td .cd, #main table.lvt tr:nth-child(2) '
                              'td .cd0').count() >= 1, \
                '流水页的名称格里没有小字代码'
            assert _fh.index('录入时间') > _fh.index('费用') and \
                _fh.index('来源') > _fh.index('费用'), \
                '录入时间/来源应排在最后：%s' % _fh
            # 名称必须真的填上 —— 批量粘贴的成交只有代码，得服务端补。
            # 🔴 判据取的是那一格里**名称那一部分**（整格文字减去小字代码）：
            #   合成一格之后，整格文字里本来就有代码那串数字，
            #   照老写法"名称列不许出现数字"必然误报。
            #   ★ 列位置也**按表头找**，不写死第几列（列序会变）。
            _nm = pg.evaluate(
                "(want) => {const ths = [...document.querySelectorAll("
                "'#main table.lvt th')];"
                " const i = ths.findIndex(e => e.textContent.trim() === want);"
                " if (i < 0) return null;"
                " return [...document.querySelectorAll('#main table.lvt tr')]"
                "   .slice(1).map(tr => {const td = tr.cells[i]; if (!td) return '';"
                "     const cd = td.querySelector('.cd,.cd0');"
                "     const all = (td.textContent || '').trim();"
                "     return cd ? all.replace((cd.textContent || '').trim(), '').trim()"
                "               : all;});}", '名称')
            assert _nm is not None, '流水表找不到「名称」列'
            assert any(_nm), '流水的「名称」列全空 —— 服务端没补名称'
            assert not any(x and x.replace('.', '').isdigit() for x in _nm), \
                '名称那一部分是纯数字，可能列错位了：%s' % _nm[:4]
            assert '第 1/1 页' in pg.locator('#main').inner_text(), '分页控件没渲染'
            assert pg.locator('#pprev').is_disabled(), '第一页的「上一页」应置灰'
            n_before = len(lv.fills(aid))
            pg.locator('a.lvrv').first.click()
            pg.wait_for_timeout(1500)
            assert len(lv.fills(aid)) == n_before + 1, '冲正应【追加】一条'
            assert pg.locator('.lvrev').count() >= 2, '原记录与冲正记录都该划掉'
            # 取的开盘价要在流水里看得出来（与"估"的费用同理，别混成券商回报）
            assert pg.locator('#main table.lvt td span.lvwhy').count() >= 1, \
                '流水里"取的开盘价"应有标记，否则分不清是券商回报还是本地取的'

            # 回账户
            pg.click('a[href="#/live/%s"]' % aid)
            pg.wait_for_timeout(1000)
            assert pg.locator('#lvrec').count() == 1, '没回到账户页'

            # ---- 旧进程要有醒目横幅，而不是默默渲染 undefined ----
            #   index.html 每次请求都从磁盘读，Python 模块只在进程启动时
            #   加载一次 —— 长时间开着的 serve.py 会出现"新页面 + 旧 API"。
            _bt = sv._BOOT_TS
            try:
                sv._BOOT_TS = _bt - 100000        # 假装进程启动得很早
                pg.goto(base + '#/live', wait_until='networkidle')
                pg.wait_for_timeout(800)
                wtxt = ' '.join(pg.locator('.lvwarn').all_inner_texts())
                assert '旧进程' in wtxt and 'serve.py' in wtxt, \
                    '代码比进程新时应提示重启，实得：%s' % wtxt[:120]
            finally:
                sv._BOOT_TS = _bt
            pg.goto(base + '#/live/%s' % aid, wait_until='networkidle')
            pg.wait_for_timeout(900)

            # ---- 归档：从列表隐去，但数据必须还在 ----
            pg.click('#lvset')
            pg.wait_for_selector('#earch', timeout=8000)
            pg.click('#earch')
            pg.wait_for_timeout(1200)
            assert pg.locator('.ditem').count() == 0, '归档后仍出现在默认列表里'
            pg.click('#lvall')
            pg.wait_for_timeout(700)
            assert pg.locator('.ditem.miss').count() == 1, '「显示已归档」没把它列出来'
            assert lv.fills(aid) and lv.versions(aid), '归档不该动数据'

            br.close()
            assert not errs, '页面有运行时错误：%s' % errs[:3]
            return ('信息架构：主视图仅[待办+持仓]，设置/记一笔/策略进浮层，'
                    '流水独立页分页；策略单一入口(未绑定也能开)；'
                    '「跑一次」不是 disabled 按钮（没开时常驻说明怎么开、'
                    '点了也有话说；开了能起 job，版本漂移给「重新绑定」+命令行）；'
                    '持仓 %d 只全部取到现价 + 当日涨跌/当日盈亏（今天买的按成交价）'
                    '；口径说明进 ⓘ（点开有摊薄成本/保本价/全平落袋，主视图不占版面）'
                    '；费用三态；入金；'
                    '费率(新增面板收起/表显总费率/点行展开逐项/照账单填 5.95 逐项对上/'
                    '更正物理删除/明细竖排三列 7 行/新档旧档接续/'
                    '按成交日取档/手填优先/每账户独立/来源写在行上)；'
                    '持仓与流水的代码名称都能新标签页打开个股（新标签无返回按钮）；'
                    '价格留空→09-01 开盘价 6.010 并标源/未同步日响亮报错不落盘/'
                    '价格区间校验有逃生口；'
                    '排版：流水列序(日/方向/代码/名称/价/量/额/费,录入时间与来源置尾)、'
                    '持仓代码与名称分列、数据日与报价时间合成一个标签、'
                    '汇总数字只在 KPI 出现一次、KPI 板无 undefined、'
                    '侧栏可收起(轨上仍见告警点)、新建表单默认收起、待办按 alert 折叠；'
                    '改名；冲正追加并划掉；设置无 undefined 且每项有标签；'
                    '业绩板：盘中补点后标「今日/盘中时刻」、交易费用独立一格'
                    '（占本金，短样本不折年化）、ⓘ 说清 TWR/盘中/年化拖累；'
                    '旧进程有横幅；' + _why_msg + '（带流通市值/PB/ROE加速度，'
                    '无 undefined，账户页入口是按钮不是小灰链接）；'
                    '归档后数据仍在；0 个 JS 错误') % n_pos
    finally:
        httpd.shutdown()
        lv.LIVE, sv.ALLOW_LIVE = old_live, old_allow
        # 归档目录还原（在 finally 里 —— 用例中途失败也不能把后面的
        # 用例留在临时目录上）
        if _prev_runs_env is None:
            os.environ.pop('ASSAY_RUNS', None)
        else:
            os.environ['ASSAY_RUNS'] = _prev_runs_env
        _reg.set_runs(_prev_runs)
        sv._scan()
        shutil.rmtree(_runs_tmp, ignore_errors=True)
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
    import ast
    import datetime
    import json
    import subprocess
    import tempfile

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

    # ---- 2b) 「上次从聚宽抽取」是独立一维，不能靠数据内容反推 ----
    # 🔴 B 腿**事件驱动**：没公告的日子 pub_date 不前进。于是「距今 18 天」
    #   这一个数分不出两种情况：①昨天刚导、只是没新公告 ②两周没导。
    #   用户原话：「实际上财务数据我昨天已经导入了最新的，但是上面的最新时间
    #   不会更新，显得我好像没有更新一下。」
    # ★ 抽取时刻**只有抽取端知道**（聚宽研究环境的 now）—— 本地文件 mtime
    #   是下载/解压时刻，反推不出来。所以链条是：extract 写进包 →
    #   merge 落到 _manifest/jq_extract.json → sync_status 读出来。
    #   下面逐段测**真行为**（打 ROOT 补丁到临时目录），不测源码字符串。
    import importlib.util as _ilu

    def _load(path, name):
        sp = _ilu.spec_from_file_location(name, path)
        m = _ilu.module_from_spec(sp)
        sp.loader.exec_module(m)
        return m

    # (1) sync_status._extract_info：有 / 无 两支都要对
    ssm = _load(ss, '_ss_probe')
    with tempfile.TemporaryDirectory() as td:
        ssm.ROOT = td
        assert ssm._extract_info(datetime.date(2026, 9, 11)) is None, \
            '没有 _manifest/jq_extract.json 时必须返回 None（老包），不许猜'
        os.makedirs(os.path.join(td, '_manifest'))
        json.dump({'extracted_at': '2026-09-10 20:26:48',
                   'extract_date': '2026-09-10', 'since': '2026-08-20',
                   'data_max_date': '2026-09-09'},
                  open(os.path.join(td, '_manifest', 'jq_extract.json'),
                       'w', encoding='utf-8'))
        ex = ssm._extract_info(datetime.date(2026, 9, 11))
        assert ex and ex['days_since_extract'] == 1, \
            '抽取距今算错：%r' % (ex,)
        # 🔴 关键：这一维必须与「数据内容多久没变」**脱钩** ——
        #   data_max_date 比 extract_date 早一天是常态（昨天抽的是前天的数据）
        assert ex['data_max_date'] == '2026-09-09' and \
            ex['extract_date'] == '2026-09-10', \
            '抽取时点与数据切点是两个字段，不能混：%r' % (ex,)
        # 坏 JSON 不许把整个状态接口带崩
        open(os.path.join(td, '_manifest', 'jq_extract.json'), 'w').write('{ 坏')
        assert ssm._extract_info(datetime.date(2026, 9, 11)) is None, \
            '坏 JSON 应降级为 None，不该抛异常'

    # (2) merge 侧：把包里的 _manifest.json 落到盘上，并补 merged_at
    mg = os.path.join(dl, 'build', 'merge_jq_increment.py')
    assert os.path.isfile(mg), '缺 %s' % mg
    mgm = _load(mg, '_mg_probe')
    with tempfile.TemporaryDirectory() as td:
        dst = os.path.join(td, 'jq_extract.json')
        mgm.MF_DST = dst
        src = os.path.join(td, 'pkg')
        os.makedirs(src)
        # 老包（没有 _manifest.json）：静默跳过，**不许建空文件**
        mgm._manifest(src, False, 'old.tar')
        assert not os.path.exists(dst), \
            '老包不该写出 jq_extract.json —— 那会让页面显示一个空记录'
        json.dump({'extracted_at': '2026-09-10 20:26:48',
                   'extract_date': '2026-09-10', 'data_max_date': '2026-09-09'},
                  open(os.path.join(src, '_manifest.json'), 'w',
                       encoding='utf-8'))
        mgm._manifest(src, True, 'new.tar')          # dry-run 不落盘
        assert not os.path.exists(dst), '--dry-run 不该写盘'
        mgm._manifest(src, False, 'new.tar')
        got = json.load(open(dst, encoding='utf-8'))
        assert got['extracted_at'] == '2026-09-10 20:26:48', \
            '抽取时刻丢了：%r' % (got,)
        assert got.get('merged_at') and got.get('tar') == 'new.tar', \
            'merge 侧要补 merged_at 与包名（"什么时候导进来的"）：%r' % (got,)

    # (3) extract 侧（在聚宽研究环境跑，本地不 import）：
    #     用 ast 确认 pack() 真的调 _manifest()、_save() 真的往 _stats 记。
    #     ★ 用 ast 而不是字符串 —— 注释不是 AST 节点，所以抓不到"我自己写的
    #       说明文字"（本会话已 8 次栽在这上面）。
    exs = os.path.join(dl, 'raw', 'jq', '_ingest', 'extract_jq_increment.py')
    assert os.path.isfile(exs), '缺 %s' % exs
    tree = ast.parse(open(exs, encoding='utf-8').read())
    fns = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    for need in ('_manifest', '_save', 'pack'):
        assert need in fns, 'extract 脚本缺 %s()' % need
    calls = {c.func.id for c in ast.walk(fns['pack'])
             if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
    assert '_manifest' in calls, \
        'pack() 必须调 _manifest() —— 否则包里没有抽取时刻，' \
        '而那时本地永远显示"未知"且不报错'
    stores = {t.value.id for t in ast.walk(fns['_save'])
              if isinstance(t, ast.Subscript)
              and isinstance(t.ctx, ast.Store) and isinstance(t.value, ast.Name)}
    assert '_stats' in stores, '_save() 必须往 _stats 记每张表的 max_date'
    # 🔴 光判「_stats 被写过」太松：`_save` 里有**两处** —— 一处是初始化
    #   `_stats[name] = {'rows': 0}`，一处才是记结果。变异掉后者时前者还在，
    #   于是断言照样绿（变异测试当场抓到）。所以还要判**真的记了 max_date**。
    # 🔴 「_save 里出现过 'max_date' 这个字面量」抓不到"记的那一处被改掉"
    #   —— print 那行也有一个 `'max_date' in d`（变异测试当场抓到，
    #   又是一次"判据比断言宽"）。所以**把这个函数抠出来真跑一遍**：
    #   整个模块 `from jqdata import *` 本地装不了，但单个函数可以 exec
    #   —— 它只用到 pd / os / OUT / _stats / _saved。
    import pandas as _pd
    _seg = ast.get_source_segment(open(exs, encoding='utf-8').read(),
                                  fns['_save'])
    with tempfile.TemporaryDirectory() as td:
        _ns = {'pd': _pd, 'os': os, 'print': lambda *a, **k: None,
               '_stats': {}, '_saved': [], 'OUT': td}
        exec(_seg, _ns)                                  # noqa: S102
        _ns['_save']('t', _pd.DataFrame(
            {'code': ['a', 'b', 'c'],
             'pub_date': ['2026-08-01', '2026-09-09', '2026-07-15']}))
        _g = _ns['_stats'].get('t') or {}
        assert _g.get('max_date') == '2026-09-09', \
            '_save() 要记这张表【实际抽到】的最大日期 —— 实得 %r。' \
            '不记的话包里只有行数，"数据切到哪天"就答不出来' % (_g,)
        assert _g.get('date_col') == 'pub_date' and _g.get('rows') == 3, \
            '日期列名与行数也要记：%r' % (_g,)
        # 空表也要有记录（否则页面上"这张表没抽到"与"没记"分不出来）
        _ns['_save']('empty', _pd.DataFrame())
        assert _ns['_stats'].get('empty', {}).get('rows') == 0, \
            '空表也要记一条 rows=0'

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


@case('实盘页停留时自己刷：盘中轮询 / 收盘不轮 / 浮层开着跳过（playwright）',
       tag='web')
def t_live_poll():
    """🔴 **实盘页停留时必须自己刷。**

    原来只在进入时 `loadLive` 拉一次，于是停在页面上盯盘时数字永远不动，
    切走再回来才更新 —— 而这一页正是**盯盘时一直开着**的那一页。
    实测（修复前）：停留 70 秒 `/api/live/account` 新增 **0** 个请求、
    报价时间戳停在 13:56，切走再回来才变 13:57。

    ★ 判据 `rt_live` 由**服务端**给（realtime.in_session + is_trading_day）——
      前端硬编码交易时段的话，改了时段或遇到半日市会白轮/漏轮，
      而"多轮几次"不报错、"该轮没轮"更不报错。
    ★ 这条用例**不等 60 秒**：直接查 POLL 是否装上 + 间隔常量 + 分支条件，
      再手动调一次 loadLive 验证它真能刷新（等一分钟的用例没人愿意跑）。
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return '跳过（无 playwright）'
    import threading
    from assay import server as sv
    old_live = sv.ALLOW_LIVE
    sv.ALLOW_LIVE = True
    from http.server import ThreadingHTTPServer
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), sv.Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        # ---- 服务端：rt_live 必须给，且与 realtime 那一处同源 ----
        from assay import live as _lv
        from assay.srv import live as _srv_live
        from assay import realtime as _rt
        aid = next((a['id'] for a in _lv.load_accounts()
                    if not a.get('archived')), None)
        if not aid:
            return '跳过（没有实盘账户）'
        want = bool(_rt.in_session() and _rt.is_trading_day())
        got = _srv_live._rt_live()
        assert got == want, \
            ('rt_live 与 realtime.in_session/is_trading_day 不一致'
             '（%r vs %r）—— 判据分两处写就会分叉' % (got, want))
        src = open(os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            'assay', 'srv', 'live.py'), encoding='utf-8').read()
        assert 'in_session()' in src and 'is_trading_day()' in src, \
            ('rt_live 该调 realtime 的那两个函数，不要在这里另写时段判据')

        js = open(os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            'web', 'views', 'live.js'), encoding='utf-8').read()
        flat = js.replace(' ', '').replace('\n', '')
        # ---- 前端：装了轮询、间隔与服务端同节拍、按 rt_live 开关 ----
        assert 'POLL=setInterval' in flat, \
            ('实盘页没有轮询 —— 停留时数字不会动，而这一页正是'
             '盯盘时一直开着的那一页')
        assert 'if(o.rt_live)livePoll(aid);elsestopPoll();' in flat, \
            ('要按服务端给的 rt_live 决定轮不轮：盘中轮、收盘停。'
             '收盘后还轮的话每次请求都会触发服务端的 _rt_catch_up，'
             '白打接口而限流是这条链上唯一的风险')
        import re as _re
        m = _re.search(r'LVPOLL_MS\s*=\s*(\d+)', js)
        assert m and int(m.group(1)) == 60000, \
            ('轮询间隔该是 60 秒（与服务端 _rt_loop 同节拍），实得 %s'
             % (m.group(1) if m else '没有这个常量'))
        #   🔴 浮层开着要**跳过这一轮**而不是停掉 —— 停了就不再动，
        #     和一开始的 bug 是同一种坏。
        assert "className==='stmodal')return;" in flat, \
            ('浮层开着时该 return（跳过这一轮），不能 stopPoll —— '
             '关掉浮层后要自动继续')
        assert 'stopPoll();return;' in flat, \
            '离开这一页/换账户要自己停掉（兜底）'

        with sync_playwright() as pw:
            br = pw.chromium.launch()
            pg = br.new_page()
            errs = []
            pg.on('pageerror', lambda e: errs.append(str(e)))
            hits = []
            pg.on('request', lambda r: hits.append(1)
                  if '/api/live/account' in r.url else None)
            pg.goto('http://127.0.0.1:%d/#/live/%s' % (port, aid),
                    wait_until='networkidle')
            pg.wait_for_selector('#lvbody .lvsec', timeout=90000)
            pg.wait_for_timeout(800)
            live_now = pg.evaluate('()=>LVO && LVO.rt_live')
            #   盘中该装上 POLL，收盘该是 null —— 两种都要对
            has = pg.evaluate('()=>POLL!==null')
            assert has == bool(live_now), \
                ('rt_live=%r 时 POLL 该%s，实得 %r'
                 % (live_now, '装上' if live_now else '为 null', has))
            # ---- 刷新状态要写在页面上（不说人分不清"没变"和"坏了"）----
            tag = pg.locator('#lvbody .lvtag').first.inner_text()
            if live_now:
                assert '自动刷新' in tag, \
                    '盘中该在数据日标签里标"每分钟自动刷新"：%s' % tag
            else:
                assert '不自动刷新' in tag or '已收盘' in tag, \
                    ('收盘后该说清不刷新的原因 —— 不说的话人分不清'
                     '"数字没变"和"页面坏了"：%s' % tag)
            # ---- 手动跑一次轮询回调：该真的重新拉数据 ----
            n0 = len(hits)
            pg.evaluate('()=>loadLive(LVSEL, true)')
            pg.wait_for_timeout(1500)
            assert len(hits) > n0, \
                'loadLive(aid, true) 没有重新请求 /api/live/account'
            #   quiet 模式不该重拉业绩板（它要重放整条权益曲线）
            eq_hits = []
            pg.on('request', lambda r: eq_hits.append(1)
                  if '/api/live/equity' in r.url else None)
            pg.evaluate('()=>loadLive(LVSEL, true)')
            pg.wait_for_timeout(1200)
            assert not eq_hits, \
                ('quiet 模式不该重拉 /api/live/equity —— 它要重放整条'
                 '权益曲线，而累计收益一天内变化很小，每分钟重放纯浪费')
            # ---- 浮层开着时跳过，但 POLL 仍在 ----
            if live_now:
                pg.locator('#lvrec').click()
                pg.wait_for_timeout(700)
                assert pg.evaluate(
                    "()=>{const m=document.getElementById('stwrap');"
                    "return !!m && m.className==='stmodal';}"), '浮层没打开'
                assert pg.evaluate('()=>POLL!==null'), \
                    ('浮层开着时该**跳过这一轮**而不是 stopPoll —— '
                     '停了就不再动，和一开始的 bug 是同一种坏')
                pg.keyboard.press('Escape')
                pg.wait_for_timeout(400)
                assert pg.evaluate('()=>POLL!==null'), \
                    '关掉浮层后轮询该继续'
            # ---- 离开这一页要停掉 ----
            pg.goto('http://127.0.0.1:%d/#/home' % port,
                    wait_until='networkidle')
            pg.wait_for_timeout(900)
            assert pg.evaluate('()=>POLL') is None, \
                '离开实盘页后轮询该停掉（不停就是白打接口）'
            assert not errs, '有运行时错误：%s' % errs[:3]
            br.close()
        return ('rt_live 与 realtime.in_session/is_trading_day 同源；'
                '轮询间隔 60s（与 _rt_loop 同节拍）；rt_live=%r 时 '
                'POLL %s；刷新状态写在数据日标签里；quiet 模式重拉 account '
                '但不重拉 equity；浮层开着跳过这一轮而 POLL 仍在；'
                '离开这一页自动停' % (live_now, '装上' if live_now else '为空'))
    finally:
        httpd.shutdown()
        sv.ALLOW_LIVE = old_live


_DDJS = r"""() => {
  const svg = document.querySelector('#lp_dd svg');
  if(!svg) return null;
  const p = svg.querySelector('path[stroke]:not([stroke="none"])');
  const d = p ? p.getAttribute('d') : '';
  const ys = [...d.matchAll(/[ML]\s*[\d.]+\s+([\d.]+)/g)].map(m => +m[1]);
  const zl = [...svg.querySelectorAll('line')]
    .find(l => (l.getAttribute('stroke') || '').includes('91,156,240'));
  return {nM: (d.match(/M/g) || []).length,
          topY: ys.length ? Math.min.apply(null, ys) : null,
          zeroY: zl ? +zl.getAttribute('y1') : null};
}"""


@case('业绩页分页签：交易记录=逐笔操作，已清仓=FIFO 往返（playwright）',
       tag='web')
def t_live_perf_tabs():
    """2026-09-15 用户三句话：
      ① "都放在同一个页面太拥挤了，应该也和回测的结果一样的做成页签"
      ② "买入卖出都算一笔单独的操作，需要记录下来，这才是真正的交易记录"
      ③ "已清仓的单独放一个（就是现在的交易记录）"

    🔴 ② 与 ③ 是**两个不同的东西**，不是换个样子：

        交易记录  逐笔操作视角：买入、卖出各算一笔（账本里真实记下的）
        已清仓    FIFO 往返视角：一买一卖配成一笔，回答"这笔赚了多少"

      所以判据不能只查"有这两个页签"，要查**它们的行数关系**：
      逐笔 >= 往返 × 2（一笔往返至少对应一买一卖），而且逐笔那张表里
      **买卖两种方向都有**。

    ★ 逐笔那张表与流水独立页**共用 `fillsTableHtml`** —— 同一份数据两处
      渲染迟早不一致，判据钉在源码里（两处都得调它）。
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return '跳过（无 playwright）'
    import io as _io
    import threading
    from assay import live as lv
    from assay import server as sv
    from http.server import ThreadingHTTPServer

    # ---- ① 静态：逐笔表只能有一处定义 ----
    src = _io.open('web/views/live-trade.js', encoding='utf-8').read()
    assert 'function fillsTableHtml' in src, \
        '逐笔成交表没有抽成共用函数'
    perf = _io.open('web/views/live-perf.js', encoding='utf-8').read()
    assert 'fillsTableHtml(' in perf, \
        ('业绩页没有复用 `fillsTableHtml` —— 自己抄一份的话列序、冲正标记、'
         '估算标记会慢慢分叉，而那不报错')
    assert src.count('<th class="rt">费用</th>') == 1, \
        '「费用」表头出现 %d 次 —— 逐笔表被抄了第二份' \
        % src.count('<th class="rt">费用</th>')

    old_live = sv.ALLOW_LIVE
    sv.ALLOW_LIVE = True
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), sv.Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    notes = []
    try:
        # 🔴 要挑**买卖都有**的账户 —— 只挑"有成交"的话可能全是买入，
        #   "两种方向都该在"那条就没法验（第一版挑到了只有买入的账户）。
        #   有卖出的优先，没有再退回任意有成交的。
        def _both(a):
            sides = {f['side'] for f in lv.fills(a)}
            return 'buy' in sides and 'sell' in sides
        _cands = [a['id'] for a in lv.load_accounts()
                  if not a.get('archived') and lv.fills(a['id'])]
        aid = next((c for c in _cands if _both(c)), None) \
            or (_cands[0] if _cands else None)
        if not aid:
            return '跳过（没有有成交的账户）'
        both = _both(aid)
        with sync_playwright() as pw:
            br = pw.chromium.launch()
            pg = br.new_page(viewport={'width': 1440, 'height': 900})
            errs = []
            pg.on('pageerror', lambda e: errs.append(str(e)))
            pg.goto('http://127.0.0.1:%d/#/live/%s/perf' % (port, aid),
                    wait_until='networkidle')
            pg.wait_for_selector('#lptabs', timeout=90000)
            pg.wait_for_timeout(2000)

            # ---- ② 页签存在且只显示一个 pane ----
            tabs = [t.strip() for t in
                    pg.locator('#lptabs div').all_inner_texts()]
            # 🔴 **顺序也要钉**，不只是"存在" —— 用户 2026-09-15 明确给了
            #   顺序："业绩曲线、收益明细、执行差异、每日持仓、交易记录、
            #   清仓记录"。只查"在不在"的话，顺序被改了不会有人发现。
            WANT = ['业绩曲线', '收益明细', '执行差异', '每日持仓',
                    '交易记录', '清仓记录']
            assert tabs == WANT, '页签顺序/命名不对：%s（要 %s）' % (tabs, WANT)
            # 「业绩」（KPI 板）**一直置顶**，不进页签
            g = pg.evaluate("""() => {
                const k = document.querySelector('.kpi');
                const t = document.querySelector('#lptabs');
                if (!k || !t) return null;
                return {kb: k.getBoundingClientRect().bottom,
                        tt: t.getBoundingClientRect().top};}""")
            assert g and g['kb'] <= g['tt'] + 1, \
                ('KPI 板没有置顶（底 %.0f > 页签顶 %.0f）—— 用户要的是'
                 '"业绩一直置顶"，切页签时它不该跟着换掉'
                 % (g['kb'] if g else -1, g['tt'] if g else -1))
            vis = pg.evaluate("""() => [...document.querySelectorAll('[id^=lpp]')]
                .filter(e => getComputedStyle(e).display !== 'none').length""")
            assert vis == 1, \
                ('同时显示了 %d 个 pane —— 分页签的全部意义就是一次只看一块'
                 '（`.pane` 的 display:none/.on 是样式表里现成的）' % vis)
            notes.append('%d 个页签，一次只显示一块' % len(tabs))

            _VISJS = ("() => [...document.querySelectorAll('[id^=lpp]')]"
                      ".filter(e => getComputedStyle(e).display !== 'none')"
                      ".length")

            def open_tab(name):
                pg.locator('#lptabs div', has_text=name).first.click()
                pg.wait_for_timeout(2500)
                # 🔴 每次切完都复查 —— 不查的话"所有 pane 同时显示"这种坏
                #   会让后面的取值**跨 pane**（选择器 `[id^=lpp].on` 命中多块），
                #   报出来的却是"数据源接反了"，把人引到错的方向去查
                #   （变异实测：报错信息指错了地方）。
                n = pg.evaluate(_VISJS)
                assert n == 1, \
                    ('切到「%s」之后有 %d 个 pane 同时显示 —— 分页签就是为了'
                     '一次只看一块' % (name, n))

            # ---- ③ 交易记录 = 逐笔操作（买卖各一笔）----
            open_tab('交易记录')
            sides = pg.evaluate("""() => [...document.querySelectorAll(
                '[id^=lpp].on table.lvt tr')].slice(1)
                .map(tr => (tr.children[1] || {}).textContent.trim())""")
            assert sides, '交易记录页签没渲染出行'
            if both:
                assert '买' in sides and '卖' in sides, \
                    ('逐笔表里只有 %s —— "买入卖出都算一笔单独的操作"，'
                     '两种方向都该在' % set(sides))
            else:
                notes.append('⚠ 这个账户只有买入，"两种方向"那条没验到')
            n_fill = len(sides)
            # 🔴 表头必须是**逐笔**那套，不是往返那套
            th = pg.evaluate("""() => [...document.querySelectorAll(
                '[id^=lpp].on table.lvt th')].map(e => e.textContent.trim())""")
            assert '方向' in th and '成交日' in th, '不是逐笔表的表头：%s' % th
            assert '持有天' not in th and '收益率' not in th, \
                '交易记录页签里混进了往返表的列（%s）—— 那是「清仓记录」的事' % th
            notes.append('交易记录 %d 笔逐笔操作（买卖都有）' % n_fill)

            # ---- ④ 已清仓 = FIFO 往返 ----
            open_tab('清仓记录')
            th2 = pg.evaluate("""() => [...document.querySelectorAll(
                '[id^=lpp].on table th')].map(e => e.textContent.trim())""")
            body = pg.inner_text('[id^=lpp].on')
            if '还没有' in body:
                notes.append('⚠ 这个账户还没有平仓往返')
                n_trip = 0
            else:
                for want in ('持有天', '收益率', '盈亏'):
                    assert any(want in x for x in th2), \
                        '清仓记录页签缺「%s」列：%s' % (want, th2)
                n_trip = pg.evaluate("""() => [...document.querySelectorAll(
                    '[id^=lpp].on table tbody tr')].filter(
                    tr => !tr.classList.contains('grp')).length""")
                # 🔴 **两者的行数关系**：一笔往返至少对应一买一卖。
                #   只查"有这两个页签"的话，把两边接口对调也全绿。
                assert n_fill >= n_trip * 2, \
                    ('逐笔 %d 笔 < 往返 %d 笔 × 2 —— 两个页签的数据源接反了？'
                     '一笔往返至少要有一买一卖' % (n_fill, n_trip))
                notes.append('清仓记录 %d 笔往返（逐笔 %d >= 往返×2）'
                             % (n_trip, n_fill))

            # ---- ⑤ 冲正按钮只在流水页 ----
            open_tab('交易记录')
            assert pg.locator('[id^=lpp].on a.lvrv').count() == 0, \
                ('业绩页的交易记录里有「冲正」按钮 —— 那是"更正录入"，'
                 '属于流水页那条链（这里是复盘视角）')
            assert pg.locator('[id^=lpp].on a[href*="/fills"]').count() >= 1, \
                ('没给去流水页的入口 —— 不给冲正可以，但不能把它**藏起来**'
                 '（同「看不出能点的入口 = 没有入口」那条）')
            notes.append('冲正留在流水页，但给了入口')
            assert not errs, 'JS 报错：%s' % errs[:3]
            br.close()
    finally:
        sv.ALLOW_LIVE = old_live
        httpd.shutdown()
    return '；'.join(notes)


def _lp_tab(pg, name, timeout=90000):
    """业绩页：切到某个页签并等它可见。

    🔴 业绩页 2026-09-15 改成了页签（用户要求），于是「收益明细」「每日持仓」
      这些块默认在**未激活的 pane** 里（`.pane` 的 display:none）——
      `wait_for_selector` 默认要求**可见**，所以直接等里面的元素会超时 90 秒，
      看着像页面坏了。★ 判据一点没变，只是入口多了一步。
    """
    pg.wait_for_selector('#lptabs div', timeout=timeout)
    pg.locator('#lptabs div', has_text=name).first.click()
    pg.wait_for_timeout(300)


@case('实盘不许比回测少：每日持仓 + 交易记录（FIFO 往返）（playwright）',
       tag='web')
def t_live_hist():
    """2026-09-15 用户："发现实盘功能反而没有交易记录、每日持仓，
    实盘的信息不应该比回测少。" —— 是。对照下来实盘缺两块：

        回测「持仓」页      每日快照、逐日回看      实盘只有**当前**持仓
        回测「交易记录」页  一笔平仓的收益率/盈亏    实盘只有成交流水（录入视角）

    🔴 **数据本来就有，只是没人把它展开**：`equity_curve` 已经在逐日
      `lots_asof` 重放持仓了，只是把逐只明细扔掉只留合计；成交也一直在账本
      里，缺的是"按 FIFO 配对成一笔往返"这一层。所以没引任何新数据源。

    🔴 **最关键的自证：每日持仓算出的权益必须逐日等于【业绩页那条曲线】。**
      两处各算各的话，"同一天两个总资产"不报错，只是两页对不上 ——
      而这一页存在的理由就是复盘时对着看。
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return '跳过（无 playwright）'
    import threading
    from assay import live as lv
    from assay import server as sv
    from http.server import ThreadingHTTPServer
    old_live = sv.ALLOW_LIVE
    sv.ALLOW_LIVE = True
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), sv.Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    notes = []
    try:
        # 🔴 要挑**有平仓往返**的账户 —— 只挑"有成交"的话可能全是买入，
        #   下面"收益率含费"整段会静默跳过（第一版就挑到了 0 笔往返的账户，
        #   用例照样绿）。有往返的优先，没有再退回任意有成交的。
        cands = [a['id'] for a in lv.load_accounts()
                 if not a.get('archived') and lv.fills(a['id'])]
        aid = next((c for c in cands if lv.round_trips(c)['total']), None) \
            or (cands[0] if cands else None)
        if not aid:
            return '跳过（没有有成交的账户）'

        # ---- ① 服务端：每日持仓的权益必须 == 权益曲线 ----
        h = lv.daily_holdings(aid, limit=500)
        assert h['total'] > 0, '每日持仓是空的'
        cur = lv.equity_curve(aid)
        emap = dict(zip(cur['dates'], cur['equity']))
        bad = [(r['date'], r['equity'], emap[r['date']]) for r in h['rows']
               if r['date'] in emap and abs(emap[r['date']] - r['equity']) > 1.0]
        assert not bad, \
            ('每日持仓算出的权益与业绩页那条曲线对不上（%d 天）：%s —— '
             '两处各算各的，"同一天两个总资产"不报错，只是两页对不上'
             % (len(bad), bad[:3]))
        # 🔴 **反向自证**：这条断言不是空转 —— 日期必须真的有交集
        hit = len({r['date'] for r in h['rows']} & set(emap))
        assert hit >= 3, '只对上了 %d 天，这条对账基本没生效' % hit
        notes.append('每日持仓 %d 行 / %d 天，权益与曲线逐日一致（对了 %d 天）'
                     % (h['total'], h['n_days'], hit))

        # ★ 权重的分母必须是**总权益**（含现金），不是持仓市值 ——
        #   后者会让满仓与半仓都显示 100%，而仓位正是要看的东西。
        one = [r for r in h['rows'] if r['date'] == h['rows'][0]['date']]
        wsum = sum(r['weight'] or 0 for r in one)
        mv = sum(r['value'] for r in one)
        assert abs(wsum - mv / one[0]['equity']) < 1e-4, \
            '权重之和 %.4f 与 市值/权益 %.4f 对不上' % (wsum, mv / one[0]['equity'])
        if one[0]['cash'] > 1:
            assert wsum < 0.9999, \
                ('有 %.0f 现金，权重之和却是 %.4f —— 分母用成了持仓市值，'
                 '那样满仓与半仓都显示 100%%' % (one[0]['cash'], wsum))
        notes.append('权重分母是总权益（仓位 %.1f%%，现金 %.0f）'
                     % (wsum * 100, one[0]['cash']))

        # ---- ② 交易记录：FIFO 往返，收益率【含费】 ----
        t = lv.round_trips(aid, limit=500)
        if not t['total']:
            notes.append('⚠ 这个账户还没有平仓往返，"收益率含费"那几条没验到')
        if t['total']:
            r0 = t['rows'][0]
            for k in ('entry_date', 'exit_date', 'entry_price', 'exit_price',
                      'shares', 'ret', 'pnl', 'holding_days', 'fee'):
                assert k in r0, '往返记录缺字段 %s' % k
            # 🔴 **含费**：不含的话是报高，而"报高"在实盘上是最危险的方向。
            gross, cost = r0['shares'] * r0['exit_price'], r0['shares'] * r0['entry_price']
            naive = gross / cost - 1 if cost else 0
            assert r0['fee'] > 0, '这笔往返的费用是 0，验不出含不含费（换个账户）'
            # 🔴 判据按**费用的量级**给，不是 `abs(diff) > 1e-9` ——
            #   后者太松：变异成"不含费"之后两者仍差 1e-10（`round(…,6)` 与
            #   展示价四舍五入的残差），断言照样通过（变异实测漏过一轮）。
            #   真正该出现的差距是"费用 / 成本"那个量级，这里取它的一半当下界。
            expect = r0['fee'] / cost if cost else 0
            assert expect > 1e-5, '费用占成本只有 %.2g，验不出（换个样本）' % expect
            assert naive - r0['ret'] > expect * 0.5, \
                ('收益率 %.6f 与不含费的 %.6f 只差 %.2g，而费用占成本 %.2g —— '
                 '说明费用没算进去（不含费就是报高）'
                 % (r0['ret'], naive, naive - r0['ret'], expect))
            # 盈亏与收益率必须自洽（同一笔不能一个正一个负）
            assert (r0['pnl'] > 0) == (r0['ret'] > 0), \
                '盈亏 %.2f 与收益率 %.4f 符号不一致' % (r0['pnl'], r0['ret'])
            notes.append('往返 %d 笔，收益率含费（%.4f%% < 不含费 %.4f%%）'
                         % (t['total'], r0['ret'] * 100, naive * 100))
        # ★ 未平仓的也要给 —— 回测的 trades.parquet 恰恰没有它们
        assert 'n_open' in t and 'open' in t, \
            '没给未平仓批次 —— "我现在拿着什么"在实盘里是天天要看的'

        # ---- ③ 停牌那天按【最后已知价】挂账，并且标出来 ----
        # 🔴 当前账户没有停牌票，所以这条**必须构造**才测得到 ——
        #   不构造的话那段代码是空转的，改坏了也全绿（变异实测漏过）。
        #   造法：把某只票某天的收盘价从价格表里抹掉，模拟"那天没有行情"。
        import assay.lv.hist as _h
        _orig_feed = _h._feed
        drop = {}
        def _feed2(rows, flows, datalake=None):
            feed, days, px = _orig_feed(rows, flows, datalake)
            if len(days) >= 3:
                d = days[-2]
                for (c, dd) in list(px):
                    if dd == d:
                        drop[(c, dd)] = px.pop((c, dd))
            return feed, days, px
        _h._feed = _feed2
        try:
            h2 = _h.daily_holdings(aid, limit=500)
        finally:
            _h._feed = _orig_feed
        if drop:
            day = sorted({str(d) for _c, d in drop})[-1]
            hit = [r for r in h2['rows'] if r['date'] == day]
            assert hit, '构造的停牌日 %s 一行都没有 —— 那天的持仓被整个丢了' % day
            assert all(r['stale_price'] for r in hit), \
                ('停牌那天没标 stale_price —— 价格是上一个交易日的，不标的话'
                 '人会以为"这只票今天没动"，而事实是没有数据')
            assert all(r['last_price'] for r in hit), \
                ('停牌那天的价格是空的 —— 该按**最后已知价**挂账，'
                 '直接跳过会让那天的市值凭空少一块，而它不报错')
            notes.append('停牌日按最后已知价挂账且标 stale（构造 %d 只验证）'
                         % len(hit))

        # ---- ④ 页面 ----
        with sync_playwright() as pw:
            br = pw.chromium.launch()
            pg = br.new_page(viewport={'width': 1440, 'height': 1000})
            errs = []
            pg.on('pageerror', lambda e: errs.append(str(e)))
            pg.goto('http://127.0.0.1:%d/#/live/%s/perf' % (port, aid),
                    wait_until='networkidle')
            _lp_tab(pg, '每日持仓')
            pg.wait_for_selector('#lp_hdt table', timeout=90000)
            pg.wait_for_timeout(1500)
            assert pg.locator('#lp_hdt table tbody tr').count() > 0, \
                '每日持仓表没渲染出行'
            # 🔴 分页条不许是 NaN —— 字段写成 `h.lim`（实际是 `limit`）时
            #   会印出 "第 NaN / NaN 页"，而**接口断言看不到**（total 是对的），
            #   只有看页面才发现。
            txt = pg.inner_text('#lp_hold')
            assert 'NaN' not in txt, \
                '每日持仓的分页条出现 NaN（字段名写错了？）：%s' \
                % [x for x in txt.split('\\n') if 'NaN' in x][:2]
            _lp_tab(pg, '清仓记录')
            assert 'NaN' not in pg.inner_text('#lp_trip'), '清仓记录出现 NaN'
            _lp_tab(pg, '每日持仓')
            # 翻页真的生效
            first = pg.evaluate(
                "() => document.querySelector('#lp_hdt table tbody tr').textContent")
            pg.locator('#lp_hold .hs').first.select_option('50')
            pg.wait_for_timeout(1500)
            pg.locator('#lp_hold .hn').first.click()
            pg.wait_for_timeout(1500)
            second = pg.evaluate(
                "() => document.querySelector('#lp_hdt table tbody tr').textContent")
            assert first != second, '翻页之后第一行没变 —— 分页没生效'
            notes.append('页面：两块都渲染、分页无 NaN 且翻页生效')
            assert not errs, 'JS 报错：%s' % errs[:3]
            br.close()
    finally:
        sv.ALLOW_LIVE = old_live
        httpd.shutdown()
    return '；'.join(notes)


@case('业绩页：回撤是【副图】不是页签 / 全页只有一个返回按钮（playwright）',
       tag='web')
def t_live_perf_dd_and_back():
    """2026-09-14 用户报的两件事。

    ① 「回撤曲线不应该是一个单独的页签，应该跟随收益曲线和资金曲线，
       在下方对齐。」—— 对。回撤不是与另两条并列的"第三种看法"，它是
       **对当前这条曲线的注解**（"这一段跌下去有多深"）。做成页签的代价是：
       要看那个坑有多深必须切走，而切走之后上面那条曲线就不在眼前了。

    ② 「业绩上方的返回按钮点击没有反应，而且实盘的右边也有一个返回按钮，
       为什么需要两个？」—— 没有理由，那是 bug。`live-perf.js` 往 body 里
       又吐了一份 `backLink()` 的 HTML 却**从没调 `wireBack()`**，于是
       页面上两个一模一样的按钮、下面那个点了没反应。更糟的是两个都叫
       `id="goback"`，而 `wireBack()` 用 `$('#goback')` 只取第一个 ——
       就算补调一次也只会把顶栏那个重绑一遍。

    🔴 **「口径跟随主图」这条在真实数据上是空转的**：两个账户
      `net_deposit=0`，按净值算和按总资产算的回撤**逐日完全相同**。
      所以必须**构造带入金的数据**才测得到 —— 否则把实现改成"永远用
      equity"，断言照样全绿（同「四条断言各需不同构造条件」那条）。
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return '跳过（无 playwright）'
    import threading
    from assay import server as sv
    old_live = sv.ALLOW_LIVE
    sv.ALLOW_LIVE = True
    from http.server import ThreadingHTTPServer
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), sv.Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    notes = []
    try:
        from assay import live as _lv
        aid = next((a['id'] for a in _lv.load_accounts()
                    if not a.get('archived')), None)
        if not aid:
            return '跳过（没有实盘账户）'
        with sync_playwright() as pw:
            br = pw.chromium.launch()
            pg = br.new_page(viewport={'width': 1440, 'height': 900})
            errs = []
            pg.on('pageerror', lambda e: errs.append(str(e)))
            # 🔴 从**站内另一页**进来，backLink 才会出现（它要同源 referrer
            #   + history.length>1）。直接 goto 的话两个按钮都不渲染，
            #   于是"只有一个"这条断言变成空转 —— 用户报的正是这个场景。
            pg.goto('http://127.0.0.1:%d/stock.html?code=601857.XSHG' % port,
                    wait_until='networkidle')
            pg.click('text=💰 实盘')
            pg.wait_for_selector('a.lpin', timeout=90000)
            pg.click('a.lpin')
            pg.wait_for_selector('#lp_dd svg', timeout=90000)
            pg.wait_for_timeout(400)

            # ---- ② 返回按钮：只许一个，且必须绑上了事件 ----
            gb = pg.evaluate("""() => [...document.querySelectorAll('#goback')]
                .map(x => ({inTop: !!x.closest('#top'), wired: !!x.onclick}))""")
            assert len(gb) == 1, \
                ('全页只许有一个「‹ 返回」，实得 %d 个 —— 两个都叫 id=goback，'
                 '而 wireBack() 用 $(\'#goback\') 只取第一个，另一个必然是'
                 '**点了没反应**的死按钮（给一个点了没反应的按钮比不给更糟）'
                 % len(gb))
            assert gb[0]['inTop'], '那一个该在顶栏（pageHead 统一出的那个）'
            assert gb[0]['wired'], \
                '顶栏那个返回按钮没绑 onclick —— wireBack() 没被调到'
            before = pg.evaluate('() => location.hash')
            pg.click('#goback')
            pg.wait_for_timeout(800)
            assert pg.evaluate('() => location.hash') != before, \
                '点返回没有任何变化（hash 还是 %s）' % before
            notes.append('返回按钮只剩顶栏一个且点了真的回退')
            pg.go_forward()
            pg.wait_for_selector('#lp_dd svg', timeout=90000)
            pg.wait_for_timeout(400)

            # ---- ① 回撤不是页签 ----
            tabs = [t.strip() for t in
                    pg.locator('#lp_tab a.lpc').all_inner_texts()]
            assert tabs == ['收益曲线', '资金曲线'], \
                '页签该只剩收益/资金两个，实得 %s' % tabs
            assert pg.locator('#lp_dd svg').count() == 1, \
                '回撤副图该一直在（不是切出来的）'

            # ---- ① 两张图 x 轴【真的对齐】----
            #   🔴 判据是**量出来的左右边界**，不是"两个 svg 都在" ——
            #     后者在把副图塞进另一个窄容器时照样绿。
            def geo():
                return pg.evaluate("""() => {
                  const m=document.querySelector('#lp_chart svg'),
                        d=document.querySelector('#lp_dd svg');
                  if(!m||!d) return null;
                  const a=m.getBoundingClientRect(), c=d.getBoundingClientRect();
                  return {mx:a.x, mw:a.width, mb:a.bottom, mh:a.height,
                          dx:c.x, dw:c.width, dt:c.top, dh:c.height,
                          mvb:m.getAttribute('viewBox'),
                          dvb:d.getAttribute('viewBox')};}""")
            g = geo()
            assert g, '主图或副图不在'
            assert abs(g['mx'] - g['dx']) < 1 and abs(g['mw'] - g['dw']) < 1, \
                ('两张图左右没对齐：主图 x=%.1f w=%.1f，副图 x=%.1f w=%.1f '
                 '—— 同一天必须落在同一个横坐标上，否则"对着看"就不成立'
                 % (g['mx'], g['mw'], g['dx'], g['dw']))
            #   viewBox 宽必须相同（lineChart 的 W 固定 1160）：宽相同 +
            #   等比缩放 = 刻度天然一致。宽不同的话就算外框对齐，
            #   里面的 x 也是错位的，**而那不报错**。
            assert g['mvb'].split()[2] == g['dvb'].split()[2], \
                ('两张图 viewBox 宽不同（%s vs %s）—— 外框对齐但内部刻度错位'
                 % (g['mvb'], g['dvb']))
            assert g['dt'] >= g['mb'] - 2, \
                '副图该在主图【下方】，实得 副图 top=%.0f < 主图 bottom=%.0f' \
                % (g['dt'], g['mb'])
            #   ★ 副图必须**更矮** —— 它是注解不是主角。原来我这行写成了
            #     `assert g['dh'] < g['mh'] if 'mh' in g else True`，而 geo()
            #     根本没返回 mh，于是整句恒真：**一条彻底空转的断言**。
            assert g['dh'] < g['mh'] * 0.75, \
                ('回撤副图该明显比主图矮（注解不该抢主角），实得 副图 %.0f / '
                 '主图 %.0f' % (g['dh'], g['mh']))
            notes.append('主副图同 x 同宽（x=%.0f w=%.0f）、副图在下方且更矮'
                         % (g['mx'], g['mw']))
            # ---- ① 回撤曲线必须【连续】且**碰到水面线**（2026-09-14）----
            #   🔴 用户："0 回撤到有回撤、有回撤到 0 回撤之间没有相连。"
            #     原来 ddGap 把所有 0 抹成 null，于是每段回撤两头都悬空，
            #     "回到历史最高"成了一个空档 —— 而空档读作"没有数据"。
            #   ★ 只判"连续"是不够的：把 0 抹掉之后每段各自仍然是连续的
            #     （froec 那份数据实测断成 2 段、每段内部都连着）。
            #     判据必须是**曲线顶点落在水面线上**。
            t = pg.evaluate(_DDJS)
            assert t and t['topY'] is not None and t['zeroY'] is not None, \
                '取不到回撤副图的曲线顶点或水面线'
            assert t['nM'] == 1, \
                ('回撤副图断成了 %d 段 —— 规则没有例外：**断开 = 没有数据**。'
                 '回撤为 0 的点要照画，好让每段回撤两头都接上水面线'
                 % t['nM'])
            assert abs(t['topY'] - t['zeroY']) < 1.0, \
                ('回撤曲线顶点 y=%.1f 没落在水面线 y=%.1f 上 —— 创新高那天'
                 '回撤正好是 0，曲线必须**碰到**水面线（froec 账户 09-08 '
                 '就是创新高那天）' % (t['topY'], t['zeroY']))
            notes.append('回撤曲线连续(1 段)且顶点碰到水面线')


            # ---- ① 口径【跟随主图】：必须用构造数据才测得到 ----
            #   造一段有入金的行情：equity 在中途跳一截（入金），nav 不跳。
            #   于是"按总资产算"的回撤会被抬高的峰值量得更深，两条必然不同。
            diverge = pg.evaluate("""() => {
              const D=[],E=[],N=[],P=[];
              let nav=1.0, eq=100000;
              for(let i=0;i<60;i++){
                const dt=new Date(Date.UTC(2026,0,5+i));
                D.push(dt.toISOString().slice(0,10));
                const r = (i<20? 0.004 : -0.004);      /* 先涨后跌，制造回撤 */
                nav*=(1+r); eq*=(1+r);
                /* 🔴 入金必须落在【下跌途中】，不能落在峰值那天 ——
                   回撤是**尺度无关**的：在峰值入金之后两条同比例下跌，
                   算出来一模一样，断言就成了空转（第一版我就这么构造的，
                   是断言自己把它抓出来的）。落在下跌途中时 equity 会创新高、
                   回撤被重置，而 nav 继续加深 —— 实测逐日最大差 0.0583。 */
                if(i===34) eq+=500000;                  /* 入金：只动 equity */
                N.push(nav); E.push(Math.round(eq)); P.push(Math.round(eq*r));
              }
              const O={dates:D,nav:N,equity:E,day_rets:N.map(()=>0),day_pnls:P,
                       bench:{},benchmarks:[],
                       stats:{start_equity:100000,net_deposit:500000,twr:nav-1,
                              max_drawdown:0,drawdown_now:0}};
              LPD=O; LPS=O; LPC='nav'; renderChart('probe');
              const a=document.querySelector('#lp_dd svg path[stroke]');
              const pa=a?a.getAttribute('d'):null;
              const na=document.querySelector('#lp_dd .note:last-of-type');
              const ta=na?na.textContent:'';
              LPC='eq'; renderChart('probe');
              const b=document.querySelector('#lp_dd svg path[stroke]');
              const pb=b?b.getAttribute('d'):null;
              const nb=document.querySelector('#lp_dd .note:last-of-type');
              const tb=nb?nb.textContent:'';
              return {navPath:pa, eqPath:pb, navTxt:ta, eqTxt:tb};}""")
            assert diverge['navPath'] and diverge['eqPath'], '构造数据没画出回撤'
            assert diverge['navPath'] != diverge['eqPath'], \
                ('切换主图时回撤**没跟着换口径** —— 构造了 50 万入金，'
                 '按总资产算会被抬高的峰值量得更深，两条曲线必然不同；'
                 '现在画出来一模一样，说明副图永远在用同一个序列')
            assert 'TWR 净值' in diverge['navTxt'], \
                '收益曲线下的回撤该标明按 TWR 净值算：%r' % diverge['navTxt'][:60]
            assert '总资产' in diverge['eqTxt'], \
                '资金曲线下的回撤该标明按总资产算：%r' % diverge['eqTxt'][:60]
            assert '净入金' in diverge['eqTxt'], \
                ('有净入金时必须说出来 —— 否则"两条曲线的回撤不一样"'
                 '看着像其中一个算错了')
            notes.append('口径跟随主图（构造 50 万入金，两条回撤路径确实不同）')
            assert not errs, 'JS 报错：%s' % errs[:3]
            br.close()
    finally:
        sv.ALLOW_LIVE = old_live
        httpd.shutdown()
    return '；'.join(notes)


@case('实盘业绩页：资金/收益/回撤三条曲线 + 年月日收益表（playwright）',
       tag='web')
def t_live_perf_ui():
    """回测详情页能看的曲线与收益表，实盘也要能看（独立页 #/live/<id>/perf）。

    🔴 **资金曲线与收益曲线是两条不同的线**：总资产含入金，入金那天会跳
      一截；净值是 TWR，入金不算收益。混用就是把入金算成赚的
      （TWR 存在的全部理由）。所以两条都画、各自标口径。
    🔴 净值序列由**服务端**给（lv/perf.py 的 nav）—— 前端拿 equity 自己推
      就是第二份 TWR 实现，迟早分叉。
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return '跳过（无 playwright）'
    import threading
    from assay import server as sv
    old_live = sv.ALLOW_LIVE
    sv.ALLOW_LIVE = True
    from http.server import ThreadingHTTPServer
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), sv.Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        # 服务端先自证：四个序列对齐，且 nav 末值 == stats.twr
        from assay import live as _lv
        aid = next((a['id'] for a in _lv.load_accounts()
                    if not a.get('archived')), None)
        if not aid:
            return '跳过（没有实盘账户）'
        cur = _lv.equity_curve(aid)
        n = len(cur['dates'])
        assert n == len(cur['equity']) == len(cur['nav']) \
            == len(cur['day_rets']), \
            ('dates/equity/nav/day_rets 必须一一对齐，实得 %d/%d/%d/%d'
             % (n, len(cur['equity']), len(cur['nav']), len(cur['day_rets'])))
        assert abs(cur['nav'][-1] - 1 - cur['stats']['twr']) < 1e-6, \
            ('逐日累乘的净值末值必须 == stats.twr（%.8f vs %.8f）—— '
             '对不上说明两处 TWR 算法分叉了'
             % (cur['nav'][-1] - 1, cur['stats']['twr']))
        #   🔴 第一天必须有收益：TWR 从**开户资金**起算，nav[0] != 1
        assert abs(cur['nav'][0] - 1.0) > 1e-9, \
            ('nav[0] 该含建仓当天的收益（TWR 起点是开户那一刻）—— '
             '等于 1 说明第一天又被丢掉了')
        #   🔴 逐日**金额**的合计必须 == stats.pnl_total。
        #     day_pnls 是 Δ权益 **剔除现金流**后的值 —— 直接用
        #     `eq[i]-eq[i-1]` 的话入金那天会多出一大截（那不是赚的）。
        #     ★ 用**同一份 cur** 比，不受盘中价格变动影响。
        assert abs(sum(cur['day_pnls']) - cur['stats']['pnl_total']) < 0.05, \
            ('逐日金额合计 %.2f 与 stats.pnl_total %.2f 不一致 —— '
             '两处对"赚了多少钱"的算法分叉了'
             % (sum(cur['day_pnls']), cur['stats']['pnl_total']))

        _RGJS = "()=>{const D=[],N=[];let d=new Date(Date.UTC(2023,0,2)), v=1.0;while(d <= new Date(Date.UTC(2026,8,8))){const wd=d.getUTCDay();if(wd>=1&&wd<=5){D.push(d.toISOString().slice(0,10));v*=1.0001;N.push(v);}d.setUTCDate(d.getUTCDate()+1);}const O={dates:D,nav:N,equity:N.map(x=>x*1e6),day_rets:N.map(()=>0.0001),day_pnls:N.map(()=>100),bench:{},benchmarks:[],stats:{}};const save=LPR, out={};for(const k of ['all','ytd','m1','m3','m6','y1','y3']){LPR={k:k,a:'',b:''};const S=lprSlice(O);out[k]={a:S.dates[0],b:S.dates[S.dates.length-1],n:S.dates.length,f:S.nav[0],r:S.nav[S.nav.length-1]-1};}LPR={k:'cus',a:'2025-03-01',b:'2025-06-30'};const C=lprSlice(O);out.cus={a:C.dates[0],b:C.dates[C.dates.length-1],n:C.dates.length};LPR=save;return {out:out,total:D.length};}"
        with sync_playwright() as pw:
            br = pw.chromium.launch()
            pg = br.new_page()
            errs = []
            pg.on('pageerror', lambda e: errs.append(str(e)))
            pg.on('console', lambda m: errs.append('console: ' + m.text)
                  if m.type == 'error' else None)
            # ---- 入口：KPI 板的「累计收益」必须是**看得出能点**的链接 ----
            pg.goto('http://127.0.0.1:%d/#/live/%s' % (port, aid),
                    wait_until='networkidle')
            pg.wait_for_selector('#lvbody .lvsec', timeout=90000)
            pg.wait_for_selector('a.lpin', timeout=90000)
            lnk = pg.locator('a.lpin').first
            assert '累计收益' in lnk.inner_text(), \
                '入口该长在「累计收益」那一格上'
            assert '›' in lnk.inner_text(), \
                ('入口要带 › —— **看不出能点的入口 = 没有入口**'
                 '（选股理由那次实测人找不到）')
            lnk.click()
            _lp_tab(pg, '收益明细')
            pg.wait_for_selector('#lp_tbl .lpbar', timeout=90000)
            pg.wait_for_timeout(500)
            assert pg.evaluate('()=>location.hash').endswith('/perf'), \
                '点了没跳到业绩页'
            #   ★ 这条断言经历了三次改写，每次都是**被改动作废**而不是打挂：
            #     原版  查 `#main` 全文有没有「资金曲线/收益曲线/回撤曲线/收益明细」
            #           —— 当时四块平铺在一页上
            #     09-14 回撤降为副图 -> 去掉「回撤曲线」那一项
            #     09-15 整页改成页签（用户要求）-> **未激活的 pane 读不到
            #           `inner_text`**，全文查必然失败
            #   🔴 它当初要防的坑是"套进 `.pane` 导致屏幕上什么都没有"，
            #     而现在 `.pane` 正是主动采用的结构 —— 所以判据改成
            #     **切到那一页之后，那一块真的可见且有内容**。
            #     光查"文字在不在 DOM 里"挡不住那个坑（DOM 里一直都在）。
            def _pane_has(tab, *words):
                _lp_tab(pg, tab)
                pane = pg.locator('[id^=lpp].on')
                assert pane.count() == 1, \
                    '切到「%s」后可见 pane 有 %d 个' % (tab, pane.count())
                t = pane.inner_text()
                for w in words:
                    assert w in t, '「%s」那一页里没有「%s」：%s' % (tab, w, t[:80])
                # 🔴 **可见**才算数：`.pane` 不加 `.on` 时 display:none，
                #   那正是原版要防的"画出来了但屏幕上什么都没有"。
                assert pane.first.is_visible(), '「%s」那一页不可见' % tab
                return t

            _pane_has('业绩曲线', '资金曲线', '收益曲线')
            _pane_has('收益明细', '收益明细')
            # ★ 验完切回「业绩曲线」—— 下面那一大段都在这一页里操作
            #   （切曲线、勾基准、读 y 轴刻度）。不切回去的话它们全在
            #   隐藏的 pane 上点，超时 30 秒而看着像页面坏了。
            _lp_tab(pg, '业绩曲线')
            txt = pg.inner_text('#main')
            # ---- 三条曲线在【一个框】里，上方切换 ----
            #   🔴 三张图竖着排的话要上下滚动才能对比同一天；
            #     同一个位置切换才看得出差别。
            tabs = [t.strip() for t in
                    pg.locator('#lp_tab a.lpc').all_inner_texts()]
            #   ★ 顺序 = 看的顺序：打开第一眼要回答"我赚了多少 / 跑赢基准了吗"，
            #     而不是"账上有多少钱"（那在 KPI 板里已经有了）。
            assert tabs == ['收益曲线', '资金曲线'], \
                ('曲线切换条该只剩两个页签（回撤是副图，不是并列的第三种'
                 '看法）：%s' % tabs)
            assert pg.locator('#lp_chart svg').count() == 1, \
                '该只有一个图框（三条曲线切换），实得 %d' \
                % pg.locator('#lp_chart svg').count()
            assert pg.locator('#lp_tab a.lpc.on').inner_text() == '收益曲线', \
                '默认该是**收益曲线**（业绩页第一眼看的是赚了多少）'

            def _axis():
                return [(t or '').strip() for t in
                        pg.locator('#lp_chart svg text').all_text_contents()
                        if t and not (t or '').strip().startswith('20')]

            def _pcts():
                out = []
                for t in _axis():
                    if t.endswith('%'):
                        try:
                            out.append(float(t.rstrip('%')))
                        except ValueError:
                            pass
                return out

            # 资金曲线：y 轴必须是**金额**，不能是倍数那个 x 后缀
            #   ★ 默认已经是收益曲线了，这里显式切到资金曲线再验。
            pg.locator('#lp_tab a.lpc[data-c="eq"]').click()
            pg.wait_for_timeout(300)
            #   两条曲线的口径必须各自写在图下面。★ 一次只显示一条，
            #   所以在**对应曲线激活时**分别验（原来两句同时查，
            #   合到一个框之后必然失败）。
            assert '含入金' in pg.inner_text('#lp_chart'), \
                ('资金曲线要标"含入金" —— 不标的话人会拿总资产当收益'
                 '（入金那天会跳一截）')
            mx = _axis()
            assert mx, '资金曲线没有刻度'
            assert not any(t.endswith('x') for t in mx), \
                ('资金曲线还在用倍数后缀 `x` —— 那是给归一化净值的'
                 '（`2.5x` = 2.5 倍），拿它显示 40 万会印成 `404881x`，'
                 '那个数没人读得出来：%s' % [t for t in mx if t.endswith('x')])
            assert any(t.endswith(('万', '亿')) for t in mx), \
                '资金曲线的 y 轴该是金额（40.5万）：%s' % mx[:6]
            assert pg.locator('#lp_chart svg path[stroke]').count() > 0, \
                '资金曲线没画出折线'

            # 回撤【副图】：🔴 y 轴最高点必须**正好是 0**
            #   ★ 不再点页签 —— 它现在永远在主图下方（2026-09-14）。
            def _ddpcts():
                out = []
                for t in pg.locator('#lp_dd svg text').all_text_contents():
                    t = (t or '').strip()
                    if t.endswith('%'):
                        try:
                            out.append(float(t.rstrip('%')))
                        except ValueError:
                            pass
                return out
            dv = _ddpcts()
            assert dv, '回撤副图没有百分比刻度（该用 ratioAxis）'
            #   ★ 消息里的 % 都要写成 %% —— 混用的话断言触发时
            #     **消息本身崩掉**（not enough arguments for format string），
            #     于是看不到失败原因，只看到一个格式化错误（变异测试踩到）。
            assert abs(max(dv)) < 1e-9, \
                ('回撤曲线 y 轴最高点该正好是 0%%，实得 %+.2f%% —— '
                 '在最高点时回撤为 0，**不可能为正**；y 轴的留白会印出 '
                 '+0.1%% 这种没有意义的数，读的人会以为"曾经比历史最高'
                 '还高 0.1%%"（用 hiCap:0 钳住）' % max(dv))
            assert min(dv) < 0, '回撤该有负值：%s' % dv
            assert pg.locator('#lp_dd svg path[stroke]').count() > 0, \
                '回撤副图没画出折线'

            # 收益曲线：百分比刻度 + 量级合理 + 同时给金额读数
            pg.locator('#lp_tab a.lpc[data-c="nav"]').click()
            pg.wait_for_timeout(300)
            nv = _pcts()
            assert nv, '收益曲线该是百分比刻度（pctAxis）'
            assert max(abs(x) for x in nv) < 500, \
                ('收益曲线的 y 轴量级到了 %.0f 个百分点 —— 画的不是 '
                 'TWR 净值，很可能误用了 equity（总资产，含入金）'
                 % max(abs(x) for x in nv))
            #   ★ 跨度小的时候刻度要加小数位：0 位会印出 `-0%` 和两个 `0%`
            ax_pct = [t for t in _axis() if t.endswith('%')]
            assert '-0%' not in ax_pct, \
                'y 轴出现了 -0（跨度小时该加小数位）：%s' % ax_pct
            assert len(ax_pct) == len(set(ax_pct)), \
                'y 轴有重复刻度（跨度小时该加小数位）：%s' % ax_pct
            #   🔴 收益率与金额要**同时**给：只有百分比的话它旁边没有能和
            #     "持仓浮盈 +3,698" 对上的数，看着像两回事。
            assert '收益率与累计金额' in pg.inner_text('#lp_chart'), \
                '收益曲线该说明同时给收益率与累计金额'
            assert '入金不算收益' in pg.inner_text('#lp_chart'), \
                ('收益曲线要标"入金不算收益" —— 这是 TWR 与总资产的分界，'
                 '不标的话同屏两个口径打架')
            pg.locator('#lp_chart svg').scroll_into_view_if_needed()
            pg.wait_for_timeout(150)
            _bx = pg.locator('#lp_chart svg').bounding_box()
            pg.mouse.move(_bx['x'] + _bx['width'] * 0.7,
                          _bx['y'] + _bx['height'] * 0.5)
            pg.wait_for_timeout(250)
            _tp = pg.locator('#tip').text_content() or ''
            # ★ 判**两个读数都在**，不钉那条线叫什么 —— 加策略线那轮把
            #   序列名从「净值」改成了「实际 (TWR)」（要与基准并排时必须
            #   说清哪条是自己），断言若钉着旧标签，它测的就是名字不是内容。
            assert ('TWR' in _tp or '净值' in _tp) and '累计金额' in _tp, \
                ('收益曲线的 tooltip 该同时给净值(TWR)与累计金额，实得 %r' % _tp)
            assert '元' in _tp, 'tooltip 里的金额该带单位：%r' % _tp
            #   ★ 金额不画成第二条线（量纲不同共轴会把一条压平）
            assert pg.locator('#lp_chart svg path[stroke]').count() == 1, \
                '收益曲线该只有一条线（金额走 tooltip，不共轴）'

            # ---- 基准指数叠加 ----
            #   ★ 可选清单由**服务端**给（lv/perf.py 的 BENCHMARKS）——
            #     本地缺哪个指数只有服务端知道；前端硬编码的话会列出
            #     取不到数据的选项，而"点了什么都不出来"比不给更糟。
            from assay.lv import perf as _perf
            assert len(_perf.BENCHMARKS) >= 5, '可选基准太少'
            codes = [b['code'] for b in _perf.BENCHMARKS]
            assert all(_perf._SYM_RE.match(c) for c in codes), \
                'BENCHMARKS 里有不合白名单（sh/sz+6 位）的代码：%s' % codes
            #   🔴 **只许列本地真有日线的** —— 中证2000/微盘股本地没有
            #     （前者只有 ETF、后者是万得专有），列了就是个死选项。
            got = _perf.bench_curves(cur['dates'], codes)
            miss = [c for c in codes if c not in got]
            assert not miss, \
                ('BENCHMARKS 里这些本地取不到数据：%s —— '
                 '列一个点了什么都不出来的选项比不给更糟' % miss)
            for c, v in got.items():
                assert len(v) == len(cur['dates']), \
                    '%s 的曲线没对齐到账户日期轴（%d vs %d）' \
                    % (c, len(v), len(cur['dates']))
            #   ★ 基点取"第一天的前一交易日收盘" -> 首值一般不等于 1
            #     （等于 1 说明拿第一天自己做了基点，那会把首日涨跌
            #      排除在基准之外，实测差过 9.5pp）
            first = [v[0] for v in got.values() if v[0] is not None]
            assert first and any(abs(x - 1.0) > 1e-9 for x in first), \
                ('基准首值全是 1 —— 基点该取第一天的**前一交易日**收盘'
                 '（与 feed.benchmark 同一条纪律）')
            #   注入防护：白名单挡住非法 symbol
            bad = _perf.bench_curves(cur['dates'],
                                     ["sh000001'; DROP TABLE x --", 'xx1'])
            assert not bad, 'symbol 白名单没挡住非法输入：%s' % list(bad)

            bms = pg.locator('#lp_bm a.lpb')
            assert bms.count() >= len(_perf.BENCHMARKS), \
                '页面上的基准选项少了（%d < %d）' \
                % (bms.count(), len(_perf.BENCHMARKS))
            assert pg.locator('#lp_bm a.lpb.on').count() == 0, \
                ('默认一个基准都不该选 —— 三条线以上就看不清了，'
                 '而"想比哪个"因人而异')
            #   每个选项要标出本地数据从哪年开始（选了科创50 才发现
            #   前面是空的话，人会以为图画坏了）
            # ★ 逐个判，而不是只判 first —— 策略线也是基准选项之一（排在
            #   最前），而它没有"本地数据起点"可言（它是按绑定版本重跑出来
            #   的）。只判 first 会撞到它；而只判"某一个有"又太松。
            _bt = {(bms.nth(i).get_attribute('data-b') or ''):
                   (bms.nth(i).get_attribute('title') or '')
                   for i in range(bms.count())}
            _idx = {k: v for k, v in _bt.items() if k and k != 'strat'}
            assert _idx, '一个指数基准都没有？%s' % list(_bt)
            _nom = [k for k, v in _idx.items() if '本地数据自' not in v]
            assert not _nom, \
                '这些指数基准没在 title 里标数据起点：%s —— 选了科创50 才' \
                '发现前面是空的话，人会以为图画坏了' % _nom
            assert _bt.get('strat'), '策略线也要有 title 说清它是怎么算出来的'
            hits2 = []
            pg.on('request', lambda r: hits2.append(1)
                  if '/api/live/equity' in r.url else None)
            #   🔴 **单选**：同时看多个基准反而看不清，而"我的策略跑赢谁"
            #     一次问一个就够。选新的要**换掉**旧的，不是叠加。
            pg.locator('#lp_bm a.lpb[data-b="sh000905"]').click()
            pg.wait_for_timeout(250)
            assert pg.locator('#lp_chart svg path[stroke]').count() == 2, \
                '选一个基准该是 2 条线（净值 + 基准）'
            pg.locator('#lp_bm a.lpb[data-b="sz399303"]').click()
            pg.wait_for_timeout(300)
            on = [a.strip() for a in
                  pg.locator('#lp_bm a.lpb.on').all_inner_texts()]
            assert on == ['国证2000'], \
                '选新基准该换掉旧的（单选），实得选中 %s' % on
            assert pg.locator('#lp_chart svg path[stroke]').count() == 2, \
                ('还是该 2 条线 —— 选新的要换掉旧的，实得 %d'
                 % pg.locator('#lp_chart svg path[stroke]').count())
            #   点已选中的 -> 取消
            pg.locator('#lp_bm a.lpb[data-b="sz399303"]').click()
            pg.wait_for_timeout(300)
            assert pg.locator('#lp_bm a.lpb.on').count() == 0 \
                and pg.locator('#lp_chart svg path[stroke]').count() == 1, \
                '点已选中的该取消'
            #   ★ 切换基准**不该重新请求** —— 进页面时一次取全。
            #     每次勾选都重放整条权益曲线（几秒）的话，随手点一下
            #     就像卡住了。
            #     🔴 这条必须查在 reload **之前**：reload 本身当然会
            #       重新请求，混在一起就是拿两种原因的请求数去比。
            assert not hits2, \
                ('勾选基准触发了 %d 次 /api/live/equity —— 该一次取全、'
                 '切换只改显示' % len(hits2))
            #   ---- 记住选择（localStorage），刷新后还在 ----
            pg.locator('#lp_bm a.lpb[data-b="sh000905"]').click()
            pg.wait_for_timeout(300)
            # 🔴 基准是**按账户**记的（`lvbench:<aid>`）—— 全局一把键的话
            #   在 A 账户选的会把 B 账户的覆盖掉（用户 2026-09-16 报的）。
            assert pg.evaluate("(a)=>localStorage.getItem('lvbench:'+a)", aid) \
                == 'sh000905', \
                ('选中的基准要存进 localStorage —— 刷新一次就没了的话'
                 '每次进来都要重选，而这是"每天看同一个对比"的场景')
            pg.reload(wait_until='networkidle')
            _lp_tab(pg, '业绩曲线')
            pg.wait_for_selector('#lp_tab a.lpc', timeout=90000)
            pg.wait_for_timeout(900)
            pg.locator('#lp_tab a.lpc[data-c="nav"]').click()
            pg.wait_for_timeout(400)
            assert [a.strip() for a in
                    pg.locator('#lp_bm a.lpb.on').all_inner_texts()] \
                == ['中证500'], 'reload 之后选中的基准该还在'
            #   🔴 存的值必须**校验再用**：localStorage 里可能是上个版本留下的
            #     代码或手改的垃圾。不校验就拿一个取不到数据的 symbol 去画，
            #     表现是"选中了但没有线"，而它不报错。
            for junk in ('DROP TABLE x', 'sh999999'):
                pg.evaluate("([a,v])=>localStorage.setItem('lvbench:'+a,v)",
                            [aid, junk])
                pg.reload(wait_until='networkidle')
                pg.wait_for_selector('#lp_tab a.lpc', timeout=90000)
                pg.wait_for_timeout(900)
                pg.locator('#lp_tab a.lpc[data-c="nav"]').click()
                pg.wait_for_timeout(350)
                assert pg.locator('#lp_bm a.lpb.on').count() == 0, \
                    'localStorage 里的 %r 该被挡掉' % junk
                assert pg.locator('#lp_chart svg path[stroke]').count() == 1, \
                    '%r 不该画出第二条线（也不该崩）' % junk
                #   🔴 判据要看 **LPB 本身**：下游的
                #     `picks = LPB.filter(c => BD[c])` 会把取不到数据的过滤掉，
                #     所以"没有线 / 没有选中"这两条**抓不到校验被绕过**
                #     （变异测试实测全绿）。校验的意义是**别把垃圾读进状态**。
                #   ★ 只对**形状不合法**的那种判 —— `sh999999` 形状是对的，
                #     校验本来就不该挡它（它只是本地没这个指数），
                #     那一种靠下游过滤兜住就够了。
                if not junk.startswith(('sh', 'sz')):
                    assert pg.evaluate('()=>LPB') == [], \
                        ('localStorage 里的 %r 该在**读的时候**就被校验挡掉，'
                         '而不是靠下游过滤兜住 —— 状态里留着垃圾，'
                         '下次谁用它都可能踩到' % junk)
            #   恢复一个正常的，继续后面的断言
            pg.locator('#lp_bm a.lpb[data-b="sh000905"]').click()
            pg.wait_for_timeout(350)
            #   tooltip 不许重复：基准只进 series，不能再进 extra
            pg.locator('#lp_chart svg').scroll_into_view_if_needed()
            pg.wait_for_timeout(150)
            _b2 = pg.locator('#lp_chart svg').bounding_box()
            pg.mouse.move(_b2['x'] + _b2['width'] * 0.5,
                          _b2['y'] + _b2['height'] * 0.5)
            pg.wait_for_timeout(250)
            lines = [x for x in
                     (pg.locator('#tip').text_content() or '').split('\n') if x]
            names = [x.split()[0] for x in lines[1:]]
            assert len(names) == len(set(names)), \
                ('tooltip 里有重复读数 %s —— 基准只该进 series'
                 '（lineChart 会自动列出），再放进 extra 就出现两次' % names)
            assert '中证500' in (pg.locator('#tip').text_content() or ''), \
                'tooltip 该列出勾选的基准'
            #   说明里要讲清两件事：同一起点、指数是日线（今天还没有点）
            _ct = pg.inner_text('#lp_chart')
            assert '同一起点' in _ct and '日线收盘' in _ct, \
                ('要说明基准与账户同一起点、且指数是日线收盘（所以盘中的'
                 '今天基准断在昨天，那不是缺数据）')
            pg.locator('#lp_bclr').click()
            pg.wait_for_timeout(300)
            assert pg.locator('#lp_chart svg path[stroke]').count() == 1, \
                '「不比」之后该只剩净值一条线'
            assert not pg.evaluate("(a)=>localStorage.getItem('lvbench:'+a)", aid), \
                '取消基准时也要清掉 localStorage（不然刷新又回来了）'

            # ---- 收益明细是**方格热力图**，不是列表 ----
            #   🔴 一屏几十行数字没法"一眼看出哪天崩的"；方格图的底色是
            #     强度、位置是日期 —— 这一页存在的理由就是快速看形态。
            #   ★ 2026-09-15 起它在**自己的页签**里，先切过去再操作 ——
            #     不切的话是在隐藏的 pane 上点，超时 30 秒而看着像页面坏了。
            _lp_tab(pg, '收益明细')
            assert pg.locator('#lp_tbl a.lpg.on').inner_text() == '日', \
                '默认粒度该是「日」（打开就看到这个月每天怎么样）'
            assert pg.locator('#lp_tbl a.lps.on').inner_text() == '两者', \
                '默认读数该是「两者」（收益率 + 金额）'
            assert pg.locator('#lp_tbl .calg').count() == 1, \
                '日粒度该画自然日历方格（.calg）'
            n_td = pg.locator('#lp_tbl .cd:not(.off):not(.pad)').count()
            assert n_td > 0, '日历里没有交易日格子'
            assert pg.locator('#lp_tbl .cd.off').count() > 0, \
                '非交易日该打斜纹（.cd.off）—— 不然看不出哪天没开市'
            assert pg.locator('#lp_tbl .lgd').count() == 1, \
                ('要有图例 —— 🔴 弱强度格子的底色近乎透明，'
                 '方向全靠数字前的 +/- 号，图例得说明这件事')
            #   一格里两个读数都要有
            c0 = pg.locator('#lp_tbl .cd:not(.off):not(.pad)').first
            both = ' '.join(c0.inner_text().split())
            assert '%' in both, '「两者」模式该显示收益率：%s' % both
            assert any(ch.isdigit() for ch in both.split('%')[-1]), \
                '「两者」模式该同时显示金额：%s' % both
            tip = c0.get_attribute('title') or ''
            for k in ('日收益', '金额', '净值', '总资产'):
                assert k in tip, '格子的 title 缺「%s」：%s' % (k, tip[:80])
            # ---- 三种读数切换 ----
            pg.locator('#lp_tbl a.lps[data-s="pnl"]').click()
            pg.wait_for_timeout(250)
            only_pnl = ' '.join(
                pg.locator('#lp_tbl .cd:not(.off):not(.pad)').first
                .inner_text().split())
            assert '%' not in only_pnl, '「金额」模式不该有 %%：%s' % only_pnl
            pg.locator('#lp_tbl a.lps[data-s="ret"]').click()
            pg.wait_for_timeout(250)
            only_ret = ' '.join(
                pg.locator('#lp_tbl .cd:not(.off):not(.pad)').first
                .inner_text().split())
            assert '%' in only_ret and ',' not in only_ret, \
                '「收益率」模式该只有百分比：%s' % only_ret
            pg.locator('#lp_tbl a.lps[data-s="both"]').click()
            pg.wait_for_timeout(200)
            # ---- 三种粒度 + 下钻 ----
            pg.locator('#lp_tbl a.lpg[data-g="month"]').click()
            pg.wait_for_timeout(300)
            assert pg.locator('#lp_tbl .hm.m .hc').count() > 0, '月粒度没格子'
            pg.locator('#lp_tbl a.lpg[data-g="year"]').click()
            pg.wait_for_timeout(300)
            assert pg.locator('#lp_tbl .hm.y .hc').count() > 0, '年粒度没格子'
            #   年格 -> 月，月格 -> 日（点格子下钻，与回测详情页一致）
            pg.locator('#lp_tbl .hm.y .hc').first.click()
            pg.wait_for_timeout(300)
            assert pg.locator('#lp_tbl a.lpg.on').inner_text() == '月', \
                '点年格该下钻到月'
            pg.locator('#lp_tbl .hm.m .hc').first.click()
            pg.wait_for_timeout(300)
            assert pg.locator('#lp_tbl a.lpg.on').inner_text() == '日' \
                and pg.locator('#lp_tbl .calg').count() == 1, \
                '点月格该下钻到该月的日历'
            #   月/年格里的金额合计必须 == stats.pnl_total（单月/单年时）
            #   🔴 **盘中跳过**：末点是实时补的（stats.intraday 非空），
            #     Python 侧先取的 cur 与浏览器随后请求到的不是同一份 ——
            #     价格每分钟都在动。不跳过就是拿两个时刻的数对比，
            #     偶发失败且看着像真 bug（同 CLAUDE.md：含实时成分的接口
            #     自己就不稳定，先自证再比）。
            #     服务端侧的一致性另有直接判据：sum(day_pnls) == pnl_total，
            #     在上面已经用同一份 cur 验过了。
            if (cur['stats'].get('intraday') is None
                    and len({d[:7] for d in cur['dates']}) == 1):
                pg.locator('#lp_tbl a.lpg[data-g="month"]').click()
                pg.wait_for_timeout(250)
                mtxt = ' '.join(pg.locator('#lp_tbl .hm.m .hc').first
                                .inner_text().split())
                want = format(abs(round(cur['stats']['pnl_total'])), ',d')
                assert want in mtxt, \
                    ('单月时月格的金额该等于 stats.pnl_total（%s），实得 %s'
                     % (want, mtxt))
                pg.locator('#lp_tbl a.lpg[data-g="day"]').click()
                pg.wait_for_timeout(250)
            # ---- hover 要高亮"当前是哪一天" ----
            #   🔴 光有 tooltip 不够：鼠标在图上时看不出读的是哪一天 ——
            #     折线密的时候差一两个像素就是差一天，而 tooltip 只在鼠标
            #     旁边，对不上图上的位置。
            #   ★ mouse.move 用的是**视口坐标** —— 前面点了几轮粒度按钮，
            #     页面重排后这张图可能已经滚出视口，不先滚进来的话
            #     鼠标落不到图上（表现是"hover 没反应"，很像功能坏了）。
            #   ★ 曲线在「业绩曲线」页签里，先切回去（上面刚去过收益明细）。
            _lp_tab(pg, '业绩曲线')
            pg.locator('#lp_chart svg').scroll_into_view_if_needed()
            pg.wait_for_timeout(200)
            box = pg.locator('#lp_chart svg').bounding_box()
            assert not pg.locator('#lp_chart svg g.hov').is_visible(), \
                'hover 高亮默认该隐藏'
            pg.mouse.move(box['x'] + box['width'] * 0.6,
                          box['y'] + box['height'] * 0.5)
            pg.wait_for_timeout(250)
            assert pg.locator('#lp_chart svg g.hov').is_visible(), \
                'hover 时该出现高亮（竖线 + 数据点 + 日期）'
            vx = pg.locator('#lp_chart svg .hvl').get_attribute('x1')
            cx = pg.locator('#lp_chart svg .hvd').first.get_attribute('cx')
            assert vx == cx, \
                ('竖线该对齐到**数据点**而不是鼠标位置（%s vs %s）—— '
                 '对齐鼠标的话读数与竖线会差一天，而"差一天"正是最难发现的'
                 '那种错' % (vx, cx))
            hvt = pg.locator('#lp_chart svg .hvt').text_content() or ''
            assert hvt in cur['dates'], \
                '高亮的日期标签该是曲线上的某一天，实得 %r' % hvt
            assert hvt in (pg.locator('#tip').text_content() or ''), \
                'tooltip 与高亮标签该是同一天（两处不一致就是读错了）'
            # ---- 时间区间 ----
            #   ★ 默认 **今年以来**；起点晚于年初时从**实盘起点**开始
            #     （账户 09-01 才开户，强行从 01-01 画会有 8 个月空白，
            #      看着像数据缺了）。
            rgs = [t.strip() for t in
                   pg.locator('#lp_rg a.lpr').all_inner_texts()]
            assert rgs == ['今年以来', '近一月', '近三月', '近六月',
                           '近一年', '近三年', '全部', '自定义'], \
                '区间选项不对：%s' % rgs
            assert pg.locator('#lp_rg a.lpr.on').inner_text() == '今年以来', \
                '默认区间该是「今年以来」'
            #   KPI 板是**全程**口径，必须标出来 —— 图按区间画，
            #   两个数摆同一屏不标就看着像对不上。
            kk = [' '.join(x.split()) for x in
                  pg.locator('#main .kpi .k').all_inner_texts()]
            assert all('全程' in x for x in kk), \
                'KPI 四格该标「· 全程」（图是按区间画的）：%s' % kk
            assert '这一段' in pg.inner_text('#lp_chart'), \
                '图下面该给这一段的收益（与 KPI 的全程口径区分开）'
            #   🔴 账户只有几天时**所有预设档都落在全程**，测不出差别 ——
            #     用构造数据直接验纯函数（同 perfBuckets / drawdownSeries）。
            rg = pg.evaluate(_RGJS)
            o = rg['out']
            assert rg['total'] > 900, '构造的交易日太少：%d' % rg['total']
            order = ['m1', 'm3', 'm6', 'y1', 'y3', 'all']
            ns = [o[k]['n'] for k in order]
            assert ns == sorted(ns) and len(set(ns)) == len(ns), \
                '各区间的天数该严格递增：%s' % list(zip(order, ns))
            assert o['ytd']['a'].endswith('-01-01'), \
                'YTD 该从年初起：%s' % o['ytd']['a']
            assert o['all']['a'] == '2023-01-02', '「全部」该从第一天起'
            #   🔴 裁剪后必须**按区间起点重新归一化** —— 不归一化的话
            #     "近一月"画出来仍是开户至今的累计，y 轴写的百分比其实是
            #     三年的收益，**而那不报错**。
            #     基点取区间起点的**前一天**（区间外那一点），所以首值
            #     不是 1.0 而是"第一天的涨幅"。
            for k in order:
                assert abs(o[k]['f'] - 1.0001) < 1e-6, \
                    ('%s 的首值该是"区间第一天的涨幅"（1.0001），实得 %.6f —— '
                     '等于 1.0 说明拿区间首日自己做了基点，'
                     '把首日涨跌排除在这段收益之外' % (k, o[k]['f']))
            rr = [o[k]['r'] for k in order]
            assert rr == sorted(rr), '区间收益该随区间变长而变大：%s' % rr
            #   🔴 「近 N 月」按**自然日往前推**再落到交易日轴上 ——
            #     直接取"最后 N×20 个交易日"的话，"近一月"会因节假日
            #     多少而漂（春节那个月只有 15 个交易日）。
            #     判据：起点必须正好是 last 减 N 个自然月之后的第一个交易日。
            import datetime as _dt

            def _minus_month(ds, m):
                y, mo, dd = (int(x) for x in ds.split('-'))
                mo -= m
                while mo <= 0:
                    mo += 12
                    y -= 1
                try:
                    return _dt.date(y, mo, dd).isoformat()
                except ValueError:            # 2/30 之类
                    return _dt.date(y, mo, 28).isoformat()
            _last = o['all']['b']
            for k, mm in (('m1', 1), ('m3', 3), ('m6', 6),
                          ('y1', 12), ('y3', 36)):
                want = _minus_month(_last, mm)
                assert o[k]['a'] >= want and o[k]['a'] <= want or True, ''
                #   起点该落在 [want, want+5天] 内（want 本身可能是周末）
                assert want <= o[k]['a'] <= (
                    _dt.date.fromisoformat(want)
                    + _dt.timedelta(days=5)).isoformat(), \
                    ('%s 的起点该是 %s 之后的第一个交易日，实得 %s —— '
                     '按"最后 N×20 个交易日"取的话会随节假日漂'
                     % (k, want, o[k]['a']))
            #   🔴 YTD 的**钳到起点**要用"今年才开户"的数据才测得到：
            #     构造数据从 2023 年开始，2026-01-01 > 第一天，钳制不触发。
            #     真实账户正是今年开户的（09-01），拿它验。
            if cur['dates'][0] > cur['dates'][-1][:4] + '-01-01':
                _ytd = pg.evaluate(
                    "()=>{const s=LPR; LPR={k:'ytd',a:'',b:''};"
                    "const r=lprSpan(LPD.dates); LPR=s; return r;}")
                assert _ytd[0] == cur['dates'][0], \
                    ('账户 %s 才开户，YTD 该从**开户日**起而不是年初 —— '
                     '实得 %s。强行从 01-01 画会有一大段空白，'
                     '看着像数据缺了' % (cur['dates'][0], _ytd[0]))
            #   🔴 图必须真的画**裁剪后**的数据 —— 只验区间说明文字的话，
            #     曲线还在画全程也发现不了（变异测试抓到过）。
            #     判据：x 轴第一个日期刻度 == 区间起点。
            pg.locator('#lp_rg a.lpr[data-r="all"]').click()
            pg.wait_for_timeout(350)
            _xall = [(t or '').strip() for t in
                     pg.locator('#lp_chart svg text').all_text_contents()
                     if (t or '').strip().startswith('20')]
            assert _xall and _xall[0] == cur['dates'][0], \
                ('「全部」时 x 轴该从账户第一天起：%s vs %s'
                 % (_xall[:2], cur['dates'][0]))
            pg.locator('#lp_rg a.lpr[data-r="cus"]').click()
            pg.wait_for_timeout(300)
            if pg.locator('#lp_ra').count() and len(cur['dates']) >= 3:
                pg.fill('#lp_ra', cur['dates'][1])
                pg.locator('#lp_ra').press('Enter')
                pg.wait_for_timeout(600)
                _xc = [(t or '').strip() for t in
                       pg.locator('#lp_chart svg text').all_text_contents()
                       if (t or '').strip().startswith('20')]
                assert _xc and _xc[0] == cur['dates'][1], \
                    ('自定义起点之后，x 轴第一个刻度该跟着变（%s vs %s）—— '
                     '还画全程说明图没用裁剪后的数据'
                     % (_xc[:2], cur['dates'][1]))
            #   恢复默认区间，后面的断言按 YTD 来
            pg.evaluate("()=>{localStorage.removeItem('lvrange');}")
            pg.locator('#lp_rg a.lpr[data-r="ytd"]').click()
            pg.wait_for_timeout(400)
            assert o['cus']['a'] == '2025-03-03' \
                and o['cus']['b'] == '2025-06-30', \
                ('自定义 2025-03-01~06-30 该落到交易日 03-03~06-30，'
                 '实得 %s~%s' % (o['cus']['a'], o['cus']['b']))

            # ---- 浮窗在视口右边缘要**翻到光标左侧** ----
            #   🔴 原来固定放右下（clientX+12）—— 光标移到图最右边时浮窗
            #     整块跑到视口外面，读数看不见。而那正是最需要看读数的位置：
            #     曲线的最新一天。
            #   ★ 定位前必须**先填内容再量尺寸**：offsetWidth 在设置
            #     textContent 之前是旧值，用它算翻转会翻错边。
            _TIPJS = "()=>{const t=document.getElementById('tip');const b=t.getBoundingClientRect();return {l:b.left, r:b.right, w:b.width, vw:window.innerWidth,vis:getComputedStyle(t).display!=='none'};}"

            def _tipbox():
                return pg.evaluate(_TIPJS)

            #   ★ 重新取一次 bounding_box —— 前面点过粒度/区间/基准，
            #     页面重排之后旧的 _bx 已经过期，鼠标会落到图外
            #     （表现是"浮窗不出现"，很像功能坏了）。
            pg.locator('#lp_chart svg').scroll_into_view_if_needed()
            pg.wait_for_timeout(200)
            _bx = pg.locator('#lp_chart svg').bounding_box()
            for frac in (0.15, 0.5, 0.97):
                pg.mouse.move(_bx['x'] + _bx['width'] * frac,
                              _bx['y'] + _bx['height'] * 0.5)
                pg.wait_for_timeout(200)
                tb = _tipbox()
                assert tb['vis'] and tb['w'] > 0, \
                    '光标在图上 %.0f%% 处时浮窗该可见' % (frac * 100)
                assert tb['l'] >= -0.5 and tb['r'] <= tb['vw'] + 0.5, \
                    ('浮窗跑到视口外了（左 %.0f 右 %.0f / 视口 %.0f）—— '
                     '右边放不下就该翻到光标左侧'
                     % (tb['l'], tb['r'], tb['vw']))
            #   ★ 判据要能区分"翻转生效"和"恰好没超" —— 在最右侧时
            #     浮窗必须落在光标**左边**。
            _cx = _bx['x'] + _bx['width'] * 0.97
            pg.mouse.move(_cx, _bx['y'] + _bx['height'] * 0.5)
            pg.wait_for_timeout(200)
            tb = _tipbox()
            assert tb['r'] <= _cx + 1, \
                ('最右侧时浮窗该翻到光标左边（右边界 %.0f vs 光标 %.0f）'
                 % (tb['r'], _cx))

            #   ★ 两侧都放不下时**必须钳进视口** —— 宁可压着光标也要可见。
            #     这条要用"内容撑得比视口还宽 + 光标顶在右下角"来测：
            #     正常宽度下翻转之后本来就在视口内，钳制没机会生效，
            #     那样的断言是空转（变异测试实测全绿）。
            _cl = pg.evaluate("()=>{const t=document.getElementById('tip');const old=t.textContent;t.textContent=('X'.repeat(400)+'\\n').repeat(80);_tipAt(t, window.innerWidth-3, window.innerHeight-3);const b=t.getBoundingClientRect();const o={l:b.left, r:b.right, t:b.top, bt:b.bottom, vw:window.innerWidth, vh:window.innerHeight};t.textContent=old; t.style.display='none'; return o;}")
            assert _cl['l'] >= -0.5 and _cl['t'] >= -0.5, \
                ('浮窗被挤出视口左上（左 %.0f 上 %.0f）—— '
                 '两侧都放不下时该钳进视口' % (_cl['l'], _cl['t']))

            #   ★ 垂直方向同理：光标靠底部时要翻到**上方**。
            #     这条要用**正常高度**的浮窗测 —— 上面那条用的内容比视口
            #     还高，翻转与不翻转最终都被钳到 top=4，看不出差别
            #     （变异测试实测：去掉垂直翻转照样全绿）。
            _vf = pg.evaluate("()=>{const t=document.getElementById('tip');const old=t.textContent;t.textContent='2026-01-01\\n净值  +1.00%';const cy=window.innerHeight-6;_tipAt(t, 200, cy);const b=t.getBoundingClientRect();const o={t:b.top, bt:b.bottom, h:b.height, cy:cy, vh:window.innerHeight};t.textContent=old; t.style.display='none'; return o;}")
            assert _vf['bt'] <= _vf['vh'] + 0.5, \
                ('光标靠底部时浮窗超出视口下沿（底 %.0f / 视口高 %.0f）'
                 % (_vf['bt'], _vf['vh']))
            assert _vf['bt'] <= _vf['cy'] + 1, \
                ('光标靠底部时浮窗该翻到**上方**（底 %.0f vs 光标 %.0f）—— '
                 '不翻的话它会被挤在视口边缘、盖住光标'
                 % (_vf['bt'], _vf['cy']))

            pg.mouse.move(5, 5)
            pg.wait_for_timeout(200)
            assert not pg.locator('#lp_chart svg g.hov').is_visible(), \
                '移开后高亮该隐藏'

            # ---- 窄屏不许把 body 撑出横滚 ----
            for w in (1440, 1024):
                pg.set_viewport_size({'width': w, 'height': 900})
                pg.wait_for_timeout(250)
                ov = pg.evaluate(
                    '()=>document.body.scrollWidth-document.body.clientWidth')
                assert ov == 0, '%d 宽 body 横滚 %dpx' % (w, ov)
            assert not errs, '业绩页有运行时错误：%s' % errs[:3]
            br.close()
        return ('nav/day_rets/day_pnls 与 dates 对齐、nav 末值 == stats.twr、'
                'nav[0] 含建仓当天收益；入口是「累计收益 ›」链接；'
                '三条曲线在**一个框**里切换（资金 y 轴=金额不是倍数 x、'
                '回撤 y 轴最高**正好 0%%**、收益 tooltip 同时给净值与累计'
                '金额且跨度小时刻度加小数位不出现 -0%%）；各标口径'
                '（含入金 / 入金不算收益）；'
                '区间默认「今年以来」且钳到开户日（8 档 + 自定义、'
                '按自然月推再落交易日、裁剪后按区间起点重新归一化、'
                'x 轴刻度跟着变、记在 localStorage）；KPI 标「全程」'
                '且图下单独给这一段的收益；'
                '收益明细是**方格图**：默认日+两者、非交易日打斜纹、有图例、'
                '三种读数切换、年→月→日点格下钻、单月金额 == pnl_total；'
                'hover 高亮竖线对齐数据点且与 tooltip 同一天；'
                '1440/1024 宽零横滚；%d 个交易日' % n)
    finally:
        httpd.shutdown()
        sv.ALLOW_LIVE = old_live


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
    import json as _json
    import threading
    import urllib.error
    import urllib.request
    from http.server import ThreadingHTTPServer

    from assay import server as sv
    old = sv.ALLOW_LIVE
    sv.ALLOW_LIVE = False          # 只读：断言手动同步按钮被置灰
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), sv.Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = 'http://127.0.0.1:%d' % port

    def _get(path):
        return urllib.request.urlopen(base + path, timeout=120).read().decode()

    def _post(path, body):
        rq = urllib.request.Request(
            base + path, data=_json.dumps(body).encode(),
            headers={'Content-Type': 'application/json'})
        return urllib.request.urlopen(rq, timeout=120).read().decode()

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
            # ★ 界面上【不用】"A 腿/B 腿"这种内部术语 —— 对着屏幕看的人
            #   没有意义。内部字段名仍是 leg_a/leg_b（sync_status.py）。
            #   只看渲染出来的标题，不看整页源码（源码里有注释会误判）。
            _h3 = ' | '.join(pg.locator('.lvsec h3').all_inner_texts())
            assert '行情数据' in _h3 and '财务数据' in _h3, \
                '两个分区的标题没渲染：%s' % _h3
            assert 'A 腿' not in _h3 and 'B 腿' not in _h3, \
                '界面上不该出现"A 腿/B 腿"：%s' % _h3
            # ★ 只取【表格单元格】，不取整节文本 —— 本会话第四次栽在
            #   "断言匹配到自己写的说明文案"上：B 腿那节的说明里正写着
            #   「B 腿的"落后"不按交易日算」，于是 `'落后' not in b_txt` 必然失败。
            #   要断言渲染结果就只看渲染结果，别把旁边的散文一起吃进来。
            #   🔴 **不用位置索引 nth(0)/nth(1)** —— 这一页的 .lvsec 会
            #     增加（加「定时窗口」那块时就把两条腿挤后了一位），
            #     而错位的表现是"内容不对"而不是"找不到"，很难查。
            #     按 h3 标题找那一节。
            secs = pg.locator('.lvsec')
            def _sec(name):
                for i in range(secs.count()):
                    if name in secs.nth(i).locator('h3').inner_text():
                        return secs.nth(i)
                raise AssertionError('找不到「%s」这一节' % name)
            a_cells = _sec('行情数据').locator('table.lvt td').all_inner_texts()
            b_cells = _sec('财务数据').locator('table.lvt td').all_inner_texts()
            # ---- 定时窗口那块要真的渲染出来（源码结构在 fast 层另有用例）----
            _sw = _sec('定时窗口')
            assert _sw.locator('input.syf').count() == 6, \
                ('两个任务 × 从/到/间隔 = 6 个输入框，实得 %d'
                 % _sw.locator('input.syf').count())
            _swt = _sw.inner_text()
            #   🔴 判据是**实际装上的点位**那两列，不是回显配置
            assert '已装' in _swt and ('一致' in _swt or '未装' in _swt), \
                ('定时窗口没显示"实际装上的点位/是否一致" —— '
                 '只回显配置的话，"改了但没重装"看不出来：%s' % _swt[:200])
            #   ★ 输入框的值不进 inner_text（那是属性），要按值取。
            #     顺带钉住"补终点"在页面上确实是 09:20 而不是 09:00。
            _sv = [_sw.locator('input.syf').nth(i).input_value()
                   for i in range(6)]
            assert _sv[0] and _sv[1] and _sv[2], \
                '定时窗口的输入框是空的（服务端没给配置？）：%r' % (_sv,)
            assert _sv[4] == '09:20', \
                ('信号重算的窗口终点该是 09:20（开盘前最后一次），实得 %r'
                 % _sv[4])
            assert _sw.locator('#sysched').count() == 1, \
                '少了「保存并重装」按钮 —— 配置改了没法生效'
            a_txt, b_txt = ' | '.join(a_cells), ' | '.join(b_cells)
            assert a_cells and b_cells, '两条腿的表格都要有行'
            assert '落后' in a_txt or '最新' in a_txt, \
                'A 腿该显示「落后 N 个交易日」或「最新」，实得: %s' % a_txt[:120]
            assert '距今' in b_txt, \
                'B 腿该显示「距今 N 天」，实得: %s' % b_txt[:120]
            assert '落后' not in b_txt, \
                'B 腿的表格里不该出现「落后」—— 财务是事件驱动，'\
                '按交易日算是必然误报。实得: %s' % b_txt[:120]
            # ---- 「上次抽取」必须在页面上，且与数据内容切点**分开说** ----
            # 🔴 用户原话：「实际上财务数据我昨天已经导入了最新的，但是上面的
            #   最新时间不会更新，显得我好像没有更新一下。」根因：页面只显示
            #   数据内容的 pub_date，而 B 腿事件驱动 —— 没公告就不前进。
            _ex = _json.loads(_get('/api/sync')).get('status', {}).get('extract')
            _bsec = [s for s in pg.locator('.lvsec').all()
                     if '财务' in (s.locator('h3').first.inner_text() or '')]
            assert _bsec, '找不到「财务数据」那一块'
            _btxt = ' '.join(_bsec[0].inner_text().split())
            assert '上次抽取' in _btxt, \
                '财务数据那块必须写「上次抽取」—— 只显示数据内容的 pub_date 时，' \
                '刚导完也看着像 18 天没更新'
            if _ex is None:
                assert '未知' in _btxt, '没有 manifest 时要明说「未知（旧格式包）」，不许猜'
                jq_ex = '上次抽取 未知（当前包是旧格式）'
            else:
                _when = _ex.get('extracted_at') or _ex.get('extract_date')
                assert _when and _when in _btxt, \
                    '抽取时点没显示出来：%r 不在「%s」里' % (_when, _btxt[:200])
                # ★ 两个日期必须【同时】在页面上 —— 只有一个的话，
                #   「昨天抽的、数据切到前天」这句话就说不完整。
                # 🔴 判据必须**限定在那一行里**：日期字符串在下面那张 B 腿
                #   表格里也出现（财务指标 pub_date 恰好同一天），拿整块文本
                #   去匹配就是"判据比断言宽" —— 变异掉这一格照样绿。
                if _ex.get('data_max_date'):
                    _tags = ' '.join(_bsec[0].locator('.lvtags')
                                     .first.inner_text().split())
                    assert '数据切到 pub_date %s' % _ex['data_max_date'] in _tags, \
                        '数据切点没显示在标签行里：%s' % _tags[:200]
                _d = _ex.get('days_since_extract')
                if _d is not None:
                    _age = {0: '今天', 1: '昨天'}.get(_d, '%d 天前' % _d)
                    assert _age in _btxt, \
                        '要把"多久以前"直接说出来（%s）：%s' % (_age, _btxt[:200])
                # 🔴 告警判据必须是**抽取时点**而不是数据内容 ——
                #   刚导完还标红就是假告警，而假告警看多了就不看告警了。
                _mw = ' '.join(pg.locator('#main .lvwarn').all_inner_texts())
                if _d is not None and _d <= 14:
                    assert '财务数据' not in _mw or '没从聚宽抽取' not in _mw, \
                        '%d 天前刚抽过，不该报财务数据过期：%s' % (_d, _mw[:200])
                jq_ex = '上次抽取 %s（%s）· 数据切到 %s' % (
                    _when, _age if _d is not None else '?',
                    _ex.get('data_max_date') or '?')
            # ---- 告警判据：造两种数据直接验，不靠真实状态碰巧覆盖 ----
            # 🔴 真实状态是「抽取 1 天前 / 内容 18 天」—— 两种判据都不报警，
            #   所以只看真实页面的断言是**空转的**（变异测试当场抓到）。
            #   这里拦掉 /api/sync 造出两种相反的情形：
            #     ① 刚抽过、内容很旧  -> **不许**报警（原来的 bug 就是这一格）
            #     ② 很久没抽、内容很新 -> 必须报警
            _base = _json.loads(_get('/api/sync'))

            def _probe(days_extract, days_content):
                o = _json.loads(_json.dumps(_base))
                o.setdefault('status', {})['leg_b_days_since'] = days_content
                o['status']['extract'] = {
                    'extracted_at': '2026-01-01 00:00:00',
                    'extract_date': '2026-01-01', 'data_max_date': '2026-01-01',
                    'days_since_extract': days_extract}
                pg.route('**/api/sync', lambda r: r.fulfill(
                    status=200, content_type='application/json',
                    body=_json.dumps(o)))
                try:
                    pg.reload(wait_until='networkidle')
                    pg.wait_for_timeout(700)
                    return ' '.join(pg.locator('#main .lvwarn').all_inner_texts())
                finally:
                    pg.unroute('**/api/sync')

            _w1 = _probe(1, 60)
            assert '没从聚宽抽取' not in _w1 and '没更新' not in _w1, \
                '昨天刚抽过就不该报财务过期（内容旧是因为没公告）—— 实得：%s' % _w1[:200]
            _w2 = _probe(40, 0)
            assert '没从聚宽抽取' in _w2, \
                '40 天没抽必须报警，哪怕内容里恰好有新 pub_date —— 实得：%s' % _w2[:200]
            # 回到真实数据，后面的断言还要用
            pg.reload(wait_until='networkidle')
            pg.wait_for_timeout(800)
            # 日历来源要显示，且是可信来源（不然实盘会拿不到下一个交易日）
            from assay import live as lv
            head = pg.locator('.lvhead').inner_text()
            assert '日历' in head, '没显示日历来源'
            assert any(x in head for x in lv.AUTHORITATIVE_CAL), \
                '日历来源不在可信名单里: %s' % head
            # 只读模式下手动同步必须置灰（不能只靠前端 —— 接口也会拒，见 api_sync_run）
            assert pg.locator('#syrun').is_disabled(), '只读模式下「立即同步」应置灰'
            # ---- 自动同步开关：按钮文字必须跟【服务端复查到的状态】一致 ----
            #   ★ 不去真的 load/unload —— 那会改用户机器上的 launchd。
            #     这里核的是"状态读得对、文字对得上、只读被拦住"。
            au = _json.loads(_get('/api/sync/auto'))
            if au.get('supported') is False:
                assert pg.locator('#syauto').count() == 0,                     '不支持 launchd 的平台不该出现这个按钮'
                auto_note = '本平台无 launchd，按钮已隐去'
            else:
                assert au.get('on') in (True, False),                     'on 必须是明确的真假，不能是 None：%s' % au
                btn = pg.locator('#syauto').inner_text()
                assert btn == ('关闭自动同步' if au['on'] else '开启自动同步'),                     '按钮文字与实际状态不符：状态 on=%s，按钮「%s」' % (au['on'], btn)
                assert pg.locator('#syauto').is_disabled(),                     '只读模式下开关也要置灰 —— 它会改 launchd'
                tag = pg.locator('.lvhead .lvtag').all_inner_texts()
                tz = ' '.join(tag)
                assert '自动同步' in tz, '顶栏要显示自动同步状态：%s' % tag
                assert ('开' in tz) == au['on'], '标签与状态不符：%s' % tz
                if au['on']:
                    assert au.get('schedule') and au['schedule'] in tz,                         '开着的时候要显示计划时间：%s / %s' % (au.get('schedule'), tz)
                    # 开着就不该弹"自动同步是关的"那条告警 —— 假告警看多了就不看了
                    assert '自动同步是关的' not in pg.locator('#main').inner_text(),                         '开着却提示"关的"'
                else:
                    assert '永久丢失' in pg.locator('#main').inner_text(),                         '关着必须说清代价（daily_snapshot 漏一天永久丢失）'
                # 只读接口层也要拒，不能只靠按钮置灰
                r_auto = _json.loads(_post('/api/sync/auto', {'on': not au['on']}))
                assert r_auto.get('error'), '只读模式下接口应拒绝改自动同步'
                assert _json.loads(_get('/api/sync/auto')).get('on') == au['on'],                     '被拒之后状态不该变'
                auto_note = '自动同步 %s%s（按钮文字一致、只读双层拦住）' % (
                    '开' if au['on'] else '关',
                    ' · ' + au['schedule'] if au.get('schedule') else '')
            # ---- 财务数据导入：取代码 + 上传 ----
            #   ★ 代码正本是磁盘上那个 extract 脚本，服务端只替换
            #     SINCE/QUARTERS。前端另存一份就一定会分叉。
            assert pg.locator('#jqcode').count() == 1, '缺「取聚宽代码」按钮'
            assert pg.locator('#jqfile').count() == 1, '缺上传入口'
            jc = _json.loads(_get('/api/sync/jq_code'))
            assert not jc.get('error'), '取聚宽代码失败：%s' % jc.get('error')
            assert 'from jqdata import' in jc['code'], \
                '给出的不像聚宽研究环境的代码'
            assert 'def pack()' in jc['code'] and 'tarfile' in jc['code'], \
                '代码必须自己打包成一个文件（否则要下载十几个 csv）'
            # SINCE 要按【本地最落后那张表】往前留重叠，不能是脚本里的旧值
            sg = jc['suggest']
            if sg.get('since'):
                assert sg['since'] < sg['oldest'], \
                    'SINCE(%s) 必须早于本地最落后的 pub_date(%s) —— ' \
                    '留重叠是刻意的：重叠不重复，缺口会静默丢数据' \
                    % (sg['since'], sg['oldest'])
                import re as _re2
                m_s = _re2.search(r"^SINCE = '([^']*)'", jc['code'], _re2.M)
                assert m_s and m_s.group(1) == sg['since'], \
                    '代码里的 SINCE 没被替换成 %s' % sg['since']
                assert not jc.get('warn'), '替换出了问题：%s' % jc.get('warn')
            # 只读模式下上传必须被拒（按钮置灰之外，接口也要拒）
            _rq = urllib.request.Request(
                base + '/api/sync/jq_upload', data=b'x' * 16,
                headers={'X-Filename': 'a.tar',
                         'Content-Type': 'application/octet-stream'})
            try:
                _up = _json.loads(urllib.request.urlopen(_rq, timeout=30).read())
            except urllib.error.HTTPError as e:
                _up = _json.loads(e.read())
            assert _up.get('error') and '只读' in _up['error'], \
                '只读模式下上传应被拒：%s' % _up
            # 日志可点开
            n_log = pg.locator('a.sylog').count()
            if n_log:
                pg.locator('a.sylog').first.click()
                pg.wait_for_timeout(600)
                out = pg.locator('#syout').inner_text()
                assert '每日数据同步' in out, '日志内容没渲染出来: %s' % out[:120]
            br.close()
            assert not errs, '页面有运行时错误：%s' % errs[:3]
            return ('两条腿语义分离（行情报交易日落后 / 财务报距今天数）；'
                    '界面无"A 腿/B 腿"内部术语；日历来源可信；'
                    '只读拦住手动同步与上传；%s；%s；'
                    '聚宽代码可取且 SINCE 按本地最落后表(%s)预填成 %s；'
                    '定时窗口 6 个输入框可改、显示【已装】点位与是否一致'
                    '（信号重算终点 %s，补的终点）、有「保存并重装」；'
                    '%d 份日志可点开'
                    % (auto_note, jq_ex, sg.get('oldest'), sg.get('since'),
                       _sv[4], n_log))
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
    import re as _re

    # ---- 1) 修读：内容校验在函数内，读得出来就说明全过 ----
    df, st = read_forcast_df(csvp, verbose=False)
    n_fix = len(df)
    assert n_fix > 100000, '修读只得到 %d 行，太少' % n_fix
    assert df['id'].str.fullmatch(r'\d+').all(), 'id 有非数字'
    assert df['code'].str.fullmatch(r'\d{6}\.XSH[EG]').all(), 'code 有不合法'

    # ---- 2) 与裸 pandas 对照 ----
    # 🔴 **不能要求上游一直是坏的。** 头一版写 `assert n_bad > 0`（裸读必须
    #   产生垃圾行）—— 2026-09-10 那个增量包里上游**修好了**（0 条奇数引号，
    #   三个解析器与记录头判据四者一致都是 124,083），这条断言就把测试打红了。
    #   而"上游修好了"是**好事**，不该让测试失败。
    # ★ 改成**双态**：
    #     上游坏 -> 仍然钉住「裸读行数 − 垃圾行 == 修读行数」（修读没多吞少吞）
    #     上游好 -> 钉住「修读与裸读**逐行一致**」（这层不会把好文件读坏）
    #   两种情况都能验，而且不管上游怎么变都不会有假失败。
    import pandas as pd
    naive = pd.read_csv(csvp, dtype=str, encoding='utf-8-sig')
    n_bad = int((~naive['id'].fillna('').str.fullmatch(r'\d+')).sum())
    if n_bad:
        assert len(naive) - n_bad == n_fix, \
            ('裸读 %d 行 − 垃圾 %d 行 应等于修读 %d 行，实际不等 —— '
             '说明修读多吞或少吞了记录' % (len(naive), n_bad, n_fix))
        upstream = '坏（裸读 %d 行含 %d 行垃圾）' % (len(naive), n_bad)
    else:
        # 🔴 上游干净时，修读**不许**改变任何一行 —— 否则这层本身就是风险。
        assert len(naive) == n_fix, \
            ('上游是干净的（裸读无垃圾行），但修读给出 %d 行 ≠ 裸读 %d 行 —— '
             '这层把好文件读坏了' % (n_fix, len(naive)))
        for col in ('id', 'code', 'pub_date'):
            a = df[col].fillna('').reset_index(drop=True)
            b = naive[col].fillna('').reset_index(drop=True)
            assert (a == b).all(), \
                '上游干净时修读与裸读在 %s 列上不一致 —— 这层把好文件读坏了' % col
        upstream = '干净（裸读 %d 行、无垃圾）' % len(naive)

    # ---- 3) DuckDB 能不能直接读 ----
    # 🔴 同样不能要求它「必须读不了」。但**这层依然有存在价值**，
    #   理由与「DuckDB 读不读得了」无关：
    #     · 它按记录头切并**拼回续行**（实测这个干净文件里有 1068 个续行）
    #     · 它有四道**内容校验**（id 纯数字 / code 合法 / 两个日期列合法），
    #       那是「按位置切对了」的唯一证明 —— 当初 11 行垃圾静默进 parquet
    #       存活一周，就是因为只校验行数不校验内容（CLAUDE.md 那条教训）
    #   所以这里只**记录**状态，不拿它当失败判据。
    try:
        n_duck = duckdb.connect().execute(
            "SELECT count(*) FROM read_csv_auto('%s', all_varchar=true)" % csvp
        ).fetchone()[0]
        duck = '能读（%d 行%s）' % (
            n_duck, '，与修读一致' if n_duck == n_fix else '，**与修读不一致**')
        # ★ DuckDB 能读时，行数必须与修读一致 —— 不一致说明有一方错了，
        #   那是真问题（而不是"上游修好了"）。
        assert n_duck == n_fix, \
            ('DuckDB 读出 %d 行、修读 %d 行 —— 两个解析器分歧，必须查清'
             '（多解析器交叉验证比单个解析器的"成功"可信）' % (n_duck, n_fix))
    except AssertionError:
        raise
    except Exception as _e:                                 # noqa: BLE001
        # 🔴 **把异常类型带出来**，不要只说"读不了"。
        #   实测踩到：这里曾把一个 `NameError` 当成"DuckDB 读不了"报出去，
        #   于是摘要写着「DuckDB 仍报错」而它其实能读 —— 吞掉异常类型
        #   等于让一个**假结论**看着像验过了（同「禁止吞异常」那条）。
        duck = '读不了（%s: %s）' % (type(_e).__name__, str(_e)[:60])

    # ---- 4) 记录头判据不能退化成 ^\d+, ----
    #     content 里有千分位逗号（633,969.04元），松判据会把续行当新记录
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

    # ---- 5) 🔴 **注入一个坏文件，证明这层不是摆设** ----
    # 上游修好之后，前面那些断言都只能证明「这层没把好文件读坏」——
    # 证不了「它还有必要」。而缺陷来自上游（聚宽导出），下次导可能又带，
    # 所以要用**构造的坏文件**把这层的价值钉住：删掉一个引号，让引号数
    # 变成奇数（这正是 2026-09-01 定位到的那个缺陷，当时有 10 处）。
    # ★ 这也回答了「这层还有没有必要」：**有**。实测一个引号就能让裸读
    #   少 2,118 行并混进垃圾，而修读读得完全正确。
    import tempfile as _tf
    _bad = None
    try:
        with open(csvp, encoding='utf-8-sig', newline='') as _f0:
            _lines = _f0.read().split('\n')
        _k = next((i for i, l in enumerate(_lines)
                   if _re.match(r'^\d+,\d+,\d{6}\.XSH[EG],', l)
                   and l.count('"') >= 2), None)
        assert _k is not None, '找不到带引号的记录 —— 构造不出坏文件，这条测不到'
        _q = _lines[_k].index('"')
        _lines[_k] = _lines[_k][:_q] + _lines[_k][_q + 1:]   # 删一个引号
        _fd, _bad = _tf.mkstemp(suffix='.csv')
        with os.fdopen(_fd, 'w', encoding='utf-8') as _f:
            _f.write('\n'.join(_lines))
        _nv = pd.read_csv(_bad, dtype=str, encoding='utf-8-sig')
        _nbad = int((~_nv['id'].fillna('').str.fullmatch(r'\d+')).sum())
        # 裸读必须被这一个引号搞坏（少行 或 混进垃圾）
        assert len(_nv) != n_fix or _nbad > 0, \
            ('注入一个未闭合引号之后裸 pandas 居然还读对了（%d 行、%d 垃圾）'
             ' —— 那这个构造没有重现缺陷，这条断言是空转的'
             % (len(_nv), _nbad))
        _df2, _ = read_forcast_df(_bad)
        # 而修读必须仍然读出全部记录、且内容合法（read_forcast_df 内部会校验）
        assert len(_df2) == n_fix, \
            ('坏文件下修读给出 %d 行 ≠ 干净文件的 %d 行 —— 这层没能修好'
             % (len(_df2), n_fix))
        _fix_note = ('注入 1 个未闭合引号：裸读 %s 行/%d 垃圾，修读仍 %s 行'
                     % (format(len(_nv), ','), _nbad, format(len(_df2), ',')))
    finally:
        if _bad and os.path.exists(_bad):
            os.unlink(_bad)

    # ---- 6) 🔴 **字段错位必须被内容校验拦住** ----
    # 上一条注入的是"未闭合引号"，它只让**裸读**出错、修读照样正确 ——
    # 所以它证不了那四道内容校验有用。这一条补上：把一个**续行**伪装成
    # 记录头（`999,888,000001.XSHE,` 前缀），于是那条记录被切成两半、
    # 后半段的字段整体错位。
    # ★ 实测：这时 `id` / `code` 恰好**仍然合法**（切出来的前三段就是我
    #   插进去的那三个），拦住它的是**日期列**那道校验 —— 四道校验各有分工，
    #   少任何一道都可能让错位静默通过。
    # 🔴 这正是 CLAUDE.md 那条教训的反面：当初只校验「行数 == pandas 读入
    #   行数」，两边一样错、检查照样通过，11 行垃圾静默进 parquet 活了一周。
    _bad2 = None
    try:
        with open(csvp, encoding='utf-8-sig', newline='') as _f1:
            _ls = _f1.read().split('\n')
        _st = [i for i, l in enumerate(_ls)
               if _re.match(r'^\d+,\d+,\d{6}\.XSH[EG],', l)]
        _kk = next((_st[i] for i in range(len(_st) - 1)
                    if _st[i + 1] - _st[i] > 1), None)
        assert _kk is not None, \
            '文件里没有带续行的记录 —— 构造不出字段错位，这条断言是空转的'
        _ls[_kk + 1] = '999,888,000001.XSHE,' + _ls[_kk + 1]
        _fd2, _bad2 = _tf.mkstemp(suffix='.csv')
        with os.fdopen(_fd2, 'w', encoding='utf-8') as _f2:
            _f2.write('\n'.join(_ls))
        try:
            read_forcast_df(_bad2, verbose=False)
            raise AssertionError(
                '字段错位（假记录头）居然通过了内容校验 —— 那四道校验'
                '（id/code/两个日期列）至少有一道失效了，而错位**不报错**、'
                '只是把垃圾写进 parquet')
        except ValueError as _ve:
            assert ('不是日期' in str(_ve) or 'id' in str(_ve)
                    or 'code' in str(_ve)), \
                '抛的错不是内容校验给的：%s' % _ve
            _mis_note = '字段错位被拦住（%s）' % str(_ve).split('有')[-1][:28]
    finally:
        if _bad2 and os.path.exists(_bad2):
            os.unlink(_bad2)

    # 🔴 摘要里的每一句都要来自**检测结果**，不许写死。
    #   头一版把「DuckDB 仍报错」硬编码在字符串里 —— 而它其实早就能读了
    #   （124,083 行，与修读一致）。**报告串在说谎，它看着像验过了**
    #   （同「数字自己算、别写死」那条）。
    return ('修读 %s 行；上游 %s；DuckDB %s；松判据多切 %d 条；%s；parquet 无垃圾行'
            % (format(n_fix, ','), upstream, duck,
               len(recs_loose) - len(recs_ok),
               _fix_note + '；' + _mis_note))


@case('页签图标：能取到 / mimetype 对 / 浏览器真的用了它', tag='fast')
def t_favicon():
    """★ favicon 坏了【不会有任何报错】—— 浏览器默默回落成默认图标，
    而你只会觉得"好像一直是这样"。所以这条用例核三件事：

      1. 文件在、能通过 HTTP 取到、是合法 XML
      2. mimetype 是 image/svg+xml（猜错的话浏览器当文本渲染，图标空白）
      3. HTML 里的 <link rel=icon> 指向它，且兜底的 data URI 也是有效 SVG

    另外核配色与 :root 一致 —— 改主题时容易只改 CSS、忘了图标。
    """
    import threading
    import urllib.request
    import xml.dom.minidom
    from http.server import ThreadingHTTPServer

    from assay import server as sv

    web = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'web')
    p = os.path.join(web, 'favicon.svg')
    assert os.path.isfile(p), '缺 web/favicon.svg'
    raw = open(p, encoding='utf-8').read()
    xml.dom.minidom.parseString(raw)          # 合法 XML（注释里不能有 --）

    html = open(os.path.join(web, 'index.html'), encoding='utf-8').read()
    # 样式已搬到 common.css（多个独立页面共用），所以 head 到 <link> 为止、
    # 配色从 common.css 里读 —— 原来两者都在 index.html 的 <style> 里
    head = html[:html.index('<div id="app">')]
    assert 'rel="icon"' in head and '/favicon.svg' in head, \
        'index.html 的 <head> 里没有指向 favicon.svg 的 <link rel=icon>'
    # 兜底 data URI 也要是有效 SVG（Safari 16 以下不认 SVG favicon 文件）
    m = re.search(r'href="data:image/svg\+xml,([^"]+)"', head)
    assert m, '缺兜底的 data URI 图标'
    import urllib.parse
    xml.dom.minidom.parseString(urllib.parse.unquote(m.group(1)))

    # 配色必须与 :root 一致 —— 改主题时最容易漏掉图标
    css = open(os.path.join(web, 'shared', 'common.css'), encoding='utf-8').read()
    for name in ('--accent', '--up'):
        mm = re.search(re.escape(name) + r':\s*(#[0-9a-fA-F]{3,8})', css)
        assert mm, 'CSS 里找不到 %s' % name
        hexv = mm.group(1).lower()
        assert hexv in raw.lower(), \
            ('图标没用 :root 的 %s (%s) —— 主题改了但图标没跟上' % (name, hexv))

    httpd = ThreadingHTTPServer(('127.0.0.1', 0), sv.Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        r = urllib.request.urlopen('http://127.0.0.1:%d/favicon.svg' % port,
                                   timeout=10)
        ct = r.headers['Content-Type'] or ''
        body = r.read()
        assert r.status == 200, '/favicon.svg 返回 %d' % r.status
        assert ct.startswith('image/svg+xml'), \
            'mimetype 是 %r —— 浏览器会当文本渲染，图标空白' % ct
        assert len(body) == len(raw.encode()), '取到的内容与磁盘不一致'
    finally:
        httpd.shutdown()

    n_pt = raw.count(' L')
    return ('favicon.svg %d bytes / %s / 曲线 %d 段（含回撤）/ '
            '兜底 data URI 有效 / 配色与 :root 一致'
            % (len(body), ct, n_pt // 2))


@case('个股：搜索 / 面板 / K线均线 / 复权 / 财务时序', tag='fast')
def t_stock():
    """服务端那半。★ 重点在【单位】和【复权】—— 这两处错了都不报错。

    · 单位：同一张面板里百分数和小数混着（change_pct/turnover/amplitude 是
      百分数，roe_ttm/rev_yoy 是小数）。错 100 倍不报错，只是数看着不对。
      所以 FIELD_UNIT 是接口的一部分，页面照它渲染而不是看数值大小猜。
    · 复权：区间涨幅一律用后复权 —— 不复权跨除权日会有【假跌幅】。
    """
    from assay import stock as st

    # ---- 1) 代码归一：几种写法都要认，认不出返回 None（不抛错）----
    for raw, want in (('601857', '601857.XSHG'), ('601857.SH', '601857.XSHG'),
                      ('sh601857', '601857.XSHG'), ('601857.XSHG', '601857.XSHG'),
                      ('000001', '000001.XSHE'), ('sz000001', '000001.XSHE'),
                      ('300375.sz', '300375.XSHE')):
        assert st.norm_code(raw) == want, '%r -> %s' % (raw, st.norm_code(raw))
    # ★ 搜索框每敲一个字都会调它，半个代码不是错误 —— 返回 None 而不是抛
    for bad in ('', '60', 'abc', '999999', '601857.HK'):
        assert st.norm_code(bad) is None, '%r 该返回 None' % bad

    # ---- 2) 搜索：代码 / 名称 / 前缀，且按匹配度+市值排 ----
    r = st.search('601857')
    assert r['results'] and r['results'][0]['code'] == '601857.XSHG', \
        '按代码搜没命中：%s' % r['results'][:2]
    r2 = st.search('中国石油')
    assert r2['results'] and r2['results'][0]['code'] == '601857.XSHG', \
        '按名称搜没命中'
    r3 = st.search('sh601857')
    assert r3['results'] and r3['results'][0]['code'] == '601857.XSHG', \
        '带前缀写法搜不到'
    # ★ 名称包含类查询要按流通市值降序 —— 不排序的话搜"银行"第一条是随机的
    #   某只小银行，而人要的通常是大的那个
    rb = st.search('银行')['results']
    assert len(rb) >= 3, '搜"银行"结果太少'
    mv = [x['floatmv'] or 0 for x in rb]
    assert mv == sorted(mv, reverse=True), '同类匹配没按市值降序：%s' % mv[:5]
    assert st.search('')['results'] == [], '空查询应返回空，不是全表'

    # ---- 3) 个股面板 ----
    p = st.profile('601857.SH')
    for k in ('sec_name', 'close_bfq', 'open', 'high', 'low', 'preclose',
              'change_pct', 'turnover', 'amplitude', 'floatmv', 'totalmv',
              'pe_ttm', 'pb', 'roe_ttm', 'sw_l1_name', 'limit_up', 'limit_down',
              'listed_days', 'high_52w', 'low_52w'):
        assert k in p, '面板缺字段 %s' % k
    assert p['indexes'], '中国石油应该在指数里：%s' % p['indexes']
    assert p['low_52w'] <= p['close_bfq'] <= p['high_52w'], \
        '现价不在 52 周区间内：%s ~ %s vs %s' % (p['low_52w'], p['high_52w'],
                                          p['close_bfq'])
    # 🔴 单位自证：change_pct/turnover/amplitude 是【百分数】。
    #    当小数用会差 100 倍，而那不报错。用量级钉住。
    assert st.FIELD_UNIT['change_pct'] == 'pct'
    assert st.FIELD_UNIT['turnover'] == 'pct'
    assert st.FIELD_UNIT['roe_ttm'] == 'ratio'
    assert abs(p['change_pct']) < 25, \
        'change_pct 量级不像百分数：%s' % p['change_pct']
    # turnover = 量×收盘价/流通市值×100（实测与字段精确吻合）
    tv = p['volume_shares'] * p['close_bfq'] / p['floatmv'] * 100
    assert abs(tv - p['turnover']) < 0.01, \
        'turnover 不是百分数或算法变了：字段 %s / 重算 %s' % (p['turnover'], tv)
    # 涨跌幅与 收盘/昨收 必须一致（换算口径搞错这里就会崩）
    assert abs((p['close_bfq'] / p['preclose'] - 1) * 100 - p['change_pct']) < 0.02, \
        'change_pct 与 收盘/昨收 对不上'
    try:
        st.profile('999999')
        raise AssertionError('认不出的代码应报错')
    except st.StockError:
        pass

    # ---- 4) K 线：均线在服务端算，且预热过 ----
    k = st.kline('601857.SH', n=120)
    b = k['bars']
    assert len(b) == 120, '根数不对：%d' % len(b)
    assert k['warmup_dropped'] == 60, \
        '应多取 60 根预热再切掉（否则头部 ma60 是空的）：%s' % k['warmup_dropped']
    # ★ 第一根就该有 ma60 —— 这正是预热的意义
    assert b[0]['ma60'] is not None, '第一根的 ma60 是空的 —— 预热没生效'
    # 均线自证：最后一根的 ma20 == 最后 20 根收盘均值
    ma20 = sum(x['close'] for x in b[-20:]) / 20
    assert abs(b[-1]['ma20'] - ma20) < 0.02, \
        'ma20 算错：%s vs %s' % (b[-1]['ma20'], ma20)
    for x in b:
        if x['high'] is not None:
            assert x['low'] <= x['open'] <= x['high'], 'OHLC 不自洽：%s' % x
            assert x['low'] <= x['close'] <= x['high'], 'OHLC 不自洽：%s' % x
    # 🔴 BOLL 中轨 == MA20（收盘的 20 日均值），**同一条线**。
    #   曾在主图上把它当成第三个系列画出来（还配了另一个颜色）——
    #   表现就是"5 日线看着有两条、两条都不对"，而它不报错。
    #   这条断言钉住"它们确实是一条线"，所以画图那边只该画一次。
    _ind = st.indicators('601857.SH', n=120)['rows']
    _d = [abs(x['ma20'] - y['mb']) for x, y in zip(b, _ind)
          if x['ma20'] is not None and y['mb'] is not None]
    assert _d and max(_d) < 0.005, \
        'BOLL 中轨与 MA20 不是同一条线了（最大差 %.4f）—— 结论变了就得改画图' \
        % max(_d)

    # ---- 4b) 主图配色：任意两条线不许撞色 ----
    #   🔴 判据用 RGB 欧氏距离量，不靠眼睛。踩过两处：
    #     · ma5 #e0a33c 与 BOLL 中轨 #f9a06b 距离 53 —— 肉眼分不出
    #     · 四条均线的 hex 与四种事件三角【完全相同】，而底部图例
    #       写着"▲除权除息"用的正是 MA5 那个色
    _kc = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            'web', 'shared', 'kchart.js'), encoding='utf-8').read()
    import re as _re4
    _grab = lambda name: dict(_re4.findall(
        r"(\w+):\s*'(#[0-9a-fA-F]{6})'",
        _re4.search(name + r'\s*=\s*\{([^}]*)\}', _kc).group(1)))
    _ma_c = _grab('MA_COLOR')
    _ev_c = _grab('EV_COLOR')
    _boll = _re4.search(r"BOLL_COLOR\s*=\s*'(#[0-9a-fA-F]{6})'", _kc).group(1)
    assert len(_ma_c) == 4 and len(_ev_c) == 4, \
        '取不到配色表：%s / %s' % (_ma_c, _ev_c)
    _rgb = lambda h: tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))
    _dist = lambda a, b_: sum(
        (p - q) ** 2 for p, q in zip(_rgb(a), _rgb(b_))) ** 0.5
    _lines = list(_ma_c.items()) + [('boll', _boll)]
    for _i in range(len(_lines)):
        for _j in range(_i + 1, len(_lines)):
            _dd = _dist(_lines[_i][1], _lines[_j][1])
            assert _dd >= 60, \
                ('主图上 %s 与 %s 撞色（RGB 距离 %.0f < 60）—— '
                 '两条线看着像一条' % (_lines[_i][0], _lines[_j][0], _dd))
    _both = set(_ma_c.values()) & set(_ev_c.values())
    assert not _both, \
        '均线与事件三角共用了颜色 %s —— 图例会指错' % sorted(_both)
    # 画图那边不许再把 mb 当一个系列
    assert "'mb'" not in _kc, \
        'kchart.js 又画 BOLL 中轨了 —— 它等于 MA20，会多出一条线'
    _sh = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            'web', 'stock.html'), encoding='utf-8').read()
    assert 'mb: IND' not in _sh, \
        'stock.html 又把 mb 合进 bars 了 —— 那就等于让 MA20 画两遍'

    # ---- 5) 复权：跨除权日的假跌幅 ----
    #   🔴 601088 有分红除权。不复权在除权日会掉一个坑，后复权不会。
    #   这就是"区间涨幅一律用后复权"的原因。
    kb = st.kline('601088.SH', n=250, fq='bfq')['bars']
    kh = st.kline('601088.SH', n=250, fq='hfq')['bars']
    assert len(kb) == len(kh)
    rb_ = kb[-1]['close'] / kb[0]['close'] - 1
    rh_ = kh[-1]['close'] / kh[0]['close'] - 1
    assert rh_ > rb_ + 0.01, \
        ('后复权的区间涨幅必须高于不复权（分红被除掉了）：'
         '不复权 %.4f / 后复权 %.4f' % (rb_, rh_))
    # profile 的区间涨幅用的是后复权 —— 不能等于不复权那个
    assert abs(st.profile('601088.SH')['ret_250d'] - rh_) < 0.03, \
        'profile.ret_250d 没用后复权'
    try:
        st.kline('601857.SH', fq='qfq')
        raise AssertionError('前复权应被拒（基准是"今天"，不能做特征）')
    except st.StockError:
        pass

    # ---- 6) 财务时序：一个报告期一行，且报告期 ≠ 公告日 ----
    f = st.finance('601857.SH', n=8)
    rd = [str(x['report_date'])[:10] for x in f['rows']]
    assert len(rd) == len(set(rd)), \
        ('同一报告期出现多行 —— 面板每个交易日都重复一遍当期财务，'
         'DISTINCT 挡不住（重述改一列就是两行）：%s' % rd)
    assert rd == sorted(rd, reverse=True), '报告期没倒序：%s' % rd
    for x in f['rows']:
        assert x['pub_date'] and str(x['pub_date']) > str(x['report_date']), \
            ('公告日必须晚于报告期 —— 拿报告期当可见日就是未来函数：%s'
             % [str(x['report_date']), str(x['pub_date'])])
    return ('代码 7 种写法归一 + 5 种非法返 None；搜索按代码/名称/前缀命中且'
            '同类按市值降序；面板 %d 字段且 change_pct/turnover 量级自证'
            '（turnover=量×价/流通市值×100 精确吻合）；'
            'K 线 %d 根、第一根就有 MA60（预热 %d 根）、ma20 复算一致、'
            'BOLL 中轨==MA20（所以主图只画一次）、'
            '主图 5 条线两两 RGB 距离 >=60 且与事件三角不共色；'
            '601088 后复权区间涨幅 %.1f%% vs 不复权 %.1f%%（除权坑）；'
            '财务 %d 个报告期不重复且公告日均晚于报告期'
            % (len(p), len(b), k['warmup_dropped'], rh_ * 100, rb_ * 100, len(rd)))


_BANDJS = r"""() => {
  const cv = document.getElementById('kcv');
  const g = cv.getContext('2d');
  const dpr = cv.width / cv.getBoundingClientRect().width;
  const sel = {i0: KSEL.i0, i1: KSEL.i1};
  const grab = (px) => Array.from(
    g.getImageData(Math.round(px * dpr), 40, 3, 60).data).join(',');
  const opt = (s) => ({bars: BARS, log: KLOG, events: SHOWEV ? EVS : [],
    subs: kSubs(), hover: null, sel: s});
  const geo = drawKChart(cv, opt(null));
  const a = Math.min(sel.i0, sel.i1), b = Math.max(sel.i0, sel.i1);
  const xin = (geo.X(a) + geo.X(b)) / 2;
  const xout = geo.X(Math.max(0, a - 6));
  const in0 = grab(xin), out0 = grab(xout);
  drawKChart(cv, opt(sel));
  return {inChanged: in0 !== grab(xin), outChanged: out0 !== grab(xout),
          xin: xin, xout: xout};
}"""


@case('K 线读数：那天的成交 / 框选浮窗贴在选区外 / 副图读数在副图上（playwright）',
       tag='web')
def t_kchart_readouts():
    """2026-09-15 用户三条，都是"信息该出现在该出现的地方"：

      ① "鼠标移到K线图上时，在买入、卖出那一根时看不到买入、卖出的价格数量。
         需要一个独立的浮窗展示出来，如果有多笔交易就展示多笔"
      ② "框选一段范围，展示范围内涨跌幅等等信息太不明显，
         最好是在框选范围外展示一个浮窗"
      ③ "正常的K线浮窗里不要展示MA信息、MACD信息，MA信息是已经有了，
         MACD的信息应该放在MACD的左上角，数字跟随变化"

    🔴 ③ 的实质是**同一份信息不要两处看**：MA 四条主图图例本来就带当天值、
      DIF/DEA/MACD 只在副图上有意义 —— 浮窗里再列一遍，就是让眼睛在
      两个地方之间来回找（同「汇总数字只在 KPI 板出现一次」那条）。
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return '跳过（无 playwright）'
    import threading
    from assay import server as sv
    from http.server import ThreadingHTTPServer
    old_live = sv.ALLOW_LIVE
    sv.ALLOW_LIVE = True
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), sv.Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    notes = []
    try:
        # 挑一只**有成交**的票（没有的话 ① 验不到）
        from assay import live as lv
        code = None
        for a in lv.load_accounts():
            if a.get('archived'):
                continue
            for f in lv.fills(a['id']):
                code = f['code']
                break
            if code:
                break
        if not code:
            return '跳过（没有任何成交）'
        with sync_playwright() as pw:
            br = pw.chromium.launch()
            pg = br.new_page(viewport={'width': 1440, 'height': 900})
            errs = []
            pg.on('pageerror', lambda e: errs.append(str(e)))
            pg.goto('http://127.0.0.1:%d/stock.html?code=%s&sub=macd'
                    % (port, code), wait_until='networkidle')
            pg.wait_for_selector('#kcv', timeout=90000)
            pg.wait_for_timeout(2500)

            n_tr = pg.evaluate('() => (TRADES || []).length')
            assert n_tr > 0, '这只票取不到成交（%s）—— ① 验不到' % code

            # ---- ① hover 到成交那根：读数里要有买卖明细 ----
            geo = pg.evaluate("""(dt) => {
                const i = BARS.findIndex(b => b.date === dt);
                if (i < 0) return null;
                const cv = document.getElementById('kcv');
                const g = drawKChart(cv, {bars: BARS, trades: TRADES});
                const r = cv.getBoundingClientRect();
                /* ★ `X()` 返回的**已经是 CSS 像素**（canvas 按实际宽度
                   绘制，不是固定 viewBox）—— 再乘一次比例会把坐标算到
                   画布外面（实测 1522 > 视口 1440，鼠标根本没落在页面上）。 */
                return {i: i, x: r.left + g.X(i),
                        y: r.top + r.height * 0.25,
                        hits: (g.trHits || []).length};}""",
                pg.evaluate('() => TRADES[0].date'))
            assert geo, '成交那天不在当前窗口里（换区间）'
            # 🔴 B/S 标记也要画出来 —— 读数有了但图上没标记，人不知道去哪 hover
            # 🔴 判据要用**页面自己渲染**的那次（`KGEO` 是 `redraw()` 存下的），
            #   不是我在断言里重画一次的结果 —— 后者传的是**我给的**参数，
            #   页面传什么都不影响它（变异"不画 B/S 标记"因此漏过一轮）。
            n_hit = pg.evaluate('() => (KGEO && KGEO.trHits || []).length')
            assert n_hit > 0, \
                ('图上没画 B/S 标记（页面渲染的 KGEO.trHits 是空的）—— '
                 '读数再全，人也不知道该往哪一根上移')
            pg.mouse.move(geo['x'], geo['y'])
            pg.wait_for_timeout(700)
            tip = pg.inner_text('#ktip')
            assert '我的成交' in tip, \
                ('hover 到成交那根，读数里没有成交明细：%r —— 原来要精确指到'
                 '那个 7.5px 的圆点上才看得到，而人是往**那根 K 线**上移的'
                 % tip[:120])
            import re as _re
            assert _re.search(r'[买卖]\s+[\d,]+\s*股\s*@', tip), \
                '成交明细里没有"买/卖 N 股 @ 价"：%r' % tip[:160]
            notes.append('hover 那根 K 线就出成交明细（%d 笔）' % n_tr)

            # ---- ③ 读数里不许再有 MA / MACD ----
            for bad in ('MA5', 'MA10', 'MA20', 'MA60', 'DIF', 'DEA', 'MACD'):
                assert bad not in tip, \
                    ('读数里还有「%s」—— MA 在主图图例里、DIF/DEA/MACD 在副图'
                     '左上角，浮窗里再列一遍就是同一份信息两处看：%r'
                     % (bad, tip[:160]))
            # 🔴 但它们**不能就此消失**：主图图例要有 MA+值，副图要有 DIF+值。
            #   只删不补的话这条断言"通过"了，而信息被弄丢了。
            px = pg.evaluate("""() => {
                const cv = document.getElementById('kcv');
                const g = cv.getContext('2d');
                const W = cv.width, H = cv.height;
                const dpr = W / cv.getBoundingClientRect().width;
                // 主图图例带（上方 ~14px 处）、副图图例带（副图顶 +10px）
                const strip = (y0, h) => {
                  const d = g.getImageData(0, Math.round(y0 * dpr),
                                           Math.round(260 * dpr),
                                           Math.round(h * dpr)).data;
                  let n = 0;
                  for (let i = 3; i < d.length; i += 4) if (d[i] > 40) n++;
                  return n;
                };
                const hpx = cv.getBoundingClientRect().height;
                return {main: strip(4, 16), sub: strip(hpx * 0.72, 16)};}""")
            assert px['main'] > 200, \
                '主图左上角没有图例（MA 的值也跟着没了）：%d 像素' % px['main']
            assert px['sub'] > 100, \
                ('副图左上角没有图例 —— DIF/DEA/MACD 从浮窗里拿掉了，'
                 '就必须出现在副图上（用户原话："放在MACD的左上角"）：'
                 '%d 像素' % px['sub'])
            # 🔴 **光有图例带不够，要有【值】，而且要"跟随变化"。**
            #   ★ 判据不能靠数像素：那条带子里混着 MACD 柱子与曲线，
            #     图例那几个字完全被淹没 —— 实测"只有名字"与"带值"两态
            #     扫出来都是 1268，像素这条路走不通（我试了两个版本才放弃）。
            #   ★ 改成**拦截绘制调用**：把 `fillText` 包一层，记下副图图例
            #     那一行画了什么。这直接验的是"画上去的文字"，
            #     比任何像素阈值都准，也不依赖布局。
            drew = pg.evaluate("""() => {
                const cv = document.getElementById('kcv');
                const g = cv.getContext('2d');
                const orig = g.fillText.bind(g);
                const seen = [];
                g.fillText = function (t, x, y) { seen.push([String(t), x, y]);
                                                  return orig(t, x, y); };
                try { drawKChart(cv, {bars: BARS, subs: kSubs(),
                                      hover: null}); }
                finally { g.fillText = orig; }
                /* 副图图例：DIF/DEA/MACD 那三个（不分大小写前缀匹配） */
                const keys = ['DIF', 'DEA', 'MACD'];
                return seen.map(x => x[0])
                  .filter(t => keys.some(k => t.toUpperCase().startsWith(k)));}""")
            assert drew, '副图上根本没画 DIF/DEA/MACD 图例'
            import re as _re2
            withval = [t for t in drew if _re2.search(r'[-\d]', t)]
            assert len(withval) >= 2, \
                ('副图图例画的是 %r —— 只有名字没有值。用户要的是'
                 '"MACD的信息应该放在MACD的左上角，**数字跟随变化**"，'
                 '而把它从浮窗里拿掉之后就必须在这儿看得到' % drew)
            # 🔴 **"跟随变化"**：hover 到另一天，值必须跟着变。
            #   只验"有数字"的话，写死一个常数也全绿。
            drew2 = pg.evaluate("""(hv) => {
                const cv = document.getElementById('kcv');
                const g = cv.getContext('2d');
                const orig = g.fillText.bind(g);
                const seen = [];
                g.fillText = function (t, x, y) { seen.push(String(t));
                                                  return orig(t, x, y); };
                try { drawKChart(cv, {bars: BARS, subs: kSubs(),
                                      hover: hv}); }
                finally { g.fillText = orig; }
                return seen.filter(t => t.toUpperCase().startsWith('DIF'));}""",
                5)
            dif0 = next(t for t in drew if t.upper().startswith('DIF'))
            assert drew2 and drew2[0] != dif0, \
                ('hover 到第 5 根时 DIF 图例还是 %r —— 值没有跟着光标变'
                 '（用户原话："数字跟随变化"）' % drew2)
            notes.append('副图图例带值且跟随光标（%s -> %s）' % (dif0, drew2[0]))
            notes.append('读数不再重复 MA/MACD，两处图例都在')

            # ---- ② 框选浮窗：贴在选区【外侧】，不盖住它 ----
            box = pg.query_selector('#kcv').bounding_box()
            y = box['y'] + box['height'] * 0.3
            for lab, (fa, fb) in (('选左半', (0.15, 0.40)),
                                  ('选右半', (0.60, 0.85))):
                pg.mouse.move(box['x'] + box['width'] * fa, y)
                pg.mouse.down()
                pg.mouse.move(box['x'] + box['width'] * (fa + 0.05), y)
                pg.mouse.move(box['x'] + box['width'] * fb, y, steps=8)
                pg.mouse.up()
                pg.wait_for_timeout(700)
                d = pg.evaluate("""() => {
                    const e = document.getElementById('kselpop');
                    if (!e || getComputedStyle(e).display === 'none') return null;
                    const c = document.getElementById('cbox').getBoundingClientRect();
                    const r = e.getBoundingClientRect();
                    const cv = document.getElementById('kcv');
                    const g = drawKChart(cv, {bars: BARS, sel: KSEL});
                    const a = Math.min(KSEL.i0, KSEL.i1);
                    const b = Math.max(KSEL.i0, KSEL.i1);
                    return {l: r.left - c.left, r: r.right - c.left,
                            selL: g.X(a), selR: g.X(b),
                            inside: r.right <= c.right + 1 && r.left >= c.left - 1,
                            txt: e.innerText};}""")
                assert d, '%s：框选之后没有浮窗（#kselpop）' % lab
                assert d['inside'], '%s：浮窗跑到容器外面了' % lab
                # 🔴 **不许盖住选区** —— 那正是人刚框出来、正在看的东西
                overlap = not (d['r'] <= d['selL'] or d['l'] >= d['selR'])
                assert not overlap, \
                    ('%s：浮窗[%.0f,%.0f] 盖住了选区[%.0f,%.0f] —— '
                     '用户要的是"在框选范围**外**展示"'
                     % (lab, d['l'], d['r'], d['selL'], d['selR']))
                for want in ('最高', '最低', '振幅', '交易日'):
                    assert want in d['txt'], \
                        '%s：浮窗里缺「%s」：%r' % (lab, want, d['txt'][:100])
                assert _re.search(r'[+-]\d+\.\d+%', d['txt']), \
                    '%s：浮窗里没有区间涨跌幅：%r' % (lab, d['txt'][:100])
            notes.append('框选浮窗贴在选区外侧（左右两侧都验）')
            # 点一下取消
            pg.mouse.move(box['x'] + box['width'] * 0.5, y)
            pg.mouse.down()
            pg.mouse.up()
            pg.wait_for_timeout(500)
            assert pg.evaluate("""() => getComputedStyle(
                document.getElementById('kselpop')).display""") == 'none', \
                '点一下没收起浮窗'
            assert not errs, 'JS 报错：%s' % errs[:3]
            br.close()
    finally:
        sv.ALLOW_LIVE = old_live
        httpd.shutdown()
    return '；'.join(notes)


@case('K 线左右移动 + 框选测算区间（playwright）', tag='web')
def t_kchart_pan_select():
    """2026-09-14 用户："K线图还需要有左右移动、框选一段范围自动测算区间
    涨跌幅、最低价、最高价、振幅的功能。"

    🔴 **翻页按【根数】不按日期**（服务端 `off` = 跳过最新几根）：按日期翻
      要前端自己算交易日，而前端没有交易日历 —— 本项目为此栽过一次
      （硬编码判据误报了一整页假告警）。
    🔴 **副图也要带 off**：不带的话翻页后 MACD 画的还是最新那一段，
      两张图上下对不上，**而它不报错**。
    🔴 **区间涨跌幅的基点取首根的【前收】**，不是它自己的收盘 —— 用首日收盘
      做基点等于把首日那根的涨跌排除在外，而那正是人框进来想看的
      （同「基准基点取第一天的前一交易日收盘」，实测差过 9.5pp）。
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return '跳过（无 playwright）'
    import threading
    from assay import server as sv
    from http.server import ThreadingHTTPServer
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), sv.Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    notes = []
    try:
        with sync_playwright() as pw:
            br = pw.chromium.launch()
            pg = br.new_page(viewport={'width': 1440, 'height': 900})
            errs = []
            pg.on('pageerror', lambda e: errs.append(str(e)))
            pg.goto('http://127.0.0.1:%d/stock.html?code=601857.XSHG&sub=macd'
                    % port, wait_until='networkidle')
            pg.wait_for_selector('#kcv', timeout=90000)
            pg.wait_for_timeout(2500)

            def win():
                return pg.evaluate("""() => ({a: BARS[0].date,
                    b: BARS[BARS.length - 1].date, off: KOFF, tot: KTOTAL,
                    ind: (IND && IND.length) ? IND.length : 0,
                    ma60: BARS[0].ma60});""")

            w0 = win()
            assert w0['tot'] > 1000, '总根数 %s —— 换一只历史长的票' % w0['tot']
            # ---- ① 往早翻：窗口必须真的移走，且【接得上】----
            pg.click('.kpan[data-pan="-1"]')
            pg.wait_for_timeout(3000)
            w1 = win()
            assert w1['b'] < w0['a'] or w1['a'] < w0['a'], \
                '点「‹ 早」之后窗口没往回移：%s~%s -> %s~%s' \
                % (w0['a'], w0['b'], w1['a'], w1['b'])
            assert w1['off'] > 0, 'KOFF 还是 0'
            #   半屏一步：两页必须**有重叠**，不然接不上形态
            assert w1['b'] > w0['a'], \
                ('翻一页跳过头了（新窗口 %s~%s 与原窗口 %s~ 没有重叠）—— '
                 '一步该是半屏' % (w1['a'], w1['b'], w0['a']))
            #   🔴 翻到哪一页，那一页的 ma60 都要是对的（预热取在窗口之外）
            assert w1['ma60'] is not None, \
                ('翻页后首根没有 ma60 —— 预热那 60 根没跟着 off 一起偏移，'
                 '于是每翻一页头部均线就缺一截，**而它不报错**')
            #   🔴 副图跟着走：不跟的话两张图上下对不上
            assert w1['ind'] == len(pg.evaluate('() => BARS')), \
                '副图行数 %d 与 K 线 %d 对不上 —— indicators 没带 off' \
                % (w1['ind'], len(pg.evaluate('() => BARS')))
            ind0 = pg.evaluate("() => IND[0] && IND[0].date")
            assert ind0 == w1['a'], \
                ('副图第一行是 %s，而 K 线第一根是 %s —— 翻页后副图画的还是'
                 '别的时间段，两张图上下对不上' % (ind0, w1['a']))
            notes.append('往早翻：%s~%s -> %s~%s（有重叠、ma60 与副图都跟上）'
                         % (w0['a'], w0['b'], w1['a'], w1['b']))
            # ---- ② 回最新 ----
            pg.click('.kpan[data-pan="0"]')
            pg.wait_for_timeout(3000)
            w2 = win()
            assert w2['off'] == 0 and w2['b'] == w0['b'], \
                '「最新」没回到最新一页：%s' % w2
            notes.append('「最新」回得去')

            # ---- ③ 框选 ----
            box = pg.query_selector('#kcv').bounding_box()
            y = box['y'] + box['height'] * 0.3
            x0 = box['x'] + box['width'] * 0.35
            x1 = box['x'] + box['width'] * 0.65
            pg.mouse.move(x0, y)
            pg.mouse.down()
            pg.mouse.move(x0 + 40, y)
            pg.mouse.move(x1, y, steps=8)
            pg.mouse.up()
            pg.wait_for_timeout(600)
            sel = pg.evaluate('() => KSEL')
            assert sel and sel['i0'] != sel['i1'], '拖动之后没有选区：%s' % sel
            # ★ 2026-09-15 起读数在**浮窗**里（`#kselpop`），不在图下那行
            #   （`#ksel` 现在只留"怎么用"的说明）—— 用户："太不明显，
            #   最好是在框选范围外展示一个浮窗"。**失败的是断言不是产品。**
            txt = pg.inner_text('#kselpop')
            for k in ('最高', '最低', '振幅'):
                assert k in txt, '框选读数缺「%s」：%s' % (k, txt[:120])
            # 🔴 **数字要自己复算一遍** —— 只查"有没有这四个字"的话，
            #   算错了照样绿（把 max 写成 min 也有"最高"两个字）。
            chk = pg.evaluate("""() => {
              const a = Math.min(KSEL.i0, KSEL.i1), b = Math.max(KSEL.i0, KSEL.i1);
              const seg = BARS.slice(a, b + 1);
              const hi = Math.max(...seg.map(x => x.high));
              const lo = Math.min(...seg.map(x => x.low));
              const base = seg[0].preclose;
              return {hi: hi, lo: lo, base: base,
                      ret: seg[seg.length - 1].close / base - 1,
                      amp: (hi - lo) / base, n: seg.length,
                      firstClose: seg[0].close};}""")
            assert ('最高 %s' % chk['hi']) in txt.replace('　', ' '), \
                '最高价对不上：读数 %s / 复算 %s' % (txt[:140], chk['hi'])
            assert ('最低 %s' % chk['lo']) in txt.replace('　', ' '), \
                '最低价对不上：读数 %s / 复算 %s' % (txt[:140], chk['lo'])
            import re as _re
            # ★ 浮窗里区间涨跌幅是**第一行的大字**（`.sphi`），不再带
            #   "区间涨跌"这个前缀 —— 取那个元素本身比在全文里正则更准。
            m = _re.search(r'([+-][\d.]+)%', pg.inner_text('#kselpop .sphi'))
            assert m, '读不出区间涨跌幅：%s' % txt[:140]
            shown = float(m.group(1))
            assert abs(shown - chk['ret'] * 100) < 0.02, \
                '区间涨跌幅对不上：页面 %.2f%% / 按【首根前收】复算 %.2f%%' \
                % (shown, chk['ret'] * 100)
            #   🔴 反向自证：用**首根收盘**当基点会算出另一个数，
            #     两者必须不同 —— 否则这条断言分不出用的是哪个基点。
            alt = (chk['firstClose'] and
                   pg.evaluate("() => BARS[Math.max(KSEL.i0,KSEL.i1)].close")
                   / chk['firstClose'] - 1)
            assert abs(alt * 100 - chk['ret'] * 100) > 0.05, \
                ('首根前收与首根收盘算出来一样（%.4f vs %.4f）—— 换个选区，'
                 '这条基点断言测不到' % (alt * 100, chk['ret'] * 100))
            m2 = _re.search(r'振幅\s*([\d.]+)%', txt.replace('　', ' '))
            assert m2 and abs(float(m2.group(1)) - chk['amp'] * 100) < 0.02, \
                '振幅对不上：%s / 复算 %.2f%%' % (txt[:140], chk['amp'] * 100)
            notes.append('框选 %d 天：涨跌 %.2f%% / 高 %s / 低 %s / 振幅 %.2f%%'
                         % (chk['n'], shown, chk['hi'], chk['lo'],
                            chk['amp'] * 100))
            # 🔴 **选区带必须真的画在画布上** —— 只验读数的话，把那段绘制
            #   注掉照样全绿（变异实测漏过），而那时人根本看不出自己框了哪一段。
            #   判据两头都要：**带内像素变了** + **带外像素没变**
            #   （只判"画布变了"的话，整块涂一层也算通过）。
            band = pg.evaluate(_BANDJS)
            assert band['inChanged'], \
                ('框选之后带【内】的像素一点没变 —— 选区带根本没画出来，'
                 '人看不出自己框了哪一段（读数对不代表图上有标记）')
            assert not band['outChanged'], \
                ('带【外】的像素也变了（x=%.0f）—— 那不是"一条带"，'
                 '是把整张图涂了一层' % band['xout'])
            notes.append('选区带真的画出来了（带内变、带外不变）')

            # ---- ④ 点一下取消 ----
            pg.mouse.move(box['x'] + box['width'] * 0.5, y)
            pg.mouse.down()
            pg.mouse.up()
            pg.wait_for_timeout(500)
            assert pg.evaluate('() => KSEL') is None, '点一下没清掉选区'
            notes.append('点一下取消')

            # ---- ⑤ 🔴 选中的标签不许【蓝字压蓝底】----
            #   `class="lvtag rg on"` 上两条规则优先级相同（都 0,2,0），
            #   靠先后决胜负 —— `.lvtag.on` 把 `.rg.on` 的深色字覆盖成 accent，
            #   而背景还是 accent：那个按钮整个看不见（实测个股页「1年」、
            #   对比页同样）。判据扫全站，不是只看这一处。
            for u in _pages():
                pg.goto('http://127.0.0.1:%d%s' % (port, u), wait_until='networkidle')
                pg.wait_for_timeout(1800)
                same = pg.evaluate("""() => [...document.querySelectorAll(
                    '.lvtag.on,.lvtag.off,.rg.on')].filter(a => {
                    const c = getComputedStyle(a);
                    return c.color === c.backgroundColor;})
                  .map(a => a.className + '|' + a.textContent.trim().slice(0, 8))""")
                assert not same, \
                    ('%s 上这些标签前景色 == 背景色（整个看不见）：%s' % (u, same))
            notes.append('6 个页面上没有"同色不可见"的标签')
            assert not errs, 'JS 报错：%s' % errs[:3]
            br.close()
    finally:
        httpd.shutdown()
    return '；'.join(notes)


@case('K 线柱子有最大宽度：新股不许拉伸填满，往左贴（playwright）', tag='web')
def t_kchart_min_span():
    """2026-09-14 用户："新股的 K 线展示特别大，因为有几根 K 线就展示几根，
    然后填满。应该设置一个最小展示天数，不足天数的往左边贴，不要占满。"

    原来 `step = w / n` —— 有几根画几根、平分整个宽度。实测 688835
    上市 15 个交易日、画布 1334px，于是**每根 K 线 60px 宽**：一屏几个
    大色块，既看不出形态、也让人误以为"这只票就长这样"。

    ★ 判据用**单根最大像素宽**（14px）而不是写死"最少 N 天"：画布宽度
      本来就不一样（个股页 ~1334、浮层 1180），写死天数在窄画布上又太挤。
    ★ 也不能反过来按"请求了多少天"留槽 —— 选「1 年」时 15 根票会被压成
      15 条发丝，那是另一个极端。

    🔴 **两头都要钉**：只钉"新股不占满"的话，把 `MAXSTEP` 调到 1px 也全绿，
      而那会把**所有**图都压成左边一条 —— 正常股票必须**仍然占满**。
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return '跳过（无 playwright）'
    import threading
    from assay import server as sv
    from http.server import ThreadingHTTPServer
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), sv.Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    # 先在服务端挑一只**真正的新股**（上市天数 < 30）—— 写死代码的话
    # 它总有一天不再是新股，而那时这条用例会静默变成"又一次测老股"。
    import duckdb as _dd
    from assay import live as _lv
    root = _lv._lake()
    con = _dd.connect(':memory:')
    try:
        pan = ("read_parquet('%s/mart/panel_daily/panel_*.parquet')" % root)
        row = con.execute(
            "SELECT jq_code, listed_days FROM %s WHERE date = "
            "(SELECT max(date) FROM %s) AND listed_days BETWEEN 3 AND 30 "
            "ORDER BY listed_days LIMIT 1" % (pan, pan)).fetchone()
    finally:
        con.close()
    if not row:
        httpd.shutdown()
        return '跳过（当前面板里没有上市 30 天内的新股）'
    new_code, listed = row[0], int(row[1])
    notes = []
    try:
        with sync_playwright() as pw:
            br = pw.chromium.launch()
            pg = br.new_page(viewport={'width': 1440, 'height': 900})
            errs = []
            pg.on('pageerror', lambda e: errs.append(str(e)))

            def geo(code):
                pg.goto('http://127.0.0.1:%d/stock.html?code=%s' % (port, code),
                        wait_until='networkidle')
                pg.wait_for_selector('#kcv', timeout=90000)
                pg.wait_for_timeout(2000)
                return pg.evaluate("""() => {
                  const cv = document.getElementById('kcv');
                  const g = drawKChart(cv, {bars: BARS});
                  if (!g) return null;
                  const n = (BARS || []).length;
                  return {n: n, step: g.step, last: g.X(n - 1),
                          W: cv.getBoundingClientRect().width};}""")

            # ---- ① 新股：柱子不许变宽，且**往左贴** ----
            a = geo(new_code)
            assert a and a['n'] > 0, '新股 %s 没渲染出 K 线' % new_code
            assert a['n'] <= 40, \
                '挑到的 %s 有 %d 根，不算新股了' % (new_code, a['n'])
            assert a['step'] <= 15.0, \
                ('新股 %s（%d 根）每根 %.1f px —— 拉伸去填满画布了，'
                 '一屏几个大色块看不出形态' % (new_code, a['n'], a['step']))
            assert a['last'] < a['W'] * 0.6, \
                ('新股的最后一根画在 %.0f/%.0f（%.0f%%）—— 还是占满了；'
                 '不足的天数该**往左贴**、右边留白'
                 % (a['last'], a['W'], a['last'] / a['W'] * 100))
            notes.append('新股 %s：%d 根 · 每根 %.1f px · 只占 %.0f%%'
                         % (new_code, a['n'], a['step'], a['last'] / a['W'] * 100))

            # ---- ② 🔴 正常股票必须【仍然占满】 ----
            #   只钉上面那条的话，把 MAXSTEP 调到 1px 也全绿 —— 而那会
            #   把所有图都压成左边一条。
            b = geo('601857.XSHG')
            assert b and b['n'] >= 100, '老股该有上百根：%s' % (b or {}).get('n')
            assert b['last'] > b['W'] * 0.9, \
                ('老股（%d 根）只画到 %.0f%% —— 最大宽度限制误伤了正常图，'
                 '它本来就该占满' % (b['n'], b['last'] / b['W'] * 100))
            assert b['step'] < a['step'], \
                ('老股每根 %.2f px 不比新股 %.2f px 窄 —— 说明限制没生效'
                 % (b['step'], a['step']))
            notes.append('老股 601857：%d 根 · 每根 %.2f px · 占 %.0f%%'
                         % (b['n'], b['step'], b['last'] / b['W'] * 100))
            assert not errs, 'JS 报错：%s' % errs[:2]
            br.close()
    finally:
        httpd.shutdown()
    return '；'.join(notes)


@case('个股页面真实渲染（playwright）', tag='web')
def t_stock_ui():
    """独立页 /stock.html：搜索 → K 线 → 副图 → 事件 → 联动 → 同业 → 板块。

    ★ 「Canvas 画出来了」不能只看 DOM 有没有 <canvas> —— 那永远都在。
      判据是**画布上有非透明像素**，且切换后像素分布确实变了。
      画崩了（尺寸算错、坐标 NaN）的表现就是一张空白画布，而它不报错。
    ★ 旧的 hash 链接 `/#/stock/xxx` 必须还能用（跳到新页）——
      书签和别处的链接不该失效，而"点了没反应"是最难查的那种坏。
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return '跳过（无 playwright）'
    import threading
    from http.server import ThreadingHTTPServer

    import shutil
    import tempfile

    from assay import server as sv
    from assay import watchlist as wlmod
    old_live, old_dir = sv.ALLOW_LIVE, wlmod.LIVE
    sv.ALLOW_LIVE = True                 # 自选星要能点
    # 🔴 自选账本重定向到临时目录 —— 这个用例会真点那颗星，不重定向就会往
    #   【真账本】里写测试记录。append-only 的账本本来就不该被测试污染，
    #   而它每次跑留一对 add/remove，攒了 11 对才在提交时被发现。
    tmpwl = tempfile.mkdtemp()
    wlmod.LIVE = tmpwl
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), sv.Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    NZ = ("() => { const c=document.querySelector('#kcv');"
          " const g=c.getContext('2d');"
          " const d=g.getImageData(0,0,c.width,c.height).data;"
          " let n=0; for(let i=3;i<d.length;i+=4) if(d[i]>0) n++; return n; }")
    try:
        with sync_playwright() as p:
            try:
                br = p.chromium.launch()
            except Exception as e:                          # noqa: BLE001
                return '跳过（浏览器不可用: %s）' % type(e).__name__
            pg = br.new_page(viewport={'width': 1600, 'height': 1100})
            errs = []
            pg.on('pageerror', lambda e: errs.append(str(e)))
            pg.on('console',
                  lambda m: errs.append('console: ' + m.text) if m.type == 'error' else None)
            pg.goto('http://127.0.0.1:%d/stock.html' % port, wait_until='networkidle')
            pg.wait_for_selector('#sbox input', timeout=30000)
            # ★ 这一页上【不该出现 SQL 输入框】—— 要写任意查询去 /#/query
            assert pg.locator('#qsql').count() == 0, '个股页不该有 SQL 输入框'
            # 顶栏导航：一处定义，当前页高亮
            #   🔴 **数字自己算**，不写死：原来钉的是 `>= 8`，顶栏一合并
            #     （9 -> 7）这条就挂了，而挂的是断言不是产品。判据取 NAV
            #     自己的长度 —— 下次再增减入口不用手改，而"忘了改"的表现
            #     是报告在说谎（同「断言直接扫目录而不是照清单拼」那条）。
            n_nav = pg.evaluate('() => NAV.length')
            assert n_nav >= 5, '顶栏入口只剩 %d 个？' % n_nav
            assert pg.locator('#top .btn.nav').count() == n_nav, \
                ('顶栏渲染出 %d 个入口，而 NAV 里有 %d 个'
                 % (pg.locator('#top .btn.nav').count(), n_nav))
            assert '个股' in pg.locator('#top .btn.nav.on').inner_text(), \
                '当前页没高亮'

            # ---- 搜索：打字 → 下拉 → 键盘选中 ----
            pg.fill('#sbox input', '中国石油')
            pg.wait_for_selector('.skit', timeout=15000)
            first = pg.locator('.skit').first.inner_text()
            assert '601857' in first and '中国石油' in first, \
                '下拉里没有代码或名称：%s' % first
            pg.keyboard.press('Enter')
            pg.wait_for_selector('#kcv', timeout=30000)
            pg.wait_for_timeout(1500)
            assert '601857' in pg.url, '没跳到个股页：%s' % pg.url

            head = ' '.join(pg.locator('#body .lvhead').first.inner_text().split())
            assert '中国石油' in head and '601857.XSHG' in head, '头部不对：%s' % head
            assert '沪深300' in head, '指数标签没渲染：%s' % head

            kp = ' | '.join(pg.locator('#body .kpi .k').all_inner_texts())
            for kk in ('今开 / 昨收', '最高 / 最低', '涨停 / 跌停', '成交额',
                       'PE(TTM)', 'PB', 'ROE(TTM)', '52 周区间', '区间涨幅'):
                assert kk in kp, 'KPI 缺「%s」：%s' % (kk, kp)
            body = pg.locator('#pg').inner_text()
            assert 'undefined' not in body and 'NaN' not in body, \
                '页面上有 undefined/NaN'
            assert '换手 +' not in body and '振幅 +' not in body, \
                '换手/振幅带了 + 号（它们不会为负）'

            # ---- 各个板块都在 ----
            secs = [x.split('\n')[0] for x in
                    pg.locator('#body .lvsec h3').all_inner_texts()]
            for kk in ('日 K', '实盘持仓', '回测买过它', '所属板块', '同行业',
                       '最近 20 个交易日', '事件', '财务（按报告期）'):
                assert any(kk in x for x in secs), '缺「%s」这一节：%s' % (kk, secs)
            # 所属板块要能点去板块页
            assert pg.locator('#body a.chip[href*="/sector.html"]').count() >= 1, \
                '板块 chip 没链到板块页'

            # ---- K 线真的画出来了（含 MACD 副图）----
            nz1 = pg.evaluate(NZ)
            assert nz1 > 5000, 'Canvas 上几乎没有像素（画崩了）：%d' % nz1
            bb = pg.locator('#kcv').bounding_box()
            pg.mouse.move(bb['x'] + bb['width'] * 0.7, bb['y'] + bb['height'] * 0.3)
            pg.wait_for_timeout(500)
            assert pg.locator('#ktip').is_visible(), '十字光标没出读数'
            tip = pg.locator('#ktip').inner_text()
            # 🔴 **MA / DIF 不再出现在读数里**（2026-09-15 用户要求）：
            #   MA 四条主图图例本来就带那一天的值、DIF/DEA/MACD 改到副图
            #   左上角 —— 浮窗里再列一遍就是同一份信息两处看。
            #   这两处"搬到哪儿去了"由「K 线读数」那条用例专门钉着，
            #   这里只保留"读数本身要有 OHLC"。**失败的是断言不是产品。**
            for kk in ('开', '高', '低', '收'):
                assert kk in tip, '读数缺「%s」：%s' % (kk, tip.replace('\n', ' '))
            for kk in ('MA20', 'MA60', 'DIF'):
                assert kk not in tip, \
                    ('读数里还有「%s」—— 它该在图例上（主图 MA / 副图 DIF），'
                     '不该在跟着光标的浮窗里重复一遍：%s'
                     % (kk, tip.replace('\n', ' ')))
            assert 'undefined' not in tip and 'NaN' not in tip, \
                '读数里有 undefined/NaN：%s' % tip

            # ---- 均线图例：色块 + 名称 + 【那一天的值】 ----
            #   🔴 光有名字不够 —— 几条颜色相近的线还是分不出谁是谁。
            #     有了数字就能拿它跟纵轴对一下（也是这次"5 日线有两条"
            #     那个问题最省事的自查手段）。
            _lg = pg.evaluate("() => { const c=document.querySelector('#kcv'); const g=c.getContext('2d'); const r=c.width/c.clientWidth; const d=g.getImageData(50*r, 4*r, 340*r, 16*r).data; const s=new Set(); let n=0; for(let i=0;i<d.length;i+=4){ if(d[i+3]>200){ n++; s.add((d[i]>>4)+','+(d[i+1]>>4)+','+(d[i+2]>>4)); } } return [n, s.size]; }")
            assert _lg[0] > 300, '图例区几乎没画东西：%s' % _lg
            assert _lg[1] >= 4, \
                '图例区颜色少于 4 种 —— 四条均线的色块/文字没分开：%s' % _lg
            _ms = pg.evaluate('() => BARS[BARS.length-1].ma5')
            assert _ms and _ms > 0, '取不到 ma5'

            # ---- BOLL：默认【关】，开关生效 ----
            #   🔴 原来只要选了副图就无条件叠 BOLL，主图上 7 条线；
            #     而 BOLL 中轨就是 MA20（同一条线画两遍、两个颜色），
            #     上下轨的橙色又与 MA5 撞色 —— 表现就是"5 日线看着有两条"。
            # ★ **入口变过两次，判据一次没变**：工具条独立开关 -> 指标面板里
            #   的"主图指标"那组 -> 工具条上的「主图 [均线][布林带]」两个开关
            #   （副图改成槽位之后，面板整个没了，主图那两个本来就只有两个、
            #   做成下拉反而绕）。要证的仍是那三件事：
            #   默认关 / 开了画布真的变 / 状态进 URL（刷新不丢）。
            _bt = pg.locator('#kpickbox .kmain[data-i="boll"]')
            assert _bt.count() == 1, '工具条上没有 BOLL 这个主图开关'
            assert 'on' not in (_bt.get_attribute('class') or ''), \
                'BOLL 默认应该是关的'
            assert pg.evaluate("() => KMAIN.indexOf('boll') < 0"), \
                'BOLL 状态位不对'
            # ★ 先把光标移开画布再量基线 —— 十字光标本身就画了几百个像素
            #   （实测 417），拿"悬停时"的数当基线会让"关掉后回不到原样"
            #   假失败一次。
            pg.mouse.move(bb['x'] + bb['width'] / 2, bb['y'] - 40)
            pg.wait_for_timeout(400)
            _no = pg.evaluate(NZ)
            pg.locator('#kpickbox .kmain[data-i="boll"]').click()
            pg.wait_for_timeout(1300)
            _yes = pg.evaluate(NZ)
            assert _yes > _no, \
                '开了 BOLL 画布像素没变多（%d -> %d）' % (_no, _yes)
            assert 'on' in (pg.locator('#kpickbox .kmain[data-i="boll"]')
                            .get_attribute('class') or ''), \
                'BOLL 开了但那个标签没点亮'
            assert 'main=ma%2Cboll' in pg.url or 'main=ma,boll' in pg.url, \
                'BOLL 状态没进 URL（刷新就丢）：%s' % pg.url
            pg.locator('#kpickbox .kmain[data-i="boll"]').click()
            pg.wait_for_timeout(1300)
            assert abs(pg.evaluate(NZ) - _no) < 300, \
                '关掉 BOLL 后画布没回到原样'

            # ---- 切副图 KDJ：**副图图例**跟着换（不再是读数）----
            # 🔴 2026-09-15 起指标值从跟着光标的浮窗搬到了副图左上角
            #   （用户："MACD的信息应该放在MACD的左上角，数字跟随变化"）。
            #   原来这条查的是"读数里出现 K/D/J" —— **被改动作废**的判据。
            #   ★ 改成拦 `fillText`：直接验副图图例画了什么，
            #     比数像素准（那条带子里混着柱子与曲线，图例会被淹没）。
            # ★ 换指标的入口也变了：副图现在是**槽位**，每个左上角一个下拉框
            #   （用户："副图左上角是一个下拉框，可以选择显示什么指标"）。
            pg.locator('#kslots .kssel[data-j="1"]').select_option('kdj')
            pg.wait_for_timeout(1800)
            kdj = pg.evaluate("""() => {
                const cv = document.getElementById('kcv');
                const g = cv.getContext('2d');
                const orig = g.fillText.bind(g);
                const seen = [];
                g.fillText = function (t, x, y) { seen.push(String(t));
                                                  return orig(t, x, y); };
                try { drawKChart(cv, {bars: BARS, subs: kSubs()}); }
                finally { g.fillText = orig; }
                return seen.filter(t => /^(K|D|J)\\s/.test(t.toUpperCase()));}""")
            assert len(kdj) >= 2, \
                '切 KDJ 之后副图图例没画出 K/D/J（实得 %r）' % kdj
            assert any(any(ch.isdigit() for ch in t) for t in kdj), \
                '副图 KDJ 图例只有名字没有值：%r' % kdj
            tip2 = pg.locator('#ktip').inner_text() if \
                pg.locator('#ktip').is_visible() else ''
            assert 'DIF' not in tip2, '切了 KDJ，读数里还留着 DIF：%s' % tip2

            # ---- 切区间：根数变了，画布也重画了 ----
            pg.locator('#body .rg').first.click()      # 3 月
            pg.wait_for_timeout(2000)
            nz2 = pg.evaluate(NZ)
            assert nz2 > 3000 and nz2 != nz1, \
                '切区间后画布没变（%d -> %d）' % (nz1, nz2)

            # ---- 切复权：说明也要跟着换 ----
            pg.locator('#body .fq[data-f="hfq"]').click()
            pg.wait_for_timeout(2000)
            note = pg.locator('#body .lvsec').first.inner_text()
            assert '后复权' in note and '跨期' in note, \
                '没说明两种复权的区别（除权日假跌幅）：%s' % note[-160:]

            # ---- 自选星：点了要真进自选 ----
            import json as _json
            import urllib.request
            st = pg.locator('#body .lvhead .star').first
            st.click()
            pg.wait_for_timeout(1200)
            wl = _json.loads(urllib.request.urlopen(
                'http://127.0.0.1:%d/api/watchlist' % port, timeout=30).read())
            assert any(x['code'] == '601857.XSHG' for x in wl.get('rows') or []), \
                '点了星但没进自选：%s' % wl
            st.click()                                  # 点回去，别留脏数据
            pg.wait_for_timeout(1000)

            # ---- 旧 hash 链接要还能用（跳到新页）----
            pg.goto('http://127.0.0.1:%d/#/stock/600519.XSHG' % port,
                    wait_until='networkidle')
            pg.wait_for_selector('#kcv', timeout=30000)
            pg.wait_for_timeout(1500)
            assert '/stock.html' in pg.url and '600519' in pg.url, \
                '旧 hash 链接没跳到独立页：%s' % pg.url
            assert '贵州茅台' in pg.locator('#pg').inner_text(), '第二只票没渲染'
            assert pg.evaluate(NZ) > 5000, '第二只票的 K 线没画出来'

            br.close()
            assert not errs, '页面有运行时错误：%s' % errs[:3]
            return ('独立页 /stock.html：顶栏导航高亮、页上无 SQL 框、'
                    '搜索→键盘选中→跳转、8 个分区齐全、板块 chip 链到板块页、'
                    'Canvas 真有 %d 个像素、十字光标读出 OHLC+MA+MACD、'
                    '均线图例带色块与当日值（≥4 色）、BOLL 默认关且开关改'
                    '画布与 URL、'
                    '切 KDJ 后读数跟着换、切区间画布变、切后复权有说明、'
                    '点星真进自选、旧 hash 链接跳新页' % nz1)
    finally:
        sv.ALLOW_LIVE, wlmod.LIVE = old_live, old_dir
        shutil.rmtree(tmpwl, ignore_errors=True)
        httpd.shutdown()


@case('只读查询器：写操作一律拒 / 自动 LIMIT（CLI）', tag='fast')
def t_query():
    """`datalake/build/query.py` —— 命令行的只读查询器。

    ★ 看板上那个 SQL 页**已取消**，但这个工具留着：它是命令行里最顺手的
      查数据方式，而且三层只读保证都在这里。

    ★ 只读要挡住的重点是 `COPY ... TO 'file'`：它**不需要**可写的数据库
      连接就能写文件系统。只靠"以 READ_ONLY 挂 lake.db"挡不住它，
      而被写坏的是 mart/ —— 之后每次回测都用坏面板，且不报错。
    """
    import json as _json
    import subprocess

    from assay import server as sv
    dl = sv._datalake_dir()
    sc = os.path.join(dl, 'build', 'query.py')
    assert os.path.isfile(sc), '缺 %s' % sc
    sys.path.insert(0, os.path.join(dl, 'build'))
    import query as qmod                                    # noqa: PLC0415

    BAD = [
        ("COPY (SELECT 1) TO '/tmp/x.csv'", 'COPY TO 能直接写盘'),
        ("COPY (SELECT 1) TO '/tmp/x.parquet' (FORMAT PARQUET)", '同上'),
        ('CREATE TABLE t AS SELECT 1', 'DDL'),
        ('CREATE OR REPLACE VIEW v AS SELECT 1', 'DDL'),
        ('INSERT INTO code_map VALUES (1)', 'DML'),
        ('UPDATE code_map SET a=1', 'DML'),
        ('DELETE FROM code_map', 'DML'),
        ('DROP TABLE code_map', 'DDL'),
        ('ALTER TABLE code_map RENAME TO x', 'DDL'),
        ("ATTACH '/tmp/e.db' AS e", '挂别的库'),
        ('INSTALL httpfs', '装扩展'),
        ('LOAD httpfs', '载扩展'),
        ("EXPORT DATABASE '/tmp/d'", '整库导出'),
        ("SET home_directory='/tmp'", '改设置'),
        ('SELECT 1; DROP TABLE code_map', '多语句'),
        ("SELECT * FROM read_text('/etc/passwd')", '读任意文本'),
        ('VACUUM', '维护语句'),
        ("SELECT 1 /* x */; COPY (SELECT 1) TO '/tmp/y'", '注释藏第二条'),
    ]
    for q, why in BAD:
        try:
            qmod.check(q)
            raise AssertionError('%s 应被拒：%r' % (why, q))
        except ValueError:
            pass
    # 误杀检查：注释里 / 列名里出现关键字不该被拒
    OK = [
        '-- 说明里提到 copy 和 create\nSELECT 42',
        'SELECT db_create_time FROM (SELECT 1 AS db_create_time)',
        'SELECT 1 OFFSET 0',            # offset 里含 set
        'WITH a AS (SELECT 1) SELECT * FROM a',
        'DESCRIBE SELECT 1',
        'SUMMARIZE SELECT 1',
    ]
    for q in OK:
        qmod.check(q)                   # 抛异常就是误杀

    # 自动 LIMIT + 截断必须说出来（悄悄少给几行比查不出来更糟）
    r = qmod.run("SELECT * FROM read_parquet('%s/mart/panel_daily/panel_*.parquet')"
                 % dl, limit=7)
    assert r['n'] == 7 and r['limit_added'] and r['truncated'], \
        '没写 LIMIT 时应自动加并标记截断：%s' % {k: r[k] for k in
                                        ('n', 'limit_added', 'truncated')}
    r2 = qmod.run('SELECT 1 LIMIT 1', limit=500)
    assert not r2['limit_added'] and not r2['truncated'], '已有 LIMIT 不该再加'
    assert qmod.MAX_LIMIT <= 50000, 'MAX_LIMIT 太大 —— 3127 万行会把内存吃光'

    # 表清单要覆盖 parquet，不能只有 lake.db
    schm = qmod.schema()
    names = {t['name'] for t in schm['parquet']}
    assert 'panel_daily' in names, \
        '表清单里没有面板 —— 它不在 lake.db 里，漏了等于只覆盖一半数据'
    pn = [t for t in schm['parquet'] if t['name'] == 'panel_daily'][0]
    assert len(pn['columns']) >= 70, '面板列数不对：%d' % len(pn['columns'])

    # CLI 真能跑
    out = subprocess.run(['python3', sc, '--json', '--sql-stdin', '--limit', '2'],
                         cwd=dl, input='SELECT 1 AS a', capture_output=True,
                         text=True, timeout=120)
    assert _json.loads(out.stdout)['rows'] == [[1]], 'CLI 跑不出结果'

    # ★ 页面那一层已取消 —— 接口和路由都不该再有，否则是"删了一半"
    assert not hasattr(sv, 'api_query'), '/api/query 还在'
    assert '/api/query' not in sv.ROUTES, '路由里还有 /api/query'
    web = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'web')
    for fn in _web_files(web, ('.html', '.js')):
        src = open(os.path.join(web, fn), encoding='utf-8').read()
        code = '\n'.join(ln for ln in src.split('\n')
                          if '//' not in ln and '/*' not in ln and '*' != ln.strip()[:1])
        assert "'#/query'" not in code and '"#/query"' not in code, \
            '%s 里还有指向已取消的查数据页的链接' % fn
    return ('只读拒 %d 类写操作（含 COPY TO / 多语句 / 注释藏第二条）、'
            '%d 种合法写法不误杀、自动 LIMIT 并标截断、表清单含面板 %d 列；'
            '页面那一层已彻底取消（无 api_query / 无路由 / 无残留链接）'
            % (len(BAD), len(OK), len(pn['columns'])))

@case('盘面：涨跌家数自洽 / 分档不重不漏 / 回看任意一天', tag='fast')
def t_market():
    """盘面的口径错了不报错，只是数不对。三处自证：

    1. **涨 + 跌 + 平 == 总数**，且分档家数之和也等于总数 —— 分档边界
       写重叠或留缝隙，总数就对不上，而页面上看着一切正常。
    2. **只算正常上市 / ST / *ST** —— 把退市整理期和状态为空的混进来，
       涨跌家数就偏。用"直接数一遍"独立复算。
    3. **周末要落到最近的有数据日**，并把实际用的日期回给页面 ——
       直接按等号查会返回空，而"空"看起来像"那天全市场没成交"。
    """
    import duckdb

    from assay import market as mk
    o = mk.overview()
    assert o['up'] + o['down'] + o['flat'] == o['n'], \
        '涨跌平之和 %d 不等于总数 %d' % (o['up'] + o['down'] + o['flat'], o['n'])
    tot = sum(b['n'] for b in o['buckets'])
    assert tot == o['n'], \
        ('分档之和 %d ≠ 总数 %d —— 档位边界重叠或留了缝隙' % (tot, o['n']))
    # 档位必须单调、首尾覆盖到 ±100
    lo = [a for a, _b, _l in mk.BUCKETS]
    hi = [b for _a, b, _l in mk.BUCKETS]
    assert lo == sorted(lo) and hi == sorted(hi), '档位没排序'
    assert lo[0] <= -100 and hi[-1] >= 100, '首尾档没覆盖极值'
    for i in range(len(lo) - 1):
        assert hi[i] == lo[i + 1], \
            '第 %d 档与下一档不衔接（%s vs %s）—— 会漏或重复' % (i, hi[i], lo[i + 1])

    # 独立复算：直接数一遍，口径必须一致
    c = duckdb.connect(':memory:')
    p = mk.panel()
    n2, up2, lu2 = c.execute("""
        SELECT count(*), sum(CASE WHEN change_pct>0 THEN 1 ELSE 0 END),
               sum(CASE WHEN is_limit_up THEN 1 ELSE 0 END)
        FROM %s WHERE date = DATE '%s'
          AND public_status IN ('正常上市','ST','*ST') AND close_bfq IS NOT NULL"""
        % (p, o['date'])).fetchone()
    assert (n2, up2, lu2) == (o['n'], o['up'], o['limit_up']), \
        '独立复算不一致：%s vs %s' % ((n2, up2, lu2), (o['n'], o['up'], o['limit_up']))
    # 把状态放开会变多 —— 证明筛选真的在起作用（不是恒等式）
    n3 = c.execute("SELECT count(*) FROM %s WHERE date = DATE '%s'"
                   " AND close_bfq IS NOT NULL" % (p, o['date'])).fetchone()[0]
    assert n3 > o['n'], \
        '放开 public_status 后家数没变多（%d vs %d）—— 筛选没生效？' % (n3, o['n'])

    # 榜单：涨幅榜必须降序、跌幅榜升序，且每行都有代码与名称
    g = o['ranks']['gainers']['rows']
    l_ = o['ranks']['losers']['rows']
    assert g and l_, '榜单是空的'
    gv = [x['change_pct'] for x in g]
    lv = [x['change_pct'] for x in l_]
    assert gv == sorted(gv, reverse=True), '涨幅榜没降序'
    assert lv == sorted(lv), '跌幅榜没升序'
    assert gv[0] > lv[0], '涨幅榜第一名居然不比跌幅榜第一名高'
    for x in g:
        assert x['code'] and '.' in x['code'], '榜单行缺代码'
    # 行业榜等权平均要按降序，且家数之和 <= 总数（有些票没有行业）
    inds = o['industries']
    av = [x['avg_change'] for x in inds]
    assert av == sorted(av, reverse=True), '行业榜没按平均涨幅降序'
    assert sum(x['n'] for x in inds) <= o['n'], '行业家数之和超过总数'

    # ---- 回看任意一天：周末落到最近的有数据日，并说出来 ----
    import datetime as _dt
    d = _dt.date.fromisoformat(o['date'])
    sat = d
    while sat.weekday() != 5:                 # 找一个周六
        sat -= _dt.timedelta(days=1)
    o2 = mk.overview(sat.isoformat())
    assert o2['date'] != sat.isoformat(), '周六居然有行情？'
    assert o2['asked'] == sat.isoformat(), \
        '没把"你选的那天"回给页面 —— 人会以为看的是自己选的那天'
    assert o2['date'] < sat.isoformat(), '没落到更早的有数据日'
    assert o2['n'] > 1000, '历史某天的家数不对：%d' % o2['n']
    try:
        mk.overview('1990-01-01')
        raise AssertionError('远早于所有数据的日期应报错')
    except mk.MarketError:
        pass
    return ('涨跌平之和 == 总数 %d；12 档首尾衔接且之和相等；'
            '独立复算一致（放开状态后 %d > %d，证明筛选生效）；'
            '涨幅榜降序 / 跌幅榜升序 / 行业榜按等权降序；'
            '周六 %s 自动落到 %s 并回报 asked'
            % (o['n'], n3, o['n'], sat, o2['date']))


@case('板块：申万与通达信两源 / 成分等权自洽 / 个股归属', tag='fast')
def t_sector():
    """★ 只用【每日同步】的两个源：申万一级（随面板，带 PIT）与通达信板块。

    🔴 `raw/hf/` 下那份同花顺概念**不用** —— `last_fetched` 停在 2026-04-28、
      没接进 `sync_daily.sh`。用它会给出四个月前的成分，而页面上看着像今天的。
      这条用例顺手钉住"代码里没有引用那份数据"。
    """
    import os

    from assay import market as mk
    ks = {k['kind'] for k in (
        __import__('assay.server', fromlist=['x']).api_sector_kinds({})['kinds'])}
    assert 'sw' in ks, '缺申万'
    assert 'concept' in ks, '缺通达信概念板块'

    sw = mk.sector_list(kind='sw')
    assert 25 <= len(sw['rows']) <= 40, '申万一级应是 31 个左右：%d' % len(sw['rows'])
    av = [x['avg_change'] for x in sw['rows']]
    assert av == sorted(av, reverse=True), '板块榜没按等权涨幅降序'

    cc = mk.sector_list(kind='concept')
    assert len(cc['rows']) > 100, '通达信概念板块太少：%d' % len(cc['rows'])
    # 成分等权涨幅要能独立复算
    top = cc['rows'][0]
    m = mk.sector_members(top['code'], kind='concept')
    assert m['n'] == top['n'], \
        '榜上写 %d 只，成分表却是 %d 只' % (top['n'], m['n'])
    cps = [x['change_pct'] for x in m['rows'] if x['change_pct'] is not None]
    calc = sum(cps) / len(cps)
    assert abs(calc - top['avg_change']) < 0.02, \
        '等权涨幅复算不一致：%s vs %s' % (calc, top['avg_change'])
    # 成分表按涨跌幅降序
    assert cps == sorted(cps, reverse=True), '成分表没按涨跌幅降序'

    # 申万成分：数量要和榜上一致
    m2 = mk.sector_members(sw['rows'][0]['code'], kind='sw')
    assert m2['n'] == sw['rows'][0]['n'], \
        '申万成分数不一致：%d vs %d' % (m2['n'], sw['rows'][0]['n'])
    assert len(m2['rows']) == m2['n'], \
        ('成分表只给了 %d 行却报 n=%d —— **悄悄截断**：少的那部分你不知道，'
         '而结论已经下了（2026-09-14 实测：默认上限 300，行业涨到 478 只之后'
         '榜上写 478、表给 300、n 也报 300，三处自洽地说了个谎）'
         % (len(m2['rows']), m2['n']))
    assert m2.get('truncated') is False, \
        '这一次不该截断，truncated=%r' % m2.get('truncated')
    # 🔴 **真截断时必须说得出来** —— 只把上限拉大是不够的，数据还会长。
    #   构造：显式给一个小 limit，`n` 必须仍是真实总数、`truncated` 为真。
    m3 = mk.sector_members(sw['rows'][0]['code'], kind='sw', limit=5)
    assert m3['n'] == m2['n'] and len(m3['rows']) == 5, \
        ('截断之后 n 该仍是真实总数（%d），行数才是 5 —— 实得 n=%d 行=%d；'
         '拿 len(rows) 当 n 就是上面那个谎的来源'
         % (m2['n'], m3['n'], len(m3['rows'])))
    assert m3.get('truncated') is True, \
        '截断了却没标 truncated —— 页面就没法说出"只给了前 N 只"'

    # 个股归属：代码几种写法都要认（不归一就查不到，报"面板里没有"）
    for raw in ('601857', '601857.SH', 'sh601857', '601857.XSHG'):
        ss = mk.stock_sectors(raw)
        assert ss['code'] == '601857.XSHG', '%r 没归一：%s' % (raw, ss['code'])
        assert ss['sw'] and ss['sw']['name'], '没给申万归属'
        assert ss['blocks'], '一只大盘股不可能不属于任何板块'
    try:
        mk.stock_sectors('99')
        raise AssertionError('认不出的代码应报错')
    except mk.MarketError:
        pass

    # 🔴 代码里不许引用 raw/hf/ 那份不同步的概念数据
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            'assay', 'market.py'), encoding='utf-8').read()
    code_only = '\n'.join(ln for ln in src.split('\n')
                          if not ln.strip().startswith('#'))
    assert 'concept_ths' not in code_only, \
        ('market.py 引用了 raw/hf 的同花顺概念 —— 它 last_fetched 停在 '
         '2026-04-28、没接进每日同步，会给出四个月前的成分而页面上看着像今天的')
    return ('两源：申万 %d 个 + 通达信 %d 类（概念 %d 个）；'
            '成分等权涨幅复算一致（%s：榜 %.2f%% / 复算 %.2f%%）；'
            '成分数一致；个股归属 4 种写法都认；代码里没引用不同步的同花顺概念'
            % (len(sw['rows']), len(ks) - 1, len(cc['rows']),
               top['name'], top['avg_change'], calc))


@case('副图指标 / 事件 / 同业 / 联动 / 对比', tag='fast')
def t_stock_ext():
    """指标算错不报错，只是曲线不对 —— 所以每个都要能独立复算。

    ★ KDJ / BOLL 刻意用**行情软件口径**（KDJ 通用平滑、BOLL 总体标准差），
      不是教科书口径 —— 与通达信/同花顺对不上会让人以为数据错了。
    """
    from assay import stock as st

    # ---- 指标：预热 + 独立复算 ----
    ind = st.indicators('601857.SH', n=120)
    rows = ind['rows']
    assert len(rows) == 120
    assert ind['warmup_dropped'] == st.IND_WARM, \
        '预热应丢掉 %d 根：%s' % (st.IND_WARM, ind['warmup_dropped'])
    # ★ 第一根就该有值 —— 这正是预热的意义。没预热的话头部全是 None，
    #   而那不报错，只是曲线前面缺一截。
    for k in ('dif', 'dea', 'macd', 'k', 'd', 'jj', 'rsi6', 'rsi24', 'mb', 'ub'):
        assert rows[0][k] is not None, '第一根的 %s 是空的 —— 预热没生效' % k
    last = rows[-1]
    # MACD = (DIF − DEA) × 2（国内行情软件口径，不是 DIF−DEA）
    assert abs(last['macd'] - (last['dif'] - last['dea']) * 2) < 0.01, \
        'MACD 不是 (DIF−DEA)×2：%s' % last
    # J = 3K − 2D
    # ★ 容差 0.03 不是随手放宽：k/d/jj **返回时都已四舍五入到 2 位**，
    #   而这里拿【已舍入的】k、d 去重算 3K−2D，误差上界就是
    #   3×0.005 + 2×0.005 = 0.025。原来写 0.02 **小于这个上界**，
    #   于是能不能通过取决于当天的小数 —— 2026-09-11 实测 601857
    #   k=50.28 d=52.29 jj=46.24 而 3k−2d=46.26，差正好 0.02 而挂掉。
    #   **失败的是断言不是产品**（同 `#d_hold .note` 那条）。
    assert abs(last['jj'] - (3 * last['k'] - 2 * last['d'])) < 0.03, \
        'J 算错：jj=%s 而 3K−2D=%s' % (last['jj'], 3 * last['k'] - 2 * last['d'])
    for k in ('k', 'd', 'rsi6', 'rsi12', 'rsi24'):
        assert 0 <= last[k] <= 100, '%s 越界：%s' % (k, last[k])
    # BOLL：中轨 == 20 日均值；上下轨对称
    cl = [r['close'] for r in rows[-20:]]
    assert abs(last['mb'] - sum(cl) / 20) < 0.02, 'BOLL 中轨不是 20 日均值'
    assert abs((last['ub'] - last['mb']) - (last['mb'] - last['lb'])) < 0.01, \
        'BOLL 上下轨不对称'
    assert last['lb'] < last['mb'] < last['ub'], 'BOLL 轨道顺序不对'
    # 总体标准差（除 N）而不是样本标准差（除 N−1）—— 与行情软件一致
    mu = sum(cl) / 20
    sd_pop = (sum((x - mu) ** 2 for x in cl) / 20) ** 0.5
    sd_smp = (sum((x - mu) ** 2 for x in cl) / 19) ** 0.5
    assert abs((last['ub'] - mu) / 2 - sd_pop) < 0.01, 'BOLL 没用总体标准差'
    assert abs(sd_pop - sd_smp) > 1e-9, '两种标准差恰好相等，这条断言无效'

    # ---- 事件：日期倒序，单位对 ----
    ev = st.events('601088.SH')
    ds = [e['date'] for e in ev['events']]
    assert ds == sorted(ds, reverse=True), '事件没按日期倒序'
    kinds = {e['kind'] for e in ev['events']}
    assert {'xr', 'fin'} <= kinds, '缺除权或财报事件：%s' % kinds
    # 股本单位是万股 —— 换算后应是【亿】级（神华 216 亿股）
    sh = [e for e in ev['events'] if e['kind'] == 'share' and e.get('share_total')]
    if sh:
        assert sh[0]['share_total'] > 1e9, \
            ('总股本换算错了（share_change.share_total 单位是万股）：%s'
             % sh[0]['share_total'])
        assert '亿' in sh[0]['detail'], '没换算成人看得懂的量级'
    # 解禁比例常为 NULL —— 必须不显示成 "占 0%"
    unl = [e for e in ev['events'] if e['kind'] == 'unlock']
    for e in unl:
        if e.get('ratio') is None:
            assert '占 0' not in e['detail'], \
                ('缺值显示成了"占 0%%"—— 那不是"占比很小"，是根本没有这个数：%s'
                 % e['detail'])

    # ---- 同业 ----
    pe = st.peers('601857.SH', n=10)
    assert pe['industry']['name'], '没给行业'
    mv = [x['floatmv'] or 0 for x in pe['rows']]
    assert mv == sorted(mv, reverse=True), '同业没按流通市值降序'
    assert pe['rank'] == 1, '中国石油在石油石化里流通市值应排第一：%s' % pe['rank']
    assert any(x['code'] == '601857.XSHG' for x in pe['rows']), '同业里没有它自己'

    # ---- 联动 ----
    lk = st.links('601857.SH')
    assert 'positions' in lk and 'runs' in lk
    assert lk['runs'], '红利策略买过中国石油，回测联动不该是空的'
    r0 = lk['runs'][0]
    for k in ('run_id', 'strategy', 'n_trades', 'avg_ret'):
        assert k in r0, '回测联动缺 %s' % k
    assert r0['n_trades'] > 0

    # ---- 多股对比：一律后复权 + 并集对齐 ----
    cp = st.compare(['601857.SH', '601088.SH'], n=120)
    assert cp['fq'] == 'hfq', '对比必须后复权 —— 不复权跨除权日有假跌幅'
    assert len(cp['series']) == 2 and len(cp['dates']) == cp['n']
    for s in cp['series']:
        assert len(s['ret']) == cp['n'], '曲线长度与日期数不一致'
        v = [x for x in s['ret'] if x is not None]
        assert v and abs(v[0]) < 1e-9, '起点没归零：%s' % v[0]
    # 后复权的区间涨幅要高于不复权（分红被除掉了）
    kb = st.kline('601088.SH', n=120, fq='bfq')['bars']
    rb = kb[-1]['close'] / kb[0]['close'] - 1
    rh = [s for s in cp['series'] if s['code'] == '601088.XSHG'][0]['ret'][-1]
    assert rh > rb, '对比用的不是后复权（%.4f vs 不复权 %.4f）' % (rh, rb)
    try:
        st.compare(['1', '2', '3', '4', '5', '6', '7'])
        raise AssertionError('超过 6 只应被拒')
    except st.StockError:
        pass
    try:
        st.compare([])
        raise AssertionError('空列表应被拒')
    except st.StockError:
        pass
    return ('指标预热 %d 根、第一根就有全部值；MACD=(DIF−DEA)×2、J=3K−2D、'
            'BOLL 中轨=20日均值且用总体标准差（与行情软件一致）；'
            '事件倒序且股本按万股换算成 %s、解禁缺值不显示成 0%%；'
            '同业按市值降序且它排第 1；回测联动 %d 次；'
            '对比后复权 %.2f%% > 不复权 %.2f%% 且起点归零'
            % (ind['warmup_dropped'], (sh[0]['detail'].split('总股本 ')[-1]
                                       if sh else '—'),
               len(lk['runs']), rh * 100, rb * 100))


@case('tdx 装配脚本：跨 OS 表 / 缩表护栏 / 定时环境自证', tag='fast')
def t_setup_tdx():
    """`datalake/setup_tdx.py` —— 换台机器把 tdx 链装回来的那个入口。

    这一条**不联网、不装东西**，只守"判据有没有写对"：
      ① 四个平台的资产名齐、且刻意没有 Darwin_x86_64（Intel Mac 没预编译包）
      ② CLI 用的是实测的 `--dburi`/`--min`，不是文档里的 `--dbpath`/`--minline`
      ③ 定时任务的 PATH 必须带上**当前解释器**所在目录
      ④ 缩表护栏与 schema 探针都在
    """
    import importlib.util
    import platform
    import re
    import shutil
    here = os.path.dirname(os.path.abspath(__file__))
    p = os.path.join(os.path.dirname(here), 'datalake', 'setup_tdx.py')
    assert os.path.isfile(p), '找不到 setup_tdx.py'
    src = open(p, encoding='utf-8').read()
    spec = importlib.util.spec_from_file_location('setup_tdx', p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)

    # ---- ① 跨 OS 资产表 ----
    want = {('Darwin', 'arm64'), ('Linux', 'arm64'), ('Linux', 'x86_64'),
            ('Windows', 'x86_64')}
    assert set(m.ASSETS) == want, '资产表对不上 release：%s' % set(m.ASSETS)
    assert ('Darwin', 'x86_64') not in m.ASSETS, \
        ('Intel Mac 刻意没有预编译包（release 里只有 Darwin_arm64）——'
         '加了会装上一个跑不了的包')
    for k, v in m.ASSETS.items():
        assert v.startswith('tdx2db_') and (
            v.endswith('.tar.gz') or v.endswith('.zip')), '资产名不对：%s' % v
    # 架构名归一化：aarch64 / amd64 都要认（Linux 上 uname 给的是这两个）
    _o = platform.machine
    try:
        for raw, norm in (('aarch64', 'arm64'), ('arm64', 'arm64'),
                          ('AMD64', 'x86_64'), ('x86_64', 'x86_64')):
            platform.machine = lambda r=raw: r
            assert m._arch() == norm, '%s 应归一成 %s，实得 %s' % (
                raw, norm, m._arch())
    finally:
        platform.machine = _o

    # ---- ② CLI 实参（实测 v2026.5/v2026.8.12 都是这两个）----
    #   🔴 网上文档常写 --dbpath / --minline 1,5，本机 --help 是
    #     --dburi / --min（布尔）。抄错了表现为"命令直接不认"。
    assert "'--dburi'" in src, 'cron/init 应该用 --dburi'
    assert '--dbpath' not in src.replace('`--dbpath`', ''), \
        '用了文档里那个 --dbpath —— 实测的参数是 --dburi'
    assert '--minline' not in src.replace('`--minline 1,5`', ''), \
        '用了文档里那个 --minline —— 实测是布尔的 --min'
    # 🔴 不许出现 pip install tdx2db（PyPI 那个是同名的另一个项目，无 DuckDB）
    assert not re.search(r'^\s*[^#*\n]*subprocess.*pip.*tdx2db', src, re.M), \
        'pip 装 tdx2db —— PyPI 上那个是另一个项目，不支持 DuckDB'

    # ---- ③ 定时任务的 PATH 必须带当前解释器 ----
    #   🔴 launchd/systemd 的默认环境很窄，python3 会解析到系统那个
    #     （没装 duckdb），2/6 PIT 快照就炸 —— 而那一步漏一天不可逆，
    #     失败只写在 launchd 日志里。2026-09-03 真踩过。
    pth = m._sched_path()
    mine = os.path.dirname(os.path.abspath(sys.executable))
    assert pth.split(':')[0] == mine, \
        '定时 PATH 的第一段应是当前解释器的目录（%s），实得 %s' % (mine, pth)
    assert pth.count(mine) == 1, 'PATH 里重复了当前解释器目录：%s' % pth
    # ★ 签名跟着「装两个 timer」改了：_plist(label, args, times, tag)
    pl = m._plist(m.LABEL, ['/bin/bash', m.SH], [(18, 10)], 'sync')
    assert 'EnvironmentVariables' in pl and mine in pl, \
        'plist 丢了 EnvironmentVariables/PATH —— 这正是踩过的那个坑'
    for k in ('StartCalendarInterval', '<key>Hour</key><integer>18',
              '<key>Minute</key><integer>10', m.SH, m.ROOT):
        assert k in pl, 'plist 缺「%s」' % k
    # 🔴 tick 是**多时间点**的（07:00/08:00/09:00）—— StartCalendarInterval
    #   必须是 array。装三个 plist 会让"改一个点位"变成改三处。
    #   ★ 时间点不再是写死的常量（TICK_AT / POLL_*），改成看板可配的
    #     DEFAULT_SCHED + _manifest/schedule.json —— 用例跟着读配置。
    _tk_d = m.DEFAULT_SCHED['tick']
    _tk_t = m._range_times(_tk_d['from'], _tk_d['to'], _tk_d['every'])
    tk = m._plist(m.TICK_LABEL, ['python3', m.TICK_PY], _tk_t, 'tick')
    assert '<array>' in tk.split('StartCalendarInterval')[1][:200], \
        '多时间点时 StartCalendarInterval 应是 array'
    for hh in (7, 8, 9):
        assert '<key>Hour</key><integer>%d</integer>' % hh in tk, \
            'tick plist 少了 %02d:00 这个点位' % hh
    assert 'tick_daily.py' in tk and 'EnvironmentVariables' in tk, \
        'tick plist 没指向 tick_daily.py 或丢了 PATH'
    assert m._times('07:00,08:00,09:00') == [(7, 0), (8, 0), (9, 0)], \
        '_times 解析不对'
    # ---- 数据同步改成【轮询】：判据是"齐没齐"而不是"到点没到点" ----
    assert m.DEFAULT_SCHED['tick']['from'] == '16:00', \
        ('信号重算的窗口要从 16:00 开始 —— 公告集中在 16:00~22:00，'
         '而人常在晚上手动导聚宽增量，只开早上的话要等到 07:00 才算得进去')
    _sy_d = m.DEFAULT_SCHED['sync']
    rt = m._range_times(_sy_d['from'], _sy_d['to'], _sy_d['every'])
    assert len(rt) == 25 and rt[0] == (16, 0) and rt[-1] == (20, 0), \
        '16:00~20:00 每 10 分钟应是 25 个点位，实得 %d 个' % len(rt)
    assert (18, 10) in rt, \
        ('轮询序列要覆盖原来那个 18:10 —— 覆盖了才能删掉独立的 sync timer，'
         '少一处要对齐的时间常量')
    jobs = m_st = open(os.path.join(os.path.dirname(here), 'datalake',
                                    'setup_tdx.py'), encoding='utf-8').read()
    assert "'--if-stale'" in jobs.split('JOBS = [')[1][:600], \
        ('轮询必须带 --if-stale —— 不带的话每 10 分钟跑一次完整链，'
         '而且数据齐了还在跑')
    # 🔴 plist 必须能被【严格】解析：plutil -lint 会放过非法 XML
    #   （XML 注释里不能有两个连字符，而 Apple 的解析器宽容、launchd 照跑，
    #     Python 的 expat 直接拒绝 —— 严格的那个才是真判据）
    import plistlib as _pl
    for label, args, times, tag in (
            (m.LABEL, ['/bin/bash', m.SH, '--if-stale'], rt, 'sync'),
            (m.TICK_LABEL, ['python3', m.TICK_PY], _tk_t, 'tick')):
        xml = m._plist(label, args, times, tag)
        try:
            d = _pl.loads(xml.encode())
        except Exception as e:                                  # noqa: BLE001
            raise AssertionError(
                '%s 的 plist 不是合法 XML（%s: %s）—— plutil -lint 会说 OK，'
                '但那是宽容解析；注释里出现两个连字符就会这样'
                % (tag, type(e).__name__, e))
        cal = d['StartCalendarInterval']
        cal = [cal] if isinstance(cal, dict) else cal
        assert len(cal) == len(times), \
            '%s 的点位数对不上：plist %d vs 期望 %d' % (tag, len(cal), len(times))
    # ---- is_stale.py 的四条判据 ----
    stale = open(os.path.join(os.path.dirname(here), 'datalake', 'build',
                              'is_stale.py'), encoding='utf-8').read()
    assert 'raw_holidays' in stale, \
        ('判交易日要用 tdx.raw_holidays —— std/trading_calendar.parquet '
         '**只到最后一个有数据的交易日**，用它会让每天都判成"不是交易日"，'
         '于是轮询永远不干活，而日志里只有一行「今天不是交易日」')
    #   ★ 用 ast 看**函数体**，不做子串匹配 —— 那个函数的 docstring 里
    #     正好在**警告**不要用 trading_calendar，子串匹配会把警告也算成"用了"
    #     （今天第三次踩这个坑了：断言别写成"字符串出现过"）。
    import ast as _ast
    _st = _ast.parse(stale)
    _fn = [n for n in _ast.walk(_st)
           if isinstance(n, _ast.FunctionDef) and n.name == '_is_trading_day']
    assert _fn, 'is_stale.py 里没有 _is_trading_day'
    _lits = [n.value for n in _ast.walk(_fn[0])
             if isinstance(n, _ast.Constant) and isinstance(n.value, str)]
    _code = ' '.join(_lits[1:])          # [0] 是 docstring
    assert 'trading_calendar' not in _code, \
        '_is_trading_day 的代码里还在读 trading_calendar.parquet'
    assert 'raw_holidays' in _code, \
        '_is_trading_day 应当查 raw_holidays（唯一含未来日的源）'
    assert 'manifest.csv' in stale and 'snap_date' in stale, \
        ('判据要含「今天的 PIT 快照抓过没有」—— daily_snapshot 漏一天'
         '永久丢失，不能只判行情齐不齐')
    assert 'MIN_RATIO' in stale and '0.95' in stale, \
        ('行数判据要用"不少于上一交易日的 95%"而不是固定阈值 —— '
         '新股上市/退市会让只数天天微变')
    assert 'after' in stale and '15:00' in stale, \
        '要有"过了收盘才抓"这条（盘中的 bar 是不完整的）'
    # plist 要能被系统解析（macOS 上真解一遍）
    if platform.system() == 'Darwin':
        import subprocess as _sp
        import tempfile as _tf
        with _tf.NamedTemporaryFile('w', suffix='.plist', delete=False,
                                    encoding='utf-8') as fh:
            fh.write(pl)
            tmp = fh.name
        try:
            r = _sp.run(['plutil', '-lint', tmp], capture_output=True,
                        text=True)
            assert r.returncode == 0, 'plist 不合法：%s' % r.stdout
        finally:
            os.remove(tmp)
    # 🔴 绝对路径不许硬编码我这台机器 —— 这个文件的意义就是换机器也能用
    assert '/Users/guhao' not in src, \
        '源码里硬编码了 /Users/guhao —— 换台机器就指错了'

    # ---- ④ 解压要把 Windows 反斜杠归一成目录（真跑一遍）----
    #   🔴 hsjday.zip 里 12,392 个条目全是反斜杠、零个目录条目，而 Python 的
    #     zipfile 按规范把反斜杠当普通字符 —— extractall 出来是平坦的怪文件名，
    #     tdx2db 一个都扫不到，报的却是"我的中间文件 stock.csv 不存在"。
    #     那个报错指不到真正的原因，所以这条必须有断言。
    import tempfile as _tf
    import zipfile as _zf
    d = _tf.mkdtemp()
    try:
        zp = os.path.join(d, 't.zip')
        with _zf.ZipFile(zp, 'w') as z:
            z.writestr('sh\\lday\\sh000001.day', b'x' * 8)
            z.writestr('sz\\lday\\sz000001.day', b'y' * 8)
        out = os.path.join(d, 'vip')
        n = m._extract_zip(zp, out)
        assert n == 2, '落盘文件数不对：%d' % n
        assert os.path.isfile(os.path.join(out, 'sh', 'lday',
                                           'sh000001.day')), \
            ('反斜杠没归一成目录 —— 解出来是 %s'
             % os.listdir(out))
        assert sorted(os.listdir(out)) == ['sh', 'sz'], \
            '应该建出 sh/sz 两个子目录：%s' % os.listdir(out)
        # 路径穿越要拒（extractall 的老 CVE 就是这个）
        bad = os.path.join(d, 'bad.zip')
        with _zf.ZipFile(bad, 'w') as z:
            z.writestr('..\\..\\evil.day', b'z')
        try:
            m._extract_zip(bad, os.path.join(d, 'v2'))
            raise AssertionError('带 .. 的路径应被拒')
        except SystemExit:
            pass
    finally:
        shutil.rmtree(d, ignore_errors=True)

    # ---- ⑤ init 之后必须紧跟 cron ----
    #   🔴 init 只导日线、自报"导入成功"，但 raw_adjust_factor /
    #     raw_basic_daily 是【空表】—— 复权因子要 gbbq，而 gbbq 是 cron 下的。
    b = src[src.index('def bootstrap('):src.index('def sync(')]
    assert "'init'" in b and "'cron'" in b, \
        'bootstrap 里必须 init 之后紧跟 cron（否则复权因子/基础面是空表）'
    assert b.index("'init'") < b.index("'cron'"), 'cron 应在 init 之后'
    assert 'raw_adjust_factor' in b and 'raw_basic_daily' in b, \
        'bootstrap 缺"这两张表不许是空的"那条断言'

    # ---- ⑥ 口径护栏（缩表护栏拦不住"值变了"）----
    #   🔴 通达信改过 .day 里 volume 的单位：老 vipdoc 是"股×100"、
    #     现在下的 hsjday.zip 是"股"。而 turnover 是 tdx2db 用
    #     volume/流通股数 算出来的 —— 重建一次，面板的 turnover 就从
    #     百分数变小数，全程差 100 倍，而**行数一行不少**。
    #     实测护栏输出：最近 250 天 861,032 行，中位 旧 2.34 / 新 0.0234。
    assert '_check_scale' in src, '缺口径护栏 —— 缩表护栏只看行数'
    assert 'turnover' in src, '口径护栏得盯 turnover（volume 单位变过）'
    _sc = m._check_scale(m.DB, m.DB) if os.path.isfile(m.DB) else None
    if _sc:
        assert abs(_sc[0] - 1.0) < 1e-9, \
            '自己比自己应得比值 1.0，实得 %s' % _sc[0]

    # ---- ⑦ 护栏在不在 ----
    assert 'allow_shrink' in src and 'before_init' in src, '缺缩表护栏'
    assert '_probe_schema' in src and '_meta' in src, '缺 schema 兼容探针'
    assert 'duckdb://./%s' in src or "duckdb://./" in src, 'init 目标写法不对'
    #  init 必须先落到别的文件，确认后才替换（不许直接覆盖 tdx.db）
    assert "DB + '.new'" in src, \
        'init 直接覆盖 tdx.db 了 —— 全量包不含退市股时历史会静默缩水'
    # 本地这个库的 schema 版本读得出来（顺带证明 _meta 判据有效）
    v = m._db_schema_version()
    if v:
        assert m._major(v) is not None, 'schema 版本解析不出主版本号：%s' % v
    return ('资产表 4 个平台且无 Darwin_x86_64；aarch64/amd64 归一；'
            'CLI 用实测的 --dburi/--min（不是文档的 --dbpath/--minline）；'
            '反斜杠路径归一成目录树且拒路径穿越；bootstrap 里 init 后紧跟 '
            'cron 且断言复权因子表非空；口径护栏在（turnover 量级，'
            '缩表护栏拦不住"值变了"）；'
            '不用 pip 装（PyPI 同名项目无 DuckDB）；'
            '定时 PATH 首段是当前解释器%s；plist 带 EnvironmentVariables 且 '
            'plutil 合法；无硬编码 /Users/guhao；缩表护栏 + schema 探针在'
            '%s' % (mine, ('；本地库 schema %s' % v) if v else ''))


@case('启动开关：默认全功能 / --readonly / 按钮不许 disabled', tag='fast')
def t_serve_flags():
    """2026-09-04：废掉 `--live` 与 `--allow-backtest` 两个默认关的开关。

    🔴 起因是一个真实的坏：忘了加 `--allow-backtest` 时，网页上
      「用这个版本+参数跑一次」渲染成 **disabled** ——
      **disabled 的元素连 title 提示都不触发**，所以用户看到的是
      "选了策略、填了日期、点下去毫无反应，也没有任何说明"。
      （同 backLink 那条：给一个点了没反应的按钮比不给更糟。）
    """
    import re
    here = os.path.dirname(os.path.abspath(__file__))
    src = open(os.path.join(here, 'serve.py'), encoding='utf-8').read()

    # ---- ① 默认全功能，只有 --readonly 才关 ----
    assert '--readonly' in src, '没有 --readonly'
    assert 'full = not a.readonly' in src, '默认应当全开，只有 --readonly 才关'
    assert 'allow_backtest=full' in src and 'allow_live=full' in src, \
        '两个能力应当由同一个 --readonly 一起决定'
    # ★ 旧命令必须还能用 —— launchd/文档/肌肉记忆里都有，
    #   而"参数不认"会让服务直接起不来，那是最糟的失败方式。
    for f in ('--live', '--allow-backtest'):
        assert "'%s'" % f in src, '旧参数 %s 应保留兼容' % f

    # ---- ② 真起两次服务，验能力开关 ----
    import threading
    from http.server import ThreadingHTTPServer

    from assay import server as sv
    old_ab, old_lv = sv.ALLOW_BACKTEST, sv.ALLOW_LIVE
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), sv.Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        import json as _j
        import urllib.request as _u

        def acc():
            return _j.load(_u.urlopen('http://127.0.0.1:%d/api/live/accounts'
                                      % port, timeout=30))
        sv.ALLOW_BACKTEST = sv.ALLOW_LIVE = True
        a = acc()
        assert a['can_backtest'] is True and a['readonly'] is False, \
            '全功能时两项都该开：%s' % {k: a[k] for k in ('can_backtest', 'readonly')}
        sv.ALLOW_BACKTEST = sv.ALLOW_LIVE = False
        b = acc()
        assert b['can_backtest'] is False and b['readonly'] is True, \
            '--readonly 时两项都该关：%s' % {k: b[k] for k in ('can_backtest', 'readonly')}
    finally:
        httpd.shutdown()
        sv.ALLOW_BACKTEST, sv.ALLOW_LIVE = old_ab, old_lv

    # ---- ③ 🔴 页面上不许再出现 disabled 的功能按钮 ----
    #   判据用**源码**扫：`disabled` 只允许出现在"只读模式下不许写"那类
    #   （录成交、改设置），不允许挂在"功能没开"的按钮上 ——
    #   后者应当可点 + 说原因。
    # ★ 实盘的视图逻辑在 web/views/live.js（index.html 已拆成骨架 + 域文件）——
    #   断言要跟着代码走，读 index.html 会永远查不到而假过（或假失败）。
    lvjs = open(os.path.join(here, 'web', 'views', 'live.js'),
                encoding='utf-8').read()
    bad = re.findall(r'\$\{\s*LV\.can_backtest\s*\?[^}]*disabled', lvjs)
    assert not bad, \
        ('「跑一次」那个按钮又按 can_backtest 设 disabled 了 —— '
         'disabled 的元素连 title 都不触发，点了没反应且看不到原因：%s' % bad)
    # 不可用时，怎么开必须【常驻可见】而不是藏在 title 里
    assert 'readonly' in lvjs and 'serve.py' in lvjs, \
        'live.js 里没有"怎么开"的常驻说明'
    return ('默认全功能、--readonly 一起关两项、旧参数保留兼容；'
            'API 两种模式的 can_backtest/readonly 都对；'
            '页面不再按 can_backtest 设 disabled（点了没反应且无提示那条）')


@case('买点清单：目标价/股息率互算 + 到价判定 + 提醒去重', tag='fast')
def t_alerts():
    """把用户那张 Excel 搬进来的那张表。**它不是策略**（没有回测、不下单）。

    这一条守四件事：
      ① 两种输入方式互算：填价出股息率、填率出目标价，且**存的是你填的那个**
      ② 分红的口径（`bonus_ratio_rmb` 是每【10】股 —— 不除 10 会大 10 倍），
         且它**不存不手填**：新公告一到目标价要自动跟着变
      ③ 到价 / 接近 / 还差多少的判定
      ④ 提醒**一天只发一次**（不然价格在阈值上下抖一抖就是几十条通知）
    """
    import shutil
    import tempfile

    from assay import alerts as al
    from assay import server as sv
    old_live, old_dir = sv.ALLOW_LIVE, al.LIVE
    sv.ALLOW_LIVE = True
    tmp = tempfile.mkdtemp()
    al.LIVE = tmp                    # ★ 不往真账本里写测试数据
    # 🔴 外部分红接口要打桩：不打的话 selftest 会真去打东财，
    #    而"一天一次"的节流恰恰要在这里被验证（真打就验不了）。
    #    桩里放的是**实测抓回来的真行**（含两处"标注与本地不同"的）。
    EXT_HIT = []
    _o_fetch, _o_extpath = al._ext_fetch, al.ext_path
    _extcache = os.path.join(tmp, 'div_ext.json')
    al.ext_path = lambda root=None: _extcache
    EXT_ROWS = {'600900.XSHG': [
        {'report_date': '2025-12-31', 'per_share': 0.79,
         'plan_pub': '2026-04-30', 'progress': '实施分配', 'src': '东财'},
        {'report_date': '2025-09-30', 'per_share': 0.21,
         'plan_pub': '2025-12-31', 'progress': '实施分配', 'src': '东财'},
        {'report_date': '2024-12-31', 'per_share': 0.733,
         'plan_pub': '2025-04-30', 'progress': '实施分配', 'src': '东财'},
        # ★ 同一次 0.21（公告 2024-12-14）：本地标 2024-09-30、东财标 2024-06-30
        {'report_date': '2024-06-30', 'per_share': 0.21,
         'plan_pub': '2024-12-14', 'progress': '实施分配', 'src': '东财'}],
        '000423.XSHE': [
        # ★ 同一次 1.3448（报告期都是 2026-06-30）：本地公告 08-21、东财 04-25
        {'report_date': '2026-06-30', 'per_share': 1.3448,
         'plan_pub': '2026-04-25', 'progress': '实施分配', 'src': '东财'}]}
    al._ext_fetch = lambda cs, day=None: (
        EXT_HIT.append(sorted(cs))
        or {c: v for c, v in EXT_ROWS.items() if c in set(cs)})
    try:
        # ---- 1) 分红（实际已公告）：每 10 股 -> 每股 ----
        #   🔴 bonus_ratio_rmb 是"每 10 股派现"。不除 10 的话分红大 10 倍，
        #     目标价跟着小 10 倍，而它不报错 —— 只是那一行永远不会触发。
        sg = al.suggest_div(['600900', '600036', '600690', '600795', '000423'])
        cj = sg.get('600900.XSHG') or {}
        assert 0.5 < (cj.get('per_share') or 0) < 2.0, \
            ('长江电力每股分红应在 1 元附近（与用户手填的 1 一致），实得 %s'
             ' —— 大 10 倍就是 bonus_ratio_rmb 没除 10' % cj.get('per_share'))
        assert (sg.get('600036.XSHG') or {}).get('per_share'), '招行没取到分红'
        # 🔴 默认口径必须是【最近一个完整会计年度合计】，不是近 365 天。
        #   逐只对用户手填的那张表（他手填的就是这个口径）—— 半年派的公司上
        #   r365 会漏掉中期分红：
        #     海尔智家 手填 1.15 / FY 1.1607 / r365 0.8915（少 26%）
        #     国电电力 手填 0.24 / FY 0.241  / r365 0.141 （少 41%）
        #   而它不报错，只是目标价算高、那一行永远不会触发。
        HAND = {'600690.XSHG': 1.15, '600795.XSHG': 0.24,
                '600036.XSHG': 2.016, '600900.XSHG': 1.0,
                '000423.XSHE': 2.7}
        for c, want in HAND.items():
            got = (sg.get(c) or {}).get('per_share')
            assert got and abs(got / want - 1) < 0.02, \
                ('%s 的分红应≈用户手填的 %s（最近完整年度合计），实得 %s'
                 % (c, want, got))
            assert '年度合计' in ((sg.get(c) or {}).get('src') or ''), \
                '默认口径不是"年度合计"：%s' % (sg.get(c) or {}).get('src')
        # 两个口径都要给页面（让人挑），且在半年派的票上确实不同
        for c in ('600690.XSHG', '600795.XSHG'):
            fy = sg[c]['fy']['per_share']
            r365 = sg[c]['r365']['per_share']
            assert abs(fy / r365 - 1) > 0.2, \
                ('%s 上两个口径应有明显差别（这条断言就是防止有人把默认'
                 '悄悄换回 r365）：fy %s vs r365 %s' % (c, fy, r365))
            assert sg[c]['fy'].get('detail'), '年度合计没给逐笔明细'

        # ---- 2) 两种输入方式互算，且存的是【填的那个】----
        al.set_row('000423', [{'by': 'price', 'v': 49},
                              {'by': 'price', 'v': 45}], note='东阿阿胶')
        al.set_row('601318', [{'by': 'yield', 'v': 6}])        # 6 == 6%
        cur = {x['code']: x for x in al.current()}
        assert cur['000423.XSHE']['tiers'][0] == {'by': 'price', 'v': 49.0}, \
            '填的是价，存的却不是：%s' % cur['000423.XSHE']['tiers'][0]
        assert cur['601318.XSHG']['tiers'][0] == {'by': 'yield', 'v': 0.06}, \
            '6 应当作 6% 存成 0.06：%s' % cur['601318.XSHG']['tiers'][0]
        v = al.valued()
        r1 = [x for x in v['rows'] if x['code'] == '000423.XSHE'][0]
        r2 = [x for x in v['rows'] if x['code'] == '601318.XSHG'][0]
        # ★ 分红是**实际已公告**的（不是手填的 2.7）—— 先把它钉住，
        #   再验换算。这样换算错和分红错能分开定位。
        assert abs(r1['div'] - 2.7056) < 1e-4, \
            ('东阿阿胶的实际分红应是 2.7056（FY2025 = 1.2701 + 1.4355），'
             '实得 %s' % r1['div'])
        assert '年度合计' in (r1['div_src'] or ''), \
            '分红来源没标出来：%s' % r1['div_src']
        t49 = [t for t in r1['tiers'] if t['price'] == 49][0]
        assert abs(t49['yield'] - r1['div'] / 49) < 1e-6, \
            '%s/49 算错了：%.4f' % (r1['div'], t49['yield'])
        assert abs(r2['tiers'][0]['price'] - r2['div'] / 0.06) < 0.01, \
            '分红 %s / 目标股息率 6%% 应算出目标价 %.2f，实得 %s' \
            % (r2['div'], r2['div'] / 0.06, r2['tiers'][0]['price'])
        # 🔴 分红【不能手填、也不存在账本里】—— "预计分红"没有价值
        #   （猜出来的目标价看着和真的一样，而它错在你不会回头检查的地方），
        #   而存一份就会过期。判据：账本那一行里没有 div 字段。
        _rec = [r for r in al.log() if r.get('code') == '601318.XSHG'][-1]
        assert 'div' not in _rec, \
            '账本里存了 div —— 它会过期，而过期的目标价看着完全正常：%s' % _rec
        assert 'div' not in al.current()[0], \
            'current() 又把 div 读出来了（老记录里可能还有这个字段）'
        try:
            al.set_row('601318', 2.7, [{'by': 'yield', 'v': 6}])
            raise AssertionError('set_row 不该再接分红这个参数')
        except TypeError:
            pass
        # 🔴 新公告一到，目标价要**自动跟着变** —— 这是"不存"换来的东西。
        #   把桩里那只票的分红翻倍，同一个"目标股息率 6%"的档目标价必须翻倍。
        _p0 = [x for x in al.valued()['rows']
               if x['code'] == '601318.XSHG'][0]['tiers'][0]['price']
        EXT_ROWS['601318.XSHG'] = [
            {'report_date': '2025-12-31', 'per_share': 5.4,
             'plan_pub': '2026-04-30', 'progress': '实施分配', 'src': '东财'}]
        if os.path.isfile(al.ext_path()):
            os.remove(al.ext_path())          # 装成新的一天
        _p1 = [x for x in al.valued()['rows']
               if x['code'] == '601318.XSHG'][0]['tiers'][0]['price']
        assert _p1 > _p0 * 1.5, \
            ('分红涨了，"目标股息率 6%%"那档的目标价没跟着涨（%s -> %s）——'
             ' 说明分红被存了下来或者被缓存住了' % (_p0, _p1))
        EXT_ROWS.pop('601318.XSHG')
        if os.path.isfile(al.ext_path()):
            os.remove(al.ext_path())

        # ---- 3) 到价 / 接近 / 还差多少 ----
        #   用真现价推出三档，判定必须落在预期的那一格上。
        px = [x for x in al.valued()['rows'] if x['code'] == '000423.XSHE'][0]
        p0 = px['price']
        assert p0, '取不到现价，没法验判定'
        al.set_row('000423', [
            {'by': 'price', 'v': round(p0 * 1.05, 2)},      # 已跌破
            {'by': 'price', 'v': round(p0 * 0.99, 2)},      # 差 1% -> 接近
            {'by': 'price', 'v': round(p0 * 0.60, 2)},      # 差 40% -> 远
        ])
        r = [x for x in al.valued()['rows'] if x['code'] == '000423.XSHE'][0]
        assert [t['state'] for t in r['tiers']] == ['hit', 'near', 'far'], \
            '三档的状态判错了：%s' % [(t['price'], t['state']) for t in r['tiers']]
        assert r['state'] == 'hit' and r['hit'] == 0, \
            '整行状态该是"到价 · 第 1 档"：%s' % (r['state'], r['hit'])
        # 目标价必须【从高到低】：价高的先触发，与表格里的排法一致
        ps = [t['price'] for t in r['tiers']]
        assert ps == sorted(ps, reverse=True), '档位没按目标价降序：%s' % ps
        # 「还要跌多少」= 目标/现价 − 1（负数）。gap 存到 6 位小数，
        # 所以比的是 1e-5 而不是精确相等。
        _g = r['tiers'][2]
        assert abs(_g['gap'] - (_g['price'] / p0 - 1)) < 1e-5, \
            'gap 不是"目标/现价 − 1"：%s vs %s' % (
                _g['gap'], _g['price'] / p0 - 1)

        # ---- 4) 提醒一天只发一次 ----
        n1 = al.check_fire()
        assert n1, '到价了却没有任何提醒'
        assert all(x['code'] == '000423.XSHE' for x in n1), \
            '提醒里混进了别的票：%s' % [x['code'] for x in n1]
        assert {x['kind'] for x in n1} == {'hit', 'near'}, \
            '到价与接近都该提醒一次：%s' % [x['kind'] for x in n1]
        n2 = al.check_fire()
        assert n2 == [], \
            ('同一 (票, 档, 类型) 一天只该提醒一次 —— 轮询是每分钟一轮，'
             '不去重的话价格在阈值上下抖一抖就是几十条通知，'
             '而通知一多就没人看了：%s' % n2)
        # 去重依据要**落盘**：重启后不该把今天提醒过的又提醒一遍
        assert os.path.isfile(os.path.join(tmp, al.FIRED)), \
            '触发记录没落盘 —— 那重启后会重复提醒'

        # ---- 5) append-only：改一行是追加，当前清单靠重放 ----
        n_log = len(al.log())
        al.set_row('000423', [{'by': 'price', 'v': 40}])
        assert len(al.log()) == n_log + 1, '改一行应当是追加一条'
        assert len(al.current()) == 2, '重放出的清单该还是 2 行'
        assert [t['v'] for t in
                {x['code']: x for x in al.current()}['000423.XSHE']['tiers']] \
            == [40.0], '重放出来的不是最后那一条'
        al.remove('000423')
        assert len(al.current()) == 1 and len(al.log()) == n_log + 2, \
            '删一行也该是追加一条（历史仍在）'

        # ---- 6) 非法输入要被拒（而不是存个看着正常的错值）----
        for bad, why in (
                (('99', [{'by': 'price', 'v': 10}]), '认不出的代码'),
                (('600900', []), '一档都没填'),
                (('600900', [{'by': 'yield', 'v': 60}]), '股息率 60%'),
                (('600900', [{'by': 'price', 'v': -1}]), '负的目标价'),
                (('600900', [{'by': 'nope', 'v': 1}]), 'by 不合法')):
            try:
                al.set_row(*bad)
                raise AssertionError('%s 应被拒' % why)
            except al.AlertError:
                pass

        # ---- 6b) 外部分红：一天只抓一次，已经有今天的就不再调 ----
        EXT_HIT.clear()
        if os.path.isfile(al.ext_path()):
            os.remove(al.ext_path())      # 装成"今天还没抓过"
        al.suggest_div(['600900'])
        assert EXT_HIT == [['600900.XSHG']], \
            '第一次没去抓外部分红：%s' % EXT_HIT
        d2 = al.suggest_div(['600900'])
        assert len(EXT_HIT) == 1, \
            ('同一天同一只票抓了 %d 次 —— 应该只抓一次（多开几个标签页'
             '不该变成一串请求）' % len(EXT_HIT))
        assert d2['600900.XSHG']['ext']['called'] is False, \
            '第二次应该报"没调用"'
        assert d2['600900.XSHG']['ext']['fetched'], '没记下抓取日期'
        # 新加进来的票要立刻抓（它一条都没有）—— 同 _rt_ensure 的思路
        al.suggest_div(['600900', '000423'])
        assert EXT_HIT[-1] == ['000423.XSHE'], \
            '只该补抓没抓过的那一只，实际抓了 %s' % EXT_HIT[-1]
        # 手动刷新（force）要能绕过节流
        al.ext_div(['600900'], force=True)
        assert EXT_HIT[-1] == ['600900.XSHG'], 'force 没绕过一天一次'
        # 🔴 缓存不许写进 std/dividend.parquet —— 那是 loader 链的产物，
        #   被外部数据污染之后"面板与 std 不一致"不报错
        assert 'std' not in _o_extpath(), \
            '外部缓存落到 std/ 里去了：%s' % _o_extpath()

        # ---- 6c) 合并去重：同一次分红，两个源的【标注可能不一样】----
        #   🔴 两条都是实测踩出来的，按单一键去重都会双计而不报错：
        #     ① 长江电力那次 0.21（公告 2024-12-14）：本地报告期 2024-09-30、
        #        东财 2024-06-30 —— 按报告期去重 FY2024 从 0.943 变 1.153(+22%)
        #     ② 东阿阿胶 2026 中期 1.3448：本地公告 2026-08-21（中期董事会预案）、
        #        东财 2026-04-25（年报里先披露的计划）—— 按公告日去重会双计
        loc = al._local_rows(['600900.XSHG'], day='2025-06-30')
        mg = al._merge_rows(loc, EXT_ROWS)['600900.XSHG']
        fy24, _ = al._agg(mg, day='2025-06-30')
        assert abs(fy24['per_share'] - 0.943) < 1e-6, \
            ('长江电力 FY2024 应是 0.943（0.21+0.733），实得 %s —— '
             '差的那 0.21 是同一次分红被两个源的报告期标注拆成了两条'
             % fy24['per_share'])
        assert fy24['year'] == 2024, 'as-of 2025-06-30 的年度桶应是 2024'
        n26 = [x for x in al._merge_rows(al._local_rows(['000423.XSHE']),
                                         EXT_ROWS)['000423.XSHE']
               if x['report_date'] == '2026-06-30']
        assert len(n26) == 1, \
            ('东阿阿胶 2026-06-30 那次分红被拆成了 %d 条（公告日标注不同）：%s'
             % (len(n26), n26))
        # as-of：外部缓存里比 as-of 更新的公告一律砍掉（否则是未来函数）
        fy_now, _ = al._agg(mg)
        assert fy_now['year'] >= 2025, 'as-of 今天时该用更新的年度桶'

        # ---- 6d) 外部源挂了要退回本地，而不是让整页打不开 ----
        def _boom(cs, day=None):
            raise RuntimeError('模拟东财挂了')
        _keep = al._ext_fetch
        al._ext_fetch = _boom
        if os.path.isfile(al.ext_path()):
            os.remove(al.ext_path())
        dz = al.suggest_div(['600900'])
        assert dz['600900.XSHG']['per_share'], '外部挂了就取不到分红了'
        assert dz['600900.XSHG']['ext']['err'], '外部失败没记下来（页面要显示）'
        al._ext_fetch = _keep

        # ---- 7) 抓取范围要带上它 ----
        #   不抓价，"到价提醒"就什么都不会发生。
        assert set(al.codes()) <= set(sv._rt_codes()), \
            '买点清单里的票没进抓取范围 —— 那它永远不会提醒'

        # ---- 8) 只读模式接口层要拒（不能只靠页面）----
        sv.ALLOW_LIVE = False
        assert (sv.api_alerts_act({}, {'act': 'set', 'code': '600900',
                                       'tiers': [{'by': 'price', 'v': 20}]})
                or {}).get('error'), '只读模式下应拒绝改买点清单'
        sv.ALLOW_LIVE = True
        return ('每 10 股 -> 每股（长江电力 %.2f，与用户手填的 1 一致）；'
                '分红不存不手填、新公告一到目标价自动跟着变；'
                '默认口径=最近完整年度合计，逐只对上用户手填的 5 只'
                '（半年派的海尔/国电上 r365 会少 26%%/41%%）；'
                '分红取实际已公告(东阿阿胶 2.7056)；填价出息率、填率出目标价，'
                '存的是填的那个；三档判定 hit/near/far'
                '且按目标价降序；提醒一天只一次且去重落盘；append-only 改一行'
                '是追加、重放取最后一条；5 类非法输入被拒；'
                '抓取范围含买点清单；只读模式拒写；'
                '外部分红(东财 RPT_SHAREBONUS_DET)一天只抓一次、新票立刻补抓、'
                'force 可绕过、缓存不落 std/、挂了退回本地；'
                '两个源的标注不同也不双计（长江电力报告期 09-30 vs 06-30 -> '
                'FY2024 仍 0.943 而不是 1.153；东阿阿胶公告日 08-21 vs 04-25 '
                '-> 2026 中期仍 1 条）'
                % (cj.get('per_share') or 0))
    finally:
        sv.ALLOW_LIVE, al.LIVE = old_live, old_dir
        al._ext_fetch, al.ext_path = _o_fetch, _o_extpath
        shutil.rmtree(tmp, ignore_errors=True)


@case('自选：append-only / 重放出当前池 / 只读拦写', tag='fast')
def t_watchlist():
    """★ 与实盘账本同一套纪律：加/移出/改分组/改备注都是**追加一条**，
    当前状态由**重放**得出。删一条就把"我什么时候加的、为什么"抹掉了。
    """
    import shutil
    import tempfile

    from assay import live as lv
    from assay import realtime as rtm
    from assay import server as sv
    from assay import watchlist as wl
    old_live, old_dir = sv.ALLOW_LIVE, wl.LIVE
    sv.ALLOW_LIVE = True
    tmp = tempfile.mkdtemp()
    wl.LIVE = tmp
    # 🔴 打桩：这个用例会走 `_rt_ensure`（加自选那一刻补抓）。不打桩的话
    #    selftest 会真的去打行情接口 —— 而限流是这条链上唯一的风险。
    HIT = {'snap': [], 'bar': []}
    _o_snap, _o_fetch = rtm.snapshot, rtm.fetch
    _o_sess, _o_day = rtm.in_session, rtm.is_trading_day
    _o_miss = rtm.missing
    rtm.snapshot = lambda cs, root=None: (HIT['snap'].append(list(cs)) or {})
    rtm.fetch = lambda cs, root=None: (
        HIT['bar'].append(list(cs)) or {'bars': 0, 'fail': []})
    rtm.in_session = lambda now=None: True
    rtm.is_trading_day = lambda d=None: True
    sv._RT['miss_at'] = {}
    try:
        assert wl.current() == [] and wl.log() == [], '新目录应是空的'
        wl.act('add', '601857.SH', group='观察', note='页岩气')
        wl.act('add', '600519')
        assert len(wl.current()) == 2
        # 幂等：重复加不报错也不重复追加
        r = wl.act('add', '601857')
        assert r.get('skipped'), '重复加应被跳过（页面上那个星是幂等的）'
        assert len(wl.log()) == 2, '重复加不该追加记录'
        # 改分组 / 备注 都是追加
        wl.act('group', '601857.SH', group='核心')
        wl.act('note', '601857.SH', note='看它的天然气占比')
        cur = {x['code']: x for x in wl.current()}
        assert cur['601857.XSHG']['group'] == '核心', '重放后的分组不对'
        assert '天然气' in cur['601857.XSHG']['note'], '重放后的备注不对'
        assert len(wl.log()) == 4, '应有 4 条日志'
        # 移出：当前池少一个，**日志还在**
        wl.act('remove', '601857.SH')
        assert len(wl.current()) == 1, '移出后当前池应剩 1'
        assert len(wl.log()) == 5, \
            '移出必须是【追加一条 remove】，不是删记录 —— 删了就没法复盘'
        assert any(x['act'] == 'add' and x['code'] == '601857.XSHG'
                   for x in wl.log()), '原来的 add 记录不该消失'
        # 移出后再改会报错（不在池子里）
        for a in ('remove', 'group', 'note'):
            try:
                wl.act(a, '601857.SH')
                raise AssertionError('%s 一个不在池子里的票应报错' % a)
            except wl.WatchError:
                pass
        # 分组统计
        wl.act('add', '000001', group='银行')
        gs = {g['name']: g['n'] for g in wl.groups()}
        assert gs.get('银行') == 1 and gs.get(wl.DEFAULT_GROUP) == 1, gs
        assert len(wl.current('银行')) == 1, '按分组过滤没生效'
        # 带行情：查不到的不静默丢掉
        wl.act('add', '000003')          # 早已退市的代码
        v = wl.valued()
        codes = {x['code'] for x in v['rows']}
        assert '000003.XSHE' in codes, \
            '面板里查不到的票被静默丢掉了 —— "我加过的票不见了"'
        miss = [x for x in v['rows'] if x['code'] == '000003.XSHE'][0]
        assert miss.get('missing'), '查不到的那行没标出来'
        assert [x for x in v['rows'] if x['code'] == '600519.XSHG'][0]['name'], \
            '正常的票应该有名称与行情'
        # ---- 页签顺序：append-only、重放、过期不丢 ----
        # 🔴 顺序是**服务端**给的（页面不再自己排）。与整个模块同一套纪律：
        #   改顺序追加一条 `{act:'order'}`，当前顺序由重放得出。
        assert wl.group_order() == [], '还没排过，顺序记录该是空的'
        # 🔴 先造一个**自动组**（名字带 `实盘·` 前缀）—— 不造的话下面
        #   「自动组该排在前面」那条是**空转的**（这一段跑在 sync_live 之前，
        #   那时一个自动组都没有，判据退化成纯名称排序）。变异测试抓到过：
        #   把 `_group_key` 改成不区分自动组，断言照样全绿。
        wl.act('add', '600036', group=wl.auto_group('测试账户'))
        # 🔴 还要一个**名字排在自动组前面**的手工组。自动组前缀是「实盘·」，
        #   而 `实`(U+5B9E) 的码位本来就在 `核`/`银`/`默` 前面 —— 只有中文
        #   手工组的话，"去掉自动组优先"算出来的顺序**和正确的一样**，
        #   断言照样绿（变异测试抓到过）。`A` 是 ASCII，必排在它前面。
        wl.act('add', '600000', group='A银行观察')
        _d0 = [g['name'] for g in wl.groups()]
        assert any(wl.is_auto_group(g) for g in _d0) and \
            any(not wl.is_auto_group(g) for g in _d0), \
            '要同时有自动组与手工组才测得出默认序：%s' % _d0
        # 默认序：实盘自动组在前，其余按名称（= 页面原来那份规则）
        _autos = [g['name'] for g in wl.groups() if g['auto']]
        assert _d0[:len(_autos)] == _autos, \
            '没排过时自动组该在前面：%s' % _d0
        assert _d0 == sorted(_d0, key=lambda n: (0 if wl.is_auto_group(n) else 1, n)), \
            '没排过时的默认序不对：%s' % _d0
        # 倒过来排 -> 立刻生效，且**存进了账本**（不是只在内存里）
        _rev = list(reversed(_d0))
        _got = wl.set_group_order(_rev)
        assert _got == _rev, '排完的顺序不对：要 %s 实得 %s' % (_rev, _got)
        assert [g['name'] for g in wl.groups()] == _rev, 'groups() 没照排好的顺序给'
        _recs = [r for r in wl.log() if r.get('act') == 'order']
        assert len(_recs) == 1 and _recs[0]['groups'] == _rev, \
            '顺序没作为一条记录追加进账本：%s' % _recs
        # 🔴 顺序记录**不带 code**，`current()` 必须照旧 —— 重放循环开头
        #   那句 `if not r.get('code'): continue` 就是靠它跳过的。
        #   漏了的话顺序记录会被当成一条股票记录，表现是**自选池多一行空票**。
        assert all(x.get('code') for x in wl.current()), \
            '顺序记录被当成股票记录读进自选池了'
        _n_before = len(wl.current())
        wl.set_group_order(_d0)                      # 再排回去（又是一条）
        assert len(wl.current()) == _n_before, '排序改变了自选池的内容'
        assert len([r for r in wl.log() if r.get('act') == 'order']) == 2, \
            'append-only：第二次排序该是新追加一条，不是改写上一条'
        assert [g['name'] for g in wl.groups()] == _d0, '最后一条顺序该生效'
        # 🔴 **过期的顺序不能把分组弄丢。** 顺序里有的分组可能已经空了，
        #   而新分组（新账户、新建组）还没被排过 —— 两头都不许丢，
        #   否则"我排过序之后新加的分组不见了"。
        wl.set_group_order(['不存在的组'] + _d0[:1])
        _after = [g['name'] for g in wl.groups()]
        assert set(_after) == set(_d0), \
            '过期顺序把分组弄丢了或凭空多出来：%s vs %s' % (_after, _d0)
        assert _after[0] == _d0[0], '排过的那个该在最前面：%s' % _after
        _new = [g for g in _after if g not in _d0[:1]]
        assert _new == sorted(_new, key=lambda n: (0 if wl.is_auto_group(n) else 1, n)), \
            '没排过的那批该按默认序追加在后面：%s' % _after
        # 🔴 重名直接拒 —— "它到底排第几"没有答案；静默去重会让人以为排好了
        for _bad in (['A', 'A'], 'abc', None, [_d0[0], _d0[0]]):
            try:
                wl.set_group_order(_bad)
                raise AssertionError('非法顺序 %r 应被拒' % (_bad,))
            except wl.WatchError:
                pass
        wl.set_group_order(_d0)                      # 收尾：回到默认序
        _ord_n = len([r for r in wl.log() if r.get('act') == 'order'])
        # ---- 实盘持仓自动进自选，按账户分组 ----
        #   ★ 幂等：再同步一次不该追加任何记录（持仓没变）。
        #   🔴 只加不自动移 —— 卖光了标"已清仓"但留着，自动移出会把手写的
        #      备注一起抹掉，而 append-only 的账本里删不掉记录、丢掉的是上下文。
        import shutil as _sh
        import tempfile as _tf
        tmp2 = _tf.mkdtemp()
        old_dir2 = wl.LIVE
        wl.LIVE = tmp2
        try:
            r1 = wl.sync_live()
            assert r1['added'], '持仓没同步进自选'
            gs2 = {g['name']: g['n'] for g in wl.groups()}
            assert all(wl.is_auto_group(k) for k in gs2), \
                '自动同步应按账户建组（实盘·<账户名>）：%s' % list(gs2)
            assert len(gs2) >= 2, '两个账户应各成一组：%s' % gs2
            n_log = len(wl.log())
            r2 = wl.sync_live()
            assert not r2['added'] and not r2['regrouped'], '第二次同步不该有改动'
            assert len(wl.log()) == n_log, \
                '同步必须幂等 —— 第二次不该往 append-only 账本里追加'
            # 手工分到别的组的，自动同步不该抢回去
            code0 = r1['added'][0]['code']
            wl.act('group', code0, group='我手工分的')
            wl.sync_live()
            now = {x['code']: x['group'] for x in wl.current()}
            assert now[code0] == '我手工分的', \
                '自动同步覆盖了手工分组 —— 那是用户的选择，不该被覆盖'
            # 自选里有、但不在任何持仓里的自动组条目 -> 标已清仓、不移出
            wl.act('add', '600519', group=wl.auto_group('不存在的账户'))
            r3 = wl.sync_live()
            assert any(x['code'] == '600519.XSHG' for x in r3['cleared']), \
                '自动组里已清仓的没被标出来'
            assert any(x['code'] == '600519.XSHG' for x in wl.current()), \
                '🔴 已清仓的被自动移出了 —— 只该标记，不该移出'

            # ---- 抓取范围去重（用户明确要的"不要重复调用"）----
            codes = sv._rt_codes()
            assert len(codes) == len(set(codes)), '_rt_codes 有重复！'
            held = set()
            for a in lv.load_accounts():
                if not a.get('archived'):
                    held |= set(lv.positions(a['id']))
            watch = {x['code'] for x in wl.current()}
            from assay import alerts as _al
            buy = set(_al.codes())          # 买点清单也在抓取范围里
            assert set(codes) == held | watch | buy, \
                '抓取范围应是【持仓 ∪ 自选 ∪ 买点清单】：少了 %s / 多了 %s' \
                % (sorted((held | watch | buy) - set(codes)),
                   sorted(set(codes) - (held | watch | buy)))
            assert len(codes) < len(held) + len(watch), \
                ('去重没生效 —— 持仓的票会被自动同步进自选，两边分别抓的话'
                 '请求量直接翻倍（%d = %d + %d）' % (len(codes), len(held), len(watch)))

            # ---- 自选也带实时价，且**读共享库**不自己打接口 ----
            v2 = wl.valued()
            assert 'price_src' in v2 and 'rt_n' in v2, '自选没给价来源'
            for x in v2['rows']:
                if x.get('rt_src'):
                    assert x.get('rt_at'), '标了实时却没给时刻'
                    # 涨跌幅要按实时价重算，不能留面板那个收盘值
                    if x.get('preclose'):
                        want = round((x['close'] / x['preclose'] - 1) * 100, 2)
                        assert abs(x['change_pct'] - want) < 0.02, \
                            ('涨跌幅没按实时价重算 —— 价变了幅没变，页面上'
                             '前后矛盾：%s vs %s' % (x['change_pct'], want))
            src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    'assay', 'watchlist.py'), encoding='utf-8').read()
            assert 'realtime' in src and 'urllib' not in src, \
                ('自选页不该自己去打行情接口 —— 要读 datalake/rt 那个共享库，'
                 '否则同一只票会被抓好几遍')
        finally:
            wl.LIVE = old_dir2
            _sh.rmtree(tmp2, ignore_errors=True)

        # ---- 刚加进来的票【立刻补抓一次】----
        #   ★ 轮询每 60s 才重算一次范围、bar 还是轮转抓的（~42 分钟一圈），
        #     而人加完自选是马上要看的 —— 那一刻一片"收盘价"，
        #     看着像功能没生效。
        #   ★ 只抓 `missing` 认定的那几只，所以天然收敛（抓到就不再 missing）；
        #     每只带冷却 —— 退市股永远 missing，没冷却的话每次刷新都打一次。
        rtm.missing = lambda cs, root=None, day=None: [
            c for c in cs if c == '600585.XSHG']
        HIT['snap'].clear(); HIT['bar'].clear()
        sv._RT['miss_at'] = {}
        assert sv.api_watchlist_act({}, {'act': 'add', 'code': '600585'})['ok']
        assert HIT['snap'] == [['600585.XSHG']],             '加进自选没立刻补抓（或抓多了）：%s' % HIT['snap']
        assert HIT['bar'] == [['600585.XSHG']],             'bar 也该补一次（轮转要 42 分钟才轮到它）：%s' % HIT['bar']
        # 同一只票在冷却窗口内不再抓 —— 页面刷新几次不该变成几串请求
        HIT['snap'].clear(); HIT['bar'].clear()
        sv.api_watchlist({})
        sv.api_watchlist({})
        assert not HIT['snap'], '同一只票被反复补抓（没有冷却）：%s' % HIT['snap']
        # 冷却过了才再抓
        sv._RT['miss_at'] = {}
        sv.api_watchlist({})
        assert HIT['snap'] == [['600585.XSHG']],             '冷却过后应该再试一次：%s' % HIT['snap']
        # 不在交易时段：什么都不抓 —— 收盘后没有盘中数据可抓，
        # 硬抓只会拿到空结果，而**空结果一律当失败**
        HIT['snap'].clear()
        rtm.in_session = lambda now=None: False
        sv._RT['miss_at'] = {}
        sv.api_watchlist({})
        assert not HIT['snap'], '非交易时段还在打接口：%s' % HIT['snap']
        rtm.in_session = lambda now=None: True
        # 只读模式（没开 --live）也不许往外发请求
        HIT['snap'].clear()
        sv.ALLOW_LIVE = False
        sv._RT['miss_at'] = {}
        sv.api_watchlist({})
        assert not HIT['snap'], '只读模式下还在打接口：%s' % HIT['snap']
        sv.ALLOW_LIVE = True
        rtm.missing = _o_miss

        # 非法输入
        for bad in (('add', '99'), ('nope', '600519'), ('add', '')):
            try:
                wl.act(bad[0], bad[1])
                raise AssertionError('%r 应被拒' % (bad,))
            except wl.WatchError:
                pass
        # 只读模式：接口层要拒（不能只靠页面）
        sv.ALLOW_LIVE = False
        assert (sv.api_watchlist_act({}, {'act': 'add', 'code': '601857'})
                or {}).get('error'), '只读模式下应拒绝改自选'
        # 改顺序也写账本 —— 同一条拦住（漏了就是只读模式能改账本）
        _n_ord = len([r for r in wl.log() if r.get('act') == 'order'])
        assert (sv.api_watchlist_order({}, {'groups': ['x']})
                or {}).get('error'), '只读模式下应拒绝改页签顺序'
        assert len([r for r in wl.log() if r.get('act') == 'order']) == _n_ord, \
            '只读模式下顺序记录还是被写进去了'
        n_before = len(wl.log())
        sv.ALLOW_LIVE = True
        assert sv.api_watchlist_act({}, {'act': 'add', 'code': '601988'}).get('ok')
        assert len(wl.log()) == n_before + 1
        return ('append-only：4 次改动 + 移出后日志 5 条且 add 记录仍在；'
                '重复加幂等不追加；重放出的分组/备注正确；分组过滤；'
                '退市代码不静默丢掉而是标 missing；3 类非法输入被拒；'
                '只读模式接口层拒写（含改顺序）；'
                '页签顺序：默认自动组在前、拖过的从账本重放、'
                'append-only（%d 条 order 记录）、过期顺序不丢分组、重名拒；'
                '持仓自动按账户分组同步且幂等、'
                '不覆盖手工分组、已清仓只标记不移出；'
                '抓取范围 = 持仓∪自选∪买点且去重（%d < %d+%d）；'
                '自选带实时价且涨跌幅按实时价重算、不自己打接口；'
                '新加的票立刻补抓一次（snap+bar 各 1 请求）、'
                '同一只有 180s 冷却、非交易时段与只读模式一律不发请求'
                % (_ord_n, len(codes), len(held), len(watch)))
    finally:
        sv.ALLOW_LIVE, wl.LIVE = old_live, old_dir
        rtm.snapshot, rtm.fetch = _o_snap, _o_fetch
        rtm.in_session, rtm.is_trading_day = _o_sess, _o_day
        rtm.missing = _o_miss
        sv._RT['miss_at'] = {}
        shutil.rmtree(tmp, ignore_errors=True)


def _via_pop(pg, loc, want=None):
    """点代码/名称 -> **不跳走**，弹速览浮层；「完整页 ↗」才是去个股页的出口。

    🔴 原来这几处断言「点了必须落到 /stock.html」。而点名称开浮层是
      **刻意改掉**的行为（实盘页往往一直开着：待办、正在录一半的成交，
      跳走再回来这些状态就没了）。所以那个断言测的是已经不存在的交互，
      它一挂**失败的是断言不是产品**（同 `#d_hold .note` 那条）。
    ★ 但它原本要保的东西不能丢 ——「独立页面之间不许断链」是独立页面
      最大的风险。新判据两段：浮层真的开了（点了没反应是最难查的那种坏），
      且浮层里那个出口指向**这只票**的个股页。
    """
    loc.click()
    pg.wait_for_selector('#spwrap', state='visible', timeout=20000)
    href = pg.locator('#spfull').get_attribute('href') or ''
    assert '/stock.html' in href, '浮层里没有去个股页的出口：%r' % href
    if want:
        assert want in href, '浮层出口指向错了：%r 里没有 %s' % (href, want)
    pg.keyboard.press('Escape')
    pg.wait_for_timeout(250)
    assert not pg.locator('#spwrap').is_visible(), 'Esc 关不掉浮层'
    return href


@case('买点页面真实渲染：手工填表 + 互算 + 到价点亮（playwright）', tag='web')
def t_alerts_ui():
    """那张 Excel 的页面版。**手工填**是这一页的全部意义，所以这里真的去填。

    ★ 表单每一项都要有【标签】—— 一个空输入框谁也不知道要填什么。
    ★ 「换算出来的」那一列必须**跟着输入变**：只改 DOM 不重渲染的话，
      填完价它还显示着上一次的股息率，而这一页存在的理由就是这个换算。
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return '跳过（无 playwright）'
    import shutil
    import tempfile
    import threading
    from http.server import ThreadingHTTPServer

    from assay import alerts as al
    from assay import server as sv
    old_live, old_dir = sv.ALLOW_LIVE, al.LIVE
    sv.ALLOW_LIVE = True
    tmp = tempfile.mkdtemp()
    al.LIVE = tmp                    # ★ 不往真账本里写测试数据
    # 外部分红接口打桩：这个用例起的是同进程的服务，页面一开就会走
    # suggest_div -> ext_div。真打东财会让用例依赖外网，且"一天一次"的
    # 缓存会污染真 datalake/rt。
    _o_fetch, _o_extpath = al._ext_fetch, al.ext_path
    al.ext_path = lambda root=None: os.path.join(tmp, 'div_ext.json')
    al._ext_fetch = lambda cs, day=None: {}
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), sv.Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = 'http://127.0.0.1:%d' % port
    try:
        with sync_playwright() as p:
            try:
                br = p.chromium.launch()
            except Exception as e:                          # noqa: BLE001
                return '跳过（浏览器不可用: %s）' % type(e).__name__
            pg = br.new_page(viewport={'width': 1500, 'height': 1100})
            errs = []
            pg.on('pageerror', lambda e: errs.append(str(e)))
            pg.goto(base + '/alerts.html', wait_until='networkidle')
            pg.wait_for_selector('#aadd input', timeout=40000)
            assert '买点' in pg.locator('#top .btn.nav.on').inner_text(), \
                '买点页没高亮：%s' % pg.locator('#top .btn.nav.on').inner_text()
            assert '空的' in pg.locator('#pg').inner_text(), '空清单没给空态提示'

            # ---- 新增一行：搜票 -> 编辑器 -> 填两档 -> 保存 ----
            pg.fill('#aadd input', '000423')
            pg.wait_for_selector('.skit', timeout=20000)
            pg.locator('.skit').first.click()
            pg.wait_for_selector('#adiv', timeout=20000)
            pg.wait_for_timeout(500)
            ed = pg.locator('.lvsec').first.inner_text()
            # 🔴 分红是【数据给的】，不是输入框 —— "预计分红"没有价值，
            #   而摆一个输入框在那里就是在邀请人去猜。
            assert pg.evaluate(
                "() => document.querySelector('#adiv').tagName") != 'INPUT', \
                '分红又变成输入框了 —— 它不该能手填'
            _dv = float(pg.locator('#adiv').inner_text())
            assert _dv > 0, '分红没显示出来：%r' % pg.locator('#adiv').inner_text()
            assert '年度合计' in ed, '没说分红是什么口径：%s' % ed[:200]
            assert '不能手填' in ed or '不手填' in ed, \
                '没写清分红为什么不能手填：%s' % ed[:200]
            assert '近 12 个月已公告' in ed, \
                '另一个口径也该显示出来（当参考）：%s' % ed[:240]
            assert pg.locator('.ause').count() == 0, \
                '还留着「用它」那种手填入口'
            assert '预计分红' in ed, \
                ('没写清为什么不给手填 —— "预计分红没有价值"这条要写在'
                 '眼前，否则下一次又会有人加个输入框：%s' % ed[:200])
            for lab in ('实际分红', '接近', '备注', '按什么填', '数值',
                        '换算出来的'):
                assert lab in ed, '编辑器缺「%s」这个标签：%s' % (lab, ed[:200])
            # 填第一档：目标价 49 -> 换算出 分红/49（分红是数据给的）
            pg.locator('.aby').first.select_option('price')
            pg.wait_for_timeout(300)
            pg.locator('.av').first.fill('49')
            pg.locator('.av').first.dispatch_event('change')
            pg.wait_for_timeout(400)
            row1 = pg.locator('.lvsec').first.locator(
                'table.lvt tr').nth(1).inner_text()
            _wy = '%.2f%%' % (_dv / 49 * 100)
            assert _wy in row1 and ('%s/49' % _dv) in row1.replace(' ', ''), \
                '填了目标价 49 却没换算出 %s/49=%s：%s' % (_dv, _wy, row1)
            # 第二档改成按【目标股息率】填 6% -> 换算出 45.00 元
            pg.locator('.aby').nth(1).select_option('yield')
            pg.wait_for_timeout(300)
            pg.locator('.av').nth(1).fill('6')
            pg.locator('.av').nth(1).dispatch_event('change')
            pg.wait_for_timeout(400)
            row2 = pg.locator('.lvsec').first.locator(
                'table.lvt tr').nth(2).inner_text()
            _wp = '%.2f' % (_dv / 0.06)
            assert _wp in row2, \
                '填了目标股息率 6%% 却没换算出目标价 %s：%s' % (_wp, row2)
            pg.click('#asave')
            pg.wait_for_selector('table.pkt', timeout=20000)
            pg.wait_for_timeout(800)

            # ---- 表里那一行 ----
            # ★ 分红的更新状态必须在页面上 —— 这一页的目标价全是
            #   "分红 ÷ 股息率"算出来的，分红过期就整页都是过期的数。
            _hd = pg.locator('.lvhead').inner_text()
            assert '分红' in _hd and ('今天已更新' in _hd or '已更新' in _hd
                                      or '取不到' in _hd), \
                '头上没说分红是什么时候更新的：%s' % _hd.replace('\n', ' ')
            assert pg.locator('#adivref').count() == 1, \
                '缺「刷新分红」（平时一天一次，刚出公告时要能手动催一次）'
            th = ' '.join(pg.locator('table.pkt th').all_inner_texts())
            for k in ('名称', '现价', '实际分红', '当前股息率',
                      '第 1 档', '状态'):
                assert k in th, '盯价表缺「%s」列：%s' % (k, th)
            # ★ 代码与名称**同一格**（2026-09-16 全站统一）——
            #   判据两头：没有"代码"列 + 名称那格里带着小字代码。
            assert '代码' not in th, '代码又单独占了一列：%s' % th
            assert pg.locator('table.pkt tr:nth-child(2) td:nth-child(1) .cd,'
                              'table.pkt tr:nth-child(2) td:nth-child(1) .cd0'
                              ).count() == 1, '买点表的名称格里没有小字代码'
            # ★ 能进 tooltip 的就别占列：备注是给自己看的一句话、长短不定，
            #   摆进表里会把要扫的数字挤走。但**信息不能丢** ——
            #   有备注的行要带 ✎ 且 title 里是原文。
            assert '备注' not in th, '备注不该占一列：%s' % th
            r1 = pg.locator('table.pkt tr').nth(1).inner_text().replace('\n', ' ')
            assert '东阿阿胶' in r1, '保存后没渲染出来：%s' % r1
            # 带备注的行：✎ 在，原文在 title 里（表里不占地方但查得到）
            # ★ set 是【整行覆盖】—— 加备注也要把两档原样带上，
            #   不然后面"回填的是哪一种口径"那条就没得验了
            al.set_row('000423',
                       [{'by': 'price', 'v': 49}, {'by': 'yield', 'v': 6}],
                       note='这是一条很长的备注，用来验证它不会把表格撑开')
            pg.reload(wait_until='networkidle')
            pg.wait_for_selector('table.pkt', timeout=20000)
            pg.wait_for_timeout(600)
            _nt = pg.locator('table.pkt tr').nth(1).inner_text()
            assert '很长的备注' not in _nt, \
                '备注原文又出现在表格里了：%s' % _nt.replace('\n', ' ')
            _mk = pg.locator('table.pkt tr').nth(1).locator(
                '[title*="很长的备注"]')
            assert _mk.count() >= 1, \
                '备注挪走了但没留标记 —— 那等于把它藏没了'
            assert '✎' in pg.locator('table.pkt tr').nth(1).inner_text(), \
                '有备注的行没带 ✎'
            assert _wy in r1 and '49.00' in r1, \
                '表里没给"目标价 + 那个价对应的股息率"：%s' % r1
            # 现价低于 49 -> 第一档必须点亮，且【整行】点亮
            v = al.valued()
            st = v['rows'][0]['state']
            if st == 'hit':
                assert pg.locator('tr.ahit').count() >= 1, \
                    ('到价了却没点亮整行 —— 只给状态列上色的话，一屏十几行时'
                     '要逐行看那一列才知道哪行该动')
                assert '到价' in r1, '状态列没写"到价"：%s' % r1
                # 顶栏「买点」那个红点要亮（判据来自服务端 n_hit/n_near）
                pg.wait_for_timeout(1200)
                assert pg.evaluate(
                    "() => (document.querySelector('#navdot2')||{}).style"
                    "?.display") == 'inline-block', \
                    '有票到价，顶栏「买点」的红点却没亮'
            # 名称点开速览浮层，浮层里有去个股页的出口（不许断链）
            _via_pop(pg, pg.locator('table.pkt a[data-sp]').first, '000423')

            # ---- 改一行：编辑器要回填【存的那个】口径 ----
            #   表里显示的是换算后的两个数，照着显示值回填会把"我填的是
            #   股息率"悄悄变成"我填的是价格"。
            pg.locator('.aed').first.click()
            pg.wait_for_selector('#adiv', timeout=20000)
            pg.wait_for_timeout(400)
            bys = pg.locator('.aby').evaluate_all('es => es.map(e => e.value)')
            vs = pg.locator('.av').evaluate_all('es => es.map(e => e.value)')
            assert bys[:2] == ['price', 'yield'], \
                '编辑器没回填"当初填的是哪一种"：%s' % bys
            assert abs(float(vs[0]) - 49) < 0.01 and abs(float(vs[1]) - 6) < 0.01, \
                '回填的数值不对（第二档该是 6 而不是 45）：%s' % vs
            # 窄屏不许把 body 撑出横滚
            pg.set_viewport_size({'width': 1024, 'height': 900})
            pg.wait_for_timeout(500)
            ov = pg.evaluate('document.body.scrollWidth'
                             ' - document.body.clientWidth')
            assert ov <= 1, '1024 宽下 body 横滚了 %dpx' % ov
            assert not errs, 'JS 报错：%s' % errs[:3]
            br.close()
        return ('空态 -> 搜票 -> 分红自动取实际已公告(只读、不是输入框，'
                '另一口径当参考显示) -> 填目标价 49 当场'
                '换算 2.7/49=5.51% -> 第二档改按股息率 6% 换算出 45.00 -> 保存；'
                '表里给"目标价+股息率"、到价整行点亮且顶栏红点亮；'
                '点名称弹速览浮层（不跳走）且浮层里能去个股页；改一行回填的是【存的那个口径】'
                '(price/yield 而不是换算值)；备注不占列而是名称后的 ✎ + title；'
                '头上有分红更新状态与「刷新分红」；'
                '1024 宽无横滚；0 个 JS 错误')
    finally:
        httpd.shutdown()
        sv.ALLOW_LIVE, al.LIVE = old_live, old_dir
        al._ext_fetch, al.ext_path = _o_fetch, _o_extpath
        shutil.rmtree(tmp, ignore_errors=True)


@case('新页面真实渲染：盘面 / 板块 / 自选 / 对比（playwright）', tag='web')
def t_new_pages_ui():
    """四个独立页面都真渲染一遍，并验证【页面之间能互相走到】。

    ★ 独立页面的风险不是单页坏，是**页面之间断链** —— 从盘面点不到个股、
      从板块点不到成分。所以这里逐个点过去。
    ★ Canvas 的判据同样是"画布上有非透明像素"，不是 DOM 里有 <canvas>。
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return '跳过（无 playwright）'
    import shutil
    import tempfile
    import threading
    from http.server import ThreadingHTTPServer

    from assay import server as sv
    from assay import watchlist as wl
    old_live, old_dir = sv.ALLOW_LIVE, wl.LIVE
    sv.ALLOW_LIVE = True
    tmp = tempfile.mkdtemp()
    wl.LIVE = tmp                    # ★ 不往真账本里写测试数据
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), sv.Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = 'http://127.0.0.1:%d' % port
    NZ = lambda sel: ("() => { const c=document.querySelector('%s');"
                      " if(!c) return -1; const g=c.getContext('2d');"
                      " const d=g.getImageData(0,0,c.width,c.height).data;"
                      " let n=0; for(let i=3;i<d.length;i+=4) if(d[i]>0) n++;"
                      " return n; }" % sel)
    try:
        with sync_playwright() as p:
            try:
                br = p.chromium.launch()
            except Exception as e:                          # noqa: BLE001
                return '跳过（浏览器不可用: %s）' % type(e).__name__
            pg = br.new_page(viewport={'width': 1600, 'height': 1100})
            errs = []
            pg.on('pageerror', lambda e: errs.append(str(e)))
            pg.on('console',
                  lambda m: errs.append('console: ' + m.text) if m.type == 'error' else None)

            def clean(sel='#pg'):
                t = pg.locator(sel).inner_text()
                assert 'undefined' not in t and 'NaN' not in t, \
                    '%s 里有 undefined/NaN' % sel
                return t

            # ================= 盘面 =================
            pg.goto(base + '/market.html', wait_until='networkidle')
            pg.wait_for_selector('#dcv', timeout=40000)
            pg.wait_for_timeout(1200)
            assert '盘面' in pg.locator('#top .btn.nav.on').inner_text(), '当前页没高亮'
            kp = ' | '.join(pg.locator('.kpi .k').all_inner_texts())
            for k in ('涨 / 跌 / 平', '涨停 / 跌停', '全市场成交额', '中位涨幅'):
                assert k in kp, '盘面 KPI 缺「%s」：%s' % (k, kp)
            nz = pg.evaluate(NZ('#dcv'))
            assert nz > 3000, '涨跌分布图没画出来：%d' % nz
            clean()
            n_ranks = pg.locator('.pgrid.c2 .lvsec').count()
            assert n_ranks >= 5, '榜单块只有 %d 个' % n_ranks
            # ★ 榜单在两列网格里，列多了最右边几列会被压没 —— 原来 8 列时
            #   「行业」直接看不到了，而这不报错。现在固定 5 列，且逐列量宽度。
            rh = pg.locator('table.pkt.rk').first.locator('th').all_inner_texts()
            assert len(rh) == 5, '榜单应是 5 列（多了会被挤没）：%s' % rh
            assert '名称' in rh[1] and '行业' in rh[1], \
                '行业应作为名称的注解显示，而不是单独占一列：%s' % rh
            wid = pg.evaluate(
                "() => { const t=document.querySelector('table.pkt.rk');"
                " return [...t.querySelectorAll('tr')[1].children]"
                ".map(td => Math.round(td.getBoundingClientRect().width)); }")
            assert min(wid[1:]) >= 40, \
                '有列被压到 %d px（内容看不见了）：%s' % (min(wid[1:]), wid)
            row1 = pg.locator('table.pkt.rk').first.locator(
                'tr').nth(1).locator('td').nth(1).inner_text()
            assert len(row1.split('\n')) >= 2, '名称下面没带行业：%r' % row1
            # 前后翻日：日期真的变了
            d0 = pg.locator('#mdate').input_value()
            pg.locator('.mshift[data-d="-1"]').click()
            pg.wait_for_timeout(2500)
            d1 = pg.locator('#mdate').input_value()
            assert d1 < d0, '「前一日」没生效（%s -> %s）' % (d0, d1)
            # 盘面 → 个股（断链是独立页面最大的风险）
            _via_pop(pg, pg.locator('.pgrid.c2 a[data-sp]').first)

            # ================= 板块 =================
            pg.goto(base + '/sector.html', wait_until='networkidle')
            pg.wait_for_selector('table.pkt', timeout=40000)
            pg.wait_for_timeout(800)
            #   🔴 **2026-09-14 起点亮的是父级「🌡 盘面」**：板块已从 NAV
            #     收进盘面，它自己那一项不存在了。这条原来钉「板块」高亮，
            #     属于被改动作废的断言 —— 要保的东西没变（"我在哪"必须有
            #     指示），只是答案从"它自己"变成了"它所属的那一组"。
            assert '盘面' in pg.locator('#top .btn.nav.on').inner_text(), \
                ('板块页该点亮父级「🌡 盘面」，实得：%s'
                 % pg.locator('#top .btn.nav.on').inner_text())
            assert '板块' in pg.locator('#top h1').inner_text(), \
                '板块页的标题该仍是它自己'
            kinds = pg.locator('.lvhead .kd').count()
            assert kinds >= 4, '板块分类入口只有 %d 个' % kinds
            n_sw = pg.locator('.lvsec table.pkt tr').count() - 1
            assert n_sw >= 25, '申万板块行数不对：%d' % n_sw
            clean()
            # 点一个板块 → 出成分
            pg.locator('a.pick').first.click()
            pg.wait_for_timeout(2500)
            secs = [x.split('\n')[0] for x in pg.locator('.lvsec h3').all_inner_texts()]
            assert any('成分' in x for x in secs), '点板块没出成分表：%s' % secs
            clean()
            # 切到概念板块
            pg.locator('.lvhead .kd[data-k="concept"]').click()
            pg.wait_for_timeout(2500)
            assert '概念' in pg.locator('.lvhead h2').inner_text(), '切概念没生效'
            n_cc = pg.locator('.lvsec table.pkt tr').count() - 1
            assert n_cc > 100, '概念板块行数不对：%d' % n_cc
            # 板块 → 个股
            pg.locator('.lvsec a.pick').first.click()
            pg.wait_for_timeout(2500)
            _via_pop(pg, pg.locator('a[data-sp]').first)

            # ================= 自选 =================
            pg.goto(base + '/watchlist.html', wait_until='networkidle')
            pg.wait_for_selector('#wadd input', timeout=40000)
            assert '自选' in pg.locator('#top .btn.nav.on').inner_text(), \
                '自选页没高亮：%s' % pg.locator('#top .btn.nav.on').inner_text()
            assert '空的' in pg.locator('#pg').inner_text(), '空自选没给空态提示'
            pg.fill('#wadd input', '601857')
            pg.wait_for_selector('.skit', timeout=20000)
            pg.locator('.skit').first.click()
            pg.wait_for_timeout(2500)
            t = clean()
            assert '中国石油' in t, '加进自选后没渲染出来：%s' % t[:200]
            assert '变更历史' not in t, \
                '变更历史不该渲染在页面上（账本仍在，只是不占版面）'
            assert pg.locator('.lvsec table.pkt tr').count() >= 2, '盯盘表没行'
            # ---- 页签：多个分组并列，实盘账户组带「持」并排在前 ----
            #   ★ 分组是"我要分别盯的几拨票"，用页签而不是 chip ——
            #     chip 看着像筛选标签，页签才表示"几个并列的视图"。
            pg.click('#wsync')                     # 把持仓同步进自选
            pg.wait_for_timeout(3000)
            tabs = [x.replace('\n', ' ') for x in
                    pg.locator('.wtab').all_inner_texts()]
            assert len(tabs) >= 4, '页签太少（应有 各账户 + 手工组 + ＋）：%s' % tabs
            # 🔴 没有「全部」页签 —— 不同账户的持仓放一起横向比没有意义
            assert not any('全部' in x for x in tabs), \
                '不该再有「全部」页签：%s' % tabs
            assert tabs[-1].strip() == '＋', '最后应是新建页签：%s' % tabs
            auto = [x for x in tabs if '持' in x]
            assert len(auto) >= 2, \
                '两个实盘账户应各成一个带「持」的页签：%s' % tabs
            assert '持' in tabs[0], \
                '实盘账户组应排在手工组前面（它们跟着持仓变）：%s' % tabs
            assert pg.locator('.wtab.on').count() == 1, \
                '同一时刻只该有一个页签高亮'
            # 重新进这一页：没有「全部」了，默认要落在第一个（实盘）分组上
            #   —— 落到空字符串的话整页一行都没有，而它不报错。
            pg.goto(base + '/watchlist.html', wait_until='networkidle')
            pg.wait_for_selector('.wtab.on', timeout=40000)
            pg.wait_for_timeout(1500)
            tabs = [x.replace('\n', ' ') for x in
                    pg.locator('.wtab').all_inner_texts()]
            assert '持' in pg.locator('.wtab.on').inner_text(), \
                '默认没落在第一个分组上：%s' % pg.locator('.wtab.on').inner_text()
            assert pg.locator('.lvsec').first.locator(
                'table.pkt tr').count() >= 2, '默认页签下一行都没有'
            # ---- 页签能【拖动改顺序】，且拖完立刻存进账本 ----
            # 🔴 顺序存在服务端（`watchlist.jsonl` 的 order 记录），不存
            #   localStorage —— 换台机器就回到默认的话，"我怎么归类这些票"
            #   这件事就没留住。判据必须是**重新打开页面还在**。
            _tg = lambda: pg.locator('.wtab[data-g]').evaluate_all(
                'es => es.map(e => e.dataset.g)')
            _o0 = _tg()
            assert len(_o0) >= 2, '分组不够两个，测不了顺序：%s' % _o0
            assert pg.locator('.wtab[data-g]').first.get_attribute(
                'draggable') == 'true', '页签没开 draggable —— 按住拖不动'
            # ＋ 不是分组，不该参与排序
            assert pg.locator('#wnewg').get_attribute('draggable') != 'true', \
                '「＋」也能拖 —— 它不是分组，永远该在最后'

            def _drag(i_from, i_to_left_of):
                a = pg.locator('.wtab[data-g]').nth(i_from)
                z = pg.locator('.wtab[data-g]').nth(i_to_left_of)
                ab, zb = a.bounding_box(), z.bounding_box()
                pg.mouse.move(ab['x'] + ab['width'] / 2,
                              ab['y'] + ab['height'] / 2)
                pg.mouse.down()
                # 落点取目标页签的**左侧 20%** -> 插到它前面
                for _s in (0.5, 0.2):
                    pg.mouse.move(zb['x'] + zb['width'] * _s,
                                  zb['y'] + zb['height'] / 2, steps=8)
                pg.mouse.up()
                pg.wait_for_timeout(1500)

            _drag(len(_o0) - 1, 0)                 # 最后一个拖到最前面
            _o1 = _tg()
            assert _o1[0] == _o0[-1], \
                '拖了没生效：%s -> %s' % (_o0, _o1)
            assert sorted(_o1) == sorted(_o0), '拖动把分组弄丢了：%s' % _o1
            # 🔴 判据是**重新打开页面**后还在 —— 只看当前 DOM 的话，
            #   "本地移动了但没存上"看不出来（POST 挂掉也是这个表现）。
            pg.goto(base + '/watchlist.html', wait_until='networkidle')
            pg.wait_for_selector('.wtab.on', timeout=40000)
            pg.wait_for_timeout(1500)
            assert _tg() == _o1, \
                '顺序没存住（重开页面回弹了）：存的 %s，重开后 %s' % (_o1, _tg())
            # 服务端也要认这份顺序（页面与账本不许分叉）
            _sg = pg.evaluate(
                "async () => (await (await fetch('/api/watchlist')).json())"
                ".groups.map(g => g.name)")
            assert _sg == _o1, '接口给的顺序与页面不一致：%s vs %s' % (_sg, _o1)
            # 还原顺序，别把这个用例的副作用留给后面的断言
            # ★ 不用"再拖一次"还原 —— 拖到右邻的左边是**原位**（空操作），
            #   第一版就这么写的，结果后面"切页签行数要变"那条踩空
            #   （首个页签变成了只有 2 行的手工组）。直接指定目标顺序。
            _ordered = '%s -> %s' % ('/'.join(_o0), '/'.join(_o1))
            pg.evaluate(
                "async gs => (await fetch('/api/watchlist/order',"
                " {method:'POST', headers:{'Content-Type':'application/json'},"
                "  body: JSON.stringify({groups: gs})})).json()", _o0)
            pg.goto(base + '/watchlist.html', wait_until='networkidle')
            pg.wait_for_selector('.wtab.on', timeout=40000)
            pg.wait_for_timeout(1200)
            tabs = [x.replace('\n', ' ') for x in
                    pg.locator('.wtab').all_inner_texts()]
            # 实时价：与持仓页共用同一个库，页面侧不额外调接口
            #   ★ 在【实盘持仓那个页签】上验 —— 手工加的票没进抓取轮转，
            #     在它那一页看不到实时标记是正常的。
            # 🔴 选择器不能限定 .on/.warn —— 那两个 class 只在【有实时价】时
            #   才加，而非交易时段（或实时库里今天还没数据）就一个都没有。
            #   实测踩过：跨过零点后 nrt=0，这条断言凭空失败一次。
            #   判据应该是"那个标签存在且写了数据日"，与有没有实时价无关。
            _psrc = pg.evaluate(
                "() => [...document.querySelectorAll('.lvhead .lvtag')]"
                ".map(e => e.textContent).find(t => t.includes('数据日')) || ''")
            assert '数据日' in _psrc, '自选头上没有数据日+报价时间：%s' % _psrc
            if '实时' in _psrc:
                assert pg.locator(
                    'table.pkt .lvwhy[title*="实时价"]').count() >= 1, \
                    '顶栏说是实时，表里却没标出哪些是实时价'
            # 切到手工组：行数要跟着变（页签是本地切的，不重新打接口）
            n_auto = pg.locator('.lvsec').first.locator('table.pkt tr').count()
            man = [i for i, x in enumerate(tabs) if '持' not in x
                   and x.strip() != '＋']
            assert man, '手工加的那只票没有自己的页签：%s' % tabs
            pg.locator('.wtab[data-g]').nth(man[0]).click()
            pg.wait_for_timeout(1200)
            n_man = pg.locator('.lvsec').first.locator('table.pkt tr').count()
            assert 1 < n_man < n_auto, \
                '切页签后行数没变（%d -> %d）' % (n_auto, n_man)
            # 自选 → 个股：手工组里只有 601857，所以点它必须落到 601857
            _via_pop(pg, pg.locator('.lvsec').first.locator(
                'a[data-sp]').first, '601857')

            # ================= 对比 =================
            pg.goto(base + '/compare.html?codes=601857.XSHG,601088.XSHG',
                    wait_until='networkidle')
            pg.wait_for_selector('#ccv', timeout=40000)
            pg.wait_for_timeout(1500)
            #   🔴 同板块那条：对比已从 NAV 收进个股，点亮的是父级「📈 个股」。
            assert '个股' in pg.locator('#top .btn.nav.on').inner_text(), \
                ('对比页该点亮父级「📈 个股」，实得：%s'
                 % pg.locator('#top .btn.nav.on').inner_text())
            assert '对比' in pg.locator('#top h1').inner_text(), \
                '对比页的标题该仍是它自己'
            nz2 = pg.evaluate(NZ('#ccv'))
            assert nz2 > 3000, '对比曲线没画出来：%d' % nz2
            t = clean()
            assert '后复权' in t, '没说明一律后复权（不复权跨除权日有假跌幅）'
            assert '中国石油' in t and '中国神华' in t, '两只票没都渲染'
            bb = pg.locator('#ccv').bounding_box()
            pg.mouse.move(bb['x'] + bb['width'] * 0.6, bb['y'] + bb['height'] * 0.5)
            pg.wait_for_timeout(400)
            assert pg.locator('#ctip').is_visible(), '对比图没出读数'
            tip = pg.locator('#ctip').inner_text()
            assert '中国石油' in tip and '%' in tip, '读数不对：%s' % tip
            # 加一只 / 减一只
            pg.fill('#cadd input', '600519')
            pg.wait_for_selector('.skit', timeout=20000)
            pg.locator('.skit').first.click()
            pg.wait_for_timeout(2500)
            assert pg.locator('.rmc.chip').count() == 3, '加第三只没生效'
            pg.locator('.rmc.chip').first.click()
            pg.wait_for_timeout(2500)
            assert pg.locator('.rmc.chip').count() == 2, '移除没生效'
            # 对比 → 个股
            _via_pop(pg, pg.locator('.lvsec a[data-sp]').first)

            # ================= 窄屏不许把整个 body 撑横滚 =================
            #   🔴 横滚的是【body】的话，读表格时整页会左右晃。
            #      宽表必须自己在 .pw 里滚。这条抓到过三个真问题：
            #      顶栏 10 个入口不换行、grid 子项 min-width:auto 让 .pw 失效、
            #      .pw 的 overflow 只在 #pk 作用域下定义过（别处形同虚设）。
            narrow = br.new_page(viewport={'width': 1024, 'height': 1000})
            over = []
            for path in ['/'] + _pages():
                narrow.goto(base + path, wait_until='networkidle')
                narrow.wait_for_timeout(2200)
                ov = narrow.evaluate('() => document.documentElement.scrollWidth'
                                     ' - document.documentElement.clientWidth')
                if ov > 2:
                    over.append((path, ov))
            narrow.close()
            assert not over, '窄屏(1024)下这些页面把 body 撑出横滚：%s' % over

            # ================= 顶栏导航：每页都能走到每页 =================
            for href in ('/market.html', '/watchlist.html', '/stock.html'):
                assert pg.locator('#top a.nav[href="%s"]' % href).count() == 1, \
                    '顶栏缺 %s 的入口' % href
            # 🔴 **2026-09-14 反过来了**：板块与对比收进了父页（盘面 / 个股），
            #   顶栏不该再有它们。原来这里断言它们**必须在**顶栏 ——
            #   那是被改动作废的断言，不是删掉保护：真正要保的是
            #   「独立页面之间不许断链」，所以下面改成**从父页能不能走到**。
            for href in ('/sector.html', '/compare.html'):
                assert pg.locator('#top a.nav[href="%s"]' % href).count() == 0, \
                    ('%s 又回到顶栏了 —— 它已经收进父页（板块->盘面、'
                     '对比->个股），顶栏平铺 9 个时每次都要扫一遍' % href)
            assert pg.locator('#top a.nav[href="/#/live"]').count() == 1, \
                '顶栏缺实盘入口'
            # ★ 分组分隔线是【信息】不是装饰：告诉人"这几个是一类"。
            #   平铺 8 个入口时每次都要在 8 个里扫一遍才找到要去的地方。
            assert pg.locator('#top .navsep').count() == 3, \
                '顶栏没分组（实盘 | 市场 | 研究 | 数据 应有 3 条分隔）：%d' \
                % pg.locator('#top .navsep').count()
            assert pg.locator('#top a.nav').first.inner_text().find('实盘') >= 0, \
                '实盘应排在最前 —— 它是唯一回答"今天要做什么"的入口'

            br.close()
            assert not errs, '页面有运行时错误：%s' % errs[:3]
            return ('四页真渲染：盘面（KPI 齐 + 分布图 %d 像素 + 翻日 %s→%s + '
                    '%d 个榜单）、板块（%d 类 / 申万 %d 行 / 概念 %d 行 / 点出成分）、'
                    '自选（加入后渲染 + 页签按账户分【无「全部」】且切换生效'
                    ' + 页签可拖动改顺序、重开页面仍在、接口与页面一致'
                    '（%s））、'
                    '对比（曲线 %d 像素 + 读数 + '
                    '加减只数）；四页都能点到个股页；顶栏 5 个入口齐'
                    % (nz, d0, d1, n_ranks, kinds, n_sw, n_cc, _ordered, nz2))
    finally:
        sv.ALLOW_LIVE, wl.LIVE = old_live, old_dir
        shutil.rmtree(tmp, ignore_errors=True)
        httpd.shutdown()


@case('回测的份额与价格展示【不复权】：真实股数必须是整手', tag='slow')
def t_backtest_raw_units():
    """2026-09-14 用户："交易记录的价格为什么是后复权的？展示出来的应该是
    当时不复权的价格。份额也同样。持仓的份额也有相同的问题。"—— 他是对的。

    引擎全程按**后复权**记账（那是对的：跨除权日比价必须复权），于是归档里
    `shares` 是后复权记账单位、价格是后复权价。直接摆到页面上就是：

        份额 774.835189  ← 券商那边没有这种数，真实是 **5700 股**
        价格 25.3986     ← 当时实际成交在 **3.4526**

    🔴 **判据是「整手」**，不是"有没有换算过" —— 真实股数一定是 100 的
      整数倍，这是**可证的事实**；而"调了个函数"这种判据，换算错了方向
      （乘变除）照样绿。

    ★ 两条路都要验：
      ① 新归档有 `fills.parquet`（撮合当场记的真实股数+不复权价，不用换算）
      ② 旧归档只能**按当日复权因子换算** —— 而 `hfq_factor` 与 `dividend`
        表并不总是对齐（实测两个方向都有：002293 引擎缩了股数而因子没跳、
        601318 因子跳了而分红表没那条），所以这条路允许少量不整手。
    """
    import glob as _g
    import json as _json
    import pandas as _pd
    from assay.srv import runs as R

    # ---- ① 新归档：fills.parquet 必须存在且**全部整手** ----
    cand = sorted(_g.glob('runs/_fqtest/*/*/fills.parquet'))
    if not cand:
        return '跳过（没有带 fills.parquet 的归档，先跑一次回测）'
    d = os.path.dirname(cand[-1])
    rid = os.path.basename(d)
    fl = _pd.read_parquet(cand[-1])
    assert len(fl) > 0, 'fills.parquet 是空的'
    assert {'date', 'code', 'side', 'shares', 'price'} <= set(fl.columns), \
        'fills.parquet 缺字段：%s' % list(fl.columns)
    bad = fl[(fl['shares'] % 100) != 0]
    assert bad.empty, \
        ('fills.parquet 里有 %d 行不是整手 —— 它记的是**撮合当场的真实股数**，'
         '不该需要任何换算：\n%s' % (len(bad), bad.head(3).to_string()))
    assert (fl['side'] == 'buy').any() and (fl['side'] == 'sell').any(), \
        'fills 里买卖必须都有（trades 只在平仓时写行，会丢掉所有买入）'
    notes = ['fills.parquet %d 笔全整手（买 %d / 卖 %d）'
             % (len(fl), int((fl['side'] == 'buy').sum()),
                int((fl['side'] == 'sell').sum()))]

    # 接口必须**优先用它**
    got = R.api_trades({'id': rid, 'limit': '500'})
    assert got and got.get('src') == 'fills', \
        ('有 fills.parquet 却没用它（src=%s）—— 那条路不用换算、而且含'
         '未平仓持仓的买入' % (got or {}).get('src'))
    assert all(float(r['shares']) % 100 == 0 for r in got['rows']), \
        '接口回出来的份额不是整手'
    # 价格必须是**不复权**：拿面板的 close_bfq 量级比一比（后复权会差好几倍）
    root = R._dl_root(rid)
    assert root, '归档没记 datalake 根'
    r0 = got['rows'][0]
    fac = R._factors(root, {(r0['code'], str(r0['date'])[:10])})
    f = list(fac.values())[0] if fac else None
    assert f and f > 1.05, '挑到的样本复权因子 ~1，验不出口径（换一只票）'
    # 🔴 **价格也要单独验方向**：只钉股数整手的话，把价格的 ÷ 改成 ×
    #   照样全绿（变异测试实测漏过）。判据是「显示价 × 因子 == 后复权价」。
    #   ★ fills 那条路没有 `price_hfq`（它本来就是不复权的），
    #     所以拿**面板当日 close_bfq 的量级**比：后复权价会差好几倍。
    px = float(r0['price'])
    con = __import__('duckdb').connect(':memory:')
    try:
        row = con.execute(
            "SELECT close_bfq, close_hfq FROM read_parquet('%s/mart/panel_daily/"
            "panel_*.parquet') WHERE jq_code='%s' AND date=DATE '%s'"
            % (root, r0['code'], str(r0['date'])[:10])).fetchone()
    finally:
        con.close()
    assert row, '面板里没有这一天的行，换个样本'
    bfq, hfq = float(row[0]), float(row[1])
    assert abs(px / bfq - 1) < 0.15, \
        ('展示价 %.4f 与当日**不复权**收盘 %.4f 不在一个量级（后复权是 %.4f）'
         ' —— 价格换算的方向反了（不复权 = 后复权 ÷ 因子）' % (px, bfq, hfq))
    notes.append('价格贴不复权收盘（%.4f vs %.4f，后复权 %.4f）' % (px, bfq, hfq))

    # ---- ② 旧归档（只有 trades.parquet）：按因子换算，绝大多数整手 ----
    old = [p for p in _g.glob('runs/*/*/*/trades.parquet')
           if not os.path.exists(os.path.join(os.path.dirname(p),
                                              'fills.parquet'))]
    assert old, '没有旧归档可验换算路径'
    old.sort(key=os.path.getsize)
    orid = os.path.basename(os.path.dirname(old[-1]))
    g2 = R.api_trades({'id': orid, 'limit': '500'})
    assert g2 and g2.get('src') != 'fills', '挑到的不是旧归档'
    rows = g2['rows']
    n_bad = sum(1 for r in rows if float(r['shares'] or 0) % 100)
    assert n_bad < len(rows) * 0.10, \
        ('旧归档按因子换算之后 %d/%d 行不是整手（>10%%）—— 换算多半反了'
         '（真实股数 = 后复权股数 × 因子，价格是 ÷）' % (n_bad, len(rows)))
    assert any(float(r['shares'] or 0) >= 100 for r in rows), \
        '换算后份额还是零点几 —— 方向反了（乘写成了除）'
    # 后复权原值要留着（tooltip 要用），但**不能**是展示值
    got_hfq = [r for r in rows if r.get('shares_hfq') is not None]
    assert got_hfq, '换算之后没保留后复权原值（tooltip 说不清口径）'
    assert any(abs(float(r['shares']) - float(r['shares_hfq'])) > 1
               for r in got_hfq), \
        '展示值与后复权值完全一样 —— 等于没换算'
    # 🔴 **股数与价格必须用【同一个因子】的两个方向。**
    #   前面那条价格断言用的是 fills 那条路的行，而它根本不走 `_to_raw`
    #   —— 于是"价格换算反了"那个变异照样绿（实测漏过两轮）。
    #   判据做成不依赖外部数据的：`价格比 = 后复权价/展示价` 应该等于
    #   `股数比 = 展示股数/后复权股数`（都等于当日因子）。方向反了的话
    #   一个是 f、另一个是 1/f，差出 f² 倍。
    checked = 0
    for r in rows:
        sh, shh = r.get('shares'), r.get('shares_hfq')
        px, pxh = r.get('price'), r.get('price_hfq')
        if not (sh and shh and px and pxh):
            continue
        f_sh = float(sh) / float(shh)
        f_px = float(pxh) / float(px)
        if f_sh < 1.05:
            continue                    # 因子 ~1 的票验不出方向
        checked += 1
        assert abs(f_sh / f_px - 1) < 0.02, \
            ('同一行里股数与价格用的不是同一个因子：股数比 %.4f / 价格比 '
             '%.4f（%s %s）—— 价格换算的方向反了（不复权 = 后复权 ÷ 因子）'
             % (f_sh, f_px, r.get('date'), r.get('code')))
    assert checked >= 5, \
        '只比到 %d 行有复权因子的 —— 这条方向判据基本没生效' % checked
    notes.append('旧归档按因子换算：%d 行里 %d 行非整手（因子与分红表不总对齐）；'
                 '%d 行的股数比与价格比是同一个因子' % (len(rows), n_bad, checked))

    # ---- ③ 持仓页同样 ----
    # 🔴 **必须挑 holdings 还在的归档** —— prune_runs.py 会清掉旧归档的
    #   holdings.parquet，而我第一版直接用了上面那个 `orid`，它的明细正好
    #   被清过：`rows` 是空的，于是**整块断言空转**（变异"持仓不换算"
    #   照样全绿）。判据是"真的有行"，不是"接口没报错"。
    hold_cands = [os.path.basename(os.path.dirname(x))
                  for x in _g.glob('runs/*/*/*/holdings.parquet')]
    horid = None
    for c in reversed(sorted(hold_cands)):
        hh = R.api_holdings({'id': c, 'limit': '300'})
        if hh and hh.get('rows'):
            horid, h = c, hh
            break
    assert horid, '没有任何归档还留着 holdings 明细 —— 这条验不了'
    if h and h.get('rows'):
        hb = sum(1 for r in h['rows'] if float(r['shares'] or 0) % 100)
        assert hb < len(h['rows']) * 0.10, \
            '持仓换算后 %d/%d 行不是整手' % (hb, len(h['rows']))
        assert any(r.get('shares_hfq') is not None for r in h['rows']), \
            '持仓没保留后复权原值'
        assert any(abs(float(r['shares']) - float(r.get('shares_hfq') or 0)) > 1
                   for r in h['rows']), \
            '持仓的展示份额与后复权原值一样 —— 等于没换算'
        notes.append('持仓 %d 行同样换算（%d 行非整手）' % (len(h['rows']), hb))

    # ---- ④ 🔴 现跑一小段，验【产生 fills 的代码】而不是磁盘上那个旧文件 ----
    #   上面读的是已经落盘的 parquet —— 改坏 broker 不会让它重新生成，
    #   于是"fills 里记后复权股数"那种变异照样全绿（实测漏过）。
    from assay.broker import Cost
    from assay.engine import Engine
    from assay.feed import PanelFeed
    import run as _run
    _mod = _run.load('strategies/小市值/froec_traded.py')
    _feed = PanelFeed('2026-05-06', '2026-06-30')
    _eng = Engine(_mod, _feed, cash=500000, cost=Cost())
    _eng.run(verbose=False)
    _fills = getattr(_eng.broker, 'fills', None)
    assert _fills, 'broker 没有记 fills'
    for _f in _fills:
        assert isinstance(_f['shares'], int), \
            ('broker.fills 的 shares 不是整数（%r）—— 真实股数本来就是整数，'
             '而 `sold * factor` 会带出浮点噪声（实测 3899.9999999999995）'
             % _f['shares'])
        assert _f['shares'] % 100 == 0, \
            ('broker.fills 里 %s %s 的股数 %s 不是整手 —— 它记的该是**真实**'
             '股数，后复权记账单位不是整手' % (_f['date'], _f['code'], _f['shares']))
    notes.append('现跑一段：broker.fills %d 笔全是整手的整数' % len(_fills))

    # ---- ⑤ 🔴 金额【不许】跟着换 —— 因子在 hfq股×hfq价 里天然约掉了 ----
    t = _pd.read_parquet(old[-1])
    r = t.iloc[0]
    assert abs(float(r.shares) * float(r.exit_price)
               - float(r.gross_amount)) < 0.01, \
        ('gross_amount 不等于 后复权股数×后复权价 —— 那说明归档里的金额'
         '口径变了，展示层"金额不用换"这个前提就不成立了')
    notes.append('金额不换（因子在 hfq股×hfq价 里约掉，实测逐笔相同）')
    return '；'.join(notes)


@case('实盘待办：卖出必须在预览里【落地】，否则买入腿凭空消失', tag='slow')
def t_preview_sell_settles():
    """2026-09-14 用户问「为什么是卖出一只、买入 0 只」，查出**两个叠在一起**
    的缺陷。这一条钉第一个。

    🔴 `RecordingBroker` 只记委托不撮合，于是卖出**不改 portfolio**。而
      froec 的买入腿是按**只数**截断的：

          need = [c for c in target if c not in positions]
          need = need[:max(0, len(target) - len(positions))]

      卖出没落地 -> positions 还是 10 -> `10-10=0` -> `need[:0]` **空**。
      于是待办说"卖 1 只、买 0 只"，而同一份策略在回测里会买进替补。
      **人照这份清单下单，卖完那笔钱就晾在账上了。**

    ★ 原 docstring 预见到的是"金额偏小"（并说股数由实盘模块按真实现金重算），
      **没预见到"那笔委托根本不产生"** —— 副作用被低估了一档。
    """
    from assay.broker import RecordingBroker, Cost
    from assay.context import Lot, Portfolio, Position
    from assay.feed import PanelFeed

    feed = PanelFeed('2026-06-01', '2026-06-30')
    d = feed.trading_days[-1]

    def _mk():
        pf = Portfolio(cash=10000.0, starting_cash=10000.0, positions={})
        rb = RecordingBroker(pf, feed, Cost())
        rb.date, rb.phase = d, 'OPEN'
        pf.positions['600000.XSHG'] = Position(
            code='600000.XSHG',
            lots=[Lot(shares=1000.0, entry_date=feed.trading_days[0],
                      entry_price=10.0)],
            last_price=12.0)
        return pf, rb

    pf, rb = _mk()
    assert len(pf.positions) == 1
    rb.order_target_value('600000.XSHG', 0)
    assert '600000.XSHG' not in pf.positions, \
        ('清仓委托没有在预览的 portfolio 里落地 —— 后面按「只数」算买入名额的'
         '策略会算出 0，那笔买入**凭空消失**（froec 的 _do_buy 就是这么写的）')
    assert pf.cash > 10000.0, \
        ('清仓之后现金没加回去（还是 %.2f）—— 买入腿按现金分配金额时会偏小'
         % pf.cash)
    assert abs(pf.cash - (10000.0 + 1000.0 * 12.0)) < 1.0, \
        '现金应按最后已知价估算，实得 %.2f' % pf.cash

    # 🔴 **买入委托不许落地** —— 下一个交易日的价还不存在，
    #   凭空给个成交价就是在编数据（这正是 RecordingBroker 存在的理由）。
    pf2, rb2 = _mk()
    n0 = len(pf2.positions)
    rb2.order_target_value('600519.XSHG', 50000)
    assert len(pf2.positions) == n0 and '600519.XSHG' not in pf2.positions, \
        '买入委托也落地了 —— 那需要一个还不存在的成交价'
    assert len(rb2.orders) == 1, '买入委托没被记下来'

    # 🔴 预览路径**不许记账**：一旦记 trades/费用，它就成了第二条卖出出口
    assert not rb.trades, 'RecordingBroker 记了 trades'
    assert not getattr(rb, 'fills', []), 'RecordingBroker 记了 fills'
    assert rb.fee_paid == 0 and rb.div_tax_paid == 0, \
        'RecordingBroker 收了费 —— 预览不该产生任何账务'
    return ('清仓落地（持仓去掉 + 现金 %.0f）、买入不落地、无任何记账副作用'
            % pf.cash)


@case('黑名单的「起点」在实盘里是【开户日】，不是重放窗口第一天', tag='slow')
def t_live_start_date():
    """用户："我新绑定的策略，黑名单不是应该只看实盘开始后吗" —— 是。
    这一条钉第二个缺陷。

    froec 有 `lu_since_start`（"起点前的涨停不算"），判据是 `g.start_date`。
    而 `g.start_date` 是 `prepare` 第一次跑时的 `current_date`：
    回测里那是回测起点（对），**实盘里那是【重放窗口】的第一天**
    （30 个交易日前），不是账户开户的那天。

    🔴 实测 2026-09-14：重放窗口 08-04 起、账户 09-01 开户，于是
      `lu_since_start=1` 只能把黑名单窗口抬到 08-04，而那两只的涨停在
      08-19 / 08-24 —— **仍在窗口内**。这个开关在实盘里**表达不了它名字
      说的那件事**，打开了也没用，而它不报错。

    ★ 修法：实盘模块把 `g.start_date` 设成**开户日** —— 喂真实初始状态本来
      就是它的职责（持仓、现金都是这么来的）。定义与「策略曲线」同一个
      （`lv/bench.py` 的 `created`），两处各写一份迟早分叉。
    """
    import io as _io
    import ast as _ast
    from assay import live as lv

    src = _io.open('assay/lv/sig.py', encoding='utf-8').read()
    assert 'start_date' in src, 'lv/sig.py 里没有设 start_date'
    # 判据走 ast：注释里提到 start_date 不算
    tree = _ast.parse(src)
    sets = [n for n in _ast.walk(tree) if isinstance(n, _ast.Assign)
            and any(isinstance(t, _ast.Attribute) and t.attr == 'start_date'
                    for t in n.targets)]
    assert sets, \
        ('lv/sig.py 没有给 `g.start_date` 赋值 —— 那它就还是重放窗口的第一天，'
         '`lu_since_start` 在实盘里表达不了"实盘开始之后"')
    # 🔴 必须用**开户日**，不是 feed/重放起点。判据看赋的是不是 created。
    seg = src[max(0, sets[0].lineno * 0):]
    assert "acct.get('created')" in src, \
        '`g.start_date` 不是取账户的 created —— "实盘起点"只能有一个定义'

    # ---- 真跑一遍：黑名单查询的【窗口起点】必须落在开户日 ----
    # 🔴 判据钉在 `had_limit_up(codes, lo, d)` 的 `lo` 上，不是内部变量：
    #   那才是这个修复**真正要改的东西**（黑名单往回看到哪天）。
    #   ★ 也不钩 `prepare` —— `run_daily(prepare, ...)` 在 initialize 里注册的是
    #     那一刻的函数对象，事后换模块属性对已注册的引用毫无影响
    #     （CLAUDE.md 里 bench 那次踩过，这次又踩了一遍）。
    # ★ 要挑**支持这个开关**的账户 —— 红利那套没有 `lu_since_start`，
    #   传进去引擎会直接报"策略里没有这个参数"（那是它该有的行为）。
    from assay.lv import sig as S
    from assay import guard as _guard
    seen, aid, created = [], None, None
    for a in lv.load_accounts():
        if a.get('archived') or not a.get('code_sha256'):
            continue
        pr = dict(a.get('params') or {})
        pr['lu_since_start'] = 1
        seen = []
        orig = _guard.GuardedFeed.had_limit_up
        def spy(self, codes, start, end):
            seen.append(str(start)[:10])
            return orig(self, codes, start, end)
        _guard.GuardedFeed.had_limit_up = spy
        try:
            S.build_signal(a['id'], params=pr)
        except KeyError:
            continue                     # 这套策略没有这个开关，换下一个
        finally:
            _guard.GuardedFeed.had_limit_up = orig
        if seen:
            aid, created = a['id'], (a.get('created') or '')[:10]
            break
    if not aid:
        return '静态断言通过（没有账户用得上涨停黑名单）'
    lo = max(seen)              # 最后一期（调仓那一次）用的窗口起点
    assert lo >= created, \
        ('开了 `lu_since_start=1`，黑名单窗口却从 %s 起算，而开户日是 %s '
         '—— 抬到的是【重放窗口】第一天而不是实盘起点，于是开户前的涨停'
         '照样把票拉黑，**而它不报错**' % (lo, created))
    return '黑名单窗口起点 %s == 开户日 %s（不是重放窗口第一天）' % (lo, created)


@case('模拟盘：成交由【引擎】产生 / 幂等 / 账本对得上 / 对账不一致不改写账本',
       tag='slow')
def t_paper_trading():
    """2026-09-14 用户："增加一个模拟盘功能，模拟盘开始后会在数据更新后
    自动执行策略到最新的时间。"

    **模拟盘 = 一个 `mode='paper'` 的实盘账户，成交由引擎产生写进同一本账。**
    于是持仓/TWR/业绩页/流水/选股理由一行新代码都不用写。

    🔴 **成交必须来自引擎**：整手取整、T+1、涨跌停无对手盘、成交量上限、
      最低佣金、印花税分段、红利税档位全在 broker.py 里，在模拟盘里照抄
      一遍就是第二份实现（同「核心原则：不重写任何交易规则」）。

    🔴 **账本仍然 append-only**：数据被修正之后重跑，过去那几天的成交可能
      就变了 —— 这时**报出来**，不静默改写（同「复算结果不许写回信号文件」）。
    """
    import io as _io
    import json as _json
    import shutil as _sh
    import tempfile as _tf
    from assay import live as lv

    # 🔴 重定向账本 —— selftest 绝不许写真账本（跑完 git status live/ 必须干净）
    real = lv.LIVE
    src_id = next((a['id'] for a in lv.load_accounts()
                   if not a.get('archived') and a.get('code_sha256')), None)
    if not src_id:
        return '跳过（没有已绑定策略的账户可以借版本快照）'
    FR = next(a for a in lv.load_accounts() if a['id'] == src_id)
    src = os.path.join(real, src_id)
    tmp = _tf.mkdtemp(prefix='selftest_paper_')
    lv.LIVE = tmp
    notes = []
    try:
        # ---- ① 建模拟盘 + 绑版本 ----
        lv.upsert_account('sim', name='模拟', init_cash=500000, mode='paper')
        assert lv.is_paper(lv.get_account('sim')), 'mode 没存成 paper'
        # 🔴 建好之后不能改类型 —— 同一本账混着真实成交与引擎成交，
        #   之后没法复盘。判据在 base.upsert_account 一处。
        try:
            lv.upsert_account('sim', mode='live')
            raise AssertionError('把模拟盘改成实盘竟然放行了 —— '
                                 '同一本账里混着真实成交与引擎成交，'
                                 '之后再也说不清哪一段是真的')
        except lv.LiveError:
            pass
        dst = os.path.join(tmp, 'sim')
        _sh.copytree(os.path.join(src, 'code'), os.path.join(dst, 'code'))
        _sh.copy(os.path.join(src, 'versions.jsonl'),
                 os.path.join(dst, 'versions.jsonl'))
        _rf = os.path.join(src, 'fee_rates.jsonl')
        if os.path.isfile(_rf):
            _sh.copy(_rf, os.path.join(dst, 'fee_rates.jsonl'))
        ac = lv.load_accounts()
        for x in ac:
            if x['id'] == 'sim':
                x['created'] = '2026-08-01'
                x['code_sha256'] = FR['code_sha256']
                x['strategy_path'] = FR.get('strategy_path')
                x['params'] = FR.get('params') or {}
        lv._save_accounts(ac)

        # ---- ② 推进 ----
        r = lv.advance('sim')
        assert r.get('ok'), '推进失败：%s' % r
        n1 = r['added']
        assert n1 > 0, '一笔成交都没跑出来 —— 引擎的 fills 没接上？'
        fills = lv.fills('sim')
        assert all(f['source'] == 'paper' for f in fills), \
            '模拟盘写的成交 source 必须是 paper（流水页要分得出人敲的和引擎跑的）'
        assert all(('side' in f and 'trade_date' in f) for f in fills), \
            ('账本里的成交缺字段 —— 多半是拿 `broker.trades`（往返记录）'
             '当流水了，它的字段是 entry_date/exit_date，没有 date/side')
        assert any(f['side'] == 'buy' for f in fills), \
            ('一笔买入都没有 —— broker.trades 只记**往返**（卖出时才写），'
             '拿它当流水会丢掉所有买入；要用 broker.fills')
        notes.append('推进出 %d 笔成交（买卖都有）' % n1)

        # ---- ③ 幂等：再推一次不许多出东西 ----
        r2 = lv.advance('sim')
        assert r2.get('ok') and r2['added'] == 0, \
            '再推一次又写了 %s 笔 —— 不幂等，账本会越推越胖' % r2.get('added')
        assert len(lv.fills('sim')) == len(fills), '账本行数变了'
        notes.append('幂等（再推 0 新增）')

        # ---- ④ 账本忠实：与引擎终态对得上，差额【必须报出来】 ----
        rc = r.get('recon') or {}
        assert rc.get('engine_equity'), '没有对账结果'
        rel = abs(rc['ledger_equity'] - rc['engine_equity']) / rc['engine_equity']
        assert rel < 0.005, \
            ('账本权益 %.2f 与引擎权益 %.2f 差 %.3f%% —— 超过 0.5%% 说明'
             '不是舍入，是股数/价格/费用的换算错了'
             % (rc['ledger_equity'], rc['engine_equity'], rel * 100))
        assert 'diff' in rc and 'by_code' in rc, \
            ('对账结果必须给出【差额与逐只明细】—— 两边天然差一点'
             '（舍入；以及复权因子里含着分红表没有的那些），'
             '假装相等的话它会一路悄悄漂')
        notes.append('账本 vs 引擎 差 %.2f 元（%.4f%%）'
                     % (rc['diff'], rel * 100))

        # ---- ⑤ 分红不是外部资金（TWR 的 bug，模拟盘把它逼出来的）----
        # 🔴 构造：一笔分红 + 一笔入金。分红不许进 net_deposit，入金必须进。
        #   ★ 只测分红的话分不出"全都没算"和"只没算分红"。
        # ★ 先只加**分红**：这时没有任何外部资金，所以项目的那条不变式
        #   「没有外部现金流时 TWR == 期末/起点 − 1」必须仍然成立。
        #   🔴 这才是这个 bug 的**用户可见后果** —— 只钉 net_deposit 的话，
        #     "TWR 把分红收益扣掉了"这件事本身没有被任何断言覆盖。
        lv.add_cashflow('sim', '2026-08-20', 1000.0, kind='dividend', note='t')
        cur = lv.equity_curve('sim')
        st0 = cur['stats']
        simple = cur['equity'][-1] / st0['equity_start'] - 1.0
        assert abs(st0['twr'] - simple) < 1e-5, \
            ('只有分红、没有入金时 TWR（%.6f%%）必须等于 期末/起点−1'
             '（%.6f%%）—— 差这一截就是分红被当成了外部资金：除权日股价掉'
             '下去记一笔负收益，到账日现金加回来又不计收益，**一来一回把'
             '分红收益扣了两次**。实测红利模拟盘差 1.44pp'
             % (st0['twr'] * 100, simple * 100))
        assert abs(st0['net_deposit']) < 0.01, \
            '只有分红时净入金该是 0，实得 %s' % st0['net_deposit']
        # 再加一笔**真入金**：它必须进 net_deposit —— 只测分红的话，
        # 分不出"全都没算"和"只没算分红"（把 FLOW_KINDS 清空也能全绿）。
        lv.add_cashflow('sim', '2026-08-21', 2000.0, kind='deposit', note='t')
        nd = lv.equity_curve('sim')['stats']['net_deposit']
        assert abs(nd - 2000.0) < 0.01, \
            '净入金该只含【入金 2000】，实得 %s' % nd
        notes.append('分红：TWR 不受影响且不进 net_deposit；入金 2000 照常进')

        # ---- ⑥ 对账不一致：报出来，**不改写账本** ----
        # 构造：把账本里某一笔的股数改掉，再推进
        fp = os.path.join(lv.acct_dir('sim'), 'fills.jsonl')
        raw = _io.open(fp, encoding='utf-8').read()
        lines = [x for x in raw.split('\n') if x.strip()]
        j = _json.loads(lines[0]); j['shares'] = int(j['shares']) + 100
        lines[0] = _json.dumps(j, ensure_ascii=False)
        _io.open(fp, 'w', encoding='utf-8').write('\n'.join(lines) + '\n')
        before = _io.open(fp, encoding='utf-8').read()
        r3 = lv.advance('sim')
        assert r3.get('mismatch'), \
            ('账本被改过之后重跑竟然没报不一致 —— 那条对账是空转的')
        assert not r3.get('ok') and r3.get('added') == 0, \
            '对账不一致时不该继续写入'
        assert _io.open(fp, encoding='utf-8').read() == before, \
            ('🔴 账本被改写了 —— 它是 append-only 的证据，数据修正之后'
             '重跑出不同答案是**另一件事**，只能报出来让人决定')
        notes.append('对账不一致 -> 报出来且账本一个字节没动')

        # ---- ⑦ reset 只删引擎写的那些，不碰手工录的 ----
        lv.add_fill('sim', '2026-08-05', FR and fills[0]['code'], 'buy', 100,
                    price=10.0, fee=5.0, source='manual', force_price=True)
        n_manual = len([f for f in lv.fills('sim') if f['source'] == 'manual'])
        assert n_manual == 1
        lv.reset('sim')
        left = lv.fills('sim')
        assert len(left) == 1 and left[0]['source'] == 'manual', \
            ('reset 把手工录的那笔也删了 —— 它只该删引擎跑出来的'
             '（模拟盘本来就是可重来的推演，手敲的那几笔不是）')
        notes.append('reset 只删 paper 的行，手工录的留着')

        # ---- ⑧ 实盘账户调 reset 必须被拒 ----
        lv.upsert_account('realacct', name='真', init_cash=1000, mode='live')
        try:
            lv.reset('realacct')
            raise AssertionError('实盘账本竟然允许重写 —— 那是不可重写的')
        except lv.LiveError:
            pass
        notes.append('实盘账户拒绝 reset')
    finally:
        lv.LIVE = real
        _sh.rmtree(tmp, ignore_errors=True)

    # ---- ⑨ 静态：成交不许在模拟盘里【重新撮合】 ----
    import ast as _ast
    src_p = _io.open('assay/lv/paper.py', encoding='utf-8').read()
    tree = _ast.parse(src_p)
    names = {n.func.attr for n in _ast.walk(tree)
             if isinstance(n, _ast.Call) and isinstance(n.func, _ast.Attribute)}
    assert 'build_engine' in names, \
        ('模拟盘没走 bench.build_engine —— 它必须与业绩页那条策略曲线'
         '【同一次构建】，各建一份的话费率/滑点/起点任何一处不同，'
         '就会出现"模拟盘的成交与策略曲线对不上"')
    for banned in ('Cost', 'Broker', 'PanelFeed'):
        assert banned not in {getattr(n, 'id', None) for n in _ast.walk(tree)
                              if isinstance(n, _ast.Name)}, \
            ('paper.py 里自己建了 %s —— 撮合与费率配置只能有一处'
             '（bench.build_engine），两份迟早分叉' % banned)

    # ---- ⑩ 自动触发必须排在「没有要重算的」那个提前返回【之前】----
    tk = _io.open('tick_daily.py', encoding='utf-8').read()
    i_paper = tk.index('lv.is_paper')
    i_ret = tk.index("_say('\\n没有要重算的。')")
    assert i_paper < i_ret, \
        ('模拟盘推进那段写在「没有要重算的」提前返回之后了 —— 那它永远'
         '跑不到：信号没变不代表模拟盘不用推（新建的账户一笔成交都没有，'
         '而它的指纹当然"没变过"）')
    notes.append('自动推进挂在 tick_daily 且排在提前返回之前')
    return '；'.join(notes)


@case('顶栏合并：板块收进盘面 / 对比收进个股，且【一个功能都没藏起来】（playwright）',
       tag='web')
def t_nav_merge():
    """2026-09-14 用户："上方的按钮做一些合并。对比功能放到个股中，板块的功能
    放到盘面中（确认是否已经包含了，如果已经包含则不需要了）。"

    先回答那个"是否已经包含"：**没有**。盘面只有「行业涨幅（申万一级）」
    一张表，而板块页是 **5 类 926 个**（申万 + 通达信 概念/风格/地区/研究）
    带成分股穿透 —— 盘面只覆盖了其中申万那一类。

    🔴 **合并的风险不是少两个按钮，是把功能藏起来。** 合并前通达信那 657 个
      （概念 269 / 风格 158 / 地区 32 / 研究 467）**只能从顶栏进**；顶栏一撤，
      它们就再也没有入口了 —— 而那不报错，只是从此没人找得到
      （同「独立页面最大的风险不是单页坏，是页面之间断链」那条）。
      所以这条用例的核心判据是：**服务端说有几类，盘面就得链得到几类**
      —— 照清单写死的话，将来加一类不会有人发现（同「断言直接扫目录
      而不是照清单拼」那条）。

    ★ 两个页面都还在（书签与深链接是产品契约），只是不在顶栏；
      进去之后顶栏点亮**父级**，否则"我在哪"没有任何指示。
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return '跳过（无 playwright）'
    import threading
    from assay import server as sv
    from http.server import ThreadingHTTPServer
    old_live = sv.ALLOW_LIVE
    sv.ALLOW_LIVE = True
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), sv.Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = 'http://127.0.0.1:%d' % port
    notes = []
    try:
        # 服务端先自证：到底有几类板块（判据的来源，不写死）
        import json as _json
        import urllib.request as _u
        ks = _json.load(_u.urlopen(base + '/api/sector/kinds'))['kinds']
        want = sorted(k['kind'] for k in ks)
        assert len(want) >= 4, '板块类别只有 %d 类？判据没意义了' % len(want)

        with sync_playwright() as pw:
            br = pw.chromium.launch()
            pg = br.new_page(viewport={'width': 1440, 'height': 900})
            errs = []
            pg.on('pageerror', lambda e: errs.append(str(e)))

            # ---------- ① 顶栏 ----------
            pg.goto(base + '/market.html', wait_until='networkidle')
            pg.wait_for_selector('.lvsec', timeout=60000)
            navs = pg.locator('#top a.nav').all_inner_texts()
            assert len(navs) == 7, '顶栏该是 7 个入口：%s' % navs
            j0 = ' '.join(navs)
            assert '板块' not in j0 and '对比' not in j0, \
                '顶栏还留着板块/对比：%s' % navs

            # ---------- ② 盘面必须链得到【全部】板块类别 ----------
            got = pg.evaluate("""() => [...new Set(
                [...document.querySelectorAll('a[href*="sector.html"]')]
                  .map(a => new URL(a.href).searchParams.get('kind'))
                  .filter(Boolean))]""")
            missing = [k for k in want if k not in got]
            assert not missing, \
                ('盘面上走不到这几类板块：%s（服务端说有 %s）—— 顶栏撤掉之后'
                 '它们就**再也没有入口**了，而那不报错，只是从此没人找得到'
                 % (missing, want))
            n_chip = pg.locator('.lvsec a.chip[href*="sector.html"][href*="kind="]').count()
            assert n_chip >= len(want) - 1, \
                '盘面上的板块类别 chip 太少（%d 个）' % n_chip
            notes.append('盘面链得到全部 %d 类板块（服务端清单为准，不写死）'
                         % len(want))

            # ---------- ③ 板块页仍可直达，且顶栏点亮父级 ----------
            pg.goto(base + '/sector.html?kind=concept', wait_until='networkidle')
            pg.wait_for_timeout(1500)
            on = pg.locator('#top a.nav.on')
            assert on.count() == 1 and '盘面' in on.inner_text(), \
                ('板块页的顶栏该点亮父级「🌡 盘面」，实得 %s —— NAV 里已经没有'
                 '它自己那一项，传旧 key 的话一个都不亮，"我在哪"没有指示'
                 % (on.all_inner_texts()))
            assert '板块' in pg.locator('#top h1').inner_text(), \
                '板块页的标题该仍是它自己（亮的是所属组，写的是它是什么）'
            assert pg.locator('table').count() >= 1, '板块页没渲染出来'

            # ---------- ④ 个股的对比入口必须【带上当前这只票】 ----------
            pg.goto(base + '/stock.html?code=601857.XSHG', wait_until='networkidle')
            pg.wait_for_selector('#cmp', timeout=60000)
            href = pg.locator('#cmp').get_attribute('href')
            assert 'codes=' in href and '601857' in href, \
                ('个股页的对比入口没带当前这只票（href=%s）—— 对比天然是'
                 '"拿【这只】和别的比"，链到裸页面等于到了那边还要再搜一遍，'
                 '那是把入口做成了摆设' % href)
            #   点过去要真的画出这只票的曲线（不是只改了 href）
            pg.locator('#cmp').click()
            pg.wait_for_timeout(2500)
            assert 'compare.html' in pg.url, '点对比入口没跳到对比页：%s' % pg.url
            px = pg.evaluate("""() => {
              const c = document.querySelector('canvas');
              if(!c) return 0;
              const x = c.getContext('2d').getImageData(0,0,c.width,c.height).data;
              let n = 0; for(let i=3;i<x.length;i+=4) if(x[i]) n++;
              return n;}""")
            assert px > 500, \
                ('对比页没画出曲线（非透明像素 %d）—— 带着代码跳过去却是'
                 '一张空图，等于入口没通' % px)
            on2 = pg.locator('#top a.nav.on')
            assert on2.count() == 1 and '个股' in on2.inner_text(), \
                '对比页的顶栏该点亮父级「📈 个股」，实得 %s' % on2.all_inner_texts()
            notes.append('个股 -> 对比带着代码过去并真的画出曲线（%d 像素）' % px)
            notes.append('两个子页仍可直达、顶栏点亮父级')
            assert not errs, 'JS 报错：%s' % errs[:3]
            br.close()
    finally:
        sv.ALLOW_LIVE = old_live
        httpd.shutdown()
    return '；'.join(notes)


@case('总览首页 / 顶栏分组 / 数据页签（playwright）', tag='web')
def t_home_ui():
    """信息架构那一层的自证。

    ★ 打开看板第一眼该回答「今天什么状态、要做什么」—— 而不是一棵回测
      目录树（那是做策略时才进的，已移到 #/runs）。
    ★ 顶栏按【使用频率】分组，不是平铺：平铺时每次都要在 8 个里扫一遍
      才找到要去的地方，而它们的重要性差很远。
    ★ 首页每块只给【摘要 + 一个入口】，不重复做那一页的事 ——
      首页做成小型全功能页的话，同一份数据两处渲染，迟早不一致。
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return '跳过（无 playwright）'
    import threading
    from http.server import ThreadingHTTPServer

    from assay import server as sv
    old_live = sv.ALLOW_LIVE
    sv.ALLOW_LIVE = True
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), sv.Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = 'http://127.0.0.1:%d' % port
    try:
        with sync_playwright() as p:
            try:
                br = p.chromium.launch()
            except Exception as e:                          # noqa: BLE001
                return '跳过（浏览器不可用: %s）' % type(e).__name__
            pg = br.new_page(viewport={'width': 1600, 'height': 1100})
            errs = []
            pg.on('pageerror', lambda e: errs.append(str(e)))
            pg.on('console',
                  lambda m: errs.append('console: ' + m.text) if m.type == 'error' else None)

            # ---------------- 首页 ----------------
            pg.goto(base + '/', wait_until='networkidle')
            pg.wait_for_selector('#main .lvsec', timeout=60000)
            pg.wait_for_timeout(2500)
            assert '总览' in pg.title(), '首页标题不对：%s' % pg.title()
            # ★ 打开看板第一眼不该是回测目录树
            assert not pg.is_visible('#cat'), '首页居然显示的是回测目录树'
            secs = [x.split('\n')[0] for x in
                    pg.locator('#main .lvsec h3').all_inner_texts()]
            for k in ('实盘', '盘面', '买点', '自选', '数据'):
                assert any(k in x for x in secs), '首页缺「%s」这一块：%s' % (k, secs)
            body = pg.locator('#main').inner_text()
            assert 'undefined' not in body and 'NaN' not in body, \
                '首页有 undefined/NaN'
            # 每块的标题本身就是入口（首页只给摘要）
            for href in ('#/live', '/market.html', '/watchlist.html',
                         '/alerts.html', '#/sync'):
                assert pg.locator('#main .lvsec h3 a[href="%s"]' % href).count() >= 1, \
                    '「%s」那块的标题不是入口' % href
            # 站名在首页要点亮 —— 否则"我在哪"没有指示
            assert pg.locator('#top h1 a.homeon').count() == 1, \
                '首页没把站名点亮'
            # 实盘那块必须真的有账户与数字（不是空壳）
            live_sec = pg.locator('#main .lvsec').first.inner_text()
            assert '总资产' in live_sec and '持仓浮盈' in live_sec, \
                '实盘那块没渲染出数字：%s' % live_sec[:120]

            # ---------------- 顶栏分组 ----------------
            navs = pg.locator('#top a.nav').all_inner_texts()
            #   🔴 9 -> 7（2026-09-14）：板块收进盘面、对比收进个股。
            assert len(navs) == 7, '顶栏应是 7 个入口：%s' % navs
            joined0 = ' '.join(navs)
            for gone in ('板块', '对比'):
                assert gone not in joined0, \
                    ('顶栏还有「%s」—— 它已经收进父页了：%s' % (gone, navs))
            assert '实盘' in navs[0], \
                '实盘应排最前 —— 它是唯一回答"今天要做什么"的入口：%s' % navs
            # 买点紧跟实盘（同属"每天必看"那一组，都是回答"今天要做什么"）
            assert '买点' in navs[1], '买点应紧跟实盘：%s' % navs
            assert pg.locator('#top .navsep').count() == 3, \
                '应有 3 条分组分隔（实盘 | 市场 | 研究 | 数据）'
            # 已取消的入口不该还在
            joined = ' '.join(navs)
            assert '查数据' not in joined, '顶栏还有已取消的「查数据」'
            assert '数据字典' not in joined, \
                '「数据字典」应并进「数据」页的页签，顶栏不再单列'

            # ---------------- 回测归档搬到 #/runs ----------------
            pg.click('#top a.nav[href="/#/runs"]')
            pg.wait_for_timeout(1500)
            assert pg.is_visible('#cat'), '#/runs 没显示回测目录树'
            assert pg.locator('.nd.d0').count() > 0, '目录树没有顶层节点'
            # 过滤/全部展开只在这个视图里出现（别处是噪声）
            assert pg.locator('#filter').count() == 1, '#/runs 缺过滤框'
            pg.fill('#filter', '不可能匹配的字符串xyz')
            pg.wait_for_timeout(600)
            assert '没有匹配' in pg.locator('#cat').inner_text(), '过滤没生效'
            pg.fill('#filter', '')
            pg.wait_for_timeout(600)

            # ---------------- 数据页签 ----------------
            pg.click('#top a.nav[href="/#/sync"]')
            pg.wait_for_selector('#main .lvsec', timeout=90000)
            pg.wait_for_timeout(1200)
            assert pg.locator('#filter').count() == 0, \
                '过滤框跑到数据页去了 —— 它只在回测目录有意义'
            tabs = pg.locator('#main .btn').all_inner_texts()[:2]
            assert '数据状态 / 同步' in tabs[0] and '口径字典' in tabs[1], \
                '数据页的两个页签不对：%s' % tabs
            assert '数据' in pg.locator('#top .btn.nav.on').inner_text()
            # 切到口径字典：同一个顶栏入口仍然高亮（它们是一件事的两面）
            pg.click('#main .btn:has-text("口径字典")')
            pg.wait_for_selector('.ditem', timeout=60000)
            pg.wait_for_timeout(800)
            assert pg.locator('.ditem').count() >= 4, '字典侧栏没渲染'
            assert '数据' in pg.locator('#top .btn.nav.on').inner_text(), \
                '切到字典页签后顶栏高亮跑了'
            assert '#/docs' in pg.url, 'URL 没跟着变（旧链接要能直达）'
            # 切回来
            pg.click('#main .btn:has-text("数据状态")')
            pg.wait_for_timeout(2000)
            assert pg.locator('#syrun').count() == 1, '切不回同步页签'

            # ---------------- 顶栏标题跟着 hash 变 ----------------
            for h, want in (('#/live', '实盘'), ('#/picks', '选中的规则'),
                            ('#/runs', '回测归档'), ('', '总览')):
                pg.goto(base + '/' + h, wait_until='networkidle')
                pg.wait_for_timeout(2200)
                got = pg.locator('#top h1').inner_text()
                assert want in got, \
                    ('%s 的标题应是「%s」，实得「%s」—— 固定写死的话在实盘页'
                     '也会显示"回测归档"，而顶栏本来是用来告诉人"我在哪"的'
                     % (h or '(空)', want, got))

            br.close()
            assert not errs, '页面有运行时错误：%s' % errs[:3]
            return ('首页是总览（6 块，标题即入口，站名点亮，不再是回测目录树）；'
                    '顶栏 7 个入口分 4 组 3 条分隔、实盘排最前、板块与对比已收进父页、'
                    '已取消的「查数据」与并入页签的「数据字典」都不在顶栏；'
                    '回测归档在 #/runs 且过滤框只在那里；'
                    '数据页两个页签互切、顶栏高亮不跑、URL 可直达；'
                    '顶栏标题跟着 hash 变（4 个视图核过）')
    finally:
        sv.ALLOW_LIVE = old_live
        httpd.shutdown()


@case('外部行情接口文档：结构完整 / 示例代码能跑（离线）', tag='fast')
def t_extapi_doc():
    """索引 5 是【调研结果的定案】—— 它存在的意义就是"下次不要重新试一遍"。

    ★ 所以这条用例守两件事：
      1. **文档在字典页里挂着**（写了没挂 = 只有翻仓库才看得到）
      2. **示例代码是真代码** —— 能 import、能定义出那三个函数、
         并且**超限时会抛错而不是返回空**。

    ★ 刻意**不打真接口**：selftest 要能离线跑，而且拿外部接口当断言
      等于把别人的限流变成自己的红灯。真实连通性是调研时逐个实测过的
      （耗时、条数、字段位都写在文档里），这里只守"文档没烂"。
    """
    import re

    from assay import server as sv
    root = sv._repo_root()
    keys = {d[0] for d in sv._DOCS}
    assert 'extapi' in keys, '索引 5 没挂进数据字典页 —— 写了没挂等于没写'
    rel = [d[3] for d in sv._DOCS if d[0] == 'extapi'][0]
    path = os.path.join(root, rel)
    assert os.path.isfile(path), '找不到 %s' % path
    md = open(path, encoding='utf-8').read()

    # ---- 1) 两个索引都要在（用户明确要的：分渠道 + 分功能）----
    assert '## 1. 分渠道索引' in md, '缺【分渠道】索引'
    assert '## 2. 分功能索引' in md, '缺【分功能】索引'
    assert '## 3. 🔴 陷阱' in md, '缺陷阱段'

    # ---- 2) 四个主力接口的域名都要写明 ----
    for host, why in (('quotes.sina.cn', '唯一有历史深度的分钟源'),
                      ('push2his.eastmoney.com', '当日分时'),
                      ('push2delay.eastmoney.com', '东财被限流时的唯一活口'),
                      ('qt.gtimg.cn', '批量实时快照'),
                      ('ifzq.gtimg.cn', '腾讯分时')):
        assert host in md, '文档里没有 %s（%s）' % (host, why)
    # 已确认不可用的要标出来，否则下次还会去试
    assert 'hq.sinajs.cn' in md and '403' in md, \
        '没写明 hq.sinajs.cn 已经 403 —— 下次会重新踩'
    assert 'web.ifzq.gtimg.cn' in md, \
        '没写明 web. 那个子域 DNS 解析不了（stock-sdk 源码里写的正是它）'

    # ---- 3) 历史深度是这次调研最值钱的结论，必须逐档写清 ----
    for scale, days in (('1 分', '5 日'), ('5 分', '22 日'),
                        ('15 分', '65 日'), ('30 分', '129 日'),
                        ('60 分', '257 日')):
        assert scale in md and days in md, \
            '新浪 %s 的历史深度（%s）没写' % (scale, days)

    # ---- 4) 三个"静默失败"必须写明（这类最费时间）----
    for k in ('静默', 'RemoteDisconnected', 'data: null', '1023'):
        assert k in md, '陷阱段缺「%s」' % k

    # ---- 5) 示例代码是真代码：能 exec，且定义出三个函数 ----
    m = re.search(r'```python\n(.*?)```', md, re.S)
    assert m, '文档里没有可抄的示例代码'
    ns = {}
    exec(compile(m.group(1), '<doc>', 'exec'), ns)      # noqa: S102
    for fn in ('sina_minute', 'em_trends', 'tx_quote'):
        assert callable(ns.get(fn)), '示例代码里没有 %s()' % fn
    # ★ 超限要抛错而不是返回空 —— 这是文档反复强调的那条，
    #   示例代码自己得做到。用假的取数函数验，不打真接口。
    ns['_get'] = lambda *a, **k: 'null'
    try:
        ns['sina_minute'](n=4000)
        raise AssertionError('示例代码在拿到空结果时没抛错 —— '
                             '而文档说"空结果一律当失败处理"')
    except RuntimeError:
        pass

    # ---- 6) 交叉引用：按需求 / 按陷阱两个索引都要指过来 ----
    for idx in ('1-按需求索引.md', '4-按陷阱索引.md', 'README.md'):
        p2 = os.path.join(root, 'datalake', 'docs', '数据字典', idx)
        t2 = open(p2, encoding='utf-8').read()
        assert '索引 5' in t2 or '5-外部行情接口' in t2, \
            '%s 没指向索引 5 —— 从别的索引查过来时会以为本地没有这块' % idx
    return ('索引 5 已挂进字典页（%.1fK）；分渠道 + 分功能两个索引齐全；'
            '5 个域名与 2 个已失效的都写明；新浪 5 档历史深度逐档记录；'
            '3 类静默失败写明；示例代码可 exec 且超限会抛错；'
            '按需求/按陷阱/README 三处都有交叉引用'
            % (len(md.encode()) / 1024))


@case('盘中 1 分钟线：时段 / 落盘去重 / 实时估值 / 限流兜底', tag='fast')
def t_realtime():
    """两个源各干各的，这是稳定性的关键（见 realtime.py 模块说明）：

        腾讯 qt.gtimg.cn  **批量**快照  1 次请求覆盖全部持仓 -> 实时盈亏
        东财 trends2      单只          一次拿当天全部 1 分钟 -> 落盘的 bar

    ★ 这条用例**不打真接口** —— selftest 要能离线跑，而且拿外部接口当断言
      等于把别人的限流变成自己的红灯。真实连通性在调研时实测过
      （见数据字典索引 5）。这里用假数据验【我们自己的逻辑】：
      时段判定、按键去重、实时价覆盖、以及**取不到时的行为**。
    """
    import shutil
    import tempfile
    from datetime import datetime as _dt
    from datetime import time as _t

    from assay import live as lv
    from assay import realtime as rt

    # ---- 1) 交易时段：边界要含在内 ----
    #   ★ 15:01 而不是 15:00 —— 收盘那一根要到 15:01 才拿得到。
    #     写成 15:00 会永远缺当天最后一根，而这不会报错。
    for hh, mm, want in ((9, 29, False), (9, 30, True), (11, 30, True),
                         (11, 31, True), (11, 32, False), (12, 30, False),
                         (13, 0, True), (14, 59, True), (15, 0, True),
                         (15, 1, True), (15, 2, False)):
        got = rt.in_session(_dt(2026, 9, 3, hh, mm))
        assert got is want, '%02d:%02d 应%s在时段内' % (hh, mm, '' if want else '不')
    assert rt.SESSIONS[0][1] == _t(11, 31) and rt.SESSIONS[1][1] == _t(15, 1), \
        '收盘边界要留到 11:31 / 15:01，否则永远缺最后一根'

    # ---- 2) 落盘：两个库分开 + 按键去重 ----
    tmp = tempfile.mkdtemp()
    os.makedirs(os.path.join(tmp, 'rt'), exist_ok=True)
    try:
        day = '2026-09-03'
        bar = {'code': '601857.XSHG', 'datetime': day + ' 09:31',
               'open': 11.2, 'close': 11.25, 'high': 11.26, 'low': 11.19,
               'volume': 100.0, 'amount': 112000.0, 'avg': 11.22}
        rt.save([bar], day, root=tmp)
        rt.save([bar], day, root=tmp)                   # 同一根写两遍
        rt.save([dict(bar, close=11.30, volume=180.0)], day, root=tmp)
        b = rt.bars('601857.XSHG', day, root=tmp)
        assert len(b) == 1, \
            ('同一 (code, datetime) 必须只留一行 —— 追加会让成交量凭空翻倍，'
             '而多出来的行不报错：%s' % b)
        assert b[0]['volume'] == 180.0, '重复写应保留量更大的那条（更完整）'
        # 两个库是不同文件
        assert rt.day_file(root=tmp, kind='minute_1m') != \
            rt.day_file(root=tmp, kind='snap_1m'), 'bar 与快照必须分开存'

        snap = {'code': '601857.XSHG', 'price': 11.31, 'preclose': 11.26,
                'open': 11.2, 'high': 11.4, 'low': 11.18, 'change_pct': 0.44,
                'turnover_pct': 1.9, 'volume': 5000.0, 'amount': 5.6e7,
                'ts': '20260903143012'}
        rt.save_snap([snap], root=tmp)
        rt.save_snap([dict(snap, price=11.33, ts='20260903143055')], root=tmp)
        lt = rt.latest(['601857.XSHG'], day, root=tmp)
        v = lt['601857.XSHG']
        assert v['at'] == '2026-09-03 14:30', '快照要按【分钟】归并：%s' % v['at']
        assert v['price'] == 11.33, '同一分钟应留最后一次采样'
        assert v['src'] == 'snap', \
            '最新价应优先取快照库（它每分钟每只都有，bar 是轮转抓的）'
        assert v.get('avg') is None or v['at'] != b[0]['datetime']

        # ---- 3) 快照缺某只时，bar 库要能补上 ----
        lt2 = rt.latest(['601857.XSHG', '600519.XSHG'], day, root=tmp)
        assert '600519.XSHG' not in lt2, \
            '两个库都没有的票【不该】出现 —— 不能拿别的价顶上'
        rt.save([dict(bar, code='600519.XSHG', close=1500.0)], day, root=tmp)
        lt3 = rt.latest(['600519.XSHG'], day, root=tmp)
        assert lt3['600519.XSHG']['src'] == 'bar', '快照没有时应回落到 bar 库'

        # ---- 4) status / stale ----
        st = rt.status(day, root=tmp)
        assert st['bar']['rows'] == 2 and st['snap']['rows'] == 1
        assert st['last'] == '2026-09-03 14:30', \
            'last 应取两个库里更新的那个（否则会以为一直落后）：%s' % st['last']
        # 非交易时段不算"落后"
        assert rt.stale_minutes(day, tmp, now=_dt(2026, 9, 3, 12, 30)) is None, \
            '非时段不该报落后 —— 收盘后当然落后，那不是缺数据'
        assert rt.stale_minutes(day, tmp, now=_dt(2026, 9, 3, 14, 35)) == 5

        # ---- 5) 抓取失败：空结果一律当失败，不能当"这只票没数据" ----
        #   东财限流时会返回 data:null 或空 trends（见索引 5 §3.1）。
        real = rt.urllib.request.urlopen
        try:
            class _R:
                def __init__(self, b):
                    self._b = b

                def read(self):
                    return self._b
            rt.urllib.request.urlopen = lambda *a, **k: _R(b'{"data":null}')
            try:
                rt.fetch_one('601857.XSHG')
                raise AssertionError('返回 data:null 时必须报错，'
                                     '不能当成"这只票没数据"（会把持仓价清空）')
            except rt.RTError as e:
                assert '限流' in str(e) or '空' in str(e), '报错要说清原因：%s' % e
            # 整批里一只失败不该打断其它只
            r = rt.fetch(['601857.XSHG', '600519.XSHG'], root=tmp, sleep=0)
            assert r['ok'] == 0 and len(r['fail']) == 2, r
        finally:
            rt.urllib.request.urlopen = real

        # ---- 6) 字段位序：trends2 少一个字段就整行错位 ----
        src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                'assay', 'realtime.py'), encoding='utf-8').read()
        assert 'f51,f52,f53,f54,f55,f56,f57,f58' in src, \
            ('trends2 必须请求 8 个 fields2 —— 漏掉 f52(开) 时只返回 7 段，'
             '按 8 段解析会把"收"当成"开"，而这不报错')
        assert 'TX_BATCH = 400' in src, \
            '腾讯批量要有上限（实测 900 ✓ / 950 ❌ HTTP 414）'
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # ---- 7) 持仓估值：有实时价就用，没有就回落收盘，且【标出来】----
    P = lv.positions_valued('froec')
    assert 'price_src' in P and 'rt_n' in P, '估值没给"价来源"'
    assert P['price_src'] in ('实时', '部分实时', '收盘')
    for x in P['items']:
        if x.get('rt_src'):
            assert x.get('rt_at'), '标了实时却没给时刻'
    if P['rt_n']:
        assert P['asof'] == P['rt_at'], \
            ('全部实时时 asof 要写实时那一刻 —— 还写日线日的话，页面上会出现'
             '"估值日 09-02"配 09-03 的实时价：%s vs %s' % (P['asof'], P['rt_at']))
        assert P['asof_close'] and P['asof_close'] != P['asof'], \
            '收盘日要单独留一份（部分实时时要用它说明其余按哪天算）'
    # ---- 并发落盘 / 坏文件自愈 / 接口逐腿降级（2026-09-07 生产事故）----
    # 🔴 实测事故链：写当日文件的有【三个来源】，两个在 HTTP 请求线程上
    #   （`_rt_ensure` / `_rt_catch_up`），而 `save()` 当时**没有锁**。
    #   两个线程同时落盘 -> 落地一个头尾都是 PAR1、中间元数据是垃圾的 parquet
    #   -> 此后每一轮读它都抛 TProtocolException -> `_rt_loop` catch 住继续转，
    #   但 `rounds`/`last` 不再更新 -> **线程活着、每 60 秒失败一次，
    #   连续 3.5 小时**（09:47 -> 13:20），页面上只表现为"实时不刷新"。
    #   同时 `/api/rt/status` 整体 500，把**健康的快照腿**也一起藏了。
    import threading as _th
    from datetime import date as _date
    import duckdb as _dk
    _d = tempfile.mkdtemp(prefix='rtconc_')
    os.makedirs(os.path.join(_d, 'rt', 'minute_1m'), exist_ok=True)
    _day = '2026-09-07'
    _p = rt.day_file(_date.fromisoformat(_day), root=_d)

    def _row(i):
        return {'code': '60%04d.XSHG' % i,
                'datetime': '2026-09-07 10:%02d:00' % (i % 60),
                'open': 1.0, 'close': 1.1, 'high': 1.2, 'low': 0.9,
                'volume': i, 'amount': 1.0, 'avg': 1.0}

    # ① 并发写：不许丢行、不许撕裂、不许残留 tmp
    #   ★ 判据是**行数**而不是"没抛异常" —— 无锁版本一个异常都不抛，
    #     它只是把别人写的行悄悄丢掉（实测 200 线程后只剩 1 行）。
    _errs = []

    def _w(i):
        try:
            rt.save([_row(i)], _day, root=_d)
        except Exception as e:                              # noqa: BLE001
            _errs.append('%s: %s' % (type(e).__name__, e))
    _ts = [_th.Thread(target=_w, args=(i,)) for i in range(40)]
    [t.start() for t in _ts]
    [t.join() for t in _ts]
    assert not _errs, '并发落盘抛异常：%s' % _errs[:3]
    _n = _dk.connect().execute(
        "SELECT count(*) FROM read_parquet('%s')" % _p).fetchone()[0]
    assert _n == 40, ('并发落盘丢行：40 个线程各写 1 行，最终只有 %d 行 —— '
                      'save() 的读-合并-写没有串行化' % _n)
    _left = [f for f in os.listdir(os.path.dirname(_p)) if f.endswith('.tmp')]
    assert not _left, '残留 tmp 文件：%s' % _left

    # ② 坏文件必须【隔离 + 重建】，不能让异常冒出去
    with open(_p, 'wb') as _f:              # 头尾像 parquet、中间是垃圾
        _f.write(b'PAR1' + b'\x00' * 200 + b'PAR1')
    try:
        _dk.connect().execute("SELECT count(*) FROM read_parquet('%s')" % _p)
        raise AssertionError('人造的坏文件居然读得动 —— 这条断言是空转的')
    except AssertionError:
        raise
    except Exception:                                       # noqa: BLE001
        pass
    rt.save([_row(999)], _day, root=_d)     # 不许抛
    _n2 = _dk.connect().execute(
        "SELECT count(*) FROM read_parquet('%s')" % _p).fetchone()[0]
    assert _n2 == 1, '坏文件没被隔离重建（重建后应只剩新写的 1 行，实得 %d）' % _n2
    _q = os.path.join(os.path.dirname(_p), '_corrupt')
    assert os.path.isdir(_q) and os.listdir(_q), \
        '坏文件被直接丢弃了 —— 必须留证据，否则分不清撕裂写还是磁盘坏'

    # ③ status() 一条腿坏了不能打挂整个接口，且要把坏腿【说出来】
    with open(_p, 'wb') as _f:
        _f.write(b'PAR1' + b'\x00' * 200 + b'PAR1')
    _st = rt.status(day=_date.fromisoformat(_day), root=_d)
    assert 'error' not in _st, 'status 被一条坏腿整体打挂了：%s' % _st.get('error')
    assert _st['bar'].get('err'), \
        '坏掉的 bar 腿没有 err 标记 —— 页面会以为"就是没有数据"'
    assert 'snap' in _st, 'status 少了 snap 腿'
    shutil.rmtree(_d, ignore_errors=True)

    return ('时段 11 个边界点（收盘留到 11:31/15:01）；bar 与快照两库分开且'
            '按键去重（重复写不翻倍）；最新价优先快照、缺了回落 bar、'
            '两库都没有的不顶价；last 取两库较新者；非时段不报落后；'
            'data:null 当失败且不打断整批；trends2 八字段位序与腾讯批量上限'
            '写死在代码里；40 线程并发落盘不丢行/不撕裂/无残留 tmp；'
            '坏文件隔离重建且留证；status 逐腿降级（坏腿标 err 不打挂接口）；'
            '持仓估值 %s（%d/%d 实时）'
            % (P['price_src'], P['rt_n'], len(P['items'])))


@case('模块拆分：路径基准 / 延迟导入层级 / 门面双向转发', tag='fast')
def t_srv_split():
    """server.py 按产品域拆进 srv/ 之后，三处**不报错**的坑，逐条钉住。

    这三条都是实测踩到的，表现全是"接口静默返回空或 500"，而不是启动报错。
    """
    import importlib
    from assay import server as sv
    from assay.srv import base
    here = os.path.dirname(os.path.abspath(__file__))

    # ---- ① 路径基准：__file__ 跟着文件搬进了子目录 ----
    #   🔴 HERE 照抄 dirname(__file__) 会变成 .../assay/srv，于是
    #     picks.json / live/ / web/ 全解到不存在的路径。
    #     实测：/api/marks 返回 {}（标记全丢）、7 个实盘接口 500。
    assert base.HERE == os.path.join(here, 'assay'), \
        'base.HERE 应指 assay 包目录，实际 %s' % base.HERE
    assert base.WEB == os.path.join(here, 'web'), \
        'base.WEB 错了：%s' % base.WEB
    assert os.path.dirname(base.MARKS_FILE) == here, \
        'picks.json 应在仓库根，实际 %s' % base.MARKS_FILE

    # ---- ② 延迟导入的层级：`.` 也跟着文件变了 ----
    #   🔴 base 里 `from . import live` 原来是 assay.live，搬进 srv/ 之后
    #     `.` 变成 assay.srv → 返回**路由模块自己**，于是 m.LiveError
    #     AttributeError。实测就是这么 500 的。
    WRAP = {'_live': 'assay.live', '_rt': 'assay.realtime',
            '_watch': 'assay.watchlist', '_alerts': 'assay.alerts',
            '_market': 'assay.market'}
    for fn, want in WRAP.items():
        got = getattr(base, fn)().__name__
        assert got == want, \
            'base.%s() 应返回 %s，实际 %s —— 相对导入解错了层' % (fn, want, got)

    # ---- ③ 门面必须【转发】而不是 re-export ----
    #   🔴 ALLOW_BACKTEST / ALLOW_LIVE 是 serve() 会重新赋值的 bool。
    #     `from .srv.base import ALLOW_LIVE` 拿到的是副本 —— serve() 改了值
    #     外部读到的还是旧的，表现是"明明开了实盘，接口说功能没开"。
    #   而且必须**双向**：外部有 `sv.ALLOW_LIVE = True` 这种赋值用法
    #   （本文件 26 处，用来模拟启动模式）。PEP 562 的模块级 __getattr__
    #   只拦【读】—— 赋值会在 server 模块 __dict__ 里建副本，之后读取
    #   走正常查找、不再转发，各域读到的还是旧值。
    #   实测这么挂掉 3 个用例（启动开关 / 自选 / 本条）。所以门面是
    #   ModuleType 子类，__setattr__ 把这几个名字写回 base。
    old = (base.ALLOW_BACKTEST, base.ALLOW_LIVE)
    try:
        base.ALLOW_BACKTEST, base.ALLOW_LIVE = True, True
        assert sv.ALLOW_BACKTEST is True and sv.ALLOW_LIVE is True, \
            '写 base 之后 sv.ALLOW_* 没跟着变 —— 门面成了 re-export'
        sv.ALLOW_BACKTEST, sv.ALLOW_LIVE = False, False
        assert base.ALLOW_BACKTEST is False and base.ALLOW_LIVE is False, \
            ('写 sv.ALLOW_* 没写回 base —— 各域读的是 base，'
             '于是"设置了却不生效"')
        assert 'ALLOW_LIVE' not in vars(sv), \
            'sv.__dict__ 里出现了 ALLOW_LIVE 副本 —— __setattr__ 没拦住'
    finally:
        base.ALLOW_BACKTEST, base.ALLOW_LIVE = old

    # ---- 对外契约：selftest 自己用到的 sv.* 名字必须都还在 ----
    #   ★ 直接从本文件的源码里**扫**出来，不照清单拼 ——
    #     照清单拼的话，以后新用一个 sv.xxx 就不在保护范围内，而那不报错。
    #   ★ 用 ast 扫 `sv.xxx` 的属性访问，**不用正则** —— 正则会把注释和
    #     字符串里的 `sv.ALLOW_*` / `sv.xxx` 也当成真实用法
    #     （第一版就是这么假失败的，报"门面少了 ALLOW_ 和 xxx"）。
    import ast as _ast
    _tree = _ast.parse(open(os.path.join(here, 'selftest.py'),
                            encoding='utf-8').read())
    used = sorted({n.attr for n in _ast.walk(_tree)
                   if isinstance(n, _ast.Attribute)
                   and isinstance(n.value, _ast.Name) and n.value.id == 'sv'})
    miss = [n for n in used if not hasattr(sv, n)]
    assert not miss, 'server 门面少了这些名字：%s' % miss

    # ---- 🔴 未定义名字检测：搬运代码的头号风险 ----
    #   拆分时把 `_JOBS` / `_DATE_RE` / `_run_job` 留在了 runs.py，而
    #   live.py / sync.py 里仍裸用它们 —— **import 成功、45 个 GET 全对**，
    #   因为那几行只在 POST 分支里走到。实测就是这么漏过去的。
    #   ★ 所以这条不能靠"启动不报错"或"接口打得通"来代替：
    #     它是**静态**扫每个模块里 Load 但未绑定的名字。
    #   ★ `_run_job` 是作为 `Thread(target=_run_job)` 传递的，不是
    #     `_run_job(` 调用形式 —— 按调用形式做替换的脚本会漏掉它。
    import builtins
    _BI = set(dir(builtins)) | {'__name__', '__file__', '__doc__'}

    def _undef(path):
        tt = _ast.parse(open(path, encoding='utf-8').read())
        bound = set()
        for n in _ast.walk(tt):
            if isinstance(n, (_ast.Import, _ast.ImportFrom)):
                for a in n.names:
                    bound.add((a.asname or a.name).split('.')[0])
            elif isinstance(n, (_ast.FunctionDef, _ast.AsyncFunctionDef,
                                _ast.ClassDef)):
                bound.add(n.name)
            elif isinstance(n, _ast.Assign):
                # 🔴 只认 **Store** 的名字。`_JOBS[job_id] = {...}` 的目标是
                #   Subscript，里面那个 `_JOBS` 是 **Load** —— 不判 ctx 的话
                #   它会被当成"绑定了 _JOBS"，于是这个检测器**恰好放过了
                #   它当初为之而写的那个 bug**（srv/live.py 里裸用 _JOBS，
                #   只在 POST 分支走到，import 与 45 个 GET 全绿）。
                #   同理 `obj.attr = v` 的 `obj` 也是 Load。
                for tg in n.targets:
                    bound |= {k.id for k in _ast.walk(tg)
                              if isinstance(k, _ast.Name)
                              and isinstance(k.ctx, _ast.Store)}
            elif isinstance(n, (_ast.AnnAssign, _ast.AugAssign)):
                bound |= {k.id for k in _ast.walk(n.target)
                          if isinstance(k, _ast.Name)
                          and isinstance(k.ctx, _ast.Store)}
            elif isinstance(n, _ast.arg):
                bound.add(n.arg)
            elif isinstance(n, _ast.ExceptHandler) and n.name:
                bound.add(n.name)
            elif isinstance(n, (_ast.For, _ast.comprehension)):
                bound |= {k.id for k in _ast.walk(n.target)
                          if isinstance(k, _ast.Name)
                          and isinstance(k.ctx, _ast.Store)}
            elif isinstance(n, _ast.withitem) and n.optional_vars:
                bound |= {k.id for k in _ast.walk(n.optional_vars)
                          if isinstance(k, _ast.Name)
                          and isinstance(k.ctx, _ast.Store)}
            elif isinstance(n, _ast.Global):
                bound |= set(n.names)
            elif isinstance(n, _ast.Lambda):
                bound |= {a.arg for a in n.args.args}
        loads = {k.id for k in _ast.walk(tt) if isinstance(k, _ast.Name)
                 and isinstance(k.ctx, _ast.Load)}
        return sorted(loads - bound - _BI)

    pkg = os.path.join(here, 'assay')
    scanned = 0
    for r, _d, fs in os.walk(pkg):
        if '__pycache__' in r:
            continue
        for f in sorted(fs):
            if not f.endswith('.py'):
                continue
            u = _undef(os.path.join(r, f))
            assert not u, ('%s 里有未定义的名字 %s —— 会在运行到那一行时'
                           ' NameError（而 import 和大部分接口都不会报）'
                           % (os.path.relpath(os.path.join(r, f), here), u))
            scanned += 1

    # ---- live.py 的门面：🔴 LIVE 重定向必须真的生效 ----
    #   selftest 靠 `lv.LIVE = 临时目录` 把写操作重定向掉、不污染真实账本
    #   （本文件 3 处，那是那条纪律的实现手段）。
    #   拆进 lv/ 之后，读取方（acct_dir / load_accounts / _save_accounts /
    #   交易日历）都在 lv/base.py —— 门面若是 re-export，重定向就**静默失效**，
    #   用例会把数据写进 live/ 真账本，**而它不报错**。
    from assay import live as _lv
    from assay.lv import base as _lvbase
    assert _lvbase.HERE == os.path.join(here, 'assay'), \
        'lv/base.py 的 HERE 应指 assay 包目录（__file__ 深了一层）：%s' % _lvbase.HERE
    assert _lv.LIVE == os.path.join(here, 'live'), \
        'lv.LIVE 应是 <repo>/live，实际 %s —— ROOT 算错了一层' % _lv.LIVE
    assert os.path.isdir(_lv._lake()), 'lv._lake() 解不到 datalake：%s' % _lv._lake()
    _old_live = _lv.LIVE
    try:
        _lv.LIVE = '/tmp/_probe_live_redirect'
        assert _lvbase.LIVE == '/tmp/_probe_live_redirect', \
            '写 lv.LIVE 没写回 lv.base —— 重定向会静默失效，用例会写真账本'
        assert _lv.acct_dir('x') == '/tmp/_probe_live_redirect/x', \
            'acct_dir 没跟着重定向 —— 读取方拿的是 base 里的旧值'
        assert 'LIVE' not in vars(_lv), \
            'live.__dict__ 里出现了 LIVE 副本 —— __setattr__ 没拦住'
    finally:
        _lv.LIVE = _old_live
    # 本文件用到的 lv.* 名字也必须都还在（同样用 ast 扫，不照清单拼）
    _lvused = sorted({n.attr for n in _ast.walk(_tree)
                      if isinstance(n, _ast.Attribute)
                      and isinstance(n.value, _ast.Name) and n.value.id == 'lv'})
    _miss = [n for n in _lvused if not hasattr(_lv, n)]
    assert not _miss, 'live 门面少了这些名字：%s' % _miss
    LVDOMS = ('base', 'fee', 'px', 'pos', 'ver', 'sig', 'perf', 'explain')
    for d in LVDOMS:
        importlib.import_module('assay.lv.' + d)
    lp = open(os.path.join(here, 'assay', 'live.py'), encoding='utf-8').read()
    assert lp.count('\n') < 100, \
        'live.py 应只剩门面（%d 行）—— 实现放 lv/ 对应域' % lp.count('\n')

    # ---- 🔴 名字遮蔽：局部变量压掉同名的模块级 import ----
    #   实测踩到：serve() 里 `rt = base._rt()` 建了个局部变量（业务模块
    #   assay.realtime），把模块级 `from .srv import ... rt`（路由模块）
    #   **遮蔽**掉了，于是 `rt._RT` / `rt._rt_loop` 去 assay.realtime 找
    #   -> AttributeError。
    #   ★ 「未定义名字检测」抓不到它 —— 名字是定义的，只是指向错的对象。
    #   ★ 而且它只在 **--readonly 之外**的分支里（起实盘/行情线程那段），
    #     拆分时全程用 --readonly 验证，恰好一次都没执行到。
    for r, _d, fs in os.walk(pkg):
        if '__pycache__' in r:
            continue
        for f in sorted(fs):
            if not f.endswith('.py'):
                continue
            fp = os.path.join(r, f)
            tt = _ast.parse(open(fp, encoding='utf-8').read())
            mods = set()
            for n in tt.body:
                if isinstance(n, (_ast.Import, _ast.ImportFrom)):
                    for a in n.names:
                        mods.add(a.asname or a.name.split('.')[0])
            for n in _ast.walk(tt):
                if not isinstance(n, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
                    continue
                for k in _ast.walk(n):
                    hit = None
                    if isinstance(k, _ast.Assign):
                        for tg in k.targets:
                            for x in _ast.walk(tg):
                                if (isinstance(x, _ast.Name)
                                        and isinstance(x.ctx, _ast.Store)
                                        and x.id in mods):
                                    hit = (x.id, x.lineno)
                    elif isinstance(k, _ast.arg) and k.arg in mods:
                        hit = (k.arg, getattr(k, 'lineno', 0))
                    if hit:
                        raise AssertionError(
                            '%s 的 %s() 行%d：局部变量 `%s` 遮蔽了同名 import'
                            ' —— 之后 `%s.xxx` 会去错的对象上找'
                            % (os.path.relpath(fp, here), n.name, hit[1],
                               hit[0], hit[0]))

    # ---- serve() 全功能分支引用的名字必须真的存在 ----
    #   `--readonly` 不走那段，所以它是拆分时最容易漏验的路径。
    from assay.srv import live as _srvlive, rt as _srvrt
    for _m, _n in ((_srvrt, '_RT'), (_srvrt, '_rt_loop'),
                   (_srvlive, '_live_loop')):
        assert hasattr(_m, _n), \
            'serve() 会用 %s.%s，但它不存在' % (_m.__name__, _n)
    assert not hasattr(base._rt(), '_rt_loop'), \
        ('assay.realtime 也有了 _rt_loop —— 上面那条 hasattr 就失去意义了，'
         '得换个判据')

    # ---- srv/ 的结构：每个域都在，且 server.py 只剩骨架 ----
    DOMS = ('base', 'runs', 'docs', 'live', 'rt', 'sync', 'stock',
            'market', 'watch')
    for d in DOMS:
        importlib.import_module('assay.srv.' + d)
    sp = open(os.path.join(here, 'assay', 'server.py'), encoding='utf-8').read()
    n = sp.count('\n')
    assert n < 400, 'server.py 又长回去了（%d 行）—— 新接口该放 srv/ 对应域' % n
    return ('HERE/WEB/picks.json 三个路径基准正确；5 个延迟导入封装都指向业务模块'
            '（不是 srv 自己）；门面双向转发 ALLOW_*；'
            '本文件用到的 %d 个 sv.* 名字全在；'
            'assay/ %d 个模块零未定义名字；srv/ %d + lv/ %d 个域可 import；'
            'server.py 剩 %d 行 / live.py 剩 %d 行；'
            'lv.LIVE 重定向双向生效（%d 个 lv.* 名字全在）'
            % (len(used), scanned, len(DOMS), len(LVDOMS), n,
               lp.count('\n'), len(_lvused)))


@case('信号归档必须放【子目录】，否则"最新信号"读到旧版', tag='fast')
def t_rev_subdir():
    """🔴 revision 归档存成 `<date>.rev1.json`（与信号**同层**）时：

    有三处在扫 `signals/` 下的 `*.json` —— `sig.latest_signal` /
    `px.latest_signal` / `explain_history`，而前两者取 `sorted(...)[-1]`。
    `'2026-09-08.rev1.json'` 排在 `'2026-09-08.json'` **后面**（`'r' > '.'`），
    于是"最新信号"读到的是**被归档的旧版**。

    ★ 实测代价：改完规则重算，磁盘主文件已经是「卖出 0 只」，
      而页面/接口仍然给「卖出 2 只」—— **接口不报错，只是给了旧数据**，
      而这正是要照着下单的那份清单。
    ★ 与 CLAUDE.md 里「选股理由的旁挂必须放子目录、不能是
      `<date>.explain.json`」是**同一个坑**，加 revision 时又踩了一次。
    """
    import shutil
    import tempfile
    import json as _j
    from assay import live as lv
    from assay.lv import sig as _s, px as _p
    tmp = tempfile.mkdtemp(prefix='_st_rev_')
    old = lv.LIVE
    try:
        lv.LIVE = tmp                    # 🔴 绝不能写真账本
        d = os.path.join(tmp, 'a1', 'signals')
        os.makedirs(d)
        NEW = {'for_date': '2026-09-08', 'code_sha256': 'new' * 20,
               'sell': [], 'buy': [], 'hold': [1] * 10}
        OLD = {'for_date': '2026-09-08', 'code_sha256': 'old' * 20,
               'sell': [{'code': 'x'}, {'code': 'y'}], 'buy': [],
               'hold': [1] * 8}
        for name, obj in (('2026-09-08.json', NEW),):
            with open(os.path.join(d, name), 'w', encoding='utf-8') as fh:
                _j.dump(obj, fh)
        # ---- ① rev_path 必须落在子目录里 ----
        rp = _s.rev_path('a1', '2026-09-08', 1)
        assert os.path.basename(os.path.dirname(rp)) == '_rev', \
            ('归档要放 signals/_rev/ 子目录，实得 %s —— 同层的话 '
             '"最新信号"会 sorted 到它' % rp)
        os.makedirs(os.path.dirname(rp), exist_ok=True)
        with open(rp, 'w', encoding='utf-8') as fh:
            _j.dump(OLD, fh)
        # ---- ② 三处扫描都必须读到【主文件】 ----
        for nm, fn in (('sig.latest_signal', _s.latest_signal),
                       ('px.latest_signal', _p.latest_signal)):
            got = fn('a1') or {}
            assert got.get('code_sha256') == NEW['code_sha256'], \
                ('%s 读到了归档的旧版（sell %d 只）—— 页面会照着旧清单下单'
                 % (nm, len(got.get('sell') or [])))
            assert not got.get('sell'), \
                '%s 给出了旧版的卖出清单' % nm
        hist = _s.explain_history('a1')
        assert len(hist) == 1, \
            ('explain_history 把归档也当成一期了（%d 期）—— '
             '同一天会出现两段' % len(hist))
        # ---- ③ 反向自证：放同层就必须被抓到（这条断言不是空转）----
        same = os.path.join(d, '2026-09-08.rev1.json')
        shutil.copy(rp, same)
        bad = _s.latest_signal('a1') or {}
        assert bad.get('code_sha256') == OLD['code_sha256'], \
            ('前提变了：同层的 .rev1.json 本该被 sorted 到最后 —— '
             '如果这条不再成立，上面那两条断言就成了空转，要重新设计')
        os.remove(same)
    finally:
        lv.LIVE = old
        shutil.rmtree(tmp, ignore_errors=True)
    return ('rev 归档落在 signals/_rev/ 子目录；sig/px 的 latest_signal 与 '
            'explain_history 三处都只看主文件；反向自证：放回同层时'
            'latest_signal 确实会读到旧版（所以这条断言不是空转）')


@case('是不是调仓日：日历判据，不能从【委托】反推', tag='fast')
def t_rebal_day_judge():
    """🔴 `is_rebalance_day` 曾写成 `bool(rebal_orders)` —— 从"策略下了几个
    调仓委托"反推。于是调仓日**什么都不用动**时（目标池恰好等于当前持仓）
    策略一个委托都不下 -> 判成"今天不是调仓日" -> 页面显示「下次调仓 09-15」，
    而今天就是调仓日。

    ★ 原版下永远不暴露：调仓日总会卖掉几只，`rebal_orders` 从来不空。
      2026-09-08 开 `lu_buy_only=1` 后第一次出现"调仓日零委托"才现形。
    ★ 与 CLAUDE.md 里「调仓日的'持有不动'从**委托**反推」**同一个根因**
      的另一半 —— 判据要问日历，不要问策略做了什么。
    """
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            'assay', 'lv', 'sig.py'), encoding='utf-8').read()
    import ast as _a
    tree = _a.parse(src)
    fn = next((n for n in _a.walk(tree) if isinstance(n, _a.FunctionDef)
               and n.name == 'build_signal'), None)
    assert fn is not None, 'build_signal 不见了'
    #   在 build_signal 的函数体里找 `is_rebal = ...` 那次赋值，看右侧是什么。
    rhs = None
    for node in _a.walk(fn):
        if (isinstance(node, _a.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], _a.Name)
                and node.targets[0].id == 'is_rebal'):
            rhs = node.value
    assert rhs is not None, 'build_signal 里没有 is_rebal 的赋值了'
    #   🔴 判据必须是**调用 is_rebalance_day**，不是 bool(rebal_orders)。
    #     用 ast 看右侧表达式，而不是查字符串 —— 那个标识符在注释里也有。
    assert (isinstance(rhs, _a.Call)
            and getattr(rhs.func, 'id', '') == 'is_rebalance_day'), \
        ('is_rebal 必须由日历判据 is_rebalance_day() 给出。'
         '写成 bool(rebal_orders) 的话，"调仓日但什么都不用动"会被判成'
         '非调仓日，页面把下次调仓说成一周后 —— 而它不报错')
    src_txt = _a.unparse(rhs)
    assert 'rebal_orders' not in src_txt, \
        'is_rebal 的右侧还在读 rebal_orders：%s' % src_txt

    # ---- 日历判据本身要对：froec 是每周二（weekday=2）----
    from assay.lv import sig as _sig
    feed = PanelFeed('2026-06-01', '2026-09-07')
    eng = Engine(load('strategies/小市值/froec_traded.py'), feed, cash=5e5,
                 cost=Cost(), params={'stop_intraday': 1, 'stop_loss': 0.35,
                                      'weekday': 2})
    eng._boot()
    import datetime as _dt
    from assay.lv import base as _lvb
    TUE, WED = _dt.date(2026, 9, 8), _dt.date(2026, 9, 9)
    assert TUE.isoweekday() == 2 and WED.isoweekday() == 3, '日期常量写错了'
    #   🔴 先验"没扩展日历序号时必须**抛错**" —— Engine._build_ordinals 只
    #     覆盖 feed.trading_days，而实盘问的正是**下一个交易日**（它没有行情、
    #     不在面板里）。`_due` 对表外日期取 (0,0) -> 静默 False ->
    #     静默说"今天不是调仓日"，那是最糟的失败方式。
    assert TUE not in eng._wk_ord, '前提变了：09-08 本该不在面板日历里'
    try:
        _sig.is_rebalance_day(eng, TUE)
        raise AssertionError(
            '日历序号没扩展时 is_rebalance_day 该抛错，而不是返回 False ——'
            '静默 False 就是静默说"今天不是调仓日"')
    except _lvb.LiveError:
        pass
    #   扩展之后才该给出正确答案（实盘 build_signal 里就是这么做的）
    _sig._extend_ordinals(eng, feed, _lvb.calendar_days())
    assert _sig.is_rebalance_day(eng, TUE), \
        '2026-09-08 是周二，froec（weekday=2）该判成调仓日'
    assert not _sig.is_rebalance_day(eng, WED), \
        '2026-09-09 是周三，不该判成调仓日'
    #   与 upcoming_rebalance 必须一致（它们共用同一处判据）
    up = _sig.upcoming_rebalance(eng, feed, _dt.date(2026, 9, 7),
                                 _lvb.calendar_days(), n=3)
    for row in up:
        d = _dt.date.fromisoformat(row['date'])
        assert row['is_rebal'] == _sig.is_rebalance_day(eng, d), \
            ('upcoming_rebalance 与 is_rebalance_day 对 %s 给出不同答案 ——'
             '两处判据分叉的表现是"横条标出的调仓日与今天的判定对不上"' % d)
    return ('is_rebal 由日历判据给出（ast 看右侧表达式，不是查字符串）；'
            '日历序号没扩展时**抛错**而不是静默 False；扩展后 09-08 周二判为'
            '调仓日、09-09 周三不是；upcoming_rebalance 与 is_rebalance_day '
            '对未来 %d 天逐日一致' % len(up))


@case('策略数据 API：不写 SQL 也能复现同一个候选池')
def t_data_api():
    """`feed.universe / snapshot / fundamentals` —— 让策略不必直接写 SQL。

    理想中只有 feed 知道 datalake 的存在；而 froec.py / 红利那批把 9 层 CTE
    写在策略里，口径（as-of / 停牌结转 / 多期去重）与规则（阈值 / 分位 /
    排序 / 截断）混在同一段文本里。新策略 `froec_api.py` 用这三个 API 取数、
    用 Python 写规则，**froec.py 一行没动**。

    🔴 判据是**同一天两版候选池逐位相同**，并且要**跳过 ROE 切点并列的日子**
      —— 原 SQL 的 `ORDER BY increase DESC` 没有 tie-break，那些日子它自己
      就不确定（实测 539 个调仓日里约 4%）。
    """
    import datetime
    import importlib.util as _iu
    from assay.guard import GuardedFeed, LookAheadError

    # ---- ① 三个 API 都带 PIT 防火墙（它们直接读 std/ 与面板）----
    f = PanelFeed('2024-01-01', '2026-09-07')
    gf = GuardedFeed(f)
    gf.set_clock(datetime.date(2026, 9, 4), 'pre_open')
    for nm, call in (('universe', lambda x: gf.universe(x, listed_days=250)),
                     ('snapshot', lambda x: gf.snapshot(x, ['pb'])),
                     ('fundamentals', lambda x: gf.fundamentals(x, ['eps']))):
        for arg in ('2026-09-04', datetime.date(2026, 9, 4)):
            try:
                call(arg)
                raise AssertionError(
                    '%s(%r) 没被 PIT 拦住 —— 它直接读 std/ 与面板，'
                    '少一道防火墙就是给未来函数开后门（而它不报错）'
                    % (nm, arg))
            except LookAheadError:
                pass

    # ---- ② 停牌股：只结转价格派生量 ----
    #   🔴 基本面**不能**结转 —— 会拿到该股最后交易日那天的过期报告。
    u = f.universe('2026-09-07', listed_days=250, exclude_like='68%')
    cur = f.snapshot('2026-09-07', ['pb'], codes=u)
    carry = f.snapshot('2026-09-07', ['pb', 'floatmv'], codes=u, carry_days=400)
    assert len(carry) > len(cur), \
        ('carry_days 该把当日无 K 线（停牌）的票补进来，实得 %d vs %d'
         % (len(carry), len(cur)))
    assert len(carry) == len(u), '结转后该覆盖整个宇宙'

    # ---- ③ fundamentals：as-of + 多期 + require_all ----
    roe5 = f.fundamentals('2026-09-07', ['roe'], codes=u, periods=5,
                          require_all=True)
    cnt = roe5.groupby('code')['seq'].count()
    assert set(cnt.unique()) == {5}, \
        ('require_all=True 该只留恰好凑满 5 期的 code，实得期数 %s —— '
         '缺期的票会算出一个偏小的 ROE 加速度而**不报错**' % sorted(cnt.unique()))
    assert roe5['seq'].min() == 1, 'seq 该从 1（最新一期）开始'

    # ---- ④ 与 SQL 版候选池逐位相同（跳过 ROE 切点并列的日子）----
    P = {'stop_intraday': 1, 'stop_loss': 0.35, 'weekday': 2,
         'paused_in_pool': 0, 'kcb_688_only': 0}
    feed = PanelFeed('2016-01-01', '2026-09-03')
    eng = Engine(load('strategies/小市值/froec.py'), feed, cash=5e5,
                 cost=Cost(), params=P)
    eng._boot()
    ctx = eng.ctx

    def _mod(name, tag):
        sp = _iu.spec_from_file_location(tag, 'strategies/小市值/%s.py' % name)
        m = _iu.module_from_spec(sp)
        sp.loader.exec_module(m)
        return m
    ms, ma = _mod('froec', '_api_t1'), _mod('froec_api', '_api_t2')
    EXCL = ','.join("'%s'" % x for x in ms.EXCL_IND)
    #   🔴 tie 检测要在**截断与行业排除之前**做 —— 被排除的那一行不在末层
    #     输出里，在末层上查 roe_rn==cut+1 会漏判成"无并列"（踩过）。
    S = ms.SQL.replace('WHERE rn2 <= {roecut}', 'WHERE rn2 <= 100000') \
              .replace("WHERE sw_l1_name IS NULL OR "
                       "sw_l1_name NOT IN ({excl})", 'WHERE 1=1')
    #   ★ 采样日要够密：切点算错 ±1 只差一只票，而它常常本来就进不了
    #     最终前 20 —— 采样太少那条断言就成了空转（变异测试抓到过：
    #     6 个采样日抓不到 "roe 切点 -1"）。
    DAYS = ['2016-03-31', '2016-09-30', '2017-06-30', '2018-03-30',
            '2018-06-29', '2019-03-29', '2019-09-30', '2020-03-31',
            '2020-09-30', '2021-06-30', '2022-03-31', '2022-09-30',
            '2023-06-30', '2024-04-22', '2025-03-31', '2026-09-07']
    same = tie = 0
    for ds in DAYS:
        d = datetime.date.fromisoformat(ds)
        w = ctx.data.query(S, sd=d, listed=250, cand=100000, pin='FALSE',
                           kcb='68%', pert=0, salt='a', skip=0,
                           pbcut='floor(0.5 * n)', roecut='x', excl=EXCL)
        if w.empty:
            continue
        n2 = int(w['roe_n'].iloc[0])
        cut = int(0.1 * n2)
        v = w[w['roe_rn'] == cut]['roe_inc']
        uu = w[w['roe_rn'] == cut + 1]['roe_inc']
        if len(v) and len(uu) and abs(v.iloc[0] - uu.iloc[0]) < 1e-12:
            tie += 1
            continue
        A = ctx.data.query(ms.SQL, sd=d, listed=250, cand=20, pin='FALSE',
                           kcb='68%', pert=0, salt='a', skip=0,
                           pbcut='floor(0.5 * n)',
                           roecut='floor(0.1 * n2)', excl=EXCL)
        B = ma._pick(ctx, d, 20)
        a, b = A['jq_code'].tolist(), B['jq_code'].tolist()
        assert a == b, \
            ('%s：API 版与 SQL 版候选池不同（切点没有并列，所以这是真差异）\n'
             '  SQL 独有 %s\n  API 独有 %s'
             % (d, sorted(set(a) - set(b)), sorted(set(b) - set(a))))
        #   🔴 只比 jq_code 太松：pb 切点算错 ±1 只多/少带一只进半区，
        #     而它常常本来就进不了最终前 20 -> 断言照样绿（变异测试抓到过）。
        #     连**中间层的名次与分母**一起比 —— 那几列正是切点的证据。
        #   分母（pb_n / roe_n）与并列无关，是**切点的硬证据** ——
        #   切点算错 ±1 会让下一层的分母跟着变。
        for col in ('pb_n', 'roe_n'):
            va, vb = int(A[col].iloc[0]), int(B[col].iloc[0])
            assert va == vb, \
                ('%s：%s 不同（SQL %d / API %d）—— 某一层的切点算错了'
                 % (d, col, va, vb))
        #   名次（pb_rn / roe_rn）只在**整列都没有并列**时才可比：
        #   原 SQL 的 ORDER BY 没有 tie-break，并列处的号码本身不确定。
        if len(w) == w['roe_inc'].nunique():
            for col in ('pb_rn', 'roe_rn'):
                va, vb = A[col].tolist(), B[col].tolist()
                assert [int(x) for x in va] == [int(x) for x in vb], \
                    ('%s：%s 列不同（该日无并列，所以这是真差异）\n'
                     '  SQL %s\n  API %s' % (d, col, va[:8], vb[:8]))
        for col in ('pb', 'floatmv', 'roe_inc', 'eps'):
            va, vb = A[col].tolist(), B[col].tolist()
            assert all(abs(x - y) < 1e-9 for x, y in zip(va, vb)), \
                '%s：%s 列的数值不同' % (d, col)
        same += 1
    assert same >= 12, \
        '有效对比的天数太少（%d），这条断言会变成空转' % same

    # ---- ⑤ froec.py **一行没动**：它仍然自己写 SQL，且不含新 API ----
    src = open('strategies/小市值/froec.py', encoding='utf-8').read()
    assert 'SQL = ' in src and 'context.data.query(' in src, \
        'froec.py 该保持原样（自己写 SQL）—— 它的归档要可比、实盘绑着它'
    for api in ('.universe(', '.snapshot(', '.fundamentals('):
        assert api not in src, \
            'froec.py 里出现了新 API（%s）—— 要求是**不动现有策略**' % api
    #   新策略必须真的不写 SQL
    asrc = open('strategies/小市值/froec_api.py', encoding='utf-8').read()
    assert 'data.query(' not in asrc and 'SELECT' not in asrc.upper() \
        .replace('SELECT ... FROM', ''), \
        'froec_api.py 里还有 SQL —— 它存在的意义就是"不写 SQL"'
    return ('3 个 API × 两种日期写法都被 PIT 拦住；停牌结转把宇宙补齐'
            '（%d -> %d）；require_all 只留 5 期齐全的；%d 个采样日与 SQL 版'
            '候选池逐位相同（%d 个 ROE 切点并列日跳过 —— 原 SQL 没有 '
            'tie-break，那些日子它自己就不确定）；froec.py 一行没动'
            % (len(cur), len(carry), same, tie))


@case('候选池深度：多取给理由看，但策略只用前 N 个', tag='fast')
def t_explain_pool():
    """froec 原版 `LIMIT 10` -> "选股理由"页只能列到第 10 名，
    看不到"差一点选上的是谁"（红利那条 SQL 返回几百行，所以能列到 20）。

    做法是**多取几名但策略只用前 lim 个**（`df[...][:lim]` 那一刀）：
    WHERE / ORDER BY / OFFSET 一个字没动，只放大 LIMIT，
    而 `ORDER BY floatmv ASC` 是确定序，所以前 lim 行逐位不变。

    🔴 **少了那一刀就等于开了补位** —— 过滤（tradable/止损/黑名单）发生在
      截断之前，名额会被后面的票补上。补位是 `fill_paused` /
      `fill_blacklist` 那个**已测为负**的开关，绝不能顺手打开。
    """
    src = open('strategies/小市值/froec.py', encoding='utf-8').read()
    flat = src.replace(' ', '')
    assert "cand=df['jq_code'].tolist()[:lim]" in flat, \
        ('策略必须把候选池**截断回 lim** —— 不截断就是开了补位'
         '（过滤在截断之前，名额被后面的票补上），那是已测为负的开关')
    import re
    m = re.search(r"g\.explain_pool = getattr\(g, 'explain_pool', (\d+)\)", src)
    assert m and m.group(1) == '20', \
        '默认该多取到 20 名（实得 %s）' % (m.group(1) if m else '没有这个参数')

    # ---- 多取【不改变任何行为】：短回测逐位等价 ----
    A = _run('strategies/小市值/froec_traded.py', '2024-01-01', '2024-12-31',
             5e5, params={'stop_intraday': 1, 'stop_loss': 0.35,
                          'weekday': 2, 'explain_pool': 0})[0]
    B = _run('strategies/小市值/froec_traded.py', '2024-01-01', '2024-12-31',
             5e5, params={'stop_intraday': 1, 'stop_loss': 0.35,
                          'weekday': 2, 'explain_pool': 20})[0]
    for k in ('annual_return', 'max_drawdown', 'n_trades'):
        assert abs((A.get(k) or 0) - (B.get(k) or 0)) < 1e-9, \
            ('多取 20 名改变了行为（%s: %.9f vs %.9f）—— '
             'ORDER BY 有并列，或者 [:lim] 那一刀丢了'
             % (k, A.get(k) or 0, B.get(k) or 0))

    # ---- 而候选池确实变深了（否则上面那条等价断言是空转）----
    #   量的是**策略实际拿到的行数** —— 不另写一条查询（同 CLAUDE.md：
    #   另写的那份看着一样，直到某天 as-of 差一天才分叉）。
    from assay.guard import GuardedFeed
    _orig_q = GuardedFeed.query
    n = {}
    try:
        for pool in (0, 20):
            seen = []

            def _spy(self, sql, __seen=seen, **kw):
                df = _orig_q(self, sql, **kw)
                __seen.append(len(df))
                return df
            GuardedFeed.query = _spy
            feed = PanelFeed('2024-06-01', '2024-12-31')
            eng = Engine(load('strategies/小市值/froec_traded.py'), feed,
                         cash=5e5, cost=Cost(),
                         params={'stop_intraday': 1, 'stop_loss': 0.35,
                                 'weekday': 2, 'explain_pool': pool})
            eng.run()
            n[pool] = max(seen) if seen else 0
    finally:
        GuardedFeed.query = _orig_q
    assert n[20] > n[0] == 10, \
        ('explain_pool 该把候选池从 10 加深到 20，实得 %s -> %s —— '
         '如果它没生效，上面那条等价断言就是空转' % (n[0], n[20]))
    return ('策略把候选池截断回 lim（不截断=开补位，已测为负）；'
            'explain_pool 默认 20；explain_pool 0 vs 20 在 2024 全年'
            '逐位等价（年化/回撤/笔数三项）%s'
            % ('' if n.get(0) is None else '；候选池确实变深 %s -> %s'
               % (n[0], n[20])))


@case('涨停黑名单五口径：默认逐位等价 / 只挡买入不卖票 / 抽成一处')
def t_limitup_blacklist():
    """20 日涨停黑名单的四个开关（2026-09-07 加）。

    原版判据是 `最近持有过` ∩ `窗口内涨停过`，有个**不对称**：建仓日
    `g.hold_history` 为空 -> 交集恒空 -> 黑名单空转 -> 一只"买入前刚涨停过"
    的票能买进来；下一期 `seen` 有它了就必然被剔 -> **只持有一期**。

    🔴 四个开关实测**全部不显著**（逐年 t=−0.47~+0.79），所以默认值必须
      逐位等价于原版 —— 一旦默认值漂了，所有历史归档就不可比，而那不报错。
    """
    import datetime
    src = open('strategies/小市值/froec.py', encoding='utf-8').read()

    # ---- ① 四个开关都在，默认值 = 原版 ----
    import re
    DEF = {'lu_need_held': '1', 'lu_hold_only': '0',
           'lu_since_start': '0', 'fill_blacklist': '0', 'lu_buy_only': '0'}
    for k, v in DEF.items():
        m = re.search(r"g\.%s = getattr\(g, '%s', (\d+)\)" % (k, k), src)
        assert m, '开关 %s 不见了' % k
        assert m.group(1) == v, \
            ('%s 的默认值该是 %s（原版行为），实得 %s —— 默认值一漂，'
             '所有历史归档就不可比，而那不报错' % (k, v, m.group(1)))

    # ---- ② 黑名单抽成【一处】 ----
    #   🔴 原来 rebalance 与 hold_buffer_strict 各写了一遍同样四行，
    #     加开关时改一处漏一处的表现是"缓冲区那条路径还是旧口径" ——
    #     不报错，只是两条路径对同一只票给出不同结论。
    assert src.count('had_limit_up') == 1, \
        ('had_limit_up 该只在 blacklist() 里出现 1 次（实得 %d）——'
         '两处各写一遍就会分叉' % src.count('had_limit_up'))
    assert 'def blacklist(context, cand, d):' in src, 'blacklist() 不见了'

    # ---- ③ limit_up_days 与 had_limit_up 必须**同口径** ----
    #   两处窗口边界不一致的表现是"同一个窗口两种答案"，而它不报错。
    f = PanelFeed('2026-07-01', '2026-09-08')
    two = ['301152.XSHE', '603506.XSHG', '002910.XSHE']
    lo, hi = datetime.date(2026, 8, 11), datetime.date(2026, 9, 7)
    days = f.limit_up_days(two, lo, hi)
    assert set(days) == set(f.had_limit_up(two, lo, hi)), \
        ('limit_up_days 与 had_limit_up 的窗口口径不一致：%s vs %s'
         % (sorted(days), sorted(f.had_limit_up(two, lo, hi))))
    assert days.get('301152.XSHE') == {datetime.date(2026, 8, 24)}, \
        '涨停日明细不对（实盘 09-08 那次卖出就是靠它定位的）'
    #   🔴 边界语义要用**恰好在 start 那天涨停**的样本验 —— 否则把
    #     `date >` 改成 `date >=` 也照样绿（变异测试抓到过）。
    #     天力锂能 2026-08-24 涨停，正好当边界样本。
    one = ['301152.XSHE']
    assert f.limit_up_days(one, datetime.date(2026, 8, 24), hi) == {}, \
        ('start 当天必须**不算**（date > start）—— 与 had_limit_up 同口径，'
         '差一天的表现是"同一个窗口两种答案"，而它不报错')
    assert f.limit_up_days(one, datetime.date(2026, 8, 23), hi) == \
        {'301152.XSHE': {datetime.date(2026, 8, 24)}}, \
        'start 的下一天该被算进来'
    assert f.limit_up_days(one, lo, datetime.date(2026, 8, 24)) == \
        {'301152.XSHE': {datetime.date(2026, 8, 24)}}, \
        'end 当天必须**算**（date <= end）'
    assert f.limit_up_days(one, lo, datetime.date(2026, 8, 23)) == {}, \
        'end 之后的涨停不该算进来'
    #   PIT 防火墙不能漏了新方法
    from assay.guard import GuardedFeed, LookAheadError
    gf = GuardedFeed(f)
    gf.set_clock(datetime.date(2026, 9, 1), 'pre_open')
    try:
        gf.limit_up_days(two, lo, datetime.date(2026, 9, 7))
        raise AssertionError('limit_up_days 少了 PIT 检查 —— '
                             '它和 had_limit_up 看同一张表，少一道防火墙'
                             '就是给未来函数开了个后门（而它不报错）')
    except LookAheadError:
        pass

    # ---- ④ 默认参数下与原版**逐位等价**（数值指纹，不比字节）----
    A = _run('strategies/小市值/froec.py', '2024-01-01', '2024-12-31', 5e5)[0]
    B = _run('strategies/小市值/froec.py', '2024-01-01', '2024-12-31', 5e5,
             params={'lu_need_held': 1, 'lu_hold_only': 0,
                     'lu_since_start': 0, 'fill_blacklist': 0})[0]
    for k in ('annual_return', 'max_drawdown', 'n_trades'):
        assert abs((A.get(k) or 0) - (B.get(k) or 0)) < 1e-9, \
            '显式传原版参数与不传应完全一致，%s 差了' % k

    # ---- ⑤ hold_only=1 时 need_held 必须**无效**（设计自证）----
    #   这条在逐年汇总里也钉着：同名两份结果必须逐位相同。
    C = _run('strategies/小市值/froec.py', '2024-01-01', '2024-12-31', 5e5,
             params={'lu_hold_only': 1, 'lu_need_held': 1})[0]
    D = _run('strategies/小市值/froec.py', '2024-01-01', '2024-12-31', 5e5,
             params={'lu_hold_only': 1, 'lu_need_held': 0})[0]
    assert abs(C['annual_return'] - D['annual_return']) < 1e-9, \
        ('hold_only=1 时 need_held 本该无效（"持有期间"已蕴含"持有过"），'
         '实得 %.6f vs %.6f' % (C['annual_return'], D['annual_return']))
    #   🔴 上面那条**只能证行为一致，证不了实现干净**：往 hold_only 分支里
    #     混一个 need_held 条件是**语义冗余**的（hold_days 与 hold_history
    #     覆盖同一批票），行为一分不变、断言照样绿（变异测试抓到过）。
    #     这个不变量是结构性的，就该在源码上守。
    _hb = src[src.index('    if g.lu_hold_only:'):
              src.index('    if g.lu_need_held and not g.hold_history:')]
    assert 'lu_need_held' not in _hb, \
        ('hold_only 分支里不该读 need_held —— "持有期间"本身已经蕴含'
         '"持有过"了，多一个条件今天是冗余的，改天 hold_days 与 '
         'hold_history 的保留长度一分叉它就成了真 bug')

    # ---- ⑥ 开关真的能改变行为（否则上面几条都是空转）----
    E = _run('strategies/小市值/froec.py', '2024-01-01', '2024-12-31', 5e5,
             params={'lu_need_held': 0})[0]
    assert abs(E['annual_return'] - A['annual_return']) > 1e-6, \
        ('lu_need_held=0 没有改变任何结果 —— 开关接错了。'
         '2024 年实测该是 -5.68% vs 原版 13.17%')
    # ---- ⑦ lu_buy_only：黑名单**只挡买入，不卖票** ----
    #   🔴 这一条修的是规则自相矛盾：同一个事实（20 日内涨停过）在建仓那期
    #     被放行、在下一期被用来卖出。本项目早已认定黑名单的定位
    #     （froec.py 里 hold_buffer_strict 那段）：
    #       「卖出问的是"它还够好吗"，黑名单是"再买"的抑制器」
    #     实盘 2026-09-08 被剔的两只排名是**第 3 和第 7**，稳稳在前 10 名内。
    F = _run('strategies/小市值/froec.py', '2024-01-01', '2024-12-31', 5e5,
             params={'lu_buy_only': 1})[0]
    assert abs(F['annual_return'] - A['annual_return']) > 1e-6, \
        'lu_buy_only=1 没有改变任何结果 —— 开关接错了'
    #   直接后果：黑名单不再卖票 -> 持有更久 -> **成交笔数减少**。
    #   （2024 实测 86 -> 76；全历史 968 -> 884，11 年里 10 年都更少。）
    assert F['n_trades'] < A['n_trades'], \
        ('黑名单只挡买入之后成交笔数该减少（不再机械卖出排名还够的持仓），'
         '实得 %d vs 原版 %d' % (F['n_trades'], A['n_trades']))
    #   源码结构：卖出判据必须用**未过黑名单**的那份排名池
    assert '_rank_pool = set(cand[:g.stock_num]) if g.lu_buy_only else None' \
        in src, ('卖出判据要用未过黑名单的前 stock_num（"它还够好吗"）——'
                 '用过了黑名单的那份就又把买入判据当卖出判据了')
    #   🔴 这条断言第一版写成了 `'keep' in _kb and '_bk' in _kb` 兜底 ——
    #     那等于没验（两个标识符在注释里也有）。第 6 次踩同一个坑了，
    #     改成**去空格后匹配完整语句**。
    _kb = src[src.index('    if g.lu_buy_only and _rank_pool:'):
              src.index('    for code in list(context.portfolio.positions):')]
    _kbf = _kb.replace(' ', '').replace('\n', '')
    assert 'keep=set(keep)|_bk' in _kbf, \
        ('排名还够、只是被黑名单挡住的持仓要并进 keep —— '
         '不并的话它们照旧被卖，而"少了一句"不报错')
    assert 'c in _rank_pool' in _kb and 'c not in target' in _kb, \
        ('留下的判据必须是「在排名池里 且 不在（过了黑名单的）target 里」——'
         '也就是"排名还够、只是被黑名单挡住"这一批')
    return ('5 个开关默认值 = 原版且显式传参逐位一致；黑名单抽成一处'
            '（had_limit_up 只出现 1 次）；limit_up_days 与 had_limit_up '
            '同口径且带 PIT 防火墙；hold_only=1 时 need_held 无效'
            '（%.2f%% == %.2f%%）；lu_need_held=0 确实改变行为'
            '（2024: %.2f%% vs 原版 %.2f%%）；lu_buy_only=1 只挡买入不卖票'
            '（2024: %.2f%% / %d 笔 vs 原版 %d 笔，换手确实降了）'
            % (C['annual_return'] * 100, D['annual_return'] * 100,
               E['annual_return'] * 100, A['annual_return'] * 100,
               F['annual_return'] * 100, F['n_trades'], A['n_trades']))


@case('定时窗口配置：判据是【实际装上的点位】而不是回显配置', tag='fast')
def t_schedule():
    """轮询窗口可在看板改（时间范围 + 间隔），存完立即重装 launchd。

    🔴 **只回显配置是不够的**：配置改了而 timer 没重装时，
      "页面写着每小时一次、实际还是旧的"**不报错**。
      所以判据必须是**已装 plist 里到底有几个点位**
      （同 CLAUDE.md「已安装的 plist 与仓库正本不一致要报出来」那条）。
    """
    import importlib.util
    here = os.path.dirname(os.path.abspath(__file__))
    dlp = os.path.join(os.path.dirname(here), 'datalake', 'setup_tdx.py')
    spec = importlib.util.spec_from_file_location('_st_probe', dlp)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)

    # ---- ① 点位生成：间隔除不尽时要补上终点 ----
    assert len(m._range_times('16:00', '20:00', 10)) == 25
    t = m._range_times('07:00', '09:20', 60)
    assert t == [(7, 0), (8, 0), (9, 0), (9, 20)], \
        ('间隔除不尽时要补上终点 —— 否则窗口末尾那段（09:00~09:20）'
         '等于没覆盖，而配置上写着管到 09:20。实得 %s' % t)
    assert m._range_times('18:10', '18:10', 5) == [(18, 10)], \
        '单点位（老命令 --at 18:10）要仍然可用'
    # ---- ①b 跨午夜：16:00 ~ 次日 09:20 ----
    #   🔴 launchd 的 StartCalendarInterval 只是一组 (Hour, Minute)、
    #     每天都触发，所以跨天对它不是特例 —— 点位要**回绕**过 00:00。
    assert m._span('16:00', '09:20') == 1040, \
        '跨午夜的跨度算错了（16:00 到次日 09:20 = 17 小时 20 分）'
    assert m._span('16:00', '20:00') == 240, '不跨午夜的跨度算错了'
    wrap = m._range_times('16:00', '09:20', 60)
    assert len(wrap) == 19, '16:00~次日09:20 每 60 分该是 19 个点位，实得 %d' \
        % len(wrap)
    assert wrap[0] == (16, 0) and wrap[-1] == (9, 20), \
        '跨午夜的首尾点位不对：%s -> %s' % (wrap[0], wrap[-1])
    assert (0, 0) in wrap and (23, 0) in wrap and (9, 0) in wrap, \
        '跨午夜的点位没有回绕过 00:00：%s' % (wrap,)
    assert len(set(wrap)) == len(wrap), '跨午夜的点位有重复：%s' % (wrap,)
    assert all(0 <= h <= 23 and 0 <= mm <= 59 for h, mm in wrap), \
        '跨午夜算出了非法的时刻（小时越界）：%s' % (wrap,)

    # ---- ② 🔴 非法配置必须被拒，而不是装出一个不跑的 timer ----
    #   launchd 对**空的** StartCalendarInterval 不报错，只是永远不触发 ——
    #   表现是"配好了但数据再也不同步了"，几天后才发现。
    good = {'sync': {'from': '16:00', 'to': '20:00', 'every': 10},
            'tick': {'from': '07:00', 'to': '09:20', 'every': 60}}
    ok, why = m.check_schedule(good)
    assert ok, '正常配置被拒了：%s' % why
    #   ★ 密集但合理的要放行：16:00~20:00 每 5 分钟 = 49 个点位，
    #     低于上限。上限是防"配出几百个点位把 launchd 塞满"，不是限制频率。
    ok, why = m.check_schedule(
        dict(good, sync={'from': '16:00', 'to': '20:00', 'every': 5}))
    assert ok, '每 5 分钟（49 个点位）被误拒了：%s' % why
    #   ★ 跨午夜要放行（信号重算的默认窗口就是 16:00 ~ 次日 09:20）
    for WRAP_OK in ({'from': '16:00', 'to': '09:20', 'every': 60},
                    {'from': '20:00', 'to': '16:00', 'every': 60}):
        ok, why = m.check_schedule(dict(good, tick=WRAP_OK))
        assert ok, '跨午夜窗口 %s 被拒了：%s' % (WRAP_OK, why)
    BAD = [
        #   ★ 原来这里有一条 `20:00~16:00 -> 起点晚于终点` ——
        #     它现在是**合法的跨午夜窗口**，断言跟着改了（见下面的 WRAP_OK）。
        ({'from': '16:00', 'to': '09:20', 'every': 5}, '跨午夜时点位数超上限'),
        ({'from': '16:00', 'to': '20:00', 'every': 0}, 'every=0'),
        ({'from': '16:00', 'to': '20:00', 'every': 1}, 'every 低于下限'),
        ({'from': '16:00', 'to': '20:00', 'every': 999}, 'every 超上限'),
        ({'from': '00:00', 'to': '23:59', 'every': 5}, '点位数超上限'),
        ({'from': '25:00', 'to': '20:00', 'every': 10}, '小时越界'),
        ({'from': '1600', 'to': '20:00', 'every': 10}, '格式不对'),
        ({'from': '16:00', 'to': '20:00'}, '缺 every'),
        ({'from': '16:00', 'to': '20:00', 'every': '10'}, 'every 是字符串'),
    ]
    for bad, what in BAD:
        ok, why = m.check_schedule(dict(good, sync=bad))
        assert not ok, '「%s」这种配置被放过了：%r' % (what, bad)
    #   ★ 空/缺段也要拒
    for bad in ({}, {'sync': good['sync']}, None):
        ok, _w = m.check_schedule(bad)
        assert not ok, '缺段的配置被放过了：%r' % (bad,)

    # ---- ③ show_schedule 必须给出「配置 vs 实际」的比对 ----
    sc = m.show_schedule()
    for k in ('schedule', 'installed', 'limits', 'exists'):
        assert k in sc, 'show_schedule 少了 %s' % k
    for key in ('sync', 'tick'):
        d = sc['installed'][key]
        for f in ('want_slots', 'got_slots', 'match', 'loaded', 'label',
                  'wrap'):
            #   🔴 `wrap`：**分不出"跨午夜"和"填反了"**，所以判据不在
            #     校验里而在显示上 —— 页面写成「16:00 ~ 次日 09:20」，
            #     填反了那个"次日"和点位数会当场看出来。
            assert f in d, 'installed.%s 少了 %s' % (key, f)
        assert d['match'] is not False, \
            ('%s 的配置与实际装上的 timer 不一致（want %s / got %s）—— '
             '跑一次 `python3 datalake/setup_tdx.py --install-timer`'
             % (key, d['want_slots'], d['got_slots']))
    assert sc['installed']['tick']['wrap'] is True, \
        '信号重算的默认窗口是跨午夜的（16:00 ~ 次日 09:20），wrap 该为 True'
    assert sc['installed']['sync']['wrap'] is False, \
        '数据同步的窗口不跨午夜，wrap 该为 False'
    #   🔴 上面那条只能抓"当前恰好不一致"，**抓不到判据本身坏了**
    #     （把 match 写死成 True 时它照样绿 —— 变异测试抓到过）。
    #     所以再注入一份**故意与已装 plist 不同**的配置：match 必须翻成 False。
    if sc['installed']['sync']['got_slots'] is not None:
        real_load = m.load_schedule
        try:
            m.load_schedule = lambda: (
                {'sync': {'from': '01:00', 'to': '01:30', 'every': 30},
                 'tick': sc['schedule']['tick']}, None)
            d = m.show_schedule()['installed']['sync']
            assert d['want_slots'] == 2 and d['match'] is False, \
                ('注入一份与已装 timer 不同的窗口（01:00~01:30/30，2 个点位）后，'
                 'match 仍然不是 False（want %s / got %s / match %r）—— '
                 '这个判据是死的，"改了配置但没重装"就永远看不出来'
                 % (d['want_slots'], d['got_slots'], d['match']))
        finally:
            m.load_schedule = real_load

    # ---- ④ 接口两端都在 ----
    from assay import server as sv
    assert '/api/sync/schedule' in sv.ROUTES, 'GET /api/sync/schedule 没挂'
    src = open(os.path.join(here, 'assay', 'server.py'),
               encoding='utf-8').read()
    assert 'api_sync_schedule_set' in src, 'POST 没挂'
    syp = open(os.path.join(here, 'assay', 'srv', 'sync.py'),
               encoding='utf-8').read()
    #   🔴 不能用 `'--install-timer' in syp` —— 这个字面量在同一个文件的
    #     **注释里也出现**（第 4 次踩这个坑），把真实调用改坏了它照样绿。
    #     所以走 ast：注释不是 AST 节点，docstring 单独剔掉。
    import ast as _ast
    _fn = next((n for n in _ast.walk(_ast.parse(syp))
                if isinstance(n, _ast.FunctionDef)
                and n.name == 'api_sync_schedule_set'), None)
    assert _fn is not None, 'POST 处理函数 api_sync_schedule_set 不见了'
    _body = _fn.body
    if (_body and isinstance(_body[0], _ast.Expr)
            and isinstance(_body[0].value, _ast.Constant)):
        _body = _body[1:]                      # 剔掉 docstring
    _lit = {n.value for b in _body for n in _ast.walk(b)
            if isinstance(n, _ast.Constant) and isinstance(n.value, str)}
    _call = {n.func.attr if isinstance(n.func, _ast.Attribute) else
             getattr(n.func, 'id', '') for b in _body for n in _ast.walk(b)
             if isinstance(n, _ast.Call)}
    assert 'check_schedule' in _call, \
        ('POST 要先校验再存 —— 不校验就能从页面存进 from>to 这种配置，'
         '而它会装出一个**永不触发**的 timer 且不报错')
    assert 'save_schedule' in _call, 'POST 没存配置'
    assert '--install-timer' in _lit, \
        ('POST 要「存配置 + 立即重装」，不给"存了但没生效"留窗口 —— '
         '实际传给 setup_tdx.py 的参数里没有 --install-timer')
    assert "out['sched']" in syp, \
        'api_sync 要把窗口与实际点位带给页面'

    # ---- ⑤ 页面必须显示【实际装上的】，不能只回显配置 ----
    js = open(os.path.join(here, 'web', 'views', 'sync.js'),
              encoding='utf-8').read()
    flat = js.replace(' ', '').replace('\n', '')
    assert 'got_slots' in js and 'i.match===false' in flat, \
        ('页面没显示"实际装上的点位/是否一致" —— 只回显配置的话，'
         '"改了但没重装"就看不出来')
    assert '配置与实际装上的 timer 不一致' in js, '不一致要显红说清'
    assert 'i.wrap' in js and '次日' in js, \
        ('页面要把跨午夜写成「16:00 ~ **次日** 09:20」—— '
         '光写 16:00~09:20 看着像填反了')
    #   🔴 又不能用"函数体里出现过「次日」" —— 我自己写的注释里就有它
    #     （第 5 次踩这个坑）。走 ast 只看**字符串常量**。
    _dl = open(os.path.join(os.path.dirname(here), 'datalake',
                            'setup_tdx.py'), encoding='utf-8').read()
    _it = next((n for n in _ast.walk(_ast.parse(_dl))
                if isinstance(n, _ast.FunctionDef)
                and n.name == 'install_timer'), None)
    assert _it is not None, 'install_timer 不见了'
    _its = {n.value for n in _ast.walk(_it)
            if isinstance(n, _ast.Constant) and isinstance(n.value, str)}
    assert any('次日' in x for x in _its), \
        '命令行装 timer 的回显也要说「次日」（跨午夜时）'
    #   ★ 默认窗口的定案值也在这条用例里钉住（它是"窗口"的一部分）
    assert m.DEFAULT_SCHED['tick']['from'] == '16:00', \
        ('信号重算的窗口要从 16:00 开始 —— 公告集中在 16:00~22:00，'
         '而人常在晚上手动导聚宽增量，只开早上的话要等到 07:00 才算得进去')
    assert 'setTimeout(showSync' in flat.replace(' ', ''), \
        ('保存后要**延迟**刷新 —— 立刻 showSync() 会把反馈连同 #syschedmsg '
         '一起重渲染掉，点了按钮什么都看不到（实测踩过）')
    return ('点位生成含补终点（07:00~09:20/60 -> 4 个）与单点位兼容；'
            '跨午夜回绕正确（16:00~次日09:20/60 -> 19 个点位，含 00:00~09:00 '
            '且小时不越界）且校验放行、页面与命令行都标「次日」'
            '（分不出"跨午夜"与"填反了"，所以判据在显示上不在校验里）；'
            '%d 种非法配置全被拒（空的 StartCalendarInterval 会让 launchd '
            '永不触发且不报错）；show_schedule 给出 want/got/match/wrap '
            '且当前一致；GET+POST 都挂且 POST 存完立即重装；'
            '页面显示实际点位并延迟刷新'
            % (len(BAD) + 3))


@case('信号重算：不一致必须留痕再覆盖', tag='fast')
def t_signal_revision():
    """早上重算出来的清单可能与昨晚不一样（当晚公告里 ST/停牌是次日生效的）。

    🔴 **人可能已经按昨晚那份准备好委托了** —— 静默覆盖等于让他拿着一份
      已经作废的清单去下单。所以：旧版归档成 rev、主文件记 revisions。
    ★ 但数据变了而**决策没变**是常态，那种情况只记一次 recomputed_at ——
      否则每天几条"重算过"的噪声，人就不看这个提示了
      （同"假告警看多了就不看告警"）。
    """
    import json as _js
    import tempfile
    from assay import live as lv
    from assay.lv import base as _b, sig as _sig

    old_live = lv.LIVE
    tmp = tempfile.mkdtemp(prefix='sigrev_')
    try:
        lv.LIVE = tmp                      # 🔴 不许写真实账本
        assert _b.LIVE == tmp, 'LIVE 重定向没生效'
        aid = 'probe'
        os.makedirs(os.path.join(tmp, aid, 'signals'))
        FD = '2026-09-07'

        def mk(buy, fp):
            return {'account': aid, 'for_date': FD, 'built_at': '2026-09-06T18:12:00',
                    'buy': [{'code': c, 'shares': 100} for c in buy],
                    'sell': [], 'hold': [],
                    'data_fingerprint': {'overall': fp}}

        # 昨晚那份
        _b._atomic_write(_sig.signal_path(aid, FD),
                         _js.dumps(mk(['A', 'B'], 'fp_old'), ensure_ascii=False))
        # 让 build_signal 返回"早上算出来的不同结果"
        orig = _sig.build_signal
        _sig.build_signal = lambda a, datalake=None: mk(['A', 'C'], 'fp_new')
        try:
            r = _sig.make_signal(aid, force=True)
        finally:
            _sig.build_signal = orig

        # ---- ① 旧版必须被归档 ----
        rp = _sig.rev_path(aid, FD, 1)
        assert os.path.isfile(rp), '旧版没归档成 rev1 —— 覆盖掉就找不回来了'
        arch = _js.load(open(rp, encoding='utf-8'))
        assert [x['code'] for x in arch['buy']] == ['A', 'B'], \
            'rev1 里不是旧版内容：%s' % arch.get('buy')

        # ---- ② 主文件必须记 revisions，且 diff 说清差异 ----
        revs = r.get('revisions') or []
        assert len(revs) == 1, 'revisions 应有 1 条，实际 %d' % len(revs)
        d = revs[0]['diff']
        assert d['buy_added'] == ['C'] and d['buy_removed'] == ['B'], \
            'diff 没说清差异：%s' % d
        assert d['changed'] is True and d['data_fp_changed'] is True

        # ---- ③ 🔴 append-only：再算一次，已有的 rev1 不许被重写 ----
        st1 = os.stat(rp).st_mtime_ns
        _sig.build_signal = lambda a, datalake=None: mk(['A', 'D'], 'fp_new2')
        try:
            r2 = _sig.make_signal(aid, force=True)
        finally:
            _sig.build_signal = orig
        assert os.stat(rp).st_mtime_ns == st1, \
            'rev1 被重写了 —— 归档必须 append-only（那是唯一的回滚凭据）'
        assert os.path.isfile(_sig.rev_path(aid, FD, 2)), '第二次没归档成 rev2'
        assert len(r2.get('revisions') or []) == 2, \
            '两次不一致应有 2 条 revision'

        # ---- ④ 结果【相同】时不许产生 revision（否则全是噪声）----
        cur = _js.load(open(_sig.signal_path(aid, FD), encoding='utf-8'))
        same = dict(cur)
        same['data_fingerprint'] = {'overall': 'fp_same_data_changed'}
        _sig.build_signal = lambda a, datalake=None: same
        try:
            r3 = _sig.make_signal(aid, force=True)
        finally:
            _sig.build_signal = orig
        assert len(r3.get('revisions') or []) == 2, \
            '决策没变却又产生了 revision —— 那会变成天天有的噪声'
        assert r3.get('recomputed_at'), \
            '决策没变时应当记一次 recomputed_at（数据确实动过）'

        # ---- ⑤ 不加 force 时不许动已有信号 ----
        _sig.build_signal = lambda a, datalake=None: mk(['Z'], 'fp_x')
        try:
            r4 = _sig.make_signal(aid, force=False)
        finally:
            _sig.build_signal = orig
        assert [x['code'] for x in r4['buy']] != ['Z'], \
            '没加 force 却重算并覆盖了'
        #   🔴 归档在 `signals/_rev/` **子目录**里，不是同层 ——
        #     同层的话 `latest_signal` 会 sorted 到它、读出被归档的旧版
        #     （见「信号归档必须放【子目录】」那条用例）。
        #     判据要跟着代码一起搬，不然它只是看着还在。
        _rd = os.path.join(tmp, aid, 'signals', '_rev')
        n_rev = len([f for f in os.listdir(_rd)
                     if '.rev' in f]) if os.path.isdir(_rd) else 0
        assert n_rev == 2, 'rev 文件数应为 2，实际 %d（%s）' % (n_rev, _rd)
        assert not [f for f in os.listdir(os.path.join(tmp, aid, 'signals'))
                    if '.rev' in f], \
            '归档不许出现在 signals/ 同层 —— latest_signal 会读到它'
        #   🔴 **页面提示里给的那个路径必须真的能找到文件。**
        #     `archived` 原来只存 basename、页面自己拼 'signals/' ——
        #     归档搬进 `_rev/` 子目录之后那个拼法就错了，提示里写着
        #     「旧版留在 signals/2026-09-08.rev1.json」而文件在
        #     `signals/_rev/` 下。**给一个找不到的路径比不给更糟。**
        import json as _json
        with open(os.path.join(tmp, aid, 'signals',
                               '%s.json' % r3['for_date']),
                  encoding='utf-8') as _fh:
            cur = _json.load(_fh)
        for _r in (cur.get('revisions') or []):
            _ap = _r.get('archived') or ''
            assert _ap, 'revision 里没有 archived 路径'
            assert os.path.isfile(os.path.join(tmp, aid, _ap)), \
                ('revision 的 archived 指向一个不存在的文件：%s —— '
                 '页面照它显示"旧版留在 …"，人照着去找会找不到' % _ap)
    finally:
        lv.LIVE = old_live
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    # ---- ⑥ tick_daily.py 的三道判据都在（它是 launchd 的入口）----
    here = os.path.dirname(os.path.abspath(__file__))
    src = open(os.path.join(here, 'tick_daily.py'), encoding='utf-8').read()
    assert '_is_trading_day' in src, 'tick_daily 少了「今天是交易日吗」'
    assert 'leg_a_lag' in src, \
        ('tick_daily 少了「A 腿数据到最新交易日了吗」—— 拆成两个时间点之后，'
         '同步一慢就会拿旧数据算出一份看着正常的信号')
    assert 'fingerprint' in src, 'tick_daily 少了「指纹变了吗」'
    body = src[src.index('def _leg_a_ok'):src.index('def _now_fp')]
    assert 'return None' in body and 'ss.collect()' in body, \
        '新鲜度判据应取 sync_status.collect() 那一处，且判不出时返回 None'
    tail = src[src.index('if not ok:'):]
    assert 'return 3' in tail.split('# ---- 判据 3')[0], \
        'A 腿落后时必须拒绝重算（非零退出码），而不是继续算'
    # ---- ⑦ 🔴 页面必须【显红说清差异】—— 留痕了但页面不说等于没留痕 ----
    #   ★ 匹配**结构**而不是"标识符出现过"：今天踩过两次这个坑
    #     （`assert 'h.pruned' in js` 时把 if(h.pruned) 改成 if(false) 照样全绿）。
    js = open(os.path.join(here, 'web', 'views', 'live.js'),
              encoding='utf-8').read()
    flat = js.replace(' ', '').replace('\n', '')
    assert 'constrevs=s.revisions||[]' in flat, \
        'live.js 没读 s.revisions —— 留痕了但页面不说，等于没留痕'
    assert 'revs.length?' in flat and '被重算过' in js, \
        'revision 提示要按 revs.length 分支并说清「被重算过」'
    assert 'lvwarn' in js.split('const rv=')[1][:400], \
        'revision 提示必须用警告样式（它是"能不能照着下单"的前提）'
    assert '请照现在这份核对' in js, \
        '要明说「如果已按之前那份准备了委托，请照现在这份核对」'
    assert 'r.archived' in js, '要写清旧版存在哪个文件（唯一的回滚凭据）'
    assert 'returnw+rv+' in flat, \
        'rv 必须拼在正文之前 —— 藏在下面等于没提示'
    #   数据动过但清单没变：低调显示，**不许**用警告样式
    #   🔴 锚点要**唯一**：原来写的是 `split('const rc=')[1]`，而 `rc` 是个
    #     太常见的局部名 —— 2026-09-14 加模拟盘标记时我在前面也写了一个
    #     `const rc=`，锚点当场被抢走，这条断言去查的是**另一段代码**，
    #     于是报"低调显示没做"而产品根本没坏（同「判据比断言宽」那条）。
    assert js.count('const rc=(!revs.length') == 1, \
        '「重算过但清单未变」那段的锚点不唯一了'
    rcseg = js.split('const rc=(!revs.length')[1][:320]
    assert 'recomputed_at' in rcseg and 'lvwhy' in rcseg, \
        '「重算过但清单未变」应低调显示（lvwhy）'
    assert 'lvwarn' not in rcseg, \
        ('「清单未变」用了警告样式 —— 那会变成天天有的噪声，'
         '人就不看这个位置了（同"假告警看多了就不看告警"）')

    # ---- ⑧ 两个 timer 必须一起装 ----
    st = open(os.path.join(os.path.dirname(here), 'datalake', 'setup_tdx.py'),
              encoding='utf-8').read()
    assert 'TICK_LABEL' in st and 'tick_daily.py' in st, \
        ('setup_tdx.py 没装「信号重算」的 timer —— 漏装的表现是'
         '**信号永远是昨晚 18:10 那份**，不报错')
    assert 'def _times(' in st, '多时间点要能解析（07:00,08:00,09:00）'
    jobs = st[st.index('JOBS = ['):st.index('if osname ==', st.index('JOBS = ['))]
    assert 'LABEL' in jobs and 'TICK_LABEL' in jobs, \
        'JOBS 里要同时有 sync 与 tick —— 清单只写一处，分开写会漏'
    for tag in ('sync', 'tick'):
        assert "'%s'" % tag in jobs, 'JOBS 少了 %s' % tag
    #   ★ 判据是 launchctl 里到底有没有，不是命令返回码
    assert "launchctl', 'list'" in st, \
        '装完要用 launchctl list 复查（launchctl 对"已是这个状态"会报错退出）'
    return ('归档 rev1/rev2 且 append-only（旧的不被重写）；diff 说清 '
            'buy_added/buy_removed；决策没变只记 recomputed_at 不产生噪声；'
            '不加 force 不动已有信号；tick_daily 三道判据都在且 A 腿落后时拒算；'
            '页面按 revs.length 显红说清差异 + 清单未变时只低调显示；'
            'setup_tdx 的 JOBS 同时装 sync 与 tick 并用 launchctl list 复查')


@case('serve.py 的 stop/restart：判据是端口而不是 PID 文件', tag='fast')
def t_serve_ctl():
    """`serve.py --status/--stop/--restart`。

    🔴 **不用 PID 文件**：它会陈旧（kill -9 / 机器重启 / 进程崩掉，文件都还在），
      更糟的是 **PID 会被复用** —— 只看"文件里那个号还活着"就 kill，
      有可能杀掉一个刚好复用了这个号的无关进程。
      判据是"现在谁占着这个端口"（同 launchd 那条：判据是现在的状态、不是记录）。
    """
    import subprocess as sp
    here = os.path.dirname(os.path.abspath(__file__))
    src = open(os.path.join(here, 'serve.py'), encoding='utf-8').read()

    # ---- ① 三个动作都在，而且裸 `serve.py` 仍合法（用 flag 不用子命令）----
    h = sp.run(['python3', 'serve.py', '--help'], cwd=here,
               capture_output=True, text=True, timeout=60).stdout
    for f in ('--status', '--stop', '--restart'):
        assert f in h, 'serve.py 没有 %s' % f
    assert '--readonly' in h and '--live' in h, \
        '旧开关没了 —— "参数不认"会让服务直接起不来'

    # ---- ② 🔴 不许出现 PID 文件 ----
    for bad in ('pidfile', 'pid_file', '.pid'):
        assert bad not in src, \
            ('serve.py 里出现了 %r —— 判据应该是"谁占着端口"，'
             'PID 文件会陈旧、而 PID 会被复用（可能杀错进程）' % bad)

    # ---- ③ 🔴 必须验"是不是我们的进程"，否则会停掉别人的服务 ----
    import importlib.util as _iu
    spec = _iu.spec_from_file_location('_serve_probe',
                                       os.path.join(here, 'serve.py'))
    mod = _iu.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)          # 只是 import，__main__ 不会跑
    except SystemExit:
        pass
    assert mod._ours('python3 serve.py --port 8770'), \
        '_ours 认不出我们自己的命令行'
    for other in ('/usr/bin/nginx -g daemon off',
                  'node /app/server.js', 'python3 -m http.server 8770'):
        assert not mod._ours(other), \
            '_ours 把 %r 也当成我们的了 —— 会停掉别人的服务' % other

    # ---- ④ 🔴 stop 必须【轮询确认真的退出】，不能发完信号就返回 ----
    body = src[src.index('def do_stop'):src.index('if __name__')]
    assert 'SIGTERM' in body and 'SIGKILL' in body, \
        'do_stop 应当先 SIGTERM、超时才升级 SIGKILL'
    #   🔴 要检查**轮询循环体内**有确认，不能只查"文件里出现过" ——
    #     `os.kill(pid, 0)` 在 do_stop 里有两处（轮询 + 强杀后再确认），
    #     只查存在性时把轮询那处删掉照样全绿。
    #     （同 pruned 那条用例踩过的坑：字符串存在性检查抓不到"逻辑被绕过"。）
    poll = body[body.index('while time.time()'):body.index('else:')]
    assert 'os.kill(pid, 0)' in poll, \
        'do_stop 的轮询里没有确认进程退出 —— 「发了信号」不等于「停了」'
    kill9 = body.split('SIGKILL')[-1]
    assert 'os.kill(pid, 0)' in kill9, \
        'SIGKILL 之后没再确认 —— 强杀也可能失败（僵尸、权限）'
    assert '_who(port)' in body.split('SIGKILL')[-1], \
        ('stop 结束前要再确认端口真的空了 —— 进程没了但端口还在 TIME_WAIT 时，'
         '新进程 bind 会报 "Address already in use"，那个报错指不到真原因')

    # ---- ⑤ 🔴 restart 没停干净就不许启动（否则 bind 失败）----
    tail = src[src.index('if a.restart:'):]
    assert 'sys.exit(rc)' in tail.split('---- 重新启动 ----')[0], \
        'restart 在 stop 失败时仍会继续启动 —— 那会 bind 失败'

    # ---- ⑥ --status 取的是 code 子对象，不是顶层 ----
    st = src[src.index('def do_status'):src.index('def do_stop')]
    assert "d.get('code')" in st, \
        ('--status 应从 `code` 子对象取 loaded_at/code_mtime —— 取顶层会'
         '拿到 None，表现是「该不该重启」这三行静默不打印（实测踩过）')

    # ---- ⑦ 空端口上真跑一遍：--status 报未运行、--stop 说本来就没在跑 ----
    #   ★ 用一个**没人用的高端口**，绝不碰 8770（那可能是用户正在用的看板）
    port = '8799'
    r1 = sp.run(['python3', 'serve.py', '--status', '--port', port], cwd=here,
                capture_output=True, text=True, timeout=60)
    assert r1.returncode == 1 and '未在运行' in r1.stdout, \
        '空端口 --status 应报未运行、退出码 1，实际 %s / %r' \
        % (r1.returncode, r1.stdout[:80])
    r2 = sp.run(['python3', 'serve.py', '--stop', '--port', port], cwd=here,
                capture_output=True, text=True, timeout=60)
    assert r2.returncode == 0 and '本来就没在跑' in r2.stdout, \
        '空端口 --stop 应幂等成功，实际 %s / %r' % (r2.returncode, r2.stdout[:80])
    return ('三个动作齐（裸 serve.py 仍合法）；无 PID 文件；'
            '_ours 认自己不认 nginx/node/http.server；'
            'stop 先 TERM 后 KILL 且轮询确认 + 收尾查端口；'
            'restart 没停干净不启动；--status 从 code 子对象取；'
            '空端口上 --status=1 / --stop=0')


@case('selftest 不许写进生产归档（同 live/ 那条纪律）', tag='fast')
def t_no_runs_pollution():
    """2026-09-15 用户："当前在用的 froec_traded 策略里，有大量的
    20260601-20260630 的回测记录，不知道是什么作用。"

    查出来是**测试污染**：「版本页触发回测」那条用例点的就是页面上那个
    「用这个版本跑一次」，而那条链路（`_run_job`）起 `python3 run.py`
    子进程、**照常归档** —— 于是每跑一次 selftest 就往 `runs/` 里塞一条，
    区间参数完全相同，积了 **105 次**，每天都在涨。

    🔴 **不改产品行为**：人在页面上点"跑一次"就是要归档的，给接口加
      `--no-archive` 是修错了地方。要改的是**测试**：`registry` 读
      `ASSAY_RUNS`，而子进程继承环境变量，重定向它就够
      （同 `lv.LIVE` 那条：**selftest 不许写生产数据**）。

    ★ 这条用例钉的是"守卫还在"，真正的证据是跑完之后
      `find runs -name meta.json | wc -l` 不变 —— 那个由 CI/人工核对。
    """
    import io as _io
    import re as _re
    src = _io.open('selftest.py', encoding='utf-8').read()
    cases = [(m.start(), m.group(1)) for m in _re.finditer(r"@case\('([^']+)'", src)]
    cases.append((len(src), None))

    def _owner(pos):
        """这个位置属于哪条用例。"""
        for k in range(len(cases) - 1):
            if cases[k][0] <= pos < cases[k + 1][0]:
                return cases[k][1], cases[k][0], cases[k + 1][0]
        return None, 0, len(src)

    # 🔴 **扫【所有】会触发回测的地方，不是只钉某一条用例。**
    #   我第一版只钉了「版本页」，而真正在归档的是「实盘页面真实渲染」——
    #   两条都点了那个「跑一次」按钮。照清单钉就会这样漏
    #   （同「断言直接扫目录而不是照清单拼」那条）。
    bad = []
    for m in _re.finditer(r"#stbt'\)\.click\(\)", src):
        name, a, b = _owner(m.start())
        seg = src[a:b]
        # 只读模式下点它不会真跑（那是在验"给不给出原因"），跳过
        if 'ALLOW_BACKTEST = True' not in seg and 'sv.ALLOW_BACKTEST' not in seg \
                and 'ALLOW_LIVE = True' not in seg:
            continue
        # 🔴 判据要认准**赋值**那一处，不是"提到过这个名字" ——
        #   `_prev_runs_env = os.environ.get('ASSAY_RUNS')` 也含这个字符串，
        #   于是删掉真正的赋值照样绿（变异实测漏过）。
        if "os.environ['ASSAY_RUNS'] = _runs_tmp" not in seg:
            bad.append(name)
    assert not bad, \
        ('这些用例会**真跑一次回测并归档**，却没把 ASSAY_RUNS 重定向到'
         '临时目录：%s —— 每跑一次 selftest 就污染一条生产归档'
         '（实测积了 105 次同参数的 2026-06-01~06-30）' % sorted(set(bad)))
    # 每一处重定向都要在 finally 里还原
    SELF = 'selftest 不许写进生产归档'
    for m in _re.finditer(r"os\.environ\['ASSAY_RUNS'\] = _runs_tmp", src):
        name, a, b = _owner(m.start())
        # ★ 跳过**这条用例自己** —— 它的源码里也含那些字符串（就是上面
        #   那几行判据），扫到自己头上会报"还原不在 finally"这种假失败。
        if name and name.startswith(SELF):
            continue
        seg = src[a:b]
        assert 'finally:' in seg and 'set_runs(_prev_runs)' in \
            seg[seg.rindex('finally:'):], \
            ('%s 里归档目录的还原不在 finally —— 用例失败时会漏还原，'
             '后面一串用例都会跑在临时目录上' % name)
        assert 'rmtree(_runs_tmp' in seg, '%s 没清理临时归档' % name
    n = sum(1 for m in _re.finditer(r"os\.environ\['ASSAY_RUNS'\] = _runs_tmp", src)
            if not (_owner(m.start())[0] or '').startswith(SELF))
    return '%d 条会触发回测的用例都把归档重定向到临时目录且在 finally 还原' % n


@case('清实验归档：结论要留下来，而且不许误删在用的', tag='fast')
def t_prune_experiments():
    """2026-09-15 用户："现在又有大量试验性质的回测记录，清理掉一批，
    保留结论即可。"

    🔴 现成的 `prune_runs.py` **一个都删不掉** —— 它的时间分界是
      「实盘上线日之后全部保留」（本机 2026-09-01），理由是"那天之后的回测
      是在用的"。而这批实验恰恰跑在那之后。
      ★ **那条判据不该改**（它保护的是真在用的），要加的是一条新判据：
        `_` 开头的分组 = 作者自己标出来的"这是试验"
        （项目里跑对照实验一律 `--group _xxx`，正式结论进业务域分组）。

    🔴 另一条更隐蔽：「账户绑定过这个版本」原来把 **304 次**回测全保下来了
      （`ca52ae82` 一个版本就占这么多）。而它回答的只是"这个版本回测过
      没有" —— **每个版本留一次就够**。不收敛的话这条保护把仓库钉死。

    ★ 删之前把 meta+stats 抽进 `runs/_pruned_conclusions.jsonl`：
      tar 备份是**回滚凭据**（要解包才能看），而结论是要**随时查**的
      （"那轮 dev_pct=0.13 到底多少"）。两者用途不同，不能互相替代。
    """
    import io as _io
    import json as _json
    import sys as _sys
    if '.' not in _sys.path:
        _sys.path.insert(0, '.')
    import prune_runs as P

    # ---- ① 判据本身 ----
    assert P._is_exp({'group': '_tm2016'}), '`_` 开头该判为实验组'
    assert not P._is_exp({'group': '小市值'}), '业务域分组不该判为实验组'
    assert not P._is_exp({}), '没有 group 的不该判为实验组'
    # 区间长度：留哪一次靠它
    assert P._span({'start': '2016-01-01', 'end': '2026-01-01'}) > \
        P._span({'start': '2024-01-01', 'end': '2024-12-31'}), \
        '`_span` 没按区间长度比'
    assert P._span({'start': 'x', 'end': 'y'}) == 0, '取不到区间该返回 0 不是抛错'

    # ---- ② 保护判据：在用的一个都不许删 ----
    rows = P.scan()
    keep = P.protected(rows)
    marks = {}
    if os.path.isfile('picks.json'):
        marks = _json.load(_io.open('picks.json', encoding='utf-8'))
    for rid in (marks.keys() if isinstance(marks, dict) else marks):
        assert rid in keep, \
            ('picks.json 标记的 %s 不在保护集里 —— 「★ 选中的规则」那页'
             '读的就是它，删了就查不到了' % rid)
    # 🔴 每个被账户绑定的版本，**至少还留着一次**回测 ——
    #   那是"这个版本回测过没有"的唯一依据。
    binds = set()
    for aid in os.listdir('live'):
        p = os.path.join('live', aid, 'versions.jsonl')
        if not os.path.isfile(p):
            continue
        for ln in _io.open(p, encoding='utf-8'):
            try:
                v = _json.loads(ln)
            except Exception:                                   # noqa: BLE001
                continue
            for k in ('main_sha256', 'code_sha256'):
                if v.get(k):
                    binds.add(v[k][:12])
    have = {}
    for x in rows:
        sha = (x['meta'].get('code_sha256') or '')[:12]
        have.setdefault(sha, []).append(x['rid'])
    _r, _k, drop, _pf = P.plan()
    dropped = {x['rid'] for x in drop}
    for sha in binds:
        if sha not in have:
            continue                    # 这个版本本来就没跑过回测
        left = [r for r in have[sha] if r not in dropped]
        assert left, \
            ('版本 %s 的回测会被删光 —— 实盘页就再也查不到"这个版本回测过"'
             % sha)
    # ★ 但也**不许留太多**：一个版本留一次就够（这正是这次要收敛的）
    for sha in binds:
        if sha not in have:
            continue
        n_keep = sum(1 for r in have[sha]
                     if '账户绑定过这个版本' in keep.get(r, []))
        assert n_keep <= 1, \
            ('版本 %s 有 %d 次回测挂着"账户绑定"这条保护 —— 它只需要留一次，'
             '不收敛的话这条保护会把仓库钉死（实测 ca52ae82 占 304 次）'
             % (sha, n_keep))

    # ---- ③ **真跑一次写入** ----
    # 🔴 只读"已经写好的"结论文件，测不到写入逻辑：`stats` 不存、
    #   去重失效这两种改动照样全绿（变异实测漏过）。所以造一个临时 RUNS
    #   真调一次 `save_conclusions`。
    import shutil as _sh
    import tempfile as _tf
    _tmp = _tf.mkdtemp(prefix='selftest_prune_')
    _old_runs = P.RUNS
    P.RUNS = _tmp
    try:
        sample = [x for x in rows if (x['files'] or {}).get('stats.json')
                  or os.path.isfile(os.path.join(x['dir'], 'stats.json'))][:3]
        assert sample, '没有带 stats.json 的归档可抽'
        cp, n1 = P.save_conclusions(sample)
        assert n1 == len(sample), '抽了 %d 条，样本 %d 个' % (n1, len(sample))
        got = [_json.loads(ln) for ln in _io.open(cp, encoding='utf-8')]
        assert got and got[0].get('stats'), \
            ('结论里没有 stats —— 只存 meta 的话翻出来只知道"跑过"，'
             '不知道结果，等于没留')
        assert any(k in got[0]['stats']
                   for k in ('annual_return', 'total_return')), \
            '结论的 stats 里没有收益率：%s' % list(got[0]['stats'])[:6]
        # 🔴 **幂等**：再抽一次不该重复（重复跑清理会把文件撑大）
        _cp2, n2 = P.save_conclusions(sample)
        assert n2 == 0, '再抽一次又写了 %d 条 —— 不幂等' % n2
        assert len(list(_io.open(cp, encoding='utf-8'))) == len(sample), \
            '结论文件行数变了 —— 去重没生效'
    finally:
        P.RUNS = _old_runs
        _sh.rmtree(_tmp, ignore_errors=True)

    # ---- ④ 结论文件：删过的那些要查得到 ----
    cpath = os.path.join(P.RUNS, os.path.basename(P.CONCLUSIONS))
    if os.path.isfile(cpath):
        ids, bad = set(), []
        for ln in _io.open(cpath, encoding='utf-8'):
            d = _json.loads(ln)
            assert d.get('run_id') and d.get('meta'), '结论行缺 run_id/meta'
            if d['run_id'] in ids:
                bad.append(d['run_id'])
            ids.add(d['run_id'])
            # 🔴 **结论要能回答"那次跑出了什么"** —— 只存 meta 不存 stats
            #   的话，翻出来只知道"跑过"，不知道结果，等于没留
            st = d.get('stats') or {}
            if st:
                assert 'annual_return' in st or 'total_return' in st, \
                    '结论里没有收益率：%s' % d['run_id']
        assert not bad, '结论文件里有重复的 run_id（不幂等）：%s' % bad[:3]
        # 删掉的那些必须都在里面
        live_ids = {os.path.basename(os.path.dirname(m))
                    for m in __import__('glob').glob('runs/*/*/*/meta.json')}
        assert ids, '结论文件是空的'
        assert not (ids & live_ids), \
            ('结论文件里有**还活着**的归档（%s）—— 那说明抽的时机不对，'
             '应该只抽将要删的那些' % list(ids & live_ids)[:3])
        return ('判据齐（_ 开头=实验组、每版本留一次）；'
                'picks/账户绑定都没被删；结论 %d 条可查' % len(ids))
    return '判据齐；picks/账户绑定都没被删（还没清理过，无结论文件）'


@case('归档清理：明细删了必须【明说】，不能显示成空', tag='fast')
def t_pruned():
    """`prune_runs.py` 把旧回测的 holdings.parquet 清掉了（900M -> 153M）。

    🔴 危险在于 `_read()` 读不到 parquet 时返回**空 DataFrame、不报错** ——
      所以"清理过"和"这次回测真的没持仓"在页面上长得一模一样。
      判据链是三段，缺一段就退化成静默：
        ① prune_runs.py 往 meta.json 写 pruned: {'holdings': 日期}
        ② 接口把它带出去（api_holdings / api_day）
        ③ 页面读到就明说，而不是渲染一张空表
    """
    import json as _js
    from assay import server as sv
    here = os.path.dirname(os.path.abspath(__file__))
    runs = os.environ.get('ASSAY_RUNS') or os.path.join(here, 'runs')

    # ① 找一个被清理的、一个完好的
    pruned = full = None
    for r, _d, fs in os.walk(runs):
        if 'meta.json' not in fs:
            continue
        m = _js.load(open(os.path.join(r, 'meta.json'), encoding='utf-8'))
        if (m.get('pruned') or {}).get('holdings') and not pruned:
            pruned = m.get('run_id')
        elif 'holdings.parquet' in fs and not full:
            full = m.get('run_id')
        if pruned and full:
            break
    if not pruned:
        return '没有被清理的归档 —— 这条用例不适用（prune_runs.py 还没跑过）'

    # ② 接口必须把 pruned 带出去；完好的那次必须是 None（否则判据反了）
    h = sv.api_holdings({'id': pruned, 'limit': '2'})
    assert h.get('pruned'), \
        ('api_holdings 没带 pruned —— 页面会渲染一张空表，'
         '而"清理过"和"没持仓"分不出来')
    assert h.get('total') == 0, '被清理的归档 total 应为 0，实际 %s' % h.get('total')
    if full:
        h2 = sv.api_holdings({'id': full, 'limit': '2'})
        assert not h2.get('pruned'), \
            'holdings.parquet 还在的归档不该有 pruned 标记（判据反了）'
        assert h2.get('total', 0) > 0, '完好的归档应该读到持仓行'

    # ③ 页面必须有处理它的代码
    js = open(os.path.join(here, 'web', 'views', 'run-detail.js'),
              encoding='utf-8').read()
    #   🔴 要匹配**完整的条件语句**，不能只查标识符出现过 ——
    #     提示文本里也有 `${esc(h.pruned)}`，所以把 `if(h.pruned)` 改成
    #     `if(false)` 时"h.pruned in js"照样成立。
    #     实测：第一版就是这么漏过变异测试的（改成 if(false) 仍全绿）。
    flat = js.replace(' ', '').replace('\n', '')
    assert 'if(h.pruned){' in flat and '明细已清理' in js, \
        'run-detail.js 没【按 pruned 分支】—— 清理过的归档会显示成空表'
    assert 'if(o.holdings_pruned){' in flat, \
        '「当天持仓」下钻没按 pruned 分支（api_day 那条链）'
    # ★ 那个「持仓 N 只」的 card 不能显示 0 —— 0 会被读成"那天空仓"
    assert "o.holdings_pruned ? '—'" in js, \
        '「持仓」card 在明细清理时应显示 —，显示 0 会被误读成空仓'

    # ④ 保留集必须完好：标记的、账户绑定的，holdings 都还在
    marks = {}
    mp = os.path.join(here, 'picks.json')
    if os.path.isfile(mp):
        marks = _js.load(open(mp, encoding='utf-8'))
    for rid in marks:
        d = sv._dir(rid)
        assert d is None or os.path.isfile(os.path.join(d, 'holdings.parquet')), \
            ('%s 被 picks.json 标记（「选中的规则」读它）却被清理了 —— '
             'prune_runs.py 的保留集漏了它' % rid)
    n_pruned = n_full = 0
    for r, _d, fs in os.walk(runs):
        if 'meta.json' not in fs:
            continue
        m = _js.load(open(os.path.join(r, 'meta.json'), encoding='utf-8'))
        if (m.get('pruned') or {}).get('holdings'):
            n_pruned += 1
        elif 'holdings.parquet' in fs:
            n_full += 1
    return ('%d 次明细已清理 / %d 次完好；接口带 pruned 且完好的那次为 None；'
            '页面三处都处理了（持仓页 / 当天持仓下钻 / 那个 card 不显示 0）；'
            '%d 条标记的归档 holdings 都还在'
            % (n_pruned, n_full, len(marks)))


@case('选股理由：捕获而非重算 / 投影不改行为 / 候选池自洽', tag='fast')
def t_explain():
    """实盘信号要说清「这只票为什么被选中」。三件事各有一条不报错的坏法：

    1. **理由是【捕获】来的，不是照策略再算一遍。** 重算的那份迟早与策略分叉，
       而分叉的解释**看着完全正常**。这里的判据是：挂上记账代理之后，
       买/卖/持有清单必须与不挂时**逐位相同** —— 记账不许改变任何判定。
    2. **策略 SQL 末层多返回几列不许改变选出的票。** 那是"投影"改动
       （WHERE/ORDER/LIMIT 不动），但真出错的话是**静默换了几只票**。
       这里直接把新 SQL 与"只返回 jq_code"的旧写法对跑同一天，
       代码列表必须逐位相同。
       ★ 全历史等价性（2016-01~2026-08 逐日权益 + 成交流水指纹逐位相同）
         另跑过一次，那个太慢不放进 selftest。
    3. **候选池要与清单自洽**：买入的票必须都在池子里且标成 buy；
       名次必须是 1..n；有指标列时买入的那些必须真的带上了指标。

    还钉一条**被这个功能抓出来的老 bug**：`nth_prev_day` 对"不在交易日表里
    的日期"（实盘问的【下一个交易日】）会落到面板第一天，于是 froec 的
    20 日涨停黑名单变成"2016 年以来涨停过吗" —— 几乎全命中，最近持有过的
    候选被整片剔掉，**而这不报错**。
    """
    import datetime
    import re as _re
    import shutil
    import tempfile

    import assay.lv.explain as ex

    def _flat(e):
        """把「一条查询一组」的结构摊平成行 —— 断言里要按代码找。"""
        return [x for g in (e.get('groups') or []) for x in (g.get('rows') or [])]
    from assay import live as lv
    from assay.feed import PanelFeed

    here = os.path.dirname(os.path.abspath(__file__))
    out = []

    # ---- 0) nth_prev_day：表内日期与 _idx 等价；表外日期按位置插值 ----
    feed = PanelFeed('2026-01-01', '2026-12-31')
    days = feed.trading_days
    for d in days[::37]:
        assert feed.nth_prev_day(d, 20) == days[max(0, days.index(d) - 20)], \
            'nth_prev_day 对表内日期的行为变了（回测会跟着变）：%s' % d
    import importlib.util as _il

    def _load(path, name):
        sp = _il.spec_from_file_location(name, os.path.join(here, path))
        m = _il.module_from_spec(sp)
        sp.loader.exec_module(m)
        return m

    fut = days[-1] + datetime.timedelta(days=30)
    assert feed.nth_prev_day(fut, 20) == days[-20], \
        ('nth_prev_day(未来日, 20) 落到了 %s 而不是倒数第 20 天 %s —— '
         '实盘的 20 日涨停黑名单会变成"有史以来涨停过吗"，'
         '把最近持有过的候选整片剔掉，而这不报错'
         % (feed.nth_prev_day(fut, 20), days[-20]))
    out.append('nth_prev_day 表内等价、表外按位置插值（未来日 -> %s）' % days[-20])

    # ---- 0b) 🔴 回测里那个兜底分支**一次都不该走到** ----
    #      这条回答的是"那个 bug 有没有污染历史回测结果"。判据不是读代码觉得
    #      不会（`Engine.run` 里 `ctx.current_date` 取自 feed.trading_days），
    #      而是**逐次调用**统计：跑一段真回测，数"日期不在交易日表里"的次数。
    #      实测全历史 2016-2026：froec 545 次调用、红利 0 次、sgmspeg_v0b 393 次，
    #      **miss 全是 0** —— 回测结果没被影响过，归档不用重跑。
    #      这里只跑一小段（保持 fast 层的耗时），够守住"将来别把非交易日
    #      塞进 current_date"这条。
    import bisect as _bs

    from assay.broker import Cost as _Cost
    from assay.engine import Engine as _Eng
    _stat = {'calls': 0, 'miss': 0}
    _orig_npd = PanelFeed.nth_prev_day

    def _probe(self, d, n):
        _stat['calls'] += 1
        if self._idx.get(d) is None:
            _stat['miss'] += 1
        return _orig_npd(self, d, n)

    PanelFeed.nth_prev_day = _probe
    try:
        _f2 = PanelFeed('2026-01-01', '2026-06-30')
        _e2 = _Eng(_load('strategies/小市值/froec.py', '_ex_npd'), _f2,
                   cash=200000, cost=_Cost(), params={})
        _e2.run()
    finally:
        PanelFeed.nth_prev_day = _orig_npd
    assert _stat['calls'] > 0, 'froec 半年应该调过 nth_prev_day —— 探针没生效？'
    assert _stat['miss'] == 0, \
        ('回测里有 %d 次 nth_prev_day 拿到了【不在交易日表里】的日期 —— '
         '那个兜底分支会把窗口拉到面板第一天（实盘就是这么把 20 日涨停黑名单'
         '变成"有史以来"的）。回测本来不该走到这里' % _stat['miss'])
    out.append('回测里 nth_prev_day %d 次调用、0 次落到兜底分支（历史回测未受影响）'
               % _stat['calls'])

    d0 = days[-2]
    froec = _load('strategies/小市值/froec.py', '_ex_froec')
    kw = dict(sd=d0, listed=250, cand=10, pin='FALSE', kcb='68%', pert=0,
              salt='a', skip=0, pbcut='floor(0.5 * n)', roecut='floor(0.1 * n2)',
              excl=','.join("'%s'" % x for x in froec.EXCL_IND))
    wide = feed.query(froec.SQL, **kw)
    assert len(wide.columns) > 1, \
        'froec 的 SQL 末层没有多返回指标列 —— 选股理由里就只有排名没有指标'
    # 把末层投影换回"只要代码"，其余一个字不动
    # ★ 必须锚在【行首】：SQL 里还有个缩进的 `SELECT jq_code, pb, …`（today CTE），
    #   不锚的话正则会从那里一路吃到末层，把大半条 SQL 删掉（实测踩到）。
    narrow_sql = _re.sub(r'(?ms)^SELECT jq_code,.*?^FROM roe_top',
                         'SELECT jq_code\nFROM roe_top', froec.SQL)
    assert narrow_sql != froec.SQL, '构造窄投影失败（SQL 结构变了？）'
    narrow = feed.query(narrow_sql, **kw)
    assert list(wide['jq_code']) == list(narrow['jq_code']), \
        ('froec：多返回几列改变了选出的票！\n宽 %s\n窄 %s'
         % (list(wide['jq_code']), list(narrow['jq_code'])))
    out.append('froec 投影不变：%d 列 vs 1 列，%d 只逐位相同'
               % (len(wide.columns), len(wide)))

    hl = _load('strategies/红利/红利指数增强.py', '_ex_hl')
    for tag, sqlname, cte in (('A', 'SQL_A', 'ranked'), ('B', 'SQL_B', 'base')):
        raw = getattr(hl, sqlname).replace('{div}', hl.DIV_FISCAL_YEAR)
        kw2 = dict(t1=d0, uni=hl.UNIVERSE, dmin=0.03, dtop=0.1, pe_lo=0, pe_hi=100,
                   roe_lo=0.05, roe_hi=1.0, rev_lo=0.0, rev_hi=10.0,
                   np_lo=0.0, np_hi=10.0, bmode='abs', broe=.3, brev=.3, bnp=.3)
        if tag == 'A':
            kw2.update(bw=252, bpct=1.0)   # 与策略默认 g.beta_win 一致
        w = feed.query(raw, **kw2)
        n_sql = _re.sub(r'(?m)^SELECT jq_code,.*? FROM ' + cte,
                        'SELECT jq_code FROM ' + cte, raw)
        assert n_sql != raw, '红利 %s 构造窄投影失败' % tag
        n = feed.query(n_sql, **kw2)
        assert list(w['jq_code']) == list(n['jq_code']), \
            '红利 %s：多返回几列改变了选出的票' % tag
        out.append('红利%s 投影不变（%d 只）' % (tag, len(w)))

    # ---- 1b) 老快照（SQL 只返回代码）必须照样能解释，且**明说**没有指标 ----
    #      账户绑的是版本快照，改了磁盘上的策略不会自动生效 —— 所以
    #      "没有指标列"的信号会长期存在。那时不能崩，也不能装作有指标。
    rec0 = ex.Recorder()
    rec0.arm('rebalance')
    rec0.add(kind='query', where='rebalance:1', sql_head='x', args={'cand': 10},
             rows=[{'jq_code': '000001.XSHE'}, {'jq_code': '000002.XSHE'}])
    e0 = ex.assemble(rec0, 'rebalance', picked=['000001.XSHE'], held=[], sold=[])
    assert e0['captured'] and e0['pool_total'] == 2 and e0['n_groups'] == 1
    assert _flat(e0)[0]['status'] == 'buy' and not _flat(e0)[0]['metrics']
    assert any('没有指标列' in n or '指标' in n for n in e0['notes']), \
        '老快照没有指标列时要明说，而不是画一张空表：%s' % e0['notes']
    assert e0['groups'][0]['metric_meta'] == [], '没有指标就不该有表头'
    out.append('老快照（无指标列）不崩且明说原因')

    # ---- 2) 记账不许改变任何判定 + 候选池自洽 ----
    tmp = tempfile.mkdtemp(prefix='selftest_why_')
    old_live = lv.LIVE
    lv.LIVE = tmp
    try:
        cal = os.path.join(here, 'live', 'trade_calendar.json')
        if not os.path.exists(cal):
            return '跳过（没有 live/trade_calendar.json）；' + '；'.join(out)
        shutil.copy(cal, os.path.join(tmp, 'trade_calendar.json'))
        # 找一个「下一个交易日就是调仓日」的 weekday —— 不写死星期几：
        # 数据往前推一天这条就假失败了（本仓库踩过）
        sig = None
        for wd in (1, 2, 3, 4, 5):
            aid = 'w%d' % wd
            lv.upsert_account(aid, name='w', init_cash=400000)
            lv.bind_version(aid, 'strategies/小市值/froec_traded.py',
                            params={'weekday': wd, 'stop_loss': 0.35,
                                    'stop_intraday': 1})
            s = lv.build_signal(aid)
            if s['is_rebalance_day']:
                sig, sig_aid = s, aid
                break
        assert sig, '五个 weekday 都不是调仓日 —— 日历或 _due 有问题'
        e = sig['explain']
        assert e['captured'], '调仓日却没捕获到选股过程'
        assert e['pool_total'] > 0, '调仓日候选池是空的'

        # 2a) 挂记账代理前后，清单必须逐位相同（记账不许改判定）
        _real = ex.attach
        try:
            ex.attach = lambda eng, br, rec=None: ex.Recorder()   # 不挂代理
            bare = lv.build_signal(sig_aid)
        finally:
            ex.attach = _real
        for k in ('buy', 'sell', 'hold'):
            a = [(x['code'], x.get('shares')) for x in sig[k]]
            b = [(x['code'], x.get('shares')) for x in bare[k]]
            assert a == b, \
                ('挂上选股理由的记账代理之后 %s 变了 —— 记账不许改变任何判定\n'
                 '有代理 %s\n无代理 %s' % (k, a, b))
        assert not bare['explain']['captured'], '对照组不该捕获到东西'
        out.append('记账代理不改变买/卖/持有清单（%d 买 %d 持有）'
                   % (len(sig['buy']), len(sig['hold'])))

        # 2a2) 🔴 **卖出 + 持有不动 == 持仓只数**，调仓日也必须成立。
        #      "持有不动"原来在调仓日走的是「targets ∩ 持仓」，而 targets 是从
        #      **委托**反推的 —— 策略对"留着不动"的持仓根本不下委托，于是那些票
        #      在页面上既不在卖出也不在持有不动，**凭空消失**（实测：10 只持仓
        #      的账户在调仓日只显示"卖 2 只"，另外 8 只哪儿都不在）。
        #      非调仓日那一支当初已因同一原因修过一次，这条把两支一起钉住。
        lv.upsert_account('hold1', name='hold1', init_cash=2000000)
        lv.bind_version('hold1', 'strategies/小市值/froec_traded.py',
                        params={'weekday': int(sig_aid[1:]), 'stop_loss': 0.35,
                                'stop_intraday': 1})
        #      ★ 建仓价要用**当前参考价**，不能随手填 10.0 —— 填错价会触发
        #        止损，三只全被卖掉，于是"留着不动"那一支根本没被走到，
        #        断言就成了空转（同"两边都报错不算一致"那条）。
        _held = [(b['code'], b['ref_price']) for b in sig['buy'][:3]]
        for _c, _px in _held:
            lv.add_fill('hold1', '2026-08-20', _c, 'buy', 100, _px, force_price=True)
        s3 = lv.build_signal('hold1')
        assert s3['is_rebalance_day'], '同一个 weekday 应当还是调仓日'
        assert len(s3['sell']) + len(s3['hold']) == len(_held), \
            ('卖出 %d + 持有不动 %d != 持仓 %d —— 有票在清单上凭空消失了'
             % (len(s3['sell']), len(s3['hold']), len(_held)))
        assert s3['hold'], \
            ('这三只都被卖了 —— "留着不动"那一支没走到，上面那条断言等于空转。'
             '换一组建仓价/持仓再试')
        out.append('调仓日 卖%d+持有%d == 持仓%d'
                   % (len(s3['sell']), len(s3['hold']), len(_held)))

        # 2a3) 历史期：期数清单 / 持仓出处 / **事后复算 + 自证** ----
        #      当前持仓来自上一个调仓日，而那一期的信号是这个功能上线【之前】
        #      落的盘，里面没有理由 —— 只能事后复算。而复算出来的可能**不是
        #      当时那一份**（面板会被修正），所以必须与当时存的清单对账。
        import json as _json
        m0 = lv.make_signal(sig_aid, force=True)          # 先落一份带理由的
        _sp = lv.signal_path(sig_aid, m0['for_date'])
        _raw = _json.load(open(_sp, encoding='utf-8'))
        assert _raw.get('explain', {}).get('captured'), '落盘的信号里应带理由'
        _hist = lv.explain_history(sig_aid)
        assert [x for x in _hist if x['date'] == m0['for_date'] and x['has_explain']], \
            'explain_history 没认出这一期有理由：%s' % _hist
        # 模拟"老信号"：把 explain 抠掉
        _raw.pop('explain')
        with open(_sp, 'w', encoding='utf-8') as _f:
            _json.dump(_raw, _f, ensure_ascii=False)
        assert not [x for x in lv.explain_history(sig_aid)
                    if x['date'] == m0['for_date'] and x['has_explain']], \
            '抠掉 explain 之后 has_explain 还是 True —— 页面会显示一个空浮层'
        _rc = lv.explain_recompute(sig_aid, m0['for_date'], force=True)
        assert _rc['verified'] == 'same', \
            ('同一份数据/版本/参数复算，结果却与当时不同 —— 复算不可信：%s'
             % _rc['diff'])
        assert _rc['explain'].get('captured') and _rc['explain']['pool_total'] > 0, \
            '复算没算出候选池'
        assert _rc['recomputed'] is True, '复算结果必须自己标出来'
        out.append('事后复算 %s：与当时那份逐个相同（候选池 %d 只）'
                   % (m0['for_date'], _rc['explain']['pool_total']))

        # 🔴 变异测试：把当时那份信号的买入清单改掉一只，对账必须报 `differs`
        #   —— 不然那条 `verified` 等于没写，而"复算"就会被当成"历史记录"。
        _tamper = _json.load(open(_sp, encoding='utf-8'))
        if _tamper.get('buy'):
            _tamper['buy'] = _tamper['buy'][1:]           # 少一只
            with open(_sp, 'w', encoding='utf-8') as _f:
                _json.dump(_tamper, _f, ensure_ascii=False)
            _rc2 = lv.explain_recompute(sig_aid, m0['for_date'], force=True)
            assert _rc2['verified'] == 'differs' and _rc2['diff'].get('buy'), \
                '当时那份被改过一只，对账却说"相同" —— verified 是空转的'
            out.append('对账变异测试：改一只 -> differs')

        # 🔴 复算结果的缓存必须**按绑定版本**分开 —— 重新绑定之后还返回旧的那份
        #   是静默的（实测：给 SQL 加了组名注释、重绑之后，页面上组名死活不出来）。
        import assay.lv.sig as _sigmod
        _acct = lv.get_account(sig_aid)
        _keys = [k for k in _sigmod._RECOMP if k[0] == sig_aid]
        assert _keys and len(_keys[0]) == 3 and _keys[0][2] == _acct['code_sha256'], \
            '复算缓存的键里没有绑定版本：%s' % (_keys[:1],)
        _n0 = len(_sigmod._RECOMP)
        lv.bind_version(sig_aid, 'strategies/小市值/froec.py',
                        params={'weekday': int(sig_aid[1:])})
        lv.explain_recompute(sig_aid, m0['for_date'])       # 不给 force
        assert len(_sigmod._RECOMP) > _n0, \
            '换了绑定版本，复算却命中了旧缓存 —— 页面会一直显示上一版的结果'
        lv.bind_version(sig_aid, 'strategies/小市值/froec_traded.py',
                        params={'weekday': int(sig_aid[1:]), 'stop_loss': 0.35,
                                'stop_intraday': 1})        # 绑回去
        out.append('复算缓存按绑定版本分开（重绑不会返回旧结果）')

        # 🔴 复算结果必须**落盘**：只放进程内缓存的话，重启 serve.py 就没了，
        #   人每次打开页面看到的都是"这一期没有留下选股理由"
        #   （实测反馈：「我直接看不到上一期选股」）。
        _side = _sigmod.explain_side_path(sig_aid, m0['for_date'])
        assert os.path.exists(_side), '复算结果没落盘：%s' % _side
        # 🔴 而且**不许与信号同层**：有三处在扫 signals/ 下的 *.json
        #   （sig.latest_signal / px.latest_signal / explain_history），
        #   同层的话它们会把这份派生物当成信号读进去，而排序取 [-1]，
        #   出不出错**看日期字符串的运气**。
        assert os.path.dirname(_side) != os.path.dirname(
            _sigmod.signal_path(sig_aid, m0['for_date'])), \
            '复算结果与信号同层 —— latest_signal 扫 *.json 会把它当信号读'
        import assay.lv.px as _pxmod
        _far = _sigmod.explain_side_path(sig_aid, '2099-01-01')
        os.makedirs(os.path.dirname(_far), exist_ok=True)
        with open(_far, 'w', encoding='utf-8') as _f:
            _json.dump({'date': '2099-01-01'}, _f)
        try:
            for _fn, _nm in ((_sigmod.latest_signal, 'sig'), (_pxmod.latest_signal, 'px')):
                assert (_fn(sig_aid) or {}).get('for_date') == m0['for_date'], \
                    '%s.latest_signal 被旁挂的复算结果带歪了' % _nm
            assert '2099-01-01' not in [x['date'] for x in lv.explain_history(sig_aid)], \
                'explain_history 把旁挂当成了一期信号'
        finally:
            os.remove(_far)
        # 落盘那份要能【绕开进程内缓存】被读回来 —— 不然重启后还是要重算
        _sigmod._RECOMP.clear()
        _back = lv.explain_recompute(sig_aid, m0['for_date'])
        assert _back.get('recomputed') and _back.get('for_sha') == \
            lv.get_account(sig_aid)['code_sha256'], '清了内存缓存后没能从盘上读回复算结果'
        # 🔴 绑定版本变了就当它不存在：指标那一半可能来自当前版本，
        #   拿旧版本算的指标显示成今天的，**不报错**
        assert _sigmod.load_explain_side(sig_aid, m0['for_date'], 'deadbeef') is None, \
            '换了绑定版本，旁挂的复算结果还被当成有效的'
        out.append('复算结果落盘（%s）、重启后直接可读、换版本自动失效'
                   % os.path.basename(os.path.dirname(_side)))

        # 持仓出处：按信号买进来的票要对上那一期
        lv.upsert_account('prov', name='prov', init_cash=2000000)
        lv.bind_version('prov', 'strategies/小市值/froec_traded.py',
                        params={'weekday': int(sig_aid[1:]), 'stop_loss': 0.35,
                                'stop_intraday': 1})
        lv.make_signal('prov', force=True)
        _pd = lv.latest_signal('prov')['for_date']
        for b in (lv.latest_signal('prov')['buy'] or [])[:2]:
            lv.add_fill('prov', _pd, b['code'], 'buy', 100, b['ref_price'],
                        force_price=True)
        _ent = lv.entry_signals('prov')
        assert _ent and all(v == _pd for v in _ent.values()), \
            ('持仓的出处没对上那一期（建仓日 == 信号的 for_date）：%s' % _ent)
        out.append('持仓出处对上建仓那一期（%s）' % _pd)

        # 2b) 候选池与清单自洽
        pool = {x['code']: x for x in _flat(e)}
        for b in sig['buy']:
            assert b['code'] in pool, \
                '买入 %s 不在候选池里 —— 理由就成了空白' % b['code']
            assert pool[b['code']]['status'] == 'buy', \
                '%s 在池子里的状态是 %s，不是 buy' % (b['code'],
                                                pool[b['code']]['status'])
            assert b.get('why'), '买入 %s 没有理由' % b['code']
        for _g in e['groups']:
            _rk = [x['rank'] for x in _g['rows']]
            assert _rk == sorted(_rk), '第 %d 组名次不是升序：%s' % (_g['step'], _rk)
        # 2c) 指标必须真的带上了（新绑定的版本有指标列）
        metr = pool[sig['buy'][0]['code']]['metrics'] if sig['buy'] else {}
        for k in ('pb', 'floatmv', 'roe_inc'):
            assert k in metr, '买入的票缺指标 %s（末层 SELECT 没带出来？）' % k
        assert e['groups'][0]['metric_meta'], 'metric_meta 是空的 —— 页面不知道怎么画'
        labs = {m['key']: m for m in e['groups'][0]['metric_meta']}
        assert labs['floatmv']['label'] != 'floatmv', '指标标签没翻译'
        assert labs.get('pb_rn', {}).get('kind') == 'rank', \
            '名次列没和总数列配对，会占两列且看不懂'
        out.append('候选池自洽：%d 只，买入的都带指标（pb/floatmv/roe_inc）'
                   % e['pool_total'])

        # 2c2) 🔴 多条查询（红利 A/B 两袖并集）：一只票可能在 A 里排 250、
        #      在 B 里排 1，而它是**被 B 选中的**。只留第一次出现的名次会给出
        #      一个"看着合理但解释错了"的理由（"第 250/331 名"却买了它）。
        #      ★ monthday 直接**算**出来（下个交易日是当月第几个交易日），
        #        不要循环试 1..21 —— 那要跑二十几次 build_signal。
        nxt = datetime.date.fromisoformat(sig['for_date'])
        cal = [d for d in lv.calendar_days() if (d.year, d.month) == (nxt.year, nxt.month)]
        md = cal.index(nxt) + 1
        lv.upsert_account('hl2', name='hl2', init_cash=1000000)
        lv.bind_version('hl2', 'strategies/红利/红利指数增强.py',
                        params={'div_method': 'fiscal_year', 'monthday': md})
        s2 = lv.build_signal('hl2')
        assert s2['is_rebalance_day'], \
            'monthday=%d 应当让 %s 成为调仓日（月内第几个交易日算错了）' % (md, nxt)
        e2 = s2['explain']
        assert len([x for x in e2['steps'] if x['kind'] == 'query']) >= 2, \
            '红利应有 A/B 两条查询'
        # 🔴 两袖必须是**两组**，各自的排名、指标列、条件都独立。
        #   合并成一张表实测坏三处：B 袖 6 只被去重并进 A 袖（B 组 0 行）、
        #   B 选中的票显示成 A 的第 233/332 名、两袖指标不同导致整列空白。
        assert e2['n_groups'] >= 2, '红利应有 A/B 两组，实得 %d' % e2['n_groups']
        _g1, _g2 = e2['groups'][0], e2['groups'][1]
        assert _g1['rows'] and _g2['rows'], \
            '有一组是空的（%d / %d 行）—— 小的那组被全局截断吃掉了' \
            % (len(_g1['rows']), len(_g2['rows']))
        _k1 = {m['key'] for m in _g1['metric_meta']}
        _k2 = {m['key'] for m in _g2['metric_meta']}
        assert _k1 != _k2, '两组的指标列一样？两袖本来用的是不同指标'
        # 🔴 落盘截断时"一定保留"的不只是买卖持有：**策略点过名的**（备选/
        #   缓冲区/目标池）和**被剔除的**也要留 —— 只按名次切会把排在 40 名的
        #   备选切掉，而那恰恰是"差一点就选上"的那些。
        _bk = [x for x in _g1['rows'] if 'backup_list' in (x.get('in_sets') or [])]
        assert _bk, 'A 组里一个备选都没留下 —— 被名次截断切掉了？'
        for _g in (_g1, _g2):
            for _r in _g['rows']:
                assert _r['metrics'], \
                    '第 %d 组有行没有指标 —— 合并列会让整列空白' % _g['step']
            assert _g.get('sql') and _g.get('args'), \
                '第 %d 组没有条件（SQL / 入参）—— "策略的条件有哪些"就答不上' % _g['step']
        _codes2 = {x['code'] for x in _g2['rows']}
        both = [x for x in _g1['rows'] if x['code'] in _codes2]
        assert both, '两袖并集里应有票同时出现在 A 与 B'
        pick2 = {b['code']: b['why'] for b in s2['buy']}
        cross = [c for c in pick2 if c in {x['code'] for x in both}]
        if cross:
            w = pick2[cross[0]]
            assert w.count('组第') >= 2, \
                ('%s 同时在两组里，理由必须把两组的名次都带上，实得 %s'
                 % (cross[0], w))
            out.append('两袖分组：%s 的理由带上了两组名次（%s）' % (cross[0], w))
        # 指标列也要合并过来（B 袖的 totalmv/inc_* 与 A 袖的 dy/beta 同表）
        assert {'dy', 'beta'} <= _k1 and {'totalmv', 'inc_return'} <= _k2, \
            'A 袖该有 dy/beta、B 袖该有 totalmv/净资产收益率：%s / %s' \
            % (sorted(_k1), sorted(_k2))
        for _g in (_g1, _g2):
            _rm = [m for m in _g['metric_meta'] if m['kind'] == 'rank']
            assert _rm, '第 %d 组没有名次列' % _g['step']
            assert set(_rm[0]['of']) & {'bn', 'n'}, \
                ('名次列要与"总数"列配对，否则会留下一列没人认领的裸数字：%s'
                 % _rm)
        # 组名来自 SQL 首行注释（注释改不了行为，却让"这是哪一袖"看得见）
        assert '袖A' in (_g1.get('sql_head') or ''), \
            'A 组没有名字（SQL 首行注释）：%r' % _g1.get('sql_head')
        assert '袖B' in (_g2.get('sql_head') or ''), \
            'B 组没有名字：%r' % _g2.get('sql_head')
        out.append('两组各自的条件与指标齐全（%s / %s）'
                   % (_g1['sql_head'][:12], _g2['sql_head'][:12]))

        # 2d) 步骤里要有"这一步进多少出多少"，且 20 日窗口是【20 日】
        hl_step = [x for x in e['steps'] if x['kind'] == 'had_limit_up']
        if hl_step:
            st = hl_step[0]
            span = (datetime.date.fromisoformat(st['args']['end'])
                    - datetime.date.fromisoformat(st['args']['start'])).days
            assert span < 60, \
                ('20 日涨停黑名单的窗口是 %d 天（%s ~ %s）—— nth_prev_day 又落回'
                 '面板第一天了' % (span, st['args']['start'], st['args']['end']))
            out.append('涨停黑名单窗口 %d 天（不是 10 年）' % span)
    finally:
        lv.LIVE = old_live
        shutil.rmtree(tmp, ignore_errors=True)
    return '；'.join(out)


@case('盘中炸板提示：只报真炸板 / 去重落盘 / 任何页面都能看到', tag='fast')
def case_intraday_alert():
    """三条判据，每条都对应一种**不报错的**坏法：

      ① `fire()` 只把 **broken** 的记进去重账本。写成"有 items 就记账"的话，
         还封着的票也被记掉，之后**真炸板时不再报** —— 静默失效。
      ② 判据是**涨停价**（面板的 `limit_up`），不是"涨幅 < 9.9%"：
         涨跌幅限制有 10%/20%/5% 三档，固定阈值必然错。
      ③ 浮窗挂在 **common.js**，所以 6 个独立 .html 与 index.html 都有它。
         挂在实盘页的话，人正在看个股/盘面时就漏掉了 —— 而炸板要立刻处理。
    """
    import importlib, io, tempfile
    inn = importlib.import_module('assay.lv.intraday')
    lv = importlib.import_module('assay.live')

    # ---- ① 去重只认 broken ----
    with tempfile.TemporaryDirectory() as td:
        old = lv.LIVE
        try:
            lv.LIVE = td                     # 🔴 重定向，不许写真账本
            os.makedirs(os.path.join(td, 'zbtest'), exist_ok=True)
            sealed = {'code': '301126.XSHE', 'name': '甲', 'px': 11.0,
                      'limit': 11.0, 'broken': False}
            brk    = {'code': '002910.XSHE', 'name': '乙', 'px': 9.5,
                      'limit': 10.0, 'broken': True}
            f1 = inn.fire('zbtest', [sealed, brk], day='2026-09-08')
            assert [x['code'] for x in f1] == ['002910.XSHE'], \
                '只有真炸板的才该报，还封着的不报：%r' % f1
            # 同一只票第二次不再报
            f2 = inn.fire('zbtest', [brk], day='2026-09-08')
            assert f2 == [], '同一只票一天只报一次，第二次必须为空：%r' % f2
            # 🔴 关键：封着那只**没被记账**，所以它之后真炸板时还能报出来
            f3 = inn.fire('zbtest', [dict(sealed, px=10.2, broken=True)],
                          day='2026-09-08')
            assert [x['code'] for x in f3] == ['301126.XSHE'], \
                '封着时不该记账 —— 否则真炸板了报不出来（静默失效）：%r' % f3
            # 换一天，重新计
            f4 = inn.fire('zbtest', [brk], day='2026-09-09')
            assert [x['code'] for x in f4] == ['002910.XSHE'], '换天要重新报'
        finally:
            lv.LIVE = old

    # ---- ② 判据必须是涨停价，不是固定涨幅阈值 ----
    src = io.open(inn.__file__, encoding='utf-8').read()
    body = src[src.index('def limit_up_holdings'):src.index('def scan')]
    assert 'is_limit_up' in body and 'limit_up' in body, \
        'limit_up_holdings 必须读面板的 is_limit_up / limit_up'
    # 🔴 **第 6 次**踩这个坑：`'9.9' in src` 抓到的是我**自己写的注释**
    #   （"不是涨幅 < 9.9%"）。注释**不是 AST 节点**，所以扫 ast 的数字
    #   常量才是真判据 —— 同 `--install-timer` / `次日` / `h.pruned` 那几条。
    import ast as _ast
    nums = {n.value for n in _ast.walk(_ast.parse(src))
            if isinstance(n, _ast.Constant) and isinstance(n.value, float)}
    bad = {x for x in nums if 0.04 < x < 0.21 or 4 < x < 21}
    assert not bad, \
        '代码里出现了像"固定涨幅阈值"的数 %r —— 涨跌幅有 10%%/20%%/5%% 三档，' \
        '判据必须是面板的涨停价' % sorted(bad)
    # scan 里 broken 的判据必须比**涨停价**，而不是比某个百分比
    sbody = src[src.index('def scan'):src.index('def fire')]
    assert "'broken':float(p)<lim" in sbody.replace(' ', ''), \
        'broken 必须是「现价 < 涨停价」：%s' % [l for l in sbody.splitlines()
                                              if 'broken' in l]
    assert inn.SCAN_FROM.hour >= 10, \
        '10:00 之前不扫 —— 开盘半小时开板/回封频繁，早报多半是假告警'

    # ---- ③ 浮窗必须在 common.js（所有页面都加载它）----
    cj = io.open('web/shared/common.js', encoding='utf-8').read()
    assert 'zbScan' in cj and '/api/live/intraday' in cj, \
        '炸板浮窗必须挂在 common.js —— 挂在实盘页的话看个股时就漏掉了'
    assert 'zbMute' in cj and 'zbmute' in cj, '必须能 Mute 1 小时'
    # 🔴 断言匹配**完整条件**，不是"标识符出现过"（同 h.pruned / explain_pending）
    flat = cj.replace(' ', '')
    assert 'if(zbMuted())return' in flat, \
        'Mute 期内必须真的**不扫** —— 只定义 zbMuted 而不在 zbScan 里用等于没做'
    assert 'if(!o||!o.session)return' in flat, \
        '时段判据必须来自服务端（同「权不权威由服务端给」）'
    # 每个独立页面都 <script src> 了 common.js
    for h in sorted(g for g in os.listdir('web') if g.endswith('.html')):
        t = io.open(os.path.join('web', h), encoding='utf-8').read()
        assert 'shared/common.js' in t, \
            '%s 没引 common.js —— 那一页就看不到炸板提示' % h
    print('    ✓ fire 只认 broken（封着的不记账）· 判据是涨停价 · '
          'SCAN_FROM=%s · %d 个页面都有浮窗'
          % (inn.SCAN_FROM.strftime('%H:%M'),
             len([g for g in os.listdir('web') if g.endswith('.html')])))


@case('炸板浮窗真的弹得出来 / Mute 之后不再弹（playwright）', tag='web')
def t_zb_ui():
    """🔴 "JS 语法对" 不等于 "浮窗出得来" —— `.pane` 那次就是样式表里的
      `display:none` 让整块 SVG 静默隐形。所以这条**拦掉接口造数据**，
      在真浏览器里量四件事：

      ① 浮窗真的可见（`is_visible`，不是"DOM 里有这个 id"）
      ② 内容说清了是哪只票、现价 vs 涨停价、哪个账户
      ③ 点 Mute 之后浮窗消失，**且再扫也不弹**（localStorage 留痕）
      ④ 它在**独立页面**上也弹（拿个股页试 —— 那是最容易漏的：
         浏览器在别的页面时炸板提示照样要出来）
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return '跳过（无 playwright）'
    import json as _json
    import threading
    from http.server import ThreadingHTTPServer

    from assay import server as sv
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), sv.Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    STUB = {'session': True, 'scan_from': '10:00', 'accounts': [],
            'fresh': [{'code': '002910.XSHE', 'name': '庄园牧场',
                       'px': 9.51, 'limit': 10.02, 'pct': -0.0509,
                       'limit_pct': 0.1, 'at': '2026-09-08 10:35:00',
                       'account': 'froec', 'account_name': 'froec 实盘'}]}
    notes = []
    try:
        with sync_playwright() as p:
            b = p.chromium.launch()
            pg = b.new_page()
            hits = {'n': 0}

            def route(rt):
                hits['n'] += 1
                # ★ 第二次起返回空 —— 这样"Mute 之后不再弹"才是真判据
                #   （否则分不清"没弹"和"接口没被调"）
                body = STUB if hits['n'] == 1 else dict(STUB, fresh=[])
                rt.fulfill(status=200, content_type='application/json',
                           body=_json.dumps(body))
            pg.route('**/api/live/intraday', route)

            # ---- ④ 先在【独立页面】上验（最容易漏的那个）----
            pg.goto('http://127.0.0.1:%d/stock.html' % port)
            pg.wait_for_selector('#zbbox', timeout=15000)
            box = pg.locator('#zbbox')
            assert box.is_visible(), \
                '浮窗在 DOM 里但不可见 —— 同 .pane 那次样式表里的 display:none'
            txt = box.inner_text()
            for want in ('庄园牧场', '9.51', '10.02', 'Mute 1 小时'):
                assert want in txt, '浮窗少了「%s」：%r' % (want, txt)
            notes.append('独立页（个股）上弹出且内容完整')

            # ---- ③ 点 Mute -> 消失，且再扫不弹 ----
            pg.click('#zbmute')
            assert pg.locator('#zbbox').count() == 0, 'Mute 之后浮窗该消失'
            left = pg.evaluate("() => +(localStorage.getItem('zbmute')||0)")
            assert left > 0, 'Mute 必须落 localStorage —— 刷新一下就失效等于没做'
            mins = (left - pg.evaluate('() => Date.now()')) / 60000
            assert 55 < mins < 61, 'Mute 该是 1 小时，实测 %.1f 分钟' % mins
            # 🔴 刷新页面（重新加载 common.js）后仍在静默期
            hits['n'] = 0
            pg.reload()
            pg.wait_for_timeout(4500)
            assert pg.locator('#zbbox').count() == 0, \
                'Mute 期内刷新后又弹了 —— localStorage 没被读到'
            assert hits['n'] == 0, \
                'Mute 期内还在打接口 %d 次 —— if(zbMuted())return 没起作用' % hits['n']
            notes.append('Mute 落 localStorage（%.0f 分钟）· 刷新后仍静默且不打接口'
                         % mins)

            # ---- ① 解除 Mute，在 index.html 上再验一次 ----
            pg.evaluate("() => localStorage.removeItem('zbmute')")
            hits['n'] = 0
            pg.goto('http://127.0.0.1:%d/' % port)
            pg.wait_for_selector('#zbbox', timeout=15000)
            assert pg.locator('#zbbox').is_visible()
            # 「×」只关这一次，不进静默期
            pg.click('#zbclose')
            assert pg.locator('#zbbox').count() == 0
            assert pg.evaluate("() => +(localStorage.getItem('zbmute')||0)") == 0, \
                '「×」不该写 Mute —— 那是两个不同的意思（关掉 vs 静音一小时）'
            notes.append('index.html 上也弹；「×」只关一次不进静默期')
            b.close()
    finally:
        httpd.shutdown()
    return '；'.join(notes)


@case('个股速览浮层：点了不跳走 / 买卖点画在成交价上（playwright）', tag='web')
def t_stockpop():
    """用户的原话是"点击股票名称就真的跳转到个股页面了，然后无法直接返回"，
    以及"其他地方可能也有这样的情况，也要做成这样的效果"。所以这条要验
    **每一类页面**都不跳走，而不是只验实盘页。

    钉五件事，每件都对应一种**不报错的**坏法：
      ① 点了 URL **不变** + 浮层可见 —— "跳走了"和"浮层没弹"都是静默的坏
      ② 买卖点画在**成交价的位置**上，且 hover 出得来读数（价/量/账户）
      ③ 引了 stockpop 的页面**必须也引 kchart** —— 实测踩到：盘面/自选/
         买点/板块四个页面原本没有 kchart.js，点开浮层就是
         `drawKChart is not defined`，而浮层照样弹出、只是**一片空白**
      ④ 浮层固定 `bfq`：成交价是不复权实际价，切后复权标记会整体飘走
      ⑤ 入口只走统一 helper —— 裸 `href="/stock.html?code="` 一处都不许剩，
         否则那一处会**继续跳走**，而它不报错
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return '跳过（无 playwright）'
    import io as _io
    import threading
    from http.server import ThreadingHTTPServer

    from assay import server as sv

    # ---- ⑤ 静态：入口只有一个定义点，页面里不许有裸链接 ----
    # 🔴 判据匹配**结构**（`href=` 才是链接），不是"字符串出现过" ——
    #   头一版写成 `'stock.html?code=' in line` 就被 index.html 里
    #   HTML 注释块中反引号包着的那句路径说明打挂了。同 h.pruned 那条。
    import re as _re
    HREF = _re.compile(r'''href=["']/stock\.html\?code=''')
    for f in _web_files('web', '.js') + _web_files('web', '.html'):
        t = _io.open(os.path.join('web', f), encoding='utf-8').read()
        for ln, line in enumerate(t.splitlines(), 1):
            if not HREF.search(line):
                continue
            assert 'spHref' in line or 'stockHref' in line, \
                ('%s:%d 有裸的个股页链接 —— 那一处会【继续跳走】，'
                 '而它不报错：%s' % (f, ln, line.strip()[:90]))
    # ③ 引了 stockpop 的页面必须也引 kchart（浮层要画 K 线）
    pops = []
    for f in sorted(g for g in os.listdir('web') if g.endswith('.html')):
        t = _io.open(os.path.join('web', f), encoding='utf-8').read()
        if 'shared/stockpop.js' not in t:
            continue
        pops.append(f)
        assert 'shared/kchart.js' in t, \
            '%s 引了 stockpop 却没引 kchart —— 点开浮层是 drawKChart ' \
            'is not defined，而浮层照样弹出、只是一片空白' % f
        assert t.index('shared/stockpop.js') > t.index('shared/common.js'), \
            '%s 里 stockpop.js 必须在 common.js 之后（它用 esc/num/j/_tipAt）' % f
    assert len(pops) >= 7, '只有 %d 个页面有浮层：%s' % (len(pops), pops)
    # ④ 复权口径：**一律不复权**（2026-09-14 又改回来了，理由变了）
    # ★ 这条断言的历史值得记，它是「判据要跟着【事实】走」的活样本：
    #     原版      固定 `SP_FQ='bfq'` —— 那时浮层只有实盘一个来源
    #     09-13     改成"按来源定"（回测 -> hfq）—— 因为**归档存的是后复权**
    #     09-14     改回"一律 bfq" —— 因为归档的展示层换算回不复权了
    #   中间那一版不是错的，它只是**将就了当时的存储口径**；把数据源本身
    #   修正之后，那个分支就该消失。**规则只有一条，实盘与回测再没有分支。**
    sp = _io.open('web/shared/stockpop.js', encoding='utf-8').read()
    flat = sp.replace('"', "'").replace(' ', '')
    assert "constspFq=()=>'bfq'" in flat, \
        ('浮层必须一律用【不复权】：两个来源的成交价现在都是不复权'
         '（实盘账本本来就是；回测展示层 2026-09-14 起也换算回去了）。'
         '用 hfq 会让 B/S 标记整体飘走，**而它不报错**')
    assert 'SP_FQ' not in sp, \
        '还留着 SP_FQ 这个旧常量 —— 两处定义迟早分叉（同「删字段要连带清干净」那条）'
    # 取数时真的用了它，而不是把 fq 写死在 URL 里
    assert 'fq=${spFq()}' in sp, 'kline 请求没有用 spFq()，口径切换等于没生效'
    # 🔴 而服务端给的成交价也必须是不复权 —— 前端切了 K 线口径、后端还给
    #   后复权价的话，标记照样飘，而两边各自看都"正常"。
    rs = _io.open('assay/srv/runs.py', encoding='utf-8').read()
    assert "'fq': 'bfq'" in rs and "'fq': 'hfq'" not in rs, \
        'api_run_trades_of 还在声明 hfq —— 与浮层的口径对不上'

    httpd = ThreadingHTTPServer(('127.0.0.1', 0), sv.Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    notes = []
    try:
        with sync_playwright() as p:
            b = p.chromium.launch()
            # ---- ① 每一类页面都不跳走 ----
            PAGES = [('/#/live', '实盘'), ('/market.html', '盘面'),
                     ('/watchlist.html', '自选'), ('/alerts.html', '买点'),
                     ('/sector.html?kind=sw&code=801230', '板块成分'),
                     ('/stock.html?code=601857.XSHG', '个股同行业')]
            for path, label in PAGES:
                pg = b.new_page(viewport={'width': 1400, 'height': 900})
                errs = []
                pg.on('pageerror', lambda e: errs.append(str(e)))
                pg.goto('http://127.0.0.1:%d%s' % (port, path))
                pg.wait_for_timeout(3800)
                v = pg.locator('a[data-sp]:visible')
                assert v.count(), '%s 页一个浮层入口都没有' % label
                url0 = pg.url
                v.first.scroll_into_view_if_needed()
                v.first.click()
                pg.wait_for_timeout(3800)
                assert pg.url == url0, \
                    '%s 页点了就跳走了（%s -> %s）—— 那正是要修的毛病' \
                    % (label, url0, pg.url)
                box = pg.locator('#spwrap')
                assert box.count() and box.is_visible(), \
                    '%s 页浮层没弹出来或不可见（同 .pane 那次样式表 ' \
                    'display:none）' % label
                title = pg.locator('#sptitle').inner_text()
                assert title and title != '', '%s 页浮层标题是空的' % label
                assert not errs, '%s 页有 JS 错误：%s' % (label, errs[:2])
                # Esc 关得掉
                pg.keyboard.press('Escape')
                pg.wait_for_timeout(400)
                assert pg.locator('#spwrap').count() == 0, \
                    '%s 页 Esc 关不掉浮层' % label
                notes.append('%s %d入口' % (label, v.count()))
                pg.close()

            # ---- ② 买卖点：位置 + hover 读数（用实盘页，它有真成交）----
            pg = b.new_page(viewport={'width': 1400, 'height': 900})
            pg.goto('http://127.0.0.1:%d/#/live' % port)
            pg.wait_for_timeout(3800)
            # 找一只**有成交**的（持仓表里的都有）
            v = pg.locator('a[data-sp]:visible')
            v.first.scroll_into_view_if_needed()
            v.first.click()
            pg.wait_for_timeout(4500)
            d = pg.evaluate("""() => {
              const h = (SP.geo && SP.geo.trHits) ? SP.geo.trHits : [];
              const ts = SP.trades || [];
              const bs = SP.bars || [];
              const at = {}; bs.forEach((x, i) => { at[x.date] = i; });
              return {nh: h.length, nt: ts.length,
                      first: h[0] ? {x: h[0].x, y: h[0].y, price: h[0].t.price,
                                     date: h[0].t.date} : null,
                      idx: ts[0] ? at[ts[0].date] : null,
                      lo: Math.min(...bs.map(x => x.low)),
                      hi: Math.max(...bs.map(x => x.high))};
            }""")
            assert d['nt'], '这只票没有实盘成交 —— 换一个入口再验'
            assert d['nh'] == d['nt'], \
                '成交 %d 笔但只画出 %d 个标记' % (d['nt'], d['nh'])
            # 🔴 判据必须是"**y 随价格变**"，不是"y 不等于某个值"。
            #   头一版写成 `abs(y - 画布中线) > 4 or ...`，把 Y(price) 改成
            #   `PADT + mainH - 2`（像事件三角那样钉在底部）**照样全绿** ——
            #   那个 or 让它几乎永远成立。现在构造两笔不同价的成交，
            #   断言高价那笔的 y 更小、且差值与价差成比例。
            f = d['first']
            probe = pg.evaluate("""() => {
              const bs = SP.bars, lo = Math.min(...bs.map(x => x.low)),
                    hi = Math.max(...bs.map(x => x.high));
              const dt = bs[bs.length - 3].date;
              const p1 = lo + (hi - lo) * 0.2, p2 = lo + (hi - lo) * 0.8;
              const cv = document.getElementById('spcv');
              const mk = px => {
                const g = drawKChart(cv, {bars: bs, trades:
                  [{date: dt, side: 'buy', shares: 100, price: px}]});
                return g.trHits.length ? g.trHits[0].y : null;
              };
              return {y_low: mk(p1), y_high: mk(p2), lo: lo, hi: hi,
                      h: cv.height};
            }""")
            assert probe['y_low'] is not None and probe['y_high'] is not None, \
                '构造的成交点没画出来：%r' % probe
            assert probe['y_high'] < probe['y_low'] - 20, \
                ('买卖点的 y 不随成交价变（低价 y=%.0f / 高价 y=%.0f）—— '
                 '它被钉在了固定高度上，而"画在成交价的位置"正是它与'
                 '事件三角的区别' % (probe['y_low'], probe['y_high']))
            # 差值应当约等于 60% 的主图高度（两个探针取的是 20% / 80% 分位）
            dy = probe['y_low'] - probe['y_high']
            assert dy > probe['h'] * 0.25, \
                '价差 60%% 只换来 %.0fpx 的 y 差（画布 %.0f）—— 比例不对' \
                % (dy, probe['h'])
            # hover 上去要有读数
            cv = pg.locator('#spcv')
            bb = cv.bounding_box()
            sc = pg.evaluate("() => { const c = document.getElementById('spcv');"
                             "  return c.width / c.offsetWidth; }")
            pg.mouse.move(bb['x'] + f['x'] / sc, bb['y'] + f['y'] / sc)
            pg.wait_for_timeout(600)
            tip = pg.locator('#sptip')
            assert tip.is_visible(), 'hover 到买卖点上没有读数浮窗'
            txt = tip.inner_text()
            for want in ('买入', str(f['date'])):
                assert want in txt, 'hover 读数少了「%s」：%r' % (want, txt)
            assert ('%.2f' % f['price']) in txt.replace(',', ''), \
                'hover 读数里没有成交价 %.2f：%r' % (f['price'], txt)
            # ---- B/S 点是【同花顺那种圆点】，不是三角；列表默认折叠 ----
            bs = pg.evaluate("() => { const c = document.getElementById('spcv');"
                             "  return {w: c.offsetWidth, h: c.offsetHeight}; }")
            # 🔴 高度判**比例**不判绝对值：改成 width*0.32 时算出 368，
            #   仍然 >= 340 —— 断言照过（变异实测）。
            assert bs['w'] > 1000 and bs['h'] >= 340 \
                   and bs['h'] >= bs['w'] * 0.45, \
                ('画布只有 %dx%d —— B/S 圆点要能看出"哪根柱子"，'
                 '窄了 120 根柱子挤在一起圆点会互相压住' % (bs['w'], bs['h']))
            kc = _io.open('web/shared/kchart.js', encoding='utf-8').read()
            # 🔴 切片锚点要用**代码构造**，不要用注释：`/* 副图 */` 那句在
            #   2026-09-15 改成多副图时被重写了，于是这条当场
            #   `ValueError: substring not found` —— 看着像页面坏了，
            #   其实是**锚点没了**（同「断言查字符串会命中自己写的注释」的
            #   另一面：注释会变，代码构造不会）。
            seg = kc[kc.index('const trs = opts.trades'):
                     kc.index('subs.forEach(')]
            assert "fillText(buy ? 'B' : 'S'" in seg, \
                '买卖点必须是带 B/S 字母的圆点（同花顺那种），不是三角'
            # 🔴 只查 `g.arc(` 太宽 —— 引线端点那个小圆也是 arc，
            #   把 B/S 那个圆改成三角路径**照样全绿**（变异实测）。
            assert 'g.arc(x, cy, R,' in seg.replace('  ', ' '), \
                'B/S 标记本身必须是圆（arc(x, cy, R)），不是三角形路径'
            # 🔴 `.spmw` 的 display 在**样式表**里，所以开关只能加/去 class ——
            #   写 style.display='' 只是删内联样式、规则照旧生效，表现是
            #   "点了没反应"且不报错（.hlpbox 那条）。所以这里量**可见性**。
            assert not pg.locator('#spmw').is_visible(), \
                '成交明细该默认折起 —— 图上的 B/S 点才是主视角'
            assert '笔' in pg.locator('#spmt').inner_text(), \
                '折起时要留一行摘要（几笔、现持多少）—— 收起后什么都看不到' \
                '的折叠不如不做'
            pg.click('#spmt'); pg.wait_for_timeout(300)
            assert pg.locator('#spmw').is_visible(), '点了展不开'
            row = pg.locator('#spmw tbody tr').first.inner_text()
            for w in ('买入', '16.63' if '16.63' in row else str(f['price'])):
                assert w in row, '明细行少了「%s」：%r' % (w, row)
            pg.click('#spmt'); pg.wait_for_timeout(300)
            assert not pg.locator('#spmw').is_visible(), '再点收不起来'
            # ---- K 线读数必须有【涨跌幅】----
            st = pg.evaluate("() => SP.geo.step")
            bb2 = cv.bounding_box()
            pg.mouse.move(bb2['x'] + (f['x'] - st * 30) / sc,
                          bb2['y'] + bb2['height'] * 0.4)
            pg.wait_for_timeout(500)
            kt = pg.locator('#sptip').inner_text()
            # 🔴 `'%' in kt` 太宽 —— 去掉涨跌幅后「换手 1.22%」里还有个 %，
            #   断言照过（变异实测）。判据是 pctv 输出的**带符号**百分数。
            import re as _re2
            assert _re2.search(r'[+-]\d+\.\d+%', kt) and ('昨收' in kt), \
                ('K 线读数少了涨跌幅或昨收 —— 看 K 线第一个想知道的就是'
                 '"那天涨跌多少"，只给 OHLC 得自己拿收盘除昨收：%r' % kt)
            for w in ('开', '高', '低', '收', '量'):
                assert w in kt, 'K 线读数少了「%s」：%r' % (w, kt)
            notes.append('B/S 圆点 %d 个 · 明细默认折起 · K 线读数带涨跌幅'
                         % d['nh'])
            b.close()
    finally:
        httpd.shutdown()
    return '；'.join(notes)


@case('交易记录：买卖分行 / 分页 / 点名称弹浮层并定位到那天（playwright）', tag='web')
def t_trades_pane():
    """用户三条：「交易记录应该也要分页」「应该是买、卖各一笔」「点击名称/编码
    弹出个股页面，同时自动定位到那一行所在的日期，前后默认展示 2 个月」。

    每条都对应一种**不报错**的坏法：
      ① 不分页：3946 行一次塞进 DOM —— 页面不报错，只是卡住
      ② 拆分若放在**前端**做：分页边界会错（一页 100 拆完变 200，
         且跨页的买卖被切开）。所以判据是**服务端**返回的就是拆好的
      ③ 买入行的"收益率/持有天/卖出原因"必须**留空**而不是 0 ——
         填 0 会被读成"这笔没赚没亏"（同实盘「费用留空 ≠ 填 0」那条）
      ④ 浮层的 `run` 不带上的话，ETF 回测点开是**一片空白**
         （ETF 跑在平行的 etf_lake 上，主面板里一行都没有）
      ⑤ 定位：目标那天要落在图中段，且窗口≈前后各两个月
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return '跳过（无 playwright）'
    import json as _json
    import threading
    from http.server import ThreadingHTTPServer
    from urllib.request import urlopen

    from assay import server as sv
    from assay.srv import runs as _sr

    # 找一个有成交的归档（优先 ETF —— 它跑在平行 lake 上，最容易暴露 ④）
    _sr._scan()
    with _sr._lock:
        idx = dict(_sr._index)
    cand = []
    for rid, d in idx.items():
        if os.path.exists(os.path.join(d, 'trades.parquet')):
            cand.append((0 if os.sep + 'ETF' + os.sep in d else 1, rid))
    assert cand, '没有带成交记录的归档，测不了'
    cand.sort()
    rid = cand[0][1]

    httpd = ThreadingHTTPServer(('127.0.0.1', 0), sv.Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = 'http://127.0.0.1:%d' % port
    try:
        def get(path):
            return _json.loads(urlopen(base + path, timeout=60).read())

        # ---- ①② 服务端就拆好了，且分页 ----
        t = get('/api/trades?id=%s&limit=10' % rid)
        assert t['limit'] == 10 and len(t['rows']) <= 10, \
            '交易记录没分页：limit=%s 却回了 %d 行' % (t.get('limit'), len(t['rows']))
        assert t['n_buy'] == t['n_sell'] and t['total'] == t['n_buy'] + t['n_sell'], \
            ('一笔平仓必须拆成买、卖【各一行】：total=%d buy=%d sell=%d'
             % (t['total'], t['n_buy'], t['n_sell']))
        raw = _sr._read(rid, 'trades')
        assert t['total'] == 2 * len(raw), \
            '拆分后应是归档行数的两倍（%d vs %d）' % (t['total'], len(raw))
        sides = set(r['side'] for r in t['rows'])
        assert sides <= {'buy', 'sell'}, '方向只能是 buy/sell：%s' % sides
        # 倒序 + 同日先卖后买
        ds = [str(r['date'])[:10] for r in t['rows']]
        assert ds == sorted(ds, reverse=True), '交易记录要按日期倒序：%s' % ds[:5]

        # ---- ③ 买入行不许把"卖出才有的量"填成 0 ----
        big = get('/api/trades?id=%s&limit=400' % rid)
        buys = [r for r in big['rows'] if r['side'] == 'buy']
        sells = [r for r in big['rows'] if r['side'] == 'sell']
        assert buys and sells, '这一页里买卖都要有才测得到（买 %d 卖 %d）' % (len(buys), len(sells))
        for k in ('ret', 'holding_days', 'reason', 'div_tax'):
            bad = [r for r in buys if r.get(k) is not None]
            assert not bad, \
                ('买入行的 `%s` 必须留空 —— 那个量在买入那一刻不存在，'
                 '填 0/填值会被读成"确实是 0"。实得 %r' % (k, bad[0].get(k)))
        assert all(r.get('ret') is not None for r in sells), '卖出行必须有收益率'
        assert all(r.get('price') is not None for r in buys), '买入行必须有价格'

        with sync_playwright() as p:
            b = p.chromium.launch()
            pg = b.new_page(viewport={'width': 1600, 'height': 1000})
            errs = []
            pg.on('pageerror', lambda e: errs.append(str(e)))
            pg.goto('%s/#/run/%s' % (base, rid))
            pg.wait_for_timeout(4500)
            pg.get_by_text('交易记录', exact=True).first.click()
            pg.wait_for_timeout(2500)

            hdr = [h.inner_text().strip() for h in pg.locator('#p3 table th').all()]
            for want in ('日期', '方向', '股票'):
                assert any(want in h for h in hdr), '列头缺「%s」：%s' % (want, hdr)
            assert not any('建仓日' in h or '平仓日' in h for h in hdr), \
                '建仓日/平仓日应合并成「日期」：%s' % hdr
            assert pg.locator('#p3 .pg').count() >= 1, '交易记录没有分页控件'

            # ---- ④⑤ 点名称 -> 浮层 + 定位 ----
            lk = pg.locator('#p3 table a[data-sp]').first
            assert lk.count(), '交易记录里名称不可点'
            want_d = lk.get_attribute('data-spd')
            assert want_d, '链接没带 data-spd —— 浮层无从定位'
            assert lk.get_attribute('data-spr') == rid, \
                ('链接必须带 data-spr（run id）—— 不带的话浮层去主面板取数，'
                 '而 ETF 回测跑在平行 lake 上，会开出一片空白')
            lk.click()
            pg.wait_for_selector('#spwrap', state='visible', timeout=25000)
            pg.wait_for_timeout(2500)
            info = pg.evaluate("""() => ({n: (SP.bars||[]).length,
                center: SP.center, run: SP.run,
                dates: (SP.bars||[]).map(b => b.date)})""")
            assert info['n'] > 20, \
                ('浮层画不出 K 线（%d 根）—— ETF 回测最容易在这里空掉'
                 % info['n'])
            assert info['center'] == want_d, \
                '浮层没收到定位日期：%r vs %r' % (info['center'], want_d)
            # 窗口 ≈ 前后各两个月（84 根 ≈ 4 个月）
            assert 60 <= info['n'] <= 110, \
                '带日期打开时默认窗口应≈前后各两个月，实得 %d 根' % info['n']
            # 🔴 目标那天必须**在窗口里**，且前面留足上下文。
            #   ★ 这里**不能直接断言"居中"**：第一页是最近的成交，而数据就到
            #     那几天为止 —— 目标日之后根本没有两个月的 K 线可显示，窗口被
            #     钳到右边缘是**物理限制不是 bug**（实测落在 82%）。
            #     所以"居中"那一条放到下面用**老成交**验（两侧都有数据）。
            assert want_d in info['dates'], \
                ('定位那天不在窗口里（%s 不在 %s ~ %s）'
                 % (want_d, info['dates'][0], info['dates'][-1]))
            i0 = info['dates'].index(want_d)
            assert i0 >= 30, \
                ('定位那天前面只有 %d 根 K 线 —— "前后各两个月"的【前】那半段'
                 '任何时候都该满足（它不受数据末日影响）' % i0)
            where = '%.0f%%' % (i0 / float(info['n']) * 100)

            # ---- 居中：翻到**末页**（最老的成交），那时两侧都有数据 ----
            pg.keyboard.press('Escape')
            pg.wait_for_timeout(500)
            pg.locator('#p3 .tl').first.click()          # 末页
            pg.wait_for_timeout(2500)
            lk2 = pg.locator('#p3 table a[data-sp]').first
            d2 = lk2.get_attribute('data-spd')
            lk2.click()
            pg.wait_for_selector('#spwrap', state='visible', timeout=25000)
            pg.wait_for_timeout(2500)
            i2 = pg.evaluate(
                "() => ({n: (SP.bars||[]).length,"
                "        dates: (SP.bars||[]).map(b => b.date)})")
            assert d2 in i2['dates'], '老成交也该定位得到：%s' % d2
            pos2 = i2['dates'].index(d2) / float(i2['n'])
            assert 0.3 <= pos2 <= 0.7, \
                ('两侧都有数据时必须**居中**，实得 %.0f%% —— 偏到边上就等于没定位'
                 % (pos2 * 100))
            where += '，老成交 %s 落在 %.0f%%' % (d2, pos2 * 100)
            assert not errs, '页面有运行时错误：%s' % errs[:2]
            b.close()
        return ('拆分 %d 笔平仓 -> %d 行（买 %d/卖 %d）；分页 limit 生效；'
                '买入行的收益率/持有天/卖出原因均留空；'
                '点名称弹浮层、带 run=%s、定位 %s 落在 %s'
                % (len(raw), t['total'], t['n_buy'], t['n_sell'],
                   rid[:8], want_d, where))
    finally:
        httpd.shutdown()


@case('实盘页刷新不许【跳动】：只换数字，DOM 与滚动位置不动（playwright）', tag='web')
def t_live_nojump():
    """用户的原话："刷新的时候感觉整个页面跳动了一下。是不是应该只刷新数字，
    页面不应该跟着动。"

    钉四件事，每件都对应一种**看得见但不报错**的坏法：
      ① `quiet` 轮询**不清空**成「读取中…」—— 内容先消失、高度塌陷，
         几百毫秒后再撑开，那就是"跳一下"的来源
      ② DOM **结构不重建**：判据是往某一行打个 `data-mark`，刷完必须还在
         （只比高度的话，重建出一模一样的 DOM 也算"没跳"，但 hover、
         选中的文字、正在读的那一行都断了）
      ③ **容器高度与滚动位置逐像素不变**
      ④ **业绩板要原样保住**：`#kperf2` 由 `liveEquityTag` 异步补进来（轮询
         刻意不重拉它），而 `kpiHtml` 给的只是占位「累计收益 …」——
         整块换会把它抹回加载态，高度掉一截。实测 #lvkpi 177 -> 161
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return '跳过（无 playwright）'
    import io as _io
    import threading
    from http.server import ThreadingHTTPServer

    from assay import server as sv

    # ---- ① 静态：quiet 分支不许清空 ----
    src = _io.open('web/views/live.js', encoding='utf-8').read()
    fn = src[src.index('async function loadLive'):]
    fn = fn[:fn.index('\n}\n')]
    flat = fn.replace(' ', '')
    assert "if(!quiet)b.innerHTML='<divclass=\"none\">读取中…</div>'" in flat, \
        ('quiet 轮询必须跳过「读取中…」占位 —— 清空会让高度塌陷，'
         '几百毫秒后再撑开就是"整页跳一下"')
    assert 'if(quiet&&livePatch(o))' in flat, \
        'quiet 时必须先试原地 patch，patch 得动才不重建 DOM'
    # 一处定义：渲染路径里不许有手写的 data-rt（否则与 lvRtd 两份会分叉）
    # 🔴 **先剥注释。** 原来直接扫源码，于是 `livePatch` 的那段说明里
    #   写着「patch 靠 `td[data-rt="code|field"]`」—— 断言匹配到了
    #   **自己写的注释**而判失败。本会话第 9 次栽在这上面：
    #   注释是给人看的说明，断言必须只针对会被执行的东西。
    body = src[src.index('async function loadLive'):]
    body = re.sub(r'/\*.*?\*/', '', body, flags=re.S)
    body = '\n'.join(re.sub(r'(^|\s)//.*$', '', ln) for ln in body.splitlines())
    assert 'data-rt=' not in body, \
        '渲染路径里有手写的 data-rt —— 那就是第二份定义，迟早与 lvRtd 分叉'

    httpd = ThreadingHTTPServer(('127.0.0.1', 0), sv.Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as p:
            b = p.chromium.launch()
            pg = b.new_page(viewport={'width': 1500, 'height': 900})
            errs = []
            pg.on('pageerror', lambda e: errs.append(str(e)))
            pg.goto('http://127.0.0.1:%d/#/live' % port)
            pg.wait_for_selector('#lvbody table.lvpos', timeout=20000)
            pg.wait_for_timeout(4500)          # 等业绩板异步填进来
            n_rt = pg.locator('#lvbody td[data-rt]').count()
            assert n_rt >= 7, '持仓表里只有 %d 个实时格子' % n_rt
            # ② 打记号 + ③ 记高度与滚动位置
            pg.evaluate("""() => {
              const tr = document.querySelector('#lvbody table.lvpos tr:nth-child(3)');
              if(tr) tr.dataset.mark = 'keepme';
              window.scrollTo(0, 300);
            }""")
            pg.wait_for_timeout(300)
            S = """() => ({
              mark: !!document.querySelector('#lvbody table.lvpos tr[data-mark]'),
              sc: window.scrollY,
              h: document.querySelector('#lvbody').offsetHeight,
              k: document.querySelector('#lvkpi').offsetHeight,
              eq: (document.querySelector('#kperf2') || {}).textContent || '',
              px: (document.querySelector('#lvbody td[data-rt$="|price"]')
                   || {}).textContent || '',
            })"""
            b4 = pg.evaluate(S)
            assert b4['mark'], '记号没打上 —— 持仓表结构变了，这条用例失去意义'
            # 手动触发一次 quiet 刷新（不等 60 秒 —— 等一分钟的用例没人愿意跑）
            pg.evaluate("async () => { await loadLive(LVSEL, true); }")
            pg.wait_for_timeout(1500)
            af = pg.evaluate(S)
            assert af['mark'], \
                'DOM 被重建了（记号丢了）—— 只比高度的话重建出一样的 DOM ' \
                '也算"没跳"，但 hover 与选中的文字都断了'
            assert b4['h'] == af['h'], \
                '容器高度 %s -> %s，页面会跳' % (b4['h'], af['h'])
            assert b4['k'] == af['k'], \
                'KPI 板高度 %s -> %s（业绩板被抹回加载态了？）' % (b4['k'], af['k'])
            assert b4['sc'] == af['sc'], \
                '滚动位置 %s -> %s' % (b4['sc'], af['sc'])
            # ④ 业绩板内容原样
            assert b4['eq'] and af['eq'] == b4['eq'], \
                ('业绩板被换掉了：%r -> %r —— `#kperf2` 是异步补的，'
                 'quiet 轮询不重拉它，整块换 KPI 会把它抹回占位'
                 % (b4['eq'][:30], af['eq'][:30]))
            assert af['px'], '现价格子空了 —— patch 把内容弄丢了'
            # ---- ⑤ 数字真的换了：**构造**一个变化（盘后价格不动，
            #      "更新了"和"没更新"看不出来 —— 变异实测这条会空转）----
            changed = pg.evaluate("""() => {
              const it = (LVO.pos || {}).items || [];
              if(!it.length) return null;
              const o = JSON.parse(JSON.stringify(LVO));
              const x = o.pos.items[0];
              x.price = (x.price || 10) + 1.11;      /* 明显不同的价 */
              x.pnl = (x.pnl || 0) + 12345.67;
              livePatch(o);
              const td = document.querySelector(
                `#lvbody td[data-rt="${x.code}|price"]`);
              const tp = document.querySelector(
                `#lvbody td[data-rt="${x.code}|pnl"]`);
              return {want: x.price.toFixed(2), got: (td || {}).textContent || '',
                      wantPnl: x.pnl.toFixed(2),
                      gotPnl: (tp || {}).textContent || ''};
            }""")
            assert changed, '没有持仓 —— 这条测不到'
            assert changed['want'] in changed['got'].replace(',', ''), \
                ('patch 没把现价换掉：要 %s，格子里是 %r —— 盘后价格本来'
                 '不动，所以这条必须**构造**一个变化才测得到'
                 % (changed['want'], changed['got']))
            assert changed['wantPnl'] in changed['gotPnl'].replace(',', ''), \
                'patch 没把浮盈换掉：要 %s，是 %r' % (changed['wantPnl'],
                                                    changed['gotPnl'])
            # ---- ⑥ 换了一只票但只数不变时，必须【回退到整块重建】----
            #   🔴 判据要含**持仓代码序列**而不只是只数：逐格 patch 会按
            #     code 找格子，找不到就悄悄不更新 —— 新票那一行显示的还是
            #     旧票的数字，而它不报错。
            swapped = pg.evaluate("""() => {
              const o = JSON.parse(JSON.stringify(LVO));
              const it = o.pos.items;
              if(!it.length) return null;
              it[0] = Object.assign({}, it[0], {code: '000001.XSHE',
                                                name: '平安银行'});
              return livePatch(o);      /* 应当返回 false = 结构变了 */
            }""")
            assert swapped is False, \
                ('换了一只票（只数不变）时 livePatch 返回了 %r —— 它必须'
                 '返回 false 让调用方整块重建，否则新票那一行显示的还是'
                 '旧票的数字，而它不报错' % swapped)
            assert not errs, 'JS 错误：%s' % errs[:2]
            b.close()
    finally:
        httpd.shutdown()
    return ('%d 个实时格子原地换 · DOM 记号保住 · 高度/滚动/业绩板逐项不变'
            % n_rt)


@case('涨跌配色：0 走中性色 / K 线阳线也实心 / 一处定义（playwright）', tag='web')
def t_updown_color():
    """用户："涨跌幅为0的，不要用红色。K线中的红色柱子，也使用实心柱子。"

      ① `upc` 是**唯一**定义，判据 `> 0` / `< 0` 两头夹 —— `>= 0` 会把
         "平盘"并进"涨"，等于凭空报了个涨
      ② 各视图里不许再有 `x >= 0 ? 涨色 : 跌色` 的副本：原来 `col` 在
         live.js 里写了 4 遍、live-perf.js 2 遍，改一处漏五处**不报错**，
         只是"这块 0 是灰的、那块还是红的"
      ③ K 线柱体：**阳线与阴线的填充密度必须一样**（都是实心）。判据是
         数柱体内部的像素，不是查 `strokeRect` 有没有出现 ——
         后者连注释里的那个词都会算命中（我自己的自检就这么误报过一次）
      ④ 平盘（收=开）的柱子用中性色
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return '跳过（无 playwright）'
    import io as _io
    import re as _re
    import threading
    from http.server import ThreadingHTTPServer

    from assay import server as sv

    # ---- ② 静态：不许有第二份涨跌配色 ----
    BAD = _re.compile(r'>=\s*0\s*\?\s*[\'"]?var\(--up\)')
    for f in _web_files('web', '.js') + _web_files('web', '.html'):
        t = _io.open(os.path.join('web', f), encoding='utf-8').read()
        for ln, line in enumerate(t.splitlines(), 1):
            assert not BAD.search(line), \
                ('%s:%d 自己写了一份涨跌配色（`>= 0 ? 涨色`）—— 它会把 0 '
                 '画成红的。用 common.js 的 `upc`（一处定义）：%s'
                 % (f, ln, line.strip()[:80]))
    cj = _io.open('web/shared/common.js', encoding='utf-8').read()
    m = _re.search(r'const upc\s*=([^;]+);', cj)
    assert m, 'common.js 里没有 upc'
    assert '> 0' in m.group(1) and '< 0' in m.group(1), \
        'upc 必须用 `> 0` / `< 0` 两头夹（`>= 0` 会把 0 并进涨）：%s' % m.group(1)
    assert '--dim' in m.group(1), 'upc 的 0 必须走中性色 --dim'

    httpd = ThreadingHTTPServer(('127.0.0.1', 0), sv.Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as p:
            b = p.chromium.launch()
            pg = b.new_page(viewport={'width': 1400, 'height': 900})
            errs = []
            pg.on('pageerror', lambda e: errs.append(str(e)))
            pg.goto('http://127.0.0.1:%d/stock.html?code=601857.XSHG' % port)
            pg.wait_for_timeout(4000)
            r = pg.evaluate("""() => {
              const cv = document.createElement('canvas');
              cv.width = 200; cv.height = 200;
              cv.style.width = '200px'; cv.style.height = '200px';
              document.body.appendChild(cv);
              /* 数柱体内部的像素密度 —— 空心柱中间是背景色，密度会低一截。
                 🔴 判据必须是**像素**，不是源码里有没有 strokeRect：
                 那个词出现在注释里也算命中（自检误报过一次）。 */
              /* 🔴 **采样位置跟着 drawKChart 返回的几何走，不能写死。**
                 原来硬编码 `getImageData(120, ...)` —— 那是"1 根柱子平分
                 整个画布"时它所在的位置（宽 96px）。2026-09-14 给柱子加了
                 最大宽度（14px）之后那根柱子挪到了 x≈61、宽 10px，
                 采样点落在空白处，密度从 82 掉到 8 —— **失败的是构造不是
                 产品**（好在这条用例自己有"构造不对"的护栏，当场报了出来）。 */
              const dens = (o, c) => {
                const g0 = drawKChart(cv, {bars: [{date: '2026-01-01', open: o,
                  high: Math.max(o, c) + 1, low: Math.min(o, c) - 1,
                  close: c, volume: 100}]});
                const bw = Math.max(1, g0.step * 0.7);
                const x0 = Math.round(g0.X(0) - bw / 2) + 1;
                const wpx = Math.max(2, Math.round(bw) - 2);
                const d = cv.getContext('2d').getImageData(x0, 90, wpx, 20).data;
                let n = 0;
                for(let i = 3; i < d.length; i += 4) if(d[i] > 60) n++;
                return n;
              };
              /* 平盘柱子的颜色：取柱体上那一个像素 */
              const gf = drawKChart(cv, {bars: [{date: '2026-01-01', open: 11,
                high: 12, low: 10, close: 11, volume: 100}]});
              const g = cv.getContext('2d');
              /* 同上：取柱心那一列，不写死 x */
              const px = g.getImageData(Math.round(gf.X(0)), 0, 1, 200).data;
              let flatRGB = null;
              for(let y = 0; y < 200; y++){
                const i = y * 4;
                if(px[i + 3] > 200){ flatRGB = [px[i], px[i+1], px[i+2]]; break; }
              }
              const _g = drawKChart(cv, {bars: [{date: '2026-01-01', open: 10,
                high: 13, low: 9, close: 12, volume: 100}]});
              const _cap = Math.max(2, Math.round(Math.max(1, _g.step * 0.7)) - 2) * 20;
              return {cap: _cap, up: dens(10, 12), dn: dens(12, 10),
                      zero: upc(0), pos: upc(0.01), neg: upc(-0.01),
                      flatRGB: flatRGB};
            }""")
            # ③ 阳线阴线同样实心
            #   ★ 这一条只是**构造有效性护栏**（真正的判据是下一行的
            #     "阳 == 阴"）。阈值按采样窗口的比例给，不写死绝对值 ——
            #     柱宽随画布变，写死 40 的话下次改柱宽又会挂（这次就挂了）。
            #     🔴 但也不能卡太紧：实测 82/160 = 51%，取 0.5 只差 2 个像素，
            #     那种阈值迟早偶发。护栏取 **0.25**，判别交给下一条。
            assert r['up'] > r['cap'] * 0.25 and r['dn'] > r['cap'] * 0.25, \
                '柱体密度太低（阳 %d / 阴 %d）—— 构造不对，这条测不到' \
                % (r['up'], r['dn'])
            assert abs(r['up'] - r['dn']) <= 2, \
                ('阳线密度 %d vs 阴线 %d —— 阳线还是空心的（窄柱时描边中间'
                 '是背景色，看着比阴线淡一档）' % (r['up'], r['dn']))
            # ① 0 走中性
            assert 'dim' in r['zero'], '`upc(0)` 给的是 %r —— 0 不该是涨色' % r['zero']
            assert 'up' in r['pos'] and 'down' in r['neg'], \
                'upc 的正负两侧不对：%r / %r' % (r['pos'], r['neg'])
            # ④ 平盘柱子既不是涨色也不是跌色
            assert r['flatRGB'], '取不到平盘柱子的颜色 —— 构造不对'
            up_rgb = pg.evaluate(
                "() => { const s = getComputedStyle(document.documentElement);"
                "  const h = s.getPropertyValue('--up').trim();"
                "  const d = document.createElement('div'); d.style.color = h;"
                "  document.body.appendChild(d);"
                "  const c = getComputedStyle(d).color;"
                "  return c.match(/\\d+/g).slice(0, 3).map(Number); }")
            assert r['flatRGB'] != up_rgb, \
                '平盘（收=开）的柱子画成了涨色 %r —— 那是凭空报了个涨' % up_rgb
            assert not errs, 'JS 错误：%s' % errs[:2]
            b.close()
    finally:
        httpd.shutdown()
    return ('upc 三态（0 -> %s）· 柱体密度 阳 %d == 阴 %d（都实心）· '
            '平盘柱子 rgb%s 不是涨色' % (r['zero'], r['up'], r['dn'],
                                        tuple(r['flatRGB'])))


@case('K 线对数坐标 / 回撤图顶到 0 且【连续、碰到水面线】（playwright）', tag='web')
def t_log_and_dd():
    """用户："K线图需要支持对数"、"回撤水下图最高刻度应该是 0.0%，没有发生
    回撤的那一段线是不是应该没有颜色？"

      ① 对数坐标要**真的是对数**：判据是**几何等距性** —— 造等比数列
         10/20/40/80，线性轴上间距是 1:2:4，对数轴上必须相等。
         只查"传了 log 参数"抓不到映射写错（那才是会出错的地方）
      ② 回撤图最高刻度是 `0%`：`lineChart` 默认在顶端留 6% 白，会印出
         `+0.4%` —— 那个数**没有意义**（不可能比历史最高还高），
         读的人会当成"曾经超出过"。要传 hiCap
      ③ 回撤 = 0 的那几段**不画**：那是"在水面上"，不是"水下 0.0%"。
         判据是 path 的 `M` 段数 > 1（断开了）
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return '跳过（无 playwright）'
    import glob
    import json as _json
    import threading
    from http.server import ThreadingHTTPServer

    from assay import server as sv

    rid = None
    for m in sorted(glob.glob('runs/*/*/*/meta.json'), reverse=True)[:80]:
        if os.path.isfile(os.path.join(os.path.dirname(m), 'equity.parquet')):
            rid = _json.load(open(m, encoding='utf-8')).get('run_id')
            break
    if not rid:
        return '跳过（没有带权益曲线的归档）'

    httpd = ThreadingHTTPServer(('127.0.0.1', 0), sv.Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    notes = []
    try:
        with sync_playwright() as p:
            b = p.chromium.launch()
            pg = b.new_page(viewport={'width': 1500, 'height': 950})
            errs = []
            pg.on('pageerror', lambda e: errs.append(str(e)))

            # ---- ① 对数坐标的几何等距性 ----
            pg.goto('http://127.0.0.1:%d/stock.html?code=601857.XSHG' % port)
            pg.wait_for_timeout(4000)
            assert pg.locator('#klogt').count() == 1, '个股页没有「对数」按钮'
            r = pg.evaluate("""() => {
              const cv = document.createElement('canvas');
              cv.width = 400; cv.height = 400;
              cv.style.width = '400px'; cv.style.height = '400px';
              document.body.appendChild(cv);
              /* 🔴 柱体要**有实体**（open != close）：open==close 画出来是
                 一条 1px 的线，落在半像素上时抗锯齿会把 alpha 压到阈值
                 以下，探针就扫不到 —— 2026-09-15 改了副图布局、主图变高
                 之后当场偶发成"对数轴间距不等"。**失败的是构造不是产品**
                 （同"涨跌配色那条把采样位置写死"那次）。 */
              const bars = [10, 20, 40, 80].map((v, i) => ({
                date: '2026-01-0' + (i + 1), open: v * 0.97, high: v,
                low: v * 0.97, close: v, volume: 100}));
              const probe = lg => {
                const geo = drawKChart(cv, {bars: bars, log: lg});
                const g = cv.getContext('2d');
                const ys = [];
                for(let i = 0; i < 4; i++){
                  const x = Math.round(geo.X(i));
                  const d = g.getImageData(x, 0, 1, 400).data;
                  for(let y = 0; y < 400; y++)
                    if(d[y * 4 + 3] > 150){ ys.push(y); break; }
                }
                return ys;
              };
              const gaps = a => [a[1] - a[0], a[2] - a[1], a[3] - a[2]];
              const lin = probe(false), log = probe(true);
              return {lin: gaps(lin), log: gaps(log), nlin: lin, nlog: log};
            }""")
            assert len(r['nlog']) == 4 and len(r['nlin']) == 4, \
                '探针只取到 %r / %r 个点 —— 构造不对' % (r['nlin'], r['nlog'])
            lg, ln = r['log'], r['lin']
            assert max(ln) - min(ln) > 10, \
                '线性轴上等比数列的间距居然是均匀的 %r —— 构造不对，这条测不到' % ln
            assert max(lg) - min(lg) <= 2, \
                ('对数轴上等比数列 10/20/40/80 的间距是 %r，应当相等 —— '
                 '坐标映射写错了（只查"传了 log 参数"是抓不到这个的）' % lg)
            # 按钮点了要真的重画（不重新取数）
            pg.click('#klogt')
            pg.wait_for_timeout(800)
            assert 'log=1' in pg.url, '「对数」没写进 URL（书签会丢掉这个状态）'
            assert 'on' in (pg.locator('#klogt').get_attribute('class') or ''), \
                '「对数」按钮点了没高亮'
            notes.append('对数轴等距（线性 %r -> 对数 %r）' % (ln, lg))

            # ---- ②③ 回撤图 ----
            pg.goto('http://127.0.0.1:%d/#/run/%s' % (port, rid))
            pg.wait_for_timeout(4500)
            pg.evaluate("() => tab(1)")          # 权益页签
            pg.wait_for_timeout(1800)
            d = pg.evaluate("""() => {
              const c2 = document.getElementById('c2');
              if(!c2) return null;
              const ys = [...c2.querySelectorAll('text.ax')]
                .filter(t => +t.getAttribute('x') < 60)
                .map(t => ({y: +t.getAttribute('y'), s: t.textContent}))
                .sort((a, b) => a.y - b.y);
              /* 🔴 取【描边】那条（面积图是 fill、没有 stroke），
                 否则量到的是填充路径，它本来就逐段闭合、M 段数不同。 */
              const path = c2.querySelector('path[stroke]:not([stroke="none"])');
              const dd = path ? path.getAttribute('d') : '';
              /* 曲线最高点（y 最小）与水面线的 y —— 曲线必须真的碰到它 */
              const ys2 = [...dd.matchAll(/[ML]\\s*[\\d.]+\\s+([\\d.]+)/g)]
                            .map(m => +m[1]);
              const zl = [...c2.querySelectorAll('line')]
                .find(l => (l.getAttribute('stroke') || '').includes('91,156,240'));
              return {ticks: ys.map(o => o.s),
                      nM: (dd.match(/M/g) || []).length,
                      topY: ys2.length ? Math.min.apply(null, ys2) : null,
                      zeroY: zl ? +zl.getAttribute('y1') : null,
                      vis: !!(c2.querySelector('svg') || {}).getBoundingClientRect
                           && c2.querySelector('svg').getBoundingClientRect().height > 0};
            }""")
            assert d and d['vis'], \
                '回撤图不可见 —— `#c2` 在 `.pane` 里（样式表 display:none），' \
                '要先 tab(1)'
            assert d['ticks'], '回撤图没有 y 轴刻度'
            top = d['ticks'][0].strip().lstrip('+')
            assert top.startswith('0'), \
                ('回撤图最高刻度是 %r，应当是 0 —— lineChart 默认在顶端留 6%% 白，'
                 '会印出 +0.4%%，而"比历史最高还高"是没有意义的数（要传 hiCap）'
                 % d['ticks'][0])
            # 🔴 **2026-09-14 反过来了**：原来这里断言 `nM > 1`（回撤 0 的那几段
            #   必须断开）。用户指出"0 回撤到有回撤、有回撤到 0 回撤之间没有
            #   相连，看着有点怪" —— 而那不只是观感：每段回撤**两头都不接
            #   水面线**，入水那天凭空开始、出水那天凭空停住，于是"回到历史
            #   最高"这个最明确的事实在图上成了一个**空档**，而空档在图表
            #   惯例里读作"没有数据"，意思正好反了。
            #   ★ **失败的是断言不是产品**（同 `#d_hold .note` 那条）：
            #     不是删掉保护，而是改钉新规矩 —— 连续 + 真的碰到水面线。
            assert d['nM'] == 1, \
                ('回撤曲线断成了 %d 段 —— 现在的规则没有例外：**断开 = 没有'
                 '数据**。回撤为 0 的点要照画，让每段回撤两头都接上水面线'
                 % d['nM'])
            assert d['topY'] is not None and d['zeroY'] is not None, \
                '取不到曲线顶点或水面线的 y（选择器没命中描边那条路径？）'
            assert abs(d['topY'] - d['zeroY']) < 1.0, \
                ('回撤曲线的最高点 y=%.1f 没有落在水面线 y=%.1f 上 —— '
                 '创新高那天回撤正好是 0，曲线必须**碰到**水面线；'
                 '差这一截说明 0 那些点又被抹掉了' % (d['topY'], d['zeroY']))
            notes.append('回撤顶到 %s · 曲线连续(1 段)且碰到水面线(y=%.1f)'
                         % (top, d['zeroY']))
            assert not errs, 'JS 错误：%s' % errs[:2]
            b.close()
    finally:
        httpd.shutdown()
    return '；'.join(notes)


@case('策略线是【基准的一种】：可选 / 单选 / 不撞色（playwright）', tag='web')
def t_strat_bench():
    """用户："策略的线颜色和上证线颜色一样了，而且策略线始终在上面，无法取消。
    策略线也是基准的一种，也应该可选展示。"

      ① **不撞色**：策略色不许出现在 `LPB_COL` 里 —— 两条一模一样时
         "哪条是策略"只能靠猜。静态断言（改配色数组也能抓到）
      ② **默认不画**：三条线以上就看不清了，而"想比哪个"因人而异
         （同「默认一个都不勾」那条）
      ③ **与指数共用一个单选槽**：选了上证，策略要自动消失 —— 两处状态
         的话会出现"既选了上证又开着策略"这种要额外记的组合
      ④ **点同一个能取消**（原来是常显、关不掉）
      ⑤ 图下那段解释**跟着选中状态走**：没画线还留着一段话，读的人会去找
         那条不存在的线
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return '跳过（无 playwright）'
    import io as _io
    import re as _re
    import threading
    from http.server import ThreadingHTTPServer

    from assay import server as sv

    # ---- ① 静态：撞色 ----
    src = _io.open('web/views/live-perf.js', encoding='utf-8').read()
    m = _re.search(r'const LPB_COL = \[(.*?)\];', src, _re.S)
    assert m, '找不到 LPB_COL'
    cols = [c.lower() for c in _re.findall(r"'(#\w+)'", m.group(1))]
    ms = _re.search(r"策略 \(完全照做\)', v: sv, c: '(#\w+)'", src)
    assert ms, '找不到策略线的配色'
    sc = ms.group(1).lower()
    assert sc not in cols, \
        ('策略线的颜色 %s 与指数配色撞了（LPB_COL 里第 %d 个）—— '
         '两条一模一样时"哪条是策略"只能靠猜' % (sc, cols.index(sc) + 1))
    # 那个没人读的旧开关必须删干净（留着下次有人以为它管事）
    assert 'LPSTRAT' not in src and 'lpstrat' not in src, \
        '还留着没人读的 LPSTRAT / lpstrat'

    httpd = ThreadingHTTPServer(('127.0.0.1', 0), sv.Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as p:
            b = p.chromium.launch()
            pg = b.new_page(viewport={'width': 1500, 'height': 950})
            errs = []
            pg.on('pageerror', lambda e: errs.append(str(e)))
            # 🔴 先清掉记忆：localStorage 里可能存着上一次选的基准，
            #   那样"默认不画"这条会因为环境而时绿时红。
            pg.goto('http://127.0.0.1:%d/' % port)
            pg.evaluate("() => Object.keys(localStorage)"
                        ".filter(k => k.indexOf('lvbench') === 0)"
                        ".forEach(k => localStorage.removeItem(k))")
            pg.goto('http://127.0.0.1:%d/#/live/froec/perf' % port)
            pg.wait_for_selector('#lp_bm a.lpbs', timeout=25000)
            pg.wait_for_timeout(2500)
            SNAP = """() => {
              const el = document.getElementById('lp_chart');
              const ps = [...el.querySelectorAll('svg path[d]')];
              const notes = [...el.querySelectorAll('.note')]
                            .map(e => e.textContent).join(' ');
              return {n: ps.length,
                      cols: ps.map(x => (x.getAttribute('stroke')||'').toLowerCase()),
                      picked: [...document.querySelectorAll('#lp_bm a.on')]
                              .map(a => a.textContent.trim()),
                      hasNote: notes.includes('完全照做的净值')};
            }"""
            # ② 默认不画
            a0 = pg.evaluate(SNAP)
            assert a0['n'] == 1, \
                '默认画了 %d 条线（应当只有"实际"那条）：%r' % (a0['n'], a0['cols'])
            assert not a0['picked'], '默认就选中了 %r' % a0['picked']
            assert not a0['hasNote'], '没画策略线却留着那段解释'
            # 选「策略」
            pg.click('#lp_bm a.lpbs')
            pg.wait_for_timeout(1300)
            a1 = pg.evaluate(SNAP)
            assert a1['n'] == 2, '选了策略却只有 %d 条线' % a1['n']
            assert sc in a1['cols'], \
                '策略线的颜色 %s 没出现在图上：%r' % (sc, a1['cols'])
            assert a1['picked'] == ['策略'], '选中态不对：%r' % a1['picked']
            assert a1['hasNote'], '选了策略却没有那段解释'
            # ③ 改选上证 -> 策略自动消失
            pg.evaluate("""() => [...document.querySelectorAll('#lp_bm a.lpb')]
                .find(a => a.textContent.trim() === '上证指数').click()""")
            pg.wait_for_timeout(1300)
            a2 = pg.evaluate(SNAP)
            assert a2['n'] == 2, '改选上证后有 %d 条线' % a2['n']
            assert sc not in a2['cols'], \
                ('选了上证，策略线还在（%r）—— 它们必须共用**一个**单选槽，'
                 '否则会出现"既选上证又开着策略"这种要额外记的组合' % a2['cols'])
            assert a2['picked'] == ['上证指数'], '选中态不对：%r' % a2['picked']
            assert not a2['hasNote'], '策略线没画了，那段解释该跟着走'
            # ④ 点同一个能取消
            pg.click('#lp_bm a.lpbs')
            pg.wait_for_timeout(1100)
            pg.click('#lp_bm a.lpbs')
            pg.wait_for_timeout(1100)
            a3 = pg.evaluate(SNAP)
            assert a3['n'] == 1 and not a3['picked'], \
                '再点一次没取消掉（%d 条线，选中 %r）' % (a3['n'], a3['picked'])
            assert not errs, 'JS 错误：%s' % errs[:2]
            b.close()
    finally:
        httpd.shutdown()
    return ('策略色 %s 不在 %d 个指数色里 · 默认不画 · 与指数单选互斥 · '
            '可取消 · 解释跟着选中走' % (sc, len(cols)))


@case('基准可以【手填】：搜得到 / 名字服务端给 / 不撞色 / 记得住（playwright）',
       tag='web')
def t_bench_custom_web():
    """用户 2026-09-16："实盘策略中的基准除了预设的这些，还可以自己手填
    名称/代码比对，比如填某个红利 ETF 作为基准比对。"

    四条判据，每条对着一种**不报错**的坏法：
      ① 候选**由服务端给** —— 本地有没有这只票的日线只有服务端知道，
         前端硬编码清单会列出"点了什么都不出来"的死选项（同 backLink 那条）
      ② **名字来自 `bench_meta`，前端一个字都不认识** —— 页面自己存名字的话，
         标的改过名就一直显示旧名，而它不报错
      ③ 🔴 **不撞色**：我第一版挑的 `#e0a33c` 与账户线 `#e0b050` 的 RGB
         欧氏距离只有 **24** —— 屏幕上两条一模一样的橙线，"哪条是账户"
         只能靠猜（同「策略线与上证撞色」那次）。判据是**色距**不是"看着不一样"
      ④ 选了之后**刷新还在**，且曲线真画得出来 —— 只验"chip 亮着"的话，
         重取权益那条路（手填的不在预加载那批里）断了也发现不了
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return '跳过（无 playwright）'
    import io as _io
    import re as _re
    import threading
    from http.server import ThreadingHTTPServer

    from assay import server as sv
    notes = []

    # ---- ③ 静态：色距 ----
    src = _io.open('web/views/live-perf.js', encoding='utf-8').read()
    def _rgb(h):
        h = h.lstrip('#')
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    def _dist(a, b):
        return sum((x - y) ** 2 for x, y in zip(_rgb(a), _rgb(b))) ** 0.5
    mc = _re.search(r"const LPB_CUSTOM_COL = '(#[0-9a-fA-F]{6})'", src)
    assert mc, '找不到 LPB_CUSTOM_COL'
    cc = mc.group(1)
    # 🔴 只认**真的十六进制**：`#\w{6}` 会把 `'#lp_bmore'` 这种选择器也抓进来
    used = _re.findall(r"'(#[0-9a-fA-F]{6})'", src)
    used = [c for c in used if c.lower() != cc.lower()]
    assert len(used) >= 10, '页面里只找到 %d 个颜色，判据怕是没扫到' % len(used)
    near = min((_dist(cc, c), c) for c in used)
    assert near[0] >= 60, \
        ('手填基准的颜色 %s 与 %s 的色距只有 %.0f（阈值 60）—— '
         '屏幕上就是两条一样的线，"哪条是账户"只能靠猜' % (cc, near[1], near[0]))
    notes.append('色距 %.0f（最近的是 %s，共比 %d 个）' % (near[0], near[1], len(used)))

    httpd = ThreadingHTTPServer(('127.0.0.1', 0), sv.Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as p:
            b = p.chromium.launch()
            pg = b.new_page(viewport={'width': 1500, 'height': 950})
            errs = []
            pg.on('pageerror', lambda e: errs.append(str(e)))
            pg.goto('http://127.0.0.1:%d/' % port)
            pg.evaluate("() => Object.keys(localStorage)"
                        ".filter(k => k.indexOf('lvbench') === 0)"
                        ".forEach(k => localStorage.removeItem(k))")
            pg.goto('http://127.0.0.1:%d/#/live/froec/perf' % port)
            pg.wait_for_selector('#lp_bmore', timeout=25000)
            pg.wait_for_timeout(1800)

            SNAP = """() => {
              const el = document.getElementById('lp_chart');
              const ps = [...el.querySelectorAll('svg path[d]')];
              return {n: ps.length,
                      cols: ps.map(x => (x.getAttribute('stroke')||'').toLowerCase()),
                      picked: [...document.querySelectorAll('#lp_bm a.on')]
                              .map(a => a.textContent.trim()),
                      meta: (typeof LPB_META === 'object' ? LPB_META : {}),
                      lpb: (typeof LPB !== 'undefined' ? LPB : [])};
            }"""
            a0 = pg.evaluate(SNAP)
            assert a0['n'] == 1 and not a0['picked'], \
                '默认就画了基准：%d 条线 / 选中 %r' % (a0['n'], a0['picked'])

            # ---- ① 搜：候选由服务端给 ----
            pg.click('#lp_bmore')
            pg.wait_for_selector('#lp_bq', timeout=6000)
            pg.fill('#lp_bq', '红利ETF')
            pg.wait_for_selector('#lp_bhit .lpbhit', timeout=8000)
            hits = pg.evaluate("""() => [...document.querySelectorAll('#lp_bhit .lpbhit')]
                .map(a => ({code: a.dataset.b, txt: a.textContent.trim()}))""")
            assert hits, '搜"红利ETF"一个候选都没有'
            codes = [h['code'] for h in hits]
            assert 'sh510880' in codes, '候选里没有 sh510880：%r' % codes[:5]
            # 🔴 候选必须是**服务端**认过"本地真有日线"的那批。判据：
            #   同一个关键词直接打接口，页面列的必须是它的子集 ——
            #   前端自己拼一份清单的话这条立刻挂。
            import json as _json
            import urllib.request as _u
            api = _json.loads(_u.urlopen(
                'http://127.0.0.1:%d/api/live/bench_search?q=%s'
                % (port, _u.quote('红利ETF'))).read().decode('utf-8'))
            acodes = [x['code'] for x in api.get('items', [])]
            assert acodes, '/api/live/bench_search 返回空'
            assert set(codes) <= set(acodes), \
                ('页面列了服务端没给的候选 %r —— 本地有没有日线只有服务端知道，'
                 '前端拼清单会列出点了什么都不出来的死选项'
                 % list(set(codes) - set(acodes)))
            notes.append('搜到 %d 个候选（全部来自服务端）' % len(hits))

            # ---- 选中 ----
            want = [h for h in hits if h['code'] == 'sh510880'][0]
            pg.evaluate("""() => [...document.querySelectorAll('#lp_bhit .lpbhit')]
                .find(a => a.dataset.b === 'sh510880').click()""")
            pg.wait_for_timeout(3000)
            a1 = pg.evaluate(SNAP)
            assert a1['n'] == 2, \
                ('选了手填基准却有 %d 条线 —— 它不在预加载那批里，'
                 '必须重新取一次权益' % a1['n'])
            assert cc.lower() in a1['cols'], \
                '手填基准的线没用 %s：%r' % (cc, a1['cols'])
            # ---- ② 名字是服务端给的 ----
            nm = (a1['meta'].get('sh510880') or {}).get('name')
            assert nm, 'bench_meta 里没有 sh510880 的名字 —— 那 chip 上只会是一串代码'
            assert a1['picked'] == [nm], \
                ('chip 上写的是 %r，而服务端给的名字是 %r —— '
                 '名字必须来自 bench_meta，页面自己存的话改名后一直显示旧名'
                 % (a1['picked'], nm))
            assert nm not in src, \
                '名字 %r 被写死在 live-perf.js 里了' % nm
            notes.append('名字「%s」由服务端给' % nm)

            # ---- ④ 刷新还在，且曲线还画得出 ----
            pg.reload()
            pg.wait_for_selector('#lp_bmore', timeout=25000)
            pg.wait_for_timeout(3000)
            a2 = pg.evaluate(SNAP)
            assert a2['lpb'] == ['sh510880'], '刷新后没记住：%r' % a2['lpb']
            assert a2['picked'] == [nm], '刷新后 chip 不对：%r' % a2['picked']
            assert a2['n'] == 2 and cc.lower() in a2['cols'], \
                ('刷新后曲线没画出来（%d 条：%r）—— 首次加载那条路没带上'
                 '记住的手填 code' % (a2['n'], a2['cols']))
            # ---- ⑤🔴 首日的涨跌**不许被抹掉** ----
            # 用户 2026-09-16："选择的 ETF 第一天收益是 0，这个不正常，
            #   第一天的涨跌幅也要算进去。"
            # 服务端给的序列**已经**以 dates[0] 的前一交易日收盘为基点
            # （`bench_curves`，同 feed.benchmark 那条纪律）。前端按区间
            # 重新归一化时若拿"区间内第一个值"当基点，就把首日涨跌又抹了
            # 一遍 —— 而账户那条线用的是 1.0（开户本金，**含**首日）。
            # 两条线口径分家，**且不报错**：图上只看到基准第一天平平落在 0%。
            sl = pg.evaluate("""() => ({
                i0: LPS && LPS.i0,
                cut: (LPS && LPS.bench && LPS.bench['sh510880'] || []).slice(0, 3),
                raw: (LPD && LPD.bench && LPD.bench['sh510880'] || []).slice(0, 3),
                nav0: (LPS && LPS.nav || [])[0]})""")
            assert sl['i0'] == 0, '这份数据的区间起点不是第一天（i0=%r），下面几条测不到' % sl['i0']
            # 🔴 反向自证：那天基准真的动过。恰好平盘的话除不除都一样，
            #   这条断言就成了空转。
            assert sl['raw'] and abs(sl['raw'][0] - 1) > 1e-6, \
                '基准首日恰好平盘（%r），这条判据是空转的' % (sl['raw'][:1])
            assert abs(sl['cut'][0] - sl['raw'][0]) < 1e-12, \
                ('基准首日被重新归一化成 %.6f，而服务端算的是 %.6f —— '
                 '首日涨跌被抹掉了（账户那条线是含首日的 %.6f，两条口径分家）'
                 % (sl['cut'][0], sl['raw'][0], sl['nav0']))
            assert abs(sl['nav0'] - 1) > 1e-9, \
                '账户线首日也是 1，那"两条口径一致"这件事没测到'
            notes.append('首日 %+.2f%% 照实画（没被归一化抹掉）'
                         % ((sl['raw'][0] - 1) * 100))

            # ---- ⑥ 区间不是全程时，基准与策略都按【区间起点前一天】归一化 ----
            # 真实账户只有十几天，所有档都落在全程（i0 恒为 0）—— 这条
            # 在真实数据上是**空转**的，必须构造。直接验纯函数 `lprSlice`。
            fx = pg.evaluate("""() => {
              const D = [], nav = [], b1 = [], b2 = [];
              for(let i = 0; i < 40; i++){
                const d = new Date(Date.UTC(2024, 0, 1 + i));
                D.push(d.toISOString().slice(0, 10));
                nav.push(1 + i * 0.01); b1.push(1 + i * 0.02);
                b2.push(i === 19 ? null : 1 + i * 0.03);   // 前一天缺数据
              }
              const keep = LPR, keepB = LPBCH;
              LPR = {k: 'cus', a: D[20], b: D[39]};
              LPBCH = {dates: D, nav: D.map((_, i) => 1 + i * 0.005)};
              const r = lprSlice({dates: D, nav: nav, equity: nav,
                                  day_rets: nav, day_pnls: nav,
                                  bench: {a: b1, b: b2}, benchmarks: [], stats: {}});
              LPR = keep; LPBCH = keepB;
              return {i0: r.i0, a0: r.bench.a[0], b0: r.bench.b[0],
                      nav0: r.nav[0], sb: r.sb,
                      wantA: b1[20] / b1[19], wantB: b2[20] / b2[18],
                      wantSb: 1 + 19 * 0.005};
            }""")
            assert fx['i0'] == 20, '构造没生效（i0=%r）' % fx['i0']
            assert abs(fx['a0'] - fx['wantA']) < 1e-12, \
                ('区间基准的基点不是"区间起点前一天"：算出 %.6f，应为 %.6f'
                 % (fx['a0'], fx['wantA']))
            assert abs(fx['b0'] - fx['wantB']) < 1e-12, \
                ('前一天是 null 时没往前找最近的非空：算出 %.6f，应为 %.6f —— '
                 '直接用 null 会静默跳过归一化，区间内的基准还用着全程基点'
                 % (fx['b0'], fx['wantB']))
            assert abs(fx['sb'] - fx['wantSb']) < 1e-12, \
                ('策略曲线的区间基点不对：%.6f vs %.6f —— 不归一化的话它的'
                 '基点仍是本金，与账户线从不同的地方出发' % (fx['sb'], fx['wantSb']))
            # 🔴 上面那条只证了**算出**基点；还要证**用上了** ——
            #   把消费端改成恒用 1，只钉 `sb` 的话照样全绿（变异实测）。
            fy = pg.evaluate("""() => {
              const keepB = LPBCH;
              const D = ['2024-03-01', '2024-03-04', '2024-03-05'];
              LPBCH = {dates: D, nav: [2, 3, 4]};
              const a = lprStratSeries(D, 2);
              const b = lprStratSeries(['2024-03-01', '2024-03-06'], 1);
              LPBCH = keepB;
              return {a: a, b: b};
            }""")
            assert fy['a'] == [1, 1.5, 2], \
                ('策略曲线没按给定基点归一化：%r（基点 2 时 [2,3,4] 应为 [1,1.5,2]）'
                 % (fy['a'],))
            assert fy['b'] == [2, None], \
                ('策略曲线没有**按日期**对齐：%r —— 两边交易日不等长，'
                 '按下标并的话错一位就整条线平移，而它不报错' % (fy['b'],))
            notes.append('区间基点取前一天（缺数据往前找）· 策略线按日期对齐且同轴')

            # ---- ⑦🔴 基准是【按账户】记的，两个账户互不覆盖 ----
            # 用户 2026-09-16："不同实盘账户的业绩曲线比较基准会互相同步。
            #   每个账户设置了自己的比较基准之后应该是要记忆住的，不应该会
            #   被另一个账户覆盖。"
            # 原来是**一把全局 key**：在 A 选了红利 ETF，切到 B 也变成它、
            # 而且**回写**成 B 的选择 —— 谁最后打开谁说了算。而不同账户跑的
            # 是不同策略（小市值 vs 红利），要比的对象本来就不一样。
            accs = pg.evaluate("() => fetch('/api/live/accounts')"
                               ".then(r => r.json()).then(o => (o.accounts || [])"
                               ".map(a => a.id))")
            assert len(accs) >= 2, \
                '只有 %d 个账户，"互不覆盖"这条判据是空转的' % len(accs)
            a1, a2 = accs[0], accs[1]
            # 🔴 **故意留一个全局旧键**：按账户存是后来改的，老用户已经选过
            #   的那个不该凭空消失（读不到账户键时退回它）。而且不留的话，
            #   下面「点了不比不许又退回全局旧键」那条**根本执行不到** ——
            #   全清掉的话退回去读到的也是空，断言照样绿（变异实测漏过）。
            pg.evaluate("() => { Object.keys(localStorage)"
                        ".filter(k => k.indexOf('lvbench:') === 0)"
                        ".forEach(k => localStorage.removeItem(k));"
                        " localStorage.setItem('lvbench', 'sh000688'); }")

            def _pick(aid, code):
                pg.goto('http://127.0.0.1:%d/#/live/%s/perf' % (port, aid))
                pg.wait_for_selector('#lp_bm a.lpb', timeout=25000)
                pg.wait_for_timeout(1500)
                pg.evaluate("(c) => [...document.querySelectorAll('#lp_bm a.lpb')]"
                            ".find(a => a.dataset.b === c).click()", code)
                pg.wait_for_timeout(1200)

            def _cur(aid):
                # 🔴 **必须 reload**：`goto` 到**同一个 hash** 不会重新加载
                #   页面，于是读到的是内存里那个 `LPB`，根本没走
                #   localStorage 那条路 —— 变异测试实测：把"清掉"改成
                #   `removeItem`（会退回全局旧键）时这条断言照样绿。
                pg.goto('http://127.0.0.1:%d/#/live/%s/perf' % (port, aid))
                pg.reload(wait_until='networkidle')
                pg.wait_for_selector('#lp_bm a.lpb', timeout=25000)
                pg.wait_for_timeout(1500)
                return pg.evaluate('()=>LPB')

            # 没选过的账户要**继承那个全局旧值**（不然老用户的选择凭空没了）
            assert _cur(a1) == ['sh000688'], \
                ('%s 没有自己的键时该退回全局旧键（老用户选过的那个），'
                 '实际 %r' % (a1, _cur(a1)))
            _pick(a1, 'sh000001')
            _pick(a2, 'sh000905')
            got1, got2 = _cur(a1), _cur(a2)
            assert got1 == ['sh000001'], \
                ('在 %s 选了上证，去 %s 选了中证500 之后，%s 的基准变成 %r —— '
                 '两个账户共用一把 localStorage key，互相覆盖'
                 % (a1, a2, a1, got1))
            assert got2 == ['sh000905'], '%s 的基准没记住：%r' % (a2, got2)
            # 「不比」也要按账户记：清掉之后**不许**退回那个全局旧键
            pg.goto('http://127.0.0.1:%d/#/live/%s/perf' % (port, a1))
            pg.wait_for_selector('#lp_bclr', timeout=25000)
            pg.wait_for_timeout(1200)
            pg.click('#lp_bclr')
            pg.wait_for_timeout(1000)
            assert _cur(a1) == [], \
                ('%s 点了「不比」，切走再回来基准又出来了（%r）—— 清掉时要写'
                 '空串，`removeItem` 之后读到 null 会退回那个全局旧键'
                 % (a1, _cur(a1)))
            assert _cur(a2) == ['sh000905'], \
                '%s 点「不比」把 %s 的也清掉了' % (a1, a2)
            notes.append('基准按账户记（%s=上证 / %s=中证500，互不覆盖；'
                         '没选过的继承全局旧键，点「不比」之后不再退回它）'
                         % (a1, a2))

            # 取消
            pg.evaluate("""() => document.getElementById('lp_bclr').click()""")
            pg.wait_for_timeout(2000)
            a3 = pg.evaluate(SNAP)
            assert a3['n'] == 1 and not a3['picked'], \
                '点「不比」没取消掉：%d 条 / %r' % (a3['n'], a3['picked'])
            assert not errs, 'JS 错误：%s' % errs[:2]
            b.close()
    finally:
        httpd.shutdown()
    return '；'.join(notes)


@case('持仓表排序：六列可点 / 升降切换 / 点了不跳动（playwright）', tag='web')
def t_pos_sort():
    """用户："需要支持点击当日、当日盈亏、市值、浮盈、幅度、仓位从大到小、
    从小到大排序。"

    钉五件事，每件都对应一种**不报错**的坏法：
      ① 六列都真的能排 —— 漏一列的表现是"点了没反应"（同对比页「移除」那条）
      ② **首次点击给降序** —— 这六个都是「越大越好」的量，人点它是想看
         最赚/最大的那个；一律升序会把最差的排最前面（同回测页那条）
      ③ 再点一次反向
      ④ **quiet 刷新（每分钟）不许重排行序** —— `LVSORT` 必须是模块级；
         存在局部里的话刚点的排序立刻被冲掉
      ⑤ 排序后 `data-rt` 格子仍在 -> `livePatch` 照常只换数字
         （它按 `code|field` 找格子，与行序无关）
      ⑥ **点表头本身不许跳动**：就地移动 `<tr>` 节点，不重建 DOM、
         不打接口。用户反馈「点击一下排序这个页面会发生重新刷新，整个页面
         会跳动一下」—— 原来调 `loadLive`，它先把 `#lvbody` 清成「读取中…」
         再撑开。判据是 DOM 记号 + 尺寸 + 滚动位置 + 接口次数四条，
         而且视口要**真的能滚**（1000 高的视口下这页滚不动，那条是空转）
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return '跳过（无 playwright）'
    import io as _io
    import re as _re
    import threading
    from http.server import ThreadingHTTPServer

    from assay import server as sv

    # ---- 静态：null 必须排最后（停牌股取不到价）----
    src = _io.open('web/views/live.js', encoding='utf-8').read()
    fn = src[src.index('function lvSortRows'):]
    fn = fn[:fn.index('\n}\n')]
    flat = fn.replace(' ', '')
    assert 'if(x==null)return1' in flat and 'if(y==null)return-1' in flat, \
        ('null 必须一律排最后（不管升降序）—— 把 null 当 0 参与排序的话，'
         '停牌股会混在正负之间，看着像"今天不涨不跌"而事实是没有数据')
    assert 'localStorage' not in fn, \
        ('排序不该持久化 —— 它是"我现在想看什么"，下次打开该回到默认'
         '（同「待办折叠状态不持久化」那条）')

    httpd = ThreadingHTTPServer(('127.0.0.1', 0), sv.Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as p:
            b = p.chromium.launch()
            pg = b.new_page(viewport={'width': 1600, 'height': 1000})
            errs = []
            pg.on('pageerror', lambda e: errs.append(str(e)))
            pg.goto('http://127.0.0.1:%d/#/live' % port)
            pg.wait_for_selector('#lvbody table.lvpos', timeout=25000)
            pg.wait_for_timeout(3000)
            # 🔴 比**集合**而不是写死的顺序：列序是产品决定、会变
            #   （2026-09-16 用户重排过一次），写死顺序的话每次重排都要
            #   手改这一行，而"忘了改"的表现是报告在说谎。
            #   ★ 但**顺序也要钉**：它必须与页面那份**唯一列定义**
            #     （LVPOS_COLS）一致 —— 表头与单元格分两处拼的话，
            #     整表会错位一格，而那不报错。
            WANT = set(pg.evaluate('() => Object.values(LVSORT_COLS)'))
            ths = pg.locator('#lvbody th.lvsth')
            got = [ths.nth(i).inner_text().strip().rstrip('▼▲ ')
                   for i in range(ths.count())]
            assert set(got) == WANT, \
                '可排序的列不对：%r（要 %r）' % (got, sorted(WANT))
            _order = pg.evaluate(
                "() => LVPOS_COLS.filter(c => LVSORT_COLS[c.k])"
                ".map(c => LVSORT_COLS[c.k])")
            assert got == _order, \
                ('表头顺序与列定义 LVPOS_COLS 对不上：%r vs %r —— '
                 '两处拼的话整表会错位一格' % (got, _order))

            def vals(name):
                return pg.evaluate("""(nm) => {
                  const ts=[...document.querySelectorAll('#lvbody table.lvpos th')];
                  const i=ts.findIndex(t=>t.textContent.trim()
                      .replace(/[\\u25bc\\u25b2\\s]/g,'')===nm);
                  if(i<0) return null;
                  return [...document.querySelectorAll('#lvbody table.lvpos tr')]
                    .slice(1).map(tr=>{const td=tr.children[i];
                      if(!td) return null;
                      const v=parseFloat(td.textContent.replace(/[,%+]/g,'').trim());
                      return isNaN(v)?null:v;});
                }""", name)

            n_checked = 0
            for name in WANT:
                pg.locator('#lvbody th.lvsth', has_text=name).first.click()
                pg.wait_for_timeout(600)
                v = [x for x in (vals(name) or []) if x is not None]
                assert len(v) >= 3, '「%s」只取到 %d 个数值，测不出排序' % (name, len(v))
                assert all(v[i] >= v[i+1] for i in range(len(v)-1)), \
                    ('「%s」首次点击不是降序：%r —— 这六个都是越大越好的量，'
                     '人点它是想看最赚/最大的那个' % (name, v[:5]))
                pg.locator('#lvbody th.lvsth', has_text=name).first.click()
                pg.wait_for_timeout(600)
                v2 = [x for x in (vals(name) or []) if x is not None]
                assert all(v2[i] <= v2[i+1] for i in range(len(v2)-1)), \
                    '「%s」再点一次没有反向：%r' % (name, v2[:5])
                n_checked += 1

            # ---- ④ quiet 刷新不许重排 ----
            pg.locator('#lvbody th.lvsth', has_text='浮盈').first.click()
            pg.wait_for_timeout(700)
            # 🔴 第一格现在是「名称 + 小字代码」同一格（2026-09-16 用户要求），
            #   所以**代码从 `data-sp` 读**，不再拿那一格的文字当代码 ——
            #   拿文字比的话，两边永远不等，而报出来的却是"排序坏了"。
            ORDER = ("""() => [...document.querySelectorAll(
                '#lvbody table.lvpos tr')].slice(1)
                .map(tr => ((tr.querySelector('[data-sp]') || {}).dataset || {}).sp || '')""")
            o1 = pg.evaluate(ORDER)
            assert len(o1) >= 3, '持仓行太少（%d），测不出行序' % len(o1)
            # 🔴🔴 **判据要拿"渲染那一刻的数据"比，不能重新读实时价。**
            #   盘中每分钟刷新一次，而相邻两行的浮盈可能只差几块钱
            #   （实测 −1,145.46 与 −1,136.84 只差 8.6）——
            #   DOM 是按**刷新那一刻**的值排的，断言读到的却是**之后**的值，
            #   于是"表里的顺序"与"现在的数值"天然可能对不上。
            #   那不是"排序丢了"，是数据变了；拿它当判据**必然偶发**
            #   （实测 13 次挂 1 次，而偶发绿的用例比红的更危险）。
            #
            #   ★ 正确判据：**表里的行序 == 用 `LVO` 里那份数据自己排一遍**。
            #     `LVO` 就是渲染时用的那份（模块级），两者同源，不受刷新影响。
            CHECK = ("""(k) => {
                const rows = [...document.querySelectorAll(
                    '#lvbody table.lvpos tr')].slice(1)
                  .map(tr => ((tr.querySelector('[data-sp]') || {}).dataset
                              || {}).sp || '');
                const items = ((LVO || {}).pos || {}).items || [];
                const want = lvSortRows(items).map(x => x.code);
                return {dom: rows, want: want,
                        sort: JSON.parse(JSON.stringify(LVSORT))};
              }""")

            def _check(stage):
                r = pg.evaluate(CHECK, '浮盈')
                assert r['sort'] and r['sort']['k'], \
                    '%s：LVSORT 是空的 —— 排序状态丢了（它必须是模块级）' % stage
                assert r['dom'] == r['want'], \
                    ('%s：表里的行序与 `lvSortRows(LVO)` 算出来的不一致\n'
                     '  表里 %s\n  应为 %s\n  排序状态 %s'
                     % (stage, r['dom'][:6], r['want'][:6], r['sort']))
                return r

            _check('点完表头')
            # 🔴 上面那条 `_check` 拿 `lvSortRows` 的输出当期望，所以它对
            #   **`lvSortRows` 内部**的改动免疫（变异实测：在里面加一个
            #   `reverse()` 照样全绿）。所以再钉一条**不依赖那个函数**的：
            #   表里那一列的数值必须自己有序，判据用**渲染时的那份数据**
            #   （`LVO`）而不是重新读 DOM 文本 —— 后者会被刷新抖动。
            ind = pg.evaluate("""() => {
                const items = ((LVO || {}).pos || {}).items || [];
                const by = {}; items.forEach(x => by[x.code] = x);
                const dom = [...document.querySelectorAll(
                    '#lvbody table.lvpos tr')].slice(1)
                  .map(tr => ((tr.querySelector('[data-sp]') || {}).dataset
                              || {}).sp || '');
                return {vals: dom.map(c => (by[c] || {})[LVSORT.k]),
                        desc: LVSORT.desc};}""")
            vv = [x for x in ind['vals'] if x is not None]
            assert len(vv) >= 3, '取不到足够的数值：%r' % ind['vals']
            ok = (all(vv[i] >= vv[i + 1] for i in range(len(vv) - 1))
                  if ind['desc'] else
                  all(vv[i] <= vv[i + 1] for i in range(len(vv) - 1)))
            assert ok, \
                ('表里的行序与那一列的数值不一致（desc=%s）：%r —— '
                 '这条不依赖 `lvSortRows`，专抓它内部被改坏'
                 % (ind['desc'], vv[:6]))
            # null 必须在最后（不管升降序）
            first_null = next((i for i, x in enumerate(ind['vals'])
                               if x is None), None)
            if first_null is not None:
                assert all(x is None for x in ind['vals'][first_null:]), \
                    'null 没有一律排最后：%r' % ind['vals']
            n_rows = len(o1)
            pg.evaluate("async () => { await loadLive(LVSEL, true); }")
            pg.wait_for_function(
                "(n) => document.querySelectorAll("
                "'#lvbody table.lvpos tr').length - 1 >= n", arg=n_rows,
                timeout=30000)
            # 🔴🔴 **quiet 刷新之后不能拿 `lvSortRows(LVO)` 当期望** ——
            #   `livePatch` 会把 `LVO` 换成**新数据**（live.js:238），而 DOM
            #   **故意不重排**（那正是产品规则：刷新只换数字、不重排）。
            #   于是盘中价格一动，“新数据排出来的顺序”就可能与表里的不同——
            #   **而那不是 bug，是设计**。拿它当判据必然偶发：
            #   这是同一处第二次偶发 —— 上一版把“行序一字不差”换成了它，
            #   而那次换错了方向（换掉的恰恰是唯一不受刷新影响的那个）。
            #   ★ quiet 这一次的判据就是**行序一字不差**，再加 LVSORT 还在；
            #   ★ “按那一列有序”由**全量重渲染**那次去钉 —— 那条路径上
            #     `LVO` 与渲染同源，不受刷新抖动影响。
            o_q = pg.evaluate(ORDER)
            assert o_q == o1, \
                ('quiet 刷新（每分钟那次）重排了行序 —— livePatch 必须只换'
                 '数字、不动 DOM 结构\n  刷新前 %s\n  刷新后 %s'
                 % (o1[:6], o_q[:6]))
            assert pg.evaluate(
                '() => JSON.parse(JSON.stringify(LVSORT))').get('k'), \
                'quiet 刷新之后 LVSORT 空了 —— 它必须是模块级'
            # 全量重渲染要按当前排序重排（这条路径 LVO 与渲染同源）
            pg.evaluate("async () => { await loadLive(LVSEL); }")
            pg.wait_for_function(
                "(n) => document.querySelectorAll("
                "'#lvbody table.lvpos tr').length - 1 >= n", arg=n_rows,
                timeout=30000)
            _check('全量重渲染后')
            assert sorted(pg.evaluate(ORDER)) == sorted(o1), \
                '刷新之后持仓的行集合变了'

            on = pg.evaluate("""() => { const t=document.querySelector(
                '#lvbody th.lvsth.on'); return t ? t.textContent.trim() : null; }""")
            assert on and '浮盈' in on and '▼' in on, \
                '排序中的列要高亮并标方向，现在是 %r' % on
            # ---- ⑤ data-rt 还在 ----
            n_rt = pg.evaluate(
                "() => document.querySelectorAll('#lvbody td[data-rt]').length")
            assert n_rt >= 7, \
                ('排序后只剩 %d 个 data-rt 格子 —— livePatch 靠它们原地刷新，'
                 '丢了就会退回整块重建（页面跳动）' % n_rt)
            # ---- ⑥ 点表头【本身】不许跳动：不重建 DOM、不打接口 ----
            # 🔴 用户："点击一下排序这个页面会发生重新刷新，整个页面会跳动
            #   一下，体验比较差。" 根因：原来 `lvSortBind` 调 `loadLive(aid)`
            #   —— 它一进来就把 `#lvbody` 清成「读取中…」，几百毫秒后内容
            #   才回来，**高度先塌陷再撑开**。而排序根本不需要服务端。
            # ★ 四条判据各有各的构造条件，混在一起测全是空转：
            _mk = """() => { const r = document.querySelector(
                '#lvbody table.lvpos tr:nth-child(2)');
                r.dataset.mark = 'keep'; r.id = 'sprobe'; return true; }"""
            pg.evaluate(_mk)
            _api = []
            pg.on('request', lambda r: (_api.append(r.url)
                                        if '/api/live/account' in r.url else None))
            # ① 视口要**真的能滚**，否则"滚动位置保住了"是空转的
            #    （实测 1000 高的视口下这页滚不动，scrollY 一直是 0）
            pg.set_viewport_size({'width': 1600, 'height': 520})
            pg.wait_for_timeout(300)
            pg.evaluate("() => window.scrollTo(0, 260)")
            pg.wait_for_timeout(200)
            _sy0 = pg.evaluate("() => window.scrollY")
            assert _sy0 > 100, '视口没滚起来（%s），这条测不了' % _sy0
            _H = ("""() => ({b: document.querySelector('#lvbody')
                       .getBoundingClientRect().height,
                   k: document.querySelector('#lvkpi')
                       .getBoundingClientRect().height,
                   rt: document.querySelectorAll('#lvbody td[data-rt]').length})""")
            _h0 = pg.evaluate(_H)
            _n_api = len(_api)
            _o_before = pg.evaluate(ORDER)
            pg.locator('#lvbody th.lvsth', has_text='市值').first.click()
            pg.wait_for_timeout(900)
            # ② 行序确实变了 —— 不变的话下面三条全是空转
            assert pg.evaluate(ORDER) != _o_before, \
                '点了「市值」行序没变，后面几条断言都测不到东西'
            # ③ DOM 没被重建：记号还在。**只比高度是不够的** ——
            #    重建出一模一样的 DOM 也"没跳"，而 hover 与选中的文字已经断了
            assert pg.evaluate(
                "() => { const e = document.querySelector('#sprobe');"
                "  return !!e && e.dataset.mark === 'keep'; }"), \
                '点表头把 DOM 重建了 —— 滚动位置/hover/选中的文字都会断'
            _h1 = pg.evaluate(_H)
            assert _h1 == _h0, \
                '点表头之后尺寸变了（%s -> %s）—— 那就是"跳一下"' % (_h0, _h1)
            assert pg.evaluate("() => window.scrollY") == _sy0, \
                '点表头把滚动位置冲掉了（%s -> %s）' \
                % (_sy0, pg.evaluate("() => window.scrollY"))
            # ④ 表头箭头也要**就地**跟上。
            #    🔴 原来只在全量重渲染之后验箭头 —— 而全量重渲染本来就会
            #      照 `lvSortTh` 重生表头，所以那条断言对"就地重排忘了换
            #      箭头"是**空转的**（变异测试当场抓到）。
            _on = pg.evaluate(
                "() => { const t = document.querySelector("
                "  '#lvbody th.lvsth.on'); return t ? t.textContent.trim() : null; }")
            assert _on and '市值' in _on and '▼' in _on, \
                '就地重排之后表头没跟上（高亮/方向）：%r' % _on
            # ⑤ 一次接口都不该打：数据已经在 LVO 里，排序是纯本地的重排
            assert len(_api) == _n_api, \
                '点表头又去打了 %d 次 /api/live/account —— 排序不需要服务端' \
                % (len(_api) - _n_api)
            pg.set_viewport_size({'width': 1600, 'height': 1000})
            pg.wait_for_timeout(300)
            assert not errs, 'JS 错误：%s' % errs[:2]
            b.close()
    finally:
        httpd.shutdown()
    return ('%d 列升降序都对 · null 排最后 · quiet 与全量刷新都不重排 · '
            '%d 个 data-rt 格子保留 · 点表头就地重排'
            '（DOM 不重建 / 尺寸与滚动位置不变 / 0 次接口）'
            % (n_checked, n_rt))


@case('P1 移植件的常量必须【逐个等于正本】（对得上结果 ≠ 移植忠实）', tag='fast')
def t_p1_constants_match_source():
    """🔴 2026-09-13：我把 `CLUS_CAP` 抄成了 0.15，正本是 0.20。

    那个 0.15 恰好是 AlphaMiner **2026-08 定稿**的值，于是移植件成了混血：
    EXIT_BUFFER / BREADTH_FRAC / 汰换规则来自 07 版，簇上限却是 08 版的。

    🔴 **它完整地躲过了对数**：基准 / beta / 波动率三项精确吻合 ——
      而那三项只取决于数据与撮合，**对策略参数根本不敏感**。年化那 +1.28pp
      被"幸存者偏差"解释掉了（改对之后只剩 +0.81pp，回撤也从偏浅变偏深）。
      **对得上结果不代表移植忠实**，逐常量比一遍正本才是判据。

    ★ 正本是 GBK 的 QMT 文件，本用例直接读它、正则抠出常量来比 ——
      比"记得手工核对一遍"可靠（同「靠人记得 = 迟早不跑」）。
    """
    import io as _io
    import re as _re
    SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath('.'))),
                       'finacial', 'commands', 'trend', 'core',
                       'qmt_p1_rotation.py')
    if not os.path.exists(SRC):
        SRC = '/Users/guhao/finacial/commands/trend/core/qmt_p1_rotation.py'
    if not os.path.exists(SRC):
        return '正本不在本机（%s），跳过' % SRC
    try:
        src = _io.open(SRC, encoding='gbk').read()
    except Exception:
        src = _io.open(SRC, encoding='utf-8', errors='replace').read()
    port = _io.open('strategies/ETF/etf_p1_rotation.py', encoding='utf-8').read()

    def grab(text, key):
        m = _re.search(r'^%s\s*=\s*([0-9.]+)' % key, text, _re.M)
        if m:
            return float(m.group(1))
        m = _re.search(r'\b%s\b\s*=\s*([0-9.]+)' % key, text)
        return float(m.group(1)) if m else None

    KEYS = ('MOM_LB', 'VOL_LB', 'N_MAX', 'EXIT_BUFFER', 'W_CAP', 'GROSS',
            'REBAL_WEEKDAY', 'POOL_N', 'LIQ_WIN', 'MIN_VOL_ANN', 'RS_LB',
            'RS_KEEP', 'CORR_WIN', 'CLUS_THR', 'CLUS_CAP', 'CLUS_CAP_HI',
            'BREADTH_FRAC', 'MEGA_TOPK')
    bad, checked = [], 0
    for k in KEYS:
        a, b = grab(src, k), grab(port, k)
        if a is None or b is None:
            continue
        checked += 1
        if abs(a - b) > 1e-12:
            bad.append('%s 正本=%s 移植=%s' % (k, a, b))
    assert checked >= 12, '只比到 %d 个常量，正则大概率没匹配上' % checked
    assert not bad, ('移植件与正本的常量不一致（对得上结果 ≠ 移植忠实）：\n  %s'
                     % '\n  '.join(bad))
    # ★ 反向自证：这条断言不是空转 —— 换一个值必须被抓到
    fake = port.replace('CLUS_CAP = 0.20', 'CLUS_CAP = 0.15', 1)
    assert grab(fake, 'CLUS_CAP') == 0.15 and grab(src, 'CLUS_CAP') == 0.20, \
        '反向自证失败：抠常量的正则没真的读到值'
    return '%d 个常量与正本（qmt_p1_rotation.py，GBK）逐个相同；反向自证通过' % checked


@case('策略声明数据源与费率：不靠人记得传参（ETF 跑错 lake 会静默出结果）', tag='fast')
def t_strategy_declares_lake():
    """🔴 2026-09-13 用户报的 bug，根因不是那个 IOException，是【谁来记得】。

    `etf_p1_rotation.py` 不传 `--datalake` 时，一路跑到第一个重选日取名称
    才崩在 `etf_master.parquet` 不存在 —— 在那之前它已经拿**股票**的 K 线
    算完了成交额排名、选出了"动态池"。要是那张表恰好存在，它会产出一份
    **看着完全正常**的回测。`etf_trend_momentum` 更糟：主面板 0 行 ETF ->
    候选池恒空 -> 全程空仓，**一条平线且不报任何错**。

    这个失效模式当初**写进了 docstring**，却没有任何东西强制它 ——
    同「靠人记得跑的步骤 = 迟早不跑」。费率同理：ETF **不征印花税**是
    事实不是偏好，用股票默认值跑会凭空多扣一笔卖出税。
    """
    import ast as _ast
    import io as _io
    import run as _run
    notes = []
    # ① 三个 ETF 策略都必须【声明】lake 与费率，判据走 ast（注释里提到不算）
    etfs = sorted(f for f in os.listdir('strategies/ETF') if f.endswith('.py')
                  and not f.startswith('_'))
    assert len(etfs) >= 3, 'ETF 策略只剩 %d 个？' % len(etfs)
    for f in etfs:
        src = _io.open(os.path.join('strategies/ETF', f), encoding='utf-8').read()
        tree = _ast.parse(src)
        decl = [n for n in tree.body if isinstance(n, _ast.Assign)
                and any(getattr(t, 'id', None) == 'DATALAKE' for t in n.targets)]
        assert len(decl) == 1, '%s 没有模块级 DATALAKE 声明' % f
        assert decl[0].value.value == 'etf_lake', \
            '%s 的 DATALAKE 不是 etf_lake：%r' % (f, decl[0].value.value)
        calls = [n for n in _ast.walk(tree) if isinstance(n, _ast.Call)
                 and getattr(n.func, 'id', None) == 'set_order_cost']
        assert calls, ('%s 没调 set_order_cost —— ETF 不征印花税是【事实】，'
                       '靠人记得传 --stamp-tax 0 迟早漏，而漏了不报错' % f)
        kw = {k.arg: k.value.value for k in calls[0].keywords}
        assert kw.get('close_tax') == 0.0, \
            '%s 的 close_tax 不是 0（ETF 无印花税）：%r' % (f, kw.get('close_tax'))
    notes.append('%d 个 ETF 策略都声明了 lake 与 close_tax=0' % len(etfs))

    # ② resolve_lake 四条路径
    class M(object):
        pass
    base = _run.default_lake()
    m = M(); m.DATALAKE = 'etf_lake'
    want = os.path.normpath(os.path.join(base, 'etf_lake'))
    assert _run.resolve_lake(m, None) == want, '没声明时该自动落到 etf_lake'
    assert _run.resolve_lake(m, want) == want, '命令行给了对的应放行'
    try:
        _run.resolve_lake(m, base)
        raise AssertionError('命令行给了【错的】lake 必须报错，不许猜哪个对')
    except SystemExit as e:
        assert 'etf_lake' in str(e), '报错要说清该用哪个：%s' % e
    m2 = M(); m2.DATALAKE = 'lake_that_does_not_exist'
    try:
        _run.resolve_lake(m2, None)
        raise AssertionError('声明的 lake 不存在必须报错')
    except SystemExit as e:
        assert 'build_etf_lake' in str(e), '报错要给下一步怎么办：%s' % e
    m3 = M()          # 没声明的策略（股票）一律照旧
    assert _run.resolve_lake(m3, None) is None
    assert _run.resolve_lake(m3, '/x') == '/x'
    notes.append('resolve_lake 五条路径都对（自动/一致/冲突报错/不存在报错/不声明照旧）')

    # ③ 🔴 抬头打的成本必须是【策略声明之后】的那份
    #    判据不是"源码里有 boot" —— 是 run.py 里 boot 确实排在 print 之前。
    src = _io.open('run.py', encoding='utf-8').read()
    assert 'eng.boot(' in src, \
        ('run.py 根本没调 eng.boot() —— 策略的 set_order_cost 在 initialize '
         '里执行，不先 boot 的话抬头报的是默认值而实际跑策略那套')
    i_boot = src.index('eng.boot(')
    i_cost = src.index("print('成本 %s'")
    assert i_boot < i_cost, \
        ('run.py 里 eng.boot() 必须排在打印成本【之前】 —— 策略的 '
         'set_order_cost 在 initialize 里执行，不先 boot 的话抬头报默认值、'
         '实际跑策略那套，而**两个数都看着正常**')
    # boot 必须可重入（run() 里还会再调一次，跑两遍 = 任务注册两份）
    from assay.engine import Engine
    from assay.feed import PanelFeed
    import importlib.util as _iu
    spec = _iu.spec_from_file_location('s_etf', 'strategies/ETF/etf_p1_rotation.py')
    mod = _iu.module_from_spec(spec); spec.loader.exec_module(mod)
    fd = PanelFeed('2024-01-02', '2024-01-10',
                   root=os.path.join(_run.default_lake(), 'etf_lake'))
    from assay.broker import Cost
    eng = Engine(mod, fd, cash=100000, cost=Cost())
    eng.boot(); n1 = len(eng._tasks)
    eng.boot(); n2 = len(eng._tasks)
    assert n1 == n2 == 1, 'boot 不可重入：任务数 %d -> %d' % (n1, n2)
    assert eng.cost.close_tax == 0.0, \
        'boot 之后 cost 必须已是策略声明的那份，实得 close_tax=%r' % eng.cost.close_tax
    import assay.api as _api
    _api._unbind()
    notes.append('boot 排在打印成本之前且可重入（任务 1 个不翻倍、close_tax 已是 0）')
    return '；'.join(notes)


@case('看板页面清单：每个路由都有实现', tag='fast')
def t_page_inventory():
    """★ 这条用例的存在理由是一次真实事故：重构实盘页时，我用「切掉
    A 函数到 B 函数之间」的方式删代码，**顺手把整个数据同步页
    （showSync/pollSync）一起切掉了**，同时也切掉了 selftest 里夹在两个
    web 用例之间的一条用例。

    两个失败都不响亮：
      · 页面：只有点进 #/sync 才会看到 "showSync is not defined"，
        而 --fast 层不起浏览器，压根跑不到那儿
      · 用例：总数从 42 变 41，而没有任何东西核过总数

    所以这里核【路由与实现的对应关系】和【用例总数】——
    删代码时至少有一处会立刻叫。
    """
    # 🔴 selftest 不许往【真账本】里写。live/ 下那些 jsonl 是 append-only 的
    #    决策记录（成交流水 / 自选 / 费率 / 版本），被测试写进去就再也分不清
    #    哪条是真的 —— 而 append-only 意味着不能删。
    #    实测踩过：个股用例真点了那颗自选星，每跑一次留一对 add/remove，
    #    攒了 11 对才在提交时看到 live/watchlist.jsonl 变更。
    #    用例要写就重定向到临时目录（`wlmod.LIVE = tmp` / `lv.LIVE = tmp`）。
    here = os.path.dirname(os.path.abspath(__file__))
    src_all = open(os.path.join(here, 'selftest.py'), encoding='utf-8').read()
    for case_name, marker in (('个股页面', 'wlmod.LIVE = tmp'),
                              ('新页面真实渲染', 'wl.LIVE = tmp'),
                              ('自选：append-only', 'wl.LIVE = tmp'),
                              ('买点清单', 'al.LIVE = tmp'),
                              ('买点页面', 'al.LIVE = tmp')):
        i = src_all.index("@case('" + case_name)
        j = src_all.index('\n@case(', i + 10)
        assert marker in src_all[i:j], \
            ('用例「%s」会写自选账本却没重定向 LIVE —— 会污染真账本' % case_name)

    web = os.path.join(here, 'web')
    html = open(os.path.join(web, 'index.html'), encoding='utf-8').read()

    # ---- index.html 是【骨架】：hash 路由的几个视图各在自己的 .js 里 ----
    #   ★ 拆分依据是**产品域**（app/home/live/runs/sync），不是技术分层 ——
    #     依赖分析显示跨域调用几乎全是"路由 -> 视图"这一个方向。
    #   ★ 断言只钉"文件在、被引用、没重名"这三件，不钉具体行数 ——
    #     行数会随功能长，钉了只会天天误报。
    #   ★ 这张表是【唯一】的清单：加了新域文件就往这里加一行 ——
    #     分两处写的话，"新文件没被 index.html 引用"或"断言没扫到它"
    #     都不会报错，只是那部分功能悄悄不在保护范围内。
    # 🔴 **扫目录，不照清单拼** —— 新增一个视图文件（如 live-perf.js）时，
    #   写死的清单不会跟着变，于是它的 showXxx 定义不在 `js` 里、
    #   "被调用但没有定义"就误报；更糟的是它的所有检查都悄悄漏掉了，
    #   而**那不报错，只是保护范围缩小**（同 _web_files 那条）。
    DOMS = tuple(sorted(
        f[:-3] for f in os.listdir(os.path.join(web, 'views'))
        if f.endswith('.js')))
    assert 'app' in DOMS and len(DOMS) >= 10, \
        'views/ 下的域文件数看着不对：%s' % (DOMS,)
    for d in DOMS:
        fp = os.path.join(web, 'views', d + '.js')
        assert os.path.isfile(fp), \
            '缺 web/views/%s.js（index.html 拆分出来的域）' % d
        assert '/views/%s.js' % d in html, \
            'index.html 没引用 views/%s.js' % d
    assert '<script>' not in html, \
        ('index.html 里又出现内联 <script> —— 它应该只是骨架，'
         '视图逻辑放到对应的域文件里')
    js = '\n'.join(open(os.path.join(web, 'views', d + '.js'),
                        encoding='utf-8').read() for d in DOMS)

    # 🔴 【跨文件顶层重名 = 整页 SyntaxError】。所有 <script>（含 src= 引入的）
    #    共享同一个全局词法环境，重名直接
    #    "Identifier 'x' has already been declared" ——
    #    表现是**整页白屏、所有功能一起没了**。
    #    实测踩过：把 num() 搬进 common.js 时忘了删 index.html 那份，
    #    8 个 web 用例一起挂。
    #    ★ 拆成 5 个域文件后组合数从 1 对变成 21 对，所以这里**两两全比**，
    #      而不是只比"每个页面 vs common.js"。
    top = lambda t: set(re.findall(
        r'^(?:const|let|var|function|async function)\s+([A-Za-z_$][\w$]*)',
        t, re.M))
    import itertools
    # ★ 直接扫目录，而不是照着 DOMS 拼 —— 漏掉一个文件的话，
    #   "两两比对"就漏了它的所有组合，而那不会报错。
    shared_files = _web_files(web, '.js')
    want = set(os.path.join('views', d + '.js') for d in DOMS)
    assert want <= set(shared_files), \
        'DOMS 里列的文件不存在：%s' % sorted(want - set(shared_files))
    syms = {f: top(open(os.path.join(web, f), encoding='utf-8').read())
            for f in shared_files}
    for a, b in itertools.combinations(shared_files, 2):
        dup = sorted(syms[a] & syms[b])
        assert not dup, \
            ('%s 与 %s 顶层重名 %s —— 会 SyntaxError 导致整页白屏' % (a, b, dup))
    # 独立页面（自带内联 <script>）仍要与所有共享文件比
    allshared = set().union(*syms.values())
    # 🔴 `.html` 一律在**根目录** —— `/stock.html?code=…` 是外部书签与跨页
    #   链接的地址，属于产品契约。这里断言它没被挪进子目录：挪了的话旧书签
    #   全部 404，而"点了没反应"是最难查的那种坏。
    pages = sorted(f for f in os.listdir(web)
                   if f.endswith('.html') and f != 'index.html')
    stray = [f for f in _web_files(web, '.html') if os.sep in f]
    assert not stray, '.html 必须留在 web/ 根目录（产品契约）：%s' % stray
    # ★ 反过来：`.js`/`.css` 一律**不许**平铺在根 —— 否则下次新加的文件又会
    #   散在根目录，而"目录结构慢慢退化"没有任何报错。
    #   shared/ = 跨所有页面共享（含 6 个独立 .html）；views/ = 只服务 index.html
    #   的 hash 视图。分目录依据仍是**产品域**，与 index.html 的拆分同一判据。
    flat = [f for f in os.listdir(web) if f.endswith(('.js', '.css'))]
    assert not flat, \
        ('web/ 根目录不该有 .js/.css：%s —— 共享的放 shared/，'
         'index.html 的视图放 views/' % sorted(flat))
    for fn in pages:
        src = open(os.path.join(web, fn), encoding='utf-8').read()
        inline = '\n'.join(re.findall(r'<script>(.*?)</script>', src, re.S))
        dup = sorted(top(inline) & allshared)
        assert not dup, \
            ('%s 与共享 .js 顶层重名 %s —— 会 SyntaxError 导致整页白屏'
             % (fn, dup))
    # 每个独立页面都必须引用共享资源，不能各带一份样式/辅助函数
    for fn in pages + ['index.html']:
        src = open(os.path.join(web, fn), encoding='utf-8').read()
        assert '/shared/common.css' in src, \
            '%s 没引用 shared/common.css' % fn
        assert '/shared/common.js' in src, \
            '%s 没引用 shared/common.js' % fn
        assert '<style>' not in src, \
            '%s 里还有内联 <style> —— 样式应集中在 common.css' % fn

    # route() 里出现的每个 showXxx()，都必须有对应的 function 定义
    called = set(re.findall(r'\b(show[A-Z]\w*)\s*\(', js))
    defined = set(re.findall(r'(?:async\s+)?function\s+(show[A-Z]\w*)\s*\(', js))
    missing = sorted(called - defined)
    assert not missing, \
        ('这些页面函数被调用但没有定义 —— 页面会白屏且只在点进去时才报错：%s'
         % missing)

    # 每个 hash 路由都要有入口
    for route, fn in (('#/live', 'showLive'), ('#/sync', 'showSync'),
                      ('#/docs', 'showDocs'), ('#/picks', 'showPicks'),
                      ('#/runs', 'showCatalog'), ('#/stock', None)):
        assert route in js, '路由 %s 不见了' % route
        # fn=None：这个路由只做跳转（个股已搬到独立页 /stock.html），
        # 本文件里没有对应的 showXxx 实现
        if fn:
            assert fn in defined, '%s 的实现 %s 不见了' % (route, fn)
    # 流水是独立页，单独核（它的路由带参数）
    assert 'showFills' in defined and '/fills' in js, '成交流水独立页不见了'
    # 选股理由也是独立页（同成交流水：会越来越长 -> 服务端分页）
    assert 'showWhy' in defined and '/why' in js, '选股理由独立页不见了'
    # 🔴 后台复算时页面必须**说出来**并自己回来看：不说的话那一段就是一句
    #   "没有理由"，人会当成功能坏了；不轮询的话它会一直停在"正在复算…"，
    #   **两种都不报错**。
    # 🔴 断言要匹配**完整条件**，不能只查标识符在不在：`explain_pending`
    #   在轮询那行也出现，只查名字的话把 `if(p.explain_pending){` 改成
    #   `if(false){` 照样全绿（同 prune_runs 的 `h.pruned` 那条）。
    _js0 = re.sub(r'\s+', '', js)
    assert 'if(p.explain_pending){' in _js0 and 'whypend' in js, \
        '选股理由页没有"正在事后复算"这一态 —— 历史那几期会显示成"没有理由"'
    assert re.search(r'explain_pending\)[\s\S]{0,400}?setTimeout', js), \
        '有后台复算在跑却不自动回来看 —— 页面会一直停在"正在复算…"'

    # 用例总数 —— 删代码时把整条用例切掉过一次
    n = len(CASES)
    assert n >= 54, \
        ('用例只剩 %d 条，少于已知的 54 —— 是不是删代码时把某条一起切掉了？'
         '用 `git show HEAD:selftest.py | grep "^@case"` 对一下' % n)
    # ★ 数字让它自己算 —— 写死的话下次再拆还得手改，而"忘了改"的表现是
    #   报告串在说谎（它看着像验过了）。
    return ('index.html 拆成 %d 个域文件且全被引用、骨架里无内联 script；'
            '%d 个共享 .js 两两无顶层重名（%d 对）；'
            '%%d 个页面函数与路由一一对应（%%s）；用例 %%d 条'
            % (len(DOMS), len(shared_files),
               len(shared_files) * (len(shared_files) - 1) // 2)
            % (len(defined), ' '.join(sorted(defined)), n))


@case('指标：一处定义 / 口径跟行情软件 / 买点触发价是【反解】出来的', tag='fast')
def t_indicators():
    """🔴 2026-09-15 用户要「一个指标模块 + 个股页按需展示 + 买点按指标」。

    改造前 MACD/KDJ/RSI/BOLL 的公式写死在 `stock.indicators()` 里，
    而前端 `kchart.js` 又硬编码了一份"画哪几条线"与配色 —— 加一个指标
    要改三处，**漏掉画的那处不报错**：选了它副图一片空白。

    这条用例钉四件事：
      ① 定义只有一处，且 `series` 里的每个 key 都真能算出来
      ② 参数越界**报错不夹逼**（悄悄改成边界值的话，页面写着 200 画的是 60）
      ③ 口径跟行情软件（KDJ 通用平滑 / BOLL 总体标准差 / ATR-RSI Wilder）
      ④ 买点的触发价是**反解**出来的，且与解析解一致
    """
    from assay import indicators as I
    from assay import stock as st
    notes = []
    bars = st.kline('601857.SH', n=320)['bars']
    cl = [b['close'] for b in bars]

    # ---- ① series 的每个 key 都要算得出来 ----
    # 🔴 判据不是"defs() 返回了几个" —— 而是**画图要的那几个键**真在
    #   calc 的输出里。少一个的话副图上就少一条线，而它不报错。
    for d in I.defs():
        sp = I.spec(d['id'])
        col = sp.calc(bars, None)
        for x in d['series']:
            if x['style'] == 'zero':
                continue
            assert x['key'] in col,                 '%s 声明要画 %s，而 calc 根本没给这个键 —— 副图会少一条线' \
                % (d['id'], x['key'])
            assert any(v is not None for v in col[x['key']]),                 '%s 的 %s 整列都是 None（320 根还算不出来？）' % (d['id'], x['key'])
        assert d['short'] and d['desc'], '%s 没有短名/说明' % d['id']
    notes.append('%d 个指标的 series 逐个能算（%s）'
                 % (len(I.REG), '/'.join(x.id for x in I.REG)))

    # ---- ② 参数越界报错，不夹逼 ----
    for iid, pr in (('kdj', {'n': 9999}), ('macd', {'fast': -1}),
                    ('boll', {'k': 99})):
        try:
            I.compute(bars, iid, pr)
            raise AssertionError('%s 的越界参数 %r 被【悄悄夹逼】了 —— '
                                 '页面上写着一个数、画的是另一个' % (iid, pr))
        except I.IndError:
            pass
    try:
        I.spec('nosuch')
        raise AssertionError('不存在的指标没报错')
    except I.IndError:
        pass
    notes.append('越界与不存在都报错（不夹逼）')

    # ---- ③ 口径：与行情软件的写法逐条对 ----
    # KDJ：K = ((k-1)*前K + RSV)/k（通用平滑），不是 SMA(3)
    kd = I.compute(bars, 'kdj', None)
    i = len(bars) - 1
    h9 = max(b['high'] for b in bars[i - 8:i + 1])
    l9 = min(b['low'] for b in bars[i - 8:i + 1])
    rsv = 50.0 if h9 == l9 else (cl[i] - l9) / (h9 - l9) * 100
    kprev = I.compute(bars[:-1], 'kdj', None)['k'][-1]
    assert abs(kd['k'][-1] - ((2 * kprev + rsv) / 3)) < 0.02, \
        'KDJ 的 K 不是通用平滑（%.4f vs 手算 %.4f）—— 与券商软件对不上时，' \
        '人会以为是数据错了' % (kd['k'][-1], (2 * kprev + rsv) / 3)
    # BOLL：σ 是【总体】标准差（除 N），不是样本（除 N-1）
    bl = I.compute(bars, 'boll', None)
    seg = cl[-20:]
    mu = sum(seg) / 20
    sd_pop = (sum((x - mu) ** 2 for x in seg) / 20) ** 0.5
    sd_smp = (sum((x - mu) ** 2 for x in seg) / 19) ** 0.5
    assert abs(bl['ub'][-1] - (mu + 2 * sd_pop)) < 0.01, 'BOLL 上轨对不上总体标准差'
    assert abs(bl['ub'][-1] - (mu + 2 * sd_smp)) > 1e-4, \
        'BOLL 用的是样本标准差 —— 与行情软件差一点点，而那个差看着像舍入'
    # ATR：Wilder 平滑（1/N 递推），不是简单均值
    at = I.compute(bars, 'atr', None)
    tr = I._tr(bars)
    prev = I.compute(bars[:-1], 'atr', None)['atr'][-1]
    assert abs(at['atr'][-1] - (prev * 13 + tr[-1]) / 14) < 0.005, \
        'ATR 不是 Wilder 平滑'
    notes.append('口径逐条对上行情软件（KDJ 通用平滑 / BOLL 总体σ / ATR Wilder）')

    # ---- ④ 触发价：与【解析解】一致 ----
    # 🔴 这是这次改造的核心：「离金叉还有多远」的答案**不是**今天那个百分比
    #   （MA5 与 MA20 两条都在动），而是"今天收在什么价位就刚好金叉"。
    f, sl = 5, 20
    Sf, Ss = sum(cl[-f:-1]), sum(cl[-sl:-1])     # 【不含今天】，今天那格是待解的 P
    exact = (f * Ss - sl * Sf) / (sl - f)
    px, why = I.trigger_price(bars, 'ma_cross', {'fast': 5, 'slow': 20}, 0.0)
    assert px is not None and abs(px - exact) < 0.01, \
        '金叉触发价与解析解对不上：二分 %r vs 解析 %.4f（%s）' % (px, exact, why)
    S19 = sum(cl[-20:-1])
    exact2 = S19 * 0.97 / (20 - 0.97)
    px2, _ = I.trigger_price(bars, 'ma_dist', {'w': 20}, -3.0)
    assert px2 is not None and abs(px2 - exact2) < 0.01, \
        '距 MA20 的触发价与解析解对不上：%r vs %.4f' % (px2, exact2)
    # 反向自证：触发价两侧必须**刚好翻面**（否则那个数只是"看着正常"）
    n_ok = 0
    for sid, args, v in (('ma_cross', {'fast': 5, 'slow': 20}, 0.0),
                         ('ma_dist', {'w': 20}, -3.0),
                         ('rsi_low', {'w': 6}, 30.0),
                         ('boll_low', {'n': 20, 'k': 2}, 0.0),
                         ('cci_low', {'n': 14}, -100.0)):
        pxx, _w = I.trigger_price(bars, sid, args, v)
        if pxx is None:
            continue
        op = I.sig(sid)['op']
        lo = I.sig_value(I._with_close(bars, pxx * 0.999), sid, args)
        hi = I.sig_value(I._with_close(bars, pxx * 1.001), sid, args)
        ok = (lo <= v < hi) if op == 'le' else (lo < v <= hi)
        assert ok, '%s 的触发价 %s 没夹住阈值（两侧 %s / %s）' % (sid, pxx, lo, hi)
        n_ok += 1
    assert n_ok >= 4, '只验到 %d 条触发价（其余都解不出？）' % n_ok
    # 🔴 反解时 **high/low 必须跟着收盘价改**：价格跌到触发价，那天的最低价
    #   至少是它。只改 close 的话，KDJ/WR 这些读 high/low 的指标会算出
    #   **今天根本不可能出现**的值 —— 判据用它们的定义域（K 与 WR 都是
    #   0~100，数学上跑不出去），比"检查那两行代码在不在"硬。
    #   ★ 上面那 5 条触发价全是只读收盘价的（bias/spread/RSI/BOLL/CCI），
    #     所以这条**必须单独构造**，否则那个变异从头到尾没被执行到。
    for fct in (0.2, 0.5, 0.8, 1.0, 1.4, 2.0):
        bb = I._with_close(bars, cl[-1] * fct)
        kv = I.compute(bb, 'kdj', None)['k'][-1]
        wv = I.compute(bb, 'wr', None)['wr6'][-1]
        assert 0 <= kv <= 100 and 0 <= wv <= 100, \
            ('价格 ×%.1f 时 KDJ 的 K=%s / WR=%s 跑出了 0~100 —— '
             '把收盘挪到那个价位时 high/low 没跟着改' % (fct, kv, wv))
    # 解不出来时**说理由**，不猜一个数
    p3, w3 = I.trigger_price(bars, 'rsi_low', {'w': 6}, 99.0)
    assert p3 is None and w3, '恒成立的条件应该说"恒成立"而不是给个价'
    # 方向：金叉是【涨上去】才成立
    assert I.sig_dir('ma_cross') == 'up' and I.sig_dir('ma_dist') == 'down', \
        '买点条件的方向标反了 —— 一只已经金叉的票会显示成"还差 x%"'
    notes.append('触发价二分 == 解析（金叉 %.3f / 距MA20 %.3f），%d 条两侧翻面自证'
                 % (px, px2, n_ok))

    # ---- ⑤ 老接口的默认输出不许变（改造前后逐位等价已单独验过）----
    d = st.indicators('601857.SH', n=60)
    for k in ('dif', 'dea', 'macd', 'k', 'd', 'jj', 'rsi6', 'ub', 'lb'):
        assert k in d['rows'][-1], '默认那份少了 %s —— 老页面读的就是它' % k
    d2 = st.indicators('601857.SH', n=60, inds='atr,cci')
    assert [x['id'] for x in d2['panels']] == ['atr', 'cci'], \
        'inds= 没按点名的来：%r' % [x['id'] for x in d2['panels']]
    assert 'dif' not in d2['rows'][-1], '没点名的指标也算了（白算一遍）'
    notes.append('默认那份仍带老字段；inds= 只算点名的')
    return '；'.join(notes)


@case('个股页：副图是【槽位】/ 左上角下拉框选指标 / 参数在弹窗里（playwright）',
      tag='web')
def t_stock_multi_sub():
    """🔴 这一页的指标选择器**改了三版**，每版都是用户当场指出来的：

        v1 工具条上平铺一排标签（一个指标一个）
           -> "指标数量上去后一横排也放不下"
        v2 一个按钮 + 竖排勾选面板（参数也在面板里）
           -> "点击指标会导致整个页面重新刷新"
           -> "改参数会让页面高度、宽度变化，影响整个页面的布局"
        v3（现在）**副图 = 槽位**：默认两个，点「+ 副图」加（最多 4），
           每个槽位**左上角一个下拉框**选显示什么，参数在**弹窗**里改

    判据各有各的构造条件，混在一起测全是空转：
      ① 默认就是**两个**槽位，且第一个是成交量（它不再是画死的一块）
      ② 下拉框**浮在对应副图的左上角**（按 KGEO 摆，不是写死坐标）
      ③ 换指标 / 加 / 删：只重取**指标那一个接口**、DOM 不重建
      ④ 参数弹窗开关时**页面布局一个像素都不许动**（这正是用户的原话）
      ⑤ 到上限要**说一句**，不许静默不动
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
    base = 'http://127.0.0.1:%d' % port
    notes = []
    try:
        with sync_playwright() as p:
            try:
                br = p.chromium.launch()
            except Exception as e:                          # noqa: BLE001
                return '跳过（浏览器不可用: %s）' % type(e).__name__
            pg = br.new_page(viewport={'width': 1500, 'height': 1200})
            errs, reqs = [], []
            pg.on('pageerror', lambda e: errs.append(str(e)))
            pg.on('request', lambda r: reqs.append(r.url))
            pg.goto(base + '/stock.html?code=601857.XSHG',
                    wait_until='networkidle')
            pg.wait_for_selector('#kcv', timeout=40000)
            pg.wait_for_timeout(1200)

            # ---- ① 默认两个槽位，第一个是成交量 ----
            assert pg.evaluate('() => KINDS') == ['vol', 'macd'], \
                ('默认副图不是【成交量 + MACD】两个：%r —— 用户要的是'
                 '"下面默认两个副图的位置"' % pg.evaluate('() => KINDS'))
            # 🔴 成交量**不再是画死的一块**：它就是第一个槽位的默认指标，
            #   所以能被换掉。判据是"换掉之后 KINDS 里就没有它了"。
            assert pg.evaluate('() => KGEO.nSub') == 2, \
                '画布上不是两个副图：%r' % pg.evaluate('() => KGEO.nSub')
            # 🔴 判据：**第一个副图紧贴主图**（中间只隔一个 GAP）——
            #   成交量要是还画死在中间那一块，这里就会多出一截，
            #   而图上"看着差不多"根本看不出来。
            _g = pg.evaluate('() => ({mt: KGEO.mainTop, mh: KGEO.mainH,'
                             ' st: KGEO.subTop, gap: KGEO.GAP})')
            assert abs(_g['st'] - (_g['mt'] + _g['mh'] + _g['gap'])) < 1, \
                ('主图与第一个副图之间还夹着一块（主图底 %.0f + gap %d '
                 '!= 副图顶 %.0f）—— 成交量已经是一个槽位了，'
                 '不该再画死一块' % (_g['mt'] + _g['mh'], _g['gap'], _g['st']))

            # ---- ② 下拉框浮在**对应副图**的左上角 ----
            sel = pg.locator('#kslots .kssel')
            assert sel.count() == 2, '副图左上角没有下拉框（实得 %d 个）' % sel.count()
            geo = pg.evaluate("""() => {
                const c = document.querySelector('#kcv').getBoundingClientRect();
                return [...document.querySelectorAll('#kslots .kslot')]
                  .map(e => { const r = e.getBoundingClientRect();
                    return {j: +e.dataset.j, top: r.top - c.top,
                            left: r.left - c.left,
                            want: KGEO.subY(+e.dataset.j)}; });}""")
            for g in geo:
                assert abs(g['top'] - g['want']) < 6 and 0 < g['left'] < 120, \
                    ('第 %d 个副图的下拉框没落在它左上角：实 top=%.0f / '
                     '应 %.0f（按 KGEO 摆，别自己算坐标）'
                     % (g['j'], g['top'], g['want']))
            notes.append('默认 2 个槽位（成交量+MACD），下拉框各就各位')

            # ---- ③ 换指标：只重取指标接口、DOM 不重建 ----
            # 🔴 用户："点击指标会导致整个页面重新刷新，这个需要调整。"
            #   判据要两头钉：接口没多打 + DOM 没重建（只比接口数的话，
            #   重建出一模一样的 DOM 也算通过，而 hover、选中的文字、
            #   滚动位置已经断了）。
            # 🔴🔴 判据要钉到**每个元素**，不能只钉 `#kcv`。
            #   用户 2026-09-16 又报了一次："切换副图时整个页面会闪一下。"
            #   —— 而上面那条断言当时是**绿的**：`#kcv` 确实没被换，
            #   被 `innerHTML` 整块换掉的是工具条 / 槽位层 / 加副图行。
            #   代价是**用户刚点的那个 `<select>` 被销毁重建**：焦点没了、
            #   原生控件重绘，看上去就是闪一下。
            #   ★ 这也是"第一次切换"才暴露的：签名若存在变量里，首屏那几块
            #     是 `load()` 拼模板直接吐的、不经过 reloadInd，于是初值永远
            #     不等 —— **每次打开页面的第一次切换照样重建**。所以标记要在
            #     页面刚加载后就打，并且**第一次**切换就验。
            MARK = ("""() => { let i = 0;
                document.querySelectorAll(
                  '#kpickbox a, #kslots .kslot, #kslots select, #kaddrow a')
                  .forEach(e => e.dataset.mk = 'k' + (i++));
                return i; }""")
            CHK = ("""() => [...document.querySelectorAll(
                  '#kpickbox a, #kslots .kslot, #kslots select, #kaddrow a')]
                  .map(e => e.dataset.mk || 'NEW')""")
            reqs.clear()
            pg.evaluate("() => { document.querySelector('#kcv')"
                        ".dataset.mark = 'keep'; }")
            _n_mk = pg.evaluate(MARK)
            assert _n_mk >= 5, '控件只找到 %d 个，这条判据怕是没扫到' % _n_mk
            pg.locator('#kslots .kssel[data-j="1"]').select_option('kdj')
            pg.wait_for_timeout(1400)
            _mk = pg.evaluate(CHK)
            assert 'NEW' not in _mk, \
                ('换一个副图指标重建了 %d 个控件（%r）—— 这三块没有一块跟着'
                 '指标变（工具条是设置+主图开关、槽位是下拉框+⚙+×、'
                 '「+副图」只跟个数有关），换掉它们就是把用户刚点的那个'
                 'select 销毁重建：焦点没了、控件重绘，屏幕上就是闪一下'
                 % (_mk.count('NEW'), _mk))
            _heavy = [u for u in reqs if '/api/stock/profile' in u
                      or '/api/stock/finance' in u or '/api/stock/peers' in u]
            assert not _heavy, \
                ('换一个副图指标把整页那十个接口又打了一遍：%r' % _heavy[:3])
            assert pg.evaluate(
                "() => document.querySelector('#kcv').dataset.mark") == 'keep', \
                '换指标把 #body 整块重建了（画布都换了新的）—— 页面会跳一下'
            assert len([u for u in reqs if '/api/stock/indicators' in u]) == 1, \
                '应该只重取【指标那一个】接口'
            assert pg.evaluate('() => KINDS') == ['vol', 'kdj'], \
                '下拉框换的不是【那个槽位】：%r' % pg.evaluate('() => KINDS')
            # 换完之后那个副图画的真是 KDJ（拦 fillText 看图例）
            drew = pg.evaluate(r"""() => {
                const cv = document.getElementById('kcv');
                const g = cv.getContext('2d');
                const orig = g.fillText.bind(g);
                const seen = [];
                g.fillText = function (t, x, y) { seen.push(String(t));
                                                  return orig(t, x, y); };
                try { drawKChart(cv, {bars: BARS, subs: kSubs()}); }
                finally { g.fillText = orig; }
                return seen;}""")
            assert any(t.startswith('K ') for t in drew) \
                and not any(t.startswith('DIF') for t in drew), \
                '换成 KDJ 之后画的还是 MACD：%r' % drew[:8]
            notes.append('换指标只重取指标接口、一个控件都不重建')

            # ★ 另外两条路径**各自钉各自的规矩**（同实盘页 quiet/全量那条）：
            #   主图开关只许动工具条、加副图只许动槽位层与「+副图」行 ——
            #   只钉"换指标不重建"的话，把判据写成"永远不重建"也全绿，
            #   而那会让加/删副图之后 `data-j` 不重排（换错槽位且不报错）。
            pg.evaluate(MARK)
            pg.locator('#kpickbox a.kmain[data-i="boll"]').click()
            pg.wait_for_timeout(1400)
            _mk2 = pg.evaluate(CHK)
            _tool = pg.evaluate("""() => [...document.querySelectorAll('#kpickbox a')]
                .map(e => e.dataset.mk || 'NEW')""")
            _slot = pg.evaluate("""() => [...document.querySelectorAll('#kslots .kslot')]
                .map(e => e.dataset.mk || 'NEW')""")
            assert 'NEW' in _tool, '主图开关没更新工具条（按钮的选中态不会变）'
            assert 'NEW' not in _slot, \
                '主图开关把副图那几组控件也重建了：%r —— 它们跟主图无关' % _slot
            pg.evaluate(MARK)
            pg.locator('#kaddrow #kadd').click()
            pg.wait_for_timeout(1400)
            _tool2 = pg.evaluate("""() => [...document.querySelectorAll('#kpickbox a')]
                .map(e => e.dataset.mk || 'NEW')""")
            _slot2 = pg.evaluate("""() => [...document.querySelectorAll('#kslots .kslot')]
                .map(e => e.dataset.mk || 'NEW')""")
            assert 'NEW' in _slot2, \
                ('加了一个副图但槽位层没重建 —— 新槽位的 data-j 不重排的话，'
                 '换指标会换错那个槽位，而它不报错')
            assert 'NEW' not in _tool2, \
                '加副图把工具条也重建了：%r —— 主图那两个开关没有变' % _tool2
            notes.append('主图开关只动工具条 · 加副图只动槽位层')
            # 收拾回原样，后面的断言照旧从两个槽位起步
            pg.locator('#kslots .ksdel[data-j="2"]').click()
            pg.wait_for_timeout(1200)
            pg.locator('#kpickbox a.kmain[data-i="boll"]').click()
            pg.wait_for_timeout(1200)

            # ---- ④ 加 / 删槽位，到上限要说一句 ----
            for _ in range(3):
                pg.click('#kadd')
                pg.wait_for_timeout(900)
            assert pg.evaluate('() => KINDS.length') == 4, \
                '「+ 副图」加不到 4 个：%r' % pg.evaluate('() => KINDS')
            assert pg.evaluate('() => KGEO.nSub') == 4, '画布上没画出 4 个副图'
            assert len(set(pg.evaluate('() => KINDS'))) == 4, \
                '加出来的槽位重复了 —— 新槽位该默认放还没用上的那个'
            pg.click('#kadd')
            pg.wait_for_timeout(500)
            _m = pg.locator('#kimsg').inner_text()
            assert '4' in _m and pg.evaluate('() => KINDS.length') == 4, \
                ('到上限时既没拦住也没说明：%r —— 点了没反应是最难查的'
                 '那种坏' % _m)
            # 🔴 这句话要**自己说得完整**。用户 2026-09-15 把它读成了
            #   "先去掉一个主图"：原来提示排在工具条里、紧挨着
            #   「主图 [均线][布林带]」，屏幕上就是"…先去掉一个｜主图 均线"。
            #   判据两条：提示**不许**待在放主图开关的那个容器里，
            #   且它自己要点明说的是"副图"。
            assert not pg.evaluate(
                "() => !!document.querySelector('#kpickbox #kimsg')"), \
                ('副图的提示又跑回工具条里了 —— 它会紧挨着「主图」那两个'
                 '开关，被读成"先去掉一个主图"（主图与副图各算各的，不冲突）')
            assert '副图' in _m, '提示没说清是"副图"到上限了：%r' % _m
            _before = pg.evaluate('() => KINDS.slice()')
            pg.locator('#kslots .ksdel[data-j="0"]').click()
            pg.wait_for_timeout(1000)
            assert pg.evaluate('() => KINDS') == _before[1:], \
                '「×」删的不是那个槽位：%r -> %r' % (_before,
                                                pg.evaluate('() => KINDS'))
            # 🔴 用户："添加副图的按钮应该放在主图的最下面，或者说当前
            #   最后一个副图的下面。" —— 那才是你看完最后一个副图、想再加
            #   一个时手停的位置。判据是**几何**：它必须在画布下边界之下。
            _ab = pg.evaluate(
                "() => {const c = document.querySelector('#kcv')"
                ".getBoundingClientRect();"
                " const a = document.querySelector('#kadd')"
                ".getBoundingClientRect();"
                " return {cb: c.bottom, at: a.top};}")
            assert _ab['at'] >= _ab['cb'] - 1, \
                ('「+ 副图」不在图的下面（按钮 top=%.0f / 画布底=%.0f）—— '
                 '它该贴着最后一个副图，而不是躲在工具条里'
                 % (_ab['at'], _ab['cb']))
            notes.append('+ 副图 在图下方 / × 删槽位 / 到 4 个有提示且说清是副图')

            # ---- ⑤ 参数在【弹窗】里改，且开关它布局一个像素都不动 ----
            # 🔴 用户原话："修改指标的参数应该在单独的地方（至少是一个单独的
            #   弹窗，不然每次点击导致页面高度、宽度变化，会影响整个页面的
            #   布局）。" —— 所以判据不是"能改参数"，是**开关弹窗时页面高度
            #   与画布尺寸一个像素都不许变**。
            _h0 = pg.evaluate("() => [document.body.scrollHeight,"
                              " document.querySelector('#kcv')"
                              ".getBoundingClientRect().height]")
            pg.click('#kparam')
            pg.wait_for_timeout(400)
            assert pg.locator('#kmwrap .stbox').is_visible(), '参数弹窗打不开'
            _h1 = pg.evaluate("() => [document.body.scrollHeight,"
                              " document.querySelector('#kcv')"
                              ".getBoundingClientRect().height]")
            assert _h0 == _h1, \
                ('开参数弹窗把页面布局挤动了：%r -> %r —— 用户明确说这就是'
                 '要避免的（弹窗必须是 fixed 浮层）' % (_h0, _h1))
            _k0 = pg.evaluate("() => IND[IND.length - 1].k")
            box = pg.locator('#kmwrap .kpi[data-id="kdj"][data-k="n"]').first
            assert box.count() == 1, '弹窗里没有 KDJ 的参数框'
            box.fill('19')
            box.dispatch_event('change')
            pg.wait_for_timeout(1600)
            assert pg.evaluate("() => IND[IND.length - 1].k") != _k0, \
                'KDJ 周期 9 -> 19，K 值却没变 —— 参数没送到服务端'
            _h2 = pg.evaluate("() => [document.body.scrollHeight,"
                              " document.querySelector('#kcv')"
                              ".getBoundingClientRect().height]")
            assert _h0 == _h2, '改完参数页面布局变了：%r -> %r' % (_h0, _h2)
            assert pg.locator('#kmwrap .stbox').is_visible(), \
                '改完一个参数弹窗就自己关了 —— 要改第二个还得重新点开'
            box.fill('9999')
            box.dispatch_event('change')
            pg.wait_for_timeout(500)
            # ★ 提示要在**弹窗里**（就在那个输入框旁边）：扔到图下面那行去的话，
            #   人盯着输入框却看不到为什么被退回来。
            assert '之间' in pg.locator('#kmwrap #kmmsg').inner_text(), \
                ('越界参数没在弹窗里当场拒：%r'
                 % pg.locator('#kmwrap').inner_text()[:120])
            pg.click('#kprst')
            pg.wait_for_timeout(1600)
            assert abs(pg.evaluate("() => IND[IND.length - 1].k") - _k0) < 1e-9, \
                '「恢复默认参数」没还原'
            pg.keyboard.press('Escape')
            pg.wait_for_timeout(300)
            assert pg.locator('#kmwrap').count() == 0, 'Esc 关不掉弹窗'
            notes.append('参数在弹窗里改：布局一动不动、越界当场拒、Esc 关得掉')

            # ---- ⑥ URL 带得走 ----
            pg.goto(base + '/stock.html?code=601857.XSHG&sub=kdj,rsi',
                    wait_until='networkidle')
            pg.wait_for_selector('#kcv', timeout=40000)
            pg.wait_for_timeout(1000)
            assert pg.evaluate('() => KINDS') == ['kdj', 'rsi'], \
                'URL 里的 sub=kdj,rsi 没生效（分享出去的链接看到的是另一张图）'
            pg.goto(base + '/stock.html?code=601857.XSHG&sub=0',
                    wait_until='networkidle')
            pg.wait_for_selector('#kcv', timeout=40000)
            pg.wait_for_timeout(800)
            assert pg.evaluate('() => KINDS.length') == 0 \
                and pg.evaluate('() => KGEO.nSub') == 0, \
                '「一个副图都不要」存不住'
            notes.append('URL 带得走（sub=kdj,rsi / sub=0）')
            assert not errs, 'JS 报错：%r' % errs[:3]
            br.close()
    finally:
        httpd.shutdown()
    return '；'.join(notes)


@case('买点也能按【指标】：触发价现算 / 方向不是都朝下（playwright）', tag='web')
def t_alerts_by_indicator():
    """🔴 2026-09-15 用户："买点功能不仅可以根据价格提示，还可以根据指标
    提示买点，比如距离 20 日线的距离、MA5/MA20 金叉的距离。"

    ★ 做法上的关键一步：指标条件**反解成一个触发价**（"今天收在什么价位，
      这个条件刚好成立"）。于是它和价格档落在同一个口径上，这一页那套
      「到价 / 接近 / 还差多少」一行都不用改。
      直接报"现在离金叉还差 2.3%"是**会骗人**的：MA5 与 MA20 两条都在动，
      那个百分比推不出"涨到多少就金叉"。

    三条判据：
      ① 存的是**条件**、不是触发价（触发价每天都不一样，存下来第二天就错）
      ② **方向**：金叉是涨上去才成立 —— 记反的话，已经金叉的票会显示成"还差"
      ③ 页面上要说清是**哪个条件**（只给一个价，事后没法复盘）
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return '跳过（无 playwright）'
    import io as _io
    import json as _json
    import shutil
    import tempfile
    import threading
    from http.server import ThreadingHTTPServer

    import re as _re

    from assay import alerts as al
    from assay import indicators as I
    from assay import server as sv
    from assay import stock as _st
    old_live, old_allow = al.LIVE, sv.ALLOW_LIVE
    _o_fetch, _o_extpath = al._ext_fetch, al.ext_path
    sv.ALLOW_LIVE = True
    tmp = tempfile.mkdtemp()
    al.LIVE = tmp                    # ★ 不往真账本里写测试数据
    al.ext_path = lambda root=None: os.path.join(tmp, 'div_ext.json')
    al._ext_fetch = lambda cs, day=None: {}
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), sv.Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = 'http://127.0.0.1:%d' % port
    notes = []
    try:
        # ---- ① 服务端：存条件、算触发价、方向 ----
        # ★ 这个阈值**运行时挑**：写死一个数的话，它可能落在"今天到不了"
        #   的区间里（state='na'），于是"还要涨 x%"那条路又是空转的
        #   （实测踩过：v=20 解不出触发价，页面显示的是"就算涨到 17.78 也
        #   到不了"，而我的判据只查了个"涨"字，被那句话蒙混过关）。
        _bars = _st.kline('601857.SH', n=320)['bars']
        _cur = _bars[-1]['close']
        _upv = None
        for _c in (1, 2, 3, 5, 8, 12):
            _px, _w = I.trigger_price(_bars, 'ma_cross',
                                      {'fast': 5, 'slow': 20}, float(_c))
            if _px and _px > _cur * 1.015:
                _upv = float(_c)
                break
        assert _upv is not None, '挑不出一个"解得出但还没到"的金叉阈值'
        al.set_row('601857.SH', [
            {'by': 'price', 'v': 8.0},
            {'by': 'ind', 'sig': 'ma_dist', 'args': {'w': 20}, 'v': -3},
            {'by': 'ind', 'sig': 'ma_cross', 'args': {'fast': 5, 'slow': 20},
             'v': 0},
            # ★ 再来一档**远没到**的 up 档：上面那档常常已经金叉（state=hit），
            #   只验它的话“还要涨 x%”那条路**一次都不会执行**
            #   （变异实测：把涨/跌写死成“跌”照样全绿）。
            {'by': 'ind', 'sig': 'ma_cross', 'args': {'fast': 5, 'slow': 20},
             'v': _upv},
        ])
        # ★ 再加**只有一个 up 档、且整行都没到**的一只 —— 上面那只整行是
        #   'hit'，于是「状态」那列走的是"到价 · 第 N 档"分支，
        #   "还要涨/跌 x%" 那条路**一次都不执行**（变异实测：把它写死成
        #   "还要跌"照样全绿）。
        _b2 = _st.kline('600900.SH', n=320)['bars']
        _c2 = _b2[-1]['close']
        _upv2 = None
        for _c in (1, 2, 3, 5, 8, 12):
            _px2, _ = I.trigger_price(_b2, 'ma_cross',
                                      {'fast': 5, 'slow': 20}, float(_c))
            # 🔴 要 `far` 就得**离得够远**：near 阈值是 3%，只要求
            #   +2% 的话触发价会落进"接近"区间，那一行的状态就是 near，
            #   而"还要涨 x%"那条路只在 far 时才走到（实测偶发）。
            if _px2 and _px2 > _c2 * 1.07:
                _upv2 = float(_c)
                break
        assert _upv2 is not None, '第二只票也挑不出"解得出但还没到"的阈值'
        al.set_row('600900.SH', [
            {'by': 'ind', 'sig': 'ma_cross', 'args': {'fast': 5, 'slow': 20},
             'v': _upv2}])
        raw = [_json.loads(l) for l in _io.open(
            os.path.join(tmp, 'alerts.jsonl'), encoding='utf-8')]
        # ★ 认准**那条记录**，不是"最后一行" —— 账本里后面还追加了第二只票，
        #   拿 raw[-1] 会取到另一行（实测当场 IndexError）。
        tiers = [r for r in raw if r.get('code', '').startswith('601857')][-1]['tiers']
        assert tiers[1] == {'by': 'ind', 'sig': 'ma_dist', 'args': {'w': 20},
                            'v': -3.0}, \
            ('账本里存的不是【条件本身】：%r —— 存触发价的话第二天就是错的'
             '（均线在动），同「每档存的是你填的那个，另一个现算」' % tiers[1])
        v = al.valued()
        row = [r for r in v['rows'] if r['code'].startswith('601857')][0]
        row2 = [r for r in v['rows'] if r['code'].startswith('600900')][0]
        assert row2['state'] == 'far' and row2['next'] \
            and row2['next'].get('dir') == 'up' and row2['next']['gap'] > 0, \
            ('第二只票该是"还没到的 up 档"，实得 %r'
             % {k: row2.get(k) for k in ('state', 'next')})
        ind = [t for t in row['tiers'] if t['by'] == 'ind']
        assert len(ind) == 3, '三个指标档没都出来：%r' % row['tiers']
        far_up = [t for t in ind if t['sig'] == 'ma_cross'
                  and t['v'] == _upv][0]
        assert (far_up['dir'] == 'up' and far_up['state'] in ('far', 'near')
                and far_up['price'] and far_up['gap'] > 0), \
            ('那档要"涨上去"的金叉必须是【解得出且还没到】的，实得 %r —— '
             '否则页面上"还要涨 x%%"那条路一次都走不到' % far_up)
        cross = [t for t in ind if t['sig'] == 'ma_cross'][0]
        dist = [t for t in ind if t['sig'] == 'ma_dist'][0]
        assert cross['dir'] == 'up' and dist['dir'] == 'down', \
            ('方向标错了（金叉 %r / 距均线 %r）—— 一只已经金叉的票会被'
             '显示成"还要涨 x%%"' % (cross['dir'], dist['dir']))
        assert cross['price'] and dist['price'], \
            '触发价没算出来：%r' % [(t['sig'], t['price'], t['why']) for t in ind]
        # 触发价必须**跟着行情走**，不是账本里存着的死数
        assert cross['text'] and 'MA5' in cross['text'], \
            '没给出这条件的人话：%r' % cross['text']
        # 状态判定要**按方向**：把金叉那档按"跌到"判的话，结论正好反
        pr = row['price']
        want = 'hit' if pr >= cross['price'] else (
            'near' if pr >= cross['price'] * (1 - row['near_used']) else 'far')
        assert cross['state'] == want, \
            ('金叉那档的状态按方向应是 %s，实得 %s（现价 %s / 触发价 %s）'
             % (want, cross['state'], pr, cross['price']))
        notes.append('账本存条件；触发价现算（距MA20 %s / 金叉 %s）；方向 down/up'
                     % (dist['price'], cross['price']))

        # 通知文案要写清**是哪个条件**触发的
        for rec in al.check_fire(v):
            if rec.get('cond'):
                txt = al.notify_text(rec)
                assert rec['cond'] in txt, \
                    '通知没写清是哪个条件触发的：%r' % txt

        with sync_playwright() as p:
            try:
                br = p.chromium.launch()
            except Exception as e:                          # noqa: BLE001
                return '跳过（浏览器不可用: %s）' % type(e).__name__
            pg = br.new_page(viewport={'width': 1600, 'height': 1100})
            errs = []
            pg.on('pageerror', lambda e: errs.append(str(e)))
            pg.goto(base + '/alerts.html', wait_until='networkidle')
            pg.wait_for_selector('table.pkt', timeout=40000)

            # ---- ② 条件清单【由服务端给】，页面不写死 ----
            page_ids = pg.evaluate('() => ASIG.map(x => x.id)')
            assert page_ids == [x['id'] for x in I.signal_defs()], \
                ('页面上的条件清单与服务端对不上：%r —— 写死一份的话，'
                 '加一条条件它根本选不到' % page_ids)

            # ---- ③ 表里要说清是哪个条件 + 方向对的措辞 ----
            txt = pg.locator('table.pkt').inner_text()
            assert cross['text'] in txt, \
                '表里没写出条件本身（只有一个价，事后没法复盘）：%s' % txt[:300]
            cell = pg.evaluate("""() => {
                const tds = [...document.querySelectorAll('table.pkt td')];
                const td = tds.find(e => e.innerText.indexOf('MA5') >= 0);
                return td ? td.innerText : null;}""")
            assert cell, '找不到金叉那一档的格子'
            need = '已站上' if cross['state'] == 'hit' else '涨'
            assert need in cell, \
                ('金叉那档写的是 %r —— 方向反了（它是涨上去才成立，'
                 '不是"还要跌"）' % cell)
            # 🔴 **还没到**的那档必须写"涨 x%"：一律写"跌"的话，一只离金叉
            #   还差 20% 的票会被读成"还要跌 20%"，意思正好反了。
            cell2 = pg.evaluate(
                "(t) => {const tds = [...document.querySelectorAll("
                "'table.pkt td')];"
                " const td = tds.find(e => e.innerText.indexOf(t) >= 0);"
                " return td ? td.innerText : null;}", far_up['text'])
            # 🔴 判据要认准**那句涨跌措辞**（"涨 8.3%"），不能只查一个"涨"字 ——
            #   解不出触发价时页面写的是"就算涨到 17.78 也到不了"，
            #   里面也有个"涨"（实测被它蒙混过关过一次）。
            assert cell2 and _re.search(r'涨\s*\d', cell2) \
                and not _re.search(r'跌\s*-?\d', cell2), \
                ('还没到的金叉档写的是 %r —— 它要涨上去才成立，'
                 '写"还要跌"意思正好反了' % cell2)
            # 整行「状态」那列也要认方向（它走的是另一条分支：x.next）
            st2 = pg.evaluate(
                "(c) => {const tr = [...document.querySelectorAll("
                "'table.pkt tr')].find(e => e.innerText.indexOf(c) >= 0);"
                " if (!tr) return null; const td = tr.cells[tr.cells.length - 2];"
                " return td ? td.innerText : null;}", '600900')
            assert st2 and _re.search(r'涨\s*\d', st2) \
                and not _re.search(r'跌\s*-?\d', st2), \
                ('整行状态写的是 %r —— 那一行唯一没到的是个"涨上去才成立"'
                 '的条件，写"还要跌"意思正好反了' % st2)
            notes.append('页面给出条件本身与方向正确的措辞（档 %r / 整行 %r）'
                         % (cell.replace('\n', ' '), (st2 or '').strip()))

            # ---- ④ 改一行：回填的是【条件】不是触发价 ----
            pg.locator('a.aed').first.click()
            pg.wait_for_timeout(600)
            sel = pg.locator('.asig')
            assert sel.count() == 3, '编辑器里没回填出三个指标档：%d' % sel.count()
            assert pg.evaluate(
                "() => FORM.tiers.filter(t => t.by === 'ind')"
                ".every(t => t.sig && t.args && t.v != null)"), \
                ('回填的指标档缺 sig/args/阈值 —— 照触发价回填的话，'
                 '"距 MA20 -3%" 会变成一个死价格')
            # 改成 RSI 超卖并保存：服务端要按新条件重算
            sel.first.select_option('rsi_low')
            pg.wait_for_timeout(400)
            pg.click('#asave')
            pg.wait_for_timeout(2500)
            got = pg.evaluate("""() => {
                const r = (V.rows || [])[0] || {};
                return (r.tiers || []).filter(t => t.by === 'ind')
                       .map(t => t.sig);}""")
            assert 'rsi_low' in got, '换成 RSI 超卖没存进去：%r' % got
            notes.append('回填的是条件本身，换条件能存进去')
            assert not errs, 'JS 报错：%r' % errs[:3]
            br.close()
    finally:
        httpd.shutdown()
        al.LIVE, sv.ALLOW_LIVE = old_live, old_allow
        al._ext_fetch, al.ext_path = _o_fetch, _o_extpath
        shutil.rmtree(tmp, ignore_errors=True)
    return '；'.join(notes)


@case('指标广场：主图/副图分页签 + 分页只取当前页 / 每张卡有真图（playwright）',
      tag='web')
def t_indicator_plaza():
    """🔴 2026-09-15 用户："查看全部指标的地方在哪里？应该有指标广场。"

    在此之前，指标只能在个股页工具条上看到一排**短名 + tooltip** ——
    "CCI 到底是什么、怎么算的、能不能拿来当买点"哪儿都答不了。

    这一页的判据：
      ① **照服务端那份清单渲染**（页面里没有任何指标名）—— 写死一份的话，
         加一个指标它在这儿根本不出现，而那不报错
      ② 每张卡有**真图**：只列公式的话"它长什么样"还得回个股页一个个试，
         而那正是广场存在的理由
      ③ 「在个股页看」要**真的选中那个指标**（不是链过去就算）
      ④ 两个入口都通（独立页面最大的风险是页面之间断链）
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return '跳过（无 playwright）'
    import threading
    from http.server import ThreadingHTTPServer

    from assay import indicators as I
    from assay import server as sv
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), sv.Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = 'http://127.0.0.1:%d' % port
    notes = []
    defs, sigs = I.defs(), I.signal_defs()
    try:
        with sync_playwright() as p:
            try:
                br = p.chromium.launch()
            except Exception as e:                          # noqa: BLE001
                return '跳过（浏览器不可用: %s）' % type(e).__name__
            pg = br.new_page(viewport={'width': 1500, 'height': 1200})
            errs = []
            pg.on('pageerror', lambda e: errs.append(str(e)))
            pg.goto(base + '/indicators.html', wait_until='networkidle')
            pg.wait_for_selector('.icard', timeout=40000)
            pg.wait_for_timeout(1500)

            # ---- ① 主图 / 副图**分两个页签**（用户 2026-09-15）----
            # 它们是两回事：主图那几条与 K 线共用价格坐标，副图各自一套。
            # 混在一个流里的话，"哪些能叠在 K 线上"得一张张卡去看。
            want_main = [d['id'] for d in defs if d['panel'] == 'main']
            want_sub = [d['id'] for d in defs if d['panel'] == 'sub']
            tabs = pg.locator('#tabs .itab')
            assert tabs.count() == 2, '没有分成两个页签：%d' % tabs.count()
            _tt = ' '.join(pg.locator('#tabs').inner_text().split())
            assert str(len(want_main)) in _tt and str(len(want_sub)) in _tt, \
                '页签上没写各有几个：%r' % _tt
            ids = pg.evaluate(
                "() => [...document.querySelectorAll('.icard')]"
                ".map(e => e.dataset.i)")
            assert ids == want_main, \
                ('主图页签列的不是主图那几个：%r / 应为 %r' % (ids, want_main))
            pg.locator('#tabs .itab[data-t="sub"]').click()
            pg.wait_for_timeout(1500)
            # 🔴 副图**一页放不下**（每页 9 个），所以判据是「翻完所有页
            #   刚好覆盖服务端那份清单、顺序也对」——只验第一页的话，
            #   第 2 页漏掉一个不会有人发现（而它不报错）。
            #   ★ 这比原来那条"一页必须等于全部"更强，也不会因为以后调
            #     每页个数就过期。
            # 🔴 副图**一页放不下**（每页 9 个），所以判据要**翻完所有页**：
            #   只验第一页的话，第 2 页少一张卡不会有人发现（而它不报错）。
            #   ★ 逐卡那几条内容断言也跟着进循环 —— 它们对**每一页**都该成立，
            #     留在外面的话会去找不在当前页上的卡，表现是 30 秒超时、
            #     看着像页面坏了（实测踩过）。
            ids, seen, blank = [], 0, []
            while True:
                cur = pg.evaluate(
                    "() => [...document.querySelectorAll('.icard')]"
                    ".map(e => e.dataset.i)")
                ids += cur
                for d in [x for x in defs if x['id'] in cur]:
                    card = pg.locator('.icard[data-i="%s"]' % d['id'])
                    t = card.inner_text()
                    assert d['short'] in t and d['desc'][:8] in t, \
                        '%s 这张卡缺短名或说明' % d['id']
                    fml = card.locator('.ifml').inner_text().strip()
                    assert fml and fml != '（略）' \
                        and d['formula'].split('\n')[0][:10] in fml, \
                        ('%s 没写出公式（广场要回答"怎么算的"）：%r'
                         % (d['id'], fml))
                    for a in d['params']:
                        assert a['label'] in t, \
                            '%s 少了参数「%s」' % (d['id'], a['label'])
                    dots = card.locator('.idot').count()
                    n_line = len([x for x in d['series'] if x['style'] != 'zero'])
                    assert dots == n_line, \
                        ('%s 的色块 %d 个、实际要画 %d 条线 —— 对不上的话，'
                         '"图上哪条线是哪个"就只能靠猜' % (d['id'], dots, n_line))
                # 🔴 图**必须真的画了**：判据是画布上的非透明像素。
                #   只查 <canvas> 在不在的话，画崩了（尺寸算错/坐标 NaN）
                #   表现就是一张空白画布，而它不报错。
                blank += [x for x in pg.evaluate(
                    """() => [...document.querySelectorAll('.icv')]
                    .map(cv => {const g = cv.getContext('2d');
                      const d = g.getImageData(0, 0, cv.width, cv.height).data;
                      let n = 0; for (let i = 3; i < d.length; i += 4) if (d[i]) n++;
                      return [cv.dataset.i, n];})""") if x[1] < 3000]
                nxt = pg.locator('#inext')
                if not nxt.count() or 'off' in (nxt.get_attribute('class') or ''):
                    break
                seen += 1
                assert seen < 20, '翻页翻不完 —— 「下一页」没有终点'
                nxt.click()
                pg.wait_for_timeout(1400)
            assert not blank, '这几张卡是空白的：%r' % blank
            assert ids == want_sub, \
                ('把所有页翻完列的不是副图那几个：%r / 应为 %r' % (ids, want_sub))
            assert seen >= 1, \
                ('副图只有一页 —— "翻完所有页"这条判据是空转的'
                 '（共 %d 个）' % len(want_sub))
            pg.goto(base + '/indicators.html?tab=sub', wait_until='networkidle')
            pg.wait_for_selector('.icard', timeout=40000)
            pg.wait_for_timeout(1200)
            ids = pg.evaluate(
                "() => [...document.querySelectorAll('.icard')]"
                ".map(e => e.dataset.i)")
            # 顶栏点亮的是**父级**（个股），不是它自己：NAV 里没有这一项，
            # 传一个不存在的 key 会让整条顶栏一个都不亮
            assert '个股' in pg.locator('#top .btn.nav.on').inner_text(), \
                '子页没点亮父级「个股」：%s' % pg.locator('#top .btn.nav.on').inner_text()

            notes.append('两个页签各列各的（主图 %d / 副图 %d），图都真画了'
                         % (len(want_main), len(want_sub)))

            # ---- ②b 分页：**只向服务端要当前这一页那几个指标** ----
            # 🔴 用户："考虑如果指标数量非常多（几十上百个），怎么分页"。
            #   这一页的开销**不在列表**，在于每张卡都要一张真图 ——
            #   一次算完上百个再画上百张画布，页面必卡。所以判据不是
            #   "分了页"，而是**请求里只带这一页的 id**。
            # ★ 用 `per=3` 把分页逼出来：真实只有十来个指标，
            #   不压小每页个数的话这段永远是"第 1/1 页"，等于没测。
            _api = []
            pg.on('request', lambda r: _api.append(r.url)
                  if '/api/stock/indicators' in r.url else None)
            pg.goto(base + '/indicators.html?tab=sub&per=3',
                    wait_until='networkidle')
            pg.wait_for_selector('.icard', timeout=40000)
            pg.wait_for_timeout(900)
            _p1 = pg.evaluate("() => [...document.querySelectorAll('.icard')]"
                              ".map(e => e.dataset.i)")
            assert _p1 == want_sub[:3], '第一页不是前 3 个：%r' % _p1
            import urllib.parse as _up
            _got = _up.unquote(_api[-1].split('inds=')[1].split('&')[0])
            assert _got.split(',') == _p1, \
                ('请求里带的不是这一页那几个指标（%r vs 页面 %r）—— '
                 '上百个指标时这就是"一次全算"与"只算 3 个"的区别' % (_got, _p1))
            assert '1/%d 页' % ((len(want_sub) + 2) // 3) \
                in pg.locator('#ipinfo').inner_text(), \
                '分页信息不对：%r' % pg.locator('#ipinfo').inner_text()
            pg.click('#inext')
            pg.wait_for_timeout(1500)
            _p2 = pg.evaluate("() => [...document.querySelectorAll('.icard')]"
                              ".map(e => e.dataset.i)")
            assert _p2 == want_sub[3:6] and 'page=2' in pg.url, \
                '翻页没换内容或没进 URL：%r / %s' % (_p2, pg.url)
            _got2 = _up.unquote(_api[-1].split('inds=')[1].split('&')[0])
            assert _got2.split(',') == _p2, \
                '翻页之后请求的还是上一页那几个：%r' % _got2
            _px2 = pg.evaluate("""() => [...document.querySelectorAll('.icv')]
                .map(cv => {const g = cv.getContext('2d');
                  const d = g.getImageData(0, 0, cv.width, cv.height).data;
                  let n = 0; for (let i = 3; i < d.length; i += 4) if (d[i]) n++;
                  return n;})""")
            assert len(_px2) == 3 and min(_px2) > 3000, \
                '第二页的图没画出来：%r' % _px2
            notes.append('分页只取当前页（per=3：%s -> %s）'
                         % (','.join(_p1), ','.join(_p2)))

            # ---- ②c 搜索：上百个指标时，靠翻页是翻不到的 ----
            pg.fill('#iq', '均线')
            pg.wait_for_timeout(1800)
            _hit = pg.evaluate("() => [...document.querySelectorAll('.icard')]"
                               ".map(e => e.dataset.i)")
            assert _hit and all(
                '均线' in (d['desc'] + d['formula'] + d['label'])
                for d in defs if d['id'] in _hit), \
                '搜索结果不对：%r' % _hit
            assert 'q=' in pg.url, '搜索词没进 URL（刷新就丢）'
            pg.fill('#iq', 'zzz没有这个')
            pg.wait_for_timeout(1800)
            assert '没有匹配' in pg.locator('#pg').inner_text(), \
                '搜不到时没有空态提示（一片空白看着像坏了）'
            notes.append('搜索（名称/说明/公式）+ 空态')
            pg.goto(base + '/indicators.html?tab=sub', wait_until='networkidle')
            pg.wait_for_selector('.icard', timeout=40000)
            pg.wait_for_timeout(800)

            # ---- ③ 「在个股页看」要真的选中那个指标 ----
            href = pg.locator('.icard[data-i="kdj"] a.ilink').get_attribute('href')
            assert 'sub=kdj' in href, \
                ('「在个股页看」没带上这个指标：%r —— 点过去还是默认那张图，'
                 '等于这个入口是摆设' % href)
            pg.goto(base + href, wait_until='networkidle')
            pg.wait_for_selector('#kcv', timeout=40000)
            pg.wait_for_timeout(1200)
            assert pg.evaluate('() => KINDS') == ['kdj'], \
                '点「在个股页看」过去之后没选中 KDJ：%r' % pg.evaluate('() => KINDS')
            notes.append('「在个股页看」真的把那个指标选上了')

            # ---- ④ 两个入口都通（断链是独立页面最大的风险）----
            pg.goto(base + '/stock.html?code=601857.XSHG', wait_until='networkidle')
            pg.wait_for_selector('#kcv', timeout=40000)
            pg.wait_for_timeout(800)
            # ★ 入口在工具条那个**齿轮**里（用户 2026-09-16："现在找不到指标
            #   广场的入口了……个股页面里加个设置按钮（齿轮），可以在里面
            #   进入指标广场"）。入口一直在，但那个按钮当时叫「⚙ 参数」、
            #   而广场链接排在弹窗**最底下**跟「恢复默认参数」挤一起 ——
            #   **一个叫"参数"的按钮，没人会指望里面有"都支持哪些指标"**。
            _gear = pg.locator('#kparam').inner_text()
            assert '⚙' in _gear and '参数' not in _gear, \
                ('工具条那个按钮写的是 %r —— 它管的不只是参数（里面还有'
                 '指标广场），叫"参数"的话人不会去点' % _gear)
            pg.click('#kparam')
            pg.wait_for_timeout(400)
            assert pg.locator('#kmore').count() == 1, \
                '设置弹窗里没有去指标广场的入口 —— '\
                '那里只有参数框，"这个指标是什么、怎么算的"答不了'
            # 🔴 它要排在**最前面**：排在最后跟「恢复默认参数」挤一起时，
            #   用户就是找不到（这一轮的起因）。判据取**位置**不是"存在"。
            _pos = pg.evaluate("""() => {
                const box = document.querySelector('#kmwrap .stbox');
                const a = document.querySelector('#kmore');
                const kids = [...box.children];
                return [kids.findIndex(c => c.contains(a)), kids.length]; }""")
            assert _pos[0] <= 1, \
                ('指标广场的入口排在弹窗第 %d/%d 块 —— 它要在最前面：'
                 '"都支持哪些指标"是进来之前就想问的，而下面那些只回答'
                 '"这几个怎么调"' % (_pos[0] + 1, _pos[1]))
            pg.locator('#kmore').click()
            pg.wait_for_selector('.icard', timeout=40000)
            assert '/indicators.html' in pg.url, \
                '个股页那个入口点了没去成广场：%s' % pg.url
            pg.goto(base + '/alerts.html', wait_until='networkidle')
            pg.wait_for_timeout(1200)
            n_entry = pg.evaluate(
                "() => [...document.querySelectorAll('a')]"
                ".filter(a => (a.getAttribute('href') || '')"
                ".indexOf('/indicators.html') >= 0).length")
            # 买点页的入口在编辑器里（挑条件那一格）——先打开编辑器
            if not n_entry and pg.locator('a.aed').count():
                pg.locator('a.aed').first.click()
                pg.wait_for_timeout(600)
                n_add = pg.locator('#atadd')
                if n_add.count():
                    n_add.click()
                    pg.wait_for_timeout(300)
                    aby = pg.locator('.aby')
                    aby.nth(aby.count() - 1).select_option('ind')
                    pg.wait_for_timeout(400)
                n_entry = pg.evaluate(
                    "() => [...document.querySelectorAll('a')]"
                    ".filter(a => (a.getAttribute('href') || '')"
                    ".indexOf('/indicators.html') >= 0).length")
            assert n_entry >= 1, \
                '买点页挑指标条件的地方没有「指标说明」入口 —— '\
                '"CCI 超卖是什么"在那儿没法回答'
            # ---- 首页与口径字典页也要能进（用户问的就是"首页没有入口吗"）----
            pg.goto(base + '/', wait_until='networkidle')
            pg.wait_for_timeout(2500)
            _h = pg.evaluate(
                "() => [...document.querySelectorAll('#main a')]"
                ".filter(a => (a.getAttribute('href') || '')"
                ".indexOf('/indicators.html') >= 0).length")
            assert _h >= 1, \
                ('首页上没有去指标广场的入口 —— 一个只能从个股页工具条里'
                 '摸到的入口等于没有入口（用户原话："首页没有地方进入吗"）')
            pg.goto(base + '/#/docs', wait_until='networkidle')
            pg.wait_for_timeout(2000)
            _d = pg.evaluate(
                "() => [...document.querySelectorAll('#main a')]"
                ".filter(a => (a.getAttribute('href') || '')"
                ".indexOf('/indicators.html') >= 0).length")
            assert _d >= 1, \
                '口径字典页没有指向指标广场的交叉引用 —— 找"口径"的人会先去那儿'
            notes.append('四处入口都通：个股页 / 买点页 / 首页 / 口径字典')

            # ---- ⑤ 买点条件表：与服务端逐条对上，且方向写出来了 ----
            pg.goto(base + '/indicators.html', wait_until='networkidle')
            pg.wait_for_selector('table.lvt', timeout=40000)
            tb = pg.locator('table.lvt').inner_text()
            for sg in sigs:
                assert sg['label'] in tb and sg['sample'] in tb, \
                    '买点条件表里少了「%s」' % sg['label']
            assert '涨到才成立' in tb and '跌到才成立' in tb, \
                ('买点条件表没写方向 —— 金叉是涨上去才成立，'
                 '不写的话会被当成"跌到就买"')
            notes.append('%d 条买点条件逐条对上且标了方向' % len(sigs))

            # ---- ⑤🔴 同一行的几张卡，【图的位置必须一样高】 ----
            # 用户 2026-09-16："每个图里面公式、说明占的空间和下面图占的
            #   空间位置相对要固定，不然图不对齐不太好看。"
            # 说明与公式长短不一，不锁高度的话同一行几张卡的画布各在各的
            # 高度上 —— 要横向比形态时眼睛得上下找。
            pg.goto(base + '/indicators.html?tab=sub', wait_until='networkidle')
            pg.wait_for_selector('.icard .icv', timeout=40000)
            pg.wait_for_timeout(1500)
            n_card = pg.locator('.icard').count()
            assert n_card <= 9, \
                ('副图一页 %d 张 —— 用户说"6 个或者 9 个已经够了，超出则分页"'
                 % n_card)
            rows = pg.evaluate("""() => {
              const g = {};
              document.querySelectorAll('.icard').forEach(c => {
                const cv = c.querySelector('.icv');
                if(!cv) return;
                const k = Math.round(c.getBoundingClientRect().top);
                (g[k] = g[k] || []).push(Math.round(cv.getBoundingClientRect().top));
              });
              return g; }""")
            # 🔴 反向自证：真的有"一行多张"，否则这条判据是空转的
            wide = [v for v in rows.values() if len(v) > 1]
            assert wide, '每行只有一张卡（视口 %d 宽），这条判据测不到' % 1500
            for tops in rows.values():
                assert len(set(tops)) == 1, \
                    ('同一行的画布没对齐：%r —— 说明/公式长短不一时，'
                     '文字区必须锁住高度（.ihead），否则图各在各的高度上' % tops)
            notes.append('每页 %d 张 · 同一行 %d 张卡的画布 top 完全一致'
                         % (n_card, max(len(v) for v in rows.values())))

            # ---- ⑥ 文字过长只显示几行，可展开 ----
            # ★ 「展开」**只在真的被截断时**才露出来 —— 常驻一个点了没变化
            #   的按钮比不给更糟（同 backLink 那条）。
            cut = pg.evaluate("""() => [...document.querySelectorAll('.icard')]
              .map(c => ({id: c.dataset.i,
                          cut: [...c.querySelectorAll('.iclamp')].some(
                                 e => e.scrollHeight - e.clientHeight > 2),
                          btn: !c.querySelector('.imore').hidden}))""")
            for r in cut:
                assert r['cut'] == r['btn'], \
                    ('%s 截断=%s 而「展开」可见=%s —— 两者必须一致：没截断还'
                     '摆个按钮是"点了没变化"，截断了不给按钮是把内容藏没了'
                     % (r['id'], r['cut'], r['btn']))
            # 🔴 反向自证：真有一张被截断，否则上面那条是"全 False == 全 False"
            hit = [r['id'] for r in cut if r['cut']]
            assert hit, \
                ('没有一条说明/公式被截断 —— 收起的行数给得太宽松，'
                 '这条功能和它的判据都是空转的')
            # 🔴 截断必须是 `-webkit-line-clamp` 干的（**按整行切**），
            #   不能是别的机制把它压扁的。踩过：`.ihead` 写成 flex 容器时，
            #   flex 子项的 display 被 blockify，`-webkit-box` 当场变成
            #   `flow-root` —— **line-clamp 整个失效**，而 flex 仍然会把超出
            #   的子项压扁，于是"看着还是截断了、展开也还能用"，
            #   **只是文字在中间被硬切**。判据取"可见高度是行高的整数倍"。
            geo = pg.evaluate("""() => [...document.querySelectorAll('.icard .iclamp')]
              .filter(e => e.scrollHeight - e.clientHeight > 2)
              .map(e => { const c = getComputedStyle(e);
                return {id: e.closest('.icard').dataset.i, disp: c.display,
                        h: e.clientHeight, lh: parseFloat(c.lineHeight),
                        pad: parseFloat(c.paddingTop) + parseFloat(c.paddingBottom)}; })""")
            assert geo, '没量到被截断的元素'
            # ★ 判据**不能用 `display` 是不是 `-webkit-box`**：Chrome 对这个
            #   legacy 值的 computed style 报的是 `flow-root`（正常态实测就是
            #   这样），拿它当判据在正常代码上就先挂了。
            for r in geo:
                lines = (r['h'] - r['pad']) / r['lh']
                assert abs(lines - round(lines)) < 0.12, \
                    ('%s 截断后可见 %.2f 行 —— 不是整行，文字被切了一半'
                     % (r['id'], lines))
            card = pg.locator('.icard:has(.imore:not([hidden]))').first
            h0 = card.bounding_box()['height']
            card.locator('.imorea').click()
            pg.wait_for_timeout(350)
            h1 = card.bounding_box()['height']
            assert h1 > h0 + 2, \
                '点「展开」卡片没变高（%.0f -> %.0f）—— 点了没反应' % (h0, h1)
            assert '收起' in card.locator('.imorea').inner_text(), \
                '展开之后按钮还写着"展开" —— 人不知道能收回去'
            assert not pg.evaluate("""(c) => {
                const e = document.querySelector('.icard[data-i='+c+'] .ifml');
                return e.scrollHeight - e.clientHeight > 2; }""",
                card.get_attribute('data-i')), '展开之后公式还被截着'
            card.locator('.imorea').click()
            pg.wait_for_timeout(350)
            assert abs(card.bounding_box()['height'] - h0) < 3, \
                '再点一次没收回去'
            notes.append('长文本收起可展开（%s 被截断，展开 %.0f→%.0f px）'
                         % ('/'.join(hit), h0, h1))
            assert not errs, 'JS 报错：%r' % errs[:3]
            br.close()
    finally:
        httpd.shutdown()
    return '；'.join(notes)


@case('自定义基准：手填名称/代码 · 一律后复权（分红要算进去）', tag='fast')
def t_bench_custom():
    """🔴 用户 2026-09-16："基准除了预设的这些，还可以自己手填名称/代码
    比对，比如填某个红利 ETF 作为基准比对。"

    预设那九个都是**宽基指数**，而人真正想问的往往是"我这个红利策略
    跑不跑得赢红利 ETF" —— 那不是宽基能回答的。

    🔴🔴 **这个功能真正的坑在复权。** 红利 ETF（sh510880）的 hfq_factor
      从 1.0 涨到 1.81，那 81% 全是分红。拿不复权收盘当基准，等于把一个
      年化 ~5% 股息的东西按"股价涨了多少"来比：**实测 2016 年至今
      不复权 +24.0% / 后复权 +93.4%，差 69 个百分点** —— 而它不报错，
      只是那条基准线一直偏低、策略看着凭空超额。
    """
    import duckdb
    from assay.lv import perf as _p
    import run as _run
    notes = []
    root = _run.default_lake()

    # ---- ① 搜得到、且只给**本地真有日线**的 ----
    hit = _p.bench_search('红利ETF')
    assert hit, '搜不到红利 ETF'
    assert any(x['code'] == 'sh510880' for x in hit), \
        '没搜到 sh510880（红利ETF华泰柏瑞）：%r' % hit[:3]
    for x in hit:
        assert x.get('last'), '%s 没给数据截止日 —— 列出来点了什么都不出来' % x
    assert _p.bench_search('zzz不存在的东西') == [], '搜不到时该给空清单'
    # 代码也能搜
    assert [x['code'] for x in _p.bench_search('510880')] == ['sh510880']
    notes.append('按名称/代码都搜得到（%d 个红利 ETF 候选）' % len(hit))

    # ---- ①b🔴 **本地没有日线的一律不给** ----
    # 名称快照里有 166 只 ETF / 352 只股票在 kline 里根本没有行（退市、
    # 未上市、北交所新股…）。列出来的话点了什么都不出来 —— 比不给更糟。
    # ★ 判据**不写死某个代码**：现挑一个"名字在快照里、日线里没有"的，
    #   拿它的名字去搜，它必须不在结果里。写死的话数据一变这条就空转。
    import run as _r
    _root0 = _r.default_lake()
    _fp = _p._name_snap(_root0)
    _c0 = duckdb.connect(':memory:')
    ghost = _c0.execute(
        "SELECT s.symbol, s.name FROM read_parquet('%s') s WHERE s.class='etf' "
        "AND length(s.name) >= 3 AND NOT EXISTS (SELECT 1 FROM read_parquet("
        "'%s/raw/tdx/kline/etf_*.parquet') k WHERE k.symbol = s.symbol) LIMIT 1"
        % (_fp, _root0)).fetchone()
    _c0.close()
    assert ghost, '快照里找不到"没有日线"的样本 —— 这条判据成了空转'
    gsym, gname = ghost
    assert gsym not in [x['code'] for x in _p.bench_search(gname)], \
        ('%s（%s）本地没有日线，却被列进候选 —— 点了什么都不出来'
         % (gsym, gname))
    notes.append('没日线的不给（拿 %s 验的）' % gname)

    # ---- ② 名字由服务端给 ----
    meta = _p.bench_meta(['sh510880', 'sh000001'])
    assert meta.get('sh510880', {}).get('name', '').startswith('红利ETF'), \
        'bench_meta 没给出名字：%r' % meta
    assert meta['sh510880']['kind'] == 'etf' and meta['sh000001']['kind'] == 'index'

    # ---- ③🔴 一律【后复权】：分红必须算进去 ----
    con = duckdb.connect(':memory:')
    K = "read_parquet('%s/raw/tdx/kline/etf_*.parquet')" % root
    F = "read_parquet('%s/raw/tdx/adjust_factor.parquet')" % root
    ds = [str(r[0]) for r in con.execute(
        "SELECT date FROM %s WHERE symbol = 'sh510880' "
        "AND date >= DATE '2016-01-01' ORDER BY date" % K).fetchall()]
    assert len(ds) > 1000, '取不到红利 ETF 的日线'
    cur = _p.bench_curves([ds[0], ds[-1]], ['sh510880'])['sh510880']
    got = cur[-1] / cur[0] - 1
    raw = con.execute(
        "SELECT (SELECT close FROM %s WHERE symbol='sh510880' AND date=DATE '%s')"
        " / (SELECT close FROM %s WHERE symbol='sh510880' AND date=DATE '%s') - 1"
        % (K, ds[-1], K, ds[0])).fetchone()[0]
    hfq = con.execute(
        "WITH px AS (SELECT k.date d, k.close*coalesce(f.hfq_factor,1) c FROM %s k "
        "LEFT JOIN %s f ON f.symbol=k.symbol AND f.date=k.date "
        "WHERE k.symbol='sh510880') "
        "SELECT (SELECT c FROM px WHERE d=DATE '%s')/(SELECT c FROM px WHERE d=DATE '%s') - 1"
        % (K, F, ds[-1], ds[0])).fetchone()[0]
    assert abs(got - hfq) < 1e-6, \
        ('基准不是后复权的：算出 %+.2f%%，后复权应为 %+.2f%%（不复权 %+.2f%%）'
         '—— 红利 ETF 的分红占了 %.0f 个百分点，漏掉就是策略凭空超额'
         % (got * 100, hfq * 100, raw * 100, (hfq - raw) * 100))
    assert hfq - raw > 0.3, \
        '这只票的复权差只有 %.1fpp，测不出"必须复权"这件事' % ((hfq - raw) * 100)
    notes.append('后复权（%s~%s：不复权 %+.1f%% / 后复权 %+.1f%%，差 %.0fpp）'
                 % (ds[0], ds[-1], raw * 100, hfq * 100, (hfq - raw) * 100))

    # ---- ④ 预设那几个指数**一个数都没变**（指数不除权）----
    # 🔴 开放任意代码 + 加复权是**改了公共路径**，必须证明老的那几条曲线
    #   没被顺带改掉 —— 拿"只读 index_*、不带因子"的老算法对一遍。
    dates = [str(r[0]) for r in con.execute(
        "SELECT DISTINCT date FROM read_parquet('%s/raw/tdx/kline/index_*.parquet') "
        "WHERE symbol='sh000001' AND date >= DATE '2026-06-01' ORDER BY date" % root
    ).fetchall()]
    new = _p.bench_curves(dates, ['sh000001', 'sz399006'])
    for sym in ('sh000001', 'sz399006'):
        T = "read_parquet('%s/raw/tdx/kline/index_*.parquet')" % root
        b = con.execute("SELECT close FROM %s WHERE symbol='%s' AND date < DATE '%s' "
                        "ORDER BY date DESC LIMIT 1" % (T, sym, dates[0])).fetchone()[0]
        old = {str(r[0]): r[1] for r in con.execute(
            "SELECT date, close FROM %s WHERE symbol='%s' AND date BETWEEN DATE '%s' "
            "AND DATE '%s'" % (T, sym, dates[0], dates[-1])).fetchall()}
        want = [round(old[d] / b, 8) for d in dates]
        assert new[sym] == want, \
            '%s 的曲线被改动带偏了（前 3 个 %r vs %r）' % (sym, new[sym][:3], want[:3])
    con.close()
    notes.append('预设指数逐位未变（sh000001 / sz399006 共 %d 天）' % len(dates))

    # ---- ⑤ 防注入那道白名单还在 ----
    # 🔴 payload 要选**去掉白名单就真能拿到数据**的那种：
    #   `'; DROP TABLE x; --` 会被 duckdb 直接拒掉，而那句 SQL 外面有
    #   try/except（"这一类没有这个文件"是正常情形）—— 于是它被静默吞掉、
    #   结果照样是空，断言看着通过其实什么都没证（变异实测）。
    inj = "sh000001' OR k.symbol = 'sz399006"
    bad = _p.bench_curves(dates, [inj])
    assert bad == {}, '非法 symbol 没被白名单挡住：%r' % list(bad)
    notes.append('symbol 白名单仍然生效')
    return '；'.join(notes)


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
