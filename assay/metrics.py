#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""绩效指标。

两条硬约定：
  · **回撤按日频权益曲线算** —— 只在调仓日取样会大幅低估回撤。
    旧引擎曾把 44% 的真实回撤算成 17%，而那个反常的小回撤才是暴露死仓 bug 的线索。
  · **超额指标必须有** —— 聚宽结果里给的是 信息比率 / 超额收益最大回撤 /
    alpha / beta，没有基准就无法与之对比，只能比年化，归因粒度太粗。
"""
import math
from collections import OrderedDict

TRADING_DAYS = 250


def _rets(vals):
    return [vals[i] / vals[i - 1] - 1 for i in range(1, len(vals))]


def _std(xs):
    if len(xs) < 2:
        return 0.0
    mu = sum(xs) / len(xs)
    return math.sqrt(sum((x - mu) ** 2 for x in xs) / len(xs))


def _mdd(vals):
    peak = mdd = 0.0
    lo = hi = None
    for i, v in enumerate(vals):
        if v > peak:
            peak, _hi = v, i
        dd = 1 - v / peak if peak else 0.0
        if dd > mdd:
            mdd, hi, lo = dd, _hi, i
    return mdd, hi, lo


def summarize(curve, starting_cash, bench=None, broker=None, bench_base=None, engine=None):
    if not curve:
        return {}
    dates = [d for d, _ in curve]
    # ★ 把初始资金作为第 0 点：否则【回测首日的收益】不会进入日收益序列，
    #   夏普/波动/beta 全都会漏掉第一天。基准侧同理用前一交易日收盘做基点。
    vals = [starting_cash] + [v for _, v in curve]
    yrs = (dates[-1] - dates[0]).days / 365.25
    tot = vals[-1] / starting_cash
    rs = _rets(vals)
    sd = _std(rs)
    mdd, _, _ = _mdd(vals[1:])   # 回撤只看实际权益曲线，不含初始点
    ann = tot ** (1 / yrs) - 1 if yrs > 0 else 0.0

    m = {
        'end_value': vals[-1],
        'total_return': tot - 1,
        'annual_return': ann,
        'max_drawdown': mdd,
        'years': yrs,
        'volatility': sd * math.sqrt(TRADING_DAYS),
        'sharpe': (sum(rs) / len(rs) * TRADING_DAYS) / (sd * math.sqrt(TRADING_DAYS))
                  if sd > 0 else 0.0,
        'calmar': ann / mdd if mdd > 0 else None,
    }
    # 索提诺：只罚下行波动。小市值策略上行波动很大，用总波动会低估风险调整后收益
    dn = _std([min(r, 0.0) for r in rs])
    m['sortino'] = (sum(rs) / len(rs) * TRADING_DAYS) / (dn * math.sqrt(TRADING_DAYS)) \
        if dn > 0 else None

    # ---------- 基准与超额 ----------
    if bench and len(bench) > 2:
        bd = {d: v for d, v in bench}
        pair = [(vals[i + 1], bd[dates[i]]) for i in range(len(dates)) if dates[i] in bd]
        if len(pair) > 2:
            sv = [starting_cash] + [p[0] for p in pair]
            bv = [bench_base if bench_base else pair[0][1]] + [p[1] for p in pair]
            brs = _rets(bv)
            srs = _rets(sv)
            btot = bv[-1] / bv[0]
            m['benchmark_return'] = btot - 1
            m['benchmark_annual'] = btot ** (1 / yrs) - 1 if yrs > 0 else 0.0
            m['excess_return'] = m['total_return'] - m['benchmark_return']
            # beta / alpha：日收益回归
            mb = sum(brs) / len(brs)
            ms = sum(srs) / len(srs)
            var = sum((b - mb) ** 2 for b in brs) / len(brs)
            cov = sum((srs[i] - ms) * (brs[i] - mb) for i in range(len(brs))) / len(brs)
            beta = cov / var if var > 0 else None
            m['beta'] = beta
            m['alpha'] = ((ms - beta * mb) * TRADING_DAYS) if beta is not None else None
            # 超额收益曲线：逐日 (1+rs)/(1+rb) 累乘 —— 不是简单相减，
            # 简单相减在长区间会因复利失真
            ex, eq = [1.0], 1.0
            for i in range(len(brs)):
                eq *= (1 + srs[i]) / (1 + brs[i])
                ex.append(eq)
            exr = _rets(ex)
            exsd = _std(exr)
            m['excess_annual'] = ex[-1] ** (1 / yrs) - 1 if yrs > 0 else 0.0
            m['excess_max_drawdown'] = _mdd(ex)[0]
            m['info_ratio'] = (sum(exr) / len(exr) * TRADING_DAYS) / \
                (exsd * math.sqrt(TRADING_DAYS)) if exsd > 0 else None

    # ---------- 换手与成本 ----------
    if broker is not None:
        avg_eq = sum(vals[1:]) / len(vals[1:])
        if avg_eq > 0 and yrs > 0:
            # 「全仓往返次数/年」：卖出额 / 平均权益 / 年数。
            # ⚠️ 曾用「成交笔数」算过，导致 2 倍虚高 —— 笔数不等于全仓往返。
            m['turnover_per_year'] = broker.sell_amount / avg_eq / yrs
        m['fee_paid'] = broker.fee_paid
        m['div_tax_paid'] = broker.div_tax_paid
        m['frozen_days'] = broker.frozen_days
        m['max_frozen_run'] = broker.max_frozen_run
        m['n_frozen_at_end'] = len(broker.frozen_now())
        m['buy_amount'] = broker.buy_amount
        m['sell_amount'] = broker.sell_amount
        if broker.trades:
            rts = [t['ret'] for t in broker.trades]
            win = [r for r in rts if r > 0]
            loss = [r for r in rts if r <= 0]
            m['n_trades'] = len(rts)
            m['win_rate'] = len(win) / len(rts)
            m['avg_win'] = sum(win) / len(win) if win else 0.0
            m['avg_loss'] = sum(loss) / len(loss) if loss else 0.0
            m['profit_factor'] = abs(m['avg_win'] / m['avg_loss']) if m['avg_loss'] else None
            m['avg_holding_days'] = sum(t['holding_days'] for t in broker.trades) / len(rts)

    # ★ 空仓统计：数据缺失（如某张表区间不够）会让选股 SQL 返空 -> 长期空仓，
    #   而回测照常出报告。实测过一次 8 年空仓仍给出「年化 10.85%」。
    if engine is not None:
        m['empty_days'] = engine.empty_days
        m['max_empty_run'] = engine.max_empty_run
        m['empty_pct'] = (100.0 * engine.empty_days / len(engine.curve)
                          if engine.curve else 0.0)
        m['empty_span'] = engine.empty_span

        # ★★ 仓位（positions_value / total_value）比「空仓日数」更本质：
        #   空仓守卫只抓 n_positions == 0，抓不到【欠配】—— 目标 8 只只买到 3 只时
        #   n_positions=3>0，守卫完全沉默，而仓位其实只有 ~37%。
        #   实测红利价值：空仓日 8.1%，但仓位<50% 的日子有 11.6% ——
        #   中间那 3.5% 就是守卫的盲区。
        #   所以把仓位作为主检查项：低仓位 = 收益被现金稀释 = 指标不可直接比。
        exp = [(r['positions_value'] / r['total_value']) if r['total_value'] else 0.0
               for r in engine.daily]
        if exp:
            m['exposure_avg'] = 100.0 * sum(exp) / len(exp)
            m['exposure_min'] = 100.0 * min(exp)
            m['exposure_lt50_pct'] = 100.0 * sum(1 for x in exp if x < 0.5) / len(exp)
            m['exposure_lt80_pct'] = 100.0 * sum(1 for x in exp if x < 0.8) / len(exp)
            by = OrderedDict()
            for r in engine.daily:
                y = r['date'].year
                e = (r['positions_value'] / r['total_value']) if r['total_value'] else 0.0
                by.setdefault(y, []).append(e)
            m['exposure_yearly'] = OrderedDict(
                (y, round(100.0 * sum(v) / len(v), 1)) for y, v in by.items())
    return m


def monthly_table(curve):
    """月度收益表。看「哪一年哪个月崩的」比看一个总回撤有用得多。"""
    by = OrderedDict()
    prev = None
    for d, v in curve:
        k = (d.year, d.month)
        if k not in by:
            by[k] = [prev if prev is not None else v, v]
        by[k][1] = v
        prev = v
    return OrderedDict(((y, mo), b / a - 1) for (y, mo), (a, b) in by.items() if a)


def concentration(holdings):
    """持仓集中度：最大权重均值 + HHI。等权策略若出现高集中度，
    通常意味着「不再平衡 + 整手取整」在悄悄改变组合结构。"""
    if not holdings:
        return {}
    by = {}
    for h in holdings:
        by.setdefault(h['date'], []).append(h.get('weight') or 0.0)
    mx = [max(w) for w in by.values() if w]
    hhi = [sum(x * x for x in w) for w in by.values() if w]
    return {'max_weight_avg': sum(mx) / len(mx) if mx else None,
            'max_weight_peak': max(mx) if mx else None,
            'hhi_avg': sum(hhi) / len(hhi) if hhi else None}


def _pct(v):
    return '%13.2f%%' % (v * 100) if v is not None else '%14s' % '—'


def _num(v, w=13, p=2):
    return ('%*.*f' % (w, p, v)) if v is not None else '%*s' % (w, '—')


def report(curve, starting_cash, broker=None, bench=None, bench_code=None,
           holdings=None, monthly=False, bench_base=None, engine=None):
    m = summarize(curve, starting_cash, bench=bench, broker=broker,
                  bench_base=bench_base, engine=engine)
    m.update(concentration(holdings or []))
    print('=' * 66)
    print('  期末权益   %14s' % format(int(m['end_value']), ','))
    print('  累计收益   %s' % _pct(m['total_return']))
    print('  年化收益   %s' % _pct(m['annual_return']))
    print('  最大回撤   %s  (日频)' % _pct(m['max_drawdown']))
    print('  年化波动   %s' % _pct(m['volatility']))
    print('  夏普       %s      索提诺 %s      卡玛 %s'
          % (_num(m.get('sharpe')), _num(m.get('sortino'), 6), _num(m.get('calmar'), 6)))
    if 'benchmark_return' in m:
        print('  ' + '-' * 62)
        print('  基准 %s' % (bench_code or ''))
        print('  基准收益   %s   基准年化 %s'
              % (_pct(m['benchmark_return']), _pct(m['benchmark_annual'])))
        print('  超额收益   %s   超额年化 %s'
              % (_pct(m['excess_return']), _pct(m['excess_annual'])))
        print('  超额回撤   %s   信息比率 %s'
              % (_pct(m['excess_max_drawdown']), _num(m.get('info_ratio'))))
        print('  alpha      %s   beta     %s'
              % (_pct(m.get('alpha')), _num(m.get('beta'))))
    if broker is not None:
        print('  ' + '-' * 62)
        print('  平仓笔数   %13d      胜率 %s      盈亏比 %s'
              % (m.get('n_trades', 0), _pct(m.get('win_rate')), _num(m.get('profit_factor'), 6)))
        print('  年换手     %s 次全仓往返   平均持有 %s 天'
              % (_num(m.get('turnover_per_year')), _num(m.get('avg_holding_days'), 6, 1)))
        print('  费用合计   %14s      红利税 %s'
              % (format(int(m.get('fee_paid', 0)), ','),
                 format(int(m.get('div_tax_paid', 0)), ',')))
        print('  退市清算   %13d 笔' % len(broker.delisted))
        if broker.frozen_days:
            print('  停牌挂账   %13d 持仓日   最长连续 %d 天 (%s)'
                  % (broker.frozen_days, broker.max_frozen_run, broker.max_frozen_code))
        fz = broker.frozen_now()
        if fz:
            # ★ 期末仍有停牌持仓 -> 它们按【陈价】计入最终权益，结论不可验证。
            #   必须响亮，不能只当一行统计。
            print('  ⚠ 期末仍有 %d 只停牌持仓（按陈价计入权益，最终收益不可验证）:'
                  % len(fz))
            for c, n in sorted(fz.items(), key=lambda x: -x[1]):
                print('      %-14s 已停牌 %d 个交易日' % (c, n))
        if broker.rejects:
            from collections import Counter
            c = Counter(r[3] for r in broker.rejects)
            print('  拒单 %d 笔:' % len(broker.rejects))
            for k, v in c.most_common():
                print('      %-28s %6d' % (k, v))
    # ★ 长期空仓 = 响亮告警。数据缺失（某张表区间不够、字段全 NULL）会让选股
    #   SQL 返空集，策略连续空仓，而年化/夏普照常输出、看着完全合理。
    #   实测过一次：beta_daily 起点错设成 2013，红利低波 2005-2012 连续 8 年
    #   空仓（占 38% 时间），报告给出「年化 10.85%」，只有逐年拆表才发现。
    if m.get('exposure_avg') is not None:
        ea = m['exposure_avg']
        lvl = '⚠⚠' if ea < 70 else ('⚠' if ea < 90 else ' ')
        print('  %s 平均仓位   %11.1f%%   最低 %.1f%%   仓位<80%% 的日子 %.1f%%'
              % (lvl, ea, m['exposure_min'], m['exposure_lt80_pct']))
        if ea < 70:
            print('     ↑ 平均仓位过低 —— 收益被现金稀释，【与满仓策略不可直接比】。')
            print('       常见原因：目标池长期给不满（选股条件太严）、')
            print('       或选股 SQL 依赖的表区间不覆盖回测起点。查 [PICK] 的池子规模。')
    if m.get('empty_days'):
        span = m.get('empty_span')
        print('  %s 空仓 %d 交易日（占 %.1f%%）  最长连续 %d 日%s'
              % ('⚠⚠' if m['empty_pct'] >= 10 else '⚠',
                 m['empty_days'], m['empty_pct'], m['max_empty_run'],
                 ('  %s ~ %s' % (span[0], span[1])) if span else ''))
        if m['empty_pct'] >= 10:
            print('     ↑ 空仓占比过高，收益/回撤/夏普均被稀释，【结论不可用】。')
            print('       先查选股 SQL 依赖的表区间是否覆盖回测起点'
                  '（如 std/beta_daily.parquet 从 2005 起）。')
    if m.get('max_weight_avg') is not None:
        print('  集中度     最大权重均值 %.1f%% / 峰值 %.1f%% / HHI %.3f'
              % (m['max_weight_avg'] * 100, m['max_weight_peak'] * 100, m['hhi_avg']))
    print('=' * 66)
    if monthly:
        print_monthly(curve)
    return m


def print_monthly(curve):
    tb = monthly_table(curve)
    years = sorted({y for y, _ in tb})
    print('\n月度收益 (%)')
    print('%-6s' % '年' + ''.join('%7s' % ('%d月' % mo) for mo in range(1, 13)) + '%9s' % '全年')
    for y in years:
        row, acc = '%-6d' % y, 1.0
        for mo in range(1, 13):
            v = tb.get((y, mo))
            row += '%7s' % ('%.1f' % (v * 100) if v is not None else '')
            if v is not None:
                acc *= 1 + v
        print(row + '%8.1f%%' % ((acc - 1) * 100))
