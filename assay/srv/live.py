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
from . import base as _base
from .base import (HERE, _live, _parse_note, _parse_params, _scan, _staleness, _watch)
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
    """进程启动时间 vs **整个包**的最新代码 mtime。

    🔴 原来只 stat `server.py` 与 `live.py` 两个文件 —— 而实现早就拆进了
      `srv/` 与 `lv/`（server.py 只剩 280 行骨架、live.py 只剩 66 行门面）。
      于是改任何一个域文件都**不会**触发"请重启 serve.py"的横幅，
      而那正是这个横幅存在的全部理由（新页面 + 旧 API = 满屏 undefined
      且不报错）。判据要跟着代码一起搬，不然它只是看着还在。
    """
    out = []
    for r, _d, fs in os.walk(HERE):
        if '__pycache__' in r:
            continue
        for f in fs:
            if f.endswith('.py'):
                try:
                    out.append(int(os.path.getmtime(os.path.join(r, f))))
                except OSError:
                    pass
    if not out:
        out = [0]
    # 🔴 读 `_base._BOOT_TS` 而不是 import 进来的副本。
    #   `from .base import _BOOT_TS` 抄的是**那一刻的值**，之后谁改 base 的
    #   都不会跟着变 —— selftest 正是靠「把 _BOOT_TS 调早」来模拟"进程比代码旧"，
    #   而那个模拟一直是**空转的**（断言够不着，因为前面先挂在 _JOBS 上）。
    #   同 CLAUDE.md 里门面那条：re-export 拿到的是副本，转发才是引用。
    return {'loaded_at': int(_base._BOOT_TS), 'code_mtime': max(out)}



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
        # 🔴 **「是不是模拟盘」由服务端判**（`lv.is_paper` 是唯一定义）——
        #   前端原来各处写 `a.mode === 'paper'`，已经有两处了，再加侧栏分组
        #   就是第三处；而老账户的 `mode` 字段**根本不存在**（早于那次改动），
        #   靠前端比字符串就得记住"undefined 也算实盘"这条隐含规则。
        #   同「权不权威这类判据由服务端给」那条。
        out.append(dict(a, n_positions=len(pos), cash=round(m.cash(a['id']), 2),
                        n_fills=len(m.fills(a['id'])),
                        n_versions=len(m.versions(a['id'])),
                        paper=m.is_paper(a),
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



def _rt_live():
    """现在是不是"盘中"（交易日 + 交易时段）—— 页面据此决定要不要轮询。

    ★ 取 assay/realtime.py 的 in_session / is_trading_day，**不另写判据**。
      拿不到（比如实时模块没起）就返回 None -> 页面按"不确定"处理：
      仍然轮一次但不持续，宁可少轮也不要谎报"盘中"。
    """
    try:
        rt = base._rt()
        return bool(rt.in_session() and rt.is_trading_day())
    except Exception:                                       # noqa: BLE001
        return None


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
        _acct = m.get_account(aid)
        # ★ 模拟盘的推进状态跟着账户一起给 —— 页面要显示"推进到哪天了"、
        #   以及对账差了多少。非模拟盘不带这个字段（页面按有无判断）。
        _paper = m.state(aid) if m.is_paper(_acct) else None
        # 🔴🔴 **旧状态里没有 `why_empty`，页面就又是一片空白** ——
        #   那正是用户报的症状（「也推进了，但是没有任何数据出现」），
        #   只是这次的成因是**状态文件早于这个功能**：`rejects`/`why_empty`
        #   落在 `_paper.json` 里，上一次推进时还没有这两个字段。
        #   ★ **不猜原因**（本金太小 / 候选池空 / 没有调仓日会表现成同一个
        #     "0 笔"，而要做的事完全不同）—— 只说清"这份状态没记原因"
        #     并给下一步。同「删了要留痕，否则『空』与『本来就没有』
        #     分不出来」。
        #   ★ 它是**派生的显示兜底**，所以在这里拼、不写回状态文件
        #     （`state()` 读出来的东西会被下一次 `_save_state` 落盘）。
        if (_paper and _paper.get('advanced_to')
                and not _paper.get('n_fills')
                and not _paper.get('why_empty')):
            _paper = dict(_paper, why_empty=(
                '这份推进状态是旧版本留下的，没有记下原因（引擎的拒单原因'
                '是后来才带出来的）。点上面的「▷ 推进」重算一次就会给出。'),
                stale_state=True)
        # 同列表接口那条：`paper` 这个布尔由服务端给，页面不比字符串
        _acct = dict(_acct or {}, paper=m.is_paper(_acct))
        return {
            'account': _acct,
            'paper': _paper,
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
            # ★ 现在是不是盘中 —— **页面靠它决定要不要继续轮询**。
            #   🔴 判据必须由服务端给：前端硬编码交易时段的话，
            #     改了时段（或遇到半日市）就会静默轮空/白轮，
            #     而"多轮几次"不报错、"该轮没轮"更不报错。
            #     （同"权不权威由服务端给"那条：前端硬编码过一次
            #      `source !== 'jq.get_all_trade_days'`，弹了一整页假告警。）
            #   ★ 与 /api/rt/status 的 in_session / trading_day 同一处实现
            #     （realtime.in_session / is_trading_day），不另写。
            'rt_live': _rt_live(),
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



def api_live_intraday(_q):
    """GET /api/live/intraday —— 盘中炸板扫描（**全部**非归档账户）。

    ★ 不带 `id`：这个提示要在**任何页面**都能看到（浮窗挂在 common.js 里），
      而那时页面不知道"当前是哪个账户" —— 所以一次扫完所有账户。
    ★ `fire()` 负责去重（同一只票一天只报一次，落盘）；返回里
      `fresh` 是本次新增的、`items` 是当前全部状态（含还封着的）——
      "一条都没有"要能分出"没有涨停持仓"和"链条没工作"。
    """
    m = _live()
    from ..lv import intraday as _in
    out = {'accounts': [], 'fresh': [], 'session': None, 'scan_from': None}
    try:
        out['scan_from'] = _in.SCAN_FROM.strftime('%H:%M')
    except Exception:                                       # noqa: BLE001
        pass
    for a in m.load_accounts():
        if a.get('archived'):
            continue
        try:
            r = _in.scan(a['id'])
        except Exception as e:                              # noqa: BLE001
            out['accounts'].append({'id': a['id'], 'name': a.get('name'),
                                    'error': '%s: %s' % (type(e).__name__, e)})
            continue
        if out['session'] is None:
            out['session'] = r.get('session')
        fresh = _in.fire(a['id'], r.get('items') or []) if r.get('items') else []
        for x in fresh:
            x = dict(x)
            x['account'] = a['id']
            x['account_name'] = a.get('name')
            out['fresh'].append(x)
        out['accounts'].append({'id': a['id'], 'name': a.get('name'),
                                'skipped': r.get('skipped'),
                                'at': r.get('at'),
                                'items': r.get('items') or []})
    return out


def api_live_equity(q):
    """GET /api/live/equity?id=&bench=sh000001,sh000905
    —— 逐日权益曲线 + 时间加权收益（+ 可选的基准指数,对齐到同一日期轴）。

    ★ 基准跟权益曲线**同一次请求**给：日期轴天然对齐，前端不用自己拼；
      另开一个接口的话两边取到的交易日可能差一天，而那种错不报错。
    ★ 可选清单（`benchmarks`）总是带上 —— 前端不该硬编码有哪些指数
      可选（本地缺哪个只有服务端知道，见 lv/perf.py 的 BENCHMARKS）。
    """
    m = _live()
    aid = (q.get('id') or '').strip()
    codes = [x.strip() for x in (q.get('bench') or '').split(',') if x.strip()]

    def _go():
        out = m.equity_curve(aid)
        from ..lv import perf as _perf
        out['benchmarks'] = _perf.BENCHMARKS
        if codes and out.get('dates'):
            out['bench'] = _perf.bench_curves(out['dates'], codes)
            # ★ 自定义基准的**名字由服务端给**：页面只存一个 symbol
            #   （一个 localStorage 槽），名字存在前端的话，改过名的标的
            #   会一直显示旧名，而它不报错。
            out['bench_meta'] = _perf.bench_meta(codes)
        return out
    return _live_err(_go)



def api_live_signal(q):
    m = _live()
    aid = (q.get('id') or '').strip()
    d = (q.get('date') or '').strip()
    return _live_err(lambda: (m.load_signal(aid, d) if d else m.latest_signal(aid))
                     or {'error': '还没有信号 —— 点「立即重算」'})



# ★ 后台事后复算：历史那几期（早于这个功能）没有留下选股理由，要重放 30 天
#   warmup 才能补出来，约 1~3 秒 —— 太慢，不能在请求里同步跑。
#   🔴 但也**不能只给一个按钮**：实测用户打开页面看到的就是"没有理由"，
#     而"要点一下才有"这件事页面说了他也不会当回事（实测反馈：
#     「我直接看不到上一期选股」）。所以**打开就自动开算**，页面显示
#     「正在复算…」并轮询；算完落盘（`signals/_explain/`），以后直接就有。
_EXP_JOBS = {}                       # (aid, date) -> 'running' | 'done' | 'err:...'
_EXP_LOCK = threading.Lock()


def _explain_kick(aid, day):
    """开一个后台复算（同一期同时只会有一个）。返回当前状态。"""
    key = (aid, day)
    with _EXP_LOCK:
        st = _EXP_JOBS.get(key)
        if st == 'running':
            return st
        _EXP_JOBS[key] = 'running'

    def _run():
        try:
            _live().explain_recompute(aid, day)
            _EXP_JOBS[key] = 'done'
        except Exception as e:                              # noqa: BLE001
            # 失败必须留下原因：这一格在页面上是"正在复算…"，
            # 不写状态的话它会一直转，而**没有任何报错**
            _EXP_JOBS[key] = 'err:%s' % e
    threading.Thread(target=_run, daemon=True).start()
    return 'running'


def _explain_attach(aid, r, sg):
    """给一期补上选股理由：信号里有就用信号里的，没有就用旁挂的复算结果；
    再没有、且是调仓日，就**开一个后台复算**并标 `explain_pending`。"""
    e = sg.get('explain') or {}
    if e.get('captured'):
        return dict(r, explain=e, explain_from='signal')
    m = _live()
    side = m.load_explain_side(aid, r['date'])
    if side:
        return dict(r, explain=side.get('explain') or {}, explain_from='recomputed',
                    verified=side.get('verified'), metrics_from=side.get('metrics_from'),
                    diff=side.get('diff') or {}, computed_at=side.get('computed_at'))
    if not r.get('is_rebalance'):
        return dict(r, explain={}, explain_from=None)
    st = _explain_kick(aid, r['date'])
    return dict(r, explain={}, explain_from=None,
                explain_pending=(st == 'running'),
                explain_err=(st[4:] if str(st).startswith('err:') else None))


def api_live_explain(q):
    """GET /api/live/explain?id=&date=&recompute=0|1 —— 某一期的选股理由。

    不带 `date` 就给最近一期。`recompute=1` 时**事后复算**那一期
    （老信号里没有理由 —— 这个功能是后加的），复算结果带 `verified`：
    `same` = 复算出的买/卖/持有与当时存的逐个相同，说明面板没被动过、
    这就是当时那份；`differs` = 不是当时那份，页面必须照实说。

    ★ 复算要重放 30 天 warmup，约 1~3 秒，所以**只在人点了才算**，
      不在账户接口里顺手带出来。
    """
    m = _live()
    aid = (q.get('id') or '').strip()
    d = (q.get('date') or '').strip()

    def _go():
        sg = (m.load_signal(aid, d) if d else m.latest_signal(aid)) or {}
        if not sg.get('for_date'):
            return {'error': '没有这一期的信号'}
        e = sg.get('explain') or {}
        force = q.get('recompute') in ('1', 'true')
        if not e.get('captured') and not force:
            # 旁挂的复算结果（`signals/_explain/`）算数 —— 不然重启一次
            # serve.py 就又要人点一遍
            side = m.load_explain_side(aid, sg['for_date'])
            if side:
                return dict(side, is_rebalance=bool(sg.get('is_rebalance_day')))
        if e.get('captured') or not force:
            return {'date': sg['for_date'], 'data_asof': sg.get('data_asof'),
                    'code_sha': sg.get('code_sha'), 'params': sg.get('params') or {},
                    'is_rebalance': bool(sg.get('is_rebalance_day')),
                    'recomputed': bool(sg.get('recomputed')),
                    'explain': e, 'buy': sg.get('buy') or [],
                    'sell': sg.get('sell') or [], 'hold': sg.get('hold') or []}
        r = m.explain_recompute(aid, sg['for_date'])
        r['is_rebalance'] = bool(sg.get('is_rebalance_day'))
        return r
    return _live_err(_go)



def api_live_explains(q):
    """GET /api/live/explains?id=&offset=&limit=&full=1 —— 期数清单（可带内容）。

    - 不带 `full`：只给清单（日期 / 调仓日 / 有没有理由 / 买卖几只），
      账户页拿它标持仓的"出处"，很便宜。
    - `full=1`：连**这一页那几期的完整理由**一起给 —— 选股理由是**独立页**，
      一期一段列出来（同成交流水那条：会越来越长，所以**服务端分页**）。

    🔴 一次只给一页。红利一期的候选池有几百只，全给会让这个接口越用越慢，
      而"慢"在本地是最容易被忽略的坏。
    """
    m = _live()
    aid = (q.get('id') or '').strip()

    def _go():
        rows = m.explain_history(aid)
        rows.reverse()                       # 最近的在最上面
        n_all = len(rows)
        # 🔴 默认**只列调仓日**。非调仓日本来就不选股，列出来是三段
        #   "本期不选股"的空段 —— 会把真正要看的那期推到第二页去
        #   （实测：froec 最近三期全是非调仓日，第一页什么都没有）。
        if (q.get('only') or 'rebal') == 'rebal':
            rows = [r for r in rows if r['is_rebalance']]
        out = {'total': len(rows), 'total_all': n_all,
               'only': (q.get('only') or 'rebal'),
               'entry': m.entry_signals(aid)}
        if q.get('full') not in ('1', 'true'):
            out['periods'] = rows
            out['offset'], out['limit'] = 0, len(rows)
            return out
        try:
            off = max(0, int(q.get('offset') or 0))
        except ValueError:
            off = 0
        try:
            lim = min(20, max(1, int(q.get('limit') or 3)))
        except ValueError:
            lim = 3
        page = []
        for r in rows[off:off + lim]:
            sg = m.load_signal(aid, r['date']) or {}
            page.append(dict(_explain_attach(aid, r, sg),
                             buy=sg.get('buy') or [], sell=sg.get('sell') or [],
                             hold=sg.get('hold') or [],
                             params=sg.get('params') or {}))
        out['periods'], out['offset'], out['limit'] = page, off, lim
        return out
    return _live_err(_go)



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



def api_live_strategies(q):
    """GET /api/live/strategies?current= —— 可以绑的策略清单。

    🔴 **清单由服务端给**（同「可选清单由服务端给」那条）：本地有哪几个
      策略、哪个是共享层、哪个文件已经不在了，**只有服务端知道**。
      前端硬编码一份的话，加一个策略它不会出现（而那不报错，
      只是从页面上绑不到），删一个则会列出一个绑上去就崩的死选项。
    ★ 未绑定的账户也要打这个接口 —— 那正是最需要选择器的场景，
      而 `/api/live/strategy` 在未绑定时前端根本不发请求。
    """
    m = _live()
    return _live_err(lambda: m.list_strategies(q.get('current') or ''))


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
    _runs._JOBS[job_id] = {'state': 'running', 'lines': [], 'cmd': cmd,
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



def api_live_holdings(q):
    """GET /api/live/holdings?id=&offset=&limit= —— 【每日】持仓快照。

    ★ 与实盘主视图那个「当前持仓」是两件事：那个回答"我现在拿着什么"，
      这个回答"那天我拿着什么" —— 回测详情页早就有后者（holdings.parquet），
      实盘一直没有（用户 2026-09-15 指出）。
    """
    m = _live()
    aid = (q.get('id') or '').strip()
    return _live_err(lambda: m.daily_holdings(
        aid, offset=q.get('offset') or 0, limit=q.get('limit') or 100))


def api_live_trips(q):
    """GET /api/live/trips?id=&offset=&limit= —— 交易记录（FIFO 往返）。

    ★ 与「成交流水」是两件事：流水是**录入视角**（那天买了/卖了什么），
      这个是**往返视角**（这一笔赚了多少、持有多久）。
    """
    m = _live()
    aid = (q.get('id') or '').strip()
    return _live_err(lambda: m.round_trips(
        aid, offset=q.get('offset') or 0, limit=q.get('limit') or 100))


def api_live_paper(_q, body):
    """模拟盘：推进到最新数据日 / 删档重建。

    🔴 `reset` 是**唯一会删账本行的入口**，所以要显式传 `confirm` ——
      它删掉的是引擎跑出来的那些成交（模拟盘本来就是可重来的推演），
      但手滑点一下就没了，而账本里那几十笔是看过的东西。
    ★ 实盘账户调它会被 `lv/paper.py` 直接拒 —— 判据在那边一处。
    """
    bad = _live_guard()
    if bad:
        return bad
    m = _live()
    b = body or {}
    aid = (b.get('id') or '').strip()
    act = (b.get('act') or 'advance').strip()

    def _go():
        if act == 'reset':
            if not b.get('confirm'):
                return {'error': '重建会删掉模拟盘已有的成交，请确认'}
            r = m.reset(aid)
            r.update(m.advance(aid))
            return r
        return m.advance(aid, rebuild=bool(b.get('rebuild')))
    return _live_err(_go)


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
                         fee=b.get('fee'), mode=b.get('mode'),
                         paper_start=b.get('paper_start'))
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

def api_live_trades_of(q):
    """GET /api/live/trades_of?code= —— 某只票在【全部账户】的成交。

    给个股浮层画买卖点用。★ 不带 `id`：浮层会在盘面/自选/买点页上打开，
      那时"当前是哪个账户"不存在，而"我在这只票上买卖过没有"正是那时
      最想知道的事。逐笔标出自哪个账户。
    🔴 **浮层固定用不复权 K 线**，所以这里直接给成交价、不做复权换算：
      成交价是不复权实际价，切到后复权就会把标记画到错误价位上，
      **而它不报错**，只是看着像"买在了那根阴线上面"。要看后复权的
      去完整个股页（浮层里有出口）。
    """
    code = q.get('code') or ''
    if not code:
        return {'error': '缺 code'}
    m = _live()
    try:
        return {'code': m.normalize_code(code), 'trades': m.trades_of(code)}
    except Exception as e:                                  # noqa: BLE001
        return {'error': '%s: %s' % (type(e).__name__, e)}

def api_live_bench_search(q):
    """GET /api/live/bench_search?q= —— 手填基准时按**名称或代码**找。

    🔴 只返回**真有日线的**：列出来点了什么都不出来，比不给这个选项更糟
      （同 backLink 那条）。范围是指数 / ETF / 股票 —— 都是"能拿来比"的
      东西；通达信板块不进候选（它不是能买到的标的）。
    """
    from ..lv import perf as _p
    return {'items': _p.bench_search(q.get('q') or '', limit=12)}


def api_live_bench(q):
    """GET /api/live/bench?id=[&force=1] —— 绑定策略的理论曲线。

    实盘的实际操作与绑定策略**必然有差异**（漏单、价格不同、手工加减），
    所以业绩页要两条曲线：实际（TWR）与策略（完全照做）。
    ★ 跑一次回测要 0.5~几秒，所以三层缓存（进程内 -> 旁挂 -> 真跑），
      缓存键含 `data_fingerprint`（数据修正过必须重算）。
    """
    aid = q.get('id') or ''
    if not aid:
        return {'error': '缺 id'}
    from ..lv import bench as _b
    try:
        return _b.compute(aid, force=(q.get('force') == '1'))
    except Exception as e:                                  # noqa: BLE001
        return {'error': '%s: %s' % (type(e).__name__, e)}


def api_live_exec_diff(q):
    """GET /api/live/exec_diff?id=[&date=] —— 每期「策略说什么 vs 实际做了什么」。

    🔴 **调仓提示本来就以实际持仓为准**（`lv/sig.py` 的 `_seed` 用
      `fifo_lots(真实成交流水)` 播种），所以这里比的是**执行**：
      提示的 10 只买了几只、股数与价格差多少。
    🔴 股数**按本金归一化后再比** —— 实测 froec 09-01 那份信号是按 100 万
      算的（账户后来改成 40 万），不归一化会把 10 只里 9 只判成"买少了"，
      而那根本不是执行差异。
    """
    aid = q.get('id') or ''
    if not aid:
        return {'error': '缺 id'}
    from ..lv import bench as _b
    d = q.get('date')
    try:
        if d:
            return _b.diff_one(aid, d)
        return {'items': _b.diff_history(aid, limit=int(q.get('limit') or 60))}
    except Exception as e:                                  # noqa: BLE001
        return {'error': '%s: %s' % (type(e).__name__, e)}
