import { useEffect, useState } from 'react'
import { api } from '@/shared/api/client'
import type { Doctor, DoctorAction, DoctorCheck, Run } from '@/shared/api/types'
import { MathText } from '@/shared/components/MathText'
import { NumberInput } from '@/shared/primitives/NumberInput'
import { ResonanceWave } from '@/shared/primitives/ResonanceWave'
import { StatusPill } from '@/shared/primitives/StatusPill'

type Patch = NonNullable<DoctorAction['patch']>
export type DoctorLinks = {
  go: (r: string) => void
  /** Apply a Gym patch (Vela's handler: opens the Gym with the change). */
  onApplyPatch?: (runId: string, patch: Patch) => void
  /** Open another Explain tab in place (Explain passes it; elsewhere the doctor navigates to Explain). */
  openTab?: (tab: string, extra?: Record<string, string>) => void
}

const SEV_PILL: Record<string, 'danger' | 'warning' | 'neutral'> = { critical: 'danger', major: 'warning', minor: 'neutral' }
const STATUS: Record<string, { label: string; pill: 'danger' | 'warning' | 'success' | 'neutral' | 'info' }> = {
  finding: { label: 'finding', pill: 'warning' }, clear: { label: 'clear', pill: 'success' }, 'no-evidence': { label: 'no evidence', pill: 'neutral' },
  missing: { label: 'not run', pill: 'info' }, 'n/a': { label: 'n/a', pill: 'neutral' },
}
const TAB_OF: Record<string, string> = { learning_curve: 'Learning Curve', shift: 'Shift', slices: 'Weak Slices', latency: 'Performance' }

const num = (v: unknown): string => {
  if (typeof v !== 'number') return typeof v === 'boolean' ? (v ? 'yes' : 'no') : v == null ? '—' : String(v)
  if (Number.isInteger(v)) return v.toLocaleString()
  const a = Math.abs(v)
  return a !== 0 && (a < 0.001 || a >= 1e5) ? v.toExponential(2) : v.toFixed(a < 1 ? 4 : 3)
}

/** The evidence of one check as `key = value` pairs (nested objects one level deep; lists of numbers as intervals). */
function Evidence({ ev }: { ev: Record<string, unknown> }) {
  const items: [string, string][] = []
  const push = (k: string, v: unknown) => {
    if (v == null) return
    if (Array.isArray(v)) {
      if (v.length === 2 && v.every((x) => typeof x === 'number')) items.push([k, `[${num(v[0])}, ${num(v[1])}]`])
      else if (v.length && typeof v[0] !== 'object') items.push([k, v.map(num).join(', ')])
      else if (v.length) items.push([k, `${v.length} item(s)`])
    } else if (typeof v === 'object') {
      for (const [k2, v2] of Object.entries(v as Record<string, unknown>)) if (typeof v2 !== 'object' || Array.isArray(v2)) push(`${k}.${k2}`, v2)
    } else items.push([k, num(v)])
  }
  for (const [k, v] of Object.entries(ev)) push(k, v)
  if (!items.length) return null
  return <div className="doc-ev" data-testid="doctor-evidence">{items.slice(0, 18).map(([k, v]) => <span key={k}><span className="mono k">{k}</span> = <b className="mono">{v}</b></span>)}</div>
}

function useActions(run: Run, links: DoctorLinks, reload: () => void) {
  const [busy, setBusy] = useState<string | null>(null)
  const [note, setNote] = useState<{ text: string; link?: [string, string] } | null>(null)
  const tab = (t: string, extra: Record<string, string> = {}) => {
    if (links.openTab) links.openTab(t, extra)
    else links.go(`explain?run=${run.id}&tab=${encodeURIComponent(t)}${Object.entries(extra).map(([k, v]) => `&${k}=${encodeURIComponent(v)}`).join('')}`)
  }
  const act = async (a: DoctorAction) => {
    setNote(null)
    if (a.kind === 'gym_patch' && a.patch) {
      if (links.onApplyPatch) links.onApplyPatch(run.id, a.patch)
      else links.go(`gym?from=${run.id}`)
      return
    }
    if (a.kind === 'labelling_queue') return tab('Labelling Queue', a.slice_id ? { slice: a.slice_id } : {})
    if (a.kind === 'open_slice') return tab('Weak Slices', a.slice_id ? { slice: a.slice_id } : {})
    if (a.kind === 'inbox') return tab('Annotators')
    if (a.kind === 'register') return links.go(`registry?register=${run.id}`)
    if (!a.endpoint) return
    setBusy(a.title)
    try {
      const out = await api.runs.doctorCall(a.endpoint, a.method ?? 'POST', a.body)
      if (a.kind === 'shift_weights' && out.patch && links.onApplyPatch) { links.onApplyPatch(run.id, out.patch as Patch); return }
      if (a.kind === 'split_twin') setNote({ text: `Started ${String(out.id)}: the same body with an out-of-time split. Diagnose again when it finishes.`, link: [`quick-train?run=${String(out.id)}`, 'Open the twin →'] })
      else if (a.kind === 'run_check' && (a.check === 'learning_curve' || a.check === 'shift')) setNote({ text: `${a.title}: started in its own process. Diagnose again when it finishes.`, link: [`explain?run=${run.id}&tab=${encodeURIComponent(TAB_OF[a.check])}`, `Watch it in ${TAB_OF[a.check]} →`] })
      else reload()
    } catch (e) { setNote({ text: `✕ ${(e as Error).message}` }) } finally { setBusy(null) }
  }
  return { act, busy, note }
}

function ActBtn({ a, act, busy, primary, testId }: { a: DoctorAction; act: (a: DoctorAction) => void; busy: string | null; primary?: boolean; testId?: string }) {
  if (a.kind === 'info') return <span className="helper" style={{ fontSize: 12 }}>{a.title}</span>
  return <button className={`btn sm ${primary ? 'primary' : ''}`} onClick={() => act(a)} disabled={busy != null} title={a.why ?? ''} data-testid={testId}>
    {busy === a.title ? 'Working…' : `${a.title}${a.kind === 'gym_patch' ? ' → Gym' : ''}`}</button>
}

/** F17: the Model Doctor. One fixed decision procedure over every check the workbench has (Vela, F3, F6, F12–F16, the
 *  error floor and the train–holdout gap); the primary limiting factor with its evidence and a one-click prescription,
 *  the ranked secondary ones, and offers to run the checks that are missing. Every number is GET /runs/{id}/doctor. */
export function DoctorPanel({ run, links, compact }: { run: Run; links: DoctorLinks; compact?: boolean }) {
  const [d, setD] = useState<Doctor | null>(null)
  const [err, setErr] = useState('')
  const [tick, setTick] = useState(0)
  const [budget, setBudget] = useState<number | null>(null)
  const [measure, setMeasure] = useState(false)
  useEffect(() => {
    let alive = true
    setErr('')
    api.runs.doctor(run.id, { latency_ms: budget, measure_latency: measure }).then((x) => alive && setD(x)).catch((e) => alive && setErr((e as Error).message))
    return () => { alive = false }
  }, [run.id, tick, budget, measure, run.threshold?.value])
  const reload = () => setTick((t) => t + 1)
  const { act, busy, note } = useActions(run, links, reload)

  if (err && !d) return <div className="card card-body" role="alert" data-testid="doctor-error"><p className="helper">✕ {err}</p></div>
  if (!d) return <div className="card card-body row"><ResonanceWave /> The Model Doctor is reading {run.id}: Vela's checks, the train–holdout gap, the error floor and every earlier result…</div>
  const p = d.primary
  const counts = d.checks.reduce<Record<string, number>>((m, c) => ({ ...m, [c.status]: (m[c.status] ?? 0) + 1 }), {})
  const offers = d.checks.filter((c) => c.offer && c.status !== 'finding')
  const noteEl = note && <p className="helper" style={{ fontSize: 12.5 }} data-testid="doctor-note">{note.text}{note.link ? <> <button className="btn sm ghost" onClick={() => links.go(note.link![0])}>{note.link[1]}</button></> : null}</p>

  if (compact) return (
    <div className="card doc-compact" data-testid="doctor-compact" data-primary={p?.id ?? 'none'}>
      <div className="card-head"><div className="left"><h3 className="card-title">Model Doctor</h3>
        <span className="helper" style={{ fontSize: 12 }}>the limiting factor, by a fixed procedure over {d.checks.length} checks</span></div>
        {p ? <span className={`pill ${SEV_PILL[p.severity!]}`}>{p.severity}</span> : <StatusPill status="success">nothing limiting</StatusPill>}</div>
      <div className="card-body stack" style={{ gap: 8 }}>
        {p ? <>
          <b data-testid="doctor-primary">{p.title}</b>
          <p className="helper" style={{ fontSize: 12.5, margin: 0 }}>{p.reason}</p>
          {p.prescription && <div className="row" style={{ flexWrap: 'wrap', gap: 8 }}><span className="micro-label">prescription</span><ActBtn a={p.prescription} act={act} busy={busy} primary testId="doctor-rx" /></div>}
          {d.secondary.length > 0 && <p className="helper" style={{ fontSize: 12, margin: 0 }}>Then: {d.secondary.map((c) => `${c.title.toLowerCase()} (${c.severity})`).join(' · ')}</p>}
        </> : <p className="helper" style={{ fontSize: 12.5, margin: 0 }}>{d.headline}</p>}
        {offers.length > 0 && <p className="helper" style={{ fontSize: 12, margin: 0 }}>Not run yet: {offers.map((c) => c.title.toLowerCase()).join(', ')}.</p>}
        {noteEl}
        <div className="row"><button className="btn sm" onClick={() => links.openTab ? links.openTab('Model Doctor') : links.go(`explain?run=${run.id}&tab=${encodeURIComponent('Model Doctor')}`)} data-testid="doctor-open">Open Model Doctor →</button></div>
      </div>
    </div>
  )

  return (
    <div className="stack" style={{ gap: 'var(--space-16)' }} data-testid="doctor-panel" data-primary={p?.id ?? 'none'}>
      <div className="card navy explain-hero" data-testid="doctor-hero">
        <div className="eh-cell lead" style={{ minWidth: 260 }}><span className="micro-label">primary limiting factor</span>
          <b style={{ fontSize: 22, whiteSpace: 'normal', lineHeight: 1.25 }} data-testid="doctor-primary">{p ? p.title : 'None found'}</b>
          <small>{p ? <><span className={`pill ${SEV_PILL[p.severity!]}`}>{p.severity}</span> · from {p.source}</> : 'no check that ran found a limiting factor'}</small></div>
        <div className="eh-cell"><span className="micro-label">secondary</span><b className="mono">{d.secondary.length}</b><small>{d.secondary.map((c) => c.id.replace('_', ' ')).join(' · ') || 'none'}</small></div>
        <div className="eh-cell"><span className="micro-label">checks</span><b className="mono">{d.checks.length}</b>
          <small>{Object.entries(counts).map(([k, v]) => `${v} ${STATUS[k]?.label ?? k}`).join(' · ')}</small></div>
        {p?.prescription && <div className="eh-cell"><span className="micro-label">prescription</span>
          <ActBtn a={p.prescription} act={act} busy={busy} primary testId="doctor-rx" /><small style={{ whiteSpace: 'normal' }}>{p.prescription.why}</small></div>}
      </div>
      {p && <p className="helper" style={{ fontSize: 13 }} data-testid="doctor-reason"><b>Why:</b> {p.reason}</p>}
      {noteEl}
      {p && <div className="card"><div className="card-body stack" style={{ gap: 8 }}>
        <Evidence ev={p.evidence} />
        {p.formula && <div className="vela-formula math"><MathText text={p.formula} /></div>}
        {p.rule && <p className="helper" style={{ fontSize: 12, margin: 0 }}><span className="micro-label">rule</span> {p.rule}</p>}
      </div></div>}

      <div className="card" data-testid="doctor-ranked">
        <div className="card-head"><div className="left"><h3 className="card-title">Ranked Factors</h3>
          <span className="helper" style={{ fontSize: 12 }}>by severity, ties by precedence: {d.precedence.join(' › ').replace(/_/g, ' ')}</span></div></div>
        <div className="card-body" style={{ overflowX: 'auto' }}>
          {p ? <table className="table compact"><thead><tr><th className="r">#</th><th>factor</th><th>severity</th><th>evidence</th><th>prescription</th></tr></thead>
            <tbody>{[p, ...d.secondary].map((c, i) => <tr key={c.id} data-testid={`doctor-factor-${c.id}`}>
              <td className="r mono">{i + 1}</td><td><b>{c.title}</b><div className="muted" style={{ fontSize: 11 }}>{c.source}</div></td>
              <td><span className={`pill ${SEV_PILL[c.severity!]}`}>{c.severity}</span></td>
              <td className="helper" style={{ fontSize: 12, whiteSpace: 'normal', maxWidth: 560 }}>{c.reason}</td>
              <td>{c.prescription ? <ActBtn a={c.prescription} act={act} busy={busy} /> : '—'}</td></tr>)}</tbody></table>
            : <p className="helper">{d.headline}</p>}
        </div>
      </div>

      {offers.length > 0 && <div className="card" data-testid="doctor-offers">
        <div className="card-head"><div className="left"><h3 className="card-title">Checks To Run</h3>
          <span className="helper" style={{ fontSize: 12 }}>expensive checks are never started on their own: run them here, then diagnose again</span></div>
          <button className="btn sm" onClick={reload} data-testid="doctor-refresh">Diagnose Again</button></div>
        <div className="card-body stack" style={{ gap: 8 }}>{offers.map((c) => <div key={c.id} className="row" style={{ gap: 12, flexWrap: 'wrap' }}>
          <StatusPill status={STATUS[c.status].pill}>{STATUS[c.status].label}</StatusPill><b style={{ minWidth: 200 }}>{c.title}</b>
          <span className="helper" style={{ fontSize: 12, flex: 1, minWidth: 240 }}>{c.reason} {c.offer!.why}</span>
          <ActBtn a={c.offer!} act={act} busy={busy} testId={`doctor-offer-${c.id}`} /></div>)}</div>
      </div>}

      <div className="card" data-testid="doctor-checks">
        <div className="card-head"><div className="left"><h3 className="card-title">Every Check</h3>
          <span className="helper" style={{ fontSize: 12 }}>status, the numbers, and the fixed rule each one applies</span></div></div>
        <div className="card-body stack" style={{ gap: 0 }}>{d.checks.map((c) => <CheckRow key={c.id} c={c} />)}</div>
      </div>

      <div className="card" data-testid="doctor-procedure">
        <div className="card-head"><div className="left"><h3 className="card-title">How The Doctor Decides</h3><span className="mono helper" style={{ fontSize: 11 }}>{d.procedure}</span></div></div>
        <div className="card-body stack" style={{ gap: 6 }}>
          <p className="helper" style={{ fontSize: 12.5, margin: 0 }}>Each check ends as <b>finding</b> (with a severity: critical › major › minor), <b>clear</b>, <b>no evidence</b> (a learning curve or active-learning comparison that is within noise counts as no evidence, never as a finding), <b>not run</b> (an expensive job that has not run: offered above) or <b>n/a</b>. The primary factor is the finding with the highest severity; ties go to the earlier factor in the precedence list, so what invalidates the measurement (leakage, shift, wrong labels) comes before what limits the model.</p>
          <ul className="helper" style={{ fontSize: 12, margin: 0, paddingLeft: 18 }}>
            <li><b>Label noise vs hard by nature.</b> cleanlab also flags rows where the classes overlap, so noise needs an error floor among the 20 % most confident predictions that the model itself expects (not overconfidence).</li>
            <li><b>Overfit vs label noise.</b> Noisy labels also open a train–holdout gap; overfit needs the gap and an overconfident holdout.</li>
            <li><b>Saturated curve.</b> If the predictions never change, saturation is about the model (underfit), not the data.</li>
            <li><b>Shift.</b> The adversarial check (F15) sees covariate shift only; the random vs out-of-time gap (F6) sees any shift: either raises it.</li>
            <li><b>Weak slices.</b> A slice is fixable only if the model does worse there than predicting the slice's own base rate.</li>
          </ul>
          <details className="an-adv" data-testid="doctor-advanced"><summary className="micro-label">Advanced · latency budget</summary>
            <div className="row" style={{ gap: 'var(--space-16)', flexWrap: 'wrap', marginTop: 'var(--space-8)' }}>
              <label className="stack" style={{ gap: 4 }}><span className="micro-label">one-row budget (ms)</span>
                <NumberInput value={budget ?? d.inputs.latency_budget_ms ?? 50} min={0.5} max={10000} step={1} onChange={(v) => setBudget(v)} label="Latency budget" /></label>
              <label className="row" style={{ gap: 6, alignSelf: 'end' }}><input type="checkbox" checked={measure} onChange={(e) => setMeasure(e.target.checked)} /> <span className="micro-label">measure served latency if missing (F3)</span></label>
            </div>
          </details>
          {(d.inputs.measured_errors.length > 0 || d.inputs.vela_errors.length > 0) && <p className="helper" style={{ fontSize: 11 }}>Could not compute: {[...d.inputs.measured_errors, ...d.inputs.vela_errors].join('; ')}</p>}
        </div>
      </div>
    </div>
  )
}

function CheckRow({ c }: { c: DoctorCheck }) {
  const s = STATUS[c.status]
  return (
    <details className="doc-check" data-testid={`doctor-check-${c.id}`} data-status={c.status}>
      <summary className="row" style={{ gap: 10 }}>
        <span style={{ width: 92 }}><StatusPill status={c.status === 'finding' ? (c.severity === 'critical' ? 'danger' : c.severity === 'major' ? 'warning' : 'neutral') : s.pill}>{c.status === 'finding' ? c.severity! : s.label}</StatusPill></span>
        <b style={{ width: 220 }}>{c.title}</b>
        <span className="helper" style={{ fontSize: 12, flex: 1, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{c.reason}</span>
      </summary>
      <div className="stack" style={{ gap: 6, padding: '6px 0 10px 102px' }}>
        <p className="helper" style={{ fontSize: 12.5, margin: 0 }}>{c.reason}</p>
        <Evidence ev={c.evidence} />
        {c.formula && <div className="vela-formula math"><MathText text={c.formula} /></div>}
        {c.rule && <p className="helper" style={{ fontSize: 11.5, margin: 0 }}><span className="micro-label">rule</span> {c.rule}</p>}
        {c.source && <span className="muted" style={{ fontSize: 11 }}>source: {c.source}</span>}
      </div>
    </details>
  )
}
