import { useEffect, useState } from 'react'
import { api } from '@/shared/api/client'
import type { ModelVersion, ReasonCode, ReasonMapping, ReasonsConfig } from '@/shared/api/types'
import { fmtScore } from '@/shared/lib/metrics'
import { ParamChip } from '@/shared/primitives/ParamChip'
import { ResonanceWave } from '@/shared/primitives/ResonanceWave'

const pct = (x: number | null | undefined) => (x == null ? '—' : `${(x * 100).toFixed(0)}%`)

/** F10 · reason codes for adverse decisions: the rule at the version's threshold, how φ is computed for the served
 *  model, the editable mapping table (per model) and the stability check. */
export function ReasonCodes({ v, actor, onEdited }: { v: ModelVersion; actor: string; onEdited?: () => void }) {
  const [c, setC] = useState<ReasonsConfig | null>(null)
  const [rows, setRows] = useState<ReasonMapping[]>([])
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState('')
  const [saved, setSaved] = useState('')
  useEffect(() => {
    let live = true
    setC(null); setErr('')
    api.registry.reasons(v.id).then((r) => { if (live) { setC(r); setRows(r.mapping) } }).catch((e) => live && setErr((e as Error).message))
    return () => { live = false }
  }, [v.id])
  if (err && !c) return <div className="card card-body"><p className="helper" role="alert" style={{ color: 'var(--status-danger)' }}>✕ {err}</p></div>
  if (!c) return <div className="card card-body row"><ResonanceWave /> Setting up the attribution for {v.result?.version?.best_model}…</div>
  const a = c.attribution
  const dirty = JSON.stringify(rows) !== JSON.stringify(c.mapping)
  const set = (i: number, k: keyof ReasonMapping, val: string) => setRows(rows.map((r, j) => (j === i ? { ...r, [k]: val } : r)))
  const save = async () => {
    setBusy('save'); setErr(''); setSaved('')
    try {
      const r = await api.registry.editReasons(v.id, actor, { mapping: rows.map(({ feature, code, high, low, category, missing }) => ({ feature, code, high, low, category, missing })) })
      setC(r); setRows(r.mapping); setSaved(`Saved for every version of ${r.model_name} · audited as ${actor}`); onEdited?.()
    } catch (e) { setErr((e as Error).message) } finally { setBusy('') }
  }
  const setRef = async (reference: 'approved' | 'population') => {
    setBusy('ref'); setErr('')
    try { const r = await api.registry.editReasons(v.id, actor, { reference }); setC(r); setRows(r.mapping) } catch (e) { setErr((e as Error).message) } finally { setBusy('') }
  }
  const stab = async () => {
    setBusy('stab'); setErr('')
    try { const s = await api.registry.reasonStability(v.id, { rows: 300, noise: 0.05, repeats: 3 }); setC({ ...c, stability: s }); onEdited?.() } catch (e) { setErr((e as Error).message) } finally { setBusy('') }
  }
  const st = c.stability
  const sgn = c.s > 0 ? '+1' : '−1'
  return (
    <div className="stack" style={{ gap: 'var(--space-16)' }} data-testid="reason-codes">
      <div className="card">
        <div className="card-head">
          <div className="left"><h3 className="card-title">Reason Codes (Adverse Action)</h3><span className="helper" style={{ fontSize: 12 }}>the top {c.top_k} factors behind each adverse decision of {v.model_name} v{v.version}</span></div>
          <span className="mono helper" style={{ fontSize: 12 }}>{v.result?.version?.best_model}</span>
        </div>
        <div className="card-body stack" style={{ gap: 'var(--space-12)' }}>
          <div className="navy-well" style={{ padding: 'var(--space-12) var(--space-16)', minHeight: 0 }} data-testid="reason-rule">
            <div className="tp-eq" style={{ fontSize: 14 }}>adverse(x) ⟺ ŷ(x) = <b>{c.adverse_class}</b> ⟺ p̂(x) {c.s > 0 ? '≥' : '<'} t = <b className="mono" data-testid="reason-t">{c.threshold.toFixed(4)}</b></div>
            <div className="tp-eq" style={{ fontSize: 14 }}>aⱼ(x) = s·φⱼ(x), &nbsp;s = {sgn}; &nbsp;reasons(x) = the top {c.top_k} features j with aⱼ(x) &gt; 0, ranked by aⱼ</div>
            <div className="tp-eq" style={{ fontSize: 14 }}>φⱼ: Shapley value of v(S) = (1/B) Σ<sub>b</sub> f(x<sub>S</sub>, b<sub>S̄</sub>), &nbsp;f(x) = φ₀ + Σⱼ φⱼ(x)</div>
          </div>
          <p className="helper" style={{ fontSize: 12.5 }}>
            p̂(x) is the served model's probability of <b>{c.positive_class}</b>; t is this version's own threshold (calibrated at training — on cost when the run was trained to a business cost); s = +1 when the adverse class is the positive class.
            The background b ranges over B = {a.background} reference applicants: {c.reference === 'approved' ? <>training rows the served model <b>approves</b> ({c.reference_pool.rows.toLocaleString()} of {c.reference_pool.of?.toLocaleString()} sampled), so φ₀ = E[f | approved] and each reason says how this applicant differs from approved ones</> : <>all training rows, so φ₀ = E[f]</>}.
            A feature with aⱼ ≤ 0 lowers the risk and is never cited, so a row with fewer than {c.top_k} such features gets fewer codes.
          </p>
          <div className="row" style={{ gap: 'var(--space-8)', flexWrap: 'wrap' }}>
            <ParamChip sym="method" value={a.method === 'tree' ? 'TreeSHAP (exact)' : a.exact ? 'Kernel SHAP (exact)' : 'Kernel SHAP (sampled)'} />
            <ParamChip sym="B" value={String(a.background)} />
            {a.coalitions != null && <ParamChip sym="M" value={String(a.coalitions)} />}
            <ParamChip sym="d" value={String(a.d)} />
            {a.evals_per_row != null && <ParamChip sym="f calls / row" value={a.evals_per_row.toLocaleString()} />}
            <span className="row helper" style={{ fontSize: 12, gap: 6 }}>reference:
              <button className={`btn sm ${c.reference === 'approved' ? 'primary' : ''}`} disabled={!!busy || !actor} onClick={() => setRef('approved')}>approved</button>
              <button className={`btn sm ${c.reference === 'population' ? 'primary' : ''}`} disabled={!!busy || !actor} onClick={() => setRef('population')}>everyone</button>
            </span>
          </div>
          <p className="helper" style={{ fontSize: 12 }} data-testid="reason-method">{a.name}. {a.method === 'tree'
            ? <>Exact for the served model: {a.why}; f(x) = φ₀ + Σφⱼ is checked to 10⁻⁶ on every call.</>
            : <>Why not TreeSHAP: {a.why}. M coalitions S (the heaviest sizes |S| = 1, d − 1, … enumerated with their Shapley-kernel weights, the rest sampled in complementary pairs, seed {a.seed}) are shared by every row, and one predict call scores R·M·B hybrid rows; f(x) = φ₀ + Σφⱼ holds exactly, the φⱼ carry sampling error measured below against M = 4,096.</>}</p>
          {Object.keys(c.constraints).length > 0 && <p className="helper" style={{ fontSize: 12 }} data-testid="reason-monotone">
            Monotone constraints (F8): {Object.entries(c.constraints).map(([f, s]) => `${f} ${s > 0 ? '↑' : '↓'}`).join(', ')}. For one background row b, φⱼ is a weighted sum of f(xⱼ, ·) − f(bⱼ, ·), each with sign sⱼ·sign(xⱼ − bⱼ). So a constrained feature can only push toward {c.adverse_class} by being {c.s > 0 ? 'above' : 'below'} reference values (its statement direction is fixed), and xⱼ beyond every background value forces the sign of φⱼ — checked on every explained row.
          </p>}
        </div>
      </div>

      <div className="card">
        <div className="card-head">
          <div className="left"><h3 className="card-title">Mapping Table</h3><span className="helper" style={{ fontSize: 12 }}>feature → code and plain-English statement, per model{c.updated_by ? ` · last edited by ${c.updated_by}` : ''}</span></div>
          <div className="row" style={{ gap: 'var(--space-8)' }}>
            {saved && <span className="helper" style={{ fontSize: 12 }}>✓ {saved}</span>}
            <button className="btn primary" data-testid="reason-save" disabled={!dirty || !!busy || !actor} title={actor ? '' : 'Set your name at the top of the registry first'} onClick={save}>Save mapping</button>
          </div>
        </div>
        <div className="table-wrap">
          <table className="table compact" data-testid="reason-mapping">
            <colgroup><col style={{ width: '15%' }} /><col style={{ width: '8%' }} /><col style={{ width: '29%' }} /><col style={{ width: '24%' }} /><col style={{ width: '24%' }} /></colgroup>
            <thead><tr><th>feature</th><th>code</th><th>statement when the value is high · or for a category ({'{value}'})</th><th>statement when low</th><th>missing</th></tr></thead>
            <tbody>{rows.map((r, i) => (
              <tr key={r.feature}>
                <td className="mono">{r.feature}{r.constraint ? <small className="helper"> {r.constraint > 0 ? '↑' : '↓'}</small> : null}</td>
                <td><input className="input" style={{ width: '100%' }} value={r.code} data-testid={`reason-code-${r.feature}`} onChange={(e) => set(i, 'code', e.target.value)} /></td>
                <td>{r.kind === 'number'
                  ? <input className="input" style={{ width: '100%' }} value={r.high ?? ''} data-testid={`reason-high-${r.feature}`} onChange={(e) => set(i, 'high', e.target.value)} />
                  : <input className="input" style={{ width: '100%' }} value={r.category ?? ''} onChange={(e) => set(i, 'category', e.target.value)} />}</td>
                <td>{r.kind === 'number' ? <input className="input" style={{ width: '100%' }} value={r.low ?? ''} data-testid={`reason-low-${r.feature}`} onChange={(e) => set(i, 'low', e.target.value)} /> : <span className="helper">—</span>}</td>
                <td><input className="input" style={{ width: '100%' }} value={r.missing} onChange={(e) => set(i, 'missing', e.target.value)} /></td>
              </tr>
            ))}</tbody>
          </table>
        </div>
        <p className="helper card-body" style={{ fontSize: 12, paddingTop: 0 }}>A numeric feature uses “high” when xⱼ ≥ the reference median, else “low”; a constrained one always uses the side its constraint makes adverse. Codes must be unique.</p>
        {err && <p className="helper card-body" role="alert" style={{ color: 'var(--status-danger)' }}>✕ {err}</p>}
      </div>

      <div className="card" data-testid="reason-stability">
        <div className="card-head">
          <div className="left"><h3 className="card-title">Stability</h3><span className="helper" style={{ fontSize: 12 }}>do the top reasons survive small input noise?</span></div>
          <button className="btn" data-testid="reason-stability-run" disabled={!!busy} onClick={stab}>{st ? 'Run again' : 'Run the stability check'}</button>
        </div>
        <div className="card-body">
          {busy === 'stab' && <span className="row helper"><ResonanceWave width={60} height={14} /> 300 holdout rows, 3 noise draws, in a worker…</span>}
          {!st && busy !== 'stab' && <p className="helper">Not run yet for this version.</p>}
          {st && !st.error && (
            <>
              <div className="navy-well" style={{ padding: 'var(--space-8) var(--space-16)', marginBottom: 8, minHeight: 0 }}><div className="tp-eq" style={{ fontSize: 13.5 }}>x̃ⱼ = xⱼ + ε·σⱼ·z, &nbsp;z ~ N(0, 1), &nbsp;ε = {st.noise}; same coalitions and background for x and x̃; compare reasons(x) with reasons(x̃)</div></div>
              <div className="card navy explain-hero">
                <div className="eh-cell"><span className="micro-label">top-{c.top_k} set unchanged</span><b className="mono" data-testid="stab-set">{pct(st.same_set)}</b></div>
                <div className="eh-cell"><span className="micro-label">first reason unchanged</span><b className="mono">{pct(st.same_top1)}</b></div>
                <div className="eh-cell"><span className="micro-label">same order</span><b className="mono">{pct(st.same_order)}</b></div>
                <div className="eh-cell"><span className="micro-label">mean Jaccard</span><b className="mono">{fmtScore(st.mean_jaccard, 2)}</b></div>
                <div className="eh-cell"><span className="micro-label">decision flipped</span><b className="mono">{pct(st.decision_flips)}</b></div>
              </div>
              <p className="helper" style={{ fontSize: 12, marginTop: 8 }}>{st.rows} adverse decisions among the first {st.holdout_rows} holdout rows × {st.repeats} draws ({st.comparisons} comparisons where the decision stayed adverse); σⱼ is feature j's standard deviation among the reference applicants; categories unchanged. {st.seconds.toFixed(0)} s.
                {st.vs_reference && <> Estimator check against the same game with M = {st.vs_reference.coalitions.toLocaleString()}: same top-{c.top_k} set on {pct(st.vs_reference.same_set)} of rows, mean overlap {pct(st.vs_reference.mean_overlap)}, {pct(st.vs_reference.cited_positive)} of cited features have reference aⱼ &gt; 0.</>}</p>
            </>
          )}
          {st?.error && <p className="helper">{st.error}</p>}
        </div>
      </div>
    </div>
  )
}

/** The codes of one decision (Try It). */
export function ReasonList({ codes, adverse, adverseClass, testId }: { codes: ReasonCode[] | null; adverse: boolean; adverseClass: string; testId?: string }) {
  if (!adverse) return <p className="helper" data-testid={testId}>Not adverse (ŷ ≠ {adverseClass}): no reason codes.</p>
  return (
    <ol className="reasons" data-testid={testId}>
      {(codes ?? []).map((r) => (
        <li key={r.code}>
          <span className="reason-dir up mono" style={{ minWidth: 44 }}>{r.code}</span>
          <span><b>{r.statement}</b> <small className="helper">(<span className="mono">{r.feature}</span> = <span className="mono">{r.value == null ? 'missing' : String(r.value)}</span>, φ = <span className="mono">{r.phi >= 0 ? '+' : ''}{r.phi.toFixed(4)}</span>)</small></span>
        </li>
      ))}
    </ol>
  )
}
