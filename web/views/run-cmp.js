/* ============ ⚖ 策略比对（#/cmp/<idA>,<idB>[,C,D]）============

   把 2~4 次回测摆在一起：归一净值 + 回撤副图 + 逐年收益/回撤 + KPI 对比。

   🔴🔴 **这一页最值钱的不是曲线，是「口径不一致要响亮地说出来」。**
     本项目两次栽在「拿滑点 0 的数字比含滑点的基准」（FROEC 与 v0b 各一次），
     2026-09-17 又踩了「基线截 08-07、变体截 09-17」。所以每次回测的
     成本 / 区间 / 本金 / 数据指纹 / 策略文件逐项比，不一致就标红并点名。
     没有这一层，这页就是一台生产错误结论的机器。

   ★ **零新增服务端接口**：`/api/run`（meta+stats）与 `/api/equity`
     （dates + 归一化 equity）本来就有，各打 N 次即可。
   ★ **逐年数字一律走 `perfBuckets`**（shared/chart.js，回测详情页与实盘
     业绩页同一份）—— 自己再算一遍的话会出现「详情页说 2024 +29.43%、
     比对页说 +29.4x%」，而那不报错。
   🔴 **不做 t 检验 / 显著性**：那要【逐年独立】回测（每年重置本金），
     而这一页比的是**全程连跑**的归档。把 t 值摆在这里会诱导人拿
     路径混沌当规则证据 —— 2026-09-17 刚踩过（全程排序与逐年排序相反）。
     页面上写一句这个区别，而不是给一个看着权威的数。                       */

const CMP_MAX = 4;                  /* 上限理由是**看得清**，不是放不下 */
/* picks 页勾选的 run_id（有序：第一个是基准 A）。★ 模块级 —— showPicks
   每次排序都重渲染整张表，存局部里的话刚勾的立刻被冲掉（同 LVSORT 那条）。*/
let CMPSEL = [];

const CMP_COL = ['#7ec8a0', '#e0b050', '#00a6fb', '#b07de0'];

function cmpToggle(rid, on){
  CMPSEL = CMPSEL.filter(x => x !== rid);
  if(on) CMPSEL.push(rid);
  return CMPSEL.length <= CMP_MAX;
}

/* ============ 选择条：归档目录与「选中的规则」两页共用 ============
   🔴 **画成 `.btn`，不是 `.lvtag`。** 第一版两个入口都是暗标签，
     用户当场问「对比的入口在哪里？」——「**一个能点的东西被画成了标签**」
     这条 CLAUDE.md 里为「选股理由」记过一次，这里又犯了一遍。
   ★ 放在标题下面第一行（人看完标题就找"我能做什么"），不跟说明文字做邻居。 */
function cmpBar(msg){
  const el = $('#cmpbar');
  if(!el) return;
  const n = CMPSEL.length;
  el.innerHTML = `<a class="btn${n >= 2 ? ' on' : ''}" id="cmpgo" href="javascript:void(0)"
      title="${n < 2 ? '先勾 2 行' : '第一条是基准 A，其余与它比'}">⚖ 比对（${n}）</a>
    <span class="lvwhy">勾任意 2~${CMP_MAX} 行做比对${n ? '：' + CMPSEL.map((x, i) =>
      (i ? '' : 'A=') + x.slice(0, 15)).join(' · ') : ''}</span>
    ${n ? `<a class="btn" id="cmpclr" href="javascript:void(0)">清空</a>` : ''}
    ${msg ? `<span class="stale">${esc(msg)}</span>` : ''}`;
  const g = $('#cmpgo');
  if(g) g.onclick = () => {
    if(CMPSEL.length < 2){ cmpBar('至少勾 2 行才能比对。'); return; }
    location.hash = '#/cmp/' + encodeURIComponent(CMPSEL.join(','));
  };
  const c = $('#cmpclr');
  if(c) c.onclick = () => {
    CMPSEL = [];
    document.querySelectorAll('[data-cmp]').forEach(e => { e.checked = false; });
    cmpBar();
  };
}

/* 把页面上所有 `[data-cmp]` 勾选框接上。两页（归档目录 / 选中的规则）
   共用 —— 各写一份的话上限、提示、清空迟早分叉。 */
function cmpWire(){
  document.querySelectorAll('input[data-cmp]').forEach(e => {
    e.checked = CMPSEL.indexOf(e.dataset.cmp) >= 0;
    e.onclick = ev => {
      /* 🔴 必须 stopPropagation：行本身的 onclick 是"进这次回测的详情"，
         不拦的话勾一下就跳走了。 */
      ev.stopPropagation();
      if(!cmpToggle(e.dataset.cmp, e.checked)){
        e.checked = false; cmpToggle(e.dataset.cmp, false);
        cmpBar(`最多同时比 ${CMP_MAX} 次 —— 先取消一个再勾。`);   /* 到上限要说一句 */
        return;
      }
      cmpBar();
    };
  });
  cmpBar();
}

/* 口径逐项比 -> [{k, t, vals, same}]；`same=false` 的那几行要标红。 */
function cmpAxes(rs){
  const cost = m => {
    const c = (m.cost || {});
    const sl = c.slippage == null ? '?' : (c.slippage * 1e4).toFixed(1) + '‱';
    const cm = c.commission == null ? '?' : (c.commission * 1e4).toFixed(2) + '‱';
    const mn = c.min_commission == null ? '?' : (+c.min_commission).toFixed(0) + '元';
    const tx = c.close_tax === 'auto' ? '分段' : ((+c.close_tax || 0) * 1e4).toFixed(1) + '‱';
    return `滑点${sl} 佣金${cm} 最低${mn} 印花税${tx}`;
  };
  const par = m => Object.entries(m.params || {}).sort()
      .map(([k, v]) => k + '=' + v).join(' ') || '默认参数';
  const fp = m => String((m.data_fingerprint || {}).overall || m.data_fingerprint || '—').slice(0, 10);
  /* `crit` = 不一致会让【比较本身失效】的项。
     🔴 **策略文件与参数不在其中** —— 它们不同正是这一页存在的理由。
       把它们也算进警告里的话，每次比对都会亮一条红字，
       于是人就不看这个位置了（同「常驻一条『一切正常』的横幅等于
       教人忽略这个位置」「假告警看多了就不看告警」那两条）。
       它们只在口径卡里标成**中性**的「比对项」。 */
  const ROWS = [
    ['strategy', '策略文件', m => String(m.strategy_path || '').split('/').pop(), false],
    ['params',   '参数',     par,   false],
    ['range',    '区间',     m => (m.start || '?') + ' ~ ' + (m.end || '?'), true],
    ['cash',     '本金',     m => (+m.cash || 0).toLocaleString(), true],
    ['cost',     '成本口径', cost,  true],
    ['fp',       '数据指纹', fp,    true],
  ];
  return ROWS.map(([k, t, f, crit]) => {
    const vals = rs.map(r => f(r.meta || {}));
    return {k: k, t: t, vals: vals, same: new Set(vals).size === 1, crit: crit};
  });
}

/* 归一化基点：区间起点【之前】那一点。
   🔴 `i0 === 0` 时基点是 **1**，不是 `v[0]` —— `/api/equity` 给的已经是
     「除以初始资金」的净值，`v[0]` 已经含了首日盈亏。拿 `v[0]` 当基点等于
     **把首日的涨跌排除掉**，首日恒为 0.00%（同 `lprSlice` 的 `pbase()`，
     那次是实盘基准线第一天恒为 0 的根因）。 */
function cmpBase(v, i0){
  if(i0 <= 0) return 1;
  for(let i = i0 - 1; i >= 0; i--) if(v[i] != null) return v[i];
  return 1;
}

/* 取交集区间并各自重新归一化 -> {dates, navs, sliced} */
function cmpAlign(eqs){
  const same = eqs.every(e => e.dates.length === eqs[0].dates.length &&
                              e.dates[0] === eqs[0].dates[0] &&
                              e.dates[e.dates.length - 1] === eqs[0].dates[eqs[0].dates.length - 1]);
  if(same) return {dates: eqs[0].dates.slice(), navs: eqs.map(e => e.equity.slice()), sliced: false};
  /* 交集：起点取最大、终点取最小；再按各自的日期数组切 */
  const lo = eqs.map(e => e.dates[0]).sort().pop();
  const hi = eqs.map(e => e.dates[e.dates.length - 1]).sort()[0];
  const ds = eqs[0].dates.filter(d => d >= lo && d <= hi);
  const navs = eqs.map(e => {
    const idx = {}; e.dates.forEach((d, i) => idx[d] = i);
    const i0 = e.dates.findIndex(d => d >= lo);
    const b = cmpBase(e.equity, i0);
    return ds.map(d => (d in idx) && e.equity[idx[d]] != null ? e.equity[idx[d]] / b : null);
  });
  return {dates: ds, navs: navs, sliced: true, lo: lo, hi: hi};
}

async function showCmp(ids){
  stopPoll();
  enterView();
  ids = (ids || []).filter(Boolean).slice(0, CMP_MAX);
  const head = '<div id="cmp"><h2>⚖ 策略比对</h2>';
  if(ids.length < 2){
    $('#main').innerHTML = head + `<div class="none">至少要选 2 次回测。
      去 <a href="#/picks">★ 选中的规则</a> 勾选 2~${CMP_MAX} 行，再点「⚖ 比对」。</div></div>`;
    return;
  }
  $('#main').innerHTML = head + '<div class="none">读取中…</div></div>';
  let rs;
  try{
    rs = await Promise.all(ids.map(async id => {
      const [r, e] = await Promise.all([
        j('/api/run?id=' + encodeURIComponent(id)),
        j('/api/equity?id=' + encodeURIComponent(id))]);
      return {id: id, meta: (r || {}).meta, stats: (r || {}).stats, eq: e};
    }));
  }catch(err){
    $('#main').innerHTML = head + `<div class="none">读取失败：${esc(String(err))}</div></div>`;
    return;
  }
  /* 🔴 容器可能已经不在了（人切走了）—— 异步回调往 null 写只在控制台报，
     页面看着正常（CLAUDE.md 里 renderChart 那条）。判据用容器在不在。 */
  if(!$('#cmp')) return;
  const bad = rs.filter(r => !r.meta || !r.eq);
  if(bad.length){
    $('#main').innerHTML = head + `<div class="none">这几次回测取不到数据（可能已被清理）：
      ${bad.map(b => esc(b.id)).join('、')}</div></div>`;
    return;
  }
  cmpRender(rs);
}

function cmpRender(rs){
  const ax = cmpAxes(rs);
  const al = cmpAlign(rs.map(r => r.eq));
  const diff = ax.filter(a => !a.same && a.crit);      /* 只有这些才报警 */
  const nm = r => String((r.meta || {}).strategy_path || '').split('/').pop().replace(/\.py$/, '');
  /* 图例标签：`A · pb_pct=0.5` —— **只列与基准不同的那几个参数**。
     把整条参数串印上去的话，两条图例就把一行占满，而真正要认的
     "这条是哪个配置"反而被淹没（同「能进 tooltip 的就别占列」）。 */
  const p0 = rs[0].meta.params || {};
  const lab = rs.map((r, i) => {
    const pp = r.meta.params || {};
    const d = Object.keys(pp).concat(Object.keys(p0))
      .filter((k, n, a) => a.indexOf(k) === n)
      .filter(k => String(pp[k]) !== String(p0[k]))
      .sort().map(k => k + '=' + (pp[k] === undefined ? '默认' : pp[k]));
    return String.fromCharCode(65 + i) + ' · ' + nm(r) + (d.length ? ' ' + d.join(' ') : '');
  });

  /* ---- ① 口径卡 + ② 警告条 ---- */
  const axHtml = `<div class="pw"><table class="pkt cmpax">
    <tr><th class="tx">口径</th>${rs.map((r, i) =>
      `<th class="tx"><span class="cmpdot" style="background:${CMP_COL[i]}"></span>${
        i === 0 ? '<b>A（基准）</b>' : String.fromCharCode(65 + i)} · ${esc(r.id)}</th>`).join('')}</tr>
    ${ax.map(a => `<tr class="${a.same ? '' : (a.crit ? 'cmpbad' : 'cmpdif')}"><td class="tx">${esc(a.t)}</td>
      ${a.vals.map(v => `<td class="tx">${esc(v)}</td>`).join('')}</tr>`).join('')}
  </table></div>`;
  const warn = diff.length ? `<div class="warn cmpwarn">⚠ 这 ${rs.length} 次回测的
      <b>${diff.map(d => esc(d.t)).join('、')}</b> 不一致 —— 直接比数字会得出错误结论。
      ${diff.some(d => d.k === 'cost') ? '<b>成本口径不同是最危险的一条</b>：本项目两次栽在拿滑点 0 的数字比含滑点的基准。' : ''}
      ${diff.some(d => d.k === 'range') ? '区间不同 —— 下面的图已按<b>交集区间</b>重新归一化，但 KPI 表仍是各自<b>全程</b>的口径。' : ''}
      ${diff.some(d => d.k === 'fp') ? '数据指纹不同 —— 两次跑在不同的数据上（面板被修正过）。' : ''}
      要严格可比，请用<b>同一区间、同一成本</b>重跑一次。</div>` : '';

  /* ---- ③ KPI 对比表（差值相对 A）---- */
  const KPI = [
    ['total_return', '总收益',  v => pct(v, 1), 1],
    ['annual_return', '年化',   v => pct(v, 2), 1],
    ['max_drawdown', '最大回撤', v => pct(v, 2), -1],
    ['sharpe',       '夏普',    v => fmtN(v, 3), 1],
    ['calmar',       '卡玛',    v => fmtN(v, 2), 1],
    ['n_trades',     '平仓笔数', v => v == null ? '—' : v, 0],
    ['win_rate',     '胜率',    v => pct(v, 2), 1],
    ['turnover_per_year', '年换手', v => fmtN(v, 2), 0],
    ['fee_paid',     '费用合计', v => v == null ? '—' : Math.round(v).toLocaleString(), 0],
  ];
  const sv = (r, k) => {
    const s = r.stats || {};
    if(k === 'total_return' && s[k] == null){
      const e = r.eq.equity; return e && e.length ? e[e.length - 1] - 1 : null;
    }
    return s[k];
  };
  const kpiHtml = `<div class="pw"><table class="pkt cmpkpi">
    <tr><th class="tx">指标</th>${rs.map((r, i) =>
      `<th><span class="cmpdot" style="background:${CMP_COL[i]}"></span>${String.fromCharCode(65 + i)}</th>`).join('')}
      ${rs.length === 2 ? '<th>B − A</th>' : ''}</tr>
    ${KPI.map(([k, t, f, dir]) => {
      const vs = rs.map(r => sv(r, k));
      let d = '';
      if(rs.length === 2 && vs[0] != null && vs[1] != null && typeof vs[0] === 'number'){
        const gap = vs[1] - vs[0];
        /* 差值的"好坏"按指标方向给：回撤是越小越好 -> 差为负才算好。
           dir=0 的（笔数/换手/费用）不上色 —— 它们没有"越大越好"。 */
        const good = dir === 0 ? null : (dir > 0 ? gap : -gap);
        /* 🔴 `总收益` 的差不用 pp：4855% vs 7525% 差「+2670pp」读不出任何东西。
           十年累积要看的是**期末净值的倍数比**（×1.54），那才是"多赚了多少"。 */
        const txt = k === 'total_return'
          ? ((1 + vs[1]) / (1 + vs[0])).toFixed(2) + '×'
          : (k === 'n_trades' || k === 'fee_paid')
          ? (gap > 0 ? '+' : '') + Math.round(gap).toLocaleString()
          : (k === 'sharpe' || k === 'calmar' || k === 'turnover_per_year')
            ? (gap > 0 ? '+' : '') + gap.toFixed(2)
            : (gap > 0 ? '+' : '') + (gap * 100).toFixed(2) + 'pp';
        d = `<td><b style="color:${good == null ? 'var(--fg)' : upc(good)}">${txt}</b></td>`;
      }else if(rs.length === 2){ d = '<td>—</td>'; }
      return `<tr><td class="tx">${t}</td>${vs.map(v =>
        `<td>${f(v)}</td>`).join('')}${d}</tr>`;
    }).join('')}
  </table></div>`;

  /* ---- ④ 主图 + 回撤副图 ---- */
  const note = al.sliced
    ? `图按<b>交集区间 ${esc(al.lo)} ~ ${esc(al.hi)}</b>画，并按该区间起点<b>重新归一化</b>
       —— 不重新归一化的话 y 轴写的百分比其实是各自全程的收益。
       KPI 表仍是各自<b>全程</b>的口径（笔数/费用这些没法按区间切）。`
    : `三项口径（区间/本金/成本）一致时才可以直接读差值。`;

  $('#main').innerHTML = `<div id="cmp">
    <h2>⚖ 策略比对（${rs.length} 次）</h2>
    ${warn}
    ${axHtml}
    ${kpiHtml}
    <div class="lvsec"><h3>净值与回撤</h3>
      <div class="note">${note}</div>
      <div id="cmpc1"></div><div id="cmpc2"></div></div>
    <div class="lvsec"><h3>逐年收益 / 年内回撤</h3>
      <div class="note">🔴 这是<b>全程连跑</b>的年度切片（资金滚动、年初带着持仓），
        <b>不是逐年独立回测</b>（每年重置本金、年初空仓）。判「规则好不好」只能用后者
        —— 全程切片测的是路径混沌，同一年两种口径能差很多。
        年内回撤的峰值在年初重置。</div>
      <div class="lvtags" id="cmpymode">
        <span class="lvtag on" data-ym="abs">各自收益</span>
        <span class="lvtag" data-ym="diff">对比基准 A 的差</span></div>
      <div id="cmpyb1"></div><div id="cmpysum"></div><div id="cmpyb2"></div>
      <div class="note" id="cmpyrt"><span class="lvtag" id="cmpyrtb">展开逐年数字表 ›</span></div>
      <div id="cmpyr" style="display:none"></div></div>
    <div class="note">← <a href="#/picks">回 ★ 选中的规则</a></div>
  </div>`;

  const ser = rs.map((r, i) => ({n: lab[i], v: al.navs[i], c: CMP_COL[i]}));
  lineChart($('#cmpc1'), ser, {dates: al.dates, title: '归一净值（起点 = 1）', h: 340});
  /* 回撤副图：值是 `回撤+1`，所以三个选项要**一起**给 —— `pctAxis` 把纵轴印成
     百分数、`zero:1` 是水面、`hiCap:1` 把顶端钳在 0（不钳的话 lineChart 会多留
     6%，最高刻度印成 `+0.4%`，而"比历史最高还高"没有意义）。
     🔴 与详情页**不同的一点**：这里不填面积（`fill`）—— 2~4 条水下区域叠在
       一起会糊成一片，看不出是谁跌得深。单条时填充是对的，多条时不是。 */
  const dds = al.navs.map((v, i) => ({n: lab[i], v: drawdownSeries(v, 1).map(x => x == null ? null : x + 1),
                                      c: CMP_COL[i], w: 1.3}));
  /* ★ 副图**不再印一遍图例**：与主图是同一组系列，第二行纯噪声。
     这一页颜色已经出现在口径卡表头、KPI 表头、主图图例三处了
     （同「同一份信息只在一个地方看」那条）。 */
  lineChart($('#cmpc2'), dds, {dates: al.dates, title: '回撤（0 = 创新高）', h: 190,
                               pctAxis: true, hiCap: 1, zero: 1, legend: false});

  /* ---- ⑤ 逐年表 ---- */
  /* 🔴 走 `perfBuckets` —— 与回测详情页的年度热力图同一份实现。
     自己算一遍的话两页对同一次回测给出不同的年度收益，而那不报错。 */
  const bk = al.navs.map(v => perfBuckets(al.dates, v, null).years);
  const years = Array.from(new Set(al.dates.map(d => d.slice(0, 4)))).sort();

  /* ---- 逐年：两块柱状图，共用 x 轴（年）----
     🔴 **不画折线**：逐年是离散量，折线会在 2016 与 2017 之间画出一段
       不存在的"过程"。
     🔴 **也不把收益与回撤镜像进同一块**：亏损年那两根**同向朝下**
       （实测 2022 pb1.0：收益 −4.52%、回撤 −26.14%），只能靠颜色分，
       而颜色在这一页已经被"哪个配置"占用了；何况两者量纲差一个数量级
       （收益 −4.5%~+108.9% / 回撤 −8%~−40%），同轴会把回撤压成一小截。
     ★ 上下两块的结构与上面那张「净值 + 回撤副图」一致 —— 同一页里
       两处一致，而且 barChart 与 lineChart 的 W/L/R 相同，左边界天然对齐。 */
  const ySer = (key, sgn) => rs.map((r, i) => ({
    n: lab[i], c: CMP_COL[i],
    v: years.map(y => (bk[i][y] && bk[i][y][key] != null) ? bk[i][y][key] * sgn : null),
  }));
  /* ---- 两种看法，一个开关 ----
     🔴 **「各自收益」看不出"B 比 A 好在哪几年"** —— 三根柱子高低相近时，
       眼睛比不出 5pp 的差；而这一页存在的理由正是那个差。
       2026-09-17 分析 pb1.0 时最关键的事实是「优势几乎全来自 2024 单年」，
       在绝对值图上要逐年目测两根柱子的高度差才看得出来，
       而在差值图上**那一根柱子高得离谱**，一眼就是结论。
     ★ 差值图里**基准 A 不画**（它恒为 0，画一排贴着零线的空柱子是噪声），
       并在标题里写明"正 = 比 A 好"，免得把符号读反。 */
  const drawYear = mode => {
    const diff = mode === 'diff' && rs.length > 1;
    const mk = (key, sgn) => {
      const base = years.map(y => (bk[0][y] && bk[0][y][key] != null) ? bk[0][y][key] * sgn : null);
      return rs.map((r, i) => ({
        n: lab[i], c: CMP_COL[i],
        v: years.map((y, j) => {
          const v = (bk[i][y] && bk[i][y][key] != null) ? bk[i][y][key] * sgn : null;
          if(!diff) return v;
          return (v == null || base[j] == null) ? null : v - base[j];
        }),
      })).filter((_s, i) => !diff || i > 0);
    };
    barChart($('#cmpyb1'), years, mk('ret', 1),
             {title: diff ? '逐年收益 − 基准 A（正 = 比 A 赚得多）' : '逐年收益', h: 210});
    /* 🔴🔴 **把"数柱子"会得出的那几个数，页面自己印出来 —— 带着口径。**
       2026-09-17 实测：用户看这张图数出「pb=1 大部分年份都赢」，而那是
       **全程连跑切片**的 7/11；同一对配置按【逐年独立】只有 5/11，
       其中 2017 与 2020 两年**符号相反**（2020 差 41pp，全来自路径）。
       ★ 根因不是他读错了，是**页面请人数柱子，却把口径塞在一段灰字里** ——
         注释输给了图。**人自己数出来的数字不带口径，页面印出来的可以带。**
       ★ 这里只印**描述性**的三个数（胜/均差/去掉最好那年），**不印 t 值**：
         t 看着权威，会把路径混沌坐实成规则证据（同「不做显著性」那条）。
         而「去掉最好的一年」恰恰是防"单年主导"的那一个，最该印。 */
    $('#cmpysum').innerHTML = !diff ? '' : `<div class="note cmpsum">${
      mk('ret', 1).map(s2 => {
        const v = s2.v.filter(x => x != null);
        if(!v.length) return '';
        const w = v.filter(x => x > 0).length;
        const m = v.reduce((a2, b2) => a2 + b2, 0) / v.length;
        const bi = v.indexOf(Math.max(...v));
        const rest = v.filter((_x, i) => i !== bi);
        const m2 = rest.length ? rest.reduce((a2, b2) => a2 + b2, 0) / rest.length : null;
        const byr = years[s2.v.indexOf(Math.max(...v))];
        return `<div><span class="cmpdot" style="background:${s2.c}"></span>${esc(s2.n)}
          　胜 <b>${w}/${v.length}</b>　均差 <b style="color:${upc(m)}">${
            (m > 0 ? '+' : '') + (m * 100).toFixed(2)}pp</b>
          　去掉最好的 ${byr}：<b style="color:${upc(m2)}">${
            m2 == null ? '—' : (m2 > 0 ? '+' : '') + (m2 * 100).toFixed(2) + 'pp'}</b></div>`;
      }).join('')}
      <div style="margin-top:4px">🔴 这三个数是<b>全程连跑切片</b>上数出来的，
        <b>不能用来判规则</b> —— 同一对配置按【逐年独立】（每年重置本金、年初空仓）
        重跑，胜负数与<b>符号</b>都可能变（实测 11 年里有 2 年翻号，其中一年差 41pp）。
        而且 11 个样本里「胜 7」抛硬币也有 27% 的概率出现。
        <b>真正稳的判据是「去掉最好的那一年还剩多少」</b>。</div></div>`;
    barChart($('#cmpyb2'), years, mk('mdd', -1),
             {title: diff ? '年内最大回撤 − 基准 A（负 = 比 A 跌得更深）' : '年内最大回撤',
              h: 170, legend: false});
  };
  drawYear('abs');
  document.querySelectorAll('#cmpymode [data-ym]').forEach(e => {
    e.onclick = () => {
      document.querySelectorAll('#cmpymode [data-ym]').forEach(x =>
        x.classList.toggle('on', x === e));
      drawYear(e.dataset.ym);
    };
  });
  const _tb = $('#cmpyrtb');
  if(_tb) _tb.onclick = () => {
    /* ★ 表格**不删只收起**：图看形状、表看精确值，两件事
       （同「只删不补是弄丢信息」）。 */
    const box = $('#cmpyr'), on = box.style.display === 'none';
    box.style.display = on ? '' : 'none';
    _tb.textContent = on ? '收起逐年数字表 ›' : '展开逐年数字表 ›';
  };
  const cellv = (o, k, d) => o && o[k] != null ? pct(o[k] * (k === 'mdd' ? -1 : 1), d) : '—';
  $('#cmpyr').innerHTML = `<div class="pw"><table class="pkt cmpyr">
    <tr><th class="tx">年</th>${rs.map((r, i) =>
      `<th colspan="2"><span class="cmpdot" style="background:${CMP_COL[i]}"></span>${
        String.fromCharCode(65 + i)}</th>`).join('')}${rs.length === 2 ? '<th>收益差</th>' : ''}</tr>
    <tr><th></th>${rs.map(() => '<th class="sub">收益</th><th class="sub">年内回撤</th>').join('')}
      ${rs.length === 2 ? '<th class="sub">B − A</th>' : ''}</tr>
    ${years.map(y => {
      const os = bk.map(b => b[y]);
      let d = '';
      if(rs.length === 2){
        const g = (os[0] && os[1] && os[0].ret != null && os[1].ret != null)
          ? os[1].ret - os[0].ret : null;
        d = `<td><b style="color:${g == null ? 'var(--fg)' : upc(g)}">${
          g == null ? '—' : (g > 0 ? '+' : '') + (g * 100).toFixed(2) + 'pp'}</b></td>`;
      }
      return `<tr><td class="tx">${y}</td>${os.map(o =>
        `<td><b style="color:${o && o.ret != null ? upc(o.ret) : 'var(--fg)'}">${cellv(o, 'ret', 2)}</b></td>
         <td class="cost">${cellv(o, 'mdd', 1)}</td>`).join('')}${d}</tr>`;
    }).join('')}
  </table></div>`;
}
