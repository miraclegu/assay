"""srv/rt.py —— 盘中 1 分钟线的抓取循环与接口。

★ 被 srv/live.py 与 srv/watch.py 单向依赖（_rt_ensure）；
  反过来它调 live/watch 走的是 base 里的延迟导入封装 —— 循环由此断开。"""
import ast
import json
import os
import re
import time
from datetime import datetime
from datetime import time as dtime

from . import base
from .base import (_alerts, _live, _rt, _watch)


_RT = {'thread': None, 'last': None, 'rounds': 0, 'err': None,
       'catch_at': 0}



def _rt_codes():
    """要抓哪些 —— **持仓 ∪ 自选 ∪ 股息率买点清单**，按代码去重。

    🔴 **去重是硬要求**（用户明确说"不要重复调用"）：持仓的票会被自动同步
      进自选，所以一只票通常同时出现在持仓和自选里；同一只还可能被手工加进
      多个分组。用 set 合并 —— 按"持仓一份、自选一份"分别抓的话，
      请求量直接翻倍，而限流是这条链上唯一的风险。

    ★ 自选页与持仓页读的是**同一个实时库**（`datalake/rt/`），
      页面侧一次接口都不多打。这和 table-data-viewer 的
      「全局共享行情缓存，跨所有页签复用」是同一个思路。
    """
    m = _live()
    out = set()
    for a in m.load_accounts():
        if a.get('archived'):
            continue
        try:
            out.update(m.positions(a['id']))
        except Exception:                                   # noqa: BLE001
            pass
    try:
        out.update(x['code'] for x in _watch().current())
    except Exception:                                       # noqa: BLE001
        pass
    try:
        # 股息率买点清单：它存在的全部意义就是"价格到了叫我"，
        # 不抓价就什么都不会发生。同一只票已在持仓/自选里的话 set 会去重。
        out.update(_alerts().codes())
    except Exception:                                       # noqa: BLE001
        pass
    return sorted(out)



def _rt_loop():
    """守护线程：交易时段内每 60s 一轮。

    ★ 时段与交易日都在这里判：
      · 9:30~11:31 / 13:00~15:01（收盘那根要到 15:01 才拿得到）
      · 非交易日整天不抓 —— 日历取不到（None）时**照抓**：
        "不知道是不是交易日"和"确定不是"是两件事，前者宁可多抓一次。
    """
    import time as _t
    rt = _rt()
    while True:
        try:
            now = datetime.now()
            day_ok = rt.is_trading_day()
            if rt.in_session(now) and day_ok is not False:
                codes = _rt_codes()
                if codes:
                    # 收盘那一轮对全部持仓强抓，保证当天 bar 完整
                    force = now.time() >= dtime(15, 0)
                    r = rt.poll_once(codes, force_all=force)
                    _RT['last'] = r
                    _RT['rounds'] += 1
                    _RT['err'] = r.get('fail') or None
                    _fire_alerts()
        except Exception as e:                              # noqa: BLE001
            _RT['err'] = [{'stage': 'loop', 'error': '%s: %s' % (type(e).__name__, e)}]
            print('[rt] 异常: %s: %s' % (type(e).__name__, e), flush=True)
        _t.sleep(60)



def _notify(title, text):
    """macOS 通知中心。**失败就算了** —— 提醒不该把轮询搞挂。

    ★ 用 osascript 而不是第三方库：不引依赖，且 serve.py 关着的时候
      本来就不该有通知（提醒只在"看板开着"时有意义）。
    """
    try:
        import shlex
        import subprocess
        subprocess.run(
            ['osascript', '-e',
             'display notification %s with title %s sound name "Ping"'
             % (shlex.quote(text[:200]), shlex.quote(title[:60]))],
            timeout=5, capture_output=True, check=False)
        return True
    except Exception:                                       # noqa: BLE001
        return False



def _fire_alerts():
    """价格到了就提醒。**判据与去重都在 alerts.check_fire 里**（一处）。

    🔴 通知一多就没人看了（同"假告警看多了就不看告警"那条），所以同一
      (code, 档, 类型) 一天只发一次 —— 去重依据落盘在 alerts_fired.jsonl，
      重启也不会把今天提醒过的又提醒一遍。
    """
    if not base.ALLOW_LIVE:
        return []
    try:
        a = _alerts()
        new = a.check_fire()
        for r in new:
            _notify('assay · 买点到了' if r['kind'] == 'hit'
                    else 'assay · 接近买点', a.notify_text(r))
        if new:
            _RT['fired'] = new
        return new
    except Exception as e:                                  # noqa: BLE001
        _RT['err'] = [{'stage': 'alerts',
                       'error': '%s: %s' % (type(e).__name__, e)}]
        return []


# 距上次"补抓"至少隔这么久，避免页面一刷新就打一串请求

_RT_MIN_GAP = 20.0


# 同一只票补抓失败后至少隔这么久再试。退市/停牌的票永远 missing，
# 不设冷却的话每次打开页面都会为它打一次请求。

_RT_MISS_GAP = 180.0



def _rt_ensure(codes):
    """**刚进抓取范围的票立刻补一次实时数据。**

    为什么需要：轮询每 60s 才重算一次范围（持仓 ∪ 自选），bar 还是轮转抓的
    （~42 分钟一圈）。而人加完自选、录完成交是**马上**要看的 —— 那一刻页面上
    一片"收盘价"，看着像功能没生效。

    ★ 只抓 `realtime.missing()` 认定的那几只，抓到就不再 missing ——
      天然收敛，不会变成"每次刷新都打一遍"。
    ★ 每只票带 180s 冷却：退市/停牌的票永远 missing，没有冷却的话它会在
      每次打开页面时都换来一次请求。
    ★ 与轮询同一个开关（--live）：realtime 的规矩是"只有 server 开着才调
      接口"，只读模式下不该因为打开个页面就去打外部接口。
    """
    if not base.ALLOW_LIVE:
        return None
    rt = _rt()
    try:
        miss = rt.missing(codes)
    except Exception:                                       # noqa: BLE001
        return None
    now = time.time()
    seen = _RT.setdefault('miss_at', {})
    todo = [c for c in miss if now - (seen.get(c) or 0) >= _RT_MISS_GAP]
    if not todo:
        return None
    for c in todo:
        seen[c] = now
    try:
        r = rt.ensure_codes(todo)
        _RT['ensure'] = dict(r, at=datetime.now().replace(
            microsecond=0).isoformat())
        return r
    except Exception as e:                                  # noqa: BLE001
        _RT['err'] = [{'stage': 'ensure',
                       'error': '%s: %s' % (type(e).__name__, e)}]
        return None



def _rt_catch_up(max_stale=2):
    """落后就补一次。**有节流**：多个标签页同时刷新不该变成一串请求。"""
    rt = _rt()
    now = time.time()
    if now - (_RT.get('catch_at') or 0) < _RT_MIN_GAP:
        return None
    try:
        st = rt.stale_minutes()
        if st is None or st <= max_stale:
            return None
        codes = _rt_codes()
        if not codes:
            return None
        _RT['catch_at'] = now
        r = rt.poll_once(codes)
        _RT['last'] = r
        _RT['rounds'] += 1
        return r
    except Exception as e:                                  # noqa: BLE001
        _RT['err'] = [{'stage': 'catch_up', 'error': '%s: %s' % (type(e).__name__, e)}]
        return None



def api_rt_indices(_q):
    """GET /api/rt/indices —— 主要指数的实时快照（所有页面底部那条带子）。

    ★ 清单与缓存都在 `realtime.indices()` **一处**（同「可选清单由服务端
      给」）：前端硬编码的话，加一个指数页面上不会出现、删一个会显示
      一条取不到的空行。
    ★ 返回里带 `session`（是不是交易时段）—— 页面据此决定轮不轮询。
      **前端不自己判时段**：改了时段或遇到半日市会白轮/漏轮，而
      "多轮几次"不报错、"该轮没轮"更不报错（同实盘页 `rt_live` 那条）。
    """
    return _rt().indices()


def api_rt_status(_q):
    """GET /api/rt/status —— 1 分钟线库的状态 + 轮询情况。"""
    rt = _rt()
    try:
        st = rt.status()
        st['poll'] = {'rounds': _RT['rounds'], 'last': _RT['last'],
                      'err': _RT['err'], 'running': bool(_RT['thread'])}
        st['stale_minutes'] = rt.stale_minutes()
        st['codes_watched'] = _rt_codes() if base.ALLOW_LIVE else []
        # 最近一次"新票补抓"（加自选 / 新买入触发的那次）
        st['ensure'] = _RT.get('ensure')
        # 还有哪几只今天一条实时数据都没有 —— 页面要能看见，
        # 不然"这只票没实时价"会被当成整条链没工作
        if base.ALLOW_LIVE:
            try:
                st['no_data'] = rt.missing(st['codes_watched'])
            except Exception:                               # noqa: BLE001
                pass
        return st
    except Exception as e:                                  # noqa: BLE001
        return {'error': '%s: %s' % (type(e).__name__, e)}



def api_rt_bars(q):
    """GET /api/rt/bars?code=&day= —— 某只票当日 1 分钟线（画分时用）。"""
    rt = _rt()
    try:
        return {'code': q.get('code'), 'bars': rt.bars(q.get('code') or '',
                                                       q.get('day'))}
    except Exception as e:                                  # noqa: BLE001
        return {'error': '%s: %s' % (type(e).__name__, e)}



def api_rt_poll(_q, body):
    """POST /api/rt/poll —— 立刻抓一次（页面上的"刷新实时价"）。"""
    if not base.ALLOW_LIVE:
        return {'error': '服务以只读模式启动 —— 用 python3 serve.py --live 开启'}
    rt = _rt()
    try:
        codes = _rt_codes()
        if not codes:
            return {'error': '没有持仓，没什么可抓的'}
        r = rt.poll_once(codes, force_all=bool((body or {}).get('all')))
        _RT['last'] = r
        _RT['rounds'] += 1
        return r
    except Exception as e:                                  # noqa: BLE001
        return {'error': '%s: %s' % (type(e).__name__, e)}


# ==================== 数据同步状态（只读）====================
# ★ 判据【不在这里实现】—— 调 datalake/build/sync_status.py。
#   新鲜度写两遍必然漂移（脚本说没问题、页面说落后 3 天）。
#   同步本身也不在这里跑：调度是 launchd 的事（daily_snapshot.py 漏一天
#   永久丢失，不能挂在「看板恰好开着」上）。这里只提供【看】+【手动触发一次】。
