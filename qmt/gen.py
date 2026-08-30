#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按 profile 生成 qmt/strategies/ 下的 QMT 策略文件。

    python3 qmt/gen.py            # 生成
    python3 qmt/gen.py --check    # 只校验磁盘上的与生成结果是否一致

为什么要生成而不是手写 5 个文件：
  QMT 里【一个策略粘一个文件】，不能 import 共享模块 —— 所以 froec 那三个
  配置必然是三份 1200 行的拷贝。手工维护三份拷贝，改一处漏两处只是时间问题。
  这里把重复变成【生成 + 校验】：模板只有一份，磁盘上的文件必须与模板
  逐字节一致，check.py 会验。

为什么不干脆用一个文件加开关：
  那样粘进 QMT 后还得手改开关，「默认参数 = 回测参数」这句话就只对其中
  一个配置成立，其余全靠人记。一个配置一个文件，粘完就能跑。
"""
import argparse
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TPL = os.path.join(HERE, '_tpl')
OUT = os.path.join(HERE, 'strategies')

# ---------------------------------------------------------------- profile 表
# 每条 = 一个本地标星配置 -> 一个 QMT 文件。
# 'set' 里的键必须是模板参数区里已存在的模块级常量名（gen 会逐行改写它们）。
PROFILES = [
    dict(out='froec.py', tpl='froec.py',
         title='FROEC（聚宽原版口径 + 修掉 689 CDR 漏网）',
         strategy='strategies/小市值/froec.py',
         run_id='20260828-205911-486094',
         local={'kcb_688_only': 0},
         metrics={'annual': 36.38, 'max_drawdown': 46.84, 'sharpe': 1.21},
         set={'TRADED_UNIVERSE': False, 'STOP_LOSS': 0.0, 'STOP_INTRADAY': True}),
    dict(out='froec_traded.py', tpl='froec.py',
         title='FROEC-TRADED（宇宙 = 决策日实际有成交的票）',
         strategy='strategies/小市值/froec_traded.py',
         run_id='20260828-205938-4926f5',
         local={},
         metrics={'annual': 39.50, 'max_drawdown': 47.10, 'sharpe': 1.28},
         set={'TRADED_UNIVERSE': True, 'STOP_LOSS': 0.0, 'STOP_INTRADAY': True}),
    dict(out='froec_traded_stop35.py', tpl='froec.py',
         title='FROEC-TRADED + 盘中 -35% 固定止损【当前标星】',
         strategy='strategies/小市值/froec_traded.py',
         run_id='20260829-170945-4926f5',
         local={'stop_loss': 0.35, 'stop_intraday': 1},
         metrics={'annual': 40.87, 'max_drawdown': 38.54, 'sharpe': 1.33},
         set={'TRADED_UNIVERSE': True, 'STOP_LOSS': 0.35, 'STOP_INTRADAY': True}),
    dict(out='hongli_index_plus.py', tpl='signal_executor.py',
         title='红利指数增强（信号执行：选股在本地，QMT 只下单）',
         strategy='strategies/红利/红利指数增强.py',
         run_id='20260828-210010-adcde3',
         local={'div_method': 'fiscal_year'},
         metrics={'annual': 20.07, 'max_drawdown': 17.95, 'sharpe': 1.30},
         set={'SIGNAL_PATH': r'D:\work\finacial\signal_hongli.csv'},
         export=('python3 export_signal.py strategies/红利/红利指数增强.py '
                 '--param div_method=fiscal_year --cash 1000000 '
                 '-o signal_hongli.csv')),
    dict(out='sgmspeg_v0b.py', tpl='signal_executor.py',
         title='SG-MS-PEG-HL v0b（信号执行：选股在本地，QMT 只下单）',
         strategy='strategies/小市值/sgmspeg_v0b.py',
         run_id='20260828-205350-a76665',
         local={},
         metrics={'annual': 32.68, 'max_drawdown': 52.26, 'sharpe': 1.08},
         set={'SIGNAL_PATH': r'D:\work\finacial\signal_v0b.csv'},
         export=('python3 export_signal.py strategies/小市值/sgmspeg_v0b.py '
                 '--cash 1000000 -o signal_v0b.csv')),
]


def _fmt(v):
    if isinstance(v, bool):
        return 'True' if v else 'False'
    if isinstance(v, str):
        return "r'%s'" % v if '\\' in v else "'%s'" % v
    return repr(v)


def _rewrite_const(src, name, value):
    """改写模板参数区里 `NAME = ...` 那一行，保留行内注释与对齐。"""
    out, hit = [], 0
    for line in src.split('\n'):
        # ★ 只认【零缩进】的模块级赋值。用 lstrip 匹配会误伤 docstring 里
        #   "STOP_INTRADAY=True 用当日最低价判定" 这种说明文字（真踩过）。
        if line[:1] not in (' ', '\t') and line.startswith(name) and '=' in line:
            lhs, _, rest = line.partition('=')
            if lhs.strip() == name:
                cmt = ''
                # 保留 # 注释（值里不会有 #，参数区都是字面量）
                if '#' in rest:
                    cmt = '  ' + rest[rest.index('#'):].strip()
                out.append('%s= %s%s' % (lhs, _fmt(value), cmt))
                hit += 1
                continue
        out.append(line)
    if hit != 1:
        raise SystemExit('模板里 %s 命中 %d 次（应为 1）' % (name, hit))
    return '\n'.join(out)


def _local_port(p):
    kind = 'port' if p['tpl'] == 'froec.py' else 'executor'
    L = []
    L.append('#============================== 与本地策略的关联 ==============================')
    L.append('# [!] 这不是注释，是【可校验的声明】：python3 qmt/check.py 会拿它去核对')
    L.append('#     本地策略在不在、run_id 在不在归档、参数对不对得上、')
    L.append('#     基线指标与 stats.json 是否一致、以及本文件是否与模板逐字节同步。')
    L.append('#')
    L.append('# 本文件 = %s' % p['title'])
    L.append('# 本地   : %s' % p['strategy'])
    L.append('# 归档   : %s   年化 %.2f%% / 回撤 %.2f%% / 夏普 %.2f'
             % (p['run_id'], p['metrics']['annual'],
                p['metrics']['max_drawdown'], p['metrics']['sharpe']))
    L.append('# 参数   : %s' % (p['local'] or '（全默认）'))
    if p.get('export'):
        L.append('#')
        L.append('# 用前先在本地导出信号：')
        L.append('#   %s' % p['export'])
    L.append('#')
    L.append('# [!] 本文件由 qmt/gen.py 从 qmt/_tpl/%s 生成，**不要手工改**。' % p['tpl'])
    L.append('#     要改逻辑改模板，要改参数改 gen.py 的 PROFILES，然后重跑 gen。')
    L.append('LOCAL_PORT = {')
    L.append("    'kind': '%s'," % kind)
    L.append("    'generated_from': '_tpl/%s'," % p['tpl'])
    L.append("    'profiles': {")
    L.append("        '%s': {" % os.path.splitext(p['out'])[0])
    L.append("            'strategy': '%s'," % p['strategy'])
    L.append("            'run_id': '%s'," % p['run_id'])
    L.append("            'local': %s," % (p['local'],))
    L.append("            'metrics': %s," % (p['metrics'],))
    if kind == 'port':
        qmt = {k: v for k, v in p['set'].items() if k != 'SIGNAL_PATH'}
        L.append("            'qmt': %s," % (qmt,))
    L.append('        },')
    L.append('    },')
    if kind == 'executor':
        L.append("    'exporter': 'export_signal.py',")
    L.append('}')
    return '\n'.join(L)


def render(p):
    src = io.open(os.path.join(TPL, p['tpl']), encoding='gbk').read()
    for k, v in p['set'].items():
        src = _rewrite_const(src, k, v)
    src = src.replace('{LOCAL_PORT}', _local_port(p))
    return src.encode('gbk')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--check', action='store_true', help='只校验，不写入')
    a = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    bad = 0
    for p in PROFILES:
        want = render(p)
        dst = os.path.join(OUT, p['out'])
        if a.check:
            got = open(dst, 'rb').read() if os.path.exists(dst) else None
            if got != want:
                print('  x %-26s 与模板不同步（改过手工？重跑 qmt/gen.py）' % p['out'])
                bad += 1
            else:
                print('  v %-26s %6d 字节  <- _tpl/%s' % (p['out'], len(want), p['tpl']))
        else:
            open(dst, 'wb').write(want)
            print('  写出 %-26s %6d 字节  <- _tpl/%s' % (p['out'], len(want), p['tpl']))
    if a.check:
        print('\n%d 个文件，%d 个不同步' % (len(PROFILES), bad))
        sys.exit(1 if bad else 0)


if __name__ == '__main__':
    main()
