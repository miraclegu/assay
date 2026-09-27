"""srv/watch.py —— 自选 与 买点清单（股息率到价提醒）。"""
import re
from datetime import datetime

from . import base
from .base import (_alerts, _watch)
from . import rt as _rt  # _rt_ensure


def api_alerts(_q):
    """GET /api/alerts —— 股息率买点清单 + 现价 + 每档状态。

    ★ 顺手补抓没有实时数据的那几只（同自选）—— 这张表的全部意义是
      "价格到了叫我"，而价格取不到就什么都不会发生。
    """
    a = _alerts()
    try:
        try:
            _rt._rt_ensure(a.codes())
        except Exception:                                   # noqa: BLE001
            pass
        # ★ 分红由 valued() 自己解析（实际已公告的，不是账本字段），
        #   所以这里不再单独调 suggest_div —— 同一份算两遍迟早不一致。
        v = a.valued()
        v['day'] = datetime.now().date().isoformat()
        v['fired_today'] = [r for r in a.fired_log()
                            if str(r.get('ts', ''))[:10]
                            == datetime.now().date().isoformat()]
        return v
    except Exception as e:                                  # noqa: BLE001
        # 🔴 走翻译器：空 lake 上这里接到的是 duckdb 的
        #   `IOException: No files found ...` —— 原样返回的话页面上是
        #   一段裸 SQL 报错，而人要的是「我该去哪把数据建出来」。
        return base.explain_err(e)



def api_alerts_refresh_div(_q, body):
    """POST /api/alerts/refresh_div —— 手动重抓一次分红明细（绕过一天一次）。

    ★ 平时是**一天一次**（`ext_div` 自己按每只票记 fetched 日期）；
      这个按钮是"我知道刚出了公告"时用的，由人触发所以不需要节流。
    """
    a = _alerts()
    try:
        cs = a.codes()
        if not cs:
            return {'ok': True, 'n_codes': 0, 'n_rows': 0, 'n_new': 0}
        r = a.ext_div(cs, force=True)
        if r.get('err'):
            return {'error': '外部分红接口失败：%s' % r['err']}
        sg = a.suggest_div(cs)
        return {'ok': True, 'n_codes': len(cs),
                'n_rows': sum(len(v) for v in (r.get('rows') or {}).values()),
                'n_new': sum((v.get('ext') or {}).get('n_new') or 0
                             for v in sg.values())}
    except Exception as e:                                  # noqa: BLE001
        return {'error': '%s: %s' % (type(e).__name__, e)}



def api_alerts_suggest(q):
    """GET /api/alerts/suggest?code= —— 新增一行时的分红预填。"""
    a = _alerts()
    try:
        code = q.get('code') or ''
        d = a.suggest_div([code])
        from assay import stock as st
        jc = st.norm_code(code)
        return {'code': jc, 'suggest': d.get(jc)}
    except Exception as e:                                  # noqa: BLE001
        return base.explain_err(e)



def api_alerts_act(_q, body):
    """POST /api/alerts —— 加/改一行（set，整行覆盖）或删一行（remove）。

    ★ 归 --live 管：它写 live/ 下的账本。
    """
    if not base.ALLOW_LIVE:
        return {'error': '服务以只读模式启动 —— 用 python3 serve.py --live 开启'}
    a = _alerts()
    b = body or {}
    act = (b.get('act') or 'set').strip()
    try:
        if act == 'remove':
            a.remove(b.get('code') or '')
        elif act == 'set':
            # ★ 刻意不接 div —— 分红不手填（"预计分红"没有价值：
            #   猜出来的目标价看着和真的一样，而它错在你不会回头检查的地方）
            a.set_row(b.get('code') or '', b.get('tiers') or [],
                      note=b.get('note') or '', near=b.get('near'))
            _rt._rt_ensure([b.get('code') or ''])
        else:
            return {'error': 'act 只能是 set / remove，收到 %r' % act}
        return dict(api_alerts({}), ok=True)
    except a.AlertError as e:
        return {'error': str(e)}



def api_watchlist(q):
    """GET /api/watchlist?group= —— 当前自选 + 行情。

    ★ 顺手把"今天还没有实时数据"的那几只补一次（`_rt_ensure`）——
      服务重启前加进来的、或加进来时不在交易时段的，都靠这一步补上。
      抓到就不再 missing，所以不会每次刷新都打请求。
    """
    w = _watch()
    try:
        try:
            _rt._rt_ensure([x['code'] for x in w.current()])
        except Exception:                                   # noqa: BLE001
            pass            # 补抓失败不该让自选打不开
        return w.valued(q.get('group'))
    except Exception as e:                                  # noqa: BLE001
        # 🔴 走翻译器：空 lake 上这里接到的是 duckdb 的
        #   `IOException: No files found ...` —— 原样返回的话页面上是
        #   一段裸 SQL 报错，而人要的是「我该去哪把数据建出来」。
        return base.explain_err(e)



def api_watchlist_log(_q):
    """GET /api/watchlist/log —— 全部历史（含已移出的）。append-only。"""
    w = _watch()
    try:
        return {'rows': list(reversed(w.log()))}
    except Exception as e:                                  # noqa: BLE001
        return {'error': '%s: %s' % (type(e).__name__, e)}



def api_watchlist_sync(_q, body):
    """POST /api/watchlist/sync —— 立刻把持仓同步进自选。"""
    if not base.ALLOW_LIVE:
        return {'error': '服务以只读模式启动 —— 用 python3 serve.py --live 开启'}
    w = _watch()
    try:
        r = w.sync_live()
        if r.get('added'):
            _rt._rt_ensure([x['code'] for x in r['added']])
        return dict(r, rows=w.valued().get('rows'), groups=w.groups())
    except Exception as e:                                  # noqa: BLE001
        return {'error': '%s: %s' % (type(e).__name__, e)}

def api_watchlist_order(_q, body):
    """POST /api/watchlist/order —— 改页签顺序。body: {groups: [...]}

    ★ 归 --live 管（写 live/ 下的账本），只读模式下不许改。
    ★ 回传**生效后**的 groups（含只数）—— 页面拖完直接用它重渲染，
      不自己推算顺序（那就是第二份实现）。
    """
    if not base.ALLOW_LIVE:
        return {'error': '服务以只读模式启动 —— 去掉 --readonly 重启即可'}
    w = _watch()
    try:
        w.set_group_order((body or {}).get('groups') or [])
        return {'ok': True, 'groups': w.groups()}
    except w.WatchError as e:
        return {'error': str(e)}
    except Exception as e:                                  # noqa: BLE001
        return {'error': '%s: %s' % (type(e).__name__, e)}



def api_watchlist_act(_q, body):
    """POST /api/watchlist —— 追加一条（add / remove / group / note）。

    ★ 归 --live 管：它写 live/ 下的账本。只读模式下不许改。
    """
    if not base.ALLOW_LIVE:
        return {'error': '服务以只读模式启动 —— 用 python3 serve.py --live 开启'}
    w = _watch()
    b = body or {}
    try:
        r = w.act((b.get('act') or '').strip(), b.get('code') or '',
                  group=b.get('group'), note=b.get('note') or '')
        # ★ 加进来的那一刻就抓一次 —— 不然要等下一轮轮询（≤60s）、
        #   bar 还要等轮转（~42 分钟），而人是马上要看的。
        rte = None
        if (b.get('act') or '').strip() == 'add' and r.get('code'):
            rte = _rt._rt_ensure([r['code']])
        return {'ok': True, 'rec': r, 'rows': w.valued().get('rows'),
                'groups': w.groups(), 'rt': rte}
    except w.WatchError as e:
        return {'error': str(e)}


# ★ 只读 SQL 页（/#/query）已取消 —— 命令行工具仍在
#   `datalake/build/query.py`（`--sql-stdin` / `--json` / `--schema`），
#   三层只读保证也都在那里。页面上那一层不再暴露。

# ==================== 财务数据（聚宽）导入 ====================
# 闭环三步：① 页面给出可直接粘进研究环境的代码（SINCE/QUARTERS 按本地状态
# 预填）② 在聚宽跑完下载那**一个** tar ③ 页面上传，服务端 merge + 跑全部
# loader。原来第 ③ 步要人对着打印出来的命令手抄 5~8 条，抄漏一条就是
# raw 与 std 不一致 —— 而那不会报错。
