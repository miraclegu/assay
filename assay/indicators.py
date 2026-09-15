# -*- coding: utf-8 -*-
"""assay/indicators.py —— 技术指标的【唯一正本】。

原来 MACD/KDJ/RSI/BOLL 是写死在 `stock.indicators()` 里的一段流水账，
而前端 `kchart.js` 又硬编码了一份 `keys`（哪几条线）与配色。于是

    加一个指标要改【三处】：算的地方、接口、画的地方
    而漏掉画的那处不报错 —— 只是选了它副图一片空白

现在一个指标 = 一条 `Spec`：**参数、算法、画哪几条线、什么样式、什么颜色、
预热多少根**全在一处声明，接口把它原样给前端（`defs()`），
**页面不认识任何一个指标名**（同「指标的中文标签/量纲在服务端」那条）。

## 🔴 指标一律在【服务端】算

与均线同一个理由：前端自己算就得多取预热数据，而"少取了导致头部值是错的"
**不报错**，只是曲线不对。每个 Spec 自报 `warm`（要多少根预热），
`compute()` 多取这么多根再切掉。

## 🔴 口径跟着行情软件走，不跟着教科书走

- KDJ 用**通用平滑**（K = 2/3·前K + 1/3·RSV），不是 SMA(3)
- BOLL 的 σ 用**总体标准差**（除 N），不是样本标准差
- ATR / RSI 用 **Wilder 平滑**（1/N 递推），不是简单均值
- CCI 的分母用**平均绝对偏差**，不是标准差

对不上券商软件的话，人会以为是**数据错了**，而不会想到是口径不同。

## 买点条件（`SIGNALS`）：指标 + 一个阈值，**现算出一个触发价**

「距离 20 日线 −3%」「MA5/MA20 金叉」这类条件，答案不该是一句"还差
2.3%" —— 那个百分比会骗人：MA5 与 MA20 **两条都在动**，光看今天的差值
推不出"涨到多少就金叉"。

所以这里把它**反解成价格**：`trigger_price()` 用二分法求
「今天收在什么价位，这个条件刚好成立」。于是指标买点与价格买点
**落在同一个口径上**（都是一个目标价），页面上那套「到价 / 接近 /
还要跌 x%」一行都不用改。

★ 二分法而不是解析式：解析式每加一个指标就要重推一次（MA 好推、
  BOLL 与 RSI 就很难），而二分只要求**单调**——而且单调性是**当场验**的
  （两端取值必须夹住阈值），不成立就明说"今天到不了"，不猜一个数。
🔴 反解时必须把当天的 **high/low 一起改**：价格跌到触发价，那天的最低价
  至少是它。只改 close 的话 KDJ/WR/ATR 这些用到 high/low 的指标会算出
  一个**今天根本不可能出现**的值，而它不报错。
"""
import math


class IndError(Exception):
    pass


# 副图曲线的配色：与主图均线（MA_COLOR）刻意不同族 —— 副图是另一套坐标，
# 用同样的颜色会让人以为"这条线和上面那条是一回事"。
PAL = ['#e0a33c', '#5b9cf0', '#a06bf0', '#2fd6a8', '#ff8fb1']


def _r3(x):
    return None if x is None else round(x, 3)


def _sma_seed_ema(xs, n):
    """EMA，第一个值用 SMA 起步（不是直接拿首值），少一点起步偏差。"""
    out = [None] * len(xs)
    if len(xs) < n or n <= 0:
        return out
    s = sum(xs[:n]) / n
    out[n - 1] = s
    k = 2.0 / (n + 1)
    for i in range(n, len(xs)):
        s = xs[i] * k + s * (1 - k)
        out[i] = s
    return out


def _wilder(xs, n):
    """Wilder 平滑：首值取前 n 个的均值，之后 (prev*(n-1)+x)/n。

    ATR 与 RSI 都用它。用简单移动平均的话数值能差 10% 以上，
    而两个都是"看着像个正常数字"的错。
    """
    out = [None] * len(xs)
    got = [i for i, x in enumerate(xs) if x is not None]
    if len(got) < n or n <= 0:
        return out
    st = got[n - 1]
    s = sum(xs[i] for i in got[:n]) / n
    out[st] = s
    for i in range(st + 1, len(xs)):
        if xs[i] is None:
            out[i] = s
            continue
        s = (s * (n - 1) + xs[i]) / n
        out[i] = s
    return out


def _ma(xs, n):
    out = [None] * len(xs)
    if n <= 0:
        return out
    s = 0.0
    for i, x in enumerate(xs):
        s += x
        if i >= n:
            s -= xs[i - n]
        if i >= n - 1:
            out[i] = s / n
    return out


# ============================== 指标定义 ==============================
class Spec(object):
    """一个指标。

    `params`  [{key,label,default,min,max}]  —— 页面照这个渲染输入框
    `series(p)` -> [{key,label,style,color}] —— 画哪几条、什么样式
                   style: line（折线）/ bar（柱，涨跌配色）/ zero（画一条 0 轴）
    `calc(bars, p)` -> {key: [值]}           —— 可以比 series 多给几个键
                       （买点条件会用到不画出来的那些，比如 BOLL 的 lbd）
    `warm(p)` -> int                          —— 要多少根预热
    """

    def __init__(self, id, label, panel, params, series, calc, warm,
                 unit='', desc='', short=None):
        self.id, self.label, self.panel = id, label, panel
        # ★ 短名给按钮用。**服务端给**而不是让页面去 split 标签 ——
        #   "均线差（金叉）"按空格切出来是整串，而 "ATR 真实波幅" 切出来是
        #   ATR：同一套规则两种结果，页面不该猜这种事。
        self.short = short or label.split(' ')[0]
        self.params, self._series, self._calc, self._warm = params, series, calc, warm
        self.unit, self.desc = unit, desc

    def merge(self, p):
        """把用户给的参数并进默认值，并做范围校验。

        🔴 越界**报错不夹逼** —— 悄悄改成边界值的话，页面上写着 200
          而画的是 60，两个数都看着正常。
        """
        out = {}
        for d in self.params:
            v = (p or {}).get(d['key'], d['default'])
            try:
                v = int(v)
            except Exception:                               # noqa: BLE001
                raise IndError('%s 的参数 %s 要是整数，收到 %r'
                               % (self.label, d['label'], v))
            if v != 0 and not (d['min'] <= v <= d['max']):
                raise IndError('%s 的参数 %s 要在 %d~%d 之间（0 = 不算），收到 %d'
                               % (self.label, d['label'], d['min'], d['max'], v))
            out[d['key']] = v
        return out

    def series(self, p=None):
        return self._series(self.merge(p))

    def warm(self, p=None):
        return self._warm(self.merge(p))

    def calc(self, bars, p=None):
        return self._calc(bars, self.merge(p))


def _P(key, label, default, lo=1, hi=500):
    return {'key': key, 'label': label, 'default': default, 'min': lo, 'max': hi}


def _S(key, label, style='line', color=None, i=0):
    return {'key': key, 'label': label, 'style': style,
            'color': color or PAL[i % len(PAL)]}


# ---------------------------------------------------------------- MA（主图）
def _ma_series(p):
    return [_S('ma%d' % p[k], 'MA%d' % p[k], 'line', None, i)
            for i, k in enumerate(('n1', 'n2', 'n3', 'n4')) if p[k]]


def _ma_calc(bars, p):
    cl = [b['close'] for b in bars]
    out = {}
    for k in ('n1', 'n2', 'n3', 'n4'):
        if p[k]:
            out['ma%d' % p[k]] = [_r3(x) for x in _ma(cl, p[k])]
    return out


# -------------------------------------------------------------- BOLL（主图）
def _boll_calc(bars, p):
    cl = [b['close'] for b in bars]
    n, k = p['n'], p['k']
    mb, ub, lb, lbd, ubd = [], [], [], [], []
    for i in range(len(cl)):
        if i < n - 1:
            mb.append(None); ub.append(None); lb.append(None)
            lbd.append(None); ubd.append(None)
            continue
        seg = cl[i - n + 1:i + 1]
        mu = sum(seg) / n
        sd = (sum((x - mu) ** 2 for x in seg) / n) ** 0.5   # 总体标准差
        u, l = mu + k * sd, mu - k * sd
        mb.append(round(mu, 3)); ub.append(round(u, 3)); lb.append(round(l, 3))
        # 距上/下轨（%）—— 买点条件用它，不画出来
        lbd.append(round((cl[i] / l - 1) * 100, 3) if l else None)
        ubd.append(round((cl[i] / u - 1) * 100, 3) if u else None)
    return {'mb': mb, 'ub': ub, 'lb': lb, 'lbd': lbd, 'ubd': ubd}


# -------------------------------------------------------------------- MACD
def _macd_calc(bars, p):
    cl = [b['close'] for b in bars]
    m = len(cl)
    ef, es = _sma_seed_ema(cl, p['fast']), _sma_seed_ema(cl, p['slow'])
    dif = [(ef[i] - es[i]) if (ef[i] is not None and es[i] is not None) else None
           for i in range(m)]
    got = [x for x in dif if x is not None]
    dea_raw = _sma_seed_ema(got, p['signal'])
    off = m - len(got)
    dea = [(dea_raw[i - off] if i >= off else None) for i in range(m)]
    return {'dif': [_r3(x) for x in dif], 'dea': [_r3(x) for x in dea],
            'macd': [_r3((dif[i] - dea[i]) * 2)
                     if (dif[i] is not None and dea[i] is not None) else None
                     for i in range(m)]}


# --------------------------------------------------------------------- KDJ
def _kdj_calc(bars, p):
    n, a1, a2 = p['n'], p['k'], p['d']
    cl = [b['close'] for b in bars]
    hi = [b['high'] for b in bars]
    lo = [b['low'] for b in bars]
    kk = dd = 50.0
    K, D, J = [], [], []
    for i in range(len(bars)):
        if i < n - 1:
            K.append(None); D.append(None); J.append(None)
            continue
        h = max(x for x in hi[i - n + 1:i + 1] if x is not None)
        l_ = min(x for x in lo[i - n + 1:i + 1] if x is not None)
        rsv = 50.0 if h == l_ else (cl[i] - l_) / (h - l_) * 100
        kk = ((a1 - 1) * kk + rsv) / a1
        dd = ((a2 - 1) * dd + kk) / a2
        K.append(round(kk, 2)); D.append(round(dd, 2))
        J.append(round(3 * kk - 2 * dd, 2))
    return {'k': K, 'd': D, 'jj': J}


# --------------------------------------------------------------------- RSI
def _rsi_one(cl, w):
    """与原实现逐位一致（Wilder，首值那一段的写法刻意保留）。"""
    out = [None] * len(cl)
    up = dn = 0.0
    for i in range(len(cl)):
        if i == 0:
            continue
        ch = cl[i] - cl[i - 1]
        u, v = max(ch, 0.0), max(-ch, 0.0)
        if i <= w:
            up += u / w
            dn += v / w
            out[i] = (round(up / (up + dn) * 100, 2)
                      if i == w and (up + dn) else None)
        else:
            up = (up * (w - 1) + u) / w
            dn = (dn * (w - 1) + v) / w
            out[i] = round(up / (up + dn) * 100, 2) if (up + dn) else None
    return out


def _rsi_calc(bars, p):
    cl = [b['close'] for b in bars]
    out = {}
    for k in ('n1', 'n2', 'n3'):
        if p[k]:
            out['rsi%d' % p[k]] = _rsi_one(cl, p[k])
    return out


# --------------------------------------------------------------------- ATR
def _tr(bars):
    out = [None] * len(bars)
    for i, b in enumerate(bars):
        if i == 0:
            out[i] = b['high'] - b['low']
            continue
        pc = bars[i - 1]['close']
        out[i] = max(b['high'] - b['low'], abs(b['high'] - pc), abs(b['low'] - pc))
    return out


def _atr_calc(bars, p):
    n = p['n']
    a = _wilder(_tr(bars), n)
    return {'atr': [_r3(x) for x in a],
            # 波动率（ATR 占股价的比例，%）—— 跨股票可比，买点条件用它
            'atrp': [(_r3(a[i] / bars[i]['close'] * 100)
                      if a[i] is not None and bars[i]['close'] else None)
                     for i in range(len(bars))]}


# --------------------------------------------------------------------- CCI
def _cci_calc(bars, p):
    n = p['n']
    tp = [(b['high'] + b['low'] + b['close']) / 3.0 for b in bars]
    ma = _ma(tp, n)
    out = [None] * len(bars)
    for i in range(len(bars)):
        if ma[i] is None:
            continue
        seg = tp[i - n + 1:i + 1]
        md = sum(abs(x - ma[i]) for x in seg) / n      # 平均绝对偏差
        out[i] = round((tp[i] - ma[i]) / (0.015 * md), 2) if md else None
    return {'cci': out}


# ---------------------------------------------------------------------- WR
def _wr_calc(bars, p):
    out = {}
    for key in ('n1', 'n2'):
        n = p[key]
        if not n:
            continue
        col = [None] * len(bars)
        for i in range(len(bars)):
            if i < n - 1:
                continue
            h = max(b['high'] for b in bars[i - n + 1:i + 1])
            l_ = min(b['low'] for b in bars[i - n + 1:i + 1])
            col[i] = round((h - bars[i]['close']) / (h - l_) * 100, 2) if h > l_ else None
        out['wr%d' % n] = col
    return out


# --------------------------------------------------------------------- OBV
def _obv_calc(bars, p):
    """能量潮：涨日加量、跌日减量，累计。单位【万手】——

    🔴 原始值是"股×100"（面板 volume 的单位），累计几年就是 1e10 量级，
      纵轴上全是科学计数法。换算成万手，纵轴才读得出来。
    """
    s = 0.0
    out = []
    for i, b in enumerate(bars):
        v = b.get('volume') or 0
        if i:
            d = b['close'] - bars[i - 1]['close']
            s += v if d > 0 else (-v if d < 0 else 0)
        out.append(round(s / 1e4, 2))
    ma = _ma(out, p['n']) if p['n'] else [None] * len(bars)
    return {'obv': out, 'obvma': [_r3(x) for x in ma]}


# -------------------------------------------------------------------- BIAS
def _bias_series(p):
    return [_S('bias%d' % p[k], 'BIAS%d' % p[k], 'line', None, i)
            for i, k in enumerate(('n1', 'n2', 'n3')) if p[k]] + [_S('_zero', '', 'zero')]


def _bias_calc(bars, p):
    cl = [b['close'] for b in bars]
    out = {}
    for k in ('n1', 'n2', 'n3'):
        n = p[k]
        if not n:
            continue
        ma = _ma(cl, n)
        out['bias%d' % n] = [(round((cl[i] / ma[i] - 1) * 100, 3)
                              if ma[i] else None) for i in range(len(cl))]
    return out


# ------------------------------------------------------------ 均线差（金叉）
def _spread_calc(bars, p):
    """MA(fast) 比 MA(slow) 高多少（%）。>0 = 金叉状态。

    ★ 这条线**过零点就是金叉/死叉**，所以它比"画两条均线让人自己看"
      更直接回答「离金叉还有多远」——而真正的答案是反解出来的触发价
      （见 `SIGNALS`），这条线只是让人看见趋势。
    """
    cl = [b['close'] for b in bars]
    f, s = _ma(cl, p['fast']), _ma(cl, p['slow'])
    return {'spread': [(round((f[i] / s[i] - 1) * 100, 3)
                        if (f[i] and s[i]) else None) for i in range(len(cl))]}


REG = [
    Spec('ma', '均线 MA', 'main',
         [_P('n1', '周期1', 5), _P('n2', '周期2', 10),
          _P('n3', '周期3', 20), _P('n4', '周期4', 60)],
         _ma_series, _ma_calc, lambda p: max(p['n1'], p['n2'], p['n3'], p['n4']),
         desc='收盘价的简单移动平均，叠在主图上'),
    Spec('boll', '布林带 BOLL', 'main',
         [_P('n', '周期', 20), _P('k', '倍数σ', 2, 1, 5)],
         lambda p: [_S('ub', '上轨', 'line', '#7b8794'),
                    _S('lb', '下轨', 'line', '#7b8794')],
         _boll_calc, lambda p: p['n'],
         desc='中轨=MA(N)，上下轨=中轨±k倍总体标准差。中轨与 MA20 是同一条线，所以不画'),
    Spec('macd', 'MACD', 'sub',
         [_P('fast', '快线', 12), _P('slow', '慢线', 26), _P('signal', '信号', 9)],
         lambda p: [_S('dif', 'DIF', 'line', None, 0), _S('dea', 'DEA', 'line', None, 1),
                    _S('macd', 'MACD', 'bar')],
         _macd_calc, lambda p: p['slow'] * 3 + p['signal'],
         desc='DIF=EMA快−EMA慢，DEA=DIF的EMA，柱=（DIF−DEA）×2'),
    Spec('kdj', 'KDJ', 'sub',
         [_P('n', '周期', 9), _P('k', 'K平滑', 3, 1, 20), _P('d', 'D平滑', 3, 1, 20)],
         lambda p: [_S('k', 'K', 'line', None, 0), _S('d', 'D', 'line', None, 1),
                    _S('jj', 'J', 'line', None, 2)],
         _kdj_calc, lambda p: p['n'] + 30,
         desc='随机指标。K/D 用通用平滑（与通达信一致），J=3K−2D'),
    Spec('rsi', 'RSI', 'sub',
         [_P('n1', '周期1', 6), _P('n2', '周期2', 12), _P('n3', '周期3', 24)],
         lambda p: [_S('rsi%d' % p[k], 'RSI%d' % p[k], 'line', None, i)
                    for i, k in enumerate(('n1', 'n2', 'n3')) if p[k]],
         _rsi_calc, lambda p: max(p['n1'], p['n2'], p['n3']) * 4,
         desc='相对强弱。Wilder 平滑，0~100'),
    Spec('atr', 'ATR 真实波幅', 'sub',
         [_P('n', '周期', 14)],
         lambda p: [_S('atr', 'ATR', 'line', None, 0)],
         _atr_calc, lambda p: p['n'] * 4, unit='元',
         desc='TR=max(高−低, |高−昨收|, |低−昨收|) 的 Wilder 均值。衡量波动幅度，不指示方向'),
    Spec('cci', 'CCI 顺势指标', 'sub',
         [_P('n', '周期', 14)],
         lambda p: [_S('cci', 'CCI', 'line', None, 0), _S('_zero', '', 'zero')],
         _cci_calc, lambda p: p['n'] + 5,
         desc='(典型价−均值)/(0.015×平均绝对偏差)。常用 ±100 当超买超卖线'),
    Spec('wr', 'WR 威廉指标', 'sub',
         [_P('n1', '周期1', 6), _P('n2', '周期2', 10)],
         lambda p: [_S('wr%d' % p[k], 'WR%d' % p[k], 'line', None, i)
                    for i, k in enumerate(('n1', 'n2')) if p[k]],
         _wr_calc, lambda p: max(p['n1'], p['n2']) + 5,
         desc='(N日最高−收盘)/(N日最高−N日最低)×100。**数值越小越强**（与 KDJ 方向相反）'),
    Spec('obv', 'OBV 能量潮', 'sub',
         [_P('n', '均线', 30)],
         lambda p: [_S('obv', 'OBV', 'line', None, 0),
                    _S('obvma', 'MA', 'line', None, 1)],
         _obv_calc, lambda p: p['n'], unit='万手',
         desc='涨日加量、跌日减量的累计值。看量价是否同向'),
    Spec('bias', 'BIAS 乖离率', 'sub',
         [_P('n1', '周期1', 6), _P('n2', '周期2', 12), _P('n3', '周期3', 24)],
         _bias_series, _bias_calc,
         lambda p: max(p['n1'], p['n2'], p['n3']), unit='%',
         desc='(收盘/MA(N)−1)×100。现价离均线多远'),
    Spec('spread', '均线差（金叉）', 'sub',
         [_P('fast', '快线', 5), _P('slow', '慢线', 20)],
         lambda p: [_S('spread', 'MA%d−MA%d' % (p['fast'], p['slow']),
                       'line', None, 0), _S('_zero', '', 'zero')],
         _spread_calc, lambda p: max(p['fast'], p['slow']), unit='%',
         short='均线差',
         desc='(MA快/MA慢−1)×100。**过零点就是金叉/死叉**'),
]
BY_ID = dict((s.id, s) for s in REG)


def spec(ind_id):
    s = BY_ID.get((ind_id or '').strip())
    if not s:
        raise IndError('没有这个指标：%r（有的是 %s）'
                       % (ind_id, '/'.join(BY_ID)))
    return s


def defs():
    """给页面的指标清单 —— **页面不认识任何一个指标名**，照这份渲染。"""
    out = []
    for s in REG:
        p = s.merge(None)
        out.append({'id': s.id, 'label': s.label, 'short': s.short,
                    'panel': s.panel,
                    'unit': s.unit, 'desc': s.desc, 'params': s.params,
                    'series': s.series(None), 'warm': s.warm(None),
                    'defaults': p})
    return out


def compute(bars, ind_id, params=None):
    """算一个指标。`bars` 要**已经带够预热**（见 `warm`），这里不管取数。"""
    return spec(ind_id).calc(bars, params)


# ============================== 买点条件 ==============================
# 一条 = 指标里的某个序列 + 比较方向 + 阈值。页面照这份渲染，不认识业务语义。
SIGNALS = [
    {'id': 'ma_dist', 'label': '距均线', 'ind': 'bias', 'unit': '%',
     'args': [_P('w', '均线周期', 20)],
     'key': lambda a: 'bias%d' % a['w'],
     'params': lambda a: {'n1': a['w'], 'n2': 0, 'n3': 0},
     'op': 'le', 'v': -3.0,
     'text': lambda a, v: '距 MA%d %s %+.4g%%' % (a['w'], '\u2264', v),
     'tip': '现价比 MA(N) 低这么多就提醒。填 -3 = 跌到 20 日线下方 3%'},
    {'id': 'ma_cross', 'label': '均线金叉', 'ind': 'spread', 'unit': '%',
     'args': [_P('fast', '快线', 5), _P('slow', '慢线', 20)],
     'key': lambda a: 'spread',
     'params': lambda a: {'fast': a['fast'], 'slow': a['slow']},
     'op': 'ge', 'v': 0.0,
     'text': lambda a, v: ('MA%d 上穿 MA%d（金叉）' % (a['fast'], a['slow'])
                           if abs(v) < 1e-9 else
                           'MA%d 高于 MA%d %+.4g%%' % (a['fast'], a['slow'], v)),
     'tip': 'MA快 比 MA慢 高这么多就提醒。填 0 = 刚好金叉那一刻'},
    {'id': 'rsi_low', 'label': 'RSI 超卖', 'ind': 'rsi', 'unit': '',
     'args': [_P('w', '周期', 6)],
     'key': lambda a: 'rsi%d' % a['w'],
     'params': lambda a: {'n1': a['w'], 'n2': 0, 'n3': 0},
     'op': 'le', 'v': 30.0,
     'text': lambda a, v: 'RSI%d %s %.4g' % (a['w'], '\u2264', v),
     'tip': 'RSI 跌到这个值以下就提醒'},
    {'id': 'kdj_low', 'label': 'KDJ 的 K 超卖', 'ind': 'kdj', 'unit': '',
     'args': [_P('n', '周期', 9)],
     'key': lambda a: 'k',
     'params': lambda a: {'n': a['n'], 'k': 3, 'd': 3},
     'op': 'le', 'v': 20.0,
     'text': lambda a, v: 'KDJ(%d) 的 K %s %.4g' % (a['n'], '\u2264', v),
     'tip': 'K 值跌到这个值以下就提醒'},
    {'id': 'boll_low', 'label': '距布林下轨', 'ind': 'boll', 'unit': '%',
     'args': [_P('n', '周期', 20), _P('k', '倍数σ', 2, 1, 5)],
     'key': lambda a: 'lbd',
     'params': lambda a: {'n': a['n'], 'k': a['k']},
     'op': 'le', 'v': 0.0,
     'text': lambda a, v: ('触到 BOLL(%d,%d) 下轨' % (a['n'], a['k'])
                           if abs(v) < 1e-9 else
                           '距 BOLL(%d,%d) 下轨 %s %+.4g%%' % (a['n'], a['k'], '\u2264', v)),
     'tip': '现价离下轨还有多少（%）。填 0 = 触到下轨'},
    {'id': 'cci_low', 'label': 'CCI 超卖', 'ind': 'cci', 'unit': '',
     'args': [_P('n', '周期', 14)],
     'key': lambda a: 'cci',
     'params': lambda a: {'n': a['n']},
     'op': 'le', 'v': -100.0,
     'text': lambda a, v: 'CCI(%d) %s %.4g' % (a['n'], '\u2264', v),
     'tip': 'CCI 跌到这个值以下就提醒（常用 −100）'},
]
SIG_BY_ID = dict((s['id'], s) for s in SIGNALS)


def signal_defs():
    """给页面的买点条件清单（`args` 里已经是可直接渲染的输入框定义）。"""
    out = []
    for s in SIGNALS:
        out.append({'id': s['id'], 'label': s['label'], 'ind': s['ind'],
                    'unit': s['unit'], 'args': s['args'], 'op': s['op'],
                    'v': s['v'], 'tip': s['tip'], 'dir': sig_dir(s['id']),
                    'sample': sig_text(s['id'], None, s['v']),
                    'ind_label': spec(s['ind']).label})
    return out


def sig_dir(sig_id):
    """这条件是【跌到】还是【涨到】才成立。

    🔴 这不是装饰：买点页原来只有"跌到目标价"一种方向（`现价 <= 目标价`
      = 到价）。而「MA5 上穿 MA20」是**涨上去**才成立 —— 方向记反的话，
      一只已经金叉的票会被判成"还差 3%"，**而它不报错**。
    ★ 判据取 `op`：本模块所有序列都**随当日价格单调增**（bias/spread/RSI/
      KDJ-K/CCI/距下轨都是），所以 `le` = 价格往下走才成立、`ge` = 往上。
      单调性不是假设 —— `trigger_price` 每次都当场验（两端夹住 + 解完
      两侧翻面），不成立就报"解不出"。
    """
    return 'down' if sig(sig_id)['op'] == 'le' else 'up'


def sig(sig_id):
    s = SIG_BY_ID.get((sig_id or '').strip())
    if not s:
        raise IndError('没有这个买点条件：%r（有的是 %s）'
                       % (sig_id, '/'.join(SIG_BY_ID)))
    return s


def sig_args(sig_id, args):
    """校验并补齐一条买点条件的参数（同 `Spec.merge`：越界报错不夹逼）。"""
    s = sig(sig_id)
    out = {}
    for d in s['args']:
        v = (args or {}).get(d['key'], d['default'])
        try:
            v = int(v)
        except Exception:                                   # noqa: BLE001
            raise IndError('%s 的 %s 要是整数，收到 %r' % (s['label'], d['label'], v))
        if not (d['min'] <= v <= d['max']):
            raise IndError('%s 的 %s 要在 %d~%d 之间，收到 %d'
                           % (s['label'], d['label'], d['min'], d['max'], v))
        out[d['key']] = v
    if s['id'] == 'ma_cross' and out['fast'] >= out['slow']:
        raise IndError('金叉的快线要短于慢线，收到 %d/%d' % (out['fast'], out['slow']))
    return out


def sig_text(sig_id, args, v):
    """这条件的人话，比如「距 MA20 ≤ -3%」「MA5 上穿 MA20（金叉）」。

    🔴 **在服务端拼**：页面拿到的是一句现成的话，它不认识 `ma_dist`
      是什么意思、也不知道 `w` 该念成"周期"还是"均线"（同
      「指标的中文标签/量纲配对在服务端，页面不认识任何一个列名」那条）。
    """
    s = sig(sig_id)
    return s['text'](sig_args(sig_id, args), float(v))


def sig_warm(sig_id, args):
    s = sig(sig_id)
    a = sig_args(sig_id, args)
    return spec(s['ind']).warm(s['params'](a))


def sig_value(bars, sig_id, args):
    """这条件当前的值（用最后一根 bar 算）。取不到给 None。"""
    s = sig(sig_id)
    a = sig_args(sig_id, args)
    col = spec(s['ind']).calc(bars, s['params'](a)).get(s['key'](a))
    return (col or [None])[-1] if col else None


def _with_close(bars, px):
    """把最后一根换成"收在 px"的样子。

    🔴 high/low 必须一起改：价格跌到 px，那天的最低价**至少**是 px。
      只改 close 的话 KDJ/WR/ATR 这些读 high/low 的指标会算出一个
      今天根本不可能出现的值，而它不报错。
    """
    b = dict(bars[-1])
    b['close'] = px
    b['high'] = max(b.get('high') or px, px)
    b['low'] = min(b.get('low') or px, px)
    return bars[:-1] + [b]


def trigger_price(bars, sig_id, args, v, lo=0.5, hi=1.6, iters=44):
    """反解：今天收在什么价位，这个条件刚好成立。

    返回 (价格, 原因)；解不出来时价格是 None，`原因` 说明为什么 ——
    **不猜一个数**（同「拿不到分红那一格标"查不到"」那条）。

    判据是**当场验单调**：在 [lo, hi]×现价 两端各算一次，阈值必须被夹在
    中间；不夹就说明这个区间里根本到不了（或者这条件在这只票上不单调），
    两种都该说出来而不是给个数。
    """
    s = sig(sig_id)
    a = sig_args(sig_id, args)
    sp, key, pr = spec(s['ind']), s['key'](a), s['params'](a)
    if not bars:
        return None, '没有行情'
    cur = bars[-1]['close']
    if not cur:
        return None, '没有现价'

    def f(px):
        col = sp.calc(_with_close(bars, px), pr).get(key)
        return (col or [None])[-1]

    a0, b0 = cur * lo, cur * hi
    fa, fb = f(a0), f(b0)
    if fa is None or fb is None:
        return None, '数据不够，算不出这个指标'
    # op='le'：要 f(px) <= v，而 f 随价格单调增 -> 触发价是最大的满足者
    if s['op'] == 'le':
        if fa > v:
            return None, '就算跌到 %.2f 也到不了' % a0
        if fb <= v:
            return None, '这个条件现在恒成立'
    else:
        if fb < v:
            return None, '就算涨到 %.2f 也到不了' % b0
        if fa >= v:
            return None, '这个条件现在恒成立'
    for _ in range(iters):
        mid = (a0 + b0) / 2.0
        fm = f(mid)
        if fm is None:
            return None, '数据不够，算不出这个指标'
        below = (fm <= v) if s['op'] == 'le' else (fm >= v)
        # f 单调增：le 时解在右边界，ge 时解在左边界
        if s['op'] == 'le':
            if below:
                a0 = mid
            else:
                b0 = mid
        else:
            if below:
                b0 = mid
            else:
                a0 = mid
    px = round((a0 + b0) / 2.0, 3)
    # 🔴 **解完必须当场验**：二分只保证"两端夹住"，对非单调的函数会收敛到
    #   一个**假根**（而那个数看着完全正常）。判据是触发价两侧必须刚好翻面。
    d = max(px * 0.002, 0.01)
    f1, f2 = f(px - d), f(px + d)
    if f1 is None or f2 is None:
        return None, '数据不够，算不出这个指标'
    ok = (f1 <= v < f2) if s['op'] == 'le' else (f1 < v <= f2)
    if not ok:
        return None, '这个条件在这只票上不单调，解不出唯一的触发价'
    return px, ''
