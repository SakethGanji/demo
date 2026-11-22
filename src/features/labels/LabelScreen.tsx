import { useCallback, useEffect, useRef, useState } from 'react'
import { labels, classesOf, specCol, wilson, arr, type Item, type Layer, type Quality, type ReviewItem, type Task } from '@/shared/api/labels'
import { useFetch } from '@/shared/hooks/useFetch'
import { useCan, why } from '@/shared/hooks/useCan'
import { usePoc } from '@/shared/hooks/usePoc'
import { Badge, CardHead, Callout, CoverageBar, Flash, Stats, Why } from '@/shared/components/DataKit'
import { Tabs } from '@/shared/primitives/Tabs'
import { int, pct, plural } from '@/shared/lib/format'
import { Busy, Kbd, errText, show } from './parts'

type Mode = 'label' | 'review' | 'quality'
const MODES: Mode[] = ['label', 'review', 'quality']
const MODE_WORDS: Record<Mode, string> = { label: 'Label', review: 'Review', quality: 'Quality' }
const kappaWord = (k: number | null | undefined) => (k == null ? '—' : k >= 0.8 ? 'almost perfect' : k >= 0.6 ? 'substantial' : k >= 0.4 ? 'moderate' : k >= 0.2 ? 'fair' : 'slight')

/** LB-C: one task's working screens: labelling (keyboard-first), review / adjudication, and quality. */
export function LabelScreen({ mode, taskId, layer, root, by, onBack, onMode }: { mode: Mode; taskId: string; layer: Layer; root: string; by: string; onBack: () => void; onMode: (m: Mode) => void }) {
  const t = useFetch(() => labels.task(taskId), [taskId])
  const q = useFetch(() => labels.quality(taskId), [taskId], { pollMs: 15_000 })
  const task = t.data
  const classes = classesOf(task?.classes ?? specCol(layer.schema?.spec, arr(task?.columns ?? task?.columns_json)[0])?.classes)
  const guidelines = task?.guidelines ?? layer.schema?.guidelines ?? ''
  return (
    <div className="stack lbl" style={{ gap: 'var(--space-12)' }} data-testid={`lbl-screen-${mode}`}>
      <div className="card lbl-taskbar">
        <div className="lbl-taskbar-row">
          <button className="btn sm ghost" onClick={onBack} data-testid="lbl-back">← Labels</button>
          <div className="lbl-taskbar-title"><span className="micro-label">task · {layer.name}</span><b>{task?.name ?? '…'}</b></div>
          {task && <Badge status={task.state} />}
          <span className="spacer" />
          <TaskStats task={task} q={q.data} />
        </div>
        <Tabs tabs={MODES.map((m) => MODE_WORDS[m])} value={MODE_WORDS[mode]} onChange={(w) => onMode(MODES.find((m) => MODE_WORDS[m] === w)!)}
          testIds={{ Label: 'lbl-mode-label', Review: 'lbl-mode-review', Quality: 'lbl-mode-quality' }} />
      </div>
      {t.err && !task ? <Callout tone="danger" title="The task could not be read">{t.err}</Callout> : !task ? <Busy>Reading the task…</Busy>
        : mode === 'label' ? <Labelling task={task} classes={classes} guidelines={guidelines} by={by} root={root} onAnswered={q.reload} />
        : mode === 'review' ? <Review task={task} classes={classes} by={by} root={root} />
        : <QualityView q={q.data} err={q.err} />}
    </div>
  )
}

function TaskStats({ task, q }: { task?: Task | null; q?: Quality | null }) {
  const p = task?.progress ?? {}
  const total = p.total ?? (p.open ?? 0) + (p.leased ?? 0) + (p.done ?? 0) + (p.skipped ?? 0)
  const k = q?.kappa ?? q?.fleiss_kappa ?? null
  return (
    <div className="lbl-taskstats" data-testid="lbl-task-stats">
      <span className="lbl-ts"><span className="micro-label">progress</span><CoverageBar m={p.done ?? 0} N={total || null} /><span className="mono muted">{int(p.done ?? 0)} / {int(total)}</span></span>
      <span className="lbl-ts" title={k == null ? 'agreement appears with two or more votes on the same rows' : `Cohen's / Fleiss' κ = ${k.toFixed(3)} (${kappaWord(k)})`}><span className="micro-label">agreement κ</span><b className="mono" data-testid="lbl-kappa">{k == null ? '—' : k.toFixed(2)}</b>{k != null && <span className="muted">{kappaWord(k)}</span>}</span>
      <span className="lbl-ts"><span className="micro-label">disputed</span><b className="mono">{int(p.disputed ?? q?.disputed ?? 0)}</b></span>
    </div>
  )
}

/** The labelling screen: `1–9` class, `Enter` submit (or confirm the pre-label), `S` skip, `F` flag, `←/→` (or ↑/↓)
 *  between rows, `?` guidelines. Prefetches 25 leased items. */
function Labelling({ task, classes, guidelines, by, root, onAnswered }: { task: Task; classes: ReturnType<typeof classesOf>; guidelines: string; by: string; root: string; onAnswered: () => void }) {
  const can = useCan('label.annotate', 'dataset', root, by)
  const [items, setItems] = useState<Item[]>([])
  const [i, setI] = useState(0)
  const [answers, setAnswers] = useState<Record<string, { value?: string; skip?: boolean; flag?: boolean }>>({})
  const [pick, setPick] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)
  const [showGuide, setShowGuide] = useState(true)
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(true)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const [gold, setGold] = useState<{ correct: number; n: number }>({ correct: 0, n: 0 })
  const [paused, setPaused] = useState<string | null>(null)
  const t0 = useRef(Date.now())
  const session = useRef({ n: 0, start: Date.now() })
  const ref = useRef<HTMLDivElement>(null)
  const load = useCallback(async () => {
    setLoading(true)
    try { const r = await labels.next(task.id, 25, by); setItems((cur) => [...cur, ...r.filter((x) => !cur.some((c) => c.assignment_id === x.assignment_id))]) }
    catch (e) { const s = errText(e); if (/paus/i.test(s)) setPaused(s); else setMsg({ ok: false, text: s }) }
    finally { setLoading(false) }
  }, [task.id, by])
  useEffect(() => { void load() }, [load])
  useEffect(() => { ref.current?.focus() }, [items.length > 0]) // eslint-disable-line react-hooks/exhaustive-deps
  const it = items[i]
  const done = it ? answers[it.assignment_id] : undefined
  useEffect(() => { setPick(done?.value ?? null); setNote(null); t0.current = Date.now() }, [i, it?.assignment_id]) // eslint-disable-line react-hooks/exhaustive-deps
  const pending = items.filter((x) => !answers[x.assignment_id]).length
  const submit = async (a: { value?: string; skip?: boolean; flag?: boolean; note?: string }) => {
    if (!it || busy || done) return
    setBusy(true); setMsg(null)
    try {
      const r = await labels.answers(task.id, [{ assignment_id: it.assignment_id, ...a, ms: Date.now() - t0.current }], by)
      setAnswers((x) => ({ ...x, [it.assignment_id]: a }))
      session.current.n += 1
      if (r.gold) setGold({ correct: gold.correct + r.gold.correct, n: gold.n + r.gold.n })
      if (r.paused) setPaused('Your gold accuracy fell below the task minimum: a reviewer resumes you.')
      onAnswered()
      const nx = i + 1
      if (nx >= items.length - 3) void load()
      if (nx < items.length) setI(nx)
    } catch (e) { const s = errText(e); if (/paus/i.test(s)) setPaused(s); else setMsg({ ok: false, text: s }) } finally { setBusy(false) }
  }
  const onKey = (e: React.KeyboardEvent) => {
    if ((e.target as HTMLElement).tagName === 'INPUT' || (e.target as HTMLElement).tagName === 'TEXTAREA') {
      if (e.key === 'Enter' && note != null) { e.preventDefault(); void submit({ flag: true, note }); setNote(null) }
      if (e.key === 'Escape') { setNote(null); ref.current?.focus() }
      return
    }
    const c = classes.find((x) => x.hotkey === e.key)
    if (c && !done) { e.preventDefault(); if (task.task_type === 'multi_label') { const s = new Set((pick ?? '').split('|').filter(Boolean)); s.has(c.value) ? s.delete(c.value) : s.add(c.value); setPick([...s].join('|') || null) } else setPick(c.value); return }
    if (e.key === 'Enter') { e.preventDefault(); const v = pick ?? (it?.prelabel && !it.is_gold && !it.is_blind ? String(it.prelabel.value) : null); if (v != null) void submit({ value: v }); return }
    if (e.key === 's' || e.key === 'S') { e.preventDefault(); void submit({ skip: true }); return }
    if (e.key === 'f' || e.key === 'F') { e.preventDefault(); setNote(''); return }
    if (e.key === '?') { e.preventDefault(); setShowGuide((x) => !x); return }
    if (e.key === 'ArrowRight' || e.key === 'ArrowDown') { e.preventDefault(); setI(Math.min(items.length - 1, i + 1)); return }
    if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') { e.preventDefault(); setI(Math.max(0, i - 1)); return }
  }
  const mins = (Date.now() - session.current.start) / 60000
  const pace = session.current.n && mins > 0.05 ? Math.round(session.current.n / (mins / 60)) : null
  const gci = gold.n ? wilson(gold.correct, gold.n) : null
  const masked = new Set(it?.masked ?? [])
  const ctx = it ? Object.entries(it.context ?? {}).filter(([k]) => k !== '__row_id') : []
  const preShown = it?.prelabel && !it.is_gold && !it.is_blind
  return (
    <div className={`lbl-work ${showGuide ? 'with-guide' : ''}`}>
      <div className="card lbl-item" tabIndex={0} ref={ref} onKeyDown={onKey} data-testid="lbl-item" aria-label="Labelling: number keys pick a class, Enter submits, S skips, arrows move">
        <CardHead title={it ? <>Row <span className="mono" data-testid="lbl-row-id">#{it.row_id}</span> <span className="muted">· {i + 1} of {items.length}</span></> : 'Queue'}
          badge={it ? (done ? <Badge status={done.skip ? 'skip' : done.flag ? 'flagged' : 'done'}>{done.skip ? 'skipped' : done.flag ? 'flagged' : `answered: ${done.value}`}</Badge> : it.reason ? <Badge status="none">{it.reason}</Badge> : undefined) : undefined}
          sub={it ? `${plural(session.current.n, 'answer')} this session${pace ? ` · ${pace}/h` : ''} · ${pending} waiting` : undefined}
          actions={<span className="lbl-qa" data-testid="lbl-qa" title="gold items are graded blind: you are not told which they are">
            <span className="micro-label">QA</span>{gold.n ? <><b className="mono">{gold.correct}/{gold.n}</b> gold <span className="muted mono">{gci ? `${pct(gci[0], 0)}–${pct(gci[1], 0)}` : ''}</span></> : <span className="muted">gold graded blind</span>}</span>} />
        <div className="card-body stack" style={{ gap: 12 }}>
          {paused && <Callout tone="danger" title="You are paused on this task" testId="lbl-paused">{paused}</Callout>}
          {!it ? (loading ? <Busy>Leasing rows…</Busy> : <Callout tone="info" title="Nothing left in your queue">Every leased row is answered. A task manager can queue more rows (fill).</Callout>) : <>
            {masked.size > 0 && <div className="mask-bar" data-testid="lbl-mask"><span className="micro-label">masked for you</span>{[...masked].map((c) => <span key={c} className="pt-chip">🔒 {c}</span>)}<span className="muted">PII-tagged columns: shown masked, never sent unmasked</span></div>}
            <table className="table lbl-ctx" data-testid="lbl-ctx"><tbody>
              {ctx.map(([k, v]) => <tr key={k}><td className="mono lbl-ctx-k">{masked.has(k) && <span aria-label="masked">🔒 </span>}{k}</td><td className={`lbl-ctx-v ${masked.has(k) ? 'masked-cell' : ''}`}>{show(v)}</td></tr>)}
            </tbody></table>
            {preShown && <div className="lbl-pre" data-testid="lbl-prelabel"><span className="micro-label">pre-label</span><span className="pt-chip">{String(it.prelabel!.value)}</span>
              <span className="muted">from {it.prelabel!.source ?? 'a machine'}{it.prelabel!.confidence != null ? ` · ${Number(it.prelabel!.confidence).toFixed(2)}` : ''} · <Kbd>Enter</Kbd> confirms it</span></div>}
            <div className="lbl-classes" role="radiogroup" aria-label="Class">
              {classes.map((c) => { const on = (pick ?? '').split('|').includes(c.value)
                return <button key={c.value} type="button" role="radio" aria-checked={on} className={`lbl-cbtn ${on ? 'on' : ''}`} disabled={!!done}
                  onClick={() => { setPick(c.value); ref.current?.focus() }} data-testid={`lbl-class-${c.value}`}>{c.hotkey && <kbd className="kbd">{c.hotkey}</kbd>}{c.label ?? c.value}</button> })}
            </div>
            {note != null && <div className="row" style={{ gap: 6 }}><input className="input" autoFocus style={{ flex: 1 }} value={note} onChange={(e) => setNote(e.target.value)} onKeyDown={onKey} placeholder="what is wrong with this row? Enter flags it, Esc cancels" data-testid="lbl-flag-note" /></div>}
            <div className="row" style={{ gap: 6 }}>
              <button className="btn sm primary" disabled={!!done || busy || !can?.allow || (!pick && !preShown)} title={why(can, 'Submit (Enter)')} onClick={() => submit({ value: pick ?? String(it.prelabel?.value) })} data-testid="lbl-submit">Submit <Kbd>Enter</Kbd></button>
              <button className="btn sm" disabled={!!done || busy} onClick={() => submit({ skip: true })} data-testid="lbl-skip">Skip <Kbd>S</Kbd></button>
              <button className="btn sm ghost" disabled={!!done || busy} onClick={() => setNote('')}>Flag <Kbd>F</Kbd></button>
              <span className="spacer" />
              <button className="btn sm ghost" disabled={i === 0} onClick={() => setI(i - 1)} aria-label="previous row">←</button>
              <button className="btn sm ghost" disabled={i >= items.length - 1} onClick={() => setI(i + 1)} aria-label="next row">→</button>
            </div>
            {can && !can.allow && <Why>{why(can)}</Why>}
          </>}
          <Flash msg={msg} testId="lbl-answer-msg" />
          <div className="lbl-keys muted"><Kbd>1</Kbd>–<Kbd>9</Kbd> class · <Kbd>Enter</Kbd> submit · <Kbd>S</Kbd> skip · <Kbd>F</Kbd> flag · <Kbd>←</Kbd><Kbd>→</Kbd> rows · <Kbd>?</Kbd> guidelines</div>
        </div>
      </div>
      {showGuide && <div className="card lbl-guide" data-testid="lbl-guide">
        <CardHead title="Guidelines" sub={`schema s${task.schema_version ?? '—'}`} actions={<button className="btn sm ghost" onClick={() => setShowGuide(false)} aria-label="hide guidelines">✕</button>} />
        <div className="card-body">{guidelines ? <pre className="lbl-guidelines">{guidelines}</pre> : <span className="muted">No guidelines for this task.</span>}
          <div className="lbl-legend">{classes.map((c) => <div key={c.value}><kbd className="kbd">{c.hotkey ?? '·'}</kbd> <b>{c.label ?? c.value}</b>{c.description && <span className="muted"> {c.description}</span>}</div>)}</div>
        </div>
      </div>}
    </div>
  )
}

const KINDS = ['disputed', 'gold_fail', 'stale', 'flagged'] as const
function Review({ task, classes, by, root }: { task: Task; classes: ReturnType<typeof classesOf>; by: string; root: string }) {
  const [kind, setKind] = useState<(typeof KINDS)[number]>('disputed')
  const r = useFetch(() => labels.review(task.id, kind), [task.id, kind])
  const can = useCan('label.review', 'dataset', root, by)
  const poc = usePoc()
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const [sel, setSel] = useState(0)
  const adjudicate = async (x: ReviewItem, value: string) => {
    try { await labels.adjudicate(task.id, { row_id: x.row_id, column: x.column ?? arr(task.columns ?? task.columns_json)[0], value, assignment_id: x.assignment_id }, by); setMsg({ ok: true, text: `Row #${x.row_id} adjudicated: ${value}.` }); r.reload() }
    catch (e) { setMsg({ ok: false, text: errText(e) }) }
  }
  const items = r.data ?? []
  const onKey = (e: React.KeyboardEvent) => {
    const x = items[sel]; if (!x) return
    const c = classes.find((k) => k.hotkey === e.key)
    if (c && (poc || !x.you_voted)) { e.preventDefault(); void adjudicate(x, c.value) }
    if (e.key === 'ArrowDown') { e.preventDefault(); setSel(Math.min(items.length - 1, sel + 1)) }
    if (e.key === 'ArrowUp') { e.preventDefault(); setSel(Math.max(0, sel - 1)) }
  }
  return (
    <div className="card" data-testid="lbl-review" tabIndex={0} onKeyDown={onKey}>
      <CardHead title="Review and adjudication" sub="votes side by side; the reviewer's choice settles the row (never on a row you voted on)"
        actions={<div className="seg" role="group">{KINDS.map((k) => <button key={k} className={k === kind ? 'on' : ''} aria-pressed={k === kind} onClick={() => { setKind(k); setSel(0) }} data-testid={`lbl-review-${k}`}>{k.replace('_', ' ')}</button>)}</div>} />
      <div className="card-body stack" style={{ gap: 8 }}>
        <Flash msg={msg} testId="lbl-review-msg" />
        {r.err && !r.data ? <Callout tone="danger">{r.err}</Callout> : !r.data ? <Busy>Reading the {kind.replace('_', ' ')} items…</Busy>
          : !items.length ? <span className="muted">Nothing {kind.replace('_', ' ')} right now.</span>
          : items.map((x, j) => {
            const lock = !poc && !!x.you_voted
            return <div key={`${x.row_id}:${x.column ?? ''}`} className={`lbl-rv ${j === sel ? 'on' : ''}`} onClick={() => setSel(j)} data-testid={`lbl-rv-${x.row_id}`}>
              <div className="row" style={{ gap: 8 }}><b className="mono">#{x.row_id}</b>{x.column && <span className="mono muted">{x.column}</span>}{x.reason && <Badge status={kind}>{x.reason}</Badge>}
                {x.gold != null && <span className="pt-chip">gold: {show(x.gold)}</span>}{lock && <span className="pt-chip" title="four eyes on items: you voted on this row">🔒 you voted on this item</span>}</div>
              <div className="lbl-votes">{x.votes.map((v, k) => <span key={k} className="lbl-vote"><span className="muted">{v.display_name ?? v.actor}{v.source && v.source !== 'human' ? ` (${v.source})` : ''}</span><b>{show(v.value)}</b>
                {v.gold_accuracy != null && <span className="mono muted" title="this annotator's gold accuracy">{pct(v.gold_accuracy, 0)}</span>}</span>)}</div>
              {x.context && <div className="lbl-rv-ctx muted">{Object.entries(x.context).slice(0, 6).map(([k, v]) => <span key={k}><span className="mono">{k}</span> {x.masked?.includes(k) ? '•••' : show(v)}</span>)}</div>}
              <div className="row" style={{ gap: 6 }}>{classes.map((c) => <button key={c.value} className="btn sm" disabled={lock || !by || !can?.allow} title={lock ? 'Four eyes: you voted on this item, so another reviewer settles it' : why(can, `Settle as ${c.value}`)}
                onClick={() => adjudicate(x, c.value)} data-testid={`lbl-adj-${x.row_id}-${c.value}`}>{c.hotkey && <kbd className="kbd">{c.hotkey}</kbd>}{c.value}</button>)}</div>
            </div>
          })}
      </div>
    </div>
  )
}

function QualityView({ q, err }: { q?: Quality | null; err: string }) {
  if (err && !q) return <Callout tone="danger" title="Quality not available">{err}</Callout>
  if (!q) return <Busy>Measuring agreement and gold accuracy…</Busy>
  const k = q.kappa ?? q.fleiss_kappa ?? null
  return (
    <div className="card" data-testid="lbl-quality">
      <CardHead title="Quality" sub="gold accuracy with its 95 % Wilson interval, agreement (κ), pace and the anchoring check, per annotator" />
      <div className="card-body stack" style={{ gap: 10 }}>
        <Stats items={[{ label: 'agreement κ', value: k == null ? '—' : k.toFixed(3), sub: kappaWord(k) }, { label: 'items', value: int(q.n_items ?? 0) }, { label: 'disputed', value: int(q.disputed ?? 0) },
          { label: 'raw agreement', value: q.agreement == null ? '—' : pct(q.agreement) }]} />
        <div className="table-wrap"><table className="table" data-testid="lbl-quality-t">
          <thead><tr><th>Annotator</th><th className="r">Items</th><th className="r">Per hour</th><th>Gold accuracy (95 % CI)</th><th className="r">κ</th><th>Anchoring</th><th>State</th></tr></thead>
          <tbody>{(q.annotators ?? []).map((a) => {
            const ci = a.gold_ci ?? (a.gold_n ? wilson(a.gold_correct ?? 0, a.gold_n) : null)
            return <tr key={a.principal_id}><td>{a.display_name ?? a.principal_id}</td><td className="r mono">{int(a.items ?? 0)}</td><td className="r mono">{a.per_hour != null ? Math.round(a.per_hour) : '—'}</td>
              <td>{a.gold_accuracy != null ? <><CoverageBar value={a.gold_accuracy} /> <span className="mono muted">{ci ? `${pct(ci[0], 0)}–${pct(ci[1], 0)}` : ''}{a.gold_n ? ` · n ${a.gold_n}` : ''}</span></> : <span className="muted">no gold yet</span>}</td>
              <td className="r mono">{a.kappa != null ? a.kappa.toFixed(2) : '—'}</td>
              <td>{a.anchoring ? <Badge status="warn">follows pre-labels</Badge> : <span className="muted">—</span>}</td>
              <td>{a.paused ? <Badge status="paused" title={a.paused_reason ?? ''} /> : <Badge status="active" />}</td></tr>
          })}</tbody></table></div>
        {!(q.annotators ?? []).length && <span className="muted">No answers yet.</span>}
      </div>
    </div>
  )
}
