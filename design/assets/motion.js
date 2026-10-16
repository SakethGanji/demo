/* Motion and small details for the original pages. Runs after Shell.mount; changes behaviour, not the look. */
(function () {
  const $ = (s, r = document) => r.querySelector(s), $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const I = (n, c) => `<i data-lucide="${n}"${c ? ` class="${c}"` : ''}></i>`;

  function init() {
    timestamps(); sliders(); tabsLine(); sidebarPill(); tooltips(); copyUrl(); rowActions(); envLine(); palette(); stats(); liveRun(); themeSwap();
    if (window.lucide) lucide.createIcons();
    requestAnimationFrame(() => $$('.seg').forEach(place));
  }


  /* timestamps: relative text, exact time on hover */
  function timestamps() {
    $$('[data-ts]').forEach((el) => { const d = new Date(el.dataset.ts); el.dataset.tip = d.toLocaleString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false, timeZone: 'UTC' }) + ' UTC'; });
  }

  /* underline tabs: one indicator slides to the active tab */
  function tabsLine() {
    $$('.tabs').forEach((g) => {
      if (!g.querySelector('.tab.active')) return;
      g.style.position = 'relative'; g.classList.add('m-tabs'); g.insertAdjacentHTML('beforeend', '<span class="m-tabline"></span>');
      const put = () => { const a = g.querySelector('.tab.active'), l = g.querySelector('.m-tabline'); l.style.width = a.offsetWidth + 'px'; l.style.transform = `translateX(${a.offsetLeft}px)`; };
      put(); g.addEventListener('click', () => requestAnimationFrame(put)); addEventListener('resize', put);
      document.fonts && document.fonts.ready.then(put);
    });
  }

  /* sidebar: the active highlight slides from the previous page's item to this one */
  function sidebarPill() {
    const nav = $('.outline-scroll'), on = nav && nav.querySelector('.item.on'); if (!on) return;
    nav.style.position = 'relative'; nav.classList.add('m-pilled'); nav.insertAdjacentHTML('afterbegin', '<span class="m-pill"></span>');
    const pill = nav.querySelector('.m-pill'), to = on.offsetTop, h = on.offsetHeight;
    let from = to; try { const v = sessionStorage.getItem('luna-pill'); if (v != null) from = +v; sessionStorage.setItem('luna-pill', to); } catch (e) {}
    pill.style.height = h + 'px'; pill.style.transition = 'none'; pill.style.transform = `translateY(${from}px)`;
    requestAnimationFrame(() => requestAnimationFrame(() => { pill.style.transition = ''; pill.style.transform = `translateY(${to}px)`; }));
    $$('.item[href]', nav).forEach((a) => a.addEventListener('click', () => { try { sessionStorage.setItem('luna-pill', on.offsetTop); } catch (e) {} }));
  }

  /* theme toggle: switch instantly instead of fading every colour at once */
  function themeSwap() {
    const t = $('#theme-toggle'); if (!t) return;
    t.addEventListener('click', () => { document.documentElement.classList.add('m-no-tr'); requestAnimationFrame(() => requestAnimationFrame(() => document.documentElement.classList.remove('m-no-tr'))); }, true);
  }

  /* segmented controls: a single thumb slides to the active option */
  function place(g) {
    const on = g.querySelector(':scope > .on'), t = g.querySelector(':scope > .m-thumb');
    if (!on || !t) return;
    t.style.width = on.offsetWidth + 'px'; t.style.height = on.offsetHeight + 'px'; t.style.transform = `translateX(${on.offsetLeft}px)`;
  }
  function sliders() {
    $$('.seg').forEach((g) => {
      if (!g.querySelector(':scope > .on')) return;
      g.insertAdjacentHTML('afterbegin', '<span class="m-thumb"></span>'); g.classList.add('m-ready');
      g.addEventListener('click', (e) => {
        const b = e.target.closest('.seg > button, .seg > a'); if (!b) return;
        if (b.tagName === 'A' && b.getAttribute('href') === '#') e.preventDefault();
        g.querySelectorAll(':scope > button, :scope > a').forEach((x) => x.classList.toggle('on', x === b)); place(g);
      });
    });
    addEventListener('resize', () => $$('.seg').forEach(place));
    document.fonts && document.fonts.ready.then(() => $$('.seg').forEach(place));
  }

  /* tooltips: 500 ms delay, then instant while moving between targets */
  function tooltips() {
    const tips = [
      ['.search-trigger', 'Search projects, routes and commands', '⌘K'], ['.switcher', 'Switch project', '⌘P'], ['#theme-toggle', 'Toggle theme'],
      ['#env', 'Environment for tests and secrets'], ['.topbar .btn.outline', 'Create a release', '⇧R'], ['.group-head button', 'Create new'],
      ['.item.ai', 'Generate a module from a prompt', '⌘I'], ['.url', 'Copy https://commandstudio.dev/api/p/payments-ops'],
    ];
    tips.forEach(([s, t, k]) => $$(s).forEach((el) => { el.dataset.tip = t; if (k) el.dataset.kbd = k; }));
    const tip = document.createElement('div'); tip.className = 'm-tip'; document.body.appendChild(tip);
    let timer, warm = 0, cur = null;
    const show = (el) => {
      cur = el; tip.innerHTML = el.dataset.tip + (el.dataset.kbd ? `<span class="kbd">${el.dataset.kbd}</span>` : '');
      tip.classList.add('show'); const r = el.getBoundingClientRect(), w = tip.offsetWidth, h = tip.offsetHeight;
      tip.style.left = Math.min(Math.max(8, r.left + r.width / 2 - w / 2), innerWidth - w - 8) + 'px';
      tip.style.top = (r.top < 70 ? r.bottom + 8 : r.top - h - 8) + 'px';
    };
    const hide = () => { clearTimeout(timer); if (tip.classList.contains('show')) warm = Date.now() + 300; tip.classList.remove('show'); cur = null; };
    document.addEventListener('pointerover', (e) => { const el = e.target.closest('[data-tip]'); if (!el || el === cur) return; hide(); timer = setTimeout(() => show(el), Date.now() < warm ? 0 : 500); });
    document.addEventListener('pointerout', (e) => { const el = e.target.closest('[data-tip]'); if (el && !el.contains(e.relatedTarget)) hide(); });
    document.addEventListener('pointerdown', hide);
    window.MotionTip = { show, tip };
  }

  /* URL chip: copy, check, "Copied" */
  function copyUrl() {
    const u = $('.url'); if (!u) return;
    const icon = u.querySelector('[data-lucide="copy"], svg'); if (icon) icon.outerHTML = `<span class="m-swap">${I('copy', 'a')}${I('check', 'b')}</span>`;
    u.addEventListener('click', () => {
      try { navigator.clipboard.writeText('https://commandstudio.dev/api/p/payments-ops').catch(() => {}); } catch (e) {}
      const sw = u.querySelector('.m-swap'); sw.classList.add('done');
      window.MotionTip.show(u); window.MotionTip.tip.textContent = 'Copied';
      setTimeout(() => sw.classList.remove('done'), 1200);
    });
  }

  /* table rows: the play icon becomes a hover-revealed button with a tooltip */
  function rowActions() {
    $$('.table td').forEach((td) => {
      const p = td.querySelector('[data-lucide="play"]'); if (!p || td.children.length !== 1) return;
      td.innerHTML = `<button class="m-act" data-tip="Test this route" data-kbd="↵">${I('play')}</button>`;
    });
  }

  /* environment: prod draws a thin warning line along the top of the main panel */
  function envLine() {
    const env = $('#env'), ws = $('.workspace'); if (!env || !ws) return;
    env.addEventListener('click', (e) => { const b = e.target.closest('button'); if (b) ws.classList.toggle('m-prod', /prod/.test(b.textContent)); });
  }

  /* ⌘K palette */
  function palette() {
    const CMDS = [['Jump to', 'file-code-2', 'refunds.py', 'G B', 'backend.html'], ['Jump to', 'table-2', 'refunds table', 'G D', 'data.html'], ['Jump to', 'history', 'Runs', 'G R', 'runs.html'],
      ['Jump to', 'layout-grid', 'Projects', 'G P', 'projects.html'], ['Actions', 'sparkles', 'Generate a module with AI', '⌘I'], ['Actions', 'wand-sparkles', 'Create UI from backend', '⌘U'],
      ['Actions', 'key-round', 'Add PAYMENTS_TOKEN to prod', ''], ['Actions', 'rocket', 'Create a release', '⇧R']];
    document.body.insertAdjacentHTML('beforeend', `<div class="m-scrim"></div><div class="m-pal" role="dialog" aria-label="Command palette"><input id="m-pq" placeholder="Search or run a command…" autocomplete="off"><div class="res"></div><div class="ft"><span><span class="kbd">↑↓</span> move</span><span><span class="kbd">↵</span> open</span><span><span class="kbd">esc</span> close</span></div></div>`);
    const pal = $('.m-pal'), scrim = $('.m-scrim'), q = $('#m-pq'), res = $('.m-pal .res'); let hl = 0, list = [];
    const render = () => {
      list = CMDS.filter((c) => c[2].toLowerCase().includes(q.value.toLowerCase())); hl = Math.min(hl, Math.max(0, list.length - 1)); let last = '';
      res.innerHTML = list.map((c, i) => (c[0] !== last ? `<div class="lbl">${(last = c[0])}</div>` : '') + `<div class="mi${i === hl ? ' hl' : ''}" data-i="${i}">${I(c[1])}${c[2]}${c[3] ? `<span class="kbd">${c[3]}</span>` : ''}</div>`).join('') || '<div class="lbl">No matches</div>';
      if (window.lucide) lucide.createIcons();
    };
    const mark = () => $$('.mi', res).forEach((x) => x.classList.toggle('hl', +x.dataset.i === hl));
    const open = () => { scrim.classList.add('open'); pal.classList.add('open'); q.value = ''; hl = 0; render(); setTimeout(() => q.focus(), 30); };
    const close = () => { scrim.classList.remove('open'); pal.classList.remove('open'); };
    const go = () => { const c = list[hl]; close(); if (c && c[4]) setTimeout(() => (location.href = c[4]), 120); };
    $$('.search-trigger').forEach((b) => b.addEventListener('click', open)); scrim.addEventListener('click', close);
    q.addEventListener('input', () => { hl = 0; render(); });
    res.addEventListener('pointermove', (e) => { const m = e.target.closest('.mi'); if (m && +m.dataset.i !== hl) { hl = +m.dataset.i; mark(); } });
    res.addEventListener('click', (e) => { if (e.target.closest('.mi')) go(); });
    document.addEventListener('keydown', (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); pal.classList.contains('open') ? close() : open(); return; }
      if (!pal.classList.contains('open')) return; const n = list.length || 1;
      if (e.key === 'Escape') close();
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { e.preventDefault(); hl = (hl + (e.key === 'ArrowDown' ? 1 : n - 1)) % n; mark(); }
      if (e.key === 'Enter') go();
    });
  }

  /* stats: the Runs value rolls when it changes; the dotted line becomes a real sparkline */
  const spark = [3, 1, 1, 6, 14, 19, 17, 19, 17, 13, 9, 9]; // runs per 2 h, 128 in 24 h
  function drawSpark() { const s = $('.m-spark'); if (s) s.innerHTML = spark.map((v, i) => { const h = Math.max(2, v / 19 * 18); return `<rect x="${i * 5.4}" y="${20 - h}" width="3.4" height="${h}" rx="1" class="${i === spark.length - 1 ? 'last' : ''}"/>`; }).join(''); }
  function setRoll(el, v) {
    const s = String(v);
    while (el.children.length < s.length) el.insertAdjacentHTML('afterbegin', `<span class="m-dg">${'0123456789'.split('').map((d) => `<span>${d}</span>`).join('')}</span>`);
    const n = el.children.length; // --d-slow, --ease-out; digits settle right to left, 20 ms apart
    [...el.children].forEach((d, i) => { d.style.transition = `transform var(--d-slow) var(--ease-out) ${(n - 1 - i) * 20}ms`; d.style.transform = `translateY(-${+s[i] * 1.2}em)`; });
  }
  function stats() {
    const runs = $$('.stat').find((s) => /Runs/.test(s.textContent)); if (!runs) return;
    const v = runs.querySelector('.value'); v.innerHTML = '<span class="m-roll" id="m-runs"></span><svg class="m-spark" width="64" height="20" viewBox="0 0 64 20" preserveAspectRatio="none"></svg>';
    setRoll($('#m-runs'), 128); drawSpark();
  }

  /* a real state change: a request arrives, runs, completes; everything that depends on it updates once */
  function liveRun() {
    const panel = $$('.panel').find((p) => /Recent runs/.test(p.querySelector('.panel-head')?.textContent || '')); if (!panel || !$('#m-runs')) return;
    setTimeout(() => {
      panel.querySelector('.panel-head').insertAdjacentHTML('afterend', `<div class="li m-new" id="m-run"><span class="dot m-live"></span><span class="method post" style="background:none">POST</span><span class="mono">/refunds</span><span class="r"><span id="m-dur">running</span> &nbsp; now</span></div>`);
    }, 1400);
    setTimeout(() => {
      const d = $('#m-run .dot'); d.classList.remove('m-live'); d.classList.add('ok', 'flat'); $('#m-dur').textContent = '41 ms';
      setRoll($('#m-runs'), 129); if (window.LunaArt) { LunaArt.pulseWire('system', 'post'); LunaArt.pulseWire('system', 'app', { delay: 560 }); }
      spark[spark.length - 1]++; drawSpark();
      const tail = $$('.item').find((i) => /Runs/.test(i.textContent))?.querySelector('.tail'); if (tail) tail.textContent = '129 · 24h';
      const row = $$('.table tbody tr').find((r) => /createRefund/.test(r.textContent));
      if (row) { const c = row.children; c[4].innerHTML = '<span class="dot ok flat" style="margin-right:6px"></span>success · now'; c[4].classList.remove('muted'); c[5].textContent = '41 ms'; row.classList.add('m-flash'); }
    }, 2800);
  }

  window.MotionRoll = setRoll;
  window.Motion = { $, $$, setRoll, place };

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => setTimeout(init)); else setTimeout(init);
})();
