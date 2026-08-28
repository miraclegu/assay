#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""回测入口。跑完自动归档到 runs/<分组>/<策略>/<run_id>/。

    python3 run.py strategies/小市值/sgmspeg_v0b.py --start 2019-01-01 --end 2026-06-30 --cash 1000000
    python3 run.py strategies/小市值/froec.py --start 2016-01-01 --end 2026-08-07 --cash 100000
"""
import argparse
import importlib.util
import io
import datetime
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from assay import registry                # noqa: E402
from assay.broker import Cost             # noqa: E402
from assay.engine import Engine           # noqa: E402
from assay.feed import PanelFeed          # noqa: E402
from assay.metrics import report          # noqa: E402


class _Tee:
    """同时写终端和归档日志。日志是复盘材料，不能只留在滚动的终端里。"""

    def __init__(self, stream):
        self.stream, self.buf = stream, io.StringIO()

    def write(self, s):
        self.stream.write(s)
        self.buf.write(s)

    def flush(self):
        self.stream.flush()


def load(path):
    spec = importlib.util.spec_from_file_location('strategy', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if not hasattr(mod, 'initialize'):
        raise SystemExit('策略缺少 initialize(context)')
    return mod


def parse_params(items):
    """K=V -> 带类型推断的 dict。int/float/bool 自动识别，其余当字符串。"""
    out = {}
    for it in items:
        if '=' not in it:
            raise SystemExit('--param 需要 K=V 形式: %s' % it)
        k, v = it.split('=', 1)
        out[k.strip()] = cast(v.strip())
    return out


def cast(v):
    low = v.lower()
    if low in ('true', 'false'):
        return low == 'true'
    if low in ('none', 'null'):
        return None
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        return v


def _build_cost(a):
    """命令行显式指定的字段会被 lock 住，策略无法覆盖（且覆盖时告警）。"""
    kw, locked = {}, []
    if a.jq_cost:
        # 聚宽原版：set_slippage(FixedSlippage(0)) + 万三 + 最低5 + 印花税千一固定
        kw.update(slippage=0.0, commission=0.0003, min_commission=5.0, close_tax=0.001)
        locked += ['slippage', 'commission', 'min_commission', 'close_tax']
    for cli, field in (('slippage', 'slippage'), ('commission', 'commission'),
                       ('min_commission', 'min_commission'), ('open_tax', 'open_tax'),
                       ('volume_ratio', 'volume_ratio')):
        v = getattr(a, cli)
        if v is not None:
            kw[field] = v
            if field not in locked:
                locked.append(field)
    if a.stamp_tax is not None:
        kw['close_tax'] = a.stamp_tax if a.stamp_tax == 'auto' else float(a.stamp_tax)
        if 'close_tax' not in locked:
            locked.append('close_tax')
    if a.no_dividend_tax:
        kw['dividend_tax'] = False
        locked.append('dividend_tax')
    return Cost(locked=locked, **kw)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('strategy')
    ap.add_argument('--start', default='2019-01-01')
    ap.add_argument('--end', default='2026-06-30')
    # ---- 资金与成本：不填就用业内常用默认值 ----
    ap.add_argument('--cash', type=float, default=500000,
                    help='初始资金，默认 50 万')
    ap.add_argument('--slippage', type=float, default=None,
                    help='双边滑点，默认 0.0015（买 +x/2、卖 -x/2）')
    ap.add_argument('--commission', type=float, default=None,
                    help='佣金比例，默认 0.00025（万 2.5）')
    ap.add_argument('--min-commission', type=float, default=None,
                    help='单笔最低佣金，默认 5 元')
    ap.add_argument('--stamp-tax', default=None, metavar='auto|RATE',
                    help="卖出印花税；默认 auto = 按日期分段"
                         "（2023-08-28 起减半：0.001 -> 0.0005）")
    ap.add_argument('--volume-ratio', type=float, default=None,
                    help='单笔委托占当日成交额上限，默认 0.25；0 = 不限制')
    ap.add_argument('--open-tax', type=float, default=None,
                    help='买入印花税，默认 0（A 股买入不征）')
    ap.add_argument('--warmup-months', type=int, default=0, metavar='N',
                    help='统计前跳过前 N 个自然月：策略照常从 --start 运行并建仓，只是收益/回撤/夏普/胜率等从第 N+1 个月首个交易日起算。'
                         ' 用途：不同 monthday 的首次建仓时点不同，会污染整段对比。')
    ap.add_argument('--jq-cost', action='store_true',
                    help='一键套用聚宽原版设定（滑点 0、佣金万 3、印花税千一固定），对标用')
    ap.add_argument('--datalake', default=None)
    ap.add_argument('--group', default=None,
                    help='覆盖分组（默认由策略文件在 strategies/ 下的路径推导）')
    ap.add_argument('--no-dividend-tax', action='store_true',
                    help='关闭红利税（聚宽会扣，仅用于量化这笔税本身的影响）')
    ap.add_argument('--benchmark', default=None,
                    help="基准指数，如 000905.XSHG；覆盖策略里的 set_benchmark")
    ap.add_argument('--param', action='append', default=[], metavar='K=V',
                    help='覆盖策略参数，如 --param candidate_num=20，可重复')
    ap.add_argument('--monthly', action='store_true', help='打印月度收益表')
    ap.add_argument('--no-archive', action='store_true', help='不归档，只看结果')
    ap.add_argument('-v', '--verbose', action='store_true')
    a = ap.parse_args()

    tee = _Tee(sys.stdout)
    sys.stdout = tee
    t0 = time.time()
    try:
        feed = PanelFeed(a.start, a.end, root=a.datalake)
        cost = _build_cost(a)
        eng = Engine(load(a.strategy), feed, cash=a.cash, cost=cost,
                     params=parse_params(a.param))
        group = a.group or registry.derive_group(a.strategy)
        print('策略 %s | 分组 %s | 区间 %s ~ %s | 交易日 %d | 初始资金 %s'
              % (os.path.basename(a.strategy), group, feed.trading_days[0],
                 feed.trading_days[-1], len(feed.trading_days), format(int(a.cash), ',')))
        # 成本假设必须打印出来 —— 它直接决定结果，绝不能只躺在默认值里
        print('成本 %s' % cost.describe())
        if cost.locked:
            print('     （命令行指定: %s，策略内的同名设定会被忽略并告警）'
                  % ', '.join(sorted(cost.locked)))
        if a.benchmark:
            eng.set_benchmark(a.benchmark)   # 命令行优先于策略内 set_benchmark
            eng._bench_cli = True
        curve = eng.run(verbose=a.verbose)
        print()
        # ★★ 预热期：不同 monthday 的【首次建仓时点】不同，会污染整段对比。
        #    实测：组合版 md=-1 在 2016-01 平均仓位仅 5.0%（首次调仓在 1-29），
        #    整月空仓躲过两次熔断，凭此在 2016 年领先 md=1 达 +26.20pp；
        #    而剔掉 2016 后 md=-1 在 2017-2025 年化【低 2.97pp】、四项指标全差。
        #    那 +26pp 衡量的是「1 月该不该在场」，与调仓日选择无关 —— 是回测
        #    起点的人为产物。本选项把这段不可比的头部从统计里剪掉：
        #    策略照常运行建仓，只是指标从预热期结束后起算。
        base_cash, curve_st, bench_st, trades_bak = a.cash, curve, eng.bench, None
        if a.warmup_months > 0 and curve:
            y, m = curve[0][0].year, curve[0][0].month + a.warmup_months
            y, m = y + (m - 1) // 12, (m - 1) % 12 + 1
            cut = datetime.date(y, m, 1)
            keep = [x for x in curve if x[0] >= cut]
            if len(keep) < 2:
                print('⚠ 预热 %d 个月后剩余交易日不足，忽略 --warmup-months'
                      % a.warmup_months)
            else:
                curve_st = keep
                base_cash = keep[0][1]          # 预热末的权益作为新起点
                bench_st = [x for x in (eng.bench or []) if x[0] >= cut] or None
                # 成交也要一起裁，否则胜率/换手仍含预热期
                trades_bak = eng.broker.trades
                eng.broker.trades = [t for t in trades_bak
                                     if t.get('exit_date', cut) >= cut]
                print('预热 %d 个月：统计自 %s 起算（权益 %s），'
                      '预热期内 %d 笔平仓不计入'
                      % (a.warmup_months, keep[0][0], format(int(base_cash), ','),
                         len(trades_bak) - len(eng.broker.trades)))
                print()
        hold_st = ([h for h in eng.holdings if h['date'] >= curve_st[0][0]]
                   if a.warmup_months > 0 else eng.holdings)
        stats = report(curve_st, base_cash, eng.broker, engine=eng, bench=bench_st,
                       bench_code=eng.bench_code, holdings=hold_st,
                       monthly=a.monthly,
                       bench_base=(bench_st[0][1]
                                   if (bench_st and a.warmup_months > 0)
                                   else eng.bench_base))
        if trades_bak is not None:
            eng.broker.trades = trades_bak   # 归档仍存完整成交，不改动历史
        stats['warmup_months'] = a.warmup_months
        stats['stats_from'] = str(curve_st[0][0])
        elapsed = time.time() - t0
        if not a.no_archive:
            d, rid = registry.save(a.strategy, group, eng, stats, a, tee.buf.getvalue(), elapsed)
            print('已归档 %s' % os.path.relpath(d, os.path.dirname(os.path.abspath(__file__))))
            print('  run_id = %s   查询: python3 runs.py show %s' % (rid, rid))
    finally:
        sys.stdout = tee.stream


if __name__ == '__main__':
    main()
