#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""启动归档查看服务。

    python3 serve.py                  # http://127.0.0.1:8770
    python3 serve.py --port 9000
    python3 serve.py --runs /vol/runs  # 归档在别的盘（也可用 ASSAY_RUNS）
    python3 serve.py --allow-backtest  # 允许网页触发回测（默认只读）
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from assay.server import serve       # noqa: E402
from assay import registry           # noqa: E402

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--host', default='127.0.0.1')
    ap.add_argument('--port', type=int, default=8770)
    ap.add_argument('--runs', default=None,
                    help='归档目录，默认 ASSAY_RUNS 环境变量或 <repo>/runs')
    ap.add_argument('--allow-backtest', action='store_true',
                    help='允许从网页触发回测（会起子进程）。默认只读')
    a = ap.parse_args()
    print('归档目录 %s' % registry.set_runs(a.runs))
    serve(a.host, a.port, allow_backtest=a.allow_backtest)
