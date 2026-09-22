# -*- coding: utf-8 -*-
"""看盘：个股 / K 线与指标 / 盘面板块自选 / 买点 / 指标广场

这一份是 `selftest.py` 按**产品域**拆出来的一块（2026-09-17，见
`tests/_base.py` 的说明）。判据、注释、教训一个字都没动 —— 拆的是**归属**，
不是内容。
"""
from tests._base import *          # noqa: F401,F403  框架 + 共用辅助
from tests._base import (CASES, JQ, REPO, case, _run, _pages, _web_files,  # noqa: F401
                         _kset, _kmain, _kfq, _klog, _lp_tab, _via_pop)
import os, re, sys, io, json, glob, time, shutil, subprocess, datetime  # noqa: E401,F401


@case('个股：搜索 / 面板 / K线均线 / 复权 / 财务时序', tag='fast')
def t_stock():
    """服务端那半。★ 重点在【单位】和【复权】—— 这两处错了都不报错。

    · 单位：同一张面板里百分数和小数混着（change_pct/turnover/amplitude 是
      百分数，roe_ttm/rev_yoy 是小数）。错 100 倍不报错，只是数看着不对。
      所以 FIELD_UNIT 是接口的一部分，页面照它渲染而不是看数值大小猜。
    · 复权：区间涨幅一律用后复权 —— 不复权跨除权日会有【假跌幅】。
    """
    from assay import stock as st

    # ---- 1) 代码归一：几种写法都要认，认不出返回 None（不抛错）----
    for raw, want in (('601857', '601857.XSHG'), ('601857.SH', '601857.XSHG'),
                      ('sh601857', '601857.XSHG'), ('601857.XSHG', '601857.XSHG'),
                      ('000001', '000001.XSHE'), ('sz000001', '000001.XSHE'),
                      ('300375.sz', '300375.XSHE')):
        assert st.norm_code(raw) == want, '%r -> %s' % (raw, st.norm_code(raw))
    # ★ 搜索框每敲一个字都会调它，半个代码不是错误 —— 返回 None 而不是抛
    for bad in ('', '60', 'abc', '999999', '601857.HK'):
        assert st.norm_code(bad) is None, '%r 该返回 None' % bad

    # ---- 2) 搜索：代码 / 名称 / 前缀，且按匹配度+市值排 ----
    r = st.search('601857')
    assert r['results'] and r['results'][0]['code'] == '601857.XSHG', \
        '按代码搜没命中：%s' % r['results'][:2]
    r2 = st.search('中国石油')
    assert r2['results'] and r2['results'][0]['code'] == '601857.XSHG', \
        '按名称搜没命中'
    r3 = st.search('sh601857')
    assert r3['results'] and r3['results'][0]['code'] == '601857.XSHG', \
        '带前缀写法搜不到'
    # ★ 名称包含类查询要按流通市值降序 —— 不排序的话搜"银行"第一条是随机的
    #   某只小银行，而人要的通常是大的那个
    rb = st.search('银行')['results']
    assert len(rb) >= 3, '搜"银行"结果太少'
    mv = [x['floatmv'] or 0 for x in rb]
    assert mv == sorted(mv, reverse=True), '同类匹配没按市值降序：%s' % mv[:5]
    assert st.search('')['results'] == [], '空查询应返回空，不是全表'

    # ---- 3) 个股面板 ----
    p = st.profile('601857.SH')
    for k in ('sec_name', 'close_bfq', 'open', 'high', 'low', 'preclose',
              'change_pct', 'turnover', 'amplitude', 'floatmv', 'totalmv',
              'pe_ttm', 'pb', 'roe_ttm', 'sw_l1_name', 'limit_up', 'limit_down',
              'listed_days', 'high_52w', 'low_52w'):
        assert k in p, '面板缺字段 %s' % k
    assert p['indexes'], '中国石油应该在指数里：%s' % p['indexes']
    assert p['low_52w'] <= p['close_bfq'] <= p['high_52w'], \
        '现价不在 52 周区间内：%s ~ %s vs %s' % (p['low_52w'], p['high_52w'],
                                          p['close_bfq'])
    # 🔴 单位自证：change_pct/turnover/amplitude 是【百分数】。
    #    当小数用会差 100 倍，而那不报错。用量级钉住。
    assert st.FIELD_UNIT['change_pct'] == 'pct'
    assert st.FIELD_UNIT['turnover'] == 'pct'
    assert st.FIELD_UNIT['roe_ttm'] == 'ratio'
    assert abs(p['change_pct']) < 25, \
        'change_pct 量级不像百分数：%s' % p['change_pct']
    # turnover = 量×收盘价/流通市值×100（实测与字段精确吻合）
    tv = p['volume_shares'] * p['close_bfq'] / p['floatmv'] * 100
    assert abs(tv - p['turnover']) < 0.01, \
        'turnover 不是百分数或算法变了：字段 %s / 重算 %s' % (p['turnover'], tv)
    # 涨跌幅与 收盘/昨收 必须一致（换算口径搞错这里就会崩）
    assert abs((p['close_bfq'] / p['preclose'] - 1) * 100 - p['change_pct']) < 0.02, \
        'change_pct 与 收盘/昨收 对不上'
    try:
        st.profile('999999')
        raise AssertionError('认不出的代码应报错')
    except st.StockError:
        pass

    # ---- 4) K 线：均线在服务端算，且预热过 ----
    k = st.kline('601857.SH', n=120)
    b = k['bars']
    assert len(b) == 120, '根数不对：%d' % len(b)
    # 🔴 预热根数**跟着最长均线走**，不写死 —— 2026-09-21 加 MA120 时
    #   这条钉着 60 的断言当场挂了（**失败的是断言不是产品**）。
    #   写死 120 是同一个错法的下一个版本：判据要问 `MA_PERIODS`。
    from assay.stk.quote import MA_PERIODS
    assert k['warmup_dropped'] == max(MA_PERIODS), \
        '预热应当是 max(MA_PERIODS)=%d 根（少了的话头部长均线是空的，' \
        '而它不报错，只是那条线短一截）：%s' % (max(MA_PERIODS),
                                             k['warmup_dropped'])
    # 反向自证：最长那条均线在**第一根**就得有值（预热真的取在窗口之外）
    longest = 'ma%d' % max(MA_PERIODS)
    assert b[0].get(longest) is not None, \
        '%s 在第一根就是空的 —— 预热没取够（浮层默认只有 120 根，' \
        '那条线会整条为空）' % longest
    # ★ 第一根就该有 ma60 —— 这正是预热的意义
    assert b[0]['ma60'] is not None, '第一根的 ma60 是空的 —— 预热没生效'
    # 均线自证：最后一根的 ma20 == 最后 20 根收盘均值
    ma20 = sum(x['close'] for x in b[-20:]) / 20
    assert abs(b[-1]['ma20'] - ma20) < 0.02, \
        'ma20 算错：%s vs %s' % (b[-1]['ma20'], ma20)
    for x in b:
        if x['high'] is not None:
            assert x['low'] <= x['open'] <= x['high'], 'OHLC 不自洽：%s' % x
            assert x['low'] <= x['close'] <= x['high'], 'OHLC 不自洽：%s' % x
    # 🔴 BOLL 中轨 == MA20（收盘的 20 日均值），**同一条线**。
    #   曾在主图上把它当成第三个系列画出来（还配了另一个颜色）——
    #   表现就是"5 日线看着有两条、两条都不对"，而它不报错。
    #   这条断言钉住"它们确实是一条线"，所以画图那边只该画一次。
    _ind = st.indicators('601857.SH', n=120)['rows']
    _d = [abs(x['ma20'] - y['mb']) for x, y in zip(b, _ind)
          if x['ma20'] is not None and y['mb'] is not None]
    assert _d and max(_d) < 0.005, \
        'BOLL 中轨与 MA20 不是同一条线了（最大差 %.4f）—— 结论变了就得改画图' \
        % max(_d)

    # ---- 4b) 主图配色：任意两条线不许撞色 ----
    #   🔴 判据用 RGB 欧氏距离量，不靠眼睛。踩过两处：
    #     · ma5 #e0a33c 与 BOLL 中轨 #f9a06b 距离 53 —— 肉眼分不出
    #     · 四条均线的 hex 与四种事件三角【完全相同】，而底部图例
    #       写着"▲除权除息"用的正是 MA5 那个色
    _kc = open(os.path.join(REPO,
                            'web', 'shared', 'kchart.js'), encoding='utf-8').read()
    import re as _re4
    _grab = lambda name: dict(_re4.findall(
        r"(\w+):\s*'(#[0-9a-fA-F]{6})'",
        _re4.search(name + r'\s*=\s*\{([^}]*)\}', _kc).group(1)))
    _ma_c = _grab('MA_COLOR')
    _ev_c = _grab('EV_COLOR')
    _boll = _re4.search(r"BOLL_COLOR\s*=\s*'(#[0-9a-fA-F]{6})'", _kc).group(1)
    # 🔴 **配色表要与服务端算的那几条均线一一对应**，不写死条数
    #   （原来钉的是 4，2026-09-21 加 MA40/MA120 时当场挂了）。
    #   少一条的表现是那条线用 `undefined` 颜色画 —— **而它不报错**。
    assert set(_ma_c) == {'ma%d' % w for w in MA_PERIODS}, \
        ('kchart 的 MA_COLOR 与服务端算的均线对不上：配色 %s，'
         '服务端 %s —— 少的那条会用 undefined 颜色画，而它不报错'
         % (sorted(_ma_c), sorted('ma%d' % w for w in MA_PERIODS)))
    # ★ `_grab` 只认十六进制值，标签表要用通用的那个（我第一版就写错了）
    _ma_l = dict(_re4.findall(
        r"(\w+):\s*'([^']+)'",
        _re4.search(r'MA_LABEL\s*=\s*\{([^}]*)\}', _kc).group(1)))
    assert set(_ma_l) == set(_ma_c), 'MA_LABEL 与 MA_COLOR 对不上：%s' % (
        sorted(set(_ma_l) ^ set(_ma_c)),)
    assert len(_ev_c) == 4, '取不到事件配色表：%s' % (_ev_c,)
    _rgb = lambda h: tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))
    _dist = lambda a, b_: sum(
        (p - q) ** 2 for p, q in zip(_rgb(a), _rgb(b_))) ** 0.5
    # 🔴 **买卖标记也要进这张表** —— 2026-09-21 我给卖出标记挑了
    #   `#2e9bff`，跑判据才发现它与 ma10 的 `#3d8bfd` 只差 22，
    #   等于把"分不清"从 K 线搬到均线上（换成了 `#00e0ff`）。
    _mk = _re4.search(r"MK_SELL\s*=\s*'(#[0-9a-fA-F]{6})'", _kc).group(1)
    _lines = list(_ma_c.items()) + [('boll', _boll), ('买卖标记', _mk)]
    for _i in range(len(_lines)):
        for _j in range(_i + 1, len(_lines)):
            _dd = _dist(_lines[_i][1], _lines[_j][1])
            assert _dd >= 60, \
                ('主图上 %s 与 %s 撞色（RGB 距离 %.0f < 60）—— '
                 '两条线看着像一条' % (_lines[_i][0], _lines[_j][0], _dd))
    # 🔴 **均线的键只许来自一处**（kchart.js 的 `MA_KEYS`）。
    #   2026-09-21 实测：`stock.html` 里另有一份写死的
    #   `['ma5','ma10','ma20','ma60']`（"没勾均线就删掉"用的），
    #   加了 MA40/MA120 之后那两条**删不掉、照画**，而它不报错。
    import glob as _g5
    for _f in _g5.glob(os.path.join(REPO, 'web', '**', '*.js'), recursive=True) \
            + _g5.glob(os.path.join(REPO, 'web', '*.html')):
        if _f.endswith('kchart.js'):
            continue
        _t5 = open(_f, encoding='utf-8').read()
        _hit = _re4.search(r"\[\s*'ma\d+'\s*,\s*'ma\d+'", _t5)
        assert not _hit, (
            '%s 里又写死了一份均线清单（%s…）—— 走 kchart.js 的 MA_KEYS，'
            '否则加一条均线时这里会漏，而漏了不报错'
            % (os.path.relpath(_f, REPO), _hit.group(0)))
    _both = set(_ma_c.values()) & set(_ev_c.values())
    assert not _both, \
        '均线与事件三角共用了颜色 %s —— 图例会指错' % sorted(_both)
    # 画图那边不许再把 mb 当一个系列
    assert "'mb'" not in _kc, \
        'kchart.js 又画 BOLL 中轨了 —— 它等于 MA20，会多出一条线'
    _sh = open(os.path.join(REPO,
                            'web', 'stock.html'), encoding='utf-8').read()
    assert 'mb: IND' not in _sh, \
        'stock.html 又把 mb 合进 bars 了 —— 那就等于让 MA20 画两遍'

    # ---- 5) 复权：跨除权日的假跌幅 ----
    #   🔴 601088 有分红除权。不复权在除权日会掉一个坑，后复权不会。
    #   这就是"区间涨幅一律用后复权"的原因。
    kb = st.kline('601088.SH', n=250, fq='bfq')['bars']
    kh = st.kline('601088.SH', n=250, fq='hfq')['bars']
    assert len(kb) == len(kh)
    rb_ = kb[-1]['close'] / kb[0]['close'] - 1
    rh_ = kh[-1]['close'] / kh[0]['close'] - 1
    assert rh_ > rb_ + 0.01, \
        ('后复权的区间涨幅必须高于不复权（分红被除掉了）：'
         '不复权 %.4f / 后复权 %.4f' % (rb_, rh_))
    # profile 的区间涨幅用的是后复权 —— 不能等于不复权那个
    assert abs(st.profile('601088.SH')['ret_250d'] - rh_) < 0.03, \
        'profile.ret_250d 没用后复权'
    # 🔴 前复权：**看盘页给、特征与回测不给**（2026-09-16 起）。
    #   这条断言原来钉的是"`kline` 必须拒绝 qfq" —— 而那把两件事混成了
    #   一件。要保的是**引擎那条路**不许有前复权（基准是"今天"，每来一次
    #   分红整条历史重算，拿它做特征就是未来函数）；
    #   展示层是另一回事，券商软件默认就是它。
    _q = st.kline('601857.SH', fq='qfq')['bars']
    _b = st.kline('601857.SH', fq='bfq')['bars']
    assert abs(_q[-1]['close'] - _b[-1]['close']) < 0.01, \
        '前复权最新一根该等于当前实际价'
    # **引擎/feed 那条路一个 qfq 都不许有** —— 判据走 ast，不查字符串
    #   （注释里提到它也会命中）。
    import ast as _ast
    import io as _io
    for _f in ('assay/feed.py', 'assay/guard.py', 'assay/engine.py',
               'assay/broker.py'):
        _src = _io.open(_f, encoding='utf-8').read()
        _lits = [nd.value for nd in _ast.walk(_ast.parse(_src))
                 if isinstance(nd, _ast.Constant) and isinstance(nd.value, str)]
        assert 'qfq' not in _lits, \
            ('%s 里出现了前复权 —— 它的基准是"今天"，每来一次分红整条历史'
             '重算一遍，拿它做特征就是未来函数' % _f)

    # ---- 6) 财务时序：一个报告期一行，且报告期 ≠ 公告日 ----
    f = st.finance('601857.SH', n=8)
    rd = [str(x['report_date'])[:10] for x in f['rows']]
    assert len(rd) == len(set(rd)), \
        ('同一报告期出现多行 —— 面板每个交易日都重复一遍当期财务，'
         'DISTINCT 挡不住（重述改一列就是两行）：%s' % rd)
    assert rd == sorted(rd, reverse=True), '报告期没倒序：%s' % rd
    for x in f['rows']:
        assert x['pub_date'] and str(x['pub_date']) > str(x['report_date']), \
            ('公告日必须晚于报告期 —— 拿报告期当可见日就是未来函数：%s'
             % [str(x['report_date']), str(x['pub_date'])])
    return ('代码 7 种写法归一 + 5 种非法返 None；搜索按代码/名称/前缀命中且'
            '同类按市值降序；面板 %d 字段且 change_pct/turnover 量级自证'
            '（turnover=量×价/流通市值×100 精确吻合）；'
            'K 线 %d 根、第一根就有 %s（预热 %d 根）、ma20 复算一致、'
            'BOLL 中轨==MA20（所以主图只画一次）、'
            '主图 %d 条线（含 BOLL 与买卖标记）两两 RGB 距离 >=60 '
            '且与事件三角不共色；'
            '601088 后复权区间涨幅 %.1f%% vs 不复权 %.1f%%（除权坑）；'
            '财务 %d 个报告期不重复且公告日均晚于报告期'
            % (len(p), len(b), longest, k['warmup_dropped'], len(_lines),
               rh_ * 100, rb_ * 100, len(rd)))


_BANDJS = r"""() => {
  const cv = document.getElementById('kcv');
  const g = cv.getContext('2d');
  const dpr = cv.width / cv.getBoundingClientRect().width;
  const sel = {i0: KSEL.i0, i1: KSEL.i1};
  const grab = (px) => Array.from(
    g.getImageData(Math.round(px * dpr), 40, 3, 60).data).join(',');
  const opt = (s) => ({bars: BARS, log: KLOG, events: SHOWEV ? EVS : [],
    subs: kSubs(), hover: null, sel: s});
  const geo = drawKChart(cv, opt(null));
  const a = Math.min(sel.i0, sel.i1), b = Math.max(sel.i0, sel.i1);
  const xin = (geo.X(a) + geo.X(b)) / 2;
  const xout = geo.X(Math.max(0, a - 6));
  const in0 = grab(xin), out0 = grab(xout);
  drawKChart(cv, opt(sel));
  return {inChanged: in0 !== grab(xin), outChanged: out0 !== grab(xout),
          xin: xin, xout: xout};
}"""


@case('K 线读数：那天的成交 / 框选浮窗贴在选区外 / 副图读数在副图上（playwright）',
       tag='web')
def t_kchart_readouts():
    """2026-09-15 用户三条，都是"信息该出现在该出现的地方"：

      ① "鼠标移到K线图上时，在买入、卖出那一根时看不到买入、卖出的价格数量。
         需要一个独立的浮窗展示出来，如果有多笔交易就展示多笔"
      ② "框选一段范围，展示范围内涨跌幅等等信息太不明显，
         最好是在框选范围外展示一个浮窗"
      ③ "正常的K线浮窗里不要展示MA信息、MACD信息，MA信息是已经有了，
         MACD的信息应该放在MACD的左上角，数字跟随变化"

    🔴 ③ 的实质是**同一份信息不要两处看**：MA 四条主图图例本来就带当天值、
      DIF/DEA/MACD 只在副图上有意义 —— 浮窗里再列一遍，就是让眼睛在
      两个地方之间来回找（同「汇总数字只在 KPI 板出现一次」那条）。
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
    notes = []
    try:
        # 挑一只**有成交**的票（没有的话 ① 验不到）
        from assay import live as lv
        code = None
        for a in lv.load_accounts():
            if a.get('archived'):
                continue
            for f in lv.fills(a['id']):
                code = f['code']
                break
            if code:
                break
        if not code:
            return '跳过（没有任何成交）'
        with sync_playwright() as pw:
            br = pw.chromium.launch()
            pg = br.new_page(viewport={'width': 1440, 'height': 900})
            errs = []
            pg.on('pageerror', lambda e: errs.append(str(e)))
            pg.goto('http://127.0.0.1:%d/stock.html?code=%s&sub=macd'
                    % (port, code), wait_until='networkidle')
            pg.wait_for_selector('#kcv', timeout=90000)
            pg.wait_for_timeout(2500)

            n_tr = pg.evaluate('() => (TRADES || []).length')
            assert n_tr > 0, '这只票取不到成交（%s）—— ① 验不到' % code

            # ---- ① hover 到成交那根：读数里要有买卖明细 ----
            geo = pg.evaluate("""(dt) => {
                const i = BARS.findIndex(b => b.date === dt);
                if (i < 0) return null;
                const cv = document.getElementById('kcv');
                const g = drawKChart(cv, {bars: BARS, trades: TRADES});
                const r = cv.getBoundingClientRect();
                /* ★ `X()` 返回的**已经是 CSS 像素**（canvas 按实际宽度
                   绘制，不是固定 viewBox）—— 再乘一次比例会把坐标算到
                   画布外面（实测 1522 > 视口 1440，鼠标根本没落在页面上）。 */
                return {i: i, x: r.left + g.X(i),
                        y: r.top + r.height * 0.25,
                        hits: (g.trHits || []).length};}""",
                pg.evaluate('() => TRADES[0].date'))
            assert geo, '成交那天不在当前窗口里（换区间）'
            # 🔴 B/S 标记也要画出来 —— 读数有了但图上没标记，人不知道去哪 hover
            # 🔴 判据要用**页面自己渲染**的那次（`KGEO` 是 `redraw()` 存下的），
            #   不是我在断言里重画一次的结果 —— 后者传的是**我给的**参数，
            #   页面传什么都不影响它（变异"不画 B/S 标记"因此漏过一轮）。
            n_hit = pg.evaluate('() => (KGEO && KGEO.trHits || []).length')
            assert n_hit > 0, \
                ('图上没画 B/S 标记（页面渲染的 KGEO.trHits 是空的）—— '
                 '读数再全，人也不知道该往哪一根上移')
            pg.mouse.move(geo['x'], geo['y'])
            pg.wait_for_timeout(700)
            tip = pg.inner_text('#ktip')
            assert '我的成交' in tip, \
                ('hover 到成交那根，读数里没有成交明细：%r —— 原来要精确指到'
                 '那个 7.5px 的圆点上才看得到，而人是往**那根 K 线**上移的'
                 % tip[:120])
            import re as _re
            assert _re.search(r'[买卖]\s+[\d,]+\s*股\s*@', tip), \
                '成交明细里没有"买/卖 N 股 @ 价"：%r' % tip[:160]
            notes.append('hover 那根 K 线就出成交明细（%d 笔）' % n_tr)

            # ---- ③ 读数里不许再有 MA / MACD ----
            for bad in ('MA5', 'MA10', 'MA20', 'MA60', 'DIF', 'DEA', 'MACD'):
                assert bad not in tip, \
                    ('读数里还有「%s」—— MA 在主图图例里、DIF/DEA/MACD 在副图'
                     '左上角，浮窗里再列一遍就是同一份信息两处看：%r'
                     % (bad, tip[:160]))
            # 🔴 但它们**不能就此消失**：主图图例要有 MA+值，副图要有 DIF+值。
            #   只删不补的话这条断言"通过"了，而信息被弄丢了。
            px = pg.evaluate("""() => {
                const cv = document.getElementById('kcv');
                const g = cv.getContext('2d');
                const W = cv.width, H = cv.height;
                const dpr = W / cv.getBoundingClientRect().width;
                // 主图图例带（上方 ~14px 处）、副图图例带（副图顶 +10px）
                const strip = (y0, h) => {
                  const d = g.getImageData(0, Math.round(y0 * dpr),
                                           Math.round(260 * dpr),
                                           Math.round(h * dpr)).data;
                  let n = 0;
                  for (let i = 3; i < d.length; i += 4) if (d[i] > 40) n++;
                  return n;
                };
                const hpx = cv.getBoundingClientRect().height;
                return {main: strip(4, 16), sub: strip(hpx * 0.72, 16)};}""")
            assert px['main'] > 200, \
                '主图左上角没有图例（MA 的值也跟着没了）：%d 像素' % px['main']
            assert px['sub'] > 100, \
                ('副图左上角没有图例 —— DIF/DEA/MACD 从浮窗里拿掉了，'
                 '就必须出现在副图上（用户原话："放在MACD的左上角"）：'
                 '%d 像素' % px['sub'])
            # 🔴 **光有图例带不够，要有【值】，而且要"跟随变化"。**
            #   ★ 判据不能靠数像素：那条带子里混着 MACD 柱子与曲线，
            #     图例那几个字完全被淹没 —— 实测"只有名字"与"带值"两态
            #     扫出来都是 1268，像素这条路走不通（我试了两个版本才放弃）。
            #   ★ 改成**拦截绘制调用**：把 `fillText` 包一层，记下副图图例
            #     那一行画了什么。这直接验的是"画上去的文字"，
            #     比任何像素阈值都准，也不依赖布局。
            drew = pg.evaluate("""() => {
                const cv = document.getElementById('kcv');
                const g = cv.getContext('2d');
                const orig = g.fillText.bind(g);
                const seen = [];
                g.fillText = function (t, x, y) { seen.push([String(t), x, y]);
                                                  return orig(t, x, y); };
                try { drawKChart(cv, {bars: BARS, subs: kSubs(),
                                      hover: null}); }
                finally { g.fillText = orig; }
                /* 副图图例：DIF/DEA/MACD 那三个（不分大小写前缀匹配） */
                const keys = ['DIF', 'DEA', 'MACD'];
                return seen.map(x => x[0])
                  .filter(t => keys.some(k => t.toUpperCase().startsWith(k)));}""")
            assert drew, '副图上根本没画 DIF/DEA/MACD 图例'
            import re as _re2
            withval = [t for t in drew if _re2.search(r'[-\d]', t)]
            assert len(withval) >= 2, \
                ('副图图例画的是 %r —— 只有名字没有值。用户要的是'
                 '"MACD的信息应该放在MACD的左上角，**数字跟随变化**"，'
                 '而把它从浮窗里拿掉之后就必须在这儿看得到' % drew)
            # 🔴 **"跟随变化"**：hover 到另一天，值必须跟着变。
            #   只验"有数字"的话，写死一个常数也全绿。
            drew2 = pg.evaluate("""(hv) => {
                const cv = document.getElementById('kcv');
                const g = cv.getContext('2d');
                const orig = g.fillText.bind(g);
                const seen = [];
                g.fillText = function (t, x, y) { seen.push(String(t));
                                                  return orig(t, x, y); };
                try { drawKChart(cv, {bars: BARS, subs: kSubs(),
                                      hover: hv}); }
                finally { g.fillText = orig; }
                return seen.filter(t => t.toUpperCase().startsWith('DIF'));}""",
                5)
            dif0 = next(t for t in drew if t.upper().startswith('DIF'))
            assert drew2 and drew2[0] != dif0, \
                ('hover 到第 5 根时 DIF 图例还是 %r —— 值没有跟着光标变'
                 '（用户原话："数字跟随变化"）' % drew2)
            notes.append('副图图例带值且跟随光标（%s -> %s）' % (dif0, drew2[0]))
            notes.append('读数不再重复 MA/MACD，两处图例都在')

            # ---- ② 框选浮窗：贴在选区【外侧】，不盖住它 ----
            box = pg.query_selector('#kcv').bounding_box()
            y = box['y'] + box['height'] * 0.3
            for lab, (fa, fb) in (('选左半', (0.15, 0.40)),
                                  ('选右半', (0.60, 0.85))):
                pg.mouse.move(box['x'] + box['width'] * fa, y)
                pg.mouse.down()
                pg.mouse.move(box['x'] + box['width'] * (fa + 0.05), y)
                pg.mouse.move(box['x'] + box['width'] * fb, y, steps=8)
                pg.mouse.up()
                pg.wait_for_timeout(700)
                d = pg.evaluate("""() => {
                    const e = document.getElementById('kselpop');
                    if (!e || getComputedStyle(e).display === 'none') return null;
                    const c = document.getElementById('cbox').getBoundingClientRect();
                    const r = e.getBoundingClientRect();
                    const cv = document.getElementById('kcv');
                    const g = drawKChart(cv, {bars: BARS, sel: KSEL});
                    const a = Math.min(KSEL.i0, KSEL.i1);
                    const b = Math.max(KSEL.i0, KSEL.i1);
                    return {l: r.left - c.left, r: r.right - c.left,
                            selL: g.X(a), selR: g.X(b),
                            inside: r.right <= c.right + 1 && r.left >= c.left - 1,
                            txt: e.innerText};}""")
                assert d, '%s：框选之后没有浮窗（#kselpop）' % lab
                assert d['inside'], '%s：浮窗跑到容器外面了' % lab
                # 🔴 **不许盖住选区** —— 那正是人刚框出来、正在看的东西
                overlap = not (d['r'] <= d['selL'] or d['l'] >= d['selR'])
                assert not overlap, \
                    ('%s：浮窗[%.0f,%.0f] 盖住了选区[%.0f,%.0f] —— '
                     '用户要的是"在框选范围**外**展示"'
                     % (lab, d['l'], d['r'], d['selL'], d['selR']))
                for want in ('最高', '最低', '振幅', '交易日'):
                    assert want in d['txt'], \
                        '%s：浮窗里缺「%s」：%r' % (lab, want, d['txt'][:100])
                assert _re.search(r'[+-]\d+\.\d+%', d['txt']), \
                    '%s：浮窗里没有区间涨跌幅：%r' % (lab, d['txt'][:100])
            notes.append('框选浮窗贴在选区外侧（左右两侧都验）')
            # 点一下取消
            pg.mouse.move(box['x'] + box['width'] * 0.5, y)
            pg.mouse.down()
            pg.mouse.up()
            pg.wait_for_timeout(500)
            assert pg.evaluate("""() => getComputedStyle(
                document.getElementById('kselpop')).display""") == 'none', \
                '点一下没收起浮窗'
            assert not errs, 'JS 报错：%s' % errs[:3]
            br.close()
    finally:
        sv.ALLOW_LIVE = old_live
        httpd.shutdown()
    return '；'.join(notes)


@case('K 线左右移动 + 框选测算区间（playwright）', tag='web')
def t_kchart_pan_select():
    """2026-09-14 用户："K线图还需要有左右移动、框选一段范围自动测算区间
    涨跌幅、最低价、最高价、振幅的功能。"

    🔴 **翻页按【根数】不按日期**（服务端 `off` = 跳过最新几根）：按日期翻
      要前端自己算交易日，而前端没有交易日历 —— 本项目为此栽过一次
      （硬编码判据误报了一整页假告警）。
    🔴 **副图也要带 off**：不带的话翻页后 MACD 画的还是最新那一段，
      两张图上下对不上，**而它不报错**。
    🔴 **区间涨跌幅的基点取首根的【前收】**，不是它自己的收盘 —— 用首日收盘
      做基点等于把首日那根的涨跌排除在外，而那正是人框进来想看的
      （同「基准基点取第一天的前一交易日收盘」，实测差过 9.5pp）。
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return '跳过（无 playwright）'
    import threading
    from assay import server as sv
    from http.server import ThreadingHTTPServer
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), sv.Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    notes = []
    try:
        with sync_playwright() as pw:
            br = pw.chromium.launch()
            pg = br.new_page(viewport={'width': 1440, 'height': 900})
            errs = []
            pg.on('pageerror', lambda e: errs.append(str(e)))
            pg.goto('http://127.0.0.1:%d/stock.html?code=601857.XSHG&sub=macd'
                    % port, wait_until='networkidle')
            pg.wait_for_selector('#kcv', timeout=90000)
            pg.wait_for_timeout(2500)

            def win():
                return pg.evaluate("""() => ({a: BARS[0].date,
                    b: BARS[BARS.length - 1].date, off: KOFF, tot: KTOTAL,
                    ind: (IND && IND.length) ? IND.length : 0,
                    ma60: BARS[0].ma60});""")

            w0 = win()
            assert w0['tot'] > 1000, '总根数 %s —— 换一只历史长的票' % w0['tot']
            # ---- ① 往早翻：窗口必须真的移走，且【接得上】----
            pg.click('.kpan[data-pan="-1"]')
            pg.wait_for_timeout(3000)
            w1 = win()
            assert w1['b'] < w0['a'] or w1['a'] < w0['a'], \
                '点「‹ 早」之后窗口没往回移：%s~%s -> %s~%s' \
                % (w0['a'], w0['b'], w1['a'], w1['b'])
            assert w1['off'] > 0, 'KOFF 还是 0'
            #   半屏一步：两页必须**有重叠**，不然接不上形态
            assert w1['b'] > w0['a'], \
                ('翻一页跳过头了（新窗口 %s~%s 与原窗口 %s~ 没有重叠）—— '
                 '一步该是半屏' % (w1['a'], w1['b'], w0['a']))
            #   🔴 翻到哪一页，那一页的 ma60 都要是对的（预热取在窗口之外）
            assert w1['ma60'] is not None, \
                ('翻页后首根没有 ma60 —— 预热那 60 根没跟着 off 一起偏移，'
                 '于是每翻一页头部均线就缺一截，**而它不报错**')
            #   🔴 副图跟着走：不跟的话两张图上下对不上
            assert w1['ind'] == len(pg.evaluate('() => BARS')), \
                '副图行数 %d 与 K 线 %d 对不上 —— indicators 没带 off' \
                % (w1['ind'], len(pg.evaluate('() => BARS')))
            ind0 = pg.evaluate("() => IND[0] && IND[0].date")
            assert ind0 == w1['a'], \
                ('副图第一行是 %s，而 K 线第一根是 %s —— 翻页后副图画的还是'
                 '别的时间段，两张图上下对不上' % (ind0, w1['a']))
            notes.append('往早翻：%s~%s -> %s~%s（有重叠、ma60 与副图都跟上）'
                         % (w0['a'], w0['b'], w1['a'], w1['b']))
            # ---- ② 回最新 ----
            pg.click('.kpan[data-pan="0"]')
            pg.wait_for_timeout(3000)
            w2 = win()
            assert w2['off'] == 0 and w2['b'] == w0['b'], \
                '「最新」没回到最新一页：%s' % w2
            notes.append('「最新」回得去')

            # ---- ③ 框选 ----
            box = pg.query_selector('#kcv').bounding_box()
            y = box['y'] + box['height'] * 0.3
            x0 = box['x'] + box['width'] * 0.35
            x1 = box['x'] + box['width'] * 0.65
            pg.mouse.move(x0, y)
            pg.mouse.down()
            pg.mouse.move(x0 + 40, y)
            pg.mouse.move(x1, y, steps=8)
            pg.mouse.up()
            pg.wait_for_timeout(600)
            sel = pg.evaluate('() => KSEL')
            assert sel and sel['i0'] != sel['i1'], '拖动之后没有选区：%s' % sel
            # ★ 2026-09-15 起读数在**浮窗**里（`#kselpop`），不在图下那行
            #   （`#ksel` 现在只留"怎么用"的说明）—— 用户："太不明显，
            #   最好是在框选范围外展示一个浮窗"。**失败的是断言不是产品。**
            txt = pg.inner_text('#kselpop')
            for k in ('最高', '最低', '振幅'):
                assert k in txt, '框选读数缺「%s」：%s' % (k, txt[:120])
            # 🔴 **数字要自己复算一遍** —— 只查"有没有这四个字"的话，
            #   算错了照样绿（把 max 写成 min 也有"最高"两个字）。
            chk = pg.evaluate("""() => {
              const a = Math.min(KSEL.i0, KSEL.i1), b = Math.max(KSEL.i0, KSEL.i1);
              const seg = BARS.slice(a, b + 1);
              const hi = Math.max(...seg.map(x => x.high));
              const lo = Math.min(...seg.map(x => x.low));
              const base = seg[0].preclose;
              return {hi: hi, lo: lo, base: base,
                      ret: seg[seg.length - 1].close / base - 1,
                      amp: (hi - lo) / base, n: seg.length,
                      firstClose: seg[0].close};}""")
            assert ('最高 %s' % chk['hi']) in txt.replace('　', ' '), \
                '最高价对不上：读数 %s / 复算 %s' % (txt[:140], chk['hi'])
            assert ('最低 %s' % chk['lo']) in txt.replace('　', ' '), \
                '最低价对不上：读数 %s / 复算 %s' % (txt[:140], chk['lo'])
            import re as _re
            # ★ 浮窗里区间涨跌幅是**第一行的大字**（`.sphi`），不再带
            #   "区间涨跌"这个前缀 —— 取那个元素本身比在全文里正则更准。
            m = _re.search(r'([+-][\d.]+)%', pg.inner_text('#kselpop .sphi'))
            assert m, '读不出区间涨跌幅：%s' % txt[:140]
            shown = float(m.group(1))
            assert abs(shown - chk['ret'] * 100) < 0.02, \
                '区间涨跌幅对不上：页面 %.2f%% / 按【首根前收】复算 %.2f%%' \
                % (shown, chk['ret'] * 100)
            #   🔴 反向自证：用**首根收盘**当基点会算出另一个数，
            #     两者必须不同 —— 否则这条断言分不出用的是哪个基点。
            alt = (chk['firstClose'] and
                   pg.evaluate("() => BARS[Math.max(KSEL.i0,KSEL.i1)].close")
                   / chk['firstClose'] - 1)
            assert abs(alt * 100 - chk['ret'] * 100) > 0.05, \
                ('首根前收与首根收盘算出来一样（%.4f vs %.4f）—— 换个选区，'
                 '这条基点断言测不到' % (alt * 100, chk['ret'] * 100))
            m2 = _re.search(r'振幅\s*([\d.]+)%', txt.replace('　', ' '))
            assert m2 and abs(float(m2.group(1)) - chk['amp'] * 100) < 0.02, \
                '振幅对不上：%s / 复算 %.2f%%' % (txt[:140], chk['amp'] * 100)
            notes.append('框选 %d 天：涨跌 %.2f%% / 高 %s / 低 %s / 振幅 %.2f%%'
                         % (chk['n'], shown, chk['hi'], chk['lo'],
                            chk['amp'] * 100))
            # 🔴 **选区带必须真的画在画布上** —— 只验读数的话，把那段绘制
            #   注掉照样全绿（变异实测漏过），而那时人根本看不出自己框了哪一段。
            #   判据两头都要：**带内像素变了** + **带外像素没变**
            #   （只判"画布变了"的话，整块涂一层也算通过）。
            band = pg.evaluate(_BANDJS)
            assert band['inChanged'], \
                ('框选之后带【内】的像素一点没变 —— 选区带根本没画出来，'
                 '人看不出自己框了哪一段（读数对不代表图上有标记）')
            assert not band['outChanged'], \
                ('带【外】的像素也变了（x=%.0f）—— 那不是"一条带"，'
                 '是把整张图涂了一层' % band['xout'])
            notes.append('选区带真的画出来了（带内变、带外不变）')

            # ---- ④ 点一下取消 ----
            pg.mouse.move(box['x'] + box['width'] * 0.5, y)
            pg.mouse.down()
            pg.mouse.up()
            pg.wait_for_timeout(500)
            assert pg.evaluate('() => KSEL') is None, '点一下没清掉选区'
            notes.append('点一下取消')

            # ---- ⑤ 🔴 选中的标签不许【蓝字压蓝底】----
            #   `class="lvtag rg on"` 上两条规则优先级相同（都 0,2,0），
            #   靠先后决胜负 —— `.lvtag.on` 把 `.rg.on` 的深色字覆盖成 accent，
            #   而背景还是 accent：那个按钮整个看不见（实测个股页「1年」、
            #   对比页同样）。判据扫全站，不是只看这一处。
            for u in _pages():
                pg.goto('http://127.0.0.1:%d%s' % (port, u), wait_until='networkidle')
                pg.wait_for_timeout(1800)
                same = pg.evaluate("""() => [...document.querySelectorAll(
                    '.lvtag.on,.lvtag.off,.rg.on')].filter(a => {
                    const c = getComputedStyle(a);
                    return c.color === c.backgroundColor;})
                  .map(a => a.className + '|' + a.textContent.trim().slice(0, 8))""")
                assert not same, \
                    ('%s 上这些标签前景色 == 背景色（整个看不见）：%s' % (u, same))
            notes.append('6 个页面上没有"同色不可见"的标签')
            assert not errs, 'JS 报错：%s' % errs[:3]
            br.close()
    finally:
        httpd.shutdown()
    return '；'.join(notes)


@case('K 线柱子有最大宽度：新股不许拉伸填满，往左贴（playwright）', tag='web')
def t_kchart_min_span():
    """2026-09-14 用户："新股的 K 线展示特别大，因为有几根 K 线就展示几根，
    然后填满。应该设置一个最小展示天数，不足天数的往左边贴，不要占满。"

    原来 `step = w / n` —— 有几根画几根、平分整个宽度。实测 688835
    上市 15 个交易日、画布 1334px，于是**每根 K 线 60px 宽**：一屏几个
    大色块，既看不出形态、也让人误以为"这只票就长这样"。

    ★ 判据用**单根最大像素宽**（14px）而不是写死"最少 N 天"：画布宽度
      本来就不一样（个股页 ~1334、浮层 1180），写死天数在窄画布上又太挤。
    ★ 也不能反过来按"请求了多少天"留槽 —— 选「1 年」时 15 根票会被压成
      15 条发丝，那是另一个极端。

    🔴 **两头都要钉**：只钉"新股不占满"的话，把 `MAXSTEP` 调到 1px 也全绿，
      而那会把**所有**图都压成左边一条 —— 正常股票必须**仍然占满**。
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return '跳过（无 playwright）'
    import threading
    from assay import server as sv
    from http.server import ThreadingHTTPServer
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), sv.Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    # 先在服务端挑一只**真正的新股**（上市天数 < 30）—— 写死代码的话
    # 它总有一天不再是新股，而那时这条用例会静默变成"又一次测老股"。
    import duckdb as _dd
    from assay import live as _lv
    root = _lv._lake()
    con = _dd.connect(':memory:')
    try:
        pan = ("read_parquet('%s/mart/panel_daily/panel_*.parquet')" % root)
        row = con.execute(
            "SELECT jq_code, listed_days FROM %s WHERE date = "
            "(SELECT max(date) FROM %s) AND listed_days BETWEEN 3 AND 30 "
            "ORDER BY listed_days LIMIT 1" % (pan, pan)).fetchone()
    finally:
        con.close()
    if not row:
        httpd.shutdown()
        return '跳过（当前面板里没有上市 30 天内的新股）'
    new_code, listed = row[0], int(row[1])
    notes = []
    try:
        with sync_playwright() as pw:
            br = pw.chromium.launch()
            pg = br.new_page(viewport={'width': 1440, 'height': 900})
            errs = []
            pg.on('pageerror', lambda e: errs.append(str(e)))

            def geo(code):
                pg.goto('http://127.0.0.1:%d/stock.html?code=%s' % (port, code),
                        wait_until='networkidle')
                pg.wait_for_selector('#kcv', timeout=90000)
                pg.wait_for_timeout(2000)
                return pg.evaluate("""() => {
                  const cv = document.getElementById('kcv');
                  const g = drawKChart(cv, {bars: BARS});
                  if (!g) return null;
                  const n = (BARS || []).length;
                  return {n: n, step: g.step, last: g.X(n - 1),
                          W: cv.getBoundingClientRect().width};}""")

            # ---- ① 新股：柱子不许变宽，且**往左贴** ----
            a = geo(new_code)
            assert a and a['n'] > 0, '新股 %s 没渲染出 K 线' % new_code
            assert a['n'] <= 40, \
                '挑到的 %s 有 %d 根，不算新股了' % (new_code, a['n'])
            assert a['step'] <= 15.0, \
                ('新股 %s（%d 根）每根 %.1f px —— 拉伸去填满画布了，'
                 '一屏几个大色块看不出形态' % (new_code, a['n'], a['step']))
            assert a['last'] < a['W'] * 0.6, \
                ('新股的最后一根画在 %.0f/%.0f（%.0f%%）—— 还是占满了；'
                 '不足的天数该**往左贴**、右边留白'
                 % (a['last'], a['W'], a['last'] / a['W'] * 100))
            notes.append('新股 %s：%d 根 · 每根 %.1f px · 只占 %.0f%%'
                         % (new_code, a['n'], a['step'], a['last'] / a['W'] * 100))

            # ---- ② 🔴 正常股票必须【仍然占满】 ----
            #   只钉上面那条的话，把 MAXSTEP 调到 1px 也全绿 —— 而那会
            #   把所有图都压成左边一条。
            b = geo('601857.XSHG')
            assert b and b['n'] >= 100, '老股该有上百根：%s' % (b or {}).get('n')
            assert b['last'] > b['W'] * 0.9, \
                ('老股（%d 根）只画到 %.0f%% —— 最大宽度限制误伤了正常图，'
                 '它本来就该占满' % (b['n'], b['last'] / b['W'] * 100))
            assert b['step'] < a['step'], \
                ('老股每根 %.2f px 不比新股 %.2f px 窄 —— 说明限制没生效'
                 % (b['step'], a['step']))
            notes.append('老股 601857：%d 根 · 每根 %.2f px · 占 %.0f%%'
                         % (b['n'], b['step'], b['last'] / b['W'] * 100))
            assert not errs, 'JS 报错：%s' % errs[:2]
            br.close()
    finally:
        httpd.shutdown()
    return '；'.join(notes)


@case('个股页面真实渲染（playwright）', tag='web')
def t_stock_ui():
    """独立页 /stock.html：搜索 → K 线 → 副图 → 事件 → 联动 → 同业 → 板块。

    ★ 「Canvas 画出来了」不能只看 DOM 有没有 <canvas> —— 那永远都在。
      判据是**画布上有非透明像素**，且切换后像素分布确实变了。
      画崩了（尺寸算错、坐标 NaN）的表现就是一张空白画布，而它不报错。
    ★ 旧的 hash 链接 `/#/stock/xxx` 必须还能用（跳到新页）——
      书签和别处的链接不该失效，而"点了没反应"是最难查的那种坏。
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return '跳过（无 playwright）'
    import threading
    from http.server import ThreadingHTTPServer

    import shutil
    import tempfile

    from assay import server as sv
    from assay import watchlist as wlmod
    old_live, old_dir = sv.ALLOW_LIVE, wlmod.LIVE
    sv.ALLOW_LIVE = True                 # 自选星要能点
    # 🔴 自选账本重定向到临时目录 —— 这个用例会真点那颗星，不重定向就会往
    #   【真账本】里写测试记录。append-only 的账本本来就不该被测试污染，
    #   而它每次跑留一对 add/remove，攒了 11 对才在提交时被发现。
    tmpwl = tempfile.mkdtemp()
    wlmod.LIVE = tmpwl
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), sv.Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    NZ = ("() => { const c=document.querySelector('#kcv');"
          " const g=c.getContext('2d');"
          " const d=g.getImageData(0,0,c.width,c.height).data;"
          " let n=0; for(let i=3;i<d.length;i+=4) if(d[i]>0) n++; return n; }")
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
            pg.goto('http://127.0.0.1:%d/stock.html' % port, wait_until='networkidle')
            pg.wait_for_selector('#sbox input', timeout=30000)
            # ★ 这一页上【不该出现 SQL 输入框】—— 要写任意查询去 /#/query
            assert pg.locator('#qsql').count() == 0, '个股页不该有 SQL 输入框'
            # 顶栏导航：一处定义，当前页高亮
            #   🔴 **数字自己算**，不写死：原来钉的是 `>= 8`，顶栏一合并
            #     （9 -> 7）这条就挂了，而挂的是断言不是产品。判据取 NAV
            #     自己的长度 —— 下次再增减入口不用手改，而"忘了改"的表现
            #     是报告在说谎（同「断言直接扫目录而不是照清单拼」那条）。
            n_nav = pg.evaluate('() => NAV.length')
            assert n_nav >= 5, '顶栏入口只剩 %d 个？' % n_nav
            assert pg.locator('#top .btn.nav').count() == n_nav, \
                ('顶栏渲染出 %d 个入口，而 NAV 里有 %d 个'
                 % (pg.locator('#top .btn.nav').count(), n_nav))
            assert '个股' in pg.locator('#top .btn.nav.on').inner_text(), \
                '当前页没高亮'

            # ---- 搜索：打字 → 下拉 → 键盘选中 ----
            pg.fill('#sbox input', '中国石油')
            pg.wait_for_selector('.skit', timeout=15000)
            first = pg.locator('.skit').first.inner_text()
            assert '601857' in first and '中国石油' in first, \
                '下拉里没有代码或名称：%s' % first
            pg.keyboard.press('Enter')
            pg.wait_for_selector('#kcv', timeout=30000)
            pg.wait_for_timeout(1500)
            assert '601857' in pg.url, '没跳到个股页：%s' % pg.url

            head = ' '.join(pg.locator('#body .lvhead').first.inner_text().split())
            assert '中国石油' in head and '601857.XSHG' in head, '头部不对：%s' % head
            assert '沪深300' in head, '指数标签没渲染：%s' % head

            kp = ' | '.join(pg.locator('#body .kpi .k').all_inner_texts())
            for kk in ('今开 / 昨收', '最高 / 最低', '涨停 / 跌停', '成交额',
                       'PE(TTM)', 'PB', 'ROE(TTM)', '52 周区间', '区间涨幅'):
                assert kk in kp, 'KPI 缺「%s」：%s' % (kk, kp)
            body = pg.locator('#pg').inner_text()
            assert 'undefined' not in body and 'NaN' not in body, \
                '页面上有 undefined/NaN'
            assert '换手 +' not in body and '振幅 +' not in body, \
                '换手/振幅带了 + 号（它们不会为负）'

            # ---- 各个板块都在 ----
            secs = [x.split('\n')[0] for x in
                    pg.locator('#body .lvsec h3').all_inner_texts()]
            for kk in ('日 K', '实盘持仓', '回测买过它', '所属板块', '同行业',
                       '最近 20 个交易日', '事件', '财务（按报告期）'):
                assert any(kk in x for x in secs), '缺「%s」这一节：%s' % (kk, secs)
            # 所属板块要能点去板块页
            assert pg.locator('#body a.chip[href*="/sector.html"]').count() >= 1, \
                '板块 chip 没链到板块页'

            # ---- K 线真的画出来了（含 MACD 副图）----
            nz1 = pg.evaluate(NZ)
            assert nz1 > 5000, 'Canvas 上几乎没有像素（画崩了）：%d' % nz1
            bb = pg.locator('#kcv').bounding_box()
            pg.mouse.move(bb['x'] + bb['width'] * 0.7, bb['y'] + bb['height'] * 0.3)
            pg.wait_for_timeout(500)
            assert pg.locator('#ktip').is_visible(), '十字光标没出读数'
            tip = pg.locator('#ktip').inner_text()
            # 🔴 **MA / DIF 不再出现在读数里**（2026-09-15 用户要求）：
            #   MA 四条主图图例本来就带那一天的值、DIF/DEA/MACD 改到副图
            #   左上角 —— 浮窗里再列一遍就是同一份信息两处看。
            #   这两处"搬到哪儿去了"由「K 线读数」那条用例专门钉着，
            #   这里只保留"读数本身要有 OHLC"。**失败的是断言不是产品。**
            for kk in ('开', '高', '低', '收'):
                assert kk in tip, '读数缺「%s」：%s' % (kk, tip.replace('\n', ' '))
            for kk in ('MA20', 'MA60', 'DIF'):
                assert kk not in tip, \
                    ('读数里还有「%s」—— 它该在图例上（主图 MA / 副图 DIF），'
                     '不该在跟着光标的浮窗里重复一遍：%s'
                     % (kk, tip.replace('\n', ' ')))
            assert 'undefined' not in tip and 'NaN' not in tip, \
                '读数里有 undefined/NaN：%s' % tip

            # ---- 均线图例：色块 + 名称 + 【那一天的值】 ----
            #   🔴 光有名字不够 —— 几条颜色相近的线还是分不出谁是谁。
            #     有了数字就能拿它跟纵轴对一下（也是这次"5 日线有两条"
            #     那个问题最省事的自查手段）。
            _lg = pg.evaluate("() => { const c=document.querySelector('#kcv'); const g=c.getContext('2d'); const r=c.width/c.clientWidth; const d=g.getImageData(50*r, 4*r, 340*r, 16*r).data; const s=new Set(); let n=0; for(let i=0;i<d.length;i+=4){ if(d[i+3]>200){ n++; s.add((d[i]>>4)+','+(d[i+1]>>4)+','+(d[i+2]>>4)); } } return [n, s.size]; }")
            assert _lg[0] > 300, '图例区几乎没画东西：%s' % _lg
            assert _lg[1] >= 4, \
                '图例区颜色少于 4 种 —— 四条均线的色块/文字没分开：%s' % _lg
            _ms = pg.evaluate('() => BARS[BARS.length-1].ma5')
            assert _ms and _ms > 0, '取不到 ma5'

            # ---- BOLL：默认【关】，开关生效 ----
            #   🔴 原来只要选了副图就无条件叠 BOLL，主图上 7 条线；
            #     而 BOLL 中轨就是 MA20（同一条线画两遍、两个颜色），
            #     上下轨的橙色又与 MA5 撞色 —— 表现就是"5 日线看着有两条"。
            # ★ **入口变过两次，判据一次没变**：工具条独立开关 -> 指标面板里
            #   的"主图指标"那组 -> 工具条上的「主图 [均线][布林带]」两个开关
            #   （副图改成槽位之后，面板整个没了，主图那两个本来就只有两个、
            #   做成下拉反而绕）。要证的仍是那三件事：
            #   默认关 / 开了画布真的变 / 状态进 URL（刷新不丢）。
            #   第四次：chip + 「+ 主图」挑；**第五次**（2026-09-16）：
            #   全部收进「⚙ 设置」浮窗里**勾选** —— 工具条原来塞着四组东西
            #   （翻页 + 区间 + 复权 + 主图 + 对数 + 事件），窄一点就绕成两排
            #   （用户："排版总是感觉太挤……全部变成主图的设置选项"）。
            assert pg.locator('#ktoolbar #kparam').count() == 1, \
                '工具条上没有「⚙ 设置」这个入口'
            _kset(pg, True)
            assert pg.locator('#kmwrap .kmain[data-i="boll"]').count() == 1, \
                '设置里没有 BOLL 这个主图指标'
            assert not pg.locator('#kmwrap .kmain[data-i="boll"]').is_checked(), \
                'BOLL 默认就叠上去了（默认该只有均线）'
            _kset(pg, False)
            assert pg.evaluate("() => KMAIN.indexOf('boll') < 0"), \
                'BOLL 状态位不对'
            # ★ 先把光标移开画布再量基线 —— 十字光标本身就画了几百个像素
            #   （实测 417），拿"悬停时"的数当基线会让"关掉后回不到原样"
            #   假失败一次。
            pg.mouse.move(bb['x'] + bb['width'] / 2, bb['y'] - 40)
            pg.wait_for_timeout(400)
            _no = pg.evaluate(NZ)
            _kmain(pg, 'boll', True)
            _yes = pg.evaluate(NZ)
            assert _yes > _no, \
                '开了 BOLL 画布像素没变多（%d -> %d）' % (_no, _yes)
            _kset(pg, True)
            assert pg.locator('#kmwrap .kmain[data-i="boll"]').is_checked(), \
                'BOLL 叠上去了，设置里那个勾却没打上 —— 那就没法再把它拿下来'
            _kset(pg, False)
            assert 'main=ma%2Cboll' in pg.url or 'main=ma,boll' in pg.url, \
                'BOLL 状态没进 URL（刷新就丢）：%s' % pg.url
            _kmain(pg, 'boll', False)
            assert abs(pg.evaluate(NZ) - _no) < 300, \
                '关掉 BOLL 后画布没回到原样'

            # ---- 切副图 KDJ：**副图图例**跟着换（不再是读数）----
            # 🔴 2026-09-15 起指标值从跟着光标的浮窗搬到了副图左上角
            #   （用户："MACD的信息应该放在MACD的左上角，数字跟随变化"）。
            #   原来这条查的是"读数里出现 K/D/J" —— **被改动作废**的判据。
            #   ★ 改成拦 `fillText`：直接验副图图例画了什么，
            #     比数像素准（那条带子里混着柱子与曲线，图例会被淹没）。
            # ★ 换指标的入口也变了：副图现在是**槽位**，每个左上角一个下拉框
            #   （用户："副图左上角是一个下拉框，可以选择显示什么指标"）。
            pg.locator('#kslots .kssel[data-j="1"]').select_option('kdj')
            pg.wait_for_timeout(1800)
            kdj = pg.evaluate("""() => {
                const cv = document.getElementById('kcv');
                const g = cv.getContext('2d');
                const orig = g.fillText.bind(g);
                const seen = [];
                g.fillText = function (t, x, y) { seen.push(String(t));
                                                  return orig(t, x, y); };
                try { drawKChart(cv, {bars: BARS, subs: kSubs()}); }
                finally { g.fillText = orig; }
                return seen.filter(t => /^(K|D|J)\\s/.test(t.toUpperCase()));}""")
            assert len(kdj) >= 2, \
                '切 KDJ 之后副图图例没画出 K/D/J（实得 %r）' % kdj
            assert any(any(ch.isdigit() for ch in t) for t in kdj), \
                '副图 KDJ 图例只有名字没有值：%r' % kdj
            tip2 = pg.locator('#ktip').inner_text() if \
                pg.locator('#ktip').is_visible() else ''
            assert 'DIF' not in tip2, '切了 KDJ，读数里还留着 DIF：%s' % tip2

            # ---- 切区间：根数变了，画布也重画了 ----
            pg.locator('#ktoolbar .rg').first.click()  # 3 月
            pg.wait_for_timeout(2000)
            nz2 = pg.evaluate(NZ)
            assert nz2 > 3000 and nz2 != nz1, \
                '切区间后画布没变（%d -> %d）' % (nz1, nz2)

            # ---- 切复权：说明也要跟着换 ----
            _kfq(pg, 'hfq')
            pg.wait_for_timeout(2000)
            note = pg.locator('#body .lvsec').first.inner_text()
            assert '后复权' in note and '跨期' in note, \
                '没说明两种复权的区别（除权日假跌幅）：%s' % note[-160:]

            # ---- 自选星：点了要真进自选 ----
            import json as _json
            import urllib.request
            st = pg.locator('#body .lvhead .star').first
            st.click()
            pg.wait_for_timeout(1200)
            wl = _json.loads(urllib.request.urlopen(
                'http://127.0.0.1:%d/api/watchlist' % port, timeout=30).read())
            assert any(x['code'] == '601857.XSHG' for x in wl.get('rows') or []), \
                '点了星但没进自选：%s' % wl
            st.click()                                  # 点回去，别留脏数据
            pg.wait_for_timeout(1000)

            # ---- 旧 hash 链接要还能用（跳到新页）----
            pg.goto('http://127.0.0.1:%d/#/stock/600519.XSHG' % port,
                    wait_until='networkidle')
            pg.wait_for_selector('#kcv', timeout=30000)
            pg.wait_for_timeout(1500)
            assert '/stock.html' in pg.url and '600519' in pg.url, \
                '旧 hash 链接没跳到独立页：%s' % pg.url
            assert '贵州茅台' in pg.locator('#pg').inner_text(), '第二只票没渲染'
            assert pg.evaluate(NZ) > 5000, '第二只票的 K 线没画出来'

            br.close()
            assert not errs, '页面有运行时错误：%s' % errs[:3]
            return ('独立页 /stock.html：顶栏导航高亮、页上无 SQL 框、'
                    '搜索→键盘选中→跳转、8 个分区齐全、板块 chip 链到板块页、'
                    'Canvas 真有 %d 个像素、十字光标读出 OHLC+MA+MACD、'
                    '均线图例带色块与当日值（≥4 色）、BOLL 默认关且开关改'
                    '画布与 URL、'
                    '切 KDJ 后读数跟着换、切区间画布变、切后复权有说明、'
                    '点星真进自选、旧 hash 链接跳新页' % nz1)
    finally:
        sv.ALLOW_LIVE, wlmod.LIVE = old_live, old_dir
        shutil.rmtree(tmpwl, ignore_errors=True)
        httpd.shutdown()


@case('盘面：涨跌家数自洽 / 分档不重不漏 / 回看任意一天', tag='fast')
def t_market():
    """盘面的口径错了不报错，只是数不对。三处自证：

    1. **涨 + 跌 + 平 == 总数**，且分档家数之和也等于总数 —— 分档边界
       写重叠或留缝隙，总数就对不上，而页面上看着一切正常。
    2. **只算正常上市 / ST / *ST** —— 把退市整理期和状态为空的混进来，
       涨跌家数就偏。用"直接数一遍"独立复算。
    3. **周末要落到最近的有数据日**，并把实际用的日期回给页面 ——
       直接按等号查会返回空，而"空"看起来像"那天全市场没成交"。
    """
    import duckdb

    from assay import market as mk
    o = mk.overview()
    assert o['up'] + o['down'] + o['flat'] == o['n'], \
        '涨跌平之和 %d 不等于总数 %d' % (o['up'] + o['down'] + o['flat'], o['n'])
    tot = sum(b['n'] for b in o['buckets'])
    assert tot == o['n'], \
        ('分档之和 %d ≠ 总数 %d —— 档位边界重叠或留了缝隙' % (tot, o['n']))
    # 档位必须单调、首尾覆盖到 ±100
    lo = [a for a, _b, _l in mk.BUCKETS]
    hi = [b for _a, b, _l in mk.BUCKETS]
    assert lo == sorted(lo) and hi == sorted(hi), '档位没排序'
    assert lo[0] <= -100 and hi[-1] >= 100, '首尾档没覆盖极值'
    for i in range(len(lo) - 1):
        assert hi[i] == lo[i + 1], \
            '第 %d 档与下一档不衔接（%s vs %s）—— 会漏或重复' % (i, hi[i], lo[i + 1])

    # 独立复算：直接数一遍，口径必须一致
    c = duckdb.connect(':memory:')
    p = mk.panel()
    n2, up2, lu2 = c.execute("""
        SELECT count(*), sum(CASE WHEN change_pct>0 THEN 1 ELSE 0 END),
               sum(CASE WHEN is_limit_up THEN 1 ELSE 0 END)
        FROM %s WHERE date = DATE '%s'
          AND public_status IN ('正常上市','ST','*ST') AND close_bfq IS NOT NULL"""
        % (p, o['date'])).fetchone()
    assert (n2, up2, lu2) == (o['n'], o['up'], o['limit_up']), \
        '独立复算不一致：%s vs %s' % ((n2, up2, lu2), (o['n'], o['up'], o['limit_up']))
    # 把状态放开会变多 —— 证明筛选真的在起作用（不是恒等式）
    n3 = c.execute("SELECT count(*) FROM %s WHERE date = DATE '%s'"
                   " AND close_bfq IS NOT NULL" % (p, o['date'])).fetchone()[0]
    assert n3 > o['n'], \
        '放开 public_status 后家数没变多（%d vs %d）—— 筛选没生效？' % (n3, o['n'])

    # 榜单：涨幅榜必须降序、跌幅榜升序，且每行都有代码与名称
    g = o['ranks']['gainers']['rows']
    l_ = o['ranks']['losers']['rows']
    assert g and l_, '榜单是空的'
    gv = [x['change_pct'] for x in g]
    lv = [x['change_pct'] for x in l_]
    assert gv == sorted(gv, reverse=True), '涨幅榜没降序'
    assert lv == sorted(lv), '跌幅榜没升序'
    assert gv[0] > lv[0], '涨幅榜第一名居然不比跌幅榜第一名高'
    for x in g:
        assert x['code'] and '.' in x['code'], '榜单行缺代码'
    # 行业榜等权平均要按降序，且家数之和 <= 总数（有些票没有行业）
    inds = o['industries']
    av = [x['avg_change'] for x in inds]
    assert av == sorted(av, reverse=True), '行业榜没按平均涨幅降序'
    assert sum(x['n'] for x in inds) <= o['n'], '行业家数之和超过总数'

    # ---- 回看任意一天：周末落到最近的有数据日，并说出来 ----
    import datetime as _dt
    d = _dt.date.fromisoformat(o['date'])
    sat = d
    while sat.weekday() != 5:                 # 找一个周六
        sat -= _dt.timedelta(days=1)
    o2 = mk.overview(sat.isoformat())
    assert o2['date'] != sat.isoformat(), '周六居然有行情？'
    assert o2['asked'] == sat.isoformat(), \
        '没把"你选的那天"回给页面 —— 人会以为看的是自己选的那天'
    assert o2['date'] < sat.isoformat(), '没落到更早的有数据日'
    assert o2['n'] > 1000, '历史某天的家数不对：%d' % o2['n']
    try:
        mk.overview('1990-01-01')
        raise AssertionError('远早于所有数据的日期应报错')
    except mk.MarketError:
        pass
    return ('涨跌平之和 == 总数 %d；12 档首尾衔接且之和相等；'
            '独立复算一致（放开状态后 %d > %d，证明筛选生效）；'
            '涨幅榜降序 / 跌幅榜升序 / 行业榜按等权降序；'
            '周六 %s 自动落到 %s 并回报 asked'
            % (o['n'], n3, o['n'], sat, o2['date']))


@case('板块：申万与通达信两源 / 成分等权自洽 / 个股归属', tag='fast')
def t_sector():
    """★ 只用【每日同步】的两个源：申万一级（随面板，带 PIT）与通达信板块。

    🔴 `raw/hf/` 下那份同花顺概念**不用** —— `last_fetched` 停在 2026-04-28、
      没接进 `sync_daily.sh`。用它会给出四个月前的成分，而页面上看着像今天的。
      这条用例顺手钉住"代码里没有引用那份数据"。
    """
    import os

    from assay import market as mk
    ks = {k['kind'] for k in (
        __import__('assay.server', fromlist=['x']).api_sector_kinds({})['kinds'])}
    assert 'sw' in ks, '缺申万'
    assert 'concept' in ks, '缺通达信概念板块'

    sw = mk.sector_list(kind='sw')
    assert 25 <= len(sw['rows']) <= 40, '申万一级应是 31 个左右：%d' % len(sw['rows'])
    av = [x['avg_change'] for x in sw['rows']]
    assert av == sorted(av, reverse=True), '板块榜没按等权涨幅降序'

    cc = mk.sector_list(kind='concept')
    assert len(cc['rows']) > 100, '通达信概念板块太少：%d' % len(cc['rows'])
    # 成分等权涨幅要能独立复算
    top = cc['rows'][0]
    m = mk.sector_members(top['code'], kind='concept')
    assert m['n'] == top['n'], \
        '榜上写 %d 只，成分表却是 %d 只' % (top['n'], m['n'])
    cps = [x['change_pct'] for x in m['rows'] if x['change_pct'] is not None]
    calc = sum(cps) / len(cps)
    assert abs(calc - top['avg_change']) < 0.02, \
        '等权涨幅复算不一致：%s vs %s' % (calc, top['avg_change'])
    # 成分表按涨跌幅降序
    assert cps == sorted(cps, reverse=True), '成分表没按涨跌幅降序'

    # 申万成分：数量要和榜上一致
    m2 = mk.sector_members(sw['rows'][0]['code'], kind='sw')
    assert m2['n'] == sw['rows'][0]['n'], \
        '申万成分数不一致：%d vs %d' % (m2['n'], sw['rows'][0]['n'])
    assert len(m2['rows']) == m2['n'], \
        ('成分表只给了 %d 行却报 n=%d —— **悄悄截断**：少的那部分你不知道，'
         '而结论已经下了（2026-09-14 实测：默认上限 300，行业涨到 478 只之后'
         '榜上写 478、表给 300、n 也报 300，三处自洽地说了个谎）'
         % (len(m2['rows']), m2['n']))
    assert m2.get('truncated') is False, \
        '这一次不该截断，truncated=%r' % m2.get('truncated')
    # 🔴 **真截断时必须说得出来** —— 只把上限拉大是不够的，数据还会长。
    #   构造：显式给一个小 limit，`n` 必须仍是真实总数、`truncated` 为真。
    m3 = mk.sector_members(sw['rows'][0]['code'], kind='sw', limit=5)
    assert m3['n'] == m2['n'] and len(m3['rows']) == 5, \
        ('截断之后 n 该仍是真实总数（%d），行数才是 5 —— 实得 n=%d 行=%d；'
         '拿 len(rows) 当 n 就是上面那个谎的来源'
         % (m2['n'], m3['n'], len(m3['rows'])))
    assert m3.get('truncated') is True, \
        '截断了却没标 truncated —— 页面就没法说出"只给了前 N 只"'

    # 个股归属：代码几种写法都要认（不归一就查不到，报"面板里没有"）
    for raw in ('601857', '601857.SH', 'sh601857', '601857.XSHG'):
        ss = mk.stock_sectors(raw)
        assert ss['code'] == '601857.XSHG', '%r 没归一：%s' % (raw, ss['code'])
        assert ss['sw'] and ss['sw']['name'], '没给申万归属'
        assert ss['blocks'], '一只大盘股不可能不属于任何板块'
    try:
        mk.stock_sectors('99')
        raise AssertionError('认不出的代码应报错')
    except mk.MarketError:
        pass

    # 🔴 代码里不许引用 raw/hf/ 那份不同步的概念数据
    src = open(os.path.join(REPO,
                            'assay', 'market.py'), encoding='utf-8').read()
    code_only = '\n'.join(ln for ln in src.split('\n')
                          if not ln.strip().startswith('#'))
    assert 'concept_ths' not in code_only, \
        ('market.py 引用了 raw/hf 的同花顺概念 —— 它 last_fetched 停在 '
         '2026-04-28、没接进每日同步，会给出四个月前的成分而页面上看着像今天的')
    return ('两源：申万 %d 个 + 通达信 %d 类（概念 %d 个）；'
            '成分等权涨幅复算一致（%s：榜 %.2f%% / 复算 %.2f%%）；'
            '成分数一致；个股归属 4 种写法都认；代码里没引用不同步的同花顺概念'
            % (len(sw['rows']), len(ks) - 1, len(cc['rows']),
               top['name'], top['avg_change'], calc))


@case('副图指标 / 事件 / 同业 / 联动 / 对比', tag='fast')
def t_stock_ext():
    """指标算错不报错，只是曲线不对 —— 所以每个都要能独立复算。

    ★ KDJ / BOLL 刻意用**行情软件口径**（KDJ 通用平滑、BOLL 总体标准差），
      不是教科书口径 —— 与通达信/同花顺对不上会让人以为数据错了。
    """
    from assay import stock as st

    # ---- 指标：预热 + 独立复算 ----
    ind = st.indicators('601857.SH', n=120)
    rows = ind['rows']
    assert len(rows) == 120
    assert ind['warmup_dropped'] == st.IND_WARM, \
        '预热应丢掉 %d 根：%s' % (st.IND_WARM, ind['warmup_dropped'])
    # ★ 第一根就该有值 —— 这正是预热的意义。没预热的话头部全是 None，
    #   而那不报错，只是曲线前面缺一截。
    for k in ('dif', 'dea', 'macd', 'k', 'd', 'jj', 'rsi6', 'rsi24', 'mb', 'ub'):
        assert rows[0][k] is not None, '第一根的 %s 是空的 —— 预热没生效' % k
    last = rows[-1]
    # MACD = (DIF − DEA) × 2（国内行情软件口径，不是 DIF−DEA）
    assert abs(last['macd'] - (last['dif'] - last['dea']) * 2) < 0.01, \
        'MACD 不是 (DIF−DEA)×2：%s' % last
    # J = 3K − 2D
    # ★ 容差 0.03 不是随手放宽：k/d/jj **返回时都已四舍五入到 2 位**，
    #   而这里拿【已舍入的】k、d 去重算 3K−2D，误差上界就是
    #   3×0.005 + 2×0.005 = 0.025。原来写 0.02 **小于这个上界**，
    #   于是能不能通过取决于当天的小数 —— 2026-09-11 实测 601857
    #   k=50.28 d=52.29 jj=46.24 而 3k−2d=46.26，差正好 0.02 而挂掉。
    #   **失败的是断言不是产品**（同 `#d_hold .note` 那条）。
    assert abs(last['jj'] - (3 * last['k'] - 2 * last['d'])) < 0.03, \
        'J 算错：jj=%s 而 3K−2D=%s' % (last['jj'], 3 * last['k'] - 2 * last['d'])
    for k in ('k', 'd', 'rsi6', 'rsi12', 'rsi24'):
        assert 0 <= last[k] <= 100, '%s 越界：%s' % (k, last[k])
    # BOLL：中轨 == 20 日均值；上下轨对称
    cl = [r['close'] for r in rows[-20:]]
    assert abs(last['mb'] - sum(cl) / 20) < 0.02, 'BOLL 中轨不是 20 日均值'
    assert abs((last['ub'] - last['mb']) - (last['mb'] - last['lb'])) < 0.01, \
        'BOLL 上下轨不对称'
    assert last['lb'] < last['mb'] < last['ub'], 'BOLL 轨道顺序不对'
    # 总体标准差（除 N）而不是样本标准差（除 N−1）—— 与行情软件一致
    mu = sum(cl) / 20
    sd_pop = (sum((x - mu) ** 2 for x in cl) / 20) ** 0.5
    sd_smp = (sum((x - mu) ** 2 for x in cl) / 19) ** 0.5
    assert abs((last['ub'] - mu) / 2 - sd_pop) < 0.01, 'BOLL 没用总体标准差'
    assert abs(sd_pop - sd_smp) > 1e-9, '两种标准差恰好相等，这条断言无效'

    # ---- 事件：日期倒序，单位对 ----
    ev = st.events('601088.SH')
    ds = [e['date'] for e in ev['events']]
    assert ds == sorted(ds, reverse=True), '事件没按日期倒序'
    kinds = {e['kind'] for e in ev['events']}
    assert {'xr', 'fin'} <= kinds, '缺除权或财报事件：%s' % kinds
    # 股本单位是万股 —— 换算后应是【亿】级（神华 216 亿股）
    sh = [e for e in ev['events'] if e['kind'] == 'share' and e.get('share_total')]
    if sh:
        assert sh[0]['share_total'] > 1e9, \
            ('总股本换算错了（share_change.share_total 单位是万股）：%s'
             % sh[0]['share_total'])
        assert '亿' in sh[0]['detail'], '没换算成人看得懂的量级'
    # 解禁比例常为 NULL —— 必须不显示成 "占 0%"
    unl = [e for e in ev['events'] if e['kind'] == 'unlock']
    for e in unl:
        if e.get('ratio') is None:
            assert '占 0' not in e['detail'], \
                ('缺值显示成了"占 0%%"—— 那不是"占比很小"，是根本没有这个数：%s'
                 % e['detail'])

    # ---- 同业 ----
    pe = st.peers('601857.SH', n=10)
    assert pe['industry']['name'], '没给行业'
    mv = [x['floatmv'] or 0 for x in pe['rows']]
    assert mv == sorted(mv, reverse=True), '同业没按流通市值降序'
    assert pe['rank'] == 1, '中国石油在石油石化里流通市值应排第一：%s' % pe['rank']
    assert any(x['code'] == '601857.XSHG' for x in pe['rows']), '同业里没有它自己'

    # ---- 联动 ----
    lk = st.links('601857.SH')
    assert 'positions' in lk and 'runs' in lk
    assert lk['runs'], '红利策略买过中国石油，回测联动不该是空的'
    r0 = lk['runs'][0]
    for k in ('run_id', 'strategy', 'n_trades', 'avg_ret'):
        assert k in r0, '回测联动缺 %s' % k
    assert r0['n_trades'] > 0

    # ---- 多股对比：一律后复权 + 并集对齐 ----
    cp = st.compare(['601857.SH', '601088.SH'], n=120)
    assert cp['fq'] == 'hfq', '对比必须后复权 —— 不复权跨除权日有假跌幅'
    assert len(cp['series']) == 2 and len(cp['dates']) == cp['n']
    for s in cp['series']:
        assert len(s['ret']) == cp['n'], '曲线长度与日期数不一致'
        v = [x for x in s['ret'] if x is not None]
        assert v and abs(v[0]) < 1e-9, '起点没归零：%s' % v[0]
    # 后复权的区间涨幅要高于不复权（分红被除掉了）
    kb = st.kline('601088.SH', n=120, fq='bfq')['bars']
    rb = kb[-1]['close'] / kb[0]['close'] - 1
    rh = [s for s in cp['series'] if s['code'] == '601088.XSHG'][0]['ret'][-1]
    assert rh > rb, '对比用的不是后复权（%.4f vs 不复权 %.4f）' % (rh, rb)
    try:
        st.compare(['1', '2', '3', '4', '5', '6', '7'])
        raise AssertionError('超过 6 只应被拒')
    except st.StockError:
        pass
    try:
        st.compare([])
        raise AssertionError('空列表应被拒')
    except st.StockError:
        pass
    return ('指标预热 %d 根、第一根就有全部值；MACD=(DIF−DEA)×2、J=3K−2D、'
            'BOLL 中轨=20日均值且用总体标准差（与行情软件一致）；'
            '事件倒序且股本按万股换算成 %s、解禁缺值不显示成 0%%；'
            '同业按市值降序且它排第 1；回测联动 %d 次；'
            '对比后复权 %.2f%% > 不复权 %.2f%% 且起点归零'
            % (ind['warmup_dropped'], (sh[0]['detail'].split('总股本 ')[-1]
                                       if sh else '—'),
               len(lk['runs']), rh * 100, rb * 100))


@case('买点清单：目标价/股息率互算 + 到价判定 + 提醒去重', tag='fast')
def t_alerts():
    """把用户那张 Excel 搬进来的那张表。**它不是策略**（没有回测、不下单）。

    这一条守四件事：
      ① 两种输入方式互算：填价出股息率、填率出目标价，且**存的是你填的那个**
      ② 分红的口径（`bonus_ratio_rmb` 是每【10】股 —— 不除 10 会大 10 倍），
         且它**不存不手填**：新公告一到目标价要自动跟着变
      ③ 到价 / 接近 / 还差多少的判定
      ④ 提醒**一天只发一次**（不然价格在阈值上下抖一抖就是几十条通知）
    """
    import shutil
    import tempfile

    from assay import alerts as al
    from assay import server as sv
    old_live, old_dir = sv.ALLOW_LIVE, al.LIVE
    sv.ALLOW_LIVE = True
    tmp = tempfile.mkdtemp()
    al.LIVE = tmp                    # ★ 不往真账本里写测试数据
    # 🔴 外部分红接口要打桩：不打的话 selftest 会真去打东财，
    #    而"一天一次"的节流恰恰要在这里被验证（真打就验不了）。
    #    桩里放的是**实测抓回来的真行**（含两处"标注与本地不同"的）。
    EXT_HIT = []
    _o_fetch, _o_extpath = al._ext_fetch, al.ext_path
    _extcache = os.path.join(tmp, 'div_ext.json')
    al.ext_path = lambda root=None: _extcache
    EXT_ROWS = {'600900.XSHG': [
        {'report_date': '2025-12-31', 'per_share': 0.79,
         'plan_pub': '2026-04-30', 'progress': '实施分配', 'src': '东财'},
        {'report_date': '2025-09-30', 'per_share': 0.21,
         'plan_pub': '2025-12-31', 'progress': '实施分配', 'src': '东财'},
        {'report_date': '2024-12-31', 'per_share': 0.733,
         'plan_pub': '2025-04-30', 'progress': '实施分配', 'src': '东财'},
        # ★ 同一次 0.21（公告 2024-12-14）：本地标 2024-09-30、东财标 2024-06-30
        {'report_date': '2024-06-30', 'per_share': 0.21,
         'plan_pub': '2024-12-14', 'progress': '实施分配', 'src': '东财'}],
        '000423.XSHE': [
        # ★ 同一次 1.3448（报告期都是 2026-06-30）：本地公告 08-21、东财 04-25
        {'report_date': '2026-06-30', 'per_share': 1.3448,
         'plan_pub': '2026-04-25', 'progress': '实施分配', 'src': '东财'}]}
    al._ext_fetch = lambda cs, day=None: (
        EXT_HIT.append(sorted(cs))
        or {c: v for c, v in EXT_ROWS.items() if c in set(cs)})
    try:
        # ---- 1) 分红（实际已公告）：每 10 股 -> 每股 ----
        #   🔴 bonus_ratio_rmb 是"每 10 股派现"。不除 10 的话分红大 10 倍，
        #     目标价跟着小 10 倍，而它不报错 —— 只是那一行永远不会触发。
        sg = al.suggest_div(['600900', '600036', '600690', '600795', '000423'])
        cj = sg.get('600900.XSHG') or {}
        assert 0.5 < (cj.get('per_share') or 0) < 2.0, \
            ('长江电力每股分红应在 1 元附近（与用户手填的 1 一致），实得 %s'
             ' —— 大 10 倍就是 bonus_ratio_rmb 没除 10' % cj.get('per_share'))
        assert (sg.get('600036.XSHG') or {}).get('per_share'), '招行没取到分红'
        # 🔴 默认口径必须是【最近一个完整会计年度合计】，不是近 365 天。
        #   逐只对用户手填的那张表（他手填的就是这个口径）—— 半年派的公司上
        #   r365 会漏掉中期分红：
        #     海尔智家 手填 1.15 / FY 1.1607 / r365 0.8915（少 26%）
        #     国电电力 手填 0.24 / FY 0.241  / r365 0.141 （少 41%）
        #   而它不报错，只是目标价算高、那一行永远不会触发。
        HAND = {'600690.XSHG': 1.15, '600795.XSHG': 0.24,
                '600036.XSHG': 2.016, '600900.XSHG': 1.0,
                '000423.XSHE': 2.7}
        for c, want in HAND.items():
            got = (sg.get(c) or {}).get('per_share')
            assert got and abs(got / want - 1) < 0.02, \
                ('%s 的分红应≈用户手填的 %s（最近完整年度合计），实得 %s'
                 % (c, want, got))
            assert '年度合计' in ((sg.get(c) or {}).get('src') or ''), \
                '默认口径不是"年度合计"：%s' % (sg.get(c) or {}).get('src')
        # 两个口径都要给页面（让人挑），且在半年派的票上确实不同
        for c in ('600690.XSHG', '600795.XSHG'):
            fy = sg[c]['fy']['per_share']
            r365 = sg[c]['r365']['per_share']
            assert abs(fy / r365 - 1) > 0.2, \
                ('%s 上两个口径应有明显差别（这条断言就是防止有人把默认'
                 '悄悄换回 r365）：fy %s vs r365 %s' % (c, fy, r365))
            assert sg[c]['fy'].get('detail'), '年度合计没给逐笔明细'

        # ---- 2) 两种输入方式互算，且存的是【填的那个】----
        al.set_row('000423', [{'by': 'price', 'v': 49},
                              {'by': 'price', 'v': 45}], note='东阿阿胶')
        al.set_row('601318', [{'by': 'yield', 'v': 6}])        # 6 == 6%
        cur = {x['code']: x for x in al.current()}
        assert cur['000423.XSHE']['tiers'][0] == {'by': 'price', 'v': 49.0}, \
            '填的是价，存的却不是：%s' % cur['000423.XSHE']['tiers'][0]
        assert cur['601318.XSHG']['tiers'][0] == {'by': 'yield', 'v': 0.06}, \
            '6 应当作 6% 存成 0.06：%s' % cur['601318.XSHG']['tiers'][0]
        v = al.valued()
        r1 = [x for x in v['rows'] if x['code'] == '000423.XSHE'][0]
        r2 = [x for x in v['rows'] if x['code'] == '601318.XSHG'][0]
        # ★ 分红是**实际已公告**的（不是手填的 2.7）—— 先把它钉住，
        #   再验换算。这样换算错和分红错能分开定位。
        assert abs(r1['div'] - 2.7056) < 1e-4, \
            ('东阿阿胶的实际分红应是 2.7056（FY2025 = 1.2701 + 1.4355），'
             '实得 %s' % r1['div'])
        assert '年度合计' in (r1['div_src'] or ''), \
            '分红来源没标出来：%s' % r1['div_src']
        t49 = [t for t in r1['tiers'] if t['price'] == 49][0]
        assert abs(t49['yield'] - r1['div'] / 49) < 1e-6, \
            '%s/49 算错了：%.4f' % (r1['div'], t49['yield'])
        assert abs(r2['tiers'][0]['price'] - r2['div'] / 0.06) < 0.01, \
            '分红 %s / 目标股息率 6%% 应算出目标价 %.2f，实得 %s' \
            % (r2['div'], r2['div'] / 0.06, r2['tiers'][0]['price'])
        # 🔴 分红【不能手填、也不存在账本里】—— "预计分红"没有价值
        #   （猜出来的目标价看着和真的一样，而它错在你不会回头检查的地方），
        #   而存一份就会过期。判据：账本那一行里没有 div 字段。
        _rec = [r for r in al.log() if r.get('code') == '601318.XSHG'][-1]
        assert 'div' not in _rec, \
            '账本里存了 div —— 它会过期，而过期的目标价看着完全正常：%s' % _rec
        assert 'div' not in al.current()[0], \
            'current() 又把 div 读出来了（老记录里可能还有这个字段）'
        try:
            al.set_row('601318', 2.7, [{'by': 'yield', 'v': 6}])
            raise AssertionError('set_row 不该再接分红这个参数')
        except TypeError:
            pass
        # 🔴 新公告一到，目标价要**自动跟着变** —— 这是"不存"换来的东西。
        #   把桩里那只票的分红翻倍，同一个"目标股息率 6%"的档目标价必须翻倍。
        _p0 = [x for x in al.valued()['rows']
               if x['code'] == '601318.XSHG'][0]['tiers'][0]['price']
        EXT_ROWS['601318.XSHG'] = [
            {'report_date': '2025-12-31', 'per_share': 5.4,
             'plan_pub': '2026-04-30', 'progress': '实施分配', 'src': '东财'}]
        if os.path.isfile(al.ext_path()):
            os.remove(al.ext_path())          # 装成新的一天
        _p1 = [x for x in al.valued()['rows']
               if x['code'] == '601318.XSHG'][0]['tiers'][0]['price']
        assert _p1 > _p0 * 1.5, \
            ('分红涨了，"目标股息率 6%%"那档的目标价没跟着涨（%s -> %s）——'
             ' 说明分红被存了下来或者被缓存住了' % (_p0, _p1))
        EXT_ROWS.pop('601318.XSHG')
        if os.path.isfile(al.ext_path()):
            os.remove(al.ext_path())

        # ---- 3) 到价 / 接近 / 还差多少 ----
        #   用真现价推出三档，判定必须落在预期的那一格上。
        px = [x for x in al.valued()['rows'] if x['code'] == '000423.XSHE'][0]
        p0 = px['price']
        assert p0, '取不到现价，没法验判定'
        al.set_row('000423', [
            {'by': 'price', 'v': round(p0 * 1.05, 2)},      # 已跌破
            {'by': 'price', 'v': round(p0 * 0.99, 2)},      # 差 1% -> 接近
            {'by': 'price', 'v': round(p0 * 0.60, 2)},      # 差 40% -> 远
        ])
        r = [x for x in al.valued()['rows'] if x['code'] == '000423.XSHE'][0]
        assert [t['state'] for t in r['tiers']] == ['hit', 'near', 'far'], \
            '三档的状态判错了：%s' % [(t['price'], t['state']) for t in r['tiers']]
        assert r['state'] == 'hit' and r['hit'] == 0, \
            '整行状态该是"到价 · 第 1 档"：%s' % (r['state'], r['hit'])
        # 目标价必须【从高到低】：价高的先触发，与表格里的排法一致
        ps = [t['price'] for t in r['tiers']]
        assert ps == sorted(ps, reverse=True), '档位没按目标价降序：%s' % ps
        # 「还要跌多少」= 目标/现价 − 1（负数）。gap 存到 6 位小数，
        # 所以比的是 1e-5 而不是精确相等。
        _g = r['tiers'][2]
        assert abs(_g['gap'] - (_g['price'] / p0 - 1)) < 1e-5, \
            'gap 不是"目标/现价 − 1"：%s vs %s' % (
                _g['gap'], _g['price'] / p0 - 1)

        # ---- 4) 提醒一天只发一次 ----
        n1 = al.check_fire()
        assert n1, '到价了却没有任何提醒'
        assert all(x['code'] == '000423.XSHE' for x in n1), \
            '提醒里混进了别的票：%s' % [x['code'] for x in n1]
        assert {x['kind'] for x in n1} == {'hit', 'near'}, \
            '到价与接近都该提醒一次：%s' % [x['kind'] for x in n1]
        n2 = al.check_fire()
        assert n2 == [], \
            ('同一 (票, 档, 类型) 一天只该提醒一次 —— 轮询是每分钟一轮，'
             '不去重的话价格在阈值上下抖一抖就是几十条通知，'
             '而通知一多就没人看了：%s' % n2)
        # 去重依据要**落盘**：重启后不该把今天提醒过的又提醒一遍
        assert os.path.isfile(os.path.join(tmp, al.FIRED)), \
            '触发记录没落盘 —— 那重启后会重复提醒'

        # ---- 5) append-only：改一行是追加，当前清单靠重放 ----
        n_log = len(al.log())
        al.set_row('000423', [{'by': 'price', 'v': 40}])
        assert len(al.log()) == n_log + 1, '改一行应当是追加一条'
        assert len(al.current()) == 2, '重放出的清单该还是 2 行'
        assert [t['v'] for t in
                {x['code']: x for x in al.current()}['000423.XSHE']['tiers']] \
            == [40.0], '重放出来的不是最后那一条'
        al.remove('000423')
        assert len(al.current()) == 1 and len(al.log()) == n_log + 2, \
            '删一行也该是追加一条（历史仍在）'

        # ---- 6) 非法输入要被拒（而不是存个看着正常的错值）----
        for bad, why in (
                (('99', [{'by': 'price', 'v': 10}]), '认不出的代码'),
                (('600900', []), '一档都没填'),
                (('600900', [{'by': 'yield', 'v': 60}]), '股息率 60%'),
                (('600900', [{'by': 'price', 'v': -1}]), '负的目标价'),
                (('600900', [{'by': 'nope', 'v': 1}]), 'by 不合法')):
            try:
                al.set_row(*bad)
                raise AssertionError('%s 应被拒' % why)
            except al.AlertError:
                pass

        # ---- 6b) 外部分红：一天只抓一次，已经有今天的就不再调 ----
        EXT_HIT.clear()
        if os.path.isfile(al.ext_path()):
            os.remove(al.ext_path())      # 装成"今天还没抓过"
        al.suggest_div(['600900'])
        assert EXT_HIT == [['600900.XSHG']], \
            '第一次没去抓外部分红：%s' % EXT_HIT
        d2 = al.suggest_div(['600900'])
        assert len(EXT_HIT) == 1, \
            ('同一天同一只票抓了 %d 次 —— 应该只抓一次（多开几个标签页'
             '不该变成一串请求）' % len(EXT_HIT))
        assert d2['600900.XSHG']['ext']['called'] is False, \
            '第二次应该报"没调用"'
        assert d2['600900.XSHG']['ext']['fetched'], '没记下抓取日期'
        # 新加进来的票要立刻抓（它一条都没有）—— 同 _rt_ensure 的思路
        al.suggest_div(['600900', '000423'])
        assert EXT_HIT[-1] == ['000423.XSHE'], \
            '只该补抓没抓过的那一只，实际抓了 %s' % EXT_HIT[-1]
        # 手动刷新（force）要能绕过节流
        al.ext_div(['600900'], force=True)
        assert EXT_HIT[-1] == ['600900.XSHG'], 'force 没绕过一天一次'
        # 🔴 缓存不许写进 std/dividend.parquet —— 那是 loader 链的产物，
        #   被外部数据污染之后"面板与 std 不一致"不报错
        assert 'std' not in _o_extpath(), \
            '外部缓存落到 std/ 里去了：%s' % _o_extpath()

        # ---- 6c) 合并去重：同一次分红，两个源的【标注可能不一样】----
        #   🔴 两条都是实测踩出来的，按单一键去重都会双计而不报错：
        #     ① 长江电力那次 0.21（公告 2024-12-14）：本地报告期 2024-09-30、
        #        东财 2024-06-30 —— 按报告期去重 FY2024 从 0.943 变 1.153(+22%)
        #     ② 东阿阿胶 2026 中期 1.3448：本地公告 2026-08-21（中期董事会预案）、
        #        东财 2026-04-25（年报里先披露的计划）—— 按公告日去重会双计
        loc = al._local_rows(['600900.XSHG'], day='2025-06-30')
        mg = al._merge_rows(loc, EXT_ROWS)['600900.XSHG']
        fy24, _ = al._agg(mg, day='2025-06-30')
        assert abs(fy24['per_share'] - 0.943) < 1e-6, \
            ('长江电力 FY2024 应是 0.943（0.21+0.733），实得 %s —— '
             '差的那 0.21 是同一次分红被两个源的报告期标注拆成了两条'
             % fy24['per_share'])
        assert fy24['year'] == 2024, 'as-of 2025-06-30 的年度桶应是 2024'
        n26 = [x for x in al._merge_rows(al._local_rows(['000423.XSHE']),
                                         EXT_ROWS)['000423.XSHE']
               if x['report_date'] == '2026-06-30']
        assert len(n26) == 1, \
            ('东阿阿胶 2026-06-30 那次分红被拆成了 %d 条（公告日标注不同）：%s'
             % (len(n26), n26))
        # as-of：外部缓存里比 as-of 更新的公告一律砍掉（否则是未来函数）
        fy_now, _ = al._agg(mg)
        assert fy_now['year'] >= 2025, 'as-of 今天时该用更新的年度桶'

        # ---- 6d) 外部源挂了要退回本地，而不是让整页打不开 ----
        def _boom(cs, day=None):
            raise RuntimeError('模拟东财挂了')
        _keep = al._ext_fetch
        al._ext_fetch = _boom
        if os.path.isfile(al.ext_path()):
            os.remove(al.ext_path())
        dz = al.suggest_div(['600900'])
        assert dz['600900.XSHG']['per_share'], '外部挂了就取不到分红了'
        assert dz['600900.XSHG']['ext']['err'], '外部失败没记下来（页面要显示）'
        al._ext_fetch = _keep

        # ---- 7) 抓取范围要带上它 ----
        #   不抓价，"到价提醒"就什么都不会发生。
        assert set(al.codes()) <= set(sv._rt_codes()), \
            '买点清单里的票没进抓取范围 —— 那它永远不会提醒'

        # ---- 8) 只读模式接口层要拒（不能只靠页面）----
        sv.ALLOW_LIVE = False
        assert (sv.api_alerts_act({}, {'act': 'set', 'code': '600900',
                                       'tiers': [{'by': 'price', 'v': 20}]})
                or {}).get('error'), '只读模式下应拒绝改买点清单'
        sv.ALLOW_LIVE = True
        return ('每 10 股 -> 每股（长江电力 %.2f，与用户手填的 1 一致）；'
                '分红不存不手填、新公告一到目标价自动跟着变；'
                '默认口径=最近完整年度合计，逐只对上用户手填的 5 只'
                '（半年派的海尔/国电上 r365 会少 26%%/41%%）；'
                '分红取实际已公告(东阿阿胶 2.7056)；填价出息率、填率出目标价，'
                '存的是填的那个；三档判定 hit/near/far'
                '且按目标价降序；提醒一天只一次且去重落盘；append-only 改一行'
                '是追加、重放取最后一条；5 类非法输入被拒；'
                '抓取范围含买点清单；只读模式拒写；'
                '外部分红(东财 RPT_SHAREBONUS_DET)一天只抓一次、新票立刻补抓、'
                'force 可绕过、缓存不落 std/、挂了退回本地；'
                '两个源的标注不同也不双计（长江电力报告期 09-30 vs 06-30 -> '
                'FY2024 仍 0.943 而不是 1.153；东阿阿胶公告日 08-21 vs 04-25 '
                '-> 2026 中期仍 1 条）'
                % (cj.get('per_share') or 0))
    finally:
        sv.ALLOW_LIVE, al.LIVE = old_live, old_dir
        al._ext_fetch, al.ext_path = _o_fetch, _o_extpath
        shutil.rmtree(tmp, ignore_errors=True)


@case('自选：append-only / 重放出当前池 / 只读拦写', tag='fast')
def t_watchlist():
    """★ 与实盘账本同一套纪律：加/移出/改分组/改备注都是**追加一条**，
    当前状态由**重放**得出。删一条就把"我什么时候加的、为什么"抹掉了。
    """
    import shutil
    import tempfile

    from assay import live as lv
    from assay import realtime as rtm
    from assay import server as sv
    from assay import watchlist as wl
    old_live, old_dir = sv.ALLOW_LIVE, wl.LIVE
    sv.ALLOW_LIVE = True
    tmp = tempfile.mkdtemp()
    wl.LIVE = tmp
    # 🔴 打桩：这个用例会走 `_rt_ensure`（加自选那一刻补抓）。不打桩的话
    #    selftest 会真的去打行情接口 —— 而限流是这条链上唯一的风险。
    HIT = {'snap': [], 'bar': []}
    _o_snap, _o_fetch = rtm.snapshot, rtm.fetch
    _o_sess, _o_day = rtm.in_session, rtm.is_trading_day
    _o_miss = rtm.missing
    rtm.snapshot = lambda cs, root=None: (HIT['snap'].append(list(cs)) or {})
    rtm.fetch = lambda cs, root=None: (
        HIT['bar'].append(list(cs)) or {'bars': 0, 'fail': []})
    rtm.in_session = lambda now=None: True
    rtm.is_trading_day = lambda d=None: True
    sv._RT['miss_at'] = {}
    try:
        assert wl.current() == [] and wl.log() == [], '新目录应是空的'
        wl.act('add', '601857.SH', group='观察', note='页岩气')
        wl.act('add', '600519')
        assert len(wl.current()) == 2
        # 幂等：重复加不报错也不重复追加
        r = wl.act('add', '601857')
        assert r.get('skipped'), '重复加应被跳过（页面上那个星是幂等的）'
        assert len(wl.log()) == 2, '重复加不该追加记录'
        # 改分组 / 备注 都是追加
        wl.act('group', '601857.SH', group='核心')
        wl.act('note', '601857.SH', note='看它的天然气占比')
        cur = {x['code']: x for x in wl.current()}
        assert cur['601857.XSHG']['group'] == '核心', '重放后的分组不对'
        assert '天然气' in cur['601857.XSHG']['note'], '重放后的备注不对'
        assert len(wl.log()) == 4, '应有 4 条日志'
        # 移出：当前池少一个，**日志还在**
        wl.act('remove', '601857.SH')
        assert len(wl.current()) == 1, '移出后当前池应剩 1'
        assert len(wl.log()) == 5, \
            '移出必须是【追加一条 remove】，不是删记录 —— 删了就没法复盘'
        assert any(x['act'] == 'add' and x['code'] == '601857.XSHG'
                   for x in wl.log()), '原来的 add 记录不该消失'
        # 移出后再改会报错（不在池子里）
        for a in ('remove', 'group', 'note'):
            try:
                wl.act(a, '601857.SH')
                raise AssertionError('%s 一个不在池子里的票应报错' % a)
            except wl.WatchError:
                pass
        # 分组统计
        wl.act('add', '000001', group='银行')
        gs = {g['name']: g['n'] for g in wl.groups()}
        assert gs.get('银行') == 1 and gs.get(wl.DEFAULT_GROUP) == 1, gs
        assert len(wl.current('银行')) == 1, '按分组过滤没生效'
        # 带行情：查不到的不静默丢掉
        wl.act('add', '000003')          # 早已退市的代码
        v = wl.valued()
        codes = {x['code'] for x in v['rows']}
        assert '000003.XSHE' in codes, \
            '面板里查不到的票被静默丢掉了 —— "我加过的票不见了"'
        miss = [x for x in v['rows'] if x['code'] == '000003.XSHE'][0]
        assert miss.get('missing'), '查不到的那行没标出来'
        assert [x for x in v['rows'] if x['code'] == '600519.XSHG'][0]['name'], \
            '正常的票应该有名称与行情'
        # ---- 页签顺序：append-only、重放、过期不丢 ----
        # 🔴 顺序是**服务端**给的（页面不再自己排）。与整个模块同一套纪律：
        #   改顺序追加一条 `{act:'order'}`，当前顺序由重放得出。
        assert wl.group_order() == [], '还没排过，顺序记录该是空的'
        # 🔴 先造一个**自动组**（名字带 `实盘·` 前缀）—— 不造的话下面
        #   「自动组该排在前面」那条是**空转的**（这一段跑在 sync_live 之前，
        #   那时一个自动组都没有，判据退化成纯名称排序）。变异测试抓到过：
        #   把 `_group_key` 改成不区分自动组，断言照样全绿。
        wl.act('add', '600036', group=wl.auto_group('测试账户'))
        # 🔴 还要一个**名字排在自动组前面**的手工组。自动组前缀是「实盘·」，
        #   而 `实`(U+5B9E) 的码位本来就在 `核`/`银`/`默` 前面 —— 只有中文
        #   手工组的话，"去掉自动组优先"算出来的顺序**和正确的一样**，
        #   断言照样绿（变异测试抓到过）。`A` 是 ASCII，必排在它前面。
        wl.act('add', '600000', group='A银行观察')
        _d0 = [g['name'] for g in wl.groups()]
        assert any(wl.is_auto_group(g) for g in _d0) and \
            any(not wl.is_auto_group(g) for g in _d0), \
            '要同时有自动组与手工组才测得出默认序：%s' % _d0
        # 默认序：实盘自动组在前，其余按名称（= 页面原来那份规则）
        _autos = [g['name'] for g in wl.groups() if g['auto']]
        assert _d0[:len(_autos)] == _autos, \
            '没排过时自动组该在前面：%s' % _d0
        assert _d0 == sorted(_d0, key=lambda n: (0 if wl.is_auto_group(n) else 1, n)), \
            '没排过时的默认序不对：%s' % _d0
        # 倒过来排 -> 立刻生效，且**存进了账本**（不是只在内存里）
        _rev = list(reversed(_d0))
        _got = wl.set_group_order(_rev)
        assert _got == _rev, '排完的顺序不对：要 %s 实得 %s' % (_rev, _got)
        assert [g['name'] for g in wl.groups()] == _rev, 'groups() 没照排好的顺序给'
        _recs = [r for r in wl.log() if r.get('act') == 'order']
        assert len(_recs) == 1 and _recs[0]['groups'] == _rev, \
            '顺序没作为一条记录追加进账本：%s' % _recs
        # 🔴 顺序记录**不带 code**，`current()` 必须照旧 —— 重放循环开头
        #   那句 `if not r.get('code'): continue` 就是靠它跳过的。
        #   漏了的话顺序记录会被当成一条股票记录，表现是**自选池多一行空票**。
        assert all(x.get('code') for x in wl.current()), \
            '顺序记录被当成股票记录读进自选池了'
        _n_before = len(wl.current())
        wl.set_group_order(_d0)                      # 再排回去（又是一条）
        assert len(wl.current()) == _n_before, '排序改变了自选池的内容'
        assert len([r for r in wl.log() if r.get('act') == 'order']) == 2, \
            'append-only：第二次排序该是新追加一条，不是改写上一条'
        assert [g['name'] for g in wl.groups()] == _d0, '最后一条顺序该生效'
        # 🔴 **过期的顺序不能把分组弄丢。** 顺序里有的分组可能已经空了，
        #   而新分组（新账户、新建组）还没被排过 —— 两头都不许丢，
        #   否则"我排过序之后新加的分组不见了"。
        wl.set_group_order(['不存在的组'] + _d0[:1])
        _after = [g['name'] for g in wl.groups()]
        assert set(_after) == set(_d0), \
            '过期顺序把分组弄丢了或凭空多出来：%s vs %s' % (_after, _d0)
        assert _after[0] == _d0[0], '排过的那个该在最前面：%s' % _after
        _new = [g for g in _after if g not in _d0[:1]]
        assert _new == sorted(_new, key=lambda n: (0 if wl.is_auto_group(n) else 1, n)), \
            '没排过的那批该按默认序追加在后面：%s' % _after
        # 🔴 重名直接拒 —— "它到底排第几"没有答案；静默去重会让人以为排好了
        for _bad in (['A', 'A'], 'abc', None, [_d0[0], _d0[0]]):
            try:
                wl.set_group_order(_bad)
                raise AssertionError('非法顺序 %r 应被拒' % (_bad,))
            except wl.WatchError:
                pass
        wl.set_group_order(_d0)                      # 收尾：回到默认序
        _ord_n = len([r for r in wl.log() if r.get('act') == 'order'])
        # ---- 实盘持仓自动进自选，按账户分组 ----
        #   ★ 幂等：再同步一次不该追加任何记录（持仓没变）。
        #   🔴 只加不自动移 —— 卖光了标"已清仓"但留着，自动移出会把手写的
        #      备注一起抹掉，而 append-only 的账本里删不掉记录、丢掉的是上下文。
        import shutil as _sh
        import tempfile as _tf
        tmp2 = _tf.mkdtemp()
        old_dir2 = wl.LIVE
        wl.LIVE = tmp2
        try:
            r1 = wl.sync_live()
            assert r1['added'], '持仓没同步进自选'
            gs2 = {g['name']: g['n'] for g in wl.groups()}
            assert all(wl.is_auto_group(k) for k in gs2), \
                '自动同步应按账户建组（实盘·<账户名>）：%s' % list(gs2)
            assert len(gs2) >= 2, '两个账户应各成一组：%s' % gs2
            n_log = len(wl.log())
            r2 = wl.sync_live()
            assert not r2['added'] and not r2['regrouped'], '第二次同步不该有改动'
            assert len(wl.log()) == n_log, \
                '同步必须幂等 —— 第二次不该往 append-only 账本里追加'
            # 手工分到别的组的，自动同步不该抢回去
            code0 = r1['added'][0]['code']
            wl.act('group', code0, group='我手工分的')
            wl.sync_live()
            now = {x['code']: x['group'] for x in wl.current()}
            assert now[code0] == '我手工分的', \
                '自动同步覆盖了手工分组 —— 那是用户的选择，不该被覆盖'
            # 自选里有、但不在任何持仓里的自动组条目 -> 标已清仓、不移出
            wl.act('add', '600519', group=wl.auto_group('不存在的账户'))
            r3 = wl.sync_live()
            assert any(x['code'] == '600519.XSHG' for x in r3['cleared']), \
                '自动组里已清仓的没被标出来'
            assert any(x['code'] == '600519.XSHG' for x in wl.current()), \
                '🔴 已清仓的被自动移出了 —— 只该标记，不该移出'

            # ---- 抓取范围去重（用户明确要的"不要重复调用"）----
            codes = sv._rt_codes()
            assert len(codes) == len(set(codes)), '_rt_codes 有重复！'
            held = set()
            for a in lv.load_accounts():
                if not a.get('archived'):
                    held |= set(lv.positions(a['id']))
            watch = {x['code'] for x in wl.current()}
            from assay import alerts as _al
            buy = set(_al.codes())          # 买点清单也在抓取范围里
            assert set(codes) == held | watch | buy, \
                '抓取范围应是【持仓 ∪ 自选 ∪ 买点清单】：少了 %s / 多了 %s' \
                % (sorted((held | watch | buy) - set(codes)),
                   sorted(set(codes) - (held | watch | buy)))
            assert len(codes) < len(held) + len(watch), \
                ('去重没生效 —— 持仓的票会被自动同步进自选，两边分别抓的话'
                 '请求量直接翻倍（%d = %d + %d）' % (len(codes), len(held), len(watch)))

            # ---- 自选也带实时价，且**读共享库**不自己打接口 ----
            v2 = wl.valued()
            assert 'price_src' in v2 and 'rt_n' in v2, '自选没给价来源'
            for x in v2['rows']:
                if x.get('rt_src'):
                    assert x.get('rt_at'), '标了实时却没给时刻'
                    # 涨跌幅要按实时价重算，不能留面板那个收盘值
                    if x.get('preclose'):
                        want = round((x['close'] / x['preclose'] - 1) * 100, 2)
                        assert abs(x['change_pct'] - want) < 0.02, \
                            ('涨跌幅没按实时价重算 —— 价变了幅没变，页面上'
                             '前后矛盾：%s vs %s' % (x['change_pct'], want))
            src = open(os.path.join(REPO,
                                    'assay', 'watchlist.py'), encoding='utf-8').read()
            assert 'realtime' in src and 'urllib' not in src, \
                ('自选页不该自己去打行情接口 —— 要读 datalake/rt 那个共享库，'
                 '否则同一只票会被抓好几遍')
        finally:
            wl.LIVE = old_dir2
            _sh.rmtree(tmp2, ignore_errors=True)

        # ---- 刚加进来的票【立刻补抓一次】----
        #   ★ 轮询每 60s 才重算一次范围、bar 还是轮转抓的（~42 分钟一圈），
        #     而人加完自选是马上要看的 —— 那一刻一片"收盘价"，
        #     看着像功能没生效。
        #   ★ 只抓 `missing` 认定的那几只，所以天然收敛（抓到就不再 missing）；
        #     每只带冷却 —— 退市股永远 missing，没冷却的话每次刷新都打一次。
        rtm.missing = lambda cs, root=None, day=None: [
            c for c in cs if c == '600585.XSHG']
        HIT['snap'].clear(); HIT['bar'].clear()
        sv._RT['miss_at'] = {}
        assert sv.api_watchlist_act({}, {'act': 'add', 'code': '600585'})['ok']
        assert HIT['snap'] == [['600585.XSHG']],             '加进自选没立刻补抓（或抓多了）：%s' % HIT['snap']
        assert HIT['bar'] == [['600585.XSHG']],             'bar 也该补一次（轮转要 42 分钟才轮到它）：%s' % HIT['bar']
        # 同一只票在冷却窗口内不再抓 —— 页面刷新几次不该变成几串请求
        HIT['snap'].clear(); HIT['bar'].clear()
        sv.api_watchlist({})
        sv.api_watchlist({})
        assert not HIT['snap'], '同一只票被反复补抓（没有冷却）：%s' % HIT['snap']
        # 冷却过了才再抓
        sv._RT['miss_at'] = {}
        sv.api_watchlist({})
        assert HIT['snap'] == [['600585.XSHG']],             '冷却过后应该再试一次：%s' % HIT['snap']
        # 不在交易时段：什么都不抓 —— 收盘后没有盘中数据可抓，
        # 硬抓只会拿到空结果，而**空结果一律当失败**
        HIT['snap'].clear()
        rtm.in_session = lambda now=None: False
        sv._RT['miss_at'] = {}
        sv.api_watchlist({})
        assert not HIT['snap'], '非交易时段还在打接口：%s' % HIT['snap']
        rtm.in_session = lambda now=None: True
        # 只读模式（没开 --live）也不许往外发请求
        HIT['snap'].clear()
        sv.ALLOW_LIVE = False
        sv._RT['miss_at'] = {}
        sv.api_watchlist({})
        assert not HIT['snap'], '只读模式下还在打接口：%s' % HIT['snap']
        sv.ALLOW_LIVE = True
        rtm.missing = _o_miss

        # 非法输入
        for bad in (('add', '99'), ('nope', '600519'), ('add', '')):
            try:
                wl.act(bad[0], bad[1])
                raise AssertionError('%r 应被拒' % (bad,))
            except wl.WatchError:
                pass
        # 只读模式：接口层要拒（不能只靠页面）
        sv.ALLOW_LIVE = False
        assert (sv.api_watchlist_act({}, {'act': 'add', 'code': '601857'})
                or {}).get('error'), '只读模式下应拒绝改自选'
        # 改顺序也写账本 —— 同一条拦住（漏了就是只读模式能改账本）
        _n_ord = len([r for r in wl.log() if r.get('act') == 'order'])
        assert (sv.api_watchlist_order({}, {'groups': ['x']})
                or {}).get('error'), '只读模式下应拒绝改页签顺序'
        assert len([r for r in wl.log() if r.get('act') == 'order']) == _n_ord, \
            '只读模式下顺序记录还是被写进去了'
        n_before = len(wl.log())
        sv.ALLOW_LIVE = True
        assert sv.api_watchlist_act({}, {'act': 'add', 'code': '601988'}).get('ok')
        assert len(wl.log()) == n_before + 1
        return ('append-only：4 次改动 + 移出后日志 5 条且 add 记录仍在；'
                '重复加幂等不追加；重放出的分组/备注正确；分组过滤；'
                '退市代码不静默丢掉而是标 missing；3 类非法输入被拒；'
                '只读模式接口层拒写（含改顺序）；'
                '页签顺序：默认自动组在前、拖过的从账本重放、'
                'append-only（%d 条 order 记录）、过期顺序不丢分组、重名拒；'
                '持仓自动按账户分组同步且幂等、'
                '不覆盖手工分组、已清仓只标记不移出；'
                '抓取范围 = 持仓∪自选∪买点且去重（%d < %d+%d）；'
                '自选带实时价且涨跌幅按实时价重算、不自己打接口；'
                '新加的票立刻补抓一次（snap+bar 各 1 请求）、'
                '同一只有 180s 冷却、非交易时段与只读模式一律不发请求'
                % (_ord_n, len(codes), len(held), len(watch)))
    finally:
        sv.ALLOW_LIVE, wl.LIVE = old_live, old_dir
        rtm.snapshot, rtm.fetch = _o_snap, _o_fetch
        rtm.in_session, rtm.is_trading_day = _o_sess, _o_day
        rtm.missing = _o_miss
        sv._RT['miss_at'] = {}
        shutil.rmtree(tmp, ignore_errors=True)


@case('买点页面真实渲染：手工填表 + 互算 + 到价点亮（playwright）', tag='web')
def t_alerts_ui():
    """那张 Excel 的页面版。**手工填**是这一页的全部意义，所以这里真的去填。

    ★ 表单每一项都要有【标签】—— 一个空输入框谁也不知道要填什么。
    ★ 「换算出来的」那一列必须**跟着输入变**：只改 DOM 不重渲染的话，
      填完价它还显示着上一次的股息率，而这一页存在的理由就是这个换算。
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return '跳过（无 playwright）'
    import shutil
    import tempfile
    import threading
    from http.server import ThreadingHTTPServer

    from assay import alerts as al
    from assay import server as sv
    old_live, old_dir = sv.ALLOW_LIVE, al.LIVE
    sv.ALLOW_LIVE = True
    tmp = tempfile.mkdtemp()
    al.LIVE = tmp                    # ★ 不往真账本里写测试数据
    # 外部分红接口打桩：这个用例起的是同进程的服务，页面一开就会走
    # suggest_div -> ext_div。真打东财会让用例依赖外网，且"一天一次"的
    # 缓存会污染真 datalake/rt。
    _o_fetch, _o_extpath = al._ext_fetch, al.ext_path
    al.ext_path = lambda root=None: os.path.join(tmp, 'div_ext.json')
    al._ext_fetch = lambda cs, day=None: {}
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
            pg = br.new_page(viewport={'width': 1500, 'height': 1100})
            errs = []
            pg.on('pageerror', lambda e: errs.append(str(e)))
            pg.goto(base + '/alerts.html', wait_until='networkidle')
            pg.wait_for_selector('#aadd input', timeout=40000)
            assert '买点' in pg.locator('#top .btn.nav.on').inner_text(), \
                '买点页没高亮：%s' % pg.locator('#top .btn.nav.on').inner_text()
            assert '空的' in pg.locator('#pg').inner_text(), '空清单没给空态提示'

            # ---- 新增一行：搜票 -> 编辑器 -> 填两档 -> 保存 ----
            pg.fill('#aadd input', '000423')
            pg.wait_for_selector('.skit', timeout=20000)
            pg.locator('.skit').first.click()
            pg.wait_for_selector('#adiv', timeout=20000)
            pg.wait_for_timeout(500)
            ed = pg.locator('.lvsec').first.inner_text()
            # 🔴 分红是【数据给的】，不是输入框 —— "预计分红"没有价值，
            #   而摆一个输入框在那里就是在邀请人去猜。
            assert pg.evaluate(
                "() => document.querySelector('#adiv').tagName") != 'INPUT', \
                '分红又变成输入框了 —— 它不该能手填'
            _dv = float(pg.locator('#adiv').inner_text())
            assert _dv > 0, '分红没显示出来：%r' % pg.locator('#adiv').inner_text()
            assert '年度合计' in ed, '没说分红是什么口径：%s' % ed[:200]
            assert '不能手填' in ed or '不手填' in ed, \
                '没写清分红为什么不能手填：%s' % ed[:200]
            assert '近 12 个月已公告' in ed, \
                '另一个口径也该显示出来（当参考）：%s' % ed[:240]
            assert pg.locator('.ause').count() == 0, \
                '还留着「用它」那种手填入口'
            assert '预计分红' in ed, \
                ('没写清为什么不给手填 —— "预计分红没有价值"这条要写在'
                 '眼前，否则下一次又会有人加个输入框：%s' % ed[:200])
            for lab in ('实际分红', '接近', '备注', '按什么填', '数值',
                        '换算出来的'):
                assert lab in ed, '编辑器缺「%s」这个标签：%s' % (lab, ed[:200])
            # 填第一档：目标价 49 -> 换算出 分红/49（分红是数据给的）
            pg.locator('.aby').first.select_option('price')
            pg.wait_for_timeout(300)
            pg.locator('.av').first.fill('49')
            pg.locator('.av').first.dispatch_event('change')
            pg.wait_for_timeout(400)
            row1 = pg.locator('.lvsec').first.locator(
                'table.lvt tr').nth(1).inner_text()
            _wy = '%.2f%%' % (_dv / 49 * 100)
            assert _wy in row1 and ('%s/49' % _dv) in row1.replace(' ', ''), \
                '填了目标价 49 却没换算出 %s/49=%s：%s' % (_dv, _wy, row1)
            # 第二档改成按【目标股息率】填 6% -> 换算出 45.00 元
            pg.locator('.aby').nth(1).select_option('yield')
            pg.wait_for_timeout(300)
            pg.locator('.av').nth(1).fill('6')
            pg.locator('.av').nth(1).dispatch_event('change')
            pg.wait_for_timeout(400)
            row2 = pg.locator('.lvsec').first.locator(
                'table.lvt tr').nth(2).inner_text()
            _wp = '%.2f' % (_dv / 0.06)
            assert _wp in row2, \
                '填了目标股息率 6%% 却没换算出目标价 %s：%s' % (_wp, row2)
            pg.click('#asave')
            pg.wait_for_selector('table.pkt', timeout=20000)
            pg.wait_for_timeout(800)

            # ---- 表里那一行 ----
            # ★ 分红的更新状态必须在页面上 —— 这一页的目标价全是
            #   "分红 ÷ 股息率"算出来的，分红过期就整页都是过期的数。
            _hd = pg.locator('.lvhead').inner_text()
            assert '分红' in _hd and ('今天已更新' in _hd or '已更新' in _hd
                                      or '取不到' in _hd), \
                '头上没说分红是什么时候更新的：%s' % _hd.replace('\n', ' ')
            assert pg.locator('#adivref').count() == 1, \
                '缺「刷新分红」（平时一天一次，刚出公告时要能手动催一次）'
            th = ' '.join(pg.locator('table.pkt th').all_inner_texts())
            for k in ('名称', '现价', '实际分红', '当前股息率',
                      '第 1 档', '状态'):
                assert k in th, '盯价表缺「%s」列：%s' % (k, th)
            # ★ 代码与名称**同一格**（2026-09-16 全站统一）——
            #   判据两头：没有"代码"列 + 名称那格里带着小字代码。
            assert '代码' not in th, '代码又单独占了一列：%s' % th
            assert pg.locator('table.pkt tr:nth-child(2) td:nth-child(1) .cd,'
                              'table.pkt tr:nth-child(2) td:nth-child(1) .cd0'
                              ).count() == 1, '买点表的名称格里没有小字代码'
            # ★ 能进 tooltip 的就别占列：备注是给自己看的一句话、长短不定，
            #   摆进表里会把要扫的数字挤走。但**信息不能丢** ——
            #   有备注的行要带 ✎ 且 title 里是原文。
            assert '备注' not in th, '备注不该占一列：%s' % th
            r1 = pg.locator('table.pkt tr').nth(1).inner_text().replace('\n', ' ')
            assert '东阿阿胶' in r1, '保存后没渲染出来：%s' % r1
            # 带备注的行：✎ 在，原文在 title 里（表里不占地方但查得到）
            # ★ set 是【整行覆盖】—— 加备注也要把两档原样带上，
            #   不然后面"回填的是哪一种口径"那条就没得验了
            al.set_row('000423',
                       [{'by': 'price', 'v': 49}, {'by': 'yield', 'v': 6}],
                       note='这是一条很长的备注，用来验证它不会把表格撑开')
            pg.reload(wait_until='networkidle')
            pg.wait_for_selector('table.pkt', timeout=20000)
            pg.wait_for_timeout(600)
            _nt = pg.locator('table.pkt tr').nth(1).inner_text()
            assert '很长的备注' not in _nt, \
                '备注原文又出现在表格里了：%s' % _nt.replace('\n', ' ')
            _mk = pg.locator('table.pkt tr').nth(1).locator(
                '[title*="很长的备注"]')
            assert _mk.count() >= 1, \
                '备注挪走了但没留标记 —— 那等于把它藏没了'
            assert '✎' in pg.locator('table.pkt tr').nth(1).inner_text(), \
                '有备注的行没带 ✎'
            assert _wy in r1 and '49.00' in r1, \
                '表里没给"目标价 + 那个价对应的股息率"：%s' % r1
            # 现价低于 49 -> 第一档必须点亮，且【整行】点亮
            v = al.valued()
            st = v['rows'][0]['state']
            if st == 'hit':
                assert pg.locator('tr.ahit').count() >= 1, \
                    ('到价了却没点亮整行 —— 只给状态列上色的话，一屏十几行时'
                     '要逐行看那一列才知道哪行该动')
                assert '到价' in r1, '状态列没写"到价"：%s' % r1
                # 顶栏「买点」那个红点要亮（判据来自服务端 n_hit/n_near）
                pg.wait_for_timeout(1200)
                assert pg.evaluate(
                    "() => (document.querySelector('#navdot2')||{}).style"
                    "?.display") == 'inline-block', \
                    '有票到价，顶栏「买点」的红点却没亮'
            # 名称点开速览浮层，浮层里有去个股页的出口（不许断链）
            _via_pop(pg, pg.locator('table.pkt a[data-sp]').first, '000423')

            # ---- 改一行：编辑器要回填【存的那个】口径 ----
            #   表里显示的是换算后的两个数，照着显示值回填会把"我填的是
            #   股息率"悄悄变成"我填的是价格"。
            pg.locator('.aed').first.click()
            pg.wait_for_selector('#adiv', timeout=20000)
            pg.wait_for_timeout(400)
            bys = pg.locator('.aby').evaluate_all('es => es.map(e => e.value)')
            vs = pg.locator('.av').evaluate_all('es => es.map(e => e.value)')
            assert bys[:2] == ['price', 'yield'], \
                '编辑器没回填"当初填的是哪一种"：%s' % bys
            assert abs(float(vs[0]) - 49) < 0.01 and abs(float(vs[1]) - 6) < 0.01, \
                '回填的数值不对（第二档该是 6 而不是 45）：%s' % vs
            # 窄屏不许把 body 撑出横滚
            pg.set_viewport_size({'width': 1024, 'height': 900})
            pg.wait_for_timeout(500)
            ov = pg.evaluate('document.body.scrollWidth'
                             ' - document.body.clientWidth')
            assert ov <= 1, '1024 宽下 body 横滚了 %dpx' % ov
            assert not errs, 'JS 报错：%s' % errs[:3]
            br.close()
        return ('空态 -> 搜票 -> 分红自动取实际已公告(只读、不是输入框，'
                '另一口径当参考显示) -> 填目标价 49 当场'
                '换算 2.7/49=5.51% -> 第二档改按股息率 6% 换算出 45.00 -> 保存；'
                '表里给"目标价+股息率"、到价整行点亮且顶栏红点亮；'
                '点名称弹速览浮层（不跳走）且浮层里能去个股页；改一行回填的是【存的那个口径】'
                '(price/yield 而不是换算值)；备注不占列而是名称后的 ✎ + title；'
                '头上有分红更新状态与「刷新分红」；'
                '1024 宽无横滚；0 个 JS 错误')
    finally:
        httpd.shutdown()
        sv.ALLOW_LIVE, al.LIVE = old_live, old_dir
        al._ext_fetch, al.ext_path = _o_fetch, _o_extpath
        shutil.rmtree(tmp, ignore_errors=True)


@case('新页面真实渲染：盘面 / 板块 / 自选 / 对比（playwright）', tag='web')
def t_new_pages_ui():
    """四个独立页面都真渲染一遍，并验证【页面之间能互相走到】。

    ★ 独立页面的风险不是单页坏，是**页面之间断链** —— 从盘面点不到个股、
      从板块点不到成分。所以这里逐个点过去。
    ★ Canvas 的判据同样是"画布上有非透明像素"，不是 DOM 里有 <canvas>。
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return '跳过（无 playwright）'
    import shutil
    import tempfile
    import threading
    from http.server import ThreadingHTTPServer

    from assay import server as sv
    from assay import watchlist as wl
    old_live, old_dir = sv.ALLOW_LIVE, wl.LIVE
    sv.ALLOW_LIVE = True
    tmp = tempfile.mkdtemp()
    wl.LIVE = tmp                    # ★ 不往真账本里写测试数据
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), sv.Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = 'http://127.0.0.1:%d' % port
    NZ = lambda sel: ("() => { const c=document.querySelector('%s');"
                      " if(!c) return -1; const g=c.getContext('2d');"
                      " const d=g.getImageData(0,0,c.width,c.height).data;"
                      " let n=0; for(let i=3;i<d.length;i+=4) if(d[i]>0) n++;"
                      " return n; }" % sel)
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

            def clean(sel='#pg'):
                t = pg.locator(sel).inner_text()
                assert 'undefined' not in t and 'NaN' not in t, \
                    '%s 里有 undefined/NaN' % sel
                return t

            # ================= 盘面 =================
            pg.goto(base + '/market.html', wait_until='networkidle')
            pg.wait_for_selector('#dcv', timeout=40000)
            pg.wait_for_timeout(1200)
            assert '盘面' in pg.locator('#top .btn.nav.on').inner_text(), '当前页没高亮'
            kp = ' | '.join(pg.locator('.kpi .k').all_inner_texts())
            for k in ('涨 / 跌 / 平', '涨停 / 跌停', '全市场成交额', '中位涨幅'):
                assert k in kp, '盘面 KPI 缺「%s」：%s' % (k, kp)
            nz = pg.evaluate(NZ('#dcv'))
            assert nz > 3000, '涨跌分布图没画出来：%d' % nz
            clean()
            n_ranks = pg.locator('.pgrid.c2 .lvsec').count()
            assert n_ranks >= 5, '榜单块只有 %d 个' % n_ranks
            # ★ 榜单在两列网格里，列多了最右边几列会被压没 —— 原来 8 列时
            #   「行业」直接看不到了，而这不报错。现在固定 5 列，且逐列量宽度。
            rh = pg.locator('table.pkt.rk').first.locator('th').all_inner_texts()
            assert len(rh) == 5, '榜单应是 5 列（多了会被挤没）：%s' % rh
            assert '名称' in rh[1] and '行业' in rh[1], \
                '行业应作为名称的注解显示，而不是单独占一列：%s' % rh
            wid = pg.evaluate(
                "() => { const t=document.querySelector('table.pkt.rk');"
                " return [...t.querySelectorAll('tr')[1].children]"
                ".map(td => Math.round(td.getBoundingClientRect().width)); }")
            assert min(wid[1:]) >= 40, \
                '有列被压到 %d px（内容看不见了）：%s' % (min(wid[1:]), wid)
            row1 = pg.locator('table.pkt.rk').first.locator(
                'tr').nth(1).locator('td').nth(1).inner_text()
            assert len(row1.split('\n')) >= 2, '名称下面没带行业：%r' % row1
            # 前后翻日：日期真的变了
            d0 = pg.locator('#mdate').input_value()
            pg.locator('.mshift[data-d="-1"]').click()
            pg.wait_for_timeout(2500)
            d1 = pg.locator('#mdate').input_value()
            assert d1 < d0, '「前一日」没生效（%s -> %s）' % (d0, d1)
            # 盘面 → 个股（断链是独立页面最大的风险）
            _via_pop(pg, pg.locator('.pgrid.c2 a[data-sp]').first)

            # ================= 板块 =================
            pg.goto(base + '/sector.html', wait_until='networkidle')
            pg.wait_for_selector('table.pkt', timeout=40000)
            pg.wait_for_timeout(800)
            #   🔴 **2026-09-14 起点亮的是父级「🌡 盘面」**：板块已从 NAV
            #     收进盘面，它自己那一项不存在了。这条原来钉「板块」高亮，
            #     属于被改动作废的断言 —— 要保的东西没变（"我在哪"必须有
            #     指示），只是答案从"它自己"变成了"它所属的那一组"。
            assert '盘面' in pg.locator('#top .btn.nav.on').inner_text(), \
                ('板块页该点亮父级「🌡 盘面」，实得：%s'
                 % pg.locator('#top .btn.nav.on').inner_text())
            assert '板块' in pg.locator('#top h1').inner_text(), \
                '板块页的标题该仍是它自己'
            kinds = pg.locator('.lvhead .kd').count()
            assert kinds >= 4, '板块分类入口只有 %d 个' % kinds
            n_sw = pg.locator('.lvsec table.pkt tr').count() - 1
            assert n_sw >= 25, '申万板块行数不对：%d' % n_sw
            clean()
            # 点一个板块 → 出成分
            pg.locator('a.pick').first.click()
            pg.wait_for_timeout(2500)
            secs = [x.split('\n')[0] for x in pg.locator('.lvsec h3').all_inner_texts()]
            assert any('成分' in x for x in secs), '点板块没出成分表：%s' % secs
            clean()
            # 切到概念板块
            pg.locator('.lvhead .kd[data-k="concept"]').click()
            pg.wait_for_timeout(2500)
            assert '概念' in pg.locator('.lvhead h2').inner_text(), '切概念没生效'
            n_cc = pg.locator('.lvsec table.pkt tr').count() - 1
            assert n_cc > 100, '概念板块行数不对：%d' % n_cc
            # 板块 → 个股
            pg.locator('.lvsec a.pick').first.click()
            pg.wait_for_timeout(2500)
            _via_pop(pg, pg.locator('a[data-sp]').first)

            # ================= 自选 =================
            pg.goto(base + '/watchlist.html', wait_until='networkidle')
            pg.wait_for_selector('#wadd input', timeout=40000)
            assert '自选' in pg.locator('#top .btn.nav.on').inner_text(), \
                '自选页没高亮：%s' % pg.locator('#top .btn.nav.on').inner_text()
            assert '空的' in pg.locator('#pg').inner_text(), '空自选没给空态提示'
            pg.fill('#wadd input', '601857')
            pg.wait_for_selector('.skit', timeout=20000)
            pg.locator('.skit').first.click()
            pg.wait_for_timeout(2500)
            t = clean()
            assert '中国石油' in t, '加进自选后没渲染出来：%s' % t[:200]
            assert '变更历史' not in t, \
                '变更历史不该渲染在页面上（账本仍在，只是不占版面）'
            assert pg.locator('.lvsec table.pkt tr').count() >= 2, '盯盘表没行'
            # ---- 页签：多个分组并列，实盘账户组带「持」并排在前 ----
            #   ★ 分组是"我要分别盯的几拨票"，用页签而不是 chip ——
            #     chip 看着像筛选标签，页签才表示"几个并列的视图"。
            pg.click('#wsync')                     # 把持仓同步进自选
            pg.wait_for_timeout(3000)
            tabs = [x.replace('\n', ' ') for x in
                    pg.locator('.wtab').all_inner_texts()]
            assert len(tabs) >= 4, '页签太少（应有 各账户 + 手工组 + ＋）：%s' % tabs
            # 🔴 没有「全部」页签 —— 不同账户的持仓放一起横向比没有意义
            assert not any('全部' in x for x in tabs), \
                '不该再有「全部」页签：%s' % tabs
            assert tabs[-1].strip() == '＋', '最后应是新建页签：%s' % tabs
            auto = [x for x in tabs if '持' in x]
            assert len(auto) >= 2, \
                '两个实盘账户应各成一个带「持」的页签：%s' % tabs
            assert '持' in tabs[0], \
                '实盘账户组应排在手工组前面（它们跟着持仓变）：%s' % tabs
            assert pg.locator('.wtab.on').count() == 1, \
                '同一时刻只该有一个页签高亮'
            # 重新进这一页：没有「全部」了，默认要落在第一个（实盘）分组上
            #   —— 落到空字符串的话整页一行都没有，而它不报错。
            pg.goto(base + '/watchlist.html', wait_until='networkidle')
            pg.wait_for_selector('.wtab.on', timeout=40000)
            pg.wait_for_timeout(1500)
            tabs = [x.replace('\n', ' ') for x in
                    pg.locator('.wtab').all_inner_texts()]
            assert '持' in pg.locator('.wtab.on').inner_text(), \
                '默认没落在第一个分组上：%s' % pg.locator('.wtab.on').inner_text()
            assert pg.locator('.lvsec').first.locator(
                'table.pkt tr').count() >= 2, '默认页签下一行都没有'
            # ---- 页签能【拖动改顺序】，且拖完立刻存进账本 ----
            # 🔴 顺序存在服务端（`watchlist.jsonl` 的 order 记录），不存
            #   localStorage —— 换台机器就回到默认的话，"我怎么归类这些票"
            #   这件事就没留住。判据必须是**重新打开页面还在**。
            _tg = lambda: pg.locator('.wtab[data-g]').evaluate_all(
                'es => es.map(e => e.dataset.g)')
            _o0 = _tg()
            assert len(_o0) >= 2, '分组不够两个，测不了顺序：%s' % _o0
            assert pg.locator('.wtab[data-g]').first.get_attribute(
                'draggable') == 'true', '页签没开 draggable —— 按住拖不动'
            # ＋ 不是分组，不该参与排序
            assert pg.locator('#wnewg').get_attribute('draggable') != 'true', \
                '「＋」也能拖 —— 它不是分组，永远该在最后'

            def _drag(i_from, i_to_left_of):
                a = pg.locator('.wtab[data-g]').nth(i_from)
                z = pg.locator('.wtab[data-g]').nth(i_to_left_of)
                ab, zb = a.bounding_box(), z.bounding_box()
                pg.mouse.move(ab['x'] + ab['width'] / 2,
                              ab['y'] + ab['height'] / 2)
                pg.mouse.down()
                # 落点取目标页签的**左侧 20%** -> 插到它前面
                for _s in (0.5, 0.2):
                    pg.mouse.move(zb['x'] + zb['width'] * _s,
                                  zb['y'] + zb['height'] / 2, steps=8)
                pg.mouse.up()
                pg.wait_for_timeout(1500)

            _drag(len(_o0) - 1, 0)                 # 最后一个拖到最前面
            _o1 = _tg()
            assert _o1[0] == _o0[-1], \
                '拖了没生效：%s -> %s' % (_o0, _o1)
            assert sorted(_o1) == sorted(_o0), '拖动把分组弄丢了：%s' % _o1
            # 🔴 判据是**重新打开页面**后还在 —— 只看当前 DOM 的话，
            #   "本地移动了但没存上"看不出来（POST 挂掉也是这个表现）。
            pg.goto(base + '/watchlist.html', wait_until='networkidle')
            pg.wait_for_selector('.wtab.on', timeout=40000)
            pg.wait_for_timeout(1500)
            assert _tg() == _o1, \
                '顺序没存住（重开页面回弹了）：存的 %s，重开后 %s' % (_o1, _tg())
            # 服务端也要认这份顺序（页面与账本不许分叉）
            _sg = pg.evaluate(
                "async () => (await (await fetch('/api/watchlist')).json())"
                ".groups.map(g => g.name)")
            assert _sg == _o1, '接口给的顺序与页面不一致：%s vs %s' % (_sg, _o1)
            # 还原顺序，别把这个用例的副作用留给后面的断言
            # ★ 不用"再拖一次"还原 —— 拖到右邻的左边是**原位**（空操作），
            #   第一版就这么写的，结果后面"切页签行数要变"那条踩空
            #   （首个页签变成了只有 2 行的手工组）。直接指定目标顺序。
            _ordered = '%s -> %s' % ('/'.join(_o0), '/'.join(_o1))
            pg.evaluate(
                "async gs => (await fetch('/api/watchlist/order',"
                " {method:'POST', headers:{'Content-Type':'application/json'},"
                "  body: JSON.stringify({groups: gs})})).json()", _o0)
            pg.goto(base + '/watchlist.html', wait_until='networkidle')
            pg.wait_for_selector('.wtab.on', timeout=40000)
            pg.wait_for_timeout(1200)
            tabs = [x.replace('\n', ' ') for x in
                    pg.locator('.wtab').all_inner_texts()]
            # 实时价：与持仓页共用同一个库，页面侧不额外调接口
            #   ★ 在【实盘持仓那个页签】上验 —— 手工加的票没进抓取轮转，
            #     在它那一页看不到实时标记是正常的。
            # 🔴 选择器不能限定 .on/.warn —— 那两个 class 只在【有实时价】时
            #   才加，而非交易时段（或实时库里今天还没数据）就一个都没有。
            #   实测踩过：跨过零点后 nrt=0，这条断言凭空失败一次。
            #   判据应该是"那个标签存在且写了数据日"，与有没有实时价无关。
            _psrc = pg.evaluate(
                "() => [...document.querySelectorAll('.lvhead .lvtag')]"
                ".map(e => e.textContent).find(t => t.includes('数据日')) || ''")
            assert '数据日' in _psrc, '自选头上没有数据日+报价时间：%s' % _psrc
            if '实时' in _psrc:
                assert pg.locator(
                    'table.pkt .lvwhy[title*="实时价"]').count() >= 1, \
                    '顶栏说是实时，表里却没标出哪些是实时价'
            # 切到手工组：行数要跟着变（页签是本地切的，不重新打接口）
            n_auto = pg.locator('.lvsec').first.locator('table.pkt tr').count()
            man = [i for i, x in enumerate(tabs) if '持' not in x
                   and x.strip() != '＋']
            assert man, '手工加的那只票没有自己的页签：%s' % tabs
            pg.locator('.wtab[data-g]').nth(man[0]).click()
            pg.wait_for_timeout(1200)
            n_man = pg.locator('.lvsec').first.locator('table.pkt tr').count()
            assert 1 < n_man < n_auto, \
                '切页签后行数没变（%d -> %d）' % (n_auto, n_man)
            # 自选 → 个股：手工组里只有 601857，所以点它必须落到 601857
            _via_pop(pg, pg.locator('.lvsec').first.locator(
                'a[data-sp]').first, '601857')

            # ================= 对比 =================
            pg.goto(base + '/compare.html?codes=601857.XSHG,601088.XSHG',
                    wait_until='networkidle')
            pg.wait_for_selector('#ccv', timeout=40000)
            pg.wait_for_timeout(1500)
            #   🔴 同板块那条：对比已从 NAV 收进个股，点亮的是父级「📈 个股」。
            assert '个股' in pg.locator('#top .btn.nav.on').inner_text(), \
                ('对比页该点亮父级「📈 个股」，实得：%s'
                 % pg.locator('#top .btn.nav.on').inner_text())
            assert '对比' in pg.locator('#top h1').inner_text(), \
                '对比页的标题该仍是它自己'
            nz2 = pg.evaluate(NZ('#ccv'))
            assert nz2 > 3000, '对比曲线没画出来：%d' % nz2
            t = clean()
            assert '后复权' in t, '没说明一律后复权（不复权跨除权日有假跌幅）'
            assert '中国石油' in t and '中国神华' in t, '两只票没都渲染'
            bb = pg.locator('#ccv').bounding_box()
            pg.mouse.move(bb['x'] + bb['width'] * 0.6, bb['y'] + bb['height'] * 0.5)
            pg.wait_for_timeout(400)
            assert pg.locator('#ctip').is_visible(), '对比图没出读数'
            tip = pg.locator('#ctip').inner_text()
            assert '中国石油' in tip and '%' in tip, '读数不对：%s' % tip
            # 加一只 / 减一只
            pg.fill('#cadd input', '600519')
            pg.wait_for_selector('.skit', timeout=20000)
            pg.locator('.skit').first.click()
            pg.wait_for_timeout(2500)
            assert pg.locator('.rmc.chip').count() == 3, '加第三只没生效'
            pg.locator('.rmc.chip').first.click()
            pg.wait_for_timeout(2500)
            assert pg.locator('.rmc.chip').count() == 2, '移除没生效'
            # 对比 → 个股
            _via_pop(pg, pg.locator('.lvsec a[data-sp]').first)

            # ================= 窄屏不许把整个 body 撑横滚 =================
            #   🔴 横滚的是【body】的话，读表格时整页会左右晃。
            #      宽表必须自己在 .pw 里滚。这条抓到过三个真问题：
            #      顶栏 10 个入口不换行、grid 子项 min-width:auto 让 .pw 失效、
            #      .pw 的 overflow 只在 #pk 作用域下定义过（别处形同虚设）。
            narrow = br.new_page(viewport={'width': 1024, 'height': 1000})
            over = []
            for path in ['/'] + _pages():
                narrow.goto(base + path, wait_until='networkidle')
                narrow.wait_for_timeout(2200)
                ov = narrow.evaluate('() => document.documentElement.scrollWidth'
                                     ' - document.documentElement.clientWidth')
                if ov > 2:
                    over.append((path, ov))
            narrow.close()
            assert not over, '窄屏(1024)下这些页面把 body 撑出横滚：%s' % over

            # ================= 顶栏导航：每页都能走到每页 =================
            for href in ('/market.html', '/watchlist.html', '/stock.html'):
                assert pg.locator('#top a.nav[href="%s"]' % href).count() == 1, \
                    '顶栏缺 %s 的入口' % href
            # 🔴 **2026-09-14 反过来了**：板块与对比收进了父页（盘面 / 个股），
            #   顶栏不该再有它们。原来这里断言它们**必须在**顶栏 ——
            #   那是被改动作废的断言，不是删掉保护：真正要保的是
            #   「独立页面之间不许断链」，所以下面改成**从父页能不能走到**。
            for href in ('/sector.html', '/compare.html'):
                assert pg.locator('#top a.nav[href="%s"]' % href).count() == 0, \
                    ('%s 又回到顶栏了 —— 它已经收进父页（板块->盘面、'
                     '对比->个股），顶栏平铺 9 个时每次都要扫一遍' % href)
            assert pg.locator('#top a.nav[href="/#/live"]').count() == 1, \
                '顶栏缺实盘入口'
            # ★ 分组分隔线是【信息】不是装饰：告诉人"这几个是一类"。
            #   平铺 8 个入口时每次都要在 8 个里扫一遍才找到要去的地方。
            assert pg.locator('#top .navsep').count() == 3, \
                '顶栏没分组（实盘 | 市场 | 研究 | 数据 应有 3 条分隔）：%d' \
                % pg.locator('#top .navsep').count()
            assert pg.locator('#top a.nav').first.inner_text().find('实盘') >= 0, \
                '实盘应排在最前 —— 它是唯一回答"今天要做什么"的入口'

            br.close()
            assert not errs, '页面有运行时错误：%s' % errs[:3]
            return ('四页真渲染：盘面（KPI 齐 + 分布图 %d 像素 + 翻日 %s→%s + '
                    '%d 个榜单）、板块（%d 类 / 申万 %d 行 / 概念 %d 行 / 点出成分）、'
                    '自选（加入后渲染 + 页签按账户分【无「全部」】且切换生效'
                    ' + 页签可拖动改顺序、重开页面仍在、接口与页面一致'
                    '（%s））、'
                    '对比（曲线 %d 像素 + 读数 + '
                    '加减只数）；四页都能点到个股页；顶栏 5 个入口齐'
                    % (nz, d0, d1, n_ranks, kinds, n_sw, n_cc, _ordered, nz2))
    finally:
        sv.ALLOW_LIVE, wl.LIVE = old_live, old_dir
        shutil.rmtree(tmp, ignore_errors=True)
        httpd.shutdown()


@case('除权日的涨跌幅与昨收：三种口径都自洽（ETF 一拆二不许显示成腰斩）', 'fast')
def t_xr_change_pct():
    """🔴🔴 用户 2026-09-22："点进完整页，图看起来虽然是正确了，但是涨跌幅
    仍然是错的，7-10 这天显示出 -53.47%。"

    159516 半导体设备ETF国泰 2026-07-10 做过一次 **1 拆 2**（因子 2.0 -> 4.0），
    不复权收盘 1.9450 -> 0.9050。而 `alt_panel` 当时是拿**两天的不复权收盘
    直接相除**算 `change_pct` 的 -> **-53.47pp**，那天真实跌幅是 **-6.94pp**。
    页面上就是一根"腰斩"，**而它不报错**。

    🔴 顺带查出一个**所有标的都有**的旧问题：`kline` 把 `preclose` **原样
      透传**，而 `close`/OHLC 会按 fq 缩放 —— 于是后复权图上「昨收 10.5、
      收 25.4」自相矛盾。

    判据是**自洽**：`close / preclose - 1 == change_pct`，三种口径都要成立。
    ★ 口径对齐面板：面板的 `preclose` 就是**除权后昨收**（这条也在下面钉住）。
    🔴 **反向自证不可省**：窗口里必须**真的含一个因子跳变日**，否则
      naive 算法与正确算法给出同样的结果，这条用例是空转的
      （本项目为「挑到因子恰好是 1 的票」栽过一次）。
    """
    from assay import stock as _S
    from assay import paths as _P
    import duckdb as _dk

    CASES = [('159516.XSHE', '2026-07-10', '2026-07-14'),   # ETF：1 拆 2
             ('605128.XSHG', '2026-06-02', '2026-06-05')]   # 股票：除权
    for code, xr, end in CASES:
        seen_jump = False
        for fq in ('bfq', 'hfq', 'qfq'):
            bars = _S.kline(code, 12, fq, end=end)['bars']
            row = next((b for b in bars if b['date'] == xr), None)
            assert row, '%s 的 %s 不在窗口里 —— 构造不对' % (code, xr)
            # 反向自证：这一天**真的**是因子跳变日（否则这条空转）
            i = [b['date'] for b in bars].index(xr)
            prev = bars[i - 1]
            naive = (row['close'] / prev['close'] - 1) * 100
            if abs(naive - row['change_pct']) > 1:
                seen_jump = True
            # 自洽：容差按**显示精度**给（价格 round 到 3 位，
            # 0.97 的 ETF 上最后一位就值 0.1pp）
            got = (row['close'] / row['preclose'] - 1) * 100
            tol = max(0.02, 100 * 0.0006 / max(row['preclose'], 1e-6))
            assert abs(got - row['change_pct']) <= tol, (
                '%s %s(%s)：close/preclose-1 = %.2fpp 而 change_pct = %.2fpp'
                '（容差 %.2f）—— 昨收与涨跌幅不是同一个口径'
                % (code, xr, fq, got, row['change_pct'], tol))
        assert seen_jump, (
            '%s 的 %s 不是因子跳变日 —— 这条判据在这只票上是空转的'
            '（naive 算法与正确算法给出同样的结果）' % (code, xr))

    # ETF 那一天具体是多少：钉死数值，防"改对了方向但算错了"
    b = next(x for x in _S.kline('159516.XSHE', 12, 'bfq', end='2026-07-14')['bars']
             if x['date'] == '2026-07-10')
    assert -7.5 < b['change_pct'] < -6.4, \
        '159516 在 2026-07-10 的涨跌幅是 %.2fpp —— 应当在 -6.94pp 附近；' \
        '拿两天不复权收盘直接相除会得到 -53.47pp' % b['change_pct']

    # 面板的 preclose 是【除权后昨收】—— 上面那条口径对齐的依据
    c = _dk.connect(':memory:')
    n, bad = c.execute("""
        WITH x AS (SELECT jq_code, date, close_bfq, preclose, change_pct,
                          hfq_factor,
                          lag(hfq_factor) OVER (PARTITION BY jq_code
                                                ORDER BY date) pf
                   FROM %s WHERE date >= DATE '2026-06-01')
        SELECT count(*), sum(CASE WHEN abs((close_bfq / preclose - 1) * 100
                                           - change_pct) > 0.02 THEN 1 ELSE 0 END)
        FROM x WHERE pf IS NOT NULL AND abs(hfq_factor / pf - 1) > 0.01
    """ % _P.panel_sql()).fetchone()
    assert n and n > 20, '除权样本太少（%s 个），这条是空转的' % n
    assert not bad, ('面板里有 %d/%d 个除权日的 `close_bfq/preclose-1` 对不上 '
                     '`change_pct` —— preclose 不再是除权后昨收了，'
                     '而 alt_panel 是照它对齐的' % (bad, n))
    return 'ETF 一拆二那天 %.2fpp（naive 会给 -53.47pp）；股票与 ETF × 三种口径' \
           '昨收与涨跌幅自洽；面板 %d 个除权日 preclose 口径逐行成立' % (
               b['change_pct'], n)


@case('个股速览浮层：点了不跳走 / 买卖点贴 K 线且同日合并（playwright）', tag='web')
def t_stockpop():
    """用户的原话是"点击股票名称就真的跳转到个股页面了，然后无法直接返回"，
    以及"其他地方可能也有这样的情况，也要做成这样的效果"。所以这条要验
    **每一类页面**都不跳走，而不是只验实盘页。

    钉五件事，每件都对应一种**不报错的**坏法：
      ① 点了 URL **不变** + 浮层可见 —— "跳走了"和"浮层没弹"都是静默的坏
      ② 买卖点**贴着那根 K 线**（S 在上、B 在下）、同日同向**合成一个**，
         而 hover 出得来逐笔读数（价/量/合计）—— 2026-09-21 改的：
         原来画在成交价的 y 上，口径一错就整体飘走且不报错
      ③ 引了 stockpop 的页面**必须也引 kchart** —— 实测踩到：盘面/自选/
         买点/板块四个页面原本没有 kchart.js，点开浮层就是
         `drawKChart is not defined`，而浮层照样弹出、只是**一片空白**
      ④ 浮层固定 `bfq`：成交价是不复权实际价，切后复权标记会整体飘走
      ⑤ 入口只走统一 helper —— 裸 `href="/stock.html?code="` 一处都不许剩，
         否则那一处会**继续跳走**，而它不报错
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return '跳过（无 playwright）'
    import io as _io
    import threading
    from http.server import ThreadingHTTPServer

    from assay import server as sv

    # ---- ⑤ 静态：入口只有一个定义点，页面里不许有裸链接 ----
    # 🔴 判据匹配**结构**（`href=` 才是链接），不是"字符串出现过" ——
    #   头一版写成 `'stock.html?code=' in line` 就被 index.html 里
    #   HTML 注释块中反引号包着的那句路径说明打挂了。同 h.pruned 那条。
    import re as _re
    HREF = _re.compile(r'''href=["']/stock\.html\?code=''')
    for f in _web_files('web', '.js') + _web_files('web', '.html'):
        t = _io.open(os.path.join('web', f), encoding='utf-8').read()
        for ln, line in enumerate(t.splitlines(), 1):
            if not HREF.search(line):
                continue
            assert 'spHref' in line or 'stockHref' in line, \
                ('%s:%d 有裸的个股页链接 —— 那一处会【继续跳走】，'
                 '而它不报错：%s' % (f, ln, line.strip()[:90]))
    # ③ 引了 stockpop 的页面必须也引 kchart（浮层要画 K 线）
    pops = []
    for f in sorted(g for g in os.listdir('web') if g.endswith('.html')):
        t = _io.open(os.path.join('web', f), encoding='utf-8').read()
        if 'shared/stockpop.js' not in t:
            continue
        pops.append(f)
        assert 'shared/kchart.js' in t, \
            '%s 引了 stockpop 却没引 kchart —— 点开浮层是 drawKChart ' \
            'is not defined，而浮层照样弹出、只是一片空白' % f
        assert t.index('shared/stockpop.js') > t.index('shared/common.js'), \
            '%s 里 stockpop.js 必须在 common.js 之后（它用 esc/num/j/_tipAt）' % f
    assert len(pops) >= 7, '只有 %d 个页面有浮层：%s' % (len(pops), pops)
    # ④ 复权口径：**K 线前复权、成交价不复权 —— 两种并存**（2026-09-22）
    # ★ 这条断言的历史是「判据要跟着【事实】走」的活样本，四版：
    #     原版      固定 `SP_FQ='bfq'` —— 那时浮层只有实盘一个来源
    #     09-13     改成"按来源定"（回测 -> hfq）—— 因为**归档存的是后复权**
    #     09-14     改回"一律 bfq" —— 因为归档的展示层换算回不复权了
    #     09-22     K 线改 **qfq** —— 因为**前三版共同的前提没了**：
    #               买卖点此前画在 `Y(成交价)` 上，切复权就飘走；而 09-21
    #               标记改成贴那根 K 线的上下方，位置与价格再无关系。
    #   **前提一没，那条限制就该撤销，而不是留着一条没有理由的规则。**
    #   用户 09-22 的原话："不复权的看不出正常的走势" —— 跨除权日的假跌幅
    #   在形态上就是一根凭空的大阴线。
    sp = _io.open('web/shared/stockpop.js', encoding='utf-8').read()
    flat = sp.replace('"', "'").replace(' ', '')
    assert "constspFq=()=>'qfq'" in flat, \
        ('浮层的 K 线必须是【前复权】：不复权跨除权日有假跌幅，形态上是一根'
         '凭空的大阴线；后复权的纵轴又与券商软件对不上（同个股页的默认）')
    assert 'SP_FQ' not in sp, \
        '还留着 SP_FQ 这个旧常量 —— 两处定义迟早分叉（同「删字段要连带清干净」那条）'
    # 取数时真的用了它，而不是把 fq 写死在 URL 里
    assert 'fq=${spFq()}' in sp, 'kline 请求没有用 spFq()，口径切换等于没生效'
    # 🔴 **成交价那一半仍然必须是不复权** —— 这一页现在两种口径并存，
    #   而「价格与份额照着券商对账单」那条没有变（同「记账口径不该漏到页面上」）。
    rs = _io.open('assay/srv/runs.py', encoding='utf-8').read()
    assert "'fq': 'bfq'" in rs and "'fq': 'hfq'" not in rs, \
        'api_run_trades_of 还在声明 hfq —— 成交价必须是不复权实际价'

    httpd = ThreadingHTTPServer(('127.0.0.1', 0), sv.Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    notes = []
    try:
        with sync_playwright() as p:
            b = p.chromium.launch()
            # ---- ① 每一类页面都不跳走 ----
            PAGES = [('/#/live', '实盘'), ('/market.html', '盘面'),
                     ('/watchlist.html', '自选'), ('/alerts.html', '买点'),
                     ('/sector.html?kind=sw&code=801230', '板块成分'),
                     ('/stock.html?code=601857.XSHG', '个股同行业')]
            for path, label in PAGES:
                pg = b.new_page(viewport={'width': 1400, 'height': 900})
                errs = []
                pg.on('pageerror', lambda e: errs.append(str(e)))
                pg.goto('http://127.0.0.1:%d%s' % (port, path))
                pg.wait_for_timeout(3800)
                v = pg.locator('a[data-sp]:visible')
                assert v.count(), '%s 页一个浮层入口都没有' % label
                url0 = pg.url
                v.first.scroll_into_view_if_needed()
                v.first.click()
                pg.wait_for_timeout(3800)
                assert pg.url == url0, \
                    '%s 页点了就跳走了（%s -> %s）—— 那正是要修的毛病' \
                    % (label, url0, pg.url)
                box = pg.locator('#spwrap')
                assert box.count() and box.is_visible(), \
                    '%s 页浮层没弹出来或不可见（同 .pane 那次样式表 ' \
                    'display:none）' % label
                title = pg.locator('#sptitle').inner_text()
                assert title and title != '', '%s 页浮层标题是空的' % label
                assert not errs, '%s 页有 JS 错误：%s' % (label, errs[:2])
                # Esc 关得掉
                pg.keyboard.press('Escape')
                pg.wait_for_timeout(400)
                assert pg.locator('#spwrap').count() == 0, \
                    '%s 页 Esc 关不掉浮层' % label
                notes.append('%s %d入口' % (label, v.count()))
                pg.close()

            # ---- ② 买卖点：位置 + hover 读数（用实盘页，它有真成交）----
            pg = b.new_page(viewport={'width': 1400, 'height': 900})
            pg.goto('http://127.0.0.1:%d/#/live' % port)
            pg.wait_for_timeout(3800)
            # 找一只**有成交**的（持仓表里的都有）
            v = pg.locator('a[data-sp]:visible')
            v.first.scroll_into_view_if_needed()
            v.first.click()
            pg.wait_for_timeout(4500)
            d = pg.evaluate("""() => {
              const h = (SP.geo && SP.geo.trHits) ? SP.geo.trHits : [];
              const ts = SP.trades || [];
              const bs = SP.bars || [];
              const at = {}; bs.forEach((x, i) => { at[x.date] = i; });
              const key = {};
              ts.forEach(t => { if (at[t.date] != null)
                                  key[at[t.date] + '|' + t.side] = 1; });
              return {nh: h.length, nt: ts.length, ngrp: Object.keys(key).length,
                      first: h[0] ? {x: h[0].x, y: h[0].y,
                                     n: (h[0].ts || []).length,
                                     price: h[0].ts[0].price,
                                     date: h[0].ts[0].date} : null};
            }""")
            assert d['nt'], '这只票没有实盘成交 —— 换一个入口再验'

            # 🔴 两种口径并存 -> 副标题必须**分开说**：K 线前复权、成交价
            #   不复权。合成一句的话，人拿浮窗里的成交价去比纵轴会发现对不上，
            #   而那看着像有一边算错了。（这个标签历史上**两次与图相反**。）
            # ★ 判据钉**渲染出来的那行字**，不是源码：我第一版切了源码里
            #   那 900 个字符，而**注释自己就提到这两个词** -> 永远命中、
            #   变异当场漏掉（同「判据比要证的事宽」那条）。
            sub = pg.inner_text('#spsub')
            assert 'K 线' in sub and '前复权' in sub, \
                '副标题没说 K 线是前复权：%r' % sub
            assert '不复权' in sub and ('成交价' in sub or '份额' in sub), \
                '副标题没说成交价/份额仍是不复权：%r' % sub

            # 🔴 B/S 那个**字必须落在圆心上**（用户 2026-09-21 报了两次：
            #   "字不在圆圈中央"）。第一次以为是右上角那个笔数小圆盖住了，
            #   删掉之后**还是歪** —— 真因是 canvas 的 `textBaseline` 默认
            #   `alphabetic`（字身整个在基线上方，10px 粗体偏高约 3px），
            #   而 `textAlign` 这段**一次都没设过**，用的是本文件上一处画图
            #   留下的值 —— 水平位置取决于**画图顺序**。
            # ★ 判据是**墨迹重心与圆心的偏差**（可量的视觉事实），
            #   不是"有没有画字"：后者在字歪到圆外时照样全绿。
            ink = pg.evaluate("""(hit) => {
              const cv = document.getElementById('spcv');
              const g = cv.getContext('2d');
              const dpr = cv.width / cv.clientWidth;
              const R = 7.5, pad = Math.ceil((R + 2) * dpr);
              const cx = hit.x * dpr, cy = hit.y * dpr;
              const d = g.getImageData(Math.round(cx - pad), Math.round(cy - pad),
                                       pad * 2, pad * 2).data;
              /* 字是纯白 #fff，圆是品牌色、描边是背景色 —— 只收近白像素 */
              let n = 0, sx = 0, sy = 0;
              for (let j = 0; j < pad * 2; j++) for (let i = 0; i < pad * 2; i++) {
                const o = (j * pad * 2 + i) * 4;
                if (d[o] > 235 && d[o+1] > 235 && d[o+2] > 235 && d[o+3] > 200) {
                  n++; sx += i; sy += j;
                }
              }
              if (!n) return {n: 0};
              return {n: n, dx: (sx / n - pad) / dpr, dy: (sy / n - pad) / dpr};
            }""", d['first'])
            # 反向自证：真的找到了字的墨迹（找不到的话下面两条是空转的）
            assert ink['n'] >= 8, \
                ('圆心附近一个白像素都没扫到（%s）—— 构造不对，'
                 '这条判据是空转的' % ink['n'])
            assert abs(ink['dy']) <= 1.2, \
                ('B/S 的字垂直偏离圆心 %.2fpx —— textBaseline 没设成 middle 的话'
                 '默认 alphabetic 会让它偏高约 3px' % ink['dy'])
            assert abs(ink['dx']) <= 1.2, \
                ('B/S 的字水平偏离圆心 %.2fpx —— textAlign 没显式设成 center，'
                 '用的是上一处画图留下的值（画图顺序一变就错位）' % ink['dx'])
            # 🔴 **同一天同方向只画一个**（2026-09-21 用户要求）。
            #   原来是每笔一个、错开 8px —— 科创半导 2026-07-09 那天四个 S
            #   叠成一串。判据落在「组数」上，并**反向自证真的有合并发生**
            #   （笔数 > 组数），否则这条在"恰好每天一笔"的数据上是空转的。
            assert d['nh'] == d['ngrp'], \
                '标记数 %d != (日期,方向) 组数 %d —— 同日同向没合并' % (
                    d['nh'], d['ngrp'])

            # 🔴 判据从「y 随成交价变」换成「**贴着那根 K 线**」。
            #   旧设计把标记画在 `Y(成交价)` 上、位置本身表达价位 ——
            #   代价是口径一错就整体飘走，**而它不报错**：实测回测那条链
            #   21/106 只票飘出 K 线区间（`_to_raw` 拿不到 code 就静默不换算）。
            #   现在 y 只说"这一天有买/卖"。
            probe = pg.evaluate("""() => {
              const bs = SP.bars, cv = document.getElementById('spcv');
              const i = bs.length - 3, dt = bs[i].date, b = bs[i];
              const lo = Math.min(...bs.map(x => x.low)),
                    hi = Math.max(...bs.map(x => x.high));
              const p1 = lo + (hi - lo) * 0.2, p2 = lo + (hi - lo) * 0.8;
              const mk = tr => {
                const g = drawKChart(cv, {bars: bs, trades: tr});
                return g;
              };
              const g1 = mk([{date: dt, side: 'buy', shares: 100, price: p1}]);
              const y1 = g1.trHits.length ? g1.trHits[0].y : null;
              const g2 = mk([{date: dt, side: 'buy', shares: 100, price: p2}]);
              const y2 = g2.trHits.length ? g2.trHits[0].y : null;
              /* 同一天 3 笔买 + 1 笔卖 -> 应当只有 2 个标记 */
              const g3 = mk([{date: dt, side: 'buy', shares: 1, price: p1},
                             {date: dt, side: 'buy', shares: 2, price: p2},
                             {date: dt, side: 'buy', shares: 3, price: p1},
                             {date: dt, side: 'sell', shares: 4, price: p2}]);
              const hs = g3.trHits.map(h => ({y: h.y, n: h.ts.length,
                                              side: h.ts[0].side}));
              return {y1: y1, y2: y2, hs: hs, h: cv.height,
                      yHigh: g1.Y ? null : null,
                      barTop: g3.YY ? null : null,
                      low: b.low, high: b.high, mainTop: g3.mainTop,
                      mainH: g3.mainH};
            }""")
            assert probe['y1'] is not None and probe['y2'] is not None, \
                '构造的成交点没画出来：%r' % probe
            # ① y **不再随成交价变** —— 这正是旧判据的反面，防止改回去
            assert abs(probe['y1'] - probe['y2']) < 1, \
                ('同一天两笔不同价的买入画在了不同高度（%.0f / %.0f）—— '
                 '现在的规矩是贴着 K 线，位置不表达价位'
                 % (probe['y1'], probe['y2']))
            # ② 真的**贴着**那根 K 线：买在最低价下方一点点
            yl = probe['mainTop'] + probe['mainH']      # 主图下沿，仅作范围检查
            assert probe['mainTop'] <= probe['y1'] <= yl + 20, \
                '买入标记跑出主图了：y=%.0f 而主图 %.0f~%.0f' % (
                    probe['y1'], probe['mainTop'], yl)
            # ③ 同日 3 买 + 1 卖 -> 2 个标记，且买那个带着 3 笔
            hs = probe['hs']
            assert len(hs) == 2, '同日 3 买 1 卖应当只有 2 个标记，实得 %d' % len(hs)
            byside = {x['side']: x for x in hs}
            assert byside['buy']['n'] == 3 and byside['sell']['n'] == 1, \
                '合并后每个标记带的笔数不对：%r' % hs
            # ④ 卖在上、买在下（贴 K 线的方向不许反）
            assert byside['sell']['y'] < byside['buy']['y'], \
                'S 应当在 K 线上方、B 在下方，实得 S y=%.0f B y=%.0f' % (
                    byside['sell']['y'], byside['buy']['y'])

            # hover 上去要有读数：**逐笔列全 + 合计**（合并只收图标，不丢信息）
            cv = pg.locator('#spcv')
            bb = cv.bounding_box()
            sc = pg.evaluate("() => { const c = document.getElementById('spcv');"
                             "  return c.width / c.offsetWidth; }")
            f = d['first']
            pg.mouse.move(bb['x'] + f['x'] / sc, bb['y'] + f['y'] / sc)
            pg.wait_for_timeout(600)
            tip = pg.locator('#sptip')
            assert tip.is_visible(), 'hover 到买卖点上没有读数浮窗'
            txt = tip.inner_text()
            for want in ('买入', str(f['date'])):
                assert want in txt, 'hover 读数少了「%s」：%r' % (want, txt)
            assert ('%.3f' % f['price']) in txt.replace(',', ''), \
                'hover 读数里没有成交价 %.3f：%r' % (f['price'], txt)
            if f['n'] > 1:
                assert '合计' in txt and ('%d 笔' % f['n']) in txt, \
                    '合并了 %d 笔却没在读数里说清（逐笔 + 合计）：%r' % (
                        f['n'], txt)
            # ---- B/S 点是【同花顺那种圆点】，不是三角；列表默认折叠 ----
            bs = pg.evaluate("() => { const c = document.getElementById('spcv');"
                             "  return {w: c.offsetWidth, h: c.offsetHeight}; }")
            # 🔴 高度判**比例**不判绝对值：改成 width*0.32 时算出 368，
            #   仍然 >= 340 —— 断言照过（变异实测）。
            assert bs['w'] > 1000 and bs['h'] >= 340 \
                   and bs['h'] >= bs['w'] * 0.45, \
                ('画布只有 %dx%d —— B/S 圆点要能看出"哪根柱子"，'
                 '窄了 120 根柱子挤在一起圆点会互相压住' % (bs['w'], bs['h']))
            kc = _io.open('web/shared/kchart.js', encoding='utf-8').read()
            # 🔴 切片锚点要用**代码构造**，不要用注释：`/* 副图 */` 那句在
            #   2026-09-15 改成多副图时被重写了，于是这条当场
            #   `ValueError: substring not found` —— 看着像页面坏了，
            #   其实是**锚点没了**（同「断言查字符串会命中自己写的注释」的
            #   另一面：注释会变，代码构造不会）。
            seg = kc[kc.index('const trs = opts.trades'):
                     kc.index('subs.forEach(')]
            assert "fillText(buy ? 'B' : 'S'" in seg, \
                '买卖点必须是带 B/S 字母的圆点（同花顺那种），不是三角'
            # 🔴 只查 `g.arc(` 太宽 —— 引线端点那个小圆也是 arc，
            #   把 B/S 那个圆改成三角路径**照样全绿**（变异实测）。
            assert 'g.arc(x, cy, R,' in seg.replace('  ', ' '), \
                'B/S 标记本身必须是圆（arc(x, cy, R)），不是三角形路径'
            # 🔴 `.spmw` 的 display 在**样式表**里，所以开关只能加/去 class ——
            #   写 style.display='' 只是删内联样式、规则照旧生效，表现是
            #   "点了没反应"且不报错（.hlpbox 那条）。所以这里量**可见性**。
            assert not pg.locator('#spmw').is_visible(), \
                '成交明细该默认折起 —— 图上的 B/S 点才是主视角'
            assert '笔' in pg.locator('#spmt').inner_text(), \
                '折起时要留一行摘要（几笔、现持多少）—— 收起后什么都看不到' \
                '的折叠不如不做'
            pg.click('#spmt'); pg.wait_for_timeout(300)
            assert pg.locator('#spmw').is_visible(), '点了展不开'
            row = pg.locator('#spmw tbody tr').first.inner_text()
            for w in ('买入', '16.63' if '16.63' in row else str(f['price'])):
                assert w in row, '明细行少了「%s」：%r' % (w, row)
            pg.click('#spmt'); pg.wait_for_timeout(300)
            assert not pg.locator('#spmw').is_visible(), '再点收不起来'
            # ---- K 线读数必须有【涨跌幅】----
            st = pg.evaluate("() => SP.geo.step")
            bb2 = cv.bounding_box()
            pg.mouse.move(bb2['x'] + (f['x'] - st * 30) / sc,
                          bb2['y'] + bb2['height'] * 0.4)
            pg.wait_for_timeout(500)
            kt = pg.locator('#sptip').inner_text()
            # 🔴 `'%' in kt` 太宽 —— 去掉涨跌幅后「换手 1.22%」里还有个 %，
            #   断言照过（变异实测）。判据是 pctv 输出的**带符号**百分数。
            import re as _re2
            assert _re2.search(r'[+-]\d+\.\d+%', kt) and ('昨收' in kt), \
                ('K 线读数少了涨跌幅或昨收 —— 看 K 线第一个想知道的就是'
                 '"那天涨跌多少"，只给 OHLC 得自己拿收盘除昨收：%r' % kt)
            for w in ('开', '高', '低', '收', '量'):
                assert w in kt, 'K 线读数少了「%s」：%r' % (w, kt)
            notes.append('B/S 圆点 %d 个 · 明细默认折起 · K 线读数带涨跌幅'
                         % d['nh'])
            b.close()
    finally:
        httpd.shutdown()
    return '；'.join(notes)


@case('涨跌配色：0 走中性色 / K 线阳线也实心 / 一处定义（playwright）', tag='web')
def t_updown_color():
    """用户："涨跌幅为0的，不要用红色。K线中的红色柱子，也使用实心柱子。"

      ① `upc` 是**唯一**定义，判据 `> 0` / `< 0` 两头夹 —— `>= 0` 会把
         "平盘"并进"涨"，等于凭空报了个涨
      ② 各视图里不许再有 `x >= 0 ? 涨色 : 跌色` 的副本：原来 `col` 在
         live.js 里写了 4 遍、live-perf.js 2 遍，改一处漏五处**不报错**，
         只是"这块 0 是灰的、那块还是红的"
      ③ K 线柱体：**阳线与阴线的填充密度必须一样**（都是实心）。判据是
         数柱体内部的像素，不是查 `strokeRect` 有没有出现 ——
         后者连注释里的那个词都会算命中（我自己的自检就这么误报过一次）
      ④ 平盘（收=开）的柱子用中性色
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return '跳过（无 playwright）'
    import io as _io
    import re as _re
    import threading
    from http.server import ThreadingHTTPServer

    from assay import server as sv

    # ---- ② 静态：不许有第二份涨跌配色 ----
    BAD = _re.compile(r'>=\s*0\s*\?\s*[\'"]?var\(--up\)')
    for f in _web_files('web', '.js') + _web_files('web', '.html'):
        t = _io.open(os.path.join('web', f), encoding='utf-8').read()
        for ln, line in enumerate(t.splitlines(), 1):
            assert not BAD.search(line), \
                ('%s:%d 自己写了一份涨跌配色（`>= 0 ? 涨色`）—— 它会把 0 '
                 '画成红的。用 common.js 的 `upc`（一处定义）：%s'
                 % (f, ln, line.strip()[:80]))
    cj = _io.open('web/shared/common.js', encoding='utf-8').read()
    m = _re.search(r'const upc\s*=([^;]+);', cj)
    assert m, 'common.js 里没有 upc'
    assert '> 0' in m.group(1) and '< 0' in m.group(1), \
        'upc 必须用 `> 0` / `< 0` 两头夹（`>= 0` 会把 0 并进涨）：%s' % m.group(1)
    assert '--dim' in m.group(1), 'upc 的 0 必须走中性色 --dim'

    httpd = ThreadingHTTPServer(('127.0.0.1', 0), sv.Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as p:
            b = p.chromium.launch()
            pg = b.new_page(viewport={'width': 1400, 'height': 900})
            errs = []
            pg.on('pageerror', lambda e: errs.append(str(e)))
            pg.goto('http://127.0.0.1:%d/stock.html?code=601857.XSHG' % port)
            pg.wait_for_timeout(4000)
            r = pg.evaluate("""() => {
              const cv = document.createElement('canvas');
              cv.width = 200; cv.height = 200;
              cv.style.width = '200px'; cv.style.height = '200px';
              document.body.appendChild(cv);
              /* 数柱体内部的像素密度 —— 空心柱中间是背景色，密度会低一截。
                 🔴 判据必须是**像素**，不是源码里有没有 strokeRect：
                 那个词出现在注释里也算命中（自检误报过一次）。 */
              /* 🔴 **采样位置跟着 drawKChart 返回的几何走，不能写死。**
                 原来硬编码 `getImageData(120, ...)` —— 那是"1 根柱子平分
                 整个画布"时它所在的位置（宽 96px）。2026-09-14 给柱子加了
                 最大宽度（14px）之后那根柱子挪到了 x≈61、宽 10px，
                 采样点落在空白处，密度从 82 掉到 8 —— **失败的是构造不是
                 产品**（好在这条用例自己有"构造不对"的护栏，当场报了出来）。 */
              const dens = (o, c) => {
                const g0 = drawKChart(cv, {bars: [{date: '2026-01-01', open: o,
                  high: Math.max(o, c) + 1, low: Math.min(o, c) - 1,
                  close: c, volume: 100}]});
                const bw = Math.max(1, g0.step * 0.7);
                const x0 = Math.round(g0.X(0) - bw / 2) + 1;
                const wpx = Math.max(2, Math.round(bw) - 2);
                const d = cv.getContext('2d').getImageData(x0, 90, wpx, 20).data;
                let n = 0;
                for(let i = 3; i < d.length; i += 4) if(d[i] > 60) n++;
                return n;
              };
              /* 平盘柱子的颜色：取柱体上那一个像素 */
              const gf = drawKChart(cv, {bars: [{date: '2026-01-01', open: 11,
                high: 12, low: 10, close: 11, volume: 100}]});
              const g = cv.getContext('2d');
              /* 同上：取柱心那一列，不写死 x */
              const px = g.getImageData(Math.round(gf.X(0)), 0, 1, 200).data;
              let flatRGB = null;
              for(let y = 0; y < 200; y++){
                const i = y * 4;
                if(px[i + 3] > 200){ flatRGB = [px[i], px[i+1], px[i+2]]; break; }
              }
              const _g = drawKChart(cv, {bars: [{date: '2026-01-01', open: 10,
                high: 13, low: 9, close: 12, volume: 100}]});
              const _cap = Math.max(2, Math.round(Math.max(1, _g.step * 0.7)) - 2) * 20;
              return {cap: _cap, up: dens(10, 12), dn: dens(12, 10),
                      zero: upc(0), pos: upc(0.01), neg: upc(-0.01),
                      flatRGB: flatRGB};
            }""")
            # ③ 阳线阴线同样实心
            #   ★ 这一条只是**构造有效性护栏**（真正的判据是下一行的
            #     "阳 == 阴"）。阈值按采样窗口的比例给，不写死绝对值 ——
            #     柱宽随画布变，写死 40 的话下次改柱宽又会挂（这次就挂了）。
            #     🔴 但也不能卡太紧：实测 82/160 = 51%，取 0.5 只差 2 个像素，
            #     那种阈值迟早偶发。护栏取 **0.25**，判别交给下一条。
            assert r['up'] > r['cap'] * 0.25 and r['dn'] > r['cap'] * 0.25, \
                '柱体密度太低（阳 %d / 阴 %d）—— 构造不对，这条测不到' \
                % (r['up'], r['dn'])
            assert abs(r['up'] - r['dn']) <= 2, \
                ('阳线密度 %d vs 阴线 %d —— 阳线还是空心的（窄柱时描边中间'
                 '是背景色，看着比阴线淡一档）' % (r['up'], r['dn']))
            # ① 0 走中性
            assert 'dim' in r['zero'], '`upc(0)` 给的是 %r —— 0 不该是涨色' % r['zero']
            assert 'up' in r['pos'] and 'down' in r['neg'], \
                'upc 的正负两侧不对：%r / %r' % (r['pos'], r['neg'])
            # ④ 平盘柱子既不是涨色也不是跌色
            assert r['flatRGB'], '取不到平盘柱子的颜色 —— 构造不对'
            up_rgb = pg.evaluate(
                "() => { const s = getComputedStyle(document.documentElement);"
                "  const h = s.getPropertyValue('--up').trim();"
                "  const d = document.createElement('div'); d.style.color = h;"
                "  document.body.appendChild(d);"
                "  const c = getComputedStyle(d).color;"
                "  return c.match(/\\d+/g).slice(0, 3).map(Number); }")
            assert r['flatRGB'] != up_rgb, \
                '平盘（收=开）的柱子画成了涨色 %r —— 那是凭空报了个涨' % up_rgb
            assert not errs, 'JS 错误：%s' % errs[:2]
            b.close()
    finally:
        httpd.shutdown()
    return ('upc 三态（0 -> %s）· 柱体密度 阳 %d == 阴 %d（都实心）· '
            '平盘柱子 rgb%s 不是涨色' % (r['zero'], r['up'], r['dn'],
                                        tuple(r['flatRGB'])))


@case('K 线对数坐标 / 回撤图顶到 0 且【连续、碰到水面线】（playwright）', tag='web')
def t_log_and_dd():
    """用户："K线图需要支持对数"、"回撤水下图最高刻度应该是 0.0%，没有发生
    回撤的那一段线是不是应该没有颜色？"

      ① 对数坐标要**真的是对数**：判据是**几何等距性** —— 造等比数列
         10/20/40/80，线性轴上间距是 1:2:4，对数轴上必须相等。
         只查"传了 log 参数"抓不到映射写错（那才是会出错的地方）
      ② 回撤图最高刻度是 `0%`：`lineChart` 默认在顶端留 6% 白，会印出
         `+0.4%` —— 那个数**没有意义**（不可能比历史最高还高），
         读的人会当成"曾经超出过"。要传 hiCap
      ③ 回撤 = 0 的那几段**不画**：那是"在水面上"，不是"水下 0.0%"。
         判据是 path 的 `M` 段数 > 1（断开了）
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return '跳过（无 playwright）'
    import glob
    import json as _json
    import threading
    from http.server import ThreadingHTTPServer

    from assay import server as sv

    rid = None
    for m in sorted(glob.glob('runs/*/*/*/meta.json'), reverse=True)[:80]:
        if os.path.isfile(os.path.join(os.path.dirname(m), 'equity.parquet')):
            rid = _json.load(open(m, encoding='utf-8')).get('run_id')
            break
    if not rid:
        return '跳过（没有带权益曲线的归档）'

    httpd = ThreadingHTTPServer(('127.0.0.1', 0), sv.Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    notes = []
    try:
        with sync_playwright() as p:
            b = p.chromium.launch()
            pg = b.new_page(viewport={'width': 1500, 'height': 950})
            errs = []
            pg.on('pageerror', lambda e: errs.append(str(e)))

            # ---- ① 对数坐标的几何等距性 ----
            pg.goto('http://127.0.0.1:%d/stock.html?code=601857.XSHG' % port)
            pg.wait_for_timeout(4000)
            # 「对数」也搬进了设置浮窗（同上）——判据只是入口多一步
            _kset(pg, True)
            assert pg.locator('#kmwrap #klogt').count() == 1 \
                and pg.locator('#kmwrap #klin').count() == 1, \
                '设置里没有「线性 / 对数」这一组'
            _kset(pg, False)
            r = pg.evaluate("""() => {
              const cv = document.createElement('canvas');
              cv.width = 400; cv.height = 400;
              cv.style.width = '400px'; cv.style.height = '400px';
              document.body.appendChild(cv);
              /* 🔴 柱体要**有实体**（open != close）：open==close 画出来是
                 一条 1px 的线，落在半像素上时抗锯齿会把 alpha 压到阈值
                 以下，探针就扫不到 —— 2026-09-15 改了副图布局、主图变高
                 之后当场偶发成"对数轴间距不等"。**失败的是构造不是产品**
                 （同"涨跌配色那条把采样位置写死"那次）。 */
              const bars = [10, 20, 40, 80].map((v, i) => ({
                date: '2026-01-0' + (i + 1), open: v * 0.97, high: v,
                low: v * 0.97, close: v, volume: 100}));
              const probe = lg => {
                const geo = drawKChart(cv, {bars: bars, log: lg});
                const g = cv.getContext('2d');
                const ys = [];
                for(let i = 0; i < 4; i++){
                  const x = Math.round(geo.X(i));
                  const d = g.getImageData(x, 0, 1, 400).data;
                  for(let y = 0; y < 400; y++)
                    if(d[y * 4 + 3] > 150){ ys.push(y); break; }
                }
                return ys;
              };
              const gaps = a => [a[1] - a[0], a[2] - a[1], a[3] - a[2]];
              const lin = probe(false), log = probe(true);
              return {lin: gaps(lin), log: gaps(log), nlin: lin, nlog: log};
            }""")
            assert len(r['nlog']) == 4 and len(r['nlin']) == 4, \
                '探针只取到 %r / %r 个点 —— 构造不对' % (r['nlin'], r['nlog'])
            lg, ln = r['log'], r['lin']
            assert max(ln) - min(ln) > 10, \
                '线性轴上等比数列的间距居然是均匀的 %r —— 构造不对，这条测不到' % ln
            assert max(lg) - min(lg) <= 2, \
                ('对数轴上等比数列 10/20/40/80 的间距是 %r，应当相等 —— '
                 '坐标映射写错了（只查"传了 log 参数"是抓不到这个的）' % lg)
            # 按钮点了要真的重画（不重新取数）
            _klog(pg, True)
            _kset(pg, True)
            assert 'log=1' in pg.url, '「对数」没写进 URL（书签会丢掉这个状态）'
            assert 'on' in (pg.locator('#kmwrap #klogt').get_attribute('class') or ''), \
                '「对数」按钮点了没高亮'
            # ★ 工具条上那句"· 对数"是全收进浮窗之后**唯一**能看出当前坐标
            #   的地方 —— 不跟着变的话，关掉浮窗就再也看不出是哪种坐标。
            assert '对数' in (pg.locator('#ktoolbar #kparam').inner_text() or ''), \
                '切了对数，工具条上那个「⚙ 设置」没把它说出来'
            _kset(pg, False)
            notes.append('对数轴等距（线性 %r -> 对数 %r）' % (ln, lg))

            # ---- ②③ 回撤图 ----
            pg.goto('http://127.0.0.1:%d/#/run/%s' % (port, rid))
            pg.wait_for_timeout(4500)
            pg.evaluate("() => tab(1)")          # 权益页签
            pg.wait_for_timeout(1800)
            d = pg.evaluate("""() => {
              const c2 = document.getElementById('c2');
              if(!c2) return null;
              const ys = [...c2.querySelectorAll('text.ax')]
                .filter(t => +t.getAttribute('x') < 60)
                .map(t => ({y: +t.getAttribute('y'), s: t.textContent}))
                .sort((a, b) => a.y - b.y);
              /* 🔴 取【描边】那条（面积图是 fill、没有 stroke），
                 否则量到的是填充路径，它本来就逐段闭合、M 段数不同。 */
              const path = c2.querySelector('path[stroke]:not([stroke="none"])');
              const dd = path ? path.getAttribute('d') : '';
              /* 曲线最高点（y 最小）与水面线的 y —— 曲线必须真的碰到它 */
              const ys2 = [...dd.matchAll(/[ML]\\s*[\\d.]+\\s+([\\d.]+)/g)]
                            .map(m => +m[1]);
              const zl = [...c2.querySelectorAll('line')]
                .find(l => (l.getAttribute('stroke') || '').includes('91,156,240'));
              return {ticks: ys.map(o => o.s),
                      nM: (dd.match(/M/g) || []).length,
                      topY: ys2.length ? Math.min.apply(null, ys2) : null,
                      zeroY: zl ? +zl.getAttribute('y1') : null,
                      vis: !!(c2.querySelector('svg') || {}).getBoundingClientRect
                           && c2.querySelector('svg').getBoundingClientRect().height > 0};
            }""")
            assert d and d['vis'], \
                '回撤图不可见 —— `#c2` 在 `.pane` 里（样式表 display:none），' \
                '要先 tab(1)'
            assert d['ticks'], '回撤图没有 y 轴刻度'
            top = d['ticks'][0].strip().lstrip('+')
            assert top.startswith('0'), \
                ('回撤图最高刻度是 %r，应当是 0 —— lineChart 默认在顶端留 6%% 白，'
                 '会印出 +0.4%%，而"比历史最高还高"是没有意义的数（要传 hiCap）'
                 % d['ticks'][0])
            # 🔴 **2026-09-14 反过来了**：原来这里断言 `nM > 1`（回撤 0 的那几段
            #   必须断开）。用户指出"0 回撤到有回撤、有回撤到 0 回撤之间没有
            #   相连，看着有点怪" —— 而那不只是观感：每段回撤**两头都不接
            #   水面线**，入水那天凭空开始、出水那天凭空停住，于是"回到历史
            #   最高"这个最明确的事实在图上成了一个**空档**，而空档在图表
            #   惯例里读作"没有数据"，意思正好反了。
            #   ★ **失败的是断言不是产品**（同 `#d_hold .note` 那条）：
            #     不是删掉保护，而是改钉新规矩 —— 连续 + 真的碰到水面线。
            assert d['nM'] == 1, \
                ('回撤曲线断成了 %d 段 —— 现在的规则没有例外：**断开 = 没有'
                 '数据**。回撤为 0 的点要照画，让每段回撤两头都接上水面线'
                 % d['nM'])
            assert d['topY'] is not None and d['zeroY'] is not None, \
                '取不到曲线顶点或水面线的 y（选择器没命中描边那条路径？）'
            assert abs(d['topY'] - d['zeroY']) < 1.0, \
                ('回撤曲线的最高点 y=%.1f 没有落在水面线 y=%.1f 上 —— '
                 '创新高那天回撤正好是 0，曲线必须**碰到**水面线；'
                 '差这一截说明 0 那些点又被抹掉了' % (d['topY'], d['zeroY']))
            notes.append('回撤顶到 %s · 曲线连续(1 段)且碰到水面线(y=%.1f)'
                         % (top, d['zeroY']))
            assert not errs, 'JS 错误：%s' % errs[:2]
            b.close()
    finally:
        httpd.shutdown()
    return '；'.join(notes)


@case('指标：一处定义 / 口径跟行情软件 / 买点触发价是【反解】出来的', tag='fast')
def t_indicators():
    """🔴 2026-09-15 用户要「一个指标模块 + 个股页按需展示 + 买点按指标」。

    改造前 MACD/KDJ/RSI/BOLL 的公式写死在 `stock.indicators()` 里，
    而前端 `kchart.js` 又硬编码了一份"画哪几条线"与配色 —— 加一个指标
    要改三处，**漏掉画的那处不报错**：选了它副图一片空白。

    这条用例钉四件事：
      ① 定义只有一处，且 `series` 里的每个 key 都真能算出来
      ② 参数越界**报错不夹逼**（悄悄改成边界值的话，页面写着 200 画的是 60）
      ③ 口径跟行情软件（KDJ 通用平滑 / BOLL 总体标准差 / ATR-RSI Wilder）
      ④ 买点的触发价是**反解**出来的，且与解析解一致
    """
    from assay import indicators as I
    from assay import stock as st
    notes = []
    bars = st.kline('601857.SH', n=320)['bars']
    cl = [b['close'] for b in bars]

    # ---- ① series 的每个 key 都要算得出来 ----
    # 🔴 判据不是"defs() 返回了几个" —— 而是**画图要的那几个键**真在
    #   calc 的输出里。少一个的话副图上就少一条线，而它不报错。
    for d in I.defs():
        sp = I.spec(d['id'])
        col = sp.calc(bars, None)
        for x in d['series']:
            if x['style'] == 'zero':
                continue
            assert x['key'] in col,                 '%s 声明要画 %s，而 calc 根本没给这个键 —— 副图会少一条线' \
                % (d['id'], x['key'])
            assert any(v is not None for v in col[x['key']]),                 '%s 的 %s 整列都是 None（320 根还算不出来？）' % (d['id'], x['key'])
        assert d['short'] and d['desc'], '%s 没有短名/说明' % d['id']
    notes.append('%d 个指标的 series 逐个能算（%s）'
                 % (len(I.REG), '/'.join(x.id for x in I.REG)))

    # ---- ② 参数越界报错，不夹逼 ----
    for iid, pr in (('kdj', {'n': 9999}), ('macd', {'fast': -1}),
                    ('boll', {'k': 99})):
        try:
            I.compute(bars, iid, pr)
            raise AssertionError('%s 的越界参数 %r 被【悄悄夹逼】了 —— '
                                 '页面上写着一个数、画的是另一个' % (iid, pr))
        except I.IndError:
            pass
    try:
        I.spec('nosuch')
        raise AssertionError('不存在的指标没报错')
    except I.IndError:
        pass
    notes.append('越界与不存在都报错（不夹逼）')

    # ---- ③ 口径：与行情软件的写法逐条对 ----
    # KDJ：K = ((k-1)*前K + RSV)/k（通用平滑），不是 SMA(3)
    kd = I.compute(bars, 'kdj', None)
    i = len(bars) - 1
    h9 = max(b['high'] for b in bars[i - 8:i + 1])
    l9 = min(b['low'] for b in bars[i - 8:i + 1])
    rsv = 50.0 if h9 == l9 else (cl[i] - l9) / (h9 - l9) * 100
    kprev = I.compute(bars[:-1], 'kdj', None)['k'][-1]
    assert abs(kd['k'][-1] - ((2 * kprev + rsv) / 3)) < 0.02, \
        'KDJ 的 K 不是通用平滑（%.4f vs 手算 %.4f）—— 与券商软件对不上时，' \
        '人会以为是数据错了' % (kd['k'][-1], (2 * kprev + rsv) / 3)
    # BOLL：σ 是【总体】标准差（除 N），不是样本（除 N-1）
    bl = I.compute(bars, 'boll', None)
    seg = cl[-20:]
    mu = sum(seg) / 20
    sd_pop = (sum((x - mu) ** 2 for x in seg) / 20) ** 0.5
    sd_smp = (sum((x - mu) ** 2 for x in seg) / 19) ** 0.5
    assert abs(bl['ub'][-1] - (mu + 2 * sd_pop)) < 0.01, 'BOLL 上轨对不上总体标准差'
    assert abs(bl['ub'][-1] - (mu + 2 * sd_smp)) > 1e-4, \
        'BOLL 用的是样本标准差 —— 与行情软件差一点点，而那个差看着像舍入'
    # ATR：Wilder 平滑（1/N 递推），不是简单均值
    at = I.compute(bars, 'atr', None)
    tr = I._tr(bars)
    prev = I.compute(bars[:-1], 'atr', None)['atr'][-1]
    assert abs(at['atr'][-1] - (prev * 13 + tr[-1]) / 14) < 0.005, \
        'ATR 不是 Wilder 平滑'
    notes.append('口径逐条对上行情软件（KDJ 通用平滑 / BOLL 总体σ / ATR Wilder）')

    # ---- ④ 触发价：与【解析解】一致 ----
    # 🔴 这是这次改造的核心：「离金叉还有多远」的答案**不是**今天那个百分比
    #   （MA5 与 MA20 两条都在动），而是"今天收在什么价位就刚好金叉"。
    f, sl = 5, 20
    Sf, Ss = sum(cl[-f:-1]), sum(cl[-sl:-1])     # 【不含今天】，今天那格是待解的 P
    exact = (f * Ss - sl * Sf) / (sl - f)
    px, why = I.trigger_price(bars, 'ma_cross', {'fast': 5, 'slow': 20}, 0.0)
    assert px is not None and abs(px - exact) < 0.01, \
        '金叉触发价与解析解对不上：二分 %r vs 解析 %.4f（%s）' % (px, exact, why)
    S19 = sum(cl[-20:-1])
    exact2 = S19 * 0.97 / (20 - 0.97)
    px2, _ = I.trigger_price(bars, 'ma_dist', {'w': 20}, -3.0)
    assert px2 is not None and abs(px2 - exact2) < 0.01, \
        '距 MA20 的触发价与解析解对不上：%r vs %.4f' % (px2, exact2)
    # 反向自证：触发价两侧必须**刚好翻面**（否则那个数只是"看着正常"）
    n_ok = 0
    for sid, args, v in (('ma_cross', {'fast': 5, 'slow': 20}, 0.0),
                         ('ma_dist', {'w': 20}, -3.0),
                         ('rsi_low', {'w': 6}, 30.0),
                         ('boll_low', {'n': 20, 'k': 2}, 0.0),
                         ('cci_low', {'n': 14}, -100.0)):
        pxx, _w = I.trigger_price(bars, sid, args, v)
        if pxx is None:
            continue
        op = I.sig(sid)['op']
        lo = I.sig_value(I._with_close(bars, pxx * 0.999), sid, args)
        hi = I.sig_value(I._with_close(bars, pxx * 1.001), sid, args)
        ok = (lo <= v < hi) if op == 'le' else (lo < v <= hi)
        assert ok, '%s 的触发价 %s 没夹住阈值（两侧 %s / %s）' % (sid, pxx, lo, hi)
        n_ok += 1
    assert n_ok >= 4, '只验到 %d 条触发价（其余都解不出？）' % n_ok
    # 🔴 反解时 **high/low 必须跟着收盘价改**：价格跌到触发价，那天的最低价
    #   至少是它。只改 close 的话，KDJ/WR 这些读 high/low 的指标会算出
    #   **今天根本不可能出现**的值 —— 判据用它们的定义域（K 与 WR 都是
    #   0~100，数学上跑不出去），比"检查那两行代码在不在"硬。
    #   ★ 上面那 5 条触发价全是只读收盘价的（bias/spread/RSI/BOLL/CCI），
    #     所以这条**必须单独构造**，否则那个变异从头到尾没被执行到。
    for fct in (0.2, 0.5, 0.8, 1.0, 1.4, 2.0):
        bb = I._with_close(bars, cl[-1] * fct)
        kv = I.compute(bb, 'kdj', None)['k'][-1]
        wv = I.compute(bb, 'wr', None)['wr6'][-1]
        assert 0 <= kv <= 100 and 0 <= wv <= 100, \
            ('价格 ×%.1f 时 KDJ 的 K=%s / WR=%s 跑出了 0~100 —— '
             '把收盘挪到那个价位时 high/low 没跟着改' % (fct, kv, wv))
    # 解不出来时**说理由**，不猜一个数
    p3, w3 = I.trigger_price(bars, 'rsi_low', {'w': 6}, 99.0)
    assert p3 is None and w3, '恒成立的条件应该说"恒成立"而不是给个价'
    # 方向：金叉是【涨上去】才成立
    assert I.sig_dir('ma_cross') == 'up' and I.sig_dir('ma_dist') == 'down', \
        '买点条件的方向标反了 —— 一只已经金叉的票会显示成"还差 x%"'
    notes.append('触发价二分 == 解析（金叉 %.3f / 距MA20 %.3f），%d 条两侧翻面自证'
                 % (px, px2, n_ok))

    # ---- ⑤ 老接口的默认输出不许变（改造前后逐位等价已单独验过）----
    d = st.indicators('601857.SH', n=60)
    for k in ('dif', 'dea', 'macd', 'k', 'd', 'jj', 'rsi6', 'ub', 'lb'):
        assert k in d['rows'][-1], '默认那份少了 %s —— 老页面读的就是它' % k
    d2 = st.indicators('601857.SH', n=60, inds='atr,cci')
    assert [x['id'] for x in d2['panels']] == ['atr', 'cci'], \
        'inds= 没按点名的来：%r' % [x['id'] for x in d2['panels']]
    assert 'dif' not in d2['rows'][-1], '没点名的指标也算了（白算一遍）'
    notes.append('默认那份仍带老字段；inds= 只算点名的')
    return '；'.join(notes)


@case('个股页支持 ETF / 指数：搜得到 / K 线与指标照画 / 没有的块明说', tag='fast')
def t_etf_index():
    """🔴 用户 2026-09-16："个股功能应该也要支持 ETF、指数。"

    面板（`mart/panel_daily`）是**股票宽表** —— ETF 与指数一行都没有。
    它们的日线在 `raw/tdx/kline/{index,etf}_*.parquet`（与实盘业绩页那个
    自定义基准同一份数据源）。做法是拼一个**与面板同形**的子查询，
    下游（K 线、全部指标、翻页、复权）一个字都不用改。

    判据五条，每条对着一种不报错的坏法：
      ① **搜得到** —— 搜不到就等于没这个功能（入口即功能）
      ② 完整 symbol **精确命中优先**：`sh000001` 要给上证指数，
         而那 6 位数字同时是平安银行
      ③ **股票优先**：搜"银行"要银行股，而"银行ETF银华"是名称前缀命中、
         "招商银行"只是包含 —— 光按 score 排，一屏全是 ETF
      ④ K 线 / 指标照常算，且**后复权**（ETF 会分红）
      ⑤ 财务 / 同行业 / 板块要**明说"本来就没有"**，不是静默的空
    """
    from assay import market as mk
    from assay import stock as st
    notes = []

    # ---- ① 搜得到 ----
    for q, want in (('红利ETF', 'etf'), ('上证指数', 'index')):
        r = st.search(q)['results']
        assert r and r[0]['kind'] == want, \
            '搜「%s」第一条不是 %s：%r' % (q, want, [(x['name'], x['kind']) for x in r[:3]])
    # ---- ② 完整 symbol 精确命中优先（那 6 位同时是平安银行）----
    r = st.search('sh000001')['results']
    assert r and r[0]['code'] == 'sh000001', \
        ('搜 sh000001 第一条是 %r —— 完整代码精确命中要排最前，'
         '而 000001 同时是平安银行' % (r[0] if r else None))
    assert st.search('000001')['results'][0]['kind'] == 'stock', \
        '打纯数字 000001 该先给股票（平安银行）'
    # ---- ③ 股票优先（"银行ETF银华"是前缀命中、"招商银行"只是包含）----
    rb = st.search('银行')['results']
    assert rb[0]['kind'] == 'stock', \
        ('搜"银行"第一条是 %s「%s」—— 人要的是银行股；ETF 名字以"银行"开头'
         '（前缀命中）会盖过"招商银行"（包含命中）'
         % (rb[0]['kind'], rb[0]['name']))
    notes.append('搜得到（精确命中优先 / 同名时股票优先）')

    # ---- ④ K 线与指标：照常算 + 后复权 ----
    for sym, kind in (('sh510880', 'etf'), ('sh000001', 'index')):
        assert st.alt_kind(sym) == (kind, sym), '%s 没被认成 %s' % (sym, kind)
        k = st.kline(sym, n=60)
        assert len(k['bars']) >= 50 and k['total'] > 1000, \
            '%s 的 K 线取不全：%d 根 / 共 %d' % (sym, len(k['bars']), k['total'])
        b = k['bars'][-1]
        for f in ('open', 'high', 'low', 'close', 'volume', 'preclose',
                  'change_pct', 'ma5', 'ma20'):
            assert b.get(f) is not None, '%s 的 K 线少了 %s' % (sym, f)
        # 🔴 涨跌幅要自洽 —— 它是我们自己用 lag() 算出来的，算错了不报错
        assert abs(b['change_pct'] - (b['close'] / b['preclose'] - 1) * 100) < 1e-6, \
            '%s 的 change_pct 与 close/preclose 对不上' % sym
        ind = st.indicators(sym, n=60, inds='macd,kdj,boll')
        assert len(ind['rows']) == 60 and ind['rows'][-1].get('dif') is not None \
            and ind['rows'][-1].get('k') is not None, \
            '%s 算不出指标' % sym
    # 🔴 **ETF 要后复权**：红利 ETF 的因子 1.0 -> 1.81，那 81% 全是分红；
    #   不复权跨除权日有假跌幅（同「对比页一律后复权」那条）。
    hb = st.kline('sh510880', n=30, fq='hfq')['bars'][-1]['close']
    bb = st.kline('sh510880', n=30, fq='bfq')['bars'][-1]['close']
    assert hb > bb * 1.5, \
        ('红利 ETF 的后复权收盘 %.3f 与不复权 %.3f 差得太少 —— 复权没生效，'
         '而它不报错，只是那条线一直偏低' % (hb, bb))
    # 指数不除权：两者必须相等（`coalesce(hfq_factor, 1)` 正好）
    ih = st.kline('sh000001', n=30, fq='hfq')['bars'][-1]['close']
    ib = st.kline('sh000001', n=30, fq='bfq')['bars'][-1]['close']
    assert abs(ih - ib) < 1e-9, \
        '指数被复权了（%.4f vs %.4f）—— 它不除权，因子表里没有它的行' % (ih, ib)
    notes.append('K 线与指标照画（ETF 后复权 %.2f vs %.2f / 指数不复权）' % (hb, bb))

    # ---- ④b🔴 前复权：ETF 的默认口径 ----
    # 用户 2026-09-16："ETF 怎么只有不复权和后复权的选择，应该默认是前复权吧。"
    # ★ 项目里那条「前复权不提供」保护的是**特征与回测**（基准是"今天"，
    #   每来一次分红整条历史重算一遍）；**展示**是另一回事：不复权的 ETF
    #   图上是一串假跌幅，后复权的纵轴写 6.11 而券商那儿是 3.37 —— 对不上。
    for sym in ('sh510880', '601857.XSHG'):
        q = st.kline(sym, n=250, fq='qfq')['bars']
        b0 = st.kline(sym, n=250, fq='bfq')['bars']
        # 🔴 最新一根 == 不复权（基准是"今天"）—— 这正是它和后复权的分界
        assert abs(q[-1]['close'] - b0[-1]['close']) < 0.01, \
            ('%s 前复权最新一根 %.3f != 不复权 %.3f —— 前复权的基准是"今天"，'
             '最新那根就该是当前实际价' % (sym, q[-1]['close'], b0[-1]['close']))
        # 历史价被往下调（分红从历史里扣掉），且**不等于**不复权
        assert q[0]['close'] < b0[0]['close'], \
            '%s 前复权的历史价没被往下调（%.3f vs %.3f）' % (
                sym, q[0]['close'], b0[0]['close'])
        # 🔴 **往回翻页不许改变同一天的价**：分母必须是**全局最新**因子。
        #   ★ 判据要用 `off > 0` —— 只改窗口大小（n=60 vs n=120）的话，
        #     两种实现取到的都是最新那根，**测不出区别**（变异实测漏过）。
        #     往回翻之后窗口里根本没有最新那根，错的实现当场露馅。
        base = {b['date']: b['close'] for b in st.kline(sym, n=250, fq='qfq')['bars']}
        back = st.kline(sym, n=60, fq='qfq', off=120)['bars']
        assert back, '%s 翻不回去' % sym
        same = [b for b in back if b['date'] in base]
        assert len(same) >= 30, \
            '%s 翻页后只有 %d 天能对照，这条判据成了空转' % (sym, len(same))
        bad = [(b['date'], b['close'], base[b['date']]) for b in same
               if abs(b['close'] - base[b['date']]) > 1e-9]
        assert not bad, \
            ('%s 往回翻页之后同一天的前复权价变了（%r）—— 分母必须是'
             '**全局最新**因子，不是这一屏的最后一根；否则翻一页就换一套刻度，'
             '而图看着一切正常' % (sym, bad[:2]))
        # 每根的 `fqk` 要能把不复权价换算到当前坐标（买卖点靠它落位）
        bad = [i for i, (x, y) in enumerate(zip(b0, q))
               if abs(x['close'] * y['fqk'] - y['close']) > 0.01]
        assert not bad, '%s 有 %d 根的 fqk 换算对不上' % (sym, len(bad))
    # ---- ④c 默认**一律前复权**（由服务端定）----
    # 用户 2026-09-16 分两次定下来的：先"ETF 应该默认前复权"，
    #   再"股票也要默认前复权" —— 理由同一条：主流行情软件的默认就是它。
    for sym in ('sh510880', '601857.XSHG', 'sh000001', '000001.XSHE'):
        assert st.kline(sym, n=20)['fq'] == 'qfq', \
            '%s 的默认口径不是前复权' % sym
    assert st.kline('sh510880', n=20, fq='bfq')['fq'] == 'bfq', \
        '显式指定的口径被默认值盖掉了'
    # ★ 指数走这条也无妨：它不除权，qfq 与 bfq **逐位相同**（上面已验）。
    # 🔴 指标必须**跟着 K 线同一个口径** —— 两边不一致的话，
    #   图上是前复权的价、副图是不复权算的 MACD，而它不报错。
    for sym in ('sh510880', '601857.XSHG'):
        assert st.indicators(sym, n=20, inds='macd')['fq'] \
            == st.kline(sym, n=20)['fq'], '%s 的指标口径与 K 线对不上' % sym
    notes.append('前复权（最新一根=现价 / 翻页自洽 / fqk 可换算）· ETF 默认它')

    # ---- ⑤ 没有的块要**明说**，不是静默的空 ----
    for fn, nm in ((st.finance, '财务'), (st.events, '事件'), (st.peers, '同行业'),
                   (mk.stock_sectors, '所属板块')):
        for sym in ('sh510880', 'sh000001'):
            r = fn(sym)
            assert r.get('not_applicable') and r.get('why'), \
                ('%s 对 %s 返回了一个**静默的空** —— "本来就没有"与'
                 '"没取到"必须分得出来，否则页面上那张空表会被读成数据坏了'
                 % (nm, sym))
    # 🔴 `links` 反过来：**ETF 是能买的**（项目里就有 ETF 轮动策略），
    #   实盘持有过、回测选过它都讲得通；指数才买不了。
    assert not st.links('sh510880').get('not_applicable'), \
        'ETF 是能买的，「我的持仓与回测」这一块对它有意义'
    assert st.links('sh000001').get('not_applicable'), \
        '指数买不了，查"我持有多少上证指数"本身就没有意义'
    # 股票那条路一个字都不许变
    for fn in (st.finance, st.events, st.peers):
        assert not fn('601857.XSHG').get('not_applicable'), \
            '股票被误判成了 ETF/指数'
    notes.append('财务/同行业/板块明说"没有这项"·ETF 保留实盘与回测联动')
    return '；'.join(notes)


@case('个股页支持 ETF / 指数：页面真能打开且标出是什么（playwright）', tag='web')
def t_etf_index_web():
    """接口通不等于页面能用 —— 这一页有十个接口并发，任何一个对 ETF/指数
    抛异常都会让整页打不开，而"打不开"与"这只票没数据"在屏幕上长得一样。
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
    base = 'http://127.0.0.1:%d' % port
    notes = []
    try:
        with sync_playwright() as p:
            try:
                br = p.chromium.launch()
            except Exception as e:                          # noqa: BLE001
                return '跳过（浏览器不可用: %s）' % type(e).__name__
            pg = br.new_page(viewport={'width': 1500, 'height': 1100})
            errs = []
            pg.on('pageerror', lambda e: errs.append(str(e)))
            for sym, tag, nm in (('sh510880', 'ETF', '红利ETF华泰柏瑞'),
                                 ('sh000001', '指数', '上证指数')):
                pg.goto(base + '/stock.html?code=' + sym, wait_until='networkidle')
                pg.wait_for_selector('#kcv', timeout=40000)
                pg.wait_for_timeout(1500)
                assert nm in (pg.locator('.lvhead h2').inner_text() or ''), \
                    '%s 的标题不是它的名字' % sym
                # 🔴 **一眼看得出这不是股票**：这一页所有数字长得和股票一样，
                #   而 ETF 有折溢价与管理费损耗、指数根本没有成交对手 ——
                #   不标的话把指数当股票读只是时间问题（同模拟盘那个紫标签）。
                tags = [x.strip() for x in
                        pg.locator('.lvhead .lvtag').all_inner_texts()]
                assert tag in tags, \
                    '%s 页面上没标出它是 %s：%r' % (sym, tag, tags)
                # 画布真的画了（画崩了就是一张空白画布，而它不报错）
                px = pg.evaluate("""() => {const cv = document.getElementById('kcv');
                    const g = cv.getContext('2d');
                    const d = g.getImageData(0, 0, cv.width, cv.height).data;
                    let n = 0; for (let i = 3; i < d.length; i += 4) if (d[i]) n++;
                    return n;}""")
                assert px > 30000, '%s 的 K 线没画出来（%d 个像素）' % (sym, px)
                # 三个复权口径在「⚙ 设置」浮窗里，且默认那个是亮的
                _kset(pg, True)
                fqs = pg.evaluate(
                    "()=>[...document.querySelectorAll('#kmwrap .fq')]"
                    ".map(a=>a.dataset.f)")
                assert fqs == ['bfq', 'qfq', 'hfq'], \
                    '复权那三个按钮不全：%r' % fqs
                on = pg.evaluate(
                    "()=>[...document.querySelectorAll('#kmwrap .fq.on')]"
                    ".map(a=>a.dataset.f)")
                assert on == ['qfq'], \
                    ('%s 默认高亮的是 %r，应当是前复权 —— 默认口径由服务端定，'
                     '页面要把它读回来，否则三个按钮一个都不亮、'
                     '人看不出现在是哪种口径' % (sym, on))
                _kset(pg, False)
                # 不适用的块要**说一句**，不是空白
                for t in ('所属板块', '同行业', '财务'):
                    txt = pg.evaluate("""(t) => {
                        const h = [...document.querySelectorAll('.lvsec h3')]
                          .find(x => x.textContent.indexOf(t) >= 0);
                        if (!h) return null;
                        const n = h.parentElement.querySelector('.none');
                        return n ? n.textContent.trim() : '(有内容)';}""", t)
                    assert txt and ('不是股票' in txt), \
                        ('%s 的「%s」那块写的是 %r —— 要明说"本来就没有"，'
                         '空着会被读成数据没取到' % (sym, t, txt))
            # 搜索入口：从股票页搜 ETF -> 选中 -> 真的跳过去
            pg.goto(base + '/stock.html?code=601857.XSHG', wait_until='networkidle')
            pg.wait_for_selector('#kcv', timeout=40000)
            pg.wait_for_timeout(1200)
            pg.fill('#sbox .skq', '红利ETF华泰柏瑞')
            pg.wait_for_timeout(1000)
            pg.keyboard.press('ArrowDown')
            pg.keyboard.press('Enter')
            pg.wait_for_selector('#kcv', timeout=40000)
            pg.wait_for_timeout(1500)
            assert 'sh510880' in pg.url, \
                '搜索里选中 ETF 没跳过去：%s' % pg.url
            assert not errs, 'JS 报错：%r' % errs[:3]
            notes.append('ETF 与指数页面都打得开（标出是什么 / 图真画了 / '
                         '不适用的块明说）· 搜索选中能跳过去')
            br.close()
    finally:
        httpd.shutdown()
    return '；'.join(notes)


@case('个股页：副图是【槽位】/ 左上角下拉框选指标 / 参数在弹窗里（playwright）',
      tag='web')
def t_stock_multi_sub():
    """🔴 这一页的指标选择器**改了三版**，每版都是用户当场指出来的：

        v1 工具条上平铺一排标签（一个指标一个）
           -> "指标数量上去后一横排也放不下"
        v2 一个按钮 + 竖排勾选面板（参数也在面板里）
           -> "点击指标会导致整个页面重新刷新"
           -> "改参数会让页面高度、宽度变化，影响整个页面的布局"
        v3（现在）**副图 = 槽位**：默认两个，点「+ 副图」加（最多 4），
           每个槽位**左上角一个下拉框**选显示什么，参数在**弹窗**里改

    判据各有各的构造条件，混在一起测全是空转：
      ① 默认就是**两个**槽位，且第一个是成交量（它不再是画死的一块）
      ② 下拉框**浮在对应副图的左上角**（按 KGEO 摆，不是写死坐标）
      ③ 换指标 / 加 / 删：只重取**指标那一个接口**、DOM 不重建
      ④ 参数弹窗开关时**页面布局一个像素都不许动**（这正是用户的原话）
      ⑤ 到上限要**说一句**，不许静默不动
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
    base = 'http://127.0.0.1:%d' % port
    notes = []
    try:
        with sync_playwright() as p:
            try:
                br = p.chromium.launch()
            except Exception as e:                          # noqa: BLE001
                return '跳过（浏览器不可用: %s）' % type(e).__name__
            pg = br.new_page(viewport={'width': 1500, 'height': 1200})
            errs, reqs = [], []
            pg.on('pageerror', lambda e: errs.append(str(e)))
            pg.on('request', lambda r: reqs.append(r.url))
            pg.goto(base + '/stock.html?code=601857.XSHG',
                    wait_until='networkidle')
            pg.wait_for_selector('#kcv', timeout=40000)
            pg.wait_for_timeout(1200)

            # ---- ① 默认两个槽位，第一个是成交量 ----
            assert pg.evaluate('() => KINDS') == ['vol', 'macd'], \
                ('默认副图不是【成交量 + MACD】两个：%r —— 用户要的是'
                 '"下面默认两个副图的位置"' % pg.evaluate('() => KINDS'))
            # 🔴 成交量**不再是画死的一块**：它就是第一个槽位的默认指标，
            #   所以能被换掉。判据是"换掉之后 KINDS 里就没有它了"。
            assert pg.evaluate('() => KGEO.nSub') == 2, \
                '画布上不是两个副图：%r' % pg.evaluate('() => KGEO.nSub')
            # 🔴 判据：**第一个副图紧贴主图**（中间只隔一个 GAP）——
            #   成交量要是还画死在中间那一块，这里就会多出一截，
            #   而图上"看着差不多"根本看不出来。
            _g = pg.evaluate('() => ({mt: KGEO.mainTop, mh: KGEO.mainH,'
                             ' st: KGEO.subTop, gap: KGEO.GAP})')
            assert abs(_g['st'] - (_g['mt'] + _g['mh'] + _g['gap'])) < 1, \
                ('主图与第一个副图之间还夹着一块（主图底 %.0f + gap %d '
                 '!= 副图顶 %.0f）—— 成交量已经是一个槽位了，'
                 '不该再画死一块' % (_g['mt'] + _g['mh'], _g['gap'], _g['st']))

            # ---- ② 下拉框浮在**对应副图**的左上角 ----
            sel = pg.locator('#kslots .kssel')
            assert sel.count() == 2, '副图左上角没有下拉框（实得 %d 个）' % sel.count()
            geo = pg.evaluate("""() => {
                const c = document.querySelector('#kcv').getBoundingClientRect();
                return [...document.querySelectorAll('#kslots .kslot')]
                  .map(e => { const r = e.getBoundingClientRect();
                    return {j: +e.dataset.j, top: r.top - c.top,
                            left: r.left - c.left,
                            want: KGEO.subY(+e.dataset.j)}; });}""")
            for g in geo:
                assert abs(g['top'] - g['want']) < 6 and 0 < g['left'] < 120, \
                    ('第 %d 个副图的下拉框没落在它左上角：实 top=%.0f / '
                     '应 %.0f（按 KGEO 摆，别自己算坐标）'
                     % (g['j'], g['top'], g['want']))
            notes.append('默认 2 个槽位（成交量+MACD），下拉框各就各位')

            # ---- ③ 换指标：只重取指标接口、DOM 不重建 ----
            # 🔴 用户："点击指标会导致整个页面重新刷新，这个需要调整。"
            #   判据要两头钉：接口没多打 + DOM 没重建（只比接口数的话，
            #   重建出一模一样的 DOM 也算通过，而 hover、选中的文字、
            #   滚动位置已经断了）。
            # 🔴🔴 判据要钉到**每个元素**，不能只钉 `#kcv`。
            #   用户 2026-09-16 又报了一次："切换副图时整个页面会闪一下。"
            #   —— 而上面那条断言当时是**绿的**：`#kcv` 确实没被换，
            #   被 `innerHTML` 整块换掉的是工具条 / 槽位层 / 加副图行。
            #   代价是**用户刚点的那个 `<select>` 被销毁重建**：焦点没了、
            #   原生控件重绘，看上去就是闪一下。
            #   ★ 这也是"第一次切换"才暴露的：签名若存在变量里，首屏那几块
            #     是 `load()` 拼模板直接吐的、不经过 reloadInd，于是初值永远
            #     不等 —— **每次打开页面的第一次切换照样重建**。所以标记要在
            #     页面刚加载后就打，并且**第一次**切换就验。
            MARK = ("""() => { let i = 0;
                document.querySelectorAll(
                  '#kpickbox a, #kslots .kslot, #kslots select, #kaddrow a')
                  .forEach(e => e.dataset.mk = 'k' + (i++));
                return i; }""")
            CHK = ("""() => [...document.querySelectorAll(
                  '#kpickbox a, #kslots .kslot, #kslots select, #kaddrow a')]
                  .map(e => e.dataset.mk || 'NEW')""")
            reqs.clear()
            pg.evaluate("() => { document.querySelector('#kcv')"
                        ".dataset.mark = 'keep'; }")
            _n_mk = pg.evaluate(MARK)
            assert _n_mk >= 5, '控件只找到 %d 个，这条判据怕是没扫到' % _n_mk
            pg.locator('#kslots .kssel[data-j="1"]').select_option('kdj')
            pg.wait_for_timeout(1400)
            _mk = pg.evaluate(CHK)
            assert 'NEW' not in _mk, \
                ('换一个副图指标重建了 %d 个控件（%r）—— 这三块没有一块跟着'
                 '指标变（工具条是设置+主图开关、槽位是下拉框+⚙+×、'
                 '「+副图」只跟个数有关），换掉它们就是把用户刚点的那个'
                 'select 销毁重建：焦点没了、控件重绘，屏幕上就是闪一下'
                 % (_mk.count('NEW'), _mk))
            _heavy = [u for u in reqs if '/api/stock/profile' in u
                      or '/api/stock/finance' in u or '/api/stock/peers' in u]
            assert not _heavy, \
                ('换一个副图指标把整页那十个接口又打了一遍：%r' % _heavy[:3])
            assert pg.evaluate(
                "() => document.querySelector('#kcv').dataset.mark") == 'keep', \
                '换指标把 #body 整块重建了（画布都换了新的）—— 页面会跳一下'
            # 🔴🔴 换指标**一个请求都不该打**。用户 2026-09-16 第三次反馈
            #   "切换附图时还是会明显感觉跳动一下" —— 逐帧量过：DOM 零变化、
            #   整页 242 帧位置不动、屏幕帧的像素差也只落在副图那一块。
            #   剩下的就是那几百毫秒：点完下拉框，旧图还杵在那儿，等接口
            #   回来才"啪"地换成新图。**那个突变就是跳动感的来源**。
            #   实测一次取全部 10 个副图 47.6KB/29ms、只取 2 个 13.4KB/28ms
            #   —— 几乎一样快，所以一次全取、缓存住，切换零请求。
            assert not [u for u in reqs if '/api/stock/indicators' in u], \
                ('换个副图指标还去打了一次指标接口 —— 数据早就在手上了'
                 '（一次全取），那几百毫秒的"旧图停一下再换新图"正是'
                 '用户说的跳动')
            # 🔴 **反向自证缓存键**：换复权 / 换区间 / 翻页必须重取。
            #   只钉"零请求"的话，把缓存写成"永远命中"也全绿 ——
            #   而那会拿着不复权的数字去画后复权的图，且不报错。
            for _lbl, _sel in (('区间', '#ktoolbar .rg[data-n="60"]'),
                               ('区间2', '#ktoolbar .rg[data-n="120"]')):
                reqs.clear()
                pg.locator(_sel).click()
                pg.wait_for_timeout(1600)
                assert [u for u in reqs if '/api/stock/indicators' in u], \
                    ('换%s之后没重取指标 —— 缓存键里少了它，'
                     '画出来的是上一个口径的数字而不报错' % _lbl)
            pg.locator('#ktoolbar .rg[data-n="250"]').click()
            pg.wait_for_timeout(1600)
            # 回到默认之后再验一次：换指标仍然零请求
            reqs.clear()
            pg.locator('#kslots .kssel[data-j="1"]').select_option('rsi')
            pg.wait_for_timeout(1400)
            assert not [u for u in reqs if '/api/stock/' in u], \
                '换回默认口径之后，换指标又开始打接口了：%r' % reqs[:3]
            pg.locator('#kslots .kssel[data-j="1"]').select_option('kdj')
            pg.wait_for_timeout(1200)
            assert pg.evaluate('() => KINDS') == ['vol', 'kdj'], \
                '下拉框换的不是【那个槽位】：%r' % pg.evaluate('() => KINDS')
            # 换完之后那个副图画的真是 KDJ（拦 fillText 看图例）
            drew = pg.evaluate(r"""() => {
                const cv = document.getElementById('kcv');
                const g = cv.getContext('2d');
                const orig = g.fillText.bind(g);
                const seen = [];
                g.fillText = function (t, x, y) { seen.push(String(t));
                                                  return orig(t, x, y); };
                try { drawKChart(cv, {bars: BARS, subs: kSubs()}); }
                finally { g.fillText = orig; }
                return seen;}""")
            assert any(t.startswith('K ') for t in drew) \
                and not any(t.startswith('DIF') for t in drew), \
                '换成 KDJ 之后画的还是 MACD：%r' % drew[:8]
            notes.append('换指标只重取指标接口、一个控件都不重建')

            # ★ 另外两条路径**各自钉各自的规矩**（同实盘页 quiet/全量那条）：
            #   勾主图只该动主图那条线、加副图才动槽位层 ——
            #   只钉"换指标不重建"的话，把判据写成"永远不重建"也全绿，
            #   而那会让加/删副图之后 `data-j` 不重排（换错槽位且不报错）。
            _SLOT = ("""() => [...document.querySelectorAll('#kslots .kslot')]
                .map(e => e.dataset.mk || 'NEW')""")
            pg.evaluate(MARK)
            _kmain(pg, 'boll', True)
            assert 'NEW' not in pg.evaluate(_SLOT), \
                ('勾一个主图指标把副图那几组控件也重建了 —— 它们跟主图无关'
                 '（主图是叠在 K 线上的，副图是下面的槽位）')
            assert pg.evaluate("() => KMAIN.indexOf('boll') >= 0"), \
                '勾了 BOLL 但状态位没变'
            pg.evaluate(MARK)
            pg.locator('#kaddrow #kadd').click()
            pg.wait_for_timeout(1400)
            assert 'NEW' in pg.evaluate(_SLOT), \
                ('加了一个副图但槽位层没重建 —— 新槽位的 data-j 不重排的话，'
                 '换指标会换错那个槽位，而它不报错')
            notes.append('勾主图不动副图槽位 · 加副图才重建槽位层')

            # ---- ③c🔴 工具条只留三样，其余全在「⚙ 设置」里 ----
            # 用户 2026-09-16："添加主图的方式仍然不够友好，可以打开一个浮窗，
            #   在里面勾选，最多勾选 3 个这样，不然排版总是感觉太挤。
            #   前复权/后复权/不复权、对数/线性图也一样，全部变成主图的设置选项。"
            _kset(pg, False)
            _tool = [x.strip() for x in
                     pg.locator('#ktoolbar a').all_inner_texts()]
            for _bad in ('不复权', '前复权', '后复权', '对数', '线性', '+ 主图'):
                assert not any(_bad in t and '设置' not in t for t in _tool), \
                    ('「%s」还留在工具条上：%r —— 它们该收进「⚙ 设置」浮窗'
                     % (_bad, _tool))
            assert pg.locator('#ktoolbar #evck').count() == 0, \
                '「事件」那个勾还留在工具条上'
            # 留下的是**看的时候一直在调**的那两组
            assert pg.locator('#ktoolbar .kpan').count() == 3 \
                and pg.locator('#ktoolbar .rg').count() >= 4, \
                '工具条上该留着翻页与区间：%r' % _tool
            # 🔴 全收进浮窗之后，"现在是哪种口径"在屏幕上就只剩这一处
            _kfq(pg, 'hfq')
            assert '后复权' in (pg.locator('#ktoolbar #kparam').inner_text() or ''), \
                ('切了后复权，工具条上那个「⚙ 设置」没把它说出来 —— '
                 '关掉浮窗之后就再也看不出当前是哪种口径了')
            # 🔴 在浮窗里改设置，浮窗**不许自己消失**（原来走 load() 重建
            #   整个 #body，而浮窗就在里面 —— 点完一个就得重新点开）
            _kset(pg, True)
            pg.locator('#kmwrap .fq[data-f="bfq"]').click()
            pg.wait_for_timeout(2000)
            assert pg.locator('#kmwrap .stbox').is_visible(), \
                ('在设置里点了复权，浮窗自己没了 —— 它就在 #body 里，'
                 '走整页 `load()` 会把它一起换掉')
            assert pg.evaluate('()=>FQ') == 'bfq', '复权没切过去'
            _kset(pg, False)
            notes.append('工具条只留翻页+区间+⚙（其余进浮窗，改完浮窗不消失）')

            # ---- ③d🔴 主图叠加**有上限**，到了要说清怎么腾位置 ----
            # 用户 2026-09-16："主图选择的方式也改一下，如果后续主图的数量
            #   增多，都要显示不下了。要限制叠加主图的数量，不能无限制叠加。"
            # 🔴 本地主图指标统共两个（均线 / BOLL），**凑不出默认的 3 个**
            #   —— 这条在真实数据上是空转的，必须把上限压下来才测得到
            #   （同指标广场用 `?per=` 把每页个数压到 3 才测得到分页）。
            _kmain(pg, 'boll', False)
            _real_max = pg.evaluate('() => KMAIN_MAX')
            assert _real_max >= 2, '上限 %r 太小，正常使用都会被挡' % _real_max
            pg.evaluate("() => { KMAIN_MAX = 1; }")
            try:
                _kset(pg, True)
                pg.evaluate("() => kModal(true)")      # 按新上限重渲染浮窗
                pg.wait_for_timeout(300)
                # 已勾的那个（均线）还能点（要留出"去掉一个"的路），
                # 而**没勾的那些被禁用**并说清怎么腾位置
                assert pg.locator('#kmwrap .kmain[data-i="boll"]').is_disabled(), \
                    '到上限了，没勾的那些还能接着勾 —— 上限形同虚设'
                assert not pg.locator('#kmwrap .kmain[data-i="ma"]').is_disabled(), \
                    ('已经叠上去的那个也被禁用了 —— 那就再也去不掉，'
                     '人被锁死在当前这一组里')
                _m = pg.locator('#kmwrap .kmgrid').inner_text()
                assert '1' in _m and ('上限' in _m or '去掉' in _m), \
                    ('到上限只是静默点不动（写的是 %r）—— 要说清怎么腾位置'
                     '（同「+ 副图」那条）' % _m[-60:])
                # 🔴 数据那道也要有：URL 里手写 `main=a,b,c,d` 绕得过界面
                assert pg.evaluate("() => kToggleMain('boll')") is False, \
                    '到上限了 kToggleMain 还返回成功 —— 数据层那道上限没有'
                assert pg.evaluate("() => KMAIN.indexOf('boll') < 0"), \
                    '被拒了却还是叠上去了'
            finally:
                pg.evaluate("(v) => { KMAIN_MAX = v; }", _real_max)
                pg.evaluate("() => kModal(true)")
                pg.wait_for_timeout(200)
                _kset(pg, False)
            # URL 那条路也截断（手写超限时只认前 N 个）
            assert pg.evaluate(
                "() => { const u = new URL(location.href);"
                " u.searchParams.set('main', 'ma,boll,ma,boll');"
                " history.replaceState(null, '', u);"
                " const r = mainFromUrl().length;"
                " u.searchParams.set('main', KMAIN.join(',') || '0');"
                " history.replaceState(null, '', u); return r; }") == _real_max, \
                ('URL 里手写超限的 `main=` 没被截到 %d 个 —— 绕过按钮之后'
                 'K 线上十来条线全糊在一起，而它不报错' % _real_max)
            notes.append('主图叠加有上限（%d 个）· 到了说清怎么腾位置 · '
                         'URL 那条路也截断' % _real_max)
            # 收拾回原样，后面的断言照旧从两个槽位起步
            pg.locator('#kslots .ksdel[data-j="2"]').click()
            pg.wait_for_timeout(1200)
            _kmain(pg, 'boll', False)

            # ---- ③c🔴 换区间 / 复权时**不许把已经画好的内容清掉** ----
            # 用户 2026-09-16（第三次）："切换附图时还是会明显感觉跳动一下。"
            #   `load()` 原来一进来就把 `#body` 换成「加载中…」——
            #   **高度先塌陷再撑开**，那就是跳动（实盘页早为这条改过，
            #   见 CLAUDE.md「刷新只换数字，不许重建 DOM」）。而「区间 /
            #   复权」这几个按钮就挨着副图的下拉框，点它们同样整页塌陷。
            # ★ 判据要在**请求还没回来的那一刻**看，所以把接口拖慢。
            _h_before = pg.evaluate("() => document.body.scrollHeight")
            # ★ 把**一个**接口扣住不放行，就能停在"还没回来"的那一刻。
            #   🔴 扣住的请求**必须自己放回去**（`continue_`）：留着不管的话
            #     这一页的连接一直挂着，`networkidle` 永远不到，后面的用例
            #     跟着一起超时（全量跑时实测挂了 4 条，而单独跑都绿 ——
            #     "单跑通过、全量失败"这次不是缓存，是我没收拾干净）。
            #   ★ route handler 里**不要调页面 API**（`pg.wait_for_timeout`
            #     之类）—— 它跑在事件回调里，会把自己锁死。
            # 🔴 另一头：换区间/翻页**不许把整页那十个接口又打一遍**。
            #   只钉"没塌陷"是不够的 —— `load()` 现在也不清空内容了，
            #   于是"又走了一遍 load"这件事在画面上看不出来，而它会把
            #   财务、事件、板块、同行业、我的持仓全重取一遍（变异实测漏过）。
            _r2 = []
            pg.on('request', lambda r: _r2.append(r.url)
                  if '/api/stock/' in r.url or '/api/live/trades_of' in r.url
                  else None)
            pg.locator('#ktoolbar .rg[data-n="120"]').click()
            pg.wait_for_timeout(2200)
            _heavy = [u for u in _r2 if any(
                x in u for x in ('profile', 'finance', 'events', 'peers',
                                 'sectors', 'links', 'trades_of'))]
            assert not _heavy, \
                ('换区间把整页那批接口又打了一遍：%r —— 这几样只影响 K 线'
                 '那一块，profile 的 KPI、财务、板块一个都不跟着变'
                 % [u.split('/api/')[-1].split('?')[0] for u in _heavy][:4])
            assert [u for u in _r2 if 'kline' in u], '换区间连 K 线都没重取'
            _held = []
            pg.route('**/api/stock/finance*', lambda r: _held.append(r))
            pg.locator('#ktoolbar .rg[data-n="60"]').click()
            pg.wait_for_timeout(700)
            assert pg.locator('#kcv').count() == 1, \
                ('换区间时把画布清掉了（换成了「加载中…」）—— 高度先塌陷'
                 '再撑开就是用户说的"跳动一下"；已经有内容时该留着旧的，'
                 '新数据到了一次性换')
            _h_mid = pg.evaluate("() => document.body.scrollHeight")
            assert abs(_h_mid - _h_before) < 4, \
                '换区间的加载过程中页面高度变了 %d -> %d' % (_h_before, _h_mid)
            for _r in _held:
                try: _r.continue_()
                except Exception: pass            # noqa: BLE001
            pg.unroute('**/api/stock/finance*')
            pg.reload(wait_until='networkidle')
            pg.wait_for_selector('#kcv', timeout=40000)
            pg.wait_for_timeout(1200)
            notes.append('换区间/复权不清空已画好的内容（不塌陷）')

            # ---- ④ 加 / 删槽位，到上限要说一句 ----
            for _ in range(3):
                pg.click('#kadd')
                pg.wait_for_timeout(900)
            assert pg.evaluate('() => KINDS.length') == 4, \
                '「+ 副图」加不到 4 个：%r' % pg.evaluate('() => KINDS')
            assert pg.evaluate('() => KGEO.nSub') == 4, '画布上没画出 4 个副图'
            assert len(set(pg.evaluate('() => KINDS'))) == 4, \
                '加出来的槽位重复了 —— 新槽位该默认放还没用上的那个'
            pg.click('#kadd')
            pg.wait_for_timeout(500)
            _m = pg.locator('#kimsg').inner_text()
            assert '4' in _m and pg.evaluate('() => KINDS.length') == 4, \
                ('到上限时既没拦住也没说明：%r —— 点了没反应是最难查的'
                 '那种坏' % _m)
            # 🔴 这句话要**自己说得完整**。用户 2026-09-15 把它读成了
            #   "先去掉一个主图"：原来提示排在工具条里、紧挨着
            #   「主图 [均线][布林带]」，屏幕上就是"…先去掉一个｜主图 均线"。
            #   判据两条：提示**不许**待在放主图开关的那个容器里，
            #   且它自己要点明说的是"副图"。
            assert not pg.evaluate(
                "() => !!document.querySelector('#kpickbox #kimsg')"), \
                ('副图的提示又跑回工具条里了 —— 它会紧挨着「主图」那两个'
                 '开关，被读成"先去掉一个主图"（主图与副图各算各的，不冲突）')
            assert '副图' in _m, '提示没说清是"副图"到上限了：%r' % _m
            _before = pg.evaluate('() => KINDS.slice()')
            pg.locator('#kslots .ksdel[data-j="0"]').click()
            pg.wait_for_timeout(1000)
            assert pg.evaluate('() => KINDS') == _before[1:], \
                '「×」删的不是那个槽位：%r -> %r' % (_before,
                                                pg.evaluate('() => KINDS'))
            # 🔴 用户："添加副图的按钮应该放在主图的最下面，或者说当前
            #   最后一个副图的下面。" —— 那才是你看完最后一个副图、想再加
            #   一个时手停的位置。判据是**几何**：它必须在画布下边界之下。
            _ab = pg.evaluate(
                "() => {const c = document.querySelector('#kcv')"
                ".getBoundingClientRect();"
                " const a = document.querySelector('#kadd')"
                ".getBoundingClientRect();"
                " return {cb: c.bottom, at: a.top};}")
            assert _ab['at'] >= _ab['cb'] - 1, \
                ('「+ 副图」不在图的下面（按钮 top=%.0f / 画布底=%.0f）—— '
                 '它该贴着最后一个副图，而不是躲在工具条里'
                 % (_ab['at'], _ab['cb']))
            notes.append('+ 副图 在图下方 / × 删槽位 / 到 4 个有提示且说清是副图')

            # ---- ⑤ 参数在【弹窗】里改，且开关它布局一个像素都不动 ----
            # 🔴 用户原话："修改指标的参数应该在单独的地方（至少是一个单独的
            #   弹窗，不然每次点击导致页面高度、宽度变化，会影响整个页面的
            #   布局）。" —— 所以判据不是"能改参数"，是**开关弹窗时页面高度
            #   与画布尺寸一个像素都不许变**。
            _h0 = pg.evaluate("() => [document.body.scrollHeight,"
                              " document.querySelector('#kcv')"
                              ".getBoundingClientRect().height]")
            pg.click('#kparam')
            pg.wait_for_timeout(400)
            assert pg.locator('#kmwrap .stbox').is_visible(), '参数弹窗打不开'
            _h1 = pg.evaluate("() => [document.body.scrollHeight,"
                              " document.querySelector('#kcv')"
                              ".getBoundingClientRect().height]")
            assert _h0 == _h1, \
                ('开参数弹窗把页面布局挤动了：%r -> %r —— 用户明确说这就是'
                 '要避免的（弹窗必须是 fixed 浮层）' % (_h0, _h1))
            _k0 = pg.evaluate("() => IND[IND.length - 1].k")
            box = pg.locator('#kmwrap .kpi[data-id="kdj"][data-k="n"]').first
            assert box.count() == 1, '弹窗里没有 KDJ 的参数框'
            box.fill('19')
            box.dispatch_event('change')
            pg.wait_for_timeout(1600)
            assert pg.evaluate("() => IND[IND.length - 1].k") != _k0, \
                'KDJ 周期 9 -> 19，K 值却没变 —— 参数没送到服务端'
            _h2 = pg.evaluate("() => [document.body.scrollHeight,"
                              " document.querySelector('#kcv')"
                              ".getBoundingClientRect().height]")
            assert _h0 == _h2, '改完参数页面布局变了：%r -> %r' % (_h0, _h2)
            assert pg.locator('#kmwrap .stbox').is_visible(), \
                '改完一个参数弹窗就自己关了 —— 要改第二个还得重新点开'
            # 🔴 改完一个参数，**光标还在那个框里**。原来 `kApplyInd` 会把
            #   整个弹窗重渲染一遍 —— 输入框被换掉，失焦 + 光标位置丢失，
            #   想连着改两个参数得重新点一次；而且销毁正聚焦的元素会让那次
            #   `innerHTML` 赋值**直接失败**（`Perhaps it was moved in a
            #   'blur' event handler?`），只在控制台里报。
            assert pg.evaluate(
                "() => { const a = document.activeElement;"
                " return !!(a && a.classList && a.classList.contains('kpi')"
                " && a.dataset.id === 'kdj'); }"), \
                ('改完参数焦点跑了 —— 弹窗被整块重渲染，'
                 '想接着改第二个参数还得重新点进那个框')
            box.fill('9999')
            box.dispatch_event('change')
            pg.wait_for_timeout(500)
            # ★ 提示要在**弹窗里**（就在那个输入框旁边）：扔到图下面那行去的话，
            #   人盯着输入框却看不到为什么被退回来。
            assert '之间' in pg.locator('#kmwrap #kmmsg').inner_text(), \
                ('越界参数没在弹窗里当场拒：%r'
                 % pg.locator('#kmwrap').inner_text()[:120])
            pg.click('#kprst')
            pg.wait_for_timeout(1600)
            assert abs(pg.evaluate("() => IND[IND.length - 1].k") - _k0) < 1e-9, \
                '「恢复默认参数」没还原'
            pg.keyboard.press('Escape')
            pg.wait_for_timeout(300)
            assert pg.locator('#kmwrap').count() == 0, 'Esc 关不掉弹窗'
            notes.append('参数在弹窗里改：布局一动不动、越界当场拒、Esc 关得掉')

            # ---- ⑥ URL 带得走 ----
            pg.goto(base + '/stock.html?code=601857.XSHG&sub=kdj,rsi',
                    wait_until='networkidle')
            pg.wait_for_selector('#kcv', timeout=40000)
            pg.wait_for_timeout(1000)
            assert pg.evaluate('() => KINDS') == ['kdj', 'rsi'], \
                'URL 里的 sub=kdj,rsi 没生效（分享出去的链接看到的是另一张图）'
            pg.goto(base + '/stock.html?code=601857.XSHG&sub=0',
                    wait_until='networkidle')
            pg.wait_for_selector('#kcv', timeout=40000)
            pg.wait_for_timeout(800)
            assert pg.evaluate('() => KINDS.length') == 0 \
                and pg.evaluate('() => KGEO.nSub') == 0, \
                '「一个副图都不要」存不住'
            notes.append('URL 带得走（sub=kdj,rsi / sub=0）')
            assert not errs, 'JS 报错：%r' % errs[:3]
            br.close()
    finally:
        httpd.shutdown()
    return '；'.join(notes)


@case('买点也能按【指标】：触发价现算 / 方向不是都朝下（playwright）', tag='web')
def t_alerts_by_indicator():
    """🔴 2026-09-15 用户："买点功能不仅可以根据价格提示，还可以根据指标
    提示买点，比如距离 20 日线的距离、MA5/MA20 金叉的距离。"

    ★ 做法上的关键一步：指标条件**反解成一个触发价**（"今天收在什么价位，
      这个条件刚好成立"）。于是它和价格档落在同一个口径上，这一页那套
      「到价 / 接近 / 还差多少」一行都不用改。
      直接报"现在离金叉还差 2.3%"是**会骗人**的：MA5 与 MA20 两条都在动，
      那个百分比推不出"涨到多少就金叉"。

    三条判据：
      ① 存的是**条件**、不是触发价（触发价每天都不一样，存下来第二天就错）
      ② **方向**：金叉是涨上去才成立 —— 记反的话，已经金叉的票会显示成"还差"
      ③ 页面上要说清是**哪个条件**（只给一个价，事后没法复盘）
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return '跳过（无 playwright）'
    import io as _io
    import json as _json
    import shutil
    import tempfile
    import threading
    from http.server import ThreadingHTTPServer

    import re as _re

    from assay import alerts as al
    from assay import indicators as I
    from assay import server as sv
    from assay import stock as _st
    old_live, old_allow = al.LIVE, sv.ALLOW_LIVE
    _o_fetch, _o_extpath = al._ext_fetch, al.ext_path
    sv.ALLOW_LIVE = True
    tmp = tempfile.mkdtemp()
    al.LIVE = tmp                    # ★ 不往真账本里写测试数据
    al.ext_path = lambda root=None: os.path.join(tmp, 'div_ext.json')
    al._ext_fetch = lambda cs, day=None: {}
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), sv.Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = 'http://127.0.0.1:%d' % port
    notes = []
    try:
        # ---- ① 服务端：存条件、算触发价、方向 ----
        # ★ 这个阈值**运行时挑**：写死一个数的话，它可能落在"今天到不了"
        #   的区间里（state='na'），于是"还要涨 x%"那条路又是空转的
        #   （实测踩过：v=20 解不出触发价，页面显示的是"就算涨到 17.78 也
        #   到不了"，而我的判据只查了个"涨"字，被那句话蒙混过关）。
        _bars = _st.kline('601857.SH', n=320)['bars']
        _cur = _bars[-1]['close']
        _upv = None
        for _c in (1, 2, 3, 5, 8, 12):
            _px, _w = I.trigger_price(_bars, 'ma_cross',
                                      {'fast': 5, 'slow': 20}, float(_c))
            if _px and _px > _cur * 1.015:
                _upv = float(_c)
                break
        assert _upv is not None, '挑不出一个"解得出但还没到"的金叉阈值'
        al.set_row('601857.SH', [
            {'by': 'price', 'v': 8.0},
            {'by': 'ind', 'sig': 'ma_dist', 'args': {'w': 20}, 'v': -3},
            {'by': 'ind', 'sig': 'ma_cross', 'args': {'fast': 5, 'slow': 20},
             'v': 0},
            # ★ 再来一档**远没到**的 up 档：上面那档常常已经金叉（state=hit），
            #   只验它的话“还要涨 x%”那条路**一次都不会执行**
            #   （变异实测：把涨/跌写死成“跌”照样全绿）。
            {'by': 'ind', 'sig': 'ma_cross', 'args': {'fast': 5, 'slow': 20},
             'v': _upv},
        ])
        # ★ 再加**只有一个 up 档、且整行都没到**的一只 —— 上面那只整行是
        #   'hit'，于是「状态」那列走的是"到价 · 第 N 档"分支，
        #   "还要涨/跌 x%" 那条路**一次都不执行**（变异实测：把它写死成
        #   "还要跌"照样全绿）。
        _b2 = _st.kline('600900.SH', n=320)['bars']
        _c2 = _b2[-1]['close']
        _upv2 = None
        for _c in (1, 2, 3, 5, 8, 12):
            _px2, _ = I.trigger_price(_b2, 'ma_cross',
                                      {'fast': 5, 'slow': 20}, float(_c))
            # 🔴 要 `far` 就得**离得够远**：near 阈值是 3%，只要求
            #   +2% 的话触发价会落进"接近"区间，那一行的状态就是 near，
            #   而"还要涨 x%"那条路只在 far 时才走到（实测偶发）。
            if _px2 and _px2 > _c2 * 1.07:
                _upv2 = float(_c)
                break
        assert _upv2 is not None, '第二只票也挑不出"解得出但还没到"的阈值'
        al.set_row('600900.SH', [
            {'by': 'ind', 'sig': 'ma_cross', 'args': {'fast': 5, 'slow': 20},
             'v': _upv2}])
        raw = [_json.loads(l) for l in _io.open(
            os.path.join(tmp, 'alerts.jsonl'), encoding='utf-8')]
        # ★ 认准**那条记录**，不是"最后一行" —— 账本里后面还追加了第二只票，
        #   拿 raw[-1] 会取到另一行（实测当场 IndexError）。
        tiers = [r for r in raw if r.get('code', '').startswith('601857')][-1]['tiers']
        assert tiers[1] == {'by': 'ind', 'sig': 'ma_dist', 'args': {'w': 20},
                            'v': -3.0}, \
            ('账本里存的不是【条件本身】：%r —— 存触发价的话第二天就是错的'
             '（均线在动），同「每档存的是你填的那个，另一个现算」' % tiers[1])
        v = al.valued()
        row = [r for r in v['rows'] if r['code'].startswith('601857')][0]
        row2 = [r for r in v['rows'] if r['code'].startswith('600900')][0]
        assert row2['state'] == 'far' and row2['next'] \
            and row2['next'].get('dir') == 'up' and row2['next']['gap'] > 0, \
            ('第二只票该是"还没到的 up 档"，实得 %r'
             % {k: row2.get(k) for k in ('state', 'next')})
        ind = [t for t in row['tiers'] if t['by'] == 'ind']
        assert len(ind) == 3, '三个指标档没都出来：%r' % row['tiers']
        far_up = [t for t in ind if t['sig'] == 'ma_cross'
                  and t['v'] == _upv][0]
        assert (far_up['dir'] == 'up' and far_up['state'] in ('far', 'near')
                and far_up['price'] and far_up['gap'] > 0), \
            ('那档要"涨上去"的金叉必须是【解得出且还没到】的，实得 %r —— '
             '否则页面上"还要涨 x%%"那条路一次都走不到' % far_up)
        cross = [t for t in ind if t['sig'] == 'ma_cross'][0]
        dist = [t for t in ind if t['sig'] == 'ma_dist'][0]
        assert cross['dir'] == 'up' and dist['dir'] == 'down', \
            ('方向标错了（金叉 %r / 距均线 %r）—— 一只已经金叉的票会被'
             '显示成"还要涨 x%%"' % (cross['dir'], dist['dir']))
        assert cross['price'] and dist['price'], \
            '触发价没算出来：%r' % [(t['sig'], t['price'], t['why']) for t in ind]
        # 触发价必须**跟着行情走**，不是账本里存着的死数
        assert cross['text'] and 'MA5' in cross['text'], \
            '没给出这条件的人话：%r' % cross['text']
        # 状态判定要**按方向**：把金叉那档按"跌到"判的话，结论正好反
        pr = row['price']
        want = 'hit' if pr >= cross['price'] else (
            'near' if pr >= cross['price'] * (1 - row['near_used']) else 'far')
        assert cross['state'] == want, \
            ('金叉那档的状态按方向应是 %s，实得 %s（现价 %s / 触发价 %s）'
             % (want, cross['state'], pr, cross['price']))
        notes.append('账本存条件；触发价现算（距MA20 %s / 金叉 %s）；方向 down/up'
                     % (dist['price'], cross['price']))

        # 通知文案要写清**是哪个条件**触发的
        for rec in al.check_fire(v):
            if rec.get('cond'):
                txt = al.notify_text(rec)
                assert rec['cond'] in txt, \
                    '通知没写清是哪个条件触发的：%r' % txt

        with sync_playwright() as p:
            try:
                br = p.chromium.launch()
            except Exception as e:                          # noqa: BLE001
                return '跳过（浏览器不可用: %s）' % type(e).__name__
            pg = br.new_page(viewport={'width': 1600, 'height': 1100})
            errs = []
            pg.on('pageerror', lambda e: errs.append(str(e)))
            pg.goto(base + '/alerts.html', wait_until='networkidle')
            pg.wait_for_selector('table.pkt', timeout=40000)

            # ---- ② 条件清单【由服务端给】，页面不写死 ----
            page_ids = pg.evaluate('() => ASIG.map(x => x.id)')
            assert page_ids == [x['id'] for x in I.signal_defs()], \
                ('页面上的条件清单与服务端对不上：%r —— 写死一份的话，'
                 '加一条条件它根本选不到' % page_ids)

            # ---- ③ 表里要说清是哪个条件 + 方向对的措辞 ----
            txt = pg.locator('table.pkt').inner_text()
            assert cross['text'] in txt, \
                '表里没写出条件本身（只有一个价，事后没法复盘）：%s' % txt[:300]
            cell = pg.evaluate("""() => {
                const tds = [...document.querySelectorAll('table.pkt td')];
                const td = tds.find(e => e.innerText.indexOf('MA5') >= 0);
                return td ? td.innerText : null;}""")
            assert cell, '找不到金叉那一档的格子'
            need = '已站上' if cross['state'] == 'hit' else '涨'
            assert need in cell, \
                ('金叉那档写的是 %r —— 方向反了（它是涨上去才成立，'
                 '不是"还要跌"）' % cell)
            # 🔴 **还没到**的那档必须写"涨 x%"：一律写"跌"的话，一只离金叉
            #   还差 20% 的票会被读成"还要跌 20%"，意思正好反了。
            cell2 = pg.evaluate(
                "(t) => {const tds = [...document.querySelectorAll("
                "'table.pkt td')];"
                " const td = tds.find(e => e.innerText.indexOf(t) >= 0);"
                " return td ? td.innerText : null;}", far_up['text'])
            # 🔴 判据要认准**那句涨跌措辞**（"涨 8.3%"），不能只查一个"涨"字 ——
            #   解不出触发价时页面写的是"就算涨到 17.78 也到不了"，
            #   里面也有个"涨"（实测被它蒙混过关过一次）。
            assert cell2 and _re.search(r'涨\s*\d', cell2) \
                and not _re.search(r'跌\s*-?\d', cell2), \
                ('还没到的金叉档写的是 %r —— 它要涨上去才成立，'
                 '写"还要跌"意思正好反了' % cell2)
            # 整行「状态」那列也要认方向（它走的是另一条分支：x.next）
            st2 = pg.evaluate(
                "(c) => {const tr = [...document.querySelectorAll("
                "'table.pkt tr')].find(e => e.innerText.indexOf(c) >= 0);"
                " if (!tr) return null; const td = tr.cells[tr.cells.length - 2];"
                " return td ? td.innerText : null;}", '600900')
            assert st2 and _re.search(r'涨\s*\d', st2) \
                and not _re.search(r'跌\s*-?\d', st2), \
                ('整行状态写的是 %r —— 那一行唯一没到的是个"涨上去才成立"'
                 '的条件，写"还要跌"意思正好反了' % st2)
            notes.append('页面给出条件本身与方向正确的措辞（档 %r / 整行 %r）'
                         % (cell.replace('\n', ' '), (st2 or '').strip()))

            # ---- ④ 改一行：回填的是【条件】不是触发价 ----
            pg.locator('a.aed').first.click()
            pg.wait_for_timeout(600)
            sel = pg.locator('.asig')
            assert sel.count() == 3, '编辑器里没回填出三个指标档：%d' % sel.count()
            assert pg.evaluate(
                "() => FORM.tiers.filter(t => t.by === 'ind')"
                ".every(t => t.sig && t.args && t.v != null)"), \
                ('回填的指标档缺 sig/args/阈值 —— 照触发价回填的话，'
                 '"距 MA20 -3%" 会变成一个死价格')
            # 改成 RSI 超卖并保存：服务端要按新条件重算
            sel.first.select_option('rsi_low')
            pg.wait_for_timeout(400)
            pg.click('#asave')
            pg.wait_for_timeout(2500)
            got = pg.evaluate("""() => {
                const r = (V.rows || [])[0] || {};
                return (r.tiers || []).filter(t => t.by === 'ind')
                       .map(t => t.sig);}""")
            assert 'rsi_low' in got, '换成 RSI 超卖没存进去：%r' % got
            notes.append('回填的是条件本身，换条件能存进去')
            assert not errs, 'JS 报错：%r' % errs[:3]
            br.close()
    finally:
        httpd.shutdown()
        al.LIVE, sv.ALLOW_LIVE = old_live, old_allow
        al._ext_fetch, al.ext_path = _o_fetch, _o_extpath
        shutil.rmtree(tmp, ignore_errors=True)
    return '；'.join(notes)


@case('指标广场：主图/副图分页签 + 分页只取当前页 / 每张卡有真图（playwright）',
      tag='web')
def t_indicator_plaza():
    """🔴 2026-09-15 用户："查看全部指标的地方在哪里？应该有指标广场。"

    在此之前，指标只能在个股页工具条上看到一排**短名 + tooltip** ——
    "CCI 到底是什么、怎么算的、能不能拿来当买点"哪儿都答不了。

    这一页的判据：
      ① **照服务端那份清单渲染**（页面里没有任何指标名）—— 写死一份的话，
         加一个指标它在这儿根本不出现，而那不报错
      ② 每张卡有**真图**：只列公式的话"它长什么样"还得回个股页一个个试，
         而那正是广场存在的理由
      ③ 「在个股页看」要**真的选中那个指标**（不是链过去就算）
      ④ 两个入口都通（独立页面最大的风险是页面之间断链）
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return '跳过（无 playwright）'
    import threading
    from http.server import ThreadingHTTPServer

    from assay import indicators as I
    from assay import server as sv
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), sv.Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = 'http://127.0.0.1:%d' % port
    notes = []
    defs, sigs = I.defs(), I.signal_defs()
    try:
        with sync_playwright() as p:
            try:
                br = p.chromium.launch()
            except Exception as e:                          # noqa: BLE001
                return '跳过（浏览器不可用: %s）' % type(e).__name__
            pg = br.new_page(viewport={'width': 1500, 'height': 1200})
            errs = []
            pg.on('pageerror', lambda e: errs.append(str(e)))
            pg.goto(base + '/indicators.html', wait_until='networkidle')
            pg.wait_for_selector('.icard', timeout=40000)
            pg.wait_for_timeout(1500)

            # ---- ① 主图 / 副图**分两个页签**（用户 2026-09-15）----
            # 它们是两回事：主图那几条与 K 线共用价格坐标，副图各自一套。
            # 混在一个流里的话，"哪些能叠在 K 线上"得一张张卡去看。
            want_main = [d['id'] for d in defs if d['panel'] == 'main']
            want_sub = [d['id'] for d in defs if d['panel'] == 'sub']
            tabs = pg.locator('#tabs .itab')
            assert tabs.count() == 2, '没有分成两个页签：%d' % tabs.count()
            _tt = ' '.join(pg.locator('#tabs').inner_text().split())
            assert str(len(want_main)) in _tt and str(len(want_sub)) in _tt, \
                '页签上没写各有几个：%r' % _tt
            ids = pg.evaluate(
                "() => [...document.querySelectorAll('.icard')]"
                ".map(e => e.dataset.i)")
            assert ids == want_main, \
                ('主图页签列的不是主图那几个：%r / 应为 %r' % (ids, want_main))
            pg.locator('#tabs .itab[data-t="sub"]').click()
            pg.wait_for_timeout(1500)
            # 🔴 副图**一页放不下**（每页 9 个），所以判据是「翻完所有页
            #   刚好覆盖服务端那份清单、顺序也对」——只验第一页的话，
            #   第 2 页漏掉一个不会有人发现（而它不报错）。
            #   ★ 这比原来那条"一页必须等于全部"更强，也不会因为以后调
            #     每页个数就过期。
            # 🔴 副图**一页放不下**（每页 9 个），所以判据要**翻完所有页**：
            #   只验第一页的话，第 2 页少一张卡不会有人发现（而它不报错）。
            #   ★ 逐卡那几条内容断言也跟着进循环 —— 它们对**每一页**都该成立，
            #     留在外面的话会去找不在当前页上的卡，表现是 30 秒超时、
            #     看着像页面坏了（实测踩过）。
            ids, seen, blank = [], 0, []
            while True:
                cur = pg.evaluate(
                    "() => [...document.querySelectorAll('.icard')]"
                    ".map(e => e.dataset.i)")
                ids += cur
                for d in [x for x in defs if x['id'] in cur]:
                    card = pg.locator('.icard[data-i="%s"]' % d['id'])
                    t = card.inner_text()
                    assert d['short'] in t and d['desc'][:8] in t, \
                        '%s 这张卡缺短名或说明' % d['id']
                    fml = card.locator('.ifml').inner_text().strip()
                    assert fml and fml != '（略）' \
                        and d['formula'].split('\n')[0][:10] in fml, \
                        ('%s 没写出公式（广场要回答"怎么算的"）：%r'
                         % (d['id'], fml))
                    for a in d['params']:
                        assert a['label'] in t, \
                            '%s 少了参数「%s」' % (d['id'], a['label'])
                    dots = card.locator('.idot').count()
                    n_line = len([x for x in d['series'] if x['style'] != 'zero'])
                    assert dots == n_line, \
                        ('%s 的色块 %d 个、实际要画 %d 条线 —— 对不上的话，'
                         '"图上哪条线是哪个"就只能靠猜' % (d['id'], dots, n_line))
                # 🔴 图**必须真的画了**：判据是画布上的非透明像素。
                #   只查 <canvas> 在不在的话，画崩了（尺寸算错/坐标 NaN）
                #   表现就是一张空白画布，而它不报错。
                blank += [x for x in pg.evaluate(
                    """() => [...document.querySelectorAll('.icv')]
                    .map(cv => {const g = cv.getContext('2d');
                      const d = g.getImageData(0, 0, cv.width, cv.height).data;
                      let n = 0; for (let i = 3; i < d.length; i += 4) if (d[i]) n++;
                      return [cv.dataset.i, n];})""") if x[1] < 3000]
                nxt = pg.locator('#inext')
                if not nxt.count() or 'off' in (nxt.get_attribute('class') or ''):
                    break
                seen += 1
                assert seen < 20, '翻页翻不完 —— 「下一页」没有终点'
                nxt.click()
                pg.wait_for_timeout(1400)
            assert not blank, '这几张卡是空白的：%r' % blank
            assert ids == want_sub, \
                ('把所有页翻完列的不是副图那几个：%r / 应为 %r' % (ids, want_sub))
            assert seen >= 1, \
                ('副图只有一页 —— "翻完所有页"这条判据是空转的'
                 '（共 %d 个）' % len(want_sub))
            pg.goto(base + '/indicators.html?tab=sub', wait_until='networkidle')
            pg.wait_for_selector('.icard', timeout=40000)
            pg.wait_for_timeout(1200)
            ids = pg.evaluate(
                "() => [...document.querySelectorAll('.icard')]"
                ".map(e => e.dataset.i)")
            # 顶栏点亮的是**父级**（个股），不是它自己：NAV 里没有这一项，
            # 传一个不存在的 key 会让整条顶栏一个都不亮
            assert '个股' in pg.locator('#top .btn.nav.on').inner_text(), \
                '子页没点亮父级「个股」：%s' % pg.locator('#top .btn.nav.on').inner_text()

            notes.append('两个页签各列各的（主图 %d / 副图 %d），图都真画了'
                         % (len(want_main), len(want_sub)))

            # ---- ②b 分页：**只向服务端要当前这一页那几个指标** ----
            # 🔴 用户："考虑如果指标数量非常多（几十上百个），怎么分页"。
            #   这一页的开销**不在列表**，在于每张卡都要一张真图 ——
            #   一次算完上百个再画上百张画布，页面必卡。所以判据不是
            #   "分了页"，而是**请求里只带这一页的 id**。
            # ★ 用 `per=3` 把分页逼出来：真实只有十来个指标，
            #   不压小每页个数的话这段永远是"第 1/1 页"，等于没测。
            _api = []
            pg.on('request', lambda r: _api.append(r.url)
                  if '/api/stock/indicators' in r.url else None)
            pg.goto(base + '/indicators.html?tab=sub&per=3',
                    wait_until='networkidle')
            pg.wait_for_selector('.icard', timeout=40000)
            pg.wait_for_timeout(900)
            _p1 = pg.evaluate("() => [...document.querySelectorAll('.icard')]"
                              ".map(e => e.dataset.i)")
            assert _p1 == want_sub[:3], '第一页不是前 3 个：%r' % _p1
            import urllib.parse as _up
            _got = _up.unquote(_api[-1].split('inds=')[1].split('&')[0])
            assert _got.split(',') == _p1, \
                ('请求里带的不是这一页那几个指标（%r vs 页面 %r）—— '
                 '上百个指标时这就是"一次全算"与"只算 3 个"的区别' % (_got, _p1))
            assert '1/%d 页' % ((len(want_sub) + 2) // 3) \
                in pg.locator('#ipinfo').inner_text(), \
                '分页信息不对：%r' % pg.locator('#ipinfo').inner_text()
            pg.click('#inext')
            pg.wait_for_timeout(1500)
            _p2 = pg.evaluate("() => [...document.querySelectorAll('.icard')]"
                              ".map(e => e.dataset.i)")
            assert _p2 == want_sub[3:6] and 'page=2' in pg.url, \
                '翻页没换内容或没进 URL：%r / %s' % (_p2, pg.url)
            _got2 = _up.unquote(_api[-1].split('inds=')[1].split('&')[0])
            assert _got2.split(',') == _p2, \
                '翻页之后请求的还是上一页那几个：%r' % _got2
            _px2 = pg.evaluate("""() => [...document.querySelectorAll('.icv')]
                .map(cv => {const g = cv.getContext('2d');
                  const d = g.getImageData(0, 0, cv.width, cv.height).data;
                  let n = 0; for (let i = 3; i < d.length; i += 4) if (d[i]) n++;
                  return n;})""")
            assert len(_px2) == 3 and min(_px2) > 3000, \
                '第二页的图没画出来：%r' % _px2
            notes.append('分页只取当前页（per=3：%s -> %s）'
                         % (','.join(_p1), ','.join(_p2)))

            # ---- ②c 搜索：上百个指标时，靠翻页是翻不到的 ----
            pg.fill('#iq', '均线')
            pg.wait_for_timeout(1800)
            _hit = pg.evaluate("() => [...document.querySelectorAll('.icard')]"
                               ".map(e => e.dataset.i)")
            assert _hit and all(
                '均线' in (d['desc'] + d['formula'] + d['label'])
                for d in defs if d['id'] in _hit), \
                '搜索结果不对：%r' % _hit
            assert 'q=' in pg.url, '搜索词没进 URL（刷新就丢）'
            pg.fill('#iq', 'zzz没有这个')
            pg.wait_for_timeout(1800)
            assert '没有匹配' in pg.locator('#pg').inner_text(), \
                '搜不到时没有空态提示（一片空白看着像坏了）'
            notes.append('搜索（名称/说明/公式）+ 空态')
            pg.goto(base + '/indicators.html?tab=sub', wait_until='networkidle')
            pg.wait_for_selector('.icard', timeout=40000)
            pg.wait_for_timeout(800)

            # ---- ③ 「在个股页看」要真的选中那个指标 ----
            href = pg.locator('.icard[data-i="kdj"] a.ilink').get_attribute('href')
            assert 'sub=kdj' in href, \
                ('「在个股页看」没带上这个指标：%r —— 点过去还是默认那张图，'
                 '等于这个入口是摆设' % href)
            pg.goto(base + href, wait_until='networkidle')
            pg.wait_for_selector('#kcv', timeout=40000)
            pg.wait_for_timeout(1200)
            assert pg.evaluate('() => KINDS') == ['kdj'], \
                '点「在个股页看」过去之后没选中 KDJ：%r' % pg.evaluate('() => KINDS')
            notes.append('「在个股页看」真的把那个指标选上了')

            # ---- ④ 两个入口都通（断链是独立页面最大的风险）----
            pg.goto(base + '/stock.html?code=601857.XSHG', wait_until='networkidle')
            pg.wait_for_selector('#kcv', timeout=40000)
            pg.wait_for_timeout(800)
            # ★ 入口在工具条那个**齿轮**里（用户 2026-09-16："现在找不到指标
            #   广场的入口了……个股页面里加个设置按钮（齿轮），可以在里面
            #   进入指标广场"）。入口一直在，但那个按钮当时叫「⚙ 参数」、
            #   而广场链接排在弹窗**最底下**跟「恢复默认参数」挤一起 ——
            #   **一个叫"参数"的按钮，没人会指望里面有"都支持哪些指标"**。
            _gear = pg.locator('#kparam').inner_text()
            assert '⚙' in _gear and '参数' not in _gear, \
                ('工具条那个按钮写的是 %r —— 它管的不只是参数（里面还有'
                 '指标广场），叫"参数"的话人不会去点' % _gear)
            pg.click('#kparam')
            pg.wait_for_timeout(400)
            assert pg.locator('#kmore').count() == 1, \
                '设置弹窗里没有去指标广场的入口 —— '\
                '那里只有参数框，"这个指标是什么、怎么算的"答不了'
            # 🔴 它要排在**最前面**：排在最后跟「恢复默认参数」挤一起时，
            #   用户就是找不到（这一轮的起因）。判据取**位置**不是"存在"。
            _pos = pg.evaluate("""() => {
                const box = document.querySelector('#kmwrap .stbox');
                const a = document.querySelector('#kmore');
                const kids = [...box.children];
                return [kids.findIndex(c => c.contains(a)), kids.length]; }""")
            assert _pos[0] <= 1, \
                ('指标广场的入口排在弹窗第 %d/%d 块 —— 它要在最前面：'
                 '"都支持哪些指标"是进来之前就想问的，而下面那些只回答'
                 '"这几个怎么调"' % (_pos[0] + 1, _pos[1]))
            pg.locator('#kmore').click()
            pg.wait_for_selector('.icard', timeout=40000)
            assert '/indicators.html' in pg.url, \
                '个股页那个入口点了没去成广场：%s' % pg.url
            pg.goto(base + '/alerts.html', wait_until='networkidle')
            pg.wait_for_timeout(1200)
            n_entry = pg.evaluate(
                "() => [...document.querySelectorAll('a')]"
                ".filter(a => (a.getAttribute('href') || '')"
                ".indexOf('/indicators.html') >= 0).length")
            # 买点页的入口在编辑器里（挑条件那一格）——先打开编辑器
            if not n_entry and pg.locator('a.aed').count():
                pg.locator('a.aed').first.click()
                pg.wait_for_timeout(600)
                n_add = pg.locator('#atadd')
                if n_add.count():
                    n_add.click()
                    pg.wait_for_timeout(300)
                    aby = pg.locator('.aby')
                    aby.nth(aby.count() - 1).select_option('ind')
                    pg.wait_for_timeout(400)
                n_entry = pg.evaluate(
                    "() => [...document.querySelectorAll('a')]"
                    ".filter(a => (a.getAttribute('href') || '')"
                    ".indexOf('/indicators.html') >= 0).length")
            assert n_entry >= 1, \
                '买点页挑指标条件的地方没有「指标说明」入口 —— '\
                '"CCI 超卖是什么"在那儿没法回答'
            # ---- 首页与口径字典页也要能进（用户问的就是"首页没有入口吗"）----
            pg.goto(base + '/', wait_until='networkidle')
            pg.wait_for_timeout(2500)
            _h = pg.evaluate(
                "() => [...document.querySelectorAll('#main a')]"
                ".filter(a => (a.getAttribute('href') || '')"
                ".indexOf('/indicators.html') >= 0).length")
            assert _h >= 1, \
                ('首页上没有去指标广场的入口 —— 一个只能从个股页工具条里'
                 '摸到的入口等于没有入口（用户原话："首页没有地方进入吗"）')
            pg.goto(base + '/#/docs', wait_until='networkidle')
            pg.wait_for_timeout(2000)
            _d = pg.evaluate(
                "() => [...document.querySelectorAll('#main a')]"
                ".filter(a => (a.getAttribute('href') || '')"
                ".indexOf('/indicators.html') >= 0).length")
            assert _d >= 1, \
                '口径字典页没有指向指标广场的交叉引用 —— 找"口径"的人会先去那儿'
            notes.append('四处入口都通：个股页 / 买点页 / 首页 / 口径字典')

            # ---- ⑤ 买点条件表：与服务端逐条对上，且方向写出来了 ----
            pg.goto(base + '/indicators.html', wait_until='networkidle')
            pg.wait_for_selector('table.lvt', timeout=40000)
            tb = pg.locator('table.lvt').inner_text()
            for sg in sigs:
                assert sg['label'] in tb and sg['sample'] in tb, \
                    '买点条件表里少了「%s」' % sg['label']
            assert '涨到才成立' in tb and '跌到才成立' in tb, \
                ('买点条件表没写方向 —— 金叉是涨上去才成立，'
                 '不写的话会被当成"跌到就买"')
            notes.append('%d 条买点条件逐条对上且标了方向' % len(sigs))

            # ---- ⑤🔴 同一行的几张卡，【图的位置必须一样高】 ----
            # 用户 2026-09-16："每个图里面公式、说明占的空间和下面图占的
            #   空间位置相对要固定，不然图不对齐不太好看。"
            # 说明与公式长短不一，不锁高度的话同一行几张卡的画布各在各的
            # 高度上 —— 要横向比形态时眼睛得上下找。
            pg.goto(base + '/indicators.html?tab=sub', wait_until='networkidle')
            pg.wait_for_selector('.icard .icv', timeout=40000)
            pg.wait_for_timeout(1500)
            n_card = pg.locator('.icard').count()
            assert n_card <= 9, \
                ('副图一页 %d 张 —— 用户说"6 个或者 9 个已经够了，超出则分页"'
                 % n_card)
            rows = pg.evaluate("""() => {
              const g = {};
              document.querySelectorAll('.icard').forEach(c => {
                const cv = c.querySelector('.icv');
                if(!cv) return;
                const k = Math.round(c.getBoundingClientRect().top);
                (g[k] = g[k] || []).push(Math.round(cv.getBoundingClientRect().top));
              });
              return g; }""")
            # 🔴 反向自证：真的有"一行多张"，否则这条判据是空转的
            wide = [v for v in rows.values() if len(v) > 1]
            assert wide, '每行只有一张卡（视口 %d 宽），这条判据测不到' % 1500
            for tops in rows.values():
                assert len(set(tops)) == 1, \
                    ('同一行的画布没对齐：%r —— 说明/公式长短不一时，'
                     '文字区必须锁住高度（.ihead），否则图各在各的高度上' % tops)
            notes.append('每页 %d 张 · 同一行 %d 张卡的画布 top 完全一致'
                         % (n_card, max(len(v) for v in rows.values())))

            # ---- ⑥ 文字过长只显示几行，可展开 ----
            # ★ 「展开」**只在真的被截断时**才露出来 —— 常驻一个点了没变化
            #   的按钮比不给更糟（同 backLink 那条）。
            cut = pg.evaluate("""() => [...document.querySelectorAll('.icard')]
              .map(c => ({id: c.dataset.i,
                          cut: [...c.querySelectorAll('.iclamp')].some(
                                 e => e.scrollHeight - e.clientHeight > 2),
                          btn: !c.querySelector('.imore').hidden}))""")
            for r in cut:
                assert r['cut'] == r['btn'], \
                    ('%s 截断=%s 而「展开」可见=%s —— 两者必须一致：没截断还'
                     '摆个按钮是"点了没变化"，截断了不给按钮是把内容藏没了'
                     % (r['id'], r['cut'], r['btn']))
            # 🔴 反向自证：真有一张被截断，否则上面那条是"全 False == 全 False"
            hit = [r['id'] for r in cut if r['cut']]
            assert hit, \
                ('没有一条说明/公式被截断 —— 收起的行数给得太宽松，'
                 '这条功能和它的判据都是空转的')
            # 🔴 截断必须是 `-webkit-line-clamp` 干的（**按整行切**），
            #   不能是别的机制把它压扁的。踩过：`.ihead` 写成 flex 容器时，
            #   flex 子项的 display 被 blockify，`-webkit-box` 当场变成
            #   `flow-root` —— **line-clamp 整个失效**，而 flex 仍然会把超出
            #   的子项压扁，于是"看着还是截断了、展开也还能用"，
            #   **只是文字在中间被硬切**。判据取"可见高度是行高的整数倍"。
            geo = pg.evaluate("""() => [...document.querySelectorAll('.icard .iclamp')]
              .filter(e => e.scrollHeight - e.clientHeight > 2)
              .map(e => { const c = getComputedStyle(e);
                return {id: e.closest('.icard').dataset.i, disp: c.display,
                        h: e.clientHeight, lh: parseFloat(c.lineHeight),
                        pad: parseFloat(c.paddingTop) + parseFloat(c.paddingBottom)}; })""")
            assert geo, '没量到被截断的元素'
            # ★ 判据**不能用 `display` 是不是 `-webkit-box`**：Chrome 对这个
            #   legacy 值的 computed style 报的是 `flow-root`（正常态实测就是
            #   这样），拿它当判据在正常代码上就先挂了。
            for r in geo:
                lines = (r['h'] - r['pad']) / r['lh']
                assert abs(lines - round(lines)) < 0.12, \
                    ('%s 截断后可见 %.2f 行 —— 不是整行，文字被切了一半'
                     % (r['id'], lines))
            card = pg.locator('.icard:has(.imore:not([hidden]))').first
            h0 = card.bounding_box()['height']
            card.locator('.imorea').click()
            pg.wait_for_timeout(350)
            h1 = card.bounding_box()['height']
            assert h1 > h0 + 2, \
                '点「展开」卡片没变高（%.0f -> %.0f）—— 点了没反应' % (h0, h1)
            assert '收起' in card.locator('.imorea').inner_text(), \
                '展开之后按钮还写着"展开" —— 人不知道能收回去'
            assert not pg.evaluate("""(c) => {
                const e = document.querySelector('.icard[data-i='+c+'] .ifml');
                return e.scrollHeight - e.clientHeight > 2; }""",
                card.get_attribute('data-i')), '展开之后公式还被截着'
            card.locator('.imorea').click()
            pg.wait_for_timeout(350)
            assert abs(card.bounding_box()['height'] - h0) < 3, \
                '再点一次没收回去'
            notes.append('长文本收起可展开（%s 被截断，展开 %.0f→%.0f px）'
                         % ('/'.join(hit), h0, h1))

            # ---- ⑦🔴 三种复权都能切，**默认前复权** ----
            # 用户 2026-09-16："指标广场里也加上前复权的选项。短期指标用前
            #   复权差异不大，主流的软件默认都是展示前复权，只是在做回测的
            #   时候使用后复权。"
            assert pg.evaluate('()=>FQ') == 'qfq', \
                ('广场默认不是前复权（%r）—— 主流行情软件的默认就是它，'
                 '而不复权跨除权日有假跌幅，MACD/KDJ 会在那天凭空拐一下'
                 % pg.evaluate('()=>FQ'))
            fqs = pg.evaluate(
                "()=>[...document.querySelectorAll('.ifq')].map(a=>a.dataset.f)")
            assert fqs == ['bfq', 'qfq', 'hfq'], '三种口径没给全：%r' % fqs
            assert pg.evaluate(
                "()=>[...document.querySelectorAll('.ifq.on')]"
                ".map(a=>a.dataset.f)") == ['qfq'], '默认那个没点亮'
            # 🔴 **取数真的按这个口径**（只看按钮高亮的话，改了显示没改请求
            #   也全绿 —— 而那时图和线是两套口径算的，且不报错）。
            _rq = []
            pg.on('request', lambda r: _rq.append(r.url)
                  if '/api/stock/' in r.url else None)
            pg.evaluate("()=>document.querySelector('.ifq[data-f=hfq]').click()")
            pg.wait_for_timeout(3000)
            _got = sorted(set(u.split('fq=')[1].split('&')[0]
                              for u in _rq if 'fq=' in u))
            assert _got == ['hfq'], \
                '切了后复权，请求里带的还是 %r' % _got
            assert 'fq=hfq' in pg.url, '复权口径没进 URL（分享/刷新就丢）'
            assert [u for u in _rq if 'kline' in u], \
                ('切复权没重取 K 线 —— 缓存键里少了它，图还是上一套口径的，'
                 '而指标是按新口径算的：两者对不上且不报错')
            # ★ 那段"什么时候用哪种"要**跟着切换变** —— 摆三个按钮而不说
            #   区别的话，这一页就没回答它该回答的问题。
            _why = pg.evaluate("()=>document.querySelectorAll('#pg>.lvwhy')[0]"
                               "?.innerText || ''")
            assert '后复权' in _why and ('回测' in _why or '特征' in _why), \
                '切到后复权之后那段说明没跟着变：%r' % _why[:60]
            pg.evaluate("()=>document.querySelector('.ifq[data-f=qfq]').click()")
            pg.wait_for_timeout(2500)
            notes.append('三种复权可切 · 默认前复权 · 取数与说明都跟着走')
            assert not errs, 'JS 报错：%r' % errs[:3]
            br.close()
    finally:
        httpd.shutdown()
    return '；'.join(notes)




@case('主要指数常驻带子：每个页面都有 / 清单来自服务端 / 收盘不轮（playwright）',
      tag='web')
def t_index_bar():
    """用户 2026-09-19：「把主要的指数都实时获取，比如上证指数、创业板、
    科创50、中证500、微盘股等等（始终显示在最上方或最下方，不用特别大和
    显眼，普通字体大小即可）」。

    🔴 **挂在 `common.js`** —— 6 个独立 .html 与 index.html 都加载它，
      所以任何页面都看得到（同炸板浮窗那条：挂在某一页的话，
      人正在看别的页面时就没有了）。这条判据要**逐个页面**验，
      而且要用 `_pages()` 扫出来的清单、不写死路径
      （写死的话新页面自动不在保护里 —— 那条纪律的另一半）。

    🔴 **「微盘股」给不了**：万得 8841431.WI 是**万得专有**，
      腾讯与 tdx 都不提供（2026-09-19 逐个写法试过：`sh932000`
      中证2000 也取不到）。这里给的是**国证2000** —— 同为"小市值 2000 只"
      口径但**大一档**，所以名字里**不写"微盘"**，只写它本来的名字
      （同「不拿 ETF 当指数用」：近似物要叫自己的名字）。

    ★ 判据落在**可量的事实**上：项数 == 服务端清单、字号是普通大小
      （不是放大的）、真的贴在视口底边、body 留出了等高的白。
    """
    import shutil
    import threading
    from http.server import ThreadingHTTPServer

    from playwright.sync_api import sync_playwright

    from assay import realtime as _rt
    from assay import server as sv

    # ---- 服务端清单本身 ----
    assert len(_rt.INDICES) >= 6, '指数清单太短：%d' % len(_rt.INDICES)
    syms = [c for c, _ in _rt.INDICES]
    assert len(set(syms)) == len(syms), '清单里有重复代码'
    for c, short in _rt.INDICES:
        assert re.match(r'^(sh|sz|bj)\d{6}$', c), '代码形状不对：%r' % c
        assert short and len(short) <= 8, '短名太长，带子会被撑开：%r' % short
    # 🔴 **不许出现"微盘"** —— 本地与腾讯都没有那个指数，叫这个名字
    #   等于把一个近似物冒充成它（同「不拿 ETF 当指数用」那条）。
    assert not any('微盘' in s for _, s in _rt.INDICES), \
        ('清单里有叫"微盘"的 —— 万得微盘股是专有指数，腾讯/tdx 都没有，'
         '给的其实是国证2000 之类的近似物，不能顶着那个名字')

    d = _rt.indices()
    assert d['items'], '一条指数都没取到（网络？）'

    # ---- 🔴🔴 自建「微盘400」----
    #   万得微盘股拿不到，而国证2000 **每 5 天就有 1 天把方向说反**
    #   （2024-01 起 658 个交易日：日差 sd 1.39pp、|差|>1pp 占 38.6%、
    #   **方向相反占 19.6%**）。所以这一格自己算。
    mic = next((r for r in d['items']
                if str(r['symbol']).startswith('micro')), None)
    assert mic, '带子里没有自建的微盘格'
    assert '微盘' in mic['short'] and str(_rt.MICRO_N) in mic['short'], \
        ('名字要自己说清是什么（"微盘400"）—— 它是近似物，'
         '不能顶着万得那个指数的名字：%r' % mic['short'])
    # 🔴 **没有点位就不给点位**：等权组合没有"点位"这回事，
    #   编一个出来就是造了个看着像指数的数。
    assert mic['price'] is None, \
        '等权组合不该有点位，却给了 %r' % mic['price']
    assert mic.get('n', 0) >= _rt.MICRO_N * 0.6, \
        '只有 %s 只有成交，样本太少不该给这一格' % mic.get('n')

    # 🔴 **最硬的自证：实时链算出来的 == 面板算出来的。**
    #   两条路完全独立（腾讯快照 vs 本地面板），对得上才说明
    #   成分与等权那两步都没算错。
    # 🔴 前提是**两条路落在同一天上**（2026-09-21 修）。原来的门是
    #   `not d['session']`，注释写着"收盘后跑时两者是同一天" —— 而
    #   `session=False` 同时命中三种情况，其中两种**跨天**：
    #       ① 午休 11:31~13:00        实时=今天上午、面板=上一个交易日
    #       ② 收盘后 ~ 当晚同步完成     同样跨天
    #       ③ 同步之后 / 非交易日       ← 只有这种才是它想要的
    #   实测 09-21 周一午休时假失败：实时 +2.15% vs 面板 +0.38%（上周五）。
    #   **判据该钉"同一天"，不是"收没收盘"。**
    _micro_xcheck = None
    import os as _os
    _root = _os.environ.get('ASSAY_DATALAKE') or \
        _os.path.join(_os.path.dirname(REPO), 'datalake')
    import duckdb as _dd
    _P = "read_parquet('%s/mart/panel_daily/panel_*.parquet')" % _root
    _pday = str(_dd.connect().execute(
        'SELECT max(date) FROM %s' % _P).fetchone()[0] or '').replace('-', '')
    _rday = str(d.get('asof') or '')[:8]
    if not d['session'] and _pday and _rday and _pday == _rday:
        _panel = _dd.connect().execute(
            "WITH r AS (SELECT change_pct, row_number() OVER "
            "(ORDER BY totalmv) rk FROM %s WHERE date = "
            "(SELECT max(date) FROM %s) AND totalmv > 0 AND change_pct IS NOT NULL "
            "AND public_status IN ('正常上市','ST','*ST')) "
            "SELECT avg(change_pct) FROM r WHERE rk <= %d"
            % (_P, _P, _rt.MICRO_N)).fetchone()[0]
        assert abs(mic['change_pct'] - _panel) < 0.05, \
            ('实时链算的 %+.2f%% 与面板算的 %+.2f%% 对不上 —— '
             '成分或等权那一步错了' % (mic['change_pct'], _panel))
        _micro_xcheck = '实时链 %+.2f%% == 面板 %+.2f%%（同为 %s）' % (
            mic['change_pct'], _panel, _rday)
    else:
        # ★ 跳过要**说出来**，不然"这条自证有没有跑"看不出来（静默跳过
        #   等于判据悄悄消失）。
        _micro_xcheck = '跨天/盘中，跳过实时↔面板互证（实时 %s / 面板 %s%s）' % (
            _rday or '?', _pday or '?', '，盘中' if d['session'] else '')

    # ---- 🔴 停牌/取不到的成分**不许算成 0%** ----
    #   算成 0 会把一批没交易的票当成"今天平盘"，等权平均被系统性拉向 0。
    #   ★ 这条**必须构造**：真实 400 只当前全都取得到，那条路平时
    #     一步都走不到（变异「停牌算 0」第一轮就是这么漏的）。
    _bak_m = list(_rt._MICRO['syms'])
    try:
        _rt._MICRO['syms'] = _bak_m + ['sh999999'] * 20   # 20 个取不到的
        _rt._IDX_CACHE.update(at=0, rows=[])
        d3 = _rt.indices(force=True)
        m3 = next(r for r in d3['items'] if str(r['symbol']).startswith('micro'))
        assert m3['n'] == mic['n'], \
            ('塞了 20 个取不到的成分之后样本数从 %s 变成 %s —— '
             '它们被算进去了' % (mic['n'], m3['n']))
        assert abs(m3['change_pct'] - mic['change_pct']) < 0.01, \
            ('塞了 20 个取不到的成分之后涨跌从 %+.2f%% 变成 %+.2f%% —— '
             '它们被当成 0%% 算进了等权平均'
             % (mic['change_pct'], m3['change_pct']))
    finally:
        _rt._MICRO['syms'] = _bak_m
        _rt._IDX_CACHE.update(at=0, rows=[])

    # ★ 小市值那一档必须排在**前面**：窄屏上带子会横滚，
    #   排后面的默认看不见（实测 1280px 只露得出前 8 格）。
    _order = [r['short'] for r in d['items']]
    for _k in (mic['short'], '国证2000'):
        assert _k in _order[:6], \
            '%s 排在第 %d 位 —— 窄屏上要滚出来才看得见' % (_k, _order.index(_k) + 1)
    assert len(d['items']) >= len(_rt.INDICES) - 1, \
        '清单 %d 个，只取到 %d 个' % (len(_rt.INDICES), len(d['items']))
    assert isinstance(d['session'], bool), 'session 必须由服务端给（前端不判时段）'
    for r in d['items']:
        # ★ 分两种：**指数**必须有点位且 >0；**自建的等权组合**没有点位
        #   （`price is None`）—— 那不是缺失，是它本来就没有这回事。
        #   两者都不许出现 0（0 会被读成"今天是 0 点"）。
        if str(r['symbol']).startswith('micro'):
            assert r['price'] is None, \
                '等权组合不该有点位，却给了 %r' % r['price']
        else:
            assert r['price'] and r['price'] > 0, '%s 价格是 0' % r['short']
        assert r['change_pct'] is not None, '%s 没有涨跌幅' % r['short']

    # ---- 🔴 取不到的**不许塞 0**，要整格不显示 ----
    #   （同「空结果一律当失败」「拿不到分红那一格标查不到，不猜一个数」）
    #   ★ 这条**必须构造**：真实的 9 个指数全都取得到，那条分支平时
    #     一步都走不到 —— 变异「取不到就塞 0」第一轮就是这么漏的。
    _bak = list(_rt.INDICES)
    try:
        _rt.INDICES.append(('sh999999', '不存在'))
        _rt._IDX_CACHE.update(at=0, rows=[])     # 绕开 20 秒缓存
        d2 = _rt.indices(force=True)
        _got = {r['symbol'] for r in d2['items']}
        assert 'sh999999' not in _got, \
            ('取不到的指数被塞了一格（price=%s）—— 屏幕上会出现一个 0.00，'
             '而那看着像"这个指数今天是 0"'
             % next((r['price'] for r in d2['items']
                     if r['symbol'] == 'sh999999'), '?'))
        assert len(d2['items']) >= len(_bak) - 1, \
            '加了个取不到的代码之后，别的指数也没了'
    finally:
        _rt.INDICES[:] = _bak
        _rt._IDX_CACHE.update(at=0, rows=[])

    prev = sv.ALLOW_LIVE
    sv.ALLOW_LIVE = True
    sv._scan()
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), sv.Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as p:
            try:
                br = p.chromium.launch()
            except Exception as e:                          # noqa: BLE001
                return '跳过（浏览器不可用: %s）' % type(e).__name__
            pg = br.new_page(viewport={'width': 1500, 'height': 950})
            errs = []
            pg.on('pageerror', lambda e: errs.append(e.stack or str(e)))
            base = 'http://127.0.0.1:%d/' % port

            # ★ 逐个页面验 —— 清单**扫出来**（同 `_pages()` 那条纪律）
            pages = ['#/'] + [x for x in _pages()]
            seen = 0
            for u in pages:
                pg.goto(base + u.lstrip('/'), wait_until='networkidle')
                # 🔴 **不要直接等选择器** —— 带子没挂上时那是一句 25 秒
                #   超时，**报错指不到原因**（同「报错必须指向真正的原因」）。
                #   等页面渲染完，再自己判有没有这条带子。
                pg.wait_for_selector('#top', timeout=20000)
                try:
                    pg.wait_for_selector('#idxbar', timeout=8000)
                except Exception:                           # noqa: BLE001
                    raise AssertionError(
                        '%s 上没有指数带子 —— 它挂在 common.js 上，'
                        '本该**每个页面**都有（同炸板浮窗那条）' % u)
                pg.wait_for_timeout(250)
                n = pg.eval_on_selector_all('#idxbar .ix', 'es => es.length')
                assert n == len(d['items']), \
                    '%s 上的带子有 %d 项，服务端给了 %d 项' % (u, n, len(d['items']))
                box = pg.eval_on_selector('#idxbar', """e => {
                    const r = e.getBoundingClientRect(), c = getComputedStyle(e);
                    return {bottom: r.bottom, h: r.height,
                            fs: parseFloat(c.fontSize), pos: c.position}; }""")
                assert box['pos'] == 'fixed', \
                    '%s 上的带子不是固定的 —— 滚下去就看不见了' % u
                assert abs(box['bottom'] - 950) < 2, \
                    '%s 上的带子没贴住视口底边（bottom=%s）' % (u, box['bottom'])
                # 「不用特别大和显眼」——普通字号
                assert 11 <= box['fs'] <= 13, \
                    '带子字号 %spx，用户要的是普通大小' % box['fs']
                # 🔴 fixed 会盖住页面底部内容 -> body 必须留出等高的白
                pb = pg.eval_on_selector(
                    'body', 'e => parseFloat(getComputedStyle(e).paddingBottom)')
                assert pb >= box['h'] - 2, \
                    ('%s 的 body 只留了 %spx，而带子高 %spx —— 会盖住页面'
                     '最下面那行' % (u, pb, box['h']))
                seen += 1
            # 🔴 没有点位的那一格，页面上**不许冒出一个 0.00**
            #   （`Number(null).toFixed(2)` 就是 '0.00'，而那看着像
            #   "这个指数今天是 0 点"）。
            _mtxt = pg.eval_on_selector_all(
                '#idxbar .ix',
                "es => es.map(e => e.innerText.replace(/\\s+/g,' ').trim())")
            _mcell = [x for x in _mtxt if x.startswith(mic['short'])]
            assert _mcell, '页面上没有微盘那一格：%s' % _mtxt[:3]
            assert '0.00' not in _mcell[0].split('%')[0], \
                '微盘那格显示了假点位：%r' % _mcell[0]
            assert '%' in _mcell[0], '微盘那格没有涨跌幅：%r' % _mcell[0]

            # ---- ⚙ 设置：显示哪些 / 什么顺序 ----
            bar = lambda: pg.eval_on_selector_all(
                '#idxbar .ix b', 'es => es.map(e => e.innerText.trim())')
            base_order = bar()
            assert pg.query_selector('#idxcfg'), '带子上没有设置入口'
            pg.click('#idxcfg')
            pg.wait_for_selector('#idxcfgw table', timeout=10000)
            n_rows = pg.eval_on_selector_all('#idxcfgw tr', 'es => es.length')
            assert n_rows == len(d['items']), \
                '设置面板列了 %d 行，而服务端给了 %d 格' % (n_rows, len(d['items']))
            # ① 勾掉一个 -> 带子里少一个
            _off = d['items'][-1]['symbol']
            pg.uncheck('#idxcfgw input[data-c="%s"]' % _off)
            pg.wait_for_timeout(300)
            assert len(bar()) == len(base_order) - 1, '勾掉一个之后带子没少'
            # ② 上移 -> 顺序真的变
            _sym, _mv = d['items'][-2]['symbol'], d['items'][-2]['short']
            for _ in range(4):
                e = pg.query_selector('#idxcfgw a[data-up="%s"]' % _sym)
                if not e or not e.is_visible():
                    break
                e.click()
                pg.wait_for_timeout(150)
            assert bar().index(_mv) < base_order.index(_mv), \
                '点了上移，带子里的位置没往前：%s' % bar()
            # ③🔴 **刷新之后还在**（不然设置等于没设）
            moved = bar()
            pg.reload(wait_until='networkidle')
            pg.wait_for_selector('#idxbar .ix', timeout=20000)
            pg.wait_for_timeout(900)
            assert bar() == moved, '刷新之后设置丢了：%s -> %s' % (moved, bar())
            # ④🔴 **全关掉时那个齿轮必须还在** —— 否则设置入口自己消失、
            #    再也打不开（同 backLink 那条：死路比不给更糟）。
            _all = json.dumps([r['symbol'] for r in d['items']])
            pg.evaluate("() => localStorage.setItem('idxpref', JSON.stringify("
                        "{hide: %s, order: []}))" % _all)
            pg.reload(wait_until='networkidle')
            pg.wait_for_selector('#idxbar', timeout=20000)
            pg.wait_for_timeout(900)
            assert not bar(), '全关掉了却还显示着：%s' % bar()
            assert pg.query_selector('#idxcfg'), \
                ('全关掉之后设置入口也没了 —— 那就再也打不开这个面板')
            # ⑤🔴 存的是【隐藏列表】不是显示列表 —— 服务端将来加一个指数时
            #    它必须**自动出现**（同「加一个指标，广场上自动就有」）。
            pg.evaluate("() => localStorage.setItem('idxpref', JSON.stringify("
                        "{hide: ['sh000001'], order: []}))")
            pg.reload(wait_until='networkidle')
            pg.wait_for_selector('#idxbar .ix', timeout=20000)
            pg.wait_for_timeout(900)
            _now = bar()
            assert len(_now) == len(d['items']) - 1, \
                ('只藏了 1 个却显示 %d/%d —— 存的怕是"显示列表"，'
                 '那样新加的指数会静默不出现' % (len(_now), len(d['items'])))
            pg.evaluate("() => localStorage.removeItem('idxpref')")
            pg.reload(wait_until='networkidle')
            pg.wait_for_selector('#idxbar .ix', timeout=20000)
            pg.wait_for_timeout(900)

            txt = pg.inner_text('#idxbar')
            assert ('实时' in txt or '已收盘' in txt), \
                ('带子上没说这是实时还是收盘值 —— 两者差一天而屏幕上'
                 '长得一模一样（同「数据日与报价时间写同一个标签」）')
            assert not errs, '页面抛了异常：%s' % errs[:1]
            br.close()
    finally:
        httpd.shutdown()
        sv.ALLOW_LIVE = prev
    return ('%d 个页面上都有 %d 个指数（fixed 贴底、%s、body 留白）；'
            '清单与时段判据都来自服务端；%s'
            % (seen, len(d['items']), '已收盘' if not d['session'] else '实时',
               _micro_xcheck))
