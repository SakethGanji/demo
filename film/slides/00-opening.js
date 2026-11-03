/* 00 · Opening — "It started as a workflow builder." The c6f429f hero, as it was: the headline over the 3D glass
 * window, which tilts in and settles while the AI builder replays a build (ask → plan → nodes land one by one,
 * a dashed blueprint first, then the solid node → review with Keep). Background is still. */
Film.slide({
  index: 0,
  id: 'opening',
  name: 'Opening',
  word: 'Where it started',
  head: false,
  enter: 'fade',
  dur: 7.2,
  css: `
    .s-opening .op-head { position: absolute; left: 120px; right: 120px; top: 64px; display: flex; flex-direction: column; align-items: center; gap: 16px; text-align: center; }
    .s-opening .op-l1 { margin: 0; font-size: 76px; line-height: 1.0; font-weight: 720; letter-spacing: -0.045em; }
    .s-opening .op-l2 { margin: 0; font-size: 30px; line-height: 1.2; font-weight: 600; letter-spacing: -0.025em; color: var(--muted); }
    .s-opening .op-stage { position: absolute; left: 50%; top: 250px; width: 1180px; margin-left: -590px; perspective: 1600px; }
    .s-opening .op-tilt { transform-origin: 50% 0%; transform-style: preserve-3d; }
    .s-opening .op-grid { display: grid; grid-template-columns: 1fr 292px; gap: 12px; height: 496px; }
    .s-opening .op-canvas { position: relative; overflow: hidden; }
    .s-opening .op-side { display: flex; flex-direction: column; gap: 10px; min-width: 0; min-height: 0; overflow: hidden; }
    .s-opening .op-ask { padding: 12px 14px; font-size: 13px; line-height: 1.45; }
    .s-opening .op-ask .who { display: flex; align-items: center; gap: 6px; margin-bottom: 5px; font-size: 11px; font-weight: 600; color: var(--ai-text); }
    .s-opening .op-review { display: flex; flex-direction: column; gap: 8px; padding: 12px 14px; }
    .s-opening .op-review .r { display: flex; align-items: center; gap: 7px; font-size: 12.5px; font-weight: 500; }
    .s-opening .op-review .ok { color: var(--ok-text); }
    .s-opening .op-review .warn { color: var(--warn-text); font-size: 12px; }
    .s-opening .op-review .btns { display: flex; gap: 6px; margin-top: 2px; }
    .s-opening .op-tests { flex: 1; display: flex; flex-direction: column; gap: 2px; padding: 10px 12px; }
    .s-opening .op-tests .k-label { margin: 0 2px 6px; }
    .s-opening .op-tests .tr { display: flex; align-items: center; gap: 8px; height: 24px; padding: 0 2px; font-size: 12px; color: var(--fg); }
    .s-opening .op-tests .tr svg { color: var(--ok); }
  `,
  build(area, kit, root) {
    const { h, icon } = kit
    const l1 = h('h1.op-l1', 'It started as a workflow builder.')
    const l2 = h('p.op-l2', 'Then its engine became the base for everything else.')
    root.appendChild(h('div.op-head', l1, l2))

    const win = kit.window({ title: 'New workflow · Build', right: h('span.k-chip.ai.sm', icon('sparkles', 12), 'AI') })
    const grid = h('div.op-grid')
    win.body.appendChild(grid)

    // the canvas: the generic request workflow from c6f429f fixtures.ts, on an 8 × 4 grid
    const CW = 1180 - 24 - 292 - 12, CH = 496
    const canvas = h('div.op-canvas.k-well.k-dots', { style: { height: CH + 'px' } })
    const COLS = 8, ROWS = 4
    const at = (c, r) => [((c + 0.5) / COLS) * CW, ((r + 0.5) / ROWS) * CH]
    const N = [
      ['New request', 'webhook', 'trigger', 0, 1],
      ['Validate', 'layers', 'code', 1, 1],
      ['Needs review?', 'branch', 'flow', 2, 1],
      ['Approval', 'userCheck', 'person', 3, 2],
      ['Call API', 'globe', 'out', 4, 1],
      ['Succeeded?', 'branch', 'flow', 5, 1],
      ['Save record', 'database', 'data', 6, 0],
      ['Notify team', 'mail', 'out', 4, 3],
      ['Log error', 'layers', 'code', 6, 2],
      ['Respond', 'send', 'trigger', 7, 1],
    ]
    const E = [[0, 1], [1, 2], [2, 4], [2, 3], [3, 4], [3, 7], [4, 5], [5, 6], [5, 8], [6, 9], [7, 9], [8, 9]]
    const ed = kit.edges(CW, CH, E.map(([a, b]) => [...at(N[a][3], N[a][4]), ...at(N[b][3], N[b][4])]))
    canvas.appendChild(ed.svg)
    const nodes = N.map(([name, ic, kind, c, r]) => {
      const [x, y] = at(c, r)
      const el = kit.node({ x, y, name, icon: ic, kind })
      canvas.appendChild(el)
      return el
    })

    // the build panel
    const ask = h('div.op-ask.k-raised', h('div.who', icon('sparkles', 12), 'You asked'), 'Handle new requests, send the risky ones for approval.')
    const steps = kit.stepper([{ title: 'Ask' }, { title: 'Plan', meta: '' }, { title: 'Build', meta: '' }, { title: 'Review' }])
    const review = h('div.op-review.k-raised',
      h('div.r.ok', icon('flask', 14), '6 of 6 tests passed'),
      h('div.r.warn', icon('globe', 14), 'New address: api.example.com'),
      h('div.btns', h('span.k-btn.sm', icon('check', 12), 'Keep'), h('span.k-btn.sm.ghost', 'Discard')))
    const TESTS = ['Risky request waits for approval', 'API failure is logged']
    const testRows = TESTS.map((t) => h('div.tr', icon('checkCircle', 14), t))
    const tests = h('div.op-tests.k-well', h('div.k-label', 'Tests · locked in the plan'), testRows)
    grid.append(canvas, h('div.op-side', ask, steps.el, review, tests))

    const tilt = h('div.op-tilt', win.el)
    root.appendChild(h('div.op-stage', tilt))
    return { l1, l2, tilt, ask, steps, review, tests, testRows, nodes, paths: ed.paths, E }
  },
  timeline(tl, kit, s) {
    kit.kinetic(s.l1, { at: 0.15, stagger: 0.08, dur: 0.95 })
    kit.kinetic(s.l2, { at: 5.7, stagger: 0.05, dur: 0.85 })

    // the hero tilts in and settles (c6f429f: rotateX 26 → 0, scale .9 → 1, y 40 → 0); it rests at a slight tilt
    tl.add(s.tilt, { opacity: [0, 1] }, 0.4, 0.9, 'out')
    tl.add(s.tilt, { rx: [26, 7], s: [0.9, 1], y: [40, 0] }, 0.3, 2.6, 'soft')

    // ask → plan → build → review
    const T_ASK = 1.0, T_PLAN = 1.6, T_BUILD = 2.1, TICK = 0.3
    const T_REVIEW = T_BUILD + s.nodes.length * TICK + 0.25
    kit.reveal(s.ask, { at: 0.9, y: 8, dur: 0.5 })
    tl.fn(0, 0, (p, t) => {
      const phase = t < T_ASK ? -1 : t < T_PLAN ? 0 : t < T_BUILD ? 1 : t < T_REVIEW ? 2 : 4
      s.steps.set(phase)
      const built = Math.max(0, Math.min(s.nodes.length, Math.floor((t - T_BUILD) / TICK) + 1))
      s.steps.items[1].querySelector('.mt').textContent = t >= T_PLAN ? '6 steps · tests locked' : ''
      s.steps.items[2].querySelector('.mt').textContent = t >= T_BUILD ? `${t >= T_REVIEW ? s.nodes.length : built} of ${s.nodes.length} steps` : ''
    })
    s.nodes.forEach((el, i) => {
      const tb = T_BUILD + i * TICK - 0.12, ts = T_BUILD + (i + 1) * TICK - 0.12
      tl.add(el, { opacity: [0, 1] }, tb, 0.18, 'out')
      tl.step(ts, (on) => el.classList.toggle('blueprint', !on))
      tl.add(el.querySelector('.t'), { s: [0.86, 1] }, ts, 0.5, 'pop')
      tl.add(el.querySelector('.n'), { opacity: [0, 1] }, ts, 0.3, 'out')
    })
    s.E.forEach(([a, b], k) => {
      const ts = T_BUILD + (Math.max(a, b) + 1) * TICK - 0.07
      kit.draw(s.paths[k], { at: ts, dur: 0.45, stagger: 0 })
    })
    kit.reveal(s.tests, { at: T_PLAN + 0.1, y: 6, dur: 0.45 })
    tl.stagger(s.testRows, { opacity: [0.35, 1] }, T_REVIEW - 0.2, 0.12, 0.3, 'out')
    s.testRows.forEach((r, i) => tl.add(r.querySelector('svg'), { s: [0.5, 1], opacity: [0, 1] }, T_REVIEW - 0.2 + i * 0.12, 0.4, 'pop'))
    kit.reveal(s.review, { at: T_REVIEW + 0.35, y: 6, dur: 0.45 })
  },
})
