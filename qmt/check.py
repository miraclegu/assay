#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""QMT 策略体检：编码 / 编译 / **与本地策略的关联**。

    python3 qmt/check.py

三件事，缺一件这些文件就会悄悄烂掉：

1) **按各自声明的编码解码再编译**
   QMT 内置编辑器要 GBK。曾经 roec_weekly_rotation.py 磁盘上是 UTF-8
   却声明 `#coding:gbk`，粘进 QMT 直接 SyntaxError —— 而在 Mac 上用编辑器
   打开一切正常，肉眼完全看不出来。只有「按声明解码」才能暴露。

2) **LOCAL_PORT 声明的关联必须成立**
   本地策略文件在不在、run_id 在不在归档里、归档记的策略/参数是否与
   声明一致、基线指标是否与 stats.json 吻合。
   —— 关联写成注释是留不住的：本地策略一改、标星一换，注释还在那儿，
   人却不会去改它。做成断言才有意义。

3) **QMT 侧常量与默认 profile 一致**
   froec.py 的 TRADED_UNIVERSE / STOP_LOSS 等，必须等于默认 profile 声明的值，
   否则「默认参数 = 回测参数」这句话就是假的。
"""
import ast
import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
STRAT = os.path.join(HERE, 'strategies')
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)


def declared_encoding(raw):
    """PEP 263：编码声明只在前两行找。"""
    for line in raw.split(b'\n')[:2]:
        m = re.search(rb'coding[:=]\s*([-\w.]+)', line)
        if m:
            return m.group(1).decode('ascii')
    return 'utf-8'


def _consts(tree):
    """取模块级的字面量常量（QMT 参数区就是这种写法）。"""
    out = {}
    for n in tree.body:
        if isinstance(n, ast.Assign) and len(n.targets) == 1 \
                and isinstance(n.targets[0], ast.Name):
            try:
                out[n.targets[0].id] = ast.literal_eval(n.value)
            except (ValueError, SyntaxError):
                pass
    return out


def _find_run(run_id):
    from assay import registry
    for dp, _dn, fns in os.walk(registry.RUNS):
        if 'meta.json' in fns and os.path.basename(dp) == run_id:
            with open(os.path.join(dp, 'meta.json'), encoding='utf-8') as f:
                meta = json.load(f)
            with open(os.path.join(dp, 'stats.json'), encoding='utf-8') as f:
                stats = json.load(f)
            return meta, stats
    return None, None


def check_file(path):
    rel = os.path.relpath(path, ROOT)
    raw = open(path, 'rb').read()
    enc = declared_encoding(raw)
    errs = []
    try:
        src = raw.decode(enc)
    except UnicodeDecodeError as e:
        return rel, enc, ['声明 %s 但字节不是它：%s' % (enc, str(e)[:70])], 0
    try:
        tree = ast.parse(src, path)
    except SyntaxError as e:
        return rel, enc, ['语法错误 行%s：%s' % (e.lineno, e.msg)], 0

    consts = _consts(tree)
    lp = consts.get('LOCAL_PORT')
    if lp is None:
        return rel, enc, ['缺 LOCAL_PORT 声明（无法关联到本地策略）'], 0

    profiles = lp.get('profiles') or {}
    for name, p in sorted(profiles.items()):
        sp = os.path.join(ROOT, p['strategy'])
        if not os.path.exists(sp):
            errs.append('[%s] 本地策略不存在: %s' % (name, p['strategy']))
        meta, stats = _find_run(p['run_id'])
        if meta is None:
            errs.append('[%s] 归档里找不到 run_id %s' % (name, p['run_id']))
            continue
        want_strat = os.path.splitext(os.path.basename(p['strategy']))[0]
        if meta.get('strategy') != want_strat:
            errs.append('[%s] run %s 记的是策略 %s，声明的是 %s'
                        % (name, p['run_id'], meta.get('strategy'), want_strat))
        if dict(meta.get('params') or {}) != dict(p.get('local') or {}):
            errs.append('[%s] 参数不一致：归档 %s / 声明 %s'
                        % (name, meta.get('params'), p.get('local')))
        for k, key in (('annual', 'annual_return'), ('max_drawdown', 'max_drawdown'),
                       ('sharpe', 'sharpe')):
            want = (p.get('metrics') or {}).get(k)
            if want is None:
                continue
            got = stats.get(key)
            got = got * 100 if key != 'sharpe' else got
            if got is None or abs(got - want) > 0.015:
                errs.append('[%s] %s 声明 %.2f 但归档是 %.2f'
                            % (name, k, want, got if got is not None else float('nan')))

    if lp.get('kind') == 'port':
        # 一个文件一个 profile：QMT 侧常量必须等于它，
        # 这就是「粘进去就能跑、不用手改开关」的保证。
        dft = list(profiles)[0] if profiles else None
        if dft is None:
            errs.append('没有 profile')
        else:
            for k, v in (profiles[dft].get('qmt') or {}).items():
                if k not in consts:
                    errs.append('QMT 侧缺常量 %s' % k)
                elif consts[k] != v:
                    errs.append('QMT 常量 %s = %r，但默认 profile %s 声明 %r —— '
                                '「默认参数=回测参数」不成立' % (k, consts[k], dft, v))
    elif lp.get('kind') == 'executor':
        ep = os.path.join(ROOT, lp.get('exporter', ''))
        if not os.path.exists(ep):
            errs.append('导出器不存在: %s' % lp.get('exporter'))
    return rel, enc, errs, len(profiles)


def main():
    # ★ 先验「磁盘上的策略文件是否与模板同步」—— 这些文件是 gen.py 生成的，
    #   手工改会在下次 gen 时被无声覆盖，所以必须先拦住。
    import subprocess
    r = subprocess.run([sys.executable, os.path.join(HERE, 'gen.py'), '--check'],
                       capture_output=True, text=True)
    sys.stdout.write(r.stdout)
    sync_bad = r.returncode != 0

    files = sorted(glob.glob(os.path.join(STRAT, '*.py')))
    bad = 0
    for p in files:
        rel, enc, errs, n = check_file(p)
        if errs:
            bad += 1
            print('  x %-24s [%s]' % (rel, enc))
            for e in errs:
                print('      - %s' % e)
        else:
            print('  v %-24s [%s]  关联 %d 个 profile 全部核对通过' % (rel, enc, n))
    print('\n%d 个文件，%d 个有问题%s'
          % (len(files), bad, '；另有与模板不同步' if sync_bad else ''))
    sys.exit(1 if (bad or sync_bad) else 0)


if __name__ == '__main__':
    main()
