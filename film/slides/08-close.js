/* 08 · Close — "One engine. Many uses." The last frame of the talk: two slam lines, then three stat plates in the
 * window material rise from depth and fan gently (the middle one forward, with the only halo), joined underneath by
 * one hairline spine (one engine), then the closing question. Rests perfectly still. */
Film.slide({
  index: 8,
  id: 'close',
  name: 'Close',
  word: 'Recap',
  head: false,
  dur: 7.4,
  css: `
    .s-close .cl-head { position: absolute; left: 160px; right: 160px; top: 138px; display: flex; flex-direction: column; align-items: center; gap: 6px; text-align: center; }
    .s-close .cl-head .k-slam { line-height: 1.04; }
    .s-close .cl-l2 .k-w > span { background: linear-gradient(100deg, var(--brand-1), var(--brand-2)); -webkit-background-clip: text; background-clip: text; color: transparent; padding-right: .04em; }
    .s-close .cl-card { font-variant-numeric: proportional-nums; }
    .s-close .cl-row { position: absolute; left: 50%; top: 432px; width: 1160px; height: 212px; margin-left: -580px; perspective: 1400px; }
    .s-close .cl-fan { position: absolute; inset: 0; transform-style: preserve-3d; }
    .s-close .cl-slot { position: absolute; top: 0; width: 360px; height: 212px; transform-style: preserve-3d; }
    .s-close .cl-rise { position: absolute; inset: 0; transform-style: preserve-3d; transform-origin: 50% 100%; }
    .s-close .cl-card .k-body { display: flex; flex-direction: column; padding: 24px 26px 22px; }
    .s-close .cl-top { display: flex; align-items: center; justify-content: space-between; }
    .s-close .cl-v { margin-top: auto; font-size: 44px; line-height: 1; font-weight: 720; letter-spacing: -0.035em; color: var(--fg); white-space: nowrap; }
    .s-close .cl-mid .cl-v { background: linear-gradient(100deg, var(--brand-1), var(--brand-2)); -webkit-background-clip: text; background-clip: text; color: transparent; }
    .s-close .cl-l { margin-top: 12px; font-size: 14px; line-height: 1.45; color: var(--muted); max-width: 300px; }
    .s-close .cl-spine { position: absolute; left: 50%; top: 672px; width: 1160px; height: 24px; margin-left: -580px; overflow: visible; }
    .s-close .cl-spine path { fill: none; stroke: color-mix(in srgb, var(--primary) 45%, transparent); stroke-width: 1.25; stroke-linecap: round; }
    .s-close .cl-spine circle { fill: var(--bg); stroke: color-mix(in srgb, var(--primary) 70%, transparent); stroke-width: 1.5; transform-box: fill-box; transform-origin: center; }
    .s-close .cl-q { position: absolute; left: 160px; right: 160px; top: 738px; margin: 0; text-align: center; font-size: 30px; line-height: 1.2; font-weight: 600; letter-spacing: -0.025em; color: var(--muted); }
  `,
  build(area, kit, root) {
    const { h, icon } = kit
    const l1 = h('h1.k-slam.cl-l1', 'One engine.')
    // one mask for the whole line so the gradient runs continuously across both words
    const l2in = h('span', 'Many uses.')
    const l2 = h('h2.k-slam.cl-l2', h('span.k-w', l2in))
    root.appendChild(h('div.cl-head', l1, l2))

    const STATS = [
      { ic: 'workflow', kc: 'var(--primary)', v: '35', l: 'node types in an engine built from scratch' },
      { ic: 'notebook', kc: 'var(--brand-2)', v: '2 ideas', l: 'data analysis and testing, both in the notebook, on the same engine' },
      { ic: 'lock', kc: 'var(--ok)', v: 'Read-only', l: 'by default. Notebook writes wait for a person.' },
    ]
    const fan = h('div.cl-fan')
    const slots = [], rises = [], vals = []
    STATS.forEach((st, i) => {
      const w = kit.window({ bar: false, halo: i === 1, h: 212 })
      w.win.classList.add('cl-card')
      const v = h('div.cl-v', st.v)
      kit.append(w.body, [
        h('div.cl-top', h('span.k-icon-tile.lg', { '--kc': st.kc }, icon(st.ic, 20))),
        v,
        h('div.cl-l', st.l),
      ])
      const rise = h('div.cl-rise', w.el)
      const slot = h('div.cl-slot' + (i === 1 ? '.cl-mid' : ''), { style: { left: i * 400 + 'px' } }, rise)
      fan.appendChild(slot)
      slots.push(slot); rises.push(rise); vals.push(v)
    })
    root.appendChild(h('div.cl-row', fan))

    // the spine: one hairline joining the three plates (one engine)
    const NS = 'http://www.w3.org/2000/svg'
    const svg = document.createElementNS(NS, 'svg')
    svg.setAttribute('class', 'cl-spine'); svg.setAttribute('viewBox', '0 0 1160 24')
    const path = document.createElementNS(NS, 'path')
    path.setAttribute('d', 'M 180 12 L 980 12')
    svg.appendChild(path)
    const dots = [180, 580, 980].map((x) => {
      const c = document.createElementNS(NS, 'circle')
      c.setAttribute('cx', x); c.setAttribute('cy', 12); c.setAttribute('r', 4)
      svg.appendChild(c)
      return c
    })
    root.appendChild(svg)

    const q = h('p.cl-q', 'What would you build on it?')
    root.appendChild(q)
    return { l1, l2in, slots, rises, vals, path, dots, q }
  },
  timeline(tl, kit, s) {
    kit.kinetic(s.l1, { at: 0.5, stagger: 0.09, dur: 0.95 })
    tl.add(s.l2in, { yp: [108, 0] }, 1.3, 1.0, 'out')

    // the plates rise from depth one by one, then fan to their resting angles
    s.rises.forEach((r, i) => {
      const at = 2.2 + i * 0.18
      tl.add(r, { opacity: [0, 1] }, at, 0.6, 'out')
      tl.add(r, { z: [-300, 0], rx: [20, 0], y: [40, 0] }, at, 1.25, 'soft')
    })
    const FAN = [{ ry: [0, 8] }, { y: [0, -10] }, { ry: [0, -8] }]
    s.slots.forEach((sl, i) => tl.add(sl, FAN[i], 3.4, 0.9, 'io'))
    kit.count(s.vals[0], 35, { at: 2.3, dur: 1.3 })

    // one engine: the spine draws left to right under them
    kit.draw(s.path, { at: 3.9, dur: 0.9, stagger: 0 })
    s.dots.forEach((d, i) => {
      tl.add(d, { opacity: [0, 1] }, 3.9 + i * 0.4, 0.3, 'out')
      tl.add(d, { s: [0.4, 1] }, 3.9 + i * 0.4, 0.45, 'pop')
    })

    // the closing question
    tl.add(s.q, { opacity: [0, 1], y: [16, 0] }, 4.8, 0.8, 'out')
    tl.fn(4.8, 1.2, (p) => { s.q.style.color = `color-mix(in srgb, var(--fg) ${Math.round(p * 100)}%, var(--muted))` }, 'out')
  },
})
