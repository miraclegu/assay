/* K 线绘制（Canvas）。★ 独立一个文件 —— 个股页与对比页都用它，
   而"两个页面各画一份"必然分叉（一处修了坐标，另一处还是老的）。

   ★ 为什么手画不引图表库：这个看板是单文件静态资源、无构建、无 CDN
     （见 serve.py）。引库要么得打包要么得联网，两条都不成立。

   ★ 按 devicePixelRatio 放大再画，否则 Retina 上全是毛边。
   ★ 涨红跌绿（A 股习惯，与 :root 的 --up/--down 一致）。 */

/* 🔴 均线配色要【互相拉开】，也要与事件三角拉开。踩过的两处：
     · ma5 #e0a33c 与 BOLL 中轨 #f9a06b 色距只有 53（肉眼难分）——
       表现就是"5 日线看着有两条"，而其中一条其实是 MA20（见下）
     · 四条均线的 hex 与四种事件三角【完全相同】，底部图例写着
       "▲除权除息 ▲财报公告…" 用的正是均线那四个色
   判据不靠眼睛：selftest 里按 RGB 欧氏距离量，任意两条 < 60 就算撞色。 */
const MA_COLOR = {ma5: '#f5d33f', ma10: '#3d8bfd', ma20: '#c264e8',
                  ma60: '#2fd6a8'};
const MA_LABEL = {ma5: 'MA5', ma10: 'MA10', ma20: 'MA20', ma60: 'MA60'};
/* 事件三角另成一族（与均线不共享任何色）。 */
const EV_COLOR = {xr: '#ff8fb1', fin: '#8ea9ff', unlock: '#ffb066',
                  share: '#9ad1ff'};
/* BOLL 上下轨：单色 + 虚线 —— 它是"波动带"不是均线，形状上就该不一样。
   🔴 **中轨刻意不画**：BOLL 中轨 = MA(20) 收盘均值，与 ma20 是同一条线
     （实测最大差 0.001，纯舍入）。同一条线画两遍还配两个颜色，
     就是"看着有两条、两条都不对"的来源。 */
const BOLL_COLOR = '#7b8794';

/* 主图 K 线 + 成交量 + 可选事件标记与副图。
   opts: {bars, events, sub, subKind, hover, sel} —— hover 是索引或 null；
   sel = {i0, i1} 是框选区间（按**索引**给，不是像素 —— 重画后要落在同样那几天）。
   返回几何信息供命中测试用。 */
function drawKChart(cv, opts) {
  const bars = opts.bars || [];
  if (!bars.length) return null;
  const {g, W, H} = cvPrep(cv);
  const UP = cssv('--up', '#f05b5b'), DN = cssv('--down', '#2fb87a');
  const LINE = cssv('--line', '#2a313a'), DIM = cssv('--dim', '#8b97a6');
  const PADL = 54, PADR = 8, PADT = 8, PADB = 18;
  const sub = opts.sub && opts.sub.length ? opts.sub : null;
  const GAP = 8;
  const volH = Math.round(H * (sub ? 0.15 : 0.22));
  const subH = sub ? Math.round(H * 0.20) : 0;
  const mainH = H - PADT - PADB - GAP * (sub ? 2 : 1) - volH - subH;
  const volTop = PADT + mainH + GAP;
  const subTop = volTop + volH + GAP;
  const w = W - PADL - PADR;
  let lo = Infinity, hi = -Infinity, vmax = 0;
  bars.forEach(b => {
    if (b.low != null) lo = Math.min(lo, b.low);
    if (b.high != null) hi = Math.max(hi, b.high);
    ['ma5', 'ma10', 'ma20', 'ma60', 'ub', 'lb'].forEach(k => {
      if (b[k] != null) { lo = Math.min(lo, b[k]); hi = Math.max(hi, b[k]); }
    });
    vmax = Math.max(vmax, b.volume || 0);
  });
  if (!isFinite(lo) || !isFinite(hi)) return null;
  /* 🔴 **对数坐标**（opts.log）。为什么 K 线需要它：线性轴上"涨 1 块"
     在 10 元和 100 元处占同样的高度，于是看长区间时**低价那一段的波动被
     压平**，翻倍的行情看着像一条平线。对数轴上等百分比 = 等高度，
     几年的走势才可比。
     ★ 只换坐标映射，**数据一个字不动** —— 蜡烛、MA、BOLL、买卖点、
       十字光标读数全都走这同一个 `Y`，所以改这一处就够（不用逐处改）。
     🔴 `log10` 的定义域要求 v > 0：价格理论上恒正，但**面板里可能有 0
       或 null**（停牌补的行、脏数据），`Math.log10(0)` 是 -Infinity，
       会让整张图的坐标变成 NaN —— 一片空白且不报错。所以下界兜到一个
       正数，并且 lo 也不再按线性减 pad（那会减成负数）。 */
  const LOG = !!opts.log && lo > 0;
  const lg = v => Math.log10(v > 0 ? v : 1e-9);
  let plo, phi;
  if (LOG) {
    plo = lg(lo); phi = lg(hi);
    const p2 = (phi - plo) * 0.06 || 0.01;
    plo -= p2; phi += p2;
  } else {
    const pad = (hi - lo) * 0.06 || 1;
    plo = lo - pad; phi = hi + pad;
  }
  /* 🔴 **柱子有最大宽度，画不满就【往左贴】，不要拉伸去填满画布。**
     原来 `step = w / n` —— 有几根画几根、平分整个宽度。新股只有十几根时
     每根就有 **60px 宽**（实测 688835 上市 15 天，1440px 画布），
     一屏几个大色块，既看不出形态、也让人误以为"这只票就长这样"。

     ★ 判据用**单根最大像素宽**而不是写死"最少显示 N 天"：
       画布宽度本来就不一样（个股页 ~1380、浮层 1180），写死天数在窄画布上
       又会太挤；而"一根 K 线最宽 14px"在哪儿都成立。
       换算过来就是"最少展示 w/14 ≈ 98 天"，随画布自适应。
     ★ 也不能反过来按"请求了多少天"留槽：选「1 年」时 15 根票会被压成
       15 条发丝，那是另一个极端 —— 人要看的是这只票，不是那个空窗口。

     🔴 **往左贴而不是往右贴**，理由不只是"用户要的"：右贴意味着左边那片
       空白代表"上市之前"，而那些日子在 `bars` 里根本没有 —— x 轴标不出
       日期，等于画一段说不清是什么的留白。左贴时空白在右边，含义是
       "还没到的日子"，那是**自明**的。
     ★ 命中测试天然不受影响：两个调用点都有 `i < n` 的护栏，
       空槽反解出来的 index 越界，tooltip 自己就不显示。 */
  const MAXSTEP = opts.maxStep || 14;
  const n = bars.length, step = Math.min(w / n, MAXSTEP);
  const bw = Math.max(1, step * 0.7);
  const X = i => PADL + step * (i + 0.5);
  const Y = v => PADT + mainH
    - ((LOG ? lg(v) : v) - plo) / (phi - plo) * mainH;
  const VY = v => volTop + volH - (vmax ? v / vmax * volH : 0);

  g.font = '10px ui-monospace,Menlo,monospace';
  g.textAlign = 'right';
  for (let i = 0; i <= 4; i++) {
    /* ★ 刻度在**当前坐标系里**均分，再映回价格 —— 对数轴上线性均分的话
       上半张图会挤成一团（同一段像素高度对应的价差不一样）。 */
    const t = plo + (phi - plo) * i / 4;
    const v = LOG ? Math.pow(10, t) : t, y = Y(v);
    g.strokeStyle = LINE; g.globalAlpha = .5;
    g.beginPath(); g.moveTo(PADL, y); g.lineTo(W - PADR, y); g.stroke();
    g.globalAlpha = 1;
    g.fillStyle = DIM; g.fillText(v.toFixed(2), PADL - 5, y + 3);
  }
  /* BOLL 上下轨先画（在蜡烛下层）。虚线 + 单色，且【不画中轨】。 */
  if (bars[0] && bars[0].ub !== undefined) {
    g.setLineDash([4, 3]);
    ['ub', 'lb'].forEach(k => {
      g.strokeStyle = BOLL_COLOR; g.globalAlpha = .8; g.lineWidth = 1;
      g.beginPath();
      let st = false;
      bars.forEach((b, i) => {
        const v = b[k]; if (v == null) { st = false; return; }
        if (!st) { g.moveTo(X(i), Y(v)); st = true; } else g.lineTo(X(i), Y(v));
      });
      g.stroke(); g.globalAlpha = 1;
    });
    g.setLineDash([]);
  }
  bars.forEach((b, i) => {
    if (b.close == null) return;
    /* 🔴 **阳线也实心**（2026-09-09 用户要求）：原来阳线画 strokeRect（空心，
       A 股软件的老习惯），但柱子窄的时候（120 根挤在一屏）1px 的描边中间
       是背景色，看着比阴线**淡一档**，一眼扫过去像"涨的那些不重要"。
       ★ 影线仍然 stroke —— 它本来就是一条线。
       🔴 **平盘（收=开）用中性色**，不并进涨色：`>= 0` 会把"没涨"画成红的，
       等于凭空报了个涨（同 common.js 的 `upc` 那条）。 */
    const o0 = b.open == null ? b.close : b.open;
    const c = b.close > o0 ? UP : b.close < o0 ? DN : cssv('--dim', '#8b97a6');
    g.strokeStyle = c; g.fillStyle = c; g.lineWidth = 1;
    const x = X(i);
    g.beginPath(); g.moveTo(x, Y(b.high)); g.lineTo(x, Y(b.low)); g.stroke();
    const y1 = Y(Math.max(o0, b.close)), y2 = Y(Math.min(o0, b.close));
    const hh = Math.max(1, y2 - y1);
    g.fillRect(x - bw / 2, y1, bw, hh);
    g.fillStyle = c; g.globalAlpha = .55;
    g.fillRect(x - bw / 2, VY(b.volume || 0), bw, volTop + volH - VY(b.volume || 0));
    g.globalAlpha = 1;
    if (b.limit_up || b.limit_down) {
      g.fillStyle = b.limit_up ? UP : DN;
      g.beginPath(); g.arc(x, volTop + volH + 4, 1.6, 0, 6.284); g.fill();
    }
  });
  /* 均线：由长到短画 —— 短均线最活跃、要压在上层不被遮住。 */
  ['ma60', 'ma20', 'ma10', 'ma5'].forEach(k => {
    if (bars[0] && bars[0][k] === undefined) return;
    g.strokeStyle = MA_COLOR[k];
    g.lineWidth = (k === 'ma5' ? 1.5 : 1.1);
    g.beginPath();
    let st = false;
    bars.forEach((b, i) => {
      const v = b[k]; if (v == null) { st = false; return; }
      if (!st) { g.moveTo(X(i), Y(v)); st = true; } else g.lineTo(X(i), Y(v));
    });
    g.stroke();
  });
  /* 事件标记：在主图底部按日期打小三角。
     ★ 分红用【除权日】定位 —— 那天价格才跳；用公告日会标错位置。 */
  const evs = opts.events || [];
  if (evs.length) {
    const at = {};
    bars.forEach((b, i) => { at[b.date] = i; });
    const EC = EV_COLOR;
    evs.forEach(e => {
      const i = at[e.date]; if (i == null) return;
      const x = X(i), y = PADT + mainH - 2;
      g.fillStyle = EC[e.kind] || DIM;
      g.beginPath(); g.moveTo(x, y); g.lineTo(x - 3.2, y + 5.4);
      g.lineTo(x + 3.2, y + 5.4); g.closePath(); g.fill();
    });
  }
  /* 买卖点：画在**成交价的位置**上，不是主图底部。
     ★ 与事件三角刻意不同族：事件回答"那天发生了什么"（固定在底部按日期
       排开就够），买卖点回答"我在**哪个价位**进出的" —— 位置本身就是信息，
       钉在底部等于把它扔掉。
     🔴 成交价是**不复权**实际价，所以浮层固定用 bfq。切到后复权时标记会
       整体飘走，**而它不报错** —— 判据写在调用方（stockpop 固定 bfq）。
     ★ 同日多笔各画一个：最低佣金按成交笔收，分笔录入就是对的（见 CLAUDE.md），
       图上也该看得出"那天分了两笔"。 */
  const trs = opts.trades || [];
  const trHits = [];
  if (trs.length) {
    const at = {};
    bars.forEach((bb, i) => { at[bb.date] = i; });
    const bySlot = {};
    g.save();
    g.font = 'bold 10px ui-sans-serif,system-ui,sans-serif';
    g.textAlign = 'center';
    g.textBaseline = 'middle';
    trs.forEach(t => {
      const i = at[t.date];
      if (i == null || t.price == null) return;
      const k = i + '|' + t.side;
      bySlot[k] = (bySlot[k] || 0) + 1;
      const dup = bySlot[k] - 1;               /* 同日同向的第 n 笔，往外错开 */
      const buy = t.side === 'buy';
      const x = X(i), py = Y(+t.price);
      const R = 7.5;
      const cy = buy ? py + R + 5 + dup * (R * 2 + 2)
                     : py - R - 5 - dup * (R * 2 + 2);
      /* ① 从圆心到**成交价那一点**的细引线 + 一个小点：圆本身有半径，
         光靠圆的位置说不出"到底是哪个价" —— 引线的端点才是确切价位。 */
      g.strokeStyle = buy ? UP : DN;
      g.lineWidth = 1;
      g.globalAlpha = .75;
      g.beginPath(); g.moveTo(x, py); g.lineTo(x, cy + (buy ? -R : R)); g.stroke();
      g.globalAlpha = 1;
      g.beginPath(); g.arc(x, py, 1.6, 0, 6.2832); g.fill();
      /* ② B / S 圆点。★ 描一圈背景色的边：K 线密的时候圆压在影线上，
         没有这圈边就糊成一团（同均线配色要互相拉开那条）。 */
      g.beginPath(); g.arc(x, cy, R, 0, 6.2832);
      g.fillStyle = buy ? UP : DN; g.fill();
      g.strokeStyle = cssv('--bg', '#0f1216'); g.lineWidth = 1.4; g.stroke();
      g.fillStyle = '#fff';
      g.fillText(buy ? 'B' : 'S', x, cy + .5);
      trHits.push({x: x, y: cy, r: R + 3, t: t});
    });
    g.restore();
  }

  /* 副图 */
  if (sub) {
    const rows = opts.sub;
    const kind = opts.subKind || 'macd';
    let slo = Infinity, shi = -Infinity;
    const keys = kind === 'macd' ? ['dif', 'dea', 'macd']
      : kind === 'kdj' ? ['k', 'd', 'jj'] : ['rsi6', 'rsi12', 'rsi24'];
    rows.forEach(r => keys.forEach(k => {
      if (r[k] != null) { slo = Math.min(slo, r[k]); shi = Math.max(shi, r[k]); }
    }));
    if (isFinite(slo) && isFinite(shi)) {
      if (shi === slo) { shi += 1; slo -= 1; }
      const SY = v => subTop + subH - (v - slo) / (shi - slo) * subH;
      g.strokeStyle = LINE; g.globalAlpha = .5;
      g.beginPath(); g.moveTo(PADL, subTop); g.lineTo(W - PADR, subTop); g.stroke();
      g.globalAlpha = 1;
      g.textAlign = 'right'; g.fillStyle = DIM;
      g.fillText(shi.toFixed(1), PADL - 5, subTop + 8);
      g.fillText(slo.toFixed(1), PADL - 5, subTop + subH);
      if (kind === 'macd') {
        rows.forEach((r, i) => {
          if (r.macd == null) return;
          g.fillStyle = r.macd >= 0 ? UP : DN;
          const y0 = SY(0), y1 = SY(r.macd);
          g.fillRect(X(i) - bw / 2, Math.min(y0, y1), bw, Math.abs(y1 - y0) || 1);
        });
      }
      const SC = {dif: '#e0a33c', dea: '#5b9cf0', k: '#e0a33c', d: '#5b9cf0',
                  jj: '#a06bf0', rsi6: '#e0a33c', rsi12: '#5b9cf0', rsi24: '#a06bf0'};
      keys.filter(k => k !== 'macd').forEach(k => {
        g.strokeStyle = SC[k] || DIM; g.lineWidth = 1.1; g.beginPath();
        let st = false;
        rows.forEach((r, i) => {
          const v = r[k]; if (v == null) { st = false; return; }
          if (!st) { g.moveTo(X(i), SY(v)); st = true; } else g.lineTo(X(i), SY(v));
        });
        g.stroke();
      });
      /* 副图图例：名称 + **那一天的值**，跟着光标变。
         🔴 用户 2026-09-15："MACD的信息应该放在MACD的左上角，数字跟随变化"
           —— 原来这里只有名字没有值，于是 DIF/DEA/MACD 得去读数浮窗里找，
           而那三个数**只在副图上有意义**（主图的 MA 图例早就带值了，
           所以浮窗里再列一遍是重复）。
         ★ 光标没停在哪天就显示**最后一根**（同主图图例那条）。 */
      g.textAlign = 'left'; let lx = PADL + 2;
      const sr = rows[(opts.hover != null && rows[opts.hover]) ? opts.hover
                      : rows.length - 1] || {};
      keys.forEach(k => {
        g.fillStyle = k === 'macd' ? DIM : (SC[k] || DIM);
        const v = sr[k];
        const t = k.toUpperCase() + ' '
          + (v == null ? '—' : (Math.abs(v) >= 100 ? v.toFixed(0) : v.toFixed(2)));
        g.fillText(t, lx, subTop + 10);
        lx += g.measureText(t).width + 9;
      });
    }
  }
  /* ---- 图例：色块 + 名称 + 【那一天的值】 ----
     ★ 光标停在哪天就显示那天的值，没停就显示最后一根。
     🔴 值必须给 —— 只有名字的话，几条颜色相近的线还是分不出谁是谁；
       有了数字就能拿它跟纵轴对一下，"哪条是 MA5"不用猜。 */
  g.textAlign = 'left'; g.font = '10px ui-monospace,Menlo,monospace';
  let lx = PADL + 2;
  const lb = bars[(opts.hover != null && bars[opts.hover]) ? opts.hover
                  : bars.length - 1] || {};
  ['ma5', 'ma10', 'ma20', 'ma60'].forEach(k => {
    if (bars[0] && bars[0][k] === undefined) return;
    g.fillStyle = MA_COLOR[k];
    g.fillRect(lx, PADT + 3, 7, (k === 'ma5' ? 2.5 : 1.6));
    lx += 10;
    const t = MA_LABEL[k] + ' ' + (lb[k] == null ? '—' : lb[k].toFixed(2));
    g.fillText(t, lx, PADT + 10);
    lx += g.measureText(t).width + 9;
  });
  if (bars[0] && bars[0].ub !== undefined) {
    g.fillStyle = BOLL_COLOR;
    g.fillRect(lx, PADT + 5, 3, 1.4); g.fillRect(lx + 4, PADT + 5, 3, 1.4);
    lx += 10;
    const t = 'BOLL ' + (lb.lb == null ? '—' : lb.lb.toFixed(2)) + '~'
      + (lb.ub == null ? '—' : lb.ub.toFixed(2));
    g.fillText(t, lx, PADT + 10);
  }
  g.fillStyle = DIM; g.textAlign = 'center';
  [0, Math.floor(n / 2), n - 1].forEach(i => {
    if (bars[i]) g.fillText(bars[i].date.slice(2), X(i), H - 5);
  });
  if (opts.hover != null && bars[opts.hover]) {
    const x = X(opts.hover);
    g.strokeStyle = DIM; g.globalAlpha = .6; g.setLineDash([3, 3]);
    g.beginPath(); g.moveTo(x, PADT); g.lineTo(x, subTop + (sub ? subH : 0) || volTop + volH);
    g.stroke(); g.setLineDash([]); g.globalAlpha = 1;
  }
  /* 框选出来的区间：一条半透明带 + 两条边界线，**贯穿主图/量/副图** ——
     只在主图上画的话，量能那一段对不上就没法看"这几天是不是放量"。
     ★ 画在**最后**：它压在蜡烛上面，否则被后画的柱子盖住一半。
     ★ 用 `bars` 的索引而不是像素：重画（换区间/换副图/resize）之后
       选区必须还在同样的那几天上，而像素会随画布宽度变。 */
  if (opts.sel && opts.sel.i0 != null && opts.sel.i1 != null) {
    const a = Math.max(0, Math.min(opts.sel.i0, opts.sel.i1));
    const b = Math.min(n - 1, Math.max(opts.sel.i0, opts.sel.i1));
    const x0 = X(a) - step / 2, x1 = X(b) + step / 2;
    const yb = (sub ? subTop + subH : volTop + volH);
    g.fillStyle = cssv('--accent', '#5b9cf0');
    g.globalAlpha = .12;
    g.fillRect(x0, PADT, Math.max(1, x1 - x0), yb - PADT);
    g.globalAlpha = .55;
    g.strokeStyle = cssv('--accent', '#5b9cf0'); g.lineWidth = 1;
    g.beginPath(); g.moveTo(x0, PADT); g.lineTo(x0, yb);
    g.moveTo(x1, PADT); g.lineTo(x1, yb); g.stroke();
    g.globalAlpha = 1;
  }
  return {trHits: trHits, PADL, PADR, step, n, X};
}

/* 多股归一涨幅折线（对比页用）。series: [{code,name,ret:[...]}] */
function drawLines(cv, dates, series, hover) {
  if (!dates || !dates.length || !series || !series.length) return null;
  const {g, W, H} = cvPrep(cv);
  const LINE = cssv('--line', '#2a313a'), DIM = cssv('--dim', '#8b97a6');
  const PADL = 56, PADR = 8, PADT = 10, PADB = 20;
  const w = W - PADL - PADR, h = H - PADT - PADB;
  let lo = Infinity, hi = -Infinity;
  series.forEach(s => (s.ret || []).forEach(v => {
    if (v != null) { lo = Math.min(lo, v); hi = Math.max(hi, v); }
  }));
  if (!isFinite(lo) || !isFinite(hi)) return null;
  const pad = (hi - lo) * 0.08 || 0.01;
  lo -= pad; hi += pad;
  const n = dates.length, step = n > 1 ? w / (n - 1) : w;
  const X = i => PADL + step * i;
  const Y = v => PADT + h - (v - lo) / (hi - lo) * h;
  g.font = '10px ui-monospace,Menlo,monospace'; g.textAlign = 'right';
  for (let i = 0; i <= 4; i++) {
    const v = lo + (hi - lo) * i / 4, y = Y(v);
    g.strokeStyle = LINE; g.globalAlpha = .5;
    g.beginPath(); g.moveTo(PADL, y); g.lineTo(W - PADR, y); g.stroke();
    g.globalAlpha = 1;
    g.fillStyle = DIM;
    g.fillText((v >= 0 ? '+' : '') + (v * 100).toFixed(1) + '%', PADL - 5, y + 3);
  }
  /* 0 轴加粗 —— 「有没有赚」是这张图的第一判据 */
  if (lo < 0 && hi > 0) {
    g.strokeStyle = DIM; g.globalAlpha = .8; g.lineWidth = 1;
    g.beginPath(); g.moveTo(PADL, Y(0)); g.lineTo(W - PADR, Y(0)); g.stroke();
    g.globalAlpha = 1;
  }
  const PAL = ['#5b9cf0', '#f05b5b', '#2fb87a', '#e0a33c', '#a06bf0', '#3ec8d8'];
  series.forEach((s, k) => {
    g.strokeStyle = s.color || PAL[k % PAL.length];
    g.lineWidth = 1.5; g.beginPath();
    let st = false;
    (s.ret || []).forEach((v, i) => {
      if (v == null) { st = false; return; }   /* 停牌那几天断开，不连直线 */
      if (!st) { g.moveTo(X(i), Y(v)); st = true; } else g.lineTo(X(i), Y(v));
    });
    g.stroke();
  });
  g.fillStyle = DIM; g.textAlign = 'center';
  [0, Math.floor(n / 2), n - 1].forEach(i => {
    if (dates[i]) g.fillText(dates[i].slice(2), X(i), H - 5);
  });
  if (hover != null && dates[hover]) {
    const x = X(hover);
    g.strokeStyle = DIM; g.globalAlpha = .6; g.setLineDash([3, 3]);
    g.beginPath(); g.moveTo(x, PADT); g.lineTo(x, PADT + h); g.stroke();
    g.setLineDash([]); g.globalAlpha = 1;
    series.forEach((s, k) => {
      const v = (s.ret || [])[hover]; if (v == null) return;
      g.fillStyle = s.color || PAL[k % PAL.length];
      g.beginPath(); g.arc(x, Y(v), 2.6, 0, 6.284); g.fill();
    });
  }
  return {PADL, step, n, X, palette: PAL};
}
