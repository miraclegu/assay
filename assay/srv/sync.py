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
    ld = os.path.join(dl, '_manifest', 'sync_logs')
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
    """自动同步开着还是关着 —— 按钮要照这个显示文字，不能瞎猜。"""
    import subprocess
    src, dst = _sync_plist_paths()
    out = {'label': SYNC_LABEL, 'plist': dst, 'src': src,
           'installed': os.path.isfile(dst), 'src_exists': os.path.isfile(src),
           'supported': sys.platform == 'darwin'}
    if not out['supported']:
        out['on'] = None
        out['note'] = 'launchd 只有 macOS 有；其它平台请自行接 cron'
        return out
    try:
        r = subprocess.run(['launchctl', 'list', SYNC_LABEL],
                           capture_output=True, text=True, timeout=15)
        out['loaded'] = (r.returncode == 0)
    except Exception as e:                                  # noqa: BLE001
        out['loaded'] = None
        out['note'] = '%s: %s' % (type(e).__name__, e)
    out['on'] = bool(out['installed'] and out['loaded'])
    # ★ 已安装那份与仓库正本不一致时要说出来：改了 plist 但没重新安装，
    #   跑的还是旧的（比如时间还停在旧的 18:10），而这**不会报错**。
    if out['installed'] and out['src_exists']:
        try:
            out['drift'] = (open(src, 'rb').read() != open(dst, 'rb').read())
        except Exception:                                   # noqa: BLE001
            out['drift'] = None
    # 计划时间从【已安装那份】里读 —— 显示正在生效的，不是仓库里的
    out['schedule'] = None
    try:
        txt = open(dst if out['installed'] else src, encoding='utf-8').read()
        h = re.search(r'<key>Hour</key>\s*<integer>(\d+)</integer>', txt)
        mi = re.search(r'<key>Minute</key>\s*<integer>(\d+)</integer>', txt)
        if h and mi:
            out['schedule'] = '%02d:%02d' % (int(h.group(1)), int(mi.group(1)))
    except Exception:                                       # noqa: BLE001
        pass
    return out



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


def api_sync_auto(_q):
    """GET /api/sync/auto —— 自动同步的当前状态。"""
    return autosync_status()



def api_sync_auto_set(_q, body):
    """POST /api/sync/auto —— 开/关自动同步（load / unload 那个 launchd agent）。

    ★ 用 `-w`：它同时写 Disabled 标记，重启后仍然生效。不带 -w 的话
      "关掉"只活到下次登录 —— 而那种"以为关了其实又开了"比开着更糟。
    ★ 开启时若 LaunchAgents 下没有或与仓库正本不一致，先复制过去 ——
      否则会 load 到一份旧的 plist，而这不会报错。
    """
    if not base.ALLOW_LIVE:
        return {'error': '服务以只读模式启动 —— 用 python3 serve.py --live 开启'}
    import shutil
    import subprocess
    on = bool((body or {}).get('on'))
    st = autosync_status()
    if not st.get('supported'):
        return {'error': st.get('note') or '当前平台不支持 launchd'}
    src, dst = _sync_plist_paths()
    if on:
        if not os.path.isfile(src):
            return {'error': '找不到 plist 正本：%s' % src}
        if not st['installed'] or st.get('drift'):
            os.makedirs(_launch_agents_dir(), exist_ok=True)
            shutil.copyfile(src, dst)
    elif not st['installed']:
        return dict(autosync_status(), changed=False,
                    note='本来就没安装，无需关闭')
    cmd = ['launchctl', 'load' if on else 'unload', '-w', dst]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    st2 = autosync_status()
    # ★ 以【复查到的状态】为准，不以命令返回码为准：launchctl 对"已经是这个
    #   状态"会报错退出，而那不是失败。判据永远是"现在到底开着没"。
    if st2.get('on') != on:
        return dict(st2, changed=False,
                    error='%s 失败：%s' % ('开启' if on else '关闭',
                                          (r.stderr or r.stdout or
                                           '返回码 %d' % r.returncode).strip()[-300:]))
    return dict(st2, changed=True, cmd=' '.join(cmd))


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



_LOG_RE = re.compile(r'^[0-9]{8}-[0-9]{6}\.log$')



def api_sync_log(q):
    """GET /api/sync/log?name=... —— ★ 只接受 8位日期-6位时间.log 这个形状，
    且只在 _manifest/sync_logs 下找。文件名来自 URL，不能直接 join。"""
    name = (q.get('name') or '').strip()
    if not _LOG_RE.match(name):
        return {'error': '日志名格式不对：%r' % name}
    p = os.path.join(_datalake_dir(), '_manifest', 'sync_logs', name)
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
    sh = os.path.join(dl, 'sync_daily.sh')
    if not os.path.isfile(sh):
        return {'error': '找不到 %s' % sh}
    cmd = ['bash', sh]
    if (body or {}).get('no_live'):
        cmd.append('--no-live')
    job_id = 'sync-%s' % datetime.now().strftime('%H%M%S')
    _runs._JOBS[job_id] = {'state': 'running', 'lines': [], 'cmd': cmd,
                     'sha': None, 'run_id': None, 'rc': None}
    threading.Thread(target=_runs._run_job, args=(job_id, cmd, dl),
                     daemon=True).start()
    return {'job_id': job_id, 'cmd': ' '.join(cmd)}
