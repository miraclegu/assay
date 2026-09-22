/* ============ 实盘 · ✎ 记一笔 + 成交流水 ============
   两者是同一件事的两头：录进去、查出来。所以放一个文件。

   🔴 账本 **append-only**：录错了写一条反向冲正，不物理删除 ——
   实盘账本一旦能被改写就没法复盘。改一笔 = 冲正 + 重录。 */
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
      <button class="btn rtab" data-t="back">补录</button>
      <button class="btn rtab" data-t="cash">现金</button>
      <span style="flex:1"></span><button class="btn" id="mclose">关闭</button></div>
    <div id="rfill" class="lvsec">
      <div class="lvwarn" id="fbackhint" style="display:none;margin-bottom:8px">
        <b>补录历史成交</b> —— 这里是给「那天做了、当时没录」用的，和日常录入
        分开放，免得手滑把今天的成交记到别的日子上。三件要知道的：<br>
        ① 账本 <b>append-only</b>：录错了只能写一条反向冲正再重录，删不掉；<br>
        ② 可卖清单按<b>成交日那天</b>的持仓给（不是今天的）——
           那天有、今天已清仓的票照样选得到；<br>
        ③ 费用按<b>成交日那一档</b>费率算。换过券商之后拿今天的费率补录
           三个月前那笔，数字看着很正常，<b>只是错的</b>。
      </div>
      <h3 id="fh3">逐笔录入</h3>
      <div class="lvform">
        <select id="fs"><option value="buy">买</option><option value="sell">卖</option></select>
        <span id="fdb" class="fdbox"></span>
        <input type="hidden" id="fd" value="${esc(d0)}">
        <span id="fcb" class="fcbox"></span>
        <input type="hidden" id="fc">
        <input id="fq" size="7" placeholder="股数">
        <input id="fp" size="9" placeholder="价格(留空=开盘价)">
        <input id="ff" size="9" placeholder="费用(留空=按费率)">
        <button class="btn" id="fb" ${ro?'disabled':''}>录一笔</button>
      </div>
      <div class="frow"><label></label><span class="lvwhy" id="fcmsg"></span></div>
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
      <div id="rbulk">
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
      </div>
      <!-- 🔴 fmsg 必须在 rbulk 【外面】：批量粘贴那块在「补录」页签下
           是 display:none，而它【同时也是逐笔录入的提示位】—— 套在里面的话
           补录失败时屏幕上【什么都不出现】（那正是"点了没反应"这种最难查的
           坏；实测：服务端明明回了"当天行情还没同步"，页面一个字没有）。
           是既有那条「取不到当日行情时页面要说清原因」抓到的。 -->
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
    /* 🔴 这三个必须声明在 `sel()` **之前** —— `sel(tab||'fill')` 在下面
       几行就被调用，而 `let/const` 有**暂时性死区**：写在后面的话
       `sel` 里一引用就抛 ReferenceError，**而它只在控制台里报**，
       表现是弹窗里日期那一格空着（同「geo 是局部变量」那次）。
       ★ `fcmsg` 同理从 `const 箭头` 改成**函数声明**（可提升）。 */
    const D0 = $('#fd').value;
    let FMODE = 'fill';
    let FSMAX = null;                  /* 卖出上限（股）；买入时是 null */
    const sel=t=>{ $('#rfill').style.display=(t==='cash'?'none':'');
                   $('#rcash').style.display=(t==='cash'?'':'none');
                   document.querySelectorAll('.rtab').forEach(x=>
                     x.classList.toggle('on', x.dataset.t===t));
                   if(t==='cash') return;
                   FMODE = t;
                   const hint=$('#fbackhint'), bulk=$('#rbulk'), h3=$('#fh3');
                   if(hint) hint.style.display = (t==='back'?'':'none');
                   if(bulk) bulk.style.display = (t==='back'?'none':'');
                   if(h3) h3.textContent = (t==='back'?'补录一笔':'逐笔录入');
                   fdRender(); fcRender(); };
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
    /* ---- 🔴 补录与常规操作**分开**（用户 2026-09-22）----
       「补录功能可以开单独的按钮，和常规操作做区分，而不是混在正常的操作里」。

       真正"混在正常操作里"的是**逐笔录入那个自由日期框** —— 它是手填的、
       最容易填错，而填错的后果是**成本价与建仓日错**，那两个值直接喂给
       止损判定与红利税档位（同「刻意删掉那个『含不含规费』开关」的理由：
       **少一个能填错的地方**）。
       所以常规那档把日期**锁成文本**，要改日期得显式切到「补录」。

       ★ **只有一份表单**：两个页签共用 `#rfill` 那一套，只有「日期那一格」
         与「顶部说明」按模式变。各写一份的话两边迟早分叉，而分叉的表现是
         "补录那边少了个校验"。
       ★ 批量粘贴**留在常规**：它的日期来自你粘进来的**对账单**、不是手填的，
         而"照账单整批抄"本来就是常规操作。 */
    function fdRender(){
      const box = $('#fdb'); if(!box) return;
      if(FMODE === 'back'){
        /* ★ 默认**空**，不给一个可能错的默认值（同「拿不到分红那一格标
             『查不到』，不猜一个数填上去」）。 */
        box.innerHTML = '<input id="fdi" type="date" style="width:132px">';
        $('#fd').value = '';
        $('#fdi').onchange = () => { $('#fd').value = $('#fdi').value || '';
                                     fcRender(); };
      }else{
        /* 常规：锁成文本。**切回来时要复位到 d0** —— 把补录填的那天带回
           日常录入，正是"混在一起"最坏的后果。 */
        $('#fd').value = D0;
        box.innerHTML = '<b>' + esc(D0) + '</b>';
      }
    }

    /* ---- 🔴 买与卖是**两种不同的输入**（用户 2026-09-22）----
       「选择卖的时候，只能从当前持仓中选择，交易的数量不能超过持仓的数量。
         买的时候，输入代码、名称，出现下拉框供选择。」

       买：一个裸输入框要人**记住代码**（`301126.SZ` / `sz301126` / …），
           打错一个字最好的情况是报"代码不对"，**打成另一只真实存在的票**
           就是静默录错，而账本是 append-only 的。所以给搜索下拉。
       卖：能卖的就那十来只，而且**有上限** —— 让人去自由输入等于把
           "超卖"这件事留给服务端在提交后才说。

       ★ 方向 `<select>` 提到**最前**：它决定后面那一格长什么样。
       🔴 判据「能卖多少」由**服务端**给，且取的是**成交日那天**的持仓
         （`/api/live/sellable?date=`）—— 照"今天"做会误伤补录，
         实测用户自己的账本里就有这种票（600774 只在 09-01 那天可卖）。 */
    /* 常规那档的日期 = 模板里填好的那个（信号的 for_date 或今天）。
       ★ **从 DOM 读**，不在这儿再算一遍 —— 两处算迟早不一致
         （同「签名要从 DOM 读，不要另存一份」那条）。 */
    function fcmsg(html, bad){
      const e = $('#fcmsg'); if (!e) return;
      e.innerHTML = html || '';
      e.style.color = bad ? 'var(--down)' : '';
    }

    async function loadSellable(){
      const box = $('#fcb'), d = $('#fd').value.trim();
      if(!d){
        /* 补录模式下还没填日期 —— 说清下一步，不要摆一个空下拉在那儿
           （空下拉看着像"没有持仓"，而事实是"还不知道问哪天"）。 */
        box.innerHTML = '<select id="fsel"><option value="">先填成交日</option></select>';
        fcmsg('先在左边填上<b>成交日</b> —— 能卖多少是按那天的持仓算的');
        return;
      }
      box.innerHTML = '<select id="fsel"><option value="">读取中…</option></select>';
      let o;
      try{ o = await j('/api/live/sellable?id=' + encodeURIComponent(aid)
                       + '&date=' + encodeURIComponent(d)); }
      catch(e){ box.innerHTML = '<select id="fsel"><option value="">取不到</option></select>';
                fcmsg('可卖清单取不到：' + esc(String(e)), 1); return; }
      const it = o.items || [];
      if(!it.length){
        box.innerHTML = '<select id="fsel"><option value="">那天没有持仓</option></select>';
        /* ★ 空的时候要说清是**哪天**没有持仓 —— 只说"没有持仓"的话，
             补录历史卖出的人会以为功能坏了。 */
        fcmsg('<b>' + esc(o.date || d) + '</b> 那天这个账户一股都没有 —— '
              + '卖出要先有持仓。补录历史成交时请把上面的日期改成那一天。', 1);
        return;
      }
      box.innerHTML = '<select id="fsel"><option value="">选一只（'
        + it.length + ' 只可卖）</option>'
        + it.map(x => '<option value="' + esc(x.code) + '" data-q="' + x.shares
            + '">' + esc(x.name || x.code) + ' · ' + num(x.shares, 0)
            + ' 股</option>').join('') + '</select>';
      $('#fsel').onchange = () => {
        const op = $('#fsel').selectedOptions[0];
        const q = op ? parseInt(op.dataset.q || '0', 10) : 0;
        $('#fc').value = $('#fsel').value || '';
        FSMAX = q || null;
        if(FSMAX){
          $('#fq').max = FSMAX;
          fcmsg('<b>' + esc(o.date || d) + '</b> 可卖 <b>' + num(FSMAX, 0)
                + '</b> 股（点「全部」自动填）'
                + ' <a href="#" id="fqall">全部</a>');
          const al = $('#fqall');
          if(al) al.onclick = ev => { ev.preventDefault();
            $('#fq').value = FSMAX; chkQ(); };
        }else{ $('#fq').removeAttribute('max'); fcmsg(''); }
      };
    }

    /* 超量**当场拦住并说清**，而不是等提交后服务端退回来。
       ★ 但**不给按钮加 disabled**（项目纪律：disabled 的元素连 title 都不
         触发，点了没反应是最难查的那种坏）—— 按钮照样可点，点了说原因。 */
    function chkQ(){
      const n = parseInt($('#fq').value, 10);
      if(FSMAX && n > FSMAX){
        fcmsg('填了 <b>' + num(n, 0) + '</b> 股，而 <b>'
              + esc($('#fd').value.trim()) + '</b> 那天只有 <b>'
              + num(FSMAX, 0) + '</b> 股 —— 卖不了那么多', 1);
        return false;
      }
      return true;
    }

    function fcRender(){
      const side = $('#fs').value, box = $('#fcb');
      $('#fc').value = ''; FSMAX = null;
      $('#fq').removeAttribute('max');
      fcmsg('');
      if(side === 'sell'){ loadSellable(); return; }
      box.innerHTML = '';
      mountSearch(box, {
        keep: true,
        placeholder: '代码或名称，如 601857 / 中国石油',
        /* 🔴 **排除指数** —— 它买不了，而且 `normalize_code('sh000001')`
             会报"自相矛盾"，那个报错指不到真正的原因。ETF 是能买的
             （项目里就有 ETF 轮动策略），所以只挡 index 这一类。 */
        filter: r => r.kind !== 'index',
        onPick: r => { $('#fc').value = r.code;
          fcmsg('已选 <b>' + esc(r.name || '') + '</b> '
                + '<code>' + esc(r.code) + '</code>'
                + (r.kind === 'etf' ? ' · ETF' : '')); },
      });
    }

    $('#fs').onchange = fcRender;
    $('#fd').onchange = () => { if($('#fs').value === 'sell') loadSellable(); };
    $('#fq').oninput = chkQ;
    fcRender();

    $('#fb').onclick=()=>{ const fv=$('#ff').value.trim(), pv=$('#fp').value.trim();
      if(!$('#fc').value.trim()){
        m().className='lvmsg bad';
        m().textContent=($('#fs').value==='sell'
          ?'先从上面的下拉里选一只要卖的票'
          :'先在上面搜一只票并选中（敲代码或名称）');
        return; }
      if(!chkQ()){ m().className='lvmsg bad';
        m().textContent='股数超过那天的持仓 —— 见上面那行红字';
        return; }
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
/* ============ 逐笔成交表：**一处定义，两处用** ============
   用户 2026-09-15："买入卖出都算一笔单独的操作，需要记录下来，
   这才是真正的交易记录。" —— 对。这张表就是它。

   🔴 **不许再抄一份**：同一份数据两处渲染迟早不一致（列序、冲正按钮、
     估算标记会慢慢分叉），而那不报错。
   ★ 2026-09-22 起调用方只剩一处（业绩页的「交易明细」页签）——
     流水独立页并进去了。`showRev` 这个开关**保留**：它区分的是
     "这一处该不该给更正入口"，而不是"能不能改"（后者是 `ro`）。
     现在交易明细两个都给 —— 那是账本唯一的更正手段，藏起来等于
     录错了再也改不了。 */
function fillsTableHtml(rows, opt) {
  opt = opt || {};
  const ro = opt.ro, showRev = opt.showRev !== false;
  if (!rows || !rows.length) return '';
  return `<table class="lvt">
    <!-- 列序按【看的顺序】排：哪天、买还是卖、哪只票、什么价、多少股、
         多少钱。录入时间与来源是审计信息，平时不看，挪到最后并压暗。
         数字列右对齐 + tabular-nums，位数才对得齐（.rt / .lvt td.rt）。 -->
    <tr><th>成交日</th><th class="tx">方向</th><th class="tx">名称</th>
        <th class="rt">价格</th><th class="rt">股数</th><th class="rt">金额</th>
        <th class="rt">费用</th>${showRev ? '<th></th>' : ''}
        <th class="lvwhy tx">录入时间</th><th class="lvwhy tx">来源</th></tr>
    ${rows.map(f => {
      const rev = !!f.reverse_of, dead = !!f._dead, fid = f.uid || f.ts;
      return `<tr class="${rev || dead ? 'lvrev' : ''}">
      <td>${esc(f.trade_date)}</td>
      <td class="tx" style="color:${f.side === 'buy' ? 'var(--up)' : 'var(--down)'}">${f.side === 'buy' ? '买' : '卖'}</td>
      <td class="tx">${cnCell(f.code, f.name)}</td>
      <td class="rt" title="${f.price_from ? '取的成交日' + (f.price_from === 'open' ? '开盘价' : '收盘价') + '，不是券商回报' : ''}">${num(f.price, 3)}${
        f.price_from ? '<span class="lvwhy">' + (f.price_from === 'open' ? '开' : '收') + '</span>' : ''}</td>
      <td class="rt">${num(f.shares)}</td>
      <td class="rt">${num(f.shares * f.price, 2)}</td>
      <td class="rt" title="${f.fee_estimated ? '估算值，对完账单请冲正改成实际' : ''}">${num(f.fee, 2)}${
        f.fee_estimated ? '<span class="lvwhy">估</span>' : ''}</td>
      ${showRev ? `<td>${(rev || dead || ro) ? '' : `<a href="#" class="lvrv" data-ts="${esc(fid)}"
        data-d="${esc(f.trade_date)}" data-c="${esc(f.code)}" data-s="${f.side}"
        data-q="${f.shares}" data-p="${f.price}" data-n="${esc(f.name || '')}"
        data-f="${f.fee || 0}">冲正</a>`}</td>` : ''}
      <td class="lvwhy tx">${esc(f.ts.slice(5, 16).replace('T', ' '))}</td>
      <td class="lvwhy tx">${rev ? '冲正' : esc(f.source || '')}</td></tr>`;
    }).join('')}
    </table>`;
}

/* 冲正按钮的事件。★ innerHTML 之后才存在的元素要在这里绑 ——
   只在渲染开头绑的话点了没反应且不报错（对比页「移除」栽过）。 */
function bindFillRevert(root, aid, reload) {
  (root || document).querySelectorAll('a.lvrv').forEach(e => e.onclick = async ev => {
    ev.preventDefault();
    const d = e.dataset;
    const of = parseFloat(d.f || '0') || 0;
    if (!confirm(`冲正：${d.d} ${d.c} ${d.s === 'buy' ? '买' : '卖'} ${d.q} @${d.p}（费用 ${of.toFixed(2)}）\n`
      + `会追加一条反方向记录（${d.s === 'buy' ? '卖' : '买'} ${d.q}，费用 ${(-of).toFixed(2)}），\n`
      + `原记录保留并划掉。净现金影响为 0。`)) return;
    try {
      await post('/api/live/fill', {id: aid, rows: [{trade_date: d.d, code: d.c,
        side: d.s === 'buy' ? 'sell' : 'buy', shares: parseInt(d.q, 10),
        price: parseFloat(d.p), fee: -of, name: d.n, source: 'reverse',
        reverse_of: d.ts, note: '冲正'}]});
      reload();
    } catch (e) { alert(String(e)); }
  });
}

/* 🔴 成交流水的**独立页 `showFills` 已删干净**（2026-09-22）：用户把它并进了
   业绩页的「交易明细」页签（"和业绩里的交易记录有所重复"）。
   留一个没人调的渲染函数，下次有人会以为它是正本 —— 而那时两份已经分叉了。
   ★ 旧 hash `#/live/<id>/fills` **仍然可达**：`app.js` 里 redirect 到
     `#/live/<id>/perf?tab=fills`（书签是产品契约）。
   ★ `fillsTableHtml` 与 `bindFillRevert` 都还在，只是调用方从两处变成一处。 */

/* 策略详情：源码快照 + 参数表 + 【用这个版本跑过的回测】。
   关联键是主文件自身哈希 —— 账户的 code_sha256 是"主文件+依赖"的打包哈希，
   与归档的单文件哈希口径不同，直接比永远匹配不上（服务端已处理）。 */
