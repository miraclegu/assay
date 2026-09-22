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
/* 🔴🔴 **代际计数：await 回来之前，人可能已经切到别的账户了。**
   `showLive` / `loadLive` 都是 `await` 取数再写 DOM。两发并发时
   **谁后回来谁赢** —— 于是出现「hash 指向账户 A、页面显示账户 B」，
   `LVSEL` 也被后回来那发覆盖，**而它不报错**。
   真实场景：在账户列表里连点两个账户，第二个的请求先回、第一个后回，
   就停在第一个上，地址栏却写着第二个，刷新一下才对。
   ★ 判据是**代际**不是 `location.hash`：`showLive` 也被非路由调用
     （建完账户、切「显示已归档」、收起侧栏），那几处 hash 根本没变。
   ★ 实测：selftest 里 1/5 复现（建完账户那一发后回来，把刚 goto 过去的
     账户覆盖掉，标题再也不变 -> 断言超时）。

   🔴 **两道的分工别记反**（同 realtime 里「锁是根本修复、唯一 tmp 名
     防跨进程」那条）：
       loadLive 那道  **根本修复** —— 变异测试当场抓到，堆栈精确指到
                      `$('#lvset').onclick` 那行
       showLive 那道  **冗余防御** —— 构造出真并发之后变异**没抓到**
                      （旧那发回来时页面并没有被盖回去）。保留它是因为
                      逻辑上它才是"页面显示哪个账户"的守门人，而
                      loadLive 只管 `#lvbody` 那一块；将来 showLive 里
                      多渲染点东西时，这道就不再是冗余的。
                      **没把它说成"抓到了"** —— 对数表里不许留没有判断的行。 */
let LVGEN = 0;
const CFK={deposit:'入金', withdraw:'出金', dividend:'分红到账', adjust:'调整'};

/* esc 复用文件上方那个（第 ~357 行）—— 不重复定义。 */
/* 这一发还算不算数。
   🔴 **光比代际是不够的**：`showLive` 也被【非路由】调用（建完账户、
     收起侧栏、切「显示已归档」、关掉某个浮层），那几发的 hash 没变 ——
     于是一发后到的重渲染可以把**路由要的那个账户**顶掉，结果是
     「hash 指向 A、页面显示 B」，**正是代际守卫本来要防的那件事**。
   ★ 2026-09-21 实测：那条 web 用例 60 轮挂 12 轮，现场一律是
     `hash='#/live/hongli'` 而 `LVSEL='a6'`、无异常 —— 建完账户那一发
     取到更大的代际先回来渲染，路由那一发回来时按设计作废了自己。
   🔴 所以真值只有一个：**hash 点名了具体账户时，就只有它算数**；
     hash 没点名（`#/live` 列表页）时才退回比代际。 */
function lvStale(gen, aid){
  const m = /^#\/live(?:\/([\w-]+))?/.exec(location.hash || '');
  if (m && m[1]) return m[1] !== aid;
  return gen !== LVGEN;
}


async function showLive(aid){
  const gen = ++LVGEN;
  stopPoll();
  enterView();
  let o;
  try{ o=await j('/api/live/accounts'+(LVALL?'?all=1':'')); }
  catch(e){
    if(lvStale(gen, aid)) return;      // 不是 hash 要的那一发，作废
    $('#main').innerHTML='<div class="none">实盘模块读取失败：'+esc(e)+'</div>'; return; }
  if(lvStale(gen, aid)) return;        // ← 同上：不许把别的账户盖上来
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
  /* 🔴🔴 **实盘与模拟盘分两个区**（2026-09-17，用户："希望模拟盘有一个单独的
       入口……或者说账户区域和实盘的分开也可以"）。

     ★ **没有做成顶栏第 8 个入口**，三条理由：
       ① 模拟盘与实盘是**同一个页面、同一套 hash 路由**（`#/live/<id>`）——
          多一个顶栏入口的话，打开模拟盘账户时那两个入口该亮哪个？
          而顶栏高亮本来就是回答"我在哪"的（同「子页要点亮父级」那条）；
       ② 顶栏是按**"今天要做什么"**分组的，而模拟盘不是一件独立的事，
          它是**另一批账户**；
       ③ 实盘与模拟盘要**互相对照**（同一策略，人执行 vs 引擎执行），
          分成两页那个对照就要来回切。
     🔴 **建账户的 `mode` 从"勾选框"变成"在哪个区里点的新建"** ——
       `mode` **建好之后不能改**（同一本账混着真实成交与引擎成交就说不清了），
       所以那个勾选框是最容易填错、且代价最大的一处。
       少一个能填错的概念（同「刻意删掉『这个率含不含规费』开关」那条）。
     🔴 **空的那一区照样显示**（带一句说明 + 新建入口）—— 藏起来的话，
       从没建过模拟盘的人根本不知道有这功能（同「合并的风险是把功能藏起来」）。 */
  /* 说明放**名称下方的小字**，不另占一列 —— 它是"这只账户是干什么的"、
     属于名称的注解（同盘面榜单把「行业」放名称下方那条）。
     一行截断，全文进 `title`：说明可长可短，摆进主行会把「N 只」挤走。 */
  const item=a=>`<a class="ditem${a.id===LVSEL?' on':''}${a.archived?' miss':''}"
      href="#/live/${a.id}" title="${esc([a.broker_note,
          a.alert?(a.alert_why||[]).join('；'):'', a.archived?'已归档':'']
        .filter(Boolean).join(' —— '))}">
      <span>${dot(a)} ${esc(a.name)}${a.broker_note
        ? `<i class="dnote">${esc(a.broker_note)}</i>` : ''}</span>
      <span class="dmeta">${a.n_positions} 只</span></a>`;
  /* 「是不是模拟盘」**读服务端给的布尔**，不在前端比 `mode` 字符串 ——
     老账户根本没有 `mode` 这个字段（早于那次改动），比字符串就得记住
     "undefined 也算实盘"这条隐含规则。 */
  /* 🔴 **分组后的顺序只算一份**，展开态与收起态共用（`ORD`）——
     rail 原来直接 `o.accounts.map`（账本原始顺序），于是"先建模拟盘再建实盘"
     的账本上**两态的账户顺序不一样**：展开是「实盘…模拟盘」、收起是
     「模拟盘…实盘」，收起再展开同一个账户跳到另一个位置。
     实测构造验过（把模拟盘挪到 accounts.json 最前面即可复现），
     **而它不报错**（同「两处各写一份迟早分叉」那条）。 */
  const real=o.accounts.filter(a=>!a.paper), papr=o.accounts.filter(a=>a.paper);
  const ORD=real.concat(papr);
  const zone=(key,ttl,hint,list)=>`<div class="dzone" data-zone="${key}">
      <div class="dgrp dzh"><span style="flex:1">${ttl}</span>
        <a href="#" class="lvwhy znew" data-zone="${key}">+ 新建</a></div>
      ${list.length?list.map(item).join(''):`<div class="dzempty">${hint}</div>`}
      <div class="nfwrap" id="nf_${key}"></div></div>`;
  const tabs=zone('live','💰 实盘账户',
      '还没有实盘账户 —— 成交由你录入，持仓与收益由流水推导。', real)
    + zone('paper','🧪 模拟盘账户',
      '还没有模拟盘 —— 成交由<b>引擎</b>按绑定策略跑出来，数据更新后自动推进到最新；'
      + '页面与实盘完全一样，只是账上的钱是推演出来的。', papr);
  /* 收起态：一条 44px 的轨，每个账户一个方块（名称首字）+ 角上的告警点。
     "收起后就看不到该干什么了"的收起功能不如不做。 */
  const rail=`<div class="drail">
      <a href="#" class="dchip" id="lvunfold" title="展开账户列表">›</a>
      ${ORD.map((a,i)=>`${(i && a.paper && !ORD[i-1].paper)?'<i class="dsep"></i>':''}<a class="dchip${a.id===LVSEL?' on':''}${a.paper?' paper':''}"
        href="#/live/${a.id}"
        title="${esc(a.name)}${a.paper?'（模拟盘）':''}${a.alert?' —— '+esc((a.alert_why||[]).join('；')):''}"
        >${esc((a.name||'?').trim().slice(0,1))}${dot(a)}</a>`)
        /* 🔴 收起态也要看得出哪个是模拟盘 —— 收起来就分不出的话，
           "把推演当成真金白银"只是时间问题（同那个紫色标签的理由）。
           这里靠**紫色边**，不另加文字：轨只有 44px 宽。 */
        .join('')}
    </div>`;
  $('#main').innerHTML=head+`<div id="dk" class="${LVFOLD?'fold':''}">
    ${LVFOLD?rail:`<div class="dside">
      <div class="dgrp" style="display:flex;align-items:baseline;gap:6px">
        <span style="flex:1">账户${LVALL?'（含已归档）':''}</span>
        <a href="#" id="lvfold" class="lvwhy" title="收起到左边">‹ 收起</a></div>
      ${tabs}
      <div class="lvform" style="padding:2px 8px;gap:10px">
        <a href="#" id="lvall" class="lvwhy">${LVALL?'只看在用的':'显示已归档的'}</a>
      </div>
      <div class="dhint" style="margin-top:12px">成交流水<b>只追加</b>，持仓由它推导。<br>
        策略版本绑定后源码<b>永久留痕</b>。</div>
    </div>`}
    <div class="dbody" id="lvbody"></div></div>`;
  const fold=v=>{ LVFOLD=v; localStorage.setItem('lvfold', v?'1':'0'); showLive(LVSEL); };
  if($('#lvfold')) $('#lvfold').onclick=ev=>{ ev.preventDefault(); fold(true); };
  if($('#lvunfold')) $('#lvunfold').onclick=ev=>{ ev.preventDefault(); fold(false); };
  if($('#lvall')) $('#lvall').onclick=ev=>{ ev.preventDefault(); LVALL=!LVALL; showLive(LVSEL); };
  /* 建账户表单**长在它所属的那个区里**，`mode` 由区决定 ——
     表单里没有"这是模拟盘吗"这个选项，也就没法填错。 */
  document.querySelectorAll('.znew').forEach(e=>{ e.onclick=ev=>{
    ev.preventDefault();
    const z=e.dataset.zone, box=$('#nf_'+z);
    if(box.innerHTML){ box.innerHTML=''; return; }          /* 再点一次收起 */
    document.querySelectorAll('.nfwrap').forEach(x=>x.innerHTML='');  /* 只开一个 */
    const paper = z==='paper';
    box.innerHTML=`<div class="nform">
      <div class="lvform" style="padding:0">
        <input class="nn" placeholder="${paper?'模拟盘名称':'账户名称'}" style="flex:1;min-width:100px">
        <!-- 🔴 **单位要写在框里**：原来只写"初始资金"，2026-09-18 用户
             填了 40（想填 40 万），于是一手都买不起、推演出 0 笔成交、
             页面一片空白。少一个能填错的地方，比事后解释便宜。 -->
        <input class="nc" size="9" placeholder="初始资金（元）">
        <button class="btn nb" ${o.readonly?'disabled':''}>建${paper?'模拟盘':'实盘账户'}</button>
      </div>
      <!-- ★ 说明在**建的时候**就能填：建完再去 ⚙ 设置里找的话多半不会填
           （现有四个账户的说明全是空的，就是这么来的）。留空也行，不强制。 -->
      <div class="lvwhy ncwhy" style="padding:2px 0 0;line-height:1.7"></div>
      <div class="lvform" style="padding:4px 0 0">
        <input class="nd" style="flex:1;min-width:160px" placeholder="${paper
          ? '说明（选填）：在验证什么、和哪个实盘账户对照'
          : '说明（选填）：哪个券商、跑什么策略、本金多少'}"></div>
      ${paper?`<!-- 🔴 起点在【建的时候】设：建完再改的话，若已经推演过就
           必须先「重建」（换起点会让整段账本对不上）—— 那时人已经看过
           一遍数据了。留空 = 从今天起。 -->
      <div class="lvform" style="padding:4px 0 0;align-items:center">
        <span class="lvwhy" style="white-space:nowrap">推演起点</span>
        <input class="ns" type="date" style="min-width:130px">
        <span class="lvwhy">留空 = 从今天起。设成历史某天的话，
          建好绑上策略就会一路推演到最新数据日。</span></div>`:''}
      <div class="lvwhy" style="padding:4px 0 0">
        ${paper?'成交由<b>引擎</b>按绑定策略跑出来，不用人录 —— 建好之后去「策略」绑一个版本，它会自动推进到最新数据日。<br>':''}
        id 自动分配（下一个 <code>${esc(o.next_id||'a1')}</code>），
        它是内部主键、决定目录名、<b>不可改</b>；名称随时可改。<br>
        🔴 <b>实盘 / 模拟盘建好之后不能改</b> —— 同一本账里混着真实成交与引擎
        成交，之后就说不清哪一段是真的了。要换请新建一个账户。
      </div>
      <div class="lvmsg nmsg"></div></div>`;
    const nn=box.querySelector('.nn'); nn.focus();
    /* ★ 本金太小**当场说一句**，不拦 —— 40 元的模拟盘也是合法的
       （只是没意义），硬拦会挡住"我就想试试"。但不说的话人不会发现
       自己少打了三个零（同「悄悄截断比查不出来更糟」那条的精神）。 */
    const nc=box.querySelector('.nc'), ncw=box.querySelector('.ncwhy');
    nc.oninput=()=>{ const v=parseFloat(nc.value||'0');
      ncw.innerHTML = (v>0 && v<10000)
        ? `🔴 ${num(v,0)} 元买不起一手 —— A 股一手 100 股，常见的票一手要
           几千到几万元。填这个数的话推演出来会是<b>零笔成交</b>。
           是不是想填 ${num(v*10000,0)}（${num(v,0)} 万）？`
        : ''; };
    box.querySelector('.nb').onclick=async()=>{
      const msg=box.querySelector('.nmsg');
      const h0 = location.hash;   // 见下：await 回来时人可能已经走开
      try{
        const r=await post('/api/live/save',{
          name:nn.value.trim(), init_cash:parseFloat(box.querySelector('.nc').value||'0'),
          broker_note: box.querySelector('.nd').value.trim(),
          paper_start: paper ? (box.querySelector('.ns').value || '') : undefined,
          mode: paper?'paper':'live'});
        msg.className='lvmsg ok'; msg.textContent='已建';
        /* 🔴 **走 hash**，不直接 showLive —— 否则"页面显示哪个账户"又有了
           第二个真值，而 hash 还停在上一个（那正是上面那条竞态的成因）。
           🔴 但**只有人没自己走开时才跳**：这一发是 await 回来的，
             期间人可能已经点开别的账户了 —— 无条件跳就是把竞态从
             "渲染"搬到"导航"（2026-09-21 实测：现场从
             `hash=hongli 显示 a6` 变成了 `hash 被顶回 a6`）。
             走开了就不跳，新账户下次列表刷新自然有。 */
        if(location.hash === h0) location.hash = '#/live/' + r.account.id;
      }catch(err){ msg.className='lvmsg bad'; msg.textContent=String(err); }
    };
  };});
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
/* 今天卖掉的那几笔 —— **必须列出来**。
   🔴 当日盈亏的合计现在含"当天卖出已实现"，于是它**必然不等于持仓表
     各行之和**（那张表里已经没有卖掉的那只了）。不把这几行摆出来的话，
     屏幕上就是一个对不上的合计，而人只会以为是算错了
     （同「删了要留痕，否则『空』与『本来就没有』分不出来」）。 */
/* 🔴 这两个函数是**模块级**的，所以里面不能用 `sgn` / `col` ——
   它们是 `kpiHtml` / `posHtml` 那几个函数里的 **const 局部**。
   第一版就这么写了，结果 `sgn is not defined`，而它**只在控制台报**，
   页面卡在"读取中…"（同 `money is not defined` 那次、同 `geo` 那次：
   回调里的异常不让页面报错，只让它少做一半）。
   这里一律用 common.js 的全局件（`upc` / `num` / `esc`）。 */
const _lvsg = v => (v == null ? '' : (v >= 0 ? '+' : ''));

function lvSoldToday(P){
  const rs = P.sold_today || [];
  if(!rs.length) return '';
  return '<div class="lvwhy" style="margin-top:8px">今天卖出（已计入当日盈亏）：'
    + rs.map(x=>'<b>' + esc(x.name||x.code) + '</b> '
        + num(x.shares) + ' 股 @ ' + num(x.price,3)
        + (x.preclose!=null ? '（昨收 ' + num(x.preclose,3) + '）' : '')
        + ' <span style="color:' + upc(x.pnl_day) + '">'
        + _lvsg(x.pnl_day) + num(x.pnl_day,2) + '</span>'
      ).join('　·　')
    + (P.same_day_sell ? '　·　⚠ 其中 ' + num(P.same_day_sell)
                         + ' 股是当天买当天卖（补录？基准用买入价）' : '')
    + '</div>';
}

/* 当日盈亏的构成：持仓浮动 / 当天卖出已实现 / 当天费用。
   ★ 只在真有后两项时才展开 —— 没有卖出也没费用的日子，
     合计就等于持仓表各行之和，多写一行是噪声。 */
/* 副标题**只给一个百分比**（用户 2026-09-22 定：「我只需要一个总资产的
   百分比就行，后面也不要加总资产这 3 个字」）。
   ★ 分母是【总资产】：口径已经是账户当日全口径了，还按持仓市值算的话，
     有卖出的日子分母里少了卖掉的那部分，百分比会偏大，而它不报错。
   🔴 构成（持仓 / 已实现 / 费用）挪进这一格的 `title` —— 合计必然不等于
     持仓表各行之和，那个差额的**正式解释**是表下面那行「今天卖出」，
     title 只是顺手能 hover 到。**不占主视图**（同「能进 tooltip 的就别占列」）。 */
function lvDayPct(P){
  if(P.pnl_day==null || P.equity==null) return '';
  const base = P.equity - P.pnl_day;
  if(!base) return '';
  return _lvsg(P.pnl_day/base) + (P.pnl_day/base*100).toFixed(2) + '%';
}

function lvDayTitle(P){
  if(P.pnl_day==null) return '';
  const r=P.pnl_day_realized||0, f=P.pnl_day_fee||0;
  if(!r && !f) return '';
  const t=[];
  if(P.pnl_day_hold!=null) t.push('持仓 '+_lvsg(P.pnl_day_hold)+num(P.pnl_day_hold,2));
  if(r) t.push('当天卖出已实现 '+_lvsg(r)+num(r,2));
  if(f) t.push('当天费用 '+num(f,2));
  return ' title="当日盈亏 = ' + esc(t.join(' + ').replace('+ 当天费用 -','− 当天费用 '))
         + '"';
}

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

/* 模拟盘「重建」= 删档重开：删掉引擎推演出来的成交（`source=='paper'`），
   按当前设置（本金 / 起点 / 绑定版本 / 参数）重跑一遍。

   🔴 **手工补录的那几笔不动** —— 判据在 `lv/paper.py` 的 `reset` 一处
     （它只删 `source == 'paper'` 的行），页面不重复实现。
   🔴 **服务端要求显式 `confirm`** —— 手滑点一下就把账本里那几十笔删了，
     而那是看过的东西。所以这里必须 confirm 一次，而且要**说清代价**。
   ★ 一处定义、两个入口共用（0 笔那条警告 / 对账不一致那个浮层）——
     各写一份的话措辞与 `confirm:true` 这个必传项迟早分叉。 */
async function paperRebuild(aid){
  const n=((LVO||{}).paper||{}).n_fills;
  if(!confirm('重建会删掉这个模拟盘推演出来的成交'
      + (n ? '（当前 '+n+' 笔）' : '')
      + '，然后按当前的本金 / 起点 / 绑定版本重跑一遍。\n'
      + '手工补录的那几笔不会被删。要继续吗？')) return;
  const m=$('#lvmsg');
  if(m){ m.className='lvmsg'; m.textContent='重建中…（从起点重放一遍）'; }
  try{
    const r=await post('/api/live/paper',{id:aid, act:'reset', confirm:true});
    await loadLive(aid);
    const m2=$('#lvmsg');
    if(m2){ m2.className='lvmsg ok';
      m2.textContent='重建完成 —— 推进到 '+(r.advanced_to||'—')
        +'，成交 '+(r.n_fills!=null?r.n_fills:'—')+' 笔'; }
  }catch(e){
    const m2=$('#lvmsg');
    if(m2){ m2.className='lvmsg bad'; m2.textContent=String(e); } else alert(String(e));
  }
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
  const gen = LVGEN;
  let o; try{ o=await j('/api/live/account?id='+encodeURIComponent(aid)); }
  catch(e){
    /* ★ 轮询失败**不要**把已经渲好的内容换成错误信息：网络抖一下就把
       持仓表清掉、下一轮又回来，比不刷新更糟。安静地跳过这一轮。 */
    if(!quiet) b.innerHTML='<div class="none">'+esc(e)+'</div>';
    return;
  }
  /* 🔴 **await 期间人可能已经走开了。** `b` 是在 await **之前**取的，
     而这几百毫秒里 hash 可能已经切到别的账户 / 别的视图 —— 那时
     `#lvbody` 已被换成新的一个，往旧的 `b` 上写等于**写进一个脱离文档的
     节点**，紧接着 `$('#lvset').onclick` 就是 **null** 而抛
     `Cannot set properties of null` —— **只在控制台里报**，页面看着正常
     （同 `renderChart` / `lprBar` 往 null 写那次）。
     实测：selftest 快速切账户时**偶发**，连跑三次才复现一次。
     ★ 判据用**容器还是不是同一个**，不是 `location.hash` ——
       同一个 hash 下也会重渲染（换区间 / 刷新），而"这个容器还在不在"
       是当下的事实。 */
  /* 两道一起：容器被换掉了（切走了），或者**期间有更新的一发**
     —— 后者容器判据挡不住（同一个 `#lvbody` 上两发 loadLive 并发，
     旧的后回来会拿旧数据盖掉新数据）。 */
  if($('#lvbody') !== b || lvStale(gen, aid)) return;
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
  /* 「这个账户是干什么的」—— 它是标题的注解，所以**独占一行、紧跟标题**，
     不挤进 `.lvhead` 那一排（那排已经有名称/模拟盘标签/数据日/策略/三个按钮，
     同「能进 tooltip 的就别占列」）。
     🔴 **没有说明时整行不渲染** —— 留一句"（未填写）"就是常驻噪声
     （同「常驻一条『一切正常』的横幅等于教人忽略这个位置」）。
     入口指向 ⚙ 设置，否则人看得见却不知道去哪改。 */
  const noteHtml = a.broker_note
    ? `<div class="lvnote">${esc(a.broker_note)}</div>`
    : '';
  /* 🔴🔴 **推进"成功"但一笔成交都没有 -> 必须说出为什么。**
     2026-09-18 用户报「选了起始时间、也推进了，但是没有任何数据出现」——
     那次是本金填了 40（元），一手都买不起，引擎记了 30 条
     「资金不足一手」拒单，而链条断在 `advance` 没把它带出来。
     ★ 用**警告样式**不是 ⓘ：它要人去做事（改本金再重建），
       而「警告不许进 ⓘ」—— 藏起来等于没有。
     ★ 空着时整块不渲染（同说明那条）：常驻一条"一切正常"等于教人
       忽略这个位置。 */
  const emptyHtml = (a.paper && (o.paper||{}).why_empty)
    ? `<div class="lvwarn"><b>这个模拟盘推进完之后一笔成交都没有</b><br>
        ${esc(o.paper.why_empty)}
        ${(o.paper.rejects||[]).length ? `<br><span class="lvwhy">引擎的拒单原因：${
          o.paper.rejects.map(r=>`${esc(r.why)} × ${r.n}`).join('、')}</span>` : ''}
        <!-- 🔴 **说了下一步就得给入口。** 提示里写着"改大初始资金再重建"，
             而页面上原来**没有重建按钮**（前端从来没调过 act=reset）——
             那就是「说了不能做却不给出路」，同 backLink 那条的反面。
             🔴 **旧状态（没记原因）那一支不给这两个按钮** —— 那时我们
             并不知道为什么 0 笔，摆一个「改初始资金」在那儿就是在
             **暗示原因是本金**，而它可能根本不是（候选池空 / 没有调仓日
             长得一模一样）。那一支的下一步是上面的「▷ 推进」。 -->
        ${o.paper.stale_state ? '' : `<div style="margin-top:8px">
          <button class="btn" id="lvsetc" ${ro?'disabled':''}>⚙ 改初始资金</button>
          <button class="btn" id="lvrb" ${ro?'disabled':''}>↻ 重建</button>
          <span class="lvwhy">重建 = 删掉推演出来的成交、按当前设置重跑
            （手工补录的那几笔不动）</span></div>`}
       </div>`
    : '';
  const it=P.items||[];
  const sgn=x=>x==null?'':(x>=0?'+':'');
  const col = upc;
  b.innerHTML=`
  <div class="lvhead">
    <h2>${esc(a.name)}</h2>
    ${paperTag(o)}
    <span id="lvday">${dataDayTag(o)}</span>
    ${a.code_sha256?`<a class="lvtag on" href="#" id="lvstrat"
        title="源码 / 参数 / 版本历史 / 用这个版本跑过的回测">${esc(a.strategy_path.split('/').pop())}
        @${esc(a.code_sha256.slice(0,8))} ›</a>`
      :`<span class="lvtag" title="这个账户不跑策略：成交自己录，没有信号与待办。
随时可以绑一个策略，绑了就有了。">手工账户</span>
        <a class="lvtag" href="#" id="lvstrat">绑定策略 ›</a>`}
    <span style="flex:1"></span>
    <!-- ★「立即重算」不在这一排：这排是**账户级**动作（记一笔/业绩/设置），
         而重算算的是**调仓信号** —— 它属于「今日待办」那一块，
         按钮就该长在它作用的那块里。挪过去了（见 sigHtml）。 -->
    <button class="btn" id="lvrec" ${ro?'disabled':''}>✎ 记一笔</button>
    <!-- 🔴 业绩页的**正式入口**（用户 2026-09-22：「点击累计收益，进去的其实
         不仅仅是累计收益，是一个综合的面板」）。原来唯一的入口是 KPI 板里那个
         「累计收益 ›」链接 —— **入口的名字说不出里面是什么**。
         🔴 同一天用户又把「流水」与「选股理由」两个独立页并进了那一页
         （"和业绩里的交易记录有所重复""执行差异都已经在里面了"），
         于是这一排只剩**要做的事**：记一笔（录）· 业绩（看）·〔推进〕· 设置。
         ★ 两个旧 hash 仍然可达（app.js 里 redirect），书签一个没失效。 -->
    <a class="btn" href="#/live/${a.id}/perf" id="lvperf"
       title="业绩曲线 / 业绩明细 / 每日持仓 / 交易明细 / 清仓记录 / 盈亏榜 / 选股理由 / 执行差异">📊 业绩</a>
    ${a.paper?`<button class="btn" id="lvadv" ${ro?'disabled':''}
       title="按绑定策略跑到最新数据日，把新成交写进账本（幂等，没新交易日就什么都不做）"
       >▷ 推进</button>`:''}
    <button class="btn" id="lvset">⚙</button>
  </div>
  ${noteHtml}${emptyHtml}
  <div class="lvmsg" id="lvmsg"></div>
  <div id="lvkpi">${kpiHtml(o)}</div>
  ${sigHtml(sig, o.alert, o.alert_why, !!a.code_sha256)}
  <div class="lvsec"><h3>当前持仓
      ${it.length?`<span class="lvwhy">${it.length} 只</span>${posHelp(P)}
        ${Object.keys((LVWHYH||{}).entry||{}).length
          ?'<span class="lvwhy">名称后的 <b>?</b> 进它建仓那一期的选股理由</span>':''}`:''}</h3>
    ${it.length?`<div class="pw"><table class="lvt lvpos">
      <tr>${LVPOS_COLS.map(c => LVSORT_COLS[c.k] ? lvSortTh(c.k, LVSORT_COLS[c.k])
        : `<th class="${c.tx ? 'tx' : 'rt'}">${esc(c.t || LVPOS_TH[c.k] || c.k)}</th>`).join('')}</tr>
      ${lvSortRows(it).map(x=>`<tr>${LVPOS_COLS.map(c =>
        c.rt ? lvRtd(c.k, x, P) : lvPosTd(c, x, aid)).join('')}</tr>`).join('')}
      </table></div>
      <!-- 持仓表包在 .pw 里，见本文件顶部注释 -->
      ${lvSoldToday(P)}
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
  /* 🔴 **按钮照样可点，点了把原因和下一步说清楚** —— 项目纪律：
     一律不设 `disabled`（disabled 的元素连 title 都不触发，
     "点了没反应"是最难查的那种坏）。
     规则本身在服务端 `pos.add_fill` 一处判（页面能绕过）。 */
  $('#lvrec').onclick=()=>{
    if(a.paper && a.code_sha256){
      modal(`<h3>这个模拟盘绑了策略，成交由引擎产生</h3>
        <div class="lvwhy" style="line-height:1.9">
          <!-- 🔴 HTML 里渲染不了 markdown 的星号，要用 <b> ——
               同「desc 里不写 markdown 星号」那条（那次是 title 属性）。 -->
          手工录的那几笔引擎不会跑出来，<b>下一次「▷ 推进」会整段对不上账</b>
          —— 而那时报的原因是"数据被修正过"，指不到真正的原因。<br><br>
          · 想让引擎跑 -> 点上面的 <b>▷ 推进</b><br>
          · 想自己手工推演 -> 先在 <b>${esc((a.strategy_path||'策略').split('/').pop())}</b>
            那里<b>解绑策略</b>，这个模拟盘就变成"手工模拟盘"<br>
          · 两种都想要 -> 建两个模拟盘（账本混在一起就说不清哪笔是谁的）
        </div>
        <div style="margin-top:12px"><button class="btn" id="rcok">知道了</button>
        <a class="btn" href="#" id="rcstrat">去解绑策略 ›</a></div>`, () => {
        $('#rcok').onclick=()=>{ const e=$('#stwrap'); e.className=''; e.innerHTML=''; };
        $('#rcstrat').onclick=ev=>{ ev.preventDefault();
          const e=$('#stwrap'); e.className=''; e.innerHTML='';
          openStrat(aid, a.code_sha256||''); };
      });
      return;
    }
    openRecord(aid, sig, ro);
  };
  /* 模拟盘手动推进。★ 自动那条挂在 tick_daily（数据更新后），这里是
     "我现在就想看看"的入口 —— 两者调的是同一个接口，不是两套逻辑。
     🔴 innerHTML 之后才存在的元素要在这里绑，不是渲染开头
       （对比页「移除」栽过：点了没反应且不报错）。 */
  if($('#lvadv')) $('#lvadv').onclick=async()=>{
    const b=$('#lvadv'), old=b.textContent;
    b.textContent='推进中…';
    try{
      const r=await post('/api/live/paper',{id:aid});
      /* 🔴 对账不一致时**不刷新成"好像成功了"** —— 说清楚再让人决定。 */
      if(r.mismatch){
        /* 🔴 **硬拒要给出路** —— 原来只弹一句"账本没有被改动"就完了，
           人在页面上没有任何办法继续（同「硬拒而不给出路最后会变成
           绕过整个入口」那条）。现在把「重建」摆在这里。 */
        modal(`<h3>重跑结果与账本对不上（到 ${esc(r.mismatch.until)}）</h3>
          <div class="lvwhy" style="line-height:1.9">${esc(r.mismatch.why)}<br><br>
            账本里 ${r.mismatch.n_ledger} 笔、重跑出 ${r.mismatch.n_rerun} 笔。
            <b>账本一个字节都没动。</b></div>
          <div style="margin-top:12px">
            <button class="btn" id="mmno">先不动</button>
            <button class="btn" id="mmrb">↻ 重建（删档重开）</button></div>`, () => {
          $('#mmno').onclick=()=>{ const e=$('#stwrap'); e.className=''; e.innerHTML=''; };
          $('#mmrb').onclick=()=>{ const e=$('#stwrap'); e.className=''; e.innerHTML='';
            paperRebuild(aid); };
        });
      }
      await loadLive(aid);
    }catch(e){ b.textContent=old; alert(String(e)); }
  };
  /* ★ 两个入口（0 笔警告里、对账不一致浮层里）走**同一条**重建链 ——
     各写一份的话 confirm 的措辞与 `confirm:true` 这个必传项迟早分叉。 */
  /* ★ 「手工账户」那块里的「绑定策略 ›」与顶栏那个是**同一条链** ——
     各写一份的话迟早一个能开一个不能（同 `paperRebuild` 那条）。 */
  if($('#lvbind2')) $('#lvbind2').onclick=ev=>{
    ev.preventDefault(); openStrat(aid, a.code_sha256); };
  if($('#lvrb')) $('#lvrb').onclick=()=>paperRebuild(aid);
  if($('#lvsetc')) $('#lvsetc').onclick=()=>openSettings(aid, a, ro);
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
           /* 🔴 2026-09-22 改口径：这一格现在是【账户当日全口径】——
                持仓浮动 + 当天卖出已实现 − 当天费用，与业绩板那格「今日」
                是**同一个数**（那边给百分比、这边给金额与构成）。
                改之前它只算持仓浮动，于是当天卖掉的那部分一分都不算 ——
                用户 2026-09-22 报：开盘竞价卖出 300980 的涨幅没进来，
                实测漏了 +460.00（而跌着卖会把当日盈亏**报高**）。
              ★ 副标题给【构成】而不是百分比：合计必然不等于持仓表各行之和
                （多了已实现、少了费用），不写出来就看着像算错了。
                只在真有已实现或费用时才写 —— 常驻一行噪声等于没写。 */
           lvDayPct(P), '', lvDayTitle(P))}
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
      /* 🔴 这一格**不再是业绩页的入口**（2026-09-22）：那一页有六个页签，
           叫它"累计收益"等于给destination起了个错名字。正式入口是上面那排
           的「📊 业绩」按钮 —— 名字说得出里面是什么。
         ★ 这里只留数字与口径 ⓘ，不留一个半对的链接（同「一个能点的东西
           被画成标签」的反面：一个链接的名字必须说得出它去哪）。 */
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

/* 🔴 **持仓表的列写在这一处**，表头与单元格都读它。
   改之前是两处：表头照 `LVSORT_COLS` 的顺序拼、单元格照 `LV_RT_FIELDS`
   的顺序拼，中间还夹着几个静态列 —— 想把「成本」插到「浮盈」与「现价」
   之间，就得同时改两处顺序，**漏一处就是整表错位一格，而它不报错**
   （同「★ 选中的规则」那条：列定义写一处，表头/排序/单元格都读它）。

   列序是用户 2026-09-16 定的：
     名称 · 当日 · 当日盈亏 · 幅度 · 浮盈 · 成本 · 现价 · 股数 · 市值 · 仓位 · 建仓
   —— 先回答"今天怎么样"，再回答"这笔是什么成本、多大规模"。 */
const LVPOS_COLS = [
  {k: 'name', t: '名称', tx: 1},
  {k: 'chg_day', rt: 1}, {k: 'pnl_day', rt: 1},
  {k: 'pnl_pct', rt: 1}, {k: 'pnl', rt: 1},
  {k: 'cost', t: '成本'},
  {k: 'price', rt: 1},
  {k: 'shares', t: '股数'},
  {k: 'value', rt: 1}, {k: 'weight', rt: 1},
  {k: 'entry', t: '建仓', tx: 1},
];

/* 一行的静态格（实时那几个走 `lvRtd`，它是刷新与渲染的唯一定义）。 */
function lvPosTd(c, x, aid) {
  switch (c.k) {
    case 'name':
      /* 名称 + 小字代码**同一格**（`cnCell`，全站唯一定义）——
         原来代码与名称各占一列，而这张表已经十来列了。 */
      return `<td class="tx">${cnCell(x.code, x.name)}${lvHoldMark(aid, x.code)}</td>`;
    case 'cost':
      return `<td class="rt" title="摊薄成本（含买入费 ${num(x.buy_fee, 2)}）—— 浮盈按它算。&#10;成交均价 ${num(x.cost, 4)}（引擎 entry_price 用的是这个，不含费）。&#10;保本价 ${x.breakeven == null ? '—' : num(x.breakeven, 3)}（含估算卖出费 ${x.exit_fee_est == null ? '—' : num(x.exit_fee_est, 2)}）">${num(x.cost_net, 3)}</td>`;
    case 'shares': return `<td class="rt">${num(x.shares)}</td>`;
    case 'entry': return `<td class="lvwhy tx">${esc(x.entry)}</td>`;
  }
  return '';
}
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

/* 不参与排序的实时列（现价）也要有表头 —— 它在 LVSORT_COLS 里没有条目。 */
const LVPOS_TH = {price: '现价'};

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

/* 模拟盘标记 + 推进状态。**必须一眼看得出这是模拟盘** ——
   这一页上所有数字（持仓、浮盈、累计收益）长得和实盘一模一样，
   不标的话把推演当成真金白银只是时间问题。
   ★ 顺带给出「推进到哪天」与对账差额：
     🔴 差额不进 ⓘ —— 那是"这个数可能不对"，而 ⓘ 只放"这个数怎么算的"
       （同「警告不许进 ⓘ」那条）。 */
function paperTag(o){
  const a=o.account||{}, p=o.paper;
  if(!a.paper) return '';          /* 判据由服务端给（lv.is_paper 唯一定义）*/
  const st=p||{};
  const to=st.advanced_to||'—';
  /* 对账差：账本与引擎天然差一点（舍入 + 复权因子里含着分红表没有的那些），
     超过本金万分之五就标出来 —— 低于它是舍入，高于它是有话要说。 */
  const rec=st.recon||{}, dp=rec.diff_pct;
  const bad=(dp!=null && Math.abs(dp)>0.0005);
  const mm=st.mismatch;
  /* 盘中写下、还没被权威数据确认的笔数。判据由**服务端**给
     （`prov_day` 是那一天、`prov_uids` 是那几笔）—— 前端不自己比日期：
     "权威数据到没到"只有服务端知道（同「可选清单由服务端给」那条）。 */
  const pv=(st.prov_day&&st.prov_day===st.advanced_to)
    ?((st.prov_uids||[]).length||0):0;
  /* 盘中被推迟的任务个数（服务端给的 `intraday_deferred`）。*/
  const dfn=pv?Object.keys(st.intraday_deferred||{}).length:0;
  return `<span class="lvtag on" style="background:#6b5bd6;border-color:#6b5bd6"
      title="成交由引擎按绑定策略跑出来，不是真实成交">模拟盘</span>
    <span class="lvtag" id="lvpaper" title="${esc(mm?mm.why:'数据更新后自动推进到最新数据日')
        +(st.datalake_declared?'\n\n这个策略自己声明了数据源（DATALAKE），'
          +'所以推进到的是【那份数据】的最新交易日，可能与主数据不同天。':'')}"
      >推进到 ${esc(to)}${
        /* 🔴 策略自己声明了数据源时**必须说出来**：它的最新日与主数据
           常常不是同一天（etf_lake 手工建、不在每日同步链里），
           只写"推进到 09-11"的话人拿它跟主数据 09-17 一比就以为坏了。 */
        st.datalake_declared&&st.datalake?' · 数据源 '+esc(st.datalake):''
      }${
        /* 🔴🔴 **盘中那一格必须标出来，而且要说清它为什么还没被确认。**
           用户 2026-09-22 问「今天应该是有交易计划的，为什么没能推进成功」——
           当时页面上同时摆着「今天要卖 1 买 3」和「推进到 09-21」，
           两个数看着自相矛盾，**而没有任何地方说为什么**（今天的日线要等
           晚上同步）。现在盘中就按【今开】推一格，但它是**近似**：
           今天的复权因子本地还没有，涨跌停价是按昨收重建的 ——
           所以日终权威数据落地后会逐笔对账，不同就冲正重录。
           ★ 不标的话人会把它当成已经落定的成交（同模拟盘那个紫标签的理由）。*/
        pv?` · <b>盘中·按今开</b>（${pv} 笔待权威确认${
             /* ★ 「还没跑完」也要说：盘中只跑到开盘竞价那一档，
                  14:00 的止损与炸板离场读的是**收盘派生量**、今天还不存在。
                  不说的话盘中那份持仓看着就是最终结果。 */
             dfn?`，另有 ${dfn} 个 14:00 的判定留到日终`:''}）`:''
      }${mm?' · 🔴 对账不一致':''}${
        bad?` · 对账差 ${num(rec.diff,2)} 元`:''}</span>${
        /* ★ 「为什么今天没推」也要说 —— 沉默与"推过了"在屏幕上长得一样。
             只在**今天还没推过**时出现，推过了就不占地方。 */
        (!pv&&st.intraday_why&&!mm)
          ?`<span class="lvtag" title="盘中推进的判据（每条都可证）">盘中未推：${
              esc(st.intraday_why)}</span>`:''
      }`;
}

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


function sigHtml(s, alert, alertWhy, bound){
  /* 🔴 无信号那支**也要有按钮**：文案写着"点「立即重算」"，而按钮已从
     账户头部挪进这一块 —— 不给的话那句话指向一个不存在的按钮
     （同 backLink 那条：给一个点了没反应的入口比不给更糟）。
     而且恰恰是"还没有信号"时最需要它。 */
  /* 🔴 **"没绑策略"是一种【正常状态】，不是"还差一步"。**
     原来这两支混成一支：未绑策略的账户也显示「还没有信号 —— 点「立即重算」」
     加一个按钮，而点下去必然报"账户还没绑定策略" ——
     **指向一条走不通的路**（同 backLink 那条的反面）。
     2026-09-19 用户要的「非策略账户」就是这个：手工记账的账户不该被
     一直催着去绑策略。
     ★ 判据用**绑没绑**（`code_sha256`）这个既有事实，**不加新字段** ——
       加一个 `no_strategy` 就是第二份状态，而"标了 no_strategy 却绑了策略"
       之类的组合迟早出现，且分叉不报错。 */
  if(!bound) return `<div class="lvsec" style="margin-bottom:14px"><h3>今日待办
      <span style="flex:1"></span>
      <a class="btn" href="#" id="lvbind2">绑定策略 ›</a></h3>
    <div class="none">这是<b>手工账户</b> —— 不跑策略，所以没有信号与待办。
      <div class="lvwhy" style="margin-top:4px">持仓、成本、收益、业绩页都照常用，
        成交自己在「✎ 记一笔」里录。想让它按策略出买卖清单，绑一个策略即可。</div>
    </div></div>`;
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
  /* 🔴🔴 一条提示要回答两个问题，原来两个都没答（用户 2026-09-22）：
       ①「基于哪天的数据」—— 原来只给两个**时间戳**（建于 -> 被替换于），
         而判断"这份还能不能用"靠的是 `data_asof`。实测那次两版**都是
         09-21**（当天 16:16 同步重建了面板但 09-22 行情还没出来），
         所以"覆盖"是对的 —— 只是人完全无从判断。
       ②「为什么变了」—— 那次消失的「买 301062 / 卖 300980」**不是决策变了，
         是他当天 10:11/10:12 已经照着成交了**。而提示写的是"请照现在这份
         核对"，而现在这份是**空的** —— 照它核对会读成"不该买不该卖"，
         正好相反。
     ★ 所以分两种情形，**用两种样式**：全都能用"已成交"解释的走低调样式
       （那不是要人做事的事）；真有决策变化才显红。
       混成一种的话，每次执行完信号都跳一条红字 ——「假告警看多了就不看
       告警」。 */
  const _rvAsof=r=>{
    const a=r.data_asof, b=r.new_data_asof||s.data_asof;
    if(!a) return '数据日未知（旧格式）';
    return a===b?`数据日 ${a}，未变`:`数据日 ${a} → ${b}`;
  };
  const _rvSeg=r=>{
    const d=r.diff||{}, dn=r.done||{}, seg=[];
    for(const k of ['buy','sell','hold']){
      const nm={buy:'买入',sell:'卖出',hold:'持有'}[k];
      if((d[k+'_added']||[]).length) seg.push(`${nm}新增 ${d[k+'_added'].join('、')}`);
      const rm=(d[k+'_removed']||[]), dd=(dn[k]||[]);
      if(rm.length){
        const rest=rm.filter(x=>dd.indexOf(x)<0);
        seg.push(`${nm}移除 ${rm.join('、')}`
          +(dd.length?`（其中 ${dd.join('、')} <b>你已成交</b>）`:'')
          +(rest.length&&dd.length?`，${rest.join('、')} 是真的被改掉了`:''));
      }
    }
    return seg.length?seg.join('；'):'（清单未变，只是指纹变了）';
  };
  const allDone=revs.length&&revs.every(r=>r.all_done);
  const rv=!revs.length?''
    :allDone
    /* 全都能用"已成交"解释时，别再逐项写"移除 X（其中 X 你已成交）" ——
       那是把同一个代码印两遍。直接把那几只列一次就够。 */
    ? `<div class="lvwhy" style="margin:4px 0">这份清单重算过 ${revs.length} 次（${
        esc(_rvAsof(revs[revs.length-1]))}）。${revs.map(r=>{
        const dn=r.done||{}, t=[];
        if((dn.sell||[]).length) t.push('卖出 '+dn.sell.join('、'));
        if((dn.buy||[]).length) t.push('买入 '+dn.buy.join('、'));
        return `<br>第 ${r.rev} 版里的「${esc(t.join('　'))}」已从清单移除 ——`
          + ` <b>你当天已经成交了</b>，不是清单被改。`
          + `旧版留在 ${esc(r.archived||'')}`;
      }).join('')}</div>`
    : `<div class="lvwarn"><b>⚠️ 这份清单被重算过
      ${revs.length} 次，与最初那份不一致</b>${revs.map(r=>
        `<div style="margin-top:4px">第 ${r.rev} 版（${esc(_rvAsof(r))}　·　${
          esc((r.built_at||'').slice(5,16))} 建 → ${
          esc((r.replaced_at||'').slice(5,16))} 重算）：${_rvSeg(r)}
          <span class="lvwhy">　旧版留在 ${esc(r.archived||'')}</span></div>`
      ).join('')}<div style="margin-top:4px">如果已按之前那份准备了委托，
      <b>请照现在这份核对</b>。</div></div>`;
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
        ${(s.sell||[]).length?`<table class="lvt lvsell"><tr><th class="tx">名称</th>
            <th class="rt">股数</th><th class="rt">参考价</th><th class="tx">原因</th></tr>
          ${s.sell.map(x=>`<tr><td class="tx">${cnCell(x.code, x.name)}${lvWhyMark(x)}</td>
            <td class="rt">${num(x.shares)}</td>
            <td class="rt">${num(x.ref_price,2)}</td><td class="lvwhy tx">${esc(why[x.reason]||x.reason)}</td></tr>`).join('')}</table>`
          :'<div class="none">无</div>'}</div>
      <div><b style="color:var(--down)">买入 ${(s.buy||[]).length} 只</b>
        ${(s.buy||[]).length?`<table class="lvt lvbuy"><tr><th class="tx">名称</th>
            <th class="rt">股数</th><th class="rt">限价</th><th class="rt">金额</th></tr>
          ${s.buy.map(x=>`<tr><td class="tx">${cnCell(x.code, x.name)}${lvWhyMark(x)}</td>
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
