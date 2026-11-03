/* 02 · Connect — "It talks to the systems a bank already has."
 * One hero object: a frosted glass globe with an etched lattice, the engine (a small glass gear) at its heart,
 * and seventeen integration tiles on its surface. Everything is projected from one pure function of t
 * (yaw eases from -60° to 50° and settles): tiles in front sit above the glass and stay crisp, tiles behind
 * sit under it (frosted by the sphere's backdrop blur, dimmed, slightly blurred). The tiles rise out of the
 * core to the surface; runs pulse out from the core to Postgres, GitHub, Jira and Teams; then the Jira
 * ticket the run created and the Teams post it made come forward as two glass cards, tethered to their tiles.
 * The pointer leans the whole object, and front tiles parallax more than back ones (CSS vars, so it works at rest). */
Film.slide({
  index: 2,
  id: 'connect',
  name: 'Connect',
  word: 'Connections',
  kicker: '02 · What it connects to',
  caption: 'It talks to the systems a bank already has.',
  headAt: 0.45,
  dur: 10,
  css: `
    .s-connect { --cn-spec: rgb(255 255 255 / .92); --cn-rim: rgb(255 255 255 / .95); --cn-fres: rgb(255 255 255 / .62); --cn-glint: color-mix(in srgb, var(--brand-2) 55%, transparent);
      --cn-body: rgb(255 255 255 / .07); --cn-body-hi: rgb(255 255 255 / .2); --cn-shade: color-mix(in srgb, var(--brand-1) 20%, transparent);
      --cn-caustic: color-mix(in srgb, var(--brand-2) 30%, transparent); --cn-aura: color-mix(in srgb, var(--primary) 20%, transparent);
      --cn-floor: color-mix(in srgb, var(--brand-1) 20%, transparent); --cn-contact: rgb(20 36 100 / .20); --cn-line: var(--primary); --cn-lat: .9;
      --cn-bead-hi: rgb(255 255 255 / .92); --cn-sheen: rgb(255 255 255 / .62); }
    :root[data-theme="dark"] .s-connect { --cn-spec: rgb(205 222 255 / .26); --cn-rim: rgb(190 210 255 / .62); --cn-fres: rgb(130 160 255 / .26); --cn-glint: color-mix(in srgb, var(--brand-2) 60%, transparent);
      --cn-body: rgb(14 24 46 / .26); --cn-body-hi: rgb(120 150 255 / .10); --cn-shade: color-mix(in srgb, var(--brand-2) 22%, transparent);
      --cn-caustic: color-mix(in srgb, var(--brand-2) 26%, transparent); --cn-aura: color-mix(in srgb, var(--brand-1) 22%, transparent);
      --cn-floor: color-mix(in srgb, var(--brand-2) 16%, transparent); --cn-contact: rgb(0 0 0 / .55); --cn-line: var(--brand-1); --cn-lat: .78;
      --cn-bead-hi: rgb(210 225 255 / .22); --cn-sheen: rgb(225 235 255 / .34); }

    .s-connect .cn-stage { position: absolute; left: 0; top: 0; width: 1472px; height: 630px; }
    .s-connect .cn-tilt { position: absolute; inset: 0; transform-origin: 50% 0%; transform-style: preserve-3d; }
    .s-connect .cn-lean { position: absolute; inset: 0; transform-origin: 34% 50%; transform: rotateX(calc(var(--ly) * -3deg)) rotateY(calc(var(--lx) * 4.5deg)); }
    .s-connect .cn-globe { position: absolute; inset: 0; }
    .s-connect .cn-layer { position: absolute; left: 0; top: 0; width: 1472px; height: 630px; overflow: visible; pointer-events: none; }
    .s-connect .cn-back { z-index: 5; }
    .s-connect .cn-front { z-index: 51; }
    .s-connect .cn-spokes { z-index: 53; }
    .s-connect .cn-links { z-index: 55; }
    /* lattice hairlines: stroked with a radial gradient, so the front fades toward the limb (fresnel) and the back is faint glass-dimmed */
    .s-connect .cn-lat { fill: none; stroke-linecap: round; stroke-linejoin: round; vector-effect: non-scaling-stroke; stroke-width: 1; }
    .s-connect .cn-front .cn-lat { stroke: url(#cn-lg-f); opacity: calc(var(--cn-lat) * .82); }
    .s-connect .cn-front .cn-lat.eq { opacity: var(--cn-lat); stroke-width: 1.2; }
    .s-connect .cn-back .cn-lat { stroke: url(#cn-lg-b); opacity: calc(var(--cn-lat) * .9); }
    .s-connect .cn-back .cn-lat.eq { opacity: var(--cn-lat); }
    .s-connect .cn-ls { stop-color: var(--cn-line); }
    /* run arcs: a soft glow under a thin bright line */
    .s-connect .cn-spokes path { fill: none; stroke: var(--cn-line); stroke-linecap: round; stroke-linejoin: round; }
    .s-connect .cn-spokes path.gl { stroke-width: 6; stroke-opacity: .16; }
    .s-connect .cn-spokes path.ln { stroke-width: 1.6; }
    .s-connect .cn-links path { fill: none; stroke: var(--cn-line); stroke-linecap: round; }
    .s-connect .cn-links path.gl { stroke-width: 6; stroke-opacity: .12; }
    .s-connect .cn-links path.ln { stroke-width: 1.5; stroke-opacity: .8; }
    .s-connect .cn-links circle { fill: var(--node-bg); stroke: var(--primary); stroke-width: 2; }

    /* the glass sphere: grain, a soft specular lobe that follows the pointer, an inner caustic opposite the light,
       a shaded limb, a fresnel glow inside the edge, and (on .cn-fres) a refractive edge band + a crisp 1px rim */
    .s-connect .cn-floor { position: absolute; z-index: 1; border-radius: 50%;
      background: radial-gradient(ellipse 36% 30% at 50% 42%, var(--cn-contact), transparent 100%), radial-gradient(closest-side, var(--cn-floor), transparent 100%); }
    .s-connect .cn-sphere { position: absolute; z-index: 50; border-radius: 50%;
      background:
        var(--grain) 0 0 / 180px 180px,
        radial-gradient(ellipse 30% 21% at calc(34% + var(--lx) * 4%) calc(22% + var(--ly) * 3%), var(--cn-spec), transparent 100%),
        radial-gradient(closest-side, transparent 80%, var(--cn-fres) 100%),
        radial-gradient(circle at 64% 80%, var(--cn-caustic), transparent 32%),
        radial-gradient(circle at 40% 34%, transparent 44%, var(--cn-shade) 98%),
        radial-gradient(circle at 36% 28%, var(--cn-body-hi), var(--cn-body) 72%);
      }   /* no box-shadow: the fresnel glow is a gradient layer and the floor carries the shadow (blur radius on a 490px disc costs frames) */
    .s-connect .cn-fres { position: absolute; z-index: 52; border-radius: 50%; pointer-events: none; }
    .s-connect .cn-fres::before, .s-connect .cn-fres::after { content: ''; position: absolute; inset: 0; border-radius: 50%;
      background: linear-gradient(140deg, var(--cn-rim) 0%, transparent 36%, transparent 60%, var(--cn-glint) 100%); }
    .s-connect .cn-fres::before { opacity: .55; -webkit-mask: radial-gradient(closest-side, transparent 86%, #000 100%); mask: radial-gradient(closest-side, transparent 86%, #000 100%); }
    .s-connect .cn-fres::after { -webkit-mask: radial-gradient(closest-side, transparent calc(100% - 2px), #000 calc(100% - 1px)); mask: radial-gradient(closest-side, transparent calc(100% - 2px), #000 calc(100% - 1px)); }
    .s-connect .cn-glint { position: absolute; z-index: 52; width: 112px; height: 38px; margin: -19px 0 0 -56px; border-radius: 50%; pointer-events: none; transform: rotate(-36deg);
      background: radial-gradient(closest-side, var(--cn-spec), transparent 100%); opacity: .9; }
    .s-connect .cn-halo.k-halo { z-index: 50; border-radius: 50%; inset: auto; padding: 1.5px; opacity: .8; }

    /* the engine at the heart: a glass gear in a glass bead, with a soft caustic aura in the sphere */
    .s-connect .cn-core { position: absolute; z-index: 52; width: 92px; height: 92px; margin: -46px 0 0 -46px; }
    .s-connect .cn-aura { position: absolute; inset: -70px; border-radius: 50%; background: radial-gradient(closest-side, var(--cn-aura), transparent 100%); }
    .s-connect .cn-bead { position: absolute; inset: 0; border-radius: 50%;
      background: radial-gradient(circle at 34% 24%, var(--cn-bead-hi), transparent 40%), var(--glass-edge), var(--glass-tint), var(--glass-fill);
      box-shadow: inset 0 1px 0 var(--hi-line), inset 0 0 0 .5px rgb(255 255 255 / .5), inset 0 -12px 20px -12px color-mix(in srgb, var(--primary) 50%, transparent),
        0 0 0 1px color-mix(in srgb, var(--primary) 16%, transparent), var(--drop), 0 16px 32px -16px var(--primary); }
    .s-connect .cn-glow { position: absolute; inset: -8px; border-radius: 50%; box-shadow: 0 0 0 3px var(--brand-soft), 0 0 30px 2px color-mix(in srgb, var(--primary) 55%, transparent); }
    .s-connect .cn-gear { position: absolute; left: 13px; top: 13px; width: 66px; height: 66px; }
    .s-connect .cn-gear svg { display: block; overflow: visible; }
    .s-connect .cn-gear .gf { fill: url(#cn-gear-fill); stroke: var(--gear-rim); stroke-width: 1.1; }
    .s-connect .cn-gear .gsh { fill: url(#cn-gear-sheen); stroke: none; }
    .s-connect .cn-gear .gh { fill: none; stroke: var(--gear-rim-2); stroke-width: 1.1; }
    .s-connect .cn-gear .gs { stop-color: var(--brand-2); }
    .s-connect .cn-gear .ge { stop-color: var(--brand-1); }
    .s-connect .cn-gear .gw { stop-color: var(--cn-sheen); }
    .s-connect .cn-cl { position: absolute; left: 50%; top: 100%; margin-top: 8px; transform: translateX(-50%); display: inline-flex; align-items: center; height: 22px; padding: 0 10px;
      border-radius: 99px; background: var(--face), var(--glass-fill); box-shadow: inset 0 1px 0 var(--hi-line), inset 0 0 0 .5px var(--node-ring), var(--drop);
      font-size: 11.5px; font-weight: 700; color: var(--fg); white-space: nowrap; }

    /* integration tiles: convex glass pills (kit finish) with a two-tone glyph; behind the sphere they go flat and dim */
    .s-connect .cn-tile { position: absolute; left: 0; top: 0; display: flex; align-items: center; gap: 8px; height: 40px; padding: 0 13px 0 6px; border-radius: 12px; white-space: nowrap;
      background: var(--face), color-mix(in srgb, var(--node-bg) 88%, transparent);
      box-shadow: inset 0 1px 0 var(--hi-line), inset 0 0 0 .5px var(--node-ring), var(--drop), 0 14px 24px -16px color-mix(in srgb, var(--kc) 60%, transparent); }
    .s-connect .cn-tile .g { display: flex; align-items: center; justify-content: center; width: 28px; height: 28px; border-radius: 8px; color: var(--kc);
      background: linear-gradient(180deg, color-mix(in srgb, var(--kc) 19%, transparent), color-mix(in srgb, var(--kc) 10%, transparent));
      box-shadow: inset 0 0 0 .5px color-mix(in srgb, var(--kc) 32%, transparent), inset 0 1px 0 var(--hi-line); }
    .s-connect .cn-tile .g svg { width: 17px; height: 17px; }
    .s-connect .cn-tile .n { font-size: 13px; font-weight: 650; letter-spacing: -0.005em; color: var(--fg); }
    .s-connect .cn-tile .n svg { width: 11px; height: 11px; margin-left: 4px; vertical-align: -1px; color: var(--faint); }
    .s-connect .cn-tile.bk { background: color-mix(in srgb, var(--node-bg) 58%, transparent); box-shadow: inset 0 0 0 .5px var(--node-ring); }
    .s-connect .cn-tile.on { box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--primary) 60%, transparent), inset 0 1px 0 var(--hi-line), 0 0 0 4px var(--brand-soft), 0 14px 30px -12px var(--primary); }
    .s-connect .cn-port { position: absolute; right: -4px; top: 50%; width: 8px; height: 8px; margin-top: -4px; border-radius: 50%; background: var(--primary);
      box-shadow: 0 0 0 2px var(--node-bg), 0 0 10px 1px color-mix(in srgb, var(--primary) 60%, transparent); opacity: 0; }
    .s-connect .k-i.cn-fill { fill: currentColor; stroke: none; }
    .s-connect .cn-tile .g text, .s-connect .cn-card .g text { font-family: var(--font); font-weight: 800; }

    .s-connect .cn-pulse { position: absolute; z-index: 54; left: 0; top: 0; }

    /* the two detail cards */
    .s-connect .cn-cards { position: absolute; inset: 0; z-index: 100; }
    .s-connect .cn-card { position: absolute; width: 440px; padding: 16px 18px 16px; }
    .s-connect .cn-ch { display: flex; align-items: center; gap: 10px; }
    .s-connect .cn-ch .g { display: flex; align-items: center; justify-content: center; width: 32px; height: 32px; border-radius: 9px; color: var(--kc);
      background: linear-gradient(180deg, color-mix(in srgb, var(--kc) 19%, transparent), color-mix(in srgb, var(--kc) 10%, transparent));
      box-shadow: inset 0 0 0 .5px color-mix(in srgb, var(--kc) 32%, transparent), inset 0 1px 0 var(--hi-line); }
    .s-connect .cn-ch .g svg { width: 19px; height: 19px; }
    .s-connect .cn-ch .nm { font-size: 13.5px; font-weight: 700; color: var(--fg); line-height: 1.2; }
    .s-connect .cn-ch .sb { font-size: 12px; color: var(--muted); line-height: 1.25; }
    .s-connect .cn-ch .sb b { font-family: var(--mono); font-weight: 600; font-size: 11.5px; color: var(--fg); }
    .s-connect .cn-ch .rt { margin-left: auto; }
    .s-connect .cn-ttl { margin-top: 12px; font-size: 16px; font-weight: 680; letter-spacing: -0.012em; line-height: 1.3; color: var(--fg); }
    .s-connect .cn-desc { margin-top: 4px; font-size: 12.5px; line-height: 1.45; color: var(--muted); }
    .s-connect .cn-fields { margin-top: 12px; display: grid; grid-template-columns: 1fr 1fr; gap: 1px; overflow: hidden; }
    .s-connect .cn-f { display: flex; flex-direction: column; gap: 4px; padding: 9px 12px; }
    .s-connect .cn-f .k-label { font-size: 11px; }
    .s-connect .cn-f .v { display: flex; align-items: center; gap: 7px; height: 24px; font-size: 13px; font-weight: 600; color: var(--fg); }
    .s-connect .cn-f .v .k-av { width: 22px; height: 22px; font-size: 10px; }
    .s-connect .cn-f .v .mono { font-family: var(--mono); font-size: 12px; }
    .s-connect .cn-f .v svg { width: 14px; height: 14px; color: var(--warn); }
    .s-connect .cn-f + .cn-f:nth-child(2), .s-connect .cn-f:nth-child(4) { box-shadow: inset 1px 0 0 var(--hairline); }
    .s-connect .cn-f:nth-child(3), .s-connect .cn-f:nth-child(4) { border-top: 1px solid var(--hairline); }

    .s-connect .cn-msg { margin-top: 12px; display: grid; grid-template-columns: 32px 1fr; column-gap: 10px; padding: 12px 14px; }
    .s-connect .cn-bot { display: flex; align-items: center; justify-content: center; width: 32px; height: 32px; border-radius: 9px; color: var(--primary-fg);
      background: linear-gradient(135deg, var(--brand-1), var(--brand-2)); box-shadow: inset 0 1px 0 rgb(255 255 255 / .35), 0 6px 14px -8px var(--primary); }
    .s-connect .cn-bot svg { width: 17px; height: 17px; }
    .s-connect .cn-who { display: flex; align-items: center; gap: 7px; font-size: 13px; font-weight: 700; color: var(--fg); line-height: 1.2; }
    .s-connect .cn-who .k-meta { font-size: 11.5px; font-weight: 500; }
    .s-connect .cn-txt { margin-top: 4px; font-size: 13px; line-height: 1.5; color: var(--fg); }
    .s-connect .cn-txt b { font-weight: 650; }
    .s-connect .cn-txt .mu { color: var(--muted); }
    .s-connect .cn-act { margin-top: 10px; display: flex; align-items: center; gap: 8px; }
    .s-connect .cn-act .k-tok { color: var(--primary); background: var(--brand-soft); }
    .s-connect .cn-act .k-btn { margin-left: auto; }
  `,
  build(area, kit) {
    const { h, html, icon } = kit
    const NS = 'http://www.w3.org/2000/svg'
    const W = 1472, H = 630
    const G = { x: 472, y: 330, r: 246 }          // the globe in area px
    const svg = (cls) => { const s = document.createElementNS(NS, 'svg'); s.setAttribute('class', 'cn-layer ' + cls); s.setAttribute('viewBox', `0 0 ${W} ${H}`); return s }
    const path = (parent, cls) => { const p = document.createElementNS(NS, 'path'); if (cls) p.setAttribute('class', cls); parent.appendChild(p); return p }

    /* glyphs: simple monochrome / two-tone marks in the palette (not brand artwork) */
    const NB = 'var(--node-bg)'
    const letterTile = (L, extra) => `<rect x="2" y="5.5" width="13" height="13" rx="3" fill="currentColor" stroke="none"/><text x="8.5" y="15.6" text-anchor="middle" font-size="9.5" fill="${NB}" stroke="none">${L}</text>${extra}`
    const GL = {
      jira: '<path d="M12 2.2 21.8 12 12 21.8 2.2 12Z" fill="currentColor" fill-opacity=".2" stroke="none"/><path d="M12 6.6 17.4 12 12 17.4 6.6 12Z" fill="currentColor" stroke="none"/>',
      github: '<path d="M5.5 10V3.8l3.6 2.7a8.4 8.4 0 0 1 5.8 0l3.6-2.7V10a6.8 6.8 0 0 1 .3 2A6.8 6.8 0 0 1 12 18.8 6.8 6.8 0 0 1 5.2 12a6.8 6.8 0 0 1 .3-2Z"/><path d="M9 21.3c-2.4-.2-3.6-1.4-4.6-3"/>',
      teams: letterTile('T', '<circle cx="18.6" cy="6.6" r="2.4"/><path d="M17.5 10.8h3a1.5 1.5 0 0 1 1.5 1.5v3.2a3.3 3.3 0 0 1-5 2.8"/>'),
      outlook: '<rect x="9" y="5" width="13" height="14" rx="2.2"/><path d="m9.6 7.6 5.9 4.4 5.9-4.4"/>' + `<rect x="2" y="6.5" width="11" height="11" rx="2.6" fill="currentColor" stroke="none"/><circle cx="7.5" cy="12" r="2.6" stroke="${NB}" stroke-width="1.9" fill="none"/>`,
      sharepoint: letterTile('S', '<circle cx="16.5" cy="8.5" r="4.8"/><circle cx="17.8" cy="16.2" r="3.6"/>'),
      slack: '<path d="M9 3.2v7.6M15 13.2v7.6M3.2 15h7.6M13.2 9h7.6" stroke-width="3.2"/>',
      confluence: '<path d="M3.5 17.2c2.6-3.6 5.6-4.9 9.2-3.5l6.6 2.6"/><path d="M20.5 6.8c-2.6 3.6-5.6 4.9-9.2 3.5L4.7 7.7"/>',
      servicenow: '<circle cx="12" cy="12" r="8.6"/><path d="M8 17.2a5 5 0 0 1 8 0" fill="currentColor" stroke="none"/><circle cx="12" cy="11.6" r="3.6" fill="currentColor" stroke="none"/>',
      mongo: '<path d="M12 2.5c3.6 3.6 5.2 7.1 5.2 10.6 0 3.6-2.3 6.1-5.2 7.6-2.9-1.5-5.2-4-5.2-7.6 0-3.5 1.6-7 5.2-10.6Z" fill="currentColor" fill-opacity=".18"/><path d="M12 6v15.6"/>',
      python: '<path d="M12 3H9.2a3 3 0 0 0-3 3v3H12M12 21h2.8a3 3 0 0 0 3-3v-3H12"/><path d="M6.2 9H5a2.5 2.5 0 0 0-2.5 2.5v1A2.5 2.5 0 0 0 5 15h7M17.8 15H19a2.5 2.5 0 0 0 2.5-2.5v-1A2.5 2.5 0 0 0 19 9h-7"/><circle cx="9.4" cy="5.7" r=".9" fill="currentColor" stroke="none"/><circle cx="14.6" cy="18.3" r=".9" fill="currentColor" stroke="none"/>',
    }
    const glyph = (name, size) => {
      if (!GL[name]) return icon(name, size)
      return html(`<svg class="k-i" viewBox="0 0 24 24" width="${size}" height="${size}" aria-hidden="true">${GL[name]}</svg>`)
    }

    /* the seventeen integrations, spread evenly over the sphere on three loose bands (lat, lon in degrees).
       Placed by a small solver (tools/g2): the middle band dips just below the core and leaves a gap where it would
       cross in front of the Engine, so no tile ever passes over the gear or its label; front tiles never overlap at rest. */
    const C = { work: 'var(--k-out)', ind: 'var(--k-flow)', data: 'var(--k-data)', trig: 'var(--k-trigger)', code: 'var(--k-code)', person: 'var(--k-person)', fg: 'var(--muted)' }
    const SYS = [
      // band A · high
      ['Jira', 'jira', C.work, 48, 20],
      ['Confluence', 'confluence', C.work, 56, 118],
      ['Webhooks', 'webhook', C.trig, 49, 195],
      ['Schedules', 'clock', C.trig, 44, 260],
      ['GitHub', 'github', C.fg, 34, 322],
      // band B · around the equator (REST APIs sits up, on the back)
      ['Postgres', 'database', C.data, -3, 50],
      ['Slack', 'slack', C.ind, -6, 121],
      ['MongoDB', 'mongo', C.data, -6, 170],
      ['Forms', 'form', C.person, 4, 227],
      ['Outlook', 'outlook', C.work, 1, 283],
      ['REST APIs', 'braces', C.code, 21, 76],
      // band C · low
      ['Microsoft Teams', 'teams', C.ind, -39, 347],
      ['ServiceNow', 'servicenow', C.data, -41, 52],
      ['Neo4j', 'graph', C.data, -52, 173],
      ['Email', 'mail', C.work, -46, 107],
      ['Python', 'python', C.code, -37, 231],
      ['SharePoint', 'sharepoint', C.work, -38, 288],
    ]
    const back = svg('cn-back'), front = svg('cn-front'), spokes = svg('cn-spokes'), links = svg('cn-links')
    // lattice strokes: radial gradients in the layer's own px, centred on the globe (front fades to the limb, back rises toward it)
    const grad = (parent, id, stops) => { const d = document.createElementNS(NS, 'defs')
      d.innerHTML = `<radialGradient id="${id}" gradientUnits="userSpaceOnUse" cx="${G.x - 30}" cy="${G.y - 40}" r="${G.r * 1.08}">` +
        stops.map(([o, a]) => `<stop offset="${o}" class="cn-ls" stop-opacity="${a}"/>`).join('') + '</radialGradient>'
      parent.appendChild(d) }
    grad(front, 'cn-lg-f', [[0, 0.46], [0.55, 0.34], [0.86, 0.16], [1, 0.07]])
    grad(back, 'cn-lg-b', [[0, 0.1], [0.7, 0.14], [1, 0.22]])
    const latB = path(back, 'cn-lat'), latF = path(front, 'cn-lat'), eqB = path(back, 'cn-lat eq'), eqF = path(front, 'cn-lat eq')

    const globe = h('div.cn-globe')
    // the perspective silhouette of the sphere is a touch larger than r (the lattice meets it exactly)
    const SR = G.r / Math.sqrt(1 - Math.pow(G.r / 2100, 2))
    const disc = (cls, pad = 0) => h(cls, { style: { left: G.x - SR - pad + 'px', top: G.y - SR - pad + 'px', width: 2 * (SR + pad) + 'px', height: 2 * (SR + pad) + 'px' } })
    const floor = h('div.cn-floor', { style: { left: G.x - G.r * 0.86 + 'px', top: G.y + G.r * 0.8 + 'px', width: G.r * 1.72 + 'px', height: G.r * 0.4 + 'px' } })
    const sphere = disc('div.cn-sphere')
    const fres = disc('div.cn-fres')
    const glint = h('span.cn-glint', { style: { left: G.x - SR * 0.38 + 'px', top: G.y - SR * 0.6 + 'px' } })
    const halo = disc('span.k-halo.cn-halo', 1.5); halo.appendChild(h('span'))

    // the engine core: a glass gear (12 teeth) in a glass bead, turning only while the scene plays
    const gearSvg = (() => {
      const R = 33, r = 27.5, n = 12, pts = []
      for (let i = 0; i < n; i++) {
        const a0 = (i / n) * Math.PI * 2, st = (Math.PI * 2) / n
        pts.push([r, a0], [r, a0 + st * 0.12], [R, a0 + st * 0.27], [R, a0 + st * 0.58], [r, a0 + st * 0.73])
      }
      const hole = 'M41 33 A8 8 0 1 0 25 33 A8 8 0 1 0 41 33 Z'
      const d = pts.map(([rad, a], i) => `${i ? 'L' : 'M'}${(33 + rad * Math.cos(a)).toFixed(1)} ${(33 + rad * Math.sin(a)).toFixed(1)}`).join(' ') + ' Z'
      return html(`<svg viewBox="0 0 66 66" width="66" height="66" aria-hidden="true"><defs><linearGradient id="cn-gear-fill" x1="0" y1="0" x2="1" y2="1"><stop offset="0" class="gs" stop-opacity=".78"/><stop offset="1" class="ge" stop-opacity=".92"/></linearGradient>` +
        `<linearGradient id="cn-gear-sheen" x1=".15" y1="0" x2=".6" y2=".8"><stop offset="0" class="gw" stop-opacity="1"/><stop offset=".55" class="gw" stop-opacity="0"/></linearGradient></defs>` +
        `<path class="gf" fill-rule="evenodd" d="${d} ${hole}"/><path class="gsh" fill-rule="evenodd" d="${d} ${hole}"/><circle class="gh" cx="33" cy="33" r="17"/></svg>`)
    })()
    const gear = h('div.cn-gear', gearSvg)
    const glow = h('span.cn-glow')
    const coreLabel = h('span.cn-cl', 'Engine')
    const core = h('div.cn-core', { style: { left: G.x + 'px', top: G.y + 'px' } }, h('span.cn-aura'), glow, h('span.cn-bead'), gear, coreLabel)

    const tiles = SYS.map(([name, gl, kc, lat, lon]) => {
      const port = name === 'Jira' || name === 'Microsoft Teams' ? h('span.cn-port') : null
      const el = h('div.cn-tile', { '--kc': kc }, h('span.g', glyph(gl, 17)), h('span.n', name, name === 'Postgres' ? icon('lock', 11) : null), port)
      return { el, name, lat, lon, port }
    })

    // pulses out of the core, one at a time
    // (each run is an arc over the glass: a soft glow path under a thin line; side = which way the arc bows)
    const PULSES = [['Postgres', 3.15, 1], ['GitHub', 3.7, -1], ['Jira', 4.25, 1], ['Microsoft Teams', 6.25, -1]]
    const pulses = PULSES.map(([name, at, side]) => {
      const tile = tiles.find((t) => t.name === name)
      const sg = path(spokes, 'gl'), sp = path(spokes, 'ln')
      const dot = h('span.k-pulse.cn-pulse')
      return { tile, at, side, sg, sp, dot }
    })

    // the two detail cards
    const JIRA = { x: 958, y: 44, w: 440, hgt: 262 }, TEAMS = { x: 958, y: 338, w: 440, hgt: 240 }
    const jHead = h('div.cn-ch', { '--kc': C.work }, h('span.g', glyph('jira', 19)), h('div', h('div.nm', 'Jira'), h('div.sb', h('b', 'PAY-1482'), ' · Payments Ops')),
      h('span.rt.k-chip.ok.sm', icon('check', 12), 'Created by a run'))
    const jTitle = h('div.cn-ttl', 'Release wire W2 · $12,500.00 stuck 4 days')
    const jDesc = h('div.cn-desc', 'Scheduled check · stuck wires older than 1 day in bank.wires')
    const fld = (label, ...v) => h('div.cn-f', h('span.k-label', label), h('span.v', ...v))
    const jFields = h('div.cn-fields.k-well',
      fld('Assignee', h('span.k-av.b', 'PN'), 'Priya Nair'),
      fld('Status', h('span.k-chip.brand.sm', 'To Do')),
      fld('Priority', icon('alert', 14), 'High'),
      fld('Run', h('span.mono', '#218 · 07:02')))
    const jira = h('div.cn-card.k-glass', { style: { left: JIRA.x + 'px', top: JIRA.y + 'px' } }, jHead, jTitle, jDesc, jFields)

    const tHead = h('div.cn-ch', { '--kc': C.ind }, h('span.g', glyph('teams', 19)), h('div', h('div.nm', 'Microsoft Teams'), h('div.sb', h('b', '#payments-ops'))),
      h('span.rt.k-meta', '07:02'))
    const tMsg = h('div.cn-msg.k-raised', h('span.cn-bot', icon('workflow', 17)),
      h('div',
        h('div.cn-who', 'Engine', h('span.k-meta', 'Scheduled check')),
        h('div.cn-txt', h('b', '3 wires'), ' stuck past the settlement window.'),
        h('div.cn-txt', h('b', '$12,600.50'), ' held · oldest W2 · $12,500.00 · 4 days.'),
        h('div.cn-txt.mu', 'The release waits for a person.')))
    const tTok = h('span.k-tok', icon('link', 12), 'PAY-1482')
    const tBtn = h('span.k-btn.sm', 'Open approval', icon('arrowUpRight', 13))
    const tAct = h('div.cn-act', tTok, tBtn)
    const teams = h('div.cn-card.k-glass', { style: { left: TEAMS.x + 'px', top: TEAMS.y + 'px' } }, tHead, tMsg, tAct)

    // tethers: tile → card
    const tether = (card) => { const g = path(links, 'gl'), p = path(links, 'ln'); const c = document.createElementNS(NS, 'circle'); c.setAttribute('r', '3.5'); links.appendChild(c); return { g, p, c, card } }
    const jLink = tether(JIRA), tLink = tether(TEAMS)

    globe.append(floor, back, ...tiles.map((t) => t.el), sphere, halo, front, fres, glint, core, spokes, ...pulses.map((p) => p.dot), links)
    const cards = h('div.cn-cards', jira, teams)
    const lean = h('div.cn-lean', globe, cards)
    const tilt = h('div.cn-tilt', lean)
    area.appendChild(h('div.cn-stage', tilt))

    return { G, tilt, globe, sphere, halo, floor, core, gear, glow, coreLabel, tiles, pulses, latB, latF, eqB, eqF,
      jira, teams, JIRA, TEAMS, jLink, tLink, jRows: [jHead, jTitle, jDesc, jFields], tRows: [tHead, tMsg, tAct], tBtn, back, front, fres, glint }
  },

  timeline(tl, kit, s) {
    const D2R = Math.PI / 180
    const G = s.G, P = 2100, PITCH = -16 * D2R, CP = Math.cos(PITCH), SP = Math.sin(PITCH)
    const TH0 = -60, TH1 = 50, TA = 0.3, TD = 9.7
    const theta = (t) => { const p = kit.clamp01((t - TA) / TD); return TH0 + (TH1 - TH0) * (1 - Math.pow(1 - p, 1.6)) }
    // view-space unit vector of (lat, lon) after the globe's yaw and the fixed pitch
    const vec = (lat, lon, th) => {
      const f0 = lat * D2R, l = (lon + th) * D2R
      const x0 = Math.cos(f0) * Math.sin(l), y0 = -Math.sin(f0), z0 = Math.cos(f0) * Math.cos(l)
      return [x0, y0 * CP - z0 * SP, y0 * SP + z0 * CP]
    }
    // perspective projection of a view-space point given in globe radii
    const scr = (x, y, z) => { const f = P / (P - z * G.r); return { x: G.x + x * G.r * f, y: G.y + y * G.r * f, z, f } }
    const proj = (lat, lon, th, rf = 1) => { const [x, y, z] = vec(lat, lon, th); const q = scr(x * rf, y * rf, z * rf); q.z = z; return q }

    /* tiles rise out of the core to the surface, one after another */
    const EMERGE0 = 1.3, EST = 0.07, ED = 0.85
    const RF = 1.04
    const emerge = s.tiles.map((_, i) => EMERGE0 + i * EST)
    const REST = TA + TD

    /* lattice: 12 half meridians + 5 parallels, split into the half in front of the glass and the half behind */
    const LINES = []
    for (let k = 0; k < 12; k++) { const pts = []; for (let la = -90; la <= 90; la += 4) pts.push([la, k * 30]); LINES.push({ pts, eq: false }) }
    for (const la of [-60, -30, 0, 30, 60]) { const pts = []; for (let lo = 0; lo <= 360; lo += 4) pts.push([la, lo]); LINES.push({ pts, eq: la === 0 }) }
    const n1 = (v) => v.toFixed(1)

    /* a run arc: leaves the core toward the viewer, reaches the glass, and runs along the surface (a great circle) to the tile */
    const ARC_N = 30
    const arc = (pl, th) => {
      const T = vec(pl.tile.lat, pl.tile.lon, th)
      const sl = Math.hypot(T[0], T[1]) || 1
      const N = [(-T[1] / sl) * pl.side, (T[0] / sl) * pl.side, 0]
      let S = [N[0] * 0.32 + T[0] * 0.3, N[1] * 0.32 + T[1] * 0.3, 0.85 + T[2] * 0.15]
      const sn = Math.hypot(S[0], S[1], S[2]); S = S.map((v) => v / sn)
      const om = Math.acos(Math.max(-1, Math.min(1, S[0] * T[0] + S[1] * T[1] + S[2] * T[2]))), so = Math.sin(om) || 1
      const pts = []
      for (let i = 0; i <= ARC_N; i++) {
        const u = i / ARC_N, a = Math.sin((1 - u) * om) / so, b = Math.sin(u * om) / so
        const rho = RF * 0.985 * (1 - Math.pow(1 - u, 2.6))
        pts.push(scr((a * S[0] + b * T[0]) * rho, (a * S[1] + b * T[1]) * rho, (a * S[2] + b * T[2]) * rho))
      }
      return pts
    }
    // the part of the arc up to progress p, skipping the stretch hidden inside the bead
    const arcD = (pts, p) => {
      const end = p * ARC_N, k = Math.floor(end)
      let d = '', head = pts[0]
      for (let i = 0; i <= Math.min(k + 1, ARC_N); i++) {
        let q = pts[i]
        if (i > end) { const r = end - k, a = pts[k]; q = { x: a.x + (q.x - a.x) * r, y: a.y + (q.y - a.y) * r } }
        head = q
        if (Math.hypot(q.x - G.x, q.y - G.y) < 44) continue
        d += (d ? 'L' : 'M') + n1(q.x) + ' ' + n1(q.y)
      }
      return { d: d || 'M0 0', head }
    }

    const lastTh = { v: null }
    const render = (tRaw) => {
      const t = Math.min(tRaw, 30)
      const th = theta(t)
      const atRest = t >= REST
      // lattice
      if (lastTh.v !== th) {
        lastTh.v = th
        let dF = '', dB = '', eF = '', eB = ''
        for (const ln of LINES) {
          let prev = null, prevSide = null, sF = '', sB = ''
          for (const [la, lo] of ln.pts) {
            const q = proj(la, lo, th)
            const side = q.z >= 0
            if (prev && side === prevSide) { if (side) sF += `L${n1(q.x)} ${n1(q.y)}`; else sB += `L${n1(q.x)} ${n1(q.y)}` }
            else if (prev) {
              // the segment crosses the limb: start the new side at the previous point (no gap)
              if (side) sF += `M${n1(prev.x)} ${n1(prev.y)}L${n1(q.x)} ${n1(q.y)}`; else sB += `M${n1(prev.x)} ${n1(prev.y)}L${n1(q.x)} ${n1(q.y)}`
            } else { if (side) sF += `M${n1(q.x)} ${n1(q.y)}`; else sB += `M${n1(q.x)} ${n1(q.y)}` }
            prev = q; prevSide = side
          }
          if (ln.eq) { eF += sF; eB += sB } else { dF += sF; dB += sB }
        }
        s.latF.setAttribute('d', dF || 'M0 0'); s.latB.setAttribute('d', dB || 'M0 0')
        s.eqF.setAttribute('d', eF || 'M0 0'); s.eqB.setAttribute('d', eB || 'M0 0')
      }
      // tiles: in front they sit on the glass, crisp; behind they go flat and dim under the frosted sphere
      s.tiles.forEach((tile, i) => {
        const e = kit.ease.out(kit.clamp01((t - emerge[i]) / ED))
        const rf = 0.12 + (RF - 0.12) * e
        const q = proj(tile.lat, tile.lon, th, rf)
        tile.q = q
        const d = q.z
        const vis = Math.min(1, e * 2.2)
        // behind: dimmer with depth, and fading where it would sit right behind the Engine
        const near = d >= 0 ? 1 : 0.3 + 0.7 * kit.clamp01((Math.hypot(q.x - G.x, (q.y - G.y - 18) * 1.25) - 70) / 60)
        const op = vis * (d >= 0 ? 1 : (1 - Math.min(1, -d / 0.5) * 0.42) * near)
        const blur = d >= 0 || e < 0.35 ? 0 : Math.round(Math.min(1, -d / 0.55) * 1.1 * 4) / 4
        const sc = (0.55 + 0.45 * e) * q.f * (d >= 0 ? 1 : 0.94)
        // stacking: front above the glass; just behind the limb also above it (so a tile straddling the silhouette is not cut by the glass); the rest under it
        const z = e < 0.35 ? 52 : d >= 0 ? 60 + Math.round(d * 30) : d > -0.28 ? 56 : 10 + Math.round((d + 1) * 30)
        const par = (0.5 + 0.5 * d) * rf
        // whole pixels at rest so the labels stay crisp
        const px = atRest ? String(Math.round(q.x)) : n1(q.x), py = atRest ? String(Math.round(q.y)) : n1(q.y)
        const tf = `translate(${px}px,${py}px) translate(calc(var(--lx) * ${(par * 16).toFixed(1)}px - 50%), calc(var(--ly) * ${(par * 10).toFixed(1)}px - 50%)) scale(${sc.toFixed(4)})`
        const st = tile.el.style
        if (tile._tf !== tf) { st.transform = tf; tile._tf = tf }
        const o = op.toFixed(3)
        if (tile._o !== o) { st.opacity = o; tile._o = o }
        if (tile._z !== z) { st.zIndex = String(z); tile._z = z }
        const bk = d < 0 && e > 0.35
        if (tile._bk !== bk) { tile.el.classList.toggle('bk', bk); tile._bk = bk }
        if (tile._b !== blur) { st.filter = blur > 0 ? `blur(${blur}px)` : 'none'; tile._b = blur }
      })
      // runs: an arc from the core over the glass to the tile, the dot at its head
      s.pulses.forEach((pl) => {
        const p = kit.ease.io(kit.clamp01((t - pl.at) / 0.55))
        const live = t >= pl.at && t <= pl.at + 0.6
        const keep = pl.tile.name === 'Jira' || pl.tile.name === 'Microsoft Teams'
        const spO = t < pl.at ? 0 : keep ? 0.6 : Math.max(0, 0.8 - Math.max(0, t - pl.at - 0.55) / 0.6)
        if (spO > 0 || live) {
          const { d, head } = arcD(arc(pl, th), p)
          pl.sp.setAttribute('d', d); pl.sg.setAttribute('d', d)
          pl.dot.style.transform = `translate(${n1(head.x)}px,${n1(head.y)}px)`
        }
        const so = spO.toFixed(3)
        if (pl._o !== so) { pl.sp.style.opacity = so; pl.sg.style.opacity = so; pl._o = so }
        const dO = live && Math.hypot(pl.tile.q.x - G.x, pl.tile.q.y - G.y) * p > 40 ? '1' : '0'
        if (pl._d !== dO) { pl.dot.style.opacity = dO; pl._d = dO }
      })
      // tethers tile → card (the card's left edge, a third of the way down)
      for (const [lk, at, tile] of [[s.jLink, 4.9, s.tiles[0]], [s.tLink, 6.85, s.tiles[11]]]) {
        const q = tile.q
        // the end rides on the card while it flies out of its tile (same motion as flyOut), so the line never hangs in space
        const b = lk.card, fp = kit.ease.soft(kit.clamp01((t - at) / 1.0)), sc = 0.18 + 0.82 * fp
        const q0 = proj(tile.lat, tile.lon, theta(at), RF), cx = b.x + b.w / 2, cy = b.y + b.hgt / 2
        const ex = cx + (b.x - cx) * sc + (q0.x - cx) * (1 - fp), ey = cy + (b.y + 64 - cy) * sc + (q0.y - cy) * (1 - fp)
        const mx = (q.x + ex) / 2
        const d = `M${n1(q.x)} ${n1(q.y)}C${n1(mx)} ${n1(q.y)} ${n1(mx)} ${n1(ey)} ${n1(ex)} ${n1(ey)}`
        lk.p.setAttribute('d', d); lk.g.setAttribute('d', d)
        lk.c.setAttribute('cx', n1(ex)); lk.c.setAttribute('cy', n1(ey))
        lk.c.style.opacity = t >= at + 0.75 ? '1' : '0'
      }
    }
    tl.fn(0, 0, (p, t) => render(t))

    /* 1 · the hero tilts in from depth and rests slightly tilted */
    tl.add(s.tilt, { opacity: [0, 1] }, 0.5, 0.9, 'out')
    tl.add(s.tilt, { rx: [24, 5], s: [0.9, 1], y: [40, 0] }, 0.5, 2.6, 'soft')
    tl.add([s.back, s.front], { opacity: [0, 1] }, 0.75, 1.0, 'out')
    tl.add([s.halo, s.glint], { opacity: [0, 1] }, 1.6, 1.0, 'out')

    /* 2 · the engine core lands; its gear turns while the scene plays and settles at rest */
    kit.pop(s.core, { at: 0.95, dur: 0.6, from: 0.6 })
    tl.add(s.gear, { rz: [0, 210] }, 0.95, 9.0, 'out')
    kit.reveal(s.coreLabel, { at: 1.25, y: 0, dur: 0.5 })
    tl.add(s.glow, { opacity: [0, 0] }, 0, 0)
    s.pulses.forEach((pl) => {
      tl.add(s.glow, { opacity: [0, 0.9] }, pl.at - 0.12, 0.15, 'out')
      tl.add(s.glow, { opacity: [0.9, 0] }, pl.at + 0.05, 0.45, 'in')
      // the tile lights as the pulse arrives (Jira and Teams stay lit)
      const keep = pl.tile.name === 'Jira' || pl.tile.name === 'Microsoft Teams'
      tl.cls(pl.tile.el, 'on', pl.at + 0.5, keep ? null : pl.at + 1.1)
    })

    /* 3 · the cards come forward out of their tiles; a port lights on the tile where the tether leaves */
    const flyOut = (card, box, tile, at) => {
      const q = proj(tile.lat, tile.lon, theta(at), RF)
      const dx = q.x - (box.x + box.w / 2), dy = q.y - (box.y + box.hgt / 2)
      tl.add(card, { opacity: [0, 1] }, at, 0.4, 'out')
      tl.add(card, { x: [dx, 0], y: [dy, 0], s: [0.18, 1], ry: [-28, 0] }, at, 1.0, 'soft')
    }
    flyOut(s.jira, s.JIRA, s.tiles[0], 4.9)
    tl.add([s.jLink.g, s.jLink.p], { draw: [0, 1], opacity: [0, 1] }, 5.15, 0.6, 'out')
    tl.add(s.tiles[0].port, { opacity: [0, 1] }, 5.1, 0.3, 'out')
    kit.reveal(s.jRows, { at: 5.0, stagger: 0.06, dur: 0.45, y: 8 })

    flyOut(s.teams, s.TEAMS, s.tiles[11], 6.85)
    tl.add([s.tLink.g, s.tLink.p], { draw: [0, 1], opacity: [0, 1] }, 7.1, 0.6, 'out')
    tl.add(s.tiles[11].port, { opacity: [0, 1] }, 7.05, 0.3, 'out')
    kit.reveal(s.tRows, { at: 6.95, stagger: 0.07, dur: 0.45, y: 8 })
    kit.pop(s.tBtn, { at: 7.7, dur: 0.5 })

  },
})
