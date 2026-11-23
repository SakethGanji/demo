/* Custom vector art drawn from the project's own data. Precise line work with one brand accent;
   every colour is a theme token, so it works in light and dark. Fills any [data-art] placeholder. */
(function () {
  const v = (n) => `var(--${n})`;
  const defs = (id) => `
    <defs>
      <linearGradient id="${id}-brand" x1="0" y1="0" x2="1" y2="1"><stop offset="0" style="stop-color:${v('brand-1')}"/><stop offset="1" style="stop-color:${v('brand-2')}"/></linearGradient>
      <linearGradient id="${id}-fade" x1="0" y1="0" x2="0" y2="1"><stop offset="0" style="stop-color:${v('brand-1')};stop-opacity:.28"/><stop offset="1" style="stop-color:${v('brand-1')};stop-opacity:0"/></linearGradient>
      <radialGradient id="${id}-glow" cx=".5" cy=".5" r=".5"><stop offset="0" style="stop-color:${v('brand-1')};stop-opacity:.22"/><stop offset="1" style="stop-color:${v('brand-1')};stop-opacity:0"/></radialGradient>
      <pattern id="${id}-dots" width="12" height="12" patternUnits="userSpaceOnUse"><circle cx="1" cy="1" r=".9" style="fill:${v('art-faint')}"/></pattern>
      <radialGradient id="${id}-mask-g" cx=".55" cy=".5" r=".6"><stop offset=".3" stop-color="#fff"/><stop offset="1" stop-color="#000"/></radialGradient>
      <mask id="${id}-mask"><rect width="100%" height="100%" fill="url(#${id}-mask-g)"/></mask>
    </defs>`;
  const chip = (x, y, m, mc, path) => `
    <g transform="translate(${x} ${y})">
      <rect width="150" height="30" rx="8" class="a-card"/>
      <text x="12" y="19.5" class="a-mono a-strong" style="fill:${v('method-' + mc)}">${m}</text>
      <text x="${m.length > 3 ? 48 : 42}" y="19.5" class="a-mono">${path}</text>
    </g>`;

  function system() {
    const id = 'sys';
    return `<svg viewBox="0 0 540 172" class="art" role="img" aria-label="Three routes in refunds.py read and write the refunds table, which feeds the Refunds dashboard app. PAYMENTS_TOKEN is missing in prod.">
      ${defs(id)}
      <rect width="540" height="172" fill="url(#${id}-dots)" mask="url(#${id}-mask)"/>
      <ellipse cx="300" cy="74" rx="120" ry="70" fill="url(#${id}-glow)"/>
      <!-- wires -->
      <path id="w1" data-wire="get" d="M170 39 C 220 39, 225 62, 262 66" class="a-wire"/>
      <path id="w2" data-wire="post" d="M170 83 C 215 83, 230 76, 262 75" class="a-wire a-hot"/>
      <path id="w3" data-wire="stats" d="M170 127 C 220 127, 225 90, 262 84" class="a-wire a-hot"/>
      <path id="w4" data-wire="app" d="M338 75 C 360 75, 362 82, 384 82" class="a-wire a-hot"/>
      <path id="w5" data-wire="secret" d="M150 142 C 150 158, 196 160, 222 160" class="a-wire a-warn"/>
      <!-- routes -->
      <text x="20" y="16" class="a-label">refunds.py</text>
      ${chip(20, 24, 'GET', 'get', '/refunds')}${chip(20, 68, 'POST', 'post', '/refunds')}${chip(20, 112, 'GET', 'get', '/refunds/stats')}
      <!-- table -->
      <g transform="translate(262 40)">
        <path d="M0 10 v44 a38 10 0 0 0 76 0 v-44" style="fill:${v('card')}" class="a-stroke-brand"/>
        <path d="M0 10 v44 a38 10 0 0 0 76 0 v-44" fill="url(#${id}-fade)"/>
        <path d="M0 32 a38 10 0 0 0 76 0" class="a-stroke-brand" fill="none" opacity=".5"/>
        <ellipse cx="38" cy="10" rx="38" ry="10" style="fill:${v('card')}" class="a-stroke-brand"/>
        <ellipse cx="38" cy="10" rx="38" ry="10" fill="url(#${id}-fade)"/>
        <text x="38" y="84" text-anchor="middle" class="a-mono a-strong">refunds</text>
        <text x="38" y="97" text-anchor="middle" class="a-label">3 rows</text>
      </g>
      <!-- app -->
      <g transform="translate(384 30)">
        <rect width="136" height="104" rx="10" class="a-card"/>
        <path d="M0 10 a10 10 0 0 1 10 -10 h116 a10 10 0 0 1 10 10 v8 h-136z" style="fill:${v('muted')}"/>
        <circle cx="10" cy="9" r="2.2" class="a-dot"/><circle cx="17" cy="9" r="2.2" class="a-dot"/><circle cx="24" cy="9" r="2.2" class="a-dot"/>
        <rect x="10" y="26" width="34" height="18" rx="4" class="a-tile"/><rect x="51" y="26" width="34" height="18" rx="4" class="a-tile"/><rect x="92" y="26" width="34" height="18" rx="4" class="a-tile"/>
        <rect x="14" y="31" width="14" height="3" rx="1.5" class="a-bar"/><rect x="14" y="37" width="22" height="3" rx="1.5" style="fill:url(#${id}-brand)"/>
        <path d="M10 92 L28 80 L44 84 L62 68 L80 74 L98 58 L126 62 L126 96 L10 96Z" fill="url(#${id}-fade)"/>
        <path d="M10 92 L28 80 L44 84 L62 68 L80 74 L98 58 L126 62" fill="none" style="stroke:url(#${id}-brand)" stroke-width="1.8" stroke-linejoin="round" stroke-linecap="round"/>
        <circle cx="126" cy="62" r="3" style="fill:${v('brand-2')}"/>
        <text x="68" y="122" text-anchor="middle" class="a-label">Refunds dashboard</text>
      </g>
      <!-- secret -->
      <g transform="translate(222 150)" data-secret-pill>
        <rect width="168" height="20" rx="10" class="a-warn-pill"/>
        <circle cx="11" cy="10" r="3" style="fill:${v('warning')}"/>
        <text x="20" y="14" class="a-mono" style="fill:${v('warning-text')}">PAYMENTS_TOKEN · prod</text>
      </g>
    </svg>`;
  }

  function app() {
    const id = 'thumb';
    return `<svg viewBox="0 0 96 60" class="art" role="img" aria-label="Refunds dashboard preview">${defs(id)}
      <rect x=".5" y=".5" width="95" height="59" rx="7" class="a-card"/>
      <path d="M.5 7.5 a7 7 0 0 1 7 -7 h81 a7 7 0 0 1 7 7 v3 h-95z" style="fill:${v('muted')}"/>
      <circle cx="6" cy="6" r="1.4" class="a-dot"/><circle cx="10.5" cy="6" r="1.4" class="a-dot"/><circle cx="15" cy="6" r="1.4" class="a-dot"/>
      <rect x="6" y="15" width="25" height="11" rx="2.5" class="a-tile"/><rect x="35.5" y="15" width="25" height="11" rx="2.5" class="a-tile"/><rect x="65" y="15" width="25" height="11" rx="2.5" class="a-tile"/>
      <rect x="9" y="19" width="12" height="2.5" rx="1.2" style="fill:url(#${id}-brand)"/>
      <path d="M6 53 L18 46 L28 48 L40 39 L52 43 L64 34 L76 37 L90 31 L90 55 L6 55Z" fill="url(#${id}-fade)"/>
      <path d="M6 53 L18 46 L28 48 L40 39 L52 43 L64 34 L76 37 L90 31" fill="none" style="stroke:url(#${id}-brand)" stroke-width="1.4" stroke-linejoin="round" stroke-linecap="round"/>
    </svg>`;
  }

  function release() {
    const id = 'rel';
    return `<svg viewBox="0 0 300 58" class="art" role="img" aria-label="dev is live with the latest saved code. prod has no release yet and one blocker.">${defs(id)}
      <path d="M22 22 H278" class="a-wire" stroke-dasharray="3 5"/>
      <path d="M22 22 H120" style="stroke:url(#${id}-brand)" stroke-width="2" stroke-linecap="round"/>
      <circle cx="22" cy="22" r="9" style="fill:${v('brand-1')};fill-opacity:.14"/><circle cx="22" cy="22" r="5" style="fill:url(#${id}-brand)"/>
      <g transform="translate(150 22)"><circle r="11" style="fill:${v('card')};stroke:${v('warning')}" stroke-width="1.5"/>
        <path d="M-2.5 2.5 l5 -5 M1 -4 a2.6 2.6 0 1 1 3 3" style="stroke:${v('warning')}" stroke-width="1.5" fill="none" stroke-linecap="round"/></g>
      <circle cx="278" cy="22" r="6" style="fill:${v('card')};stroke:${v('art-line')}" stroke-width="1.5"/>
      <text x="22" y="50" text-anchor="start" class="a-mono a-strong">dev</text><text x="46" y="50" class="a-label">latest code</text>
      <text x="150" y="50" text-anchor="middle" class="a-label" style="fill:${v('warning-text')}">1 blocker</text>
      <text x="222" y="50" class="a-mono a-strong">prod</text><text x="252" y="50" class="a-label">no release</text>
    </svg>`;
  }

  /* ── Packets are events, not decoration: one packet per real event, wires stay still when idle ──
     LunaArt.pulse(pathOrId, { color: 'brand' | 'warn' | 'secret', delay }) sends one packet along an SVG path.
     'secret' travels amber -> green (a secret was just set). rAF + getPointAtLength: reliable on any SVG path. */
  const css = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
  const ms = (n, d) => parseFloat(css(n)) || d;
  const reduced = () => matchMedia('(prefers-reduced-motion: reduce)').matches;
  function bezier(str) { // "cubic-bezier(a,b,c,d)" -> easing fn
    const m = /cubic-bezier\(([^)]+)\)/.exec(str || ''); const [x1, y1, x2, y2] = m ? m[1].split(',').map(Number) : [.65, 0, .35, 1];
    const B = (t, a, b) => 3 * a * t * (1 - t) * (1 - t) + 3 * b * t * t * (1 - t) + t * t * t;
    return (x) => { let lo = 0, hi = 1, t = x; for (let i = 0; i < 24; i++) { t = (lo + hi) / 2; if (B(t, x1, x2) < x) lo = t; else hi = t; } return B(t, y1, y2); };
  }
  const last = new WeakMap(), GAP = 280;
  const FILL = { brand: 'var(--brand-1)', warn: 'var(--warning)' };
  const GLOW = { brand: 'drop-shadow(0 0 2.5px var(--brand-1)) drop-shadow(0 0 6px var(--glow))',
    warn: 'drop-shadow(0 0 2.5px var(--warning)) drop-shadow(0 0 6px color-mix(in srgb, var(--warning) 30%, transparent))' };
  const mixAt = (u) => `color-mix(in srgb, var(--success) ${Math.round(u * 100)}%, var(--warning))`;
  function pulse(target, opts = {}) {
    const path = typeof target === 'string' ? document.getElementById(target) : target;
    if (!path || !path.getTotalLength) return;
    if (opts.delay) { setTimeout(() => pulse(path, { ...opts, delay: 0 }), opts.delay); return; }
    const now = performance.now(); if (now - (last.get(path) || -1e9) < GAP) return; last.set(path, now);
    const color = opts.color || 'brand';
    if (reduced()) { // no travel: the wire flashes once
      const to = getComputedStyle(path).stroke, from = color === 'brand' ? css('--brand-1') : color === 'warn' ? css('--warning') : css('--success');
      path.animate([{ stroke: from }, { stroke: to }], { duration: ms('--d-base', 200), easing: css('--ease-out') || 'ease-out' }); return;
    }
    const NS = 'http://www.w3.org/2000/svg', dot = document.createElementNS(NS, 'circle');
    const len = path.getTotalLength(), D = (opts.duration || ms('--d-slow', 280) * 2), ease = bezier(css('--ease-io'));
    dot.setAttribute('r', opts.r || 2.6); dot.setAttribute('class', 'a-pulse'); dot.setAttribute('aria-hidden', 'true');
    dot.style.fill = FILL[color] || mixAt(0); dot.style.filter = GLOW[color === 'warn' ? 'warn' : 'brand'];
    path.parentNode.insertBefore(dot, path.nextSibling);
    const t0 = performance.now(); setTimeout(() => dot.remove(), D + 120); // never lingers, even if frames stall
    (function tick(t) {
      const x = Math.min(1, (t - t0) / D), u = ease(x), pt = path.getPointAtLength(u * len);
      dot.setAttribute('cx', pt.x); dot.setAttribute('cy', pt.y);
      dot.style.opacity = x < .12 ? x / .12 : x > .88 ? (1 - x) / .12 : 1;
      if (color === 'secret') { dot.style.fill = mixAt(u); dot.style.filter = `drop-shadow(0 0 2.5px ${mixAt(u)})`; }
      if (x < 1 && dot.isConnected) requestAnimationFrame(tick); else dot.remove();
    })(t0);
  }
  /* named wires: LunaArt.pulseWire('system', 'post') pulses every [data-wire="post"] inside [data-art="system"] */
  function pulseWire(art, wire, opts) { document.querySelectorAll(`[data-art="${art}"] [data-wire="${wire}"]`).forEach((p) => pulse(p, opts)); }

  /* a secret was added: its dashed amber wire turns solid, with one amber -> green packet */
  function secretAdded(name) {
    if (name !== 'PAYMENTS_TOKEN') return;
    document.querySelectorAll('[data-wire="secret"]:not(.a-solid)').forEach((p) => { p.classList.add('a-solid'); pulse(p, { color: 'secret' }); });
    document.querySelectorAll('[data-secret-pill]:not(.a-set)').forEach((g) => g.classList.add('a-set'));
  }
  document.addEventListener('cs:secret-added', (e) => secretAdded(e.detail && e.detail.name));
  const st = document.createElement('style');
  st.textContent = `.art .a-pulse{pointer-events:none}
.art .a-wire[data-wire="secret"],.art [data-secret-pill] *{transition:stroke var(--d-slow) var(--ease-out),stroke-dasharray var(--d-slow) var(--ease-out),fill var(--d-slow) var(--ease-out),opacity var(--d-slow) var(--ease-out)}
.art .a-wire.a-solid{stroke-dasharray:none;stroke:color-mix(in srgb,var(--success) 70%,transparent);opacity:1}
.art [data-secret-pill].a-set rect{fill:color-mix(in srgb,var(--success) 9%,var(--card));stroke:color-mix(in srgb,var(--success) 40%,transparent)}
.art [data-secret-pill].a-set circle{fill:var(--success)!important}.art [data-secret-pill].a-set text{fill:var(--success-text)!important}`;
  document.head.appendChild(st);

  /* the overview hero: the hot wires each carry one packet when the page arrives, then go still */
  function hello(el) { if (el.dataset.art !== 'system') return; ['post', 'stats'].forEach((w, i) => pulseWire('system', w, { delay: 500 + i * 160 })); pulseWire('system', 'app', { delay: 500 + 560 + 160 }); }

  const ART = { system, app, release };
  /* pages can register their own drawings: LunaArt.add('name', fn) before DOMContentLoaded, or LunaArt.draw() after */
  window.LunaArt = { defs, chip, v, add: (k, f) => { ART[k] = f; }, draw: () => fill(), pulse, pulseWire, secretAdded };
  function fill() { document.querySelectorAll('[data-art]').forEach((el) => { if (!el.dataset.drawn && ART[el.dataset.art]) { el.innerHTML = ART[el.dataset.art](); el.dataset.drawn = 1; hello(el); } }); }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => setTimeout(fill)); else setTimeout(fill);
})();
