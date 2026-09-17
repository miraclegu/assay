# -*- coding: utf-8 -*-
"""回测引擎与策略纪律：PIT / 成本 / FIFO / 分红 / 涨跌停 / 确定性

这一份是 `selftest.py` 按**产品域**拆出来的一块（2026-09-17，见
`tests/_base.py` 的说明）。判据、注释、教训一个字都没动 —— 拆的是**归属**，
不是内容。
"""
from tests._base import *          # noqa: F401,F403  框架 + 共用辅助
from tests._base import (CASES, JQ, REPO, case, _run, _pages, _web_files,  # noqa: F401
                         _kset, _kmain, _kfq, _klog, _lp_tab, _via_pop)
import os, re, sys, io, json, glob, time, shutil, subprocess, datetime  # noqa: E401,F401


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


@case('JQ 策略：与本地归档的关联', tag='fast')
def t_jq():
    """聚宽版的 LOCAL_PORT 必须是断言而不是注释。

    关联写成注释留不住：本地策略一改、标星一换，注释还在那儿，人却不会去改它。
    check.py 核 run_id 在不在归档、params/区间/本金/指标是否对得上 ——
    反向验证过四种改法（改 run_id / params / cash / metrics）都会红。
    """
    import subprocess
    root = REPO
    if not os.path.isdir(os.path.join(root, 'jq', 'strategies')):
        return '跳过（jq/ 尚未建立）'
    r = subprocess.run([sys.executable, 'jq/check.py'],
                       capture_output=True, text=True, cwd=root)
    assert r.returncode == 0, 'jq/check.py 失败:\n%s' % (r.stdout + r.stderr)[-600:]
    ok = [l for l in r.stdout.splitlines() if l.strip().startswith('v ')]
    assert ok, 'check.py 没有输出通过行:\n%s' % r.stdout[-400:]
    n_align = sum(int(x) for l in ok for x in re.findall(r'对照 (\d+) 条', l))
    return '%d 个聚宽策略关联核对通过（选股参数逐项对照 %d 条）' % (len(ok), n_align)


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
                       cwd=REPO)
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
    root = REPO
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
    P = os.path.join(REPO,
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
    # 🔴🔴 **这条用例从加进来（2026-09-14）就一直在跳过**，报绿、耗时 0.0s，
    #   一个断言都没执行 —— 因为它找的是 `runs/_fqtest/` 这个**专用测试分组**，
    #   而那个分组从来没有人去创建过。盘上明明有 8 个带 `fills.parquet` 的
    #   归档，只是在别的分组下。
    #   ★ 这正是「持仓页签」那个 bug 的同款陷阱：**判据依赖"盘上恰好有什么"**。
    #     而跳过比空转更糟 —— 空转至少跑了，跳过连跑都没跑，报告还说它绿了。
    #   ★ 所以扫**全部归档**，并在一条都没有时**报失败而不是跳过**：
    #     2026-09-14 起新归档都带 fills.parquet，一个都找不到就说明那条链断了。
    cand = sorted(_g.glob('runs/*/*/*/fills.parquet'))
    assert cand, \
        ('一个带 `fills.parquet` 的归档都没有 —— 2026-09-14 起 broker 会逐笔'
         '记成交，新归档都该有它。跑一次回测就能生成；'
         '要是跑了还没有，那就是 broker 那条链断了（而它不报错）')
    d = os.path.dirname(cand[-1])
    rid = os.path.basename(d)
    fl = _pd.read_parquet(cand[-1])
    assert len(fl) > 0, 'fills.parquet 是空的'
    assert {'date', 'code', 'side', 'shares', 'price'} <= set(fl.columns), \
        'fills.parquet 缺字段：%s' % list(fl.columns)
    # 🔴 **整手只对【买入】成立。** 这条用例一开始跑就抓到 22 笔"不整手"，
    #   查下来**全是卖出** —— 持有期间送股 / 转增会产生不足 100 股的零股，
    #   清仓时连零股一起卖，那是**真实行为不是 bug**（A 股允许零股卖出、
    #   不允许零股买入）。实测 8 个归档：买入 1071 笔**零例外**全整手，
    #   卖出 1048 笔里 28 笔带零股。
    #   ★ 这个错判被"用例一直跳过"藏了三天 —— 它当初要是真跑过，
    #     作者当场就会发现判据写宽了。
    buy = fl[fl['side'] == 'buy']
    assert len(buy) > 0, 'fills 里没有买入'
    bad = buy[(buy['shares'] % 100) != 0]
    assert bad.empty, \
        ('fills.parquet 里有 %d 笔**买入**不是整手 —— 它记的是撮合当场的'
         '真实股数，而 A 股不允许零股买入：\n%s'
         % (len(bad), bad.head(3).to_string()))
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
    assert all(float(r['shares']) % 100 == 0
               for r in got['rows'] if r.get('side') == 'buy'), \
        '接口回出来的**买入**份额不是整手'
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


