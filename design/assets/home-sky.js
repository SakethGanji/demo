/* Projects home only: a denser sky with a faint Milky Way band and the logo's two colours over a very slight gradient.
   The same composition in light and dark; only the palette changes. Stars thin out toward the cards so the content stays calm. No planet, nothing moves.
   Painted once on a canvas (seeded, so it is the same every time); repaints on resize and theme change. */
(function () {
  const root = document.documentElement;
  function rng(seed) { let x = seed % 2147483647; if (x <= 0) x += 2147483646; return () => (x = (x * 16807) % 2147483647) / 2147483647; }
  const TEMPS = [[255, 255, 255, .55], [205, 222, 255, .27], [255, 242, 224, .13], [255, 208, 170, .05]];
  const temp = (u) => { let a = 0; for (const t of TEMPS) { a += t[3]; if (u < a) return t; } return TEMPS[0]; };

  function star(ctx, x, y, r, a, t, dark) {
    const col = (al) => `rgba(${t[0]},${t[1]},${t[2]},${Math.min(1, al)})`;
    if (dark && r > 1.05) { const g = ctx.createRadialGradient(x, y, 0, x, y, r * 4.5); g.addColorStop(0, col(a * .32)); g.addColorStop(1, col(0));
      ctx.fillStyle = g; ctx.beginPath(); ctx.arc(x, y, r * 4.5, 0, 7); ctx.fill(); }
    if (dark && r > 2.3) { ctx.lineWidth = .6; [[1, 0], [0, 1]].forEach(([dx, dy]) => { const L = r * 8, g = ctx.createLinearGradient(x - dx * L, y - dy * L, x + dx * L, y + dy * L);
      g.addColorStop(0, col(0)); g.addColorStop(.5, col(a * .75)); g.addColorStop(1, col(0)); ctx.strokeStyle = g;
      ctx.beginPath(); ctx.moveTo(x - dx * L, y - dy * L); ctx.lineTo(x + dx * L, y + dy * L); ctx.stroke(); }); }
    ctx.fillStyle = col(a); ctx.beginPath(); ctx.arc(x, y, r, 0, 7); ctx.fill();
  }

  function paint() {
    const host = document.querySelector('.hs-scene'); if (!host) return;
    const cv = host.querySelector('canvas'), dark = root.classList.contains('dark');
    const w = document.documentElement.clientWidth, h = Math.max(document.body.scrollHeight, innerHeight);
    host.style.height = h + 'px';
    const dpr = Math.min(2, devicePixelRatio || 1), ctx = cv.getContext('2d');
    cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr); cv.style.width = w + 'px'; cv.style.height = h + 'px';
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.clearRect(0, 0, w, h);

    // One composition in both themes; only the palette changes.
    // 1. a slight gradient: a tint at the top that settles into the page colour by the time the cards begin
    const g = ctx.createLinearGradient(0, 0, 0, 760);
    if (dark) { g.addColorStop(0, '#0c1120'); g.addColorStop(.55, '#07090f'); g.addColorStop(1, '#050507'); }
    else { g.addColorStop(0, '#ffffff'); g.addColorStop(1, '#ffffff'); }          // light: pure white, the blooms carry the colour
    ctx.fillStyle = g; ctx.fillRect(0, 0, w, h);

    // 2. the logo's two colours: steel blue from the top left, aqua from the right, a brighter meeting point behind
    //    the greeting. Light mode reads it as dawn; dark mode as a faint nebula.
    const clear = (c) => c.replace(/[\d.]+\)$/, '0)');           // fade to the same colour at 0 alpha; fading to black turns it grey
    const bloom = (x, y, r, c) => { const gr = ctx.createRadialGradient(x, y, 0, x, y, r); gr.addColorStop(0, c); gr.addColorStop(1, clear(c));
      ctx.fillStyle = gr; ctx.fillRect(x - r, y - r, r * 2, r * 2); };
    bloom(w * .1, -60, w * .55, dark ? 'rgba(74,118,222,.08)' : 'rgba(74,118,222,.34)');
    bloom(w * .97, 200, w * .42, dark ? 'rgba(56,186,204,.05)' : 'rgba(56,186,204,.34)');
    bloom(w * .62, -80, w * .3, dark ? 'rgba(56,186,204,.03)' : 'rgba(56,186,204,.18)');
    bloom(w * .4, 150, w * .26, dark ? 'rgba(120,150,230,.025)' : 'rgba(255,255,255,.28)');

    // stars are full strength at the top and thin out toward the cards
    const fall = (y) => 1 - .65 * Math.min(1, Math.max(0, (y - 120) / 640));
    const ink = () => [28, 52, 118];                              // light mode: deep navy stars, same positions as dark
    const k = dark ? 1 : .85;

    // 3. the sky
    const S = rng(101), n = Math.round(w * h / 1100);
    for (let i = 0; i < n; i++) { const x = S() * w, y = S() * h, u = S(), a = (.16 + Math.pow(S(), 2) * .55) * k * fall(y), t = temp(S());
      star(ctx, x, y, .25 + Math.pow(u, 14) * 2.6, a, dark ? t : ink(), true); }

    // 4. a faint Milky Way band across the top, rising to the left
    const top = 520, band = (x) => top * .12 + (x / w) * top * .55;
    for (let i = 0; i < 6; i++) { const x = (i + .5) * w / 6, y = band(x), r = w * .16, gl = ctx.createRadialGradient(x, y, 0, x, y, r);
      const bc = dark ? 'rgba(150,175,235,.03)' : 'rgba(60,100,200,.06)'; gl.addColorStop(0, bc); gl.addColorStop(1, clear(bc));
      ctx.fillStyle = gl; ctx.fillRect(x - r, y - r, r * 2, r * 2); }
    const B = rng(202), dust = Math.round(w * 1.4);
    for (let i = 0; i < dust; i++) { const x = B() * w, y = band(x) + (B() + B() + B() - 1.5) * top * .16, r = .22 + Math.pow(B(), 10) * 1.3, a = (.1 + B() * .35) * k * fall(y), t = temp(B());
      if (y < 0) continue;
      star(ctx, x, y, r, a, dark ? t : ink(), true); }
  }

  function init() {
    if (document.querySelector('.hs-scene')) return;
    const s = document.createElement('div'); s.className = 'hs-scene'; s.setAttribute('aria-hidden', 'true');
    s.innerHTML = '<canvas></canvas>'; document.body.prepend(s);
    paint();
    let t; addEventListener('resize', () => { clearTimeout(t); t = setTimeout(paint, 120); });
    new MutationObserver(paint).observe(root, { attributes: true, attributeFilter: ['class'] });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => setTimeout(init, 80)); else setTimeout(init, 80);
})();
