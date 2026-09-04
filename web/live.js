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


/* ============ 主视图 ============
   信息架构：**天天要看的留在页面上，偶尔用的进浮层。**
     页面   今日待办 + 当前持仓（含盈亏汇总）
     浮层   ⚙设置 / ✎记一笔（成交+现金两个 tab）/ 策略（含版本历史）
     独立页 成交流水 #/live/<id>/fills —— 会越来越长，服务端分页
   ★ 策略只有【一个】入口（排头那个标签）。原来排头一个、下面又一块
     "策略版本"，是同一件事两处入口 —— 版本历史已并进浮层。 */
async function loadLive(aid){
  const b=$('#lvbody'); b.innerHTML='<div class="none">读取中…</div>';
  let o; try{ o=await j('/api/live/account?id='+encodeURIComponent(aid)); }
  catch(e){ b.innerHTML='<div class="none">'+esc(e)+'</div>'; return; }
  LVO=o;
  const a=o.account, sig=o.signal, ro=LV.readonly, P=o.pos||{};
  const it=P.items||[];
  const sgn=x=>x==null?'':(x>=0?'+':'');
  const col=x=>x==null?'':(x>=0?'var(--up)':'var(--down)');
  b.innerHTML=`
  <div class="lvhead">
    <h2>${esc(a.name)}</h2>
    ${dataDayTag(o)}
    ${a.code_sha256?`<a class="lvtag on" href="#" id="lvstrat"
        title="源码 / 参数 / 版本历史 / 用这个版本跑过的回测">${esc(a.strategy_path.split('/').pop())}
        @${esc(a.code_sha256.slice(0,8))} ›</a>`
      :'<a class="lvtag" href="#" id="lvstrat">未绑定策略 ›</a>'}
    <span style="flex:1"></span>
    <button class="btn" id="lvtick" ${ro?'disabled':''}>立即重算</button>
    <button class="btn" id="lvrec" ${ro?'disabled':''}>✎ 记一笔</button>
    <a class="btn" href="#/live/${a.id}/fills">流水${o.n_fills==null?'':' '+o.n_fills}</a>
    <button class="btn" id="lvset">⚙</button>
  </div>
  <div class="lvmsg" id="lvmsg"></div>
  ${kpiHtml(o)}
  ${sigHtml(sig, o.alert, o.alert_why)}
  <div class="lvsec"><h3>当前持仓
      ${it.length?`<span class="lvwhy">${it.length} 只</span>${posHelp(P)}`:''}</h3>
    ${it.length?`<table class="lvt lvpos">
      <tr><th>代码</th><th class="tx">名称</th><th class="rt">股数</th>
          <th class="rt">成本</th>
          <th class="rt">现价</th>
          <th class="rt">当日</th><th class="rt">当日盈亏</th>
          <th class="rt">市值</th>
          <th class="rt">浮盈</th><th class="rt">幅度</th><th class="rt">仓位</th>
          <th class="tx">建仓</th></tr>
      ${it.map(x=>`<tr>
        <td>${skLink(x.code, x.code)}</td>
        <td class="tx">${skLink(x.code, x.name||'')}</td>
        <td class="rt">${num(x.shares)}</td>
        <td class="rt" title="摊薄成本（含买入费 ${num(x.buy_fee,2)}）—— 浮盈按它算。&#10;成交均价 ${num(x.cost,4)}（引擎 entry_price 用的是这个，不含费）。&#10;保本价 ${x.breakeven==null?'—':num(x.breakeven,3)}（含估算卖出费 ${x.exit_fee_est==null?'—':num(x.exit_fee_est,2)}）">${num(x.cost_net,3)}</td>
        <td class="rt">${x.price==null?'—':num(x.price,2)}${x.stale?
            `<span class="lvwhy" title="当日无行情（停牌），按最后已知价 ${esc(x.px_date)} 挂账">停</span>`:''}${
          x.rt_src?`<span class="lvwhy" style="color:var(--accent)"
            title="盘中实时价（${esc(x.rt_at||'')}，来源 ${esc(x.rt_src)}）">实</span>`:''}</td>
        <td class="rt" style="color:${col(x.chg_day)}"
            title="相对昨收 ${x.preclose==null?'—':num(x.preclose,2)}">${
          x.chg_day==null?'—':sgn(x.chg_day)+(x.chg_day*100).toFixed(2)+'%'}</td>
        <td class="rt" style="color:${col(x.pnl_day)}"
            title="${x.entry===(P.asof||'').slice(0,10)?'今天建的仓 —— 基准是成交价，不是昨收':'基准是昨收 '+num(x.preclose,2)}">${
          x.pnl_day==null?'—':sgn(x.pnl_day)+num(x.pnl_day,2)}</td>
        <td class="rt">${x.value==null?'—':num(x.value,2)}</td>
        <td class="rt" style="color:${col(x.pnl)}">${x.pnl==null?'—':sgn(x.pnl)+num(x.pnl,2)}</td>
        <td class="rt" style="color:${col(x.pnl_pct)}">${x.pnl_pct==null?'—':sgn(x.pnl_pct)+(x.pnl_pct*100).toFixed(2)+'%'}</td>
        <td class="rt">${x.weight==null?'—':(x.weight*100).toFixed(1)+'%'}</td>
        <td class="lvwhy tx">${esc(x.entry)}</td></tr>`).join('')}
      </table>
      ${P.fee_estimated_n?'<div class="lvwhy" style="margin-top:6px">'
        +'⚠ 有 <b>'+P.fee_estimated_n+'</b> 笔买入的费用是<b>估算</b>的，'
        +'成本跟着也是估算 —— 对完账单可在流水页「冲正 + 重录」填实际值。'
        +'</div>':''}`
      :`<div class="none">空仓${o.n_fills?'':' —— 还没录过成交，点「✎ 记一笔」'}</div>`}
  </div>`;
  bindHelp();
  liveEquityTag(aid);
  if($('#todofold')) $('#todofold').onclick=ev=>{ ev.preventDefault();
    LVTODO = !((LVTODO==null) ? !!o.alert : LVTODO); loadLive(aid); };
  $('#lvstrat').onclick=ev=>{ ev.preventDefault(); openStrat(aid, a.code_sha256||''); };
  $('#lvset').onclick=()=>openSettings(aid, a, ro);
  $('#lvrec').onclick=()=>openRecord(aid, sig, ro);
  $('#lvtick').onclick=async()=>{
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
  const col=x=>x==null?'':(x>=0?'var(--up)':'var(--down)');
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
  const col=x=>x==null?'':(x>=0?'var(--up)':'var(--down)');
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
      cell('累计收益'+perfHelp(st),
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


/* ---- ⚙ 账户设置（偶尔才用）---- */
function openSettings(aid, a, ro){
  /* ★ 费率的【完整取值】由后端给（fee_effective = FEE_DEFAULT + 账户覆盖）。
     前端不再维护第二份默认值 —— 实测漏了 regulatory 一项，输入框读出
     undefined，保存被后端拒，而错误提示藏在浮层里不容易发现。 */
  /* ★ 费率取值优先用后端给的 fee_effective（= FEE_DEFAULT + 账户覆盖）。
     兜一层【硬编码的最后防线】：万一后端是旧进程、没这个字段，
     也要填出可读的数字而不是 undefined。旧进程本身另有横幅提示。
     这两个值只是"不至于空白"的兜底，真正的权威在后端。 */
  const FB={mode:'parts', buy_rate:0.00031, sell_rate:0.00081, flat_min:5,
            commission:0.00025, min_commission:5, commission_incl_reg:false,
            regulatory:0.0000541, transfer:0.00001, stamp:'auto'};
  const F=Object.assign({}, FB, (LVO&&LVO.fee_default)||{},
                        (LVO&&LVO.fee_effective)||{});
  const H=(LVO&&LVO.fee_history)||[];
  /* 过户费另收范围的选项表【由服务端给】——
     前端抄过一次费率默认值，抄漏一个键就读成 undefined。 */
  const TEO=(LVO&&LVO.transfer_extra_opts)||{both:'沪深都另收'};
  /* ★ 沪深不同价时【两个数都要显示】。银河这档就是沪 万0.96 / 深 万0.86 ——
     只显示一个数（哪怕标了是"较高的那个"）会让另一个市场的成交对不上账，
     而对不上的时候人第一反应是"系统算错了"。 */
  const frRate=(rt,k)=>{
    const bm=rt&&rt.by_market;
    if(!bm||rt.same) return '万'+(((rt||{})[k]||0)*1e4).toFixed(2);
    return '沪 万'+((bm.XSHG[k]||0)*1e4).toFixed(2)
         + ' / 深 万'+((bm.XSHE[k]||0)*1e4).toFixed(2);
  };
  const frFee=(rt,k)=>{
    const bm=rt&&rt.by_market;
    if(!bm||rt.same) return num((rt||{})[k],2)+' 元';
    return '沪 '+num(bm.XSHG[k],2)+' / 深 '+num(bm.XSHE[k],2)+' 元';
  };
  modal(`<div class="lvhead" style="margin-bottom:10px">
      <h2>账户设置</h2><span class="lvtag">id ${esc(a.id)}</span>
      <span style="flex:1"></span><button class="btn" id="mclose">关闭</button></div>
    <div class="lvsec">
      <div class="frow"><label>账户名称</label>
        <input id="ename" value="${esc(a.name||'')}" style="flex:1;min-width:200px">
        <span class="lvwhy">随时可改；与策略解耦，换策略不用新建账户</span></div>
      <div class="frow"><label>初始资金</label>
        <input id="ecash" size="12" value="${a.init_cash||0}">
        <span class="lvwhy">元。<b>开户那一刻的余额</b> —— 后来的入金走「✎ 记一笔 › 现金」</span></div>
      <div class="frow"><label>兜底出信号</label>
        <input id="etick" size="12" value="${esc(a.tick_time||'')}">
        <span class="lvwhy">HH:MM。只在数据同步压根没跑时才用；
          主路径是 <code>sync_daily.sh</code> 跑完直接触发</span></div>
      <div class="frow"><label>预热起点</label>
        <input id="ewarm" size="12" value="${esc(a.warmup_start||'')}">
        <span class="lvwhy">出信号时引擎从这天开始跑，要够策略建仓与滚动</span></div>
      <div class="frow"><label>券商备注</label>
        <input id="enote" value="${esc(a.broker_note||'')}" style="flex:1;min-width:200px">
        <span class="lvwhy">给自己看的，如"华泰 · 主账户"</span></div>
      <div class="frow"><label></label>
        <button class="btn" id="esave" ${ro?'disabled':''}>保存账户信息</button>
        <button class="btn" id="earch" ${ro?'disabled':''}
          title="只从列表隐去，不删任何数据">${a.archived?'取消归档':'归档'}</button>
        <span class="lvwhy">归档只是从列表隐去，<b>不删任何数据</b>（流水与版本快照是决策证据）</span></div>
      <div class="lvmsg" id="emsg"></div>
      <h3 style="margin-top:14px">费率（按【成交日】取当时生效的那一档）
        <button class="btn" id="fadd" ${ro?'disabled':''}
          style="float:right;margin-top:-3px">+ 新增费率</button></h3>
      ${H.length?`<table class="lvt lvfr">
        <tr><th>生效期间</th><th>方式</th>
          <th>买入总费率<div class="lvwhy">10 万一笔</div></th>
          <th>卖出总费率<div class="lvwhy">10 万一笔</div></th><th></th></tr>
        ${H.slice().reverse().map((r,i)=>{
          const m=r.model||{}, rt=r.rates||{};
          /* ★ 表里显示【总费率】而不是佣金率 —— 佣金只是其中一项，
             看"佣金万0.26"完全说明不了实付万0.9。 */
          /* ★ 备注在【行上】就能看见，不能只藏在展开的明细里 ——
             "照账单核过的" 和 "照报价推定的" 在表里长得一模一样，
             而后者可能整体差 60%（同一个万0.8，含规费/另收两种口径）。
             来源不明确的费率看着最像对的。 */
          const nt=(r.note||'').trim();
          return `<tr class="frhead" data-i="${i}" style="cursor:pointer">
            <td>${esc(r.from)} ~ ${r.to?esc(r.to):'<b>至今</b>'}${
              nt?'<div class="lvwhy">'+esc(nt.length>52?nt.slice(0,52)+'…':nt)+'</div>':
                 '<div class="lvwhy">（没写来源）</div>'}</td>
            <td>${m.mode==='flat'?'总费率':'按项'}</td>
            <td><b>${frRate(rt,'buy_rate')}</b></td>
            <td><b>${frRate(rt,'sell_rate')}</b></td>
            <td class="lvwhy">▸ 明细</td></tr>
          <tr class="frbody" data-i="${i}" style="display:none"><td colspan="5">
            <!-- 竖排三列：费用名称 / 费率 / 说明 —— 一眼从上往下扫完，
                 不用在横排卡片间来回找。说明里点明"法定"还是"可谈"。 -->
            <table class="lvt frt">
              <tr><th>费用名称</th><th>费率</th><th>说明</th></tr>
              ${(m.mode==='flat'?[
                  ['买入总费率', '万'+((m.buy_rate||0)*1e4).toFixed(3),
                   '不拆项。10 万一笔买入 '+num(rt.buy_fee,2)+' 元'],
                  ['卖出总费率', '万'+((m.sell_rate||0)*1e4).toFixed(3),
                   '<b>已含印花税</b>，不再另加。10 万一笔卖出 '+num(rt.sell_fee,2)+' 元'],
                  ['单笔最低', (m.flat_min||0)+' 元',
                   (m.flat_min?'成交额小时按这个数收':'<b>没有最低</b>')],
                ]:[
                  ['净佣金', '万'+((m.commission||0)*1e4).toFixed(3),
                   '<b>券商那部分，可谈</b>'+(m.commission_incl_reg?
                     '。<b>此档口径为「已含规费」</b>，所以规费不再另收':'')],
                  ['规费', '万'+(((m.commission_incl_reg?0:m.regulatory)||0)*1e4).toFixed(3),
                   m.commission_incl_reg
                     ? '已含在上面的佣金里（本档填 0）'
                     : '<b>法定</b>：证管费万0.2（证监会）+ 经手费万0.341（交易所），双边'],
                  ['过户费', '万'+((m.transfer||0)*1e4).toFixed(3),
                   '<b>法定</b>：中登，沪深都收、双边。此档口径：<b>'
                     + (TEO[m.transfer_extra||'both']||m.transfer_extra) + '</b>'
                     + (m.transfer_extra==='xshg'
                        ? '（所以沪市那笔比深市多付万'+((m.transfer||0)*1e4).toFixed(2)+'）':'')],
                  ['印花税', (m.stamp==='auto'?'万5':'万'+((m.stamp||0)*1e4).toFixed(3)),
                   '<b>法定，仅卖出</b>'+(m.stamp==='auto'
                     ? '。<code>auto</code> = 按日期分段：2023-08-28 起万5，之前千1'
                     : '（此档写死，不随日期分段）')],
                  ['单笔最低佣金', (m.min_commission||0)+' 元',
                   (m.min_commission?'佣金不足这个数时按它收':'<b>没有最低</b>')],
                  /* ★ 有最低佣金时，"这个账户的费率"根本不是一个数：
                     分界点以下实付是常数（折成率反而更高），以上才是那个费率。
                     只显示 10 万一笔的总费率会让小额单看着比实际便宜。 */
                  ...(m.min_commission&&m.commission ? [[
                    '最低的分界点', num(Math.round(m.min_commission/m.commission),0)+' 元',
                    '<b>成交额低于这个数，最低佣金就会顶上来</b>：实付 = '
                      + (m.min_commission||0) + ' 元'
                      + (m.transfer_extra==='xshg'
                         ? '（沪市再加过户费万'+((m.transfer||0)*1e4).toFixed(2)+'，深市不加）'
                         : (m.transfer? '＋过户费万'+((m.transfer||0)*1e4).toFixed(2):''))
                      + '，折成费率会比上面那个<b>高</b>'
                  ]] : []),
                  ['— 买入合计', frRate(rt,'buy_rate'),
                   '净佣金 + 规费 + 过户费。10 万一笔 '+frFee(rt,'buy_fee')],
                  ['— 卖出合计', frRate(rt,'sell_rate'),
                   '买入合计 + 印花税。10 万一笔 '+frFee(rt,'sell_fee')],
                ]).map(x=>`<tr><td>${x[0]}</td><td class="frv">${x[1]}</td>
                  <td class="lvwhy">${x[2]}</td></tr>`).join('')}
            </table>
            ${r.note?`<div class="lvwhy" style="margin-top:6px">备注：${esc(r.note)}</div>`:''}
            <div class="lvwhy">录入于 ${esc((r.ts||'').slice(0,16).replace('T',' '))}</div>
          </td></tr>`;}).join('')}</table>`
        :'<div class="lvwhy">还没配过费率 —— 点右上「+ 新增费率」。未配置时按默认费率估（偏保守）。</div>'}
      <div class="lvwhy" style="margin:6px 0 0">
        <b>只能新增，不能改已有的。</b>新一档生效，上一档自动在前一天结束。<br>
        ★ <b>补录历史成交会用那笔成交【当时】的费率</b> ——
        换过券商之后拿今天的费率去算三个月前那笔，数字看着正常，只是错的。
        ${H.length?'（'+esc(H[0].from)+' 之前没有配置，按默认费率估）':''}
      </div>

      <div id="fpanel" style="display:none;margin-top:12px;border-top:1px solid var(--line);padding-top:10px">
      <div class="frow"><label>生效日</label>
        <input id="ffrom" size="12" value="${new Date().toISOString().slice(0,10)}">
        <span class="lvwhy">这一档从哪天开始算</span></div>
      <div class="frow"><label>怎么填</label>
        <select id="fway">
          <option value="bill">照一张真实账单填（推荐）</option>
          <option value="rate">直接填各项费率</option>
          <option value="flat">只填买卖总费率</option>
        </select>
        <span class="lvwhy">前两种最后存的是同一套【逐项费率】；
          总费率是"不想拆项"时用的</span></div>

      <div id="fbill">
        <div class="frow"><label>成交金额</label>
          <input id="c0" size="12" class="ci" placeholder="66110">
          <span class="lvwhy">元。这张账单那一笔的成交金额</span></div>
        <div class="frow"><label>佣金</label>
          <input id="c1" size="12" class="ci" placeholder="1.72">
          <span class="lvwhy" id="r1">元 → 折成费率</span></div>
        <div class="frow"><label>规费</label>
          <input id="c2" size="12" class="ci" placeholder="3.57">
          <span class="lvwhy" id="r2">元 → 法定：证管费万0.2 + 经手费万0.341。
            <b>账单上没有这一行就填 0</b>（说明已并进佣金里）</span></div>
        <div class="frow"><label>过户费</label>
          <input id="c3" size="12" class="ci" placeholder="0.66">
          <span class="lvwhy" id="r3">元 → 法定：中登，沪深都收</span></div>
        <div class="frow"><label>这笔是哪个市场</label>
          <select id="cmk" class="ci">
            <option value="600000.XSHG">沪市（60/68 开头）</option>
            <option value="000001.XSHE">深市（00/30 开头）</option>
          </select>
          <span class="lvwhy">复算这张账单要用 —— 过户费按下面那项的口径
            可能只有一个市场另收</span></div>
        <div class="frow"><label>印花税</label>
          <input id="c4" size="12" class="ci" placeholder="留空 = 法定万5">
          <span class="lvwhy" id="r4">元 → <b>仅卖出</b>。买入的账单上没有这行，
            留空即按法定万5（2023-08-28 起；之前是千1）</span></div>
        <div class="frow"><label>单笔最低佣金</label>
          <input id="c5" size="12" class="ci" value="0">
          <span class="lvwhy">元。<b>0 = 没有最低</b>。
            账单上佣金若被抬到某个整数（常见 5 元），填那个数</span></div>
        <div class="frow"><label></label><span class="lvwhy" id="bsum">
          填上面几格，这里会显示合计与折算出的总费率</span></div>
      </div>

      <div id="frate" style="display:none">
        <div class="frow"><label>净佣金率</label>
          <input id="fr1" size="12" value="${F.commission}">
          <span class="lvwhy">= 万${(F.commission*1e4).toFixed(3)}。
            <b>只填券商那部分</b>，不含规费</span></div>
        <div class="frow"><label>规费率</label>
          <input id="fr6" size="12" value="${F.commission_incl_reg?0:F.regulatory}">
          <span class="lvwhy">= 万${((F.commission_incl_reg?0:F.regulatory)*1e4).toFixed(3)}。
            法定万0.541。<b>填 0 = 已经含在上面的佣金率里</b></span></div>
        <div class="frow"><label>过户费率</label>
          <input id="fr3" size="12" value="${F.transfer}">
          <span class="lvwhy">= 万${(F.transfer*1e4).toFixed(2)}。法定万0.1</span></div>
        <div class="frow"><label>卖出印花税</label>
          <input id="fr4" size="12" value="${esc(String(F.stamp))}">
          <span class="lvwhy"><code>auto</code> = 按日期分段（万5，仅卖出）；
            与回测引擎共用一份实现</span></div>
        <div class="frow"><label>单笔最低佣金</label>
          <input id="fr2" size="12" value="${F.min_commission}">
          <span class="lvwhy">元。<b>0 = 没有最低</b></span></div>
      </div>

      <div id="fflat" style="display:none">
        <div class="frow"><label>买入总费率</label>
          <input id="fb1" size="12" value="${F.buy_rate}">
          <span class="lvwhy">= 万${(F.buy_rate*1e4).toFixed(2)}</span></div>
        <div class="frow"><label>卖出总费率</label>
          <input id="fb2" size="12" value="${F.sell_rate}">
          <span class="lvwhy">= 万${(F.sell_rate*1e4).toFixed(2)}。
            <b>要把印花税万5 算进去</b></span></div>
        <div class="frow"><label>单笔最低</label>
          <input id="fb3" size="12" value="${F.flat_min}">
          <span class="lvwhy">元，0 = 无</span></div>
        <div class="frow"><label></label>
          <button class="btn" id="ffill">按当前逐项折算填入</button>
          <span class="lvwhy">填了总费率就<b>不再拆项、也不另加印花税</b></span></div>
      </div>

      <div class="frow" id="fterow"><label>过户费另收范围</label>
        <select id="fte">${Object.keys(TEO).map(k=>
          `<option value="${k}" ${(F.transfer_extra||'both')===k?'selected':''}
            >${esc(TEO[k])}</option>`).join('')}</select>
        <span class="lvwhy">★ <b>分市场</b>：券商的"万X 最低5元"常是个打包价，
          可能已经含了过户费。实测银河 —— 深市的过户费在打包价里、沪市另收，
          所以同一账户 沪 万0.96 / 深 万0.86。<b>一张账单定不出这一项</b>，
          要沪深各一张对着看</span></div>
      <div class="frow" style="margin-top:10px"><label>备注</label>
        <input id="fnote" placeholder="为什么改（如：换券商 / 谈到万0.5 / 上一档填错了）"
               style="flex:1;min-width:200px"></div>
      <div class="frow"><label></label>
        <label class="lvwhy" style="flex:0 0 auto;text-align:left">
          <input type="checkbox" id="fsup"> 更正上一档（同一生效日，把它替换掉）</label>
        <button class="btn" id="fsave" ${ro?'disabled':''}>新增这一档</button>
        <span class="lvwhy">不勾：生效日必须晚于上一档（费率变了）。<br>
          勾上：允许同一生效日 —— <b>上一档填错了</b>时用，那一档会被
          <b>直接删掉</b>（它有效区间是零长度，从来没有成交按它算过）。<br>
          ⚠ 只解决填错。若已按错费率算过成交，那些费用已落在流水里 ——
          要去流水页「冲正 + 重录」让它重算。</span></div>
      <div class="lvmsg" id="emsg2"></div>
      </div>
      <div class="lvwhy">id <code>${esc(a.id)}</code> 不可改 —— 它决定 live/&lt;id&gt;/
        目录名，改了等于换账户、流水与版本历史全断。<br>
        <b>初始资金 = 开户那一刻的余额。</b>后来的入金请走「✎ 记一笔 › 现金」——
        改初始资金会把入金追溯到开户日，过去每天的权益都变，而且不留痕迹。<br>
        <b>兜底时刻</b>只在数据同步压根没跑时才用；主路径是 sync_daily.sh
        跑完直接触发出信号。</div>
    </div>`, close=>{
    $('#esave').onclick=async()=>{
      const m=$('#emsg');
      try{
        await post('/api/live/save',{id:aid, name:$('#ename').value.trim(),
          init_cash:parseFloat($('#ecash').value||'0'),
          tick_time:$('#etick').value.trim(), warmup_start:$('#ewarm').value.trim(),
          broker_note:$('#enote').value});
        close(); showLive(aid);
      }catch(e){ m.className='lvmsg bad'; m.textContent=String(e); }
    };
    /* 三种填法都产出【同一套逐项费率】（总费率那种除外）。
       ★ 刻意去掉了原来的"这个率含不含规费"开关 —— 它是最容易填错的一处：
         同一个万0.8，含规费口径算 5.95、另收算 9.53，差 60%。
         现在改成【规费自己一行】：填 0 就表示已含在佣金里，少一个概念。 */
    const bill=()=>{
      const g=id=>parseFloat($('#'+id).value)||0;
      const amt=g('c0');
      const put=(id,v)=>{ const e=$('#'+id); if(!e) return;
        e.dataset.base=e.dataset.base||e.innerHTML;
        e.innerHTML=(amt>0?'= 万'+(v/amt*1e4).toFixed(3)+'　':'')+e.dataset.base; };
      put('r1',g('c1')); put('r2',g('c2')); put('r3',g('c3')); put('r4',g('c4'));
      /* ★ 这张账单的过户费到底算不算进合计，取决于「过户费另收范围」+
         这笔是哪个市场。银河的深市账单里，过户费是【含在佣金里】的，
         照单相加会把它算两次（5.00 的账单显示成 5.33）。 */
      const te=($('#fte')||{}).value||'both';
      const mk=(($('#cmk')||{}).value||'').split('.')[1]||'XSHG';
      const trExtra=(te==='both')||(te==='xshg'&&mk!=='XSHE');
      const buy=g('c1')+g('c2')+(trExtra?g('c3'):0);
      const sell=buy+(g('c4')||(amt*0.0005));
      /* 🔴 佣金那行正好等于最低佣金 -> 这笔被最低顶住了，反推不出真实费率。
         实测银河：南都物业 37,455 佣金5.00 反推万1.34，而真值万0.86（大唐
         106,856 佣金9.19）—— 差 56%，两个都是"能算出来的数"。 */
      const mc=g('c5'), pinned=mc>0&&Math.abs(g('c1')+g('c2')-mc)<0.005;
      $('#bsum').innerHTML=!(amt>0)
        ? '填上面几格，这里会显示合计与折算出的总费率'
        : (pinned
            ? '<b style="color:var(--warn)">⚠ 这笔的佣金正好等于最低 '+mc.toFixed(2)
              +' 元 —— 它被【最低】顶住了，除出来的 万'
              +((g('c1')+g('c2'))/amt*1e4).toFixed(2)+' 不是真实费率。</b><br>'
              +'请换一笔【金额更大】的账单：要大于约 '
              +num(Math.round(mc/0.000086),0)+' 元（按万0.86 估）最低才不 binding。<br>'
            : '')
          + '账单这一笔（买入口径）合计 <b>'+buy.toFixed(2)+'</b> 元 = 万'
          +(buy/amt*1e4).toFixed(2)
          +(trExtra?'':'（过户费按当前口径<b>已含在佣金里</b>，没有重复计入）')
          +'；卖出再加印花税 → 万'+(sell/amt*1e4).toFixed(2)
          +'<br>保存后系统复算这一笔，若与账单差 0.01 是券商逐项截尾，正常';
    };
    const syncWay=()=>{ const w=$('#fway').value;
      $('#fbill').style.display=w==='bill'?'':'none';
      $('#frate').style.display=w==='rate'?'':'none';
      $('#fflat').style.display=w==='flat'?'':'none';
      /* 总费率模式里没有"过户费"这个概念（总费率里就该已含），
         留着这一项只会让人以为还要另配 */
      $('#fterow').style.display=w==='flat'?'none':'';
      bill(); };
    $('#fway').onchange=syncWay; syncWay();
    $('#fadd').onclick=()=>{ const e=$('#fpanel');
      e.style.display=(e.style.display==='none'?'':'none');
      if(e.style.display==='') e.scrollIntoView({block:'nearest'}); };
    /* 点费率那一行展开逐项明细 —— 原来这些信息挤在"备注"里，
       而备注是给人写"为什么改"的，不该塞规格。 */
    document.querySelectorAll('tr.frhead').forEach(tr=>tr.onclick=()=>{
      const i=tr.dataset.i;
      const b=document.querySelector('tr.frbody[data-i="'+i+'"]');
      if(!b) return;
      const open=b.style.display==='none';
      b.style.display=open?'':'none';
      tr.querySelector('td:last-child').textContent=open?'▾ 收起':'▸ 明细';
    });
    document.querySelectorAll('.ci').forEach(e=>e.oninput=bill);
    bill();
    $('#ffill').onclick=()=>{
      /* 把 parts 折出来的总费率填进 flat —— 服务端算好给的（fee_rates），
         前端不再自己拆一遍。 */
      const r=(LVO||{}).fee_rates||{};
      if(r.buy_rate!=null){ $('#fb1').value=r.buy_rate; $('#fb2').value=r.sell_rate;
        $('#emsg2').className='lvmsg ok';
        $('#emsg2').textContent='已按当前拆项填入：买入 万'+(r.buy_rate*1e4).toFixed(2)
          +' / 卖出 万'+(r.sell_rate*1e4).toFixed(2)+'（按 '+num(r.amount,0)+' 元一笔折算）';
      }else{ $('#emsg2').className='lvmsg bad';
        $('#emsg2').textContent='拿不到折算结果 —— 服务端可能是旧进程，重启 serve.py'; }
    };
    $('#fsave').onclick=async()=>{
      const m=$('#emsg2');
      try{
        const w=$('#fway').value;
        let fee;
        if(w==='flat'){
          fee={mode:'flat', buy_rate:parseFloat($('#fb1').value),
               sell_rate:parseFloat($('#fb2').value),
               flat_min:parseFloat($('#fb3').value||'0')};
        }else if(w==='bill'){
          /* 照账单填：把各项金额除以成交金额得到费率。
             ★ commission_incl_reg 永远写 false —— 规费自己一行，
               填 0 就表示"已含在佣金里"。不再有那个开关。 */
          const g=id=>parseFloat($('#'+id).value)||0;
          const amt=g('c0');
          if(!(amt>0)) throw new Error('请填这张账单那一笔的成交金额');
          const st=$('#c4').value.trim();
          fee={mode:'parts', commission_incl_reg:false,
               commission:g('c1')/amt, regulatory:g('c2')/amt,
               transfer:g('c3')/amt, transfer_extra:$('#fte').value,
               stamp:(st===''?'auto':g('c4')/amt),
               min_commission:g('c5')};
        }else{
          fee={mode:'parts', commission_incl_reg:false,
               commission:parseFloat($('#fr1').value),
               regulatory:parseFloat($('#fr6').value||'0'),
               transfer:parseFloat($('#fr3').value||'0'),
               transfer_extra:$('#fte').value,
               min_commission:parseFloat($('#fr2').value||'0'),
               stamp:($('#fr4').value.trim()==='auto'?'auto':parseFloat($('#fr4').value))};
        }
        await post('/api/live/fee_rate',{id:aid,
          from:$('#ffrom').value.trim(), note:$('#fnote').value,
          supersede:$('#fsup').checked, fee:fee});
        m.className='lvmsg ok';
        m.textContent='已新增一档 —— 上一档在前一天结束；'
          +'成交费用按【成交日】取对应那一档';
        setTimeout(()=>{ close(); showLive(aid); }, 900);
      }catch(e){ m.className='lvmsg bad'; m.textContent=String(e); }
    };
    /* 原来的「反推」按钮已被【照账单填】取代 —— 那本来就是同一件事
       （拿账单各项金额除以成交金额），只是绕了一圈：先反推、再回填到
       "佣金率 + 口径开关"两个格子里，而口径正是最容易填错的地方。
       现在直接对着账单逐项抄，费率是实时算出来的，没有中间环节。 */
    $('#earch').onclick=async()=>{
      try{ await post('/api/live/save',{id:aid, archived:!a.archived});
        close(); showLive(null);
      }catch(e){ $('#emsg').className='lvmsg bad'; $('#emsg').textContent=String(e); }
    };
  });
}

/* ---- ✎ 记一笔：成交 / 现金 两个 tab（合成一个入口）---- */
function openRecord(aid, sig, ro, tab){
  const o=LVO||{};
  const cf=(o.cashflows||[]);
  const today=new Date().toISOString().slice(0,10);
  const d0=(sig&&!sig.error)?sig.for_date:today;
  /* ★ 「用开盘价」只在成交日的行情【已经同步到本地】时才默认勾上。
     调仓日早上录入是最常见的场景，那时 for_date 的行情还不存在，
     勾着会让整批十几笔全部失败 —— 信号自己带着 data_asof，
     能提前知道的事不要留到报错时才说。 */
  const sigOpen=!!(sig&&!sig.error&&sig.data_asof&&sig.for_date
                   &&sig.data_asof>=sig.for_date);
  modal(`<div class="lvhead" style="margin-bottom:10px">
      <h2>记一笔</h2>
      <button class="btn rtab" data-t="fill">成交</button>
      <button class="btn rtab" data-t="cash">现金</button>
      <span style="flex:1"></span><button class="btn" id="mclose">关闭</button></div>
    <div id="rfill" class="lvsec">
      <h3>逐笔录入</h3>
      <div class="lvform">
        <input id="fd" size="10" placeholder="YYYY-MM-DD" value="${esc(d0)}">
        <input id="fc" size="14" placeholder="代码 301126.SZ / sz301126">
        <select id="fs"><option value="buy">买</option><option value="sell">卖</option></select>
        <input id="fq" size="7" placeholder="股数">
        <input id="fp" size="9" placeholder="价格(留空=开盘价)">
        <input id="ff" size="9" placeholder="费用(留空=按费率)">
        <button class="btn" id="fb" ${ro?'disabled':''}>录一笔</button>
      </div>
      <div class="frow"><label></label>
        <label class="lvwhy" style="flex:0 0 auto;text-align:left">
          <input type="checkbox" id="fforce"> 按填的价，不校验当日高低区间</label>
        <span class="lvwhy">默认会校验 —— 价格落在当日 high/low 之外是
          <b>可证的错</b>（小数点点错、看错行、误填后复权价），而成本价
          要喂给止损判定。<b>大宗交易</b>等确实成交在区间外时勾上</span></div>
      <div class="lvwhy">
        <b>价格留空 = 按成交日的【开盘价】</b> —— A 股开盘价就是 09:15-09:25
        集合竞价的成交价，所以竞价买入的单子留空就对。<br>
        ⚠ 需要<b>当天行情已同步</b>（datalake 要等当晚 sync_daily.sh 跑完）。
        调仓日早上就要录的话请手填价格，或等晚上再录。<br>
        <b>费用留空 = 按这个账户的费率算</b>（见 ⚙ 里的费率），入账标「估」。
        <b>留空不等于 0</b> —— 年换手 4.5 次的话，费用当 0 算一年会让权益虚高约 0.5%。
      </div>
      <h3 style="margin-top:12px">批量粘贴</h3>
      <div class="lvform">
        <textarea id="ft" placeholder="每行「日期 代码 买/卖 股数 价格 [费用]」，逗号/制表符/空格分隔皆可。价格填 - 表示按当日开盘价"></textarea>
        <div class="lvwhy">代码<b>各家写法都认</b>，不用改：
          <code>301126.SZ</code>（券商/QMT）、<code>sz301126</code>（通达信）、
          <code>301126.XSHE</code>（聚宽）、<code>301126</code>（裸六位按前缀判市场）。
          落盘统一成聚宽口径。<br>
          ⚠ 前缀与标记<b>冲突会拒</b>（如 <code>600000.SZ</code>）——
          市场决定过户费收不收（万0.1），有一个写错了就不替你选。</div>
        <button class="btn" id="fbulk" ${ro?'disabled':''}>解析并录入</button>
        ${sig&&!sig.error&&((sig.sell||[]).length+(sig.buy||[]).length)?
          `<button class="btn" id="fauto" ${ro?'disabled':''}>按今日信号全部成交</button>
           <label class="lvwhy" style="flex:0 0 auto">
             <input type="checkbox" id="fopen" ${sigOpen?'checked':''}>
             用 ${esc(d0)} 的开盘价${sigOpen?'':
               '（该日行情还没同步，本地只到 '+esc(sig.data_asof||'?')+'）'}</label>`:''}
      </div>
      <div class="lvmsg" id="fmsg"></div>
    </div>
    <div id="rcash" class="lvsec" style="display:none">
      <h3>入金 / 出金 / 分红到账</h3>
      <div class="lvform">
        <input id="cfd" size="10" placeholder="YYYY-MM-DD" value="${today}">
        <select id="cfk">
          <option value="deposit">入金</option><option value="withdraw">出金</option>
          <option value="dividend">分红到账</option><option value="adjust">调整</option>
        </select>
        <input id="cfa" size="9" placeholder="金额（正数）">
        <input id="cfn" placeholder="备注" style="flex:1;min-width:100px">
        <button class="btn" id="cfb" ${ro?'disabled':''}>记一笔</button>
      </div>
      <div class="lvmsg" id="cfmsg"></div>
      <div class="lvwhy">金额一律填正数，方向由类型决定 —— 避免"负的出金"这种双重否定。<br>
        分红到账<b>不自动推</b>：没有数据源能确认到账日与税后金额。</div>
      ${cf.length?`<table class="lvt" style="margin-top:8px">
        <tr><th>日期</th><th>类型</th><th>金额</th><th>备注</th></tr>
        ${cf.map(f=>`<tr><td>${esc(f.date)}</td><td>${esc(CFK[f.kind]||f.kind)}</td>
          <td style="color:${f.signed<0?'var(--down)':'var(--up)'}">${(f.signed>0?'+':'')+num(f.signed,2)}</td>
          <td class="lvwhy">${esc(f.note||'')}</td></tr>`).join('')}</table>`
        :'<div class="none" style="margin-top:8px">还没有现金流水</div>'}
    </div>`, close=>{
    const sel=t=>{ $('#rfill').style.display=(t==='cash'?'none':'');
                   $('#rcash').style.display=(t==='cash'?'':'none');
                   document.querySelectorAll('.rtab').forEach(x=>
                     x.classList.toggle('on', x.dataset.t===t)); };
    document.querySelectorAll('.rtab').forEach(x=>x.onclick=()=>sel(x.dataset.t));
    sel(tab||'fill');
    if(ro) return;
    const m=()=>$('#fmsg');
    const done=async rows=>{
      try{
        const r=await post('/api/live/fill',{id:aid, rows:rows,
          force_price:!!($('#fforce')&&$('#fforce').checked)});
        if(r.errors&&r.errors.length){
          /* ★ 批量失败时同一条原因会重复十几遍（比如"当天行情还没同步"
             会对每一行各报一次），按原因折叠，只在前面列出行号。
             刷屏的提示等于没有提示。 */
          const g={};
          r.errors.forEach(e=>{
            const mm=String(e).match(/^第 (\d+) 行：([\s\S]*)$/);
            const ln=mm?mm[1]:'', why=mm?mm[2]:String(e);
            (g[why]=g[why]||[]).push(ln);
          });
          m().className='lvmsg bad';
          m().innerHTML='录入 '+r.added+' 笔，'+r.errors.length+' 笔失败：<br>'
            + Object.keys(g).map(why=>{
                const ls=g[why].filter(Boolean);
                return (ls.length?'<b>第 '+(ls.length>4
                        ? ls.slice(0,4).join('/')+' 等 '+ls.length+' 行'
                        : ls.join('/')+' 行')+'</b>：':'')
                       + esc(why).replace(/\n/g,'<br>');
              }).join('<hr style="border:0;border-top:1px solid var(--line);margin:6px 0">');
          m().scrollIntoView({block:'center'});
        }else{ close(); showLive(aid); }
      }catch(e){ m().className='lvmsg bad'; m().textContent=String(e); }
    };
    $('#fb').onclick=()=>{ const fv=$('#ff').value.trim(), pv=$('#fp').value.trim();
      done([{trade_date:$('#fd').value.trim(), code:$('#fc').value.trim().toUpperCase(),
        side:$('#fs').value, shares:parseInt($('#fq').value,10),
        price:(pv===''||pv==='-'?null:parseFloat(pv)),
        fee:(fv===''?null:parseFloat(fv)),
        force_price:$('#fforce').checked}]); };
    $('#fbulk').onclick=()=>{ const r=parseBulk($('#ft').value);
      if(r.bad.length){ m().className='lvmsg bad'; m().innerHTML=r.bad.map(esc).join('<br>'); return; }
      if(!r.rows.length){ m().className='lvmsg bad'; m().textContent='没解析出任何一行'; return; }
      done(r.rows); };
    const fa=$('#fauto');
    if(fa) fa.onclick=()=>{
      /* 勾上「用当日开盘价」-> price 传 null，由服务端取成交日开盘价（=集合竞价价）。
         不勾 -> 用信号里的 T-1 参考价，那只是个估算。 */
      const useOpen=$('#fopen') && $('#fopen').checked;
      const px=x=>useOpen?null:x.ref_price;
      const rows=[].concat(
        (sig.sell||[]).map(x=>({trade_date:sig.for_date, code:x.code, side:'sell',
          shares:x.shares, price:px(x), name:x.name, source:'signal', fee:null})),
        (sig.buy||[]).map(x=>({trade_date:sig.for_date, code:x.code, side:'buy',
          shares:x.shares, price:px(x), name:x.name, source:'signal', fee:null})));
      if(!confirm('按信号录入 '+rows.length+' 笔？\n'
        +(useOpen?'价格取 '+sig.for_date+' 的【开盘价】（集合竞价成交价）——'
                 +'需当天行情已同步，否则会报错并告诉你原因。'
                 :'价格用的是 T-1 参考价（估算），成交后请到流水页冲正改成实际价。'))) return;
      done(rows); };
    $('#cfb').onclick=async()=>{
      try{
        await post('/api/live/cash',{id:aid, date:$('#cfd').value.trim(),
          kind:$('#cfk').value, amount:parseFloat($('#cfa').value), note:$('#cfn').value});
        close(); showLive(aid);
      }catch(e){ $('#cfmsg').className='lvmsg bad'; $('#cfmsg').textContent=String(e); }
    };
  });
}

/* ---- 成交流水：独立页面 + 服务端分页 ---- */
async function showFills(aid, off){
  stopPoll();
  enterView();
  if(!LV){ try{ LV=await j('/api/live/accounts'); }catch(e){} }
  let o; try{ o=await j('/api/live/fills?id='+encodeURIComponent(aid)
      +'&offset='+(off||0)+'&limit='+LVPAGE); }
  catch(e){ $('#main').innerHTML='<div class="none">'+esc(e)+'</div>'; return; }
  const ro=(LV||{}).readonly, n=o.total, from=o.offset+1, to=Math.min(o.offset+o.limit,n);
  const pages=Math.max(1, Math.ceil(n/o.limit)), cur=Math.floor(o.offset/o.limit)+1;
  $('#main').innerHTML=`
  <div class="lvhead">
    <h2>成交流水</h2>
    <a class="lvtag" href="#/live/${esc(aid)}">‹ 回账户</a>
    <span class="lvtag">${n} 笔</span>
    <span class="lvtag">费用合计 ${num(o.fee_total,2)}${
      o.fee_estimated_n?'（'+o.fee_estimated_n+' 笔估算）':''}</span>
  </div>
  ${n?`<div class="lvsec"><table class="lvt">
    <!-- 列序按【看的顺序】排：哪天、买还是卖、哪只票、什么价、多少股、
         多少钱。录入时间与来源是审计信息，平时不看，挪到最后并压暗。
         数字列右对齐 + tabular-nums，位数才对得齐（.rt / .lvt td.rt）。 -->
    <tr><th>成交日</th><th class="tx">方向</th><th class="tx">代码</th><th class="tx">名称</th>
        <th class="rt">价格</th><th class="rt">股数</th><th class="rt">金额</th>
        <th class="rt">费用</th><th></th>
        <th class="lvwhy tx">录入时间</th><th class="lvwhy tx">来源</th></tr>
    ${o.rows.map(f=>{
      const rev=!!f.reverse_of, dead=!!f._dead, fid=f.uid||f.ts;
      return `<tr class="${rev||dead?'lvrev':''}">
      <td>${esc(f.trade_date)}</td>
      <td class="tx" style="color:${f.side==='buy'?'var(--up)':'var(--down)'}">${f.side==='buy'?'买':'卖'}</td>
      <td class="tx">${skLink(f.code, f.code)}</td>
      <td class="tx">${skLink(f.code, f.name||'')}</td>
      <td class="rt" title="${f.price_from?'取的成交日'+(f.price_from==='open'?'开盘价':'收盘价')+'，不是券商回报':''}">${num(f.price,3)}${
        f.price_from?'<span class="lvwhy">'+(f.price_from==='open'?'开':'收')+'</span>':''}</td>
      <td class="rt">${num(f.shares)}</td>
      <td class="rt">${num(f.shares*f.price,2)}</td>
      <td class="rt" title="${f.fee_estimated?'估算值，对完账单请冲正改成实际':''}">${num(f.fee,2)}${
        f.fee_estimated?'<span class="lvwhy">估</span>':''}</td>
      <td>${(rev||dead||ro)?'':`<a href="#" class="lvrv" data-ts="${esc(fid)}"
        data-d="${esc(f.trade_date)}" data-c="${esc(f.code)}" data-s="${f.side}"
        data-q="${f.shares}" data-p="${f.price}" data-n="${esc(f.name||'')}"
        data-f="${f.fee||0}">冲正</a>`}</td>
      <td class="lvwhy tx">${esc(f.ts.slice(5,16).replace('T',' '))}</td>
      <td class="lvwhy tx">${rev?'冲正':esc(f.source||'')}</td></tr>`;}).join('')}
    </table>
    <div class="lvform" style="margin-top:10px;align-items:baseline">
      <button class="btn" id="pprev" ${o.offset<=0?'disabled':''}>‹ 上一页</button>
      <span class="lvwhy">第 ${cur}/${pages} 页 · 第 ${from}–${to} 笔（倒序）</span>
      <button class="btn" id="pnext" ${to>=n?'disabled':''}>下一页 ›</button>
    </div>
    <div class="lvwhy" style="margin-top:6px">账本<b>只追加</b>：录错了点「冲正」——
      追加一条反方向记录（费用取 −原费用，净影响 0），原记录保留并划掉。
      改一笔 = 冲正 + 重录。</div>
  </div>`
  :'<div class="none">还没录过成交</div>'}`;
  const go=x=>{ location.hash='#/live/'+aid+'/fills'+(x?'/'+x:''); };
  if($('#pprev')) $('#pprev').onclick=()=>go(Math.max(0,o.offset-o.limit));
  if($('#pnext')) $('#pnext').onclick=()=>go(o.offset+o.limit);
  document.querySelectorAll('a.lvrv').forEach(e=>e.onclick=async ev=>{
    ev.preventDefault(); const d=e.dataset;
    const of=parseFloat(d.f||'0')||0;
    if(!confirm(`冲正：${d.d} ${d.c} ${d.s==='buy'?'买':'卖'} ${d.q} @${d.p}（费用 ${of.toFixed(2)}）\n`
      +`会追加一条反方向记录（${d.s==='buy'?'卖':'买'} ${d.q}，费用 ${(-of).toFixed(2)}），\n`
      +`原记录保留并划掉。净现金影响为 0。`)) return;
    try{
      await post('/api/live/fill',{id:aid, rows:[{trade_date:d.d, code:d.c,
        side:d.s==='buy'?'sell':'buy', shares:parseInt(d.q,10), price:parseFloat(d.p),
        fee:-of, name:d.n, source:'reverse', reverse_of:d.ts, note:'冲正'}]});
      showFills(aid, o.offset);
    }catch(e){ alert(String(e)); }
  });
}

/* 策略详情：源码快照 + 参数表 + 【用这个版本跑过的回测】。
   关联键是主文件自身哈希 —— 账户的 code_sha256 是"主文件+依赖"的打包哈希，
   与归档的单文件哈希口径不同，直接比永远匹配不上（服务端已处理）。 */
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
      ${vs.length?`<table class="lvt lvvt"><tr><th>时间</th><th>版本</th><th>参数</th></tr>
        ${vs.map(x=>`<tr><td>${esc(x.ts.slice(0,10))}<br><span class="lvwhy">${esc(x.ts.slice(11,16))}</span></td>
          <td><a class="lvver" href="#" data-sha="${esc(x.code_sha256)}"
                 title="${esc(x.strategy_path||'')}">${esc(x.code_sha)}</a>
              ${x.code_sha256===sha?'<br><span class="lvwhy">当前</span>':''}</td>
          <td class="lvpar">${Object.entries(x.params||{}).map(([k,y])=>
              `<span class="lvkv">${esc(k)}=${esc(y)}</span>`).join('')||'<span class="lvwhy">默认</span>'}
              ${x.reason?`<div class="lvwhy">${esc(x.reason)}</div>`:''}</td></tr>`).join('')}</table>`
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
/* ---- 实盘里的代码/名称 -> 个股页 ------------------------------------
   ★ 用 `target="_blank"` 新标签页打开：实盘页往往一直开着（待办、持仓、
     正在录一半的成交），跳走再回来这些状态就没了。新标签页天然满足
     "能返回到当前实盘页面"。
   ★ 代码与名称都可点 —— 只有代码可点的话，习惯认名字的人会以为不能点。 */
const skLink=(code, text, cls)=>code
  ? `<a href="/stock.html?code=${encodeURIComponent(code)}" target="_blank"
       rel="noopener" class="${cls||''}" style="color:inherit"
       title="在新标签页打开个股：${esc(code)}">${esc(text==null?code:text)}</a>`
  : esc(text||'');

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
  return `<span class="lvtag${behind?' warn':''}"
    title="日线行情最新到 ${dd}${t}${behind?'。⚠ 当前信号是用 '+asof+' 的数据算的 —— 点「立即重算」':''}"
    >数据日 ${dd}${px}${behind?' · 信号用的是 '+asof:''}</span>`;
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
  if(!s) return '<div class="lvsec" style="margin-bottom:14px"><h3>今日待办</h3><div class="none">还没有信号 —— 点「立即重算」</div></div>';
  if(s.error) return `<div class="lvwarn"><b>出信号失败</b><br>${esc(s.error).replace(/\n/g,'<br>')}</div>`;
  const w=(s.warnings||[]).map(x=>`<div class="lvwarn">${esc(x)}</div>`).join('');
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
  return w+`<div class="lvsec${alert?' lvalert':''}" style="margin-bottom:14px">
    <h3>${alert?'<span class="adot"></span> ':''}${esc(s.for_date)} 待办
        ${s.is_rebalance_day?'· 调仓日':'· 非调仓日'}
        <span class="lvtag">数据 ${esc(s.data_asof)} 收盘</span>
        <span class="lvwhy">版本 ${esc(s.code_sha)}</span>
        <span style="flex:1"></span>
        <a href="#" id="todofold" class="lvwhy">${open?'收起 ▾':'展开 ▸'}</a></h3>
    ${open?'':`<div class="lvwhy" style="padding:2px 0">${esc(sum)}${
       rb?' · 下次调仓 '+esc(rb)+(du!=null?'（还有 '+du+' 个交易日）':''):''}</div>`}
    <div style="display:${open?'':'none'}">
    ${banner}${strip}
    <div class="lvgrid">
      <div><b style="color:var(--up)">卖出 ${(s.sell||[]).length} 只</b>
        ${(s.sell||[]).length?`<table class="lvt lvsell"><tr><th>代码</th><th class="tx">名称</th>
            <th class="rt">股数</th><th class="rt">参考价</th><th class="tx">原因</th></tr>
          ${s.sell.map(x=>`<tr><td>${skLink(x.code, x.code)}</td><td class="tx">${skLink(x.code, x.name)}</td>
            <td class="rt">${num(x.shares)}</td>
            <td class="rt">${num(x.ref_price,2)}</td><td class="lvwhy tx">${esc(why[x.reason]||x.reason)}</td></tr>`).join('')}</table>`
          :'<div class="none">无</div>'}</div>
      <div><b style="color:var(--down)">买入 ${(s.buy||[]).length} 只</b>
        ${(s.buy||[]).length?`<table class="lvt lvbuy"><tr><th>代码</th><th class="tx">名称</th>
            <th class="rt">股数</th><th class="rt">限价</th><th class="rt">金额</th></tr>
          ${s.buy.map(x=>`<tr><td>${skLink(x.code, x.code)}</td><td class="tx">${skLink(x.code, x.name)}</td>
            <td class="rt">${num(x.shares)}</td>
            <td class="rt">${num(x.limit,2)}</td><td class="rt">${num(x.amount)}</td></tr>`).join('')}</table>
          <div class="lvwhy">限价 = T-1 收盘 × 1.05（防高开买不进）；twopass 分配，估余 ${num(s.left_est)}</div>`
          :'<div class="none">无</div>'}</div>
      <div><b>持有不动 ${(s.hold||[]).length} 只</b>
        <div class="lvwhy" style="margin-top:4px">${(s.hold||[]).map(x=>
          skLink(x.code, x.name||x.code)).join('、')||'无'}</div></div>
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
