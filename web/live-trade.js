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
