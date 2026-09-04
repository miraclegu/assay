"""lv/px.py —— 取价与名称：不复权价 / 当日高低 / 成交价校验 /
信号新鲜度 / 代码->名称。"""
import datetime
import json
import os
import re
import duckdb
from ..feed import PanelFeed

from . import base as _base


def _lake(root=None):
    """datalake 根目录。与 PanelFeed 同一套解析规则（含 ASSAY_DATALAKE）。"""
    r = root or os.environ.get('ASSAY_DATALAKE') or os.path.join(
        # 🔴 `__file__` 在 `lv/` 里比原来深一层，所以要多剥一层 dirname：
        #   assay/assay/lv/px.py -> assay/assay -> assay -> finacial -> datalake
        os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))), '..', 'datalake')
    r = os.path.normpath(r)
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
    col = 'open' if which == 'open' else 'close_bfq'
    # ★ 不走 PanelFeed：它会把整个区间物化成 bars 表（取一个价付不起），
    #   而且 start/end 传 None 会拼成 `DATE 'None'`（踩过的坑）。
    #   这里只要一行，直接对 parquet 点查。
    panel = "read_parquet('%s/mart/panel_daily/panel_*.parquet')" % _lake(datalake)
    con = duckdb.connect(':memory:')
    row = con.execute(
        "SELECT %s FROM %s WHERE jq_code = ? AND date = DATE '%s'"
        % (col, panel, d), [code]).fetchone()
    if row is None or row[0] is None:
        # ★ "本地最新数据日"要独立查面板 —— 这一行是给人判断"是不是没同步"
        #   的唯一依据，绝不能出现 None。
        mx = con.execute('SELECT MAX(date) FROM %s' % panel).fetchone()
        last = mx[0] if mx else None
        what = PRICE_FIELDS.get(which, which)
        # 🔴 报错必须指向【真正的】原因。这天的面板明明在（last >= d）却说
        #   "还没同步"是自相矛盾的，而人会照着这句去等晚上重试 —— 白等。
        #   实测踩过：粘的是 301126.SZ（券商写法），报的却是"行情还没同步"。
        if last is not None and last >= d:
            has = con.execute(
                "SELECT count(*) FROM %s WHERE date = DATE '%s' AND jq_code = ?"
                % (panel, d), [code]).fetchone()[0]
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
    return round(float(row[0]), 3)



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
    panel = "read_parquet('%s/mart/panel_daily/panel_*.parquet')" % _lake(datalake)
    row = duckdb.connect(':memory:').execute(
        'SELECT MAX(date) FROM %s' % panel).fetchone()
    return row[0].isoformat() if row and row[0] else None



def names_of(codes, day=None, datalake=None):
    """代码 -> 名称。批量粘贴的成交没有名称，流水页要靠这个补。

    ★ 取 <= day 的最后一个非空 sec_name（400 天内）：退市/改名的票也能显示，
      而不是留个空白让人对着代码猜。
    """
    codes = sorted({_base.normalize_code(c) for c in (codes or [])})
    if not codes:
        return {}
    d = _base._d(day) if day else (latest_data_day(datalake) or
                             datetime.date.today().isoformat())
    panel = "read_parquet('%s/mart/panel_daily/panel_*.parquet')" % _lake(datalake)
    rows = duckdb.connect(':memory:').execute("""
        SELECT code, sec_name FROM (
          SELECT jq_code AS code, sec_name,
                 row_number() OVER (PARTITION BY jq_code ORDER BY date DESC) rn
          FROM %s
          WHERE jq_code IN ('%s') AND date <= DATE '%s'
            AND date > DATE '%s' - INTERVAL 400 DAY
        ) WHERE rn = 1""" % (panel, "','".join(codes), d, d)).fetchall()
    return {c: n for c, n in rows if n}



def day_range(code, date, datalake=None):
    """当日 high/low（不复权）。取不到返回 None —— 不报错，这是个可选校验。"""
    d = _base._d(date)
    panel = "read_parquet('%s/mart/panel_daily/panel_*.parquet')" % _lake(datalake)
    row = duckdb.connect(':memory:').execute(
        "SELECT low, high FROM %s WHERE jq_code = ? AND date = DATE '%s'"
        % (panel, d), [_base.normalize_code(code)]).fetchone()
    if not row or row[0] is None or row[1] is None:
        return None
    return (round(float(row[0]), 3), round(float(row[1]), 3))



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
