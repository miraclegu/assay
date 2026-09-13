"""归档查看服务的【骨架】：路由表 + HTTP Handler + serve()。

    python3 serve.py            # 默认 http://127.0.0.1:8770

## 两个设计决定（没变）

1. **stdlib-only**（`ThreadingHTTPServer`）。装 Flask 只为一个本地看板不值得，
   而且多一个依赖就多一处「环境不一致导致跑不起来」。
2. **图表手写 SVG，不用 CDN**。CDN 加载失败就是白屏，离线时完全不可用。

## 安全

`run_id` 来自 URL，**必须先在已知归档集合里查到才用于拼路径**，
绝不把用户输入直接 join 进文件路径（目录穿越）。服务默认只监听 127.0.0.1。

## 这一页只是骨架 —— 2,558 行按【产品域】拆进了 `srv/`

切点是两轮客观分析的结果，不是按行数均分：作者原有的段落标记给出 9 个域，
「被 2 个以上段调用」的定义（12 个）+ 传递闭包进 `srv/base.py`。
依赖是单向的：`server → srv.* → srv.base`，`srv/live.py` 与 `srv/watch.py`
单向依赖 `srv/rt.py`，反向调用一律走 base 里的延迟导入封装。

🔴 **模块级 `__getattr__`（PEP 562）是【转发】而不是 re-export。**
`assay.server` 是对外契约（selftest 用 28 个 `sv.*` 名字），其中
`ALLOW_BACKTEST` / `ALLOW_LIVE` 是 **serve() 会重新赋值的 bool** ——
写成 `from .srv.base import ALLOW_LIVE` 的话这里拿到的是**副本**，
serve() 改了值外部读到的还是旧的，**而这不报错**。转发保证永远读到真值。
"""
import json
import mimetypes
import os
import sys
import threading
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .srv import base
# ★ 这几个名字指的是 `srv/` 下的**路由模块**，不是 assay/live.py 那些业务模块
#   （业务模块一律通过 base 里的延迟导入封装拿：base._live() / base._rt() …）
from .srv import docs, live, market, rt, runs, stock, sync, watch


ROUTES = {
    '/api/docs': docs.api_docs,
    '/api/doc': docs.api_doc,
    '/api/runs': runs.api_runs,
    '/api/run': runs.api_run,
    '/api/equity': runs.api_equity,
    '/api/trades': runs.api_trades,
    '/api/run/trades_of': runs.api_run_trades_of,
    '/api/holdings': runs.api_holdings,
    '/api/day': runs.api_day,
    '/api/rejects': runs.api_rejects,
    '/api/code': lambda q: runs.api_text(q, 'strategy.py'),
    '/api/log': lambda q: runs.api_text(q, 'run.log'),
    '/api/version': runs.api_version,
    '/api/datafp': runs.api_datafp,
    '/api/job': runs.api_job,
    '/api/marks': runs.api_marks,
    '/api/live/accounts': live.api_live_accounts,
    '/api/live/account': live.api_live_account,
    '/api/live/signal': live.api_live_signal,
    '/api/live/explain': live.api_live_explain,
    '/api/live/explains': live.api_live_explains,
    '/api/live/code': live.api_live_code,
    '/api/live/strategy': live.api_live_strategy,
    '/api/live/equity': live.api_live_equity,
    '/api/live/intraday': live.api_live_intraday,
    '/api/live/trades_of': live.api_live_trades_of,
    '/api/live/bench': live.api_live_bench,
    '/api/live/exec_diff': live.api_live_exec_diff,
    '/api/live/fills': live.api_live_fills,
    '/api/sync': sync.api_sync,
    '/api/sync/auto': sync.api_sync_auto,
    '/api/sync/schedule': sync.api_sync_schedule,
    '/api/sync/jq_code': sync.api_sync_jq_code,
    '/api/stock/search': stock.api_stock_search,
    '/api/stock/profile': stock.api_stock_profile,
    '/api/stock/kline': stock.api_stock_kline,
    '/api/stock/finance': stock.api_stock_finance,
    '/api/rt/status': rt.api_rt_status,
    '/api/rt/bars': rt.api_rt_bars,
    '/api/stock/indicators': stock.api_stock_indicators,
    '/api/stock/events': stock.api_stock_events,
    '/api/stock/peers': stock.api_stock_peers,
    '/api/stock/links': stock.api_stock_links,
    '/api/stock/sectors': stock.api_stock_sectors,
    '/api/compare': stock.api_compare,
    '/api/market/overview': market.api_market_overview,
    '/api/sector/kinds': market.api_sector_kinds,
    '/api/sector/list': market.api_sector_list,
    '/api/sector/members': market.api_sector_members,
    '/api/alerts': watch.api_alerts,
    '/api/alerts/suggest': watch.api_alerts_suggest,
    '/api/watchlist': watch.api_watchlist,
    '/api/watchlist/log': watch.api_watchlist_log,
    '/api/sync/log': sync.api_sync_log,
}



class Handler(BaseHTTPRequestHandler):
    server_version = 'assay'

    def log_message(self, fmt, *args):       # 静音访问日志，别淹没终端
        pass

    def _send(self, code, body, ctype='application/json; charset=utf-8'):
        data = body if isinstance(body, bytes) else body.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):                       # noqa: N802
        u = urlparse(self.path)
        POSTS = {'/api/backtest': runs.api_backtest, '/api/mark': runs.api_mark,
                 '/api/live/save': live.api_live_save,
                 '/api/live/fill': live.api_live_fill,
                 '/api/live/tick': live.api_live_tick,
                 '/api/live/cash': live.api_live_cash,
                 '/api/live/fee_infer': live.api_live_fee_infer,
                 '/api/live/fee_rate': live.api_live_fee_add,
                 '/api/sync/auto': sync.api_sync_auto_set,
                 '/api/sync/schedule': sync.api_sync_schedule_set,
                 '/api/alerts': watch.api_alerts_act,
                 '/api/alerts/refresh_div': watch.api_alerts_refresh_div,
                 '/api/watchlist': watch.api_watchlist_act,
                 '/api/watchlist/sync': watch.api_watchlist_sync,
                 '/api/watchlist/order': watch.api_watchlist_order,
                 '/api/rt/poll': rt.api_rt_poll,
                 '/api/live/backtest': live.api_live_backtest,
                 '/api/sync/run': sync.api_sync_run}
        # ★ 上传走【原始字节】分支：几十 MB 的包不该先变成 base64 再
        #   json.loads（多传 33% + 整包再复制一遍）。所以它不能和下面的
        #   JSON 解析共用一条路。
        if u.path == '/api/sync/jq_upload':
            try:
                n = int(self.headers.get('Content-Length') or 0)
                if n <= 0:
                    return self._send(400, json.dumps(
                        {'error': '没有上传内容'}, ensure_ascii=False))
                if n > sync.JQ_UPLOAD_MAX:
                    return self._send(413, json.dumps(
                        {'error': '文件太大（%.0f MB > 上限 %.0f MB）'
                                  % (n / 1e6, sync.JQ_UPLOAD_MAX / 1e6)},
                        ensure_ascii=False))
                raw = self.rfile.read(n)
                r = sync.api_sync_jq_upload(self.headers, raw)
            except Exception as e:                          # noqa: BLE001
                import traceback
                traceback.print_exc()
                return self._send(500, json.dumps(
                    {'error': '%s: %s' % (type(e).__name__, e)},
                    ensure_ascii=False))
            return self._send(200, json.dumps(r, ensure_ascii=False, default=str))
        fn = POSTS.get(u.path)
        if fn is None:
            return self._send(404, json.dumps({'error': 'no such endpoint'}))
        try:
            n = int(self.headers.get('Content-Length') or 0)
            body = json.loads(self.rfile.read(n).decode('utf-8')) if n else {}
        except Exception as e:                   # noqa: BLE001
            return self._send(400, json.dumps({'error': '请求体不是合法 JSON: %s' % e},
                                              ensure_ascii=False))
        try:
            r = fn({}, body)
        except Exception as e:                   # noqa: BLE001
            import traceback
            traceback.print_exc()
            return self._send(500, json.dumps({'error': '%s: %s' % (type(e).__name__, e)},
                                              ensure_ascii=False))
        # ★ default=str 与 GET 分支保持一致 —— POST 的返回里也可能带
        #   date/Timestamp（实盘持仓的建仓日就是），少这一个参数就是 500。
        return self._send(200, json.dumps(r, ensure_ascii=False, default=str))

    def do_GET(self):                        # noqa: N802
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if u.path in ROUTES:
            try:
                r = ROUTES[u.path](q)
            except Exception as e:           # noqa: BLE001
                # 错误要显式返回，不能静默给空 —— 前端才好判断是没数据还是出错了
                import traceback
                traceback.print_exc()
                return self._send(500, json.dumps(
                    {'error': '%s: %s' % (type(e).__name__, e)}, ensure_ascii=False))
            if r is None:
                return self._send(404, json.dumps({'error': 'run_id 不存在或无效'},
                                                  ensure_ascii=False))
            return self._send(200, json.dumps(r, ensure_ascii=False, default=str))
        # 静态文件
        rel = u.path.lstrip('/') or 'index.html'
        p = os.path.normpath(os.path.join(base.WEB, rel))
        if not p.startswith(base.WEB) or not os.path.isfile(p):
            return self._send(404, 'not found', 'text/plain; charset=utf-8')
        ctype = mimetypes.guess_type(p)[0] or 'application/octet-stream'
        if ctype.startswith('text/') or ctype.endswith('javascript'):
            ctype += '; charset=utf-8'
        self._send(200, open(p, 'rb').read(), ctype)



def serve(host='127.0.0.1', port=8770, allow_backtest=False, allow_live=False):
    base.ALLOW_BACKTEST = bool(allow_backtest)
    base.ALLOW_LIVE = bool(allow_live)
    n = len(base._scan())
    print('assay 归档查看服务  http://%s:%d   (%d 次回测)' % (host, port, n))
    print('模式：%s' % ('可触发回测（--allow-backtest）'
                      if base.ALLOW_BACKTEST else '只读（网页触发回测已关闭）'))
    # flush：重定向到文件时 stdout 是块缓冲，SIGTERM 不会刷新，
    # 启动横幅会看着像根本没打印。
    if base.ALLOW_LIVE:
        m = base._live()
        accts = m.load_accounts()
        cal = m.calendar_meta()
        print('实盘模块：开启  %d 个账户  日历来源 %s'
              % (len(accts), cal.get('source')))
        base._live_thread = threading.Thread(target=live._live_loop, daemon=True)
        base._live_thread.start()
        # 盘中 1 分钟线：只在 server 开着时跑，只抓持仓，只在交易时段
        # 🔴 局部变量**不能叫 `rt`** —— 模块级 `from .srv import ... rt` 是
        #   路由模块 `assay.srv.rt`，而 `base._rt()` 返回的是业务模块
        #   `assay.realtime`。同名会把模块级那个**遮蔽**掉，于是
        #   `rt._RT` / `rt._rt_loop` 去 assay.realtime 找 -> AttributeError。
        #   实测踩到：`--readonly` 不走这个分支，所以拆分时的验证全程没执行到
        #   这几行，直到真正 `python3 serve.py` 才炸。
        rtm = base._rt()                       # 业务模块 assay.realtime
        rt._RT['thread'] = threading.Thread(target=rt._rt_loop, daemon=True)
        rt._RT['thread'].start()
        print('盘中 1 分钟线：开启  时段 %s  存 %s'
              % (' / '.join('%s~%s' % (a.strftime('%H:%M'), b.strftime('%H:%M'))
                            for a, b in rtm.SESSIONS),
                 os.path.join(base._datalake_dir(), 'rt')))
    else:
        print('实盘模块：关闭（--live 开启）')
        print('盘中 1 分钟线：关闭（跟随 --live）')
    print('Ctrl-C 退出', flush=True)
    ThreadingHTTPServer((host, port), Handler).serve_forever()


# ---------------------------------------------------------------- 对外门面
#   `assay.server` 是对外契约：selftest 用 28 个 `sv.*` 名字（含 _scan /
#   _staleness / _DOCS / _RT / ALLOW_LIVE 这些私有名与模块级状态）。
#   拆分之后它们分散在 srv/ 各域，这里用 PEP 562 的模块级 __getattr__
#   **转发**过去 —— 见文件头那条：转发而不是 re-export，
#   因为 ALLOW_* 是 serve() 会重新赋值的，re-export 拿到的是副本。
_FACADE = (base, runs, docs, live, rt, sync, stock, market, watch)


class _Facade(types.ModuleType):
    """让 `assay.server` 成为对 `srv/` 各域的透明门面 —— **读和写都转发**。

    🔴 **为什么 PEP 562 的模块级 `__getattr__` 不够**：它只在属性查找
      **失败**时才调用。而外部有 `sv.ALLOW_LIVE = True` 这种**赋值**用法
      （selftest 里 26 处，用来模拟不同启动模式）——赋值会在本模块的
      `__dict__` 里建一个**副本**，之后读取走正常查找、不再转发，于是
      「设置了却不生效」：各域读的还是 base 里的旧值。
      实测就是这么挂掉 3 个用例的（启动开关 / 自选 / 本身那条断言）。
      所以真值**只有一份**（在 `base`），这里把赋值也转回去。

    ★ 白名单只放**会被外部重新赋值**的那几个。其余属性照常设在本模块上
      —— 否则 `ROUTES` 这种本模块自己的定义也会被转走。
    """

    #   `_BOOT_TS`：selftest 把它调早来模拟"进程比代码旧"的横幅。
    #   不转发的话赋值只落在 server 自己的 __dict__ 上，各域读的还是原值
    #   —— 又是一次"设置了却不生效"。
    _FWD = ('ALLOW_BACKTEST', 'ALLOW_LIVE', '_live_thread', '_BOOT_TS')

    def __getattr__(self, name):
        for _m in _FACADE:
            try:
                return getattr(_m, name)
            except AttributeError:
                pass
        raise AttributeError('module %r has no attribute %r'
                             % (__name__, name))

    def __setattr__(self, name, value):
        if name in _Facade._FWD:
            setattr(base, name, value)          # 真值只有一份，在 base
        else:
            super().__setattr__(name, value)


# ★ 替换本模块的类型 —— 放在文件末尾：模块体执行期间（ROUTES = {...} 那些）
#   还是普通 ModuleType，不会被 __setattr__ 拦。
sys.modules[__name__].__class__ = _Facade
