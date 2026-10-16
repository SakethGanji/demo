/* Shared chrome (topbar + outline sidebar) + theme toggle. Pages call Shell.mount({ active }) . */
(function () {
  const root = document.documentElement;
  const q = new URLSearchParams(location.search).get('theme');
  let saved = null; try { saved = localStorage.getItem('luna-theme'); } catch (e) {}
  const isDark = (q || saved) === 'dark';
  root.classList.toggle('dark', isDark);

  // Browser-tab icon: the Command Studio mark.
  document.write('<link rel="icon" type="image/svg+xml" href="assets/logo.svg">');
  // Glass for floating layers only (palette, menus, tooltips, sticky bar).
  document.write('<link rel="stylesheet" href="assets/glass.css">');
  try { localStorage.removeItem('luna-glass'); } catch (e) {}

  // Layout variant: floating (default) | docked | linear. ?layout=… or the bottom-left switcher; remembered.
  const LAYOUTS = ['linear', 'floating', 'docked'];
  let layout = new URLSearchParams(location.search).get('layout');
  try { if (layout) localStorage.setItem('cs-layout', layout); else layout = localStorage.getItem('cs-layout'); } catch (e) {}
  if (!LAYOUTS.includes(layout)) layout = 'linear';
  root.dataset.layout = layout;
  document.write('<link rel="stylesheet" href="assets/layout.css">');
  // "Lean in" layer: crisper corners, entity colour, space atmosphere. ?lean=0 compares without it.
  if (new URLSearchParams(location.search).get('lean') !== '0') document.write('<link rel="stylesheet" href="assets/lean.css">');
  document.write('<link rel="stylesheet" href="assets/accent.css"><script src="assets/accent.js"><\/script>');
  // Refine layer: usability and consistency fixes. ?refine=0 compares without it.
  // The accent and layout pills are for comparing variants, so they only show when one was asked for.
  const qs = new URLSearchParams(location.search);
  root.dataset.page = (location.pathname.split('/').pop() || 'index.html').replace('.html', '');
  if (qs.has('accent') || qs.has('layout') || qs.has('compare') || qs.get('refine') === '0') root.dataset.compare = '';
  if (qs.get('refine') !== '0') {
    document.write('<link rel="stylesheet" href="assets/refine.css">');
    // Projects home: a denser sky over a slight gradient, and frosted glass cards. ?hero=0 compares without it.
    if (root.dataset.page === 'projects' && qs.get('hero') !== '0') { root.dataset.hero = ''; document.write('<script src="assets/home-sky.js"><\/script>'); }
  }
  document.addEventListener('DOMContentLoaded', () => setTimeout(() => {
    if (document.querySelector('.lay-switch')) return;
    let asked = new URLSearchParams(location.search).has('layout'); try { asked = asked || !!localStorage.getItem('cs-layout'); } catch (e) {}
    if (!asked) return;
    const w = document.createElement('div'); w.className = 'lay-switch'; w.setAttribute('role', 'group'); w.setAttribute('aria-label', 'Layout');
    w.innerHTML = LAYOUTS.map((l) => `<button type="button" data-l="${l}" aria-pressed="${l === root.dataset.layout}">${l[0].toUpperCase() + l.slice(1)}</button>`).join('');
    w.addEventListener('click', (e) => {
      const b = e.target.closest('button'); if (!b) return;
      root.dataset.layout = b.dataset.l; try { localStorage.setItem('cs-layout', b.dataset.l); } catch (e2) {}
      w.querySelectorAll('button').forEach((x) => x.setAttribute('aria-pressed', x === b));
      window.Edges?.refresh?.(); dispatchEvent(new Event('resize'));
    });
    document.body.appendChild(w);
  }));

  const I = (n, c) => `<i data-lucide="${n}"${c ? ` class="${c}"` : ''}></i>`;
  const m = (v) => `<span class="method ${v.toLowerCase()}">${v}</span>`;

  function topbar(o) {
    return `
    <header class="topbar">
      <a class="brand" href="projects.html" aria-label="Command Studio"><span class="brand-mark"></span><span class="brand-word">Command <span>Studio</span></span></a>
      <span class="crumb-sep">/</span>
      <button class="switcher" data-kit="switcher" aria-haspopup="listbox" aria-expanded="false"><span class="dot ok"></span><span>Payments Ops</span>${I('chevrons-up-down')}</button>
      <nav class="seg soft lg" style="margin-left:8px">
        <a href="overview.html" class="${o.tab !== 'workflows' ? 'on' : ''}">${I('layout-grid')} Project</a>
        <a href="workflows.html" class="${o.tab === 'workflows' ? 'on' : ''}">${I('workflow')} Workflows <span class="count">1</span></a>
      </nav>
      <div class="spacer"></div>
      <button class="search-trigger">${I('search')} <span>Search or jump to…</span> <span class="kbd">⌘K</span></button>
      <span class="divider-v"></span>
      <div class="seg soft lg" id="env"><button class="on"><span class="dot ok"></span>dev</button><button><span class="dot warn"></span>prod</button></div>
      <button class="btn outline sm" data-kit="release" aria-haspopup="dialog" style="margin-left:6px">${I('rocket')} Release</button>
      <button class="btn ghost icon-sm" id="theme-toggle" aria-label="Toggle theme">${I(isDark ? 'moon' : 'sun')}</button>
    </header>`;
  }

  function sidebar(active) {
    const on = (k) => (active === k ? ' on' : '');
    return `
    <aside class="sidebar panel"><nav class="outline-nav">
      <div class="outline-scroll">
        <a class="item${on('overview')}" href="overview.html">${I('layout-grid')} Overview</a>
        <div class="group-head"><span class="eyebrow">Backend</span><button>${I('plus')}</button></div>
        <a class="item${on('backend')}" href="backend.html">${I('file-code-2')} <span class="mono" style="font-size:12.5px">refunds.py</span><span class="tail">3</span></a>
        <a class="item route" href="backend.html">${m('GET')}<span>/refunds</span></a>
        <a class="item route" href="backend.html">${m('POST')}<span>/refunds</span></a>
        <a class="item route${active === 'backend' ? ' on' : ''}" href="backend.html">${m('GET')}<span>/refunds/stats</span></a>
        <a class="item ai" href="backend.html">${I('sparkles')} Generate with AI</a>
        <div class="group-head"><span class="eyebrow">Data</span><button>${I('plus')}</button></div>
        <a class="item${on('data')}" href="data.html">${I('table-2')} refunds <span class="tail">3 rows</span></a>
        <div class="group-head"><span class="eyebrow">Apps</span><button>${I('plus')}</button></div>
        <a class="item" href="#">${I('app-window')} Refunds dashboard ${I('arrow-up-right')}</a>
        <div class="group-head"><span class="eyebrow">Project</span></div>
        <a class="item${on('secrets')}" href="#">${I('key-round')} Secrets <span class="tail warn-text" style="display:inline-flex;gap:6px;align-items:center"><span class="dot warn"></span>1 missing</span></a>
        <a class="item${on('runs')}" href="runs.html">${I('history')} Runs <span class="tail">128 · 24h</span></a>
      </div>
      <div class="outline-bottom">
        <button class="item" data-kit="settings" aria-haspopup="dialog">${I('settings-2')} Settings</button>
        <button class="item">${I('key')} API keys</button>
      </div>
    </nav></aside>`;
  }

  function mount(o) {
    o = o || {};
    const host = document.getElementById('shell');
    host.className = 'workspace';
    host.innerHTML = topbar(o) + `<div class="workspace-body">${sidebar(o.active)}<div class="main" id="main"></div></div>`;
    const main = document.getElementById('main');
    const tpl = document.getElementById('page');
    main.appendChild(tpl.content);
    if (o.mainClass) main.className += ' ' + o.mainClass;
    bind();
  }

  function bind() {
    const t = document.getElementById('theme-toggle');
    if (t) t.addEventListener('click', () => {
      const d = !root.classList.contains('dark');
      root.classList.toggle('dark', d);
      try { localStorage.setItem('luna-theme', d ? 'dark' : 'light'); } catch (e) {}
      t.innerHTML = `<i data-lucide="${d ? 'moon' : 'sun'}"></i>`;
      icons();
    });
    document.querySelectorAll('.seg').forEach((g) => {
      if (g.dataset.static != null) return;
      g.addEventListener('click', (e) => {
        const b = e.target.closest('button'); if (!b || !g.contains(b)) return;
        g.querySelectorAll(':scope > button').forEach((x) => x.classList.toggle('on', x === b));
      });
    });
    document.querySelectorAll('.tabs').forEach((g) => g.addEventListener('click', (e) => {
      const b = e.target.closest('.tab'); if (!b) return;
      g.querySelectorAll('.tab').forEach((x) => x.classList.toggle('active', x === b));
    }));
    icons();
  }
  function icons() { if (window.lucide) lucide.createIcons(); }

  window.Shell = { mount, bind, icons, I, m };
  document.addEventListener('DOMContentLoaded', () => { if (!document.getElementById('shell')) bind(); });
})();
