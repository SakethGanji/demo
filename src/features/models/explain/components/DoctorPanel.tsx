import { useEffect, useState } from 'react'
import { api } from '@/shared/api/client'
import type { Doctor, DoctorAction, DoctorCheck, Lever, MissingInfo, Run } from '@/shared/api/types'
import { MathText } from '@/shared/components/MathText'
import { NumberInput } from '@/shared/primitives/NumberInput'
import { ResonanceWave } from '@/shared/primitives/ResonanceWave'
import { StatusPill } from '@/shared/primitives/StatusPill'
import { runView } from '../../tabs'
import { ReviewPanel } from './ReviewPanel'

type Patch = NonNullable<DoctorAction['patch']>
export type DoctorLinks = {
  go: (r: string) => void
  /** Apply a Gym patch (Vela's handler: opens the Gym with the change). */
  onApplyPatch?: (runId: string, patch: Patch) => void
  /** Open another view of the run page in place (the run page passes it; elsewhere the doctor navigates there). */
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
    else links.go(runView(run.id, t, extra))
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
    // F19: the latency prescription opens Register with its model preselected (serve_model from the API's body)
    if (a.kind === 'register') {
      const sm = (a.body as { serve_model?: string } | undefined)?.serve_model
      return links.go(`registry?register=${run.id}${sm ? `&serve=${encodeURIComponent(sm)}` : ''}`)
    }
    // F18: the retrain proposal lives on the version's monitoring page; it launches there, on a click
    if (a.kind === 'retrain' && a.version_id) return links.go(`registry?version=${a.version_id}&tab=Monitoring`)
    // D3: open another run (an experiment), a view of this run, or a page
    if (a.kind === 'open_run' && a.run_id) return links.go(`models?run=${a.run_id}`)
    if (a.kind === 'open_view' && a.view) return tab(a.view)
    if (a.kind === 'link' && a.link) return links.go(a.link)
    if (a.kind === 'confirm_decision_time') return links.go(`build?dataset=${encodeURIComponent(a.dataset_id ?? run.dataset_id)}`)
    if (!a.endpoint) return
    setBusy(a.title)
    try {
      const out = await api.runs.doctorCall(a.endpoint, a.method ?? 'POST', a.body)
      if (a.kind === 'shift_weights' && out.patch && links.onApplyPatch) { links.onApplyPatch(run.id, out.patch as Patch); return }
      if (a.kind === 'experiment') setNote({ text: `Started ${String(out.id)}: ${String(out.title ?? a.title)}, same holdout. Diagnose again when it finishes: the card then shows Δ with its CI.`, link: [`quick-train?run=${String(out.id)}`, 'Watch it →'] })
      else if (a.kind === 'split_twin') setNote({ text: `Started ${String(out.id)}: the same body with an out-of-time split. Diagnose again when it finishes.`, link: [`quick-train?run=${String(out.id)}`, 'Open the twin →'] })
      else if (a.kind === 'run_check' && (a.check === 'learning_curve' || a.check === 'shift')) setNote({ text: `${a.title}: started in its own process. Diagnose again when it finishes.`, link: [runView(run.id, TAB_OF[a.check]), `Watch it in ${TAB_OF[a.check]} →`] })
      else reload()
    } catch (e) { setNote({ text: `✕ ${(e as Error).message}` }) } finally { setBusy(null) }
  }
  return { act, busy, note }
}

export function ActBtn({ a, act, busy, primary, testId }: { a: DoctorAction; act: (a: DoctorAction) => void; busy: string | null; primary?: boolean; testId?: string }) {
  if (a.kind === 'info') return <span className="helper" style={{ fontSize: 12 }}>{a.title}</span>
  return <button className={`btn sm ${primary ? 'primary' : ''}`} style={{ whiteSpace: 'normal', maxWidth: '100%', textAlign: 'left', height: 'auto', minHeight: 28 }} onClick={() => act(a)} disabled={busy != null} title={a.why ?? ''} data-testid={testId}>
    {busy === a.title ? 'Working…' : `${a.title}${a.kind === 'gym_patch' ? ' → Gym' : a.kind === 'link' || a.kind === 'open_run' || a.kind === 'open_view' || a.kind === 'confirm_decision_time' ? ' →' : ''}`}</button>
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
      <div className="card-head"><div className="left"><h3 className="card-title">Model Doctor</h3></div>
        {p ? <span className={`pill ${SEV_PILL[p.severity!]}`}>{p.severity}</span> : <StatusPill status="success">nothing limiting</StatusPill>}</div>
      <div className="card-body stack" style={{ gap: 8 }}>
        <span className="micro-label">limiting factor · fixed procedure over {d.checks.length} checks</span>
        {p ? <>
          <b data-testid="doctor-primary">{p.title}</b>
          <p className="helper" style={{ fontSize: 12.5, margin: 0 }}>{p.reason}</p>
          {p.prescription && <div className="row" style={{ flexWrap: 'wrap', gap: 8 }}><span className="micro-label">prescription</span><ActBtn a={p.prescription} act={act} busy={busy} primary testId="doctor-rx" /></div>}
          {d.secondary.length > 0 && <p className="helper" style={{ fontSize: 12, margin: 0 }}>Then: {d.secondary.map((c) => `${c.title.toLowerCase()} (${c.severity})`).join(' · ')}</p>}
        </> : <p className="helper" style={{ fontSize: 12.5, margin: 0 }}>{d.headline}</p>}
        {offers.length > 0 && <p className="helper" style={{ fontSize: 12, margin: 0 }}>Not run yet: {offers.map((c) => c.title.toLowerCase()).join(', ')}.</p>}
        {noteEl}
        <div className="row"><button className="btn sm" onClick={() => links.openTab ? links.openTab('Model Doctor') : links.go(runView(run.id, 'Model Doctor'))} data-testid="doctor-open">Open Model Doctor →</button></div>
      </div>
    </div>
  )

  return (
    <div className="stack" style={{ gap: 'var(--space-16)' }} data-testid="doctor-panel" data-primary={p?.id ?? 'none'}>
      <p className="doc-headline" data-testid="doctor-headline"><span className="micro-label">diagnosis</span> {p ? <><b>{p.title}</b> <span className={`pill ${SEV_PILL[p.severity!]}`}>{p.severity}</span></> : <b>No limiting factor found</b>}{d.secondary.length > 0 && <span className="helper"> · then {d.secondary.slice(0, 3).map((c) => c.title.toLowerCase()).join(', ')}</span>}</p>
      {d.missing_info && <WhatsMissing m={d.missing_info} />}
      {d.improve && d.improve.length > 0 && <HowToImprove levers={d.improve} act={act} busy={busy} />}
      {noteEl}
      <ReviewPanel run={run} go={links.go} />
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
      {(d.pii_features?.length ?? 0) > 0 && <p className="helper data-warn" style={{ fontSize: 12.5 }} data-testid="doctor-pii">🔒 <b>{d.pii_features!.length} feature{d.pii_features!.length === 1 ? '' : 's'} carry a PII or protected tag</b>: {d.pii_features!.map((f) => `${f.feature} (${f.tag}${f.source === 'suggested' ? ', suggested' : ''})`).join(', ')}. Their values are masked on this page; a model that reads PII needs a reason on its card, and a protected column is normally left out (fair lending).</p>}
      {p && <div className="card"><div className="card-body stack" style={{ gap: 8 }}>
        {p.id === 'leakage' ? <LeakDetail c={p} /> : p.id === 'cost_tradeoff' ? <CostDetail c={p} /> : p.id === 'ceiling' ? <CeilingDetail c={p} /> : <Evidence ev={p.evidence} />}
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

      {!!d.next?.length && <div className="card" data-testid="doctor-next">
        <div className="card-head"><div className="left"><h3 className="card-title">Also Worth Checking</h3>
          <span className="helper" style={{ fontSize: 12 }}>not limits of this model: other ways to look at the same table, from Smart Intake's roles</span></div></div>
        <div className="card-body stack" style={{ gap: 8 }}>{d.next.map((n) => <div key={n.id} className="row" style={{ gap: 12, flexWrap: 'wrap' }} data-testid={`doctor-next-${n.id}`}>
          <b style={{ minWidth: 200 }}>{n.title}</b><span className="helper" style={{ fontSize: 12, flex: 1, minWidth: 240 }}>{n.why}</span>
          <button className="btn sm" onClick={() => links.go(n.link)}>Open →</button></div>)}</div>
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


// --------------------------------------------------------------------------- D3 · What's missing, How to improve

const sgn = (v: number, d = 4) => `${v >= 0 ? '+' : '−'}${Math.abs(v).toFixed(d)}`
const ciTxt = (ci: [number, number] | null | undefined, d = 4) => (ci ? `[${sgn(ci[0], d)}, ${sgn(ci[1], d)}]` : '')
const pc = (v: number) => `${(100 * v).toFixed(Math.abs(v) < 0.1 ? 1 : 0)}%`

function GainCell({ g }: { g: { delta: number | null; ci: [number, number] | null; metric: string | null; simulated?: boolean } | null }) {
  if (!g || g.delta == null) return <span className="muted">not measured</span>
  return <span className="mono" style={{ whiteSpace: 'nowrap' }}><b>{sgn(g.delta)}</b> {g.metric ?? ''}{g.ci && <div className="muted" style={{ fontSize: 11 }}>{ciTxt(g.ci)}</div>}{g.simulated && <div><span className="pill neutral" title="simulated perfect information in this group: an upper bound, net of what a random group of the same size gets">simulation</span></div>}</span>
}

/** The information the table lacks, ranked by measured potential gain; each gap is a data request the bank can act on. */
function WhatsMissing({ m }: { m: MissingInfo }) {
  const [copied, setCopied] = useState(false)
  const ir = m.irreducible
  const copy = async () => {
    const txt = m.gaps.map((g) => `${g.rank}. ${g.title}: ${g.request}${g.gain ? ` (measured ${sgn(g.gain.delta)} ${g.gain.metric ?? ''}${g.gain.simulated ? ', a simulation' : ''})` : ''}`).join('\n')
    try { await navigator.clipboard.writeText(txt); setCopied(true) } catch { setCopied(false) }
  }
  return (
    <div className="card" data-testid="doctor-missing" data-top={m.gaps[0]?.kind ?? 'none'}>
      <div className="card-head"><div className="left"><h3 className="card-title">What's Missing</h3>
        <span className="helper" style={{ fontSize: 12 }}>information the table lacks, ranked by measured potential gain; each gap is a data request</span></div>
        <button className="btn sm primary" onClick={copy} disabled={!m.gaps.length} data-testid="missing-copy">{copied ? 'Copied ✓' : 'Copy The Data Requests'}</button></div>
      <div className="card-body stack" style={{ gap: 10 }}>
        {m.caveat && <p className="helper" role="note" style={{ margin: 0, fontSize: 12.5, color: 'var(--status-warning, #a15c00)' }} data-testid="missing-caveat">▲ {m.caveat}</p>}
        {ir && <p className="helper" style={{ margin: 0, fontSize: 12.5 }} data-testid="missing-irreducible"><span className="micro-label">error no model can avoid with these columns</span> about <b className="mono">{ir.lower.toFixed(3)}</b> to <b className="mono">{ir.r_nn.toFixed(3)}</b> <span className="muted">(from how often look-alike rows disagree; 95 % CI [{ir.r_nn_ci[0].toFixed(3)}, {ir.r_nn_ci[1].toFixed(3)}]; approximate)</span> against a model error of <b className="mono">{ir.model_error.toFixed(3)}</b>{m.caveat
          ? <>: not read as the data's limit while the leak question above is open (the columns include the suspects)</>
          : ir.explained != null && (ir.model_error >= ir.lower && ir.explained < 0.95
          ? <>: roughly <b>{pc(ir.explained)}</b> or more of the error is beyond these columns</>
          : <>: the model errs less than this estimate, so here it is not a lower bound (it runs high with many columns), or the model reads something look-alikes cannot see (a leak, an ID, rows shared with training): see Leakage</>)}.</p>}
        {m.gaps.length ? <table className="table compact"><thead><tr><th className="r">#</th><th>gap</th><th>what the data shows → what to request</th><th className="r">potential gain</th></tr></thead>
          <tbody>{m.gaps.map((g) => <tr key={g.id} data-testid={`gap-${g.kind}`}>
            <td className="r mono">{g.rank}</td><td style={{ minWidth: 170 }}><b>{g.title}</b></td>
            <td className="helper" style={{ fontSize: 12, whiteSpace: 'normal', maxWidth: 640 }}>{g.finding}<div style={{ marginTop: 4, color: 'var(--text-primary)' }}>→ {g.request}</div></td>
            <td className="r"><GainCell g={g.gain} /></td></tr>)}</tbody></table>
          : <p className="helper">{m.errors.length ? `Could not measure: ${m.errors.join('; ')}` : 'No gap measured.'}</p>}
        <div className="helper" style={{ margin: 0, fontSize: 12 }} data-testid="missing-domain" data-status={m.domain_gaps.status}><span className="micro-label">{m.domain_gaps.label}</span>
          {m.domain_gaps.items.length ? <ul style={{ margin: '4px 0 0', paddingLeft: 18 }}>{m.domain_gaps.items.map((x) => <li key={x.title}><b>{x.title}</b>: {x.why}</li>)}</ul>
            : <> {m.domain_gaps.note ?? 'not run yet (the review loop adds them).'}</>}</div>
        <details className="an-adv"><summary className="micro-label">How it is measured</summary>
          <ul className="helper" style={{ fontSize: 11.5, margin: '6px 0 0', paddingLeft: 18 }}>
            {Object.entries(m.rules).filter(([, v]) => v).map(([k, v]) => <li key={k}><b>{k}</b>: {v}</li>)}
            {m.quick && <li><b>quick model</b>: {m.quick.model}, {m.quick.features} features, {m.quick.rows.toLocaleString()} training rows, holdout {m.quick.metric} {m.quick.score.toFixed(4)}</li>}
          </ul></details>
      </div>
    </div>
  )
}

/** One ranked list of levers, each with its measured Δ (or the experiment that measures it); one primary action. */
function HowToImprove({ levers, act, busy }: { levers: Lever[]; act: (a: DoctorAction) => void; busy: string | null }) {
  const first = levers.find((l) => l.action && l.action.kind !== 'info')
  return (
    <div className="card" data-testid="doctor-improve" data-top={levers[0]?.id}>
      <div className="card-head"><div className="left"><h3 className="card-title">How To Improve</h3>
        <span className="helper" style={{ fontSize: 12 }}>each lever with its measured Δ on the same holdout; a must-do first, then by gain</span></div></div>
      <div className="card-body" style={{ overflowX: 'auto' }}>
        <table className="table compact"><thead><tr><th className="r">#</th><th>lever</th><th className="r">measured Δ</th><th>how it was measured</th><th /></tr></thead>
          <tbody>{levers.map((l) => <tr key={l.id} data-testid={`lever-${l.id}`}>
            <td className="r mono">{l.rank}</td><td><b>{l.title}</b>{l.must && <> <span className="pill danger">must</span></>}{l.suggested && <> <span className="pill warning">suggested</span></>}</td>
            <td className="r"><GainCell g={l} /></td>
            <td className="helper" style={{ fontSize: 12, whiteSpace: 'normal', maxWidth: 420 }}>{l.source}{l.note ? ` ${l.note}` : ''}</td>
            <td>{l.action && <ActBtn a={l.action} act={act} busy={busy} primary={l === first} testId={`lever-act-${l.id}`} />}</td></tr>)}</tbody></table>
      </div>
    </div>
  )
}

function LeakDetail({ c }: { c: DoctorCheck }) {
  const ab = c.evidence.ablation as { with?: number; without?: number; delta?: number; ci?: [number, number]; metric?: string; run_id: string; status?: string } | undefined
  const sus = (c.evidence.suspects as { feature: string; reason: string; share?: number; ratio?: number; single_auc?: number }[]) ?? []
  return <div className="stack" style={{ gap: 8 }} data-testid="leak-detail">
    <div className="doc-ev">{sus.map((s) => <span key={s.feature}><span className="mono k">{s.feature}</span> {s.reason}{s.share != null && <> · <b className="mono">{pc(s.share)}</b> of importance</>}{s.ratio != null && <> · <b className="mono">{s.ratio.toFixed(1)}×</b> the next</>}{s.single_auc != null && <> · AUC alone <b className="mono">{s.single_auc.toFixed(3)}</b></>}</span>)}</div>
    {ab?.delta != null ? <div className="row" style={{ gap: 18, flexWrap: 'wrap' }} data-testid="ablation">
      <span><span className="micro-label">with</span> <b className="mono">{ab.with!.toFixed(4)}</b></span>
      <span><span className="micro-label">without</span> <b className="mono">{ab.without!.toFixed(4)}</b></span>
      <span><span className="micro-label">Δ {ab.metric}</span> <b className="mono">{sgn(ab.delta)}</b> <span className="muted mono">{ciTxt(ab.ci)}</span></span>
      <span className="muted" style={{ fontSize: 11 }}>run without the column <span className="mono">{ab.run_id}</span>, same settings and holdout</span></div>
      : <p className="helper" style={{ margin: 0, fontSize: 12 }}>{ab ? `Ablation ${ab.run_id} is ${ab.status}.` : 'Ablation not run yet: the prescription retrains without the column(s).'}</p>}
  </div>
}

function CostDetail({ c }: { c: DoctorCheck }) {
  const rows = (c.evidence.rows as { name: string; t: number; decline_rate: number; catch_rate: number; cost_per_1000: number; cost_per_1000_at_ratio?: number }[]) ?? []
  return <table className="table compact" data-testid="cost-tradeoff"><thead><tr><th>rule</th><th className="r">threshold</th><th className="r">decline rate</th><th className="r">catch rate</th><th className="r">cost / 1,000 (run's costs)</th><th className="r">cost / 1,000 at that ratio</th></tr></thead>
    <tbody>{rows.map((r) => <tr key={r.name} className={r.name === 'served' ? 'hl' : ''}><td><b>{r.name}</b></td><td className="r mono">{r.t.toFixed(3)}</td><td className="r mono">{pc(r.decline_rate)}</td><td className="r mono">{pc(r.catch_rate)}</td><td className="r mono">{r.cost_per_1000.toFixed(0)}</td><td className="r mono">{r.cost_per_1000_at_ratio != null ? r.cost_per_1000_at_ratio.toFixed(0) : '—'}</td></tr>)}</tbody></table>
}

function CeilingDetail({ c }: { c: DoctorCheck }) {
  const conds = (c.evidence.conditions as Record<string, { ok: boolean } & Record<string, unknown>>) ?? {}
  const label: Record<string, string> = { flat: 'learning curve flat', agree: 'single GBM ≈ the best rung', gap_noise: 'train–holdout gap within noise', not_concentrated: 'no fixable weak slice' }
  return <div className="doc-ev" data-testid="ceiling-detail">{Object.entries(conds).map(([k, v]) => <span key={k}>{v.ok ? '✓' : '✕'} {label[k] ?? k}</span>)}</div>
}
