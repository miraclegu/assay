/* ============ 实盘 · 选股理由（独立页 #/live/<id>/why）============
   一期一段，**按排名依次列出**当期候选池里的每一只：选中的、备选的、
   没轮到的、被剔除的，各带自己的指标。

   数据全部来自信号 JSON 的 `explain` —— 服务端把策略**当时真实跑出来的过程**
   记下来（lv/explain.py），这里只负责画。
   🔴 前端不认识任何一个指标列的含义，也不认识"备选"是什么：指标的标签与量纲
     来自服务端的 `metric_meta`，"备选/缓冲区/目标池"来自 `set_labels`
     （那是**策略自己在 g 里声明的集合**，不是我们判的）。
     本项目栽过前端硬编码业务判据那次（把 `tdx.raw_holidays` 误判成"不权威"，
     每次打开实盘页都弹一条假告警）——**假告警看多了就不看告警了**。

   ★ **做成独立页而不是浮层**：一开始做的是浮层 + 一个 `.lvwhy` 小链接，
     实测**人找不到它** —— 那个链接和旁边的"版本 9FB82061"长得一模一样，
     一个能点的东西被画成了标签。同 backLink 那条的反面：
     **看不出能点的入口 = 没有入口**。而且这一页会越来越长（每天一期），
     独立页 + 服务端分页是本项目对这种东西的既定做法（同成交流水）。

   🔴 **老信号里没有理由**（这个功能 2026-09-05 才加）。那几期要**事后复算**，
     而复算出来的**可能不是当时那一份**（面板会被修正 —— 本项目修过 volume
     74,952 行、复权因子）。所以复算结果一律带自证：`same` = 复算出的
     买/卖/持有与当时存的逐个相同；`differs` = **不是当时那份**。 */

const LVWHYP = 3;       /* 每页几期。一期可能有几十行，三期一屏正好翻 */
/* 每组默认显示到第几名。★ 20 之后全是"没轮到"，看不出信息 —— 而**一定要显示**
   的那些（选中/持有/卖出/备选/缓冲区/被剔除）不受这个数限制，
   哪怕它排在 40 名也照样列出来（"差一点就选上"正是要看的）。 */
const LVWHYN = 20;
let LVWHYALL = {};      /* {期-组: true} 展开过的那几块 */
let LVWHYH = null;      /* {entry:{code:那一期}} —— 账户页拿它标持仓的出处 */

/* 状态 -> 显示。★ `not_taken` 与 `drop` 必须分开：
   "排在后面没轮到" 和 "被规则剔掉" 是两回事，混成"未选中"就丢了信息。 */
const LVST = {buy: ['选中·买入', 'st-buy'], hold: ['选中·持有', 'st-hold'],
              sell: ['卖出', 'st-sell'], not_taken: ['没轮到', 'st-none'],
              drop: ['被剔除', 'st-drop']};

/* 按服务端给的 kind 格式化。money 用「亿/万」——13.8 亿比 1384414777 好读，
   而候选池是拿来横向扫的。 */
function lvFmt(v, kind){
  if(v===null||v===undefined||v==='') return '—';
  if(kind==='text') return esc(String(v));
  if(kind==='money'){
    const x=Number(v);
    if(!isFinite(x)) return '—';
    // 🔴 **只转发**给 common.js 的 `yiv`（折算规则一份）；这一页的
    //   候选池是拿来横向扫的，所以万位不留小数、单位前带个空格。
    return yiv(x, {wan: 0, sp: ' '});
  }
  if(kind==='pct'){ const x=Number(v); return isFinite(x)?(x*100).toFixed(2)+'%':'—'; }
  const x=Number(v);
  return isFinite(x) ? (Math.abs(x)>=100?x.toFixed(0):x.toFixed(3)) : esc(String(v));
}

/* 待办里买/卖行后面的小标记。有理由才出现 —— 没有的时候不该有个点不开的 ?
   （同 backLink 那条：给一个点了没反应的按钮比不给更糟）。 */
function lvWhyMark(x){
  const w=(x||{}).why;
  return w ? ` <span class="lvq" title="${esc(w)}">?</span>` : '';
}

/* 持仓行后面的「出处」：这只票是哪一期买进来的 —— 点进选股理由页的那一期。
   ★ 对不上任何一期的（手工买的）不给标记，硬凑一个出处比没有更糟。 */
function lvHoldMark(aid, code){
  const d=((LVWHYH||{}).entry||{})[code];
  if(!d) return '';
  return ` <a class="lvq" href="#/live/${esc(aid)}/why?d=${esc(d)}"
    title="建仓于 ${esc(d)} 那一期 —— 点开看当时为什么选它">?</a>`;
}

/* 账户页打开时取一次（只要清单，不要内容）。 */
async function lvWhyLoad(aid){
  try{ LVWHYH = await j('/api/live/explains?id='+encodeURIComponent(aid)); }
  catch(e){ LVWHYH = null; }
}

/* ---------------- 独立页 ---------------- */
async function showWhy(aid, off, focus, all){
  stopPoll();
  enterView();
  if(!LV){ try{ LV=await j('/api/live/accounts'); }catch(e){} }
  const M=$('#main');
  M.innerHTML='<div class="none">读取中…</div>';
  let o;
  try{ o=await j('/api/live/explains?id='+encodeURIComponent(aid)
      +'&full=1&offset='+(off||0)+'&limit='+LVWHYP
      +'&only='+(all?'all':'rebal')); }
  catch(e){ M.innerHTML='<div class="none">'+esc(e)+'</div>'; return; }
  const n=o.total, pages=Math.max(1, Math.ceil(n/(o.limit||LVWHYP)));
  const cur=Math.floor((o.offset||0)/(o.limit||LVWHYP))+1;
  const acc=((LV||{}).accounts||[]).find(x=>x.id===aid)||{};
  M.innerHTML=`
  <div class="lvhead">
    <h2>选股理由</h2>
    <a class="lvtag" href="#/live/${esc(aid)}">‹ 回账户</a>
    <span class="lvtag">${esc(acc.name||aid)}</span>
    <span class="lvtag">${all?'共 '+n+' 期':'调仓日 '+n+' 期'}</span>
    <a class="btn" id="wall" href="#">${all?'只看调仓日':'含非调仓日（共 '+(o.total_all||n)+' 期）'}</a>
    <span class="lvwhy">一期一段，按候选池排名依次列出 —— 选中 / 备选 / 没轮到 / 被剔除</span>
  </div>
  ${(o.periods||[]).map(p=>lvWhySec(aid, p)).join('')||'<div class="none">还没有信号</div>'}
  <div class="lvhead" style="margin-top:10px">
    <button class="btn" id="wprev" ${cur<=1?'disabled':''}>‹ 更近</button>
    <span class="lvtag">第 ${cur} / ${pages} 页</span>
    <button class="btn" id="wnext" ${cur>=pages?'disabled':''}>更早 ›</button>
  </div>`;
  /* 🔴 innerHTML 之后才存在的元素必须重新绑 —— 只在渲染开头绑的话点了没反应，
     而且没有任何报错（对比页表格里的「移除」栽过）。 */
  const q0=(all?'all=1':'');
  const go=k=>{ const ps=[]; if(k) ps.push('off='+k); if(all) ps.push('all=1');
                location.hash='#/live/'+aid+'/why'+(ps.length?'?'+ps.join('&'):''); };
  if($('#wall')) $('#wall').onclick=ev=>{ ev.preventDefault();
    location.hash='#/live/'+aid+'/why'+(all?'':'?all=1'); };
  if($('#wprev')) $('#wprev').onclick=()=>go(Math.max(0,(o.offset||0)-LVWHYP));
  if($('#wnext')) $('#wnext').onclick=()=>go((o.offset||0)+LVWHYP);
  M.querySelectorAll('button.whyrc').forEach(b=>{
    b.onclick=()=>lvWhyRecompute(aid, b.dataset.d, b);
  });
  /* 🔴 有后台复算在跑就自己回来看 —— 靠人手动刷新的话，页面会一直停在
     "正在复算…"，**而它不报错**。用 hash 当护栏：人已经走开就别再渲染
     （`stopPoll` 管的是 setInterval，这里是 setTimeout）。 */
  if((o.periods||[]).some(p=>p.explain_pending)){
    const h=location.hash;
    setTimeout(()=>{ if(location.hash===h) showWhy(aid, off, focus, all); }, 2500);
  }
  lvWhySqlBind(M);
  lvWhyMoreBind(M, aid, o);
  if(focus){
    const el=document.getElementById('why-'+focus);
    if(el){ el.scrollIntoView({block:'start'}); el.classList.add('whyhit'); }
  }
}

/* 一期一段；一期里**一条查询一块表** */
function lvWhySec(aid, p){
  const e=p.explain||{}, groups=e.groups||[];
  const head=`<div class="lvhead whyhd" id="why-${esc(p.date)}">
    <h3>${esc(p.date)}</h3>
    <span class="lvtag${p.is_rebalance?' on':''}">${p.is_rebalance?'调仓日':'非调仓日'}</span>
    <span class="lvtag">数据 ${esc(p.data_asof||'')} 收盘</span>
    <span class="lvtag">版本 ${esc(p.code_sha||'')}</span>
    <span class="lvtag">买 ${p.n_buy} / 卖 ${p.n_sell} / 持有 ${p.n_hold}</span>
    ${e.n_groups?`<span class="lvtag">${e.n_groups} 组查询 · 共 ${e.codes_total} 只</span>`:''}
    ${(p.recomputed||p.explain_from==='recomputed')
      ?`<span class="lvtag ${p.verified==='same'?'':'bad'}">事后复算 · ${
        p.verified==='same'?'与当时逐个相同':'🔴 与当时不同'}</span>`:''}
  </div>`;
  if(!groups.length){
    /* 三种"没有池子"要分清楚：非调仓日本来就不选股 / 老信号没留理由 / 真的空 */
    /* 四种"没有池子"要分清楚：非调仓日本来就不选股 / 正在后台复算 /
       复算失败 / 真的空。**"正在算"必须说出来** —— 不说的话页面上就是
       一句"没有理由"，人会当成功能坏了（实测反馈：「我直接看不到上一期选股」）。 */
    if(p.explain_pending){
      return `<div class="lvsec whysec">${head}
        <div class="lvwhy whypend">⏳ 这一期早于功能上线（2026-09-05），
          正在照当时的数据日、版本与参数<b>事后复算</b>（约几秒，算完自动出现，
          并与当时那份信号对账）…</div></div>`;
    }
    const why = !p.is_rebalance
      ? '非调仓日，本期不选股。'
      : p.explain_err ? '事后复算失败：'+esc(p.explain_err)
      : (p.has_explain ? '这一期没有选出任何候选。'
         : '这一期没有留下选股理由 —— 它早于这个功能上线（2026-09-05）。');
    return `<div class="lvsec whysec">${head}
      <div class="lvwhy">${why}
      ${(!p.has_explain && p.is_rebalance)
        ?`<button class="btn whyrc" data-d="${esc(p.date)}">再算一次</button>
          <span class="lvwhy">照当时的数据日、版本与参数重跑，并与当时那份信号对账</span>`:''}
      </div></div>`;
  }
  const pars=Object.entries(p.params||e.params||{})
    .map(([k,v])=>`<span class="lvkv">${esc(k)}=${esc(String(v))}</span>`).join('');
  const sets=Object.entries(e.g_sets||{})
    .map(([k,v])=>`<div class="lvwhy"><b>${esc((e.set_labels||{})[k]||k)}</b>（${v.length}）：${
      v.slice(0,25).map(c=>skLink(c,c)).join('、')}${v.length>25?' …':''}</div>`).join('');
  const notes=(e.notes||[]).map(x=>`<div class="lvwarn">${esc(x)}</div>`).join('');
  const other=(e.steps||[]).filter(st=>st.kind!=='query'&&st.kind!=='codes_at')
    .map(st=>`<div class="lvstep"><b>${st.i}. ${esc(st.kind)}</b>
      <span class="lvtag">${esc((st.n_in!=null?st.n_in+' → ':'')+(st.n_out!=null?st.n_out:''))}</span>
      <span class="lvwhy">${esc(st.where||'')}</span>
      <div class="lvpar">${Object.entries(st.args||{})
        .map(([k,v])=>`<span class="lvkv">${esc(k)}=${esc(String(v).slice(0,42))}</span>`).join('')}</div>
      ${st.dropped?`<div class="lvwhy">剔除：${Object.entries(st.dropped)
        .map(([c,w])=>esc(c)+'（'+esc(w)+'）').join('、')}</div>`:''}</div>`).join('');
  return `<div class="lvsec whysec">${head}${notes}
    ${groups.map(g=>lvWhyGroup(p, e, g)).join('')}
    ${other?'<h4>之后还做了什么（对候选池的过滤）</h4>'+other:''}
    ${sets?'<h4>策略状态里的代码集合</h4>'+sets:''}
    ${pars?'<h4>策略参数</h4><div class="lvpar" style="justify-content:flex-start">'
        +pars+'</div>':''}
  </div>`;
}

/* 一条查询 = 一块：条件 + 按排名列出的表
   🔴 **不能把多条查询并成一张表。** 红利是「低波(A) + 价值(B)」两袖并集：
     两条腿是两套规则、两套指标、两套排名。合并后实测三处都坏了 ——
     B 袖 6 只被去重并进 A 袖（B 那一组 0 行）、B 选中的票显示成 A 的
     第 233/332 名、两袖指标列不同导致**整列空白**（市值/净资产收益率全是空的）。 */
function lvWhyGroup(p, e, g){
  const meta=g.metric_meta||[], all=g.rows||[];
  const gid=p.date+'-'+g.step, opened=!!LVWHYALL[gid];
  /* 默认只到第 20 名；"一定要显示"的（选中/持有/卖出/在策略集合里/被剔除）
     无论排第几都保留 —— 判据与服务端落盘时那条一致。 */
  const must=r=>(['buy','hold','sell'].indexOf(r.status)>=0
                 || (r.in_sets||[]).length || r.dropped_by);
  const rows = opened ? all
    : all.filter(r=>r.rank<=LVWHYN || must(r));
  const hidden = all.length - rows.length;
  const cols=meta.map(m=>`<th class="rt" title="${esc(m.label)}">${esc(m.label)}</th>`).join('');
  /* 🔴 名次跳号要**明说**。默认只到第 20 名，但排在后面的"一定要显示"的行
     （比如被另一条腿选中的票，在这一组里排第 325）照样列出来 ——
     中间那一大段直接消失的话，看着像数据缺了一块。 */
  let prev=0;
  const body=rows.map(r=>{
    const gap=(r.rank>prev+1) ? `<tr class="whygap"><td colspan="${4+meta.length}"
      class="tx lvwhy">… 第 ${prev+1}–${r.rank-1} 名省略（都没轮到）</td></tr>` : '';
    prev=r.rank;
    const st=LVST[r.status]||[r.status||'', ''];
    const sets=(r.in_sets||[]).map(k=>`<span class="lvkv">${
      esc((e.set_labels||{})[k]||k)}</span>`).join('')
      + (r.also_in||[]).map(k=>`<span class="lvkv" title="同一只票也出现在第 ${k} 组">
          也在第 ${k} 组</span>`).join('');
    const cells=meta.map(m=>{
      if(m.kind==='rank'){
        const a=(r.metrics||{})[m.key];
        const b=(m.of||[]).map(k=>(r.metrics||{})[k]).find(v=>v!=null);
        return `<td class="rt">${a==null?'—':esc(a)+(b==null?'':
          ' <span class="lvwhy">/ '+esc(b)+'</span>')}</td>`;
      }
      return `<td class="${m.kind==='text'?'tx':'rt'}">${lvFmt((r.metrics||{})[m.key], m.kind)}</td>`;
    }).join('');
    const note=(r.dropped_by||{}).why || (r.facts||[]).join('、') || '';
    return gap+`<tr class="${st[1]}">
      <td class="rt">${r.rank}</td>
      <td class="tx">${cnCell(r.code, r.name)}</td>
      <td class="tx"><span class="lvbadge ${st[1]}">${esc(st[0])}</span>${sets}
        ${note?`<span class="lvwhy" title="${esc(note)}">${esc(note)}</span>`:''}</td>
      ${cells}</tr>`;
  }).join('');
  /* 条件：参数 chip（中文标签由服务端给）+ 可展开的**完整 SQL**。
     🔴 光给几个参数值是不够的 —— 条件长在 SQL 的 WHERE/排序/截断里
     （`dy > 0.03`、`rk <= floor(0.1*n)`、`ORDER BY beta ASC`），
     而那些没有别处可抄：另写一份"条件说明"就是第二份口径，
     策略改了它不会跟着变。SQL 是**代入参数之后**的，就是当时真的跑的那条。 */
  const args=Object.entries(g.args||{}).map(([k,v])=>
    `<span class="lvkv" title="${esc(k)}">${esc((g.arg_labels||{})[k]||k)}=${
      esc(String(v).length>60?String(v).slice(0,60)+'…':String(v))}</span>`).join('');
  const sid='sql-'+esc(p.date)+'-'+g.step;
  return `<div class="whygrp">
    <div class="lvhead whyghd">
      <b>第 ${g.step} 组${g.sql_head?'｜'+esc(g.sql_head.replace(/^[-\s★]+/,'')):''}</b>
      <span class="lvtag">返回 ${g.n_rows} 只${
        g.shown<g.n_rows?'（存了前 '+g.shown+' 只）':''}</span>
      ${hidden>0?`<a href="#" class="lvwhy whymore" data-g="${esc(gid)}"
        >显示到第 ${LVWHYN} 名以后（还有 ${hidden} 只）▸</a>`
        :(opened&&all.length>LVWHYN?`<a href="#" class="lvwhy whymore" data-g="${esc(gid)}"
        >只看前 ${LVWHYN} 名 ▾</a>`:'')}
      <span class="lvwhy">${esc(g.where||'')}</span>
      <span style="flex:1"></span>
      ${g.sql?`<a href="#" class="lvwhy sqltog" data-t="${sid}">条件（完整 SQL）▸</a>`:''}
    </div>
    <div class="lvpar whyargs">${args}</div>
    ${g.sql?`<pre class="stcode" id="${sid}" style="display:none">${esc(g.sql)}</pre>`:''}
    <div class="pw"><table class="lvt lvpoolt">
      <tr><th class="rt">#</th><th class="tx">名称</th>
          <th class="tx">状态</th>${cols}</tr>${body}</table></div>
  </div>`;
}

/* 「显示更多 / 只看前 N 名」。★ 只重画那一块，不整页刷新 —— 别的期可能已经
   复算过了，刷掉就白算（同复算那条）。 */
function lvWhyMoreBind(root, aid, o){
  (root||document).querySelectorAll('a.whymore').forEach(a=>{
    a.onclick=ev=>{ ev.preventDefault();   /* <a href="#"> 当按钮用必须拦 */
      const gid=a.dataset.g;
      LVWHYALL[gid]=!LVWHYALL[gid];
      const d=gid.slice(0, gid.lastIndexOf('-'));
      const p=((o||{}).periods||[]).find(x=>x.date===d);
      const sec=document.getElementById('why-'+d);
      if(!p||!sec) return;
      const wrap=sec.closest('.whysec');
      wrap.outerHTML=lvWhySec(aid, p);
      lvWhySqlBind(document); lvWhyMoreBind(document, aid, o);
      const nb=document.querySelector('.whysec button.whyrc[data-d="'+d+'"]');
      if(nb) nb.onclick=()=>lvWhyRecompute(aid, d, nb);
    };
  });
}

/* SQL 折叠。★ `display:none` 写在内联 style 上，所以这里 'block'/'none' 都行；
   若哪天挪进样式表，就**只能**写 'block' —— `''` 只是删内联样式，
   样式表规则照旧生效（表现是"点了没反应"且不报错）。 */
function lvWhySqlBind(root){
  (root||document).querySelectorAll('a.sqltog').forEach(a=>{
    a.onclick=ev=>{ ev.preventDefault();      /* <a href="#"> 当按钮用必须拦 */
      const el=document.getElementById(a.dataset.t);
      if(!el) return;
      const on=el.style.display==='none';
      el.style.display=on?'block':'none';
      a.textContent=on?'条件（完整 SQL）▾':'条件（完整 SQL）▸';
    };
  });
}

async function lvWhyRecompute(aid, date, btn){
  btn.disabled=true;
  btn.textContent='复算中…（重放 30 天 warmup，约 1~3 秒）';
  let o;
  try{ o=await j('/api/live/explain?id='+encodeURIComponent(aid)
      +'&date='+encodeURIComponent(date)+'&recompute=1'); }
  catch(e){ btn.disabled=false; btn.textContent='复算失败，再试一次';
            alert(String(e)); return; }
  /* 原地替换那一段：不整页刷新 —— 别的期可能已经复算过了，刷掉就白算 */
  const sec=document.getElementById('why-'+date);
  if(!sec) return;
  const wrap=sec.closest('.whysec');
  const p=Object.assign({}, o, {date:o.date, n_buy:(o.buy||[]).length,
    n_sell:(o.sell||[]).length, n_hold:(o.hold||[]).length,
    has_explain:true, is_rebalance:o.is_rebalance});
  wrap.outerHTML=lvWhySec(aid, p);
  const nb=document.querySelector('.whysec button.whyrc[data-d="'+date+'"]');
  if(nb) nb.onclick=()=>lvWhyRecompute(aid, date, nb);
  lvWhySqlBind(document);   /* 🔴 替换后的那一段是新元素，事件要重新绑 */
  lvWhyMoreBind(document, aid, {periods:[p]});
}
