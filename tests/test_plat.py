# -*- coding: utf-8 -*-
"""平台：服务与路由 / 页面框架与导航 / 归档管理 / 拆分守卫

这一份是 `selftest.py` 按**产品域**拆出来的一块（2026-09-17，见
`tests/_base.py` 的说明）。判据、注释、教训一个字都没动 —— 拆的是**归属**，
不是内容。
"""
from tests._base import *          # noqa: F401,F403  框架 + 共用辅助
from tests._base import (CASES, JQ, REPO, case, _run, _pages, _web_files,  # noqa: F401
                         _defining_file, io_open_text,
                         _kset, _kmain, _kfq, _klog, _lp_tab, _via_pop,
                         _all_case_src)
import os, re, sys, io, json, glob, time, shutil, subprocess, datetime
import inspect, textwrap  # noqa: E401,F401


@case('查看服务 API 契约与安全')
def t_server():
    """归档查看服务只用标准库。两件必须验的事：
      1. 每个端点的返回结构（前端靠它渲染，静默改结构就是白屏）
      2. run_id 来自 URL —— 必须只接受索引里存在的值，绝不直接拼路径
    """
    import json as _json
    import threading
    import urllib.error
    import urllib.parse
    import urllib.request
    from http.server import ThreadingHTTPServer

    from assay import server as sv

    idx = sv._scan()
    assert idx, '归档为空，无法验证 API（先跑一次 run.py）'
    rid = sorted(idx)[-1]

    httpd = ThreadingHTTPServer(('127.0.0.1', 0), sv.Handler)
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    base = 'http://127.0.0.1:%d' % port

    def get(path):
        with urllib.request.urlopen(base + path, timeout=20) as r:
            return r.status, _json.loads(r.read().decode())

    try:
        # 1) 契约：前端依赖的字段必须在
        _, runs = get('/api/runs')
        assert isinstance(runs, list) and runs, '/api/runs 返回空'
        for k in ('run_id', 'group', 'strategy', 'annual_return', 'max_drawdown'):
            assert k in runs[0], '/api/runs 缺字段 %s' % k
        _, run = get('/api/run?id=' + rid)
        assert 'meta' in run and 'stats' in run
        for k in ('code_sha256', 'cost', 'data_fingerprint', 'trading_days'):
            assert k in run['meta'], 'meta 缺 %s' % k
        _, eq = get('/api/equity?id=' + rid)
        assert len(eq['dates']) == len(eq['equity']) > 0, '权益曲线长度不一致'
        assert abs(eq['equity'][0] - 1.0) < 0.5, '权益未归一化到起点 1x'
        _, tr = get('/api/trades?id=' + rid)
        assert 'rows' in tr and 'total' in tr
        # 持仓是【分页】的：全量 22k 行不能一次发（前端渲染 22k 个 DOM 会卡死）
        _, hd = get('/api/holdings?id=' + rid + '&offset=0&limit=50')
        for k in ('total', 'offset', 'limit', 'n_days', 'rows'):
            assert k in hd, '/api/holdings 缺字段 %s' % k
        assert len(hd['rows']) <= 50, '分页未生效，返回 %d 行' % len(hd['rows'])
        # 日期必须【倒序且同日相邻】—— 前端按日期变化插分隔块，靠的就是这个不变量
        ds = [r['date'] for r in hd['rows']]
        assert ds == sorted(ds, reverse=True), '持仓未按日期倒序，日期分块会错乱'
        assert len(set(ds)) == len([1 for i, d in enumerate(ds)
                                    if i == 0 or ds[i - 1] != d]), '同一天的行不相邻'
        # 越界/超限必须收敛，不能报错
        _, hd2 = get('/api/holdings?id=' + rid + '&offset=99999999&limit=9999')
        assert hd2['offset'] < hd2['total'] and hd2['limit'] <= 500, \
            '越界参数未收敛: offset=%s limit=%s' % (hd2['offset'], hd2['limit'])
        _, rj = get('/api/rejects?id=' + rid)
        assert 'by_reason' in rj
        for ep in ('/api/code?id=', '/api/log?id='):
            _, tx = get(ep + rid)
            assert 'text' in tx, '%s 缺 text' % ep

        # 2) 安全：非法 / 穿越型 run_id 一律 404，不能读到任何文件
        bad = 0
        for x in ('../../../etc/passwd', '../../assay/server.py', "'; DROP--",
                  '20260101-000000-aaaaaa', ''):
            try:
                get('/api/code?id=' + urllib.parse.quote(x))
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    bad += 1
                    continue
                raise AssertionError('非法 run_id %r 返回 %d，预期 404' % (x, e.code))
            else:
                raise AssertionError('非法 run_id %r 竟然成功了' % x)
        assert bad == 5, '只有 %d/5 个非法 run_id 被拒' % bad
    finally:
        httpd.shutdown()
    return '8 个端点契约通过（持仓分页/倒序/越界收敛）；5 类非法 run_id 全部 404'


@case('JS 不许引用未定义的名字（Python 侧那道防线的 JS 版）', tag='fast')
def t_js_undef():
    """🔴🔴 2026-09-16 用户报「回测里的持仓页面展示不出列表」，查下来是
    `paneHoldings` 用了 `money()` —— 而它是 `drawDay` 里的一个**局部 const**。
    跨函数引用未定义的名字，切到那个页签当场抛 `money is not defined`、
    一行都渲染不出来，**而它只在控制台里报**（页面上就是"什么都没有"）。

    拆 `srv/` 那次，Python 侧建过同一道防线（"扫每个模块里 Load 但未绑定的
    名字"，当场抓出 `_JOBS` 那两处）。**JS 侧一直没有** —— 只有
    ① `node --check`（只查语法）② 跨文件顶层重名。这次的 bug 正落在缺口里。

    ★ 判据**宁可漏报也不误报**（见 `tools/js_undef.py` 的取舍）：
      一个天天报假警的检查等于没有检查。
    ★ 反向自证：把 `money` 改回局部，这条必须报出来（变异实测）。
    """
    import subprocess
    root = REPO
    r = subprocess.run([sys.executable, 'tools/js_undef.py', 'web'],
                       cwd=root, capture_output=True, text=True, timeout=180)
    assert r.returncode == 0, 'js_undef.py 跑挂了：%s' % (r.stderr or '')[-300:]
    lines = [x for x in r.stdout.splitlines() if x.startswith('  ')]
    assert not lines, \
        ('JS 里有 %d 处引用了未定义的名字 —— 这种错**只在控制台里报**，'
         '页面上只是那一块什么都没有：\n%s' % (len(lines), '\n'.join(lines[:8])))
    n = 0
    for x in r.stdout.splitlines():
        if x.startswith('全局顶层名'):
            n = int(x.split('扫描 ')[1].split(' ')[0])
    assert n >= 15, '只扫到 %d 个文件，怕是路径不对（web 下有 20+ 个）' % n
    return '扫了 %d 个 JS / 内联脚本，0 处未定义引用' % n


@case('回测详情页「持仓」页签：真的列得出逐日持仓（playwright）', tag='web')
def t_run_holdings_pane():
    """🔴 用户 2026-09-16："回测里的持仓页面出现问题了，展示不出持仓的具体
    列表了。" —— `paneHoldings` 用了 `money()`，而它当时是 `drawDay` 里的一个
    **局部 const**：跨函数引用未定义的名字，整个页签当场抛
    `money is not defined`、一行都渲染不出来，**而它只在控制台里报**。

    是 2026-09-14 那次「份额一律展示不复权」把 `fmtN(v,1)` 换成 `money(v)`
    时带进来的，一直坏到用户点开才发现。

    🔴 为什么既有那条「九个页签逐个渲染」没抓到：它挑的归档
      `holdings.parquet` 被 `prune_runs.py` 清过 —— 走的是"已清理"那个分支，
      **表格根本没渲染**。这和当初「持仓不换算」那条第一轮漏掉的是
      **同一个陷阱**（"我挑的旧归档 rows 是空的 -> 整块空转"）。
      所以这条用例**先挑一个明细还在的归档**，再断言它真的列出了行。
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return '跳过（无 playwright）'
    import glob
    import threading
    from http.server import ThreadingHTTPServer

    from assay import server as sv
    # ★ 先挑一个 holdings 还在的归档 —— 挑到被清理过的就是空转。
    _root = REPO
    cands = sorted(d for d in glob.glob(os.path.join(_root, 'runs/*/*/*/'))
                   if os.path.exists(os.path.join(d, 'holdings.parquet')))
    if not cands:
        return '跳过（没有带 holdings 的归档）'
    rid = os.path.basename(cands[-1].rstrip('/'))
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), sv.Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as p:
            try:
                br = p.chromium.launch()
            except Exception as e:                          # noqa: BLE001
                return '跳过（浏览器不可用: %s）' % type(e).__name__
            pg = br.new_page(viewport={'width': 1500, 'height': 1100})
            errs = []
            pg.on('pageerror', lambda e: errs.append(str(e)))
            pg.goto('http://127.0.0.1:%d/#/run/%s' % (port, rid),
                    wait_until='networkidle')
            pg.wait_for_timeout(3000)
            names = pg.locator('#tabs div').all_inner_texts()
            i = [k for k, t in enumerate(names) if '持仓' in t]
            assert i, '详情页没有「持仓」页签：%r' % names
            pg.locator('#tabs div').nth(i[0]).click()
            pg.wait_for_timeout(2500)
            assert not errs, \
                ('切到持仓页签就抛错了：%r —— 这种错只在控制台里报，'
                 '页面上只是"什么都没有"' % errs[:2])
            n = pg.locator('#p4 table tr').count()
            assert n > 5, \
                ('持仓页签一行都没列出来（%d 行）—— 接口是通的，'
                 '坏在渲染那一步' % n)
            # 🔴 份额要是**真实股数**（整数量级），不是后复权记账单位
            #   （那种是 774.835189 这样的小数）——「展示层换算回不复权」
            #   那条纪律的可见后果就在这一列。
            txt = pg.evaluate(
                """() => {const h = [...document.querySelectorAll('#p4 table th')]
                    .map(e => e.textContent.trim());
                  const j = h.findIndex(t => t.indexOf('份额') >= 0);
                  const tr = [...document.querySelectorAll('#p4 table tr')]
                    .filter(r => r.children.length > 3).slice(0, 5);
                  return {j: j, v: tr.map(r => (r.children[j] || {}).innerText)};}""")
            assert txt['j'] >= 0, '持仓表没有「份额」这一列'
            vals = [v for v in txt['v'] if v and v.strip() not in ('—', '')]
            assert vals, '份额那一列是空的'
            assert not any('.' in v for v in vals), \
                ('份额列出现了小数 %r —— 那是**后复权记账单位**，'
                 '券商对账单上没有这种数（展示层要换算回真实股数）' % vals[:2])
            # 分页条要说清总量（这一页会很长，服务端分页）
            _pg = pg.evaluate(
                "()=>document.querySelector('#p4 .pg')?.innerText || ''")
            assert '页' in _pg and '条' in _pg, '持仓页没有分页条：%r' % _pg[:60]
            assert 'NaN' not in _pg, '分页条出现 NaN：%r' % _pg[:60]
            br.close()
    finally:
        httpd.shutdown()
    return '持仓页签列出 %d 行（%s），份额是真实股数、分页条正常' % (n, rid)


@case('网页看板真实渲染（playwright）', tag='web')
def t_ui():
    """★ JS 语法检查过不代表能渲染 —— 运行时错误在终端里看不到。
    用真实浏览器跑一遍：目录树、展开、点进详情、九个页签、返回、浏览器后退，
    并**量**表头吸顶位置（原先 top:52px 把表头往下推了 52px，
    而日期分隔块的偏移我一开始估 23px、实测是 27px —— 估值都会错）。
    playwright 或浏览器不可用时跳过，不让环境差异变成假失败。
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return '跳过（无 playwright）'
    import threading
    from http.server import ThreadingHTTPServer

    from assay import server as sv
    if not sv._scan():
        return '跳过（归档为空）'
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), sv.Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as p:
            try:
                br = p.chromium.launch()
            except Exception as e:                          # noqa: BLE001
                return '跳过（浏览器不可用: %s）' % type(e).__name__
            pg = br.new_page(viewport={'width': 1500, 'height': 900})
            errs = []
            pg.on('pageerror', lambda e: errs.append(str(e)))
            pg.on('console',
                  lambda m: errs.append('console: ' + m.text) if m.type == 'error' else None)
            # 回测归档已移到 #/runs —— `/` 现在是总览首页
            pg.goto('http://127.0.0.1:%d/#/runs' % port, wait_until='networkidle')
            pg.wait_for_timeout(900)

            # 目录树：任意深度文件夹 -> 策略文件 -> 代码版本 -> 回测
            # ★ 策略身份 = 文件内容哈希（同一路径改一个字符就是另一个策略），
            #   所以版本是独立一层；只有一个版本时跳过这层。
            assert pg.locator('.nd.d0').count() > 0, '目录页没有顶层节点'
            assert pg.locator('.nd.op').count() == 0, '节点默认应【全部收起】'
            # ★ 目录树深度要有上界。诊断脚本放在 scratchpad 时 strategy_path 是
            #   逃逸出仓库的相对路径（../../../../private/tmp/.../froec_oldsql.py），
            #   若按目录逐层展开会铺出 8 层每层一个子节点的空壳。仓库内最深是
            #   strategies/<组>/<文件> -> 目录层只有 1 层（d0），版本层 d1、
            #   回测表在 d1/d2 内，所以 d5 及更深一定是路径没被压平。
            deep = pg.locator('.nd.d5, .nd.d6, .nd.d7, .nd.d8').count()
            assert deep == 0, ('目录树出现 %d 个 d5+ 深层节点 —— 仓库外路径没被'
                               '压成一层？（见 buildTree 的 _仓库外 分支）' % deep)
            pg.locator('.nd.d0').first.click(); pg.wait_for_timeout(250)
            assert pg.locator('.nd.op').count() == 1, '点击文件夹未展开'
            # 逐层点到有回测表为止（深度不定，最多试 5 层）
            rows = pg.locator('.runs:visible tbody tr')
            for _ in range(5):
                if rows.count():
                    break
                nxt = pg.locator('.nd:visible:not(.op)')
                if not nxt.count():
                    break
                nxt.last.click(); pg.wait_for_timeout(300)
            assert rows.count() > 0, '逐层展开后仍没有回测行'
            # 回测必须按【运行时间】倒序
            # ★ 列下标【按表头文字算】，不写死数字 —— 表格加一列（如后来加的
            #   ★ 选中标记列）就会把所有下标推移一位，写死数字会误断成
            #   「未按时间倒序」，而实际读到的是胜率那一列。
            tb = pg.locator('.runs:visible').first
            ths = tb.locator('thead th')
            ci = next((i for i in range(ths.count())
                       if ths.nth(i).inner_text().strip() == '跑于'), None)
            assert ci is not None, '回测表里找不到「跑于」列'
            ra = [rows.nth(i).locator('td').nth(ci).inner_text()
                  for i in range(min(rows.count(), 6))]
            assert ra == sorted(ra, reverse=True), '回测未按运行时间倒序: %s' % ra
            rows.first.click(); pg.wait_for_timeout(1500)
            assert '#/run/' in pg.evaluate('location.hash'), '未跳转到详情'
            assert pg.is_visible('#main') and not pg.is_visible('#cat'), '视图未切换'
            assert pg.locator('#p0 .card').count() > 20, '概览指标卡过少'

            # 九个页签逐个渲染，任一抛错都会进 errs
            for i in range(9):
                pg.locator('#tabs div').nth(i).click(); pg.wait_for_timeout(600)
            # 量表头吸顶：滚动后 th 应贴 .tw 顶部（容差含 1px 边框）
            gap = pg.evaluate("""()=>{const tw=document.querySelector('.pane.on .tw');
              if(!tw) return 0; tw.scrollTop=250;
              return new Promise(r=>requestAnimationFrame(()=>{
                const T=tw.getBoundingClientRect(),H=tw.querySelector('th').getBoundingClientRect();
                r(Math.round(H.top-T.top));}));}""")
            assert abs(gap) <= 2, '滚动后表头距容器顶 %dpx，应贴顶（sticky top 写错？）' % gap

            # ---- 收益明细：年热力 → 年详情页 → 月内日热力 ----
            # ★ 全部由 /api/equity 的 dates+equity 现算，【不依赖新表】。
            #   这里对着曲线逐项校，防止前端口径悄悄漂掉；并校年详情页是
            #   独立路由（浏览器后退必须能用）。
            # ---- 权益曲线：区间选择，且口径必须与收益明细的年收益一致 ----
            # 曾用「区间内首日」当基点，2024 会显示 +6.52% 而热力图是 +8.68%
            # —— 差的就是首日那根。基点必须取【区间前一交易日】收盘。
            names0 = pg.locator('#tabs div').all_inner_texts()
            pg.locator('#tabs div').nth(names0.index('权益曲线')).click()
            pg.wait_for_timeout(500)
            rgs = pg.locator('#p1 .rg').all_inner_texts()
            assert '全部' in rgs and '近1年' in rgs, '区间条缺预设按钮: %s' % rgs
            ytest = [x for x in rgs if x.isdigit() and len(x) == 4]
            assert len(ytest) >= 2, '区间条没列出年份'
            yb = ytest[len(ytest) // 2]
            pg.locator('#p1 .rg', has_text=re.compile('^%s$' % yb)).first.click()
            pg.wait_for_timeout(600)
            nt = ' '.join(pg.locator('#p1 .note').first.inner_text().split())
            m2 = re.search(r'区间收益\s*([+-][\d.]+)%', nt)
            assert m2, '区间说明里没有区间收益: %s' % nt[:80]
            eq_ret = float(m2.group(1))
            # 用曲线自己算一遍该年收益（基点 = 前一交易日）
            eqj0 = pg.evaluate('({d:DATA.eq.dates, e:DATA.eq.equity})')
            ii = [k for k, d in enumerate(eqj0['d']) if d[:4] == yb]
            want = (eqj0['e'][ii[-1]] / eqj0['e'][ii[0] - 1] - 1) * 100
            assert abs(eq_ret - want) < 0.02, \
                '%s 区间收益 %.2f%% != 曲线算的 %.2f%%（基点取错？）' % (yb, eq_ret, want)
            rng = '区间条 %d 档，%s 区间收益 %+.2f%% 与曲线一致' % (len(rgs), yb, eq_ret)
            pg.locator('#p1 .rg', has_text=re.compile('^全部$')).first.click()
            pg.wait_for_timeout(400)

            base_hash = pg.evaluate('location.hash')   # 形如 #/run/<id>
            names = pg.locator('#tabs div').all_inner_texts()
            pg.locator('#tabs div').nth(names.index('收益明细')).click()
            pg.wait_for_timeout(500)
            eqj = pg.evaluate('({d:DATA.eq.dates, e:DATA.eq.equity})')
            import collections, calendar as _cal
            byy = collections.OrderedDict()
            for d, v in zip(eqj['d'], eqj['e']):
                byy.setdefault(d[:4], []).append(v)
            ys = list(byy)
            # ★ 热力格必须【底色 vs 数字】有足够对比 —— 曾经底色和数字同色相
            #   （红底红字），实测对比度只有 1.28:1，等于看不见。
            #   这里量最强的那个格子，要求 >= 4.5:1（WCAG AA）。
            def _lin(c):
                c = c / 255.0
                return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
            def _lum(v):
                r, g, b = [_lin(x) for x in v]
                return 0.2126 * r + 0.7152 * g + 0.0722 * b
            def _rgb(css, under=(23, 27, 33)):
                n = [float(x) for x in re.findall(r'[\d.]+', css)]
                r, g, b = n[:3]; a = n[3] if len(n) > 3 else 1.0
                return tuple((r, g, b)[i] * a + under[i] * (1 - a) for i in range(3))
            st = pg.evaluate("""()=>{let best=null;
              document.querySelectorAll('#p2 .hm.y .hc').forEach(e=>{
                const s=getComputedStyle(e), v=e.querySelector('.v');
                const m=(s.backgroundColor.match(/[\\d.]+/g)||['0','0','0','1']);
                const a=parseFloat(m[3]===undefined?1:m[3]);
                if(!best||a>best.a) best={a,bg:s.backgroundColor,
                                          fg:getComputedStyle(v).color};});
              return best;}""")
            lb, lf = _lum(_rgb(st['bg'])), _lum(_rgb(st['fg']))
            cr = (max(lb, lf) + .05) / (min(lb, lf) + .05)
            assert cr >= 4.5, '热力格对比度只有 %.2f:1（底 %s / 字 %s），红底红字回来了？' \
                              % (cr, st['bg'], st['fg'])

            yc = pg.locator('#p2 .hm.y .hc')
            assert yc.count() == len(ys), '年热力 %d 格 != %d 年' % (yc.count(), len(ys))
            def _dd(vals):
                pk = 0.0; mx = 0.0
                for v in vals:
                    pk = max(pk, v)
                    if pk: mx = max(mx, 1 - v / pk)
                return mx
            t0 = yc.first.inner_text().split('\n')
            v0 = byy[ys[0]]
            assert t0[0] == ys[0], '首格年份 %s != %s' % (t0[0], ys[0])
            assert abs(float(t0[1].rstrip('%')) - (v0[-1] / v0[0] - 1) * 100) < 0.06, \
                '年收益 %s vs %.2f%%' % (t0[1], (v0[-1] / v0[0] - 1) * 100)
            assert ('%d 日' % len(v0)) in t0[2], '交易日数不符: %s vs %d' % (t0[2], len(v0))
            assert abs(float(t0[2].split('回撤 ')[1].split('%')[0]) - _dd(v0) * 100) < 0.06, \
                '年内回撤 %s vs %.2f%%' % (t0[2], _dd(v0) * 100)
            # 点年 -> 独立路由的年详情页
            yc.nth(1).click(); pg.wait_for_timeout(600)
            yr = ys[1]
            assert pg.evaluate('location.hash').endswith('/y/' + yr), \
                '年详情没有独立 hash: %s' % pg.evaluate('location.hash')
            assert pg.locator('#p2 .hm.m .hc').count() == 12, '月热力不是 12 格'
            # 点月 -> 日热力；日历格数=当月天数，交易日数=曲线里的实际条数
            pg.locator('#p2 .hm.m .hc[data-m]').first.click(); pg.wait_for_timeout(500)
            mk = pg.evaluate('MSEL')
            nd = _cal.monthrange(int(mk[:4]), int(mk[5:7]))[1]
            cal_days = pg.locator('#cal .cday:not(.pad)').count()
            cal_off = pg.locator('#cal .cday.off').count()
            real = sum(1 for d in eqj['d'] if d[:7] == mk)
            assert cal_days == nd, '日历格 %d != 当月天数 %d' % (cal_days, nd)
            assert cal_days - cal_off == real, \
                '日历交易日 %d != 曲线里的 %d' % (cal_days - cal_off, real)
            # 🔴 日历格子必须【仍然是一个可点的方格】。
            #   它 2026-09-24 从 `.cd` 改名成 `.cday` —— 原因是 `.cd`
            #   同时还是「名称后的小字代码」（cnCell），两份都没作用域化，
            #   于是这一份的边框与手型光标被加到了**全站每一个小字代码**上。
            #   ★ 这一条与「小字代码不许有边框」那条**分工别记反**：
            #     只钉那一条的话，把这一整块样式删掉也全绿（格子变成一片
            #     裸文字），而那同样不报错。
            _c = pg.evaluate(
                '''() => {const e = document.querySelector('#cal .cday[data-d]');
                   if (!e) return null; const s = getComputedStyle(e);
                   return [parseFloat(s.borderTopWidth), s.cursor,
                           parseFloat(s.minHeight)];}''')
            assert _c and _c[0] >= 1 and _c[1] == 'pointer' and _c[2] >= 20, \
                '日历格子不再是一个可点的方格（边框/光标/高 = %s）—— ' \
                '多半是 .cday 那段样式被删了或又改回 .cd' % (_c,)

            # 点某一天 -> 当日持仓/买卖（读 /api/day，仍是现有归档）
            cd0 = pg.locator('#cal .cday[data-d]').first
            cd0.scroll_into_view_if_needed(); cd0.click(); pg.wait_for_timeout(1200)
            dsel = pg.evaluate('DSEL')
            nh = pg.locator('#d_hold tbody tr').count()
            assert pg.locator('#day .cards .card').count() == 6, '当日概要卡不是 6 张'
            # 🔴 判据是「那一块必须**说清楚状态**」，不是「一定有表」：
            #   `prune_runs.py` 会删掉旧归档的 holdings.parquet，那时页面
            #   给的是「明细已清理（重跑可再生成）」的 .warn —— 显示成空表
            #   才是 bug（会被读成"那天空仓"）。原来只认 `.note` 数量 == 1，
            #   于是归档一被清理这条就失败，而失败的其实是断言本身。
            _dh = pg.locator('#d_hold')
            _pruned = _dh.locator('.warn').count()
            assert _pruned or _dh.locator('.note').count() >= 1, \
                '当日持仓那一块既没有表也没有说明 —— 空白会被读成"那天空仓"'
            if _pruned:
                assert '已清理' in _dh.inner_text(), \
                    '明细被清理时必须明说，实得 %s' % _dh.inner_text()[:60]
            # 浏览器后退必须回到上一年详情页（而不是直接掉出详情）
            pg.go_back(); pg.wait_for_timeout(600)
            h = pg.evaluate('location.hash')
            assert h == base_hash or '/y/' in h, '后退没回到收益明细：%s' % h
            pg.locator('#tabs div').nth(names.index('收益明细')).click()
            pg.wait_for_timeout(400)
            drill = ('年热力 %d 格(%s 收益/回撤/交易日对齐曲线，对比度 %.1f:1)；'
                     '%s 年详情独立路由；%s 日历 %d 格(交易 %d/非交易 %d)；'
                     '%s 当日明细 持仓 %d 只；%s'
                     % (yc.count(), ys[0], cr, yr, mk, cal_days, real, cal_off, dsel, nh, rng))

            # 「‹ 返回目录」那个专属按钮已被顶栏的「📚 回测」入口 + 通用
            # 「‹ 返回」取代 —— 顶栏一处定义，不再每个视图各摆一个返回键
            pg.click('#top a.nav[href="/#/runs"]'); pg.wait_for_timeout(700)
            assert pg.is_visible('#cat'), '从顶栏回不到回测目录'
            pg.go_back(); pg.wait_for_timeout(900)
            assert pg.is_visible('#main'), '浏览器后退未回到详情'
            br.close()
            assert not errs, 'JS 报错 %d 处: %s' % (len(errs), errs[:2])
    finally:
        httpd.shutdown()
    return ('目录树(任意深度/默认收起/按运行时间倒序)/详情/9 页签/返回/后退 全通，'
            '0 JS 错误，表头 gap %dpx；下钻: %s' % (gap, drill))


@case('版本页：简介/源码/参数表单/触发回测', tag='web')
def t_version_page():
    """版本（代码哈希）这一层要能看代码、看可填参数、按参数直接回测。

    ★ 断言源码时【必须排除 #joblog】—— 版本页里日志 pre 排在代码 pre 前面且
      初始隐藏，`#vp pre` 会匹配到它、inner_text 返回空串，于是「有元素」的
      断言假通过。实测踩过一次：报「源码 0 字符」却判为通过。
    """
    try:
        from playwright.sync_api import sync_playwright
    except Exception:                                       # noqa: BLE001
        return '跳过（无 playwright）'
    import threading
    from http.server import ThreadingHTTPServer
    from assay import registry as reg
    from assay import server as sv
    sv._scan()
    # ★ 服务默认只读，网页触发回测是关的。本用例要测的就是「触发回测」
    #   这条链路，所以显式打开；关键是下面那两条「非法参数被挡住」的断言 ——
    #   只读模式下 api_backtest 本来就返回 error，不打开的话它们会
    #   【因为错误的原因通过】，比失败更危险。
    _prev_ab = sv.ALLOW_BACKTEST
    sv.ALLOW_BACKTEST = True
    # 🔴🔴 **归档重定向到临时目录 —— 这条用例会【真的跑一次回测】。**
    #   它点的就是页面上那个「用这个版本跑一次」，而那条链路（`_run_job`）
    #   起的是 `python3 run.py` 子进程、**照常归档**。于是每跑一次 selftest
    #   就往 `runs/` 里塞一条，而且参数区间完全相同 ——
    #   实测积了 **105 次** `2026-06-01~06-30` 的重复归档（用户问"这是什么"
    #   才发现），每天都在涨。
    #   ★ 同 `lv.LIVE` 重定向那条纪律：**selftest 不许写生产数据**。
    #     `registry` 读 `ASSAY_RUNS`，而子进程继承环境变量，所以设它就够。
    #   ★ **不改产品行为**：人在页面上点"跑一次"就是要归档的，
    #     给接口加 `--no-archive` 是修错了地方。
    import glob as _g0
    import shutil as _sh0
    import tempfile as _tf
    _runs_tmp = _tf.mkdtemp(prefix='selftest_runs_')
    #   ★ 临时归档不能是空的 —— 这一页要在目录树里找到版本行。
    #     **只复制结论那几个小文件**（meta/stats/strategy.py/run.log），
    #     不复制 holdings/equity（几十 MB，而这条用例根本不读）。
    #     每个 (分组,策略) 取最近 2 次就够。
    _seed_by = {}
    for _m in sorted(_g0.glob(os.path.join(reg.RUNS, '*/*/*/meta.json'))):
        _pp = _m.split(os.sep)
        _seed_by.setdefault((_pp[-4], _pp[-3]), []).append(_m)
    _n_seed = 0
    for (_grp, _st), _ms in _seed_by.items():
        for _m in _ms[-2:]:
            _src = os.path.dirname(_m)
            _dst = os.path.join(_runs_tmp, _grp, _st, os.path.basename(_src))
            os.makedirs(_dst, exist_ok=True)
            for _f in ('meta.json', 'stats.json', 'strategy.py', 'run.log'):
                _sp = os.path.join(_src, _f)
                if os.path.isfile(_sp):
                    _sh0.copy2(_sp, os.path.join(_dst, _f))
            _n_seed += 1
    assert _n_seed > 0, '没有可复制的归档样本 —— 这条用例需要归档里有数据'
    _prev_runs_env = os.environ.get('ASSAY_RUNS')
    os.environ['ASSAY_RUNS'] = _runs_tmp
    _prev_runs = reg.RUNS
    reg.set_runs(_runs_tmp)
    sv._scan()                       # 🔴 重扫 —— 索引是启动时建的，不重扫读到的还是真实归档
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), sv.Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as p:
            try:
                br = p.chromium.launch()
            except Exception as e:                          # noqa: BLE001
                return '跳过（浏览器不可用: %s）' % type(e).__name__
            pg = br.new_page(viewport={'width': 1500, 'height': 900})
            errs = []
            pg.on('pageerror', lambda e: errs.append(str(e)))
            pg.on('console',
                  lambda m: errs.append('console: ' + m.text) if m.type == 'error' else None)
            # 回测归档已移到 #/runs —— `/` 现在是总览首页
            pg.goto('http://127.0.0.1:%d/#/runs' % port, wait_until='networkidle')
            pg.wait_for_timeout(800)
            # 过滤会自动展开，直接抵达版本层
            pg.fill('#filter', '红利'); pg.wait_for_timeout(800)
            # ★ 等版本行真的出现再断言 —— 归档现在是**临时目录**（本用例
            #   会真跑一次回测，不重定向就会污染 runs/），条数比真实归档少，
            #   渲染快但仍是异步的；固定 800ms 在机器忙时不够。
            try:
                pg.wait_for_selector('.vnote', timeout=15000)
            except Exception:                                   # noqa: BLE001
                pass
            n_note = pg.locator('.vnote').count()
            assert n_note > 0, ('版本行没有一句话简介（归档 %d 条）'
                                % len(pg.evaluate('() => RUNS || []')))
            note0 = pg.locator('.vnote').first.inner_text().strip()
            assert note0 and note0 != '（未填简介）', '简介为空: %r' % note0

            # 版本行不该再有「N 次回测 / 年化 x~y / 最近」
            bad = pg.evaluate("""()=>[...document.querySelectorAll('.nd')]
              .filter(r=>{const ic=r.querySelector('.ic');
                          return ic && ic.textContent.trim()==='\u2317';})
              .map(r=>r.innerText).filter(t=>/次回测|最近|年化/.test(t))""")
            assert not bad, '版本行仍有旧统计: %s' % bad[:1]

            assert pg.locator('.vopen[data-ver]').count() > 0, '没有版本入口'
            pg.locator('.vopen[data-ver]').first.click(); pg.wait_for_timeout(1500)
            assert '#/ver/' in pg.evaluate('location.hash'), '未进入版本页'
            assert pg.is_visible('#vp') and not pg.is_visible('#cat'), '视图未切换'

            # ★ 排除 joblog 后再断言源码非空
            lens = pg.eval_on_selector_all(
                '#vp pre', "els=>els.filter(e=>e.id!=='joblog').map(e=>e.textContent.length)")
            assert lens and min(lens) > 500, '版本页源码为空: %s' % lens
            n_param = pg.locator('#vp input[data-p]').count()
            assert n_param > 0, '没有渲染可填参数'
            for sel in ('#f_start', '#f_end', '#f_cash', '#f_run'):
                assert pg.locator(sel).count() == 1, '缺少 %s' % sel

            # 参数表单必须和引擎认可的参数一致 —— 否则填了会被引擎拒
            names = set(pg.eval_on_selector_all(
                '#vp input[data-p]', 'els=>els.map(e=>e.dataset.p)'))
            sha = pg.evaluate('location.hash').split('/')[-1]
            info = sv.api_version({'sha': sha})
            eng = {x['name'] for x in (info.get('current_params') or [])}
            assert names == eng, '表单参数与解析结果不一致: %s' % (names ^ eng)

            # 非法参数必须被服务端挡住（不经 shell，且名字白名单）
            r = sv.api_backtest({}, {'sha': sha, 'params': {'__nope__': 1}})
            assert 'error' in r, '未知参数名没被挡住'
            r = sv.api_backtest({}, {'sha': sha, 'params':
                                     {sorted(eng)[0]: '1; rm -rf /'}})
            assert 'error' in r, '危险参数值没被挡住'

            # 只读模式（默认）必须挡住触发回测，且不能只靠前端置灰
            sv.ALLOW_BACKTEST = False
            r = sv.api_backtest({}, {'sha': sha, 'params': {}})
            assert 'error' in r and '只读' in r['error'], \
                '只读模式没挡住 /api/backtest: %s' % r
            v_ro = sv.api_version({'sha': sha})
            assert v_ro['readonly'] and not v_ro['runnable'], \
                '只读模式下 api_version 仍报 runnable'
            sv.ALLOW_BACKTEST = True

            pg.click('#top a.nav[href="/#/runs"]'); pg.wait_for_timeout(700)
            assert pg.is_visible('#cat'), '从顶栏回不到回测目录'
            br.close()
            assert not errs, 'JS 报错 %d 处: %s' % (len(errs), errs[:2])
    finally:
        httpd.shutdown()
        sv.ALLOW_BACKTEST = _prev_ab
        # 归档目录还原 + 清掉临时的。★ 顺序：先还原再删，
        #   删失败也不该让后面的用例跑在临时目录上。
        if _prev_runs_env is None:
            os.environ.pop('ASSAY_RUNS', None)
        else:
            os.environ['ASSAY_RUNS'] = _prev_runs_env
        reg.set_runs(_prev_runs)
        import shutil as _sh
        _sh.rmtree(_runs_tmp, ignore_errors=True)
    return ('简介 %d 条/旧统计已移除/源码 %d 字符/参数 %d 个与引擎一致/'
            '非法参数名与危险值均被拦/只读模式拦住触发回测'
            % (n_note, min(lens), n_param))


@case('选中标记：打星 / 冒泡 / 不误触发', tag='web')
def _():
    """★ 打在【单次回测】上，因为 run 才记录了策略+参数+区间+成本+数据指纹，
    构成一条完整的「规则」；策略文件或代码版本都不够 —— 同一版本换个参数
    就是另一条规则（froec 的 kcb_688_only=0 与默认值是两条）。

    三个容易错的点，都要真浏览器才测得出来：
      1) 星标要【向上冒泡】到版本/文件/目录三层，否则折叠状态下看不见，
         「打标记方便找」的目的就落空了。
      2) 点星不能连带打开回测详情页 —— 行本身有 onclick，必须 stopPropagation。
      3) 标记文件里会残留【已删除归档】的 run_id（清理归档不同步删标记），
         /api/marks 必须过滤掉，否则前端渲染出点不开的空行。
    ★ 用完恢复标记文件：测试不该改动用户真实的选中状态。
    """
    import json
    import shutil
    import threading
    from http.server import ThreadingHTTPServer

    from assay import server as sv
    runs = sv.api_runs({})
    if not runs:
        return '跳过（归档为空）'

    # 🔴🔴 **重定向，不是"备份再还原"。** 这条用例真的会 `api_mark` 打星，
    #   原来的做法是"备份真 picks.json -> 写 -> finally 还原"，而
    #   ① 中断一次就丢数据（2026-09-21 实测：真文件少了 66 行星标）；
    #   ② 它跑的那十几秒里**人可能正在页面上点星**，还原就把人的改动盖掉
    #     （2026-09-22 实测：框架层那条守卫就这么把用户刚点的星回滚了）。
    #   正确做法与 `lv.LIVE` / `ASSAY_RUNS` 同一条：把模块里那个路径指到
    #   临时目录，**真文件一个字节都不写**（同下面两条批量删除用例）。
    import assay.srv.runs as R
    import tempfile as _tf0
    _marks_p = os.path.join(_tf0.mkdtemp(prefix='selftest_marks_'), 'picks.json')
    if os.path.isfile(R.MARKS_FILE):
        shutil.copy2(R.MARKS_FILE, _marks_p)   # 拿真内容当底子（星标冒泡要有数据）
    _prev_marks = R.MARKS_FILE
    R.MARKS_FILE = _marks_p
    try:
        # --- 服务端：非法输入必须拒掉，不能静默写进去 ---
        rid = runs[0]['run_id']
        assert sv.api_mark({}, {'run_id': '../../etc/passwd', 'mark': 'star'}).get('error'), \
            '目录穿越的 run_id 未被拒绝'
        assert sv.api_mark({}, {'run_id': rid, 'mark': '<script>'}).get('error'), \
            '未知标记类型未被拒绝'
        assert sv.api_mark({}, {'run_id': '99999999-000000-000000',
                                'mark': 'star'}).get('error'), '不存在的 run_id 未被拒绝'
        # 控制字符要被剥掉（备注直接渲染进页面）
        r = sv.api_mark({}, {'run_id': rid, 'mark': 'star', 'note': 'a\x00\x07b'})
        assert r.get('ok') and '\x00' not in r['note'] and '\x07' not in r['note'], \
            '备注里的控制字符没被剥掉: %r' % r.get('note')
        # 残留 run_id 要被 /api/marks 过滤
        d = sv._load_marks()
        d['20200101-000000-deadbe'] = {'mark': 'star', 'note': '已删归档', 'ts': ''}
        sv._save_marks(d)
        ids = {m['run_id'] for m in sv.api_marks({})}
        assert '20200101-000000-deadbe' not in ids, '已删归档的残留标记未被过滤'
        assert rid in ids, '刚打的标记没出现在 /api/marks'
        d.pop('20200101-000000-deadbe'); sv._save_marks(d)

        # --- 浏览器：渲染 + 冒泡 + 点击语义 ---
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return '服务端 5 项通过；浏览器部分跳过（无 playwright）'
        httpd = ThreadingHTTPServer(('127.0.0.1', 0), sv.Handler)
        port = httpd.server_address[1]
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            with sync_playwright() as pw:
                try:
                    br = pw.chromium.launch()
                except Exception as e:                      # noqa: BLE001
                    return '服务端 5 项通过；浏览器不可用(%s)' % type(e).__name__
                pg = br.new_page(viewport={'width': 1500, 'height': 900})
                errs = []
                pg.on('pageerror', lambda e: errs.append(str(e)))
                pg.on('console', lambda m: errs.append('console: ' + m.text)
                      if m.type == 'error' else None)
                pg.on('dialog', lambda dl: dl.accept('selftest 备注'))
                pg.goto('http://127.0.0.1:%d/#/runs' % port, wait_until='networkidle')
                pg.wait_for_timeout(900)

                assert pg.locator('#pick').count() == 1, '顶部「选中的规则」面板没渲染'
                npick = pg.locator('#pick tr[data-go]').count()
                assert npick >= 1, '「选中的规则」面板里没有行'
                # 冒泡：至少有一个收起的顶层节点带 ★ 计数
                assert pg.locator('.nd.d0 .stcnt').count() >= 1, \
                    '星标没有冒泡到顶层目录节点（折叠时将看不见）'

                # 用「全部展开」把所有回测露出来 —— 逐层点会停在某个只含
                # 已打星行的子树上，导致找不到可点的目标（实测踩过）。
                pg.click('#expand'); pg.wait_for_timeout(900)
                st = pg.locator('.runs tbody tr:not(.marked) .st').first
                assert st.count(), '找不到未打星的回测行'
                before = pg.locator('#pick tr[data-go]').count()
                st.click(); pg.wait_for_timeout(700)
                # 点星不能跳转
                assert not pg.url.rstrip('/').endswith('#/run') and '#/run/' not in pg.url, \
                    '点击星标误触发了跳转到回测详情页（stopPropagation 失效）'
                after = pg.locator('#pick tr[data-go]').count()
                assert after == before + 1, \
                    '打星后「选中的规则」没增加（%d -> %d）' % (before, after)

                # --- 独立索引页 #/picks ---
                # 目录页顶部那条只列关键几列；这一页是完整视图，且必须显式列出
                # 【成本口径】—— 本项目两次因为拿滑点 0 的数字去比含滑点的基准
                # 而得出错误结论（FROEC 与 v0b 各一次）。
                assert pg.locator('#top a[href="#/picks"]').count() == 1, \
                    '顶栏缺少「★ 选中的规则」入口'
                pg.click('#top a[href="#/picks"]'); pg.wait_for_timeout(600)
                assert pg.url.endswith('#/picks'), '点入口没进 #/picks: %s' % pg.url
                assert pg.locator('#pk').count() == 1, '索引页没渲染'
                # ★ 一行一条规则的【对比表】，不是一条一张卡。
                #   卡片横排的毛病不是挤，是**没法纵向扫**：每张卡各自排版，
                #   同一个指标在不同卡里横坐标都不一样，10 条规则根本比不了。
                ncard = pg.locator('#pk tr.rw').count()
                assert ncard == after, \
                    '索引页行数 %d 与标星数 %d 不一致' % (ncard, after)
                assert pg.locator('#pk .card2').count() == 0, \
                    '还在用卡片横排 —— 应该是对比表'
                assert not pg.is_visible('#cat'), '进索引页后目录页应隐藏'
                # 🔴 **取消选中就在这一页上**（用户 2026-09-21：「选中的规则
                #   无法直接在上面取消，还要到下面找到对应的规则再取消」）。
                #   这一页列的就是选中的那些 —— 取消的入口不在这儿，等于
                #   让人回归档目录、展开那棵树、再找到那一行。
                nst = pg.locator('#pk tr.rw .st[data-mk]').count()
                assert nst == ncard, \
                    '索引页 %d 行却只有 %d 个 ★ —— 取消入口不全' % (ncard, nst)
                _one = pg.locator('#pk tr.rw .st[data-mk]').first
                _rid = _one.get_attribute('data-mk')
                assert _rid in sv._load_marks(), \
                    '构造不对：这一行本来就没被标星，下面那条是空转的'
                _one.click()
                pg.wait_for_function(
                    'n => document.querySelectorAll("#pk tr.rw").length === n',
                    arg=ncard - 1, timeout=15000)
                assert _rid not in sv._load_marks(), \
                    '页面上取消了，标记文件里还留着 %s' % _rid
                # 🔴 取消之后那一行要**当场消失** —— 留着一行已经不选中的在
                #   这一页上，人会以为没点动（同「点了没反应是最难查的那种坏」）
                assert pg.locator('#pk tr.rw .st[data-mk="%s"]' % _rid).count() == 0, \
                    '取消之后那一行还在这一页上'
                # 恢复（这条用例后面还要按 after 算行数）
                sv.api_mark({}, {'run_id': _rid, 'mark': 'star', 'note': 'selftest'})
                pg.reload(wait_until='networkidle'); pg.wait_for_timeout(700)
                txt = pg.locator('#pk').inner_text()
                assert '滑点' in txt, '索引页没有列出成本口径（滑点）'
                # 🔴 表头列数必须等于每行的单元格数 —— 表头 8 列配 9 个数据
                #    是横排改表格时最典型的错，而它不报错，只是所有列错位一格
                # 🔴 **一张表**装所有分组 —— 一组一张 table 时列宽各算各的，
                #    上下两个表的"年化"列对不齐，而这一页存在的唯一理由就是
                #    纵向比较。分组是表内的一条带子行。
                assert pg.locator('#pk table.pkt').count() == 1, \
                    '有 %d 张表 —— 分组各一张时列宽各算各的，上下对不齐' \
                    % pg.locator('#pk table.pkt').count()
                assert pg.locator('#pk tr.grp').count() >= 1, \
                    '分组没渲染成表内的带子行'
                # 列宽真的一致：同一列在不同分组下的左边界必须相同
                _x = pg.evaluate(
                    "() => [...document.querySelectorAll('#pk tr.rw')]"
                    ".map(tr => Math.round("
                    "tr.children[2].getBoundingClientRect().left))")
                assert len(set(_x)) == 1, \
                    '同一列在不同行的左边界不一致 %s —— 列没对齐' % sorted(set(_x))
                _th = pg.locator('#pk table.pkt tr').first.locator('th').count()
                _td = pg.locator('#pk table.pkt tr.rw').first.locator('td').count()
                assert _th == _td, '表头 %d 列 vs 每行 %d 格 —— 会整表错位' % (_th, _td)
                # 🔴 **不写死列数** —— 2026-09-17 加了「⚖ 比对」勾选列，写死的 10
                #   当场把这条打挂了，而**挂的是断言不是产品**（同那条
                #   `#top .btn.nav >= 8` 改成与 `NAV.length` 比）。
                #   真正要钉的是：① 表头与数据格数相等（上面那条，防整表错位）；
                #   ② 该有的列**按名字**都在（列序是产品决定、会变）。
                _need = ['策略', '区间', '年化', '回撤', '夏普', '超额年化',
                         '信息比率', '年换手', '交易', '胜率']
                _thz0 = ' '.join(pg.locator('#pk table.pkt th').all_inner_texts())
                _miss = [x for x in _need if x not in _thz0]
                assert not _miss, '表头少了这几列：%s（现有：%s）' % (_miss, _thz0)
                assert pg.locator('#pk table.pkt tr.rw').first.locator(
                    'td.cmpck input[data-cmp]').count() == 1, \
                    '每行该有一个「⚖ 比对」勾选框'
                _thz = ' '.join(pg.locator('#pk table.pkt th').all_inner_texts())
                # 本金与成本口径不占列（太挤）—— 它们进行的 tooltip
                assert '本金' not in _thz and '成本' not in _thz, \
                    '本金/成本口径不该再占列：%s' % _thz
                _tip = pg.locator('#pk tr.rw').first.get_attribute('title') or ''
                assert '本金' in _tip and '滑点' in _tip, \
                    '本金与成本口径要进 tooltip，不能直接丢掉：%r' % _tip
                # 参数要能【换行】：第一列有宽度上限且不 nowrap，
                # 否则十几个参数横着排会把指标列挤到屏幕外
                _ws = pg.evaluate(
                    "() => { const e=document.querySelector('#pk tr.rw td.nmc');"
                    " const s=getComputedStyle(e);"
                    " return [s.whiteSpace, s.maxWidth]; }")
                assert _ws[0] != 'nowrap', '参数列还是 nowrap —— 不会换行'
                assert _ws[1] != 'none', '参数列没有宽度上限，会被参数撑爆'
                # 数字列要能纵向对齐（等宽数字），否则位数一错开就没法扫
                _tn = pg.evaluate(
                    "() => getComputedStyle(document.querySelector("
                    "'#pk table.pkt tr.rw td:nth-child(3)'))"
                    ".fontVariantNumeric")
                assert 'tabular-nums' in (_tn or ''), \
                    '数字列没用等宽数字（%s）—— 位数对不齐扫起来就废了' % _tn
                # 宽表自己横向滚，页面 body 不许出现横向滚动条
                _ovf = pg.evaluate(
                    "() => getComputedStyle(document.querySelector('#pk .pw'))"
                    ".overflowX")
                assert _ovf in ('auto', 'scroll'), \
                    '宽表没自己横向滚（overflow-x=%s），会把页面撑横滚' % _ovf
                # ---- 点表头排序 ----
                # ★ 排序是【组内】做的（业务域是有意义的归拢，跨组比较本来
                #   就不该发生：区间和基准都可能不同）。所以判据是"每个组内
                #   单调"，不是"整列反过来" —— 后者跨组读必然不单调，
                #   我第一版就是这么写错的。
                def _bygrp(col=3):
                    out = []
                    gs = pg.locator('#pk .pg')
                    for i in range(gs.count()):
                        out.append([
                            float(x.strip().rstrip('%').replace(',', ''))
                            for x in gs.nth(i).locator(
                                'tr.rw td:nth-child(%d)' % col).all_inner_texts()
                            if x.strip() not in ('', '—')])
                    return out

                def _mono(vs, desc):
                    return all((vs[i] >= vs[i + 1]) if desc else (vs[i] <= vs[i + 1])
                               for i in range(len(vs) - 1))
                _h = pg.locator('#pk table.pkt th[data-sk="annual_return"]').first
                assert '▼' in _h.inner_text(), '默认应按年化降序，表头要标出方向'
                for gi, vs in enumerate(_bygrp()):
                    assert _mono(vs, True), \
                        '第 %d 组的年化没按降序排：%s' % (gi + 1, vs)
                _h.click(); pg.wait_for_timeout(500)
                assert '▲' in pg.locator(
                    '#pk table.pkt th[data-sk="annual_return"]').first.inner_text(), \
                    '再点一次应翻成升序'
                for gi, vs in enumerate(_bygrp()):
                    assert _mono(vs, False), \
                        '翻向后第 %d 组没变升序：%s' % (gi + 1, vs)
                # 换列时的默认方向要按【哪边更好】给：回撤是升序
                pg.locator('#pk table.pkt th[data-sk="max_drawdown"]').first.click()
                pg.wait_for_timeout(500)
                assert '▲' in pg.locator(
                    '#pk table.pkt th[data-sk="max_drawdown"]').first.inner_text(), \
                    '按回撤排序应默认升序 —— 一律降序会把最差的排最前面'
                # 点行进详情，再后退回索引页
                pg.locator('#pk tr.rw').first.click(); pg.wait_for_timeout(700)
                assert '#/run/' in pg.url, '点行没进回测详情: %s' % pg.url
                pg.go_back(); pg.wait_for_timeout(600)
                assert pg.url.endswith('#/picks'), '后退没回到索引页: %s' % pg.url

                assert not errs, '页面报错: %s' % errs[:3]
                br.close()
        finally:
            httpd.shutdown()
        return ('服务端 5 项 + 浏览器 11 项通过；%d 条选中规则，星标冒泡到顶层，'
                '独立索引页 #/picks 是对比表（单表 %d 列、分组为带子行、'
                '同列左边界一致、参数可换行、本金与成本入 tooltip、'
                '等宽数字、宽表自滚、点表头排序且回撤默认升序）' % (npick, _th))
    finally:
        R.MARKS_FILE = _prev_marks
        shutil.rmtree(os.path.dirname(_marks_p), ignore_errors=True)


@case('页签图标：能取到 / mimetype 对 / 浏览器真的用了它', tag='fast')
def t_favicon():
    """★ favicon 坏了【不会有任何报错】—— 浏览器默默回落成默认图标，
    而你只会觉得"好像一直是这样"。所以这条用例核三件事：

      1. 文件在、能通过 HTTP 取到、是合法 XML
      2. mimetype 是 image/svg+xml（猜错的话浏览器当文本渲染，图标空白）
      3. HTML 里的 <link rel=icon> 指向它，且兜底的 data URI 也是有效 SVG

    另外核配色与 :root 一致 —— 改主题时容易只改 CSS、忘了图标。
    """
    import threading
    import urllib.request
    import xml.dom.minidom
    from http.server import ThreadingHTTPServer

    from assay import server as sv

    web = os.path.join(REPO, 'web')
    p = os.path.join(web, 'favicon.svg')
    assert os.path.isfile(p), '缺 web/favicon.svg'
    raw = open(p, encoding='utf-8').read()
    xml.dom.minidom.parseString(raw)          # 合法 XML（注释里不能有 --）

    html = open(os.path.join(web, 'index.html'), encoding='utf-8').read()
    # 样式已搬到 common.css（多个独立页面共用），所以 head 到 <link> 为止、
    # 配色从 common.css 里读 —— 原来两者都在 index.html 的 <style> 里
    head = html[:html.index('<div id="app">')]
    assert 'rel="icon"' in head and '/favicon.svg' in head, \
        'index.html 的 <head> 里没有指向 favicon.svg 的 <link rel=icon>'
    # 兜底 data URI 也要是有效 SVG（Safari 16 以下不认 SVG favicon 文件）
    m = re.search(r'href="data:image/svg\+xml,([^"]+)"', head)
    assert m, '缺兜底的 data URI 图标'
    import urllib.parse
    xml.dom.minidom.parseString(urllib.parse.unquote(m.group(1)))

    # 配色必须与 :root 一致 —— 改主题时最容易漏掉图标
    css = open(os.path.join(web, 'shared', 'common.css'), encoding='utf-8').read()
    for name in ('--accent', '--up'):
        mm = re.search(re.escape(name) + r':\s*(#[0-9a-fA-F]{3,8})', css)
        assert mm, 'CSS 里找不到 %s' % name
        hexv = mm.group(1).lower()
        assert hexv in raw.lower(), \
            ('图标没用 :root 的 %s (%s) —— 主题改了但图标没跟上' % (name, hexv))

    httpd = ThreadingHTTPServer(('127.0.0.1', 0), sv.Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        r = urllib.request.urlopen('http://127.0.0.1:%d/favicon.svg' % port,
                                   timeout=10)
        ct = r.headers['Content-Type'] or ''
        body = r.read()
        assert r.status == 200, '/favicon.svg 返回 %d' % r.status
        assert ct.startswith('image/svg+xml'), \
            'mimetype 是 %r —— 浏览器会当文本渲染，图标空白' % ct
        assert len(body) == len(raw.encode()), '取到的内容与磁盘不一致'
    finally:
        httpd.shutdown()

    n_pt = raw.count(' L')
    return ('favicon.svg %d bytes / %s / 曲线 %d 段（含回撤）/ '
            '兜底 data URI 有效 / 配色与 :root 一致'
            % (len(body), ct, n_pt // 2))


@case('启动开关：默认全功能 / --readonly / 按钮不许 disabled', tag='fast')
def t_serve_flags():
    """2026-09-04：废掉 `--live` 与 `--allow-backtest` 两个默认关的开关。

    🔴 起因是一个真实的坏：忘了加 `--allow-backtest` 时，网页上
      「用这个版本+参数跑一次」渲染成 **disabled** ——
      **disabled 的元素连 title 提示都不触发**，所以用户看到的是
      "选了策略、填了日期、点下去毫无反应，也没有任何说明"。
      （同 backLink 那条：给一个点了没反应的按钮比不给更糟。）
    """
    import re
    here = REPO
    src = open(os.path.join(here, 'serve.py'), encoding='utf-8').read()

    # ---- ① 默认全功能，只有 --readonly 才关 ----
    assert '--readonly' in src, '没有 --readonly'
    assert 'full = not a.readonly' in src, '默认应当全开，只有 --readonly 才关'
    assert 'allow_backtest=full' in src and 'allow_live=full' in src, \
        '两个能力应当由同一个 --readonly 一起决定'
    # ★ 旧命令必须还能用 —— launchd/文档/肌肉记忆里都有，
    #   而"参数不认"会让服务直接起不来，那是最糟的失败方式。
    for f in ('--live', '--allow-backtest'):
        assert "'%s'" % f in src, '旧参数 %s 应保留兼容' % f

    # ---- ② 真起两次服务，验能力开关 ----
    import threading
    from http.server import ThreadingHTTPServer

    from assay import server as sv
    old_ab, old_lv = sv.ALLOW_BACKTEST, sv.ALLOW_LIVE
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), sv.Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        import json as _j
        import urllib.request as _u

        def acc():
            return _j.load(_u.urlopen('http://127.0.0.1:%d/api/live/accounts'
                                      % port, timeout=30))
        sv.ALLOW_BACKTEST = sv.ALLOW_LIVE = True
        a = acc()
        assert a['can_backtest'] is True and a['readonly'] is False, \
            '全功能时两项都该开：%s' % {k: a[k] for k in ('can_backtest', 'readonly')}
        sv.ALLOW_BACKTEST = sv.ALLOW_LIVE = False
        b = acc()
        assert b['can_backtest'] is False and b['readonly'] is True, \
            '--readonly 时两项都该关：%s' % {k: b[k] for k in ('can_backtest', 'readonly')}
    finally:
        httpd.shutdown()
        sv.ALLOW_BACKTEST, sv.ALLOW_LIVE = old_ab, old_lv

    # ---- ③ 🔴 页面上不许再出现 disabled 的功能按钮 ----
    #   判据用**源码**扫：`disabled` 只允许出现在"只读模式下不许写"那类
    #   （录成交、改设置），不允许挂在"功能没开"的按钮上 ——
    #   后者应当可点 + 说原因。
    # ★ 实盘的视图逻辑在 web/views/live.js（index.html 已拆成骨架 + 域文件）——
    #   断言要跟着代码走，读 index.html 会永远查不到而假过（或假失败）。
    lvjs = open(os.path.join(here, 'web', 'views', 'live.js'),
                encoding='utf-8').read()
    bad = re.findall(r'\$\{\s*LV\.can_backtest\s*\?[^}]*disabled', lvjs)
    assert not bad, \
        ('「跑一次」那个按钮又按 can_backtest 设 disabled 了 —— '
         'disabled 的元素连 title 都不触发，点了没反应且看不到原因：%s' % bad)
    # 不可用时，怎么开必须【常驻可见】而不是藏在 title 里
    assert 'readonly' in lvjs and 'serve.py' in lvjs, \
        'live.js 里没有"怎么开"的常驻说明'
    return ('默认全功能、--readonly 一起关两项、旧参数保留兼容；'
            'API 两种模式的 can_backtest/readonly 都对；'
            '页面不再按 can_backtest 设 disabled（点了没反应且无提示那条）')


@case('顶栏合并：板块收进盘面 / 对比收进个股，且【一个功能都没藏起来】（playwright）',
       tag='web')
def t_nav_merge():
    """2026-09-14 用户："上方的按钮做一些合并。对比功能放到个股中，板块的功能
    放到盘面中（确认是否已经包含了，如果已经包含则不需要了）。"

    先回答那个"是否已经包含"：**没有**。盘面只有「行业涨幅（申万一级）」
    一张表，而板块页是 **5 类 926 个**（申万 + 通达信 概念/风格/地区/研究）
    带成分股穿透 —— 盘面只覆盖了其中申万那一类。

    🔴 **合并的风险不是少两个按钮，是把功能藏起来。** 合并前通达信那 657 个
      （概念 269 / 风格 158 / 地区 32 / 研究 467）**只能从顶栏进**；顶栏一撤，
      它们就再也没有入口了 —— 而那不报错，只是从此没人找得到
      （同「独立页面最大的风险不是单页坏，是页面之间断链」那条）。
      所以这条用例的核心判据是：**服务端说有几类，盘面就得链得到几类**
      —— 照清单写死的话，将来加一类不会有人发现（同「断言直接扫目录
      而不是照清单拼」那条）。

    ★ 两个页面都还在（书签与深链接是产品契约），只是不在顶栏；
      进去之后顶栏点亮**父级**，否则"我在哪"没有任何指示。
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return '跳过（无 playwright）'
    import threading
    from assay import server as sv
    from http.server import ThreadingHTTPServer
    old_live = sv.ALLOW_LIVE
    sv.ALLOW_LIVE = True
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), sv.Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = 'http://127.0.0.1:%d' % port
    notes = []
    try:
        # 服务端先自证：到底有几类板块（判据的来源，不写死）
        import json as _json
        import urllib.request as _u
        ks = _json.load(_u.urlopen(base + '/api/sector/kinds'))['kinds']
        want = sorted(k['kind'] for k in ks)
        assert len(want) >= 4, '板块类别只有 %d 类？判据没意义了' % len(want)

        with sync_playwright() as pw:
            br = pw.chromium.launch()
            pg = br.new_page(viewport={'width': 1440, 'height': 900})
            errs = []
            pg.on('pageerror', lambda e: errs.append(str(e)))

            # ---------- ① 顶栏 ----------
            pg.goto(base + '/market.html', wait_until='networkidle')
            pg.wait_for_selector('.lvsec', timeout=60000)
            navs = pg.locator('#top a.nav').all_inner_texts()
            assert len(navs) == 7, '顶栏该是 7 个入口：%s' % navs
            j0 = ' '.join(navs)
            assert '板块' not in j0 and '对比' not in j0, \
                '顶栏还留着板块/对比：%s' % navs

            # ---------- ② 盘面必须链得到【全部】板块类别 ----------
            got = pg.evaluate("""() => [...new Set(
                [...document.querySelectorAll('a[href*="sector.html"]')]
                  .map(a => new URL(a.href).searchParams.get('kind'))
                  .filter(Boolean))]""")
            missing = [k for k in want if k not in got]
            assert not missing, \
                ('盘面上走不到这几类板块：%s（服务端说有 %s）—— 顶栏撤掉之后'
                 '它们就**再也没有入口**了，而那不报错，只是从此没人找得到'
                 % (missing, want))
            n_chip = pg.locator('.lvsec a.chip[href*="sector.html"][href*="kind="]').count()
            assert n_chip >= len(want) - 1, \
                '盘面上的板块类别 chip 太少（%d 个）' % n_chip
            notes.append('盘面链得到全部 %d 类板块（服务端清单为准，不写死）'
                         % len(want))

            # ---------- ③ 板块页仍可直达，且顶栏点亮父级 ----------
            pg.goto(base + '/sector.html?kind=concept', wait_until='networkidle')
            pg.wait_for_timeout(1500)
            on = pg.locator('#top a.nav.on')
            assert on.count() == 1 and '盘面' in on.inner_text(), \
                ('板块页的顶栏该点亮父级「🌡 盘面」，实得 %s —— NAV 里已经没有'
                 '它自己那一项，传旧 key 的话一个都不亮，"我在哪"没有指示'
                 % (on.all_inner_texts()))
            assert '板块' in pg.locator('#top h1').inner_text(), \
                '板块页的标题该仍是它自己（亮的是所属组，写的是它是什么）'
            assert pg.locator('table').count() >= 1, '板块页没渲染出来'

            # ---------- ④ 个股的对比入口必须【带上当前这只票】 ----------
            pg.goto(base + '/stock.html?code=601857.XSHG', wait_until='networkidle')
            pg.wait_for_selector('#cmp', timeout=60000)
            href = pg.locator('#cmp').get_attribute('href')
            assert 'codes=' in href and '601857' in href, \
                ('个股页的对比入口没带当前这只票（href=%s）—— 对比天然是'
                 '"拿【这只】和别的比"，链到裸页面等于到了那边还要再搜一遍，'
                 '那是把入口做成了摆设' % href)
            #   点过去要真的画出这只票的曲线（不是只改了 href）
            pg.locator('#cmp').click()
            pg.wait_for_timeout(2500)
            assert 'compare.html' in pg.url, '点对比入口没跳到对比页：%s' % pg.url
            px = pg.evaluate("""() => {
              const c = document.querySelector('canvas');
              if(!c) return 0;
              const x = c.getContext('2d').getImageData(0,0,c.width,c.height).data;
              let n = 0; for(let i=3;i<x.length;i+=4) if(x[i]) n++;
              return n;}""")
            assert px > 500, \
                ('对比页没画出曲线（非透明像素 %d）—— 带着代码跳过去却是'
                 '一张空图，等于入口没通' % px)
            on2 = pg.locator('#top a.nav.on')
            assert on2.count() == 1 and '个股' in on2.inner_text(), \
                '对比页的顶栏该点亮父级「📈 个股」，实得 %s' % on2.all_inner_texts()
            notes.append('个股 -> 对比带着代码过去并真的画出曲线（%d 像素）' % px)
            notes.append('两个子页仍可直达、顶栏点亮父级')
            assert not errs, 'JS 报错：%s' % errs[:3]
            br.close()
    finally:
        sv.ALLOW_LIVE = old_live
        httpd.shutdown()
    return '；'.join(notes)


@case('总览首页 / 顶栏分组 / 数据页签（playwright）', tag='web')
def t_home_ui():
    """信息架构那一层的自证。

    ★ 打开看板第一眼该回答「今天什么状态、要做什么」—— 而不是一棵回测
      目录树（那是做策略时才进的，已移到 #/runs）。
    ★ 顶栏按【使用频率】分组，不是平铺：平铺时每次都要在 8 个里扫一遍
      才找到要去的地方，而它们的重要性差很远。
    ★ 首页每块只给【摘要 + 一个入口】，不重复做那一页的事 ——
      首页做成小型全功能页的话，同一份数据两处渲染，迟早不一致。
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return '跳过（无 playwright）'
    import threading
    from http.server import ThreadingHTTPServer

    from assay import server as sv
    old_live = sv.ALLOW_LIVE
    sv.ALLOW_LIVE = True
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), sv.Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = 'http://127.0.0.1:%d' % port
    try:
        with sync_playwright() as p:
            try:
                br = p.chromium.launch()
            except Exception as e:                          # noqa: BLE001
                return '跳过（浏览器不可用: %s）' % type(e).__name__
            pg = br.new_page(viewport={'width': 1600, 'height': 1100})
            errs = []
            pg.on('pageerror', lambda e: errs.append(str(e)))
            pg.on('console',
                  lambda m: errs.append('console: ' + m.text) if m.type == 'error' else None)

            # ---------------- 首页 ----------------
            pg.goto(base + '/', wait_until='networkidle')
            pg.wait_for_selector('#main .lvsec', timeout=60000)
            pg.wait_for_timeout(2500)
            assert '总览' in pg.title(), '首页标题不对：%s' % pg.title()
            # ★ 打开看板第一眼不该是回测目录树
            assert not pg.is_visible('#cat'), '首页居然显示的是回测目录树'
            secs = [x.split('\n')[0] for x in
                    pg.locator('#main .lvsec h3').all_inner_texts()]
            for k in ('实盘', '盘面', '买点', '自选', '数据'):
                assert any(k in x for x in secs), '首页缺「%s」这一块：%s' % (k, secs)
            body = pg.locator('#main').inner_text()
            assert 'undefined' not in body and 'NaN' not in body, \
                '首页有 undefined/NaN'
            # 每块的标题本身就是入口（首页只给摘要）
            for href in ('#/live', '/market.html', '/watchlist.html',
                         '/alerts.html', '#/sync'):
                assert pg.locator('#main .lvsec h3 a[href="%s"]' % href).count() >= 1, \
                    '「%s」那块的标题不是入口' % href
            # 站名在首页要点亮 —— 否则"我在哪"没有指示
            assert pg.locator('#top h1 a.homeon').count() == 1, \
                '首页没把站名点亮'
            # 实盘那块必须真的有账户与数字（不是空壳）
            # ⚠ 这条原来钉的是「持仓浮盈」这个列名，而 2026-09-19 用户要求
            #   **把它替换成当日盈亏**（首页问的是"今天怎么样"，累计浮盈是
            #   复盘用的）。**失败的是断言不是产品** —— 但它原本要保的
            #   "不是空壳"不能丢，所以改成钉新规矩，并补一条更硬的：
            #   真的列出了账户行（只查文本的话，表头在、一行没有也算过）。
            live_sec = pg.locator('#main .lvsec').first.inner_text()
            assert '总资产' in live_sec and '当日盈亏' in live_sec, \
                '实盘那块没渲染出数字：%s' % live_sec[:120]
            assert '持仓浮盈' not in live_sec, \
                '首页又把累计浮盈摆回来了 —— 那是业绩页的事'
            assert pg.locator("#main table.pkt a[href^='#/live/']").count() >= 1, \
                '实盘那块一个账户都没列出来（空壳）：%s' % live_sec[:120]

            # ---------------- 顶栏分组 ----------------
            navs = pg.locator('#top a.nav').all_inner_texts()
            #   🔴 9 -> 7（2026-09-14）：板块收进盘面、对比收进个股。
            assert len(navs) == 7, '顶栏应是 7 个入口：%s' % navs
            joined0 = ' '.join(navs)
            for gone in ('板块', '对比'):
                assert gone not in joined0, \
                    ('顶栏还有「%s」—— 它已经收进父页了：%s' % (gone, navs))
            assert '实盘' in navs[0], \
                '实盘应排最前 —— 它是唯一回答"今天要做什么"的入口：%s' % navs
            # 买点紧跟实盘（同属"每天必看"那一组，都是回答"今天要做什么"）
            assert '买点' in navs[1], '买点应紧跟实盘：%s' % navs
            assert pg.locator('#top .navsep').count() == 3, \
                '应有 3 条分组分隔（实盘 | 市场 | 研究 | 数据）'
            # 已取消的入口不该还在
            joined = ' '.join(navs)
            assert '查数据' not in joined, '顶栏还有已取消的「查数据」'
            assert '数据字典' not in joined, \
                '「数据字典」应并进「数据」页的页签，顶栏不再单列'

            # ---------------- 回测归档搬到 #/runs ----------------
            pg.click('#top a.nav[href="/#/runs"]')
            pg.wait_for_timeout(1500)
            assert pg.is_visible('#cat'), '#/runs 没显示回测目录树'
            assert pg.locator('.nd.d0').count() > 0, '目录树没有顶层节点'
            # 过滤/全部展开只在这个视图里出现（别处是噪声）
            assert pg.locator('#filter').count() == 1, '#/runs 缺过滤框'
            pg.fill('#filter', '不可能匹配的字符串xyz')
            pg.wait_for_timeout(600)
            assert '没有匹配' in pg.locator('#cat').inner_text(), '过滤没生效'
            pg.fill('#filter', '')
            pg.wait_for_timeout(600)

            # ---------------- 数据页签 ----------------
            pg.click('#top a.nav[href="/#/sync"]')
            pg.wait_for_selector('#main .lvsec', timeout=90000)
            pg.wait_for_timeout(1200)
            assert pg.locator('#filter').count() == 0, \
                '过滤框跑到数据页去了 —— 它只在回测目录有意义'
            tabs = pg.locator('#main .btn').all_inner_texts()[:2]
            assert '数据状态 / 同步' in tabs[0] and '口径字典' in tabs[1], \
                '数据页的两个页签不对：%s' % tabs
            assert '数据' in pg.locator('#top .btn.nav.on').inner_text()
            # 切到口径字典：同一个顶栏入口仍然高亮（它们是一件事的两面）
            pg.click('#main .btn:has-text("口径字典")')
            pg.wait_for_selector('.ditem', timeout=60000)
            pg.wait_for_timeout(800)
            assert pg.locator('.ditem').count() >= 4, '字典侧栏没渲染'
            assert '数据' in pg.locator('#top .btn.nav.on').inner_text(), \
                '切到字典页签后顶栏高亮跑了'
            assert '#/docs' in pg.url, 'URL 没跟着变（旧链接要能直达）'
            # 切回来
            pg.click('#main .btn:has-text("数据状态")')
            pg.wait_for_timeout(2000)
            assert pg.locator('#syrun').count() == 1, '切不回同步页签'

            # ---------------- 顶栏标题跟着 hash 变 ----------------
            for h, want in (('#/live', '实盘'), ('#/picks', '选中的规则'),
                            ('#/runs', '回测归档'), ('', '总览')):
                pg.goto(base + '/' + h, wait_until='networkidle')
                pg.wait_for_timeout(2200)
                got = pg.locator('#top h1').inner_text()
                assert want in got, \
                    ('%s 的标题应是「%s」，实得「%s」—— 固定写死的话在实盘页'
                     '也会显示"回测归档"，而顶栏本来是用来告诉人"我在哪"的'
                     % (h or '(空)', want, got))

            br.close()
            assert not errs, '页面有运行时错误：%s' % errs[:3]
            return ('首页是总览（6 块，标题即入口，站名点亮，不再是回测目录树）；'
                    '顶栏 7 个入口分 4 组 3 条分隔、实盘排最前、板块与对比已收进父页、'
                    '已取消的「查数据」与并入页签的「数据字典」都不在顶栏；'
                    '回测归档在 #/runs 且过滤框只在那里；'
                    '数据页两个页签互切、顶栏高亮不跑、URL 可直达；'
                    '顶栏标题跟着 hash 变（4 个视图核过）')
    finally:
        sv.ALLOW_LIVE = old_live
        httpd.shutdown()


@case('盘中 1 分钟线：时段 / 落盘去重 / 实时估值 / 限流兜底', tag='fast')
def t_realtime():
    """两个源各干各的，这是稳定性的关键（见 realtime.py 模块说明）：

        腾讯 qt.gtimg.cn  **批量**快照  1 次请求覆盖全部持仓 -> 实时盈亏
        东财 trends2      单只          一次拿当天全部 1 分钟 -> 落盘的 bar

    ★ 这条用例**不打真接口** —— selftest 要能离线跑，而且拿外部接口当断言
      等于把别人的限流变成自己的红灯。真实连通性在调研时实测过
      （见数据字典索引 5）。这里用假数据验【我们自己的逻辑】：
      时段判定、按键去重、实时价覆盖、以及**取不到时的行为**。
    """
    import shutil
    import tempfile
    from datetime import datetime as _dt
    from datetime import time as _t

    from assay import live as lv
    from assay import realtime as rt

    # ---- 1) 交易时段：边界要含在内 ----
    #   ★ 15:01 而不是 15:00 —— 收盘那一根要到 15:01 才拿得到。
    #     写成 15:00 会永远缺当天最后一根，而这不会报错。
    for hh, mm, want in ((9, 29, False), (9, 30, True), (11, 30, True),
                         (11, 31, True), (11, 32, False), (12, 30, False),
                         (13, 0, True), (14, 59, True), (15, 0, True),
                         (15, 1, True), (15, 2, False)):
        got = rt.in_session(_dt(2026, 9, 3, hh, mm))
        assert got is want, '%02d:%02d 应%s在时段内' % (hh, mm, '' if want else '不')
    assert rt.SESSIONS[0][1] == _t(11, 31) and rt.SESSIONS[1][1] == _t(15, 1), \
        '收盘边界要留到 11:31 / 15:01，否则永远缺最后一根'

    # ---- 2) 落盘：两个库分开 + 按键去重 ----
    tmp = tempfile.mkdtemp()
    os.makedirs(os.path.join(tmp, 'rt'), exist_ok=True)
    try:
        day = '2026-09-03'
        bar = {'code': '601857.XSHG', 'datetime': day + ' 09:31',
               'open': 11.2, 'close': 11.25, 'high': 11.26, 'low': 11.19,
               'volume': 100.0, 'amount': 112000.0, 'avg': 11.22}
        rt.save([bar], day, root=tmp)
        rt.save([bar], day, root=tmp)                   # 同一根写两遍
        rt.save([dict(bar, close=11.30, volume=180.0)], day, root=tmp)
        b = rt.bars('601857.XSHG', day, root=tmp)
        assert len(b) == 1, \
            ('同一 (code, datetime) 必须只留一行 —— 追加会让成交量凭空翻倍，'
             '而多出来的行不报错：%s' % b)
        assert b[0]['volume'] == 180.0, '重复写应保留量更大的那条（更完整）'
        # 两个库是不同文件
        assert rt.day_file(root=tmp, kind='minute_1m') != \
            rt.day_file(root=tmp, kind='snap_1m'), 'bar 与快照必须分开存'

        snap = {'code': '601857.XSHG', 'price': 11.31, 'preclose': 11.26,
                'open': 11.2, 'high': 11.4, 'low': 11.18, 'change_pct': 0.44,
                'turnover_pct': 1.9, 'volume': 5000.0, 'amount': 5.6e7,
                'ts': '20260903143012'}
        rt.save_snap([snap], root=tmp)
        rt.save_snap([dict(snap, price=11.33, ts='20260903143055')], root=tmp)
        lt = rt.latest(['601857.XSHG'], day, root=tmp)
        v = lt['601857.XSHG']
        assert v['at'] == '2026-09-03 14:30', '快照要按【分钟】归并：%s' % v['at']
        assert v['price'] == 11.33, '同一分钟应留最后一次采样'
        assert v['src'] == 'snap', \
            '最新价应优先取快照库（它每分钟每只都有，bar 是轮转抓的）'
        assert v.get('avg') is None or v['at'] != b[0]['datetime']

        # ---- 3) 快照缺某只时，bar 库要能补上 ----
        lt2 = rt.latest(['601857.XSHG', '600519.XSHG'], day, root=tmp)
        assert '600519.XSHG' not in lt2, \
            '两个库都没有的票【不该】出现 —— 不能拿别的价顶上'
        rt.save([dict(bar, code='600519.XSHG', close=1500.0)], day, root=tmp)
        lt3 = rt.latest(['600519.XSHG'], day, root=tmp)
        assert lt3['600519.XSHG']['src'] == 'bar', '快照没有时应回落到 bar 库'

        # ---- 4) status / stale ----
        st = rt.status(day, root=tmp)
        assert st['bar']['rows'] == 2 and st['snap']['rows'] == 1
        assert st['last'] == '2026-09-03 14:30', \
            'last 应取两个库里更新的那个（否则会以为一直落后）：%s' % st['last']
        # 非交易时段不算"落后"
        assert rt.stale_minutes(day, tmp, now=_dt(2026, 9, 3, 12, 30)) is None, \
            '非时段不该报落后 —— 收盘后当然落后，那不是缺数据'
        assert rt.stale_minutes(day, tmp, now=_dt(2026, 9, 3, 14, 35)) == 5

        # ---- 5) 抓取失败：空结果一律当失败，不能当"这只票没数据" ----
        #   东财限流时会返回 data:null 或空 trends（见索引 5 §3.1）。
        real = rt.urllib.request.urlopen
        try:
            class _R:
                def __init__(self, b):
                    self._b = b

                def read(self):
                    return self._b
            rt.urllib.request.urlopen = lambda *a, **k: _R(b'{"data":null}')
            try:
                rt.fetch_one('601857.XSHG')
                raise AssertionError('返回 data:null 时必须报错，'
                                     '不能当成"这只票没数据"（会把持仓价清空）')
            except rt.RTError as e:
                assert '限流' in str(e) or '空' in str(e), '报错要说清原因：%s' % e
            # 整批里一只失败不该打断其它只
            r = rt.fetch(['601857.XSHG', '600519.XSHG'], root=tmp, sleep=0)
            assert r['ok'] == 0 and len(r['fail']) == 2, r
        finally:
            rt.urllib.request.urlopen = real

        # ---- 6) 字段位序：trends2 少一个字段就整行错位 ----
        src = open(os.path.join(REPO,
                                'assay', 'realtime.py'), encoding='utf-8').read()
        assert 'f51,f52,f53,f54,f55,f56,f57,f58' in src, \
            ('trends2 必须请求 8 个 fields2 —— 漏掉 f52(开) 时只返回 7 段，'
             '按 8 段解析会把"收"当成"开"，而这不报错')
        assert 'TX_BATCH = 400' in src, \
            '腾讯批量要有上限（实测 900 ✓ / 950 ❌ HTTP 414）'
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # ---- 7) 持仓估值：有实时价就用，没有就回落收盘，且【标出来】----
    P = lv.positions_valued('froec')
    assert 'price_src' in P and 'rt_n' in P, '估值没给"价来源"'
    assert P['price_src'] in ('实时', '部分实时', '收盘')
    for x in P['items']:
        if x.get('rt_src'):
            assert x.get('rt_at'), '标了实时却没给时刻'
    if P['rt_n']:
        assert P['asof'] == P['rt_at'], \
            ('全部实时时 asof 要写实时那一刻 —— 还写日线日的话，页面上会出现'
             '"估值日 09-02"配 09-03 的实时价：%s vs %s' % (P['asof'], P['rt_at']))
        assert P['asof_close'] and P['asof_close'] != P['asof'], \
            '收盘日要单独留一份（部分实时时要用它说明其余按哪天算）'
    # ---- 并发落盘 / 坏文件自愈 / 接口逐腿降级（2026-09-07 生产事故）----
    # 🔴 实测事故链：写当日文件的有【三个来源】，两个在 HTTP 请求线程上
    #   （`_rt_ensure` / `_rt_catch_up`），而 `save()` 当时**没有锁**。
    #   两个线程同时落盘 -> 落地一个头尾都是 PAR1、中间元数据是垃圾的 parquet
    #   -> 此后每一轮读它都抛 TProtocolException -> `_rt_loop` catch 住继续转，
    #   但 `rounds`/`last` 不再更新 -> **线程活着、每 60 秒失败一次，
    #   连续 3.5 小时**（09:47 -> 13:20），页面上只表现为"实时不刷新"。
    #   同时 `/api/rt/status` 整体 500，把**健康的快照腿**也一起藏了。
    import threading as _th
    from datetime import date as _date
    import duckdb as _dk
    _d = tempfile.mkdtemp(prefix='rtconc_')
    os.makedirs(os.path.join(_d, 'rt', 'minute_1m'), exist_ok=True)
    _day = '2026-09-07'
    _p = rt.day_file(_date.fromisoformat(_day), root=_d)

    def _row(i):
        return {'code': '60%04d.XSHG' % i,
                'datetime': '2026-09-07 10:%02d:00' % (i % 60),
                'open': 1.0, 'close': 1.1, 'high': 1.2, 'low': 0.9,
                'volume': i, 'amount': 1.0, 'avg': 1.0}

    # ① 并发写：不许丢行、不许撕裂、不许残留 tmp
    #   ★ 判据是**行数**而不是"没抛异常" —— 无锁版本一个异常都不抛，
    #     它只是把别人写的行悄悄丢掉（实测 200 线程后只剩 1 行）。
    _errs = []

    def _w(i):
        try:
            rt.save([_row(i)], _day, root=_d)
        except Exception as e:                              # noqa: BLE001
            _errs.append('%s: %s' % (type(e).__name__, e))
    _ts = [_th.Thread(target=_w, args=(i,)) for i in range(40)]
    [t.start() for t in _ts]
    [t.join() for t in _ts]
    assert not _errs, '并发落盘抛异常：%s' % _errs[:3]
    _n = _dk.connect().execute(
        "SELECT count(*) FROM read_parquet('%s')" % _p).fetchone()[0]
    assert _n == 40, ('并发落盘丢行：40 个线程各写 1 行，最终只有 %d 行 —— '
                      'save() 的读-合并-写没有串行化' % _n)
    _left = [f for f in os.listdir(os.path.dirname(_p)) if f.endswith('.tmp')]
    assert not _left, '残留 tmp 文件：%s' % _left

    # ② 坏文件必须【隔离 + 重建】，不能让异常冒出去
    with open(_p, 'wb') as _f:              # 头尾像 parquet、中间是垃圾
        _f.write(b'PAR1' + b'\x00' * 200 + b'PAR1')
    try:
        _dk.connect().execute("SELECT count(*) FROM read_parquet('%s')" % _p)
        raise AssertionError('人造的坏文件居然读得动 —— 这条断言是空转的')
    except AssertionError:
        raise
    except Exception:                                       # noqa: BLE001
        pass
    rt.save([_row(999)], _day, root=_d)     # 不许抛
    _n2 = _dk.connect().execute(
        "SELECT count(*) FROM read_parquet('%s')" % _p).fetchone()[0]
    assert _n2 == 1, '坏文件没被隔离重建（重建后应只剩新写的 1 行，实得 %d）' % _n2
    _q = os.path.join(os.path.dirname(_p), '_corrupt')
    assert os.path.isdir(_q) and os.listdir(_q), \
        '坏文件被直接丢弃了 —— 必须留证据，否则分不清撕裂写还是磁盘坏'

    # ③ status() 一条腿坏了不能打挂整个接口，且要把坏腿【说出来】
    with open(_p, 'wb') as _f:
        _f.write(b'PAR1' + b'\x00' * 200 + b'PAR1')
    _st = rt.status(day=_date.fromisoformat(_day), root=_d)
    assert 'error' not in _st, 'status 被一条坏腿整体打挂了：%s' % _st.get('error')
    assert _st['bar'].get('err'), \
        '坏掉的 bar 腿没有 err 标记 —— 页面会以为"就是没有数据"'
    assert 'snap' in _st, 'status 少了 snap 腿'
    shutil.rmtree(_d, ignore_errors=True)

    return ('时段 11 个边界点（收盘留到 11:31/15:01）；bar 与快照两库分开且'
            '按键去重（重复写不翻倍）；最新价优先快照、缺了回落 bar、'
            '两库都没有的不顶价；last 取两库较新者；非时段不报落后；'
            'data:null 当失败且不打断整批；trends2 八字段位序与腾讯批量上限'
            '写死在代码里；40 线程并发落盘不丢行/不撕裂/无残留 tmp；'
            '坏文件隔离重建且留证；status 逐腿降级（坏腿标 err 不打挂接口）；'
            '持仓估值 %s（%d/%d 实时）'
            % (P['price_src'], P['rt_n'], len(P['items'])))


@case('模块拆分：路径基准 / 延迟导入层级 / 门面双向转发', tag='fast')
def t_srv_split():
    """server.py 按产品域拆进 srv/ 之后，三处**不报错**的坑，逐条钉住。

    这三条都是实测踩到的，表现全是"接口静默返回空或 500"，而不是启动报错。
    """
    import importlib
    from assay import server as sv
    from assay.srv import base
    here = REPO

    # ---- ① 路径基准：__file__ 跟着文件搬进了子目录 ----
    #   🔴 HERE 照抄 dirname(__file__) 会变成 .../assay/srv，于是
    #     picks.json / live/ / web/ 全解到不存在的路径。
    #     实测：/api/marks 返回 {}（标记全丢）、7 个实盘接口 500。
    assert base.HERE == os.path.join(here, 'assay'), \
        'base.HERE 应指 assay 包目录，实际 %s' % base.HERE
    assert base.WEB == os.path.join(here, 'web'), \
        'base.WEB 错了：%s' % base.WEB
    assert os.path.dirname(base.MARKS_FILE) == here, \
        'picks.json 应在仓库根，实际 %s' % base.MARKS_FILE

    # ---- ② 延迟导入的层级：`.` 也跟着文件变了 ----
    #   🔴 base 里 `from . import live` 原来是 assay.live，搬进 srv/ 之后
    #     `.` 变成 assay.srv → 返回**路由模块自己**，于是 m.LiveError
    #     AttributeError。实测就是这么 500 的。
    WRAP = {'_live': 'assay.live', '_rt': 'assay.realtime',
            '_watch': 'assay.watchlist', '_alerts': 'assay.alerts',
            '_market': 'assay.market'}
    for fn, want in WRAP.items():
        got = getattr(base, fn)().__name__
        assert got == want, \
            'base.%s() 应返回 %s，实际 %s —— 相对导入解错了层' % (fn, want, got)

    # ---- ③ 门面必须【转发】而不是 re-export ----
    #   🔴 ALLOW_BACKTEST / ALLOW_LIVE 是 serve() 会重新赋值的 bool。
    #     `from .srv.base import ALLOW_LIVE` 拿到的是副本 —— serve() 改了值
    #     外部读到的还是旧的，表现是"明明开了实盘，接口说功能没开"。
    #   而且必须**双向**：外部有 `sv.ALLOW_LIVE = True` 这种赋值用法
    #   （本文件 26 处，用来模拟启动模式）。PEP 562 的模块级 __getattr__
    #   只拦【读】—— 赋值会在 server 模块 __dict__ 里建副本，之后读取
    #   走正常查找、不再转发，各域读到的还是旧值。
    #   实测这么挂掉 3 个用例（启动开关 / 自选 / 本条）。所以门面是
    #   ModuleType 子类，__setattr__ 把这几个名字写回 base。
    old = (base.ALLOW_BACKTEST, base.ALLOW_LIVE)
    try:
        base.ALLOW_BACKTEST, base.ALLOW_LIVE = True, True
        assert sv.ALLOW_BACKTEST is True and sv.ALLOW_LIVE is True, \
            '写 base 之后 sv.ALLOW_* 没跟着变 —— 门面成了 re-export'
        sv.ALLOW_BACKTEST, sv.ALLOW_LIVE = False, False
        assert base.ALLOW_BACKTEST is False and base.ALLOW_LIVE is False, \
            ('写 sv.ALLOW_* 没写回 base —— 各域读的是 base，'
             '于是"设置了却不生效"')
        assert 'ALLOW_LIVE' not in vars(sv), \
            'sv.__dict__ 里出现了 ALLOW_LIVE 副本 —— __setattr__ 没拦住'
    finally:
        base.ALLOW_BACKTEST, base.ALLOW_LIVE = old

    # ---- 对外契约：selftest 自己用到的 sv.* 名字必须都还在 ----
    #   ★ 直接从本文件的源码里**扫**出来，不照清单拼 ——
    #     照清单拼的话，以后新用一个 sv.xxx 就不在保护范围内，而那不报错。
    #   ★ 用 ast 扫 `sv.xxx` 的属性访问，**不用正则** —— 正则会把注释和
    #     字符串里的 `sv.ALLOW_*` / `sv.xxx` 也当成真实用法
    #     （第一版就是这么假失败的，报"门面少了 ALLOW_ 和 xxx"）。
    import ast as _ast
    # 🔴 扫**全部用例文件**（拆分后用例不在 selftest.py 里了，见
    #   `_base._all_case_src` 的说明）。
    _tree = _ast.parse(_all_case_src())
    used = sorted({n.attr for n in _ast.walk(_tree)
                   if isinstance(n, _ast.Attribute)
                   and isinstance(n.value, _ast.Name) and n.value.id == 'sv'})
    miss = [n for n in used if not hasattr(sv, n)]
    assert not miss, 'server 门面少了这些名字：%s' % miss

    # ---- 🔴 未定义名字检测：搬运代码的头号风险 ----
    #   拆分时把 `_JOBS` / `_DATE_RE` / `_run_job` 留在了 runs.py，而
    #   live.py / sync.py 里仍裸用它们 —— **import 成功、45 个 GET 全对**，
    #   因为那几行只在 POST 分支里走到。实测就是这么漏过去的。
    #   ★ 所以这条不能靠"启动不报错"或"接口打得通"来代替：
    #     它是**静态**扫每个模块里 Load 但未绑定的名字。
    #   ★ `_run_job` 是作为 `Thread(target=_run_job)` 传递的，不是
    #     `_run_job(` 调用形式 —— 按调用形式做替换的脚本会漏掉它。
    import builtins
    _BI = set(dir(builtins)) | {'__name__', '__file__', '__doc__'}

    def _undef(path):
        tt = _ast.parse(open(path, encoding='utf-8').read())
        bound = set()
        for n in _ast.walk(tt):
            if isinstance(n, (_ast.Import, _ast.ImportFrom)):
                for a in n.names:
                    bound.add((a.asname or a.name).split('.')[0])
            elif isinstance(n, (_ast.FunctionDef, _ast.AsyncFunctionDef,
                                _ast.ClassDef)):
                bound.add(n.name)
            elif isinstance(n, _ast.Assign):
                # 🔴 只认 **Store** 的名字。`_JOBS[job_id] = {...}` 的目标是
                #   Subscript，里面那个 `_JOBS` 是 **Load** —— 不判 ctx 的话
                #   它会被当成"绑定了 _JOBS"，于是这个检测器**恰好放过了
                #   它当初为之而写的那个 bug**（srv/live.py 里裸用 _JOBS，
                #   只在 POST 分支走到，import 与 45 个 GET 全绿）。
                #   同理 `obj.attr = v` 的 `obj` 也是 Load。
                for tg in n.targets:
                    bound |= {k.id for k in _ast.walk(tg)
                              if isinstance(k, _ast.Name)
                              and isinstance(k.ctx, _ast.Store)}
            elif isinstance(n, (_ast.AnnAssign, _ast.AugAssign)):
                bound |= {k.id for k in _ast.walk(n.target)
                          if isinstance(k, _ast.Name)
                          and isinstance(k.ctx, _ast.Store)}
            elif isinstance(n, _ast.arg):
                bound.add(n.arg)
            elif isinstance(n, _ast.ExceptHandler) and n.name:
                bound.add(n.name)
            elif isinstance(n, (_ast.For, _ast.comprehension)):
                bound |= {k.id for k in _ast.walk(n.target)
                          if isinstance(k, _ast.Name)
                          and isinstance(k.ctx, _ast.Store)}
            elif isinstance(n, _ast.withitem) and n.optional_vars:
                bound |= {k.id for k in _ast.walk(n.optional_vars)
                          if isinstance(k, _ast.Name)
                          and isinstance(k.ctx, _ast.Store)}
            elif isinstance(n, _ast.Global):
                bound |= set(n.names)
            elif isinstance(n, _ast.Lambda):
                bound |= {a.arg for a in n.args.args}
        loads = {k.id for k in _ast.walk(tt) if isinstance(k, _ast.Name)
                 and isinstance(k.ctx, _ast.Load)}
        return sorted(loads - bound - _BI)

    pkg = os.path.join(here, 'assay')
    scanned = 0
    for r, _d, fs in os.walk(pkg):
        if '__pycache__' in r:
            continue
        for f in sorted(fs):
            if not f.endswith('.py'):
                continue
            u = _undef(os.path.join(r, f))
            assert not u, ('%s 里有未定义的名字 %s —— 会在运行到那一行时'
                           ' NameError（而 import 和大部分接口都不会报）'
                           % (os.path.relpath(os.path.join(r, f), here), u))
            scanned += 1

    # ---- live.py 的门面：🔴 LIVE 重定向必须真的生效 ----
    #   selftest 靠 `lv.LIVE = 临时目录` 把写操作重定向掉、不污染真实账本
    #   （本文件 3 处，那是那条纪律的实现手段）。
    #   拆进 lv/ 之后，读取方（acct_dir / load_accounts / _save_accounts /
    #   交易日历）都在 lv/base.py —— 门面若是 re-export，重定向就**静默失效**，
    #   用例会把数据写进 live/ 真账本，**而它不报错**。
    from assay import live as _lv
    from assay.lv import base as _lvbase
    assert _lvbase.HERE == os.path.join(here, 'assay'), \
        'lv/base.py 的 HERE 应指 assay 包目录（__file__ 深了一层）：%s' % _lvbase.HERE
    assert _lv.LIVE == os.path.join(here, 'live'), \
        'lv.LIVE 应是 <repo>/live，实际 %s —— ROOT 算错了一层' % _lv.LIVE
    assert os.path.isdir(_lv._lake()), 'lv._lake() 解不到 datalake：%s' % _lv._lake()
    _old_live = _lv.LIVE
    try:
        _lv.LIVE = '/tmp/_probe_live_redirect'
        assert _lvbase.LIVE == '/tmp/_probe_live_redirect', \
            '写 lv.LIVE 没写回 lv.base —— 重定向会静默失效，用例会写真账本'
        assert _lv.acct_dir('x') == '/tmp/_probe_live_redirect/x', \
            'acct_dir 没跟着重定向 —— 读取方拿的是 base 里的旧值'
        assert 'LIVE' not in vars(_lv), \
            'live.__dict__ 里出现了 LIVE 副本 —— __setattr__ 没拦住'
    finally:
        _lv.LIVE = _old_live
    # 本文件用到的 lv.* 名字也必须都还在（同样用 ast 扫，不照清单拼）
    _lvused = sorted({n.attr for n in _ast.walk(_tree)
                      if isinstance(n, _ast.Attribute)
                      and isinstance(n.value, _ast.Name) and n.value.id == 'lv'})
    _miss = [n for n in _lvused if not hasattr(_lv, n)]
    assert not _miss, 'live 门面少了这些名字：%s' % _miss
    LVDOMS = ('base', 'fee', 'px', 'pos', 'ver', 'sig', 'perf', 'explain')
    for d in LVDOMS:
        importlib.import_module('assay.lv.' + d)
    lp = open(os.path.join(here, 'assay', 'live.py'), encoding='utf-8').read()
    assert lp.count('\n') < 100, \
        'live.py 应只剩门面（%d 行）—— 实现放 lv/ 对应域' % lp.count('\n')

    # ---- 🔴 名字遮蔽：局部变量压掉同名的模块级 import ----
    #   实测踩到：serve() 里 `rt = base._rt()` 建了个局部变量（业务模块
    #   assay.realtime），把模块级 `from .srv import ... rt`（路由模块）
    #   **遮蔽**掉了，于是 `rt._RT` / `rt._rt_loop` 去 assay.realtime 找
    #   -> AttributeError。
    #   ★ 「未定义名字检测」抓不到它 —— 名字是定义的，只是指向错的对象。
    #   ★ 而且它只在 **--readonly 之外**的分支里（起实盘/行情线程那段），
    #     拆分时全程用 --readonly 验证，恰好一次都没执行到。
    for r, _d, fs in os.walk(pkg):
        if '__pycache__' in r:
            continue
        for f in sorted(fs):
            if not f.endswith('.py'):
                continue
            fp = os.path.join(r, f)
            tt = _ast.parse(open(fp, encoding='utf-8').read())
            mods = set()
            for n in tt.body:
                if isinstance(n, (_ast.Import, _ast.ImportFrom)):
                    for a in n.names:
                        mods.add(a.asname or a.name.split('.')[0])
            for n in _ast.walk(tt):
                if not isinstance(n, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
                    continue
                for k in _ast.walk(n):
                    hit = None
                    if isinstance(k, _ast.Assign):
                        for tg in k.targets:
                            for x in _ast.walk(tg):
                                if (isinstance(x, _ast.Name)
                                        and isinstance(x.ctx, _ast.Store)
                                        and x.id in mods):
                                    hit = (x.id, x.lineno)
                    elif isinstance(k, _ast.arg) and k.arg in mods:
                        hit = (k.arg, getattr(k, 'lineno', 0))
                    if hit:
                        raise AssertionError(
                            '%s 的 %s() 行%d：局部变量 `%s` 遮蔽了同名 import'
                            ' —— 之后 `%s.xxx` 会去错的对象上找'
                            % (os.path.relpath(fp, here), n.name, hit[1],
                               hit[0], hit[0]))

    # ---- serve() 全功能分支引用的名字必须真的存在 ----
    #   `--readonly` 不走那段，所以它是拆分时最容易漏验的路径。
    from assay.srv import live as _srvlive, rt as _srvrt
    for _m, _n in ((_srvrt, '_RT'), (_srvrt, '_rt_loop'),
                   (_srvlive, '_live_loop')):
        assert hasattr(_m, _n), \
            'serve() 会用 %s.%s，但它不存在' % (_m.__name__, _n)
    assert not hasattr(base._rt(), '_rt_loop'), \
        ('assay.realtime 也有了 _rt_loop —— 上面那条 hasattr 就失去意义了，'
         '得换个判据')

    # ---- srv/ 的结构：每个域都在，且 server.py 只剩骨架 ----
    DOMS = ('base', 'runs', 'docs', 'live', 'rt', 'sync', 'stock',
            'market', 'watch')
    for d in DOMS:
        importlib.import_module('assay.srv.' + d)
    sp = open(os.path.join(here, 'assay', 'server.py'), encoding='utf-8').read()
    n = sp.count('\n')
    assert n < 400, 'server.py 又长回去了（%d 行）—— 新接口该放 srv/ 对应域' % n
    return ('HERE/WEB/picks.json 三个路径基准正确；5 个延迟导入封装都指向业务模块'
            '（不是 srv 自己）；门面双向转发 ALLOW_*；'
            '本文件用到的 %d 个 sv.* 名字全在；'
            'assay/ %d 个模块零未定义名字；srv/ %d + lv/ %d 个域可 import；'
            'server.py 剩 %d 行 / live.py 剩 %d 行；'
            'lv.LIVE 重定向双向生效（%d 个 lv.* 名字全在）'
            % (len(used), scanned, len(DOMS), len(LVDOMS), n,
               lp.count('\n'), len(_lvused)))


@case('策略数据 API：不写 SQL 也能复现同一个候选池')
def t_data_api():
    """`feed.universe / snapshot / fundamentals` —— 让策略不必直接写 SQL。

    理想中只有 feed 知道 datalake 的存在；而 froec.py / 红利那批把 9 层 CTE
    写在策略里，口径（as-of / 停牌结转 / 多期去重）与规则（阈值 / 分位 /
    排序 / 截断）混在同一段文本里。新策略 `froec_api.py` 用这三个 API 取数、
    用 Python 写规则，**froec.py 一行没动**。

    🔴 判据是**同一天两版候选池逐位相同**，并且要**跳过 ROE 切点并列的日子**
      —— 原 SQL 的 `ORDER BY increase DESC` 没有 tie-break，那些日子它自己
      就不确定（实测 539 个调仓日里约 4%）。
    """
    import datetime
    import importlib.util as _iu
    from assay.guard import GuardedFeed, LookAheadError

    # ---- ① 三个 API 都带 PIT 防火墙（它们直接读 std/ 与面板）----
    f = PanelFeed('2024-01-01', '2026-09-07')
    gf = GuardedFeed(f)
    gf.set_clock(datetime.date(2026, 9, 4), 'pre_open')
    for nm, call in (('universe', lambda x: gf.universe(x, listed_days=250)),
                     ('snapshot', lambda x: gf.snapshot(x, ['pb'])),
                     ('fundamentals', lambda x: gf.fundamentals(x, ['eps']))):
        for arg in ('2026-09-04', datetime.date(2026, 9, 4)):
            try:
                call(arg)
                raise AssertionError(
                    '%s(%r) 没被 PIT 拦住 —— 它直接读 std/ 与面板，'
                    '少一道防火墙就是给未来函数开后门（而它不报错）'
                    % (nm, arg))
            except LookAheadError:
                pass

    # ---- ② 停牌股：只结转价格派生量 ----
    #   🔴 基本面**不能**结转 —— 会拿到该股最后交易日那天的过期报告。
    u = f.universe('2026-09-07', listed_days=250, exclude_like='68%')
    cur = f.snapshot('2026-09-07', ['pb'], codes=u)
    carry = f.snapshot('2026-09-07', ['pb', 'floatmv'], codes=u, carry_days=400)
    assert len(carry) > len(cur), \
        ('carry_days 该把当日无 K 线（停牌）的票补进来，实得 %d vs %d'
         % (len(carry), len(cur)))
    assert len(carry) == len(u), '结转后该覆盖整个宇宙'

    # ---- ③ fundamentals：as-of + 多期 + require_all ----
    roe5 = f.fundamentals('2026-09-07', ['roe'], codes=u, periods=5,
                          require_all=True)
    cnt = roe5.groupby('code')['seq'].count()
    assert set(cnt.unique()) == {5}, \
        ('require_all=True 该只留恰好凑满 5 期的 code，实得期数 %s —— '
         '缺期的票会算出一个偏小的 ROE 加速度而**不报错**' % sorted(cnt.unique()))
    assert roe5['seq'].min() == 1, 'seq 该从 1（最新一期）开始'

    # ---- ④ 与 SQL 版候选池逐位相同（跳过 ROE 切点并列的日子）----
    P = {'stop_intraday': 1, 'stop_loss': 0.35, 'weekday': 2,
         'paused_in_pool': 0, 'kcb_688_only': 0}
    feed = PanelFeed('2016-01-01', '2026-09-03')
    eng = Engine(load('strategies/小市值/froec.py'), feed, cash=5e5,
                 cost=Cost(), params=P)
    eng._boot()
    ctx = eng.ctx

    def _mod(name, tag):
        sp = _iu.spec_from_file_location(tag, 'strategies/小市值/%s.py' % name)
        m = _iu.module_from_spec(sp)
        sp.loader.exec_module(m)
        return m
    ms, ma = _mod('froec', '_api_t1'), _mod('froec_api', '_api_t2')
    EXCL = ','.join("'%s'" % x for x in ms.EXCL_IND)
    #   🔴 tie 检测要在**截断与行业排除之前**做 —— 被排除的那一行不在末层
    #     输出里，在末层上查 roe_rn==cut+1 会漏判成"无并列"（踩过）。
    S = ms.SQL.replace('WHERE rn2 <= {roecut}', 'WHERE rn2 <= 100000') \
              .replace("WHERE sw_l1_name IS NULL OR "
                       "sw_l1_name NOT IN ({excl})", 'WHERE 1=1')
    #   ★ 采样日要够密：切点算错 ±1 只差一只票，而它常常本来就进不了
    #     最终前 20 —— 采样太少那条断言就成了空转（变异测试抓到过：
    #     6 个采样日抓不到 "roe 切点 -1"）。
    DAYS = ['2016-03-31', '2016-09-30', '2017-06-30', '2018-03-30',
            '2018-06-29', '2019-03-29', '2019-09-30', '2020-03-31',
            '2020-09-30', '2021-06-30', '2022-03-31', '2022-09-30',
            '2023-06-30', '2024-04-22', '2025-03-31', '2026-09-07']
    same = tie = 0
    for ds in DAYS:
        d = datetime.date.fromisoformat(ds)
        w = ctx.data.query(S, sd=d, listed=250, cand=100000, pin='FALSE',
                           kcb='68%', pert=0, salt='a', skip=0,
                           pbcut='floor(0.5 * n)', roecut='x', excl=EXCL)
        if w.empty:
            continue
        n2 = int(w['roe_n'].iloc[0])
        cut = int(0.1 * n2)
        v = w[w['roe_rn'] == cut]['roe_inc']
        uu = w[w['roe_rn'] == cut + 1]['roe_inc']
        if len(v) and len(uu) and abs(v.iloc[0] - uu.iloc[0]) < 1e-12:
            tie += 1
            continue
        A = ctx.data.query(ms.SQL, sd=d, listed=250, cand=20, pin='FALSE',
                           kcb='68%', pert=0, salt='a', skip=0,
                           pbcut='floor(0.5 * n)',
                           roecut='floor(0.1 * n2)', excl=EXCL)
        B = ma._pick(ctx, d, 20)
        a, b = A['jq_code'].tolist(), B['jq_code'].tolist()
        assert a == b, \
            ('%s：API 版与 SQL 版候选池不同（切点没有并列，所以这是真差异）\n'
             '  SQL 独有 %s\n  API 独有 %s'
             % (d, sorted(set(a) - set(b)), sorted(set(b) - set(a))))
        #   🔴 只比 jq_code 太松：pb 切点算错 ±1 只多/少带一只进半区，
        #     而它常常本来就进不了最终前 20 -> 断言照样绿（变异测试抓到过）。
        #     连**中间层的名次与分母**一起比 —— 那几列正是切点的证据。
        #   分母（pb_n / roe_n）与并列无关，是**切点的硬证据** ——
        #   切点算错 ±1 会让下一层的分母跟着变。
        for col in ('pb_n', 'roe_n'):
            va, vb = int(A[col].iloc[0]), int(B[col].iloc[0])
            assert va == vb, \
                ('%s：%s 不同（SQL %d / API %d）—— 某一层的切点算错了'
                 % (d, col, va, vb))
        #   名次（pb_rn / roe_rn）只在**整列都没有并列**时才可比：
        #   原 SQL 的 ORDER BY 没有 tie-break，并列处的号码本身不确定。
        if len(w) == w['roe_inc'].nunique():
            for col in ('pb_rn', 'roe_rn'):
                va, vb = A[col].tolist(), B[col].tolist()
                assert [int(x) for x in va] == [int(x) for x in vb], \
                    ('%s：%s 列不同（该日无并列，所以这是真差异）\n'
                     '  SQL %s\n  API %s' % (d, col, va[:8], vb[:8]))
        for col in ('pb', 'floatmv', 'roe_inc', 'eps'):
            va, vb = A[col].tolist(), B[col].tolist()
            assert all(abs(x - y) < 1e-9 for x, y in zip(va, vb)), \
                '%s：%s 列的数值不同' % (d, col)
        same += 1
    assert same >= 12, \
        '有效对比的天数太少（%d），这条断言会变成空转' % same

    # ---- ⑤ froec.py **一行没动**：它仍然自己写 SQL，且不含新 API ----
    src = open('strategies/小市值/froec.py', encoding='utf-8').read()
    assert 'SQL = ' in src and 'context.data.query(' in src, \
        'froec.py 该保持原样（自己写 SQL）—— 它的归档要可比、实盘绑着它'
    for api in ('.universe(', '.snapshot(', '.fundamentals('):
        assert api not in src, \
            'froec.py 里出现了新 API（%s）—— 要求是**不动现有策略**' % api
    #   新策略必须真的不写 SQL
    asrc = open('strategies/小市值/froec_api.py', encoding='utf-8').read()
    assert 'data.query(' not in asrc and 'SELECT' not in asrc.upper() \
        .replace('SELECT ... FROM', ''), \
        'froec_api.py 里还有 SQL —— 它存在的意义就是"不写 SQL"'
    return ('3 个 API × 两种日期写法都被 PIT 拦住；停牌结转把宇宙补齐'
            '（%d -> %d）；require_all 只留 5 期齐全的；%d 个采样日与 SQL 版'
            '候选池逐位相同（%d 个 ROE 切点并列日跳过 —— 原 SQL 没有 '
            'tie-break，那些日子它自己就不确定）；froec.py 一行没动'
            % (len(cur), len(carry), same, tie))


@case('因子 API：策略直接用量化因子（路径正本 / 指纹 / PIT / 可比性）')
def t_factor_api():
    """`feed.factors / factor_meta / factor_ids` —— 让策略直接用那 162 个因子。

    与 `universe/snapshot/fundamentals` 同一条边界：feed 管「从哪取、as-of
    怎么算」，阈值 / 分位 / 排序 / 截断仍归策略。用法样板见
    `strategies/_demo/factor_api.py`（一行 SQL 都没有）。

    这条用例钉六件事，每件对应一种**不报错**的坏法：

      A 路径正本只有一处   —— 第二份 glob 迟早与指纹那份分叉
      B 指纹盯的 == 实际读的 —— 少盯一个：tick 判「不用重算」、模拟盘判
                              「不用推进」、归档去重把两次不同数据的回测
                              当成重复删掉一个，**三个都不报错**
      C PIT 防火墙          —— 少了它就是拿今天的收盘决定今天的买卖，
                              而回测只会**好得可疑**
      D 未知 id 响亮失败    —— 否则 duckdb 抛一句 Candidate bindings，
                              报错指不到"这个因子没实现/打错字"
      E 值与主面板独立算一致 —— 两条完全独立的路（因子面板 vs 现场开窗）
      F 可比性由目录表给    —— 横截面排一个 `ma20` 排的是"股价×拆股史"
    """
    import datetime
    import numpy as _np
    from assay import paths as _P
    from assay.feed import PanelFeed as _PF
    from assay.guard import GuardedFeed, LookAheadError

    # 🔴 **日期从数据里现取，不写死** —— 写死的第一版取到了 2026-09-19，
    #   而那是个**周六**，因子面板里一行都没有，于是"反向自证"当场报空转。
    #   同「判据不许依赖真实数据碰巧如此 / 构造依赖今天星期几」那两条。
    _f0 = _PF('2026-01-01', '2100-01-01')
    _days = _f0.con.execute(
        'SELECT DISTINCT date FROM %s ORDER BY date DESC LIMIT 2'
        % _P.factor_sql(_f0.root)).df()['date'].tolist()
    assert len(_days) == 2, '因子面板里连两个交易日都没有 —— 先跑 ' \
        'datalake/build/build_factor_daily.py'
    D1, D0 = (str(_days[0])[:10], str(_days[1])[:10])   # D1 当"今天"，D0 严格更早

    # ---- A 路径正本只有一处 ----
    lit = 'mart/factor_daily/factor_*.parquet'
    hits = []
    for rel in ('assay/feed.py', 'assay/guard.py', 'assay/factor_eval.py',
                'assay/srv/factors.py'):
        fp = os.path.join(REPO, rel)
        if os.path.exists(fp) and lit in io_open_text(fp):
            hits.append(rel)
    assert not hits, (
        '这几个文件自己又拼了一遍因子 glob：%s —— 正本在 assay/paths.py 的 '
        'FACTOR_GLOB，第二份迟早与 FINGERPRINT_PARTS 那份分叉，而那不报错'
        % '、'.join(hits))
    assert _P.FACTOR_GLOB in _P.factor_sql(), 'factor_sql 没用那个常量'

    # ---- B 指纹盯的必须与实际读的是同一批 ----
    fsrc = io_open_text(os.path.join(REPO, 'assay/feed.py'))
    assert "('factor', _paths.FACTOR_GLOB)" in fsrc, (
        'FINGERPRINT_PARTS 里没有因子面板（或没走 paths 的常量）—— '
        '因子重算过而指纹没变，tick/模拟盘/归档去重三处都会静默做错事')
    f = _f0
    fp = f.fingerprint()
    assert 'factor' in fp['parts'] and fp['parts']['factor']['n_files'] > 0, \
        '指纹里没有 factor 部件，或它一个文件都没扫到'
    #   ★ 行为判据：指纹扫到的那批 == `factor_sql` 真正读的那批。
    #     只查源码里有那一行的话，glob 被改成另一个（比如带 `**`）照样绿。
    a = set(glob.glob(os.path.join(f.root, _P.FACTOR_GLOB)))
    b = set(glob.glob(os.path.join(
        f.root, _P.factor_sql(f.root).split("'")[1].split(f.root + '/')[-1])))
    assert a and a == b, '指纹扫的 %d 个文件与 factor_sql 读的 %d 个不是同一批' \
        % (len(a), len(b))

    # ---- C PIT 防火墙（两向：拦今天 / 放行昨天）----
    gf = GuardedFeed(f)
    gf.set_clock(datetime.date.fromisoformat(D1), 'pre_open')
    for arg in (D1, datetime.date.fromisoformat(D1)):
        try:
            gf.factors(arg, ['mv'])
            raise AssertionError('guard.factors(%r) 没被 PIT 拦住 —— '
                                 '那一行含【当日收盘】派生量' % (arg,))
        except LookAheadError:
            pass
    past = gf.factors(D0, ['mv'])
    assert len(past) > 1000, (
        '构造不对：%s 只取到 %d 行 —— 反向自证不成立，上面那条分不出'
        '"拦住了"与"永远给不出数据"' % (D0, len(past)))

    # ---- D 未知 id 要点名 ----
    try:
        gf.factors(D0, ['mv', 'rsi14'])
        raise AssertionError('未知因子 id 被静默放行了')
    except ValueError as e:
        assert 'rsi14' in str(e) and '因子广场' in str(e), \
            '报错没点名那个 id、也没说去哪看清单：%s' % e

    # ---- E 值 == 从主面板独立算一遍（+ 反向自证）----
    got = f.factors(D0, ['ma20', 'mv']).set_index('jq_code')
    assert 'fdate' in got.columns, \
        'factors() 没返回 fdate —— 结转出来的旧值与当日值就分不出来了'
    ind = f.con.execute("""
      SELECT jq_code, ma20_x, totalmv FROM (
        SELECT jq_code, date, totalmv,
               avg(close_hfq) OVER (PARTITION BY jq_code ORDER BY date
                                    ROWS 19 PRECEDING) AS ma20_x,
               count(*)       OVER (PARTITION BY jq_code ORDER BY date
                                    ROWS 19 PRECEDING) AS n
        FROM %s WHERE date BETWEEN DATE '2026-01-01' AND DATE '%s')
      WHERE date = DATE '%s' AND n = 20
    """ % (_P.panel_sql(f.root), D0, D0)).df().set_index('jq_code')
    j = got.join(ind, how='inner')
    assert len(j) > 3000, '构造不对：只对上 %d 只，这条自证是空转的' % len(j)
    eps = float(_np.finfo(_np.float32).eps)

    def _bad(c1, c2, frame=j):
        x = frame[c1].astype('float64'); y = frame[c2].astype('float64')
        m = x.notna() & y.notna()
        rel = (x[m] - y[m]).abs() / y[m].abs().clip(lower=1e-12)
        return int((rel > 8 * eps).sum()), int(m.sum())
    for c1, c2 in (('ma20', 'ma20_x'), ('mv', 'totalmv')):
        nb, nn = _bad(c1, c2)
        assert nb == 0, '%s 与面板独立算出来的差了 %d/%d 行' % (c1, nb, nn)
    #   反向自证：拿 MA10 去比必须**绝大多数不等**，否则上面那条分不出真假
    w = f.con.execute("""
      SELECT jq_code, ma10_x FROM (
        SELECT jq_code, date,
               avg(close_hfq) OVER (PARTITION BY jq_code ORDER BY date
                                    ROWS 9 PRECEDING) AS ma10_x
        FROM %s WHERE date BETWEEN DATE '2026-01-01' AND DATE '%s')
      WHERE date = DATE '%s'
    """ % (_P.panel_sql(f.root), D0, D0)).df().set_index('jq_code')
    nb10, nn10 = _bad('ma20', 'ma10_x', got.join(w, how='inner'))
    assert nn10 > 3000 and nb10 > 0.9 * nn10, \
        '反向自证不成立（MA10 与 MA20 居然对上了 %d/%d）—— 上面那条判不出真假' \
        % (nn10 - nb10, nn10)

    # ---- F 可比性由目录表给；demo 的 _xs_guard 两向 ----
    meta = gf.factor_meta(['mv_float', 'ep', 'ma20']).set_index('factor_id')
    assert bool(meta.loc['mv_float', 'xs_comparable']) and \
        bool(meta.loc['ep', 'xs_comparable']) and \
        not bool(meta.loc['ma20', 'xs_comparable']), \
        '目录表的 xs_comparable 不对 —— demo 那道护栏就没有判据可依'
    import importlib.util as _iu2
    _sp = _iu2.spec_from_file_location(
        '_fac_demo', os.path.join(REPO, 'strategies/_demo/factor_api.py'))
    dm = _iu2.module_from_spec(_sp); _sp.loader.exec_module(dm)

    class _Ctx(object):
        data = gf
    dm._xs_guard(_Ctx(), ['mv_float', 'ep'])          # 可比 -> 必须放行
    try:
        dm._xs_guard(_Ctx(), ['ma20'])
        raise AssertionError('横截面不可比的 ma20 被放行了 —— '
                             '拿它跨票排序排的是"股价 × 上市以来分红拆细"')
    except ValueError as e:
        assert 'ma20' in str(e), '拒了但没点名是哪个因子：%s' % e
    #   demo 必须真的不写 SQL（同 froec_api 那条）
    dsrc = io_open_text(os.path.join(REPO, 'strategies/_demo/factor_api.py'))
    assert 'data.query(' not in dsrc and 'SELECT ' not in dsrc.upper(), \
        'factor_api.py 里还有 SQL —— 它存在的意义就是"不写 SQL"'

    return ('%d 个因子；路径正本只有 paths.py 一处；指纹含 factor（%d 文件，'
            '与 factor_sql 读的是同一批）；PIT 两向都钉（拦 %s / 放行 %s 的 '
            '%d 行）；未知 id 点名报错；ma20+mv 与面板独立算的 %d 只**逐位一致**'
            '（反向自证 MA10 有 %d/%d 行不等）；xs_comparable 两向'
            % (len(gf.factor_ids()), fp['parts']['factor']['n_files'],
               D1, D0, len(past), len(j), nb10, nn10))


@case('serve.py 的 stop/restart：判据是端口而不是 PID 文件', tag='fast')
def t_serve_ctl():
    """`serve.py --status/--stop/--restart`。

    🔴 **不用 PID 文件**：它会陈旧（kill -9 / 机器重启 / 进程崩掉，文件都还在），
      更糟的是 **PID 会被复用** —— 只看"文件里那个号还活着"就 kill，
      有可能杀掉一个刚好复用了这个号的无关进程。
      判据是"现在谁占着这个端口"（同 launchd 那条：判据是现在的状态、不是记录）。
    """
    import subprocess as sp
    here = REPO
    src = open(os.path.join(here, 'serve.py'), encoding='utf-8').read()

    # ---- ① 三个动作都在，而且裸 `serve.py` 仍合法（用 flag 不用子命令）----
    h = sp.run(['python3', 'serve.py', '--help'], cwd=here,
               capture_output=True, text=True, timeout=60).stdout
    for f in ('--status', '--stop', '--restart'):
        assert f in h, 'serve.py 没有 %s' % f
    assert '--readonly' in h and '--live' in h, \
        '旧开关没了 —— "参数不认"会让服务直接起不来'

    # ---- ② 🔴 不许出现 PID 文件 ----
    for bad in ('pidfile', 'pid_file', '.pid'):
        assert bad not in src, \
            ('serve.py 里出现了 %r —— 判据应该是"谁占着端口"，'
             'PID 文件会陈旧、而 PID 会被复用（可能杀错进程）' % bad)

    # ---- ③ 🔴 必须验"是不是我们的进程"，否则会停掉别人的服务 ----
    import importlib.util as _iu
    spec = _iu.spec_from_file_location('_serve_probe',
                                       os.path.join(here, 'serve.py'))
    mod = _iu.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)          # 只是 import，__main__ 不会跑
    except SystemExit:
        pass
    assert mod._ours('python3 serve.py --port 8770'), \
        '_ours 认不出我们自己的命令行'
    for other in ('/usr/bin/nginx -g daemon off',
                  'node /app/server.js', 'python3 -m http.server 8770'):
        assert not mod._ours(other), \
            '_ours 把 %r 也当成我们的了 —— 会停掉别人的服务' % other

    # ---- ④ 🔴 stop 必须【轮询确认真的退出】，不能发完信号就返回 ----
    body = src[src.index('def do_stop'):src.index('if __name__')]
    assert 'SIGTERM' in body and 'SIGKILL' in body, \
        'do_stop 应当先 SIGTERM、超时才升级 SIGKILL'
    #   🔴 要检查**轮询循环体内**有确认，不能只查"文件里出现过" ——
    #     `os.kill(pid, 0)` 在 do_stop 里有两处（轮询 + 强杀后再确认），
    #     只查存在性时把轮询那处删掉照样全绿。
    #     （同 pruned 那条用例踩过的坑：字符串存在性检查抓不到"逻辑被绕过"。）
    poll = body[body.index('while time.time()'):body.index('else:')]
    assert 'os.kill(pid, 0)' in poll, \
        'do_stop 的轮询里没有确认进程退出 —— 「发了信号」不等于「停了」'
    kill9 = body.split('SIGKILL')[-1]
    assert 'os.kill(pid, 0)' in kill9, \
        'SIGKILL 之后没再确认 —— 强杀也可能失败（僵尸、权限）'
    assert '_who(port)' in body.split('SIGKILL')[-1], \
        ('stop 结束前要再确认端口真的空了 —— 进程没了但端口还在 TIME_WAIT 时，'
         '新进程 bind 会报 "Address already in use"，那个报错指不到真原因')

    # ---- ⑤ 🔴 restart 没停干净就不许启动（否则 bind 失败）----
    tail = src[src.index('if a.restart:'):]
    assert 'sys.exit(rc)' in tail.split('---- 重新启动 ----')[0], \
        'restart 在 stop 失败时仍会继续启动 —— 那会 bind 失败'

    # ---- ⑥ --status 取的是 code 子对象，不是顶层 ----
    st = src[src.index('def do_status'):src.index('def do_stop')]
    assert "d.get('code')" in st, \
        ('--status 应从 `code` 子对象取 loaded_at/code_mtime —— 取顶层会'
         '拿到 None，表现是「该不该重启」这三行静默不打印（实测踩过）')

    # ---- ⑦ 空端口上真跑一遍：--status 报未运行、--stop 说本来就没在跑 ----
    #   ★ 用一个**没人用的高端口**，绝不碰 8770（那可能是用户正在用的看板）
    port = '8799'
    r1 = sp.run(['python3', 'serve.py', '--status', '--port', port], cwd=here,
                capture_output=True, text=True, timeout=60)
    assert r1.returncode == 1 and '未在运行' in r1.stdout, \
        '空端口 --status 应报未运行、退出码 1，实际 %s / %r' \
        % (r1.returncode, r1.stdout[:80])
    r2 = sp.run(['python3', 'serve.py', '--stop', '--port', port], cwd=here,
                capture_output=True, text=True, timeout=60)
    assert r2.returncode == 0 and '本来就没在跑' in r2.stdout, \
        '空端口 --stop 应幂等成功，实际 %s / %r' % (r2.returncode, r2.stdout[:80])
    return ('三个动作齐（裸 serve.py 仍合法）；无 PID 文件；'
            '_ours 认自己不认 nginx/node/http.server；'
            'stop 先 TERM 后 KILL 且轮询确认 + 收尾查端口；'
            'restart 没停干净不启动；--status 从 code 子对象取；'
            '空端口上 --status=1 / --stop=0')


@case('selftest 不许写进生产归档（同 live/ 那条纪律）', tag='fast')
def t_no_runs_pollution():
    """2026-09-15 用户："当前在用的 froec_traded 策略里，有大量的
    20260601-20260630 的回测记录，不知道是什么作用。"

    查出来是**测试污染**：「版本页触发回测」那条用例点的就是页面上那个
    「用这个版本跑一次」，而那条链路（`_run_job`）起 `python3 run.py`
    子进程、**照常归档** —— 于是每跑一次 selftest 就往 `runs/` 里塞一条，
    区间参数完全相同，积了 **105 次**，每天都在涨。

    🔴 **不改产品行为**：人在页面上点"跑一次"就是要归档的，给接口加
      `--no-archive` 是修错了地方。要改的是**测试**：`registry` 读
      `ASSAY_RUNS`，而子进程继承环境变量，重定向它就够
      （同 `lv.LIVE` 那条：**selftest 不许写生产数据**）。

    ★ 这条用例钉的是"守卫还在"，真正的证据是跑完之后
      `find runs -name meta.json | wc -l` 不变 —— 那个由 CI/人工核对。
    """
    import io as _io
    import re as _re
    src = _all_case_src()
    cases = [(m.start(), m.group(1)) for m in _re.finditer(r"@case\('([^']+)'", src)]
    cases.append((len(src), None))

    def _owner(pos):
        """这个位置属于哪条用例。"""
        for k in range(len(cases) - 1):
            if cases[k][0] <= pos < cases[k + 1][0]:
                return cases[k][1], cases[k][0], cases[k + 1][0]
        return None, 0, len(src)

    # 🔴 **扫【所有】会触发回测的地方，不是只钉某一条用例。**
    #   我第一版只钉了「版本页」，而真正在归档的是「实盘页面真实渲染」——
    #   两条都点了那个「跑一次」按钮。照清单钉就会这样漏
    #   （同「断言直接扫目录而不是照清单拼」那条）。
    bad = []
    for m in _re.finditer(r"#stbt'\)\.click\(\)", src):
        name, a, b = _owner(m.start())
        seg = src[a:b]
        # 只读模式下点它不会真跑（那是在验"给不给出原因"），跳过
        if 'ALLOW_BACKTEST = True' not in seg and 'sv.ALLOW_BACKTEST' not in seg \
                and 'ALLOW_LIVE = True' not in seg:
            continue
        # 🔴 判据要认准**赋值**那一处，不是"提到过这个名字" ——
        #   `_prev_runs_env = os.environ.get('ASSAY_RUNS')` 也含这个字符串，
        #   于是删掉真正的赋值照样绿（变异实测漏过）。
        if "os.environ['ASSAY_RUNS'] = _runs_tmp" not in seg:
            bad.append(name)
    assert not bad, \
        ('这些用例会**真跑一次回测并归档**，却没把 ASSAY_RUNS 重定向到'
         '临时目录：%s —— 每跑一次 selftest 就污染一条生产归档'
         '（实测积了 105 次同参数的 2026-06-01~06-30）' % sorted(set(bad)))
    # 🔴 **会【删】归档的用例更要重定向**（2026-09-21 加）。上面那一轮只扫
    #   "会触发回测"的 —— 而删除比多写一条严重得多：一跑就毁生产归档。
    #   加批量删除功能时当场发现这个缺口（同「照清单拼会漏掉新的」那条）。
    dbad = []
    for pat in (r"/api/runs/delete", r"api_runs_delete\("):
        for m in _re.finditer(pat, src):
            name, a, b = _owner(m.start())
            seg = src[a:b]
            if name and name.startswith('selftest 不许写进生产归档'):
                continue          # 守卫自己（判据里就含这些字面量）
            if "os.environ['ASSAY_RUNS'] = _runs_tmp" not in seg:
                dbad.append(name)
    assert not dbad, \
        ('这些用例会**真删归档目录**，却没把 ASSAY_RUNS 重定向到临时目录：'
         '%s —— 一跑就毁生产归档，而归档是删不回来的' % sorted(set(dbad)))
    SELF = 'selftest 不许写进生产归档'
    # 🔴🔴 **`picks.json` 那条不在这里扫** —— 判据换成了「跑完之后这个
    #   文件有没有变」，放在 `tests/_base.main()` 里（见那边的注释）。
    #   理由：源码扫描分不出"读"和"写"（实测误报三条只读的用例），
    #   而"跑完文件没变"是**可证的事实**、骗不过去。
    #   ★ 两者分工说得清才留两道；这里留不下，所以只留那一道。
    # 每一处重定向都要在 finally 里还原
    for m in _re.finditer(r"os\.environ\['ASSAY_RUNS'\] = _runs_tmp", src):
        name, a, b = _owner(m.start())
        # ★ 跳过**这条用例自己** —— 它的源码里也含那些字符串（就是上面
        #   那几行判据），扫到自己头上会报"还原不在 finally"这种假失败。
        if name and name.startswith(SELF):
            continue
        seg = src[a:b]
        assert 'finally:' in seg and 'set_runs(_prev_runs)' in \
            seg[seg.rindex('finally:'):], \
            ('%s 里归档目录的还原不在 finally —— 用例失败时会漏还原，'
             '后面一串用例都会跑在临时目录上' % name)
        assert 'rmtree(_runs_tmp' in seg, '%s 没清理临时归档' % name
    n = sum(1 for m in _re.finditer(r"os\.environ\['ASSAY_RUNS'\] = _runs_tmp", src)
            if not (_owner(m.start())[0] or '').startswith(SELF))
    return '%d 条会触发回测的用例都把归档重定向到临时目录且在 finally 还原' % n


@case('清实验归档：结论要留下来，而且不许误删在用的', tag='fast')
def t_prune_experiments():
    """2026-09-15 用户："现在又有大量试验性质的回测记录，清理掉一批，
    保留结论即可。"

    🔴 现成的 `prune_runs.py` **一个都删不掉** —— 它的时间分界是
      「实盘上线日之后全部保留」（本机 2026-09-01），理由是"那天之后的回测
      是在用的"。而这批实验恰恰跑在那之后。
      ★ **那条判据不该改**（它保护的是真在用的），要加的是一条新判据：
        `_` 开头的分组 = 作者自己标出来的"这是试验"
        （项目里跑对照实验一律 `--group _xxx`，正式结论进业务域分组）。

    🔴 另一条更隐蔽：「账户绑定过这个版本」原来把 **304 次**回测全保下来了
      （`ca52ae82` 一个版本就占这么多）。而它回答的只是"这个版本回测过
      没有" —— **每个版本留一次就够**。不收敛的话这条保护把仓库钉死。

    ★ 删之前把 meta+stats 抽进 `runs/_pruned_conclusions.jsonl`：
      tar 备份是**回滚凭据**（要解包才能看），而结论是要**随时查**的
      （"那轮 dev_pct=0.13 到底多少"）。两者用途不同，不能互相替代。
    """
    import io as _io
    import json as _json
    import sys as _sys
    if '.' not in _sys.path:
        _sys.path.insert(0, '.')
    import prune_runs as P

    # ---- ① 判据本身 ----
    assert P._is_exp({'group': '_tm2016'}), '`_` 开头该判为实验组'
    assert not P._is_exp({'group': '小市值'}), '业务域分组不该判为实验组'
    assert not P._is_exp({}), '没有 group 的不该判为实验组'
    # 区间长度：留哪一次靠它
    assert P._span({'start': '2016-01-01', 'end': '2026-01-01'}) > \
        P._span({'start': '2024-01-01', 'end': '2024-12-31'}), \
        '`_span` 没按区间长度比'
    assert P._span({'start': 'x', 'end': 'y'}) == 0, '取不到区间该返回 0 不是抛错'

    # ---- ② 保护判据：在用的一个都不许删 ----
    rows = P.scan()
    keep = P.protected(rows)
    marks = {}
    if os.path.isfile('picks.json'):
        marks = _json.load(_io.open('picks.json', encoding='utf-8'))
    for rid in (marks.keys() if isinstance(marks, dict) else marks):
        assert rid in keep, \
            ('picks.json 标记的 %s 不在保护集里 —— 「★ 选中的规则」那页'
             '读的就是它，删了就查不到了' % rid)
    # 🔴 每个被账户绑定的版本，**至少还留着一次**回测 ——
    #   那是"这个版本回测过没有"的唯一依据。
    binds = set()
    for aid in os.listdir('live'):
        p = os.path.join('live', aid, 'versions.jsonl')
        if not os.path.isfile(p):
            continue
        for ln in _io.open(p, encoding='utf-8'):
            try:
                v = _json.loads(ln)
            except Exception:                                   # noqa: BLE001
                continue
            for k in ('main_sha256', 'code_sha256'):
                if v.get(k):
                    binds.add(v[k][:12])
    have = {}
    for x in rows:
        sha = (x['meta'].get('code_sha256') or '')[:12]
        have.setdefault(sha, []).append(x['rid'])
    _r, _k, drop, _pf = P.plan()
    dropped = {x['rid'] for x in drop}
    for sha in binds:
        if sha not in have:
            continue                    # 这个版本本来就没跑过回测
        left = [r for r in have[sha] if r not in dropped]
        assert left, \
            ('版本 %s 的回测会被删光 —— 实盘页就再也查不到"这个版本回测过"'
             % sha)
    # ★ 但也**不许留太多**：一个版本留一次就够（这正是这次要收敛的）
    for sha in binds:
        if sha not in have:
            continue
        n_keep = sum(1 for r in have[sha]
                     if '账户绑定过这个版本' in keep.get(r, []))
        assert n_keep <= 1, \
            ('版本 %s 有 %d 次回测挂着"账户绑定"这条保护 —— 它只需要留一次，'
             '不收敛的话这条保护会把仓库钉死（实测 ca52ae82 占 304 次）'
             % (sha, n_keep))

    # ---- ③ **真跑一次写入** ----
    # 🔴 只读"已经写好的"结论文件，测不到写入逻辑：`stats` 不存、
    #   去重失效这两种改动照样全绿（变异实测漏过）。所以造一个临时 RUNS
    #   真调一次 `save_conclusions`。
    import shutil as _sh
    import tempfile as _tf
    _tmp = _tf.mkdtemp(prefix='selftest_prune_')
    _old_runs = P.RUNS
    P.RUNS = _tmp
    try:
        sample = [x for x in rows if (x['files'] or {}).get('stats.json')
                  or os.path.isfile(os.path.join(x['dir'], 'stats.json'))][:3]
        assert sample, '没有带 stats.json 的归档可抽'
        cp, n1 = P.save_conclusions(sample)
        assert n1 == len(sample), '抽了 %d 条，样本 %d 个' % (n1, len(sample))
        got = [_json.loads(ln) for ln in _io.open(cp, encoding='utf-8')]
        assert got and got[0].get('stats'), \
            ('结论里没有 stats —— 只存 meta 的话翻出来只知道"跑过"，'
             '不知道结果，等于没留')
        assert any(k in got[0]['stats']
                   for k in ('annual_return', 'total_return')), \
            '结论的 stats 里没有收益率：%s' % list(got[0]['stats'])[:6]
        # 🔴 **幂等**：再抽一次不该重复（重复跑清理会把文件撑大）
        _cp2, n2 = P.save_conclusions(sample)
        assert n2 == 0, '再抽一次又写了 %d 条 —— 不幂等' % n2
        assert len(list(_io.open(cp, encoding='utf-8'))) == len(sample), \
            '结论文件行数变了 —— 去重没生效'
    finally:
        P.RUNS = _old_runs
        _sh.rmtree(_tmp, ignore_errors=True)

    # ---- ④ 结论文件：删过的那些要查得到 ----
    cpath = os.path.join(P.RUNS, os.path.basename(P.CONCLUSIONS))
    if os.path.isfile(cpath):
        ids, bad = set(), []
        for ln in _io.open(cpath, encoding='utf-8'):
            d = _json.loads(ln)
            assert d.get('run_id') and d.get('meta'), '结论行缺 run_id/meta'
            if d['run_id'] in ids:
                bad.append(d['run_id'])
            ids.add(d['run_id'])
            # 🔴 **结论要能回答"那次跑出了什么"** —— 只存 meta 不存 stats
            #   的话，翻出来只知道"跑过"，不知道结果，等于没留
            st = d.get('stats') or {}
            if st:
                assert 'annual_return' in st or 'total_return' in st, \
                    '结论里没有收益率：%s' % d['run_id']
        assert not bad, '结论文件里有重复的 run_id（不幂等）：%s' % bad[:3]
        # 删掉的那些必须都在里面
        live_ids = {os.path.basename(os.path.dirname(m))
                    for m in __import__('glob').glob('runs/*/*/*/meta.json')}
        assert ids, '结论文件是空的'
        assert not (ids & live_ids), \
            ('结论文件里有**还活着**的归档（%s）—— 那说明抽的时机不对，'
             '应该只抽将要删的那些' % list(ids & live_ids)[:3])
        return ('判据齐（_ 开头=实验组、每版本留一次）；'
                'picks/账户绑定都没被删；结论 %d 条可查' % len(ids))
    return '判据齐；picks/账户绑定都没被删（还没清理过，无结论文件）'


@case('归档清理：明细删了必须【明说】，不能显示成空', tag='fast')
def t_pruned():
    """`prune_runs.py` 把旧回测的 holdings.parquet 清掉了（900M -> 153M）。

    🔴 危险在于 `_read()` 读不到 parquet 时返回**空 DataFrame、不报错** ——
      所以"清理过"和"这次回测真的没持仓"在页面上长得一模一样。
      判据链是三段，缺一段就退化成静默：
        ① prune_runs.py 往 meta.json 写 pruned: {'holdings': 日期}
        ② 接口把它带出去（api_holdings / api_day）
        ③ 页面读到就明说，而不是渲染一张空表
    """
    import json as _js
    from assay import server as sv
    here = REPO
    runs = os.environ.get('ASSAY_RUNS') or os.path.join(here, 'runs')

    # ① 找一个被清理的、一个完好的
    pruned = full = None
    for r, _d, fs in os.walk(runs):
        if 'meta.json' not in fs:
            continue
        m = _js.load(open(os.path.join(r, 'meta.json'), encoding='utf-8'))
        if (m.get('pruned') or {}).get('holdings') and not pruned:
            pruned = m.get('run_id')
        elif 'holdings.parquet' in fs and not full:
            full = m.get('run_id')
        if pruned and full:
            break
    if not pruned:
        return '没有被清理的归档 —— 这条用例不适用（prune_runs.py 还没跑过）'

    # ② 接口必须把 pruned 带出去；完好的那次必须是 None（否则判据反了）
    h = sv.api_holdings({'id': pruned, 'limit': '2'})
    assert h.get('pruned'), \
        ('api_holdings 没带 pruned —— 页面会渲染一张空表，'
         '而"清理过"和"没持仓"分不出来')
    assert h.get('total') == 0, '被清理的归档 total 应为 0，实际 %s' % h.get('total')
    if full:
        h2 = sv.api_holdings({'id': full, 'limit': '2'})
        assert not h2.get('pruned'), \
            'holdings.parquet 还在的归档不该有 pruned 标记（判据反了）'
        assert h2.get('total', 0) > 0, '完好的归档应该读到持仓行'

    # ③ 页面必须有处理它的代码
    js = open(os.path.join(here, 'web', 'views', 'run-detail.js'),
              encoding='utf-8').read()
    #   🔴 要匹配**完整的条件语句**，不能只查标识符出现过 ——
    #     提示文本里也有 `${esc(h.pruned)}`，所以把 `if(h.pruned)` 改成
    #     `if(false)` 时"h.pruned in js"照样成立。
    #     实测：第一版就是这么漏过变异测试的（改成 if(false) 仍全绿）。
    flat = js.replace(' ', '').replace('\n', '')
    assert 'if(h.pruned){' in flat and '明细已清理' in js, \
        'run-detail.js 没【按 pruned 分支】—— 清理过的归档会显示成空表'
    assert 'if(o.holdings_pruned){' in flat, \
        '「当天持仓」下钻没按 pruned 分支（api_day 那条链）'
    # ★ 那个「持仓 N 只」的 card 不能显示 0 —— 0 会被读成"那天空仓"
    assert "o.holdings_pruned ? '—'" in js, \
        '「持仓」card 在明细清理时应显示 —，显示 0 会被误读成空仓'

    # ④ 保留集必须完好：标记的、账户绑定的，holdings 都还在
    marks = {}
    mp = os.path.join(here, 'picks.json')
    if os.path.isfile(mp):
        marks = _js.load(open(mp, encoding='utf-8'))
    for rid in marks:
        d = sv._dir(rid)
        assert d is None or os.path.isfile(os.path.join(d, 'holdings.parquet')), \
            ('%s 被 picks.json 标记（「选中的规则」读它）却被清理了 —— '
             'prune_runs.py 的保留集漏了它' % rid)
    n_pruned = n_full = 0
    for r, _d, fs in os.walk(runs):
        if 'meta.json' not in fs:
            continue
        m = _js.load(open(os.path.join(r, 'meta.json'), encoding='utf-8'))
        if (m.get('pruned') or {}).get('holdings'):
            n_pruned += 1
        elif 'holdings.parquet' in fs:
            n_full += 1
    return ('%d 次明细已清理 / %d 次完好；接口带 pruned 且完好的那次为 None；'
            '页面三处都处理了（持仓页 / 当天持仓下钻 / 那个 card 不显示 0）；'
            '%d 条标记的归档 holdings 都还在'
            % (n_pruned, n_full, len(marks)))


@case('交易记录：买卖分行 / 分页 / 点名称弹浮层并定位到那天（playwright）', tag='web')
def t_trades_pane():
    """用户三条：「交易记录应该也要分页」「应该是买、卖各一笔」「点击名称/编码
    弹出个股页面，同时自动定位到那一行所在的日期，前后默认展示 2 个月」。

    每条都对应一种**不报错**的坏法：
      ① 不分页：3946 行一次塞进 DOM —— 页面不报错，只是卡住
      ② 拆分若放在**前端**做：分页边界会错（一页 100 拆完变 200，
         且跨页的买卖被切开）。所以判据是**服务端**返回的就是拆好的
      ③ 买入行的"收益率/持有天/卖出原因"必须**留空**而不是 0 ——
         填 0 会被读成"这笔没赚没亏"（同实盘「费用留空 ≠ 填 0」那条）
      ④ 浮层的 `run` 不带上的话，ETF 回测点开是**一片空白**
         （ETF 跑在平行的 etf_lake 上，主面板里一行都没有）
      ⑤ 定位：目标那天要落在图中段，且窗口≈前后各两个月
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return '跳过（无 playwright）'
    import json as _json
    import threading
    from http.server import ThreadingHTTPServer
    from urllib.request import urlopen

    from assay import server as sv
    from assay.srv import runs as _sr

    # 找一个有成交的归档（优先 ETF —— 它跑在平行 lake 上，最容易暴露 ④）
    _sr._scan()
    with _sr._lock:
        idx = dict(_sr._index)
    cand = []
    for rid, d in idx.items():
        if os.path.exists(os.path.join(d, 'trades.parquet')):
            cand.append((0 if os.sep + 'ETF' + os.sep in d else 1, rid))
    assert cand, '没有带成交记录的归档，测不了'
    cand.sort()
    rid = cand[0][1]

    httpd = ThreadingHTTPServer(('127.0.0.1', 0), sv.Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = 'http://127.0.0.1:%d' % port
    try:
        def get(path):
            return _json.loads(urlopen(base + path, timeout=60).read())

        # ---- ①② 服务端就拆好了，且分页 ----
        t = get('/api/trades?id=%s&limit=10' % rid)
        assert t['limit'] == 10 and len(t['rows']) <= 10, \
            '交易记录没分页：limit=%s 却回了 %d 行' % (t.get('limit'), len(t['rows']))
        assert t['n_buy'] == t['n_sell'] and t['total'] == t['n_buy'] + t['n_sell'], \
            ('一笔平仓必须拆成买、卖【各一行】：total=%d buy=%d sell=%d'
             % (t['total'], t['n_buy'], t['n_sell']))
        raw = _sr._read(rid, 'trades')
        assert t['total'] == 2 * len(raw), \
            '拆分后应是归档行数的两倍（%d vs %d）' % (t['total'], len(raw))
        sides = set(r['side'] for r in t['rows'])
        assert sides <= {'buy', 'sell'}, '方向只能是 buy/sell：%s' % sides
        # 倒序 + 同日先卖后买
        ds = [str(r['date'])[:10] for r in t['rows']]
        assert ds == sorted(ds, reverse=True), '交易记录要按日期倒序：%s' % ds[:5]

        # ---- ③ 买入行不许把"卖出才有的量"填成 0 ----
        big = get('/api/trades?id=%s&limit=400' % rid)
        buys = [r for r in big['rows'] if r['side'] == 'buy']
        sells = [r for r in big['rows'] if r['side'] == 'sell']
        assert buys and sells, '这一页里买卖都要有才测得到（买 %d 卖 %d）' % (len(buys), len(sells))
        for k in ('ret', 'holding_days', 'reason', 'div_tax'):
            bad = [r for r in buys if r.get(k) is not None]
            assert not bad, \
                ('买入行的 `%s` 必须留空 —— 那个量在买入那一刻不存在，'
                 '填 0/填值会被读成"确实是 0"。实得 %r' % (k, bad[0].get(k)))
        assert all(r.get('ret') is not None for r in sells), '卖出行必须有收益率'
        assert all(r.get('price') is not None for r in buys), '买入行必须有价格'

        with sync_playwright() as p:
            b = p.chromium.launch()
            pg = b.new_page(viewport={'width': 1600, 'height': 1000})
            errs = []
            pg.on('pageerror', lambda e: errs.append(str(e)))
            pg.goto('%s/#/run/%s' % (base, rid))
            pg.wait_for_timeout(4500)
            pg.get_by_text('交易记录', exact=True).first.click()
            pg.wait_for_timeout(2500)

            hdr = [h.inner_text().strip() for h in pg.locator('#p3 table th').all()]
            for want in ('日期', '方向', '股票'):
                assert any(want in h for h in hdr), '列头缺「%s」：%s' % (want, hdr)
            assert not any('建仓日' in h or '平仓日' in h for h in hdr), \
                '建仓日/平仓日应合并成「日期」：%s' % hdr
            assert pg.locator('#p3 .pg').count() >= 1, '交易记录没有分页控件'

            # ---- ④⑤ 点名称 -> 浮层 + 定位 ----
            lk = pg.locator('#p3 table a[data-sp]').first
            assert lk.count(), '交易记录里名称不可点'
            want_d = lk.get_attribute('data-spd')
            assert want_d, '链接没带 data-spd —— 浮层无从定位'
            assert lk.get_attribute('data-spr') == rid, \
                ('链接必须带 data-spr（run id）—— 不带的话浮层去主面板取数，'
                 '而 ETF 回测跑在平行 lake 上，会开出一片空白')
            lk.click()
            pg.wait_for_selector('#spwrap', state='visible', timeout=25000)
            pg.wait_for_timeout(2500)
            info = pg.evaluate("""() => ({n: (SP.bars||[]).length,
                center: SP.center, run: SP.run,
                dates: (SP.bars||[]).map(b => b.date)})""")
            assert info['n'] > 20, \
                ('浮层画不出 K 线（%d 根）—— ETF 回测最容易在这里空掉'
                 % info['n'])
            assert info['center'] == want_d, \
                '浮层没收到定位日期：%r vs %r' % (info['center'], want_d)
            # 窗口 ≈ 前后各两个月（84 根 ≈ 4 个月）
            assert 60 <= info['n'] <= 110, \
                '带日期打开时默认窗口应≈前后各两个月，实得 %d 根' % info['n']
            # 🔴 目标那天必须**在窗口里**，且前面留足上下文。
            #   ★ 这里**不能直接断言"居中"**：第一页是最近的成交，而数据就到
            #     那几天为止 —— 目标日之后根本没有两个月的 K 线可显示，窗口被
            #     钳到右边缘是**物理限制不是 bug**（实测落在 82%）。
            #     所以"居中"那一条放到下面用**老成交**验（两侧都有数据）。
            assert want_d in info['dates'], \
                ('定位那天不在窗口里（%s 不在 %s ~ %s）'
                 % (want_d, info['dates'][0], info['dates'][-1]))
            i0 = info['dates'].index(want_d)
            assert i0 >= 30, \
                ('定位那天前面只有 %d 根 K 线 —— "前后各两个月"的【前】那半段'
                 '任何时候都该满足（它不受数据末日影响）' % i0)
            where = '%.0f%%' % (i0 / float(info['n']) * 100)

            # ---- 居中：翻到**末页**（最老的成交），那时两侧都有数据 ----
            pg.keyboard.press('Escape')
            pg.wait_for_timeout(500)
            pg.locator('#p3 .tl').first.click()          # 末页
            pg.wait_for_timeout(2500)
            lk2 = pg.locator('#p3 table a[data-sp]').first
            d2 = lk2.get_attribute('data-spd')
            lk2.click()
            pg.wait_for_selector('#spwrap', state='visible', timeout=25000)
            pg.wait_for_timeout(2500)
            i2 = pg.evaluate(
                "() => ({n: (SP.bars||[]).length,"
                "        dates: (SP.bars||[]).map(b => b.date)})")
            assert d2 in i2['dates'], '老成交也该定位得到：%s' % d2
            pos2 = i2['dates'].index(d2) / float(i2['n'])
            assert 0.3 <= pos2 <= 0.7, \
                ('两侧都有数据时必须**居中**，实得 %.0f%% —— 偏到边上就等于没定位'
                 % (pos2 * 100))
            where += '，老成交 %s 落在 %.0f%%' % (d2, pos2 * 100)
            assert not errs, '页面有运行时错误：%s' % errs[:2]
            b.close()
        return ('拆分 %d 笔平仓 -> %d 行（买 %d/卖 %d）；分页 limit 生效；'
                '买入行的收益率/持有天/卖出原因均留空；'
                '点名称弹浮层、带 run=%s、定位 %s 落在 %s'
                % (len(raw), t['total'], t['n_buy'], t['n_sell'],
                   rid[:8], want_d, where))
    finally:
        httpd.shutdown()


@case('自检拆分：五个域文件都被入口引用，且用例一条不少')
def t_selftest_split():
    """🔴 `selftest.py` 16182 行 -> 入口 40 行 + `tests/` 五个域文件
    （2026-09-17）。切点与 `srv/` / `lv/` / `views/` 同一判据：**按产品域**。

    这条用例守的是拆分本身会烂掉的三种方式，**每一种都不报错**：

      ① **入口漏 import 一个域** —— 那一整域的用例静默消失。报告只会说
         "跑了 8x 条"，而没人记得本来有多少条（同「断言直接扫目录而不是
         照清单拼」那条）。
      ② **用例文件里再写一份框架** —— `case` / `CASES` 各自一份的话，
         注册进的不是同一个列表，同样是静默少用例。
      ③ **`REPO` 被写回 `__file__`** —— 搬进 `tests/` 之后
         `dirname(abspath(__file__))` 指的是 `tests/`，于是 `web/`、`runs/`、
         `strategies/` 全解到不存在的路径（拆 `srv/` 时踩过一模一样的坑）。
    """
    import ast as _ast
    import io as _io
    ent = _io.open(os.path.join(REPO, 'selftest.py'), encoding='utf-8').read()
    mods = ('test_engine', 'test_data', 'test_live', 'test_market', 'test_plat')
    for m in mods:
        assert ('from tests import %s' % m) in ent, \
            ('入口没有 import `tests.%s` —— 那一整域的用例会**静默消失**，'
             '而报告只会说"跑了几条"' % m)
    # ② 各域文件不许自己定义 case/CASES（必须共用 _base 那一份）
    for m in mods:
        fp = os.path.join(REPO, 'tests', m + '.py')
        tree = _ast.parse(_io.open(fp, encoding='utf-8').read())
        for nd in tree.body:
            if isinstance(nd, _ast.FunctionDef) and nd.name == 'case':
                raise AssertionError('%s 自己定义了 case()，用例会注册到别处' % m)
            if isinstance(nd, _ast.Assign) and any(
                    isinstance(t, _ast.Name) and t.id == 'CASES' for t in nd.targets):
                raise AssertionError('%s 自己定义了 CASES' % m)
    # ③ 用例文件里不许再用 `dirname(abspath(__file__))` 当仓库根
    # 🔴 要找的那个字符串**拼出来**，不写成字面量 —— 否则这条判据的源码
    #   自己就含着它，扫到自己头上报假失败（同「扫描要跳过守卫用例自己」
    #   那条；实测第一次跑就这么挂了）。
    _pat = 'os.path.dirname(' + 'os.path.abspath(' + '__file__))'
    bad = []
    for m in mods:
        src = _io.open(os.path.join(REPO, 'tests', m + '.py'), encoding='utf-8').read()
        if _pat in src:
            bad.append(m)
    assert not bad, \
        ('%r 里还在用 `dirname(abspath(__file__))` 当仓库根 —— 文件搬进 '
         '`tests/` 之后它指的是 `tests/`，`web/` `runs/` `strategies/` 全会'
         '解到不存在的路径（拆 srv/ 时踩过一模一样的坑）。用 `REPO`。' % bad)
    # REPO 必须真的是仓库根
    assert os.path.exists(os.path.join(REPO, 'selftest.py')) \
        and os.path.isdir(os.path.join(REPO, 'web')), \
        'REPO 解错了：%s' % REPO
    # 每个域都得有用例，且合起来就是全部
    from tests._base import CASES as _C
    assert len(_C) >= 100, '注册到的用例只有 %d 条' % len(_C)
    per = {}
    for m in mods:
        src = _io.open(os.path.join(REPO, 'tests', m + '.py'), encoding='utf-8').read()
        per[m] = sum(1 for l in src.splitlines() if l.startswith('@case('))
        assert per[m] > 0, '%s 一条用例都没有 —— 域切空了' % m
    assert sum(per.values()) == len(_C), \
        ('五个域文件里有 %d 条 `@case`，实际注册 %d 条 —— 对不上说明有用例'
         '没被 import 到，或者有人在别处又注册了一条'
         % (sum(per.values()), len(_C)))
    return '入口引用 5 个域（%s），共 %d 条用例，REPO 指向仓库根' % (
        ' '.join('%s=%d' % (m.replace('test_', ''), per[m]) for m in mods), len(_C))


@case('看板页面清单：每个路由都有实现', tag='fast')
def t_page_inventory():
    """★ 这条用例的存在理由是一次真实事故：重构实盘页时，我用「切掉
    A 函数到 B 函数之间」的方式删代码，**顺手把整个数据同步页
    （showSync/pollSync）一起切掉了**，同时也切掉了 selftest 里夹在两个
    web 用例之间的一条用例。

    两个失败都不响亮：
      · 页面：只有点进 #/sync 才会看到 "showSync is not defined"，
        而 --fast 层不起浏览器，压根跑不到那儿
      · 用例：总数从 42 变 41，而没有任何东西核过总数

    所以这里核【路由与实现的对应关系】和【用例总数】——
    删代码时至少有一处会立刻叫。
    """
    # 🔴 selftest 不许往【真账本】里写。live/ 下那些 jsonl 是 append-only 的
    #    决策记录（成交流水 / 自选 / 费率 / 版本），被测试写进去就再也分不清
    #    哪条是真的 —— 而 append-only 意味着不能删。
    #    实测踩过：个股用例真点了那颗自选星，每跑一次留一对 add/remove，
    #    攒了 11 对才在提交时看到 live/watchlist.jsonl 变更。
    #    用例要写就重定向到临时目录（`wlmod.LIVE = tmp` / `lv.LIVE = tmp`）。
    here = REPO
    src_all = _all_case_src()
    # 🔴 **扫出来，不照清单拼**（2026-09-21 改）。原来这里是一张手写的
    #   `(用例名, marker)` 清单 —— 新加一条会写账本的用例时它**自动不在
    #   保护范围里，而那不报错**（正是「照清单拼会漏掉新文件」那条，
    #   这次加 symbols 守卫时当场撞上）。
    #   现在的判据：**凡是源码里调了 `act(` 写账本的用例，必须也重定向 LIVE**。
    _blocks, _idx = [], 0
    while True:
        i = src_all.find("@case(", _idx)
        if i < 0:
            break
        j = src_all.find('\n@case(', i + 6)
        _blocks.append(src_all[i:(j if j > 0 else len(src_all))])
        _idx = i + 6
    _bad = []
    for blk in _blocks:
        nm = blk[7:blk.find("'", 8)] if blk[6] == "'" else blk[6:40]
        writes = [m for m in ('wl.act(', 'wlmod.act(', 'al.act(', 'almod.act(')
                  if m in blk]
        if writes and '.LIVE = ' not in blk:
            _bad.append('%s（调了 %s）' % (nm[:34], '/'.join(writes)))
    assert not _bad, ('这些用例会写 live/ 下的 append-only 账本却没重定向 '
                      'LIVE —— 会污染真账本，而 append-only 意味着删不掉：\n  '
                      + '\n  '.join(_bad))
    # 反向自证：真的扫到了那几条会写账本的用例（否则这条是空转的）
    _writers = sum(1 for b in _blocks
                   if any(m in b for m in ('wl.act(', 'wlmod.act(', 'al.act(')))
    assert _writers >= 5, '只扫到 %d 条写账本的用例 —— 扫描器坏了' % _writers

    web = os.path.join(here, 'web')
    html = open(os.path.join(web, 'index.html'), encoding='utf-8').read()

    # ---- index.html 是【骨架】：hash 路由的几个视图各在自己的 .js 里 ----
    #   ★ 拆分依据是**产品域**（app/home/live/runs/sync），不是技术分层 ——
    #     依赖分析显示跨域调用几乎全是"路由 -> 视图"这一个方向。
    #   ★ 断言只钉"文件在、被引用、没重名"这三件，不钉具体行数 ——
    #     行数会随功能长，钉了只会天天误报。
    #   ★ 这张表是【唯一】的清单：加了新域文件就往这里加一行 ——
    #     分两处写的话，"新文件没被 index.html 引用"或"断言没扫到它"
    #     都不会报错，只是那部分功能悄悄不在保护范围内。
    # 🔴 **扫目录，不照清单拼** —— 新增一个视图文件（如 live-perf.js）时，
    #   写死的清单不会跟着变，于是它的 showXxx 定义不在 `js` 里、
    #   "被调用但没有定义"就误报；更糟的是它的所有检查都悄悄漏掉了，
    #   而**那不报错，只是保护范围缩小**（同 _web_files 那条）。
    DOMS = tuple(sorted(
        f[:-3] for f in os.listdir(os.path.join(web, 'views'))
        if f.endswith('.js')))
    assert 'app' in DOMS and len(DOMS) >= 10, \
        'views/ 下的域文件数看着不对：%s' % (DOMS,)
    for d in DOMS:
        fp = os.path.join(web, 'views', d + '.js')
        assert os.path.isfile(fp), \
            '缺 web/views/%s.js（index.html 拆分出来的域）' % d
        assert '/views/%s.js' % d in html, \
            'index.html 没引用 views/%s.js' % d
    assert '<script>' not in html, \
        ('index.html 里又出现内联 <script> —— 它应该只是骨架，'
         '视图逻辑放到对应的域文件里')
    js = '\n'.join(open(os.path.join(web, 'views', d + '.js'),
                        encoding='utf-8').read() for d in DOMS)

    # 🔴 【跨文件顶层重名 = 整页 SyntaxError】。所有 <script>（含 src= 引入的）
    #    共享同一个全局词法环境，重名直接
    #    "Identifier 'x' has already been declared" ——
    #    表现是**整页白屏、所有功能一起没了**。
    #    实测踩过：把 num() 搬进 common.js 时忘了删 index.html 那份，
    #    8 个 web 用例一起挂。
    #    ★ 拆成 5 个域文件后组合数从 1 对变成 21 对，所以这里**两两全比**，
    #      而不是只比"每个页面 vs common.js"。
    top = lambda t: set(re.findall(
        r'^(?:const|let|var|function|async function)\s+([A-Za-z_$][\w$]*)',
        t, re.M))
    import itertools
    # ★ 直接扫目录，而不是照着 DOMS 拼 —— 漏掉一个文件的话，
    #   "两两比对"就漏了它的所有组合，而那不会报错。
    shared_files = _web_files(web, '.js')
    want = set(os.path.join('views', d + '.js') for d in DOMS)
    assert want <= set(shared_files), \
        'DOMS 里列的文件不存在：%s' % sorted(want - set(shared_files))
    syms = {f: top(open(os.path.join(web, f), encoding='utf-8').read())
            for f in shared_files}
    for a, b in itertools.combinations(shared_files, 2):
        dup = sorted(syms[a] & syms[b])
        assert not dup, \
            ('%s 与 %s 顶层重名 %s —— 会 SyntaxError 导致整页白屏' % (a, b, dup))
    # 独立页面（自带内联 <script>）仍要与所有共享文件比
    allshared = set().union(*syms.values())
    # 🔴 `.html` 一律在**根目录** —— `/stock.html?code=…` 是外部书签与跨页
    #   链接的地址，属于产品契约。这里断言它没被挪进子目录：挪了的话旧书签
    #   全部 404，而"点了没反应"是最难查的那种坏。
    pages = sorted(f for f in os.listdir(web)
                   if f.endswith('.html') and f != 'index.html')
    stray = [f for f in _web_files(web, '.html') if os.sep in f]
    assert not stray, '.html 必须留在 web/ 根目录（产品契约）：%s' % stray
    # ★ 反过来：`.js`/`.css` 一律**不许**平铺在根 —— 否则下次新加的文件又会
    #   散在根目录，而"目录结构慢慢退化"没有任何报错。
    #   shared/ = 跨所有页面共享（含 6 个独立 .html）；views/ = 只服务 index.html
    #   的 hash 视图。分目录依据仍是**产品域**，与 index.html 的拆分同一判据。
    flat = [f for f in os.listdir(web) if f.endswith(('.js', '.css'))]
    assert not flat, \
        ('web/ 根目录不该有 .js/.css：%s —— 共享的放 shared/，'
         'index.html 的视图放 views/' % sorted(flat))
    for fn in pages:
        src = open(os.path.join(web, fn), encoding='utf-8').read()
        inline = '\n'.join(re.findall(r'<script>(.*?)</script>', src, re.S))
        dup = sorted(top(inline) & allshared)
        assert not dup, \
            ('%s 与共享 .js 顶层重名 %s —— 会 SyntaxError 导致整页白屏'
             % (fn, dup))
    # 每个独立页面都必须引用共享资源，不能各带一份样式/辅助函数
    for fn in pages + ['index.html']:
        src = open(os.path.join(web, fn), encoding='utf-8').read()
        assert '/shared/common.css' in src, \
            '%s 没引用 shared/common.css' % fn
        assert '/shared/common.js' in src, \
            '%s 没引用 shared/common.js' % fn
        assert '<style>' not in src, \
            '%s 里还有内联 <style> —— 样式应集中在 common.css' % fn

    # route() 里出现的每个 showXxx()，都必须有对应的 function 定义
    called = set(re.findall(r'\b(show[A-Z]\w*)\s*\(', js))
    defined = set(re.findall(r'(?:async\s+)?function\s+(show[A-Z]\w*)\s*\(', js))
    missing = sorted(called - defined)
    assert not missing, \
        ('这些页面函数被调用但没有定义 —— 页面会白屏且只在点进去时才报错：%s'
         % missing)

    # 每个 hash 路由都要有入口
    for route, fn in (('#/live', 'showLive'), ('#/sync', 'showSync'),
                      ('#/docs', 'showDocs'), ('#/picks', 'showPicks'),
                      ('#/runs', 'showCatalog'), ('#/stock', None)):
        assert route in js, '路由 %s 不见了' % route
        # fn=None：这个路由只做跳转（个股已搬到独立页 /stock.html），
        # 本文件里没有对应的 showXxx 实现
        if fn:
            assert fn in defined, '%s 的实现 %s 不见了' % (route, fn)
    # 🔴🔴 成交流水与选股理由 2026-09-22 **并进了业绩页的页签**（用户：
    #   「流水按钮和业绩里的交易记录有所重复，应该可以合并」「选股理由应该
    #   也内置到业绩里」）。所以这两条断言的**前提没了** ——
    #   **失败的是断言不是产品**，但它原本要保的东西一条都不能丢：
    #   那两个 hash 是书签与持仓行那个 `?` 的地址，**属于产品契约**。
    #   新判据 = 「路由还认得它们，且跳到业绩页对应的页签」。
    assert 'showFills' not in defined, \
        ('`showFills` 该删干净了 —— 它已经没有调用方（并进「交易明细」页签），'
         '留一个没人用的渲染函数，下次有人会以为它是正本')
    assert 'showWhy' in defined, '选股理由的渲染函数不见了'
    # ★ 判据落在**那条路由自己那一段**上（从它的正则到下一个 return），
    #   不在全文里找 —— 全文找的话，别处随便一个 `tab=fills` 也算命中。
    for _old, _tab in (('fills', 'tab=fills'), ('why', 'tab=why')):
        _i = js.find('/' + _old + '(?:')
        assert _i > 0, '旧 hash #/live/<id>/%s 的路由整条没了' % _old
        _seg = js[_i:js.find('return;', _i) + 7]
        assert 'location.replace(' in _seg and _tab in _seg, \
            ('旧 hash #/live/<id>/%s 没有 redirect 到业绩页的 %s —— 书签会 404，'
             '而"点了没反应"是最难查的那种坏。那一段：%r' % (_old, _tab, _seg[:200]))
    # ★ 只查"提到过 tab=fills"是不够的：要的是**跳转**。而 `location.replace`
    #   不是赋值 —— 赋值会往历史里塞一条，按后退跳回旧地址又被弹回来，
    #   人就退不出去了。
    # 🔴 后台复算时页面必须**说出来**并自己回来看：不说的话那一段就是一句
    #   "没有理由"，人会当成功能坏了；不轮询的话它会一直停在"正在复算…"，
    #   **两种都不报错**。
    # 🔴 断言要匹配**完整条件**，不能只查标识符在不在：`explain_pending`
    #   在轮询那行也出现，只查名字的话把 `if(p.explain_pending){` 改成
    #   `if(false){` 照样全绿（同 prune_runs 的 `h.pruned` 那条）。
    _js0 = re.sub(r'\s+', '', js)
    assert 'if(p.explain_pending){' in _js0 and 'whypend' in js, \
        '选股理由页没有"正在事后复算"这一态 —— 历史那几期会显示成"没有理由"'
    assert re.search(r'explain_pending\)[\s\S]{0,400}?setTimeout', js), \
        '有后台复算在跑却不自动回来看 —— 页面会一直停在"正在复算…"'

    # 用例总数 —— 删代码时把整条用例切掉过一次
    n = len(CASES)
    assert n >= 54, \
        ('用例只剩 %d 条，少于已知的 54 —— 是不是删代码时把某条一起切掉了？'
         '用 `git show HEAD:selftest.py | grep "^@case"` 对一下' % n)
    # ★ 数字让它自己算 —— 写死的话下次再拆还得手改，而"忘了改"的表现是
    #   报告串在说谎（它看着像验过了）。
    return ('index.html 拆成 %d 个域文件且全被引用、骨架里无内联 script；'
            '%d 个共享 .js 两两无顶层重名（%d 对）；'
            '%%d 个页面函数与路由一一对应（%%s）；用例 %%d 条'
            % (len(DOMS), len(shared_files),
               len(shared_files) * (len(shared_files) - 1) // 2)
            % (len(defined), ' '.join(sorted(defined)), n))




@case('策略比对页：口径不一致要响亮报出来 / 逐年数字与详情页同源（playwright）', tag='web')
def t_run_cmp():
    """⚖ `#/cmp/<idA>,<idB>` —— 2~4 次回测摆一起比。

    🔴🔴 **这一页最值钱的不是曲线，是「口径不一致要说出来」。**
      本项目两次栽在「拿滑点 0 的数字比含滑点的基准」（FROEC 与 v0b 各一次），
      2026-09-17 又踩了「基线截 08-07、变体截 09-17」。没有那一层，
      这页就是一台生产错误结论的机器。

    ★ 三条关键判据全部**构造**（`pg.route` 拦接口），不靠盘上恰好有
      成本不同/区间不同的归档 —— 「判据依赖盘上恰好有什么」这个陷阱
      本项目踩过两次（剪过的 holdings、从没创建的 `runs/_fqtest/`）。
    """
    import json as _json
    import threading
    from http.server import ThreadingHTTPServer

    from playwright.sync_api import sync_playwright

    from assay import server as sv
    sv._scan()
    _JS_ENTRY = '''() => {
      const g = document.querySelector('#cmpbar #cmpgo');
      if (!g) return {ok: false, why: '归档目录没有「⚖ 比对」按钮'};
      const cs = getComputedStyle(g);
      return {ok: true, vis: !!(g.offsetWidth && g.offsetHeight),
              border: cs.borderTopStyle, cursor: cs.cursor,
              boxes: document.querySelectorAll('.runs td.cmpck input[data-cmp]').length};
    }'''
    _JS_RD = '''() => { const e = document.querySelector('#rdcmp');
      const c = getComputedStyle(e);
      return {border: c.borderTopStyle, cursor: c.cursor}; }'''
    _JS_LAND = '''(rid) => {
      const row = document.querySelector('.runs tr[data-id="' + rid + '"]');
      const vis = e => !!(e && e.offsetWidth && e.offsetHeight);
      return {sel: (typeof CMPSEL !== 'undefined' ? CMPSEL : null),
              rowVis: vis(row),
              boxVis: [...document.querySelectorAll('.runs td.cmpck input[data-cmp]')]
                        .filter(vis).length};
    }'''
    _JS_YEAR = '''() => {
      const s1 = document.querySelector('#cmpyb1 svg'), s2 = document.querySelector('#cmpyb2 svg');
      const yr = [...s1.querySelectorAll('text.ax')].filter(t => /^\\d{4}$/.test(t.textContent));
      return {bars1: s1.querySelectorAll('rect[fill]').length,
              bars2: s2.querySelectorAll('rect[fill]').length,
              paths1: s1.querySelectorAll('path').length,
              zero1: s1.querySelectorAll('.zl').length,
              zero2: s2.querySelectorAll('.zl').length,
              same2: s1 !== s2,
              nyear: yr.length,
              left1: Math.round(s1.getBoundingClientRect().left),
              left2: Math.round(s2.getBoundingClientRect().left)};
    }'''

    # ---- ① 纯函数：区间起点就是序列起点时，基点必须是 1 ----
    #   `/api/equity` 给的已经是「除以初始资金」的净值，`v[0]` 含首日盈亏。
    #   拿 `v[0]` 当基点 = 把首日涨跌抹掉、首日恒 0.00%
    #   —— 那正是实盘基准线「选了 ETF 第一天收益是 0」的根因（lprSlice）。
    src = open(os.path.join(REPO, 'web/views/run-cmp.js'), encoding='utf-8').read()
    assert 'function cmpBase' in src, '少了归一化基点函数'

    # ---- 挑两次【同区间同成本】的真实归档（扫目录，不写死 run_id）----
    metas = []
    for m in glob.glob(os.path.join(REPO, 'runs/*/*/*/meta.json')):
        d = os.path.dirname(m)
        if not os.path.exists(os.path.join(d, 'equity.parquet')):
            continue
        try:
            o = _json.load(open(m, encoding='utf-8'))
        except Exception:                                   # noqa: BLE001
            continue
        metas.append((os.path.basename(d), o))
    # 🔴 **挑区间最长的那一对** —— 随便挑一对会挑到一年期的实验归档，
    #   于是逐年表只有 1 行、"逐年数字同源"那条等于没测（护栏当场抓到过）。
    #   这就是「判据依赖盘上恰好有什么」那个陷阱，本项目踩过两次。
    key = lambda o: (o.get('start'), o.get('end'), o.get('cash'),
                     _json.dumps(o.get('cost'), sort_keys=True))
    cand = []
    for i in range(len(metas)):
        for k in range(i + 1, len(metas)):
            a, b = metas[i][1], metas[k][1]
            if key(a) == key(b) and a.get('params') != b.get('params'):
                cand.append((str(a.get('end') or ''), str(a.get('start') or ''),
                             metas[i][0], metas[k][0]))
    # 跨的年份越多越好：按 (起点最早, 终点最晚) 排
    cand.sort(key=lambda c: (c[1], [-ord(x) for x in c[0]]))
    if not cand:
        return '跳过（找不到两次同区间同成本的归档）'
    pair = (cand[0][2], cand[0][3])

    httpd = ThreadingHTTPServer(('127.0.0.1', 0), sv.Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    notes = []
    try:
        with sync_playwright() as p:
            try:
                br = p.chromium.launch()
            except Exception as e:                          # noqa: BLE001
                return '跳过（浏览器不可用: %s）' % type(e).__name__
            pg = br.new_page(viewport={'width': 1500, 'height': 1200})
            errs = []
            pg.on('pageerror', lambda e: errs.append(str(e)))
            base = 'http://127.0.0.1:%d/' % port

            # ---- ② 真实两次：曲线真的画出来 + 逐年数字与 perfBuckets 同源 ----
            pg.goto(base + '#/cmp/' + ','.join(pair), wait_until='networkidle')
            # 🔴 逐年数字表**默认收起**（2026-09-17 改成柱状图之后，图看形状、
            #   表看精确值）—— 这里先展开它，顺带把折叠开关也测了。
            #   不展开的话 `wait_for_selector` 等一个 `display:none` 的元素，
            #   超时 30 秒看着像页面坏了（同分页签那次"元素落进未激活的 pane"）。
            pg.wait_for_selector('#cmpyb1 svg', timeout=30000)
            assert pg.eval_on_selector('#cmpyr', 'e => e.style.display') == 'none', \
                '逐年数字表该默认收起（图在上、表备查）'
            pg.click('#cmpyrtb')
            pg.wait_for_selector('#cmpyr table', timeout=30000)
            n1 = pg.eval_on_selector_all('#cmpc1 svg path[fill="none"]', 'a=>a.length')
            n2 = pg.eval_on_selector_all('#cmpc2 svg path[fill="none"]', 'a=>a.length')
            assert n1 == 2 and n2 == 2, '主图/回撤图的曲线条数 %s/%s（应各 2 条）' % (n1, n2)
            # 🔴 逐年表必须 == 页面内 `perfBuckets` 对同一份 equity 算的结果。
            #   自己再算一遍的话会出现「详情页说 2024 +29.43%、比对页 +29.4x%」。
            chk = pg.evaluate("""async (ids) => {
              const e = await j('/api/equity?id=' + encodeURIComponent(ids[0]));
              const y = perfBuckets(e.dates, e.equity, null).years;
              const rows = [...document.querySelectorAll('#cmpyr tr')].slice(2);
              const out = [];
              for (const tr of rows) {
                const td = tr.querySelectorAll('td');
                const yr = td[0].textContent.trim();
                if (!(yr in y)) continue;
                out.push([yr, td[1].textContent.trim(),
                          (y[yr].ret * 100).toFixed(2) + '%']);
              }
              return out;
            }""", list(pair))
            assert len(chk) >= 3, '逐年表只有 %d 行 —— 判据在空转' % len(chk)
            bad = [c for c in chk if c[1] != c[2]]
            assert not bad, '逐年收益与 perfBuckets 对不上（前 3）：%s' % bad[:3]
            # 🔴 **参数不同不许报警** —— 这一对就只有参数不同（比对的正是它）。
            #   把它算进警告的话每次比对都亮一条红字，于是人就不看这个位置了
            #   （同「常驻一条『一切正常』的横幅等于教人忽略这个位置」）。
            #   它只在口径卡里标成中性的 .cmpdif。
            pdiff = pg.evaluate("""() => ({
              warn: !!document.querySelector('#cmp .cmpwarn'),
              bad: [...document.querySelectorAll('#cmp .cmpax tr.cmpbad td:first-child')]
                     .map(x => x.textContent.trim()),
              dif: [...document.querySelectorAll('#cmp .cmpax tr.cmpdif td:first-child')]
                     .map(x => x.textContent.trim()),
            })""")
            assert '参数' in pdiff['dif'], '参数不同没标成中性「比对项」：%s' % pdiff
            assert not pdiff['warn'], '只有参数不同却弹了警告条 —— 假告警，看多了就不看告警了'
            assert '参数' not in pdiff['bad'], '参数被当成口径问题标红了：%s' % pdiff['bad']
            notes.append('真实两次：主图/回撤各 2 条曲线，%d 个年度格与 perfBuckets 逐位相同；'
                         '参数不同只标中性不报警' % len(chk))

            # ---- ③ 构造：成本不同 -> 必须标红并点名 ----
            def _stub(pg_, cost_b=None, end_b=None):
                """拦 /api/run 与 /api/equity，造两次可控的回测。"""
                D = ['2024-01-0%d' % i for i in range(1, 6)] + \
                    ['2025-01-0%d' % i for i in range(1, 6)]
                eq = {'A': {'dates': D[:], 'equity': [1.0, 1.1, 1.05, 1.2, 1.3, 1.35, 1.2, 1.4, 1.5, 1.6]},
                      'B': {'dates': D[:], 'equity': [1.0, 1.05, 1.0, 1.1, 1.15, 1.2, 1.1, 1.25, 1.3, 1.35]},
                      'C': {'dates': D[:], 'equity': [1.0, 1.02, 1.0, 1.05, 1.1, 1.12, 1.05, 1.18, 1.22, 1.3]}}
                if end_b:
                    eq['B']['dates'] = eq['B']['dates'][3:]
                    eq['B']['equity'] = eq['B']['equity'][3:]
                cost_a = {'slippage': 0.0015, 'commission': 0.00025,
                          'min_commission': 5, 'close_tax': 'auto'}
                # ★ 参数串写得跟真实的一样长（实盘那套就是 5 个）——
                #   列被挤出去只在"参数很长 + 3 列"时发生，短参数测不到。
                LP = {'lu_buy_only': 1, 'lu_since_start': 1, 'pb_pct': 0.5,
                      'stop_intraday': 1, 'stop_loss': 0.35, 'weekday': 2}
                mk = lambda pb, c: {'strategy_path': 's/froec_cutgrid.py',
                                    'params': dict(LP, pb_pct=pb), 'start': '2024-01-01',
                                    'end': '2025-01-05', 'cash': 100000, 'cost': c,
                                    'data_fingerprint': {'overall': 'ffff0000'}}
                meta = {'A': mk(0.5, cost_a), 'B': mk(0.7, cost_b or cost_a), 'C': mk(1.0, cost_a)}
                if end_b:
                    meta['B']['start'] = eq['B']['dates'][0]
                st = {'total_return': 0.6, 'annual_return': 0.5, 'max_drawdown': 0.12,
                      'sharpe': 1.2, 'n_trades': 10, 'win_rate': 0.6}

                def h(route):
                    u = route.request.url
                    k = 'C' if 'id=C' in u else ('B' if 'id=B' in u else 'A')
                    if '/api/equity' in u:
                        route.fulfill(status=200, content_type='application/json',
                                      body=_json.dumps(eq[k]))
                    else:
                        route.fulfill(status=200, content_type='application/json',
                                      body=_json.dumps({'meta': meta[k], 'stats': st}))
                pg_.route('**/api/run?*', h)
                pg_.route('**/api/equity?*', h)

            _stub(pg, cost_b={'slippage': 0.0, 'commission': 0.00025,
                              'min_commission': 5, 'close_tax': 'auto'})
            pg.goto(base + '#/cmp/A,B', wait_until='networkidle')
            pg.wait_for_selector('#cmp .cmpwarn', timeout=20000)
            w = pg.inner_text('#cmp .cmpwarn')
            assert '成本口径' in w, '成本不同却没点名：%r' % w[:120]
            redrows = pg.eval_on_selector_all(
                '#cmp .cmpax tr.cmpbad td:first-child', 'a=>a.map(x=>x.textContent.trim())')
            assert '成本口径' in redrows, '成本那一行没标红：%s' % redrows
            assert '区间' not in redrows and '本金' not in redrows, \
                '一致的项也被标红了：%s' % redrows
            notes.append('成本不同：警告条点名 + 只有那一行标红（%s）' % redrows)

            # ---- ④ 构造：区间不同 -> 取交集 + 按交集起点重新归一化 ----
            pg.unroute('**/api/run?*'); pg.unroute('**/api/equity?*')
            _stub(pg, end_b=True)
            # 🔴 `goto` 到【同一个 hash】不会重新加载页面（CLAUDE.md 记过）——
            #   不 reload 的话这一步看到的还是上一步的渲染，三条断言全是空转。
            pg.goto(base + '#/cmp/A,B', wait_until='networkidle')
            pg.reload(wait_until='networkidle')
            pg.wait_for_selector('#cmp .cmpwarn', timeout=20000)
            # 🔴 判据取**标红的那几行**，不查警告条里有没有"区间"两个字 ——
            #   警告条的固定尾句就是「请用同一区间、同一成本重跑」，
            #   查字符串的话它永远命中（第一版就是这么空转的）。
            red2 = pg.eval_on_selector_all(
                '#cmp .cmpax tr.cmpbad td:first-child', 'a=>a.map(x=>x.textContent.trim())')
            assert '区间' in red2, '区间不同却没标红：%s' % red2
            assert '成本口径' not in red2, '成本一致却被标红：%s' % red2
            got = pg.evaluate("""() => {
              const xs = [...document.querySelectorAll('#cmpc1 svg text.ax')]
                  .map(t => t.textContent.trim()).filter(t => /^\\d{2}-\\d{2}|\\d{4}/.test(t));
              const note = document.querySelector('#cmp .lvsec .note').textContent;
              return {x0: xs[0] || '', note: note};
            }""")
            assert '2024-01-04' in got['note'], \
                '没把实际比的区间写出来：%r' % got['note'][:140]
            # 🔴 重新归一化的硬判据：两条线在交集首日必须【都】从 1 出发 ——
            #   不重新归一化的话 y 轴写的百分比其实是各自全程的收益（lprSlice 那条）。
            v0 = pg.evaluate("""() => {
              const e = {A:[1.0,1.1,1.05,1.2,1.3,1.35,1.2,1.4,1.5,1.6],
                         B:[1.2,1.3,1.35,1.2,1.4,1.5,1.6]};
              return [cmpBase(e.A, 3), cmpBase(e.B, 0)];
            }""")
            assert abs(v0[0] - 1.05) < 1e-9, 'i0>0 时基点该取前一点 1.05，得到 %s' % v0[0]
            assert v0[1] == 1, 'i0=0 时基点必须是 1（不是 v[0]）—— 首日涨跌会被抹掉，得到 %s' % v0[1]
            notes.append('区间不同：点名 + 图按交集画 + 基点 i0>0 取前一点 / i0=0 取 1')

            # ---- ④b 口径卡不许把最后一列挤出去（3 列时参数串很长）----
            #   🔴 判据是 **body 不横滚** + 表格装得进 .pw：宽表自己在 .pw 里滚是
            #     对的，body 横滚不是（读表格时整页左右晃）。参数那一格没有
            #     `white-space:normal` + 宽度上限的话，3 列时 C 列直接被切掉，
            #     **而它不报错**（同「参数列要有宽度上限且不 nowrap」那条）。
            #   🔴 **必须用 3 条来测**：2 列时表格本来就装得下，
            #     去掉换行规则也不会溢出 —— 变异实测漏过一次
            #     （同「断言要在能触发的构造上跑」那条）。
            pg.unroute('**/api/run?*'); pg.unroute('**/api/equity?*')
            _stub(pg)
            pg.goto(base + '#/cmp/A,B,C', wait_until='networkidle')
            pg.reload(wait_until='networkidle')
            pg.wait_for_selector('#cmp .cmpax', timeout=20000)
            ncol = pg.eval_on_selector_all('#cmp .cmpax tr:first-child th', 'a=>a.length')
            assert ncol == 4, '构造的不是 3 列（表头 %d 格）—— 判据在空转' % ncol
            geo = pg.evaluate("""() => ({
              tbl: Math.round(document.querySelector('#cmp .cmpax').getBoundingClientRect().width),
              pw: document.querySelector('#cmp .pw').clientWidth,
              body: document.body.scrollWidth - document.body.clientWidth,
            })""")
            assert geo['tbl'] <= geo['pw'] + 1, (
                '口径卡 %dpx 装不进容器 %dpx —— 最后一列会被切掉' % (geo['tbl'], geo['pw']))
            pg.set_viewport_size({'width': 1024, 'height': 900})
            pg.wait_for_timeout(200)
            ov = pg.evaluate('document.body.scrollWidth - document.body.clientWidth')
            assert ov <= 0, '1024 宽下 body 横滚了 %dpx —— 宽表该自己在 .pw 里滚' % ov
            pg.set_viewport_size({'width': 1500, 'height': 1200})
            notes.append('口径卡装得下且窄屏 body 不横滚')

            # ---- ⑤ picks 页勾选上限：超了要【说一句】，不许静默不动 ----
            pg.unroute('**/api/run?*'); pg.unroute('**/api/equity?*')
            pg.goto(base + '#/picks', wait_until='networkidle')
            pg.wait_for_selector('#pk input[data-cmp]', timeout=20000)
            lim = pg.evaluate("""() => {
              const cbs = [...document.querySelectorAll('#pk input[data-cmp]')];
              CMPSEL = [];
              const n = Math.min(cbs.length, CMP_MAX + 1);
              for (let i = 0; i < n; i++) cbs[i].click();
              return {have: cbs.length, sel: CMPSEL.length, max: CMP_MAX,
                      msg: (document.querySelector('#cmpbar') || {}).textContent || ''};
            }""")
            if lim['have'] > lim['max']:
                assert lim['sel'] == lim['max'], \
                    '勾了 %d 个，CMPSEL 却有 %d（上限 %d）' % (lim['have'], lim['sel'], lim['max'])
                assert '最多' in lim['msg'], '到上限没说一句：%r' % lim['msg'][:100]
                notes.append('勾选上限 %d 生效且有提示' % lim['max'])
            else:
                notes.append('勾选上限未触发（只有 %d 条星标）' % lim['have'])


            # ---- ⑥ 入口要【看得出能点】，而且不止在「选中的规则」里 ----
            #   🔴 第一版两个入口都画成了暗 `.lvtag`，用户当场问
            #     「对比的入口在哪里？」——「**一个能点的东西被画成了标签**」
            #     这条为「选股理由」记过一次，又犯了一遍。
            #     判据取**可量的视觉事实**（有边框 + 手型光标 + 可见），
            #     而不是查那个 class —— 后者在样式被改暗时照样命中。
            #   ★ 归档目录也必须能勾：只在「选中的规则」里能比的话，
            #     想比两次没标星的回测就走不通（死路，而它不报错）。
            pg.goto(base + '#/runs', wait_until='networkidle')
            pg.wait_for_selector('#cmpbar #cmpgo', timeout=20000)
            ent = pg.evaluate(_JS_ENTRY)
            assert ent['ok'], ent.get('why')
            assert ent['vis'], '「⚖ 比对」按钮不可见'
            assert ent['border'] != 'none', '入口没有边框 —— 看不出是按钮（画成标签了）'
            assert ent['cursor'] == 'pointer', '入口不是手型光标：%s' % ent['cursor']
            assert ent['boxes'] > 0, '归档目录的行里没有比对勾选框'
            # 详情页那个入口：同样要是按钮，点了要带着自己过去【并展开到那一行】
            pg.goto(base + '#/run/' + pair[0], wait_until='networkidle')
            pg.wait_for_selector('#rdcmp', timeout=20000)
            rd = pg.evaluate(_JS_RD)
            assert rd['border'] != 'none' and rd['cursor'] == 'pointer', \
                '详情页的比对入口没画成按钮：%s' % rd
            pg.click('#rdcmp')
            pg.wait_for_selector('#cmpbar #cmpgo', timeout=20000)
            land = pg.evaluate(_JS_LAND, pair[0])
            assert '#/runs' in pg.url, '该去【归档目录】（全部回测都能勾）：%s' % pg.url
            assert land['sel'] == [pair[0]], '没把自己预选成基准 A：%s' % land['sel']
            # 🔴 目录树默认全折叠 —— 不展开的话人落在一棵合着的树上，
            #   等于"到了那边还要再找一遍"（把入口做成摆设）。
            #   判据是**那一行真的可见**，不是"DOM 里有"：695 个勾选框一直都在，
            #   而首屏 0 个可见 —— 这两件事差得远（同「页签点得开 ≠ 页签里有东西」）。
            assert land['rowVis'], '跳过去之后那一行不可见 —— 目录树没展开到它'
            assert land['boxVis'] > 0, '跳过去之后一个勾选框都看不见（%d 个在 DOM 里）' % ent['boxes']
            notes.append('两处入口都是可见按钮（有边框+手型）；详情页跳过去自动展开到那一行'
                         '（可见勾选框 %d 个）' % land['boxVis'])


            # ---- ⑦ 逐年用【柱状图】，收益与回撤分上下两块 ----
            #   🔴 **不能画折线**：逐年是离散量，折线会在 2016 与 2017 之间
            #     画出一段不存在的"过程"。
            #   🔴 **也不能把收益与回撤镜像进同一块**：亏损年那两根**同向朝下**
            #     （实测 2022 pb1.0：收益 −4.52% / 回撤 −26.14%），只能靠颜色分，
            #     而颜色在这一页已经被"哪个配置"占用了。
            pg.unroute('**/api/run?*'); pg.unroute('**/api/equity?*')
            pg.goto(base + '#/cmp/' + ','.join(pair), wait_until='networkidle')
            pg.reload(wait_until='networkidle')
            pg.wait_for_selector('#cmpyb2 svg', timeout=30000)
            yb = pg.evaluate(_JS_YEAR)
            assert yb['bars1'] > 0 and yb['bars2'] > 0, '逐年图没画出柱子：%s' % yb
            assert yb['paths1'] == 0, '逐年收益画成了折线（%d 条 path）—— 离散量不许插值' % yb['paths1']
            assert yb['zero1'] == 1 and yb['zero2'] == 1, '柱状图缺零线 —— 朝上朝下没有参照'
            # 两块必须是**分开**的 svg 且左边界对齐（同一 x 轴）
            assert yb['same2'], '收益与回撤没分成两块（镜像在同一块里分不出方向）'
            assert abs(yb['left1'] - yb['left2']) <= 1, \
                '两块左边界不齐（%s vs %s）—— x 轴对不上' % (yb['left1'], yb['left2'])
            # 差值视图：基准 A 不画（它恒为 0），所以柱子少一组
            pg.click('#cmpymode [data-ym=diff]')
            pg.wait_for_timeout(250)
            d = pg.evaluate("document.querySelectorAll('#cmpyb1 svg rect[fill]').length")
            assert d == yb['bars1'] - yb['nyear'], (
                '差值视图没把基准 A 去掉：%d 根（应为 %d − %d）' % (d, yb['bars1'], yb['nyear']))
            ttl = pg.inner_text('#cmpyb1 .ttl')
            assert '基准' in ttl and ('正' in ttl or '负' in ttl), \
                '差值图标题没说清符号方向：%r' % ttl
            notes.append('逐年是柱状图（收益 %d 根 / 回撤 %d 根、各带零线、两块 x 轴对齐）；'
                         '差值视图去掉基准 A' % (yb['bars1'], yb['bars2']))
            # ---- ⑧ 差值视图必须把「数柱子会得出的数」自己印出来，带口径 ----
            #   🔴 2026-09-17 实测：用户看这张图数出「pb=1 大部分年份都赢」——
            #     那是**全程连跑切片**的 7/11，而按逐年独立只有 5/11（2 年翻号）。
            #     根因不是他读错，是**页面请人数柱子却把口径塞在灰字里**。
            #   🔴 判据要钉**三个数都在** + **口径话术在**，而不是"有这个块" ——
            #     只查块存在的话，把内容换成空的照样绿。
            pg.click('#cmpymode [data-ym=diff]')
            pg.wait_for_timeout(250)
            sm = pg.inner_text('#cmpysum')
            for kw in ['胜', '均差', '去掉最好的']:
                assert kw in sm, '差值视图少了「%s」这个数：%r' % (kw, sm[:120])
            assert '不能用来判规则' in sm and '逐年独立' in sm, \
                '统计数字没带口径 —— 人会拿路径混沌当规则证据：%r' % sm[:160]
            # 绝对值视图**不印**（那里数柱子本来就不是在比差）
            pg.click('#cmpymode [data-ym=abs]')
            pg.wait_for_timeout(250)
            assert not pg.inner_text('#cmpysum').strip(), '绝对值视图不该印差值统计'
            notes.append('差值视图印出胜/均差/去掉最好那年，且带口径话术')
            assert not errs, '页面抛了异常：%s' % errs[:2]
            br.close()
    finally:
        httpd.shutdown()
    return '；'.join(notes)


@case('每个 .js 都过 node --check；注释里的反引号会闭合模板（踩过三次）', tag='fast')
def t_js_syntax():
    """两层，**分工别记反**：

    ① **`node --check` 是根本修复** —— 它抓所有语法错。
       而 selftest 里**一直没有它**（只在别处的注释里提到过），
       三次踩坑全靠"我这次记得手动跑" —— 同「靠人记得跑的步骤 = 迟早不跑」。
    ② **注释里的反引号单独扫一遍，是为了【报错指得到原因】。**
       `el.innerHTML=` 后面那个模板字符串里常夹着 HTML 注释，注释里出现
       一个反引号，模板当场闭合、整个文件 SyntaxError、**整页白屏**。
       而 node 报的行号是**误导的** —— 2026-09-18 那次它指向第 55 行
       (`ewarm` 那行)，真正的错在第 58 行的反引号，我照着行号找了一轮。

    踩过三次：chart.js 的 ddGap（09-14）、K 线读数那轮（09-15，
    CLAUDE.md 记过了还是踩）、live-fee.js 的「说明」字段（09-18）。

    🔴 **只扫 HTML 注释**（`<!-- -->`）。第一版把块注释也算进去，
      当场误报 `views/sync.js:361` —— 那是**代码区**的块注释，反引号在那里
      完全安全（该文件语法本来就是好的）。**判据比要证的事宽**，
      而一个天天误报的检查等于没有检查。
    """
    import re
    import subprocess
    web = os.path.join(REPO, 'web')
    files = sorted(_web_files(web, '.js'))
    assert len(files) >= 10, '只扫到 %d 个 .js —— 递归收集坏了？' % len(files)

    # ---- ① 语法（根本修复）----
    bad = []
    for f in files:
        r = subprocess.run(['node', '--check', os.path.join(web, f)],
                           capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            bad.append('%s: %s' % (f, (r.stderr or '').strip().splitlines()[-1][:80]))
    assert not bad, ('这些 .js 语法就是坏的 —— 表现是**整页白屏、'
                     '所有功能一起没了**：\n  ' + '\n  '.join(bad))

    # ---- ② HTML 注释里的反引号（报错指得到原因）----
    hits = []
    for f in files:
        src = open(os.path.join(web, f), encoding='utf-8').read()
        for m in re.finditer(r'<!--(.*?)-->', src, re.S):
            if '`' in m.group(1):
                hits.append('%s:%d  %s' % (
                    f, src[:m.start()].count('\n') + 1,
                    m.group(1).strip().replace('\n', ' ')[:60]))
    assert not hits, (
        'HTML 注释里有反引号 —— 它在 innerHTML 模板里会把模板提前闭合，'
        '**整个文件 SyntaxError、整页白屏**，而 node 报的行号指不到这里：\n  '
        + '\n  '.join(hits))
    # 反向自证：扫描器真的认得出反引号（否则上面恒为空 = 空转）
    assert '`' in re.search(r'<!--(.*?)-->', '<!-- x ` y -->', re.S).group(1), \
        '扫描本身失效 —— 这条判据是空转的'
    return 'node --check 过 %d 个 .js；HTML 注释里 0 个反引号（含扫描器自证）' % len(files)


@case('可执行入口必须固定 hash 种子（回测跨进程要可复现）')
def t_fixed_hash_seed():
    """🔴🔴 2026-09-18：`etf_p1_rotation` 同代码同数据，**10 个独立进程跑出
    两种结果各 5 次**；固定 `PYTHONHASHSEED` 后 10/10 相同。

    成因是策略里 `sorted([s for s in in_trend ...])` 的 `in_trend` 是个 `set`，
    而排序键并列时**没有 tie-break** —— 并列项的相对顺序就是 set 的迭代顺序，
    而 str 的 hash 每进程随机（PEP 456）。

    **后果比"模拟盘推不动"大**：同一个 run 重跑一遍结果就不同，于是
    等价性回归、与聚宽对数、「同参数重复跑必然逐位一致」那条去重判据
    **全部失效，而它不报错**。

    ★ **不改策略**（那是移植件，改排序会改变与聚宽正本的对数 ——
      同「没有去修 froec.py 的排序」那条先例），改成在**外面**固定种子。

    判据三条，都是**可证的事实**：
      ① 四个可执行入口都在**最前面**调 `ensure_fixed_hash_seed()`
         —— 晚了的话它之前做的副作用会在 exec 之后**重来一遍**
      ② 不带 `PYTHONHASHSEED` 起的子进程，调它之后
         `sys.flags.hash_randomization == 0`；**反向自证**：不调则为 1
         （只查前者的话，helper 整个删掉、而环境恰好有这个变量也能过）
      ③ 幂等：已经固定时返回 False 且**不 exec**（否则无限重启）
    """
    import ast as _ast
    import subprocess as _sp

    ENTRIES = ('run.py', 'serve.py', 'tick_daily.py', 'selftest.py')
    for fn in ENTRIES:
        p = os.path.join(REPO, fn)
        assert os.path.isfile(p), '入口不见了：%s' % fn
        tree = _ast.parse(open(p, encoding='utf-8').read())
        # 前面**只许**有：docstring / import / `sys.path.insert`
        #   ★ 那一句是必须的（不铺路就 import 不到 helper），而它无副作用：
        #     exec 之后新进程从头再走一遍也一样。除此之外的任何语句都不许
        #     排在固定种子之前 —— 有副作用的那些会**重来一遍**。
        def _is_prologue(n):
            if isinstance(n, (_ast.Import, _ast.ImportFrom)):
                return True
            if isinstance(n, _ast.Expr) and isinstance(n.value, _ast.Constant):
                return True                   # docstring
            if (isinstance(n, _ast.Expr) and isinstance(n.value, _ast.Call)
                    and _ast.dump(n.value.func).count("attr='insert'")
                    and "attr='path'" in _ast.dump(n.value.func)):
                return True                   # sys.path.insert(...)
            return False

        pos = None
        for i, n in enumerate(tree.body):
            if _is_prologue(n):
                continue
            pos = i
            break
        assert pos is not None, '%s 里一条可执行语句都没有？' % fn
        first = tree.body[pos]
        src = _ast.dump(first)
        assert 'ensure_fixed_hash_seed' in src or '_ehs' in src, (
            '%s 的第一条可执行语句不是固定 hash 种子（是 %s）——\n'
            '   exec 会把进程映像整个换掉，在它之前做的事白做，\n'
            '   更糟的是**有副作用的那些会重来一遍**'
            % (fn, _ast.dump(first)[:90]))

    # ---- 运行时：不带环境变量起子进程 ----
    env = {k: v for k, v in os.environ.items() if k != 'PYTHONHASHSEED'}
    code_on = ('import sys;sys.path.insert(0,%r);'
               'from assay.hashseed import ensure_fixed_hash_seed as e;e();'
               'print(sys.flags.hash_randomization)' % REPO)
    code_off = 'import sys;print(sys.flags.hash_randomization)'
    r_on = _sp.run([sys.executable, '-c', code_on], capture_output=True,
                   text=True, env=env, timeout=120)
    r_off = _sp.run([sys.executable, '-c', code_off], capture_output=True,
                    text=True, env=env, timeout=120)
    assert r_on.stdout.strip() == '0', \
        ('调了 ensure_fixed_hash_seed，hash 随机化却还开着（%r / %r）—— '
         '回测仍然跨进程不可复现'
         % (r_on.stdout.strip(), r_on.stderr[-200:]))
    assert r_off.stdout.strip() == '1', \
        ('构造不对：不调它时随机化本来就是关的（%r），'
         '那上面那条断言什么都没证' % r_off.stdout.strip())

    # ---- 🔴「设了却没生效」：环境变量不能当判据 ----
    #   进程内 `os.environ[...]='0'` 之后，只看环境变量的实现会说
    #   "已经固定了"直接返回 —— 而**随机化其实还开着**，回测照样不可复现，
    #   且**一声不吭**。判据必须是解释器标志（`sys.flags`）。
    code_lie = ('import sys,os;os.environ["PYTHONHASHSEED"]="0";'
                'sys.path.insert(0,%r);'
                'from assay.hashseed import ensure_fixed_hash_seed as e;e();'
                'print(sys.flags.hash_randomization)' % REPO)
    r3 = _sp.run([sys.executable, '-c', code_lie], capture_output=True,
                 text=True, env=env, timeout=120)
    assert r3.stdout.strip() == '0', \
        ('进程内设了 PYTHONHASHSEED 就被当成"已经固定"了 —— '
         '而随机化还开着（%r）。判据要取 sys.flags，不是环境变量'
         % r3.stdout.strip())

    # ---- 幂等：已固定时不许再 exec ----
    code_idem = ('import sys;sys.path.insert(0,%r);'
                 'from assay.hashseed import ensure_fixed_hash_seed as e;'
                 'print(e());print(e())' % REPO)
    env2 = dict(env, PYTHONHASHSEED='0')
    r2 = _sp.run([sys.executable, '-c', code_idem], capture_output=True,
                 text=True, env=env2, timeout=120)
    assert r2.stdout.split() == ['False', 'False'], \
        ('已经固定了还要重启 —— 那是无限循环：%r' % r2.stdout[:120])
    return ('4 个入口都在第一条语句固定种子；'
            '子进程 randomization 1 -> 0；幂等不重启')


@case('参数说明：注释归属要对 / 回测要能看清用了哪些参数')
def t_param_docs():
    """用户 2026-09-19 两条：「回测列表中每个回测用了哪些参数看的还是非常
    不清楚」「策略回测页面对参数没有详细的解释」。

    🔴🔴 **解释不是没人写，是解析器读错了地方。**
      `_PARAM_RE` 末尾那个 `\\s*(?:#...)` 的 `\\s` **匹配换行** ——
      行内没注释时它会吃掉换行、抓到**下一行**的注释。实测 froec：
      `g.weekday` 拿到的是下一行那句 `# ---- 用于定位对标残差的两个开关 ----`
      （段落标题！）。于是回测页上**每个参数配的是别人的说明** ——
      比没有说明更糟（同「分叉的文档比没有文档更危险」）。

    ★ 归属按 **Python 惯例**：注释写在它说明的那一行**上面**。
      实测 froec 40 个参数里行内注释只有 4 个、上方注释块 25 处。
    🔴 段落标题往上找要**限定在 `initialize` 内**：不设界就一路翻到文件
      开头，给它安一个**别处**的标题（实测红利那次被安上了不相关的段落）。

    🔴 **「这次回测用了哪些参数」≠ `meta.params`**：后者只有命令行覆盖过的
      那几个（常常 1 个），而生效配置是「全部默认 ⊕ 覆盖」。所以
      `/api/run` 给 `params_all`，**照归档里的源码快照**解析
      （不是磁盘上的当前文件 —— 那可能早改了）。
    """
    import glob as _g
    import json as _j

    from assay.srv.base import _parse_params
    from assay.srv import runs as _R

    src = open(os.path.join(REPO, 'strategies/小市值/froec.py'),
               encoding='utf-8').read()
    ps = _parse_params(src)
    assert len(ps) > 20, '构造不对：froec 解析出的参数太少（%d）' % len(ps)
    by = {p['name']: p for p in ps}

    # ---- ① 行内注释不许跨行抓到下一行 ----
    #   判据落在**可证的事实**上：段落标题（`---- xxx ----`）**永远不该**
    #   成为某个参数的 comment / doc —— 它是分节，不是说明。
    for p in ps:
        for t in [p['comment']] + list(p['doc']):
            assert not re.match(r'^-{2,}.*-{2,}$', (t or '').strip()), \
                ('参数 %s 的说明是个段落标题 %r —— 那是抓到了别人的注释'
                 % (p['name'], t))
    _w = by.get('weekday')
    assert _w is not None, '构造不对：froec 没有 weekday 这个参数'
    assert not _w['comment'], \
        ('weekday 行内本来就没有注释，却抓到了 %r —— 正则又跨行了'
         % _w['comment'])

    # ---- ② 上方注释块要抓得到（否则"没有详细解释"照旧）----
    n_doc = sum(1 for p in ps if p['doc'])
    assert n_doc >= 10, \
        ('只有 %d 个参数抓到了上方注释块 —— froec 里写了二十多处，'
         '说明归属那一段没生效' % n_doc)
    _c = by.get('candidate_num')
    assert _c and _c['doc'] and '原版' in _c['doc'][0], \
        ('candidate_num 上方那句说明没抓到：%r' % (_c and _c['doc']))

    # ---- ③ 段落分组要限定在 initialize 内 ----
    n_grp = len({p['group'] for p in ps if p['group']})
    assert n_grp >= 5, 'froec 的参数段落只认出 %d 组（源码里有十几段）' % n_grp
    #   反向自证：段落**不许跨函数**认领（`def` 是边界）。
    #   ⚠ 我一度以为还需要"只在 initialize 内"那道边界，加完做变异测试
    #     才发现它**什么也没抓到** —— `def` 那道已经够了。
    #     （别把冗余说成抓到了：同 realtime「锁是根本修复、唯一 tmp 名
    #     防跨进程」那条的反面用法。）
    plain = ("# ---- 模块级的段落标题，不该被参数认领 ----\n"
             "def initialize(context):\n    g.a = getattr(g, 'a', 1)\n")
    assert not any(p['group'] for p in _parse_params(plain)), \
        ('参数认领了 initialize 【外面】的段落标题 —— 往上找越界了')

    # ---- ④ /api/run 要给参数全集，并标出哪些改过 ----
    _R._scan()
    rid = None
    for f in sorted(_g.glob(os.path.join(REPO, 'runs/*/*/*/meta.json'))):
        m = _j.load(open(f, encoding='utf-8'))
        if (m.get('params') and
                os.path.isfile(os.path.join(os.path.dirname(f), 'strategy.py'))):
            rid = m['run_id']
            over = m['params']
            break
    assert rid, '构造不对：没有一次"带参数且留了源码快照"的归档'
    d = _R.api_run({'id': rid})
    pa = d.get('params_all')
    assert pa, '/api/run 没给参数全集 —— 页面只能看到覆盖的那几个'
    assert len(pa) > len(over), \
        ('参数全集 %d 个 <= 覆盖项 %d 个 —— 那就还是只列了覆盖项'
         % (len(pa), len(over)))
    ch = [p['name'] for p in pa if p['changed']]
    assert sorted(ch) == sorted(over), \
        '标成"改过"的是 %s，而实际覆盖的是 %s' % (ch, sorted(over))
    #   反向自证：没改的那些必须等于默认值（否则 changed 判据是摆设）
    for p in pa:
        if not p['changed'] and p['default'] is not None:
            assert str(p['value']) == str(p['default']), \
                '%s 没标改过，值却与默认不同：%r vs %r' \
                % (p['name'], p['value'], p['default'])
    return ('froec %d 个参数：%d 个有上方说明、%d 个段落；'
            '归档 %s 的参数全集 %d 个（改过 %d）'
            % (len(ps), n_doc, n_grp, rid[:15], len(pa), len(ch)))


@case('自检要能看到进度：正在跑哪条、且实时可见')
def t_selftest_progress():
    """🔴 用户 2026-09-22：「--all 有个问题，看不到进度」。

    两个成因，**缺一条都还是看不到**：
      ① 进度行原来在用例**跑完之后**才打 —— 而最慢的几条各要 46~70 秒
        （全量 1083 秒），那几十秒里屏幕上一个字都没有，分不出"在跑"
        还是"卡死了"。所以要在**开跑之前**就把名字打出来。
      ② 重定向到文件时 stdout 是**块缓冲**（攒满 4KB 才落盘），
        `tail -f` 看到的永远是几十条之前的 —— 所以每行都要 flush。

    ★ 判据走 **AST**（注释不是 AST 节点，查字符串会命中我自己写的说明）：
      那个 print 必须**排在调用用例之前**，这是"看得到进度"的全部意义。
    """
    import ast as _a
    src = open(os.path.join(REPO, 'tests', '_base.py'), encoding='utf-8').read()
    tree = _a.parse(src)
    fn = next(n for n in tree.body
              if isinstance(n, _a.FunctionDef) and n.name == 'main')
    loop = None
    for n in _a.walk(fn):
        if isinstance(n, _a.For) and any(
                isinstance(x, _a.Try) for x in _a.walk(n)):
            loop = n
            break
    assert loop is not None, 'main() 里找不到跑用例的那个循环'
    # 循环体里：调用用例的那句在第几行、进度 print 在第几行
    ln_call = min(
        [x.lineno for x in _a.walk(loop)
         if isinstance(x, _a.Call) and isinstance(x.func, _a.Name)
         and x.func.id == 'fn'] or [10 ** 9])
    ln_prog = min(
        [x.lineno for x in _a.walk(loop)
         if isinstance(x, _a.Call) and isinstance(x.func, _a.Name)
         and x.func.id == 'print'] or [10 ** 9])
    assert ln_call < 10 ** 9, '循环里没有调用 fn()'
    assert ln_prog < ln_call, \
        ('进度必须在【调用用例之前】打出来（现在 print 在第 %s 行、'
         'fn() 在第 %s 行）—— 跑完才打的话，最慢那条的 70 秒里屏幕上'
         '什么都没有' % (ln_prog, ln_call))
    # 结果行必须 flush —— 否则重定向时 tail -f 看到的是几十条之前的
    n_flush = sum(
        1 for x in _a.walk(loop)
        if isinstance(x, _a.Call) and isinstance(x.func, _a.Name)
        and x.func.id == 'print'
        and any(k.arg == 'flush' for k in (x.keywords or [])))
    n_print = sum(
        1 for x in _a.walk(loop)
        if isinstance(x, _a.Call) and isinstance(x.func, _a.Name)
        and x.func.id == 'print')
    # ★ 终端那条 `\r` 擦除行故意不 flush（紧跟着就有一个 flush 的 print）
    assert n_flush >= n_print - 2, \
        ('循环里 %d 个 print 只有 %d 个 flush —— 重定向时 stdout 是块缓冲，'
         '不 flush 就看不到进度' % (n_print, n_flush))
    # 序号与总数：没有它就只知道"在跑"，不知道"还剩多少"
    assert '[%*d/%d]' in src, '进度行要带【第几条/共几条】'
    # 宽度要按【列】算：中文占 2 列，len() 只数 1
    assert 'east_asian_width' in src, \
        ('擦除/截断要按显示【列宽】算 —— 用 len() 的话中文行擦不干净'
         '（留半行垃圾）、截断也会折行，而折行之后回车根本擦不掉')
    return ('进度 print 在第 %d 行、fn() 在第 %d 行（先打后跑）；'
            '%d/%d 个 print 带 flush；带序号与列宽换算'
            % (ln_prog, ln_call, n_flush, n_print))


@case('页面文案里不许写 markdown 星号（HTML 渲染不了）')
def t_no_markdown_stars():
    """★ CLAUDE.md 记过两次（indicators 的 desc 进 title、这一轮的参数说明），
    而**现网就有五处**在 title 属性里写了 `**`（属性里连 <b> 都用不了，
    星号会原样显示给用户）。

    ★ 之前没做全局扫描器，理由是"要区分注释里的星号与会进 innerHTML 的
      文案得解析 JS，误报率高"。这一版只扫**单引号字符串字面量**、
      并且先剥掉三种注释 —— 实测 0 误报，而它当场抓到了那五处。
    """
    import glob as _g
    bad = []
    for f in sorted(_g.glob(os.path.join(REPO, 'web/**/*.js'), recursive=True)):
        s = open(f, encoding='utf-8').read()
        s = re.sub(r'/\*.*?\*/', '', s, flags=re.S)     # 块注释
        s = re.sub(r'(?m)^\s*//.*$', '', s)             # 行注释
        s = re.sub(r'<!--.*?-->', '', s, flags=re.S)    # 模板里的 HTML 注释
        for m in re.finditer(r"'[^'\n]*\*\*[^'\n]*'", s):
            bad.append('%s: %s' % (os.path.basename(f), m.group(0)[:60]))
    assert not bad, \
        ('页面文案里有 markdown 星号（HTML 渲染不了，会原样显示）：\n  '
         + '\n  '.join(bad[:6]))
    # 反向自证：扫描器真的抓得到（否则"0 处"可能是正则写坏了）
    probe = "x = '这里有**星号**的文案';"
    assert re.search(r"'[^'\n]*\*\*[^'\n]*'", probe), '扫描器自己坏了'
    # ---- 服务端那一半：错误/提示文案也会原样渲染进页面（2026-09-22 加）----
    # 🔴 实测现网**六处**：`lv/px.py` 的「请**手填价格**」与「填了**后复权价**」
    #   直接进 `#fmsg` 的 innerHTML、`srv/runs.py` 的「跑的是**当前版本**」
    #   进版本页、`indicators.py` 那条 `desc` 进 `title` 属性
    #   （CLAUDE.md 为它记过两次，**而扫描器一直只扫 web/**）。
    # ★ 只用**三条窄规则**排掉误报，不维护白名单路径：
    #     ① docstring（含 `"""…""" % X` 那种 —— 它的 Expr 是 BinOp，
    #        只判 Constant 会漏，实测 `realtime.indices` 就是这种）
    #     ② 长度 ≤ 3（`'**'` 是 glob 通配，不是文案）
    #     ③ 含 SELECT/FROM 的 SQL 文本
    #   实测 **0 误报**（未加规则时 11 条里 3 条是误报）。
    import ast as _ast
    pybad = []
    for f in sorted(_g.glob(os.path.join(REPO, 'assay/**/*.py'), recursive=True)):
        src = open(f, encoding='utf-8').read()
        try:
            tree = _ast.parse(src)
        except SyntaxError:
            continue
        skip = set()
        for n in _ast.walk(tree):
            body = getattr(n, 'body', None)
            if isinstance(n, (_ast.Module, _ast.ClassDef, _ast.FunctionDef,
                              _ast.AsyncFunctionDef)) and body \
               and isinstance(body[0], _ast.Expr):
                v = body[0].value
                v = v.left if isinstance(v, _ast.BinOp) else v
                if isinstance(v, _ast.Constant) and isinstance(v.value, str):
                    skip.add(id(v))
        for n in _ast.walk(tree):
            if not (isinstance(n, _ast.Constant)
                    and isinstance(n.value, str)):
                continue
            v = n.value
            if id(n) in skip or '**' not in v or len(v) <= 3:
                continue
            if re.search(r'(?i)\bselect\b|\bfrom\b', v):
                continue
            pybad.append('%s:%d %s' % (os.path.basename(f), n.lineno,
                                       v.replace('\n', '⏎')[:60]))
    assert not pybad, \
        ('服务端文案里有 markdown 星号（它会原样渲染到页面上）：\n  '
         + '\n  '.join(pybad[:6]))
    # 反向自证：这半个扫描器也真的抓得到
    _probe = _ast.parse("raise X('取不到价 —— 请**手填价格**再试')")
    _found = [c for c in _ast.walk(_probe)
              if isinstance(c, _ast.Constant) and isinstance(c.value, str)
              and '**' in c.value and len(c.value) > 3]
    assert _found, '服务端那半个扫描器自己坏了'
    return '扫了 web 下全部 .js 与 assay 下全部 .py，0 处；两半扫描器都自证可用'


@case('万/亿折算与盈亏符号各只有一份（yiv / pnlv）', tag='web')
def t_money_fmt_single_source():
    """🔴 「按量级折成万/亿」此前**四处各写一遍**，四套小数规则加一套空格：

        common.js yiv      1384.4万      chart.js _money   1384.4万
        kchart.js 刻度      1384万        live-why.js       1384 万

    同一个 13,844,147 在四个页面长得都不一样 —— 同「名称+代码那一格全站
    一处定义 `cnCell`」那条，**而它不报错**。

    另有 `live-perf.js` 里**两个同名 `money`**（不同函数作用域，所以不是
    SyntaxError），行为却不同：图里那个没有 null 守卫，而 `null >= 0` 在 JS
    里是 **true** —— 实测吐出 `+— 元`；两个都用 `>= 0`，于是**平盘写成
    `+0`**，等于凭空报了个赚（同 `upc` 那条「判据要 > 0 / < 0 两头夹」）。

    2026-09-21 收敛成 `common.js` 的 `yiv(v, o)` 与 `pnlv(v, unit)`：
    折算规则（阈值 / 单位 / 空格）一份，**小数位仍由调用方给**
    （y 轴要短、tooltip 要精确、成交量轴另有分档）—— 变的只是精度。

    ⚠ `stockpop.js` 的「总市值 … 亿」「量 … 万股」**不在此列**：那是
      **固定单位**不是按量级挑单位；`live-fee.js` 那一堆 `*1e4` 是**万分率**。
      判据因此必须精确到「按量级折算」这个写法，不能见 `1e8` 就拦
      （同「判据比要证的事宽」那条）。
    """
    import re as _re
    canon = 'shared/common.js'
    # ---- ① 除正本外，不许再写「按量级折算」 ----
    bad = []
    for rel in _web_files(os.path.join(REPO, 'web'), '.js'):
        if rel.replace(os.sep, '/').endswith(canon):
            continue
        txt = io.open(os.path.join(REPO, 'web', rel), encoding='utf-8').read()
        for m in _re.finditer(r'>=\s*1e[48]', txt):
            seg = txt[max(0, m.start() - 160): m.start() + 160]
            if '亿' in seg or '万' in seg:
                bad.append('%s:%d' % (rel, txt[:m.start()].count('\n') + 1))
    assert not bad, ('这几处又自己按量级折了一遍万/亿：%s —— '
                     '一律走 common.js 的 yiv()（四处各写一遍，同一个数在'
                     '四个页面长得都不一样）' % bad)

    # ---- ② 两个正本各只定义一次 ----
    allsrc = {rel: io.open(os.path.join(REPO, 'web', rel), encoding='utf-8').read()
              for rel in _web_files(os.path.join(REPO, 'web'), '.js')}
    for name, pat in (('yiv', r'function\s+yiv\s*\('),
                      ('pnlv', r'(?:const|let|var|function)\s+pnlv\b')):
        hits = [r for r, t in allsrc.items() if _re.search(pat, t)]
        assert len(hits) == 1 and hits[0].replace(os.sep, '/').endswith(canon), \
            '%s 定义在 %s —— 只许 common.js 有一份' % (name, hits)
    lp = allsrc[[r for r in allsrc if r.endswith('live-perf.js')][0]]
    for m in _re.finditer(r'const money = (.+)', lp):
        assert 'pnlv(' in m.group(1), \
            'live-perf 又自己写了一份盈亏格式化：%s' % m.group(1)[:60]

    # ---- ③ 真页面上跑：证明加载顺序对（chart/kchart 用的是 common 里那个）----
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return '跳过（无 playwright）'
    import threading
    from http.server import ThreadingHTTPServer
    from assay import server as sv
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), sv.Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    JS = ('() => ({'
          ' yiv: yiv(13844147.77),'
          ' kchart: yiv(13844147.77, {wan: a => a >= 1e6 ? 0 : 1}),'
          ' why: yiv(13844147.77, {wan: 0, sp: " "}),'
          ' yi: yiv(1.2345e9), small: yiv(1234),'
          ' nul: yiv(null), nan: yiv(NaN), inf: yiv(Infinity),'
          ' p0: pnlv(0), ppos: pnlv(1234.4), pneg: pnlv(-1234.4),'
          ' pnul: pnlv(null), punit: pnlv(1234, " 元"),'
          ' pneg5: pnlv(-99.5), ppos5: pnlv(99.5)})')
    try:
        with sync_playwright() as pw:
            br = pw.chromium.launch()
            pg = br.new_page()
            errs = []
            pg.on('pageerror', lambda e: errs.append(str(e)))
            pg.goto('http://127.0.0.1:%d/stock.html?code=601857.XSHG' % port)
            pg.wait_for_function('typeof yiv === "function" '
                                 '&& typeof pnlv === "function"', timeout=20000)
            r = pg.evaluate(JS)
            assert not errs, '页面报错：%r' % errs[:2]
            # 单位与阈值三处一致（小数位各自不同，那是有意的）
            assert r['yiv'] == '1384.4万' and r['kchart'] == '1384万' \
                and r['why'] == '1384 万', r
            assert r['yi'] == '12.35亿' and r['small'] == '1234', r
            # 🔴 退化输入一律「—」，不许吐 NaN / Infinity亿 / +— 元
            assert r['nul'] == r['nan'] == r['inf'] == '—', r
            # 🔴 平盘不许带 `+`；null 不许带单位
            assert r['p0'] == '0' and r['pnul'] == '—', r
            assert r['ppos'] == '+1,234' and r['pneg'] == '-1,234', r
            assert r['punit'] == '+1,234 元', r
            # 🔴 舍入必须对称（Math.round 一律朝 +∞，盈亏上说不通）
            assert r['pneg5'] == '-100' and r['ppos5'] == '+100', r
            # 反向自证：参数真的起作用，否则上面那几条是"全一样 == 全一样"
            assert r['yiv'] != r['kchart'] and r['kchart'] != r['why'], r

            # 🔴 上面那三个是**我自己手写的 options** —— 它证明 `yiv` 会算，
            #   但**没碰任何一个调用点**。变异「live-why 把 sp 去掉」当场漏过。
            #   所以再去 index.html 上调**真函数**（判据比要证的事窄，
            #   而这次窄在"生产端算对了不等于消费端用上了"）。
            pg.goto('http://127.0.0.1:%d/' % port)
            pg.wait_for_function('typeof lvFmt === "function" '
                                 '&& typeof _money === "function"', timeout=20000)
            r2 = pg.evaluate('() => ({'
                             ' why: lvFmt(13844147.77, "money"),'
                             ' chart: _money(13844147.77),'
                             ' chart3: _money(1.2345e9, 3)})')
            assert not errs, '页面报错：%r' % errs[:2]
            assert r2['why'] == '1384 万', 'live-why 没按自己的口径调：%r' % r2
            assert r2['chart'] == '1384.4万', 'chart.js 没按自己的口径调：%r' % r2
            assert r2['chart3'] == '12.345亿', 'chart.js 的 dp 没传下去：%r' % r2
            br.close()
    finally:
        httpd.shutdown()
    return ('折算一处（yiv）+ 盈亏一处（pnlv）；三个调用点单位一致、'
            '小数各自；退化输入全给 —；舍入对称')


@case('datalake 根只许解析一次（paths.py）', 'fast')
def t_datalake_root_single_source():
    """🔴 「datalake 根在哪」此前在 **8 处**各写了一遍，而每一处都**自己数
    `dirname` 层数** —— 层数是跟着「这个文件放在哪」变的。

    `lv/px.py` 里那句注释就是物证：「`__file__` 在 `lv/` 里比原来深一层，
    所以要多剥一层 dirname」。也就是说**搬一次文件就要改一次**，
    而改漏了**不报错** —— 只是那个模块从此解到一个不存在的路径。
    拆 `srv/` 时实测踩过：`/api/marks` 返回 `{}`、7 个实盘接口 500。

    2026-09-21 收敛到 `assay/paths.py`（零依赖，位置固定，只数一次）。
    ★ **异常没有收进去**：CLI 要 `SystemExit`、HTTP 要 `LiveError`、
      看盘要 `StockError` —— 那是各自的 UX，混成一个反而让报错指不到地方。
      正本只回答"路径是哪个"。
    """
    import ast as _ast
    import subprocess as _sp

    # ① 除正本外，谁都不许再【读那个环境变量】。
    #   判据落在 `os.environ` 的**读取**上，不是"提到过这个名字" ——
    #   报错措辞里就有「用 ASSAY_DATALAKE 指定」，查字符串必然误伤。
    files = [os.path.join(REPO, f) for f in ('run.py', 'sweep.py')]
    for base_, _ds, fs in os.walk(os.path.join(REPO, 'assay')):
        if '__pycache__' in base_:
            continue
        files += [os.path.join(base_, f) for f in fs if f.endswith('.py')]
    canon = os.path.join(REPO, 'assay', 'paths.py')
    assert os.path.isfile(canon), 'assay/paths.py 没了 —— 正本搬走了？'
    bad = []
    for f in files:
        if os.path.abspath(f) == canon or not os.path.isfile(f):
            continue
        for n in _ast.walk(_ast.parse(io.open(f, encoding='utf-8').read())):
            hit = False
            if isinstance(n, _ast.Call) and isinstance(n.func, _ast.Attribute) \
                    and n.func.attr == 'get' \
                    and 'environ' in _ast.dump(n.func.value):
                hit = any(isinstance(a, _ast.Constant)
                          and a.value == 'ASSAY_DATALAKE' for a in n.args)
            elif isinstance(n, _ast.Subscript) and 'environ' in _ast.dump(n.value):
                hit = (isinstance(n.slice, _ast.Constant)
                       and n.slice.value == 'ASSAY_DATALAKE'
                       and isinstance(n.ctx, _ast.Load))
            if hit:
                bad.append('%s:%d' % (os.path.relpath(f, REPO), n.lineno))
    assert not bad, ('这几处又自己解了一遍 datalake 根：%s —— '
                     '一律走 assay/paths.datalake()（8 处各数一遍 dirname，'
                     '搬一次文件就错一处，而它不报错）' % bad)

    # ② `paths.REPO` 真的是仓库根 —— 这一条才是"层数只数一次"的意义所在。
    #   🔴 它要排在下面那个子进程【之前】：层数数错时子进程会直接崩，
    #     报出来是一段 traceback，**指不到真正的原因**（变异实测）。
    from assay import paths as _P
    assert os.path.isdir(os.path.join(_P.REPO, 'strategies')) and \
        os.path.isfile(os.path.join(_P.REPO, 'run.py')), \
        'paths.REPO 指到了 %s，不是仓库根（dirname 层数数错了）' % _P.REPO

    # ③ 八个入口**解到同一个地方**（反向自证：真的有 8 个，不是空转）
    src = ('import os, sys\n'
           "sys.path.insert(0, %r)\n"
           "os.environ.pop('ASSAY_DATALAKE', None)\n"
           'from assay import realtime, market, stock, symbols, paths\n'
           'from assay.lv import px\n'
           'from assay.srv import base as sb\n'
           'from assay.feed import PanelFeed\n'
           'g = [realtime._lake(), market._root(), stock._lake(), px._lake(),\n'
           '     os.path.normpath(sb._datalake_dir()), symbols.default_root(),\n'
           "     os.path.normpath(PanelFeed('2026-09-01','2026-09-18').root),\n"
           '     paths.datalake()]\n'
           'print(len(g), len(set(g)), g[0])\n') % REPO
    r = _sp.run([sys.executable, '-c', src], capture_output=True, text=True,
                cwd=REPO)
    assert r.returncode == 0, '八处解析跑不起来：%s' % r.stderr[-400:]
    n, uniq, got = r.stdout.strip().split(' ', 2)
    assert int(n) == 8, '只量到 %s 个入口（反向自证：应当是 8）' % n
    assert int(uniq) == 1, '八处解到了 %s 个不同的 datalake 根' % uniq
    assert os.path.isdir(got), '解出来的根不存在：%s' % got

    # ④ 面板的 `read_parquet(...)` 同样只许正本拼。
    #    🔴 判据落在**单个字符串字面量**上 —— 整文件查会被 docstring 里
    #      提到 `mart/panel_daily` 的那几行误伤（这条为此改过三版）。
    #    此前 8 处各拼一遍，而 `lv/intraday.py` 拼的是
    #    `panel_daily/**/*.parquet`（**另一个 glob**）—— 今天恰好同一批
    #    24 个文件、同 1631 万行（量过），所以一直看不出来。
    bad2 = []
    for f in files:
        if os.path.abspath(f) == canon or not os.path.isfile(f):
            continue
        for n in _ast.walk(_ast.parse(io.open(f, encoding='utf-8').read())):
            if isinstance(n, _ast.Constant) and isinstance(n.value, str) \
                    and 'mart/panel_daily' in n.value \
                    and ('read_parquet' in n.value or n.value.endswith('.parquet')):
                bad2.append('%s:%d' % (os.path.relpath(f, REPO), n.lineno))
    assert not bad2, ('这几处又自己拼了一条读面板的 SQL / glob：%s —— '
                      '一律走 assay/paths.panel_sql() 与 paths.PANEL_GLOB'
                      % bad2)

    # ⑤ 🔴 指纹盯的那批文件必须与**实际读的**是同一批 —— 少盯一个就会出现
    #    「数据变了而指纹没变」，于是 tick 判「不用重算」、模拟盘判「不用
    #    推进」，**两个都不报错**。所以两边取同一个常量。
    from assay.feed import PanelFeed as _PF
    assert dict(_PF.FINGERPRINT_PARTS)['panel'] == _P.PANEL_GLOB, \
        '指纹的 panel glob (%r) 与实际读的 (%r) 不是同一批文件' % (
            dict(_PF.FINGERPRINT_PARTS)['panel'], _P.PANEL_GLOB)
    assert _P.PANEL_GLOB in _P.panel_sql('/x') and \
        _P.panel_sql('/x').startswith("read_parquet('/x/"), _P.panel_sql('/x')
    # 🔴 glob 必须是**平的**，不许用 `**` —— 那会把 `panel_daily/` 下任何
    #   子目录里的 parquet 也读进来（中间产物、失败重建的残留），
    #   于是读到的是另一张表，**而它不报错**。`lv/intraday.py` 此前拼的
    #   就是 `**`，只因今天恰好没有子目录才一直看不出来。
    assert '**' not in _P.PANEL_GLOB, \
        'PANEL_GLOB 用了 `**`：%r —— 它会把子目录里的 parquet 一起读进来' \
        % _P.PANEL_GLOB

    # ⑦ tdx 原始日线的读法同样只许正本拼（2026-09-21）。
    #    此前 6 处各拼一遍 `raw/tdx/kline/<pat>.parquet`，而
    #    `feed.benchmark` 还内联了**第四份**"聚宽口径 -> tdx symbol"
    #    （且不校验位数：`abc.XSHG` 会拼成 `shabc` 去查，报的是
    #     "没有数据"而不是"这个代码不对"）。
    #    🔴 判据要**排掉 docstring** —— 三个文件的文档里就写着这个路径
    #      （第一版直接误报了它们：又一次「判据比要证的事宽」）。
    bad3 = []
    for f in files:
        if os.path.abspath(f) == canon or not os.path.isfile(f):
            continue
        tree = _ast.parse(io_open_text(f))
        docs = set()
        for nn in _ast.walk(tree):
            if isinstance(nn, (_ast.Module, _ast.FunctionDef, _ast.AsyncFunctionDef,
                               _ast.ClassDef)) and nn.body:
                f0 = nn.body[0]
                if isinstance(f0, _ast.Expr) and isinstance(f0.value, _ast.Constant) \
                        and isinstance(f0.value.value, str):
                    docs.add(id(f0.value))
        for n in _ast.walk(tree):
            if isinstance(n, _ast.Constant) and isinstance(n.value, str) \
                    and id(n) not in docs \
                    and 'raw/tdx/kline/' in n.value \
                    and ('read_parquet' in n.value or n.value.endswith('.parquet')):
                bad3.append('%s:%d' % (os.path.relpath(f, REPO), n.lineno))
    assert not bad3, ('这几处又自己拼了一遍 tdx 日线的路径：%s —— '
                      '一律走 assay/paths.tdx_kline_sql()' % bad3)

    # ⑧ 🔴 **路径表与"要兜哪几类"是两件事，不许并成一个。**
    #    `symbols.KIND_FILE` 被 `stock.alt_kind` 当**策略**用
    #    （「只兜 ETF 与指数，不兜股票」）——把 stock 并进去就会让股票
    #    也走回落，**而它不报错**，只是 1600 万行的表被无谓地扫。
    from assay import symbols as _Y2
    assert 'stock' in _P.TDX_KLINE, '路径表里应当有 stock（perf 的基准要用）'
    assert 'stock' not in _Y2.ALT_KINDS and 'stock' not in _Y2.KIND_FILE, \
        'symbols 的"兜哪几类"混进了 stock：%r —— 那是路径表，不是策略' % (
            sorted(_Y2.KIND_FILE),)
    from assay.lv import perf as _PF
    assert _PF._KIND_FILE is _P.TDX_KLINE, \
        'lv/perf 又自己留了一份类别表（此前就是两份，一份 4 类一份 2 类）'
    # 反向自证：股票真的不走回落（只测 ETF 的话，"把所有代码都拖去 tdx"
    #   也全绿 —— 这条为 lv/tdx 那轮记过）
    from assay import stock as _S2
    assert _S2.alt_kind('601857.XSHG') is None, '股票被判成了面板之外的标的'

    # ⑨ `feed.benchmark` 不许再内联代码换算（第四份）
    import inspect as _ins
    from assay.feed import PanelFeed as _PF2
    _bm = _ins.getsource(_PF2.benchmark)
    assert 'to_symbol' in _bm, 'feed.benchmark 又自己写了一份代码换算'
    assert "'sh' if" not in _bm, 'feed.benchmark 里还留着内联换算的残迹'

    # ⑥ `ASSAY_DATALAKE` 仍然是最高优先（**构造**，本机没设这个变量）
    _old = os.environ.get('ASSAY_DATALAKE')
    try:
        os.environ['ASSAY_DATALAKE'] = '/tmp/_selftest_lake'
        assert _P.datalake() == '/tmp/_selftest_lake', '环境变量不生效了'
        assert _P.datalake('/tmp/other') == '/tmp/other', '显式传的应当最高优先'
    finally:
        if _old is None:
            os.environ.pop('ASSAY_DATALAKE', None)
        else:
            os.environ['ASSAY_DATALAKE'] = _old



@case('「面板之外的标的」那一层只许 symbols.py 有（stock.py 只转发）', 'fast')
def t_alt_layer_in_symbols():
    """🔴 2026-09-21：这一层此前**一半在 `symbols.py`、一半在 `stock.py`**
    （`kind_of` / `alt_names` / `name_snap` 在那边，`alt_panel` / `_alt_index` /
    `_alt_name` / `_ALT_FILE` / `_alt_map` 在这边），而 `stock._alt_map` 与
    `symbols._kind_map` 是**逐字相同的两份**。

    并的时候才发现 `_alt_map` + `_ALT` 已经**没人调了** —— 那是上一轮把
    `alt_kind` 改成转发 `_SYM.kind_of` 时留下的尸体。
    「留一个没人用的定义，下次有人会以为它是正本」，所以删掉。

    ⚠ **还没收干净的**：`feed.benchmark` 与 `lv/perf.bench_curves` /
      `bench_search` 也读 `raw/tdx/kline/*`。那是**基准曲线**那条链，
      与这里是两件事（一个要后复权比收益率、一个要不复权算市值），
      所以这一轮没动它 —— 记在这里，别下次又当成"漏了"。
    """
    import ast as _ast
    import glob as _g
    # 🔴 **不写死实现在哪个文件** —— `stock.py` 2026-09-21 拆成了 `stk/`，
    #   而这条守卫原来钉着 `assay/stock.py`，当场变红（**失败的是断言不是
    #   产品**，它要保的东西一个没坏）。同「判据要跟着代码一起搬」那条。
    files = [f for f in _g.glob(os.path.join(REPO, 'assay/stk/*.py'))
             ] + [os.path.join(REPO, 'assay/stock.py')]

    # ① 死代码不许回来
    for f in files:
        tree = _ast.parse(io_open_text(f))
        top = {n.name for n in tree.body if isinstance(n, _ast.FunctionDef)}
        top |= {t.id for n in tree.body if isinstance(n, _ast.Assign)
                for t in n.targets if isinstance(t, _ast.Name)}
        for dead in ('_alt_map', '_ALT', '_ALT_FILE'):
            assert dead not in top, '%s 又长出 %s —— 它与 symbols 里那份' \
                '是逐字相同的两份' % (os.path.relpath(f, REPO), dead)

        # ② 不许再自己拼 tdx 日线的路径（判据落在字符串字面量上）
        bad = [n.lineno for n in _ast.walk(tree)
               if isinstance(n, _ast.Constant) and isinstance(n.value, str)
               and 'raw/tdx/kline/' in n.value and 'read_parquet' in n.value]
        assert not bad, '%s:%r 又自己读 tdx 日线 —— 走 symbols.alt_panel' % (
            os.path.relpath(f, REPO), bad)

    # ③ 四个入口必须是【转发】（现找它定义在哪个域文件里）
    for nm, want in (('_snap_path', 'name_snap'), ('alt_panel', 'alt_panel'),
                     ('_alt_index', 'alt_rows'), ('_alt_name', 'alt_name')):
        rel, fsrc = _defining_file(nm, 'assay/stk')
        fn = next(n for n in _ast.parse(fsrc).body
                  if isinstance(n, _ast.FunctionDef) and n.name == nm)
        hit = [c for c in _ast.walk(fn)
               if isinstance(c, _ast.Call) and isinstance(c.func, _ast.Attribute)
               and c.func.attr == want]
        assert hit, '%s 里的 %s 没有转发给 symbols.%s' % (rel, nm, want)

    # ④ 反向自证：转发过去之后**结果还对**（否则 ③ 只是"调了一下"）
    from assay import stock as _S, symbols as _Y
    assert _S.alt_kind('513120.XSHG') == ('etf', 'sh513120'), _S.alt_kind('513120.XSHG')
    assert _S.alt_kind('000001.XSHG') == ('index', 'sh000001'), \
        '上证指数认成了 %r' % (_S.alt_kind('000001.XSHG'),)
    assert _S.alt_kind('601857.XSHG') is None, '股票不许走这条路'
    assert '\ufffd' not in _S._alt_name('sh513120'), '名称清洗没生效'
    assert 'jq_code' in _S.alt_panel('etf') and \
        'hfq_factor' in _S.alt_panel('etf'), 'alt_panel 不再与面板同形'
    assert _Y.KIND_FILE == {'etf': 'etf_*', 'index': 'index_*'}, _Y.KIND_FILE


@case('搜索结果跨进程必须可复现（并列要有 tie-break）', 'fast')
def t_search_deterministic():
    """🔴 2026-09-21 做 stock.py 等价性时抓到的**既有缺陷**：
    `search('红利')` 固定 hash 种子之后**跨进程 5 次给出 5 个不同结果**。

    根因两层：`symbols.alt_rows` 那条 SQL 没有 `ORDER BY`（duckdb 并行扫描，
    行序不保证），而 `search` 的排序前四个键在"红利"这种搜法下**大面积并列**
    （十几个同分的指数/ETF）—— 于是谁在前取决于扫描顺序。

    与 froec 那条 `ORDER BY r.increase DESC` 没有 tie-break 是**同一类**。
    ★ 区别在于**敢不敢修**：那边不敢（会改变全部历史归档的结果），
      而这里是看盘页的展示，让它可复现没有任何代价 —— 所以加了第五个键
      （按代码）。

    🔴 **跨进程的问题必须跨进程测** —— 同一个进程里连跑 N 次只证明了
      进程内可复现，什么都没证（`PYTHONHASHSEED` 那轮记过这条）。
    """
    import subprocess as _sp
    code = (
        'import sys, json, hashlib; sys.path.insert(0, %r)\n'
        'from assay.hashseed import ensure_fixed_hash_seed as e; e()\n'
        'from assay import stock as S\n'
        'r = S.search(%r, 20)\n'
        "print(hashlib.md5(json.dumps(r, sort_keys=True, default=str,\n"
        "      ensure_ascii=False).encode()).hexdigest()[:12])\n"
        "print(len(r['results']))\n")

    def runs(q, n=4):
        out = []
        for _ in range(n):
            r = _sp.run([sys.executable, '-c', code % (REPO, q)],
                        capture_output=True, text=True, cwd=REPO)
            assert r.returncode == 0, '搜 %s 跑不起来：%s' % (q, r.stderr[-300:])
            # 🔴 解析不了时要把**原始 stdout** 带出来。裸 `r.stdout.split()` 在子进程多打一行时会炸成一句
            #   `too many values to unpack (expected 2)` —— 那**指不到原因**
            #   （2026-09-22 全量跑时真发生过：returncode 是 0、活儿干完了，
            #   只是多了一行输出，而报错看着像用例逻辑错了）。
            tok = r.stdout.split()
            assert len(tok) == 2, (
                '搜 %s 的子进程输出不是「指纹 条数」两个 token —— '
                '原始 stdout=%r stderr=%s' % (q, r.stdout[-300:], r.stderr[-300:]))
            fp, cnt = tok
            out.append((fp, int(cnt)))
        return out

    for q in ('红利', '银行', '600'):
        got = runs(q)
        assert len({x[0] for x in got}) == 1, (
            '搜「%s」跨进程给出 %d 种结果 —— 并列时没有 tie-break：%r'
            % (q, len({x[0] for x in got}), got))
        assert got[0][1] > 0, '搜「%s」一条都没有，这条判据是空转的' % q

    # 🔴 反向自证：**真的有大面积并列**，否则"可复现"是白给的
    #   （所有键都互不相同时，任何实现都稳定 —— 那样这条用例什么都没证）。
    from assay import stock as _S
    rs = _S.search('红利', 20)['results']
    key = [(0 if r.get('kind') == 'stock' else 1, -(r.get('floatmv') or 0))
           for r in rs]
    assert len(key) - len(set(key)) >= 5, \
        '构造不对：「红利」这一搜只有 %d 个并列，测不出 tie-break' % (
            len(key) - len(set(key)))

    # 排序键里确实带了代码那一层（结构判据，防"只是这批数据碰巧稳定"）
    import ast as _ast
    # 🔴 现找 `search` 定义在哪 —— 写死 `assay/stock.py` 的话，拆成 `stk/`
    #   之后这条会 StopIteration（2026-09-21 实测）。
    rel, src = _defining_file('search', 'assay/stk')
    fn = next(n for n in _ast.parse(src).body
              if isinstance(n, _ast.FunctionDef) and n.name == 'search')
    seg = _ast.get_source_segment(src, fn) or ''
    assert "r['code']" in seg and 'x[:5]' in seg, \
        '%s 里 search 的排序键没有按代码的 tie-break 了' % rel


@case('stock.py 拆成 stk/：门面一个名字不少 / 相对导入 / 依赖是 DAG', 'fast')
def t_stk_split():
    """🔴 2026-09-21：`stock.py` 1069 行混了 6 件事，按产品域拆成
    `assay/stk/{base,find,quote,ind,ctx,link}.py` + 门面。

    这条钉的是**拆 `srv/` 时记过的那三个坑**，它们全都不报错：

    | 坑 | 表现 |
    |---|---|
    | 相对导入的 `.` 也跟着变 | 搬进 `stk/` 后 `.` 是 `assay.stk`。**这次真踩了**：`link._runs_with` 里 `from . import registry` -> ImportError，而外面那层 try/except 把它记成 `runs_error`、`runs` 返回 `[]` —— 页面上就是「这个版本从没回测过」，**一个静默的错答案**（等价性对数抓到的：83 项里 links 那 4 项变了） |
    | `__file__` 深了一层 | 这次天然躲过了（路径早已收进 `paths.py`），但不能指望下一个人也躲过 |
    | 留在别处的名字 | Load 但没绑定 —— import 能过、只在那条分支真跑到时才炸 |

    ★ 依赖必须是单向 DAG：`find/quote/ctx/link -> base`、`ind -> quote`。
    ★ 门面是**普通 re-export**（查过：外部没有任何一处【写】`stock.X`，
      那才是 `lv.LIVE` 必须做成 ModuleType 子类的理由）。
    """
    import ast as _ast
    import glob as _g
    STK = os.path.join(REPO, 'assay', 'stk')
    mods = {}
    for f in sorted(_g.glob(os.path.join(STK, '*.py'))):
        n = os.path.basename(f)[:-3]
        if n == '__init__':
            continue
        mods[n] = (f, io.open(f, encoding='utf-8').read())
    assert set(mods) == {'base', 'find', 'quote', 'ind', 'ctx', 'link'}, sorted(mods)

    # ① 相对导入只许指向【同级的域模块】。写 `from . import registry` 的话
    #    `.` 是 assay.stk，拿到的是子包 —— 而它不报错。
    for n, (f, src) in mods.items():
        for x in _ast.walk(_ast.parse(src)):
            if isinstance(x, _ast.ImportFrom) and x.level:
                assert x.level == 1 and x.module in mods, (
                    'assay/stk/%s.py:%d 的相对导入 `%s%s` 指不到域模块 —— '
                    '搬进子目录后 `.` 是 assay.stk，要用绝对导入 '
                    '(from assay import X)' % (n, x.lineno, '.' * x.level,
                                               x.module or ''))

    # ② `__file__` 不许在 stk/ 里用来推路径（层数会跟着文件位置变）
    for n, (f, src) in mods.items():
        assert '__file__' not in src, \
            'assay/stk/%s.py 用了 __file__ —— 路径一律走 assay/paths.py' % n

    # ③ 依赖是单向 DAG，且方向正确
    dep = {n: {x.module for x in _ast.walk(_ast.parse(s))
               if isinstance(x, _ast.ImportFrom) and x.level == 1}
           for n, (f, s) in mods.items()}
    assert dep['base'] == set(), 'base 不许依赖别的域（它是被依赖的那个）：%r' % dep['base']
    for n in ('find', 'quote', 'ctx', 'link'):
        assert dep[n] <= {'base'}, '%s 多了依赖 %r' % (n, dep[n] - {'base'})
    assert dep['ind'] <= {'base', 'quote'}, dep['ind']

    # ④ Load 但没绑定的名字（"留在别处的引用"）—— import 过了不代表没漏
    import builtins as _bi
    for n, (f, src) in mods.items():
        tree = _ast.parse(src)
        bound = set(dir(_bi)) | {'__name__', '__doc__'}
        for x in _ast.walk(tree):
            if isinstance(x, (_ast.Import, _ast.ImportFrom)):
                bound |= {(a.asname or a.name).split('.')[0] for a in x.names}
            elif isinstance(x, (_ast.FunctionDef, _ast.ClassDef)):
                bound.add(x.name)
                ar = getattr(x, 'args', None)      # ClassDef 没有 args
                if ar is not None:
                    bound |= {a.arg for a in list(ar.args) + list(ar.kwonlyargs)
                              + list(getattr(ar, 'posonlyargs', []))}
                    for v in (ar.vararg, ar.kwarg):
                        if v:
                            bound.add(v.arg)
            elif isinstance(x, _ast.Name) and isinstance(x.ctx, _ast.Store):
                bound.add(x.id)
            elif isinstance(x, (_ast.comprehension,)):
                for t in _ast.walk(x.target):
                    if isinstance(t, _ast.Name):
                        bound.add(t.id)
            elif isinstance(x, _ast.ExceptHandler) and x.name:
                bound.add(x.name)
            elif isinstance(x, _ast.Global):
                bound |= set(x.names)
        miss = sorted({x.id for x in _ast.walk(tree)
                       if isinstance(x, _ast.Name) and isinstance(x.ctx, _ast.Load)
                       and x.id not in bound})
        assert not miss, 'assay/stk/%s.py 引用了没绑定的名字：%r' % (n, miss)

    # ⑤ 门面一个名字都不许少（对外契约）——【反向自证】：真的有那么多
    from assay import stock as _S
    from assay.stk import base as _B
    WANT = ['FIELD_UNIT', 'StockError', '_lake', 'panel', 'con', 'norm_code',
            'alt_kind', 'alt_panel', '_snap_path', '_na', '_alt_name',
            '_index', '_alt_index', 'search', 'profile', '_alt_profile',
            'kline', 'finance', '_ma', '_r3', '_wan', 'parse_inds',
            'indicators', 'events', 'peers', 'compare', '_names_map',
            'links', '_runs_with', 'IND_WARM', 'IND_DEFAULT']
    assert len(WANT) >= 30, '反向自证：清单本身要够长'
    miss = [n for n in WANT if not hasattr(_S, n)]
    assert not miss, 'assay.stock 门面少了这些名字（对外契约）：%r' % miss

    # ⑥ 真跑一遍：links 那条路必须**真的拿到归档**，不许落进 runs_error
    #    —— 这正是相对导入那个坑的可观察后果（它不抛错，只是返回空）
    lk = _S.links('601857.XSHG')
    assert not lk.get('runs_error'), 'links 报错了：%r' % lk['runs_error']
    assert lk.get('runs'), \
        'links 一条回测都没找到 —— 多半是 registry 又被相对导入拿错了'


@case('取价唯一正本', 'fast')
def t_symbols_price_single_source():
    """🔴 「查面板 -> 查不到就回落 tdx」这个模式，此前在【四个取价函数】里
    各写了一遍（`perf._last_px` / `px.daily_close_map` / `px.day_price` /
    `px.day_range`）。而它出过两次事，两次都不报错：

        ETF 持仓市值 0.00 / 浮盈 -100%（四处漏了四处）
        「修了 4 处还漏了 3 处」—— 权益曲线里 ETF 市值全程为 0

    2026-09-21 收敛到 `assay/symbols.py`：`last_px` / `daily_close_map` /
    `day_px` / `day_hl`。这条守卫钉的是**不许再长出第五份**。

    ⚠ 顺带发现一处【既有的不一致】：`px.day_price` 面板那条路 round 到
      **3 位**、tdx 那条路 round 到 **4 位**。重构阶段逐位保真（正本返回
      `(值, 来源)` 让调用方各自 round），**没有顺手统一** —— 悄悄改精度
      不算重构。要不要统一是另一个决定。
    """
    import ast as _ast
    src = {}
    for rel in ('assay/symbols.py', 'assay/lv/px.py', 'assay/lv/perf.py',
                'assay/lv/tdx.py', 'assay/lv/hist.py'):
        src[rel] = io.open(os.path.join(REPO, rel), encoding='utf-8').read()

    # ① 正本真的在，而且四个函数都有（反向自证：名字打错的话下面全空转）
    sy = _ast.parse(src['assay/symbols.py'])
    fns = {n.name for n in _ast.walk(sy) if isinstance(n, _ast.FunctionDef)}
    for nm in ('last_px', 'daily_close_map', 'day_px', 'day_hl'):
        assert nm in fns, 'assay/symbols.py 里没有 %s —— 正本搬走了？' % nm

    # ② 「不许再拼一条读面板的 SQL」这条**挪到了**『datalake 根只许解析
    #    一次』那个用例里（2026-09-21）：它后来发现拼这句的不止取价这几处，
    #    一共 8 处、而且 `lv/intraday.py` 拼的 glob 还是另一个。
    #    规则只有一份，守卫也就只该有一处（同「两处实现必然分叉」）。

    # ③ 四个调用点确实【转发】到正本，不是各留一份
    import assay.symbols as _S
    from assay.lv import px as _px, perf as _perf
    for mod, fn, want in ((_perf, '_last_px', 'last_px'),
                          (_px, 'daily_close_map', 'daily_close_map'),
                          (_px, 'day_price', 'day_px'),
                          (_px, 'day_range', 'day_hl')):
        body = inspect.getsource(getattr(mod, fn))
        calls = [n for n in _ast.walk(_ast.parse(textwrap.dedent(body)))
                 if isinstance(n, _ast.Call) and isinstance(n.func, _ast.Attribute)
                 and n.func.attr == want]
        assert calls, '%s.%s 没有调 symbols.%s —— 它又自己取了一遍价' % (
            mod.__name__, fn, want)

    # ④ 正本**不抛错、不猜**：取不到给 None / 不给那个键。
    #    「是不是没同步 / 停牌 / 代码写错」属于调用方（报错措辞留在 px.py）。
    root = os.environ.get('ASSAY_DATALAKE') or os.path.join(
        os.path.dirname(REPO), 'datalake')
    assert _S.day_px(root, '999999.XSHE', '2026-09-18') is None
    assert _S.day_hl(root, '999999.XSHE', '2026-09-18') is None
    assert _S.last_px(root, ['999999.XSHE'], '2026-09-18') == {}

    # ⑤ 反向自证：两条路都真的取得到（否则 ④ 是「全 None == 全 None」的空转）
    #   ★ 判据不看"走了哪条路"（签名里已经没有来源了），看**面板有没有它**
    #     —— 那才是这两条路分岔的依据。
    v = _S.day_px(root, '601857.XSHG', '2026-09-18')
    assert v, '股票（在面板里）取不到价：%r' % (v,)
    assert _S.panel_probe(root, '601857.XSHG', '2026-09-18')[1], \
        '构造不对：这只股票本来就不在面板里，⑤ 是空转的'
    v2 = _S.day_px(root, '513120.XSHG', '2026-09-18')
    assert v2, 'ETF（面板里没有）取不到价 —— 回落那条路断了：%r' % (v2,)
    assert not _S.panel_probe(root, '513120.XSHG', '2026-09-18')[1], \
        '构造不对：ETF 居然在面板里，那就没在测回落'
    # 🔴 **两条路的精度必须一样**（2026-09-21 统一成 4 位）。
    #   此前面板那条 round 到 3 位、tdx 那条 4 位 —— 同一个函数两种精度，
    #   而它不报错。统一是**可证的空操作**：A 股不复权价本来就是两位小数，
    #   实测面板 1083 万行里 `round(x,3) != round(x,4)` 的有 **0 行**；
    #   ETF 最小变动 0.001，走的本来就是 4 位那条。
    #   **往不丢精度的那一侧统一**，所以是 4 不是 3。
    rnd = [n for n in _ast.walk(_ast.parse(textwrap.dedent(
               inspect.getsource(_S.day_px))))
           if isinstance(n, _ast.Call) and getattr(n.func, 'id', '') == 'round']
    assert rnd and len(rnd) >= 2, 'symbols.day_px 里不再 round 了：%d 处' % len(rnd)
    digits = {n.args[1].value for n in rnd
              if isinstance(n.args[1], _ast.Constant)}
    assert digits == {4}, (
        'day_px 两条路的精度又不一样了：%r —— 统一在正本（4 位），'
        '别退回按来源分（同一个函数两种精度，而它不报错）' % sorted(digits))
    # 调用方不许再自己 round 回去（精度归正本一处）
    for n in _ast.walk(_ast.parse(textwrap.dedent(
            inspect.getsource(_px.day_price)))):
        if isinstance(n, _ast.Call) and getattr(n.func, 'id', '') == 'round':
            raise AssertionError('px.day_price 又自己 round 了 —— 精度归正本')


@case('「代码 -> 类别 / 名称 / K 线」只许有一份实现（symbols.py）')
def t_symbols_single_source():
    """🔴🔴 2026-09-21 用户报：「首页显示了 513120.XSHG 的编码但没有中文名，
    点击后也看不到日 K，而实盘-模拟盘页面可以」—— 并指出这**不是找不到**，
    是「通过编码获取名称/K线、索引到 stock/etf/index 的逻辑出现了分叉」。

    查下来**三处分叉**，五份实现、三种行为，而且**没有一处会报错**：

        ① 代码口径  stock.kline('sh513120')    -> 10 根
                    stock.kline('513120.XSHG') -> 取不到（账本记的就是这个口径）
                    `stock.alt_kind` 的 docstring 自己写着"只认 tdx symbol，
                    别的写法一律当股票走原路" —— 换算一直有（`to_symbol`），只是没调
        ② 取名      watchlist / alerts 只查【股票面板】-> ETF 名字是空的
                    -> 页面 `name || code` 回落成代码（实测自选 5 只 ETF 全中）
        ③ 名称清洗  lv/tdx 去了 U+FFFD、stock._alt_name 没去
                    -> **同一只票在两个页面上两个名字**

    正本现在是 `assay/symbols.py`；`lv/tdx.py` 只剩转发门面。

    判据四条，每条对应一种**不报错**的坏法：
      ① 聚宽口径与 symbol 口径必须**逐位相同**（只测"能取到"的话，
         两条路各自算出不同的数也照样绿）
      ② 清洗只许有一处：全仓扫 `\\ufffd`，除 `symbols.clean_name` 外不许出现；
         且四个取名出口给出的名字都不含它
      ③ 结构守卫：除 `symbols.py` 外，任何模块不许再出现
         「自己查 sec_name + 自己回落 tdx」那个组合 —— 那就是第六份实现
      ④ 股票**不许**被拖去 ETF 那条路（反向自证）；面板里查不到的股票
         仍要标 `missing`，否则"退市"与"我加的票不见了"分不出来
    """
    import io as _io
    from assay import symbols as _S
    from assay import stock as _stk

    # ---------- ① 两种口径逐位相同 ----------
    PAIRS = [('513120.XSHG', 'sh513120'), ('510880.XSHG', 'sh510880')]
    for jq, sym in PAIRS:
        assert _S.as_symbol(jq) == sym and _S.to_jq(sym) == jq, (jq, sym)
        for fq in ('bfq', 'qfq', 'hfq'):
            a = _stk.kline(jq, n=40, fq=fq)['bars']
            b = _stk.kline(sym, n=40, fq=fq)['bars']
            assert a and a == b, \
                '%s 与 %s 的 %s K 线不一致 —— 代码口径又分叉了' % (jq, sym, fq)
        pa, pb = _stk.profile(jq), _stk.profile(sym)
        assert pa.get('sec_name') and pa.get('sec_name') == pb.get('sec_name'), \
            '%s / %s 的名字不一致' % (jq, sym)

    # ---------- ② 清洗只许有一处 ----------
    # 🔴 判据走 **ast**，不查字符串 —— 本项目记过三次：查字符串会命中
    #   **自己写的注释/docstring**（这里 stock.py 与 symbols.py 的 docstring
    #   里都提到了这个字符，第一版就是这么误报的）。注释不是 AST 节点。
    import ast as _a
    hits = []
    for r, ds, fs in os.walk(os.path.join(REPO, 'assay')):
        if '__pycache__' in r:
            continue
        for fn in fs:
            if not fn.endswith('.py'):
                continue
            fp = os.path.join(r, fn)
            try:
                tree = _a.parse(_io.open(fp, encoding='utf-8').read())
            except SyntaxError:
                continue
            for nd in _a.walk(tree):
                if (isinstance(nd, _a.Call)
                        and isinstance(nd.func, _a.Attribute)
                        and nd.func.attr == 'replace' and nd.args
                        and isinstance(nd.args[0], _a.Constant)
                        and isinstance(nd.args[0].value, str)
                        and '\ufffd' in nd.args[0].value):
                    hits.append('%s:%d' % (os.path.relpath(fp, REPO), nd.lineno))
    assert len(hits) == 1 and hits[0].endswith('symbols.py:%s' % hits[0].split(':')[-1]) \
        and 'symbols.py' in hits[0], \
        'U+FFFD 的清洗不止一处（或不在正本里）：%s' % hits
    root = _S.default_root()
    for nm in list(_S.names(['513120.XSHG', '510880.XSHG']).values()) + \
              list(_S.alt_names(root, ['513120.XSHG']).values()) + \
              [_stk._alt_name('sh513120'), _stk.profile('513120.XSHG')['sec_name']]:
        assert '�' not in nm, '取名出口漏了清洗：%r' % nm

    # ---------- ③ 结构守卫：不许再长出第六份 ----------
    # 🔴 判据必须落到**单条 SQL 字符串**上，不是文件级 —— `alerts.py` 的
    #   row_number 是取 bars 的、`stock.py` 的是财务报告期去重，它们与取名
    #   毫无关系。文件级判据把这两个**消费方**误报了两轮（如实记一笔：
    #   这条判据我改了三版才对，前两版都是「判据比要证的事宽」）。
    # ★ 要禁的是「同一条 SQL 里既取 sec_name、又按 jq_code 取最近一行」——
    #   那正是 `symbols.names` 的内脏，重写它就是第六份实现。
    NEEDLE = 'row_number() OVER (PARTITION BY jq_code ORDER BY date DESC)'
    dup = []
    for r, ds, fs in os.walk(os.path.join(REPO, 'assay')):
        if '__pycache__' in r:
            continue
        for fn in fs:
            if not fn.endswith('.py') or fn == 'symbols.py':
                continue
            fp2 = os.path.join(r, fn)
            try:
                tree2 = _a.parse(_io.open(fp2, encoding='utf-8').read())
            except SyntaxError:
                continue
            for nd in _a.walk(tree2):
                if (isinstance(nd, _a.Constant) and isinstance(nd.value, str)
                        and 'sec_name' in nd.value and NEEDLE in nd.value):
                    dup.append('%s:%d' % (os.path.relpath(fp2, REPO), nd.lineno))
    assert not dup, ('这些地方自己又写了一遍「按 jq_code 取最近非空 sec_name」'
                     '那套 SQL —— 应该调 `symbols.names`：\n  ' + '\n  '.join(dup))
    # 反向自证：正本里**必须**有那条 SQL，否则上面这条是空转的
    _src_sym = _io.open(os.path.join(REPO, 'assay', 'symbols.py'),
                        encoding='utf-8').read()
    assert 'sec_name' in _src_sym and NEEDLE in _src_sym, \
        '正本里没有那条 SQL —— 结构守卫成了空转'

    # ---------- ④ 反向自证：股票不许被拖去 ETF 那条路 ----------
    for c in ('601857.XSHG', '000001.XSHE', '300750.XSHE'):
        assert _S.kind_of(c) is None, '%s 被误判成了 %s' % (c, _S.kind_of(c))
        assert _stk.alt_kind(c) is None, '%s 走了 ETF/指数那条路' % c
    # 面板里查不到的**股票**仍要标 missing（退市 ≠ ETF）
    assert _S.kind_of('000003.XSHE') is None, '退市股票被判成了 ETF/指数'
    # 🔴 **这条必须构造**：`alt_names('000003.XSHE')` 在当前快照上本来就返回
    #   空，所以"去掉 kind 门控"在真实数据上**可观察行为完全相同** ——
    #   变异测试第一轮就是这么漏的。而门控不是冗余：快照里将来完全可能
    #   出现退市股票，那时没有门控就会把它"救"回来、missing 再也标不出来。
    #   （同「真实数据触发不到的上限，判据必须能构造出来」那条。）
    import tempfile as _tf
    import shutil as _sh
    from assay import watchlist as _wl
    _old_live, _old_alt = _wl.LIVE, _S.alt_names
    _tmp = _tf.mkdtemp(prefix='wlgate_')
    try:
        _wl.LIVE = _tmp                      # 🔴 不许写生产账本
        _wl.act('add', '000003')             # 早已退市
        _wl.act('add', '513120.XSHG')        # 真 ETF，同一批里做正向对照
        _S.alt_names = lambda rt, cs: {c: '假名字' for c in cs}
        _rows = {x['code']: x for x in _wl.valued()['rows']}
        _d = _rows.get('000003.XSHE') or {}
        assert _d.get('missing') and not _d.get('name'), \
            ('退市股票被 ETF 快照"救"回来了（name=%r missing=%r）—— '
             '那样"我加的票不见了"与"它退市了"就分不出来'
             % (_d.get('name'), _d.get('missing')))
        _e = _rows.get('513120.XSHG') or {}
        assert _e.get('name') == '假名字' and not _e.get('missing'), \
            '正向对照没走到：ETF 那条回落路径根本没执行（判据是空转的）'
    finally:
        _S.alt_names, _wl.LIVE = _old_alt, _old_live
        _sh.rmtree(_tmp, ignore_errors=True)
    return ('两种口径 2 只 × 3 复权逐位相同；清洗只有 1 处且 4 个出口都干净；'
            '无第六份实现；3 只股票未被误路由，退市股仍标 missing')


@case('归档批量删除：先弹窗列【服务端的】清单，受保护的默认不删（playwright）',
      tag='web')
def t_runs_batch_delete():
    """用户 2026-09-21："加一个策略回测结果批量删除的功能，可以多选回测结果，
    批量删除，删除之前要再弹窗确认。"

    形态取「🗑 管理」**模式开关**而不是共用比对那套勾选：比对上限是 4
    （理由是看得清），批量删除要能一次选几十个 —— 共用一套选中列表的话
    "上限对谁生效"就成了要记的事。同一列勾选框、两种含义，用显式模式分开。

    判据六条，每条对应一种**不报错**的坏法：
      ① 进管理模式时**比对工具条必须收起** —— 两条工具条同屏而勾选框只有
         一列的话，"我勾的这个是干嘛的"没法回答
      ② 选中数**可以超过比对上限 4** —— 不然"批量"没有意义
      ③ 弹窗里的清单必须来自**服务端 dry 预演**（拦接口看 `dry:true` 真的发了）
         —— 前端自己算一份的话，"页面说删 12、实际删了 15"不报错
      ④ **取消不删任何东西**（点了取消再数一遍归档）
      ⑤ 受保护的（标星）默认**不在删除清单里**，且弹窗里单列一块 + 逃生口
      ⑥ 确认之后**真的少了**，而且**结论落进了 _pruned_conclusions.jsonl**
         —— 「tar 备份 != 保留结论」那条：只删不留结论等于把那轮跑过什么弄丢
    🔴 全程跑在**临时归档**上（`ASSAY_RUNS` + `reg.set_runs`）：这条用例会
      **真的删目录**，不重定向的话一跑就毁生产归档 —— 比"污染一条"严重得多。
    """
    import glob as _g0
    import json as _js
    import shutil as _sh0
    import tempfile as _tf
    import threading
    from http.server import ThreadingHTTPServer
    from assay import registry as reg
    from assay import server as sv
    sv._scan()

    _runs_tmp = _tf.mkdtemp(prefix='selftest_runs_')
    _seed_by = {}
    for _m in sorted(_g0.glob(os.path.join(reg.RUNS, '*/*/*/meta.json'))):
        _pp = _m.split(os.sep)
        _seed_by.setdefault((_pp[-4], _pp[-3]), []).append(_m)
    _n_seed = 0
    for (_grp, _st), _ms in _seed_by.items():
        for _m in _ms[-2:]:
            _src = os.path.dirname(_m)
            _dst = os.path.join(_runs_tmp, _grp, _st, os.path.basename(_src))
            os.makedirs(_dst, exist_ok=True)
            for _f in ('meta.json', 'stats.json', 'strategy.py', 'run.log'):
                _sp = os.path.join(_src, _f)
                if os.path.isfile(_sp):
                    _sh0.copy2(_sp, os.path.join(_dst, _f))
            _n_seed += 1
    assert _n_seed >= 6, '临时归档只有 %d 个 —— 这条用例要选超过 4 个' % _n_seed
    _prev_runs_env = os.environ.get('ASSAY_RUNS')
    os.environ['ASSAY_RUNS'] = _runs_tmp
    _prev_runs = reg.RUNS
    reg.set_runs(_runs_tmp)
    sv._scan()
    # ★ 挑一个种子归档标星，专门用来验"受保护的默认不删"
    # 🔴🔴 **不许碰真的 `picks.json`** —— 它是决策记录（标星同时是
    #   `prune_runs` 的保护依据）。原来这两条用例是"备份 -> 覆盖 -> finally
    #   还原"，而 2026-09-21 做变异测试时反复中断它，某一次没还原成功：
    #   **真文件少了 66 行星标**，是 `git status` 才发现的。
    #   备份再还原永远有这个窗口 —— 正确做法与 `lv.LIVE` / `ASSAY_RUNS`
    #   同一条：**重定向**（`srv.runs.MARKS_FILE`，`_prune_mod()` 会把它
    #   喂给 prune_runs），真文件一个字节都不写。
    import assay.srv.runs as R
    _marks_real = os.path.join(REPO, 'picks.json')
    _marks_bak = open(_marks_real, encoding='utf-8').read()
    _marks_p = os.path.join(_tf.mkdtemp(prefix='selftest_marks_'), 'picks.json')
    _prev_marks = R.MARKS_FILE
    R.MARKS_FILE = _marks_p
    _all = [os.path.basename(os.path.dirname(x)) for x in
            sorted(_g0.glob(os.path.join(_runs_tmp, '*/*/*/meta.json')))]
    _star = _all[0]
    _mk = _js.loads(_marks_bak)
    _mk[_star] = {'mark': 'star', 'note': 'selftest 临时', 'ts': 'x'}
    open(_marks_p, 'w', encoding='utf-8').write(_js.dumps(_mk, ensure_ascii=False))

    httpd = ThreadingHTTPServer(('127.0.0.1', 0), sv.Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            try:
                br = p.chromium.launch()
            except Exception as e:                          # noqa: BLE001
                return '跳过（浏览器不可用: %s）' % type(e).__name__
            pg = br.new_page(viewport={'width': 1500, 'height': 900})
            errs = []
            pg.on('pageerror', lambda e: errs.append(str(e)))
            dry_seen = []
            pg.on('request', lambda r: dry_seen.append(r.post_data or '')
                  if r.url.endswith('/api/runs/delete') else None)
            pg.goto('http://127.0.0.1:%d/#/runs' % port)
            pg.wait_for_selector('#delbar', timeout=20000)
            # ---- ① 进管理模式 -> 比对条收起 ----
            assert pg.eval_on_selector('#cmpbar', 'e => e.offsetParent !== null'), \
                '一进来比对条就该是可见的'
            pg.click('#delon')
            pg.wait_for_selector('#delgo', timeout=10000)
            assert pg.eval_on_selector('#cmpbar', 'e => e.offsetParent === null'), \
                '进了管理模式，比对工具条却还在 —— 同一列勾选框两种含义会说不清'
            # ---- ② 选中数可以超过比对上限 ----
            got = pg.evaluate("""() => {
              document.querySelectorAll('.nd[data-k]').forEach(e => e.click());
              const cbs = [...document.querySelectorAll('input[data-cmp]')]
                            .filter(e => e.offsetParent);
              const n = Math.min(cbs.length, 6);
              for (let i = 0; i < n; i++) cbs[i].click();
              return {sel: DELSEL.length, cmpmax: (typeof CMP_MAX === 'number' ? CMP_MAX : 4)};
            }""")
            assert got['sel'] > got['cmpmax'], \
                '只选到 %d 个（比对上限 %d）—— 批量删除被上限卡住了' % (got['sel'], got['cmpmax'])
            n_sel = got['sel']
            # ---- ③ 弹窗清单来自服务端 dry 预演 ----
            before = len(_g0.glob(os.path.join(_runs_tmp, '*/*/*/meta.json')))
            pg.click('#delgo')
            pg.wait_for_selector('#delok', timeout=15000)
            assert any('"dry": true' in d or '"dry":true' in d for d in dry_seen), \
                '弹窗没先向服务端要预演 —— 清单是前端自己算的，会和真删的对不上'
            box = pg.inner_text('.stbox')
            # 🔴 计划里说删几个，就得删几个 —— **判据跟着服务端的计划走**，
            #   不自己算。第一版我假设"只有我标星的那 1 个受保护"，而种子
            #   归档是从真归档复制的、里面本来就有标星的，于是实际删 2 个、
            #   断言期望 5 个（同「断言不许依赖真实数据碰巧如此」那条）。
            import re as _re2
            _m = _re2.search(r'确认删除\s*(\d+)\s*次', pg.inner_text('#delok'))
            assert _m, '确认按钮没写清要删几个：%r' % pg.inner_text('#delok')
            n_plan = int(_m.group(1))
            assert 1 <= n_plan < n_sel, \
                '计划删 %d / 选了 %d —— 要么全删（保护没生效）要么没得删' % (n_plan, n_sel)
            assert '不可撤销' in box, '弹窗没说清不可撤销'
            assert '_pruned_conclusions' in box, '弹窗没说结论会留下来'
            # ---- ⑤ 受保护的单列一块 + 逃生口 ----
            assert pg.locator('#delforce').count() == 1, \
                '标星的那条没被单列出来（或没给显式逃生口）'
            assert pg.eval_on_selector('#delforce', 'e => !e.checked'), \
                '逃生口默认就是勾上的 —— 那等于没有保护'
            # ---- ④ 取消不删 ----
            pg.click('#mclose')
            pg.wait_for_timeout(300)
            mid = len(_g0.glob(os.path.join(_runs_tmp, '*/*/*/meta.json')))
            assert mid == before, '点了关闭却删掉了 %d 个' % (before - mid)
            # ---- ⑥ 确认之后真的少了，且结论落盘 ----
            pg.click('#delgo')
            pg.wait_for_selector('#delok', timeout=15000)
            pg.click('#delok')
            pg.wait_for_function(
                '() => !document.querySelector("#delok")', timeout=20000)
            after = len(_g0.glob(os.path.join(_runs_tmp, '*/*/*/meta.json')))
            n_del = before - after
            assert n_del == n_plan, \
                '弹窗说删 %d 个，实际删了 %d —— 页面与服务端对不上' % (n_plan, n_del)
            cj = os.path.join(_runs_tmp, '_pruned_conclusions.jsonl')
            assert os.path.isfile(cj), '删了却没留结论 —— 那轮跑过什么就查无对证了'
            rows = [_js.loads(l) for l in open(cj, encoding='utf-8')]
            assert len(rows) == n_del, '结论只留了 %d 条，删了 %d 个' % (len(rows), n_del)
            blob = _js.dumps(rows, ensure_ascii=False)
            assert 'annual_return' in blob, \
                '结论里只有 meta 没有 stats —— 翻出来只知道跑过、不知道结果'
            assert os.path.isdir(os.path.join(_runs_tmp, *_star.split('|'))) or True
            assert _star in [os.path.basename(os.path.dirname(x)) for x in
                             _g0.glob(os.path.join(_runs_tmp, '*/*/*/meta.json'))], \
                '标星的那个被删了 —— 默认不删这条没生效'
            assert not errs, 'JS 报错 %d 处: %s' % (len(errs), errs[:2])
    finally:
        httpd.shutdown()
        R.MARKS_FILE = _prev_marks
        assert open(_marks_real, encoding='utf-8').read() == _marks_bak, \
            '真 picks.json 被动过了 —— 重定向没生效'
        if _prev_runs_env is None:
            os.environ.pop('ASSAY_RUNS', None)
        else:
            os.environ['ASSAY_RUNS'] = _prev_runs_env
        reg.set_runs(_prev_runs)
        sv._scan()
        import shutil as _sh
        _sh.rmtree(_runs_tmp, ignore_errors=True)
        # 🔴 marks 那个临时目录也要清 —— 原来只清了 _runs_tmp，于是
        #   **每跑一次漏 2 个**（2026-09-22 实测盘上积了 38 个）。
        #   泄漏不报错，只是慢慢把 $TMPDIR 塞满。
        _sh.rmtree(os.path.dirname(_marks_p), ignore_errors=True)
    return ('管理模式与比对互斥；选 %d 个（>比对上限 %d）；弹窗用服务端 dry 清单；'
            '取消不删；受保护的默认不删且有逃生口；说删 %d 个就删了 %d 个、'
            '结论 %d 条含指标' % (n_sel, got['cmpmax'], n_plan, n_del, len(rows)))


@case('归档批量删除：服务端那几道闸（confirm / 保护 / 存结论 / 认不出）', tag='fast')
def t_runs_delete_api():
    """页面那条 web 用例走的是"人怎么点"，而 UI **永远会传 confirm** ——
    所以服务端的几道闸只能在这里钉（变异实测：去掉 confirm 门控，
    web 用例照样绿）。

    判据五条，每条对应一种**不报错**的坏法：
      ① `dry=True` 一个文件都不许动（预演却把东西删了，最糟）
      ② 漏 `confirm` 必须**拒**，而且要说清缺什么（不是静默不动）
      ③ 受保护的（标星 / 账户绑定过）默认不删；给了 `force_protected` 才删
      ④ 删之前结论必须落进 `_pruned_conclusions.jsonl`，而且**带 stats**
         —— 只存 meta 的话翻出来只知道"跑过"、不知道结果，等于没留
      ⑤ 认不出的 run_id 单独报（`unknown`），不静默跳过
    🔴 全程在**临时归档**上（`ASSAY_RUNS` + `reg.set_runs`）—— 这条会真删目录。
    """
    import glob as _g0
    import json as _js
    import shutil as _sh0
    import tempfile as _tf
    from assay import registry as reg
    import assay.srv.runs as R
    import assay.srv.base as B

    _runs_tmp = _tf.mkdtemp(prefix='selftest_runs_')
    src = sorted(_g0.glob(os.path.join(reg.RUNS, '*/*/*/meta.json')))[:4]
    assert len(src) >= 4, '归档里样本不够（要 4 个）'
    made = []
    for _m in src:
        d = os.path.dirname(_m)
        dst = os.path.join(_runs_tmp, os.path.relpath(d, reg.RUNS))
        os.makedirs(dst, exist_ok=True)
        for f in ('meta.json', 'stats.json', 'strategy.py', 'run.log'):
            sp = os.path.join(d, f)
            if os.path.isfile(sp):
                _sh0.copy2(sp, dst)
        # 🔴 把 `code_sha256` 换成一个**没有账户绑过**的值。
        #   下面要断言"只有标星那个被挡下"，而归档另有一条保护
        #   （账户绑定过这个版本），且**它是集合相关的** —— `protected()`
        #   对每个绑定版本只留一次，留哪一次取决于传进去的那批。
        #   于是照真数据取样时，`prune_runs.py` 跑一次、或者多标几个星，
        #   held 就会莫名多出几个（2026-09-21 实测 2 个，而**产品是对的**）。
        #   改 sha 之后这道保护在临时盘上不存在，构造与真数据无关了
        #   （同「断言不许依赖真实数据碰巧如此」那条）。
        _mp = os.path.join(dst, 'meta.json')
        _mj = _js.loads(io.open(_mp, encoding='utf-8').read())
        _mj['code_sha256'] = 'selftest' + (_mj.get('code_sha256') or '')[:56]
        io.open(_mp, 'w', encoding='utf-8').write(
            _js.dumps(_mj, ensure_ascii=False))
        made.append(os.path.basename(d))
    _prev_runs_env = os.environ.get('ASSAY_RUNS')
    os.environ['ASSAY_RUNS'] = _runs_tmp
    _prev_runs = reg.RUNS
    reg.set_runs(_runs_tmp)
    B._scan()
    # 🔴🔴 **不许碰真的 `picks.json`** —— 它是决策记录（标星同时是
    #   `prune_runs` 的保护依据）。原来这两条用例是"备份 -> 覆盖 -> finally
    #   还原"，而 2026-09-21 做变异测试时反复中断它，某一次没还原成功：
    #   **真文件少了 66 行星标**，是 `git status` 才发现的。
    #   备份再还原永远有这个窗口 —— 正确做法与 `lv.LIVE` / `ASSAY_RUNS`
    #   同一条：**重定向**（`srv.runs.MARKS_FILE`，`_prune_mod()` 会把它
    #   喂给 prune_runs），真文件一个字节都不写。
    _marks_real = os.path.join(REPO, 'picks.json')
    _bak = open(_marks_real, encoding='utf-8').read()
    _marks_p = os.path.join(_tf.mkdtemp(prefix='selftest_marks_'), 'picks.json')
    _prev_marks = R.MARKS_FILE
    R.MARKS_FILE = _marks_p
    n_cnt = lambda: len(_g0.glob(os.path.join(_runs_tmp, '*/*/*/meta.json')))
    try:
        # 🔴 **换掉整份 picks**，只留一条 —— 种子是从真归档复制的，里面本来
        #   就有标星的，不换的话"受保护的恰好有几个"随真数据变（第一版就是
        #   这么写的，期望 1 个、实际 4 个）。同「断言不许依赖真实数据碰巧如此」。
        star = made[0]
        open(_marks_p, 'w', encoding='utf-8').write(
            _js.dumps({star: {'mark': 'star', 'note': 'selftest', 'ts': 'x'}},
                      ensure_ascii=False))
        ids = made + ['20990101-000000-deadbe']          # 掺一个认不出的
        # ---- ① dry 不动文件 ----
        n0 = n_cnt()
        p = R.api_runs_delete({}, {'run_ids': ids, 'dry': True})
        assert n_cnt() == n0, 'dry 预演动了文件'
        # ---- ⑤ 认不出的单独报 ----
        assert p['unknown'] == ['20990101-000000-deadbe'], \
            '认不出的 run_id 没单独报：%r' % (p['unknown'],)
        # ---- ③ 受保护的默认不删 ----
        assert p['n_held'] == 1 and p['held'][0]['run_id'] == star, \
            '标星的那个没被挡下：held=%r' % [h['run_id'] for h in p['held']]
        assert p['n_delete'] == len(made) - 1, \
            '计划删 %d 个，应是 %d' % (p['n_delete'], len(made) - 1)
        # ---- ② 漏 confirm 必须拒且说清 ----
        r = R.api_runs_delete({}, {'run_ids': ids})
        assert 'confirm' in (r.get('error') or ''), \
            '漏 confirm 却没拒（或没说清缺什么）：%r' % r.get('error')
        assert n_cnt() == n0, '漏 confirm 却把文件删了'
        # ---- 真删 ----
        r = R.api_runs_delete({}, {'run_ids': ids, 'confirm': True})
        assert n_cnt() == n0 - p['n_delete'], \
            '说删 %d 个，实际剩 %d（原 %d）' % (p['n_delete'], n_cnt(), n0)
        assert star in [os.path.basename(os.path.dirname(x))
                        for x in _g0.glob(os.path.join(_runs_tmp, '*/*/*/meta.json'))], \
            '受保护的被删了'
        # ---- ④ 结论落盘且带 stats ----
        cj = os.path.join(_runs_tmp, '_pruned_conclusions.jsonl')
        assert os.path.isfile(cj), '删了却没留结论'
        rows = [_js.loads(l) for l in open(cj, encoding='utf-8')]
        assert len(rows) == p['n_delete'], \
            '结论 %d 条、删了 %d 个' % (len(rows), p['n_delete'])
        assert 'annual_return' in _js.dumps(rows, ensure_ascii=False), \
            '结论里只有 meta 没有 stats —— 翻出来只知道跑过、不知道结果'
        # ---- ③ 逃生口：给了 force 才删得掉 ----
        r2 = R.api_runs_delete({}, {'run_ids': [star], 'confirm': True,
                                    'force_protected': True})
        assert r2.get('n_deleted') == 1 and n_cnt() == n0 - p['n_delete'] - 1, \
            '给了 force_protected 仍然删不掉受保护的 —— 硬拒不给出路，' \
            '最后会变成绕过整个入口'
        n_left = n_cnt()
    finally:
        R.MARKS_FILE = _prev_marks
        assert open(_marks_real, encoding='utf-8').read() == _bak, \
            '真 picks.json 被动过了 —— 重定向没生效'
        if _prev_runs_env is None:
            os.environ.pop('ASSAY_RUNS', None)
        else:
            os.environ['ASSAY_RUNS'] = _prev_runs_env
        reg.set_runs(_prev_runs)
        B._scan()
        import shutil as _sh
        _sh.rmtree(_runs_tmp, ignore_errors=True)
        # 🔴 marks 那个临时目录也要清 —— 原来只清了 _runs_tmp，于是
        #   **每跑一次漏 2 个**（2026-09-22 实测盘上积了 38 个）。
        #   泄漏不报错，只是慢慢把 $TMPDIR 塞满。
        _sh.rmtree(os.path.dirname(_marks_p), ignore_errors=True)
    return ('dry 不动文件；漏 confirm 被拒；认不出的单独报；标星默认不删、'
            'force 才删；结论 %d 条含指标；剩 %d 个' % (len(rows), n_left))


@case('日历网格与通用表格各只有一份实现（calGrid / tbl）', tag='web')
def t_shared_widgets_single_source():
    """🔴 两个"通用件"此前各有各的毛病，**两种都不报错**：

    ① **日历网格写了两份**：`shared/chart.js` 的 `calGrid` 与
       `views/run-detail.js` 的 `drawCal` —— 几何（周一起始的 lead 偏移 /
       pad 格 / 非交易日 / 星期表头 / 交易日计数）**逐行相同**，连
       `['一','二','三','四','五','六','日']` 那行都是两份。物证是
       2026-09-24 把日历格子从 `.cd` 改名成 `.cday` 时**两个文件都要动** ——
       漏一处的表现是"两页的日历从此长得不一样"，而它不报错。

    ② **`tbl()` 定义在 `views/run-detail.js`，被 `views/live-perf.js`
       跨域引用** —— 不是重复，是**放错了地方**：域文件拥有一个通用件，
       等于"谁先写谁就是家"。它与回测无关（实盘业绩页的每日持仓 /
       清仓记录也在用），所以 2026-09-24 搬进 `shared/common.js`。

    判据四段，**分工别记反**（少一段就往另一边飘）：

        A 源码  drawCal 必须调 calGrid + 星期表头字面量全站恰好 1 处
        B 源码  `function tbl(` 全站恰好 1 处，且在 shared/
        C 行为  calGrid 的几何：周一为第一列 / pad 数 == lead / head+note 两个开关
        D 行为  在【独立页面】上 tbl 真的可用、ⓘ 真的有样式、点表头真的排序

    🔴 D 那段是**构造**出来的：目前还没有独立 .html 用 `tbl()`，所以
      "它到底有没有真的进共享层"在现网是**不可观测**的 —— 不构造的话这条
      判据要等到将来有人用它才生效，而那时才发现就晚了（同「真实数据触发
      不到的上限，判据必须能构造出来」）。顺带它也钉住 `.thq` 不作用域化：
      那条样式原本写成 `#main th .thq`，而 `#main` **只有 index.html 有**。
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return '跳过（无 playwright）'
    import calendar as _cal
    import threading
    from http.server import ThreadingHTTPServer

    from assay import server as sv
    web = os.path.join(REPO, 'web')
    src = {f: io.open(os.path.join(web, f), encoding='utf-8').read()
           for f in _web_files(web, '.js') + _web_files(web, '.html')}

    # ---- A 日历网格只有一份 ----
    rd = src['views/run-detail.js']
    i = rd.index('function drawCal(S){')
    body = rd[i:rd.index('\n  drawDay(S);\n}\n', i)]
    assert 'calGrid(' in body, \
        ('`drawCal` 没有调 `calGrid` —— 网格几何又被抄了第二份，'
         '而两份分叉不报错，只是两页的日历慢慢长得不一样')
    wd = "['一','二','三','四','五','六','日']"
    hit = [f for f, s in src.items() if wd in s]
    assert hit, '构造不对：全站找不到星期表头那行字面量（是不是换了写法？）'
    assert hit == ['shared/chart.js'], \
        ('星期表头字面量出现在 %r —— 只许 `shared/chart.js` 有一处，'
         '多一处就是网格又被写了一遍' % hit)

    # ---- B tbl 只在共享层 ----
    owns = [f for f, s in src.items() if 'function tbl(' in s]
    assert owns == ['shared/common.js'], \
        ('`tbl()` 定义在 %r —— 它被回测详情页与实盘业绩页【两个域】调用，'
         '必须住在 shared/；留在某个域文件里就是"谁先写谁就是家"' % owns)

    httpd = ThreadingHTTPServer(('127.0.0.1', 0), sv.Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as p:
            try:
                br = p.chromium.launch()
            except Exception as e:                          # noqa: BLE001
                return '跳过（浏览器不可用: %s）' % type(e).__name__
            pg = br.new_page(viewport={'width': 1400, 'height': 1000})
            errs = []
            pg.on('pageerror', lambda e: errs.append(str(e)))

            # ---- C calGrid 的几何（index.html 才加载 chart.js）----
            pg.goto('http://127.0.0.1:%d/' % port, wait_until='networkidle')
            ym = '2026-02'
            nd = _cal.monthrange(2026, 2)[1]
            lead = _cal.weekday(2026, 2, 1)          # 周一 = 0，与 calGrid 同口径
            geo = pg.evaluate(
                """([ym, nd]) => {
                  const mk = o => {
                    const d = document.createElement('div');
                    d.innerHTML = calGrid(ym, {}, x => '', o);
                    return d;
                  };
                  const plain = mk({scale: 12});
                  const noNote = mk({scale: 12, note: false});
                  const withHead = mk({scale: 12, note: false,
                                       head: (a, b) => '<h3 id="_gh">' + a + '/' + b + '</h3>'});
                  const wd = [...plain.querySelectorAll('.calg .wd')].map(e => e.textContent);
                  return {wd: wd,
                          pad: plain.querySelectorAll('.cday.pad').length,
                          days: plain.querySelectorAll('.cday:not(.pad)').length,
                          note: plain.querySelectorAll('.note').length,
                          noteOff: noNote.querySelectorAll('.note').length,
                          head: (withHead.querySelector('#_gh') || {}).textContent || '',
                          headFirst: withHead.firstElementChild.id};}""",
                [ym, nd])
            assert geo['wd'] == ['一', '二', '三', '四', '五', '六', '日'], \
                ('星期表头不是周一起始：%r —— 换成周日起始的话整张日历'
                 '每一格都挪了位，而它不报错' % geo['wd'])
            assert geo['pad'] == lead, \
                '%s 的 pad 格 %d != 周一起始偏移 %d' % (ym, geo['pad'], lead)
            assert geo['days'] == nd, \
                '%s 的日格 %d != 当月天数 %d' % (ym, geo['days'], nd)
            # 两个开关各自生效：note 默认追加计数行、note:false 不追加、
            # head 给了就出现在网格【上方】（回测详情页那个 h3 靠它）
            assert geo['note'] == 1 and geo['noteOff'] == 0, \
                'note 开关没生效：默认 %d 个 / 关掉 %d 个' % (geo['note'], geo['noteOff'])
            assert geo['head'] == '%d/%d' % (0, nd), \
                ('head 拿到的交易日/非交易日计数不对：%r（空 items 下应是 0/%d）'
                 % (geo['head'], nd))
            assert geo['headFirst'] == '_gh', \
                'head 没排在网格上方 —— 回测详情页那个标题会掉到日历下面'

            # ---- D 独立页面上 tbl 真的能用 ----
            pg.goto('http://127.0.0.1:%d/market.html' % port,
                    wait_until='networkidle')
            out = pg.evaluate(
                """() => {
                  if (typeof tbl !== 'function') return {no: 1};
                  const box = document.createElement('div');
                  document.body.appendChild(box);
                  tbl(box, [{a: 3, b: 'x'}, {a: 1, b: 'y'}, {a: 2, b: 'z'}],
                      [{k: 'a', t: '值', h: '这一列的口径说明'}, {k: 'b', t: '名', l: 1}]);
                  const col = () => [...box.querySelectorAll('tbody tr')]
                      .map(r => r.children[0].textContent.trim());
                  const q = box.querySelector('th .thq');
                  // 🔴 尺寸要在元素【还在文档里】的时候量：getComputedStyle
                  //   返回的是活对象，box.remove() 之后 fontSize 变成空串，
                  //   parseFloat 给出 NaN —— 那看着像"样式没生效"，而其实
                  //   是判据自己量错了时候（第一版就这么假失败了一次）。
                  const _cs = q ? getComputedStyle(q) : null;
                  const fs = _cs ? parseFloat(_cs.fontSize) : null;
                  const va = _cs ? _cs.verticalAlign : null;
                  const before = col();
                  box.querySelectorAll('th')[0].click();
                  const after = col();
                  box.remove();
                  return {before: before, after: after, fs: fs, va: va,
                          hasQ: !!q};}""")
            assert not out.get('no'), \
                ('独立页面上没有 `tbl` —— 它没有真的进共享层'
                 '（搬到另一个 views/ 文件里同样只有 index.html 看得到）')
            assert out['before'] == ['3', '1', '2'], \
                '默认行序应照传入顺序，实得 %r' % out['before']
            assert out['after'] == ['3', '2', '1'], \
                ('点表头没有排序（实得 %r）—— 首次点击给降序' % out['after'])
            assert out['hasQ'], '列头的 ⓘ 没渲染出来（`c.h` 那条路断了）'
            # 🔴 判据要**钉那条规则自己声明的值**，不是"比某个数小"。
            #   第一版写的是 `fs < 12` —— 而把规则改回 `#main th .thq` 之后
            #   ⓘ 继承 th 的 11px，**仍然 < 12**，变异当场漏过
            #   （同「判据比要证的事宽」那条）。
            assert out['fs'] == 10 and out['va'] == 'top', \
                ('独立页面上 `.thq` 没拿到样式（字号 %r / vertical-align %r，'
                 '应是 10px / top）—— 那条规则原本写成 `#main th .thq`，'
                 '而 `#main` 只有 index.html 有。作用域化的样式最容易这样漏，'
                 '而漏了不报错' % (out['fs'], out['va']))
            assert not errs, '页面抛错：%r' % errs[:2]
            br.close()
    finally:
        httpd.shutdown()
    return ('日历网格一份（drawCal 调 calGrid、星期表头全站 1 处）；'
            'tbl 在 shared/common.js 一处；calGrid 几何 周一起始/pad %d/日格 %d、'
            'head+note 两个开关各自生效；独立页面上 tbl 可用、点表头排序、'
            'ⓘ 字号 %spx' % (lead, nd, out['fs']))
