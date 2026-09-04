#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""清理回测归档：删重复、把旧回测降级成"只留结论"。

    python3 assay/prune_runs.py                       # 预演，一个文件都不动
    python3 assay/prune_runs.py --apply --backup FILE # 打包备份后执行

## 为什么能清：889 MB 里 83% 是一个文件

    holdings.parquet   749 MB   ← 只喂「收益热力四层下钻」的最后一层
    equity.parquet      63 MB   ← 🔴 等价性回归的数值指纹靠它
    trades.parquet      50 MB   ← 🔴 个股页「哪几次回测买过它」靠它
    strategy.py         10 MB
    meta+stats+其它     16 MB   ← **结论本身只有 6 MB**

## 两类清理，判据都是可证的

**① 完全重复 —— 整个目录删。**
同 `(code_sha256, start, end, cash, cost, params, data_fingerprint)` 的多次
回测，结果**必然逐位一致**（代码、参数、区间、成本、数据全同）。留最新一次。
★ 判据里**必须带 `data_fingerprint`**：不带的话，"数据修正前后各跑一次"
  会被误判成重复 —— 而那两份恰恰是要留的（本机实测差 4 次 / 3 MB）。

**② 旧回测 —— 只删 `holdings.parquet`。**
结论（meta/stats）、权益曲线、成交记录、源码快照全留，所以
等价性回归、个股页联动、指标对比都照旧。只有"看某天持仓"这一层不可用。

## 🔴 完整保留三类，一个都不能少

| 保留 | 为什么 |
|---|---|
| `picks.json` 标记的 | 看板顶部「★ 选中的规则」读它 |
| 账户绑定版本回测过的 | `versions.jsonl` 的 `main_sha256` join `meta.code_sha256` —— 那是"这个版本回测过没有"的唯一依据 |
| 最近 N 天的 | 实盘上线后还在用的那些 |

## 🔴 删了必须留痕，否则"空"和"本来就没有"分不出来

`_read()` 读不到 parquet 时返回**空 DataFrame**（不报错）—— 所以删完不写
标记的话，持仓页显示一片空白，人会以为"这次回测没持仓"。
所以往 `meta.json` 写 `pruned: {'holdings': '日期'}`，接口带出去，
页面显示「明细已清理（重跑该回测可再生成）」。
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
RUNS = os.environ.get('ASSAY_RUNS') or os.path.join(HERE, 'runs')
LIVE = os.path.join(HERE, 'live')
MARKS = os.path.join(HERE, 'picks.json')
# 完整保留的时间分界。★ 不是"最近 N 天"，是**实盘上线日** ——
#   从 live/accounts.json 最早的 created 取（本机 2026-09-01）。
#   理由：那天之后的回测是**在用的**（给实盘出信号、验版本绑定），
#   之前的 615 次是探索期。
#   🔴 用"最近 30 天"的话本机全部 649 次都在窗口内，等于没清 ——
#     判据要对上"为什么保留"，不能拿个看着合理的天数糊过去。
PRUNE = ('holdings',)   # 旧回测里要删的 parquet（不含 equity/trades —— 见文件头）


def _say(*a):
    print(*a, flush=True)


def _h(n):
    for u in ('B', 'K', 'M', 'G'):
        if n < 1024 or u == 'G':
            return '%.0f%s' % (n, u)
        n /= 1024.0


def scan():
    out = []
    for r, _d, fs in os.walk(RUNS):
        if 'meta.json' not in fs:
            continue
        try:
            m = json.load(open(os.path.join(r, 'meta.json'), encoding='utf-8'))
        except Exception:                                       # noqa: BLE001
            continue
        out.append({
            'dir': r, 'rid': os.path.basename(r), 'meta': m,
            'files': {f: os.path.getsize(os.path.join(r, f)) for f in fs},
            'size': sum(os.path.getsize(os.path.join(r, f)) for f in fs),
            'ran': m.get('ran_at') or '',
        })
    return out


def _since():
    """完整保留的起始日（YYYYMMDD）：实盘上线日 = 最早的账户创建日。

    取不到就退回"今天" —— **宁可少删**，不要因为读不到账户就把在用的
    归档也降级了。
    """
    env = os.environ.get('ASSAY_PRUNE_SINCE')
    if env:
        return env.replace('-', '')
    p = os.path.join(LIVE, 'accounts.json')
    try:
        a = json.load(open(p, encoding='utf-8'))
        accts = a if isinstance(a, list) else (a.get('accounts') or [])
        ds = sorted(str(x.get('created'))[:10].replace('-', '')
                    for x in accts if x.get('created'))
        if ds:
            return ds[0]
    except Exception:                                           # noqa: BLE001
        pass
    return time.strftime('%Y%m%d')


def protected(rows):
    """完整保留的集合 + 每一项的理由（理由要能打印出来，方便人核对）。"""
    why = {}
    marks = {}
    if os.path.isfile(MARKS):
        try:
            marks = json.load(open(MARKS, encoding='utf-8'))
        except Exception:                                       # noqa: BLE001
            pass
    for rid in marks:
        why.setdefault(rid, []).append('picks.json 标记')
    binds = set()
    if os.path.isdir(LIVE):
        for aid in os.listdir(LIVE):
            p = os.path.join(LIVE, aid, 'versions.jsonl')
            if not os.path.isfile(p):
                continue
            for ln in open(p, encoding='utf-8'):
                try:
                    v = json.loads(ln)
                except Exception:                               # noqa: BLE001
                    continue
                for k in ('main_sha256', 'code_sha256', 'sem_sha256'):
                    if v.get(k):
                        binds.add(v[k])
    cut = _since()
    for x in rows:
        sha = x['meta'].get('code_sha256') or ''
        if sha and (sha in binds or
                    any(sha[:12] == b[:12] for b in binds)):
            why.setdefault(x['rid'], []).append('账户绑定过这个版本')
        if x['rid'][:8] >= cut:
            why.setdefault(x['rid'], []).append('实盘上线（%s）之后' % cut)
    return why


def plan():
    rows = scan()
    keep = protected(rows)
    by = {}
    for x in rows:
        m = x['meta']
        fp = (m.get('data_fingerprint') or {}).get('overall')
        # 🔴 键里必须带 data_fingerprint —— 见文件头
        k = (m.get('code_sha256'), m.get('start'), m.get('end'), m.get('cash'),
             json.dumps(m.get('cost'), sort_keys=True),
             json.dumps(m.get('params'), sort_keys=True), fp)
        by.setdefault(k, []).append(x)
    drop_dirs, prune_files = [], []
    for k, v in by.items():
        if len(v) > 1:
            v = sorted(v, key=lambda z: z['ran'])
            # 组里被保护的一律留；其余只留最新那次
            surv = [x for x in v if x['rid'] in keep] or [v[-1]]
            for x in v:
                if x not in surv:
                    drop_dirs.append(x)
    dropped = {id(x) for x in drop_dirs}
    for x in rows:
        if id(x) in dropped or x['rid'] in keep:
            continue
        for what in PRUNE:
            f = what + '.parquet'
            if f in x['files']:
                prune_files.append((x, f, x['files'][f]))
    return rows, keep, drop_dirs, prune_files


def show(rows, keep, drop_dirs, prune_files):
    tot = sum(x['size'] for x in rows)
    _say('归档 %d 次，共 %s（%s）' % (len(rows), _h(tot),
                                     os.path.relpath(RUNS, os.path.dirname(HERE))))
    _say('\n① 完整保留 %d 次' % len(keep))
    cnt = {}
    for rid, ws in keep.items():
        for w in ws:
            cnt[w] = cnt.get(w, 0) + 1
    for w, n in sorted(cnt.items(), key=lambda x: -x[1]):
        _say('     %-22s %d 次' % (w, n))
    _say('\n② 整目录删除（完全重复）%d 次，%s'
         % (len(drop_dirs), _h(sum(x['size'] for x in drop_dirs))))
    for x in sorted(drop_dirs, key=lambda z: -z['size'])[:6]:
        m = x['meta']
        _say('     %-24s %-8s %-16s %s~%s  %s'
             % (x['rid'], m.get('group'), m.get('strategy'),
                m.get('start'), m.get('end'), _h(x['size'])))
    if len(drop_dirs) > 6:
        _say('     …… 另 %d 次' % (len(drop_dirs) - 6))
    _say('\n③ 只删明细（保留结论）%d 个文件，%s'
         % (len(prune_files), _h(sum(s for _x, _f, s in prune_files))))
    per = {}
    for _x, f, s in prune_files:
        per[f] = per.get(f, [0, 0])
        per[f][0] += 1
        per[f][1] += s
    for f, (n, s) in per.items():
        _say('     %-20s %4d 个  %s' % (f, n, _h(s)))
    after = tot - sum(x['size'] for x in drop_dirs) \
        - sum(s for _x, _f, s in prune_files)
    _say('\n结果：%s → %s（省 %.0f%%），归档数 %d → %d'
         % (_h(tot), _h(after), 100.0 * (tot - after) / tot,
            len(rows), len(rows) - len(drop_dirs)))


def apply(rows, keep, drop_dirs, prune_files, backup):
    if not backup:
        raise SystemExit('🔴 --apply 必须带 --backup FILE ——'
                         '删除不可逆，而 holdings 重跑一次回测就能再生成，'
                         '但那是几十秒 × N 次。')
    backup = os.path.abspath(os.path.expanduser(backup))
    if os.path.exists(backup):
        raise SystemExit('🔴 %s 已存在 —— 不覆盖（备份是唯一的回滚凭据）'
                         % backup)
    if backup.startswith(os.path.dirname(HERE) + os.sep):
        _say('⚠️ 备份放在仓库里了（%s）—— 会被算进迁移量，建议放外面' % backup)
    # 1) 打包：整目录删的那些 + 要删的明细文件
    root = os.path.dirname(os.path.dirname(RUNS))
    items = [os.path.relpath(x['dir'], root) for x in drop_dirs]
    items += [os.path.relpath(os.path.join(x['dir'], f), root)
              for x, f, _s in prune_files]
    _say('打包 %d 项 -> %s' % (len(items), backup))
    lst = backup + '.filelist'
    with open(lst, 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(items))
    r = subprocess.run(['tar', '-cf', backup, '-C', root, '-T', lst],
                       capture_output=True, text=True)
    if r.returncode != 0:
        _say(r.stderr[-800:])
        raise SystemExit('🔴 打包失败 —— 什么都没删')
    os.remove(lst)
    n = int(subprocess.run(['tar', '-tf', backup], capture_output=True,
                           text=True).stdout.count('\n'))
    _say('  备份 %s，%d 个条目' % (_h(os.path.getsize(backup)), n))
    if n < len(items):
        raise SystemExit('🔴 备份里只有 %d 项，少于要删的 %d 项 —— 中止'
                         % (n, len(items)))
    # 2) 删
    for x in drop_dirs:
        shutil.rmtree(x['dir'])
    today = time.strftime('%Y-%m-%d')
    touched = {}
    for x, f, _s in prune_files:
        os.remove(os.path.join(x['dir'], f))
        touched.setdefault(x['dir'], []).append(f[:-len('.parquet')])
    # 3) 🔴 留痕：不写标记的话，页面上"空"与"本来就没有"分不出来
    for d, whats in touched.items():
        p = os.path.join(d, 'meta.json')
        m = json.load(open(p, encoding='utf-8'))
        pr = m.get('pruned') or {}
        for w in whats:
            pr[w] = today
        m['pruned'] = pr
        tmp = p + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as fh:
            json.dump(m, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, p)
    _say('\n✅ 删了 %d 个目录 + %d 个明细文件；%d 份 meta.json 写了 pruned 标记'
         % (len(drop_dirs), len(prune_files), len(touched)))
    _say('   回滚：tar -xf %s -C %s' % (backup, root))
    return 0


def main():
    ap = argparse.ArgumentParser(description='清理回测归档')
    ap.add_argument('--apply', action='store_true', help='真的删（要 --backup）')
    ap.add_argument('--backup', metavar='FILE', help='先打包到这个 tar')
    a = ap.parse_args()
    rows, keep, dd, pf = plan()
    show(rows, keep, dd, pf)
    if not a.apply:
        _say('\n（预演，一个文件都没动。加 --apply --backup FILE 执行）')
        return 0
    return apply(rows, keep, dd, pf, a.backup)


if __name__ == '__main__':
    sys.exit(main() or 0)
