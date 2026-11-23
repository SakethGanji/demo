/**
 * Signal field: a drifting aurora (domain-warped noise) with fine light ribbons over it (raw WebGL2, one full-screen triangle, no dependencies), the
 * landing page's moving backdrop. Colours come from the brand tokens at runtime and re-read on a theme change;
 * light mode draws darker ink at a lower strength, dark mode a soft glow. The pointer gently bends the ribbons.
 *
 * Budget: DPR capped at 1, drawn at 30fps (a slow drift needs no more; it halves the GPU work), the aurora drawn at 35% resolution and upscaled (it is all soft noise), ribbon
 * sines advanced by rotation rather than recomputed, paused while the tab is hidden, one still frame under reduced motion (or the e2e
 * motion flag). No WebGL2, or a lost context: the canvas hides and the CSS backdrop shows through.
 */
import { useEffect, useRef } from 'react'
import { cn } from '@/shared/lib/utils'
import { isDarkTheme, tokenRGB } from '@/shared/lib/tokenColor'
import { useMotionOff } from '@/features/knowledge/hooks/useMotionOff'

const VERT = `#version 300 es
layout(location = 0) in vec2 p;
void main() { gl_Position = vec4(p, 0.0, 1.0); }`

const HEAD = `#version 300 es
precision highp float;
uniform vec2 uRes;
uniform float uTime;
uniform vec2 uMouse;
uniform vec3 uC1;
uniform vec3 uC2;
uniform vec3 uC3;
uniform vec3 uC4;
uniform float uDark;
out vec4 o;
float hash(vec2 p) { return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
`

/** Pass 1, at a fraction of the resolution (the aurora is all soft noise): the domain-warped colour field. */
const AURORA = `${HEAD}
float noise(vec2 p) {
  vec2 i = floor(p), f = fract(p);
  vec2 u = f * f * (3.0 - 2.0 * f);
  return mix(mix(hash(i), hash(i + vec2(1, 0)), u.x), mix(hash(i + vec2(0, 1)), hash(i + vec2(1, 1)), u.x), u.y);
}
float fbm(vec2 p) {
  float v = 0.0, a = 0.5;
  for (int i = 0; i < 4; i++) { v += a * noise(p); p = p * 2.03 + 17.1; a *= 0.5; }
  return v;
}
void main() {
  vec2 p = (gl_FragCoord.xy - 0.5 * uRes) / uRes.y;
  float t = uTime * 0.11;
  vec2 q = p * 1.4;
  vec2 w = vec2(fbm(q + vec2(t * 0.7, -t * 0.4)), fbm(q + vec2(-t * 0.5, t * 0.6) + 5.2));
  float n = fbm(q + 2.2 * w + (uMouse - p) * 0.15);
  vec3 aur = mix(uC1, uC2, smoothstep(0.25, 0.75, w.x));
  aur = mix(aur, uC3, smoothstep(0.35, 0.85, w.y));
  aur = mix(aur, uC4, smoothstep(0.62, 0.95, n) * 0.8);
  float aa = smoothstep(0.28, 0.85, n) * mix(0.42, 0.62, uDark);
  o = vec4(aur * aa, aa);
}`

/**
 * Pass 2, at full resolution: the upscaled aurora, then the fine ribbons and grain. Each ribbon's sines differ
 * only by a phase that steps evenly with the ribbon index, so they are advanced by a rotation (a few multiplies)
 * instead of four sin() calls per ribbon; ribbons far from the pixel skip their colour work.
 */
const FRAG = `${HEAD}
uniform sampler2D uAur;
float band(float d, float w) { return smoothstep(w, 0.0, abs(d)); }
const int N = 24;
const float STEP = 1.0 / float(N - 1);
void main() {
  vec2 p = (gl_FragCoord.xy - 0.5 * uRes) / uRes.y;
  float t = uTime * 0.11;
  vec4 aur = texture(uAur, gl_FragCoord.xy / uRes);
  vec3 col = aur.rgb;
  float a = aur.a;

  // (cos, sin) of each harmonic's phase at ribbon 0, and its per-ribbon step
  float A1 = p.x * 1.35 + t * 1.3, A2 = p.x * 3.10 - t * 1.7, A3 = p.x * 7.00 + t * 2.4, A4 = p.x * 1.1 + t;
  vec2 h1 = vec2(cos(A1), sin(A1)), h2 = vec2(cos(A2), sin(A2)), h3 = vec2(cos(A3), sin(A3)), h4 = vec2(cos(A4), sin(A4));
  vec2 r1 = vec2(cos(2.2 * STEP), sin(2.2 * STEP)), r2 = vec2(cos(6.0 * STEP), sin(6.0 * STEP));
  vec2 r3 = vec2(cos(11.0 * STEP), sin(11.0 * STEP)), r4 = vec2(cos(3.0 * STEP), sin(3.0 * STEP));
  float pull = exp(-pow((p.x - uMouse.x) * 2.0, 2.0)) * 0.25;
  float gain = mix(0.75, 1.0, uDark);

  for (int i = 0; i < N; i++) {
    float f = float(i) * STEP;
    float y = (f - 0.5) * 0.46 + 0.22 * h1.y + 0.08 * h2.y + 0.03 * h3.y - 0.05;
    y += pull * (uMouse.y - y);
    float d = p.y - y;
    if (abs(d) < 0.06) {
      float core = band(d, mix(0.0018, 0.0034, f) * (1.0 + 0.5 * uDark));
      float g = (core + band(d, 0.06) * 0.12) * gain;
      vec3 c = mix(mix(uC1, uC2, f), uC3, 0.5 + 0.5 * h4.y);
      if (uDark > 0.5) c = mix(c, vec3(1.0), core * 0.35);
      col += c * g;
      a += g;
    }
    h1 = vec2(h1.x * r1.x - h1.y * r1.y, h1.y * r1.x + h1.x * r1.y);
    h2 = vec2(h2.x * r2.x - h2.y * r2.y, h2.y * r2.x + h2.x * r2.y);
    h3 = vec2(h3.x * r3.x - h3.y * r3.y, h3.y * r3.x + h3.x * r3.y);
    h4 = vec2(h4.x * r4.x - h4.y * r4.y, h4.y * r4.x + h4.x * r4.y);
  }

  // film grain, so the gradients (and the upscaled aurora) never band
  float grain = (hash(gl_FragCoord.xy + fract(uTime) * 91.0) - 0.5) * 0.035;
  a = clamp(a + grain * a, 0.0, 0.95);
  col = clamp(col + grain * a, 0.0, 1.0);
  col = min(col, vec3(a));
  o = vec4(col, a);
}`

/** the aurora pass renders at this fraction of the canvas resolution */
const AURORA_SCALE = 0.35
/** ~30fps: the field drifts slowly, so every other display frame is plenty */
const FRAME_MS = 32

function compile(gl: WebGL2RenderingContext, type: number, src: string): WebGLShader | null {
  const s = gl.createShader(type)
  if (!s) return null
  gl.shaderSource(s, src)
  gl.compileShader(s)
  if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) {
    gl.deleteShader(s)
    return null
  }
  return s
}

export function SignalField({ className }: { className?: string }) {
  const ref = useRef<HTMLCanvasElement>(null)
  const still = useMotionOff()

  useEffect(() => {
    const canvas = ref.current
    if (!canvas) return
    const gl = canvas.getContext('webgl2', { premultipliedAlpha: true, antialias: false, alpha: true })
    if (!gl) {
      canvas.style.display = 'none'
      return
    }
    const link = (frag: string) => {
      const vs = compile(gl, gl.VERTEX_SHADER, VERT)
      const fs = compile(gl, gl.FRAGMENT_SHADER, frag)
      const prog = gl.createProgram()
      if (!vs || !fs || !prog) return null
      gl.attachShader(prog, vs)
      gl.attachShader(prog, fs)
      gl.linkProgram(prog)
      gl.deleteShader(vs)
      gl.deleteShader(fs)
      if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) return null
      const u = (n: string) => gl.getUniformLocation(prog, n)
      return {
        prog,
        uRes: u('uRes'), uTime: u('uTime'), uMouse: u('uMouse'), uDark: u('uDark'), uAur: u('uAur'),
        uC: [u('uC1'), u('uC2'), u('uC3'), u('uC4')],
      }
    }
    const aurora = link(AURORA)
    const main = link(FRAG)
    if (!aurora || !main) {
      canvas.style.display = 'none'
      return
    }
    const progs = [aurora, main]

    // one full-screen triangle, shared by both passes (attribute 0 in both programs)
    const vao = gl.createVertexArray()
    gl.bindVertexArray(vao)
    const buf = gl.createBuffer()
    gl.bindBuffer(gl.ARRAY_BUFFER, buf)
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW)
    gl.enableVertexAttribArray(0)
    gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0)

    // the low-resolution aurora target, sampled (bilinear) by the main pass
    const tex = gl.createTexture()
    gl.bindTexture(gl.TEXTURE_2D, tex)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE)
    const fbo = gl.createFramebuffer()

    const readColors = () => {
      const cs = [tokenRGB('var(--brand-1)'), tokenRGB('var(--brand-2)'), tokenRGB('var(--hue-ai)'), tokenRGB('var(--star-pink)')]
      const dark = isDarkTheme() ? 1 : 0
      for (const pr of progs) {
        gl.useProgram(pr.prog)
        pr.uC.forEach((loc, i) => gl.uniform3fv(loc, cs[i]))
        gl.uniform1f(pr.uDark, dark)
      }
      gl.useProgram(main.prog)
      gl.uniform1i(main.uAur, 0)
    }
    readColors()

    let w = 0, h = 0, aw = 1, ah = 1
    const resize = () => {
      const dpr = Math.min(window.devicePixelRatio || 1, 1)
      w = Math.max(1, Math.round(canvas.clientWidth * dpr))
      h = Math.max(1, Math.round(canvas.clientHeight * dpr))
      if (canvas.width !== w || canvas.height !== h) {
        canvas.width = w
        canvas.height = h
      }
      aw = Math.max(1, Math.round(w * AURORA_SCALE))
      ah = Math.max(1, Math.round(h * AURORA_SCALE))
      gl.bindTexture(gl.TEXTURE_2D, tex)
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA8, aw, ah, 0, gl.RGBA, gl.UNSIGNED_BYTE, null)
      gl.bindFramebuffer(gl.FRAMEBUFFER, fbo)
      gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, tex, 0)
      gl.bindFramebuffer(gl.FRAMEBUFFER, null)
      gl.useProgram(aurora.prog)
      gl.uniform2f(aurora.uRes, aw, ah)
      gl.useProgram(main.prog)
      gl.uniform2f(main.uRes, w, h)
    }

    // the pointer in shader space, eased toward its target each frame
    const mouse = { x: 0.4, y: 0.1, tx: 0.4, ty: 0.1 }
    const onMove = (e: PointerEvent) => {
      const r = canvas.getBoundingClientRect()
      mouse.tx = (e.clientX - r.left - r.width / 2) / r.height
      mouse.ty = -(e.clientY - r.top - r.height / 2) / r.height
    }

    const start = performance.now() - 8000
    let raf = 0
    const draw = (now: number) => {
      mouse.x += (mouse.tx - mouse.x) * 0.04
      mouse.y += (mouse.ty - mouse.y) * 0.04
      const time = (now - start) / 1000
      // pass 1: aurora into the small target
      gl.bindFramebuffer(gl.FRAMEBUFFER, fbo)
      gl.viewport(0, 0, aw, ah)
      gl.useProgram(aurora.prog)
      gl.uniform1f(aurora.uTime, time)
      gl.uniform2f(aurora.uMouse, mouse.x, mouse.y)
      gl.drawArrays(gl.TRIANGLES, 0, 3)
      // pass 2: ribbons + grain over the upscaled aurora, to the canvas
      gl.bindFramebuffer(gl.FRAMEBUFFER, null)
      gl.viewport(0, 0, w, h)
      gl.useProgram(main.prog)
      gl.uniform1f(main.uTime, time)
      gl.uniform2f(main.uMouse, mouse.x, mouse.y)
      gl.activeTexture(gl.TEXTURE0)
      gl.bindTexture(gl.TEXTURE_2D, tex)
      gl.drawArrays(gl.TRIANGLES, 0, 3)
    }
    let drawn = 0
    const loop = (now: number) => {
      if (now - drawn >= FRAME_MS) {
        drawn = now
        draw(now)
      }
      raf = requestAnimationFrame(loop)
    }
    const play = () => {
      cancelAnimationFrame(raf)
      if (still || document.hidden) draw(start + 8000)
      else raf = requestAnimationFrame(loop)
    }

    const ro = new ResizeObserver(() => {
      resize()
      if (still) draw(start + 8000)
    })
    ro.observe(canvas)
    resize()
    play()

    const theme = new MutationObserver(() => {
      readColors()
      if (still) draw(start + 8000)
    })
    theme.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] })
    const onVis = () => play()
    document.addEventListener('visibilitychange', onVis)
    if (!still) window.addEventListener('pointermove', onMove, { passive: true })
    const onLost = (e: Event) => {
      e.preventDefault()
      cancelAnimationFrame(raf)
      canvas.style.display = 'none'
    }
    canvas.addEventListener('webglcontextlost', onLost)

    return () => {
      cancelAnimationFrame(raf)
      ro.disconnect()
      theme.disconnect()
      document.removeEventListener('visibilitychange', onVis)
      window.removeEventListener('pointermove', onMove)
      canvas.removeEventListener('webglcontextlost', onLost)
      gl.deleteBuffer(buf)
      gl.deleteVertexArray(vao)
      gl.deleteFramebuffer(fbo)
      gl.deleteTexture(tex)
      gl.deleteProgram(aurora.prog)
      gl.deleteProgram(main.prog)
      gl.getExtension('WEBGL_lose_context')?.loseContext()
    }
  }, [still])

  return <canvas ref={ref} aria-hidden data-testid="signal-field" className={cn('pointer-events-none block h-full w-full', className)} />
}
