/* ============ 数据：同步 + 字典 ============ */
/* ---- 自动同步开关 ---------------------------------------------------
   "自动同步"就是那个 launchd agent，没有第二套定时（serve.py 里刻意不做
   定时线程：daily_snapshot 漏一天永久丢失，不能挂在"看板恰好开着"上）。
   ★ 按钮文字照【服务端复查到的状态】显示 —— 不是点一下就假设成功。
     launchctl 对"已经是这个状态"会报错退出，所以判据是"现在到底开着没"。 */
function autoTag(au){
  if(!au) return '<span class="lvtag">自动同步 —</span>';
  if(au.error) return `<span class="lvtag warn" title="${esc(au.error)}">自动同步 查不了</span>`;
  if(au.supported===false) return `<span class="lvtag" title="${esc(au.note||'')}">自动同步 不支持</span>`;
  /* 🔴 **只说"开"是不够的，要说"下一次什么时候"** —— 用户问的正是
     「下一次准备扫描的时间」。而口径写全（窗口 + 间隔），不是原来那个
     只抠第一个点位的「每日 16:00」——25 个点位只报一个是在说谎。 */
  const nx = autoNext(au);
  return `<span class="lvtag ${au.on?'on':(au.partial?'warn':'warn')}"
    title="${esc(autoTitle(au))}"
    >自动同步 ${au.on?'开':(au.partial?'半开':'关')}${nx?' · 下次 '+esc(nx):''}</span>`;
}
/* 两条任务里**最近**的那一次 —— 页面上问的是"下一次什么时候动"，
   而不是"sync 那条什么时候动"。都没开就不给（给了是在骗人）。 */
function autoNext(au){
  const ts=(au.tasks||[]).filter(t=>t.on&&t.next_at);
  if(!ts.length) return '';
  ts.sort((a,b)=>(a.next_in||0)-(b.next_in||0));
  const t=ts[0], hm=String(t.next_at).slice(11);
  const mins=Math.round((t.next_in||0)/60);
  return hm+(mins>=0?'（'+(mins<60?mins+' 分钟后':Math.round(mins/60)+' 小时后')+'）':'');
}
function autoTitle(au){
  return (au.tasks||[]).map(t=>`${t.name}：${t.on?'开':'关'} · ${t.window_text||'?'}`
    +(t.next_at?` · 下次 ${t.next_at}`:'')
    +(t.match===false?'　⚠️ 装上的点位与配置不一致':'')).join('\n')
    ||esc(au.label||'');
}
function autoBtn(au, ro){
  if(!au||au.supported===false||au.error) return '';
  /* 按钮**一律不设 disabled**（项目纪律：disabled 的元素连 title 都不触发）
     —— 只读模式下点了会把原因说清楚。 */
  return `<button class="btn" id="syauto">${au.on?'关闭自动同步':'开启自动同步'}</button>`;
}
/* 定时窗口。🔴 **必须显示"实际装上的点位"**，不能只回显配置 ——
   配置改了而 timer 没重装时，"页面写着每小时一次、实际还是旧的"不报错。
   判据是服务端从已装 plist 里读出来的 got_slots / match。 */
function schedBlock(sc, ro){
  if(!sc) return '';
  if(sc.error) return `<div class="lvwarn">定时窗口读不到：${esc(sc.error)}</div>`;
  const L=sc.limits||{}, ins=sc.installed||{};
  const row=(k,name,why)=>{
    const d=(sc.schedule||{})[k]||{}, i=ins[k]||{};
    const bad = i.match===false;
    return `<tr class="${bad?'sybad':''}">
      <td class="tx"><b>${name}</b><div class="lvwhy">${why}</div></td>
      <td><input class="syf" data-k="${k}" data-f="from" value="${esc(d.from||'')}"
            size="5" ${ro?'disabled':''}></td>
      <td>${i.wrap?'<span class="lvwhy">次日</span> ':''}<input class="syf"
            data-k="${k}" data-f="to" value="${esc(d.to||'')}"
            size="5" ${ro?'disabled':''}></td>
      <td><input class="syf" data-k="${k}" data-f="every" value="${d.every||''}"
            size="4" ${ro?'disabled':''}> 分</td>
      <td class="rt">${i.want_slots==null?'—':i.want_slots}</td>
      <td class="rt">${i.got_slots==null?'<span class="lvwhy">未装</span>':i.got_slots}</td>
      <td>${i.match===true?'<span class="ok">✓ 一致</span>'
            :i.match===false?'<b class="bad">🔴 不一致</b>'
            :'<span class="lvwhy">—</span>'}</td></tr>`;
  };
  const drift=Object.keys(ins).filter(k=>ins[k].match===false);
  return `<div class="lvsec"><h3>定时窗口
      <span class="lvwhy">判据是「齐没齐」而不是「到点没到点」——
        窗口内每隔一段问一次，齐了就秒退</span></h3>
    ${Object.keys(ins).some(k=>ins[k].wrap)?`<div class="hint">
      标「次日」的是**跨午夜**窗口（16:00 一直开到次日 09:20）——
      launchd 只认「几点几分」、每天都触发，所以跨天对它不是特例。
      非交易日那些点位由 tick_daily.py 的判据①拦掉（不是交易日就什么都不做）。
      </div>`:''}
    ${drift.length?`<div class="lvwarn"><b>🔴 配置与实际装上的 timer 不一致</b>
      （${esc(drift.join('、'))}）—— <b>定时跑的还是旧窗口</b>。
      点「保存并重装」让它生效。</div>`:''}
    <table class="lvt">
      <tr><th>任务</th><th>从</th><th>到</th><th>间隔</th>
          <th>点位</th><th>已装</th><th></th></tr>
      ${row('sync','数据同步','抓当天行情/复权；判据见 build/is_stale.py 四条')}
      ${row('tick','信号重算','出当天调仓清单；数据指纹没变就跳过')}
    </table>
    <div class="lvwhy">间隔 ${L.every_min||5}~${L.every_max||240} 分钟，
      点位上限 ${L.max_slots||100} 个。间隔除不尽时会**补上终点**
      （07:00~09:20 每 60 分钟 → 07:00 08:00 09:00 <b>09:20</b>），
      否则窗口末尾那段等于没覆盖。
      ${sc.exists?'':'　当前用的是默认值（还没存过配置）'}
      ${sc.warn?'<br>⚠ '+esc(sc.warn):''}</div>
    <div style="margin-top:8px">
      <button class="btn" id="sysched" ${ro?'disabled':''}>保存并重装</button>
      <span class="lvmsg" id="syschedmsg"></span></div></div>`;
}
function autoNote(au){
  if(!au) return '';
  let w='';
  /* 关着的时候必须说清代价 —— PIT 快照漏一天是【永久】丢失，
     而"关了自动同步"这件事本身不会报错，只会在几个月后发现历史缺口。 */
  if(au.supported!==false && !au.on)
    w+=`<div class="lvwarn"><b>自动同步是关的</b> —— 每天要自己点「立即同步」。<br>
      ⚠ <code>daily_snapshot.py</code> 是<b>漏一天永久丢失</b>的（tdx 的名称/
      分类/板块成分是 type-1 覆盖写，当天状态错过就再也重建不出来）。
      忘一天就是历史上少一天，而这不会报错。</div>`;
  if(au.drift)
    w+=`<div class="lvwarn"><b>已安装的 plist 与仓库正本不一致</b> ——
      正在生效的是 <code>${esc(au.plist)}</code> 那份（计划 ${esc(au.schedule||'?')}）。
      改了仓库里的 plist 但没重新安装，跑的还是旧的，<b>而这不会报错</b>。
      点「关闭」再「开启」会用正本覆盖。</div>`;
  if(au.installed && au.loaded===false && au.supported!==false)
    w+=`<div class="lvwarn">plist 已安装但没 load —— 点「开启自动同步」。</div>`;
  return w;
}


/* ============ 数据同步 ============ */
/* 页面不判断新鲜度 —— 判据在 datalake/build/sync_status.py，
   服务端调它。写两遍必然漂移（脚本说没问题、页面说落后 3 天）。 */
let SY=null, SYJOB=null;

let SU=null;

/* ============ 数据装配：从零把本地数据建起来 ============ */
/* 用户："没有数据也要能启动 server，然后点击数据加载开始同步数据，
   需要兼容 Windows 和 Mac。"

   🔴 **阶段清单、当前状态、要跑什么命令，全由服务端给**（/api/setup ->
     datalake/setup_stages.py）。这一段里不许出现任何一个阶段名或命令 ——
     写死的话加一个阶段页面上不会出现，**而那不报错**
     （同「加一个指标，广场上自动就有」）。
   🔴 **判据是"现在磁盘上是什么"，不是"跑过没有"** —— 记一个 done 标记
     会在「文件被删了 / 手工跑过 / 换了台机器」时说谎（同 launchd 那条）。 */
function setupBlock(su){
  if(!su || su.error) return su&&su.error
    ? '<div class="lvwarn"><b>装配状态读不出来</b><br>'+esc(su.error)+'</div>' : '';
  const ico={ok:'\u2705', todo:'\u2b1c', manual:'\u270b'};
  const rows=(su.stages||[]).map(s=>{
    const can = s.state==='todo' && s.cmd && su.can_run;
    return '<tr><td class="tx">'+ico[s.state]+' '+esc(s.name)+
      '<div class="cd">'+esc(s.why||'')+'</div></td>'+
      '<td class="tx">'+esc(s.detail||'')+'</td>'+
      '<td class="tx">'+esc(s.eta||'')+'</td>'+
      '<td class="tx">'+(can
        ? '<button class="btn susbtn" data-sid="'+esc(s.id)+'">\u25b7 开始</button>'
        : (s.state==='ok'?'<span class="cd">已完成</span>'
          :(s.state==='manual'?'<span class="cd">人工</span>'
            :'<span class="cd">'+(su.can_run?'等上一步':esc(su.why||'只读'))+'</span>')))+
      '</td></tr>';
  }).join('');
  const tbl='<div class="pw"><table class="lvt">'+
    '<tr><th class="tx">阶段</th><th class="tx">现在的状态</th>'+
    '<th class="tx">大概多久</th><th class="tx"></th></tr>'+rows+'</table></div>';
  /* 全齐时【不占半屏】，但也不许藏没了 —— 一行摘要 + 可展开
     （同「常驻一条『一切正常』的横幅等于教人忽略这个位置」）。 */
  /* 🔴 收起态**不标 `.lvsec`** —— 它是"一行摘要"不是一个分区，而分区
     在这一页里是「有 h3 的那种」。标成 lvsec 会让遍历 `.lvsec` 找 h3 的
     代码在它身上空等（实测：既有那条页面用例裸超时 30 秒）。 */
  if(su.ready) return '<details style="margin:0 0 10px">'+
    '<summary class="lvwhy" style="cursor:pointer">\u2705 数据装配 '+
    su.stages.length+' 个阶段都齐了（本机 '+esc(su.os||'')+'）—— 展开看每一项</summary>'+
    tbl+'</details>';
  /* 🔴 **首次加载给一个按钮，一次跑到底** —— 用户：「首次加载，应该是
     系统启动后展示一个数据初始化的按钮，然后点击按钮开始加载数据」。
     改之前是七个按钮、要人自己点六次、还得自己判断上一步跑完没有
     （同「靠人记得跑的步骤 = 迟早不跑」）。逐阶段那几个按钮**留着**：
     某一步失败时要能单独重来（同「硬拒必须配一个逃生口」）。 */
  const one = su.running
    ? '<span class="cd">正在跑：'+esc(su.running.stage||'')+'　'+
      '（跑完会自动接着下一步）</span>'
    : (su.can_run
       ? '<button class="btn" id="suall">\u25b7 开始建本地数据</button>'+
         '<span class="cd" style="margin-left:8px">一次跑完剩下的 '+
         su.n_auto_todo+' 步'+(su.eta_text?'，约需 '+esc(su.eta_text)+'（估）':'')+
         '；中途失败会停下并说清是哪一步</span>'
       : '<span class="cd">'+esc(su.why||'只读模式，不能跑')+'</span>');
  return '<div class="lvwarn"><b>\u8fd8\u5dee '+su.n_todo+' \u6b65\u624d\u80fd\u7528</b><br>'+
    '本机 '+esc(su.os||'')+'。<b>首次</b>点下面那个按钮一次建完；'+
    '之后每天的增量由「自动同步」跑，不用再点。'+
    '每一步都可以中断，再点一次从没完成的地方接着跑（都是幂等的）。'+
    '<div style="margin-top:8px">'+one+'</div>'+
    '</div><div class="lvsec" style="margin-bottom:10px">'+
    '<h3>\u6570\u636e\u88c5\u914d</h3>'+tbl+'</div>';
}

function setupWire(){
  const all=$('#suall');
  if(all) all.onclick=async()=>{
    all.textContent='\u542f\u52a8\u4e2d\u2026';
    const m=$('#symsg');
    try{
      const o=await post('/api/setup/run',{stage:'__all__'});
      if(o.error){ m.className='lvmsg bad'; m.textContent=o.error;
                   all.textContent='\u25b7 \u5f00\u59cb\u5efa\u672c\u5730\u6570\u636e'; return; }
      m.className='lvmsg';
      m.textContent='已开始，还要跑 '+(o.todo||[]).length+' 步 —— '+
        '可以关掉这一页，顶上那条横条在【任何页面】都看得到进度。';
      SYJOB=o.job_id; pollSync();
    }catch(e){ m.className='lvmsg bad'; m.textContent='启动失败：'+esc(e);
               all.textContent='\u25b7 \u5f00\u59cb\u5efa\u672c\u5730\u6570\u636e'; }
  };
  document.querySelectorAll('.susbtn').forEach(b=>{
    b.onclick=async()=>{
      const sid=b.dataset.sid;
      b.disabled=true; b.textContent='\u542f\u52a8\u4e2d\u2026';
      const m=$('#symsg');
      try{
        const o=await post('/api/setup/run',{stage:sid});
        if(o.error){ m.className='lvmsg bad'; m.textContent=o.error;
                     b.disabled=false; b.textContent='\u25b7 \u5f00\u59cb'; return; }
        m.className='lvmsg'; m.textContent='已开始：'+o.stage+'　'+o.cmd;
        SYJOB=o.job_id; pollSync();
      }catch(e){ m.className='lvmsg bad'; m.textContent='启动失败：'+esc(e);
                 b.disabled=false; b.textContent='\u25b7 \u5f00\u59cb'; }
    };
  });
}


async function showSync(){
  stopPoll();
  enterView();
  let o, su=null;
  /* 🔴 两发**并发**，而且装配那发【失败也不许挡住整页】—— 空 lake 上
     `/api/sync` 很可能是它自己先报错，那时更需要看得到装配清单。 */
  try{
    const r = await Promise.all([j('/api/sync'), j('/api/setup').catch(e=>({error:String(e)}))]);
    o = r[0]; su = r[1];
  }catch(e){
    try{ su = await j('/api/setup'); }catch(_){}
    $('#main').innerHTML = (su?setupBlock(su):'') +
      '<div class="none">数据同步状态读取失败：'+esc(e)+'</div>';
    setupWire(); return;
  }
  SY=o; SU=su;
  const st=o.status||{}, it=st.items||[], cal=st.calendar||{};
  const A=it.filter(x=>x.leg==='A'), B=it.filter(x=>x.leg==='B');
  let warn='';
  if(o.error) warn+=`<div class="lvwarn"><b>状态读取失败</b><br>${esc(o.error)}</div>`;
  if(st.leg_a_lag) warn+=`<div class="lvwarn"><b>行情数据落后 ${st.leg_a_lag} 个交易日</b><br>
     同步没跑成功。点下面「立即同步」，或看最近一次日志。</div>`;
  /* 🔴 **财务数据的告警判据是「上次抽取」，不是「数据内容多久没变」。**
     B 腿事件驱动：没公告的日子 pub_date 本来就不前进。只看内容的话，
     人昨天刚导完、页面照样标红「距今 18 天没更新」 —— 用户原话：
     「显得我好像没有更新一下」。假告警看多了就不看告警。
     ★ 老包没有 _manifest.json（ex 为 null）时**退回**看内容 ——
       不然旧格式包一律不告警，反而把真的过期藏起来。 */
  const ex = st.extract, exd = ex && ex.days_since_extract;
  const bStale = (exd!=null) ? exd>14 : (st.leg_b_days_since>21);
  if(bStale) warn+=`<div class="lvwarn"><b>财务数据${exd!=null
       ?'已经 '+exd+' 天没从聚宽抽取'
       :'距今 '+st.leg_b_days_since+' 天没更新'}</b><br>
     行情天天新、财务过期时，回测照跑、报告看着完全正常，而选股用的是旧财报
     （数据字典 E-0「数据新鲜度错配」）。实测代价：2026 中报只覆盖 55.6% 时，
     froec 同日选股与聚宽只对上 6/10；补完升到 9/10。<br>
     去聚宽研究环境跑 <code>raw/jq/_ingest/extract_jq_increment.py</code>。</div>`;

  if(o.readonly) warn+=`<div class="lvwarn">只读模式 —— 可以看，不能手动触发。
     这个服务是 <code>--readonly</code> 起的 —— 去掉它重启即可。</div>`;

  /* 内部术语叫 A 腿 / B 腿（sync_status.py 的字段名就是 leg_a / leg_b），
     但【界面上不用】—— "腿"对着屏幕看的人没有意义。界面写
     「行情数据 · 全自动」/「财务数据 · 需手动导出」。
     A 腿看"落后几个交易日"（每个交易日必然有新行情，缺了就是没同步）；
     B 腿只看"距今几天" —— 财务是事件驱动，没公告的日子本来就没有新
     pub_date，按交易日算落后是必然误报。判据在服务端，这里只负责显示。 */
  const row=x=>{
    let tag;
    if(x.leg==='A' || x.error){
      tag=lagSpan(x, '个交易日');
    } else {
      const ds=x.days_since;
      tag = ds==null ? '—'
          : (ds>21 ? `<span style="color:var(--up)">距今 ${ds} 天</span>`
                   : `<span style="color:var(--dim)">距今 ${ds} 天</span>`);
    }
    return `<tr><td>${esc(x.name)}</td><td>${esc(x.max||'—')}</td><td>${tag}</td></tr>`;
  };
  $('#main').innerHTML=warn+`
  ${dataTabs('sync')}
  ${setupBlock(SU)}
  <div class="lvhead">
    <h2>数据同步</h2>
    <span class="lvtag">应到交易日 ${esc(st.expect_trade_day||'?')}</span>
    <span class="lvtag${cal.authoritative?' on':''}">
      日历 ${esc(cal.source||'缺')} → ${esc(cal.max||'?')}</span>
    ${autoTag(o.auto)}
    <span style="flex:1"></span>
    <button class="btn" id="syrun" ${o.readonly?'disabled':''}>立即同步</button>
    ${autoBtn(o.auto, o.readonly)}
    <button class="btn" id="syref">刷新</button>
  </div>
  <div class="lvmsg" id="symsg"></div>
  ${autoNote(o.auto)}
  ${schedBlock(o.sched, o.readonly)}
  <div class="lvgrid">
    <div class="lvsec"><h3>行情数据 · 全自动</h3>
      <table class="lvt"><tr><th>项</th><th>最新</th><th>状态</th></tr>
      ${A.map(row).join('')}</table>
      <div class="lvwhy" style="margin-top:6px">
        由 <code>datalake/sync_daily.py</code> 一条链串起：抓日线 →
        <b>PIT 快照（漏一天永久丢失）</b> → 规范层 → 面板 → 因子。
        跑完直接触发实盘出信号 —— 依赖写进调用顺序，不靠两个时间常量隔开。
        <b>步数不写在这里</b>：链条长度只在那个文件里定义一处，
        抄一份到页面上迟早对不上，而那不报错。
      </div>
    </div>
    <div class="lvsec"><h3>财务数据 · 需手动导出</h3>
      ${(()=>{
        /* ★ 这一行回答的是「**我上次导是什么时候**」，下面那张表回答的是
           「数据内容切到哪天」—— 两件事，摆一起才不矛盾。原来页面上只有
           后者，于是刚导完也显示「距今 18 天」。
           ★ 抽取时刻由**抽取端**写进包（聚宽研究环境的 now），本地反推不出来
             —— 文件 mtime 是下载/解压时刻，不是抽取时刻。 */
        if(!ex) return `<div class="lvwhy">上次抽取 <b>未知</b> ——
          最近导入的包是旧格式（没有 <code>_manifest.json</code>）。
          下次用「① 取聚宽代码」导出的包会带上抽取时刻。</div>`;
        const d = ex.days_since_extract;
        const age = d==null?'':(d===0?'今天':(d===1?'昨天':d+' 天前'));
        return `<div class="lvtags" style="margin:2px 0 6px">
          <span class="lvtag${d!=null&&d>14?' warn':''}">上次抽取
            ${esc(ex.extracted_at||ex.extract_date||'未知')}${age?' · '+age:''}</span>
          ${ex.data_max_date?`<span class="lvtag">数据切到 pub_date
            ${esc(ex.data_max_date)}</span>`:''}
          ${ex.since?`<span class="lvtag">SINCE ${esc(ex.since)}</span>`:''}
          ${ex.merged_at?`<span class="lvtag" title="${esc(ex.tar||'')}">本地合并
            ${esc(ex.merged_at)}</span>`:''}
        </div>${ex.recovered?`<div class="lvwhy">${esc(ex.recovered)}</div>`:''}`;
      })()}
      <table class="lvt"><tr><th>项</th><th>最新</th><th>距今</th></tr>
      ${B.map(row).join('')}</table>
      <div class="lvwhy" style="margin-top:6px">
        聚宽研究环境没有本地 API，只能人工导出。财务数据的"落后"<b>不按交易日算</b>
        —— 它是<b>事件驱动</b>的，没公告的日子本来就不该有新 pub_date；
        按交易日算会天天标红，然后你就不看红字了。
        ${st.leg_b_days_since!=null?'距今 '+st.leg_b_days_since+' 天。':''}
      </div>
      <div class="lvform" style="margin-top:8px;gap:8px">
        <button class="btn" id="jqcode">① 取聚宽代码</button>
        <label class="btn" style="cursor:pointer">② 上传导出的包
          <input type="file" id="jqfile" accept=".tar,.tar.gz,.tgz"
                 style="display:none" ${o.readonly?'disabled':''}></label>
      </div>
      <div class="lvwhy" style="margin-top:4px">
        ① 复制代码 → 粘进聚宽研究环境一个 cell 跑完 → 它打包成<b>一个 tar</b>
        → ② 上传，服务端自动 merge 并把该跑的 loader <b>全跑一遍</b>
        （原来这步要照着打印出来的命令手抄 5~8 条，<b>抄漏一条就是 raw 与 std
        不一致，而那不会报错</b>）。
      </div>
      <div class="lvmsg" id="jqmsg"></div>
    </div>
    <div class="lvsec"><h3>最近同步日志</h3>
      ${(o.logs||[]).length?`<table class="lvt"><tr><th>时间</th><th>大小</th></tr>
        ${o.logs.map(l=>`<tr><td><a href="#" class="sylog" data-n="${esc(l.name)}">${esc(l.name)}</a></td>
          <td>${(l.bytes/1024).toFixed(1)}K</td></tr>`).join('')}</table>`
        :'<div class="none">还没有日志</div>'}
    </div>
  </div>
  <pre id="syout" style="display:none;background:var(--panel2);border:1px solid var(--line);
    border-radius:6px;padding:12px;overflow:auto;max-height:60vh;font:11px/1.6 'SF Mono',Menlo,monospace"></pre>`;

  $('#syref').onclick=()=>showSync();
  setupWire();   /* 装配那几个按钮与主渲染同一条路 */
  if($('#syauto')) $('#syauto').onclick=async()=>{
    const on=!o.auto.on, m=$('#symsg');
    /* 关闭要确认：它的代价是"几个月后发现历史有缺口"，而不是立刻报错。 */
    if(!on && !confirm('关闭自动同步？\n\n之后每天要自己点「立即同步」。'
        +'daily_snapshot 是漏一天永久丢失的（tdx 的名称/分类/板块成分是'
        +'覆盖写），忘一天就是历史上少一天，而这不会报错。')) return;
    m.className='lvmsg'; m.textContent=(on?'开启':'关闭')+'中…';
    try{
      const r=await post('/api/sync/auto',{on:on});
      m.className='lvmsg ok';
      /* 🔴 照**复查到的**状态说，而且把两条任务与下次时间都说出来 ——
         原来那句「每日 16:00」是从第一个点位抠的，25 个点位只报一个。 */
      m.className='lvmsg '+(r.error?'bad':'ok');
      m.textContent=r.error?String(r.error)
        :(r.on?'自动同步已开启 —— '+(r.tasks||[]).map(t=>t.name+' '+(t.window_text||'')).join('；')
               +(autoNext(r)?'，下次 '+autoNext(r):'')
             :'自动同步已关闭 —— 记得每天手动点「立即同步」');
      showSync();
    }catch(e){ m.className='lvmsg bad'; m.textContent=String(e); }
  };
  if($('#sysched')) $('#sysched').onclick=async()=>{
    const m=$('#syschedmsg'), sc={};
    $$('.syf').forEach(x=>{
      sc[x.dataset.k]=sc[x.dataset.k]||{};
      sc[x.dataset.k][x.dataset.f]=x.dataset.f==='every'
        ? parseInt(x.value,10) : x.value.trim();
    });
    m.className='lvmsg'; m.textContent='保存并重装…';
    try{
      const r=await post('/api/sync/schedule',{schedule:sc});
      if(r.error){ m.className='lvmsg bad'; m.textContent=r.error; }
      else{
        const ins=r.installed||{};
        m.className='lvmsg ok';
        m.textContent='已生效：'+Object.keys(ins).map(k=>
          k+' '+ins[k].first+'~'+(ins[k].wrap?'次日 ':'')+ins[k].last
          +' 共 '+ins[k].got_slots+' 个点位'
        ).join('；');
      }
      /* ★ 延迟刷新：立刻 showSync() 会把这条反馈连同 #syschedmsg 一起
         重渲染掉，点了按钮什么都看不到（实测就是这样）。
         等两秒让人看到"装上了几个点位"，再刷新去掉那条不一致警告。 */
      setTimeout(showSync, 2000);
    }catch(e){ m.className='lvmsg bad'; m.textContent=String(e); }
  };
  $('#syrun').onclick=async()=>{
    const m=$('#symsg'); m.className='lvmsg'; m.textContent='已启动…（tdx2db cron 要几分钟）';
    try{
      const r=await post('/api/sync/run',{});
      SYJOB=r.job_id; pollSync();
    }catch(e){ m.className='lvmsg bad'; m.textContent=String(e); }
  };
  /* ① 取聚宽代码：正本是磁盘上那个 extract 脚本，服务端只把 SINCE /
     QUARTERS 按本地状态替换掉 —— 前端【不另存一份】，两份一定会分叉。 */
  if($('#jqcode')) $('#jqcode').onclick=async()=>{
    const m=$('#jqmsg'); m.className='lvmsg'; m.textContent='取代码中…';
    try{
      const r=await j('/api/sync/jq_code');
      if(r.error) throw new Error(r.error);
      const el=$('#syout'); el.style.display=''; el.textContent=r.code;
      el.scrollTop=0;
      const sg=r.suggest||{};
      m.className='lvmsg '+(r.warn?'bad':'ok');
      m.innerHTML=(r.warn?'⚠ '+esc(r.warn)+'<br>':'')
        +'代码已显示在下面，<b>整段复制</b>粘进聚宽研究环境一个 cell 跑。'
        +(r.substituted&&r.substituted.length
           ? '<br>已按本地状态填好：<code>'+esc(r.substituted.join('　'))+'</code>'
             +(sg.oldest?'　（本地最落后的表到 '+esc(sg.oldest)
               +'，SINCE 往前留了几天重叠 —— <b>重叠不会重复，缺口会静默丢数据</b>）':'')
           : '')
        +(sg.note?'<br>'+esc(sg.note):'')
        +'　<a href="#" id="jqcopy">复制到剪贴板</a>';
      const cp=$('#jqcopy');
      if(cp) cp.onclick=async ev=>{ ev.preventDefault();
        try{ await navigator.clipboard.writeText(r.code);
             cp.textContent='已复制 ✓'; }
        catch(e){ cp.textContent='复制失败，请手动全选下面那段'; } };
    }catch(e){ m.className='lvmsg bad'; m.textContent=String(e); }
  };
  /* ② 上传：原始字节直传（几十 MB 不该走 base64）。上传完服务端就
     merge + 跑全部 loader，日志实时滚在下面。 */
  if($('#jqfile')) $('#jqfile').onchange=async ev=>{
    const f=ev.target.files&&ev.target.files[0]; if(!f) return;
    const m=$('#jqmsg'); m.className='lvmsg';
    m.textContent='上传 '+f.name+'（'+(f.size/1e6).toFixed(1)+' MB）…';
    try{
      const res=await fetch('/api/sync/jq_upload',{method:'POST',
        headers:{'X-Filename':f.name,'Content-Type':'application/octet-stream'},
        body:f});
      const r=await res.json();
      if(r.error) throw new Error(r.error);
      m.className='lvmsg ok';
      m.innerHTML='已收到 '+esc(f.name)+'，包内 <b>'+(r.members||[]).length
        +'</b> 个文件：<code>'+esc((r.members||[]).join(' '))+'</code><br>'
        +'正在 merge 并跑 loader —— 日志在下面滚动，<b>别关页面</b>。';
      SYJOB=r.job_id; pollSync();
    }catch(e){ m.className='lvmsg bad'; m.textContent=String(e); }
    finally{ ev.target.value=''; }   /* 清掉，否则同一个文件选不了第二次 */
  };
  document.querySelectorAll('a.sylog').forEach(e=>e.onclick=async ev=>{
    ev.preventDefault();
    const r=await j('/api/sync/log?name='+encodeURIComponent(e.dataset.n));
    const el=$('#syout'); el.style.display=''; el.textContent=r.text;
    el.scrollTop=el.scrollHeight;
  });
}

async function pollSync(){
  if(!SYJOB) return;
  const el=$('#syout'); el.style.display='';
  try{
    const o=await j('/api/job?id='+SYJOB);
    el.textContent=(o.lines||[]).join('\n');
    el.scrollTop=el.scrollHeight;
    if(o.state==='running'){ setTimeout(pollSync, 2000); return; }
    const m=$('#symsg');
    m.className='lvmsg '+(o.rc===0?'ok':'bad');
    /* 同一个轮询同时服务"立即同步"和"上传财务数据" —— 文案要分开，
       否则财务导入完了会显示"信号已重算"，而那步压根没跑。 */
    const isJQ=String(SYJOB||'').indexOf('jq-')===0;
    m.textContent=o.rc===0
      ? (isJQ?'财务数据已导入（merge + loader + 面板 + beta 全跑完）'
             :'同步完成（信号已重算）')
      : ((isJQ?'导入失败':'同步失败')+'，rc='+o.rc
         +(isJQ?' —— 日志里会写明是哪一步，前面的步骤已生效':''));
    SYJOB=null;
    setTimeout(()=>showSync(), 1200);
  }catch(e){ SYJOB=null; }
}

/* ============ 数据字典 ============ */
/* 内容【不烤在前端】—— 服务端每次请求重读磁盘上的 md。
   改 md 刷新页面就生效，新增一份文档只需在 server.py 的 _DOCS 加一行。 */
let DOCS=null, DOCKEY=null, DOCQ='', DOCHTML={}, DOCALL=false;

/* 「数据」这一块的两个页签。★ 合并的理由：同步（新鲜度）与字典（口径）
   都是**数据的元信息**，而且都是低频 —— 出问题时或者写代码查口径时才进。
   顶栏平铺两个入口，等于让人每次在 9 个里多扫一个。
   ★ `#/docs/<key>` 仍然是直达路由，旧链接与书签不失效。 */
function dataTabs(cur){
  const T=[['sync','#/sync','数据状态 / 同步'],
           ['docs','#/docs','口径字典']];
  return `<div class="lvform" style="gap:6px;margin-bottom:10px">
    ${T.map(([k,h,t])=>`<a class="btn${k===cur?' on':''}" href="${h}">${t}</a>`).join('')}
    <span class="lvwhy">两件事都是【数据的元信息】：一个管"新不新"，
      一个管"口径是什么"。都是低频，所以并成一个入口。
      <!-- ★ 交叉引用而不是第三个页签：指标广场是**个股的子页**
           （顶栏点亮个股），做成这里的页签会出现"从数据页签点进去、
           顶栏却亮个股"的自相矛盾。 -->
      指标的口径在 <a href="/indicators.html">📊 指标广场</a>，
      因子的口径与公式在 <a href="/factors.html">🧪 因子广场</a>。</span>
  </div>`;
}

async function showDocs(key){
  stopPoll();
  enterView();
  if(!DOCS){
    try{ DOCS=await j('/api/docs'); }
    catch(e){ $('#main').innerHTML='<div class="none">数据字典读取失败：'+e+'</div>'; return; }
  }
  const avail=DOCS.docs.filter(d=>d.exists);
  DOCKEY = key || (avail[0]&&avail[0].key) || null;
  const groups=[];
  DOCS.docs.forEach(d=>{ let g=groups.find(x=>x.g===d.group);
    if(!g){ g={g:d.group, items:[]}; groups.push(g); } g.items.push(d); });
  $('#main').innerHTML=dataTabs('docs')+`<div id="dk">
    <div class="dside">
      <div class="dhint">直接读仓库里的 md —— <b>改文件刷新即生效</b>。<br>
        根目录 <code>${DOCS.root}</code></div>
      ${groups.map(g=>`<div class="dgrp">${g.g}</div>`+g.items.map(d=>
        `<a class="ditem${d.key===DOCKEY?' on':''}${d.exists?'':' miss'}"
            href="#/docs/${d.key}" ${d.exists?'':'title="文件不存在"'}>
           <span>${d.title}</span>
           <span class="dmeta" data-key="${d.key}"
                 data-size="${d.exists?(d.bytes/1024).toFixed(1)+'K':'缺'}"
           >${d.exists?(d.bytes/1024).toFixed(1)+'K':'缺'}</span></a>`).join('')).join('')}
    </div>
    <div class="dbody">
      <div class="dbar">
        <input id="dq" placeholder="在本篇内过滤：接口名 / 字段名 / 症状 …" autocomplete="off">
        <span class="dhits" id="dhits"></span>
      </div>
      <div id="dc" class="dmd"></div>
    </div></div>`;
  $('#dq').value=DOCQ;
  $('#dq').oninput=()=>{ DOCQ=$('#dq').value; dfilter(); };
  if(DOCKEY) loadDoc(DOCKEY);
}

async function loadDoc(key){
  const c=$('#dc'); if(!c) return;
  c.innerHTML='<div class="none">载入中…</div>';
  let r=DOCHTML[key];
  if(!r){
    try{ r=await j('/api/doc?key='+encodeURIComponent(key)); DOCHTML[key]=r; }
    catch(e){ c.innerHTML='<div class="none">读取失败：'+e+'</div>'; return; }
  }
  c.innerHTML=`<div class="dsrc">${r.rel}${r.mtime?' · 更新于 '+r.mtime:''}</div>`+r.html;
  dfilter();
}

/* 过滤当前这篇：命中的行留下并高亮，整表空掉就连带标题一起隐藏。
   —— 表格是这批文档的主体，按【行】过滤比按段落过滤有用得多。 */
function dfilter(){
  const c=$('#dc'); if(!c) return;
  const q=(DOCQ||'').trim().toLowerCase();
  const rows=[...c.querySelectorAll('table.dt tbody tr')];
  rows.forEach(tr=>{
    tr.querySelectorAll('td').forEach(td=>{
      if(td.dataset.raw!==undefined){ td.innerHTML=td.dataset.raw; delete td.dataset.raw; }
    });
  });
  let hit=0;
  rows.forEach(tr=>{
    const ok = !q || tr.textContent.toLowerCase().includes(q);
    tr.style.display = ok?'':'none';
    if(ok){ hit++; if(q) tr.querySelectorAll('td').forEach(td=>dmark(td,q)); }
  });
  // 空表 + 其后紧跟的标题一起收起
  c.querySelectorAll('.dtw').forEach(w=>{
    const n=[...w.querySelectorAll('tbody tr')].filter(t=>t.style.display!=='none').length;
    w.style.display = (q && n===0)?'none':'';
  });
  if(q){
    [...c.querySelectorAll('h3.dh, h4.dh')].forEach(h=>{
      let n=h.nextElementSibling, vis=false;
      while(n && !/^H[2-4]$/.test(n.tagName)){
        if(n.style.display!=='none' && n.textContent.trim()) vis=true;
        n=n.nextElementSibling;
      }
      h.style.display = vis?'':'none';
    });
    c.querySelectorAll('p, ul, ol, blockquote').forEach(el=>{
      el.style.display = el.textContent.toLowerCase().includes(q)?'':'none';
    });
  }else{
    c.querySelectorAll('h3.dh, h4.dh, p, ul, ol, blockquote').forEach(
      el=>{el.style.display='';});
  }
  const h=$('#dhits');
  if(h) h.textContent = rows.length ? (q?hit+' / '+rows.length+' 行':rows.length+' 行') : '';
  dcount(q);
}

/* 跨篇计数：过滤时在侧栏把每一篇的命中行数标出来 —— 否则你只能看到当前这篇，
   而「这个概念在哪一篇」恰恰是最常问的。首次过滤把全部文档拉一遍并缓存。 */
async function dcount(q){
  const metas=[...document.querySelectorAll('.dmeta[data-key]')];
  if(!q || q.length<2){
    metas.forEach(m=>{ m.textContent=m.dataset.size;
      m.parentElement.classList.remove('nohit'); });
    return;
  }
  if(!DOCALL){
    DOCALL=true;
    const need=(DOCS?DOCS.docs:[]).filter(d=>d.exists && !DOCHTML[d.key]);
    await Promise.all(need.map(async d=>{
      try{ DOCHTML[d.key]=await j('/api/doc?key='+encodeURIComponent(d.key)); }
      catch(e){ /* 单篇失败不拖累其它篇 */ }
    }));
  }
  const box=document.createElement('div');
  metas.forEach(m=>{
    const r=DOCHTML[m.dataset.key];
    if(!r||!r.html){ m.textContent=m.dataset.size; return; }
    box.innerHTML=r.html;
    const n=[...box.querySelectorAll('table.dt tbody tr')]
              .filter(tr=>tr.textContent.toLowerCase().includes(q)).length;
    m.textContent = n? n+' 命中' : '—';
    m.parentElement.classList.toggle('nohit', n===0);
  });
}

function dmark(td,q){
  const raw=td.innerHTML;
  const t=td.textContent, i=t.toLowerCase().indexOf(q);
  if(i<0) return;
  td.dataset.raw=raw;
  // 只在纯文本节点上高亮，避免破坏 <code>/<b>/<a> 结构
  const walk=n=>{
    if(n.nodeType===3){
      const s=n.nodeValue, k=s.toLowerCase().indexOf(q);
      if(k<0) return;
      const sp=document.createElement('span'); sp.className='dhi';
      const after=n.splitText(k); after.splitText(q.length);
      sp.textContent=after.nodeValue; after.parentNode.replaceChild(sp, after);
      return;
    }
    [...n.childNodes].forEach(walk);
  };
  [...td.childNodes].forEach(walk);
}
