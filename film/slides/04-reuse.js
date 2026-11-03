/* 04 · Reuse — "Macro elements: each block is a whole capability."
 * The hero is a block library window. Four capability blocks sit in a dotted well as closed glass boxes. One at a
 * time each lid hinges up toward the viewer (rotateX on its top edge, per-box perspective) and the inside shows the
 * micro-node graph the block encapsulates: the tiles pop and lift out in z, the edges draw. The lid closes, the
 * face's dashed signature turns solid and a "N steps inside" badge lands. Then the Notebook card lights up as the
 * way in and two blocks get their use-case tags. Rest: Data check left half-open, its insides showing.
 * One variable per box (--open, 0..1) drives the lid angle, the lid light, the shadow in the tray and the
 * visibility of the inside, so the whole box is a pure function of t. */
Film.slide({
  index: 4,
  id: 'reuse',
  name: 'Reuse',
  word: 'Macro elements',
  kicker: '04 · Macro elements',
  captions: [
    { at: 0.6, text: 'Macro elements: each block is a whole capability.' },
    { at: 9.5, text: 'Two ideas to show next: data analysis and testing.' },
  ],
  sub: 'What it is now · macro elements, not micro nodes',
  subAt: 1.25,
  dur: 14.5,
  css: `
    .s-reuse .k-title { font-size: 46px; max-width: 1180px; }
    .s-reuse .k-title-stack > .k-title { align-self: center; }
    .s-reuse .ru-stage { position: absolute; left: 16px; top: 46px; width: 1440px; perspective: 1600px; }
    .s-reuse .ru-tilt { transform-origin: 50% 0%; transform-style: preserve-3d; }
    .s-reuse .ru-well { height: 100%; padding: 16px 20px 0; display: flex; flex-direction: column; }

    /* toolbar */
    .s-reuse .ru-tools { display: flex; align-items: center; gap: 8px; height: 26px; }
    .s-reuse .ru-tools .lt { display: flex; align-items: center; gap: 8px; color: var(--faint); }
    .s-reuse .ru-tools .lt .k-label { color: var(--muted); }
    .s-reuse .ru-tools .sp { flex: 1; }
    .s-reuse .ru-search { display: inline-flex; align-items: center; gap: 7px; height: 26px; width: 200px; padding: 0 10px; font-size: 12px; color: var(--faint); box-shadow: inset 0 0 0 1px var(--hairline); }

    /* the four boxes */
    .s-reuse .ru-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 20px; height: 340px; margin-top: 14px; }
    .s-reuse .ru-col { position: relative; }
    .s-reuse .ru-box { position: absolute; inset: 0; perspective: 1800px; perspective-origin: 50% 30%; --open: 0; }
    .s-reuse .ru-tray { position: absolute; inset: 0; border-radius: 16px; transform-style: preserve-3d;
      background: radial-gradient(circle, var(--grid-dot) .8px, transparent 1.2px) 0 0 / 14px 14px, color-mix(in srgb, var(--fg) 5%, var(--well));
      box-shadow: inset 0 2px 8px color-mix(in srgb, var(--brand-1) 16%, transparent), inset 0 0 0 1px var(--hairline), 0 1px 0 var(--raised-ring); }
    .s-reuse .ru-tray::before { content: ''; position: absolute; left: 7px; right: 7px; top: 7px; height: 190px; border-radius: 12px 12px 40% 40%; pointer-events: none;
      background: linear-gradient(180deg, color-mix(in srgb, var(--brand-1) 22%, transparent), transparent); opacity: calc(var(--open) * .9); }
    .s-reuse .ru-in { opacity: clamp(0, calc(var(--open) * 3 - .2), 1); }
    .s-reuse .ru-tray .k-edges { opacity: clamp(0, calc(var(--open) * 3 - .2), 1); }
    .s-reuse .ru-mw { position: absolute; left: 0; top: 0; width: 0; height: 0; transform-style: preserve-3d; opacity: calc(clamp(0, calc(var(--open) * 3 - .2), 1) * var(--a, 0)); }
    .s-reuse .ru-mw .k-node .t { box-shadow: inset 0 0 0 1px var(--node-ring), inset 0 1px 0 rgb(255 255 255 / .5), 0 12px 22px -10px var(--kc, var(--primary)); }
    .s-reuse .ru-mw .k-av { width: 26px; height: 26px; font-size: 11px; box-shadow: none; }
    .s-reuse .ru-ck { display: inline-flex; align-items: center; justify-content: center; width: 16px; height: 16px; border-radius: 99px; color: #fff; background: var(--ok); box-shadow: 0 0 0 2px var(--node-bg); }
    .s-reuse .ru-ck svg { width: 10px; height: 10px; stroke-width: 3; }
    .s-reuse .ru-foot { position: absolute; left: 16px; right: 16px; top: 306px; height: 22px; display: flex; align-items: center; justify-content: space-between; gap: 8px; }

    /* the lid: the block's compact face, hinged on its top edge */
    .s-reuse .ru-lid { position: absolute; left: 7px; right: 7px; top: 7px; bottom: 7px; border-radius: 12px; padding: 18px 18px 16px;
      display: flex; flex-direction: column; transform-origin: 50% 0%; transform: rotateX(calc(var(--open) * 62deg));
      background: linear-gradient(180deg, rgb(255 255 255 / .10), transparent 40%), var(--node-bg);
      box-shadow: inset 0 0 0 1px var(--node-ring), inset 0 1px 0 rgb(255 255 255 / .55), 0 12px 26px -18px var(--primary), 0 calc(var(--open) * 40px) calc(var(--open) * 50px) calc(var(--open) * -22px) color-mix(in srgb, var(--brand-1) 45%, transparent); }
    .s-reuse .ru-lid::after { content: ''; position: absolute; inset: 0; border-radius: inherit; pointer-events: none; opacity: calc(var(--open) * .8);
      background: linear-gradient(180deg, transparent 30%, color-mix(in srgb, var(--brand-2) 16%, transparent)); }
    .s-reuse .ru-top { display: flex; align-items: center; gap: 12px; }
    .s-reuse .ru-nm { font-size: 18px; font-weight: 650; letter-spacing: -0.015em; line-height: 1.2; color: var(--fg); }
    .s-reuse .ru-fx { margin-top: 2px; font-size: 12px; color: var(--faint); }
    .s-reuse .ru-line { margin-top: 16px; font-size: 14px; line-height: 1.45; color: var(--muted); min-height: 40px; }
    .s-reuse .ru-io { margin-top: 16px; padding: 8px 10px; display: flex; flex-direction: column; gap: 6px; }
    .s-reuse .ru-io .r { display: flex; align-items: center; gap: 10px; height: 22px; }
    .s-reuse .ru-io .k-label { width: 30px; }
    .s-reuse .ru-last { margin-top: 16px; display: flex; align-items: center; gap: 7px; font-size: 12px; color: var(--muted); }
    .s-reuse .ru-last svg { color: var(--faint); width: 14px; height: 14px; }
    .s-reuse .ru-bot { margin-top: auto; padding-top: 12px; border-top: 1px solid var(--hairline); display: flex; align-items: center; justify-content: space-between; gap: 8px; }
    .s-reuse .ru-sig { display: inline-flex; align-items: center; height: 32px; padding: 0 6px; }
    .s-reuse .ru-sig .s { display: inline-flex; align-items: center; justify-content: center; width: 22px; height: 22px; border-radius: 6px; color: var(--kc);
      background: var(--node-bg); box-shadow: inset 0 0 0 1px var(--node-ring); }
    .s-reuse .ru-sig .s svg { width: 12px; height: 12px; }
    .s-reuse .ru-sig .l { width: 10px; height: 1.5px; background: color-mix(in srgb, var(--primary) 45%, transparent); }
    .s-reuse .ru-sig.bp .s { background: transparent; box-shadow: none; outline: 1.2px dashed color-mix(in srgb, var(--primary) 55%, transparent); color: var(--faint); }
    .s-reuse .ru-sig.bp .l { background: color-mix(in srgb, var(--primary) 22%, transparent); }

    /* the use-case tab on top of a box */
    .s-reuse .ru-tag { position: absolute; z-index: 3; top: -12px; right: 16px; }
    .s-reuse .ru-tag .k-chip { height: 24px; box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--ai) 30%, transparent), 0 8px 18px -10px var(--ai);
      background: linear-gradient(var(--ai-soft), var(--ai-soft)), var(--node-bg); }

    /* the strip of smaller blocks */
    .s-reuse .ru-strip { display: grid; grid-template-columns: repeat(3, 1fr); gap: 20px; height: 98px; margin-top: 18px; }
    .s-reuse .ru-sm { position: relative; display: flex; align-items: center; gap: 14px; padding: 0 18px; border-radius: 14px; }
    .s-reuse .ru-sm .tx { flex: 1; min-width: 0; }
    .s-reuse .ru-sm .nm { font-size: 15px; font-weight: 650; letter-spacing: -0.01em; color: var(--fg); line-height: 1.25; }
    .s-reuse .ru-sm .ln { margin-top: 2px; font-size: 13px; line-height: 1.4; color: var(--muted); }
    .s-reuse .ru-sm .rt { flex: none; display: flex; align-items: center; gap: 6px; }
    .s-reuse .ru-ring { position: absolute; inset: 0; border-radius: inherit; pointer-events: none;
      box-shadow: 0 0 0 1.5px color-mix(in srgb, var(--primary) 55%, transparent), 0 0 0 5px var(--brand-soft), 0 16px 34px -16px var(--primary); }
  `,
  build(area, kit) {
    const { h, icon } = kit
    const BW = 329, BH = 340, NY = 262

    const BLOCKS = [
      {
        name: 'Data check', ic: 'search', kind: 'data', fx: 'Effects · database, network',
        line: 'Query → rule → look up the source API → one alert',
        io: [['In', 'bank.wires'], ['Out', 'one alert']], last: ['history', 'Last run · today 07:00 · 3 stuck found'],
        badge: '4 steps inside', inner: 'Inside · 4 micro nodes', tok: ['bank · read-only', ''],
        nodes: [['Query', 'database', 'data'], ['Rule', 'filter', 'flow'], ['Source API', 'globe', 'out'], ['Alert', 'alert', 'trigger']],
        tag: 'Idea 1 · data analysis',
      },
      {
        name: 'Browser test', ic: 'browser', kind: 'trigger', fx: 'Effects · network',
        line: 'Portal sign-in, steps you can take over, exports a Playwright spec',
        io: [['In', 'portal sign-in'], ['Out', 'spec.ts']], last: ['checkCircle', 'Last run · 5 of 5 steps passed'],
        badge: '4 steps inside', inner: 'Inside · 4 micro nodes', tok: ['spec.ts', ''],
        nodes: [['Open portal', 'browser', 'trigger'], ['Fill', 'form', 'code'], ['Sign in', 'hand', 'person'], ['Export', 'download', 'out']],
        tag: 'Idea 2 · testing',
      },
      {
        name: 'Approval chain', ic: 'users', kind: 'person', fx: 'Effects · a person',
        line: 'Maker → checker → treasury, each decision kept',
        io: [['In', 'proposed change'], ['Out', 'decision record']], last: ['history', 'Last decision · W2 · stuck → released'],
        badge: '3 steps inside', inner: 'Inside · 3 micro nodes', tok: ['each decision kept', 'ok'],
        people: [['Maker', 'M', ''], ['Checker', 'C', 'b'], ['Treasury', 'T', 'c']],
      },
      {
        name: 'Scheduled check', ic: 'calendar', kind: 'trigger', fx: 'Effects · database',
        line: 'Every business day 07:00, same code, same answer',
        io: [['In', 'weekdays · 07:00'], ['Out', 'what it finds']], last: ['clock', 'Next run · tomorrow 07:00'],
        badge: '3 steps inside', inner: 'Inside · 3 micro nodes', tok: ['no model in the loop', ''],
        nodes: [['07:00', 'clock', 'trigger'], ['Same code', 'code', 'code'], ['Same answer', 'checkCircle', 'data']],
      },
    ]

    const win = kit.window({ title: 'Blocks · Library', right: h('span.k-chip.ai.sm', icon('layers', 12), 'macro elements'), w: 1440, h: 598 })
    const well = h('div.ru-well.k-well.k-dots')
    win.body.appendChild(well)

    // toolbar: a real library header
    const tools = h('div.ru-tools',
      h('div.lt', icon('layers', 14), h('span.k-label', 'Library · 7 blocks')),
      h('span.sp'),
      h('span.k-chip.brand.sm', 'All'), h('span.k-chip.sm', 'Data'), h('span.k-chip.sm', 'Testing'), h('span.k-chip.sm', 'People'),
      h('span.ru-search.k-recess', icon('search', 13), 'Search blocks'))
    well.appendChild(tools)

    const sigOf = (list) => {
      const el = h('div.ru-sig.k-recess.bp')
      list.forEach(([ic, kind], i) => {
        if (i) el.appendChild(h('span.l'))
        el.appendChild(h('span.s', { '--kc': `var(--k-${kind})` }, icon(ic, 12)))
      })
      return el
    }

    const grid = h('div.ru-grid')
    well.appendChild(grid)
    const blocks = BLOCKS.map((b) => {
      const col = h('div.ru-col')
      const box = h('div.ru-box')
      // the inside: the micro-node graph this block wraps
      const tray = h('div.ru-tray')
      const items = b.people || b.nodes
      const n = items.length
      const span = n === 4 ? 73 : 93
      const x0 = BW / 2 - (span * (n - 1)) / 2
      const xs = items.map((_, i) => x0 + i * span)
      const ed = kit.edges(BW, BH, xs.slice(1).map((x, i) => [xs[i] + 22, NY, x - 22, NY, 'hot']))
      tray.appendChild(ed.svg)
      const minis = items.map((it, i) => {
        let nd
        if (b.people) {
          nd = kit.node({ x: 0, y: 0, name: it[0], icon: 'user', kind: 'person' })
          const t = nd.querySelector('.t'); t.textContent = ''
          t.appendChild(h('span.k-av' + (it[2] ? '.' + it[2] : ''), it[1]))
          nd.appendChild(h('span.st', h('span.ru-ck', icon('check', 10))))
        } else {
          nd = kit.node({ x: 0, y: 0, name: it[0], icon: it[1], kind: it[2] })
        }
        const mw = h('div.ru-mw', { style: { left: xs[i] + 'px', top: NY + 'px' } }, nd)
        tray.appendChild(mw)
        return { mw, t: nd.querySelector('.t'), ck: nd.querySelector('.ru-ck') }
      })
      const foot = h('div.ru-foot.ru-in', h('span.k-label', b.inner), h('span.k-tok' + (b.tok[1] ? '.' + b.tok[1] : ''), b.tok[0]))
      tray.appendChild(foot)

      // the lid: the block's compact face
      const sig = sigOf(items.map((it) => (b.people ? ['user', 'person'] : [it[1], it[2]])))
      const badge = h('span.k-chip.brand.sm', icon('check', 11), b.badge)
      const lid = h('div.ru-lid',
        h('div.ru-top', h('span.k-icon-tile.lg', { '--kc': `var(--k-${b.kind})` }, icon(b.ic, 20)), h('div', h('div.ru-nm', b.name), h('div.ru-fx', b.fx))),
        h('div.ru-line', b.line),
        h('div.ru-io.k-recess', b.io.map(([k, v]) => h('div.r', h('span.k-label', k), h('span.k-tok', v)))),
        h('div.ru-last', icon(b.last[0], 14), b.last[1]),
        h('div.ru-bot', sig, badge))
      box.append(tray, lid)
      col.appendChild(box)
      let tag = null
      if (b.tag) { tag = h('div.ru-tag', h('span.k-chip.ai', icon('sparkles', 12), b.tag)); col.appendChild(tag) }
      grid.appendChild(col)
      return { col, box, lid, minis, paths: ed.paths, foot, sig, badge, tag }
    })

    // the strip of smaller blocks
    const nbToks = [h('span.k-tok.ai', 'data analysis'), h('span.k-tok.ai', 'testing')]
    const nbRing = h('span.ru-ring')
    const SMALL = [
      { name: 'Notebook', ic: 'notebook', kind: 'flow', line: 'the way in', rt: nbToks, ring: nbRing },
      { name: 'Reconciliation', ic: 'compare', kind: 'data', line: 'match two systems, flag the breaks', rt: [h('div.ru-sig.k-recess', h('span.s', { '--kc': 'var(--k-data)' }, icon('database', 12)), h('span.l'), h('span.s', { '--kc': 'var(--k-flow)' }, icon('compare', 12)), h('span.l'), h('span.s', { '--kc': 'var(--k-data)' }, icon('database', 12)))] },
      { name: 'App builder', ic: 'wand', kind: 'out', line: 'describe a screen, it builds a React app on a workflow', rt: [h('div.ru-sig.k-recess', h('span.s', { '--kc': 'var(--k-flow)' }, icon('sparkles', 12)), h('span.l'), h('span.s', { '--kc': 'var(--k-out)' }, icon('browser', 12)), h('span.l'), h('span.s', { '--kc': 'var(--k-code)' }, icon('workflow', 12)))] },
    ]
    const strip = h('div.ru-strip')
    const smalls = SMALL.map((s) => {
      const el = h('div.ru-sm.k-raised',
        s.ring || null,
        h('span.k-icon-tile.lg', { '--kc': `var(--k-${s.kind})` }, icon(s.ic, 20)),
        h('div.tx', h('div.nm', s.name), h('div.ln', s.line)),
        h('div.rt', s.rt))
      strip.appendChild(el)
      return el
    })
    well.appendChild(strip)

    const tilt = h('div.ru-tilt', win.el)
    area.appendChild(h('div.ru-stage', tilt))
    return { tilt, tools, blocks, smalls, nbToks, nbRing }
  },
  timeline(tl, kit, s) {
    // hero tilts in and settles
    tl.add(s.tilt, { opacity: [0, 1] }, 0.45, 0.9, 'out')
    tl.add(s.tilt, { rx: [26, 7], s: [0.9, 1], y: [40, 0] }, 0.35, 2.6, 'soft')
    kit.reveal(s.tools, { at: 0.95, y: 6, dur: 0.5 })
    kit.reveal(s.blocks.map((b) => b.col), { at: 1.0, stagger: 0.08, y: 16, dur: 0.6 })
    kit.reveal(s.smalls, { at: 1.35, stagger: 0.08, y: 10, dur: 0.5 })

    // each box opens, its inside builds, it closes and the face shows what it holds
    const T0 = 2.0, STEP = 1.8
    const REOPEN = 11.0
    s.blocks.forEach((b, i) => {
      const T = T0 + i * STEP
      tl.add(b.box, { '--open': [0, 1] }, T, 0.6, 'out')
      b.minis.forEach((m, k) => {
        const at = T + 0.3 + k * 0.12
        tl.add(m.mw, { '--a': [0, 1] }, at, 0.25, 'out')
        tl.add(m.t, { s: [0.7, 1] }, at, 0.5, 'pop')
        tl.add(m.mw, { z: [0, 22] }, at, 0.5, 'out')
        tl.add(m.mw, { z: [22, 0] }, T + 1.18, 0.3, 'io')
        if (m.ck) tl.add(m.ck, { s: [0.4, 1], opacity: [0, 1] }, at + 0.22, 0.4, 'pop')
      })
      b.paths.forEach((p, k) => kit.draw(p, { at: T + 0.42 + (k + 1) * 0.12, dur: 0.3, stagger: 0 }))
      tl.add(b.foot, { y: [6, 0] }, T + 0.75, 0.4, 'out')
      tl.add(b.box, { '--open': [1, 0] }, T + 1.25, 0.5, 'io')
      tl.step(T + 1.68, (on) => b.sig.classList.toggle('bp', !on))
      // the dashed signature turns solid with a small pop (it stays visible, dashed, before that)
      b.sig.querySelectorAll('.s').forEach((el, k) => {
        const at = T + 1.68 + k * 0.04
        tl.fn(at, 0.4, (p, t) => { const v = t < at ? '' : `scale(${(0.72 + 0.28 * p).toFixed(4)})`; if (el.style.transform !== v) el.style.transform = v }, 'pop')
      })
      kit.pop(b.badge, { at: T + 1.78, dur: 0.5 })
    })
    // the opening box sits above its neighbours while its lid is up
    const cols = s.blocks.map((b) => b.col)
    tl.fn(0, 0, (p, t) => cols.forEach((c, i) => {
      const T = T0 + i * STEP
      const on = (t >= T && t < T + 1.8) || (i === 0 && t >= REOPEN - 0.05)
      const z = on ? '2' : ''
      if (c.style.zIndex !== z) c.style.zIndex = z
    }))

    // the second line: the notebook is the way in, two blocks are the use cases
    tl.add(s.nbRing, { opacity: [0, 1] }, 9.6, 0.6, 'out')
    kit.pop(s.nbToks, { at: 9.8, stagger: 0.1 })
    const tags = s.blocks.filter((b) => b.tag).map((b) => b.tag)
    tags.forEach((t, k) => { tl.add(t, { opacity: [0, 1], y: [8, 0] }, 10.1 + k * 0.18, 0.5, 'out'); tl.add(t.firstChild, { s: [0.85, 1] }, 10.1 + k * 0.18, 0.55, 'pop') })

    // rest: Data check left half-open, its insides lifted a little
    const d = s.blocks[0]
    tl.add(d.box, { '--open': [0, 0.85] }, REOPEN, 1.1, 'soft')
    d.minis.forEach((m, k) => tl.add(m.mw, { z: [0, 12] }, REOPEN + 0.25 + k * 0.06, 0.8, 'out'))
  },
})
