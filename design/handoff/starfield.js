/* Command Studio · starfield
   The canvas sky from the mockups (assets/accent.js), framework-free. Power-law star sizes (mostly pinpricks,
   a rare bright star), colour temperature in dark, a soft halo on the bigger stars and diffraction spikes on
   the rarest. Deterministic: the same seed always paints the same sky.

   Presets (the values the final mockups use):
     SKY   a fixed full-screen canvas behind the app, CSS opacity var(--sky-opacity) (.4 dark, .8 light)
     SHEET inside the content sheet, masked to its margins (light only; hidden in dark)
     DUST  a 480px tile for empty fields, used as a background-image (see the `stardust` utility)

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

/* star colour temperatures in dark: [r, g, b, share]. Light draws every star in one warm ink, faded. */
export const TEMPS = [[255, 255, 255, .55], [205, 222, 255, .25], [255, 242, 224, .14], [255, 208, 170, .06]];
const LIGHT_INK = [38, 34, 28];
const LIGHT_ALPHA = 0.32;

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
