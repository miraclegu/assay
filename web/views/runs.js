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
