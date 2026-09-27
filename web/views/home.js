/* ============ 总览首页 ============ */
/* ============ 总览首页 ============
   ★ 打开看板第一眼该回答的是「今天什么状态、要做什么」，
     而不是一棵回测目录树 —— 那是做策略时才进的（已移到 #/runs）。

   ★ 每一块都只给【摘要 + 一个入口】，不在首页重复做那一页的事：
     首页做成小型全功能页的话，同一份数据两处渲染，迟早不一致。

   ★ 五块并发拉，任何一块失败只让它自己显示错误 —— 数据同步挂了不该
     让待办也看不见。 */
async function showHome(){
  stopPoll();
  enterView();
  $('#main').innerHTML='<div class="lvwhy" style="padding:20px">加载中…</div>';
  /* 🔴 `no_data`/`missing` 要一起带出来 —— 只留 `String(e)` 的话，
     「本地还没有数据」与「真出错了」在下面就分不开了，四块会一起刷红。 */
  /* ★ 用 `e.message` 不是 `String(e)` —— 后者给的是 `Error: 本地还没有…`，
     那个 `Error:` 前缀是 JS 的内部形状，不该出现在屏幕上。 */
  const one=u=>j(u).catch(e=>({error:(e&&e.message)||String(e),
    no_data:!!(e&&e.no_data), missing:(e&&e.missing)||''}));
  const [accts, sync, mkt, watch, marks, alerts] = await Promise.all([
    one('/api/live/accounts'), one('/api/sync'),
    one('/api/market/overview?top=5'), one('/api/watchlist'), one('/api/marks'),
    one('/api/alerts'),
  ]);
  const todo = [];
  if(accts && !accts.error){
    /* 逐账户拉一次详情才有待办清单与权益 —— 账户不多（个数级），
       并发拉没问题。 */
    const ds = await Promise.all((accts.accounts||[])
      /* 🔴 **首页只列真实盘，模拟盘不进来**（2026-09-19 用户要求）。
         首页回答的是"今天什么状态、要做什么"，而模拟盘是**推演** ——
         把它混进来有两个坏处：待办里会出现"引擎明天要买什么"这种
         其实不用你动手的事；总资产那一列也会把推演的钱算进视野。
         ★ 判据用服务端给的 `paper`（`lv.is_paper` 唯一定义），
           不在前端比 `mode` 字符串（老账户根本没有这个字段）。 */
      .filter(a=>!a.archived && !a.paper)
      .map(a=>one('/api/live/account?id='+encodeURIComponent(a.id))
        .then(o=>({acct:a, d:o}))));
    ds.forEach(x=>todo.push(x));
  }
  $('#main').innerHTML=`
    ${homeAlert(todo, sync)}
    <div class="pgrid c2">
      ${homeLive(accts, todo)}
      ${homeMarket(mkt)}
    </div>
    <div class="pgrid c2" style="margin-top:14px">
      ${homeAlerts(alerts)}
      ${homeWatch(watch)}
    </div>
    <div class="pgrid c2" style="margin-top:14px">
      ${homeData(sync)}
    </div>
    ${homeMarks(marks)}
    ${homeRef()}`;
  document.querySelectorAll('#main [data-star]').forEach(e=>e.onclick=ev=>{
    ev.preventDefault(); ev.stopPropagation();
    toggleStar(e, e.dataset.star, !!e.dataset.on); });
}

/* 底部一行【参考】。★ 刻意**不做成一块** —— 首页回答的是"今天什么状态、
   要做什么"，而口径字典与指标广场都是低频的参考：做成块会占掉一屏、
   与那六块抢注意力（同「首页每块只给摘要 + 一个入口」那条）。
   ★ 但也不能没有：用户 2026-09-15 问"指标广场从哪里进入？首页没有地方
     进入吗" —— 一个只能从个股页工具条里摸到的入口，等于没有入口。 */
function homeRef(){
  return `<div class="lvwhy" style="margin-top:14px;text-align:center">
    参考 · <a href="#/docs">📖 口径字典</a>（数据字段怎么算的）
    · <a href="/indicators.html">📊 指标广场</a>（看盘：单只票的时间序列）
    · <a href="/factors.html">🧪 因子广场</a>（选股：全市场的横截面打分）
    · <a href="#/runs">📚 回测归档</a></div>`;
}

/* 顶部横条：只在【真的要做什么】时出现。
   ★ 没有待办就不显示 —— 常驻一条"一切正常"的横幅，等于教人忽略这个位置。 */
function homeAlert(todo, sync){
  const hit = todo.filter(x=>x.d && !x.d.error && x.d.alert);
  const lag = (sync && !sync.error && sync.status && sync.status.leg_a_lag) || 0;
  let h='';
  if(hit.length) h+=`<div class="lvrb now"><b>今天有事要做</b> —— ${
    hit.map(x=>`<a href="#/live/${esc(x.acct.id)}" style="color:inherit">${esc(x.acct.name)}</a>：${
      esc((x.d.alert_why||[]).join('；'))}`).join('　·　')}</div>`;
  if(lag) h+=`<div class="lvwarn"><b>行情数据落后 ${lag} 个交易日</b> ——
    信号会用旧数据算。<a href="#/sync">去数据页同步 ›</a></div>`;
  return h;
}

function homeLive(accts, todo){
  if(!accts || accts.error) return homeCard('实盘', '#/live',
    errHtml(accts, '账户'));
  const rows = todo.filter(x=>x.d && !x.d.error);
  if(!rows.length) return homeCard('实盘', '#/live',
    '<div class="none">还没有实盘账户 —— 去实盘页建一个'
     + '<div class="lvwhy" style="margin-top:4px">模拟盘不列在首页：'
     + '它是推演，不是今天要你动手的事</div></div>');
  return homeCard('实盘 · 今日待办与持仓', '#/live', `
    <table class="pkt"><tr>
      <th class="tx">账户</th><th class="rt">总资产</th><th class="rt">当日盈亏</th>
      <th class="rt">仓位</th><th class="tx">今天</th></tr>
      ${rows.map(x=>{
        const P=x.d.pos||{}, wt=(P.equity&&P.market_value!=null)?P.market_value/P.equity:null;
        const sg=x.d.signal||{};
        const act=x.d.alert
          ? `<b style="color:var(--warn)">${esc((x.d.alert_why||[]).join('；'))}</b>`
          : `<span class="lvwhy">${sg.next_rebalance
              ? '下次调仓 '+esc(sg.next_rebalance)
                +(sg.days_until_rebalance!=null?`（${sg.days_until_rebalance} 个交易日）`:'')
              : '没有要动的'}</span>`;
        return `<tr>
          <td class="tx">${x.d.alert?'<span class="adot"></span> ':''}<a
            href="#/live/${esc(x.acct.id)}" style="color:inherit">${esc(x.acct.name)}</a>
            <div class="lvwhy">${(P.items||[]).length} 只持仓</div></td>
          <td class="rt">${num(P.equity,2)}</td>
          <!-- 🔴 **当日盈亏，不是累计浮盈**：首页问的是"今天怎么样"，
               而浮盈是开仓至今的累计 —— 那是复盘时看的，属于业绩页。
               🔴 2026-09-22：pnl_day 的口径改成【账户当日全口径】了
                 （持仓浮动 + 当天卖出已实现 − 当天费用），所以分母跟着改成
                 **总资产**。还按持仓市值算的话，有卖出的日子分母里少了
                 已经卖掉的那部分，百分比会偏大 —— 而它不报错。
               ★ 停牌股取不到价时 pnl_day 是 null（整只票没数据）——
                 显示"—"而不是 0 —— 0 会被读成"今天不涨不跌"。 -->
          <td class="rt" style="color:${upc(P.pnl_day)}">${
            P.pnl_day==null?'—':(P.pnl_day>=0?'+':'')+num(P.pnl_day,2)}
            <div class="lvwhy">${(P.pnl_day==null||!P.equity)?'—'
              :ratv(P.pnl_day/(P.equity-P.pnl_day))+' 总资产'}</div></td>
          <td class="rt">${wt==null?'—':(wt*100).toFixed(1)+'%'}</td>
          <td class="tx">${act}</td></tr>`;}).join('')}
    </table>
    <div class="lvwhy" style="margin-top:4px">当日盈亏 = Σ 每一批 ×（现价 − 基准），
      <b>今天买的</b>那批基准用成交价、<b>以前买的</b>用昨收；幅度的分母是持仓市值。
      累计浮盈去账户页看。调仓日是纯日历的，能提前算出来；清单要等前一交易日收盘。</div>`);
}

function homeMarket(o){
  if(!o || o.error) return homeCard('盘面', '/market.html',
    errHtml(o, '盘面'));
  const rate = o.n ? (o.up/o.n*100) : null;
  return homeCard(`盘面 · ${esc(o.date)}`, '/market.html', `
    <div class="kpi" style="margin-bottom:8px">
      ${cell('涨 / 跌',
        `<span style="color:var(--up)">${num(o.up)}</span> / <span style="color:var(--down)">${num(o.down)}</span>`,
        rate==null?'':'上涨占比 '+rate.toFixed(1)+'%')}
      ${cell('涨停 / 跌停',
        `<span style="color:var(--up)">${num(o.limit_up)}</span> / <span style="color:var(--down)">${num(o.limit_down)}</span>`,
        '炸板 '+num(o.open_limit_up))}
      ${cell('成交额', yiv(o.amount), '中位换手 '+pctn(o.median_turnover))}
      ${cell('中位涨幅',
        `<span style="color:${upc(o.median_change)}">${pctv(o.median_change)}</span>`, '')}
    </div>
    <div class="lvwhy" style="margin-bottom:4px">行业 · 领涨领跌</div>
    ${(o.industries||[]).slice(0,3).map(x=>
      `<a class="chip" href="/sector.html?kind=sw&code=${encodeURIComponent(x.code||'')}"
        >${esc(x.name)} <b style="color:${upc(x.avg_change)}">${pctv(x.avg_change)}</b></a>`).join('')}
    ${(o.industries||[]).slice(-2).map(x=>
      `<a class="chip" href="/sector.html?kind=sw&code=${encodeURIComponent(x.code||'')}"
        >${esc(x.name)} <b style="color:${upc(x.avg_change)}">${pctv(x.avg_change)}</b></a>`).join('')}`);
}

function homeWatch(w){
  if(!w || w.error) return homeCard('自选', '/watchlist.html',
    errHtml(w, '自选'));
  const rows=(w.rows||[]).filter(x=>x.change_pct!=null)
    .sort((a,b)=>Math.abs(b.change_pct)-Math.abs(a.change_pct));
  if(!rows.length) return homeCard('自选', '/watchlist.html',
    '<div class="none">自选是空的 —— 在个股页或盘面榜单里点 ★ 加进来</div>');
  return homeCard(`自选 · 异动前 ${Math.min(8,rows.length)}`, '/watchlist.html', `
    <table class="pkt"><tr><th class="tx">名称</th><th class="rt">现价</th>
      <th class="rt">涨跌</th><th class="rt">换手</th><th class="tx">分组</th></tr>
      ${rows.slice(0,8).map(x=>`<tr>
        <td class="tx">${spLink(x.code, x.name||x.code)}
          ${x.limit_up?'<span class="lvwhy" style="color:var(--up)">涨停</span>':''}
          ${x.limit_down?'<span class="lvwhy" style="color:var(--down)">跌停</span>':''}</td>
        <td class="rt">${fmtN(x.close)}</td>
        <td class="rt" style="color:${upc(x.change_pct)}">${pctv(x.change_pct)}</td>
        <td class="rt">${pctn(x.turnover)}</td>
        <td class="tx lvwhy">${esc(x.group||'')}</td></tr>`).join('')}
    </table>
    <div class="lvwhy" style="margin-top:4px">按<b>涨跌幅绝对值</b>排 ——
      跌得多的和涨得多的一样需要知道。</div>`);
}

/* 买点到价 —— 首页只给【要动的那几行】。
   ★ 不重复做那一页的事：这里不列每一档，只说"哪只、到了第几档、目标价"。
     全清单在买点页（标题就是入口）。 */
function homeAlerts(o){
  if(!o || o.error) return homeCard('买点', '/alerts.html',
    errHtml(o, '买点清单'));
  const rows=(o.rows||[]);
  if(!rows.length) return homeCard('买点清单', '/alerts.html',
    '<div class="none">清单是空的 —— 去买点页加几只票、写几档想买的价（分红自动填）</div>');
  const act=rows.filter(x=>x.state==='hit'||x.state==='near');
  /* 没有到价的就给"最接近的三只" —— 一块空卡片不如告诉人"还差多少" */
  const show=(act.length?act:rows.filter(x=>x.next)
    .sort((a,b)=>b.next.gap-a.next.gap)).slice(0,6);
  return homeCard(`买点${act.length?' · '+act.length+' 只到价/接近':' · 最接近的几只'}`,
    '/alerts.html', `
    <table class="pkt"><tr><th class="tx">名称</th><th class="rt">现价</th>
      <th class="rt">股息率</th><th class="rt">目标</th><th class="tx">状态</th></tr>
      ${show.map(x=>{
        const t=(x.state==='hit'?x.tiers[x.hit]
                :(x.state==='near'?x.tiers[x.near]:x.next))||{};
        return `<tr${x.state==='hit'?' class="ahit"'
          :(x.state==='near'?' class="anear"':'')}>
        <td class="tx">${spLink(x.code, x.name||x.code)}</td>
        <td class="rt">${fmtN(x.price)}</td>
        <td class="rt">${x.yield_now==null?'—':(x.yield_now*100).toFixed(2)+'%'}</td>
        <td class="rt">${fmtN(t.price)}<span class="lvwhy">${
          t.yield==null?'':'/'+(t.yield*100).toFixed(2)+'%'}</span></td>
        <td class="tx">${x.state==='hit'
          ? `<b style="color:var(--up)">到价 · 第 ${x.hit+1} 档</b>`
          : (x.state==='near'
             ? `<b style="color:var(--warn)">接近 · 第 ${x.near+1} 档</b>`
             : `<span class="lvwhy">还要跌 ${(-t.gap*100).toFixed(1)}%</span>`)}</td>
      </tr>`;}).join('')}
    </table>
    <div class="lvwhy" style="margin-top:4px">手工填的挂单计划（不是策略）——
      到价会走系统通知，同一档一天只提醒一次。</div>`);
}

function homeData(o){
  if(!o || o.error) return homeCard('数据', '#/sync',
    errHtml(o, '数据状态'));
  const st=o.status||{};
  const A=(st.items||[]).filter(x=>x.leg==='A');
  const B=(st.items||[]).filter(x=>x.leg==='B');
  const au=o.auto||{};
  return homeCard('数据 · 新鲜度', '#/sync', `
    <table class="pkt"><tr><th class="tx">项</th><th class="tx">最新</th>
      <th class="tx">状态</th></tr>
      ${A.map(x=>`<tr><td class="tx">${esc(x.name)}</td>
        <td class="tx">${esc(x.max||'—')}</td>
        <td class="tx">${lagSpan(x)}</td></tr>`).join('')}
      <tr><td class="tx lvwhy" colspan="3">财务（事件驱动，不按交易日算落后）</td></tr>
      ${B.slice(0,3).map(x=>`<tr><td class="tx">${esc(x.name)}</td>
        <td class="tx">${esc(x.max||'—')}</td>
        <td class="tx lvwhy">距今 ${x.days_since==null?'—':x.days_since} 天</td></tr>`).join('')}
    </table>
    <div class="lvwhy" style="margin-top:4px">
      自动同步 <b>${au.on?'开 · 每日 '+esc(au.schedule||'?'):(au.supported===false?'不支持':'关')}</b>。
      财务的"落后"<b>不按交易日算</b> —— 没公告的日子本来就没有新 pub_date。
    </div>`);
}

function homeMarks(ms){
  if(!ms || ms.error || !ms.length) return '';
  const top=ms.slice().sort((a,b)=>(b.annual_return||-9)-(a.annual_return||-9)).slice(0,5);
  return homeCard('选中的规则 · 年化前 5', '#/picks', `
    <table class="pkt"><tr><th class="tx">策略 · 参数</th><th class="rt">年化</th>
      <th class="rt">回撤</th><th class="rt">夏普</th><th class="tx">区间</th></tr>
      ${top.map(r=>`<tr>
        <td class="tx"><a href="#/run/${encodeURIComponent(r.run_id)}"
          style="color:inherit">${esc(r.strategy||'')}</a>
          <div class="lvwhy">${Object.entries(r.params||{}).slice(0,4)
            .map(([k,v])=>esc(k+'='+v)).join(' ')||'默认参数'}</div></td>
        <td class="rt"><b class="${sign(r.annual_return)}">${pct(r.annual_return,2)}</b></td>
        <td class="rt neg">${pct(r.max_drawdown,2)}</td>
        <td class="rt">${fmtN(r.sharpe)}</td>
        <td class="tx lvwhy">${esc(r.start||'')}<br>~ ${esc(r.end||'')}</td></tr>`).join('')}
    </table>`, 'wide');
}

/* 首页的一块。★ 标题本身就是入口 —— 首页只给摘要，要看全的点标题过去。
   ★ 叫 homeCard 而不是 card —— 这个文件里已经有一个 `card(k,v,cls,note)`
     （回测详情页的指标卡）。同名函数不会报错，**后定义的直接覆盖前面的**，
     表现是首页渲染出一堆指标卡的骨架而内容全错位。 */
function homeCard(title, href, body, cls){
  return `<div class="lvsec ${cls||''}"><h3>
      <a href="${href}" style="color:inherit;text-decoration:none">${esc(title)} ›</a>
      <span style="flex:1"></span>
      <a class="lvwhy" href="${href}">看全部 ›</a></h3>${body}</div>`;
}

/* 个股与查数据都已经不在这个文件里：
   · 个股 -> 独立页 /stock.html（本文件的 #/stock 路由只做跳转，旧链接不失效）
   · 查数据（只读 SQL 页）-> **已取消**。命令行工具仍在
     `datalake/build/query.py`（`--sql-stdin` / `--schema`），
     安全规则那三层也还在那里。 */
