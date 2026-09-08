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
   opts: {bars, events, sub, subKind, hover} —— hover 是索引或 null。
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
  const pad = (hi - lo) * 0.06 || 1;
  lo -= pad; hi += pad;
  const n = bars.length, step = w / n, bw = Math.max(1, step * 0.7);
  const X = i => PADL + step * (i + 0.5);
  const Y = v => PADT + mainH - (v - lo) / (hi - lo) * mainH;
  const VY = v => volTop + volH - (vmax ? v / vmax * volH : 0);

  g.font = '10px ui-monospace,Menlo,monospace';
  g.textAlign = 'right';
  for (let i = 0; i <= 4; i++) {
    const v = lo + (hi - lo) * i / 4, y = Y(v);
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
    const up = b.close >= (b.open == null ? b.close : b.open);
    const c = up ? UP : DN;
    g.strokeStyle = c; g.fillStyle = c; g.lineWidth = 1;
    const x = X(i);
    g.beginPath(); g.moveTo(x, Y(b.high)); g.lineTo(x, Y(b.low)); g.stroke();
    const y1 = Y(Math.max(b.open, b.close)), y2 = Y(Math.min(b.open, b.close));
    const hh = Math.max(1, y2 - y1);
    if (up) g.strokeRect(x - bw / 2, y1, bw, hh);   /* 阳线空心，A 股习惯 */
    else g.fillRect(x - bw / 2, y1, bw, hh);
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
    bars.forEach((b, i) => { at[b.date] = i; });
    const bySlot = {};
    trs.forEach(t => {
      const i = at[t.date];
      if (i == null || t.price == null) return;
      const k = i + '|' + t.side;
      bySlot[k] = (bySlot[k] || 0) + 1;
      const dup = bySlot[k] - 1;               // 同日同向的第 n 笔，错开一点
      const buy = t.side === 'buy';
      const x = X(i), y = Y(+t.price);
      const off = 7 + dup * 8;
      const ty = buy ? y + off : y - off;      // 买在下方、卖在上方（不挡住 K 线）
      g.fillStyle = buy ? UP : DN;
      g.beginPath();
      if (buy) { g.moveTo(x, y + 2); g.lineTo(x - 4.6, ty + 3); g.lineTo(x + 4.6, ty + 3); }
      else { g.moveTo(x, y - 2); g.lineTo(x - 4.6, ty - 3); g.lineTo(x + 4.6, ty - 3); }
      g.closePath(); g.fill();
      g.strokeStyle = cssv('--bg', '#0f1216'); g.lineWidth = .8; g.stroke();
      trHits.push({x: x, y: ty, r: 8, t: t});
    });
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
      g.textAlign = 'left'; let lx = PADL + 2;
      keys.forEach(k => {
        g.fillStyle = k === 'macd' ? DIM : (SC[k] || DIM);
        const t = k.toUpperCase(); g.fillText(t, lx, subTop + 10);
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
