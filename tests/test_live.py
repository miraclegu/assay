# -*- coding: utf-8 -*-
"""实盘：账本与信号 / 业绩与基准 / 模拟盘 / 实盘页

这一份是 `selftest.py` 按**产品域**拆出来的一块（2026-09-17，见
`tests/_base.py` 的说明）。判据、注释、教训一个字都没动 —— 拆的是**归属**，
不是内容。
"""
from tests._base import *          # noqa: F401,F403  框架 + 共用辅助
from tests._base import (CASES, JQ, REPO, case, _run, _pages, _web_files,  # noqa: F401
                         _kset, _kmain, _kfq, _klog, _lp_tab, _lp_enter, _via_pop,
                          _lv_open_newform, _lv_new_account,
                          _lv_bind_strategy, _lv_rec_fill)
import os, re, sys, io, json, glob, time, shutil, subprocess, datetime  # noqa: E401,F401


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

    root = REPO
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
        # 🔴 现金里还有【公司行动派现】那一项（2026-09-23 加）——
        #   这个构造买的 601857 在 2026-09-16 除权 0.26 元/股，持 1000 股
        #   -> +260.00。**失败的是断言不是产品**：原来的期望式漏了这一项。
        #   ★ 期望值从**正本**取（`corp.actions`），不写死 260 ——
        #     写死的话分红数据一变它就假失败；而更糟的是反过来：
        #     `corp` 要是静默返回 0，写死的期望**照样绿**（等于没测）。
        import datetime as _dt
        from assay.lv import corp as _corp
        _acts = _corp.actions(['601857.XSHG'], '2026-08-20', '2026-12-31')
        _div = sum(1000 * a['cash'] for a in _acts if a['ex_date'] <= _dt.date.today())
        assert _div > 0, ('构造不对：601857 在这段里没有除权，'
                          '那"派现要进现金"这条就是空转的')
        exp = 100000 - (1000 * 11.42 + r1['fee']) - (100 * 48.78 + 3.21) \
            - (200 * 16.69) + _div
        assert abs(lv.cash('t_fee') - exp) < 1e-6, \
            '现金没把费用扣进去或漏了公司行动派现：%.2f vs %.2f' % (lv.cash('t_fee'), exp)
        # ★ 反向自证：没有公司行动的那只票，现金里不许凭空多出什么。
        assert not _corp.actions(['600012.XSHG'], '2026-08-22', '2026-12-31'), \
            '600012 这段里有公司行动了 —— 下面那条对照失效，换一只'
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
            # 🔴🔴 **构造出"当天有卖出"再断言** —— 上面那条一直是绿的，
            #   因为它跑在一个当天没有任何成交的日子上，而
            #   `positions_valued` 只遍历**当前持仓**：今天卖掉的批次已经
            #   不在里面，实现的那部分一分都没算。用户 2026-09-22 报出来的
            #   就是它（开盘竞价卖出 300980 的涨幅没进当日盈亏，实测漏 460）。
            #   **不构造出卖出，这个断言永远抓不到。**
            _code = '601857.XSHG'
            _sell = 100.0
            _sp = (_pc.get(_code) or 0) + 1.0        # 就按实时价卖
            lv.add_fill('t_twr', _nx, _code, 'sell', _sell, price=_sp,
                        fee=3.0, force_price=True)   # 未来日无行情 -> 显式逃生口
            e5 = lv.equity_curve('t_twr')
            _pv5 = lv.positions_valued('t_twr')
            assert abs(e5['stats']['day_pnl'] - _pv5['pnl_day']) < 0.02, \
                ('有卖出的日子两条对不上：权益曲线 %.2f vs 持仓表 %.2f —— '
                 '持仓表漏了【当天卖出实现】或【当天费用】（构成：持仓 %s / '
                 '已实现 %s / 费用 %s）'
                 % (e5['stats']['day_pnl'], _pv5['pnl_day'],
                    _pv5.get('pnl_day_hold'), _pv5.get('pnl_day_realized'),
                    _pv5.get('pnl_day_fee')))
            # 反向自证：这一笔**真的**产生了已实现与费用，否则上面那条
            # 又退化成"两个相同的数相等"（= 空转）。
            assert _pv5.get('pnl_day_realized'), \
                '构造没生效：当天卖出的已实现是 %s' % _pv5.get('pnl_day_realized')
            assert abs(_pv5['pnl_day_realized'] - _sell * (_sp - _pc[_code])) < 0.02, \
                ('已实现算错：%s 股 × (卖价 %.4f − 昨收 %.4f) 应为 %.2f，实得 %s'
                 % (_sell, _sp, _pc[_code], _sell * (_sp - _pc[_code]),
                    _pv5['pnl_day_realized']))
            assert abs(_pv5['pnl_day_fee'] + 3.0) < 0.01, \
                '当天费用没扣或扣错：%s' % _pv5.get('pnl_day_fee')
            # 三块必须真的加得起来 —— 合计与分项对不上是最难查的那种错
            assert abs(_pv5['pnl_day'] - (_pv5['pnl_day_hold']
                       + _pv5['pnl_day_realized'] + _pv5['pnl_day_fee'])) < 0.01, \
                '合计 != 持仓 + 已实现 + 费用：%s' % _pv5
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
        ec1 = lv.equity_curve('t_seed')
        es = ec1['stats']
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
        ec2 = lv.equity_curve('t_seed')
        es2 = ec2['stats']
        #   🔴🔴 判据要**与盈亏方向无关**。这条断言前后错过两次，
        #     每次都是"判据依赖了写它那天的数据状态"：
        #       ① 第一版 `abs(差) < 0.02` —— 入金日固定、面板每天在长，
        #          摊薄累积越来越大，某天必然越线；
        #       ② 第二版 `es2.twr <= es.twr` —— **写它那天账户是盈利的**，
        #          摊薄把收益率往 0 拉 = 变小。而账户一转亏（TWR −0.0216），
        #          同样的摊薄就把它**往上**拉到 −0.0183，断言当场报
        #          "入金被当成收益了" —— 而那恰恰是正确行为。
        #     **摊薄的本质是把收益率往 0 拉，不是单调变小。**
        #   ★ 所以钉**金额口径**：入金不是收益，`pnl_total` 一分不许动，
        #     而 `net_deposit` 要如实增加。这两条与盈亏符号无关。
        assert abs(es2['pnl_total'] - es['pnl_total']) < 0.02, \
            ('周末入金被当成收益了：累计收益金额从 %.2f 变成 %.2f —— '
             '入金是本金不是利润，`pnl_total = 期末 − 起点 − 净入金`'
             % (es['pnl_total'], es2['pnl_total']))
        assert abs((es2['net_deposit'] or 0) - (es['net_deposit'] or 0)
                   - 200000) < 0.02, \
            ('周末那笔 20 万没进 net_deposit（%s -> %s）—— 落在非交易日的'
             '现金流要按**区间**取，不是"正好落在那天"'
             % (es['net_deposit'], es2['net_deposit']))
        # ★ 摊薄的方向要钉在**逐日收益率**上，不是在 TWR 上。
        #   🔴 「|TWR| 只会变小」**在数学上不成立**（这是这条断言错的第三次）：
        #     TWR = A × B − 1（A = 入金前那段的连乘、B = 入金后那段）。
        #     入金只稀释 B、不动 A，而 A 与 1 差得远时结果可能**远离** 0 ——
        #     构造 A=2.0 / B=0.5 时旧 TWR 恰好 0，把 B 稀释到 0.8 就成了
        #     +0.6，|TWR| 反而变大。现在这个账户上碰巧没触发，
        #     **而"碰巧"正是前两版栽的地方**。
        #   ★ 真正无条件成立的是：**入金那天之后每一天的收益率都被往 0 拉，
        #     之前那几天一个数都不许动**。它与盈亏符号、与 A 离 1 多远都无关。
        DEP = '2026-08-16'
        d1, r1 = ec1['dates'], ec1['day_rets']
        d2, r2 = ec2['dates'], ec2['day_rets']
        assert d1 == d2, '入金不该改变日期轴：%s vs %s' % (d1[:2], d2[:2])
        pre = [i for i, d in enumerate(d1) if d <= DEP]
        post = [i for i, d in enumerate(d1) if d > DEP]
        assert pre and post, \
            '构造不对：入金日 %s 两侧都要有交易日（前 %d 天 / 后 %d 天）' \
            % (DEP, len(pre), len(post))
        bad = [(d1[i], r1[i], r2[i]) for i in pre
               if abs((r1[i] or 0) - (r2[i] or 0)) > 1e-9]
        assert not bad, \
            ('入金【之前】那几天的收益率被改了（%s）—— 现金流只该切开区间，'
             '不该回溯改写历史' % bad[:2])
        worse = [(d1[i], r1[i], r2[i]) for i in post
                 if abs(r2[i] or 0) > abs(r1[i] or 0) + 1e-9]
        assert not worse, \
            ('入金【之后】有几天的 |收益率| 反而变大了（%s）—— 20 万闲置现金'
             '按 0%% 计，只该把每天的收益率往 0 摊薄' % worse[:2])
        # ★ 反向自证：真的有几天被拉动了 —— 否则"没有变大"可能只是因为
        #   两边逐位相同（那时这条断言一个字都没证）。
        moved = [i for i in post
                 if abs((r1[i] or 0) - (r2[i] or 0)) > 1e-9]
        assert moved, \
            ('入金之后没有任何一天的收益率被摊薄 —— 那 20 万根本没进权益，'
             '这条断言是空转的（入金后有 %d 个交易日）' % len(post))

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

    root = REPO
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
            # 🔴 空账本时**两个区都要说清它是什么**，不能是一片空白。
            #   原来这里查的是全局那句「还没有账户」—— 2026-09-17 侧栏改成
            #   「实盘 / 模拟盘」两区之后那句话没有了，每区各给自己的空态说明。
            #   **失败的是断言不是产品**，但它原本要保的东西不能丢：
            #   钉成"两个区都在 + 各自有空态说明"（藏起来的话，从没建过
            #   模拟盘的人根本不知道有这功能）。
            _z = pg.evaluate('''() => [...document.querySelectorAll('.dzone')]
                .map(z => ({zone: z.dataset.zone,
                            items: z.querySelectorAll('.ditem').length,
                            hint: (z.querySelector('.dzempty') || {}).textContent || ''}))''')
            assert [x['zone'] for x in _z] == ['live', 'paper'], \
                '账户区不是【实盘、模拟盘】两个：%s' % _z
            for x in _z:
                assert x['items'] or len(x['hint'].strip()) >= 8, \
                    '空的「%s」区没说明它是什么 —— 一片空白等于藏起来：%s' % (x['zone'], _z)
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
            _lv_new_account(pg, 'UI 测试', 400000)
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
            _lv_bind_strategy(
                pg, 'strategies/小市值/froec_traded.py',
                '{"stop_loss":0.35,"stop_intraday":1,"weekday":%d}' % wd)
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
                REPO,
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


            # ---- 选股理由：业绩页的一个【页签】，一期一段按排名列出 ----
            #   ★ 一开始做成浮层 + 一个 .lvwhy 小链接，实测**人找不到它**
            #     （和旁边的"版本 9FB82061"长得一模一样）——
            #     看不出能点的入口 = 没有入口。于是改成按钮 + 独立页。
            #   🔴 2026-09-22 用户又把它**并进业绩页**（"选股理由应该也内置到
            #     业绩里，执行差异都已经在里面了"）。所以账户页那个按钮没了，
            #     入口是「📊 业绩」-> 「选股理由」页签 —— **失败的是断言不是
            #     产品**，而它要保的（一期一段 / 按排名 / 只列调仓日 / 表头
            #     top=0 / 没有 undefined）一条都没丢。
            assert pg.locator('table.lvbuy span.lvq').count() == nbuy, \
                '每一行买入都该有一个「?」标出选中理由'
            # 当前账户 id 从侧栏高亮那一项取（这一段比下面的 `aid` 早）
            _aid = pg.locator('.ditem.on').get_attribute('href').split('/')[-1]
            assert pg.locator('#lvbody .lvhead a.btn',
                              has_text='选股理由').count() == 0, \
                '账户页那一排还留着「选股理由」按钮 —— 它已经并进业绩页了'
            # 🔴 对不上任何一期的持仓**不许**有出处标记 —— 硬凑一个出处
            #   比没有更糟（这些成交不是照信号做的）。
            assert pg.locator('#lvbody table.lvpos a.lvq').count() == 0, \
                '这些成交对不上任何一期信号，却给了"出处"标记'
            _lp_enter(pg)
            _lp_tab(pg, '选股理由')
            pg.wait_for_selector('#lp_why .whysec', timeout=60000)
            pg.wait_for_timeout(400)
            assert '选股理由' in pg.locator('#lp_why .ttl').first.inner_text()
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
            _why_msg = ('选股理由页签：%d 段（只列调仓日）、候选池 %d 行按排名升序、'
                        '.pw 表头 top=0' % (_secs, _rows.count() - 1))
            # 🔴 旧 hash 必须仍然到得了那一页签（书签 + 持仓行那个 `?`）——
            #   合并最容易丢的就是这个，**而 404 之后"点了没反应"最难查**。
            pg.goto(base + '#/live/%s/why' % _aid, wait_until='networkidle')
            pg.wait_for_selector('#lp_why .whysec', timeout=60000)
            assert 'tab=why' in pg.evaluate('()=>location.hash'), \
                '旧 hash #/live/<id>/why 没 redirect 到选股理由页签：%s' \
                % pg.evaluate('()=>location.hash')
            assert pg.locator('#lptabs div.on').inner_text().strip() == '选股理由', \
                '重定向过去了但激活的不是「选股理由」页签'
            pg.goto(base + '#/live/%s' % _aid, wait_until='networkidle')  # 回账户
            pg.wait_for_selector('#lvrec', timeout=60000)
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
            # 建账户表单默认收起（它平时不用），点「+ 新建」才展开。
            # 🔴 表单现在**长在它所属的那个区里**（`#nf_live` / `#nf_paper`），
            #   不再是全局一个 `#nform` —— mode 由区决定，表单里没有
            #   "这是模拟盘吗"这个选项，也就没法填错。
            assert pg.locator('#nf_live .nform').count() == 0, \
                '创建账户的表单应默认收起（点「+ 新建」才展开）'
            _lv_open_newform(pg, 'live')
            assert pg.locator('#nf_live .nform').is_visible(), '点「+ 新建」应展开'
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

            # ---- 记一笔：一个入口、三个 tab ----
            # ★ 2026-09-22 从两个变成三个（「补录」从常规操作里分出来，
            #   用户："补录功能可以开单独的按钮……而不是混在正常的操作里"）。
            #   **失败的是断言不是产品** —— 但它保的"入口只有一个、页签齐全"
            #   不能丢，所以是钉新规矩，不是删掉这条。
            #   页签各自的行为由「记一笔：买是搜索下拉…」那条专门验。
            pg.click('#lvrec')
            pg.wait_for_selector('#rfill', timeout=10000)
            tabs = pg.locator('.rtab').all_inner_texts()
            assert tabs == ['成交', '补录', '现金'], \
                '记一笔应有成交/补录/现金三个 tab，实得 %s' % tabs
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
                _lv_rec_fill(pg, code, q, price=px,
                             fee=(None if fv == '' else fv))
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
            _lv_rec_fill(pg, '601857.XSHG', '1000', price='11.42', fee='7.77', date='2026-09-02')
            pg.wait_for_timeout(1500)
            # 🔴 现金差里还夹着【公司行动派现】：这一笔买的 601857 在 2026-09-16
            #   除权 0.26 元/股，持 1000 股 -> 现金 +260，于是 `b4 - 现在` 会
            #   **少 260**。**失败的是断言不是产品**（2026-09-23）。
            #   ★ 期望从正本取、且**反向自证它非 0** —— 写死 260 的话，
            #     corp 静默返回 0 时这条照样绿（等于把新功能测没了）。
            from assay.lv import corp as _corp
            import datetime as _dt2
            _div2 = sum(1000 * a['cash'] for a in
                        _corp.actions(['601857.XSHG'], '2026-09-02', '2026-12-31')
                        if a['ex_date'] <= _dt2.date.today())
            assert _div2 > 0, ('构造不对：601857 在这段里没有除权，'
                               '那"派现进现金"这条在页面上是空转的')
            paid = round(b4 - _cash() - 11420 + _div2, 2)
            assert abs(paid - 7.77) < 0.011, \
                '手填费用应优先于费率：填 7.77 实得 %.2f（已扣掉派现 %.2f）' % (paid, _div2)

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
            # 价格/费用**留空** —— 留空 = 按成交日开盘价 / 按费率
            _lv_rec_fill(pg, '603889.XSHG', '100', date='2026-09-01')
            pg.wait_for_timeout(300)
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
            _lv_rec_fill(pg, '603889.XSHG', '100', price='', date='2028-01-03')
            pg.wait_for_timeout(1500)
            _m = pg.locator('#rfill').inner_text()
            assert '还没同步' in _m or '取不到' in _m, \
                ('取不到当日行情时页面要说清原因。#fmsg=%r / #fd=%r / #fc=%r'
                 ' / #fq=%r'
                 % (pg.locator('#fmsg').inner_text()[:300],
                    pg.input_value('#fd'), pg.input_value('#fc'),
                    pg.input_value('#fq')))
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

            # ---- 交易明细（原「流水独立页」）+ 分页 + 冲正 ----
            # 🔴🔴 2026-09-22 用户把流水独立页**并进了业绩页的「交易明细」
            #   页签**（"和业绩里的交易记录有所重复"）。所以入口从
            #   `a[href*="/fills"]` 变成「📊 业绩」-> 那个页签 ——
            #   **失败的是断言不是产品**，它原本要保的那几条一个不少：
            #   列序 / 名称那一格带小字代码 / 名称不是纯数字 / 分页 /
            #   冲正是**追加**一条且两行都划掉 / "取的开盘价"有标记。
            aid = pg.locator('.ditem.on').get_attribute('href').split('/')[-1]
            _lp_enter(pg)
            _lp_tab(pg, '交易明细')
            pg.wait_for_selector('#lp_fill table.lvt', timeout=60000)
            FT = '#lp_fill table.lvt'
            # 代码/名称也要能点开个股
            assert pg.locator(FT + ' a[href*="/stock.html"]').count() >= 2, \
                '交易明细的代码/名称没链到个股页'
            # 列序按【看的顺序】：哪天、买还是卖、哪只票、什么价、多少股、多少钱。
            # 录入时间与来源是审计信息，平时不看，排在最后。
            _fh = [x.strip() for x in pg.locator(FT + ' th').all_inner_texts()]
            # ★ 代码与名称合成一格（2026-09-16 全站统一）—— 列序其余不变。
            _want = ['成交日', '方向', '名称', '价格', '股数', '金额', '费用']
            assert _fh[:len(_want)] == _want, '交易明细列序不对：%s' % _fh
            assert pg.locator(FT + ' tr:nth-child(2) td .cd, '
                              + FT + ' tr:nth-child(2) td .cd0').count() >= 1, \
                '交易明细的名称格里没有小字代码'
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
                "'#lp_fill table.lvt th')];"
                " const i = ths.findIndex(e => e.textContent.trim() === want);"
                " if (i < 0) return null;"
                " return [...document.querySelectorAll('#lp_fill table.lvt tr')]"
                "   .slice(1).map(tr => {const td = tr.cells[i]; if (!td) return '';"
                "     const cd = td.querySelector('.cd,.cd0');"
                "     const all = (td.textContent || '').trim();"
                "     return cd ? all.replace((cd.textContent || '').trim(), '').trim()"
                "               : all;});}", '名称')
            assert _nm is not None, '交易明细表找不到「名称」列'
            assert any(_nm), '交易明细的「名称」列全空 —— 服务端没补名称'
            assert not any(x and x.replace('.', '').isdigit() for x in _nm), \
                '名称那一部分是纯数字，可能列错位了：%s' % _nm[:4]
            # 分页条（上下各一套，走 lpNav 的 class 不是 id）
            assert pg.locator('#lp_fill .pg').count() >= 2, '交易明细没有分页控件'
            assert pg.locator('#lp_fill .pg button.fp').first.is_disabled(), \
                '第一页的「上一页」应置灰'
            # 🔴 「每页 N 条」回显的必须是**这一页自己**的分页状态（LPF），
            #   不是清仓记录那份（lpNav 原来按 cls 猜，猜错了不报错）。
            _per = pg.eval_on_selector('#lp_fill .pg select.fs', 'e=>e.value')
            _lim = pg.evaluate('()=>String(LPF.lim)')
            assert _per == _lim, ('交易明细的「每页」回显 %s 而 LPF.lim=%s'
                                  ' —— 回显的是别人的分页状态' % (_per, _lim))
            n_before = len(lv.fills(aid))
            pg.locator('#lp_fill a.lvrv').first.click()
            pg.wait_for_timeout(1800)
            assert len(lv.fills(aid)) == n_before + 1, '冲正应【追加】一条'
            assert pg.locator('#lp_fill .lvrev').count() >= 2, \
                '原记录与冲正记录都该划掉'
            # 取的开盘价要看得出来（与"估"的费用同理，别混成券商回报）
            assert pg.locator(FT + ' td span.lvwhy').count() >= 1, \
                '"取的开盘价"应有标记，否则分不清是券商回报还是本地取的'

            # 回账户
            pg.goto(base + '#/live/%s' % aid, wait_until='networkidle')
            pg.wait_for_selector('#lvrec', timeout=60000)
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
                    '交易明细分页；策略单一入口(未绑定也能开)；'
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
                    '排版：交易明细列序(日/方向/名称/价/量/额/费,录入时间与来源置尾)、'
                    '持仓代码与名称分列、数据日与报价时间合成一个标签、'
                    '汇总数字只在 KPI 出现一次、KPI 板无 undefined、'
                    '侧栏可收起(轨上仍见告警点)、新建表单默认收起、待办按 alert 折叠；'
                    '改名；冲正追加并划掉；设置无 undefined 且每项有标签；'
                    '业绩板：盘中补点后标「今日/盘中时刻」、交易费用独立一格'
                    '（占本金，短样本不折年化）、ⓘ 说清 TWR/盘中/年化拖累；'
                    '旧进程有横幅；' + _why_msg + '（带流通市值/PB/ROE加速度，'
                    '无 undefined，旧 hash 仍 redirect 得到）；'
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
            REPO,
            'assay', 'srv', 'live.py'), encoding='utf-8').read()
        assert 'in_session()' in src and 'is_trading_day()' in src, \
            ('rt_live 该调 realtime 的那两个函数，不要在这里另写时段判据')

        js = open(os.path.join(
            REPO,
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
            # 🔴 **顺序也要钉**，不只是"存在" —— 用户两次都明确给了顺序，
            #   只查"在不在"的话顺序被改了不会有人发现。
            #   2026-09-15："业绩曲线、收益明细、执行差异、每日持仓、
            #                交易记录、清仓记录"
            #   2026-09-22（并进流水与选股理由、拆出盈亏榜之后重排）：
            #     "业绩曲线、业绩明细、每日持仓、交易明细、清仓记录、
            #      盈亏榜、选股理由、执行差异"
            #   —— **执行差异挪到最后、选股理由在它前面**，那是他的使用顺序。
            #   2026-09-24：「公司行动」插在盈亏榜与选股理由之间 ——
            #     主视图那行原来把逐条事件**横排**铺开，用户指出
            #     「如果行动项很多的话可能会排列有点拥挤」，所以明细搬来这里
            #     （会越来越长的复盘信息都归业绩页，同「成交流水并进来」那条）。
            WANT = ['业绩曲线', '业绩明细', '每日持仓', '交易明细',
                    '清仓记录', '盈亏榜', '公司行动', '选股理由', '执行差异']
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
            open_tab('交易明细')
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

            # ---- ④b 公司行动那一页签（2026-09-24 新增）----
            #   🔴 它**不依赖权益曲线**，而且要列**已清仓**的票 ——
            #     那正是主视图那行摘要给不出来的两样（持仓表里早没有它们了）。
            #   🔴 **这一段自己挑账户**：上面那个是按"有买有卖"挑的，而
            #     发生过公司行动的是另一个 —— 沿用它的话整段空转
            #     （第一版就是这样，靠它自己那句"没验到"暴露出来的；
            #      同「断言要在能触发的构造上跑」那条）。
            _cid, _ce = None, []
            for _a in lv.load_accounts():
                _e = lv.corp_summary(_a['id'])['events']
                if _e:
                    _cid, _ce = _a['id'], _e
                    break
            assert _cid, ('构造不对：没有一个账户发生过公司行动 —— '
                          '那这一页签的判据全是空转的')
            # 顺带把「?tab=corp 真的落到这一页」一起验了（主视图摘要的入口）
            pg.goto('http://127.0.0.1:%d/#/live/%s/perf?tab=corp' % (port, _cid),
                    wait_until='domcontentloaded')
            pg.wait_for_selector('#lptabs div', timeout=30000)
            pg.wait_for_timeout(2500)
            _on = pg.evaluate(
                "() => [...document.querySelectorAll('#lptabs div')]"
                ".filter(e => e.classList.contains('on')).map(e => e.textContent.trim())")
            assert _on == ['公司行动'], \
                '?tab=corp 没落到公司行动那一页：%s' % _on
            assert pg.evaluate(_VISJS) == 1, '切到公司行动之后有多个 pane 同时显示'
            _rows = pg.evaluate("""() => [...document.querySelectorAll(
                '[id^=lpp].on table.lvt tr')].slice(1)
                .map(tr => [...tr.children].map(td => td.textContent.trim()))""")
            _txt = pg.evaluate(
                "() => document.querySelector('[id^=lpp].on').innerText")
            assert len(_rows) == len(_ce), \
                ('公司行动页签列了 %d 行，而账上发生过 %d 次' % (len(_rows), len(_ce)))
            _by_date = {r[0]: r for r in _rows}
            for _e in _ce:
                assert _e['date'] in _txt, '明细里缺 %s 那一条' % _e['date']
                # 🔴 **股数那一列要逐格对**：送转会把它变成 `1000 → 1480`，
                #   而派现是一个数。不验的话把整列删掉照样绿（M9 实测漏过），
                #   而那一列正是"送转改了股数"唯一看得出来的地方。
                _r = _by_date.get(_e['date'])
                assert _r and len(_r) == 6, '明细表列数不对：%s' % (_r,)
                _want = (format(_e['shares_before'], ',')
                         if _e['shares_after'] == _e['shares_before']
                         else '%s → %s' % (format(_e['shares_before'], ','),
                                           format(_e['shares_after'], ',')))
                assert _r[4] == _want, \
                    ('%s 那一行的股数列是 %r，应是 %r'
                     % (_e['date'], _r[4], _want))
                # 🔴 名称要是**名称**不是代码 —— 事件里含已清仓的票，页面拿
                #   "当前持仓"去配是配不上的（那正是要在服务端补名的理由）
                assert _e['name'] in _txt and _e['name'] != _e['code'], \
                    '明细里 %s 只有代码没有名称' % _e['code']
                if _e['cash']:
                    assert num_ok(_txt, _e['cash']), \
                        '明细没写出 %s 派回多少（%.2f）' % (_e['code'], _e['cash'])
            assert '**' not in _txt, '页面文案里有 markdown 星号'
            notes.append('公司行动 %d 条逐条成表（?tab=corp 直达、名称与金额都在）'
                         % len(_ce))
            pg.goto('http://127.0.0.1:%d/#/live/%s/perf' % (port, aid),
                    wait_until='domcontentloaded')
            pg.wait_for_selector('#lptabs div', timeout=30000)
            pg.wait_for_timeout(1500)

            # ---- ⑤ 🔴🔴 冲正**必须在这里**（2026-09-22 起流水独立页并了进来）
            #   这条断言原来是反的（"业绩页不许有冲正，它属于流水页那条链"）
            #   —— **失败的是断言不是产品**：流水页没有入口之后，那条链的
            #   另一头就没了，而冲正是 append-only 账本**唯一**的更正手段。
            #   说了不能做却不给出路，最后会变成绕过整个入口（同 backLink）。
            open_tab('交易明细')
            assert pg.locator('[id^=lpp].on a.lvrv').count() >= 1, \
                ('交易明细里没有「冲正」—— 流水独立页已经并进来了，'
                 '这里不给的话录错了就再也改不了（账本只追加）')
            # ★ 反向自证：已冲正/已作废的那几行本来就不给按钮，所以
            #   "有按钮的行数 <= 总行数"必须成立 —— 否则是按钮画多了。
            _nrow = pg.locator('[id^=lpp].on table.lvt tr').count() - 1
            _nrv = pg.locator('[id^=lpp].on a.lvrv').count()
            assert 0 < _nrv <= _nrow, '冲正按钮 %d 个 / 数据 %d 行' % (_nrv, _nrow)
            # 🔴 旧的「去流水页更正 ›」链接要**删干净**：那一页已经没有入口了，
            #   点过去只会被 redirect 弹回来（一个绕圈的死链）。
            assert pg.locator('[id^=lpp].on a[href*="/fills"]').count() == 0, \
                '交易明细里还留着「去流水页」的链接 —— 那一页已经并过来了'
            notes.append('冲正已随流水页并入「交易明细」（%d/%d 行可冲正）'
                         % (_nrv, _nrow))
            assert not errs, 'JS 报错：%s' % errs[:3]
            br.close()
    finally:
        sv.ALLOW_LIVE = old_live
        httpd.shutdown()
    return '；'.join(notes)


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
            _lp_enter(pg)
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
            # ---- 入口：账户按钮排里的「📊 业绩」 ----
            # 🔴 这条断言**被改动作废过一次**（2026-09-22）：原版钉的是
            #   KPI 板那个 `a.lpin`「累计收益 ›」链接 —— 而换掉它正是这次
            #   要做的事（用户：「点击累计收益，进去的其实不仅仅是累计收益，
            #   是一个综合的面板，从累计收益这边进去感觉不太合适」）。
            #   **失败的是断言不是产品**，但它原本要保的东西一条都不能丢：
            #   ① 入口**看得出能点**（按钮，不是一个画成标签的东西 ——
            #      「看不出能点的入口 = 没有入口」，选股理由那次实测人找不到）；
            #   ② 名字**说得出里面是什么**（这是这次新加的那一条）；
            #   ③ 点了真的到得了。
            pg.goto('http://127.0.0.1:%d/#/live/%s' % (port, aid),
                    wait_until='networkidle')
            pg.wait_for_selector('#lvbody .lvsec', timeout=90000)
            pg.wait_for_selector('#lvperf', timeout=90000)
            ent = pg.evaluate('''() => { const a = document.querySelector('#lvperf');
                if(!a) return null;
                const cs = getComputedStyle(a);
                return {txt: a.innerText.trim(), cur: cs.cursor,
                        bd: cs.borderTopWidth, vis: a.offsetWidth > 0};
            }''')
            assert ent and ent['vis'], '业绩页入口不可见'
            assert '业绩' in ent['txt'], \
                ('入口的名字要说得出里面是什么 —— 那一页有六个页签，'
                 '叫「累计收益」等于给 destination 起了个错名字。实际：%r'
                 % ent['txt'])
            assert ent['cur'] == 'pointer' and ent['bd'] != '0px', \
                ('入口要**看得出能点**（按钮：手型光标 + 边框），'
                 '画成灰字标签的话人不会去点。实际：%r' % ent)
            # 🔴 「累计收益」那一格**不再是入口** —— 留一个半对的链接
            #   与一个对的按钮并存，等于两个入口一个名字是错的。
            assert pg.locator('a.lpin').count() == 0, \
                '「累计收益」那一格不该再是链接（入口已经挪到「📊 业绩」按钮）'
            pg.click('#lvperf')
            _lp_tab(pg, '业绩明细')
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
            _pane_has('业绩明细', '业绩明细')
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
            _lp_tab(pg, '业绩明细')
            assert pg.locator('#lp_tbl a.lpg.on').inner_text() == '日', \
                '默认粒度该是「日」（打开就看到这个月每天怎么样）'
            assert pg.locator('#lp_tbl a.lps.on').inner_text() == '两者', \
                '默认读数该是「两者」（收益率 + 金额）'
            assert pg.locator('#lp_tbl .calg').count() == 1, \
                '日粒度该画自然日历方格（.calg）'
            n_td = pg.locator('#lp_tbl .cday:not(.off):not(.pad)').count()
            assert n_td > 0, '日历里没有交易日格子'
            assert pg.locator('#lp_tbl .cday.off').count() > 0, \
                '非交易日该打斜纹（.cday.off）—— 不然看不出哪天没开市'
            assert pg.locator('#lp_tbl .lgd').count() == 1, \
                ('要有图例 —— 🔴 弱强度格子的底色近乎透明，'
                 '方向全靠数字前的 +/- 号，图例得说明这件事')
            #   一格里两个读数都要有
            c0 = pg.locator('#lp_tbl .cday:not(.off):not(.pad)').first
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
                pg.locator('#lp_tbl .cday:not(.off):not(.pad)').first
                .inner_text().split())
            assert '%' not in only_pnl, '「金额」模式不该有 %%：%s' % only_pnl
            pg.locator('#lp_tbl a.lps[data-s="ret"]').click()
            pg.wait_for_timeout(250)
            only_ret = ' '.join(
                pg.locator('#lp_tbl .cday:not(.off):not(.pad)').first
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
                ('KPI 板**每一格**都要标「· 全程」（图是按区间画的）——'
                 '2026-09-22 加交易统计那四格时它当场抓到了漏标：%s' % kk)
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
                'nav[0] 含建仓当天收益；入口是「📊 业绩」按钮（名字说得出里面是什么）；'
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
        # 🔴 **绑了策略的模拟盘现在【不许】手工录**（2026-09-18 加的规则，
        #   理由是手工那几笔引擎不会跑出来 -> 下次推进必然 mismatch）。
        #   于是这一步的老写法当场被拦 —— **失败的是构造不是产品**：
        #   它要证的是「reset 不碰手工录的那几笔」，而那种记录**真实存在**
        #   （在绑策略之前录的，或者手工模拟盘后来才绑）。
        #   所以照那个真实路径构造：先解绑、录一笔、再绑回去。
        _accs = json.load(open(os.path.join(lv.LIVE, 'accounts.json')))
        _sha_bak = None
        for _x in _accs:
            if _x['id'] == 'sim':
                _sha_bak = _x.get('code_sha256')
                _x['code_sha256'] = None
        json.dump(_accs, open(os.path.join(lv.LIVE, 'accounts.json'), 'w'),
                  ensure_ascii=False)
        lv.add_fill('sim', '2026-08-05', FR and fills[0]['code'], 'buy', 100,
                    price=10.0, fee=5.0, source='manual', force_price=True)
        _accs = json.load(open(os.path.join(lv.LIVE, 'accounts.json')))
        for _x in _accs:
            if _x['id'] == 'sim':
                _x['code_sha256'] = _sha_bak
        json.dump(_accs, open(os.path.join(lv.LIVE, 'accounts.json'), 'w'),
                  ensure_ascii=False)
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
    src = open(os.path.join(REPO,
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

    here = REPO
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
            # ---- ④b🔴 自定义基准是一份【清单】，最多 3 个，点别的不消失 ----
            # 用户 2026-09-16："自定义基准应该最多能保存 3 个，超出三个则
            #   必须先删除（右上角 X 掉）才能新建自定义。**不要一点击其他
            #   的基准，已选中过的自定义基准框就消失了**。"
            # 原来 chip 的渲染条件是"`LPB[0]` 恰好是它" —— 切去看一眼上证，
            # 辛苦搜出来的那只票就没了，想再比还得重新搜一遍。
            def _add_cus(kw, code):
                pg.click('#lp_bmore')
                pg.wait_for_selector('#lp_bq', timeout=6000)
                pg.fill('#lp_bq', kw)
                pg.wait_for_selector('#lp_bhit .lpbhit', timeout=9000)
                pg.evaluate("(c)=>[...document.querySelectorAll('#lp_bhit .lpbhit')]"
                            ".find(a=>a.dataset.b===c).click()", code)
                pg.wait_for_timeout(2500)
            _cus = lambda: pg.evaluate(
                "()=>[...document.querySelectorAll('.chipw a.lpb')]"
                ".map(a=>a.dataset.b)")
            for _kw, _c in (('上证50', 'sh000016'), ('沪深300', 'sh000300')):
                _add_cus(_kw, _c)
            assert _cus() == ['sh510880', 'sh000016', 'sh000300'], \
                '三个自定义 chip 没都留着：%r' % (_cus(),)
            # 🔴 满了：不给开搜索框，而且要**说清怎么腾位置**
            pg.click('#lp_bmore')
            pg.wait_for_timeout(500)
            assert pg.locator('#lp_bq').count() == 0, \
                '已经 3 个了还让接着加 —— 上限形同虚设'
            _msg = pg.evaluate("()=>{const e=document.querySelector('#lp_bfind');"
                               " return e ? e.innerText.trim() : ''}")
            assert '3' in _msg and '×' in _msg, \
                ('到上限只是静默不动（提示 %r）—— 要说清怎么腾位置，'
                 '不然人只会觉得"点了没反应"（同「+ 副图」到上限那条）' % _msg)
            # 🔴 上限有**两道**：按钮那道（上面刚验）与数据那道（`lpcAdd`）。
            #   只点按钮的话，数据那道**在 UI 上不可达** —— 去掉它照样全绿
            #   （变异实测）。所以直接调它一次。
            assert pg.evaluate("()=>lpcAdd('sh000905')") is False, \
                '满了之后 lpcAdd 还返回成功 —— 数据层那道上限形同虚设'
            assert len(pg.evaluate('()=>LPCUS')) == 3, \
                'lpcAdd 被拒了却还是把它塞进了清单：%r' % pg.evaluate('()=>LPCUS')
            # localStorage 里被塞多了也只认前 3 个（上个版本 / 手改的）
            assert pg.evaluate(
                "()=>{const k='lvbenchcus:froec', old=localStorage.getItem(k);"
                " localStorage.setItem(k, JSON.stringify(['sh000001','sh000016',"
                "'sh000300','sh000905','zzz']));"
                " lpcLoad('froec'); const r = LPCUS.slice();"
                " localStorage.setItem(k, old); lpcLoad('froec'); return r;}") \
                == ['sh000001', 'sh000016', 'sh000300'], \
                '读回来时没截断 / 没挡掉非法值 —— localStorage 里的东西要校验再用'
            # 🔴🔴 点别的基准，chip **一个都不许消失**（用户报的就是这条）
            reqs2 = []
            pg.on('request', lambda r: reqs2.append(r.url)
                  if '/api/live/equity' in r.url else None)
            pg.evaluate("()=>[...document.querySelectorAll('#lp_bm a.lpb')]"
                        ".find(a=>a.dataset.b==='sh000001').click()")
            pg.wait_for_timeout(1500)
            assert _cus() == ['sh510880', 'sh000016', 'sh000300'], \
                ('点了上证之后自定义 chip 没了：%r —— 清单与"当前选中哪个"'
                 '是两件事，切去看别的不该把搜出来的票弄丢' % (_cus(),))
            # 在几个自定义之间切换：**零请求**（进页面时已一次全取）
            pg.evaluate("()=>[...document.querySelectorAll('.chipw a.lpb')]"
                        ".find(a=>a.dataset.b==='sh000300').click()")
            pg.wait_for_timeout(1500)
            assert pg.evaluate('()=>LPB') == ['sh000300'], '切不过去'
            assert pg.evaluate(
                "()=>document.querySelectorAll('#lp_chart svg path[d]').length") == 2, \
                '切到另一个自定义基准之后线没画出来'
            assert not reqs2, \
                ('在几个自定义之间切换还去重放了 %d 次权益曲线 —— 清单里那几个'
                 '进页面时就该一次全取（同个股页"一次全取"那条）' % len(reqs2))
            # × 删掉一个之后又能加
            pg.evaluate("()=>document.querySelector"
                        "('.chipx[data-x=sh000016]').click()")
            pg.wait_for_timeout(1200)
            assert _cus() == ['sh510880', 'sh000300'], \
                '点 × 没从清单里去掉：%r' % (_cus(),)
            pg.click('#lp_bmore')
            pg.wait_for_timeout(500)
            assert pg.locator('#lp_bq').count() == 1, \
                '删掉一个之后仍然加不了新的 —— 上限没跟着清单走'
            pg.keyboard.press('Escape')
            # 刷新后清单与选中都还在，且**按账户**存
            pg.reload(wait_until='networkidle')
            pg.wait_for_selector('#lp_bmore', timeout=25000)
            pg.wait_for_timeout(2500)
            assert _cus() == ['sh510880', 'sh000300'], \
                '刷新后自定义清单没了：%r' % (_cus(),)
            assert pg.evaluate("()=>Object.keys(localStorage)"
                               ".filter(k=>k.indexOf('lvbenchcus')===0)") \
                == ['lvbenchcus:froec'], \
                '自定义清单没按账户存 —— 两个账户会互相覆盖（同 lvbench 那条）'
            notes.append('自定义清单最多 %d 个 · 点别的基准不消失 · 切换零请求'
                         ' · × 删得掉且删完能再加' % 3)
            # 收拾回只剩一个，后面几条按原样跑
            pg.evaluate("()=>document.querySelector"
                        "('.chipx[data-x=sh000300]').click()")
            pg.wait_for_timeout(1000)
            pg.evaluate("()=>[...document.querySelectorAll('.chipw a.lpb')]"
                        ".find(a=>a.dataset.b==='sh510880').click()")
            pg.wait_for_timeout(1500)

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




@case('实盘 / 模拟盘分两个账户区：在哪个区建就是哪种（playwright）', tag='web')
def t_live_zones():
    """用户 2026-09-17："希望模拟盘有一个单独的入口……或者说账户区域和实盘的
    分开也可以，现在左边有一块实盘账号区了，再加一个模拟盘账号区，
    在里面创建的就是模拟盘。"

    ★ **没做成顶栏第 8 个入口**：模拟盘与实盘是**同一个页面、同一套 hash 路由**
      （`#/live/<id>`）—— 多一个顶栏入口的话，打开模拟盘账户时那两个该亮哪个？
      而顶栏高亮本来就是回答"我在哪"的。顶栏是按"今天要做什么"分组的，
      而模拟盘不是一件独立的事，它是**另一批账户**。

    🔴 **`mode` 从"勾选框"变成"在哪个区里点的新建"** —— `mode` 建好之后
      **不能改**（同一本账混着真实成交与引擎成交就说不清了），所以那个
      勾选框是最容易填错、代价又最大的一处。少一个能填错的概念。

    🔴 **判据要落在【账本里存的 mode】上**，不是"按钮上写了什么字" ——
      后者在 POST 漏传 mode 时照样绿（那时两个区建出来的都是实盘，
      **而它不报错**，只是模拟盘区里多了个不会自己推进的账户）。
    """
    import shutil
    import tempfile
    import threading
    from http.server import ThreadingHTTPServer

    from playwright.sync_api import sync_playwright

    from assay import live as lv
    from assay import server as sv

    def _wait_acct(name, secs=20):
        """轮询**账本**直到这个账户出现 —— 不看 DOM。

        🔴 页面建完立刻 `showLive()` 整页重渲染，成功提示当场就没了；
          而"模拟盘区里出现了账户"这个条件在「漏传 mode」「不分组」
          「服务端 paper 恒 False」三种变异下**都不成立** —— 等它就是
          超时 20 秒，**报错指不到真正的原因**。
          等账本则三条各自落到自己那句断言上。
        """
        import time as _t
        t0 = _t.time()
        while _t.time() - t0 < secs:
            hit = [x for x in lv.load_accounts() if x['name'] == name]
            if hit:
                return hit[0]
            _t.sleep(0.2)
        raise AssertionError('建了 %s 但账本里一直没出现（%d 秒）' % (name, secs))

    real = lv.LIVE
    tmp = tempfile.mkdtemp(prefix='selftest_zone_')
    shutil.copytree(real, os.path.join(tmp, 'live'), dirs_exist_ok=True)
    lv.LIVE = os.path.join(tmp, 'live')          # 🔴 不许写真账本
    # 🔴 **「模拟盘区为空」这个前提要【构造】，不能靠"真账本里恰好没有"。**
    #   2026-09-18 用户在页面上真建了第一个模拟盘，这条当场变红 ——
    #   而产品是好的（同「断言要在能触发的构造上跑」「判据不许依赖真实
    #   数据碰巧如此」那两条）。所以拷完之后把 paper 账户全摘掉。
    _ap = os.path.join(lv.LIVE, 'accounts.json')
    _keep = [x for x in json.load(open(_ap)) if not lv.is_paper(x)]
    assert _keep, '构造不对：清掉模拟盘之后一个实盘账户都不剩'
    json.dump(_keep, open(_ap, 'w'), ensure_ascii=False)
    prev_allow = sv.ALLOW_LIVE
    sv.ALLOW_LIVE = True
    sv._scan()
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), sv.Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    notes = []
    try:
        with sync_playwright() as p:
            try:
                br = p.chromium.launch()
            except Exception as e:                          # noqa: BLE001
                return '跳过（浏览器不可用: %s）' % type(e).__name__
            pg = br.new_page(viewport={'width': 1500, 'height': 950})
            errs = []
            pg.on('pageerror', lambda e: errs.append(str(e)))
            pg.goto('http://127.0.0.1:%d/#/live' % port, wait_until='networkidle')
            pg.wait_for_selector('.dzone[data-zone=paper]', timeout=30000)

            # ---- ① 两个区都在，且**空的那个也显示**（藏起来 = 没有这个功能）----
            zs = pg.evaluate("[...document.querySelectorAll('.dzone')].map(e=>e.dataset.zone)")
            assert zs == ['live', 'paper'], '账户区不是【实盘、模拟盘】两个：%s' % zs
            assert pg.eval_on_selector_all('.dzone[data-zone=paper] .ditem', 'a=>a.length') == 0, \
                '构造不对：临时账本里的模拟盘没清干净'
            assert pg.query_selector('.dzone[data-zone=paper] .dzempty') is not None, \
                '空的模拟盘区没显示说明 —— 从没建过的人根本不知道有这功能'
            # 建账户表单里**不许**再有"这是模拟盘吗"这个选项
            assert pg.query_selector('#npaper') is None, \
                '模拟盘还是靠勾选框选的 —— 那是最容易填错、且改不回来的一处'

            # ---- ② 在模拟盘区建 -> 账本里必须是 paper ----
            _lv_new_account(pg, '判据模拟盘', 100000, zone='paper')
            _a1 = _wait_acct('判据模拟盘')
            assert lv.is_paper(_a1), \
                '模拟盘区建出来的不是 paper：%r' % _a1.get('mode')

            # ---- ③ 反向自证：实盘区建出来的必须【不是】paper ----
            #   只测一个方向的话，"两个区都建 paper" 照样绿。
            _lv_new_account(pg, '判据实盘', 100000, zone='live')
            _a2 = _wait_acct('判据实盘')
            assert not lv.is_paper(_a2), \
                '实盘区建出来的成了 paper：%r' % _a2.get('mode')

            # ---- ④ 建完要落进对应的区（分组读的是服务端给的 paper 布尔）----
            pg.goto('http://127.0.0.1:%d/#/live' % port, wait_until='networkidle')
            pg.reload(wait_until='networkidle')
            pg.wait_for_selector('.dzone[data-zone=live] .ditem', timeout=20000)
            names = pg.evaluate("""() => {
              const pick = z => [...document.querySelectorAll(
                '.dzone[data-zone=' + z + '] .ditem')].map(e => e.textContent.trim());
              return {live: pick('live'), paper: pick('paper')};
            }""")
            assert any('判据模拟盘' in x for x in names['paper']), \
                '模拟盘没落进模拟盘区：%s' % names
            assert any('判据实盘' in x for x in names['live']), \
                '实盘没落进实盘区：%s' % names
            assert not any('判据模拟盘' in x for x in names['live']), \
                '模拟盘同时出现在实盘区：%s' % names

            # ---- ⑤ 展开态与收起态的【顺序必须一致】 ----
            #   🔴 rail 原来直接用账本原始顺序，而展开态按 real/paper 分组 ——
            #     "先建模拟盘、后建实盘"的账本上两态就**反着排**，收起再展开
            #     同一个账户跳到另一个位置，**而它不报错**。
            #   🔴 **这条必须构造**：真账本里模拟盘本来就排在最后，
            #     不把它挪到最前面的话两种实现给出同样的顺序，判据空转。
            import json as _json
            _ap = os.path.join(lv.LIVE, 'accounts.json')
            _acc = _json.load(open(_ap))
            _acc = ([x for x in _acc if lv.is_paper(x)]
                    + [x for x in _acc if not lv.is_paper(x)])
            assert lv.is_paper(_acc[0]), '构造不对：账本第一个应是模拟盘'
            _json.dump(_acc, open(_ap, 'w'), ensure_ascii=False)
            pg.reload(wait_until='networkidle')
            pg.wait_for_selector('.dzone[data-zone=paper] .ditem', timeout=20000)
            _exp = pg.evaluate("[...document.querySelectorAll('.dzone .ditem')]"
                               ".map(e => e.getAttribute('href'))")
            pg.evaluate("localStorage.setItem('lvfold','1')")
            pg.reload(wait_until='networkidle')
            pg.wait_for_selector('.drail .dchip', timeout=20000)
            # 轨顶部那个「›」展开按钮的 href 是 '#'，不是账户 —— 过滤掉
            _rail = pg.evaluate('''() => [...document.querySelectorAll('.drail .dchip')]
                .map(e => e.getAttribute('href'))
                .filter(h => h && h.startsWith('#/live/'))''')
            assert _exp == _rail, \
                '展开态与收起态账户顺序不一致：\n  展开 %s\n  收起 %s' % (_exp, _rail)
            assert lv.is_paper(lv.get_account(_exp[-1].rsplit('/', 1)[-1])), \
                '分组后模拟盘应排在最后：%s' % _exp
            # 收起态两组之间要有分隔（光靠紫边得先注意到颜色差别）。
            # 🔴 判据取**可量的视觉事实**（真有高度 + 背景不透明），不是
            #   "那个元素在不在" —— 后者在 CSS 规则被删掉时照样命中
            #   （元素还在、只是看不见）。变异实测漏过一次。
            _sep = pg.evaluate('''() => {
              const es = [...document.querySelectorAll('.drail .dsep')];
              if (es.length !== 1) return {n: es.length};
              const cs = getComputedStyle(es[0]);
              const r = es[0].getBoundingClientRect();
              return {n: 1, h: r.height, w: r.width, bg: cs.backgroundColor};
            }''')
            assert _sep['n'] == 1, '收起态两组之间应有且只有 1 条分隔：%s' % _sep
            # 🔴 **宽度也要量**：`.drail` 是 flex 列，块级元素没内容就宽 0 ——
            #   高 1px、宽 0 与"根本没画"在屏幕上无从分辨，而只量高度照样绿
            #   （实测踩过，是截图时 wait_for_selector 报 hidden 才发现的）。
            assert _sep['h'] >= 1 and _sep['w'] >= 8 \
                and 'rgba(0, 0, 0, 0)' not in _sep['bg'], \
                '分隔线画了但看不见（%s×%s / 背景 %s）' % (
                    _sep.get('w'), _sep.get('h'), _sep.get('bg'))

            # ---- ⑥ 收起态也要看得出哪个是模拟盘（轨只有 44px，靠紫色边）----
            pg.evaluate("localStorage.setItem('lvfold','1')")
            pg.reload(wait_until='networkidle')
            pg.wait_for_selector('.drail .dchip', timeout=20000)
            rail = pg.evaluate("""() => {
              const cs = [...document.querySelectorAll('.drail .dchip[href]')];
              const pa = cs.filter(c => c.classList.contains('paper'));
              return {n: cs.length, paper: pa.length,
                      border: pa.length ? getComputedStyle(pa[0]).borderTopColor : null,
                      other: cs.filter(c => !c.classList.contains('paper'))
                               .map(c => getComputedStyle(c).borderTopColor)[0]};
            }""")
            pg.evaluate("localStorage.setItem('lvfold','0')")
            assert rail['paper'] == 1, '收起态没标出模拟盘：%s' % rail
            assert rail['border'] != rail['other'], \
                '收起态模拟盘与实盘边框同色 —— 收起来就分不出了：%s' % rail

            assert not errs, '页面抛了异常：%s' % errs[:2]
            notes.append('两区各自建出 paper/live（判据落在账本的 mode 上）；'
                         '空区有说明；没有勾选框；收起态紫边可辨')
            br.close()
    finally:
        httpd.shutdown()
        lv.LIVE = real
        sv.ALLOW_LIVE = prev_allow
        shutil.rmtree(tmp, ignore_errors=True)
    return '；'.join(notes)


@case('账户说明：填得进、三处看得见、空着不占位（playwright）', tag='web')
def t_account_note():
    """用户 2026-09-18："实盘、模拟盘可以对账户增加一些说明信息。"

    🔴 **没有加新字段** —— `broker_note` 早就在模型、`upsert_account` 与
      POST 接口里了（同「先查有没有，再决定写不写」）。真正的问题是：
      ① 标签叫**「券商备注」**，而**模拟盘根本没有券商** —— 对它是错的；
      ② 只在 ⚙ 设置浮层第 5 行能填，**主视图/侧栏一个字都不显示**；
      ③ 于是四个账户的说明**全是空的** —— 写了也看不见，所以没人写。

    所以这一轮改的是「看得见」和「填得到」，不是加字段：
      设置浮层 单行 input -> 2 行 textarea，标签改「说明」、例子按账户类型给
      建账户时 就能填（建完再去设置里找的话多半不会填 —— 空了这么久就是证据）
      主视图   独占一行（不挤进 `.lvhead`，那排已经有 6 样东西）
      侧栏     名称**下方的小字**（同盘面榜单把「行业」放名称下方那条）

    🔴 **空着时整块不渲染** —— 留一句"（未填写）"就是常驻噪声
      （同「常驻一条『一切正常』的横幅等于教人忽略这个位置」）。
    """
    import shutil
    import tempfile
    import threading
    from http.server import ThreadingHTTPServer

    from playwright.sync_api import sync_playwright

    from assay import live as lv
    from assay import server as sv

    def _goto_acct(pg, base, aid):
        """打开某个账户页，并**等它真的渲染出来**。

        🔴 `pg.goto('#/live/<别的账户>')` 换的只是 hash，**页面不重新加载**，
          而 `#lvset` / `.lvhead` 这些选择器**上一个账户页上也有** ——
          于是 `wait_for_selector` 立刻满足，后面的断言跑在**旧页面**上。
          实测：这条用例"单跑绿、全量 web 偶发红"，根因就是建完账户之后
          页面停在**新账户**上，goto 回 tgt 时渲染还没跟上，
          `#lvset` 点开的是新账户的设置浮层（它的说明当然不等于 NOTE）。
          机器闲时渲染快、恰好不翻车 —— **偶发红比常红更难查**。
        ★ 判据要等**标题真的变成这个账户**，不是等一个两个页面都有的选择器。
        """
        # 🔴 选择器要限定 `#main` —— **设置浮层（`#stwrap`）里也有 `.lvhead h2`**
        #   （标题是"账户设置"），不限定的话等到的是浮层的标题，
        #   而那与"页面切到哪个账户"毫无关系（判据比要证的事宽）。
        nm = next(x['name'] for x in lv.load_accounts() if x['id'] == aid)
        pg.goto(base + '#/live/' + aid, wait_until='networkidle')
        try:
            pg.wait_for_function(
                'n => { const h = document.querySelector("#main .lvhead h2");'
                '       return h && h.textContent.trim() === n; }',
                arg=nm, timeout=20000)
        except Exception:
            # 🔴 超时本身什么都说明不了 —— 把**现场**打出来。
            #   这条用例偶发红过好几轮，每一轮都是靠猜在改判据
            #   （同「该在第一次就把失败现场打出来」那条）。
            st = pg.evaluate(
                '() => ({hash: location.hash,'
                ' h2: (document.querySelector("#main .lvhead h2")||{}).textContent,'
                ' all: [...document.querySelectorAll(".lvhead h2")]'
                '        .map(e => e.textContent),'
                ' sel: (typeof LVSEL === "undefined") ? "无" : LVSEL,'
                ' gen: (typeof LVGEN === "undefined") ? "无" : LVGEN,'
                ' body: (document.querySelector("#main")||{}).textContent'
                '        ? (document.querySelector("#main").textContent'
                '           .slice(0, 160)) : "#main 是空的"})')
            raise AssertionError(
                '切到账户 %s(%s) 之后标题没变成它。现场：hash=%r h2=%r '
                '全部 h2=%r LVSEL=%r LVGEN=%r 页面报错=%r #main 前 160 字=%r'
                % (nm, aid, st['hash'], st['h2'], st['all'], st['sel'],
                   st['gen'], errs[-3:], st['body']))

    real = lv.LIVE
    tmp = tempfile.mkdtemp(prefix='selftest_note_')
    shutil.copytree(real, os.path.join(tmp, 'live'), dirs_exist_ok=True)
    lv.LIVE = os.path.join(tmp, 'live')          # 🔴 不许写真账本
    prev_allow = sv.ALLOW_LIVE
    sv.ALLOW_LIVE = True
    sv._scan()
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), sv.Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    notes = []
    try:
        accs = [a for a in lv.load_accounts() if not a.get('archived')]
        assert accs, '构造不对：临时账本里没有账户'
        tgt, other = accs[0]['id'], (accs[1]['id'] if len(accs) > 1 else None)
        NOTE = '银河 · 主账户 · 小市值\n本金 40 万'
        lv.upsert_account(tgt, broker_note=NOTE)
        if other:
            lv.upsert_account(other, broker_note='')   # 空的那个用来验"不占位"

        with sync_playwright() as p:
            try:
                br = p.chromium.launch()
            except Exception as e:                      # noqa: BLE001
                return '跳过（浏览器不可用: %s）' % type(e).__name__
            pg = br.new_page(viewport={'width': 1500, 'height': 950})
            errs = []
            # ★ 收**堆栈**不只收消息 —— "Cannot set properties of null" 这种
            #   消息本身指不到是哪一处（同「报错必须指向真正的原因」）。
            pg.on('pageerror', lambda e: errs.append(e.stack or str(e)))
            base = 'http://127.0.0.1:%d/' % port
            _goto_acct(pg, base, tgt)
            pg.wait_for_selector('.lvnote', timeout=30000)

            # ---- ① 主视图：原文都在（含换行），且**不在** .lvhead 那一排 ----
            got = pg.inner_text('.lvnote')
            assert NOTE.split('\n')[0] in got and NOTE.split('\n')[1] in got, \
                '主视图说明不全（换行被吃掉？）：%r' % got
            assert pg.eval_on_selector(
                '.lvnote', 'e => !e.closest(".lvhead")'), \
                '说明挤进了 .lvhead —— 那一排已经有名称/标签/数据日/策略/三个按钮'
            # 换行要真的换行（`white-space` 没设的话两句会挤成一行）
            _ws = pg.eval_on_selector('.lvnote', 'e => getComputedStyle(e).whiteSpace')
            assert 'pre' in _ws, '说明没保留换行（white-space=%s）' % _ws

            # ---- ② 侧栏：名称下方的小字，且**全文进 title**（截断不许藏没了）----
            side = pg.evaluate("""() => [...document.querySelectorAll('.ditem')]
                .map(e => ({href: e.getAttribute('href'),
                            note: (e.querySelector('.dnote') || {}).textContent || '',
                            title: e.getAttribute('title') || ''}))""")
            mine = [x for x in side if x['href'].endswith('/' + tgt)]
            assert mine and NOTE.split('\n')[0] in mine[0]['note'], \
                '侧栏没显示说明：%s' % mine
            assert NOTE.split('\n')[0] in mine[0]['title'], \
                '说明被截断了却没进 title —— 挪走可以，藏没了不行：%s' % mine
            # ---- ③ 空说明的账户**一个空位都不占** ----
            if other:
                oth = [x for x in side if x['href'].endswith('/' + other)]
                assert oth and not oth[0]['note'].strip(), \
                    '说明为空的账户还渲染了小字（常驻噪声）：%s' % oth
                # 🔴 `goto` 换 hash **不重新加载页面**，而 `.lvhead` 立刻就
                #   满足（上一个账户的还在）—— 断言会跑在**旧页面**上，
                #   把"产品对的"报成失败（实测：切过去 1.5 秒后标题才变）。
                #   要等**标题真的换成这个账户**（同「看着在验持久化、
                #   其实在读内存」那条的变体）。
                _goto_acct(pg, base, other)
                assert pg.query_selector('.lvnote') is None, \
                    '说明为空时主视图还留着那一行'

            # ---- ④ 建账户时就能填（建完再找的话没人会填）----
            pg.goto(base + '#/live', wait_until='networkidle')
            pg.wait_for_selector('.znew[data-zone=paper]', timeout=20000)
            _lv_open_newform(pg, 'paper')
            pg.fill('#nf_paper .nn', '说明判据')
            pg.fill('#nf_paper .nc', '100000')
            pg.fill('#nf_paper .nd', '验证不手工干预的表现')
            pg.click('#nf_paper .nb')
            import time as _t
            t0 = _t.time()
            hit = None
            while _t.time() - t0 < 20:
                hit = next((x for x in lv.load_accounts()
                            if x['name'] == '说明判据'), None)
                if hit:
                    break
                _t.sleep(0.2)
            assert hit, '建账户失败'
            # 🔴 判据落在**账本里存的值**上 —— 只查"表单里有那个框"的话，
            #   POST 漏传 broker_note 时照样绿（而它不报错，只是说明丢了）。
            assert hit.get('broker_note') == '验证不手工干预的表现', \
                '建账户时填的说明没存进账本：%r' % hit.get('broker_note')

            # ---- ⑤ 设置浮层：标签不许再叫「券商备注」（模拟盘没有券商）----
            # 🔴 上一步刚建完账户，页面停在**新账户**上 —— 必须等标题切过来，
            #   否则 `#lvset` 点开的是那个新账户的设置（偶发红的根因）。
            _goto_acct(pg, base, tgt)
            pg.click('#lvset')
            pg.wait_for_selector('#enote', timeout=20000)
            assert pg.eval_on_selector('#enote', 'e => e.tagName') == 'TEXTAREA', \
                '说明还是单行 input —— 一句话说不完"这个账户在干什么"'
            _got = pg.eval_on_selector('#enote', 'e => e.value')
            if _got != NOTE:
                # 🔴 失败时把**现场**打出来，不靠猜（同「该在第一次就把失败
                #   现场打出来」那条）—— 这条曾"单跑绿、全量红"。
                _ledger = next((x for x in lv.load_accounts()
                                if x['id'] == tgt), None)
                _hdr = pg.inner_text('#main .lvhead h2')
                raise AssertionError(
                    '设置浮层里没回填当前说明\n'
                    '  期望   %r\n  框里   %r\n'
                    '  账本   %r\n  当前页标题 %r（tgt=%s）\n'
                    '  lv.LIVE=%s' % (
                        NOTE, _got, (_ledger or {}).get('broker_note'),
                        _hdr, tgt, lv.LIVE))
            _lab = pg.eval_on_selector(
                '#enote', 'e => e.closest(".frow").querySelector("label").textContent')
            assert '券商' not in _lab, \
                '标签还叫「%s」—— 模拟盘根本没有券商，对它是错的' % _lab

            assert not errs, '页面抛了异常：%s' % errs[:2]
            notes.append('主视图独占一行且保留换行；侧栏小字 + 全文进 title；'
                         '空说明不占位；建账户时填的值落进账本；标签不再是「券商备注」')
            br.close()
    finally:
        httpd.shutdown()
        lv.LIVE = real
        sv.ALLOW_LIVE = prev_allow
        shutil.rmtree(tmp, ignore_errors=True)
    return '；'.join(notes)


@case('切账户的两处并发：旧那一发不许盖掉新页面（playwright）', tag='web')
def t_loadlive_race():
    """🔴 `loadLive` 的 `b = $('#lvbody')` 取在 **await 之前**，而取数据那
    几百毫秒里 hash 可能已经切到别的账户 —— 回来时 `#lvbody` 已被换掉，
    往旧的 `b` 写就是写进一个**脱离文档的节点**，紧接着
    `$('#lvset').onclick` 是 **null**，抛 `Cannot set properties of null`。

    **只在控制台里报，页面看着正常**（新页面自己会渲染），所以一直没人发现
    —— 是 2026-09-18「账户说明」那条用例**偶发红**才暴露的，连跑三次才
    复现一次（同 `renderChart` / `lprBar` 往 null 写那次）。

    🔴 **这个竞态必须构造**：正常点击慢得多，真实使用下几乎撞不上，
      靠"跑几遍碰运气"等于没测（同「断言要在能触发的构造上跑」）。
      做法是 `pg.route` 把 `/api/live/account` **压慢**，在它还没回来时
      切到另一个账户。
    ★ 判据是 **pageerror 一条都没有** + 切过去的那一页**照常可用**
      （`#lvset` 点得开）—— 只查"没报错"的话，把整个 `loadLive` 删掉也全绿。

    🔴 **`showLive` 那道守卫这条用例【抓不到】** —— 构造出真并发之后，
      去掉它页面也没被盖回去（诊断过：旧那发回来时标题没变）。
      它是**冗余防御**，不是这条用例证明的东西。**如实记下来** ——
      把冗余说成"抓到了"，下次有人就会以为它被覆盖着
      （同「对数表里不许留只有数、没有判断的行」）。
    """
    import shutil
    import tempfile
    import threading
    from http.server import ThreadingHTTPServer

    from playwright.sync_api import sync_playwright

    from assay import live as lv
    from assay import server as sv

    real = lv.LIVE
    tmp = tempfile.mkdtemp(prefix='selftest_race_')
    shutil.copytree(real, os.path.join(tmp, 'live'), dirs_exist_ok=True)
    lv.LIVE = os.path.join(tmp, 'live')
    prev_allow = sv.ALLOW_LIVE
    sv.ALLOW_LIVE = True
    sv._scan()
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), sv.Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        accs = [a for a in lv.load_accounts() if not a.get('archived')]
        assert len(accs) >= 2, '构造不对：要两个账户才能切'
        a1, a2 = accs[0]['id'], accs[1]['id']
        n2 = accs[1]['name']

        with sync_playwright() as p:
            try:
                br = p.chromium.launch()
            except Exception as e:                      # noqa: BLE001
                return '跳过（浏览器不可用: %s）' % type(e).__name__
            pg = br.new_page(viewport={'width': 1500, 'height': 950})
            errs = []
            pg.on('pageerror', lambda e: errs.append(e.stack or str(e)))
            base = 'http://127.0.0.1:%d/' % port

            # 🔴 把账户接口压慢 —— 构造"await 还没回来就切走"
            def _slow(route):
                time.sleep(1.2)
                route.continue_()
            pg.route('**/api/live/account?*', _slow)

            pg.goto(base + '#/live/' + a1, wait_until='domcontentloaded')
            pg.wait_for_timeout(250)          # 第一发还在飞
            pg.evaluate("location.hash = '#/live/' + %r" % a2)
            pg.wait_for_function(
                'n => { const h = document.querySelector("#main .lvhead h2");'
                '       return h && h.textContent.trim() === n; }',
                arg=n2, timeout=30000)
            pg.wait_for_timeout(1500)         # 让被切走那一发也回来

            assert not errs, (
                'loadLive 在 await 期间被切走时往 null 写了 —— '
                '**只在控制台里报，页面看着正常**：\n%s' % errs[0][:400])
            # 反向自证：切过去那一页**照常可用**（不是"什么都没渲染所以不报错"）
            assert pg.eval_on_selector('#main .lvhead h2', 'e => e.textContent.trim()') == n2, \
                '切过去之后标题不对'
            pg.click('#lvset')
            pg.wait_for_selector('#enote', timeout=20000)
            assert not errs, '打开设置浮层时抛了：%s' % errs[:1]

            # ---- 🔴 更严重的一半：`showLive` 的并发 ----
            #   它 await 之后无条件写 `#main`，两发并发时**谁后回来谁赢** ——
            #   于是「hash 指向账户 A、页面显示账户 B」，`LVSEL` 也被盖掉，
            #   **而它不报错**。真实场景：在列表里连点两个账户。
            #   构造：让 `/api/live/accounts` **第一发慢、第二发快**，
            #   于是旧那发后回来 —— 没有代际判据的话它会把新页面盖回去。
            # ---- 🔴 更严重的一半：`showLive` 的并发 ----
            #   它 await 之后无条件写 `#main`，两发并发时**谁后回来谁赢** ——
            #   「hash 指向账户 A、页面显示账户 B」，`LVSEL` 也被盖掉，
            #   **而它不报错**。真实场景：在列表里连点两个账户。
            #
            # 🔴🔴 **构造不能用 `pg.route` + `time.sleep`**：sync API 的
            #   route handler 跑在 driver 线程上，sleep **把后续请求也堵住**
            #   —— 两发被串行化，竞态根本没发生（第一版这么写，
            #   "去掉 showLive 守卫"那条变异**没抓到**，看着像守卫没用）。
            #   改成在**浏览器里**把第一发的 promise 延后 resolve：
            #   真并发，且不碰 driver。
            pg.unroute('**/api/live/account?*')
            _SLOW1 = """() => {
              const _j = window.j; let n = 0;
              window.j = async (u, ...rest) => {
                const p = _j(u, ...rest);
                if (String(u).includes('/api/live/accounts') && ++n === 1) {
                  const r = await p;
                  await new Promise(z => setTimeout(z, 1500));
                  return r;
                }
                return p;
              };
            }"""
            pg.evaluate(_SLOW1)
            _n2 = next(x['name'] for x in lv.load_accounts() if x['id'] == a1)
            pg.evaluate("location.hash = '#/live/' + %r" % a2)   # 第一发（慢）
            pg.wait_for_timeout(150)
            pg.evaluate("location.hash = '#/live/' + %r" % a1)   # 第二发（快）
            # ★ 先等它**真的渲染出来**，再多等 2 秒让被作废的那一发回来 ——
            #   它若没被挡住就会把页面盖回上一个账户。
            pg.wait_for_function(
                'n => { const h = document.querySelector("#main .lvhead h2");'
                '       return h && h.textContent.trim() === n; }',
                arg=_n2, timeout=30000)
            pg.wait_for_timeout(2000)
            _final = pg.eval_on_selector('#main .lvhead h2', 'e => e.textContent.trim()')
            assert _final == _n2, (
                'showLive 的旧那一发把新页面盖回去了 —— '
                'hash 指着 %r，页面却显示 %r（而它不报错）' % (_n2, _final))
            assert not errs, '并发切账户时抛了：%s' % errs[:1]

            # ---- 🔴🔴 第三种：一发【走路由】、一发【不走】 ----
            #   上面两段两发都改了 hash，所以"最新那发"与"hash 要的那发"
            #   恰好是同一个 —— **纯代际判据就够了，测不出区别**。
            #   而 `showLive` 还被非路由调用（建完账户 / 收起侧栏 /
            #   切「显示已归档」/ 关掉浮层），那几发 hash 没变：
            #   于是一发后到的重渲染能把**路由要的那个账户**顶掉，
            #   路由那发回来时反而按代际"作废了自己" ——
            #   结果就是「hash 指向 A、页面显示 B」，**而它不报错**。
            #   ★ 这是 2026-09-21 从**失败现场**倒推出来的：那条用例 60 轮
            #     挂 12 轮，现场一律 `hash='#/live/hongli'` 而 `LVSEL='a6'`、
            #     零 pageerror（光靠超时信息永远查不到这一步）。
            pg.evaluate("location.hash = '#/live'")
            pg.wait_for_timeout(400)
            pg.evaluate(_SLOW1)
            pg.evaluate("location.hash = '#/live/' + %r" % a1)   # 路由那发（慢）
            pg.wait_for_timeout(150)
            pg.evaluate("showLive(%r)" % a2)                     # 非路由那发（快）
            pg.wait_for_timeout(3000)
            _f2 = pg.eval_on_selector('#main .lvhead h2',
                                      'e => e.textContent.trim()')
            _sel = pg.evaluate('() => LVSEL')
            assert _f2 == _n2 and _sel == a1, (
                'hash 指着 %r(%s)，页面却显示 %r(LVSEL=%r) —— '
                '非路由那一发把路由要的账户顶掉了' % (_n2, a1, _f2, _sel))
            assert not errs, '第三种构造里抛了：%s' % errs[:1]

            # ---- 🔴 第四种：在【别的账户页上】建一个新账户 ----
            #   真实场景就是"我在看 hongli，顺手建一个"。
            #   这一段防的是**修上面那条竞态时最容易引入的反作用**：
            #   建完之后若还走 `showLive(新id)`，`lvStale` 会因为
            #   "hash 点名的是 hongli" 把它判成过期 -> **账户建出来了却
            #   不显示，屏幕上只有"已建"两个字**，而它不报错。
            #   所以建完必须**走 hash**（且只在人没自己走开时跳）。
            # 🔴 **从干净页面开始**：上一段给 `window.j` 打了补丁、页面还在
            #   不停重渲染，漏进这一段的话「+ 新建」那个按钮解析得到却
            #   **点不动**（playwright 的可操作性检查过不去，报的是
            #   `Timeout 30000ms exceeded`，指不到真正的原因）。
            pg.goto(base + '#/live/' + a1, wait_until='networkidle')
            pg.reload(wait_until='networkidle')
            pg.wait_for_function(
                'n => { const h = document.querySelector("#main .lvhead h2");'
                '       return h && h.textContent.trim() === n; }',
                arg=_n2, timeout=30000)
            _nm4 = '并发判据-新建'
            _lv_new_account(pg, _nm4, '100000', zone='live')
            try:
                pg.wait_for_function(
                    'n => { const h = document.querySelector("#main .lvhead h2");'
                    '       return h && h.textContent.trim() === n; }',
                    arg=_nm4, timeout=30000)
            except Exception:
                # 🔴 裸超时指不到原因 —— 把现场说出来（变异实测：建完之后
                #   还走 `showLive(新id)` 的话，它会被 lvStale 判成过期而
                #   作废，屏幕上只有"已建"两个字）。
                _st = pg.evaluate(
                    '() => ({hash: location.hash, sel: LVSEL,'
                    ' h2: (document.querySelector("#main .lvhead h2")||{})'
                    '       .textContent})')
                raise AssertionError(
                    '在别的账户页上建了账户 %r，页面却没跳过去 —— 现场：%r。'
                    '账户建出来了却不显示（"已建"之外没有任何反馈），'
                    '多半是建完走了 showLive() 而不是改 hash' % (_nm4, _st))
            _h4 = pg.evaluate('() => location.hash')
            _id4 = next(x['id'] for x in lv.load_accounts() if x['name'] == _nm4)
            assert _h4 == '#/live/' + _id4, (
                '建完账户之后 hash 没跟过去（%r）—— 页面与 hash 又成了'
                '两个真值' % _h4)
            assert not errs, '第四种构造里抛了：%s' % errs[:1]
            br.close()
    finally:
        httpd.shutdown()
        lv.LIVE = real
        sv.ALLOW_LIVE = prev_allow
        shutil.rmtree(tmp, ignore_errors=True)
    return '构造出 await 期间切走的竞态：0 个 pageerror，且切过去那页照常可用'


@case('模拟盘：可设推演起点；绑了策略就不许手工录（playwright）', tag='web')
def t_paper_start_and_manual():
    """用户 2026-09-18："模拟账户功能需要有选择策略、设置参数、设置起始时间
    这些选项，然后自然推演到最近的日期。模拟账户只有在不设置策略的时候，
    才能手动操作。"

    三条里**选策略 + 设参数本来就有**（策略浮层实盘模拟盘共用），
    缺的是起始时间与那条手工约束：

    ① **推演起点 `paper_start`** —— 原来 `lv/bench.py` 写死
       `a['created'][:10]`（开户日），于是模拟盘只能"从今天起"。
       🔴 **只对模拟盘生效**：实盘那条「策略曲线」必须从开户日起，
         才能与实际曲线**对齐起点**（同「两条的基点都必须是本金」）。
       ★ 为什么不直接改 `created`：`prune_runs.py` 拿**最早的 created**
         当归档保护的分界日 —— 把它设成 2016 会把全部归档保护起来，
         等于不清理。而业绩页那条权益曲线的起点是**账本第一笔**，
         所以加新字段不会与它分家（查过 `perf.equity_curve` 的 `d0`）。

    ② **绑了策略就不许手工录**（`pos.add_fill` 一处判，页面能绕过）。
       理由不是洁癖：`paper.advance` 每次**从起点重放整段**再与账本逐笔
       对账，手工那几笔引擎不会跑出来 -> **下次推进必然 mismatch**，
       而它报的原因是"多半是数据被修正过" —— **指不到真正的原因**。
       ★ 反过来**没绑策略的模拟盘照常能录** —— 那是"手工模拟盘"。

    ③ 已经推演出成交之后**不许静默改起点**（同样会让整段对不上）。
    """
    import shutil
    import tempfile
    import threading
    from http.server import ThreadingHTTPServer

    from playwright.sync_api import sync_playwright

    from assay import live as lv
    from assay import server as sv
    from assay.lv import bench as _bench

    real = lv.LIVE
    tmp = tempfile.mkdtemp(prefix='selftest_pstart_')
    shutil.copytree(real, os.path.join(tmp, 'live'), dirs_exist_ok=True)
    lv.LIVE = os.path.join(tmp, 'live')
    prev_allow = sv.ALLOW_LIVE
    sv.ALLOW_LIVE = True
    sv._scan()
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), sv.Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    notes = []
    try:
        src = next((a for a in lv.load_accounts() if lv.is_paper(a)
                    and a.get('code_sha256')), None)
        if src is None:
            return '跳过（本机没有绑了策略的模拟盘可借版本快照）'
        pid = src['id']

        # ---- ① 起点只对模拟盘生效；实盘仍是开户日 ----
        lv.upsert_account('tps', name='起点判据', init_cash=200000,
                          mode='paper', paper_start='2025-06-02')
        shutil.copytree(os.path.join(lv.LIVE, pid, 'code'),
                        os.path.join(lv.LIVE, 'tps', 'code'), dirs_exist_ok=True)
        shutil.copy(os.path.join(lv.LIVE, pid, 'versions.jsonl'),
                    os.path.join(lv.LIVE, 'tps', 'versions.jsonl'))
        _ap = os.path.join(lv.LIVE, 'accounts.json')
        _d = json.load(open(_ap))
        for x in _d:
            if x['id'] == 'tps':
                x['code_sha256'] = src['code_sha256']
                x['strategy_path'] = src['strategy_path']
        json.dump(_d, open(_ap, 'w'), ensure_ascii=False)

        _eng, meta = _bench.build_engine('tps')
        assert meta.get('start') == '2025-06-02', \
            '推演起点没生效：引擎 start=%r（设的是 2025-06-02）' % meta.get('start')
        # 反向自证：开户日是今天，所以"没生效"与"生效"给出的是两个不同的值
        assert lv.get_account('tps')['created'][:10] != '2025-06-02', \
            '构造不对：开户日恰好等于设的起点，这条断言分不出生效没有'
        # 实盘那条必须还是开户日
        _lv = next((a for a in lv.load_accounts()
                    if not lv.is_paper(a) and a.get('code_sha256')), None)
        if _lv:
            _, m2 = _bench.build_engine(_lv['id'])
            assert m2.get('start') == _lv['created'][:10], \
                '实盘的策略曲线起点被带偏了：%r（应为开户日 %r）' % (
                    m2.get('start'), _lv['created'][:10])
        # 🔴 **两道守卫，分工别记反**（上一轮刚吃过把冗余说成"抓到了"的亏）：
        #   base 那道  **根本修复** —— `upsert_account` 直接拒绝实盘设这个字段
        #   bench 那道 **冗余防御** —— 即使账本里真被塞进去了也不采信
        # 实盘账户不许设这个字段（base 那道）
        try:
            if _lv:
                lv.upsert_account(_lv['id'], paper_start='2020-01-01')
                raise AssertionError('实盘账户竟然能设推演起点')
        except lv.LiveError:
            pass
        # bench 那道：**绕过 base 直接往账本塞**，它仍然不许采信 ——
        # 不绕过的话实盘根本没有这个字段，"实盘也用 paper_start"那个变异
        # 是**无效**的（两种实现给出同样的结果，判据空转）。
        if _lv:
            _d2 = json.load(open(_ap))
            for x in _d2:
                if x['id'] == _lv['id']:
                    x['paper_start'] = '2020-01-01'
            json.dump(_d2, open(_ap, 'w'), ensure_ascii=False)
            _, m3 = _bench.build_engine(_lv['id'])
            assert m3.get('start') == _lv['created'][:10], \
                ('账本里被塞了 paper_start，实盘的策略曲线就被带偏到 %r '
                 '（应仍为开户日 %r）—— 它必须与实际曲线对齐起点'
                 % (m3.get('start'), _lv['created'][:10]))
            for x in _d2:
                if x['id'] == _lv['id']:
                    x.pop('paper_start', None)
            json.dump(_d2, open(_ap, 'w'), ensure_ascii=False)
        notes.append('起点只对模拟盘生效（实盘仍是开户日）')

        # ---- ② 绑了策略 -> 服务端拒绝手工录；解绑 -> 放行 ----
        try:
            lv.add_fill('tps', '2026-09-17', '600000.XSHG', 'buy', 100,
                        price=9.10, fee=5)
            raise AssertionError('绑了策略的模拟盘竟然能手工录成交')
        except lv.LiveError as e:
            assert '解绑' in str(e) or '策略' in str(e), \
                '拒绝了但没说清怎么办：%s' % e
        # 引擎那条路（source='paper'）不许被误伤
        lv.add_fill('tps', '2026-09-17', '600000.XSHG', 'buy', 100,
                    price=9.10, fee=5, source='paper')
        # 解绑之后照常能录
        _d = json.load(open(_ap))
        for x in _d:
            if x['id'] == 'tps':
                x['code_sha256'] = None
        json.dump(_d, open(_ap, 'w'), ensure_ascii=False)
        lv.add_fill('tps', '2026-09-17', '600519.XSHG', 'buy', 100,
                    price=None, fee=5)
        notes.append('绑了策略拒绝手工录（引擎那条路不误伤）；解绑后放行')

        # ---- ③ 已经推演出成交之后不许静默改起点 ----
        _d = json.load(open(_ap))
        for x in _d:
            if x['id'] == 'tps':
                x['code_sha256'] = src['code_sha256']
        json.dump(_d, open(_ap, 'w'), ensure_ascii=False)
        try:
            lv.upsert_account('tps', paper_start='2024-01-01')
            raise AssertionError('已经推演过还能静默改起点 —— '
                                 '下次推进会整段对不上，而报的原因指不到这里')
        except lv.LiveError as e:
            assert '重建' in str(e), '拒绝了但没给下一步：%s' % e
        notes.append('已推演过就不许静默改起点（提示去重建）')

        # ---- ④ 页面：建的时候能设起点 + 点「记一笔」要说清原因 ----
        with sync_playwright() as p:
            try:
                br = p.chromium.launch()
            except Exception as e:                          # noqa: BLE001
                return '跳过（浏览器不可用: %s）' % type(e).__name__
            pg = br.new_page(viewport={'width': 1500, 'height': 950})
            errs = []
            pg.on('pageerror', lambda e: errs.append(e.stack or str(e)))
            base = 'http://127.0.0.1:%d/' % port

            pg.goto(base + '#/live', wait_until='networkidle')
            pg.wait_for_selector('.znew[data-zone=paper]', timeout=30000)
            _lv_open_newform(pg, 'paper')
            assert pg.query_selector('#nf_paper .ns') is not None, \
                '建模拟盘时没法设起点 —— 建完再改的话，推演过就得先重建'
            pg.fill('#nf_paper .nn', '起点UI判据')
            pg.fill('#nf_paper .nc', '200000')
            pg.fill('#nf_paper .ns', '2025-06-02')
            pg.click('#nf_paper .nb')
            t0 = time.time()
            hit = None
            while time.time() - t0 < 20:
                hit = next((x for x in lv.load_accounts()
                            if x['name'] == '起点UI判据'), None)
                if hit:
                    break
                time.sleep(0.2)
            assert hit, '建账户失败'
            # 🔴 判据落在**账本里存的值**上：只查"表单里有那个框"的话，
            #   POST 漏传 paper_start 时照样绿（而它不报错，起点悄悄变成今天）。
            assert hit.get('paper_start') == '2025-06-02', \
                '建的时候填的起点没存进账本：%r' % hit.get('paper_start')

            # 点「记一笔」：**不许 disabled**，点了要说清原因
            _nm = next(x['name'] for x in lv.load_accounts() if x['id'] == pid)
            pg.goto(base + '#/live/' + pid, wait_until='networkidle')
            pg.wait_for_function(
                'n => { const h = document.querySelector("#main .lvhead h2");'
                '       return h && h.textContent.trim() === n; }',
                arg=_nm, timeout=30000)
            assert pg.eval_on_selector('#lvrec', 'e => !e.disabled'), \
                '按钮设了 disabled —— 项目纪律：一律不设，' \
                'disabled 的元素连 title 都不触发'
            pg.click('#lvrec')
            pg.wait_for_selector('#stwrap .stbox', timeout=20000)
            _t = pg.inner_text('#stwrap .stbox')
            assert '绑了策略' in _t and '解绑' in _t, \
                '点了没说清为什么不能录、怎么办：%r' % _t[:120]
            assert pg.query_selector('#rcstrat') is not None, \
                '没给"去解绑策略"的入口 —— 说了不能做却不给下一步'
            # 🔴 文案里不许有裸 markdown 星号（HTML 渲染不了，会原样显示）
            assert '**' not in _t, \
                '浮层文案里有 markdown 星号，页面上会原样显示：%r' % _t[:150]
            assert not errs, '页面抛了异常：%s' % errs[:1]
            notes.append('页面：建时可设起点（值落进账本）；'
                         '记一笔不 disabled、点了说清原因且给解绑入口')
            br.close()
    finally:
        httpd.shutdown()
        lv.LIVE = real
        sv.ALLOW_LIVE = prev_allow
        shutil.rmtree(tmp, ignore_errors=True)
    return '；'.join(notes)


@case('推进完 0 笔成交必须说出为什么（playwright）', tag='web')
def t_paper_why_empty():
    """🔴🔴 2026-09-18 用户报「FROEA-TRADE模拟账户我选了起始时间，也推进了，
    但是没有任何数据出现」。

    查下来不是推进坏了：`paper_start` 存上了、`advanced_to=2026-09-17`、
    `ok=True` —— 而 **`init_cash=40`（四十元）**，一手股票要几千元。
    引擎**已经记了 30 条「资金不足一手」拒单**（`broker.rejects`，
    那行注释就写着"拒单必须可见，不静默"），而 `advance` **没把它带出来**：
    页面一片空白，**没有任何地方说原因**。链条在这里断了 ——
    broker 记了，传不到页面等于没记。

    ★ 判据取**引擎自己给的拒单原因**，不自己猜："本金太小"只是这一次的
      原因，候选池为空 / 起点之后没有调仓日都会表现成同一个"0 笔"，
      而它们要做的事完全不同。
    ★ 用**警告样式**不是 ⓘ：它要人去做事（改本金再重建），
      而「警告不许进 ⓘ」—— 藏起来等于没有。
    ★ **有成交时整块不渲染** —— 常驻一条"一切正常"等于教人忽略这个位置。
    """
    import shutil
    import tempfile
    import threading
    from http.server import ThreadingHTTPServer

    from playwright.sync_api import sync_playwright

    from assay import live as lv
    from assay import server as sv
    from assay.lv import paper as _paper

    real = lv.LIVE
    tmp = tempfile.mkdtemp(prefix='selftest_why_')
    shutil.copytree(real, os.path.join(tmp, 'live'), dirs_exist_ok=True)
    lv.LIVE = os.path.join(tmp, 'live')
    prev_allow = sv.ALLOW_LIVE
    sv.ALLOW_LIVE = True
    sv._scan()
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), sv.Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    notes = []
    try:
        src = next((a for a in lv.load_accounts() if lv.is_paper(a)
                    and a.get('code_sha256')), None)
        if src is None:
            return '跳过（本机没有绑了策略的模拟盘）'
        aid = src['id']
        _ap = os.path.join(lv.LIVE, 'accounts.json')

        def _set(cash, start):
            d = json.load(open(_ap))
            for x in d:
                if x['id'] == aid:
                    x['init_cash'] = cash
                    x['paper_start'] = start
            json.dump(d, open(_ap, 'w'), ensure_ascii=False)

        # ---- ① 本金太小 -> 0 笔，且原因来自引擎的拒单 ----
        #   🔴 这个构造是**用户的真实现状**（40 元 / 起点 2026-09-01）。
        _set(40.0, '2026-09-01')
        _paper.reset(aid)
        r = _paper.advance(aid)
        assert r.get('ok'), '推进本身该成功（它没坏）：%s' % r
        assert r.get('n_fills') == 0, \
            '构造不对：40 元竟然买到了 %s 笔' % r.get('n_fills')
        assert r.get('rejects'), \
            'advance 没把引擎的拒单带出来 —— broker 记了、传不到就等于没记'
        assert any('资金不足' in x['why'] for x in r['rejects']), \
            '拒单原因不对：%s' % r['rejects']
        assert r.get('why_empty') and '40' in r['why_empty'], \
            '没说清为什么 0 笔（或没提本金）：%r' % r.get('why_empty')
        assert '重建' in r['why_empty'], \
            '说了原因却没给下一步 —— 改本金之后必须重建才生效：%r' % r['why_empty']

        # ---- ② 有成交时不许留这块（常驻警告 = 教人忽略这个位置）----
        _set(400000.0, '2026-09-01')
        _paper.reset(aid)
        r2 = _paper.advance(aid)
        assert r2.get('n_fills', 0) > 0, \
            '构造不对：40 万也买不到票？%s' % r2
        assert not r2.get('why_empty'), \
            '有成交了还在说"一笔都没有"：%r' % r2.get('why_empty')
        notes.append('0 笔时给出引擎的拒单原因 + 下一步；有成交时不渲染')

        # ---- ③ 页面：警告可见、说了原因、给了下一步；填小数字当场提示 ----
        with sync_playwright() as p:
            try:
                br = p.chromium.launch()
            except Exception as e:                          # noqa: BLE001
                return '跳过（浏览器不可用: %s）' % type(e).__name__
            pg = br.new_page(viewport={'width': 1500, 'height': 950})
            errs = []
            pg.on('pageerror', lambda e: errs.append(e.stack or str(e)))
            base = 'http://127.0.0.1:%d/' % port
            _nm = next(x['name'] for x in lv.load_accounts() if x['id'] == aid)

            # 有成交的那一版：整块不该出现
            pg.goto(base + '#/live/' + aid, wait_until='networkidle')
            pg.wait_for_function(
                'n => { const h = document.querySelector("#main .lvhead h2");'
                '       return h && h.textContent.trim() === n; }',
                arg=_nm, timeout=30000)
            _w = pg.query_selector_all('#main .lvwarn')
            assert not any('一笔成交都没有' in e.inner_text() for e in _w), \
                '有成交时页面还挂着"一笔都没有"的警告'

            # 回到 0 笔那一版
            _set(40.0, '2026-09-01')
            _paper.reset(aid)
            _paper.advance(aid)
            pg.reload(wait_until='networkidle')
            # 🔴 **不要等那个选择器** —— 页面不渲染那块时是超时 30 秒，
            #   **报错指不到原因**（同「报错必须指向真正的原因」）。
            #   等"页面渲染完"，然后自己判有没有那块。
            pg.wait_for_function(
                'n => { const h = document.querySelector("#main .lvhead h2");'
                '       return h && h.textContent.trim() === n; }',
                arg=_nm, timeout=30000)
            pg.wait_for_timeout(400)
            _box = pg.query_selector('#main .lvwarn')
            assert _box is not None, (
                '推进出 0 笔，页面却一个字都不说 —— 那正是用户报的'
                '「也推进了，但是没有任何数据出现」')
            _t = pg.inner_text('#main .lvwarn')
            assert '一笔成交都没有' in _t, '页面没说"0 笔"：%r' % _t[:120]
            assert '资金不足' in _t, '页面没给引擎的拒单原因：%r' % _t[:150]
            assert '重建' in _t, '页面没给下一步：%r' % _t[:150]
            # 🔴 同一句话不许说两遍（标题 + 正文都写"一笔成交都没有"）
            assert _t.count('一笔成交都没有') == 1, \
                '"一笔成交都没有"说了 %d 遍' % _t.count('一笔成交都没有')

            # 建账户：资金框要写单位，填小数字当场提示
            pg.goto(base + '#/live', wait_until='networkidle')
            pg.wait_for_selector('.znew[data-zone=paper]', timeout=20000)
            _lv_open_newform(pg, 'paper')
            _ph = pg.eval_on_selector('#nf_paper .nc', 'e => e.placeholder')
            assert '元' in _ph, \
                '资金框没写单位（%r）—— 那正是填成 40 的直接原因' % _ph
            pg.fill('#nf_paper .nc', '40')
            pg.wait_for_timeout(250)
            _hint = pg.inner_text('#nf_paper .ncwhy')
            assert '买不起一手' in _hint and '400,000' in _hint, \
                '填 40 没当场提示（或没给"是不是想填 40 万"）：%r' % _hint
            pg.fill('#nf_paper .nc', '400000')
            pg.wait_for_timeout(250)
            assert not pg.inner_text('#nf_paper .ncwhy').strip(), \
                '填了正常金额还在报警 —— 那就是常驻告警'
            assert not errs, '页面抛了异常：%s' % errs[:1]
            notes.append('页面：警告可见且不重复、给下一步；'
                         '资金框带单位、填小数字当场提示')
            br.close()
    finally:
        httpd.shutdown()
        lv.LIVE = real
        sv.ALLOW_LIVE = prev_allow
        shutil.rmtree(tmp, ignore_errors=True)
    return '；'.join(notes)


@case('说了「重建」就得有重建按钮：两个入口都走得通（playwright）', tag='web')
def t_paper_rebuild_entry():
    """🔴🔴 **说了下一步就得给入口。**

    上一条用例（0 笔警告）把话说到了「改大初始资金再**重建**即可」，
    而查 `grep "act:'reset'" web/views/*.js` —— **前端从来没调过它**。
    也就是说页面指了一条**不存在的**路：0 笔那条警告与对账不一致那个
    `alert` 都告诉人去重建，而页面上**一个重建按钮都没有**。
    那是 backLink 那条的反面（「给一个点了没反应的按钮比不给更糟」）的
    另一面 —— **说了不能做却不给出路，最后会变成绕过整个入口**
    （同 `force_price` / `--allow-shrink` 那条：硬拒必须配逃生口）。

    两个入口**走同一条重建链**（`paperRebuild`）—— 各写一份的话
    confirm 的措辞与 `confirm:true` 这个服务端必传项迟早分叉。

    判据是**走完整条路**，不是"有那个按钮"：
      ① 0 笔 -> 点「⚙ 改初始资金」真的开到改本金那个框（不是开了个别的浮层）
      ② 改完 -> 点「↻ 重建」-> **账本里真的有成交了**、警告自己消失
      ③ 对账不一致 -> 浮层里有「先不动」与「重建」两条路（不是一个
         只能点确定的 alert），点重建 -> 不一致消失
      ★ ④ **confirm 里承诺的事要兑现**：手工补录的那几笔重建之后必须还在
         —— 页面上写着"手工补录的那几笔不会被删"，那就是一句可验证的承诺。
    """
    import shutil
    import tempfile
    import threading
    from http.server import ThreadingHTTPServer

    from playwright.sync_api import sync_playwright

    from assay import live as lv
    from assay import server as sv
    from assay.lv import paper as _paper

    real = lv.LIVE
    tmp = tempfile.mkdtemp(prefix='selftest_rebuild_')
    shutil.copytree(real, os.path.join(tmp, 'live'), dirs_exist_ok=True)
    lv.LIVE = os.path.join(tmp, 'live')
    prev_allow = sv.ALLOW_LIVE
    sv.ALLOW_LIVE = True
    sv._scan()
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), sv.Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    notes = []
    try:
        src = next((a for a in lv.load_accounts() if lv.is_paper(a)
                    and a.get('code_sha256')), None)
        if src is None:
            return '跳过（本机没有绑了策略的模拟盘）'
        aid = src['id']
        _ap = os.path.join(lv.LIVE, 'accounts.json')
        _fp = os.path.join(lv.LIVE, aid, 'fills.jsonl')

        def _set_cash(cash):
            d = json.load(open(_ap))
            for x in d:
                if x['id'] == aid:
                    x['init_cash'] = cash
                    x['paper_start'] = '2026-09-01'
            json.dump(d, open(_ap, 'w'), ensure_ascii=False)

        def _n_paper():
            return len([r for r in lv.fills(aid) if r.get('source') == 'paper'])

        # 构造：用户的真实现状 —— 40 元本金，推进出 0 笔
        _set_cash(40.0)
        _paper.reset(aid)
        assert _paper.advance(aid).get('n_fills') == 0, '构造不对：40 元买到票了'

        with sync_playwright() as p:
            try:
                br = p.chromium.launch()
            except Exception as e:                          # noqa: BLE001
                return '跳过（浏览器不可用: %s）' % type(e).__name__
            pg = br.new_page(viewport={'width': 1500, 'height': 950})
            errs = []
            pg.on('pageerror', lambda e: errs.append(e.stack or str(e)))
            pg.on('dialog', lambda d: d.accept())           # confirm 一路答应
            base = 'http://127.0.0.1:%d/' % port
            _nm = next(x['name'] for x in lv.load_accounts() if x['id'] == aid)

            def _open():
                # 🔴 **同一个 hash `goto` 不重新加载页面** —— 已经在这一页时
                #   要 `reload()`，否则接下来那几条断言读的是**上一段**的
                #   渲染（本项目为此空转过三条判据）。
                _url = base + '#/live/' + aid
                if pg.url.endswith('#/live/' + aid):
                    pg.reload(wait_until='networkidle')
                else:
                    pg.goto(_url, wait_until='networkidle')
                pg.wait_for_function(
                    'n => { const h = document.querySelector("#main .lvhead h2");'
                    '       return h && h.textContent.trim() === n; }',
                    arg=_nm, timeout=30000)

            # ---- ① 两个入口是【按钮】，且「改初始资金」真的开到那个框 ----
            _open()
            pg.wait_for_selector('#lvrb', timeout=20000)
            for _id, _what in (('#lvrb', '重建'), ('#lvsetc', '改初始资金')):
                _e = pg.query_selector(_id)
                assert _e is not None, '0 笔那条警告里没有「%s」入口' % _what
                # 🔴 判据是**看得出能点**，不是"DOM 里有这个 id"——
                #   画成一段灰字的话人根本不会去点（同「选股理由」那次）。
                assert _e.is_visible(), '「%s」不可见' % _what
                assert pg.eval_on_selector(
                    _id, 'e => getComputedStyle(e).cursor') == 'pointer', \
                    '「%s」没有手型光标 —— 看不出能点' % _what
            pg.click('#lvsetc')
            # 🔴 **等浮层开了，再自己判里面是什么** —— 直接
            #   `wait_for_selector('#ecash')` 的话，"开到了别的浮层"
            #   （比如策略）是一句超时，**报错指不到原因**
            #   （同「报错必须指向真正的原因」那条）。
            pg.wait_for_selector('.stmodal', timeout=20000, state='visible')
            pg.wait_for_timeout(400)
            assert pg.query_selector('#ecash') is not None, (
                '「⚙ 改初始资金」开的不是改本金那个浮层（开到了「%s」）'
                ' —— 点了有反应不等于走得到'
                % (pg.inner_text('.stmodal h3') if
                   pg.query_selector('.stmodal h3') else '?'))
            assert pg.is_visible('#ecash'), '改本金那个框不可见'
            notes.append('0 笔警告里两个入口都可点，「改初始资金」开到 #ecash')

            # ---- ② 改完 -> 重建 -> 账本里真的有成交 ----
            pg.fill('#ecash', '400000')
            pg.click('#esave')
            pg.wait_for_timeout(1500)
            assert float(lv.get_account(aid)['init_cash']) == 400000.0, \
                '本金没存上：%s' % lv.get_account(aid).get('init_cash')
            _open()
            pg.wait_for_selector('#lvrb', timeout=20000)
            pg.click('#lvrb')
            # 重建要重放整段（几秒），等**账本**而不是等 DOM ——
            # 等 DOM 的话三种坏法（没绑 handler / 漏传 confirm / 接口失败）
            # 都是同一个超时，报错指不到原因。
            _deadline = time.time() + 120
            while time.time() < _deadline and _n_paper() == 0:
                pg.wait_for_timeout(500)
            assert _n_paper() > 0, (
                '点了「重建」账本里还是 0 笔 —— 页面指的那条路走不通'
                '（没绑 handler？漏传 confirm？）')
            pg.wait_for_timeout(1200)
            assert not any('一笔成交都没有' in e.inner_text()
                           for e in pg.query_selector_all('#main .lvwarn')), \
                '重建出成交了，页面还挂着"一笔都没有"的警告'
            notes.append('重建之后账本 %d 笔、警告自己消失' % _n_paper())

            # ---- ③ 对账不一致：给的是【两条路的浮层】，不是一个 alert ----
            #   构造：把账本里某一笔的股数改掉（身份就变了）->
            #   重跑必然对不上。同时补一笔**手工**记录，验 ④ 那句承诺。
            _rows = [json.loads(x) for x in
                     open(_fp, encoding='utf-8').read().splitlines() if x.strip()]
            _hit = next(i for i, r in enumerate(_rows)
                        if r.get('source') == 'paper')
            _rows[_hit]['shares'] = int(_rows[_hit]['shares']) + 100
            _man = dict(_rows[_hit], source='manual', uid='selftestmanual',
                        note='selftest 手工补录')
            _rows.append(_man)
            open(_fp, 'w', encoding='utf-8').write(
                '\n'.join(json.dumps(r, ensure_ascii=False) for r in _rows) + '\n')
            _r = _paper.advance(aid)
            assert _r.get('mismatch'), '构造不对：改了股数竟然还对得上：%s' % _r

            _open()
            pg.wait_for_selector('#lvadv', timeout=20000)
            pg.click('#lvadv')
            pg.wait_for_selector('#mmrb', timeout=30000, state='visible')
            _mt = pg.inner_text('.stmodal')
            assert '对不上' in _mt, '浮层没说清对账不一致：%r' % _mt[:150]
            assert pg.query_selector('#mmno') is not None, \
                '只给了重建这一条路 —— 「先不动」也得有（账本没被改动，' \
                '不该逼人现在就删档）'
            # 两个入口必须是**同一条**重建链
            _src = open(os.path.join(REPO, 'web/views/live.js'),
                        encoding='utf-8').read()
            assert _src.count('paperRebuild(aid)') >= 2 \
                and _src.count('async function paperRebuild(') == 1, \
                '两个入口没走同一条重建链 —— confirm 措辞与 confirm:true 会分叉'
            pg.click('#mmrb')
            # 🔴 **不能只等 `mismatch` 消失**：`state()` 读不到文件时返回
            #   `{}`，而「重建」第一步 `reset` 正好把 `_paper.json` 删掉 ——
            #   于是轮询在**重建刚开始**那一刻就满足了，后面读状态文件直接
            #   `FileNotFoundError`。要等的是**重建真的做完**：
            #   状态文件回来了、`advanced_to` 有了、且没有 mismatch。
            #   （全量跑时才偶发 —— 单跑 `--web` 靠时序运气一直是绿的，
            #   正是「偶发绿的用例比红的更危险」那条。）
            _deadline = time.time() + 120
            while time.time() < _deadline:
                _st = _paper.state(aid)
                if _st.get('advanced_to') and not _st.get('mismatch'):
                    break
                pg.wait_for_timeout(500)
            _st = _paper.state(aid)
            assert _st.get('advanced_to') and not _st.get('mismatch'), \
                '点了「重建」没重建完（state=%r）' % ({
                    k: _st.get(k) for k in ('advanced_to', 'n_fills')},)
            notes.append('对账不一致给的是浮层（两条路），重建后不一致消失')

            # ---- ⑤ 旧状态（没记原因）也不许渲染成一片空白 ----
            #   🔴 `rejects`/`why_empty` 落在 `_paper.json` 里，而它可能是
            #     **这个功能之前**写的 —— 那时页面又是一个字都不说，
            #     **和用户报的症状一模一样**，只是成因不同。
            #   ★ 那一支**不给「改初始资金」按钮** —— 我们并不知道原因，
            #     摆一个在那儿就是在暗示"原因是本金"。
            _sp = os.path.join(lv.LIVE, aid, '_paper.json')
            _st = json.load(open(_sp))
            _st.pop('why_empty', None)
            _st.pop('rejects', None)
            _st['n_fills'] = 0
            json.dump(_st, open(_sp, 'w'), ensure_ascii=False)
            _open()
            pg.wait_for_timeout(500)
            _box = pg.query_selector('#main .lvwarn')
            assert _box is not None, (
                '旧状态（0 笔、没记原因）页面又是一片空白 —— '
                '那正是用户报的「也推进了，但是没有任何数据出现」')
            _t2 = _box.inner_text()
            assert '旧版本' in _t2 and '推进' in _t2, \
                '没说清"这份状态没记原因"也没给下一步：%r' % _t2[:140]
            assert pg.query_selector('#lvsetc') is None, (
                '旧状态下摆了「改初始资金」—— 那是在暗示原因是本金，'
                '而我们并不知道（候选池空 / 没有调仓日长得一模一样）')
            notes.append('旧状态也说得出话，且不暗示原因')

            # ---- ④ confirm 里承诺"手工补录的不会被删" -> 必须兑现 ----
            _left = [r for r in lv.fills(aid) if r.get('uid') == 'selftestmanual']
            assert _left, (
                '重建把手工补录的那笔也删了 —— 而 confirm 里写着"不会被删"，'
                '那是一句承诺')
            assert not errs, '页面抛了异常：%s' % errs[:1]
            notes.append('手工补录的那笔重建之后还在')
            br.close()
    finally:
        httpd.shutdown()
        lv.LIVE = real
        sv.ALLOW_LIVE = prev_allow
        shutil.rmtree(tmp, ignore_errors=True)
    return '；'.join(notes)


@case('绑策略是【选】不是手填路径，清单由服务端给（playwright）', tag='web')
def t_strategy_picker():
    """用户 2026-09-18："实盘、模拟盘中绑定策略，需要手动填路径，
    应该是一个列表的形式用于选择。"

    原来是个裸 `<input placeholder="strategies/…/x.py">` —— 要绑就得
    **记住路径**，而本地有 28 个策略、目录名还带中文。打错一个字的表现是
    `策略文件不存在`（还算响亮），但**打成另一个真实存在的策略**就是
    静默绑错，而实盘信号从此按另一套规则出。

    🔴 **清单由服务端给**（`lv.list_strategies`，同「可选清单由服务端给」）：
      判据是**有没有顶层 `initialize`** —— 那正是 `run.py` 认的东西，
      所以共享层 `ETF/_etf_core.py`（自己 docstring 就写着"本文件不是策略"）
      自动挡在外面，而不是靠 `_` 前缀那种会过期的约定。

    判据是**走完整条路**，不是"有个 select"：
      ① 控件真的是 select（改回 input 要被抓到）
      ② option 集合 == 服务端清单，且页面源码里**没有任何策略路径字面量**
      ③ `_etf_core.py` 不在里面（+ 反向自证它确实在磁盘上，否则空转）
      ④ 当前绑的那个被选中
      ⑤ 🔴 绑着一个**磁盘上已经没有**的文件时，它仍在列表里、仍被选中，
        且页面**说出来** —— 静默消失的话选择器会落到别的策略，
        一保存就把账户悄悄改绑了
      ⑥ 选一个别的 -> 绑 -> **账本里真的变了**
      ⑦ 手填逃生口还在（`bind_version` 本来就接受任意路径）
    """
    import shutil
    import tempfile
    import threading
    from http.server import ThreadingHTTPServer

    from playwright.sync_api import sync_playwright

    from assay import live as lv
    from assay import server as sv

    real = lv.LIVE
    tmp = tempfile.mkdtemp(prefix='selftest_pick_')
    shutil.copytree(real, os.path.join(tmp, 'live'), dirs_exist_ok=True)
    lv.LIVE = os.path.join(tmp, 'live')
    prev_allow = sv.ALLOW_LIVE
    sv.ALLOW_LIVE = True
    sv._scan()
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), sv.Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    notes = []
    try:
        # ---- 服务端清单本身 ----
        d = lv.list_strategies('')
        items = d['items']
        assert len(items) >= 10, '清单太短，扫目录那步可能坏了：%d' % len(items)
        paths = [x['path'] for x in items]
        core = os.path.join(REPO, 'strategies/ETF/_etf_core.py')
        assert os.path.isfile(core), \
            '构造不对：_etf_core.py 不在磁盘上了，③ 那条成了空转'
        assert not any(x.endswith('_etf_core.py') for x in paths), \
            '共享层混进了可绑清单 —— 它没有 initialize，绑上去 run.py 直接崩'
        assert all(x['desc'] for x in items[:5]), \
            '没给说明：光看 froec_lu_trail5 这种文件名选不出东西'
        # 🔴 **说明里不许留 markdown 标记**：它会进 `<option>` 的文字与
        #   `title` 属性，而**属性里连 `<b>` 都用不了** —— 星号原样显示成
        #   一串 `**`（同 indicators 的 desc 进 title 那条，记过两次了）。
        #   处理在**展示层**：实测 29 个策略里 8 个的 docstring 首行用了
        #   markdown，而那本来就是写给人读的 —— 要二十多个作者改写作风格
        #   是修错了地方。
        _md = [x['name'] for x in items
               if '**' in (x['desc'] or '') or '`' in (x['desc'] or '')]
        assert not _md, '这些策略的说明里留着 markdown 标记：%s' % _md[:5]
        # 反向自证：源码里**确实有**带 markdown 的首行（否则上面那条空转）
        _src_md = 0
        for x in items:
            try:
                import ast as _a2
                _t = _a2.get_docstring(_a2.parse(
                    open(os.path.join(REPO, x['path']), encoding='utf-8').read()))
            except Exception:                           # noqa: BLE001
                _t = None
            if _t and '**' in _t.strip().splitlines()[0]:
                _src_md += 1
        assert _src_md >= 3, \
            ('构造不对：只有 %d 个策略的 docstring 首行带 markdown，'
             '"剥标记"那条断言测不到' % _src_md)

        aid = 'froec'
        src = next((a for a in lv.load_accounts() if a['id'] == aid), None)
        if src is None or not src.get('strategy_path'):
            return '跳过（本机没有绑了策略的实盘账户）'
        _ap = os.path.join(lv.LIVE, 'accounts.json')

        def _set_path(pth):
            # 🔴 浮层里的"当前绑的是什么"取自**版本行**（append-only 的绑定
            #   记录），不是 accounts.json —— 后者只是它的镜像。
            #   只改镜像的话这段构造根本不生效（第一版就是这么空转的）。
            js = json.load(open(_ap))
            sha = None
            for x in js:
                if x['id'] == aid:
                    x['strategy_path'] = pth
                    sha = x.get('code_sha256')
            json.dump(js, open(_ap, 'w'), ensure_ascii=False)
            vp = os.path.join(lv.LIVE, aid, 'versions.jsonl')
            rows = [json.loads(ln) for ln in open(vp, encoding='utf-8')
                    if ln.strip()]
            for r in rows:
                if r.get('code_sha256') == sha:
                    r['strategy_path'] = pth
            with open(vp, 'w', encoding='utf-8') as f:
                for r in rows:
                    f.write(json.dumps(r, ensure_ascii=False) + '\n')

        with sync_playwright() as p:
            try:
                br = p.chromium.launch()
            except Exception as e:                          # noqa: BLE001
                return '跳过（浏览器不可用: %s）' % type(e).__name__
            pg = br.new_page(viewport={'width': 1500, 'height': 950})
            errs = []
            pg.on('pageerror', lambda e: errs.append(e.stack or str(e)))
            base = 'http://127.0.0.1:%d/' % port

            def _open_strat(acc):
                nm = next(x['name'] for x in lv.load_accounts()
                          if x['id'] == acc)
                # 🔴 同一个 hash `goto` 不重载 —— 已经在这一页就 reload
                if pg.url.endswith('#/live/' + acc):
                    pg.reload(wait_until='networkidle')
                else:
                    pg.goto(base + '#/live/' + acc, wait_until='networkidle')
                pg.wait_for_function(
                    'n => { const h = document.querySelector("#main .lvhead h2");'
                    '       return h && h.textContent.trim() === n; }',
                    arg=nm, timeout=30000)
                pg.click('#lvstrat')
                pg.wait_for_selector('#bp', timeout=20000)

            # ---- ①②③④ ----
            _open_strat(aid)
            assert pg.eval_on_selector('#bp', 'e => e.tagName') == 'SELECT', \
                '绑定策略还是个裸输入框 —— 要绑就得背下 28 条路径'
            got = pg.eval_on_selector_all(
                '#bp option', 'es => es.map(e => e.value)')
            assert [x for x in got if x != '__manual__'] == paths, \
                ('页面列的与服务端清单对不上 —— 前端自己拼了一份？\n'
                 '页面 %s\n服务端 %s' % (got[:3], paths[:3]))
            _gs = pg.eval_on_selector_all('#bp optgroup', 'es => es.map(e => e.label)')
            assert len(_gs) >= 3, '没有按目录分组：%s' % _gs
            assert pg.eval_on_selector('#bp', 'e => e.value') \
                == src['strategy_path'], '当前绑的那个没被选中'
            # 页面源码里不许有策略路径字面量（清单必须来自服务端）
            _js = open(os.path.join(REPO, 'web/views/live-strat.js'),
                       encoding='utf-8').read()
            _code = re.sub(r'/\*.*?\*/', '', _js, flags=re.S)
            _code = re.sub(r'(?m)^\s*//.*$', '', _code)
            for _p in paths[:8]:
                assert _p not in _code, \
                    '页面里写死了策略路径 %s —— 加一个策略它不会出现' % _p
            notes.append('%d 个策略按 %d 组列出，当前项选中，共享层挡在外面'
                         % (len(paths), len(_gs)))

            # ---- ⑦ 手填逃生口 ----
            assert not pg.is_visible('#bpm'), '手填框平时不该占地方'
            pg.select_option('#bp', '__manual__')
            pg.wait_for_timeout(250)
            assert pg.is_visible('#bpm'), \
                '选了「其它」手填框没出来 —— 绑 strategies/ 之外的文件这条路被堵死了'
            # 🔴 光验"框露出来"是不够的：`bpVal` 仍取 select 的话，
            #   手填的内容根本没进 POST（绑上去的是字面量 `__manual__`）——
            #   而那一版照样让框弹出来（变异实测漏过）。判据要**走完**：
            #   填进去、绑一次、账本里必须是填的那个。
            _man = next(x for x in paths if x != src['strategy_path'])
            pg.fill('#bpm', _man)
            pg.click('#bb')
            _dl0 = time.time() + 60
            while time.time() < _dl0 and \
                    lv.get_account(aid)['strategy_path'] != _man:
                pg.wait_for_timeout(300)
            assert lv.get_account(aid)['strategy_path'] == _man, \
                ('手填了路径、点了绑定，账本里却不是它（%s）—— '
                 '手填的值没进 POST，那个逃生口是摆设'
                 % lv.get_account(aid)['strategy_path'])
            notes.append('手填逃生口真的绑得上')
            _open_strat(aid)

            # ---- ⑥ 换一个 -> 绑 -> 账本真的变 ----
            _cur_now = lv.get_account(aid)['strategy_path']
            _other = next(x for x in paths if x != _cur_now)
            _lv_bind_strategy(pg, _other)
            _dl = time.time() + 60
            while time.time() < _dl and \
                    lv.get_account(aid)['strategy_path'] != _other:
                pg.wait_for_timeout(300)
            assert lv.get_account(aid)['strategy_path'] == _other, \
                ('选了另一个策略、点了绑定，账本里还是旧的 —— '
                 '选择器的值没传到 POST？实际 %s'
                 % lv.get_account(aid)['strategy_path'])
            notes.append('选另一个 -> 绑定 -> 账本跟着变')

            # ---- ⑤ 绑着一个磁盘上没有的文件 ----
            _gone = 'strategies/小市值/这个文件不存在_selftest.py'
            assert not os.path.isfile(os.path.join(REPO, _gone)), \
                '构造不对：那个"不存在"的文件竟然在'
            _set_path(_gone)
            _open_strat(aid)
            _got2 = pg.eval_on_selector_all(
                '#bp option', 'es => es.map(e => e.value)')
            assert _gone in _got2, (
                '绑着的文件没了，清单里就把它抹掉了 —— '
                '选择器会落到别的策略，一保存就把账户悄悄改绑了')
            assert pg.eval_on_selector('#bp', 'e => e.value') == _gone, \
                '那一项没被选中 —— 等于默默替人选了另一个策略'
            # 🔴 判据要**限定在警告块里** —— 服务端给那一项的 desc 就写着
            #   "……不在磁盘上了"，而它渲染在 `<option>` 里，
            #   查 `.stbox` 全文的话**必然命中**（变异实测：把整块警告
            #   去掉照样绿，同「判据比要证的事宽」）。
            # ★ 认准**这一块**（`#bpgone`）：浮层里本来就有另一个
            #   `.lvwarn`（"网页触发回测没开"），按 class 取会命中它。
            _w = pg.query_selector('#bpgone')
            assert _w is not None, \
                '绑的文件没了，页面上没有任何警告 —— 只有下拉里一行小字'
            _wt = _w.inner_text()
            assert '不在磁盘上' in _wt and _gone in _wt, \
                '警告没说清是哪个文件：%r' % _wt[:200]
            assert not errs, '页面抛了异常：%s' % errs[:1]
            notes.append('绑的文件没了：仍在列表、仍选中、页面说出来')
            br.close()
    finally:
        httpd.shutdown()
        lv.LIVE = real
        sv.ALLOW_LIVE = prev_allow
        shutil.rmtree(tmp, ignore_errors=True)
    return '；'.join(notes)


@case('模拟盘要认策略声明的数据源与印花税（ETF 那条链）', tag='slow')
def t_paper_declared_lake():
    """用户 2026-09-18："ETF轮动模拟，点击推进报错。"

    报的是 `IOException: No files found ... /datalake/std/etf_master.parquet`
    —— **正是 2026-09-13 记过的那个失效模式**：ETF 策略必须跑在 `etf_lake`
    上，靠人记得传 `--datalake` 会漏，所以改成策略模块级 `DATALAKE='etf_lake'`
    + `run.py` 的 `resolve_lake` 解析。

    🔴 **但建引擎的路径不止 run.py 一条。** 模拟盘与业绩页那条「策略曲线」
      走的是 `lv/bench.build_engine`，而它一直没接上那个声明 ——
      同一条纪律的另一半漏了整整五天，直到有人真的建了个 ETF 模拟盘。
      ★ 而**崩掉还算好的**：`etf_trend_momentum` 在股票面板上候选池恒空
        -> 全程空仓、一条平线、**不报任何错**。

    🔴 印花税同理：`close_tax` 原来被 `locked` 锁死成 'auto'（股票口径），
      于是 ETF 每笔卖出都被按万5 收 —— 年换手 9 次就是 **0.45%/年**
      凭空扣掉，**而它不报错**。ETF 无印花税是**事实不是偏好**，
      所以它写在策略里，这里必须让它生效。
    ★ 分工要钉住：佣金 / 最低佣金 = 券商的事（用**账户**的，仍然锁）；
      印花税 = 标的的事（听策略的）。只钉一头的话"全听策略"也能过。
    """
    from assay import live as lv
    # ★ `bench` 不在门面的转发清单里（它不是账本读写的域），直接 import
    from assay.lv import bench as _bench

    accts = lv.load_accounts()
    etf = next((a for a in accts
                if 'etf' in (a.get('strategy_path') or '').lower()
                and a.get('code_sha256')), None)
    plain = next((a for a in accts
                  if a.get('code_sha256')
                  and 'etf' not in (a.get('strategy_path') or '').lower()), None)
    if etf is None or plain is None:
        return '跳过（本机没有 ETF 账户或普通账户）'

    # ---- 声明了 DATALAKE 的：必须解析到那个 lake ----
    eng, meta = _bench.build_engine(etf['id'])
    assert eng is not None, '建引擎就失败了：%s' % meta.get('error')
    assert meta.get('datalake_declared') is True, \
        '这个策略声明了 DATALAKE，服务端却说没有'
    assert os.path.basename(meta['datalake']) == 'etf_lake', \
        ('没按策略声明解析数据源，跑在 %s 上 —— 那是股票面板，'
         'ETF 策略会崩在 etf_master.parquet（或更糟：候选池恒空、'
         '全程空仓而不报错）' % meta['datalake'])
    # feed 真的指向它（只看 meta 的话，"报了但没用上"照样绿）
    assert os.path.realpath(str(eng.feed.root or '')) \
        == os.path.realpath(meta['datalake']), \
        'meta 说 %s，而 feed 实际跑在 %s' % (meta['datalake'], eng.feed.root)

    # ---- 成本分工：印花税听策略、佣金听账户 ----
    # 🔴 `set_order_cost` 在 `initialize` 里，**boot 之后**才生效 ——
    #   直接读 `eng.cost` 拿到的是建引擎时的值（同「抬头打印的成本是假的」）。
    _acct_comm = eng.cost.commission
    eng.boot()
    # ★ 先判类型再比值：锁回去时它是字符串 'auto'，`float()` 会抛
    #   `could not convert string to float` —— 用例是红的，但**报错指不到
    #   原因**（同「报错必须指向真正的原因」）。
    assert not isinstance(eng.cost.close_tax, str) \
        and float(eng.cost.close_tax) == 0.0, \
        ('ETF 的印花税该是 0（策略自己声明的事实），实际 %r —— '
         '每笔卖出凭空多扣万5' % (eng.cost.close_tax,))
    assert eng.cost.commission == _acct_comm, \
        ('佣金被策略覆盖了（%s -> %s）—— 那是【券商】收多少，'
         '该用账户配的那档' % (_acct_comm, eng.cost.commission))

    # ---- 反向自证：没声明的策略一切照旧（否则上面两条可能是"全听策略"）----
    eng2, meta2 = _bench.build_engine(plain['id'])
    assert eng2 is not None, '建引擎失败：%s' % meta2.get('error')
    assert meta2.get('datalake_declared') is False, \
        '这个策略没声明 DATALAKE，服务端却说有'
    assert os.path.basename(meta2['datalake']) != 'etf_lake', \
        '没声明的策略被拖到 etf_lake 上了'
    eng2.boot()
    assert str(eng2.cost.close_tax) == 'auto', \
        ('没声明成本的策略，印花税该仍是按日期分段的 auto，实际 %r'
         % eng2.cost.close_tax)
    # ---- lake 不存在时要说人话，不能是 500 ----
    # 🔴 `resolve_lake` 抛的是 **SystemExit（BaseException）**，
    #   而 `_live_err` 只 `except Exception` —— 不翻译的话请求线程直接死，
    #   页面看到一个**没有原因**的 500。
    # ★ 这条**必须构造**：本机 `etf_lake` 真的存在，那条错误路径平时
    #   一步都走不到（变异实测第一轮就是这么漏的）。
    import run as _run
    _orig = _run.resolve_lake
    try:
        def _boom(mod, cli):
            raise SystemExit('策略声明要跑在 /nope（DATALAKE=…），但它不存在。')
        _run.resolve_lake = _boom
        try:
            _bench.build_engine(etf['id'])
            raise AssertionError('lake 不存在却照跑了')
        except lv.LiveError as e:
            assert '不存在' in str(e), '翻译丢了原因：%r' % str(e)
        except SystemExit:
            raise AssertionError(
                'SystemExit 没翻成 LiveError —— 它是 BaseException，'
                '`_live_err` 抓不到，页面会看到一个没有原因的 500')
    finally:
        _run.resolve_lake = _orig

    return ('ETF 账户解析到 etf_lake、印花税 0、佣金仍是账户的；'
            '未声明的账户走默认 lake 且印花税仍 auto；'
            'lake 不存在时翻成人话不是 500')


@case('ETF / 指数不在面板里：取价与取名要有回落（实盘四处）', tag='slow')
def t_etf_price_and_name():
    """🔴🔴 2026-09-18 用户报：ETF 模拟盘「持仓市值 0.00、浮盈 −100%、
    而且 ETF 持仓都没有中文名」。

    根因一句话：**主面板 `mart/panel_daily/` 是【股票】宽表，ETF 一行都没有**，
    而实盘模块四处取数全查它 —— 查不到 -> 价格 None -> 市值 0 -> 浮盈 −100%，
    **而它不报错**，屏幕上看着就像"这个账户把钱亏光了"。

    四处都要回落（`lv/tdx.py`，同一份实现）：

        perf._last_px   持仓估值取价      -> 市值 0、浮盈 −100%
        sig._names      持仓/待办取名      -> 那一列空着
        px.day_price    「价格留空自动补」  -> 报"面板里没有这个代码"，整条路走不通
        px.day_range    「成交价必须在当日区间内」-> **静默放行**，小数点点错拦不住

    ★ 判据落在**可证的事实**上：价必须等于 tdx 原始日线里那天的收盘、
      名字必须非空、离谱价必须被拦。
    🔴 **反向自证：股票不许走回落** —— 回落只扫 `etf_*`/`index_*`
      （`stock_*` 是 1600 万行，拿它兜底既慢又没必要）。只测 ETF 的话，
      "把所有代码都拖去 tdx"也能全绿，而那会让股票的取价绕开面板。
    """
    import shutil
    import tempfile

    from assay import live as lv
    from assay.lv import tdx as _tdx
    from assay.lv import px as _px

    root = _px._lake(None)
    # ---- 现挑一只【本地真有日线】的 ETF，不写死代码 ----
    import duckdb
    import glob as _g
    pat = os.path.join(root, 'raw/tdx/kline/etf_*.parquet')
    if not _g.glob(pat):
        return '跳过（本地没有 ETF 日线）'
    row = duckdb.connect().execute(
        "SELECT symbol, date, open, high, low, close FROM read_parquet('%s') "
        "WHERE close > 0 QUALIFY row_number() OVER "
        "(PARTITION BY symbol ORDER BY date DESC) = 1 "
        "ORDER BY date DESC LIMIT 1" % pat).fetchone()
    assert row, '构造不对：ETF 日线里一行都没有'
    sym, day, o, hi, lo, cl = row
    jq = sym[2:] + ('.XSHG' if sym[:2] == 'sh' else '.XSHE')
    assert _tdx.to_symbol(jq) == sym, \
        'jq_code <-> symbol 换算错了：%s -> %s（应为 %s）' \
        % (jq, _tdx.to_symbol(jq), sym)

    # ---- ① 面板里确实没有它（否则整条用例是空转）----
    n = duckdb.connect().execute(
        "SELECT count(*) FROM read_parquet('%s/mart/panel_daily/panel_*.parquet') "
        "WHERE jq_code = '%s'" % (root, jq)).fetchone()[0]
    assert n == 0, '构造不对：%s 竟然在面板里（%d 行），这条用例测不到回落' % (jq, n)

    # ---- ② 取价 / 取名 / 区间 ----
    assert abs(lv.day_price(jq, day, 'close') - float(cl)) < 1e-6, \
        'ETF 取不到当日收盘（%s %s）' % (jq, day)
    assert abs(lv.day_price(jq, day, 'open') - float(o)) < 1e-6, \
        'ETF 取不到当日开盘 —— 「价格留空自动补」那条路对 ETF 走不通'
    rg = lv.day_range(jq, day)
    assert rg and abs(rg[0] - float(lo)) < 1e-3 and abs(rg[1] - float(hi)) < 1e-3, \
        'ETF 取不到当日 high/low：%r' % (rg,)
    try:
        lv.check_price_in_range(jq, day, float(hi) * 10 + 1)
        raise AssertionError(
            '离谱价没被拦 —— 那道校验对 ETF 静默放行了，'
            '小数点点错、误填后复权价全都拦不住')
    except lv.LiveError:
        pass
    nm = _tdx.names(root, [jq])
    assert nm.get(jq), 'ETF 取不到名字：%r' % nm
    # 🔴 「名字里不许有截断乱码」这条要**现挑一个真被截断的**来验 ——
    #   上面那只是"最新那天的第一只"，名字恰好完整，于是这条断言在它身上
    #   是**空转**的（变异实测漏过：不去乱码照样绿）。
    #   tdx 的名称字段定长 16 字节，截断处会劈开一个汉字 -> U+FFFD。
    _FF = '\ufffd'
    _snap = _tdx.name_snap(root)
    _bad = None
    if _snap:
        for _sy, _nn in duckdb.connect().execute(
                "SELECT symbol, name FROM read_parquet('%s') "
                "WHERE class = 'etf'" % _snap).fetchall():
            if _nn and _FF in _nn:
                _bad = _sy
                break
    assert _bad, '构造不对：快照里一个被截断的 ETF 名字都没有，这条测不到'
    _bjq = _bad[2:] + ('.XSHG' if _bad[:2] == 'sh' else '.XSHE')
    _bn = _tdx.names(root, [_bjq]).get(_bjq)
    assert _bn and _FF not in _bn, \
        ('名字里留着截断产生的乱码字符（屏幕上是个方块）：%r' % _bn)

    # ---- ③🔴 反向自证：股票不许走这条回落 ----
    st = duckdb.connect().execute(
        "SELECT jq_code, date, close_bfq FROM "
        "read_parquet('%s/mart/panel_daily/panel_*.parquet') "
        "WHERE close_bfq > 0 ORDER BY date DESC LIMIT 1" % root).fetchone()
    assert st, '面板里一行都没有？'
    assert not _tdx.last_close(root, [st[0]], st[1]), \
        ('股票被 tdx 回落接管了 —— 它只该扫 etf_*/index_*，'
         '而 stock_* 是 1600 万行')
    assert abs(lv.day_price(st[0], st[1], 'close') - float(st[2])) < 1e-6, \
        '股票的取价被改坏了（应该还走面板）'

    # ---- ④ 端到端：构造一个持有 ETF 的账户，市值不许是 0 ----
    real = lv.LIVE
    tmp = tempfile.mkdtemp(prefix='selftest_etfpx_')
    shutil.copytree(real, os.path.join(tmp, 'live'), dirs_exist_ok=True)
    lv.LIVE = os.path.join(tmp, 'live')
    try:
        lv.upsert_account('etfpx', name='ETF估值', init_cash=100000.0)
        lv.add_fill('etfpx', str(day), jq, 'buy', 1000,
                    price=float(cl), fee=5.0, source='manual')
        p = lv.positions_valued('etfpx')
        it = p['items'][0]
        assert it['price'], 'ETF 持仓取不到现价 —— 市值会是 0、浮盈 −100%%'
        assert p['market_value'] > 0, \
            '持仓市值是 0（用户报的就是这个）：%r' % p['market_value']
        assert it.get('name'), 'ETF 持仓没有中文名（用户报的第二件事）'
        assert abs(p['equity'] - (p['cash'] + p['market_value'])) < 0.01, \
            '总资产 != 现金 + 市值'

        # ---- ⑤🔴 权益曲线 / 每日持仓也要有回落 ----
        #   用户 2026-09-18 第二次报：「单天 +41.8%、初始 100000、当前
        #   98432.79，累计收益居然 −18%」。根因同上，只是漏在**另外两处**
        #   （`equity_curve` 与 `hist._feed` 各抄了一份逐日价格表）——
        #   ETF 市值全程 0，于是 TWR 恰好等于 `现金/本金 − 1`。
        e = lv.equity_curve('etfpx')
        assert e['equity'], '权益曲线是空的'
        _last = e['equity'][-1]
        assert abs(_last - p['equity']) < 0.01, \
            ('权益曲线末值 %.2f 与持仓估值 %.2f 对不上 —— '
             'ETF 市值在曲线里丢了' % (_last, p['equity']))
        # 没有外部现金流时 TWR 必须等于 期末/起点 − 1（既有纪律）
        _twr = e['stats']['twr']
        _naive = _last / 100000.0 - 1
        assert abs(_twr - _naive) < 1e-6, \
            'TWR %.4f%% != 期末/本金−1 %.4f%%' % (_twr * 100, _naive * 100)
        assert abs(_twr - (p['cash'] / 100000.0 - 1)) > 1e-6, \
            ('TWR 恰好等于"只算现金"的收益率 —— 那正是 ETF 市值全程为 0 '
             '的指纹（用户看到的 −18%%）')
        # 🔴 两页必须逐日相等（既有纪律，而它们原来是**两份**取价实现）
        _em = {str(d): v for d, v in zip(e['dates'], e['equity'])}
        _h = lv.daily_holdings('etfpx', limit=500)
        _n = _bad = 0
        for _r in _h['rows']:
            _d = str(_r['date'])
            if _d in _em:
                _n += 1
                if abs(_r['equity'] - _em[_d]) > 0.01:
                    _bad += 1
        assert _h['rows'], \
            ('每日持仓一行都没有 —— 取价那一处如果不是与权益曲线【同一份】'
             '实现，这里就会空掉（它们原来正是抄的两份）')
        assert _n > 0, '构造不对：每日持仓与权益曲线没有重叠的日子'
        assert _bad == 0, \
            '每日持仓与权益曲线有 %d 天对不上（取价实现分叉了）' % _bad
    finally:
        lv.LIVE = real
        shutil.rmtree(tmp, ignore_errors=True)
    # ---- ⑥ 三条建引擎的路径都要解析策略声明的数据源 ----
    #   🔴 2026-09-13 只在 `run.py` 里接了 `resolve_lake`，于是
    #     `lv/bench.py`（模拟盘 / 策略曲线）与 `lv/sig.py`（出信号）
    #     **各漏了一次** —— 前者崩在点「推进」，后者会在 tick 里
    #     **每小时崩一次**（tick 对所有绑了策略的账户跑，含模拟盘）。
    #   判据用 `ast`：那两处都必须调 `resolve_strategy_lake`，
    #   **而且在建 `PanelFeed` 之前**（晚了 feed 已经指着主面板了）。
    import ast as _ast
    # ★ 用**真实的函数名**：`lv.make_signal` 是门面上的别名，
    #   `sig.py` 里那个叫 `build_signal`（凭印象猜名字 —— 这个坑记过好几次了）。
    for fn, func in (('assay/lv/bench.py', 'build_engine'),
                     ('assay/lv/sig.py', 'build_signal')):
        src = open(os.path.join(REPO, fn), encoding='utf-8').read()
        tree = _ast.parse(src)
        node = next((n for n in _ast.walk(tree)
                     if isinstance(n, _ast.FunctionDef) and n.name == func), None)
        assert node is not None, '%s 里没有 %s' % (fn, func)
        # ★ 比**真实调用的行号**，不比 `ast.dump` 里的字符位置 ——
        #   函数内还有 `from assay.feed import PanelFeed` 这一句，
        #   按字符串找会命中那个 import（第一版就这么误报了）。
        def _first_call(nd, name):
            ls = [n.lineno for n in _ast.walk(nd)
                  if isinstance(n, _ast.Call)
                  and (getattr(n.func, 'id', None) == name
                       or getattr(n.func, 'attr', None) == name)]
            return min(ls) if ls else None

        ln_r = _first_call(node, 'resolve_strategy_lake')
        ln_f = _first_call(node, 'PanelFeed')
        assert ln_r is not None, \
            ('%s 的 %s 没解析策略声明的数据源 —— ETF 策略会跑在股票面板上'
             % (fn, func))
        assert ln_f is None or ln_r < ln_f, \
            ('%s 的 %s 在建完 PanelFeed（第 %s 行）之后才解析数据源'
             '（第 %s 行）—— 太晚了，feed 已经指着主面板了'
             % (fn, func, ln_f, ln_r))
    return ('%s(%s) 取价/取名/区间校验都走 tdx 回落；股票仍走面板；'
            '持有 ETF 的账户市值非 0、有名字、权益曲线与每日持仓逐日一致；'
            'bench/sig 都在建 feed 前解析 DATALAKE' % (jq, nm[jq]))


@case('模拟盘盘中推进：按今开成交 / 收盘派生量是哨兵 / 日终冲正重录', 'slow')
def t_paper_intraday():
    """🔴🔴 用户 2026-09-22：「这种模拟盘数据，如果应该按照交易计划的具体分时
    去推进，如果是开盘竞价，应该调接口拿开盘数据自动推进。」

    在此之前 `advance` 的终点是**最新数据日**，而今天的日线要等收盘后那个
    同步窗口 —— 于是页面上同时摆着「今天要卖 1 买 3」和「推进到 09-21」，
    **两个数看着自相矛盾，而没有任何地方说为什么**。

    ★ 判据全部**构造**：拿**过去某一天**当"盘中那一天"（面板假装停在前一天），
      于是能把盘中拼出来的 bar 与**日终权威值逐位对照** —— 而靠"今天恰好是
      交易日且过了 09:31"的判据是空转的。
    ★ 全程跑在**临时 `lv.LIVE`** 上，真账本一个字节不动。
    """
    import assay.live as lv
    from assay.lv import openbar as ob, paper as pp, pos as _pos, base as _lb
    import duckdb
    import os
    from assay import paths as _P
    from assay.lv import bench as _bh0

    DAY, PREV = '2026-09-21', '2026-09-18'
    DAY0 = DAY
    con = duckdb.connect(':memory:')

    # ---- ① 盘中拼的 bar 必须 == 日终权威值（逐位） ----
    codes = [r[0] for r in con.execute(
        "SELECT jq_code FROM %s WHERE date = DATE '%s' ORDER BY jq_code"
        % (_P.panel_sql(), DAY)).fetchall()]
    bars, skipped = ob.today_bars(DAY, PREV, codes)
    assert len(bars) >= 3, \
        ('盘中只拼出 %d 根 bar —— 构造不对（那天的快照要在 '
         'datalake/rt/snap_1m/ 里），下面的判据全是空转' % len(bars))
    rows = con.execute(
        "SELECT jq_code, open, hfq_factor, is_open_limit_up, is_open_limit_down"
        " FROM %s WHERE date = DATE '%s' AND jq_code IN ('%s')"
        % (_P.panel_sql(), DAY, "','".join(bars))).fetchall()
    assert len(rows) == len(bars)
    for code, o, f, lu, ld in rows:
        b = bars[code]
        assert abs(b.open_raw - float(o)) < 1e-9, \
            '%s 盘中今开 %.4f != 日终权威 %.4f' % (code, b.open_raw, float(o))
        assert abs(b.factor - float(f)) < 1e-9, \
            '%s 复权因子对不上（盘中用的是昨天那份）' % code
        assert abs(b.open_hfq - round(float(o) * float(f), 4)) < 1e-4, \
            '%s open_hfq 不等于 不复权开盘 × 因子' % code
        # 🔴 一字板判定要对得上 —— 它是 OPEN 相位**唯一**能拦住成交的判据
        #   （broker：开盘涨停买不进 / 开盘跌停无对手盘）。涨跌停价是拿
        #   昨收 × limit_pct 重建的，错了会**静默放行或静默拦掉**。
        assert bool(b.open_limit_up) == bool(lu), '%s 开盘涨停判定不一致' % code
        assert bool(b.open_limit_down) == bool(ld), '%s 开盘跌停判定不一致' % code

    # ---- ①b ETF 那条路也要验：两个 lake 的 `limit_pct` **量纲不一样** ----
    # 🔴 实测 2026-09-22：主面板（股票）是 **0.20**（小数），而 `etf_lake`
    #   是 **10.0**（百分数）。照小数口径算 ETF 会得到 `pre × 11` 的涨停价
    #   —— 不是"差一点"，是**十一倍**；而它在 broker 里只表现为
    #   "开盘没涨停、照常成交"，**一个字都不报**。
    # ★ ETF 的最小变动价位还是 0.001（股票 0.01），所以舍入位数也不同。
    #   两件事都靠"复现昨天那一行的权威 limit_up"逐只自证。
    _e = next((x for x in _lb.load_accounts()
               if lv.is_paper(x) and (x.get('strategy_path') or '').find('ETF') >= 0
               and not x.get('archived')), None)
    if _e:
        _eb, _em = _bh0.build_engine(_e['id'])
        _eroot = str(_em.get('datalake') or '')
        _eheld = sorted(_pos.positions(_e['id']) or {})
        if _eroot and _eheld and os.path.isdir(_eroot):
            eb, esk = ob.today_bars(DAY0, PREV, _eheld, root=_eroot)
            assert eb, \
                ('ETF lake 上一根 bar 都没拼出来（跳过 %s）—— 那说明涨跌停的'
                 '口径/舍入自证失败，于是**每一只都被跳过**：ETF 模拟盘的'
                 '盘中推进等于没有，而它不报错' % [x['why'][:30] for x in esk][:2])
            erows = con.execute(
                "SELECT jq_code, open, is_open_limit_up FROM %s"
                " WHERE date = DATE '%s' AND jq_code IN ('%s')"
                % (_P.panel_sql(_eroot), DAY0, "','".join(eb))).fetchall()
            assert len(erows) == len(eb)
            for code, o, lu in erows:
                assert abs(eb[code].open_raw - float(o)) < 1e-9, \
                    'ETF %s 今开对不上权威值' % code
                assert bool(eb[code].open_limit_up) == bool(lu), \
                    ('ETF %s 开盘涨停判定不一致 —— 涨跌停价多半按错了量纲'
                     '（etf_lake 的 limit_pct 是百分数）' % code)
            _n_etf = len(erows)
        else:
            _n_etf = 0
    else:
        _n_etf = 0

    # ---- ② 收盘派生量是哨兵：读到就抛，不是 None ----
    one = bars[sorted(bars)[0]]
    for fld in ob.CLOSE_DERIVED:
        v = getattr(one, fld)
        assert isinstance(v, ob.Unknown), \
            ('`%s` 应当是哨兵而不是 %r —— 给 None 的话策略侧会静默收到 None、'
             '规则整段空转（本项目为 `guard.current()` 漏一列栽过）' % (fld, v))
        try:
            bool(v)
            raise AssertionError('读 `%s` 居然没抛 —— 哨兵形同虚设' % fld)
        except _lb.LiveError:
            pass

    # ---- ③ 除权守卫：今日基准价与面板昨收不一致 -> 跳过这只并说原因 ----
    # ★ 构造（真实数据里今天没有除权的票 -> 那条分支平时一步都走不到）
    import assay.realtime as _rtm
    _keep = _rtm.latest           # 🔴 先抓住**原函数**再打补丁 ——
    #   补丁里直接调 `_rt.latest` 的话调的是**被打过补丁的自己**，
    #   当场 RecursionError（第一版就是这么挂的）。

    def _fake_latest(codes_, day=None, root=None):
        d = _keep(codes_, day=day, root=root) or {}
        for k in d:                      # 把基准价改掉 = 假装今天除权了
            if d[k].get('preclose'):
                d[k]['preclose'] = float(d[k]['preclose']) * 0.5
        return d

    try:
        _rtm.latest = _fake_latest
        b2, sk2 = ob.today_bars(DAY, PREV, sorted(bars)[:3])
    finally:
        _rtm.latest = _keep
    assert not b2 and len(sk2) == 3, \
        ('除权守卫没生效：基准价对不上时还拼出了 %d 根 bar —— '
         '拿昨天的因子去算除权后的价，股数与成交价会一起错，**而它不报错**'
         % len(b2))
    assert any('除权' in x['why'] for x in sk2), \
        '跳过的原因里没提除权 —— 报错要指得到原因：%s' % sk2[:2]

    # ---- ④~⑦ 账本那一半：全程在临时 LIVE 上 ----
    import shutil
    import tempfile
    import json as _json
    import io as _io2
    # 🔴🔴 **账户是【构造】出来的，不借用真账户。**
    #   原来这里用 a2（froec_traded 的模拟盘），于是这一半悄悄依赖两件
    #   "真实数据碰巧如此"的事，2026-09-22 两件一起翻车：
    #     ① NEXT 要恰好是那个策略的**调仓日**（froec_traded 只在周内第 2 个
    #        交易日调仓）。面板当天推到 09-22（周二）之后 NEXT = 09-23（周三）
    #        -> 策略一个委托都不下 -> 「盘中那天一笔都没写」，**而产品一个
    #        字没改**。
    #     ② 就算撞上调仓日，a2 那会儿**满仓、只剩 193 元现金** ->
    #        买入腿报"资金不足一手"、卖出腿报"收盘跌停无对手盘" -> 照样 0 笔。
    #   所以现在：建一个**空仓、满现金**的模拟盘，并把调仓日**设成 NEXT**
    #   （算出 NEXT 在它那个 ISO 周里是第几个交易日，喂给 `weekday`）——
    #   于是它在 NEXT 这天必然建仓，与今天星期几、真账户什么状态全都无关。
    # ★ `weekday` 只能设成 NEXT 自己的序号：引擎的周序号表是按 **feed 里
    #   有的交易日**算的，而 feed = 面板(≤panel_last) + NEXT，所以只有
    #   "紧挨着的下一个交易日"序号才是对的（隔一天的话那一周在 feed 里
    #   缺了前几天，序号就偏了 —— 实测 09-29 算出来是 (1,-1) 不是 (2,-1)）。
    aid = 'obx'
    prev_live = lv.LIVE
    tmp = tempfile.mkdtemp(prefix='ob_case_')
    try:
        shutil.copytree(prev_live, os.path.join(tmp, 'live'))
        lv.LIVE = os.path.join(tmp, 'live')
        # 🔴 「盘中那一天」必须是**面板没有**的日子 —— 否则
        #   `can_advance_intraday` 会（正确地）拒掉："面板已经有那天的日线了"。
        panel_last = str(lv.latest_data_day())[:10]
        NEXT = str(lv.next_trading_day(panel_last))[:10]
        assert NEXT > panel_last, '构造不对：下一个交易日没算出来'
        base_rows = con.execute(
            "SELECT jq_code, close_bfq FROM %s WHERE date = DATE '%s'"
            % (_P.panel_sql(), panel_last)).fetchall()
        flat = {r[0]: float(r[1]) for r in base_rows if r[1]}
        assert len(flat) > 100, '构造不对：面板最后一天只有 %d 行' % len(flat)

        _wk = datetime.date.fromisoformat(NEXT).isocalendar()[:2]
        _ordn = sum(1 for d in _lb.calendar_days()
                    if str(d)[:10] <= NEXT and d.isocalendar()[:2] == _wk)
        assert _ordn >= 1, '构造不对：算不出 NEXT 在那一周里的序号'
        lv.upsert_account(aid, name='盘中推进构造', init_cash=400000,
                          mode='paper', paper_start=panel_last)
        lv.bind_version(aid, 'strategies/小市值/froec_traded.py',
                        params={'weekday': _ordn}, reason='用例构造')
        fp = os.path.join(lv.LIVE, aid, 'fills.jsonl')
        nf = lambda: (sum(1 for _ in _io2.open(fp, encoding='utf-8'))
                      if os.path.exists(fp) else 0)
        # 先推到面板最新日：paper_start = panel_last 且那天不是调仓日，
        # 所以这一步**不该产生成交** —— 组合就停在"空仓 + 满现金"上。
        r0 = pp.advance(aid)
        assert r0.get('ok'), '构造不对：基础推进失败 %s' % r0
        assert r0['advanced_to'] == panel_last, \
            '构造不对：基础推进到了 %s' % r0['advanced_to']

        # 构造出来的"今天快照"：平开（今开 = 昨收），高低都等于它。
        # ★ `preclose` 必须**等于**面板昨收，否则除权守卫会（正确地）跳过。
        def _synth(codes_, day=None, root=None):
            if str(day or '')[:10] != NEXT:
                return _keep(codes_, day=day, root=root)
            out = {}
            for c in (codes_ or ()):
                v = flat.get(c)
                if v:
                    out[c] = {'code': c, 'at': NEXT + ' 09:31', 'price': v,
                              'preclose': v, 'open': v, 'high': v, 'low': v,
                              'amount': 1e7, 'src': 'snap'}
            return out

        base_n = nf()

        _rtm.latest = _synth
        DAY = NEXT
        # ④ **只跑到 OPEN**：判据是**数任务调用次数**，不是猜成交的 reason
        #   字符串（OPEN 相位的 reason 有 `open` 也有 `rebalance`，
        #   我第一版就猜错了 —— 判据比要证的事窄）。
        from assay.lv import bench as _bh
        _eng, _mt = _bh.build_engine(aid, intraday=DAY)
        assert _mt.get('intraday_day') == DAY, \
            '构造不对：build_engine 没把 %s 当成盘中那一天（why=%s）' % (
                DAY, _mt.get('intraday_why'))
        _eng.boot()
        _eng.run(verbose=False)
        # 判据是引擎自己记的「被推迟的任务」——外面包一层数调用次数是量不出来的
        # （被跳过的任务照样被调一次，我第一版就这么错了）。
        dfr = dict(getattr(_eng, '_ob_deferred', {}) or {})
        assert dfr, \
            ('盘中那天没有任何任务被推迟 —— 而 froec_traded 在 14:00 注册了'
             '炸板离场与止损两个任务，它们读的是**收盘派生量**，09:31 根本'
             '不存在。一个都没拦住说明截断没生效（判据空转）')
        assert all(t > ob.PHASE_CUTOFF for t in dfr), \
            '被推迟的里混进了不晚于 %s 的任务：%s' % (ob.PHASE_CUTOFF, dfr)
        _late_fills = [f for f in _eng.broker.fills
                       if str(f['date'])[:10] == DAY
                       and f.get('reason') == 'stop']
        assert not _late_fills, \
            '盘中那天出现了止损成交 %s —— 那一档必须留到日终' % _late_fills[:2]

        r1 = pp.advance(aid, intraday=DAY)
        assert r1.get('ok'), '盘中推进失败：%s' % r1
        assert r1['advanced_to'] == DAY and r1['panel_end'] < DAY, \
            '推进到的不是那一天 / panel_end 不对：%s' % (
                (r1['advanced_to'], r1['panel_end']),)
        day_rows = [_json.loads(l) for l in _io2.open(fp, encoding='utf-8')
                    if _json.loads(l).get('trade_date') == DAY]
        assert day_rows, '盘中那天一笔都没写 —— 后面的判据全是空转'
        # ⑤ provisional 标记 + 幂等
        assert r1['prov_day'] == DAY and r1['n_provisional'] == len(day_rows), \
            '标记不对：prov_day=%s 笔数=%s 实际 %d 笔' % (
                r1['prov_day'], r1['n_provisional'], len(day_rows))
        r2 = pp.advance(aid, intraday=DAY)
        assert r2['added'] == 0 and nf() == base_n + len(day_rows), \
            '盘中推进不幂等：再推一次又加了 %s 笔（账本 %d 行）' % (
                r2['added'], nf())
        assert r2['prov_ok'] == len(day_rows) and r2['prov_reverted'] == 0

        # ⑥ 日终数据还没到时跑一次 -> **不许冲正、标记要留着**
        r3 = pp.advance(aid)
        assert r3.get('ok'), r3
        assert r3['prov_reverted'] == 0, \
            ('日终数据还没到就把 %s 笔冲正了 —— 它们本来是对的、只是还没被确认'
             % r3['prov_reverted'])
        assert r3['n_provisional'] == len(day_rows), \
            ('标记被清掉了（%s 笔）—— 清了之后那几笔会被当成 confirmed 历史，'
             '下次日终一有差异就报 mismatch，而那正是要避免的那条路'
             % r3['n_provisional'])

        # ⑦ 盘中那笔算错了 -> 冲正 + 重录，且必须收敛
        ls = _io2.open(fp, encoding='utf-8').read().splitlines()
        hit = None
        for i, ln in enumerate(ls):
            d = _json.loads(ln)
            if d.get('trade_date') == DAY and d.get('side') == 'buy':
                hit = (d['code'], int(d['shares']))
                d['shares'] = int(d['shares']) + 100
                ls[i] = _json.dumps(d, ensure_ascii=False)
                break
        assert hit, '那天没有买入成交 —— 这一段是空转的'
        _io2.open(fp, 'w', encoding='utf-8').write('\n'.join(ls) + '\n')
        r4 = pp.advance(aid, intraday=DAY)
        assert r4.get('ok'), '篡改后推进直接报 mismatch 了：%s' % r4.get('mismatch')
        assert r4['prov_reverted'] == 1 and r4['added'] == 1, \
            '没有走「冲正 + 重录」：冲正 %s 重录 %s' % (
                r4['prov_reverted'], r4['added'])
        pos = _pos.positions(aid).get(hit[0]) or {}
        sh = pos.get('shares') if isinstance(pos, dict) else pos
        assert int(sh or 0) == hit[1], \
            ('冲正之后持仓没回到权威值：%s 现在 %s 股、应当 %s 股 —— '
             '冲正必须是**反向**那一笔，同向写的话 `active_fills` 配不上对，'
             '那笔会被当成又一次买入' % (hit[0], sh, hit[1]))
        r5 = pp.advance(aid, intraday=DAY)
        assert r5['prov_reverted'] == 0 and r5['added'] == 0, \
            ('冲正过之后不收敛：又冲了 %s 笔、又加了 %s 笔 —— '
             '「冲正记录」与「被冲掉的原始记录」必须排除在对账之外，'
             '否则账本每轮都长胖' % (r5['prov_reverted'], r5['added']))
    finally:
        _rtm.latest = _keep          # ★ 补丁一定要还回去
        lv.LIVE = prev_live
        shutil.rmtree(tmp, ignore_errors=True)

    # ---- 补抓与读取必须是【同一个快照库】----
    # 🔴🔴 实测 2026-09-24 的 a4/a5（etf_p1_mask / maskpos）：8 个目标只买进
    #   3 只，另外 5 只每 5 分钟推一次、**推了一整天都买不进**，
    #   而它**不报错**（拒单只写"停牌/无行情"，看着像那几只真停牌了）。
    #   根因是 `IntradayFeed.bars` 的补抓传了 `self._root`（策略声明的
    #   `etf_lake`），而 `today_bars` 读的是主 `datalake/rt/` —— **两个库**：
    #   补抓"成功"了、写进了一个没人读的地方（实测 `etf_lake/rt/snap_1m/`
    #   里躺着 09:31 那一瞬的 9 行，而主库里只有 3 只）。
    #   更糟的是 `missing()` 的收敛设计（抓到了就不再 missing）让它
    #   **只错一次就再也不重试** —— 于是那 5 只永久买不进。
    # ★ `today_bars` 的注释早就写出了这条（「快照库只有一个，root 只用于
    #   面板」），但修的只有**读**那一侧 —— 这是同一件事的另一半。
    # ★ 判据是**三个调用看到的快照库路径必须相同**，不是"源码里有没有
    #   `root=`" —— 后者改个参数名就绕过去了。
    _seen = []
    import assay.realtime as _rtm
    _sav = (_rtm.missing, _rtm.ensure_codes, _rtm.latest)
    _alt = os.path.join(_P.datalake(), 'etf_lake')
    assert _P.datalake(_alt) != _P.datalake(), \
        '构造不对：备用 lake 与主 lake 解到同一个路径，这条判据是空转的'

    class _Inner(object):
        trading_days = [datetime.date.fromisoformat(PREV)]
    try:
        _rtm.missing = lambda codes, root=None, day=None: (
            _seen.append(('missing', root)) or list(codes))
        _rtm.ensure_codes = lambda codes, root=None, max_bars=3, day=None: (
            _seen.append(('ensure_codes', root)) or {})
        _rtm.latest = lambda codes=None, day=None, root=None: (
            _seen.append(('latest', root)) or {})
        ob.IntradayFeed(_Inner(), DAY0, PREV, root=_alt).bars(
            DAY0, ['513310.XSHG'])
    finally:
        _rtm.missing, _rtm.ensure_codes, _rtm.latest = _sav
    #   反向自证：三个都真的被调到了，否则下面那条"路径相同"是空转的
    assert {k for k, _ in _seen} == {'missing', 'ensure_codes', 'latest'}, \
        '构造不对：只调到 %s —— 这条判据没测到' % sorted({k for k, _ in _seen})
    # ★ 第二道，**分工与上面那条不同，别记反**：
    #     上面那条抓的是「**谁**写错了」（精确指到 IntradayFeed 的三个调用），
    #     这一条抓的是「**结果**不对」—— 策略 lake 底下根本不该有 rt 目录，
    #     不管是哪个新调用点写出来的。前者改个参数名就绕过去、后者绕不过去；
    #     而后者报错只说"出现了"、指不到是谁写的，所以两条都要。
    # ★ 策略 lake 的清单**照声明取**（模块级 `DATALAKE`，与 `run.resolve_lake`
    #   同源），不写死 'etf_lake' —— 将来多一个 lake 它自动跟着管。
    import ast as _ast
    _lakes = set()
    for _dp, _dn, _fn in os.walk(os.path.join(REPO, 'strategies')):
        for _f in _fn:
            if not _f.endswith('.py'):
                continue
            try:
                _t = _ast.parse(io_open_text(os.path.join(_dp, _f)))
            except SyntaxError:
                continue
            for _n in _t.body:
                if isinstance(_n, _ast.Assign) and any(
                        getattr(x, 'id', '') == 'DATALAKE' for x in _n.targets) \
                        and isinstance(_n.value, _ast.Constant):
                    _lakes.add(_n.value.value)
    assert _lakes, '构造不对：一个声明了 DATALAKE 的策略都没扫到 —— 这条判据空转'
    for _lk in sorted(_lakes):
        _d = os.path.join(_P.datalake(_lk if os.path.isabs(_lk)
                                      else os.path.join(_P.datalake(), _lk)),
                          'rt')
        assert not os.path.isdir(_d), (
            '策略 lake 底下出现了快照库：%s —— 快照库只有一个'
            '（datalake/rt/）。写进这里的东西【没人读】，而那几只票会'
            '永久买不进，且只写"停牌/无行情"不报错。' % _d)

    _stores = {_P.datalake(r) for _, r in _seen}
    assert len(_stores) == 1, (
        '补抓与读取指向了【不同的快照库】：%s —— 补抓会写进一个没人读的地方，'
        '而 missing() 的收敛让它只错一次就再也不重试，那几只票永久买不进'
        % sorted(_stores))

    return ('盘中 bar 与日终权威 %d 只逐位相同（open/因子/open_hfq/涨跌停判定）；'
            '%d 个收盘派生量是哨兵；除权守卫 3/3 跳过；'
            'ETF lake %d 只（量纲/舍入逐只自证）；'
            '只跑 OPEN、幂等、日终未到不冲正、篡改后冲正重录并收敛；'
            '补抓与读取同一个快照库（3 个调用）+ %d 个策略 lake 底下没有 rt/'
            % (len(rows), len(ob.CLOSE_DERIVED), _n_etf, len(_lakes)))


@case('记一笔：买是搜索下拉 / 卖只能从【那天的】持仓选且不超量（playwright）',
      tag='web')
def t_record_side_inputs():
    """🔴 用户 2026-09-22：「选择卖的时候，只能从当前持仓中选择，交易的数量
    不能超过持仓的数量。买的时候，输入代码、名称，出现下拉框供选择。」

    ★ 判据落在**可量的事实**上，不是"有没有那个 class"：
      买入那格必须是**搜索框**、卖出那格必须是**持仓下拉**，而且切换之后
      对方**不许还在**（只查"新的出现了"的话，两个叠着也算过）。
    🔴 「能卖多少」的判据是**成交日那天**的持仓 —— 照"今天"做会误伤补录。
      反向自证用的是账本里真实存在的那种票（在某天可卖、今天已清仓）。
    """
    import threading
    from http.server import ThreadingHTTPServer
    from playwright.sync_api import sync_playwright
    import assay.live as lv
    import assay.server as sv
    from assay.lv import pos as _pos

    import shutil
    import tempfile

    sv.ALLOW_LIVE = True
    sv._scan()
    aid = 'froec'
    # 🔴 **跑在临时账本上。** 这条用例会点「录一笔」去验"超量被拦住" ——
    #   万一哪天那道判据坏了（或做变异测试时），那一笔就**真的写进生产账本**
    #   了，而账本是 append-only 的、删不掉。
    #   （同「selftest 不许写生产数据」那条纪律。）
    _prev_live = lv.LIVE
    _tmp = tempfile.mkdtemp(prefix='rec_case_')
    shutil.copytree(_prev_live, os.path.join(_tmp, 'live'))
    lv.LIVE = os.path.join(_tmp, 'live')
    # ★ 先在服务端找一个**"那天有、今天没有"**的日子 —— 没有这个构造，
    #   「按成交日取」与「按今天取」两种实现给出同样的结果，判据空转。
    today_it = {x['code'] for x in
                _pos.sellable_asof(aid, lv.latest_data_day())['items']}
    back_day, gone = None, None
    for d in ('2026-09-01', '2026-09-02', '2026-09-08'):
        s0 = {x['code'] for x in _pos.sellable_asof(aid, d)['items']}
        ex = sorted(s0 - today_it)
        if ex:
            back_day, gone = d, ex[0]
            break
    assert back_day, \
        ('构造不对：找不到"那天可卖、今天已清仓"的票 —— 「按成交日取」这条'
         '判据会空转（两种实现给出同样的结果）')

    httpd = ThreadingHTTPServer(('127.0.0.1', 0), sv.Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    errs = []
    try:
        with sync_playwright() as p:
            b = p.chromium.launch()
            pg = b.new_page(viewport={'width': 1500, 'height': 950})
            pg.on('pageerror', lambda e: errs.append(str(e)[:200]))
            pg.goto('http://127.0.0.1:%d/#/live/%s' % (port, aid),
                    wait_until='domcontentloaded')
            pg.wait_for_function("() => document.querySelector('#lvrec')",
                                 timeout=30000)
            pg.click('#lvrec')
            pg.wait_for_selector('#fcb', timeout=20000)
            pg.wait_for_timeout(800)

            # ---- ① 默认是买：搜索框，且**没有**持仓下拉 ----
            assert pg.is_visible('#fcb .skq'), \
                '买入那格不是搜索框 —— 裸输入框要人记住代码，打成另一只真实'\
                '存在的票就是静默录错，而账本是 append-only 的'
            assert not pg.query_selector('#fsel'), \
                '买入时还挂着持仓下拉 —— 两种输入叠在一起'
            # ★ 「常规档」那几条要**排在 ③ 之前**：`#fd` 空掉的话，下面
            #   算期望值的那句 `sellable_asof(aid, '')` 会直接崩成
            #   `Invalid isoformat string`，**报错指不到原因**
            #   （变异「成交档也给自由日期框」第一轮就是这么一句）。
            d0 = pg.input_value('#fd')
            assert d0, '常规档的成交日是空的 —— 它该默认是今天'
            assert not pg.query_selector('#fdi'), \
                ('「成交」页签里还留着自由日期输入框 —— 手填日期最容易错，'
                 '而填错的后果是成本价与建仓日错，那两个值直接喂给止损判定'
                 '与红利税档位。要改日期必须显式切到「补录」')
            assert pg.is_visible('#rbulk'), \
                '批量粘贴不在「成交」里 —— 它的日期来自对账单、不是手填的'
            assert not pg.is_visible('#fbackhint'), \
                '常规档挂着补录的说明 —— 那是常驻噪声'

            # ---- ② 买入候选**不许有指数**（指数买不了）----
            pg.fill('#fcb .skq', '上证指数')
            pg.wait_for_timeout(900)
            cand = pg.eval_on_selector_all(
                '#fcb .skit b', 'es => es.map(e => e.textContent || "")')
            assert cand, '搜「上证指数」一条候选都没有 —— 下面那条判据会空转'
            assert not any(c.strip() == '上证指数' for c in cand), \
                ('买入候选里出现了**指数**（%s）—— 它买不了，而 '
                 '`normalize_code(\'sh000001\')` 会报"自相矛盾"，'
                 '那个报错指不到真正的原因' % cand[:3])
            # ★ 反向自证：这一搜**确实有结果**（都是 ETF）—— 否则"没有指数"
            #   可能只是因为一条都没搜到
            assert any('ETF' in c for c in cand), \
                '这一搜没搜到 ETF，"排除指数"这条判据是空转的：%s' % cand[:3]

            # 选一只真能买的，验证代码真的填进去了
            pg.fill('#fcb .skq', '中国石油')
            pg.wait_for_timeout(900)
            assert pg.query_selector_all('#fcb .skit'), '搜「中国石油」没有候选'
            pg.click('#fcb .skit >> nth=0')
            pg.wait_for_timeout(250)
            assert pg.input_value('#fc') == '601857.XSHG', \
                '选中之后代码没填进去（#fc=%r）—— 选了没反应是最难查的那种坏' \
                % pg.input_value('#fc')

            # ---- ③ 切到卖：持仓下拉，且搜索框**不许还在** ----
            pg.select_option('#fs', 'sell')
            # ★ 等不到就**说清是什么没出来** —— 裸 `wait_for_selector` 超时
            #   报的是一句 "Timeout 15000ms exceeded"，指不到原因
            #   （变异「卖也用搜索框」第一轮就是这么一句）。
            try:
                pg.wait_for_selector('#fsel', timeout=12000)
            except Exception:
                raise AssertionError(
                    '切到「卖」之后没有出现持仓下拉（#fsel）—— 卖出必须'
                    '只能从持仓里选；现在那一格是 %s'
                    % ('搜索框' if pg.query_selector('#fcb .skq') else '空的'))
            pg.wait_for_timeout(500)
            assert not pg.query_selector('#fcb .skq'), \
                '切到卖之后搜索框还在 —— 卖出只能从持仓里选'
            got = set(pg.eval_on_selector_all(
                '#fsel option', 'es => es.map(e => e.value).filter(Boolean)'))
            want = {x['code'] for x in
                    _pos.sellable_asof(aid, pg.input_value('#fd'))['items']}
            assert got == want, \
                ('卖出下拉与服务端的可卖清单对不上：只在页面 %s / 只在服务端 %s'
                 % (sorted(got - want)[:3], sorted(want - got)[:3]))

            # ---- ④ 「补录」是**单独一个页签**，不混在常规操作里 ----
            # 用户 2026-09-22：「补录功能可以开单独的按钮，和常规操作做区分，
            # 而不是混在正常的操作里」。
            # 🔴 判据落在**可量的事实**上：常规那档日期框**不存在**（锁成
            #   文本，见 ①）、补录那档**才有**；切回来日期要**复位**。
            #   只查"有没有那个页签"的话，把 `fdRender` 整个删掉照样全绿 ——
            #   那时两档都是自由输入框，等于没分开。
            pg.click('.rtab[data-t="back"]')
            # ★ 不裸 `wait_for_selector` —— 那报的是一句 "Timeout 10000ms
            #   exceeded"，**指不到原因**（同 ③ 那处）。
            pg.wait_for_timeout(600)
            assert pg.query_selector('#fdi'), \
                ('「补录」页签里没有日期输入框 —— 那这个页签就什么都改不了，'
                 '而补录的**全部意义**就是改成交日')
            assert pg.input_value('#fd') == '', \
                ('切到「补录」之后日期还带着 %r —— 不该给一个可能错的默认值'
                 '（同「拿不到分红那一格标『查不到』，不猜一个数填上去」）'
                 % pg.input_value('#fd'))
            assert pg.is_visible('#fbackhint'), '补录档没有说明它是干什么的'
            assert not pg.is_visible('#rbulk'), \
                '补录档里还有批量粘贴 —— 两件事该分开'

            pg.click('.rtab[data-t="fill"]')
            pg.wait_for_timeout(300)
            assert pg.input_value('#fd') == d0 and not pg.query_selector('#fdi'), \
                ('从补录切回「成交」之后日期没复位（现在 %r，该是 %r）—— '
                 '把补录填的那天带回日常录入，正是"混在一起"最坏的后果'
                 % (pg.input_value('#fd'), d0))

            # ---- ⑤ 改成交日要**重取** —— 判据是那只已清仓的票必须出现 ----
            # ★ 日期现在锁在「成交」页签里，改日期要走**补录** —— 那正是
            #   这一轮要分开的东西（用户："补录……和常规操作做区分"）。
            pg.click('.rtab[data-t="back"]')
            pg.wait_for_selector('#fdi', timeout=10000)
            pg.select_option('#fs', 'sell')
            pg.fill('#fdi', back_day)
            pg.dispatch_event('#fdi', 'change')
            try:
                pg.wait_for_function(
                    "([c]) => !!document.querySelector("
                    "'#fsel option[value=\\'' + c + '\\']')",
                    arg=[gone], timeout=12000)
            except Exception:
                raise AssertionError(
                    '把成交日改成 %s 之后，%s 没进下拉 —— 要么改日期没重取'
                    '（`#fd` 的 change 没接上），要么服务端给的是【今天】的'
                    '持仓而不是那天的。当前下拉里是：%s'
                    % (back_day, gone, sorted(set(pg.eval_on_selector_all(
                        '#fsel option',
                        'es => es.map(e => e.value).filter(Boolean)')))[:4]))
            back_got = set(pg.eval_on_selector_all(
                '#fsel option', 'es => es.map(e => e.value).filter(Boolean)'))
            assert gone in back_got, \
                ('把成交日改成 %s 之后，那天可卖的 %s 没出现在下拉里 —— '
                 '照"今天的持仓"做的话，补录历史卖出时那只票永远选不到，'
                 '而它不报错，只是"不在列表里"' % (back_day, gone))

            # ---- ⑥ 超量：**当场**拦住并说清，提交也拦 ----
            pg.select_option('#fsel', gone)
            pg.wait_for_timeout(300)
            mx = int(pg.get_attribute('#fq', 'max') or 0)
            assert mx > 0, '选中之后没给股数上限（max=%r）' % mx
            pg.fill('#fq', str(mx + 100))
            pg.wait_for_timeout(250)
            t1 = pg.inner_text('#fcmsg') or ''
            assert str(mx) in t1.replace(',', '') and '卖不了' in t1, \
                '填了超过上限的股数，没有当场说清可卖多少：%r' % t1[:80]
            # ★ 按钮**不许**是 disabled（项目纪律：disabled 的元素连 title
            #   都不触发，"点了没反应"是最难查的那种坏）—— 它照样可点，
            #   点了把原因说清楚。
            assert not pg.get_attribute('#fb', 'disabled'), \
                '「录一笔」被设成 disabled 了 —— 本项目一律不这么做'
            n_before = len(_pos.active_fills(lv.fills(aid)))
            pg.click('#fb')
            pg.wait_for_timeout(700)
            assert '超过' in (pg.inner_text('#fmsg') or ''), \
                '超量提交没有被拦住（提示：%r）' % (pg.inner_text('#fmsg') or '')[:60]
            assert len(_pos.active_fills(lv.fills(aid))) == n_before, \
                '🔴 超量那一笔真的写进账本了 —— 账本是 append-only 的'

            # ---- ⑦ 一只都没选就提交 -> 说清下一步 ----
            pg.select_option('#fsel', '')
            pg.fill('#fq', '100')
            pg.click('#fb')
            pg.wait_for_timeout(500)
            assert '选一只' in (pg.inner_text('#fmsg') or ''), \
                '没选票就提交，提示没说清下一步：%r' % (
                    pg.inner_text('#fmsg') or '')[:60]
            assert len(_pos.active_fills(lv.fills(aid))) == n_before, \
                '没选票却写进了账本'
            assert not errs, '页面报错：%s' % errs[:2]
            b.close()
    finally:
        httpd.shutdown()
        lv.LIVE = _prev_live
        shutil.rmtree(_tmp, ignore_errors=True)
    return ('买=搜索下拉（指数已排除、候选里有 ETF 自证）；卖=%d 只可卖'
            '与服务端逐只一致；补录是单独页签（成交档没有日期框、切回复位到 %s）；'
            '改成交日到 %s 后 %s 回到清单；'
            '超量与未选都当场拦住、账本 %d 笔未变'
            % (len(want), d0, back_day, gone[:6], n_before))


def num_ok(txt, v):
    """页面上的金额带千分位，所以两种写法都认（`1,482.00` / `1482.00`）。"""
    return format(v, ',.2f') in txt or ('%.2f' % v) in txt


@case('实盘页每个账户都要真的渲染出来（不许卡在"读取中"）', tag='web')
def t_live_pages_render():
    """🔴🔴 用户 2026-09-22：「貌似直接把 froea 实盘账户跑挂了，一直在读取中」。

    根因是我把 `lvDayParts` / `lvSoldToday` 写成**模块级函数**，而里面用的
    `sgn` / `col` 是 `kpiHtml` 里的 **const 局部** -> `sgn is not defined`。
    而它**只在控制台报**：异步回调里的异常不让页面报错，只让它少做一半，
    屏幕上就卡在「读取中…」（同 `money is not defined` 那次、同 `geo` 那次
    —— 本项目这是第三回）。

    ★ **`node --check` 抓不到它**（语法完全合法），所以那条守卫不够。
      这里钉两条，**分工别记反**：
        · `pageerror` 为空      —— 直接判据，但将来谁加个 try/catch 吞掉就瞎了
        · `#main` 里不许残留「读取中」 —— **用户可见的后果**，吞不掉
      第二条才是根本判据，第一条是让报错指得到原因。

    另外钉住当日盈亏那一格的形态（用户 2026-09-22 定）：
      · 副标题**只有一个百分比**，不带"总资产"三个字、不带构成
      · 构成进 `title`（hover 可见），差额的正式解释是表下那行「今天卖出」
    """
    import re
    import shutil
    import tempfile
    import threading
    from http.server import ThreadingHTTPServer

    from playwright.sync_api import sync_playwright

    from assay import live as lv
    from assay import server as sv

    real = lv.LIVE
    tmp = tempfile.mkdtemp(prefix='selftest_render_')
    shutil.copytree(real, os.path.join(tmp, 'live'), dirs_exist_ok=True)
    lv.LIVE = os.path.join(tmp, 'live')
    prev = sv.ALLOW_LIVE
    sv.ALLOW_LIVE = True
    sv._scan()
    try:
        accs = [a for a in lv.load_accounts() if not a.get('archived')]
        # 🔴 **构造出"当天有卖出"** —— 不构造的话"构成 / 今天卖出"那几条
        #   全是空转（真账本明天就没有今天的卖出了，同「断言不许依赖真实
        #   数据碰巧如此」）。
        # 🔴 日期要与 `positions_valued` 里那个 `d_now` **同源** ——
        #   盘中它是**实时那天**（不是面板最新日）。第一版按面板最新日记，
        #   于是那笔不算"今天"，已实现是 0、整段空转（构造自己的护栏抓到了）。
        hit = None
        for a in accs:
            pv = lv.positions_valued(a['id'])
            if pv.get('items'):
                it = pv['items'][0]
                if it.get('price') and it['shares'] >= 100:
                    d_now = max([x['rt_at'][:10] for x in pv['items']
                                 if x.get('rt_at')] or [pv['asof']])
                    hit = (a['id'], it['code'], it['price'], d_now)
                    break
        assert hit, '构造不对：没有一个账户有可卖的持仓'
        aid0, code0, px0, day = hit
        lv.add_fill(aid0, day, code0, 'sell', 100, price=px0, fee=3.0,
                    force_price=True)
        _pv0 = lv.positions_valued(aid0)
        assert _pv0.get('pnl_day_realized'), \
            ('构造没生效：当天卖出的已实现是 %s —— 下面那几条会空转'
             % _pv0.get('pnl_day_realized'))
        # 🔴🔴 已实现要**逐分对得上**，不能只判"非 0"（2026-09-24 实测）：
        #   今日卖出的基准原来写死取 `_last_px` 三元组的 `[0]`（**当天收盘**），
        #   而那一位只有"面板停在昨天"时才等于昨收。收盘后实时为空时它就
        #   走到了 —— 卖在收盘价上算出来正好是 **0**，**而它不报错**
        #   （正是用户 2026-09-22 要修的那个数）。
        #   ★ 期望从**面板独立算**，不拿 `positions_valued` 自己的输出当期望。
        # 🔴 `_prev_close` 的两支要**各构造一次** —— 盘中那一支（面板停在
        #   昨天）在收盘后的真实数据上**走不到**，光靠上面那条断言的话
        #   "一律给 preclose" 这种变异抓不到（实测就是无效变异）。
        import datetime as _dt3
        from assay.lv.perf import _prev_close as _pcf
        _tp = (15.43, _dt3.date(2026, 9, 23), 15.36)
        assert _pcf(_tp, '2026-09-24') == 15.43, \
            '盘中（面板停在昨天）：昨收就是面板那天的【收盘】'
        assert _pcf(_tp, '2026-09-23') == 15.36, \
            '收盘后（面板已含当天）：昨收要取那一行的【preclose】'
        assert _pcf(None, '2026-09-23') is None, '取不到就给 None，不猜'
        import duckdb as _dd
        from assay import paths as _pth
        _pcq = _dd.connect().execute(
            "SELECT close_bfq, preclose FROM %s WHERE jq_code = ? AND date = ?"
            % _pth.panel_sql(), [code0, day]).fetchone()
        if _pcq and _pcq[1]:
            _want = round(100 * (px0 - float(_pcq[1])), 2)
            assert abs(_pv0['pnl_day_realized'] - _want) < 0.02, \
                ('今日卖出的已实现基准不对：实得 %.2f，按【昨收 %.4f】'
                 '算应是 %.2f —— 多半是拿【当天收盘】当基准了'
                 % (_pv0['pnl_day_realized'], float(_pcq[1]), _want))

        httpd = ThreadingHTTPServer(('127.0.0.1', 0), sv.Handler)
        port = httpd.server_address[1]
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            with sync_playwright() as p:
                try:
                    br = p.chromium.launch()
                except Exception as e:                      # noqa: BLE001
                    return '跳过（浏览器不可用: %s）' % type(e).__name__
                pg = br.new_page(viewport={'width': 1500, 'height': 950})
                errs = []
                pg.on('pageerror', lambda e: errs.append(e.stack or str(e)))
                n_ok = 0
                for a in accs:
                    errs.clear()
                    pg.goto('http://127.0.0.1:%d/#/live/%s' % (port, a['id']),
                            wait_until='domcontentloaded')
                    # 🔴 **先等内容出来，再等"读取中"消失** —— 只判后者的话
                    #   页面还空着时就满足了（空白里当然没有"读取中"），
                    #   于是这条判据在真卡住的页面上也是绿的。实测踩到。
                    try:
                        pg.wait_for_function(
                            "() => { const k = document.querySelector('#lvkpi');"
                            " const m = document.querySelector('#main');"
                            " return k && k.textContent.trim().length > 0 && m"
                            " && m.textContent.indexOf('读取中') < 0; }",
                            timeout=25000)
                    except Exception:
                        # 🔴 **超时要把现场带出来** —— 裸 Timeout 报的是
                        #   "Timeout 25000ms exceeded"，而真正的原因是控制台
                        #   里那条异常（变异实测：去掉 `_lvsg` 就是这样）。
                        raise AssertionError(
                            '账户「%s」没渲染出来（卡在「读取中」或空白）。'
                            '控制台报错：%s'
                            % (a.get('name'), (errs[0][:200] if errs else '无')))
                    pg.wait_for_timeout(600)
                    assert not errs, \
                        ('账户「%s」页面报错（页面不会自己说，只会少渲染一半）：%s'
                         % (a.get('name'), errs[0][:200]))
                    t = pg.inner_text('#main')
                    assert '读取中' not in t, \
                        ('账户「%s」卡在「读取中」—— 多半是某个回调抛了异常，'
                         '而异常只在控制台里' % a.get('name'))
                    n_ok += 1
                # ---- 当日盈亏那一格的形态 ----
                pg.goto('http://127.0.0.1:%d/#/live/%s' % (port, aid0),
                        wait_until='domcontentloaded')
                pg.wait_for_selector('#lvkpi', timeout=25000)
                pg.wait_for_timeout(800)
                g = pg.evaluate(
                    "() => [...document.querySelectorAll('#lvkpi div')]"
                    ".filter(e => e.querySelector(':scope > .k') &&"
                    " e.querySelector(':scope > .k').textContent.indexOf"
                    "('当日盈亏') === 0)"
                    ".map(e => ({title: e.getAttribute('title'),"
                    " sub: (e.querySelector(':scope > .s')||{}).textContent||''}))")
                assert len(g) == 1, '当日盈亏那格找不到或有多个：%s' % g
                sub = (g[0]['sub'] or '').strip()
                assert re.fullmatch(r'[+-]?\d+\.\d\d%', sub), \
                    ('当日盈亏的副标题该【只有一个百分比】，实得 %r —— '
                     '用户明确要求不带"总资产"三个字、也不要构成' % sub)
                for w in ('持仓', '已实现', '费用'):
                    assert w in (g[0]['title'] or ''), \
                        '构成没进 title（%s 缺失）：%r' % (w, g[0]['title'])
                assert '今天卖出' in pg.inner_text('#main'), \
                    ('持仓表下面没有「今天卖出」那一行 —— 当日盈亏的合计含'
                     '已实现，必然不等于持仓表各行之和，不列出来就是个'
                     '对不上的数')
                # ---- 公司行动要【留痕】（用户 2026-09-23）----
                #   🔴 判据落在**服务端真的给了事件**的那个账户上，而且
                #     两头都钉：有事件的必须显示、事件里那几只必须点得出
                #     名字与金额。只查"有没有那几个字"的话，事件为空时
                #     渲染一句空话照样命中（判据比要证的事宽）。
                cid = None
                for a in accs:
                    ev = (lv.positions_valued(a['id']).get('corp') or {}).get('events')
                    if ev:
                        cid = (a['id'], ev)
                        break
                assert cid, ('构造不对：没有一个账户发生过公司行动 —— '
                             '那"留痕"这条在页面上是空转的')
                pg.goto('http://127.0.0.1:%d/#/live/%s' % (port, cid[0]),
                        wait_until='domcontentloaded')
                pg.wait_for_selector('#lvkpi', timeout=25000)
                pg.wait_for_timeout(900)
                mt = pg.inner_text('#main')
                # 🔴 **主视图那里只许有一行摘要**（2026-09-24 改）。v1 把逐条
                #   横排铺开，用户指出「行动项很多的话可能会排列有点拥挤」。
                #   判据因此是**可量的事实**：那一块的行数与字数都有上界，
                #   而"公司行动"这四个字仍必须在（藏起来同样不行）。
                assert '公司行动' in mt, \
                    ('持仓表下面没有「公司行动」那一行 —— 成本与股数被调过了'
                     '却不说为什么，人只能猜（而猜正是本项目反复吃亏的地方）')
                _sum = pg.evaluate(
                    "() => {const a = [...document.querySelectorAll('#lvbody .lvwhy')]"
                    ".filter(e => e.textContent.indexOf('公司行动') === 0);"
                    " return a.length ? {t: a[0].innerText,"
                    "   href: (a[0].querySelector('a')||{}).getAttribute"
                    "     ? a[0].querySelector('a').getAttribute('href') : null} : null}")
                assert _sum, '找不到那行摘要'
                assert '\n' not in _sum['t'].strip(), \
                    '摘要不许换行（逐条铺开了？）：%r' % _sum['t']
                assert len(_sum['t']) < 120, \
                    ('摘要太长（%d 字）—— 逐条铺开的话行动项一多就绕成几排：%r'
                     % (len(_sum['t']), _sum['t']))
                # 逐条的日期**不许**出现在主视图（那是明细页的事）
                for e in cid[1]:
                    assert e['date'][5:] not in _sum['t'], \
                        '摘要里铺开了逐条事件（%s）' % e['date']
                # 🔴 但**必须给得出入口** —— 只收起不给入口就是把明细藏起来
                assert _sum['href'] and 'tab=corp' in _sum['href'], \
                    '摘要那行没有去明细的入口：%r' % _sum['href']
                # 汇总的现金要在（那 3,560 元在屏幕上得有出处）
                _cash = sum(e['cash'] or 0 for e in cid[1])
                if _cash:
                    assert num_ok(_sum['t'], _cash), \
                        '摘要没写出一共派回多少钱（%.2f）：%r' % (_cash, _sum['t'])
                # 持仓行上的 ⊙：hover 给**这只票**的
                _held = {x['code'] for x in
                         lv.positions_valued(cid[0])['items']}
                _mk = pg.evaluate(
                    "() => [...document.querySelectorAll('.lvpos .cmark')]"
                    ".map(e => e.getAttribute('title'))")
                _want = len({e['code'] for e in cid[1]} & _held)
                assert len(_mk) == _want, \
                    ('持仓行的 ⊙ 个数不对：%d 个，而持仓里发生过公司行动的有 %d 只'
                     % (len(_mk), _want))
                if _mk:
                    assert any(c in ''.join(_mk) for c in ('派现', '送转', '配股')), \
                        '⊙ 的 title 里没写发生了什么：%r' % _mk[:1]
                assert '**' not in mt, '页面文案里有 markdown 星号（HTML 渲染不了）'
                n_corp = len(cid[1])
        finally:
            httpd.shutdown()
    finally:
        lv.LIVE = real
        sv.ALLOW_LIVE = prev
        sv._scan()
        shutil.rmtree(tmp, ignore_errors=True)
    return ('%d 个账户逐个渲染、0 个 JS 报错、无「读取中」残留；'
            '当日盈亏副标题只有一个百分比（%s）、构成在 title 里、'
            '表下有「今天卖出」；公司行动 %d 条收成一行摘要（带入口、带合计、'
            '不铺开）+ 持仓行 ⊙'
            % (n_ok, sub, n_corp))


@case('信号重算的提示：分清「你已成交」与「清单真变了」（playwright）', tag='web')
def t_signal_revision_notice():
    """🔴🔴 用户 2026-09-22：「09-21T23:00 → 被 09-22T16:06 覆盖是这么意思？
    这个数据还是基于 09-21 的收盘数据计算出来的吗？」

    查下来两版的 `data_asof` **都是 2026-09-21**（当天 16:16 那轮同步重建了
    面板文件但 09-22 行情还没出来，指纹一变 tick 就重算），所以"覆盖"没错 ——
    **错在页面只给了两个时间戳**，而判断"这份还能不能用"靠的是数据日。

    更要命的是第二条：那一版的「买 301062 / 卖 300980」之所以消失，
    **不是决策变了，是他当天 10:11/10:12 已经照着成交了**。而提示写的是
    「请照现在这份核对」—— 而现在这份是**空的**，照它核对会读成
    "不该买不该卖"，正好相反。

    ★ 判据**两个方向都要**，只测一头的话"永远低调"或"永远显红"都全绿：
        A 消失的那几只当天都已成交  -> **低调样式** + 说"你已成交"
        B 有一只没成交（真的被改掉） -> **警告样式** + "请照现在这份核对"
    🔴 两种情形都要**构造**：真账本明天就没有今天那几笔成交了
      （同「断言不许依赖真实数据碰巧如此」）。
    """
    import copy
    import json as _j
    import shutil
    import tempfile
    import threading
    from http.server import ThreadingHTTPServer

    from playwright.sync_api import sync_playwright

    from assay import live as lv
    from assay import server as sv
    from assay.lv import sig as _sg

    real = lv.LIVE
    tmp = tempfile.mkdtemp(prefix='selftest_rev_')
    shutil.copytree(real, os.path.join(tmp, 'live'), dirs_exist_ok=True)
    lv.LIVE = os.path.join(tmp, 'live')
    prev = sv.ALLOW_LIVE
    sv.ALLOW_LIVE = True
    sv._scan()
    try:
        aid = 'froec'
        base_sig = lv.latest_signal(aid)
        assert base_sig and base_sig.get('for_date'), \
            '构造不对：%s 没有信号文件' % aid
        pv = lv.positions_valued(aid)
        assert len(pv.get('items') or []) >= 2, '构造不对：持仓不足 2 只'
        sold, kept = pv['items'][0], pv['items'][1]
        # 🔴 必须用【最新那份信号的 for_date】，不是面板最新日 ——
        #   `latest_signal` 取 `sorted(signals/*.json)[-1]`，写一个更早的日期
        #   等于**根本没被读到**，而那时页面显示的是真账本那份。
        #   实测：A 档因此"通过"了（真账本恰好就是 all_done），
        #   是 B 档把它抓出来的（同「断言不许依赖真实数据碰巧如此」）。
        day = str(base_sig['for_date'])[:10]
        # 构造两笔当天的成交：卖 sold、买一只新的
        lv.add_fill(aid, day, sold['code'], 'sell', 100,
                    price=sold['price'], fee=3.0, force_price=True)
        newc = '000001.XSHE'
        lv.add_fill(aid, day, newc, 'buy', 100, price=10.0, fee=3.0,
                    force_price=True)

        def _write(removed_sell, removed_buy):
            s = copy.deepcopy(base_sig)
            s['for_date'] = day
            s['buy'], s['sell'], s['keep'] = [], [], []
            s['revisions'] = [{
                'rev': 1, 'built_at': day + 'T23:00:00',
                'replaced_at': day + 'T16:06:00',
                'data_asof': s.get('data_asof'),
                'new_data_asof': s.get('data_asof'),
                'archived': 'signals/_rev/%s.rev1.json' % day,
                'diff': {'changed': True, 'buy_added': [], 'sell_added': [],
                         'buy_removed': removed_buy,
                         'sell_removed': removed_sell,
                         'hold_added': [], 'hold_removed': []}}]
            p = _sg.signal_path(aid, day)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, 'w', encoding='utf-8') as f:
                f.write(_j.dumps(s, ensure_ascii=False, indent=1, sort_keys=True))

        httpd = ThreadingHTTPServer(('127.0.0.1', 0), sv.Handler)
        port = httpd.server_address[1]
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        got = {}
        try:
            with sync_playwright() as p:
                try:
                    br = p.chromium.launch()
                except Exception as e:                      # noqa: BLE001
                    return '跳过（浏览器不可用: %s）' % type(e).__name__
                pg = br.new_page(viewport={'width': 1500, 'height': 950})
                errs = []
                pg.on('pageerror', lambda e: errs.append(e.stack or str(e)))

                def _look(tag):
                    # 🔴 同一个 hash goto 不重新加载 —— 必须 reload
                    #   （本项目踩过四次）
                    pg.goto('http://127.0.0.1:%d/#/live/%s' % (port, aid),
                            wait_until='domcontentloaded')
                    pg.reload()
                    pg.wait_for_function(
                        "() => { const m = document.querySelector('#main');"
                        " return m && m.textContent.indexOf('重算过') >= 0; }",
                        timeout=25000)
                    r = pg.evaluate(
                        "() => { const q = c => [...document.querySelectorAll(c)]"
                        ".filter(e => e.textContent.indexOf('重算过') >= 0);"
                        " const w = q('.lvwarn'), s = q('.lvwhy');"
                        " return {warn: w.length, quiet: s.length,"
                        "  txt: (w[0]||s[0]||{}).innerText || ''}; }")
                    assert not errs, '页面报错：%s' % errs[0][:200]
                    got[tag] = r
                    return r

                # ---- A 全都已成交 -> 低调 ----
                _write([sold['code']], [newc])
                a = _look('done')
                assert a['quiet'] == 1 and a['warn'] == 0, \
                    ('消失的那几只当天都已成交，不该显红（warn=%d quiet=%d）：%s'
                     % (a['warn'], a['quiet'], a['txt'][:160]))
                assert '你当天已经成交了' in a['txt'], \
                    '没说清"是你已经成交了"：%s' % a['txt'][:200]
                assert '数据日' in a['txt'], \
                    ('提示里没有【数据日】—— 用户问的就是"这份基于哪天的数据"，'
                     '只给时间戳答不了：%s' % a['txt'][:200])

                # ---- B 有一只没成交 -> 显红 ----
                _write([sold['code'], kept['code']], [newc])
                bb = _look('changed')
                assert bb['warn'] == 1 and bb['quiet'] == 0, \
                    ('有一只不是你成交的（清单真被改了），必须显红'
                     '（warn=%d quiet=%d）：%s'
                     % (bb['warn'], bb['quiet'], bb['txt'][:160]))
                assert '请照现在这份核对' in bb['txt'], \
                    '真变了却没让人去核对：%s' % bb['txt'][:200]
                assert kept['code'] in bb['txt'] and '你已成交' in bb['txt'], \
                    ('要把"已成交的"与"真被改的"分开点名：%s' % bb['txt'][:250])
                br.close()
        finally:
            httpd.shutdown()
    finally:
        lv.LIVE = real
        sv.ALLOW_LIVE = prev
        sv._scan()
        shutil.rmtree(tmp, ignore_errors=True)
    return ('已成交那档走低调样式并写明数据日；有一只没成交时显红并点名'
            '（%s 是真被改的）' % kept['code'][:6])


@case('执行差异要比【下单时那一版】，且取整不许吃掉容差', tag='fast')
def t_exec_diff_as_of():
    """🔴🔴 用户 2026-09-22：「2026-09-15、2026-09-22 这两期我都是按提示
    买入、卖出的，为什么现在都显示是提示外买入、卖出」。

    两个独立缺陷叠在一起，**都不报错**：

    ① 比的是**主文件**，而主文件是【执行完之后重算】出来的 —— 照着做完
       策略当然说"无事可做"，清单变空，于是**照做的每一笔都被判成"提示外"**。
       实测两期都精确对得上当时那一版（09-15 rev2 / 09-22 rev1）。
       改法：`sig.signal_as_of` 取**开盘前最后一版**（信号本来就是给次日开盘
       用的，人照着下单时手上就是那份；开盘后重算的那些没机会被执行）。

    ② 判据**先把归一化后的期望值向下取整到手、再比容差** ——
       `2600 x 0.843 = 2191.5 -> 2100`，丢掉 4.2%；而 TOL 的注释写的正是
       "一手 100 股 + 归一化后的凑整误差"，先取整再比等于把这份误差
       从容差里**扣掉一遍**。实测：实际 2300 对未取整的 2191.5 只差 4.95%
       （容差内 = 照做了），对取整后的 2100 却是 9.5% -> 判成 `over`。
       而且向下取整是**单向**偏差，系统性地更容易报"超量"。

    ★ 两条都要**反向自证**：拿主文件去比必须不 clean、按取整值比必须判 over
      —— 否则判据分不出"修好了"和"本来就这样"。
    """
    import copy
    import json as _j
    import shutil
    import tempfile

    from assay import live as lv
    from assay.lv import bench as _b
    from assay.lv import sig as _sg

    real = lv.LIVE
    tmp = tempfile.mkdtemp(prefix='selftest_xd_')
    shutil.copytree(real, os.path.join(tmp, 'live'), dirs_exist_ok=True)
    lv.LIVE = os.path.join(tmp, 'live')
    try:
        # 🔴 **现找一个有 revision 的日子**，不要拿"最新那份"——
        #   最新那份常常是明天的信号、一版都没被覆盖过，
        #   于是这条用例静默变成空转（实测第一版就是这样）。
        import glob as _g
        aid = 'froec'
        cur = day = None
        for _f in sorted(_g.glob(os.path.join(
                lv.LIVE, aid, 'signals', '*.json')), reverse=True):
            _o = _j.load(open(_f, encoding='utf-8'))
            if _o.get('revisions'):
                cur, day = _o, _o['for_date']
                break
        assert cur, '构造不对：%s 没有任何被覆盖过的信号' % aid
        # ---- ① 照【下单时那一版】比 ----
        old, used = _sg.signal_as_of(aid, day)
        assert used and used.get('rev'), \
            ('没挑到归档的那一版（used=%s）—— 主文件建于 %s，'
             'rev 建于更早才对' % (used, cur.get('built_at')))
        assert used['built_at'] < day + 'T09:30', \
            '挑的那版建于开盘之后（%s），它没机会被执行' % used['built_at']
        d1 = _b.diff_one(aid, day)
        assert d1.get('clean'), \
            ('照着当时那一版做的，不该有差异：%s'
             % [(r['code'], r['kind']) for r in d1['rows'] if r['kind'] != 'ok'])
        # 反向自证：拿【主文件】去比必须不 clean，否则上面那条什么都没证
        d0 = _b.diff_one(aid, day, sig=cur)
        assert not d0.get('clean'), \
            ('构造没意义：拿"现在这份"去比也是 clean 的 —— 那说明主文件与'
             '那一版一样，这条判据分不出改没改')
        # ---- ② 取整不许吃掉容差 ----
        #   构造：把当时那一版的股数改成一个"归一化后刚好卡在整手边界"的值
        b0 = [x for x in (old.get('buy') or [])]
        assert b0, '构造不对：那一版没有买入'
        got = _b._fill_map(aid, day).get(
            lv.normalize_code(b0[0]['code']), {})
        bs = got.get('buy_shares')
        TOL_T = 0.08          # 与 bench.diff_one 里那个 TOL 同一个数
        assert bs, '构造不对：账本里没有那一只的买入'
        # 🔴 构造要**直接控制 `amount`**：`scale = 实际买入额 / Σamount`，
        #   而 `want_cmp = shares x scale`。第一版把 amount 写成
        #   `shares x ref_price`，于是 shares 一改 scale 跟着变、
        #   **期望值被抵消回原处**（W 与 shares 无关），变异 ③ 因此漏过。
        #   现在反解：要让 W 落在"未取整算在容差内、取整后越线"的那个带里。
        got_amt = float(got.get('buy_amt') or 0)
        assert got_amt > 0, '构造不对：那一天没有买入金额'
        lo_w = bs / (1 + TOL_T)              # 未取整仍在容差内的下界
        hi_w = (int(lo_w / 100) + 1) * 100   # 取整后会掉到更低一档
        W = (lo_w + hi_w) / 2.0
        assert abs(bs - W) / W <= TOL_T and \
            abs(bs - int(W / 100) * 100) / (int(W / 100) * 100) > TOL_T, \
            ('构造不成立：W=%.1f 分不出"取整前后"（bs=%s）' % (W, bs))
        s2 = copy.deepcopy(old)
        S = 10000
        s2['buy'] = [dict(b0[0], shares=S, amount=S * got_amt / W)]
        s2['sell'] = []
        d2 = _b.diff_one(aid, day, sig=s2)
        kinds = {r['code']: r['kind'] for r in d2['rows']}
        k0 = kinds.get(lv.normalize_code(b0[0]['code']))
        assert k0 == 'ok', \
            ('实际 %s 股 vs 期望 %.1f 股（差 %.1f%%，在 %.0f%% 容差内）却判成 %r'
             ' —— 多半是又拿【取整后 %d】的值去比了（那是 %.1f%%）'
             % (bs, W, abs(bs - W) / W * 100, TOL_T * 100, k0,
                int(W / 100) * 100,
                abs(bs - int(W / 100) * 100) / (int(W / 100) * 100) * 100))
        # 反向自证：真的超量时必须判出来
        s3 = copy.deepcopy(s2)
        s3['buy'] = [dict(b0[0], shares=int(bs * 1.5))]
        d3 = _b.diff_one(aid, day, sig=s3)
        k3 = {r['code']: r['kind'] for r in d3['rows']}.get(
            lv.normalize_code(b0[0]['code']))
        assert k3 in ('short', 'over'), \
            '期望差 50%% 却还判 %r —— 容差成了摆设' % k3
    finally:
        lv.LIVE = real
        shutil.rmtree(tmp, ignore_errors=True)
    return ('%s 照 rev%s（建于 %s）比 -> clean，拿主文件比 -> 不 clean；'
            '容差按未取整值算（差 4.5%% 判 ok、差 50%% 判 %s）'
            % (day, used['rev'], used['built_at'][5:16], k3))


@case('首页只列真实盘 · 当日盈亏 · 手工账户是一等状态（playwright）', tag='web')
def t_home_real_only_and_manual():
    """用户 2026-09-19 两条：

      ①「首页只需要展示真正的实盘，模拟盘不展示，并且能把当日涨跌金额、
        幅度展示出来，替代掉持仓的浮盈」
      ②「策略账户增加一种非策略账户，无需绑定策略」

    ★ ② 的做法是**不加字段**：`code_sha256` 的有无已经是这个状态的唯一真值，
      加一个 `no_strategy` 就是第二份状态，而"标了却绑了策略"之类的组合
      迟早出现且分叉不报错。要改的是**怎么说它** —— 原来未绑策略的账户
      待办区显示「还没有信号 —— 点「立即重算」」加一个按钮，
      而点下去必然报"账户还没绑定策略"：**指向一条走不通的路**。

    判据都落在**可量的事实**上：
      · 首页列出的账户集合 == 服务端说的非模拟盘账户（不是"少了两行"）
      · 表头是「当日盈亏」且那一列的值等于服务端的 `pos.pnl_day`
      · 手工账户页上没有"还没有信号"那句，而有「手工账户」与绑定入口
    """
    import shutil
    import tempfile
    import threading
    from http.server import ThreadingHTTPServer

    from playwright.sync_api import sync_playwright

    from assay import live as lv
    from assay import server as sv

    real = lv.LIVE
    tmp = tempfile.mkdtemp(prefix='selftest_home_')
    shutil.copytree(real, os.path.join(tmp, 'live'), dirs_exist_ok=True)
    lv.LIVE = os.path.join(tmp, 'live')
    prev = sv.ALLOW_LIVE
    sv.ALLOW_LIVE = True
    sv._scan()
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), sv.Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        accs = [a for a in lv.load_accounts() if not a.get('archived')]
        want = sorted(a['name'] for a in accs if not lv.is_paper(a))
        papr = [a for a in accs if lv.is_paper(a)]
        assert want and papr, \
            '构造不对：要同时有真实盘与模拟盘才测得到（真 %d / 模拟 %d）' \
            % (len(want), len(papr))
        manual = next((a for a in accs
                       if not a.get('code_sha256') and not lv.is_paper(a)), None)
        assert manual, '构造不对：没有"未绑策略的实盘账户"'

        with sync_playwright() as p:
            try:
                br = p.chromium.launch()
            except Exception as e:                          # noqa: BLE001
                return '跳过（浏览器不可用: %s）' % type(e).__name__
            pg = br.new_page(viewport={'width': 1500, 'height': 950})
            errs = []
            pg.on('pageerror', lambda e: errs.append(e.stack or str(e)))
            base = 'http://127.0.0.1:%d/' % port

            # ---- ① 首页 ----
            pg.goto(base + '#/', wait_until='networkidle')
            pg.wait_for_selector('#main table.pkt', timeout=30000)
            pg.wait_for_timeout(1200)
            hdr = pg.eval_on_selector_all(
                '#main table.pkt tr:first-child th',
                'es => es.map(e => e.innerText.trim())')
            assert '当日盈亏' in hdr, '首页表头没换成「当日盈亏」：%s' % hdr
            assert '持仓浮盈' not in hdr, \
                '「持仓浮盈」还在 —— 用户要的是替代掉它：%s' % hdr
            got = sorted(pg.eval_on_selector_all(
                "#main table.pkt a[href^='#/live/']",
                'es => es.map(e => e.innerText.trim())'))
            assert got == want, \
                ('首页列的账户与"非模拟盘"对不上\n  页面 %s\n  该有 %s'
                 % (got, want))
            for a in papr:
                assert a['name'] not in got, \
                    '模拟盘 %s 出现在首页 —— 它是推演，不是今天要动手的事' % a['name']
            # 那一列的数必须等于服务端的 pnl_day（不是随手填了个数）
            _one = next(a for a in accs if not lv.is_paper(a)
                        and (lv.positions_valued(a['id']).get('pnl_day')
                             is not None))
            _pd = lv.positions_valued(_one['id'])['pnl_day']
            _txt = pg.inner_text('#main table.pkt')
            assert ('%.2f' % abs(_pd)).replace('.00', '') [:6] in _txt.replace(',', '') \
                or ('%.2f' % _pd) in _txt.replace(',', ''), \
                '首页那一列不是服务端的 pnl_day（%.2f）' % _pd

            # ---- ② 手工账户 ----
            pg.goto(base + '#/live/' + manual['id'], wait_until='networkidle')
            pg.wait_for_function(
                'n => { const h = document.querySelector("#main .lvhead h2");'
                '       return h && h.textContent.trim() === n; }',
                arg=manual['name'], timeout=30000)
            t = pg.inner_text('#main')
            assert '手工账户' in t, \
                '未绑策略的账户没被当成一等状态（还在催你绑）：%r' % t[:160]
            assert '还没有信号' not in t, \
                ('手工账户还显示「还没有信号 —— 点「立即重算」」，'
                 '而点下去必然报"没绑定策略" —— 指向一条走不通的路')
            assert pg.query_selector('#lvbind2') is not None, \
                '手工账户里没有「绑定策略」入口 —— 想绑的人就走不通了'
            assert pg.query_selector('#lvtick') is None, \
                '手工账户不该有「立即重算」—— 它点了必然报错'
            assert not errs, '页面抛了异常：%s' % errs[:1]
            br.close()
    finally:
        httpd.shutdown()
        lv.LIVE = real
        sv.ALLOW_LIVE = prev
        shutil.rmtree(tmp, ignore_errors=True)
    return ('首页只列 %d 个真实盘（%d 个模拟盘不列）、表头是当日盈亏；'
            '手工账户不再催你绑策略且入口还在' % (len(want), len(papr)))


@case('交易统计：分页无关 + 榜只列真的', 'fast')
def t_trade_stats():
    """`/api/live/trade_stats` —— 笔数 / 胜率 / 盈亏比 / 盈亏榜。

    用户 2026-09-22：「综合面板中的信息相对回测还是少了点，比如交易笔数、
    胜率、盈亏比、盈利前十（收益率）、亏损前十（收益率）」。

    🔴 判据三条，各对应一种**不报错**的坏法：

      ① **统计必须与分页无关**：`round_trips` 默认只给 100 条，照它算胜率
         会随翻页变。判据是 `n == 全量行数`，而**这条必须构造 >100 笔往返
         才测得到** —— 真账本只有 2 笔，写死一个小账户等于空转
         （同「断言要在能触发的构造上跑」）。
      ② **榜只列真的**：往返少的时候「盈利前十」里会混进反号的那些
         （实测 froec 只有 2 笔时就列出一笔 −0.21%）。判据两头钉：
         best 全 > 0、worst 全 < 0，**并反向自证两种都真的存在**。
      ③ **一笔亏损都没有时盈亏比给 None**：给 ∞ 或一个很大的数会看着像
         "这个策略很厉害"（同「拿不到分红那一格标查不到，不猜一个数」）。

    ★ 极值用**另一条独立的路**取（`round_trips(limit=10**9)` 全量自己排序），
      不拿 `trade_stats` 自己的输出当期望（判据要独立）。
    ★ 全程跑在**临时 `lv.LIVE`** 上，真账本一个字节不动。
    """
    from assay import live as lv
    real = lv.LIVE
    tmp = os.path.join(REPO, '_tmp_tstats')
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(os.path.join(tmp, 'a'))
    try:
        lv.LIVE = tmp
        with io.open(os.path.join(tmp, 'accounts.json'), 'w', encoding='utf-8') as f:
            json.dump([{'id': 'a', 'name': 'T', 'init_cash': 1e7,
                        'created': '2020-01-02T09:00:00'}], f)
        # ---- 构造 N 笔往返：收益率**逐笔不同**，且正负都有 ----
        # 🔴 N 必须 > round_trips 的默认 limit（100），否则判据 ① 空转。
        n = 130
        rows = []
        for i in range(n):
            code = '%06d.XSHE' % (300000 + i)
            buy = 10.0
            # 一半赚一半亏，幅度各不相同 -> 极值唯一，榜才验得出来
            sell = buy * (1.0 + (0.01 * (i + 1) if i % 2 == 0 else -0.01 * (i + 1)))
            for side, px, d in (('buy', buy, '2020-01-02'),
                                ('sell', round(sell, 4), '2020-02-03')):
                rows.append({'code': code, 'name': '', 'side': side,
                             'shares': 100, 'price': px, 'fee': 0.0,
                             'trade_date': d, 'ts': d + 'T10:00:00',
                             'uid': '%s%04d' % (side[0], i), 'source': 'manual',
                             'note': ''})
        with io.open(os.path.join(tmp, 'a', 'fills.jsonl'), 'w', encoding='utf-8') as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + '\n')

        full = lv.round_trips('a', offset=0, limit=10 ** 9)['rows']
        page = lv.round_trips('a')                      # 默认分页
        st = lv.trade_stats('a', top=10)
        assert len(full) == n and page['total'] == n, \
            '构造不对：应该有 %d 笔往返，实际全量 %d / total %d' % (
                n, len(full), page['total'])
        assert len(page['rows']) < n, \
            '构造不对：默认分页应该只给一页（否则判据 ① 空转）'

        # ---- ① 分页无关 ----
        assert st['n'] == n, ('统计要用**全量**往返，不是当前这一页 —— '
                              '照页面手上那一页算，胜率会随翻页变而不报错。'
                              '实际 n=%s，全量 %d' % (st['n'], n))

        # ---- ② 榜只列真的 + 极值对得上（独立算一遍） ----
        rets = sorted([r['ret'] for r in full if r.get('ret') is not None])
        assert rets[0] < 0 < rets[-1], '构造不对：要同时有赚的和亏的'
        assert st['best'] and st['worst'], '两张榜都不该是空的'
        assert all((r['ret'] or 0) > 0 for r in st['best']), \
            '盈利榜里混进了不赚钱的 —— 列不满十条是事实，摆一个名不副实的榜不是'
        assert all((r['ret'] or 0) < 0 for r in st['worst']), '亏损榜里混进了赚钱的'
        assert abs(st['best'][0]['ret'] - rets[-1]) < 1e-9, \
            '盈利榜第一名不是全量最大：%s vs %s' % (st['best'][0]['ret'], rets[-1])
        assert abs(st['worst'][0]['ret'] - rets[0]) < 1e-9, \
            '亏损榜第一名不是全量最小：%s vs %s' % (st['worst'][0]['ret'], rets[0])
        assert len(st['best']) == 10 and len(st['worst']) == 10, \
            'top=10 没生效：%d / %d' % (len(st['best']), len(st['worst']))

        # ---- ③ 没有亏损时盈亏比 = None ----
        assert st['profit_factor'] is not None, '构造里有亏损，盈亏比不该是 None'
        os.makedirs(os.path.join(tmp, 'b'))
        with io.open(os.path.join(tmp, 'accounts.json'), 'w', encoding='utf-8') as f:
            json.dump([{'id': 'b', 'name': 'T2', 'init_cash': 1e7,
                        'created': '2020-01-02T09:00:00'}], f)
        win_only = [r for r in rows
                    if r['side'] == 'buy' or float(r['price']) > 10.0]
        keep = set(r['code'] for r in win_only if r['side'] == 'sell')
        with io.open(os.path.join(tmp, 'b', 'fills.jsonl'), 'w', encoding='utf-8') as f:
            for r in rows:
                if r['code'] in keep:
                    f.write(json.dumps(r, ensure_ascii=False) + '\n')
        st2 = lv.trade_stats('b')
        assert st2['n'] and st2['avg_loss'] is None, '构造不对：b 该是全赚的'
        assert st2['profit_factor'] is None, \
            ('一笔亏损都没有时盈亏比要给 None（页面显示「—」）—— '
             '给 ∞ 或一个很大的数会看着像"这个策略很厉害"。实际 %s'
             % st2['profit_factor'])
        assert not st2['worst'], '全赚的账户不该有亏损榜'
    finally:
        lv.LIVE = real
        shutil.rmtree(tmp, ignore_errors=True)
    return ('%d 笔往返：统计与分页无关（默认页只有 %d 条）、'
            '两张榜各 10 条且极值对得上、全赚时盈亏比为 None'
            % (n, len(page['rows'])))


@case('业绩页：执行差异带方向 + 交易统计四格与盈亏榜（playwright）', 'web')
def t_perf_trade_info():
    """用户 2026-09-22 的两条：

      「执行差异中，比对操作时没有买卖方向，需要加上」
      「综合面板中的信息相对回测还是少了点，比如交易笔数、胜率、盈亏比、
        盈利前十（收益率）、亏损前十（收益率）等等，尽可能补全信息」

    🔴 三条判据都落在**可证的事实**上，不是"有那一列 / 有那个格子"：

      ① 方向列的每一格 == 服务端 `diff_history` 给的 `side` ——
         前端自己按 `got_buy`/`got_sell` 推就是第二份定义（而 `extra` /
         `sell_extra` 恰恰是 `want_side` 为 None 的那两种）。
      ② KPI 四格的数 == `/api/live/trade_stats` —— 页面自己拿当前那一页
         的往返算一遍的话，胜率会随翻页变**而不报错**。
      ③ 两张榜只列真的：盈利榜每行 > 0、亏损榜每行 < 0。

    ★ 「怎么进业绩页」走 `_lp_enter`（那个入口 2026-09-22 刚变过一次）。
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return 'playwright 没装，跳过'
    from assay import server as sv
    from assay import live as lv
    from http.server import ThreadingHTTPServer
    import threading

    aid = None
    for a in lv.load_accounts():
        if a.get('archived'):
            continue
        if (lv.trade_stats(a['id']) or {}).get('n'):
            aid = a['id']
            break
    if not aid:
        return '没有账户有平仓往返，跳过'
    want = lv.trade_stats(aid)
    # ★ `diff_history` 在 `lv/bench.py`，不在门面的转发名单里（那份只收
    #   base/fee/px/pos/ver/sig/perf/paper/hist）—— 直接 import 正本。
    from assay.lv import bench as _bench
    xd = {it['date']: it for it in _bench.diff_history(aid)}

    prev = sv.ALLOW_LIVE
    sv.ALLOW_LIVE = True
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), sv.Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    errs, nrow = [], 0
    try:
        with sync_playwright() as pw:
            br = pw.chromium.launch()
            pg = br.new_page(viewport={'width': 1440, 'height': 1000})
            pg.on('pageerror', lambda e: errs.append(str(e)))
            pg.goto('http://127.0.0.1:%d/#/live/%s' % (port, aid),
                    wait_until='networkidle')
            pg.wait_for_selector('#lvbody .lvsec', timeout=90000)
            _lp_enter(pg)

            # ---- ② KPI 四格：值必须来自服务端 ----
            pg.wait_for_function(
                "() => document.querySelectorAll('#lp_kpi > div').length >= 8",
                timeout=90000)
            # 🔴 **补进去这一发要幂等**：`render()` 会被调好几次（换基准 /
            #   换区间都会重取权益），前一发在飞、后一发命中缓存立刻返回，
            #   两发都往同一个 `#lp_kpi` 里 append -> 屏幕上 12 个格子。
            #   判据是**再调一次仍然 8 格**（`>= 8` 那种写法看不出来）。
            pg.evaluate("(a) => renderTradeKpi(a)", aid)
            pg.wait_for_timeout(400)
            n8 = pg.evaluate("() => document.querySelectorAll('#lp_kpi > div').length")
            assert n8 == 8, 'KPI 板该是 8 格（4 全程 + 4 交易统计），实际 %d' % n8
            # ★ 标签末尾都带「· 全程」（KPI 是全程口径、图按区间画）——
            #   这里按**前缀**取，免得那个后缀一改这条就挂。
            kpi = {k.split('\u00b7')[0].strip(): v for k, v in pg.evaluate(
                """() => [...document.querySelectorAll('#lp_kpi > div')].map(
                     d => [d.querySelector('.k').innerText.replace(/\\s+/g, ' ').trim(),
                           d.querySelector('.v').innerText.trim()])""")}
            assert '平仓笔数' in kpi and '胜率' in kpi and '盈亏比' in kpi \
                and '平均持有' in kpi, '交易统计四格没补全：%r' % sorted(kpi)
            assert kpi['平仓笔数'].replace(',', '') == str(want['n']), \
                '平仓笔数对不上服务端：%r vs %s' % (kpi['平仓笔数'], want['n'])
            # ★ 页面 `toFixed(1)` 的舍入误差上界正好是 5e-4，阈值写成它
            #   就卡在边界上（偶发红）。放到 1e-3 仍然抓得到真错
            #   （变异 ×90：45.0% vs 50%）。
            assert abs(float(kpi['胜率'].rstrip('%')) / 100
                       - want['win_rate']) < 1e-3, \
                '胜率对不上服务端：%r vs %s' % (kpi['胜率'], want['win_rate'])
            if want['profit_factor'] is None:
                assert kpi['盈亏比'] == '—', \
                    '没有亏损的往返时盈亏比要显示「—」，不能编一个数'
            else:
                assert abs(float(kpi['盈亏比']) - want['profit_factor']) < 0.01, \
                    '盈亏比对不上：%r vs %s' % (kpi['盈亏比'], want['profit_factor'])

            # ---- ③ 盈亏榜：只列真的 ----
            # 🔴 它 2026-09-22 从「清仓记录」挪成了**自己一个页签**
            #   （用户：「盈利榜亏损榜不要放在清仓记录中」）——
            #   **失败的是断言不是产品**，要证的事一个字没变，只是换了容器。
            _lp_tab(pg, '盈亏榜')
            pg.wait_for_selector('#lp_top .pgrid', timeout=90000)
            tops = pg.evaluate(
                """() => [...document.querySelectorAll('#lp_top .pgrid > div')]
                     .map(d => ({t: d.querySelector('.ttl').innerText,
                       r: [...d.querySelectorAll('tbody tr')].map(
                            tr => tr.children[2].innerText.trim())}))""")
            assert len(tops) == 2, '盈利榜 / 亏损榜该并排两块，实际 %d' % len(tops)
            assert '盈利' in tops[0]['t'] and '亏损' in tops[1]['t'], \
                '两张榜的标题不对：%r' % [t['t'] for t in tops]
            assert len(tops[0]['r']) == len(want['best']) \
                and len(tops[1]['r']) == len(want['worst']), \
                '榜的行数与服务端不一致'
            def _num(v):
                return float(v.replace('\u2212', '-').rstrip('%').replace(',', ''))
            for v in tops[0]['r']:
                assert _num(v) > 0, \
                    '盈利榜里混进了不赚钱的（%s）—— 列不满十条是事实' % v
            for v in tops[1]['r']:
                assert _num(v) < 0, '亏损榜里混进了赚钱的（%s）' % v

            # ---- ① 执行差异：方向列逐格对服务端 ----
            _lp_tab(pg, '执行差异')
            pg.wait_for_selector('#lp_exec .xdt', timeout=90000)
            heads = pg.evaluate(
                """() => [...document.querySelectorAll('#lp_exec .xdt')[0]
                     .querySelectorAll('thead th')].map(x => x.innerText.trim())""")
            assert '方向' in heads, '执行差异表没有「方向」列：%r' % heads
            i = heads.index('方向')
            secs = pg.evaluate(
                """(i) => [...document.querySelectorAll('#lp_exec .xds')].map(s => ({
                     d: s.querySelector('h3').innerText.trim().split(/\\s+/)[0],
                     rows: [...s.querySelectorAll('tbody tr')].map(tr => [
                       tr.children[0].innerText.replace(/\\s+/g, ' ').trim(),
                       tr.children[i].innerText.trim()])}))""", i)
            lbl = {'buy': '买', 'sell': '卖', 'both': '买+卖', None: '—'}
            for s in secs:
                it = xd.get(s['d'])
                assert it, '页面上那一期服务端没有：%r' % s['d']
                assert len(s['rows']) == len(it['rows']), '行数对不上 %s' % s['d']
                for (cell_txt, got), r in zip(s['rows'], it['rows']):
                    assert r['code'] in cell_txt, \
                        '行序对不上：%r 不含 %s' % (cell_txt, r['code'])
                    assert got == lbl[r.get('side')], \
                        ('方向对不上服务端：%s %s 页面 %r，服务端 side=%r'
                         % (s['d'], r['code'], got, r.get('side')))
                    nrow += 1
            assert nrow, '一行都没比到 —— 断言空转了'
            br.close()
    finally:
        httpd.shutdown()
        sv.ALLOW_LIVE = prev
    assert not errs, '页面报错：%r' % errs[:3]
    return ('%s：KPI 四格对上服务端（%d 笔 / 胜率 %.0f%%）、'
            '盈亏榜 %d+%d 条各自同号、执行差异 %d 行方向逐格一致'
            % (aid, want['n'], (want['win_rate'] or 0) * 100,
               len(want['best']), len(want['worst']), nrow))


@case('业绩页并页：旧 hash 不失效 / 盈亏榜独立 / 冲正跟着搬（playwright）', 'web')
def t_perf_merge():
    """用户 2026-09-22 四条：

      「流水按钮是否还有存在的必要，和业绩里的交易记录有所重复，应该可以
        合并到交易记录里」
      「选股理由应该也内置到业绩里，执行差异都已经在里面了」
      「盈利榜亏损榜不要放在清仓记录中，也单独开个页签在业绩」
      「执行差异应该放最后面，前面放选股理由」

    🔴 合并**唯一**真正的风险不是少两个按钮，是把东西弄丢（同「板块并进
      盘面」那条）。所以判据全落在"还找得到吗"上：

      ① 两个旧 hash 仍然到得了对应页签（书签 + 持仓行那个 `?` 的地址），
         且走的是 **replace** 不是赋值 —— 赋值会往历史里塞一条，
         按后退跳回旧地址又被弹回来，人就退不出去。
      ② 盈亏榜**离开了清仓记录**（不然就是"挪了个位置还留着一份"）。
      ③ 冲正**跟着搬进来了** —— 它是 append-only 账本唯一的更正手段。
      ④ **没有成交的账户也要能看选股理由**：这一页现在是它唯一的入口，
         而"还没有权益曲线"与选股理由毫无关系（第一版就这么挂了：
         整页只剩一句话，页签一个都没有）。
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return 'playwright 没装，跳过'
    import shutil
    import tempfile
    import threading
    from http.server import ThreadingHTTPServer
    import assay.live as lv
    import assay.server as sv

    prev_live, prev_allow = lv.LIVE, sv.ALLOW_LIVE
    tmp = tempfile.mkdtemp(prefix='merge_case_')
    shutil.copytree(prev_live, os.path.join(tmp, 'live'))
    lv.LIVE = os.path.join(tmp, 'live')
    sv.ALLOW_LIVE = True
    # ★ 挑一个**有往返**的账户：盈亏榜与冲正两条都要真数据才测得到
    aid = next((a['id'] for a in lv.load_accounts()
                if not a.get('archived') and (lv.trade_stats(a['id']) or {}).get('n')), None)
    # 🔴 ④ 那条要一个**一笔成交都没有**的账户 —— 真账本里不一定有，
    #   所以**构造**一个（同「断言要在能触发的构造上跑」）。
    empty = 'zz_nofill'
    lv.upsert_account(empty, name='空账户', init_cash=100000)
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), sv.Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = 'http://127.0.0.1:%d/' % port
    errs, msg = [], []
    try:
        if not aid:
            return '没有账户有往返，跳过'
        with sync_playwright() as pw:
            br = pw.chromium.launch()
            pg = br.new_page(viewport={'width': 1440, 'height': 1000})
            pg.on('pageerror', lambda e: errs.append(str(e)))

            # ---- ① 旧 hash -> 页签，且不往历史里塞一条 ----
            pg.goto(base + '#/live', wait_until='networkidle')
            pg.wait_for_selector('.ditem', timeout=60000)
            home = pg.evaluate('()=>history.length')
            for old, tab in (('/fills', '交易明细'), ('/why', '选股理由')):
                pg.evaluate("(h)=>{location.hash=h;}", '#/live/%s%s' % (aid, old))
                pg.wait_for_function("()=>location.hash.includes('/perf?tab=')",
                                     timeout=60000)
                pg.wait_for_selector('#lptabs div.on', timeout=60000)
                assert pg.locator('#lptabs div.on').inner_text().strip() == tab, \
                    ('旧 hash #/live/<id>%s 没落在「%s」页签上，而是 %s'
                     % (old, tab, pg.locator('#lptabs div.on').inner_text()))
            # replace 不是赋值：两次跳转 history 长度不该各涨一条
            #   （`location.hash=` 那两次是用例自己塞的，各算 1）
            grew = pg.evaluate('()=>history.length') - home
            assert grew <= 2, \
                ('旧 hash 用的是赋值不是 location.replace —— history 涨了 %d 条，'
                 '按后退会跳回旧地址又被弹回来，人退不出去' % grew)
            # 🔴 **参数也要跟着过去**，不只是落在那个页签上：
            #   `?d=` 是持仓行那个 `?`「定位到它建仓那一期」的全部信息，
            #   丢了的话人点过去落在第一页，看着像"这只票没有理由"——
            #   **而它不报错**（变异实测：只钉页签的话丢 `d=` 照样全绿）。
            pg.evaluate("(h)=>{location.hash=h;}",
                        '#/live/%s/why?d=2026-09-01&all=1' % aid)
            pg.wait_for_function("()=>location.hash.includes('/perf?tab=why')",
                                 timeout=60000)
            _h = pg.evaluate('()=>location.hash')
            assert 'd=2026-09-01' in _h and 'all=1' in _h, \
                '旧 hash 的 ?d= / ?all= 没带过去：%s' % _h

            # ---- ② 盈亏榜独立，且**离开了**清仓记录 ----
            pg.goto(base + '#/live/%s/perf' % aid, wait_until='networkidle')
            pg.wait_for_selector('#lptabs div', timeout=60000)
            tabs = [t.strip() for t in pg.locator('#lptabs div').all_inner_texts()]
            assert tabs.index('选股理由') < tabs.index('执行差异'), \
                '执行差异要排在最后、选股理由在它前面：%s' % tabs
            _lp_tab(pg, '盈亏榜')
            pg.wait_for_selector('#lp_top .pgrid', timeout=60000)
            n_top = pg.locator('#lp_top .pgrid > div').count()
            assert n_top == 2, '盈亏榜该是两块（盈利/亏损），实得 %d' % n_top
            _lp_tab(pg, '清仓记录')
            # ★ 等它**真的渲染完**再判"榜不在这里" —— 直接判的话，
            #   pane 还空着时 count() 也是 0，断言空转（同「页签点得开 ≠
            #   页签里有东西」）。
            pg.wait_for_function(
                "()=>{const e=document.querySelector('#lp_trip');"
                " return e && /清仓记录/.test(e.innerText);}", timeout=60000)
            assert pg.locator('#lp_trip .pgrid').count() == 0, \
                ('清仓记录里还留着盈亏榜 —— 那是"挪了个位置还留着一份"，'
                 '同一份信息两处渲染迟早分叉')

            # ---- ③ 冲正跟着搬进来了（账本唯一的更正手段）----
            _lp_tab(pg, '交易明细')
            pg.wait_for_selector('#lp_fill table.lvt', timeout=60000)
            rv = pg.locator('#lp_fill a.lvrv').count()
            assert rv >= 1, '交易明细里没有「冲正」—— 录错了就再也改不了'
            assert pg.locator('#lp_fill a[href*="/fills"]').count() == 0, \
                '还留着「去流水页」的链接 —— 那一页已经并过来了（点了只会绕回来）'
            msg.append('旧 hash 两条都 redirect 到页签；盈亏榜独立成页签；'
                       '冲正 %d 个按钮' % rv)

            # ---- ④ 一笔成交都没有的账户，选股理由仍然到得了 ----
            pg.goto(base + '#/live/%s/perf' % empty, wait_until='networkidle')
            # ★ 不直接 `wait_for_selector('#lptabs div')` —— 整页空掉时那是
            #   一句 60 秒裸超时，**报错指不到原因**（变异实测就是这样）。
            #   等"页面渲染完"，再自己判页签在不在。
            pg.wait_for_function(
                "()=>{const m=document.querySelector('#main');"
                " return m && m.innerText.trim() && !/读取中/.test(m.innerText);}",
                timeout=60000)
            t2 = [t.strip() for t in pg.locator('#lptabs div').all_inner_texts()]
            assert t2, ('没有成交的账户业绩页**一个页签都没有** —— 屏幕上只有'
                        '"%s"。而这一页是选股理由与执行差异的唯一入口。'
                        % pg.inner_text('#main')[:60].replace('\n', ' '))
            assert t2 == tabs, \
                ('没有成交的账户页签少了：%s —— 这一页现在是选股理由与执行差异的'
                 '**唯一**入口，不能因为"还没有权益曲线"就整页空掉' % t2)
            _lp_tab(pg, '选股理由')
            pg.wait_for_selector('#lp_why', timeout=60000)
            pg.wait_for_function(
                "()=>{const e=document.querySelector('#lp_why');"
                " return e && e.innerText.trim() && !/读取中/.test(e.innerText);}",
                timeout=60000)
            w = pg.inner_text('#lp_why')
            assert '选股理由' in w or '还没有信号' in w, \
                '空账户的选股理由页签没内容：%r' % w[:120]
            assert '还没有权益曲线' not in w, \
                ('空账户的选股理由页签显示的是"还没有权益曲线" —— '
                 '那句话与选股理由毫无关系')
            msg.append('空账户仍有 %d 个页签且选股理由打得开' % len(t2))
            br.close()
    finally:
        httpd.shutdown()
        lv.LIVE, sv.ALLOW_LIVE = prev_live, prev_allow
        shutil.rmtree(tmp, ignore_errors=True)
    assert not errs, '页面报错：%r' % errs[:3]
    return '；'.join(msg)


@case('公司行动：除权调成本、送转调股数、配股只留痕（实盘账本）', tag='fast')
def t_corp_actions():
    """🔴🔴 用户 2026-09-23：「分红后现金增加，对应的股的成本也应该降低」
    「不仅仅是分红，如果有拆股等等，都要能正确的计算价格、股数」。

    实测红利混合-M 三次除权合计 **3,560 元**：股价除权当天真的掉下去，
    而账本记的成本一分没动 -> 逐只浮盈系统性偏低，**而总资产那一格看着
    完全正常**（现金那半在券商那边加回来了）。正本 `lv/corp.py`。

    🔴 **送转与配股在真实账本里一条都没有**（实测 34 只持仓票只有 3 笔
      现金分红）—— 所以那两条路**必须构造**，否则整段空转
      （同「断言要在能触发的构造上跑」）。夹具取真实的历史事件：

        688399.XSHG 2026-07-10  纯送转 0.48/股（10 送转 4.8）
        001388.XSHE 2026-07-17  派现 0.50 + 送转 0.48（验"先派现后送转"）
        300176.XSHE 2026-08-21  配股 0.40/股 @ 3.36（**不许自动执行**）
    """
    import datetime as _dt
    import shutil
    import tempfile

    from assay import live as lv
    from assay.lv import corp as _corp
    from assay.lv import pos as _pos

    # ---- 0) 夹具必须真的存在 —— 反向自证，不然下面全是空转 ----
    FX = {'688399.XSHG': ('2026-07-10', 0.0, 0.48, 0.0),
          '001388.XSHE': ('2026-07-17', 0.5, 0.48, 0.0),
          '300176.XSHE': ('2026-08-21', 0.0, 0.0, 0.4)}
    for c, (d, c1, c3, c4) in FX.items():
        got = [a for a in _corp.actions([c], d, d)]
        assert len(got) == 1, '夹具没了：%s 在 %s 没有公司行动' % (c, d)
        a = got[0]
        assert abs(a['cash'] - c1) < 1e-6 and abs(a['split'] - c3) < 1e-6 \
            and abs(a['rights'] - c4) < 1e-6, \
            '夹具变了：%s %s -> %s' % (c, (c1, c3, c4), (a['cash'], a['split'], a['rights']))

    real = lv.LIVE
    tmp = tempfile.mkdtemp(prefix='selftest_corp_')
    # 🔴 selftest 不许写生产账本 —— 重定向（同 `lv.LIVE` 那条纪律）
    lv.LIVE = os.path.join(tmp, 'live')
    os.makedirs(lv.LIVE, exist_ok=True)
    try:
        lv.upsert_account('t_corp', name='corp', init_cash=1000000)

        def _buy(code, d, sh, px):
            return lv.add_fill('t_corp', d, code, 'buy', sh, px,
                               fee=0, force_price=True)

        # ---- 1) 纯送转：股数按比例涨，实付本金不动 ----
        _buy('688399.XSHG', '2026-07-01', 1000, 30.0)
        b = _pos.fifo_lots(lv.fills('t_corp'))
        lot = b['688399.XSHG'][0]
        assert lot['shares'] == 1480, '10 送转 4.8：1000 股应变 1480，实得 %s' % lot['shares']
        assert abs(lot['paid'] - 30000.0) < 1e-6, \
            '送转没有新钱投进去，实付本金不该变：%s' % lot['paid']
        assert abs(lot['price'] - 30.0) < 1e-9, \
            '🔴 `price` 是喂给引擎 entry_price 的口径，一个字都不能动'

        # ---- 2) 派现 + 送转同日：**先派现后送转** ----
        #     反过来的话分红会按送转【之后】的股数算 -> 凭空翻倍
        _buy('001388.XSHE', '2026-07-01', 1000, 20.0)
        b = _pos.fifo_lots(lv.fills('t_corp'))
        lot = b['001388.XSHE'][0]
        assert lot['shares'] == 1480, '送转后应是 1480 股，实得 %s' % lot['shares']
        assert abs(lot['div_gross'] - 500.0) < 1e-6, \
            ('分红要按【除权前】的 1000 股算 = 500 元；实得 %.2f'
             '（%.2f 说明顺序反了：按送转后的 1480 股算了）'
             % (lot['div_gross'], 740.0))
        assert abs(lot['paid'] - (20000.0 - 500.0)) < 1e-6, \
            '实付本金要按到手现金往下调：%s' % lot['paid']
        # 🔴 「price 不许动」要钉在**真收到过现金**的那一批上 —— 钉在
        #   688399（c1=0）上是空转的：`price -= cash` 对它是空操作
        #   （变异 M9 第一轮就是这么漏的，判据比要证的事窄）。
        assert abs(lot['price'] - 20.0) < 1e-9, \
            ('🔴 `price` 是喂给引擎 entry_price 的口径（止损/吊灯/红利税档位都'
             '读它），派现不许动它：%s' % lot['price'])

        # ---- 3) 配股【不自动执行】：要掏钱且可以放弃 ----
        _buy('300176.XSHE', '2026-08-01', 1000, 5.0)
        b = _pos.fifo_lots(lv.fills('t_corp'))
        lot = b['300176.XSHE'][0]
        assert lot['shares'] == 1000, \
            '🔴 配股不许自动执行（要掏钱、可以放弃），股数不该变：%s' % lot['shares']
        assert abs(lot['paid'] - 5000.0) < 1e-6
        # 但必须**留痕**：页面要说得出这件事发生过
        summ = lv.positions_valued('t_corp')['corp']
        rg = [e for e in summ['events'] if e['code'] == '300176.XSHE']
        assert rg and rg[0]['rights_per_share'], \
            '配股不自动执行，但必须留痕 —— 说了不能做就得让人知道它发生过'
        assert '配' in rg[0]['what'], '留痕要说清是什么事：%s' % rg[0]['what']

        # ---- 3b) 部分卖出：paid / div_gross 要和 fee 走【同一个比例】 ----
        #     不缩 paid 的话，卖掉一半之后摊薄成本会翻倍，**而它不报错**。
        #     🔴 这条必须构造 —— 上面几步全是只买不卖，那条路一步都没走到
        #       （变异 M10 第一轮就是这么漏的）。
        lv.add_fill('t_corp', '2026-09-01', '001388.XSHE', 'sell', 740,
                    price=20.0, fee=0, force_price=True)
        b = _pos.fifo_lots(lv.fills('t_corp'))
        lh = b['001388.XSHE'][0]
        assert lh['shares'] == 740, '卖掉一半应剩 740 股：%s' % lh['shares']
        assert abs(lh['paid'] - (20000.0 - 500.0) / 2) < 1e-6, \
            ('🔴 部分卖出后实付本金要按剩余股数比例缩（应 %.2f，实得 %.2f）'
             '—— 不缩的话摊薄成本会翻倍，而它不报错'
             % ((20000.0 - 500.0) / 2, lh['paid']))
        assert abs(lh['div_gross'] - 250.0) < 1e-6, \
            '已收分红也要按比例缩（红利税按批定档要用它）：%s' % lh['div_gross']

        # ---- 3b2) 🔴🔴 送转之后【卖得出去】：那道校验也要认识公司行动 ----
        #   券商账户里 10 送 10 之后是 1480 股，人照实录一笔卖 1480 ——
        #   而 `replay_violation` 原来自己数了一份 `held`（**第四份重放**，
        #   不认识公司行动），按 1000 判、**当场拒掉**：
        #     「这笔卖出会让 … 持仓变负：当时只有 1000 股，要卖 1480 股」
        #   一笔真实成交录不进去，而那句报错还指不到真正的原因。
        #   ★ 反向自证：卖**超过**真实股数的仍然要被拒，别矫枉过正。
        lv.upsert_account('t_corp4', name='corp4', init_cash=1000000)
        lv.add_fill('t_corp4', '2026-07-01', '688399.XSHG', 'buy', 1000, 30.0,
                    fee=0, force_price=True)
        _r = lv.add_fill('t_corp4', '2026-09-02', '688399.XSHG', 'sell', 1480,
                         price=30.0, fee=0, force_price=True)
        assert _r and int(_r['shares']) == 1480, \
            '送转后卖出全部 1480 股被拒了 —— 那是券商账户里真实的股数'
        try:
            lv.add_fill('t_corp4', '2026-09-03', '688399.XSHG', 'sell', 100,
                        price=30.0, fee=0, force_price=True)
            raise AssertionError('已经清仓了还能再卖 —— 那道校验被放宽过头了')
        except lv.LiveError:
            pass

        # ---- 3c) 🔴 **已清仓的票也要留在事件里** ----
        #   它在除权那天还持有着，那笔分红是真发生过的。而持仓表里早就没有
        #   它了 —— 所以"明细"那一页能给出的东西比主视图那行摘要多这一样。
        #   ★ 这条只能在**数据层**构造：页面那条用例读的是真账本，
        #     不许往里写（同「selftest 不许写生产账本」）。
        lv.add_fill('t_corp', '2026-09-02', '001388.XSHE', 'sell', 740,
                    price=20.0, fee=0, force_price=True)
        assert '001388.XSHE' not in _pos.fifo_lots(lv.fills('t_corp')), \
            '构造不对：001388 应该已经清仓了'
        _ev = lv.positions_valued('t_corp')['corp']['events']
        assert any(e['code'] == '001388.XSHE' for e in _ev), \
            ('🔴 清仓之后那次派现/送转从留痕里消失了 —— 它是真发生过的，'
             '而持仓表里已经没有这只票，明细页是唯一能查到它的地方')

        # ---- 4) 同一天：先行动、后成交（除权日当天【买入】拿不到分红）----
        #     分红归**登记日收盘**（= 除权日前一天）的持有人
        lv.upsert_account('t_corp2', name='corp2', init_cash=1000000)
        lv.add_fill('t_corp2', '2026-07-17', '001388.XSHE', 'buy', 1000, 20.0,
                    fee=0, force_price=True)
        b2 = _pos.fifo_lots(lv.fills('t_corp2'))
        l2 = b2['001388.XSHE'][0]
        assert abs(l2.get('div_gross') or 0) < 1e-9, \
            '🔴 除权日当天买入的拿不到这次分红（登记日是前一天），实得 %s' % l2.get('div_gross')
        assert l2['shares'] == 1000, \
            '除权日当天买的就是除权后的股数，不该再送一次：%s' % l2['shares']

        # ---- 5) 没有未来函数：除权日【前一天】的持仓必须还是原样 ----
        rows = lv.fills('t_corp')
        pre = _pos.lots_asof(rows, '2026-07-09')['688399.XSHG'][0]
        assert pre['shares'] == 1000, \
            ('🔴 `lots_asof` 把 asof 传下去了没有 —— 2026-07-09 的持仓被'
             '07-10 才发生的送转调过了（未来函数）：%s' % pre['shares'])
        post = _pos.lots_asof(rows, '2026-07-10')['688399.XSHG'][0]
        assert post['shares'] == 1480, '除权当日就该生效：%s' % post['shares']

        # ---- 6) 现金：三笔的派现合计要进现金，且与逐条留痕对得上 ----
        exp_div = 1000 * 0.5          # 只有 001388 派现（688399/300176 都没有）
        s2 = lv.positions_valued('t_corp')['corp']
        assert abs(s2['cash'] - exp_div) < 1e-6, \
            '留痕的合计对不上：%.2f vs %.2f' % (s2['cash'], exp_div)
        c_now = lv.cash('t_corp')
        c_naive = 1000000 - 30000 - 20000 - 5000 + 740 * 20.0 + 740 * 20.0
        assert abs(c_now - (c_naive + exp_div)) < 1e-6, \
            '现金没把公司行动派现加进来：%.2f vs %.2f' % (c_now, c_naive + exp_div)

        # ---- 7) 手工录过分红流水就【不自动加】（双计），并且要说出来 ----
        lv.add_cashflow('t_corp', '2026-07-17', 400.0, kind='dividend',
                        note='手工录的')
        assert abs(lv.cash('t_corp') - (c_naive + 400.0)) < 1e-6, \
            '🔴 手工录过分红就不许再自动加一遍 —— 那是双计'
        cf = _pos.corp_conflict('t_corp')
        assert cf and cf['manual_n'] == 1 and cf['auto_amount'] > 0, \
            '撞车了要说出来（报出来，别替人决定）：%s' % cf

        # ---- 8) 反向自证：没有公司行动的账户，一个数都不许变 ----
        lv.upsert_account('t_corp3', name='corp3', init_cash=500000)
        lv.add_fill('t_corp3', '2026-09-22', '600012.XSHG', 'buy', 100, 16.69,
                    fee=1.0, force_price=True)
        assert not _corp.actions(['600012.XSHG'], '2026-09-22', '2026-12-31'), \
            '600012 这段有公司行动了 —— 换一只没有的当对照'
        assert abs(lv.cash('t_corp3') - (500000 - 100 * 16.69 - 1.0)) < 1e-6, \
            '没有公司行动的账户现金不许变'
        l3 = _pos.fifo_lots(lv.fills('t_corp3'))['600012.XSHG'][0]
        assert l3['shares'] == 100 and abs(l3['paid'] - 1669.0) < 1e-6

        # ---- 9) 数据新鲜度要能说出来（落后 = 那几天除权的不会被调，不报错）----
        st = _corp.status()
        assert st['exists'] and st['max_ex_date'], 'gbbq 导出不在：%s' % st
        assert st['stale_days'] is not None and not st['stale'], \
            ('公司行动数据落后 %s 天 —— 跑一次 datalake/sync_daily.sh'
             % st['stale_days'])
        return ('送转 1000->1480 股且实付本金不动；同日先派现后送转（500 不是 740）；'
                '配股不自动执行但留痕；除权日当天买入拿不到分红；'
                'lots_asof 无未来函数；现金 +%.0f 且与留痕对得上；'
                '手工分红不双计并报冲突；无行动账户逐位不变；gbbq 覆盖到 %s'
                % (exp_div, st['max_ex_date']))
    finally:
        lv.LIVE = real
        shutil.rmtree(tmp, ignore_errors=True)
