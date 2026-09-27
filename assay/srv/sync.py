"""srv/sync.py —— 数据同步状态 / 自动同步开关（launchd）/ 聚宽财务导入。"""
import json
import os
import re
import sys
import threading
import time
from datetime import date, datetime, timedelta

from . import base
from .base import (_datalake_dir, _live)
from . import runs as _runs  # _JOBS, _run_job


def api_sync(_q):
    """GET /api/sync —— 两条腿的新鲜度 + 上次同步日志摘要。"""
    dl = _datalake_dir()
    out = {'datalake': dl, 'readonly': not base.ALLOW_LIVE}
    script = os.path.join(dl, 'build', 'sync_status.py')
    if not os.path.isfile(script):
        return dict(out, error='找不到 %s' % script)
    import subprocess
    try:
        r = subprocess.run(['python3', script, '--json'], cwd=dl,
                           capture_output=True, text=True, timeout=120)
        out['status'] = json.loads(r.stdout) if r.returncode == 0 else None
        if out['status'] and out['status'].get('calendar'):
            c = out['status']['calendar']
            c['authoritative'] = c.get('source') in _live().AUTHORITATIVE_CAL
        if out['status'] is None:
            out['error'] = (r.stderr or r.stdout or '')[-400:]
    except Exception as e:                                  # noqa: BLE001
        out['error'] = '%s: %s' % (type(e).__name__, e)
    # 最近几次同步日志（只列文件名与大小，内容按需取）
    ld = _sync_log_dir()           # 写/列/读同一处（见 _sync_log_dir）
    logs = []
    if os.path.isdir(ld):
        for fn in sorted(os.listdir(ld), reverse=True)[:12]:
            if not fn.endswith('.log'):
                continue
            p = os.path.join(ld, fn)
            logs.append({'name': fn, 'bytes': os.path.getsize(p),
                         'mtime': datetime.fromtimestamp(
                             os.path.getmtime(p)).replace(microsecond=0).isoformat()})
    out['logs'] = logs
    # ★ 自动同步状态跟着一起给 —— 按钮的文字要照它显示（"开启"还是"关闭"），
    #   前端不能自己猜。也不该为这一个字段再打一次请求。
    out['auto'] = autosync_status()
    # ★ 定时窗口与「实际装上的点位」一起给页面 —— 页面要能看出
    #   "配置改了但 timer 没重装"（那不报错）
    try:
        out['sched'] = _setup_tdx().show_schedule()
    except Exception as e:                                      # noqa: BLE001
        out['sched'] = {'error': '%s: %s' % (type(e).__name__, e)}
    return out


# ============================ 自动同步（launchd） ============================
# ★ "自动同步"就是那个 launchd agent，没有第二套定时。serve.py 里【不做】
#   定时线程 —— daily_snapshot.py 是漏一天永久丢失的（tdx 的名称/分类/板块
#   成分是 type-1 覆盖写），挂在"看板恰好开着"上不可靠。launchd 还会在机器
#   睡过预定时刻后醒来补跑。所以这个开关只是 load / unload 那个 agent。

SYNC_LABEL = 'com.miraclegu.finacial.sync'

SYNC_PLIST = SYNC_LABEL + '.plist'



def _launch_agents_dir():
    return os.path.join(os.path.expanduser('~'), 'Library', 'LaunchAgents')



def _sync_plist_paths():
    """(仓库里的正本, 已安装的那份)。"""
    return (os.path.join(_datalake_dir(), '_manifest', SYNC_PLIST),
            os.path.join(_launch_agents_dir(), SYNC_PLIST))



def autosync_status():
    """自动同步开着还是关着 —— 按钮要照这个显示文字，不能瞎猜。

    🔴🔴 **判据整个转发给 `setup_tdx.show_schedule()`，这里不再自己查。**
      原来这儿是**第二份实现**，有三处各自出错而都不报错：

        ① 只认 launchd（`supported = sys.platform == 'darwin'`）——
           而定时器 2026-09-26 已经跨平台了（Windows 走 schtasks）。
           于是 Windows 上这个开关**永远显示"不支持"**。
        ② 只管 sync 一条，**tick 那条不管** —— 关掉"自动同步"之后
           信号重算每小时照跑（判据 ② 拒绝、日志里一串"A 腿落后"）。
        ③ `schedule` 用正则抠**第一个** Hour/Minute —— 25 个点位只报
           一个，页面上写着「每日 16:00」，而实际是 16:00~20:00 每 10 分。
           **那是在说谎**，不是少说。

    ★ 旧字段名（on / supported / label / plist / schedule / drift）保留 ——
      前端与既有用例都在读它们（同「命令行是产品契约」那条）。
    """
    try:
        m = _setup_tdx()
        d = m.show_schedule()
    except Exception as e:                                  # noqa: BLE001
        return {'supported': None, 'on': None,
                'error': '%s: %s' % (type(e).__name__, e)}

    ins, sc = d.get('installed') or {}, d.get('schedule') or {}
    tasks = []
    for k, name in (('sync', '数据同步'), ('tick', '信号重算')):
        v, w = dict(ins.get(k) or {}), sc.get(k) or {}
        v.update({'key': k, 'name': name, 'window': w,
                  'window_text': _window_text(w, v.get('wrap'))})
        tasks.append(v)
    # 🔴 **两条都开着才算"开"** —— 只看 sync 的话，tick 掉了之后页面写着
    #   "开"，而早上的信号重算其实已经没了（同「漏装了的表现是信号永远
    #   停在昨晚那份，不报错」）。部分开着要单独说，不能假装正常。
    on_n = sum(1 for t in tasks if t.get('on'))
    out = {'label': SYNC_LABEL, 'tasks': tasks,
           'supported': bool(tasks) and tasks[0].get('on') is not None
                        or sys.platform in ('darwin', 'win32', 'linux'),
           'on': on_n == len(tasks) and on_n > 0,
           'partial': 0 < on_n < len(tasks),
           'plist': (ins.get('sync') or {}).get('label') or SYNC_LABEL,
           'schedule': _window_text((sc.get('sync') or {}),
                                    (ins.get('sync') or {}).get('wrap')),
           # 配置与实际装上的不一致 —— 旧字段名叫 drift，含义没变
           'drift': any(t.get('match') is False for t in tasks)}
    if d.get('warn'):
        out['note'] = d['warn']
    return out


def _window_text(w, wrap=False):
    """'16:00 ~ 20:00 每 10 分钟'。跨午夜要把「次日」说出来。"""
    if not w:
        return None
    return '%s ~ %s%s 每 %s 分钟' % (w.get('from'), '次日 ' if wrap else '',
                                     w.get('to'), w.get('every'))


def _setup_tdx():
    """把 datalake/setup_tdx.py 当模块用（窗口配置的读写校验都在那儿）。

    ★ 判据只在那一处 —— 看板与命令行 `--install-timer` 必须用同一套，
      分两处写就会出现"页面存下去的配置，装 timer 时又被判成不合法"。
    """
    import importlib.util
    dl = base._datalake_dir()
    p = os.path.join(dl, 'setup_tdx.py')
    spec = importlib.util.spec_from_file_location('_setup_tdx_mod', p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def api_sync_schedule(_q):
    """GET /api/sync/schedule —— 窗口配置 + **实际装上的点位**。

    🔴 只回显配置是不够的：配置改了而 timer 没重装时，
      "页面写着每小时一次、实际还是旧的"**不报错**。所以这里同时给出
      已装 plist 里的点位数与 `match`，页面必须显示它
      （同「已安装的 plist 与仓库正本不一致要报出来」那条）。
    """
    try:
        return _setup_tdx().show_schedule()
    except Exception as e:                                      # noqa: BLE001
        return {'error': '%s: %s' % (type(e).__name__, e)}


def api_sync_schedule_set(_q, body):
    """POST /api/sync/schedule —— 存配置**并立即重装** timer。

    ★ 存完就装，不给"存了但没生效"留窗口。装完再读一次实际点位回给页面 ——
      判据是"现在装上的是什么"，不是"我刚写了什么"。
    """
    if not base.ALLOW_LIVE:
        return {'error': '服务以只读模式启动 —— 用 python3 serve.py 开启'}
    m = _setup_tdx()
    sc = (body or {}).get('schedule') or {}
    ok, why = m.check_schedule(sc)
    if not ok:
        return {'error': '配置不合法：%s' % why}
    import subprocess
    m.save_schedule(sc)
    r = subprocess.run([sys.executable, os.path.join(
        base._datalake_dir(), 'setup_tdx.py'), '--install-timer'],
        capture_output=True, text=True, timeout=180)
    out = m.show_schedule()
    out['install_log'] = ((r.stdout or '') + (r.stderr or ''))[-1200:]
    out['install_rc'] = r.returncode
    bad = [k for k, v in out['installed'].items() if v.get('match') is False]
    if r.returncode != 0 or bad:
        out['error'] = ('存下了，但重装没成功（%s）—— 定时还是旧的。'
                        '看 install_log' % (bad or 'rc=%d' % r.returncode))
    return out


_AUTO_SUM = {'at': 0.0, 'd': None}


def _autosync_cached(force=False):
    """横条那条轮询用的自动同步状态 —— **缓存 20 秒**。

    🔴 `autosync_status()` 要起 `launchctl list` / `schtasks /query` 子进程，
      而横条在**每个页面**上每 2~10 秒轮一次 —— 不缓存就是每台标签页每几秒
      一个子进程（同 `_setup_summary` 那条 20 秒缓存的理由）。
    ★ 开关点完之后 `force=True` 主动失效：否则刚装上还有 20 秒写着
      「还没开自动同步」，**那看着像没生效**。
    """
    import time as _t
    if not force and _AUTO_SUM['d'] is not None and _t.time() - _AUTO_SUM['at'] < 20:
        return _AUTO_SUM['d']
    try:
        d = autosync_status()
    except Exception as e:                                  # noqa: BLE001
        d = {'error': '%s: %s' % (type(e).__name__, e), 'on': None}
    _AUTO_SUM['at'], _AUTO_SUM['d'] = _t.time(), d
    return d


def api_sync_auto(_q):
    """GET /api/sync/auto —— 自动同步的当前状态。"""
    return autosync_status()



def api_sync_auto_set(_q, body):
    """POST /api/sync/auto —— 开/关自动同步（两条定时一起）。

    🔴 **走子进程跑 `setup_tdx.py --install-timer / --uninstall-timer`**，
      不在这儿自己拼 launchctl：
        · 那是**跨平台**的正本（launchd / systemd+cron / schtasks 三套），
          自己拼一份的话 Windows 上这个按钮就是死的；
        · `install_timer` 里 `raise SystemExit`（BaseException）——
          在请求线程里直接把线程打死、页面只看到一个没有原因的 500
          （同 `resolve_lake` 那条）。子进程把它变成返回码 + 日志。
    🔴 **两条一起开关**：tick 是 sync 的下半截（数据到了要重算信号），
      只关一条的表现是"日志里每小时一条『A 腿落后，拒绝重算』"——
      那是噪声不是工作（同「一起装是因为它们配套」）。

    ★ 判据仍然是**复查到的状态**，不是命令返回码 ——
      `launchctl` 对"已经是这个状态"会报错退出，而那不是失败。
    """
    if not base.ALLOW_LIVE:
        return {'error': '服务以只读模式启动 —— 用 python3 serve.py 开启'}
    import subprocess
    on = bool((body or {}).get('on'))
    before = autosync_status()
    if before.get('error'):
        return dict(before, changed=False)
    if not on and not (before.get('on') or before.get('partial')):
        return dict(autosync_status(), changed=False, note='本来就没装，无需关闭')

    flag = '--install-timer' if on else '--uninstall-timer'
    r = subprocess.run(
        [sys.executable, os.path.join(base._datalake_dir(), 'setup_tdx.py'),
         flag], capture_output=True, text=True, timeout=180)
    st = _autosync_cached(force=True)
    st = dict(st)
    st['log'] = ((r.stdout or '') + (r.stderr or ''))[-1500:]
    st['rc'] = r.returncode
    # ★ 复查：说"开了"就得真的两条都在，说"关了"就得一条都不剩。
    if st.get('on') != on:
        st['changed'] = False
        st['error'] = '%s 失败%s —— 看 log' % (
            '开启' if on else '关闭',
            '（只成功了一半）' if st.get('partial') else '')
        return st
    st['changed'] = True
    st['cmd'] = 'setup_tdx.py %s' % flag
    return st


# ==================== 个股（搜索 / 面板 / K 线 / 财务） ====================
# ★ 这一层【不做 SQL 输入】—— 那是「🔍 查数据」页的事。个股页给的是
#   固定的几个问题的答案（这只票现在什么样、K 线什么形态、财务什么趋势），
#   所以接口是固定形状的，不接受任意查询。



JQ_SCRIPT = os.path.join('raw', 'jq', '_ingest', 'extract_jq_increment.py')
# 上传体积上限。财务增量实测几十 MB；给到 512MB 是为了万一整批重抽。

JQ_UPLOAD_MAX = 512 * 1024 * 1024

JQ_NAME_RE = re.compile(r'^[A-Za-z0-9._-]{1,80}$')



def _jq_overlap_days():
    """SINCE 往前留几天重叠。

    ★ 留重叠是刻意的：合并按自然键去重，**重叠不会重复，而缺口会静默丢数据**。
      这条写在 extract 脚本的文件头，这里只是把它变成默认值。
    """
    return 5



def jq_suggest():
    """按本地当前状态算出该填的 SINCE / QUARTERS。

    ★ 让人手填这两个值是最容易出错的一处：SINCE 填晚了就是**静默丢数据**
      （中间那几天的公告永远补不回来，除非重抽）。本地各表的最新 pub_date
      服务端知道，直接算给它。
    """
    import subprocess
    dl = _datalake_dir()
    out = {'since': None, 'quarters': None, 'b_max': {}, 'note': None}
    try:
        r = subprocess.run(['python3', os.path.join(dl, 'build', 'sync_status.py'),
                            '--json'], cwd=dl, capture_output=True, text=True,
                           timeout=120)
        items = json.loads(r.stdout).get('items') or []
    except Exception as e:                                  # noqa: BLE001
        out['note'] = '读不到本地状态（%s），SINCE 请自己填' % type(e).__name__
        return out
    mx = {}
    for it in items:
        if it.get('leg') == 'B' and it.get('max'):
            mx[it['key']] = it['max']
    out['b_max'] = mx
    if not mx:
        out['note'] = '本地还没有财务数据 —— 这是首次导入，SINCE 请自己定'
        return out
    oldest = min(mx.values())
    d = datetime.strptime(oldest, '%Y-%m-%d').date() - \
        timedelta(days=_jq_overlap_days())
    out['since'] = d.isoformat()
    out['oldest'] = oldest
    # 报告期：当前季与上一季（重抽顺带捡回财报重述）
    today = date.today()
    q = (today.month - 1) // 3 + 1
    cur = (today.year, q)
    prev = (today.year, q - 1) if q > 1 else (today.year - 1, 4)
    out['quarters'] = ['%dq%d' % cur, '%dq%d' % prev]
    return out



def api_sync_jq_code(_q):
    """GET /api/sync/jq_code —— 给出可直接粘进聚宽研究环境的代码。

    ★ 正本是磁盘上那个脚本（datalake/raw/jq/_ingest/extract_jq_increment.py），
      这里只把「改这两处」的 SINCE / QUARTERS 按本地状态替换掉。
      **不在这里另写一份** —— 两份一定会分叉，而分叉的那份跑出来的数据
      看着正常。
    """
    dl = _datalake_dir()
    p = os.path.join(dl, JQ_SCRIPT)
    out = {'path': p, 'suggest': jq_suggest()}
    if not os.path.isfile(p):
        return dict(out, error='找不到抽取脚本：%s' % p)
    src = open(p, encoding='utf-8').read()
    sug = out['suggest']
    subs = []
    if sug.get('since'):
        src, n = re.subn(r"^SINCE = '[^']*'",
                         "SINCE = '%s'" % sug['since'], src, count=1,
                         flags=re.M)
        if n:
            subs.append('SINCE=%s' % sug['since'])
    if sug.get('quarters'):
        q = ', '.join("'%s'" % x for x in sug['quarters'])
        src, n = re.subn(r'^QUARTERS = \[[^\]]*\]',
                         'QUARTERS = [%s]' % q, src, count=1, flags=re.M)
        if n:
            subs.append('QUARTERS=[%s]' % q)
    # ★ 替换失败要说出来，不能默默给一份没改的：那会让人以为已经按本地状态
    #   填好了，而 SINCE 停在几个月前就是静默丢数据。
    out['substituted'] = subs
    out['code'] = src
    out['bytes'] = len(src.encode('utf-8'))
    if sug.get('since') and 'SINCE=%s' % sug['since'] not in subs:
        out['warn'] = ('没能自动替换 SINCE（脚本里那一行的格式变了？）——'
                       '粘过去之后请手动把 SINCE 改成 %s' % sug['since'])
    return out



def api_sync_jq_upload(headers, raw):
    """POST /api/sync/jq_upload —— 上传聚宽导出的 tar，随即 merge + 跑全部 loader。

    ★ 走【原始字节】而不是 JSON+base64：几十 MB 的 base64 要多传 33%，
      而且 json.loads 会把整包再复制一遍。
    ★ 文件名来自请求头，**只接受 [A-Za-z0-9._-]** 且只落到 _ingest/downloads
      —— 不能拿它直接 join（路径穿越）。
    """
    if not base.ALLOW_LIVE:
        return {'error': '服务以只读模式启动 —— 用 python3 serve.py --live 开启'}
    name = (headers.get('X-Filename') or '').strip()
    if not JQ_NAME_RE.match(name):
        return {'error': '文件名不合法：%r（只接受字母数字 . _ -）' % name}
    if not (name.endswith('.tar') or name.endswith('.tar.gz')
            or name.endswith('.tgz')):
        return {'error': '要上传聚宽脚本打包出来的 .tar（收到 %s）' % name}
    if not raw:
        return {'error': '上传内容是空的'}
    dl = _datalake_dir()
    dst_dir = os.path.join(dl, 'raw', 'jq', '_ingest', 'downloads')
    os.makedirs(dst_dir, exist_ok=True)
    # 同名不覆盖：加时间戳。上传的包是**原始凭据**，覆盖了就没法复查
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    dst = os.path.join(dst_dir, '%s__%s' % (stamp, name))
    tmp = dst + '.part'
    with open(tmp, 'wb') as fh:
        fh.write(raw)
    os.replace(tmp, dst)                       # 先写 .part 再 rename，同 _save_marks
    # 立刻验一下是不是真的 tar —— 坏包早失败，别等 merge 跑一半
    import tarfile
    try:
        with tarfile.open(dst) as t:
            members = [m.name for m in t.getmembers()]
    except Exception as e:                                  # noqa: BLE001
        os.remove(dst)
        return {'error': '不是可读的 tar（%s: %s）—— 已删除' % (type(e).__name__, e)}
    if not members:
        os.remove(dst)
        return {'error': 'tar 是空的 —— 已删除。聚宽那边可能一张表都没抽到'}
    merge = os.path.join(dl, 'build', 'merge_jq_increment.py')
    if not os.path.isfile(merge):
        return {'error': '找不到 %s' % merge}
    cmd = ['python3', merge, dst, '--and-load']
    job_id = 'jq-%s' % datetime.now().strftime('%H%M%S')
    _runs._JOBS[job_id] = {'state': 'running', 'lines': [], 'cmd': cmd,
                     'sha': None, 'run_id': None, 'rc': None}
    threading.Thread(target=_runs._run_job, args=(job_id, cmd, dl),
                     daemon=True).start()
    return {'job_id': job_id, 'saved': dst, 'bytes': len(raw),
            'members': members, 'cmd': ' '.join(cmd)}



# 🔴 **白名单**，不是黑名单 —— 文件名是从 URL 回来的，直接 join 就是
#   目录穿越。三种形状：
#     20260927-161500.log                 每日同步链（sync_daily 自己写的）
#     setup-20260927-161500.log           装配一键链
#     setup-bootstrap-20260927-161500.log 单独重跑某一个阶段
#   ★ 装配日志**带 `setup-` 前缀**而不是混用同一种名字：「最近同步日志」
#     那张表会把两者列在一起，名字一样的话人看不出哪个是建库、哪个是每日
#     增量（同「不拿 ETF 当指数用：近似物要叫自己的名字」）。
_LOG_RE = re.compile(
    r'^(?:setup-(?:[a-z0-9_]{1,20}-)?)?[0-9]{8}-[0-9]{6}\.log$')



def api_sync_log(q):
    """GET /api/sync/log?name=... —— ★ 只接受 8位日期-6位时间.log 这个形状，
    且只在 _manifest/sync_logs 下找。文件名来自 URL，不能直接 join。"""
    name = (q.get('name') or '').strip()
    if not _LOG_RE.match(name):
        return {'error': '日志名格式不对：%r' % name}
    p = _setup_log_path(name)      # 读与写同一处，不许各拼各的
    if not os.path.isfile(p):
        return {'error': '日志不存在：%s' % name}
    txt = open(p, encoding='utf-8', errors='replace').read()
    return {'name': name, 'text': txt[-200000:], 'bytes': os.path.getsize(p)}



def api_sync_run(_q, body):
    """POST /api/sync/run —— 手动触发一次同步（后台子进程，复用 _JOBS）。

    ★ 只有【手动】走这里；定时永远是 launchd。理由见 sync_daily.sh 文件头。
    """
    if not base.ALLOW_LIVE:
        return {'error': '服务以只读模式启动，手动同步已关闭。'
                         '用 python3 serve.py --live 开启。'}
    dl = _datalake_dir()
    # 🔴 **调 .py 不调 .sh** —— Windows 上没有 bash。正本本来就在
    #   `sync_daily.py`（`.sh` 只是一行转发，留着是因为命令行是产品契约）。
    #   走 `sys.executable` 而不是字面量 'python3'：venv 里那个才是对的，
    #   而 Windows 上根本没有 `python3` 这个名字。
    py = os.path.join(dl, 'sync_daily.py')
    if not os.path.isfile(py):
        return {'error': '找不到 %s' % py}
    cmd = [sys.executable, py]
    if (body or {}).get('no_live'):
        cmd.append('--no-live')
    job_id = 'sync-%s' % datetime.now().strftime('%H%M%S')
    _runs._JOBS[job_id] = {'state': 'running', 'lines': [], 'cmd': cmd,
                     'sha': None, 'run_id': None, 'rc': None}
    threading.Thread(target=_runs._run_job, args=(job_id, cmd, dl),
                     daemon=True).start()
    return {'job_id': job_id, 'cmd': ' '.join(cmd)}


# ==== 数据装配：从零把本地数据建起来（Windows / Mac 通用） ====
#
# 用户："没有数据也要能启动 server，然后点击数据加载开始同步数据。"
#
# 🔴 阶段清单与**当前状态**都由 `datalake/setup_stages.py` 给 —— 页面里
#   不许出现任何一个阶段名或命令（同「可选清单由服务端给」「加一个指标，
#   广场上自动就有」）。加一个阶段页面上自动就有，**而写死的话不报错**。

def _stages_mod():
    """晚绑定地 import `setup_stages` —— datalake 的路径是运行时才知道的。"""
    dl = _datalake_dir()
    p = os.path.join(dl, 'setup_stages.py')
    if not os.path.isfile(p):
        return None, '找不到 %s' % p
    if dl not in sys.path:
        sys.path.insert(0, dl)
    import importlib
    import setup_stages as m
    importlib.reload(m)       # 🔴 每次重读：进程可能比那个文件老
    return m, None


def api_setup(_q):
    """GET /api/setup —— 现在有什么数据、还差哪几步。

    ★ 这一发**任何模式都给**（含 `--readonly`）：它只读磁盘状态，
      而「我这台机器有什么数据」正是空 lake 上最需要回答的问题。
    """
    m, err = _stages_mod()
    if err:
        return {'error': err}
    d = m.summary()
    d.update(_setup_flags())
    # 已经在跑的那个（页面刷新之后要接得回去 —— 否则看着像"点了没反应"）
    for jid, j in list(_runs._JOBS.items()):
        # ★ `':' not in jid` —— 一键链的子任务叫 `<父>:<阶段id>`，它的
        #   state 也是 running。页面要的是那条链的身份（父任务），
        #   而"多久没输出"由 job_live 往下找一层取（一份实现，两处共用）。
        if (jid.startswith('setup-') and ':' not in jid
                and j.get('state') == 'running'):
            idle, nl, log = _runs.job_live(jid)
            d['running'] = {'job_id': jid, 'stage': j.get('stage'),
                            'stage_id': j.get('stage_id'),
                            'log': log, 'n_lines': nl, 'idle': idle}
            break
    return d


def api_setup_run(_q, body):
    """POST /api/setup/run {stage} —— 跑某一个阶段（后台子进程，复用 _JOBS）。

    🔴 **只认清单里的 id，不接受任意命令**：命令由服务端按 id 查出来。
      让页面传命令行的话，这个接口就成了远程执行入口。
    🔴 **一次只许跑一个**：这些步骤重（面板 6 分钟、因子面板 30 分钟），
      而且**互相有依赖**（因子面板吃面板的产物）。并发跑最好的情况是白烧
      一遍 CPU，最坏是下游读到只建了一半的上游 —— **而那不报错**
      （同「先把面板改对再建因子分片，反过来那 45 分钟白付两遍」）。
    """
    if not base.ALLOW_LIVE:
        return {'error': '服务以只读模式启动，装配已关闭。'
                         '用 python3 serve.py 开启（默认就是全功能）。'}
    m, err = _stages_mod()
    if err:
        return {'error': err}
    for jid, j in _runs._JOBS.items():
        if jid.startswith('setup-') and j.get('state') == 'running':
            return {'error': '已经有一个装配任务在跑（%s）——'
                             '它们有先后依赖，等它跑完再点。' % j.get('stage')}
    sid = (body or {}).get('stage')

    # ---- 一键：从没完成的那一步接着跑到底 ----
    # 用户："首次加载，应该是系统启动后展示一个数据初始化的按钮，
    #        然后点击按钮开始加载数据。"
    # 🔴 之前是**七个按钮**、要人自己点六次、还要自己判断上一个跑完没有
    #   —— 同「靠人记得跑的步骤 = 迟早不跑」。首次建库是一条有先后依赖的
    #   链，那就该像 `sync_daily` 那样**一次跑完**。
    if sid == '__all__':
        auto = [x for x in m.stages() if x.get('cmd')]
        if not [x for x in auto if x['state'] == 'todo']:
            return {'error': '本地数据已经齐了 —— 没有要建的步骤。'}
        job_id = 'setup-all-%s' % datetime.now().strftime('%H%M%S')
        # ★ 落盘复用 `_manifest/sync_logs/`：那一套已经有列表（api_sync 的
        #   logs）、有防穿越的读取（api_synclog）、也已经被 logs.trim_by_days
        #   按天清理。另造一个目录就是第二份实现（先查有没有，再决定写不写）。
        log_path = _setup_log_path(
            'setup-%s.log' % datetime.now().strftime('%Y%m%d-%H%M%S'))
        # ★ 日志名**一开始就定下来**（不等第一个子任务跑起来才有）——
        #   否则页面第一次渲染时「看完整日志」那个入口还不存在，
        #   要等下一次重渲染才冒出来（实测：入口迟到了整整一步）。
        _runs._JOBS[job_id] = {'state': 'running', 'lines': [], 'cmd': [],
                               'sha': None, 'run_id': None, 'rc': None,
                               'stage': '建本地数据',
                               'log': os.path.basename(log_path)}
        pg = None
        try:
            import progress as _P
            pg = _P.Progress('setup', '建本地数据（首次批量加载）', len(auto))
        except Exception as e:                              # noqa: BLE001
            # 🔴 **不许静默 pass**：进度文件是顶上那条横条的唯一数据源，
            #   它建不起来的话"到底在不在跑"就再也没地方看得到，
            #   而装配本身照跑 —— 屏幕上就是"点了没反应"。
            #   装配不该被进度拖垮，所以不抛；但必须说一句
            #   （同「保护分支不该静默跳过」）。
            _runs._JOBS[job_id]['lines'].append(
                '⚠ 进度条建不起来（%s: %s）—— 装配照跑，'
                '但顶上那条横条不会显示进度，只能看这里的日志。'
                % (type(e).__name__, e))

        def _go_all():
            j = _runs._JOBS[job_id]
            ids = [x['id'] for x in auto]
            rc = 0
            dlog = _day_setup()
            _day_say(dlog, '=' * 60)
            _day_say(dlog, '建本地数据（一键）—— 还差 %d 步：%s'
                     % (len(auto), ' / '.join(x['name'] for x in auto)))
            while True:
                # 🔴 **每跑完一个就重新查状态**，不照一开始那份清单硬跑到底
                #   —— 判据永远是"现在磁盘上是什么"（同 launchd 那条）。
                #   上一步跑完可能顺带满足了下一步，也可能人在别处补过。
                try:
                    cur = [x for x in m.stages() if x.get('cmd')]
                except Exception as e:                      # noqa: BLE001
                    j['lines'].append('读阶段状态失败: %s' % e)
                    rc = 1
                    break
                nxt = next((x for x in cur if x['state'] == 'todo'), None)
                if not nxt:
                    break
                if pg:
                    pg.step(nxt['name'], at=ids.index(nxt['id']) + 1)
                j['stage'] = nxt['name']
                j['stage_id'] = nxt['id']
                j['lines'].append('')
                j['lines'].append('───── %s ─────' % nxt['name'])
                sub = job_id + ':' + nxt['id']
                _runs._JOBS[sub] = {'state': 'running', 'lines': j['lines'],
                                    'cmd': list(nxt['cmd']), 'sha': None,
                                    'run_id': None, 'rc': None}
                _day_say(dlog, '───── %s ─────' % nxt['name'])
                _t0 = time.time()
                _runs._run_job(sub, list(nxt['cmd']), _datalake_dir(),
                               log_path=log_path)
                rc = _runs._JOBS[sub].get('rc') or 0
                _day_done(dlog, nxt['name'], rc, int(time.time() - _t0),
                          _runs._JOBS[sub].get('lines'), log_path)
                _runs._JOBS.pop(sub, None)
                if pg:
                    pg.finish_step('ok' if rc == 0 else 'bad')
                if rc != 0:
                    # 🔴 **失败就停**：后面每步都吃前一步的产物，带着坏数据
                    #   往下跑会一路传播，**而下游不报错**（同 sync 链那条
                    #   「前置失败就跳过 5~13，不在坏数据上继续加工」）。
                    j['lines'].append(
                        '❌ 「%s」失败（rc=%s）—— 后面几步吃它的产物，'
                        '就停在这里了。修好再点一次，会从这一步接着跑。'
                        % (nxt['name'], rc))
                    break
            j['rc'] = rc
            j['state'] = 'done' if rc == 0 else 'failed'
            _day_say(dlog, '全部结束  rc=%s' % rc)
            if dlog is not None:
                dlog.close()                # 把最后那段折叠计数写出去
            if pg:
                pg.finish(rc)
            _setup_summary(force=True)      # 建完了，横条要立刻反映

        threading.Thread(target=_go_all, daemon=True).start()
        return {'job_id': job_id, 'stage': '建本地数据',
                'total': len(auto),
                'todo': [x['id'] for x in auto if x['state'] == 'todo']}

    st = [s for s in m.stages() if s['id'] == sid]
    if not st:
        return {'error': '没有这个阶段：%r' % sid}
    st = st[0]
    if not st.get('cmd'):
        return {'error': '「%s」要人工完成，没有可跑的命令。' % st['name']}
    cmd = list(st['cmd'])
    job_id = 'setup-%s-%s' % (sid, datetime.now().strftime('%H%M%S'))
    _runs._JOBS[job_id] = {'state': 'running', 'lines': [], 'cmd': cmd,
                           'sha': None, 'run_id': None, 'rc': None,
                           'stage': st['name'], 'stage_id': sid}

    # 🔴 **进度按【整条装配链】报，不是"这次点了 1 步"** —— 人要看的是
    #   「第 4 步 / 共 7 步」。能自动跑的阶段才算分母（⑦ 财务是人工的，
    #   把它算进去会让分母永远差一步跑不满）。
    auto = [x for x in m.stages() if x.get('cmd')]
    pos = [x['id'] for x in auto].index(sid) + 1
    pg = None
    try:
        import progress as _P
        pg = _P.Progress('setup', '数据装配（历史批量加载）', len(auto),
                         None)
        pg.step(st['name'], at=pos)
    except Exception:                                       # noqa: BLE001
        pass                    # 进度坏了不许挡住装配本身

    def _go():
        dlog = _day_setup()
        _lp = _setup_log_path('setup-%s-%s.log' % (
            sid, datetime.now().strftime('%Y%m%d-%H%M%S')))
        _day_say(dlog, '───── %s（单步）─────' % st[0]['name'])
        _t0 = time.time()
        _runs._run_job(job_id, cmd, _datalake_dir(), log_path=_lp)
        _day_done(dlog, st[0]['name'], _runs._JOBS[job_id].get('rc'),
                  int(time.time() - _t0),
                  _runs._JOBS[job_id].get('lines'), _lp)
        if dlog is not None:
            dlog.close()
        if pg:
            rc = _runs._JOBS[job_id].get('rc')
            pg.finish_step('ok' if rc == 0 else 'bad')
            pg.finish(rc if rc is not None else 1)
        _setup_summary(force=True)

    threading.Thread(target=_go, daemon=True).start()
    return {'job_id': job_id, 'stage': st['name'], 'cmd': ' '.join(cmd),
            'step': pos, 'total': len(auto)}


def _sync_log_dir():
    """日志目录 —— **写（装配）、列（api_sync）、读（api_sync_log）共用一处**。

    改之前写那侧我另拼了一份路径，而读那侧还用自己的 —— 于是守卫把写
    重定向到临时目录之后，读那边照旧去生产目录找，报「日志不存在」。
    **两处实现必然分叉**，这次分叉是当场造出来的。

    复用 `_manifest/sync_logs/`：那一套已经有列表、有防穿越的读取、
    也已经被 logs.trim_by_days 按天清理（先查有没有，再决定写不写）。
    ★ 抽成函数还有一个用处：守卫要把它重定向到临时目录，而**打桩整个
      datalake 根会误伤** —— `/api/sync` 也读那个根，一指到空目录它就报错，
      页面于是走进"状态读取失败"那条分支（实测踩过，查了一轮）。
    """
    return os.path.join(_datalake_dir(), '_manifest', 'sync_logs')


def _day_setup():
    """建本地数据那条链的【按天】日志 —— 拿不到就返回 None（不许把装配搞挂）。

    🔴 **只进骨架**（哪一步开始 / rc / 用时 / 失败时的尾部 + 指到明细文件），
      不是每一行。每步的完整输出仍然写 `sync_logs/setup-<阶段>-<时间戳>.log`
      —— 数据页上「看完整日志」点的就是它。两者分工与同步链那边一样：

          setup-<天>.log       今天这台机器建数据都跑了什么、成没成
          setup-<阶段>-<ts>    那一步的全部输出（几百 MB 下载进度都在里面）

      合成一个的话，tdx2db init 那几万行进度会把"哪一步失败了"整个淹掉。
    """
    try:
        dl = _datalake_dir()
        if dl not in sys.path:
            sys.path.insert(0, dl)
        import logs as _logs                                # noqa: E402
        # 🔴 **这里不清日志** —— 清理归 sync / tick 那两条定时链（它们每
        #   10 分钟 / 每小时跑一次，足够了）。放在这里有两个害处：职责不对，
        #   而且那一次 glob 会**把 `_go_all` 的启动拖慢**，
        #   实测把一个既有竞态的窗口撑宽到 3/4 必现
        #   （同 corp 那轮「我把既有竞态的窗口撑宽了」）。
        return _logs.DayLog(_logs.KIND_SETUP, dl, 'setup')
    except Exception as e:                                  # noqa: BLE001
        # 🔴 不许静默：没有这份日志，"那台机器上建到哪一步崩的"就查无对证，
        #   而屏幕上一切正常（同「保护分支不该静默跳过」）。
        print('⚠ 建库按天日志没接上：%s: %s' % (type(e).__name__, e),
              flush=True)
        return None


def _day_say(d, s):
    if d is not None:
        try:
            d.line(s)
        except Exception:                                   # noqa: BLE001
            pass            # 日志写不动不许让装配停下来；接不上那次已经说过了


def _day_done(d, name, rc, sec, lines, log_path):
    """一步跑完 —— 成功一行，失败要把尾部带上并指到明细文件。"""
    if d is None:
        return
    if rc == 0:
        _day_say(d, '✅ %s  %ds' % (name, sec))
        return
    _day_say(d, '❌ %s 失败（rc=%s，%ds）—— 明细见 %s'
             % (name, rc, sec, os.path.basename(log_path or '(无)')))
    for ln in (lines or [])[-8:]:
        _day_say(d, '    ' + ln)


def _setup_log_path(name):
    return os.path.join(_sync_log_dir(), name)


def _setup_flags():
    """「能不能点」与「为什么不能」—— 两个入口（数据页、横条）共用一处。

    各写一份的话，改了只读模式的措辞会有一处跟不上，**而那不报错**。
    """
    ok = bool(base.ALLOW_LIVE)
    return {'can_run': ok,
            'why': None if ok else '服务以只读模式启动，装配按钮已关闭。'}


_SETUP_SUM = {'at': 0.0, 'd': None}


def _setup_summary(force=False):
    """装配状态，**缓存 20 秒** —— 横条在每个页面上都轮询它。

    ★ 实测 `summary()` 冷 0.12 秒 / 热 0.033 秒（要 glob + 查几张 parquet
      的 max(date)）。不缓存的话 N 个标签页就是 N 倍（同指数带子那条）。
    🔴 缓存**只有一个键**，所以不会掉进「miss 就 clear() = 等于没有缓存」
      那个坑（本项目已经犯过三次）。
    ★ 装配跑完时 `force=True` 主动失效 —— 否则刚建完还会有 20 秒
      显示"还差 1 步"，那看着像没生效。
    """
    import time as _t
    if not force and _SETUP_SUM['d'] and _t.time() - _SETUP_SUM['at'] < 20:
        return _SETUP_SUM['d']
    try:
        m, err = _stages_mod()
        d = None if err else m.summary()
    except Exception:                                       # noqa: BLE001
        d = None
    _SETUP_SUM['at'], _SETUP_SUM['d'] = _t.time(), d
    return d


def api_progress(_q):
    """GET /api/progress —— 现在有没有数据加载在跑、跑到第几步、还要多久。

    🔴 **读的是【进度文件】不是 `_JOBS`** —— 每天真正跑同步的是 launchd，
      它不经过 serve.py。只看 `_JOBS` 的话「定时任务正在跑」在页面上
      完全不可见，而那是最常见的情形。
    ★ 这一发**任何模式都给**（含 `--readonly`）：它只读状态。
    ★ 页面**每 2 秒轮一次**，所以这里不做任何重活：读两个小 json 而已。
    """
    try:
        dl = _datalake_dir()
        if dl not in sys.path:
            sys.path.insert(0, dl)
        import progress as _P
        # 🔴 **这里不 reload** —— selftest 靠 `progress.DIR = 临时目录` 把
        #   写操作重定向掉（同 `lv.LIVE` 那条纪律），reload 会把它冲回
        #   生产的 `_manifest/progress/`，于是**用例写进真目录而不报错**。
        rows = _P.read()
    except Exception as e:                                  # noqa: BLE001
        return {'error': '%s: %s' % (type(e).__name__, e), 'jobs': []}

    # 没跑完但进程已经没了的，`read()` 标成 stale —— 页面要说"中断了"，
    # 不许一直转圈（同「线程还活着不等于链条还在工作」）。
    # ★ **刚跑完的那 90 秒也给** —— 否则任务一结束横条当场消失，
    #   人正好走开一分钟就完全不知道"到底成没成"。90 秒之后它自己退场
    #   （常驻一条"一切正常"等于教人忽略这个位置）。
    import time as _t
    def _show(r):
        st, now = r.get('state'), _t.time()
        if st == 'running':
            return True
        # 🔴 中断过（进程没了而文件还写着 running）要说，但**不能常驻** ——
        #   隔了一天还挂在那儿就是噪声，而「数据新鲜度」那条横幅本来就会
        #   接管"数据落后了"这件事（同「假告警看多了就不看告警」）。
        #   下一次跑会覆盖同名文件，所以正常情况下它自己就消失了。
        if st == 'stale':
            return now - r.get('started', now) < 86400
        return bool(r.get('ended')) and now - r['ended'] < 90
    live = [r for r in rows if _show(r)]

    # 🔴 **没有数据时，横条就是那个入口。**
    #   用户："系统启动后展示一个数据初始化的按钮，然后点击按钮开始加载。"
    #   原来这件事只有翻到「🔄 数据」页才看得见 —— 而新机器上第一眼打开的
    #   是首页，那等于**没有入口**（同「只能从个股页工具条里摸到的入口，
    #   等于没有入口」）。
    # ★ 这不违反「顶部横条只在真的要做什么时出现」那条 —— 本地没有数据
    #   **正是**真的要做什么；数据齐了它自己就消失，不是常驻横幅。
    if not [r for r in live if r.get('state') == 'running']:
        su = _setup_summary()
        if su and not su.get('ready'):
            live.append({'job': 'setup', 'kind': 'setup_needed',
                         'title': '本地还没有数据',
                         'state': 'idle', 'i': 0,
                         'total': su.get('n_todo') or 0,
                         'n_todo': su.get('n_todo'),
                         'next_name': su.get('next_name'),
                         # ⚠ 阶段自己声明的估计，**不是实测** —— 页面上写「估」
                         'eta_text': su.get('eta_text'),
                         **_setup_flags()})
        elif su and su.get('ready'):
            # 🔴🔴 **数据建好了 != 它会自己更新。** 装定时任务的入口有三个
            #   （命令行 --install-timer / 数据页那个开关 / 改窗口后重装），
            #   **三个都要人主动做**，而一键建库跑完不装。于是新机器上
            #   「七个阶段全绿、横条自己消失、首页一切正常」，
            #   **而明天起数据再也不更新** —— 同「靠人记得跑的步骤 =
            #   迟早不跑」，且它**不报错**。
            # 🔴 代价还不对称：`daily_snapshot` 是 type-1 覆盖写、
            #   **漏一天永久丢失**（关自动同步那句 confirm 写的就是这条）
            #   —— 没装等于每天都在丢，而且补不回来。
            # ★ **不替人装**：往 ~/Library/LaunchAgents 或 schtasks 里写
            #   条目是系统级副作用，该由人点一下。但「必须说出来」——
            #   而装上之后这一行自己消失（不常驻，同上面那条纪律）。
            au = _autosync_cached()
            if au and not au.get('error') and au.get('supported') \
                    and not au.get('on'):
                live.append({
                    'job': 'autosync', 'kind': 'autosync_needed',
                    'title': '数据已就绪 · 还没开自动同步',
                    'state': 'idle', 'i': 0, 'total': 0,
                    # 窗口口径由服务端给（前端写死的话，改了窗口它不会跟着变）
                    'window': au.get('schedule'),
                    'partial': bool(au.get('partial')),
                    'tasks': [{'name': t.get('name'),
                               'on': t.get('on'),
                               'window_text': t.get('window_text')}
                              for t in (au.get('tasks') or [])],
                    **_setup_flags()})

    # 页面点出来的那些能链到日志；launchd 那条只有日志文件路径。
    for r in live:
        for jid, j in _runs._JOBS.items():
            if j.get('state') == 'running' and jid.split('-')[0] == r['job']:
                r['job_id'] = jid
                break
    # ★ 没有实测 ETA 时，退回阶段自己声明的那个**文字**估计，并标明是估的
    #   —— 「约 6 分钟」比一个空白有用，但不能让它看着像实测出来的。
    try:
        m, err = _stages_mod()
        if m and not err:
            by = {x['name']: x.get('eta') for x in m.stages()}
            for r in live:
                if r.get('eta') is None and r.get('step') in by:
                    r['eta_text'] = by[r['step']]
    except Exception:                                       # noqa: BLE001
        pass
    return {'jobs': live, 'n': len(live)}
