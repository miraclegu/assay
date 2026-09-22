# -*- coding: utf-8 -*-
"""自检的**框架与共用件** —— 用例本身在 `tests/test_*.py`。

拆分（2026-09-17）：`selftest.py` 长到 16182 行，是整个仓库唯一失控的
文件 —— 它当初是拆 `srv/` / `lv/` / `views/` 的执行者，自己却没被拆。
切点与那几次同一判据：**按产品域**（用户在做什么），不按 tag 分层。

🔴 拆之前先验了**没有顺序依赖**：三个随机种子把 fast 层 62 条与 slow 层
  11 条打乱跑，全绿。按域拆必然改变执行顺序，这个前提不成立的话，
  拆完会得到一堆"单跑通过、全量失败"（本项目为 pyc 缓存栽过同款）。

🔴 **`REPO` 取代那 36 处 `os.path.dirname(os.path.abspath(__file__))`。**
  用例搬进 `tests/` 之后 `__file__` 深了一层 —— 那几处会解到 `tests/`，
  于是 `web/`、`runs/`、`strategies/` 全指到不存在的路径。
  这正是拆 `srv/` 时踩过的第一个坑（"`HERE = dirname(__file__)` 变成
  `.../assay/srv`，`/api/marks` 返回 {}、7 个实盘接口 500"）。
"""
import argparse
import os
import re
import sys
import traceback

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from assay.broker import Cost                     # noqa: E402
from assay.engine import Engine                   # noqa: E402
from assay.feed import PanelFeed                  # noqa: E402
from assay.guard import LookAheadError            # noqa: E402
from assay.metrics import summarize               # noqa: E402
from run import load                              # noqa: E402

JQ = dict(slippage=0.0, commission=0.0003, min_commission=5.0, close_tax=0.001)
CASES = []


def _web_files(web, ext):
    """递归收集 web/ 下的文件（相对 web 的路径）。

    🔴 **必须递归**。web/ 分了子目录（shared/ 与 views/）之后，
      `os.listdir` 只看一层 —— 漏掉的文件其**所有组合都不参与比对**，
      而那不报错，只是保护范围悄悄缩小（同"直接扫目录而不是照清单拼"
      那条：漏了不报错才是最贵的）。
    """
    out = []
    for r, _d, fs in os.walk(web):
        for f in fs:
            if f.endswith(ext):
                out.append(os.path.relpath(os.path.join(r, f), web))
    return sorted(out)


def _pages(web='web'):
    """所有独立 .html 页面的可访问 URL（带够用的查询参数）。

    🔴 **扫目录得出，不照清单拼** —— 照清单拼会漏掉新加的页面，
      而漏了**不报错**，只是保护范围悄悄缩小（同 `_web_files` 那条）。
      2026-09-15 加指标广场时就验证了这一点：两处页面清单都是手写的，
      新页面自动不在保护里。
    ★ 有几页没参数就看不到东西（个股要 code、对比要 codes），
      所以这里只维护**参数**，页面本身仍然来自目录扫描。
    """
    args = {'stock.html': '?code=601857.XSHG',
            'compare.html': '?codes=601857.XSHG,601088.XSHG',
            'sector.html': '?kind=concept'}
    out = []
    for f in sorted(os.listdir(web)):
        if not f.endswith('.html') or f == 'index.html':
            continue
        out.append('/' + f + args.get(f, ''))
    return out


def _all_case_src():
    """**全部用例的源码**拼成一段（按 selftest.py 的 import 顺序）。

    🔴 有三条用例是**扫自己的源码**的（验门面转发覆盖了所有 `sv.xxx`、
      验哪几条点了「跑一次」必须重定向 `ASSAY_RUNS`、验哪几条写 live/
      必须重定向 `LIVE`）。拆分之前它们读 `selftest.py` 就够了 ——
      拆完用例搬进 `tests/`，那个文件里一条 `@case` 都没有了，
      于是 `src.index("@case('...")` 当场 `ValueError: substring not found`。
      ★ 这正是拆 `srv/` 时那第三个坑（"留在别处的引用"）的变体：
        **import 成功、语法全对，只在某条用例真跑到时才炸**。
    ★ 顺序与 `selftest.py` 的 import 一致 —— 那几条用例按**位置**反查
      所属用例（`_owner(pos)`），顺序一乱就会归错。
    """
    out = []
    for m in ('test_engine', 'test_data', 'test_live', 'test_market', 'test_plat'):
        fp = os.path.join(os.path.dirname(os.path.abspath(__file__)), m + '.py')
        if os.path.exists(fp):
            out.append(io_open_text(fp))
    return '\n'.join(out)


def io_open_text(fp):
    import io as _io
    return _io.open(fp, encoding='utf-8').read()


def case(name, tag='fast'):
    """tag 决定用例进哪一层，依据是【实测耗时】不是感觉：

      fast (22 个, 约 18s)  日常改代码跑这个
      slow (7 个,  约 189s) 跑长区间回测的，占全量 80% 的时间
      web  (3 个,  约 30s)  需要浏览器

    新增用例默认 fast；如果它要跑多年回测或起浏览器，记得显式标 tag，
    否则 --fast 会慢慢退化回全量。
    """
    def deco(fn):
        CASES.append((name, fn, tag))
        return fn
    return deco


def _run(path, start, end, cash, params=None, **cost):
    feed = PanelFeed(start, end)
    eng = Engine(load(path), feed, cash=cash, cost=Cost(**cost), params=params)
    curve = eng.run()
    # ★ 必须传 engine：空仓指标挂在 engine 上，不传就拿不到（而缺失是静默的）
    return summarize(curve, cash, broker=eng.broker, engine=eng), eng




def _kset(pg, open_=True):
    """个股页的「⚙ 设置」浮窗 —— 主图叠加 / 复权口径 / 坐标 / 指标参数
    都在里面（2026-09-16 起，用户："全部变成主图的设置选项"）。"""
    shown = pg.locator('#kmwrap .stbox').count() > 0
    if open_ and not shown:
        pg.click('#ktoolbar #kparam')
        pg.wait_for_selector('#kmwrap .stbox', timeout=8000)
        pg.wait_for_timeout(300)
    elif not open_ and shown:
        pg.locator('#kmclose').click()
        pg.wait_for_timeout(300)


def _kmain(pg, ind, on=True):
    """个股页：把某个**主图**指标叠上去 / 拿下来。

    🔴 这个入口**变过五次**（工具条独立开关 -> 指标面板里那组 -> 工具条两个
      开关 -> chip + 「+ 主图」挑 -> 现在的**浮窗里勾选**），而要证的事一次
      没变：默认关 / 开了画布真的变 / 状态进 URL。
      所以把"怎么点"收进这一个函数 —— 散在各条用例里的话，入口再变一次
      就要改 N 处（同 `_via_pop` / `_lp_tab` 那两个）。
    """
    _kset(pg, True)
    box = pg.locator('#kmwrap .kmain[data-i="%s"]' % ind)
    assert box.count() == 1, '设置里没有主图指标 %s' % ind
    if box.is_checked() != on:
        box.click()
        pg.wait_for_timeout(1400)
    # 🔴 **用完就收**：浮窗是个遮罩，开着会把工具条（区间 / 翻页）挡住 ——
    #   playwright 报的是"另一个元素拦截了点击"，看着像那个按钮坏了。
    _kset(pg, False)


def _kfq(pg, fq):
    """切价格口径（不复权 / 前复权 / 后复权）—— 它也搬进设置浮窗了。"""
    _kset(pg, True)
    if pg.evaluate('()=>FQ') != fq:
        pg.locator('#kmwrap .fq[data-f="%s"]' % fq).click()
        pg.wait_for_timeout(2000)
    _kset(pg, False)


def _klog(pg, on=True):
    """切线性 / 对数坐标 —— 同上。"""
    _kset(pg, True)
    if bool(pg.evaluate('()=>KLOG')) != on:
        pg.locator('#kmwrap #%s' % ('klogt' if on else 'klin')).click()
        pg.wait_for_timeout(1200)
    _kset(pg, False)


def _lp_enter(pg, timeout=90000):
    """实盘账户页 -> 业绩页。**怎么点收进这一处**。

    🔴 这个入口变过一次（2026-09-22）：原来唯一的入口是 KPI 板里那个
      「累计收益 ›」链接（`a.lpin`），而那一页有六个页签 ——
      用户：「点击累计收益，进去的其实不仅仅是累计收益，是一个综合的面板，
      从累计收益这边进去感觉不太合适」。现在是账户按钮排里的「📊 业绩」。
    ★ 散在各条用例里的话，入口再变一次就要改 N 处
      （同 `_kmain` / `_via_pop` / `_lv_open_newform`）。
    """
    pg.wait_for_selector('#lvperf', timeout=timeout)
    pg.click('#lvperf')
    pg.wait_for_function("() => location.hash.endsWith('/perf')", timeout=timeout)


def _lp_tab(pg, name, timeout=90000):
    """业绩页：切到某个页签并等它可见。

    🔴 业绩页 2026-09-15 改成了页签（用户要求），于是「收益明细」「每日持仓」
      这些块默认在**未激活的 pane** 里（`.pane` 的 display:none）——
      `wait_for_selector` 默认要求**可见**，所以直接等里面的元素会超时 90 秒，
      看着像页面坏了。★ 判据一点没变，只是入口多了一步。
    """
    pg.wait_for_selector('#lptabs div', timeout=timeout)
    pg.locator('#lptabs div', has_text=name).first.click()
    pg.wait_for_timeout(300)


def _via_pop(pg, loc, want=None):
    """点代码/名称 -> **不跳走**，弹速览浮层；「完整页 ↗」才是去个股页的出口。

    🔴 原来这几处断言「点了必须落到 /stock.html」。而点名称开浮层是
      **刻意改掉**的行为（实盘页往往一直开着：待办、正在录一半的成交，
      跳走再回来这些状态就没了）。所以那个断言测的是已经不存在的交互，
      它一挂**失败的是断言不是产品**（同 `#d_hold .note` 那条）。
    ★ 但它原本要保的东西不能丢 ——「独立页面之间不许断链」是独立页面
      最大的风险。新判据两段：浮层真的开了（点了没反应是最难查的那种坏），
      且浮层里那个出口指向**这只票**的个股页。
    """
    loc.click()
    pg.wait_for_selector('#spwrap', state='visible', timeout=20000)
    href = pg.locator('#spfull').get_attribute('href') or ''
    assert '/stock.html' in href, '浮层里没有去个股页的出口：%r' % href
    if want:
        assert want in href, '浮层出口指向错了：%r 里没有 %s' % (href, want)
    pg.keyboard.press('Escape')
    pg.wait_for_timeout(250)
    assert not pg.locator('#spwrap').is_visible(), 'Esc 关不掉浮层'
    return href


_GUARD_FILES = ('picks.json',)


def _guard_snapshot():
    """跑之前记下【生产文件】的内容。

    🔴🔴 **selftest 不许写生产数据**这条纪律，此前只有 `live/` 有人工核对，
      `picks.json` 一直没人盯 —— 2026-09-21 就出事了：两条批量删除的用例
      是"备份真文件 -> 覆盖 -> finally 还原"，做变异测试时反复中断它们，
      某一次没还原成功，**真 picks.json 少了 66 行星标**，是 `git status`
      才发现的。标星是决策记录，同时是 `prune_runs` 的保护依据 ——
      丢了不报错，只是下次清理会把那些归档一并删掉。
    ★ 判据故意选**跑完文件变没变**，不是扫源码：扫源码分不出"读"和"写"
      （实测误报三条只读的用例），而这个是**可证的事实**、骗不过去。
    ⚠ 不盯 `live/`：真实的 serve.py 在后台 tick，它写账本是**它该做的事**，
      盯了就是天天假报（同「假告警看多了就不看告警」）。
    🔴🔴 **而这条守卫【绝不能把文件改回去】。** 2026-09-22 实测：全量跑要
      18 分钟，用户在这期间**在页面上点了一个星** —— 守卫拿开跑前的快照
      把它覆盖掉了，**真实的决策记录就这么丢了**。它防的是"测试污染"，
      而它造成的是"用户改动被回滚"，后者严重得多（同「对账不一致要报出来、
      不改写账本」那条：报出来，别替人决定）。
      ★ 上面那句"不盯 live/，serve.py 写它是它该做的事"**对 picks.json
        一字不差地成立** —— 作者认出了这条原则，只是没套到这个文件上。
    ★ 「测试污染」与「用户在页面上点的」靠**归属**分，不靠内容：
      逐条用例跑完就查一次 -> 变了就能**指到那条用例**（= 污染，判失败）；
      只在末尾那一次扫到 -> 没有用例能归属（= serve.py / 人点的，照实说，
      不算失败、也不动文件）。**这个区分在"只在末尾查一次"时是做不到的。**
    """
    out = {}
    for rel in _GUARD_FILES:
        fp = os.path.join(REPO, rel)
        try:
            out[rel] = io_open_text(fp)
        except Exception:                                   # noqa: BLE001
            out[rel] = None
    return out


def _guard_changed(snap):
    """比一遍，返回变了的文件名列表；**一个字节都不写回去**。

    🔴 **逐条用例跑完就查一次**（而不是只在最后查一次）。两个理由：
      ① 这条守卫自己的措辞是「哪条用例碰了生产文件？」—— 只在末尾查的话
         它**答不了自己这个问题**。2026-09-22 实测：三条嫌疑用例单跑都
         干净、只有全量跑才脏，于是只能靠 18 分钟一轮的全量去二分；
      ② **归属**是区分"测试污染"与"用户在页面上点的"唯一办法（见
         `_guard_snapshot` 的最后一条）。
    ★ 比完就把 `snap` 更新成现状 —— 不然用户点一次星，**后面每条用例都会
      被冤枉一遍**。
    ★ `_GUARD_FILES` 只有一个小文件，137 次读的开销可以忽略。
    """
    bad = []
    for rel, before in snap.items():
        fp = os.path.join(REPO, rel)
        try:
            now = io_open_text(fp)
        except Exception:                                   # noqa: BLE001
            now = None
        if now != before:
            bad.append(rel)
            snap[rel] = now             # 只记录新状态，绝不写回文件
    return bad


def _guard_msg(bad, who):
    return ('🔴 测试污染：%s 之后 %s 变了 —— 正确做法是【重定向】'
            '（把模块里那个路径指到临时目录），不是"备份再还原"：'
            '后者中断一次就丢数据。**文件没有被动过**，自己核对一下'
            % (who, '、'.join(bad)))


def main():
    import time as _t
    ap = argparse.ArgumentParser(description='assay 自检')
    g = ap.add_mutually_exclusive_group()
    g.add_argument('--fast', action='store_true', help='只跑 fast 层（约 18s，无浏览器）')
    g.add_argument('--slow', action='store_true', help='只跑 slow 层（长区间回测）')
    g.add_argument('--web', action='store_true', help='只跑 web 层（需要浏览器）')
    g.add_argument('--all', action='store_true', help='全部（默认）')
    ap.add_argument('-k', default=None, help='按用例名关键字过滤（可与分层叠加）')
    ap.add_argument('--list', action='store_true', help='只列用例与分层，不执行')
    a = ap.parse_args()
    want = ({'fast'} if a.fast else {'slow'} if a.slow else
            {'web'} if a.web else {'fast', 'slow', 'web'})
    sel = [(n_, f_, t_) for n_, f_, t_ in CASES
           if t_ in want and (not a.k or a.k in n_)]
    if a.list:
        for t_ in ('fast', 'slow', 'web'):
            names = [n_ for n_, _, tt in CASES if tt == t_]
            print('%-5s %2d 个: %s' % (t_, len(names), '、'.join(names)))
        return
    if not sel:
        print('没有匹配的用例'); sys.exit(1)
    print('层 %s%s —— %d/%d 个用例\n'
          % ('+'.join(sorted(want)), (' 关键字 %r' % a.k) if a.k else '',
             len(sel), len(CASES)))
    ok = fail = 0
    times = []
    _snap = _guard_snapshot()          # 见 `_guard_snapshot`：不许写生产文件
    _dirty = []
    # ---- 进度（用户 2026-09-22：「--all 看不到进度」）--------------------
    # 🔴 关键不是"第几条"，是**正在跑哪一条**：全量 1083 秒里最慢的几条各要
    #   46~70 秒，跑的时候屏幕上一个字都没有 —— 分不出"在跑"还是"卡死了"。
    #   所以开跑【之前】就把名字打出来。
    # ★ 两种输出各走各的：
    #     终端   用回车原地刷新，结果行把它盖掉（不留两行噪声）
    #     重定向 单独打一行 —— tail -f 才看得见跑到哪了；
    #            **而这正是看不到进度的直接原因**：重定向时 stdout 是块缓冲，
    #            攒满 4KB 才落盘。所以每一行都 flush。
    _tty = sys.stdout.isatty()
    _t0all = _t.time()
    _w = len(str(len(sel)))

    def _wid(t):
        # 🔴 终端按【列宽】算，中日韩字符占 2 列而 len() 只数 1 ——
        #   拿 len() 去擦除会短一半、留下半行垃圾；拿 len() 去截断则会
        #   把 110 个汉字排成 220 列而折行，折行之后回车根本擦不掉。
        import unicodedata as _u
        return sum(2 if _u.east_asian_width(c) in 'WF' else 1 for c in t)

    def _cut(t, w=108):
        out, n = [], 0
        for c in t:
            k = _wid(c)
            if n + k > w:
                break
            out.append(c)
            n += k
        return ''.join(out), n

    def _dur(x):
        return '%d 秒' % x if x < 90 else '%.1f 分' % (x / 60.0)

    def _eta(i):
        # 按已跑完那几条的平均耗时外推；样本不足就不猜
        if i <= 1:
            return ''
        el = _t.time() - _t0all
        return '　已用 %s，约剩 %s' % (
            _dur(el), _dur(el / (i - 1) * (len(sel) - i + 1)))

    for _i, (name, fn, _tag) in enumerate(sel, 1):
        head = '[%*d/%d]' % (_w, _i, len(sel))
        line, _lw = _cut('  · %s %s …%s' % (head, name, _eta(_i)))
        if _tty:
            # ★ **精确擦掉**自己那一行再打结果，不靠"结果行更长"去盖 ——
            #   结果行短一截时会留下半行垃圾，而那看着像输出错乱。
            print(line, end='\r', flush=True)
        else:
            print(line, flush=True)
        t0 = _t.time()
        try:
            msg = fn()
            dt = _t.time() - t0
            times.append((dt, name))
            if _tty:
                print('\r' + ' ' * _lw, end='\r')
            print('  ✓ %s %-28s %6.1fs  %s' % (head, name, dt, msg or ''),
                  flush=True)
            ok += 1
            _b = _guard_changed(_snap)
            if _b:
                print('    ' + _guard_msg(_b, '「%s」' % name), flush=True)
                _dirty.append(name)
        except Exception as e:                              # noqa: BLE001
            dt = _t.time() - t0
            times.append((dt, name))
            if _tty:
                print('\r' + ' ' * _lw, end='\r')
            print('  ✗ %s %-28s %6.1fs  %s: %s'
                  % (head, name, dt, type(e).__name__, e), flush=True)
            if os.environ.get('SELFTEST_TRACE'):
                traceback.print_exc()
            fail += 1
            _b = _guard_changed(_snap)
            if _b:
                print('    ' + _guard_msg(_b, '「%s」' % name), flush=True)
                _dirty.append(name)
    # ★ 末尾这一次**归属不到任何用例** -> 那就是跑着的 serve.py 或人在页面上
    #   点的（跑一轮要十几分钟，这很正常）。照实说一句，**不算失败、也不动
    #   文件** —— 同 `_guard_snapshot` 里"不盯 live/"那条理由。
    _b = _guard_changed(_snap)
    if _b:
        print('\n· %s 在跑的这段时间里变过，但归属不到任何用例 —— '
              '多半是跑着的 serve.py 或你在页面上点的（比如标星）。'
              '**没有动它**。' % '、'.join(_b))
    if _dirty:
        print('🔴 碰了生产文件的用例：%s' % '、'.join(_dirty))
        fail += 1                      # 污染了生产文件就是失败，不只是提醒
    print('\n%d 通过 / %d 失败   总耗时 %.1fs（共 %d 个用例，本次跑 %d 个）'
          % (ok, fail, sum(d for d, _ in times), len(CASES), len(sel)))
    if os.environ.get('SELFTEST_TIMING'):
        print('\n耗时排序:')
        for d, n in sorted(times, reverse=True):
            print('  %6.1fs  %s' % (d, n))
    sys.exit(1 if fail else 0)


def _lv_rec_fill(pg, code, shares, price=None, fee=None, date=None,
                 side='buy', force=False, submit=True):
    """在「记一笔」弹窗里填一笔并（默认）提交。**入口只在这里一处。**

    这个入口 2026-09-22 变过两次，每次都打挂几条既有用例：
      ① 代码框从裸 `<input id=fc>` 变成**按方向分**（买=搜索下拉、
         卖=持仓下拉），`#fc` 成了 hidden -> `pg.fill('#fc', …)` 直接
         `Timeout`（元素填不了），**而报错指不到原因**；
      ② 日期从自由输入框变成「常规锁死 + 补录单独一个页签」->
         `pg.fill('#fd', …)` 同样填不了。
    ★ 所以「怎么填」收进一处：入口再变一次只改这里
      （同 `_kmain` / `_via_pop` / `_lp_tab` / `_lv_bind_strategy`）。

    `date` 给了且与常规那档不同 -> **自动切到「补录」页签**。
    """
    pg.wait_for_selector('#fdb', timeout=15000)
    d0 = pg.input_value('#fd')
    if date and date != d0:
        pg.click('.rtab[data-t="back"]')
        pg.wait_for_selector('#fdi', timeout=10000)
        pg.fill('#fdi', date)
        pg.dispatch_event('#fdi', 'change')
    else:
        pg.click('.rtab[data-t="fill"]')
    pg.wait_for_timeout(250)
    if side != 'buy':
        pg.select_option('#fs', side)
        pg.wait_for_timeout(600)
    if side == 'sell':
        # 卖：从持仓下拉里选（它只列**那天**能卖的）
        pg.wait_for_selector('#fsel', timeout=12000)
        pg.select_option('#fsel', code)
    else:
        # 买：搜索下拉。**用代码搜**（名称在不同快照里会变，代码不会）
        pg.fill('#fcb .skq', code.split('.')[0])
        pg.wait_for_timeout(900)
        hit = pg.query_selector_all('#fcb .skit')
        assert hit, '搜「%s」一条候选都没有 —— 这一笔填不进去' % code
        pg.click('#fcb .skit >> nth=0')
    pg.wait_for_timeout(200)
    got = pg.input_value('#fc')
    assert got == code, \
        '选中的是 %r 而不是 %r —— 下拉里排第一的不是要的那只' % (got, code)
    pg.fill('#fq', str(shares))
    pg.fill('#fp', '' if price is None else str(price))
    pg.fill('#ff', '' if fee is None else str(fee))
    if force and not pg.is_checked('#fforce'):
        pg.check('#fforce')
    if submit:
        pg.click('#fb')
        pg.wait_for_timeout(1200)


def _lv_bind_strategy(pg, path, params_json=None):
    """在策略浮层里绑一个策略（浮层要先打开）。

    🔴 「怎么绑」收进**一处** —— 这个入口 2026-09-18 从「裸输入框手填路径」
      变成「分组下拉 + 手填逃生口」，当场打挂一条既有用例
      （`pg.fill('#bp')` -> `Element is not an <input>`）。散在各条用例里的话，
      入口再变一次就要改 N 处（同 `_kmain` / `_via_pop` / `_lp_tab`
      / `_lv_open_newform`）。

    ★ 路径不在下拉里（清单外的文件）就走**手填**那条 —— 两条路都在这里，
      调用方不必知道当前是哪种形态。
    """
    pg.wait_for_selector('#bp', timeout=20000)
    opts = pg.eval_on_selector_all('#bp option', 'es => es.map(e => e.value)')
    if path in opts:
        pg.select_option('#bp', path)
    else:
        pg.select_option('#bp', '__manual__')
        pg.fill('#bpm', path)
    if params_json is not None:
        pg.fill('#bj', params_json)
    pg.click('#bb')


def _lv_open_newform(pg, zone='live', timeout=20000):
    """打开某个账户区的「+ 新建」表单，返回那个容器的选择器。

    ★ 这个入口**已经变过一次**（2026-09-17：全局一个 `#lvnew` + `☐ 模拟盘`
      勾选框 -> 实盘 / 模拟盘两区各一个「+ 新建」）。把"怎么点"收进一处，
      入口再变时只改这个函数（同 `_kmain` / `_via_pop` / `_lp_tab` 那三个）——
      散在各条用例里的话，下次要改 N 处，而漏改那处表现是超时 30 秒、
      看着像页面坏了。
    """
    sel = '#nf_' + zone
    # 🔴 **点击可能落在正在被替换的 DOM 上**：侧栏由 `showLive` 整块
    #   `innerHTML` 重渲染（建完账户、切账户都会），那一瞬点下去的按钮
    #   连同它的 handler 一起被换掉 —— **表单永远不出来，且不报错**
    #   （同「innerHTML 填充之后才存在的元素要重新绑事件」那一族）。
    #   2026-09-21 实测：`#nf_paper .nn` 等满 20 秒也没出现，不是慢，
    #   是那一下点空了。所以这里**认得出这种情况并重点一次**。
    # ⚠ 这是**测试侧**的韧性，不是把产品问题盖住 —— 真实用户在重渲染那
    #   一瞬点「+ 新建」同样会没反应。那个隐患另记，不在这条用例里修。
    for _ in range(3):
        pg.click('.znew[data-zone=%s]' % zone)
        try:
            pg.wait_for_selector(sel + ' .nn', state='visible',
                                 timeout=max(2000, timeout // 3))
            return sel
        except Exception:                                   # noqa: BLE001
            pg.wait_for_timeout(600)      # 等那一轮重渲染落定再点
    pg.wait_for_selector(sel + ' .nn', state='visible', timeout=timeout)
    return sel


def _lv_new_account(pg, name, cash, zone='live', timeout=20000):
    """在某个区建一个账户。`zone='paper'` 建出来的就是模拟盘。

    🔴 **开表单与填表单要一起重试**：侧栏由 `showLive` 整块 innerHTML
      重渲染，表单可能在"打开之后、填之前"就被换掉 —— 那时
      `pg.fill` 会一直等一个**已经脱离文档**的输入框，报的是
      `Timeout 30000ms exceeded`，**指不到真正的原因**。
      只给 `_lv_open_newform` 加重试是不够的（2026-09-21 实测：
      开的那步过了，挂在 fill 上）。
    ⚠ 同 `_lv_open_newform`：这是**测试侧**的韧性。真实用户在重渲染
      那一瞬操作同样会丢，那个隐患另记，不在这里盖住。
    """
    last = None
    for _ in range(3):
        sel = _lv_open_newform(pg, zone, timeout)
        try:
            pg.fill(sel + ' .nn', name, timeout=max(3000, timeout // 4))
            pg.fill(sel + ' .nc', str(cash), timeout=max(3000, timeout // 4))
            pg.click(sel + ' .nb', timeout=max(3000, timeout // 4))
            return
        except Exception as e:                              # noqa: BLE001
            last = e
            pg.wait_for_timeout(600)      # 等那一轮重渲染落定，从开表单重来
    raise AssertionError(
        '建账户的表单填不进去（%s 区）—— 多半是填到一半被整块重渲染换掉了：%s'
        % (zone, last))


def _defining_file(name, sub='assay'):
    """`assay/` 下**哪个文件**定义了这个顶层名字 -> `(路径, 源码)`。

    🔴 守卫里别把实现文件写死。`stock.py` 拆成 `stk/` 之后，两条钉着
      `assay/stock.py` 的守卫当场变红 —— **失败的是断言不是产品**，
      而它们要保的东西一个都没坏（同「判据要跟着代码一起搬，
      不然它只是看着还在」那条）。现找就不会有这个问题。
    """
    import ast as _a
    import glob as _g
    hits = []
    for f in sorted(_g.glob(os.path.join(REPO, sub, '**', '*.py'),
                            recursive=True)):
        if '__pycache__' in f:
            continue
        src = io_open_text(f)
        try:
            tree = _a.parse(src)
        except SyntaxError:
            continue
        for n in tree.body:
            ok = (isinstance(n, (_a.FunctionDef, _a.ClassDef)) and n.name == name)
            if not ok and isinstance(n, _a.Assign):
                ok = any(isinstance(t, _a.Name) and t.id == name for t in n.targets)
            if ok:
                hits.append((os.path.relpath(f, REPO), src))
                break
    assert len(hits) == 1, (
        '`%s` 在 %d 个文件里有顶层定义：%r —— 要么是又抄了一份，'
        '要么这个名字太泛，判据得说清是哪一个'
        % (name, len(hits), [h[0] for h in hits]))
    return hits[0]
