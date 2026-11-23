/* 06 · Keep it running — "the notebook answer becomes a running thing".
 * One hero window, "Keep this answer": on the left the kept notebook answer and a Keep-as chooser
 * (Workflow / Test suite / Scheduled check); Scheduled check is picked and Keep is pressed. On the right the
 * scheduled workflow builds card by card in the dotted well, then the next runs (business days only) and the
 * last runs with identical result hashes, then the two guarantees: same code, same answer (notebook cell =
 * workflow rule, same source hash) and no model in the loop. A single run travels the graph and stops, gold,
 * at the step that asks a person first. */
Film.slide({
  index: 6,
  id: 'keep',
  name: 'Keep',
  word: 'Keep it',
  kicker: '06 · Keep it running',
  caption: 'Keep it, and it runs every business day exactly as you saw it.',
  dur: 12,
  css: `
    .s-keep .k-title { font-size: 46px; max-width: 1180px; }
    .s-keep .kp-stage { position: absolute; left: 6px; top: 6px; width: 1460px; }
    .s-keep .kp-tilt { transform-origin: 50% 0%; transform-style: preserve-3d; }
    .s-keep .kp-grid { display: grid; grid-template-columns: 380px 1fr; gap: 14px; height: 100%; min-height: 0; }

    /* ── left: the kept answer + Keep-as chooser ── */
    .s-keep .kp-left { display: flex; flex-direction: column; gap: 10px; min-width: 0; min-height: 0; }
    .s-keep .kp-src { display: flex; flex-direction: column; gap: 10px; padding: 12px 14px; }
    .s-keep .kp-src .row { display: flex; align-items: center; gap: 10px; }
    .s-keep .kp-src .tt { font-size: 13.5px; font-weight: 650; line-height: 1.2; }
    .s-keep .kp-src .mt { font-size: 12px; color: var(--muted); line-height: 1.3; }
    .s-keep .kp-src .toks { display: flex; gap: 6px; flex-wrap: wrap; }
    .s-keep .kp-src .ans { margin-left: auto; text-align: right; }
    .s-keep .kp-src .ans .v { font-size: 13px; font-weight: 650; }
    .s-keep .kp-lab { display: flex; align-items: center; justify-content: space-between; margin: 4px 2px -2px; }
    .s-keep .kp-opts { flex: 1; display: flex; flex-direction: column; gap: 8px; min-height: 0; }
    .s-keep .kp-opt { position: relative; display: grid; grid-template-columns: 30px 1fr 20px; gap: 12px; align-items: start; align-content: center; padding: 12px 14px; flex: 1; }
    .s-keep .kp-opt .tt { font-size: 13.5px; font-weight: 650; line-height: 1.25; }
    .s-keep .kp-opt .ds { margin-top: 3px; font-size: 12px; line-height: 1.4; color: var(--muted); }
    .s-keep .kp-opt .ft { margin-top: 8px; display: flex; gap: 6px; }
    .s-keep .kp-opt .rd { position: relative; margin-top: 6px; width: 18px; height: 18px; border-radius: 99px; box-shadow: inset 0 0 0 1.5px var(--input); background: var(--recess); }
    .s-keep .kp-opt .rd > span { position: absolute; inset: 0; border-radius: 99px; background: linear-gradient(135deg, var(--brand-1), var(--brand-2)); box-shadow: 0 0 0 4px var(--brand-soft); opacity: 0; }
    .s-keep .kp-opt .rd > span::after { content: ''; position: absolute; left: 6px; top: 6px; width: 6px; height: 6px; border-radius: 99px; background: #fff; }
    .s-keep .kp-opt .ring { position: absolute; inset: 0; border-radius: inherit; pointer-events: none; opacity: 0;
      box-shadow: 0 0 0 1.5px color-mix(in srgb, var(--primary) 55%, transparent), 0 14px 30px -16px var(--primary); }
    .s-keep .kp-act { display: flex; align-items: center; gap: 8px; margin-top: 2px; }
    .s-keep .kp-act .kb { display: inline-flex; }
    .s-keep .kp-act .kept { margin-left: auto; }

    /* ── right: graph, runs, guarantees ── */
    .s-keep .kp-right { display: grid; grid-template-rows: 218px 148px 1fr; gap: 12px; min-width: 0; min-height: 0; }
    .s-keep .kp-graph { overflow: hidden; }
    .s-keep .kp-ghead { position: absolute; left: 16px; right: 16px; top: 13px; display: flex; align-items: center; gap: 10px; }
    .s-keep .kp-ghead .tt { font-size: 13px; font-weight: 650; }
    .s-keep .kp-ghead .r { margin-left: auto; display: flex; gap: 6px; }
    .s-keep .kp-empty { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; gap: 8px; font-size: 13px; color: var(--faint); }
    .s-keep .kp-graph .k-ncard { height: 64px; width: 172px; white-space: normal; align-items: center; }
    .s-keep .kp-graph .k-ncard .m { max-width: 104px; }
    .s-keep .kp-graph .k-ncard.bp { background: transparent; box-shadow: none; outline: 1.5px dashed color-mix(in srgb, var(--primary) 55%, transparent); outline-offset: -1px; }
    .s-keep .kp-graph .k-ncard.bp > * { opacity: 0; }
    .s-keep .kp-graph .k-ncard .gold { position: absolute; inset: 0; border-radius: inherit; pointer-events: none; opacity: 0;
      box-shadow: 0 0 0 1.5px color-mix(in srgb, var(--warn) 70%, transparent), 0 12px 26px -14px var(--warn); }
    .s-keep .kp-graph .k-ncard .bdg { position: absolute; top: -10px; right: 10px; }
    .s-keep .kp-fx { position: absolute; top: 160px; width: 172px; text-align: center; font-family: var(--mono); font-size: 11px; color: var(--faint); white-space: nowrap; }
    .s-keep .kp-fx b { font-weight: 600; color: var(--muted); }
    .s-keep .kp-graph .k-pulse { left: 0; top: 0; opacity: 0; }

    .s-keep .kp-runs { display: grid; grid-template-columns: 452px 1fr; gap: 0; padding: 14px 0; }
    .s-keep .kp-runs > div { padding: 0 16px; min-width: 0; }
    .s-keep .kp-runs > div + div { border-left: 1px solid var(--hairline); }
    .s-keep .kp-runs .k-label { display: flex; align-items: center; justify-content: space-between; margin-bottom: 9px; }
    .s-keep .kp-runs .k-label span { text-transform: none; letter-spacing: 0; font-weight: 500; color: var(--faint); }
    .s-keep .kp-cal { display: grid; grid-template-columns: repeat(7, 1fr); gap: 8px; }
    .s-keep .kp-day { display: flex; flex-direction: column; align-items: center; gap: 3px; padding: 8px 0 9px; }
    .s-keep .kp-day .d { font-size: 11px; font-weight: 600; color: var(--muted); }
    .s-keep .kp-day .n { font-size: 17px; font-weight: 700; line-height: 1.1; letter-spacing: -0.02em; }
    .s-keep .kp-day .tm { font-family: var(--mono); font-size: 11px; font-weight: 600; color: var(--primary); }
    .s-keep .kp-day.off { background: transparent; box-shadow: inset 0 0 0 1px var(--hairline); }
    .s-keep .kp-day.off .n, .s-keep .kp-day.off .d { color: var(--faint); }
    .s-keep .kp-day.off .tm { color: var(--faint); font-family: var(--font); font-weight: 500; }
    .s-keep .kp-day.nx { --raised-tint: color-mix(in srgb, var(--primary) 45%, transparent); }
    .s-keep .kp-hist { display: flex; flex-direction: column; gap: 6px; }
    .s-keep .kp-run { display: grid; grid-template-columns: 18px 82px 34px 44px 1fr auto; align-items: center; gap: 8px; height: 28px; padding: 0 6px 0 10px; border-radius: var(--r-ctl); font-size: 12.5px; }
    .s-keep .kp-run > svg { color: var(--ok); }
    .s-keep .kp-run .dt { font-weight: 600; }
    .s-keep .kp-run .tm { font-family: var(--mono); font-size: 12px; color: var(--muted); }
    .s-keep .kp-run .sa { color: var(--muted); }

    .s-keep .kp-calls { display: grid; grid-template-columns: 1fr 392px; gap: 12px; min-height: 0; }
    .s-keep .kp-co { position: relative; display: flex; flex-direction: column; justify-content: center; gap: 12px; padding: 12px 16px; min-width: 0; }
    .s-keep .kp-co .hd { display: flex; align-items: flex-start; gap: 12px; }
    .s-keep .kp-co .hd .tt { font-size: 14px; font-weight: 650; line-height: 1.25; }
    .s-keep .kp-co .hd .ds { margin-top: 2px; font-size: 12px; line-height: 1.4; color: var(--muted); }
    .s-keep .kp-co .hd .k-chip { margin-left: auto; margin-top: 2px; }
    .s-keep .kp-cmp { display: grid; grid-template-columns: 1fr 34px 1fr; align-items: center; gap: 8px; }
    .s-keep .kp-cb { padding: 9px 12px 10px; min-width: 0; }
    .s-keep .kp-cb .lb { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 5px; font-size: 11px; font-weight: 600; color: var(--faint); }
    .s-keep .kp-cb .lb .k-tok { height: 20px; font-size: 11px; }
    .s-keep .kp-cb .k-code { font-variant-ligatures: none; padding: 0; font-size: 12px; line-height: 1.6; }
    .s-keep .kp-eq { display: flex; align-items: center; justify-content: center; width: 34px; height: 34px; border-radius: 99px; color: var(--ok-text); background: var(--ok-soft); font-size: 18px; font-weight: 700; }
    .s-keep .kp-nm-wrap { position: relative; }
    .s-keep .kp-nm-wrap { position: relative; display: inline-flex; flex: none; }
    .s-keep .kp-nm-wrap > .sk { position: absolute; left: 3px; top: 2px; width: 35px; height: 2px; border-radius: 2px; background: var(--ai-text); box-shadow: 0 0 0 1.5px var(--node-bg); transform-origin: 0 50%; rotate: 45deg; }
    .s-keep .kp-stats { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
    .s-keep .kp-stats .k-stat { padding: 10px 12px; }
    .s-keep .kp-stats .k-stat .v { font-size: 26px; }
  `,
  build(area, kit) {
    const { h, icon } = kit
    const win = kit.window({
      title: 'Keep this answer',
      right: h('span.k-chip.ok.sm', icon('notebook', 12), 'from Notebook · Stuck wires'),
      h: 612,
    })

    /* ── left ── */
    const src = h('div.kp-src.k-raised',
      h('div.row',
        h('span.k-icon-tile', { '--kc': 'var(--k-code)' }, icon('notebook', 16)),
        h('div', h('div.tt', 'Stuck wires'), h('div.mt', 'Notebook · 4 cells · ran today')),
        h('div.ans', h('div.v', '3 stuck'), h('div.mt', '$12,600.50 held'))),
      h('div.toks', ['DATA', 'QUERY', 'RULE', 'CHANGE'].map((t) => h('span.k-tok', t))))

    const OPTS = [
      ['workflow', 'Workflow', 'Run on demand or from another workflow.', [['play', 'on demand'], ['merge', 'as a sub-workflow']], 'var(--k-flow)'],
      ['flask', 'Test suite', 'Pass/fail every run; browser steps export as Playwright tests.', [['checkCircle', 'pass / fail'], ['download', 'Playwright']], 'var(--k-data)'],
      ['calendar', 'Scheduled check', 'Runs on a schedule, flags what it finds.', [['clock', 'every business day 07:00'], ['alert', 'flags findings']], 'var(--k-trigger)'],
    ]
    const opts = OPTS.map(([ic, tt, ds, chips, kc]) => {
      const dot = h('span')
      const ring = h('span.ring')
      const el = h('div.kp-opt.k-raised',
        h('span.k-icon-tile', { '--kc': kc }, icon(ic, 16)),
        h('div', h('div.tt', tt), h('div.ds', ds), h('div.ft', chips.map(([ci, ct]) => h('span.k-chip.sm', icon(ci, 11), ct)))),
        h('span.rd', dot), ring)
      return { el, dot, ring }
    })
    const keepBtn = h('span.k-btn', icon('check', 14), 'Keep')
    const keepWrap = h('span.kb', keepBtn)
    const kept = h('span.k-chip.ok.kept', icon('checkCircle', 13), 'Kept as a scheduled check')
    const act = h('div.kp-act', keepWrap, h('span.k-btn.ghost', 'Cancel'), kept)
    const lab = h('div.kp-lab', h('span.k-label', 'Keep as'), h('span.k-meta', 'pick one'))
    const left = h('div.kp-left', src, lab, h('div.kp-opts', opts.map((o) => o.el)), act)

    /* ── right: the scheduled workflow ── */
    const GW = 1042, GH = 218, CW = 172, CY = 80, STEP = (GW - 32 - CW) / 4
    const N = [
      ['Trigger', 'every business day 07:00', 'clock', 'trigger', 'none'],
      ['Query', 'runs on bank, read-only', 'database', 'data', 'database · read'],
      ['Rule', 'same code as the notebook', 'code', 'code', 'none'],
      ['Change', 'asks a person first', 'userCheck', 'person', 'a person'],
      ['Write', 'through bank_rw', 'database', 'data', 'database · write'],
    ]
    const graph = h('div.kp-graph.k-well.k-dots')
    const empty = h('div.kp-empty', icon('cursor', 15), 'Pick how to keep this answer')
    const ghead = h('div.kp-ghead',
      h('span.k-icon-tile', { '--kc': 'var(--k-trigger)' }, icon('calendar', 16)),
      h('span.tt', 'Scheduled check · Stuck wires'),
      h('span.r', h('span.k-chip.sm', icon('workflow', 11), '5 steps'), h('span.k-chip.brand.sm', icon('clock', 11), 'next run Tue 07:00')))
    const xs = N.map((_, i) => 16 + i * STEP)
    const ey = CY + 32
    const ed = kit.edges(GW, GH, xs.slice(0, 4).map((x, i) => [x + CW, ey, xs[i + 1], ey, i === 3 ? 'dash' : '']))
    graph.append(ed.svg, empty, ghead)
    const cards = N.map(([name, meta, ic, kind], i) => {
      const c = kit.ncard({ x: xs[i], y: CY, name, meta, icon: ic, kind, w: CW })
      graph.appendChild(c)
      return c
    })
    const fx = N.map(([, , , , e], i) => {
      const el = h('div.kp-fx', { style: { left: xs[i] + 'px' } }, 'effect · ', h('b', e))
      graph.appendChild(el)
      return el
    })
    const lockB = h('span.k-chip.sm.bdg', icon('lock', 11), 'read-only')
    cards[1].appendChild(lockB)
    const gold = h('span.gold')
    const waitB = h('span.k-chip.warn.sm.bdg', icon('userCheck', 11), 'waits for a person')
    cards[3].append(gold, waitB)
    const dot = h('span.k-pulse'); graph.appendChild(dot)

    /* ── right: next runs + last runs ── */
    const DAYS = [['Tue', '3'], ['Wed', '4'], ['Thu', '5'], ['Fri', '6'], ['Sat', '7', 1], ['Sun', '8', 1], ['Mon', '9']]
    const days = DAYS.map(([d, n, off], i) => h('div.kp-day.k-raised' + (off ? '.off' : '') + (i === 0 ? '.nx' : ''),
      h('span.d', d), h('span.n', n), h('span.tm', off ? 'no run' : '07:00')))
    const RUNS = ['Mon 2 Nov', 'Fri 30 Oct', 'Thu 29 Oct']
    const runs = RUNS.map((d) => h('div.kp-run.k-raised',
      icon('checkCircle', 15), h('span.dt', d), h('span.k-chip.ok.sm', 'ok'), h('span.tm', '07:00'), h('span.sa', 'same answer'), h('span.k-tok', icon('hash', 11), 'sha 9f3c…')))
    const runsEl = h('div.kp-runs.k-well',
      h('div', h('div.k-label', 'Next runs', h('span', 'business days only')), h('div.kp-cal', days)),
      h('div', h('div.k-label', 'Last runs', h('span', 'result hash')), h('div.kp-hist', runs)))

    /* ── right: the two guarantees ── */
    const codeA = kit.code([[['wires', ''], ['.', ''], ['filter', 'f'], ['(w ', ''], ['=>', 'k'], [' w.amount ', ''], ['>', 'k'], [' ', ''], ['50', 'n'], [')', '']]], { gutter: false })
    const codeB = kit.code([[['wires', ''], ['.', ''], ['filter', 'f'], ['(w ', ''], ['=>', 'k'], [' w.amount ', ''], ['>', 'k'], [' ', ''], ['50', 'n'], [')', '']]], { gutter: false })
    const srcA = h('span.k-tok', 'src a41e…'), srcB = h('span.k-tok', 'src a41e…')
    const cbA = h('div.kp-cb.k-raised', h('div.lb', h('span', 'Notebook · RULE cell'), srcA), codeA.el)
    const cbB = h('div.kp-cb.k-raised', h('div.lb', h('span', 'Workflow · Rule step'), srcB), codeB.el)
    const eq = h('span.kp-eq', '=')
    const ident = h('span.k-chip.ok', icon('check', 13), 'identical')
    const same = h('div.kp-co.k-well',
      h('div.hd', h('span.k-icon-tile', { '--kc': 'var(--k-code)' }, icon('compare', 16)),
        h('div', h('div.tt', 'Same code, same answer'), h('div.ds', 'The rule node runs the exact source the notebook ran.')), ident),
      h('div.kp-cmp', cbA, eq, cbB))

    const strike = h('span.sk')
    const noAI = h('span.k-chip.brand', icon('cpu', 13), 'no AI at run time')
    const z1 = h('span.v', '0'), z2 = h('span.v', '5 of 5')
    const nomodel = h('div.kp-co.k-well',
      h('div.hd', h('span.kp-nm-wrap', h('span.k-icon-tile', { '--kc': 'var(--ai)' }, icon('sparkles', 16)), strike),
        h('div', h('div.tt', 'No model in the loop'), h('div.ds', 'Nothing is regenerated.')), noAI),
      h('div.kp-stats',
        h('div.k-stat.k-raised', z1, h('span.l', 'model calls per run')),
        h('div.k-stat.k-raised', z2, h('span.l', 'steps run as kept'))))

    const right = h('div.kp-right', graph, runsEl, h('div.kp-calls', same, nomodel))
    win.body.appendChild(h('div.kp-grid', left, right))

    const tilt = h('div.kp-tilt', win.el)
    area.appendChild(h('div.kp-stage', tilt))
    return { tilt, src, lab, opts, act, keepWrap, kept, empty, ghead, cards, fx, lockB, gold, waitB, paths: ed.paths, dot,
      runsEl, days, runs, same, cbA, cbB, codeA, codeB, srcA, srcB, eq, ident, nomodel, strike, noAI, stats: [z1.parentNode, z2.parentNode] }
  },
  timeline(tl, kit, s) {
    // hero tilts in and settles at 7deg
    tl.add(s.tilt, { opacity: [0, 1] }, 0.5, 0.9, 'out')
    tl.add(s.tilt, { rx: [26, 7], s: [0.9, 1], y: [40, 0] }, 0.45, 2.6, 'soft')

    // the kept answer and the chooser
    kit.reveal(s.src, { at: 0.9, y: 8, dur: 0.5 })
    kit.reveal(s.lab, { at: 1.0, y: 6, dur: 0.4 })
    kit.reveal(s.opts.map((o) => o.el), { at: 1.05, stagger: 0.12, y: 10, dur: 0.55 })
    kit.reveal(s.act, { at: 1.5, y: 6, dur: 0.45 })

    // Scheduled check is picked
    const pick = s.opts[2]
    tl.add(pick.ring, { opacity: [0, 1] }, 2.4, 0.35, 'out')
    tl.add(pick.dot, { opacity: [0, 1] }, 2.4, 0.2, 'out')
    tl.add(pick.dot, { s: [0.3, 1] }, 2.4, 0.45, 'pop')
    tl.add(pick.el, { s: [1, 1.012] }, 2.4, 0.3, 'spring')
    s.opts.slice(0, 2).forEach((o) => tl.add(o.el, { opacity: [1, 0.55] }, 2.5, 0.45, 'out'))

    // Keep is pressed
    tl.add(s.keepWrap, { s: [1, 0.93] }, 3.4, 0.12, 'out')
    tl.add(s.keepWrap, { s: [0.93, 1] }, 3.52, 0.35, 'spring')
    kit.pop(s.kept, { at: 3.62 })
    tl.add(s.empty, { opacity: [1, 0] }, 3.5, 0.3, 'out')
    kit.reveal(s.ghead, { at: 3.65, y: 6, dur: 0.45 })

    // the scheduled workflow builds: blueprint, then solid; edges draw once both ends exist
    const T0 = 3.9, TICK = 0.3
    s.cards.forEach((c, i) => {
      const tb = T0 + i * TICK, ts = tb + TICK
      tl.add(c, { opacity: [0, 1] }, tb, 0.18, 'out')
      tl.step(ts, (on) => c.classList.toggle('bp', !on))
      tl.add(c, { s: [0.9, 1] }, ts, 0.5, 'pop')
      kit.reveal(s.fx[i], { at: ts + 0.05, y: 4, dur: 0.4 })
      if (i > 0) kit.draw(s.paths[i - 1], { at: ts + 0.04, dur: 0.4, stagger: 0 })
    })
    kit.pop(s.lockB, { at: T0 + 2 * TICK + 0.1 })

    // next runs, then the last runs with the same result hash
    kit.reveal(s.runsEl, { at: 5.7, y: 8, dur: 0.5 })
    kit.pop(s.days, { at: 6.0, stagger: 0.08, from: 0.8 })
    kit.reveal(s.runs, { at: 7.0, stagger: 0.16, y: 6, dur: 0.45 })
    s.runs.forEach((r, i) => kit.pop(r.querySelector('.k-tok'), { at: 7.25 + i * 0.16 }))

    // same code, same answer
    kit.reveal(s.same, { at: 8.0, y: 8, dur: 0.5 })
    kit.reveal([s.cbA, s.cbB], { at: 8.15, stagger: 0.1, y: 6, dur: 0.4 })
    tl.add(s.codeA.lines[0], { clip: [0, 1] }, 8.25, 0.55, 'linear')
    tl.add(s.codeB.lines[0], { clip: [0, 1] }, 8.55, 0.55, 'linear')
    kit.pop([s.srcA, s.srcB], { at: 8.85, stagger: 0.1 })
    kit.pop(s.eq, { at: 9.0, from: 0.5 })
    kit.pop(s.ident, { at: 9.15 })

    // no model in the loop
    kit.reveal(s.nomodel, { at: 9.4, y: 8, dur: 0.5 })
    tl.add(s.strike, { sx: [0, 1] }, 9.75, 0.35, 'out')
    kit.reveal(s.stats, { at: 9.7, stagger: 0.1, y: 6, dur: 0.4 })
    kit.pop(s.noAI, { at: 10.0 })

    // one run travels the graph and stops at the step that asks a person first
    const TR = 10.3, TD = 1.26
    tl.fn(TR, TD, (p, t) => {
      const k = Math.min(2, Math.floor(p * 3)), q = kit.pointAt(s.paths[k], p * 3 - k)
      s.dot.style.left = q.x + 'px'; s.dot.style.top = q.y + 'px'
      s.dot.style.opacity = t < TR || t > TR + TD + 0.15 ? '0' : '1'
    }, 'io')
    tl.add(s.gold, { opacity: [0, 1] }, 11.45, 0.4, 'out')
    kit.pop(s.waitB, { at: 11.5 })
  },
})
