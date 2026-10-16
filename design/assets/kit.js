/* Kit: shared interactive components. Contract (implemented by the kit owner):
   Kit.toast({ title, desc?, tone?: 'ok'|'warn'|'err'|'info'|'busy', action?: { label, onClick }, duration? }) -> { update(opts), close() }
   Kit.sheet({ title, body: html|Node, footer?: html|Node, width? }) -> { el, close() }
   Kit.menu(anchorEl, { items: [{ label, sub?, icon?, dot?, kbd?, group?, onSelect }], search?: bool, placeholder?, width? }) -> { close() }
   Kit.beam(el, on: bool | 'once')   // state-driven light beam around an element's border

   Details (all optional extras on top of the contract; see DESIGN.md §10):
   - title / desc / label / sub are trusted HTML strings (so `<span class="mono">` works); pass text you control.
   - toast:  tone defaults to 'info'. duration defaults to 4000 ms ('busy' stays until updated); 0 or Infinity = sticky.
             action: { label, onClick?, href? }; onClick returning false keeps the toast open. Hover pauses the timer.
             update(opts) merges opts and restarts the timer (busy → ok/err is the usual flow).
   - sheet:  also returns { body, foot, setFooter(html|Node) }. opts.onClose(), opts.width (default 440).
             Esc and the scrim close it; focus moves into the sheet and back to the opener on close. One sheet at a time.
   - menu:   item.checked (current item, check mark), item.method ('GET'… → method chip; or icon:'GET'),
             item.dot ('ok'|'warn'|'err'|'idle'|'live'), item.href, item.disabled, { separator: true }.
             opts.align: 'start' (default) | 'end'. Calling Kit.menu on the same anchor again toggles it closed.
   - beam:   true = 3 s linear laps until false (then fades over --d-slow); 'once' = one 560 ms lap (--ease-io).
             Reduced motion: no beam, a static .vx-edge while on. */
(function () {
  'use strict';
  const Kit = (window.Kit = window.Kit || {});
  const D = { instant: 80, fast: 140, base: 200, slow: 280 };
  const reduced = () => window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches;
  const $ = (s, r = document) => r.querySelector(s), $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const I = (n) => `<i data-lucide="${n}"></i>`;
  const icons = () => { if (window.lucide) lucide.createIcons(); };
  const div = (cls, tag = 'div') => { const e = document.createElement(tag); e.className = cls; return e; };
  const put = (host, c) => { if (c == null || !host) return; if (c instanceof Node) host.appendChild(c); else host.insertAdjacentHTML('beforeend', String(c)); };
  const METHODS = /^(GET|POST|PUT|PATCH|DELETE)$/i;
  const CHECK = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 6 9 17l-5-5"/></svg>';
  const method = (m) => `<span class="method ${m.toLowerCase()}">${m.toUpperCase()}</span>`;
  const ready = (fn) => (document.body ? fn() : document.addEventListener('DOMContentLoaded', fn, { once: true }));

  /* ── layers: the top-most menu or sheet gets Esc and the arrow keys ── */
  const layers = [];
  const top = () => layers[layers.length - 1];
  const drop = (l) => { const i = layers.indexOf(l); if (i >= 0) layers.splice(i, 1); };

  /* ════════ Toast ════════ */
  let tray = null;
  const toasts = [];
  const TONE = { ok: 'dot ok', warn: 'dot warn', err: 'dot err', info: 'dot flat kit-info', busy: 'dot m-live' };
  function trayEl() {
    if (!tray || !tray.isConnected) {
      tray = div('kit-toasts'); tray.setAttribute('role', 'region'); tray.setAttribute('aria-label', 'Notifications');
      document.body.appendChild(tray);
    }
    return tray;
  }
  function renderToast(t) {
    const o = t.opts, tone = TONE[o.tone] ? o.tone : 'info', a = o.action;
    t.el.setAttribute('role', tone === 'err' ? 'alert' : 'status');
    t.el.dataset.tone = tone;
    t.el.innerHTML =
      `<span class="kit-toast-dot"><span class="${TONE[tone]}"></span></span>` +
      `<div class="kit-toast-text"><div class="kit-toast-title">${o.title || ''}</div>${o.desc ? `<div class="kit-toast-desc">${o.desc}</div>` : ''}</div>` +
      `<div class="kit-toast-acts">${a && a.label ? `<button class="btn ghost sm kit-toast-act">${a.label}</button>` : ''}` +
      `<button class="btn ghost icon-xs kit-toast-x" aria-label="Dismiss">${I('x')}</button></div>`;
    icons();
  }
  function armToast(t) {
    clearTimeout(t.timer);
    const o = t.opts, d = o.duration != null ? o.duration : o.tone === 'busy' ? 0 : 4000;
    t.left = d > 0 && isFinite(d) ? d : 0;
    if (t.left && !t.hover) { t.start = Date.now(); t.timer = setTimeout(() => closeToast(t), t.left); }
  }
  function closeToast(t) {
    if (t.closed) return; t.closed = true; clearTimeout(t.timer);
    const i = toasts.indexOf(t); if (i >= 0) toasts.splice(i, 1);
    t.el.classList.remove('kit-in'); t.el.classList.add('kit-out');
    setTimeout(() => t.el.remove(), reduced() ? 0 : D.fast + 20);
  }
  Kit.toast = function (opts) {
    const t = { opts: Object.assign({}, typeof opts === 'string' ? { title: opts } : opts), el: div('kit-toast'), timer: 0, left: 0, start: 0, hover: false, closed: false };
    const api = {
      update(o) { if (t.closed) return api; Object.assign(t.opts, o || {}); renderToast(t); armToast(t); return api; },
      close() { closeToast(t); },
      get el() { return t.el; },
    };
    ready(() => {
      renderToast(t);
      t.el.addEventListener('mouseenter', () => { t.hover = true; if (t.left && t.timer) { clearTimeout(t.timer); t.timer = 0; t.left = Math.max(800, t.left - (Date.now() - t.start)); } });
      t.el.addEventListener('mouseleave', () => { t.hover = false; if (t.left && !t.closed) { t.start = Date.now(); t.timer = setTimeout(() => closeToast(t), t.left); } });
      t.el.addEventListener('click', (e) => {
        if (e.target.closest('.kit-toast-x')) return closeToast(t);
        const b = e.target.closest('.kit-toast-act'); if (!b) return;
        const a = t.opts.action || {};
        const keep = typeof a.onClick === 'function' ? a.onClick(e, api) === false : false;
        if (a.href) location.href = a.href;
        if (!keep) closeToast(t);
      });
      trayEl().appendChild(t.el); toasts.push(t);
      // at most three: the oldest finished one leaves first, then the oldest of any kind
      while (toasts.length > 3) closeToast(toasts.find((x) => x.opts.tone !== 'busy') || toasts[0]);
      void t.el.offsetWidth; t.el.classList.add('kit-in');
      armToast(t);
    });
    return api;
  };

  /* toasts step left of an open sheet, so they never cover its footer */
  function toastRoom() {
    const s = layers.filter((l) => l.type === 'sheet').pop(), r = document.documentElement.style;
    if (s && innerWidth - s.el.offsetWidth - 36 >= 360) r.setProperty('--kit-toast-r', s.el.offsetWidth + 28 + 'px');
    else r.removeProperty('--kit-toast-r');
  }

  /* ════════ Sheet ════════ */
  Kit.sheet = function (opts) {
    opts = opts || {};
    layers.filter((l) => l.type === 'sheet').forEach((l) => l.close());
    const opener = document.activeElement;
    const scrim = div('kit-scrim'), el = div('kit-sheet', 'section');
    el.setAttribute('role', 'dialog'); el.setAttribute('aria-modal', 'true'); el.tabIndex = -1;
    el.style.setProperty('--kit-w', (opts.width || 440) + 'px');
    el.innerHTML = `<header class="kit-sheet-head"><h3 class="kit-sheet-title"></h3><button class="btn ghost icon-sm kit-sheet-x" aria-label="Close" data-tip="Close" data-kbd="esc">${I('x')}</button></header><div class="kit-sheet-body"></div>`;
    const title = $('.kit-sheet-title', el), body = $('.kit-sheet-body', el);
    title.innerHTML = opts.title || ''; el.setAttribute('aria-label', title.textContent);
    put(body, opts.body);
    let foot = null;
    const setFooter = (c) => {
      if (c == null) { if (foot) foot.remove(); foot = null; return; }
      if (!foot) { foot = div('kit-sheet-foot', 'footer'); el.appendChild(foot); }
      foot.innerHTML = ''; put(foot, c); icons();
    };
    setFooter(opts.footer);
    let closed = false;
    const layer = { type: 'sheet', el };
    const close = () => {
      if (closed) return; closed = true; drop(layer);
      if (Kit.beam) Kit.beam(el, false);
      scrim.classList.remove('kit-in'); el.classList.remove('kit-in'); el.classList.add('kit-out');
      setTimeout(() => { scrim.remove(); el.remove(); }, reduced() ? 0 : D.base + 20);
      toastRoom();
      if (opener && opener.focus && opener.isConnected) opener.focus({ preventScroll: true });
      if (typeof opts.onClose === 'function') opts.onClose();
    };
    layer.close = close;
    scrim.addEventListener('click', close);
    el.addEventListener('click', (e) => { if (e.target.closest('.kit-sheet-x')) close(); });
    // loose focus trap: Tab wraps inside the sheet
    el.addEventListener('keydown', (e) => {
      if (e.key !== 'Tab') return;
      const f = $$('button:not([disabled]), [href], input:not([disabled]), select, textarea, [tabindex]:not([tabindex="-1"])', el).filter((x) => x.offsetParent !== null);
      if (!f.length) return;
      const a = document.activeElement;
      if (e.shiftKey && (a === f[0] || a === el)) { e.preventDefault(); f[f.length - 1].focus(); }
      else if (!e.shiftKey && a === f[f.length - 1]) { e.preventDefault(); f[0].focus(); }
    });
    document.body.append(scrim, el);
    layers.push(layer);
    icons();
    void el.offsetWidth; scrim.classList.add('kit-in'); el.classList.add('kit-in');
    toastRoom();
    el.focus({ preventScroll: true });
    return { el, body, get foot() { return foot; }, setFooter, close };
  };

  /* ════════ Menu / combobox ════════ */
  Kit.menu = function (anchor, opts) {
    opts = opts || {};
    const same = layers.find((l) => l.type === 'menu' && l.anchor === anchor);
    if (same) { same.close(); return { close() {} }; }
    layers.filter((l) => l.type === 'menu').forEach((l) => l.close());

    const items = (opts.items || []).map((it, i) => Object.assign({ _i: i }, it));
    const el = div('menu kit-menu');
    el.tabIndex = -1; el.setAttribute('role', 'dialog');
    if (opts.width) el.style.setProperty('--kit-mw', opts.width + 'px');
    el.innerHTML = (opts.search ? `<label class="kit-mq">${I('search')}<input type="text" autocomplete="off" spellcheck="false" role="combobox" aria-expanded="true" aria-autocomplete="list" placeholder="${opts.placeholder || 'Search…'}"></label>` : '') +
      '<div class="kit-mlist" role="listbox"></div>';
    const list = $('.kit-mlist', el), q = $('input', el);
    let shown = [], hl = -1, closed = false;
    const id = 'kit-m' + Math.random().toString(36).slice(2, 7);
    list.id = id;

    const lead = (it) => {
      if (it.method) return method(it.method);
      if (it.icon && METHODS.test(it.icon)) return method(it.icon);
      if (it.dot) return `<span class="dot ${{ ok: 'ok flat', warn: 'warn', err: 'err', idle: 'kit-idle', live: 'm-live' }[it.dot] || it.dot}"></span>`;
      if (it.icon) return I(it.icon);
      return '';
    };
    const render = () => {
      const term = q ? q.value.trim().toLowerCase() : '';
      shown = items.filter((it) => !it.separator && (!term || `${it.label} ${it.sub || ''}`.replace(/<[^>]+>/g, '').toLowerCase().includes(term)));
      let html = '', group = null, prevSep = true;
      const src = term ? shown : items;
      src.forEach((it) => {
        if (it.separator) { if (!prevSep) html += '<div class="kit-mdiv" role="separator"></div>'; prevSep = true; group = null; return; }
        if (it.group && it.group !== group) html += `<div class="kit-mlabel" role="presentation">${it.group}</div>`;
        group = it.group || null; prevSep = false;
        const n = shown.indexOf(it);
        html += `<div class="kit-mi" role="option" id="${id}-${n}" data-n="${n}"${it.checked ? ' aria-selected="true"' : ''}${it.disabled ? ' aria-disabled="true"' : ''}>` +
          `${lead(it)}<span class="kit-mi-label">${it.label}</span>${it.sub ? `<span class="kit-mi-sub">${it.sub}</span>` : ''}` +
          `${it.kbd ? `<span class="kbd">${it.kbd}</span>` : ''}${it.checked ? `<i data-lucide="check" class="kit-mcheck"></i>` : ''}</div>`;
      });
      list.innerHTML = html || '<div class="kit-mnone">No matches</div>';
      icons();
      const c = shown.findIndex((it) => it.checked && !it.disabled);
      setHl(term ? shown.findIndex((it) => !it.disabled) : c >= 0 ? c : shown.findIndex((it) => !it.disabled));
    };
    const setHl = (n) => {
      hl = n;
      $$('.kit-mi', list).forEach((x) => x.classList.toggle('kit-hl', +x.dataset.n === n));
      const cur = $(`[data-n="${n}"]`, list);
      if (cur) { cur.scrollIntoView({ block: 'nearest' }); if (q) q.setAttribute('aria-activedescendant', cur.id); }
    };
    const move = (d) => {
      if (!shown.length) return;
      let n = hl;
      for (let k = 0; k < shown.length; k++) { n = (n + d + shown.length) % shown.length; if (!shown[n].disabled) break; }
      setHl(n);
    };
    const choose = (n) => {
      const it = shown[n]; if (!it || it.disabled) return;
      close(true);
      if (typeof it.onSelect === 'function') it.onSelect(it);
      if (it.href) location.href = it.href;
    };
    const place = () => {
      const r = anchor.getBoundingClientRect(), w = el.offsetWidth, h = el.offsetHeight, gap = 6;
      let left = opts.align === 'end' ? r.right - w : r.left;
      left = Math.min(Math.max(8, left), innerWidth - w - 8);
      const up = r.bottom + gap + h > innerHeight - 8 && r.top - gap - h >= 8;
      el.style.left = Math.round(left) + 'px';
      el.style.top = Math.round(up ? r.top - gap - h : r.bottom + gap) + 'px';
      const ox = Math.min(Math.max(12, r.left + r.width / 2 - left), w - 12);
      el.style.setProperty('--kit-origin', `${Math.round(ox)}px ${up ? '100%' : '0'}`);
    };
    const outside = (e) => { if (!el.contains(e.target) && !anchor.contains(e.target)) close(false); };
    const layer = { type: 'menu', el, anchor };
    function close(restore) {
      if (closed) return; closed = true; drop(layer);
      document.removeEventListener('pointerdown', outside, true);
      removeEventListener('resize', place); removeEventListener('scroll', place, true);
      anchor.setAttribute('aria-expanded', 'false');
      el.classList.remove('kit-in'); el.classList.add('kit-out');
      setTimeout(() => el.remove(), reduced() ? 0 : D.fast);
      if (restore !== false && anchor.focus) anchor.focus({ preventScroll: true });
    }
    layer.close = () => close(false);
    layer.key = (e) => {
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { e.preventDefault(); move(e.key === 'ArrowDown' ? 1 : -1); return true; }
      if (e.key === 'Enter') { e.preventDefault(); choose(hl); return true; }
      if (e.key === 'Escape') { e.preventDefault(); close(true); return true; }
      if (e.key === 'Tab') { close(false); return false; }
      return false;
    };

    list.addEventListener('pointermove', (e) => { const m = e.target.closest('.kit-mi'); if (m && +m.dataset.n !== hl) setHl(+m.dataset.n); });
    list.addEventListener('click', (e) => { const m = e.target.closest('.kit-mi'); if (m) choose(+m.dataset.n); });
    if (q) q.addEventListener('input', render);

    document.body.appendChild(el);
    render(); place();
    layers.push(layer);
    anchor.setAttribute('aria-expanded', 'true'); anchor.setAttribute('aria-haspopup', 'listbox');
    void el.offsetWidth; el.classList.add('kit-in');
    (q || el).focus({ preventScroll: true });
    document.addEventListener('pointerdown', outside, true);
    addEventListener('resize', place); addEventListener('scroll', place, true);
    return { close: () => close(false), el };
  };

  /* ════════ Beam ════════ */
  const beams = new WeakMap();
  const SEG = 8, N = 8; // 8 × 8 px = a 64 px beam, tail (brand-1, faint) to head (brand-2, bright)
  Kit.beam = function (el, on) {
    if (!el) return;
    if (reduced()) {
      if (on === true) el.classList.add('vx-edge');
      else if (on === 'once') { el.classList.add('vx-edge'); setTimeout(() => el.classList.remove('vx-edge'), 1200); }
      else el.classList.remove('vx-edge');
      return;
    }
    const cur = beams.get(el);
    if (!on) { if (cur) stop(el, cur, D.slow); return; }
    if (cur && !cur.dying && (cur.mode === 'loop' || on === 'once')) { if (on === true) cur.mode = 'loop', cur.node.className = 'kit-beam kit-loop'; return; }
    if (cur) stop(el, cur, 0);

    const cs = getComputedStyle(el);
    const b = { mode: on === 'once' ? 'once' : 'loop', pos: null };
    if (cs.position === 'static') { b.pos = el.style.position; el.style.position = 'relative'; }
    const node = (b.node = div('kit-beam ' + (b.mode === 'once' ? 'kit-once' : 'kit-loop'), 'span'));
    node.setAttribute('aria-hidden', 'true');
    const NS = 'http://www.w3.org/2000/svg', svg = document.createElementNS(NS, 'svg');
    const rects = [];
    for (let i = 0; i < N; i++) {
      const r = document.createElementNS(NS, 'rect'), k = i / (N - 1);
      // stacked, not tiled: layer i runs from i×8 px to the head, so alpha builds toward the head with no seams
      r.style.stroke = `color-mix(in srgb, var(--brand-2) ${Math.round(k * 100)}%, var(--brand-1))`;
      r.style.strokeOpacity = '.3';
      r.style.setProperty('--kit-seg', (N - i) * SEG + 'px');
      r.style.setProperty('--kit-off', -i * SEG + 'px');
      svg.appendChild(r); rects.push(r);
    }
    node.appendChild(svg);
    const size = () => {
      const c = getComputedStyle(el), bw = parseFloat(c.borderTopWidth) || 0;
      const w = el.offsetWidth, h = el.offsetHeight, rad = parseFloat(c.borderTopLeftRadius) || 0, s = 0.75;
      node.style.inset = -bw + 'px';
      svg.setAttribute('viewBox', `0 0 ${w} ${h}`);
      rects.forEach((r) => { r.setAttribute('x', s); r.setAttribute('y', s); r.setAttribute('width', Math.max(0, w - 2 * s)); r.setAttribute('height', Math.max(0, h - 2 * s)); r.setAttribute('rx', Math.max(0, rad - s)); });
      let len = 0; try { len = rects[0].getTotalLength(); } catch (e) {}
      if (!len) { const rr = Math.max(0, rad - s), W = w - 2 * s, H = h - 2 * s; len = 2 * (W + H) - 8 * rr + 2 * Math.PI * rr; }
      node.style.setProperty('--kit-len', len.toFixed(2) + 'px');
    };
    el.appendChild(node); size();
    b.ro = window.ResizeObserver ? new ResizeObserver(size) : null; if (b.ro) b.ro.observe(el);
    if (b.mode === 'once') svg.addEventListener('animationend', () => { if (beams.get(el) === b) stop(el, b, D.fast); });
    beams.set(el, b);
  };
  function stop(el, b, fade) {
    if (b.dying) return; b.dying = true;
    if (beams.get(el) === b) beams.delete(el);
    if (b.ro) b.ro.disconnect();
    b.node.style.transitionDuration = fade + 'ms'; b.node.classList.add('kit-fade');
    setTimeout(() => { b.node.remove(); if (b.pos != null && ![...el.children].some((c) => c.classList.contains('kit-beam'))) el.style.position = b.pos; }, fade + 40);
  }

  /* ════════ keyboard: top layer first ════════ */
  document.addEventListener('keydown', (e) => {
    const l = top(); if (!l) return;
    if (l.type === 'menu') { if (l.key(e)) e.stopPropagation(); return; }
    if (l.type === 'sheet' && e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); l.close(); }
  }, true);

  /* ════════════════════════════════════════════════════════════════════
     Wiring: the shell (switcher, Release, Settings) and toasts on page actions.
     All delegated on document, bubble phase, so each page's own handlers run first and stay untouched.
     ════════════════════════════════════════════════════════════════════ */
  const ROUTES = [['GET', '/refunds'], ['POST', '/refunds'], ['GET', '/refunds/stats']];

  function projectMenu(anchor) {
    return Kit.menu(anchor, {
      search: true, placeholder: 'Find a project…', width: 264,
      items: [
        { group: 'Projects', label: 'Payments Ops', dot: 'ok', sub: '3 routes', checked: true, onSelect() {} },
        { group: 'Projects', label: 'Default project', dot: 'idle', sub: 'empty', href: 'projects.html' },
        { separator: true },
        { label: 'New project', icon: 'plus', href: 'projects.html' },
      ],
    });
  }

  /* ── Release: a four-step stepper; the secret gate opens Promote, Promote runs with a beam ── */
  let release = null;
  function releaseSheet() {
    if (release) { release.el.focus(); return release; }
    const step = (n, state, title, inner) =>
      `<li class="kit-step kit-${state}" data-step="${n}"><span class="kit-step-dot">${state === 'done' ? CHECK : n}</span><div class="kit-step-main"><div class="kit-step-title">${title}</div>${inner}</div></li>`;
    const body = `
      <p class="kit-lead">Promote what runs in <span class="mono">dev</span> to <span class="mono">prod</span> as <b style="font-weight:600;color:var(--foreground)">v1</b>. Each step has to pass before the next.</p>
      <div class="well"><ol class="kit-steps">
        ${step(1, 'done', 'Check code', `<div class="kit-step-meta"><span class="mono">refunds.py</span><span class="kit-mid">·</span>no syntax errors<span class="kit-mid">·</span>3 routes</div>`)}
        ${step(2, 'current', 'Secrets <span class="badge warning" data-kit-blocked>blocked</span>',
          `<div class="kit-step-meta kit-warn" data-kit-meta><span class="dot warn"></span><span class="mono">PAYMENTS_TOKEN</span> is missing in prod</div>
           <div class="kit-collapse" data-kit-fixwrap><div><div class="kit-fix">
             <input class="input mono" type="password" placeholder="Paste the prod value" aria-label="PAYMENTS_TOKEN value for prod" autocomplete="off" value="">
             <button class="btn outline sm" data-kit-act="add-secret">${I('plus')} Add to prod</button>
           </div></div></div>`)}
        ${step(3, 'pending', 'Review routes', `<div class="kit-step-meta" data-kit-meta>3 routes change in prod</div><ul class="kit-routes">${ROUTES.map(([m, p]) => `<li>${method(m)}${p}</li>`).join('')}</ul>`)}
        ${step(4, 'pending', 'Promote to prod', `<div class="kit-step-meta" data-kit-meta>v1<span class="kit-mid">·</span>dev → prod<span class="kit-mid">·</span>no downtime</div>`)}
      </ol></div>`;
    const foot = () => `<button class="btn ghost" data-kit-act="cancel">Cancel</button><button class="btn" data-kit-act="promote" disabled>${I('rocket')} Promote to prod</button>`;
    const s = Kit.sheet({ title: 'Release Payments Ops', body, footer: foot(), onClose: () => { release = null; } });
    release = s;
    const el = s.el;
    const setStep = (n, state, meta) => {
      const li = $(`[data-step="${n}"]`, el); if (!li) return;
      li.className = `kit-step kit-${state}`;
      $('.kit-step-dot', li).innerHTML = state === 'done' ? CHECK : n;
      if (meta != null) { const m = $('[data-kit-meta]', li); m.className = 'kit-step-meta'; m.innerHTML = meta; }
    };
    let busy = false;
    el.addEventListener('click', (e) => {
      const b = e.target.closest('[data-kit-act]'); if (!b || b.disabled) return;
      const act = b.dataset.kitAct;
      if (act === 'cancel' || act === 'done') return s.close();
      if (act === 'add-secret') {
        b.disabled = true;
        const blk = $('[data-kit-blocked]', el); if (blk) blk.remove();
        $('[data-kit-fixwrap]', el).classList.add('kit-shut');
        setStep(2, 'done', '<span class="mono">PAYMENTS_TOKEN</span> set in dev and prod');
        setStep(3, 'current', '<span class="dot m-live"></span>Checking 3 routes…');
        Kit.toast({ tone: 'ok', title: '<span class="mono">PAYMENTS_TOKEN</span> added to prod', desc: 'Set in dev and prod. Release is unblocked.' });
        document.dispatchEvent(new CustomEvent('cs:secret-added', { detail: { name: 'PAYMENTS_TOKEN' } }));
        setTimeout(() => {
          if (!el.isConnected) return;
          setStep(3, 'done', '3 routes<span class="kit-mid">·</span>no breaking changes');
          setStep(4, 'current');
          const p = $('[data-kit-act="promote"]', el); if (p) { p.disabled = false; p.focus({ preventScroll: true }); }
        }, 400);
      }
      if (act === 'promote' && !busy) {
        busy = true;
        b.disabled = true; b.innerHTML = 'Promoting…';
        const c = $('[data-kit-act="cancel"]', el); if (c) c.disabled = true;
        setStep(4, 'current', '<span class="dot m-live"></span>Promoting v1 to prod…');
        Kit.beam(el, true);
        const t = Kit.toast({ tone: 'busy', title: 'Promoting to prod…', desc: 'v1 · 3 routes · dev → prod' });
        setTimeout(() => {
          Kit.beam(el, false);
          t.update({ tone: 'ok', title: 'v1 is live in prod', desc: '3 routes promoted · just now', action: { label: 'View run', href: 'runs.html' } });
          if (!el.isConnected) return;
          setStep(4, 'done', 'v1 is live in prod<span class="kit-mid">·</span>just now');
          s.setFooter(`<button class="btn" data-kit-act="done">Done</button>`);
          const d = $('[data-kit-act="done"]', el); if (d) d.focus({ preventScroll: true });
        }, 2200);
      }
    });
    return s;
  }

  /* ── Settings: three static sections in wells ── */
  function settingsSheet() {
    const kv = (k, v, cls = '') => `<div class="kit-kv${cls}"><span class="kit-kv-k">${k}</span><span class="kit-kv-v">${v}</span></div>`;
    const sec = (h, sub) => `<div class="kit-sec"><h4>${h}</h4>${sub ? `<span>${sub}</span>` : ''}</div>`;
    const body = `
      ${sec('General')}
      <div class="well">
        ${kv('Project name', 'Payments Ops')}
        ${kv('Slug', '<span class="mono">payments-ops</span><span class="muted kit-end">/api/p/payments-ops</span>')}
        ${kv('Runtime', 'Python 3.12')}
      </div>
      ${sec('Environments', '2')}
      <div class="well">
        ${kv('<span class="mono">dev</span>', '<span class="dot ok flat"></span>Default<span class="muted kit-end">1 secret</span>')}
        ${kv('<span class="mono">prod</span>', '<span class="dot warn"></span>No release yet<span class="muted kit-end warn-text">1 secret missing</span>')}
      </div>
      ${sec('Danger zone')}
      <div class="well">
        ${kv('Delete project', '<span class="muted">Can’t be undone.</span><button class="btn destructive sm kit-end" disabled title="Static preview">Delete…</button>', ' kit-danger')}
      </div>`;
    return Kit.sheet({ title: 'Settings', width: 420, body });
  }

  const text = (el) => (el.textContent || '').replace(/\s+/g, ' ').trim();
  document.addEventListener('click', (e) => {
    const t = e.target;
    if (!(t instanceof Element)) return;
    const b = t.closest('button, a, [role="button"]');
    if (!b) return;

    // shell
    if (b.matches('[data-kit="switcher"], .topbar .switcher')) { e.preventDefault(); projectMenu(b); return; }
    if (b.matches('[data-kit="release"]') || (b.matches('.topbar .btn') && text(b) === 'Release') || (b.tagName === 'BUTTON' && text(b) === 'Create release')) { releaseSheet(); return; }
    if (b.matches('[data-kit="settings"]') || (b.matches('.outline-bottom .item') && text(b) === 'Settings')) { settingsSheet(); return; }

    // page actions: toasts after the page's own handler has run
    if (b.matches('.main .url')) { Kit.toast({ tone: 'ok', title: 'Copied URL', desc: '<span class="mono">commandstudio.dev/api/p/payments-ops</span>' }); return; }
    if (b.matches('#add-row, #eot-add')) { Kit.toast({ tone: 'ok', title: 'Row added to <span class="mono">refunds</span>', desc: 'Added at the top of the table.' }); return; }
    if (b.matches('#refresh') && $('#runs-badge')) { Kit.toast({ tone: 'ok', title: 'Runs up to date', desc: 'No new runs since the last refresh.' }); return; }
    if (b.matches('#al-fix')) { Kit.toast({ tone: 'ok', title: '<span class="mono">PAYMENTS_TOKEN</span> added to prod', desc: 'Payments Ops is ready to release.' }); return; }
  });

  /* keyboard: ⇧R opens Release, ⌘P / Ctrl+P the project switcher (the shell's tooltips advertise both) */
  document.addEventListener('keydown', (e) => {
    if (layers.length || e.defaultPrevented) return;
    const a = document.activeElement;
    if (a && (a.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(a.tagName))) return;
    if (e.shiftKey && !e.metaKey && !e.ctrlKey && !e.altKey && e.key.toLowerCase() === 'r') {
      if ($('[data-kit="release"]')) { e.preventDefault(); releaseSheet(); }
    } else if ((e.metaKey || e.ctrlKey) && !e.shiftKey && e.key.toLowerCase() === 'p') {
      const sw = $('[data-kit="switcher"]'); if (sw) { e.preventDefault(); projectMenu(sw); }
    }
  });

  Kit.open = { release: releaseSheet, settings: settingsSheet, switcher: () => { const sw = $('[data-kit="switcher"], .topbar .switcher'); return sw && projectMenu(sw); } };
})();

/* Shared reaction to a secret being added anywhere: the sidebar stops flagging it. */
document.addEventListener('cs:secret-added', () => {
  document.querySelectorAll('.sidebar .item').forEach((it) => {
    if (!/Secrets/.test(it.textContent)) return;
    const t = it.querySelector('.tail'); if (!t || t.dataset.fixed) return;
    t.dataset.fixed = 1; t.innerHTML = '<span class="dot ok flat"></span>all set'; t.style.color = 'var(--success-text)';
  });
});
