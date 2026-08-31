#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""校验 jq/strategies/*.py 与本地归档的关联。

为什么要有这个文件：关联写成注释是留不住的 —— 本地策略一改、标星一换，
注释还在那儿，人却不会去改它。所以 LOCAL_PORT 是【断言】：

    · run_id 在不在归档里
    · params 与归档的 meta.json 是否逐字段一致
    · metrics 与归档的 stats.json 是否吻合（容差 0.015）
    · 语法能编译

[!] 不校验「JQ 侧常量 == 本地默认值」—— 那要解析 JQ 代码里的业务常量，
    脆且没必要。改成把逐项对照写在 LOCAL_PORT['aligned'] 里（人可读），
    本文件只保证【指向的那次回测是真实存在且参数没漂】。

用法：python3 jq/check.py
"""
import ast
import glob
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

TOL = 0.015          # 与 qmt/check.py 一致


def _local_port(src, path):
    """从源码里取出 LOCAL_PORT 字面量。用 ast.literal_eval 而不是 exec ——
    这文件是要粘进聚宽跑的，不能因为 import 不到 jqdata 就校验不了。"""
    mod = ast.parse(src)
    for node in mod.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name) \
                and node.targets[0].id == 'LOCAL_PORT':
            return ast.literal_eval(node.value)
    return None


def _find_run(run_id):
    hits = glob.glob(os.path.join(ROOT, 'runs', '**', run_id), recursive=True)
    return hits[0] if hits else None


def check(path):
    errs = []
    rel = os.path.relpath(path, ROOT)
    src = open(path, encoding='utf-8').read()
    try:
        ast.parse(src)
    except SyntaxError as e:
        return ['%s 语法错误: 第 %s 行 %s' % (rel, e.lineno, e.msg)], 0

    try:
        lp = _local_port(src, path)
    except Exception as e:                                  # noqa: BLE001
        return ['%s LOCAL_PORT 解析失败: %s' % (rel, e)], 0
    if not lp:
        return ['%s 缺 LOCAL_PORT 声明（无法关联到本地策略）' % rel], 0

    for k in ('strategy', 'run_id', 'params', 'metrics', 'backtest', 'aligned'):
        if k not in lp:
            errs.append('%s LOCAL_PORT 缺 %s' % (rel, k))
    if errs:
        return errs, 0

    sp = os.path.join(ROOT, lp['strategy'])
    if not os.path.exists(sp):
        errs.append('%s 指向的本地策略不存在: %s' % (rel, lp['strategy']))

    d = _find_run(lp['run_id'])
    if d is None:
        errs.append('%s run_id 不在归档里: %s' % (rel, lp['run_id']))
        return errs, 0

    meta = json.load(open(os.path.join(d, 'meta.json'), encoding='utf-8'))
    if os.path.relpath(meta.get('strategy_path', ''), '.') != lp['strategy']:
        errs.append('%s 归档的 strategy_path=%s 与 LOCAL_PORT 的 %s 不符'
                    % (rel, meta.get('strategy_path'), lp['strategy']))
    got = meta.get('params') or {}
    if got != lp['params']:
        errs.append('%s 参数不符：归档 %s / LOCAL_PORT %s' % (rel, got, lp['params']))

    # 区间与本金也要对上 —— 否则 JQ 上按 backtest 块设置出来的数字不可比
    bt = lp['backtest']
    for key, mk in (('start', 'first_day'), ('end', 'last_day'), ('cash', 'cash')):
        exp, act = bt.get(key), meta.get(mk)
        if key == 'cash':
            ok = abs(float(exp) - float(act)) < 1
        else:
            ok = str(exp) == str(act)
        if not ok:
            errs.append('%s backtest.%s=%s 与归档 %s=%s 不符' % (rel, key, exp, mk, act))

    st = json.load(open(os.path.join(d, 'stats.json'), encoding='utf-8'))

    def pct(v):
        return v * 100 if abs(v) < 1.5 else v

    for k, sk in (('annual', 'annual_return'), ('max_drawdown', 'max_drawdown'),
                  ('sharpe', 'sharpe')):
        exp = lp['metrics'].get(k)
        if exp is None:
            continue
        act = st.get(sk)
        if act is None:
            errs.append('%s stats.json 缺 %s' % (rel, sk))
            continue
        a = act if k == 'sharpe' else pct(act)
        if abs(float(exp) - float(a)) > TOL:
            errs.append('%s %s 不符：LOCAL_PORT %.4f / 归档 %.4f' % (rel, k, exp, a))

    return errs, len(lp.get('aligned') or {})


def main():
    files = sorted(glob.glob(os.path.join(ROOT, 'jq', 'strategies', '*.py')))
    if not files:
        print('jq/strategies/ 下没有文件')
        return 1
    bad = 0
    for f in files:
        errs, n_align = check(f)
        rel = os.path.relpath(f, ROOT)
        if errs:
            bad += 1
            print('  x %s' % rel)
            for e in errs:
                print('      - %s' % e)
        else:
            print('  v %s  关联核对通过（选股参数逐项对照 %d 条）' % (rel, n_align))
    print('\n%d 个文件，%d 个有问题' % (len(files), bad))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
