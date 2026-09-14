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
      trades.parquet    每笔平仓（往返视角）：进出价、持有天数、盈亏、红利税、卖出原因
                        —— 股数/价格是**后复权记账单位**
      fills.parquet     逐笔成交（时间视角）：**真实股数 + 不复权价**，
                        撮合当场记的，不用换算；含未平仓持仓的买入
      rejects.parquet   拒单：日期/代码/方向/原因 —— 失败必须留痕，不静默
      run.log           完整日志

run_id = 时间戳 + 代码哈希前 6 位。**带代码哈希是为了区分「同一份代码重跑」
和「代码改了再跑」** —— 只看时间戳分不出来，而这正是归因时最需要知道的事。
"""
import ast
import hashlib
import json
import os
import time
from datetime import datetime

import duckdb
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STRAT_DIR = os.path.join(ROOT, 'strategies')


def _default_runs():
    """归档目录：ASSAY_RUNS 环境变量 > 代码目录下的 runs/。

    归档是【产出数据】，生命周期和代码完全不同（886M / 615 次且持续增长）。
    路径可配置之后，它就能放到别的盘、被多个 checkout 共用、独立备份与清理。
    """
    return os.path.abspath(os.path.expanduser(
        os.environ.get('ASSAY_RUNS') or os.path.join(ROOT, 'runs')))


RUNS = _default_runs()


def set_runs(path=None):
    """CLI 覆盖归档目录，返回最终生效的路径。

    ★ 必须重绑【模块级】RUNS —— server.py 等按 `registry.RUNS` 取值，
      只改局部变量不会传播出去。这类错误是静默的：归档写到 A、面板读 B，
      两边都不报错，只是看不到新记录。
    """
    global RUNS
    RUNS = os.path.abspath(os.path.expanduser(path)) if path else _default_runs()
    return RUNS


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



def semantic_sha256(code):
    """**行为哈希** —— 只看 AST 结构与字面量，忽略注释/排版/简介。

    为什么需要它：字节哈希（code_sha256）把"改一个注释"也算成新版本。
    实测本次会话里给四个策略加 NOTE、修被拼接的注释、改排版，
    每次都精确复现原数字（行为完全没变），却各新增一个版本节点。

    实现要点：
      · **注释根本不在 AST 里**，所以改注释/加空行天然同哈希 —— 不需要特殊处理。
      · docstring 在 AST 里（是字符串字面量），显式剥掉。
      · `NOTE` 赋值也剥掉：它的语义就是「描述这个版本」，若参与哈希就成了
        循环依赖 —— 改简介产生新版本，新版本又要写新简介。

    刻意**不做**的事：不对 SQL 字符串做归一化。那需要 SQL 解析器，
    一旦弄错会把真实逻辑改动误判成同一版本 —— 那比多几个版本节点危险得多。
    所以 SQL 里改注释/加空格【算】新版本。

    ★ 这个哈希用于**分组展示**，不替代 code_sha256。
      后者是审计追溯：归档里躺的到底是哪份字节，一字不差。
      丢了它就丢了「当时执行的确切代码」这个保证。
    """
    tree = ast.parse(code)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            body = getattr(node, 'body', None)
            if body and isinstance(body[0], ast.Expr) \
                    and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                body.pop(0)
    tree.body = [n for n in tree.body if not (
        isinstance(n, ast.Assign) and len(n.targets) == 1
        and isinstance(n.targets[0], ast.Name) and n.targets[0].id == 'NOTE')]
    return hashlib.sha256(ast.dump(tree).encode('utf-8')).hexdigest()


def safe_semantic_sha256(code):
    """语法错误时回退到字节哈希 —— 宁可多一个版本节点，不要抛错。"""
    try:
        return semantic_sha256(code)
    except SyntaxError:
        return hashlib.sha256(code.encode('utf-8')).hexdigest()


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
        # 行为哈希：只看 AST，改注释/排版/简介不算新版本（看板按它分组）
        'semantic_sha256': safe_semantic_sha256(code),
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
    # 🔴 **逐笔成交（真实股数 + 不复权价）**（2026-09-14 加）。
    #   `trades.parquet` 是**往返**视角（FIFO 批次，卖出时才写），而且里面的
    #   股数/价格是**后复权记账单位** —— 摆到页面上是 `774.835189 股 @ 25.40`，
    #   而当时真实是 `5700 股 @ 3.4526`。
    #   换算要靠 `hfq_factor`，可它与 `dividend` 表**并不总是对齐**（实测两个
    #   方向都有：002293 引擎缩了股数而因子没跳、601318 因子跳了而分红表没有
    #   那一条）—— 于是约 4% 的行换出来不是整手。
    #   ★ 所以把 broker 记的**真实成交**直接存下来：它是撮合当场的数，
    #     不需要任何换算，也顺带把「未平仓持仓的买入不在表里」那个缺口补上。
    #   ★ 旧归档没有这张表，展示层仍退回「按因子换算」——**能精确就精确，
    #     退化也要退化得说得清**。
    _w([{'date': f['date'], 'code': f['code'], 'side': f['side'],
         'shares': float(f['shares']), 'price': float(f['price']),
         'amount': float(f['amount']), 'fee': float(f['fee']),
         'reason': f.get('reason') or ''} for f in getattr(b, 'fills', [])],
       os.path.join(d, 'fills.parquet'))
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
