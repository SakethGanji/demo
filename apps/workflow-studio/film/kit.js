/* film2 kit.js — the film runtime. See FRAMEWORK.md for the slide API.
 *
 *   Film.slide(def)           register a slide (slides/NN-name.js call this)
 *   window.film               the landing contract: { scenes, play(i), rest(i), quiet(on), theme(dark), pointer(x, y) }
 *
 * Everything that moves is a pure function of t (seconds since the scene's play started): slides
 * register tweens on a Timeline, and the kit evaluates it per frame, so rest(i) and seeking are exact.
 * Transitions (old scene flies forward through the camera, glass gears turn past, the new scene comes in
 * from depth and settles) are tweens on the same clock. No libraries. No WebGL.
 */
(function () {
  'use strict'
  const W = 1600, H = 900
  const qs = new URLSearchParams(location.search)
  const mq = window.matchMedia ? matchMedia('(prefers-reduced-motion: reduce)') : { matches: false }
  let still = mq.matches || qs.get('still') === '1'

  /* ───────────── easing ───────────── */
  function bezier(x1, y1, x2, y2) {
    const B = (a, b, s) => 3 * a * (1 - s) * (1 - s) * s + 3 * b * (1 - s) * s * s + s * s * s
    return (t) => {
      if (t <= 0) return 0
      if (t >= 1) return 1
      let lo = 0, hi = 1, s = t
      for (let i = 0; i < 22; i++) { s = (lo + hi) / 2; if (B(x1, x2, s) < t) lo = s; else hi = s }
      return B(y1, y2, s)
    }
  }
  const EASE = {
    linear: (t) => Math.min(1, Math.max(0, t)),
    out: bezier(0.22, 1, 0.36, 1),     /* the c6f429f EASE: entrances, settles */
    io: bezier(0.65, 0, 0.35, 1),      /* moves between two rest states */
    in: bezier(0.4, 0, 1, 1),          /* exits */
    spring: bezier(0.3, 1.3, 0.5, 1),  /* small overshoot: pops, presses */
    pop: bezier(0.34, 1.56, 0.64, 1),  /* bigger overshoot: a node landing */
    soft: bezier(0.16, 1, 0.3, 1),     /* long, soft settle (the hero tilt) */
  }
  const ease = (e) => (typeof e === 'function' ? e : EASE[e] || EASE.out)
  const clamp01 = (v) => (v < 0 ? 0 : v > 1 ? 1 : v)
  const lerp = (a, b, p) => a + (b - a) * p

  /* ───────────── timeline ───────────── */
  const TF = ['x', 'y', 'z', 'xp', 'yp', 'rx', 'ry', 'rz', 's', 'sx', 'sy']
  const BASE = { opacity: 1, x: 0, y: 0, z: 0, xp: 0, yp: 0, rx: 0, ry: 0, rz: 0, s: 1, sx: 1, sy: 1, clip: 1, clipy: 1, draw: 1, w: 100, h: 100, blur: 0 }
  function toEls(el) {
    if (!el) return []
    if (typeof el === 'string') throw new Error('Timeline: pass elements, not selectors')
    if (el instanceof Element) return [el]
    return Array.from(el)
  }
  class Timeline {
    constructor() { this.tw = []; this.fns = []; this.end = 0; this.map = null }
    /** tween props on el(s): props = { opacity: [0, 1], y: [18, 0], ... }; at/dur in seconds */
    add(el, props, at = 0, dur = 0.7, e = 'out') {
      for (const node of toEls(el)) for (const k in props) {
        const [a, b] = props[k]
        this.tw.push({ el: node, k, a, b, at, dur: Math.max(0, dur), e: ease(e) })
        if (k === 'draw') prepDraw(node)
      }
      this.end = Math.max(this.end, at + dur); this.map = null; return this
    }
    /** like add, but each element starts `stagger` later than the one before */
    stagger(els, props, at = 0, stagger = 0.06, dur = 0.7, e = 'out') {
      toEls(els).forEach((el, i) => this.add(el, props, at + i * stagger, dur, e)); return this
    }
    /** a custom tween: f(p, t) with p eased 0..1 (0 before `at`, 1 after at+dur). Called every frame. */
    fn(at, dur, f, e = 'linear') { this.fns.push({ at, dur, f, e: ease(e) }); this.fns.sort((a, b) => a.at - b.at); this.end = Math.max(this.end, at + dur); return this }
    /** a discrete switch: f(on) with on = t >= at */
    step(at, f) { return this.fn(at, 0, (p) => f(p >= 1)) }
    /** toggle a class on el(s) at `at` (and off again at `until`, if given) */
    cls(el, name, at, until) {
      const els = toEls(el)
      return this.fn(at, 0, (p, t) => { const on = t >= at && (until == null || t < until); els.forEach((n) => n.classList.toggle(name, on)) })
    }
    compile() {
      const m = new Map()
      for (const tw of this.tw) {
        let e = m.get(tw.el); if (!e) m.set(tw.el, (e = {}))
        ;(e[tw.k] || (e[tw.k] = [])).push(tw)
      }
      for (const e of m.values()) for (const k in e) e[k].sort((a, b) => a.at - b.at)
      this.map = m
    }
    eval(t) {
      if (!this.map) this.compile()
      for (const [el, props] of this.map) {
        const v = {}
        for (const k in props) {
          const list = props[k]
          let cur = null
          for (const tw of list) { if (tw.at <= t) cur = tw; else break }
          if (!cur) v[k] = list[0].a
          else { const p = cur.dur ? cur.e(clamp01((t - cur.at) / cur.dur)) : 1; v[k] = lerp(cur.a, cur.b, p) }
        }
        apply(el, v)
      }
      for (const f of this.fns) {
        const p = f.dur ? f.e(clamp01((t - f.at) / f.dur)) : t >= f.at ? 1 : 0
        f.f(p, t)
      }
    }
  }
  function prepDraw(el) {
    if (el.__draw) return
    el.__draw = 1
    el.setAttribute('pathLength', '1')
    el.style.strokeDasharray = '1 1'
  }
  function apply(el, v) {
    const st = el.style
    if ('opacity' in v) st.opacity = String(+v.opacity.toFixed(4))
    let hasT = false
    for (const k of TF) if (k in v) { hasT = true; break }
    if (hasT) {
      const g = (k) => (k in v ? v[k] : BASE[k])
      let s = `translate3d(${g('x').toFixed(2)}px,${g('y').toFixed(2)}px,${g('z').toFixed(2)}px)`
      if ('xp' in v || 'yp' in v) s += ` translate(${g('xp').toFixed(2)}%,${g('yp').toFixed(2)}%)`
      if ('rx' in v) s += ` rotateX(${g('rx').toFixed(3)}deg)`
      if ('ry' in v) s += ` rotateY(${g('ry').toFixed(3)}deg)`
      if ('rz' in v) s += ` rotateZ(${g('rz').toFixed(3)}deg)`
      const sc = g('s')
      s += ` scale(${(sc * g('sx')).toFixed(4)},${(sc * g('sy')).toFixed(4)})`
      st.transform = s
    }
    if ('clip' in v) st.clipPath = `inset(-2px ${((1 - v.clip) * 100).toFixed(2)}% -2px -2px)`
    if ('clipy' in v) st.clipPath = `inset(-2px -2px ${((1 - v.clipy) * 100).toFixed(2)}% -2px)`
    if ('draw' in v) st.strokeDashoffset = String((1 - v.draw).toFixed(4))
    if ('w' in v) st.width = v.w.toFixed(2) + '%'
    if ('h' in v) st.height = v.h.toFixed(2) + '%'
    if ('blur' in v) st.filter = v.blur > 0.05 ? `blur(${v.blur.toFixed(2)}px)` : 'none'
    for (const k in v) if (k.startsWith('--')) st.setProperty(k, String(+v[k].toFixed(4)))
  }

  /* ───────────── DOM helpers ───────────── */
  /** h('div.k-well.foo', {style, attrs...}, ...children) — children: nodes, strings, arrays */
  function h(sel, attrs, ...kids) {
    const m = sel.split('.')
    const el = document.createElement(m[0] || 'div')
    if (m.length > 1) el.className = m.slice(1).join(' ')
    if (attrs && (typeof attrs !== 'object' || attrs instanceof Node || Array.isArray(attrs))) { kids.unshift(attrs); attrs = null }
    if (attrs) for (const k in attrs) {
      const val = attrs[k]
      if (val == null || val === false) continue
      if (k === 'style' && typeof val === 'object') Object.assign(el.style, val)
      else if (k === 'html') el.innerHTML = val
      else if (k === 'text') el.textContent = val
      else if (k.startsWith('--')) el.style.setProperty(k, val)
      else el.setAttribute(k, val === true ? '' : val)
    }
    append(el, kids)
    return el
  }
  function append(el, kids) {
    for (const c of kids.flat(Infinity)) {
      if (c == null || c === false) continue
      el.appendChild(c instanceof Node ? c : document.createTextNode(String(c)))
    }
    return el
  }
  /** parse an html string into a single element (or a fragment if several) */
  function html(str) {
    const t = document.createElement('template'); t.innerHTML = str.trim()
    return t.content.childNodes.length === 1 ? t.content.firstChild : t.content
  }

  /* ───────────── icons (lucide-style, 24 grid, stroke) ───────────── */
  const IC = {
    clock: '<circle cx="12" cy="12" r="9.5"/><path d="M12 7v5l3.2 2"/>',
    calendar: '<rect x="3" y="4.5" width="18" height="16.5" rx="2.5"/><path d="M16 2.5v4M8 2.5v4M3 10h18"/>',
    code: '<path d="m16 18 6-6-6-6M8 6l-6 6 6 6"/>',
    terminal: '<path d="m4 17 6-6-6-6M12 19h8"/>',
    database: '<ellipse cx="12" cy="5" rx="8.5" ry="3"/><path d="M3.5 5v14c0 1.7 3.8 3 8.5 3s8.5-1.3 8.5-3V5"/><path d="M3.5 12c0 1.7 3.8 3 8.5 3s8.5-1.3 8.5-3"/>',
    filter: '<path d="M22 3H2l8 9.5V19l4 2v-8.5z"/>',
    branch: '<circle cx="6" cy="18" r="2.6"/><circle cx="6" cy="6" r="2.6"/><circle cx="18" cy="8" r="2.6"/><path d="M6 8.6v6.8M18 10.6c0 4-5 4.4-9.6 6"/>',
    merge: '<circle cx="18" cy="18" r="2.6"/><circle cx="6" cy="6" r="2.6"/><path d="M6 21V8.6M6 9a9 9 0 0 0 9.4 9"/>',
    repeat: '<path d="m17 2 4 4-4 4"/><path d="M3 11V9a3 3 0 0 1 3-3h15"/><path d="m7 22-4-4 4-4"/><path d="M21 13v2a3 3 0 0 1-3 3H3"/>',
    layers: '<path d="m12 2 10 5-10 5L2 7z"/><path d="m2 17 10 5 10-5"/><path d="m2 12 10 5 10-5"/>',
    user: '<circle cx="12" cy="7.5" r="4"/><path d="M4.5 21a7.5 7.5 0 0 1 15 0"/>',
    userCheck: '<circle cx="9" cy="7.5" r="4"/><path d="M2 21a7 7 0 0 1 14 0"/><path d="m16 11 2 2 4-4"/>',
    users: '<circle cx="9" cy="7.5" r="4"/><path d="M2 21a7 7 0 0 1 14 0"/><path d="M16 3.3a4 4 0 0 1 0 8.4M22 21a7 7 0 0 0-4-6.3"/>',
    check: '<path d="M20 6 9 17l-5-5"/>',
    checkCircle: '<circle cx="12" cy="12" r="9.5"/><path d="m8 12 2.8 2.8L16.5 9"/>',
    x: '<path d="M18 6 6 18M6 6l12 12"/>',
    xCircle: '<circle cx="12" cy="12" r="9.5"/><path d="m15 9-6 6M9 9l6 6"/>',
    zap: '<path d="M13 2 3 14h9l-1 8 10-12h-9z"/>',
    webhook: '<path d="M18 16.98h-5.99c-1.1 0-1.95.94-2.48 1.9A4 4 0 0 1 2 17c.01-.7.2-1.4.57-2"/><path d="m6 17 3.13-5.78c.53-.97.1-2.18-.5-3.1a4 4 0 1 1 6.89-4.06"/><path d="m12 6 3.13 5.73C15.66 12.7 16.9 13 18 13a4 4 0 0 1 0 8"/>',
    form: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6M8 13h8M8 17h5"/>',
    file: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/>',
    globe: '<circle cx="12" cy="12" r="9.5"/><path d="M2.5 12h19M12 2.5a14.5 14.5 0 0 1 0 19M12 2.5a14.5 14.5 0 0 0 0 19"/>',
    mail: '<rect x="2.5" y="4.5" width="19" height="15" rx="2.5"/><path d="m3 7 9 6 9-6"/>',
    send: '<path d="M22 2 11 13"/><path d="M22 2 15 22l-4-9-9-4z"/>',
    sparkles: '<path d="M11 3.5 12.9 8.6 18 10.5l-5.1 1.9L11 17.5l-1.9-5.1L4 10.5l5.1-1.9z"/><path d="M19 2.5v4M17 4.5h4M18.5 16v3M17 17.5h3"/>',
    lock: '<rect x="4" y="11" width="16" height="10.5" rx="2.5"/><path d="M8 11V7.5a4 4 0 0 1 8 0V11"/>',
    unlock: '<rect x="4" y="11" width="16" height="10.5" rx="2.5"/><path d="M8 11V7.5a4 4 0 0 1 7.7-1.6"/>',
    shield: '<path d="M12 22s8-3.6 8-10V5.2L12 2 4 5.2V12c0 6.4 8 10 8 10z"/>',
    shieldCheck: '<path d="M12 22s8-3.6 8-10V5.2L12 2 4 5.2V12c0 6.4 8 10 8 10z"/><path d="m8.8 12 2.2 2.2 4.4-4.4"/>',
    key: '<circle cx="7.5" cy="15.5" r="4.5"/><path d="m10.7 12.3 9.8-9.8M17 6l3 3M14.5 8.5l2 2"/>',
    box: '<path d="M21 16V8a2 2 0 0 0-1-1.7l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.7l7 4a2 2 0 0 0 2 0l7-4a2 2 0 0 0 1-1.7z"/><path d="M3.3 7 12 12l8.7-5M12 22V12"/>',
    cpu: '<rect x="4.5" y="4.5" width="15" height="15" rx="2"/><rect x="9" y="9" width="6" height="6" rx="1"/><path d="M9 1.5v3M15 1.5v3M9 19.5v3M15 19.5v3M19.5 9h3M19.5 15h3M1.5 9h3M1.5 15h3"/>',
    table: '<rect x="3" y="3" width="18" height="18" rx="2.5"/><path d="M3 9h18M3 15h18M9 3v18"/>',
    chart: '<path d="M3 3v18h18"/><path d="M8 17v-4M13 17V7M18 17v-7"/>',
    trend: '<path d="m3 17 6-6 4 4 8-8"/><path d="M15 7h6v6"/>',
    pie: '<path d="M21.2 15.9A10 10 0 1 1 8 2.8"/><path d="M22 12A10 10 0 0 0 12 2v10z"/>',
    play: '<path d="M6.5 3.5v17l14-8.5z"/>',
    pause: '<rect x="6" y="4" width="4" height="16" rx="1"/><rect x="14" y="4" width="4" height="16" rx="1"/>',
    search: '<circle cx="11" cy="11" r="7.5"/><path d="m21 21-4.6-4.6"/>',
    eye: '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
    eyeOff: '<path d="M9.9 4.24A9.6 9.6 0 0 1 12 4c6.5 0 10 8 10 8a17 17 0 0 1-2.2 3.2M6.6 6.6A17 17 0 0 0 2 12s3.5 7 10 7a9.4 9.4 0 0 0 5.4-1.6"/><path d="M9.9 9.9a3 3 0 0 0 4.2 4.2M2 2l20 20"/>',
    flask: '<path d="M9 3h6M10 3v6.5L4.4 19.2A1.5 1.5 0 0 0 5.7 21.5h12.6a1.5 1.5 0 0 0 1.3-2.3L14 9.5V3"/><path d="M7 15h10"/>',
    browser: '<rect x="2.5" y="3.5" width="19" height="17" rx="2.5"/><path d="M2.5 8.5h19M6 6h.01M8.5 6h.01"/>',
    cursor: '<path d="m4 4 7 17 2.5-7.5L21 11z"/>',
    hand: '<path d="M18 11V6a2 2 0 0 0-4 0v5M14 10V4a2 2 0 0 0-4 0v6M10 10.5V6a2 2 0 0 0-4 0v8"/><path d="M18 8a2 2 0 1 1 4 0v6a8 8 0 0 1-8 8h-2c-2.8 0-4.5-.9-6-2.4l-3.6-3.6a2 2 0 0 1 2.8-2.8L7 15"/>',
    arrowRight: '<path d="M5 12h14M13 5l7 7-7 7"/>',
    arrowUpRight: '<path d="M7 17 17 7M7 7h10v10"/>',
    history: '<path d="M3 3v5h5"/><path d="M3.1 13A9 9 0 1 0 6 5.3L3 8"/><path d="M12 7v5l4 2"/>',
    refresh: '<path d="M21 12a9 9 0 1 1-2.6-6.4L21 8"/><path d="M21 3v5h-5"/>',
    notebook: '<path d="M13.5 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-7.5"/><path d="M2 6h4M2 10h4M2 14h4M2 18h4"/><path d="M18.4 2.6a2.1 2.1 0 1 1 3 3L16 11l-4 1 1-4z"/>',
    link: '<path d="M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.7 1.7"/><path d="M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7"/>',
    alert: '<path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/><path d="M12 9v4M12 17h.01"/>',
    plug: '<path d="M12 22v-5M9 8V2M15 8V2M18 8v5a6 6 0 0 1-12 0V8z"/>',
    graph: '<circle cx="18" cy="5" r="2.8"/><circle cx="6" cy="12" r="2.8"/><circle cx="18" cy="19" r="2.8"/><path d="m8.5 13.4 7 4.2M15.5 6.4l-7 4.2"/>',
    braces: '<path d="M8 3H7a2 2 0 0 0-2 2v5a2 2 0 0 1-2 2 2 2 0 0 1 2 2v5a2 2 0 0 0 2 2h1M16 21h1a2 2 0 0 0 2-2v-5a2 2 0 0 1 2-2 2 2 0 0 1-2-2V5a2 2 0 0 0-2-2h-1"/>',
    workflow: '<rect x="3" y="3" width="7" height="7" rx="1.8"/><rect x="14" y="14" width="7" height="7" rx="1.8"/><path d="M6.5 10v4a3 3 0 0 0 3 3H14"/>',
    compare: '<circle cx="18" cy="18" r="2.8"/><circle cx="6" cy="6" r="2.8"/><path d="M13 6h3a2 2 0 0 1 2 2v7.2M11 18H8a2 2 0 0 1-2-2V8.8"/>',
    wand: '<path d="m3 21 11-11M15 4V2M15 12v-2M19 8h2M9 8h2M17.8 5.2 19 4M12.2 5.2 11 4M17.8 10.8 19 12"/>',
    download: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3"/>',
    hash: '<path d="M4 9h16M4 15h16M10 3 8 21M16 3l-2 18"/>',
    list: '<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>',
    gauge: '<path d="M12 14 15.5 9.5"/><path d="M3.3 19a10 10 0 1 1 17.4 0"/>',
    dot: '<circle cx="12" cy="12" r="4" fill="currentColor" stroke="none"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    minus: '<path d="M5 12h14"/>',
    rows: '<rect x="3" y="3" width="18" height="18" rx="2.5"/><path d="M3 12h18"/>',
    pivot: '<rect x="3" y="3" width="18" height="18" rx="2.5"/><path d="M3 9h18M9 3v18M13 14h4M15 12v4"/>',
    gitCommit: '<circle cx="12" cy="12" r="3.5"/><path d="M2 12h6.5M15.5 12H22"/>',
    tag: '<path d="M20.6 13.4 13.4 20.6a2 2 0 0 1-2.8 0L2 12V2h10l8.6 8.6a2 2 0 0 1 0 2.8z"/><circle cx="7.5" cy="7.5" r="1.5"/>',
  }
  /** an inline svg icon element: icon('database', 16) */
  function icon(name, size = 16, cls = '') {
    const body = IC[name] || IC.dot
    return html(`<svg class="k-i ${cls}" viewBox="0 0 24 24" width="${size}" height="${size}" aria-hidden="true">${body}</svg>`)
  }

  /* ───────────── components ───────────── */
  /** the 3D glass hero window. Returns { el (the .k-lean root to place), hero, win, bar, title, right, body } */
  function windowEl({ title = '', right = null, w = null, h: hh = null, halo = true, bar = true, bodyClass = '' } = {}) {
    const titleEl = h('span.k-wtitle', title)
    const rightEl = h('span.k-wright', right)
    const barEl = bar ? h('div.k-bar', h('i'), h('i'), h('i'), titleEl, rightEl) : null
    const body = h('div.k-body' + (bodyClass ? '.' + bodyClass : ''))
    const win = h('div.k-win', barEl, body, h('span.k-sheen'))
    const hero = h('div.k-hero', halo ? h('span.k-halo', h('span')) : null, win)
    const el = h('div.k-lean', hero)
    if (w) el.style.width = typeof w === 'number' ? w + 'px' : w
    if (hh) { win.style.height = typeof hh === 'number' ? hh + 'px' : hh; win.style.display = 'flex'; win.style.flexDirection = 'column'; body.style.flex = '1'; body.style.minHeight = '0' }
    return { el, hero, win, bar: barEl, title: titleEl, right: rightEl, body }
  }
  /** a canvas node at (x, y) px in its parent: kind = trigger|code|data|flow|person|out */
  function node({ x, y, name, meta = '', icon: ic = 'dot', kind = 'code', lg = false }) {
    const el = h('div.k-node' + (lg ? '.lg' : ''), { style: { left: x + 'px', top: y + 'px' }, '--kc': `var(--k-${kind})` },
      h('span.t', icon(ic, lg ? 20 : 16)), h('span.n', name), meta ? h('span.m', meta) : null)
    return el
  }
  /** a wide node card at (x, y) (top-left) px */
  function ncard({ x, y, name, meta = '', icon: ic = 'dot', kind = 'code', w = null }) {
    return h('div.k-ncard', { style: { left: x + 'px', top: y + 'px', width: w ? w + 'px' : null }, '--kc': `var(--k-${kind})` },
      h('span.t', icon(ic, 17)), h('span', h('div.n', name), meta ? h('div.m', meta) : null))
  }
  /** an svg layer of bezier edges: pairs = [[x1, y1, x2, y2, cls?], ...] in the parent's px. Returns { svg, paths } */
  function edges(w, hh, pairs) {
    const NS = 'http://www.w3.org/2000/svg'
    const svg = document.createElementNS(NS, 'svg')
    svg.setAttribute('class', 'k-edges'); svg.setAttribute('viewBox', `0 0 ${w} ${hh}`)
    const paths = pairs.map(([x1, y1, x2, y2, cls]) => {
      const p = document.createElementNS(NS, 'path')
      const mx = (x1 + x2) / 2
      p.setAttribute('d', Math.abs(y1 - y2) < 0.5 ? `M ${x1} ${y1} L ${x2} ${y2}` : `M ${x1} ${y1} C ${mx} ${y1}, ${mx} ${y2}, ${x2} ${y2}`)
      if (cls) p.setAttribute('class', cls)
      svg.appendChild(p)
      return p
    })
    return { svg, paths }
  }
  /** a point along an svg path at p (0..1), in its viewBox units */
  function pointAt(path, p) { const L = path.getTotalLength(); const q = path.getPointAtLength(L * clamp01(p)); return { x: q.x, y: q.y } }
  /** stepper: steps = [{title, meta}] ; returns { el, set(i, metaOverride?) } where set marks 0..i-1 done, i current */
  function stepper(steps) {
    const items = steps.map((s) => h('li.pend', h('span.mk', icon('check', 12)), h('div', h('div.tt', s.title), h('div.mt', s.meta || ''))))
    const el = h('ol.k-steps.k-well', items)
    const set = (cur) => items.forEach((li, i) => { li.className = i < cur ? 'done' : i === cur ? 'cur' : 'pend' })
    set(-1)
    return { el, items, set }
  }
  /** a table: cols = css grid template, head = [labels], rows = [[cells]] ; cells may be nodes. returns { el, rows: [rowEls] } */
  function table({ cols, head, rows, cls = '' }) {
    const th = h('div.th', head.map((c) => h('span', c)))
    const trs = rows.map((r) => h('div.tr', r.map((c) => (c instanceof Node ? h('span', c) : h('span', String(c))))))
    const el = h('div.k-table' + (cls ? '.' + cls : ''), { '--cols': cols }, th, trs)
    return { el, head: th, rows: trs }
  }
  /** masked value: ••••  4821 with a lock */
  function mask(tail = '') { return h('span.k-mask', icon('lock', 12), '•••• ' + tail) }
  /** a sparkline / area svg: values -> { svg, line, area } sized w×h */
  function spark(values, w, hh, { area = true, pad = 2 } = {}) {
    const NS = 'http://www.w3.org/2000/svg'
    const min = Math.min(...values), max = Math.max(...values)
    const X = (i) => pad + (i / (values.length - 1)) * (w - pad * 2)
    const Y = (v) => hh - pad - ((v - min) / (max - min || 1)) * (hh - pad * 2)
    const d = values.map((v, i) => `${i ? 'L' : 'M'} ${X(i).toFixed(1)} ${Y(v).toFixed(1)}`).join(' ')
    const svg = document.createElementNS(NS, 'svg')
    svg.setAttribute('class', 'k-spark'); svg.setAttribute('viewBox', `0 0 ${w} ${hh}`); svg.setAttribute('width', w); svg.setAttribute('height', hh)
    let ar = null
    if (area) { ar = document.createElementNS(NS, 'path'); ar.setAttribute('class', 'ar'); ar.setAttribute('d', `${d} L ${X(values.length - 1)} ${hh} L ${X(0)} ${hh} Z`); svg.appendChild(ar) }
    const ln = document.createElementNS(NS, 'path'); ln.setAttribute('class', 'ln'); ln.setAttribute('d', d); svg.appendChild(ln)
    return { svg, line: ln, area: ar, X, Y }
  }
  /** bars: values (0..1) -> { el, bars } ; tween bars with { sy: [0, 1] } */
  function bars(values, { hl = [], mut = [] } = {}) {
    const bs = values.map((v, i) => h('span' + (hl.includes(i) ? '.hl' : mut.includes(i) ? '.mut' : ''), { style: { height: (v * 100).toFixed(1) + '%' } }))
    return { el: h('div.k-bars', bs), bars: bs }
  }
  /** code block: lines = [[[text, cls], ...], ...] (cls: k s f n c d or '') ; returns { el, lines: [spanEls] } */
  function code(lines, { gutter = true } = {}) {
    const ls = lines.map((toks, i) => {
      const txt = h('span.tx', toks.map(([t, c]) => (c ? h('span.' + c, t) : t)))
      return h('div.ln', gutter ? h('span.g', String(i + 1)) : null, txt)
    })
    return { el: h('pre.k-code', ls), lines: ls }
  }

  /* ───────────── motion helpers (all register on a Timeline) ───────────── */
  /** opacity + rise, staggered */
  function reveal(tl, els, { at = 0, stagger = 0.06, dur = 0.7, y = 14, x = 0, s = 1, e = 'out' } = {}) {
    const props = { opacity: [0, 1] }
    if (y) props.y = [y, 0]
    if (x) props.x = [x, 0]
    if (s !== 1) props.s = [s, 1]
    return tl.stagger(els, props, at, stagger, dur, e)
  }
  /** a pop: scale .86 -> 1 with overshoot + fade (a node landing, a badge) */
  function pop(tl, els, { at = 0, stagger = 0.08, dur = 0.55, from = 0.86 } = {}) {
    toEls(els).forEach((el, i) => { tl.add(el, { opacity: [0, 1] }, at + i * stagger, dur * 0.5, 'out'); tl.add(el, { s: [from, 1] }, at + i * stagger, dur, 'pop') })
    return tl
  }
  /** kinetic words: split el's text into masked words that rise in (c6f429f Kinetic) */
  function kinetic(tl, el, { at = 0, stagger = 0.08, dur = 0.95 } = {}) {
    const words = (el.textContent || '').split(' ').filter(Boolean)
    el.textContent = ''
    const inner = words.map((w, i) => { const s = h('span', w + (i < words.length - 1 ? ' ' : '')); el.appendChild(h('span.k-w', s)); return s })
    tl.stagger(inner, { yp: [108, 0] }, at, stagger, dur, 'out')
    return inner
  }
  /** typed text: el's text appears char by char from `at` at `cps`; shows a caret while typing. returns end time */
  function type(tl, el, text, { at = 0, cps = 38, caret = true } = {}) {
    const dur = text.length / cps
    const tx = h('span'), cr = caret ? h('span.k-caret') : null
    el.textContent = ''; el.appendChild(tx); if (cr) el.appendChild(cr)
    tl.fn(at, dur, (p, t) => {
      const n = Math.round(text.length * p)
      if (tx.__n !== n) { tx.textContent = text.slice(0, n); tx.__n = n }
      if (cr) cr.style.display = t >= at && t < at + dur + 0.6 ? '' : 'none'
    })
    return at + dur
  }
  /** count a number: fmt(v) -> string */
  function count(tl, el, to, { at = 0, dur = 1.1, from = 0, fmt = (v) => Math.round(v).toLocaleString('en-US'), e = 'out' } = {}) {
    tl.fn(at, dur, (p) => { const s = fmt(lerp(from, to, p)); if (el.__s !== s) { el.textContent = s; el.__s = s } }, e)
  }
  /** draw an svg path (stroke) */
  function draw(tl, paths, { at = 0, stagger = 0.08, dur = 0.5, e = 'out' } = {}) { return tl.stagger(paths, { draw: [0, 1], opacity: [0, 1] }, at, stagger, dur, e) }
  /** swap text over time: list = [{at, text}] (or nodes) — each new item rises in, the old one lifts out */
  function swap(tl, el, list, { dur = 0.45 } = {}) {
    el.classList.add('k-title-stack')
    const items = list.map((it) => { const n = h('span', it.text instanceof Node ? it.text : String(it.text)); el.appendChild(n); return n })
    items.forEach((n, i) => {
      tl.add(n, { opacity: [0, 1], y: [10, 0] }, list[i].at, dur, 'out')
      if (i < items.length - 1) tl.add(n, { opacity: [1, 0], y: [0, -10] }, list[i + 1].at - 0.05, dur * 0.7, 'in')
    })
    return items
  }
  /** a moving dot along a path element (svg path in a container of the same px size) */
  function travel(tl, dotEl, path, { at = 0, dur = 1, e = 'io', fade = true } = {}) {
    tl.fn(at, dur, (p, t) => {
      const q = pointAt(path, p)
      dotEl.style.left = q.x + 'px'; dotEl.style.top = q.y + 'px'
      if (fade) dotEl.style.opacity = t < at || t > at + dur + 0.25 ? '0' : '1'
    }, e)
  }

  /* ───────────── gears (transition spectacle) ───────────── */
  function gearPath(R, teeth, depth, hole) {
    // a gear outline centred at (R, R): flat tips, flat roots, angled flanks; + a centre hole (evenodd)
    const r = R - depth, c = R, n = teeth, pts = []
    for (let i = 0; i < n; i++) {
      const a0 = (i / n) * Math.PI * 2, step = (Math.PI * 2) / n
      pts.push([r, a0], [r, a0 + step * 0.12], [R, a0 + step * 0.27], [R, a0 + step * 0.58], [r, a0 + step * 0.73])
    }
    const P = pts.map(([rad, a], i) => `${i ? 'L' : 'M'}${(c + rad * Math.cos(a)).toFixed(1)} ${(c + rad * Math.sin(a)).toFixed(1)}`).join(' ') + ' Z'
    const hh = `M${c + hole} ${c} A${hole} ${hole} 0 1 0 ${c - hole} ${c} A${hole} ${hole} 0 1 0 ${c + hole} ${c} Z`
    return { outer: P, hole: hh }
  }
  const GEARS = [
    // size (fraction of vmin), teeth, path from -> to (viewport fractions), z from -> to, spin (deg), time window
    { f: 0.62, n: 16, x: [1.05, -0.35], y: [0.95, 0.15], z: [-500, 260], rot: [0, -150], at: 0.0, dur: 1.35 },
    { f: 0.36, n: 11, x: [1.25, 0.05], y: [0.25, -0.30], z: [-300, 160], rot: [0, 240], at: 0.08, dur: 1.25 },
    { f: 0.22, n: 9, x: [0.85, -0.25], y: [1.25, 0.55], z: [-200, 340], rot: [0, -330], at: 0.16, dur: 1.2 },
  ]
  let gearEls = []
  function buildGears(host) {
    host.textContent = ''
    const vmin = Math.min(innerWidth, innerHeight)
    gearEls = GEARS.map((g) => {
      const D = Math.round(vmin * g.f), R = D / 2
      const gp = gearPath(R, g.n, R * 0.13, R * 0.24)
      const el = h('div.k-gear', { style: { width: D + 'px', height: D + 'px' } })
      const gl = h('div.gl')
      gl.style.clipPath = `path(evenodd, '${gp.outer} ${gp.hole}')`
      el.appendChild(gl)
      el.appendChild(html(`<svg viewBox="0 0 ${D} ${D}" width="${D}" height="${D}"><path class="r1" d="${gp.outer}"/><path class="r1" d="${gp.hole}"/><circle class="r2" cx="${R}" cy="${R}" r="${(R * 0.58).toFixed(1)}"/></svg>`))
      host.appendChild(el)
      return { el, D, g }
    })
  }
  function gearsAt(t, dir) {
    for (const { el, D, g } of gearEls) {
      const p = clamp01((t - g.at) / g.dur)
      if (p <= 0 || p >= 1) { el.style.opacity = '0'; continue }
      const e = EASE.io(p)
      const fx = dir > 0 ? lerp(g.x[0], g.x[1], e) : lerp(g.x[1], g.x[0], e)
      const fy = dir > 0 ? lerp(g.y[0], g.y[1], e) : lerp(g.y[1], g.y[0], e)
      const z = lerp(g.z[0], g.z[1], e)
      const rot = lerp(g.rot[0], g.rot[1], e) * dir
      const x = fx * innerWidth - D / 2, y = fy * innerHeight - D / 2
      el.style.transform = `translate3d(${x.toFixed(1)}px,${y.toFixed(1)}px,${z.toFixed(1)}px) rotateX(18deg) rotateY(-14deg) rotate(${rot.toFixed(2)}deg)`
      el.style.opacity = Math.min(1, Math.sin(Math.PI * p) * 1.3).toFixed(3)
    }
  }

  /* ───────────── scenes ───────────── */
  const defs = []
  const scenes = []
  let scenesEl, gearsHost
  const T_OUT = 0.75          /* the old scene flies forward and fades */
  const T_IN_AT = 0.22, T_IN = 1.3   /* the new scene comes from depth and settles */
  const T_GEARS = 1.4
  const T_HAND = 0.5          /* handoff: the old scene dissolves over the identical first frame of the new one */
  /* a scene may declare handoffFrom: <index> when its first frame continues that scene's last frame */
  const kindFor = (from, to, d, opt) => opt || (d > 0 && scenes[to].def.handoffFrom === scenes[from].def.index ? 'handoff' : 'depth')

  function buildHeader(def, root, tl) {
    if (def.head === false) return
    const center = def.headAlign === 'center'
    const head = h('header.k-head' + (center ? '.center' : ''))
    if (def.kicker) {
      const parts = String(def.kicker).split(' · ')
      const k = h('div.k-kicker', h('i'), h('span', h('b', parts[0]), parts.length > 1 ? ' · ' + parts.slice(1).join(' · ') : ''))
      head.appendChild(k)
      tl.add(k, { opacity: [0, 1], y: [8, 0] }, def.headAt ?? 0.45, 0.6, 'out')
    }
    const caps = def.captions || (def.caption ? [{ at: def.headAt != null ? def.headAt + 0.12 : 0.6, text: def.caption }] : [])
    if (caps.length) {
      const stack = h('div.k-title-stack')
      caps.forEach((c, i) => {
        const t = h('h2.k-title', c.text)
        stack.appendChild(t)
        kinetic(tl, t, { at: c.at, stagger: 0.06, dur: 0.85 })
        if (i < caps.length - 1) tl.add(t, { opacity: [1, 0], y: [0, -14] }, caps[i + 1].at - 0.12, 0.35, 'in')
      })
      head.appendChild(stack)
    }
    if (def.sub) {
      const s = h('p.k-sub', def.sub)
      head.appendChild(s)
      tl.add(s, { opacity: [0, 1], y: [10, 0] }, def.subAt ?? (caps.length ? caps[caps.length - 1].at + 0.5 : 0.9), 0.7, 'out')
    }
    root.appendChild(head)
  }

  function buildScene(def, i) {
    const el = h('section.k-scene.s-' + def.id, { 'data-scene': i, hidden: true })
    const approach = h('div.k-approach')
    const content = h('div.k-content')
    const area = h('div.k-area')
    approach.appendChild(content); el.appendChild(approach); scenesEl.appendChild(el)
    const tl = new Timeline()
    buildHeader(def, content, tl)
    content.appendChild(area)
    if (def.css) document.head.appendChild(h('style', { 'data-slide': def.id }, def.css))
    const kit = KIT(tl, area, content)
    let state = {}
    try { state = (def.build && def.build(area, kit, content)) || {} } catch (err) { console.error('[film] build failed for', def.id, err); area.appendChild(h('div', { style: { padding: '40px', color: 'var(--bad)' } }, 'build error: ' + err.message)) }
    try { def.timeline && def.timeline(tl, kit, state) } catch (err) { console.error('[film] timeline failed for', def.id, err) }
    const end = Math.max(def.dur || 0, tl.end)
    return { def, el, approach, content, area, tl, state, kit, end }
  }

  function KIT(tl, area, content) {
    return {
      W, H, tl, area, content, still: () => still,
      h, html, icon, append,
      window: windowEl, node, ncard, edges, pointAt, stepper, table, mask, spark, bars, code,
      reveal: (els, o) => reveal(tl, els, o), pop: (els, o) => pop(tl, els, o), kinetic: (el, o) => kinetic(tl, el, o),
      type: (el, text, o) => type(tl, el, text, o), count: (el, to, o) => count(tl, el, to, o), draw: (p, o) => draw(tl, p, o),
      swap: (el, list, o) => swap(tl, el, list, o), travel: (d, p, o) => travel(tl, d, p, o),
      ease: EASE, lerp, clamp01,
    }
  }

  /* ───────────── playback ───────────── */
  let cur = -1          /* the scene shown (or arriving) */
  let out = -1          /* the scene leaving, during a transition */
  let dir = 1
  let t0 = 0, raf = 0, playing = false, enterKind = 'depth'

  function show(i, on) { if (scenes[i]) scenes[i].el.hidden = !on }
  function resetApproach(sc) { sc.approach.style.transform = ''; sc.approach.style.opacity = '' }

  function approachIn(sc, t, kind) {
    if (kind === 'none' || kind === 'handoff') { resetApproach(sc); return }
    if (kind === 'fade') { sc.approach.style.transform = ''; sc.approach.style.opacity = String(EASE.out(clamp01(t / 0.9)).toFixed(3)); return }
    const p = clamp01((t - T_IN_AT) / T_IN)
    const e = EASE.soft(p)
    const z = dir > 0 ? lerp(-900, 0, e) : lerp(520, 0, e)
    const rx = lerp(dir > 0 ? 16 : -10, 0, e)
    const y = lerp(dir > 0 ? 50 : -30, 0, e)
    const o = EASE.out(clamp01((t - T_IN_AT) / 0.55))
    sc.approach.style.transform = p >= 1 ? '' : `translate3d(0,${y.toFixed(1)}px,${z.toFixed(1)}px) rotateX(${rx.toFixed(2)}deg)`
    sc.approach.style.opacity = o >= 1 ? '' : o.toFixed(3)
  }
  function approachOut(sc, t) {
    if (enterKind === 'handoff') {
      /* a continuous hand-off: the new scene starts on the old one's last frame, so the old just dissolves in place */
      sc.approach.style.transform = ''
      sc.approach.style.opacity = String((1 - EASE.io(clamp01(t / T_HAND))).toFixed(3))
      return
    }
    const p = clamp01(t / T_OUT)
    const e = EASE.in(p)
    const z = dir > 0 ? lerp(0, 760, e) : lerp(0, -1000, e)
    sc.approach.style.transform = `translate3d(0,${(dir > 0 ? -20 : 30) * e}px,${z.toFixed(1)}px) rotateX(${(dir > 0 ? -6 : 8) * e}deg)`
    sc.approach.style.opacity = String((1 - EASE.out(clamp01((t - 0.1) / (T_OUT - 0.1)))).toFixed(3))
  }

  function frame(t) {
    const sc = scenes[cur]
    if (!sc) return
    approachIn(sc, t, enterKind)
    if (out >= 0 && out !== cur) {
      if (t < (enterKind === 'handoff' ? T_HAND : T_OUT)) approachOut(scenes[out], t)
      else { show(out, false); resetApproach(scenes[out]); out = -1 }
    }
    if (enterKind === 'depth' && !still) gearsAt(t, dir)
    sc.tl.eval(t)
    try { sc.def.at && sc.def.at(t, sc.kit, sc.state) } catch (err) { console.error('[film] at() failed', err); sc.def.at = null }
  }
  function endOf(sc) { return Math.max(sc.end, enterKind === 'depth' ? T_IN_AT + T_IN : 0.9, T_GEARS) + 0.02 }
  function loop(now) {
    raf = 0
    const sc = scenes[cur]
    const t = (now - t0) / 1000
    const end = endOf(sc)
    if (t >= end) { frame(end); finish(); return }
    frame(t)
    raf = requestAnimationFrame(loop)
  }
  function finish() {
    playing = false
    if (out >= 0 && out !== cur) { show(out, false); resetApproach(scenes[out]); out = -1 }
    if (scenes[cur]) resetApproach(scenes[cur])
    gearEls.forEach((g) => (g.el.style.opacity = '0'))
    document.documentElement.dataset.playing = ''
  }
  function stop() { if (raf) cancelAnimationFrame(raf); raf = 0; if (playing) finish() }

  function play(i, opts = {}) {
    i = clampIndex(i)
    if (i < 0) return
    if (still) return rest(i)
    const prev = cur
    stop()
    if (prev >= 0 && prev !== i) { out = prev; dir = i > prev ? 1 : -1; enterKind = kindFor(prev, i, dir, opts.enter) }
    else { out = -1; dir = 1; enterKind = opts.enter || (prev < 0 ? (scenes[i].def.enter || 'fade') : 'fade') }
    cur = i
    show(i, true)
    if (out >= 0) { show(out, true); scenes[out].el.style.zIndex = '2'; scenes[i].el.style.zIndex = '1' }
    playing = true
    document.documentElement.dataset.playing = '1'
    t0 = performance.now() - (opts.from || 0) * 1000
    frame(opts.from || 0)
    raf = requestAnimationFrame(loop)
  }
  function rest(i) {
    i = clampIndex(i)
    if (i < 0) return
    stop()
    scenes.forEach((s, k) => { if (k !== i) { show(k, false); resetApproach(s) } })
    cur = i; out = -1
    show(i, true); resetApproach(scenes[i])
    scenes[i].tl.eval(1e4)
    try { scenes[i].def.at && scenes[i].def.at(1e4, scenes[i].kit, scenes[i].state) } catch (err) { console.error(err) }
  }
  /** debug/preview: freeze scene i at time t (seconds, includes the transition from `from` if given) */
  function seek(i, t, from = -1) {
    i = clampIndex(i); from = from >= 0 ? clampIndex(from) : -1; stop()
    scenes.forEach((s, k) => { if (k !== i && k !== from) show(k, false) })
    cur = i; out = from >= 0 && from !== i ? from : -1; dir = out >= 0 && i < out ? -1 : 1; enterKind = out >= 0 ? kindFor(out, i, dir) : 'none'
    show(i, true); if (out >= 0) { show(out, true); scenes[out].el.style.zIndex = '2'; scenes[i].el.style.zIndex = '1'; scenes[out].tl.eval(1e4) }
    frame(t)
  }
  function clampIndex(i) {
    if (!scenes.length) return -1
    const k = scenes.findIndex((s) => s.def.index === i)
    return k >= 0 ? k : Math.max(0, Math.min(scenes.length - 1, i))
  }
  function quiet(on) { scenesEl.classList.toggle('quiet', !!on); gearsHost.classList.toggle('off', !!on) }
  function theme(dark) { document.documentElement.dataset.theme = dark ? 'dark' : 'light' }

  /* pointer lean: springs toward the target, sleeps when settled */
  let lx = 0, ly = 0, tx = 0, ty = 0, praf = 0, plast = 0
  function pointer(x, y) {
    if (still) return
    tx = Math.max(-1, Math.min(1, +x || 0)); ty = Math.max(-1, Math.min(1, +y || 0))
    if (!praf) { plast = performance.now(); praf = requestAnimationFrame(leanLoop) }
  }
  function leanLoop(now) {
    const dt = Math.min(0.05, (now - plast) / 1000); plast = now
    const k = 1 - Math.exp(-dt * 7)
    lx += (tx - lx) * k; ly += (ty - ly) * k
    const r = document.documentElement.style
    r.setProperty('--lx', lx.toFixed(4)); r.setProperty('--ly', ly.toFixed(4))
    if (Math.abs(tx - lx) < 0.001 && Math.abs(ty - ly) < 0.001) { praf = 0; return }
    praf = requestAnimationFrame(leanLoop)
  }

  function fit() {
    const s = Math.min(innerWidth / W, innerHeight / H)
    document.documentElement.style.setProperty('--k-scale', s.toFixed(5))
  }

  /* ───────────── boot ───────────── */
  function boot() {
    if (!document.documentElement.dataset.theme) theme(qs.get('theme') ? qs.get('theme') === 'dark' : !!(window.matchMedia && matchMedia('(prefers-color-scheme: dark)').matches))
    if (still) document.documentElement.classList.add('k-still')
    scenesEl = document.getElementById('k-scenes') || document.body.appendChild(h('div', { id: 'k-scenes' }))
    gearsHost = document.getElementById('k-gears') || document.body.appendChild(h('div', { id: 'k-gears' }))
    fit(); addEventListener('resize', () => { fit(); buildGears(gearsHost) })
    buildGears(gearsHost)
    defs.sort((a, b) => a.index - b.index)
    for (const d of defs) scenes.push(buildScene(d, scenes.length))
    window.film.scenes = scenes.map((s) => ({ name: s.def.name, word: s.def.word || s.def.name }))
    if (mq.addEventListener) mq.addEventListener('change', (e) => { still = e.matches; document.documentElement.classList.toggle('k-still', still) })
    const auto = window.FILM_AUTOPLAY !== false
    const go = () => { if (!auto) return; if (still) rest(0); else play(0) }
    const fonts = document.fonts && document.fonts.ready ? Promise.race([document.fonts.ready, new Promise((r) => setTimeout(r, 700))]) : Promise.resolve()
    fonts.then(go)
    window.dispatchEvent(new Event('film:ready'))
  }

  window.Film = {
    slide(def) {
      if (!def || def.index == null || !def.id) throw new Error('Film.slide needs { index, id, name }')
      const k = defs.findIndex((d) => d.index === def.index); if (k >= 0) defs.splice(k, 1)
      defs.push(def)
    },
    Timeline, EASE, h, html, icon,
  }
  window.film = {
    scenes: [], play: (i) => play(i), rest, quiet, theme, pointer,
    _seek: seek, _playFrom: (i, from) => { rest(from); setTimeout(() => play(i), 400) }, _state: () => ({ cur, out, playing, still }),
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot)
  else setTimeout(boot, 0)
})()
