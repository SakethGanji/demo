import { useEffect, useRef, useState } from 'react'
import { TENSION, reducedMotion, usePrefs } from '../prefs'

export function useWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null)
  const [w, setW] = useState(300)
  useEffect(() => {
    const el = ref.current!
    const ro = new ResizeObserver(() => setW(Math.max(40, el.clientWidth)))
    ro.observe(el)
    return () => ro.disconnect()
  }, [])
  return [ref, w] as const
}

/** A taut string pinned at both ends. Change `pluck` to pluck it: a damped standing wave, then rest. */
export function PluckString({ pluck, at = 0.35, height = 14, stroke = 'url(#pluck-grad)', width: sw = 2, rest = 'var(--border-track)' }: { pluck: number; at?: number; height?: number; stroke?: string; width?: number; rest?: string }) {
  const [ref, w] = useWidth<HTMLDivElement>()
  const path = useRef<SVGPathElement>(null)
  const { prefs } = usePrefs()
  const first = useRef(true)
  useEffect(() => {
    const el = path.current!
    const mid = height / 2
    const flat = `M0,${mid} L${w},${mid}`
    el.setAttribute('d', flat)
    if (first.current) { first.current = false; return }
    if (prefs.motion === 'Still' || reducedMotion()) return
    const T = TENSION[prefs.tension]
    const A = (height / 2 - 1.5) * Math.min(1, T.amp)
    const t0 = performance.now()
    let raf = 0
    el.style.stroke = stroke
    const tick = (now: number) => {
      const t = (now - t0) / 1000
      const env = Math.exp(-t / T.decay)
      if (env < 0.02) { el.setAttribute('d', flat); el.style.stroke = ''; return }
      let d = ''
      for (let i = 0; i <= 48; i++) {
        const x = i / 48
        // fundamental plus the harmonic excited by where the string was plucked
        const y = A * env * (Math.sin(Math.PI * x) * Math.cos(2 * Math.PI * T.freq * t) * 0.75 + Math.sin(2 * Math.PI * x) * Math.cos(4 * Math.PI * T.freq * t) * (0.5 - at) * 0.9)
        d += `${i ? 'L' : 'M'}${(x * w).toFixed(1)},${(mid + y).toFixed(2)}`
      }
      el.setAttribute('d', d)
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => { cancelAnimationFrame(raf); el.style.stroke = '' }
  }, [pluck, w])
  return (
    <div ref={ref} style={{ height, width: '100%' }} aria-hidden>
      <svg width={w} height={height} style={{ display: 'block', overflow: 'visible' }}>
        <defs><linearGradient id="pluck-grad" x1="0" x2="1"><stop offset="0" stopColor="#0D9488" /><stop offset="1" stopColor="#38BDF8" /></linearGradient></defs>
        <path ref={path} fill="none" stroke={rest} strokeWidth={sw} strokeLinecap="round" style={{ transition: 'stroke var(--duration-snap-max) var(--ease-pluck)' }} />
        <circle cx={1.5} cy={height / 2} r={2.5} fill="var(--accent-primary)" />
        <circle cx={w - 1.5} cy={height / 2} r={2.5} fill="var(--accent-primary)" />
      </svg>
    </div>
  )
}

/** Five strings in a mirrored standing wave: the middle one still, each pair swinging outward equally.
 *  Moving the pointer across the field adds energy; its position picks the harmonic. */
export function StringField({ height = 120 }: { height?: number }) {
  const [ref, w] = useWidth<HTMLDivElement>()
  const paths = useRef<(SVGPathElement | null)[]>([])
  const state = useRef({ energy: 0.35, n: 2, lastX: 0 })
  const { prefs } = usePrefs()
  const [n, setN] = useState(2)
  useEffect(() => {
    const still = prefs.motion === 'Still' || reducedMotion()
    const T = TENSION[prefs.tension]
    const gap = 9, mid = height / 2
    let raf = 0
    const draw = (now: number) => {
      const s = state.current
      const t = now / 1000
      s.energy = Math.max(0.3, s.energy * 0.985)
      for (let k = 0; k < 5; k++) {
        const side = (k - 2) / 2
        let d = ''
        for (let i = 0; i <= 96; i++) {
          const x = i / 96
          const sw = still ? 0.6 : Math.cos(2 * Math.PI * (T.freq / 9) * t)
          const y = mid + side * gap * 2 + side * 20 * T.amp * s.energy * Math.sin(Math.PI * s.n * x) * sw
          d += `${i ? 'L' : 'M'}${(x * w).toFixed(1)},${y.toFixed(2)}`
        }
        paths.current[k]?.setAttribute('d', d)
      }
      if (!still) raf = requestAnimationFrame(draw)
    }
    raf = requestAnimationFrame(draw)
    return () => cancelAnimationFrame(raf)
  }, [w, prefs.motion, prefs.tension, height])
  const onMove = (e: React.PointerEvent) => {
    const r = ref.current!.getBoundingClientRect()
    const x = (e.clientX - r.left) / r.width
    const s = state.current
    s.energy = Math.min(1, s.energy + Math.abs(e.clientX - s.lastX) / 260)
    s.lastX = e.clientX
    const nn = 1 + Math.min(5, Math.floor(x * 6))
    if (nn !== s.n) { s.n = nn; setN(nn) }
  }
  const inks = ['#14B8A6', '#2DD4BF', '#99F6E4', '#2DD4BF', '#14B8A6']
  return (
    <div ref={ref} className="string-field" style={{ height }} onPointerMove={onMove}>
      <svg width={w} height={height} style={{ display: 'block' }} aria-hidden>
        {inks.map((c, k) => <path key={k} ref={(el) => { paths.current[k] = el }} fill="none" stroke={c} strokeWidth={k === 2 ? 1.5 : 1.25} strokeOpacity={k === 2 ? 0.9 : 0.7} />)}
        {Array.from({ length: n + 1 }, (_, i) => <circle key={i} cx={Math.min(w - 3, Math.max(3, (i / n) * w))} cy={height / 2} r={2.5} fill="#99F6E4" />)}
      </svg>
      <span className="string-field-read">mode <i>n</i> = {n} · {n - 1} interior node{n === 2 ? '' : 's'}</span>
    </div>
  )
}

export type RunState = { id: string; label: string; status: 'Training' | 'Completed' | 'Queued' | 'Failed'; progress: number; readout: string }

/** One run as one string: frets are folds, the node is progress, the live span is a travelling wave. */
export function RunString({ run, folds = 5, onClick }: { run: RunState; folds?: number; onClick?: () => void }) {
  const [ref, w] = useWidth<HTMLDivElement>()
  const live = useRef<SVGPathElement>(null)
  const { prefs } = usePrefs()
  const H = 26, mid = 13, x = Math.max(4, Math.min(w - 4, run.progress * w))
  const x0 = (Math.floor(run.progress * folds - 1e-6) / folds) * w
  useEffect(() => {
    if (run.status !== 'Training' || !live.current) return
    const el = live.current
    const still = prefs.motion === 'Still' || reducedMotion()
    let raf = 0
    const draw = (now: number) => {
      const t = now / 1000
      let d = ''
      const n = 40
      for (let i = 0; i <= n; i++) {
        const f = i / n
        const y = mid + Math.sin(Math.PI * f) * 5 * Math.sin(f * 14 - (still ? 0 : t * 6))
        d += `${i ? 'L' : 'M'}${(x0 + f * (x - x0)).toFixed(1)},${y.toFixed(2)}`
      }
      el.setAttribute('d', d)
      if (!still) raf = requestAnimationFrame(draw)
    }
    raf = requestAnimationFrame(draw)
    return () => cancelAnimationFrame(raf)
  }, [w, run.progress, run.status, prefs.motion])
  const ink = run.status === 'Failed' ? 'var(--status-danger)' : 'var(--accent-primary)'
  const glyph = { Training: '', Completed: '●', Queued: '○', Failed: '✕' }[run.status]
  return (
    <button className="run-string" onClick={onClick}>
      <span className="rs-id">{run.id}</span>
      <span className="rs-label">{run.label}</span>
      <div ref={ref} className="rs-track">
        <svg width={w} height={H} style={{ display: 'block', overflow: 'visible' }} aria-hidden>
          <line x1={run.status === 'Failed' ? x + 14 : x} x2={w} y1={mid} y2={mid} stroke="var(--border-track)" strokeWidth={1.5} strokeDasharray="4 4" />
          {run.status === 'Training'
            ? <><line x1={0} x2={x0} y1={mid} y2={mid} stroke={ink} strokeWidth={2} /><path ref={live} fill="none" stroke="var(--accent-secondary)" strokeWidth={2} strokeLinecap="round" /></>
            : run.progress > 0 && <line x1={0} x2={x} y1={mid} y2={mid} stroke={ink} strokeWidth={2} />}
          {Array.from({ length: folds + 1 }, (_, i) => <line key={i} x1={(i / folds) * w} x2={(i / folds) * w} y1={mid + 4} y2={mid + 9} stroke="var(--border-track)" />)}
          <circle cx={1} cy={mid} r={3} fill={run.progress > 0 ? ink : 'var(--bg-surface)'} stroke={run.progress > 0 ? ink : 'var(--border-track)'} strokeWidth={1.5} />
          {run.status === 'Training' && <circle cx={x} cy={mid} r={8} fill="var(--accent-secondary)" opacity={0.18} className="live-dot" />}
          {run.status === 'Failed'
            ? <path d={`M${x + 2},${mid - 5} l8,10 m0,-10 l-8,10`} stroke="var(--status-danger)" strokeWidth={2} strokeLinecap="round" />
            : <circle cx={x} cy={mid} r={4.5} fill={run.status === 'Queued' ? 'var(--bg-surface)' : run.status === 'Training' ? 'var(--accent-secondary)' : ink} stroke={run.status === 'Queued' ? 'var(--border-track)' : 'var(--bg-surface)'} strokeWidth={1.5} />}
        </svg>
      </div>
      <span className={`rs-status s-${run.status.toLowerCase()}`}>{run.status === 'Training' ? <i className="live-dot" /> : <span aria-hidden>{glyph}</span>}{run.status}</span>
      <span className="rs-read">{run.readout}</span>
    </button>
  )
}

/** Tool marks: each one is drawn from strings, nodes, frets or waves. 40px grid. */
export function ToolGlyph({ id }: { id: string }) {
  const a = 'var(--accent-primary)', b = 'var(--accent-secondary)', t = 'var(--border-track)'
  const p = { fill: 'none', strokeWidth: 1.75, strokeLinecap: 'round' as const, strokeLinejoin: 'round' as const }
  const g: Record<string, React.ReactNode> = {
    quick: <><path d="M4 12c5-8 9 8 14 0s9 8 18 0" stroke={b} {...p} /><path d="M4 21c6-4 10 4 16 0" stroke={b} {...p} strokeOpacity=".6" /><line x1="20" x2="36" y1="21" y2="21" stroke={a} {...p} /><line x1="4" x2="36" y1="30" y2="30" stroke={a} {...p} /><circle cx="4" cy="30" r="2.5" fill={a} /><circle cx="36" cy="30" r="2.5" fill={a} /></>,
    train: <><line x1="4" x2="20" y1="20" y2="20" stroke={a} {...p} /><path d="M20 20c3-9 5-9 8 0s5 9 8 0" stroke={b} {...p} /><circle cx="4" cy="20" r="2.5" fill={a} /><circle cx="12" cy="20" r="2.5" fill={a} /><circle cx="20" cy="20" r="3" fill={b} /></>,
    tune: <><line x1="4" x2="36" y1="18" y2="18" stroke={t} {...p} /><line x1="4" x2="24" y1="18" y2="18" stroke={a} {...p} />{[4, 12, 20, 28, 36].map((x) => <line key={x} x1={x} x2={x} y1="23" y2="27" stroke={t} strokeWidth="1.25" />)}<circle cx="24" cy="18" r="4" fill="var(--bg-surface)" stroke={a} strokeWidth="2" /></>,
    compare: <>{[8, 20, 32].map((x) => <line key={x} x1={x} x2={x} y1="6" y2="34" stroke={t} strokeWidth="1.25" />)}<path d="M8 12L20 26L32 10" stroke={a} {...p} /><path d="M8 28L20 14L32 24" stroke={b} {...p} /><path d="M8 20L20 20L32 30" stroke={t} {...p} strokeDasharray="3 3" /></>,
    errors: <>{[0, 1, 2].flatMap((r) => [0, 1, 2].map((c) => <rect key={`${r}${c}`} x={7 + c * 9} y={7 + r * 9} width="8" height="8" rx="1.5" fill={r === c ? 'var(--heat-5)' : (r + c) % 2 ? 'var(--heat-2)' : 'var(--heat-3)'} />))}</>,
    compose: <><path d="M9 14C16 30 24 30 31 26" stroke={a} {...p} /><rect x="4" y="9" width="10" height="10" rx="2" fill="var(--bg-surface)" stroke={a} strokeWidth="1.75" /><rect x="26" y="21" width="10" height="10" rx="2" fill="var(--bg-surface)" stroke={b} strokeWidth="1.75" /></>,
    models: <><path d="M6 34V6M6 34H34" stroke={t} strokeWidth="1.25" fill="none" /><path d="M6 30C18 30 20 10 34 10" stroke={a} {...p} /><circle cx="20" cy="20" r="2.5" fill={b} /></>,
    register: <><line x1="12" x2="12" y1="6" y2="34" stroke={a} {...p} /><circle cx="12" cy="8" r="3" fill={a} /><circle cx="12" cy="20" r="3" fill={a} /><circle cx="12" cy="32" r="3" fill="var(--bg-surface)" stroke={b} strokeWidth="1.75" /><path d="M12 20C22 22 22 30 30 30" stroke={b} {...p} strokeDasharray="3 3" /><circle cx="30" cy="30" r="2.5" fill={b} /></>,
    vela: <><path d="M4 22c4-12 8-12 12 0s8 12 12 0 6-8 8-4" stroke={b} {...p} /><path d="M4 22c4-6 8-6 12 0s8 6 12 0" stroke={a} {...p} strokeOpacity=".5" /></>,
  }
  return <svg width="40" height="40" viewBox="0 0 40 40" aria-hidden>{g[id]}</svg>
}
