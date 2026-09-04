/* ============ 回测归档：目录 / 详情 / 版本 / 选中的规则 ============ */
/* ============ 目录页 ============ */
const OPEN=new Set();          // 展开状态：'g:小市值' / 's:小市值/v0b'
let DFP=null;
async function loadRuns(){
  RUNS=await j('/api/runs');
  try{ DFP=await j('/api/datafp'); }catch(e){ DFP=null; }
  route();
}
// 数据版本横幅：归档是自包含的历史，但它的【数字】依赖当时的数据。
// 面板/std 重建后旧归档就不可比了，而报告本身看着完全正常 —— 必须显式提示。
function fpBar(){
  if(!DFP || !DFP.n_stale) return '';
  const tot=DFP.n_stale+DFP.n_current;
  return `<div id="fpbar">⚠ <b>${DFP.n_stale}/${tot}</b> 条归档的数据版本与当前不一致
    —— 它们的收益/回撤是<b>旧数据</b>算出来的，<b>不可与新回测直接对照</b>。
    <br>当前数据指纹 <span class="mono">${esc((DFP.current||'').slice(0,12))}</span>
    ${Object.entries(DFP.parts||{}).map(([k,v])=>
      `· ${k} <span class="mono">${esc((v.hash||'').slice(0,8))}</span>`).join(' ')}
    <br><span class="cmt">归档只标记不删除 —— 删了就没法复盘。要重新对照请按当前数据重跑。</span></div>`;
}

function showCatalog(){
  stopPoll();
  $('#vp').innerHTML=''; $('#vp').style.display='none';
  $('#main').innerHTML=''; $('#main').style.display='none';
  $('#cat').style.display='';
  renderCatalog();
}
// 同一语义版本下可能有多份字节版本（差异仅注释/排版）。
// 取【最近跑过】的那份作为代表 —— 它最接近磁盘上的当前文件。
function newest(rs){
  return rs.slice().sort((x,y)=>(x.ran_at||'')<(y.ran_at||'')?1:-1)[0];
}
function agg(rs){                // 一组回测的汇总
  const a=rs.map(r=>r.annual_return).filter(v=>v!=null);
  return {n:rs.length, best:a.length?Math.max(...a):null,
          worst:a.length?Math.min(...a):null,
          latest:rs.slice().sort((x,y)=>(x.ran_at||'')<(y.ran_at||'')?1:-1)[0]};
}
const RUNCOLS=[
  ['mark','★'],['run_id','run_id',1],['start','区间',1],['cash','初始资金'],['params','参数',1],
  ['annual_return','年化'],['max_drawdown','回撤'],['excess_annual','超额年化'],
  ['info_ratio','信息比率'],['sharpe','夏普'],['turnover_per_year','年换手'],
  ['n_trades','交易'],['win_rate','胜率'],['ran_at','跑于',1]];
function runTable(rs){
  // 按【回测运行时间】倒序 —— 最近跑的在最上面
  rs=rs.slice().sort((x,y)=>(x.ran_at||'')<(y.ran_at||'')?1:-1);
  return `<div class="runs"><table><thead><tr>${RUNCOLS.map(([,t,l])=>
      `<th class="${l?'l':''}">${t}</th>`).join('')}</tr></thead><tbody>
    ${rs.map(r=>`<tr data-id="${r.run_id}" class="${r.mark?'marked':''}">
      <td><span class="st ${r.mark?'on':''}" data-mk="${r.run_id}"
        title="${r.mark?('已选中'+(r.mark_note?'：'+esc(r.mark_note):'')+'（点击取消）'):'标记为选中的规则'}"
        >${r.mark?'★':'☆'}</span></td>
      <td class="l mono">${r.run_id}${r.stale?`<span class="stale" title="归档时的数据与当前不一致，变化部件：${esc(r.stale_parts)}">数据已变</span>`:''}</td>
      <td class="l">${r.start} ~ ${r.end}</td>
      <td>${(+r.cash).toLocaleString()}</td>
      <td class="l">${Object.entries(r.params||{}).map(([k,v])=>
        `<span class="pill">${k}=${v}</span>`).join('')||'—'}</td>
      <td class="${sign(r.annual_return)}"><b>${pct(r.annual_return,2)}</b></td>
      <td class="neg">${pct(r.max_drawdown,2)}</td>
      <td class="${sign(r.excess_annual)}">${pct(r.excess_annual,2)}</td>
      <td>${fmtN(r.info_ratio)}</td><td>${fmtN(r.sharpe)}</td>
      <td>${fmtN(r.turnover_per_year,1)}</td>
      <td>${r.n_trades??'—'}</td><td>${pct(r.win_rate,1)}</td>
      <td class="l">${(r.ran_at||'').replace('T',' ').slice(5,16)}</td></tr>`).join('')}
    </tbody></table></div>`;
}
/* 建任意深度的目录树：
     文件夹（可嵌套文件夹）→ 策略文件 → 【代码版本】→ 回测
   ★ 策略身份 = 文件内容哈希：同一路径改一个字符就是另一个策略，
     所以版本是独立的一层。只有一个版本时跳过这层，免得多点一次。 */
function buildTree(rs){
  const root={dirs:{}, files:{}};
  rs.forEach(r=>{
    let parts=(r.strategy_path||'').split('/').filter(Boolean);
    if(parts[0]==='strategies'){
      parts=parts.slice(1);
    }else if(parts.length>1){
      // ★ 仓库外的路径【压成一层】。诊断用的一次性脚本常放在 scratchpad，
      //   strategy_path 会是逃逸出仓库的相对路径，例如
      //   ../../../../private/tmp/claude-501/<...>/scratchpad/froec_oldsql.py
      //   —— 8 段路径会铺出 8 层、每层只有一个子节点的空壳，把目录树撑坏。
      //   完整路径仍挂在文件节点的 title 上，可审计。
      parts=['_仓库外', parts[parts.length-1]];
    }
    if(parts.length<1||!parts[0]) parts=['_未知路径', r.strategy+'.py'];
    const file=parts.pop();
    let node=root;
    parts.forEach(d=>{ node.dirs[d]=node.dirs[d]||{dirs:{},files:{}}; node=node.dirs[d]; });
    (node.files[file]=node.files[file]||[]).push(r);
  });
  return root;
}
function countTree(node){
  // ★ marked 要向上冒泡：折叠状态下也得看得见这个目录里有没有选中的规则，
  //   否则「打标记方便找」这个目的就落空了 —— 还得逐层展开才知道。
  let files=0,runs=0,strats=0,best=null,marked=0;
  Object.values(node.dirs).forEach(d=>{const c=countTree(d);
    files+=c.files; runs+=c.runs; strats+=c.strats; marked+=c.marked;
    if(c.best!=null&&(best==null||c.best>best)) best=c.best;});
  Object.values(node.files).forEach(rs=>{files++; runs+=rs.length;
    strats+=new Set(rs.map(r=>r.code_sha)).size;
    rs.forEach(r=>{if(r.mark) marked++;
      if(r.annual_return!=null&&(best==null||r.annual_return>best)) best=r.annual_return;});});
  return {files,runs,strats,best,marked};
}
function stcnt(n){return n?`<span class="stcnt" title="含 ${n} 条选中的规则">★${n>1?'×'+n:''}</span>`:'';}
function renderNode(node,depth,keyPrefix,auto){
  let h='';
  // 文件夹（可嵌套）
  Object.keys(node.dirs).sort().forEach(d=>{
    const k=keyPrefix+'/'+d, op=auto||OPEN.has(k), c=countTree(node.dirs[d]);
    h+=`<div class="nd d${depth} ${op?'op':''}" data-k="${k}"
          style="padding-left:${14+depth*20}px">
        <span class="ar">▶</span><span class="ic">📁</span>
        <span class="nm">${d}</span>${stcnt(c.marked)}
        <span class="sub">${c.files} 个文件 · ${c.strats} 个策略 · ${c.runs} 次回测</span>
        <span class="sp"></span>
        <span class="sub">最佳年化 <b class="${sign(c.best)}">${pct(c.best,1)}</b></span></div>
      <div class="body ${op?'op':''}">${renderNode(node.dirs[d],depth+1,k,auto)}</div>`;
  });
  // 策略文件
  Object.keys(node.files).sort().forEach(f=>{
    const rs=node.files[f], k=keyPrefix+'/'+f, op=auto||OPEN.has(k);
    // ★ 版本层按【语义哈希】分组：改注释/排版/简介不算新版本。
    //   字节哈希仍在每条回测里保留（审计追溯：归档里躺的是哪份字节）。
    const byv={}; rs.forEach(r=>{const k=r.sem_sha||r.code_sha;(byv[k]=byv[k]||[]).push(r);});
    const vs=Object.keys(byv).sort((a,b)=>
      (agg(byv[b]).latest.ran_at||'')<(agg(byv[a]).latest.ran_at||'')?-1:1);
    const a=agg(rs);
    h+=`<div class="nd d${depth} ${op?'op':''}" data-k="${k}"
          style="padding-left:${14+depth*20}px">
        <span class="ar">▶</span><span class="ic">📄</span>
        <span class="nm" title="${esc(rs[0].strategy_path||'')}">${f}</span>${stcnt(rs.filter(r=>r.mark).length)}
        <span class="sub">${vs.length} 个版本 · ${rs.length} 次回测</span>
        ${vs.length===1?`<span class="vnote">${esc(byv[vs[0]][0].note||'（未填简介）')}${
            byv[vs[0]][0].note_src==='docstring'?'<span class="cmt" style="margin-left:6px">取自 docstring</span>':''}</span>
          <span class="vopen" data-ver="${newest(byv[vs[0]]).code_sha256}">代码 / 回测 →</span>`:''}
        <span class="sp"></span>
        <span class="sub">年化 <b class="${sign(a.best)}">${pct(a.best,1)}</b>
          ~ <b class="${sign(a.worst)}">${pct(a.worst,1)}</b>
          · 最近 ${(a.latest.ran_at||'').replace('T',' ').slice(5,16)}</span></div>
      <div class="body ${op?'op':''}">`;
    if(vs.length===1){
      // 只有一个版本 -> 直接列回测，不多套一层
      h+=runTable(rs);
    }else{
      vs.forEach(v=>{
        const vk=k+'@'+v, vop=auto||OPEN.has(vk), va=agg(byv[v]);
        h+=`<div class="nd d${depth+1} ${vop?'op':''}" data-k="${vk}"
              style="padding-left:${14+(depth+1)*20}px">
            <span class="ar">▶</span><span class="ic">⌗</span>
            <span class="nm sha">${v}</span>${stcnt(byv[v].filter(r=>r.mark).length)}
            ${(()=>{const nb=new Set(byv[v].map(r=>r.code_sha)).size;
                return nb>1?`<span class="pill" title="行为相同，仅注释/排版/简介不同">${nb} 份字节版本</span>`:'';})()}
            <span class="vnote">${esc(byv[v][0].note||'（未填简介）')}${
              byv[v][0].note_src==='docstring'?'<span class="cmt" style="margin-left:6px">取自 docstring</span>':''}</span>
            <span class="sp"></span>
            <span class="vopen" data-ver="${newest(byv[v]).code_sha256}"
              >代码 / 回测 →</span></div>
          <div class="body ${vop?'op':''}">${runTable(byv[v])}</div>`;
      });
    }
    h+='</div>';
  });
  return h;
}
function renderCatalog(){
  /* 过滤框只在回测归档视图里存在（顶栏随 hash 变）—— 没有它时当空过滤。 */
  const fe=$('#filter');
  const q=(fe?fe.value:'').trim().toLowerCase();
  const rs=RUNS.filter(r=>!q||((r.strategy_path||'')+' '+r.strategy+' '+r.run_id+' '+r.code_sha+' '+
      Object.entries(r.params||{}).map(([k,v])=>k+'='+v).join(' ')).toLowerCase().includes(q));
  if(!rs.length){$('#cat').innerHTML='<div class="note" style="padding:40px 0;text-align:center">'+
    (RUNS.length?'没有匹配的回测':'还没有归档 —— 先跑一次 <code>python3 run.py ...</code>')+'</div>';return;}
  const auto=!!q;            // 过滤时自动展开，否则默认全收起
  $('#cat').innerHTML=fpBar()+pickBar()+'<div class="tree">'+renderNode(buildTree(rs),0,'',auto)+'</div>'+
    `<div class="note">策略身份 = <b>文件内容哈希</b>：同一路径改一个字符就是另一个策略，
      所以版本（⌗）是独立的一层；只有一个版本时会跳过这层。
      回测按<b>运行时间</b>倒序。目录可任意嵌套。</div>`;
  document.querySelectorAll('.vopen[data-ver]').forEach(e=>e.onclick=ev=>{
    ev.stopPropagation();          // 否则会连带切换所在节点的展开状态
    location.hash='#/ver/'+e.dataset.ver;});
  document.querySelectorAll('.nd[data-k]').forEach(e=>e.onclick=ev=>{
    ev.stopPropagation();
    const body=e.nextElementSibling; if(!body||!body.classList.contains('body')) return;
    const now=!e.classList.contains('op');
    e.classList.toggle('op',now); body.classList.toggle('op',now);
    now?OPEN.add(e.dataset.k):OPEN.delete(e.dataset.k);});
  document.querySelectorAll('.runs tbody tr').forEach(e=>
    e.onclick=ev=>{ev.stopPropagation();
      location.hash='#/run/'+encodeURIComponent(e.dataset.id);});
  wireStars();
}

/* ============ 独立索引页：只看标星的规则 ============ */
/* 排序状态。默认按年化降序 —— 打开这一页就是为了"哪条更好"。
   ★ 不持久化：这一页是拿来比的，下次打开该从同一个起点看，
     而不是"上次我按换手排过"。 */
let PKSORT = {k:'annual_return', desc:true};

/* 目录页顶部那条 #pick 只列关键几列（保留，便于在树里就地看到）；
   这里是完整视图：按分组/策略归拢，逐条给出参数、区间、本金、成本口径、
   全部指标、备注与数据新鲜度。
   ★ 成本口径必须显式列出：本项目两次因为「拿滑点 0 的数字去比含滑点的基准」
     得出错误结论（FROEC 与 v0b 各一次）。并列展示时口径不同必须看得见，
     否则跨行比较又是苹果比橘子。 */
function showPicks(){
  stopPoll();
  enterView();
  const ms=RUNS.filter(r=>r.mark);
  if(!ms.length){
    $('#main').innerHTML=`<div id="pk"><h2>★ 选中的规则</h2>
      <div class="none">还没有标星的回测 —— 回目录页点任意回测行最左边的 ☆ 即可。</div></div>`;
    return;
  }
  // 分组 -> 策略
  const by={};
  ms.forEach(r=>{ const g=r.group||'（无分组）';
    (by[g]=by[g]||{})[r.strategy]=((by[g]||{})[r.strategy]||[]).concat([r]); });
  const costTxt=r=>{
    const sl=r.slippage==null?'?':(r.slippage===0?'0':(r.slippage*1e4).toFixed(1)+'‱');
    const cm=r.commission==null?'?':(r.commission*1e4).toFixed(2)+'‱';
    const tx=r.close_tax==='auto'?'印花税分段':('印花税 '+((+r.close_tax||0)*1e4).toFixed(1)+'‱');
    return `滑点 ${sl} · 佣金 ${cm} · ${tx}`;
  };
  const nDiff=new Set(ms.map(r=>String(r.slippage))).size;
  /* 列定义只写一处：表头、排序、单元格都读它。
     ★ 加一列只改这个数组 —— 分三处写迟早对不上（表头 8 列、数据 9 个的那种）。 */
  const COLS=[
    {k:'annual_return', t:'年化',   f:r=>pct(r.annual_return,2), c:r=>sign(r.annual_return)},
    {k:'max_drawdown',  t:'回撤',   f:r=>pct(r.max_drawdown,2),  c:()=>'neg',
     asc:true},   /* 回撤越小越好 -> 默认升序 */
    {k:'sharpe',        t:'夏普',   f:r=>fmtN(r.sharpe)},
    {k:'excess_annual', t:'超额年化', f:r=>pct(r.excess_annual,2), c:r=>sign(r.excess_annual)},
    {k:'info_ratio',    t:'信息比率', f:r=>fmtN(r.info_ratio)},
    {k:'turnover_per_year', t:'年换手', f:r=>fmtN(r.turnover_per_year,1)},
    {k:'n_trades',      t:'交易',   f:r=>r.n_trades??'—'},
    {k:'win_rate',      t:'胜率',   f:r=>pct(r.win_rate,1)},
  ];
  const NCOL=2+COLS.length;
  /* 成本口径那一【列】按要求去掉了（太挤），但保护不能丢：本项目两次因为
     "拿滑点 0 的数字去比含滑点的基准"得出错误结论（FROEC 与 v0b 各一次）。
     改成只给【与多数行不同】的那几行挂一个「口径不同」标记 + 顶部那条总提示；
     完整口径进行的 tooltip。少一列噪声，但异常仍然看得见。 */
  const slipCnt={};
  ms.forEach(r=>{ const k=String(r.slippage); slipCnt[k]=(slipCnt[k]||0)+1; });
  const major=Object.keys(slipCnt).sort((a,b)=>slipCnt[b]-slipCnt[a])[0];
  const odd=r=>Object.keys(slipCnt).length>1&&String(r.slippage)!==major;
  const sk=PKSORT.k, sd=PKSORT.desc;
  const cmp=(a,b)=>{
    if(sk==='name') return String(a._st).localeCompare(String(b._st))*(sd?-1:1);
    const x=a[sk], y=b[sk];
    if(x==null&&y==null) return 0;
    if(x==null) return 1;            /* 缺值永远排最后，不管升降序 */
    if(y==null) return -1;
    return (x<y?-1:x>y?1:0)*(sd?-1:1);
  };
  const arrow=k=>k===sk?`<span class="ar">${sd?'▼':'▲'}</span>`:'<span class="ar">↕</span>';
  const th=(k,t,cls)=>`<th class="${cls||''}${k===sk?' on':''}" data-sk="${k}"
      title="点击按此列排序">${t} ${arrow(k)}</th>`;
  $('#main').innerHTML=`<div id="pk">
    <h2>★ 选中的规则（${ms.length}）</h2>
    <div class="note">★ 标在【单次回测】上 —— run 记录了策略 + 参数 + 区间 + 成本口径
      + 数据指纹，才是完整的一条规则；同一策略换个参数就是另一条规则。
      点<b>行</b>进入该次回测详情；点<b>表头</b>排序；本金与成本口径在<b>行的 tooltip</b> 里。${nDiff>1?
      ' <b style="color:var(--warn)">⚠ 这些规则的成本口径不一致 —— 与多数行不同的那几行标了「口径不同」，'
      +'跨行比年化前先看清（本项目两次栽在拿滑点 0 的数字比含滑点的基准上）。</b>':''}</div>
    <div class="pw"><table class="pkt">
      <tr>${th('name','策略 · 参数','tx nmc')}<th class="tx">区间</th>
        ${COLS.map(c=>th(c.k,c.t)).join('')}</tr>
      ${Object.keys(by).sort().map(g=>{
        /* 组内展平再排序 —— 分组保留（业务域是有意义的归拢），
           排序只在组内做，跨组比较本来就不该发生（区间/基准都可能不同）。 */
        const rows=[];
        Object.keys(by[g]).sort().forEach(st=>by[g][st].forEach(r=>
          rows.push(Object.assign({_st:st}, r))));
        rows.sort(cmp);
        return `<tr class="grp"><td colspan="${NCOL}">
            <span class="gb">${esc(g)}</span>
            <span class="lvwhy">　${rows.length} 条</span></td></tr>
          ${rows.map(r=>`<tr class="rw" data-go="${esc(r.run_id)}"
            title="${esc(r.run_id)}　本金 ${(+r.cash).toLocaleString()}　${esc(costTxt(r))}">
            <td class="tx nmc"><span class="nm2">${esc(r._st)}</span>${
              odd(r)?`<span class="stale" title="这一行的成本口径与多数行不同：${esc(costTxt(r))}　跨行比年化前先看清">口径不同</span>`:''}
              ${r.stale?`<span class="stale" title="归档时的数据与当前不一致：${esc(r.stale_parts)}">数据已变</span>`:''}
              <div style="margin-top:2px">${Object.entries(r.params||{}).map(([k,v])=>
                `<span class="pill">${esc(k)}=${esc(String(v))}</span>`).join('')||
                '<span class="pill">默认参数</span>'}</div>
              ${r.mark_note?`<div class="why">${esc(r.mark_note)}</div>`:''}</td>
            <td class="tx cost">${esc(r.start)}<br>${esc(r.end)}</td>
            ${COLS.map(c=>`<td><b class="${c.c?c.c(r):''}">${c.f(r)}</b></td>`).join('')}
            </tr>`).join('')}`;}).join('')}
    </table></div>
  </div>`;
  document.querySelectorAll('#pk tr.rw[data-go]').forEach(e=>
    e.onclick=()=>{ location.hash='#/run/'+encodeURIComponent(e.dataset.go); });
  document.querySelectorAll('#pk th[data-sk]').forEach(e=>
    e.onclick=()=>{
      const k=e.dataset.sk;
      if(k===PKSORT.k){ PKSORT.desc=!PKSORT.desc; }
      else{
        /* 换列时的默认方向按【哪边更好】给：年化降序、回撤升序。
           一律降序的话点"回撤"会把最差的排最前面。 */
        const c=COLS.find(x=>x.k===k);
        PKSORT.k=k; PKSORT.desc=!(c&&c.asc)&&k!=='name';
      }
      showPicks();
    });
}

/* ============ 选中标记 ============ */
/* ★ 打在【单次回测】上：run 记录了策略 + 参数 + 区间 + 成本 + 数据指纹，
   才构成一条完整的「规则」。策略文件或代码版本都不够 —— 同一版本换个
   参数就是另一条规则（froec 的 kcb_688_only=0 与默认值就是两条）。 */
function pickBar(){
  const ms=RUNS.filter(r=>r.mark);
  if(!ms.length) return '';
  ms.sort((a,b)=>((a.group||'')+a.strategy).localeCompare((b.group||'')+b.strategy));
  return `<div id="pick"><h3>★ 选中的规则（${ms.length}）</h3><table>${ms.map(r=>
    `<tr data-go="${r.run_id}">
       <td style="white-space:nowrap"><b>${esc(r.strategy)}</b></td>
       <td>${Object.entries(r.params||{}).map(([k,v])=>
            `<span class="pill">${esc(k)}=${esc(String(v))}</span>`).join('')||
            '<span class="mn">默认参数</span>'}</td>
       <td style="white-space:nowrap">年化 <b class="${sign(r.annual_return)}"
            >${pct(r.annual_return,2)}</b></td>
       <td style="white-space:nowrap">回撤 <span class="neg">${pct(r.max_drawdown,2)}</span></td>
       <td style="white-space:nowrap">夏普 ${fmtN(r.sharpe)}</td>
       <td class="mn">${r.start} ~ ${r.end}</td>
       <td class="mn">${esc(r.mark_note||'')}</td>
       ${r.stale?'<td><span class="stale">数据已变</span></td>':'<td></td>'}
     </tr>`).join('')}</table>
    <div class="note" style="margin:6px 0 0">★ 记的是 run，含参数与成本口径；
      标 <b>数据已变</b> 的说明归档时的数据与当前面板不一致，数字不能直接引用。</div></div>`;
}
function wireStars(){
  document.querySelectorAll('.st[data-mk]').forEach(e=>e.onclick=async ev=>{
    ev.stopPropagation();                 // 否则会连带打开这次回测的详情页
    const id=e.dataset.mk, r=RUNS.find(x=>x.run_id===id);
    let body;
    if(r&&r.mark){ body={run_id:id, mark:''}; }        // 已选中 -> 取消
    else{
      const note=prompt('为什么选中它？（可留空，会显示在顶部「选中的规则」里）',
                        (r&&r.mark_note)||'');
      if(note===null) return;                          // 取消对话框 = 不改动
      body={run_id:id, mark:'star', note:note};
    }
    e.style.opacity='.4';
    try{
      const res=await fetch('/api/mark',{method:'POST',
        headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
      const o=await res.json();
      if(o.error){ alert('打标记失败：'+o.error); e.style.opacity=''; return; }
      await loadRuns0();                                // 重取，别只改本地状态
      if(location.hash==='#/runs') renderCatalog();   /* 归档目录的 hash 从 '' 变成了 #/runs */
    }catch(err){ alert('打标记失败：'+err); e.style.opacity=''; }
  });
  document.querySelectorAll('#pick tr[data-go]').forEach(e=>
    e.onclick=()=>{ location.hash='#/run/'+encodeURIComponent(e.dataset.go); });
}
/* 这两个控件是【回测归档目录专属】的，每次进那个视图时才创建 ——
   所以绑定也得在那时做（原来是页面加载时一次性绑，控件搬进 pageHead 的
   extra 之后就绑不到了）。 */
function wireCatalogControls(){
  const fe=$('#filter'), ee=$('#expand');
  if(fe) fe.oninput=()=>{ renderCatalog(); };
  if(ee) ee.onclick=()=>{
  const anyClosed=document.querySelector('.nd[data-k]:not(.op)');
  if(anyClosed) document.querySelectorAll('.nd[data-k]').forEach(e=>OPEN.add(e.dataset.k));
  else OPEN.clear();
  ee.textContent=anyClosed?'全部收起':'全部展开';
  renderCatalog();
  // 展开是逐层的：新露出来的节点这一轮才存在，需要再补一次
  if(anyClosed) for(let i=0;i<6;i++){
    const more=document.querySelectorAll('.nd[data-k]:not(.op)');
    if(!more.length) break;
    more.forEach(e=>OPEN.add(e.dataset.k)); renderCatalog();
  }
  };
}


/* ============ 版本页：源码 + 可填参数 + 触发回测 ============ */
/* 版本 = 代码哈希。同一版本下所有回测的代码完全相同，所以「看代码」「按参数
   再跑一次」都属于版本这一层，而不是单次回测那一层。 */
let VER=null, POLL=null;

async function openVersion(sha){
  stopPoll();
  $('#cat').style.display='none'; $('#main').innerHTML='';
  $('#main').style.display='none'; $('#vp').style.display='';
  $('#vp').innerHTML='<div class="note" style="padding:40px 0">加载中…</div>';
  let v;
  try{ v=await j('/api/version?sha='+encodeURIComponent(sha)); }
  catch(e){ $('#vp').innerHTML=`<div class="note" style="padding:40px 0">
      加载失败：${esc(e.message)}　<a href="#">返回目录</a></div>`; return; }
  VER=v;
  // ★ 版本页以【语义版本】为单位：同一行为下的所有回测都在这里，
  //   哪怕它们的字节版本不同（差异仅注释/排版/简介）。
  const runs=RUNS.filter(r=>(r.sem_sha256||r.code_sha256)===(v.sem_sha256||v.code_sha256));
  const bytes={};
  runs.forEach(r=>{(bytes[r.code_sha]=bytes[r.code_sha]||[]).push(r);});
  const ps=v.current_params||v.params||[];   // 按【会被执行的代码】列参数
  const last=runs.slice().sort((a,b)=>(a.ran_at||'')<(b.ran_at||'')?1:-1)[0]||{};

  $('#vp').innerHTML=`
    <div id="hd">
      <div class="row1"><h2>${esc(v.strategy||'')}</h2>
        <span class="meta">${esc(v.group||'')} · <span class="mono">${esc(v.strategy_path||'')}</span>
          · 版本 <span class="sha">${esc(v.sem_sha||v.code_sha)}</span>
          <span class="cmt">(行为哈希)</span> · ${runs.length} 次回测</span></div>
      <div class="row1"><span class="vnote">${esc(v.note||'（未填简介）')}${
          v.note_src==='docstring'?'<span class="cmt" style="margin-left:8px">—— 该版本没写 NOTE，这句取自 docstring；同一文件的多个旧版本会显示相同内容</span>':''}</span></div>
    </div>

    <div class="sec">
      <h3>回测参数</h3>
      ${v.not_runnable_why?`<div class="note bad" style="margin-bottom:10px">
        ⚠️ ${esc(v.not_runnable_why)}</div>`:''}
      <div class="pgrid pf" style="margin-bottom:12px">
        <div><label>起始日期</label><input id="f_start" value="${esc(last.start||'2016-01-01')}">
          <div class="cmt">YYYY-MM-DD</div></div>
        <div><label>结束日期</label><input id="f_end" value="${esc(last.end||'2026-06-30')}">
          <div class="cmt">YYYY-MM-DD</div></div>
        <div><label>初始资金</label><input id="f_cash" value="${last.cash!=null?last.cash:500000}">
          <div class="cmt">不填用引擎默认 50 万</div></div>
        <div><label>成本口径</label>
          <div style="padding:5px 0"><label style="display:inline;color:var(--fg)">
            <input type="checkbox" id="f_jq" style="width:auto" checked>
            聚宽口径 <span class="cmt" style="display:inline">滑点0/佣金万3/印花税千一</span></label></div></div>
      </div>
      <h3>策略参数（${ps.length} 个）${ps.length?'<span class="cmt" style="display:inline"> —— 留空 = 用该版本默认值</span>':''}</h3>
      ${ps.length?`<div class="pgrid pf">${ps.map(pp=>`
        <div><label>${esc(pp.name)}</label>
          <input data-p="${esc(pp.name)}" placeholder="${esc(pp.default)}">
          <div class="cmt">默认 <span class="dv">${esc(pp.default)}</span>
            ${pp.comment?' · '+esc(pp.comment):''}</div></div>`).join('')}</div>`
        :`<div class="note">该版本没有用 <span class="mono">g.x = getattr(g,'x',默认)</span>
           的写法声明参数，所以没有可填项。</div>`}
      <div style="margin-top:14px;display:flex;align-items:center;gap:12px">
        <button class="btn" id="f_run" ${v.runnable?'':'disabled style="opacity:.45;cursor:not-allowed"'}
          >${v.same_version?'开始回测':'按当前版本回测'}</button>
        <span class="cmt" id="f_msg"></span>
      </div>
    </div>

    <div class="sec" id="jobsec" style="display:none">
      <h3>运行日志 <span class="cmt" id="jobstate"></span></h3>
      <pre id="joblog"></pre>
    </div>

    ${Object.keys(bytes).length>1?`<div class="sec">
      <h3>本版本下的字节版本 <span class="cmt">—— 行为相同，差异仅注释 / 排版 / 简介</span></h3>
      <div class="note">版本分组用的是<b>行为哈希</b>（AST 结构 + 字面量，注释不在 AST 里）。
        下面这些字节不同但行为一致，所以归在同一版本。
        字节哈希仍逐条保留 —— 归档里躺的到底是哪份字节，可以一字不差地追溯。</div>
      <div style="margin-top:8px">${Object.keys(bytes).sort((a,b)=>
        (newest(bytes[b]).ran_at||'')<(newest(bytes[a]).ran_at||'')?-1:1).map(bs=>
        `<div style="padding:4px 0"><span class="sha">${bs}</span>
          <span class="cmt" style="margin-left:8px">${bytes[bs].length} 次回测 ·
          最近 ${(newest(bytes[bs]).ran_at||'').replace('T',' ').slice(5,16)}</span>
          ${bs===v.code_sha?'<span class="pill" style="margin-left:8px">当前展示</span>':
            `<span class="vopen" data-ver="${bytes[bs][0].code_sha256}"
               style="margin-left:8px">看这份代码 →</span>`}</div>`).join('')}</div></div>`:''}

    <div class="sec"><h3>策略代码 <span class="cmt">${(v.code||'').split('\n').length} 行 ·
      字节 SHA256 ${esc(v.code_sha256)}</span></h3>
      <pre>${esc(v.code)}</pre></div>

    <div class="sec"><h3>该版本的历次回测</h3>${runs.length?runTable(runs):'<div class="note">还没有回测</div>'}</div>`;

  document.querySelectorAll('#vp .runs tbody tr[data-id]').forEach(e=>e.onclick=()=>{
    location.hash='#/run/'+encodeURIComponent(e.dataset.id);});
  document.querySelectorAll('#vp .vopen[data-ver]').forEach(e=>e.onclick=ev=>{
    ev.stopPropagation(); location.hash='#/ver/'+e.dataset.ver;});
  const btn=$('#f_run'); if(btn && v.runnable) btn.onclick=startBacktest;
}

async function startBacktest(){
  const btn=$('#f_run'), msg=$('#f_msg');
  const params={};
  document.querySelectorAll('#vp input[data-p]').forEach(e=>{
    const t=e.value.trim(); if(t) params[e.dataset.p]=t;});
  const body={sha:VER.code_sha256, params,
    start:$('#f_start').value.trim(), end:$('#f_end').value.trim(),
    cash:$('#f_cash').value.trim(), jq_cost:$('#f_jq').checked};
  btn.disabled=true; btn.style.opacity=.45; msg.textContent='提交中…';
  let r;
  try{
    r=await (await fetch('/api/backtest',{method:'POST',
      headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})).json();
  }catch(e){ r={error:String(e)}; }
  if(r.error){ msg.innerHTML='<span class="bad">'+esc(r.error)+'</span>';
    btn.disabled=false; btn.style.opacity=1; return; }
  msg.textContent='';
  $('#jobsec').style.display='';
  $('#joblog').textContent=r.cmd+'\n';
  pollJob(r.job_id, btn);
}

function pollJob(id, btn){
  stopPoll();
  const log=$('#joblog'), st=$('#jobstate');
  POLL=setInterval(async()=>{
    let o; try{ o=await j('/api/job?id='+encodeURIComponent(id)); }catch(e){ return; }
    log.textContent=(o.lines||[]).join('\n');
    log.scrollTop=log.scrollHeight;
    if(o.state==='running'){ st.textContent='运行中…'; return; }
    stopPoll();
    btn.disabled=false; btn.style.opacity=1;
    if(o.state==='done'){
      st.innerHTML='完成'+(o.run_id?` · <a href="#/run/${encodeURIComponent(o.run_id)}">查看结果 →</a>`:'');
      // 归档变了，刷新列表，这样目录树和「历次回测」都能立刻看到新记录
      loadRuns0().then(()=>{ if(VER) renderVersionRuns(); });
    }else{
      st.innerHTML='<span class="bad">失败（退出码 '+o.rc+'）</span>';
    }
  },1200);
}

async function loadRuns0(){ RUNS=await j('/api/runs'); }
function renderVersionRuns(){
  const runs=RUNS.filter(r=>(r.sem_sha256||r.code_sha256)===(VER.sem_sha256||VER.code_sha256));
  const secs=document.querySelectorAll('#vp .sec');
  const box=secs[secs.length-1];
  if(!box) return;
  box.innerHTML='<h3>该版本的历次回测</h3>'+(runs.length?runTable(runs):'<div class="note">还没有回测</div>');
  box.querySelectorAll('.runs tbody tr[data-id]').forEach(e=>e.onclick=()=>{
    location.hash='#/run/'+encodeURIComponent(e.dataset.id);});
}

/* ============ 详情页 ============ */
async function openRun(id, initTab){
  CUR=id; DATA={}; HD={off:0,lim:HD?HD.lim:100};
  MSEL=null; DSEL=null; YOPEN=null;   // 下钻状态跟着回测走，不跨回测残留
  EQR={a:null,b:null};
                              // ★ YPAGE 由 route() 先设好，这里不能清
  stopPoll();
  enterView();
  $('#main').innerHTML='<div class="note" style="padding:40px 0">加载中…</div>';
  let run,eq;
  try{ [run,eq]=await Promise.all([j('/api/run?id='+encodeURIComponent(id)),
                                   j('/api/equity?id='+encodeURIComponent(id))]); }
  catch(e){ $('#main').innerHTML=`<div class="note" style="padding:40px 0">
      加载失败：${e.message}　<a href="#">返回目录</a></div>`; return; }
  DATA.run=run; DATA.eq=eq;
  const m=run.meta;
  $('#main').innerHTML=`
   <div id="hd">
     <div class="row1"><h2>${m.strategy}</h2>
       <span class="meta">${m.group} · ${m.first_day} ~ ${m.last_day} ·
         初始 ${(+m.cash).toLocaleString()} · ${m.trading_days} 交易日
         ${Object.keys(m.params||{}).length?' · '+Object.entries(m.params).map(
            ([k,v])=>k+'='+v).join(' '):''}</span></div>
     <div class="row1"><span class="meta">run_id ${m.run_id} ·
       代码 ${(m.code_sha256||'').slice(0,8)} ·
       数据 ${((m.data_fingerprint||{}).overall||'—').slice(0,8)} ·
       跑于 ${m.ran_at} (${m.elapsed_sec}s)</span></div>
     <div id="tabs">
       ${['概览','权益曲线','收益明细','交易记录','持仓','拒单','策略代码','日志','元信息']
         .map((t,i)=>`<div data-i="${i}" class="${i?'':'on'}">${t}</div>`).join('')}
     </div>
   </div>
   ${[0,1,2,3,4,5,6,7,8].map(i=>`<div class="pane ${i?'':'on'}" id="p${i}"></div>`).join('')}`;
  document.querySelectorAll('#tabs div').forEach(e=>e.onclick=()=>tab(+e.dataset.i));
  paneOverview(); paneEquity();
  if(initTab) tab(initTab);
  window.scrollTo(0,0);
}
function tab(i){
  document.querySelectorAll('#tabs div').forEach((e,k)=>e.classList.toggle('on',k===i));
  document.querySelectorAll('.pane').forEach((e,k)=>e.classList.toggle('on',k===i));
  ({2:paneMonthly,3:paneTrades,4:paneHoldings,5:paneRejects,6:paneCode,7:paneLog,
    8:paneMeta}[i]||(()=>{}))();
}

/* ============ 概览 ============ */
function card(k,v,cls,note){return `<div class="card"><div class="k">${k}</div>
  <div class="v ${cls||''}">${v}</div>${note?`<div class="n">${note}</div>`:''}</div>`;}
function paneOverview(){
  const s=DATA.run.stats, m=DATA.run.meta, c=m.cost||{};
  const g1=[
    card('累计收益',pct(s.total_return,1),sign(s.total_return)),
    card('年化收益',pct(s.annual_return,2),sign(s.annual_return)),
    card('最大回撤',pct(s.max_drawdown,2),'neg','日频'),
    card('年化波动',pct(s.volatility,1)),
    card('夏普',fmtN(s.sharpe)),card('索提诺',fmtN(s.sortino)),
    card('卡玛',fmtN(s.calmar)),card('期末权益',(+s.end_value).toLocaleString('en',{maximumFractionDigits:0})),
  ].join('');
  const g2=s.benchmark_return==null?'<div class="note">未设基准</div>':[
    card('基准收益',pct(s.benchmark_return,2),'',s.benchmark||''),
    card('超额收益',pct(s.excess_return,1),sign(s.excess_return)),
    card('超额年化',pct(s.excess_annual,2),sign(s.excess_annual)),
    card('超额回撤',pct(s.excess_max_drawdown,2),'neg'),
    card('信息比率',fmtN(s.info_ratio)),
    card('alpha',pct(s.alpha,2),sign(s.alpha)),card('beta',fmtN(s.beta,3)),
  ].join('');
  const g3=[
    card('平仓笔数',s.n_trades??'—'),card('胜率',pct(s.win_rate,1)),
    card('盈亏比',fmtN(s.profit_factor)),
    card('平均持有',fmtN(s.avg_holding_days,1),'','天'),
    card('年换手',fmtN(s.turnover_per_year),'','次全仓往返'),
    card('费用合计',(+(s.fee_paid||0)).toLocaleString('en',{maximumFractionDigits:0})),
    card('红利税',(+(s.div_tax_paid||0)).toLocaleString('en',{maximumFractionDigits:0})),
    card('退市清算',s.n_delisted??'—','','笔'),
  ].join('');
  const risk=[
    card('最大单仓权重',pct(s.max_weight_avg,1),'','均值'),
    card('单仓权重峰值',pct(s.max_weight_peak,1),
      (s.max_weight_peak>0.3?'warn':'')),
    card('HHI',fmtN(s.hhi_avg,3),'','集中度'),
    card('停牌挂账',s.frozen_days??0,'','持仓日'),
    card('最长停牌',s.max_frozen_run??0,'','连续交易日'),
    card('期末停牌持仓',s.n_frozen_at_end??0,
      (s.n_frozen_at_end?'warn':''),s.n_frozen_at_end?'权益含陈价':''),
    card('拒单',s.n_rejects??0,'','笔'),
  ].join('');
  $('#p0').innerHTML=`<h3 class="sec">收益与风险</h3><div class="cards">${g1}</div>
    <h3 class="sec">相对基准</h3><div class="cards">${g2}</div>
    <h3 class="sec">交易与成本</h3><div class="cards">${g3}</div>
    <h3 class="sec">风险暴露</h3><div class="cards">${risk}</div>
    <h3 class="sec">成本假设</h3><div class="note">
      滑点 ${c.slippage} 双边 · 佣金 ${c.commission}（最低 ${c.min_commission} 元）·
      印花税 ${c.close_tax==='auto'?'按日期分段（2023-08-28 起减半）':c.close_tax} ·
      买入印花税 ${c.open_tax} · 红利税 ${c.dividend_tax?'计':'不计'} ·
      成交量上限 ${c.volume_ratio?'当日成交额 '+(c.volume_ratio*100)+'%':'不限'}
      ${(c.locked_by_cli||[]).length?' · 命令行指定: '+c.locked_by_cli.join(', '):''}</div>`;
}

/* ============ SVG 折线图 ============ */
function lineChart(el,series,opt){
  opt=opt||{}; const W=1160,H=opt.h||330,L=54,R=16,T=opt.t||22,B=26;
  const n=series[0].v.length; if(!n){el.innerHTML='<div class="note">无数据</div>';return;}
  const log=!!opt.log;
  const tf=v=>log?Math.log10(Math.max(v,1e-6)):v;
  let lo=Infinity,hi=-Infinity;
  series.forEach(s=>s.v.forEach(v=>{if(v==null)return;const t=tf(v);
    if(t<lo)lo=t; if(t>hi)hi=t;}));
  if(!(hi>lo)){hi=lo+1;}
  const pad=(hi-lo)*0.06; lo-=pad; hi+=pad;
  const X=i=>L+(W-L-R)*(n<2?0:i/(n-1)), Y=v=>T+(H-T-B)*(1-(tf(v)-lo)/(hi-lo));
  const path=s=>{let d='',on=false;
    s.v.forEach((v,i)=>{if(v==null){on=false;return;}
      d+=(on?'L':'M')+X(i).toFixed(1)+' '+Y(v).toFixed(1)+' ';on=true;});return d;};
  // y 轴刻度
  let ticks=[];
  for(let k=0;k<=4;k++){const t=lo+(hi-lo)*k/4; ticks.push(log?Math.pow(10,t):t);}
  const yl=v=>opt.pctAxis?((v-1)*100).toFixed(0)+'%':(v>=10?v.toFixed(0)+'x':v.toFixed(2)+'x');
  // x 轴：取 6 个日期
  const xs=[]; for(let k=0;k<6;k++){const i=Math.round((n-1)*k/5); xs.push([i,opt.dates[i]]);}
  el.innerHTML=`
   ${opt.title?`<div class="ttl">${opt.title}</div>`:''}
   ${opt.ctl||''}
   <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
     ${ticks.map(v=>`<line class="gl" x1="${L}" x2="${W-R}" y1="${Y(v).toFixed(1)}" y2="${Y(v).toFixed(1)}"/>
        <text class="ax" x="${L-7}" y="${(Y(v)+3).toFixed(1)}" text-anchor="end">${yl(v)}</text>`).join('')}
     ${xs.map(([i,d])=>`<text class="ax" x="${X(i).toFixed(1)}" y="${H-8}" text-anchor="middle">${d}</text>`).join('')}
     ${series.map(s=>`<path d="${path(s)}" fill="none" stroke="${s.c}"
        stroke-width="${s.w||1.6}" stroke-linejoin="round"/>`).join('')}
     <rect id="hz" x="${L}" y="${T}" width="${W-L-R}" height="${H-T-B}" fill="transparent"/>
   </svg>
   <div class="note" style="padding-left:8px">${series.map(s=>
      `<span style="color:${s.c}">━</span> ${s.n}`).join('　')}</div>`;
  // hover
  const svg=el.querySelector('svg'), tip=$('#tip');
  svg.onmousemove=e=>{const r=svg.getBoundingClientRect();
    const i=Math.round((n-1)*Math.min(1,Math.max(0,((e.clientX-r.left)/r.width*W-L)/(W-L-R))));
    tip.style.display='block'; tip.style.left=(e.clientX+12)+'px';
    tip.style.top=(e.clientY+10)+'px';
    tip.textContent=opt.dates[i]+'\n'+series.map(s=>s.n+'  '+
      (s.v[i]==null?'—':(opt.pctAxis?((s.v[i]-1)*100).toFixed(1)+'%':s.v[i].toFixed(3)+'x'))).join('\n');};
  svg.onmouseleave=()=>{$('#tip').style.display='none';};
}
let EQLOG=true;
let EQR={a:null,b:null};      // 权益曲线的显示区间（null = 全程）

function paneEquity(){
  const e=DATA.eq, s=DATA.run.stats;
  const D=e.dates;
  // ★ 区间【重新归一】：截一段不是把全程曲线切开看，而是以区间首日为 1x
  //   重算。否则 2024 那种年份会显示成 "从 18x 到 19x"，读不出当年涨跌。
  //   回撤同理 —— 峰值只在区间内累计，不带入区间之前的历史高点。
  let i0=0,i1=D.length-1;
  if(EQR.a){ while(i0<D.length-1 && D[i0]<EQR.a) i0++; }
  if(EQR.b){ while(i1>0 && D[i1]>EQR.b) i1--; }
  if(i1<i0){ i0=0; i1=D.length-1; EQR={a:null,b:null}; }
  // ★ 基点取【区间前一交易日】收盘，与收益明细里的年/月收益同口径
  //   （用区间内首日做基点会漏掉首日那根，2024 会显示 +6.52% 而不是 +8.68%）。
  //   锚点那天也画进曲线，所以起点正好落在 1x 上。
  const j0=i0>0?i0-1:i0;
  const cut=v=>v==null?null:v.slice(j0,i1+1);
  const dates=cut(D);
  const base=e.equity[j0];
  const eq=cut(e.equity).map(v=>v/base);
  let peak=0; const dd=eq.map(v=>{peak=Math.max(peak,v);return v/peak-1;});
  const ser=[{n:'策略',v:eq,c:'#5b9cf0',w:1.8}];
  if(e.benchmark){
    const bb=cut(e.benchmark), b0=bb.find(v=>v!=null);
    if(b0) ser.push({n:'基准 '+(e.benchmark_code||''),
                     v:bb.map(v=>v==null?null:v/b0),c:'#8b97a6',w:1.3});
  }
  const full=(i0===0&&i1===D.length-1);
  const ret=eq[eq.length-1]-1, mdd=Math.min(...dd);
  const yrs=[...new Set(D.map(d=>d.slice(0,4)))].sort();
  const btn=(t,a,b,on)=>`<span class="rg ${on?'on':''}" data-a="${a||''}" data-b="${b||''}">${t}</span>`;
  const last=D[D.length-1], shift=n=>{const d=new Date(last);
    d.setFullYear(d.getFullYear()-n); return d.toISOString().slice(0,10);};
  $('#p1').innerHTML=`
    <div class="rgbar">区间
      ${btn('全部','','',full)}
      ${btn('近1年',shift(1),'',!full&&EQR.a===shift(1))}
      ${btn('近3年',shift(3),'',!full&&EQR.a===shift(3))}
      ${btn('近5年',shift(5),'',!full&&EQR.a===shift(5))}
      <span class="sp"></span>
      ${yrs.map(y=>btn(y,y+'-01-01',y+'-12-31',
          EQR.a===y+'-01-01'&&EQR.b===y+'-12-31')).join('')}
      <span class="sp"></span>
      <input type="date" id="ra" value="${EQR.a||D[0]}">
      <span style="color:var(--dim)">~</span>
      <input type="date" id="rb" value="${EQR.b||last}">
      <span class="rg" id="rgo">应用</span>
    </div>
    <div class="note">显示 <b>${D[i0]} ~ ${D[i1]}</b>（${i1-i0+1} 交易日）：
      区间收益 <b class="${sign(ret)}">${_sn(ret,2)}%</b>，
      区间最大回撤 <b class="neg">${pct(-mdd,2)}</b>。
      ★ 截取区间会<b>重新归一</b>（基点 = ${j0<i0?'区间前一交易日 '+D[j0]:'首日'} 收盘 = 1x），
      回撤峰值也只在区间内累计 —— 不是把全程曲线切开看。
      与「收益明细」里的年/月收益<b>同口径</b>。</div>
    <div class="chart" id="c1"></div>
    <div class="note">纵轴为净值倍数（区间起点 1x）。${EQLOG?'当前<b>对数坐标</b>。':'当前线性坐标。'}
      基准按区间首日归一；统计卡里的基准收益以<b>回测首日前一交易日</b>收盘为基点
      （与聚宽一致），两者口径不同。</div>
    <div class="chart" id="c2"></div>`;
  lineChart($('#c1'),ser,{dates,log:EQLOG,title:'净值曲线',h:360,
    ctl:`<div class="ctl"><span class="${EQLOG?'on':''}" id="lg">对数</span><span class="${EQLOG?'':'on'}" id="ln">线性</span></div>`});
  $('#lg').onclick=()=>{EQLOG=true;paneEquity();};
  $('#ln').onclick=()=>{EQLOG=false;paneEquity();};
  lineChart($('#c2'),[{n:'回撤',v:dd.map(v=>v+1),c:'#e05b5b',w:1.4}],
    {dates,title:'回撤（水下图）',h:190,pctAxis:true});
  $('#p1').querySelectorAll('.rg[data-a]').forEach(b=>b.onclick=()=>{
    EQR={a:b.dataset.a||null,b:b.dataset.b||null}; paneEquity();});
  $('#rgo').onclick=()=>{EQR={a:$('#ra').value||null,b:$('#rb').value||null}; paneEquity();};
}

/* ============ 收益明细：年热力 → 年详情页（月热力）→ 月内日热力 ============ */
/* 数据全部来自 /api/equity 的 dates + equity ——【不需要新表】：
     · dates 本身就是交易日历，不在里面的日期即非交易日（日历格子打斜纹）
     · 日收益 = equity[i]/equity[i-1]-1，首日无前值故记 —
     · 月收益基点 = 上月最后一个交易日权益（首月退化为自身首日，与原实现一致）
     · 年收益 = 该年首末权益比；年内/月内最大回撤在各自的日线上重算 */
let YPAGE=null;                      // 年详情页的年份，null = 年度总览
let MSEL=null;                       // 年详情页里展开的月份 'YYYY-MM'
let DSEL=null;                       // 日历里选中的那一天 'YYYY-MM-DD'
let YOPEN=null;                      // 兼容旧状态位（openRun 会重置）

function _mstat(){
  const e=DATA.eq, by={}, dr={}, byY={};
  let prev=null;
  e.dates.forEach((d,i)=>{
    const k=d.slice(0,7), y=d.slice(0,4);
    if(!(k in by)) by[k]=[prev==null?e.equity[i]:prev,e.equity[i]];
    by[k][1]=e.equity[i];
    if(!(y in byY)) byY[y]=[prev==null?e.equity[i]:prev,e.equity[i]];
    byY[y][1]=e.equity[i];
    dr[d]=prev==null?null:(prev?e.equity[i]/prev-1:null);
    prev=e.equity[i];
  });
  const mv={}; Object.entries(by).forEach(([k,[a,b]])=>{mv[k]=a?b/a-1:null;});
  const yv={}; Object.entries(byY).forEach(([k,[a,b]])=>{yv[k]=a?b/a-1:null;});
  // 区间内最大回撤：在该区间【自己的日线】上重算，不是全程回撤的切片
  const dd=k=>{let pk=0,mx=0;
    e.dates.forEach((d,i)=>{ if(!d.startsWith(k)) return;
      if(e.equity[i]>pk) pk=e.equity[i];
      if(pk) mx=Math.max(mx,1-e.equity[i]/pk);});
    return mx;};
  const nd={}; e.dates.forEach(d=>{const y=d.slice(0,4),k=d.slice(0,7);
    nd[y]=(nd[y]||0)+1; nd[k]=(nd[k]||0)+1;});
  return {mv,yv,dr,dd,nd,idx:Object.fromEntries(e.dates.map((d,i)=>[d,i]))};
}

/* 底色标定（两次都翻过车，把结论写在这里）：
     · 第一版 rgba(240,91,91,.85) + 红字 -> 对比度 1.28:1，字溶进背景
     · 第二版矫枉过正，alpha 封顶 0.50 又太淡，弱格子几乎没颜色
   现在：**底色压暗但不降浓度**（168,44,44 / 24,116,82），alpha 0.15~0.88，
   强度走 sqrt 让中段更早出色。字保持中性亮色，实测对比度：
     最弱格 12.6:1、中段 7.5~8.6:1、最强格 6.2:1(红) / 5.2:1(绿)
   —— 全部 >= WCAG AA 4.5，多数 >= AAA 7。 */
const _hcol=(v,k)=>{
  if(v==null) return 'transparent';
  const t=Math.min(1,Math.abs(v)*(k||3));
  const a=(0.15+Math.sqrt(t)*0.73).toFixed(3);
  return v>0?`rgba(168,44,44,${a})`:`rgba(24,116,82,${a})`;
};
/* 强度弱的格子底色近乎透明，方向全靠这个 +/- 号 —— 不能省 */
const _sn=(v,d)=>v==null?'—':(v>0?'+':'')+(v*100).toFixed(d);
const _yhash=y=>'#/run/'+encodeURIComponent(CUR)+'/y/'+y;

function paneMonthly(){ YPAGE?paneYear():paneYears(); }

/* ---- 第一层：年度热力 ---- */
function paneYears(){
  const S=_mstat();
  const ys=[...new Set(DATA.eq.dates.map(d=>d.slice(0,4)))].sort();
  $('#p2').innerHTML=`<div class="note">按年看「哪一年崩的」。点年份进入该年详情，
     可继续下钻到月、到日。</div>
   <div class="hm y">${ys.map(y=>{const v=S.yv[y];
     return `<div class="hc" data-y="${y}" style="background:${_hcol(v)}">
       <div class="k">${y}</div>
       <div class="v">${_sn(v,1)}%</div>
       <div class="n">回撤 ${pct(S.dd(y),1)} · ${S.nd[y]} 日</div></div>`;}).join('')}</div>
   ${_legend()}`;
  $('#p2').querySelectorAll('.hc').forEach(c=>c.onclick=()=>{location.hash=_yhash(c.dataset.y);});
}

/* ---- 第二层：年详情页（月度热力）---- */
function paneYear(){
  const S=_mstat(), y=YPAGE;
  const ys=[...new Set(DATA.eq.dates.map(d=>d.slice(0,4)))].sort();
  const at=ys.indexOf(y);
  const ms=[...Array(12)].map((_,i)=>y+'-'+String(i+1).padStart(2,'0'))
                         .filter(k=>S.mv[k]!=null);
  const best=ms.reduce((a,b)=>a==null||S.mv[b]>S.mv[a]?b:a,null);
  const worst=ms.reduce((a,b)=>a==null||S.mv[b]<S.mv[a]?b:a,null);
  $('#p2').innerHTML=`
   <div class="crumb"><a id="c_all">← 年度总览</a><span>/</span><b>${y} 年</b>
     <span style="margin-left:auto">
       ${at>0?`<a id="c_prev">◀ ${ys[at-1]}</a>`:''}
       ${at<ys.length-1?`<a id="c_next" style="margin-left:10px">${ys[at+1]} ▶</a>`:''}
     </span></div>
   <div class="cards">
     ${card('年收益',pct(S.yv[y],2),sign(S.yv[y]))}
     ${card('年内最大回撤',pct(S.dd(y),2),'neg','按该年日线重算')}
     ${card('交易日',S.nd[y])}
     ${card('最好月',best?best.slice(5)+'月':'—',best?sign(S.mv[best]):'',best?pct(S.mv[best],1):'')}
     ${card('最差月',worst?worst.slice(5)+'月':'—',worst?sign(S.mv[worst]):'',worst?pct(S.mv[worst],1):'')}
   </div>
   <h3 class="sec">${y} 年 · 月度热力<span style="float:right;font-weight:400;color:var(--dim)">
     点月份看当月日热力</span></h3>
   <div class="hm m">${[...Array(12)].map((_,i)=>{
     const k=y+'-'+String(i+1).padStart(2,'0'), v=S.mv[k];
     if(v==null) return `<div class="hc na"><div class="k">${i+1} 月</div>
       <div class="v">—</div><div class="n">无数据</div></div>`;
     return `<div class="hc ${MSEL===k?'on':''}" data-m="${k}" style="background:${_hcol(v)}">
       <div class="k">${i+1} 月</div>
       <div class="v">${_sn(v,1)}%</div>
       <div class="n">回撤 ${pct(S.dd(k),1)} · ${S.nd[k]} 日</div></div>`;}).join('')}</div>
   ${_legend()}
   <div id="cal"></div><div id="day"></div>`;
  $('#c_all').onclick=()=>{location.hash='#/run/'+encodeURIComponent(CUR);};
  const pv=$('#c_prev'), nx=$('#c_next');
  if(pv) pv.onclick=()=>{MSEL=null;DSEL=null;location.hash=_yhash(ys[at-1]);};
  if(nx) nx.onclick=()=>{MSEL=null;DSEL=null;location.hash=_yhash(ys[at+1]);};
  $('#p2').querySelectorAll('.hc[data-m]').forEach(c=>c.onclick=()=>{
    MSEL=(MSEL===c.dataset.m)?null:c.dataset.m; DSEL=null; paneYear();});
  drawCal(S);
}

/* ---- 第三层：月内日热力（自然日历，非交易日打斜纹）---- */
function drawCal(S){
  const box=$('#cal'); if(!box) return;
  if(!MSEL){box.innerHTML='';return;}
  const [Y,M]=MSEL.split('-').map(Number);
  const first=new Date(Date.UTC(Y,M-1,1)), ndays=new Date(Date.UTC(Y,M,0)).getUTCDate();
  const lead=(first.getUTCDay()+6)%7;          // 周一为第一列
  const e=DATA.eq;
  let cells='';
  for(let i=0;i<lead;i++) cells+='<div class="cd pad"></div>';
  let nt=0,ntd=0,best=null,worst=null;
  for(let d=1;d<=ndays;d++){
    const ds=`${Y}-${String(M).padStart(2,'0')}-${String(d).padStart(2,'0')}`;
    const i=S.idx[ds];
    if(i===undefined){cells+=`<div class="cd off"><div class="d">${d}</div></div>`;nt++;continue;}
    ntd++;
    const v=S.dr[ds];
    if(v!=null){ if(best==null||v>best[1])best=[ds,v]; if(worst==null||v<worst[1])worst=[ds,v]; }
    const pos=e.cash_pct?(1-e.cash_pct[i]):null;
    cells+=`<div class="cd ${DSEL===ds?'on':''}" data-d="${ds}"
      style="background:${_hcol(v,12)}"
      title="${ds}  ${v==null?'首日无前值':'日收益 '+pct(v,2)}
仓位 ${pos==null?'—':pct(pos,1)}   持仓 ${e.n_positions?e.n_positions[i]:'—'} 只
点击看当日持仓与买卖">
      <div class="d">${d}</div>
      <div class="v">${_sn(v,2)}</div></div>`;
  }
  box.innerHTML=`<h3 class="sec">${Y} 年 ${M} 月 · 日热力
     <span style="float:right;font-weight:400">交易日 ${ntd} 天 / 非交易日 ${nt} 天
     ${best?`　最好 ${best[0].slice(8)}日 <span class="pos">${pct(best[1],2)}</span>`:''}
     ${worst?`　最差 ${worst[0].slice(8)}日 <span class="neg">${pct(worst[1],2)}</span>`:''}
     </span></h3>
   <div class="calg">${['一','二','三','四','五','六','日']
     .map(w=>`<div class="wd">${w}</div>`).join('')}${cells}</div>`;
  box.querySelectorAll('.cd[data-d]').forEach(c=>c.onclick=()=>{
    DSEL=(DSEL===c.dataset.d)?null:c.dataset.d;
    box.querySelectorAll('.cd').forEach(x=>x.classList.toggle('on',x.dataset.d===DSEL));
    drawDay(S);});
  drawDay(S);
}

function _legend(){
  const mk=v=>`<i style="background:${_hcol(v)}"></i>`;
  return `<div class="lgd">跌 ${[-.25,-.12,-.05,-.01].map(mk).join('')}
    <i style="background:transparent"></i>${[.01,.05,.12,.25].map(mk).join('')} 涨
    <span style="margin-left:10px">底色只表示方向与强度，正负看数字前的 +/- 号</span></div>`;
}

/* ---- 第四层：某一天的持仓 / 买卖（读 /api/day，只用现有归档）---- */
async function drawDay(S){
  const box=$('#day'); if(!box) return;
  if(!DSEL){box.innerHTML='';return;}
  const d=DSEL, e=DATA.eq, i=S.idx[d];
  box.innerHTML='<div class="note">加载中…</div>';
  let o; try{ o=await j('/api/day?id='+encodeURIComponent(CUR)+'&d='+d); }
  catch(err){ box.innerHTML=`<div class="note">加载失败：${err.message}</div>`; return; }
  if(DSEL!==d) return;                 // 点得快时后到的响应不要覆盖当前选择
  const pos=e.cash_pct?(1-e.cash_pct[i]):null;
  const nm=r=>`${r.code}${r.name?' '+r.name:''}`;
  const money=v=>v==null?'—':(+v).toLocaleString('en',{maximumFractionDigits:0});
  const px=v=>v==null?'—':(+v).toFixed(3);
  box.innerHTML=`<h3 class="sec">${d} · 当日明细</h3>
    <div class="cards">
      ${card('当日收益',_sn(S.dr[d],2)+'%',sign(S.dr[d]))}
      ${card('权益',money(e.equity[i]*(DATA.run.stats.cash||1)))}
      ${card('仓位',pct(pos,1))}
      ${card('持仓',o.holdings.length+' 只')}
      ${card('当日买入',o.buys.length+' 笔')}
      ${card('当日卖出',o.sells.length+' 笔')}
    </div>
    <div id="d_sell"></div><div id="d_buy"></div><div id="d_hold"></div>`;
  tbl($('#d_sell'),o.sells,[
    {k:'code',t:'标的',l:1,f:(v,r)=>nm(r)},
    {k:'shares',t:'股数',f:v=>money(v)},
    {k:'entry_date',t:'建仓日'},
    {k:'entry_price',t:'建仓价',f:px},
    {k:'exit_price',t:'卖出价',f:px},
    {k:'ret',t:'收益率',s:1,f:v=>_sn(v,2)+'%'},
    {k:'pnl',t:'盈亏',s:1,f:v=>money(v)},
    {k:'fee',t:'费用',f:v=>money(v)},
    {k:'reason',t:'原因',l:1,f:v=>({rebalance:'调仓',intraday:'盘中规则',
        stop:'止损',delist:'退市清算'}[v]||v)},
  ],'当日卖出 '+o.sells.length+' 笔'+(o.sells.length?'':'（无）'));
  tbl($('#d_buy'),o.buys,[
    {k:'code',t:'标的',l:1,f:(v,r)=>nm(r)},
    {k:'shares',t:'股数',f:v=>money(v)},
    {k:'entry_price',t:'建仓价',f:px},
    {k:'gross_amount',t:'成交金额',f:v=>money(v)},
    {k:'exit_date',t:'后来平仓于'},
    {k:'ret',t:'该笔最终',s:1,f:v=>_sn(v,2)+'%'},
  ],'当日买入 '+o.buys.length+' 笔'+(o.buys.length?
      '　（后两列是这笔【整笔】的最终结果，不是当日收益）':'（无）'));
  tbl($('#d_hold'),o.holdings,[
    {k:'code',t:'标的',l:1,f:(v,r)=>nm(r)},
    {k:'weight',t:'权重',f:v=>pct(v,1)},
    {k:'value',t:'市值',f:v=>money(v)},
    {k:'shares',t:'股数',f:v=>money(v)},
    {k:'entry_date',t:'建仓日'},
    {k:'entry_price',t:'建仓价',f:px},
    {k:'last_price',t:'现价',f:px},
    {k:'unrealized_ret',t:'浮盈',s:1,f:v=>_sn(v,2)+'%'},
    {k:'unrealized_pnl',t:'浮盈额',s:1,f:v=>money(v)},
  ],'收盘持仓 '+o.holdings.length+' 只');
}

/* ============ 通用表格（可排序） ============ */
/* opt.group: 按该列分组，值变化时插一条粘性分隔行（同组行必须相邻，由后端排序保证）。
   opt.groupNote(rows) 给分隔行右侧加汇总。点列头排序会打散分组，此时自动关掉分隔行。 */
function tbl(el,rows,cols,note,opt){
  opt=opt||{};
  if(!rows.length){el.innerHTML=(note?`<div class="note">${note}</div>`:'')+
    '<div class="note">无数据</div>';return;}
  let sk=null,sd=-1;
  const body=rs=>{
    let out='',last=null;
    const grp=opt.group&&!sk;   // 排序后分组无意义（同组行不再相邻）
    rs.forEach(r=>{
      if(grp&&r[opt.group]!==last){
        last=r[opt.group];
        const same=rs.filter(x=>x[opt.group]===last);
        out+=`<tr class="dayhd"><td colspan="${cols.length}">${last}`+
             `<span>${opt.groupNote?opt.groupNote(same):same.length+' 只'}</span></td></tr>`;
      }
      out+='<tr>'+cols.map(c=>{const v=r[c.k];
        const f=c.f?c.f(v,r):(v==null?'—':v);
        return `<td class="${c.l?'l':''} ${c.s?sign(v):''}">${f}</td>`;}).join('')+'</tr>';
    });
    return out;
  };
  const draw=()=>{
    let rs=rows.slice();
    if(sk) rs.sort((a,b)=>{const x=a[sk],y=b[sk];
      if(x==null)return 1; if(y==null)return -1;
      return (typeof x==='number'?x-y:String(x).localeCompare(String(y)))*sd;});
    el.innerHTML=(note?`<div class="note">${note}</div>`:'')+
     `<div class="tw"><table><thead><tr>${cols.map(c=>
        `<th data-k="${c.k}" class="${c.l?'l':''}">${c.t}${sk===c.k?(sd>0?' ▲':' ▼'):''}</th>`).join('')}
      </tr></thead><tbody>${body(rs)}</tbody></table></div>`;
    el.querySelectorAll('th').forEach(t=>t.onclick=()=>{
      const k=t.dataset.k; sd=(sk===k)?-sd:-1; sk=k; draw();});
    // 日期分隔行要贴在表头【下方】，而表头高度取决于字号/padding/浏览器 ——
    // 与其估一个 px 值（估错就会重叠或留缝），渲染后量出来写进 CSS 变量。
    const th=el.querySelector('th');
    if(th) el.querySelector('.tw').style.setProperty('--thh',
      Math.round(th.getBoundingClientRect().height)+'px');
  };
  draw();
}
async function paneTrades(){
  if(!DATA.tr) DATA.tr=await j('/api/trades?id='+CUR);
  const R={rebalance:'调仓',intraday:'盘中(涨停打开)',delist:'退市清算'};
  tbl($('#p3'),DATA.tr.rows,[
    {k:'code',t:'股票',l:1,f:nmcode},{k:'entry_date',t:'建仓日',l:1},{k:'exit_date',t:'平仓日',l:1},
    {k:'holding_days',t:'持有天'},
    {k:'entry_price',t:'建仓价(后复权)',f:v=>fmtN(v,3)},
    {k:'exit_price',t:'平仓价',f:v=>fmtN(v,3)},
    {k:'ret',t:'收益率',f:v=>pct(v,2),s:1},
    {k:'pnl',t:'盈亏',f:v=>v==null?'—':(+v).toFixed(0),s:1},
    {k:'fee',t:'费用',f:v=>fmtN(v,0)},
    {k:'div_gross',t:'分红',f:v=>fmtN(v,0)},{k:'div_tax',t:'红利税',f:v=>fmtN(v,0)},
    {k:'reason',t:'卖出原因',l:1,f:v=>R[v]||v},
  ],`共 ${DATA.tr.total} 笔平仓。<b>同一天同一只票可能有多行</b> ——
     加减仓走 FIFO 分批，每行是一批，带的是<b>该批</b>的建仓日与持有期
     （红利税按批的持有期分档，所以必须分开记）。收益率只反映<b>价差</b>，分红单列。`);
}
async function paneHoldings(){
  const h=await j(`/api/holdings?id=${CUR}&offset=${HD.off}&limit=${HD.lim}`);
  const pg=Math.floor(h.off===undefined?HD.off/HD.lim:h.offset/h.limit)+1;
  const np=Math.max(1,Math.ceil(h.total/h.limit));
  // ★ 上下两套导航用 class 而不是 id —— 先前用 id="p*" 正则替换成 id="b*" 生成底部
  //   一份，选择器却按 '#'+p+id.slice(1) 拼出 '#bpf'（真实 id 是 '#bf'），
  //   底部按钮全都绑不上事件。class + querySelectorAll 没有这种拼接歧义。
  const nav=`<div class="pg">
     <button class="pf" ${pg<=1?'disabled':''}>« 首页</button>
     <button class="pp" ${pg<=1?'disabled':''}>‹ 上一页</button>
     <span>第 <input class="pi" value="${pg}"> / ${np} 页</span>
     <button class="pn" ${pg>=np?'disabled':''}>下一页 ›</button>
     <button class="pl" ${pg>=np?'disabled':''}>末页 »</button>
     <span style="margin-left:10px">每页
       <select class="ps">${[50,100,200].map(x=>
         `<option ${x===h.limit?'selected':''}>${x}</option>`).join('')}</select> 条</span>
     <span style="margin-left:10px">共 ${h.total.toLocaleString()} 条 /
       ${h.n_days.toLocaleString()} 个交易日</span></div>`;
  $('#p4').innerHTML=`<div class="note">逐日持仓快照，<b>按日期倒序</b>（最近的在前），
     同日内按权重降序。份额是<b>后复权记账单位</b>，真实股数 = 份额 × 当日复权因子。
     点列头排序会打散日期分块（同组行不再相邻）。</div>
     ${nav}<div id="hdt"></div>${nav}`;
  tbl($('#hdt'),h.rows,[
    {k:'code',t:'股票',l:1,f:nmcode},{k:'weight',t:'权重',f:v=>pct(v,2)},
    {k:'value',t:'市值',f:v=>v==null?'—':(+v).toFixed(0)},
    {k:'shares',t:'份额(后复权)',f:v=>fmtN(v,1)},
    {k:'last_price',t:'现价',f:v=>fmtN(v,3)},
    {k:'entry_date',t:'建仓日',l:1},{k:'entry_price',t:'建仓价',f:v=>fmtN(v,3)},
    {k:'unrealized_ret',t:'浮动收益',f:v=>pct(v,2),s:1},
    {k:'unrealized_pnl',t:'浮动盈亏',f:v=>v==null?'—':(+v).toFixed(0),s:1},
    {k:'div_gross',t:'累计分红',f:v=>fmtN(v,0)},
  ],null,{group:'date',groupNote:rs=>{
    const mv=rs.reduce((a,r)=>a+(r.value||0),0);
    const w=rs.reduce((a,r)=>a+(r.weight||0),0);
    return `${rs.length} 只 · 市值 ${mv.toLocaleString('en',{maximumFractionDigits:0})} · 仓位 ${(w*100).toFixed(1)}%`;}});
  const go=o=>{HD.off=Math.max(0,Math.min(o,(np-1)*HD.lim));paneHoldings();};
  const on=(cls,fn,ev)=>document.querySelectorAll('#p4 .'+cls)
      .forEach(e=>e[ev||'onclick']=fn);
  on('pf',()=>go(0)); on('pp',()=>go(HD.off-HD.lim));
  on('pn',()=>go(HD.off+HD.lim)); on('pl',()=>go((np-1)*HD.lim));
  on('ps',e=>{HD.lim=+e.target.value;HD.off=0;paneHoldings();},'onchange');
  on('pi',e=>go((Math.max(1,+e.target.value)-1)*HD.lim),'onchange');
}
async function paneRejects(){
  if(!DATA.rj) DATA.rj=await j('/api/rejects?id='+CUR);
  const r=DATA.rj;
  const S={buy:'买',sell:'卖',adjust:'调仓',dividend:'分红'};
  $('#p5').innerHTML=`<div class="note"><b>失败必须留痕</b> ——
     引擎不静默丢弃任何下单失败。共 ${r.total} 笔。</div>
    <div class="cards">${Object.entries(r.by_reason).map(([k,v])=>card(k,v)).join('')}</div>
    <h3 class="sec">明细</h3><div id="rjt"></div>`;
  tbl($('#rjt'),r.rows,[{k:'date',t:'日期',l:1},{k:'code',t:'股票',l:1,f:nmcode},
    {k:'side',t:'方向',l:1,f:v=>S[v]||v},{k:'reason',t:'原因',l:1}]);
}
async function paneCode(){
  if(!DATA.code) DATA.code=await j('/api/code?id='+CUR);
  $('#p6').innerHTML=`<div class="note">策略代码的<b>逐字节快照</b>（代码会改，结果不会自己解释自己）。
    SHA256 ${DATA.run.meta.code_sha256}</div>
    <pre>${DATA.code.text.replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))}</pre>`;
}
async function paneLog(){
  if(!DATA.log) DATA.log=await j('/api/log?id='+CUR);
  $('#p7').innerHTML=`<pre>${(DATA.log.text||'（无日志）')
    .replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))}</pre>`;
}
function paneMeta(){
  const m=DATA.run.meta, fp=m.data_fingerprint||{};
  const rows=[['run_id',m.run_id],['分组',m.group],['策略',m.strategy],
    ['策略路径',m.strategy_path],['代码 SHA256',m.code_sha256],
    ['区间',m.start+' ~ '+m.end],['实际首尾',m.first_day+' ~ '+m.last_day],
    ['交易日',m.trading_days],['初始资金',(+m.cash).toLocaleString()],
    ['策略参数',JSON.stringify(m.params||{})],
    ['成本',JSON.stringify(m.cost||{})],
    ['数据源',m.datalake],['数据指纹',fp.overall||'—'],
    ...Object.entries(fp.parts||{}).map(([k,v])=>
      ['　'+k,`${v.hash}  ${v.n_files} 文件 / ${(v.total_bytes/1048576).toFixed(1)} MB / 最新 ${v.newest_mtime}`]),
    ['跑于',m.ran_at],['耗时',m.elapsed_sec+' s']];
  $('#p8').innerHTML=`<div class="note"><b>代码哈希 + 数据指纹</b>是归因的第一个岔路口：
     差异来自代码、数据，还是参数 —— 不该靠回忆。</div>
    <div class="kv">${rows.map(([k,v])=>
      `<div class="k">${k}</div><div class="v">${v==null?'—':v}</div>`).join('')}</div>`;
}
loadRuns();
