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
import datetime
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
        if x['rid'][:8] >= cut and not _is_exp(x['meta']):
            why.setdefault(x['rid'], []).append('实盘上线（%s）之后' % cut)
    # 🔴 **「账户绑定过这个版本」只需要每个版本留【一次】。**（2026-09-15）
    #   它回答的是"这个版本回测过没有"（实盘页查证的唯一依据）——
    #   一次就够。而实测 `ca52ae82` 这一个版本占了 **304 次**回测
    #   （其中 193 次是实验组），全保下来等于这条保护把仓库钉死了。
    #   ★ 留哪一次：优先**正式分组**（不是 `_` 开头）、区间最长的那次 ——
    #     那是最有代表性的，点进去能看到完整结论。
    by_sha = {}
    for x in rows:
        if '账户绑定过这个版本' not in why.get(x['rid'], []):
            continue
        sha = (x['meta'].get('code_sha256') or '')[:12]
        by_sha.setdefault(sha, []).append(x)
    for sha, lst in by_sha.items():
        best = sorted(lst, key=lambda z: (
            _is_exp(z['meta']),                       # 正式组优先
            -(_span(z['meta'])),                      # 区间长的优先
            z['ran']))[0]
        for x in lst:
            if x is best:
                continue
            w = why.get(x['rid'], [])
            w = [r for r in w if r != '账户绑定过这个版本']
            if w:
                why[x['rid']] = w
            else:
                why.pop(x['rid'], None)
    return why


def _is_exp(meta):
    """实验性分组：`_` 开头。

    ★ 项目里跑对照实验一律用 `--group _xxx`（见 CLAUDE.md 那几轮因子归因），
      正式结论进 `小市值/` `红利/` `ETF/` 这些业务域分组。
      所以"下划线开头"就是**作者自己标出来的"这是试验"**，
      不是我猜的规律 —— 判据取的是已有约定，不是新发明一个。
    """
    return str(meta.get('group') or '').startswith('_')


def _span(meta):
    """回测区间有多少天（越长越有代表性）。取不到就算 0。"""
    try:
        a = str(meta.get('start'))[:10]
        b = str(meta.get('end'))[:10]
        return (datetime.date.fromisoformat(b)
                - datetime.date.fromisoformat(a)).days
    except Exception:                                           # noqa: BLE001
        return 0


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
            # 🔴 **同参数重复跑，只留一次就够** —— 结果必然逐位一致
            #   （键里含 code_sha256 + 区间 + cash + cost + params +
            #   data_fingerprint，见文件头）。
            #   ★ 原来"组里被保护的一律留"，而「实盘上线之后」几乎保护了
            #     所有近期归档 —— 于是重复跑一次也删不掉。实测 selftest 的
            #     「版本页触发回测」用例每跑一次就归档一次，积了 **105 次**
            #     同参数的 `2026-06-01~06-30`（2026-09-15 用户问"这是什么"
            #     才发现），而 prune 报的是"0 次可删"。
            #   ★ 现在：**picks 标记与账户绑定仍然一律留**（那是决策证据与
            #     查证依据），而"只是跑在实盘上线之后"不再成为留下重复的理由
            #     —— 留最新那一次，结论一模一样。
            hard = [x for x in v
                    if any(w in ('picks.json 标记', '账户绑定过这个版本')
                           for w in keep.get(x['rid'], []))]
            surv = hard or [v[-1]]
            for x in v:
                if x not in surv:
                    drop_dirs.append(x)
    dropped = {id(x) for x in drop_dirs}
    # ---- ②b 实验分组：**整目录删，但结论先抽出来** ----
    # 🔴 用户 2026-09-15："现在又有大量试验性质的回测记录，清理掉一批，
    #   保留结论即可。" —— 实验组（`_` 开头）实测 298 次 / 82.9 MB，
    #   其中 `holdings` 占 74%，而**结论只有 meta+stats 的 1 MB**。
    # ★ 所以整目录删，删之前把 meta+stats 抽成一个 JSONL
    #   （`runs/_pruned_conclusions.jsonl`，append-only）——
    #   那是"这次实验跑出了什么"的唯一去处，删完还查得到。
    #   🔴 只删 tar 备份是不够的：备份是**回滚凭据**（要解包才能看），
    #     而结论是要**随时查**的（比如"那轮 dev_pct=0.13 到底多少"）。
    #     两者用途不同，不能互相替代。
    if not _KEEP_EXP:
        for x in rows:
            if id(x) in dropped or x['rid'] in keep:
                continue
            if _is_exp(x['meta']):
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


#: `--keep-exp` 时不动实验组（默认清）。模块级 —— `plan()` 与 `main()` 都要读。
_KEEP_EXP = False

CONCLUSIONS = 'runs/_pruned_conclusions.jsonl'


def save_conclusions(drop_dirs):
    """把要整目录删的那些**结论**（meta + stats）追加进一个 JSONL。

    ★ **append-only**：同一个 run_id 只会出现一次（按 run_id 去重），
      重复跑清理不会把文件撑大。
    ★ 放在 `runs/` 下而不是仓库根：它是归档的一部分，跟着 `ASSAY_RUNS` 走。
    """
    path = os.path.join(os.path.dirname(RUNS), CONCLUSIONS) \
        if not os.path.isabs(CONCLUSIONS) else CONCLUSIONS
    path = os.path.join(RUNS, os.path.basename(CONCLUSIONS))
    have = set()
    if os.path.isfile(path):
        for ln in open(path, encoding='utf-8'):
            try:
                have.add(json.loads(ln).get('run_id'))
            except Exception:                                   # noqa: BLE001
                pass
    n = 0
    with open(path, 'a', encoding='utf-8') as fh:
        for x in drop_dirs:
            rid = x['rid']
            if rid in have:
                continue
            st = {}
            sp = os.path.join(x['dir'], 'stats.json')
            if os.path.isfile(sp):
                try:
                    st = json.load(open(sp, encoding='utf-8'))
                except Exception:                               # noqa: BLE001
                    pass
            fh.write(json.dumps({'run_id': rid, 'meta': x['meta'], 'stats': st,
                                 'pruned_at': time.strftime('%Y-%m-%d %H:%M')},
                                ensure_ascii=False, default=str) + '\n')
            have.add(rid)
            n += 1
    return path, n


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
    # 🔴 **两类原因要分开说**：一类是"同参数重复跑"（结果必然逐位一致，
    #   删了不丢任何信息），另一类是"实验分组"（结论会被抽进 JSONL）。
    #   混成一句"完全重复"就是**报告在说谎** —— 实验组并不重复。
    _dup = [x for x in drop_dirs if not _is_exp(x['meta'])]
    _exp = [x for x in drop_dirs if _is_exp(x['meta'])]
    _say('\n② 整目录删除 %d 次，%s'
         % (len(drop_dirs), _h(sum(x['size'] for x in drop_dirs))))
    if _dup:
        _say('     同参数重复跑（结果必然逐位一致）  %d 次  %s'
             % (len(_dup), _h(sum(x['size'] for x in _dup))))
    if _exp:
        _say('     实验分组（`_` 开头，结论抽进 %s）  %d 次  %s'
             % (os.path.basename(CONCLUSIONS), len(_exp),
                _h(sum(x['size'] for x in _exp))))
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
    # 2) 🔴 **先把结论抽出来再删** —— 顺序不能反：抽失败了就什么都别删。
    #   ★ 这一步对"同参数重复跑"的那些是冗余的（留下来的那次结论一样），
    #     但对实验组是唯一的去处。宁可多存几行 JSON。
    cpath, cn = save_conclusions(drop_dirs)
    _say('  结论已抽出 %d 条 -> %s' % (cn, os.path.relpath(cpath, HERE)))

    # 3) 删
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
    ap.add_argument('--keep-exp', action='store_true',
                    help='不动实验分组（`_` 开头）—— 默认会整目录删它们，'
                         '删前把 meta+stats 抽进 runs/_pruned_conclusions.jsonl')
    ap.add_argument('--backup', metavar='FILE', help='先打包到这个 tar')
    a = ap.parse_args()
    global _KEEP_EXP
    _KEEP_EXP = bool(a.keep_exp)
    rows, keep, dd, pf = plan()
    show(rows, keep, dd, pf)
    if not a.apply:
        _say('\n（预演，一个文件都没动。加 --apply --backup FILE 执行）')
        return 0
    return apply(rows, keep, dd, pf, a.backup)


if __name__ == '__main__':
    sys.exit(main() or 0)
