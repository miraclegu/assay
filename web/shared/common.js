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
/* 涨跌配色的**唯一**定义。🔴 `0` 走中性色（`--dim`）而不是涨色：
   "平盘"和"微涨"是两件事，把 0 画成红的等于凭空报了个涨。
   ★ 判据用 `> 0` / `< 0` 两头夹，不是 `>= 0` —— 后者会把 0 并进涨。 */
const upc  = v => v == null ? '' : (v > 0 ? 'var(--up)' : v < 0 ? 'var(--down)' : 'var(--dim)');
/* 万 / 亿折算的【唯一】定义：阈值（1e8 / 1e4）、单位、要不要空格都在这儿。
   🔴 此前**四处各写一遍**（这里 / chart.js 的 _money / kchart.js 的刻度 /
     live-why.js 的 lvFmt），四套小数规则加一套空格 —— 同一个
     13,844,147 在四个页面长成 `1384.4万` / `1384万` / `1384 万`。
     同「名称+代码那一格全站一处定义 cnCell」那条：**同一份信息在每个
     页面长得都不一样**，而它不报错。
   ★ 小数位仍由调用方给（y 轴上要短、tooltip 里可以精确、成交量轴另有
     一套分档），传函数就能按量级挑 —— 变的只是精度，**折算规则只有一份**。
   ★ `small` 是不足 1 万时的位数。`sp` 给 `' '` 就是「1384 万」那种带空格的。 */
function yiv(v, o){
  if(v == null || v === '' || !isFinite(Number(v))) return '—';
  v = Number(v); const a = Math.abs(v); o = o || {};
  const sp = o.sp || '';
  const pick = (d, dflt) => typeof d === 'function' ? d(a) : (d == null ? dflt : d);
  if(a >= 1e8) return (v / 1e8).toFixed(pick(o.yi,    2)) + sp + '亿';
  if(a >= 1e4) return (v / 1e4).toFixed(pick(o.wan,   1)) + sp + '万';
  return v.toFixed(pick(o.small, 0));
}
/* 盈亏金额 -> 带符号的整数（`+1,234` / `-987` / `0`）。`unit` 例如 `' 元'`。
   🔴 符号判据是 `> 0` **两头夹**，不是 `>= 0` —— 后者会把**平盘**写成
     `+0`，那等于凭空报了个赚（同 `upc` 那条）。
   🔴 null 要给 `—`：`null >= 0` 在 JS 里是 **true**，老写法会吐出
     `+— 元` 这种东西（live-perf 的图里实测就是）。 */
/* 🔴 舍入交给 `num`（`toLocaleString`）——它是**对称**的（±99.5 各自朝外）。
     `Math.round` 一律朝 +∞：`Math.round(-99.5)` 是 **-99**，于是 +99.5 与
     -99.5 朝同一个方向舍，盈亏上那是个说不通的偏向（对数当场抓到）。 */
const pnlv = (v, unit) => (v == null || v === '' || !isFinite(Number(v)))
  ? '—' : (Number(v) > 0 ? '+' : '') + num(Number(v), 0) + (unit || '');
function num(x, d) {
  return (x == null || x === '' || isNaN(x)) ? '—'
    : Number(x).toLocaleString('zh-CN',
        {minimumFractionDigits: d == null ? 0 : d,
         maximumFractionDigits: d == null ? 0 : d});
}

/* ---- 取数 -------------------------------------------------------------
   ★ 404 要说"服务端可能是旧进程" —— 页面每次请求都从磁盘读，而 Python
     模块只在进程启动时加载一次，长时间开着的服务会出现"新页面 + 旧 API"。 */
/* 🔴🔴 服务端说了人话，就别再拿 HTTP 外壳把它埋掉。
     原来 500 那支直接吐 `${path} 返回 HTTP 500：{"error": "本地还没有…"}` ——
     屏幕上就是一行带引号的 JSON，而**那句人话就在里面**。空 lake 上首页
     四块全是这个样子（用户原话：「看起来还是有一些报错信息」）。
     现在先解析 body：服务端给了 `error` 就用它，解析不出来才退回原文。
   🔴 `no_data` / `missing` 要**挂到 Error 对象上**带出去 —— 调用方
     统统是 `catch(e => ...String(e))`，标志丢在这里的话页面分不出
     「本地还没有数据」（安静的空态 + 一个入口）与「真出错了」（红字）。 */
function _errOf(txt, path, status) {
  let o = null;
  try { o = JSON.parse(txt); } catch (e) { o = null; }
  const msg = (o && o.error) ? o.error
    : (status === 404
        ? `接口 ${path} 不存在（服务端可能是旧进程，重启 serve.py 再试）`
        : status
          ? `${path} 返回 HTTP ${status}：${txt.slice(0, 120)}`
          : `${path} 返回的不是 JSON：${txt.slice(0, 120)}`);
  const err = new Error(msg);
  if (o && o.no_data) { err.no_data = true; err.missing = o.missing || ''; }
  return err;
}

async function j(u) {
  const r = await fetch(u);
  const txt = await r.text();
  const path = u.split('?')[0];
  if (!r.ok) throw _errOf(txt, path, r.status);
  let o;
  try { o = JSON.parse(txt); }
  catch (e) { throw _errOf(txt, path, 0); }
  if (o && o.error) throw _errOf(txt, path, 200);
  return o;
}

/* ---- 一块内容取不到时的统一渲染 --------------------------------------
   🔴 **本地还没有数据 != 出错了**，所以不能都刷成红字：顶上那条横条已经
     说过一次「本地还没有数据 · ▷ 开始建本地数据」，下面四块再各喊一遍红的
     就是**常驻告警**，而常驻告警等于教人忽略这个位置。所以 `no_data` 走
     安静的空态（`.lvmsg` 不带 `.bad`），真出错了才红。
   ★ 缺的那个 glob 路径进 `title` —— 它是诊断信息，摆在屏幕上会把
     「我该做什么」挤没（同「能进 tooltip 的就别占列」）。
   ★ 入参既收 Error（页面的 catch）也收 `{error, no_data, missing}`
     （首页那种把异常转成对象的），免得调用方各自判一遍。 */
function errHtml(e, what) {
  const o = (e && typeof e === 'object') ? e : {};
  const nd = !!o.no_data;
  const msg = String(o.error || (o.message != null ? o.message : '')
                     || e || ('取不到' + (what || '数据')));
  const t = (nd && o.missing) ? ' title="' + esc('缺：' + o.missing) + '"' : '';
  return '<div class="lvmsg' + (nd ? '' : ' bad') + '"' + t + '>'
       + esc(msg) + '</div>';
}


/* ---- A 腿「落后几个交易日」的三态判断（唯一一处）--------------------
   🔴🔴 **`null`（不知道）与 `0`（真的最新）是两件事。** home.js 原来写的是
     `x.lag_days ? 落后 : 最新` —— 于是空 lake 上服务端诚实给的 `null` 被
     说成绿色的「最新」，**本地一个字节数据都没有，首页却三行「最新」**
     （同 `upc` 那条「判据要 > 0 / < 0 两头夹」、同「拿不到分红那一格标
     查不到，不猜一个数」）。而 sync.js 里那份判断一直是对的 ——
     两处各写一份，必然分叉。
   ★ 共享的是**判断**，不是标记：两页的表样式不同，各自包自己的 <td>。 */
function lagTag(x, unit) {
  const u = unit || '交易日';
  /* 🔴 顺序要紧：**先问"有没有这份数据"，再问"是不是出错了"**。
     反过来的话，空 lake 上这一格是一长串
     `Catalog Error: Table with name "t.raw_kline_daily" does not exist …` ——
     技术上没错，却答不了人要做的事。异常文本进 `title`（同「能进 tooltip
     的就别占列」）。**真有数据却又报错**才是异常，那时才红。 */
  if (!x || x.max == null)
    return {t: '还没有', c: 'var(--dim)',
            title: (x && x.error) ? String(x.error) : ''};
  if (x.error) return {t: String(x.error), c: 'var(--up)', title: ''};
  const lag = x.lag_days;
  if (lag == null) return {t: '—', c: 'var(--dim)', title: ''};
  if (lag === 0) return {t: '最新', c: 'var(--down)', title: ''};
  return {t: '落后 ' + lag + ' ' + u, c: 'var(--warn)', title: ''};
}

/* lagTag 的结果 -> 一段 <span>（两页共用，免得 title 只有一边挂上）。 */
function lagSpan(x, unit) {
  const t = lagTag(x, unit);
  return '<span style="color:' + t.c + '"'
       + (t.title ? ' title="' + esc(t.title) + '"' : '')
       + '>' + esc(t.t) + '</span>';
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
function cell(k, v, sub, cls, attr) {
  /* ★ `attr` 是给**原地更新**用的（实盘页每分钟刷一次，重建整块 DOM 会让
     页面跳一下）：调用方传 `data-rtk="equity"`，轮询时按它找到 .v 改数字。 */
  return `<div class="${cls || ''}"${attr ? ' ' + attr : ''}><div class="k">${k}</div>
    <div class="v">${v}</div>${sub ? `<div class="s">${sub}</div>` : ''}</div>`;
}

/* ---- ⓘ 口径说明 ------------------------------------------------------
   ★ **解释放进点开的浮块，不占主页面。** 主视图上每多一行"这个数是怎么
     算的"，天天要看的那几个数字就被推远一屏 —— 而口径只在头一次看时要读。
   🔴 但【警告】不许进来：警告要一直看得见。"有 3 笔费用是估算的"藏起来
     等于没有。这里只放"这个数是怎么算的"，不放"这个数可能不对"。
   ★ 用 class 开关而不是 style.display —— `.hlpbox` 的 display:none 写在
     样式表里，`style.display=''` 只是删掉内联样式，规则照旧生效（点了
     没反应，且不报错）。 */
function helpIcon(id, html) {
  return `<a href="#" class="hlp" data-h="${id}" title="口径说明">ⓘ</a>
    <div class="hlpbox" id="hlpb_${id}">${html}</div>`;
}
function bindHelp() {
  $$('.hlp').forEach(a => a.onclick = ev => {
    ev.preventDefault(); ev.stopPropagation();
    const b = document.getElementById('hlpb_' + a.dataset.h);
    if (!b) return;
    const was = b.classList.contains('on');
    $$('.hlpbox.on').forEach(x => x.classList.remove('on'));
    if (!was) b.classList.add('on');
  });
  /* 点别处收起 —— 浮块盖住下面的表格，留着不动会挡住要看的数字。 */
  document.addEventListener('click', () =>
    $$('.hlpbox.on').forEach(x => x.classList.remove('on')));
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
/* 🔴 **板块与对比不在顶栏里，它们是【子页】**（2026-09-14 合并）：
     🏭 板块 -> 从「🌡 盘面」进（盘面就是"今天各板块怎么样"，板块页是它的下钻）
     ⚖ 对比 -> 从「📈 个股」进（对比一定是"拿这只和别的比"，起点就是个股）
   ★ 两个页面**都还在**（`/sector.html` / `/compare.html`），书签与深链接
     一个都没失效 —— 那是产品契约（同「.html 一律留在根目录」那条）。
     变的只是顶栏少两个按钮：平铺 9 个时每次都要在 9 个里扫一遍。
   🔴 子页的顶栏要点亮**父级**（sector -> market、compare -> stock），
     否则进去之后"我在哪"没有任何指示（NAV 里已经没有它们自己那一项了）。
   🔴 合并的前提是**入口必须真的通到全部功能**，否则就是把功能藏起来：
     盘面原来只链得到申万一类（行业表每行），通达信那 657 个板块
     （概念/风格/地区/研究）只能从顶栏进 —— 所以盘面上补了类别 chip。
     selftest 里两条都钉着。 */
const NAV = [
  ['/#/live',         '💰 实盘',     '今日待办 / 持仓 / 录成交 / 版本留痕', 'live', 'act'],
  ['/alerts.html',    '🎯 买点',     '股息率买点清单：手填目标价/目标股息率，到价提醒', 'alerts', 'act'],
  ['/market.html',    '🌡 盘面',     '某天的全市场：涨跌分布 / 涨跌停 / 行业榜 / 各类榜单', 'market', 'mkt'],
  ['/stock.html',     '📈 个股',     '按代码或名称查：K 线 / 副图 / 事件 / 财务 / 同业 / 拉去对比', 'stock', 'mkt'],
  ['/watchlist.html', '⭐ 自选',     '自选股分组与盯盘列表', 'watch', 'mkt'],
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
    const dot = key === 'live'
      ? '<span class="adot navdot" id="navdot"></span>'
      : (key === 'alerts'
         ? '<span class="adot navdot" id="navdot2"></span>' : '');
    return `${sep}<a class="btn nav${key === cur ? ' on' : ''}" href="${href}"
       title="${esc(tip)}">${t}${dot}</a>`;
  }).join('');
}

/* 顶栏「实盘」上的红点：任一账户有待办就点亮。
   ★ 异步补，不拖住页面渲染 —— 它是锦上添花，取不到就不显示，
     不该让整页等它。 */
async function navAlert() {
  /* 🔴 先起「买点」那个红点：写在下面的话，一旦实盘没有待办就
     early-return 掉，买点的红点永远不亮 —— 而它不报错。 */
  alertDot();
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

/* 顶栏「买点」上的红点：有跌破/接近任一档就点亮。
   ★ 判据来自服务端（alerts.valued 的 n_hit / n_near）—— 前端不自己比价，
     那会变成第二份实现，而"接近"的阈值是每行可覆盖的。 */
async function alertDot() {
  if (!$('#navdot2')) return;
  try { setAlertDot(await j('/api/alerts')); }
  catch (e) { /* 清单空 / 只读：不显示 */ }
}
/* ★ 拆出一个"拿现成数据点灯"的入口：买点页自己已经取过那份数据了，
     再让红点去打一遍接口就是同一份数据取两遍（这条链上唯一的风险是限流）。
   ★ 而且买点页【改完一行要立刻更新红点】—— 只在页面加载时点一次的话，
     刚加的那只到价了红点还是灭的，而它不报错。 */
function setAlertDot(o) {
  const d = $('#navdot2');
  if (!d) return;
  d.style.display = 'none';
  if (!o || o.error || !(o.n_hit || o.n_near)) return;
  d.style.display = 'inline-block';
  d.title = (o.n_hit ? o.n_hit + ' 只跌破目标价' : '')
    + (o.n_hit && o.n_near ? '、' : '')
    + (o.n_near ? o.n_near + ' 只接近' : '');
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
        /* ★ `filter` 让调用方剔掉不该出现的候选 —— 加在**这里一处**，
             不是让每个调用方自己在 onPick 里拦：拦在 onPick 是"点了才说不行"，
             而候选本来就不该列出来（同「列出来点了什么都不出来比不给更糟」）。
             实盘买入用它排除**指数** —— 指数买不了，而且 `normalize_code`
             对 `sh000001` 会报"自相矛盾"，那个报错指不到真正的原因。 */
        rows = (r.results || []).filter(o.filter || (() => true));
        idx = rows.length ? 0 : -1; draw();
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

/* 🔴 **「名称 + 小字代码」放同一格 —— 唯一定义在这里。**
   用户 2026-09-16："当前持仓、执行差异、交易记录中的名称和代码的排布方式，
   应该都做成类似于每日持仓、清仓记录中的样式（名称、代码放在一个格子里），
   现在不同地方的排布方式不同。"

   改之前这份 HTML 有**三份各自的写法**（app.js 的 `nmcode`、run-detail 的
   `nmpop`、live-perf 里内联的一段），另有五六张表干脆拆成"代码"与"名称"
   两列 —— 于是同一份信息在每个页面长得都不一样，而**那不报错**。
   ★ 合成一格还顺带省出一列：那些表本来就宽（十来列），代码列占着一格
     而人扫的是名称。
   ★ `opt` 直接透给 `spLink`（date / run / cls）—— 点开速览浮层并定位到
     那一天的能力，各处保持一致。 */
function cnCell(code, name, opt) {
  if (!code) return esc(name || '');
  const html = name
    ? `${esc(name)}<span class="cd">${esc(code)}</span>`
    : `<span class="cd0">${esc(code)}</span>`;
  return spLink(code, name || code, (opt || {}).cls || '',
                Object.assign({}, opt || {}, {html: html}));
}
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


/* ============ 通用表格（可排序，带分组分隔行）============
   原来定义在 `views/run-detail.js` 里，而 `views/live-perf.js` 跨域引用它
   —— 域文件拥有一个通用件，等于"谁先写谁就是家"。2026-09-24 搬来这里。
   ★ 只依赖 `sign()`（本文件），列的格式化函数由调用方给，所以它对
     "表里装的是什么"一无所知 —— 那正是它能被两个域共用的原因。 */
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

/* ============ 盘中炸板提示（全局浮窗）============
   🔴 **任何页面都要能看到** —— 所以挂在 common.js 里（6 个独立 .html 与
     index.html 都加载它）。挂在实盘页的话，人正在看个股/盘面时就漏掉了，
     而炸板恰恰是要立刻处理的事。
   🔴 判据全在服务端（`/api/live/intraday`）：
     · "哪些持仓昨天涨停" 要读面板的 is_limit_up（前端没有面板）
     · "现在是不是盘中" 见 realtime.in_session
     · 涨停价用面板的 `limit_up`，不是"涨幅 < 9.9%" —— 涨跌幅限制有
       10%/20%/5% 三档，还会因 ST 状态变化而变，前端自己推必然错
   ★ 去重在**服务端落盘**（同一只票一天只报一次）：重启一次就把今天报过的
     又报一遍的话，通知一多就没人看了。
   ★ 10:00 之前不扫（服务端的 SCAN_FROM）：开盘半小时开板/回封频繁，
     早报多半是假告警。 */
const ZB_MS = 5 * 60 * 1000;          // 每 5 分钟扫一次
const ZB_MUTE_MS = 60 * 60 * 1000;    // Mute 一小时

function zbMuted(){
  try {
    const t = +(localStorage.getItem('zbmute') || 0);
    return t > Date.now() ? t : 0;
  } catch (e) { return 0; }
}

function zbMute(){
  try { localStorage.setItem('zbmute', String(Date.now() + ZB_MUTE_MS)); }
  catch (e) { /* 隐私模式写不了 —— 不该因此打挂页面 */ }
  const b = document.getElementById('zbbox');
  if(b) b.remove();
}

function zbBox(items){
  /* 🔴 已经在显示时**不要重建** —— 重建会把人正在读的那条抹掉重排；
     新的追加进去就好。 */
  let box = document.getElementById('zbbox');
  if(!box){
    box = document.createElement('div');
    box.id = 'zbbox';
    document.body.appendChild(box);
  }
  const seen = new Set([...box.querySelectorAll('[data-c]')]
    .map(x => x.dataset.c));
  const fresh = items.filter(x => !seen.has(x.code));
  if(!box.querySelector('.zbhd')){
    box.innerHTML = `<div class="zbhd">🔴 炸板离场提示
        <span class="zbx" id="zbclose" title="关掉（下次再触发还会弹）">×</span>
      </div><div id="zblist"></div>
      <div class="zbft">
        <a href="#" id="zbmute">Mute 1 小时</a>
        <a href="#" id="zbmore">去实盘页</a>
      </div>`;
    box.querySelector('#zbclose').onclick = () => box.remove();
    box.querySelector('#zbmute').onclick = ev => { ev.preventDefault(); zbMute(); };
    box.querySelector('#zbmore').onclick = ev => {
      ev.preventDefault();
      location.href = '/#/live/' + encodeURIComponent(items[0].account || '');
    };
  }
  const list = box.querySelector('#zblist');
  fresh.forEach(x => {
    const d = document.createElement('div');
    d.className = 'zbrow';
    d.dataset.c = x.code;
    d.innerHTML = `<b>${esc(x.name || x.code)}</b>
      <span class="zbc">${esc(x.code)}</span>
      <div class="zbd">现价 <b>${num(x.px, 2)}</b> / 涨停 ${num(x.limit, 2)}
        （${x.pct == null ? '' : ((x.pct * 100).toFixed(2) + '%')}）
        · ${esc(x.account_name || x.account || '')}
        ${x.at ? '· ' + esc(String(x.at).slice(11, 16)) : ''}</div>`;
    list.appendChild(d);
  });
}

async function zbScan(){
  if(zbMuted()) return;
  let o;
  try { o = await j('/api/live/intraday'); }
  catch (e) { return; }              /* 静默失败：提示挂了不该影响页面 */
  if(!o || !o.session) return;       /* 判据来自服务端，前端不自己判时段 */
  if((o.fresh || []).length) zbBox(o.fresh);
}

/* ★ 页面加载后先扫一次，再进入 5 分钟节奏 —— 刚打开页面时也可能已经开板了。
   ★ 用 setInterval 而不是 setTimeout 链：它跟 stopPoll 那套（视图切换会
     清掉的 POLL）**互不干扰** —— 这个提示要在任何页面上都活着。 */
if(typeof window !== 'undefined' && !window.__zbOn){
  window.__zbOn = true;
  setTimeout(zbScan, 3000);
  setInterval(zbScan, ZB_MS);
}

/* ============ 主要指数常驻带子（全局，页面最下方）============
   用户 2026-09-19："把主要的指数都实时获取……始终显示在最上方或最下方，
   不用特别大和显眼，普通字体大小即可。"

   🔴 **挂在 common.js 上** —— 6 个独立 .html 与 index.html 都加载它，
     所以任何页面都看得到（同炸板浮窗那条：挂在某一页的话，
     人正在看别的页面时就没有了）。
   ★ 做成**页面最下方的固定带子**而不是顶栏里一行：顶栏已经有 7 个入口，
     再塞九个数字进去会和"今天要做什么"抢注意力。底部一条 12px 的带子
     一眼扫得到、又不占视线（用户原话就是"不用特别大和显眼"）。
   🔴 **清单与缓存都在服务端**（`realtime.INDICES` / `indices()`）——
     前端硬编码的话，加一个指数页面上不会出现（而那不报错）。
   🔴 **收盘后不轮询**，判据用服务端给的 `session` ——
     前端自己判时段的话，改了时段或遇到半日市会白轮/漏轮
     （同实盘页 `rt_live` 那条）。 */
const IDX_MS = 60000;        // 与服务端 _rt_loop 同节拍
let IDXBAR = null;

/* ---- 显示哪些、什么顺序：本地偏好 ----
   🔴🔴 **存的是「隐藏哪些 + 顺序」，不是「显示哪些」。**
     存"显示列表"的话，服务端**将来加一个指数**时它不在列表里 ->
     **静默不出现**，而没人会发现（同「加一个指标，广场上自动就有」
     「照清单拼会漏掉新文件的全部组合」那两条）。
     存"隐藏列表"则相反：新指数默认就在，要关得显式去关。
   ★ 存 `localStorage` 不进 URL：这是"我习惯看哪几个"的个人设置，
     而 URL 是拿去分享的（同指标参数那条）。
   ★ **服务端始终取全部**，过滤与排序都在前端 —— 多个页面共享同一份
     20 秒缓存，改设置**零请求**（同「一次取全，页签在本地切」）。 */
const IDXPREF = 'idxpref';
let IDXLAST = null;          // 最后一次取到的原始数据，改设置时就地重画

function idxPref(){
  try { const p = JSON.parse(localStorage.getItem(IDXPREF) || '{}');
        return {hide: p.hide || [], order: p.order || []}; }
  catch(e){ return {hide: [], order: []}; }
}
function idxSave(p){
  try { localStorage.setItem(IDXPREF, JSON.stringify(p)); } catch(e){}
}
function idxApply(items){
  const p = idxPref(), h = new Set(p.hide);
  const pos = c => { const i = p.order.indexOf(c);
                     return i < 0 ? 1e6 : i; };   // 没排过的排在后面
  return (items || []).filter(r => !h.has(r.symbol))
    .map((r, i) => [r, i]).sort((a, b) =>
      (pos(a[0].symbol) - pos(b[0].symbol)) || (a[1] - b[1]))
    .map(x => x[0]);
}

async function idxScan(){
  let o = null;
  try { o = await j('/api/rt/indices'); } catch(e){ return; }
  if(!o || !(o.items || []).length) return;   // 取不到就**保持原样**，不闪成空
  IDXLAST = o;
  idxRender(o);
  return o.session;
}

function idxRender(o){
  const el = IDXBAR || (() => {
    const d = document.createElement('div');
    d.id = 'idxbar'; document.body.appendChild(d);
    document.body.classList.add('hasidx');
    return (IDXBAR = d);
  })();
  /* ★ 时间要说出来：现在是实时还是上一个交易日的收盘，
     两者差一天而屏幕上长得一模一样（同「数据日与报价时间写同一个标签」）。*/
  const ts = o.asof || '';
  const when = ts.length >= 12
    ? (ts.slice(4,6) + '-' + ts.slice(6,8) + ' ' + ts.slice(8,10) + ':' + ts.slice(10,12))
    : '';
  /* 🔴 **有的格子没有点位**（自建的「微盘400」是等权组合，
     等权组合没有"点位"这回事）—— `Number(null).toFixed(2)` 会显示成
     `0.00`，而那看着像"这个指数今天是 0 点"。没有就**不显示那一段**。 */
  const show = idxApply(o.items);
  /* 🔴 **全关掉时那个齿轮必须还在** —— 否则设置入口自己消失了，
     人再也打不开这个面板（同 backLink 那条：死路比不给更糟）。 */
  el.innerHTML = show.map(r =>
      `<span class="ix" title="${esc(r.name)}　${esc(r.symbol)}${
           r.n ? '　今日 ' + r.n + ' 只有成交' : ''}">
         <b>${esc(r.short)}</b>${
           r.price == null ? '' : ' ' + Number(r.price).toFixed(2)}
         <i style="color:${upc(r.change_pct)}">${
           (r.change_pct > 0 ? '+' : '') + Number(r.change_pct).toFixed(2)}%</i>
       </span>`).join('')
    + (show.length ? '' : '<span class="ix">指数都关掉了</span>')
    + `<span class="ixw">${o.session ? '实时 ' : '已收盘 '}${esc(when)}`
    + ` <a href="javascript:void(0)" id="idxcfg" title="选显示哪些、排什么顺序">⚙</a></span>`;
  const g = document.getElementById('idxcfg');
  if(g) g.onclick = idxCfgOpen;
  return o.session;
}

/* ---- 设置面板：显示哪些、什么顺序 ----
   ★ 入口就在带子自己上（那个 ⚙）—— 设置该放在它管的东西旁边
     （同个股页把复权/主图收进图上方那个「⚙ 设置」）。
   ★ 用 `.stmodal` 的样式（common.css 里，**所有页面都有**），
     开关逻辑自己写 —— `modal()` 在 `views/app.js` 里，6 个独立 .html
     根本没有它。 */
function idxCfgOpen(){
  if(!IDXLAST) return;
  const all = IDXLAST.items || [];
  let w = document.getElementById('idxcfgw');
  if(!w){ w = document.createElement('div'); w.id = 'idxcfgw';
          document.body.appendChild(w); }
  const draw = () => {
    const p = idxPref(), h = new Set(p.hide);
    const ord = idxApply(all).map(r => r.symbol);
    // 关掉的排在后面，仍然列出来（否则再也打不开它）
    const rows = ord.concat(all.map(r => r.symbol).filter(c => h.has(c)));
    w.className = 'stmodal';
    w.innerHTML = `<div class="stbox" style="max-width:420px">
      <div class="lvhead" style="margin-bottom:8px"><h2>指数带子</h2>
        <span style="flex:1"></span>
        <button class="btn" id="idxdone">关闭</button></div>
      <div class="lvwhy" style="margin-bottom:8px">勾掉的不显示；
        ↑↓ 调顺序。<b>窄屏上带子会横滚</b>，排在后面的要滚出来才看得见，
        所以把最常看的放前面。</div>
      <table class="lvt" style="width:100%">${rows.map((c, i) => {
        const r = all.find(x => x.symbol === c) || {};
        return `<tr><td class="tx" style="width:26px">
            <input type="checkbox" data-c="${esc(c)}"${h.has(c) ? '' : ' checked'}></td>
          <td class="tx">${esc(r.short || c)}
            <span class="lvwhy">${esc((r.name || '').slice(0, 22))}</span></td>
          <td class="rt" style="width:70px">
            <a href="javascript:void(0)" data-up="${esc(c)}"
               style="${i === 0 || h.has(c) ? 'visibility:hidden' : ''}">↑</a>
            <a href="javascript:void(0)" data-dn="${esc(c)}"
               style="${h.has(c) ? 'visibility:hidden' : ''}">↓</a></td></tr>`;
      }).join('')}</table>
      <div style="margin-top:10px"><button class="btn" id="idxrst">恢复默认</button></div>`;
    w.querySelectorAll('input[data-c]').forEach(e => e.onchange = () => {
      const q = idxPref(), c = e.dataset.c;
      q.hide = e.checked ? q.hide.filter(x => x !== c) : q.hide.concat([c]);
      idxSave(q); idxRender(IDXLAST); draw();
    });
    const move = (c, d) => {
      const q = idxPref();
      let o2 = q.order.length ? q.order.slice() : idxApply(all).map(r => r.symbol);
      // 没排过的先补进来，否则 indexOf 是 -1、换不动
      all.forEach(r => { if(o2.indexOf(r.symbol) < 0) o2.push(r.symbol); });
      const i = o2.indexOf(c), jj = i + d;
      if(i < 0 || jj < 0 || jj >= o2.length) return;
      o2[i] = o2[jj]; o2[jj] = c;
      q.order = o2; idxSave(q); idxRender(IDXLAST); draw();
    };
    w.querySelectorAll('a[data-up]').forEach(e =>
      e.onclick = () => move(e.dataset.up, -1));
    w.querySelectorAll('a[data-dn]').forEach(e =>
      e.onclick = () => move(e.dataset.dn, 1));
    const close = () => { w.className = ''; w.innerHTML = '';
                          document.onkeydown = null; };
    document.getElementById('idxdone').onclick = close;
    document.getElementById('idxrst').onclick = () => {
      idxSave({hide: [], order: []}); idxRender(IDXLAST); draw(); };
    w.onclick = ev => { if(ev.target === w) close(); };
    document.onkeydown = ev => { if(ev.key === 'Escape') close(); };
  };
  draw();
}

/* ★ 与炸板提示同一套：`setInterval` 而不是 stopPoll 那套 POLL ——
   视图切换不该把这条带子停掉。
   ★ 收盘后仍然**显示**最后的收盘值（带子不空），只是不再轮询。 */
if(typeof window !== 'undefined' && !window.__idxOn){
  window.__idxOn = true;
  const tick = async () => {
    const live = await idxScan();
    if(live !== false) setTimeout(tick, IDX_MS);
    /* live === false -> 已收盘，停掉；下次打开页面自然会再取一次。 */
  };
  setTimeout(tick, 400);
}

/* 把浮窗放到光标旁边，**放不下就翻到另一侧**。
   🔴 原来固定放右下（clientX+12）—— 光标移到图的最右边时浮窗整块跑到
     视口外面，读数看不见（而这正是最需要看读数的位置：曲线的最新一天）。
   ★ 必须**先填内容再量尺寸**：offsetWidth 在设置 textContent 之前是旧值，
     用它算翻转会翻错边（第一版就这么错过）。
   ★ 最后再把坐标钳进视口 —— 极窄屏下两侧都放不下时，宁可压着光标也要
     让它可见。 */
function _tipAt(tip, cx, cy){
  tip.style.display='block';
  const w=tip.offsetWidth, h=tip.offsetHeight;
  const W=window.innerWidth, H=window.innerHeight, M=12;
  let x=cx+M, y=cy+10;
  if(x+w > W-4) x=cx-w-M;          // 右边放不下 -> 翻到光标左侧
  if(y+h > H-4) y=cy-h-10;         // 下边放不下 -> 翻到光标上方
  tip.style.left=Math.max(4, Math.min(x, W-w-4))+'px';
  tip.style.top=Math.max(4, Math.min(y, H-h-4))+'px';
}

/* ============ 数据加载进度：页面【最上侧】那条横条 ============ */
/* 用户："这些数据加载都应该在 web 端最上侧显示，如果是分步加载的需要显示
   一共多少步，当前多少步，每一步的进度，已用多少时间，预计还要多少时间。"

   🔴 挂在 common.js —— 6 个独立 .html 与 index.html 都加载它，所以
     **任何页面**都看得到（同炸板浮窗、同指数带子那条）。数据加载要几分钟
     到几十分钟，人不会守在数据页上等。
   🔴 数据来自 `/api/progress`，而那一发读的是**进度文件**不是 `_JOBS`
     —— 每天真正跑同步的是 launchd，它不经过 serve.py。
   ★ **没有任务在跑时整条不渲染**：常驻一条"一切正常"等于教人忽略这个位置
     （同顶部横条那条纪律）。刚跑完的 90 秒仍然显示结果，然后自己退场。 */
let PRG = null, PRGT = null;
const PRG_RUN = 2000, PRG_IDLE = 10000;
/* 🔴 展开态**必须是模块级** —— 横条每 2 秒被 innerHTML 整块换掉，
   存在局部里的话刚点开就被下一轮冲掉（同 LVSORT / KPOPEN 那条）。
   ★ 不进 localStorage：它是"我现在想看"，下次打开该回到收起
     （同「待办折叠状态不持久化」）。 */
let PRGOPEN = {};

function fmtDur(s){
  if(s == null || !isFinite(s)) return null;
  s = Math.max(0, Math.round(s));
  const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60), q = s % 60;
  return h ? (h + ':' + String(m).padStart(2,'0') + ':' + String(q).padStart(2,'0'))
           : (m + ':' + String(q).padStart(2,'0'));
}

/* 展开面板：这条链**每一步**跑得怎么样。
   用户："点开可以看到其更新到第几步，已使用多长时间，大约还要多长时间"。
   🔴 数据全部来自 `/api/progress`（`done` 是这次真跑出来的，`plan` 是
     **上一次**完整跑的剖面）—— 所以未跑那几步要标【上次】：
     链加过步骤的话那份名字就是过期的，而它不报错（同「不猜一个数」）。 */
function prgSteps(j){
  const done = j.done || [], plan = j.plan || [], total = j.total || 0;
  const rows = [];
  for(let k = 0; k < total; k++){
    const d = done[k], cur = (j.state === 'running' && j.i === k + 1 && !d);
    const nm = (d && d.name) || (plan[k] && plan[k].name) || null;
    let ico = '·', cls = 'pgdq', right = '';
    if(d){
      ico = d.state === 'ok' ? '✅' : '🔴';
      cls = d.state === 'ok' ? 'pgdok' : 'pgdbad';
      right = fmtDur(d.sec) || '';
    }else if(cur){
      ico = '⟳'; cls = 'pgdcur';
      right = '已用 ' + (fmtDur(j.step_elapsed) || '0:00');
      if(j.step_eta > 0) right += ' · 上次 ' + fmtDur(j.step_eta);
    }else if(plan[k]){
      right = '上次 ' + (fmtDur(plan[k].sec) || '—');
    }
    /* 没跑到、也没有上次剖面 -> 名字是【未知】，照实说，不编一个。 */
    const label = cur ? (j.step || nm || '（这一步）')
                      : (nm || (d ? '（未记名）' : '（还不知道是哪一步）'));
    rows.push('<div class="pgdr ' + cls + '">' +
      '<span class="pgdi">' + ico + '</span>' +
      '<span class="pgdn">' + (k + 1) + '. ' + esc(label) + '</span>' +
      '<span class="pgdt">' + esc(right) + '</span></div>');
  }
  const foot = [];
  if(!plan.length && j.state === 'running')
    foot.push('还没有耗时基准（第一次跑完就有了），所以没写"还要多久"');
  else if(plan.length) foot.push('灰的那几步是【上次】跑的名字与耗时');
  if(j.log) foot.push('日志 ' + esc(j.log));
  return '<div class="pgd">' + rows.join('') +
    (foot.length ? '<div class="pgdf">' + foot.join(' · ') + '</div>' : '') +
    '</div>';
}

function prgHtml(j){
  /* 🔴 本地还没有数据 —— 横条这时就是那个【入口】。
     用户："首次加载，应该是系统启动后展示一个数据初始化的按钮，
            然后点击按钮开始加载数据。"
     ★ 按钮名写「开始建本地数据」而不是"数据初始化"：名字要说得出它做什么
       （同「一个链接的名字必须说得出它去哪」）。 */
  if(j.kind === 'setup_needed'){
    const bits = ['还差 ' + j.n_todo + ' 步'];
    if(j.eta_text) bits.push('约需 ' + esc(j.eta_text) + '（估）');
    bits.push('之后每天自动增量同步，不用再点');
    /* 只读模式下**按钮照样在**（项目纪律：一律不设 disabled，disabled 的
       元素连 title 都不触发），但先把原因说在前面 —— 让人点一下才知道
       "起不来"是把解释藏在了一次点击后面。 */
    if(!j.can_run && j.why) bits.push(esc(j.why));
    return '<span class="pgi">📦</span>' +
      '<b>' + esc(j.title) + '</b>' +
      '<span class="pgs">' + bits.join(' · ') + '</span>' +
      '<a href="#" class="btn" id="prgsetup">▷ 开始建本地数据</a>' +
      '<a href="/#/sync">看清单 ›</a>';
  }
  /* 数据齐了，但定时任务没装 —— 新机器上最容易漏掉的一步。
     用户问「新下载的项目会在什么时候启用定时任务」，查下来是
     【三个入口全要人主动做】，而一键建库跑完不装：于是七个阶段全绿、
     横条自己消失、首页一切正常，而明天起数据再也不更新。
     不替人装（往 launchd / schtasks 里写条目是系统级副作用），
     但必须说出来；装上之后这一行自己消失。 */
  if(j.kind === 'autosync_needed'){
    const bits = [];
    /* 窗口口径由服务端给 —— 前端写死的话，改了窗口它不会跟着变
       （同「清单在服务端」）。 */
    (j.tasks || []).forEach(t => {
      if(t.window_text) bits.push(esc(t.name || '') + ' ' + esc(t.window_text));
    });
    if(!bits.length && j.window) bits.push(esc(j.window));
    /* 🔴 代价要说出来，而且说的是【可证的事实】：PIT 快照是覆盖写，
       漏一天补不回来（同「关自动同步」那句 confirm）。 */
    bits.push(j.partial ? '现在只开了一半' : '现在没开 —— 数据不会自己更新');
    bits.push('PIT 快照漏一天补不回来');
    if(!j.can_run && j.why) bits.push(esc(j.why));
    return '<div class="pgrow" data-job="' + esc(j.job) + '">' +
      '<span class="pgi">⟳</span>' +
      '<b>' + esc(j.title) + '</b>' +
      '<span class="pgs">' + bits.join(' · ') + '</span>' +
      '<a href="#" class="btn" id="prgauto">▷ 开启自动同步</a>' +
      '<a href="/#/sync">看定时窗口 ›</a></div>';
  }
  const run = j.state === 'running', bad = j.state === 'stale' || j.rc;
  const ico = run ? '⟳' : (bad ? '🔴' : '✅');
  /* 进度条的刻度是【已完成的步数】，当前这一步按它自己的实测时长插值。
     🔴 没有历史耗时就**不插值**（条停在整步边界）—— 编一个看着在走的
     假进度，比条不动更糟：人会拿它去估"还要多久"。 */
  let pct = j.total ? (Math.max(0, j.i - 1) / j.total) : 0;
  if(run && j.step_eta > 0 && j.step_elapsed != null)
    pct += Math.min(j.step_elapsed / j.step_eta, 0.95) / j.total;
  pct = Math.max(0, Math.min(1, pct));

  const el = fmtDur(j.elapsed), eta = fmtDur(j.eta);
  const bits = [];
  if(run && j.total) bits.push('第 ' + j.i + ' / ' + j.total + ' 步');
  if(j.step) bits.push(esc(j.step));
  if(el) bits.push('已用 ' + el);
  /* 预计剩余：**实测**的直接给；没实测过就退回阶段自己声明的文字估计
     并标「估」；两样都没有就明说「未知」——【不猜一个数】。 */
  if(run){
    if(eta) bits.push('约剩 ' + eta);
    else if(j.eta_text) bits.push('约需 ' + esc(j.eta_text) + '（估）');
    else bits.push('剩余未知（第一次跑，跑完就有基准了）');
  }
  if(j.state === 'stale'){
    /* 说清是**什么时候**断的 —— 只说"中断过"的话，人分不出是刚才还是
       昨晚（而两者要做的事不一样）。 */
    const t = j.broke_at ? new Date(j.broke_at * 1000) : null;
    bits.push('中断了' + (t ? '（停在 ' + String(t.getMonth()+1).padStart(2,'0')
      + '-' + String(t.getDate()).padStart(2,'0') + ' '
      + String(t.getHours()).padStart(2,'0') + ':'
      + String(t.getMinutes()).padStart(2,'0') + '）' : '') + ' —— 去数据页重跑');
  }
  if(!run && !bad) bits.push('完成');

  /* 🔴 展开区是**浮层**（绝对定位贴在横条下方），不撑高横条自己 ——
     `body.hasprog{padding-top}` 是个固定值，横条一变高就会盖住顶栏、
     把导航**点不到**（同「position:fixed 会盖住页面最下面那行」）。 */
  const open = !!PRGOPEN[j.job];
  return '<div class="pgrow" data-job="' + esc(j.job) + '">' +
         '<i class="pgfill' + (bad ? ' bad' : '') + '" style="width:' +
         (pct * 100).toFixed(1) + '%"></i>' +
         '<span class="pgi">' + ico + '</span>' +
         '<b>' + esc(j.title || j.job) + '</b>' +
         '<span class="pgs">' + bits.join(' · ') + '</span>' +
         '<a href="#" class="pgmore" data-job="' + esc(j.job) + '">' +
         (open ? '收起 ▴' : '明细 ▾') + '</a>' +
         '<a href="/#/sync">看日志 ›</a>' +
         (open ? prgSteps(j) : '') + '</div>';
}

async function prgScan(){
  let o;
  try{ o = await j('/api/progress'); }catch(e){ return false; }
  const js = (o && o.jobs) || [];
  let bar = $('#prgbar');
  if(!js.length){
    if(bar){ bar.remove(); document.body.classList.remove('hasprog'); }
    return false;
  }
  if(!bar){
    bar = document.createElement('div');
    bar.id = 'prgbar';
    document.body.insertBefore(bar, document.body.firstChild);
    document.body.classList.add('hasprog');
    /* 🔴 事件**委托**在这个稳定容器上 —— 里面的内容每轮都被 innerHTML
       整块换掉，逐个绑的话换完就没有 handler 了（同「innerHTML 填充之后
       才存在的元素要重新绑事件」）。 */
    bar.onclick = async (ev) => {
      /* 明细开关：同样**委托**在这个稳定容器上（内容每轮重建）。 */
      const more = ev.target.closest('.pgmore');
      if(more){
        ev.preventDefault();
        const k = more.dataset.job;
        PRGOPEN[k] = !PRGOPEN[k];
        prgScan();                 // 立刻重渲染，别等下一轮
        return;
      }
      /* 开启自动同步：与「开始建本地数据」同一条委托链。 */
      const a = ev.target.closest('#prgauto');
      if(a){
        ev.preventDefault();
        a.textContent = '正在开启…';
        let r;
        try{ r = await post('/api/sync/auto', {on: true}); }
        catch(e){ r = {error: String(e)}; }
        if(r && r.error){
          a.textContent = '▷ 开启自动同步';
          alert('开不了：' + r.error);
          return;
        }
        prgScan();     // 装上了这一行就该自己消失，别等下一轮
        return;
      }
      const b = ev.target.closest('#prgsetup');
      if(!b) return;
      ev.preventDefault();
      b.textContent = '正在启动…';
      let r;
      try{ r = await post('/api/setup/run', {stage: '__all__'}); }
      catch(e){ r = {error: String(e)}; }
      if(r && r.error){
        /* 按钮**一律不设 disabled**（项目纪律）：点得动，点了把原因说清楚。 */
        b.textContent = '▷ 开始建本地数据';
        alert('起不来：' + r.error);
        return;
      }
      prgScan();      // 立刻换成"正在跑"那一行，别让人以为没反应
    };
  }
  bar.innerHTML = js.map(prgHtml).join('');
  return js.some(x => x.state === 'running');
}

if(typeof window !== 'undefined' && !window.__prgOn){
  window.__prgOn = true;
  const ptick = async () => {
    const running = await prgScan();
    /* 跑着的时候 2 秒一轮（进度要跟得上），闲着 10 秒一轮 ——
       每个标签页都在轮，闲时也 2 秒的话纯属白打接口。 */
    setTimeout(ptick, running ? PRG_RUN : PRG_IDLE);
  };
  setTimeout(ptick, 300);
}
