#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把某条策略【今天该持有什么】导出成 CSV，交给 QMT 执行。

为什么不把策略整条移植到 QMT：
  红利指数增强要分红历史 + 252 日 beta 回归，sgmspeg_v0b 要 jqfactor 近似
  （Barra 式 5 年回归斜率）。在 QMT 上重新推导这些，等于用一套没核对过的
  字段名和 as-of 语义重造因子链 —— 而本仓库的全部结论都建立在
  「双键 as-of（pub_date + change_date）」这套已验证的口径上。
  重造一遍最可能的结果是【看着能跑、数字悄悄是错的】。
  所以：因子与选股留在本地（口径已验证），QMT 只做执行。

    python3 export_signal.py strategies/红利/红利指数增强.py \
        --param div_method=fiscal_year --cash 1000000 -o signal.csv

原理：用 run.py 同一套引擎跑到 --end（默认今天），取【最后一个交易日的
持仓快照】作为目标组合。它就是最近一次调仓的结果，不需要改任何策略代码。

[!] 导出的是「按最后一个交易日收盘计的权重」。真正下单时价格已经变了，
    所以 QMT 侧按【权重】而不是股数来调仓。
"""
import argparse
import csv
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd                          # noqa: E402

from assay.broker import Cost                # noqa: E402
from assay.engine import Engine              # noqa: E402
from assay.feed import PanelFeed             # noqa: E402
from run import load, parse_params           # noqa: E402


def jq_to_qmt(code):
    """000001.XSHE -> 000001.SZ ; 600000.XSHG -> 600000.SH"""
    num, _, mkt = code.partition('.')
    return '%s.%s' % (num, 'SZ' if mkt.upper() == 'XSHE' else 'SH')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('strategy')
    ap.add_argument('--start', default='2016-01-01',
                    help='起点。策略需要历史来建仓与滚动，别设太近')
    ap.add_argument('--end', default=None, help='默认今天')
    ap.add_argument('--cash', type=float, default=1000000)
    ap.add_argument('--param', action='append', default=[], metavar='K=V')
    ap.add_argument('--datalake', default=None)
    ap.add_argument('-o', '--out', default='signal.csv')
    a = ap.parse_args()
    end = a.end or datetime.date.today().isoformat()

    feed = PanelFeed(a.start, end, root=a.datalake)
    eng = Engine(load(a.strategy), feed, cash=a.cash, cost=Cost(),
                 params=parse_params(a.param))
    eng.run()

    hd = pd.DataFrame(eng.holdings)          # 引擎逐日持仓快照
    if hd.empty:
        print('末态无持仓 —— 该策略当前空仓，或起点太近来不及建仓')
        rows, asof = [], feed.trading_days[-1]
    else:
        asof = hd['date'].max()
        rows = [{'code': r['code'], 'weight': float(r['weight'])}
                for _, r in hd[hd['date'] == asof].iterrows()]

    rows.sort(key=lambda r: -r['weight'])
    with open(a.out, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['asof', 'jq_code', 'qmt_code', 'weight'])
        for r in rows:
            w.writerow([asof, r['code'], jq_to_qmt(r['code']),
                        '%.6f' % r['weight']])
    tot = sum(r['weight'] for r in rows)
    print('策略 %s' % os.path.basename(a.strategy))
    print('数据截至 %s，目标组合 %d 只，合计权重 %.2f%%（其余为现金）'
          % (asof, len(rows), tot * 100))
    for r in rows:
        print('  %-12s %-11s %6.2f%%' % (r['code'], jq_to_qmt(r['code']),
                                         r['weight'] * 100))
    print('已写出 %s' % a.out)


if __name__ == '__main__':
    main()
