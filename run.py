#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""回测入口。跑完自动归档到 runs/<分组>/<策略>/<run_id>/。

    python3 run.py strategies/小市值/v0b.py --start 2019-01-01 --end 2026-06-30 --cash 1000000
    python3 run.py strategies/小市值/froec.py --start 2016-01-01 --end 2026-08-07 --cash 100000
"""
import argparse
import importlib.util
import io
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
        stats = report(curve, a.cash, eng.broker, bench=eng.bench,
                       bench_code=eng.bench_code, holdings=eng.holdings,
                       monthly=a.monthly, bench_base=eng.bench_base)
        elapsed = time.time() - t0
        if not a.no_archive:
            d, rid = registry.save(a.strategy, group, eng, stats, a, tee.buf.getvalue(), elapsed)
            print('已归档 %s' % os.path.relpath(d, os.path.dirname(os.path.abspath(__file__))))
            print('  run_id = %s   查询: python3 runs.py show %s' % (rid, rid))
    finally:
        sys.stdout = tee.stream


if __name__ == '__main__':
    main()
