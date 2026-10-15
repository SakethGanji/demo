/* Mockup shell: renders the ProjectTopBar + ProjectOutline (+ 1024 rail) from ONE dataset so every
   screen agrees (review H6). Usage: <body data-active="overview" data-state="saved|draft|new">
   and a <div id="shell"></div> before the page content. Not app code. */
(function () {
  const D = window.MOCK_DATA = {
    project: { name: 'Payments Ops', slug: 'payments-ops' },
    modules: [
      { name: 'orders_api.py', routes: [['post', '/orders'], ['get', '/orders/summary']] },
      { name: 'refunds.py', routes: [['post', '/refunds'], ['get', '/refunds'], ['get', '/refunds/stats']] },
    ],
    tables: [['orders', 5], ['refunds', 3]],
    apps: ['Orders dashboard'],
    runs24h: 12,
  };
  const b = document.body;
  const active = b.dataset.active || 'overview';
  const state = b.dataset.state || 'saved';
  const tab = b.dataset.tab || 'project';
  const icon = (n, c = '') => `<i data-lucide="${n}" class="${c}"></i>`;

  const top = `
  <div class="topbar floating" data-testid="topbar">
    <span class="brand">luna</span><span class="crumb-sep">/</span>
    <span class="crumb"><span class="dot ${state === 'new' ? 'idle' : 'warn'}" title="Degraded: a referenced secret is missing in prod"></span>${state === 'new' ? 'Refunds Desk' : D.project.name}${icon('chevrons-up-down', 'chev i12')}</span>
    <div class="nav-tabs" style="margin-left:6px">
      <span class="nav-tab ${tab === 'project' ? 'on' : ''}">${icon('layout-dashboard', 'i14')}Project</span>
      <span class="nav-tab ${tab === 'workflows' ? 'on' : ''}">${icon('workflow', 'i14')}Workflows <span class="count">${state === 'new' ? 0 : 2}</span></span>
    </div>
    <div class="grow"></div>
    <span class="btn ghost sm hide-md" style="gap:8px">${icon('search', 'i14')}Search or jump to… <span class="kbd">⌘K</span></span>
    <span class="btn ghost icon sm show-md">${icon('search', 'i14')}</span>
    <span class="sep-v"></span>
    <div class="seg" title="Viewing environment (never changes where tests run)"><span class="on"><span class="dot ${state === 'new' ? 'idle' : 'ok'}" style="box-shadow:none"></span>dev</span><span><span class="dot ${state === 'new' ? 'idle' : 'warn'}"></span>prod</span></div>
    <span class="btn outline sm" style="margin-left:6px${state === 'new' ? ';opacity:.5' : ''}">${icon('rocket', 'i14')}Release${state === 'saved' && b.dataset.release ? ' <span class="badge secondary" style="height:18px;font-size:10.5px;padding:0 6px">prod #1</span>' : ''}</span>
    <span class="btn ghost icon sm">${icon('sun', 'i14')}</span>
  </div>`;

  const item = (key, ic, label, meta = '', extra = '') =>
    `<div class="ol-item ${active === key ? 'on' : ''}" ${extra}>${icon(ic)}${label}${meta ? `<span class="meta">${meta}</span>` : ''}</div>`;
  const pill = (t, cls = 'ai') => `<span class="badge ${cls}" style="height:16px;font-size:10px;padding:0 5px;margin-left:auto">${t}</span>`;

  let side = '';
  if (state === 'new') {
    side += item('setup', 'list-checks', 'Get started', '0 / 4');
    side += `<div class="ol-group"><span class="eyebrow">Backend</span><span class="btn ghost icon xs">${icon('plus', 'i14')}</span></div><div class="ol-empty">No routes yet</div>`;
    side += `<div class="ol-item" style="color:var(--ai)">${icon('sparkles')}Generate with AI</div>`;
    side += `<div class="ol-group"><span class="eyebrow">Data</span><span class="btn ghost icon xs">${icon('plus', 'i14')}</span></div><div class="ol-empty">No tables yet</div>`;
    side += `<div class="ol-group"><span class="eyebrow">Apps</span></div><div class="ol-empty">No apps yet</div>`;
    side += `<div class="ol-group"><span class="eyebrow">Project</span></div>`;
    side += item('secrets', 'key-round', 'Secrets', '0') + item('runs', 'activity', 'Runs', '—');
  } else {
    side += item('overview', 'layout-grid', 'Overview');
    side += `<div class="ol-group"><span class="eyebrow">Backend</span><span class="btn ghost icon xs">${icon('plus', 'i14')}</span></div>`;
    D.modules.forEach((m, mi) => {
      const draft = state === 'draft' && mi === 1;
      const on = (active === 'backend' && (b.dataset.module || 'refunds.py') === m.name);
      side += `<div class="ol-item ${on ? 'on' : ''}">${icon('file-code-2')}<span class="mono" style="font-size:12.5px">${m.name}</span><span class="meta" ${draft ? 'style="color:var(--ai)"' : ''}>${draft ? 'draft' : m.routes.length}</span></div>`;
      if (mi === 0 || active === 'backend' || state === 'draft') m.routes.forEach(([meth, p], ri) => {
        const sel = active === 'backend' && draft && ri === 2;
        side += `<div class="ol-route ${sel ? 'on' : ''}"><span class="method ${meth}">${meth.toUpperCase()}</span>${p}${draft ? pill('new') : ''}</div>`;
      });
    });
    if (state !== 'draft') side += `<div class="ol-item" style="color:var(--ai)">${icon('sparkles')}Generate with AI</div>`;
    side += `<div class="ol-group"><span class="eyebrow">Data</span><span class="btn ghost icon xs">${icon('plus', 'i14')}</span></div>`;
    D.tables.forEach(([t, n], i) => {
      if (state === 'draft' && t === 'refunds') side += `<div class="ol-item" style="color:var(--ai)">${icon('table-2')}refunds<span class="meta" style="color:var(--ai)">created on save</span></div>`;
      else side += `<div class="ol-item ${active === 'data' && (b.dataset.table || 'refunds') === t ? 'on' : ''}">${icon('table-2')}${t}<span class="meta num">${state === 'draft' && t === 'orders' ? 5 : n} rows</span></div>`;
    });
    side += `<div class="ol-group"><span class="eyebrow">Apps</span><span class="btn ghost icon xs">${icon('plus', 'i14')}</span></div>`;
    D.apps.forEach(a => side += `<div class="ol-item">${icon('app-window')}${a}<span class="meta">${icon('arrow-up-right', 'i12')}</span></div>`);
    side += `<div class="ol-group"><span class="eyebrow">Project</span></div>`;
    side += item('secrets', 'key-round', 'Secrets', '<span class="row gap-1h"><span class="dot warn"></span>1 missing</span>');
    side += item('runs', 'activity', 'Runs', `${D.runs24h} · 24h`);
  }
  side += `<div class="ol-footer">${item('settings', 'settings-2', 'Settings')}${item('keys', 'key-square', 'API keys')}</div>`;

  const railItem = (key, ic, tip, dot = '') => `<div class="rail-item ${active === key ? 'on' : ''}" title="${tip}">${icon(ic)}${dot}</div>`;
  const rail = `<aside class="rail panel" data-testid="outline-rail">
    ${railItem('overview', 'layout-grid', 'Overview')}${railItem('backend', 'code-xml', 'Backend (flyout lists modules)')}${railItem('data', 'table-2', 'Data (flyout lists tables)')}${railItem('apps', 'app-window', 'Apps')}${railItem('secrets', 'key-round', 'Secrets', state !== 'new' ? '<span class="rail-dot"></span>' : '')}${railItem('runs', 'activity', 'Runs')}
    <div style="margin-top:auto">${railItem('settings', 'settings-2', 'Settings')}</div></aside>`;

  const host = document.getElementById('shell');
  if (host) host.outerHTML = top + (b.dataset.noside ? '' : `<aside class="side panel" data-testid="outline">${side}</aside>` + rail);
})();
