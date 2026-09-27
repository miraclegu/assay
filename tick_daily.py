#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""交易日早上重算调仓信号（launchd 在 07:00 / 08:00 / 09:00 各跑一次）。

    python3 assay/tick_daily.py            # 按判据决定要不要重算
    python3 assay/tick_daily.py --force    # 不管指纹变没变，都重算一次
    python3 assay/tick_daily.py --dry      # 只报判据，不算不写

## 为什么要早上再算（2026-09-07 加）

18:10 数据同步跑完仍然算一次（那份是"初步"的）。但 **A 股的公告集中在
16:00~22:00**，而其中「实施风险警示(ST)」「停牌」这类是**次日就生效**的：
T-1 晚 20:00 公告，T 日开盘简称就变 ST、涨跌幅限制变 5%。
按 18:10 那份数据算出来的信号里那只票 `is_risk_warned=false`，
froec 的「排除 ST」过滤**不会排除它**。

所以早上再算几次，赶在 09:30 下单之前。

🔴 **但这本身不自动解决 ST 问题** —— 07:00 时聚宽的 `status_change` 仍要
  手动导、tdx 的名称快照也还是 18:10 抓的。这个脚本的价值是：
    ① 离下单更近；② 给「手动导入数据后重算」留出时点
       （人早上导完聚宽增量，下一个点位就会自动检测到并重算）；
    ③ 结果与昨晚不一致时**留痕并显红**。
  下单前真正要核对的是**实时状态**（页面上那条）。

## 三道判据，每道都宁可少算不乱算

1. **今天是交易日吗** —— 判据是 `next_trading_day(昨天) == 今天`。
   不是就什么都不做（非交易日算出来的信号是给下一个交易日的，
   而那份 18:10 已经算过了）。
2. 🔴 **A 腿数据到最新交易日了吗** —— 原设计特意"同步跑完直接出信号"，
   理由是"靠两个时间常量隔开，同步一慢就错位，而错位的表现是
   **信号静默用了昨天的数据**"。现在确实拆成了两个时间点，
   所以这里必须自己查：面板不是最新交易日的就**拒绝重算**，
   而不是拿旧数据算一份看着正常的
   （同 sync_daily.sh 那条：宁可没有信号，也不要用半截数据算出来的信号）。
3. **数据指纹变了吗** —— 没变就跳过。
   `PanelFeed.fingerprint()` 本身就是文件戳（`rel|size|mtime_ns` 的哈希，
   不读内容），所以这一步很便宜。
   ★ 复用它而不是另写一份文件戳：两处实现迟早分叉，
     而分叉的表现是"该重算的没重算"。
   ★ 误判方向是安全的：文件动过但内容没变 -> 多算一次（结果相同，
     make_signal 只记一次 recomputed_at，不产生 revision 噪声）；
     文件没动 -> 内容必然没变 -> 跳过是对的。
"""

# 🔴🔴 **第一件事：固定 hash 种子，否则回测跨进程不可复现。**
#   实测 `etf_p1_rotation` 同数据同代码 10 个进程跑出两种结果各 5 次 ——
#   策略里对一个 `set` 排序、并列时没有 tie-break，而 str 的 hash 每进程随机。
#   只能在解释器启动前设，所以这里带着环境变量把自己重启一次（幂等）。
#   详见 `assay/hashseed.py`。
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from assay.hashseed import ensure_fixed_hash_seed as _ehs   # noqa: E402
from assay.hashseed import ensure_utf8_console as _euc      # noqa: E402
# 🔴 UTF-8 要在【任何打印与任何子进程】之前 —— 中文 Windows 默认 cp936，
#   编不出满屏的 ✓✗⚠，输出一被重定向（schtasks 写日志 / subprocess 抓
#   输出）就 UnicodeEncodeError、退出码 1。见 hashseed.ensure_utf8_console
# 🔴 **顺序不能反**：`_ehs()` 要固定 hash 种子，没固定就 `os.execve` 把自己
#   重启一次 —— 在它之前做的事白做、有副作用的会重来一遍，所以它必须是
#   第一条可执行语句（守卫用 ast 钉着）。而 `_euc()` 放在后面不影响 UTF-8：
#   `_ehs` 在 exec 之前一个字都不打印，当前进程靠 `reconfigure()`、
#   子进程靠 `PYTHONUTF8=1`，两条路都不要求"在 exec 之前设"。
_ehs()
_euc()
import argparse
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from assay import live as lv                                  # noqa: E402
from assay.lv import base as _lvbase                          # noqa: E402


def _say(*a):
    print(*a, flush=True)


def _is_trading_day(d):
    """今天是不是交易日。判据与 tick() 一致：next_trading_day(昨天) == 今天。"""
    try:
        return lv.next_trading_day(d - datetime.timedelta(days=1)) == d
    except Exception as e:                                     # noqa: BLE001
        _say('🔴 交易日历读不出来：%s' % e)
        return None


def _leg_a_ok():
    """A 腿（行情）到最新交易日了吗 → (ok, 说明)。

    🔴 判据取 `datalake/build/sync_status.py` 那**一处** —— 看板显示的、
      同步脚本用的、这里判的必须是同一套。分两处写就会出现
      "页面说数据是新的、而信号用的是旧的"，而那不报错。
    """
    dl = lv._lake()
    sys.path.insert(0, dl)
    try:
        from build import sync_status as ss
    except Exception as e:                                     # noqa: BLE001
        return None, 'sync_status 导不进来：%s' % e, None
    st = ss.collect()
    lag = st.get('leg_a_lag')
    exp = st.get('expect_trade_day')
    if lag is None:
        return None, 'sync_status 没给 leg_a_lag', None
    # ★ 顺带把"数据该到哪天"带出来 —— 模拟盘用它判"推进到最新没有"。
    #   自己再算一遍就是第二处判据（同上一段那条纪律）。
    return (lag == 0), 'A 腿落后 %s 个交易日（应到 %s）' % (lag, exp), exp


def _now_fp():
    """现在的数据指纹。复用 PanelFeed.fingerprint()，见文件头第 3 条。"""
    from assay.feed import PanelFeed
    f = PanelFeed(_lvbase.DEFAULT_WARMUP_START,
                  datetime.date.today().isoformat(), root=lv._lake())
    return (f.fingerprint() or {}).get('overall')


def main():
    ap = argparse.ArgumentParser(description='交易日早上重算调仓信号')
    ap.add_argument('--force', action='store_true',
                    help='不管指纹变没变都重算')
    ap.add_argument('--dry', action='store_true', help='只报判据，不算不写')
    a = ap.parse_args()

    # 🔴 **按天汇总的日志**：tick 这条链此前【只有 launchd 的 .out】——
    #   而那是 macOS 独有的，Windows 上信号重算的输出一个字都没地方留。
    #   接在最前面：后面那三道判据每一条都会 print 一句拒绝的理由，
    #   而"为什么今天没重算"正是事后要查的东西。
    try:
        _dl = lv._lake()
        if _dl not in sys.path:
            sys.path.insert(0, _dl)
        import logs as _logs                                  # noqa: E402
        _logs.tee_stdio(_logs.KIND_DAILY, _dl, 'tick')
    except Exception as _e:                                   # noqa: BLE001
        print('⚠ 按天日志没接上：%s: %s' % (type(_e).__name__, _e), flush=True)

    # 日志按天保留 —— launchd 的 .out/.err **永不轮转**（见 datalake/logs.py）。
    # ★ 包在 try 里：日志是给人看的，清理坏了不许把信号重算搞挂
    #   （同 progress 那条「进度坏了不影响主链」）。
    try:
        _dl = lv._lake()
        if _dl not in sys.path:
            sys.path.insert(0, _dl)
        import logs as _logs                                  # noqa: E402
        import paths as _dlpaths                              # noqa: E402
        # ★ 连**搬家之前**那一对一起裁：plist 里写的还是旧路径，要等人
        #   重装定时器才会变 —— 在那之前只裁新的等于没裁，旧的又会无限涨。
        for _p in (_dlpaths.launchd_logs('tick')
                   + _dlpaths.launchd_logs_legacy('tick')):
            _logs.trim_by_days(_p, days=30)
        _logs.prune_day_logs(_dl, days=30)
    except Exception as _e:                                   # noqa: BLE001
        # 🔴 **不许静默跳过。** 第一版写的是 `pass` —— 于是"日志没被裁"
        #   与"根本没跑到这里"在屏幕上一模一样，我为此查了一轮才发现
        #   是别的原因（同「保护分支不该静默跳过，该 assert」那条）。
        #   这里不 raise（清理坏了不该把信号重算搞挂），但必须说一句。
        print('⚠ 日志清理跳过：%s: %s' % (type(_e).__name__, _e), flush=True)

    today = datetime.date.today()
    _say('=' * 66)
    _say('信号重算  %s' % datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    _say('=' * 66)

    # ---- 判据 1：交易日 ----
    td = _is_trading_day(today)
    if td is None:
        return 2
    if not td:
        _say('今天 %s 不是交易日 —— 什么都不做。' % today)
        _say('（非交易日算出来的信号是给下一个交易日的，18:10 那次已经算过）')
        return 0
    _say('① 今天 %s 是交易日 ✓' % today)

    # ---- 判据 2：A 腿数据新鲜 ----
    ok, why, last_day = _leg_a_ok()
    _say('② %s' % why)
    if ok is None:
        _say('   ⚠️ 判断不了新鲜度 —— 【不重算】（宁可用昨晚那份，'
             '也不要拿不确定的数据覆盖它）')
        return 2
    if not ok:
        _say('   🔴 行情数据没到最新交易日 —— 【拒绝重算】。')
        _say('      昨晚 18:10 的同步可能失败了。先看：')
        _say('        bash datalake/sync_daily.sh --no-live')
        _say('      （宁可没有新信号，也不要用半截数据算出来的覆盖旧的）')
        return 3

    # ---- 判据 3：指纹变了吗 ----
    fp = _now_fp()
    _say('③ 当前数据指纹 %s' % fp)
    todo, skip = [], []
    for acct in lv.load_accounts():
        if acct.get('archived') or not acct.get('code_sha256'):
            continue
        aid = acct['id']
        sig = lv.load_signal(aid, today.isoformat())
        if not sig:
            todo.append((aid, '今天还没有信号'))
            continue
        old = (sig.get('data_fingerprint') or {})
        old = old.get('overall') if isinstance(old, dict) else old
        if a.force:
            todo.append((aid, '--force'))
        elif old != fp:
            todo.append((aid, '指纹变了（%s -> %s）' % (old, fp)))
        else:
            skip.append((aid, '指纹没变'))
    for aid, w in skip:
        _say('   %-8s 跳过 —— %s' % (aid, w))
    for aid, w in todo:
        _say('   %-8s 要算 —— %s' % (aid, w))
    # ---- 模拟盘：数据更新了就把它推进到最新数据日 ----
    # 🔴 **挂在这里而不是另起一个定时器。**"数据更新了没有"的判据
    #   （A 腿新鲜度 + 指纹）已经在这个脚本的三道判据里了 —— 另写一份必然
    #   分叉，而分叉的表现是"该推的没推"或"拿半截数据推了"
    #   （同「依赖写进调用顺序比写进常量可靠」那条）。
    # 🔴 **放在「没有要重算的」那个提前返回【之前】** —— 信号没变不代表
    #   模拟盘不用推：新建的模拟盘账户一笔成交都还没有，而它的指纹当然
    #   "没变过"。踩过一次：块写在末尾，结果永远跑不到。
    # ★ 判据是**推进到哪天了 vs 数据到哪天**，不是"指纹变没变" ——
    #   推进一次要重跑一遍回测（1~3 秒），而 tick 每小时一个点位，
    #   无脑重跑是白烧。`advance` 本身幂等，这道判据只是省那几秒。
    papers = [x for x in lv.load_accounts()
              if lv.is_paper(x) and not x.get('archived')]
    due = [x for x in papers
           if (lv.state(x['id']) or {}).get('advanced_to') != last_day]
    if papers:
        _say('')
        _say('④ 模拟盘 %d 个，要推进 %d 个' % (len(papers), len(due)))
        for x in papers:
            if x not in due:
                _say('   %-10s 已经是最新（%s）'
                     % (x['id'], (lv.state(x['id']) or {}).get('advanced_to')))
        if due and not a.dry:
            for x in due:
                r = lv.advance(x['id'])
                _say('   %-10s %s' % (x['id'], lv._brief(r)))
        elif due:
            _say('   （--dry：不推进）')

    if not todo:
        _say('\n没有要重算的。')
        return 0
    if a.dry:
        _say('\n（--dry：不算不写）')
        return 0

    # ---- 重算。不一致时 make_signal 会归档旧版 + 记 revision ----
    _say('')
    changed = 0
    for aid, _w in todo:
        r = lv.make_signal(aid, force=True)
        if r.get('error'):
            _say('   [%s] 🔴 %s' % (aid, r['error']))
            continue
        revs = r.get('revisions') or []
        new = [x for x in revs if x.get('replaced_at')
               and x['replaced_at'][:10] == today.isoformat()]
        if new:
            changed += 1
            d = new[-1]['diff']
            _say('   [%s] %s  🔴 【与之前那份不一致】（第 %d 版）'
                 % (aid, r['for_date'], new[-1]['rev']))
            for k in ('buy', 'sell', 'hold'):
                for tag, key in (('新增', k + '_added'), ('移除', k + '_removed')):
                    if d.get(key):
                        _say('        %s%s：%s' % (k, tag, ', '.join(d[key])))
        else:
            _say('   [%s] %s  卖%d 买%d 持有%d（与之前一致）'
                 % (aid, r['for_date'], len(r.get('sell') or []),
                    len(r.get('buy') or []), len(r.get('hold') or [])))
    if changed:
        _say('')
        _say('🔴 有 %d 个账户的清单变了 —— 如果已经按之前那份准备了委托，'
             '请照新的核对。旧版存在 signals/<日期>.rev<N>.json。' % changed)

    return 0


if __name__ == '__main__':
    sys.exit(main() or 0)
