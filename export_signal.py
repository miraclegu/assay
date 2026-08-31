#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把某条策略的目标持仓导出成 CSV，交给 QMT 执行或回测。

两种模式：

  默认（实盘）      只导【最后一个交易日】的目标组合，配 qmt/_tpl/signal_executor.py
  --series（回测）  导【全历史调仓事件序列】，配 qmt/_tpl/signal_replay.py

## --series 为什么按「调仓事件」而不是「每日持仓」

每日权重会随价格漂移。若照每日权重让 QMT 天天调仓，会造出大量【虚假换手】，
测出来的滑点/费用全是假的。所以只在【本地引擎真正成交的那些天】发出目标。

识别成交日的办法：**逐日比对持仓股数**。股数不随价格漂移，只有成交才变
（送股/转增也会变，但那是真实的股本事件，QMT 侧自己也会处理）。
比对权重是错的 —— 权重每天都在动。

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
    ap.add_argument('--series', action='store_true',
                    help='导出全历史调仓事件序列（供 QMT 回测回放），而不是只导末日')
    a = ap.parse_args()
    end = a.end or datetime.date.today().isoformat()

    feed = PanelFeed(a.start, end, root=a.datalake)
    eng = Engine(load(a.strategy), feed, cash=a.cash, cost=Cost(),
                 params=parse_params(a.param))
    eng.run()

    hd = pd.DataFrame(eng.holdings)          # 引擎逐日持仓快照

    if a.series:
        return _write_series(hd, a, eng, feed)

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


def _rebalance_days(hd, all_days):
    """从逐日持仓快照里挑出【真正成交的日子】。

    判据是逐日的 {code: shares} 是否变化 —— 股数只有成交才变，权重每天都在漂。

    [!] 日期列表必须用【全部交易日】而不是 hd 里的日期：完全空仓的那天
        在 holdings 快照里【一行都没有】，若只遍历 hd 的日期，
        「清仓」这个事件就识别不出来，回放时会变成「保持不动」——
        红利不空仓所以看不出问题，froec 止损后会空仓，一定会踩到。
    """
    by_day = {}
    for _, r in hd.iterrows():
        by_day.setdefault(r['date'], {})[r['code']] = round(float(r['shares']), 4)
    prev, out = None, []
    for d in all_days:
        cur = by_day.get(d, {})
        if prev is not None and cur != prev:
            out.append(d)
        elif prev is None and cur:
            out.append(d)                    # 首次建仓
        prev = cur
    return out


def _write_series(hd, a, eng, feed):
    if hd.empty:
        print('全程无持仓 —— 检查起点与参数')
        return
    all_days = [r['date'] for r in eng.daily] or sorted(hd['date'].unique())
    days = _rebalance_days(hd, all_days)
    rows, n_flat = [], 0
    for d in days:
        sub = hd[hd['date'] == d].sort_values('weight', ascending=False)
        if sub.empty:
            # 清仓事件：显式写一行 CASH，否则回放侧无法区分「清仓」和「无事件」
            rows.append((d, 'CASH', 'CASH', 0.0))
            n_flat += 1
            continue
        for _, r in sub.iterrows():
            rows.append((d, r['code'], jq_to_qmt(r['code']), float(r['weight'])))

    with open(a.out, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['date', 'jq_code', 'qmt_code', 'weight'])
        for d, jq, qmt, wt in rows:
            w.writerow([str(d).replace('-', ''), jq, qmt, '%.6f' % wt])

    print('策略 %s   %s ~ %s' % (os.path.basename(a.strategy),
                                 all_days[0], all_days[-1]))
    print('交易日 %d 天 -> 调仓事件 %d 次（%.1f%% 的天数发生成交）%s'
          % (len(all_days), len(days), 100.0 * len(days) / len(all_days),
             ('，其中 %d 次是清仓' % n_flat) if n_flat else ''))
    print('共 %d 行；每次事件平均 %.1f 只' % (len(rows), len(rows) / len(days)))
    # 事件间隔分布。[!] 别用中位数去推调仓频率 —— 事件里【混着两类】：
    #   ① 定期调仓（红利是 run_monthly 月频、froec 是周频）
    #   ② 盘中事件（炸板离场 check_limit_up、止损 stop_check、组合止损 pf_check）
    # 红利实测一年 24~29 次 = 12 次月度调仓 + 12~17 次盘中事件，
    # 混起来的间隔中位数是 7 天，看着像周频，其实不是。
    gaps = [(days[i] - days[i - 1]).days for i in range(1, len(days))]
    if gaps:
        import collections
        gs = sorted(gaps)
        print('事件间隔（自然日）中位 %d，p10 %d，p90 %d，最大 %d'
              % (gs[len(gs) // 2], gs[len(gs) // 10],
                 gs[int(len(gs) * 0.9)], gs[-1]))
        print('  间隔计数 %s' % collections.Counter(gaps).most_common(6))
        print('  [!] 事件 = 定期调仓 + 盘中事件（炸板离场/止损），'
              '别用中位间隔去推调仓频率')
        yrs = collections.Counter(str(d)[:4] for d in days)
        print('  每年事件数 %s' % dict(sorted(yrs.items())))
    print('已写出 %s' % a.out)
    print()
    print('QMT 侧用 qmt/strategies/*_replay.py 回放（按 date 列匹配当日）。')


if __name__ == '__main__':
    main()
