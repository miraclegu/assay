/* ============ 实盘 · 策略与版本 ============
   账户绑的是**快照**（主文件 + 全部依赖），版本历史 append-only、
   删掉 runs/ 也读得到 —— 那是决策证据。

   🔴 磁盘文件与绑定版本不一致时**拒绝**触发回测：跑出来的是另一个版本，
   却会被归档成"这个版本回测过"。拒绝时要给出【可操作的两条出路】
   （重新绑定 / 命令行），只抛一句话的话人只知道"点了报错"。 */
async function openStrat(aid, sha){
  /* ★ 未绑定的账户也要能打开这个浮层 —— 它是绑定的【唯一入口】。
     原来会直接 alert("该账户没有绑定过版本") 然后什么都不做，
     于是新建的账户根本没地方绑策略。 */
  let o=null;
  if(sha){ try{ o=await j('/api/live/strategy?id='+encodeURIComponent(aid)+'&sha='+sha); }
           catch(e){ o=null; } }
  if(!o) o={version:{}, code:'', files:[], params_declared:[],
            runs_same_params:[], runs_other_params:[], _unbound:true};
  const v=o.version||{}, ro=LV.readonly;
  const runTab=(rows,title)=>rows.length?`<div style="margin-top:8px"><b>${title}（${rows.length}）</b>
    <table class="lvt"><tr><th>区间</th><th>本金</th><th>年化</th><th>回撤</th><th>夏普</th><th>参数</th></tr>
    ${rows.map(r=>`<tr><td><a href="#/run/${encodeURIComponent(r.run_id)}">${esc(r.start)}~${esc(r.end)}</a>
      ${r.stale?'<span class="lvwhy"> 数据已变</span>':''}</td>
      <td>${num(r.cash)}</td><td>${r.annual==null?'—':(r.annual*100).toFixed(2)+'%'}</td>
      <td>${r.max_drawdown==null?'—':(r.max_drawdown*100).toFixed(2)+'%'}</td>
      <td>${r.sharpe==null?'—':Number(r.sharpe).toFixed(2)}</td>
      <td class="lvpar">${Object.entries(r.params||{}).map(([k,x])=>
        `<span class="lvkv">${esc(k)}=${esc(x)}</span>`).join('')||'<span class="lvwhy">默认</span>'}</td>
      </tr>`).join('')}</table></div>`:'';
  const same=o.runs_same_params||[], other=o.runs_other_params||[];
  const vs=((LVO||{}).versions||[]).slice().reverse();
  const el=$('#stwrap')||(()=>{ const d=document.createElement('div'); d.id='stwrap';
    document.getElementById('app').appendChild(d); return d; })();
  el.className='stmodal';
  el.innerHTML=`<div class="stbox">
    <div class="lvhead" style="margin-bottom:8px">
      <h2>${esc(v.strategy_path||'绑定策略')}</h2>
      ${v.code_sha?`<span class="lvtag on">@${esc(v.code_sha)}</span>
        <span class="lvtag">主文件 ${esc((o.main_sha256||'').slice(0,8))}</span>
        <span class="lvtag">${esc(v.ts||'')}</span>`
        :'<span class="lvtag">还没绑定 —— 填下面的路径与参数</span>'}
      <span style="flex:1"></span>
      <button class="btn" id="stclose">关闭</button>
    </div>
    <div class="lvgrid" style="${o._unbound?'display:none':''}">
      <div class="lvsec"><h3>绑定的参数</h3>
        <div class="lvpar">${Object.entries(v.params||{}).map(([k,x])=>
          `<span class="lvkv">${esc(k)}=${esc(x)}</span>`).join('')||'<span class="lvwhy">全部走默认值</span>'}</div>
        <h3 style="margin-top:10px">该版本声明的全部参数（${(o.params_declared||[]).length}）</h3>
        <table class="lvt"><tr><th>名</th><th>默认</th><th>说明</th></tr>
        ${(o.params_declared||[]).map(x=>`<tr><td>${esc(x.name)}</td><td>${esc(x.default)}</td>
          <td class="lvwhy">${esc(x.comment||'')}</td></tr>`).join('')}</table>
      </div>
      <div class="lvsec"><h3>用这个版本跑过的回测</h3>
        ${(same.length||other.length)?runTab(same,'同参数')+runTab(other,'其它参数')
          :`<div class="lvwarn" style="margin:0"><b>这个版本从没回测过。</b><br>
             账户绑的是磁盘当前内容，而归档里没有同哈希的记录 ——
             也就是说你实盘在跑的这套代码+参数，没有对应的历史业绩。</div>`}
        <div class="lvform">
          <label>起<input id="stbs" size="8" placeholder="YYYY-MM-DD" value="2019-01-01"></label>
          <label>止<input id="stbe" size="8" placeholder="默认今天"></label>
          <label>本金<input id="stbc" size="8" value="1000000"></label>
          <button class="btn${LV.can_backtest?' on':''}" id="stbt"
            >用这个版本+参数跑一次</button>
        </div>
        ${LV.can_backtest?'':`<div class="lvwarn" style="margin:6px 0 0">
          <b>网页触发回测没开</b> —— 这个服务是用
          <code>--readonly</code> 起的（只看不写）。<br>
          要用就重启：<code>python3 serve.py</code>（默认就是全功能）<br>
          不重启也能跑，命令行等价：
          <code id="stbcmd">python3 run.py ${esc(v.strategy_path||'')}${
            Object.entries(v.params||{}).map(([k,y])=>' --param '+esc(k)+'='+esc(y)).join('')}
            --start 2019-01-01 --cash 1000000</code>
          <a href="#" id="stbcp">复制</a></div>`}
        <div class="lvmsg" id="stbmsg"></div>
      </div>
    </div>
    <div class="lvsec" style="margin-top:12px"><h3>版本历史（append-only，删 runs/ 也读得到）</h3>
      ${/* 🔴 **参数与「为什么换」是两件事，各占一列。** 原来挤在同一格里，
             而那一格是 `.lvpar`（flex + justify-content:flex-end）—— 理由一长
             就把参数 chip 推到行首，于是**每行的起点都不一样**，整列纵向
             扫不下来（实测：第 1 行 chip 在最左、第 2 行在最右）。
             ★ 这几列全是文本，一律 `.tx` 左对齐 —— `table.lvt` 默认右对齐，
               不标的话 8 位 hash 与中文说明都靠右，看着像错位（同
               「数字列 .rt / 文本列 .tx」那条）。 */''}
      ${vs.length?`<table class="lvt lvvt"><tr><th class="tx">时间</th><th class="tx">版本</th
        ><th class="tx">参数</th><th class="tx">为什么换</th></tr>
        ${vs.map(x=>`<tr><td class="tx">${esc(x.ts.slice(0,10))}<br><span class="lvwhy">${esc(x.ts.slice(11,16))}</span></td>
          <td class="tx"><a class="lvver" href="#" data-sha="${esc(x.code_sha256)}"
                 title="${esc(x.strategy_path||'')}">${esc(x.code_sha)}</a>
              ${x.code_sha256===sha?'<br><span class="lvwhy">当前</span>':''}</td>
          <td class="tx lvpar">${Object.entries(x.params||{}).map(([k,y])=>
              `<span class="lvkv">${esc(k)}=${esc(y)}</span>`).join('')||'<span class="lvwhy">默认</span>'}</td>
          <td class="tx lvrsn">${x.reason?esc(x.reason):'<span class="lvwhy">—</span>'}</td></tr>`).join('')}</table>`
        :'<div class="none">还没绑定过</div>'}
      <div class="lvform">
        <input id="bp" placeholder="strategies/…/x.py" style="flex:1;min-width:200px"
               value="${esc(v.strategy_path||'')}">
        <input id="bj" placeholder='参数 {"a":1}' style="flex:1;min-width:140px"
               value="${esc(JSON.stringify(v.params||{}))}">
        <button class="btn" id="bb" ${ro?'disabled':''}>绑定当前磁盘版本</button>
      </div>
      <div class="lvmsg" id="bmsg"></div>
      <div class="lvwhy">换版本只是<b>追加一行</b>，账户与流水连续 ——
        「一段时间用策略 A、之后换 B」不需要新建账户。</div>
    </div>
    <div class="lvsec" style="margin-top:12px;${o._unbound?'display:none':''}">
      <h3>源码快照（${(o.files||[]).join(' + ')}）</h3>
      <pre class="stcode">${esc(o.code||'')}</pre></div>
  </div>`;
  $('#stclose').onclick=()=>{ el.className=''; el.innerHTML=''; document.onkeydown=null; };
  el.onclick=ev=>{ if(ev.target===el){ el.className=''; el.innerHTML=''; document.onkeydown=null; } };
  document.onkeydown=ev=>{ if(ev.key==='Escape') $('#stclose').click(); };
  el.querySelectorAll('a.lvver').forEach(e=>e.onclick=ev=>{
    ev.preventDefault(); openStrat(aid, e.dataset.sha); });
  if($('#stbcp')) $('#stbcp').onclick=ev=>{
    ev.preventDefault();
    const t=$('#stbcmd').textContent.replace(/\s+/g,' ').trim();
    navigator.clipboard.writeText(t).then(
      ()=>{ ev.target.textContent='已复制'; },
      ()=>{ ev.target.textContent='复制不了，手选吧'; });
  };
  const bb=$('#bb');
  if(bb) bb.onclick=async()=>{
    const m=$('#bmsg');
    let pr={}; try{ pr=JSON.parse($('#bj').value||'{}'); }
    catch(e){ m.className='lvmsg bad'; m.textContent='参数不是合法 JSON'; return; }
    try{ await post('/api/live/save',{id:aid, strategy_path:$('#bp').value.trim(),
          params:pr, reason:'网页绑定'});
      $('#stclose').click(); showLive(aid);
    }catch(e){ m.className='lvmsg bad'; m.textContent=String(e); }
  };
  /* 🔴 按钮【不设 disabled】—— disabled 的元素在浏览器里连 title 提示都不
     触发，于是"点了没反应而且看不到原因"，这是最难查的那种坏
     （同 backLink 那条：给一个点了没反应的按钮比不给更糟）。
     改成永远可点，点了就把原因说清楚。 */
  $('#stbt').onclick=async()=>{
    const m=$('#stbmsg');
    if(!LV.can_backtest){
      m.className='lvmsg bad';
      m.innerHTML='网页触发回测没开（这个服务是 <code>--readonly</code> 起的）'
        +' —— 重启成 <code>python3 serve.py</code>，或用上面那条命令行。';
      return;
    }
    m.className='lvmsg'; m.textContent='已提交…';
    try{
      /* ★ 不走 post()：它遇到 error 直接 throw，会丢掉 `drift` 那个结构化字段，
         而前端正是靠它渲染「重新绑定」按钮。只拿到一句话的话，
         人看到的是"点了报错"，仍然不知道该干什么。 */
      const rs=await fetch('/api/live/backtest',{method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({id:aid, sha:sha, start:$('#stbs').value.trim(),
          end:$('#stbe').value.trim(), cash:$('#stbc').value.trim()})});
      const r=await rs.json();
      if(r.error){
        m.className='lvmsg bad';
        if(r.drift){
          const d=r.drift;
          const cmd='python3 run.py '+d.path
            +Object.entries(d.params||{}).map(([k,y])=>' --param '+k+'='+y).join('')
            +' --start '+($('#stbs').value.trim()||'2019-01-01')
            +($('#stbe').value.trim()?' --end '+$('#stbe').value.trim():'')
            +' --cash '+($('#stbc').value.trim()||'1000000');
          m.innerHTML=esc(r.error)
            +'<br>两条出路：<button class="btn" id="stbrb">重新绑定到磁盘当前版本</button>'
            +' 或用命令行 <code id="stbcmd2">'+esc(cmd)+'</code>'
            +' <a href="#" id="stbcp2">复制</a>';
          $('#stbrb').onclick=async()=>{
            if(!confirm('把账户绑到磁盘当前版本？\n\n会往 versions.jsonl 追加一行'
              +'（append-only，旧版本仍然读得到）。\n'
              +'实盘信号从此按新代码算。')) return;
            try{
              await post('/api/live/save',{id:aid, strategy_path:d.path,
                params:d.params, reason:'网页：跑回测前重新绑定'});
              $('#stclose').click(); showLive(aid);
            }catch(e2){ m.textContent=String(e2); }
          };
          $('#stbcp2').onclick=ev2=>{ ev2.preventDefault();
            navigator.clipboard.writeText($('#stbcmd2').textContent)
              .then(()=>{ ev2.target.textContent='已复制'; },
                    ()=>{ ev2.target.textContent='复制不了'; }); };
        } else { m.textContent=r.error; }
        return;
      }
      m.textContent='跑中：'+r.cmd;
      const tick=async()=>{
        const o2=await j('/api/job?id='+r.job_id);
        m.textContent=(o2.lines||[]).slice(-3).join(' | ');
        if(o2.state==='running'){ setTimeout(tick,2000); return; }
        m.className='lvmsg '+(o2.rc===0?'ok':'bad');
        m.innerHTML=o2.rc===0
          ?`完成 · <a href="#/run/${encodeURIComponent(o2.run_id||'')}">查看结果 →</a>`
          :('失败 rc='+o2.rc);
        /* 跑完刷新一下浮层（新归档要出现在"用这个版本跑过的回测"里）。
           ★ 但先确认浮层【还开着】—— 用户可能已经关掉去做别的了，
             而一个自己弹回来的浮层比不刷新更烦（同"手动展开的状态不持久化"
             那条：不要替用户决定他现在该看什么）。 */
        if(o2.rc===0) setTimeout(()=>{
          if(document.querySelector('#stwrap .stbox')) openStrat(aid,sha);
        }, 1500);
      };
      tick();
    }catch(e){ m.className='lvmsg bad'; m.textContent=String(e); }
  };
}

/* ★ 数据日期【单独一个位置】，不塞在"（用 xx 收盘数据算）"的括号里。
   它是判断"信号新不新"的第一依据，而括号里的东西看着像脚注。

   ★ 光放一个日期不够 —— 得能一眼看出**落后没有**，否则单独放也白放。
     判据交给服务端（data_day = 面板 MAX(date)），前端只比对信号用的
     data_asof：两者不等说明信号是用更早的数据算的（该重算了）。
     🔴 不在前端判"落后几个交易日" —— 那要交易日历，而前端硬编码过
     一次判据就误报过一整页假告警。 */
