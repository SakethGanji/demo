// Scenarios: the harder moments from the product itself, built on the same timing helpers and curves as the
// basic scenes in Motion.tsx. A big upload that drops and resumes, Ask building real cells, a workflow run on
// the page view, a workflow waiting for a person, taking over the assistant's browser, and comparing versions.
import { useLayoutEffect, useRef, useState, type CSSProperties } from 'react'
import { Check, Dots, Scene, Spinner, bursty, easeOut, fmt, useProgress } from './Motion'
import './scenarios.css'

/* ── 11. a big upload that drops and resumes ───────────────────────── */

const GB = 2.14
const AT = 0.47 // where the connection drops
const TOTAL = 4812300
const SKIPPED = 312
const BAD: [string, string, string][] = [
  ['18,204', 'mrr_end', '“12,4OO” is not a number'],
  ['90,117', 'signup_at', '“31/02/2024” is not a date'],
  ['1,204,566', 'row', '7 columns, expected 9'],
  ['3,880,012', 'account_id', 'empty'],
]

function ResumeScene() {
  return <Scene n={11} wide title="A big upload that hits problems" why="The connection drops halfway. The bar stops and turns amber where it stopped, and a countdown shows when it retries. It resumes from the same byte, with a mark on the bar to prove nothing was re-sent. While parsing, bad rows are counted live. They're skipped rather than failing the upload, and the count opens into the actual rows."
    tags={['pause: colour 200ms, sheen off', 'retry countdown', 'resume mark pop', 'bad-row counter live', 'details open 260ms']}
    steps={[2200, 2200, 800, 2000, 2400, 1000, 2800]} rest={2400}
    render={step => <ResumeStage step={step} />} />
}

function ResumeStage({ step }: { step: number }) {
  const a = useProgress(step === 0, step > 0, 2200, bursty)
  const off = useProgress(step === 1, step > 1, 2200)
  const b = useProgress(step === 3, step > 3, 2000, bursty)
  const parse = useProgress(step === 4, step > 4, 2400, easeOut)
  const up = step <= 2 ? AT * a : AT + (1 - AT) * b
  const phase = step <= 3 ? 'up' : step === 4 ? 'parse' : 'done'
  const offline = step === 1
  const retry = Math.max(1, 3 - Math.floor(off * 3))
  const left = Math.max(1, Math.ceil((1 - up) * 56))
  const meta = offline ? `Connection lost · retrying in ${retry}s`
    : step === 2 ? `Back online · resuming from ${(AT * GB).toFixed(2)} GB`
    : phase === 'up' ? `${(up * GB).toFixed(2)} of ${GB} GB · ${(36 + Math.sin(up * 11) * 4).toFixed(0)} MB/s · ${left}s left`
    : phase === 'parse' ? `Reading rows · ${fmt(parse * TOTAL)} of ~4.8M`
    : `${GB} GB · 3 tries · nothing re-sent`
  const events: [number, string, string, string][] = [
    [0, '12:04:10', 'Upload started', ''],
    [1, '12:04:39', `Connection lost at ${(AT * GB).toFixed(2)} GB`, 'bad'],
    [2, '12:04:42', `Resumed from ${(AT * GB).toFixed(2)} GB`, 'ok'],
    [4, '12:05:11', 'Upload finished, reading rows', ''],
    [5, '12:05:24', `${fmt(TOTAL)} rows read, ${SKIPPED} skipped`, 'warn'],
  ]
  return (
    <div className="sc-res">
      <div className={`mo-job sc-res__job ${offline ? 'is-off' : ''}`}>
        <div className="mo-job__row">
          <span className="mo-file__ic">CSV</span>
          <div className="mo-job__name"><b>events_2024_full.csv</b>
            <span key={offline ? 'o' : step === 2 ? 'b' : 'n'} className={`mo-job__meta mo-fade ${offline ? 'sc-warn' : step === 2 ? 'sc-ok' : ''}`}>{meta}</span>
          </div>
          <span className={`sc-net ${offline ? 'is-off' : ''}`} aria-label={offline ? 'Offline' : 'Online'}>
            <svg viewBox="0 0 24 24" width={18} height={18} aria-hidden><path d="M2.5 9.5a14 14 0 0 1 19 0M5.5 12.8a9.5 9.5 0 0 1 13 0M8.6 16a5 5 0 0 1 6.8 0" /><circle cx="12" cy="19" r=".8" /><path className="sc-net__x" d="M4 4l16 16" /></svg>
          </span>
          {phase === 'done' ? <Check on size={22} className="mo-check--big" /> : <span className="mo-job__pct">{phase === 'up' ? `${Math.floor(up * 100)}%` : <Spinner size={16} />}</span>}
        </div>
        <div className={`mo-bar sc-res__bar ${offline || step === 2 ? 'is-paused' : ''} ${phase !== 'up' ? 'is-full' : ''}`}>
          <span style={{ transform: `scaleX(${phase === 'up' ? up : 1})` }} />
          {step >= 2 && <i className="sc-res__mark mo-pop" style={{ left: `${AT * 100}%` }} title="Resumed here" />}
        </div>
        <ol className="mo-steps">
          {['Upload', 'Read rows', 'Check types'].map((s, i) => {
            const at = phase === 'up' ? 0 : phase === 'parse' ? (parse > .6 ? 2 : 1) : 3
            const st = at > i ? 'done' : at === i ? (offline && i === 0 ? 'wait' : 'run') : 'wait'
            return <li key={s} className={`is-${st}`}>{st === 'done' ? <Check on size={14} /> : st === 'run' ? <Spinner size={12} /> : <i />}{s}</li>
          })}
        </ol>
        {phase !== 'up' && <div className="mo-counter mo-in">
          <b>{fmt(phase === 'done' ? TOTAL : parse * TOTAL)}</b><span>rows</span>
          <span className={`sc-skip ${phase === 'done' ? 'is-done' : ''}`}><b key={phase === 'done' ? 'd' : 'p'}>{fmt(phase === 'done' ? SKIPPED : parse * SKIPPED)}</b> skipped
            {phase === 'done' && <svg className={`sc-chev ${step >= 6 ? 'is-open' : ''}`} viewBox="0 0 20 20" width={12} height={12} aria-hidden><path d="M6 8l4 4 4-4" /></svg>}
          </span>
        </div>}
        <div className={`mo-out ${step >= 6 ? 'is-open' : ''}`}>
          <div>
            <ul className="sc-bad">
              {BAD.map(([r, c, why], i) => <li key={r} className={step >= 6 ? 'mo-in' : ''} style={{ animationDelay: `${i * 50}ms` }}><span>row {r}</span><code>{c}</code><em>{why}</em></li>)}
              <li className="sc-bad__more">+ 308 more, all in the dataset’s Skipped rows tab</li>
            </ul>
          </div>
        </div>
      </div>
      <ol className="sc-log" aria-label="Upload events">
        {events.filter(([s]) => step >= s).map(([s, t, l, k]) => <li key={s} className={`mo-in is-${k || 'plain'}`}><time>{t}</time><span>{l}</span></li>)}
      </ol>
    </div>
  )
}

/* ── 12. Ask builds cells ──────────────────────────────────────────── */

const ASK_CELLS: [string, string, string][] = [
  ['SQL', 'Find EMEA accounts that churned this quarter', '41 accounts · 9 columns'],
  ['Python', 'Sum the MRR they took with them, by month', '3 months · €184,200 total'],
  ['Chart', 'Chart MRR lost by month', ''],
]

function AskBuildScene() {
  return <Scene n={12} title="Ask builds the cells" why="The plan appears first as outlined placeholder cells, so you see the shape of the work. Each one then becomes a real cell and runs. The cells after it say what they're waiting for, so nothing looks stuck."
    tags={['placeholders stagger 70ms', 'materialise 440ms spring', 'run in order', 'waits-for label']}
    steps={[1000, 1100, 1300, 1300, 1300, 2400]} rest={2200}
    render={step => <AskBuildStage step={step} />} />
}

function AskBuildStage({ step }: { step: number }) {
  return (
    <div className="sc-ask">
      <div className="mo-msg mo-msg--me sc-ask__q">Which EMEA accounts churned this quarter, and how much MRR did we lose?</div>
      {step === 0 && <div className="sc-ask__think mo-fade"><span className="mo-shimmer">Planning</span><Dots label="Planning" /></div>}
      {step >= 1 && <ol className="sc-ask__cells">
        {ASK_CELLS.map(([kind, title, out], i) => {
          const built = step >= 2 + i
          const st = step > 2 + i || step === 5 ? 'done' : step === 2 + i ? 'run' : 'wait'
          return (
            <li key={kind} className={`sc-acell ${built ? 'is-built mo-land' : 'is-ghost mo-in'} is-${st}`} style={{ animationDelay: built ? '0ms' : `${i * 70}ms` }}>
              <span className="sc-acell__n">[{built && st === 'done' ? i + 4 : st === 'run' ? '*' : ' '}]</span>
              <div className="sc-acell__body">
                <div className="sc-acell__head">
                  <em data-k={kind.toLowerCase()}>{kind}</em><b>{title}</b>
                  <span className="sc-acell__st">{st === 'done' ? <Check on size={15} /> : st === 'run' ? <Spinner size={13} /> : i > 0 ? <span className="sc-acell__wait">waits for [{i + 3}]</span> : null}</span>
                </div>
                {st === 'done' && (kind === 'Chart'
                  ? <div className="sc-mini mo-in" aria-label="MRR lost by month: Jul €41k, Aug €58k, Sep €85k">{[41, 58, 85].map((v, j) => <span key={j} style={{ '--h': `${v}%`, animationDelay: `${j * 60}ms` } as CSSProperties}><i />{['Jul', 'Aug', 'Sep'][j]}</span>)}</div>
                  : <div className="sc-acell__out mo-in">{out}</div>)}
              </div>
            </li>
          )
        })}
      </ol>}
    </div>
  )
}

/* ── 13. a workflow run on the page view ───────────────────────────── */

function Tok({ on, v, ph }: { on: boolean; v: string; ph: string }) {
  return <span key={on ? 'v' : 'p'} className={`sc-tok ${on ? 'is-on mo-pop' : ''}`}>{on ? v : ph}</span>
}

function WorkflowRunScene() {
  return <Scene n={13} wide title="A workflow runs on the page" why="The page view of a workflow shows the run in place. A marker on each step goes from waiting to running to done, and the gutter line fills behind it. Placeholders become real values as they arrive. At a branch, the path taken lights up and the other is marked skipped."
    tags={['marker 200ms', 'gutter fill 400ms ease-out', 'value pop 300ms spring', 'skipped path fades to 45%']}
    steps={[700, 1000, 1100, 1000, 1000, 2600]} rest={2000}
    render={step => <WorkflowRunStage step={step} />} />
}

function WorkflowRunStage({ step }: { step: number }) {
  const st = (i: number) => (step > i + 1 || step === 5 ? 'done' : step === i + 1 ? 'run' : 'wait')
  const filled = (i: number) => step > i + 1 || step === 5
  const done = [0, 1, 2, 3].filter(i => st(i) === 'done').length
  const lines = [
    <>When a loan application arrives for <Tok on={step >= 1} v="ACC-1420" ph="account" /> asking <Tok on={step >= 1} v="€48,000" ph="amount" /></>,
    <>Get the credit score from <span className="sc-tok sc-tok--src">Bureau API</span> → score <Tok on={filled(1)} v="712" ph="score" /></>,
    <>If the score is at least <span className="sc-tok sc-tok--src">700</span></>,
    <>Email the offer to <Tok on={filled(3)} v="a.khan@example.com" ph="applicant email" /></>,
  ]
  return (
    <div className="sc-wf">
      <div className="sc-wf__head">
        <b>Loan application review</b>
        <span key={step === 5 ? 'd' : step === 0 ? 'i' : 'r'} className={`sc-run mo-fade is-${step === 5 ? 'done' : step === 0 ? 'idle' : 'run'}`}>
          {step === 5 ? <><Check on size={13} />Run #482 · finished in 3.9s</> : step === 0 ? 'Run #482 · starting' : <><Spinner size={11} />Run #482 · running</>}
        </span>
      </div>
      <ol className="sc-wf__steps" style={{ '--fill': done / 4 } as CSSProperties}>
        <span className="sc-wf__track" aria-hidden><i /></span>
        {lines.map((l, i) => (
          <li key={i} className={`sc-wf__step is-${st(i)}`}>
            <span className="sc-wf__mark">{st(i) === 'done' ? <Check on size={18} /> : st(i) === 'run' ? <Spinner size={14} /> : <i />}</span>
            <div className="sc-wf__line">
              <p>{l}</p>
              {i === 2 && <div className="sc-wf__branch">
                <span className={filled(2) ? 'is-taken mo-pop' : ''}>
                  <svg viewBox="0 0 20 20" width={13} height={13} aria-hidden><path d="M4 10h11m-4-4 4 4-4 4" /></svg>Fast track{filled(2) && <em>712 ≥ 700</em>}
                </span>
                <span className={filled(2) ? 'is-skipped' : ''}>Manual review{filled(2) && <em>skipped</em>}</span>
              </div>}
            </div>
            <span className="sc-wf__time">{st(i) === 'done' ? ['0.1s', '1.8s', '0.0s', '2.0s'][i] : ''}</span>
          </li>
        ))}
      </ol>
    </div>
  )
}

/* ── 14. a wait that a person ends ─────────────────────────────────── */

const hms = (s: number) => `${Math.floor(s / 3600)}h ${String(Math.floor(s / 60) % 60).padStart(2, '0')}m ${String(s % 60).padStart(2, '0')}s`

function WaitApproveScene() {
  return <Scene n={14} title="A wait, ended by a person" why="A waiting step looks asleep: a slow breathing ring and a countdown to when it gives up. The approval request arrives as its own card. When the manager approves, the card folds into a signed line, the wait turns into a check and the next step wakes."
    tags={['sleep ring 3s ease-in-out', 'request lift 340ms', 'fold 260ms', 'next step wakes 200ms']}
    steps={[2300, 1200, 220, 900, 1100, 2400]} rest={2000}
    render={step => <WaitApproveStage step={step} />} />
}

function WaitApproveStage({ step }: { step: number }) {
  const w = useProgress(step === 0, step > 0, 2300)
  const remain = 86398 - Math.floor(w * 2.3) - (step > 0 ? 1 : 0)
  const approved = step >= 3
  const next = step < 4 ? 'wait' : step === 4 ? 'run' : 'done'
  return (
    <div className="sc-wait">
      <div className={`sc-wait__step ${approved ? 'is-done' : 'is-sleep'}`}>
        <span className="sc-wait__ring">{approved ? <Check on size={20} /> : <svg viewBox="0 0 24 24" width={20} height={20} aria-hidden><circle cx="12" cy="12" r="9" /><path d="M12 7.5V12l3 2" /></svg>}</span>
        <div>
          <b>Wait for manager approval</b>
          <span key={approved ? 'a' : 'w'} className="mo-fade">{approved ? 'Waited 41m · approved by Priya Nair' : `Gives up in ${hms(remain)}`}</span>
        </div>
      </div>
      <div className={`mo-out ${step >= 1 && step <= 3 ? 'is-open' : ''}`}>
        <div>
          <div className={`sc-wait__req ${step >= 1 ? 'mo-lift' : ''}`}>
            <span className="sc-wait__from"><span className="mo-av" style={{ '--c': '#2f8f6b' } as CSSProperties}>PN</span>Priya’s inbox</span>
            <b>Approve a €48,000 loan for ACC-1420?</b>
            <span>Credit score 712 · fast track</span>
            <div className="mo-err__acts">
              <button className={`mo-btn mo-btn--sm ${step === 2 ? 'is-press' : ''} ${approved ? 'is-ok' : ''}`} tabIndex={-1}>
                <span className={`mo-btn__l ${approved ? '' : 'is-on'}`}>Approve</span>
                <span className={`mo-btn__l ${approved ? 'is-on' : ''}`}><Check on={approved} size={14} />Approved</span>
              </button>
              <button className="mo-ghostbtn" tabIndex={-1}>Decline</button>
            </div>
          </div>
        </div>
      </div>
      <div className={`sc-wait__step sc-wait__next is-${next}`}>
        <span className="sc-wait__ring">{next === 'done' ? <Check on size={20} /> : next === 'run' ? <Spinner size={16} /> : <i />}</span>
        <div>
          <b>Pay out €48,000 to ACC-1420</b>
          <span key={next} className="mo-fade">{next === 'done' ? 'Paid · ref TX-90412' : next === 'run' ? 'Sending to the payments API…' : 'Waits for the approval above'}</span>
        </div>
      </div>
    </div>
  )
}

/* ── 15. taking over the assistant's browser ───────────────────────── */

type Spot = 'start' | 'note' | 'take' | 'status' | 'save' | 'back' | 'next'
const NOTE = 'Flagged by the NRR review'

function TakeoverScene() {
  return <Scene n={15} title="Taking over the browser" why="While the assistant drives the page, the frame wears its colour and its cursor carries a label. Take over swaps the colour and the banner, the assistant’s cursor steps back, and yours is the only one that acts. Hand back reverses it, and the assistant picks up where you left off."
    tags={['frame colour 240ms', 'banner crossfade 180ms', 'cursor glide 700ms', 'hand-off fade + scale .9']}
    steps={[900, 1600, 700, 1000, 1000, 800, 500, 1800]} rest={2000}
    render={step => <TakeoverStage step={step} />} />
}

function TakeoverStage({ step }: { step: number }) {
  const root = useRef<HTMLDivElement>(null)
  const refs = useRef<Partial<Record<Spot, HTMLElement | null>>>({})
  const [pos, setPos] = useState<Partial<Record<Spot, [number, number]>>>({})
  useLayoutEffect(() => {
    const measure = () => {
      const r = root.current?.getBoundingClientRect()
      if (!r) return
      const next: Partial<Record<Spot, [number, number]>> = { start: [r.width * .62, r.height * .9] }
      for (const [k, el] of Object.entries(refs.current)) {
        if (!el) continue
        const b = el.getBoundingClientRect()
        next[k as Spot] = [b.left - r.left + Math.min(b.width * .3, 60), b.top - r.top + b.height * .55]
      }
      setPos(next)
    }
    measure()
    const ro = new ResizeObserver(measure)
    if (root.current) ro.observe(root.current)
    return () => ro.disconnect()
  }, [])
  const typed = useProgress(step === 1, step > 1, 1400)
  const user = step >= 3 && step <= 5
  const note = step === 0 ? '' : NOTE.slice(0, Math.round(typed * NOTE.length))
  const ai: Spot = step === 0 ? 'start' : step >= 7 ? 'next' : 'note'
  const me: Spot = step === 3 ? 'status' : step === 4 ? 'save' : step >= 5 ? 'back' : 'take'
  const saved = step >= 4
  const at = (s: Spot): CSSProperties => { const p = pos[s] ?? pos.start ?? [0, 0]; return { left: p[0], top: p[1] } }
  const set = (k: Spot) => (el: HTMLElement | null) => { refs.current[k] = el }
  return (
    <div ref={root} className={`sc-tk ${user ? 'is-user' : 'is-ai'}`}>
      <div className="sc-tk__banner">
        <span className="sc-tk__who">
          <span className={`sc-tk__lbl ${user ? '' : 'is-on'}`}><span className="sc-tk__dot" />Assistant is working in this tab</span>
          <span className={`sc-tk__lbl ${user ? 'is-on' : ''}`}><span className="sc-tk__dot" />You’re in control · the assistant is paused</span>
        </span>
        <button ref={set('take')} className={`sc-tk__btn ${user ? 'is-hidden' : ''} ${step === 2 ? 'is-press' : ''}`} tabIndex={-1}>Take over</button>
        <button ref={set('back')} className={`sc-tk__btn sc-tk__btn--back ${user ? '' : 'is-hidden'} ${step === 6 ? 'is-press' : ''}`} tabIndex={-1}>Hand back</button>
      </div>
      <div className="sc-tk__frame">
        <div className="sc-tk__chrome"><i /><i /><i /><span>crm.example.com/accounts/ACC-1420</span></div>
        <div className="sc-tk__page">
          <h4>Northwind Ltd <span>ACC-1420</span></h4>
          <div className="sc-tk__fields">
            <label>Note<span ref={set('note')} className={`sc-tk__input ${step >= 1 && step <= 2 ? 'is-focus-ai' : ''}`}>{note}{step === 1 && <i className="mo-caret is-typing" />}</span></label>
            <label>Status<span ref={set('status')} className={`sc-tk__input sc-tk__select ${step >= 3 ? 'is-set' : ''}`}><span key={step >= 3 ? 'r' : 'a'} className="mo-fade">{step >= 3 ? 'At risk' : 'Active'}</span>
              <svg viewBox="0 0 20 20" width={12} height={12} aria-hidden><path d="M6 8l4 4 4-4" /></svg></span></label>
          </div>
          <div className="sc-tk__acts">
            <button ref={set('save')} className={`mo-btn mo-btn--sm ${step === 4 ? 'is-press' : ''}`} tabIndex={-1}><span className="mo-btn__l is-on">Save</span></button>
            {saved && <span className="sc-tk__saved mo-in"><Check on size={14} />Saved by you</span>}
            <span ref={set('next')} className="sc-tk__nextlink">Next account →</span>
          </div>
        </div>
      </div>
      {pos.start && <>
        <span className={`sc-cur sc-cur--ai ${user ? 'is-away' : ''}`} style={at(ai)}>
          <svg viewBox="0 0 16 16" width={16} height={16} aria-hidden><path d="M2 1.5l11 6.2-4.6 1.3L6.3 14z" /></svg>
          <span className="sc-cur__tag">Assistant{step === 1 && <Dots label="Assistant is typing" />}</span>
        </span>
        <span className={`sc-cur sc-cur--me ${user || step === 2 || step === 6 ? '' : 'is-away'}`} style={at(me)}>
          <svg viewBox="0 0 16 16" width={16} height={16} aria-hidden><path d="M2 1.5l11 6.2-4.6 1.3L6.3 14z" /></svg>
          <span className="sc-cur__tag">You</span>
        </span>
      </>}
    </div>
  )
}

/* ── 16. comparing two versions ────────────────────────────────────── */

type VRow = { id: string; region: [string, string]; mrr: [string, string]; kind: 'same' | 'changed' | 'added' | 'removed' }
const VROWS: VRow[] = [
  { id: 'ACC-1187', region: ['LATAM', 'LATAM'], mrr: ['$3,486', '$3,486'], kind: 'same' },
  { id: 'ACC-1420', region: ['NA', 'NA'], mrr: ['$11,868', '$12,400'], kind: 'changed' },
  { id: 'ACC-2231', region: ['EMEA', 'UKI'], mrr: ['$3,472', '$3,472'], kind: 'changed' },
  { id: 'ACC-2310', region: ['APAC', ''], mrr: ['$933', ''], kind: 'removed' },
  { id: 'ACC-2944', region: ['', 'EMEA'], mrr: ['', '$5,120'], kind: 'added' },
  { id: 'ACC-3012', region: ['', 'NA'], mrr: ['', '$1,940'], kind: 'added' },
]
const SLIDE: [number, number][] = [[14, 600], [86, 1800], [86, 0], [50, 900], [50, 0]]

function VersionScene() {
  return <Scene n={16} title="Comparing two versions" why="Drag the line between this week’s version and last week’s. Left of it shows the new rows with their changes tinted: added in green, removed in red and struck through, and changed values in amber with the old value beside them. Right of it shows the version before. A summary counts the difference."
    tags={['wipe follows the handle', 'tint 180ms', 'handle grip spring', 'summary pop 300ms']}
    steps={[900, 1900, 1100, 1000, 2400]} rest={1400}
    render={step => <VersionStage step={step} />} />
}

function VersionStage({ step }: { step: number }) {
  const [x, dur] = step < SLIDE.length ? SLIDE[step] : [14, 700]
  const row = (r: VRow, v: 0 | 1) => {
    const empty = v === 0 ? r.kind === 'added' : false
    const diff = v === 1 && r.kind !== 'same'
    return (
      <div key={r.id} className={`sc-ver__row ${diff ? `is-${r.kind}` : ''} ${empty ? 'is-empty' : ''}`}>
        <span className="sc-ver__sign">{diff ? (r.kind === 'added' ? '+' : r.kind === 'removed' ? '−' : '~') : ''}</span>
        <span>{empty ? '' : r.id}</span>
        <span className={v === 1 && r.region[0] && r.region[0] !== r.region[1] && r.kind === 'changed' ? 'is-cell' : ''}>
          {v === 1 && r.kind === 'removed' ? r.region[0] : r.region[v]}{v === 1 && r.kind === 'changed' && r.region[0] !== r.region[1] && <s>{r.region[0]}</s>}
        </span>
        <span className={v === 1 && r.kind === 'changed' && r.mrr[0] !== r.mrr[1] ? 'is-cell' : ''}>
          {v === 1 && r.kind === 'removed' ? r.mrr[0] : r.mrr[v]}{v === 1 && r.kind === 'changed' && r.mrr[0] !== r.mrr[1] && <s>{r.mrr[0]}</s>}
        </span>
      </div>
    )
  }
  return (
    <div className="sc-ver">
      <div className="sc-ver__labels"><span>v4 · this week</span><span>v3 · last week</span></div>
      <div className="sc-ver__box" style={{ '--sc-x': `${x}%`, '--sc-dur': `${dur}ms` } as CSSProperties}>
        <div className="sc-ver__layer">{VROWS.map(r => row(r, 0))}</div>
        <div className="sc-ver__layer sc-ver__layer--new" aria-hidden>{VROWS.map(r => row(r, 1))}</div>
        <span className={`sc-ver__handle ${step === 1 || step === 3 ? 'is-drag' : ''}`}><i /></span>
      </div>
      <div className="sc-ver__sum">
        {step >= 4 ? [['ok', '+2 added'], ['bad', '−1 removed'], ['warn', '2 changed']].map(([k, l], i) =>
          <span key={l} className={`sc-chip is-${k} mo-pop`} style={{ animationDelay: `${i * 60}ms` }}>{l}</span>)
          : <span className="sc-ver__hint mo-fade">Drag the line to compare</span>}
      </div>
    </div>
  )
}

/* ── section ───────────────────────────────────────────────────────── */

export function Scenarios() {
  return (
    <>
      <header className="mo-hero sc-hero">
        <div className="mo-eyebrow">From the product</div>
        <h2>Harder moments</h2>
        <p>The same motion rules, applied to things only this product does: uploads that fail halfway, the assistant building a notebook, workflows running, waiting for people, sharing a browser and comparing versions.</p>
      </header>
      <div className="mo-grid">
        <ResumeScene />
        <AskBuildScene />
        <WaitApproveScene />
        <WorkflowRunScene />
        <TakeoverScene />
        <VersionScene />
      </div>
    </>
  )
}
