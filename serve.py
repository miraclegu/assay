#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""启动归档查看服务。

    python3 serve.py                  # http://127.0.0.1:8770
    python3 serve.py --port 9000
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from assay.server import serve       # noqa: E402

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--host', default='127.0.0.1')
    ap.add_argument('--port', type=int, default=8770)
    a = ap.parse_args()
    serve(a.host, a.port)
