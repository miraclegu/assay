"""srv/live.py —— 实盘账户 / 成交 / 信号 / 费率 / 策略的 HTTP 接口。

★ 业务逻辑在 `assay/live.py`，这里只做 HTTP 出入参。"""
import hashlib
import json
import os
import re
import threading
import time
from datetime import date, datetime

from .. import registry
from . import base
from .base import (HERE, _BOOT_TS, _live, _parse_note, _parse_params, _scan, _staleness, _watch)
from . import rt as _rt  # _rt_catch_up, _rt_ensure
from . import runs as _runs  # _DATE_RE, _JOBS, _run_job


def _live_err(fn, *a, **kw):
    """统一把 LiveError 翻成 {'error': ...} —— 这些是【给用户看的】提示，
    不是 500。其余异常照常冒泡到 Handler 的 500 分支。"""
    m = _live()
    try:
        return fn(*a, **kw)
    except m.LiveError as e:
        return {'error': str(e)}


# ★ 代码指纹：**web/index.html 每次请求都从磁盘读，而 Python 模块只在进程
#   启动时加载一次**。所以长时间开着的 serve.py 会出现「新页面 + 旧 API」——
#   页面去读 API 还没有的字段，渲染出一堆 undefined，而没有任何报错。
#   实测踩到：11:49 启动的服务配 12:53 改的页面，账户设置里全是 undefined。
#   这里把服务端代码的 mtime 暴露出去，页面自己比对并明说"请重启"。

def _code_stamp():
    out = []
    for f in ('server.py', 'live.py'):
        p = os.path.join(HERE, f)
        try:
            out.append(int(os.path.getmtime(p)))
        except OSError:
            out.append(0)
    return {'loaded_at': int(_BOOT_TS), 'code_mtime': max(out)}



def api_live_accounts(_q):
    """GET /api/live/accounts[?all=1] —— 默认隐去已归档的账户。"""
    m = _live()
    show_all = (_q or {}).get('all') in ('1', 'true')
    out = []
    for a in m.load_accounts():
        if a.get('archived') and not show_all:
            continue
        pos = m.positions(a['id'])
        # ★ 红点用【盘上最新那份信号】判，不重算 —— 重算要重放 30 天 warmup，
        #   而这是每次打开实盘页都会跑的列表接口。
        alert, why = m.signal_alert(m.latest_signal(a['id']))
        out.append(dict(a, n_positions=len(pos), cash=round(m.cash(a['id']), 2),
                        n_fills=len(m.fills(a['id'])),
                        n_versions=len(m.versions(a['id'])),
                        alert=alert, alert_why=why))
    cal = m.calendar_meta()
    # ★ 「权不权威」由服务端判 —— 名单只存在 live.AUTHORITATIVE_CAL 一处。
    #   前端原来硬编码 `source !== 'jq.get_all_trade_days'`，于是把
    #   tdx.raw_holidays（同级可信、每次生成都跑对数）误报成不权威，
    #   每次打开实盘页都弹一条假告警。假告警看多了就不看告警了。
    cal = dict(cal, authoritative=(cal.get('source') in m.AUTHORITATIVE_CAL))
    return {'accounts': out, 'readonly': not base.ALLOW_LIVE,
            'code': _code_stamp(),
            # ★ 2026-09-04 起默认全开，只有 --readonly 才关（见 serve.py）。
            #   下面两个变量仍然独立，是为了让 --readonly 一次关掉两边：
            #   --live 管账户/成交/信号，--allow-backtest 管
            #   起子进程跑回测。前端要分别置灰，否则按钮点了才知道被拒。
            'can_backtest': bool(base.ALLOW_BACKTEST and base.ALLOW_LIVE),
            'next_id': m.new_account_id(),
            'calendar': {'source': cal.get('source'), 'max': cal.get('max'),
                         'authoritative': cal.get('authoritative'),
                         'authoritative_until': cal.get('authoritative_until'),
                         'warn': cal.get('warn'), 'error': cal.get('error')}}



def api_live_account(q):
    m = _live()
    aid = (q.get('id') or '').strip()
    # ★ 主视图【不再整包带流水】—— 成交多了之后这个响应会越来越大，
    #   而主视图根本不显示流水（它有独立页面 + 分页）。
    def _go():
        sig = m.latest_signal(aid)
        alert, why = m.signal_alert(sig)
        # ★ 用户明确要的：「如果发现缺失最新的数据则立即调用一次」。
        #   页面来问持仓的时候顺手看一眼实时库落后没有，落后就补抓。
        #   只在交易时段生效（stale_minutes 非时段返回 None），
        #   而且只有 --live 才抓 —— 只读模式不该往外发请求。
        if base.ALLOW_LIVE:
            _rt._rt_catch_up()
            # ★ 今天刚买进来的票不在上一轮的抓取范围里 —— 补一次，
            #   不然它那一行是"收盘价"，而旁边几只是实时的（同一张表里
            #   两个口径混着看，比全是收盘价更容易看错）。
            try:
                _rt._rt_ensure(m.positions(aid))
            except Exception:                               # noqa: BLE001
                pass
        rows = m.fills(aid)
        return {
            'account': m.get_account(aid),
            # ★ 前端不要自己维护一份费率默认值 —— 那是第二份实现，
            #   会和后端 FEE_DEFAULT 漂移（实测：前端漏了 regulatory，
            #   输入框读出 undefined，保存直接被后端拒）。
            'fee_effective': m.fee_model(m.get_account(aid)),
            'fee_default': dict(m.FEE_DEFAULT),
            'fee_history': m.fee_rates(aid),
            # ★ 选项表由服务端给 —— 前端抄过一次费率默认值，抄漏一个键就
            #   读成 undefined、保存被拒而错误藏在浮层里（见 fee_effective）
            'transfer_extra_opts': m.TRANSFER_EXTRA,
            # ★ 行情最新数据日【单独给】—— 判断信号新不新的第一依据，
            #   页面上要有自己的位置，不是塞在括号里当脚注
            'data_day': _live_quiet(m.latest_data_day),
            # 当前配置折成的总费率（按 10 万一笔算；最低佣金 binding 时会更高）
            'fee_rates': m.effective_rates(m.fee_model(m.get_account(aid))),
            'pos': m.positions_valued(aid),
            'cash': round(m.cash(aid), 2),
            'versions': m.versions(aid),
            'cashflows': list(reversed(m.cashflows(aid))),
            'n_fills': len(rows),
            'fee_total': round(sum(float(f.get('fee') or 0) for f in rows), 2),
            'fee_estimated_n': sum(1 for f in rows if f.get('fee_estimated')),
            'signal': sig,
            # ★ 待办要不要默认展开、账户列表的红点，同一个判据
            #   （live.signal_alert）—— 判据分两处写就一定会分叉
            'alert': alert, 'alert_why': why,
        }
    return _live_err(_go)



def _live_quiet(fn, *a):
    """取不到就返回 None —— 这类"锦上添花"的字段不该拖垮整个账户页。"""
    try:
        return fn(*a)
    except Exception:                                       # noqa: BLE001
        return None



def api_live_fills(q):
    """GET /api/live/fills?id=&offset=&limit= —— 成交流水，倒序分页。

    ★ 分页在服务端做。成交攒到几千笔时整包发过去，前端渲染几千个 DOM
      会明显卡 —— 归档的持仓表当初就是这么踩过的（见 api_holdings）。
    """
    m = _live()
    aid = (q.get('id') or '').strip()

    def _go():
        rows = list(reversed(m.fills(aid)))
        total = len(rows)
        try:
            off = max(0, int(q.get('offset') or 0))
        except Exception:                                   # noqa: BLE001
            off = 0
        try:
            lim = int(q.get('limit') or 50)
        except Exception:                                   # noqa: BLE001
            lim = 50
        lim = max(1, min(500, lim))
        if off >= total:
            off = max(0, (total - 1) // lim * lim)          # 越界收敛到最后一页
        # 已被冲正的原记录：前端要划掉它，服务端顺手标出来，
        # 免得前端为了判断这个把全量流水都拉一遍
        reved = {f.get('reverse_of') for f in m.fills(aid) if f.get('reverse_of')}
        page = []
        # ★ 名称在【服务端】补：批量粘贴的成交只有代码。前端补不了 ——
        #   它没有面板。留空的话流水页只剩代码，得对着代码猜是哪只票。
        pg_rows = rows[off:off + lim]
        try:
            nm = m.names_of([f['code'] for f in pg_rows])
        except Exception:                                   # noqa: BLE001
            nm = {}
        for f in pg_rows:
            page.append(dict(f, _dead=((f.get('uid') or f['ts']) in reved),
                             name=(f.get('name') or nm.get(f['code'], ''))))
        return {'total': total, 'offset': off, 'limit': lim, 'rows': page,
                'fee_total': round(sum(float(f.get('fee') or 0)
                                       for f in rows), 2),
                'fee_estimated_n': sum(1 for f in rows if f.get('fee_estimated'))}
    return _live_err(_go)



def api_live_equity(q):
    """GET /api/live/equity?id= —— 逐日权益曲线 + 时间加权收益。"""
    m = _live()
    aid = (q.get('id') or '').strip()
    return _live_err(lambda: m.equity_curve(aid))



def api_live_signal(q):
    m = _live()
    aid = (q.get('id') or '').strip()
    d = (q.get('date') or '').strip()
    return _live_err(lambda: (m.load_signal(aid, d) if d else m.latest_signal(aid))
                     or {'error': '还没有信号 —— 点「立即重算」'})



def api_live_code(q):
    m = _live()
    aid = (q.get('id') or '').strip()
    sha = (q.get('sha') or '').strip().lower()
    which = (q.get('file') or '').strip() or None

    def _go():
        code, full, names = m.version_code(aid, sha, which)
        return {'code': code, 'code_sha256': full, 'files': names}
    return _live_err(_go)



def api_live_strategy(q):
    """GET /api/live/strategy?id=&sha= —— 账户绑的这个版本：
    源码快照 + 参数表 + **用同一版本跑过的历史回测**。

    ★ 关联的键是主文件【自身】哈希，不是账户的打包哈希 ——
      账户的 code_sha256 覆盖「主文件 + 依赖」（否则改 froec.py 不会改变
      账户版本号 = 版本悄悄漂移），而归档的 meta.code_sha256 是单文件哈希。
      两者口径不同，直接比会永远匹配不上。
    """
    m = _live()
    aid = (q.get('id') or '').strip()
    sha = (q.get('sha') or '').strip().lower()

    def _go():
        v = m._version_row(aid, sha)
        code, full, names = m.version_code(aid, v['code_sha256'])
        main_sha = m._main_sha(aid, v)
        out = {'version': v, 'code': code, 'files': names,
               'main_sha256': main_sha,
               'params_declared': _parse_params(code),
               'note': _parse_note(code)[0]}
        # 归档里同一主文件版本跑过的回测
        same, other = [], []
        for rid, d in _scan().items():
            try:
                meta = json.load(open(os.path.join(d, 'meta.json'), encoding='utf-8'))
            except Exception:                               # noqa: BLE001
                continue
            if not main_sha or meta.get('code_sha256') != main_sha:
                continue
            try:
                stats = json.load(open(os.path.join(d, 'stats.json'), encoding='utf-8'))
            except Exception:                               # noqa: BLE001
                stats = {}
            row = {'run_id': rid, 'params': meta.get('params') or {},
                   'start': meta.get('start'), 'end': meta.get('end'),
                   'cash': meta.get('cash'),
                   'annual': stats.get('annual'), 'total': stats.get('total_return'),
                   'max_drawdown': stats.get('max_drawdown'),
                   'sharpe': stats.get('sharpe'),
                   'stale': _staleness(meta)[0]}
            (same if row['params'] == (v.get('params') or {}) else other).append(row)
        key = lambda r: (r['start'] or '', r['end'] or '')
        out['runs_same_params'] = sorted(same, key=key, reverse=True)
        out['runs_other_params'] = sorted(other, key=key, reverse=True)
        return out
    return _live_err(_go)



def api_live_backtest(_q, body):
    """POST /api/live/backtest —— 用账户绑的【那个版本 + 那组参数】跑一次回测。

    ★ 只有磁盘文件仍等于绑定版本时才允许。否则跑出来的是**另一个版本**，
      而它会被归档成「这个版本回测过」—— 与 api_version 里那条同样的陷阱：
      「文件改过之后，那个版本就是历史快照，拿现在的文件去跑得到的是另一个
      版本，静默照跑会让归档里出现一条挂错版本的记录」。
    """
    bad = _live_guard()
    if bad:
        return bad
    if not base.ALLOW_BACKTEST:
        return {'error': '触发回测未开启 —— 加 --allow-backtest'}
    m = _live()
    b = body or {}
    aid = (b.get('id') or '').strip()
    try:
        acct = m.get_account(aid)
        v = m._version_row(aid, (b.get('sha') or acct.get('code_sha256') or ''))
    except m.LiveError as e:
        return {'error': str(e)}
    rel = v.get('strategy_path') or ''
    disk = os.path.join(registry.ROOT, rel)
    if not os.path.isfile(disk):
        return {'error': '策略文件已不在原路径：%s' % rel}
    want = m._main_sha(aid, v)
    cur = hashlib.sha256(open(disk, 'rb').read()).hexdigest()
    if want and cur != want:
        # ★ 除了说"不行"，还要给出【可操作的下一步】—— 前端据此渲染一个
        #   「重新绑定」按钮和一条可复制的命令行（`drift` 这个字段就是判据）。
        #   只抛一句话的话，人看到的是"点了报错"，仍然不知道该干什么。
        return {'error': '磁盘上的 %s 已改动（当前 %s ≠ 绑定版本 %s）。'
                         '现在跑会归档成【另一个版本】，看起来像"这个版本回测过"。'
                         % (rel, cur[:8], want[:8]),
                'drift': {'path': rel, 'disk_sha': cur[:8], 'bound_sha': want[:8],
                          'params': v.get('params') or {}}}
    cmd = ['python3', 'run.py', rel]
    for k, val in (v.get('params') or {}).items():
        cmd += ['--param', '%s=%s' % (k, val)]
    for key, flag in (('start', '--start'), ('end', '--end')):
        sv = str(b.get(key) or '').strip()
        if sv:
            if not _runs._DATE_RE.match(sv):
                return {'error': '%s 应为 YYYY-MM-DD，收到 %r' % (key, sv)}
            cmd += [flag, sv]
    csh = str(b.get('cash') or '').strip()
    if csh:
        try:
            c = float(csh)
            assert c > 0
        except Exception:                                   # noqa: BLE001
            return {'error': '本金必须是正数'}
        cmd += ['--cash', repr(c)]
    job_id = 'lvbt-%s' % datetime.now().strftime('%H%M%S')
    _JOBS[job_id] = {'state': 'running', 'lines': [], 'cmd': cmd,
                     'sha': want, 'run_id': None, 'rc': None}
    threading.Thread(target=_runs._run_job, args=(job_id, cmd, registry.ROOT),
                     daemon=True).start()
    return {'job_id': job_id, 'cmd': ' '.join(cmd)}



def api_live_fee_add(_q, body):
    """POST /api/live/fee_rate —— **新增**一档费率（append-only）。

    ★ 刻意【没有】改已有费率的接口。改了会让已经按它算过的成交无从解释；
      想改就新增一档更晚生效的把它盖掉，历史仍然留着、看得见改过。
    """
    bad = _live_guard()
    if bad:
        return bad
    m = _live()
    b = body or {}
    aid = (b.get('id') or '').strip()

    def _go():
        m.migrate_fee(aid)          # 老数据先落成第一档，再追加
        r = m.add_fee_rate(aid, b.get('from'), b.get('fee') or {},
                           note=b.get('note') or '',
                           supersede=bool(b.get('supersede')))
        return {'added': r, 'history': m.fee_rates(aid)}
    return _live_err(_go)



def api_live_fee_infer(_q, body):
    """POST /api/live/fee_infer —— 从一笔真实成交的账单反推费率。

    ★ 让用户【照账单填】而不是自己拆算：券商 App 显示的"佣金费率"通常是
      **含规费**的口径（实测红利账户万0.8 = 净佣金万0.26 + 规费万0.54），
      自己拆开去配会少收规费那部分，且不报错。
    """
    m = _live()
    b = body or {}

    def _go():
        mod = m.infer_fee_model(
            b.get('amount'), b.get('commission') or 0,
            regulatory=b.get('regulatory') or 0,
            transfer=b.get('transfer') or 0,
            stamp=b.get('stamp') or 0,
            min_commission=b.get('min_commission') or 0)
        amt = float(b.get('amount'))
        # 自证：用反推出的费率复算这一笔，应与账单合计一致
        n = int(b.get('shares') or 0) or 100
        full = dict(m.FEE_DEFAULT)
        full.update(mod)
        # ★ 复算要带【代码】：过户费是否另收分市场。账单是哪只票就用哪只，
        #   没给就按沪市样板（另收，保守方向）。
        code = (b.get('code') or '600000.XSHG').strip().upper()
        bd = m.fee_breakdown('buy', n, amt / n,
                             b.get('date') or '2026-01-01', full, code)
        want = round(float(b.get('commission') or 0)
                     + float(b.get('regulatory') or 0)
                     + float(b.get('transfer') or 0), 2)
        # ★ 最低佣金是否含规费，**这一笔定不出来** —— 要一笔小额成交
        #   （金额 < 2 万，最低会 binding）。不提醒的话用户会以为全配好了。
        floor_binds = (amt * float(mod['commission'])
                       < float(mod['min_commission'] or 0))
        return {'fee': mod, 'recompute': bd['total'], 'breakdown': bd,
                'bill_total': want, 'match': abs(bd['total'] - want) <= 0.02,
                'incl_reg': mod['commission_incl_reg'], 'code': code,
                'market': m.market_of(code),
                'rates': m.effective_rates(full, amt),
                'need_small_bill': bool(mod['min_commission']) and not floor_binds}
    return _live_err(_go)



def api_live_cash(_q, body):
    """POST /api/live/cash —— 入金 / 出金 / 分红到账 / 手工调整。append-only。"""
    bad = _live_guard()
    if bad:
        return bad
    m = _live()
    b = body or {}
    aid = (b.get('id') or '').strip()

    def _go():
        m.add_cashflow(aid, b.get('date'), b.get('amount'),
                       kind=(b.get('kind') or 'deposit'), note=b.get('note') or '')
        return {'cash': round(m.cash(aid), 2), 'cashflows': list(reversed(m.cashflows(aid)))}
    return _live_err(_go)



def _live_guard():
    if not base.ALLOW_LIVE:
        return {'error': '服务以只读模式启动，实盘模块已关闭。'
                         '用 python3 serve.py --live 开启。'}
    return None



def api_live_save(_q, body):
    """建/改账户；带 strategy_path 时顺便绑版本（append-only 留痕）。"""
    bad = _live_guard()
    if bad:
        return bad
    m = _live()
    b = body or {}
    aid = (b.get('id') or '').strip() or m.new_account_id()

    def _go():
        m.upsert_account(aid, name=b.get('name'), init_cash=b.get('init_cash'),
                         broker_note=b.get('broker_note'),
                         tick_time=b.get('tick_time'),
                         warmup_start=b.get('warmup_start'),
                         fee=b.get('fee'))
        if b.get('strategy_path'):
            m.bind_version(aid, b['strategy_path'], b.get('params') or {},
                           b.get('reason') or '')
        if 'archived' in b:
            m.archive_account(aid, bool(b['archived']))
        return {'account': m.get_account(aid), 'versions': m.versions(aid)}
    return _live_err(_go)



def api_live_fill(_q, body):
    bad = _live_guard()
    if bad:
        return bad
    m = _live()
    b = body or {}
    aid = (b.get('id') or '').strip()
    rows = b.get('rows')
    if rows is None:
        rows = [b]
    if not isinstance(rows, list):
        return {'error': 'rows 必须是数组'}
    if len(rows) > 200:
        return {'error': '一次最多 200 笔'}
    ok, errs = [], []
    for i, r in enumerate(rows):
        try:
            # ★ price / fee 都【原样透传】（含 None/''）—— 不要 `or 0`。
            #   `r.get('fee') or 0` 会把"没填"变成"明确说 0 元"；
            #   price 同理，留空的语义是"按当日开盘价取"，不是 0。
            ok.append(m.add_fill(
                aid, r.get('trade_date'), (r.get('code') or '').strip().upper(),
                (r.get('side') or '').strip(), r.get('shares'),
                price=r.get('price'), fee=r.get('fee'),
                name=r.get('name') or '',
                source=r.get('source') or 'manual', note=r.get('note') or '',
                reverse_of=r.get('reverse_of'),
                fee_estimated=bool(r.get('fee_estimated')),
                price_from=r.get('price_from'),
                force_price=bool(r.get('force_price') or b.get('force_price'))))
        except m.LiveError as e:
            errs.append('第 %d 行：%s' % (i + 1, e))
    return {'added': len(ok), 'errors': errs,
            'positions': m.positions(aid), 'cash': round(m.cash(aid), 2)}



def api_live_tick(_q, body):
    bad = _live_guard()
    if bad:
        return bad
    m = _live()
    b = body or {}
    aid = (b.get('id') or '').strip()
    if aid:
        return _live_err(lambda: m.make_signal(aid, force=True))
    return {'results': m.tick(force=True)}



def _live_loop():
    """守护线程：每 60s 看一次表。★ 幂等由 live.tick 保证（信号按
    for_date 落盘，已存在就跳过），所以轮询频率高不会重复算。

    ★ 顺手把持仓同步进自选（`watchlist.sync_live` 也是幂等的）——
      挂在这里而不是单独起一个线程：它依赖的就是持仓，而持仓变化只发生在
      录入成交之后，跟着这条线走足够及时。
    """
    import time
    m = _live()
    while True:
        try:
            try:
                r = _watch().sync_live()
                if r['added'] or r['regrouped'] or r['cleared']:
                    print('[watch] 自选同步：新加 %d 改组 %d 已清仓 %d'
                          % (len(r['added']), len(r['regrouped']),
                             len(r['cleared'])), flush=True)
            except Exception as e:                          # noqa: BLE001
                print('[watch] 同步异常: %s: %s' % (type(e).__name__, e), flush=True)
            for r in m.tick():
                if r.get('error'):
                    print('[live] %s: %s' % (r.get('account'), r['error']), flush=True)
                else:
                    print('[live] %s -> %s  卖%d 买%d'
                          % (r['account'], r['for_date'],
                             len(r.get('sell') or []), len(r.get('buy') or [])),
                          flush=True)
        except Exception as e:                              # noqa: BLE001
            print('[live] tick 异常: %s: %s' % (type(e).__name__, e), flush=True)
        time.sleep(60)


# ==================== 盘中 1 分钟线 ====================
# ★ 只有 server 开着才跑（用户明确要的）。这件事**漏了不要紧** ——
#   trends2 每次给全天、快照下一分钟就补上，而实时盈亏只在看页面时才有意义。
#   （对比 daily_snapshot.py 那种"漏一天永久丢失"的，才必须挂 launchd。）
