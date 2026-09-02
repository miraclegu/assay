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
import uuid

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


def _write_jsonl(path, rows):
    """整文件重写 jsonl（先 tmp 再 rename）。

    ★ 这个模块的账本原则是**只追加**，所以这个函数【只给一处用】：
      `add_fee_rate(supersede=True)` 删掉被同日盖掉的那档。
      为什么那种情况可以删：被盖掉的记录有效区间是**零长度**
      （to = 下一档 from − 1 天 < from），也就是**从来没有任何一笔成交
      按它算过** —— 删掉它不丢任何"当时用的是哪档"的信息，只是去掉噪声。
      成交流水、策略版本、现金流水一律不用这个函数。
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + '\n')
    os.replace(tmp, path)


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


def _uid():
    """记录的唯一标识。

    ★ 不能用 `ts` 当主键 —— `_now()` 是【秒精度】，同一秒内连续录两笔会得到
      完全相同的 ts。实测踩到：冲正记录的 reverse_of 指向那个 ts，
      by_ts 映射里只留下了后写入的那条，于是冲正配对到了**错的记录**，
      FIFO 批次没被还原（成本价与建仓日都错，而它们直接喂给止损判定）。
      ts 保留秒精度是为了可读；身份另用 uid。
    """
    return uuid.uuid4().hex[:12]


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
                   tick_time=None, warmup_start=None, fee=None):
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
    if fee is not None:
        bad = [k for k in fee if k not in FEE_FIELDS]
        if bad:
            raise LiveError('费率字段只能是 %s，收到 %s'
                            % ('/'.join(FEE_FIELDS), bad))
        if 'mode' in fee and fee['mode'] not in FEE_MODES:
            raise LiveError('mode 只能是 %s' % '/'.join(FEE_MODES))
        if 'commission_incl_reg' in fee:
            fee['commission_incl_reg'] = bool(fee['commission_incl_reg'])
        for k in ('commission', 'min_commission', 'regulatory', 'transfer',
                  'buy_rate', 'sell_rate', 'flat_min'):
            if k in fee:
                try:
                    fee[k] = float(fee[k])
                except Exception:                           # noqa: BLE001
                    raise LiveError('%s 必须是数字' % k)
                if fee[k] < 0:
                    raise LiveError('%s 不能为负' % k)
        if 'stamp' in fee and fee['stamp'] != 'auto':
            try:
                fee['stamp'] = float(fee['stamp'])
            except Exception:                               # noqa: BLE001
                raise LiveError('stamp 必须是数字或 "auto"')
        hit['fee'] = dict(hit.get('fee') or {}, **fee)
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


def add_fill(aid, trade_date, code, side, shares, price, fee=None,
             name='', source='manual', note='', reverse_of=None,
             fee_estimated=False):
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
    except Exception:                                       # noqa: BLE001
        raise LiveError('数量/价格必须是数字')
    # ★ fee=None（没填）与 fee=0（明确说没有费用）是**两件事**。
    #   没填就按引擎口径估一个并标 fee_estimated —— 默认 0 会让现金越算越多，
    #   而"多出来的钱"不会报错，只会让权益悄悄虚高（年换手 4.5 次的话，
    #   一年约 0.5% 的费用凭空消失）。
    if fee is None or fee == '':
        # 冲正不估费用 —— 见下面对负费用的说明。调用方应传 -原费用。
        # ★ 按【成交日】取费率，不是"当前费率" —— 补录三个月前那笔时，
        #   拿今天的费率去算，数字看着很正常，只是错的。
        fee = 0.0 if reverse_of else estimate_fee(
            side, shares, price, d, fee_model_at(aid, d))
        fee_estimated = not reverse_of
    else:
        try:
            fee = float(fee)
        except Exception:                                   # noqa: BLE001
            raise LiveError('费用必须是数字（不填则按引擎口径估算）')
    # ★ 只有【冲正】记录允许负费用。
    #   冲正的语义是"这笔交易没发生"，所以原记录的费用也要退掉：
    #     原买入   cash -= 11420 + 5
    #     冲正卖出 cash += 11420 - (-5)      -> 净影响 0，精确抵消
    #   若冲正记录照常估一笔费用（+10.71），净影响就是 -15.71 —— 一笔
    #   没发生的交易凭空吃掉两次费用，而且不报错。
    if fee < 0 and not reverse_of:
        raise LiveError('费用不能为负（只有冲正记录可以，用来退掉原费用）')
    if shares <= 0 or price <= 0:
        raise LiveError('数量与价格必须为正（撤销请用反向冲正，不要填负数）')
    if side == 'buy' and shares % LOT_SIZE:
        raise LiveError('买入数量必须是 %d 的整数倍，收到 %d' % (LOT_SIZE, shares))
    if '.' not in code:
        raise LiveError('代码要带市场后缀，如 601857.XSHG，收到 %r' % code)
    rec = {'uid': _uid(), 'ts': _now(), 'trade_date': d.isoformat(),
           'code': code, 'name': name, 'side': side, 'shares': shares,
           'price': price, 'fee': fee, 'source': source, 'note': note}
    if fee_estimated:
        rec['fee_estimated'] = True
    if reverse_of:
        rec['reverse_of'] = reverse_of
    # ★ 把新记录放进账本【重放一遍】再决定收不收 —— 不是只看当前持仓。
    #   这样冲正、补录历史日期都能正确判断（见 replay_violation 的注释）。
    v = replay_violation(fills(aid) + [rec])
    if v:
        c, vd, cur, want, bad = v
        if bad is rec:
            raise LiveError('这笔卖出会让 %s 在 %s 的持仓变负：当时只有 %d 股，'
                            '要卖 %d 股' % (c, vd, cur, want))
        raise LiveError(
            '加了这条之后，账本会在 %s 出现负持仓：%s 当时只有 %d 股，'
            '而那天有一笔卖 %d 股。\n'
            '—— 多半是你想冲正的这笔被【后面的记录】依赖了。'
            '先冲正那笔后续卖出，再冲正这一笔。'
            % (vd, c, cur, want))
    _append_jsonl(os.path.join(acct_dir(aid), 'fills.jsonl'), rec)
    return rec


# ============================ 费率模型 ============================
# ★ 只有【净佣金】是可谈的，其余全是法定、所有人一样、券商只是代收代缴。
#   所以法定部分有默认值 —— **不配也能用**，配置界面只需要问你两件事：
#     · 佣金率是多少
#     · 报的这个率是「净佣金」还是「已含规费」
#
#   法定费率（2026-09 现行）：
#     证管费  万0.2    证监会    双边
#     经手费  万0.341  交易所    双边
#     ---------------- 规费合计 万0.541
#     过户费  万0.1    中登      双边（沪深都收）
#     印花税  万5      税务      **仅卖出**（2023-08-28 起千1减半为万5）
#
# ★ `commission_incl_reg`（报价是否含规费）是**必须问清的一条**，因为两家
#   券商可能都说"万2.5 最低 5 元"，而实付差很多：
#     含规费口径：最低 5 元【覆盖】规费   -> 5 万成交付 13.00
#     规费另收  ：规费加在 5 元【之外】   -> 5 万成交付 15.71
#   实测用户账户是【含规费】口径：App 说万0.8，账单是佣金1.72 + 规费3.57，
#   而 66110 × 万0.8 = 5.2888 ≈ 5.29 —— 单看净佣金万0.26 与 App 对不上。
#
# ★ 怎么定"最低佣金含不含规费"：需要一笔【小额】成交（金额 < 2 万，最低会
#   binding）。看账单佣金那行是 5.00 且规费另列（另收口径），
#   还是 佣金+规费 = 5.00（含规费口径）。大额单测不出来。
REG_RATE = 0.0000541        # 规费 = 证管费万0.2 + 经手费万0.341
TRANSFER_RATE = 0.00001     # 过户费 万0.1

# 两种配置方式：
#   'parts'（默认）按项算：佣金 + 规费 + 过户费 + 印花税，法定项有默认值
#   'flat'   直接填【买入/卖出总费率】，**不再拆项**，填多少就是多少
# ★ flat 存在的理由：费率谈好之后，"买入万0.9 / 卖出万5.9"就是全部事实，
#   再拆成佣金/规费/过户费只是多几个能配错的地方（含规费 vs 净佣金那条
#   口径差就坑过一次）。想省事就用 flat。
FEE_MODES = ('parts', 'flat')
FEE_FIELDS = ('mode', 'buy_rate', 'sell_rate', 'flat_min',
              'commission', 'min_commission', 'commission_incl_reg',
              'regulatory', 'transfer', 'stamp')
# 默认取【常见零售报价】：万2.5 + 最低 5 元 + 规费另收。
# 刻意偏保守（估高不估低）：估低会让现金虚高，而"多出来的钱"不报错。
# 也正好与回测默认口径同量级，便于实盘与回测对照。
FEE_DEFAULT = {
    'mode': 'parts',
    # ---- flat 模式用的（默认值取常见零售口径，与 parts 默认同量级）----
    'buy_rate': 0.00031,          # 买入总费率 ≈ 万2.5 + 规费万0.541 + 过户费万0.1
    'sell_rate': 0.00081,         # 卖出 = 买入 + 印花税万5
    'flat_min': 5.0,              # 单笔最低（0 = 没有最低）
    # ---- parts 模式用的 ----
    'commission': 0.00025,        # 佣金率（可谈）
    'min_commission': 5.0,        # 单笔最低佣金（可谈；填 0 = 没有最低）
    'commission_incl_reg': False,  # 上面那个率是否已含规费
    # ---- 以下法定，一般不用配；留字段是为了政策变动时能改 ----
    'regulatory': REG_RATE,
    'transfer': TRANSFER_RATE,
    'stamp': 'auto',              # 'auto' = 按日期分段（复用 broker.Cost）
}
# 只有这三项需要用户填，其余留空即用法定默认
FEE_USER_FIELDS = ('commission', 'min_commission', 'commission_incl_reg')


def _merge_fee(raw):
    m = dict(FEE_DEFAULT)
    m.update({k: v for k, v in (raw or {}).items() if k in FEE_FIELDS})
    return m


def fee_rates(aid):
    """费率版本历史，按生效日升序，并**推导出结束日**。

    ★ 与策略版本、成交流水同一个模式：**append-only**。
      "新费率生效时原费率立刻结束"这件事【不靠改写上一条】实现 ——
      每条只记 `from`（生效日），`to` 由下一条的 from 减一天推出来。
      改写账本是这个模块从头到尾都在避免的事：一旦能改写就没法复盘
      "当时用的是哪个费率"。

    ★ 为什么费率必须按日期分版本：**补录一笔历史成交时，要用那笔成交
      当时生效的费率**。换过券商/谈过费率之后，拿今天的费率去算三个月前
      那笔，数字看着很正常，只是错的。
    """
    rows = sorted(_read_jsonl(os.path.join(acct_dir(aid), 'fee_rates.jsonl')),
                  key=lambda r: (r['from'], r['ts']))
    out = []
    for i, r in enumerate(rows):
        to = None
        if i + 1 < len(rows):
            to = (_d(rows[i + 1]['from']) - datetime.timedelta(days=1)).isoformat()
        # to < from 说明有一条【同日】的记录把它盖掉了（更正），
        # 有效区间零长度 —— 标成已作废，页面上照样列出来。
        dead = bool(to and _d(to) < _d(r['from']))
        # ★ 每档带上【总费率】—— 页面要显示的是总费率而不是佣金率：
        #   佣金只是其中一项，看佣金万0.26 完全说明不了实付万0.9。
        #   印花税按【该档生效日】算分段，不是今天。
        full = _merge_fee(r.get('fee'))
        out.append(dict(r, to=(None if dead else to),
                        active=(to is None), superseded=dead,
                        model=full,
                        rates=effective_rates(full, 100000.0, r['from'])))
    return out


def fee_model_at(aid, date):
    """`date` 那天生效的费率。没有任何配置、或早于第一条生效日 -> FEE_DEFAULT。

    ★ 早于第一条时【不往前延伸】—— 那是猜。用默认值（偏保守），
      并且这件事在页面上看得见（历史表第一行的生效日就是分界）。
    """
    d = _d(date)
    hit = None
    for r in fee_rates(aid):
        if _d(r['from']) <= d:
            hit = r
        else:
            break
    return _merge_fee((hit or {}).get('fee'))


def fee_model(acct):
    """兼容入口：账户【当前】生效的费率。"""
    aid = acct['id'] if isinstance(acct, dict) else acct
    rows = fee_rates(aid)
    if rows:
        return fee_model_at(aid, datetime.date.today())
    # 老数据：单份 acct['fee']，没有生效日
    return _merge_fee((acct.get('fee') or {}) if isinstance(acct, dict) else {})


def add_fee_rate(aid, from_date, model, note='', supersede=False):
    """新增一档费率。**只能追加，不能改写已有的记录。**

    两种追加：
      · 默认（费率变了）：生效日必须【严格晚于】上一档，上一档自动在前一天结束
      · `supersede=True`（**上一档填错了**）：允许与上一档【同一生效日】，
        追加一条把它盖掉。上一档的有效区间变成零长度，在历史里显示为"已作废"。

    ★ 为什么用"同日追加"而不是改写那条记录：改写就没法回答"当时用的是哪档"。
      同日追加之后，`fee_model_at` 取到的是**后写入的那条**（fee_rates 按
      (from, ts) 排序），而错的那条仍然看得见 —— 这正是账本该有的样子。

    ★ 但这条通路**只解决填错**，不解决"改历史费率"：如果已经按错的费率
      算过成交，那些成交的费用已经落在 fills 里了。要修得去流水页
      「冲正 + 重录」，让它按更正后的费率重算。
    """
    get_account(aid)
    try:
        d = _d(from_date)
    except Exception:                                       # noqa: BLE001
        raise LiveError('生效日格式应为 YYYY-MM-DD，收到 %r' % from_date)
    m = {k: v for k, v in (model or {}).items() if k in FEE_FIELDS}
    if not m:
        raise LiveError('没有可保存的费率字段')
    if m.get('mode') and m['mode'] not in FEE_MODES:
        raise LiveError('mode 只能是 %s' % '/'.join(FEE_MODES))
    for k in ('commission', 'min_commission', 'regulatory', 'transfer',
              'buy_rate', 'sell_rate', 'flat_min'):
        if k in m:
            try:
                m[k] = float(m[k])
            except Exception:                               # noqa: BLE001
                raise LiveError('%s 必须是数字' % k)
            if m[k] < 0:
                raise LiveError('%s 不能为负' % k)
    if 'stamp' in m and m['stamp'] != 'auto':
        try:
            m['stamp'] = float(m['stamp'])
        except Exception:                                   # noqa: BLE001
            raise LiveError('stamp 必须是数字或 "auto"')
    if 'commission_incl_reg' in m:
        m['commission_incl_reg'] = bool(m['commission_incl_reg'])
    cur = fee_rates(aid)
    if cur:
        last = _d(cur[-1]['from'])
        if d < last or (d == last and not supersede):
            raise LiveError(
                '生效日必须晚于上一档（%s）。费率是按时间线追加的：'
                '新的一档生效，上一档就在前一天结束。\n'
                '如果是【上一档填错了】，勾上「更正上一档」——'
                '那会用同一个生效日追加一条把它盖掉，错的那条仍然看得见。'
                % cur[-1]['from'])
    rec = {'ts': _now(), 'uid': _uid(), 'from': d.isoformat(),
           'fee': m, 'note': note or ''}
    path = os.path.join(acct_dir(aid), 'fee_rates.jsonl')
    if supersede:
        # ★ 被同日盖掉的那档【物理删除】，不留"已作废"的行。
        #   它的有效区间是零长度 —— 从来没有任何成交按它算过，
        #   留着只是噪声。见 _write_jsonl 的说明。
        keep = [r for r in _read_jsonl(path) if r.get('from') != d.isoformat()]
        n = len(_read_jsonl(path)) - len(keep)
        _write_jsonl(path, keep + [rec])
        rec['replaced'] = n
    else:
        _append_jsonl(path, rec)
    return dict(rec, to=None, active=True)


def migrate_fee(aid):
    """老数据迁移：acct['fee'] 单份配置 -> 费率历史的第一条。

    生效日取账户创建日 —— 那是它唯一可能生效的起点。
    """
    a = get_account(aid)
    if not a.get('fee') or fee_rates(aid):
        return None
    d = (a.get('created') or '2000-01-01')[:10]
    return add_fee_rate(aid, d, a['fee'], note='从单份配置迁移')


def _stamp_rate(m, d):
    v = m.get('stamp', 'auto')
    if v == 'auto':
        return Cost().close_tax_at(_d(d))
    return float(v or 0)


def fee_breakdown(side, shares, price, trade_date, model=None):
    """算一笔的费用明细。

    mode='flat'：`max(额 × 买/卖总费率, 最低)` —— **不拆项，填多少算多少**。
    mode='parts'（默认）：逐项拆开，见下。

        佣金部分 = max(额 × 佣金率, 最低佣金)
                   若报价【不含】规费，再加 额 × 规费率
        + 过户费 = 额 × 过户费率
        + 印花税 = 额 × 印花税率（仅卖出）

    ★ 印花税分段规则复用 `broker.Cost.close_tax_at`，不在这里重写 ——
      写两份的话，实盘现金与回测成本会在 2023-08-28 前后分叉。
    """
    m = model or dict(FEE_DEFAULT)
    amt = float(shares) * float(price)
    if m.get('mode') == 'flat':
        rate = float(m['sell_rate'] if side == 'sell' else m['buy_rate'])
        tot = max(amt * rate, float(m.get('flat_min') or 0))
        return {'mode': 'flat', 'amount': round(amt, 2), 'rate': rate,
                'commission': None, 'regulatory': None, 'transfer': None,
                'stamp': None, 'total': round(tot, 2)}
    comm = max(amt * float(m['commission']), float(m['min_commission'] or 0))
    reg = 0.0
    if not m.get('commission_incl_reg'):
        reg = amt * float(m.get('regulatory') or 0)
    trf = amt * float(m.get('transfer') or 0)
    stamp = amt * _stamp_rate(m, trade_date) if side == 'sell' else 0.0
    out = {'mode': 'parts', 'amount': round(amt, 2),
           'commission': round(comm, 2),
           'regulatory': round(reg, 2), 'transfer': round(trf, 2),
           'stamp': round(stamp, 2)}
    out['total'] = round(comm + reg + trf + stamp, 2)
    return out


def estimate_fee(side, shares, price, trade_date, model=None):
    """一笔成交的估算费用（分）。明细见 fee_breakdown。

    这是**估算**：券商是逐项截尾/进位后相加，可能差 ±0.01。
    对完账单用「冲正 + 重录」填实际值即可（入账已标 fee_estimated）。
    """
    return fee_breakdown(side, shares, price, trade_date, model)['total']


def effective_rates(model, amount=100000.0, trade_date=None):
    """把当前配置折成【买入/卖出总费率】。

    parts 模式下总费率随金额变（最低佣金 binding 时更高），所以要给金额。
    页面用它做两件事：显示"当前 ≈ 买入万X"，以及一键把它填进 flat 模式。
    """
    d = trade_date or datetime.date.today().isoformat()
    n = 100
    px = float(amount) / n
    b = fee_breakdown('buy', n, px, d, model)['total']
    s_ = fee_breakdown('sell', n, px, d, model)['total']
    return {'amount': float(amount),
            'buy_rate': round(b / float(amount), 8),
            'sell_rate': round(s_ / float(amount), 8),
            'buy_fee': b, 'sell_fee': s_}


def infer_fee_model(amount, commission, regulatory=0.0, transfer=0.0,
                    stamp=0.0, min_commission=0.0):
    """从一笔真实账单反推费率，并判断券商用的是哪种口径。

    ★ 判据：账单同时列了「佣金」与「规费」两行时，(佣金+规费)/金额 才是
      App 上那个"佣金费率"。所以返回 commission_incl_reg=True 并把两行
      之和折成率 —— 这样复算能与账单**精确一致**。
      若只给了佣金（没有规费行），按"规费另收"处理。

    ★ 这里定不出「最低佣金含不含规费」—— 那需要一笔【小额】成交
      （最低会 binding）。返回里带 need_small_bill 提醒。
    """
    amount = float(amount)
    if amount <= 0:
        raise LiveError('成交金额必须为正')
    commission = float(commission or 0)
    regulatory = float(regulatory or 0)
    incl = regulatory > 0
    rate = (commission + regulatory) / amount if incl else commission / amount
    out = {
        'commission': round(rate, 8),
        'min_commission': float(min_commission or 0),
        'commission_incl_reg': incl,
        'transfer': (round(float(transfer) / amount, 8) if transfer
                     else TRANSFER_RATE),
    }
    if stamp:
        out['stamp'] = round(float(stamp) / amount, 8)
    return out


def fifo_lots(rows):
    """成交流水 -> {code: [ {shares, date, price} ]}，FIFO 冲减。

    ★ 用 FIFO 而不是平均成本：引擎本身就是分批 FIFO（红利税按持有期分档、
      T+1 只锁当日买入那批），平均成本会让重建出来的持仓与引擎语义不一致。
    """
    book = {}
    # ★ 同一天同一秒的多笔要有确定顺序，否则 FIFO 批次不可复现。
    #   tiebreak 用【文件里的插入顺序】，**不能用 uid** —— uid 是随机 hex，
    #   拿它排序会让同秒的买卖顺序随机翻转（实测：卖排到买前面，
    #   于是重放报"持仓变负"，而账本本身没问题）。
    for _i, r in sorted(_indexed(active_fills(rows)),
                        key=lambda t: (t[1]['trade_date'], t[1]['ts'], t[0])):
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
    """截至 day（含）的真实持仓。重放 warmup 时每天都要用。

    ★ 先按日期切、再交给 fifo_lots（它内部会剔掉已冲正的成对记录）。
      顺序不能反 —— 冲正记录可能晚于 day，那时原记录仍然有效。
    """
    day = _d(day)
    return fifo_lots([r for r in rows if _d(r['trade_date']) <= day])


def cash_asof(init_cash, rows, day, flows=()):
    day = _d(day)
    v = float(init_cash or 0)
    for r in flows:
        if _d(r['date']) <= day:
            v += float(r.get('signed') or 0)
    for r in active_fills(rows):
        if _d(r['trade_date']) > day:
            continue
        amt = r['shares'] * r['price']
        v += (-amt if r['side'] == 'buy' else amt) - float(r.get('fee') or 0)
    return v


def _indexed(rows):
    """(插入序号, 记录)。序号来自 fills.jsonl 的行序 —— append-only 的账本里
    行序就是时间序，是唯一可靠的同秒 tiebreak。"""
    return list(enumerate(rows))


def active_fills(rows):
    """剔掉【已冲正的成对记录】—— 原记录和它的冲正记录都不算。

    ★ 为什么必须成对剔掉，而不是"让它们在账上互相抵消"：

      1. **FIFO 批次会被搞错。** 买 1000@10（08-10）、卖 500@12（08-20）、
         再冲正那笔卖出（买 500@12，08-20）。若照单全收，批次变成
         [500@10 建仓08-10, 500@12 建仓08-20] —— 而实际上什么都没发生，
         应该是 [1000@10 建仓08-10]。成本价和建仓日都错了，
         **而这两个值直接喂给止损判定和红利税档位**。
      2. **重放会出现假的负持仓。** 冲正 08-10 那笔买入时，反向记录也记在
         08-10，于是排序后它落在 08-20 那笔卖出【之前】，那一刻持仓是负的
         —— 尽管那笔卖出本身早已被冲正掉。

      3. 现金也顺带更稳：成对剔掉后，冲正记录的费用符号填错也不会影响现金。

    只有【股数一致】才视为成对冲正 —— 手工写的部分冲正不能整条剔掉。
    """
    key = lambda r: r.get('uid') or r['ts']
    by_id = {key(r): r for r in rows}
    dead = set()
    for r in rows:
        src = r.get('reverse_of')
        if not src:
            continue
        o = by_id.get(src)
        if o is not None and int(o['shares']) == int(r['shares']) \
                and o['side'] != r['side']:
            dead.add(src)
            dead.add(key(r))
    return [r for r in rows if key(r) not in dead]


def replay_violation(rows):
    """重放整个账本，返回第一个「持仓变负」的违规，没有则返回 None。

    ★ 为什么不能只看「当前持仓」（原实现就是这么做的，有两个洞）：

      1. **冲正被后面的记录锁死。** 买 1000（08-10）→ 卖 500（08-20）后想
         冲正那笔买入，反向记录是"卖 1000"，而当前只持 500 —— 于是校验
         把它拒了，那条错记录**永远改不了**。正确的判断是：冲正之后
         08-20 那笔卖出会无股可卖，所以要先冲正 08-20 那笔。
      2. **补录历史日期的卖出。** 只看当前持仓，一笔日期在所有买入【之前】
         的卖出会被放行，而重放时那一刻持仓是负的。

    返回 (code, date, 该时点持仓, 想卖的股数, 违规记录)。
    """
    held = {}
    # 排序键带 uid：同一天同一秒的多笔要有确定顺序，否则 FIFO 批次不可复现
    for _i, r in sorted(_indexed(active_fills(rows)),
                        key=lambda t: (t[1]['trade_date'], t[1]['ts'], t[0])):
        c = r['code']
        n = int(r['shares'])
        if r['side'] == 'buy':
            held[c] = held.get(c, 0) + n
        else:
            cur = held.get(c, 0)
            if n > cur:
                return (c, r['trade_date'], cur, n, r)
            held[c] = cur - n
    return None


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
    for r in active_fills(fills(aid)):
        if lim and _d(r['trade_date']) > lim:
            continue
        amt = r['shares'] * r['price']
        v += (-amt if r['side'] == 'buy' else amt) - float(r.get('fee') or 0)
    return v


# ============================ 权益与收益 ============================

def _last_px(feed, codes, day):
    """{code: (最新收盘, 那天的日期)}，按 <= day 取最近一条。
    停牌股拿最后已知价 —— 与 broker「按最后已知价挂账」一致。"""
    if not codes:
        return {}
    q = "','".join(codes)
    rows = feed.con.execute("""
        SELECT code, close_bfq, date FROM (
          SELECT jq_code AS code, close_bfq, date,
                 row_number() OVER (PARTITION BY jq_code ORDER BY date DESC) rn
          FROM read_parquet('%s/mart/panel_daily/panel_*.parquet')
          WHERE jq_code IN ('%s') AND date <= DATE '%s'
            AND date > DATE '%s' - INTERVAL 400 DAY
        ) WHERE rn = 1""" % (feed.root, q, day, day)).fetchall()
    return {r[0]: (r[1], r[2]) for r in rows}


def positions_valued(aid, datalake=None):
    """持仓 + 逐只估值 + 汇总。**页面主视图用的就是它。**

    ★ 价格不再取自"今天的信号" —— 信号只覆盖它当天关心的票，
      不在信号里的持仓就没有价格（原页面那一版正是这样，浮盈显示 —）。
      这里按最新数据日独立取价，覆盖全部持仓。

    浮盈只算【价差】，不含已收分红 —— 与引擎 trades 的 ret 口径一致
    （broker 里 entry_price 在除权时不缩，就是为了让 pnl 只反映价差）。
    """
    book = fifo_lots(fills(aid))
    money = cash(aid)
    out = {'items': [], 'cash': round(money, 2), 'market_value': 0.0,
            'pnl': 0.0, 'cost': 0.0, 'equity': round(money, 2), 'asof': None}
    if not book:
        return out
    feed = PanelFeed('2026-01-01', datetime.date.today().isoformat(),
                     root=datalake)
    day = feed.trading_days[-1]
    out['asof'] = day.isoformat()
    px = _last_px(feed, list(book), day)
    nm = _names(feed, list(book), day)
    mv = cst = 0.0
    for c, lots in sorted(book.items()):
        sh = sum(l['shares'] for l in lots)
        avg = sum(l['shares'] * l['price'] for l in lots) / sh
        p, pd_ = px.get(c, (None, None))
        v = (sh * p) if p else None
        pnl = (v - sh * avg) if v is not None else None
        out['items'].append({
            'code': c, 'name': nm.get(c, ''), 'shares': sh,
            'cost': round(avg, 4), 'price': (round(p, 3) if p else None),
            'px_date': (pd_.isoformat() if pd_ else None),
            'stale': bool(pd_ and pd_ != day),          # 停牌：价格不是当日的
            'value': (round(v, 2) if v is not None else None),
            'pnl': (round(pnl, 2) if pnl is not None else None),
            'pnl_pct': (round(p / avg - 1, 6) if p and avg else None),
            'entry': min(l['date'] for l in lots).isoformat(),
        })
        cst += sh * avg
        if v is not None:
            mv += v
    for it in out['items']:
        it['weight'] = (round(it['value'] / (mv + money), 6)
                        if it['value'] is not None and (mv + money) else None)
    out['market_value'] = round(mv, 2)
    out['cost'] = round(cst, 2)
    out['pnl'] = round(mv - cst, 2)
    out['pnl_pct'] = round(mv / cst - 1, 6) if cst else None
    out['equity'] = round(mv + money, 2)
    return out


def equity_curve(aid, datalake=None):
    """逐日权益曲线 + 收益统计。

    权益 = 现金(as-of) + 持仓按当日**不复权收盘价**估值。
    停牌当日无行情时按最后已知价挂账 —— 与引擎 broker 的做法一致。

    ★ 收益用**时间加权（TWR）**，不是 `期末/期初 - 1`。
      有入金出金时后者是错的：入金 5 万会让"收益"凭空变大，
      而那不是你赚的。TWR 在每个有外部现金流的日子把区间切开：
          r_t = (E_t − F_t) / E_{t−1} − 1        F_t = 当日净入金
      再连乘。这也是能和回测年化直接比的那个口径。

    ★ 已冲正的成对记录不参与（走 active_fills）—— 否则一笔"没发生"的交易
      会在曲线上留下一个凭空的台阶。
    """
    acct = get_account(aid)
    rows = active_fills(fills(aid))
    flows = cashflows(aid)
    init = float(acct.get('init_cash') or 0)
    if not rows and not flows:
        return {'dates': [], 'equity': [], 'stats': None,
                'note': '还没有成交或现金流水'}
    d0 = min([_d(r['trade_date']) for r in rows] +
             [_d(f['date']) for f in flows])
    feed = PanelFeed((d0 - datetime.timedelta(days=10)).isoformat(),
                     datetime.date.today().isoformat(), root=datalake)
    days = [d for d in feed.trading_days if d >= d0]
    if not days:
        return {'dates': [], 'equity': [], 'stats': None,
                'note': '首笔成交日晚于最新行情日'}
    codes = sorted({r['code'] for r in rows})
    px = {}
    if codes:
        q = "','".join(codes)
        for c, dd, p in feed.con.execute("""
            SELECT jq_code, date, close_bfq
            FROM read_parquet('%s/mart/panel_daily/panel_*.parquet')
            WHERE jq_code IN ('%s') AND date >= DATE '%s'
        """ % (feed.root, q, days[0])).fetchall():
            px[(c, dd)] = p
    last = {}
    flow_by_day = {}
    for f in flows:
        flow_by_day[_d(f['date'])] = flow_by_day.get(_d(f['date']), 0.0) \
            + float(f.get('signed') or 0)

    dates, eq, twr = [], [], []
    prev_e = None
    for d in days:
        book = lots_asof(rows, d)
        mv = 0.0
        for c, lots in book.items():
            p = px.get((c, d))
            if p is None:
                p = last.get(c)            # 停牌：按最后已知价挂账
            else:
                last[c] = p
            if p is None:
                continue
            mv += sum(l['shares'] for l in lots) * p
        e = cash_asof(init, rows, d, flows) + mv
        f = flow_by_day.get(d, 0.0)
        if prev_e is not None and prev_e > 0:
            twr.append((e - f) / prev_e - 1.0)
        prev_e = e
        dates.append(d.isoformat())
        eq.append(round(e, 2))

    cum = 1.0
    for r in twr:
        cum *= (1.0 + r)
    n = len(dates)
    peak, mdd = -1e18, 0.0
    for v in eq:
        peak = max(peak, v)
        if peak > 0:
            mdd = max(mdd, 1.0 - v / peak)
    yrs = n / 244.0
    stats = {
        'days': n,
        'equity_end': eq[-1] if eq else None,
        'twr': round(cum - 1.0, 6),
        'twr_annual': round(cum ** (1.0 / yrs) - 1.0, 6) if yrs > 0.08 else None,
        'max_drawdown': round(mdd, 6),
        'net_deposit': round(sum(flow_by_day.values()), 2),
        'init_cash': init,
    }
    return {'dates': dates, 'equity': eq, 'stats': stats, 'note': None}


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


UPCOMING_DAYS = 15          # 日历条显示几个交易日（约三周）
LOOKAHEAD_DAYS = 70         # ★ 找"下次调仓"要看得更远：月频策略（红利是
                            #   run_monthly monthday=1）的下一次可能在 20+
                            #   个交易日后，只看 15 天会返回"没有下次调仓"
                            #   —— 那比不显示更糟，看着像策略不调仓了。


def upcoming_rebalance(eng, feed, from_day, days, n=LOOKAHEAD_DAYS):
    """未来 n 个交易日里哪几天是调仓日。

    ★ 这件事【不需要数据】—— 调仓日由日历序号决定（run_monthly 的"月内第 k
      个交易日"、run_weekly 的"周内第 k 个交易日"），而日历已经有到 2030。
      所以可以提前很久告诉你"哪天要调仓"，只是**清单**得等 T-1 收盘。

    为什么要提前提示：调仓日当天早上才打开看板就已经晚了 —— 09:30 开盘调仓，
    而信号是前一晚算的。提前几天知道日期才好安排。

    返回 [{date, wday, is_rebal}]，只含 from_day 之后的交易日。
    """
    fut = [d for d in days if d > from_day][:n]
    out = []
    for d in fut:
        due = False
        for t, freq, func, wd, md, ev, off in eng._tasks:
            if freq in ('w', 'm') and eng._due(0, d, freq, wd, md, ev, off):
                due = True
                break
        out.append({'date': d.isoformat(), 'wday': d.isoweekday(),
                    'is_rebal': due})
    return out


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
        # --- 4) 未来调仓日：纯日历，可以提前很久算出来 ---
        cal_days = calendar_days()
        upcoming = upcoming_rebalance(eng, feed, t, cal_days)
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

    nxt_rb = next((x for x in upcoming if x['is_rebal']), None)
    if is_rebal:
        nxt_rb = {'date': t.isoformat(), 'wday': t.isoweekday(), 'is_rebal': True}
    days_until = None
    if nxt_rb:
        days_until = 0 if is_rebal else \
            (1 + next(i for i, x in enumerate(upcoming) if x['is_rebal']))

    fp = feed.fingerprint() if hasattr(feed, 'fingerprint') else {}
    return {
        'warnings': warn, 'calendar_source': cm.get('source'),
        # ★ 调仓日是【纯日历】的，所以提前算得出来；清单不是（要 T-1 数据）。
        #   页面上要把这两件事分清楚，别让人以为提前几天就能看到买什么。
        'upcoming': upcoming[:UPCOMING_DAYS],
        'next_rebalance': (nxt_rb or {}).get('date'),
        'days_until_rebalance': days_until,
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
