/* Edge and empty-space vectors (JS). See DESIGN.md §6b and assets/edges.css.
   Auto-injects into the shared shell, and into any element a page marks with data-edge:
     data-edge="field"   grid + ruler in the empty space after content. Options on the same element:
                           data-edge-from="sel"    the content the field starts below (required; looked up inside the host)
                           data-edge-until="sel"   stop above this element instead of the host's bottom
                           data-edge-gap="12"      space between the content and the ruler
                           data-edge-inset="8"     space kept from the host's bottom edge
     data-edge="reg"     a registration mark in each margin, level with the element
     data-edge="leader"  a dotted leader across the element's empty middle (into its .spacer, else at the end)
   The shell gets: gutter marks wherever two gutters cross, and a field in the sidebar's empty middle.
   Everything is aria-hidden and pointer-events none. Edges.refresh() re-measures. */
(function () {
  const MIN = 40, CELL = 32;
  const mk = (cls, parent) => { const e = document.createElement('span'); e.className = cls; e.setAttribute('aria-hidden', 'true'); parent && parent.appendChild(e); return e; };
  const fields = [];
  let gut = null, raf = 0, tmo = 0;

  /* ── gutter registration marks ── */
  function gutters() {
    const ws = document.querySelector('.workspace'); if (!ws) return;
    const top = ws.querySelector(':scope > .topbar');
    const ps = [...ws.querySelectorAll('.workspace-body > .sidebar.panel, .main > .panel, .main > .editor > .panel')]
      .map((p) => p.getBoundingClientRect()).filter((r) => r.width && r.height).sort((a, b) => a.left - b.left);
    if (!top || !ps.length) return;
    if (!gut) { gut = document.createElement('div'); gut.className = 'eg-gutters'; gut.setAttribute('aria-hidden', 'true'); ws.prepend(gut); }
    const W = ws.getBoundingClientRect(), t = top.getBoundingClientRect();
    const xs = [ps[0].left - W.left];
    for (let i = 0; i < ps.length - 1; i++) xs.push((ps[i].right + ps[i + 1].left) / 2 - W.left);
    xs.push(ps[ps.length - 1].right - W.left);
    xs[0] /= 2; xs[xs.length - 1] = (xs[xs.length - 1] + W.width) / 2;
    const bodyTop = Math.min(...ps.map((r) => r.top)), bodyBot = Math.max(...ps.map((r) => r.bottom));
    const yTop = (t.top - W.top) / 2, yMid = (t.bottom + bodyTop) / 2 - W.top, yBot = (bodyBot - W.top + W.height) / 2;
    const pts = [[xs[0], yTop], [xs[xs.length - 1], yTop], ...xs.flatMap((x) => [[x, yMid], [x, yBot]])];
    while (gut.children.length < pts.length) mk('eg-reg', gut);
    while (gut.children.length > pts.length) gut.lastChild.remove();
    pts.forEach(([x, y], i) => { const s = gut.children[i].style; s.left = Math.round(x) - 4 + 'px'; s.top = Math.round(y) - 4 + 'px'; });
  }

  /* ── fields ── */
  function addField(host, o) {
    if (host.dataset.edgeOn) return; host.dataset.edgeOn = 1;
    host.classList.add('eg-host');
    const el = document.createElement('div'); el.className = 'eg-field' + (o.quiet ? ' quiet' : ''); el.setAttribute('aria-hidden', 'true');
    el.innerHTML = '<div class="eg-grid"></div><div class="eg-ruler"></div>';
    const marks = [mk('eg-reg', el), mk('eg-reg', el)];
    host.appendChild(el);
    const f = { host, el, marks, ...o };
    fields.push(f);
    if (window.ResizeObserver) {
      const ro = new ResizeObserver(schedule);
      const a = host.querySelector(o.from), u = o.until && host.querySelector(o.until);
      [host, a, u, ...(a && a.parentElement ? a.parentElement.children : [])].forEach((n) => n && n !== el && ro.observe(n));
    }
    // DOM changes (a live row, an added row, an expanded run) re-measure too; ignore our own style writes
    new MutationObserver((ms) => { if (ms.some((m) => !el.contains(m.target))) schedule(); })
      .observe(host, { childList: true, subtree: true, attributes: true, attributeFilter: ['class', 'style', 'hidden'] });
  }
  function placeField(f) {
    const { host, el, marks } = f;
    const a = host.querySelector(f.from), u = f.until && host.querySelector(f.until);
    if (!a || !a.getClientRects().length) { el.classList.remove('eg-on'); return; }
    const H = host.getBoundingClientRect(), A = a.getBoundingClientRect();
    const ox = H.left + host.clientLeft, oy = H.top + host.clientTop - host.scrollTop;
    const top = Math.round(A.bottom - oy + f.gap);
    const bottom = Math.round(u ? u.getBoundingClientRect().top - oy - f.gap : Math.min(host.clientHeight + host.scrollTop, host.scrollHeight) - f.inset);
    const left = Math.round(A.left - ox), width = Math.round(A.width), h = bottom - top;
    if (h < MIN || width < 120) { el.classList.remove('eg-on'); return; }
    Object.assign(el.style, { top: top + 'px', left: left + 'px', width: width + 'px', height: h + 'px' });
    el.classList.add('eg-on');
    // two marks on the first major grid row, near the ends of the ruler's visible span
    const row = h >= CELL + 16;
    [.25, .75].forEach((k, i) => {
      const x = Math.round((width * k) / CELL) * CELL, s = marks[i].style;
      s.display = row && x > 0 && x < width ? '' : 'none'; s.left = x - 4 + 'px'; s.top = CELL - 4 + 'px';
    });
  }

  /* ── marks and leaders on elements ── */
  function decorate() {
    document.querySelectorAll('[data-edge~="reg"]').forEach((el) => {
      if (el.querySelector(':scope > .eg-reg')) return;
      mk('eg-reg eg-l', el); mk('eg-reg eg-r', el);
    });
    document.querySelectorAll('[data-edge~="leader"]').forEach((el) => {
      if (el.querySelector('.eg-leader')) return;
      const sp = el.querySelector(':scope > .spacer');
      mk('eg-leader', sp || el);
    });
    document.querySelectorAll('[data-edge~="field"]').forEach((host) => addField(host, {
      from: host.dataset.edgeFrom, until: host.dataset.edgeUntil,
      gap: +(host.dataset.edgeGap ?? 12), inset: +(host.dataset.edgeInset ?? 8), quiet: 'edgeQuiet' in host.dataset,
    }));
    // shell: the sidebar's empty middle, between the nav and its footer
    const sb = document.querySelector('.sidebar.panel');
    if (sb && sb.querySelector('.outline-bottom')) addField(sb, { from: '.outline-scroll > :last-child', until: '.outline-bottom', gap: 20, inset: 0, quiet: true });
  }

  /* static by design: no motion, so nothing here needs prefers-reduced-motion. A timeout backs up rAF (rAF pauses in background tabs). */
  function refresh() { cancelAnimationFrame(raf); clearTimeout(tmo); raf = tmo = 0; gutters(); fields.forEach(placeField); }
  function schedule() { if (!raf) { raf = requestAnimationFrame(refresh); tmo = setTimeout(refresh, 50); } }

  function init() {
    decorate(); refresh();
    addEventListener('resize', schedule);
    addEventListener('load', schedule);
    document.addEventListener('scroll', schedule, true);
    document.addEventListener('animationend', schedule, true); // the page-enter animation moves panels without resizing them
    document.addEventListener('transitionend', schedule, true); // row expand, alert collapse
    document.fonts && document.fonts.ready.then(schedule);
  }
  window.Edges = { refresh: () => { decorate(); refresh(); } };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
