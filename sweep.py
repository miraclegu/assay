#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""参数扫描与批量对比。

    # 滑点敏感性
    python3 sweep.py strategies/小市值/v0b.py --start 2019-01-01 --end 2026-06-30 \
        --cash 1000000 --grid slippage=0,0.0015,0.003

    # 策略参数 × 成本参数（笛卡尔积）
    python3 sweep.py strategies/小市值/froec.py --start 2016-01-01 --end 2026-08-07 \
        --cash 100000 --jq-cost --grid stock_num=5,10,20 --grid candidate_num=10,15

消融实验与滑点 sweep 是本项目的日常（v3a/v3b/v3c/v3d、drop-one 一整套），
以前得手写循环再手工记录，现在每个组合自动归档、跑完出并排对比表。

两个实现要点：
  · **feed 只物化一次，跨组合复用** —— 物化 bars 表是最慢的一步（FROEC 全区间
    约 20 秒），9 个组合各建一次就是 3 分钟纯浪费。
  · **策略参数名拼错立即抛错**（见 engine.py）—— 静默无效会让扫描结论变成
    「这个参数没影响」，那是最危险的一类假结论。

并行（`--jobs N`）：策略函数走 api 的模块级代理（为了照抄聚宽的裸函数手感），
有全局状态，所以**同进程内不能并发**。用多进程绕开 —— 每个子进程独立导入 assay，
各自持有一份 api 状态。

⚠️ 代价：feed 物化在每个进程各做一次。所以 worker 用 initializer 建一次 feed
   然后处理多个组合，而不是每个组合建一次。内存也按 worker 数放大
   （全区间 bars 表约 5M 行/进程）。组合数少于 worker 数时并行没有收益。
"""
import argparse
import itertools
import os
import sys
import time
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pandas as pd                                  # noqa: E402

from assay import registry                           # noqa: E402
from assay.broker import Cost                        # noqa: E402
from assay.engine import Engine                      # noqa: E402
from assay.feed import PanelFeed                      # noqa: E402
from assay.metrics import summarize                  # noqa: E402
from run import cast, load                           # noqa: E402

# 这些名字归引擎/成本，其余一律当策略参数注入 g
COST_KEYS = {'slippage', 'commission', 'min_commission', 'stamp_tax',
             'open_tax', 'dividend_tax'}
ENGINE_KEYS = {'cash'}

SHOW = ['annual_return', 'max_drawdown', 'excess_annual', 'info_ratio',
        'sharpe', 'turnover_per_year', 'n_trades', 'win_rate']
PCT = {'annual_return', 'max_drawdown', 'excess_annual', 'win_rate'}


def parse_grid(items):
    grid = {}
    for it in items:
        if '=' not in it:
            raise SystemExit('--grid 需要 K=V1,V2,... 形式: %s' % it)
        k, vs = it.split('=', 1)
        grid[k.strip()] = [cast(v.strip()) for v in vs.split(',') if v.strip() != '']
    return grid


def build_cost(base_jq, over):
    kw, locked = {}, []
    if base_jq:
        kw.update(slippage=0.0, commission=0.0003, min_commission=5.0, close_tax=0.001)
        locked += ['slippage', 'commission', 'min_commission', 'close_tax']
    for k, v in over.items():
        f = 'close_tax' if k == 'stamp_tax' else k
        kw[f] = v
        if f not in locked:
            locked.append(f)
    return Cost(locked=locked, **kw)


# ---- 多进程 worker：initializer 里建一次 feed，之后复用 ----
_W = {}


def _init(start, end, root, strategy_path):
    from assay.feed import PanelFeed
    _W['feed'] = PanelFeed(start, end, root=root)
    _W['mod'] = load(strategy_path)


def _work(job):
    """在子进程里跑一个组合。返回 (pt, metrics, run_id, elapsed)。"""
    (pt, keys, cash, jq_cost, benchmark, group, strategy_path, archive,
     start, end) = job
    cost_over = {k: v for k, v in pt.items() if k in COST_KEYS}
    params = {k: v for k, v in pt.items()
              if k not in COST_KEYS and k not in ENGINE_KEYS}
    t1 = time.time()
    eng = Engine(_W['mod'], _W['feed'], cash=cash,
                 cost=build_cost(jq_cost, cost_over), params=params)
    if benchmark:
        eng.set_benchmark(benchmark)
        eng._bench_cli = True
    curve = eng.run()
    m = summarize(curve, cash, bench=eng.bench, broker=eng.broker,
                  bench_base=eng.bench_base)
    rid = ''
    if archive:
        ns = SimpleNamespace(start=start, end=end, cash=cash)
        _, rid = registry.save(strategy_path, group, eng, m, ns, '',
                               time.time() - t1)
    return pt, {k: m.get(k) for k in SHOW}, rid, time.time() - t1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('strategy')
    ap.add_argument('--start', default='2019-01-01')
    ap.add_argument('--end', default='2026-06-30')
    ap.add_argument('--cash', type=float, default=500000)
    ap.add_argument('--grid', action='append', default=[], metavar='K=V1,V2',
                    help='扫描维度，可重复；多个维度取笛卡尔积')
    ap.add_argument('--benchmark', default=None)
    ap.add_argument('--jq-cost', action='store_true')
    ap.add_argument('--datalake', default=None)
    ap.add_argument('--group', default=None)
    ap.add_argument('--jobs', type=int, default=1,
                    help='并行进程数，默认 1（串行）。组合数少于 jobs 时并行无收益')
    ap.add_argument('--no-archive', action='store_true')
    a = ap.parse_args()

    grid = parse_grid(a.grid)
    if not grid:
        raise SystemExit('至少要给一个 --grid')
    keys = list(grid)
    combos = list(itertools.product(*(grid[k] for k in keys)))
    print('策略 %s | 扫描 %d 个组合: %s'
          % (os.path.basename(a.strategy), len(combos),
             ' × '.join('%s(%d)' % (k, len(grid[k])) for k in keys)))

    # feed 只建一次 —— 物化 bars 表是最慢的一步
    t0 = time.time()
    feed = PanelFeed(a.start, a.end, root=a.datalake)
    print('数据就绪 %d 个交易日 (%.1fs)' % (len(feed.trading_days), time.time() - t0))
    group = a.group or registry.derive_group(a.strategy)
    mod = load(a.strategy)

    jobs = [(dict(zip(keys, combo)), keys, dict(zip(keys, combo)).get('cash', a.cash),
             a.jq_cost, a.benchmark, group, a.strategy, not a.no_archive,
             a.start, a.end) for combo in combos]
    nproc = max(1, min(a.jobs, len(combos)))
    rows, done = [], 0

    def _emit(pt, m, rid, el):
        label = ' '.join('%s=%s' % (k, pt[k]) for k in keys)
        row = dict(pt); row.update(m); row['run_id'] = rid
        rows.append(row)
        print('  [%d/%d] %-40s 年化 %6.2f%%  回撤 %6.2f%%  (%.0fs)'
              % (len(rows), len(combos), label, (m.get('annual_return') or 0) * 100,
                 (m.get('max_drawdown') or 0) * 100, el))

    if nproc == 1:
        _init(a.start, a.end, a.datalake, a.strategy)     # 与并行路径走同一份代码
        for job in jobs:
            _emit(*_work(job))
    else:
        # 多进程：api 的模块级状态是每进程一份，所以子进程之间互不污染。
        # initializer 里建一次 feed，之后该 worker 的所有组合复用它。
        import concurrent.futures as cf
        print('  并行 %d 进程（feed 会在每个进程各物化一次）' % nproc)
        with cf.ProcessPoolExecutor(
                max_workers=nproc, initializer=_init,
                initargs=(a.start, a.end, a.datalake, a.strategy)) as ex:
            # 结果按完成顺序回来，不保证网格顺序；最终表格会重排回网格顺序
            for res in ex.map(_work, jobs):
                _emit(*res)

    df = pd.DataFrame(rows)
    for c in PCT:
        if c in df.columns:
            df[c] = (df[c].astype(float) * 100).round(2)
    for c in ('info_ratio', 'sharpe', 'turnover_per_year'):
        if c in df.columns:
            df[c] = df[c].astype(float).round(3)
    # 并行时结果按完成顺序返回，这里重排回网格顺序 —— 要看的是趋势，顺序不能乱
    if keys:
        order = {tuple(c): i for i, c in enumerate(combos)}
        df['_o'] = df.apply(lambda r: order.get(tuple(r[k] for k in keys), 0), axis=1)
        df = df.sort_values('_o').drop(columns='_o')
    pd.set_option('display.width', 220)
    pd.set_option('display.max_columns', 60)
    print('\n' + '=' * 100)
    # 保持网格顺序而不是按收益排序 —— 要看的是趋势，排序会把单调性打乱
    print(df.to_string(index=False))
    print('=' * 100)
    if not a.no_archive:
        print('每个组合已归档，可用 python3 runs.py diff <a> <b> 逐项对比')


if __name__ == '__main__':
    main()
