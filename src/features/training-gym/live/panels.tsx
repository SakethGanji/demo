import { useState } from 'react'
import type { GymFamily, GymValidation, PatchChange } from '@/shared/api/types'
import { ParamChip } from '@/shared/primitives/ParamChip'
import { ResonanceWave } from '@/shared/primitives/ResonanceWave'
import { MathText } from '@/shared/components/MathText'
import { fmtNum, fmtSecs } from './body'

const keyOfLine = (l: string) => /^ {4}([A-Za-z_][\w]*)=/.exec(l)?.[1]

/** The exact TabularPredictor(...).fit(...) the worker will run, as a live diff against fit()'s defaults: lines you
 * changed are tinted, preset expansions are listed as comments with the default they replace, Vela's patch is marked. */
export function CallDiff({ v, patch, pending }: { v: GymValidation | null; patch: { title: string; changes: PatchChange[] } | null; pending: boolean }) {
  const [showPreset, setShowPreset] = useState(true)
  if (!v?.call) return <div className="card"><div className="card-head"><h3 className="card-title">Resolved Call</h3></div><div className="card-body row"><ResonanceWave width={64} height={14} fast /> Resolving with the API…</div></div>
  const diff = new Map((v.diff ?? []).map((d) => [d.key, d]))
  const patched = new Set((patch?.changes ?? []).map((c) => c.path.replace(/^fit_kwargs\./, '').split('.')[0]))
  let cur: string | undefined
  const lines = v.call.split('\n').map((l) => {
    const k = keyOfLine(l)
    if (k) cur = k
    else if (/^\)/.test(l) || /^predictor\./.test(l) || /^from |^predictor =|^$/.test(l)) cur = undefined
    return { l, k: cur }
  })
  const preset = (v.diff ?? []).filter((d) => d.origin === 'preset')
  const changed = (v.diff ?? []).filter((d) => d.changed && d.origin !== 'preset').length
  const show = (x: unknown) => (typeof x === 'string' ? `'${x}'` : JSON.stringify(x).replace(/\btrue\b/g, 'True').replace(/\bfalse\b/g, 'False').replace(/\bnull\b/g, 'None'))
  return (
    <div className={`card gym-call ${pending ? 'pending' : ''}`} data-testid="gym-call">
      <div className="card-head">
        <div className="left"><h3 className="card-title">Resolved Call</h3><span className="chip">diff vs fit() defaults</span></div>
        <div className="row"><ParamChip sym="Δ" value={String(changed)} />{pending && <ResonanceWave width={36} height={12} fast />}</div>
      </div>
      <div className="card-body">
        <div className="config call" data-testid="gym-call-code">
          {v.data && <span className="cm"># train_data: {v.data.train_rows.toLocaleString()} rows × {v.data.d} features{v.data.dropped.length ? ` (dropped: ${v.data.dropped.join(', ')})` : ''}</span>}
          {lines.map(({ l, k }, i) => {
            const d = k ? diff.get(k) : undefined
            const cls = d ? (d.changed ? 'diff' : '') : ''
            const vela = k && patched.has(k)
            return <span key={i} className={`${cls} ${vela ? 'vela' : ''}`} data-key={k ?? undefined}>{l || ' '}{d && l === lines.find((x) => x.k === k)?.l && <em className={`origin ${d.origin}`}>{vela ? 'vela' : d.origin === 'you' ? 'you' : 'ensemble'}</em>}</span>
          })}
          {preset.length > 0 && <>
            <span className="cm" onClick={() => setShowPreset(!showPreset)} style={{ cursor: 'pointer' }}># presets expand to ({preset.length}) {showPreset ? '▾' : '▸'}</span>
            {showPreset && preset.map((d) => <span key={d.key} className="cm">#   {d.key}={show(d.value)}{d.changed ? `   (default ${show(d.default)})` : ''}</span>)}
          </>}
        </div>
        <p className="helper" style={{ marginTop: 'var(--space-8)', fontSize: 12 }}>Tinted lines differ from <span className="mono">TabularPredictor.fit</span>'s defaults; <em className="origin you">you</em> set them, <em className="origin ensemble">ensemble</em> added them (TF-IDF models for text, column types), <em className="origin vela">vela</em> came from the patch. Resolved on the server by the same code the fit worker runs.</p>
      </div>
    </div>
  )
}

/** T̂ with every symbol bound to this config and this data. */
export function Estimate({ v }: { v: GymValidation | null }) {
  const e = v?.estimate
  const eff = v?.effective
  if (!e || !eff) return null
  const max = Math.max(...e.families.map((f) => f.seconds), 1e-9)
  const folds = Math.max(1, e.k * e.s)
  return (
    <div className="card" data-testid="gym-estimate">
      <div className="card-head">
        <div className="left"><h3 className="card-title">Fit Estimate</h3><span className="helper" style={{ fontSize: 12 }}>from measured rates on this machine</span></div>
        <b className="mono" data-testid="gym-estimate-seconds" style={{ fontSize: 15 }}>T̂ ≈ {fmtSecs(e.seconds)}</b>
      </div>
      <div className="card-body">
        <div className="formula compact">
          <div className="eq"><i>T̂</i> = min(<i>τ</i>, <i>c</i>₀ + <span className="up">Σ</span><sub><i>f</i></sub> <i>v<sub>f</sub></i> · <i>r<sub>f</sub></i> · <i>n</i> · <i>d</i> · max(1, <i>k</i>·<i>s</i>) · (<i>L</i> + 1))</div>
          <div className="eq sub-eq mono">= min({e.budget ?? '∞'}, {e.overhead} + Σ<sub>f</sub> v·r · {e.n.toLocaleString()} · {e.d} · {folds} · {e.L + 1}) = <b>{fmtSecs(e.seconds)}</b></div>
          <dl className="where">
            <dt>τ</dt><dd>time budget, <span className="mono">time_limit</span> = {e.budget ?? 'none'} s</dd>
            <dt>n, d</dt><dd>training rows after the holdout ({e.n.toLocaleString()}) and feature columns ({e.d})</dd>
            <dt>k, s</dt><dd>bag folds and bag sets AutoGluon will use for this data: k = {e.k}, s = {e.s}</dd>
            <dt>L</dt><dd>stack levels above the base models: L = {e.L}</dd>
            <dt>v<sub>f</sub></dt><dd>configs of family f in the zoo (× HPO trials)</dd>
            <dt>r<sub>f</sub></dt><dd>seconds per row·feature for one fold model: the median over models already fit here, or a prior</dd>
            <dt>c₀</dt><dd>fixed overhead (preprocessing, ensembling) = {e.overhead} s</dd>
          </dl>
        </div>
        <table className="table est-table">
          <thead><tr><th>f</th><th className="r">v</th><th className="r">r<sub>f</sub></th><th>source</th><th style={{ width: '34%' }}>seconds</th></tr></thead>
          <tbody>{e.families.map((f) => (
            <tr key={f.family}><td className="mono">{f.family}</td><td className="r mono">{f.variants}{f.trials > 1 ? `×${f.trials}` : ''}</td><td className="r mono">{fmtNum(f.rate)}</td>
              <td className="muted" style={{ fontSize: 11.5 }}>{f.rate_source}</td>
              <td><div className="est-bar"><i style={{ width: `${(f.seconds / max) * 100}%` }} /><span className="mono">{fmtSecs(f.seconds)}</span></div></td></tr>
          ))}</tbody>
        </table>
        <p className={`helper ${e.budget_binds ? 'warn' : ''}`} style={{ fontSize: 12, marginTop: 8 }} data-testid="gym-estimate-note">{e.budget_binds ? `▲ unbounded T̂ = ${fmtSecs(e.unbounded_seconds)} > τ. ` : ''}{e.note}</p>
      </div>
    </div>
  )
}

/** What AutoGluon itself decides for this data (get_validation_and_stacking_method), shown as chips. */
export function Effective({ v }: { v: GymValidation | null }) {
  const e = v?.effective
  if (!e) return null
  return (
    <div className="chips-row" data-testid="gym-effective">
      <ParamChip sym="k" value={String(e.num_bag_folds)} />
      <ParamChip sym="s" value={String(e.num_bag_sets)} />
      <ParamChip sym="L" value={String(e.num_stack_levels)} />
      {e.num_stack_levels > 0 && <span className="chip">dynamic_stacking={String(e.dynamic_stacking)}</span>}
      {e.holdout_frac != null && <span className="chip">holdout_frac={fmtNum(e.holdout_frac)}</span>}
      <span className="chip">refit_full={String(e.refit_full)}</span>
    </div>
  )
}

/** The family's objective from /api/models, its where-list, and the slider symbols bound to their current values. */
export function FamilyFormula({ fam, values }: { fam: GymFamily; values?: Record<string, string> }) {
  return (
    <div className="formula" data-testid={`gym-formula-${fam.key}`}>
      <div className="micro-label">{fam.name} · objective</div>
      <div className="eq obj"><MathText text={fam.objective} /></div>
      <dl className="where">
        {Object.entries(fam.symbols).map(([s, d]) => <div key={s} className="wrow"><dt><MathText text={s} /></dt><dd>{d}{values?.[s] ? <> · <b className="mono">{values[s]}</b></> : null}</dd></div>)}
      </dl>
    </div>
  )
}

export function Checks({ v }: { v: GymValidation | null }) {
  if (!v) return <div className="check"><span className="g-ok">○</span><b>Validation</b><span className="table-cell">waiting for the API…</span><span /></div>
  const rows = [...v.errors.map((e) => ['err', e] as const), ...v.warnings.map((e) => ['warn', e] as const), ...v.info.map((e) => ['info', e] as const)]
  if (!rows.length) return <div className="check" data-testid="gym-check-ok"><span className="g-ok">●</span><b>All checks pass</b><span className="table-cell" style={{ color: 'var(--text-body)' }}>every fit argument is one TabularPredictor.fit accepts, with valid values for this data</span><span className="micro-label">server</span></div>
  return <>{rows.map(([k, e], i) => (
    <div key={i} className="check" data-testid={`gym-check-${k}`}>
      <span className={k === 'err' ? 'g-err' : k === 'warn' ? 'g-warn' : 'g-ok'}>{k === 'err' ? '✕' : k === 'warn' ? '▲' : 'ℹ'}</span>
      <b className="mono" style={{ fontSize: 12 }}>{e.path}</b><span className="table-cell" style={{ color: 'var(--text-body)' }}>{e.message}</span><span className="micro-label">{k === 'err' ? 'error' : k === 'warn' ? 'warning' : 'note'}</span>
    </div>
  ))}</>
}
