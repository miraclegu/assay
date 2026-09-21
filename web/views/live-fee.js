/* ============ 实盘 · ⚙ 设置与费率 ============
   信息架构：**天天要看的留在页面上，偶尔用的进浮层** —— 账户设置就是
   典型的"偶尔用"，所以整块在浮层里，不占主视图。

   费率这块是全项目最容易填错的地方，所以刻意做成
   **「照一张真实账单逐项抄」**：填金额、边填边折成费率、保存后复算那一笔。
   细则见 CLAUDE.md「费率有三层」那一节。 */
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
      ${a.paper?`<div class="frow"><label>推演起点</label>
        <input id="epstart" type="date" value="${esc(a.paper_start||'')}">
        <span class="lvwhy">模拟盘从这天开始跑到最新数据日。留空 = 开户日。<br>
          🔴 <b>已经推演出成交之后就不能改了</b> —— 换起点会让整段账本对不上，
          而那时页面只会说"数据被修正过"。要换请先「重建」（删档重开）。
        </span></div>`:''}
      <div class="frow"><label>预热起点</label>
        <input id="ewarm" size="12" value="${esc(a.warmup_start||'')}">
        <span class="lvwhy">出信号时引擎从这天开始跑，要够策略建仓与滚动</span></div>
      <!-- 🔴 原来叫「券商备注」，而**模拟盘根本没有券商** —— 那个标签对它是错的。
           内容本来也不止券商（连提示里的例子都混着账户用途）。字段键
           broker_note 这个键**不改**（改名要动模型/服务端/前端三处，收益只是
           名字；真正的问题是下面那条）。 -->
      <div class="frow"><label>说明</label>
        <textarea id="enote" rows="2" style="flex:1;min-width:240px;resize:vertical"
          placeholder="${a.paper
            ? '这个模拟盘在验证什么？例：不手工干预地跑 froec，与 FROEC-TRADE 实盘对照执行差异'
            : '这个账户是什么？例：华泰 · 主账户 · 小市值策略，本金 40 万'}"
          >${esc(a.broker_note||'')}</textarea>
        <span class="lvwhy">给自己看的 ——
          <b>账户列表与账户页都会显示</b>，几个账户放一起时靠它分辨。</span></div>
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
          broker_note:$('#enote').value,
        ...($('#epstart') ? {paper_start:$('#epstart').value} : {})});
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
        /* 走 hash（同「建完账户」那条）：归档完回列表页，
           让路由去渲染，别让页面与 hash 各说各话。 */
        close(); location.hash = '#/live';
      }catch(e){ $('#emsg').className='lvmsg bad'; $('#emsg').textContent=String(e); }
    };
  });
}
