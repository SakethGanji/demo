/* 05 · Ask — use case 1: data analysis. The showpiece: one glass notebook window. Left, the notebook: the ask is
 * typed, the AI drafts four typed cells (DATA, QUERY, RULE, CHANGE), the query runs, the result table fills, the
 * proposed change waits for a person, a person clicks Apply, the value swaps stuck → released and an audit row
 * lands. Right, the analytics panel behind the query (bank.wires v14): stats count up, the profile draws, quality
 * checks tick, the pivot fills, the sparkline and the relationship edges draw. Background is still. */
Film.slide({
  index: 5,
  id: 'ask',
  name: 'Ask',
  word: 'Data analysis',
  kicker: '05 · Idea 1 · data analysis',
  captions: [
    { at: 0.6, text: 'Ask your data a question in plain words.' },
    { at: 3.4, text: 'AI drafts the cells. The engine runs them.' },
    { at: 8.4, text: 'Nothing writes until a person says so.' },
    { at: 12.4, text: 'Written once. On the record.' },
  ],
  dur: 16,
  css: `
    .s-ask .ak-stage { position: absolute; left: 4px; top: 0; width: 1456px; height: 630px; }
    .s-ask .ak-tilt { transform-origin: 50% 0%; transform-style: preserve-3d; }
    .s-ask .k-chip.sm { font-size: 11px; }
    .s-ask .k-av { font-size: 11px; }
    .s-ask .ak-grid { display: grid; grid-template-columns: 1fr 470px; gap: 12px; height: 100%; min-height: 0; }
    .s-ask .sw { display: inline-grid; justify-items: end; align-items: center; }

    /* ── notebook column ── */
    .s-ask .ak-left { display: flex; flex-direction: column; gap: 10px; min-width: 0; min-height: 0; }
    .s-ask .ak-ask { display: flex; align-items: center; gap: 12px; padding: 9px 14px 10px 12px;
      --raised-tint: color-mix(in srgb, var(--ai) 34%, transparent);
      background: linear-gradient(90deg, var(--ai-soft), transparent 75%), var(--raised); }
    .s-ask .ak-ask .who { display: flex; align-items: center; gap: 6px; font-size: 11px; font-weight: 600; color: var(--ai-text); line-height: 15px; }
    .s-ask .ak-ask .q { font-size: 14.5px; font-weight: 550; letter-spacing: -0.01em; line-height: 21px; color: var(--fg); white-space: nowrap; }
    .s-ask .ak-ask .k-kbd { margin-left: auto; }

    .s-ask .ak-cells { position: relative; display: flex; flex-direction: column; gap: 6px; }
    .s-ask .ak-cell { position: relative; display: grid; grid-template-columns: 104px 1fr auto; align-items: center; gap: 12px; min-height: 36px; padding: 6px 12px 6px 8px; }
    .s-ask .ak-cell::after { content: ''; position: absolute; inset: 0; border-radius: inherit; pointer-events: none; opacity: var(--g, 0);
      background: linear-gradient(90deg, var(--ai-soft), transparent 60%);
      box-shadow: 0 0 0 1.5px color-mix(in srgb, var(--ai) 55%, transparent), 0 0 0 5px var(--ai-soft), 0 14px 30px -18px var(--ai); }
    .s-ask .ak-gut { position: relative; z-index: 1; display: flex; align-items: center; gap: 8px; }
    .s-ask .ak-gut .ix { display: inline-flex; align-items: center; justify-content: center; width: 20px; height: 20px; border-radius: 99px; font: 600 11px/1 var(--mono);
      color: var(--muted); background: var(--node-bg); box-shadow: inset 0 0 0 1px var(--node-ring); }
    .s-ask .ak-kind { display: inline-flex; align-items: center; gap: 5px; height: 20px; padding: 0 7px; border-radius: 5px; font-size: 11px; font-weight: 700; letter-spacing: .05em;
      color: var(--kc); background: color-mix(in srgb, var(--kc) 12%, transparent); }
    .s-ask .ak-kind svg { width: 12px; height: 12px; }
    .s-ask .ak-ct { min-width: 0; display: flex; flex-direction: column; gap: 4px; }
    .s-ask .ak-ct .t { display: flex; align-items: center; gap: 8px; font-size: 13px; font-weight: 550; white-space: nowrap; color: var(--fg); }
    .s-ask .ak-ct .t .m { font-size: 11.5px; font-weight: 450; color: var(--faint); }
    .s-ask .ak-sql { display: block; padding: 2px 10px; border-radius: 6px; background: var(--recess); font: 500 12px/19px var(--mono); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; color: var(--fg); }
    .s-ask .ak-sql > span { display: inline-block; }
    .s-ask .ak-sql .k { color: var(--syn-k); } .s-ask .ak-sql .s { color: var(--syn-s); } .s-ask .ak-sql .f { color: var(--syn-f); }

    .s-ask .ak-res { display: flex; flex-direction: column; gap: 6px; }
    .s-ask .ak-rl { display: flex; align-items: center; gap: 10px; padding: 0 4px; }
    .s-ask .ak-rl .k-meta { font-size: 11.5px; }
    .s-ask .ak-res .k-table { gap: 5px; }
    .s-ask .ak-res .k-table .tr { height: 31px; }
    .s-ask .ak-res .id { font: 600 12px var(--mono); color: var(--fg); }
    .s-ask .ak-res .amt { font: 600 12.5px var(--mono); color: var(--fg); }
    .s-ask .ak-res .age { display: inline-flex; align-items: center; gap: 8px; font-size: 12.5px; }
    .s-ask .ak-res .age .nt { font-size: 11px; color: var(--faint); }

    .s-ask .ak-chg { position: relative; display: flex; align-items: center; gap: 12px; height: 46px; padding: 0 10px 0 9px;
      --raised-tint: color-mix(in srgb, var(--warn) 45%, transparent); }
    .s-ask .ak-chg.done { --raised-tint: color-mix(in srgb, var(--ok) 45%, transparent); }
    .s-ask .ak-chg .df { display: flex; align-items: center; gap: 7px; font-size: 13px; font-weight: 600; white-space: nowrap; }
    .s-ask .ak-chg .df svg { color: var(--faint); }
    .s-ask .ak-tw { color: var(--warn-text); background: var(--warn-soft); box-shadow: none; }
    .s-ask .ak-tw.gone { text-decoration: line-through; opacity: .6; }
    .s-ask .ak-chg .via { font-size: 12px; color: var(--muted); white-space: nowrap; }
    .s-ask .ak-chg .via b { font-family: var(--mono); font-weight: 600; color: var(--fg); }
    .s-ask .ak-chg .st { margin-left: auto; }
    .s-ask .ak-chg .btns { display: flex; gap: 6px; }
    .s-ask .ak-apply { position: relative; }
    .s-ask .ak-cur { position: absolute; left: 62%; top: 58%; width: 20px; height: 20px; pointer-events: none; z-index: 3; }
    .s-ask .ak-cur svg { width: 20px; height: 20px; fill: var(--fg); stroke: var(--bg); stroke-width: 1.6; filter: drop-shadow(0 3px 5px rgb(0 0 0 / .25)); }

    .s-ask .ak-aud { display: flex; align-items: center; gap: 18px; height: 46px; padding: 0 14px 0 9px;
      --raised-tint: color-mix(in srgb, var(--ok) 30%, transparent); }
    .s-ask .ak-aud .f { display: flex; flex-direction: column; gap: 1px; min-width: 0; }
    .s-ask .ak-aud .f .k-label { font-size: 11px; line-height: 14px; }
    .s-ask .ak-aud .f .v { display: flex; align-items: center; gap: 6px; font-size: 12.5px; font-weight: 550; line-height: 20px; white-space: nowrap; color: var(--fg); }
    .s-ask .ak-aud .f .v .k-av { width: 20px; height: 20px; box-shadow: none; }
    .s-ask .ak-aud .f .v svg { color: var(--faint); width: 13px; height: 13px; }
    .s-ask .ak-aud .ttl { display: flex; flex-direction: column; gap: 1px; padding-right: 4px; }
    .s-ask .ak-aud .ttl .a { font-size: 13px; font-weight: 650; color: var(--fg); line-height: 18px; white-space: nowrap; }
    .s-ask .ak-aud .ttl .b { font-size: 11px; color: var(--ok-text); line-height: 14px; white-space: nowrap; }
    .s-ask .ak-aud .mono { font-family: var(--mono); font-size: 12px; }

    /* ── analytics panel ── */
    .s-ask .ak-skel { position: absolute; left: 14px; right: 14px; pointer-events: none; display: flex; flex-direction: column; gap: 8px; }
    .s-ask .ak-skel .k-shimmer { height: 12px; }
    .s-ask .ak-panel { position: relative; display: flex; flex-direction: column; min-width: 0; min-height: 0; overflow: hidden; padding: 8px 14px 6px; }
    .s-ask .ak-sec { padding: 6px 0; border-top: 1px solid var(--hairline); }
    .s-ask .ak-sec:first-child { padding-top: 2px; border-top: 0; }
    .s-ask .ak-sl { display: flex; align-items: center; gap: 8px; height: 16px; margin-bottom: 5px; }
    .s-ask .ak-sl .r { margin-left: auto; font-size: 11px; color: var(--faint); white-space: nowrap; }
    .s-ask .ak-sl .r.ok { display: inline-flex; align-items: center; gap: 5px; color: var(--ok-text); }
    .s-ask .ak-sl .r.ok .k-dot { width: 6px; height: 6px; box-shadow: 0 0 0 3px var(--ok-soft); }

    .s-ask .ak-hd1 { display: flex; align-items: center; gap: 9px; height: 28px; }
    .s-ask .ak-hd1 .k-icon-tile { width: 28px; height: 28px; }
    .s-ask .ak-hd1 .nm { font: 650 14px var(--mono); letter-spacing: -0.01em; color: var(--fg); }
    .s-ask .ak-rail { margin-left: auto; display: flex; align-items: flex-start; }
    .s-ask .ak-rail .v { display: flex; flex-direction: column; align-items: center; gap: 2px; width: 30px; font: 500 11px/13px var(--mono); color: var(--faint); }
    .s-ask .ak-rail .v i { width: 8px; height: 8px; margin-top: 3px; border-radius: 99px; background: color-mix(in srgb, var(--fg) 22%, transparent); }
    .s-ask .ak-rail .v.cur { color: var(--primary); font-weight: 700; }
    .s-ask .ak-rail .v.cur i { background: var(--primary); box-shadow: 0 0 0 3px var(--brand-soft); }
    .s-ask .ak-rail .ln { width: 12px; height: 1px; margin-top: 7px; background: var(--input); }
    .s-ask .ak-hd2 { display: flex; align-items: center; gap: 8px; margin-top: 4px; }
    .s-ask .ak-hd2 .k-meta { font-size: 11.5px; }

    .s-ask .ak-stats { display: grid; grid-template-columns: .95fr .62fr 1.32fr .9fr; }
    .s-ask .ak-stats .k-stat { padding: 7px 10px 8px; gap: 3px; min-width: 0; }
    .s-ask .ak-stats .k-stat + .k-stat { box-shadow: inset 1px 0 0 var(--hairline); }
    .s-ask .ak-stats .k-stat .v { font-size: 20px; white-space: nowrap; }
    .s-ask .ak-stats .k-stat .v.warn { color: var(--warn-text); }
    .s-ask .ak-stats .k-stat .l { font-size: 11.5px; white-space: nowrap; }

    .s-ask .ak-prof { display: grid; grid-template-columns: 196px 1fr; gap: 16px; }
    .s-ask .ak-hist { height: 42px; }
    .s-ask .ak-hist .k-bars { gap: 3px; }
    .s-ask .ak-ax { display: flex; justify-content: space-between; margin-top: 3px; font: 11px var(--mono); color: var(--faint); }
    .s-ask .ak-mix { display: flex; gap: 2px; height: 10px; margin: 3px 0 6px; }
    .s-ask .ak-mix span { flex: var(--n) 1 0; min-width: 7px; border-radius: 3px; transform-origin: 0 50%; }
    .s-ask .ak-mix span:first-child { border-radius: 99px 3px 3px 99px; }
    .s-ask .ak-mix span:last-child { border-radius: 3px 99px 99px 3px; }
    .s-ask .ak-leg { display: flex; flex-direction: column; gap: 2px; }
    .s-ask .ak-leg > div { display: flex; align-items: center; gap: 7px; height: 15px; font-size: 11.5px; color: var(--muted); }
    .s-ask .ak-leg i { width: 8px; height: 8px; border-radius: 2px; flex: none; }
    .s-ask .ak-leg b { margin-left: auto; font: 600 11.5px var(--mono); color: var(--fg); }

    .s-ask .ak-q { display: flex; align-items: center; gap: 8px; height: 20px; font-size: 12px; color: var(--fg); white-space: nowrap; }
    .s-ask .ak-q > svg { color: var(--ok); width: 15px; height: 15px; }
    .s-ask .ak-q code { font: 600 11.5px var(--mono); color: var(--fg); }
    .s-ask .ak-q .r { margin-left: auto; display: inline-flex; align-items: center; font: 11px var(--mono); color: var(--faint); }
    .s-ask .ak-q .k-mask { height: 20px; font-size: 11px; }

    .s-ask .ak-two { display: grid; grid-template-columns: 206px 1fr; gap: 16px; }
    .s-ask .ak-pv { display: grid; grid-template-columns: 58px repeat(3, 1fr); gap: 3px; font: 500 11.5px var(--mono); }
    .s-ask .ak-pv > span { display: flex; align-items: center; justify-content: center; height: 18px; border-radius: 4px; color: var(--fg); background: var(--recess); }
    .s-ask .ak-pv > span.h { background: none; font: 600 11px var(--font); color: var(--faint); }
    .s-ask .ak-pv > span.rh { justify-content: flex-start; padding-left: 2px; background: none; font: 550 11.5px var(--font); color: var(--muted); }
    .s-ask .ak-pv > span.z { color: var(--faint); }
    .s-ask .ak-pv > span.w { color: var(--warn-text); background: var(--warn-soft); font-weight: 700; }
    .s-ask .ak-pv > span.hl { color: var(--primary-fg); background: linear-gradient(135deg, var(--brand-1), var(--brand-2)); font-weight: 700; box-shadow: 0 6px 14px -8px var(--primary); }
    .s-ask .ak-spk { position: relative; }
    .s-ask .ak-spk .k-spark .ar { fill: color-mix(in srgb, var(--viz-1) 12%, transparent); }
    .s-ask .ak-spk .pk { position: absolute; right: 12px; top: -3px; font: 700 11px var(--mono); color: var(--primary); }
    .s-ask .ak-spk .end { position: absolute; width: 7px; height: 7px; margin: -3.5px 0 0 -3.5px; border-radius: 99px; background: var(--primary); box-shadow: 0 0 0 3px var(--brand-soft); }

    .s-ask .ak-rel { position: relative; height: 50px; }
    .s-ask .ak-rel .k-edges path { stroke-opacity: .6; }
    .s-ask .ak-tn { position: absolute; display: flex; align-items: center; gap: 6px; height: 22px; padding: 0 9px 0 6px; border-radius: 7px; font: 600 11.5px var(--mono); color: var(--fg); white-space: nowrap;
      background: var(--node-bg); box-shadow: inset 0 0 0 1px var(--node-ring), 0 6px 14px -10px var(--primary); }
    .s-ask .ak-tn svg { width: 13px; height: 13px; color: var(--k-data); }
    .s-ask .ak-tn.main { box-shadow: inset 0 0 0 1.5px color-mix(in srgb, var(--primary) 55%, transparent), 0 8px 18px -10px var(--primary); }
    .s-ask .ak-el { position: absolute; font: 500 11px var(--mono); color: var(--muted); white-space: nowrap; }
    .s-ask .ak-el.a { left: 118px; top: -4px; }
    .s-ask .ak-el.b { left: 118px; top: 38px; }
    .s-ask .ak-rel .cd { position: absolute; font: 600 11px var(--mono); color: var(--faint); }
  `,
  build(area, kit) {
    const { h, icon } = kit
    const chip = (cls, ...kids) => h('span.k-chip.sm' + (cls ? '.' + cls : ''), ...kids)

    const win = kit.window({
      title: 'Notebook · Stuck wires',
      h: 630,
      right: [h('span.k-avs', h('span.k-av', 'SG'), h('span.k-av.c', 'MR')), chip('ai', icon('sparkles', 12), 'AI drafted')],
    })
    const grid = h('div.ak-grid')
    win.body.appendChild(grid)

    /* ── left: the notebook ── */
    const q = h('div.q')
    const ask = h('div.ak-ask.k-raised',
      h('span.k-av', 'SG'),
      h('div', h('div.who', icon('sparkles', 12), 'saketh asked'), q),
      h('span.k-kbd', '↵ Ask'))

    const CELLS = [
      { kind: 'DATA', kc: 'var(--k-data)', ic: 'hash', t: ['settlement window · ', h('span.k-tok', '1 day')] },
      { kind: 'QUERY', kc: 'var(--primary)', ic: 'database', t: ['stuck wires older than 1 day in bank.wires', h('span.m', 'bank · read-only')], sql: true },
      { kind: 'RULE', kc: 'var(--k-flow)', ic: 'filter', t: ['amount is over 50', h('span.m', 'the oldest first')] },
      { kind: 'CHANGE', kc: 'var(--k-person)', ic: 'gitCommit', t: ['release the oldest · status → released', h('span.m', 'bank_rw')] },
    ]
    let sqlInner = null
    const cells = CELLS.map((c, i) => {
      const ct = h('div.ak-ct', h('div.t', c.t))
      if (c.sql) {
        const S = (t, cl) => (cl ? h('span.' + cl, t) : t)
        sqlInner = h('span',
          S('SELECT', 'k'), ' id, amount, age ', S('FROM', 'k'), ' bank.wires ', S('WHERE', 'k'), ' status = ', S("'stuck'", 's'),
          ' ', S('AND', 'k'), ' age > ', S('interval', 'f'), ' ', S("'1 day'", 's'))
        ct.appendChild(h('code.ak-sql', sqlInner))
      }
      const st = h('span.sw')
      const el = h('div.ak-cell.k-raised',
        h('div.ak-gut', h('span.ix', String(i + 1)), h('span.ak-kind', { '--kc': c.kc }, icon(c.ic, 12), c.kind)),
        ct, h('div', { style: { justifySelf: 'end' } }, st))
      return { el, st }
    })
    const cellsEl = h('div.ak-cells', cells.map((c) => c.el))

    const ST = (ok, text) => h('span.k-chip.sm.' + (ok ? 'ok' : 'warn'), h('span.k-dot'), text)
    const w2st = h('span.sw', { style: { justifyItems: 'start' } })
    const rows = [
      [h('span.id', 'W1'), kit.mask('4821'), h('span.amt', '$100.50'), ST(false, 'stuck'), h('span.age', '3 days')],
      [h('span.id', 'W2'), kit.mask('0937'), h('span.amt', '$12,500.00'), w2st, h('span.age', '4 days')],
      [h('span.id', 'W3'), kit.mask('5512'), h('span.amt', '$75.00'), ST(true, 'sent'), h('span.age', '—')],
      [h('span.id', 'W4'), kit.mask('2210'), h('span.amt', '$40.00'), ST(false, 'stuck'), h('span.age', '2 hours', h('span.nt', 'inside the window'))],
    ]
    const table = kit.table({ cols: '64px 1fr 130px 130px 200px', head: ['ID', 'Beneficiary', 'Amount', 'Status', 'Age'], rows })
    const resLabel = h('div.ak-rl', h('span.k-label', 'Result'), h('span.k-meta', '4 rows · bank.wires v14 · beneficiary masked'))
    const res = h('div.ak-res', resLabel, table.el)

    const oldTok = h('span.k-tok.ak-tw', 'stuck')
    const chgSt = h('span.sw')
    const cursor = h('span.ak-cur', icon('cursor', 20))
    const applyBtn = h('span.k-btn.sm', icon('check', 12), 'Apply')
    const discard = h('span.k-btn.sm.ghost', 'Discard')
    const btns = h('div.btns', discard, h('span.ak-apply', applyBtn, cursor))
    const chg = h('div.ak-chg.k-raised',
      h('span.k-icon-tile', { '--kc': 'var(--k-person)' }, icon('gitCommit', 16)),
      h('div.df', 'W2 status:', oldTok, icon('arrowRight', 14), h('span.k-tok.ok', 'released')),
      h('span.via', 'writes through ', h('b', 'bank_rw'), ' · once'),
      h('span.st', chgSt), btns)

    const keyTok = h('span.k-tok', 'idem-wires-W2-…')
    const F = (label, ...v) => h('div.f', h('span.k-label', label), h('span.v', ...v))
    const aud = h('div.ak-aud.k-raised',
      h('span.k-icon-tile', { '--kc': 'var(--ok)' }, icon('history', 16)),
      h('div.ttl', h('span.a', 'Audit row'), h('span.b', 'written once')),
      F('Who', h('span.k-av', 'SG'), 'saketh'),
      F('When', 'today 07:04'),
      F('Before → after', h('span.mono', 'stuck'), icon('arrowRight', 13), h('span.mono', 'released')),
      F('Key', keyTok),
      F('Wrote', h('span.mono', 'bank_rw · 1 row')))

    const left = h('div.ak-left', ask, cellsEl, res, chg, aud)

    /* ── right: the analytics panel ── */
    const sl = (label, r) => h('div.ak-sl', h('span.k-label', label), r || null)
    const rail = h('div.ak-rail', h('span.v', h('i'), 'v12'), h('span.ln'), h('span.v', h('i'), 'v13'), h('span.ln'), h('span.v.cur', h('i'), 'v14'))
    const secHead = h('div.ak-sec',
      h('div.ak-hd1', h('span.k-icon-tile', { '--kc': 'var(--k-data)' }, icon('database', 15)), h('span.nm', 'bank.wires'), chip('brand', 'v14'), rail),
      h('div.ak-hd2', chip('', icon('lock', 12), 'read-only snapshot · today 07:00'), h('span.k-meta', '9 columns · 3 linked tables')))

    const STATS = [
      { to: 1284, l: 'wires today' },
      { to: 3, l: 'stuck', warn: true },
      { to: 12600.5, l: 'held', fmt: (v) => '$' + v.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) },
      { to: 4, l: 'oldest', fmt: (v) => { const n = Math.round(v); return n + (n === 1 ? ' day' : ' days') } },
    ]
    const statV = STATS.map((s) => h('span.v' + (s.warn ? '.warn' : ''), '0'))
    const statsEl = h('div.ak-stats.k-raised', STATS.map((s, i) => h('div.k-stat', statV[i], h('span.l', s.l))))
    const secStats = h('div.ak-sec', statsEl)

    const HV = [0.34, 0.6, 0.86, 1, 0.82, 0.64, 0.5, 0.38, 0.27, 0.2, 0.14, 0.09, 0.06, 0.12]
    const hist = kit.bars(HV, { hl: [13] })
    const MIX = [
      { n: 1262, c: 'var(--viz-1)', l: 'sent' },
      { n: 19, c: 'var(--viz-3)', l: 'pending' },
      { n: 3, c: 'var(--warn)', l: 'stuck' },
    ]
    const mixSegs = MIX.map((m) => h('span', { style: { background: m.c }, '--n': m.n }))
    const legRows = MIX.map((m) => h('div', h('i', { style: { background: m.c } }), m.l, h('b', m.n.toLocaleString('en-US'))))
    const fresh = h('span.r.ok', h('span.k-dot'), 'fresh · 07:00 · 2 min ago')
    const secProf = h('div.ak-sec',
      sl('Profile', fresh),
      h('div.ak-prof',
        h('div', h('div.k-meta', { style: { fontSize: '11.5px', marginBottom: '4px' } }, 'amount'), h('div.ak-hist', hist.el), h('div.ak-ax', h('span', '$0'), h('span', '$12.5k'))),
        h('div', h('div.k-meta', { style: { fontSize: '11.5px' } }, 'status mix'), h('div.ak-mix', mixSegs), h('div.ak-leg', legRows))))

    const QS = [
      [[h('code', 'id'), ' unique'], h('span.r', '1,284 / 1,284')],
      [[h('code', 'amount'), ' not null'], h('span.r', '0 nulls')],
      [[h('code', 'beneficiary_iban'), ' masked for AI and output'], h('span.r', kit.mask('0937'))],
    ]
    const qRows = QS.map(([t, r]) => h('div.ak-q', icon('checkCircle', 15), h('span', t), r))
    const secQ = h('div.ak-sec', sl('Quality checks', h('span.r', '3 of 3 pass')), qRows)

    const PV = [
      ['', '<1d', '1–3d', '>3d'],
      ['sent', '1,262', '—', '—'],
      ['pending', '19', '—', '—'],
      ['stuck', '1', '1', '1'],
    ]
    const pvCells = []
    const pv = h('div.ak-pv', PV.map((row, r) => row.map((v, c) => {
      const cls = r === 0 ? '.h' : c === 0 ? '.rh' : v === '—' ? '.z' : r === 3 ? (c === 3 ? '.hl' : '.w') : ''
      const el = h('span' + cls, v)
      if (r > 0 && c > 0) pvCells.push(el)
      return el
    })))
    const SP = [40, 32, 28, 35, 62, 148, 336, 603]
    const SW = 214, SH = 52
    const sp = kit.spark(SP, SW, SH, { pad: 4 })
    const spEnd = h('span.end', { style: { left: sp.X(SP.length - 1) + 'px', top: sp.Y(SP[SP.length - 1]) + 'px' } })
    const spPk = h('span.pk', '603')
    const secTwo = h('div.ak-sec', h('div.ak-two',
      h('div', sl('Pivot · status × age'), pv),
      h('div', sl('Wires per hour today'), h('div.ak-spk', { style: { width: SW + 'px', height: SH + 'px' } }, sp.svg, spEnd), h('div.ak-ax', h('span', '00:00'), h('span', '07:00')))))
    secTwo.querySelector('.ak-spk').appendChild(spPk)

    // relationships: wires —account_id→ accounts, wires —batch_id→ settlement_batches
    const RW = 442, RH = 50
    const nW = h('span.ak-tn.main', { style: { left: '0px', top: '14px' } }, icon('table', 13), 'wires')
    const nA = h('span.ak-tn', { style: { left: '232px', top: '2px' } }, icon('table', 13), 'accounts')
    const nB = h('span.ak-tn', { style: { left: '232px', top: '28px' } }, icon('table', 13), 'settlement_batches')
    const re = kit.edges(RW, RH, [[76, 25, 228, 13], [76, 25, 228, 39]])
    const elA = h('span.ak-el.a', 'account_id → id')
    const elB = h('span.ak-el.b', 'batch_id → id')
    const cdA = h('span.cd', { style: { left: '346px', top: '5px' } }, 'n : 1')
    const cdB = h('span.cd', { style: { left: '400px', top: '31px' } }, 'n : 1')
    const relBox = h('div.ak-rel', re.svg, nW, nA, nB, elA, elB, cdA, cdB)
    const secRel = h('div.ak-sec', sl('Relationships', h('span.r', '2 joins found')), relBox)

    // skeletons where each section will land (the panel waits for the query to run)
    const SK = [[74, [100, 100]], [146, [38, 92, 70]], [264, [80, 64, 88]], [358, [46, 90, 76]], [474, [34, 82]]]
    const skels = SK.map(([top, ws]) => h('div.ak-skel', { style: { top: top + 'px' } }, ws.map((w, i) => h('div.k-shimmer', { style: { width: w + '%', height: i === 0 && top === 74 ? '44px' : '12px' } }))))
    const panel = h('div.ak-panel.k-well', secHead, secStats, secProf, secQ, secTwo, secRel, skels)
    grid.append(left, panel)

    const tilt = h('div.ak-tilt', win.el)
    area.appendChild(h('div.ak-stage', tilt))
    return {
      tilt, ask, q, cells, sqlInner, res, resLabel, table, w2st, chg, oldTok, chgSt, applyBtn, discard, cursor, aud, keyTok,
      secs: [secHead, secStats, secProf, secQ, secTwo, secRel], statV, STATS, hist, mixSegs, legRows, fresh, qRows, pvCells,
      sp, spEnd, spPk, re, skels, rel: [nW, nA, nB], relLabels: [elA, elB, cdA, cdB], panel,
    }
  },
  timeline(tl, kit, s) {
    const { h, icon } = kit
    const chip = (cls, ...kids) => h('span.k-chip.sm.' + cls, ...kids)

    // 1. the hero tilts in and settles at a slight tilt
    tl.add(s.tilt, { opacity: [0, 1] }, 0.5, 0.9, 'out')
    tl.add(s.tilt, { rx: [26, 5], s: [0.9, 1], y: [40, 0] }, 0.5, 2.6, 'soft')

    // 2. the ask is typed
    kit.reveal(s.ask, { at: 0.85, y: 8, dur: 0.5 })
    kit.type(s.q, 'Find wires stuck past the settlement window and release the oldest over the limit.', { at: 1.0, cps: 37 })

    // 3. the AI drafts four cells, one by one (violet glow, then they settle)
    const CT = [3.4, 3.72, 4.3, 4.62]
    s.cells.forEach((c, i) => {
      const t = CT[i]
      tl.add(c.el, { opacity: [0, 1], y: [10, 0] }, t, 0.45, 'out')
      tl.add(c.el, { '--g': [0, 1] }, t, 0.3, 'out')
      tl.add(c.el, { '--g': [1, 0] }, t + 0.7, 0.9, 'io')
    })
    tl.add(s.sqlInner, { clip: [0, 1] }, 3.9, 0.95, 'linear')

    // cell status chips: drafted → result
    kit.swap(s.cells[0].st, [{ at: CT[0], text: chip('ai', 'drafted') }, { at: 5.0, text: chip('ok', icon('check', 12), 'set') }])
    kit.swap(s.cells[1].st, [{ at: CT[1], text: chip('ai', 'drafted') }, { at: 5.0, text: chip('brand', icon('play', 11), 'running') }, { at: 5.85, text: chip('ok', icon('check', 12), '3 rows') }])
    kit.swap(s.cells[2].st, [{ at: CT[2], text: chip('ai', 'drafted') }, { at: 7.3, text: chip('ok', icon('check', 12), '1 row') }])
    kit.swap(s.cells[3].st, [{ at: CT[3], text: chip('ai', 'drafted') }, { at: 8.4, text: chip('warn', icon('userCheck', 12), 'waits for a person') }, { at: 11.35, text: chip('ok', icon('check', 12), 'applied · 1 row') }])

    // 4. the analytics panel comes alive as the query runs
    tl.add(s.panel, { opacity: [0.55, 1] }, 3.7, 0.6, 'out')
    kit.reveal(s.secs[0], { at: 3.75, y: 6, dur: 0.5 })
    ;[5.05, 5.35, 5.95, 6.45, 6.95].forEach((t, i) => tl.add(s.skels[i], { opacity: [1, 0] }, t - 0.05, 0.35, 'out'))
    kit.reveal(s.secs[1], { at: 5.05, y: 6, dur: 0.45 })
    s.statV.forEach((el, i) => kit.count(el, s.STATS[i].to, { at: 5.15 + i * 0.08, dur: 1.1, fmt: s.STATS[i].fmt }))
    kit.reveal(s.secs[2], { at: 5.35, y: 6, dur: 0.45 })
    tl.stagger(s.hist.bars, { sy: [0, 1] }, 5.45, 0.035, 0.5, 'out')
    tl.stagger(s.mixSegs, { sx: [0, 1] }, 5.6, 0.22, 0.45, 'out')
    kit.reveal(s.legRows, { at: 5.7, stagger: 0.08, y: 4, dur: 0.35 })
    kit.reveal(s.fresh, { at: 5.95, y: 0, x: -6, dur: 0.4 })
    kit.reveal(s.secs[3], { at: 5.95, y: 6, dur: 0.4 })
    s.qRows.forEach((r, i) => {
      tl.add(r, { opacity: [0.3, 1] }, 6.05 + i * 0.16, 0.3, 'out')
      tl.add(r.querySelector('svg'), { s: [0.4, 1], opacity: [0, 1] }, 6.05 + i * 0.16, 0.45, 'pop')
    })
    kit.reveal(s.secs[4], { at: 6.45, y: 6, dur: 0.4 })
    kit.pop(s.pvCells, { at: 6.55, stagger: 0.03, dur: 0.4, from: 0.7 })
    kit.draw(s.sp.line, { at: 6.65, dur: 0.95, e: 'io' })
    tl.add(s.sp.area, { opacity: [0, 1] }, 7.1, 0.6, 'out')
    kit.pop([s.spEnd, s.spPk], { at: 7.55, stagger: 0.05 })
    kit.reveal(s.secs[5], { at: 6.95, y: 6, dur: 0.4 })
    kit.pop(s.rel[0], { at: 7.05, dur: 0.45 })
    kit.draw(s.re.paths, { at: 7.2, stagger: 0.12, dur: 0.5 })
    kit.pop([s.rel[1], s.rel[2]], { at: 7.45, stagger: 0.12, dur: 0.45 })
    kit.reveal(s.relLabels, { at: 7.6, stagger: 0.06, y: 4, dur: 0.35 })

    // 5. the result fills; the rule picks W2
    kit.reveal(s.resLabel, { at: 6.15, y: 6, dur: 0.4 })
    kit.reveal(s.table.head, { at: 6.25, y: 6, dur: 0.4 })
    kit.reveal(s.table.rows, { at: 6.4, stagger: 0.1, y: 8, dur: 0.45 })
    tl.cls(s.table.rows[1], 'hl', 7.4)
    tl.cls([s.table.rows[2], s.table.rows[3]], 'dim', 7.4)
    kit.swap(s.w2st, [
      { at: 6.5, text: h('span.k-chip.sm.warn', h('span.k-dot'), 'stuck') },
      { at: 11.4, text: h('span.k-chip.sm.ok', h('span.k-dot'), 'released') },
    ])

    // 6. the change waits for a person
    kit.reveal(s.chg, { at: 8.45, y: 10, dur: 0.55 })
    kit.swap(s.chgSt, [
      { at: 8.5, text: chip('warn', icon('userCheck', 12), 'waits for a person') },
      { at: 11.3, text: chip('ok', icon('check', 12), 'written once · 07:04') },
    ])

    // 7. a person clicks Apply
    tl.add(s.cursor, { opacity: [0, 1] }, 10.15, 0.3, 'out')
    tl.add(s.cursor, { x: [-80, 0], y: [62, 0] }, 10.15, 0.95, 'io')
    tl.add(s.cursor, { opacity: [1, 0] }, 11.9, 0.4, 'out')
    tl.cls(s.applyBtn, 'press', 11.2, 11.36)
    tl.cls(s.oldTok, 'gone', 11.3)
    tl.cls(s.chg, 'done', 11.3)
    tl.add([s.applyBtn, s.discard], { opacity: [1, 0.4] }, 11.5, 0.4, 'out')

    // 8. the audit row lands, its key token pops
    tl.add(s.aud, { opacity: [0, 1] }, 12.45, 0.45, 'out')
    tl.add(s.aud, { x: [-28, 0] }, 12.45, 0.7, 'out')
    kit.pop(s.keyTok, { at: 12.85, dur: 0.55, from: 0.6 })
  },
})
