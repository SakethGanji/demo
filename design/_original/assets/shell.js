/* Shared chrome (topbar + outline sidebar) + theme toggle. Pages call Shell.mount({ active }) . */
(function () {
  const root = document.documentElement;
  const q = new URLSearchParams(location.search).get('theme');
  let saved = null; try { saved = localStorage.getItem('luna-theme'); } catch (e) {}
  const isDark = (q || saved) === 'dark';
  root.classList.toggle('dark', isDark);

  const I = (n, c) => `<i data-lucide="${n}"${c ? ` class="${c}"` : ''}></i>`;
  const m = (v) => `<span class="method ${v.toLowerCase()}">${v}</span>`;

  function topbar(o) {
    return `
    <header class="topbar">
      <a class="brand" href="projects.html">luna</a>
      <span class="crumb-sep">/</span>
      <button class="switcher"><span class="dot ok"></span><span>Payments Ops</span>${I('chevrons-up-down')}</button>
      <nav class="seg soft lg" style="margin-left:8px">
        <a href="overview.html" class="${o.tab !== 'workflows' ? 'on' : ''}">${I('layout-grid')} Project</a>
        <a href="#" class="${o.tab === 'workflows' ? 'on' : ''}">${I('workflow')} Workflows <span class="count">0</span></a>
      </nav>
      <div class="spacer"></div>
      <button class="search-trigger">${I('search')} <span>Search or jump to…</span> <span class="kbd">⌘K</span></button>
      <span class="divider-v"></span>
      <div class="seg soft lg" id="env"><button class="on"><span class="dot ok"></span>dev</button><button><span class="dot warn"></span>prod</button></div>
      <button class="btn outline sm" style="margin-left:6px">${I('rocket')} Release</button>
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
        <a class="item${on('runs')}" href="runs.html">${I('history')} Runs <span class="tail">3 · 24h</span></a>
      </div>
      <div class="outline-bottom">
        <button class="item">${I('settings-2')} Settings</button>
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
