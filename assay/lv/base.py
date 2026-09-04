"""lv/base.py —— live 模块的地基：异常 / 常量 / 原子读写 / 账户 /
交易日历 / 代码归一化 / 读账本。

🔴 **`LIVE` 必须用 `base.LIVE` 属性访问**（本域内部直接读没问题，
  别的域和外部一律走属性）。selftest 靠 `lv.LIVE = 临时目录` 把写操作
  重定向掉、避免污染真实账本（那条纪律有断言强制）——
  `from .base import LIVE` 拿到的是**副本**，重定向会**静默失效**，
  表现是用例把数据写进 `live/` 真账本，而它不报错。
  ★ 好在 LIVE 的读取点（acct_dir / load_accounts / _save_accounts /
    交易日历）全在本域，所以门面把赋值转回这里就够了。"""
import datetime
import json
import os
import re
import uuid



# 🔴 `__file__` 现在在 `lv/` 里，比原来（assay/live.py）深一层 ——
#   HERE 必须**仍指 assay 包目录**，否则 ROOT 变成 `assay/assay`，
#   于是 `LIVE` 指到 `assay/assay/live`：**账户全读不到、写也写错地方**。
#   实测就是这么错的（拆 server.py 时同一个坑已经踩过一次）。
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

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

def fills(aid):
    return _read_jsonl(os.path.join(acct_dir(aid), 'fills.jsonl'))



REG_RATE = 0.0000541        # 规费 = 证管费万0.2 + 经手费万0.341

TRANSFER_RATE = 0.00001     # 过户费 万0.1

# 两种配置方式：
#   'parts'（默认）按项算：佣金 + 规费 + 过户费 + 印花税，法定项有默认值
#   'flat'   直接填【买入/卖出总费率】，**不再拆项**，填多少就是多少
# ★ flat 存在的理由：费率谈好之后，"买入万0.9 / 卖出万5.9"就是全部事实，
#   再拆成佣金/规费/过户费只是多几个能配错的地方（含规费 vs 净佣金那条
#   口径差就坑过一次）。想省事就用 flat。

FEE_MODES = ('parts', 'flat')

# ★ 过户费【另收还是已含在佣金里】，取决于券商，而且**分市场不一样**。
#   银河实测（四张账单逐笔对到分）：万0.86 最低5元 是个**打包价**，
#   里面已含经手费+证管费；深市连过户费也在里面，沪市的过户费另收。
#     深A 107,118 → 佣金 9.21 = 2.35净+3.65经手+2.13证管+1.08过户  实付 9.21
#     沪A 106,856 → 佣金 9.19 = 3.43净+3.64经手+2.12证管         实付 10.25（+1.06 过户）
#   折成全含费率：深A 万0.86 / 沪A 万0.96 —— 差一个过户费万0.10。
#   这件事必须建模而不能取个平均：同一个账户里两个市场的费率就是不同的，
#   取平均在两边都错，且**不报错**。

TRANSFER_EXTRA = {
    'both': '沪深都另收（常见）',
    'xshg': '只有沪市另收，深市已含在佣金里（银河）',
    'none': '沪深都已含在佣金里',
}
# 'xshg' 的"已含"集合刻意只写【深市】一个：账单原文是
# "过户费（沪A、股转A、北交所、行权）"，也就是除深A之外都另收。
# 这样写还顺带保守 —— 遇到没见过的后缀会按【另收】算（估高不估低）。

TRANSFER_INCLUDED = {'both': set(), 'xshg': {'XSHE'}, 'none': None}


FEE_FIELDS = ('mode', 'buy_rate', 'sell_rate', 'flat_min',
              'commission', 'min_commission', 'commission_incl_reg',
              'regulatory', 'transfer', 'transfer_extra', 'stamp')
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
    'transfer_extra': 'both',     # 过户费另收的范围，见 TRANSFER_EXTRA
    'stamp': 'auto',              # 'auto' = 按日期分段（复用 broker.Cost）
}
# 只有这三项需要用户填，其余留空即用法定默认

FEE_USER_FIELDS = ('commission', 'min_commission', 'commission_incl_reg')


# 六位数前缀 -> 市场。面板实测只有这四种前缀（2026-09-01：00/30 深、60/68 沪）。

_PREFIX_MK = {'60': 'XSHG', '68': 'XSHG', '90': 'XSHG',
              '00': 'XSHE', '30': 'XSHE', '20': 'XSHE'}
# 各家的市场标记 -> 聚宽后缀。券商/QMT 用 SH/SZ，通达信/面板 symbol 用
# 前置的 sh/sz，Yahoo 用 .SS。

_MK_TOKEN = {'SH': 'XSHG', 'SS': 'XSHG', 'XSHG': 'XSHG', 'SSE': 'XSHG',
             'SZ': 'XSHE', 'XSHE': 'XSHE', 'SZSE': 'XSHE'}

_RE_DIGITS = re.compile(r'\d{6}')

_RE_TOKEN = re.compile(r'[A-Z]+')



def normalize_code(code):
    """把各种写法的代码归一到【聚宽口径】`601857.XSHG` / `000001.XSHE`。

    认这些（大小写随意，分隔符 `.`/`-`/`_`/空格/没有 都行）：

        601857.XSHG   000001.XSHE     聚宽（本项目口径）
        601857.SH     000001.SZ       券商 / QMT / 迅投
        sh601857      sz000001        通达信 / 面板 symbol 列
        601857.SS                     Yahoo
        601857        000001          裸六位，按前缀判市场

    ★ 做法是【把数字和市场标记分别抽出来】，而不是枚举布局 —— 枚举的话
      每见到一种新写法就得改一次，而漏掉的那种表现为"取不到行情"。

    ★ 判不出市场就报错，**不瞎给一个**：市场决定过户费收不收，
      猜错每笔差万0.1、一年几百块，且从不报错。

    🔴 前缀与标记**冲突时拒绝**（如 `600000.SZ`）：A 股前缀与市场是一一
      对应的，冲突说明有一个是错的。放行等于用错市场算费用。
    """
    raw = str(code or '').strip()
    if not raw:
        raise LiveError('代码不能为空')
    c = raw.upper()
    dg = _RE_DIGITS.search(c)
    if not dg:
        raise LiveError('代码里找不到六位数字，收到 %r' % raw)
    num = dg.group(0)
    # 数字之外只允许是市场标记和分隔符
    rest = (c[:dg.start()] + c[dg.end():]).strip(' .-_')
    toks = [t for t in _RE_TOKEN.findall(rest) if t]
    mk = None
    if toks:
        if len(toks) > 1 or toks[0] not in _MK_TOKEN:
            raise LiveError(
                '认不出市场标记 %r。支持 SH/SS/XSHG（沪）、SZ/XSHE（深），'
                '写在前面或后面都行；也可以只写六位数字' % rest)
        mk = _MK_TOKEN[toks[0]]
    by_prefix = _PREFIX_MK.get(num[:2])
    if mk and by_prefix and mk != by_prefix:
        raise LiveError(
            '%r 自相矛盾：%s 开头的是%s股票，而标记写的是%s。'
            '有一个是错的 —— 不替你选，因为市场决定过户费收不收（万0.1）'
            % (raw, num[:2], '沪市' if by_prefix == 'XSHG' else '深市',
               '沪市' if mk == 'XSHG' else '深市'))
    mk = mk or by_prefix
    if not mk:
        raise LiveError(
            '从 %r 判不出是哪个市场 —— 请带上标记，如 %s.XSHG / %s.XSHE。\n'
            '（不替你猜：市场决定过户费收不收，猜错每笔差万0.1 且不报错）'
            % (raw, num, num))
    return '%s.%s' % (num, mk)



def market_of(code):
    """代码 -> 市场（`'XSHG'` / `'XSHE'`）。各种写法都认，见 normalize_code。

    ★ 这里也走归一化：算费用的入口不止 add_fill 一个，
      直接拿 `301126.SZ` 调 fee_breakdown 时若按原样取后缀，
      会得到 'SZ'、判成"沪市另收"，每笔多算万0.1 且不报错。
    """
    try:
        return normalize_code(code).rsplit('.', 1)[1]
    except LiveError:
        return (str(code).rsplit('.', 1)[-1] or '').upper()



def transfer_is_extra(model, code):
    """这笔的过户费是【另收】还是【已含在佣金里】。

    ★ 需要 code 才能判 —— 所以 fee_breakdown / estimate_fee 都要带上代码。
      设成分市场却不给代码时**直接报错**，不替它猜一个方向：
      猜错的表现是费用差万0.10，一年下来几百块，而且从不报错。
    """
    inc = TRANSFER_INCLUDED.get((model or {}).get('transfer_extra') or 'both',
                                set())
    if inc is None:                                 # 'none'：都已含
        return False
    if not inc:                                     # 'both'：都另收
        return True
    if not code:
        raise LiveError(
            '这档费率设的是「%s」，算费用时必须知道是哪个市场 —— 请带上代码'
            % TRANSFER_EXTRA.get(model.get('transfer_extra'),
                                 model.get('transfer_extra')))
    return market_of(code) not in inc



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
