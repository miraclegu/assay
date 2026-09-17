# -*- coding: utf-8 -*-
"""平台：服务与路由 / 页面框架与导航 / 归档管理 / 拆分守卫

这一份是 `selftest.py` 按**产品域**拆出来的一块（2026-09-17，见
`tests/_base.py` 的说明）。判据、注释、教训一个字都没动 —— 拆的是**归属**，
不是内容。
"""
from tests._base import *          # noqa: F401,F403  框架 + 共用辅助
from tests._base import (CASES, JQ, REPO, case, _run, _pages, _web_files,  # noqa: F401
                         _kset, _kmain, _kfq, _klog, _lp_tab, _via_pop,
                         _all_case_src)
import os, re, sys, io, json, glob, time, shutil, subprocess, datetime  # noqa: E401,F401


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
            cal_days = pg.locator('#cal .cd:not(.pad)').count()
            cal_off = pg.locator('#cal .cd.off').count()
            real = sum(1 for d in eqj['d'] if d[:7] == mk)
            assert cal_days == nd, '日历格 %d != 当月天数 %d' % (cal_days, nd)
            assert cal_days - cal_off == real, \
                '日历交易日 %d != 曲线里的 %d' % (cal_days - cal_off, real)
            # 点某一天 -> 当日持仓/买卖（读 /api/day，仍是现有归档）
            cd0 = pg.locator('#cal .cd[data-d]').first
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

    bak = sv.MARKS_FILE + '.selftest-bak'
    had = os.path.isfile(sv.MARKS_FILE)
    if had:
        shutil.copy2(sv.MARKS_FILE, bak)
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
                assert _th == 10, '列数应是 策略·参数 + 区间 + 8 个指标：%d' % _th
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
        if had:
            shutil.move(bak, sv.MARKS_FILE)
        elif os.path.isfile(sv.MARKS_FILE):
            os.remove(sv.MARKS_FILE)


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
            live_sec = pg.locator('#main .lvsec').first.inner_text()
            assert '总资产' in live_sec and '持仓浮盈' in live_sec, \
                '实盘那块没渲染出数字：%s' % live_sec[:120]

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
    # 每一处重定向都要在 finally 里还原
    SELF = 'selftest 不许写进生产归档'
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
    for case_name, marker in (('个股页面', 'wlmod.LIVE = tmp'),
                              ('新页面真实渲染', 'wl.LIVE = tmp'),
                              ('自选：append-only', 'wl.LIVE = tmp'),
                              ('买点清单', 'al.LIVE = tmp'),
                              ('买点页面', 'al.LIVE = tmp')):
        i = src_all.index("@case('" + case_name)
        j = src_all.index('\n@case(', i + 10)
        assert marker in src_all[i:j], \
            ('用例「%s」会写自选账本却没重定向 LIVE —— 会污染真账本' % case_name)

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
    # 流水是独立页，单独核（它的路由带参数）
    assert 'showFills' in defined and '/fills' in js, '成交流水独立页不见了'
    # 选股理由也是独立页（同成交流水：会越来越长 -> 服务端分页）
    assert 'showWhy' in defined and '/why' in js, '选股理由独立页不见了'
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


