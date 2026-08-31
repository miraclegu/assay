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

# 聚宽注入的全局名（策略里不 import 也能用）。未定义名检查要放过它们。
JQ_GLOBALS = {
    'g', 'log', 'context', 'attribute_history', 'history',
    'get_price', 'get_current_data', 'get_fundamentals', 'get_fundamentals_continuously',
    'get_all_securities', 'get_security_info', 'get_index_stocks', 'get_industry_stocks',
    'get_trade_days', 'get_all_trade_days', 'get_factor_values', 'get_extras',
    'get_billboard_list', 'get_locked_shares', 'get_money_flow', 'get_mtss',
    'finance', 'query', 'valuation', 'indicator', 'income', 'balance', 'cash_flow',
    'bank_indicator', 'macro', 'opt', 'jy', 'normalize_code',
    'order', 'order_value', 'order_target', 'order_target_value', 'order_percent',
    'order_target_percent', 'cancel_order', 'get_open_orders', 'get_orders', 'get_trades',
    'set_benchmark', 'set_option', 'set_order_cost', 'set_slippage', 'set_universe',
    'set_commission', 'OrderCost', 'FixedSlippage', 'PriceRelatedSlippage',
    'LimitOrderStyle', 'MarketOrderStyle',
    'run_daily', 'run_weekly', 'run_monthly', 'scheduler',
    'inout_cash', 'record', 'read_file', 'write_file',
    'initialize', 'handle_data', 'before_trading_start', 'after_trading_end',
    'process_initialize', 'after_code_changed',
    # `from jqdata import *` 带进来的（星号导入本检查解析不了，只能列白名单）
    'datetime', 'timedelta', 'date', 'time', 'np', 'numpy', 'math', 'dt',
    # 补：froec 线用到的
    'get_history_fundamentals', 'get_industry', 'get_industries',
    'get_industry_stocks', 'OrderStatus', 'get_bars', 'get_ticks',
    'get_current_tick', 'get_dominant_future', 'get_future_contracts',
    'get_margincash_stocks', 'get_marginsec_stocks', 'get_concept',
    'get_concept_stocks', 'get_security_info', 'sm', 'statsmodels',
    'SMA', 'MA', 'EMA', 'MACD', 'KDJ', 'RSI', 'BOLL', 'ATR',
}

# 星号导入无法静态解析。遇到未列在白名单里的 `from X import *`，
# 把 X 打出来提醒 —— 否则未定义名检查会变成一堆假阳性，然后被人无视。
STAR_OK = {'jqdata', 'jqfactor', 'jqlib.technical_analysis',
           'kuanke.user_space_api', 'jqlib.optimizer'}


def undefined_names(src):
    """静态查未定义的全局名 —— ast.parse 过了不代表能跑。

    实测代价：把 VERIFY_MODE 换成 COST_MODE 后，[CONFIG] 日志行里还引用着
    VERIFY_MODE。check.py 只做 ast.parse，语法没问题，一到聚宽就
    NameError: name 'VERIFY_MODE' is not defined —— 而且是在 initialize 里炸，
    整个回测起不来。这个检查就是为了拦住它。

    做法：收集所有【被赋值/定义/import/形参/推导式变量】的名字，
    再扫所有 Name(Load) 与装饰器，凡不在集合、不是 builtins、不在
    JQ_GLOBALS 里的就报出来。有作用域上的粗糙（不区分局部/全局），
    所以只报「全文都没定义过」的名字 —— 这类必错，不会有假阳性。
    """
    import builtins
    tree = ast.parse(src)
    bound = set(dir(builtins)) | set(JQ_GLOBALS)
    used = []

    for node in ast.walk(tree):
        # 绑定：赋值 / 函数定义 / 类 / import / 形参 / for / with / except / 推导式 / global
        # [!] Lambda 也要收形参 —— 漏了它会把 lambda x: x.foo 里的 x 报成未定义。
        #     实测在 froec v3 的 df.groupby('code').apply(lambda x: x.reset_index())
        #     上误报了一次。
        if isinstance(node, ast.Lambda):
            a = node.args
            for grp in (a.args, getattr(a, 'posonlyargs', []), a.kwonlyargs):
                for x in grp:
                    bound.add(x.arg)
            for x in (a.vararg, a.kwarg):
                if x is not None:
                    bound.add(x.arg)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
            a = node.args if hasattr(node, 'args') else None
            if a is not None:
                for grp in (a.args, getattr(a, 'posonlyargs', []), a.kwonlyargs):
                    for x in grp:
                        bound.add(x.arg)
                for x in (a.vararg, a.kwarg):
                    if x is not None:
                        bound.add(x.arg)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for al in node.names:
                bound.add((al.asname or al.name).split('.')[0])
        elif isinstance(node, ast.Global):
            bound.update(node.names)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            bound.add(node.id)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bound.add(node.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            used.append((node.id, node.lineno))

    seen, out = set(), []
    for name, ln in used:
        if name in bound or name in seen:
            continue
        seen.add(name)
        out.append((name, ln))
    return out


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

    stars = [n.module for n in ast.walk(ast.parse(src))
             if isinstance(n, ast.ImportFrom)
             and any(a.name == '*' for a in n.names)]
    unknown_star = [m for m in stars if m not in STAR_OK]
    if unknown_star:
        errs.append('%s 有未登记的星号导入 %s —— 未定义名检查会失准，'
                    '把它加进 STAR_OK 并把它提供的名字加进 JQ_GLOBALS'
                    % (rel, unknown_star))

    und = undefined_names(src)
    if und:
        errs.append('%s 引用了未定义的名字（聚宽里会 NameError）: %s'
                    % (rel, ', '.join('%s(第%d行)' % (n, l) for n, l in und[:6])))

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
