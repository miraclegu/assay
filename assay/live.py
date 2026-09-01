#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""实盘账户：成交流水 -> 真实持仓 -> 喂【真引擎】-> 捕获它想下的单。

## 一句话原理

**不重写任何交易规则。** 止损、炸板离场、调仓三条规则都埋在策略 + 引擎里，
依赖 `g.pos_state` / `g.high_limit` / `pos[code].entry_price` 这些内部状态。
本模块只做两件事：

  1. 用你录的成交流水**重建 Portfolio**（FIFO lots，真实成本价）
  2. 把 broker 换成 `RecordingBroker`（只记不成交），让策略照常跑它自己的
     代码路径，我们读它下的单

所以实盘提示与回测行为**天然同源** —— 不存在"两份实现慢慢漂移"。
本项目已经因为「同一件事两处写」吃过亏（旧引擎三处各写一遍卖出，
交易日志只挂在其中一处，一次归因诊断全错、白查一轮）。

## 目录

    live/
      accounts.json            账户表
      trade_calendar.json      未来交易日
      <acct_id>/
        versions.jsonl         append-only 版本绑定历史
        code/<sha8>.py         绑定过的每个版本的策略源码【独立副本】
        fills.jsonl            append-only 成交流水
        signals/<date>.json    每天算出的待办

★ `code/<sha8>.py` 为什么独立存一份，而不指向 `runs/<run_id>/strategy.py`：
  `runs/` 在 .gitignore 里（248M 二进制产物，随时可能被清）。实盘账户绑的
  版本是**决策证据**，放在会被清掉的目录里等于没留痕 —— 那正是本模块要
  解决的问题。同理整个 live/ 入版本控制（与 picks.json 同一理由）。

★ fills 只追加不修改。录错了写一条**反向冲正**，原记录留着。
  持仓不单独存，永远由 fills 推导 —— 单一事实来源。

## 为什么需要 trade_calendar.json

`PanelFeed.trading_days` 来自**面板**（有行情的日子），所以永远不含未来。
而"下一个交易日是哪天"决定了今天要不要调仓，且**无法从星期推出**
（春节/国庆）。**但本地能算**：`tdx.db` 的 `raw_holidays` 表有休市日清单
（1991~2030），交易日 = 工作日 − 休市日，这条规则与权威日历逐日对数一致
5745 天。生成脚本 `datalake/build/build_trade_calendar.py`，每次生成都重跑
对数，不一致就拒绝写出。拿不到日历就 `LiveError` 报错 —— 不猜。
"""
import datetime
import hashlib
import importlib.util
import json
import os

from . import api
from .broker import Cost, RecordingBroker
from .context import Lot, Position
from .engine import Engine
from .feed import PanelFeed

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
LIVE = os.path.join(ROOT, 'live')

ACCT_ID_OK = set('abcdefghijklmnopqrstuvwxyz0123456789_-')
SIDES = ('buy', 'sell')
LOT_SIZE = 100
DEFAULT_WARMUP_START = '2016-01-01'
# ★ 兜底值，不是主路径。主路径是 datalake/sync_daily.sh 同步完成后【直接调
#   live.tick(force=True)】—— 把「信号必须用最新数据」这条依赖写进调用顺序，
#   而不是靠「同步 18:10 / 出信号 19:00」两个时间常量隔开。后者一旦同步变慢
#   就错位，而错位的表现是【信号静默用了昨天的数据】。
#   所以这里设得很晚：只在同步压根没跑（机器睡了、脚本坏了）时兜一次。
DEFAULT_TICK_TIME = '22:00'
PRICE_BUFFER = 1.05              # 限价 = T-1 收盘 x 这个系数（防高开买不进）
# 可信的日历来源。★ tdx.raw_holidays 那条【每次生成都跑对数】：
#   工作日 − 休市日 与 std/trading_calendar.parquet 逐日一致 5745 天才写出，
#   不一致就拒绝写（见 datalake/build/build_trade_calendar.py）。
#   所以它和聚宽的 get_all_trade_days 同级可信，不是"凑合用"。
AUTHORITATIVE_CAL = ('tdx.raw_holidays', 'jq.get_all_trade_days')


class LiveError(Exception):
    """给用户看的错误。**宁可不给结果，也不给猜出来的结果。**"""


# ============================ 原子读写 ============================
# 与 server.py 的 _save_marks 同款：先写 tmp 再 rename。
# 半截文件会让整个账户的记录消失，而它看起来只是"少了几笔"。

def _atomic_write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        f.write(text)
    os.replace(tmp, path)


def _read_json(path, default):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except Exception:                                       # noqa: BLE001
        return default


def _append_jsonl(path, obj):
    """追加一行。★ 不用 _atomic_write —— 那是全量重写，会把并发的另一条吞掉。
    单行 append 在 POSIX 上对 <4KB 的写是原子的，而我们的行远小于这个。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'a', encoding='utf-8') as f:
        f.write(json.dumps(obj, ensure_ascii=False, sort_keys=True) + '\n')


def _read_jsonl(path):
    out = []
    if not os.path.exists(path):
        return out
    with open(path, encoding='utf-8') as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            try:
                out.append(json.loads(ln))
            except Exception:                               # noqa: BLE001
                continue          # 坏行跳过，不让一行毁掉整个账本
    return out


def _now():
    return datetime.datetime.now().replace(microsecond=0).isoformat()


def _d(x):
    """任意形态 -> datetime.date。"""
    if isinstance(x, datetime.datetime):
        return x.date()
    if isinstance(x, datetime.date):
        return x
    return datetime.date.fromisoformat(str(x)[:10])


# ============================ 账户 ============================

def acct_dir(aid):
    if not aid or not set(aid) <= ACCT_ID_OK:
        raise LiveError('账户 id 只能用小写字母/数字/下划线/中划线：%r' % aid)
    return os.path.join(LIVE, aid)


def new_account_id():
    """自动生成账户 id。

    ★ id 与显示名【解耦】：id 是内部主键（决定 live/<id>/ 目录名，改了就等于
      换了个账户，流水和版本历史全断），name 随时可改。用户只填 name。
      —— 原来让用户填 id、且没有改名入口，等于把「一段时间用策略 A、
      之后换策略 B」这种正常演化逼成「新建一个账户」，历史就断了。
    """
    used = {a['id'] for a in load_accounts()}
    i = 1
    while ('a%d' % i) in used:
        i += 1
    return 'a%d' % i


def load_accounts():
    return _read_json(os.path.join(LIVE, 'accounts.json'), [])


def _save_accounts(lst):
    _atomic_write(os.path.join(LIVE, 'accounts.json'),
                  json.dumps(lst, ensure_ascii=False, indent=1, sort_keys=True))


def archive_account(aid, on=True):
    """归档 / 取消归档。**不删任何数据** —— 只是从默认列表里隐去。

    ★ 刻意没有「删除账户」：实盘流水与版本快照是决策证据，删了就没法复盘。
      真要清理就手动 rm live/<id>/，那时你会清楚自己在丢什么。
    """
    lst = load_accounts()
    hit = next((a for a in lst if a['id'] == aid), None)
    if hit is None:
        raise LiveError('账户不存在：%s' % aid)
    hit['archived'] = bool(on)
    _save_accounts(lst)
    return hit


def get_account(aid):
    for a in load_accounts():
        if a['id'] == aid:
            return a
    raise LiveError('账户不存在：%s' % aid)


def upsert_account(aid, name=None, init_cash=None, broker_note=None,
                   tick_time=None, warmup_start=None):
    lst = load_accounts()
    hit = next((a for a in lst if a['id'] == aid), None)
    if hit is None:
        acct_dir(aid)                       # 校验 id
        hit = {'id': aid, 'name': name or aid, 'init_cash': float(init_cash or 0),
               'broker_note': broker_note or '', 'tick_time': DEFAULT_TICK_TIME,
               'warmup_start': DEFAULT_WARMUP_START, 'created': _now(),
               'code_sha256': None, 'strategy_path': None, 'params': {}}
        lst.append(hit)
    if name is not None:
        hit['name'] = name
    if init_cash is not None:
        hit['init_cash'] = float(init_cash)
    if broker_note is not None:
        hit['broker_note'] = broker_note
    if tick_time is not None:
        hit['tick_time'] = tick_time
    if warmup_start is not None:
        hit['warmup_start'] = warmup_start
    _save_accounts(lst)
    return hit


# ============================ 版本绑定 ============================
# ★ 这是本模块的核心承诺：**历史策略版本不能失踪**。
#   绑定的那一刻把磁盘上的源码全文复制到 live/<id>/code/<sha8>.py，
#   并往 versions.jsonl 追加一行。两者都在版本控制里，
#   删掉整个 runs/ 目录也读得到。

# 无法从成交流水重建的规则：这些依赖【持仓期内的路径】，fills 里没有。
# 与其静默给出偏乐观的提示，不如拒绝绑定。
UNSUPPORTED = {
    'trail_stop': '移动止损依赖持仓期内的峰值，成交流水里没有',
    'chand_k': '吊灯止损依赖持仓期内的 ATR/最高价序列，成交流水里没有',
    'rebal_every': '固定间隔调仓的相位依赖回测起点的绝对序号，预览窗口里推不出',
}


def _load_with_deps(path):
    """执行策略文件，并记下它【从仓库里另外加载的 .py】。

    ★ 为什么要抓依赖：`strategies/小市值/froec_traded.py` 用
      `spec_from_file_location(..., 同目录/froec.py)` 复用基线实现。
      只快照主文件会有两个后果，都很糟：
        1. 快照挪到 live/<id>/code/ 后相对路径断了，根本跑不起来
        2. 更糟 —— 改 froec.py 【不会】改变账户的版本哈希，
           于是账户悄悄换了行为而版本号纹丝不动。**版本漂移无声发生。**
      所以依赖也进快照，且版本哈希覆盖【全部文件】。

    做法是包一层 `importlib.util.spec_from_file_location` 把 location 记下来
    —— 策略调的就是这个函数对象，包住它是精确的，不靠猜。
    """
    seen = []
    orig = importlib.util.spec_from_file_location

    def _tap(name, location=None, *a, **kw):
        if location and str(location).endswith('.py'):
            ap = os.path.abspath(str(location))
            if ap.startswith(ROOT + os.sep) and ap != os.path.abspath(path):
                seen.append(ap)
        return orig(name, location, *a, **kw)

    importlib.util.spec_from_file_location = _tap
    try:
        spec = orig('_live_probe_%d' % abs(hash(path)), path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        importlib.util.spec_from_file_location = orig
    return [os.path.abspath(path)] + sorted(set(seen))


def _bundle_sha(files):
    """版本哈希覆盖【主文件 + 全部依赖】—— 少算一个就是给版本漂移开后门。"""
    h = hashlib.sha256()
    for f in sorted(files, key=os.path.basename):
        h.update(os.path.basename(f).encode())
        h.update(b'\0')
        h.update(open(f, 'rb').read())
        h.update(b'\0')
    return h.hexdigest()


def bind_version(aid, strategy_path, params=None, reason=''):
    """把账户绑到【磁盘当前文件】的这个版本，并永久留痕。"""
    get_account(aid)
    p = strategy_path if os.path.isabs(strategy_path) \
        else os.path.join(ROOT, strategy_path)
    if not os.path.isfile(p):
        raise LiveError('策略文件不存在：%s' % strategy_path)
    raw = open(p, 'rb').read()
    params = dict(params or {})
    bad = [(k, why) for k, why in UNSUPPORTED.items()
           if float(params.get(k) or 0)]
    if bad:
        raise LiveError('这些参数实盘模块无法如实重建，拒绝绑定：\n' +
                        '\n'.join('  %s = %s —— %s' % (k, params[k], why)
                                  for k, why in bad))
    files = _load_with_deps(p)
    sha = _bundle_sha(files)
    d = os.path.join(acct_dir(aid), 'code', sha[:8])
    for f in files:
        dst = os.path.join(d, os.path.basename(f))
        if not os.path.exists(dst):
            _atomic_write(dst, open(f, encoding='utf-8').read())
    _append_jsonl(os.path.join(acct_dir(aid), 'versions.jsonl'), {
        'ts': _now(), 'code_sha256': sha, 'code_sha': sha[:8],
        'strategy_path': strategy_path, 'params': params, 'reason': reason,
        'main': os.path.basename(p),
        # ★ 主文件【自己】的哈希 —— 归档的 meta.code_sha256 就是这个口径
        #   （单文件字节哈希）。账户的 code_sha256 是**打包哈希**（主文件+依赖），
        #   两者不可比。想把账户版本关联到历史回测，必须另存这一个。
        'main_sha256': hashlib.sha256(raw).hexdigest(),
        'files': [os.path.relpath(f, ROOT) for f in files],
    })
    lst = load_accounts()
    for a in lst:
        if a['id'] == aid:
            a['code_sha256'] = sha
            a['strategy_path'] = strategy_path
            a['params'] = params
    _save_accounts(lst)
    return get_account(aid)


def versions(aid):
    return _read_jsonl(os.path.join(acct_dir(aid), 'versions.jsonl'))


def _main_sha(aid, v):
    """主文件自身哈希。老记录没存这个字段，从快照现算 —— 不迁移文件，
    因为 versions.jsonl 是 append-only 的账本，改写它本身就违背设计。"""
    if v.get('main_sha256'):
        return v['main_sha256']
    p = os.path.join(acct_dir(aid), 'code', v['code_sha256'][:8],
                     v.get('main') or '')
    if os.path.isfile(p):
        return hashlib.sha256(open(p, 'rb').read()).hexdigest()
    return None


def _version_row(aid, sha):
    hit = [v for v in versions(aid)
           if v['code_sha256'] == sha or v['code_sha256'].startswith(sha)]
    if not hit:
        raise LiveError('该账户没有绑定过版本 %s' % sha)
    return hit[-1]


def version_code(aid, sha, which=None):
    """读某个历史版本的源码快照。★ 只接受 versions.jsonl 里出现过的 sha ——
    唯一的路径来源，杜绝目录穿越。which 指定看哪个文件（默认主文件）。"""
    v = _version_row(aid, sha)
    d = os.path.join(acct_dir(aid), 'code', v['code_sha256'][:8])
    names = [os.path.basename(x) for x in v.get('files') or [v.get('main')]]
    name = which or v.get('main') or names[0]
    if name not in names:
        raise LiveError('版本 %s 里没有文件 %s（有：%s）'
                        % (v['code_sha'], name, ', '.join(names)))
    p = os.path.join(d, name)
    if not os.path.exists(p):
        raise LiveError('版本快照文件丢失：%s —— 这不该发生，'
                        'live/ 应在版本控制里' % os.path.relpath(p, ROOT))
    return open(p, encoding='utf-8').read(), v['code_sha256'], names


# ============================ 成交流水 ============================

def fills(aid):
    return _read_jsonl(os.path.join(acct_dir(aid), 'fills.jsonl'))


def add_fill(aid, trade_date, code, side, shares, price, fee=0.0,
             name='', source='manual', note='', reverse_of=None):
    """录一笔成交。**只追加**。

    校验在这里做而不是页面上 —— 页面能绕过，这里是唯一入口。
    """
    if side not in SIDES:
        raise LiveError('side 只能是 buy/sell，收到 %r' % side)
    try:
        d = _d(trade_date)
    except Exception:                                       # noqa: BLE001
        raise LiveError('成交日期格式应为 YYYY-MM-DD，收到 %r' % trade_date)
    try:
        shares = int(shares)
        price = float(price)
        fee = float(fee or 0)
    except Exception:                                       # noqa: BLE001
        raise LiveError('数量/价格/费用必须是数字')
    if shares <= 0 or price <= 0:
        raise LiveError('数量与价格必须为正（撤销请用反向冲正，不要填负数）')
    if side == 'buy' and shares % LOT_SIZE:
        raise LiveError('买入数量必须是 %d 的整数倍，收到 %d' % (LOT_SIZE, shares))
    if '.' not in code:
        raise LiveError('代码要带市场后缀，如 601857.XSHG，收到 %r' % code)
    # 卖出不能超过当前持仓 —— 这类错录进去会让后面所有信号都错，且不报错。
    if side == 'sell':
        held = positions(aid).get(code, {}).get('shares', 0)
        if shares > held:
            raise LiveError('卖出 %d 股超过当前持仓 %d 股（%s）'
                            % (shares, held, code))
    rec = {'ts': _now(), 'trade_date': d.isoformat(), 'code': code,
           'name': name, 'side': side, 'shares': shares, 'price': price,
           'fee': fee, 'source': source, 'note': note}
    if reverse_of:
        rec['reverse_of'] = reverse_of
    _append_jsonl(os.path.join(acct_dir(aid), 'fills.jsonl'), rec)
    return rec


def fifo_lots(rows):
    """成交流水 -> {code: [ {shares, date, price} ]}，FIFO 冲减。

    ★ 用 FIFO 而不是平均成本：引擎本身就是分批 FIFO（红利税按持有期分档、
      T+1 只锁当日买入那批），平均成本会让重建出来的持仓与引擎语义不一致。
    """
    book = {}
    for r in sorted(rows, key=lambda x: (x['trade_date'], x['ts'])):
        c = r['code']
        lots = book.setdefault(c, [])
        if r['side'] == 'buy':
            lots.append({'shares': int(r['shares']), 'date': _d(r['trade_date']),
                         'price': float(r['price'])})
        else:
            left = int(r['shares'])
            while left > 0 and lots:
                if lots[0]['shares'] <= left:
                    left -= lots[0]['shares']
                    lots.pop(0)
                else:
                    lots[0]['shares'] -= left
                    left = 0
    return {c: v for c, v in book.items() if v}


def lots_asof(rows, day):
    """截至 day（含）的真实持仓。重放 warmup 时每天都要用。"""
    day = _d(day)
    return fifo_lots([r for r in rows if _d(r['trade_date']) <= day])


def cash_asof(init_cash, rows, day, flows=()):
    day = _d(day)
    v = float(init_cash or 0)
    for r in flows:
        if _d(r['date']) <= day:
            v += float(r.get('signed') or 0)
    for r in rows:
        if _d(r['trade_date']) > day:
            continue
        amt = r['shares'] * r['price']
        v += (-amt if r['side'] == 'buy' else amt) - float(r.get('fee') or 0)
    return v


def positions(aid):
    """{code: {shares, cost, lots}}，cost 为股数加权平均成本（仅展示用）。

    ★ lots 里的日期转成 ISO 字符串 —— 这个函数是【接口层】的返回值，
      带 datetime.date 会让 json.dumps 直接抛 TypeError。
      内部计算一律用 fifo_lots（保留 date 对象），不要混用。
    """
    book = fifo_lots(fills(aid))
    out = {}
    for c, lots in book.items():
        sh = sum(l['shares'] for l in lots)
        out[c] = {'shares': sh,
                  'cost': sum(l['shares'] * l['price'] for l in lots) / sh,
                  'lots': [dict(l, date=l['date'].isoformat()) for l in lots]}
    return out


def cashflows(aid):
    """入金 / 出金 / 手工调整。**append-only**，与成交流水同一原则。

    ★ 为什么不是「让 init_cash 可改」：init_cash 是【开户那一刻】的余额。
      后来入金 5 万，改 init_cash 会把这 5 万追溯到开户日，于是过去每一天的
      权益都变了 —— 而回看历史时没有任何痕迹说明它变过。
      入金是个**事件**，就该按事件记。分红到账、利息、手续费返还同理。
    """
    return _read_jsonl(os.path.join(acct_dir(aid), 'cashflows.jsonl'))


CASH_KINDS = ('deposit', 'withdraw', 'dividend', 'adjust')


def add_cashflow(aid, date, amount, kind='deposit', note=''):
    """amount 一律填【正数】，方向由 kind 决定 —— 避免"负的出金"这种双重否定。"""
    get_account(aid)
    if kind not in CASH_KINDS:
        raise LiveError('kind 只能是 %s，收到 %r' % ('/'.join(CASH_KINDS), kind))
    try:
        d = _d(date)
        amount = float(amount)
    except Exception:                                       # noqa: BLE001
        raise LiveError('日期或金额格式不对')
    if amount <= 0:
        raise LiveError('金额填正数，方向由类型决定（出金选 withdraw）')
    signed = -amount if kind == 'withdraw' else amount
    if kind == 'withdraw' and cash(aid) < amount:
        raise LiveError('出金 %.2f 超过当前现金 %.2f' % (amount, cash(aid)))
    rec = {'ts': _now(), 'date': d.isoformat(), 'kind': kind,
           'amount': amount, 'signed': signed, 'note': note}
    _append_jsonl(os.path.join(acct_dir(aid), 'cashflows.jsonl'), rec)
    return rec


def cash(aid, asof=None):
    """现金 = 初始资金 + 现金流水 − 买入额 − 费用 + 卖出额。

    分红**按你录的 dividend 流水计**，不自动推 —— 没有数据源能确认到账日
    与实际税后金额。不录就是不计，此时现金是【下界】。
    """
    acct = get_account(aid)
    v = float(acct.get('init_cash') or 0)
    lim = _d(asof) if asof else None
    for r in cashflows(aid):
        if lim and _d(r['date']) > lim:
            continue
        v += float(r.get('signed') or 0)
    for r in fills(aid):
        if lim and _d(r['trade_date']) > lim:
            continue
        amt = r['shares'] * r['price']
        v += (-amt if r['side'] == 'buy' else amt) - float(r.get('fee') or 0)
    return v


# ============================ 交易日历 ============================

def _calendar():
    p = os.path.join(LIVE, 'trade_calendar.json')
    d = _read_json(p, None)
    if not d or not d.get('days'):
        raise LiveError(
            '缺少未来交易日历（%s）。\n'
            '面板只有【有行情的日子】，推不出下一个交易日，而春节/国庆'
            '无法从星期推出 —— 所以这里不猜。\n'
            '补法：在聚宽研究环境跑 get_all_trade_days()，导出成\n'
            '  {"days": ["2026-09-01", ...], "source": "jq", "updated": "..."}\n'
            '放到 %s' % (os.path.relpath(p, ROOT), os.path.relpath(p, ROOT)))
    return [datetime.date.fromisoformat(x) for x in d['days']], d


def next_trading_day(after):
    days, meta = _calendar()
    after = _d(after)
    for x in days:
        if x > after:
            return x
    raise LiveError('交易日历只到 %s，无法确定 %s 的下一个交易日 —— 请补日历'
                    % (days[-1] if days else '(空)', after))


def calendar_days():
    try:
        return _calendar()[0]
    except LiveError:
        return []


def calendar_meta():
    """日历的来源。★ 非聚宽来源要在信号里显式告警 —— 猜出来的休市安排
    会让"该调仓的日子不提示"或"休市日发一堆单"，而两者都不报错。"""
    try:
        return _calendar()[1]
    except LiveError as e:
        return {'source': None, 'error': str(e)}


# ============================ 信号生成 ============================

def _load_snapshot(aid, sha):
    """从版本快照加载策略模块。**不读磁盘当前文件** ——
    账户绑的是那个快照，磁盘改了是另一个版本。

    ★ 快照是【目录】不是单文件：依赖（如 froec_traded -> froec）与主文件
      放在同一目录下，所以主文件里那句
      `spec_from_file_location(..., 同目录/froec.py)` 解析到的是
      **快照里的依赖**，不是磁盘上的当前版本。
    """
    v = _version_row(aid, sha)
    full = v['code_sha256']
    p = os.path.join(acct_dir(aid), 'code', full[:8], v.get('main') or '')
    if not os.path.exists(p):
        raise LiveError('版本快照丢失：%s' % os.path.relpath(p, ROOT))
    spec = importlib.util.spec_from_file_location('_live_%s_%s' % (aid, full[:8]), p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod, full


def _asof_factor(feed, codes, day):
    """{code: (hfq_factor, close_hfq, close_bfq)}，按 <= day 取最近一条。
    停牌股取最后已知值 —— 与引擎「按最后已知价挂账」一致。"""
    if not codes:
        return {}
    q = "','".join(codes)
    rows = feed.con.execute("""
        SELECT code, hfq_factor, close_hfq, close_bfq FROM (
          SELECT jq_code AS code, hfq_factor, close_hfq, close_bfq,
                 row_number() OVER (PARTITION BY jq_code ORDER BY date DESC) rn
          FROM read_parquet('%s/mart/panel_daily/panel_*.parquet')
          WHERE jq_code IN ('%s') AND date <= DATE '%s'
            AND date > DATE '%s' - INTERVAL 400 DAY
        ) WHERE rn = 1""" % (feed.root, q, day, day)).fetchall()
    return {r[0]: (r[1], r[2], r[3]) for r in rows}


def _seed(eng, book, feed, day_before):
    """把真实持仓播种进引擎的 Portfolio，状态等价于【day_before 收盘后】。

    ★ 单位换算。`Lot.shares` 是**后复权记账单位**，真实股数 = shares × factor。
      引擎的不变量是 `真实股数 == lot.shares × factor(当日)`：除权日
      broker.start_day 把 shares 缩 (1-frac)、factor 同步放大 1/(1-frac)，
      两者抵消（推导见 broker.py start_day 的注释）。所以：

          lot.shares      = 真实股数 / factor(day_before)
          lot.entry_price = 真实成交价 × factor(建仓日)

      entry_price 用**建仓日**因子是刻意的 —— 引擎在除权时【不缩 entry_price】
      （缩了会把分红错记成价差收益，见 broker.py 的推导）。播种要复现的是
      引擎的状态，不是"更合理的成本价"。

    ★ 播到 day_before 而不是当日：start_day(当日) 会用 _prev_factor 处理
      当日除权。若直接按当日因子播种，当天正好除权的票会被**缩两次**。
    """
    need = list(book)
    fac_now = _asof_factor(feed, need, day_before)
    entry_days = sorted({l['date'] for lots in book.values() for l in lots})
    fac_entry = {}
    for ed in entry_days:
        fac_entry[ed] = _asof_factor(
            feed, [c for c, lots in book.items()
                   if any(l['date'] == ed for l in lots)], ed)
    missing = [c for c in need if c not in fac_now]
    if missing:
        raise LiveError('这些持仓在面板里查不到行情，无法估值：%s' % ', '.join(missing))
    for c, lots in book.items():
        f_now, close_hfq, _bfq = fac_now[c]
        f_now = f_now or 1.0
        pos = Position(code=c, lots=[], last_price=close_hfq or 0.0)
        for l in lots:
            f_e = (fac_entry.get(l['date'], {}).get(c) or (1.0,))[0] or 1.0
            pos.lots.append(Lot(shares=l['shares'] / f_now,
                                entry_date=l['date'],
                                entry_price=l['price'] * f_e))
        eng.pf.positions[c] = pos
        eng.broker._prev_factor[c] = f_now


def _extend_ordinals(eng, feed, future_days):
    """把未来交易日并进周/月序号表，让 `_due` 能判断【下一个交易日】。

    ★ 必须用「历史 + 未来」的完整日历重算，不能只补未来那几天：
      本周/本月的桶在面板侧是**截断**的（面板只到最新数据日），
      截断的桶算出来的负序号（-1 = 本周最后一个交易日）是错的。
    """
    days = list(feed.trading_days) + [d for d in future_days
                                      if d > feed.trading_days[-1]]
    for key_fn, store in ((lambda x: x.isocalendar()[:2], eng._wk_ord),
                          (lambda x: (x.year, x.month), eng._mo_ord)):
        buckets = {}
        for d in days:
            buckets.setdefault(key_fn(d), []).append(d)
        for grp in buckets.values():
            n = len(grp)
            for j, d in enumerate(grp):
                store[d] = (j + 1, j - n)


def _run_tasks(eng, feed, day, freqs, data_day=None):
    """在 day 上执行指定频率的任务，返回这一轮记录到的委托。

    ★ `data_day` 是【行情所在的那一天】。判断调仓时 day 是**下一个交易日**，
      它还没有行情，而策略会调 `context.tradable(...)` 查当日停牌/涨跌停。
      这里让 broker 停留在最新数据日 —— 等价于假设
      「明天的可交易状态与今天相同」。这是**明确的近似**，不是 bug：
      明天谁停牌、谁一字板，今天物理上不可知。信号里会带这条告警。
    """
    if data_day is not None:
        eng.broker.date = data_day
    from .engine import _phase
    eng.ctx.current_date = day
    prev = feed.prev_trading_day(day)
    if prev is None and feed.trading_days:
        prev = feed.trading_days[-1] if day > feed.trading_days[-1] else None
    eng.ctx.previous_date = prev
    n0 = len(eng.broker.orders)
    for t, freq, func, wd, md, ev, off in eng._tasks:
        if freq not in freqs:
            continue
        if freq == 'n':
            raise LiveError('该策略用了 rebal_every（固定间隔调仓），'
                            '相位依赖回测起点的绝对序号，预览推不出 —— 拒绝出信号')
        if freq in ('w', 'm') and not eng._due(0, day, freq, wd, md, ev, off):
            continue
        ph = _phase(t)
        eng.broker.phase = ph
        eng.ctx.current_phase = ph
        eng.guard.set_clock(day, ph)
        func(eng.ctx)
    return eng.broker.orders[n0:]


WARMUP_DAYS = 30      # 重放窗口，要盖住 froec 的 limit_days=20


def _replay(eng, feed, rows, init_cash, days, flows=()):
    """在 warmup 窗口上**逐日重放** run_daily 任务，让策略自己把路径状态建起来。

    ★ 为什么不能只跑最后一天。froec 有三处【逐日累积】的状态：
        g.hold_history  20 日内持有过的票（配 had_limit_up 做涨停黑名单）
        g.stop_banned   止损冷静期
        g.pos_state     吊灯/移动止损的峰值与 ATR 窗口
      只跑一天，这些全是空的 —— 黑名单会漏、冷静期会失效。而且
      **不报错**，只是多买几只本不该买的票。实测就踩到了：账户持有
      603506 时，只跑一天的版本把它当成"没持有过"，与真实规则不符。

    重放的每一天都把持仓/现金**按成交流水复原到那天的真实状态**，
    所以 hold_history 记下的是你真实持有过的票，不是引擎自己模拟出来的。
    """
    for d in days:
        book = lots_asof(rows, d)
        eng.pf.positions.clear()
        _seed(eng, book, feed, feed.prev_trading_day(d) or d)
        eng.pf.cash = cash_asof(init_cash, rows, d, flows)
        eng.broker.start_day(d)
        _run_tasks(eng, feed, d, {'d'})


def _twopass(codes, px, money):
    """先按 money/n 预分配，买不进的不占份额，剩余再平分给买得进的。

    与 jq/strategies/hongli_preview.py 同算法 —— 实测 max/min 权重 1.015、
    闲置现金 0.4%（fixed 闲置 20.6%、seq 权重漂 1.34 倍）。
    """
    n = len(codes)
    if not n:
        return {}, money
    lim = {c: round(px[c] * PRICE_BUFFER, 2) for c in codes}
    plan, spent = {}, 0.0
    per = money / n
    for c in codes:
        a = LOT_SIZE * int(per / lim[c] / LOT_SIZE) if lim[c] > 0 else 0
        if a <= 0:
            continue
        plan[c] = a
        spent += a * lim[c]
    if plan:
        add = (money - spent) / len(plan)
        for c in list(plan):
            m = LOT_SIZE * int(add / lim[c] / LOT_SIZE) if lim[c] > 0 else 0
            if m > 0:
                plan[c] += m
                spent += m * lim[c]
    return {c: (plan[c], lim[c]) for c in plan}, money - spent


def build_signal(aid, datalake=None):
    """算出【下一个交易日】的待办。返回可直接落盘的 dict。"""
    acct = get_account(aid)
    if not acct.get('code_sha256'):
        raise LiveError('账户 %s 还没绑定策略版本' % aid)
    book = fifo_lots(fills(aid))
    money = cash(aid)

    today = datetime.date.today().isoformat()
    feed = PanelFeed(acct.get('warmup_start') or DEFAULT_WARMUP_START,
                     today, root=datalake)
    t1 = feed.trading_days[-1]                    # 最新数据日
    t0 = feed.prev_trading_day(t1)
    t = next_trading_day(t1)                      # 下一个交易日；拿不到就报错

    mod, full_sha = _load_snapshot(aid, acct['code_sha256'])
    eng = Engine(mod, feed, cash=money, cost=Cost(), params=acct.get('params') or {})
    rb = RecordingBroker(eng.pf, feed, eng.cost)
    eng.broker = rb
    # ★ Context 里存的字段名是 `_broker`。写 ctx.broker 只会凭空多出一个
    #   属性，上下文仍指着原来那个 broker —— 而它 date=None，
    #   于是 context.tradable() 会拿 DATE 'None' 去查行情。
    #   这类"设了个没人读的属性"是静默失败，必须直接写 _broker。
    eng.ctx._broker = rb
    eng._boot()
    try:
        _extend_ordinals(eng, feed, calendar_days())
        # --- 1) 重放 warmup：建起逐日累积的路径状态（黑名单/冷静期/峰值）---
        rows = fills(aid)
        init = float(acct.get('init_cash') or 0)
        warm = feed.trading_days[-WARMUP_DAYS:]
        _replay(eng, feed, rows, init, warm[:-1], cashflows(aid))
        # --- 2) 离场检查：在【最新数据日】跑 run_daily 类任务 ---
        #     语义是「今天收盘触发 -> 明天开盘卖」。本地只有日线，
        #     盘中实时判定物理上做不到，这是能做到的最早时点。
        eng.pf.positions.clear()
        _seed(eng, book, feed, t0 or t1)
        eng.pf.cash = money
        rb.start_day(t1)                          # 刷新 last_price / 处理当日除权
        n0 = len(rb.orders)
        exit_orders = _run_tasks(eng, feed, t1, {'d'})
        # --- 3) 调仓检查：下一个交易日是不是调仓日 ---
        rebal_orders = _run_tasks(eng, feed, t, {'w', 'm'}, data_day=t1)
        is_rebal = bool(rebal_orders)
        held_after = dict(eng.pf.positions)        # RecordingBroker 不成交，等于真实持仓
    finally:
        api._unbind()

    # ---- 汇总成买卖清单 ----
    reason = {}
    for o in exit_orders:
        if o['target_value'] == 0:
            reason[o['code']] = 'stop' if o['kind'] == 'stop' else 'limit_up_exit'
    sells, targets = {}, []
    for o in rebal_orders:
        if o['target_value'] == 0:
            reason.setdefault(o['code'], 'rebalance_out')
        elif o['code'] not in targets:
            targets.append(o['code'])
    for c in reason:
        if c in book:
            sells[c] = sum(l['shares'] for l in book[c])

    px = _asof_factor(feed, list(set(targets) | set(book)), t1)
    names = _names(feed, list(set(targets) | set(book)), t1)

    # 卖出腾出的现金按 T-1 收盘估（真实成交价当然不同，这里只为算买入股数）
    freed = sum(sells[c] * (px.get(c, (0, 0, 0))[2] or 0) for c in sells)
    # ★ 非调仓日 targets 是空的 —— 这时"持有不动"必须是【全部持仓减去要卖的】，
    #   照 targets 算会显示成 0 只，看着像空仓。
    keep = ([c for c in targets if c in book and c not in sells] if is_rebal
            else [c for c in book if c not in sells])
    buys = [c for c in targets if c not in book or c in sells]
    buy_px = {c: (px.get(c, (0, 0, 0))[2] or 0) for c in buys}
    bad_px = [c for c in buys if not buy_px[c]]
    plan, left = _twopass([c for c in buys if buy_px[c]], buy_px, money + freed)

    warn = []
    cm = calendar_meta()
    if (cm.get('source') or '') not in AUTHORITATIVE_CAL:
        warn.append('交易日历来源是 %r，不是聚宽权威日历 —— 休市安排可能不准。'
                    '跑一次 datalake 的聚宽增量抽取即可覆盖。' % cm.get('source'))
    if t1 < (feed.trading_days[-1] if feed.trading_days else t1):
        warn.append('面板数据不是最新的')
    if bad_px:
        warn.append('这些目标票取不到 T-1 价格，已从买入清单剔除：%s' % ', '.join(bad_px))
    if (datetime.date.today() - t1).days > 4:
        warn.append('最新数据日是 %s，距今 %d 天 —— datalake 可能没刷新'
                    % (t1, (datetime.date.today() - t1).days))

    fp = feed.fingerprint() if hasattr(feed, 'fingerprint') else {}
    return {
        'warnings': warn, 'calendar_source': cm.get('source'),
        'account': aid, 'for_date': t.isoformat(), 'data_asof': t1.isoformat(),
        'built_at': _now(), 'code_sha256': full_sha, 'code_sha': full_sha[:8],
        'params': acct.get('params') or {},
        'is_rebalance_day': is_rebal,
        'cash': round(money, 2), 'freed_est': round(freed, 2),
        'left_est': round(left, 2),
        'sell': [{'code': c, 'name': names.get(c, ''), 'shares': sells[c],
                  'reason': reason[c],
                  'ref_price': round(px.get(c, (0, 0, 0))[2] or 0, 2)}
                 for c in sorted(sells)],
        'buy': [{'code': c, 'name': names.get(c, ''), 'shares': plan[c][0],
                 'limit': plan[c][1], 'amount': round(plan[c][0] * plan[c][1], 2),
                 'ref_price': round(buy_px[c], 2)}
                for c in sorted(plan, key=lambda x: -plan[x][0] * plan[x][1])],
        'hold': [{'code': c, 'name': names.get(c, ''),
                  'shares': sum(l['shares'] for l in book[c])} for c in sorted(keep)],
        'no_price': bad_px,
        'data_fingerprint': fp,
        'held_codes': sorted(held_after),
    }


def _names(feed, codes, day):
    if not codes:
        return {}
    q = "','".join(codes)
    rows = feed.con.execute("""
        SELECT code, sec_name FROM (
          SELECT jq_code AS code, sec_name,
                 row_number() OVER (PARTITION BY jq_code ORDER BY date DESC) rn
          FROM read_parquet('%s/mart/panel_daily/panel_*.parquet')
          WHERE jq_code IN ('%s') AND date <= DATE '%s'
            AND date > DATE '%s' - INTERVAL 400 DAY
        ) WHERE rn = 1""" % (feed.root, q, day, day)).fetchall()
    return dict(rows)


# ============================ 落盘 / 定时 ============================

def signal_path(aid, for_date):
    return os.path.join(acct_dir(aid), 'signals', '%s.json' % for_date)


def load_signal(aid, for_date):
    return _read_json(signal_path(aid, for_date), None)


def latest_signal(aid):
    d = os.path.join(acct_dir(aid), 'signals')
    if not os.path.isdir(d):
        return None
    fs = sorted(x for x in os.listdir(d) if x.endswith('.json'))
    return _read_json(os.path.join(d, fs[-1]), None) if fs else None


def make_signal(aid, datalake=None, force=False):
    """算并落盘。已经算过就直接返回，除非 force。"""
    try:
        sig = build_signal(aid, datalake=datalake)
    except LiveError as e:
        return {'account': aid, 'error': str(e), 'built_at': _now()}
    p = signal_path(aid, sig['for_date'])
    if os.path.exists(p) and not force:
        old = _read_json(p, None)
        if old:
            return old
    _atomic_write(p, json.dumps(sig, ensure_ascii=False, indent=1, sort_keys=True))
    return sig


def due_now(acct, now=None):
    """到点了吗。tick_time 是 'HH:MM'。"""
    now = now or datetime.datetime.now()
    try:
        hh, mm = (acct.get('tick_time') or DEFAULT_TICK_TIME).split(':')
        return (now.hour, now.minute) >= (int(hh), int(mm))
    except Exception:                                       # noqa: BLE001
        return False


def tick(datalake=None, now=None, force=False):
    """守护线程每轮做的事：到点、且【下一个交易日】还没算过的账户，算一次。

    ★ 幂等：信号按 for_date 落盘，已存在就跳过 —— 所以重启服务不会重复跑，
      也不会因为每分钟轮询就每分钟算一遍。
    """
    out = []
    try:
        nxt = next_trading_day(datetime.date.today() - datetime.timedelta(days=1))
    except LiveError as e:
        return [{'account': None, 'error': str(e), 'built_at': _now()}]
    for a in load_accounts():
        if not a.get('code_sha256'):
            continue
        if not force and not due_now(a, now):
            continue
        if not force and os.path.exists(signal_path(a['id'], nxt.isoformat())):
            continue
        try:
            out.append(make_signal(a['id'], datalake=datalake, force=force))
        except Exception as e:                              # noqa: BLE001
            out.append({'account': a['id'],
                        'error': '%s: %s' % (type(e).__name__, e),
                        'built_at': _now()})
    return out
