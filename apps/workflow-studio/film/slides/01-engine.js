/* 01 · Engine — "Take away the workflow layer. This is what it can do."
 * The Opening's workflow builder window arrives as it was left. Its node graph lifts off the canvas, then the whole
 * window swings up and away like a lid, and underneath sits the engine core: three meshing glass gears on a dotted
 * glass disc (the canvas dots stay behind). The gears spin up and keep turning slowly at rest, leaning with the
 * pointer. Eight real capabilities from apps/workflow-engine light up one by one, each wired to the core (the gears
 * kick each time one connects). Last, the same core drives a bar of the things built on it: Workflows, Notebook,
 * Tests, Scheduled checks, App builder.
 *
 * Kit additions scoped here: 3D extruded glass gears (gear outline + lightening holes as clip-path, three stacked back
 * faces for thickness), and a slow CSS idle turn on the gears (stopped under .k-still / reduced motion, like the kit
 * halo), so the core keeps turning while the speaker talks. */
Film.slide({
  index: 1,
  id: 'engine',
  name: 'Engine',
  word: 'The engine',
  handoffFrom: 0, // starts on the Opening's last frame: the same window, in the same place
  kicker: '01 · The engine underneath',
  caption: 'Take away the workflow layer. This is what it can do.',
  headAt: 0.45,
  dur: 11.6,
  css: `
    .s-engine .en-world, .s-engine .en-lean, .s-engine .en-plane, .s-engine .en-gear, .s-engine .en-rot, .s-engine .en-spin, .s-engine .en-ph { transform-style: preserve-3d; }
    .s-engine .en-world { position: absolute; inset: 0; }

    /* ── the workflow layer: the Opening's window, as it was left ── */
    .s-engine .wf-tilt { position: absolute; left: 146px; top: 36px; width: 1180px; transform-origin: 50% 0%; transform-style: preserve-3d; }
    .s-engine .wf-grid { display: grid; grid-template-columns: 1fr 292px; gap: 12px; height: 496px; }
    .s-engine .wf-canvas { position: relative; overflow: hidden; }
    .s-engine .wf-side { display: flex; flex-direction: column; gap: 10px; min-width: 0; min-height: 0; overflow: hidden; }
    .s-engine .wf-ask { padding: 12px 14px; font-size: 13px; line-height: 1.45; }
    .s-engine .wf-ask .who { display: flex; align-items: center; gap: 6px; margin-bottom: 5px; font-size: 11px; font-weight: 600; color: var(--ai-text); }
    .s-engine .wf-review { display: flex; flex-direction: column; gap: 8px; padding: 12px 14px; }
    .s-engine .wf-review .r { display: flex; align-items: center; gap: 7px; font-size: 12.5px; font-weight: 500; }
    .s-engine .wf-review .ok { color: var(--ok-text); }
    .s-engine .wf-review .warn { color: var(--warn-text); font-size: 12px; }
    .s-engine .wf-review .btns { display: flex; gap: 6px; margin-top: 2px; }
    .s-engine .wf-tests { flex: 1; display: flex; flex-direction: column; gap: 2px; padding: 10px 12px; }
    .s-engine .wf-tests .k-label { margin: 0 2px 6px; }
    .s-engine .wf-tests .tr { display: flex; align-items: center; gap: 8px; height: 24px; padding: 0 2px; font-size: 12px; color: var(--fg); }
    .s-engine .wf-tests .tr svg { color: var(--ok); }
    /* the node graph rides on its own sheet over the canvas, so it can lift off; it leans exactly like .k-lean */
    .s-engine .wf-sheet { position: absolute; inset: 0; pointer-events: none; transform-origin: 50% 50%;
      transform: rotateX(calc(var(--ly) * -2.2deg)) rotateY(calc(var(--lx) * 2.8deg)); transform-style: preserve-3d; }
    .s-engine .wf-shade { position: absolute; inset: 0; filter: blur(7px); opacity: 0; }
    .s-engine .wf-shade svg path { stroke: var(--brand-1); stroke-opacity: .35; stroke-width: 3; }
    .s-engine .wf-shade i { position: absolute; width: 36px; height: 36px; border-radius: 8px; background: color-mix(in srgb, var(--brand-1) 30%, transparent); }
    .s-engine .wf-lift { position: absolute; left: 12px; top: 52px; width: 852px; height: 496px; }

    /* ── the engine: one tilted plane that leans with the pointer ── */
    /* engine glass, scoped: clear gears (rotation-invariant body + a fixed light that counter-rotates), circle refraction */
    .s-engine {
      --en-body: radial-gradient(closest-side, rgb(232 240 255 / .34), rgb(214 228 255 / .22) 58%, rgb(176 202 252 / .40) 86%, rgb(236 243 255 / .70));
      --en-rim: rgb(64 104 206 / .50);
      --en-lite: linear-gradient(calc(140deg + var(--lx) * 26deg), rgb(255 255 255 / .78) 6%, rgb(255 255 255 / .22) 30%, transparent 50%, rgb(150 170 245 / .10) 78%, rgb(150 165 255 / .24));
      --en-back: rgb(130 156 236 / .09);
      --en-back-rim: rgb(90 120 210 / .26);
      --en-edge-glow: rgb(255 255 255 / .70);
      --en-shade: rgb(40 70 170 / .16);
      --en-circ-edge: radial-gradient(closest-side, transparent 84%, rgb(255 255 255 / .26) 96%, rgb(255 255 255 / .55));
      --en-line-glow: color-mix(in srgb, var(--brand-2) 30%, transparent);
    }
    :root[data-theme="dark"] .s-engine {
      --en-body: radial-gradient(closest-side, rgb(150 182 255 / .10), rgb(130 165 255 / .05) 58%, rgb(140 175 255 / .14) 86%, rgb(200 218 255 / .30));
      --en-rim: var(--gear-rim);
      --en-lite: linear-gradient(calc(140deg + var(--lx) * 26deg), rgb(215 228 255 / .38) 6%, rgb(200 215 255 / .08) 30%, transparent 50%, rgb(139 125 255 / .07) 78%, rgb(139 125 255 / .18));
      --en-back: rgb(150 165 255 / .045);
      --en-back-rim: rgb(139 125 255 / .30);
      --en-edge-glow: rgb(205 222 255 / .34);
      --en-shade: rgb(0 3 16 / .55);
      --en-circ-edge: radial-gradient(closest-side, transparent 84%, rgb(150 175 255 / .05) 96%, rgb(170 190 255 / .12));
      --en-line-glow: color-mix(in srgb, var(--brand-1) 34%, transparent);
    }
    .s-engine .en-lean { position: absolute; inset: 0; transform-origin: 50% 45%;
      transform: rotateX(calc(var(--ly) * -3.2deg)) rotateY(calc(var(--lx) * 4.6deg)); }
    .s-engine .en-plane { position: absolute; left: 0; top: 0; width: 1472px; height: 630px; transform-origin: 50% 40%; }
    .s-engine .en-lines { position: absolute; left: 0; top: 0; width: 1472px; height: 630px; }
    /* connectors: a hairline core over a soft glow underlay (no filter), a comet light runs along each as it connects */
    .s-engine .en-lines path { stroke: var(--primary); stroke-opacity: .55; stroke-width: 1.1; }
    .s-engine .en-lines path.glow { stroke: var(--en-line-glow); stroke-opacity: .5; stroke-width: 5; }
    .s-engine .en-lines path.lit { stroke: var(--brand-2); stroke-opacity: 1; stroke-width: 2; opacity: 0; }
    :root[data-theme="dark"] .s-engine .en-lines path.lit { stroke: color-mix(in srgb, var(--brand-1) 55%, white); }
    .s-engine .en-lines path.fan { stroke-dasharray: none; }

    /* the base disc: frosted clear glass (kit finish stack), the canvas dots, a machined inner ring, the halo rim */
    .s-engine .en-disc { position: absolute; border-radius: 50%;
      background: var(--grain) 0 0 / 180px 180px, var(--en-circ-edge), radial-gradient(circle, var(--grid-dot) .8px, transparent 1.2px) 0 0 / 16px 16px, var(--glass-tint), var(--glass-fill);
      box-shadow: var(--glass-elev), var(--hero-depth);
      -webkit-backdrop-filter: blur(8px) saturate(140%); backdrop-filter: blur(8px) saturate(140%); }
    .s-engine .en-disc::before { content: ''; position: absolute; inset: 34px; border-radius: 50%;
      box-shadow: inset 0 0 0 1px var(--hairline), inset 0 1px 0 var(--hi-line), 0 1px 0 color-mix(in srgb, var(--hi-line) 60%, transparent); }
    .s-engine .en-disc::after { content: ''; position: absolute; inset: 0; border-radius: 50%; padding: 1px; pointer-events: none; background: var(--rim-spec), var(--glass-rim);
      -webkit-mask: linear-gradient(#000 0 0) content-box, linear-gradient(#000 0 0); -webkit-mask-composite: xor; mask: linear-gradient(#000 0 0) content-box exclude, linear-gradient(#000 0 0); }
    .s-engine .en-disc .k-halo { border-radius: 50%; }
    .s-engine .en-name { position: absolute; left: 0; right: 0; bottom: 30px; display: flex; justify-content: center; }
    .s-engine .en-name .k-label { display: inline-flex; align-items: center; gap: 7px; color: var(--muted); letter-spacing: .08em; }
    .s-engine .en-name .k-label i { width: 6px; height: 6px; border-radius: 99px; background: linear-gradient(135deg, var(--brand-1), var(--brand-2)); box-shadow: 0 0 0 3px var(--brand-soft); }

    /* gears: clear glass. Front face = a rotation-invariant body (edges brighter, like light bending at a curved edge)
       clipped to the gear outline, with a fixed top-left light inside it that counter-rotates so the light stays put
       while the teeth turn; an inner edge glow (wide stroke clipped to the face); crisp rims; two faint back faces. */
    .s-engine .en-gear { position: absolute; }
    .s-engine .en-rot, .s-engine .en-spin, .s-engine .en-ph { position: absolute; inset: 0; }
    .s-engine .en-spin { animation: en-turn var(--per) linear infinite; animation-direction: var(--dir); will-change: transform; }
    .s-engine .en-cspin { position: absolute; inset: 0; animation: en-turn var(--per) linear infinite; animation-direction: var(--cdir); }
    @keyframes en-turn { to { transform: rotate(360deg); } }
    .s-engine .en-face { position: absolute; inset: 0; }
    .s-engine .en-face.front { background: var(--en-body); overflow: hidden; }
    .s-engine .en-lc, .s-engine .en-lc0 { position: absolute; inset: 0; }
    .s-engine .en-lite { position: absolute; inset: -22%; background: var(--en-lite); }
    .s-engine .en-face.back { background: var(--en-back); }
    .s-engine .en-rim { position: absolute; inset: 0; overflow: visible; }
    .s-engine .en-glow { position: absolute; inset: 0; }
    .s-engine .en-glow path { fill: none; stroke: var(--en-edge-glow); stroke-width: 7; stroke-opacity: .32; }
    .s-engine .en-glow path.in { stroke-width: 3; stroke-opacity: .55; }
    .s-engine .en-rim .r1 { fill: none; stroke: var(--en-rim); stroke-width: 1.2; }
    .s-engine .en-rim .r2 { fill: none; stroke: var(--en-back-rim); stroke-width: 1; }
    .s-engine .en-rim .r3 { fill: none; stroke: var(--gear-rim-2); stroke-width: 1; stroke-dasharray: 2 5; }
    .s-engine .en-shadow { position: absolute; border-radius: 50%; background: radial-gradient(closest-side, var(--en-shade), transparent); }
    .s-engine .en-hub { position: absolute; border-radius: 50%; display: flex; flex-direction: column; align-items: center; justify-content: center;
      background: radial-gradient(120% 90% at 32% 18%, color-mix(in srgb, var(--hi-line) 45%, transparent), transparent 55%), var(--glass-tint), color-mix(in srgb, var(--glass-fill) 70%, var(--bg));
      box-shadow: inset 0 1px 0 var(--hi-line), inset 0 0 0 1px color-mix(in srgb, var(--primary) 22%, transparent), inset 0 -1px 0 color-mix(in srgb, var(--brand-2) 22%, transparent),
        0 1px 2px rgb(10 20 60 / .12), 0 14px 30px -16px var(--primary); }
    .s-engine .en-hub b { font-size: 34px; line-height: 1; font-weight: 720; letter-spacing: -0.04em; color: var(--fg); font-variant-numeric: tabular-nums; }
    .s-engine .en-hub span { margin-top: 5px; font-size: 11px; font-weight: 600; letter-spacing: .05em; text-transform: uppercase; color: var(--muted); }
    .s-engine .en-hub.sm::after { content: ''; width: 10px; height: 10px; border-radius: 99px; background: radial-gradient(circle at 38% 32%, rgb(255 255 255 / .6), transparent 55%), linear-gradient(135deg, var(--brand-1), var(--brand-2)); box-shadow: 0 0 0 4px var(--brand-soft); }

    /* capability modules: the kit liquid-glass finish, hand-rolled (no frost on eight cards) */
    .s-engine .en-cap { --on: 0; position: absolute; width: 340px; height: 76px; display: flex; align-items: center; gap: 13px; padding: 0 18px 0 14px; border-radius: 14px;
      background: var(--glass-spec) 50% 0 / 100% 1px no-repeat, var(--grain) 0 0 / 180px 180px, var(--glass-edge), var(--glass-tint), var(--glass-fill);
      box-shadow: inset 0 1px 0 var(--hi-line), inset 0 0 0 .5px color-mix(in srgb, var(--hi-line) 60%, transparent), var(--drop),
        0 0 0 1px color-mix(in srgb, var(--primary) calc(var(--on) * 34%), var(--raised-ring)),
        0 18px 36px -22px color-mix(in srgb, var(--primary) calc(var(--on) * 90%), transparent); }
    .s-engine .en-cap::after { content: ''; position: absolute; inset: 0; border-radius: inherit; padding: 1px; pointer-events: none; background: var(--rim-spec), var(--glass-rim);
      -webkit-mask: linear-gradient(#000 0 0) content-box, linear-gradient(#000 0 0); -webkit-mask-composite: xor; mask: linear-gradient(#000 0 0) content-box exclude, linear-gradient(#000 0 0); }
    .s-engine .en-cap .k-icon-tile { box-shadow: inset 0 0 0 .5px color-mix(in srgb, var(--kc) calc(22% + var(--on) * 14%), transparent), inset 0 1px 0 var(--hi-line), 0 0 14px -4px color-mix(in srgb, var(--kc) calc(var(--on) * 40%), transparent); }
    .s-engine .en-cap .tx { min-width: 0; flex: 1; }
    .s-engine .en-cap .nm { font-size: 14px; font-weight: 650; line-height: 1.25; letter-spacing: -0.01em; color: var(--fg); white-space: nowrap; }
    .s-engine .en-cap .mt { margin-top: 3px; font-size: 12px; line-height: 1.35; color: var(--muted); white-space: nowrap; }
    .s-engine .en-led { position: absolute; top: 12px; right: 12px; width: 7px; height: 7px; border-radius: 99px;
      background: radial-gradient(circle at 38% 32%, rgb(255 255 255 / calc(var(--on) * .55)), transparent 60%), color-mix(in srgb, var(--primary) calc(var(--on) * 100%), var(--input));
      box-shadow: 0 0 0 calc(var(--on) * 3px) var(--brand-soft), 0 0 8px calc(var(--on) * 1px) color-mix(in srgb, var(--primary) calc(var(--on) * 45%), transparent); }
    /* the socket where a connector lands on the card's inner edge */
    .s-engine .en-port { position: absolute; top: 50%; width: 9px; height: 9px; margin: -4.5px 0 0 -4.5px; border-radius: 99px;
      background: radial-gradient(circle, color-mix(in srgb, var(--primary) calc(var(--on) * 100%), var(--input)) 0 2px, transparent 2.6px), var(--glass-fill);
      box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--primary) calc(20% + var(--on) * 40%), var(--raised-ring)), 0 0 8px color-mix(in srgb, var(--primary) calc(var(--on) * 40%), transparent); }

    /* the bar of what runs on the core */
    .s-engine .en-pow { position: absolute; display: flex; align-items: center; gap: 10px; padding: 10px 12px; }
    .s-engine .en-pow .lab { flex: none; width: 150px; padding: 0 6px 0 6px; }
    .s-engine .en-pow .lab .k-label { margin: 0 0 3px; }
    .s-engine .en-pow .lab div { font-size: 13px; font-weight: 600; line-height: 1.3; color: var(--fg); }
    .s-engine .en-tile { --on: 0; flex: 1; min-width: 0; display: flex; align-items: center; gap: 10px; height: 56px; padding: 0 10px 0 10px;
      box-shadow: var(--raised-hi), 0 0 0 1px color-mix(in srgb, var(--primary) calc(var(--on) * 40%), var(--raised-ring)); }
    .s-engine .en-tile .nm { font-size: 13px; font-weight: 650; line-height: 1.25; color: var(--fg); white-space: nowrap; }
    .s-engine .en-tile .mt { font-size: 11.5px; line-height: 1.3; color: var(--muted); white-space: nowrap; }
    .s-engine .en-tile.first .mt { color: var(--primary); font-weight: 600; }

    .k-still .s-engine .en-spin, .k-still .s-engine .en-cspin { animation: none; }
    @media (prefers-reduced-motion: reduce) { .s-engine .en-spin, .s-engine .en-cspin { animation: none; } }
  `,
  build(area, kit) {
    const { h, icon } = kit
    const NS = 'http://www.w3.org/2000/svg'
    const world = h('div.en-world')
    area.appendChild(world)

    /* ═════════ (1) the workflow layer: the Opening's window, finished ═════════ */
    const win = kit.window({ title: 'New workflow · Build', right: h('span.k-chip.ai.sm', icon('sparkles', 12), 'AI') })
    const grid = h('div.wf-grid')
    win.body.appendChild(grid)
    const CW = 1180 - 24 - 292 - 12, CH = 496
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
    const canvas = h('div.wf-canvas.k-well.k-dots', { style: { height: CH + 'px' } })
    const ask = h('div.wf-ask.k-raised', h('div.who', icon('sparkles', 12), 'You asked'), 'Handle new requests, send the risky ones for approval.')
    const steps = kit.stepper([{ title: 'Ask' }, { title: 'Plan', meta: '6 steps · tests locked' }, { title: 'Build', meta: `${N.length} of ${N.length} steps` }, { title: 'Review' }])
    steps.set(4)
    const review = h('div.wf-review.k-raised',
      h('div.r.ok', icon('flask', 14), '6 of 6 tests passed'),
      h('div.r.warn', icon('globe', 14), 'New address: api.example.com'),
      h('div.btns', h('span.k-btn.sm', icon('check', 12), 'Keep'), h('span.k-btn.sm.ghost', 'Discard')))
    const tests = h('div.wf-tests.k-well', h('div.k-label', 'Tests · locked in the plan'),
      ['Risky request waits for approval', 'API failure is logged'].map((t) => h('div.tr', icon('checkCircle', 14), t)))
    grid.append(canvas, h('div.wf-side', ask, steps.el, review, tests))

    // the graph, on its own sheet over the canvas
    const ed = kit.edges(CW, CH, E.map(([a, b]) => [...at(N[a][3], N[a][4]), ...at(N[b][3], N[b][4])]))
    const lift = h('div.wf-lift', ed.svg)
    N.forEach(([name, ic, kind, c, r]) => { const [x, y] = at(c, r); lift.appendChild(kit.node({ x, y, name, icon: ic, kind })) })
    const sheet = h('div.wf-sheet', lift)
    // the graph's shadow on the canvas, which shows once the graph lifts off it
    const shade = h('div.wf-shade', ed.svg.cloneNode(true), N.map(([, , , c, r]) => { const [x, y] = at(c, r); return h('i', { style: { left: x - 18 + 'px', top: y - 18 + 'px' } }) }))
    canvas.appendChild(shade)
    const wfTilt = h('div.wf-tilt', win.el, sheet)
    world.appendChild(wfTilt)

    /* ═════════ (2) the engine core ═════════ */
    const PW = 1472, PH = 630
    const plane = h('div.en-plane')
    const lean = h('div.en-lean', plane)
    world.appendChild(lean)

    // disc
    const DC = [740, 214], DR = 200
    const disc = h('div.en-disc', { style: { left: DC[0] - DR + 'px', top: DC[1] - DR + 'px', width: DR * 2 + 'px', height: DR * 2 + 'px' } },
      h('span.k-halo', h('span')),
      h('div.en-name', h('span.k-label', h('i'), 'Engine core')))

    // gear geometry (pitch p per tooth, so any two gears mesh): rp = n p / 2π, tip R = rp + d/2, root = rp - d/2
    const P = 40, D = 16
    const circ = (cx, cy, r) => `M${(cx + r).toFixed(1)} ${cy.toFixed(1)} A${r} ${r} 0 1 0 ${(cx - r).toFixed(1)} ${cy.toFixed(1)} A${r} ${r} 0 1 0 ${(cx + r).toFixed(1)} ${cy.toFixed(1)} Z`
    function gearGeo(n, hole, lights) {
      const rp = (n * P) / (2 * Math.PI), R = rp + D / 2, r = rp - D / 2, c = R, s = (Math.PI * 2) / n, pts = []
      for (let i = 0; i < n; i++) {
        const a0 = i * s
        pts.push([r, a0], [r, a0 + s * 0.12], [R, a0 + s * 0.27], [R, a0 + s * 0.58], [r, a0 + s * 0.73])
      }
      const outer = pts.map(([rad, a], i) => `${i ? 'L' : 'M'}${(c + rad * Math.cos(a)).toFixed(1)} ${(c + rad * Math.sin(a)).toFixed(1)}`).join(' ') + ' Z'
      const holes = [circ(c, c, hole)]
      if (lights) for (let k = 0; k < lights.n; k++) {
        const a = (k / lights.n) * Math.PI * 2 + Math.PI / lights.n
        holes.push(circ(c + lights.at * Math.cos(a), c + lights.at * Math.sin(a), lights.r))
      }
      return { n, rp, R, r, s, outer, holes }
    }
    const G = [
      { n: 18, hole: 66, lights: { n: 6, at: 84, r: 11 }, hub: 60 },  // the main gear: 35 node types on its hub
      { n: 11, hole: 22, lights: { n: 5, at: 46, r: 9 }, hub: 19 },
      { n: 8, hole: 17, lights: null, hub: 15 },
    ].map((g) => Object.assign(g, gearGeo(g.n, g.hole, g.lights)))
    // positions: the main gear at the disc centre-ish, the others meshing with it
    const cA = [736, 214]
    const place = (g, deg) => { const a = (deg * Math.PI) / 180, d = G[0].rp + g.rp; return { a, c: [cA[0] + d * Math.cos(a), cA[1] + d * Math.sin(a)] } }
    G[0].c = cA; G[0].rot0 = 0
    const pB = place(G[1], -33), pC = place(G[2], 158)
    G[1].c = pB.c; G[2].c = pC.c
    // phase so the teeth interlock: A's tooth at contact offset u ⇒ B's gap sits at φ+π − u·nA/nB
    const wrap = (v, m) => ((((v + m / 2) % m) + m) % m) - m / 2
    ;[[G[1], pB.a], [G[2], pC.a]].forEach(([g, phi]) => {
      const u = wrap(0.425 * G[0].s - phi, G[0].s) // A's nearest tooth centre, relative to the contact direction φ
      g.rot0 = phi + Math.PI - u * (G[0].n / g.n) - 0.925 * g.s
    })
    const K = 3.1 // seconds per tooth for the idle turn: main gear one turn ≈ 56 s
    const gearEls = G.map((g, i) => {
      const Dm = g.R * 2
      const clip = `path(evenodd, '${g.outer} ${g.holes.join(' ')}')`
      const face = (cls, z, kid) => h('div.en-face.' + cls, { style: { clipPath: clip, transform: `translateZ(${z}px)` } }, kid || null)
      const vb = `viewBox="0 0 ${Dm.toFixed(1)} ${Dm.toFixed(1)}"`
      const rim = kit.html(`<svg class="en-rim" ${vb} style="transform: translateZ(0.5px)">` +
        `<path class="r1" d="${g.outer}"/>${g.holes.map((d) => `<path class="r1" d="${d}"/>`).join('')}` +
        `<circle class="r3" cx="${g.R.toFixed(1)}" cy="${g.R.toFixed(1)}" r="${(g.r - 9).toFixed(1)}"/></svg>`)
      // the inner edge glow: wide strokes clipped to the face, so only their inside half shows
      const glow = kit.html(`<svg class="en-glow" ${vb} style="clip-path: ${clip.replace(/"/g, '&quot;')}; transform: translateZ(0.25px)">` +
        `<path d="${g.outer}"/>${g.holes.map((d) => `<path class="in" d="${d}"/>`).join('')}</svg>`)
      const backRim = kit.html(`<svg class="en-rim" ${vb} style="transform: translateZ(-12px)"><path class="r2" d="${g.outer}"/></svg>`)
      // the fixed light: counter-rotates the timeline turn (lc), the idle CSS turn (cspin) and the mesh phase (lc0)
      const rot0 = (g.rot0 * 180) / Math.PI
      const lc0 = h('div.en-lc0', { style: { transform: `rotate(${(-rot0).toFixed(3)}deg)` } }, h('div.en-lite'))
      const cspin = h('div.en-cspin', { '--per': (g.n * K).toFixed(2) + 's', '--cdir': i === 0 ? 'reverse' : 'normal' }, lc0)
      const lc = h('div.en-lc', cspin)
      const ph = h('div.en-ph', { style: { transform: `rotate(${rot0.toFixed(3)}deg)` } },
        face('back', -12), backRim, face('back', -6), face('front', 0, lc), glow, rim)
      const spin = h('div.en-spin', { '--per': (g.n * K).toFixed(2) + 's', '--dir': i === 0 ? 'normal' : 'reverse' }, ph)
      const rot = h('div.en-rot', spin)
      const hub = h('div.en-hub' + (i ? '.sm' : ''), { style: { left: g.R - g.hub + 'px', top: g.R - g.hub + 'px', width: g.hub * 2 + 'px', height: g.hub * 2 + 'px', transform: 'translateZ(16px)' } })
      let num = null
      if (i === 0) { num = h('b', '0'); hub.append(num, h('span', 'node types')) }
      const el = h('div.en-gear', { style: { left: g.c[0] - g.R + 'px', top: g.c[1] - g.R + 'px', width: Dm + 'px', height: Dm + 'px' } }, rot, hub)
      const shadow = h('div.en-shadow', { style: { left: g.c[0] - g.R * 1.05 + 'px', top: g.c[1] - g.R * 0.92 + 'px', width: g.R * 2.1 + 'px', height: g.R * 2.1 + 'px' } })
      return { el, rot, lc, hub, num, shadow, g }
    })

    /* ═════════ (3) capabilities (apps/workflow-engine) ═════════ */
    const CAPS = [
      ['Runs any graph', 'Branches, loops, parallel, sub-workflows', 'graph', 'flow'],
      ['Triggers', 'Schedule, webhook, form', 'zap', 'trigger'],
      ['Durable waits', 'A run can wait for days, then resume', 'history', 'flow'],
      ['People in the loop', 'Approvals: approved, rejected, timed out', 'userCheck', 'person'],
      ['Every step declares its effect', 'None, network, database or a person', 'effect', 'out'],
      ['Sandboxed code', 'No network, no privileges', 'box', 'code'],
      ['Read-only by default', 'SELECT only, in a read-only transaction', 'lock', 'data'],
      ['Written once, on the record', 'Idempotency key, before and after', 'key', 'person'],
    ]
    const CY0 = 38, CHh = 76, CG = 18
    const capPos = CAPS.map((_, i) => {
      const left = i < 4, row = i % 4
      return { left, x: left ? 22 : 1110, y: CY0 + row * (CHh + CG) }
    })
    // connectors: from the disc rim toward each card's inner edge
    const pairs = capPos.map((p) => {
      const tx = p.left ? p.x + 340 : p.x, ty = p.y + CHh / 2
      const dx = tx - DC[0], dy = ty - DC[1], L = Math.hypot(dx, dy)
      return [DC[0] + (dx / L) * (DR + 2), DC[1] + (dy / L) * (DR + 2), tx, ty]
    })
    const caps = CAPS.map(([name, meta, ic, kind], i) => {
      const p = capPos[i]
      // a step with its effect badge (drawn here: the kit tag glyph read as a price tag)
      const glyph = ic === 'effect'
        ? kit.html('<svg class="k-i" viewBox="0 0 24 24" width="20" height="20" aria-hidden="true"><rect x="3" y="9" width="12" height="12" rx="3"/><path d="M7 15h4"/><circle cx="18" cy="6" r="3"/><circle cx="18" cy="6" r=".9" fill="currentColor" stroke="none"/></svg>')
        : icon(ic, 20)
      const tile = h('span.k-icon-tile.lg', { '--kc': `var(--k-${kind})` }, glyph)
      const port = h('span.en-port', { style: { left: (p.left ? 340 : 0) + 'px' } })
      const el = h('div.en-cap', { style: { left: p.x + 'px', top: p.y + 'px' } }, tile, h('div.tx', h('div.nm', name), h('div.mt', meta)), h('span.en-led'), port)
      return { el, tile }
    })

    /* ═════════ (4) what runs on it ═════════ */
    const BX = 86, BY = 526, BW = 1300, BH = 76
    const POW = [
      ['Workflows', 'Where it started', 'workflow', 'flow'],
      ['Notebook', 'Data analysis', 'notebook', 'trigger'],
      ['Tests', 'Testing', 'flask', 'data'],
      ['Scheduled checks', 'Every business day 07:00', 'calendar', 'out'],
      ['App builder', 'A React app on a workflow', 'browser', 'person'],
    ]
    const tiles = POW.map(([name, meta, ic, kind], i) => {
      const tile = h('span.k-icon-tile', { '--kc': `var(--k-${kind})` }, icon(ic, 16))
      const el = h('div.en-tile.k-raised' + (i === 0 ? '.first' : ''), tile, h('div', { style: { minWidth: '0' } }, h('div.nm', name), h('div.mt', meta)))
      return { el, tile }
    })
    const powLab = h('div.lab', h('div.k-label', 'Same core'), h('div', 'Powers more than workflows'))
    const pow = h('div.en-pow.k-glass', { style: { left: BX + 'px', top: BY + 'px', width: BW + 'px', height: BH + 'px' } }, powLab, tiles.map((t) => t.el))
    // tile centres (bar: padding 12, label 178 + gap 10, then 5 tiles with 10 gaps)
    const TW = (BW - 24 - 150 - 10 - 4 * 10) / 5
    const tcx = POW.map((_, i) => BX + 12 + 150 + 10 + i * (TW + 10) + TW / 2)
    const fanFrom = [DC[0], DC[1] + DR + 2]

    // all lines in one svg in the plane
    const ed2 = kit.edges(PW, PH, pairs)
    const fans = tcx.map((x) => {
      const p = document.createElementNS(NS, 'path')
      const [x1, y1] = fanFrom, y2 = BY
      p.setAttribute('d', `M ${x1} ${y1} C ${x1} ${y1 + 70}, ${x} ${y2 - 64}, ${x} ${y2}`)
      p.setAttribute('class', 'fan')
      ed2.svg.appendChild(p)
      return p
    })
    ed2.svg.classList.add('en-lines')
    const allLines = [...ed2.paths, ...fans]
    const glows = allLines.map((p) => { const c = p.cloneNode(); c.setAttribute('class', 'glow'); ed2.svg.insertBefore(c, ed2.svg.firstChild); return c })
    const lits = allLines.map((p) => { const c = p.cloneNode(); c.setAttribute('class', 'lit'); c.setAttribute('pathLength', '1'); c.style.strokeDasharray = '0.16 2'; ed2.svg.appendChild(c); return c })
    const pulses = [...ed2.paths, ...fans].map(() => h('span.k-pulse', { style: { opacity: '0' } }))

    // depth order: lines, disc, gear shadows, gears, cards, bar, pulses
    plane.append(ed2.svg, disc, ...gearEls.map((g) => g.shadow), ...gearEls.map((g) => g.el), ...caps.map((c) => c.el), pow, ...pulses)

    return { wfTilt, win: win.el, lift, sheet, shade, plane, disc, gears: gearEls, caps, capLines: ed2.paths, fans, glows, lits, pulses, pow, tiles, powLab }
  },
  timeline(tl, kit, s) {
    /* 1 · the workflow layer lifts off: the graph rises off its canvas, then the whole layer flies up toward the
       viewer and away, and the engine core comes up from underneath it (always behind it in depth) */
    tl.add(s.lift, { z: [0, 150], y: [0, -26] }, 1.5, 0.85, 'io')
    tl.add(s.shade, { opacity: [0, 1], y: [0, 16] }, 1.5, 0.85, 'io')
    tl.add(s.wfTilt, { z: [0, -24] }, 1.5, 0.85, 'io')
    tl.add(s.wfTilt, { z: [-24, 720], y: [0, -60], rx: [7, 0] }, 2.25, 1.15, 'in')
    tl.add(s.lift, { z: [150, 460], y: [-26, -70] }, 2.25, 1.15, 'in')
    // the layer is gone before the core resolves underneath (no frame where the window sits over the gears)
    tl.add(s.win, { opacity: [1, 0] }, 2.25, 0.5, 'io')
    tl.add(s.sheet, { opacity: [1, 0] }, 2.3, 0.5, 'io')

    /* 2 · underneath, the engine core: the plane rises from depth and settles, the gears land and spin up */
    tl.add(s.plane, { z: [-420, 0], rx: [26, 8], y: [30, 0] }, 2.3, 2.5, 'soft')
    tl.add(s.disc, { opacity: [0, 1] }, 2.74, 0.75, 'out')
    tl.add(s.disc, { s: [0.9, 1] }, 2.5, 1.6, 'soft')
    s.gears.forEach((g, i) => {
      const t0 = 2.95 + i * 0.16
      tl.add(g.el, { opacity: [0, 1] }, t0, 0.45, 'out')
      // the gears land from just above the frosted disc (never behind it, where its frost would blur them)
      tl.add(g.el, { z: [14, 30], s: [0.62, 1] }, t0, 1.1, 'pop')
      tl.add(g.shadow, { opacity: [0, 1] }, t0 + 0.2, 0.8, 'out')
    })
    kit.count(s.gears[0].num, 35, { at: 3.15, dur: 1.15 })

    /* 3 · eight capabilities, one by one, each wired to the core (left/right alternating, top to bottom) */
    // a soft light that runs the length of a connector as it connects (a dash on pathLength 1), then fades
    const comet = (el, at, dur) => tl.fn(at, dur + 0.3, (p, t) => {
      const q = kit.clamp01((t - at) / dur), e = kit.ease.out(q)
      el.style.strokeDashoffset = (0.16 - e * 1.16).toFixed(4)
      el.style.opacity = (t < at || t > at + dur + 0.3 ? 0 : q < 1 ? Math.min(1, q * 6) : 1 - (t - at - dur) / 0.3).toFixed(3)
    }, 'linear')
    const ORDER = [0, 4, 1, 5, 2, 6, 3, 7]
    const T_CAP = 4.4, STEP = 0.46
    const capAt = []
    ORDER.forEach((i, k) => {
      const t = T_CAP + k * STEP
      capAt.push(t)
      const c = s.caps[i], left = i < 4
      kit.draw([s.glows[i], s.capLines[i]], { at: t - 0.3, dur: 0.45, stagger: 0 })
      kit.travel(s.pulses[i], s.capLines[i], { at: t - 0.3, dur: 0.5, e: 'out' })
      comet(s.lits[i], t - 0.3, 0.62)
      tl.add(c.el, { opacity: [0, 1] }, t - 0.05, 0.4, 'out')
      tl.add(c.el, { x: [left ? 46 : -46, 0], z: [-60, 0] }, t - 0.05, 0.7, 'out')
      tl.add(c.el, { '--on': [0, 1] }, t + 0.15, 0.4, 'out')
      tl.add(c.tile, { s: [0.6, 1] }, t + 0.1, 0.55, 'pop')
    })

    /* 4 · the same core runs more than workflows */
    const T_POW = T_CAP + ORDER.length * STEP + 0.35
    tl.add(s.pow, { opacity: [0, 1] }, T_POW - 0.1, 0.5, 'out')
    tl.add(s.pow, { y: [24, 0], z: [-40, 0] }, T_POW - 0.1, 0.8, 'out')
    kit.reveal(s.powLab, { at: T_POW + 0.15, y: 6, dur: 0.45 })
    const nCap = s.capLines.length
    s.fans.forEach((p, i) => {
      const t = T_POW + 0.2 + i * 0.16
      kit.draw([s.glows[nCap + i], p], { at: t, dur: 0.5, stagger: 0 })
      kit.travel(s.pulses[nCap + i], p, { at: t, dur: 0.55, e: 'out' })
      comet(s.lits[nCap + i], t, 0.66)
      const tile = s.tiles[i]
      tl.add(tile.el, { opacity: [0, 1] }, t + 0.35, 0.4, 'out')
      tl.add(tile.el, { '--on': [0, 1] }, t + 0.45, 0.4, 'out')
      tl.add(tile.tile, { s: [0.6, 1] }, t + 0.4, 0.5, 'pop')
    })
    tl.add(s.tiles[0].el, { '--on': [1, 0.6] }, T_POW + 2.2, 0.6, 'out')
    s.tiles.slice(1).forEach((t) => tl.add(t.el, { '--on': [1, 0.6] }, T_POW + 2.2, 0.6, 'out'))

    /* the gears: a spin-up as the core appears, then a small kick each time a capability or a product connects.
       Ratios keep the teeth meshed: B turns −nA/nB as far as A. (The slow idle turn on top is CSS, see header.) */
    const E = kit.ease
    const kicks = [...capAt.map((t) => t - 0.3), ...s.fans.map((_, i) => T_POW + 0.2 + i * 0.16)]
    const G = s.gears.map((g) => g.g)
    tl.fn(0, 0, (p, t) => {
      let a = 150 * E.out(kit.clamp01((t - 2.9) / 2.6))
      for (const k of kicks) a += 12 * E.spring(kit.clamp01((t - k) / 0.55))
      s.gears.forEach((g, i) => {
        const deg = i === 0 ? a : (-a * G[0].n) / G[i].n
        const v = deg.toFixed(2)
        if (g.rot.__v !== v) { g.rot.style.transform = `rotate(${v}deg)`; g.lc.style.transform = `rotate(${-v}deg)`; g.rot.__v = v }
      })
    })
  },
})
