/* Accent options: stars | scaffold | none. ?accent=… (remembered as cs-accent). Builds the decorations once; CSS shows the active set. */
(function () {
  const root = document.documentElement;
  const OPTS = ['stars', 'scaffold', 'none'];
  let acc = new URLSearchParams(location.search).get('accent');
  try { if (acc) localStorage.setItem('cs-accent', acc); else acc = localStorage.getItem('cs-accent'); } catch (e) {}
  if (!OPTS.includes(acc)) acc = 'stars';
  root.dataset.accent = acc;

  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const pageKey = (location.pathname.split('/').pop() || 'projects.html').replace('.html', '');

  function tiles() { return $$('.main .page .bt.panel, .card.panel, .tpl, .pulse, .main > .editor > .panel'); }

  function scaffold() {
    // tile labels + corner ticks
    tiles().forEach((t, i) => {
      if (t.querySelector(':scope > .sc-label')) return;
      if (getComputedStyle(t).position === 'static') t.style.position = 'relative';
      const name = (t.querySelector('.panel-head h3, .name, .bd b, h1, .tabs .tab.active') || {}).textContent || t.dataset.scName || '';
      t.insertAdjacentHTML('afterbegin', `<span class="sc-label"><b>${String(i + 1).padStart(2, '0')}</b> / ${name.trim().split('\n')[0].slice(0, 22)}</span>`
        + ['tl', 'tr', 'bl', 'br'].map((c) => `<span class="sc-tick ${c}"></span>`).join(''));
    });
    // rulers on the sheet (or the projects page body)
    const sheet = document.querySelector('.main') || document.querySelector('main');
    if (sheet && !sheet.querySelector(':scope > .sc-ruler')) {
      if (getComputedStyle(sheet).position === 'static') sheet.style.position = 'relative';
      const w = sheet.clientWidth, h = sheet.clientHeight;
      const top = [...Array(Math.ceil(w / 80))].map((_, i) => `<span style="left:${i * 80}px">${String.fromCharCode(65 + (i % 26))}</span>`).join('');
      const left = [...Array(Math.ceil(h / 80))].map((_, i) => `<span style="top:${i * 80}px">${i + 1}</span>`).join('');
      sheet.insertAdjacentHTML('afterbegin', `<div class="sc-ruler top">${top}</div><div class="sc-ruler left">${left}</div>`);
    }
    // status line
    const host = document.querySelector('.main') || document.body;
    if (!document.querySelector('.sc-status')) {
      const runs = (document.querySelector('.sidebar .item[href="runs.html"] .tail') || {}).textContent || '';
      const el = document.createElement('div'); el.className = 'sc-status';
      if (host === document.body) { el.style.position = 'fixed'; }
      el.innerHTML = `<span><b>CS</b>·${pageKey.toUpperCase()}</span><span>payments-ops</span><span class="ok">● dev</span><span class="warn">▲ prod: 1 secret</span>`
        + `<span>runs ${runs.split('·')[0].trim() || '129'}/24h</span><span class="sp"></span>`
        + `<span><kbd>⌘K</kbd> command</span><span><kbd>⇧R</kbd> release</span><span><kbd>?</kbd> keys</span><span id="sc-clock">UTC --:--:--</span>`;
      host.appendChild(el);
      const tick = () => { const d = new Date(); const c = document.getElementById('sc-clock'); if (c) c.textContent = 'UTC ' + d.toISOString().slice(11, 19); };
      tick(); setInterval(tick, 1000);
    }
  }

  /* ── realistic starfield ── */
  function rng(seed) { let x = seed % 2147483647; if (x <= 0) x += 2147483646; return () => (x = (x * 16807) % 2147483647) / 2147483647; }
  const TEMPS = [[255, 255, 255, .55], [205, 222, 255, .25], [255, 242, 224, .14], [255, 208, 170, .06]];
  function temp(u) { let a = 0; for (const t of TEMPS) { a += t[3]; if (u < a) return t; } return TEMPS[0]; }
  function paint(cv, w, h, o) {
    const dpr = Math.min(2, devicePixelRatio || 1), ctx = cv.getContext('2d');
    cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr); ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.clearRect(0, 0, w, h);
    const R = rng(o.seed || 7), dark = root.classList.contains('dark'), n = Math.round(w * h / o.density);
    for (let k = 0; k < n; k++) {
      const x = R() * w, y = R() * h, u = R();
      const r = 0.28 + Math.pow(u, 16) * (o.max || 2.7);          // power law: mostly pinpricks, rare bright stars
      let a = 0.14 + Math.pow(R(), 2.2) * 0.5 + (r > 1 ? 0.3 : 0);
      const t = dark ? temp(R()) : [38, 34, 28]; if (!dark) a *= 0.32;
      const col = (al) => `rgba(${t[0]},${t[1]},${t[2]},${Math.min(1, al)})`;
      if (dark && r > 1.05) {                                  // soft halo
        const g = ctx.createRadialGradient(x, y, 0, x, y, r * 4.5); g.addColorStop(0, col(a * .3)); g.addColorStop(1, col(0));
        ctx.fillStyle = g; ctx.beginPath(); ctx.arc(x, y, r * 4.5, 0, 7); ctx.fill();
      }
      if (dark && r > 2.45 && !o.noSpikes) {                     // diffraction spikes on the rare brightest
        ctx.lineWidth = .6;
        [[1, 0], [0, 1]].forEach(([dx, dy]) => { const L = r * 7, g = ctx.createLinearGradient(x - dx * L, y - dy * L, x + dx * L, y + dy * L);
          g.addColorStop(0, col(0)); g.addColorStop(.5, col(a * .7)); g.addColorStop(1, col(0)); ctx.strokeStyle = g;
          ctx.beginPath(); ctx.moveTo(x - dx * L, y - dy * L); ctx.lineTo(x + dx * L, y + dy * L); ctx.stroke(); });
      }
      ctx.fillStyle = col(a); ctx.beginPath(); ctx.arc(x, y, r, 0, 7); ctx.fill();
    }
  }
  function sky() {
    let c = document.querySelector('.cs-sky');
    if (!c) { c = document.createElement('canvas'); c.className = 'cs-sky'; c.setAttribute('aria-hidden', 'true'); document.body.prepend(c); }
    paint(c, innerWidth, innerHeight, { density: 2400, seed: 11, max: 2.2 });
  }
  function sheetSky() {
    const m = document.querySelector('.main') || document.querySelector('main'); if (!m) return;
    let c = m.querySelector(':scope > .cs-sky-sheet');
    if (!c) { if (getComputedStyle(m).position === 'static') m.style.position = 'relative'; c = document.createElement('canvas'); c.className = 'cs-sky-sheet'; c.setAttribute('aria-hidden', 'true'); m.prepend(c); }
    paint(c, m.scrollWidth || m.clientWidth, Math.max(m.clientHeight, m.scrollHeight), { density: 3200, seed: 29, max: 1.8 });
  }
  function dustTile() {
    const c = document.createElement('canvas'); paint(c, 480, 480, { density: 1500, seed: 47, max: 1.5, noSpikes: true });
    root.style.setProperty('--star-tile', `url(${c.toDataURL()})`);
  }
  function stars() {
    sky(); sheetSky(); dustTile();
    const spots = [['.bt-sys', 'top:18px;right:26px'], ['.hero', 'top:2px;left:-24px'], ['.bt-runs', 'bottom:-8px;right:30px']];
    spots.forEach(([sel, pos], i) => { const el = document.querySelector(sel); if (!el || el.querySelector(':scope > .sparkle')) return;
      if (getComputedStyle(el).position === 'static') el.style.position = 'relative';
      el.insertAdjacentHTML('beforeend', `<span class="sparkle${i % 2 ? ' sm' : ''}" style="${pos}"></span>`); });
    let t; addEventListener('resize', () => { clearTimeout(t); t = setTimeout(() => { sky(); sheetSky(); }, 120); });
    new MutationObserver(() => { sky(); sheetSky(); dustTile(); }).observe(root, { attributes: true, attributeFilter: ['class'] });
  }

  function switcher() {
    const w = document.createElement('div'); w.className = 'acc-switch'; w.setAttribute('role', 'group'); w.setAttribute('aria-label', 'Accent');
    w.innerHTML = OPTS.map((o) => `<button type="button" data-a="${o}" aria-pressed="${o === root.dataset.accent}">${o[0].toUpperCase() + o.slice(1)}</button>`).join('');
    w.addEventListener('click', (e) => { const b = e.target.closest('button'); if (!b) return; root.dataset.accent = b.dataset.a;
      try { localStorage.setItem('cs-accent', b.dataset.a); } catch (e2) {} w.querySelectorAll('button').forEach((x) => x.setAttribute('aria-pressed', x === b)); });
    document.body.appendChild(w);
  }

  function init() { scaffold(); stars(); switcher(); }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => setTimeout(init, 60)); else setTimeout(init, 60);
})();
