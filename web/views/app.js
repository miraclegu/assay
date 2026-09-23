/* ============ 骨架：hash 路由 + 跨视图共用的小部件 ============
   ★ 页面被拆成 5 个 .js（app/home/live/runs/sync），依据是**产品域**而不是
     技术分层 —— 依赖分析显示跨域调用几乎全是"路由 -> 视图"这一个方向，
     反向只有本文件这几个通用件。
   🔴 **跨文件顶层重名 = 整页 SyntaxError**（所有 <script> 共享同一个全局
     词法环境）。拆完是 6 个文件、15 对组合，selftest 里逐对比对。
     命名约定：各域用自己的前缀（实盘 LV/lv 开头、回测 RUNS/pane 开头、
     同步 SY 开头）。★ 这行原来写成 `LV*` 加斜杠，里面那两个字符
     恰好把块注释提前结束掉了 —— 后面的中文就成了裸代码，整个文件
     SyntaxError。注释里别出现那个组合。
   ★ 加载顺序无所谓：跨文件引用都在函数体内，调用时才求值；
     真正的入口只有本文件末尾那一次 route()。 */
function stopPoll(){ if(POLL){clearInterval(POLL); POLL=null;} }


/* ---- 浮层骨架：三个浮层共用，避免各写一份开关/关闭/点外面关 ---- */
function modal(html, onReady){
  const el=$('#stwrap')||(()=>{ const d=document.createElement('div'); d.id='stwrap';
    document.getElementById('app').appendChild(d); return d; })();
  el.className='stmodal';
  el.innerHTML=`<div class="stbox">${html}</div>`;
  const close=()=>{ el.className=''; el.innerHTML=''; document.onkeydown=null; };
  el.onclick=ev=>{ if(ev.target===el) close(); };
  document.onkeydown=ev=>{ if(ev.key==='Escape') close(); };
  const x=$('#mclose'); if(x) x.onclick=close;
  if(onReady) onReady(close);
  return close;
}


/* ★ 通用辅助（$ / esc / j / post / 数字格式化 / cell / normCode / boardTag /
   顶栏导航）都在 /common.js 里，这里不再重复定义 —— 重复定义会让两处慢慢
   分叉，而"两个页面同一个数显示得不一样"很难查。 */
/* 名称是【当时】的名称（服务端按行日期从 security_name 解析）——
   拿当前名称标一笔 2016 年的交易是误导：002711 走过
   欧浦钢网→欧浦智网→ST欧浦→*ST欧浦→欧浦退。名称缺失时退化为只显示代码。 */
/* ★ 转发给 `cnCell`（common.js 的唯一定义）—— 原来这里自己拼一份，
   而且**没转义**；三处各写一份的结果就是"同一份信息各页面长得不一样"。 */
const nmcode=(v,r)=>cnCell(v, r.name, {date:r.date});
let RUNS=[], CUR=null, DATA={}, HD={off:0,lim:100}, TD={off:0,lim:100};

/* 直接 r.json() 会把服务端的纯文本错误（404 的 "not found"）变成
   "Unexpected token 'o'…" 这种看不懂的解析报错 —— 而真实原因往往是
   【服务端还是旧进程、没有这个新接口】。这里先看状态码再解析，把话说清楚。 */

/* ============ 路由：用 location.hash，让浏览器前进/后退天然可用 ============ */
/* 进入一个视图前的统一收拾。
   ★ 收成一个函数而不是每处抄三行：`#filter` / `#expand` 现在只在
     回测归档目录那个视图里存在（顶栏随 hash 变），别处 `$('#filter')`
     是 null —— 每处抄一遍的话漏掉一处就是 TypeError 整页白屏。
     `?.` 让"元素不在"变成无害。 */
function enterView(){
  const c=$('#cat'); if(c) c.style.display='none';
  const v=$('#vp'); if(v){ v.innerHTML=''; v.style.display='none'; }
  const m=$('#main'); if(m) m.style.display='';
}

function route(){
  /* ★ 顶栏跟着 hash 走：这一页承载多个视图，标题与高亮固定写死的话，
     在实盘页也会显示"回测归档" —— 而顶栏高亮本来就是用来告诉人"我在哪"的。 */
  syncHead();
  // 年详情页要排在 run 之前匹配 —— run 的 (.+) 是贪婪的，会把 /y/2016 一起吃掉。
  // 同一份回测内切换年份【不重新拉数据】：CUR/DATA 还在就直接重画。
  const my=/^#\/run\/(.+)\/y\/(\d{4})$/.exec(location.hash||'');
  if(my){
    const id=decodeURIComponent(my[1]); YPAGE=my[2];
    if(warm(id)){ tab(2); } else { openRun(id, 2); }
    return;
  }
  const m=/^#\/run\/(.+)$/.exec(location.hash||'');
  if(m){
    const id=decodeURIComponent(m[1]); YPAGE=null; MSEL=null; DSEL=null;
    if(warm(id)){ tab(2); } else { openRun(id); }
    return;
  }
  const v=/^#\/ver\/([0-9a-f]{6,64})$/.exec(location.hash||'');
  if(v){ openVersion(v[1]); return; }
  /* ⚖ 策略比对：#/cmp/<idA>,<idB>[,C,D] —— 深链接可分享（同 compare.html?codes=）*/
  const cm=/^#\/cmp\/(.+)$/.exec(location.hash||'');
  if(cm){ showCmp(decodeURIComponent(cm[1]).split(',').filter(Boolean)); return; }
  if((location.hash||'')==='#/picks'){ showPicks(); return; }
  if((location.hash||'')==='#/runs'){ showCatalog(); return; }
  if((location.hash||'')==='#/sync'){ showSync(); return; }
  /* ★ 个股已经搬到独立页 /stock.html。这里保留旧 hash 路由并【跳转】——
     旧链接和书签不该失效，而"点了没反应"是最难查的那种坏。 */
  const sm=/^#\/stock(?:\/([\w.]+))?$/.exec(location.hash||'');
  if(sm){ location.replace('/stock.html'
    + (sm[1]?('?code='+encodeURIComponent(decodeURIComponent(sm[1]))):'')); return; }
  /* 🔴🔴 **成交流水与选股理由都并进业绩页了**（2026-09-22 用户：「流水按钮
       和业绩里的交易记录有所重复，应该可以合并」「选股理由应该也内置到
       业绩里」）。这两个 hash **保留并【跳转】** —— 它们是书签与持仓行
       那个 `?` 的地址，属于产品契约（同 `#/stock/xxx` 那条：旧链接不该
       失效，而"点了没反应"是最难查的那种坏）。
     ★ 用 `location.replace` 不是赋值：不往历史里塞一条，否则按「后退」
       会跳回旧地址、再被弹回来，人就退不出去了。 */
  const fm=/^#\/live\/([\w-]+)\/fills(?:\/(\d+))?$/.exec(location.hash||'');
  if(fm){ location.replace('#/live/'+fm[1]+'/perf?tab=fills'); return; }
  const wm=/^#\/live\/([\w-]+)\/why(?:\?(.*))?$/.exec(location.hash||'');
  if(wm){ const q=new URLSearchParams(wm[2]||''), ps=['tab=why'];
          if(q.get('off')) ps.push('woff='+encodeURIComponent(q.get('off')));
          if(q.get('d')) ps.push('d='+encodeURIComponent(q.get('d')));
          if(q.get('all')==='1') ps.push('all=1');
          location.replace('#/live/'+wm[1]+'/perf?'+ps.join('&')); return; }
  /* 业绩页是**独立页**：主视图只留「今日待办 + 当前持仓」，其余八个页签
     （曲线 / 明细 / 持仓 / 交易 / 清仓 / 盈亏榜 / 选股理由 / 执行差异）
     都是复盘时才看的。`?tab=` 只在进页面时读一次（见 showPerf）。 */
  const pm=/^#\/live\/([\w-]+)\/perf(?:\?(.*))?$/.exec(location.hash||'');
  if(pm){ showPerf(pm[1], new URLSearchParams(pm[2]||'')); return; }
  const lm=/^#\/live(?:\/([\w-]+))?$/.exec(location.hash||'');
  if(lm){ showLive(lm[1]||null); return; }
  const dm=/^#\/docs(?:\/([\w-]+))?$/.exec(location.hash||'');
  if(dm){ showDocs(dm[1]||null); return; }
  /* 空 hash = 总览首页。★ 回测归档搬到 #/runs —— 打开看板第一眼该看到
     "今天什么状态、要做什么"，而不是一棵回测目录树（那是做策略时才进的）。
     旧的 `/`（无 hash）书签仍然可用，只是内容换成了总览。 */
  showHome();
}
/* 同一份回测内切年份不必重拉数据。但【详情视图必须正在显示】才能走这条快捷路径 ——
   从目录页后退回来时 #main 是隐藏的，只调 tab() 不会把它显出来（曾因此白屏）。 */
function warm(id){
  return CUR===id && DATA.eq && $('#main') && $('#main').style.display!=='none';
}
window.onhashchange=route;
/* 顶栏：导航用共享的 pageHead/navHtml（一处定义，加入口只改 common.js），
   这一页自己的三个控件（返回目录 / 过滤 / 全部展开）作为 extra 传进去 ——
   它们只在回测归档页有意义。 */
/* 顶栏：导航用共享的 pageHead/navHtml（一处定义，加入口只改 common.js）。
   ★ 这一页承载多个 hash 视图（首页 / 回测归档 / 实盘 / 数据 / 字典 / 选中的
     规则），所以标题与高亮要【随 hash 变】—— 固定写死的话在实盘页也会
     显示"回测归档"，而顶栏高亮的位置本来是用来告诉人"我在哪"的。 */
const HEADS={
  '':      ['home', '总览'],
  'runs':  ['runs', '回测归档'],
  'picks': ['runs', '选中的规则'],
  'run':   ['runs', '回测详情'],
  'ver':   ['runs', '策略版本'],
  'live':  ['live', '实盘'],
  'sync':  ['sync', '数据'],
  'docs':  ['sync', '数据字典'],
};
function syncHead(){
  const seg=(location.hash||'').replace(/^#\//,'').split('/')[0];
  const [key,title]=HEADS[seg]||HEADS[''];
  /* 过滤/全部展开只在【回测归档目录】上有意义 —— 别的视图里它们是噪声。 */
  const onRuns = seg==='runs';
  /* 🔴🔴 因子广场的入口**不进顶栏**（用户 2026-09-23：「不是要把这个入口
     放到回测里吗？怎么暴露在最外面？」）—— 顶栏是**全局导航**，回答
     "我在哪 / 我要去哪"；把域内子页塞进去，它就和实盘/买点/盘面那 7 个
     并排了，层级是乱的（同「顶栏已经有 7 个入口，再塞进去会抢注意力」）。
     它在【回测内容区的顶部】，见 runs.js 的 facBar()。
     ⚠ 我上一版把它放这儿，是因为"顶栏首屏必然可见" —— 那解决了可见性，
       却牺牲了层级。**两件事要一起满足**：内容区顶部同样首屏可见。 */
  pageHead(key, title, onRuns ? `
    <input id="filter" placeholder="过滤：策略 / 分组 / run_id / 参数">
    <button class="btn" id="expand">全部展开</button>
    <a class="btn" href="#/picks" title="只看被标星的规则">★ 选中的规则</a>` : '');
  if(onRuns) wireCatalogControls();
}
