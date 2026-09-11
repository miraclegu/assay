/* ============ 实盘：账户 / 成交 / 信号 / 费率 / 策略 ============ */
/* ============ 实盘模块 ============ */
/* 页面只负责【显示与录入】。所有规则判定都在服务端 assay/live.py ——
   它把成交流水重建成 Portfolio 再喂给真引擎，不在前端重算任何一条规则。 */
let LV=null, LVSEL=null, LVALL=false, LVO=null;
/* 侧栏收起 / 待办手动展开的状态。★ 存 localStorage：每次切账户都重置的话
   等于没有这个功能。待办的展开状态**不持久化** —— 它该由"到没到警示时间"
   决定，记住上次的选择会让人下次错过提示。 */
let LVFOLD = localStorage.getItem('lvfold')==='1';
let LVTODO = null;   // null=跟随 alert；true/false=本次手动覆盖
const LVPAGE=50;                      /* 流水每页条数（服务端也会夹到 1..500） */
const CFK={deposit:'入金', withdraw:'出金', dividend:'分红到账', adjust:'调整'};

/* esc 复用文件上方那个（第 ~357 行）—— 不重复定义。 */
async function showLive(aid){
  stopPoll();
  enterView();
  let o;
  try{ o=await j('/api/live/accounts'+(LVALL?'?all=1':'')); }
  catch(e){ $('#main').innerHTML='<div class="none">实盘模块读取失败：'+esc(e)+'</div>'; return; }
  LV=o; LVSEL=aid||(o.accounts[0]&&o.accounts[0].id)||null;
  const cal=o.calendar||{};
  let head='';
  if(cal.error) head+=`<div class="lvwarn"><b>交易日历缺失</b><br>${esc(cal.error).replace(/\n/g,'<br>')}</div>`;
  /* 权不权威由服务端判（live.AUTHORITATIVE_CAL 是唯一名单）——
     前端硬编码过一次，把 tdx.raw_holidays 误报成不权威，每次打开都弹假告警。 */
  else if(cal.authoritative===false)
    head+=`<div class="lvwarn"><b>交易日历不是权威来源（${esc(cal.source)}）</b><br>${esc(cal.warn||'')}</div>`;
  /* ★ 新页面 + 旧 API 的组合会渲染出一堆 undefined 而不报错：
     index.html 每次请求都从磁盘读，而 Python 模块只在进程启动时加载一次。
     实测踩过 —— 11:49 启动的服务配 12:53 改的页面，设置里全是 undefined。 */
  const CO=o.code||{};
  if(CO.code_mtime && CO.loaded_at && CO.code_mtime > CO.loaded_at + 2)
    head+=`<div class="lvwarn"><b>服务端是旧进程 —— 请重启 serve.py</b><br>
      页面是新的（每次请求都从磁盘读），但 Python 模块还是进程启动时那份
      （启动于 ${esc(new Date(CO.loaded_at*1000).toLocaleString('zh-CN'))}，
       代码已于 ${esc(new Date(CO.code_mtime*1000).toLocaleString('zh-CN'))} 更新）。<br>
      这种组合下页面会去读 API 还没有的字段，<b>渲染成一堆 undefined 而不报错</b>。
      <code>Ctrl-C</code> 后重新 <code>python3 serve.py</code> 即可。</div>`;
  if(o.readonly) head+=`<div class="lvwarn">服务以只读模式启动 —— 可以看，不能录入 / 建账户 / 重算。
     这个服务是 <code>--readonly</code> 起的 —— 去掉它重启即可。</div>`;
  /* ★ 告警点的判据由【服务端】给（a.alert，live.signal_alert）——
     前端硬编码判据坑过一次（把 tdx 日历误报成不权威，每次打开都弹假告警）。
     账户列表的点、待办要不要展开，读的是同一个字段。 */
  const dot=a=>a.alert?`<span class="adot" title="${esc((a.alert_why||[]).join('；'))}"></span>`:'';
  const tabs=o.accounts.map(a=>`<a class="ditem${a.id===LVSEL?' on':''}${a.archived?' miss':''}"
      href="#/live/${a.id}" title="${esc(a.alert?(a.alert_why||[]).join('；'):(a.archived?'已归档':''))}">
      <span>${dot(a)} ${esc(a.name)}</span><span class="dmeta">${a.n_positions} 只</span></a>`).join('');
  /* 收起态：一条 44px 的轨，每个账户一个方块（名称首字）+ 角上的告警点。
     "收起后就看不到该干什么了"的收起功能不如不做。 */
  const rail=`<div class="drail">
      <a href="#" class="dchip" id="lvunfold" title="展开账户列表">›</a>
      ${o.accounts.map(a=>`<a class="dchip${a.id===LVSEL?' on':''}"
        href="#/live/${a.id}"
        title="${esc(a.name)}${a.alert?' —— '+esc((a.alert_why||[]).join('；')):''}"
        >${esc((a.name||'?').trim().slice(0,1))}${dot(a)}</a>`).join('')}
    </div>`;
  $('#main').innerHTML=head+`<div id="dk" class="${LVFOLD?'fold':''}">
    ${LVFOLD?rail:`<div class="dside">
      <div class="dgrp" style="display:flex;align-items:baseline;gap:6px">
        <span style="flex:1">账户${LVALL?'（含已归档）':''}</span>
        <a href="#" id="lvfold" class="lvwhy" title="收起到左边">‹ 收起</a></div>
      ${tabs||'<div class="none">还没有账户</div>'}
      <div class="lvform" style="padding:2px 8px;gap:10px">
        <a href="#" id="lvall" class="lvwhy">${LVALL?'只看在用的':'显示已归档的'}</a>
        <a href="#" id="lvnew" class="lvwhy">+ 新建账户</a>
      </div>
      <div id="nform" style="display:none;border-top:1px solid var(--line);
           margin-top:8px;padding-top:8px">
        <div class="lvform" style="padding:0 8px">
          <input id="nn" placeholder="账户名称" style="flex:1;min-width:110px">
          <input id="nc" size="6" placeholder="初始资金">
          <button class="btn" id="nb" ${o.readonly?'disabled':''}>建</button>
        </div>
        <div class="lvwhy" style="padding:2px 8px">
          id 自动分配（下一个 <code>${esc(o.next_id||'a1')}</code>）。
          id 是内部主键、决定目录名，<b>不可改</b>；名称随时可改。
        </div>
        <div class="lvmsg" id="nmsg"></div>
      </div>
      <div class="dhint" style="margin-top:12px">成交流水<b>只追加</b>，持仓由它推导。<br>
        策略版本绑定后源码<b>永久留痕</b>。</div>
    </div>`}
    <div class="dbody" id="lvbody"></div></div>`;
  const fold=v=>{ LVFOLD=v; localStorage.setItem('lvfold', v?'1':'0'); showLive(LVSEL); };
  if($('#lvfold')) $('#lvfold').onclick=ev=>{ ev.preventDefault(); fold(true); };
  if($('#lvunfold')) $('#lvunfold').onclick=ev=>{ ev.preventDefault(); fold(false); };
  if($('#lvall')) $('#lvall').onclick=ev=>{ ev.preventDefault(); LVALL=!LVALL; showLive(LVSEL); };
  if($('#lvnew')) $('#lvnew').onclick=ev=>{ ev.preventDefault();
    const e=$('#nform'); e.style.display=(e.style.display==='none'?'':'none');
    if(e.style.display==='') $('#nn').focus(); };
  if($('#nb')) $('#nb').onclick=async()=>{
    const m=$('#nmsg');
    try{
      const r=await post('/api/live/save',{
        name:$('#nn').value.trim(), init_cash:parseFloat($('#nc').value||'0')});
      m.className='lvmsg ok'; m.textContent='已建';
      showLive(r.account.id);
    }catch(e){ m.className='lvmsg bad'; m.textContent=String(e); }
  };
  if(LVSEL) loadLive(LVSEL); else $('#lvbody').innerHTML='<div class="none">先建一个账户</div>';
}


/* 🔴 持仓表包在 .pw 里：12 列宽表在窄屏会把【整个 body】撑出横滚，
     读表格时整页左右晃（CLAUDE.md「窄屏不许把整个 body 撑出横滚」）。
     实测 1024 宽、15 只持仓的账户溢出 11px；selftest 没抓到是因为它的
     桩数据只有 3 只持仓 —— 所以断言改成查【结构】（表必须在 .pw 里），
     不依赖数据恰好够宽。
     ★ sticky 表头不用再操心：.pw table th{top:0} 已经是通用规则。
   🔴 这段说明**不能放在模板字符串里的 HTML 注释中** —— 里面的反引号会
     提前结束模板字符串，整个文件 SyntaxError、整页白屏（实测踩过，
     node --check 当场抓到）。同「注释里别写连着的星号加斜杠」那条。 */

/* ============ 主视图 ============
   信息架构：**天天要看的留在页面上，偶尔用的进浮层。**
     页面   今日待办 + 当前持仓（含盈亏汇总）
     浮层   ⚙设置 / ✎记一笔（成交+现金两个 tab）/ 策略（含版本历史）
     独立页 成交流水 #/live/<id>/fills —— 会越来越长，服务端分页
   ★ 策略只有【一个】入口（排头那个标签）。原来排头一个、下面又一块
     "策略版本"，是同一件事两处入口 —— 版本历史已并进浮层。 */
/* ============ 实时轮询 ============
   🔴 **实盘页停留时必须自己刷** —— 原来只在进入时 `loadLive` 拉一次，
     于是停在页面上盯盘时数字永远不动，切走再回来才更新（实测：停留 70 秒
     `/api/live/account` 新增 0 个请求、报价时间戳停在 13:56，切走再回来
     才变 13:57）。而这一页正是**盯盘时一直开着**的那一页。
   ★ 判据 `rt_live` 由**服务端**给（realtime.in_session + is_trading_day）——
     前端硬编码交易时段的话，改了时段或遇到半日市就会白轮/漏轮，
     而"多轮几次"不报错、"该轮没轮"更不报错（同"权不权威由服务端给"那条）。
   ★ 收盘后**不轮**：那时没有新数据，每次请求还会触发服务端的
     `_rt_catch_up`（20 秒节流）—— 白打接口，而限流是这条链上唯一的风险。
   🔴 浮层开着时**跳过这一轮**：重建 DOM 会把正在录入的成交表单抹掉。
     ★ 跳过而不是停掉 —— 关掉浮层后应该自动继续，
       "停了就不再动"和一开始的 bug 是同一种坏。 */
const LVPOLL_MS = 60000;              // 与服务端 _rt_loop 同一个节拍

function livePoll(aid){
  stopPoll();
  POLL = setInterval(() => {
    // 离开这一页/换了账户就自己停掉（enterView 也会 stopPoll，这里是兜底）
    if(!(location.hash || '').startsWith('#/live') || LVSEL !== aid){
      stopPoll(); return;
    }
    const mk = $('#stwrap');
    if(mk && mk.className === 'stmodal') return;   // 浮层开着：跳过这一轮
    loadLive(aid, true);
  }, LVPOLL_MS);
}

/* ---- 实盘页的【原地刷新】---------------------------------------------
   🔴 轮询不许重建整块 DOM。原来 `loadLive` 一进来就把 #lvbody 清成
     「读取中…」，然后才发请求 —— 内容先消失、**高度塌陷**，几百毫秒后
     再撑开，看起来就是"整页跳一下"。而这一页正是盯盘时一直开着的那页。
   做法：quiet 轮询只换**会变的那几处**（持仓的实时列 + KPI 板 + 报价时间），
     DOM 结构一行不动 —— 滚动位置、hover、选中的文字全保住。
   🔴 格子内容**一处定义**（`lvRtd`）：渲染与刷新各写一份的话，
     "刷新之后的数字格式跟首次渲染不一样"不报错，只是慢慢分叉。 */
const LV_RT_FIELDS = ['price', 'chg_day', 'pnl_day', 'value',
                      'pnl', 'pnl_pct', 'weight'];

function lvRtd(f, x, P){
  const sgn = v => v == null ? '' : (v >= 0 ? '+' : '');
  const col = upc;
  const A = `data-rt="${esc(x.code)}|${f}"`;
  const asof = (P.asof || '').slice(0, 10);
  switch(f){
    case 'price': return `<td class="rt" ${A}>${
      x.price == null ? '—' : num(x.price, 2)}${x.stale ?
        `<span class="lvwhy" title="当日无行情（停牌），按最后已知价 ${
          esc(x.px_date)} 挂账">停</span>` : ''}${x.rt_src ?
        `<span class="lvwhy" style="color:var(--accent)" title="盘中实时价（${
          esc(x.rt_at || '')}，来源 ${esc(x.rt_src)}）">实</span>` : ''}</td>`;
    case 'chg_day': return `<td class="rt" ${A} style="color:${col(x.chg_day)}"
      title="相对昨收 ${x.preclose == null ? '—' : num(x.preclose, 2)}">${
      x.chg_day == null ? '—'
        : sgn(x.chg_day) + (x.chg_day * 100).toFixed(2) + '%'}</td>`;
    case 'pnl_day': return `<td class="rt" ${A} style="color:${col(x.pnl_day)}"
      title="${x.entry === asof ? '今天建的仓 —— 基准是成交价，不是昨收'
        : '基准是昨收 ' + num(x.preclose, 2)}">${
      x.pnl_day == null ? '—' : sgn(x.pnl_day) + num(x.pnl_day, 2)}</td>`;
    case 'value': return `<td class="rt" ${A}>${
      x.value == null ? '—' : num(x.value, 2)}</td>`;
    case 'pnl': return `<td class="rt" ${A} style="color:${col(x.pnl)}">${
      x.pnl == null ? '—' : sgn(x.pnl) + num(x.pnl, 2)}</td>`;
    case 'pnl_pct': return `<td class="rt" ${A} style="color:${col(x.pnl_pct)}">${
      x.pnl_pct == null ? '—'
        : sgn(x.pnl_pct) + (x.pnl_pct * 100).toFixed(2) + '%'}</td>`;
    case 'weight': return `<td class="rt" ${A}>${
      x.weight == null ? '—' : (x.weight * 100).toFixed(1) + '%'}</td>`;
  }
  return '';
}

/* 结构签名：变了就只能整块重建（新增/卖光了持仓、待办换了、版本重绑）。
   🔴 判据要含**持仓代码序列**而不只是只数：换了一只票但只数不变时，
     逐格 patch 会把新票的数字填进旧票那一行 —— 而它不报错。 */
function lvSig(o){
  const P = o.pos || {}, s = o.signal || {};
  return JSON.stringify([(P.items || []).map(x => x.code),
    s.for_date, s.code_sha256, (s.buy || []).length, (s.sell || []).length,
    o.n_fills, (o.account || {}).name, !!o.rt_live]);
}

/* 只换会变的那几处。返回 false = 结构变了，调用方要走整块重建。 */
function livePatch(o){
  const box = $('#lvbody');
  if(!box || !LVO || lvSig(o) !== lvSig(LVO)) return false;
  const P = o.pos || {};
  (P.items || []).forEach(x => {
    LV_RT_FIELDS.forEach(f => {
      const td = box.querySelector(`td[data-rt="${x.code}|${f}"]`);
      if(td) td.outerHTML = lvRtd(f, x, P);
    });
  });
  const k = $('#lvkpi');
  if(k){
    /* 🔴 **业绩板要原样搬过去。** `#kperf2` 由 `liveEquityTag` 异步补进来
       （它要重放整条权益曲线，所以 quiet 轮询刻意不重拉它），而 `kpiHtml`
       给的只是占位「累计收益 …」—— 直接整块换会把已经填好的业绩板
       **抹回加载态**，容器高度掉一截，看起来还是"跳了一下"。
       实测：DOM 没被重建、数字都对，而 #lvkpi 高度 177 -> 161。
       ★ 头一版保的是 `#kperf` —— 那是个**空的遗留容器**（display:none），
         保它等于什么都没保，而"高度还是变了"看不出是保错了对象。 */
    const kp = k.querySelector('#kperf2');
    const keep = kp ? kp.innerHTML : null;
    k.innerHTML = kpiHtml(o);
    const kp2 = k.querySelector('#kperf2');
    if(keep && kp2) kp2.innerHTML = keep;
  }
  const d = $('#lvday');
  if(d) d.innerHTML = dataDayTag(o);     /* 报价时间在这里 */
  LVO = o;
  return true;
}

async function loadLive(aid, quiet){
  const b=$('#lvbody');
  /* 🔴 quiet（每分钟的轮询）**不清空**：清成「读取中…」会让高度塌陷，
     几百毫秒后再撑开 —— 那就是"整页跳一下"的来源。首次进入才给占位，
     那时本来就是空的、没有东西可跳。 */
  if(!quiet) b.innerHTML='<div class="none">读取中…</div>';
  let o; try{ o=await j('/api/live/account?id='+encodeURIComponent(aid)); }
  catch(e){
    /* ★ 轮询失败**不要**把已经渲好的内容换成错误信息：网络抖一下就把
       持仓表清掉、下一轮又回来，比不刷新更糟。安静地跳过这一轮。 */
    if(!quiet) b.innerHTML='<div class="none">'+esc(e)+'</div>';
    return;
  }
  /* 结构没变就只换数字，DOM 一行不动（滚动位置/hover/选中都保住）。 */
  if(quiet && livePatch(o)){
    if(o.rt_live) livePoll(aid); else stopPoll();
    return;
  }
  /* 期数清单（浮层的期数切换 + 持仓行上的"出处"都用它）。★ 只是清单，
     某一期的完整理由按需再拉 —— 复算一期要重放 30 天 warmup。 */
  await lvWhyLoad(aid);
  LVO=o;
  const a=o.account, sig=o.signal, ro=LV.readonly, P=o.pos||{};
  const it=P.items||[];
  const sgn=x=>x==null?'':(x>=0?'+':'');
  const col = upc;
  b.innerHTML=`
  <div class="lvhead">
    <h2>${esc(a.name)}</h2>
    <span id="lvday">${dataDayTag(o)}</span>
    ${a.code_sha256?`<a class="lvtag on" href="#" id="lvstrat"
        title="源码 / 参数 / 版本历史 / 用这个版本跑过的回测">${esc(a.strategy_path.split('/').pop())}
        @${esc(a.code_sha256.slice(0,8))} ›</a>`
      :'<a class="lvtag" href="#" id="lvstrat">未绑定策略 ›</a>'}
    <span style="flex:1"></span>
    <!-- ★「立即重算」不在这一排：这排是**账户级**动作（记一笔/流水/设置），
         而重算算的是**调仓信号** —— 它属于「今日待办」那一块，
         按钮就该长在它作用的那块里。挪过去了（见 sigHtml）。 -->
    <button class="btn" id="lvrec" ${ro?'disabled':''}>✎ 记一笔</button>
    <a class="btn" href="#/live/${a.id}/fills">流水${o.n_fills==null?'':' '+o.n_fills}</a>
    <!-- 🔴 做成按钮而不是小灰链接：一开始放在待办标题行里、样式与旁边的
         "版本 9FB82061" 一模一样，实测**人找不到它** ——
         看不出能点的入口 = 没有入口（同 backLink 那条的反面）。 -->
    <a class="btn" href="#/live/${a.id}/why">选股理由</a>
    <button class="btn" id="lvset">⚙</button>
  </div>
  <div class="lvmsg" id="lvmsg"></div>
  <div id="lvkpi">${kpiHtml(o)}</div>
  ${sigHtml(sig, o.alert, o.alert_why)}
  <div class="lvsec"><h3>当前持仓
      ${it.length?`<span class="lvwhy">${it.length} 只</span>${posHelp(P)}
        ${Object.keys((LVWHYH||{}).entry||{}).length
          ?'<span class="lvwhy">名称后的 <b>?</b> 进它建仓那一期的选股理由</span>':''}`:''}</h3>
    ${it.length?`<div class="pw"><table class="lvt lvpos">
      <tr><th>代码</th><th class="tx">名称</th><th class="rt">股数</th>
          <th class="rt">成本</th>
          <th class="rt">现价</th>
          ${Object.entries(LVSORT_COLS).map(([k,v])=>lvSortTh(k,v)).join('')}
          <th class="tx">建仓</th></tr>
      ${lvSortRows(it).map(x=>`<tr>
        <td>${skLink(x.code, x.code)}</td>
        <td class="tx">${skLink(x.code, x.name||'')}${lvHoldMark(aid, x.code)}</td>
        <td class="rt">${num(x.shares)}</td>
        <td class="rt" title="摊薄成本（含买入费 ${num(x.buy_fee,2)}）—— 浮盈按它算。&#10;成交均价 ${num(x.cost,4)}（引擎 entry_price 用的是这个，不含费）。&#10;保本价 ${x.breakeven==null?'—':num(x.breakeven,3)}（含估算卖出费 ${x.exit_fee_est==null?'—':num(x.exit_fee_est,2)}）">${num(x.cost_net,3)}</td>
        ${LV_RT_FIELDS.map(f=>lvRtd(f, x, P)).join('')}
        <td class="lvwhy tx">${esc(x.entry)}</td></tr>`).join('')}
      </table></div>
      <!-- 持仓表包在 .pw 里，见本文件顶部注释 -->
      ${P.fee_estimated_n?'<div class="lvwhy" style="margin-top:6px">'
        +'⚠ 有 <b>'+P.fee_estimated_n+'</b> 笔买入的费用是<b>估算</b>的，'
        +'成本跟着也是估算 —— 对完账单可在流水页「冲正 + 重录」填实际值。'
        +'</div>':''}`
      :`<div class="none">空仓${o.n_fills?'':' —— 还没录过成交，点「✎ 记一笔」'}</div>`}
  </div>`;
  bindHelp();
  /* ★ 轮询时不重拉业绩板：它要重放整条权益曲线，而累计收益/年化
     这些量一天内变化很小 —— 每分钟重放一次纯属浪费。 */
  if(!quiet) liveEquityTag(aid);
  if($('#todofold')) $('#todofold').onclick=ev=>{ ev.preventDefault();
    LVTODO = !((LVTODO==null) ? !!o.alert : LVTODO); loadLive(aid); };
  $('#lvstrat').onclick=ev=>{ ev.preventDefault(); openStrat(aid, a.code_sha256||''); };
  $('#lvset').onclick=()=>openSettings(aid, a, ro);
  $('#lvrec').onclick=()=>openRecord(aid, sig, ro);
  /* 持仓表的表头排序 —— innerHTML 之后才存在，所以每次全量渲染都要重绑。 */
  lvSortBind(aid);
  /* 盘中才轮询；收盘后停掉并在页面上说清（不说的话人会以为坏了）。 */
  if(o.rt_live) livePoll(aid); else stopPoll();

  /* ★ 用 if 保护：出信号失败那支返回的是 lvwarn，里面没有这个按钮，
     不保护就是 `null.onclick` -> TypeError -> **整块渲染中断**。 */
  if($('#lvtick')) $('#lvtick').onclick=async()=>{
    const m=$('#lvmsg'); m.className='lvmsg'; m.textContent='算中…（重放 30 天 warmup）';
    try{ await post('/api/live/tick',{id:aid}); loadLive(aid); }
    catch(e){ m.className='lvmsg bad'; m.textContent=String(e); }
  };
}

/* 权益单独异步拉 —— 它要重放整条曲线，不该拖住主视图。 */
/* ---- KPI 板 ----------------------------------------------------------
   ★ 账户的数字集中一处，不再是顶栏一串标签 —— 标签栏里塞了权益/现金/收益
     之后，眼睛得在标题、标签、持仓汇总三处来回跳才凑得出"我现在怎么样"。

   ★ 分两段：**资产**（现在有多少、怎么分布）与**业绩**（赚了多少、
     波动多大、多久）。业绩那段要重放整条权益曲线，所以异步补 ——
     它不该拖住主视图（持仓和待办才是天天看的）。

   ★ 不足 20 个交易日不显示年化：把两周的收益乘 12 倍是误导，
     而那个数会被拿去跟回测年化比。宁可显示"—"并说明原因。 */

function kpiHtml(o){
  const P=o.pos||{}, sgn=x=>x==null?'':(x>=0?'+':'');
  const col = upc;
  const wt=(P.equity&&P.market_value!=null)?P.market_value/P.equity:null;
  return `<div class="kpi">
    ${cell('总资产', num(P.equity,2), '现金 + 持仓市值', 'big')}
    ${cell('持仓市值', num(P.market_value,2),
           (P.items||[]).length+' 只 · 成本 '+num(P.cost,2))}
    ${cell('可用现金', num(P.cash,2),
           wt==null?'':'仓位 '+(wt*100).toFixed(1)+'%')}
    ${cell('持仓浮盈'+pnlHelp(P),
           `<span style="color:${col(P.pnl)}">${sgn(P.pnl)}${num(P.pnl,2)}</span>`,
           (P.pnl_pct!=null?sgn(P.pnl_pct)+(P.pnl_pct*100).toFixed(2)+'%':''))}
    ${cell('当日盈亏',
           P.pnl_day==null?'—':
           `<span style="color:${col(P.pnl_day)}">${sgn(P.pnl_day)}${num(P.pnl_day,2)}</span>`,
           /* ★ 百分比要标【分母是什么】：这一格是持仓的当日浮动，
                业绩板那格「今日」是**账户**当日收益（分母是总资产，还含
                当天已实现的买卖与费用）。不标的话两个 −0.15%/−0.16%
                摆在同一屏上，看着像其中一个算错了。 */
           (P.pnl_day!=null&&P.market_value)
             ? '持仓 '+(P.pnl_day/(P.market_value-P.pnl_day)>=0?'+':'')
               +(P.pnl_day/(P.market_value-P.pnl_day)*100).toFixed(2)+'%'
             : '')}
    <div id="kperf" style="display:none"></div>
  </div>
  <div class="kpi" id="kperf2">
    ${cell('累计收益','…','时间加权 TWR')}
    ${cell('加权年化','…','')}${cell('最大回撤','…','')}
    ${cell('当前回撤','…','')}${cell('最近一日','…','')}
    ${cell('投资时间','…','')}</div>`;
}

async function liveEquityTag(aid){
  const el=$('#kperf2'); if(!el) return;
  const pct=x=>(x==null?'—':(x>=0?'+':'')+(x*100).toFixed(2)+'%');
  const col = upc;
  try{
    const e=await j('/api/live/equity?id='+encodeURIComponent(aid));
    const st=e.stats;
    if(!st){
      el.innerHTML=cell('业绩','—', esc(e.note||'还没有成交'));
      return;
    }
    /* ★ 盘中：曲线最后一点是用实时价估的（服务端 stats.intraday）。
         必须标出来 —— "累计收益"含不含今天的浮动是两个不同的数，
         而上面的总资产/持仓浮盈本来就是实时的，不标就成了同屏两个口径。 */
    const iv=st.intraday, ivs=iv?'盘中 '+esc(String(iv.at||'').slice(5)):'';
    el.innerHTML=
      /* ★ 「累计收益」是**业绩明细页的入口** —— 曲线（资金/收益/回撤）与
           年月日收益表都在 #/live/<id>/perf。
         🔴 画成带 › 的链接而不是纯文字：**看不出能点的入口 = 没有入口**
           （选股理由那次实测人找不到，见 live.js 头部那条）。 */
      cell(`<a href="#/live/${encodeURIComponent(aid)}/perf" class="lpin"
              title="资金曲线 / 收益曲线 / 回撤 / 年月日收益表"
              >累计收益 ›</a>`+perfHelp(st),
        `<span style="color:${col(st.twr)}">${pct(st.twr)}</span>`,
        /* ★ 百分比旁边必须有【金额】—— 只给 % 的话，它和上面的
             "持仓浮盈 +3,698.55" 就没有能对上的数，看着像两回事。
             金额 = 期末 − 起点 − 净入金（真正多出来的钱）。 */
        (st.pnl_total==null?''
          :`<span style="color:${col(st.pnl_total)}">${st.pnl_total>=0?'+':''}${
              num(st.pnl_total,2)}</span> 元　`)
        +'TWR'+(ivs?' · '+ivs:' · 按收盘'))
     +cell('加权年化',
        st.twr_annual==null?'—'
          :`<span style="color:${col(st.twr_annual)}">${pct(st.twr_annual)}</span>`,
        st.twr_annual==null?'不足 20 个交易日，不折年化':'与回测年化同口径')
     +cell('最大回撤', pct(-st.max_drawdown).replace('-','−'),
        st.max_drawdown_at?'最深 '+esc(st.max_drawdown_at):'')
     +cell('当前回撤',
        st.drawdown_now==null?'—':pct(-st.drawdown_now).replace('-','−'),
        '距历史最高')
     +cell(iv?'今日':'最近一日',
        st.day_ret==null?'—'
          :`<span style="color:${col(st.day_ret)}">${pct(st.day_ret)}</span>`,
        (st.day_pnl==null?'':(st.day_pnl>=0?'+':'')+num(st.day_pnl,2)+' 元')
        +(ivs?'　'+ivs:''))
     /* ★ 「交易费用」独立一格。原来它挤在"投资时间"的小字里写成
          "已付费 56.51（年化拖累 1.72%）" —— 两个数都看不懂：
          "已付费"没说是什么费，"年化拖累"更是把开户两天的费用乘了 122 倍。 */
     +cell('交易费用',
        st.fee_paid==null?'—':num(st.fee_paid,2),
        st.fee_pct==null?'':'占本金 '+(st.fee_pct*100).toFixed(3)+'%'
          +(st.fee_drag_annual!=null
            ? '　≈ 年化 −'+(st.fee_drag_annual*100).toFixed(2)+'%'
            : '　样本太短，不折年化'))
     +cell('投资时间', st.days+' 个交易日',
        esc(st.start||'')+' 起'
        +(st.net_deposit?' · 净入金 '+num(st.net_deposit,2):''));
    bindHelp();
  }catch(err){ el.innerHTML=cell('业绩','—', esc(String(err))); }
}


/* ---- 持仓表排序 -----------------------------------------------------
   点表头在【当日 / 当日盈亏 / 市值 / 浮盈 / 幅度 / 仓位】之间切换，
   再点一次反向。
   ★ 首次点击给**降序** —— 这六个都是「越大越好」的量，人点它是想看
     "最赚的/最大的是哪个"（同回测页那条：换列时默认方向按哪边更好给，
     一律升序会把最差的排最前面）。
   🔴 排序状态是**模块级**的（`LVSORT`），不是局部变量 —— `loadLive` 每
     分钟重渲染一次，存在局部里的话刚点的排序立刻被冲掉。
   🔴 与原地刷新（`livePatch`）**天然兼容**：patch 靠 `td[data-rt="code|field"]`
     找格子，与 DOM 里的行序无关。所以排序后 60 秒的自动刷新照样只换数字。
   ★ 不持久化到 localStorage：排序是"我现在想看什么"，下次打开该回到
     默认（按仓位降序 = 与建仓顺序无关的自然视角）。同「待办折叠状态
     不持久化」那条。 */
const LVSORT_COLS = {
  chg_day: '当日', pnl_day: '当日盈亏', value: '市值',
  pnl: '浮盈', pnl_pct: '幅度', weight: '仓位',
};
let LVSORT = {k: null, desc: true};

function lvSortRows(items){
  if(!LVSORT.k) return items;
  const k = LVSORT.k, sgn = LVSORT.desc ? -1 : 1;
  /* 🔴 `null` 一律排最后（不管升降序）—— 停牌股取不到价，那几行的
     当日/浮盈都是 null。把 null 当 0 参与排序的话，它们会混在正负之间，
     看着像"这只票今天不涨不跌"，而事实是**没有数据**。 */
  return items.slice().sort((a, b) => {
    const x = a[k], y = b[k];
    if(x == null && y == null) return 0;
    if(x == null) return 1;
    if(y == null) return -1;
    return (x - y) * sgn;
  });
}

function lvSortTh(k, label){
  const on = LVSORT.k === k;
  return `<th class="rt lvsth${on ? ' on' : ''}" data-sk="${k}"
    title="点击按${label}排序${on ? '（再点反向）' : ''}">${label}${
    on ? (LVSORT.desc ? ' ▼' : ' ▲') : ''}</th>`;
}

/* 点表头：**就地重排，不重新取数、不重建 DOM。**
   🔴 原来调 `loadLive(aid)` —— 它一进来就把 `#lvbody` 清成「读取中…」，
     几百毫秒后内容才回来：**高度先塌陷再撑开**，那就是"点一下整页跳一下"。
     而排序**根本不需要服务端** —— 数据已经在 `LVO.pos.items` 里，
     要变的只有行的先后与表头那个箭头。
   ★ 与 CLAUDE.md 里「刷新只换数字，不许重建 DOM」是**同一个坑的另一半**：
     那次修的是每分钟的轮询，这次是点表头。判据也同一条 ——
     DOM 不重建（滚动位置、hover、选中的文字全保住）。
   ★ `appendChild` 是**移动**不是复制，所以那些 `data-rt` 格子连同
     `livePatch` 的定位依据一起原样保留。
   返回 false = 认不出现在的表结构（比如刚换了账户），交给调用方整块重建。 */
function lvSortApply(aid){
  const tb = document.querySelector('#lvbody table.lvpos');
  if(!tb || !LVO) return false;
  const items = ((LVO.pos || {}).items) || [];
  const rows = Array.from(tb.querySelectorAll('tr'))
                    .filter(r => !r.querySelector('th'));
  if(!rows.length || rows.length !== items.length) return false;
  const byCode = new Map();
  rows.forEach(r => {
    const c = r.querySelector('td[data-rt]');
    if(c) byCode.set(String(c.dataset.rt).split('|')[0], r);
  });
  const want = lvSortRows(items).map(x => x.code);
  if(want.length !== rows.length || want.some(c => !byCode.has(c))) return false;
  const host = rows[0].parentNode;
  want.forEach(c => host.appendChild(byCode.get(c)));
  /* 表头箭头复用 `lvSortTh` 这一处定义（渲染与重排各写一份就会分叉）。
     🔴 `outerHTML` 把节点连同 onclick 一起换掉了 —— 必须重绑，
        不然第二次点就没反应，而那不报错。 */
  tb.querySelectorAll('th.lvsth').forEach(th => {
    th.outerHTML = lvSortTh(th.dataset.sk, LVSORT_COLS[th.dataset.sk]);
  });
  lvSortBind(aid);
  return true;
}

/* innerHTML 之后才存在的元素要重新绑事件 —— 只在渲染开头绑的话点了没反应
   且不报错（对比页「移除」栽过）。所以每次渲染完都调它。 */
function lvSortBind(aid){
  document.querySelectorAll('#lvbody th.lvsth').forEach(th => {
    th.onclick = () => {
      const k = th.dataset.sk;
      if(LVSORT.k === k) LVSORT.desc = !LVSORT.desc;
      else { LVSORT.k = k; LVSORT.desc = true; }   /* 换列 -> 默认降序 */
      /* 就地重排；认不出表结构才退回整块重建（那时本来就要重建） */
      if(!lvSortApply(aid)) loadLive(aid);
    };
  });
}

/* ---- 实盘里的代码/名称 -> 个股速览【浮层】 ---------------------------
   🔴 原来是 `target="_blank"` 新标签页，理由是"实盘页一直开着（待办、
     正在录一半的成交），跳走再回来状态就没了"。浮层同样满足这一点，
     而且**不用手动关标签**、Esc 就回到原位。
   ★ 实现在 `shared/stockpop.js`（`spLink`）—— 盘面/自选/买点那些页面
     也要同样的效果，所以放共享层，不放实盘页。 */
const skLink=(code, text, cls)=>spLink(code, text, cls)

/* ★ 数据日与报价时间写在【同一个标签】里，紧跟账户名。
     原来数据日在账户后面、"实时 09-03 13:19"在持仓浮盈下面 —— 两处各写
     一半，看着像自相矛盾（"数据到 09-02" vs "实时 09-03"）。其实它们是
     两件事：**日线面板**到 09-02，**现价**是 09-03 盘中的。挨着放就不矛盾了。 */
function dataDayTag(o){
  const dd=o.data_day, sg=o.signal||{}, asof=sg.data_asof, P=o.pos||{};
  if(!dd) return '<span class="lvtag" title="拿不到面板最新日 —— serve.py 可能是旧进程">数据日 —</span>';
  const behind=asof&&asof<dd;
  let px='', t='';
  if(P.price_src==='实时'){
    px=' · 实时 '+esc((P.rt_at||'').slice(5));
    t='；现价是 '+(P.rt_at||'')+' 的盘中实时价';
  }else if(P.price_src==='部分实时'){
    px=' · 部分实时 '+P.rt_n+'/'+(P.items||[]).length;
    t='；'+P.rt_n+' 只有盘中实时价（最新 '+(P.rt_at||'')
      +'），其余按 '+(P.asof_close||'')+' 收盘';
  }else if((P.items||[]).length){
    px=' · 收盘价';
    t='；现价用的是 '+(P.asof_close||dd)+' 收盘（没有盘中实时价）';
  }
  /* ★ **刷新状态也写在这个标签里** —— 盘中每分钟自动刷、收盘后不刷。
     🔴 不说的话人分不清"数字没变"和"页面坏了"：这一页正是盯盘时一直
       开着的那一页，而收盘后现价本来就不会动（同"非交易时段要把原因
       给页面"那条）。 */
  const live=o.rt_live;
  const rf = live===true ? ' · 每分钟自动刷新'
    : live===false ? ' · 已收盘，不自动刷新' : '';
  const rt = live===true
      ? '；盘中每 60 秒自动刷新一次（与服务端抓取同一个节拍）'
    : live===false
      ? '；现在不是交易时段 —— 不自动刷新（收盘后没有新的盘中数据，'
        + '硬刷只会白打接口）。要看最新的按「立即重算」或刷新页面'
      : '';
  return `<span class="lvtag${behind?' warn':''}"
    title="日线行情最新到 ${dd}${t}${rt}${behind?'。⚠ 当前信号是用 '+asof+' 的数据算的 —— 点「立即重算」':''}"
    >数据日 ${dd}${px}${behind?' · 信号用的是 '+asof:''}<span class="lvwhy">${rf}</span></span>`;
}

/* ---- 两块口径说明（点 ⓘ 才展开）--------------------------------------
   ★ 这些是"这个数怎么算的"，只在头一次看时要读 —— 常驻主页面的话，
     天天要看的数字被解释文字推远一屏。警告不在这里（见表下的 ⚠）。 */
function pnlHelp(P){
  return helpIcon('pnl', `<b>持仓浮盈</b> = 市值 − 摊薄成本额，<b>已含买入费</b>
    （券商 App 的口径）—— 费用是真金白银出去了，不算进成本等于把浮盈报高。
    往返自证：<code>浮盈 = 现值 − (本金 + 买入费) = 现值 − 现金减少额</code>。<br>
    ${P.buy_fee?'当前含买入费 <b>'+num(P.buy_fee,2)+'</b>；':''}
    不含费的口径（专门用来和回测对照）是
    ${P.pnl_gross==null?'—':'<b>'+num(P.pnl_gross,2)+'</b>'}。<br>
    再扣估算卖出费 ${P.exit_fee_est?num(P.exit_fee_est,2)+'（含印花税万5）':'—'}
    就是<b>全平落袋</b> ${P.pnl_net==null?'—':'<b>'+num(P.pnl_net,2)+'</b>'}，
    不会重复计费：买入费只在成本里出现一次，卖出费只在这一步出现一次。<br>
    <b>当日盈亏</b> = Σ 每一批 ×（现价 − 基准）。基准分两种：<b>今天买的</b>那批
    用它自己的成交价（今天开盘时还没持有它），<b>以前买的</b>用昨收。<br>
    两者都不含<b>已收分红</b>（<code>entry_price</code> 除权时不缩，就为了把
    价差与分红分开记）；分红到账请在「✎ 记一笔 › 现金」里录一条。`);
}
function perfHelp(st){
  const iv=st.intraday;
  return helpIcon('perf', `<b>累计收益</b>用<b>时间加权（TWR）</b>，不是
    "期末 ÷ 期初 − 1" —— 有入金时后者会把入金算成收益（实测：入 50 万后
    简单相除是 502%，TWR 是 1.59%）。TWR 在每个有外部现金流的日子把区间切开
    再连乘，这也是能和<b>回测年化</b>直接比的口径。<br>
    ${iv?'★ 曲线最后一点是<b>盘中'+esc(String(iv.at||''))+'用实时价估的</b>'
        +'（'+iv.n_rt+'/'+iv.n_pos+' 只有实时价）—— 所以这里的累计收益、'
        +'回撤、今日都含今天的浮动。收盘后同步了日线，这一点会被权威的'
        +'日线收盘替换。'
      :'★ 曲线全部按<b>收盘价</b>：今天还没有实时价（非交易时段，或行情'
        +'没抓到）。'}<br>
    <b>百分比与金额的分母不一样，这是正常的</b>：这里的 %
    是 TWR（分母是**整段资金**，闲置现金会摊薄它），金额是
    <code>期末 − 起点 − 净入金</code>；而上面「持仓浮盈」的 %
    分母是**投出去的那部分**（摊薄成本额）。没有已实现盈亏和入金时，
    两者的**金额相等**${st.pnl_total!=null?'（现在是 '
      +(st.pnl_total>=0?'+':'')+num(st.pnl_total,2)+'）':''}，
    百分比不等。<br>
    起点是<b>开户那一刻的资金</b>${st.equity_start!=null?'（'
      +num(st.equity_start,2)+'）':''}，不是第一天的收盘 ——
    🔴 从收盘起算会把**建仓第一天的盈亏整个丢掉**（实测：红利 09-01
    当天 +1.16%，从收盘起算的 TWR 是 −0.78%，而净值其实是 +0.37%）。<br>
    <b>「今日」是账户当日收益</b>：分母是总资产，且含当天**已实现**的买卖与
    费用；而资产板那格「当日盈亏」只算**持仓**的当日浮动、分母是持仓市值。
    没有当日成交时两者金额相同，调仓日会不同 —— 不是哪个算错了。<br>
    <b>不足 20 个交易日不折年化</b>：把两周的收益乘 12 倍是误导，而那个数
    会被拿去跟回测年化比。宁可显示"—"。<br>
    <b>交易费用</b>是已付的佣金 + 规费 + 过户费 + 印花税合计（成交流水里
    每笔那个"费用"之和；估算的那几笔也算进来）。<b>占本金</b> =
    费用 ÷（初始资金 + 净入金）—— 分母不用权益，权益含浮盈会低估拖累。<br>
    <b>年化拖累</b>是"按当前这个交易频率跑满一年，费用会吃掉年化几个点"。
    🔴 它同样要够长的样本：开户两天就把两笔建仓的费用乘 122 倍，
    会得出"年化拖累 1.72%"这种纯外推的数 —— 所以不到 20 个交易日只报绝对值。`);
}
function posHelp(P){
  return helpIcon('pos', `<b>成本</b>这一列是<b>摊薄成本</b> =
    (成交额 + 买入费) / 股数 —— 券商 App 说的"摊薄成本价"就是它，浮盈按它算。
    部分卖出时，那一批的买入费按<b>剩余股数比例</b>留在成本里。<br>
    另外两个价放在这一列的悬浮提示里：<b>成交均价</b>是喂给引擎
    <code>entry_price</code> 的那个数（<b>止损、吊灯、红利税档位都读它</b>，
    所以它不含费、不能动），也是能与回测 <code>trades.ret</code> 对照的口径
    （引擎的 ret 只含滑点不含佣金）；<b>保本价</b> = 摊薄成本 ÷(1−卖出费率)，
    卖到它才不亏。<br>
    现价带「实」的是盘中实时价，与自选页<b>共用同一个实时库</b>
    （<code>datalake/rt/</code>）；带「停」的是停牌，按最后已知价挂账。<br>
    ${P.exit_fee_est?'当前全部卖出的估算费用合计 <b>'+num(P.exit_fee_est,2)+'</b>。':''}`);
}


function sigHtml(s, alert, alertWhy){
  /* 🔴 无信号那支**也要有按钮**：文案写着"点「立即重算」"，而按钮已从
     账户头部挪进这一块 —— 不给的话那句话指向一个不存在的按钮
     （同 backLink 那条：给一个点了没反应的入口比不给更糟）。
     而且恰恰是"还没有信号"时最需要它。 */
  if(!s) return `<div class="lvsec" style="margin-bottom:14px"><h3>今日待办
      <span style="flex:1"></span>
      <button class="btn" id="lvtick" ${LV.readonly?'disabled':''}
        >立即重算</button></h3>
    <div class="none">还没有信号 —— 点「立即重算」</div></div>`;
  if(s.error) return `<div class="lvwarn"><b>出信号失败</b><br>${esc(s.error).replace(/\n/g,'<br>')}</div>`;
  const w=(s.warnings||[]).map(x=>`<div class="lvwarn">${esc(x)}</div>`).join('');
  /* 🔴 早上重算出来的清单与昨晚【不一致】时必须显红说清差异 ——
     人可能已经按昨晚那份准备好委托了（A 股公告集中在 16:00~22:00，
     其中 ST/停牌是次日生效的，18:10 那份算不到）。
     静默覆盖等于让他拿着一份已经作废的清单去下单。
     ★ 只在"决策变了"时显红；数据变了而清单没变只记 recomputed_at，
       不在这里出现 —— 天天一条"重算过"的话，人就不看这个位置了。 */
  const revs=s.revisions||[];
  const rv=revs.length?`<div class="lvwarn"><b>⚠️ 这份清单被重算过
      ${revs.length} 次，与最初那份不一致</b>${revs.map(r=>{
        const d=r.diff||{}, seg=[];
        for(const k of ['buy','sell','hold']){
          const nm={buy:'买入',sell:'卖出',hold:'持有'}[k];
          if((d[k+'_added']||[]).length) seg.push(`${nm}新增 ${d[k+'_added'].join('、')}`);
          if((d[k+'_removed']||[]).length) seg.push(`${nm}移除 ${d[k+'_removed'].join('、')}`);
        }
        return `<div style="margin-top:4px">第 ${r.rev} 版（${esc((r.built_at||'').slice(5,16))}
          → 被 ${esc((r.replaced_at||'').slice(5,16))} 覆盖）：${
          seg.length?esc(seg.join('；')):'（清单未变，只是指纹变了）'}
          <span class="lvwhy">　旧版留在 ${esc(r.archived||'')}</span></div>`;
      }).join('')}<div style="margin-top:4px">如果已按之前那份准备了委托，
      <b>请照现在这份核对</b>。</div></div>`:'';
  /* 数据动过但清单没变 —— 低调说一句就够，不用警告样式 */
  const rc=(!revs.length&&(s.recomputed_at||[]).length)
    ? `<div class="lvwhy" style="margin:4px 0">数据更新后重算过
        ${s.recomputed_at.length} 次，清单未变（最近 ${
        esc((s.recomputed_at[s.recomputed_at.length-1]||'').slice(5,16))}）。</div>`
    : '';
  const why={stop:'止损', limit_up_exit:'炸板离场', rebalance_out:'调仓换出'};
  /* ★ 调仓日是【纯日历】的，可以提前很久算出来；清单不是（要 T-1 收盘数据）。
     这两件事在页面上必须分清楚 —— 否则会以为提前几天就能看到买什么。
     提前提示的理由：调仓日当天早上才打开就已经晚了（09:30 开盘调仓，
     而信号是前一晚算的）。 */
  const W='日一二三四五六';
  const du=s.days_until_rebalance;
  const rb=s.next_rebalance;
  let banner='';
  if(s.is_rebalance_day){
    banner=`<div class="lvrb now"><b>今天就是调仓日</b> —— 下面的清单按
      ${esc(s.data_asof)} 收盘算好了，开盘前挂限价单即可。</div>`;
  }else if(rb){
    const wd=new Date(rb+'T00:00:00').getDay();
    banner=`<div class="lvrb${du!=null&&du<=2?' soon':''}">
      下次调仓 <b>${esc(rb)}（周${W[wd]}）</b>${du!=null?`　还有 <b>${du}</b> 个交易日`:''}
      <span class="lvwhy">　清单要等 ${esc(rb)} 的<b>前一个交易日收盘后</b>才算得出来
        —— 调仓日看的是 T-1 数据。</span></div>`;
  }
  const strip=(s.upcoming||[]).length?`<div class="lvstrip">${
    (s.upcoming||[]).map(x=>`<span class="lvd${x.is_rebal?' rb':''}"
      title="${x.date}${x.is_rebal?' · 调仓日':''}">${x.date.slice(5)}<i>周${W[new Date(x.date+'T00:00:00').getDay()]}</i></span>`).join('')
    }</div>`:'';
  /* ★ 没到警示时间就默认收起：非调仓日、没有止损/炸板卖出时，待办里其实
     什么都没有，而它占着主视图最上面一屏。判据用服务端的 alert
     （live.signal_alert）—— 和账户列表的红点同一个，分两处写就会分叉。
     ★ 手动展开的状态**不记住**（LVTODO 只在本次有效）：记住了会让人下次
     错过"到点了自己打开"这件事，而那正是这个折叠的意义。 */
  const open = (LVTODO==null) ? !!alert : LVTODO;
  const sum = alert ? (alertWhy||[]).join(' · ')
    : ((s.sell||[]).length+(s.buy||[]).length
        ? (s.sell||[]).length+' 卖 / '+(s.buy||[]).length+' 买'
        : '没有要动的');
  /* ★ rv（清单被改过）放在 w 之后、正文之前 —— 它是「这份清单还能不能照着
     下单」的前提，不能藏在下面。 */
  return w+rv+`<div class="lvsec${alert?' lvalert':''}" style="margin-bottom:14px">
    <h3>${alert?'<span class="adot"></span> ':''}${esc(s.for_date)} 待办
        ${s.is_rebalance_day?'· 调仓日':'· 非调仓日'}
        <span class="lvtag">数据 ${esc(s.data_asof)} 收盘</span>
        <span class="lvwhy">版本 ${esc(s.code_sha)}</span>
        <span style="flex:1"></span>
        <button class="btn" id="lvtick" ${LV.readonly?'disabled':''}
            title="按当前绑定版本与参数重算这一期的清单（要重放 30 天 warmup，几秒）"
            >立即重算</button>
        <a href="#" id="todofold" class="lvwhy">${open?'收起 ▾':'展开 ▸'}</a></h3>
    ${open?'':`<div class="lvwhy" style="padding:2px 0">${esc(sum)}${
       rb?' · 下次调仓 '+esc(rb)+(du!=null?'（还有 '+du+' 个交易日）':''):''}</div>`}
    <div style="display:${open?'':'none'}">
    ${rc}${banner}${strip}
    <div class="lvgrid">
      <div><b style="color:var(--up)">卖出 ${(s.sell||[]).length} 只</b>
        ${(s.sell||[]).length?`<table class="lvt lvsell"><tr><th>代码</th><th class="tx">名称</th>
            <th class="rt">股数</th><th class="rt">参考价</th><th class="tx">原因</th></tr>
          ${s.sell.map(x=>`<tr><td>${skLink(x.code, x.code)}</td><td class="tx">${skLink(x.code, x.name)}${lvWhyMark(x)}</td>
            <td class="rt">${num(x.shares)}</td>
            <td class="rt">${num(x.ref_price,2)}</td><td class="lvwhy tx">${esc(why[x.reason]||x.reason)}</td></tr>`).join('')}</table>`
          :'<div class="none">无</div>'}</div>
      <div><b style="color:var(--down)">买入 ${(s.buy||[]).length} 只</b>
        ${(s.buy||[]).length?`<table class="lvt lvbuy"><tr><th>代码</th><th class="tx">名称</th>
            <th class="rt">股数</th><th class="rt">限价</th><th class="rt">金额</th></tr>
          ${s.buy.map(x=>`<tr><td>${skLink(x.code, x.code)}</td><td class="tx">${skLink(x.code, x.name)}${lvWhyMark(x)}</td>
            <td class="rt">${num(x.shares)}</td>
            <td class="rt">${num(x.limit,2)}</td><td class="rt">${num(x.amount)}</td></tr>`).join('')}</table>
          <div class="lvwhy">限价 = T-1 收盘 × 1.05（防高开买不进）；twopass 分配，估余 ${num(s.left_est)}
            ${(s.explain||{}).captured?'· 名称后的 <b>?</b> 是选中理由，逐只指标看顶上的「选股理由」':''}</div>`
          :'<div class="none">无</div>'}</div>
      <div><b>持有不动 ${(s.hold||[]).length} 只</b>
        <div class="lvwhy" style="margin-top:4px">${(s.hold||[]).map(x=>
          skLink(x.code, x.name||x.code)+lvWhyMark(x)).join('、')||'无'}</div></div>
    </div></div></div>`;
}

function parseBulk(txt){
  const out=[], bad=[];
  txt.split(/\n/).forEach((ln,i)=>{
    ln=ln.trim(); if(!ln) return;
    const p=ln.split(/[\s,\t]+/).filter(Boolean);
    if(p.length<5){ bad.push('第'+(i+1)+'行字段不足5个（日期 代码 买卖 股数 价格 [费用]）'); return; }
    const side=/买|buy|b/i.test(p[2])&&!/卖/.test(p[2])?'buy':'sell';
    /* 第 6 列是可选费用。★ 省略时传 null 而不是 0 —— 服务端据此估算；
       传 0 等于"明确说没有费用"，两者语义不同。 */
    /* 价格位填 '-' = 按当日开盘价（批量里没法"留空"，位置会错乱）。 */
    out.push({trade_date:p[0], code:p[1].toUpperCase(), side:side,
              shares:parseInt(p[3],10),
              price:(p[4]==='-'?null:parseFloat(p[4])),
              fee:(p.length>=6&&p[5]!=='')?parseFloat(p[5]):null, source:'bulk'});
  });
  return {rows:out, bad:bad};
}
