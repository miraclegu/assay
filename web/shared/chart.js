/* 跨页共享的图表件。
   ★ 放 shared/ 的判据是**"谁在用"**：回测详情页（views/run-detail.js）与
     实盘业绩页（views/live-perf.js）都要画权益/收益曲线、都要按年月日分桶。
     留在 run-detail.js 里的话第二处就得抄一份，而抄出来的那份会分叉 ——
     分叉的表现是"两页对同一段权益给出不同的月收益"，且不报错。
   🔴 仍是**裸的顶层函数** + <script src>，不引模块系统：selftest 大量用
     pg.evaluate 直接读全局（同 app.js 头部那条）。
   🔴 顶层名字不能与其它 .js 撞 —— 撞了是整页 SyntaxError、所有功能一起没。
     selftest 里有两两全比的断言。 */

/* 金额 -> 「40.49 万」/「1.23 亿」。★ y 轴上要短，tooltip 里可以精确些。
   🔴 **只转发**给 common.js 的 `yiv` —— 折算规则（阈值/单位/空格）
     全站只许有一份，这里出的只是"精确些"那几位小数。 */
function _money(v, dp){
  return dp == null ? yiv(v) : yiv(v, {yi: dp, wan: 2, small: 2});
}


let _gidN = 0;

function lineChart(el,series,opt){
  opt=opt||{}; const W=1160,H=opt.h||330,L=54,R=16,T=opt.t||22,B=26;
  const n=series[0].v.length; if(!n){el.innerHTML='<div class="note">无数据</div>';return;}
  const log=!!opt.log;
  const tf=v=>log?Math.log10(Math.max(v,1e-6)):v;
  let lo=Infinity,hi=-Infinity;
  series.forEach(s=>s.v.forEach(v=>{if(v==null)return;const t=tf(v);
    if(t<lo)lo=t; if(t>hi)hi=t;}));
  if(!(hi>lo)){hi=lo+1;}
  /* 🔴 **参考线必须落在量程里，否则它静默不画。** `opt.zero` 是水面线
     （回撤图的 0%）。账户一直在水下时数据的最大值是负的（实测 -0.30%），
     量程就整段在 0 以下 —— 水面线画在画布外面，而**那不报错**：
     图看着正常，只是没有参照物，"离水面多远"读不出来。
     ★ 所以先把参考线纳入极值，再做留白与钳制。 */
  if(opt.zero!=null){const z=tf(opt.zero); if(z<lo)lo=z; if(z>hi)hi=z;}
  const pad=(hi-lo)*0.06; lo-=pad; hi+=pad;
  /* 🔴 钳住有**物理上界/下界**的序列。回撤的最高点永远是 0（在最高点时
     回撤为 0，不可能为正），而上面那 6% 的留白会把上界抬成 +0.1% ——
     那个数**没有意义**，读的人会以为"曾经比历史最高还高 0.1%"。
     ★ 只钳边界、不动数据：曲线形状一点没变，只是坐标轴不再多留一截。 */
  if(opt.hiCap!=null) hi=Math.min(hi, tf(opt.hiCap));
  if(opt.loCap!=null) lo=Math.max(lo, tf(opt.loCap));
  if(!(hi>lo)) hi=lo+(Math.abs(lo)||1)*0.01;
  const X=i=>L+(W-L-R)*(n<2?0:i/(n-1)), Y=v=>T+(H-T-B)*(1-(tf(v)-lo)/(hi-lo));
  const path=s=>{let d='',on=false;
    s.v.forEach((v,i)=>{if(v==null){on=false;return;}
      d+=(on?'L':'M')+X(i).toFixed(1)+' '+Y(v).toFixed(1)+' ';on=true;});return d;};
  /* 面积填充：每一段（null 之间）单独闭合到基线。
     🔴 **逐段闭合，不是整条闭合** —— 整条的话中间那些 null（水面上的日子）
       会被一条直线跨过去，填出一片"其实没有回撤"的红色。
     ★ 水下图的面积本身就是信息：它同时说出"跌了多深"和"在水下待了多久"，
       而单看曲线只能看出深度。 */
  const area=(s,baseV)=>{
    const yb=Y(baseV).toFixed(1); let d='',seg=[];
    const flush=()=>{
      if(seg.length<1){seg=[];return;}
      d+='M'+seg[0][0]+' '+yb+' ';
      seg.forEach(([x,y])=>{d+='L'+x+' '+y+' ';});
      d+='L'+seg[seg.length-1][0]+' '+yb+' Z ';
      seg=[];
    };
    s.v.forEach((v,i)=>{
      if(v==null){flush();return;}
      seg.push([X(i).toFixed(1),Y(v).toFixed(1)]);
    });
    flush();
    return d;
  };
  // y 轴刻度
  let ticks=[];
  for(let k=0;k<=4;k++){const t=lo+(hi-lo)*k/4; ticks.push(log?Math.pow(10,t):t);}
  /* y 轴刻度的格式。三种：
       pctAxis  —— 归一化净值 -> 百分比（(v-1)*100）
       moneyAxis —— **绝对金额** -> 万/亿。🔴 资金曲线必须用它：
                 默认那个 `x` 后缀是**倍数**（回测的权益是归一化净值，
                 `2.5x` = 2.5 倍），拿它显示 40 万会印成 `404881x`，
                 而那个数看着像个编号，没人能读出"40 万"。
       默认      —— 倍数（回测详情页一直用的，不动）。 */
  /* ★ pctAxis 的小数位跟着**跨度**走：回测的净值跨几倍（标签几百几千，
       0 位就够），而实盘一个月只有 ±2% —— 0 位会印出 `-0%` 和两个 `0%`,
       读的人分不清哪个是哪个。 */
  const _sp=Math.abs(hi-lo)*100, _dp=_sp<5?2:_sp<20?1:0;
  const yl=v=>opt.pctAxis?((v-1)*100).toFixed(_dp)+'%'
    :opt.moneyAxis?_money(v)
    :opt.ratioAxis?(v*100).toFixed(1)+'%'
    :(v>=10?v.toFixed(0)+'x':v.toFixed(2)+'x');
  /* 最高 / 最低 / 当前 三个点的读数（用户 2026-09-30：「曲线上，需标出
     最高点、最低点、当前位置的收益率，直接在节点旁边显示百分比收益率」）。
     ★ 只标**第一条**序列（账户自己）—— 每条基准都标三个就成了一片数字，
       而那三个数回答的是"我最好/最差/现在是多少"，基准的极值不在其中。
     ★ 标签用与 y 轴**同一个 `yl()`** 格式化 —— 另写一份格式化必然与轴
       对不上（轴上 `+2.0%` 而标签 `+1.95%`，读的人以为图错了）。
     🔴 标签会**顶出画布**：最高点贴着上边、最低点贴着下边、当前点贴着右边。
       所以三处都按位置翻转（上/下、左/右），并把 x 钳在绘图区内。
       顶出去的部分 svg 不报错，只是**看不见** —— 而"看不见"与"没标"
       在屏幕上一模一样。 */
  function _marks(){
    if(!opt.mark) return '';
    const s=series[0]; if(!s||!s.v) return '';
    let iMax=-1,iMin=-1,iLast=-1;
    for(let i=0;i<s.v.length;i++){ const v=s.v[i]; if(v==null) continue;
      iLast=i;
      if(iMax<0||v>s.v[iMax]) iMax=i;
      if(iMin<0||v<s.v[iMin]) iMin=i; }
    if(iLast<0) return '';
    const want=[];
    /* 🔴 **只给百分比，不写「最高/最低/当前」**（用户 2026-09-30 第二轮：
       「上面的最高、最低、当前这些字去掉，只需要一个百分比。风格要简约」）。
       位置本身已经说清是哪个：顶上那个是最高、底下那个是最低、最右那个是
       当前 —— 再写一遍字是重复，而三处各两个汉字压在曲线上就成了噪声。
       ★ 同一个点身兼两职时只画一次（不叠着画）。 */
    /* ★ 方向是**意图**不是标签：最高点的数往上放、最低点往下放 ——
       不给意图的话"离上边太近就翻下面"这条会在**最高点**把它翻到曲线
       下面去，正好压在线上（实测第一版就是这样）。 */
    const add=(i,dir)=>{ if(i<0||want.some(w=>w.i===i)) return; want.push({i:i,d:dir}); };
    add(iMax,'up'); add(iMin,'dn'); add(iLast,'up');
    return want.map(w=>{
      const x=X(w.i), y=Y(tf(s.v[w.i])!==undefined?s.v[w.i]:s.v[w.i]);
      /* 按意图放；**只有真的放不下才翻**（放不下 = 会顶出画布）。 */
      let above = (w.d !== 'dn');
      if(above && y - 9 < T + 4) above = false;        // 上面塞不下
      if(!above && y + 15 > H - B - 2) above = true;   // 下面塞不下
      const ty = above ? y-8 : y+14;
      /* 右边翻转：离右边 < 70px 就右对齐，免得数字被裁掉 */
      const near = x > W-R-70;
      const anchor = near ? 'end' : (w.i===0 ? 'start' : 'middle');
      const tx = Math.max(L+2, Math.min(W-R-2, x));
      /* 🔴 **读数不跟曲线同色**：同色时它读起来像曲线的一部分，而且
         在曲线密的地方直接糊在一起。点用曲线色（它标的就是那条线上的点），
         字用中性色 —— 分工清楚，也不跟基准线抢颜色。
         ⚠ 字的颜色由 `.mkt` 那条 CSS 管，这里**不写** `fill` ——
           SVG 里 CSS 优先于 presentation attribute，写了也不生效，
           而留着会让人以为它在起作用（变异实测：加回内联 fill 毫无变化）。 */
      return '<circle cx="'+x.toFixed(1)+'" cy="'+y.toFixed(1)+'" r="2.6" '
        + 'fill="'+s.c+'"/>'
        + '<text class="mkt" x="'+tx.toFixed(1)+'" y="'+ty.toFixed(1)+'" '
        + 'text-anchor="'+anchor+'">' + yl(s.v[w.i])+'</text>';
    }).join('');
  }

  // x 轴：取 6 个日期
  const _gid = "g" + (++_gidN);
  const xs=[]; for(let k=0;k<6;k++){const i=Math.round((n-1)*k/5); xs.push([i,opt.dates[i]]);}
  el.innerHTML=`
   ${opt.title?`<div class="ttl">${opt.title}</div>`:''}
   ${opt.ctl||''}
   <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
     ${ticks.map(v=>`<line class="gl" x1="${L}" x2="${W-R}" y1="${Y(v).toFixed(1)}" y2="${Y(v).toFixed(1)}"/>
        <text class="ax" x="${L-7}" y="${(Y(v)+3).toFixed(1)}" text-anchor="end">${yl(v)}</text>`).join('')}
     ${xs.map(([i,d])=>`<text class="ax" x="${X(i).toFixed(1)}" y="${H-8}" text-anchor="middle">${d}</text>`).join('')}
     <!-- 水下填充用**渐变**：靠水面几乎透明、越深越浓 —— 均匀一层的话
          浅回撤区域（大部分时间）糊成一片脏色，而"深度"这个信息也丢了。
          🔴 每个图的渐变 id 必须**唯一**（见 _gid）：同一页面上有多张图
            （回测详情页 c1/c2、实盘业绩页三条曲线），id 撞了的话后一个
            定义会赢，表现是"某张图的填充色莫名变了"，而它不报错。 -->
     ${series.filter(s=>s.fill).length?`<defs>${
        series.filter(s=>s.fill).map((s,k)=>
          `<linearGradient id="${_gid}_${k}" x1="0" y1="0" x2="0" y2="1">
             <stop offset="0" stop-color="${s.fill}" stop-opacity="0.05"/>
             <stop offset="1" stop-color="${s.fill}" stop-opacity="0.55"/>
           </linearGradient>`).join('')}</defs>`:''}
     ${series.filter(s=>s.fill).map((s,k)=>
        `<path d="${area(s,opt.zero==null?lo:opt.zero)}"
           fill="url(#${_gid}_${k})" stroke="none"/>`).join('')}
     <!-- 水面线（0 基准）。★ 与网格线**不同色**：网格是刻度，这一条是
          "有没有回撤"的分界 —— 曲线贴着它走的那几段就是创新高。
          🔴 **回撤为 0 的点照画，不许断开**（2026-09-14 改）：原来有个
            ddGap 把所有 0 抹成 null，理由是"那是在水面上，不是水下 0.0%"。
            代价是每一段回撤**两头都不接水面线** —— 入水那天凭空开始、
            出水那天凭空停住，而"回到历史最高"这个明确的事实在图上成了一个
            **空档**。而空档在图表惯例里读作"没有数据"，意思正好反了。
            现在规则没有例外：**断开 = 没有数据**。至于"在水面上"，
            配 hiCap:0 时 0 就是画布顶边，曲线压在顶边上本身就看得出来。
            ⚠️ 这段注释在模板字符串里 —— **不许出现反引号**（写 ddGap 时
            顺手加了一对，当场把模板闭合掉，整个文件 SyntaxError；
            同 CLAUDE.md 里"注释里别写连着的星号加斜杠"那条）。 -->
          🔴 颜色刻意淡（不抢曲线）但要能认出来，所以换个色系（偏蓝=水面）
            而不是把灰网格加深：加深的话它看着还是"某一条刻度线"。 -->
     ${opt.zero==null?'':`<line x1="${L}" x2="${W-R}"
        y1="${Y(opt.zero).toFixed(1)}" y2="${Y(opt.zero).toFixed(1)}"
        stroke="rgba(91,156,240,.55)" stroke-width="1"/>`}
     ${series.map(s=>`<path d="${path(s)}" fill="none" stroke="${s.c}"
        stroke-width="${s.w||1.6}" stroke-linejoin="round"/>`).join('')}
     ${_marks()}
     <!-- hover 高亮：一条竖线 + 每条线上一个圆点 + 顶部日期。
          🔴 光有 tooltip 不够：鼠标在图上时**看不出读的是哪一天** ——
            折线密的时候差一两个像素就是差一天，而 tooltip 只在鼠标旁边，
            对不上图上的位置。 -->
     <g class="hov" style="display:none">
       <line class="hvl" x1="0" x2="0" y1="${T}" y2="${H-B}"/>
       ${series.map(()=>'<circle class="hvd" r="3.2"/>').join('')}
       <text class="hvt" y="${T-7}" text-anchor="middle"></text>
     </g>
     <rect id="hz" x="${L}" y="${T}" width="${W-L-R}" height="${H-T-B}" fill="transparent"/>
   </svg>
   ${opt.legend === false ? "" : `<div class="note" style="padding-left:8px">${series.map(s=>
      `<span style="color:${s.c}">━</span> ${s.n}`).join('　')}</div>`}`;
  // hover
  const svg=el.querySelector('svg'), tip=$('#tip');
  const hov=svg.querySelector('.hov'), hvl=hov.querySelector('.hvl'),
        hvd=[...hov.querySelectorAll('.hvd')], hvt=hov.querySelector('.hvt');
  svg.onmousemove=e=>{const r=svg.getBoundingClientRect();
    const i=Math.round((n-1)*Math.min(1,Math.max(0,((e.clientX-r.left)/r.width*W-L)/(W-L-R))));
    /* 高亮那一天：竖线对齐到数据点（不是鼠标位置）—— 对齐到鼠标的话
       读数与竖线会差一天，而"差一天"正是最难发现的那种错。 */
    const px=X(i);
    hov.style.display='';
    hvl.setAttribute('x1',px.toFixed(1)); hvl.setAttribute('x2',px.toFixed(1));
    series.forEach((sr,k)=>{const v=sr.v[i], c=hvd[k];
      if(v==null){ c.style.display='none'; return; }
      c.style.display=''; c.setAttribute('cx',px.toFixed(1));
      c.setAttribute('cy',Y(v).toFixed(1)); c.setAttribute('fill',sr.c);});
    hvt.setAttribute('x', Math.min(W-R-26, Math.max(L+26, px)).toFixed(1));
    hvt.textContent=opt.dates[i];
    /* ★ `opt.extra` = 额外读数（同一天的另一个量纲，如"累计金额"）。
         🔴 不画成第二条线：量纲不同共一根 y 轴必然有一条被压平，
           而"压平"看着像那条线没动。读数放 tooltip 里就够。 */
    tip.textContent=opt.dates[i]+'\n'+series.map(s=>s.n+'  '+
      (s.v[i]==null?'—':(opt.pctAxis?((s.v[i]-1)*100).toFixed(2)+'%'
        :opt.moneyAxis?_money(s.v[i],2)
        :opt.ratioAxis?(s.v[i]*100).toFixed(2)+'%'
        :s.v[i].toFixed(3)+'x'))).join('\n')
      +(opt.extra||[]).map(e=>'\n'+e.n+'  '
        +(e.v[i]==null?'—':(e.fmt?e.fmt(e.v[i]):e.v[i]))).join('');
    _tipAt(tip, e.clientX, e.clientY);};
  svg.onmouseleave=()=>{$('#tip').style.display='none';
    hov.style.display='none';};
}


/* 从「日期 + 权益」序列算出年/月/日三级收益 -> 纯函数，无副作用。
   ★ 回测与实盘**共用这一份**：算法一样（日收益 = eq[i]/eq[i-1]-1，
     区间收益 = 末值/前一日末值-1），只是数据来源不同（回测是组合权益、
     实盘是 TWR 净值）。两处各写一份的话，"同一段权益两个月收益"
     这种错不会报错。
   返回 {days:{d:r}, months:{'YYYY-MM':{ret,mdd,n}}, years:{'YYYY':{...}}}
   🔴 每个桶的起点用**桶外前一日**的权益 —— 用桶内首日的话，
     那一天的涨跌会被吞掉（月初/年初那天正是常有跳空的日子）。 */
function perfBuckets(dates, equity, base, pnls){
  const days={}, months={}, years={};
  /* 🔴 `base` = 序列的**起点值**（第一天之前那一刻）。
     不传的话首日没有前值 -> 当日收益记 `—`、月/年桶的起点用桶内首日，
     于是**第一天的涨跌整段丢掉**。回测那边 equity[0] 就是初始资金
     那天的收盘，历史上一直记 `—`（保持原行为，所以默认 null）；
     而实盘的 nav[0] 已经含了建仓当天的收益（TWR 从开户资金起算），
     所以实盘必须传 base=1.0 —— 不传就是把建仓日的盈亏丢掉，
     而那正是 CLAUDE.md 里「TWR 的起点是开户那一刻」修过的同一个坑。 */
  let prev=(base==null?null:base);
  for(let i=0;i<dates.length;i++){
    const d=dates[i], v=equity[i];
    if(v==null) continue;
    const ym=d.slice(0,7), y=d.slice(0,4);
    if(!(ym in months)) months[ym]={a:prev==null?v:prev,b:v,n:0,pk:0,mdd:0,p:0};
    if(!(y in years))   years[y]  ={a:prev==null?v:prev,b:v,n:0,pk:0,mdd:0,p:0};
    for(const o of [months[ym], years[y]]){
      o.b=v; o.n++;
      if(v>o.pk) o.pk=v;
      if(o.pk) o.mdd=Math.max(o.mdd, 1-v/o.pk);
      /* ★ 金额是**逐日相加**（可选参数 pnls）。不能用"桶末权益 − 桶初权益"
           —— 那含入金。回测那边不传 pnls，桶里就没有金额，行为不变。 */
      if(pnls) o.p += (pnls[i] || 0);
    }
    days[d]=prev==null?null:(prev?v/prev-1:null);
    prev=v;
  }
  const fin=o=>{const out={};
    for(const k in o) out[k]={ret:o[k].a?o[k].b/o[k].a-1:null,
                              mdd:o[k].mdd, n:o[k].n,
                              pnl:(pnls?o[k].p:null)};
    return out;};
  return {days:days, months:fin(months), years:fin(years)};
}


/* 回撤序列（负数，0 = 在最高点）—— 纯函数，便于单独验算。
   🔴 `base` = **起点资金**。峰值从它起算，不是从曲线第一个点起算 ——
     第一天就跌的话，只看曲线上的点会把那次下跌算成"没有回撤"
     （CLAUDE.md 里 perf.py 的 max_drawdown 修过同一个坑）。 */
/* 水下图里"在水面上"的那几段**不画** —— 回撤 = 0 表示刚创新高，那不是
   "深度 0 的水下"，而是**根本不在水下**。画成贴顶的实线会让人以为
   那段也有回撤（只是很小），而"没有回撤"和"回撤 0.0%"读起来是两回事。
   🔴 一处定义：回测详情页与实盘业绩页都得这么画，两处各写一份
     `v === 0 ? null : v` 的话，改一处漏一处不报错、只是两页长得不一样。
   ★ 断开就是语义本身：线断的地方 = 在水面上。 */
function drawdownSeries(equity, base){
  const out=[]; let pk=(base==null?-Infinity:base);
  for(const v of equity){
    if(v==null){ out.push(null); continue; }
    if(v>pk) pk=v;
    out.push(pk>0?-(1-v/pk):0);
  }
  return out;
}


/* ================= 方格热力图（回测详情页与实盘业绩页共用）=================
   ★ 搬到 shared 的判据同 lineChart：两处都要画年/月/日的收益方格，
     抄一份出来的话两页的深浅、图例、正负配色会慢慢不一致，而那不报错。 */

const _hcol=(v,k)=>{
  if(v==null) return 'transparent';
  const t=Math.min(1,Math.abs(v)*(k||3));
  const a=(0.15+Math.sqrt(t)*0.73).toFixed(3);
  return v>0?`rgba(168,44,44,${a})`:`rgba(24,116,82,${a})`;
};
/* 🔴 强度弱的格子底色近乎透明，**方向全靠这个 +/- 号** —— 不能省。 */
const _sn=(v,d)=>v==null?'—':(v>0?'+':'')+(v*100).toFixed(d);

function _legend(){
  const mk=v=>`<i style="background:${_hcol(v)}"></i>`;
  return `<div class="lgd">跌 ${[-.25,-.12,-.05,-.01].map(mk).join('')}
    <i style="background:transparent"></i>${[.01,.05,.12,.25].map(mk).join('')} 涨
    <span style="margin-left:10px">底色只表示方向与强度，正负看数字前的 +/- 号</span></div>`;
}

/* 自然日历方格（周一为第一列，非交易日打斜纹）。
   items: {'YYYY-MM-DD': {ret, pnl, ...}}   —— 没有这一天的键 = 非交易日
   fmt:   (o) => 格子里显示的字符串
   opt:   {scale, sel, head, note}
     head: (交易日数, 非交易日数) => 网格【上方】的 HTML（回测详情页的 h3 用它）
     note: false 时不追加下方那行计数（头里已经写过就别写第二遍）
   🔴 **这是日历网格的唯一一份实现**（2026-09-24 合并）：回测详情页的
     `drawCal` 与业绩页的收益明细共用它。此前两处各写一遍几何（lead 偏移 /
     pad 格 / 非交易日 / 星期表头 / 计数），连 `['一','二',…]` 那行都是两份 ——
     改一次 class 名（`.cd` -> `.cday`）就要动两个文件，**而漏一处不报错**，
     只是两页的日历从此长得不一样。
   🔴 格子是 `.cday` 不是 `.cd` —— 后者是「名称后的小字代码」（cnCell），
     两个东西共用过一个名字，于是日历的边框与手型光标被加到了全站每一个
     小字代码上（2026-09-24 改名，见 common.css 那段）。 */
function calGrid(ym, items, fmt, opt){
  opt=opt||{};
  const [Y,M]=ym.split('-').map(Number);
  const first=new Date(Date.UTC(Y,M-1,1));
  const ndays=new Date(Date.UTC(Y,M,0)).getUTCDate();
  const lead=(first.getUTCDay()+6)%7;
  let cells='', nt=0, ntd=0;
  for(let i=0;i<lead;i++) cells+='<div class="cday pad"></div>';
  for(let d=1;d<=ndays;d++){
    const ds=`${Y}-${String(M).padStart(2,'0')}-${String(d).padStart(2,'0')}`;
    const o=items[ds];
    if(o===undefined){
      cells+=`<div class="cday off"><div class="d">${d}</div></div>`; nt++; continue;
    }
    ntd++;
    cells+=`<div class="cday ${opt.sel===ds?'on':''}" data-d="${ds}"
      style="background:${_hcol(o.ret, opt.scale||12)}"
      title="${ds}${o.tip?'\n'+o.tip:''}">
      <div class="d">${d}</div><div class="v">${fmt(o)}</div></div>`;
  }
  const grid = `<div class="calg">${['一','二','三','四','五','六','日']
      .map(w=>`<div class="wd">${w}</div>`).join('')}${cells}</div>`;
  return (opt.head ? opt.head(ntd, nt) : '') + grid
    + (opt.note === false ? ''
       : `<div class="note">交易日 ${ntd} 天 / 非交易日 ${nt} 天</div>`);
}


/* ============ 分组柱状图（逐年/逐月这种【离散】量）============

   🔴 **离散量不画折线。** 逐年收益在 2016 与 2017 之间没有"过程"，
     折线会画出一段斜线，读的人会以为那是渐变 —— 与「断开 = 没有数据」
     同一条：图形语言本身就在说话，不要让它说错话。

   ★ 几何与 `lineChart` 完全一致（W/L/R），所以上下两块摆在一起时
     左右边界天然对齐、x 轴刻度同位（同「回撤副图不需要任何对位代码」）。
   ★ 柱子颜色按**系列**给（哪个配置），不按涨跌 —— 这类图存在的理由就是
     横向比几个配置；涨跌由柱子朝上还是朝下表达，信息没丢。            */
function barChart(el, cats, series, opt){
  opt = opt || {};
  const W = 1160, H = opt.h || 200, L = 54, R = 16, T = opt.t || 22, B = 26;
  const n = cats.length;
  if(!n){ el.innerHTML = '<div class="note">无数据</div>'; return; }
  let lo = 0, hi = 0;                       /* 🔴 0 必须在量程里 —— 柱状图的基线 */
  series.forEach(s => s.v.forEach(v => { if(v == null) return;
    if(v < lo) lo = v; if(v > hi) hi = v; }));
  if(!(hi > lo)){ hi = lo + 1; }
  const pad = (hi - lo) * 0.08; 
  /* 只往"有数据的那一侧"留白：全为负的回撤图不该在 0 上面空一截
     （那截白看着像"曾经为正"）。 */
  if(hi > 0) hi += pad;
  if(lo < 0) lo -= pad;
  const Y = v => T + (H - T - B) * (1 - (v - lo) / (hi - lo));
  const gw = (W - L - R) / n;               /* 每个分类占的宽度 */
  const m = series.length;
  const bw = Math.min(18, gw * 0.72 / m);   /* 单根柱子最大 18px —— 同 K 线
                                               MAXSTEP 那条：少数几年时不该
                                               拉成几个大色块 */
  const gx = i => L + gw * (i + 0.5);       /* 分类中心 */
  const bx = (i, k) => gx(i) - bw * m / 2 + bw * k;

  let ticks = [];
  for(let k = 0; k <= 4; k++) ticks.push(lo + (hi - lo) * k / 4);
  const yl = v => (v * 100).toFixed(0) + '%';
  el.innerHTML = `
   ${opt.title ? `<div class="ttl">${opt.title}</div>` : ''}
   <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
     ${ticks.map(v => `<line class="gl" x1="${L}" x2="${W - R}" y1="${Y(v).toFixed(1)}" y2="${Y(v).toFixed(1)}"/>
        <text class="ax" x="${L - 7}" y="${(Y(v) + 3).toFixed(1)}" text-anchor="end">${yl(v)}</text>`).join('')}
     <line class="zl" x1="${L}" x2="${W - R}" y1="${Y(0).toFixed(1)}" y2="${Y(0).toFixed(1)}"/>
     ${cats.map((c, i) => `<text class="ax" x="${gx(i).toFixed(1)}" y="${H - 8}"
         text-anchor="middle">${esc(c)}</text>`).join('')}
     ${series.map((s, k) => s.v.map((v, i) => {
        if(v == null) return '';
        const y0 = Y(0), y1 = Y(v);
        return `<rect x="${bx(i, k).toFixed(1)}" y="${Math.min(y0, y1).toFixed(1)}"
            width="${(bw - 1.5).toFixed(1)}" height="${Math.max(1, Math.abs(y1 - y0)).toFixed(1)}"
            fill="${s.c}" opacity="0.88"/>`;
      }).join('')).join('')}
     <g class="hov" style="display:none">
       <rect class="hvb" y="${T}" height="${H - T - B}"/>
     </g>
   </svg>
   ${opt.legend === false ? '' : `<div class="note" style="padding-left:8px">${
     series.map(s => `<span class="lg"><i style="background:${s.c}"></i>${esc(s.n)}</span>`).join('')}</div>`}`;

  /* hover：整组高亮 + 读数。★ 高亮的是**那一年**（整组），不是单根柱子
     —— 这一页问的是"这一年三个配置各是多少"，逐根 hover 要移三次。 */
  const svg = el.querySelector('svg'), tip = $('#tip');
  const hov = svg.querySelector('.hov'), hvb = hov.querySelector('.hvb');
  svg.onmousemove = e => {
    const r = svg.getBoundingClientRect();
    const i = Math.max(0, Math.min(n - 1,
      Math.floor(((e.clientX - r.left) / r.width * W - L) / gw)));
    hov.style.display = '';
    hvb.setAttribute('x', (L + gw * i).toFixed(1));
    hvb.setAttribute('width', gw.toFixed(1));
    tip.textContent = cats[i] + '\n' + series.map(s =>
      s.n + '  ' + (s.v[i] == null ? '—' : (s.v[i] * 100).toFixed(2) + '%')).join('\n');
    _tipAt(tip, e.clientX, e.clientY);
  };
  svg.onmouseleave = () => { $('#tip').style.display = 'none'; hov.style.display = 'none'; };
}

/* 横向条形图 —— 类目多的时候用它，不用 barChart。
   🔴 行业 IC 有 31 个类目：纵向的话每个只有 1160/31 = 37px，标签必然叠在
   一起（而那不报错，只是看不清）。
   ★ **不给 barChart 加一个方向开关**：它被「逐年对比」用着，改它的坐标系
   有回归风险；而"横着画"是另一种图，不是同一件事的两种写法
   （同「判断该不该合要看谁在用、当什么用」）。
   rows: [{n: 名字, v: 值, t: 悬浮说明}]，opt: {h, title, fmt, c} */
function hbarChart(el, rows, opt){
  opt = opt || {};
  const W = 1160, L = opt.L || 110, R = 60, RH = opt.rh || 20, T = 24;
  const n = rows.length;
  if(!n){ el.innerHTML = '<div class="note">无数据</div>'; return; }
  const H = T + RH * n + 10;
  let lo = 0, hi = 0;
  rows.forEach(r => { if(r.v == null) return;
    if(r.v < lo) lo = r.v; if(r.v > hi) hi = r.v; });
  if(!(hi > lo)) hi = lo + 1;
  const pad = (hi - lo) * 0.08;
  if(hi > 0) hi += pad;
  if(lo < 0) lo -= pad;
  const X = v => L + (W - L - R) * (v - lo) / (hi - lo);
  const fmt = opt.fmt || (v => (v == null ? '—' : v.toFixed(4)));
  /* 🔴 配色走 common.js 的 `upc`（一处定义）—— 自己写 `v >= 0 ? 涨色`
     会把【0 画成涨色】，等于凭空报了个涨（判据要 > 0 / < 0 两头夹）。 */
  const col = opt.c || (v => upc(v) || 'var(--dim)');
  let ticks = [];
  for(let k = 0; k <= 4; k++) ticks.push(lo + (hi - lo) * k / 4);
  el.innerHTML = `
   ${opt.title ? `<div class="ttl">${opt.title}</div>` : ''}
   <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" style="height:${H}px">
     ${ticks.map(v => `<line class="gl" y1="${T - 6}" y2="${H - 6}"
        x1="${X(v).toFixed(1)}" x2="${X(v).toFixed(1)}"/>
        <text class="ax" x="${X(v).toFixed(1)}" y="${T - 10}"
          text-anchor="middle">${fmt(v)}</text>`).join('')}
     <line class="zl" y1="${T - 6}" y2="${H - 6}"
        x1="${X(0).toFixed(1)}" x2="${X(0).toFixed(1)}"/>
     ${rows.map((r, i) => {
        const y = T + RH * i, x0 = X(0), x1 = X(r.v == null ? 0 : r.v);
        return `<text class="ax" x="${L - 8}" y="${(y + RH * 0.72).toFixed(1)}"
            text-anchor="end">${esc(r.n)}</text>
          <rect class="hbr" data-n="${esc(r.n)}" x="${(L - 4).toFixed(1)}"
            y="${(y + 1).toFixed(1)}" width="${(W - L - R + 8).toFixed(1)}"
            height="${(RH - 2).toFixed(1)}" fill="transparent"
            style="${opt.onPick ? 'cursor:pointer' : ''}">
            <title>${esc(r.t || (r.n + '  ' + fmt(r.v)))}</title></rect>
          <rect x="${Math.min(x0, x1).toFixed(1)}" y="${(y + 3).toFixed(1)}"
            width="${Math.max(1, Math.abs(x1 - x0)).toFixed(1)}"
            height="${(RH - 7).toFixed(1)}" fill="${col(r.v)}" opacity="0.85"
            style="pointer-events:none"/>
          <text class="ax" x="${(x1 + (r.v < 0 ? -6 : 6)).toFixed(1)}"
            y="${(y + RH * 0.72).toFixed(1)}"
            text-anchor="${r.v < 0 ? 'end' : 'start'}">${fmt(r.v)}</text>`;
      }).join('')}
   </svg>`;
  /* 🔴 整行都可点（不只是那根柱子）—— 值接近 0 的行柱子只有 1px 宽，
     只在柱子上绑的话那几行【点不中】，而它不报错。
     ★ 事件委托绑在 svg 上：内容是 innerHTML 整块换掉的。 */
  if(opt.onPick){
    const sv = el.querySelector('svg');
    sv.onclick = e => {
      const t = e.target.closest('.hbr');
      if(t) opt.onPick(t.dataset.n);
    };
  }
}
