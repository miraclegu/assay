#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""回测归档。每次跑完把「能重现这次结果」的一切落盘，供复盘与查询。

## 分组：从策略文件路径推导，不另外声明

    strategies/小市值/froec.py        -> group = 小市值
    strategies/小市值/froec/v3d.py    -> group = 小市值/froec
    strategies/红利/hl-base.py        -> group = 红利

**为什么不在策略里写 GROUP 常量**：那会有两份真相（目录一份、常量一份），
迟早不一致。目录结构本身就是分组，用它做唯一来源。
需要例外时用 --group 覆盖，但那是例外不是常态。

## 归档内容

    runs/<group>/<strategy>/<run_id>/
      meta.json         策略名/分组/区间/资金/成本参数/代码 SHA256/耗时
      strategy.py       策略代码【逐字节快照】—— 代码会改，结果不会自己解释自己
      stats.json        最终统计
      equity.parquet    每日账户：cash / positions_value / total_value / n_positions
      holdings.parquet  每日逐持仓：shares / value / weight / 浮动盈亏
      trades.parquet    每笔平仓：进出价、持有天数、盈亏、红利税、卖出原因
      rejects.parquet   拒单：日期/代码/方向/原因 —— 失败必须留痕，不静默
      run.log           完整日志

run_id = 时间戳 + 代码哈希前 6 位。**带代码哈希是为了区分「同一份代码重跑」
和「代码改了再跑」** —— 只看时间戳分不出来，而这正是归因时最需要知道的事。
"""
import hashlib
import json
import os
import time
from datetime import datetime

import duckdb
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNS = os.path.join(ROOT, 'runs')
STRAT_DIR = os.path.join(ROOT, 'strategies')


def derive_group(strategy_path):
    """由策略文件在 strategies/ 下的相对位置推导分组（支持任意层数）。"""
    ap = os.path.abspath(strategy_path)
    try:
        rel = os.path.relpath(ap, STRAT_DIR)
    except ValueError:
        return '_external'
    parts = rel.split(os.sep)
    if parts[0] == '..':
        return '_external'
    return '/'.join(parts[:-1]) if len(parts) > 1 else '_root'


def _w(df_or_rows, path):
    df = df_or_rows if isinstance(df_or_rows, pd.DataFrame) else pd.DataFrame(df_or_rows)
    if df.empty:                      # 空表也要落盘：区分「没发生」与「没记录」
        df = pd.DataFrame(columns=df.columns if len(df.columns) else ['_empty'])
    df.to_parquet(path, index=False, compression='zstd')
    return len(df)


def save(strategy_path, group, engine, stats, args, log_text, elapsed):
    code = open(strategy_path, encoding='utf-8').read()
    sha = hashlib.sha256(code.encode('utf-8')).hexdigest()
    name = os.path.splitext(os.path.basename(strategy_path))[0]
    run_id = '%s-%s' % (datetime.now().strftime('%Y%m%d-%H%M%S'), sha[:6])
    # 目录创建必须【原子】：参数扫描会在同一秒内跑完多个，并行时更是多进程同时写。
    # 先 exists 再 makedirs 是 check-then-act 竞态 —— 两个进程会拿到同一个名字。
    # 用 makedirs(exist_ok=False) + 捕获 FileExistsError 递增，创建成功即独占。
    base = os.path.join(RUNS, group, name, run_id)
    os.makedirs(os.path.dirname(base), exist_ok=True)
    d, i = base, 1
    while True:
        try:
            os.makedirs(d, exist_ok=False)
            break
        except FileExistsError:
            i += 1
            d = '%s-%d' % (base, i)
    run_id = os.path.basename(d)

    with open(os.path.join(d, 'strategy.py'), 'w', encoding='utf-8') as f:
        f.write(code)
    with open(os.path.join(d, 'run.log'), 'w', encoding='utf-8') as f:
        f.write(log_text)

    c = engine.cost
    meta = {
        'run_id': run_id, 'group': group, 'strategy': name,
        'strategy_path': os.path.relpath(os.path.abspath(strategy_path), ROOT),
        'code_sha256': sha,
        'start': str(args.start), 'end': str(args.end), 'cash': args.cash,
        'trading_days': len(engine.feed.trading_days),
        'first_day': str(engine.feed.trading_days[0]),
        'last_day': str(engine.feed.trading_days[-1]),
        'datalake': engine.feed.root,
        'data_fingerprint': engine.feed.fingerprint(),
        'cost': c.to_dict(),
        'params': engine.params,
        'elapsed_sec': round(elapsed, 1),
        'ran_at': datetime.now().isoformat(timespec='seconds'),
    }
    json.dump(meta, open(os.path.join(d, 'meta.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=2)

    b = engine.broker
    st = dict(stats)
    st.update({
        'benchmark': engine.bench_code,
        'run_id': run_id, 'group': group, 'strategy': name,
        'start': meta['first_day'], 'end': meta['last_day'], 'cash': args.cash,
        'n_trades': len(b.trades), 'n_rejects': len(b.rejects),
        'div_tax_paid': b.div_tax_paid, 'n_delisted': len(b.delisted),
    })
    json.dump(st, open(os.path.join(d, 'stats.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=2, default=str)

    if engine.bench:
        _w([{'date': x[0], 'close': x[1]} for x in engine.bench],
           os.path.join(d, 'benchmark.parquet'))
    _w(engine.daily, os.path.join(d, 'equity.parquet'))
    _w(engine.holdings, os.path.join(d, 'holdings.parquet'))
    _w(b.trades, os.path.join(d, 'trades.parquet'))
    _w([{'date': r[0], 'code': r[1], 'side': r[2], 'reason': r[3]}
        for r in b.rejects], os.path.join(d, 'rejects.parquet'))
    return d, run_id


# ---------------- 查询 ----------------
def _con():
    return duckdb.connect(':memory:')


def list_runs(group=None, strategy=None, limit=50):
    """跨全部归档查统计。用 DuckDB 直接 glob JSON —— 不另建索引库，
    避免「索引与实际不一致」这类需要额外维护的状态。"""
    pat = os.path.join(RUNS, '**', 'stats.json')
    if not os.path.isdir(RUNS):
        return pd.DataFrame()
    where = ['1=1']
    if group:
        where.append("group_ LIKE '%s%%'" % group)
    if strategy:
        where.append("strategy = '%s'" % strategy)
    sql = """
      SELECT run_id, "group" AS group_, strategy, start, "end",
             cash, annual_return, max_drawdown, excess_annual, info_ratio,
             turnover_per_year, n_trades, win_rate
      FROM read_json_auto('%s', union_by_name=true)
    """ % pat
    df = _con().execute(
        'SELECT * FROM (%s) WHERE %s ORDER BY run_id DESC LIMIT %d'
        % (sql, ' AND '.join(where), limit)).df()
    return df


def load(run_id):
    """按 run_id 定位归档目录。"""
    for dp, dns, fns in os.walk(RUNS):
        if os.path.basename(dp) == run_id and 'meta.json' in fns:
            return dp
    return None


def read(run_id, what='equity'):
    d = load(run_id)
    if d is None:
        raise SystemExit('找不到 run_id=%s' % run_id)
    if what in ('meta', 'stats'):
        return json.load(open(os.path.join(d, what + '.json'), encoding='utf-8'))
    return pd.read_parquet(os.path.join(d, what + '.parquet'))
