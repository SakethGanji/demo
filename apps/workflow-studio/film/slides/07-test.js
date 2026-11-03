/* 07 · Test — "Use case 2 we are targeting: testing."
 * One hero: the test runner window. Left, the three business cases as raised cards (the UI test expanded into its
 * five steps); right, a live browser pane that plays the UI test: the portal loads, the runner fills the username,
 * a person takes over for the password (never recorded, never shown), the runner clicks Sign in, the accounts
 * table shows and the assertion passes. Then the steps export as a Playwright spec, typed into a panel that slides
 * up over the pane, and the API and Data suites pass. The header chip counts to 3 of 3. */
Film.slide({
  index: 7,
  id: 'test',
  name: 'Test',
  word: 'Testing',
  kicker: '07 · Use case 2 · testing',
  caption: 'Use case 2 we are targeting: testing.',
  sub: 'Business cases in, tests run.',
  dur: 13,
  css: `
    .s-test .ts-stage { position: absolute; left: 0; top: 0; width: 1472px; }
    .s-test .ts-tilt { transform-origin: 50% 0%; transform-style: preserve-3d; }
    .s-test .ts-grid { display: grid; grid-template-columns: 360px 1fr; gap: 12px; height: 100%; min-height: 0; }
    .s-test .ts-count .tx { font-variant-numeric: tabular-nums; }

    /* ── left: the suites ── */
    .s-test .ts-list { display: flex; flex-direction: column; gap: 8px; min-height: 0; }
    .s-test .ts-suite { display: flex; flex-direction: column; gap: 8px; padding: 10px; min-width: 0; }
    .s-test .ts-suite.ui { flex: 1; min-height: 0; --raised-tint: color-mix(in srgb, var(--primary) 32%, transparent); box-shadow: var(--raised-hi), 0 0 0 1px var(--raised-tint), 0 14px 30px -22px var(--primary); }
    .s-test .ts-sh { display: flex; align-items: center; gap: 10px; min-width: 0; }
    .s-test .ts-sh .nm { min-width: 0; flex: 1; }
    .s-test .ts-sh .n { font-size: 13px; font-weight: 650; line-height: 1.25; color: var(--fg); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .s-test .ts-sh .m { font-size: 11.5px; line-height: 1.3; color: var(--muted); white-space: nowrap; }
    .s-test .ts-st { display: grid; justify-items: end; flex: none; }
    .s-test .ts-st > * { grid-area: 1 / 1; }

    .s-test .ts-steps { padding: 4px; }
    .s-test .ts-steps li { padding: 9px 8px; gap: 10px; }
    .s-test .ts-steps li:not(:last-child)::before { left: 17.5px; top: 29px; bottom: -9px; }
    .s-test .ts-steps li > div { flex: 1; min-width: 0; display: flex; flex-wrap: wrap; align-items: center; column-gap: 8px; }
    .s-test .ts-steps .tt { font-size: 12.5px; white-space: nowrap; }
    .s-test .ts-steps .mt { margin-left: auto; font-family: var(--mono); font-size: 11px; color: var(--faint); white-space: nowrap; }
    .s-test .ts-steps li:nth-child(3) .mt { flex-basis: 100%; margin: 5px 0 1px; display: flex; align-items: center; gap: 6px; font-family: var(--font); }
    .s-test .ts-steps.you li:nth-child(3).cur .mk { background: var(--ai); box-shadow: 0 0 0 4px var(--ai-soft); }
    .s-test .ts-steps .mt .k-mask { height: 20px; letter-spacing: .12em; }
    .s-test .ts-foot { display: flex; align-items: center; gap: 6px; padding-top: 8px; border-top: 1px solid var(--hairline); margin-top: auto; }
    .s-test .ts-exp { display: grid; }
    .s-test .ts-exp > * { grid-area: 1 / 1; justify-self: start; }
    .s-test .ts-exp .k-tok svg { width: 12px; height: 12px; }

    .s-test .ts-api { display: grid; gap: 4px; }
    .s-test .ts-call { display: grid; grid-template-columns: 40px 1fr 46px auto; align-items: center; gap: 8px; height: 28px; padding: 0 8px; border-radius: var(--r-ctl); background: var(--recess); font-size: 12px; }
    .s-test .ts-call .mth { font-family: var(--mono); font-size: 11px; font-weight: 700; color: var(--primary); }
    .s-test .ts-call .pth { font-family: var(--mono); font-size: 11.5px; color: var(--fg); white-space: nowrap; }
    .s-test .ts-call .cd { font-family: var(--mono); font-size: 11.5px; color: var(--muted); }
    .s-test .ts-call .ck { display: inline-flex; align-items: center; gap: 5px; color: var(--muted); white-space: nowrap; }
    .s-test .ts-call .ck svg { width: 13px; height: 13px; color: var(--ok); }
    .s-test .ts-flow { display: flex; align-items: center; gap: 4px; }
    .s-test .ts-flow .ar { color: var(--faint); width: 13px; height: 13px; }
    .s-test .ts-flow .k-chip.on { color: var(--primary); background: var(--brand-soft); }
    .s-test .ts-flow .k-chip.ok svg { color: var(--ok); }
    .s-test .ts-badge { display: inline-flex; align-items: center; justify-content: center; width: 20px; height: 20px; border-radius: 99px; color: #fff; background: var(--ok); box-shadow: 0 0 0 3px var(--ok-soft); }
    .s-test .ts-badge svg { width: 12px; height: 12px; stroke-width: 2.8; }

    /* ── right: the browser pane ── */
    .s-test .ts-br { position: relative; min-width: 0; overflow: hidden; }
    .s-test .ts-url { position: absolute; left: 8px; right: 8px; top: 0; height: 48px; display: flex; align-items: center; gap: 10px; padding: 0 4px; }
    .s-test .ts-url .nav { display: flex; gap: 8px; color: var(--faint); }
    .s-test .ts-url .nav svg { width: 15px; height: 15px; }
    .s-test .ts-url .nav .bk { transform: scaleX(-1); }
    .s-test .ts-url .pill { flex: 1; display: flex; align-items: center; gap: 8px; height: 30px; padding: 0 12px; border-radius: 999px; background: var(--recess); box-shadow: inset 0 0 0 1px var(--hairline); font-size: 12.5px; color: var(--fg); min-width: 0; }
    .s-test .ts-url .pill svg { width: 13px; height: 13px; color: var(--ok); }
    .s-test .ts-url .pill .u { font-family: var(--mono); font-size: 12px; white-space: nowrap; }
    .s-test .ts-url .pill .u.blank { color: var(--faint); }
    .s-test .ts-url .run { font-size: 11.5px; color: var(--muted); white-space: nowrap; }
    .s-test .ts-vp { position: absolute; left: 8px; right: 8px; top: 48px; bottom: 8px; overflow: hidden; border-radius: 10px;
      background: color-mix(in srgb, var(--node-bg) 82%, transparent); box-shadow: inset 0 0 0 1px var(--node-ring); }
    .s-test .ts-load { position: absolute; left: 0; top: 0; height: 2px; width: 100%; background: linear-gradient(90deg, var(--brand-1), var(--brand-2)); transform-origin: 0 50%; z-index: 3; }
    .s-test .ts-page { position: absolute; inset: 0; }
    .s-test .ts-pnav { position: absolute; left: 0; right: 0; top: 0; height: 48px; display: flex; align-items: center; gap: 10px; padding: 0 20px; border-bottom: 1px solid var(--hairline); }
    .s-test .ts-pnav .lg { display: inline-flex; align-items: center; justify-content: center; width: 26px; height: 26px; border-radius: 7px; color: #fff; background: linear-gradient(135deg, var(--brand-1), var(--brand-2)); }
    .s-test .ts-pnav .lg svg { width: 14px; height: 14px; }
    .s-test .ts-pnav b { font-size: 13.5px; font-weight: 650; color: var(--fg); }
    .s-test .ts-pnav .r { margin-left: auto; font-size: 12px; color: var(--muted); display: grid; }
    .s-test .ts-pnav .r > * { grid-area: 1 / 1; justify-self: end; white-space: nowrap; }

    .s-test .ts-view { position: absolute; left: 0; right: 0; top: 48px; bottom: 0; }
    .s-test .ts-card { position: absolute; left: 350px; top: 76px; width: 360px; height: 278px; }
    .s-test .ts-card > * { position: absolute; left: 22px; right: 22px; }
    .s-test .ts-card .h { top: 20px; font-size: 20px; font-weight: 700; letter-spacing: -0.02em; line-height: 26px; color: var(--fg); }
    .s-test .ts-card .sb { top: 48px; font-size: 12.5px; line-height: 18px; color: var(--muted); }
    .s-test .ts-card .lb { font-size: 11px; font-weight: 600; line-height: 14px; color: var(--muted); }
    .s-test .ts-fld { height: 36px; display: flex; align-items: center; gap: 8px; padding: 0 12px; border-radius: var(--r-ctl); background: var(--recess); box-shadow: inset 0 0 0 1px var(--input); font-size: 13px; color: var(--fg); }
    .s-test .ts-fld.focus { box-shadow: inset 0 0 0 1.5px var(--primary), 0 0 0 4px var(--brand-soft); }
    .s-test .ts-fld.pw.focus { box-shadow: inset 0 0 0 1.5px var(--ai), 0 0 0 4px var(--ai-soft); }
    .s-test .ts-fld .ph { color: var(--faint); }
    .s-test .ts-fld .dots { letter-spacing: .2em; font-size: 14px; }
    .s-test .ts-fld .k-chip { margin-left: auto; }
    .s-test .ts-card .k-btn { top: 218px; height: 38px; font-size: 13px; }

    .s-test .ts-banner { position: absolute; left: 230px; width: 600px; top: 24px; height: 34px; display: flex; align-items: center; gap: 9px; padding: 0 6px 0 12px; border-radius: 999px;
      background: color-mix(in srgb, var(--ai) 12%, var(--node-bg)); box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--ai) 35%, transparent), 0 12px 26px -16px var(--ai); color: var(--ai-text); font-size: 12.5px; white-space: nowrap; z-index: 4; }
    .s-test .ts-banner svg { width: 15px; height: 15px; }
    .s-test .ts-banner b { font-weight: 700; }
    .s-test .ts-banner .k-btn { margin-left: auto; background: var(--ai); box-shadow: none; }

    .s-test .ts-acc { position: absolute; left: 24px; right: 24px; top: 16px; }
    .s-test .ts-acc .hd { display: flex; align-items: baseline; gap: 12px; height: 34px; }
    .s-test .ts-acc .hd .h { font-size: 20px; font-weight: 700; letter-spacing: -0.02em; color: var(--fg); }
    .s-test .ts-acc .hd .m { font-size: 12px; color: var(--muted); }
    .s-test .ts-acc .k-table { margin-top: 12px; }
    .s-test .ts-acc .an { display: inline-flex; align-items: center; gap: 8px; font-weight: 600; }
    .s-test .ts-acc .an .tl { font-family: var(--mono); font-weight: 500; font-size: 11.5px; color: var(--muted); }
    .s-test .ts-toast { position: absolute; right: 20px; top: 14px; display: flex; align-items: center; gap: 8px; height: 34px; padding: 0 14px 0 10px; border-radius: 999px;
      background: color-mix(in srgb, var(--ok) 10%, var(--node-bg)); box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--ok) 35%, transparent), 0 12px 26px -16px var(--ok); color: var(--ok-text); font-size: 12.5px; font-weight: 600; white-space: nowrap; z-index: 4; }
    .s-test .ts-toast svg { width: 16px; height: 16px; color: var(--ok); }
    .s-test .ts-toast .mono { font-family: var(--mono); font-weight: 500; font-size: 11.5px; }

    .s-test .ts-cur { position: absolute; left: 0; top: 0; z-index: 6; pointer-events: none; }
    .s-test .ts-cur svg.k-i { display: block; width: 22px; height: 22px; margin: -3.5px 0 0 -3.5px; fill: var(--primary); stroke: #fff; stroke-width: 1.6; filter: drop-shadow(0 3px 6px rgb(10 20 60 / .3)); transform-origin: 4px 4px; }
    .s-test .ts-cur .lb { position: absolute; left: 14px; top: 16px; display: grid; }
    .s-test .ts-cur .lb > span { grid-area: 1 / 1; display: inline-flex; align-items: center; height: 20px; padding: 0 8px; border-radius: 999px; font-size: 11px; font-weight: 650; color: #fff; white-space: nowrap; background: var(--primary); }
    .s-test .ts-cur .lb > span.you { background: var(--ai); }
    .s-test .ts-cur.you svg.k-i { fill: var(--ai); }

    .s-test .ts-spec { position: absolute; left: 24px; right: 24px; bottom: 16px; z-index: 5; overflow: hidden; border-radius: var(--r-well);
      background: var(--node-bg); box-shadow: inset 0 0 0 1px var(--node-ring), inset 0 1px 0 rgb(255 255 255 / .4), var(--hero-depth); }
    .s-test .ts-spec .sh { display: flex; align-items: center; gap: 10px; height: 40px; padding: 0 14px; border-bottom: 1px solid var(--hairline); }
    .s-test .ts-spec .sh > svg { width: 15px; height: 15px; color: var(--muted); }
    .s-test .ts-spec .sh .m { font-size: 11.5px; color: var(--muted); }
    .s-test .ts-spec .sh .k-chip { margin-left: auto; }
    .s-test .ts-spec .k-code { padding: 10px 16px 12px; font-size: 12.5px; line-height: 1.72; }
  `,
  build(area, kit) {
    const { h, icon } = kit
    const stack = (...kids) => h('span.ts-st', kids)

    /* window */
    const countTx = h('span.tx', '0 of 3 passed')
    const count = h('span.k-chip.sm.ts-count', icon('flask', 12), countTx)
    const win = kit.window({ title: 'Tests · Customer portal', right: count, w: 1472, h: 610 })
    const grid = h('div.ts-grid')
    win.body.appendChild(grid)

    /* ── left: suites ── */
    const statusOf = () => {
      const q = h('span.k-chip.sm', 'Queued')
      const r = h('span.k-chip.sm.brand', h('span.k-dot'), 'Running')
      const p = h('span.k-chip.sm.ok', icon('check', 11), 'Passed')
      return { q, r, p, el: stack(q, r, p) }
    }
    const head = (ic, kind, name, meta, st) =>
      h('div.ts-sh', h('span.k-icon-tile', { '--kc': `var(--k-${kind})` }, icon(ic, 16)), h('div.nm', h('div.n', name), h('div.m', meta)), st.el)

    // 1 · UI test
    const uiSt = statusOf()
    const steps = kit.stepper([
      { title: 'open the portal', meta: 'portal.bank.example' },
      { title: 'fill the username', meta: 'ops.reviewer' },
      { title: 'password typed by you and not recorded' },
      { title: 'click Sign in', meta: 'text=Sign in' },
      { title: 'the accounts table shows', meta: 'expect · table' },
    ])
    steps.el.classList.add('ts-steps')
    const pwMeta = steps.items[2].querySelector('.mt')
    pwMeta.append(icon('lock', 13), h('span.k-mask', '••••••'), h('span.k-chip.sm', icon('eyeOff', 11), 'not recorded'))
    pwMeta.querySelector('svg').style.color = 'var(--faint)'
    const takeBtn = h('span.k-btn.sm.ghost', icon('hand', 13), 'Take over')
    const expBtn = h('span.k-btn.sm', icon('download', 13), 'Export Playwright')
    const expTok = h('span.k-tok.ok', icon('check', 12), 'portal-sign-in.spec.ts')
    const ui = h('div.ts-suite.ui.k-raised',
      head('browser', 'trigger', 'UI test · portal sign-in', 'Browser · 5 steps · take over any step', uiSt),
      steps.el,
      h('div.ts-foot', takeBtn, h('span.ts-exp', expBtn, expTok)))

    // 2 · API test
    const apiSt = statusOf()
    const call = (m, p, c, check) => {
      const ck = icon('checkCircle', 13)
      return { el: h('div.ts-call', h('span.mth', m), h('span.pth', p), h('span.cd', '→ ' + c), h('span.ck', ck, check)), ck }
    }
    const c1 = call('GET', '/accounts', '200', 'has 3 rows')
    const c2 = call('POST', '/transfers', '201', 'status = pending')
    const api = h('div.ts-suite.k-raised',
      head('globe', 'out', 'API test · two calls with checks', 'REST · 2 calls · 2 checks', apiSt),
      h('div.ts-api', c1.el, c2.el))

    // 3 · Data test
    const dataSt = statusOf()
    const arrow = () => icon('arrowRight', 13, 'ar')
    const f1 = h('span.k-chip.sm', icon('repeat', 11), 'for each account')
    const f2 = h('span.k-chip.sm', icon('clock', 11), 'wait 5 min')
    const f3 = h('span.k-chip.sm', icon('checkCircle', 11), 'check')
    const data = h('div.ts-suite.k-raised',
      head('database', 'data', 'Data test · for each, then wait', 'Postgres · bank · read-only', dataSt),
      h('div.ts-flow', f1, arrow(), f2, arrow(), f3))

    const list = h('div.ts-list', ui, api, data)

    /* ── right: the browser pane ── */
    const url = h('span.u.blank', 'about:blank')
    const urlBar = h('div.ts-url',
      h('span.nav', icon('arrowRight', 15, 'bk'), icon('arrowRight', 15), icon('refresh', 15)),
      h('span.pill', icon('lock', 13), url),
      h('span.run', 'Run 1 · Chromium'))
    const load = h('span.ts-load')

    // the portal page (shared nav)
    const navR1 = h('span', 'Help')
    const navR2 = h('span', 'Signed in as ops.reviewer')
    const pnav = h('div.ts-pnav', h('span.lg', icon('shield', 14)), h('b', 'Customer portal'), h('span.r', navR1, navR2))

    // sign-in view
    const userTx = h('span')
    const userFld = h('div.ts-fld', { style: { top: '98px' } }, userTx)
    const dots = h('span.dots')
    const pwPh = h('span.ph', 'Password')
    const pwChip = h('span.k-chip.sm.ai', icon('eyeOff', 11), 'not recorded')
    const pwFld = h('div.ts-fld.pw', { style: { top: '162px' } }, pwPh, dots, pwChip)
    const userPh = h('span.ph', 'Username')
    userFld.prepend(userPh)
    const signBtn = h('span.k-btn', 'Sign in')
    const card = h('div.ts-card.k-raised',
      h('div.h', 'Sign in'),
      h('div.sb', 'Use your operations account.'),
      h('div.lb', { style: { top: '82px' } }, 'Username'),
      userFld,
      h('div.lb', { style: { top: '146px' } }, 'Password'),
      pwFld,
      signBtn)
    const handBack = h('span.k-btn.sm', 'Hand back')
    const banner = h('div.ts-banner', icon('hand', 15), h('b', 'You are in control'), h('span', '· the password is typed by you and not recorded'), handBack)
    const signin = h('div.ts-view', card, banner)

    // accounts view
    const acct = (name, tail) => h('span.an', name, h('span.tl', '****' + tail))
    const tbl = kit.table({
      cols: '1fr 190px 110px 170px',
      head: ['Account', 'Type', 'Currency', h('span', { style: { display: 'block', textAlign: 'right' } }, 'Balance')],
      rows: [
        [acct('Operating ·', '4821'), 'Business checking', 'USD', h('span.num', { style: { display: 'block' } }, '$482,190.44')],
        [acct('Payroll ·', '0937'), 'Payroll', 'USD', h('span.num', { style: { display: 'block' } }, '$1,204,550.00')],
        [acct('Treasury ·', '5512'), 'Treasury', 'USD', h('span.num', { style: { display: 'block' } }, '$8,760,312.18')],
      ],
    })
    const accHd = h('div.hd', h('span.h', 'Accounts'), h('span.m', '3 accounts · balances as of 07:02'))
    const toast = h('div.ts-toast', icon('checkCircle', 16), 'assertion passed:', h('span.mono', 'accounts table shows'))
    const accounts = h('div.ts-view', h('div.ts-acc', accHd, tbl.el), toast)

    const page = h('div.ts-page', pnav, signin, accounts)

    // the cursor (wrapper tweened with x/y; no CSS transform on it)
    const curSvg = icon('cursor', 22)
    const labRun = h('span', 'Runner')
    const labYou = h('span.you', 'You')
    const cursor = h('div.ts-cur', curSvg, h('span.lb', labRun, labYou))

    // the exported spec
    const T = (s, c) => [s, c]
    const spec = kit.code([
      [T('await ', 'k'), T('page.', ''), T('goto', 'f'), T('(', ''), T("'https://portal.bank.example/sign-in'", 's'), T(')', '')],
      [T('await ', 'k'), T('page.', ''), T('fill', 'f'), T('(', ''), T("'#username'", 's'), T(', ', ''), T("'ops.reviewer'", 's'), T(')', '')],
      [T('// password entered by a person, not recorded', 'c')],
      [T('await ', 'k'), T('page.', ''), T('click', 'f'), T('(', ''), T("'text=Sign in'", 's'), T(')', '')],
      [T('await ', 'k'), T('expect', 'f'), T('(page.', ''), T('locator', 'f'), T('(', ''), T("'table'", 's'), T(')).', ''), T('toBeVisible', 'f'), T('()', '')],
    ])
    const specPanel = h('div.ts-spec',
      h('div.sh', icon('file', 15), h('span.k-tok', 'portal-sign-in.spec.ts'), h('span.m', 'Playwright · 5 steps · runs in CI'), h('span.k-chip.sm.ok', icon('download', 11), 'Exported')),
      spec.el)

    const vp = h('div.ts-vp', load, page, cursor, specPanel)
    const br = h('div.ts-br.k-well', urlBar, vp)
    grid.append(list, br)

    const tilt = h('div.ts-tilt', win.el)
    area.appendChild(h('div.ts-stage', tilt))
    return {
      tilt, count, countTx, cards: [ui, api, data], uiSt, apiSt, dataSt, steps, takeBtn, expBtn, expTok,
      c1, c2, flow: [f1, f2, f3], url, load, pnav, navR1, navR2, signin, accounts, card, userTx, userPh, userFld, pwFld, pwPh, dots, pwChip,
      signBtn, banner, handBack, accHd, rows: tbl.rows, thead: tbl.head, toast, cursor, curSvg, labRun, labYou, specPanel, specLines: spec.lines, br,
    }
  },
  timeline(tl, kit, s) {
    /* hero */
    tl.add(s.tilt, { opacity: [0, 1] }, 0.5, 0.9, 'out')
    tl.add(s.tilt, { rx: [26, 7], s: [0.9, 1], y: [40, 0] }, 0.45, 2.6, 'soft')
    kit.reveal(s.cards, { at: 1.0, stagger: 0.09, y: 10, dur: 0.6 })

    /* suite status chips: queued → running → passed */
    const status = (st, run, pass) => {
      tl.add(st.q, { opacity: [1, 0] }, run, 0.2, 'in')
      tl.add(st.r, { opacity: [0, 1] }, run, 0.25, 'out')
      tl.add(st.r, { opacity: [1, 0] }, pass, 0.18, 'in')
      tl.add(st.p, { opacity: [0, 1] }, pass, 0.25, 'out')
      tl.add(st.p, { s: [0.8, 1] }, pass, 0.5, 'pop')
    }
    const T_RUN = 1.8, T_LOAD = 2.4, T_USER = 2.6, T_PW = 3.8, T_BACK = 5.55, T_CLICK = 5.7, T_SWAP = 6.6, T_PASS = 7.7
    const T_EXP = 8.9, T_API = 10.5, T_DATA = 11.2
    status(s.uiSt, T_RUN, T_PASS + 0.1)
    status(s.apiSt, 10.0, T_API)
    status(s.dataSt, 10.65, T_DATA)

    /* header chip counts passes */
    tl.fn(0, 0, (p, t) => {
      const n = t >= T_DATA ? 3 : t >= T_API ? 2 : t >= T_PASS + 0.1 ? 1 : 0
      const tx = `${n} of 3 passed`
      if (s.countTx.textContent !== tx) s.countTx.textContent = tx
      s.count.classList.toggle('ok', n === 3)
      s.count.classList.toggle('brand', t >= T_RUN && n < 3)
    })
    tl.add(s.count, { s: [0.85, 1] }, T_DATA, 0.55, 'pop')

    /* the left stepper follows the run */
    tl.fn(0, 0, (p, t) => {
      s.steps.set(t < T_RUN ? -1 : t < T_USER ? 0 : t < T_PW ? 1 : t < T_CLICK ? 2 : t < T_SWAP ? 3 : t < T_PASS ? 4 : 5)
    })
    tl.cls(s.steps.el, 'you', T_PW, T_BACK)

    /* open the portal: the url types, the page loads */
    const U1 = 'portal.bank.example/sign-in', U2 = 'portal.bank.example/accounts'
    tl.fn(0, 0, (p, t) => {
      let u, blank = false
      if (t < T_RUN) { u = 'about:blank'; blank = true }
      else if (t < T_SWAP) u = U1.slice(0, Math.min(U1.length, Math.round((t - T_RUN) * 44)))
      else u = U2
      if (s.url.textContent !== u) s.url.textContent = u
      s.url.classList.toggle('blank', blank)
    })
    tl.add(s.load, { sx: [0, 1] }, T_RUN + 0.5, 0.7, 'io')
    tl.add(s.load, { opacity: [0, 1] }, T_RUN + 0.5, 0.1, 'out')
    tl.add(s.load, { opacity: [1, 0] }, T_LOAD + 0.75, 0.3, 'out')
    tl.add(s.pnav, { opacity: [0, 1] }, T_LOAD, 0.4, 'out')
    tl.add(s.signin, { opacity: [0, 1], y: [8, 0] }, T_LOAD + 0.08, 0.5, 'out')
    tl.add(s.navR1, { opacity: [1, 0] }, T_SWAP, 0.2, 'in')
    tl.add(s.navR2, { opacity: [0, 1] }, T_SWAP + 0.15, 0.3, 'out')

    /* the cursor: runner → username → (person) password → runner → Sign in */
    const P0 = [800, 430], PU = [470, 238], PP = [470, 302], PB = [540, 359]
    tl.add(s.cursor, { opacity: [0, 1] }, 2.5, 0.25, 'out')
    tl.add(s.cursor, { x: [P0[0], PU[0]], y: [P0[1], PU[1]] }, T_USER, 0.5, 'io')
    tl.add(s.cursor, { x: [PU[0], PP[0]], y: [PU[1], PP[1]] }, T_PW + 0.05, 0.42, 'io')
    tl.add(s.cursor, { x: [PP[0], PB[0]], y: [PP[1], PB[1]] }, T_CLICK, 0.6, 'io')
    tl.add(s.cursor, { opacity: [1, 0] }, T_SWAP + 0.5, 0.35, 'out')
    const click = (at) => { tl.add(s.curSvg, { s: [1, 0.82] }, at, 0.09, 'out'); tl.add(s.curSvg, { s: [0.82, 1] }, at + 0.1, 0.25, 'spring') }
    click(T_USER + 0.5); click(T_PW + 0.48); click(T_CLICK + 0.62)
    tl.cls(s.cursor, 'you', T_PW, T_BACK)
    tl.add(s.labYou, { opacity: [0, 1] }, T_PW, 0.2, 'out')
    tl.add(s.labRun, { opacity: [1, 0] }, T_PW, 0.15, 'out')
    tl.add(s.labYou, { opacity: [1, 0] }, T_BACK, 0.2, 'out')
    tl.add(s.labRun, { opacity: [0, 1] }, T_BACK, 0.2, 'out')

    /* fill the username */
    tl.cls(s.userFld, 'focus', T_USER + 0.55, T_PW)
    tl.step(T_USER + 0.6, (on) => { s.userPh.style.display = on ? 'none' : '' })
    kit.type(s.userTx, 'ops.reviewer', { at: T_USER + 0.62, cps: 22 })

    /* the person takes over for the password: dots only, never the text */
    tl.cls(s.takeBtn, 'press', T_PW - 0.12, T_PW + 0.04)
    tl.add(s.banner, { opacity: [0, 1], y: [-10, 0] }, T_PW, 0.4, 'spring')
    tl.add(s.banner, { opacity: [1, 0], y: [0, -8] }, T_BACK + 0.05, 0.3, 'in')
    tl.cls(s.handBack, 'press', T_BACK - 0.16, T_BACK)
    tl.cls(s.pwFld, 'focus', T_PW + 0.5, T_BACK + 0.05)
    tl.add(s.pwPh, { opacity: [1, 0] }, T_PW + 0.55, 0.05, 'linear')
    tl.fn(0, 0, (p, t) => {
      const n = t < T_PW + 0.6 ? 0 : Math.min(6, Math.floor((t - (T_PW + 0.6)) / 0.13) + 1)
      const d = '•'.repeat(n)
      if (s.dots.textContent !== d) s.dots.textContent = d
      s.pwPh.style.display = n ? 'none' : ''
    })
    kit.pop(s.pwChip, { at: T_PW + 0.65 })

    /* click Sign in → the accounts table shows */
    tl.cls(s.signBtn, 'press', T_CLICK + 0.62, T_CLICK + 0.78)
    tl.add(s.signin, { opacity: [1, 0] }, T_SWAP, 0.25, 'in')
    tl.add(s.accounts, { opacity: [0, 1] }, T_SWAP + 0.12, 0.3, 'out')
    kit.reveal(s.accHd, { at: T_SWAP + 0.15, y: 8, dur: 0.45 })
    kit.reveal([s.thead, ...s.rows], { at: T_SWAP + 0.3, stagger: 0.08, y: 8, dur: 0.45 })
    kit.pop(s.toast, { at: T_PASS - 0.15, from: 0.8 })

    /* export the Playwright spec: the panel slides up over the pane and the lines type in */
    tl.cls(s.expBtn, 'press', T_EXP - 0.16, T_EXP)
    tl.add(s.expBtn, { opacity: [1, 0] }, T_EXP + 0.05, 0.2, 'in')
    kit.pop(s.expTok, { at: T_EXP + 0.15 })
    tl.add(s.specPanel, { opacity: [0, 1] }, T_EXP + 0.05, 0.35, 'out')
    tl.add(s.specPanel, { y: [70, 0] }, T_EXP + 0.05, 0.75, 'soft')
    s.specLines.forEach((ln, i) => {
      const tx = ln.querySelector('.tx')
      tl.add(ln, { opacity: [0, 1] }, T_EXP + 0.3 + i * 0.2, 0.08, 'linear')
      tl.add(tx, { clip: [0, 1] }, T_EXP + 0.3 + i * 0.2, 0.3, 'linear')
    })

    /* the API and Data suites run */
    kit.pop([s.c1.ck, s.c2.ck], { at: 10.2, stagger: 0.18, from: 0.5 })
    tl.cls(s.flow[0], 'on', 10.75); tl.cls(s.flow[1], 'on', 10.9)
    tl.cls(s.flow[2], 'ok', 11.05)
  },
})
