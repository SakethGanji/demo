/* 03 · Secure — "Built in from the start, not added later." One glass run inspector: six guarantees on the left,
 * an evidence pane on the right. A focus ring walks down the guarantees; for each one the evidence pane swaps to
 * the concrete proof from Run #2841 (a refused UPDATE, a refused unmocked call, an approval gate, an audit row,
 * a removed secret, sandbox limits) and the row earns its green check. It rests on six checks + the effects proof. */
Film.slide({
  index: 3,
  id: 'secure',
  name: 'Secure',
  word: 'Safety',
  kicker: '03 · How it stays safe',
  caption: 'Built in from the start, not added later.',
  dur: 11,
  css: `
    .s-secure .sc-stage { position: absolute; left: 16px; top: 0; width: 1440px; }
    .s-secure .sc-tilt { transform-origin: 50% 0%; transform-style: preserve-3d; }
    .s-secure .sc-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; height: 100%; }
    .s-secure .sc-col { position: relative; display: flex; flex-direction: column; gap: 10px; min-width: 0; min-height: 0; padding: 14px; }
    .s-secure .sc-hd { display: flex; align-items: center; gap: 10px; height: 26px; padding: 0 2px; }
    .s-secure .sc-hd .k-label { flex: none; }
    .s-secure .sc-hd .sp { flex: 1; }

    /* guarantees */
    .s-secure .sc-list { position: relative; display: flex; flex-direction: column; gap: 8px; }
    .s-secure .sc-row { position: relative; display: flex; align-items: center; gap: 14px; height: 72px; padding: 0 16px 0 14px; }
    .s-secure .sc-ic { position: relative; flex: none; display: inline-flex; }
    .s-secure .sc-ic .k-icon-tile { --kc: var(--primary); }
    .s-secure .sc-badge { position: absolute; right: -6px; top: -6px; display: inline-flex; align-items: center; justify-content: center; width: 19px; height: 19px; border-radius: 99px;
      background: var(--ok); color: #fff; box-shadow: 0 0 0 2px var(--node-bg), 0 4px 10px -4px var(--ok); }
    .s-secure .sc-badge svg { width: 11px; height: 11px; stroke-width: 3; }
    .s-secure .sc-tx { min-width: 0; flex: 1; }
    .s-secure .sc-tt { font-size: 14px; font-weight: 650; line-height: 1.3; letter-spacing: -0.01em; color: var(--fg); }
    .s-secure .sc-dt { margin-top: 2px; font-size: 12.5px; line-height: 1.4; color: var(--muted); }
    .s-secure .sc-no { flex: none; font-family: var(--mono); font-size: 11px; color: var(--faint); }
    .s-secure .sc-ring { position: absolute; left: 0; right: 0; top: 0; height: 72px; border-radius: var(--r-well); pointer-events: none;
      box-shadow: 0 0 0 1.5px color-mix(in srgb, var(--primary) 55%, transparent), 0 0 0 5px var(--brand-soft), 0 14px 30px -16px var(--primary); }

    /* evidence */
    .s-secure .sc-tabs { display: flex; gap: 6px; }
    .s-secure .sc-tab { display: inline-flex; align-items: center; justify-content: center; gap: 5px; height: 24px; min-width: 40px; padding: 0 8px; border-radius: 999px;
      font-family: var(--mono); font-size: 11px; font-weight: 600; color: var(--faint); background: var(--recess); }
    .s-secure .sc-tab svg { width: 11px; height: 11px; stroke-width: 3; display: none; }
    .s-secure .sc-tab.done { color: var(--ok-text); background: var(--ok-soft); }
    .s-secure .sc-tab.done svg { display: block; }
    .s-secure .sc-tab.cur { color: var(--primary-fg); background: linear-gradient(135deg, var(--brand-1), var(--brand-2)); box-shadow: 0 6px 14px -8px var(--primary); }
    .s-secure .sc-panes { position: relative; flex: 1; min-height: 0; }
    .s-secure .sc-ev { position: absolute; inset: 0; display: flex; flex-direction: column; justify-content: space-between; gap: 10px; }
    .s-secure .sc-evh { display: flex; align-items: center; gap: 12px; padding: 2px 2px 4px; }
    .s-secure .sc-evh .t1 { font-size: 15px; font-weight: 650; letter-spacing: -0.01em; color: var(--fg); line-height: 1.25; }
    .s-secure .sc-evh .t2 { font-size: 12px; color: var(--muted); line-height: 1.35; }
    .s-secure .sc-evh .r { margin-left: auto; }
    .s-secure .sc-card { position: relative; padding: 12px 14px; }
    .s-secure .sc-card .k-code { padding: 0; font-size: 12.5px; line-height: 1.7; }
    .s-secure .sc-line { display: flex; align-items: center; gap: 10px; font-size: 12.5px; color: var(--fg); }
    .s-secure .sc-line + .sc-line { margin-top: 8px; }
    .s-secure .sc-line .sp { flex: 1; }
    .s-secure .sc-line .k-meta { font-size: 12px; }
    .s-secure .sc-mono { font-family: var(--mono); font-size: 12px; }
    .s-secure .sc-err { font-family: var(--mono); font-size: 12px; color: var(--bad-text); }
    .s-secure .sc-bad { --raised-tint: color-mix(in srgb, var(--bad) 32%, transparent); }
    .s-secure .sc-good { --raised-tint: color-mix(in srgb, var(--ok) 32%, transparent); }
    .s-secure .sc-strikewrap { position: relative; display: inline-block; }
    .s-secure .sc-strike { position: absolute; left: -2px; right: -2px; top: 50%; height: 1.6px; margin-top: -.8px; border-radius: 2px; background: var(--bad); transform-origin: 0 50%; }
    .s-secure .sc-facts { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; }
    .s-secure .sc-fact { display: flex; flex-direction: column; gap: 3px; padding: 10px 12px; }
    .s-secure .sc-fact .v { font-family: var(--mono); font-size: 13px; font-weight: 600; color: var(--fg); }
    .s-secure .sc-sum { display: flex; align-items: center; gap: 8px; padding: 10px 14px; font-size: 12.5px; font-weight: 550; color: var(--ok-text); }
    .s-secure .sc-sum svg { color: var(--ok); }
    .s-secure .sc-sum .k-meta { margin-left: auto; font-weight: 400; }
    .s-secure .sc-eff .k-chip svg { width: 12px; height: 12px; }
    .s-secure .sc-eff .tr { height: 46px; }
    .s-secure .sc-step { display: flex; flex-direction: column; line-height: 1.25; }
    .s-secure .sc-step b { font-weight: 600; color: var(--fg); font-size: 12.5px; }
    .s-secure .sc-step span { font-size: 11px; color: var(--muted); }
    .s-secure .sc-kv { display: grid; grid-template-columns: 128px 1fr; row-gap: 9px; align-items: center; font-size: 12.5px; }
    .s-secure .sc-kv > .k { font-size: 11px; font-weight: 600; letter-spacing: .04em; text-transform: uppercase; color: var(--faint); }
    .s-secure .sc-kv > .v { display: flex; align-items: center; gap: 8px; color: var(--fg); min-width: 0; }
    .s-secure .sc-arrow { color: var(--faint); }
    .s-secure .sc-stk { display: inline-grid; }
    .s-secure .sc-person { display: flex; align-items: center; gap: 10px; }
    .s-secure .sc-person .nm { font-size: 12.5px; font-weight: 600; color: var(--fg); line-height: 1.2; }
    .s-secure .sc-person .rl { font-size: 11px; color: var(--muted); }
    .s-secure .sc-person .bt { margin-left: auto; display: flex; gap: 6px; }
    .s-secure .sc-btnwrap { display: inline-flex; }
    .s-secure .sc-res { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-top: 10px; }
    .s-secure .sc-rr { display: flex; align-items: center; gap: 10px; height: 34px; padding: 0 10px; }
    .s-secure .sc-rr .k-meta { margin-left: auto; }
    .s-secure .sc-meters { display: grid; grid-template-columns: 112px 1fr 128px; row-gap: 16px; column-gap: 14px; align-items: center; font-size: 12.5px; }
    .s-secure .sc-meters .k { display: flex; align-items: center; gap: 7px; font-weight: 600; color: var(--fg); }
    .s-secure .sc-meters .k svg { color: var(--primary); }
    .s-secure .sc-meters .v { font-family: var(--mono); font-size: 12px; color: var(--muted); text-align: right; }
    .s-secure .sc-meters .k-meter { height: 8px; }
    .s-secure .sc-meters .k-meter.off { background: var(--mask-fill), var(--recess); }
  `,
  build(area, kit) {
    const { h, icon } = kit
    const okc = (t, ic) => h('span.k-chip.ok.sm', ic ? icon(ic, 12) : null, t)
    const badc = (t) => h('span.k-chip.bad.sm', icon('xCircle', 12), t)

    const win = kit.window({
      title: 'Run #2841 · Release stuck wires · Test run',
      right: h('span.k-chip.brand.sm', icon('shield', 12), 'Policy: bank-default'),
      w: 1440, h: 600,
    })
    const grid = h('div.sc-grid')
    win.body.appendChild(grid)

    /* ── left: the six guarantees ── */
    const G = [
      ['lock', 'Read-only by default', 'connections run SELECT only, inside a read-only transaction.'],
      ['shieldCheck', 'Every step declares its effect', 'none, network, database or a person. Test runs refuse anything unmocked.'],
      ['userCheck', 'Writes wait for a person', 'notebook changes go through an approval gate first.'],
      ['history', 'Written once, on the record', 'idempotency key, before and after values, who applied it and when.'],
      ['key', 'Secrets locked to their hosts', 'encrypted, bound to allowed hosts, removed from run output.'],
      ['cpu', 'Code runs in a sandbox', 'no network, no privileges, memory and CPU limits.'],
    ]
    const ring = h('div.sc-ring')
    const rows = G.map(([ic, tt, dt], i) => {
      const badge = h('span.sc-badge', icon('check', 11))
      const el = h('div.sc-row.k-raised',
        h('span.sc-ic', h('span.k-icon-tile.lg', icon(ic, 20)), badge),
        h('div.sc-tx', h('div.sc-tt', tt), h('div.sc-dt', dt)),
        h('span.sc-no', '0' + (i + 1)))
      return { el, badge }
    })
    const list = h('div.sc-list', rows.map((r) => r.el), ring)
    const counter = h('span.k-chip.ok.sm')
    counter.textContent = '0 of 6 proven'
    const left = h('div.sc-col.k-well', h('div.sc-hd', h('span.k-label', 'Guarantees · checked on this run'), h('span.sp'), counter), list)

    /* ── right: the evidence pane ── */
    const tabs = G.map((g, i) => h('span.sc-tab', icon('check', 11), '0' + (i + 1)))
    const evHead = (ic, t1, t2, r) => h('div.sc-evh', h('span.k-icon-tile', icon(ic, 16)), h('div', h('div.t1', t1), h('div.t2', t2)), r ? h('span.r', r) : null)
    const sum = (text, meta) => h('div.sc-sum.k-raised.sc-good', icon('checkCircle', 15), text, meta ? h('span.k-meta', meta) : null)

    // 1 · read-only
    const p1sel = h('div.sc-card.k-raised',
      kit.code([
        [['SET TRANSACTION READ ONLY', 'k'], [';', '']],
        [['SELECT', 'k'], [' id, amount, status, age ', ''], ['FROM', 'k'], [' bank.wires', 'f']],
        [['  WHERE', 'k'], [' status = ', ''], ["'stuck'", 's'], [' AND age > ', ''], ["interval '1 day'", 's'], [';', '']],
      ]).el,
      h('div.k-hr', { style: { margin: '10px 0' } }),
      h('div.sc-res', [['W1', '$100.50', 'stuck 3 days'], ['W2', '$12,500.00', 'stuck 4 days']].map(([id, amt, st]) =>
        h('div.sc-rr.k-recess', h('span.k-tok', id), h('span.sc-mono', amt), h('span.k-meta', st)))),
      h('div.sc-line', { style: { marginTop: '10px' } }, h('span.k-meta', '2 rows · 41 ms'), h('span.sp'), null))
    const p1ok = okc('allowed', 'check')
    p1sel.lastChild.appendChild(p1ok)
    const strike = h('span.sc-strike')
    const p1upd = h('div.sc-card.k-raised.sc-bad',
      h('div.sc-line',
        h('span.sc-strikewrap', h('span.sc-mono', h('span', { style: { color: 'var(--syn-k)' } }, 'UPDATE'), ' bank.wires ', h('span', { style: { color: 'var(--syn-k)' } }, 'SET'), " status = 'released'"), strike),
        h('span.sp')),
      h('div.sc-line', h('span.sc-err', 'ERROR: cannot execute UPDATE in a read-only transaction')))
    const p1bad = badc('refused · read-only')
    p1upd.firstChild.appendChild(p1bad)
    const p1facts = h('div.sc-facts',
      [['Connection', 'bank'], ['Role', 'bank_ro'], ['Allowed', 'SELECT']].map(([k, v]) => h('div.sc-fact.k-raised', h('span.k-label', k), h('span.v', v))))
    const pane1 = h('div.sc-ev',
      evHead('lock', 'Read-only by default', 'Postgres · bank · every query runs in a read-only transaction', h('span.k-chip.sm', icon('database', 12), 'Query step')),
      p1sel, p1upd, p1facts,
      sum('The write never reached the table.', 'bank.wires unchanged'))

    // 2 · effects
    const EF = { none: ['dot', 'none'], database: ['database', 'database'], network: ['globe', 'network'], person: ['user', 'a person'] }
    const eff = (k) => h('span.k-chip.sm', icon(EF[k][0], 12), EF[k][1])
    const ST = [
      ['Schedule', 'Weekdays · 07:00', 'none', okc('ran')],
      ['Query', 'SELECT on bank, read-only', 'database', h('span.k-chip.brand.sm', icon('layers', 12), 'mocked')],
      ['Rule', 'amount over 50', 'none', okc('ran')],
      ['HTTP', 'check source API', 'network', badc('refused · unmocked')],
      ['Approval', 'A person decides', 'person', h('span.k-chip.brand.sm', icon('layers', 12), 'mocked')],
    ]
    const etab = kit.table({
      cols: '1.35fr 128px 168px', head: ['Step', 'Declared effect', 'In this test run'], cls: 'sc-eff',
      rows: ST.map(([n, m, e, r]) => [h('span.sc-step', h('b', n), h('span', m)), eff(e), r]),
    })
    etab.rows[3].classList.add('sc-bad')
    const p2ref = h('div.sc-card.k-raised.sc-bad',
      h('div.sc-line', h('span', { style: { color: 'var(--bad)', display: 'inline-flex' } }, icon('xCircle', 16)),
        h('span', h('b', { style: { fontWeight: 600 } }, 'HTTP · check source API'), h('span.k-meta', '  →  api.bank.internal'))),
      h('div.sc-line', h('span.k-meta', 'No mock declared for a network step, so the test run refused it before it left.')))
    const pane2 = h('div.sc-ev',
      evHead('shieldCheck', 'Every step declares its effect', 'Test runs refuse anything unmocked', h('span.k-chip.sm', icon('flask', 12), 'Test run')),
      etab.el, p2ref,
      sum('4 steps ran · 1 refused', 'no real system touched'))

    // 3 · approval gate
    const waiting = h('span.k-chip.warn.sm', icon('clock', 12), 'waiting for a person')
    const approved = h('span.k-chip.ok.sm', icon('check', 12), 'approved · 07:02')
    const gateState = h('span.sc-stk')
    const approveBtn = h('span.k-btn.sm', icon('check', 12), 'Approve')
    const p3card = h('div.sc-card.k-raised',
      h('div.sc-line', h('span.k-label', 'Proposed change · from the notebook'), h('span.sp'), gateState),
      h('div.k-hr', { style: { margin: '12px 0' } }),
      h('div.sc-kv',
        h('span.k', 'Row'), h('span.v', h('span.k-tok', 'W2'), h('span.k-meta', '$12,500.00 · stuck 4 days')),
        h('span.k', 'Change'), h('span.v', h('span.k-tok', 'status'), h('span.sc-mono', 'stuck'), h('span.sc-arrow', icon('arrowRight', 14)), h('span.k-tok.ok', 'released')),
        h('span.k', 'Because'), h('span.v', h('span.k-meta', 'the oldest stuck wire with amount over 50')),
        h('span.k', 'Drafted by'), h('span.v', h('span.k-tok.ai', icon('sparkles', 11), 'AI · CHANGE cell'), h('span.k-meta', 'not written')),
        h('span.k', 'Writes through'), h('span.v', h('span.k-tok', 'bank_rw'), h('span.k-meta', '1 row'))))
    const p3who = h('div.sc-card.k-raised',
      h('div.sc-person', h('span.k-av.b', 'MK'), h('div', h('div.nm', 'Maya Kent'), h('div.rl', 'Treasury ops · approver')),
        h('span.bt', h('span.sc-btnwrap', approveBtn), h('span.k-btn.sm.ghost', icon('x', 12), 'Reject'))))
    const pane3 = h('div.sc-ev',
      evHead('userCheck', 'Writes wait for a person', 'Notebook changes go through an approval gate first', h('span.k-chip.sm', icon('notebook', 12), 'Notebook')),
      p3card, p3who,
      h('div.sc-card.k-raised', h('div.sc-line', h('span', { style: { color: 'var(--primary)', display: 'inline-flex' } }, icon('lock', 15)), h('span', 'Nothing was written while it waited.'), h('span.sp'), h('span.k-meta', 'gate held 0 writes'))),
      sum('Applied only after approval', 'one write, one person'))

    // 4 · audit
    const p4card = h('div.sc-card.k-raised',
      h('div.sc-line', h('span.k-label', 'Audit row · bank_rw · bank.wires'), h('span.sp'), okc('1 row written', 'check')),
      h('div.k-hr', { style: { margin: '12px 0' } }),
      h('div.sc-kv',
        h('span.k', 'Who'), h('span.v', h('span.k-av.b', 'MK'), 'Maya Kent'),
        h('span.k', 'When'), h('span.v', h('span.sc-mono', '07:02:14')),
        h('span.k', 'Run'), h('span.v', h('span.sc-mono', 'Run #2841'), h('span.k-meta', 'Release stuck wires')),
        h('span.k', 'Row'), h('span.v', h('span.k-tok', 'W2'), h('span.k-meta', '$12,500.00')),
        h('span.k', 'Before'), h('span.v', h('span.sc-mono', "status = 'stuck'")),
        h('span.k', 'After'), h('span.v', h('span.sc-mono', "status = 'released'")),
        h('span.k', 'Key'), h('span.v', h('span.k-tok', 'idem-W2-2841-7f3a…'))))
    const p4retry = h('div.sc-card.k-raised',
      h('div.sc-line', h('span', { style: { color: 'var(--primary)', display: 'inline-flex' } }, icon('refresh', 15)),
        h('span', 'Retry with the same key'), h('span.sp'), h('span.k-chip.sm', 'skipped · already applied')))
    const pane4 = h('div.sc-ev',
      evHead('history', 'Written once, on the record', 'Idempotency key, before and after values, who and when', h('span.k-chip.sm', icon('database', 12), 'bank_rw')),
      p4card, p4retry,
      sum('Written exactly once', 'kept on the record'))

    // 5 · secrets
    const p5sec = h('div.sc-card.k-raised',
      h('div.sc-line', h('span', { style: { color: 'var(--primary)', display: 'inline-flex' } }, icon('key', 16)), h('b', { style: { fontWeight: 600 } }, 'bank-api-token'),
        h('span.k-chip.sm', icon('lock', 12), 'encrypted'), h('span.sp'), h('span.k-meta', 'bound to'), h('span.k-tok', 'api.bank.internal only')),
      h('div.sc-line', h('span.k-meta', 'Used by'), h('span.k-chip.sm', icon('globe', 12), 'HTTP · check source API'), h('span.sp'), h('span.k-meta', 'value never shown to AI or output')))
    const p5out = h('div.sc-card.k-raised',
      h('div.k-label', { style: { marginBottom: '6px' } }, 'Run output · HTTP · check source API'),
      kit.code([
        [['GET', 'k'], [' https://api.bank.internal/wires/W2', 'f']],
        [['Authorization: ', 'c'], ['[removed]', 'd']],
        [['X-Run: ', 'c'], ['2841', 'n']],
        [['200 OK', 's'], [' · 112 ms  ', 'c'], ['{ "id": "W2", "status": "stuck" }', '']],
      ], { gutter: false }).el)
    const p5bad = badc('refused · host not allowed')
    const p5ref = h('div.sc-card.k-raised.sc-bad',
      h('div.sc-line', h('span.sc-mono', 'POST https://paste.example.net'), h('span.sp'), p5bad),
      h('div.sc-line', h('span.k-meta', 'The token is never sent to a host it is not bound to.')))
    const pane5 = h('div.sc-ev',
      evHead('key', 'Secrets locked to their hosts', 'Encrypted, bound to allowed hosts, removed from run output', h('span.k-chip.sm', icon('globe', 12), 'HTTP step')),
      p5sec, p5out, p5ref,
      sum('0 secrets in the run output', 'token stayed on its host'))

    // 6 · sandbox
    const meters = [
      ['globe', 'Network', 0, 'off'],
      ['shield', 'Privileges', 0, 'none'],
      ['box', 'Memory', 0.38, '97 of 256 MB'],
      ['cpu', 'CPU', 0.22, '1 core'],
    ]
    const fills = []
    const p6m = h('div.sc-card.k-raised',
      h('div.sc-meters', meters.map(([ic, k, v, t]) => {
        const f = h('span', { style: { width: (v * 100).toFixed(0) + '%' } }); fills.push(f)
        return [h('span.k', icon(ic, 14), k), h('div.k-meter' + (v ? '' : '.off'), f), h('span.v', t)]
      })))
    const p6code = h('div.sc-card.k-raised',
      h('div.k-label', { style: { marginBottom: '6px' } }, 'Code · amount over 50 · python'),
      kit.code([
        [['def', 'k'], [' rule', 'f'], ['(wires):', '']],
        [['    return', 'k'], [' [w ', ''], ['for', 'k'], [' w ', ''], ['in', 'k'], [' wires ', ''], ['if', 'k'], [' w.amount > ', ''], ['50', 'n'], [']', '']],
      ], { gutter: false }).el,
      h('div.sc-res', { style: { marginTop: '8px' } }, [['W1', '$100.50'], ['W2', '$12,500.00']].map(([id, amt]) =>
        h('div.sc-rr.k-recess', h('span.k-tok', id), h('span.sc-mono', amt), h('span.k-meta', 'over 50')))),
      h('div.sc-line', { style: { marginTop: '10px' } }, h('span.k-meta', '2 of 2 wires · 18 ms'), h('span.sp'), okc('exited cleanly', 'check')))
    const pane6 = h('div.sc-ev',
      evHead('cpu', 'Code runs in a sandbox', 'No network, no privileges, memory and CPU limits', h('span.k-chip.sm', icon('code', 12), 'Code step')),
      p6m, p6code,
      sum('Ran inside its limits', 'no network, no privileges'))

    const panes = [pane1, pane2, pane3, pane4, pane5, pane6]
    const right = h('div.sc-col.k-well', h('div.sc-hd', h('span.k-label', 'Evidence'), h('span.sp'), h('span.sc-tabs', tabs)), h('div.sc-panes', panes))

    grid.append(left, right)
    const tilt = h('div.sc-tilt', win.el)
    area.appendChild(h('div.sc-stage', tilt))
    return {
      tilt, left, right, rows, ring, counter, tabs, panes,
      p1: { sel: p1sel, ok: p1ok, upd: p1upd, strike, bad: p1bad, facts: p1facts },
      p2: { rows: etab.rows, ref: p2ref, bad: etab.rows[3].lastChild.firstChild },
      p3: { waiting, approved, gateState, approveBtn, card: p3card, who: p3who },
      p4: { card: p4card, retry: p4retry },
      p5: { sec: p5sec, out: p5out, ref: p5ref, bad: p5bad },
      p6: { m: p6m, code: p6code, fills },
    }
  },
  timeline(tl, kit, s) {
    // hero tilts in and rests at 7deg
    tl.add(s.tilt, { opacity: [0, 1] }, 0.5, 0.9, 'out')
    tl.add(s.tilt, { rx: [26, 7], s: [0.9, 1], y: [40, 0] }, 0.4, 2.6, 'soft')

    // guarantees arrive dimmed (not yet proven); the evidence pane follows
    s.rows.forEach((r, i) => tl.add(r.el, { opacity: [0, 0.5], y: [10, 0] }, 1.0 + i * 0.08, 0.6, 'out'))
    tl.add(s.counter, { opacity: [0, 1] }, 1.2, 0.5, 'out')
    tl.add(s.right.querySelector('.sc-hd'), { opacity: [0, 1], y: [8, 0] }, 1.3, 0.6, 'out')

    // the scanner: one guarantee at a time (the 6th is quicker), then back to #2 for the rest frame
    const T = [2.2, 3.5, 4.8, 6.1, 7.4, 8.7]
    const T_BACK = 9.65
    const ROW = 80
    const CHECK = (k) => T[k] + (k === 5 ? 0.45 : 0.6)
    tl.add(s.ring, { opacity: [0, 1] }, T[0] - 0.15, 0.35, 'out')
    for (let k = 1; k < 6; k++) tl.add(s.ring, { y: [(k - 1) * ROW, k * ROW] }, T[k] - 0.3, 0.38, 'io')
    tl.add(s.ring, { y: [5 * ROW, ROW] }, T_BACK - 0.1, 0.6, 'io')

    s.rows.forEach((r, k) => {
      tl.add(r.el, { opacity: [0.5, 1] }, T[k] - 0.1, 0.35, 'out')
      kit.pop(r.badge, { at: CHECK(k), dur: 0.5, from: 0.4 })
    })
    tl.fn(0, 0, (p, t) => {
      const n = T.filter((_, k) => t >= CHECK(k)).length
      const txt = n + ' of 6 proven'
      if (s.counter.textContent !== txt) s.counter.textContent = txt
      let cur = -1
      T.forEach((tk, k) => { if (t >= tk - 0.05) cur = k })
      if (t >= T_BACK) cur = 1
      s.tabs.forEach((tb, k) => { tb.classList.toggle('cur', k === cur); tb.classList.toggle('done', k !== cur && t >= CHECK(k)) })
    })

    // evidence panes cross-fade with the scanner
    s.panes.forEach((pn, k) => {
      tl.add(pn, { opacity: [0, 1], y: [12, 0] }, T[k], 0.45, 'out')
      const outAt = k < 5 ? T[k + 1] - 0.08 : T_BACK - 0.08
      tl.add(pn, { opacity: [1, 0] }, outAt, 0.28, 'in')
      tl.add(pn, { y: [0, -8] }, outAt, 0.28, 'in')
    })
    tl.add(s.panes[1], { opacity: [0, 1] }, T_BACK + 0.05, 0.5, 'out')
    tl.add(s.panes[1], { y: [12, 0] }, T_BACK + 0.05, 0.5, 'out')

    // 1 · read-only: the SELECT is allowed, the UPDATE is struck through and refused
    let a = T[0]
    kit.reveal([s.p1.sel, s.p1.upd, s.p1.facts], { at: a + 0.1, stagger: 0.12, y: 8, dur: 0.45 })
    kit.pop(s.p1.ok, { at: a + 0.4 })
    tl.add(s.p1.strike, { sx: [0, 1] }, a + 0.55, 0.35, 'io')
    kit.pop(s.p1.bad, { at: a + 0.75 })

    // 2 · effects: steps list in, the unmocked HTTP call is refused
    a = T[1]
    kit.reveal(s.p2.rows, { at: a + 0.1, stagger: 0.07, y: 6, dur: 0.4 })
    kit.pop(s.p2.bad, { at: a + 0.6 })
    kit.reveal(s.p2.ref, { at: a + 0.7, y: 8, dur: 0.45 })

    // 3 · approval: waiting → the approver presses Approve → approved
    a = T[2]
    kit.reveal([s.p3.card, s.p3.who], { at: a + 0.1, stagger: 0.12, y: 8, dur: 0.45 })
    kit.swap(s.p3.gateState, [{ at: a + 0.2, text: s.p3.waiting }, { at: a + 0.85, text: s.p3.approved }])
    tl.cls(s.p3.approveBtn, 'press', a + 0.65, a + 0.82)

    // 4 · audit: the row is written, the retry is skipped
    a = T[3]
    kit.reveal(s.p4.card, { at: a + 0.1, y: 8, dur: 0.45 })
    kit.reveal(s.p4.card.querySelectorAll('.sc-kv > *'), { at: a + 0.25, stagger: 0.03, y: 4, dur: 0.35 })
    kit.reveal(s.p4.retry, { at: a + 0.75, y: 8, dur: 0.45 })

    // 5 · secrets: output shows [removed], the unbound host is refused
    a = T[4]
    kit.reveal([s.p5.sec, s.p5.out, s.p5.ref], { at: a + 0.1, stagger: 0.12, y: 8, dur: 0.45 })
    kit.pop(s.p5.bad, { at: a + 0.7 })

    // 6 · sandbox (quicker): meters fill
    a = T[5]
    kit.reveal([s.p6.m, s.p6.code], { at: a + 0.05, stagger: 0.1, y: 8, dur: 0.4 })
    tl.stagger(s.p6.fills, { sx: [0, 1] }, a + 0.2, 0.06, 0.5, 'out')
  },
})
