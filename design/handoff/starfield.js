/* Command Studio · starfield
   The canvas sky from the mockups (assets/accent.js), framework-free. Power-law star sizes (mostly pinpricks,
   a rare bright star), colour temperature in dark, a soft halo on the bigger stars and diffraction spikes on
   the rarest. Deterministic: the same seed always paints the same sky.

   Presets (the values the final mockups use):
     SKY   a fixed full-screen canvas behind the app inside a project, CSS opacity var(--sky-opacity)
           (.16 dark, .4 light). Light paints the same stars in navy ink.
     SHEET inside the content sheet, masked to its margins (not used by the final design)
     DUST  a 480px tile for empty fields, used as a background-image (see the `stardust` utility)

   Projects home paints its own fuller sky with paintHomeSky() (assets/home-sky.js in the mockups): one
   composition in both themes. Put it on an absolutely positioned canvas the height of the page, behind <main>,
   and hide the SKY canvas and the nebula on that page.

   ── React usage ─────────────────────────────────────────────────────────────────────────────────
   import { paintStarfield, starDustDataURL, SKY } from './starfield.js';

   function Starfield() {
     const ref = useRef(null);
     useEffect(() => {
       const canvas = ref.current, root = document.documentElement;
       const draw = () => {
         const dark = root.classList.contains('dark');
         paintStarfield(canvas, { ...SKY, width: innerWidth, height: innerHeight, dark });
         root.style.setProperty('--star-dust', `url(${starDustDataURL({ dark })})`);
       };
       let t; const onResize = () => { clearTimeout(t); t = setTimeout(draw, 120); };
       const mo = new MutationObserver(draw);              // theme toggles flip the .dark class on <html>
       mo.observe(root, { attributes: true, attributeFilter: ['class'] });
       addEventListener('resize', onResize);
       draw();
       return () => { mo.disconnect(); removeEventListener('resize', onResize); clearTimeout(t); };
     }, []);
     return <canvas ref={ref} aria-hidden="true"
       style={{ position: 'fixed', inset: 0, width: '100vw', height: '100vh', pointerEvents: 'none', zIndex: 0,
                opacity: 'var(--sky-opacity)' }} />;
   }
   ──────────────────────────────────────────────────────────────────────────────────────────────── */

export const SKY = { density: 2400, seed: 11, maxRadius: 2.2, spikes: true };
export const SHEET = { density: 3200, seed: 29, maxRadius: 1.8, spikes: true };
export const DUST = { width: 480, height: 480, density: 1500, seed: 47, maxRadius: 1.5, spikes: false };

/* star colour temperatures in dark: [r, g, b, share]. Light draws every star in one navy ink, so the sky stays
   visible on white (refine pass: the old warm ink at .32 was invisible). */
export const TEMPS = [[255, 255, 255, .55], [205, 222, 255, .25], [255, 242, 224, .14], [255, 208, 170, .06]];
const LIGHT_INK = [28, 52, 118];
const LIGHT_ALPHA = 0.7;

const MIN_RADIUS = 0.28;
const SIZE_POWER = 16;     // r = MIN_RADIUS + u^16 · maxRadius
const HALO_AT = 1.05;      // dark: stars with r > 1.05 get a soft halo …
const HALO_SPREAD = 4.5;   // … r · 4.5 wide, at 30% of the star's alpha
const SPIKE_AT = 2.45;     // dark: stars with r > 2.45 get cross spikes …
const SPIKE_LENGTH = 7;    // … r · 7 each way, 0.6px, at 70% alpha

/* Park–Miller minimal standard PRNG. */
export function rng(seed) {
  let x = seed % 2147483647; if (x <= 0) x += 2147483646;
  return () => (x = (x * 16807) % 2147483647) / 2147483647;
}

export function temp(u) {
  let a = 0;
  for (const t of TEMPS) { a += t[3]; if (u < a) return t; }
  return TEMPS[0];
}

/**
 * Paint a starfield into a canvas, sized for the device pixel ratio (capped at 2).
 * @param {HTMLCanvasElement|OffscreenCanvas} canvas
 * @param {{ width:number, height:number, density?:number, seed?:number, maxRadius?:number, dark?:boolean, spikes?:boolean }} o
 *   density: CSS px² per star (higher = sparser). dark: colour temperatures, halos and spikes; light paints faint ink.
 */
export function paintStarfield(canvas, { width, height, density = SKY.density, seed = SKY.seed, maxRadius = SKY.maxRadius, dark = true, spikes = true } = {}) {
  const dpr = Math.min(2, (typeof devicePixelRatio === 'number' && devicePixelRatio) || 1);
  const ctx = canvas.getContext('2d');
  canvas.width = Math.round(width * dpr); canvas.height = Math.round(height * dpr);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.clearRect(0, 0, width, height);
  const R = rng(seed), n = Math.round(width * height / density);
  for (let k = 0; k < n; k++) {
    const x = R() * width, y = R() * height, u = R();
    const r = MIN_RADIUS + Math.pow(u, SIZE_POWER) * maxRadius;
    let a = 0.14 + Math.pow(R(), 2.2) * 0.5 + (r > 1 ? 0.3 : 0);
    const t = dark ? temp(R()) : LIGHT_INK; if (!dark) a *= LIGHT_ALPHA;
    const col = (al) => `rgba(${t[0]},${t[1]},${t[2]},${Math.min(1, al)})`;
    if (dark && r > HALO_AT) {
      const g = ctx.createRadialGradient(x, y, 0, x, y, r * HALO_SPREAD);
      g.addColorStop(0, col(a * .3)); g.addColorStop(1, col(0));
      ctx.fillStyle = g; ctx.beginPath(); ctx.arc(x, y, r * HALO_SPREAD, 0, 7); ctx.fill();
    }
    if (dark && spikes && r > SPIKE_AT) {
      ctx.lineWidth = .6;
      for (const [dx, dy] of [[1, 0], [0, 1]]) {
        const L = r * SPIKE_LENGTH, g = ctx.createLinearGradient(x - dx * L, y - dy * L, x + dx * L, y + dy * L);
        g.addColorStop(0, col(0)); g.addColorStop(.5, col(a * .7)); g.addColorStop(1, col(0)); ctx.strokeStyle = g;
        ctx.beginPath(); ctx.moveTo(x - dx * L, y - dy * L); ctx.lineTo(x + dx * L, y + dy * L); ctx.stroke();
      }
    }
    ctx.fillStyle = col(a); ctx.beginPath(); ctx.arc(x, y, r, 0, 7); ctx.fill();
  }
  return canvas;
}

/**
 * A star-dust tile as a PNG data URL, for `--star-dust` (the `stardust` utility tiles it at 480px).
 * @param {{ width?:number, height?:number, density?:number, seed?:number, maxRadius?:number, dark?:boolean, spikes?:boolean }} [opts]
 */
export function starDustDataURL(opts = {}) {
  const c = document.createElement('canvas');
  paintStarfield(c, { ...DUST, ...opts });
  return c.toDataURL();
}

/* ── Projects home sky ─────────────────────────────────────────────────────────────────────────── */

const HOME_TEMPS = [[255, 255, 255, .55], [205, 222, 255, .27], [255, 242, 224, .13], [255, 208, 170, .05]];
function homeTemp(u) { let a = 0; for (const t of HOME_TEMPS) { a += t[3]; if (u < a) return t; } return HOME_TEMPS[0]; }
/* Fade a colour to itself at 0 alpha. Fading to transparent black turns a glow grey on white. */
const clear = (c) => c.replace(/[\d.]+\)$/, '0)');

function homeStar(ctx, x, y, r, a, t, dark) {
  const col = (al) => `rgba(${t[0]},${t[1]},${t[2]},${Math.min(1, al)})`;
  if (r > 1.05) {
    const g = ctx.createRadialGradient(x, y, 0, x, y, r * 4.5); g.addColorStop(0, col(a * .32)); g.addColorStop(1, col(0));
    ctx.fillStyle = g; ctx.beginPath(); ctx.arc(x, y, r * 4.5, 0, 7); ctx.fill();
  }
  if (r > 2.3) {
    ctx.lineWidth = .6;
    for (const [dx, dy] of [[1, 0], [0, 1]]) {
      const L = r * 8, g = ctx.createLinearGradient(x - dx * L, y - dy * L, x + dx * L, y + dy * L);
      g.addColorStop(0, col(0)); g.addColorStop(.5, col(a * .75)); g.addColorStop(1, col(0)); ctx.strokeStyle = g;
      ctx.beginPath(); ctx.moveTo(x - dx * L, y - dy * L); ctx.lineTo(x + dx * L, y + dy * L); ctx.stroke();
    }
  }
  ctx.fillStyle = col(a); ctx.beginPath(); ctx.arc(x, y, r, 0, 7); ctx.fill();
}

/**
 * The Projects home sky: a slight top gradient, the logo's two colours as blooms, a denser starfield that thins
 * toward the cards, and a faint Milky Way band. The same composition in both themes; only the palette changes.
 * Width is the page width, height the full scroll height.
 * @param {HTMLCanvasElement} canvas
 * @param {{ width:number, height:number, dark?:boolean }} o
 */
export function paintHomeSky(canvas, { width: w, height: h, dark = true }) {
  const dpr = Math.min(2, (typeof devicePixelRatio === 'number' && devicePixelRatio) || 1), ctx = canvas.getContext('2d');
  canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.clearRect(0, 0, w, h);

  // 1. a slight gradient: a tint at the top in dark, flat white in light
  const g = ctx.createLinearGradient(0, 0, 0, 760);
  if (dark) { g.addColorStop(0, '#0c1120'); g.addColorStop(.55, '#07090f'); g.addColorStop(1, '#050507'); }
  else { g.addColorStop(0, '#ffffff'); g.addColorStop(1, '#ffffff'); }
  ctx.fillStyle = g; ctx.fillRect(0, 0, w, h);

  // 2. steel blue from the top left, aqua from the right, a brighter meeting point behind the greeting
  const bloom = (x, y, r, c) => { const gr = ctx.createRadialGradient(x, y, 0, x, y, r); gr.addColorStop(0, c); gr.addColorStop(1, clear(c));
    ctx.fillStyle = gr; ctx.fillRect(x - r, y - r, r * 2, r * 2); };
  bloom(w * .1, -60, w * .55, dark ? 'rgba(74,118,222,.08)' : 'rgba(74,118,222,.34)');
  bloom(w * .97, 200, w * .42, dark ? 'rgba(56,186,204,.05)' : 'rgba(56,186,204,.34)');
  bloom(w * .62, -80, w * .3, dark ? 'rgba(56,186,204,.03)' : 'rgba(56,186,204,.18)');
  bloom(w * .4, 150, w * .26, dark ? 'rgba(120,150,230,.025)' : 'rgba(255,255,255,.28)');

  // stars are full strength at the top and thin out toward the cards
  const fall = (y) => 1 - .65 * Math.min(1, Math.max(0, (y - 120) / 640));
  const k = dark ? 1 : .85;

  // 3. the sky
  const S = rng(101), n = Math.round(w * h / 1100);
  for (let i = 0; i < n; i++) {
    const x = S() * w, y = S() * h, u = S(), a = (.16 + Math.pow(S(), 2) * .55) * k * fall(y), t = homeTemp(S());
    homeStar(ctx, x, y, .25 + Math.pow(u, 14) * 2.6, a, dark ? t : LIGHT_INK, dark);
  }

  // 4. a faint Milky Way band across the top, rising to the left
  const top = 520, band = (x) => top * .12 + (x / w) * top * .55;
  for (let i = 0; i < 6; i++) {
    const x = (i + .5) * w / 6, y = band(x), r = w * .16, gl = ctx.createRadialGradient(x, y, 0, x, y, r);
    const bc = dark ? 'rgba(150,175,235,.03)' : 'rgba(60,100,200,.06)'; gl.addColorStop(0, bc); gl.addColorStop(1, clear(bc));
    ctx.fillStyle = gl; ctx.fillRect(x - r, y - r, r * 2, r * 2);
  }
  const B = rng(202), dust = Math.round(w * 1.4);
  for (let i = 0; i < dust; i++) {
    const x = B() * w, y = band(x) + (B() + B() + B() - 1.5) * top * .16, r = .22 + Math.pow(B(), 10) * 1.3, a = (.1 + B() * .35) * k * fall(y), t = homeTemp(B());
    if (y < 0) continue;
    homeStar(ctx, x, y, r, a, dark ? t : LIGHT_INK, dark);
  }
  return canvas;
}
