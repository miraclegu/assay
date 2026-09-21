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
    for name, fn, _tag in sel:
        t0 = _t.time()
        try:
            msg = fn()
            dt = _t.time() - t0
            times.append((dt, name))
            print('  ✓ %-28s %6.1fs  %s' % (name, dt, msg or ''))
            ok += 1
        except Exception as e:                              # noqa: BLE001
            dt = _t.time() - t0
            times.append((dt, name))
            print('  ✗ %-28s %6.1fs  %s: %s' % (name, dt, type(e).__name__, e))
            if os.environ.get('SELFTEST_TRACE'):
                traceback.print_exc()
            fail += 1
    print('\n%d 通过 / %d 失败   总耗时 %.1fs（共 %d 个用例，本次跑 %d 个）'
          % (ok, fail, sum(d for d, _ in times), len(CASES), len(sel)))
    if os.environ.get('SELFTEST_TIMING'):
        print('\n耗时排序:')
        for d, n in sorted(times, reverse=True):
            print('  %6.1fs  %s' % (d, n))
    sys.exit(1 if fail else 0)


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


def _lv_open_newform(pg, zone='live', timeout=8000):
    """打开某个账户区的「+ 新建」表单，返回那个容器的选择器。

    ★ 这个入口**已经变过一次**（2026-09-17：全局一个 `#lvnew` + `☐ 模拟盘`
      勾选框 -> 实盘 / 模拟盘两区各一个「+ 新建」）。把"怎么点"收进一处，
      入口再变时只改这个函数（同 `_kmain` / `_via_pop` / `_lp_tab` 那三个）——
      散在各条用例里的话，下次要改 N 处，而漏改那处表现是超时 30 秒、
      看着像页面坏了。
    """
    sel = '#nf_' + zone
    pg.click('.znew[data-zone=%s]' % zone)
    pg.wait_for_selector(sel + ' .nn', state='visible', timeout=timeout)
    return sel


def _lv_new_account(pg, name, cash, zone='live', timeout=8000):
    """在某个区建一个账户。`zone='paper'` 建出来的就是模拟盘。"""
    sel = _lv_open_newform(pg, zone, timeout)
    pg.fill(sel + ' .nn', name)
    pg.fill(sel + ' .nc', str(cash))
    pg.click(sel + ' .nb')
