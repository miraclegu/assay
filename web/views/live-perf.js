/* 实盘业绩【独立页】 #/live/<id>/perf
   —— 回测详情页能看的曲线与收益表，实盘也要能看。

   信息架构：主视图仍然只有「今日待办 + 当前持仓」两块（selftest 钉着），
   业绩明细是**复盘时才看**的，所以放独立页、从 KPI 板的「累计收益 ›」点进来。

   🔴 **资金曲线与收益曲线是两条不同的线，不能混**：
     · 资金曲线 = 总资产（含入金）—— 回答"我账上有多少钱"
     · 收益曲线 = TWR 净值      —— 回答"我的钱涨了多少"
     入金那天总资产会跳一截，拿它当收益就是把入金算成赚的
     （这正是 TWR 存在的理由）。所以各自把口径写在图下面。
   ★ 净值与逐日金额由**服务端**给（lv/perf.py 的 nav / day_rets / day_pnls）：
     现金流与切区间的规则都在那边，前端拿 equity 自己推就是第二份 TWR 实现。

   ★ 画图与分桶用 shared/chart.js（lineChart / perfBuckets / calGrid /
     drawdownSeries / _hcol / _legend）—— 与回测详情页**同一份**实现。

   🔴 别把图块套在 `.pane` 里：那是回测详情页的页签容器，样式表里写着
     display:none（要靠 .on 打开）。套了之后 SVG 确实画出来了、DOM 里也在，
     **屏幕上什么都没有且不报错**（第一版就这么挂的）。 */

let LPD = null;          // 当前账户的业绩数据（全程）
let LPS = null;          // 按区间裁剪 + 重新归一化后的数据
/* 当前曲线。★ 默认 **收益曲线** —— 打开业绩页第一眼要回答的是
   "我赚了多少 / 跑赢基准了吗"，而不是"账上有多少钱"（那在 KPI 板里
   已经有了）。资金与回撤是往后翻的。 */
let LPC = 'nav';         // nav=收益 | eq=资金 | dd=回撤
/* 策略曲线（完全照做）与每期执行差异 —— 见 lv/bench.py。
   🔴 实盘与绑定策略**必然有差异**，两条曲线摆一起才看得出执行的代价。 */
let LPBCH = null, LPXD = null, LPBCH_NOTE = '';
/* 选中的基准（收益曲线上叠加）。
   ★ **单选**：同时看多个基准没有意义 —— 三条线以上就看不清，而"我的策略
     跑赢谁"一次问一个就够。选新的自动换掉旧的，点已选中的取消。
   ★ **记住选择**（localStorage）：刷新一次就没了的话，每次进来都要重选，
     而这是"每天看同一个对比"的场景（同侧栏收起 `lvfold` 那条）。
   🔴 存的值要**校验**再用：localStorage 里可能是上个版本留下的代码、
     或者手改过的垃圾。不校验就会拿一个取不到数据的 symbol 去画，
     表现是"选中了但没有线"，而它不报错。 */
/* 🔴 **策略线也是「基准」的一种**，与指数共用**同一个单选槽**。
   头一版把它做成常显 + 一个独立开关，实测两个毛病：颜色与上证撞了
   （都是 LPB_COL[0]），而且**关不掉** —— 而"想比哪个"因人而异，
   三条线以上就看不清了（同「默认一个都不勾」那条）。
   ★ 值 `strat` 与指数 code 走同一个 localStorage key：这样"当前在比什么"
     只有一处状态，不会出现"既选了上证又开着策略"这种要额外记的组合。 */
const LPB_STRAT = 'strat';

let LPB = (() => {
  try {
    const v = localStorage.getItem('lvbench') || '';
    return (v === LPB_STRAT || /^(sh|sz)\d{6}$/.test(v)) ? [v] : [];
  } catch (e) { return []; }
})();

function lpbSet(code){
  LPB = code ? [code] : [];
  try {
    if(code) localStorage.setItem('lvbench', code);
    else localStorage.removeItem('lvbench');
  } catch (e) { /* 隐私模式下写不了 —— 不该因此打挂整页 */ }
}
/* 请求时一次取全部（见 showPerf 里的注释）。真正可选哪些由**服务端**给
   （`o.benchmarks`）—— 本地缺哪个指数只有服务端知道，前端硬编码清单的话
   会列出取不到数据的选项，而"点了什么都不出来"比不给这个选项更糟。 */
const LPB_ALL = ['sh000001','sz399006','sh000688','sh000905','sh000852',
                 'sz399303','sz399101','sz399316','sz399634'].join(',');
const LPB_COL = ['#7ec8a0','#c88ad0','#d0a05b','#6fa8d8','#d07a7a',
                 '#9db85b','#5bb0b8','#b89d6f','#8f9bd8'];

function showPerf(aid){
  const b = $('#main');
  $('#cat').innerHTML = ''; $('#vp').innerHTML = '';
  b.innerHTML = '<div class="none">读取中…（要重放整条权益曲线）</div>';
  /* ★ 一次把**全部**基准取回来，之后切换只改显示 —— 零额外请求。
     每次勾选都重新请求的话，服务端要重放整条权益曲线（几秒），
     而勾选是随手点的动作，那个延迟会让人以为卡住了。 */
  /* ★ 三份并行：权益 + 策略曲线 + 执行差异。策略曲线要跑一次回测
     （服务端三层缓存），拉不到就只画实际那条 —— 不让它拖住整页。 */
  Promise.all([
    j('/api/live/bench?id=' + encodeURIComponent(aid)).catch(e => ({error: String(e)})),
    j('/api/live/exec_diff?id=' + encodeURIComponent(aid)).catch(() => ({items: []})),
  ]).then(([bch, xd]) => {
    LPBCH = bch; LPXD = xd;
    /* 🔴 **竞态**：这三份是并行拉的，谁先回来不定。头一版写
       `if(LPD) render...` —— 而这一支常常比权益那支先回来，那时 LPD
       还是 null，于是什么都不渲染；之后权益回来只调了 renderChart，
       **执行差异块永远是空的**（数据在 LPXD 里躺着，且不报错）。
       所以两支都要调，各自判自己依赖的数据齐没齐。 */
    if(LPD){ renderChart(aid); renderExec(aid); }
  });
  j('/api/live/equity?id=' + encodeURIComponent(aid) + '&bench=' + LPB_ALL).then(o => {
    LPD = o; renderPerf(aid);
  }).catch(e => { b.innerHTML = '<div class="none">' + esc(e) + '</div>'; });
}

function renderPerf(aid){
  const o = LPD, st = (o && o.stats) || {};
  const b = $('#main');
  if(!o || !o.dates || !o.dates.length){
    b.innerHTML = '<div class="none">还没有权益曲线 ——'
      + '录入第一笔成交之后就有了</div>';
    return;
  }
  const n = o.dates.length;
  const sgn = x => x == null ? '' : (x >= 0 ? '+' : '');
  const col = upc;
  const pc = x => x == null ? '—' : sgn(x) + (x * 100).toFixed(2) + '%';

  /* 🔴 **这里不再自己放「‹ 返回」。** 顶栏（`pageHead` -> `backLink`）已经有
       一个，而且是**绑了事件**的那个。这里原来也吐一份同样的 HTML 却从没调
       `wireBack()` —— 于是页面上有两个一模一样的按钮，**下面那个点了没反应**
       （2026-09-14 用户报的）。更糟的是两个都叫 `id="goback"`：`wireBack()`
       用 `$('#goback')` 只取第一个，所以就算补调一次也只会把顶栏那个重绑一遍，
       body 这个永远是死的 —— **同一个 id 出现两次本身就是 bug**。
     ★ 判据在 selftest：全页 `#goback` 只许有一个，且它必须绑上了 onclick。 */
  b.innerHTML =
    `<div class="ttl">业绩 · ${esc(aid)}
        <span class="lvwhy">${esc(o.dates[0])} ~ ${esc(o.dates[n-1])}
        · ${n} 个交易日</span></div>`
    /* ★ 用 shared/common.js 的 cell() —— 与实盘 KPI 板同一个件。
         card() 是回测详情页专用的（run-detail.js），不跨过去用。 */
    /* 🔴 这四格是**全程**（开户至今）的口径，而下面的图按所选区间画 ——
         不标"全程"的话两个数摆在同一屏上看着像对不上（区间收益写在
         图下面的 rgNote 里）。 */
    + `<div class="kpi" style="margin-bottom:10px">
        ${cell('累计收益 (TWR)<span class="lvwhy"> · 全程</span>',
               `<span style="color:${col(st.twr)}">${pc(st.twr)}</span>`,
               st.pnl_total == null ? 'TWR'
                 : (sgn(st.pnl_total) + num(st.pnl_total, 2) + ' 元 · TWR'))}
        ${cell('加权年化<span class="lvwhy"> · 全程</span>',
               `<span style="color:${col(st.twr_annual)}">${
                 st.twr_annual == null ? '—' : pc(st.twr_annual)}</span>`,
               st.twr_annual == null ? '不足 20 个交易日不外推' : 'TWR 年化')}
        ${cell('最大回撤<span class="lvwhy"> · 全程</span>', st.max_drawdown == null ? '—'
                 : (st.max_drawdown * 100).toFixed(2) + '%',
               st.max_drawdown_at ? '最深 ' + esc(st.max_drawdown_at) : '')}
        ${cell('当前回撤<span class="lvwhy"> · 全程</span>', st.drawdown_now == null ? '—'
                 : (st.drawdown_now * 100).toFixed(2) + '%', '距历史最高')}
       </div>`
    /* 🔴 **主图切换（收益 / 资金），回撤永远在下方当副图。**
         原来三条曲线并列成三个页签 —— 而回撤不是与另两条并列的"第三种
         看法"，它是**对当前这条曲线的注解**（"这一段跌下去有多深"）。
         放进页签的代价是：要看"那个坑有多深"必须切走，而切走之后上面
         那条曲线就不在眼前了，只能靠记。副图共用 x 轴，一眼就能对上。
       ★ 两张图都走 `lineChart`，而它的 W/L/R 是固定的（1160/54/16）、
         SVG 又是 `width:100%` 等比缩放 —— 所以**同宽即同刻度**，
         x 轴天然对齐，不需要额外对位代码。 */
    + '<div id="lp_rgwrap"></div>'
    + '<div class="lpbar" id="lp_tab"></div>'
    + '<div id="lp_chart" class="lpbox"></div>'
    + '<div id="lp_dd" class="lpbox lpsub"></div>'
    + '<div id="lp_tbl" class="lpbox"></div>'
    /* ★ 执行差异放在收益表之后：它是复盘时才看的，而「我涨了多少」是第一眼要看的。 */
    + '<div id="lp_exec" class="lpbox"></div>'
    /* 每日持仓 / 交易记录：排在执行差异之后 —— 前面那几块回答"我赚了多少"，
       这两块是**明细**，翻到下面才看（同「能进 tooltip 的就别占列」的取舍）。 */
    + '<div id="lp_hold" class="lpbox"></div>'
    + '<div id="lp_trip" class="lpbox"></div>';
  /* 🔴 曲线与收益明细都用**裁剪后**的数据（LPS），KPI 板用**全程**
     （上面那四格标着 TWR/最大回撤，是开户至今的口径）——
     两者混着看会以为对不上，所以区间条下面单独给这一段的收益。 */
  if(o.nav && o.day_pnls){
    LPS = lprSlice(o);
    const rw = $('#lp_rgwrap');
    if(rw) rw.innerHTML = lprBar(o, aid);      /* 同上：人可能已经走开 */
    lprBind($('#lp_rgwrap'), aid);
  } else {
    LPS = null;
  }
  renderChart(aid);
  /* ★ 执行差异也在这里调一次：它的数据可能**先**到（见上面那条竞态）。 */
  renderExec(aid);
  renderPerfTable(aid);
  /* 每日持仓 / 交易记录：各自取数、各自渲染。
     ★ 不塞进上面那个 Promise.all —— 它们只是明细，慢一点无妨；
       而让整页等它们会把"我赚了多少"这个第一眼要看的东西也推后。
     ★ 翻页时只重画自己那一块（`renderHoldings/renderTrips` 自己重取），
       不碰曲线 —— 曲线要重放整条权益，白重放一次是纯浪费。 */
  LPH.off = 0; LPT.off = 0;
  renderHoldings(aid);
  renderTrips(aid);
}

/* ============ 时间区间 ============
   ★ 默认 **今年以来**（YTD）—— 打开看的是"今年怎么样"，而不是"开户至今"
     （后者在 KPI 板里已经有了）。
   🔴 起点晚于今年年初时就从**实盘起点**开始：账户 09-01 才开户，
     强行从 01-01 画会有 8 个月的空白，看着像数据缺了。
   ★ 区间选择也**记住**（localStorage），与基准同一条理由：
     这是"每天看同一段"的场景。 */
const LPR_OPTS = [
  ['ytd', '今年以来'], ['m1', '近一月'], ['m3', '近三月'],
  ['m6', '近六月'], ['y1', '近一年'], ['y3', '近三年'],
  ['all', '全部'], ['cus', '自定义'],
];
let LPR = (() => {
  try {
    const v = JSON.parse(localStorage.getItem('lvrange') || 'null');
    if(v && LPR_OPTS.some(o => o[0] === v.k)) return v;
  } catch (e) { /* 存的是垃圾就用默认 */ }
  return {k: 'ytd', a: '', b: ''};
})();

function lprSet(v){
  LPR = v;
  try { localStorage.setItem('lvrange', JSON.stringify(v)); }
  catch (e) { /* 隐私模式下写不了 —— 不该因此打挂整页 */ }
}

/* 区间 -> [起, 止] 两个日期字符串（含端点）。
   🔴 **按自然日往前推，然后落到交易日轴上** —— 直接用"最后 N 个交易日"
     的话，"近一月"会因为节假日多少而漂（春节那个月只有 15 个交易日）。
   ★ 起点**不早于账户第一天**：账户 09-01 开户，"近一年"就是 09-01 至今，
     而不是画 10 个月的空白。 */
function lprSpan(dates){
  const last = dates[dates.length - 1], first = dates[0];
  if(LPR.k === 'all') return [first, last];
  if(LPR.k === 'cus'){
    const a = LPR.a && LPR.a >= first ? LPR.a : first;
    const b = LPR.b && LPR.b <= last ? LPR.b : last;
    return a <= b ? [a, b] : [first, last];
  }
  if(LPR.k === 'ytd') {
    const y0 = last.slice(0, 4) + '-01-01';
    return [y0 > first ? y0 : first, last];
  }
  const M = {m1: 1, m3: 3, m6: 6, y1: 12, y3: 36}[LPR.k] || 0;
  const d = new Date(last + 'T00:00:00Z');
  d.setUTCMonth(d.getUTCMonth() - M);
  const a = d.toISOString().slice(0, 10);
  return [a > first ? a : first, last];
}

/* 把所有序列裁到区间，并把净值/基准**按区间起点重新归一化**。
   🔴 不重新归一化的话，"近一月"那段画出来仍然是从开户至今的累计 ——
     y 轴写着 +1.7% 而它其实是三个月的收益，而那不报错。 */
function lprSlice(o){
  const [a, b] = lprSpan(o.dates);
  const i0 = o.dates.findIndex(d => d >= a);
  let i1 = o.dates.length - 1;
  while(i1 > i0 && o.dates[i1] > b) i1--;
  const cut = arr => (arr || []).slice(i0, i1 + 1);
  const dates = cut(o.dates);
  const nav = cut(o.nav);
  /* 基点 = 区间起点的**前一天**净值（区间外那一点）—— 与 TWR 的
     "起点是前一交易日"同一条纪律：用区间首日自己做基点会把首日
     的涨跌排除在这段收益之外。区间从第一天开始时用 1.0（开户起点）。 */
  const nb = i0 > 0 ? o.nav[i0 - 1] : 1.0;
  const bench = {};
  for(const k in (o.bench || {})){
    const v = cut(o.bench[k]);
    const bb = i0 > 0 ? o.bench[k][i0 - 1] : (v.find(x => x != null) || 1);
    bench[k] = bb ? v.map(x => (x == null ? null : x / bb)) : v;
  }
  return {dates: dates, equity: cut(o.equity),
          nav: nb ? nav.map(x => x / nb) : nav,
          day_rets: cut(o.day_rets), day_pnls: cut(o.day_pnls),
          bench: bench, benchmarks: o.benchmarks, stats: o.stats,
          i0: i0, i1: i1, span: [dates[0], dates[dates.length - 1]],
          full: o.dates.length};
}

function lprBar(o, aid){
  const [a, b] = LPR.k === 'cus' ? lprSpan(o.dates) : ['', ''];
  return '<div class="lpbar" id="lp_rg"><span class="lvwhy">区间</span>'
    + LPR_OPTS.map(([k, t]) => `<a href="#" class="lpr${LPR.k === k ? ' on' : ''}"
        data-r="${k}">${t}</a>`).join('')
    + (LPR.k === 'cus'
       ? `<input id="lp_ra" class="syf" size="10" value="${esc(a)}"
            placeholder="开始"> <input id="lp_rb" class="syf" size="10"
            value="${esc(b)}" placeholder="结束">`
       : '')
    + '</div>';
}

function lprBind(el, aid){
  el.querySelectorAll('a.lpr').forEach(x => x.onclick = ev => {
    ev.preventDefault();
    lprSet({k: x.dataset.r, a: LPR.a, b: LPR.b});
    renderPerf(aid);
  });
  const ra = el.querySelector('#lp_ra'), rb = el.querySelector('#lp_rb');
  [ra, rb].forEach(inp => { if(!inp) return;
    inp.onchange = () => {
      lprSet({k: 'cus', a: (ra.value || '').trim(), b: (rb.value || '').trim()});
      renderPerf(aid);
    };
  });
}

/* 这一段的收益 —— **必须单独给**：KPI 板那四格是开户至今的口径，
   而图画的是所选区间，两个数摆在同一屏上看着像对不上。 */
function rgNote(o){
  if(!o || !o.dates || o.dates.length < 2) return '';
  const r = o.nav ? o.nav[o.nav.length - 1] - 1 : null;
  const p = (o.day_pnls || []).reduce((a, b) => a + (b || 0), 0);
  const sg = x => (x >= 0 ? '+' : '');
  return `<div class="note">区间 <b>${esc(o.dates[0])} ~ ${
    esc(o.dates[o.dates.length - 1])}</b>（${o.dates.length} 个交易日${
    o.full && o.full !== o.dates.length ? ' / 全程 ' + o.full : ''}）　`
    + (r == null ? '' : `这一段：<b style="color:${
        upc(r)}">${sg(r)}${(r * 100).toFixed(2)}%</b>`)
    + `　<b style="color:${upc(p)}">${
        sg(p)}${num(Math.round(p), 0)}</b> 元</div>`;
}

function renderChart(aid){
  const o = LPS || LPD, st = (LPD && LPD.stats) || {};
  const bar = $('#lp_tab'), el = $('#lp_chart');
  /* 🔴 **人已经走开了。** 这几个渲染都是异步回调（权益要重放整条曲线、
     策略曲线要跑一次回测），回来时页面可能已经切走 —— 那时容器是 null，
     `null.innerHTML = ...` 直接抛。selftest 里就是这么抓到的：先访问首页
     再进 perf 页，上一页的回调回来就崩（而它只在控制台里报，页面看着正常）。
     ★ 判据用容器存在与否，不是 `location.hash` —— 同一个 hash 下也可能
       重渲染过（换区间/换基准），而容器在不在是当下的事实。 */
  if(!bar || !el || !o) return;
  const pc = x => x == null ? '—' : (x >= 0 ? '+' : '') + (x * 100).toFixed(2) + '%';
  /* 🔴 旧服务端没有 nav/day_pnls（改了 assay/*.py 但没重启 serve.py）——
     直接用会 `undefined[0]` 整页崩。说清原因比崩掉好。 */
  if(!o.nav || !o.day_pnls){
    bar.innerHTML = '';
    el.innerHTML = '<div class="lvwarn"><b>服务端还没有 nav / day_pnls 字段</b>'
      + '<br>改了 <code>assay/*.py</code> 之后要重启：'
      + '<code>python3 serve.py --restart</code>'
      + '（Python 模块只在进程启动时加载一次）</div>';
    return;
  }
  const dd = drawdownSeries(o.equity, st.start_equity || o.equity[0]);
  /* 累计金额 = 逐日金额的前缀和。★ 不能用 equity − 起点：那含入金。 */
  const cum = []; let acc = 0;
  o.day_pnls.forEach(v => { acc += (v || 0); cum.push(Math.round(acc)); });
  const money = v => (v >= 0 ? '+' : '') + num(v, 0) + ' 元';

  /* 顺序 = 看的顺序：收益（默认）-> 资金。**回撤不在这里** —— 它是副图。
     🔴 旧状态兜底：上一版 LPC 可能是 'dd'（页签已经没了），不归一的话
        会落进 else 分支画出收益曲线却没有任何页签高亮，看着像坏了。 */
  if(LPC !== 'eq') LPC = 'nav';
  const TABS = [['nav', '收益曲线'], ['eq', '资金曲线']];
  bar.innerHTML = '<span class="lvwhy">曲线</span>'
    + TABS.map(([k, t]) => `<a href="#" class="lpc${LPC === k ? ' on' : ''}"
        data-c="${k}">${t}</a>`).join('');
  /* 🔴 innerHTML 之后才存在的元素要重新绑事件 —— 只在渲染开头绑的话
     点了没反应且不报错（对比页表格里的「移除」栽过）。 */
  bar.querySelectorAll('a.lpc').forEach(a => a.onclick = ev => {
    ev.preventDefault(); LPC = a.dataset.c; renderChart(aid); });

  if(LPC === 'eq'){
    lineChart(el, [{n: '总资产', v: o.equity, c: '#5b9cf0'}],
      {dates: o.dates, h: 300, moneyAxis: true,
       ctl: rgNote(o) + '<div class="note">总资产 = 现金 + 持仓市值（按当日收盘）。'
          + '<b>含入金</b> —— 入金那天会跳一截，那不是收益。'
          + '净入金 ' + num(st.net_deposit || 0, 2) + ' 元。</div>'});
  } else if(LPC === 'nav'){
    /* 🔴 收益率与收益金额**同时给**：只有百分比的话它旁边没有能和
       "持仓浮盈 +3,698" 对上的数，看着像两回事（同 KPI 板那条）。
       ★ 金额不画成第二条线 —— 量纲不同共一根 y 轴必然把一条压平，
         而"压平"看着像那条线没动。读数走 tooltip 的 extra。 */
    /* ---- 基准叠加：与账户**同一起点**，所以直接可比 ----
       ★ 基点由服务端取"第一天的前一交易日收盘"（与 feed.benchmark 同一条
         纪律）—— 用第一天收盘做基点等于把首日涨跌排除在基准之外，
         实测差过 9.5pp。 */
    const BM = o.benchmarks || [], BD = o.bench || {};
    const picks = LPB.filter(c => BD[c]);
    const extra = [{n: '累计金额', v: cum, fmt: money}];
    const series = [{n: '实际 (TWR)', v: o.nav, c: '#e0b050', w: 2}];
    /* ---- 「策略」曲线：完全按绑定版本做会怎样 ----
       🔴 它与实际的差 = **执行的代价**（漏单、价格、手工调整），
         而不是策略好坏。两条同起点、同本金、同费率，所以差异只剩执行。
       ★ 对齐用**日期**而不是下标：两边交易日可能不等长（策略曲线从
         开户日起算，而权益曲线盘中会多补今天那一点）—— 按下标并的话
         错一位就整条线平移，而它不报错。 */
    const stratOn = LPB.indexOf(LPB_STRAT) >= 0;
    if(!stratOn) LPBCH_NOTE = '';
    if(stratOn && LPBCH && !LPBCH.error && LPBCH.dates){
      const m = {};
      LPBCH.dates.forEach((d, i) => { m[d] = LPBCH.nav[i]; });
      const sv = o.dates.map(d => m[d] == null ? null : m[d]);
      if(sv.some(v => v != null))
        /* 🔴 颜色**不能用 LPB_COL[0]**（`#7ec8a0`）—— 那是上证的色，
           两条线一模一样时"哪条是策略"只能靠猜。这里用一个不在 LPB_COL
           里的紫色，且比指数线粗一档（它不是外部基准，是"我本来该有的"）。 */
        series.push({n: '策略 (完全照做)', v: sv, c: '#b07de0', w: 1.7});
      /* ★ 两条线的**基点都是本金** —— 策略那条若拿首日收盘做基点，
         就等于把它首日的涨跌排除在外，差异里会混进「起点差」。
       ★ 末尾那一天策略常常**没有值**：面板到昨天，而实际那条盘中会用
         实时价多补一点 —— 不说明的话看着像策略线断了。 */
      /* ★ 这段说明只在**选中策略**时给：它解释的是那条线，没画线还留着
         一段话，读的人会去找那条不存在的线。 */
      LPBCH_NOTE = (() => {
        const last = o.dates[o.dates.length - 1];
        const miss = m[last] == null;
        const d0 = (o.nav[0] != null && sv[0] != null)
          ? (o.nav[0] - sv[0]) * 100 : null;
        return `<b style="color:#7ec8a0">策略</b>：按绑定版本 ${
          esc((LPBCH.sha || "").slice(0, 8))} 与当时参数、同本金同费率
          <b>完全照做</b>的净值。两条差异 = <b>执行的代价</b>
          （漏单 / 价格 / 手工加减），不是策略好坏。
          ${d0 == null ? "" : `首日就差 <b>${(d0 >= 0 ? "+" : "") +
            d0.toFixed(2)}pp</b>（调仓日与实际建仓日不同步时会这样）。`}
          ${miss ? "最后一天策略没有值 —— 面板到昨天，而实际那条盘中" +
            "用实时价多补了一点。" : ""}`;
      })();
    }
    picks.forEach((c, i) => {
      const b = BM.find(x => x.code === c) || {code: c, name: c};
      /* ★ 基准只进 `series` —— lineChart 的 tooltip 会自动列出所有
         series，再放进 `extra` 就是**同一行读数出现两次**（实测踩到）。
         `extra` 只放不共轴的那个量（累计金额）。 */
      series.push({n: b.name, v: BD[c], c: LPB_COL[LPB_ALL.split(',').indexOf(c)
                   % LPB_COL.length], w: 1.2});
    });
    /* 基准选择器。★ 每个选项标出**本地数据从哪年开始** —— 科创50 只有
       2019-12 之后，选了它却发现前面是空的话，人会以为图画坏了。
       ★ `note` 是服务端给的说明（比如"中证2000 本地没有"）—— 写在 title 里。 */
    /* ★ 「策略」排在最前并用分隔线隔开：它不是外部指数，而是
         "完全照做会怎样" —— 与账户同本金同费率，所以是最有意义的那个对照。
       ★ 拿不到策略曲线时（没绑定版本 / 快照缺文件）**不列这个选项** ——
         列出来点了什么都不出来比不给更糟（同 backLink 那条）。 */
    const canStrat = !!(LPBCH && !LPBCH.error && (LPBCH.dates || []).length);
    const bsel = '<div class="lpbar" id="lp_bm"><span class="lvwhy">基准</span>'
      + (canStrat ? `<a href="#" class="lpb lpbs${
            LPB.indexOf(LPB_STRAT) >= 0 ? ' on' : ''}" data-b="${LPB_STRAT}"
          title="按绑定版本 ${esc((LPBCH.sha || '').slice(0, 8))} 与当时参数、\
同本金同费率完全照做的净值。&#10;两条的差 = 执行的代价（漏单/价格/手工加减）"
          >策略</a><span class="lvwhy">|</span>` : '')
      + BM.map(b => `<a href="#" class="lpb${LPB.indexOf(b.code) >= 0 ? ' on' : ''}"
          data-b="${b.code}" title="${esc(b.code)}${
            b.from ? '　本地数据自 ' + esc(b.from) : ''}${
            b.note ? '\n' + esc(b.note) : ''}">${esc(b.name)}</a>`).join('')
      + (LPB.length ? '<a href="#" class="lpb" id="lp_bclr">不比</a>' : '')
      + '</div>';
    lineChart(el, series,
      {dates: o.dates, h: 300, pctAxis: true, extra: extra,
       ctl: bsel + rgNote(o) + '<div class="note">时间加权净值：每个有外部现金流的日子'
          + '切开再连乘 —— <b>入金不算收益</b>，这也是能和回测年化直接比的'
          + '口径。　鼠标移到曲线上同时给<b>收益率与累计金额</b>（当前 '
          + pc(st.twr) + ' / '
          + (cum.length ? money(cum[cum.length - 1]) : '—') + '）。'
          + (picks.length
             ? '<br>基准与账户<b>同一起点</b>（基点取第一天的前一交易日收盘）；'
               + '指数是<b>日线收盘</b>，所以盘中的今天基准还没有点，曲线断在'
               + '昨天 —— 那不是缺数据。'
             : '')
          + (LPBCH_NOTE ? '<br>' + LPBCH_NOTE : '')
          + '</div>'});
    /* 🔴 选择器是 lineChart 用 innerHTML 塞进去的，事件必须**在那之后**绑
       —— 在之前绑的话点了没反应且不报错（对比页「移除」栽过）。 */
    el.querySelectorAll('a.lpb').forEach(a => a.onclick = ev => {
      ev.preventDefault();
      /* 单选：点新的换掉旧的，点已选中的取消。★ 不做"多选 + 清空按钮" ——
         同时看多个基准反而看不清（见 LPB 的注释）。 */
      lpbSet(a.id === 'lp_bclr' ? '' : (LPB[0] === a.dataset.b ? '' : a.dataset.b));
      renderChart(aid);
    });
  }
  renderDD(aid, o, st);
}

/* 回撤副图：**跟着上面那条曲线走**，与它共用 x 轴。

   🔴 **口径必须跟随主图，否则两张图在同一屏上会自相矛盾。**
     资金曲线含入金，收益曲线（TWR 净值）不含 —— 入金那天总资产跳一截、
     峰值跟着抬高，于是"按总资产算的回撤"会把后面每一天都量深一点，
     而净值那条根本没这回事。今天两个账户 `net_deposit=0`，两种口径
     **逐日完全相同**（实测最大差 0.000000）—— 正因为现在看不出差别，
     才更要把它定死：等哪天真入金了，错的那个口径不会报错，只会
     悄悄画出一条更深的线。

   ★ 副图高度 150（主图 300）：它是注解不是主角，占一半高度既看得清
     形状、又不会把收益明细挤到屏幕外。 */
function renderDD(aid, o, st){
  const el = $('#lp_dd');
  if(!el || !o) return;
  const onNav = (LPC !== 'eq');
  const base = onNav ? 1.0 : (st.start_equity || o.equity[0]);
  const src  = onNav ? o.nav : o.equity;
  if(!src){ el.innerHTML = ''; return; }
  const dd = drawdownSeries(src, base);
  const deep = Math.min.apply(null, dd.filter(v => v != null));
  const at = (() => {                    /* 最深那天 —— 跟着本图口径算 */
    let i = dd.indexOf(deep);
    return i >= 0 && o.dates ? o.dates[i] : null;
  })();
  /* 🔴 `hiCap: 0` —— 回撤的最高点**永远是 0**（在最高点时回撤为 0，
     不可能为正）。不钳的话 y 轴留白会显示成 +0.1%，而那个数没有意义，
     读的人会以为"曾经比历史最高还高 0.1%"。 */
  lineChart(el, [{n: '回撤', v: dd, c: '#f05b5b', w: 1.3, fill: '#f05b5b'}],
    {dates: o.dates, h: 150, ratioAxis: true, hiCap: 0, zero: 0});
  /* ★ 说明放在图**下面**（lineChart 的 ctl 是塞在 svg 上方的，
       放那儿会把主图和副图撑开、破坏"贴在一起"的观感）。 */
  const note = document.createElement('div');
  note.className = 'note';
  note.innerHTML = '回撤 · 按<b>' + (onNav ? '收益曲线（TWR 净值）' : '资金曲线（总资产）')
    + '</b>算，与上图同一条线。距它自己的历史最高还差多少；峰值从<b>起点</b>'
    + '起算 —— 只看曲线上的点会把第一天的下跌算成"没有回撤"。　本区间最深 '
    + (deep == null || !isFinite(deep) ? '—' : (deep * 100).toFixed(2) + '%')
    + (at ? '（' + esc(at) + '）' : '')
    + (st.net_deposit ? '　<b>有净入金 ' + num(st.net_deposit, 2)
        + ' 元</b>，所以两条曲线的回撤不一样：入金抬高总资产的峰值，'
        + '而净值不受影响。' : '');
  el.appendChild(note);
}

/* 收益明细 = **方格热力图**（与回测详情页同一套 calGrid / _hcol / _legend）。
   🔴 不用列表：一屏几十行数字没法"一眼看出哪天崩的"，而方格图的底色
     就是强度、位置就是日期 —— 这一页存在的理由就是快速看形态。
   三种粒度（日 / 月 / 年）× 三种读数（收益率 / 金额 / 两者）。
   ★ 默认「日 + 两者」并停在**最近有数据的那个月** —— 打开就该看到"这个月
     每天怎么样"，而不是先让人选。 */
let LPV = {gran: 'day', show: 'both', ym: null};

function renderPerfTable(aid){
  const o = LPS || LPD, el = $('#lp_tbl');
  if(!el || !o) return;              /* 人已经走开了（见 renderChart 那条） */
  if(!o.nav || !o.day_pnls){ el.innerHTML = ''; return; }
  /* 🔴 用净值不用总资产；base=1.0 —— nav[0] 已含建仓当天的收益
     （TWR 从开户资金起算），不传起点会把第一天整段丢掉。
     金额传 day_pnls：逐日相加，不能用"桶末 − 桶初"（那含入金）。 */
  const B = perfBuckets(o.dates, o.nav, 1.0, o.day_pnls);
  if(!LPV.ym) LPV.ym = o.dates[o.dates.length - 1].slice(0, 7);
  const sn = (v, d) => v == null ? '—' : _sn(v, d == null ? 2 : d) + '%';
  const money = v => v == null ? '—'
    : (v >= 0 ? '+' : '') + num(Math.round(v), 0);
  const cellTxt = r => LPV.show === 'ret' ? sn(r.ret)
    : LPV.show === 'pnl' ? money(r.pnl)
    : `${sn(r.ret)}<span class="cd2">${money(r.pnl)}</span>`;

  const gbtn = (k, t) => `<a href="#" class="lpg${LPV.gran === k ? ' on' : ''}"
      data-g="${k}">${t}</a>`;
  const sbtn = (k, t) => `<a href="#" class="lps${LPV.show === k ? ' on' : ''}"
      data-s="${k}">${t}</a>`;
  let h = `<div class="ttl">收益明细
      <span class="lvwhy">按 <b>TWR 净值</b>算（与收益曲线同口径）；
        金额是逐日 Δ权益 <b>剔除现金流</b>后相加</span></div>
    <div class="lpbar">
      <span class="lvwhy">粒度</span>${gbtn('day','日')}${gbtn('month','月')}${gbtn('year','年')}
      <span class="lvwhy" style="margin-left:14px">读数</span>
      ${sbtn('ret','收益率')}${sbtn('pnl','金额')}${sbtn('both','两者')}
    </div>`;

  if(LPV.gran === 'day'){
    const yms = [...new Set(o.dates.map(d => d.slice(0, 7)))].sort();
    const at = yms.indexOf(LPV.ym);
    const items = {};
    o.dates.forEach((d, i) => {
      if(!d.startsWith(LPV.ym)) return;
      items[d] = {ret: B.days[d], pnl: o.day_pnls[i],
                  tip: `日收益 ${sn(B.days[d])}　金额 ${money(o.day_pnls[i])}\n`
                     + `净值 ${o.nav[i].toFixed(4)}　总资产 ${num(o.equity[i], 2)}`};
    });
    const M = B.months[LPV.ym] || {};
    h += `<div class="crumb">
        ${at > 0 ? `<a href="#" class="lpym" data-ym="${yms[at-1]}">◀ ${yms[at-1]}</a>` : ''}
        <b style="margin:0 10px">${LPV.ym}</b>
        ${at >= 0 && at < yms.length - 1
          ? `<a href="#" class="lpym" data-ym="${yms[at+1]}">${yms[at+1]} ▶</a>` : ''}
        <span style="margin-left:14px" class="lvwhy">本月
          收益 ${sn(M.ret)}　金额 ${money(M.pnl)}　最大回撤
          ${M.mdd == null ? '—' : (M.mdd * 100).toFixed(2) + '%'}</span>
      </div>`
      + calGrid(LPV.ym, items, cellTxt, {scale: 12}) + _legend();
  } else if(LPV.gran === 'month'){
    const ks = Object.keys(B.months).sort();
    h += `<div class="hm m">${ks.map(k => {
      const r = B.months[k];
      return `<div class="hc" data-ym="${k}" style="background:${_hcol(r.ret, 4)}"
        title="${k}　收益 ${sn(r.ret)}　金额 ${money(r.pnl)}
最大回撤 ${(r.mdd*100).toFixed(2)}%　交易日 ${r.n}
点击看这个月的每日">
        <div class="k">${k}</div><div class="v">${cellTxt(r)}</div>
        <div class="n">回撤 ${(r.mdd*100).toFixed(1)}% · ${r.n} 日</div></div>`;
    }).join('')}</div>` + _legend();
  } else {
    const ks = Object.keys(B.years).sort();
    h += `<div class="hm y">${ks.map(k => {
      const r = B.years[k];
      return `<div class="hc" data-y="${k}" style="background:${_hcol(r.ret, 2)}"
        title="${k} 年　收益 ${sn(r.ret)}　金额 ${money(r.pnl)}
最大回撤 ${(r.mdd*100).toFixed(2)}%　交易日 ${r.n}
点击看这一年的月度">
        <div class="k">${k}</div><div class="v">${cellTxt(r)}</div>
        <div class="n">回撤 ${(r.mdd*100).toFixed(1)}% · ${r.n} 日</div></div>`;
    }).join('')}</div>` + _legend();
  }
  el.innerHTML = h;

  const re = () => renderPerfTable(aid);
  el.querySelectorAll('a.lpg').forEach(a => a.onclick = ev => {
    ev.preventDefault(); LPV.gran = a.dataset.g; re(); });
  el.querySelectorAll('a.lps').forEach(a => a.onclick = ev => {
    ev.preventDefault(); LPV.show = a.dataset.s; re(); });
  el.querySelectorAll('a.lpym').forEach(a => a.onclick = ev => {
    ev.preventDefault(); LPV.ym = a.dataset.ym; re(); });
  el.querySelectorAll('.hc[data-ym]').forEach(c => c.onclick = () => {
    LPV.ym = c.dataset.ym; LPV.gran = 'day'; re(); });
  el.querySelectorAll('.hc[data-y]').forEach(c => c.onclick = () => {
    LPV.gran = 'month'; re(); });
}

/* ============ 每期「策略说什么 vs 实际做了什么」============
   🔴 **调仓提示本来就以实际持仓为准**（`lv/sig.py` 的 `_seed` 把
     `fifo_lots(真实成交流水)` 播种进引擎，让策略跑自己的代码路径）——
     所以这一块比的不是"提示对不对"，而是**执行**：提示的 10 只买了几只、
     股数与价格差多少。
   🔴 股数**按本金归一化后再比**（服务端做）：实测 froec 09-01 那份信号是
     按 100 万算的（账户后来改成 40 万），不归一化会把 10 只里 9 只判成
     "买少了"，而那根本不是执行差异 —— 页面上必须把这个比例说出来，
     否则"要 9300 股、实买 3700"看着就是没照做。 */
const XD_LABEL = {
  ok: ['照做', ''],
  missed: ['没买', 'warn'],
  short: ['买少了', 'warn'],
  over: ['买多了', 'warn'],
  extra: ['提示外买入', 'warn'],
  sell_miss: ['没卖', 'warn'],
  sell_extra: ['提示外卖出', 'warn'],
};

/* 候选名次。★ 「提示外买入」最需要它：第 11/20 名说明只差一名（策略取前 10），
   而"不在候选池"说明策略完全没考虑过它 —— 两种情况的含义完全不同。
   🔴 名次来自**旁挂的选股理由**（当时捕获的候选池），不是现算的。
     老信号（这个功能之前那几期）没有旁挂，显示「—」而不是猜一个。 */
const XD_ST = {buy: '选中·买入', hold: '选中·持有', sell: '卖出',
               not_taken: '没轮到', dropped: '被剔除'};

function xdCand(r){
  const c = r.cand;
  if(!c || c.rank == null){
    /* ★ 分两种「没有」：策略点过名但拿不到理由（老信号）vs 真的不在候选池。
       前者是数据缺失、后者是事实 —— 混成一个「—」就分不出来了。 */
    return r.want_side
      ? '<span class="lvwhy">—</span>'
      : '<span class="lvwhy" title="策略那一期的候选池里没有它">不在候选池</span>';
  }
  const st = XD_ST[c.status] || c.status || '';
  const hot = (c.status === 'not_taken' && r.kind === 'extra');
  return `<span class="${hot ? 'warn' : ''}" title="${esc(c.group || '')}${
    c.dropped_by ? '　剔除原因：' + esc(c.dropped_by) : ''}">第 ${c.rank}/${
    c.of} 名${st ? ' · ' + st : ''}</span>`;
}

function renderExec(aid){
  const el = $('#lp_exec');
  if(!el) return;                    /* 人已经走开了（见 renderChart 那条） */
  const xd = LPXD || {};
  const items = xd.items || [];
  if(xd.error){
    el.innerHTML = `<div class="warn">执行差异取不到：${esc(xd.error)}</div>`;
    return;
  }
  if(!items.length){
    el.innerHTML = '<div class="lvwhy">还没有可比对的调仓期'
      + '（要有信号、且那天有成交或有提示）。</div>';
    return;
  }
  /* ★ 汇总先给：一眼看出"照做了几期、差在哪" —— 逐期表格是往下翻的。 */
  const tot = {};
  items.forEach(it => Object.entries(it.counts || {}).forEach(
    ([k, v]) => { tot[k] = (tot[k] || 0) + v; }));
  const nClean = items.filter(it => it.clean).length;
  const pxs = items.map(it => it.px_diff_avg).filter(v => v != null);
  const pxAvg = pxs.length ? pxs.reduce((a, b) => a + b, 0) / pxs.length : null;

  el.innerHTML = `<div class="lpttl">执行差异
      <span class="lvwhy">${items.length} 期 · 完全照做 ${nClean} 期${
        pxAvg == null ? '' : ' · 平均价差 ' + pctv(pxAvg * 100)}</span>
    </div>
    <div class="lvwhy" style="margin-bottom:8px">
      调仓提示用的是<b>实际持仓</b>（成交流水重建），所以这里比的是<b>执行</b>：
      提示的票买了几只、股数与价格差多少。<b>价差</b>是<b>我的成交价 vs 策略回测的成交价</b>（都按当日开盘，策略那边含 0.075% 的滑点假设）—— 负数表示我买得比回测假设便宜。★ 不比昨收：那量的是<b>隔夜跳空</b>，既不是执行质量、也不是能控制的事。
      ${Object.entries(tot).filter(([k]) => k !== 'ok').length
        ? '合计 ' + Object.entries(tot).filter(([k]) => k !== 'ok').map(
            ([k, v]) => `<b>${(XD_LABEL[k] || [k])[0]} ${v}</b>`).join(' · ')
        : '<b>每一期都照做了。</b>'}
    </div>
    ${items.map(it => xdSection(it)).join('')}`;
}

function xdSection(it){
  const bad = (it.rows || []).filter(r => r.kind !== 'ok');
  return `<div class="lvsec xds">
    <h3>${esc(it.date)}
      ${it.is_rebalance_day ? '<span class="lvwhy">调仓日</span>' : ''}
      <span class="lvwhy">提示买 ${it.n_want_buy} 卖 ${it.n_want_sell}</span>
      ${it.clean ? '<span class="lvwhy" style="color:var(--down)">✓ 照做</span>'
        : `<span class="warn">${bad.length} 处不同</span>`}
      ${it.px_diff_avg == null ? ''
        : `<span class="lvwhy">价差均 ${pctv(it.px_diff_avg * 100)}</span>`}
    </h3>
    ${it.scale_why ? `<div class="lvwhy" style="margin:0 0 6px">
        ⓘ ${esc(it.scale_why)}</div>` : ''}
    <div class="pw"><table class="lvt xdt"><thead><tr>
      <th>代码</th><th class="tx">名称</th><th class="tx">差异</th>
      <th>提示股数</th><th>实际股数</th>
      <th>策略成交价</th><th>我的成交价</th><th>价差</th>
      <th class="tx">候选名次</th><th>笔数</th>
    </tr></thead><tbody>${(it.rows || []).map(r => {
      const [lbl, cls] = XD_LABEL[r.kind] || [r.kind, ''];
      /* ★ 只给**实际该买多少**（服务端已按本金折算并取整手）——
         原始那个数是按信号里的 cash 算的，与这个账户无关。 */
      const wantSh = r.want_shares == null ? '—' : num(r.want_shares, 0);
      return `<tr class="${r.kind === 'ok' ? '' : 'xdbad'}">
        <td>${spLink(r.code, r.code)}</td>
        <td class="tx">${spLink(r.code, r.name || '')}</td>
        <td class="tx ${cls}">${lbl}</td>
        <td>${wantSh}</td>
        <td>${r.got_buy != null ? num(r.got_buy, 0)
              : (r.got_sell != null ? '-' + num(r.got_sell, 0) : '—')}</td>
        <td>${r.strat_px == null ? '—' : num(r.strat_px, 3)}</td>
        <td>${r.got_px == null ? '—' : num(r.got_px, 3)}</td>
        <td style="color:${r.px_diff == null ? '' : upc(r.px_diff)}">${
          r.px_diff == null ? '—' : pctv(r.px_diff * 100)}</td>
        <td class="tx">${xdCand(r)}</td>
        <td class="lvwhy">${r.n_fills || '—'}</td>
      </tr>`;
    }).join('')}</tbody></table></div>
  </div>`;
}

/* ================= 每日持仓 / 交易记录（2026-09-15 加）=================
   用户："实盘功能反而没有交易记录、每日持仓，实盘的信息不应该比回测少。"

   🔴 **复用回测详情页的 `tbl()`**（run-detail.js 的顶层函数，同一个
     index.html 里全局可见）—— 两页各写一套表格渲染的话，排序、分组、
     数字格式、列宽会慢慢分叉，而那不报错，只是"同一个东西两页长得不一样"。
   ★ 放在业绩页而不是另开一页：这一页本来就是"复盘时看的"，
     而这两块正是复盘要看的东西（同「主视图只放天天要看的」那条）。 */
let LPH = {off: 0, lim: 100};      // 每日持仓分页
let LPT = {off: 0, lim: 100};      // 交易记录分页

function lpNav(p, np, cls) {
  /* ★ 上下两套导航用 class 不用 id —— 同 paneHoldings 那条（id 拼接过歧义）。 */
  return `<div class="pg">
     <button class="${cls}f" ${p <= 1 ? 'disabled' : ''}>« 首页</button>
     <button class="${cls}p" ${p <= 1 ? 'disabled' : ''}>‹ 上一页</button>
     <span>第 <input class="${cls}i" value="${p}"> / ${np} 页</span>
     <button class="${cls}n" ${p >= np ? 'disabled' : ''}>下一页 ›</button>
     <button class="${cls}l" ${p >= np ? 'disabled' : ''}>末页 »</button>
     <span style="margin-left:10px">每页
       <select class="${cls}s">${[50, 100, 200].map(x =>
         `<option ${x === (cls === 'h' ? LPH : LPT).lim ? 'selected' : ''}>${x}</option>`
       ).join('')}</select> 条</span>
   </div>`;
}

function lpBind(root, cls, st, np, redraw) {
  const go = o => { st.off = Math.max(0, Math.min(o, (np - 1) * st.lim)); redraw(); };
  const on = (k, fn, ev) => document.querySelectorAll(root + ' .' + cls + k)
    .forEach(e => e[ev || 'onclick'] = fn);
  on('f', () => go(0));
  on('p', () => go(st.off - st.lim));
  on('n', () => go(st.off + st.lim));
  on('l', () => go((np - 1) * st.lim));
  on('s', e => { st.lim = +e.target.value; st.off = 0; redraw(); }, 'onchange');
  on('i', e => { const v = +e.target.value; if (v >= 1 && v <= np) go((v - 1) * st.lim); },
     'onchange');
}

async function renderHoldings(aid) {
  const el = $('#lp_hold');
  if (!el) return;                 /* 人已经走开（同 renderChart 那条） */
  let h;
  try { h = await j(`/api/live/holdings?id=${encodeURIComponent(aid)}`
      + `&offset=${LPH.off}&limit=${LPH.lim}`); }
  catch (e) { el.innerHTML = `<div class="lvmsg bad">${esc(String(e))}</div>`; return; }
  if (!$('#lp_hold')) return;
  if (!h.total) {
    el.innerHTML = '<div class="ttl">每日持仓</div>'
      + '<div class="none">还没有持仓记录 —— 先去「✎ 记一笔」录成交。</div>';
    return;
  }
  /* 🔴 字段是 `limit` 不是 `lim` —— 写错了不报错，只是 `NaN/NaN 页`
       （截图里一眼能看到，但接口断言看不到：`total` 是对的）。 */
  const p = Math.floor(h.offset / h.limit) + 1;
  const np = Math.max(1, Math.ceil(h.total / h.limit));
  const nav = lpNav(p, np, 'h');
  el.innerHTML = `<div class="ttl">每日持仓
      <span class="lvwhy">${h.n_days} 个交易日 · ${h.total.toLocaleString()} 行</span></div>
    <div class="note">逐日快照，<b>按日期倒序</b>；同日内按权重降序。
      份额与价格都是<b>真实的那个数</b>（不复权）。
      <b>权重的分母是当日总权益</b>（持仓 + 现金）—— 用持仓市值当分母的话，
      满仓与半仓都显示 100%，而仓位正是要看的东西。
      点<b>名称/代码</b>弹速览浮层并定位到那一天。</div>
    ${nav}<div id="lp_hdt"></div>${nav}`;
  tbl($('#lp_hdt'), h.rows, [
    {k: 'code', t: '股票', l: 1,
     f: (v, r) => spLink(v, r.name || v, '', {date: r.date, html:
        (r.name ? `${esc(r.name)}<span class="cd">${esc(v)}</span>`
                : `<span class="cd0">${esc(v)}</span>`)})},
    {k: 'weight', t: '权重', f: v => pct(v, 2)},
    {k: 'value', t: '市值', f: v => v == null ? '—' : (+v).toFixed(0)},
    {k: 'shares', t: '份额', f: v => num(v, 0)},
    {k: 'last_price', t: '现价', f: (v, r) => fmtN(v, 3)
       + (r.stale_price ? '<span class="lvwhy" title="那天停牌，按最后已知价挂账">停</span>' : '')},
    {k: 'cost', t: '成本', f: v => fmtN(v, 3),
     h: '摊薄成本（含买入费）—— 与实盘持仓页同口径，那里的"浮盈"就是按它算的。'},
    {k: 'entry_date', t: '建仓日', l: 1},
    {k: 'unrealized_ret', t: '浮动收益', f: v => pct(v, 2), s: 1},
    {k: 'unrealized_pnl', t: '浮动盈亏', f: v => v == null ? '—' : (+v).toFixed(0), s: 1},
  ], null, {group: 'date', groupNote: rs => {
    const mv = rs.reduce((a, r) => a + (r.value || 0), 0);
    const w = rs.reduce((a, r) => a + (r.weight || 0), 0);
    return `${rs.length} 只 · 市值 ${num(mv, 0)} · 仓位 ${(w * 100).toFixed(1)}%`
      + ` · 现金 ${num(rs[0].cash, 0)}`;
  }});
  lpBind('#lp_hold', 'h', LPH, np, () => renderHoldings(aid));
}

async function renderTrips(aid) {
  const el = $('#lp_trip');
  if (!el) return;
  let t;
  try { t = await j(`/api/live/trips?id=${encodeURIComponent(aid)}`
      + `&offset=${LPT.off}&limit=${LPT.lim}`); }
  catch (e) { el.innerHTML = `<div class="lvmsg bad">${esc(String(e))}</div>`; return; }
  if (!$('#lp_trip')) return;
  /* ★ 未平仓那几批**也要说出来** —— 回测的 trades.parquet 恰恰没有它们，
       而实盘"我现在拿着什么、成本多少"是天天要看的。 */
  const openNote = t.n_open
    ? `<span class="lvwhy">另有 <b>${t.n_open}</b> 批未平仓（在「每日持仓」里看）</span>`
    : '';
  if (!t.total) {
    el.innerHTML = '<div class="ttl">交易记录 ' + openNote + '</div>'
      + '<div class="none">还没有<b>平仓</b>记录 —— 买入之后卖出才会配成一笔往返。</div>';
    return;
  }
  const p = Math.floor(t.offset / t.limit) + 1;
  const np = Math.max(1, Math.ceil(t.total / t.limit));
  const nav = lpNav(p, np, 't');
  el.innerHTML = `<div class="ttl">交易记录
      <span class="lvwhy">${t.total.toLocaleString()} 笔往返</span> ${openNote}</div>
    <div class="note">按 <b>FIFO 把买入与卖出配成一笔往返</b>，回答"这一笔赚了多少"
      —— 与「流水」那页是两件事（那里是<b>录入视角</b>：那天买了/卖了什么）。
      🔴 <b>收益率与盈亏都【含费】</b>：买入费摊进成本、卖出费从收入里扣。
      回测那边的收益率只含滑点不含佣金（与引擎 entry_price 同口径），
      所以两边的数<b>本来就不一样</b>，不该硬凑成一致。
      点<b>名称/代码</b>弹速览浮层并定位到平仓日。</div>
    ${nav}<div id="lp_tpt"></div>${nav}`;
  tbl($('#lp_tpt'), t.rows, [
    {k: 'exit_date', t: '平仓日', l: 1},
    {k: 'code', t: '股票', l: 1,
     f: (v, r) => spLink(v, r.name || v, '', {date: r.exit_date, html:
        (r.name ? `${esc(r.name)}<span class="cd">${esc(v)}</span>`
                : `<span class="cd0">${esc(v)}</span>`)})},
    {k: 'entry_date', t: '建仓日', l: 1},
    {k: 'holding_days', t: '持有天', f: v => v == null ? '—' : v},
    {k: 'shares', t: '份额', f: v => num(v, 0)},
    {k: 'entry_price', t: '买入价', f: v => fmtN(v, 3)},
    {k: 'exit_price', t: '卖出价', f: v => fmtN(v, 3)},
    {k: 'gross_amount', t: '卖出额', f: v => v == null ? '—' : (+v).toFixed(0)},
    {k: 'fee', t: '费用', f: v => fmtN(v, 2), h: '买卖两侧的真实费用（按份额摊到这一批）。'},
    {k: 'ret', t: '收益率', f: v => pct(v, 2), s: 1},
    {k: 'pnl', t: '盈亏', f: v => v == null ? '—' : (+v).toFixed(0), s: 1},
    {k: 'reason', t: '备注', l: 1},
  ]);
  lpBind('#lp_trip', 't', LPT, np, () => renderTrips(aid));
}
