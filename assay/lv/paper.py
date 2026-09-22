"""lv/paper.py —— 模拟盘：让引擎替你下单，成交写进同一本账。

## 它是什么

**模拟盘 = 一个 `mode='paper'` 的实盘账户，它的成交由【引擎】产生。**

不是新做一套账，也不是"回测结果另存一份"——它就是实盘账户，只是那几笔
成交不用人敲，由引擎按绑定的策略跑出来写进 `fills.jsonl`。于是：

    持仓 / 成本价 / TWR / 业绩页 / 收益明细 / 成交流水 / 选股理由
        ——— 一行新代码都不用写，全部原样复用

## 🔴 为什么成交必须来自引擎，而不是在这里撮合

同「核心原则：不重写任何交易规则」。整手取整、T+1、涨跌停无对手盘、
成交量上限、最低佣金、印花税分段、红利税档位、停牌挂账、退市清算——
这些全在 `broker.py` 里。在模拟盘里照抄一遍就是**第二份实现**，
而两份撮合规则分叉的表现是"模拟盘和回测对同一个策略给出不同的成交"，
**且不报错**。

## 🔴 与业绩页那条「策略曲线」是【同一次构建】

`bench.build_engine(aid)` 同时供两边用。各建一份的话，费率、滑点、
gated 起点任何一处不同，就会出现"模拟盘的成交与策略曲线对不上"——
而那看着像哪一边算错了，其实是两份配置。

## 推进的语义：**重放到最新数据日**，不是"增量跑几天"

`advance()` 每次都从**开户日**重跑到最新数据日，然后与账本对账、
只追加新的那几笔。为什么不只跑新增的那几天：

🔴 策略的路径状态（`g.hold_history` 涨停黑名单 / `g.stop_banned` 止损
  冷静期 / `g.pos_state` 吊灯窗口）是**逐日累积**的。只跑新的那一段，
  这些全是空的——**不报错**，只是多买几只本不该买的票
  （同「warmup 必须逐日重放」那条，实盘侧已经踩过）。

★ 代价是每次推进重跑一次。模拟盘的区间是"开户日到今天"，通常几天到
  几个月，跑一次 1~3 秒——比维护一份增量状态可靠得多。

## 🔴 账本仍然是 append-only，重跑不许改写历史

数据会被修正（本项目修过 volume 74,952 行、复权因子、ETF 价格刻度），
修正之后重跑，**过去那几天的成交可能就变了**。这时：

    不一致 -> **报出来**（`mismatch`），不静默改写

同「复算结果不许写回信号文件」那条：账本是"当时按什么数据做了什么"的
证据，今天算出来的不同答案是**另一件事**。页面上说清楚，由人决定是不是
要重建（`rebuild=True` 显式重来一遍，且那是**删档重开**，不是偷偷修补）。

★ 对账只比**已经在账本里的那几天**（按日期截断），不比未来——
  否则"今天多了一笔"永远是"不一致"。
"""

import datetime
import json
import os

from . import base as _base
from . import bench as _bench
from . import openbar as _ob
from . import pos as _pos

#: 模拟盘写进账本的 `source`。★ 与手工录入（`manual`）分开，
#: 这样流水页一眼看得出哪几笔是人敲的、哪几笔是引擎跑的。
SOURCE = 'paper'

#: `MODE_*` / `is_paper` 的**正本在 `lv/base.py`** —— 它们是账户模型的
#: 一部分，而 `lv/bench.py` 也要用（决定推演起点），放这里会循环依赖。
#: 这里 re-export 只为保住对外契约（`live.is_paper` / `paper.is_paper`）。
MODE_LIVE, MODE_PAPER = _base.MODE_LIVE, _base.MODE_PAPER
is_paper = _base.is_paper


def _fmt_money(v):
    """把金额写成人读的样子 —— 40.0 -> "40"、400000.0 -> "400,000"。"""
    try:
        f = float(v)
    except Exception:                                       # noqa: BLE001
        return str(v)
    return ('%,.0f' % f).replace('%', '') if False else format(f, ',.0f')


def _state_path(aid):
    return os.path.join(_base.acct_dir(aid), '_paper.json')


def state(aid):
    """推进到哪天了 / 上次对账结果。读不到就返回空壳，不抛错。"""
    p = _state_path(aid)
    if not os.path.isfile(p):
        return {}
    try:
        with open(p, encoding='utf-8') as f:
            return json.load(f)
    except Exception:                                       # noqa: BLE001
        return {}


def _save_state(aid, st):
    _base._atomic_write(_state_path(aid),
                        json.dumps(st, ensure_ascii=False, default=str))


def _key_of(f):
    """成交的身份 = (日期, 代码, 方向, 股数)。

    🔴 **不含价格与费用**：它们是浮点数，序列化/反序列化会有末位差异，
      拿来当身份会让每次对账都"不一致"。价格对不对单独比（带容差）。
    ★ 不用 `uid` —— 那是账本自己发的随机 hex，引擎这边没有。
    """
    return (str(f.get('trade_date') or f.get('date'))[:10],
            str(f.get('code')), str(f.get('side')),
            int(round(float(f.get('shares') or 0))))


def _ledger_fills(aid):
    """账本里由模拟盘写的那些成交，按（日期, 行序）排好。

    ★ 只看 `source == 'paper'`：模拟盘账户上仍然可以手工补录
      （比如模拟一笔分红到账），那几笔不参与对账。
    """
    # 🔴 走 `active_fills`：**被冲正掉的那一对"没有发生过"**，不该参与对账。
    #   原来只跳过冲正记录本身、却把**被冲掉的原始记录**留着 —— 于是它的
    #   key 在重跑里永远找不到，`advance` 每次都报「对账不一致」而账本不动，
    #   **人只能去走「重建」删档重开**（实测：冲正一笔之后第二次推进就这样）。
    # ★ 顺带一提，这也让"再添加一遍"不会发生：那个 key 重跑里本来就没有
    #   （它正是因为对不上才被冲掉的）。
    return [r for r in _pos.active_fills(_base.fills(aid))
            if r.get('source') == SOURCE and not r.get('reverse_of')]


def advance(aid, datalake=None, rebuild=False, intraday=False):
    """把模拟盘推进到最新数据日。**幂等**：没有新的交易日就什么都不做。

    返回 `{ok, added, advanced_to, mismatch, ...}`。
    """
    a = next((x for x in _base.load_accounts() if x['id'] == aid), None)
    if a is None:
        raise _base.LiveError('没有这个账户：%s' % aid)
    if not is_paper(a):
        return {'ok': False, 'error': '这不是模拟盘账户（mode=%r）'
                                      % a.get('mode')}
    if a.get('archived'):
        return {'ok': False, 'error': '账户已归档，不再推进'}
    if not a.get('code_sha256'):
        return {'ok': False, 'error': '还没绑定策略 —— 模拟盘不知道要跑什么'}

    eng, meta = _bench.build_engine(aid, datalake=datalake,
                                    intraday=intraday)
    if eng is None:
        return {'ok': False, 'error': meta.get('error') or '引擎建不起来'}

    eng.run(verbose=False)
    # 🔴 引擎的成交在 broker 上（`fills`，2026-09-14 加的逐笔流水）。
    #   `trades` 是往返记录、只在卖出时写，拿它当流水会**丢掉所有买入**。
    got = [f for f in getattr(eng.broker, 'fills', [])
           if str(f['date'])[:10] >= meta['start']]
    got.sort(key=lambda f: (str(f['date'])[:10], f['code'], f['side']))

    have = _ledger_fills(aid)
    st = state(aid)

    # ---- 盘中写下的那几笔（provisional）单独拿出来 ----
    # 🔴 它们**不参与** confirmed 的 mismatch 判定：盘中那根 bar 是按
    #   今开 + 昨天的因子拼的**近似**，日终权威数据落地后本来就可能不同 ——
    #   那不是"数据被修正过"，那是设计好的行为。混进 mismatch 里的话
    #   **每天一次「对账不一致」**，而它给的原因指不到真正的原因
    #   （用户 2026-09-22 选的就是「盘中先写、日终按冲正重录」这条路）。
    prov_day = str(st.get('prov_day') or '')[:10]
    prov_uids = set(st.get('prov_uids') or ())
    prov = [r for r in have if r.get('uid') in prov_uids]
    # 🔴 **`have` 要留全** —— 它同时是下面那个**去重集合** `have_keys` 的来源。
    #   第一版把 provisional 从 `have` 里剔掉了，于是那几笔每次推进都被
    #   **重新追加一遍**（实测第二次又加了 3 笔；卖出那笔被"重放出现负持仓"
    #   挡住才没成 4 笔 —— 也就是说**账本会越推越脏，而它不报错**）。
    #   排除只该发生在 **mismatch 比较**那一处。
    have_conf = [r for r in have if r.get('uid') not in prov_uids]

    # ---- 对账：账本里已有的那几天，重跑结果必须一致 ----
    # ★ 按**账本最后一天**截断再比 —— 不截的话"今天新跑出一笔"会被当成
    #   不一致，而那正是我们要追加的东西。
    mismatch = None
    if have_conf:
        last = max(str(r['trade_date'])[:10] for r in have_conf)
        a_keys = [_key_of(r) for r in have_conf]
        b_keys = [_key_of(f) for f in got if str(f['date'])[:10] <= last]
        a_keys.sort()
        b_keys.sort()
        if a_keys != b_keys:
            only_a = [k for k in a_keys if k not in b_keys]
            only_b = [k for k in b_keys if k not in a_keys]
            mismatch = {
                'until': last,
                'in_ledger_only': only_a[:20],
                'in_rerun_only': only_b[:20],
                'n_ledger': len(a_keys), 'n_rerun': len(b_keys),
                'why': ('重跑结果与账本对不上 —— 多半是数据被修正过'
                        '（本项目修过 volume / 复权因子 / ETF 价格刻度）。'
                        '账本是 append-only 的证据，【不会被静默改写】；'
                        '要按新数据重来请显式「重建」（那是删档重开）。'),
            }
            if not rebuild:
                st['mismatch'] = mismatch
                st['checked_at'] = _base._now()
                _save_state(aid, st)
                return {'ok': False, 'mismatch': mismatch,
                        'advanced_to': st.get('advanced_to'),
                        'added': 0}

    # ---- 追加新的那几笔 ----
    have_keys = set(_key_of(r) for r in have)
    added, errs = 0, []
    for f in got:
        k = _key_of(f)
        if k in have_keys:
            continue
        try:
            _pos.add_fill(
                aid, trade_date=str(f['date'])[:10], code=f['code'],
                side=f['side'], shares=int(round(f['shares'])),
                price=round(float(f['price']), 4),
                fee=round(float(f['fee']), 2),
                source=SOURCE, datalake=datalake,
                note='引擎撮合 · %s' % f.get('reason', ''),
                # 🔴 模拟盘的成交价是**引擎按开盘价 ± 滑点**算出来的，
                #   而 `check_price_in_range` 判的是当日 [low, high]。
                #   开盘价一定在区间内，但滑点会把它推出去一点点
                #   （0.075% 的一半），所以一字板那天可能落在区间外 ——
                #   那不是录错，是滑点模型的产物。
                force_price=True)
            have_keys.add(k)
            added += 1
        except Exception as e:                              # noqa: BLE001
            errs.append('%s %s %s: %s' % (f['date'], f['code'], f['side'], e))

    # ---- provisional 对账：确认，或者【冲正 + 重录】 ----
    # ★ 判据是 `_key_of`（日期/代码/方向/股数）—— 与 confirmed 那条同一个。
    #   在 `got` 里找得到 = 权威数据确认了它；找不到 = 盘中那份算错了
    #   （除权、候选池被补抓改变、策略参数被改过…），按项目自己那套
    #   **「改一笔 = 冲正 + 重录」**换掉，**不重写账本**。
    # 🔴🔴 **只在这一轮的重跑真的覆盖了 `prov_day` 时才对账。**
    #   否则会误伤：日终数据还没到就跑一次 `intraday=False`，那几笔在 `got`
    #   里当然找不到（重跑只到面板最新日）—— 于是**它们会被全部冲正掉**，
    #   而它们本来是对的、只是还没被确认。实测第一版就是这样。
    #   两种该对账的情形：
    #     ① 日终：权威日线到了（`panel_end >= prov_day`）
    #     ② 盘中同一天再推：重跑本来就覆盖这一天（候选池被补抓改变过时，
    #        旧的那几笔必须换掉）
    prov_ok, prov_rev = 0, 0
    _covered = bool(prov_day) and (
        str(meta.get('panel_end') or meta['end'])[:10] >= prov_day
        or str(meta.get('intraday_day') or '')[:10] == prov_day)
    if prov and _covered:
        rerun = set(_key_of(f) for f in got
                    if str(f['date'])[:10] == prov_day)
        for r in prov:
            if _key_of(r) in rerun:
                prov_ok += 1
                continue
            try:
                _pos.add_fill(
                    aid, trade_date=str(r['trade_date'])[:10], code=r['code'],
                    # 🔴 冲正必须是**反向**那一笔（`buy` 冲成 `sell`）——
                    #   `active_fills` 成对剔掉的条件里有 `side != side`，
                    #   同向写的话它**配不上对**，于是那笔被当成又一次买入：
                    #   实测持仓从 2000 变成 6200 股（2100+2000+2100 三个批次），
                    #   **而它不报错**。
                    side=('sell' if r['side'] == 'buy' else 'buy'),
                    shares=int(r['shares']),
                    price=r.get('price'),
                    # 🔴 冲正的费用传 **−原费用** —— 语义是"这笔交易没发生"，
                    #   手续费也要退掉（同页面上那个「冲正」按钮）。
                    fee=-float(r.get('fee') or 0.0),
                    source=SOURCE, reverse_of=r['uid'], datalake=datalake,
                    note='盘中预成交被日终权威数据推翻 -> 冲正',
                    force_price=True)
                prov_rev += 1
            except Exception as e:                          # noqa: BLE001
                errs.append('冲正 %s %s: %s' % (r['trade_date'], r['code'], e))

    # ---- 这一轮写下的哪几笔仍然是"盘中近似" ----
    # ★ 判据是**这一天的权威日线到没到**（`panel_end`），不是"是不是盘中跑的"
    #   —— 同一天日终再推一次时，那几笔就该转正（同「判据永远是现在的状态，
    #   不是记录」）。
    # ⚠ 算成**局部变量**再进最后那一次 `st.update` —— `advance` 末尾会
    #   `st = state(aid)` **重新读一次磁盘**，写在前面那个 dict 上会被丢掉
    #   （第一版就是这么挂的：`prov_day` 恒为 None、标记等于没做，
    #   **而它不报错**，只是日终不会去对账那几笔）。
    iday = meta.get('intraday_day')
    if iday and str(meta.get('panel_end'))[:10] < iday:
        # 盘中：这一天写下的全部算"还没被权威数据确认"
        prov_day_new = iday
        # 🔴 只标**还活着**的那几笔：`active_fills` 会把「冲正记录」与
        #   「被它冲掉的原始记录」成对剔掉。不排除的话那一对每轮都会被
        #   当成"对不上"再冲一次 —— **账本无限长胖，而它不报错**
        #   （实测：冲正过一笔之后再推，每轮都多一条冲正）。
        # ★ `_ledger_fills` 已经只给**还活着**的那些（走 active_fills），
        #   所以冲正掉的那一对天然不在里面 —— 不然它们每轮都会被当成
        #   "对不上"再冲一次，账本无限长胖（实测踩过）。
        prov_uids_new = sorted(
            r['uid'] for r in _ledger_fills(aid)
            if str(r['trade_date'])[:10] == iday and r.get('uid'))
    elif _covered:
        # 权威数据已经覆盖到那一天 -> 上面刚对过账，**转正**
        prov_day_new, prov_uids_new = None, []
    else:
        # 🔴 **这一轮没覆盖到就保持原样。** 清掉的话那几笔会被当成
        #   confirmed 历史，下次日终一有差异就报 mismatch —— 而那正是
        #   「盘中先写、日终按冲正重录」要避免的那条路。
        #   实测第一版就是这么挂的：日终数据还没到时跑一次
        #   `intraday=False`，标记清零，再推就报对账不一致。
        prov_day_new = prov_day or None
        prov_uids_new = sorted(prov_uids)

    # ---- 分红到账：写成【现金流】，不是成交 ----
    # 🔴 不补的话模拟盘的现金会少掉所有分红：引擎把分红计进 `pf.cash`，
    #   而账本的现金是"初始 − 买入 + 卖出"推出来的。红利那类策略一年
    #   能差几个点，**而它不报错**，只是权益一路偏低。
    # ★ 幂等判据是 `(日期, 代码)` —— 同一天同一只票只会派一次息。
    divs = [d for d in getattr(eng.broker, 'dividends', [])
            if str(d['date'])[:10] >= meta['start']]
    have_div = set()
    for r in _pos.cashflows(aid):
        if r.get('kind') == 'dividend' and (r.get('note') or '').startswith(SOURCE):
            have_div.add((str(r.get('date'))[:10], (r.get('note') or '')[-9:]))
    n_div = 0
    for d in divs:
        k = (str(d['date'])[:10], str(d['code'])[-9:])
        if k in have_div or float(d['cash']) <= 0:
            continue
        try:
            _pos.add_cashflow(aid, str(d['date'])[:10], round(float(d['cash']), 2),
                              kind='dividend',
                              note='%s 分红到账 %s' % (SOURCE, d['code']))
            have_div.add(k)
            n_div += 1
        except Exception as e:                              # noqa: BLE001
            errs.append('分红 %s %s: %s' % (d['date'], d['code'], e))

    # ---- 对账：账本权益 vs 引擎权益，差多少必须【报出来】 ----
    # 🔴 两边天然会差一点，而差的**来源是可以说清楚的**，所以要给数字
    #   而不是假装相等：
    #   ① 舍入 —— 账本存的是券商口径（价格 4 位小数、费用 2 位），
    #      实测 froec 30 笔累计差 ~94 元（本金的 0.019%）。
    #   ② 🔴 **复权因子里含着分红、而分红表里没有那一条**。引擎是
    #      「后复权总收益」口径：这种分红会表现成**价格上涨**，
    #      hfq 股数不缩、值照样涨；而账本记的是真实股数 + 不复权价，
    #      那笔钱既没进现金、价格又掉了 —— 于是账本【少掉一笔分红】。
    #      实测红利账户 601318 一只就差 486 元（因子跳 1.77%，
    #      而它不在 `eng.broker.dividends` 里）。
    #   ★ 哪一边"对"取决于问什么：要对着券商对账单看，账本是对的；
    #     要和回测比，引擎是对的。**所以不调和，只报差额**
    #     （同「对不上要说出来，不猜一个看着合理的」那条）。
    recon = None
    try:
        bars = eng.broker.bars
        led_cash = _pos.cash(aid)
        led_mv, det = 0.0, []
        led_pos = _pos.positions(aid)
        items = (led_pos.items() if isinstance(led_pos, dict)
                 else [(r['code'], r) for r in led_pos])
        for c, v in items:
            sh = float(v['shares'] if isinstance(v, dict) else v)
            b = bars.get(c)
            if not b or not b.factor or not b.close_hfq:
                continue
            px_bfq = b.close_hfq / b.factor
            led_mv += sh * px_bfq
            hfq = sum(l.shares for l in eng.pf.positions[c].lots) \
                if c in eng.pf.positions else 0.0
            gap = (hfq * b.factor - sh) * px_bfq
            if abs(gap) > 1.0:
                det.append({'code': c, 'ledger_shares': sh,
                            'engine_shares': round(hfq * b.factor, 2),
                            'gap_value': round(gap, 2)})
        led_eq = led_cash + led_mv
        eng_eq = float(eng.pf.total_value)
        det.sort(key=lambda x: -abs(x['gap_value']))
        recon = {'ledger_equity': round(led_eq, 2),
                 'engine_equity': round(eng_eq, 2),
                 'diff': round(led_eq - eng_eq, 2),
                 'diff_pct': (round((led_eq - eng_eq) / eng_eq, 6)
                              if eng_eq else None),
                 'by_code': det[:10]}
    except Exception as e:                                  # noqa: BLE001
        recon = {'error': str(e)}      # 对账失败不该让推进整个失败

    # 🔴🔴 **0 笔成交必须说出为什么** —— 2026-09-18 用户报「选了起始时间、
    #   也推进了，但是没有任何数据出现」。查下来：`init_cash=40`（40 元），
    #   而一手股票要几千元 —— 引擎**已经记了 30 条「资金不足一手」拒单**
    #   （`broker.rejects`，那行注释就写着"拒单必须可见，不静默"），
    #   而 `advance` **没把它带出来**：返回 `ok=True`、推到了最新日、
    #   `n_fills=0`，页面一片空白，**没有任何地方说原因**。
    #   链条在这里断了 —— broker 记了，传不到页面等于没记。
    #   ★ 判据取**引擎自己给的拒单原因**，不自己猜：
    #     "本金太小"只是这一次的原因，候选池为空 / 起点之后没有调仓日 /
    #     数据不够都会表现成同一个"0 笔"，而它们要做的事完全不同。
    rejects = []
    why_empty = None
    try:
        import collections as _c
        _rj = list(getattr(eng.broker, 'rejects', []) or [])
        _cnt = _c.Counter(str(r[3]) for r in _rj if len(r) >= 4)
        rejects = [{'why': w, 'n': n} for w, n in _cnt.most_common(8)]
        if not got:
            if rejects:
                # ★ 不再重复"一笔成交都没有" —— 页面的标题行已经这么写了，
                #   同一句话说两遍（同「汇总数字只在 KPI 板出现一次」）。
                why_empty = ('引擎%s。最多的原因是「%s」（%d 次）。'
                             % ('下过单，但全被拒了' if _rj else '压根没下单',
                                rejects[0]['why'], rejects[0]['n']))
                if '资金不足' in rejects[0]['why']:
                    why_empty += ('本金 %s 元买不起一手 —— A 股一手 100 股，'
                                  '常见的票一手要几千到几万元。'
                                  '改大初始资金再「重建」即可。'
                                  % _fmt_money(meta.get('cash')))
            else:
                why_empty = ('引擎一次拒单都没有 —— 也就是策略压根没下单：'
                             '起点（%s）之后可能没有调仓日，或者候选池是空的。'
                             % str(meta.get('start'))[:10])
    except Exception as e:                                  # noqa: BLE001
        rejects = [{'why': '拒单统计失败：%s' % e, 'n': 0}]

    st = state(aid)
    st.update({
        'recon': recon,
        'rejects': rejects,
        'why_empty': why_empty,
        'n_dividends': n_div,
        'advanced_to': str(meta['end'])[:10],
        'panel_end': str(meta.get('panel_end') or meta['end'])[:10],
        'intraday_day': meta.get('intraday_day'),
        'intraday_why': meta.get('intraday_why'),
        # ★ 盘中被推迟的那几个任务（14:00 的止损/炸板）——「还没跑完」
        #   要说出来，否则盘中那份持仓看着就是最终结果。
        'intraday_deferred': dict(getattr(eng, '_ob_deferred', {}) or {}),
        'intraday_skipped': ([] if not meta.get('intraday_feed') else
                             list(getattr(meta['intraday_feed'],
                                          'skipped', []))[:20]),
        'prov_ok': prov_ok, 'prov_reverted': prov_rev,
        'prov_day': prov_day_new, 'prov_uids': prov_uids_new,
        'advanced_at': _base._now(),
        'data_fingerprint': meta.get('data_fingerprint'),
        # 🔴 **"推进到哪天"要连着"这是哪份数据"一起说。**
        #   ETF 策略声明 `DATALAKE='etf_lake'`，而那个 lake 是**手工**
        #   `build_etf_lake.py` 建的、不在 `sync_daily.sh` 里 —— 实测它停在
        #   2026-09-11 而主数据已到 09-17。只写「推进到 09-11」的话，
        #   人拿它跟别处的 09-17 一比就以为推进坏了，**而它不报错**
        #   （同「数据日与报价时间写在同一个标签里，两处各写一半看着像
        #   自相矛盾」那条）。
        'datalake': os.path.basename(str(meta.get('datalake') or '')),
        'datalake_declared': bool(meta.get('datalake_declared')),
        'sha': meta.get('sha'),
        'n_fills': len(got),
        'errors': errs[:20],
    })
    st.pop('mismatch', None)
    _save_state(aid, st)
    return {'ok': True, 'added': added, 'advanced_to': st['advanced_to'],
            'panel_end': st['panel_end'], 'intraday_day': st['intraday_day'],
            'intraday_why': st['intraday_why'],
            'intraday_skipped': st['intraday_skipped'],
            'prov_day': st.get('prov_day'),
            'n_provisional': len(st.get('prov_uids') or ()),
            'prov_ok': prov_ok, 'prov_reverted': prov_rev,
            'n_fills': len(got), 'n_dividends': n_div, 'recon': recon,
            'rejects': rejects, 'why_empty': why_empty,
            'errors': errs[:20], 'mismatch': None}


# 盘中推进的节流：每个账户最多这么久一次。**漏了不要紧** —— 日终权威
# 数据落地后必然重算一遍（同 realtime「不挂 launchd」那条的理由）。
INTRADAY_EVERY = 300
_intraday_at = {}


def advance_intraday_all(datalake=None, log=None, now=None):
    """盘中：把绑了策略的模拟盘按【今开】各推进一格。

    🔴 **不能挂在 `tick_daily.py` 上** —— 那个脚本的判据 2 是「A 腿数据到了
      最新交易日」，盘中必然不成立（面板还停在昨天），于是它直接退出码 3。
      那条判据对"出信号"是对的（宁可用昨晚那份，也不要拿半截数据覆盖），
      而盘中推进**本来就设计成在那之前跑** —— 两件事判据相反，
      硬塞进去就会把其中一件做错。
    ★ 所以挂在 serve.py 那条 60 秒的线上（同 `watchlist.sync_live`）：
      盘中推进**只在你看页面时才有意义**，漏一轮下一轮就补上。

    ★ 两道节流，各有各的理由：
      ① 每个账户 `INTRADAY_EVERY` 秒一次 —— 一次推进是**从起点重放整段**
         （1~3 秒），60 秒一轮无脑推就是白烧 CPU。
      ② **收盘后只推到"今天推过一次"为止**：今开固定、成交已定，
         结果不会再变；但至少要推过一次，否则收盘到日终同步之间那几小时
         页面上还是昨天（那正是用户报的那个症状）。
    """
    import time
    from assay import realtime as _rt

    out = []
    try:
        in_sess = bool(_rt.in_session(now))
    except Exception:                                       # noqa: BLE001
        in_sess = False
    for a in _base.load_accounts():
        if not is_paper(a) or a.get('archived') or not a.get('code_sha256'):
            continue
        aid = a['id']
        st = state(aid)
        day, why = _ob.can_advance_intraday(
            st.get('panel_end') or st.get('advanced_to') or '1970-01-01',
            now=now)
        if why:
            continue
        # 收盘后：今天已经推过一次就不再推
        if not in_sess and str(st.get('prov_day') or '')[:10] == day:
            continue
        t = time.time()
        if t - _intraday_at.get(aid, 0.0) < INTRADAY_EVERY:
            continue
        _intraday_at[aid] = t
        try:
            r = advance(aid, datalake=datalake, intraday=True)
            r['account'] = a.get('name') or aid
            out.append(r)
            if log:
                log('[paper] %s 盘中推进 -> %s（新增 %s 笔，%s 笔待确认）'
                    % (r['account'], r.get('advanced_to'), r.get('added'),
                       r.get('n_provisional')))
        except Exception as e:                              # noqa: BLE001
            out.append({'account': a.get('name') or aid, 'error': str(e)})
    return out


def advance_all(datalake=None, log=None):
    """把所有模拟盘账户推进一遍。数据同步完之后调它。

    ★ 一个账户失败不该拖垮其它账户 —— 逐个 try。
    """
    out = []
    for a in _base.load_accounts():
        if not is_paper(a) or a.get('archived'):
            continue
        try:
            r = advance(a['id'], datalake=datalake)
        except Exception as e:                              # noqa: BLE001
            r = {'ok': False, 'error': str(e)}
        r['id'] = a['id']
        out.append(r)
        if log:
            log('   %-10s %s' % (a['id'], _brief(r)))
    return out


def _brief(r):
    if r.get('mismatch'):
        return '🔴 对账不一致（到 %s）—— 账本没动' % r['mismatch']['until']
    if not r.get('ok'):
        return '失败：%s' % r.get('error')
    return ('推进到 %s，新增 %d 笔' % (r.get('advanced_to'), r.get('added', 0))
            if r.get('added') else '已经是最新（%s）' % r.get('advanced_to'))


def reset(aid):
    """删档重开：清掉模拟盘写的成交与推进状态。

    🔴 **这是唯一会动账本的地方**，而且只删 `source == 'paper'` 的行 ——
      手工补录的那几笔不碰。与费率 `supersede` 同一条边界：
      重写账本必须有明确的、说得出口的理由，这里的理由是
      "模拟盘本来就是可重来的推演，它不是真实发生过的事"。
    ★ 真实盘账户调它会直接拒 —— 那才是不可重写的。
    """
    a = next((x for x in _base.load_accounts() if x['id'] == aid), None)
    if a is None:
        raise _base.LiveError('没有这个账户：%s' % aid)
    if not is_paper(a):
        raise _base.LiveError('只有模拟盘可以重建；实盘账本不可重写')
    path = os.path.join(_base.acct_dir(aid), 'fills.jsonl')
    kept, dropped = [], 0
    if os.path.isfile(path):
        with open(path, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except Exception:                           # noqa: BLE001
                    kept.append(line)
                    continue
                if r.get('source') == SOURCE:
                    dropped += 1
                    continue
                kept.append(line)
    _base._atomic_write(path, ('\n'.join(kept) + '\n') if kept else '')
    p = _state_path(aid)
    if os.path.isfile(p):
        os.remove(p)
    return {'ok': True, 'dropped': dropped}


def next_due(aid):
    """还有没有没推进的交易日（页面上显示"该推进了"）。

    ★ 判据是**数据日**，不是"今天" —— 数据没到的日子推进不了，
      而"今天是交易日但数据还没同步"很常见（收盘前）。
    """
    st = state(aid)
    done = st.get('advanced_to')
    try:
        cal = _base.calendar_days()
    except Exception:                                       # noqa: BLE001
        return None
    if not done:
        return None
    later = [d for d in cal if str(d)[:10] > str(done)[:10]]
    return str(later[0])[:10] if later else None


def _dates_after(d):
    return (datetime.date.fromisoformat(str(d)[:10])
            + datetime.timedelta(days=1)).isoformat()
