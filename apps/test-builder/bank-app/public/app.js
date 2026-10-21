// Northwind Bank SPA: history-API routing, skeletons, i18n, feature flags, session-timeout modal.
/* eslint-disable */
const S = { me: null, csrf: null, locale: 'en', poll: null, warned: false };
const WIDGET_ORIGIN = document.querySelector('meta[name=widget-origin]').content;
const t = (k, ...a) => { const v = (I18N[S.locale] || I18N.en)[k] ?? I18N.en[k] ?? k; return typeof v === 'function' ? v(...a) : v; };
const $ = (sel, el = document) => el.querySelector(sel);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const fmtMoney = (v) => { const n = Number(v); return (S.locale === 'de' ? n.toLocaleString('de-DE', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : n.toLocaleString('en-GB', { minimumFractionDigits: 2, maximumFractionDigits: 2 })); };
const uuid = () => (crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random().toString(16).slice(2));
const isoToUk = (d) => d ? `${d.slice(8, 10)}/${d.slice(5, 7)}/${d.slice(0, 4)}` : '';
const ukToIso = (d) => { const m = /^(\d{2})\/(\d{2})\/(\d{4})$/.exec(d || ''); return m ? `${m[3]}-${m[2]}-${m[1]}` : null; };

class ApiError extends Error { constructor(status, body) { super(body?.error?.message || `HTTP ${status}`); this.status = status; this.body = body; } }

async function api(path, { method = 'GET', body, headers = {}, raw = false, background = false } = {}) {
  const h = { ...headers };
  if (body !== undefined && !(body instanceof FormData) && typeof body !== 'string') h['content-type'] = 'application/json';
  if (S.csrf && method !== 'GET') h['x-csrf-token'] = S.csrf;
  if (background) h['x-background'] = '1';
  const res = await fetch(path, { method, headers: h, body: body === undefined ? undefined : (body instanceof FormData || typeof body === 'string') ? body : JSON.stringify(body) });
  if (raw) return res;
  const text = await res.text();
  const data = text ? JSON.parse(text) : null;
  if (res.status === 401 && data?.error?.code === 'SESSION_EXPIRED') { expire(); throw new ApiError(401, data); }
  if (!res.ok) throw new ApiError(res.status, data);
  return data;
}

// ---------------------------------------------------------------- router
const routes = [
  [/^\/(login)?$/, renderLogin, false], [/^\/mfa$/, renderMfa, false],
  [/^\/dashboard$/, renderDashboard, true], [/^\/accounts\/([\w-]+)$/, renderAccount, true],
  [/^\/payments\/new$/, renderNewPayment, true], [/^\/payments$/, renderPayments, true],
  [/^\/approvals$/, renderApprovals, true], [/^\/bulk$/, renderBulk, true], [/^\/cards$/, renderCards, true],
  [/^\/settings$/, renderSettings, true], [/^\/labs$/, renderLabs, true],
];

function navigate(path, replace = false) {
  if (replace) history.replaceState({}, '', path); else history.pushState({}, '', path);
  render();
}
window.addEventListener('popstate', () => render());
document.addEventListener('click', (e) => {
  const a = e.target.closest('a[href]');
  if (!a || a.hasAttribute('download') || a.dataset.native !== undefined || a.target === '_blank') return;
  const href = a.getAttribute('href');
  if (!href.startsWith('/') || href.startsWith('/api/') || href.startsWith('/legacy/') || href.startsWith('/go')) return;
  e.preventDefault();
  navigate(href);
});

async function render() {
  const path = location.pathname;
  const hit = routes.find(([re]) => re.test(path));
  if (!hit) { shell(`<h1>${t('notFound')}</h1>`); return; }
  const [re, fn, needsAuth] = hit;
  if (needsAuth && !S.me) {
    try { await loadMe(); } catch { navigate('/login', true); return; }
  }
  fn(...(re.exec(path).slice(1)));
}

async function loadMe() {
  const me = await api('/api/v1/me');
  S.me = me; S.csrf = me.csrf; S.locale = me.locale;
  document.documentElement.lang = S.locale;
  startSessionWatch();
}

// ---------------------------------------------------------------- shell
function shell(content, { title } = {}) {
  const me = S.me;
  const f = me?.flags || {};
  const links = me ? [
    ['/dashboard', t('dashboard')], ['/payments', t('payments')],
    ...(me.role !== 'auditor' ? [['/payments/new', t('newPayment')], ['/bulk', t('bulk')], ['/cards', t('cards')]] : []),
    ...(me.role === 'supervisor' ? [['/approvals', t('approvals')]] : []),
    ['/settings', t('settings')], ...(f.labs ? [['/labs', t('labs')]] : []),
  ] : [];
  $('#app').innerHTML = `
    <header class="top" role="banner">
      <a class="brand" href="/dashboard">${t('appName')}</a>
      ${me ? `<nav aria-label="${t('nav')}"><ul>${links.map(([h, l]) => `<li><a href="${h}" ${location.pathname === h ? 'aria-current="page"' : ''}>${esc(l)}</a></li>`).join('')}</ul></nav>
      <div class="who">
        <label for="lang">${t('language')}</label>
        <select id="lang"><option value="en" ${S.locale === 'en' ? 'selected' : ''}>English</option><option value="de" ${S.locale === 'de' ? 'selected' : ''}>Deutsch</option></select>
        <span>${t('signedInAs')} <strong>${esc(me.displayName)}</strong> (${esc(me.role)})</span>
        <button type="button" id="signout">${t('signOut')}</button>
      </div>` : ''}
    </header>
    <main id="main" class="${f.gridDensity === 'compact' ? 'compact' : ''}">${content}</main>`;
  if (title) document.title = `${title} - Northwind Bank`;
  $('#signout')?.addEventListener('click', async () => { await api('/auth/logout', { method: 'POST' }).catch(() => {}); S.me = null; S.csrf = null; stopSessionWatch(); navigate('/login'); });
  $('#lang')?.addEventListener('change', async (e) => { await api('/api/v1/me', { method: 'PATCH', body: { locale: e.target.value } }); S.locale = e.target.value; S.me.locale = S.locale; document.documentElement.lang = S.locale; render(); });
}

const skeleton = (rows = 5) => `<div class="skeleton" aria-busy="true" aria-label="${t('loading')}">${'<div class="sk-row"></div>'.repeat(rows)}</div>`;

function toast(msg) {
  // Legacy toast: no role, no live region, disappears after 2.5 s.
  const el = document.createElement('div');
  el.className = 'toast';
  el.textContent = msg;
  $('#toasts').appendChild(el);
  setTimeout(() => el.remove(), 2500);
}

function modal({ title, body, confirm, danger = false }) {
  return new Promise((resolve) => {
    $('#modal-root').innerHTML = `<div class="backdrop"><div role="dialog" aria-modal="true" aria-labelledby="mt" class="modal">
      <h2 id="mt">${esc(title)}</h2><div>${body}</div>
      <div class="actions"><button type="button" data-x="cancel">${t('cancel')}</button><button type="button" data-x="ok" class="${danger ? 'danger' : 'primary'}">${esc(confirm)}</button></div></div></div>`;
    const close = (v) => { $('#modal-root').innerHTML = ''; resolve(v); };
    $('#modal-root [data-x=cancel]').addEventListener('click', () => close(false));
    $('#modal-root [data-x=ok]').addEventListener('click', () => close(true));
    $('#modal-root [data-x=ok]').focus();
  });
}

// ---------------------------------------------------------------- session timeout
function startSessionWatch() {
  stopSessionWatch();
  S.poll = setInterval(async () => {
    const r = await fetch('/auth/session', { headers: { 'x-background': '1' } }).then((x) => x.json()).catch(() => null);
    if (!r) return;
    if (!r.active) { expire(); return; }
    const dlg = $('#session-dialog');
    if (r.remainingSeconds <= r.warnSeconds) {
      if (!dlg) {
        const d = document.createElement('div');
        d.id = 'session-dialog';
        d.innerHTML = `<div class="backdrop"><div role="alertdialog" aria-modal="true" aria-labelledby="st" aria-describedby="sd" class="modal">
          <h2 id="st">${t('expiringTitle')}</h2><p id="sd"></p>
          <div class="actions"><button type="button" id="stay" class="primary">${t('stay')}</button><button type="button" id="leave">${t('signOut')}</button></div></div></div>`;
        document.body.appendChild(d);
        $('#stay').addEventListener('click', async () => { await api('/auth/keepalive', { method: 'POST' }).catch(() => {}); d.remove(); });
        $('#leave').addEventListener('click', () => $('#signout')?.click() ?? d.remove());
      }
      $('#sd').textContent = t('expiringIn', r.remainingSeconds);
    } else if (dlg) dlg.remove();
  }, 2000);
}
function stopSessionWatch() { if (S.poll) clearInterval(S.poll); S.poll = null; $('#session-dialog')?.remove(); }
function expire() { stopSessionWatch(); S.me = null; S.csrf = null; navigate('/login?expired=1', true); }

// ---------------------------------------------------------------- login + MFA
function renderLogin() {
  const expired = new URLSearchParams(location.search).get('expired');
  shell(`<section class="card narrow">
    <h1>${t('signInTitle')}</h1>
    ${expired ? `<p role="alert" class="warn">${t('sessionExpired')}</p>` : ''}
    <form id="login" novalidate>
      <label for="u">${t('username')}</label><input id="u" name="username" autocomplete="username">
      <label for="p">${t('password')}</label><input id="p" name="password" type="password" autocomplete="current-password">
      <button type="submit" class="primary">${t('signIn')}</button>
      <p id="login-error" role="alert" class="error" hidden></p>
    </form></section>`, { title: t('signIn') });
  $('#login').addEventListener('submit', async (e) => {
    e.preventDefault();
    const err = $('#login-error');
    err.hidden = true;
    const btn = e.target.querySelector('button');
    btn.disabled = true;
    try {
      const pre = document.querySelector('meta[name=csrf-token]').content;
      const r = await api('/auth/login', { method: 'POST', body: { username: $('#u').value, password: $('#p').value }, headers: { 'x-csrf-token': pre } });
      S.csrf = r.csrf;
      setTimeout(() => navigate('/mfa'), 150);
    } catch (ex) { err.textContent = ex.message; err.hidden = false; btn.disabled = false; }
  });
}

function renderMfa() {
  if (!S.csrf) { navigate('/login', true); return; }
  shell(`<section class="card narrow">
    <h1>${t('verifyTitle')}</h1><p>${t('verifyHelp')}</p>
    <form id="mfa" novalidate><div class="otp">${[1, 2, 3, 4, 5, 6].map((i) => `<input aria-label="${t('digit')} ${i}" inputmode="numeric" maxlength="1" autocomplete="one-time-code">`).join('')}</div>
    <button type="submit" class="primary">${t('verify')}</button><p id="mfa-error" role="alert" class="error" hidden></p></form></section>`, { title: t('verify') });
  const boxes = [...document.querySelectorAll('.otp input')];
  boxes.forEach((b, i) => {
    b.addEventListener('input', () => {
      const v = b.value.replace(/\D/g, '');
      if (v.length > 1) { v.split('').slice(0, 6 - i).forEach((d, j) => { boxes[i + j].value = d; }); boxes[Math.min(5, i + v.length - 1)].focus(); return; }
      b.value = v;
      if (v && i < 5) boxes[i + 1].focus();
    });
    b.addEventListener('paste', (e) => { e.preventDefault(); const v = (e.clipboardData.getData('text') || '').replace(/\D/g, ''); v.split('').slice(0, 6).forEach((d, j) => { if (boxes[j]) boxes[j].value = d; }); });
    b.addEventListener('keydown', (e) => { if (e.key === 'Backspace' && !b.value && i > 0) boxes[i - 1].focus(); });
  });
  boxes[0].focus();
  $('#mfa').addEventListener('submit', async (e) => {
    e.preventDefault();
    const err = $('#mfa-error');
    err.hidden = true;
    try {
      const r = await api('/auth/mfa', { method: 'POST', body: { code: boxes.map((b) => b.value).join('') } });
      S.csrf = r.csrf;
      await loadMe();
      // Client-side redirect AFTER an async load: the classic "stale snapshot" trap.
      setTimeout(() => navigate('/dashboard'), 200);
    } catch (ex) { err.textContent = ex.message; err.hidden = false; boxes.forEach((b) => { b.value = ''; }); boxes[0].focus(); }
  });
}

// ---------------------------------------------------------------- dashboard
const QUICK = [['/payments/new', 'newPayment'], ['/bulk', 'bulk'], ['/approvals', 'approvals'], ['/cards', 'cards']];
function quickOrder() { try { return JSON.parse(localStorage.getItem('nw-quick') || 'null') || QUICK.map((q) => q[1]); } catch { return QUICK.map((q) => q[1]); } }

async function renderDashboard() {
  shell(`<h1>${t('dashboard')}</h1>
    <section aria-labelledby="acc-h"><h2 id="acc-h">${t('accounts')}</h2><div id="acc">${skeleton(4)}</div></section>
    <div class="cols">
      <section aria-labelledby="ql-h"><h2 id="ql-h">${t('quickLinks')}</h2><ul id="quick" class="quick"></ul><p id="quick-status" class="muted"></p></section>
      <section><h2>${t('spending')}</h2><canvas id="chart" width="420" height="200"></canvas></section>
    </div>
    <section aria-labelledby="rp-h"><h2 id="rp-h">${t('recent')}</h2><div id="recent">${skeleton(3)}</div></section>`, { title: t('dashboard') });
  // Quick links with HTML5 drag-and-drop reordering.
  const ql = $('#quick');
  const drawQuick = () => {
    ql.innerHTML = quickOrder().map((k) => `<li draggable="true" data-k="${k}"><span class="grip" aria-hidden="true">⠿</span> <a href="${QUICK.find((q) => q[1] === k)[0]}">${t(k)}</a></li>`).join('');
  };
  drawQuick();
  let dragK = null;
  ql.addEventListener('dragstart', (e) => { dragK = e.target.closest('li')?.dataset.k; e.dataTransfer.effectAllowed = 'move'; e.dataTransfer.setData('text/plain', dragK || ''); });
  ql.addEventListener('dragover', (e) => e.preventDefault());
  ql.addEventListener('drop', (e) => {
    e.preventDefault();
    const over = e.target.closest('li')?.dataset.k;
    if (!dragK || !over || dragK === over) return;
    const o = quickOrder().filter((k) => k !== dragK);
    o.splice(o.indexOf(over), 0, dragK);
    localStorage.setItem('nw-quick', JSON.stringify(o));
    drawQuick();
    $('#quick-status').textContent = `Quick links order saved: ${o.map((k) => t(k)).join(', ')}`;
  });

  const [accounts, pays] = await Promise.all([api('/api/v1/accounts'), api('/api/v1/payments?limit=5')]);
  const sup = S.me.role === 'supervisor';
  $('#acc').innerHTML = `<table class="grid accounts"><caption>${t('accounts')}</caption>
    <thead><tr><th>${t('account')}</th><th>${t('holder')}</th><th>${t('iban')}</th><th class="num">${t('balance')}</th><th>${t('status')}</th><th></th></tr></thead>
    <tbody>${accounts.map((a) => `<tr data-id="${a.id}">
      <td><a href="/accounts/${a.id}">${esc(a.nickname || a.name)}</a></td><td>${esc(a.holder)}</td><td class="mono">${esc(a.iban.replace(/(.{4})/g, '$1 ').trim())}</td>
      <td class="num">${fmtMoney(a.balance)}</td><td>${esc(a.status)}</td>
      <td class="row-actions"><button type="button" class="kebab" aria-haspopup="menu">⋯</button>
        <div class="menu" role="menu" hidden>
          <a role="menuitem" href="/accounts/${a.id}">View transactions</a>
          <a role="menuitem" href="/api/v1/accounts/${a.id}/statement?format=csv" download>${t('csvStatement')}</a>
          <button role="menuitem" type="button" data-act="rename">Rename</button>
          ${sup ? `<button role="menuitem" type="button" data-act="close">Close account</button>` : ''}
        </div></td></tr>`).join('')}</tbody></table>`;
  document.querySelectorAll('.accounts .kebab').forEach((k) => k.addEventListener('click', (e) => {
    const m = e.target.nextElementSibling; m.hidden = !m.hidden;
  }));
  document.querySelectorAll('.accounts [data-act]').forEach((b) => b.addEventListener('click', async (e) => {
    const id = e.target.closest('tr').dataset.id;
    if (e.target.dataset.act === 'rename') {
      const name = prompt('New nickname');
      if (name) { await api(`/api/v1/accounts/${id}`, { method: 'PATCH', body: { nickname: name } }); toast('Account renamed'); renderDashboard(); }
    } else if (e.target.dataset.act === 'close') {
      if (!(await modal({ title: 'Close account', body: `<p>Close account ${esc(id)}? This cannot be undone.</p>`, confirm: 'Close account', danger: true }))) return;
      try { await api(`/api/v1/accounts/${id}/close`, { method: 'POST' }); toast(`Account ${id} closed`); renderDashboard(); }
      catch (ex) { alertBox('#acc', ex.message); }
    }
  }));
  $('#recent').innerHTML = pays.items.length ? `<ul class="recent">${pays.items.map((p) => `<li>${esc(p.id)} · ${esc(p.payeeName)} · ${fmtMoney(p.amount)} ${p.currency} · ${esc(p.status)}</li>`).join('')}</ul>` : '<p>-</p>';
  drawChart();
}

function alertBox(sel, msg) {
  const el = document.createElement('p'); el.setAttribute('role', 'alert'); el.className = 'error'; el.textContent = msg;
  $(sel).prepend(el);
}

function drawChart() {
  // Canvas chart with NO text alternative (real legacy dashboards do this).
  const c = $('#chart'); if (!c) return;
  const ctx = c.getContext('2d');
  const data = [['Rent', 145], ['Groceries', 62], ['Utilities', 41], ['Travel', 23], ['Dining', 18]];
  ctx.clearRect(0, 0, c.width, c.height);
  data.forEach(([k, v], i) => { ctx.fillStyle = ['#1d4ed8', '#0891b2', '#16a34a', '#ca8a04', '#dc2626'][i]; ctx.fillRect(110, 15 + i * 36, v * 2, 24); ctx.fillStyle = '#111'; ctx.font = '12px sans-serif'; ctx.fillText(k, 10, 32 + i * 36); });
}

// ---------------------------------------------------------------- account + transactions grid
async function renderAccount(id) {
  const st = { page: 1, pageSize: 25, sort: 'date', order: 'desc', q: '', category: '', from: '', to: '' };
  shell(`<div id="acct-head">${skeleton(1)}</div>
    <section aria-labelledby="tx-h"><h2 id="tx-h">${t('transactions')}</h2>
    <div class="toolbar">
      <span>Filter</span><input id="q" type="search">
      <label for="cat">${t('category')}</label><select id="cat"><option value="">${t('allCategories')}</option>${['Groceries', 'Utilities', 'Dining', 'Travel', 'Salary', 'Rent'].map((c) => `<option>${c}</option>`).join('')}</select>
      <button type="button" id="range" aria-haspopup="dialog">${t('dateRange')}: <span id="range-label">${t('allDates')}</span></button>
      <label for="ps">${t('rowsPerPage')}</label><select id="ps"><option>10</option><option selected>25</option><option>50</option></select>
      <div class="export" onclick="exportCsv()">${t('exportCsv')}</div>
      <a id="pdf" href="#" download>${t('pdfStatement')}</a>
    </div>
    <div id="grid">${skeleton(8)}</div>
    <div class="pager"><button type="button" id="prev">${t('previous')}</button><span id="pageinfo"></span><button type="button" id="next">${t('next')}</button></div>
    <p id="count" role="status"></p></section>`, { title: id });
  api(`/api/v1/accounts/${id}`).then((a) => { $('#acct-head').innerHTML = `<h1>${esc(a.nickname || a.name)}</h1><p class="mono">${esc(a.iban)} · ${esc(a.holder)} · ${t('balance')}: ${fmtMoney(a.balance)} GBP</p>`; });
  const pdfLink = () => { $('#pdf').href = `/api/v1/accounts/${id}/statement?format=pdf${st.from ? `&from=${st.from}` : ''}${st.to ? `&to=${st.to}` : ''}`; };
  window.exportCsv = () => { const a = document.createElement('a'); a.href = `/api/v1/accounts/${id}/statement?format=csv${st.from ? `&from=${st.from}` : ''}${st.to ? `&to=${st.to}` : ''}`; a.download = ''; document.body.appendChild(a); a.click(); a.remove(); };
  pdfLink();
  let seq = 0;
  const load = async () => {
    const my = ++seq;
    $('#grid').innerHTML = skeleton(8);
    const qs = new URLSearchParams({ page: st.page, pageSize: st.pageSize, sort: st.sort, order: st.order, ...(st.q && { q: st.q }), ...(st.category && { category: st.category }), ...(st.from && { from: st.from }), ...(st.to && { to: st.to }) });
    const r = await api(`/api/v1/accounts/${id}/transactions?${qs}`);
    if (my !== seq) return; // a newer request superseded this one
    const th = (key, label) => `<th aria-sort="${st.sort === key ? (st.order === 'asc' ? 'ascending' : 'descending') : 'none'}"><button type="button" data-sort="${key}">${label}</button></th>`;
    $('#grid').innerHTML = r.items.length ? `<table class="grid tx"><caption>${t('transactions')}</caption><thead><tr>${th('date', t('date'))}${th('description', t('description'))}${th('category', t('category'))}${th('amount', t('amount'))}<th class="num">${t('balance')}</th></tr></thead>
      <tbody>${r.items.map((x) => `<tr><td>${isoToUk(x.date)}</td><td>${esc(x.description)}</td><td>${esc(x.category)}</td><td class="num ${Number(x.amount) < 0 ? 'neg' : ''}">${fmtMoney(x.amount)}</td><td class="num">${fmtMoney(x.balance)}</td></tr>`).join('')}</tbody></table>`
      : `<p>${t('noTx')}</p>`;
    const a = r.total ? (r.page - 1) * r.pageSize + 1 : 0;
    $('#count').textContent = t('showing', a, Math.min(r.page * r.pageSize, r.total), r.total);
    $('#pageinfo').textContent = t('pageOf', r.page, r.totalPages);
    $('#prev').disabled = r.page <= 1; $('#next').disabled = r.page >= r.totalPages;
    document.querySelectorAll('[data-sort]').forEach((b) => b.addEventListener('click', () => {
      const k = b.dataset.sort; st.order = st.sort === k && st.order === 'desc' ? 'asc' : 'desc'; st.sort = k; st.page = 1; load();
    }));
  };
  let deb;
  $('#q').addEventListener('input', (e) => { clearTimeout(deb); deb = setTimeout(() => { st.q = e.target.value; st.page = 1; load(); }, 400); });
  $('#cat').addEventListener('change', (e) => { st.category = e.target.value; st.page = 1; load(); });
  $('#ps').addEventListener('change', (e) => { st.pageSize = Number(e.target.value); st.page = 1; load(); });
  $('#prev').addEventListener('click', () => { st.page--; load(); });
  $('#next').addEventListener('click', () => { st.page++; load(); });
  $('#range').addEventListener('click', async () => {
    const r = await dateRangeDialog(st.from, st.to);
    if (!r) return;
    st.from = r.from; st.to = r.to; st.page = 1;
    $('#range-label').textContent = r.from || r.to ? `${isoToUk(r.from) || '…'} – ${isoToUk(r.to) || '…'}` : t('allDates');
    pdfLink(); load();
  });
  load();
}

function maskDate(input) {
  input.addEventListener('input', () => {
    const d = input.value.replace(/\D/g, '').slice(0, 8);
    input.value = d.length > 4 ? `${d.slice(0, 2)}/${d.slice(2, 4)}/${d.slice(4)}` : d.length > 2 ? `${d.slice(0, 2)}/${d.slice(2)}` : d;
  });
}

function dateRangeDialog(from, to) {
  return new Promise((resolve) => {
    $('#modal-root').innerHTML = `<div class="backdrop"><div role="dialog" aria-modal="true" aria-labelledby="dr" class="modal">
      <h2 id="dr">${t('chooseDates')}</h2>
      <label for="df">${t('from')}</label><input id="df" value="${isoToUk(from)}" placeholder="DD/MM/YYYY">
      <label for="dt">${t('to')}</label><input id="dt" value="${isoToUk(to)}" placeholder="DD/MM/YYYY">
      <div class="presets"><button type="button" data-p="q1">Q1 2026</button><button type="button" data-p="m3">March 2026</button></div>
      <p id="dr-err" role="alert" class="error" hidden></p>
      <div class="actions"><button type="button" data-x="clear">${t('clear')}</button><button type="button" data-x="cancel">${t('cancel')}</button><button type="button" data-x="ok" class="primary">${t('apply')}</button></div></div></div>`;
    maskDate($('#df')); maskDate($('#dt'));
    const close = (v) => { $('#modal-root').innerHTML = ''; resolve(v); };
    document.querySelectorAll('[data-p]').forEach((b) => b.addEventListener('click', () => {
      if (b.dataset.p === 'q1') { $('#df').value = '01/01/2026'; $('#dt').value = '31/03/2026'; } else { $('#df').value = '01/03/2026'; $('#dt').value = '31/03/2026'; }
    }));
    $('[data-x=cancel]').addEventListener('click', () => close(null));
    $('[data-x=clear]').addEventListener('click', () => close({ from: '', to: '' }));
    $('[data-x=ok]').addEventListener('click', () => {
      const f = $('#df').value ? ukToIso($('#df').value) : '';
      const tt = $('#dt').value ? ukToIso($('#dt').value) : '';
      if (f === null || tt === null) { $('#dr-err').textContent = 'Dates must be DD/MM/YYYY'; $('#dr-err').hidden = false; return; }
      if (f && tt && f > tt) { $('#dr-err').textContent = 'The start date must be before the end date'; $('#dr-err').hidden = false; return; }
      close({ from: f, to: tt });
    });
  });
}

// ---------------------------------------------------------------- payments
function maskAmount(input) {
  input.addEventListener('input', () => {
    let v = input.value.replace(/[^\d.]/g, '');
    const [i, ...d] = v.split('.');
    const int = (i || '').replace(/^0+(?=\d)/, '');
    input.value = (int ? Number(int).toLocaleString('en-GB') : (d.length ? '0' : '')) + (d.length ? '.' + d.join('').slice(0, 2) : '');
  });
  input.addEventListener('blur', () => { const n = Number(input.value.replace(/,/g, '')); if (input.value && Number.isFinite(n)) input.value = n.toLocaleString('en-GB', { minimumFractionDigits: 2, maximumFractionDigits: 2 }); });
}

function payeeCombobox(container, onPick) {
  container.innerHTML = `<label for="payee" id="payee-l">${t('payee')}</label>
    <div class="combo"><input id="payee" role="combobox" aria-autocomplete="list" aria-expanded="false" aria-controls="payee-list" autocomplete="off">
    <ul id="payee-list" role="listbox" aria-label="Payee suggestions" hidden></ul></div>`;
  const input = $('#payee', container); const list = $('#payee-list', container);
  let deb; let items = []; let active = -1;
  const close = () => { list.hidden = true; input.setAttribute('aria-expanded', 'false'); active = -1; };
  const pick = (p) => { input.value = p.name; input.dataset.id = p.id; close(); onPick(p); };
  input.addEventListener('input', () => {
    input.dataset.id = '';
    clearTimeout(deb);
    const q = input.value.trim();
    if (q.length < 2) { close(); return; }
    deb = setTimeout(async () => {
      items = await api(`/api/v1/payees?q=${encodeURIComponent(q)}`);
      if (input.value.trim() !== q) return;
      list.innerHTML = items.length ? items.map((p, i) => `<li role="option" id="opt-${i}" data-i="${i}">${esc(p.name)} <span class="muted">${esc(p.bank)} ··${esc(p.iban.slice(-4))}</span></li>`).join('') : '<li class="muted">No matches</li>';
      list.hidden = false; input.setAttribute('aria-expanded', 'true');
    }, 300);
  });
  list.addEventListener('mousedown', (e) => { const li = e.target.closest('[role=option]'); if (li) { e.preventDefault(); pick(items[Number(li.dataset.i)]); } });
  input.addEventListener('keydown', (e) => {
    if (list.hidden) return;
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault(); active = Math.max(0, Math.min(items.length - 1, active + (e.key === 'ArrowDown' ? 1 : -1)));
      list.querySelectorAll('[role=option]').forEach((o, i) => o.setAttribute('aria-selected', String(i === active)));
      input.setAttribute('aria-activedescendant', `opt-${active}`);
    } else if (e.key === 'Enter' && active >= 0) { e.preventDefault(); pick(items[active]); }
    else if (e.key === 'Escape') close();
  });
  input.addEventListener('blur', () => setTimeout(close, 150));
  return input;
}

async function renderNewPayment() {
  if (S.me.role === 'auditor') { shell(`<h1>${t('newPayment')}</h1><p role="alert" class="error">${t('noPermissionPay')}</p>`, { title: t('newPayment') }); return; }
  if (S.me.flags.paymentFlow === 'quick') return renderQuickPay();
  const accounts = await api('/api/v1/accounts');
  const st = { step: 1, from: '', payee: null, amount: '', reference: '', date: isoToUk(new Date().toISOString().slice(0, 10)), key: uuid() };
  const steps = [t('whoPaying'), t('howMuch'), t('review')];
  const draw = () => {
    shell(`<h1>${t('newPayment')}</h1><p class="muted">${t('limitNote', fmtMoney(S.me.paymentLimit))}</p>
      <ol class="steps" aria-label="Progress">${steps.map((s, i) => `<li ${i + 1 === st.step ? 'aria-current="step"' : ''}>${esc(s)}</li>`).join('')}</ol>
      <section class="card" aria-labelledby="sh"><h2 id="sh">${t('step', st.step, steps[st.step - 1])}</h2><form id="wiz" novalidate></form></section>`, { title: t('newPayment') });
    const f = $('#wiz');
    if (st.step === 1) {
      f.innerHTML = `<label for="from">${t('payFrom')}</label><select id="from"><option value="">--</option>${accounts.filter((a) => a.status === 'open').map((a) => `<option value="${a.id}" ${st.from === a.id ? 'selected' : ''}>${esc(a.name)} (${esc(a.holder)}) · ${fmtMoney(a.balance)}</option>`).join('')}</select>
        <div id="pc"></div><p id="e1" role="alert" class="error" hidden></p>
        <div class="actions"><button type="submit" class="primary">${t('continue')}</button></div>`;
      const inp = payeeCombobox($('#pc'), (p) => { st.payee = p; });
      if (st.payee) { inp.value = st.payee.name; inp.dataset.id = st.payee.id; }
      f.addEventListener('submit', (e) => {
        e.preventDefault();
        st.from = $('#from').value;
        if (!inp.dataset.id) st.payee = null;
        const errs = [!st.from && 'Choose an account to pay from', !st.payee && 'Choose a payee from the list'].filter(Boolean);
        if (errs.length) { $('#e1').textContent = errs.join('. '); $('#e1').hidden = false; return; }
        st.step = 2; draw();
      });
    } else if (st.step === 2) {
      f.innerHTML = `<label for="amt">${t('amountGbp')}</label><input id="amt" inputmode="decimal" value="${esc(st.amount)}">
        <label for="ref">${t('reference')}</label><input id="ref" maxlength="35" value="${esc(st.reference)}">
        <label for="dt">${t('paymentDate')}</label><input id="dt" value="${esc(st.date)}">
        <p id="e2" role="alert" class="error" hidden></p>
        <div class="actions"><button type="button" id="back">${t('back')}</button><button type="submit" class="primary">${t('continue')}</button></div>`;
      maskAmount($('#amt')); maskDate($('#dt'));
      $('#back').addEventListener('click', () => { st.amount = $('#amt').value; st.reference = $('#ref').value; st.step = 1; draw(); });
      f.addEventListener('submit', (e) => {
        e.preventDefault();
        st.amount = $('#amt').value; st.reference = $('#ref').value.trim(); st.date = $('#dt').value;
        const n = Number(st.amount.replace(/,/g, ''));
        const errs = [!(n > 0) && 'Enter an amount greater than 0', !st.reference && 'Reference is required', !ukToIso(st.date) && 'Payment date must be DD/MM/YYYY'].filter(Boolean);
        if (errs.length) { $('#e2').textContent = errs.join('. '); $('#e2').hidden = false; return; }
        st.step = 3; draw();
      });
    } else {
      const acc = accounts.find((a) => a.id === st.from);
      f.innerHTML = `<dl class="review"><dt>${t('payFrom')}</dt><dd>${esc(acc.name)} (${esc(acc.holder)})</dd><dt>${t('payee')}</dt><dd>${esc(st.payee.name)} · ${esc(st.payee.iban)}</dd>
        <dt>${t('amount')}</dt><dd>${esc(st.amount)} GBP</dd><dt>${t('reference')}</dt><dd>${esc(st.reference)}</dd><dt>${t('paymentDate')}</dt><dd>${esc(st.date)}</dd></dl>
        <label class="check"><input type="checkbox" id="ok"> ${t('checked')}</label>
        <p id="e3" role="alert" class="error" hidden></p>
        <div class="actions"><button type="button" id="back">${t('back')}</button><button type="submit" class="primary">${t('submitPayment')}</button></div>`;
      $('#back').addEventListener('click', () => { st.step = 2; draw(); });
      f.addEventListener('submit', async (e) => {
        e.preventDefault();
        if (!$('#ok').checked) { $('#e3').textContent = 'Confirm that you have checked the payee details'; $('#e3').hidden = false; return; }
        if (!(await modal({ title: t('submitPayment'), body: `<p>Pay ${esc(st.amount)} GBP to ${esc(st.payee.name)}?</p>`, confirm: t('confirmPay') }))) return;
        try {
          const p = await api('/api/v1/payments', { method: 'POST', headers: { 'idempotency-key': st.key }, body: { fromAccountId: st.from, payeeId: st.payee.id, amount: st.amount.replace(/,/g, ''), currency: 'GBP', reference: st.reference, executeOn: ukToIso(st.date) } });
          paymentResult(p);
        } catch (ex) { $('#e3').textContent = ex.body?.error?.details ? ex.body.error.details.map((d) => d.message).join('. ') : ex.message; $('#e3').hidden = false; }
      });
    }
  };
  draw();
}

function paymentResult(p) {
  shell(`<section class="card"><h1>${t('paymentSubmitted')}</h1>
    <p role="status" id="pay-status">Payment ${esc(p.id)} for ${fmtMoney(p.amount)} GBP to ${esc(p.payeeName)} is ${p.status === 'pending_approval' ? 'pending approval' : esc(p.status)}.</p>
    ${p.status === 'pending_approval' ? `<p>${t('sentForApproval', fmtMoney(S.me.paymentLimit))}</p>` : ''}
    <p><a href="/payments">${t('payments')}</a> · <a href="/payments/new">${t('newPayment')}</a></p></section>`, { title: t('paymentSubmitted') });
  toast(`Payment ${p.id} created`);
}

async function renderQuickPay() {
  // Variant B (feature flag paymentFlow=quick): single form; Enter in any field submits.
  const accounts = await api('/api/v1/accounts');
  const key = uuid();
  shell(`<h1>${t('quickPay')}</h1><form id="qp" class="card" novalidate>
    <label for="from">${t('payFrom')}</label><select id="from">${accounts.filter((a) => a.status === 'open').map((a) => `<option value="${a.id}">${esc(a.name)} (${esc(a.holder)})</option>`).join('')}</select>
    <div id="pc"></div>
    <label for="amt">${t('amountGbp')}</label><input id="amt" inputmode="decimal">
    <label for="ref">${t('reference')}</label><input id="ref" maxlength="35">
    <p id="eq" role="alert" class="error" hidden></p>
    <button type="submit" class="primary">${t('payNow')}</button></form>`, { title: t('quickPay') });
  let payee = null;
  const inp = payeeCombobox($('#pc'), (p) => { payee = p; });
  maskAmount($('#amt'));
  $('#qp').addEventListener('submit', async (e) => {
    e.preventDefault();
    if (!inp.dataset.id) payee = null;
    try {
      const p = await api('/api/v1/payments', { method: 'POST', headers: { 'idempotency-key': key }, body: { fromAccountId: $('#from').value, payeeId: payee?.id, amount: $('#amt').value.replace(/,/g, ''), currency: 'GBP', reference: $('#ref').value } });
      paymentResult(p);
    } catch (ex) { $('#eq').textContent = ex.body?.error?.details ? ex.body.error.details.map((d) => d.message).join('. ') : ex.message; $('#eq').hidden = false; }
  });
}

async function renderPayments() {
  const params = new URLSearchParams(location.search);
  const status = params.get('status') || '';
  const tabs = [['', t('allStatuses')], ['pending_approval', t('pending')], ['completed', t('completed')], ['rejected', t('rejected')]];
  shell(`<h1>${t('payments')}</h1>
    ${params.get('repeated') ? `<p role="status">Payment ${esc(params.get('repeated'))} created from a repeat.</p>` : ''}
    <div role="tablist" aria-label="${t('paymentStatus')}">${tabs.map(([v, l]) => `<a role="tab" href="/payments${v ? `?status=${v}` : ''}" aria-selected="${v === status}">${esc(l)}</a>`).join('')}</div>
    <table class="grid"><caption>${t('payments')}</caption><thead><tr><th>ID</th><th>${t('payee')}</th><th class="num">${t('amount')}</th><th>${t('status')}</th><th>${t('createdBy')}</th><th></th></tr></thead><tbody id="pay-rows"></tbody></table>
    <div id="sentinel">${skeleton(2)}</div>`, { title: t('payments') });
  let cursor = null; let busy = false; let done = false;
  const more = async () => {
    if (busy || done) return; busy = true;
    const r = await api(`/api/v1/payments?limit=10${status ? `&status=${status}` : ''}${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ''}`);
    $('#pay-rows').insertAdjacentHTML('beforeend', r.items.map((p) => `<tr><td>${esc(p.id)}</td><td>${esc(p.payeeName)}</td><td class="num">${fmtMoney(p.amount)}</td><td>${esc(p.status)}</td><td>${esc(p.createdBy)}</td>
      <td>${S.me.role !== 'auditor' ? `<a href="/legacy/repeat-payment?id=${esc(p.id)}" data-native>${t('repeat')}</a>` : ''}</td></tr>`).join(''));
    cursor = r.nextCursor; done = !cursor; busy = false;
    if (done) $('#sentinel').innerHTML = `<p class="muted">End of list</p>`;
  };
  // Infinite scroll: rows beyond the first page only load when the sentinel scrolls into view.
  new IntersectionObserver((es) => { if (es.some((e) => e.isIntersecting)) more(); }).observe($('#sentinel'));
}

async function renderApprovals() {
  if (S.me.role !== 'supervisor') { shell(`<h1>${t('approvals')}</h1><p role="alert" class="error">Only supervisors can approve payments.</p>`); return; }
  shell(`<h1>${t('pendingApprovals')}</h1><div id="ap">${skeleton(3)}</div>`, { title: t('approvals') });
  const r = await api('/api/v1/payments?status=pending_approval&limit=50');
  $('#ap').innerHTML = r.items.length ? `<table class="grid"><caption>${t('pendingApprovals')}</caption><thead><tr><th>ID</th><th>${t('payee')}</th><th class="num">${t('amount')}</th><th>${t('reference')}</th><th>${t('createdBy')}</th><th></th></tr></thead>
    <tbody>${r.items.map((p) => `<tr data-id="${p.id}"><td>${esc(p.id)}</td><td>${esc(p.payeeName)}</td><td class="num">${fmtMoney(p.amount)}</td><td>${esc(p.reference)}</td><td>${esc(p.createdBy)}</td>
      <td><button type="button" data-d="approve">${t('approve')}</button> <button type="button" data-d="reject">${t('reject')}</button></td></tr>`).join('')}</tbody></table>` : `<p>${t('noPending')}</p>`;
  document.querySelectorAll('[data-d]').forEach((b) => b.addEventListener('click', async () => {
    const id = b.closest('tr').dataset.id; const d = b.dataset.d;
    if (!(await modal({ title: d === 'approve' ? t('approveTitle') : t('rejectTitle'), body: `<p>${d === 'approve' ? t('approve') : t('reject')} ${esc(id)}?</p>`, confirm: d === 'approve' ? t('approve') : t('reject'), danger: d === 'reject' }))) return;
    try { await api(`/api/v1/payments/${id}/${d}`, { method: 'POST' }); toast(`Payment ${id} ${d === 'approve' ? 'approved' : 'rejected'}`); renderApprovals(); }
    catch (ex) { alertBox('#ap', ex.message); }
  }));
}

// ---------------------------------------------------------------- bulk upload + SSE
function renderBulk() {
  if (S.me.role === 'auditor') { shell(`<h1>${t('bulkTitle')}</h1><p role="alert" class="error">${t('noPermissionPay')}</p>`); return; }
  shell(`<h1>${t('bulkTitle')}</h1>
    <p><a href="/static/bulk-template.csv" download>${t('template')}</a></p>
    <div class="dropzone" id="dz">${t('dropHere')} <label for="file" class="linkish">${t('browse')}</label><input id="file" type="file" accept=".csv,text/csv" class="sr-only" aria-label="${t('chooseCsv')}"></div>
    <div id="bulk-result"></div><div id="job"></div>`, { title: t('bulkTitle') });
  let csv = null; let fname = '';
  const validate = async (file) => {
    fname = file.name; csv = await file.text();
    $('#bulk-result').innerHTML = skeleton(2);
    const v = await api('/api/v1/bulk-payments/validate', { method: 'POST', body: csv, headers: { 'content-type': 'text/csv' } });
    $('#bulk-result').innerHTML = v.valid
      ? `<p role="status">${esc(fname)}: ${t('fileOk', v.rows, fmtMoney(v.totalAmount))}</p><button type="button" id="submit-batch" class="primary">${t('submitBatch')}</button>`
      : `<p role="alert" class="error">${esc(fname)}: ${v.errors.length} error(s). Fix the file and upload it again.</p>
         <table class="grid"><caption>${t('fileErrors')}</caption><thead><tr><th>${t('row')}</th><th>${t('field')}</th><th>${t('problem')}</th></tr></thead>
         <tbody>${v.errors.map((x) => `<tr><td>${x.row}</td><td>${esc(x.field)}</td><td>${esc(x.message)}</td></tr>`).join('')}</tbody></table>`;
    $('#submit-batch')?.addEventListener('click', submit);
  };
  const submit = async () => {
    if (!(await modal({ title: t('submitBatch'), body: `<p>Submit ${esc(fname)} for processing?</p>`, confirm: t('submitBatch') }))) return;
    const job = await api('/api/v1/bulk-payments', { method: 'POST', body: csv, headers: { 'content-type': 'text/csv' } });
    $('#bulk-result').innerHTML = '';
    $('#job').innerHTML = `<section class="card" aria-labelledby="jh"><h2 id="jh">Job ${esc(job.id)}</h2>
      <div role="progressbar" aria-label="Batch progress" aria-valuemin="0" aria-valuemax="${job.total}" aria-valuenow="0" class="bar"><div></div></div>
      <p id="job-status" role="status">${t('processing', 0, job.total)}</p></section>`;
    const es = new EventSource(`/api/v1/bulk-payments/${job.id}/events`);
    es.addEventListener('progress', (e) => {
      const j = JSON.parse(e.data);
      const bar = $('[role=progressbar]'); if (!bar) { es.close(); return; }
      bar.setAttribute('aria-valuenow', j.processed); bar.firstElementChild.style.width = `${(100 * j.processed) / j.total}%`;
      const fin = j.status === 'completed' || j.status === 'completed_with_errors';
      $('#job-status').textContent = fin ? t('done', j.processed, j.total, j.failed) : t('processing', j.processed, j.total);
      if (fin) es.close();
    });
  };
  $('#file').addEventListener('change', (e) => e.target.files[0] && validate(e.target.files[0]));
  const dz = $('#dz');
  dz.addEventListener('dragover', (e) => { e.preventDefault(); dz.classList.add('over'); });
  dz.addEventListener('dragleave', () => dz.classList.remove('over'));
  dz.addEventListener('drop', (e) => { e.preventDefault(); dz.classList.remove('over'); const f = e.dataTransfer.files[0]; if (f) validate(f); });
}

// ---------------------------------------------------------------- cards (cross-origin iframe)
async function renderCards() {
  const accounts = await api('/api/v1/accounts');
  shell(`<h1>${t('cardsTitle')}</h1>
    <label for="card-acc">${t('cardAccount')}</label><select id="card-acc">${accounts.map((a) => `<option value="${a.id}">${esc(a.name)} (${esc(a.holder)})</option>`).join('')}</select>
    <iframe title="Card details" src="${WIDGET_ORIGIN}/widget/card-form" width="420" height="300"></iframe>
    <p id="card-status" role="status"></p>`, { title: t('cardsTitle') });
  const onMsg = async (e) => {
    if (e.origin !== WIDGET_ORIGIN || e.data?.type !== 'card-token') return;
    const accSel = $('#card-acc'); if (!accSel) { window.removeEventListener('message', onMsg); return; }
    try {
      const r = await api('/api/v1/cards', { method: 'POST', body: { accountId: accSel.value, token: e.data.token } });
      $('#card-status').textContent = t('cardSaved', r.last4, accSel.selectedOptions[0].textContent);
    } catch (ex) { $('#card-status').textContent = ex.message; }
  };
  window.addEventListener('message', onMsg);
}

// ---------------------------------------------------------------- settings (shadow DOM)
function renderSettings() {
  const f = S.me.flags;
  shell(`<h1>${t('settingsTitle')}</h1>
    <section><h2>${t('flags')}</h2><ul>${Object.entries(f).map(([k, v]) => `<li>${esc(k)}: <strong>${esc(v)}</strong></li>`).join('')}</ul></section>
    <section><h2>${t('converter')}</h2><nw-fx-converter heading="${t('converter')}"></nw-fx-converter></section>
    <section><h2>${t('security')}</h2><nw-security-key></nw-security-key></section>`, { title: t('settingsTitle') });
}

// ---------------------------------------------------------------- labs: legacy integrations
function renderLabs() {
  if (!S.me.flags.labs) { shell(`<h1>${t('notFound')}</h1>`); return; }
  shell(`<h1>${t('labs')}</h1><p class="muted">Old integrations kept for branch compatibility.</p>
    <ul class="labs">
      <li><button type="button" id="ws-sync">Sync favourites</button> <span class="muted">(live channel)</span></li>
      <li><button type="button" id="legacy-order">Resend last order</button></li>
      <li><button type="button" id="batch">Run quick actions</button></li>
      <li><button type="button" id="beacon">Send usage report</button></li>
      <li><button type="button" id="sw-reg">Enable offline mode</button> <button type="button" id="sw-flush">Sync offline queue</button></li>
      <li><button type="button" id="popup">Open partner portal</button></li>
      <li><a href="/go?url=http%3A%2F%2Foffers.partner-bank.example%2Fdeals">Partner offers</a></li>
      <li><form id="legacy-form" action="/legacy/repeat-payment" method="get"><label for="rp">Repeat payment ID</label><input id="rp" name="id" value="PMT-90002"></form></li>
    </ul><p id="labs-out" role="status"></p>`, { title: t('labs') });
  const out = (m) => { $('#labs-out').textContent = m; };
  const pay = { fromAccountId: 'ACC-1001', payeeId: 'PAY-001', amount: '1.00', currency: 'GBP', reference: 'labs' };
  $('#ws-sync').addEventListener('click', () => {
    const ws = new WebSocket(`${location.origin.replace('http', 'ws')}/ws`);
    ws.onopen = () => ws.send(JSON.stringify({ type: 'quickpay', csrf: S.csrf, ...pay, reference: 'ws sync' }));
    ws.onmessage = (e) => { const m = JSON.parse(e.data); out(m.type === 'paid' ? `Synced (${m.payment.id})` : `Sync error: ${m.error}`); ws.close(); };
    ws.onerror = () => out('Sync failed');
  });
  $('#legacy-order').addEventListener('click', async () => {
    try { const p = await api('/api/v1/orders', { method: 'POST', headers: { 'idempotency-key': uuid() }, body: { ...pay, reference: 'legacy order' } }); out(`Order resent (${p.id})`); }
    catch (ex) { out(`Order failed: ${ex.message}`); }
  });
  $('#batch').addEventListener('click', async () => {
    try { const r = await api('/api/v1/batch', { method: 'POST', body: { operations: [{ op: 'ping' }, { op: 'payments.create', args: { ...pay, reference: 'batch' } }] } }); out(`Quick actions: ${r.results.map((x) => (x.ok ? 'ok' : x.error)).join(', ')}`); }
    catch (ex) { out(`Quick actions failed: ${ex.message}`); }
  });
  $('#beacon').addEventListener('click', () => {
    const ok = navigator.sendBeacon('/api/v1/payments', new Blob([JSON.stringify({ ...pay, reference: 'beacon' })], { type: 'application/json' }));
    out(`Usage report queued: ${ok}`);
  });
  $('#sw-reg').addEventListener('click', async () => {
    try { await navigator.serviceWorker.register('/sw.js', { scope: '/' }); await navigator.serviceWorker.ready; out('Offline mode enabled'); }
    catch (ex) { out(`Offline mode failed: ${ex.message}`); }
  });
  $('#sw-flush').addEventListener('click', async () => {
    const reg = await navigator.serviceWorker.getRegistration();
    if (!reg?.active) { out('Offline mode is not enabled'); return; }
    const ch = new MessageChannel();
    ch.port1.onmessage = (e) => out(`Offline queue: ${e.data}`);
    reg.active.postMessage({ type: 'flush', csrf: S.csrf, key: uuid(), payment: { ...pay, reference: 'sw queue' } }, [ch.port2]);
  });
  $('#popup').addEventListener('click', () => { window.open('http://portal.partner-bank.example/login', 'partner'); out('Partner portal opened'); });
}

render();
