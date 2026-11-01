// Motion: the kit's animation language, shown as live scenarios (typing, thinking, running, uploading a
// dataset, loading, charts, approving, failing, presence and small interactions). Every scenario is a short
// script of timed steps that loops while the card is on screen and can be replayed. Motion rules live in
// motion.css; the page only switches classes, so the same CSS works in the real cells.
import { createContext, useCallback, useContext, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react'
import './motion.css'
import { Scenarios } from './Scenarios'

/* ── timing ─────────────────────────────────────────────────────────── */

export const Reduced = createContext(false)

function useInView<T extends Element>() {
  const ref = useRef<T>(null)
  const [seen, setSeen] = useState(false)
  useEffect(() => {
    const el = ref.current
    if (!el || typeof IntersectionObserver === 'undefined') { setSeen(true); return }
    const io = new IntersectionObserver(([e]) => setSeen(e.isIntersecting), { threshold: 0.15 })
    io.observe(el)
    return () => io.disconnect()
  }, [])
  return [ref, seen] as const
}

/** Steps through `steps` (ms each) while `active`, waits `rest` ms, then loops. Returns the step and a replay. */
function useScript(steps: number[], active: boolean, rest = 2400) {
  const [step, setStep] = useState(0)
  const [run, setRun] = useState(0)
  useEffect(() => {
    if (!active) return
    const d = step < steps.length ? steps[step] : rest
    const t = setTimeout(() => setStep(s => (s < steps.length ? s + 1 : 0)), d)
    return () => clearTimeout(t)
  }, [step, active, run]) // eslint-disable-line react-hooks/exhaustive-deps
  const replay = useCallback(() => { setStep(0); setRun(r => r + 1) }, [])
  return [step, replay, run] as const
}

/** 0→1 over `dur` ms while `running`; 1 once `done`; 0 otherwise. */
export function useProgress(running: boolean, done: boolean, dur: number, ease: (t: number) => number = t => t) {
  const [p, setP] = useState(0)
  useEffect(() => {
    if (done) { setP(1); return }
    if (!running) { setP(0); return }
    let raf = 0
    const t0 = performance.now()
    const tick = (now: number) => {
      const t = Math.min(1, (now - t0) / dur)
      setP(ease(t))
      if (t < 1) raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [running, done, dur]) // eslint-disable-line react-hooks/exhaustive-deps
  return p
}

export const easeOut = (t: number) => 1 - Math.pow(1 - t, 3)
// bytes arrive in bursts, then slow near the end, like a real upload
export const bursty = (t: number) => Math.min(1, easeOut(t) + 0.035 * Math.sin(t * 19) * (1 - t) * t)
export const fmt = (n: number) => Math.round(n).toLocaleString('en-US')

/* ── small pieces ──────────────────────────────────────────────────── */

export function Check({ on, size = 18, className = '' }: { on: boolean; size?: number; className?: string }) {
  return (
    <svg className={`mo-check ${on ? 'is-on' : ''} ${className}`} viewBox="0 0 24 24" width={size} height={size} aria-hidden>
      <circle cx="12" cy="12" r="10" pathLength={1} />
      <path d="M7.5 12.4l3 3 6-6.4" pathLength={1} />
    </svg>
  )
}

export function Spinner({ size = 14 }: { size?: number }) {
  return <svg className="mo-spin" viewBox="0 0 24 24" width={size} height={size} aria-hidden><circle cx="12" cy="12" r="9" /></svg>
}

export function Dots({ label = 'Working' }: { label?: string }) {
  return <span className="mo-dots" role="status" aria-label={label}><i /><i /><i /></span>
}

function Card({ n, title, why, tags, wide, children, onReplay }: {
  n: number; title: string; why: string; tags: string[]; wide?: boolean; children: ReactNode; onReplay?: () => void
}) {
  return (
    <section className={`mo-card ${wide ? 'mo-card--wide' : ''}`}>
      <header className="mo-card__head">
        <span className="mo-card__n">{String(n).padStart(2, '0')}</span>
        <div className="mo-card__t">
          <h3>{title}</h3>
          <p>{why}</p>
        </div>
        {onReplay && <button className="mo-replay" onClick={onReplay} aria-label={`Replay ${title}`}>
          <svg viewBox="0 0 20 20" width={13} height={13} aria-hidden><path d="M4 10a6 6 0 1 0 1.8-4.3M4 4v3.4h3.4" /></svg>Replay
        </button>}
      </header>
      <div className="mo-stage">{children}</div>
      <footer className="mo-tags">{tags.map(t => <span key={t}>{t}</span>)}</footer>
    </section>
  )
}

/** Wraps a scenario: runs its script only while visible. */
export function Scene({ steps, rest, render, ...card }: Omit<Parameters<typeof Card>[0], 'children' | 'onReplay'> & {
  steps: number[]; rest?: number; render: (step: number, run: number) => ReactNode
}) {
  const [ref, seen] = useInView<HTMLDivElement>()
  const [step, replay, run] = useScript(steps, seen, rest)
  return <div ref={ref} className={card.wide ? 'mo-span' : ''}><Card {...card} onReplay={replay}>{render(step, run)}</Card></div>
}

/* ── 1. typing in the Ask composer ─────────────────────────────────── */

const PROMPT = 'Compare NRR by region for the last 4 quarters'
const GHOST = ' and flag accounts below 100%'

function TypingScene() {
  // 0 idle · 1 typing · 2 ghost suggestion · 3 Tab accepts · 4 send press · 5 sent, reply dots
  const reduced = useContext(Reduced)
  return <Scene n={1} title="Typing and sending" why="The caret stays solid while you type and blinks only when idle. A ghost completion fades in; Tab accepts it. Sending lifts the message into the thread, and the reply starts with dots."
    tags={['caret 1.06s steps', 'ghost 180ms ease-out', 'press scale .96 · 120ms', 'lift 8px · 260ms spring']}
    steps={[700, PROMPT.length * 42, 1100, 500, 260, 2200]} rest={1400}
    render={step => <TypingStage step={step} reduced={reduced} />} />
}

function TypingStage({ step, reduced }: { step: number; reduced: boolean }) {
  const p = useProgress(step === 1, step > 1, PROMPT.length * 42)
  const typed = step === 0 ? '' : PROMPT.slice(0, reduced && step === 1 ? PROMPT.length : Math.round(p * PROMPT.length))
  const sent = step >= 5
  const accepted = step >= 3
  return (
    <div className="mo-chat">
      <div className="mo-thread">
        <div className="mo-msg mo-msg--them">Revenue table is loaded. What should I look at?</div>
        {sent && <div className="mo-msg mo-msg--me mo-lift">{PROMPT + GHOST}</div>}
        {sent && <div className="mo-msg mo-msg--them mo-lift" style={{ animationDelay: '240ms' }}><Dots label="Studio is replying" /></div>}
      </div>
      <div className={`mo-composer ${step >= 1 && step <= 4 ? 'is-focus' : ''}`}>
        <div className="mo-field" aria-label="Ask">
          {sent || step === 0 ? <span className="mo-ph">Ask about this data…</span> : <>
            <span>{typed}</span>
            {step >= 2 && <span className={`mo-ghost ${accepted ? 'is-accepted' : ''}`}>{GHOST}</span>}
            <span className={`mo-caret ${step === 1 ? 'is-typing' : ''}`} />
          </>}
          {step === 2 && <kbd className="mo-kbd mo-pop">Tab</kbd>}
          {step === 3 && <kbd className="mo-kbd is-pressed">Tab</kbd>}
        </div>
        <button className={`mo-send ${typed && !sent ? 'is-ready' : ''} ${step === 4 ? 'is-pressed' : ''}`} aria-label="Send">
          <svg viewBox="0 0 20 20" width={15} height={15} aria-hidden><path d="M10 15V5M5.5 9.5 10 5l4.5 4.5" /></svg>
        </button>
      </div>
    </div>
  )
}

/* ── 2. thinking → plan → streamed answer ──────────────────────────── */

const PLAN = ['Load subscriptions', 'Compute NRR by region', 'Chart four quarters', 'Find accounts below 100%', 'Draft the summary']
const ANSWER = 'EMEA grew fastest, from 104% to 112%. LATAM is the only region below 100% in every quarter, so its three largest accounts are flagged for review.'

function StreamScene() {
  // 0 thinking · 1 plan appears · 2 steps tick off · 3 answer streams · 4 hold
  return <Scene n={2} title="Thinking, planning, streaming" why="A shimmer on the label says work is happening, before anything is known. The plan staggers in, each step ticks off with a drawn check, then the answer streams word by word from a soft blur."
    tags={['shimmer 1.6s linear', 'stagger 60ms', 'check draw 320ms', 'word fade 280ms · blur 4px→0']}
    steps={[1500, 900, 2100, 2800]} rest={2400}
    render={step => <StreamStage step={step} />} />
}

function StreamStage({ step }: { step: number }) {
  const tick = useProgress(step === 2, step > 2, 2100)
  const stream = useProgress(step === 3, step > 3, 2800)
  const words = ANSWER.split(' ')
  const shown = Math.round(stream * words.length)
  const done = Math.floor(tick * PLAN.length + 0.0001)
  return (
    <div className="mo-think">
      <div className="mo-think__head">
        <span className="mo-orb" data-on={step < 3} />
        <span className={step < 3 ? 'mo-shimmer' : 'mo-think__done'}>{step === 0 ? 'Thinking' : step < 3 ? `Running step ${Math.min(done + 1, PLAN.length)} of ${PLAN.length}` : 'Done in 4.1s'}</span>
      </div>
      {step >= 1 && <ol className="mo-plan">
        {PLAN.map((s, i) => {
          const state = step > 2 || i < done ? 'done' : step === 2 && i === done ? 'run' : 'wait'
          return <li key={s} className={`mo-in is-${state}`} style={{ animationDelay: `${i * 60}ms` }}>
            <span className="mo-plan__mark">{state === 'done' ? <Check on size={16} /> : state === 'run' ? <Spinner /> : <i />}</span>{s}
          </li>
        })}
      </ol>}
      {step >= 3 && <p className="mo-answer">
        {words.slice(0, shown).map((w, i) => <span key={i} className="mo-word">{w} </span>)}
        {step === 3 && <span className="mo-caret is-typing" />}
      </p>}
    </div>
  )
}

/* ── 3. a cell runs ────────────────────────────────────────────────── */

const OUT_LINES = ['loading subscriptions…  12,408 rows', 'joining plans           9 columns', 'grouping by region      4 groups', 'NRR computed            EMEA 112%  APAC 106%']

function RunScene() {
  // 0 idle · 1 running · 2 done flash · 3 hold
  return <Scene n={3} title="A cell runs" why="Play morphs into Stop. The gutter ribbon carries a travelling light while it runs, the count shows [*], and the timer ticks. Output lines arrive one by one, then the ribbon settles green and the count rolls to its number."
    tags={['icon morph 160ms', 'ribbon sweep 1.2s', 'count roll 240ms spring', 'line in 200ms']}
    steps={[900, 2800, 700]} rest={2200}
    render={step => <RunStage step={step} />} />
}

function RunStage({ step }: { step: number }) {
  const p = useProgress(step === 1, step > 1, 2800)
  const running = step === 1
  const lines = step === 0 ? 0 : Math.min(OUT_LINES.length, Math.floor(p * (OUT_LINES.length + 0.6)))
  return (
    <div className={`mo-cell is-${running ? 'run' : step >= 2 ? 'ok' : 'idle'}`}>
      <div className="mo-cell__gutter">
        <button className="mo-play" aria-label={running ? 'Stop' : 'Run'}>
          <svg className={`mo-swap ${running ? '' : 'is-on'}`} viewBox="0 0 20 20" width={14} height={14} aria-hidden><path d="M7 5.5v9l7-4.5z" fill="currentColor" stroke="none" /></svg>
          <svg className={`mo-swap ${running ? 'is-on' : ''}`} viewBox="0 0 20 20" width={14} height={14} aria-hidden><rect x="6" y="6" width="8" height="8" rx="1.6" fill="currentColor" stroke="none" /></svg>
        </button>
        <span className="mo-count">[<span className="mo-count__win"><span key={running ? 'run' : step >= 2 ? 'n' : 'idle'} className="mo-roll">{running ? '*' : step >= 2 ? '7' : ' '}</span></span>]</span>
        <span className="mo-ribbon" />
      </div>
      <div className="mo-cell__body">
        <div className="mo-cell__head"><b>NRR by region</b><span className="mo-cell__time">{step === 0 ? 'not run' : running ? `${(p * 2.8).toFixed(1)}s` : '2.8s'}</span></div>
        <pre className="mo-code"><span className="k">select</span> region, <span className="f">sum</span>(mrr_end) / <span className="f">sum</span>(mrr_start) <span className="k">as</span> nrr{'\n'}<span className="k">from</span> subscriptions <span className="k">group by</span> region</pre>
        <div className={`mo-out ${lines ? 'is-open' : ''}`}>
          <div>{OUT_LINES.slice(0, lines).map(l => <div key={l} className="mo-line">{l}</div>)}</div>
        </div>
        {step >= 2 && <span className="mo-result mo-pop"><Check on size={14} />4 rows · 2.8s</span>}
      </div>
    </div>
  )
}

/* ── 4. uploading a dataset ────────────────────────────────────────── */

const COLS: [string, string][] = [['account_id', 'id'], ['region', 'text'], ['plan', 'text'], ['mrr_start', 'money'], ['mrr_end', 'money'], ['signup_at', 'date'], ['churned', 'bool'], ['seats', 'int'], ['owner', 'text']]
const ROWS = [['ACC-1187', 'LATAM', 'Growth', '$4,200', '$3,486'], ['ACC-1420', 'NA', 'Scale', '$12,900', '$11,868'], ['ACC-2231', 'EMEA', 'Growth', '$3,100', '$3,472'], ['ACC-2310', 'APAC', 'Starter', '$880', '$933']]
const SIZE_MB = 12.8, TOTAL_ROWS = 12408

function UploadScene() {
  // 0 idle · 1 file dragged over · 2 drop · 3 uploading · 4 parsing + profiling · 5 ready
  return <Scene n={4} wide title="Uploading a dataset" why="The drop zone breathes until a file comes near, then tints and lifts to say “yes, here”. The file lands with a little settle. Real bytes drive the bar, with speed and time left. Parsing counts rows live, columns pop in with their detected types, and preview rows replace skeletons. A drawn check closes it."
    tags={['dash march 1s linear', 'hover lift 1.01 · 200ms', 'land spring 420ms', 'bar = bytes, not time', 'row counter tabular-nums', 'chip stagger 70ms', 'check 360ms']}
    steps={[1300, 1300, 520, 2800, 2600]} rest={3200}
    render={step => <UploadStage step={step} />} />
}

function UploadStage({ step }: { step: number }) {
  const up = useProgress(step === 3, step > 3, 2800, bursty)
  const parse = useProgress(step === 4, step > 4, 2600, easeOut)
  const mb = up * SIZE_MB
  const left = Math.max(0, Math.ceil((1 - up) * 4.1))
  const cols = Math.floor(parse * (COLS.length + 0.5))
  const rows = Math.floor(parse * (ROWS.length + 1.2))
  const stage = step < 3 ? 0 : step === 3 ? 1 : step === 4 ? 2 : 3
  return (
    <div className="mo-up">
      <div className="mo-up__left">
        {step <= 2 ? (
          <div className={`mo-drop ${step === 1 ? 'is-over' : ''} ${step === 2 ? 'is-landing' : ''}`}>
            <svg className="mo-drop__edge" aria-hidden><rect x="1" y="1" rx="14" /></svg>
            <span className="mo-drop__icon"><svg viewBox="0 0 24 24" width={22} height={22} aria-hidden><path d="M12 15V5m0 0-4 4m4-4 4 4M5 15v2.5A1.5 1.5 0 0 0 6.5 19h11a1.5 1.5 0 0 0 1.5-1.5V15" /></svg></span>
            <span className="mo-drop__swap">
              <b className={step === 0 ? 'is-on' : ''}>Drop a CSV, Parquet or Excel file</b>
              <b className={step >= 1 ? 'is-on' : ''}>Release to upload</b>
            </span>
            <span className="mo-drop__sub">or <u>browse</u> · up to 2 GB</span>
            <div className={`mo-file mo-file--fly ${step >= 1 ? 'is-in' : ''} ${step === 2 ? 'is-drop' : ''}`}>
              <span className="mo-file__ic">CSV</span><span>subscriptions_q3.csv</span>
            </div>
          </div>
        ) : (
          <div className="mo-job mo-land">
            <div className="mo-job__row">
              <span className="mo-file__ic">CSV</span>
              <div className="mo-job__name"><b>subscriptions_q3.csv</b>
                <span className="mo-job__meta">{stage === 1 ? `${mb.toFixed(1)} of ${SIZE_MB} MB · ${(3.1 + Math.sin(up * 9) * 0.6).toFixed(1)} MB/s · ${left}s left` : `${SIZE_MB} MB · uploaded in 4.1s`}</span>
              </div>
              {stage === 3 ? <Check on size={22} className="mo-check--big" /> : <span className="mo-job__pct">{stage === 1 ? `${Math.floor(up * 100)}%` : <Spinner size={16} />}</span>}
            </div>
            <div className={`mo-bar ${stage >= 2 ? 'is-full' : ''}`}><span style={{ transform: `scaleX(${stage === 1 ? up : 1})` }} /></div>
            <ol className="mo-steps">
              {['Upload', 'Parse', 'Profile'].map((s, i) => {
                const st = stage > i + 1 || stage === 3 ? 'done' : stage === i + 1 || (i === 2 && stage === 2 && parse > .55) ? 'run' : 'wait'
                return <li key={s} className={`is-${st}`}>{st === 'done' ? <Check on size={14} /> : st === 'run' ? <Spinner size={12} /> : <i />}{s}</li>
              })}
            </ol>
            {stage >= 2 && <div className="mo-counter mo-in">
              <b>{fmt(parse * TOTAL_ROWS)}</b><span>rows</span><b>{Math.min(cols, COLS.length)}</b><span>columns</span>
              {stage === 3 && <span className="mo-pill mo-pop">2 columns have nulls</span>}
            </div>}
          </div>
        )}
      </div>
      <div className="mo-up__right">
        <div className="mo-cols">
          {COLS.map(([c, t], i) => i < cols || stage === 3
            ? <span key={c} className="mo-col mo-pop" style={{ animationDelay: stage === 3 ? '0ms' : undefined }}>{c}<em data-t={t}>{t}</em></span>
            : <span key={c} className="mo-col mo-col--ghost mo-sk" />)}
        </div>
        <div className="mo-preview">
          <div className="mo-preview__head">{['account_id', 'region', 'plan', 'mrr_start', 'mrr_end'].map(h => <span key={h}>{h}</span>)}</div>
          {ROWS.map((r, i) => i < rows || stage === 3
            ? <div key={r[0]} className="mo-preview__row mo-in" style={{ animationDelay: stage === 3 ? '0ms' : undefined }}>{r.map((v, j) => <span key={j}>{v}</span>)}</div>
            : <div key={r[0]} className="mo-preview__row is-sk">{r.map((_, j) => <span key={j}><i className="mo-sk" style={{ width: `${48 + ((i * 7 + j * 13) % 40)}%` }} /></span>)}</div>)}
        </div>
        <button className={stage === 3 ? 'mo-cta mo-pop' : 'mo-cta is-hidden'} tabIndex={stage === 3 ? 0 : -1}>Open dataset</button>
      </div>
    </div>
  )
}

/* ── 5. skeleton → content ─────────────────────────────────────────── */

const KPIS: [string, number, string, string][] = [['Net revenue retention', 104.2, '%', '+2.1 pts'], ['Active accounts', 5403, '', '+312'], ['At risk', 3, '', 'LATAM']]

function SkeletonScene() {
  return <Scene n={5} title="Skeleton to content" why="When the layout is known, skeletons hold its exact shape and shimmer once a second. Content crossfades in place, rows stagger, and numbers count up so the change reads as arrival, not a jump."
    tags={['shimmer 1.4s', 'crossfade 220ms', 'row stagger 35ms', 'count-up 700ms ease-out']}
    steps={[1900, 2600]} rest={1200}
    render={step => <SkeletonStage step={step} />} />
}

function SkeletonStage({ step }: { step: number }) {
  const k = useProgress(step === 1, step > 1, 700, easeOut)
  const on = step >= 1
  return (
    <div className="mo-skel">
      <div className="mo-kpis">
        {KPIS.map(([l, v, u, d]) => <div key={l} className="mo-kpi">
          {on ? <>
            <span className="mo-kpi__l mo-fade">{l}</span>
            <b className="mo-kpi__v">{v % 1 ? (v * k).toFixed(1) : fmt(v * k)}{u}</b>
            <span className="mo-kpi__d mo-pop" style={{ animationDelay: '380ms' }}>{d}</span>
          </> : <><i className="mo-sk" style={{ width: '70%' }} /><i className="mo-sk mo-sk--lg" style={{ width: '50%' }} /><i className="mo-sk" style={{ width: '30%' }} /></>}
        </div>)}
      </div>
      <div className="mo-table">
        {ROWS.map((r, i) => <div key={r[0]} className="mo-table__row">
          {on ? r.map((v, j) => <span key={j} className="mo-in" style={{ animationDelay: `${i * 35 + 120}ms` }}>{v}</span>)
            : r.map((_, j) => <span key={j}><i className="mo-sk" style={{ width: `${40 + ((i * 11 + j * 17) % 45)}%` }} /></span>)}
        </div>)}
      </div>
    </div>
  )
}

/* ── 6. chart draws ────────────────────────────────────────────────── */

const BARS = [72, 88, 64, 104, 96, 112]

function ChartScene() {
  return <Scene n={6} title="A chart arrives" why="Bars grow from the baseline with a small spring, one after another. The trend line draws itself after the bars, and the last point pops a label so the eye lands on the answer."
    tags={['bar grow 520ms spring', 'stagger 50ms', 'line draw 700ms', 'label pop 200ms']}
    steps={[300, 2200]} rest={2400}
    render={(step, run) => <ChartStage key={run} on={step >= 1} />} />
}

function ChartStage({ on }: { on: boolean }) {
  const pts = BARS.map((v, i) => `${30 + i * 48},${150 - v * 1.1}`).join(' ')
  return (
    <svg className={`mo-chart ${on ? 'is-on' : ''}`} viewBox="0 0 300 170" role="img" aria-label="NRR by quarter">
      {[40, 80, 120].map(y => <line key={y} x1="10" x2="290" y1={y} y2={y} className="mo-chart__grid" />)}
      {BARS.map((v, i) => <rect key={i} x={16 + i * 48} y={150 - v * 1.1} width="28" height={v * 1.1} rx="5" className="mo-chart__bar" style={{ transitionDelay: `${i * 50}ms` }} />)}
      <polyline points={pts} pathLength={1} className="mo-chart__line" />
      <g className="mo-chart__tip"><rect x="232" y={150 - 112 * 1.1 - 34} width="58" height="22" rx="6" /><text x="261" y={150 - 112 * 1.1 - 19}>112%</text></g>
      <line x1="10" x2="290" y1="150" y2="150" className="mo-chart__base" />
    </svg>
  )
}

/* ── 7. approve + toasts ───────────────────────────────────────────── */

const TOASTS = ['ACC-1187 flagged at_risk', 'ACC-1420 flagged at_risk', 'ACC-2231 flagged at_risk']

function ApproveScene() {
  // 0 idle · 1 press · 2 working · 3 success · 4..6 toasts stack
  return <Scene n={7} title="Approve, then confirm" why="The button gives under the finger, keeps its width while it works, then turns green with a drawn check. Confirmations stack like cards: each new toast rises in front and pushes the older ones back and smaller."
    tags={['press .97 · 100ms', 'label crossfade 150ms', 'success 240ms', 'toast rise 16px · 300ms spring', 'stack scale .94']}
    steps={[1000, 140, 1100, 500, 520, 520, 2200]} rest={900}
    render={step => <ApproveStage step={step} />} />
}

function ApproveStage({ step }: { step: number }) {
  const state = step === 0 ? 'idle' : step === 1 ? 'press' : step === 2 ? 'busy' : 'ok'
  const toasts = Math.max(0, Math.min(TOASTS.length, step - 3))
  return (
    <div className="mo-approve">
      <div className="mo-approve__card">
        <b>Flag 3 accounts in the CRM</b>
        <span>Checked again right before writing.</span>
        <button className={`mo-btn is-${state}`}>
          <span className={`mo-btn__l ${state === 'idle' || state === 'press' ? 'is-on' : ''}`}>Approve these 3</span>
          <span className={`mo-btn__l ${state === 'busy' ? 'is-on' : ''}`}><Spinner size={14} />Writing…</span>
          <span className={`mo-btn__l ${state === 'ok' ? 'is-on' : ''}`}><Check on={state === 'ok'} size={15} />Approved</span>
        </button>
      </div>
      <ol className="mo-toasts" aria-live="polite">
        {TOASTS.slice(0, toasts).map((t, i) => {
          const depth = toasts - 1 - i
          return <li key={t} className="mo-toast" style={{ ['--d' as string]: depth, zIndex: 10 - depth }}><Check on size={15} />{t}<span>CRM</span></li>
        })}
      </ol>
    </div>
  )
}

/* ── 8. failure + retry ────────────────────────────────────────────── */

function ErrorScene() {
  // 0 running · 1 fails (shake) · 2 error shown · 3 retry pressed · 4 rerun · 5 ok
  return <Scene n={8} title="Fails, then recovers" why="A failure gets one short shake and a red ribbon, never a modal. The error opens below with what to do next. Retry runs in place and the same card turns green, so the fix is visible where the problem was."
    tags={['shake 4px · 360ms, once', 'panel open 240ms', 'ribbon colour 200ms']}
    steps={[1200, 450, 1600, 160, 1300]} rest={2200}
    render={step => <ErrorStage step={step} />} />
}

function ErrorStage({ step }: { step: number }) {
  const state = step === 0 || step === 4 ? 'run' : step === 5 ? 'ok' : 'bad'
  return (
    <div className={`mo-err is-${state} ${step === 1 ? 'is-shake' : ''}`}>
      <div className="mo-err__head">
        <span className="mo-err__mark">{state === 'run' ? <Spinner /> : state === 'ok' ? <Check on size={16} /> : <svg viewBox="0 0 20 20" width={16} height={16} aria-hidden><circle cx="10" cy="10" r="8" /><path d="M10 6v4.5M10 13.6v.1" /></svg>}</span>
        <b>Open signup page</b>
        <span className="mo-err__st">{state === 'run' ? (step === 4 ? 'Retrying…' : 'Running…') : state === 'ok' ? 'Passed · 1.9s' : 'Failed'}</span>
      </div>
      <div className={`mo-out ${state === 'bad' && step >= 2 ? 'is-open' : ''}`}>
        <div><div className="mo-err__body">
          <code>TimeoutError: button “Sign up” not visible after 5s</code>
          <span>The page loaded slowly. Retrying usually works; if not, raise the wait.</span>
          <div className="mo-err__acts"><button className={`mo-btn mo-btn--sm ${step === 3 ? 'is-press' : ''}`}><span className="mo-btn__l is-on">Retry</span></button><button className="mo-ghostbtn">Raise wait to 10s</button></div>
        </div></div>
      </div>
    </div>
  )
}

/* ── 9. presence ───────────────────────────────────────────────────── */

const WAY = [[18, 70], [62, 36], [74, 66], [34, 30]]

function PresenceScene() {
  return <Scene n={9} title="Someone else is here" why="A new person joins with a small pop. Their cursor glides between points instead of jumping, its label trails slightly, and what they select tints in their colour. Typing shows as dots under their name."
    tags={['join pop .6→1 spring', 'cursor glide 700ms', 'selection tint 180ms', 'dots 1.2s']}
    steps={[700, 900, 1000, 1100, 1300]} rest={1400}
    render={step => <PresenceStage step={step} />} />
}

function PresenceStage({ step }: { step: number }) {
  const [x, y] = WAY[Math.min(WAY.length - 1, Math.max(0, step - 1))]
  return (
    <div className="mo-pres">
      <div className="mo-pres__bar">
        <span className="mo-av" style={{ ['--c' as string]: '#5b55f0' }}>AL</span>
        <span className="mo-av" style={{ ['--c' as string]: '#0ea5e9' }}>MC</span>
        {step >= 1 && <span className="mo-av mo-join" style={{ ['--c' as string]: '#e0457b' }}>GH</span>}
        <span className="mo-pres__live">{step >= 1 ? '3 here' : '2 here'}</span>
      </div>
      <div className="mo-pres__doc">
        <p>Revenue grew <span className={`mo-sel ${step >= 3 ? 'is-on' : ''}`}>12.4% quarter on quarter</span>, led by NA. Churn is flat.</p>
        <p className="mo-pres__line" /><p className="mo-pres__line mo-pres__line--s" />
        {step >= 1 && <span className="mo-cursor" style={{ left: `${x}%`, top: `${y}%` }}>
          <svg viewBox="0 0 16 16" width={16} height={16} aria-hidden><path d="M2 1.5l11 5.6-4.6 1.3-2 4.4z" /></svg>
          <span className="mo-cursor__tag">Grace{step >= 4 && <Dots label="Grace is typing" />}</span>
        </span>}
      </div>
    </div>
  )
}

/* ── 10. small interactions (try them) ─────────────────────────────── */

function MicroCard() {
  const [copied, setCopied] = useState(false)
  const [on, setOn] = useState(true)
  const [tab, setTab] = useState(1)
  const [pinned, setPinned] = useState(false)
  const [n, setN] = useState(42)
  const [dir, setDir] = useState<'up' | 'down'>('up')
  const tabs = ['Table', 'Chart', 'SQL']
  const tabRefs = useRef<(HTMLButtonElement | null)[]>([])
  const [ind, setInd] = useState({ x: 0, w: 0 })
  useLayoutEffect(() => {
    const b = tabRefs.current[tab]
    if (b) setInd({ x: b.offsetLeft, w: b.offsetWidth })
  }, [tab])
  useEffect(() => { if (!copied) return; const t = setTimeout(() => setCopied(false), 1400); return () => clearTimeout(t) }, [copied])
  const step = (d: 1 | -1) => { setDir(d > 0 ? 'up' : 'down'); setN(v => v + d) }
  return (
    <Card n={10} title="Small things, try them" why="Frequent actions stay quick and quiet. Icons swap with a blur crossfade, the switch thumb springs, the tab indicator slides to its target, a pin pops once, and digits roll in the direction of change."
      tags={['icon swap 150ms + blur', 'switch 220ms spring', 'indicator 240ms ease-out', 'pin pop 300ms', 'digit roll 200ms']}>
      <div className="mo-micro">
        <button className="mo-copy" onClick={() => setCopied(true)} aria-label="Copy SQL">
          <span className={`mo-iconswap ${copied ? '' : 'is-on'}`}><svg viewBox="0 0 20 20" width={15} height={15} aria-hidden><rect x="7" y="7" width="9" height="9" rx="2" /><path d="M13 7V5.5A1.5 1.5 0 0 0 11.5 4h-6A1.5 1.5 0 0 0 4 5.5v6A1.5 1.5 0 0 0 5.5 13H7" /></svg></span>
          <span className={`mo-iconswap ${copied ? 'is-on' : ''}`}><svg viewBox="0 0 20 20" width={15} height={15} aria-hidden><path d="M5 10.5l3 3 7-7" /></svg></span>
          <span className="mo-copy__l">{copied ? 'Copied' : 'Copy SQL'}</span>
        </button>
        <button className={`mo-switch ${on ? 'is-on' : ''}`} role="switch" aria-checked={on} onClick={() => setOn(v => !v)}><i /></button>
        <div className="mo-seg" role="tablist">
          <span className="mo-seg__ind" style={{ transform: `translateX(${ind.x}px)`, width: ind.w }} />
          {tabs.map((t, i) => <button key={t} ref={el => { tabRefs.current[i] = el }} role="tab" aria-selected={tab === i} className={tab === i ? 'is-on' : ''} onClick={() => setTab(i)}>{t}</button>)}
        </div>
        <button className={`mo-pin ${pinned ? 'is-on' : ''}`} onClick={() => setPinned(v => !v)} aria-label="Pin" aria-pressed={pinned}>
          <svg viewBox="0 0 20 20" width={16} height={16} aria-hidden><path d="M10 2.8l2.2 4.5 5 .7-3.6 3.5.9 5-4.5-2.4-4.5 2.4.9-5L2.8 8l5-.7z" /></svg>
          <span className="mo-burst" key={String(pinned)} />
        </button>
        <div className="mo-stepper">
          <button onClick={() => step(-1)} aria-label="Less">−</button>
          <span className="mo-stepper__win"><span key={n} className={`mo-digit mo-digit--${dir}`}>{n}</span></span>
          <button onClick={() => step(1)} aria-label="More">+</button>
        </div>
      </div>
    </Card>
  )
}

/* ── page ──────────────────────────────────────────────────────────── */

const RULES: [string, string][] = [
  ['Under 300ms', 'Presses 100–160ms, popovers 180ms, panels 240ms, page changes 300ms. Longer only for things that show progress.'],
  ['Ease out, spring for arrivals', 'Things entering decelerate. Things that land (a file, a toast, a count) use a soft spring with under 2% overshoot.'],
  ['Transform and opacity only', 'Nothing animates width, height or top. Heights open with a grid-rows trick, so layout never stutters.'],
  ['Never from nothing', 'Entrances start at 95% scale and 4–8px away, never at scale 0. Popovers grow from their trigger.'],
  ['Progress is real', 'Bars follow bytes and rows, not a timer. Counters use tabular numbers so digits don’t jitter.'],
  ['Quiet when frequent', 'Keyboard and everyday actions get the least motion. Rare moments (a dataset ready, an approval) get the most.'],
  ['Pause off screen', 'Loops run only while visible. Shimmer, dots and pulses stop when the work stops.'],
  ['Reduced motion', 'With reduced motion on, every movement becomes a plain fade; loops and shakes switch off. Try the toggle.'],
]

export function Motion() {
  const sys = typeof matchMedia !== 'undefined' && matchMedia('(prefers-reduced-motion: reduce)').matches
  const [reduced, setReduced] = useState(sys)
  const [seed, setSeed] = useState(0)
  return (
    <Reduced.Provider value={reduced}>
      <div className="mo" data-reduced={reduced}>
        <header className="mo-hero">
          <div className="mo-eyebrow">Motion</div>
          <h1>Animations for every moment in a notebook</h1>
          <p>Typing, thinking, running, uploading a dataset, loading, approving, failing and working together. Each card is a live loop with its exact timings underneath; they all share one set of curves and durations.</p>
          <div className="mo-hero__acts">
            <button className={`mo-switch ${reduced ? 'is-on' : ''}`} role="switch" aria-checked={reduced} onClick={() => setReduced(v => !v)}><i /></button>
            <span>Reduced motion</span>
            <button className="mo-ghostbtn" onClick={() => setSeed(s => s + 1)}>Replay all</button>
          </div>
        </header>

        <section className="mo-rules">
          {RULES.map(([t, d]) => <div key={t} className="mo-rule"><b>{t}</b><span>{d}</span></div>)}
          <div className="mo-curves">
            {[['ease-out', 'var(--mo-ease-out)', 'cubic-bezier(.22,1,.36,1)'], ['ease-in-out', 'var(--mo-ease-in-out)', 'cubic-bezier(.65,0,.35,1)'], ['spring', 'var(--mo-spring)', 'linear(…) · 1.7% overshoot']].map(([n, v, c]) =>
              <div key={n} className="mo-curve"><span className="mo-curve__track"><i style={{ animationTimingFunction: v }} /></span><b>{n}</b><code>{c}</code></div>)}
          </div>
        </section>

        <div className="mo-grid" key={seed}>
          <TypingScene />
          <StreamScene />
          <UploadScene />
          <RunScene />
          <SkeletonScene />
          <ChartScene />
          <ApproveScene />
          <ErrorScene />
          <PresenceScene />
          <MicroCard />
        </div>

        <Scenarios key={`sc-${seed}`} />
      </div>
    </Reduced.Provider>
  )
}
