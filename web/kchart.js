/* K 线绘制（Canvas）。★ 独立一个文件 —— 个股页与对比页都用它，
   而"两个页面各画一份"必然分叉（一处修了坐标，另一处还是老的）。

   ★ 为什么手画不引图表库：这个看板是单文件静态资源、无构建、无 CDN
     （见 serve.py）。引库要么得打包要么得联网，两条都不成立。

   ★ 按 devicePixelRatio 放大再画，否则 Retina 上全是毛边。
   ★ 涨红跌绿（A 股习惯，与 :root 的 --up/--down 一致）。 */

const MA_COLOR = {ma5: '#e0a33c', ma10: '#5b9cf0', ma20: '#a06bf0', ma60: '#3ec8d8'};

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
  /* BOLL 先画（在蜡烛下层） */
  if (bars[0] && bars[0].ub !== undefined) {
    [['ub', '#f97316'], ['mb', '#f9a06b'], ['lb', '#f97316']].forEach(([k, c]) => {
      g.strokeStyle = c; g.globalAlpha = .55; g.lineWidth = 1; g.beginPath();
      let st = false;
      bars.forEach((b, i) => {
        const v = b[k]; if (v == null) { st = false; return; }
        if (!st) { g.moveTo(X(i), Y(v)); st = true; } else g.lineTo(X(i), Y(v));
      });
      g.stroke(); g.globalAlpha = 1;
    });
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
  Object.keys(MA_COLOR).forEach(k => {
    if (bars[0] && bars[0][k] === undefined) return;
    g.strokeStyle = MA_COLOR[k]; g.lineWidth = 1.1; g.beginPath();
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
    const EC = {xr: '#e0a33c', fin: '#5b9cf0', unlock: '#a06bf0', share: '#3ec8d8'};
    evs.forEach(e => {
      const i = at[e.date]; if (i == null) return;
      const x = X(i), y = PADT + mainH - 2;
      g.fillStyle = EC[e.kind] || DIM;
      g.beginPath(); g.moveTo(x, y); g.lineTo(x - 3.2, y + 5.4);
      g.lineTo(x + 3.2, y + 5.4); g.closePath(); g.fill();
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
  g.textAlign = 'left'; g.font = '10px ui-monospace,Menlo,monospace';
  let lx = PADL + 2;
  Object.keys(MA_COLOR).forEach(k => {
    if (bars[0] && bars[0][k] === undefined) return;
    g.fillStyle = MA_COLOR[k];
    const t = k.toUpperCase(); g.fillText(t, lx, PADT + 10);
    lx += g.measureText(t).width + 10;
  });
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
  return {PADL, PADR, step, n, X};
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
