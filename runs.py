#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""归档查询。

    python3 runs.py list                         列出全部回测
    python3 runs.py list --group 小市值           按分组过滤（前缀匹配，支持多层）
    python3 runs.py show  <run_id>               单次详情
    python3 runs.py diff  <run_id> <run_id>      两次并排对比
    python3 runs.py holdings <run_id> --date 2024-02-07
    python3 runs.py trades   <run_id> --top 20
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pandas as pd                       # noqa: E402

from assay import registry                # noqa: E402

pd.set_option('display.width', 200)
pd.set_option('display.max_columns', 50)

PCT = ('annual_return', 'max_drawdown', 'total_return', 'win_rate', 'volatility',
       'excess_annual', 'excess_return', 'excess_max_drawdown',
       'benchmark_return', 'benchmark_annual', 'alpha')


def _fmt(df):
    df = df.copy()
    for c in PCT:
        if c in df.columns:
            df[c] = (df[c].astype(float) * 100).round(2)
    return df


def cmd_list(a):
    df = registry.list_runs(a.group, a.strategy, a.limit)
    if df.empty:
        print('还没有归档的回测')
        return
    print(_fmt(df).to_string(index=False))


def cmd_show(a):
    meta = registry.read(a.run_id, 'meta')
    st = registry.read(a.run_id, 'stats')
    print('=' * 66)
    for k in ('run_id', 'group', 'strategy', 'strategy_path', 'code_sha256',
              'first_day', 'last_day', 'cash', 'trading_days', 'ran_at', 'elapsed_sec'):
        print('  %-14s %s' % (k, meta.get(k)))
    print('  %-14s %s' % ('cost', meta.get('cost')))
    if meta.get('params'):
        print('  %-14s %s' % ('params', meta['params']))
    fp = meta.get('data_fingerprint') or {}
    if fp:
        print('  %-14s %s' % ('data', fp.get('overall')))
        for name, d in (fp.get('parts') or {}).items():
            print('      %-8s %s  %d 文件 / %.1f MB / 最新 %s'
                  % (name, d['hash'], d['n_files'],
                     d['total_bytes'] / 1048576, d['newest_mtime']))
    print('-' * 66)
    for k in ('benchmark', 'total_return', 'annual_return', 'max_drawdown',
              'volatility', 'sharpe', 'sortino', 'calmar',
              'benchmark_return', 'benchmark_annual', 'excess_return', 'excess_annual',
              'excess_max_drawdown', 'info_ratio', 'alpha', 'beta',
              'years', 'n_trades', 'win_rate', 'profit_factor', 'avg_holding_days',
              'turnover_per_year', 'fee_paid', 'div_tax_paid',
              'max_weight_avg', 'max_weight_peak', 'hhi_avg',
              'n_delisted', 'n_rejects'):
        v = st.get(k)
        if v is None:
            continue
        print('  %-16s %s' % (k, round(float(v), 4) if isinstance(v, (int, float)) else v))
    print('=' * 66)
    rj = registry.read(a.run_id, 'rejects')
    if len(rj) and 'reason' in rj.columns:
        print('拒单原因分布：')
        print(rj['reason'].value_counts().to_string())


def cmd_diff(a):
    rows, metas = [], []
    for rid in (a.a, a.b):
        st = registry.read(rid, 'stats')
        m = registry.read(rid, 'meta')
        metas.append(m)
        st['code_sha'] = m['code_sha256'][:8]
        st['slippage'] = m['cost']['slippage']
        st['div_tax'] = m['cost']['dividend_tax']
        rows.append(st)
    keys = ['run_id', 'strategy', 'start', 'end', 'cash', 'code_sha', 'slippage',
            'div_tax', 'benchmark', 'annual_return', 'max_drawdown',
            'excess_annual', 'excess_max_drawdown', 'info_ratio', 'alpha', 'beta',
            'sharpe', 'turnover_per_year', 'n_trades', 'win_rate', 'div_tax_paid']
    df = pd.DataFrame([{k: r.get(k) for k in keys} for r in rows]).set_index('run_id').T
    print(df.to_string())
    # 归因的第一个岔路口：差异来自【代码】、【数据】还是【参数】。
    # 这三件事不该靠回忆区分。
    print()
    if rows[0]['code_sha'] != rows[1]['code_sha']:
        print('⚠ 策略代码【不同】—— 差异可能来自代码。')
    else:
        print('✓ 策略代码相同。')
    fa, fb = (metas[0].get('data_fingerprint') or {}), (metas[1].get('data_fingerprint') or {})
    if not fa or not fb:
        print('… 有一次没有数据指纹（旧归档），无法判断数据是否变过。')
    elif fa.get('overall') != fb.get('overall'):
        diff = [n for n in (fa.get('parts') or {})
                if (fa['parts'].get(n) or {}).get('hash')
                != ((fb.get('parts') or {}).get(n) or {}).get('hash')]
        print('⚠ 数据【不同】—— 变化的部分: %s。同一策略跨数据版本比较无意义。'
              % ', '.join(diff))
    else:
        print('✓ 数据版本相同。')
    pa, pb = metas[0].get('params') or {}, metas[1].get('params') or {}
    if pa != pb:
        print('· 策略参数不同: %s  vs  %s' % (pa or '{}', pb or '{}'))


def cmd_holdings(a):
    df = registry.read(a.run_id, 'holdings')
    if a.date:
        df = df[df['date'].astype(str) == a.date]
    print(df.sort_values(['date', 'weight'], ascending=[True, False]).to_string(index=False))


def cmd_trades(a):
    df = registry.read(a.run_id, 'trades')
    df = df.sort_values('ret', ascending=False)
    print('最好 %d 笔：' % a.top)
    print(df.head(a.top).to_string(index=False))
    print('\n最差 %d 笔：' % a.top)
    print(df.tail(a.top).to_string(index=False))
    print('\n按卖出原因：')
    print(df.groupby('reason').agg(笔数=('ret', 'size'), 平均收益=('ret', 'mean')).to_string())


def main():
    # --runs 做成 parent parser：argparse 的顶层可选参数必须出现在子命令【之前】，
    # 而 `runs.py list --runs X` 才是自然写法。挂到每个子命令上，两种位置都认。
    common = argparse.ArgumentParser(add_help=False)
    # ★ default 必须是 SUPPRESS 不能是 None：parents 会让【子 parser 也带这个参数】，
    #   而子 parser 解析时会用自己的 default 覆盖顶层已经解析出的值 ——
    #   `runs.py --runs X list` 会静默退回默认目录。SUPPRESS 表示"没给就不写进
    #   namespace"，两个位置才不会互相覆盖。
    common.add_argument('--runs', default=argparse.SUPPRESS,
                        help='归档目录，默认 ASSAY_RUNS 或 <repo>/runs')
    ap = argparse.ArgumentParser(parents=[common])
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('list', parents=[common]); p.add_argument('--group'); p.add_argument('--strategy')
    p.add_argument('--limit', type=int, default=50); p.set_defaults(f=cmd_list)
    p = sub.add_parser('show', parents=[common]); p.add_argument('run_id'); p.set_defaults(f=cmd_show)
    p = sub.add_parser('diff', parents=[common]); p.add_argument('a'); p.add_argument('b'); p.set_defaults(f=cmd_diff)
    p = sub.add_parser('holdings', parents=[common]); p.add_argument('run_id'); p.add_argument('--date')
    p.set_defaults(f=cmd_holdings)
    p = sub.add_parser('trades', parents=[common]); p.add_argument('run_id'); p.add_argument('--top', type=int, default=10)
    p.set_defaults(f=cmd_trades)
    a = ap.parse_args()
    registry.set_runs(getattr(a, 'runs', None))
    a.f(a)


if __name__ == '__main__':
    main()
