# -*- coding: utf-8 -*-
"""数据平台：面板构建 / 同步与日历 / 字典 / 装配脚本 / 定时

这一份是 `selftest.py` 按**产品域**拆出来的一块（2026-09-17，见
`tests/_base.py` 的说明）。判据、注释、教训一个字都没动 —— 拆的是**归属**，
不是内容。
"""
from tests._base import *          # noqa: F401,F403  框架 + 共用辅助
from tests._base import (CASES, JQ, REPO, case, _run, _pages, _web_files,  # noqa: F401
                         _kset, _kmain, _kfq, _klog, _lp_tab, _via_pop)
import os, re, sys, io, json, glob, time, shutil, subprocess, datetime  # noqa: E401,F401


def _chain_dry(with_cwd=False):
    """同步链**真正会跑的那串命令**（`sync_daily.py --dry` 的输出）。

    🔴 正本 2026-09-26 从 `.sh` 搬进了 `.py`（Windows 上没有 bash），`.sh`
      只剩一行转发 —— 原来那几条扫 `.sh` 源码的判据当场全挂。
      **失败的是断言不是产品**，但它们保的东西一条都不能丢。
    ★ 所以改成钉 `--dry` 的输出：那是**会被执行的**东西，比查源码字符串
      严一档，而且天然躲开「查字符串命中自己写的注释」（本项目踩过五次）。
    """
    dl = os.path.join(os.path.dirname(REPO), 'datalake')
    r = subprocess.run([sys.executable, os.path.join(dl, 'sync_daily.py'),
                        '--dry'], capture_output=True, text=True, cwd=dl)
    assert r.returncode == 0, r.stderr[-400:]
    pairs = [(m.group(1), m.group(2)) for m in
             re.finditer(r'\$ \(cd ([^ ]+) && (.+)\)', r.stdout)]
    assert len(pairs) >= 8, '只解析出 %d 条命令 —— 判据在空转' % len(pairs)
    # 🔴 `with_cwd` 不是可有可无：链里有几步是**相对 cwd** 的
    #   （`daily_snapshot.py`、`scripts/check_data_anomaly.py`），
    #   丢掉 cwd 就解析不到那几个文件 —— 而它们恰恰是要守的那几个，
    #   **判据会静默缩小**（同「照清单拼会漏掉新文件」那条）。
    if with_cwd:
        return pairs
    return '\n'.join(c for _, c in pairs)


@case('数据版本指纹可用且稳定')
def t_fingerprint():
    """归档只记 datalake 路径的话，panel 重建一次同一策略结果就会变而看不出来。
    指纹用 (相对路径, 字节数, mtime) 而非内容哈希（panel 有 4GB+），
    代价是「重建出内容相同的文件」会误报 —— 方向安全：宁可误报不可漏报。"""
    feed = PanelFeed('2024-01-01', '2024-03-31')
    a = feed.fingerprint()
    b = feed.fingerprint()
    assert a == b, '同一份数据两次指纹不同 —— 不稳定'
    # 🔴 判据是**最小必需集 ⊆ 实际**，不是写死的相等 —— 写死的话加一个
    #   部件（2026-09-24 加 `factor`）就要手改一次，而"忘了改"的表现是
    #   假失败；反过来如果写成"随便几个都行"，**删掉**一个部件就没人发现，
    #   而那正是「数据变了而指纹没变」的入口。两头都要：必需的一个不能少，
    #   多出来的不拦。
    need = {'panel', 'std', 'index', 'factor'}
    got = set(a['parts'])
    assert need <= got, '指纹少了部件 %s（现有 %s）' % (sorted(need - got), sorted(got))
    #   声明与产出必须一致：`FINGERPRINT_PARTS` 里写了几个就该出来几个
    assert got == {n for n, _ in PanelFeed.FINGERPRINT_PARTS}, \
        '产出的部件与 FINGERPRINT_PARTS 声明的对不上：%s vs %s' \
        % (sorted(got), sorted(n for n, _ in PanelFeed.FINGERPRINT_PARTS))
    for k, v in a['parts'].items():
        assert v['n_files'] > 0, '%s 部件没有文件' % k
        assert v['hash'], '%s 缺哈希' % k
    return 'overall=%s (%s)' % (
        a['overall'], ' / '.join('%s %d' % (k, a['parts'][k]['n_files'])
                                 for k, _ in PanelFeed.FINGERPRINT_PARTS))


@case('面板构建必须确定 + 失败面板拒绝加载')
def t_deterministic():
    """★ 曾是不确定的：同一份源数据三次重建，FROEC 年化得到
    41.31% / 40.73% / 42.52%，跨度 1.8pp —— 意味着我们做过的每一次对比
    都含 ±0.9pp 不可归因的噪声，比任何一个确定性 bug 都更根本。

    根因：ASOF JOIN 右表键不唯一（补披露/延迟披露会在同一天公布多个报告期）。

    ★★ 2026-08-27 断言方向【反转】—— 原先要求 std/fin_quarterly 键唯一，
       那个要求本身是错的，且造成了更严重的缺陷：
       去重「同日多期取最新那期」会丢年报（A 股常在 4 月同日披露年报+一季报，
       23,398 组），而 np_ttm = 本期累计 + 【上年年报】 - 上年同期累计，
       于是 np_ttm/pe_ttm 大面积 NULL（面板 pe_ttm NULL 一度达 60.8%），
       策略被静默剔掉一半股票（`pe BETWEEN 5 AND 50` 遇 NULL 得 NULL）。

       所以现在：
         · std/fin_quarterly **必须**保留全部报告期 -> (code,pub_date) 必然重复
         · 去重只加在 ASOF 右表 _fin3_asof 上 -> 确定性仍然由它保证
       本用例改为核对这个新契约，并**直接验证年报覆盖**（缺失率是那次缺陷的
       直接指标），而不是核对一个已知有害的「键唯一」。
    """
    import os
    feed = PanelFeed('2024-01-01', '2024-03-31')
    dups = {}
    for tbl in ('fin_quarterly', 'fin_indicator_q'):
        f = os.path.join(feed.root, 'std', tbl + '.parquet')
        dups[tbl] = feed.con.execute(
            "SELECT count(*) - count(DISTINCT (code, pub_date)) FROM read_parquet('%s')"
            % f).fetchone()[0]
    # 两张 std 表都【应当】保留真实的「同日多期披露」重复 —— 它们是推导表，
    # np_ttm 之类的计算需要完整报告期。键唯一只是 ASOF 右表的要求。
    assert dups['fin_indicator_q'] > 0, \
        'fin_indicator_q 没有重复键，用例失去意义（源数据变了？）'
    assert dups['fin_quarterly'] > 0, \
        'fin_quarterly 键唯一 —— 说明去重又被加回推导链了，年报会被丢掉'

    # ★ 年报覆盖：上一次缺陷的直接指标。缺年报 -> np_ttm 断链 -> pe_ttm NULL
    fq = os.path.join(feed.root, 'std', 'fin_quarterly.parquet')
    miss_q3, miss_pct = feed.con.execute("""
        WITH y AS (SELECT code, year(report_date) yr,
             max(CASE WHEN month(report_date)=12 THEN 1 ELSE 0 END) a,
             max(CASE WHEN month(report_date)=9  THEN 1 ELSE 0 END) q
           FROM read_parquet('%s') GROUP BY 1,2)
        SELECT sum(CASE WHEN q=1 AND a=0 THEN 1 ELSE 0 END),
               100.0*sum(CASE WHEN q=1 AND a=0 THEN 1 ELSE 0 END)/nullif(sum(q),0)
        FROM y""" % fq).fetchone()
    assert miss_pct < 5.0, \
        '有 Q3 却缺年报 %d 组 (%.1f%%) —— 去重又在丢年报了（修复前是 34.6%%）' % (
            miss_q3, miss_pct)
    ttm_null = feed.con.execute(
        "SELECT 100.0*sum(CASE WHEN pe_ttm IS NULL THEN 1 ELSE 0 END)/count(*) "
        'FROM %s' % feed.panel).fetchone()[0]
    assert ttm_null < 45.0, \
        'pe_ttm NULL 率 %.1f%% 过高 —— 年报链条可能又断了（修复前 2025 年是 60.7%%）' % ttm_null

    pk = feed.con.execute(
        'SELECT count(*) - count(DISTINCT (date, jq_code)) FROM ' + feed.panel).fetchone()[0]
    assert pk == 0, '面板主键有 %d 个重复 —— ASOF 去重失效' % pk
    src = open(os.path.join(feed.root, 'build', 'build_panel_daily.py'),
               encoding='utf-8').read()
    # 确定性仍必须由 ASOF 右表的去重保证 —— 换了位置，不是取消
    assert '_fin3_asof' in src, 'ASOF 专用右表 _fin3_asof 不见了 —— 确定性保证丢失'
    assert 'PARTITION BY t.code, t.pub_date' in src, \
        '_fin3_asof 里没有按 (code, pub_date) 去重 —— ASOF 键可能不唯一'
    assert '_FAILED' in src, '构建脚本缺少 _FAILED 标记机制'
    assert '_FAILED' in open('assay/feed.py', encoding='utf-8').read(), \
        'PanelFeed 缺少拒绝加载失败面板的守卫'
    return ('两张 std 表都保留同日多期(fin_quarterly %d / fin_indicator_q %d) / '
            '有Q3缺年报 %.1f%% / pe_ttm NULL %.1f%% / 面板主键唯一 / '
            '_fin3_asof 去重与 _FAILED 守卫就位'
            % (dups['fin_quarterly'], dups['fin_indicator_q'], miss_pct, ttm_null))


@case('数据字典：读磁盘 md 并渲染，改文件即生效', tag='fast')
def t_docs():
    """★ 关键是【不烤内容进前端】这个承诺要真成立。

    验三件事：
      1. _DOCS 里每一篇都能解到实际文件（路径写错了会静默变成「缺」）
      2. markdown 渲染后不残留裸标记（表格分隔行、裸 ** 漏出来最常见）
      3. 改文件不重启服务就生效 —— 这是整个设计的理由，必须有断言守着
    """
    import re
    from assay import server as sv

    d = sv.api_docs({})
    miss = [x['rel'] for x in d['docs'] if not x['exists']]
    assert not miss, '这些 _DOCS 路径解不到文件: %s' % miss

    n_tab = n_row = 0
    for x in d['docs']:
        r = sv.api_doc({'key': x['key']})
        h = r['html']
        assert h and '<' in h, '%s 渲染为空' % x['key']
        # 裸 markdown 漏出来 = 渲染器没覆盖到某种语法。
        # 先剥掉 code/pre —— 里面的 `**kw`（如 Feed.query(sql, **kw)）是真内容不是漏出。
        bare = re.sub(r'<pre>.*?</pre>|<code>.*?</code>', '', h, flags=re.S)
        assert not re.search(r'\|\s*-{3,}', bare), '%s 表格分隔行没吃掉' % x['key']
        assert '**' not in bare, '%s 裸 ** 漏出' % x['key']
        assert not re.search(r'^#{1,6} ', bare, re.M), '%s 裸 # 漏出' % x['key']

        # 🔴 **一行切出来的格子比表头多 = 多出的那几格被【静默丢掉】**
        #   （`_md` 按 `range(len(head))` 取，超出的直接不渲染）。
        #   实测成因是文档里的 `\|`（转义竖线）：renderer 原来裸 `split('|')`，
        #   9 行被切碎。**裸 ** 那条只在加粗恰好跨过竖线时才抓得到** ——
        #   而内容被吃掉是每一行都在发生的（同「悄悄截断比查不出来更糟」）。
        src = open(sv._doc_path(x['rel']), encoding='utf-8',
                   errors='replace').read().split('\n')
        j, m = 0, len(src)
        while j < m:
            if re.match(r'^\s*\|.*\|\s*$', src[j]) and j + 1 < m \
                    and re.match(r'^\s*\|[\s:|-]+\|\s*$', src[j + 1]):
                nh = len(sv._cells(src[j])); j += 2
                while j < m and re.match(r'^\s*\|.*\|\s*$', src[j]):
                    nc = len(sv._cells(src[j]))
                    assert nc <= nh, ('%s 第 %d 行切出 %d 格而表头只有 %d 格，'
                                      '多出的会被静默丢掉: %s'
                                      % (x['key'], j + 1, nc, nh, src[j].strip()[:80]))
                    j += 1
                continue
            j += 1
        n_tab += h.count('<table')
        n_row += h.count('<tr')
    assert sv.api_doc({'key': 'nope_不存在'}) is None, '未知 key 应返回 None -> 404'

    # 改文件立即生效（这是本功能存在的全部理由）
    path = sv._doc_path([x for x in sv._DOCS if x[0] == 'trap'][0][3])
    raw = open(path, encoding='utf-8').read()
    token = '__assay_selftest_%d__' % len(raw)
    try:
        open(path, 'w', encoding='utf-8').write(raw + '\n\n' + token + '\n')
        h2 = sv.api_doc({'key': 'trap'})['html']
        assert token in h2, '改了 md 但接口没反映 —— 说明内容被缓存或烤死了'
    finally:
        open(path, 'w', encoding='utf-8').write(raw)
    assert token not in sv.api_doc({'key': 'trap'})['html'], '还原失败'

    return '%d 篇全部解析到，共 %d 张表 %d 行；改 md 不重启即生效' % (
        len(d['docs']), n_tab, n_row)


@case('数据字典页：侧栏 / 切篇 / 过滤 / 跨篇命中数', tag='web')
def t_docs_ui():
    """服务端渲染对了不代表页面能用 —— 过滤是 DOM 操作，只有真浏览器能验。

    重点验【跨篇命中数】：过滤时侧栏要标出每篇的命中行数，
    否则你只看得到当前这篇，而「这个概念在哪一篇」恰恰是最常问的。
    """
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
    errs = []
    try:
        with sync_playwright() as pw:
            b = pw.chromium.launch()
            pg = b.new_page()
            pg.on('pageerror', lambda e: errs.append('PAGEERROR %s' % e))
            pg.on('console',
                  lambda m: errs.append(m.text) if m.type == 'error' else None)
            pg.goto('http://127.0.0.1:%d/#/docs/trap' % port)
            # 🔴 **不许固定等待**（原来是 `wait_for_timeout(1500)`）——
            #   2026-09-24 全量跑时它 2.9 秒就报「侧栏只有 0 条」，而单跑
            #   6.9 秒通过：机器忙的时候 1500ms 读到的是**半截页面**，
            #   于是报出来像是产品坏了（同「等固定时间在机器忙时会读到
            #   半截表」那条 —— 那一条当初就是为这个写的，这里漏了）。
            #   ★ 调大那个数不是修法：它只是把阈值往后挪一点。
            try:
                pg.wait_for_function(
                    "() => document.querySelectorAll('.ditem').length >= 8"
                    " && document.querySelectorAll('#dc table.dt tbody tr').length > 30"
                    " && document.querySelector('#dc .dsrc')", timeout=30000)
            except Exception:                               # noqa: BLE001
                raise AssertionError(
                    '数据字典页没渲染完：侧栏 %d 条 / 表格 %d 行 / 控制台 %s'
                    % (pg.locator('.ditem').count(),
                       pg.locator('#dc table.dt tbody tr').count(),
                       (errs[0][:120] if errs else '无')))
            n_side = pg.locator('.ditem').count()
            assert n_side >= 8, '侧栏只有 %d 条' % n_side
            src = pg.locator('#dc .dsrc').inner_text()
            assert '4-按陷阱' in src, '打开的不是指定那篇: %s' % src
            tot = pg.locator('#dc table.dt tbody tr').count()
            assert tot > 30, '表格只渲染出 %d 行' % tot

            pg.fill('#dq', 'report_type')
            # ★ 过滤有 260ms 防抖 + 跨篇统计要打接口 —— 等**结果**（真的有行
            #   被藏起来），不等一个拍脑袋的毫秒数。
            try:
                pg.wait_for_function(
                    "n => document.querySelectorAll("
                    "  '#dc table.dt tbody tr:not([style*=\"display: none\"])'"
                    ").length < n", arg=tot, timeout=30000)
            except Exception:                               # noqa: BLE001
                pass            # 让下面那条断言去报（它的措辞更准）
            vis = pg.locator('#dc table.dt tbody tr:visible').count()
            assert 0 < vis < tot, '过滤没生效（%d/%d）' % (vis, tot)
            assert pg.locator('#dc .dhi').count() > 0, '命中处没高亮'
            hits = [t.inner_text() for t in pg.locator('.dmeta').all()]
            n_hit = len([x for x in hits if '命中' in x])
            assert n_hit >= 3, '跨篇命中数只标出 %d 篇' % n_hit
            assert pg.locator('.ditem.nohit').count() > 0, '无命中的篇没淡掉'

            pg.fill('#dq', '')
            pg.wait_for_timeout(700)
            back = pg.locator('#dc table.dt tbody tr:visible').count()
            assert back == tot, '清空过滤没恢复（%d != %d）' % (back, tot)

            pg.click('.ditem[href="#/docs/qmt"]')
            pg.wait_for_timeout(900)
            assert pg.locator('#dc table.dt').count() > 5, '切篇后表格没出来'
            pg.click('#top a.nav[href="/#/runs"]')
            pg.wait_for_timeout(900)
            assert pg.locator('.tree').count() >= 1, '从顶栏回不到回测目录'
            b.close()
    finally:
        httpd.shutdown()
    assert not errs, '控制台报错: %s' % errs[:3]
    return '侧栏 %d 篇 · %d 行 · 过滤 %d 行 · 跨篇命中标出 %d 篇 · 无控制台错误' % (
        n_side, tot, vis, n_hit)


@case('数据同步：日历对数 / 两条腿语义 / 脚本可执行', tag='fast')
def t_sync():
    """三条自证，都对应一个会静默出错的地方。

    1) **交易日历规则**：交易日 = 工作日 − tdx.raw_holidays。这条规则要能
       与权威日历（std/trading_calendar.parquet，从行情反推）**逐日一致**，
       否则外推到未来的调仓日就是错的 —— 而错的表现是「该调仓的日子没提示」
       或「休市日发一堆单」，两者都不报错。生成脚本每次都重跑对数、
       不一致就拒绝写出，这里核它确实还成立。
    2) **两条腿的落后语义不能混**：A 腿（行情）每个交易日必然有新数据，
       缺了就是同步没跑；B 腿（财务）是**事件驱动**，没公告的日子本来就
       没有新 pub_date，按交易日算落后是【必然误报】。混用会让页面天天
       标红，然后你就不看红字了 —— 告警失效比没有告警更糟。
    3) **同步脚本语法可执行**：它由 launchd 跑，坏了没人看得见。
    """
    import ast
    import datetime
    import json
    import subprocess
    import tempfile

    import duckdb

    root = REPO
    dl = os.path.join(os.path.dirname(root), 'datalake')
    tdx = os.path.join(root, 'raw', 'tdx', '_ingest', 'tdx.db')
    if not os.path.exists(tdx):
        return '跳过（没有 tdx.db）'

    # ---- 1) 日历规则对数 ----
    con = duckdb.connect()
    con.execute("ATTACH '%s' AS t (READ_ONLY)" % tdx)
    hol = {r[0] for r in con.execute('SELECT date FROM t.raw_holidays').fetchall()}
    truth = [r[0] for r in con.execute(
        "SELECT date FROM read_parquet('%s/std/trading_calendar.parquet') ORDER BY date"
        % dl).fetchall()]
    assert truth, '权威日历为空'
    lo, hi = truth[0], truth[-1]
    gen, d = [], lo
    while d <= hi:
        if d.weekday() < 5 and d not in hol:
            gen.append(d)
        d += datetime.timedelta(days=1)
    ts, gs = set(truth), set(gen)
    assert ts == gs, ('「工作日 − raw_holidays」不再等于权威日历：'
                      '漏判休市 %d 天 %s / 多判交易 %d 天 %s'
                      % (len(ts - gs), sorted(ts - gs)[:5],
                         len(gs - ts), sorted(gs - ts)[:5]))

    # 生成出来的 live 日历要有未来交易日，且来源可信
    calp = os.path.join(root, 'live', 'trade_calendar.json')
    assert os.path.exists(calp), '缺 live/trade_calendar.json —— 实盘拿不到下一个交易日'
    cal = json.load(open(calp, encoding='utf-8'))
    from assay import live as lv
    assert cal['source'] in lv.AUTHORITATIVE_CAL, \
        '日历来源 %r 不在可信名单里' % cal['source']
    nfut = len([x for x in cal['days'] if x > hi.isoformat()])
    assert nfut > 100, '未来交易日只有 %d 天，太少 —— 外推窗口不够' % nfut

    # ---- 2) 两条腿语义 ----
    ss = os.path.join(dl, 'build', 'sync_status.py')
    assert os.path.isfile(ss), '缺 %s' % ss
    r = subprocess.run(['python3', ss, '--json'], cwd=dl,
                       capture_output=True, text=True, timeout=180)
    assert r.returncode == 0, 'sync_status.py 挂了：%s' % (r.stderr or '')[-300:]
    st = json.loads(r.stdout)
    a = [i for i in st['items'] if i['leg'] == 'A']
    b = [i for i in st['items'] if i['leg'] == 'B']
    assert a and b, '两条腿都要有检查项'
    assert all(i.get('lag_days') is not None or i.get('error') for i in a), \
        'A 腿必须报「落后几个交易日」'
    assert all(i.get('lag_days') is None for i in b), \
        'B 腿不该报交易日落后（财务是事件驱动，按交易日算是必然误报）：%s' \
        % [i for i in b if i.get('lag_days') is not None]
    assert all('days_since' in i or i.get('error') for i in b), \
        'B 腿必须报「距今几天」'

    # ---- 2b) 「上次从聚宽抽取」是独立一维，不能靠数据内容反推 ----
    # 🔴 B 腿**事件驱动**：没公告的日子 pub_date 不前进。于是「距今 18 天」
    #   这一个数分不出两种情况：①昨天刚导、只是没新公告 ②两周没导。
    #   用户原话：「实际上财务数据我昨天已经导入了最新的，但是上面的最新时间
    #   不会更新，显得我好像没有更新一下。」
    # ★ 抽取时刻**只有抽取端知道**（聚宽研究环境的 now）—— 本地文件 mtime
    #   是下载/解压时刻，反推不出来。所以链条是：extract 写进包 →
    #   merge 落到 _manifest/jq_extract.json → sync_status 读出来。
    #   下面逐段测**真行为**（打 ROOT 补丁到临时目录），不测源码字符串。
    import importlib.util as _ilu

    def _load(path, name):
        sp = _ilu.spec_from_file_location(name, path)
        m = _ilu.module_from_spec(sp)
        sp.loader.exec_module(m)
        return m

    # (1) sync_status._extract_info：有 / 无 两支都要对
    ssm = _load(ss, '_ss_probe')
    with tempfile.TemporaryDirectory() as td:
        ssm.ROOT = td
        assert ssm._extract_info(datetime.date(2026, 9, 11)) is None, \
            '没有 _manifest/jq_extract.json 时必须返回 None（老包），不许猜'
        os.makedirs(os.path.join(td, '_manifest'))
        json.dump({'extracted_at': '2026-09-10 20:26:48',
                   'extract_date': '2026-09-10', 'since': '2026-08-20',
                   'data_max_date': '2026-09-09'},
                  open(os.path.join(td, '_manifest', 'jq_extract.json'),
                       'w', encoding='utf-8'))
        ex = ssm._extract_info(datetime.date(2026, 9, 11))
        assert ex and ex['days_since_extract'] == 1, \
            '抽取距今算错：%r' % (ex,)
        # 🔴 关键：这一维必须与「数据内容多久没变」**脱钩** ——
        #   data_max_date 比 extract_date 早一天是常态（昨天抽的是前天的数据）
        assert ex['data_max_date'] == '2026-09-09' and \
            ex['extract_date'] == '2026-09-10', \
            '抽取时点与数据切点是两个字段，不能混：%r' % (ex,)
        # 坏 JSON 不许把整个状态接口带崩
        open(os.path.join(td, '_manifest', 'jq_extract.json'), 'w').write('{ 坏')
        assert ssm._extract_info(datetime.date(2026, 9, 11)) is None, \
            '坏 JSON 应降级为 None，不该抛异常'

    # (2) merge 侧：把包里的 _manifest.json 落到盘上，并补 merged_at
    mg = os.path.join(dl, 'build', 'merge_jq_increment.py')
    assert os.path.isfile(mg), '缺 %s' % mg
    mgm = _load(mg, '_mg_probe')
    with tempfile.TemporaryDirectory() as td:
        dst = os.path.join(td, 'jq_extract.json')
        mgm.MF_DST = dst
        src = os.path.join(td, 'pkg')
        os.makedirs(src)
        # 老包（没有 _manifest.json）：静默跳过，**不许建空文件**
        mgm._manifest(src, False, 'old.tar')
        assert not os.path.exists(dst), \
            '老包不该写出 jq_extract.json —— 那会让页面显示一个空记录'
        json.dump({'extracted_at': '2026-09-10 20:26:48',
                   'extract_date': '2026-09-10', 'data_max_date': '2026-09-09'},
                  open(os.path.join(src, '_manifest.json'), 'w',
                       encoding='utf-8'))
        mgm._manifest(src, True, 'new.tar')          # dry-run 不落盘
        assert not os.path.exists(dst), '--dry-run 不该写盘'
        mgm._manifest(src, False, 'new.tar')
        got = json.load(open(dst, encoding='utf-8'))
        assert got['extracted_at'] == '2026-09-10 20:26:48', \
            '抽取时刻丢了：%r' % (got,)
        assert got.get('merged_at') and got.get('tar') == 'new.tar', \
            'merge 侧要补 merged_at 与包名（"什么时候导进来的"）：%r' % (got,)

    # (3) extract 侧（在聚宽研究环境跑，本地不 import）：
    #     用 ast 确认 pack() 真的调 _manifest()、_save() 真的往 _stats 记。
    #     ★ 用 ast 而不是字符串 —— 注释不是 AST 节点，所以抓不到"我自己写的
    #       说明文字"（本会话已 8 次栽在这上面）。
    exs = os.path.join(dl, 'raw', 'jq', '_ingest', 'extract_jq_increment.py')
    assert os.path.isfile(exs), '缺 %s' % exs
    tree = ast.parse(open(exs, encoding='utf-8').read())
    fns = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    for need in ('_manifest', '_save', 'pack'):
        assert need in fns, 'extract 脚本缺 %s()' % need
    calls = {c.func.id for c in ast.walk(fns['pack'])
             if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
    assert '_manifest' in calls, \
        'pack() 必须调 _manifest() —— 否则包里没有抽取时刻，' \
        '而那时本地永远显示"未知"且不报错'
    stores = {t.value.id for t in ast.walk(fns['_save'])
              if isinstance(t, ast.Subscript)
              and isinstance(t.ctx, ast.Store) and isinstance(t.value, ast.Name)}
    assert '_stats' in stores, '_save() 必须往 _stats 记每张表的 max_date'
    # 🔴 光判「_stats 被写过」太松：`_save` 里有**两处** —— 一处是初始化
    #   `_stats[name] = {'rows': 0}`，一处才是记结果。变异掉后者时前者还在，
    #   于是断言照样绿（变异测试当场抓到）。所以还要判**真的记了 max_date**。
    # 🔴 「_save 里出现过 'max_date' 这个字面量」抓不到"记的那一处被改掉"
    #   —— print 那行也有一个 `'max_date' in d`（变异测试当场抓到，
    #   又是一次"判据比断言宽"）。所以**把这个函数抠出来真跑一遍**：
    #   整个模块 `from jqdata import *` 本地装不了，但单个函数可以 exec
    #   —— 它只用到 pd / os / OUT / _stats / _saved。
    import pandas as _pd
    _seg = ast.get_source_segment(open(exs, encoding='utf-8').read(),
                                  fns['_save'])
    with tempfile.TemporaryDirectory() as td:
        _ns = {'pd': _pd, 'os': os, 'print': lambda *a, **k: None,
               '_stats': {}, '_saved': [], 'OUT': td}
        exec(_seg, _ns)                                  # noqa: S102
        _ns['_save']('t', _pd.DataFrame(
            {'code': ['a', 'b', 'c'],
             'pub_date': ['2026-08-01', '2026-09-09', '2026-07-15']}))
        _g = _ns['_stats'].get('t') or {}
        assert _g.get('max_date') == '2026-09-09', \
            '_save() 要记这张表【实际抽到】的最大日期 —— 实得 %r。' \
            '不记的话包里只有行数，"数据切到哪天"就答不出来' % (_g,)
        assert _g.get('date_col') == 'pub_date' and _g.get('rows') == 3, \
            '日期列名与行数也要记：%r' % (_g,)
        # 空表也要有记录（否则页面上"这张表没抽到"与"没记"分不出来）
        _ns['_save']('empty', _pd.DataFrame())
        assert _ns['_stats'].get('empty', {}).get('rows') == 0, \
            '空表也要记一条 rows=0'

    # ---- 3) 脚本可执行 ----
    sh = os.path.join(dl, 'sync_daily.sh')
    assert os.path.isfile(sh), '缺 sync_daily.sh'
    r = subprocess.run(['bash', '-n', sh], capture_output=True, text=True)
    assert r.returncode == 0, 'sync_daily.sh 语法错误：%s' % r.stderr[-300:]
    # ★ 判据钉**真正会被执行的那串命令**，不是源码文本 ——
    #   注释是给人看的说明（`.sh`/`.py` 的文件头正解释了为什么不调那两个
    #   坏脚本），查源码必然命中它自己。正本已搬进 `.py`，见 `_chain_dry`。
    assert 'sync_daily.py' in open(sh, encoding='utf-8').read(), \
        '.sh 该是一行转发到正本 .py（Windows 上没有 bash）'
    code = _chain_dry()
    for bad in ('scripts/update.sh', 'scripts/full_update.sh'):
        assert bad not in code, \
            '同步链不该调 %s —— 它引用的 fast_update_indicators.py 不存在' % bad
    assert 'daily_snapshot.py' in code, 'PIT 快照那步不能少（漏一天永久丢失）'
    # 🔴 **ETF lake 也要每天建。** 2026-09-18 实测它停在 09-11 而主数据到
    #   09-17 —— 因为它一直是**手工**跑的（同「靠人记得跑的步骤 = 迟早不跑」）。
    #   后果是 ETF 模拟盘「推进到最新数据日」只能到 09-11，**而它不报错**。
    assert 'build_etf_lake.py' in code, \
        ('同步链里没有 ETF lake 那步 —— 它只能靠人记得手工跑，'
         '而 ETF 策略/模拟盘全跑在它上面')
    # 判据落在**顺序**上：它吃的是 load_tdx_kline 的产物（raw/tdx/kline）。
    #   只查"提到过这个名字"的话，把它挪到最前面照样绿，而那时它读的是
    #   **昨天**的 raw —— 又是一个不报错的静默错值。
    assert code.index('load_tdx_kline.py') < code.index('build_etf_lake.py'), \
        'ETF lake 排在 load_tdx_kline 之前 —— 它会用到昨天的 raw'
    plist = os.path.join(dl, '_manifest', 'com.miraclegu.finacial.sync.plist')
    assert os.path.isfile(plist), '缺 launchd plist'
    r = subprocess.run(['plutil', '-lint', plist], capture_output=True, text=True)
    assert r.returncode == 0, 'plist 格式错误：%s' % r.stdout

    la = max((i['lag_days'] for i in a if i['lag_days'] is not None), default=0)
    return ('日历 %d 天逐日一致（%s~%s）+ 未来 %d 天；A 腿落后 %d 交易日；'
            'B 腿距今 %s 天；脚本/plist 均可执行'
            % (len(truth), lo, hi, nfut, la,
               '/'.join(str(i.get('days_since')) for i in b)))


@case('数据同步页面真实渲染（playwright）', tag='web')
def t_sync_ui():
    """两条腿的表格要显示【不同语义】，并且告警必须真的渲染到页面上。

    ★ 判据在服务端（datalake/build/sync_status.py），页面只负责显示。
      这里核的是「显示没把两条腿混起来」—— A 腿报落后几个交易日，
      B 腿报距今几天。混了会让页面天天标红，然后你就不看红字了，
      告警失效比没有告警更糟。
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return '跳过（无 playwright）'
    import json as _json
    import threading
    import urllib.error
    import urllib.request
    from http.server import ThreadingHTTPServer

    from assay import server as sv
    old = sv.ALLOW_LIVE
    sv.ALLOW_LIVE = False          # 只读：断言手动同步按钮被置灰
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), sv.Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = 'http://127.0.0.1:%d' % port

    def _get(path):
        return urllib.request.urlopen(base + path, timeout=120).read().decode()

    def _post(path, body):
        rq = urllib.request.Request(
            base + path, data=_json.dumps(body).encode(),
            headers={'Content-Type': 'application/json'})
        return urllib.request.urlopen(rq, timeout=120).read().decode()

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
            pg.goto('http://127.0.0.1:%d/#/sync' % port, wait_until='networkidle')
            pg.wait_for_selector('.lvsec', timeout=90000)
            pg.wait_for_timeout(400)
            # ★ 界面上【不用】"A 腿/B 腿"这种内部术语 —— 对着屏幕看的人
            #   没有意义。内部字段名仍是 leg_a/leg_b（sync_status.py）。
            #   只看渲染出来的标题，不看整页源码（源码里有注释会误判）。
            _h3 = ' | '.join(pg.locator('.lvsec h3').all_inner_texts())
            assert '行情数据' in _h3 and '财务数据' in _h3, \
                '两个分区的标题没渲染：%s' % _h3
            assert 'A 腿' not in _h3 and 'B 腿' not in _h3, \
                '界面上不该出现"A 腿/B 腿"：%s' % _h3
            # ★ 只取【表格单元格】，不取整节文本 —— 本会话第四次栽在
            #   "断言匹配到自己写的说明文案"上：B 腿那节的说明里正写着
            #   「B 腿的"落后"不按交易日算」，于是 `'落后' not in b_txt` 必然失败。
            #   要断言渲染结果就只看渲染结果，别把旁边的散文一起吃进来。
            #   🔴 **不用位置索引 nth(0)/nth(1)** —— 这一页的 .lvsec 会
            #     增加（加「定时窗口」那块时就把两条腿挤后了一位），
            #     而错位的表现是"内容不对"而不是"找不到"，很难查。
            #     按 h3 标题找那一节。
            secs = pg.locator('.lvsec')
            def _sec(name):
                for i in range(secs.count()):
                    if name in secs.nth(i).locator('h3').inner_text():
                        return secs.nth(i)
                raise AssertionError('找不到「%s」这一节' % name)
            a_cells = _sec('行情数据').locator('table.lvt td').all_inner_texts()
            b_cells = _sec('财务数据').locator('table.lvt td').all_inner_texts()
            # ---- 定时窗口那块要真的渲染出来（源码结构在 fast 层另有用例）----
            _sw = _sec('定时窗口')
            assert _sw.locator('input.syf').count() == 6, \
                ('两个任务 × 从/到/间隔 = 6 个输入框，实得 %d'
                 % _sw.locator('input.syf').count())
            _swt = _sw.inner_text()
            #   🔴 判据是**实际装上的点位**那两列，不是回显配置
            assert '已装' in _swt and ('一致' in _swt or '未装' in _swt), \
                ('定时窗口没显示"实际装上的点位/是否一致" —— '
                 '只回显配置的话，"改了但没重装"看不出来：%s' % _swt[:200])
            #   ★ 输入框的值不进 inner_text（那是属性），要按值取。
            #     顺带钉住"补终点"在页面上确实是 09:20 而不是 09:00。
            _sv = [_sw.locator('input.syf').nth(i).input_value()
                   for i in range(6)]
            assert _sv[0] and _sv[1] and _sv[2], \
                '定时窗口的输入框是空的（服务端没给配置？）：%r' % (_sv,)
            assert _sv[4] == '09:20', \
                ('信号重算的窗口终点该是 09:20（开盘前最后一次），实得 %r'
                 % _sv[4])
            assert _sw.locator('#sysched').count() == 1, \
                '少了「保存并重装」按钮 —— 配置改了没法生效'
            a_txt, b_txt = ' | '.join(a_cells), ' | '.join(b_cells)
            assert a_cells and b_cells, '两条腿的表格都要有行'
            assert '落后' in a_txt or '最新' in a_txt, \
                'A 腿该显示「落后 N 个交易日」或「最新」，实得: %s' % a_txt[:120]
            assert '距今' in b_txt, \
                'B 腿该显示「距今 N 天」，实得: %s' % b_txt[:120]
            assert '落后' not in b_txt, \
                'B 腿的表格里不该出现「落后」—— 财务是事件驱动，'\
                '按交易日算是必然误报。实得: %s' % b_txt[:120]
            # ---- 「上次抽取」必须在页面上，且与数据内容切点**分开说** ----
            # 🔴 用户原话：「实际上财务数据我昨天已经导入了最新的，但是上面的
            #   最新时间不会更新，显得我好像没有更新一下。」根因：页面只显示
            #   数据内容的 pub_date，而 B 腿事件驱动 —— 没公告就不前进。
            _ex = _json.loads(_get('/api/sync')).get('status', {}).get('extract')
            # 🔴 先看有没有 h3 再取 —— 直接 `.first.inner_text()` 碰上一个
            #   没有 h3 的 `.lvsec` 会**裸等 30 秒**，报出来只有一句
            #   `Timeout 30000ms`，指不到"哪一块没有标题"（实测踩过）。
            _bsec = [s for s in pg.locator('.lvsec').all()
                     if s.locator('h3').count()
                     and '财务' in (s.locator('h3').first.inner_text() or '')]
            assert _bsec, '找不到「财务数据」那一块'
            _btxt = ' '.join(_bsec[0].inner_text().split())
            assert '上次抽取' in _btxt, \
                '财务数据那块必须写「上次抽取」—— 只显示数据内容的 pub_date 时，' \
                '刚导完也看着像 18 天没更新'
            if _ex is None:
                assert '未知' in _btxt, '没有 manifest 时要明说「未知（旧格式包）」，不许猜'
                jq_ex = '上次抽取 未知（当前包是旧格式）'
            else:
                _when = _ex.get('extracted_at') or _ex.get('extract_date')
                assert _when and _when in _btxt, \
                    '抽取时点没显示出来：%r 不在「%s」里' % (_when, _btxt[:200])
                # ★ 两个日期必须【同时】在页面上 —— 只有一个的话，
                #   「昨天抽的、数据切到前天」这句话就说不完整。
                # 🔴 判据必须**限定在那一行里**：日期字符串在下面那张 B 腿
                #   表格里也出现（财务指标 pub_date 恰好同一天），拿整块文本
                #   去匹配就是"判据比断言宽" —— 变异掉这一格照样绿。
                if _ex.get('data_max_date'):
                    _tags = ' '.join(_bsec[0].locator('.lvtags')
                                     .first.inner_text().split())
                    assert '数据切到 pub_date %s' % _ex['data_max_date'] in _tags, \
                        '数据切点没显示在标签行里：%s' % _tags[:200]
                _d = _ex.get('days_since_extract')
                if _d is not None:
                    _age = {0: '今天', 1: '昨天'}.get(_d, '%d 天前' % _d)
                    assert _age in _btxt, \
                        '要把"多久以前"直接说出来（%s）：%s' % (_age, _btxt[:200])
                # 🔴 告警判据必须是**抽取时点**而不是数据内容 ——
                #   刚导完还标红就是假告警，而假告警看多了就不看告警了。
                _mw = ' '.join(pg.locator('#main .lvwarn').all_inner_texts())
                if _d is not None and _d <= 14:
                    assert '财务数据' not in _mw or '没从聚宽抽取' not in _mw, \
                        '%d 天前刚抽过，不该报财务数据过期：%s' % (_d, _mw[:200])
                jq_ex = '上次抽取 %s（%s）· 数据切到 %s' % (
                    _when, _age if _d is not None else '?',
                    _ex.get('data_max_date') or '?')
            # ---- 告警判据：造两种数据直接验，不靠真实状态碰巧覆盖 ----
            # 🔴 真实状态是「抽取 1 天前 / 内容 18 天」—— 两种判据都不报警，
            #   所以只看真实页面的断言是**空转的**（变异测试当场抓到）。
            #   这里拦掉 /api/sync 造出两种相反的情形：
            #     ① 刚抽过、内容很旧  -> **不许**报警（原来的 bug 就是这一格）
            #     ② 很久没抽、内容很新 -> 必须报警
            _base = _json.loads(_get('/api/sync'))

            def _probe(days_extract, days_content):
                o = _json.loads(_json.dumps(_base))
                o.setdefault('status', {})['leg_b_days_since'] = days_content
                o['status']['extract'] = {
                    'extracted_at': '2026-01-01 00:00:00',
                    'extract_date': '2026-01-01', 'data_max_date': '2026-01-01',
                    'days_since_extract': days_extract}
                pg.route('**/api/sync', lambda r: r.fulfill(
                    status=200, content_type='application/json',
                    body=_json.dumps(o)))
                try:
                    pg.reload(wait_until='networkidle')
                    pg.wait_for_timeout(700)
                    return ' '.join(pg.locator('#main .lvwarn').all_inner_texts())
                finally:
                    pg.unroute('**/api/sync')

            _w1 = _probe(1, 60)
            assert '没从聚宽抽取' not in _w1 and '没更新' not in _w1, \
                '昨天刚抽过就不该报财务过期（内容旧是因为没公告）—— 实得：%s' % _w1[:200]
            _w2 = _probe(40, 0)
            assert '没从聚宽抽取' in _w2, \
                '40 天没抽必须报警，哪怕内容里恰好有新 pub_date —— 实得：%s' % _w2[:200]
            # 回到真实数据，后面的断言还要用
            pg.reload(wait_until='networkidle')
            pg.wait_for_timeout(800)
            # 日历来源要显示，且是可信来源（不然实盘会拿不到下一个交易日）
            from assay import live as lv
            head = pg.locator('.lvhead').inner_text()
            assert '日历' in head, '没显示日历来源'
            assert any(x in head for x in lv.AUTHORITATIVE_CAL), \
                '日历来源不在可信名单里: %s' % head
            # 只读模式下手动同步必须置灰（不能只靠前端 —— 接口也会拒，见 api_sync_run）
            assert pg.locator('#syrun').is_disabled(), '只读模式下「立即同步」应置灰'
            # ---- 自动同步开关：按钮文字必须跟【服务端复查到的状态】一致 ----
            #   ★ 不去真的 load/unload —— 那会改用户机器上的 launchd。
            #     这里核的是"状态读得对、文字对得上、只读被拦住"。
            au = _json.loads(_get('/api/sync/auto'))
            if au.get('supported') is False:
                assert pg.locator('#syauto').count() == 0,                     '不支持 launchd 的平台不该出现这个按钮'
                auto_note = '本平台无 launchd，按钮已隐去'
            else:
                assert au.get('on') in (True, False),                     'on 必须是明确的真假，不能是 None：%s' % au
                btn = pg.locator('#syauto').inner_text()
                assert btn == ('关闭自动同步' if au['on'] else '开启自动同步'),                     '按钮文字与实际状态不符：状态 on=%s，按钮「%s」' % (au['on'], btn)
                # 🔴 **不再 disabled** —— 项目纪律是「按钮一律不设 disabled」：
                #   disabled 的元素**连 title 提示都不触发**，点了没反应
                #   又无从自查（`--allow-backtest` 那次实测踩过）。
                #   **失败的是断言不是产品**，但它要保的东西一个不能丢 ——
                #   只读模式不许真去改 launchd。所以判据换成两条：
                #     ① 按钮点得动（可见 + 手型）
                #     ② **服务端拒**，且把原因说出来（那才是真正的防线；
                #        前端置灰挡不住任何人直接打这个接口）
                assert not pg.locator('#syauto').is_disabled(), \
                    '按钮一律不设 disabled —— 它连 title 都不触发'
                assert pg.evaluate(
                    "()=>getComputedStyle(document.querySelector('#syauto'))"
                    ".cursor") != 'not-allowed', '看着像点不动'
                _ro = _json.loads(_post('/api/sync/auto', {'on': True}))
                assert _ro.get('error') and '只读' in _ro['error'], \
                    ('🔴 只读模式下开关必须被【服务端】拒掉并说明原因，'
                     '实得：%s' % _ro)
                tag = pg.locator('.lvhead .lvtag').all_inner_texts()
                tz = ' '.join(tag)
                assert '自动同步' in tz, '顶栏要显示自动同步状态：%s' % tag
                assert ('开' in tz) == au['on'], '标签与状态不符：%s' % tz
                if au['on']:
                    # 🔴 标签上现在写的是**下一次什么时候**（用户问的正是这个），
                    #   完整口径进 title —— 原来那句「每日 16:00」是从**第一个**
                    #   点位抠出来的，25 个点位只报一个，**那是在说谎**。
                    #   所以判据分两头：标签说"下次"，title 说全窗口与间隔。
                    assert '下次' in tz, \
                        '开着的时候要说下一次什么时候扫：%s' % tz
                    _ti = pg.locator('.lvhead .lvtag').filter(
                        has_text='自动同步').first.get_attribute('title') or ''
                    assert au.get('schedule') and au['schedule'] in _ti, \
                        ('完整口径要进 title：%s / %s'
                         % (au.get('schedule'), _ti[:160]))
                    # 两条任务都要在 title 里（只报 sync 的话，tick 掉了没人发现）
                    for _t in (au.get('tasks') or []):
                        assert _t['name'] in _ti, \
                            'title 里少了「%s」：%s' % (_t['name'], _ti[:160])
                    # 开着就不该弹"自动同步是关的"那条告警 —— 假告警看多了就不看了
                    assert '自动同步是关的' not in pg.locator('#main').inner_text(),                         '开着却提示"关的"'
                else:
                    assert '永久丢失' in pg.locator('#main').inner_text(),                         '关着必须说清代价（daily_snapshot 漏一天永久丢失）'
                # 只读接口层也要拒，不能只靠按钮置灰
                r_auto = _json.loads(_post('/api/sync/auto', {'on': not au['on']}))
                assert r_auto.get('error'), '只读模式下接口应拒绝改自动同步'
                assert _json.loads(_get('/api/sync/auto')).get('on') == au['on'],                     '被拒之后状态不该变'
                auto_note = '自动同步 %s%s（按钮文字一致、只读双层拦住）' % (
                    '开' if au['on'] else '关',
                    ' · ' + au['schedule'] if au.get('schedule') else '')
            # ---- 财务数据导入：取代码 + 上传 ----
            #   ★ 代码正本是磁盘上那个 extract 脚本，服务端只替换
            #     SINCE/QUARTERS。前端另存一份就一定会分叉。
            assert pg.locator('#jqcode').count() == 1, '缺「取聚宽代码」按钮'
            assert pg.locator('#jqfile').count() == 1, '缺上传入口'
            jc = _json.loads(_get('/api/sync/jq_code'))
            assert not jc.get('error'), '取聚宽代码失败：%s' % jc.get('error')
            assert 'from jqdata import' in jc['code'], \
                '给出的不像聚宽研究环境的代码'
            assert 'def pack()' in jc['code'] and 'tarfile' in jc['code'], \
                '代码必须自己打包成一个文件（否则要下载十几个 csv）'
            # SINCE 要按【本地最落后那张表】往前留重叠，不能是脚本里的旧值
            sg = jc['suggest']
            if sg.get('since'):
                assert sg['since'] < sg['oldest'], \
                    'SINCE(%s) 必须早于本地最落后的 pub_date(%s) —— ' \
                    '留重叠是刻意的：重叠不重复，缺口会静默丢数据' \
                    % (sg['since'], sg['oldest'])
                import re as _re2
                m_s = _re2.search(r"^SINCE = '([^']*)'", jc['code'], _re2.M)
                assert m_s and m_s.group(1) == sg['since'], \
                    '代码里的 SINCE 没被替换成 %s' % sg['since']
                assert not jc.get('warn'), '替换出了问题：%s' % jc.get('warn')
            # 只读模式下上传必须被拒（按钮置灰之外，接口也要拒）
            _rq = urllib.request.Request(
                base + '/api/sync/jq_upload', data=b'x' * 16,
                headers={'X-Filename': 'a.tar',
                         'Content-Type': 'application/octet-stream'})
            try:
                _up = _json.loads(urllib.request.urlopen(_rq, timeout=30).read())
            except urllib.error.HTTPError as e:
                _up = _json.loads(e.read())
            assert _up.get('error') and '只读' in _up['error'], \
                '只读模式下上传应被拒：%s' % _up
            # 日志可点开
            n_log = pg.locator('a.sylog').count()
            if n_log:
                pg.locator('a.sylog').first.click()
                pg.wait_for_timeout(600)
                out = pg.locator('#syout').inner_text()
                assert '每日数据同步' in out, '日志内容没渲染出来: %s' % out[:120]
            br.close()
            assert not errs, '页面有运行时错误：%s' % errs[:3]
            return ('两条腿语义分离（行情报交易日落后 / 财务报距今天数）；'
                    '界面无"A 腿/B 腿"内部术语；日历来源可信；'
                    '只读拦住手动同步与上传；%s；%s；'
                    '聚宽代码可取且 SINCE 按本地最落后表(%s)预填成 %s；'
                    '定时窗口 6 个输入框可改、显示【已装】点位与是否一致'
                    '（信号重算终点 %s，补的终点）、有「保存并重装」；'
                    '%d 份日志可点开'
                    % (auto_note, jq_ex, sg.get('oldest'), sg.get('since'),
                       _sv[4], n_log))
    finally:
        httpd.shutdown()
        sv.ALLOW_LIVE = old


@case('业绩预告 CSV：修读 / 内容校验 / 多解析器分歧', tag='fast')
def t_forcast_csv():
    """★ 这条用例存在的理由：三个解析器给三个答案，其中**两个不报错**。

    `stk_fin_forcast.csv` 的 content 是大段中文正文，含换行、含千分位逗号，
    且有 10 条记录引号未闭合（上游写坏的）：
        csv.reader   50,650 行（从未闭合处开始串行）
        pandas      124,094 行（不报错，含 11 行垃圾）
        DuckDB      直接报 state machine invalid
        修读        124,083 行（权威）

    那 11 行垃圾曾静默进 parquet 并存活一周 —— 因为当初只校验
    「落盘行数 == 读入行数」，**两边一样错，检查照样通过**。
    """
    import sys as _s
    dl = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), 'datalake')
    csvp = os.path.join(dl, 'raw', 'jq', '_ingest', 'downloads',
                        'stk_fin_forcast.csv')
    if not os.path.exists(csvp):
        return '跳过（没有 stk_fin_forcast.csv）'
    _s.path.insert(0, os.path.join(dl, 'build'))
    from csv_repair import read_forcast_df, read_repaired, split_records
    import duckdb
    import re as _re

    # ---- 1) 修读：内容校验在函数内，读得出来就说明全过 ----
    df, st = read_forcast_df(csvp, verbose=False)
    n_fix = len(df)
    assert n_fix > 100000, '修读只得到 %d 行，太少' % n_fix
    assert df['id'].str.fullmatch(r'\d+').all(), 'id 有非数字'
    assert df['code'].str.fullmatch(r'\d{6}\.XSH[EG]').all(), 'code 有不合法'

    # ---- 2) 与裸 pandas 对照 ----
    # 🔴 **不能要求上游一直是坏的。** 头一版写 `assert n_bad > 0`（裸读必须
    #   产生垃圾行）—— 2026-09-10 那个增量包里上游**修好了**（0 条奇数引号，
    #   三个解析器与记录头判据四者一致都是 124,083），这条断言就把测试打红了。
    #   而"上游修好了"是**好事**，不该让测试失败。
    # ★ 改成**双态**：
    #     上游坏 -> 仍然钉住「裸读行数 − 垃圾行 == 修读行数」（修读没多吞少吞）
    #     上游好 -> 钉住「修读与裸读**逐行一致**」（这层不会把好文件读坏）
    #   两种情况都能验，而且不管上游怎么变都不会有假失败。
    import pandas as pd
    naive = pd.read_csv(csvp, dtype=str, encoding='utf-8-sig')
    n_bad = int((~naive['id'].fillna('').str.fullmatch(r'\d+')).sum())
    if n_bad:
        assert len(naive) - n_bad == n_fix, \
            ('裸读 %d 行 − 垃圾 %d 行 应等于修读 %d 行，实际不等 —— '
             '说明修读多吞或少吞了记录' % (len(naive), n_bad, n_fix))
        upstream = '坏（裸读 %d 行含 %d 行垃圾）' % (len(naive), n_bad)
    else:
        # 🔴 上游干净时，修读**不许**改变任何一行 —— 否则这层本身就是风险。
        assert len(naive) == n_fix, \
            ('上游是干净的（裸读无垃圾行），但修读给出 %d 行 ≠ 裸读 %d 行 —— '
             '这层把好文件读坏了' % (n_fix, len(naive)))
        for col in ('id', 'code', 'pub_date'):
            a = df[col].fillna('').reset_index(drop=True)
            b = naive[col].fillna('').reset_index(drop=True)
            assert (a == b).all(), \
                '上游干净时修读与裸读在 %s 列上不一致 —— 这层把好文件读坏了' % col
        upstream = '干净（裸读 %d 行、无垃圾）' % len(naive)

    # ---- 3) DuckDB 能不能直接读 ----
    # 🔴 同样不能要求它「必须读不了」。但**这层依然有存在价值**，
    #   理由与「DuckDB 读不读得了」无关：
    #     · 它按记录头切并**拼回续行**（实测这个干净文件里有 1068 个续行）
    #     · 它有四道**内容校验**（id 纯数字 / code 合法 / 两个日期列合法），
    #       那是「按位置切对了」的唯一证明 —— 当初 11 行垃圾静默进 parquet
    #       存活一周，就是因为只校验行数不校验内容（CLAUDE.md 那条教训）
    #   所以这里只**记录**状态，不拿它当失败判据。
    try:
        n_duck = duckdb.connect().execute(
            "SELECT count(*) FROM read_csv_auto('%s', all_varchar=true)" % csvp
        ).fetchone()[0]
        duck = '能读（%d 行%s）' % (
            n_duck, '，与修读一致' if n_duck == n_fix else '，**与修读不一致**')
        # ★ DuckDB 能读时，行数必须与修读一致 —— 不一致说明有一方错了，
        #   那是真问题（而不是"上游修好了"）。
        assert n_duck == n_fix, \
            ('DuckDB 读出 %d 行、修读 %d 行 —— 两个解析器分歧，必须查清'
             '（多解析器交叉验证比单个解析器的"成功"可信）' % (n_duck, n_fix))
    except AssertionError:
        raise
    except Exception as _e:                                 # noqa: BLE001
        # 🔴 **把异常类型带出来**，不要只说"读不了"。
        #   实测踩到：这里曾把一个 `NameError` 当成"DuckDB 读不了"报出去，
        #   于是摘要写着「DuckDB 仍报错」而它其实能读 —— 吞掉异常类型
        #   等于让一个**假结论**看着像验过了（同「禁止吞异常」那条）。
        duck = '读不了（%s: %s）' % (type(_e).__name__, str(_e)[:60])

    # ---- 4) 记录头判据不能退化成 ^\d+, ----
    #     content 里有千分位逗号（633,969.04元），松判据会把续行当新记录
    _, recs_ok, _ = split_records(csvp)
    _, recs_loose, _ = split_records(csvp, start_re=_re.compile(r'^\d+,'))
    assert len(recs_loose) > len(recs_ok), \
        ('松判据 ^\\d+, 应该切出【更多】记录（把千分位续行误判成新记录），'
         '实得 %d vs 严判据 %d —— 如果一样，说明这个文件里已经没有'
         '千分位逗号续行，判据可以放松' % (len(recs_loose), len(recs_ok)))

    # ---- 5) 落盘的 parquet 必须没有垃圾行 ----
    pq = os.path.join(dl, 'raw', 'jq', 'stk_fin_forcast.parquet')
    if os.path.exists(pq):
        con = duckdb.connect()
        n_pq, n_junk = con.execute(
            "SELECT count(*), count(*) FILTER ("
            "  try_cast(id AS BIGINT) IS NULL"
            "  OR NOT regexp_matches(code, '^[0-9]{6}[.]XSH[EG]$'))"
            " FROM read_parquet('%s')" % pq).fetchone()
        assert n_junk == 0, 'parquet 里还有 %d 行垃圾' % n_junk
        assert n_pq == n_fix, 'parquet %d 行 != 修读 %d 行' % (n_pq, n_fix)

    # ---- 5) 🔴 **注入一个坏文件，证明这层不是摆设** ----
    # 上游修好之后，前面那些断言都只能证明「这层没把好文件读坏」——
    # 证不了「它还有必要」。而缺陷来自上游（聚宽导出），下次导可能又带，
    # 所以要用**构造的坏文件**把这层的价值钉住：删掉一个引号，让引号数
    # 变成奇数（这正是 2026-09-01 定位到的那个缺陷，当时有 10 处）。
    # ★ 这也回答了「这层还有没有必要」：**有**。实测一个引号就能让裸读
    #   少 2,118 行并混进垃圾，而修读读得完全正确。
    import tempfile as _tf
    _bad = None
    try:
        with open(csvp, encoding='utf-8-sig', newline='') as _f0:
            _lines = _f0.read().split('\n')
        _k = next((i for i, l in enumerate(_lines)
                   if _re.match(r'^\d+,\d+,\d{6}\.XSH[EG],', l)
                   and l.count('"') >= 2), None)
        assert _k is not None, '找不到带引号的记录 —— 构造不出坏文件，这条测不到'
        _q = _lines[_k].index('"')
        _lines[_k] = _lines[_k][:_q] + _lines[_k][_q + 1:]   # 删一个引号
        _fd, _bad = _tf.mkstemp(suffix='.csv')
        with os.fdopen(_fd, 'w', encoding='utf-8') as _f:
            _f.write('\n'.join(_lines))
        _nv = pd.read_csv(_bad, dtype=str, encoding='utf-8-sig')
        _nbad = int((~_nv['id'].fillna('').str.fullmatch(r'\d+')).sum())
        # 裸读必须被这一个引号搞坏（少行 或 混进垃圾）
        assert len(_nv) != n_fix or _nbad > 0, \
            ('注入一个未闭合引号之后裸 pandas 居然还读对了（%d 行、%d 垃圾）'
             ' —— 那这个构造没有重现缺陷，这条断言是空转的'
             % (len(_nv), _nbad))
        _df2, _ = read_forcast_df(_bad)
        # 而修读必须仍然读出全部记录、且内容合法（read_forcast_df 内部会校验）
        assert len(_df2) == n_fix, \
            ('坏文件下修读给出 %d 行 ≠ 干净文件的 %d 行 —— 这层没能修好'
             % (len(_df2), n_fix))
        _fix_note = ('注入 1 个未闭合引号：裸读 %s 行/%d 垃圾，修读仍 %s 行'
                     % (format(len(_nv), ','), _nbad, format(len(_df2), ',')))
    finally:
        if _bad and os.path.exists(_bad):
            os.unlink(_bad)

    # ---- 6) 🔴 **字段错位必须被内容校验拦住** ----
    # 上一条注入的是"未闭合引号"，它只让**裸读**出错、修读照样正确 ——
    # 所以它证不了那四道内容校验有用。这一条补上：把一个**续行**伪装成
    # 记录头（`999,888,000001.XSHE,` 前缀），于是那条记录被切成两半、
    # 后半段的字段整体错位。
    # ★ 实测：这时 `id` / `code` 恰好**仍然合法**（切出来的前三段就是我
    #   插进去的那三个），拦住它的是**日期列**那道校验 —— 四道校验各有分工，
    #   少任何一道都可能让错位静默通过。
    # 🔴 这正是 CLAUDE.md 那条教训的反面：当初只校验「行数 == pandas 读入
    #   行数」，两边一样错、检查照样通过，11 行垃圾静默进 parquet 活了一周。
    _bad2 = None
    try:
        with open(csvp, encoding='utf-8-sig', newline='') as _f1:
            _ls = _f1.read().split('\n')
        _st = [i for i, l in enumerate(_ls)
               if _re.match(r'^\d+,\d+,\d{6}\.XSH[EG],', l)]
        _kk = next((_st[i] for i in range(len(_st) - 1)
                    if _st[i + 1] - _st[i] > 1), None)
        assert _kk is not None, \
            '文件里没有带续行的记录 —— 构造不出字段错位，这条断言是空转的'
        _ls[_kk + 1] = '999,888,000001.XSHE,' + _ls[_kk + 1]
        _fd2, _bad2 = _tf.mkstemp(suffix='.csv')
        with os.fdopen(_fd2, 'w', encoding='utf-8') as _f2:
            _f2.write('\n'.join(_ls))
        try:
            read_forcast_df(_bad2, verbose=False)
            raise AssertionError(
                '字段错位（假记录头）居然通过了内容校验 —— 那四道校验'
                '（id/code/两个日期列）至少有一道失效了，而错位**不报错**、'
                '只是把垃圾写进 parquet')
        except ValueError as _ve:
            assert ('不是日期' in str(_ve) or 'id' in str(_ve)
                    or 'code' in str(_ve)), \
                '抛的错不是内容校验给的：%s' % _ve
            _mis_note = '字段错位被拦住（%s）' % str(_ve).split('有')[-1][:28]
    finally:
        if _bad2 and os.path.exists(_bad2):
            os.unlink(_bad2)

    # 🔴 摘要里的每一句都要来自**检测结果**，不许写死。
    #   头一版把「DuckDB 仍报错」硬编码在字符串里 —— 而它其实早就能读了
    #   （124,083 行，与修读一致）。**报告串在说谎，它看着像验过了**
    #   （同「数字自己算、别写死」那条）。
    return ('修读 %s 行；上游 %s；DuckDB %s；松判据多切 %d 条；%s；parquet 无垃圾行'
            % (format(n_fix, ','), upstream, duck,
               len(recs_loose) - len(recs_ok),
               _fix_note + '；' + _mis_note))


@case('只读查询器：写操作一律拒 / 自动 LIMIT（CLI）', tag='fast')
def t_query():
    """`datalake/build/query.py` —— 命令行的只读查询器。

    ★ 看板上那个 SQL 页**已取消**，但这个工具留着：它是命令行里最顺手的
      查数据方式，而且三层只读保证都在这里。

    ★ 只读要挡住的重点是 `COPY ... TO 'file'`：它**不需要**可写的数据库
      连接就能写文件系统。只靠"以 READ_ONLY 挂 lake.db"挡不住它，
      而被写坏的是 mart/ —— 之后每次回测都用坏面板，且不报错。
    """
    import json as _json
    import subprocess

    from assay import server as sv
    dl = sv._datalake_dir()
    sc = os.path.join(dl, 'build', 'query.py')
    assert os.path.isfile(sc), '缺 %s' % sc
    sys.path.insert(0, os.path.join(dl, 'build'))
    import query as qmod                                    # noqa: PLC0415

    BAD = [
        ("COPY (SELECT 1) TO '/tmp/x.csv'", 'COPY TO 能直接写盘'),
        ("COPY (SELECT 1) TO '/tmp/x.parquet' (FORMAT PARQUET)", '同上'),
        ('CREATE TABLE t AS SELECT 1', 'DDL'),
        ('CREATE OR REPLACE VIEW v AS SELECT 1', 'DDL'),
        ('INSERT INTO code_map VALUES (1)', 'DML'),
        ('UPDATE code_map SET a=1', 'DML'),
        ('DELETE FROM code_map', 'DML'),
        ('DROP TABLE code_map', 'DDL'),
        ('ALTER TABLE code_map RENAME TO x', 'DDL'),
        ("ATTACH '/tmp/e.db' AS e", '挂别的库'),
        ('INSTALL httpfs', '装扩展'),
        ('LOAD httpfs', '载扩展'),
        ("EXPORT DATABASE '/tmp/d'", '整库导出'),
        ("SET home_directory='/tmp'", '改设置'),
        ('SELECT 1; DROP TABLE code_map', '多语句'),
        ("SELECT * FROM read_text('/etc/passwd')", '读任意文本'),
        ('VACUUM', '维护语句'),
        ("SELECT 1 /* x */; COPY (SELECT 1) TO '/tmp/y'", '注释藏第二条'),
    ]
    for q, why in BAD:
        try:
            qmod.check(q)
            raise AssertionError('%s 应被拒：%r' % (why, q))
        except ValueError:
            pass
    # 误杀检查：注释里 / 列名里出现关键字不该被拒
    OK = [
        '-- 说明里提到 copy 和 create\nSELECT 42',
        'SELECT db_create_time FROM (SELECT 1 AS db_create_time)',
        'SELECT 1 OFFSET 0',            # offset 里含 set
        'WITH a AS (SELECT 1) SELECT * FROM a',
        'DESCRIBE SELECT 1',
        'SUMMARIZE SELECT 1',
    ]
    for q in OK:
        qmod.check(q)                   # 抛异常就是误杀

    # 自动 LIMIT + 截断必须说出来（悄悄少给几行比查不出来更糟）
    r = qmod.run("SELECT * FROM read_parquet('%s/mart/panel_daily/panel_*.parquet')"
                 % dl, limit=7)
    assert r['n'] == 7 and r['limit_added'] and r['truncated'], \
        '没写 LIMIT 时应自动加并标记截断：%s' % {k: r[k] for k in
                                        ('n', 'limit_added', 'truncated')}
    r2 = qmod.run('SELECT 1 LIMIT 1', limit=500)
    assert not r2['limit_added'] and not r2['truncated'], '已有 LIMIT 不该再加'
    assert qmod.MAX_LIMIT <= 50000, 'MAX_LIMIT 太大 —— 3127 万行会把内存吃光'

    # 表清单要覆盖 parquet，不能只有 lake.db
    schm = qmod.schema()
    names = {t['name'] for t in schm['parquet']}
    assert 'panel_daily' in names, \
        '表清单里没有面板 —— 它不在 lake.db 里，漏了等于只覆盖一半数据'
    pn = [t for t in schm['parquet'] if t['name'] == 'panel_daily'][0]
    assert len(pn['columns']) >= 70, '面板列数不对：%d' % len(pn['columns'])

    # CLI 真能跑
    out = subprocess.run(['python3', sc, '--json', '--sql-stdin', '--limit', '2'],
                         cwd=dl, input='SELECT 1 AS a', capture_output=True,
                         text=True, timeout=120)
    assert _json.loads(out.stdout)['rows'] == [[1]], 'CLI 跑不出结果'

    # ★ 页面那一层已取消 —— 接口和路由都不该再有，否则是"删了一半"
    assert not hasattr(sv, 'api_query'), '/api/query 还在'
    assert '/api/query' not in sv.ROUTES, '路由里还有 /api/query'
    web = os.path.join(REPO, 'web')
    for fn in _web_files(web, ('.html', '.js')):
        src = open(os.path.join(web, fn), encoding='utf-8').read()
        code = '\n'.join(ln for ln in src.split('\n')
                          if '//' not in ln and '/*' not in ln and '*' != ln.strip()[:1])
        assert "'#/query'" not in code and '"#/query"' not in code, \
            '%s 里还有指向已取消的查数据页的链接' % fn
    return ('只读拒 %d 类写操作（含 COPY TO / 多语句 / 注释藏第二条）、'
            '%d 种合法写法不误杀、自动 LIMIT 并标截断、表清单含面板 %d 列；'
            '页面那一层已彻底取消（无 api_query / 无路由 / 无残留链接）'
            % (len(BAD), len(OK), len(pn['columns'])))

@case('tdx 装配脚本：跨 OS 表 / 缩表护栏 / 定时环境自证', tag='fast')
def t_setup_tdx():
    """`datalake/setup_tdx.py` —— 换台机器把 tdx 链装回来的那个入口。

    这一条**不联网、不装东西**，只守"判据有没有写对"：
      ① 四个平台的资产名齐、且刻意没有 Darwin_x86_64（Intel Mac 没预编译包）
      ② CLI 用的是实测的 `--dburi`/`--min`，不是文档里的 `--dbpath`/`--minline`
      ③ 定时任务的 PATH 必须带上**当前解释器**所在目录
      ④ 缩表护栏与 schema 探针都在
    """
    import importlib.util
    import platform
    import re
    import shutil
    here = REPO
    p = os.path.join(os.path.dirname(here), 'datalake', 'setup_tdx.py')
    assert os.path.isfile(p), '找不到 setup_tdx.py'
    src = open(p, encoding='utf-8').read()
    spec = importlib.util.spec_from_file_location('setup_tdx', p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)

    # ---- ① 跨 OS 资产表 ----
    want = {('Darwin', 'arm64'), ('Linux', 'arm64'), ('Linux', 'x86_64'),
            ('Windows', 'x86_64')}
    assert set(m.ASSETS) == want, '资产表对不上 release：%s' % set(m.ASSETS)
    assert ('Darwin', 'x86_64') not in m.ASSETS, \
        ('Intel Mac 刻意没有预编译包（release 里只有 Darwin_arm64）——'
         '加了会装上一个跑不了的包')
    for k, v in m.ASSETS.items():
        assert v.startswith('tdx2db_') and (
            v.endswith('.tar.gz') or v.endswith('.zip')), '资产名不对：%s' % v
    # 架构名归一化：aarch64 / amd64 都要认（Linux 上 uname 给的是这两个）
    _o = platform.machine
    try:
        for raw, norm in (('aarch64', 'arm64'), ('arm64', 'arm64'),
                          ('AMD64', 'x86_64'), ('x86_64', 'x86_64')):
            platform.machine = lambda r=raw: r
            assert m._arch() == norm, '%s 应归一成 %s，实得 %s' % (
                raw, norm, m._arch())
    finally:
        platform.machine = _o

    # ---- ② CLI 实参（实测 v2026.5/v2026.8.12 都是这两个）----
    #   🔴 网上文档常写 --dbpath / --minline 1,5，本机 --help 是
    #     --dburi / --min（布尔）。抄错了表现为"命令直接不认"。
    assert "'--dburi'" in src, 'cron/init 应该用 --dburi'
    assert '--dbpath' not in src.replace('`--dbpath`', ''), \
        '用了文档里那个 --dbpath —— 实测的参数是 --dburi'
    assert '--minline' not in src.replace('`--minline 1,5`', ''), \
        '用了文档里那个 --minline —— 实测是布尔的 --min'
    # 🔴 不许出现 pip install tdx2db（PyPI 那个是同名的另一个项目，无 DuckDB）
    assert not re.search(r'^\s*[^#*\n]*subprocess.*pip.*tdx2db', src, re.M), \
        'pip 装 tdx2db —— PyPI 上那个是另一个项目，不支持 DuckDB'

    # ---- ③ 定时任务的 PATH 必须带当前解释器 ----
    #   🔴 launchd/systemd 的默认环境很窄，python3 会解析到系统那个
    #     （没装 duckdb），2/6 PIT 快照就炸 —— 而那一步漏一天不可逆，
    #     失败只写在 launchd 日志里。2026-09-03 真踩过。
    pth = m._sched_path()
    mine = os.path.dirname(os.path.abspath(sys.executable))
    assert pth.split(':')[0] == mine, \
        '定时 PATH 的第一段应是当前解释器的目录（%s），实得 %s' % (mine, pth)
    assert pth.count(mine) == 1, 'PATH 里重复了当前解释器目录：%s' % pth
    # ★ 签名跟着「装两个 timer」改了：_plist(label, args, times, tag)
    pl = m._plist(m.LABEL, ['/bin/bash', m.SH], [(18, 10)], 'sync')
    assert 'EnvironmentVariables' in pl and mine in pl, \
        'plist 丢了 EnvironmentVariables/PATH —— 这正是踩过的那个坑'
    for k in ('StartCalendarInterval', '<key>Hour</key><integer>18',
              '<key>Minute</key><integer>10', m.SH, m.ROOT):
        assert k in pl, 'plist 缺「%s」' % k
    # 🔴 tick 是**多时间点**的（07:00/08:00/09:00）—— StartCalendarInterval
    #   必须是 array。装三个 plist 会让"改一个点位"变成改三处。
    #   ★ 时间点不再是写死的常量（TICK_AT / POLL_*），改成看板可配的
    #     DEFAULT_SCHED + _manifest/schedule.json —— 用例跟着读配置。
    _tk_d = m.DEFAULT_SCHED['tick']
    _tk_t = m._range_times(_tk_d['from'], _tk_d['to'], _tk_d['every'])
    tk = m._plist(m.TICK_LABEL, ['python3', m.TICK_PY], _tk_t, 'tick')
    assert '<array>' in tk.split('StartCalendarInterval')[1][:200], \
        '多时间点时 StartCalendarInterval 应是 array'
    for hh in (7, 8, 9):
        assert '<key>Hour</key><integer>%d</integer>' % hh in tk, \
            'tick plist 少了 %02d:00 这个点位' % hh
    assert 'tick_daily.py' in tk and 'EnvironmentVariables' in tk, \
        'tick plist 没指向 tick_daily.py 或丢了 PATH'
    assert m._times('07:00,08:00,09:00') == [(7, 0), (8, 0), (9, 0)], \
        '_times 解析不对'
    # ---- 数据同步改成【轮询】：判据是"齐没齐"而不是"到点没到点" ----
    assert m.DEFAULT_SCHED['tick']['from'] == '16:00', \
        ('信号重算的窗口要从 16:00 开始 —— 公告集中在 16:00~22:00，'
         '而人常在晚上手动导聚宽增量，只开早上的话要等到 07:00 才算得进去')
    _sy_d = m.DEFAULT_SCHED['sync']
    rt = m._range_times(_sy_d['from'], _sy_d['to'], _sy_d['every'])
    assert len(rt) == 25 and rt[0] == (16, 0) and rt[-1] == (20, 0), \
        '16:00~20:00 每 10 分钟应是 25 个点位，实得 %d 个' % len(rt)
    assert (18, 10) in rt, \
        ('轮询序列要覆盖原来那个 18:10 —— 覆盖了才能删掉独立的 sync timer，'
         '少一处要对齐的时间常量')
    # 🔴 这条原来是**扫源码**里 `JOBS = [` 那一段查字符串 —— 把 JOBS
    #   抽成 `build_jobs()` 之后它当场 IndexError。失败的是断言不是产品，
    #   而它本来就该问**运行时到底传了什么**（查字符串还会命中注释）。
    _sync_job = [j for j in m.build_jobs(m.load_schedule()[0]) if j[3] == 'sync']
    assert len(_sync_job) == 1, 'build_jobs 里没有 sync 那条'
    assert '--if-stale' in _sync_job[0][1], \
        ('轮询必须带 --if-stale —— 不带的话每 10 分钟跑一次完整链，'
         '而且数据齐了还在跑：%r' % (_sync_job[0][1],))
    # 🔴 plist 必须能被【严格】解析：plutil -lint 会放过非法 XML
    #   （XML 注释里不能有两个连字符，而 Apple 的解析器宽容、launchd 照跑，
    #     Python 的 expat 直接拒绝 —— 严格的那个才是真判据）
    import plistlib as _pl
    # ★ 用**真实的** build_jobs 结果去生成 plist，不再手写一份参数 ——
    #   手写的那份与真实装的分叉时，这条断言照样绿（它就这么绿过：
    #   写着 `/bin/bash` 而 Windows 上根本跑不了）。
    for label, args, times, tag, _what in m.build_jobs(m.load_schedule()[0]):
        xml = m._plist(label, args, times, tag)
        try:
            d = _pl.loads(xml.encode())
        except Exception as e:                                  # noqa: BLE001
            raise AssertionError(
                '%s 的 plist 不是合法 XML（%s: %s）—— plutil -lint 会说 OK，'
                '但那是宽容解析；注释里出现两个连字符就会这样'
                % (tag, type(e).__name__, e))
        cal = d['StartCalendarInterval']
        cal = [cal] if isinstance(cal, dict) else cal
        assert len(cal) == len(times), \
            '%s 的点位数对不上：plist %d vs 期望 %d' % (tag, len(cal), len(times))
    # ---- is_stale.py 的四条判据 ----
    stale = open(os.path.join(os.path.dirname(here), 'datalake', 'build',
                              'is_stale.py'), encoding='utf-8').read()
    assert 'raw_holidays' in stale, \
        ('判交易日要用 tdx.raw_holidays —— std/trading_calendar.parquet '
         '**只到最后一个有数据的交易日**，用它会让每天都判成"不是交易日"，'
         '于是轮询永远不干活，而日志里只有一行「今天不是交易日」')
    #   ★ 用 ast 看**函数体**，不做子串匹配 —— 那个函数的 docstring 里
    #     正好在**警告**不要用 trading_calendar，子串匹配会把警告也算成"用了"
    #     （今天第三次踩这个坑了：断言别写成"字符串出现过"）。
    import ast as _ast
    _st = _ast.parse(stale)
    _fn = [n for n in _ast.walk(_st)
           if isinstance(n, _ast.FunctionDef) and n.name == '_is_trading_day']
    assert _fn, 'is_stale.py 里没有 _is_trading_day'
    _lits = [n.value for n in _ast.walk(_fn[0])
             if isinstance(n, _ast.Constant) and isinstance(n.value, str)]
    _code = ' '.join(_lits[1:])          # [0] 是 docstring
    assert 'trading_calendar' not in _code, \
        '_is_trading_day 的代码里还在读 trading_calendar.parquet'
    assert 'raw_holidays' in _code, \
        '_is_trading_day 应当查 raw_holidays（唯一含未来日的源）'
    assert 'manifest.csv' in stale and 'snap_date' in stale, \
        ('判据要含「今天的 PIT 快照抓过没有」—— daily_snapshot 漏一天'
         '永久丢失，不能只判行情齐不齐')
    assert 'MIN_RATIO' in stale and '0.95' in stale, \
        ('行数判据要用"不少于上一交易日的 95%"而不是固定阈值 —— '
         '新股上市/退市会让只数天天微变')
    assert 'after' in stale and '15:00' in stale, \
        '要有"过了收盘才抓"这条（盘中的 bar 是不完整的）'
    # plist 要能被系统解析（macOS 上真解一遍）
    if platform.system() == 'Darwin':
        import subprocess as _sp
        import tempfile as _tf
        with _tf.NamedTemporaryFile('w', suffix='.plist', delete=False,
                                    encoding='utf-8') as fh:
            fh.write(pl)
            tmp = fh.name
        try:
            r = _sp.run(['plutil', '-lint', tmp], capture_output=True,
                        text=True)
            assert r.returncode == 0, 'plist 不合法：%s' % r.stdout
        finally:
            os.remove(tmp)
    # 🔴 绝对路径不许硬编码我这台机器 —— 这个文件的意义就是换机器也能用
    assert '/Users/guhao' not in src, \
        '源码里硬编码了 /Users/guhao —— 换台机器就指错了'

    # ---- ④ 解压要把 Windows 反斜杠归一成目录（真跑一遍）----
    #   🔴 hsjday.zip 里 12,392 个条目全是反斜杠、零个目录条目，而 Python 的
    #     zipfile 按规范把反斜杠当普通字符 —— extractall 出来是平坦的怪文件名，
    #     tdx2db 一个都扫不到，报的却是"我的中间文件 stock.csv 不存在"。
    #     那个报错指不到真正的原因，所以这条必须有断言。
    import tempfile as _tf
    import zipfile as _zf
    d = _tf.mkdtemp()
    try:
        zp = os.path.join(d, 't.zip')
        with _zf.ZipFile(zp, 'w') as z:
            z.writestr('sh\\lday\\sh000001.day', b'x' * 8)
            z.writestr('sz\\lday\\sz000001.day', b'y' * 8)
        out = os.path.join(d, 'vip')
        n = m._extract_zip(zp, out)
        assert n == 2, '落盘文件数不对：%d' % n
        assert os.path.isfile(os.path.join(out, 'sh', 'lday',
                                           'sh000001.day')), \
            ('反斜杠没归一成目录 —— 解出来是 %s'
             % os.listdir(out))
        assert sorted(os.listdir(out)) == ['sh', 'sz'], \
            '应该建出 sh/sz 两个子目录：%s' % os.listdir(out)
        # 路径穿越要拒（extractall 的老 CVE 就是这个）
        bad = os.path.join(d, 'bad.zip')
        with _zf.ZipFile(bad, 'w') as z:
            z.writestr('..\\..\\evil.day', b'z')
        try:
            m._extract_zip(bad, os.path.join(d, 'v2'))
            raise AssertionError('带 .. 的路径应被拒')
        except SystemExit:
            pass
    finally:
        shutil.rmtree(d, ignore_errors=True)

    # ---- ⑤ init 之后必须紧跟 cron ----
    #   🔴 init 只导日线、自报"导入成功"，但 raw_adjust_factor /
    #     raw_basic_daily 是【空表】—— 复权因子要 gbbq，而 gbbq 是 cron 下的。
    b = src[src.index('def bootstrap('):src.index('def sync(')]
    assert "'init'" in b and "'cron'" in b, \
        'bootstrap 里必须 init 之后紧跟 cron（否则复权因子/基础面是空表）'
    assert b.index("'init'") < b.index("'cron'"), 'cron 应在 init 之后'
    assert 'raw_adjust_factor' in b and 'raw_basic_daily' in b, \
        'bootstrap 缺"这两张表不许是空的"那条断言'

    # ---- ⑥ 口径护栏（缩表护栏拦不住"值变了"）----
    #   🔴 通达信改过 .day 里 volume 的单位：老 vipdoc 是"股×100"、
    #     现在下的 hsjday.zip 是"股"。而 turnover 是 tdx2db 用
    #     volume/流通股数 算出来的 —— 重建一次，面板的 turnover 就从
    #     百分数变小数，全程差 100 倍，而**行数一行不少**。
    #     实测护栏输出：最近 250 天 861,032 行，中位 旧 2.34 / 新 0.0234。
    assert '_check_scale' in src, '缺口径护栏 —— 缩表护栏只看行数'
    assert 'turnover' in src, '口径护栏得盯 turnover（volume 单位变过）'
    _sc = m._check_scale(m.DB, m.DB) if os.path.isfile(m.DB) else None
    if _sc:
        assert abs(_sc[0] - 1.0) < 1e-9, \
            '自己比自己应得比值 1.0，实得 %s' % _sc[0]

    # ---- ⑦ 护栏在不在 ----
    assert 'allow_shrink' in src and 'before_init' in src, '缺缩表护栏'
    assert '_probe_schema' in src and '_meta' in src, '缺 schema 兼容探针'
    assert 'duckdb://./%s' in src or "duckdb://./" in src, 'init 目标写法不对'
    #  init 必须先落到别的文件，确认后才替换（不许直接覆盖 tdx.db）
    assert "DB + '.new'" in src, \
        'init 直接覆盖 tdx.db 了 —— 全量包不含退市股时历史会静默缩水'
    # ── 定时器【真正跑的命令】必须跨平台 ──────────────────────
    # 🔴 这条此前**完全没有判据**：两条相关守卫都是用例自己构造一份
    #   参数去调 `_plist`，而那证明不了真实装的是什么。于是 sync 那条
    #   写着 `/bin/bash …sync_daily.sh` 一直没人发现 ——
    #   **Windows 上没有 `/bin/bash`**，计划任务建得成功、到点执行失败，
    #   而失败只写在任务历史里（同「静默失效比报错贵」那条）。
    jobs = m.build_jobs(m.load_schedule()[0])
    assert len(jobs) == 2, '定时任务不是两条（sync + tick）：%d' % len(jobs)
    for label, args, times, tag, what in jobs:
        assert args and args[0] == sys.executable, (
            '%s 的命令没走 sys.executable：%r —— 写 `/bin/bash x.sh` 在 '
            'Windows 上根本跑不起来，写字面量 `python3` 则会在 launchd '
            '那种窄环境里解析到系统那个（没装 duckdb）' % (tag, args[0]))
        assert os.path.isfile(args[0]), '%s 的解释器不存在：%s' % (tag, args[0])
        # 脚本参数必须是真实存在的 .py（正本），不是 .sh 转发
        py = [a for a in args[1:] if a.endswith(('.py', '.sh'))]
        assert len(py) == 1, '%s 的命令里不是恰好一个脚本：%r' % (tag, args)
        assert py[0].endswith('.py'), (
            '%s 还指着 shell 脚本 %s —— 正本已经是 .py 了' % (tag, py[0]))
        assert os.path.isfile(py[0]), '%s 指向不存在的脚本：%s' % (tag, py[0])
        assert times, '%s 一个时间点都没有 —— 空的调度不报错，只是永不触发' % tag
    # 反向自证：两条确实是不同的脚本（否则上面那圈可能在同一条上空转）
    _scripts = {j[3]: [a for a in j[1] if a.endswith('.py')][0] for j in jobs}
    assert len(set(_scripts.values())) == 2, '两条定时指向同一个脚本：%r' % _scripts

    # ── Windows：装用的名字与查/删用的必须是同一套 ──────────────
    # 🔴 装的是 `finacial-sync-1600`（每点位一个），而查与删原来写的是
    #   不带后缀的 `finacial-sync` —— 那个名字**根本不存在**，于是
    #   「装完状态永远显示未安装」「卸载一个都删不掉却说已卸载」。
    #   判据**构造**一次往返：把 schtasks 换成桩，装一遍、再查一遍。
    _tasks = []

    def _fake_run(argv, *a, **k):
        class R:
            returncode = 0
            stdout = ''
            stderr = ''
        r = R()
        if argv[:2] == ['schtasks', '/create']:
            _tasks.append(argv[argv.index('/tn') + 1])
        elif argv[:2] == ['schtasks', '/query'] and '/fo' in argv:
            r.stdout = '\n'.join('"\\%s","N/A","Ready"' % t for t in _tasks)
        return r

    _real_run, _real_sys = m.subprocess.run, m.platform.system
    try:
        m.subprocess.run = _fake_run
        m.platform.system = lambda: 'Windows'
        for label, args, times, tag, what in jobs:
            for hh, mm in times:
                _fake_run(['schtasks', '/create', '/tn',
                           'finacial-%s-%02d%02d' % (tag, hh, mm)])
        assert _tasks, '构造不对：一个任务都没"装"上'
        got = m._win_tasks()
        assert sorted(got) == sorted(_tasks), (
            '装了 %d 个任务，查出来 %d 个 —— 装用的名字与查用的对不上，'
            '而那不报错：状态永远显示"未安装"' % (len(_tasks), len(got)))
        assert len(m._win_tasks('sync')) == len(jobs[0][2]), \
            '按 tag 筛不出 sync 的那批'
    finally:
        m.subprocess.run, m.platform.system = _real_run, _real_sys

    # ---- 「下一次什么时候跑」：跨午夜那档【必须先排序】----------------
    # 🔴 `_range_times('16:00','09:20',60)` 生成的顺序是
    #   16:00…23:00, 00:00…09:00, 09:20 —— **不是时钟升序**。
    #   直接取"第一个 >= now"的话，凌晨 02:05 会答 16:00，
    #   而真正的下一次是 03:00。**它不报错**，只是页面上那个
    #   "下次扫描"一直在说谎（同「判据永远是现在的状态」那条）。
    import datetime as _dt
    _wrap = m._range_times('16:00', '09:20', 60)
    assert _wrap[0] == (16, 0) and _wrap.index((0, 0)) > _wrap.index((23, 0)), \
        '构造不成立：这一档本来就该是乱序的，%s' % _wrap[:3]
    _cases = [('02:05', (3, 0)), ('15:00', (16, 0)), ('23:59', (0, 0))]
    for _now, _want in _cases:
        _h, _mi = (int(x) for x in _now.split(':'))
        _at, _left = m.next_slot(
            _wrap, _dt.datetime(2026, 9, 26, _h, _mi))
        assert (_at.hour, _at.minute) == _want, \
            '跨午夜下一次算错：now=%s -> %s，应是 %s' % (_now, _at, _want)
        assert _left > 0, '下一次不许落在过去：%s' % _left
    # 空点位不许猜一个出来
    assert m.next_slot([]) == (None, None), '没有点位时该给 None，不是猜一个'

    # ---- show_schedule 要答得出「下次」与「开着没有」--------------------
    _sh = m.show_schedule()
    assert set(_sh['installed']) == {'sync', 'tick'}, \
        '两条定时都要报状态 —— 只管 sync 的话，tick 掉了没人发现'
    for _k, _v in _sh['installed'].items():
        for _f in ('next_at', 'next_in', 'next_from', 'on'):
            assert _f in _v, '%s 缺字段 %s' % (_k, _f)
        if _v.get('got_slots'):
            assert _v['next_from'] == 'installed', \
                '🔴 下次时间要按【实际装上的】点位算，不是按配置 —— ' \
                '配置改了没重装时，照配置报的那个时刻根本不会发生'

    # 🔴 上面那条在"装的与配置一致"时是**空转**的（本机正是如此，
    #   两种实现给出同样的 next_at，变异测试当场漏过）。
    #   所以**构造**出「配置改了但没重装」：把 load_schedule 换成一个
    #   窗口完全不同的，此时按配置算与按已装点位算必然不同。
    #   ★ 只在进程内换，不碰 schedule.json（selftest 不许写生产数据）。
    _real_load = m.load_schedule
    try:
        _sc0, _w0 = _real_load()
        _drift = {k: dict(v) for k, v in _sc0.items()}
        _drift['sync'] = {'from': '03:00', 'to': '04:00', 'every': 30}
        m.load_schedule = lambda: (_drift, _w0)
        _d2 = m.show_schedule()['installed']['sync']
        if _d2.get('got_slots'):
            assert _d2['next_from'] == 'installed', \
                '🔴 配置改了没重装时，"下次"照的还是配置 —— 那个时刻不会发生'
            assert _d2['match'] is False, \
                '构造不成立：改了配置之后 match 该是 False'
            _h = int(_d2['next_at'][11:13])
            assert _h not in (3, 4), \
                '🔴 "下次 %s" 是照【配置】算的（03:00~04:00），' \
                '而实际装的点位根本不在那个窗口' % _d2['next_at']
    finally:
        m.load_schedule = _real_load

    # ---- 自动同步：**两条都要管**（srv 层那个开关的唯一判据）----------
    # 🔴 原来 `srv/sync.py` 自己查 launchd、只管 sync 一条 ——
    #   关掉"自动同步"之后 tick 每小时照跑（日志里一串"A 腿落后"），
    #   **而页面上写着"已关闭"**。判据落在 tasks 的集合上。
    import importlib as _il
    _sv = _il.import_module('assay.srv.sync')
    _au = _sv.autosync_status()
    if not _au.get('error'):
        assert {t['key'] for t in _au.get('tasks') or []} == {'sync', 'tick'}, \
            '🔴 自动同步只报了 %s —— 另一条掉了没人会发现' \
            % [t.get('key') for t in _au.get('tasks') or []]
        # 那个 schedule 字符串不许退回"只抠第一个点位"的写法
        _txt = _au.get('schedule') or ''
        assert '每' in _txt and '~' in _txt, \
            '🔴 schedule 只报了 %r —— 25 个点位只说一个是在说谎' % _txt

    # 本地这个库的 schema 版本读得出来（顺带证明 _meta 判据有效）
    v = m._db_schema_version()
    if v:
        assert m._major(v) is not None, 'schema 版本解析不出主版本号：%s' % v
    return ('资产表 4 个平台且无 Darwin_x86_64；aarch64/amd64 归一；'
            'CLI 用实测的 --dburi/--min（不是文档的 --dbpath/--minline）；'
            '反斜杠路径归一成目录树且拒路径穿越；bootstrap 里 init 后紧跟 '
            'cron 且断言复权因子表非空；口径护栏在（turnover 量级，'
            '缩表护栏拦不住"值变了"）；'
            '不用 pip 装（PyPI 同名项目无 DuckDB）；'
            '定时 PATH 首段是当前解释器%s；plist 带 EnvironmentVariables 且 '
            'plutil 合法；无硬编码 /Users/guhao；缩表护栏 + schema 探针在；'
            '两条定时都走 sys.executable + .py 正本（不是 /bin/bash）；'
            '跨午夜的"下次"先排序（02:05 -> 03:00 不是 16:00）；'
            'show_schedule 两条都给 next_at/on，且【构造】配置漂移后'
            '仍按已装点位算；自动同步两条都管、窗口口径说全；'
            'Windows 任务名装/查往返 %d 个'
            '%s' % (mine, len(_tasks), ('；本地库 schema %s' % v) if v else ''))


@case('外部行情接口文档：结构完整 / 示例代码能跑（离线）', tag='fast')
def t_extapi_doc():
    """索引 5 是【调研结果的定案】—— 它存在的意义就是"下次不要重新试一遍"。

    ★ 所以这条用例守两件事：
      1. **文档在字典页里挂着**（写了没挂 = 只有翻仓库才看得到）
      2. **示例代码是真代码** —— 能 import、能定义出那三个函数、
         并且**超限时会抛错而不是返回空**。

    ★ 刻意**不打真接口**：selftest 要能离线跑，而且拿外部接口当断言
      等于把别人的限流变成自己的红灯。真实连通性是调研时逐个实测过的
      （耗时、条数、字段位都写在文档里），这里只守"文档没烂"。
    """
    import re

    from assay import server as sv
    root = sv._repo_root()
    keys = {d[0] for d in sv._DOCS}
    assert 'extapi' in keys, '索引 5 没挂进数据字典页 —— 写了没挂等于没写'
    rel = [d[3] for d in sv._DOCS if d[0] == 'extapi'][0]
    path = os.path.join(root, rel)
    assert os.path.isfile(path), '找不到 %s' % path
    md = open(path, encoding='utf-8').read()

    # ---- 1) 两个索引都要在（用户明确要的：分渠道 + 分功能）----
    assert '## 1. 分渠道索引' in md, '缺【分渠道】索引'
    assert '## 2. 分功能索引' in md, '缺【分功能】索引'
    assert '## 3. 🔴 陷阱' in md, '缺陷阱段'

    # ---- 2) 四个主力接口的域名都要写明 ----
    for host, why in (('quotes.sina.cn', '唯一有历史深度的分钟源'),
                      ('push2his.eastmoney.com', '当日分时'),
                      ('push2delay.eastmoney.com', '东财被限流时的唯一活口'),
                      ('qt.gtimg.cn', '批量实时快照'),
                      ('ifzq.gtimg.cn', '腾讯分时')):
        assert host in md, '文档里没有 %s（%s）' % (host, why)
    # 已确认不可用的要标出来，否则下次还会去试
    assert 'hq.sinajs.cn' in md and '403' in md, \
        '没写明 hq.sinajs.cn 已经 403 —— 下次会重新踩'
    assert 'web.ifzq.gtimg.cn' in md, \
        '没写明 web. 那个子域 DNS 解析不了（stock-sdk 源码里写的正是它）'

    # ---- 3) 历史深度是这次调研最值钱的结论，必须逐档写清 ----
    for scale, days in (('1 分', '5 日'), ('5 分', '22 日'),
                        ('15 分', '65 日'), ('30 分', '129 日'),
                        ('60 分', '257 日')):
        assert scale in md and days in md, \
            '新浪 %s 的历史深度（%s）没写' % (scale, days)

    # ---- 4) 三个"静默失败"必须写明（这类最费时间）----
    for k in ('静默', 'RemoteDisconnected', 'data: null', '1023'):
        assert k in md, '陷阱段缺「%s」' % k

    # ---- 5) 示例代码是真代码：能 exec，且定义出三个函数 ----
    m = re.search(r'```python\n(.*?)```', md, re.S)
    assert m, '文档里没有可抄的示例代码'
    ns = {}
    exec(compile(m.group(1), '<doc>', 'exec'), ns)      # noqa: S102
    for fn in ('sina_minute', 'em_trends', 'tx_quote'):
        assert callable(ns.get(fn)), '示例代码里没有 %s()' % fn
    # ★ 超限要抛错而不是返回空 —— 这是文档反复强调的那条，
    #   示例代码自己得做到。用假的取数函数验，不打真接口。
    ns['_get'] = lambda *a, **k: 'null'
    try:
        ns['sina_minute'](n=4000)
        raise AssertionError('示例代码在拿到空结果时没抛错 —— '
                             '而文档说"空结果一律当失败处理"')
    except RuntimeError:
        pass

    # ---- 6) 交叉引用：按需求 / 按陷阱两个索引都要指过来 ----
    for idx in ('1-按需求索引.md', '4-按陷阱索引.md', 'README.md'):
        p2 = os.path.join(root, 'datalake', 'docs', '数据字典', idx)
        t2 = open(p2, encoding='utf-8').read()
        assert '索引 5' in t2 or '5-外部行情接口' in t2, \
            '%s 没指向索引 5 —— 从别的索引查过来时会以为本地没有这块' % idx
    return ('索引 5 已挂进字典页（%.1fK）；分渠道 + 分功能两个索引齐全；'
            '5 个域名与 2 个已失效的都写明；新浪 5 档历史深度逐档记录；'
            '3 类静默失败写明；示例代码可 exec 且超限会抛错；'
            '按需求/按陷阱/README 三处都有交叉引用'
            % (len(md.encode()) / 1024))


@case('定时窗口配置：判据是【实际装上的点位】而不是回显配置', tag='fast')
def t_schedule():
    """轮询窗口可在看板改（时间范围 + 间隔），存完立即重装 launchd。

    🔴 **只回显配置是不够的**：配置改了而 timer 没重装时，
      "页面写着每小时一次、实际还是旧的"**不报错**。
      所以判据必须是**已装 plist 里到底有几个点位**
      （同 CLAUDE.md「已安装的 plist 与仓库正本不一致要报出来」那条）。
    """
    import importlib.util
    here = REPO
    dlp = os.path.join(os.path.dirname(here), 'datalake', 'setup_tdx.py')
    spec = importlib.util.spec_from_file_location('_st_probe', dlp)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)

    # ---- ① 点位生成：间隔除不尽时要补上终点 ----
    assert len(m._range_times('16:00', '20:00', 10)) == 25
    t = m._range_times('07:00', '09:20', 60)
    assert t == [(7, 0), (8, 0), (9, 0), (9, 20)], \
        ('间隔除不尽时要补上终点 —— 否则窗口末尾那段（09:00~09:20）'
         '等于没覆盖，而配置上写着管到 09:20。实得 %s' % t)
    assert m._range_times('18:10', '18:10', 5) == [(18, 10)], \
        '单点位（老命令 --at 18:10）要仍然可用'
    # ---- ①b 跨午夜：16:00 ~ 次日 09:20 ----
    #   🔴 launchd 的 StartCalendarInterval 只是一组 (Hour, Minute)、
    #     每天都触发，所以跨天对它不是特例 —— 点位要**回绕**过 00:00。
    assert m._span('16:00', '09:20') == 1040, \
        '跨午夜的跨度算错了（16:00 到次日 09:20 = 17 小时 20 分）'
    assert m._span('16:00', '20:00') == 240, '不跨午夜的跨度算错了'
    wrap = m._range_times('16:00', '09:20', 60)
    assert len(wrap) == 19, '16:00~次日09:20 每 60 分该是 19 个点位，实得 %d' \
        % len(wrap)
    assert wrap[0] == (16, 0) and wrap[-1] == (9, 20), \
        '跨午夜的首尾点位不对：%s -> %s' % (wrap[0], wrap[-1])
    assert (0, 0) in wrap and (23, 0) in wrap and (9, 0) in wrap, \
        '跨午夜的点位没有回绕过 00:00：%s' % (wrap,)
    assert len(set(wrap)) == len(wrap), '跨午夜的点位有重复：%s' % (wrap,)
    assert all(0 <= h <= 23 and 0 <= mm <= 59 for h, mm in wrap), \
        '跨午夜算出了非法的时刻（小时越界）：%s' % (wrap,)

    # ---- ② 🔴 非法配置必须被拒，而不是装出一个不跑的 timer ----
    #   launchd 对**空的** StartCalendarInterval 不报错，只是永远不触发 ——
    #   表现是"配好了但数据再也不同步了"，几天后才发现。
    good = {'sync': {'from': '16:00', 'to': '20:00', 'every': 10},
            'tick': {'from': '07:00', 'to': '09:20', 'every': 60}}
    ok, why = m.check_schedule(good)
    assert ok, '正常配置被拒了：%s' % why
    #   ★ 密集但合理的要放行：16:00~20:00 每 5 分钟 = 49 个点位，
    #     低于上限。上限是防"配出几百个点位把 launchd 塞满"，不是限制频率。
    ok, why = m.check_schedule(
        dict(good, sync={'from': '16:00', 'to': '20:00', 'every': 5}))
    assert ok, '每 5 分钟（49 个点位）被误拒了：%s' % why
    #   ★ 跨午夜要放行（信号重算的默认窗口就是 16:00 ~ 次日 09:20）
    for WRAP_OK in ({'from': '16:00', 'to': '09:20', 'every': 60},
                    {'from': '20:00', 'to': '16:00', 'every': 60}):
        ok, why = m.check_schedule(dict(good, tick=WRAP_OK))
        assert ok, '跨午夜窗口 %s 被拒了：%s' % (WRAP_OK, why)
    BAD = [
        #   ★ 原来这里有一条 `20:00~16:00 -> 起点晚于终点` ——
        #     它现在是**合法的跨午夜窗口**，断言跟着改了（见下面的 WRAP_OK）。
        ({'from': '16:00', 'to': '09:20', 'every': 5}, '跨午夜时点位数超上限'),
        ({'from': '16:00', 'to': '20:00', 'every': 0}, 'every=0'),
        ({'from': '16:00', 'to': '20:00', 'every': 1}, 'every 低于下限'),
        ({'from': '16:00', 'to': '20:00', 'every': 999}, 'every 超上限'),
        ({'from': '00:00', 'to': '23:59', 'every': 5}, '点位数超上限'),
        ({'from': '25:00', 'to': '20:00', 'every': 10}, '小时越界'),
        ({'from': '1600', 'to': '20:00', 'every': 10}, '格式不对'),
        ({'from': '16:00', 'to': '20:00'}, '缺 every'),
        ({'from': '16:00', 'to': '20:00', 'every': '10'}, 'every 是字符串'),
    ]
    for bad, what in BAD:
        ok, why = m.check_schedule(dict(good, sync=bad))
        assert not ok, '「%s」这种配置被放过了：%r' % (what, bad)
    #   ★ 空/缺段也要拒
    for bad in ({}, {'sync': good['sync']}, None):
        ok, _w = m.check_schedule(bad)
        assert not ok, '缺段的配置被放过了：%r' % (bad,)

    # ---- ③ show_schedule 必须给出「配置 vs 实际」的比对 ----
    sc = m.show_schedule()
    for k in ('schedule', 'installed', 'limits', 'exists'):
        assert k in sc, 'show_schedule 少了 %s' % k
    for key in ('sync', 'tick'):
        d = sc['installed'][key]
        for f in ('want_slots', 'got_slots', 'match', 'loaded', 'label',
                  'wrap'):
            #   🔴 `wrap`：**分不出"跨午夜"和"填反了"**，所以判据不在
            #     校验里而在显示上 —— 页面写成「16:00 ~ 次日 09:20」，
            #     填反了那个"次日"和点位数会当场看出来。
            assert f in d, 'installed.%s 少了 %s' % (key, f)
        assert d['match'] is not False, \
            ('%s 的配置与实际装上的 timer 不一致（want %s / got %s）—— '
             '跑一次 `python3 datalake/setup_tdx.py --install-timer`'
             % (key, d['want_slots'], d['got_slots']))
    assert sc['installed']['tick']['wrap'] is True, \
        '信号重算的默认窗口是跨午夜的（16:00 ~ 次日 09:20），wrap 该为 True'
    assert sc['installed']['sync']['wrap'] is False, \
        '数据同步的窗口不跨午夜，wrap 该为 False'
    #   🔴 上面那条只能抓"当前恰好不一致"，**抓不到判据本身坏了**
    #     （把 match 写死成 True 时它照样绿 —— 变异测试抓到过）。
    #     所以再注入一份**故意与已装 plist 不同**的配置：match 必须翻成 False。
    if sc['installed']['sync']['got_slots'] is not None:
        real_load = m.load_schedule
        try:
            m.load_schedule = lambda: (
                {'sync': {'from': '01:00', 'to': '01:30', 'every': 30},
                 'tick': sc['schedule']['tick']}, None)
            d = m.show_schedule()['installed']['sync']
            assert d['want_slots'] == 2 and d['match'] is False, \
                ('注入一份与已装 timer 不同的窗口（01:00~01:30/30，2 个点位）后，'
                 'match 仍然不是 False（want %s / got %s / match %r）—— '
                 '这个判据是死的，"改了配置但没重装"就永远看不出来'
                 % (d['want_slots'], d['got_slots'], d['match']))
        finally:
            m.load_schedule = real_load

    # ---- ④ 接口两端都在 ----
    from assay import server as sv
    assert '/api/sync/schedule' in sv.ROUTES, 'GET /api/sync/schedule 没挂'
    src = open(os.path.join(here, 'assay', 'server.py'),
               encoding='utf-8').read()
    assert 'api_sync_schedule_set' in src, 'POST 没挂'
    syp = open(os.path.join(here, 'assay', 'srv', 'sync.py'),
               encoding='utf-8').read()
    #   🔴 不能用 `'--install-timer' in syp` —— 这个字面量在同一个文件的
    #     **注释里也出现**（第 4 次踩这个坑），把真实调用改坏了它照样绿。
    #     所以走 ast：注释不是 AST 节点，docstring 单独剔掉。
    import ast as _ast
    _fn = next((n for n in _ast.walk(_ast.parse(syp))
                if isinstance(n, _ast.FunctionDef)
                and n.name == 'api_sync_schedule_set'), None)
    assert _fn is not None, 'POST 处理函数 api_sync_schedule_set 不见了'
    _body = _fn.body
    if (_body and isinstance(_body[0], _ast.Expr)
            and isinstance(_body[0].value, _ast.Constant)):
        _body = _body[1:]                      # 剔掉 docstring
    _lit = {n.value for b in _body for n in _ast.walk(b)
            if isinstance(n, _ast.Constant) and isinstance(n.value, str)}
    _call = {n.func.attr if isinstance(n.func, _ast.Attribute) else
             getattr(n.func, 'id', '') for b in _body for n in _ast.walk(b)
             if isinstance(n, _ast.Call)}
    assert 'check_schedule' in _call, \
        ('POST 要先校验再存 —— 不校验就能从页面存进 from>to 这种配置，'
         '而它会装出一个**永不触发**的 timer 且不报错')
    assert 'save_schedule' in _call, 'POST 没存配置'
    assert '--install-timer' in _lit, \
        ('POST 要「存配置 + 立即重装」，不给"存了但没生效"留窗口 —— '
         '实际传给 setup_tdx.py 的参数里没有 --install-timer')
    assert "out['sched']" in syp, \
        'api_sync 要把窗口与实际点位带给页面'

    # ---- ⑤ 页面必须显示【实际装上的】，不能只回显配置 ----
    js = open(os.path.join(here, 'web', 'views', 'sync.js'),
              encoding='utf-8').read()
    flat = js.replace(' ', '').replace('\n', '')
    assert 'got_slots' in js and 'i.match===false' in flat, \
        ('页面没显示"实际装上的点位/是否一致" —— 只回显配置的话，'
         '"改了但没重装"就看不出来')
    assert '配置与实际装上的 timer 不一致' in js, '不一致要显红说清'
    assert 'i.wrap' in js and '次日' in js, \
        ('页面要把跨午夜写成「16:00 ~ **次日** 09:20」—— '
         '光写 16:00~09:20 看着像填反了')
    #   🔴 又不能用"函数体里出现过「次日」" —— 我自己写的注释里就有它
    #     （第 5 次踩这个坑）。走 ast 只看**字符串常量**。
    _dl = open(os.path.join(os.path.dirname(here), 'datalake',
                            'setup_tdx.py'), encoding='utf-8').read()
    _it = next((n for n in _ast.walk(_ast.parse(_dl))
                if isinstance(n, _ast.FunctionDef)
                and n.name == 'install_timer'), None)
    assert _it is not None, 'install_timer 不见了'
    _its = {n.value for n in _ast.walk(_it)
            if isinstance(n, _ast.Constant) and isinstance(n.value, str)}
    assert any('次日' in x for x in _its), \
        '命令行装 timer 的回显也要说「次日」（跨午夜时）'
    #   ★ 默认窗口的定案值也在这条用例里钉住（它是"窗口"的一部分）
    assert m.DEFAULT_SCHED['tick']['from'] == '16:00', \
        ('信号重算的窗口要从 16:00 开始 —— 公告集中在 16:00~22:00，'
         '而人常在晚上手动导聚宽增量，只开早上的话要等到 07:00 才算得进去')
    assert 'setTimeout(showSync' in flat.replace(' ', ''), \
        ('保存后要**延迟**刷新 —— 立刻 showSync() 会把反馈连同 #syschedmsg '
         '一起重渲染掉，点了按钮什么都看不到（实测踩过）')
    return ('点位生成含补终点（07:00~09:20/60 -> 4 个）与单点位兼容；'
            '跨午夜回绕正确（16:00~次日09:20/60 -> 19 个点位，含 00:00~09:00 '
            '且小时不越界）且校验放行、页面与命令行都标「次日」'
            '（分不出"跨午夜"与"填反了"，所以判据在显示上不在校验里）；'
            '%d 种非法配置全被拒（空的 StartCalendarInterval 会让 launchd '
            '永不触发且不报错）；show_schedule 给出 want/got/match/wrap '
            '且当前一致；GET+POST 都挂且 POST 存完立即重装；'
            '页面显示实际点位并延迟刷新'
            % (len(BAD) + 3))


@case('信号重算：不一致必须留痕再覆盖', tag='fast')
def t_signal_revision():
    """早上重算出来的清单可能与昨晚不一样（当晚公告里 ST/停牌是次日生效的）。

    🔴 **人可能已经按昨晚那份准备好委托了** —— 静默覆盖等于让他拿着一份
      已经作废的清单去下单。所以：旧版归档成 rev、主文件记 revisions。
    ★ 但数据变了而**决策没变**是常态，那种情况只记一次 recomputed_at ——
      否则每天几条"重算过"的噪声，人就不看这个提示了
      （同"假告警看多了就不看告警"）。
    """
    import json as _js
    import tempfile
    from assay import live as lv
    from assay.lv import base as _b, sig as _sig

    old_live = lv.LIVE
    tmp = tempfile.mkdtemp(prefix='sigrev_')
    try:
        lv.LIVE = tmp                      # 🔴 不许写真实账本
        assert _b.LIVE == tmp, 'LIVE 重定向没生效'
        aid = 'probe'
        os.makedirs(os.path.join(tmp, aid, 'signals'))
        FD = '2026-09-07'

        def mk(buy, fp):
            return {'account': aid, 'for_date': FD, 'built_at': '2026-09-06T18:12:00',
                    'buy': [{'code': c, 'shares': 100} for c in buy],
                    'sell': [], 'hold': [],
                    'data_fingerprint': {'overall': fp}}

        # 昨晚那份
        _b._atomic_write(_sig.signal_path(aid, FD),
                         _js.dumps(mk(['A', 'B'], 'fp_old'), ensure_ascii=False))
        # 让 build_signal 返回"早上算出来的不同结果"
        orig = _sig.build_signal
        _sig.build_signal = lambda a, datalake=None: mk(['A', 'C'], 'fp_new')
        try:
            r = _sig.make_signal(aid, force=True)
        finally:
            _sig.build_signal = orig

        # ---- ① 旧版必须被归档 ----
        rp = _sig.rev_path(aid, FD, 1)
        assert os.path.isfile(rp), '旧版没归档成 rev1 —— 覆盖掉就找不回来了'
        arch = _js.load(open(rp, encoding='utf-8'))
        assert [x['code'] for x in arch['buy']] == ['A', 'B'], \
            'rev1 里不是旧版内容：%s' % arch.get('buy')

        # ---- ② 主文件必须记 revisions，且 diff 说清差异 ----
        revs = r.get('revisions') or []
        assert len(revs) == 1, 'revisions 应有 1 条，实际 %d' % len(revs)
        d = revs[0]['diff']
        assert d['buy_added'] == ['C'] and d['buy_removed'] == ['B'], \
            'diff 没说清差异：%s' % d
        assert d['changed'] is True and d['data_fp_changed'] is True

        # ---- ③ 🔴 append-only：再算一次，已有的 rev1 不许被重写 ----
        st1 = os.stat(rp).st_mtime_ns
        _sig.build_signal = lambda a, datalake=None: mk(['A', 'D'], 'fp_new2')
        try:
            r2 = _sig.make_signal(aid, force=True)
        finally:
            _sig.build_signal = orig
        assert os.stat(rp).st_mtime_ns == st1, \
            'rev1 被重写了 —— 归档必须 append-only（那是唯一的回滚凭据）'
        assert os.path.isfile(_sig.rev_path(aid, FD, 2)), '第二次没归档成 rev2'
        assert len(r2.get('revisions') or []) == 2, \
            '两次不一致应有 2 条 revision'

        # ---- ④ 结果【相同】时不许产生 revision（否则全是噪声）----
        cur = _js.load(open(_sig.signal_path(aid, FD), encoding='utf-8'))
        same = dict(cur)
        same['data_fingerprint'] = {'overall': 'fp_same_data_changed'}
        _sig.build_signal = lambda a, datalake=None: same
        try:
            r3 = _sig.make_signal(aid, force=True)
        finally:
            _sig.build_signal = orig
        assert len(r3.get('revisions') or []) == 2, \
            '决策没变却又产生了 revision —— 那会变成天天有的噪声'
        assert r3.get('recomputed_at'), \
            '决策没变时应当记一次 recomputed_at（数据确实动过）'

        # ---- ⑤ 不加 force 时不许动已有信号 ----
        _sig.build_signal = lambda a, datalake=None: mk(['Z'], 'fp_x')
        try:
            r4 = _sig.make_signal(aid, force=False)
        finally:
            _sig.build_signal = orig
        assert [x['code'] for x in r4['buy']] != ['Z'], \
            '没加 force 却重算并覆盖了'
        #   🔴 归档在 `signals/_rev/` **子目录**里，不是同层 ——
        #     同层的话 `latest_signal` 会 sorted 到它、读出被归档的旧版
        #     （见「信号归档必须放【子目录】」那条用例）。
        #     判据要跟着代码一起搬，不然它只是看着还在。
        _rd = os.path.join(tmp, aid, 'signals', '_rev')
        n_rev = len([f for f in os.listdir(_rd)
                     if '.rev' in f]) if os.path.isdir(_rd) else 0
        assert n_rev == 2, 'rev 文件数应为 2，实际 %d（%s）' % (n_rev, _rd)
        assert not [f for f in os.listdir(os.path.join(tmp, aid, 'signals'))
                    if '.rev' in f], \
            '归档不许出现在 signals/ 同层 —— latest_signal 会读到它'
        #   🔴 **页面提示里给的那个路径必须真的能找到文件。**
        #     `archived` 原来只存 basename、页面自己拼 'signals/' ——
        #     归档搬进 `_rev/` 子目录之后那个拼法就错了，提示里写着
        #     「旧版留在 signals/2026-09-08.rev1.json」而文件在
        #     `signals/_rev/` 下。**给一个找不到的路径比不给更糟。**
        import json as _json
        with open(os.path.join(tmp, aid, 'signals',
                               '%s.json' % r3['for_date']),
                  encoding='utf-8') as _fh:
            cur = _json.load(_fh)
        for _r in (cur.get('revisions') or []):
            _ap = _r.get('archived') or ''
            assert _ap, 'revision 里没有 archived 路径'
            assert os.path.isfile(os.path.join(tmp, aid, _ap)), \
                ('revision 的 archived 指向一个不存在的文件：%s —— '
                 '页面照它显示"旧版留在 …"，人照着去找会找不到' % _ap)
    finally:
        lv.LIVE = old_live
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    # ---- ⑥ tick_daily.py 的三道判据都在（它是 launchd 的入口）----
    here = REPO
    src = open(os.path.join(here, 'tick_daily.py'), encoding='utf-8').read()
    assert '_is_trading_day' in src, 'tick_daily 少了「今天是交易日吗」'
    assert 'leg_a_lag' in src, \
        ('tick_daily 少了「A 腿数据到最新交易日了吗」—— 拆成两个时间点之后，'
         '同步一慢就会拿旧数据算出一份看着正常的信号')
    assert 'fingerprint' in src, 'tick_daily 少了「指纹变了吗」'
    body = src[src.index('def _leg_a_ok'):src.index('def _now_fp')]
    assert 'return None' in body and 'ss.collect()' in body, \
        '新鲜度判据应取 sync_status.collect() 那一处，且判不出时返回 None'
    tail = src[src.index('if not ok:'):]
    assert 'return 3' in tail.split('# ---- 判据 3')[0], \
        'A 腿落后时必须拒绝重算（非零退出码），而不是继续算'
    # ---- ⑦ 🔴 页面必须【显红说清差异】—— 留痕了但页面不说等于没留痕 ----
    #   ★ 匹配**结构**而不是"标识符出现过"：今天踩过两次这个坑
    #     （`assert 'h.pruned' in js` 时把 if(h.pruned) 改成 if(false) 照样全绿）。
    js = open(os.path.join(here, 'web', 'views', 'live.js'),
              encoding='utf-8').read()
    flat = js.replace(' ', '').replace('\n', '')
    assert 'constrevs=s.revisions||[]' in flat, \
        'live.js 没读 s.revisions —— 留痕了但页面不说，等于没留痕'
    assert 'revs.length?' in flat and '被重算过' in js, \
        'revision 提示要按 revs.length 分支并说清「被重算过」'
    # 🔴 2026-09-22 改成**两种样式**（用户："这里的提示感觉有问题"）：
    #   消失的那几只**当天都已成交**时走低调样式，真有决策变化才显红。
    #   一律显红的话，每次照着信号成交完都跳一条红字 —— 那就是假告警，
    #   而「假告警看多了就不看告警」。
    #   ★ 这条是**静态扫描**，只能证"两种样式都写了"；真正的行为判据在
    #     `t_signal_revision_notice`（构造两个方向 + 4 条变异全抓到）。
    _rv = js.split('const rv=')[1][:1200]
    assert 'lvwarn' in _rv and 'lvwhy' in _rv, \
        ('revision 提示要【两种样式都有】：真有决策变化 -> 警告；'
         '消失的那几只都是"你已成交" -> 低调')
    assert 'allDone' in flat, \
        '分支判据应是 allDone（服务端 `r.all_done` 给的"都是已成交"）'
    assert 'data_asof' in js, \
        ('提示里要写【数据日】—— 用户 2026-09-22 问的就是"这份基于哪天的'
         '数据"，只给两个时间戳（建于 -> 被替换于）答不了那个问题')
    assert '请照现在这份核对' in js, \
        '要明说「如果已按之前那份准备了委托，请照现在这份核对」'
    assert 'r.archived' in js, '要写清旧版存在哪个文件（唯一的回滚凭据）'
    assert 'returnw+rv+' in flat, \
        'rv 必须拼在正文之前 —— 藏在下面等于没提示'
    #   数据动过但清单没变：低调显示，**不许**用警告样式
    #   🔴 锚点要**唯一**：原来写的是 `split('const rc=')[1]`，而 `rc` 是个
    #     太常见的局部名 —— 2026-09-14 加模拟盘标记时我在前面也写了一个
    #     `const rc=`，锚点当场被抢走，这条断言去查的是**另一段代码**，
    #     于是报"低调显示没做"而产品根本没坏（同「判据比断言宽」那条）。
    assert js.count('const rc=(!revs.length') == 1, \
        '「重算过但清单未变」那段的锚点不唯一了'
    rcseg = js.split('const rc=(!revs.length')[1][:320]
    assert 'recomputed_at' in rcseg and 'lvwhy' in rcseg, \
        '「重算过但清单未变」应低调显示（lvwhy）'
    assert 'lvwarn' not in rcseg, \
        ('「清单未变」用了警告样式 —— 那会变成天天有的噪声，'
         '人就不看这个位置了（同"假告警看多了就不看告警"）')

    # ---- ⑧ 两个 timer 必须一起装 ----
    st = open(os.path.join(os.path.dirname(here), 'datalake', 'setup_tdx.py'),
              encoding='utf-8').read()
    _stx = os.path.join(os.path.dirname(REPO), 'datalake', 'setup_tdx.py')
    assert 'TICK_LABEL' in st and 'tick_daily.py' in st, \
        ('setup_tdx.py 没装「信号重算」的 timer —— 漏装的表现是'
         '**信号永远是昨晚 18:10 那份**，不报错')
    assert 'def _times(' in st, '多时间点要能解析（07:00,08:00,09:00）'
    # 🔴 这条原来是**扫源码**里 `JOBS = [` 那一段 —— 把它抽成
    #   `build_jobs()` 之后当场 `ValueError: substring not found`。
    #   **失败的是断言不是产品**；而这是「改一处之前先扫同类」漏掉的
    #   **第二处**（当场撞到的那条在 t_setup_tdx 里，已改；这条只在
    #   跑到本用例时才现形）。改成问**运行时到底装哪两个**。
    import importlib.util as _ilu
    _sp = _ilu.spec_from_file_location('_tdx_jobs_probe', _stx)
    _m = _ilu.module_from_spec(_sp)
    _sp.loader.exec_module(_m)
    _tags = {j[3] for j in _m.build_jobs(_m.load_schedule()[0])}
    assert _tags == {'sync', 'tick'}, \
        ('定时任务要同时有 sync 与 tick，实得 %s —— 清单只写一处，'
         '分开写会漏，而漏装的表现是**信号永远停在昨晚那份**' % sorted(_tags))
    _labels = {j[0] for j in _m.build_jobs(_m.load_schedule()[0])}
    assert _labels == {_m.LABEL, _m.TICK_LABEL}, \
        '两条任务的 label 对不上：%s' % sorted(_labels)
    #   ★ 判据是 launchctl 里到底有没有，不是命令返回码
    assert "launchctl', 'list'" in st, \
        '装完要用 launchctl list 复查（launchctl 对"已是这个状态"会报错退出）'
    return ('归档 rev1/rev2 且 append-only（旧的不被重写）；diff 说清 '
            'buy_added/buy_removed；决策没变只记 recomputed_at 不产生噪声；'
            '不加 force 不动已有信号；tick_daily 三道判据都在且 A 腿落后时拒算；'
            '页面按 revs.length 显红说清差异 + 清单未变时只低调显示；'
            'setup_tdx 的 JOBS 同时装 sync 与 tick 并用 launchctl list 复查')


@case('信号归档必须放【子目录】，否则"最新信号"读到旧版', tag='fast')
def t_rev_subdir():
    """🔴 revision 归档存成 `<date>.rev1.json`（与信号**同层**）时：

    有三处在扫 `signals/` 下的 `*.json` —— `sig.latest_signal` /
    `px.latest_signal` / `explain_history`，而前两者取 `sorted(...)[-1]`。
    `'2026-09-08.rev1.json'` 排在 `'2026-09-08.json'` **后面**（`'r' > '.'`），
    于是"最新信号"读到的是**被归档的旧版**。

    ★ 实测代价：改完规则重算，磁盘主文件已经是「卖出 0 只」，
      而页面/接口仍然给「卖出 2 只」—— **接口不报错，只是给了旧数据**，
      而这正是要照着下单的那份清单。
    ★ 与 CLAUDE.md 里「选股理由的旁挂必须放子目录、不能是
      `<date>.explain.json`」是**同一个坑**，加 revision 时又踩了一次。
    """
    import shutil
    import tempfile
    import json as _j
    from assay import live as lv
    from assay.lv import sig as _s, px as _p
    tmp = tempfile.mkdtemp(prefix='_st_rev_')
    old = lv.LIVE
    try:
        lv.LIVE = tmp                    # 🔴 绝不能写真账本
        d = os.path.join(tmp, 'a1', 'signals')
        os.makedirs(d)
        NEW = {'for_date': '2026-09-08', 'code_sha256': 'new' * 20,
               'sell': [], 'buy': [], 'hold': [1] * 10}
        OLD = {'for_date': '2026-09-08', 'code_sha256': 'old' * 20,
               'sell': [{'code': 'x'}, {'code': 'y'}], 'buy': [],
               'hold': [1] * 8}
        for name, obj in (('2026-09-08.json', NEW),):
            with open(os.path.join(d, name), 'w', encoding='utf-8') as fh:
                _j.dump(obj, fh)
        # ---- ① rev_path 必须落在子目录里 ----
        rp = _s.rev_path('a1', '2026-09-08', 1)
        assert os.path.basename(os.path.dirname(rp)) == '_rev', \
            ('归档要放 signals/_rev/ 子目录，实得 %s —— 同层的话 '
             '"最新信号"会 sorted 到它' % rp)
        os.makedirs(os.path.dirname(rp), exist_ok=True)
        with open(rp, 'w', encoding='utf-8') as fh:
            _j.dump(OLD, fh)
        # ---- ② 三处扫描都必须读到【主文件】 ----
        for nm, fn in (('sig.latest_signal', _s.latest_signal),
                       ('px.latest_signal', _p.latest_signal)):
            got = fn('a1') or {}
            assert got.get('code_sha256') == NEW['code_sha256'], \
                ('%s 读到了归档的旧版（sell %d 只）—— 页面会照着旧清单下单'
                 % (nm, len(got.get('sell') or [])))
            assert not got.get('sell'), \
                '%s 给出了旧版的卖出清单' % nm
        hist = _s.explain_history('a1')
        assert len(hist) == 1, \
            ('explain_history 把归档也当成一期了（%d 期）—— '
             '同一天会出现两段' % len(hist))
        # ---- ③ 反向自证：放同层就必须被抓到（这条断言不是空转）----
        same = os.path.join(d, '2026-09-08.rev1.json')
        shutil.copy(rp, same)
        bad = _s.latest_signal('a1') or {}
        assert bad.get('code_sha256') == OLD['code_sha256'], \
            ('前提变了：同层的 .rev1.json 本该被 sorted 到最后 —— '
             '如果这条不再成立，上面那两条断言就成了空转，要重新设计')
        os.remove(same)
    finally:
        lv.LIVE = old
        shutil.rmtree(tmp, ignore_errors=True)
    return ('rev 归档落在 signals/_rev/ 子目录；sig/px 的 latest_signal 与 '
            'explain_history 三处都只看主文件；反向自证：放回同层时'
            'latest_signal 确实会读到旧版（所以这条断言不是空转）')




@case('ETF 价格精度：正本是 .day，不许再塌成两位小数', tag='fast')
def t_etf_price_precision():
    """2026-09-17：用户「收益曲线里选红利低波 ETF，好几个昨天的值都没有任何变化？
    是数据没有自动同步吗？」—— 不是同步，是 ETF 收盘价被舍到了「分」。

    `tdx2db` 有两条取数路径，坏的是每天那条：

        init（引导历史）  vipdoc/*.day                    ÷1000  ✅ 2019~2025 精度完好
        cron（每日增量）  products/data/data/g4day/*.zip  ÷10000 再舍到 3 位 ❌

    于是 `1107`（真值 1.107×1000）进库成了 `0.111`：量级小 10 倍、**而且第 3 位
    小数被舍掉了**。老补丁 `fix_etf_price_scale.py` 的 ×10 只把它抬回 `1.11` ——
    **补在了错误的层上**，那一位永远回不来。

    后果是"数据看着正常、只是不动了"：红利低波 ETF 单价 1.1 元、一分钱 = 0.9%，
    而它日内只波动 0.2~0.5% —— 相邻交易日报同一个收盘价。实测持平率
    3~4% -> 30.9%（红利低波那几只 40%+），而指数 0.19% / 股票 2.44% 没受影响。

    🔴 判据**检测现象，不检测那一次事故的日期**（上一版守卫写死
      `2026-05-15 ~ 06-05`，于是 09-01 再次发生时报"异常 0"，看着一直是绿的）。
      阈值也不写死：拿**同一份数据里的历史基线**当尺子 —— 写死一个数的话，
      市场结构一变它要么天天假报、要么再也报不出来。
    """
    import duckdb
    import run as _run_mod
    root = _run_mod.default_lake()
    con = duckdb.connect(':memory:')
    K = "read_parquet('%s/raw/tdx/kline/etf_*.parquet')" % root

    def pct3(lo, hi):
        """ETF 收盘价里"带第 3 位小数"的占比 —— 精度还在不在的直接指纹。"""
        r = con.execute("""
            SELECT count(*), 100.0 * sum(CASE WHEN close <> round(close, 2) THEN 1 ELSE 0 END)
                             / nullif(count(*), 0)
            FROM %s WHERE date BETWEEN DATE '%s' AND DATE '%s'""" % (K, lo, hi)).fetchone()
        return r[0], (r[1] or 0.0)

    last = con.execute('SELECT max(date) FROM %s' % K).fetchone()[0]
    d20 = con.execute(
        'SELECT min(d) FROM (SELECT DISTINCT date d FROM %s ORDER BY d DESC LIMIT 20)' % K).fetchone()[0]

    n_base, p_base = pct3('2024-01-01', '2025-12-31')      # 基线：init 灌的那段
    n_new, p_new = pct3(d20, last)                          # 最近 20 个交易日
    assert n_base > 100000, '基线区间只有 %d 行 —— 判据在空转' % n_base
    assert n_new > 1000, '最近 20 日只有 %d 行 —— 判据在空转' % n_new
    # 基线实测 76~79%；塌陷时是 0%。取基线的一半当下界 —— 比写死数字耐用
    assert p_new >= p_base * 0.5, (
        'ETF 收盘价精度塌了：最近 20 日带第 3 位小数的只占 %.2f%%，'
        '而 2024~2025 基线是 %.2f%% —— 八成是 tdx2db 的每日路径又在舍位，'
        '跑 tdx2db/scripts/fix_etf_price_from_dayfile.py' % (p_new, p_base))

    # ② 用户真正看到的那个现象：相邻交易日收盘完全相同
    def flat(lo, hi):
        return con.execute("""
            WITH t AS (SELECT symbol, date, close,
                              lag(close) OVER (PARTITION BY symbol ORDER BY date) pc
                       FROM %s WHERE date BETWEEN DATE '%s' AND DATE '%s')
            SELECT 100.0 * sum(CASE WHEN close = pc THEN 1 ELSE 0 END) / nullif(count(*), 0)
            FROM t WHERE pc IS NOT NULL""" % (K, lo, hi)).fetchone()[0] or 0.0

    f_base, f_new = flat('2024-01-01', '2025-12-31'), flat(d20, last)
    assert f_new <= max(3.0, f_base * 3), (
        'ETF 相邻交易日收盘持平率 %.2f%%，而基线 %.2f%% —— 价格被量化了' % (f_new, f_base))

    # ③ 探测器自证：直接喂构造数据，不靠真实数据碰巧覆盖
    #    （真实数据修好之后，上面两条在"塌陷探测器坏了"时照样绿）
    sys.path.insert(0, os.path.join(os.path.dirname(REPO), 'datalake',
                                    'raw', 'tdx', '_ingest', 'scripts'))
    import importlib
    fx = importlib.import_module('fix_etf_price_from_dayfile')
    m = duckdb.connect(':memory:')
    m.execute('CREATE TABLE raw_symbol_class(symbol VARCHAR, class VARCHAR)')
    m.execute("INSERT INTO raw_symbol_class VALUES ('sz159525','etf'),('sz159001','etf')")
    m.execute('CREATE TABLE raw_kline_daily(symbol VARCHAR, date DATE, close DOUBLE)')
    rows = []
    for i in range(30):                       # 前 30 天：三位小数（正常）
        d = '2026-01-%02d' % (i + 1)
        rows += [('sz159525', d, 1.100 + i * 0.001), ('sz159001', d, 2.200 + i * 0.003)]
    for i in range(10):                       # 后 10 天：只到分（塌陷）
        d = '2026-02-%02d' % (i + 1)
        rows += [('sz159525', d, round(1.13 + i * 0.01, 2)), ('sz159001', d, round(2.50 + i * 0.01, 2))]
    m.executemany('INSERT INTO raw_kline_daily VALUES (?, ?::DATE, ?)', rows)
    since, last_ok = fx.collapse_start(m)
    assert str(since) == '2026-02-01', '塌陷起点算错: %s（应为 2026-02-01）' % since
    assert str(last_ok) == '2026-01-30', '最后一个正常日算错: %s' % last_ok
    m.execute("DELETE FROM raw_kline_daily WHERE date >= DATE '2026-02-01'")
    # 全好时塌陷探测必须给 None（它只负责"往回扩"，不负责日常）
    assert fx.collapse_start(m)[0] is None, '全是好数据时不该报塌陷'

    # ③b 🔴🔴 日常靠的是【滚动窗口】，不是塌陷探测 —— 这一条防的是
    #     "只认得那一次事故的指纹"。×10 补丁退役后，cron 新写进来的坏行是
    #     `0.111`（÷10、**仍带 3 位小数**）：塌陷探测看不见它，必须由
    #     "最近 N 个交易日无条件与正本比"兜住，否则价格小 10 倍留在库里。
    import datetime

    def _mk(days, bad_from=None, style=None):
        q = duckdb.connect(':memory:')
        q.execute('CREATE TABLE raw_symbol_class(symbol VARCHAR, class VARCHAR)')
        q.execute("INSERT INTO raw_symbol_class VALUES ('sz159525','etf')")
        q.execute('CREATE TABLE raw_kline_daily(symbol VARCHAR, date DATE, close DOUBLE)')
        d0, rs = datetime.date(2026, 1, 1), []
        for i in range(days):
            v = 1.100 + i * 0.001
            if bad_from is not None and i >= bad_from:
                v = round(v, 2) if style == 'round2' else round(v / 10, 3)
            rs.append(('sz159525', str(d0 + datetime.timedelta(days=i)), v))
        q.executemany('INSERT INTO raw_kline_daily VALUES (?, ?::DATE, ?)', rs)
        return q

    W = fx.WINDOW_DAYS
    # 🔴 窗口要留够"链条停摆"的余量：机器关着 / 网络失败 / launchd 没跑之后，
    #   cron 会一次补进好几天，窗口盖不住那几天就漏修。
    #   （这条下界不能省：上面那个 roll 的期望值是拿 WINDOW_DAYS 自己算的，
    #   把它改小时期望跟着一起走 —— 变异实测漏过。）
    assert W >= 20, '滚动窗口只有 %d 个交易日，盖不住链条停摆后的补数' % W
    roll = str(datetime.date(2026, 1, 1) + datetime.timedelta(days=60 - W))
    assert str(_mk(60) and fx.plan_since(_mk(60))[0]) == roll, (
        '全好时起点应落在滚动窗口起点 %s' % roll)
    # 明天那种坏法：最后一天 ÷10 —— 塌陷探测看不见，滚动窗口必须覆盖它
    bad = _mk(60, 59, 'div10')
    assert fx.collapse_start(bad)[0] is None, '构造不对：这种坏法本来就该躲过塌陷探测'
    since_d, _det, _lk = fx.plan_since(bad)
    assert since_d is not None and str(since_d) <= roll, (
        '÷10 的新行没被纳入核对区间（since=%s）—— 价格会小 10 倍留在库里' % since_d)
    # 历史塌陷比窗口更早时要【往回扩】
    assert str(fx.plan_since(_mk(60, 10, 'round2'))[0]) == '2026-01-11', (
        '塌陷在窗口之外时没有往回扩')
    assert str(fx.plan_since(_mk(60), '2020-01-01')[0]) == '2020-01-01', '命令行起点没生效'

    # ③c 正本比库里落后时（vipdoc 整包约 17:25~17:55 才带上当天，而轮询
    #     16:00 就开始）：今天那批行核对不了，必须**非零退出**让 5~8 跳过
    #     —— 返回 0 的话 1/10 的价格会一路进面板，而它不报错。
    assert fx._lag_stop('2026-09-17', 20260916) != 0, (
        '正本落后时返回了 0 —— 没核对过的 ETF 行会被放行进面板')

    # ③d HEAD 预检：Last-Modified 要折成【北京日期】才是"正本最多到哪天"。
    #     轮询 16:00 就开始而正本约 17:25~17:55 才更新，中间十来个点位每轮
    #     都会走到这里 —— 不预检就是每轮白下 61MB，而**限流是这条链上唯一
    #     的风险**。时区差 8 小时会在跨日那一刻判错，所以逐点钉。
    assert fx._zip_day('Wed, 16 Sep 2026 09:25:05 GMT') == datetime.date(2026, 9, 16), (
        'GMT 09:25 = 北京 17:25，应算 09-16')
    assert fx._zip_day('Wed, 16 Sep 2026 16:30:00 GMT') == datetime.date(2026, 9, 17), (
        'GMT 16:30 = 北京次日 00:30，应算 09-17（时区没加对）')
    assert fx._zip_day('garbage') is None, '解析不了必须给 None（不猜），退回下载后的权威判定'
    D = datetime.date
    assert fx.precheck_lag(D(2026, 9, 16), D(2026, 9, 17)) is True, '正本落后一天该拦下'
    # 🔴 等号必须放行：整包就是当天收盘后重打的，判成"落后"的话**每天都不往下走**，
    #   整条同步链永远跑不完，而它不报错（日志里只有一句"先不往下走"）。
    assert fx.precheck_lag(D(2026, 9, 17), D(2026, 9, 17)) is False, (
        '正本刚好到今天却被判成落后 —— 同步链会每天卡住')
    assert fx.precheck_lag(D(2026, 9, 18), D(2026, 9, 17)) is False, '正本更新反被拦'
    assert fx.precheck_lag(None, D(2026, 9, 17)) is False, '解析不出来时该放行（不猜）'

    # ④ 链条：必须调正本修复，且不许再调退役的 ×10 补丁
    #    （退役那个会把【已经正确】的价格再放大一次，而下游一路静默）
    body = _chain_dry()
    assert 'fix_etf_price_from_dayfile.py' in body, '同步链没接正本修复'
    assert 'fix_etf_price_scale.py' not in body, (
        '同步链还在调退役的 ×10 补丁 —— 它会把正确价格再 ×10')

    # ⑤ 退役守卫要真的拒，不是只在文档里写一句
    rc = subprocess.run([sys.executable,
                         os.path.join(os.path.dirname(REPO), 'datalake', 'raw',
                                      'tdx', '_ingest', 'scripts',
                                      'fix_etf_price_scale.py'),
                         '--db', '/nonexistent/x.db', '--dry-run'],
                        capture_output=True, text=True)
    assert rc.returncode == 2, '退役脚本没拒绝执行（退出码 %d）' % rc.returncode
    assert '退役' in rc.stdout, '退役脚本没说清为什么拒绝'

    return ('最近20日 %.1f%% 带第3位(基线 %.1f%%) · 持平 %.2f%%(基线 %.2f%%) · '
            '滚动窗口 %d 日覆盖÷10那种坏法 + 塌陷往回扩 + 链条 + 退役守卫'
            % (p_new, p_base, f_new, f_base, fx.WINDOW_DAYS))


@case('跨平台：tdx.db 只有一份解析，链上脚本不许写死本机路径', tag='fast')
def t_tdx_path_single_source():
    """2026-09-26 用户："这一轮修复完，是否可以直接在 window 上下载和自动
    补全数据？"

    查下来 **12 处各自解析 tdx.db**，其中 6 处换台机器必崩，而两种坏法
    **都不报错**（本机上照跑）：

        ① 绝对路径    `/Users/guhao/finacial/tdx2db/tdx.db`       3 处
        ② 走符号链接  `join(dirname(ROOT), 'tdx2db', 'tdx.db')`  3 处

    🔴🔴 `finacial/tdx2db` 是一个**符号链接**（-> `datalake/raw/tdx/_ingest`，
      实测同一个 inode），而它**不在任何仓库里**（同 `finacial/CLAUDE.md`
      那条）。新机器 clone 出来根本没有它 —— 而那 6 处里有 4 处是 13 步链的
      **2/13、5/13、6/13、10/13**。
    ★ 本机上两条路 `realpath` 相同，**所以一直看不出来**。

    判据四段，分工别记反：
        A 正本存在，且它自己不经过那个符号链接
        B 全仓不许再有 `os.path.join(..., 'tdx2db', ...)`（★ 扫目录，
          不照清单拼 —— 照清单的话新文件自动不在保护里）
        C **链上**脚本的字符串字面量里不许有本机绝对路径（清单从
          `--dry` + `setup_stages` 派生，加一步自动进保护范围）
        D 运行时：**最深**那个脚本上 bootstrap 与 cwd 无关
    """
    import ast as _ast
    import importlib.util
    dl = os.path.join(os.path.dirname(REPO), 'datalake')

    # ---- A 正本 ----
    pp = os.path.join(dl, 'paths.py')
    assert os.path.isfile(pp), '缺 datalake/paths.py —— tdx.db 的解析没有正本'
    _sp = importlib.util.spec_from_file_location('_dl_paths_probe', pp)
    _m = importlib.util.module_from_spec(_sp)
    _sp.loader.exec_module(_m)
    real = os.path.join(dl, 'raw', 'tdx', '_ingest', 'tdx.db')
    assert _m.TDX_DB == real, 'paths.TDX_DB 不是真实路径：%s' % _m.TDX_DB
    assert 'tdx2db' + os.sep + 'tdx.db' not in _m.TDX_DB, \
        '正本自己还在走 finacial/tdx2db 那个符号链接'
    assert _m.tdx2db_bin().endswith(('tdx2db', 'tdx2db.exe')), \
        'paths.tdx2db_bin() 给的不是可执行文件名'

    SKIP = ('archive', '__pycache__', '_recovery', 'venv', 'site-packages')

    def _pys():
        for r, ds, fs in os.walk(dl):
            ds[:] = [d for d in ds if d not in SKIP]
            for f in fs:
                if f.endswith('.py'):
                    yield os.path.join(r, f)

    # ---- B 不许再走符号链接（扫目录，不照清单拼）----
    bad, n_scan = [], 0
    for f in _pys():
        # ★ 跳过正本自己 —— 它的 docstring 里正在讲这个坏法
        #   （同「扫描要跳过守卫用例自己」那条）
        if os.path.basename(f) == 'paths.py':
            continue
        try:
            t = _ast.parse(io.open(f, encoding='utf-8').read())
        except (SyntaxError, UnicodeDecodeError):
            continue
        n_scan += 1
        for n in _ast.walk(t):
            if isinstance(n, _ast.Call) and isinstance(n.func, _ast.Attribute) \
                    and n.func.attr == 'join' and any(
                        isinstance(a, _ast.Constant) and a.value == 'tdx2db'
                        for a in n.args):
                bad.append('%s:%d' % (os.path.relpath(f, dl), n.lineno))
            if isinstance(n, _ast.Constant) and isinstance(n.value, str) \
                    and 'tdx2db/tdx.db' in n.value:
                bad.append('%s:%d(字面量)' % (os.path.relpath(f, dl), n.lineno))
    assert n_scan >= 40, '只扫到 %d 个 .py —— 判据在空转' % n_scan
    assert not bad, (
        '这些地方还在走 `finacial/tdx2db` 那个符号链接（它不在任何仓库里，'
        '新机器上根本没有）：%s。一律改成 `from paths import TDX_DB`' % bad[:6])

    # ---- C 链上脚本不许写死本机绝对路径 ----
    #   ★ 清单**派生**：`--dry` 那 13 步 + setup_stages 的七个阶段 ——
    #     加一步自动进保护范围（同「断言直接扫目录而不是照清单拼」）
    chain = set()
    for _cwd, _cmd in _chain_dry(with_cwd=True):
        for _a in re.findall(r'\S+\.py', _cmd):
            chain.add(os.path.normpath(os.path.join(_cwd, _a)))
    _ss = importlib.util.spec_from_file_location(
        '_ss_probe', os.path.join(dl, 'setup_stages.py'))
    _sm = importlib.util.module_from_spec(_ss)
    _ss.loader.exec_module(_sm)
    for st in _sm.stages():
        for a in (st.get('cmd') or []):
            if str(a).endswith('.py'):
                chain.add(str(a))
    files = sorted({os.path.abspath(c) for c in chain if os.path.isfile(c)})
    # 反向自证：链上那几步**逐个**都解析得到（少一个就是判据悄悄缩小）
    _miss = sorted(os.path.relpath(c, dl) for c in chain if not os.path.isfile(c))
    assert not _miss, '这几步的脚本解析不到，判据没覆盖它们：%s' % _miss
    assert len(files) >= 12, '只解析出 %d 个链上脚本 —— 判据在空转' % len(files)
    # ★ 原始那三处里最深的一个必须在保护范围内（它是 2/13 步）
    assert any(f.endswith('daily_snapshot.py') for f in files), \
        'daily_snapshot.py 不在扫描范围里 —— 而它正是当初写死路径的那三处之一'
    hard = []
    for f in files:
        try:
            t = _ast.parse(io.open(f, encoding='utf-8').read())
        except (SyntaxError, UnicodeDecodeError):
            continue
        for n in _ast.walk(t):
            if isinstance(n, _ast.Constant) and isinstance(n.value, str) \
                    and re.search(r'/(Users|home)/[a-z]', n.value) \
                    and not n.value.lstrip().startswith(('python3 ', 'tar ')):
                hard.append('%s:%d' % (os.path.basename(f), n.lineno))
    assert not hard, (
        '链上脚本里写死了本机绝对路径：%s —— 换台机器（尤其 Windows）'
        '那一步必崩，而本机上一直看不出来' % hard[:6])

    # ---- D 运行时：最深那个脚本上，bootstrap 与 cwd 无关 ----
    deep = os.path.join(dl, 'raw', 'jq', '_ingest', 'probes', 'probe_baostock.py')
    assert os.path.isfile(deep), '缺最深那个样本'
    src = io.open(deep, encoding='utf-8').read().splitlines()
    end = next(i for i, l in enumerate(src) if l.startswith('from paths import'))
    tmp = os.path.join(os.path.dirname(deep), '_probe_paths_tmp.py')
    io.open(tmp, 'w', encoding='utf-8').write(
        '\n'.join(src[:end + 1]) + '\nprint(TDX_DB)\n')
    try:
        r = subprocess.run([sys.executable, tmp], capture_output=True,
                           text=True, timeout=60, cwd=os.sep)   # cwd 故意无关
    finally:
        os.remove(tmp)
    assert r.returncode == 0 and r.stdout.strip() == real, (
        '最深那层（%s）的 bootstrap 没找到正本 —— cwd 无关这条不成立：%s'
        % (os.path.relpath(deep, dl), (r.stdout + r.stderr).strip()[-200:]))

    n_imp = sum(1 for f in _pys() if os.path.basename(f) != 'paths.py'
                and re.search(r'^(from paths import|import paths as)',
                              io.open(f, encoding='utf-8', errors='ignore').read(),
                              re.M))
    # ★ 反向自证：真的有一批文件在用正本，否则 B 那段是"全 0 == 全 0"
    assert n_imp >= 10, '只有 %d 个文件 import 正本 —— B 段在空转' % n_imp
    return ('正本 paths.py（%s）；扫 %d 个 .py 无人再走 tdx2db 符号链接；'
            '链上 %d 个脚本无本机绝对路径；最深那层 bootstrap 与 cwd 无关；'
            '%d 个文件用上正本'
            % (os.path.relpath(real, dl), n_scan, len(files), n_imp))


@case('日志按天保留：两条链各裁自己的 launchd 日志（原地截断，不改名）', tag='fast')
def t_log_retention():
    """2026-09-26 用户："现在我们的系统每天运行是不是会产生很多日志？
    有没有自动清理的机制，比如只保留 7 天、30 天之类，防止日志膨胀"

    量了一遍，真正**无界**的只有 launchd 那四个文件：

        _manifest/sync_logs/*.log    520 KB / 60 份   ✅ 有轮转（本轮改成按天）
        _manifest/launchd-sync.out   1.6 MB / 19 天   🔴 永不轮转 ~84 KB/天
        _manifest/launchd-tick.out   190 KB /  8 天   🔴 永不轮转

    🔴🔴 **只能原地截断，不能改名/删除** —— launchd 以 `O_APPEND` 持有 fd，
      rename/unlink 之后它继续往那个 inode 写：磁盘不释放、新文件永远是空的，
      **而日志看着"清干净了"**。`O_APPEND` 这条是**真跑一次验的**：把
      1.65 MB 截到 21 KB，等下一个 launchd 点位，文件从新 EOF 续写、
      NUL 字节 **0 个**（不是 O_APPEND 的话中间会留一大段空洞）。
    🔴 **按 `\n` 切，且读要 `newline=''`** —— 日志里有 `\r`（进度行原地刷新）：
      `splitlines()` 把它当换行（同一文件 `wc -l` 7860 vs splitlines 19560），
      而**文本模式读本身就会把 `\r` 翻译成 `\n`**，只改 split 是不够的。
    """
    import ast as _ast
    import importlib.util
    import datetime as _dt
    import tempfile
    dl = os.path.join(os.path.dirname(REPO), 'datalake')

    # ---- A 正本只有一处 ----
    lp = os.path.join(dl, 'logs.py')
    assert os.path.isfile(lp), '缺 datalake/logs.py —— 日志保留策略没有正本'
    _sp = importlib.util.spec_from_file_location('_dl_logs_probe', lp)
    L = importlib.util.module_from_spec(_sp)
    _sp.loader.exec_module(L)

    # ---- B 🔴 sync 链：裁必须排在 `--if-stale` 提前返回【之前】----
    #   轮询每 10 分钟一个点位，绝大多数时候走的就是那条提前返回，
    #   而它也往 launchd 的 .out 里写一行 —— 排在后面的话**最常走的那条路
    #   永远裁不到**，日志照旧涨，而它不报错。
    sd = io.open(os.path.join(dl, 'sync_daily.py'), encoding='utf-8').read()
    t = _ast.parse(sd)
    main = [n for n in t.body
            if isinstance(n, _ast.FunctionDef) and n.name == 'main'][0]
    trim_ln = [n.lineno for n in _ast.walk(main)
               if isinstance(n, _ast.Call)
               and getattr(n.func, 'attr', '') == 'trim_by_days']
    stale_ln = [n.lineno for n in _ast.walk(main)
                if isinstance(n, _ast.Call)
                and getattr(n.func, 'id', '') == '_if_stale']
    assert trim_ln, 'sync 链没有裁 launchd 日志'
    assert stale_ln, '找不到 _if_stale —— 判据在空转'
    assert min(trim_ln) < min(stale_ln), (
        '裁日志（第 %d 行）排在 `--if-stale`（第 %d 行）之后 —— '
        '而轮询绝大多数时候就从那里返回，最常走的路永远裁不到'
        % (min(trim_ln), min(stale_ln)))

    # ---- C 两条链都真的调了（漏一条就是那条链的日志无限涨）----
    td = io.open(os.path.join(REPO, 'tick_daily.py'), encoding='utf-8').read()
    assert 'trim_by_days' in td, 'tick 链没有裁自己的 launchd 日志'
    #   ★ 而且不许静默吞 —— "没裁"与"没跑到"要分得开
    assert not re.search(r'except Exception:\s*\n\s*pass\s*\n\s*today',
                         td), (
        'tick 链把日志清理的异常静默吞了（`except: pass`）—— '
        '"没裁"与"没跑到这里"就分不开了（我为此真查了一轮）')

    # ---- D 🔴 原地截断：`trim_by_days` 里不许出现 rename/replace/unlink ----
    lt = _ast.parse(io.open(lp, encoding='utf-8').read())
    fn = [n for n in lt.body
          if isinstance(n, _ast.FunctionDef) and n.name == 'trim_by_days'][0]
    for n in _ast.walk(fn):
        if isinstance(n, _ast.Call):
            a = getattr(n.func, 'attr', '')
            assert a not in ('rename', 'replace', 'unlink', 'remove'), (
                'trim_by_days 用了 os.%s —— launchd 持有 fd，改名/删除之后'
                '它继续往旧 inode 写：磁盘不释放、新文件永远是空的，'
                '而日志看着"清干净了"' % a)
    #   ★ 反向自证：它确实在**写回**（否则上面那圈是"全 0 == 全 0"）
    assert any(isinstance(n, _ast.Call) and getattr(n.func, 'attr', '') == 'open'
               and any(isinstance(a, _ast.Constant) and a.value == 'w'
                       for a in n.args)
               for n in _ast.walk(fn)), 'trim_by_days 根本没写回'

    # ---- E 行为：按天裁 / \r 不切断 / 认不出时间戳退回行数 / 幂等 ----
    NOW = _dt.datetime(2026, 9, 26, 18, 0)
    d = tempfile.mkdtemp()
    p = os.path.join(d, 'a.out')
    #   ★ 构造要**行多字少**（真日志就是这个形状），而且窗口内那段要
    #     **超过 `keep_min_lines`** —— 否则下限会把这一段也挡住，于是下面
    #     "行少字多也要裁得动"那句永远轮不到，**两句分不开**
    #     （同「别把功劳记在错的那句上」）。窗口外 140 行、窗口内 280 行。
    io.open(p, 'w', encoding='utf-8', newline='').write('\n'.join(
        ['[07-%02d 10:%02d] 窗口外 %d' % (i, j, i) for i in range(1, 29)
         for j in range(5)] +
        ['[09-%02d 10:%02d] 进度 a\r进度 b 近期 %d' % (i, j, i)
         for i in range(20, 27) for j in range(40)]) + '\n')
    n1, why1 = L.trim_by_days(p, days=30, min_bytes=0, now=NOW)
    got = io.open(p, encoding='utf-8', newline='').read()
    #   ★ 判据用 `[07-` 这个**不会被别的行前缀命中**的标记 ——
    #     写成 `'旧 1'` 的话 `旧 19` 也算命中，断言会为了错的理由红
    assert '[07-' not in got, '窗口外那 140 行没裁掉（%s）' % why1
    assert '近期 26' in got, '把窗口内的也裁了（%s）' % why1
    #   🔴 带 \r 的行必须**完整**（split('\n') + newline='' 两条缺一不可）
    assert '进度 a\r进度 b' in got, (
        '带 \\r 的行被切成两段了 —— `splitlines()` 把 \\r 当换行，'
        '而文本模式读本身也会把 \\r 翻成 \\n（要 newline=""）')
    #   幂等：全在窗口内时一个字节都不动
    n2, _ = L.trim_by_days(p, days=30, min_bytes=0, now=NOW)
    assert n2 == 0, '再裁一次又动了 %d 字节 —— 不幂等' % n2
    #   🔴 行数下限只在"认不出时间戳"那条兜底路上生效 ——
    #     按天那条路上也套的话，"行少字多"的日志永远裁不动（实测踩过）
    p2 = os.path.join(d, 'fat.out')
    io.open(p2, 'w', encoding='utf-8', newline='').write('\n'.join(
        '[%s] %s' % ((NOW - _dt.timedelta(days=k)).strftime('%m-%d %H:%M'),
                     'x' * 2000) for k in range(60, -1, -1)) + '\n')
    L.trim_by_days(p2, days=30, min_bytes=0, now=NOW)
    left = [x for x in io.open(p2, encoding='utf-8', newline='').read().split('\n') if x]
    assert 29 <= len(left) <= 32, (
        '"行少字多"的日志没裁动（剩 %d 行）—— 行数下限不该管按天那条路' % len(left))
    p3 = os.path.join(d, 'c.err')
    io.open(p3, 'w', encoding='utf-8', newline='').write(
        '\n'.join('Traceback 行 %d' % i for i in range(500)))
    L.trim_by_days(p3, days=30, keep_min_lines=50, min_bytes=0, now=NOW)
    assert len(io.open(p3, encoding='utf-8', newline='').read().split('\n')) == 50, \
        '认不出时间戳时没退回"保留最后 N 行"'

    # ---- F sync_logs 目录按【天】清，且有 keep_min 下限 ----
    assert re.search(r'^KEEP_DAYS\s*=\s*(\d+)', sd, re.M), (
        'sync_daily 还在按【份数】留日志 —— 轮询"齐了就秒退不建文件"，'
        '份数在忙的日子只盖住两三天、闲的日子盖住半年，说不清"留多久"')
    dd = os.path.join(d, 'sync_logs')
    os.makedirs(dd)
    for i in range(10):
        f = os.path.join(dd, '%02d.log' % i)
        io.open(f, 'w', encoding='utf-8').write('x')
        os.utime(f, (time.time() - i * 864000, time.time() - i * 864000))
    L.prune_dir_by_days(dd, '*.log', days=30, keep_min=5)
    assert len(os.listdir(dd)) == 5, (
        'keep_min 下限没护住 —— 系统时间跳变时整个目录会被清空')

    keep = int(re.search(r'^KEEP_DAYS\s*=\s*(\d+)', sd, re.M).group(1))
    return ('保留 %d 天；裁排在 --if-stale 之前（%d 行 < %d 行）；'
            '两条链都裁且不静默吞；原地截断不 rename（launchd 持 O_APPEND，'
            '实测截断后续写 NUL=0）；\\r 行不切断、幂等、'
            '"行少字多"也裁得动、认不出时间戳退回行数下限；'
            'sync_logs 按天清 + keep_min 护底'
            % (keep, min(trim_ln), min(stale_ln)))


@case('跨平台：首次启动那条路上不许有 POSIX-only 的东西', tag='fast')
def t_windows_boot_path():
    """2026-09-26 用户："现在的代码能在 windows 上下载后，直接启动，
    开始下载数据吗？"

    判据**扫的是那条路上真正会跑到的文件**（启动 -> 建库六个阶段 ->
    每日链 13 步），而不是全仓 —— 全仓扫会把 `rebuild_lake_db.py`
    那种一次性迁移工具也算进来，天天误报（同「假告警看多了就不看告警」）。

    🔴 抓到过的真问题：
        · `build_etf_lake.py` 的 `os.symlink` —— **Windows 上要管理员或
          开发者模式**（WinError 1314），而它是每日链的 **9/13**
        · `signal.SIGKILL` —— Windows 上**没有这个常量**
        · `os.kill(pid, 0)` —— Windows 上**会把进程真的杀掉**
      三条**都不报错**（在 macOS 上），全是换台机器才崩。
    """
    import ast as _ast
    dl = os.path.join(os.path.dirname(REPO), 'datalake')

    # 路径**派生**：建库阶段照 setup_stages 的 cmd、每日链照 --dry
    import importlib.util
    _sp = importlib.util.spec_from_file_location(
        '_ss_win', os.path.join(dl, 'setup_stages.py'))
    _sm = importlib.util.module_from_spec(_sp)
    _sp.loader.exec_module(_sm)
    files = {os.path.join(REPO, 'serve.py')}
    for st in _sm.stages():
        for a in (st.get('cmd') or []):
            if str(a).endswith('.py') and os.path.isfile(str(a)):
                files.add(os.path.abspath(str(a)))
    for _cwd, _cmd in _chain_dry(with_cwd=True):
        for _a in re.findall(r'\S+\.py', _cmd):
            c = os.path.normpath(os.path.join(_cwd, _a))
            if os.path.isfile(c):
                files.add(c)
    # 🔴 **两个定时脚本自己也要扫。** `sync_daily.py` 是【跑链的那个】——
    #   它不会出现在自己 `--dry` 的输出里，于是第一版**整个漏掉了它**
    #   （W7「某处用 shell=True」变异因此没抓到）。清单从
    #   `setup_tdx.build_jobs()` 派生 —— 那是定时器真正会执行的命令。
    _sp2 = importlib.util.spec_from_file_location(
        '_st_win', os.path.join(dl, 'setup_tdx.py'))
    _stm = importlib.util.module_from_spec(_sp2)
    _sp2.loader.exec_module(_stm)
    for _j in _stm.build_jobs(_stm.load_schedule()[0]):
        for _a in _j[1]:
            if str(_a).endswith('.py') and os.path.isfile(str(_a)):
                files.add(os.path.abspath(str(_a)))
    # ★ 再带上这些入口**直接 import 的、住在 datalake 下的**模块
    #   （`logs` / `paths` / `progress` / `build.is_stale`）—— 它们也在
    #   那条路上跑，而照清单拼会漏掉将来新加的（同「扫目录不照清单拼」）。
    for f in list(files):
        try:
            _t = _ast.parse(io.open(f, encoding='utf-8').read())
        except (SyntaxError, UnicodeDecodeError):
            continue
        for n in _ast.walk(_t):
            mods = []
            if isinstance(n, _ast.Import):
                mods = [a.name for a in n.names]
            elif isinstance(n, _ast.ImportFrom) and n.level == 0:
                mods = [n.module or '']
                mods += ['%s.%s' % (n.module or '', a.name) for a in n.names]
            for m in mods:
                cand = os.path.join(dl, *m.split('.')) + '.py'
                if os.path.isfile(cand):
                    files.add(os.path.abspath(cand))
            # ★ 还有**起子进程**跑的（`is_stale.py` 就是这么被调的：
            #   `os.path.join(DL, 'build', 'is_stale.py')` —— import 走不到，
            #   而字面量也只是 `'is_stale.py'`，单独解不出来）。
            #   所以按 `os.path.join` 的**常量参数**拼起来解。
            if isinstance(n, _ast.Call) \
                    and getattr(n.func, 'attr', '') == 'join':
                segs = [a.value for a in n.args
                        if isinstance(a, _ast.Constant)
                        and isinstance(a.value, str)]
                if segs and segs[-1].endswith('.py'):
                    for base in (dl, os.path.dirname(f),
                                 os.path.dirname(REPO), REPO):
                        cand = os.path.normpath(os.path.join(base, *segs))
                        if os.path.isfile(cand):
                            files.add(os.path.abspath(cand))
                            break
    files = sorted(files)
    assert len(files) >= 19, '只解析出 %d 个文件 —— 判据在空转' % len(files)
    #   ★ 反向自证：跑链的那个与它的正本模块都在里面
    for _must in ('sync_daily.py', 'logs.py', 'paths.py', 'is_stale.py'):
        assert any(f.endswith(_must) for f in files), \
            '%s 不在扫描范围里 —— 而它就在那条路上跑' % _must

    POSIX_MOD = {'fcntl', 'pwd', 'grp', 'termios', 'tty', 'resource', 'syslog'}
    POSIX_BIN = {'lsof', 'ps', 'launchctl', 'chmod', 'ln', 'uname', 'which',
                 'bash', 'sh', 'osascript', 'plutil', 'kill', 'sed', 'awk'}
    bad = []
    for f in files:
        src = io.open(f, encoding='utf-8').read()
        t = _ast.parse(src)
        for n in _ast.walk(t):
            for c in _ast.iter_child_nodes(n):
                c.parent = n
        rel = os.path.relpath(f, os.path.dirname(REPO))

        def _guarded(node):
            """往上找最近的 `if`，条件里提到平台就算守住了。"""
            q = node
            while getattr(q, 'parent', None) is not None:
                q = q.parent
                if isinstance(q, _ast.If):
                    c = _ast.unparse(q.test)
                    if ('platform' in c or 'osname' in c or 'os.name' in c):
                        return True
            return False

        for n in _ast.walk(t):
            if isinstance(n, (_ast.Import, _ast.ImportFrom)):
                mods = [(a.name or '').split('.')[0]
                        for a in getattr(n, 'names', [])]
                if isinstance(n, _ast.ImportFrom):
                    mods.append((n.module or '').split('.')[0])
                for m in mods:
                    if m in POSIX_MOD:
                        bad.append('%s:%d 只有 POSIX 才有的模块 %s'
                                   % (rel, n.lineno, m))
            if isinstance(n, _ast.Attribute) and n.attr == 'SIGKILL':
                # ★ `getattr(signal, 'SIGKILL', …)` 是**字符串**不是属性，
                #   所以这里命中的一定是直接引用
                bad.append('%s:%d 直接引用 signal.SIGKILL（Windows 上没有'
                           '这个常量，会 AttributeError）' % (rel, n.lineno))
            if isinstance(n, _ast.Call):
                fn = getattr(n.func, 'attr', '') or getattr(n.func, 'id', '')
                if fn == 'symlink':
                    # 🔴 `os.symlink` **只许出现在 `_link_tree` 里面** ——
                    #   第一版查的是"这个文件里有没有 `_link_tree`"，而变异
                    #   只改调用点、helper 还在 -> **照样全绿**
                    #   （判据比要证的事宽，W1 变异实测漏过）。
                    q, inside = n, False
                    while getattr(q, 'parent', None) is not None:
                        q = q.parent
                        if isinstance(q, _ast.FunctionDef) \
                                and q.name == '_link_tree':
                            inside = True
                            break
                    if not inside:
                        bad.append('%s:%d 裸 os.symlink（不在 _link_tree 里）'
                                   ' —— Windows 上要管理员或开发者模式'
                                   '（WinError 1314）' % (rel, n.lineno))
                if fn in ('run', 'Popen', 'call', 'check_output', 'check_call'):
                    a0 = n.args[0] if n.args else None
                    nm = ''
                    if isinstance(a0, _ast.List) and a0.elts \
                            and isinstance(a0.elts[0], _ast.Constant):
                        nm = os.path.basename(str(a0.elts[0].value))
                    if nm in POSIX_BIN and not _guarded(n):
                        bad.append('%s:%d 外部命令 %s 没有平台分支'
                                   % (rel, n.lineno, nm))
                if any(k.arg == 'shell' and getattr(k.value, 'value', None)
                       is True for k in n.keywords):
                    bad.append('%s:%d shell=True（Windows 上 shell 语法不同）'
                               % (rel, n.lineno))
    assert not bad, ('首次启动那条路上还有 POSIX-only 的东西（新机器上崩、'
                     '本机上一直看不出来）：\n    ' + '\n    '.join(bad[:8]))

    # ---- 目录挂载三条路：符号链接 / junction / 响亮失败（不许退回复制）----
    bl = os.path.join(dl, 'build', 'build_etf_lake.py')
    bsrc = io.open(bl, encoding='utf-8').read()
    bt = _ast.parse(bsrc)
    fn = [n for n in bt.body if isinstance(n, _ast.FunctionDef)
          and n.name == '_link_tree'][0]
    # 🔴 判据要看**真实调用的参数**，不是 `unparse` 出来的文本 ——
    #   `mklink` 就写在 `_link_tree` 自己的 docstring 里，查文本必然命中，
    #   把那条 subprocess 换成别的命令照样全绿（W2 变异实测漏过）。
    #   **这是第六次踩「查字符串命中自己写的注释」。**
    _mk = [n for n in _ast.walk(fn)
           if isinstance(n, _ast.Call)
           and any(isinstance(a, _ast.List)
                   and any(isinstance(e, _ast.Constant) and e.value == 'mklink'
                           for e in a.elts)
                   for a in n.args)]
    assert _mk, (
        '_link_tree 没有 Windows 退路 —— 目录联接（mklink /J）对本地目录'
        '不需要任何权限，是这里唯一能用的替代')
    #   🔴 不许退回"复制"：492 MB 而且**不会跟着主 lake 更新**，
    #     ETF 回测会拿着旧指数点位跑而不报错
    for n in _ast.walk(fn):
        if isinstance(n, _ast.Call):
            a = getattr(n.func, 'attr', '')
            assert a not in ('copytree', 'copy2', 'copyfile'), (
                '_link_tree 退回了复制 —— 那是 492 MB 的副本，主 lake 每天'
                '更新而它不会跟着变，回测拿旧数据跑**且不报错**')
    #   ★ 真跑一遍符号链接那条（POSIX 上走得到）+ 幂等
    import tempfile
    d = tempfile.mkdtemp()
    tgt = os.path.join(d, 'tgt')
    os.makedirs(tgt)
    io.open(os.path.join(tgt, 'x.txt'), 'w').write('hi')
    _spec = importlib.util.spec_from_file_location('_bel_probe', bl)
    _bel = importlib.util.module_from_spec(_spec)
    try:
        _spec.loader.exec_module(_bel)
    except SystemExit:
        pass
    lk = os.path.join(d, 'lk')
    how = _bel._link_tree(tgt, lk)
    assert os.path.isdir(lk) and os.path.isfile(os.path.join(lk, 'x.txt')), \
        '_link_tree 挂完读不到里面的文件（%s）' % how
    #   报错要说清下一步（不是一句裸 OSError）
    try:
        _bel._link_tree(tgt, lk)          # 已经在了 -> 必然失败
        raise AssertionError('重复挂载居然成功了 —— 判据在空转')
    except (OSError, RuntimeError) as e:
        assert os.name != 'nt' or '开发人员模式' in str(e), \
            '挂不上时没说下一步：%s' % e

    # ---- 依赖清单必须盖住【真正会被 import 的】第三方包 ----
    #   🔴 缺 duckdb 连 `serve.py` 都起不来（实测屏蔽掉它 ->
    #     `import assay.server` 直接 ModuleNotFoundError）。而报错只是
    #     一句裸 ModuleNotFoundError，**指不到"该装什么"** ——
    #     新机器上"下载完直接启动"那一步就卡死在这里。
    req = os.path.join(dl, 'requirements.txt')
    assert os.path.isfile(req), (
        '没有依赖清单 —— 新机器上不知道该 pip install 什么')
    want = {ln.split('>')[0].split('=')[0].split('[')[0].strip().lower()
            for ln in io.open(req, encoding='utf-8').read().splitlines()
            if ln.strip() and not ln.strip().startswith('#')}
    assert want, 'requirements.txt 里一个包都没有'
    #   ★ 判据取**运行时真的 import 了什么**，不是照源码猜：
    #     起一个子进程 import `assay.server`，拿 sys.modules 减标准库
    code = (
        'import sys;sys.path.insert(0,%r);b=set(sys.modules);'
        'import assay.server;'
        'print(" ".join(sorted({m.split(".")[0] for m in set(sys.modules)-b}'
        '-set(sys.stdlib_module_names))))' % REPO)
    r = subprocess.run([sys.executable, '-c', code], capture_output=True,
                       text=True, cwd=REPO, timeout=180)
    assert r.returncode == 0, 'import assay.server 失败：%s' % r.stderr[-200:]
    got = {t for t in r.stdout.split()
           if t and not t.startswith('_') and t not in (
               'assay', 'tests', 'build', 'factors', 'paths', 'logs',
               'progress', 'strategies')}
    #   ⚠ 传递依赖（cloudpickle / dateutil / six / cython_runtime 是
    #     pandas / pyarrow 自己带的）不要求列 —— 只要求**直接**那几个在
    TRANSITIVE = {'cloudpickle', 'dateutil', 'six', 'cython_runtime',
                  'pytz', 'tzdata'}
    miss = sorted((got - TRANSITIVE) - want)
    assert not miss, (
        '这些包启动时真的会 import，却不在 requirements.txt 里：%s —— '
        '新机器上就是一句裸 ModuleNotFoundError，指不到该装什么' % miss)
    #   ★ 反向自证：真的量到了东西（否则上面那句是"空 - 空"）
    assert len(got) >= 4, '只量到 %d 个第三方包 —— 判据在空转' % len(got)
    #   🔴 清单要**真的能提交**：`datalake/.gitignore` 是**白名单**
    #     （`*` 先忽略一切再逐条放行）—— 不放行的话这份清单进不了版本库，
    #     新 clone 上既没有它、上面那几句断言也会直接红（本轮实测踩到）。
    _ci = subprocess.run(['git', 'check-ignore', '-q', 'requirements.txt'],
                         cwd=dl, capture_output=True)
    assert _ci.returncode != 0, (
        'datalake/requirements.txt 被 .gitignore 忽略了 —— 它提交不进去，'
        '新 clone 上没有这份清单')
    #   🔴 **包名只许列一处**：`INSTALL.md` 里原来也硬编码了一份
    #     `pip install duckdb pandas numpy pyarrow` —— 两份清单迟早分叉，
    #     而"改漏一处"的表现是新机器上一句裸 ModuleNotFoundError。
    _ins = os.path.join(REPO, 'INSTALL.md')
    assert os.path.isfile(_ins), '缺 INSTALL.md（从零装起的指南）'
    _itxt = io.open(_ins, encoding='utf-8').read()
    for _m in re.finditer(r'^\s*pip install\s+(.+)$', _itxt, re.M):
        _args = _m.group(1)
        if _args.lstrip().startswith('-r'):
            continue
        _hit = sorted(w for w in want if re.search(r'\b%s\b' % w, _args))
        assert len(_hit) < 2, (
            'INSTALL.md 里又硬编码了一份包名清单（%s）—— 包名只许在 '
            'requirements.txt 一处列，两份迟早分叉' % ', '.join(_hit))
    #   ★ 判据要钉**真正会被照着敲的那一行**，不是"文里提到过"——
    #     第一版写的是 `'requirements.txt' in _itxt`，而上面那句说明文字
    #     里就有这个词，**把 pip 那行换掉照样全绿**（G3 变异实测漏过）。
    #     这是第七次踩「查字符串命中自己写的文案」。
    assert re.search(r'^\s*pip install\s+-r\s+\S*requirements\.txt',
                     _itxt, re.M), (
        'INSTALL.md 里没有一行 `pip install -r …requirements.txt` —— '
        '人照着它装会漏掉新依赖')

    # ---- H) Windows 装机脚本 install.bat --------------------------------
    #   用户 2026-09-26：「装机脚本可以先制作一个 windows 版本」
    #
    # 🔴🔴 **它必须是纯 ASCII。** `chcp 65001` 之后 cmd.exe 仍按**字节偏移**
    #   续读批处理文件，中间换代码页而文件里又有多字节字符，解析位置会错位
    #   —— 而那**不报错**，表现是后半截命令变成乱码或整段被跳过。
    #   所以中文一律由 `install.py` 打（它那侧的 UTF-8 是验过的）。
    #   ⚠ 这条在 macOS 上**测不到行为**，只能钉住"不许出现非 ASCII"。
    _bat = os.path.join(REPO, 'install.bat')
    assert os.path.isfile(_bat), '缺 install.bat（Windows 装机脚本）'
    _bb = io.open(_bat, 'rb').read()
    _bad = [i for i, b in enumerate(_bb) if b > 127]
    assert not _bad, (
        'install.bat 里有 %d 个非 ASCII 字节（首个在偏移 %d）—— '
        'chcp 之后 cmd 按字节偏移续读，多字节字符会让解析错位，'
        '而它不报错。中文交给 install.py 打' % (len(_bad), (_bad or [-1])[0]))
    assert not _bb.startswith(b'\xef\xbb\xbf'), (
        'install.bat 带了 UTF-8 BOM —— cmd 会把它当成命令的一部分，'
        '第一行直接报「不是内部或外部命令」')
    # 🔴 CRLF：LF-only 的 .bat 在 label / goto 上有已知的解析问题
    assert _bb.count(b'\n') == _bb.count(b'\r\n'), (
        'install.bat 有 LF-only 的行 —— .bat 要 CRLF')
    # 🔴🔴 上面那条查的是【工作区】那份——而 `core.autocrlf=input` 会在 add
    #   时把 CR 剥掉存进 blob，于是「工作区 CRLF / 仓库 LF」，守卫照样绿，
    #   **而新 clone 拿到的是坏的**（同 requirements.txt 被 .gitignore 挡住
    #   那条：本机看着好、新 clone 上不成立）。
    # 🔴 判据要读【索引】那份 `.gitattributes`，不是工作区、也不是
    #   `git check-attr`：后者在两处任一还在时都说 crlf（实测它在工作区那份
    #   缺失时回落到 index），于是「rm 工作区那份」与「git rm --cached」
    #   **两条变异都抓不到**。而"新 clone 会不会拿到 CRLF"只取决于
    #   提交进去的那份——索引就是"下一次提交会是什么"。
    _idx = subprocess.run(['git', 'show', ':.gitattributes'],
                          cwd=REPO, capture_output=True, text=True)
    assert _idx.returncode == 0, (
        '.gitattributes 不在 git 索引里 —— 少了它，install.bat 会被存成 LF'
        '（本机 core.autocrlf=input 剥掉 CR），新 clone 出来的 .bat 是坏的，'
        '而本机工作区那份仍是 CRLF、上面那条守卫照样绿')
    assert re.search(r'^\s*\*\.bat\b[^\n]*\beol=crlf\b', _idx.stdout, re.M), (
        '索引里的 .gitattributes 没有为 *.bat 声明 eol=crlf：\n%s'
        % _idx.stdout.strip()[:200])

    _bt = _bb.decode('ascii')
    # 判据不是"提到过 install.py"（注释里也提），是**真的转发**
    assert re.search(r'^\s*%PY%\s+"[^"]*install\.py"\s+%\*', _bt, re.M), (
        'install.bat 没有把参数转发给 install.py —— '
        '`install.bat --check` 会静默变成"不带参数跑"')
    # 🔴 `py -3` 必须排在裸 `python` **前面**：新装的 Windows 上 `python`
    #   常常是 Microsoft Store 的存根，跑起来是打开商店而不是执行脚本
    #   （而那不报错，只是装机卡在那儿）
    # 🔴 找位置要用正则：文件是 CRLF、而 `python -c` 那行在块里带缩进
    #   —— 第一版写死 `'\npython -c'` 找不到，判据报 -1 当场自伤
    _mpy = re.search(r'^\s*py -3 -c', _bt, re.M)
    _mpn = re.search(r'^\s*python -c', _bt, re.M)
    assert _mpy and _mpn, 'install.bat 里没有两条解释器探测'
    _ipy, _ipn = _mpy.start(), _mpn.start()
    assert 0 < _ipy < _ipn, (
        'install.bat 要先试 `py -3` 再试 `python` —— 顺序反了会撞上 '
        'Microsoft Store 的 python 存根（py=%d python=%d）' % (_ipy, _ipn))
    # 找不到解释器时要**说下一步**，不是静默退出
    assert 'python.org/downloads/windows' in _bt, (
        'install.bat 在找不到 Python 时没给下载地址 —— '
        '那正是新机器上唯一走得到的分支')
    # 双击打开时窗口不许一闪而过（否则结果一个字都看不到）
    # 🔴 判据要认准**转发之后**那一处 —— 文件里还有一处 pause 在
    #   「找不到 Python」那支里，裸 `^\s*pause$` 两处都命中，
    #   删掉末尾那个照样绿（变异 M6 实测漏过）
    assert re.search(r'install\.py"[^\n]*\n(?:.|\n)*^\s*pause\s*$',
                     _bt, re.M), (
        'install.bat 在转发给 install.py 之后没有 pause —— '
        '双击跑完窗口当场关掉，结果一个字都看不见')

    return ('扫 %d 个文件（启动+建库阶段+每日链，清单都是派生的）：'
            '没有 POSIX-only 模块 / 外部命令都有平台分支 / '
            '不许直接引用 SIGKILL / 不许 shell=True；'
            '目录挂载三条路（symlink -> junction -> 响亮失败，'
            '不退回 492 MB 的复制），真挂一次读得到里面的文件；'
            'requirements.txt 盖住运行时真的 import 的 %d 个第三方包；'
            'install.bat 纯 ASCII + CRLF（含签出属性）、py -3 优先、转发参数'
            % (len(files), len(got - TRANSITIVE)))


# ====================== 因子层（datalake/build/factors） ======================

@case('因子层：与 indicators 逐值对数 + 试跑==落盘，两条自证命令必须绿', tag='fast')
def t_factor_selfproofs():
    """把因子模块那两条**手动**守卫接进来。

    🔴 它们此前一条都不在 selftest 里 —— 而接进来的当天就发现**两条都已经
      坏了**：加财务族之后 `deps` 里出现了 as-of 才有的 `b_*` 列，
      而这两个脚本各自拼 SELECT，于是

          check_vs_indicators  -> NameError: b_total_current_assets
          factor_try --selftest -> Binder Error: 没有 b_account_receivable 这一列

      两份同时坏、坏法还一样（同「两处实现必然分叉」）。修法不是各补一次：
      取数搬进 `factors/load.py` 一处，三个调用方都用它。
      ★ 重构是**值中性**的：与盘上那份逐位比，43,428 行 × 162 列完全相同。

    ★ 走**子进程**而不是 import：这两条要证的就是"那两条命令还能跑"，
      而人真去跑的时候敲的就是这个（同「模板要逐条真跑」那条）。
    """
    import subprocess
    dl = os.path.join(os.path.dirname(REPO), 'datalake')
    outs = {}
    for name, argv in [
            ('对数', ['python3', 'build/factors/tools/check_vs_indicators.py']),
            ('自证', ['python3', 'build/factor_try.py', '--selftest'])]:
        r = subprocess.run(argv, cwd=dl, capture_output=True, text=True,
                           timeout=600)
        assert r.returncode == 0, \
            '%s 退出码 %d：\n%s' % (name, r.returncode, (r.stdout + r.stderr)[-900:])
        outs[name] = r.stdout
    # 🔴 判据不是"退出码 0" —— 脚本自己的总判定里有 🔴 就是红的
    for name, s in outs.items():
        assert '🔴' not in s.split('总判定')[-1], \
            '%s 的总判定是红的：%s' % (name, s.split('总判定')[-1][:300])
    # 🔴 **条数不许写死**（报告串会说谎）：对数脚本自己印跑了几项，
    #   这里只要求它 >= 25 且与"因子里确实有这么多个"对得上。
    m = re.search(r'✓ (\d+) 项与 indicators\.py', outs['对数'])
    assert m and int(m.group(1)) >= 25, '对数项数读不出来或少于 25：%s' % outs['对数'][-300:]
    return '对数 %s 项全过；试跑==Spec==族文件手写三者同一条路（含反向自证）' % m.group(1)


@case('因子层：注册表与盘上产物必须一致（公式改了而没重建 = 静默过期）', tag='fast')
def t_factor_artifacts():
    """注册表是正本，目录表与因子面板是产物 —— 它们对不上就是**静默过期**。

    🔴 三种对不上，表现各不相同、**没有一种会报错**：

    | 对不上什么 | 表现 |
    |---|---|
    | 目录表少了某个因子 | 页面/查询里那个因子"不存在"，而面板里其实有它 |
    | 面板少了某一列 | 那个因子**整列没落盘**，读它的人拿到 KeyError 或空 |
    | `spec_sig` 变了 | **公式改了而没重建** —— 盘上的值按旧公式算，
    |              | 而它长得和新的一模一样 |

    ★ 第三条正是 `_meta.json` 存 `spec_sig` 的全部理由；这条用例就是它的消费方。
    """
    import json
    import sys as _sys
    dl = os.path.join(os.path.dirname(REPO), 'datalake')
    bd = os.path.join(dl, 'build')
    if bd not in _sys.path:
        _sys.path.insert(0, bd)
    for m in [k for k in list(_sys.modules) if k == 'factors' or k.startswith('factors.')]:
        del _sys.modules[m]                      # 拿当前磁盘上的那份
    from factors import all_specs, GROUPS        # noqa: E402

    specs = all_specs()
    ids = [s.id for s in specs]
    assert len(ids) == len(set(ids)), '因子编号有重复'
    assert all(s.group in GROUPS for s in specs)

    # ---- ① 目录表 ----
    import pandas as _pd
    cat_p = os.path.join(dl, 'mart', 'factor_catalog.parquet')
    assert os.path.isfile(cat_p), '没有目录表 —— 跑 build_factor_catalog.py'
    cat = _pd.read_parquet(cat_p)
    assert set(cat['factor_id']) == set(ids), (
        '目录表与注册表对不上：目录多 %s / 少 %s —— 跑 build_factor_catalog.py'
        % (sorted(set(cat['factor_id']) - set(ids))[:5],
           sorted(set(ids) - set(cat['factor_id']))[:5]))

    # ---- ② 因子面板的列 ----
    import glob as _g
    ys = sorted(_g.glob(os.path.join(dl, 'mart', 'factor_daily', 'factor_*.parquet')))
    assert ys, '没有因子面板 —— 跑 build_factor_daily.py'
    import duckdb as _d
    cols = set(d[0] for d in _d.connect(':memory:').execute(
        "SELECT * FROM read_parquet('%s') LIMIT 0" % ys[-1]).description)
    miss = set(ids) - cols
    assert not miss, ('因子面板少了这几列（它们【整列没落盘】）：%s —— '
                      '跑 build_factor_daily.py --force' % sorted(miss)[:8])

    # ---- ③ 公式指纹：改了公式而没重建 ----
    meta = json.load(open(os.path.join(dl, 'mart', 'factor_daily', '_meta.json')))
    import importlib.util as _iu
    _sp = _iu.spec_from_file_location('_bfd', os.path.join(bd, 'build_factor_daily.py'))
    _bfd = _iu.module_from_spec(_sp)
    _sp.loader.exec_module(_bfd)
    now = _bfd._spec_sig()
    assert meta.get('spec_sig') == now, (
        '公式指纹对不上（盘上 %s / 当前 %s）—— 有人改了公式没重建，'
        '盘上那 %s 的值是按【旧公式】算的。跑 build_factor_daily.py --force'
        % (meta.get('spec_sig'), now, '%.1f GB' % (
            sum(v['bytes'] for v in meta.get('years', {}).values()) / 1e9)))

    # ---- ④ 🔴 跨进程可复现：注册表顺序不许依赖 set 的迭代顺序 ----
    #   本项目为「回测跨进程不可复现」栽过一次（PYTHONHASHSEED）。
    #   同一个进程里读两遍只证明进程内一致，什么都没证 —— 必须跨进程。
    import subprocess
    code = ("import sys; sys.path.insert(0, %r)\n"
            "from factors import all_specs\n"
            "print(','.join(s.id for s in all_specs()))" % bd)
    seen = set()
    for seed in ('0', '1', '12345'):
        env = dict(os.environ, PYTHONHASHSEED=seed)
        r = subprocess.run(['python3', '-c', code], capture_output=True,
                           text=True, env=env, timeout=120)
        assert r.returncode == 0, r.stderr[-400:]
        seen.add(r.stdout.strip())
    assert len(seen) == 1, '注册表顺序跨进程会变（%d 种）—— 有地方依赖了 set 的迭代顺序' % len(seen)

    n_abs = int((~cat['xs_comparable']).sum())
    return ('%d 个因子：目录表 / 面板列 / 公式指纹(%s) 三者一致；'
            '跨 3 个 hash 种子顺序相同；其中横截面不可比 %d 个'
            % (len(ids), now, n_abs))


@case('因子广场：整页照服务端清单渲染，且四处入口一个都不许断', tag='web')
def t_factor_page():
    """这一页与指标广场是**同一条纪律的两次应用**，判据也照抄那几条。

    🔴 **四件事都不报错**，所以四条都要单独钉：

    | 坏法 | 表现 |
    |---|---|
    | 前端硬编码区间/族清单 | 服务端加一个区间或一个因子族，页面上**不会出现** |
    | 三条口径不印出来 | 人拿这一页的 −19.86% 去跟 `factors.xlsx` 比大小 —— 而那是两个基准 |
    | 量纲不可比的默认列出来 | 前 12 名里 3 个是【成交量(股)】，**排序排的是股本不是因子** |
    | 入口断链 | 从哪儿都点不进来，**而那不报错，只是从此没人找得到** |

    ★ 与指标广场**互相指得到**且各自说清"我不是另一个"：名字太像，
      不说的话人会在指标广场找"哪个因子选股有效"（那一页一个横截面的数都没有）。
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return '跳过（无 playwright）'
    import json
    import threading
    from http.server import ThreadingHTTPServer
    from assay import server as sv

    httpd = ThreadingHTTPServer(('127.0.0.1', 0), sv.Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    U = 'http://127.0.0.1:%d' % port
    from assay.srv import factors as fsrv
    fe = fsrv._eval()
    meta = fsrv.api_factors_meta({})


    notes = []

    # ---- ⓪ `_win_start` 向量化之后必须与朴素写法等价 ----------------
    #   它原本是 `sorted(set(dates))[-n]` —— 对 90 万行做逐元素 Python
    #   迭代，占 summary 的 85%。改成 `np.sort(pd.unique(...))` 是纯粹的
    #   提速，**语义一个字不许变**。
    #   ★ 期望值用**被替换掉的那个朴素实现**现算（不是抄一份新的），
    #     所以这条对"将来又有人动它"仍然有效。
    import pandas as _pd
    import datetime as _dt
    _days = [_dt.date(2020, 1, 1) + _dt.timedelta(days=i) for i in range(40)]
    import random as _rnd
    _shuf = _days[:] * 3
    _rnd.Random(0).shuffle(_shuf)                          # 🔴 必须乱序
    for _src in (_pd.Series(_pd.to_datetime(_shuf)),       # 乱序 + 有重复
                 _pd.Series(_pd.to_datetime(_days * 3)),   # 有序 + 有重复
                 _pd.Series(_pd.to_datetime(_days[:5]))):  # 比 n 还短
        for _n in (None, 1, 7, 40, 999):
            got = fe._win_start(_src, _n)
            want = None
            if _n is not None:
                u = sorted(set(_src))
                want = u[-_n] if len(u) > _n else u[0]
            assert (got is None and want is None) or _pd.Timestamp(got) == _pd.Timestamp(want), \
                '_win_start(n=%s) 与朴素实现不等价：%s vs %s' % (_n, got, want)
    notes.append('_win_start 向量化与朴素写法等价')

    with sync_playwright() as pw:
        br = pw.chromium.launch()
        pg = br.new_page(viewport={'width': 1440, 'height': 900})
        errs = []
        pg.on('pageerror', lambda e: errs.append(str(e)))

        # ---- ① 清单照服务端给的渲染（三份都比，不是"有几个按钮"）----
        pg.goto(U + '/factors.html', wait_until='networkidle')
        pg.wait_for_selector('table.lvt tbody tr', timeout=60000)
        assert not errs, '页面报错：%s' % errs
        wl = pg.eval_on_selector_all('a[data-nav="win"]',
                                     'a=>a.map(x=>x.textContent.trim())')
        assert wl == [x['label'] for x in meta['windows']], \
            '区间清单与服务端对不上：%s vs %s' % (wl, [x['label'] for x in meta['windows']])
        hl = pg.eval_on_selector_all('a[data-nav="h"]', 'a=>a.map(x=>x.dataset.v)')
        assert hl == [str(x) for x in meta['horizons']], '前瞻清单对不上：%s' % hl
        gv = pg.eval_on_selector_all('#facg option',
                                     'a=>a.map(x=>x.value).filter(v=>v)')
        assert gv == [x['group_key'] for x in meta['groups']], \
            '族清单对不上：%s vs %s' % (gv, [x['group_key'] for x in meta['groups']])
        # 反向自证：这三份清单**真的有内容**，否则上面三条是"空 == 空"
        assert len(wl) >= 3 and len(hl) >= 2 and len(gv) >= 4, \
            '清单太短，上面三条等于空转：%d/%d/%d' % (len(wl), len(hl), len(gv))
        # 🔴 **池子选择器**同理照服务端那份清单渲染（2026-09-24 加）——
        #   fast 那条守卫只能查源码里有没有 `FACM.pools`，**这里才是真判据**：
        #   屏幕上真的列出了 n 个池子。
        pv = pg.eval_on_selector_all('#facp option', 'a=>a.map(x=>x.value)')
        assert pv == [x['key'] for x in meta['pools']], \
            '池子清单对不上：%s vs %s' % (pv, [x['key'] for x in meta['pools']])
        assert len(pv) >= 5, '池子太少，上面那条等于空转：%d' % len(pv)
        assert 'all' in pv, '全市场（现状那一份）不见了 —— 它是别的池唯一的对照基准'
        notes.append('清单 %d 区间 / %d 前瞻 / %d 族 / %d 池'
                     % (len(wl), len(hl), len(gv), len(pv)))

        # ---- ② 三条口径【原样】印出来 ----
        body = pg.inner_text('#pg')
        for c in meta['caveats']:
            key = c[:14]
            assert key in body, '口径没印出来：%s…' % key
        # 文案里不许有 markdown 星号（HTML 渲染不了，原样显示给用户）
        assert '**' not in body, '页面上有 markdown 星号'

        # ---- ③ 量纲不可比的默认不列，而且要【说出来】 ----
        n0 = pg.eval_on_selector_all('table.lvt tbody tr', 'a=>a.length')
        assert meta['n_abs'] > 0, '构造不成立：没有任何横截面不可比的因子'
        import re as _re
        assert _re.search(r'另有\s*%d\s*个' % meta['n_abs'], body), \
            '隐藏了 %d 个却没在页面上说' % meta['n_abs']
        pg.check('#facab')
        pg.wait_for_selector('table.lvt tbody tr', timeout=60000)
        n1 = pg.eval_on_selector_all('table.lvt tbody tr', 'a=>a.length')
        assert n1 > n0, '勾上"含量纲"之后行数没变多：%d -> %d' % (n0, n1)
        notes.append('默认 %d 行、含量纲 %d 行（藏了 %d）' % (n0, n1, meta['n_abs']))

        # ---- ③b 缓存必须真的攒住：bug 的指纹就是"只剩 1 项" ----
        #   🔴 判据用**缓存里有几项**，不用耗时 —— 时间判据在机器忙时会偶发。
        #     第一版 `_summary` 每次 miss 就 clear()，于是 6 个区间逐个
        #     miss 逐个清空，缓存最多留 1 项 -> 每次进详情重算 6 遍（2.7 秒）。
        fsrv._SUM.clear()
        fsrv.api_factor({'id': 'arbr', 'h': '20'})
        nwin = len(meta['windows'])
        assert len(fsrv._SUM) >= nwin, \
            '详情页算了 %d 个区间，缓存里却只有 %d 项 —— 缓存没攒住，' \
            '每次进详情都重算一遍' % (nwin, len(fsrv._SUM))
        notes.append('缓存攒住 %d/%d 个区间' % (len(fsrv._SUM) - 1, nwin))

        # ---- ④ 点名称进详情：公式 / 各区间 / 逐年，一块都不许少 ----
        pg.goto(U + '/factors.html', wait_until='networkidle')
        pg.wait_for_selector('table.lvt tbody tr td.tx a', timeout=60000)
        fid = pg.eval_on_selector('table.lvt tbody tr td.tx a',
                                  'a=>new URL(a.href).searchParams.get("id")')
        pg.click('table.lvt tbody tr td.tx a')
        pg.wait_for_selector('.fcode', timeout=60000)
        d0 = pg.inner_text('#pg')
        assert pg.eval_on_selector('.fcode', 'e=>e.textContent.trim().length') > 3, \
            '详情页没有公式'
        assert '各区间的表现' in d0 and '逐年表现' in d0, '详情页缺块'
        assert '**' not in d0 and 'undefined' not in d0 and 'NaN' not in d0, \
            '详情页有星号/undefined/NaN'
        # id 要进 URL —— 这一页的地址是拿去分享的
        assert 'id=' + fid in pg.url, '详情没进 URL：%s' % pg.url

        # 逐年表要有 IR（用户："逐年表现只有 ic，没有 ir"），
        # 且**与主表同口径** —— 独立算一遍比，不拿被测对象的输出当期望
        import pandas as _pd
        d = fsrv.api_factor({'id': fid, 'h': 20})
        assert d['years'] and all('ir' in y for y in d['years']), '逐年没给 IR'
        # 🔴 期望要从**接口读的那同一批数据**算，而不是那个扁平
        #   `factor_ic.parquet` —— 分池改造（2026-09-24）之后正本是
        #   `factors/ic/<池>/<年>.parquet` 分片，扁平那份**停在 09-23
        #   再没人写过**，于是判据拿 3 天前的数去比今天的接口，
        #   报出来像"IR 口径不一致"（实测踩过）。
        #   ★ 仍然是**独立**的：这里自己按 pandas 算 mean/std，
        #     不拿接口的输出当期望。
        _ic = fe._load_shards('ic', 'all')
        _ic = _ic[(_ic['factor_id'] == fid) & (_ic['h'] == 20)]
        _ic = _ic.assign(y=_pd.to_datetime(_ic['date']).dt.year)
        for y in d['years']:
            sub = _ic[_ic['y'] == y['year']]['ic']
            want = sub.mean() / sub.std()
            assert abs(y['ir'] - want) < 1e-9, \
                '%d 年 IR 对不上：%s vs %s（口径要与主表一致）' % (
                    y['year'], y['ir'], want)
        ths = pg.eval_on_selector_all(
            '#pg table.lvt:last-of-type thead th', 'a=>a.map(x=>x.textContent.trim())')
        assert 'IR' in ths, '逐年表的表头里没有 IR：%s' % ths
        notes.append('逐年 %d 年带 IR 且与主表同口径' % len(d['years']))

        # t 那一列要在页面上说清楚（用户问过「最后一列的 t 是什么意思」），
        # 而且要说出**这张表按它排序** —— 只放在列头 tooltip 里等于没说
        for kw in ('重叠', '不用来', '按 |t| 降序排'):
            assert kw in d0, 't 的说明缺「%s」' % kw
        notes.append('详情 %s：2 张表' % fid)

        # ---- ⑤ 入口：主入口要【内容区顶部 + 首屏可见】的按钮 ----
        #   两版都被用户当场指出来，判据因此要钉【两件事】，缺一不可：
        #     v1 埋在目录树下面的灰字 note 里 -> "点击回测看不到"
        #        （树几百行，要滚到底）-> 判据①：首屏可见 + 手型光标
        #     v2 挪进顶栏 pageHead 的 extra -> "怎么暴露在最外面？"
        #        （顶栏是全局导航，域内子页塞进去层级就乱）
        #        -> 判据②：**不许在 #top 里**
        #   ★ 只钉①的话它会飘上顶栏、只钉②的话它会沉回树底 —— 分工别记反。
        for url, nm in [('/#/runs', '回测归档'), ('/#/picks', '选中的规则')]:
            p2 = br.new_page(viewport={'width': 1440, 'height': 900})
            p2.goto(U + url, wait_until='networkidle')
            try:
                p2.wait_for_selector('#facbar a[href="/factors.html"]', timeout=25000)
            except Exception:
                raise AssertionError(
                    '%s（%s）的内容区顶部没有因子广场按钮 —— 埋在正文说明里'
                    '等于没有入口' % (nm, url))
            # ② 不许飘到顶栏（全局导航区只放那 7 个域入口）
            # 🔴 **反向自证**：顶栏得真的渲染了，否则这条在"顶栏整个空掉"
            #   时也成立 —— 而我就这么干过一次（块替换把 `pageHead(...)`
            #   整段删了，node --check 通过、这条用例全绿、截图还看着正常）。
            #   同「整段替换代码时夹在中间的东西会被一起删掉」那条。
            top = p2.inner_text('#top')
            assert 'assay' in top and '回测' in top, \
                '%s：顶栏没渲染（pageHead 没被调？）—— 那下面那条"不许飘到' \
                '顶栏"就是空转：%r' % (nm, top[:80])
            if nm == '回测归档':
                for want in ('全部展开', '★ 选中的规则'):
                    assert want in top, '顶栏少了这一页专属的控件：%s' % want
            assert not p2.query_selector('#top a[href="/factors.html"]'), \
                '%s：因子广场跑到【顶栏】去了 —— 它是回测的子页，' \
                '不该与实盘/买点/盘面那排全局入口并列' % nm
            g = p2.eval_on_selector('#facbar a[href="/factors.html"]', '''e=>{
                const r = e.getBoundingClientRect(), c = getComputedStyle(e);
                return {top: r.top, h: r.height, w: r.width, cur: c.cursor};
            }''')
            # ① 可量的视觉事实：首屏之内 + 看得出能点
            assert 0 <= g['top'] < 900 and g['h'] > 8 and g['w'] > 8, \
                '%s 的因子广场入口不在首屏或量不出来：%s' % (nm, g)
            assert g['cur'] == 'pointer', '%s 的入口没有手型光标' % nm
            p2.click('#facbar a[href="/factors.html"]')
            p2.wait_for_selector('table.lvt tbody tr', timeout=60000)
            assert p2.eval_on_selector_all('table.lvt tbody tr', 'a=>a.length') > 0, \
                '%s 点过去是空的' % nm
            p2.close()
        notes.append('内容区入口 2 处首屏可见且不在顶栏')

        # 交叉引用三处：点得通即可
        for url, nm in [('/', '首页参考'), ('/#/sync', '数据同步'),
                        ('/indicators.html', '指标广场')]:
            p2 = br.new_page(viewport={'width': 1440, 'height': 900})
            p2.goto(U + url, wait_until='networkidle')
            try:
                p2.wait_for_selector('a[href="/factors.html"]', timeout=25000)
            except Exception:
                raise AssertionError(
                    '%s（%s）上没有因子广场入口 —— 从这儿走不到那一页，'
                    '而那不报错，只是从此没人找得到' % (nm, url))
            p2.click('a[href="/factors.html"]')
            p2.wait_for_selector('table.lvt tbody tr', timeout=60000)
            assert p2.eval_on_selector_all('table.lvt tbody tr', 'a=>a.length') > 0, \
                '%s 点过去是空的' % nm
            p2.close()
        notes.append('交叉引用 3 处全点得通')

        # ---- ⑥ 两个广场互相指得到，且各自说清"我不是另一个" ----
        pg.goto(U + '/factors.html', wait_until='networkidle')
        pg.wait_for_selector('a[href="/indicators.html"]', timeout=30000)
        assert '时间序列' in pg.inner_text('#pg'), '因子广场没说清"我不是指标广场"'
        p3 = br.new_page()
        p3.goto(U + '/indicators.html', wait_until='networkidle')
        p3.wait_for_selector('a[href="/factors.html"]', timeout=30000)
        t3 = p3.inner_text('#pg')
        assert '横截面' in t3, '指标广场没说清"我不是因子广场"'
        p3.close()
        br.close()
    return ' · '.join(notes)


@case('因子：算不出来的那些也要有名有姓，且"做不了"与"还没写"不许混成一句', tag='web')
def t_factor_missing():
    """原清单 278 个名字，广场只列得出有值的那些 —— 剩下的此前**在页面上
    根本不存在**，于是「我要的那个因子呢」没有任何地方答得了。

    🔴 **四件事都不报错**，所以四条各钉各的：

    | 坏法 | 表现 |
    |---|---|
    | 清单手工维护、与注册表分叉 | 实现了却忘了从 MISSING 删 -> 两边都列；原清单加一行 -> 它**静默地哪一半都不在** |
    | 覆盖自证挪到写盘之后 | 对不上照样把表写出去了，页面上是一份缺了角的清单 |
    | "做不了"与"还没写"混成一句 | 65 个只差有人去写的因子被读成"这东西算不了" |
    | 入口断链 / 画成标签 | 从广场走不到这份清单，**而那不报错，只是从此没人找得到** |

    ★ 计数一律与**注册表/接口**比，不写死 117/65 —— 实现一个因子那两个数
      就变，写死的话下次是断言在说谎（同「数字自己算」那条）。
    """
    import sys as _sys

    dlb = os.path.join(os.path.dirname(REPO), 'datalake', 'build')
    if dlb not in _sys.path:
        _sys.path.insert(0, dlb)
    import factors as fac
    import pandas as pd

    notes = []

    # ---- ① 覆盖自证：两半必须正好分完原清单 ----------------------
    xlsx = os.path.join(os.path.dirname(REPO), 'factors.xlsx')
    assert os.path.isfile(xlsx), '构造不对：没有 factors.xlsx，这条判据没法跑'
    names = list(dict.fromkeys(
        pd.read_excel(xlsx)['因子名称'].astype(str).str.strip()))
    ok, msg = fac.covered_by(names)
    assert ok, '因子清单两半对不上 —— %s' % msg
    notes.append(msg)

    # 🔴 **反向自证**：三种烂法各报各的。`covered_by` 恒返回 True 的话
    #   上面那条就是空转，而那正是"清单慢慢烂掉"的样子。
    impl = sorted(s.name_cn for s in fac.all_specs())
    miss = [r['name_cn'] for r in fac.all_missing()]
    bad = {}
    bad['neither'] = fac.covered_by(names + ['__原清单新加的一行__'])
    bad['extra'] = fac.covered_by([n for n in names if n != miss[0]])
    _save = fac.MISSING
    try:
        fac.MISSING = _save + ((impl[0], 'todo'),)
        bad['both'] = fac.covered_by(names)
    finally:
        fac.MISSING = _save
    for k, (o2, m2) in bad.items():
        assert not o2, '覆盖自证放过了「%s」—— 它等于没有' % k
    assert '哪一半都不在' in bad['neither'][1], \
        '「原清单加了一行」没被点名：%s' % bad['neither'][1]
    assert '原清单里没有' in bad['extra'][1], \
        '「MISSING 里有而原清单没有」没被点名：%s' % bad['extra'][1]
    assert '又列在 MISSING' in bad['both'][1], \
        '「实现了却还留在 MISSING 里」没被点名：%s' % bad['both'][1]
    assert fac.covered_by(names)[0], '还原之后覆盖自证必须回到真'
    notes.append('三种烂法各报各的')

    # ---- ② 每条都有原因，且两档都非空 ---------------------------
    rows = fac.all_missing()
    assert rows, '构造不对：MISSING 是空的'
    for r in rows:
        assert r['reason_key'] in fac.MISS_REASONS, \
            '%s 的原因不认识：%s' % (r['name_cn'], r['reason_key'])
        assert r['reason_cn'] and r['reason_why'], \
            '%s 没有原因文案' % r['name_cn']
        for f in ('name_cn', 'reason_cn', 'reason_why', 'detail'):
            assert '**' not in (r[f] or ''), \
                '%s 的 %s 里有 markdown 星号（页面上会原样显示）' % (r['name_cn'], f)
    nb = sum(1 for r in rows if r['blocked'])
    nt = len(rows) - nb
    # 反向自证：两档都得有货，否则"分两档"那几条断言在空转
    assert nb and nt, '构造不对：做不了 %d / 还没写 %d —— 有一档是空的' % (nb, nt)

    # ---- ③ 建目录表时【先自证再写盘】，不过就拒绝写出 -------------
    #   判据走 AST 比**真实语句的行号**，不查字符串 ——
    #   查字符串会命中我自己写的注释（这条本项目踩过四次）。
    import ast
    bp = os.path.join(dlb, 'build_factor_catalog.py')
    tree = ast.parse(io.open(bp, encoding='utf-8').read())
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == 'main')
    chk = [n.lineno for n in ast.walk(fn)
           if isinstance(n, ast.Call) and getattr(n.func, 'id', '') == 'covered_by']
    ret2 = [n.lineno for n in ast.walk(fn) if isinstance(n, ast.Return)
            and isinstance(n.value, ast.Constant) and n.value.value == 2]
    wr = [n.lineno for n in ast.walk(fn) if isinstance(n, ast.Call)
          and getattr(n.func, 'attr', '') == 'to_parquet']
    assert chk, 'build_factor_catalog.main() 里没有覆盖自证'
    assert ret2, '覆盖自证不过时没有 return 2 —— 那就是"报了一句然后照样写出去"'
    assert wr, '构造不对：main() 里找不到 to_parquet'
    assert max(ret2) < min(wr), \
        '覆盖自证的 return 2 排在写盘之后（第 %s 行 vs %s 行）—— ' \
        '对不上照样把表写出去了，而它只在日志里说了一句' % (ret2, wr)
    notes.append('先自证再写盘')

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return ' · '.join(notes) + '（页面部分跳过：无 playwright）'

    import shutil
    import tempfile
    import threading
    from http.server import ThreadingHTTPServer
    from assay import factor_missing as fm
    from assay import server as sv
    from assay.srv import factors as fsrv

    # 🔴 **selftest 不许写生产账本**（同 `lv.LIVE` 那条）。顺带这也让计数
    #   变成可控的：手工那半为空，下面那些"页面几行 vs 注册表几条"的判据
    #   才不依赖"真账本里恰好没有手工条目"（同「判据不许依赖真实数据碰巧
    #   如此」那条）。
    _live0, _allow0 = fm.LIVE, sv.ALLOW_LIVE
    _tmpdir = tempfile.mkdtemp(prefix='factor_miss_')
    fm.LIVE = _tmpdir
    sv.ALLOW_LIVE = True

    # 🔴 一路 try/finally —— 断言失败时也得把 `fm.LIVE` 与
    #   `sv.ALLOW_LIVE` 还回去。不还的话**后面的用例**跑在一个
    #   临时账本与"可写"模式上，而那不报错（同「变异的还原被
    #   __pycache__ 静默吃掉」那类：污染留在了别人身上）。
    try:
        api = fsrv.api_factors_missing({})
        assert api['n'] == len(rows), '接口给的条数与注册表对不上：%d vs %d' % (
            api['n'], len(rows))

        httpd = ThreadingHTTPServer(('127.0.0.1', 0), sv.Handler)
        port = httpd.server_address[1]
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        U = 'http://127.0.0.1:%d' % port

        with sync_playwright() as pw:
            br = pw.chromium.launch()
            pg = br.new_page(viewport={'width': 1440, 'height': 900})
            errs = []
            pg.on('pageerror', lambda e: errs.append(str(e)))

            # ---- ④ 入口：首屏【看得见】且【看得出能点】-----------------
            #   判据取可量的视觉事实（可见 / 有边框 / 手型光标），不是查 class ——
            #   后者在样式被改暗时照样命中（同因子广场入口那条）。
            pg.goto(U + '/factors.html', wait_until='networkidle')
            pg.wait_for_selector('table.lvt tbody tr', timeout=60000)
            sel = '#pg a[href*="missing=1"]'
            el = pg.query_selector(sel)
            assert el, '因子广场上没有「算不出来的」入口 —— 而那不报错，' \
                       '只是那 %d 个因子从此没人找得到' % api['n']
            g = pg.eval_on_selector(sel, '''e=>{const r=e.getBoundingClientRect(),
                s=getComputedStyle(e);return {top:r.top,w:r.width,
                cur:s.cursor,bw:parseFloat(s.borderTopWidth)};}''')
            assert g['w'] > 0 and 0 <= g['top'] < 900, \
                '「算不出来的」入口不在首屏：%s' % g
            assert g['cur'] == 'pointer' and g['bw'] >= 1, \
                '入口被画成了标签（光标 %s / 边框 %s）—— 一个能点的东西看着' \
                '不像能点，等于没有入口' % (g['cur'], g['bw'])
            # 🔴 光验"有边框 + 手型光标"**不够**：`.lvtag` 两样都有，
            #   改成 .lvtag 照样全绿（变异实测漏过一次）。真正要证的是
            #   「它与旁边那些标签长得不一样」—— 拿同一页上一个真 .lvtag 去比。
            _sty = ('e=>{const s=getComputedStyle(e);'
                   'return [s.color,s.fontSize,s.paddingLeft,s.fontFamily];}')
            a1 = pg.eval_on_selector(sel, _sty)
            a2 = pg.eval_on_selector('#pg a[data-nav="win"]', _sty)
            assert a1 != a2, \
                '「算不出来的」入口与旁边的标签长得一模一样（%s）—— ' \
                '一个能点的东西被画成了标签，本项目犯过两次' % a1
            assert str(api['n']) in pg.inner_text(sel), \
                '入口上没写清有几个 —— 人不知道值不值得点进去'

            # ---- ⑤ 那一页：照服务端清单渲染，两档分得开 ---------------
            pg.goto(U + '/factors.html?missing=1', wait_until='networkidle')
            pg.wait_for_selector('tr.grp', timeout=60000)
            assert not errs, '页面报错：%s' % errs
            nrow = pg.eval_on_selector_all('table.lvt tbody tr:not(.grp)', 'a=>a.length')
            assert nrow == api['n'], '页面列了 %d 行，服务端说有 %d 个' % (nrow, api['n'])
            # 带子行【逐条】对服务端，不是"有几条" —— 只比个数的话，
            # 页面自己拼一套原因文案也算通过
            got = pg.eval_on_selector_all(
                'tr.grp .gwhy', 'a=>a.map(x=>x.textContent.trim())')
            want = [r['why'] for r in api['reasons']]
            assert got == want, '原因文案不是服务端那份：\n页面 %s\n服务端 %s' % (
                got[:2], want[:2])
            # 🔴 两档必须【看得出来是两档】：判据是可量的颜色，不是有没有那个 class
            cb = pg.eval_on_selector_all(
                'tr.grp .mblk', 'a=>a.map(x=>getComputedStyle(x).color)')
            ct = pg.eval_on_selector_all(
                'tr.grp .mtodo', 'a=>a.map(x=>getComputedStyle(x).color)')
            # 🔴 期望从**注册表**算，不从 `api['reasons']` 算 —— 后者是被测
            #   对象自己的输出，服务端把 blocked 一律设成 True 时它跟着变，
            #   于是两边永远相等、判据空转（变异实测漏过一次）。
            kb = {r['reason_key'] for r in rows if r['blocked']}
            kt = {r['reason_key'] for r in rows} - kb
            gb, gt = len(kb), len(kt)
            assert (len(cb), len(ct)) == (gb, gt), \
                '两档的【组数】对不上：页面 %d/%d，服务端 %d/%d' % (
                    len(cb), len(ct), gb, gt)
            assert (api['n_blocked'], api['n_todo']) == (nb, nt), \
                '两档的【条数】接口与注册表对不上：%d/%d vs %d/%d' % (
                    api['n_blocked'], api['n_todo'], nb, nt)
            assert set(cb) & set(ct) == set(), \
                '「做不了」与「还没写」是同一个颜色（%s）—— 揉成一句的话，' \
                '%d 个只差有人去写的因子会被读成"这东西算不了"' % (cb[:1], nt)
            txt = pg.inner_text('#pg')
            assert '不是算不出来' in txt, \
                '页面没说清最后那一档不是"算不出来" —— 那就是在说谎'

            # ---- ⑥ 搜不到时要分得出「没这个名字」和「它算不出来」------
            #   两个分支**各构造一次**：只测一头的话，"永远说算不出来"或
            #   "永远说没有"都全绿（同「判据两个方向都要」那条）。
            hit = rows[0]['name_cn']
            pg.goto(U + '/factors.html?q=' + hit, wait_until='networkidle')
            pg.wait_for_function(
                "()=>document.querySelector('#pg').innerText.includes('没有匹配')",
                timeout=30000)
            t1 = [x for x in pg.inner_text('#pg').split('\n') if '没有匹配' in x][0]
            # 🔴 判据要认准**分得出两个分支**的那句话。第一版查的是
            #   「'算不出来' in t1」—— 而"也不在那 N 个算不出来的里面"这句
            #   **也含这四个字**，于是两个分支都命中，判据空转（变异实测）。
            assert '对得上' in t1 and hit in t1, \
                '搜一个【算不出来】的因子，页面没点名它在那份清单里 —— ' \
                '人会以为自己名字打错了：%s' % t1
            assert '也不在' not in t1, '两个分支的话混在一起了：%s' % t1
            pg.goto(U + '/factors.html?q=zzz-%E6%B2%A1%E8%BF%99%E4%B8%AA-zzz',
                    wait_until='networkidle')
            pg.wait_for_function(
                "()=>document.querySelector('#pg').innerText.includes('没有匹配')",
                timeout=30000)
            t2 = [x for x in pg.inner_text('#pg').split('\n') if '没有匹配' in x][0]
            assert '也不在' in t2 and '对得上' not in t2, \
                '两边都没有时页面没说"也不在那份清单里" —— ' \
                '那句话才把"打错了"与"算不出来"分开：%s' % t2
            assert not errs, '页面报错：%s' % errs

            # ---- ⑦ 手工加一条：名字撞了要【说清撞在哪一半】--------------
            #   静默覆盖的话，注册表里那条逐条定案的原因被一句手写的话盖住，
            #   而页面上看不出是哪一条（同「锚点必须命中恰好一次，否则抛」）。
            pg.on('dialog', lambda d: d.accept())
            pg.goto(U + '/factors.html?missing=1', wait_until='networkidle')
            try:
                pg.wait_for_selector('#mfopen', timeout=30000)
            except Exception:
                raise AssertionError(
                    '这一页上没有「手工加一条」的入口 —— 那份清单就只能看、'
                    '加不进去；而它不报错，只是从此没人往里记东西')
            eb = pg.eval_on_selector('#mfopen', 'e=>e.getBoundingClientRect().top')
            assert 0 <= eb < 900, \
                '「手工加一条」不在首屏（top=%s）—— %d 行的表，摆在表尾等于' \
                '要滚到底才看得见（同因子广场入口沉在归档树底下那次）' % (eb, api['n'])
            pg.click('#mfopen')
            pg.wait_for_selector('#mfn', timeout=15000)
            impl0 = sorted(x.name_cn for x in fac.all_specs())[0]
            for nm, want in ((impl0, '已经算得出来'),
                             (rows[0]['name_cn'], '已经在注册表')):
                pg.fill('#mfn', nm)
                pg.fill('#mfd', '测试')
                pg.click('#mfok')
                try:
                    pg.wait_for_selector('#mfmsg.warn', timeout=15000)
                except Exception:
                    raise AssertionError(
                        '撞名的「%s」被【静默加进去了】—— 注册表里那条逐条'
                        '定案的原因会被一句手写的话盖住，而页面上看不出'
                        '是哪一条' % nm)
                got = pg.inner_text('#mfmsg')
                assert want in got, \
                    '加一条撞名的「%s」，报错没说清撞在哪一半：%s' % (nm, got)

            NEW = '自检·测试因子'
            pg.fill('#mfn', NEW)
            pg.fill('#mfd', '这一条是自检加的')
            pg.click('#mfok')
            pg.wait_for_function(
                "n=>document.querySelector('#pg').innerText.includes(n)",
                arg=NEW, timeout=20000)
            cur = fm.current()
            assert [r['name_cn'] for r in cur] == [NEW], \
                '账本里没有刚加的那条：%s' % cur
            n2 = pg.eval_on_selector_all('table.lvt tbody tr:not(.grp)', 'a=>a.length')
            assert n2 == api['n'] + 1, '加完页面是 %d 行，应该是 %d' % (n2, api['n'] + 1)
            dels = pg.eval_on_selector_all('a.mdel', 'a=>a.map(x=>x.dataset.n)')
            assert dels == [NEW], \
                '删除按钮不是【只给手工那条】：%s —— 注册表里的是代码，' \
                '摆一个点了必然报错的 × 比不摆更糟' % dels[:5]

            pg.click('a.mdel')
            pg.wait_for_function(
                "n=>!document.querySelector('#pg').innerText.includes(n)",
                arg=NEW, timeout=20000)
            assert fm.current() == [], '删完账本里还有：%s' % fm.current()
            assert len(fm.log()) == 2, \
                '账本不是 append-only（该是 add + remove 两条，实得 %d）' % len(fm.log())
            assert not errs, '页面报错：%s' % errs
            br.close()
    finally:
        fm.LIVE, sv.ALLOW_LIVE = _live0, _allow0
        shutil.rmtree(_tmpdir, ignore_errors=True)

    notes.append('页面 %d 行 / %d 组，两档分得开；手工加/删走通' % (
        nrow, len(api['reasons'])))
    return ' · '.join(notes)


@case('因子详情页那六张图：真画出来了 / 净值给的是净值 / 口径说得清', tag='web')
def t_factor_charts():
    """详情页六张图（2026-09-25）。**五种坏法都不报错**，所以逐条钉：

    | 坏法 | 表现 |
    |---|---|
    | 页面没引 `chart.js` / `stockpop.js` | `lineChart is not defined` 只在控制台里报，**页面照样渲染、只是那几块空着**（实测第一版就是 `spLink is not defined`，把「最大最小 20 只」整块打没了） |
    | 服务端给"净值 − 1"而不是净值 | 对数轴那条路对负值做 clamp（压到 1e-6），**而图看着完全正常** |
    | 详情页漏传 `pool` | 在沪深300 上点进因子，表格与图是**全市场**的数，而选择器还写着沪深300 —— 两个池长得一模一样 |
    | 行业被归成 11 个大类 | 本地只有申万一级；归成 11 个必然掺进"谁定的口径"，而页面上看不出来 |
    | 「衰减」被读成持仓重合度 | 别的看板那张图是 0.93~0.99（1 − 累计换手），这里是分位超额（千分之几）—— 混着读会得出相反结论 |
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return '跳过（无 playwright）'
    import threading
    from http.server import ThreadingHTTPServer
    from assay import server as sv
    import assay.factor_eval as fe
    from assay.srv import factors as sf

    # ---- ① 服务端：净值必须是【净值】（恒 > 0），不是"净值 − 1" ----
    o = sf.api_factor_charts({'id': 'mv', 'pool': 'all', 'h': '5', 'win': '1y'})
    assert not o.get('error'), o.get('error')
    allv = [v for x in o['nav']['series'] for v in x['nav'] if v is not None]
    assert allv and min(allv) > 0, (
        '净值序列里有 <= 0 的值（min=%s）—— 服务端给的多半是"净值 − 1"，'
        '而对数轴会把负值 clamp 到 1e-6，图看着完全正常' % (min(allv) if allv else None))
    assert len(o['nav']['series']) == o['nav']['nq'] + 1, (
        '净值应该是 %d 个分位 + 1 条基准，实际 %d 条'
        % (o['nav']['nq'], len(o['nav']['series'])))

    # ---- ② 衰减是【分位超额】不是持仓重合度 ----
    ex = [v for x in o['decay']['series'] for v in x['ex'] if v is not None]
    assert ex and max(abs(v) for v in ex) < 0.1, (
        '衰减的量级是 %.3f —— 0.9 那个量级是【持仓重合度】(1 − 累计换手)，'
        '不是分位超额。两者都叫"衰减"，混了会得出相反的结论'
        % max(abs(v) for v in ex))
    assert '持仓重合度' in o['caveats']['decay'], '口径里没把这两件事分开说'

    # ---- ③ 行业 IC 是申万一级，没有被归成 11 个大类 ----
    n1y = len(sf.api_factor_charts({'id': 'mv', 'h': '5', 'win': '1y'})['ind'])
    o2 = sf.api_factor_charts({'id': 'mv', 'h': '5', 'win': 'all'})
    nall = len(o2['ind'])
    assert n1y >= 28, '近 1 年只有 %d 个行业 —— 申万一级该是 31 个左右' % n1y
    assert nall > n1y, (
        '全程 %d 个、近 1 年 %d 个 —— 行业名是 PIT 的（申万 2014/2021 改过版），'
        '全程口径下必然多出已停用的名字；一样多说明那一层被写死了' % (nall, n1y))

    # ---- ④ 页面：六块都真的画出来，且【一条控制台异常都没有】----
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), sv.Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    notes = []
    try:
        with sync_playwright() as pw:
            br = pw.chromium.launch()
            pg = br.new_page(viewport={'width': 1440, 'height': 1200})
            errs = []
            pg.on('pageerror', lambda e: errs.append(str(e)))
            pg.goto('http://127.0.0.1:%d/factors.html?id=mv&h=5&win=1y' % port,
                    wait_until='networkidle')
            try:
                pg.wait_for_selector('#ch_tb table', timeout=90000)
            except Exception:
                raise AssertionError(
                    '六张图没画全 —— 控制台异常 %s ｜ #facch 开头「%s」。'
                    '这一类的指纹就是"页面照样渲染、只是那几块空着"'
                    % (errs or '无', (pg.inner_text('#facch')[:60]
                                      if pg.query_selector('#facch') else '没有容器')))
            n = lambda sel: pg.eval_on_selector_all(sel, 'a=>a.length')
            got = {'净值': n('#ch_nav svg path'), 'IC': n('#ch_ic svg path'),
                   '行业': n('#ch_ind svg rect'), '换手': n('#ch_turn svg path'),
                   '衰减': n('#ch_dec svg rect'), '最大最小': n('#ch_tb table tr')}
            for k, v in got.items():
                assert v > 0, '「%s」那块一个图元都没有（%s）' % (k, got)
            assert got['净值'] == o['nav']['nq'] + 1, (
                '净值画了 %d 条，服务端给了 %d 条' % (got['净值'], o['nav']['nq'] + 1))
            assert got['最大最小'] == len(o['top_bottom']['top']) + \
                len(o['top_bottom']['bottom']), '最大/最小两张表行数对不上'
            assert not errs, '控制台有异常：%s' % errs[:2]

            # 「全分位」关掉之后只剩 两端 + 基准 —— 只查"有这个按钮"的话，
            # 点了不起作用照样绿
            pg.click('#navctl a[data-allq]')
            pg.wait_for_timeout(400)
            n3 = n('#ch_nav svg path')
            assert n3 == 3, '关掉「全分位」应只剩 3 条（两端 + 基准），实际 %d' % n3

            # ⑤ 详情页必须带着池子 —— 这是修掉的一个现网 bug
            hit = []
            pg.on('request', lambda r: hit.append(r.url) if '/api/factor?' in r.url else None)
            pg.goto('http://127.0.0.1:%d/factors.html?id=mv&h=5&win=1y&pool=hs300' % port,
                    wait_until='networkidle')
            pg.wait_for_timeout(1200)
            assert hit and 'pool=hs300' in hit[0], (
                '详情页那一发没带 pool（%s）—— 在沪深300 上点进因子会看到'
                '【全市场】的数，而选择器还写着沪深300' % (hit[:1] or '一发都没有'))
            notes.append('池子带过去了')

            # ---- ⑥ 行业下钻：点进去的均值必须与柱子【逐位】相同 ----
            # 🔴 判据要落在一个**换过行业**的行业上。行业名是 PIT 的，
            #   而先按行业过滤再算 `lead` 会在换过行业的票的日期序列里挖出
            #   断档 -> "未来 h 日收益"跨了几年。实测美容护理I −0.0836 vs
            #   −0.0834 对不上，而**银行I 恰好一致**（银行几乎不换行业）——
            #   只拿它自证会漏过（这一轮就是这么漏的）。
            # ★ 构造：拿"全程天数最少"的那个当迁入样本（新设行业的票都是
            #   迁进来的），再拿"天数最多"的当稳定对照；两个都必须逐位相同。
            pg.goto('http://127.0.0.1:%d/factors.html?id=mv&h=5&win=all' % port,
                    wait_until='networkidle')
            pg.wait_for_selector('#ch_ind svg rect.hbr', timeout=90000)
            nb = n('#ch_ind .hbr')
            assert nb == len(o2['ind']), (
                '条形图 %d 行、服务端给了 %d 个行业' % (nb, len(o2['ind'])))
            cur = pg.eval_on_selector('#ch_ind .hbr', 'e=>getComputedStyle(e).cursor')
            assert cur == 'pointer', (
                '行业那几行看不出能点（cursor=%s）—— 看不出能点的入口 = 没有入口' % cur)
            byday = sorted(o2['ind'], key=lambda x: x['nday'])
            probe = [byday[0]['ind'], byday[-1]['ind']]
            assert byday[0]['nday'] * 2 < byday[-1]['nday'], (
                '天数最少 %d / 最多 %d —— 差得不够开，挑不出"迁入"那种行业，'
                '这条判据会退化成只验稳定行业（而那正是漏过的原因）'
                % (byday[0]['nday'], byday[-1]['nday']))
            for ind in probe:
                one = sf.api_factor_industry(
                    {'id': 'mv', 'h': '5', 'win': 'all', 'ind': ind})
                assert not one.get('error'), one.get('error')
                bar = [x for x in o2['ind'] if x['ind'] == ind][0]
                assert abs(one['ic_mean'] - bar['ic']) < 1e-12, (
                    '「%s」柱子 %.6f vs 点进去 %.6f —— 两处不同源。'
                    '多半是先按行业过滤再算 lead：行业是 PIT 的，'
                    '换过行业的票会被挖出断档，lead 就跨过去了'
                    % (ind, bar['ic'], one['ic_mean']))
                assert one['nday'] == bar['nday'], (
                    '「%s」天数 %d vs %d' % (ind, bar['nday'], one['nday']))
                assert one['years'], '「%s」没有逐年' % ind
                y0 = one['years'][-1]
                for k in ('ic', 'ir', 'ret_lo', 'ret_hi', 'ret_eq'):
                    assert k in y0, '逐年缺 %s' % k
            notes.append('行业下钻与柱子逐位同源（%s）' % '/'.join(probe))

            # 点开真的出得来（只验接口的话，"点了没反应"照样绿）
            pg.click('#ch_ind .hbr')
            pg.wait_for_selector('#ch_ind1 table', timeout=60000)
            assert n('#ind1ic svg path') == 2, '下钻的 IC 图没画出 IC + 均线两条'
            assert n('#ch_ind1 table tbody tr') > 0, '下钻的逐年表一行都没有'
            assert not errs, '控制台有异常：%s' % errs[:2]
            br.close()
    finally:
        httpd.shutdown()
    return ('六块都画出来（%s）｜净值恒 > 0 ｜衰减量级 %.4f（不是重合度）｜'
            '行业 全程 %d / 近1年 %d ｜%s'
            % ('/'.join('%s=%d' % (k, v) for k, v in got.items()),
               max(abs(v) for v in ex), nall, n1y, '、'.join(notes)))


@case('因子「横截面可不可比」：判据是单位表，而单位要分得开价格与金额', tag='fast')
def t_factor_xs_units():
    """🔴🔴 用户 2026-09-24 问：「为什么『市值』『6日成交金额的移动平均值』
    『20日资金流量』『流通市值』这些因子横截面不可比？」—— **他是对的，
    那 6 个（连同 `a_ma20` 共 7 个规模/流动性因子）被误判了。**

    原来的判据是 `ABS_UNITS = ('元','股','元/天')` **一刀切**，把 72 个判成
    不可比。它是从一条**真实发现**推出来的：`ma20` 这类后复权价格均线，
    横截面排序排的是「股价 × 上市以来分红拆细」（`hfq_factor` 跨票
    1.00~5899.9）。发现没错，**按单位外推就推过头了** ——

        元(价格)  股价与派生量      标度是股本与拆股史 -> 任意 -> 真不可比
        股        成交量水平        标度是股本         -> 任意 -> 真不可比
        元/天     回归斜率          标度是股价         -> 任意 -> 真不可比
        ───────────────────────────────────────────────────────────
        元(金额)  市值/成交额/财务   **元是全市场共同标度** -> 可比

    🔴 **自相矛盾的铁证（不用测数据就成立）**：`ln_mv = log(totalmv)` 判为
      可比、`mv = totalmv` 判为不可比 —— log 单调、这里算的是**秩相关**，
      同一个排序判成两档。而 `ln_mv` 自己的描述里白纸黑字写着「取对数
      **不改变序**，所以它与市值的横截面 IC 完全相同」——
      **代码在和自己的注释打架**（本项目记过的老毛病）。
    🔴 代价是可量的：`froec` 的第三层就是**按流通市值升序取 10**、
      ETF-P1 的池子就是**成交额前 40**，而这两个因子在广场上**默认看不见**
      （同「合并的风险不是少两个按钮，是把功能藏起来」）。

    判据五段，**分工别记反**：

        A 单位表是唯一判据，且**未登记的单位拒绝注册**（新因子写错会响亮失败）
        B 粗单位 `元` 不许回来 —— 它同时装着"价格水平"与"经济规模"两种东西
        C 两档都非空且分对：`元(金额)` 全可比 / `元(价格)` 全不可比
        D 目录表与注册表**逐行**一致（改了单位忘了重建 catalog = 页面上是旧分档）
        E 单调变换对（`log(X)` 与 `X`）必须同档 —— 正是上面那个矛盾

    ⚠ E **不能**用"IC 逐位相同"去判：实测 `mv` 与 `ln_mv` 的 13 项里只有
      6 项逐位相同，其余 7 项差 1e-9~3e-6 —— float64 把两个极近的 `totalmv`
      的 log 压成相等，在并列处翻了几下。所以 E 钉的是**公式的形状**。
    """
    import sys as _sys
    dl = os.path.join(os.path.dirname(REPO), 'datalake')
    bd = os.path.join(dl, 'build')
    if bd not in _sys.path:
        _sys.path.insert(0, bd)
    for m in [k for k in list(_sys.modules) if k == 'factors' or k.startswith('factors.')]:
        del _sys.modules[m]
    import factors as _F                          # noqa: E402
    specs = _F.all_specs()

    # ---- A 单位表是唯一判据；未登记的单位必须被拒 ----
    assert isinstance(_F.UNITS, dict) and _F.UNITS, '没有 UNITS 表'
    unk = sorted({s.unit for s in specs} - set(_F.UNITS))
    assert not unk, '这些单位没在 UNITS 里登记：%r' % unk
    for s in specs:
        assert s.xs_comparable == _F.UNITS[s.unit], \
            '%s 的可比性没有照单位表走' % s.id
    try:
        _F.Spec('_probe', '探针', 'ma', 'x', '', '升', ('close_hfq',), 1,
                lambda x: None)
    except AssertionError as e:
        assert '未登记的单位' in str(e), '拒了，但报错指不到原因：%s' % e
    else:
        raise AssertionError(
            '未登记的单位居然注册成功了 —— 新因子写个没见过的单位会被'
            '**静默**归档，而"归错档"正是这条用例存在的理由')

    # ---- B 粗单位 `元` 不许回来 ----
    assert '元' not in _F.UNITS, \
        ('`元` 又回到单位表里了 —— 它同时装着「价格水平」与「经济规模」，'
         '正是 2026-09-24 那次误判 35 个因子的根')
    assert not [s.id for s in specs if s.unit == '元'], \
        '还有因子在用粗单位 `元`：%r' % [s.id for s in specs if s.unit == '元'][:5]

    # ---- C 两档都非空、且分对 ----
    money = [s for s in specs if s.unit == '元(金额)']
    price = [s for s in specs if s.unit == '元(价格)']
    assert money and price, \
        '构造不对：元(金额) %d 个 / 元(价格) %d 个 —— 有一档是空的，判据空转' \
        % (len(money), len(price))
    bad = [s.id for s in money if not s.xs_comparable]
    assert not bad, '元(金额) 里有被判成不可比的：%r' % bad[:5]
    bad = [s.id for s in price if s.xs_comparable]
    assert not bad, '元(价格) 里有被判成可比的：%r' % bad[:5]
    # 规模 / 流动性那一批必须在「可比」这边（用户点名的就是它们）；
    # 而价格水平那批必须仍在「不可比」那边 —— **两头都钉，少一头就往另一边飘**。
    # ★ `ln_mv` **故意不在这串里** —— 它归 E 段（单调变换对）管。
    #   写进来的话 M7 那条变异会被这里抓掉，E 就永远测不到，
    #   而"哪条判据在起作用"说不清（同「别把功劳记在错的那句上」）。
    for fid in ('mv', 'mv_float', 'a_ma6', 'a_ma20', 'a_std6', 'a_std20',
                'mf_sum20'):
        assert _F.by_id(fid).xs_comparable, \
            '%s 被判成横截面不可比 —— 元是全市场共同标度，它可比' % fid
    for fid in ('ma20', 'ema20', 'boll_up', 'close_bfq', 'atr14',
                'v_ema5', 'slope6'):
        assert not _F.by_id(fid).xs_comparable, \
            ('%s 被判成可比 —— 它的标度由个股自己决定（股价 / 股本），'
             '"排第几"排的是量纲' % fid)

    # ---- D 目录表与注册表逐行一致（不只是 id 集合）----
    import pandas as _pd
    cat_p = os.path.join(dl, 'mart', 'factor_catalog.parquet')
    assert os.path.isfile(cat_p), '没有目录表 —— 跑 build_factor_catalog.py'
    cat = _pd.read_parquet(cat_p).set_index('factor_id')
    diff = [(s.id, s.unit, cat.at[s.id, 'unit'],
             s.xs_comparable, bool(cat.at[s.id, 'xs_comparable']))
            for s in specs if s.id in cat.index
            and (s.unit != cat.at[s.id, 'unit']
                 or s.xs_comparable != bool(cat.at[s.id, 'xs_comparable']))]
    assert not diff, \
        ('目录表与注册表对不上 %d 条（头一条 %r）—— `_spec_sig` 只盖'
         '「公式/预热/依赖」，**改了单位它不会变**，于是页面上还是旧分档'
         '而没有任何地方报错。跑一次 build_factor_catalog.py' % (len(diff), diff[0]))

    # ---- E 单调变换对必须同档 ----
    byf = {s.formula: s for s in specs}
    pairs = [(s, byf['log(%s)' % s.formula]) for s in specs
             if 'log(%s)' % s.formula in byf]
    assert pairs, \
        '构造不对：一对「log(X) 与 X」都找不到，这条判据是空转的'
    for a, b in pairs:
        assert a.xs_comparable == b.xs_comparable, \
            ('%s(%s) 与 %s(%s) 是同一个量的单调变换，而广场算的是**秩相关** '
             '—— 两者是同一个排序，不许判成两档（2026-09-24 就是这么把 mv '
             '判成不可比、ln_mv 判成可比的）' % (a.id, a.unit, b.id, b.unit))

    n_abs = sum(1 for s in specs if not s.xs_comparable)
    return ('%d 个因子：单位表 %d 档是唯一判据、未登记单位被拒；'
            '粗单位 `元` 已拆成 元(价格)%d / 元(金额)%d；'
            '目录表与注册表逐行一致；单调变换对 %d 组同档；'
            '横截面不可比 %d 个（此前一刀切是 72 个）'
            % (len(specs), len(_F.UNITS), len(price), len(money),
               len(pairs), n_abs))


@case('因子分池：增量判据挂在【面板】上，IC 与换手盯的年份不同', tag='fast')
def t_factor_pools():
    """分池评价（2026-09-24）。**五件事都不报错**，所以五条都要单独钉：

    | 坏法 | 表现 |
    |---|---|
    | 判据挂在**因子文件** mtime 上 | `build_factor_daily` 是 all-or-nothing，24 个年文件每天全被重写 -> 天天说"全脏" -> 增量退化成全量，每天白烧半小时 |
    | IC 不盯 **Y+1** | 年末那 20 天的 IC 在次年数据到齐后不会被重算 —— 那几天永远缺 |
    | 换手也盯 Y+1 | 白陪跑一年（它结构上不依赖未来：只比相邻两个调仓日） |
    | 加一列 / 改口径而不动指纹 | 旧分片仍判"干净"，页面读到**旧口径** |
    | `ntile` 没有 tie-break | 并列项谁在前取决于**物理行序**，换一种取数写法分位成分就变 |

    🔴 最后一条是**既有缺陷**，这一轮才发现：A-A 实测同一份代码跑两次，
      `q_lo` 2747 行不同（最大 1.87e-2）、`turn` 1902 行不同（最大 0.244）。
      实测 `aroon_up` 某日 5092 行只有 26 个不同取值、56%% 都是 0.0。
    """
    import glob
    import shutil
    import tempfile
    import assay.factor_eval as fe

    # ---- ① 清单是正本：页面里不许出现任何一个池子名 ----
    src = io.open(os.path.join(REPO, 'web', 'factors.html'), encoding='utf-8').read()
    for k, label, col, nq, mn in fe.POOLS:
        if k == 'all':
            continue
        assert ("'%s'" % k) not in src and ('"%s"' % k) not in src, (
            '页面里写死了池子 key %s —— 清单必须由服务端给，'
            '否则加一个池子页面上不会出现（而那不报错）' % k)
        assert label not in src, '页面里写死了池子名 %s' % label
    # 🔴 判据要**切到那个函数里**查 —— 只查全文有没有 `FACM.pools` 的话，
    #   `facPm()` 里也有一处，把选择器改成 `[].map(...)` 照样命中
    #   （变异 M10 实测漏过：**判据比要证的事宽**）。
    ib = src.index('function facBarHtml(')
    jb = src.index('\nfunction ', ib + 10)
    assert 'FACM.pools' in src[ib:jb], (
        '池子选择器不是照服务端那份清单渲染的 —— 加一个池子页面上不会出现，'
        '而那不报错')

    # ---- ② 判据的分工：面板 vs 因子文件、IC vs 换手 ----
    pm = fe.pool_meta('all')
    py = {2023: 'a', 2024: 'b', 2025: 'c', 2026: 'd'}
    base_ic = fe._dep_sig(py, 2024, pm, 'S1', 'ic')
    base_tn = fe._dep_sig(py, 2024, pm, 'S1', 'turn')
    py2 = dict(py); py2[2025] = 'c!'                # 动 Y+1（年份是 int，
    #   不能走 `dict(py, **{...})` —— `**` 的键必须是字符串）
    assert fe._dep_sig(py2, 2024, pm, 'S1', 'ic') != base_ic, (
        'IC 的依赖没盯 Y+1 —— 年末那 20 天在次年数据到齐后不会重算，'
        '那几天永远缺，而它不报错')
    assert fe._dep_sig(py2, 2024, pm, 'S1', 'turn') == base_tn, (
        '换手盯了 Y+1 —— 它结构上不依赖未来（只比相邻两个调仓日），'
        '盯了就是每天白陪跑一年。**两者的分工别记反**')
    for y in (2023, 2024):                          # Y-1 / Y 两个都要盯
        p3 = dict(py); p3[y] = py[y] + '!'
        assert fe._dep_sig(p3, 2024, pm, 'S1', 'ic') != base_ic
        assert fe._dep_sig(p3, 2024, pm, 'S1', 'turn') != base_tn
    assert fe._dep_sig(py, 2024, pm, 'S2', 'ic') != base_ic, (
        '因子口径 spec_sig 没进指纹 —— 改一条 Spec 的公式之后评价不会重算')
    pm5 = fe.pool_meta('sz50')
    assert fe._dep_sig(py, 2024, pm5, 'S1', 'ic') != base_ic, '池口径没进指纹'

    # 🔴🔴 **判据不许挂在因子文件上** —— 构造：只动一个因子年文件的 mtime，
    #   `plan()` 给出的"要重建哪几片"必须**一个字不变**。
    #   ⚠ 第一版我把这条写成了 `x == x`（两边是同一个表达式）——
    #     **空转而且看着绿**，正是「判据拿被测对象当期望」的极端形式。
    panel, fglob, _, _, _ = fe._paths()
    fs = sorted(glob.glob(fglob))
    assert fs, '构造不对：本地没有因子面板'
    pf = sorted(glob.glob(panel))
    assert pf, '构造不对：本地没有面板'
    # ★ 用 `plan` 给的**指纹表**而不是"要重建哪几片" —— 后者在首建时
    #   **已经饱和**（全都要建），动什么都加不出差别，那条判据就是空转。
    #   （第一版就是这么写的，反向自证当场把它抓了出来。）
    before = fe.plan(pools=['all'])[1]['want']
    st0 = os.stat(fs[-1])
    os.utime(fs[-1], (st0.st_atime, st0.st_mtime + 7))
    try:
        after = fe.plan(pools=['all'])[1]['want']
        assert after == before, (
            '动一下因子年文件的 mtime，%d 片的依赖指纹就变了（共 %d 片）—— '
            '判据挂在因子文件上了。`build_factor_daily` 是 all-or-nothing，'
            '24 个年文件每天全被重写，这等于天天全量'
            % (sum(1 for k in before if after.get(k) != before[k]), len(before)))
    finally:
        os.utime(fs[-1], (st0.st_atime, st0.st_mtime))
    # 反向自证：动**面板**年文件，plan 必须跟着变（否则上面那条是空转）
    st1 = os.stat(pf[-1])
    os.utime(pf[-1], (st1.st_atime, st1.st_mtime + 7))
    try:
        moved = fe.plan(pools=['all'])[1]['want']
        assert moved != before, (
            '动了面板年文件而 plan 一点没变 —— 那上面那条"因子文件不算数"'
            '就是空转的（面板变了本来就该重算）')
    finally:
        os.utime(pf[-1], (st1.st_atime, st1.st_mtime))

    # ---- ③ 分片格式版本进指纹 ----
    old = fe.SCHEMA_VER
    try:
        fe.SCHEMA_VER = old + 1
        assert fe._dep_sig(py, 2024, pm, 'S1', 'ic') != base_ic, (
            'SCHEMA_VER 没进指纹 —— 加一列 / 改一处 SQL 口径之后旧分片仍判'
            '"干净"，页面读到的是旧口径，而它不报错')
    finally:
        fe.SCHEMA_VER = old

    # ---- ④ ntile 必须有 tie-break：**构造**大面积并列，两种物理行序 ----
    def _q(rev):
        con = fe._con(threads=4)
        codes = ['%06d.XSHE' % i for i in range(40)]
        rows = [(c, '2024-01-02', 'f', 0.0 if i < 30 else float(i))
                for i, c in enumerate(codes)]
        if rev:
            rows = rows[::-1]
        con.execute('CREATE TABLE lng(jq_code VARCHAR, date DATE,'
                    ' factor_id VARCHAR, val DOUBLE)')
        con.executemany('INSERT INTO lng VALUES (?,?,?,?)', rows)
        fw = [(c, '2024-01-02') + tuple([float(i) * 0.01] * len(fe.HORIZONS))
              + tuple([i + 1] * len(fe.HORIZONS)) for i, c in enumerate(codes)]
        cols = ', '.join('f%d DOUBLE' % h for h in fe.HORIZONS)
        rks = ', '.join('r%d BIGINT' % h for h in fe.HORIZONS)
        con.execute('CREATE TABLE fwd(jq_code VARCHAR, date DATE, %s, %s)' % (cols, rks))
        con.executemany('INSERT INTO fwd VALUES (%s)'
                        % ','.join(['?'] * (2 + 2 * len(fe.HORIZONS))), fw)
        d = fe._year_ic(con, fe.HORIZONS[0], 10, 1)
        con.close()
        return float(d['q_lo'].iloc[0]), float(d['q_hi'].iloc[0])
    a, b = _q(False), _q(True)
    assert a == b, ('`ntile` 没有 tie-break：同一批数据换个物理行序，分位'
                    '成分就变了（%s vs %s）。实测现网 A-A 跑两次 q_lo 有 '
                    '2747 行不同、turn 有 1902 行不同' % (a, b))

    # ---- ⑤ 小池子用**自己的**分位数 ----
    assert pm5['nq'] != 10 and pm5['min_xs'] != 100, (
        '上证50 该有自己的分位口径 —— 当日成分最少 36 只，照 10 组切每组'
        '只有 3~5 只，分位收益是噪声')
    fs = io.open(os.path.join(REPO, 'assay', 'factor_eval.py'), encoding='utf-8').read()
    i = fs.index('def summary(')
    j = fs.index('\ndef ', i + 10)
    assert "piv.get(pm['nq'])" in fs[i:j], (
        "summary 里换手那一列写死了 NQ —— 小池子只有 5 组，`piv.get(10)` "
        '是个**空列**，页面上那一格永远是"—"，而它不报错')
    # ---- ⑥ 分片落盘的 tmp 名必须【唯一】（这条链上没有锁） ----
    #   写同一批分片的进程有两个来源：sync_daily.sh 的 13/13、以及人手工跑
    #   一次 --build-only。固定 tmp 名时两个进程会交错写进同一个 tmp，
    #   再把一个中间是垃圾的文件 rename 就位 —— 与 2026-09-07 realtime
    #   那次同一种坏法，**而它不报错**（下次读那片才抛）。
    iw = fs.index('def _write(')
    jw = fs.index('\ndef ', iw + 10)
    body = fs[iw:jw]
    assert "path + '.tmp'" not in body, (
        '分片的 tmp 名是固定的（path + .tmp）—— 两个进程同时写同一片会'
        '交错写进同一个 tmp，rename 就位的是坏文件，而它不报错')
    assert 'os.getpid()' in body and 'uuid' in body, (
        'tmp 名里要带 pid + uuid 才跨进程唯一（同 realtime._tmp_path）')
    assert 'os.replace(' in body, 'rename 就位那一步不能丢 —— 否则半截文件会被读到'
    # 真跑一次：两份不同内容并发写同一片，最后那片必须是【完整的】其中一份
    import pandas as _pd, threading as _th
    _td = tempfile.mkdtemp(prefix='_shard_')
    try:
        _dst = os.path.join(_td, 'x.parquet')
        _err = []

        def _w(n):
            try:
                fe._write(_pd.DataFrame({'a': list(range(n))}), _dst)
            except BaseException as e:      # noqa
                _err.append(e)
        _ts = [_th.Thread(target=_w, args=(k,)) for k in (2000, 3000, 4000)]
        for t in _ts:
            t.start()
        for t in _ts:
            t.join()
        assert not _err, '并发写报错了：%r' % (_err[:1],)
        _got = len(_pd.read_parquet(_dst))
        assert _got in (2000, 3000, 4000), (
            '并发写之后那片是 %d 行 —— 不是任何一份的完整内容，说明 tmp 被'
            '交错写了' % _got)
        assert not [x for x in os.listdir(_td) if x.endswith('.tmp')], \
            '留下了 .tmp 垃圾'
    finally:
        shutil.rmtree(_td, ignore_errors=True)

    return ('池子 %d 个 ｜ 判据分工 IC 盯 Y+1 / 换手不盯 ｜ tie-break 可复现'
            ' ｜ 分片 tmp 名带 pid+uuid（3 线程并发写，落地 %d 行是完整的一份）'
            % (len(fe.POOLS), _got))


@case('数据装配：阶段清单是正本 / 空 lake 也能起服务 / 缺数据的报错要指得到下一步', tag='fast')
def t_setup_stages():
    """用户："没有数据也要能启动 server，然后点击数据加载开始同步数据，
    需要兼容 Windows 和 Mac。"

    🔴 本机数据是**齐的**，所以"还没装"那一半在真实数据上一步都走不到 ——
      判据必须**构造**一个空 lake（同「真实数据触发不到的上限，判据必须
      能构造出来」「断言要在能触发的构造上跑」）。
    """
    import importlib
    import tempfile
    from assay import server as sv
    from assay.srv import sync as sysrv, base as sbase

    dl = sbase._datalake_dir()
    sys.path.insert(0, dl) if dl not in sys.path else None
    ss = importlib.import_module('setup_stages')
    importlib.reload(ss)

    # ── A 阶段清单只有一份，页面里不许写死 ─────────────────────
    _prev = sbase.ALLOW_LIVE
    sbase.ALLOW_LIVE = True
    try:
        api = sv.ROUTES['/api/setup'](None)
    finally:
        sbase.ALLOW_LIVE = _prev
    assert not api.get('error'), api
    ids_api = [s['id'] for s in api['stages']]
    ids_src = [s['id'] for s in ss.stages()]
    assert ids_api == ids_src, (ids_api, ids_src)
    assert len(ids_src) >= 5, ids_src
    js = io.open(os.path.join(REPO, 'web', 'views', 'sync.js'),
                 encoding='utf-8').read()
    # 🔴 判据比"有没有提到"窄一档：阶段【名】与它的【命令】都不许出现在
    #   页面里。写死的话加一个阶段页面上不会有它，**而那不报错**。
    for s in ss.stages():
        assert s['name'] not in js, '页面写死了阶段名：%s' % s['name']
        for part in (s.get('cmd') or []):
            b = os.path.basename(str(part))
            if b.endswith('.py'):
                assert b not in js, '页面写死了阶段命令：%s' % b

    # ── B 构造一个**全空**的 lake：那才是 Windows 刚 clone 下来的样子 ──
    td = tempfile.mkdtemp(prefix='_emptylake_')
    try:
        empty = os.path.join(td, 'datalake')
        os.makedirs(empty)
        d = ss.summary(empty, td)
        assert d['ready'] is False and d['n_todo'] >= 4, d
        # 🔴 逐个钉，不只钉总数：空目录上**凡是能自动跑的阶段一个都不许是 ok**
        #   （只钉 n_todo >= 4 的话，某一个阶段判错照样绿 —— 变异实测漏过）
        auto = [x for x in d['stages'] if x.get('cmd')]
        assert auto and all(x['state'] == 'todo' for x in auto), \
            [(x['id'], x['state']) for x in auto]
        todo = [s for s in d['stages'] if s['state'] == 'todo']
        assert d['next'] == todo[0]['id'], (d['next'], todo[0]['id'])
        # 反向自证：本机这一份必须**不是**空的，否则上面那段在比两个空壳
        assert ss.summary()['n_todo'] < d['n_todo'], '构造不对：本机也空？'
        # 每条 todo 都要说清"还缺什么"与"大概多久" —— 只说"没有"等于没说
        for s in d['stages']:
            assert s.get('detail'), s
            assert s.get('eta'), s
            assert s.get('why'), s
    finally:
        shutil.rmtree(td, ignore_errors=True)

    # ── C 只认清单里的 id；人工那条不给跑；只读模式拒 ───────────
    _prev = sbase.ALLOW_LIVE
    try:
        sbase.ALLOW_LIVE = True
        r = sysrv.api_setup_run(None, {'stage': '../../etc/passwd'})
        assert r.get('error') and '没有这个阶段' in r['error'], r
        man = [s for s in ss.stages() if not s.get('cmd')]
        assert man, '构造不对：没有"人工"那一档，下面这条判据空转'
        r = sysrv.api_setup_run(None, {'stage': man[0]['id']})
        assert r.get('error') and '人工' in r['error'], r
        sbase.ALLOW_LIVE = False
        r = sysrv.api_setup_run(None, {'stage': ids_src[0]})
        assert r.get('error') and '只读' in r['error'], r
        # ★ 状态那一发**只读模式也要给** —— 空 lake 上它正是唯一有内容的
        assert not sv.ROUTES['/api/setup'](None).get('error')
        assert sv.ROUTES['/api/setup'](None)['can_run'] is False
    finally:
        sbase.ALLOW_LIVE = _prev

    # ── D 缺数据的报错要指得到下一步，而**真 bug 必须原样透传** ────
    root = os.path.abspath(sbase._datalake_dir())
    miss = sv._explain_err(Exception(
        'IOException: IO Error: No files found that match the pattern '
        '"%s/mart/panel_daily/panel_*.parquet"' % root))
    assert miss.get('no_data') is True, miss
    assert '数据' in miss['error'] and '#/sync' == miss['next'], miss
    # 两头夹。🔴 **两道护栏（"是不是缺文件" / "在不在 lake 里"）会互相遮蔽**，
    #   所以两条构造要各自只触发一道，否则变异测不出哪道在起作用
    #   （同「段落往上找那两道边界」那条 —— 实测第一轮就这么漏了两条）。
    real = sv._explain_err(KeyError('close_hfq'))
    assert not real.get('no_data') and 'KeyError' in real['error'], real
    # ① 只有【在不在 lake 里】那道能挡：是缺文件，但不在我们的 lake 下
    out = sv._explain_err(Exception(
        "FileNotFoundError: No such file or directory: '/etc/nope.parquet'"))
    assert not out.get('no_data'), out
    # ② 只有【是不是缺文件】那道能挡：路径就在 lake 里，但它是**坏文件**不是
    #    "还没装" —— 翻译成"去装配"会把 2026-09-07 那种并发写坏 parquet 的
    #    事故说成"你还没建数据"，而那是两件完全不同的事
    #    ★ 路径要**带引号**：不带的话正则抠不出来，会被"抠不出路径就透传"
    #      那道兜住 —— 又是一条只触发到别人的构造（变异实测漏过两轮）
    corrupt = sv._explain_err(Exception(
        'TProtocolException: Invalid data in file "%s/mart/panel_daily/'
        'panel_2026.parquet"' % root))
    assert not corrupt.get('no_data'), corrupt

    # ── E 正本是 .py；`.sh` 只是一行转发（Windows 上没有 bash）────
    dlp = sbase._datalake_dir()
    sh = io.open(os.path.join(dlp, 'sync_daily.sh'), encoding='utf-8').read()
    body = [x for x in sh.splitlines()
            if x.strip() and not x.lstrip().startswith('#')]
    assert any('sync_daily.py' in x for x in body), sh[:400]
    assert len(body) <= 4, '.sh 不再是转发了，%d 行实代码' % len(body)
    src = io.open(os.path.join(REPO, 'assay', 'srv', 'sync.py'),
                  encoding='utf-8').read()
    seg = src[src.index('def api_sync_run'):]
    seg = seg[:seg.index('\ndef ') if '\ndef ' in seg[1:] else len(seg)]
    # 🔴 **先剥注释**：那个函数的注释里就写着「正本本来就在 sync_daily.py」——
    #   查字符串会命中自己写的注释（这是第五次踩，变异 M8 当场漏过）
    code = '\n'.join(x.split('#')[0] for x in seg.splitlines())
    assert 'sync_daily.py' in code and "'bash'" not in code, code[:600]
    assert 'sys.executable' in seg, '走字面量 python3 的话 Windows 上没这个名字'

    # ── F 链里每一步要跑的脚本【必须真的在】 ──────────────────
    # 🔴🔴 这条是被一个**既有缺陷**逼出来的：13/13「因子评价」那一步写的是
    #   `assay/factor_eval.py`，而文件在 `assay/assay/factor_eval.py` ——
    #   **从它接进链那天起一次都没跑成功过**（真跑到那一步的两次全是 ⚠️）。
    #   它是 soft step，所以只在末尾多一行告警，而那行告警天天都在
    #   —— 「天天报的告警等于没有告警」。
    # ★ 判据**不钉那一步**，钉「每一步的脚本参数都解析得到」——
    #   钉具体那一处的话，下次换个步骤写错路径照样不报（同「守卫不要钉
    #   坏成什么样，要钉和正本一不一样」）。
    dry = subprocess.run([sys.executable, os.path.join(dlp, 'sync_daily.py'),
                          '--dry'], capture_output=True, text=True, cwd=dlp)
    assert dry.returncode == 0, dry.stderr[-400:]
    n_script, missing = 0, []
    for m in re.finditer(r'\$ \(cd ([^ ]+) && (.+)\)', dry.stdout):
        cwd, cmd = m.group(1), m.group(2)
        for tok in cmd.split():
            if tok.endswith('.py') or tok.endswith('.sh'):
                n_script += 1
                if not os.path.isfile(os.path.join(cwd, tok)):
                    missing.append(os.path.join(cwd, tok))
    assert n_script >= 8, '只扫到 %d 个脚本参数 —— 这条判据在空转' % n_script
    assert not missing, '链里这些脚本不存在（跑到就失败，而 soft 步只告警）：%s' % missing

    return ('阶段 %d（%s）｜ 空 lake 构造出 %d 步待办 ｜ 缺数据的报错带 next ｜'
            ' 正本 .py、.sh %d 行转发 ｜ 链里 %d 个脚本参数全都解析得到'
            % (len(ids_src), '/'.join(ids_src), 6, len(body), n_script))


@case('首次建库：一个按钮跑到底 / 失败就停 / 齐了入口自己退场', tag='fast')
def t_setup_all():
    """用户："每日同步是系统自动同步，但是首次加载，应该是系统启动后展示一个
    数据初始化的按钮，然后点击按钮开始加载数据。"

    改之前是**七个按钮**、要人自己点六次、还得自己判断上一步跑完没有 ——
    同「靠人记得跑的步骤 = 迟早不跑」。

    🔴 用例全程跑在**假的阶段模块 + 临时 `progress.DIR`** 上：真链要跑
      45 分钟，而且它写的是 launchd 与页面共用的真目录（同 `lv.LIVE` 那条）。
    """
    import importlib
    import tempfile
    import time as _t
    from assay import server as sv
    from assay.srv import base as sbase
    from assay.srv import runs as _rn
    from assay.srv import sync as sy

    dl = sbase._datalake_dir()
    if dl not in sys.path:
        sys.path.insert(0, dl)
    P = importlib.import_module('progress')
    importlib.reload(P)

    td = tempfile.mkdtemp(prefix='_setall_')
    old_dir, old_times = P.DIR, P.TIMES
    old_mod, old_live = sy._stages_mod, sv.ALLOW_LIVE
    P.DIR, P.TIMES = os.path.join(td, 'p'), os.path.join(td, 't.json')
    sv.ALLOW_LIVE = True
    ran, done, fail = [], set(), ['']

    class Fake(object):
        """三步假链：cmd 只是 `python3 -c`，跑完把自己记进 done。"""
        NAMES = ('a', 'b', 'c')

        def stages(self, **kw):
            out = []
            for sid in self.NAMES:
                bad = sid == fail[0]
                out.append({
                    'id': sid, 'name': '阶段' + sid.upper(), 'why': '',
                    'state': 'ok' if sid in done else 'todo',
                    'detail': '', 'eta': '1 秒',
                    'cmd': [sys.executable, '-c',
                            'import sys;sys.exit(%d)' % (1 if bad else 0)]})
            return out

        def summary(self, *a, **k):
            st = self.stages()
            todo = [x for x in st if x['state'] == 'todo']
            return {'stages': st, 'n_todo': len(todo), 'ready': not todo,
                    'next_name': todo[0]['name'] if todo else None,
                    'n_auto_todo': len(todo),
                    'eta_text': '1 秒' if todo else None, 'os': 'T'}

    fake = Fake()
    real_run = _rn._run_job

    def run_job(job_id, cmd, cwd):
        """记下"谁被跑了"，成功就让那一步在下次 stages() 里变 ok。"""
        real_run(job_id, cmd, cwd)
        sid = [x for x in fake.NAMES if x not in done and x != 'DONE']
        ran.append(sid[0] if sid else '?')
        if _rn._JOBS[job_id].get('rc') == 0 and sid:
            done.add(sid[0])
        return _rn._JOBS[job_id].get('rc')

    def wait(jid, sec=30):
        t0 = _t.time()
        while _t.time() - t0 < sec:
            if _rn._JOBS[jid]['state'] != 'running':
                return _rn._JOBS[jid]
            _t.sleep(0.05)
        raise AssertionError('装配任务 %s 跑了 %d 秒还没结束' % (jid, sec))

    def fresh():
        sy._SETUP_SUM['at'] = 0
        sy._SETUP_SUM['d'] = None

    try:
        sy._stages_mod = lambda: (fake, None)
        _rn._run_job = run_job

        # ── A 一个按钮【跑到底】，不是只跑第一步 ──────────────
        r = sy.api_setup_run(None, {'stage': '__all__'})
        assert not r.get('error'), r
        j = wait(r['job_id'])
        assert j['rc'] == 0 and j['state'] == 'done', j
        assert ran == ['a', 'b', 'c'], (
            '点一次只跑了 %s —— 而首次建库是一条有先后依赖的链，'
            '停在第一步等于又把"记得点六次"推回给人' % ran)
        pr = [x for x in P.read() if x['job'] == 'setup'][0]
        assert pr['total'] == 3 and len(pr['done']) == 3, pr
        # 幂等：都齐了再点一次，不该又跑一遍
        fresh()
        r2 = sy.api_setup_run(None, {'stage': '__all__'})
        assert r2.get('error') and '齐' in r2['error'], r2

        # ── B 失败就【停】—— 后面几步吃它的产物 ────────────────
        ran[:] = []
        done.clear()
        fail[0] = 'b'
        fresh()
        r = sy.api_setup_run(None, {'stage': '__all__'})
        j = wait(r['job_id'])
        assert j['rc'] != 0 and j['state'] == 'failed', j
        assert ran == ['a', 'b'], (
            '第二步失败了却还往下跑：%s —— 下游读到只建了一半的上游，'
            '**而那不报错**' % ran)
        assert any('阶段B' in x and '失败' in x for x in j['lines']), \
            '停下来了却没说是哪一步失败的：%s' % j['lines'][-3:]
        pr = [x for x in P.read() if x['job'] == 'setup'][0]
        assert pr['state'] == 'failed' and pr['i'] == 2, pr

        # ── C 每跑完一步【重新查状态】，不照初始清单硬跑 ──────────
        # 构造：跑 a 的时候别人（手工 / 另一台机器）顺带把 c 也建好了。
        ran[:] = []
        done.clear()
        fail[0] = ''

        def run_job2(job_id, cmd, cwd):
            rc = run_job(job_id, cmd, cwd)
            if ran == ['a']:
                done.add('c')       # ← 外部让 c 变成 ok
            return rc

        _rn._run_job = run_job2
        fresh()
        r = sy.api_setup_run(None, {'stage': '__all__'})
        j = wait(r['job_id'])
        _rn._run_job = run_job
        assert j['rc'] == 0, j
        assert 'c' not in ran, (
            '照一开始那份清单硬跑到底了（跑了 %s）—— 判据永远是'
            '"现在磁盘上是什么"，不是进函数那一刻的快照' % ran)

        # ── D 空 lake 时横条【就是那个入口】；齐了自己退场 ────────
        done.clear()
        fresh()
        rows = sy.api_progress(None)['jobs']
        ent = [x for x in rows if x.get('kind') == 'setup_needed']
        assert ent, ('本地没有数据时 /api/progress 不给入口 —— 而新机器上'
                     '第一眼打开的是首页，那等于没有入口')
        assert ent[0]['n_todo'] == 3 and ent[0]['eta_text'], ent[0]
        # 🔴 反向自证：数据齐了就**不许**再给 —— 常驻一条"该装数据了"
        #   等于教人忽略这个位置（同那条横幅纪律）。
        done.update(Fake.NAMES)
        fresh()
        rows = sy.api_progress(None)['jobs']
        assert not [x for x in rows if x.get('kind') == 'setup_needed'], \
            '数据都齐了还挂着"本地还没有数据"'

        # ── E 有任务在跑时不给入口（同屏两句话互相打架）──────────
        done.clear()
        fresh()
        p = P.Progress('setup', '建本地数据（首次批量加载）', 3)
        p.step('阶段A', at=1)
        rows = sy.api_progress(None)['jobs']
        assert not [x for x in rows if x.get('kind') == 'setup_needed'], \
            '已经在跑了还并排挂一条"还没开始"'
        p.finish_step('ok')
        p.finish(0)
    finally:
        sy._stages_mod, sv.ALLOW_LIVE = old_mod, old_live
        _rn._run_job = real_run
        P.DIR, P.TIMES = old_dir, old_times
        fresh()
        for jid in [k for k in _rn._JOBS if k.startswith('setup-')]:
            _rn._JOBS.pop(jid, None)
        shutil.rmtree(td, ignore_errors=True)

    # ── F 装配那两段不许写死阶段名/命令（清单是服务端的） ────────
    # 🔴 判据**只切装配那两段**，不扫整个文件 —— 同一份 sync.js 里
    #   「立即同步」那个按钮的提示里就写着一句"tdx2db cron 要几分钟"，
    #   那说的是**每日同步链**、与装配清单无关。扫全文件会误伤它
    #   （同「判据比要证的事宽」；一个天天误报的检查等于没有检查）。
    # 🔴 比对的清单取自**正本**（真 setup_stages），不在用例里拼一份 ——
    #   拼一份的话加一个阶段它不会跟着变。
    real = importlib.import_module('setup_stages')
    importlib.reload(real)
    words = set()
    for st in real.stages():
        words.add(st['name'])
        for tok in (st.get('cmd') or []):
            b = os.path.basename(str(tok))
            if b.endswith(('.py', '.sh')):
                words.add(b)
    assert len(words) >= 8, '正本给不出阶段名，这条判据会空转'
    segs = []
    js = io.open(os.path.join(REPO, 'web/views/sync.js'), encoding='utf-8').read()
    i, k = js.index('function setupBlock('), js.index('async function showSync(')
    segs.append(('views/sync.js 装配那段', js[i:k]))
    cj = io.open(os.path.join(REPO, 'web/shared/common.js'), encoding='utf-8').read()
    i = cj.index('function prgHtml(')
    segs.append(('common.js 横条', cj[i:cj.index('async function prgScan(')]))
    for tag, seg in segs:
        for w in words:
            assert w not in seg, (
                '%s 里写死了阶段「%s」—— 加一个阶段页面上不会出现，'
                '而那不报错' % (tag, w))

    return ('一键跑完 3/3 且幂等 ｜ 第二步失败就停在那里并点名 ｜ '
            '每步重查状态（外部建好的自动跳过）｜ 空 lake 给入口、'
            '齐了与在跑时都不给 ｜ 页面不写死任何阶段')


@case('数据加载进度：步数只有一处 / 进度来自文件（launchd 也看得到）/ 不猜 ETA', tag='fast')
def t_load_progress():
    """用户："这些数据加载都应该在 web 端最上侧显示……一共多少步，当前多少步，
    每一步的进度，已用多少时间，预计还要多少时间。"

    🔴 用例全程跑在**临时 `progress.DIR`** 上（同 `lv.LIVE` 那条纪律）——
      不重定向的话它会往 `datalake/_manifest/progress/` 里写，而那是
      launchd 与页面共用的真目录。
    """
    import importlib
    import tempfile
    from assay import server as sv
    from assay.srv import base as sbase

    dl = sbase._datalake_dir()
    if dl not in sys.path:
        sys.path.insert(0, dl)
    P = importlib.import_module('progress')
    importlib.reload(P)
    src = io.open(os.path.join(dl, 'sync_daily.py'), encoding='utf-8').read()

    # ── A0 🔴🔴 活性探测不许用 `os.kill(pid, 0)`（跨平台，2026-09-26）──
    #   POSIX 上那是"只探测不发信号"的惯用法；而 **Windows 上 `os.kill`
    #   对任何非 CTRL_* 的 sig 都走 `TerminateProcess`** —— 于是
    #   **每打开一次页面、横条读一次进度，就把正在跑的同步进程杀掉**，
    #   而它不报错（同 serve.py `_alive` 那条，两处要一起改）。
    _psrc = io.open(os.path.join(dl, 'progress.py'), encoding='utf-8').read()
    _av = _psrc[_psrc.index('def _alive'):_psrc.index('class Progress')]
    assert 'os.name' in _av, (
        'progress._alive 没有分平台 —— Windows 上 os.kill(pid,0) 会把'
        '正在跑的同步进程真的杀掉，而那不报错')
    assert 'GetExitCodeProcess' in _av, \
        'progress._alive 的 Windows 分支不是"只查不杀"'
    assert P._alive(os.getpid()) and not P._alive(999999) \
        and not P._alive(None) and not P._alive('x'), \
        'progress._alive 判错：自己/不存在的 pid/None/垃圾 四种都要对'

    # ── A 「一共多少步」只有一处真值 ────────────────────────
    # 🔴 搬运前 `.sh` 里前九步写 `x/12`、后四步写 `x/13` —— 链长了只改新加
    #   那几行，屏幕上的分母自相矛盾**而它不报错**。所以步号必须生成。
    assert re.search(r'^N_STEPS\s*=\s*(\d+)', src, re.M), 'sync_daily 没有 N_STEPS'
    n_decl = int(re.search(r'^N_STEPS\s*=\s*(\d+)', src, re.M).group(1))
    hard = re.findall(r"r\.run(?:_soft)?\(\s*'(\d+)\s*/\s*\d+", src)
    assert not hard, '步号又写死进步名里了：%s' % hard
    dry = subprocess.run([sys.executable, os.path.join(dl, 'sync_daily.py'),
                          '--dry'], capture_output=True, text=True, cwd=dl)
    assert dry.returncode == 0, dry.stderr[-300:]
    num = re.findall(r'─────\s*(\d+)/(\d+)\s', dry.stdout)
    assert num, '--dry 里没有「第几步/共几步」'
    assert [int(a) for a, _ in num] == list(range(1, len(num) + 1)), num
    assert {int(b) for _, b in num} == {n_decl}, '分母不止一个：%s' % num
    assert len(num) == n_decl, '跑了 %d 步而 N_STEPS=%d' % (len(num), n_decl)
    # 跑完必须自证步数（加一步忘改 N_STEPS 就要响亮失败，不是静默错分母）
    assert 'r.n != N_STEPS' in src, 'sync_daily 少了步数自证'
    # 🔴 `--dry` 不许写进度文件 —— 否则页面冒出一条"正在同步"而什么都没跑
    assert 'if job and not dry' in src, '--dry 也会写进度文件'

    td = tempfile.mkdtemp(prefix='_prg_')
    old_dir, old_times = P.DIR, P.TIMES
    P.DIR, P.TIMES = os.path.join(td, 'p'), os.path.join(td, 't.json')
    try:
        # ── B 进度来自【文件】，所以 launchd 跑的也看得见 ──────────
        pg = P.Progress('sync', '每日数据同步', 13)
        pg.step('第三步', at=3)
        _prev = sbase.ALLOW_LIVE
        try:
            sbase.ALLOW_LIVE = False        # 只读模式也要能看进度
            api = sv.ROUTES['/api/progress'](None)
        finally:
            sbase.ALLOW_LIVE = _prev
        assert not api.get('error'), api
        assert api['n'] == 1, api
        r = api['jobs'][0]
        # 反向自证：它**不在** _JOBS 里（正是 launchd 那条路的样子）
        assert not r.get('job_id'), '这条进度不该有 job_id —— 构造没模拟到 launchd'
        assert (r['i'], r['total'], r['state']) == (3, 13, 'running'), r
        assert r['elapsed'] >= 0 and r['step'] == '第三步'

        # ── C 第一次跑【不猜】ETA；跑完一次才有基准 ────────────────
        assert r['eta'] is None, '没有历史却给了一个预计：%r' % r['eta']
        # ★ 要**完整跑一次**才存剖面 —— 半截的剖面会让 ETA 系统性偏小，
        #   而偏小的预计比没有预计更误导。所以这里从第 1 步重新跑满 13 步。
        pgf = P.Progress('sync', '每日数据同步', 13)
        for i in range(1, 14):
            pgf.step('第%d步' % i, at=i)
            pgf.finish_step('ok')
        pgf.finish(0)
        pg2 = P.Progress('sync', '每日数据同步', 13)
        pg2.step('第一步', at=1)
        r2 = [x for x in P.read('sync')][0]
        assert r2['eta'] is not None, '跑完一次之后仍然给不出预计'
        assert r2['eta_from'], '没说这个预计是哪次跑出来的'

        # 🔴 两条链的耗时剖面**不许串** —— 串了那个"还要多久"没有意义。
        #   ★ 构造里这条链的**步数要与 sync 相同**：`read` 另有一道
        #     「剖面形状对不上就不用」的护栏（`len(secs)==total`），
        #     步数不同的话是**那一道**替它挡住了，两道互相遮蔽、
        #     测不出哪道在起作用（变异实测漏过一轮）。
        #   两道的分工：一道管"取**自己这条链**的"，一道管"链加减过步数
        #     之后旧剖面作废"。
        po = P.Progress('setup', '装配', 13)
        po.step('面板', at=4)
        rs = [x for x in P.read('setup')][0]
        assert rs['eta'] is None, 'setup 用了 sync 的耗时剖面：%r' % rs['eta']
        po.finish(1)
        os.remove(os.path.join(P.DIR, 'setup.json'))   # 免得它混进下面的计数

        def _jobs():
            # ★ 按 job 取，不按个数 —— 目录里多一条别的任务时，
            #   "== 1" 会以另一条的名义通过/失败，报错指不到原因
            return {x['job']: x for x in sv.ROUTES['/api/progress'](None)['jobs']}

        # ── D 进程没了 -> stale（不是永远 running）；隔夜的不再常驻 ──
        f = os.path.join(P.DIR, 'sync.json')
        d = json.load(io.open(f, encoding='utf-8'))
        d.update(state='running', pid=999999, step_started=time.time())
        json.dump(d, io.open(f, 'w', encoding='utf-8'))
        assert P.read('sync')[0]['state'] == 'stale', '死进程还报 running'
        assert _jobs().get('sync', {}).get('state') == 'stale', '中断了却不说'
        d['started'] = time.time() - 86400 * 2      # 两天前断的
        json.dump(d, io.open(f, 'w', encoding='utf-8'))
        assert 'sync' not in _jobs(), \
            '隔了两天的中断还挂在最上面 —— 那就是常驻告警'

        # ── E 刚跑完的 90 秒仍然显示（不然人一走开就不知道成没成）──
        d.update(state='done', rc=0, ended=time.time(), started=time.time() - 9)
        json.dump(d, io.open(f, 'w', encoding='utf-8'))
        assert _jobs().get('sync', {}).get('state') == 'done', '跑完当场消失'
        d['ended'] = time.time() - 600
        json.dump(d, io.open(f, 'w', encoding='utf-8'))
        assert 'sync' not in _jobs(), '完成的横条常驻了'
    finally:
        P.DIR, P.TIMES = old_dir, old_times
        shutil.rmtree(td, ignore_errors=True)

    return ('活性探测分平台（Windows 上 os.kill 会杀进程）｜ 链 %d 步（分母只有一处、步号生成）｜ 进度走文件所以 launchd 也可见'
            ' ｜ 第一次不猜 ETA、跑完有基准且两条链不串 ｜ 死进程报中断、'
            '隔夜与完成都自己退场' % n_decl)


@case('数据加载进度横条：在最上侧、每页都有、没任务时整条不渲染（playwright）', tag='web')
def t_load_progress_bar():
    """用户要的是"直接看到进度"，所以判据全是**可量的视觉事实**：
    它在不在最上面、有没有把页面顶掉、步数与时间写没写出来。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return '跳过（无 playwright）'
    import importlib
    import tempfile
    import threading
    from http.server import ThreadingHTTPServer

    from assay import server as sv
    from assay.srv import base as sbase
    from assay.srv import sync as sy

    dl = sbase._datalake_dir()
    if dl not in sys.path:
        sys.path.insert(0, dl)
    P = importlib.import_module('progress')
    importlib.reload(P)

    td = tempfile.mkdtemp(prefix='_prgbar_')
    old_dir, old_times = P.DIR, P.TIMES
    P.DIR, P.TIMES = os.path.join(td, 'p'), os.path.join(td, 't.json')
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), sv.Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    errs, out = [], []
    try:
        with sync_playwright() as pw:
            b = pw.chromium.launch()
            pg = b.new_page(viewport={'width': 1280, 'height': 800})
            pg.on('pageerror', lambda e: errs.append(str(e)))
            # 🔴 **在【独立页面】上验** —— 横条挂在 common.js，而 6 个独立
            #   .html 与 index.html 是两套骨架；只在首页验的话"独立页面上
            #   没有横条"这种坏法抓不到（同炸板浮窗在个股页上验那条）。
            pg.goto('http://127.0.0.1:%d/market.html' % port,
                    wait_until='networkidle')

            # ① 没有任务在跑 -> 整条不渲染，也不许留白
            pg.wait_for_timeout(1500)
            assert pg.locator('#prgbar').count() == 0, \
                '没有任务在跑却挂着横条 —— 常驻一条"一切正常"等于教人忽略这个位置'
            assert pg.evaluate("getComputedStyle(document.body).paddingTop") \
                in ('0px', ''), '没挂横条却留了白'

            # ② 造一个跑到一半的任务（pid 是本进程 -> 活的）
            p = P.Progress('sync', '每日数据同步（增量补全到最新）', 13)
            p.step('面板（本年增量）', at=7)
            try:
                pg.wait_for_selector('#prgbar', timeout=20000)
            except Exception:
                raise AssertionError(
                    '任务在跑，独立页面上却没有进度横条 —— 而那不报错，'
                    '只是从此没人知道数据在不在加载')
            t = ' '.join(pg.inner_text('#prgbar').split())
            geo = pg.evaluate(
                "()=>{const e=document.querySelector('#prgbar'),"
                "f=e.querySelector('.pgfill');"
                "return {top:e.getBoundingClientRect().top,"
                " pad:parseFloat(getComputedStyle(document.body).paddingTop),"
                " h:e.getBoundingClientRect().height,"
                " fw:f?f.getBoundingClientRect().width:0,"
                " bw:e.getBoundingClientRect().width};}")
            assert geo['top'] == 0, '横条不在最上侧：top=%s' % geo['top']
            # 盖住页面第一行的话，顶栏就点不到了（同指数带子那条）
            assert geo['pad'] >= geo['h'] - 1, \
                '横条盖住了页面顶部：留白 %s < 横条高 %s' % (geo['pad'], geo['h'])
            for want in ('第 7 / 13 步', '面板（本年增量）', '已用'):
                assert want in t, '横条上缺「%s」：%s' % (want, t)
            # 第一次跑没有基准 -> **明说未知**，不猜一个数
            assert '剩余未知' in t, '没有历史却给了一个预计：%s' % t
            frac = geo['fw'] / geo['bw']
            # 🔴 钉**精确值**不是"一半左右"：没有历史耗时时条必须停在
            #   **整步边界** (i-1)/total = 6/13。写成 0.4~0.6 的话
            #   "按 i/total 硬算"（7/13 = 0.538）照样落在里面 ——
            #   而那是在编一个看着在走的假进度（变异实测漏过）。
            assert abs(frac - 6 / 13) < 0.012, (
                '第一次跑没有基准，进度条应停在整步边界 %.3f，实际 %.3f'
                ' —— 没有历史却插值 = 编一个假进度' % (6 / 13, frac))
            out.append('%.0f%%' % (frac * 100))

            # ②b 「明细」展开：这条链**每一步**跑得怎么样
            # 用户："点开可以看到其更新到第几步，已使用多长时间，
            #        大约还要多长时间"。
            more = pg.locator('#prgbar .pgmore')
            assert more.count() == 1 and more.is_visible(), \
                '横条上没有「明细」入口 —— 那些数据接口里全都有，' \
                '只是没人看得到'
            assert pg.evaluate(
                "()=>getComputedStyle(document.querySelector"
                "('#prgbar .pgmore')).cursor") == 'pointer', \
                '「明细」看不出能点 —— 看不出能点的入口 = 没有入口'
            _h0 = pg.evaluate(
                "()=>[document.querySelector('#prgbar').getBoundingClientRect()"
                ".height, parseFloat(getComputedStyle(document.body).paddingTop),"
                " document.querySelector('#top')?"
                "document.querySelector('#top').getBoundingClientRect().top:null]")
            more.click()
            try:
                pg.wait_for_selector('#prgbar .pgd', timeout=5000)
            except Exception:
                raise AssertionError('点了「明细」没展开')
            # 🔴🔴 **浮层不许被任何祖先剪掉。** 我把 `overflow:hidden`
            #   从 `#prgbar` 搬到 `.pgrow` 上，而浮层正是 `.pgrow` 的子元素
            #   —— 几何量出来 433×328「可见」，**截图上什么都没有**。
            #   所以判据要问"谁在剪"，不是问"尺寸够不够大"。
            clip = pg.evaluate(
                "()=>{let e=document.querySelector('#prgbar .pgd')"
                ".parentElement,o=[];while(e&&e!==document.documentElement){"
                "if(getComputedStyle(e).overflow!=='visible')"
                "o.push(e.id||e.className||e.tagName);e=e.parentElement;}"
                "return o;}")
            assert not clip, \
                '🔴 明细浮层被祖先剪掉了：%s —— 几何量着"可见"，' \
                '屏幕上一个字都没有' % clip
            # 🔴 展开**不许撑高横条自己**：`body.hasprog` 的 padding-top 是
            #   固定值，横条一变高就盖住顶栏、导航点不到（实测撑到 337px）。
            _h1 = pg.evaluate(
                "()=>[document.querySelector('#prgbar').getBoundingClientRect()"
                ".height, parseFloat(getComputedStyle(document.body).paddingTop),"
                " document.querySelector('#top')?"
                "document.querySelector('#top').getBoundingClientRect().top:null]")
            assert abs(_h1[0] - _h0[0]) < 2, \
                '🔴 展开把横条撑高了 %s -> %s，而 body 留白还是 %s —— ' \
                '它会盖住顶栏' % (_h0[0], _h1[0], _h1[1])
            if _h0[2] is not None:
                assert abs(_h1[2] - _h0[2]) < 2, \
                    '🔴 展开把页面顶栏顶动了：%s -> %s' % (_h0[2], _h1[2])
            rows = pg.eval_on_selector_all(
                '#prgbar .pgdr',
                "es=>es.map(e=>[e.className, e.innerText.replace(/\\n/g,' ')])")
            assert len(rows) == 13, \
                '明细该逐步列出 13 步，实得 %d 行' % len(rows)
            assert 'pgdcur' in rows[6][0], \
                '第 7 步是正在跑的那一步，应高亮：%s' % (rows[6],)
            assert '已用' in rows[6][1], '当前那步没说已用多久：%s' % (rows[6],)
            # 第一次跑没有剖面 -> 未跑那几步**明说不知道**，不编名字
            assert 'pgdq' in rows[12][0] and '还不知道' in rows[12][1], \
                '没有上次剖面时，未跑的步骤该明说不知道：%s' % (rows[12],)
            more.click()
            pg.wait_for_timeout(300)
            assert pg.locator('#prgbar .pgd').count() == 0, '再点一次没收起'
            out.append('明细 13 行')

            # ③ 跑完 -> 不再是"进行中"，但那 90 秒仍然说得出结果
            p.finish_step('ok')
            p.finish(0)
            pg.wait_for_function(
                "()=>{const e=document.querySelector('#prgbar');"
                "return e && e.innerText.indexOf('完成') >= 0;}", timeout=20000)
            assert '⟳' not in pg.inner_text('#prgbar'), '跑完了还在转圈'

            # ── ③b 数据齐了、但定时任务没装 ──────────────────────
            # 用户问「新下载的项目会在什么时候启用定时任务」—— 查下来
            # 装定时的三个入口（--install-timer / 数据页开关 / 改窗口重装）
            # **全要人主动做**，而一键建库跑完不装。于是新机器上七个阶段
            # 全绿、横条自己消失、首页一切正常，**而明天起数据不再更新**。
            # 🔴 判据三条都要，缺一条它就往另一边飘：
            #   ① 没装时要出现  ② 装上了必须消失  ③ 数据没齐时不许出现
            #     （那时该说的是"先把数据建起来"，两条一起出就是自相矛盾）
            _real_auto = sy.autosync_status
            _AU = {'on': False, 'supported': True, 'partial': False,
                   'schedule': '16:00 ~ 20:00 每 10 分钟',
                   'tasks': [{'key': 'sync', 'name': '数据同步', 'on': False,
                              'window_text': '16:00 ~ 20:00 每 10 分钟'},
                             {'key': 'tick', 'name': '信号重算', 'on': False,
                              'window_text': '16:00 ~ 次日 09:20 每 60 分钟'}]}

            class _Ready(object):
                def stages(self, **kw):
                    return [{'id': 'x', 'name': '装第一步', 'why': '',
                             'state': 'ok', 'detail': '', 'eta': '2 分钟',
                             'cmd': [sys.executable, '-c', 'pass']}]

                def summary(self, *a, **k):
                    return {'stages': self.stages(), 'n_todo': 0,
                            'ready': True, 'next_name': None,
                            'n_auto_todo': 0, 'eta_text': None, 'os': 'T'}

            _o_mod, _o_live = sy._stages_mod, sv.ALLOW_LIVE
            try:
                sy._stages_mod = lambda: (_Ready(), None)
                sv.ALLOW_LIVE = True
                sy.autosync_status = lambda: dict(_AU)
                sy._SETUP_SUM['at'] = 0; sy._SETUP_SUM['d'] = None
                sy._AUTO_SUM['at'] = 0; sy._AUTO_SUM['d'] = None
                # ① 没装 -> 横条上要有那个入口
                pg.reload()
                try:
                    pg.wait_for_selector('#prgauto', timeout=20000)
                except Exception:
                    raise AssertionError(
                        '数据齐了而定时没装，横条上却没有「开启自动同步」的'
                        '入口 —— 而那不报错，只是从明天起数据再也不更新')
                _t = ' '.join(pg.inner_text('#prgbar').split())
                # 窗口口径必须来自服务端（前端写死的话改了窗口它不跟着变）
                assert '16:00' in _t and '每 10 分钟' in _t, \
                    '没把定时窗口说出来：%s' % _t[:160]
                assert 'PIT' in _t or '补不回来' in _t, \
                    '没说代价 —— 「漏一天永久丢失」才是该立刻点它的理由：%s' % _t[:160]
                assert pg.evaluate(
                    "()=>getComputedStyle(document.querySelector('#prgauto'))"
                    ".cursor") == 'pointer', '入口看不出能点'
                # ② 装上之后这一行【自己消失】——不常驻
                _AU['on'] = True
                for t in _AU['tasks']:
                    t['on'] = True
                sy._AUTO_SUM['at'] = 0; sy._AUTO_SUM['d'] = None
                pg.reload()
                pg.wait_for_timeout(1200)
                assert pg.locator('#prgauto').count() == 0, \
                    '🔴 已经开着自动同步，横条还挂着「还没开」—— ' \
                    '常驻一条要人做事的横幅等于教人忽略这个位置'
                # ③ 数据没齐时不许出现（那时该说的是"先把数据建起来"）
                _AU['on'] = False
                for t in _AU['tasks']:
                    t['on'] = False
                class _NotReady(object):
                    def stages(self, **kw):
                        return [{'id': 'x', 'name': '装第一步', 'why': '',
                                 'state': 'todo', 'detail': '', 'eta': '2 分钟',
                                 'cmd': [sys.executable, '-c', 'pass']}]

                    def summary(self, *a, **k):
                        return {'stages': self.stages(), 'n_todo': 1,
                                'ready': False, 'next_name': '装第一步',
                                'n_auto_todo': 1, 'eta_text': '2 分钟',
                                'os': 'T'}

                sy._stages_mod = lambda: (_NotReady(), None)
                sy._SETUP_SUM['at'] = 0; sy._SETUP_SUM['d'] = None
                sy._AUTO_SUM['at'] = 0; sy._AUTO_SUM['d'] = None
                pg.reload()
                pg.wait_for_selector('#prgsetup', timeout=20000)
                assert pg.locator('#prgauto').count() == 0, \
                    '🔴 数据都还没有就催人开自动同步 —— 同屏两条自相矛盾'
                out.append('定时入口')
            finally:
                sy.autosync_status = _real_auto
                sy._stages_mod, sv.ALLOW_LIVE = _o_mod, _o_live
                sy._SETUP_SUM['at'] = 0; sy._SETUP_SUM['d'] = None
                sy._AUTO_SUM['at'] = 0; sy._AUTO_SUM['d'] = None

            # ── ④ 本地还没有数据时，横条【就是那个入口】────────────
            # 用户："系统启动后展示一个数据初始化的按钮，点击按钮开始加载。"
            # 🔴 构造成"空 lake"，不能等真机器上恰好没数据
            #   （同「判据不许依赖真实数据碰巧如此」）。
            class _Fake(object):
                def stages(self, **kw):
                    return [{'id': 'x', 'name': '装第一步', 'why': '',
                             'state': 'todo', 'detail': '', 'eta': '2 分钟',
                             'cmd': [sys.executable, '-c', 'pass']}]

                def summary(self, *a, **k):
                    return {'stages': self.stages(), 'n_todo': 1,
                            'ready': False, 'next_name': '装第一步',
                            'n_auto_todo': 1, 'eta_text': '2 分钟', 'os': 'T'}

            _old_mod, _old_live = sy._stages_mod, sv.ALLOW_LIVE
            sy._stages_mod = lambda: (_Fake(), None)
            sv.ALLOW_LIVE = True
            sy._SETUP_SUM['at'] = 0
            sy._SETUP_SUM['d'] = None
            posted = []
            pg.route('**/api/setup/run', lambda r: (
                posted.append(r.request.post_data),
                r.fulfill(status=200, content_type='application/json',
                          body='{"error":"用例拦下了，没真跑"}')))
            try:
                # 换一页进来，确保读的是新的状态而不是内存里那份
                pg.goto('http://127.0.0.1:%d/watchlist.html' % port,
                        wait_until='networkidle')
                try:
                    pg.wait_for_selector('#prgsetup', timeout=20000)
                except Exception:
                    raise AssertionError(
                        '本地还没有数据，横条上却没有"开始建"那个入口 —— '
                        '而新机器上第一眼打开的是首页，那等于没有入口')
                t2 = ' '.join(pg.inner_text('#prgbar').split())
                assert '还差 1 步' in t2 and '2 分钟' in t2, \
                    '入口没说还差几步、大概多久：%s' % t2
                # 🔴 **看得出能点**才算入口（同「一个能点的东西被画成了标签」）
                cur = pg.eval_on_selector('#prgsetup',
                                          "e=>getComputedStyle(e).cursor")
                assert cur == 'pointer', '那个入口的光标是 %s，看不出能点' % cur
                assert pg.locator('#prgsetup').is_visible(), '入口不可见'
                # 🔴 点了要**真的**发起一键建库 —— 只查"有这个按钮"的话，
                #   handler 没绑（内容每轮被 innerHTML 整块换掉！）照样全绿
                pg.click('#prgsetup')
                pg.wait_for_timeout(800)
                assert posted, '点了那个入口却什么请求都没发 —— 点了没反应'
                assert '__all__' in (posted[0] or ''), (
                    '点入口发的不是"一次跑完"：%s —— 只跑第一步等于又把'
                    '"记得点六次"推回给人' % posted[0])

                # 🔴 **第二个入口**：「🔄 数据」页上那个也得点得动。
                #   两处各写一份 handler，漏一处的表现是"点了没反应"
                #   而它不报错（同「两个『绑定策略』入口走同一条链」）。
                posted[:] = []
                pg.goto('http://127.0.0.1:%d/#/sync' % port,
                        wait_until='networkidle')
                try:
                    pg.wait_for_selector('#suall', timeout=20000)
                except Exception:
                    raise AssertionError(
                        '数据页上没有"一次建完"的入口 —— 只剩七个按钮'
                        '要人自己点六次，那正是要改掉的东西')
                pg.click('#suall')
                pg.wait_for_timeout(800)
                assert posted and '__all__' in (posted[0] or ''), \
                    '数据页那个按钮点了没发一键建库：%s' % posted[:1]
            finally:
                pg.unroute('**/api/setup/run')
                sy._stages_mod, sv.ALLOW_LIVE = _old_mod, _old_live
                sy._SETUP_SUM['at'] = 0
                sy._SETUP_SUM['d'] = None

            assert not errs, errs[:3]
            b.close()
    finally:
        httpd.shutdown()
        P.DIR, P.TIMES = old_dir, old_times
        shutil.rmtree(td, ignore_errors=True)
    return ('独立页面上有横条、贴在最上侧且不盖内容（填充 %s）｜ 空闲时整条不渲染'
            ' ｜ 步数/当前步/已用都写出来了、没有基准时明说未知 ｜ 跑完说完成'
            ' ｜ %s：不被祖先剪、不撑高横条、当前步高亮、未跑的明说不知道'
            ' ｜ %s：数据齐了但定时没装才出现，装上就消失，数据没齐时不出'
            ' ｜ 空 lake 时横条上那个入口点得动且发的是一键建库'
            % (out[0], out[1] if len(out) > 1 else '明细',
               out[2] if len(out) > 2 else '定时入口'))
