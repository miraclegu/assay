/* 看板共享 JS。★ 只有一份 —— 每个页面 <script src="/common.js"> 它。
   原来这些内联在 index.html 里；加独立页面时若各复制一份，改一个格式化函数
   就得改 N 处，而漏改的那页会和别处显示得不一样（不报错，只是数看着不对）。

   这里只放【真正跨页共享】的：DOM 取值、取数、转义、数字格式化、代码归一、
   顶栏导航。页面各自的逻辑留在各自文件里。 */

const $ = s => document.querySelector(s);
const $$ = s => Array.from(document.querySelectorAll(s));

const esc = t => String(t == null ? '' : t).replace(/[&<>"']/g,
  c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

/* ---- 数字格式化 -------------------------------------------------------
   ★ 单位在这个项目里是【混着】的，所以格式化函数按单位分开命名，
     调用处一眼能看出这个字段是百分数还是小数：
       pct  / ratv  —— 传【小数】(0.1 -> +10.00%)
       pctv / pctn  —— 传【百分数】(-1.49 -> -1.49%)，pctn 不带符号
     猜错 100 倍不报错，所以宁可函数名长一点。 */
const fmtN = (v, d = 2) => v == null ? '—' : (+v).toFixed(d);
const pct  = (v, d = 2) => v == null ? '—' : (v * 100).toFixed(d) + '%';
const ratv = (v, d) => v == null ? '—'
  : (v >= 0 ? '+' : '') + (v * 100).toFixed(d == null ? 2 : d) + '%';
const pctv = (v, d) => v == null ? '—'
  : (v >= 0 ? '+' : '') + (+v).toFixed(d == null ? 2 : d) + '%';
/* 换手/振幅/限幅不带符号 —— 它们不会为负，带个 + 读着像涨跌 */
const pctn = (v, d) => v == null ? '—' : (+v).toFixed(d == null ? 2 : d) + '%';
const sign = v => v == null ? '' : (v > 0 ? 'pos' : (v < 0 ? 'neg' : ''));
const upc  = v => v == null ? '' : (v > 0 ? 'var(--up)' : v < 0 ? 'var(--down)' : 'var(--dim)');
const yiv  = v => v == null ? '—'
  : Math.abs(v) >= 1e8 ? (v / 1e8).toFixed(2) + '亿'
  : Math.abs(v) >= 1e4 ? (v / 1e4).toFixed(1) + '万' : (+v).toFixed(0);
function num(x, d) {
  return (x == null || x === '' || isNaN(x)) ? '—'
    : Number(x).toLocaleString('zh-CN',
        {minimumFractionDigits: d == null ? 0 : d,
         maximumFractionDigits: d == null ? 0 : d});
}

/* ---- 取数 -------------------------------------------------------------
   ★ 404 要说"服务端可能是旧进程" —— 页面每次请求都从磁盘读，而 Python
     模块只在进程启动时加载一次，长时间开着的服务会出现"新页面 + 旧 API"。 */
async function j(u) {
  const r = await fetch(u);
  const txt = await r.text();
  if (!r.ok) {
    const path = u.split('?')[0];
    throw new Error(r.status === 404
      ? `接口 ${path} 不存在（服务端可能是旧进程，重启 serve.py 再试）`
      : `${path} 返回 HTTP ${r.status}：${txt.slice(0, 120)}`);
  }
  let o;
  try { o = JSON.parse(txt); }
  catch (e) { throw new Error(`${u.split('?')[0]} 返回的不是 JSON：${txt.slice(0, 120)}`); }
  if (o && o.error) throw new Error(o.error);
  return o;
}

async function post(u, body) {
  const r = await fetch(u, {method: 'POST',
    headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
  const t = await r.text();
  let o;
  try { o = JSON.parse(t); } catch (e) { throw new Error(t.slice(0, 150)); }
  if (o && o.error) throw new Error(o.error);
  return o;
}

/* ---- KPI 一格 ---- */
function cell(k, v, sub, cls) {
  return `<div class="${cls || ''}"><div class="k">${k}</div>
    <div class="v">${v}</div>${sub ? `<div class="s">${sub}</div>` : ''}</div>`;
}

/* ---- 代码 ----
   ★ 与 assay/stock.py: norm_code 同一套规则。判不出来返回 null 而不抛错 ——
     搜索框每敲一个字都会调它，半个代码不是错误。 */
const _MK = {SH:'XSHG', SS:'XSHG', XSHG:'XSHG', SZ:'XSHE', XSHE:'XSHE'};
const _PF = {'60':'XSHG','68':'XSHG','90':'XSHG','00':'XSHE','30':'XSHE','20':'XSHE'};
function normCode(s) {
  const t = String(s || '').trim().toUpperCase();
  const m = /\d{6}/.exec(t);
  if (!m) return null;
  const num6 = m[0];
  const rest = (t.slice(0, m.index) + t.slice(m.index + 6)).replace(/^[.\-_ ]+|[.\-_ ]+$/g, '');
  let mk = rest ? _MK[rest] : null;
  if (rest && !mk) return null;
  mk = mk || _PF[num6.slice(0, 2)];
  return mk ? num6 + '.' + mk : null;
}
/* 板块标记（沪/深/创/科/北）—— 颜色与 :root 的调色板一致 */
function boardTag(code) {
  const n = (code || '').slice(0, 3);
  if (n === '300' || n === '301') return ['创', '#e0a33c'];
  if (n === '688') return ['科', '#a06bf0'];
  if (n === '430' || n[0] === '8' || n[0] === '4') return ['北', '#3ec8d8'];
  if ((code || '')[0] === '6') return ['沪', '#5b9cf0'];
  return ['深', '#2fb87a'];
}

/* ---- 顶栏导航 --------------------------------------------------------
   ★ 一处定义，每页调用 —— 分页面各写一份的话，加一个入口就得改 N 个文件，
     而漏改的那页少一个入口（不报错，只是从那页走不到新功能）。
   页面用 `data-nav` 标出自己是谁，对应入口高亮。 */
/* ★ 按【使用频率】分四组，不是平铺 9 个 ——
   平铺时每次都要在 9 个里扫一遍才找到要去的地方，而它们的重要性差很远：

     实盘   唯一回答"今天要做什么"的地方，每个交易日都要看  -> 放最前 + 告警红点
     市场   收盘后看行情、研究某只票                      -> 一组
     研究   回测归档，做策略时才进                        -> 单独
     数据   新鲜度与口径，出问题或写代码时才进            -> 单独，字典并进来

   分隔线是【信息】不是装饰：它告诉人"这几个是一类，可以一起扫"。 */
const NAV = [
  ['/#/live',         '💰 实盘',     '今日待办 / 持仓 / 录成交 / 版本留痕', 'live', 'act'],
  ['/market.html',    '🌡 盘面',     '某天的全市场：涨跌分布 / 涨跌停 / 行业榜 / 各类榜单', 'market', 'mkt'],
  ['/stock.html',     '📈 个股',     '按代码或名称查：K 线 / 副图 / 事件 / 财务 / 同业', 'stock', 'mkt'],
  ['/sector.html',    '🏭 板块',     '申万行业 + 通达信板块：涨幅榜 / 成分股穿透', 'sector', 'mkt'],
  ['/watchlist.html', '⭐ 自选',     '自选股分组与盯盘列表', 'watch', 'mkt'],
  ['/compare.html',   '⚖ 对比',     '2~6 只叠加后复权涨幅曲线', 'compare', 'mkt'],
  ['/#/runs',         '📚 回测',     '回测归档目录树 / 详情 / 选中的规则', 'runs', 'res'],
  ['/#/sync',         '🔄 数据',     '数据新鲜度 / 手动同步 / 财务导入 / 口径字典', 'sync', 'data'],
];

function navHtml(cur) {
  let last = null;
  return NAV.map(([href, t, tip, key, grp]) => {
    const sep = (last !== null && grp !== last) ? '<span class="navsep"></span>' : '';
    last = grp;
    /* 实盘那个入口留一个挂红点的位置 —— 有待办时点亮（见 navAlert）。
       ★ 判据由服务端给（live.signal_alert），前端只负责显示。 */
    const dot = key === 'live' ? '<span class="adot navdot" id="navdot"></span>' : '';
    return `${sep}<a class="btn nav${key === cur ? ' on' : ''}" href="${href}"
       title="${esc(tip)}">${t}${dot}</a>`;
  }).join('');
}

/* 顶栏「实盘」上的红点：任一账户有待办就点亮。
   ★ 异步补，不拖住页面渲染 —— 它是锦上添花，取不到就不显示，
     不该让整页等它。 */
async function navAlert() {
  const d = $('#navdot');
  if (!d) return;
  d.style.display = 'none';
  try {
    const o = await j('/api/live/accounts');
    const hit = (o.accounts || []).filter(a => a.alert);
    if (!hit.length) return;
    d.style.display = 'inline-block';
    d.title = hit.map(a => a.name + '：' + (a.alert_why || []).join('；')).join('\n');
  } catch (e) { /* 只读模式或旧进程：不显示，不报错 */ }
}

/* 从站内别处跳过来时给一个「‹ 返回」。
   ★ 判据用 `document.referrer` 且**同源**，再加 `history.length > 1`。
   🔴 `history.length` 这条不能少：用 target="_blank" 从实盘页打开时
     **有 referrer 但没有可回的历史**（新标签的 history.length === 1）——
     只看 referrer 的话按钮会显示出来，点了却什么都不发生。
     "给一个点了没反应的返回按钮比不给更糟。"
   ★ 用 history.back() 而不是跳 referrer：back 能保留原页面的滚动位置与
     状态（实盘页可能正开着浮层）。 */
function backLink() {
  if (history.length <= 1) return '';
  try {
    const r = document.referrer;
    if (!r || new URL(r).origin !== location.origin) return '';
    if (new URL(r).pathname === location.pathname
        && new URL(r).search === location.search) return '';   /* 自己跳自己 */
  } catch (e) { return ''; }
  return '<button class="btn" id="goback" title="回到刚才那一页">‹ 返回</button>';
}
function wireBack() {
  const b = $('#goback');
  if (b) b.onclick = () => history.back();
}

/* 每个独立页面的骨架头。★ 页面标题也在这里出，浏览器标签页才分得清。
   `extra` 放页面自己的控件（回测归档那页的返回/过滤/全部展开）——
   它们只在那一页有意义，但必须和导航在同一条栏里，否则顶栏会变两行。 */
function pageHead(cur, title, extra) {
  document.title = title + ' · assay';
  const el = $('#top');
  if (!el) return;
  /* ★ 「assay」这个标题本身就是【首页】入口，所以 NAV 里不再单列一项 ——
     左上角的站名点回首页是通用约定，多一个"🏠 首页"按钮是冗余。
     在首页时把它点亮，否则"我在哪"就没有指示。 */
  el.innerHTML = `<h1><a href="/" class="${cur === 'home' ? 'homeon' : ''}"
      style="text-decoration:none" title="回总览首页"><b>assay</b></a>
      ${esc(title)}</h1>${backLink()}${extra || ''}
    <div class="sp"></div>${navHtml(cur)}`;
  wireBack();
  navAlert();
}

/* ---- 个股搜索框 ------------------------------------------------------
   ★ 一处实现，个股页 / 对比页 / 自选页都用它。三处各写一遍的话，
     键盘导航、防抖、点外面关闭这些细节必然有一处漏掉。
   opts: {onPick(row), placeholder, keep} —— keep=true 时选中后不清空输入框。 */
function mountSearch(box, opts) {
  const o = opts || {};
  box.classList.add('skbox');
  box.innerHTML = `<input class="skq" autocomplete="off"
      placeholder="${esc(o.placeholder || '代码或名称，如 601857 / 中国石油 / sh601857')}">
    <div class="skdd"></div>`;
  const q = box.querySelector('input'), dd = box.querySelector('.skdd');
  let idx = -1, rows = [], timer = null;
/* 🔴 打开要写 'block'，不能写 ''。
   `.skdd` / `.cvtip` 的 `display:none` 来自【样式表规则】（common.css），
   而 `style.display=''` 只是**删掉内联样式** —— 规则照旧生效，元素还是隐藏的。
   内联 style="display:none" 的老写法下 '' 是对的，搬进 CSS 之后就不对了。
   表现是"下拉/读数永远不出来"，而没有任何报错。 */
  const close = () => { dd.style.display = 'none'; idx = -1; };
  const draw = () => {
    if (!rows.length) {
      dd.innerHTML = '<div class="lvwhy" style="padding:8px">没有匹配</div>';
      dd.style.display = 'block'; return;
    }
    dd.innerHTML = rows.map((r, i) => {
      const [bt, bc] = boardTag(r.code);
      return `<div class="skit${i === idx ? ' on' : ''}" data-i="${i}">
        <span style="font-family:'SF Mono',Menlo,monospace;font-size:11px;color:var(--dim)">${esc(r.code.slice(0, 6))}</span>
        <b style="font-size:12px">${esc(r.name)}</b>
        <span class="lvwhy" style="color:${bc}">${bt}</span>
        ${r.is_st ? '<span class="stale">ST</span>' : ''}
        <span class="lvwhy">${esc(r.industry || '')}</span>
        <span style="flex:1"></span>
        <span class="lvwhy" style="font-variant-numeric:tabular-nums">${yiv(r.floatmv)}</span>
      </div>`;
    }).join('');
    dd.style.display = 'block';
    dd.querySelectorAll('.skit').forEach(e =>
      e.onclick = () => pick(rows[+e.dataset.i]));
  };
  const pick = r => {
    close();
    if (!o.keep) q.value = '';
    if (o.onPick) o.onPick(r);
  };
  q.oninput = () => {
    clearTimeout(timer);
    const t = q.value.trim();
    if (!t) { close(); return; }
    /* 防抖 160ms —— 每敲一个字打一次接口在 5200 行上没必要 */
    timer = setTimeout(async () => {
      try {
        const r = await j('/api/stock/search?limit=20&q=' + encodeURIComponent(t));
        rows = r.results || []; idx = rows.length ? 0 : -1; draw();
      } catch (e) {
        dd.innerHTML = '<div class="lvmsg bad">' + esc(String(e)) + '</div>';
        dd.style.display = 'block';
      }
    }, 160);
  };
  q.onkeydown = ev => {
    if (ev.key === 'Escape') { close(); return; }
    if (dd.style.display === 'none' || !rows.length) return;
    if (ev.key === 'ArrowDown') { ev.preventDefault(); idx = Math.min(idx + 1, rows.length - 1); draw(); }
    else if (ev.key === 'ArrowUp') { ev.preventDefault(); idx = Math.max(idx - 1, 0); draw(); }
    else if (ev.key === 'Enter') { ev.preventDefault(); if (rows[idx]) pick(rows[idx]); }
  };
  document.addEventListener('mousedown', ev => {
    if (!box.contains(ev.target)) close();
  });
  return {input: q, focus: () => q.focus()};
}

/* ---- 自选星 ----------------------------------------------------------
   ★ 只读模式（没开 --live）时点了要说清原因，不能静默无反应。 */
async function toggleStar(el, code, on) {
  try {
    await post('/api/watchlist', {act: on ? 'remove' : 'add', code: code});
    el.classList.toggle('on', !on);
    el.dataset.on = on ? '' : '1';
    el.title = on ? '加入自选' : '移出自选';
  } catch (e) {
    alert(String(e));
  }
}
function starHtml(code, on) {
  return `<span class="star${on ? ' on' : ''}" data-star="${esc(code)}"
    data-on="${on ? '1' : ''}" title="${on ? '移出自选' : '加入自选'}">★</span>`;
}
function wireStarsAll() {
  $$('[data-star]').forEach(e => e.onclick = ev => {
    ev.stopPropagation(); ev.preventDefault();
    toggleStar(e, e.dataset.star, !!e.dataset.on);
  });
}
/* 个股链接 —— 每处都跳同一个地方，别各拼一遍 URL */
const stockHref = c => '/stock.html?code=' + encodeURIComponent(c);
const sectorHref = (c, k) => '/sector.html?kind=' + encodeURIComponent(k || 'sw')
  + '&code=' + encodeURIComponent(c);
/* URL 参数 */
const qs = k => new URLSearchParams(location.search).get(k);

/* ---- Canvas 通用 ----
   ★ 放这里而不是 kchart.js：盘面页的分布图也用，而它不需要 K 线那套。 */
function cssv(name, dflt) {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || dflt;
}

/* 通用：把 canvas 按 dpr 准备好，返回 {g, W, H} */
function cvPrep(cv) {
  const dpr = window.devicePixelRatio || 1;
  const W = cv.clientWidth, H = cv.clientHeight;
  cv.width = Math.max(1, Math.round(W * dpr));
  cv.height = Math.max(1, Math.round(H * dpr));
  const g = cv.getContext('2d');
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.clearRect(0, 0, W, H);
  return {g, W, H};
}

