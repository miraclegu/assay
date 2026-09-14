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
from . import pos as _pos

#: 模拟盘写进账本的 `source`。★ 与手工录入（`manual`）分开，
#: 这样流水页一眼看得出哪几笔是人敲的、哪几笔是引擎跑的。
SOURCE = 'paper'

#: 账户的 `mode` 取值。`live` 是默认（真金白银），`paper` 是模拟盘。
MODE_LIVE, MODE_PAPER = 'live', 'paper'


def is_paper(acct):
    """这个账户是不是模拟盘。

    ★ 判据放一处 —— 散在各页写 `a.get('mode') == 'paper'` 的话，
      将来加个 `mode` 取值就得满仓库找（同「判据只有一份」那条）。
    """
    return (acct or {}).get('mode') == MODE_PAPER


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
    out = []
    for r in _base.fills(aid):
        if r.get('source') != SOURCE or r.get('reverse_of'):
            continue
        out.append(r)
    return out


def advance(aid, datalake=None, rebuild=False):
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

    eng, meta = _bench.build_engine(aid, datalake=datalake)
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

    # ---- 对账：账本里已有的那几天，重跑结果必须一致 ----
    # ★ 按**账本最后一天**截断再比 —— 不截的话"今天新跑出一笔"会被当成
    #   不一致，而那正是我们要追加的东西。
    mismatch = None
    if have:
        last = max(str(r['trade_date'])[:10] for r in have)
        a_keys = [_key_of(r) for r in have]
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
                        '账本是 append-only 的证据，**不会被静默改写**；'
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

    st = state(aid)
    st.update({
        'recon': recon,
        'n_dividends': n_div,
        'advanced_to': str(meta['end'])[:10],
        'advanced_at': _base._now(),
        'data_fingerprint': meta.get('data_fingerprint'),
        'sha': meta.get('sha'),
        'n_fills': len(got),
        'errors': errs[:20],
    })
    st.pop('mismatch', None)
    _save_state(aid, st)
    return {'ok': True, 'added': added, 'advanced_to': st['advanced_to'],
            'n_fills': len(got), 'n_dividends': n_div, 'recon': recon,
            'errors': errs[:20], 'mismatch': None}


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
