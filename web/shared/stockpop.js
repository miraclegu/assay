/* 个股速览浮层 —— 点代码/名称【不跳走】，在原页面上打开。
   ================================================================
   🔴 **为什么不真跳转**：实盘页往往一直开着（待办、持仓、正在录一半的
     成交），跳走再回来这些状态就没了。原来用 `target="_blank"` 绕过这一点，
     但新标签页会越开越多，而且**看完要手动关**。浮层按 Esc 就回到原位。
   ★ 浮层是**速览**，不是个股页的替代：K 线 + 这只票的买卖点 + 几个 KPI。
     财务时序、板块、同行业、事件那些留在完整个股页 —— 浮层里有出口
     （🔴 出口必须有：浮层放不下的东西不能因此变成"看不到"）。
   🔴 **挂在 shared/ 而不是某个页面里**：6 个独立 .html 与 index.html 都要用它
     （14 处入口，见下）。挂在实盘页的话，盘面/自选/买点页点了还是跳走 ——
     而用户的原话是"其他地方可能也有这样的情况，也要做成这样的效果"。

   入口只有两个定义点，所以 14 处调用一起改：
     · `skLink`（views/live.js）  实盘持仓/待办/持有不动/成交流水/选股理由 —— 10 处
     · `stockHref`（common.js）   盘面/板块/自选/买点/对比/同行业 —— 11 处
   ================================================================ */

/* 🔴🔴 K 线默认**前复权**（2026-09-22，用户："浮窗图我要展示后复权
   或者前复权的，不复权的看不出正常的走势"）—— 他说得对：不复权跨除权日
   有**假跌幅**，形态上就是一根凭空的大阴线。

   ⚠ 这里此前叠着**三段互相矛盾**的注释（"固定不复权" / "跟着数据来源走、
     回测用 hfq" / "一律不复权"），是三轮改动各留一段、谁也没删谁 ——
     而它们的共同前提是**同一个**：买卖点画在 `Y(成交价)` 上，切复权就整体
     飘走。那个前提 2026-09-21 已经没了：标记改成**贴那根 K 线的上下方**，
     位置与价格无关（见 kchart.js）。前提一没，这条限制就该跟着撤销 ——
     **而不是留着一条已经没有理由的规则**。

   🔴 但「价格与份额」仍然一律**不复权**：那是照着券商对账单看的数
     （同「记账口径不该漏到页面上」）。所以这一页上是**两种口径并存**，
     副标题必须把它俩**分开说**，否则人拿浮窗里的成交价去比纵轴会发现对不上。
   ★ 取 `qfq` 而不是 `hfq`：与个股页、与主流行情软件的默认一致
     （后复权的纵轴会写着 6.11 而券商那儿是 3.37 —— 对不上）。
     三种口径都要切的话去完整个股页（右上角「完整页 ↗」）。 */
/* 某一天的成交明细（HTML 片段）。**hover 到那一根 K 线就出**，
   不必精确指到那个 7.5px 的圆点上 —— 用户 2026-09-15：
   "鼠标移到K线图上时，在买入、卖出那一根时看不到买入、卖出的价格数量。
    需要一个独立的浮窗展示出来，如果有多笔交易就展示多笔"。

   🔴 **多笔全列，不合并成一行。** 同日同向可能有好几笔（最低佣金按成交笔收，
     分笔录入就是对的：002910 实测同日 3100 股 + 600 股 各收 5.00），
     合成"均价 × 总股数"会把这件事抹掉，而那正是对账时要看的。
   ★ 返回空串表示那天没有成交 —— 调用方据此决定加不加这一段。 */
function tradesOfDay(trades, date) {
  const hit = (trades || []).filter(t => t.date === date);
  if (!hit.length) return '';
  const one = t => {
    const buy = t.side === 'buy';
    return `<div class="sptr">`
      + `<b class="${buy ? 'up' : 'dn'}">${buy ? '买' : '卖'}</b> `
      + `${num(t.shares, 0)} 股 @ <b>${num(t.price, 3)}</b>`
      + `<span class="spdim"> = ${num((+t.shares || 0) * (+t.price || 0), 0)}`
      + (t.fee == null ? '' : ' · 费 ' + num(t.fee, 2))
      + (t.account_name || t.account
         ? ' · ' + esc(t.account_name || t.account) : '')
      + `</span></div>`;
  };
  /* 多笔时先给一行合计 —— 逐笔在下面，两个都要（合计回答"这天动了多少"，
     逐笔回答"分几次动的"）。 */
  let head = '';
  if (hit.length > 1) {
    const sum = side => hit.filter(t => t.side === side)
      .reduce((a, t) => a + (+t.shares || 0), 0);
    const b = sum('buy'), sl = sum('sell');
    head = `<div class="spdim">${hit.length} 笔`
      + (b ? ` · 买 ${num(b, 0)} 股` : '')
      + (sl ? ` · 卖 ${num(sl, 0)} 股` : '') + '</div>';
  }
  return `<div class="sptrs">${head}${hit.map(one).join('')}</div>`;
}

const spFq = () => 'qfq';
const SP_NS = [60, 120, 250];
let SP = null;            /* {code, n, bars, trades, prof, geo} */
/* 对数坐标记在 localStorage：跟 4 个页面共用这个浮层，每次点开都要
   重新切一次的话就等于没有这个开关。 */
let SPLOG = (() => { try { return localStorage.getItem('splog') === '1'; }
                     catch(e){ return false; } })();

const spHref = c => '/stock.html?code=' + encodeURIComponent(c);

function spClose(){
  const w = document.getElementById('spwrap');
  if(w) w.remove();
  document.removeEventListener('keydown', spEsc);
  SP = null;
  SPLIST = null;
}
function spEsc(e){ if(e.key === 'Escape') spClose(); }

/* 打开浮层。★ 幂等：已经开着时换一只票就重载内容，不叠两层浮层。 */
async function spOpen(code, name, opt){
  opt = opt || {};
  if(!code) return;
  let w = document.getElementById('spwrap');
  if(!w){
    w = document.createElement('div');
    w.id = 'spwrap';
    w.innerHTML = `<div class="spbox" role="dialog" aria-modal="true">
        <div class="sphd">
          <b id="sptitle"></b>
          <span class="spdim" id="spsub"></span>
          <span class="spgrow"></span>
          <span class="spns" id="spns"></span>
          <a id="spfull" target="_blank" rel="noopener"
             title="财务时序 / 所属板块 / 同行业 / 事件都在完整页">完整页 ↗</a>
          <span class="spx" id="spx" title="Esc 关闭">×</span>
        </div>
        <div class="spbody">
          <div class="spkpi" id="spkpi"></div>
          <div class="spcv"><canvas id="spcv"></canvas><div id="sptip"
               class="sptip"></div></div>
          <div id="spmine"></div>
        </div>
        <div class="spnav" id="spnav"></div>
      </div>`;
    document.body.appendChild(w);
    /* 🔴 点遮罩关闭只认**遮罩自己**（e.target === w）：不判的话点浮层内部
       会冒泡上来把浮层关掉，表现是"刚点开就没了"。 */
    w.onclick = e => { if(e.target === w) spClose(); };
    /* 🔴 委托在 `#spwrap` 上，不是渲染时逐个绑 —— `#spnav` 每跳一只就换一次
       innerHTML，绑在按钮上的 handler 换一次就没了（同「innerHTML 填充之后
       才存在的元素要重新绑事件，或委托在稳定容器上」）。 */
    w.addEventListener('click', e => {
      const b = e.target.closest && e.target.closest('.spnb[data-d]');
      if(!b) return;
      e.preventDefault();
      spNav(Number(b.dataset.d));
    });
    document.getElementById('spx').onclick = spClose;
    document.addEventListener('keydown', spEsc);
  }
  /* 🔴 `SP` 初始是 null（第一次打开），所以取 n 必须先判它自己 ——
     写 `SP.n` 的话第一次点开就崩在这里，表现是**浮层弹出来但一片空白**，
     而画布停在 canvas 默认的 300x150 —— 看着像「这只票没数据」而不是
     「它崩了」。实测就是这么发现的（pageerror: reading n）。 */
  /* `center`：把这一天的 K 线放到图中央（回测详情页点某一行时传它）。
     ★ 带日期打开时默认 84 根 ≈ **前后各两个月** —— 看某笔成交时想知道的是
       "那前后发生了什么"，给 250 根会把那根柱子压成一像素。
     `run`：那次回测的 id。浮层要用**它的 datalake**（ETF 回测跑在平行的
       etf_lake 上，主面板里一行都没有）与**它的买卖点**。 */
  /* ★ `keepList`：跳上一只/下一只时**保持同一份清单**（否则每跳一次就按
     新位置重算，而浮层开着的时候底下那张表没变，重算等于白算）。
     不带 `keepList` 就是一次全新的打开 —— 清单跟着这次点击走，
     取不到就置 null（**不留上一次的**：那会让箭头指向另一张表的邻居）。 */
  if(!opt.keepList) SPLIST = opt.list || null;
  SP = {code: code, n: (opt.date ? 84 : ((SP && SP.n) || 120)),
        center: opt.date || null, run: opt.run || null};
  document.getElementById('sptitle').textContent = name || code;
  /* 副标题要把**口径**说出来：定位到哪天、以及价格是不是后复权。
     不说的话，同一只票从实盘点开（不复权）和从回测点开（后复权）会给出
     两套价，人会以为其中一个错了。 */
  document.getElementById('spsub').textContent = code
    + (SP.center ? '  ·  定位 ' + SP.center : '')
    /* 🔴 **两种口径并存，必须分开说**：K 线是前复权（看形态，不复权跨
       除权日有假跌幅），而下面「我的成交」那几个价与份额是不复权（照着
       券商对账单）。合成一句话说不清，而人一定会拿这两个数互相比。
       ⚠ 这个标签历史上**两次与图相反**（写着"回测口径（后复权）"而图是
         不复权；写着"一律不复权"而图已经改了）—— 标签与图不是一处定义，
         所以改图要**当场**改它。 */
    + '  ·  K 线【前复权】看形态 · 成交价与份额【不复权】（对账单口径）';
  document.getElementById('spfull').href = spHref(code);
  document.getElementById('spkpi').innerHTML = '<span class="spdim">载入中…</span>';
  document.getElementById('spmine').innerHTML = '';
  spNsBar();
  spNavBar();
  await spLoad();
}

function spNsBar(){
  const el = document.getElementById('spns');
  if(!el) return;
  el.innerHTML = SP_NS.map(n =>
    `<a href="#" data-n="${n}" class="${n === SP.n ? 'on' : ''}">${n}日</a>`).join('')
    + `<a href="#" data-lg="1" class="${SPLOG ? 'on' : ''}"
        title="对数坐标：等百分比涨幅 = 等高度 —— 线性轴会把低价那一段的波动压平">对数</a>`;
  /* ★ `<a href="#">` 当按钮**必须 preventDefault** —— 不拦的话会把
     location.hash 改成 '#'，在 index.html 上直接触发路由跳回目录页，
     表现是"点了没反应又好像回到了首页"，且没有任何报错。 */
  [...el.querySelectorAll('a')].forEach(a => {
    a.onclick = ev => {
      ev.preventDefault();
      if(a.dataset.lg){
        /* ★ 只**重画** —— 换的是坐标映射，数据没变，不用再打接口。 */
        SPLOG = !SPLOG;
        try { localStorage.setItem('splog', SPLOG ? '1' : '0'); }
        catch(e){ /* 隐私模式写不了 —— 不该因此打挂开关 */ }
        spNsBar(); spDraw();
        return;
      }
      SP.n = +a.dataset.n;
      spNsBar(); spLoad();
    };
  });
}

async function spLoad(){
  const code = SP.code;
  const run = SP.run, qr = run ? ('&run=' + encodeURIComponent(run)) : '';
  /* 定位：取到 `center + 一半窗口` 为止，于是那一天大致落在图中央。
     ★ 交易日与自然日的换算是近似的（×1.45），所以**不保证像素级居中** ——
       但那一天本身有 B/S 标记，看得出来是哪根。刻意不做"精确居中"：
       那要先问服务端"这天往后第 N 个交易日是哪天"，多一轮请求换一点点
       对齐，不值。 */
  let qe = '';
  if(SP.center){
    const d = new Date(SP.center + 'T00:00:00');
    d.setDate(d.getDate() + Math.round(SP.n / 2 * 1.45));
    qe = '&end=' + d.toISOString().slice(0, 10);
  }
  const [k, tr, pf] = await Promise.all([
    j(`/api/stock/kline?n=${SP.n}&fq=${spFq()}&code=`
      + encodeURIComponent(code) + qe + qr).catch(e => ({error: String(e)})),
    /* 买卖点：回测看**那次回测**的，别处看实盘的 —— 两个接口返回同一形状
       （date/side/shares/price/fee），所以下面画图与明细的代码是同一段。 */
    j(run ? ('/api/run/trades_of?id=' + encodeURIComponent(run)
             + '&code=' + encodeURIComponent(code))
          : ('/api/live/trades_of?code=' + encodeURIComponent(code)))
      .catch(() => ({trades: []})),
    j('/api/stock/profile?code=' + encodeURIComponent(code) + qr)
      .catch(() => ({})),
  ]);
  if(!SP || SP.code !== code) return;        /* 期间又点了别只票 —— 丢弃这批 */
  SP.bars = (k && k.bars) || [];
  SP.trades = (tr && tr.trades) || [];
  SP.prof = pf || {};
  /* ★ 标题用 profile 回来的 `sec_name` 补一次：入口有两列（代码列/名称列），
     从代码列点进来时 spOpen 拿到的 text 就是代码 —— 那时标题写着
     「002910」而下面的表里全是名字，看着像**两只不同的票**。 */
  if(SP.prof && SP.prof.sec_name){
    const t = document.getElementById('sptitle');
    if(t) t.textContent = SP.prof.sec_name;
  }
  spKpi(k);
  spMine();
  spDraw();
}

function spKpi(k){
  /* 🔴 字段名与量纲是**实测**的，不是猜的（`/api/stock/profile`）：
       `sec_name` 名称 · `close_bfq` 现价 · `change_pct` 2.54 已是百分数
       · `turnover` 0.079219 **也是**百分数（14.36亿/1.83万亿≈0.079%）
       · `totalmv` 单位是**元** · `sw_l1_name` 申万一级
     写这段时我先按印象猜了一轮 `close`/`pct_chg`/`pe`/`industry`，
     全是 None —— 而 None 在页面上就是一排"—"，**看着像"这只票没数据"**。 */
  const p = SP.prof || {};
  const box = document.getElementById('spkpi');
  if(!box) return;
  if(k && k.error){
    box.innerHTML = `<span class="warn">取不到 K 线：${esc(k.error)}</span>`;
    return;
  }
  const cells = [
    ['现价',  p.close_bfq != null ? num(p.close_bfq, 2) : '—'],
    ['涨跌',  p.change_pct != null
      ? `<span class="${p.change_pct >= 0 ? 'up' : 'dn'}">${pctv(p.change_pct)}</span>`
      : '—'],
    ['换手',  pctn(p.turnover, 2)],
    ['总市值', p.totalmv != null ? num(p.totalmv / 1e8, 0) + ' 亿' : '—'],
    ['PE(TTM)', p.pe_ttm != null ? num(p.pe_ttm, 1) : '—'],
    ['PB',    p.pb != null ? num(p.pb, 2) : '—'],
  ];
  /* ★ ST / 停牌 / 涨跌停这些**状态**要说出来 —— 浮层是"我该不该动这只票"
     的速览，而"它今天封着涨停"直接决定能不能成交。 */
  const flags = [];
  if(p.is_st) flags.push('<span class="warn">ST</span>');
  if(p.is_risk_warned) flags.push('<span class="warn">风险警示</span>');
  if(p.is_limit_up) flags.push('<span class="up">涨停</span>');
  if(p.is_limit_down) flags.push('<span class="dn">跌停</span>');
  if(p.public_status && p.public_status !== '正常上市')
    flags.push('<span class="warn">' + esc(p.public_status) + '</span>');
  box.innerHTML = cells.map(([a, b]) =>
    `<div><span class="spdim">${a}</span>${b}</div>`).join('')
    + `<div class="spdim spnote">${esc(p.sw_l1_name || '')}${
        p.date ? ' · 数据日 ' + esc(String(p.date).slice(0, 10)) : ''}${
        flags.length ? ' · ' + flags.join(' ') : ''}</div>`;
}

/* 「我在这只票上的成交」—— 🔴 一行都不能少：图上只画得下标记，
   费用、账户、备注这些看不出来，而"我到底买了几笔、什么价"是这一页
   存在的理由。同日多笔各占一行（最低佣金按笔收，分笔录入就是对的）。 */
function spMine(){
  const el = document.getElementById('spmine');
  const ts = SP.trades || [];
  if(!el) return;
  if(!ts.length){
    /* 🔴 回测那支的措辞要**准确**：归档 `trades.parquet` 只在【平仓】时写行，
       所以期末仍持有的那几只，它们的买入压根不在里面 —— 从持仓页点开这种票时
       写"没有买卖过"是**错的**（它就在持仓里摆着），人会以为功能坏了。 */
    el.innerHTML = '<div class="spdim spnote">'
      + (SP.run
         ? '本次回测没有这只票的<b>平仓</b>记录 —— 归档只在平仓时写行，'
           + '期末仍持有的买入不在其中（去「持仓」页看）。'
         : '这只票没有实盘成交记录。')
      + '</div>';
    return;
  }
  let net = 0;
  ts.forEach(t => { net += (t.side === 'buy' ? 1 : -1) * (+t.shares || 0); });
  /* 🔴 **默认折叠。** 买卖点的主视角是图上的 B/S 点（位置就是价位，
     hover 出价/量/费用/账户）—— 一份同样内容的表格摊在下面，等于把
     刚腾出来的空间又占回去，而且人得在两处之间来回对。
     但它**不能删**：费用与账户是图上放不下的，而"这只票我到底买了几笔"
     是要能查的（同「能进 tooltip 的就别占列」+「偶尔用的进浮层」）。 */
  const openNow = SP.mineOpen ? ' on' : '';
  el.innerHTML = `<div class="spmt spfold${openNow}" id="spmt">
      <span class="sparr">▸</span> 我的成交
      <span class="spdim">${ts.length} 笔 · 现持 ${num(net, 0)} 股 ·
        图上是 <span class="up">B</span>/<span class="dn">S</span> 点</span>
    </div>
    <div class="pw spmw${openNow}" id="spmw"><table class="lvt spt"><thead><tr>
      <th class="tx">成交日</th><th class="tx">方向</th><th>股数</th>
      <th>价格</th><th>金额</th><th>费用</th><th class="tx">账户</th>
    </tr></thead><tbody>${ts.map(t => {
      const buy = t.side === 'buy';
      return `<tr><td class="tx">${esc(t.date || '')}</td>
        <td class="tx"><span class="${buy ? 'up' : 'dn'}">${
          buy ? '买入' : '卖出'}</span></td>
        <td>${num(t.shares, 0)}</td><td>${num(t.price, 2)}</td>
        <td>${num((+t.shares || 0) * (+t.price || 0), 2)}</td>
        <td>${t.fee == null ? '—' : num(t.fee, 2)}</td>
        <td class="tx spdim" title="${esc(t.note || '')}">${
          esc(t.account_name || t.account || '')}${t.note ? ' ✎' : ''}</td></tr>`;
    }).join('')}</tbody></table></div>`;
  /* 🔴 `.spmw` 的 display 在**样式表**里，所以开关只能加/去 class ——
     写 `style.display=''` 只是删掉内联样式、样式表规则照旧生效，
     表现是"点了没反应"且不报错（CLAUDE.md 里 .hlpbox 那条）。 */
  document.getElementById('spmt').onclick = () => {
    SP.mineOpen = !SP.mineOpen;
    document.getElementById('spmt').classList.toggle('on', SP.mineOpen);
    document.getElementById('spmw').classList.toggle('on', SP.mineOpen);
  };
}

function spDraw(hover){
  const cv = document.getElementById('spcv');
  if(!cv) return;
  const box = cv.parentElement.getBoundingClientRect();
  cv.style.width = box.width + 'px';
  /* ★ 高度给到宽的一半、下限 340：B/S 圆点画在成交价【上下方】，
     图矮了圆点就贴着边缘、甚至压出主图区。 */
  cv.style.height = Math.max(340, Math.round(box.width * 0.5)) + 'px';
  if(!SP.bars.length){
    const g = cv.getContext('2d');
    g.clearRect(0, 0, cv.width, cv.height);
    return;
  }
  /* 🔴 成交量**不再是 drawKChart 内置的一块**（2026-09-15 改成普通副图）——
     不传的话浮层里那块量能就没了，而它不报错，只是少了一块。
     ★ rows 直接用 bars：量本来就在里面，不用再取一遍。 */
  const volPane = {id: 'vol', label: '成交量', rows: SP.bars,
                   series: [{key: 'volume', label: '成交量', style: 'vbar'}]};
  SP.geo = drawKChart(cv, {bars: SP.bars, trades: SP.trades, subs: [volPane],
                           log: SPLOG, hover: hover});
  spBindHover(cv);
}

function spBindHover(cv){
  if(cv.dataset.bound) return;
  cv.dataset.bound = '1';
  const tip = document.getElementById('sptip');
  cv.onmousemove = e => {
    const r = cv.getBoundingClientRect();
    const x = e.clientX - r.left, y = e.clientY - r.top;
    const geo = SP.geo;
    if(!geo) return;
    /* 🔴 先测**买卖点**再测 K 线：标记就那么几个、且是人特意去指的，
       被 K 线的读数盖住的话 hover 买卖点等于点不到。 */
    const hit = (geo.trHits || []).find(h =>
      Math.abs(h.x - x) < h.r && Math.abs(h.y - y) < h.r);
    if(hit){
      /* 🔴 同日同向已经**合并成一个标记**了（2026-09-21），所以这里要
         把那几笔**逐笔列全**再给一行合计 —— 合并只是把图上的图标收成
         一个，信息不许跟着丢（同「挪走可以，藏没了不行」）。
         ★ 价格用 3 位小数：ETF 最小变动是 0.001，2 位会把 1.0915 显示成
           1.09 —— 而这一格问的正是"我到底成交在哪个价"。 */
      const ts = hit.ts || [hit.t], buy = ts[0].side === 'buy';
      const tot = ts.reduce((a, t) => a + (+t.shares || 0), 0);
      const amt = ts.reduce((a, t) => a + (+t.shares || 0) * (+t.price || 0), 0);
      const fee = ts.reduce((a, t) => a + (+t.fee || 0), 0);
      const many = ts.length > 1;
      tip.innerHTML = `<b class="${buy ? 'up' : 'dn'}">${buy ? '买入' : '卖出'}</b>
        ${esc(ts[0].date)}${many ? `<span class="spdim"> · ${ts.length} 笔</span>` : ''}
        <br>${ts.map(t => `${num(t.shares, 0)} 股 @ <b>${num(t.price, 3)}</b>`)
               .join('<br>')}
        ${many ? `<br><span class="spdim">合计 ${num(tot, 0)} 股 · 均价 ${
          num(amt / (tot || 1), 4)}</span>` : ''}
        <br>金额 ${num(amt, 2)}${fee ? ' · 费用 ' + num(fee, 2) : ''}
        <br><span class="spdim">${esc(ts[0].account_name || ts[0].account || '')}${
          ts[0].note ? ' · ' + esc(ts[0].note) : ''}</span>`;
      _tipAt(tip, e.clientX, e.clientY);
      return;
    }
    const i = Math.round((x - geo.PADL) / geo.step);
    const b = SP.bars[i];
    if(!b){ tip.style.display = 'none'; return; }
    /* 🔴 涨跌幅必须给 —— 看 K 线第一个想知道的就是"那天涨跌多少"，
       只给 OHLC 的话得自己拿收盘除昨收。`change_pct` 面板里现成有，
       **已是**百分数（别再乘 100）；`turnover` 同样是百分数。 */
    /* 🔴 三态：0 不上色（同 upc）。`>= 0` 会把平盘画成红的。 */
    const cc = upc(b.change_pct);
    tip.innerHTML = `<b>${esc(b.date)}</b>
      <span style="color:${cc}">${
        b.change_pct == null ? '' : pctv(b.change_pct)}</span>
      <br>开 ${num(b.open, 2)} 高 ${num(b.high, 2)}
      <br>低 ${num(b.low, 2)} 收 <b style="color:${cc}">${
        num(b.close, 2)}</b>
      <br>昨收 ${num(b.preclose, 2)}
      <br>量 ${num((b.volume || 0) / 1e4, 1)} 万股${
        b.turnover == null ? '' : ' · 换手 ' + pctn(b.turnover, 2)}
      <br>额 ${num((b.amount || 0) / 1e8, 2)} 亿`
      /* 🔴 **那天的成交接在这里** —— 原来只有精确悬停到那个 7.5px 的
         B/S 圆点上才看得到，而人是往**那根 K 线**上移的（用户报的就是这个）。
         圆点那条路径保留：它给的是"这一笔"，这里给的是"这一天的全部"。 */
      + tradesOfDay(SP.trades, b.date);
    _tipAt(tip, e.clientX, e.clientY);
    if(i !== SP.hover){ SP.hover = i; spDraw(i); }
  };
  cv.onmouseleave = () => {
    tip.style.display = 'none';
    if(SP.hover != null){ SP.hover = null; spDraw(null); }
  };
}

/* 统一入口：把 code/name 渲染成一个**打开浮层**的链接。
   ★ 仍然是 `<a href>` 而不是 `<span>`：中键/右键"在新标签打开"照旧可用
     （有人就爱开标签页），左键才拦下来开浮层。href 也让它看起来能点 ——
     「看不出能点的入口 = 没有入口」。 */
function spLink(code, text, cls, opt){
  if(!code) return esc(text || '');
  opt = opt || {};
  /* `opt.html`：调用方要在链接里放**带标签的内容**时用（回测页要
     「名称 + 小字代码」两段样式）。★ 传 html 的一方**自己负责转义** ——
     加这个口子是为了让 `<a data-sp>` 只有【一处】定义：否则调用方会自己
     拼一个 `<a>`，而那份迟早与这里分叉（漏个 data-spr 就是"点了不定位"）。 */
  /* `date` / `run` 走 data-*，由下面那个**文档级**委托读出来再传给 spOpen ——
     不能在渲染时逐个绑 handler（这些链接绝大多数是 innerHTML 填进去的）。 */
  const d = opt.date ? ` data-spd="${esc(String(opt.date).slice(0, 10))}"` : '';
  const r = opt.run ? ` data-spr="${esc(opt.run)}"` : '';
  return `<a href="${spHref(code)}" class="spl ${cls || ''}"
    data-sp="${esc(code)}" data-spn="${esc(text == null ? '' : text)}"${d}${r}
    title="点开速览（Esc 关）${opt.date ? ' · 定位到 ' + esc(String(opt.date).slice(0,10)) : ''}· 中键在新标签打开完整页">${
    opt.html != null ? opt.html : esc(text == null ? code : text)}</a>`;
}

/* 🔴 **事件委托挂在 document 上**，不是渲染时逐个绑：这些链接绝大多数是
   innerHTML 填进去的，"只在渲染开头绑一次"的话后填充的那些永远没有
   handler —— **点了没反应，而且没有任何报错**（对比页「移除」栽过）。 */
if(typeof document !== 'undefined' && !window.__spOn){
  window.__spOn = true;
  document.addEventListener('click', e => {
    const a = e.target.closest && e.target.closest('a[data-sp]');
    if(!a) return;
    if(e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;  /* 让它照旧开新标签 */
    e.preventDefault();
    spOpen(a.dataset.sp, a.dataset.spn || '',
           {date: a.dataset.spd || '', run: a.dataset.spr || '',
            list: spListOf(a)});
  });
}

/* ---------------------------------------------------------------- 上一只 / 下一只
   用户 2026-09-29：「点击成分股中的个股出来弹窗后，弹窗的下面应该有左右箭头
   按钮，按箭头跳到当前列表中的上一个/下一个。」

   ★ 「当前列表」= **点击那个链接所在的那张表**里的全部 `a[data-sp]`，
     按 DOM 顺序。不取整个文档 —— 页面上常有好几张表（持仓 / 成交 / 榜单），
     串在一起走会跳到另一张表里去，而人以为还在同一份清单上。
   ★ 按 code 去重：同一只票在一块里出现两次（持仓表 + 成交表）时只算一个，
     否则「第 3 / 42」这个计数会说谎。
   ★ 只收**可见**的：页签切走的那些还在 DOM 里，算进来的话计数同样说谎。 */
let SPLIST = null;         /* {items:[{code,name,date,run}], i, what} */

function spListOf(a){
  /* 就近的容器：表 -> 区块 -> 页面。取不到就不给列表（不硬凑）。 */
  const box = a.closest('table') || a.closest('.lvsec') || a.closest('#pg');
  if(!box) return null;
  const seen = {}, items = [];
  Array.from(box.querySelectorAll('a[data-sp]')).forEach(x => {
    const c = x.dataset.sp;
    if(!c || seen[c]) return;
    if(x.offsetParent === null) return;          /* 看不见的不算 */
    seen[c] = 1;
    items.push({code: c, name: x.dataset.spn || '',
                date: x.dataset.spd || '', run: x.dataset.spr || ''});
  });
  if(items.length < 2) return null;
  let i = 0;
  for(let k = 0; k < items.length; k++) if(items[k].code === a.dataset.sp) { i = k; break; }
  /* 这一组是什么 —— 计数旁边要说得出来，否则「第 3 / 42」指的是哪 42 只没人知道。
     🔴 **先把 h3 里的按钮剥掉再取文字**：板块页 2026-09-29 把页签与
       「⚖ 前 6 只去对比」挪到了 h3 开头，直接 textContent 会读成
       「子行业 成分股 ⚖ 前 6 只去对比 房地产I 成分 95 只」——
       标题里混着操作按钮，而它不报错，只是那句话读不懂。
     ★ 剥的是**元素**（clone 之后删掉所有 a），不是按文字裁剪 ——
       按文字裁会跟着按钮文案一起变。 */
  const h = box.closest('.lvsec') && box.closest('.lvsec').querySelector('h3');
  let what = '当前列表';
  if(h){
    const c = h.cloneNode(true);
    Array.from(c.querySelectorAll('a')).forEach(x => x.remove());
    /* 标题末尾那个「N 只 / N 个」去掉 —— 旁边已经写了「第 2 / 95 只」，
       留着就成了「第 2 / 95 只 · 房地产I 成分 95 只」，同一个数说两遍。 */
    what = c.textContent.replace(/\s+/g, ' ').trim()
             .replace(/\s*\d+\s*[只个](（.*）)?$/, '').trim().slice(0, 28) || what;
  }
  return {items: items, i: i, what: what};
}

/* 🔴 标签**固定**（「‹ 上一只」/「下一只 ›」），邻居的名字放 title ——
   把名字写进按钮的话，每跳一只按钮宽度就变一次，按钮在手底下**移位**
   （同一天刚修过「切页签时按钮跳 40px」那条）。
   🔴 到头了**不设 disabled**（disabled 的元素连 title 都不触发），
     改成一句「已是第一只」占住同一个位置 —— 位置不动，且说得出为什么不能点。 */
function spNavBar(){
  const e = document.getElementById('spnav');
  if(!e) return;
  const L = SPLIST;
  if(!L){ e.innerHTML = ''; e.style.display = 'none'; return; }
  e.style.display = '';
  const p = L.items[L.i - 1], n = L.items[L.i + 1];
  e.innerHTML =
    (p ? `<a href="#" class="spnb" data-d="-1" title="${esc(p.name || p.code)}">‹ 上一只</a>`
       : '<span class="spnb spnbx">已是第一只</span>')
    + `<span class="spgrow"></span><span class="spdim" id="spnavi">第 ${L.i + 1} / ${
        L.items.length} 只 · ${esc(L.what)}</span><span class="spgrow"></span>`
    + (n ? `<a href="#" class="spnb" data-d="1" title="${esc(n.name || n.code)}">下一只 ›</a>`
       : '<span class="spnb spnbx">已是最后一只</span>');
}

function spNav(d){
  const L = SPLIST;
  if(!L) return;
  const j = L.i + d;
  if(j < 0 || j >= L.items.length) return;       /* 不绕回 —— 榜单绕回会让人以为还在往下走 */
  L.i = j;
  const it = L.items[j];
  spOpen(it.code, it.name, {date: it.date, run: it.run, keepList: true});
}
