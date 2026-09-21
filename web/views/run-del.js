/* ==========================================================================
   归档批量删除 —— 「🗑 管理」模式（2026-09-21）

   用户："在页面上加一个策略回测结果批量删除的功能，可以多选回测结果，
   批量删除，删除之前要再弹窗确认。"

   为什么是【模式开关】而不是共用比对那套勾选：
     比对的上限是 4（理由是看得清），而批量删除要能一次选几十个 ——
     共用一套选中列表的话，上限对谁生效就成了要记的事。
     所以同一列勾选框、两种含义，用一个显式的模式把话说清。
   🔴 进管理模式时【比对工具条整条隐藏】—— 两条工具条同屏、而勾选框只有
     一列的话，"我勾的这个是干嘛的"就没法回答了。

   🔴 删除是不可逆的，所以这一条链上有四道：
     ① 两阶段：先 dry 预演，弹窗里列的是【服务端真的会删的那一份】
        （前端自己算一份的话，"页面说删 12、实际删了 15"不报错）
     ② 受保护的（标星 / 账户绑定过的版本）默认不删，单列一块
     ③ 要删受保护的，得再勾一个显式的逃生口 —— 硬拒不给出路，
        最后会变成绕过整个入口（同 force_price 那条）
     ④ confirm=true 是服务端必传项，漏传就拒
   ★ 服务端删之前会把结论（meta+stats）抽进 _pruned_conclusions.jsonl，
     与 prune_runs.py 同一个函数 —— 「tar 备份 != 保留结论」那条。
   ========================================================================== */

let DELMODE = false;
let DELSEL = [];

/* 当前页面上所有勾选框（归档目录与「选中的规则」两页共用同一批 data-cmp）。*/
function delBoxes(){
  return [...document.querySelectorAll('input[data-cmp]')];
}

function delBindBoxes(){
  delBoxes().forEach(e => {
    e.checked = DELSEL.indexOf(e.dataset.cmp) >= 0;
    e.onclick = ev => {
      /* 🔴 必须 stopPropagation：行本身的 onclick 是"进这次回测的详情" */
      ev.stopPropagation();
      DELSEL = DELSEL.filter(x => x !== e.dataset.cmp);
      if(e.checked) DELSEL.push(e.dataset.cmp);
      delBar();
    };
  });
}

function delSetMode(on){
  DELMODE = !!on;
  if(!DELMODE) DELSEL = [];
  if(DELMODE){
    delBindBoxes();
  } else {
    /* 退出时把勾选框还给比对（cmpWire 会按 CMPSEL 复原 checked 与 handler）*/
    if(typeof cmpWire === 'function') cmpWire();
  }
  delBar();
}

/* 全选 / 清空【当前看得见的】那些 —— 折叠起来的节点里那些不算，
   否则"我明明只展开了一个分组，却选中了 600 个"。 */
function delAllInView(on){
  delBoxes().forEach(e => {
    if(!e.offsetParent) return;                 /* 折叠在里面的跳过 */
    const id = e.dataset.cmp;
    DELSEL = DELSEL.filter(x => x !== id);
    if(on) DELSEL.push(id);
    e.checked = !!on;
  });
  delBar();
}

function delBar(){
  const el = $('#delbar');
  if(!el) return;
  const cb = $('#cmpbar');
  if(cb) cb.style.display = DELMODE ? 'none' : '';
  if(!DELMODE){
    el.innerHTML = '<a class="btn" id="delon" href="javascript:void(0)"' +
      ' title="进入管理模式：勾选若干次回测，批量删除">🗑 管理</a>' +
      '<span class="lvwhy">删归档、腾空间。删之前会弹窗列清单</span>';
    const b = $('#delon'); if(b) b.onclick = () => delSetMode(true);
    return;
  }
  const n = DELSEL.length;
  el.innerHTML = `<a class="btn${n ? ' on' : ''}" id="delgo" href="javascript:void(0)"
      title="${n ? '先弹窗列清单再删' : '先勾几行'}">🗑 删除（${n}）</a>
    <a class="btn" id="delall" href="javascript:void(0)">全选可见</a>
    <a class="btn" id="delnone" href="javascript:void(0)">清空</a>
    <a class="btn" id="deloff" href="javascript:void(0)">退出管理</a>
    <span class="lvwhy">管理模式：这一列勾选框现在是<b>选择要删除的回测</b>
      （比对已暂时收起）。已选 ${n} 个</span>`;
  const g = $('#delgo');
  if(g) g.onclick = () => {
    /* 🔴 按钮一律不设 disabled —— 点了要把原因说清，不是没反应 */
    if(!DELSEL.length){ delBar(); delMsg('先勾几行再点删除。'); return; }
    delAsk();
  };
  const a = $('#delall');  if(a) a.onclick = () => delAllInView(true);
  const c = $('#delnone'); if(c) c.onclick = () => delAllInView(false);
  const o = $('#deloff');  if(o) o.onclick = () => delSetMode(false);
}

function delMsg(t){
  const el = $('#delbar');
  if(el) el.insertAdjacentHTML('beforeend',
    ' <span class="stale">' + esc(t) + '</span>');
}

/* runs.js / picks 页渲染完之后调它。非管理模式什么都不改（比对照旧）。*/
function delWire(){
  if(DELMODE) delBindBoxes();
  delBar();
}

function delRow(x){
  const p = Object.entries(x.params || {}).map(([k, v]) =>
    `<span class="pill">${esc(k)}=${esc(String(v))}</span>`).join('') ||
    '<span class="pill">默认参数</span>';
  const ar = x.annual_return == null ? '—' : pctv(x.annual_return * 100);
  return `<tr><td class="tx">${esc(x.group || '')} / ${esc(x.strategy || '')}
      <div class="lvwhy">${esc(x.run_id)}</div></td>
    <td class="tx">${esc(x.start || '')}<br>${esc(x.end || '')}</td>
    <td class="tx">${p}</td>
    <td class="rt">${ar}</td>
    <td class="rt">${num((x.bytes || 0) / 1e6, 1)} MB</td>
    ${x.why_protected && x.why_protected.length
      ? `<td class="tx"><b style="color:var(--down)">${
          esc(x.why_protected.join(' / '))}</b></td>` : ''}</tr>`;
}

async function delAsk(){
  let p;
  try{
    p = await post('/api/runs/delete', {run_ids: DELSEL, dry: true});
  }catch(e){ delMsg('预演失败：' + e); return; }
  if(p.error){ delMsg(p.error); return; }
  modal(delPlanHtml(p), close => {
    const fx = $('#delforce');
    if(fx) fx.onchange = () => {
      const g2 = $('#delok');
      if(g2) g2.textContent = '确认删除 ' +
        (p.n_delete + (fx.checked ? p.n_held : 0)) + ' 次';
    };
    const ok = $('#delok');
    if(ok) ok.onclick = () => delDo(p, !!(fx && fx.checked), close);
  });
}

function delPlanHtml(p){
  const mb = (p.bytes || 0) / 1e6;
  const held = p.n_held ? `
    <h3 style="margin:14px 0 6px">受保护的 ${p.n_held} 次 —— 默认<b>不删</b></h3>
    <div class="lvwhy" style="margin-bottom:6px">标星是<b>决策证据</b>（「★ 选中的规则」读它）；
      账户绑定过的版本是「这个版本回测过没有」的<b>唯一依据</b>，删了实盘页就查不到。</div>
    <div class="pw"><table class="lvt"><tr><th class="tx">分组 / 策略</th>
      <th class="tx">区间</th><th class="tx">参数</th><th class="rt">年化</th>
      <th class="rt">体积</th><th class="tx">为什么受保护</th></tr>
      ${p.held.map(delRow).join('')}</table></div>
    <label style="display:block;margin-top:8px"><input type="checkbox" id="delforce"
      style="width:auto"> 我知道这些是决策证据 / 查证依据，仍然一并删除</label>` : '';
  const list = p.n_delete ? `
    <div class="pw"><table class="lvt"><tr><th class="tx">分组 / 策略</th>
      <th class="tx">区间</th><th class="tx">参数</th><th class="rt">年化</th>
      <th class="rt">体积</th></tr>
      ${p.delete.slice(0, 40).map(delRow).join('')}</table></div>
    ${p.n_delete > 40 ? `<div class="lvwhy">…… 另 ${p.n_delete - 40} 次（表里只列前 40）</div>` : ''}`
    : '<div class="lvmsg">没有可直接删除的 —— 选中的都受保护（见下）。</div>';
  const unknown = (p.unknown || []).length
    ? `<div class="lvwarn">有 ${p.unknown.length} 个 run_id 在归档里找不到，已跳过：
        ${esc(p.unknown.slice(0, 5).join('、'))}</div>` : '';
  return `<div class="lvhead"><h2>删除回测归档</h2>
      <a class="btn" id="mclose" href="javascript:void(0)">关闭</a></div>
    ${unknown}
    <div class="lvmsg bad">要删除 <b>${p.n_delete}</b> 次归档，共
      <b>${num(mb, 1)} MB</b>。<b>不可撤销。</b></div>
    <div class="lvwhy" style="margin:6px 0 10px">
      删之前会把每一次的<b>结论</b>（参数 + 全部指标）抽进
      <code>runs/_pruned_conclusions.jsonl</code> 永久留存 ——
      所以"那轮跑出来多少"以后仍然查得到；没了的是权益曲线、成交明细、
      逐日持仓与源码快照（<b>重跑该回测可再生成</b>）。</div>
    ${list}${held}
    <div style="margin-top:14px;display:flex;gap:8px">
      <a class="btn on" id="delok" href="javascript:void(0)">确认删除 ${p.n_delete} 次</a>
      <a class="btn" href="javascript:void(0)" onclick="(document.getElementById('mclose')||{}).onclick()">取消</a>
    </div>`;
}

async function delDo(p, force, close){
  const ok = $('#delok');
  if(ok) ok.textContent = '删除中…';
  let r;
  try{
    r = await post('/api/runs/delete',
      {run_ids: DELSEL, confirm: true, force_protected: !!force});
  }catch(e){ if(ok) ok.textContent = '失败：' + e; return; }
  if(r.error && !r.n_deleted){ if(ok) ok.textContent = r.error; return; }
  DELSEL = [];
  if(close) close();
  delSetMode(false);
  /* 重新拉归档列表 —— 不刷的话页面上还看得到已经删掉的行 */
  if(typeof loadRuns === 'function') loadRuns();
  else location.reload();
}
