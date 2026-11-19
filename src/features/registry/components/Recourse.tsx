import { useEffect, useState } from 'react'
import { api } from '@/shared/api/client'
import type { ModelVersion, RecourseAction, RecourseConfig, RecourseOption, RecourseResult } from '@/shared/api/types'
import { HeroCell } from '@/shared/components/Status'
import { ResonanceWave } from '@/shared/primitives/ResonanceWave'
import { pct } from '@/shared/lib/format'

const ACTION_WORDS: Record<RecourseAction, string> = { fixed: 'fixed', any: 'either way', increase: 'only up', decrease: 'only down' }

function val(v: unknown): string {
  if (v == null) return 'missing'
  return typeof v === 'number' ? v.toLocaleString(undefined, { maximumFractionDigits: 4 }) : String(v)
}

/** G1 · the counterfactual options of one applicant: what to change, by how much, and what the served model then says. */
export function RecourseOptions({ r, threshold, adverseClass, positiveClass, testId }: { r: RecourseResult; threshold: number; adverseClass: string; positiveClass: string; testId?: string }) {
  if (!r.adverse) return <p className="helper" data-testid={testId}>Not adverse (ŷ ≠ {adverseClass}): nothing to change.</p>
  const opts = r.options.filter((o) => o.valid)
  if (!opts.length) return <p className="helper" data-testid={testId}>No change within the actionable features and the training ranges flips this decision (≤ 3 changes searched).</p>
  return (
    <ol className="reasons" data-testid={testId}>
      {opts.map((o, i) => <OptionRow key={i} o={o} i={i} p0={r.p} t={threshold} pos={positiveClass} />)}
    </ol>
  )
}

function OptionRow({ o, i, p0, t, pos }: { o: RecourseOption; i: number; p0: number; t: number; pos: string }) {
  return (
    <li>
      <span className="reason-dir down mono" style={{ minWidth: 44 }}>#{i + 1}</span>
      <span>
        <b>{o.changes.map((c) => `${c.feature} ${val(c.from)} → ${val(c.to)}`).join(' and ')}</b>{' '}
        <small className="helper">(p̂({pos}) {p0.toFixed(4)} → <span className="mono">{o.p.toFixed(4)}</span> vs t = {t.toFixed(4)} · d = {o.distance.toFixed(2)} · π = {o.plausibility == null ? '—' : o.plausibility.toFixed(2)})</small>
      </span>
    </li>
  )
}

/** Try It: the counterfactuals for the row just scored (only when it is adverse). */
export function TryRecourse({ v, row, adverse }: { v: ModelVersion; row: Record<string, unknown>; adverse: boolean }) {
  const [out, setOut] = useState<{ r: RecourseResult; t: number; s: number; adv: string; pos: string; ms: number } | null>(null)
  const [err, setErr] = useState('')
  const key = JSON.stringify(row)
  useEffect(() => {
    if (!adverse) { setOut(null); return }
    let live = true
    setOut(null); setErr('')
    api.registry.recourseRows(v.id, [row])
      .then((x) => live && setOut({ r: x.results[0], t: x.threshold, s: x.s, adv: x.adverse_class, pos: x.positive_class, ms: x.seconds * 1e3 }))
      .catch((e) => live && setErr((e as Error).message))
    return () => { live = false }
  }, [v.id, key, adverse]) // eslint-disable-line react-hooks/exhaustive-deps
  if (!adverse) return null
  return (
    <div className="card-body" style={{ borderTop: '1px solid var(--border-subtle)' }} data-testid="try-recourse">
      <div className="row" style={{ justifyContent: 'space-between' }}><b>What would change the decision</b>{out && <span className="helper mono" style={{ fontSize: 12 }}>{out.ms.toFixed(0)} ms</span>}</div>
      {!out && !err && <span className="row helper"><ResonanceWave width={60} height={14} /> Searching the actionable features…</span>}
      {err && <p className="helper" role="alert">✕ {err}</p>}
      {out && <RecourseOptions r={out.r} threshold={out.t} adverseClass={out.adv} positiveClass={out.pos} testId="try-recourse-list" />}
      {out && <p className="helper" style={{ fontSize: 12 }}>Each option is re-scored by this version: not {out.adv} ⟺ p̂ {out.s > 0 ? '<' : '≥'} t = {out.t.toFixed(4)}. Only features marked as changeable in Reason Codes → Recourse move, inside the training ranges.</p>}
    </div>
  )
}

/** Reason Codes tab · Recourse: what may change (detected from G0's column roles, editable), the formula and the check. */
export function RecoursePanel({ v, actor, onEdited }: { v: ModelVersion; actor: string; onEdited?: () => void }) {
  const [c, setC] = useState<RecourseConfig | null>(null)
  const [edits, setEdits] = useState<Record<string, RecourseAction>>({})
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')
  useEffect(() => {
    let live = true
    setC(null); setErr('')
    api.registry.recourse(v.id).then((r) => live && setC(r)).catch((e) => live && setErr((e as Error).message))
    return () => { live = false }
  }, [v.id])
  if (err && !c) return <div className="card card-body"><p className="helper" role="alert">✕ Recourse: {err}</p></div>
  if (!c) return <div className="card card-body row"><ResonanceWave /> Reading what each applicant can change…</div>
  const ck = c.check
  const save = async () => {
    setBusy('save'); setErr('')
    try { setC(await api.registry.editRecourse(v.id, actor, edits)); setEdits({}); onEdited?.() } catch (e) { setErr((e as Error).message) } finally { setBusy('') }
  }
  const run = async () => {
    setBusy('check'); setErr('')
    try { const r = await api.registry.recourseCheck(v.id, 120); setC({ ...c, check: r }); onEdited?.() } catch (e) { setErr((e as Error).message) } finally { setBusy('') }
  }
  const s = c.s > 0
  return (
    <div className="card" data-testid="recourse-panel">
      <div className="card-head">
        <div className="left"><h3 className="card-title">Recourse</h3><span className="helper" style={{ fontSize: 12 }}>the smallest realistic changes that turn a {c.adverse_class} decision around</span></div>
        <button className="btn primary" data-testid="recourse-check-run" disabled={!!busy} onClick={run}>{ck ? 'Run the check again' : 'Run the check'}</button>
      </div>
      <div className="card-body stack" style={{ gap: 'var(--space-12)' }}>
        <div className="navy-well" style={{ padding: 'var(--space-12) var(--space-16)', minHeight: 0 }}>
          <div className="tp-eq" style={{ fontSize: 14 }}>find x′ near x with p̂(x′) {s ? '<' : '≥'} t = <b className="mono">{c.threshold.toFixed(4)}</b>, &nbsp;x′ⱼ = xⱼ for every fixed j, &nbsp;≤ {c.max_changes} changes</div>
          <div className="tp-eq" style={{ fontSize: 14 }}>d(x, x′) = Σⱼ |x′ⱼ − xⱼ| / IQRⱼ + #categories changed; &nbsp;π(x′) = (1/N) Σₙ 1[d<sub>NN</sub>(aₙ) ≥ d<sub>NN</sub>(x′)]</div>
        </div>
        <p className="helper" style={{ fontSize: 12.5 }}>
          p̂ is the served model's probability of <b>{c.positive_class}</b> and t this version's threshold. IQRⱼ is feature j's interquartile range in training, so d = 1 is "one typical spread" of one feature (or one category switched).
          π is plausibility: the share of the N = {c.reference_rows.toLocaleString()} approved training applicants aₙ whose nearest approved neighbour is at least as far as x′'s (≈ 0.5 typical, near 0 unusual).
          A greedy search over this version's own predictions finds up to {c.options} different options (each forbids the main feature of the earlier ones); numbers move on a grid of {c.grid} training quantiles, categories only to ones seen in training.
        </p>
        {ck && (
          <>
            <div className="card navy explain-hero" data-testid="recourse-check">
              <HeroCell label="adverse rows with a valid change">{`${ck.with_valid} / ${ck.adverse}`}</HeroCell>
              <HeroCell label="share">{pct(ck.share_valid ?? 0, 1)}</HeroCell>
              <HeroCell label="median changes">{ck.median_changes ?? '—'}</HeroCell>
              <HeroCell label="median d">{ck.median_distance?.toFixed(2) ?? '—'}</HeroCell>
              <HeroCell label="median π">{ck.median_plausibility?.toFixed(2) ?? '—'}</HeroCell>
              <HeroCell label="fixed moved">{String(ck.fixed_changed)}</HeroCell>
            </div>
            <p className="helper" style={{ fontSize: 12 }}>The first {ck.adverse} adverse holdout rows (of {ck.holdout_adverse.toLocaleString()} in {ck.holdout_rows.toLocaleString()}); every option re-scored by the served model. {ck.valid_options} of {ck.options} options flip the decision; values outside training: {ck.out_of_range}; direction breaks: {ck.wrong_direction}. Most used: {Object.entries(ck.features_used).slice(0, 4).map(([f, n]) => `${f} (${n})`).join(', ')}. {ck.seconds.toFixed(0)} s.</p>
            {ck.examples[0] && <div><span className="micro-label">first adverse holdout row (p̂ = {ck.examples[0].p.toFixed(4)})</span><RecourseOptions r={{ ...ck.examples[0], adverse: true }} threshold={ck.threshold} adverseClass={c.adverse_class} positiveClass={c.positive_class} testId="recourse-example" /></div>}
          </>
        )}
        {busy === 'check' && <span className="row helper"><ResonanceWave width={60} height={14} /> 120 adverse holdout rows in a worker…</span>}
        {!ck && busy !== 'check' && <p className="helper">Not checked yet for this version. Try It shows the options for one applicant now.</p>}
        <details className="an-adv" data-testid="recourse-actions">
          <summary className="micro-label">What may change · {c.actionable.length} of {c.features.length} features · detected from the column roles, edit to override</summary>
          <div className="table-wrap">
            <table className="table compact">
              <thead><tr><th>feature</th><th>may move</th><th>why</th></tr></thead>
              <tbody>{c.features.map((f) => (
                <tr key={f.feature}>
                  <td className="mono">{f.feature}</td>
                  <td>
                    <select className="input" value={edits[f.feature] ?? f.action} data-testid={`recourse-action-${f.feature}`} onChange={(e) => setEdits({ ...edits, [f.feature]: e.target.value as RecourseAction })}>
                      {(['fixed', 'any', 'increase', 'decrease'] as RecourseAction[]).map((a) => <option key={a} value={a}>{ACTION_WORDS[a]}</option>)}
                    </select>
                  </td>
                  <td className="helper" style={{ fontSize: 12 }}>{f.effective !== f.action ? <b>{ACTION_WORDS[f.effective]}: </b> : null}{f.monotone ?? f.why}{f.range ? ` · ${val(f.range.min)} → ${val(f.range.max)}` : ''}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
          <div className="row" style={{ gap: 'var(--space-8)', justifyContent: 'flex-end', padding: 'var(--space-8) 0' }}>
            {c.updated_by && <span className="helper" style={{ fontSize: 12 }}>last edited by {c.updated_by}</span>}
            <button className="btn" data-testid="recourse-save" disabled={!Object.keys(edits).length || !!busy || !actor} title={actor ? '' : 'Set your name at the top of the registry first'} onClick={save}>Save</button>
          </div>
        </details>
        {err && <p className="helper" role="alert" style={{ color: 'var(--status-danger)' }}>✕ {err}</p>}
      </div>
    </div>
  )
}
