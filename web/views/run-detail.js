/* ============ 回测详情：打开某一次归档之后看到的一切 ============
   与 runs.js 的分界是「挑哪一次」vs「看这一次」——
   目录树/选中的规则/版本页负责挑，这里负责看。

   概览 / 权益曲线 / 收益热力（年→月→日→当天持仓四层下钻）/ 成交 / 持仓 /
   拒单 / 源码 / 日志 / 元信息。 */
/* ============ 详情页 ============ */
async function openRun(id, initTab){
  CUR=id; DATA={}; HD={off:0,lim:HD?HD.lim:100}; TD={off:0,lim:TD?TD.lim:100};
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
/* lineChart 搬到 shared/chart.js —— 实盘业绩页也要用它。
   ★ 仍是裸的顶层函数（本项目不引模块系统），shared/ 在
     views/ 之前加载，所以这里的调用一个字都不用改。 */

/* 整数金额/份额的显示。🔴 **模块级**：原来它是 `drawDay` 里的一个局部
   const，而 `paneHoldings`（持仓页签）也在用 —— 跨函数引用未定义的名字，
   **整个持仓页签当场抛 `money is not defined`、一行都渲染不出来**，
   而它只在控制台里报（用户 2026-09-16："回测里的持仓页面出现问题了，
   展示不出持仓的具体列表了"）。
   ★ 是 2026-09-14 那次「份额一律展示不复权」把 `fmtN(v,1)` 换成
     `money(v)` 时带进来的 —— 那一轮的判据只验到了**接口**与下钻页那条路，
     持仓页签本身从没被真正渲染过（同「持仓不换算那条第一轮漏了，
     因为我挑的归档 rows 是空的」那次，只是这回漏在另一条路上）。 */
const money = v => v == null ? '—'
  : (+v).toLocaleString('en', {maximumFractionDigits: 0});

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
  /* 🔴 `hiCap:1` —— 值是 `回撤+1`（配 pctAxis），所以回撤 0 对应 **1**。
     不钳的话 lineChart 会在顶端多留 6%，最高刻度印成 `+0.4%` ——
     那个数**没有意义**（不可能比历史最高还高），读的人会当成"曾经超出过"。
     ★ 实盘业绩页早就传了 `hiCap: 0`，**这一处漏了** —— 同一个坑修过一次、
       另一处没跟上，而它不报错，只是刻度多一截。
     ★ 回撤为 0 的点**照画**（2026-09-14）：原来用 `ddGap` 抹成 null，
       结果每段回撤两头都不接水面线，"回到历史最高"在图上成了一个空档，
       而空档读作"没有数据"—— 意思正好反了。判据见 shared/chart.js。 */
  /* 水下图：面积 + 水面线。★ 只有一条断断续续的曲线时看不出"在水下待了
     多久"，而那与"跌了多深"同样重要（面积 = 痛苦的总量）。
     ★ `zero:1` 是水面（值是回撤+1，配 pctAxis）；填充色很淡，
       曲线自己保持清晰。 */
  lineChart($('#c2'),[{n:'回撤',v:dd.map(v=>v==null?null:v+1),
                       c:'#e05b5b',w:1.3,fill:'#e05b5b'}],
    {dates,title:'回撤（水下图）',h:190,pctAxis:true,hiCap:1,zero:1});
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
/* _hcol / _sn / _legend 搬到 shared/chart.js —— 实盘业绩页的方格图
   要用同一套配色与图例（抄一份出来的话两页深浅不一致）。 */
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
  /* ★ 与别处同一种排布（名称 + 小字代码），不再是"代码 名称"的纯文本 ——
     同一个页面里两种排法，眼睛每换一张表就要重新找一次。 */
  const nm=r=>cnCell(r.code, r.name, {date:d, run:CUR});
  const px=v=>v==null?'—':(+v).toFixed(3);
  box.innerHTML=`<h3 class="sec">${d} · 当日明细</h3>
    <div class="cards">
      ${card('当日收益',_sn(S.dr[d],2)+'%',sign(S.dr[d]))}
      ${card('权益',money(e.equity[i]*(DATA.run.stats.cash||1)))}
      ${card('仓位',pct(pos,1))}
      ${card('持仓', o.holdings_pruned ? '—' : o.holdings.length+' 只')}
      ${card('当日买入',o.buys.length+' 笔')}
      ${card('当日卖出',o.sells.length+' 笔')}
    </div>
    <div id="d_sell"></div><div id="d_buy"></div><div id="d_hold"></div>`;
  tbl($('#d_sell'),o.sells,[
    {k:'code',t:'标的',l:1,f:(v,r)=>nm(r)},
    {k:'shares',t:'份额',f:v=>money(v),h:'真实股数（不复权），就是券商对账单上的那个数。&#10;🔴 归档内部记的是**后复权记账单位**（实测 774.835189），展示层换算回真实值 —— 2026-09-14 起新归档直接存逐笔成交（fills），不用换算；旧归档按当日复权因子换算。'},
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
    {k:'shares',t:'份额',f:v=>money(v),h:'真实股数（不复权），就是券商对账单上的那个数。&#10;🔴 归档内部记的是**后复权记账单位**（实测 774.835189），展示层换算回真实值 —— 2026-09-14 起新归档直接存逐笔成交（fills），不用换算；旧归档按当日复权因子换算。'},
    {k:'entry_price',t:'建仓价',f:px},
    {k:'gross_amount',t:'成交金额',f:v=>money(v)},
    {k:'exit_date',t:'后来平仓于'},
    {k:'ret',t:'该笔最终',s:1,f:v=>_sn(v,2)+'%'},
  ],'当日买入 '+o.buys.length+' 笔'+(o.buys.length?
      '　（后两列是这笔【整笔】的最终结果，不是当日收益）':'（无）'));
  if(o.holdings_pruned){
    $('#d_hold').innerHTML=`<div class="warn">当天持仓明细已清理
      （${esc(o.holdings_pruned)}）—— 重跑这次回测可再生成。</div>`;
  } else tbl($('#d_hold'),o.holdings,[
    {k:'code',t:'标的',l:1,f:(v,r)=>nm(r)},
    {k:'weight',t:'权重',f:v=>pct(v,1)},
    {k:'value',t:'市值',f:v=>money(v)},
    {k:'shares',t:'份额',f:v=>money(v),h:'真实股数（不复权），就是券商对账单上的那个数。&#10;🔴 归档内部记的是**后复权记账单位**（实测 774.835189），展示层换算回真实值 —— 2026-09-14 起新归档直接存逐笔成交（fills），不用换算；旧归档按当日复权因子换算。'},
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
        `<th data-k="${c.k}" class="${c.l?'l':''}${c.h?' hasH':''}"${
          c.h?` title="${c.h.replace(/"/g,'&quot;')}"`:''}>${c.t}${
          c.h?'<span class="thq">ⓘ</span>':''}${
          sk===c.k?(sd>0?' ▲':' ▼'):''}</th>`).join('')}
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
/* 「股票」格：名称 + 小字代码，点开**速览浮层**并定位到这一行的日期。
   ★ 与实盘持仓同一个交互（点了不跳走）。`run` 必须带上 —— ETF 回测跑在
     平行的 etf_lake 上，不带的话浮层去主面板找，一行都取不到、画出一片空白。
   ★ `html` 里的 name/code 自己转义：`nmcode` 那个老写法是直接插值的，
     这里不沿用（名称来自我们自己的 parquet，风险低，但没理由把它扩散）。 */
const nmpop=(v,r)=>cnCell(v, r.name, {date:r.date||r.entry_date, run:CUR});

async function paneTrades(){
  const t=await j(`/api/trades?id=${CUR}&offset=${TD.off}&limit=${TD.lim}`);
  if(!t.total){ $('#p3').innerHTML='<div class="note">这次回测没有平仓记录。</div>'; return; }
  const R={rebalance:'调仓',intraday:'盘中(涨停打开)',delist:'退市清算',stop:'止损'};
  const pg=Math.floor(t.offset/t.limit)+1, np=Math.max(1,Math.ceil(t.total/t.limit));
  const nav=`<div class="pg">
     <button class="tf" ${pg<=1?'disabled':''}>« 首页</button>
     <button class="tp" ${pg<=1?'disabled':''}>‹ 上一页</button>
     <span>第 <input class="ti" value="${pg}"> / ${np} 页</span>
     <button class="tn" ${pg>=np?'disabled':''}>下一页 ›</button>
     <button class="tl" ${pg>=np?'disabled':''}>末页 »</button>
     <span style="margin-left:10px">每页
       <select class="ts">${[50,100,200].map(x=>
         `<option ${x===t.limit?'selected':''}>${x}</option>`).join('')}</select> 条</span>
     <span style="margin-left:10px">共 ${t.total.toLocaleString()} 行
       （买 ${t.n_buy.toLocaleString()} / 卖 ${t.n_sell.toLocaleString()}）</span></div>`;
  $('#p3').innerHTML=`<div class="note">成交流水，<b>一笔平仓拆成买入、卖出两行</b>，
     各自挂在自己的日期上；<b>按日期倒序</b>（最近的在前），同日<b>先卖后买</b>
     （与引擎撮合顺序一致：卖出先回笼现金）。点<b>名称/代码</b>弹出速览浮层，
     并定位到该行日期（前后各约两个月）。
     <br>🔴 两条口径限制：①「费用」<b>只含卖出侧</b> —— 买入侧的费用没有逐笔落进归档
     （只进了现金流与费用合计）；②<b>期末仍持有的那几只，它们的买入不在本表</b>
     —— 归档只在平仓时写行，去「持仓」页看。
     <br>同一天同一只票可能有多行：加减仓走 FIFO 分批，每行是<b>一批</b>，
     红利税按该批的持有期分档，所以必须分开记。收益率只反映<b>价差</b>，分红单列。</div>
     ${nav}<div id="trt"></div>${nav}`;
  tbl($('#trt'),t.rows,[
    {k:'date',t:'日期',l:1},
    {k:'side',t:'方向',l:1,f:v=>`<b style="color:${
       v==='buy'?'var(--up)':'var(--down)'}">${v==='buy'?'买':'卖'}</b>`},
    {k:'code',t:'股票',l:1,f:nmpop},
    {k:'price',t:'价格',f:v=>fmtN(v,3),h:'当时的**不复权**成交价（含滑点），与券商对账单同一口径。'},
    {k:'shares',t:'份额',f:v=>fmtN(v,1)},
    {k:'amount',t:'金额',f:v=>v==null?'—':(+v).toFixed(0)},
    {k:'holding_days',t:'持有天',f:v=>v==null?'':v},
    {k:'ret',t:'收益率',f:v=>v==null?'':pct(v,2),s:1},
    {k:'pnl',t:'盈亏',f:v=>v==null?'':(+v).toFixed(0),s:1},
    {k:'fee',t:'费用(卖出侧)',f:v=>v==null?'':fmtN(v,0)},
    {k:'div_gross',t:'分红',f:v=>v==null?'':fmtN(v,0)},
    {k:'div_tax',t:'红利税',f:v=>v==null?'':fmtN(v,0)},
    {k:'reason',t:'卖出原因',l:1,f:v=>v==null?'':(R[v]||v)},
  ]);
  /* ★ 上下两套导航用 class 不用 id —— 同 paneHoldings 那条（id 拼接过歧义）。 */
  const go=o=>{TD.off=Math.max(0,Math.min(o,(np-1)*TD.lim));paneTrades();};
  const on=(cls,fn,ev)=>document.querySelectorAll('#p3 .'+cls)
      .forEach(e=>e[ev||'onclick']=fn);
  on('tf',()=>go(0)); on('tp',()=>go(TD.off-TD.lim));
  on('tn',()=>go(TD.off+TD.lim)); on('tl',()=>go((np-1)*TD.lim));
  on('ts',e=>{TD.lim=+e.target.value;TD.off=0;paneTrades();},'onchange');
  on('ti',e=>go((Math.max(1,+e.target.value)-1)*TD.lim),'onchange');
}
async function paneHoldings(){
  const h=await j(`/api/holdings?id=${CUR}&offset=${HD.off}&limit=${HD.lim}`);
  // 🔴 明细被 prune_runs.py 清掉时【必须明说】——`_read()` 读不到 parquet
  //   返回的是空 DataFrame（不报错），于是这里会渲染出一张空表，
  //   而"清理过"和"这次回测没持仓"在页面上长得一模一样。
  if(h.pruned){
    $('#p4').innerHTML=`<div class="warn">这次回测的<b>逐日持仓明细已清理</b>
      （${esc(h.pruned)}，为省空间）。结论、权益曲线、成交记录都还在 ——
      需要持仓明细的话<b>重跑这次回测</b>即可再生成
      （源码快照与参数都在「源码」「元信息」两页）。</div>`;
    return;
  }
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
     点<b>名称/代码</b>弹出速览浮层并定位到该快照日（前后各约两个月）。
     同日内按权重降序。份额与价格都是<b>当时真实的那个数</b>（不复权）——
     归档内部按后复权记账，展示层换算回去。
     点列头排序会打散日期分块（同组行不再相邻）。</div>
     ${nav}<div id="hdt"></div>${nav}`;
  tbl($('#hdt'),h.rows,[
    {k:'code',t:'股票',l:1,f:nmpop},{k:'weight',t:'权重',f:v=>pct(v,2)},
    {k:'value',t:'市值',f:v=>v==null?'—':(+v).toFixed(0)},
    {k:'shares',t:'份额',f:v=>money(v),h:'真实股数（不复权），就是券商对账单上的那个数。&#10;🔴 归档内部记的是**后复权记账单位**（实测 774.835189），展示层换算回真实值 —— 2026-09-14 起新归档直接存逐笔成交（fills），不用换算；旧归档按当日复权因子换算。'},
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
