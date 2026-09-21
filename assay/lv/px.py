"""lv/px.py —— 取价与名称：不复权价 / 当日高低 / 成交价校验 /
信号新鲜度 / 代码->名称。"""
import datetime
import json
import os
import re
from ..feed import PanelFeed

from . import base as _base
from . import tdx as _tdx


from assay import symbols as _SYM   # 「代码->类别/名称」的唯一正本
from assay import paths as _paths   # datalake 根的唯一解析


def _lake(root=None):
    """datalake 根目录。与 PanelFeed 同一套解析规则（含 ASSAY_DATALAKE）。"""
    # 🔴 **不在这里数 dirname**：层数跟着"文件放在哪"变，搬一次就要改一次，
    #   而改漏了不报错（拆 srv/ 时踩过）。`paths.py` 位置固定，只数那一处。
    r = _paths.datalake(root)
    if not os.path.isdir(r):
        raise _base.LiveError('找不到 datalake：%s（用 ASSAY_DATALAKE 指定）' % r)
    return r



PRICE_FIELDS = {'open': '开盘价', 'close': '收盘价'}



def day_price(code, date, which='open', datalake=None):
    """取某只票某天的【不复权】价。`which` = 'open' / 'close'。

    ★ 为什么 open 正好是"竞价买入"的成交价：A 股开盘价就是 09:15-09:25
      集合竞价的成交价。挂在竞价里成交，成交价必然是它。

    ★ 拿不到就**响亮报错**，不退回"最近一个交易日"—— 那会静默给出错的价格。
      最常见的情形是【当天行情还没同步】：调仓日早上下单，而 datalake 要等
      当晚 sync_daily.sh 跑完才有当天的行情。那时候只能手填价格，或等晚上再录。
    """
    d = _base._d(date)
    # ★ 不走 PanelFeed：它会把整个区间物化成 bars 表（取一个价付不起），
    #   而且 start/end 传 None 会拼成 `DATE 'None'`（踩过的坑）。
    # 🔴 取价走唯一正本 `symbols.day_px`（面板优先 + ETF/指数回落）——
    #   此前这个「查面板 -> 缺的回落 tdx」的模式在四个函数里各写了一遍。
    # ★ 正本**不 round 也不报错**：它只回答"取不到吗"，
    #   而"该怎么跟人解释"（是不是没同步 / 停牌 / 代码写错）留在下面。
    # ⚠ 两条路的精度**本来就不一样**（面板 3 位 / tdx 4 位），
    #   重构阶段逐位保真，不在这里顺手统一。
    _v, _src = _SYM.day_px(_lake(datalake), code, d, which)
    if _v is not None:
        return round(_v, 3 if _src == 'panel' else 4)
    # ★ "本地最新数据日"要独立查面板 —— 这一行是给人判断"是不是没同步"
    #   的唯一依据，绝不能出现 None。查面板同样走正本（`panel_probe`）——
    #   在这里再拼一条 SQL 就是第二份「怎么查面板」（守卫钉着）。
    last, _has = _SYM.panel_probe(_lake(datalake), code, d)
    what = PRICE_FIELDS.get(which, which)
    # 🔴 报错必须指向【真正的】原因。这天的面板明明在（last >= d）却说
    #   "还没同步"是自相矛盾的，而人会照着这句去等晚上重试 —— 白等。
    #   实测踩过：粘的是 301126.SZ（券商写法），报的却是"行情还没同步"。
    if last is not None and last >= d:
        has = _has
        raise _base.LiveError(
            '取不到 %s 在 %s 的%s —— 但这天的行情本地是有的（最新到 %s），'
            '所以【不是】没同步。\n%s'
            % (code, d, what, last,
               ('这只票当天没有成交（停牌），也就没有%s。请手填价格。' % what)
               if has else
               ('面板里没有 %s 这个代码：可能当天还没上市 / 已退市，'
                '或者代码写错了。' % code)))
    raise _base.LiveError(
        '取不到 %s 在 %s 的%s。\n'
        '最常见的原因是【当天行情还没同步】—— 本地最新数据日是 %s，'
        '而 datalake 要等当晚 sync_daily.sh 跑完才有当天行情。\n'
        '现在就要录的话请**手填价格**；或者等晚上同步完再录，'
        '那时价格留空就会自动取%s。\n'
        '另一种可能：这只票当天停牌（没有成交，也就没有%s）。'
        % (code, d, what, last, what, what))



ROUNDINGS = ('round', 'floor', 'ceil')

# ★ 这里曾经有个 fee_recheck：拿手填的费用反推成交金额，用来查"价格是不是
#   填错了"。**已删除，因为前提是错的。** 2026-09-01 那批 11 笔的实测：
#     成交金额  37,455 -> 过户费 0.37     38,144 -> 过户费 0.37
#               37,824 -> 过户费 0.38     38,200 -> 过户费 0.39
#   按金额**不单调** —— 38,144 的过户费比 37,824 的还低。所以过户费不是
#   "汇总成交金额"的函数：券商是按【分笔成交明细】逐笔舍入后相加，
#   一张委托在竞价里拆成几笔就舍几次（3200 股拆成 100+3100 逐笔截尾就掉到
#   0.37）。我们手里只有汇总、没有分笔，误差上界是 0.01×分笔数，未知。
#   于是这个检查分不清"价格错了"和"拆笔多了"，只会报假警 ——
#   而假告警看多了就不看告警了，比没告警更糟。
#   能证的是下面这条：成交价必须落在当日 high/low 区间内。


# 待办"到警示时间"的判据。★ 只在这一处定义 —— 账户列表的红点、
# 待办要不要默认展开，都读它。前端硬编码过一次判据（把 tdx 日历误报成
# 不权威），每次打开都弹假告警；判据分两处写就一定会分叉。

ALERT_SOON_DAYS = 2



def signal_alert(sig):
    """这份信号要不要"提请注意"。返回 (alert, 原因列表)。

    三种要动手的情形：
      · 今天就是调仓日
      · 有卖出（止损 / 炸板离场）—— 与调仓日无关，随时可能出现
      · 调仓日在 ALERT_SOON_DAYS 个交易日内 —— 当天早上才看已经晚了

    没这些的话待办里其实什么都没有，默认收起来；一到警示就自己打开。
    """
    if not sig or sig.get('error'):
        return False, []
    why = []
    if sig.get('is_rebalance_day'):
        why.append('今天是调仓日')
    n_s, n_b = len(sig.get('sell') or []), len(sig.get('buy') or [])
    if n_s:
        why.append('%d 只要卖出' % n_s)
    if n_b and not sig.get('is_rebalance_day'):
        why.append('%d 只要买入' % n_b)
    du = sig.get('days_until_rebalance')
    if not sig.get('is_rebalance_day') and du is not None \
            and 0 < du <= ALERT_SOON_DAYS:
        why.append('还有 %d 个交易日就调仓' % du)
    return bool(why), why



def latest_signal(aid):
    """盘上最新那份信号（不重算）。账户列表要拿它判红点，不能触发重放。"""
    d = os.path.join(_base.acct_dir(aid), 'signals')
    if not os.path.isdir(d):
        return None
    names = sorted(x for x in os.listdir(d) if x.endswith('.json'))
    if not names:
        return None
    try:
        with open(os.path.join(d, names[-1]), encoding='utf-8') as fh:
            return json.load(fh)
    except Exception:                                       # noqa: BLE001
        return None



def latest_data_day(datalake=None):
    """本地行情最新到哪天。取不到返回 None。

    ★ 单独一个函数、页面上单独一个位置 —— 这是判断"信号新不新"的第一依据。
      原来它挤在"（用 2026-09-01 收盘数据算，版本 xxx）"的括号里，
      看着像个脚注，而它恰恰是最该先看的那个数。
    """
    d = _SYM.panel_last_day(_lake(datalake))
    return d.isoformat() if d else None



def daily_close_map(feed, codes, since):
    """{(code, date): 不复权收盘} —— 权益曲线与每日持仓**共用这一处**。

    🔴 **只转发**给唯一正本 `symbols.daily_close_map`（2026-09-21）。
      它本来就是为了消掉 `perf.equity_curve` 与 `hist._feed` 那两份逐字
      相同的实现而抽出来的；现在连「面板 + tdx 回落」这一层也收进正本。
    """
    return _SYM.daily_close_map(feed.root, codes, since, con=feed.con)


def names_of(codes, day=None, datalake=None):
    """代码 -> 名称。批量粘贴的成交没有名称，流水页要靠这个补。

    🔴 **实现已收到唯一正本** `assay/symbols.names`（2026-09-21）——
      此前「代码->名称」有五份实现、三种行为（`stock._names_map` 不回落、
      `watchlist`/`alerts` 只查股票面板、清洗只有两处做了）。本函数保留
      名字与签名（调用方一个都不用改），只转发。
    ★ 正本的规矩没变：取 <= day 的最后一个非空 `sec_name`（400 天内），
      面板查不到的回落到 ETF/指数名称快照，统一清洗 U+FFFD。
    """
    codes = sorted({_base.normalize_code(c) for c in (codes or [])})
    if not codes:
        return {}
    d = _base._d(day) if day else (latest_data_day(datalake) or
                             datetime.date.today().isoformat())
    return _SYM.names(codes, day=d, root=_lake(datalake))


def day_range(code, date, datalake=None):
    """当日 (low, high)（不复权）。取不到返回 None —— 不报错，这是个可选校验。

    🔴 **只转发**给唯一正本 `symbols.day_hl`。不回落的话这道校验对 ETF
      **静默放行**：小数点点错、误填后复权价全都拦不住，而它一声不吭。
    """
    return _SYM.day_hl(_lake(datalake), _base.normalize_code(code),
                       _base._d(date))



def check_price_in_range(code, date, price, datalake=None):
    """成交价必须落在当日高低区间内 —— 落在外面是**可证的**错。

    ★ 与"用费用反推价格"不同，这条没有舍入的模糊性：一笔真实成交的价格
      不可能高于当日最高、低于当日最低。所以敢直接拒。
      能挡住的是真正会造成损失的那类错：小数点点错（11.92 填成 1.192）、
      看错行填了别只票的价、误填了**后复权**价（老股能差几十倍）。
      而成交价一错，成本价就错 —— 它要喂给止损判定和红利税档位。

    ★ 拿不到当日行情就跳过（返回 None）：这是可选校验，不能因为行情没同步
      就不让人录成交。
    """
    rg = day_range(code, date, datalake)
    if rg is None:
        return None
    lo, hi = rg
    # 留一点浮点余量。真正的错至少差一个数量级，不靠这点余量。
    if lo * 0.999 <= price <= hi * 1.001:
        return rg
    raise _base.LiveError(
        '%s 在 %s 的价格区间是 %.3f ~ %.3f，而你填的是 %.3f —— 落在区间外，'
        '不可能是这天的成交价。\n'
        '常见原因：小数点点错、看错行填了别只票的价、或者填了**后复权**价。\n'
        '（成交价一错，成本价就错，而成本价要喂给止损判定和红利税档位。）\n'
        '确实是这个价（如大宗交易），录入时勾上「按填的价，不校验」。'
        % (_base.normalize_code(code), _base._d(date), lo, hi, price))
