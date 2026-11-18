import { useEffect, useState } from 'react'
import { api } from '@/shared/api/client'
import type { DatasetDetail, DatasetSummary, GymSchema, GymValidation, Run } from '@/shared/api/types'
import { FretSlider } from '@/shared/components/FretSlider'
import { StatusPill } from '@/shared/primitives/StatusPill'
import { NumberInput } from '@/shared/primitives/NumberInput'
import { sup } from '@/shared/lib/format'
import { MathText } from '@/shared/components/MathText'
import { type Body, effectiveHp, fmtNum, getPath, setHp, toggleFamily, variants } from './body'
import { FamilyFormula } from './panels'

export type StepProps = {
  body: Body
  set: (path: string, value: unknown) => void
  setBody: (b: Body) => void
  schema: GymSchema
  v: GymValidation | null
  ds: DatasetDetail | null
  guided: boolean
}

const pow10 = (v: number) => (Math.abs(Math.log10(v) - Math.round(Math.log10(v))) < 1e-9 ? `10${sup(Math.round(Math.log10(v)))}` : fmtNum(v))
const logTicks = (lo: number, hi: number) => {
  const out: number[] = []
  for (let e = Math.ceil(Math.log10(lo)); e <= Math.floor(Math.log10(hi)); e++) out.push(10 ** e)
  return out.length >= 2 ? out : [lo, hi]
}
const linTicks = (lo: number, hi: number, integer?: boolean) => {
  const n = 4
  return Array.from({ length: n + 1 }, (_, i) => { const x = lo + ((hi - lo) * i) / n; return integer ? Math.round(x) : Number(x.toPrecision(3)) })
    .filter((x, i, a) => a.indexOf(x) === i)
}

function Seg<T extends string | number | boolean | null>({ options, value, onChange, testid, label }: {
  options: { v: T; label: string }[]; value: T; onChange: (v: T) => void; testid?: string; label?: string
}) {
  return (
    <div className="seg" role="radiogroup" aria-label={label} data-testid={testid}>
      {options.map((o) => <button key={String(o.v)} role="radio" aria-checked={o.v === value} className={o.v === value ? 'on' : ''} onClick={() => onChange(o.v)}>{o.label}</button>)}
    </div>
  )
}

function Row({ label, sub, children, testid }: { label: React.ReactNode; sub?: React.ReactNode; children: React.ReactNode; testid?: string }) {
  return <div className="gym-row" data-testid={testid}><div className="label"><b>{label}</b>{sub && <small>{sub}</small>}</div><div className="ctl">{children}</div></div>
}

// -- 01 Data ------------------------------------------------------------------------------------------------------

export function DataStep({ body, set, setBody, ds, onDataset, onLoadRun }: StepProps & { onDataset: (id: string) => void; onLoadRun: (id: string) => void }) {
  const [list, setList] = useState<DatasetSummary[]>([])
  const [runs, setRuns] = useState<Run[]>([])
  useEffect(() => { api.datasets.list().then(setList).catch(() => {}); api.runs.list().then((r) => setRuns(r.filter((x) => x.status === 'succeeded'))).catch(() => {}) }, [])
  const drop = new Set(body.drop_columns ?? (ds?.profile.columns.filter((c) => c.type === 'id').map((c) => c.name) ?? []))
  const toggle = (c: string) => { const n = new Set(drop); if (n.has(c)) n.delete(c); else n.add(c); set('drop_columns', [...n]) }
  return (
    <div className="stack">
      <div className="card"><div className="card-head"><h2 className="card-title">Dataset</h2><span className="helper" style={{ fontSize: 12 }}>holdout rows fixed per dataset</span></div>
        <div className="card-body">
          <Row label="Dataset" sub="a sample or an upload from New Model">
            <select className="select" value={body.dataset_id} onChange={(e) => onDataset(e.target.value)} data-testid="gym-dataset">
              {list.map((d) => <option key={d.id} value={d.id}>{d.name} · {d.rows.toLocaleString()} × {d.n_cols}</option>)}
            </select>
          </Row>
          <Row label="Start from a run" sub="load a finished run's exact config (Vela patches do this too)">
            <select className="select" value="" onChange={(e) => e.target.value && onLoadRun(e.target.value)} data-testid="gym-load-run">
              <option value="">choose a run…</option>
              {runs.map((r) => <option key={r.id} value={r.id}>{r.id} · {r.name}</option>)}
            </select>
          </Row>
        </div>
      </div>
      {ds && (
        <div className="card"><div className="card-head"><h3 className="card-title">Columns</h3><span className="helper" style={{ fontSize: 12 }}>{ds.profile.rows.toLocaleString()} × {ds.profile.n_cols} · untick = <span className="mono">drop_columns</span></span></div>
          <div className="table-wrap"><table className="table" data-testid="gym-columns">
            <thead><tr><th style={{ width: 28 }} /><th>column</th><th>type</th><th className="r">unique</th><th className="r">missing</th><th>example</th></tr></thead>
            <tbody>{ds.profile.columns.map((c) => {
              const isLabel = c.name === body.label
              return (
                <tr key={c.name} className={drop.has(c.name) ? 'muted-row' : ''}>
                  <td><input type="checkbox" disabled={isLabel} checked={isLabel || !drop.has(c.name)} onChange={() => toggle(c.name)} aria-label={`Use ${c.name}`} data-testid={`gym-col-${c.name}`} style={{ accentColor: 'var(--accent-primary)' }} /></td>
                  <td className="mono">{c.name}{isLabel && <span className="pill info" style={{ marginLeft: 6 }}>label</span>}</td>
                  <td>{c.type}</td><td className="r mono">{c.unique.toLocaleString()}</td><td className="r mono">{(c.missing_share * 100).toFixed(1)}%</td>
                  <td className="muted ellipsis" style={{ maxWidth: 220 }}>{c.example}</td>
                </tr>
              )
            })}</tbody>
          </table></div>
        </div>
      )}
      {void setBody}
    </div>
  )
}

// -- 02 Target ----------------------------------------------------------------------------------------------------

export function TargetStep({ body, set, ds, v, guided }: StepProps) {
  const cols = ds?.profile.columns ?? []
  const numeric = cols.filter((c) => c.type === 'number' && c.name !== body.label)
  const sw = getPath(body, 'predictor_kwargs.sample_weight') as string | undefined
  return (
    <div className="card"><div className="card-head"><h2 className="card-title">What to predict</h2>{v?.problem_type && <span className="chip">inferred: {v.problem_type}</span>}</div>
      <div className="card-body">
        <Row label="Label" sub="the column AutoGluon learns to predict (TabularPredictor label)">
          <select className="select" value={body.label ?? ''} onChange={(e) => set('label', e.target.value)} data-testid="gym-label">
            {cols.map((c) => <option key={c.name} value={c.name}>{c.name} · {c.type}</option>)}
          </select>
        </Row>
        <Row label="Problem type" sub="auto lets AutoGluon infer it from the label">
          <Seg testid="gym-problem-type" value={(body.problem_type ?? 'auto') as string} onChange={(x) => set('problem_type', x)}
            options={['auto', 'binary', 'multiclass', 'regression', 'quantile'].map((x) => ({ v: x, label: x }))} />
        </Row>
        {!guided && (
          <Row label={<>Sample weight <span className="math">wᵢ</span></>} sub={<>each row's weight in the loss Σᵢ wᵢ·ℓ(yᵢ, f(xᵢ)); <span className="mono">balance_weight</span>: wᵢ ∝ n / (K·n<sub>yᵢ</sub>)</>} testid="gym-sample-weight">
            <select className="select" value={sw ?? ''} onChange={(e) => set('predictor_kwargs.sample_weight', e.target.value || undefined)}>
              <option value="">none (wᵢ = 1)</option><option value="balance_weight">balance_weight</option><option value="auto_weight">auto_weight</option>
              {numeric.map((c) => <option key={c.name} value={c.name}>column: {c.name}</option>)}
            </select>
          </Row>
        )}
      </div>
    </div>
  )
}

// -- 03 Goal ------------------------------------------------------------------------------------------------------

export function GoalStep({ body, set, schema, v }: StepProps) {
  const pt = v?.problem_type ?? 'binary'
  const metrics = schema.metrics[pt] ?? []
  const preset = (Array.isArray(body.presets) ? body.presets[0] : body.presets) ?? 'medium_quality'
  const tau = Number(body.time_limit ?? 120)
  return (
    <div className="stack">
      <div className="card"><div className="card-head"><h2 className="card-title">Goal</h2><span className="helper" style={{ fontSize: 12 }}>metric, quality preset and the time budget τ</span></div>
        <div className="card-body">
          <Row label="Metric" sub="AutoGluon's eval_metric; models and the ensemble are chosen on it">
            <select className="select" value={body.eval_metric ?? ''} onChange={(e) => set('eval_metric', e.target.value || null)} data-testid="gym-metric">
              <option value="">auto ({pt === 'regression' ? 'root_mean_squared_error' : 'accuracy'})</option>
              {metrics.map((m) => <option key={m} value={m}>{m}</option>)}
            </select>
          </Row>
          <div className="gym-presets" role="radiogroup" aria-label="Preset" data-testid="gym-presets">
            {schema.presets.map((p, i) => {
              const pv = schema.preset_values[p.key] ?? {}
              return (
                <button key={p.key} role="radio" aria-checked={p.key === preset} className={`preset-card ${p.key === preset ? 'on' : ''}`} onClick={() => set('presets', p.key)} data-testid={`gym-preset-${p.key}`}>
                  <span className="pk"><span className="idx mono">{String(i + 1).padStart(2, '0')}</span><b>{p.name}</b></span>
                  <span className="pn">{p.note}</span>
                  <span className="pv mono">{Object.entries(pv).slice(0, 3).map(([k, x]) => `${k}=${typeof x === 'string' ? x : JSON.stringify(x)}`).join(' · ') || '—'}</span>
                </button>
              )
            })}
          </div>
          <FretSlider guided={false} sym="τ" name="Time budget" help="AutoGluon stops adding models when it runs out." min={10} max={3600} log value={tau}
            onChange={(x) => set('time_limit', Math.round(x))} ticks={[10, 60, 300, 900, 3600]} tickLabel={(x) => (x >= 60 ? `${x / 60}m` : `${x}s`)}
            format={(x) => String(Math.round(x))} domain={<>τ ∈ [10, 3600] s</>} />
        </div>
      </div>
      {pt === 'binary' && (
        <div className="card"><div className="card-head"><h3 className="card-title">Decision threshold</h3><span className="helper" style={{ fontSize: 12 }}>predict positive when <span className="math">p̂ &gt; t</span>; calibrated on validation data after the fit</span></div>
          <div className="card-body">
            <Row label={<>Calibrate <span className="math">t</span> for</>} sub="auto = F₁ when the minority class is under 20% of rows" testid="gym-threshold">
              <Seg value={(body.calibrate_threshold ?? null) as string | null} onChange={(x) => set('calibrate_threshold', x)}
                options={[{ v: null, label: 'off (t = 0.5)' }, { v: 'auto', label: 'auto' }, ...schema.calibration_metrics.slice(0, 3).map((m) => ({ v: m, label: m }))]} />
            </Row>
          </div>
        </div>
      )}
    </div>
  )
}

// -- 04 Models ----------------------------------------------------------------------------------------------------

export function ModelsStep(props: StepProps) {
  return <>
    <ModelFamilies {...props} />
    {props.v?.problem_type === 'binary' && <ScorecardCard {...props} />}
    {!props.guided && <MonotoneCard {...props} />}
  </>
}

/** F11: the scorecard challenger (binary labels): on by default as the interpretable benchmark. Guided: on / off.
 * Advanced: its points scaling and binning, written to the body's `scorecard`. */
function ScorecardCard({ body, setBody, v, guided }: StepProps) {
  const raw = body.scorecard as boolean | Record<string, number | string> | null | undefined
  const on = raw !== false
  const p = (v?.scorecard || null) as Record<string, number | string> | null
  const val = (k: string, d: number) => Number((typeof raw === 'object' && raw && raw[k] != null ? raw[k] : p?.[k]) ?? d)
  const put = (k: string, x: number) => setBody({ ...body, scorecard: { ...(typeof raw === 'object' && raw ? raw : {}), [k]: x } })
  const issues = (v?.errors ?? []).filter((e) => e.path === 'scorecard')
  const pdo = val('pdo', 20), base = val('base_score', 600), odds = val('base_odds', 50)
  const factor = pdo / Math.LN2
  return (
    <div className="grid hp-grid" data-testid="gym-scorecard">
      <div className="card"><div className="card-head">
        <div className="left"><h2 className="card-title">Scorecard challenger</h2><span className="helper" style={{ fontSize: 12 }}><span className="mono">scorecard</span> · SCORECARD, trained beside the other models as the interpretable benchmark</span></div>
        <span className="chip">{on ? 'on' : 'off'}</span>
      </div>
        <div className="card-body">
          <Row label="Train the scorecard" sub="binning → weight of evidence → logistic regression → points; Explain → Scorecard shows its table and ΔAUC" testid="gym-scorecard-on">
            <Seg value={on} onChange={(x) => setBody({ ...body, scorecard: x ? null : false })} options={[{ v: true, label: 'on' }, { v: false, label: 'off' }]} />
          </Row>
          {on && !guided && <>
            <Row label={<>Points to double the odds (PDO)</>} sub="every PDO points doubles the odds of a non-event" testid="gym-scorecard-pdo">
              <NumberInput value={pdo} min={1} max={200} step={1} onChange={(x) => put('pdo', x)} label="PDO" /></Row>
            <Row label="Base score" sub="the score at the base odds" testid="gym-scorecard-base">
              <NumberInput value={base} min={0} max={2000} step={1} onChange={(x) => put('base_score', x)} label="Base score" /></Row>
            <Row label="Base odds (non-event : event)" sub="e.g. 50 for 50 : 1" testid="gym-scorecard-odds">
              <NumberInput value={odds} min={1} max={1000} step={1} onChange={(x) => put('base_odds', x)} label="Base odds" /></Row>
            <Row label="Max bins per feature" sub="adjacent bins are merged until the event rate is monotone and each holds ≥ the minimum share" testid="gym-scorecard-bins">
              <NumberInput value={val('max_bins', 6)} min={2} max={20} step={1} onChange={(x) => put('max_bins', x)} label="Max bins" /></Row>
            <Row label="Min share per bin" sub="of the training rows">
              <NumberInput value={val('min_bin_share', 0.05)} min={0.01} max={0.45} step={0.01} onChange={(x) => put('min_bin_share', x)} label="Min share per bin" /></Row>
            <Row label="Min IV to enter" sub="features with a lower information value are left out">
              <NumberInput value={val('min_iv', 0.02)} min={0} max={0.5} step={0.005} onChange={(x) => put('min_iv', x)} label="Min IV" /></Row>
          </>}
          {issues.map((e) => <p key={e.message} className="helper" style={{ fontSize: 12, color: 'var(--status-danger)' }}>✕ {e.message}</p>)}
        </div>
      </div>
      <div className="formula">
        <div className="micro-label">Points</div>
        <div className="eq">score = <i>offset</i> + <i>factor</i> · ln odds<sub>non-event</sub></div>
        <dl className="where">
          <dt>factor</dt><dd>PDO ⁄ ln 2 = {fmtNum(pdo)} ⁄ 0.6931 = <b>{factor.toFixed(3)}</b></dd>
          <dt>offset</dt><dd>base score − factor · ln base odds = {fmtNum(base)} − {factor.toFixed(3)} · ln {fmtNum(odds)} = <b>{(base - factor * Math.log(odds)).toFixed(3)}</b></dd>
          <dt>odds<sub>non-event</sub></dt><dd>(1 − P(event)) ⁄ P(event) from the logistic regression on the WoE columns</dd>
        </dl>
        <p className="helper" style={{ fontSize: 12, marginTop: 8 }}>Bins and WoE are fitted on each fold's training rows only. A feature with a monotone constraint is binned in that direction and keeps a coefficient ≤ 0, so the scorecard honours it by construction.</p>
      </div>
    </div>
  )
}

/** F8 (Advanced): ↑ / ↓ / none per numeric feature, written to the body's `monotone`. The API swaps LightGBM, XGBoost
 * and CatBoost for subclasses that map each sign onto the columns AutoGluon trains them on; the validation says which
 * families honour it, which are left out or kept, and how stacking stays monotone. */
function MonotoneCard({ body, setBody, v, ds }: StepProps) {
  const mono = (body.monotone ?? null) as { constraints?: Record<string, unknown>; compliant_only?: boolean } | null
  const cons = mono?.constraints ?? {}
  const sgn = (x: unknown) => (x === 'increasing' || x === 1 ? 1 : x === 'decreasing' || x === -1 ? -1 : 0)
  const label = body.label ?? v?.label
  const dropped = new Set(v?.data?.dropped ?? body.drop_columns ?? [])
  const types = (body.column_types ?? {}) as Record<string, string>
  const numeric = (ds?.profile.columns ?? []).filter((c) => c.name !== label && !dropped.has(c.name) && (types[c.name] ?? c.type) === 'number')
  const pt = v?.problem_type
  const ok = pt === 'binary' || pt === 'regression'
  const write = (next: Record<string, unknown>, co = mono?.compliant_only ?? true) => {
    const kept = Object.fromEntries(Object.entries(next).filter(([, x]) => sgn(x) !== 0))
    setBody({ ...body, monotone: Object.keys(kept).length ? { constraints: kept as Record<string, 'increasing' | 'decreasing'>, compliant_only: co } : null })
  }
  const vm = v?.monotone
  const issues = [...(v?.errors ?? []), ...(v?.warnings ?? []), ...(v?.info ?? [])].filter((e) => e.path === 'monotone' && !e.message.startsWith('Left out'))  // left out: shown on the line above
  const n = Object.keys(cons).length
  const score = pt === 'regression' ? 'ŷ(x)' : 'p̂(x)'
  return (
    <div className="grid hp-grid" data-testid="gym-monotone">
      <div className="card"><div className="card-head">
        <div className="left"><h2 className="card-title">Monotone constraints</h2><span className="helper" style={{ fontSize: 12 }}><span className="mono">monotone.constraints</span> · numeric features of binary and regression labels</span></div>
        <span className="chip" data-testid="gym-monotone-count">{n ? `${n} constrained` : 'none'}</span>
      </div>
        <div className="card-body">
          {!ok && pt && <p className="helper" style={{ fontSize: 12 }}>▲ This label is {pt}: constraints apply to binary and regression only (for multiclass, the softmax couples the classes, so no class probability is monotone by construction).</p>}
          <div className="mono-list">
            {numeric.map((c) => (
              <Row key={c.name} label={<span className="mono">{c.name}</span>} sub={`${c.unique.toLocaleString()} distinct values`} testid={`gym-mono-${c.name}`}>
                <Seg value={sgn(cons[c.name])} label={`Constraint on ${c.name}`} onChange={(x) => write({ ...cons, [c.name]: x === 1 ? 'increasing' : x === -1 ? 'decreasing' : 0 })}
                  options={[{ v: 1, label: '↑ increasing' }, { v: 0, label: 'none' }, { v: -1, label: '↓ decreasing' }]} />
              </Row>
            ))}
            {!numeric.length && <p className="helper">No numeric feature to constrain.</p>}
          </div>
          {n > 0 && <Row label="Compliant families only" sub="leave out the families that cannot honour a constraint (RF, XT, KNN, LR, NN, TF-IDF); off keeps them and reports which models break it" testid="gym-mono-compliant">
            <Seg value={mono?.compliant_only ?? true} onChange={(x) => write(cons, x)} options={[{ v: true, label: 'on' }, { v: false, label: 'off' }]} />
          </Row>}
          {vm && <p className="helper" style={{ fontSize: 12 }} data-testid="gym-mono-families">Honour it: <b>{vm.compliant.map((k) => (k === 'SCORECARD' ? vm.families[k] ?? k : `${vm.families[k] ?? k} (${k}_MONO)`)).join(', ') || 'none'}</b>
            {vm.left_out.length ? <> · left out: <span className="mono">{vm.left_out.join(', ')}</span></> : null}
            {vm.unconstrained.length ? <> · <span style={{ color: 'var(--status-warning)' }}>kept unconstrained: <span className="mono">{vm.unconstrained.join(', ')}</span></span></> : null}</p>}
          {issues.map((e) => <p key={e.message} className="helper" style={{ fontSize: 12, color: v?.errors.includes(e) ? 'var(--status-danger)' : v?.warnings.includes(e) ? 'var(--status-warning)' : undefined }}>{v?.errors.includes(e) ? '✕' : v?.warnings.includes(e) ? '▲' : '·'} {e.message}</p>)}
        </div>
      </div>
      <div className="formula">
        <div className="micro-label">Monotone in x<sub>j</sub></div>
        <div className="eq"><i>s<sub>j</sub></i> · (<i>f</i>(<i>x</i><sub>−<i>j</i></sub>, <i>b</i>) − <i>f</i>(<i>x</i><sub>−<i>j</i></sub>, <i>a</i>)) ≥ 0  for all <i>a</i> &lt; <i>b</i> and every <i>x</i><sub>−<i>j</i></sub></div>
        <div className="eq"><i>f</i><sup>(2)</sup>(<i>x</i>) = <i>g</i>(<i>x</i>, <i>f̂</i><sub>1</sub>(<i>x</i>), …, <i>f̂</i><sub><i>M</i></sub>(<i>x</i>)),  ∂<i>g</i>/∂<i>f̂<sub>m</sub></i> ≥ 0,  <i>ŷ</i> = Σ<sub><i>m</i></sub> <i>w<sub>m</sub></i> <i>f̂<sub>m</sub></i>, <i>w<sub>m</sub></i> ≥ 0</div>
        <dl className="where">
          <dt>f</dt><dd>the served score {score}{pt === 'binary' ? ' = P(positive class | x)' : ''}</dd>
          <dt>s<sub>j</sub></dt><dd>+1 (↑ increasing) or −1 (↓ decreasing) for feature j: {n ? Object.entries(cons).map(([k, x]) => `${k} ${sgn(x) > 0 ? '+1' : '−1'}`).join(', ') : 'none set'}</dd>
          <dt>x<sub>−j</sub></dt><dd>every other feature, held fixed</dd>
          <dt>g</dt><dd>a stack-level model: it sees x and the base models' out-of-fold predictions f̂<sub>m</sub>; those inputs are constrained to +1, so if every f̂<sub>m</sub> is monotone with sign s<sub>j</sub>, so is g</dd>
          <dt>w<sub>m</sub></dt><dd>the weighted ensemble's weights; non-negative, so it keeps the direction</dd>
        </dl>
        <p className="helper" style={{ fontSize: 12, marginTop: 8 }}>Each model maps s<sub>j</sub> onto the columns AutoGluon actually gives it (columns it drops, renames or one-hot encodes), and after the fit every model is checked with ICE curves on raw holdout rows (Explain → Monotonicity).</p>
      </div>
    </div>
  )
}

function ModelFamilies({ body, setBody, schema, v }: StepProps) {
  const { hp, source, portfolio } = effectiveHp(body, schema)
  const pt = v?.problem_type
  const excluded = new Set((getPath(body, 'fit_kwargs.excluded_model_types') as string[] | undefined) ?? [])
  return (
    <div className="card"><div className="card-head">
      <div className="left"><h2 className="card-title">Model families</h2><span className="helper" style={{ fontSize: 12 }}>
        {source === 'yours' ? <>your <span className="mono">hyperparameters</span> dict</> : hp ? <>the preset's zoo (<span className="mono">'{portfolio}'</span>); switching a family writes it out explicitly</> : <>the preset uses the <span className="mono">'{portfolio}'</span> portfolio; switching a family replaces it with an explicit dict</>}
      </span></div>
      <span className="chip" data-testid="gym-family-count">{Object.keys(hp ?? {}).length || 'portfolio'} on</span>
    </div>
      <div className="card-body fam-grid">
        {schema.families.map((f) => {
          const on = hp ? f.key in hp : !f.custom
          const n = hp && f.key in hp ? variants(hp[f.key]).length : 0
          const unsupported = pt && f.problem_types && !f.problem_types.includes(pt)
          return (
            <label key={f.key} className={`fam ${on ? 'on' : ''} ${unsupported ? 'na' : ''}`} data-testid={`gym-family-${f.key}`}>
              <span className="fh"><input type="checkbox" checked={on} onChange={(e) => setBody(toggleFamily(body, schema, f.key, e.target.checked))} aria-label={`Use ${f.name}`} />
                <b className="mono">{f.key}</b><span className="fk">{f.kind}</span>{n > 1 && <span className="chip">{n} configs</span>}{f.custom && <span className="pill info">ENSEMBLE</span>}</span>
              <span className="fn">{f.name}{excluded.has(f.key) && <StatusPill status="warning">excluded</StatusPill>}</span>
              <span className="fo"><MathText text={f.objective} /></span>
              {unsupported && <span className="helper" style={{ fontSize: 11 }}>not for {pt}: AutoGluon skips it</span>}
            </label>
          )
        })}
      </div>
    </div>
  )
}

// -- 05 Hyperparameters -------------------------------------------------------------------------------------------

export function HyperStep({ body, setBody, schema }: StepProps) {
  const { hp } = effectiveHp(body, schema)
  const fams = schema.families.filter((f) => (hp ? f.key in hp : f.in_default_zoo))
  const [fam, setFam] = useState(fams[0]?.key ?? 'GBM')
  const [variant, setVariant] = useState(0)
  const f = schema.families.find((x) => x.key === fam) ?? schema.families[0]
  const list = variants(hp?.[f.key])
  const cfg = list[Math.min(variant, list.length - 1)] ?? {}
  const bound: Record<string, string> = {}
  f.params.forEach((p) => { const x = cfg[p.key] ?? p.default; bound[p.sym] = `${p.key} = ${typeof x === 'number' ? fmtNum(x) : String(x)}` })
  return (
    <div className="grid hp-grid">
      <FamilyFormula fam={f} values={bound} />
      <div className="card"><div className="card-head">
        <div className="left"><h2 className="card-title">Hyperparameters</h2><span className="helper" style={{ fontSize: 12 }}>per family, written into <span className="mono">fit_kwargs.hyperparameters</span></span></div>
      </div>
        <div className="fam-tabs" role="tablist">{fams.map((x) => <button key={x.key} role="tab" aria-selected={x.key === fam} className={x.key === fam ? 'on' : ''} onClick={() => { setFam(x.key); setVariant(0) }} data-testid={`gym-hp-tab-${x.key}`}>{x.key}</button>)}</div>
        <div className="card-body" style={{ padding: 'var(--space-16) var(--space-20)' }}>
          {list.length > 1 && <div className="chips-row" style={{ marginBottom: 8 }}><span className="micro-label">config</span>{list.map((c, i) => <button key={i} className={`chip-btn ${i === variant ? 'on' : ''}`} onClick={() => setVariant(i)}>{i + 1}{(c.ag_args as { name_suffix?: string } | undefined)?.name_suffix ? ` · ${(c.ag_args as { name_suffix: string }).name_suffix}` : ''}</button>)}</div>}
          {f.params.map((p) => {
            const cur = cfg[p.key]
            const isSet = cur !== undefined
            if (p.choices) return (
              <div key={p.key} className="param" data-testid={`gym-hp-${f.key}-${p.key}`}>
                <div className="label"><b><span className="math">{p.sym}</span> <span className="micro-label">{p.name}</span></b><small>{p.help}</small></div>
                <div className="row"><Seg value={(cur ?? p.default) as string} onChange={(x) => setBody(setHp(body, schema, f.key, variant, p.key, x === p.default ? undefined : x))} options={p.choices.map((c) => ({ v: c as string, label: String(c) }))} /></div>
                <span />
              </div>
            )
            const val = typeof cur === 'number' ? cur : Number(p.default)
            return (
              <div key={p.key} className={`hp-slider ${isSet ? 'set' : ''}`} data-testid={`gym-hp-${f.key}-${p.key}`}>
                <FretSlider guided={false} sym={p.sym} name={p.name} help={p.help} min={p.min!} max={p.max!} log={p.log} step={p.integer ? 1 : undefined}
                  value={Math.min(p.max!, Math.max(p.min!, val))} onChange={(x) => setBody(setHp(body, schema, f.key, variant, p.key, p.integer ? Math.round(x) : x))}
                  ticks={p.log ? logTicks(p.min!, p.max!) : linTicks(p.min!, p.max!, p.integer)} tickLabel={p.log ? pow10 : (x) => fmtNum(x)} format={fmtNum}
                  domain={<>{p.sym} ∈ [{p.log ? pow10(p.min!) : fmtNum(p.min!)}, {p.log ? pow10(p.max!) : fmtNum(p.max!)}]{p.log ? ' log' : ''}{p.integer ? ', ℤ' : ''} · <span className="mono">{p.key}</span></>} />
                <div className="hp-meta">
                  {isSet ? <button className="chip-btn" onClick={() => setBody(setHp(body, schema, f.key, variant, p.key, undefined))} title="Remove the key: AutoGluon's default applies">reset → {fmtNum(Number(p.default))}</button>
                    : <span className="muted">default {fmtNum(Number(p.default))}{p.default_source === 'autogluon' ? ' (AutoGluon)' : ''}</span>}
                </div>
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}

// -- 06 Bagging & stacking ------------------------------------------------------------------------------------------

export function BaggingStep({ body, set, v }: StepProps) {
  const k = getPath(body, 'fit_kwargs.num_bag_folds') as number | undefined
  const s = getPath(body, 'fit_kwargs.num_bag_sets') as number | undefined
  const L = getPath(body, 'fit_kwargs.num_stack_levels') as number | undefined
  const dyn = getPath(body, 'fit_kwargs.dynamic_stacking') as boolean | string | undefined
  const hf = getPath(body, 'fit_kwargs.holdout_frac') as number | undefined
  const e = v?.effective
  const opt = (path: string, cur: number | undefined, label: string, min: number, max: number, sym: string, help: string, testid: string, def: number) => (
    <div className={`hp-slider ${cur !== undefined ? 'set' : ''}`} data-testid={testid}>
      <FretSlider guided={false} sym={sym} name={label} help={help} min={min} max={max} step={1} value={cur ?? def} onChange={(x) => set(path, Math.round(x))}
        ticks={Array.from({ length: max - min + 1 }, (_, i) => min + i).filter((x, i, a) => a.length <= 11 || i % 2 === 0)} domain={<>{sym} ∈ {'{'}{min}, …, {max}{'}'} · <span className="mono">{path.split('.')[1]}</span></>} />
      <div className="hp-meta">{cur !== undefined ? <button className="chip-btn" onClick={() => set(path, undefined)}>auto</button> : <span className="muted">auto → {def}</span>}</div>
    </div>
  )
  return (
    <div className="grid hp-grid">
      <div className="card"><div className="card-head"><h2 className="card-title">Bagging & stacking</h2>{e && <span className="chip">AutoGluon will use k={e.num_bag_folds} · s={e.num_bag_sets} · L={e.num_stack_levels}</span>}</div>
        <div className="card-body" style={{ padding: 'var(--space-16) var(--space-20)' }}>
          {opt('fit_kwargs.num_bag_folds', k, 'Bag folds', 0, 10, 'k', 'k-fold bagging: k models per config, each validated out-of-fold (0 = no bagging; 1 is invalid).', 'gym-k', e?.num_bag_folds ?? 0)}
          {opt('fit_kwargs.num_bag_sets', s, 'Bag sets', 1, 5, 's', 'Repeat the k-fold split s times with new shuffles and average.', 'gym-s', e?.num_bag_sets ?? 1)}
          {opt('fit_kwargs.num_stack_levels', L, 'Stack levels', 0, 3, 'L', 'Levels of models trained on the previous level’s out-of-fold predictions (needs k ≥ 2).', 'gym-L', e?.num_stack_levels ?? 0)}
          <Row label="dynamic_stacking" sub="hold out part of the data to check stacking does not overfit before committing to it" testid="gym-dynamic">
            <Seg value={(dyn ?? null) as string | boolean | null} onChange={(x) => set('fit_kwargs.dynamic_stacking', x ?? undefined)} options={[{ v: null, label: 'preset' }, { v: 'auto', label: 'auto' }, { v: true, label: 'on' }, { v: false, label: 'off' }]} />
          </Row>
          <div className={`hp-slider ${hf !== undefined ? 'set' : ''}`} data-testid="gym-holdout-frac">
            <FretSlider guided={false} sym="h" name="Holdout fraction" help="Without bagging: the share of training rows AutoGluon holds out to tune and ensemble." min={0.05} max={0.5}
              value={hf ?? Number(e?.holdout_frac ?? 0.2)} onChange={(x) => set('fit_kwargs.holdout_frac', Number(x.toFixed(3)))} ticks={[0.05, 0.15, 0.25, 0.35, 0.5]} format={(x) => x.toFixed(2)}
              domain={<>h ∈ (0, 0.5] · <span className="mono">holdout_frac</span></>} />
            <div className="hp-meta">{hf !== undefined ? <button className="chip-btn" onClick={() => set('fit_kwargs.holdout_frac', undefined)}>auto</button> : <span className="muted">auto</span>}</div>
          </div>
        </div>
      </div>
      <div className="formula">
        <div className="micro-label">Bagged, stacked ensemble</div>
        <div className="eq"><i>f̂</i><sub><i>m</i></sub>(<i>x</i>) = <span className="up">1</span>⁄<i>ks</i> <span className="up">Σ</span><sub><i>r</i>=1</sub><sup><i>s</i></sup> <span className="up">Σ</span><sub><i>j</i>=1</sub><sup><i>k</i></sup> <i>f</i><sub><i>m,r,j</i></sub>(<i>x</i>)</div>
        <div className="eq"><i>z</i><sup>(ℓ)</sup>(<i>x</i>) = [<i>x</i>, <i>f̂</i><sub>1</sub><sup>(ℓ−1)</sup>(<i>x</i>), …, <i>f̂</i><sub><i>M</i></sub><sup>(ℓ−1)</sup>(<i>x</i>)],  <i>ŷ</i> = <span className="up">Σ</span><sub><i>m</i></sub> <i>w<sub>m</sub></i> <i>f̂</i><sub><i>m</i></sub><sup>(<i>L</i>+1)</sup>(<i>x</i>)</div>
        <dl className="where">
          <dt>k, s</dt><dd>folds and repeats: {e ? <b className="mono">k = {e.num_bag_folds}, s = {e.num_bag_sets}</b> : '—'}{e && e.num_bag_folds ? ` → ${e.num_bag_folds * e.num_bag_sets} fits per config` : ''}</dd>
          <dt>f<sub>m,r,j</sub></dt><dd>config m fit on all folds but j of repeat r; it predicts fold j out-of-fold</dd>
          <dt>z<sup>(ℓ)</sup></dt><dd>level ℓ's inputs: the features plus level ℓ−1's out-of-fold predictions</dd>
          <dt>L</dt><dd>stack levels above the base level: {e ? <b className="mono">L = {e.num_stack_levels}</b> : '—'}{e?.dynamic_stacking ? ' (dynamic: checked on a held-out slice first)' : ''}</dd>
          <dt>w<sub>m</sub></dt><dd>the final weighted ensemble's weights (Caruana selection, Σ w = 1)</dd>
        </dl>
      </div>
    </div>
  )
}

// -- 07 Search ----------------------------------------------------------------------------------------------------

export function SearchStep({ body, set, schema }: StepProps) {
  const h = getPath(body, 'fit_kwargs.hyperparameter_tune_kwargs') as Record<string, unknown> | string | undefined
  const on = h != null
  const obj = (typeof h === 'object' && h) || { scheduler: 'local', searcher: typeof h === 'string' ? h : 'auto', num_trials: 10 }
  const upd = (k: string, x: unknown) => set('fit_kwargs.hyperparameter_tune_kwargs', { ...obj, [k]: x })
  return (
    <div className="grid hp-grid">
      <div className="card"><div className="card-head"><h2 className="card-title">Hyperparameter search</h2><span className="helper" style={{ fontSize: 12 }}><span className="mono">hyperparameter_tune_kwargs</span></span></div>
        <div className="card-body">
          <Row label="HPO" sub="tune each family inside its share of τ" testid="gym-hpo">
            <Seg value={on} onChange={(x) => set('fit_kwargs.hyperparameter_tune_kwargs', x ? { scheduler: 'local', searcher: 'auto', num_trials: 10 } : undefined)} options={[{ v: false, label: 'off' }, { v: true, label: 'on' }]} />
          </Row>
          {on && <>
            <Row label="scheduler" sub="local: trials run in this machine's process pool (no Ray cluster)"><Seg value={String(obj.scheduler ?? 'local')} onChange={(x) => upd('scheduler', x)} options={schema.hpo.schedulers.map((x) => ({ v: x, label: x }))} /></Row>
            <Row label="searcher" sub="auto picks per family; bayes fits a Gaussian process to past trials" testid="gym-searcher"><Seg value={String(obj.searcher ?? 'auto')} onChange={(x) => upd('searcher', x)} options={schema.hpo.searchers.map((x) => ({ v: x, label: x }))} /></Row>
            <div data-testid="gym-trials"><FretSlider guided={false} sym="N" name="Trials" help="Configurations tried per family." min={1} max={100} step={1} value={Number(obj.num_trials ?? 10)} onChange={(x) => upd('num_trials', Math.round(x))} ticks={[1, 25, 50, 75, 100]} domain={<>N ∈ {'{'}1, …, 100{'}'} · <span className="mono">num_trials</span></>} /></div>
          </>}
        </div>
      </div>
      <div className="formula">
        <div className="micro-label">Search</div>
        <div className="eq"><i>θ</i>* = arg max<sub><i>θ</i> ∈ {'{'}<i>θ</i><sub>1</sub>, …, <i>θ<sub>N</sub></i>{'}'}</sub> <i>s</i><sub>val</sub>(<i>f<sub>θ</sub></i>),  <i>θ<sub>t</sub></i> ~ <i>q</i>(<i>θ</i> | <i>θ</i><sub>&lt;<i>t</i></sub>)</div>
        <dl className="where">
          <dt>θ</dt><dd>one family's hyperparameters, drawn from its search space (a slider's interval, when you give one as a space)</dd>
          <dt>N</dt><dd>trials per family: <b className="mono">{on ? String(obj.num_trials ?? 10) : 'off'}</b></dd>
          <dt>q</dt><dd>the searcher: uniform for random, a Gaussian-process posterior for bayes</dd>
          <dt>s<sub>val</sub></dt><dd>the validation score on eval_metric</dd>
        </dl>
        <p className="helper" style={{ fontSize: 12, marginTop: 8 }}>{schema.hpo.note}</p>
      </div>
    </div>
  )
}

// -- 08 Extra data ------------------------------------------------------------------------------------------------

export function ExtraDataStep({ body, set }: StepProps) {
  const pl = body.pseudolabel ?? null
  const dist = body.distill ?? null
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [name, setName] = useState('')
  const upload = async (f: File) => {
    setBusy(true); setErr('')
    try { const d = await api.datasets.upload(f); setName(`${d.name} · ${d.rows.toLocaleString()} rows`); set('pseudolabel', { dataset_id: d.id, method: pl?.method ?? 'fit_pseudolabel', max_iter: pl?.max_iter ?? 3 }) } catch (e) { setErr((e as Error).message) } finally { setBusy(false) }
  }
  return (
    <div className="grid hp-grid">
      <div className="stack">
        <div className="card"><div className="card-head"><h2 className="card-title">Pseudo-labelling</h2><span className="helper" style={{ fontSize: 12 }}>unlabeled rows the model labels for itself</span></div>
          <div className="card-body">
            <Row label="Unlabeled rows" sub="a CSV with the feature columns (a label column, if present, is dropped)" testid="gym-unlabeled">
              <label className="btn sm">{busy ? 'Uploading…' : pl ? 'Replace CSV' : 'Upload CSV'}<input type="file" accept=".csv" hidden onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} data-testid="gym-unlabeled-file" /></label>
              {pl && <span className="chip" data-testid="gym-unlabeled-id">{name || pl.dataset_id}</span>}
              {pl && <button className="chip-btn" onClick={() => set('pseudolabel', null)}>remove</button>}
            </Row>
            {err && <p className="helper" style={{ color: 'var(--status-danger)' }}>✕ {err}</p>}
            {pl && <>
              <Row label="Method" sub="fit_pseudolabel: refit with confident pseudo-labels after the fit · unlabeled_data: passed to fit()"><Seg value={pl.method ?? 'fit_pseudolabel'} onChange={(x) => set('pseudolabel.method', x)} options={[{ v: 'fit_pseudolabel', label: 'fit_pseudolabel' }, { v: 'unlabeled_data', label: 'unlabeled_data' }]} /></Row>
              {(pl.method ?? 'fit_pseudolabel') === 'fit_pseudolabel' && <FretSlider guided={false} sym="I" name="Iterations" help="Rounds of label → refit." min={1} max={5} step={1} value={pl.max_iter ?? 3} onChange={(x) => set('pseudolabel.max_iter', Math.round(x))} ticks={[1, 2, 3, 4, 5]} domain={<>I ∈ {'{'}1, …, 5{'}'} · <span className="mono">max_iter</span></>} />}
            </>}
          </div>
        </div>
        <div className="card"><div className="card-head"><h2 className="card-title">Distillation</h2><span className="helper" style={{ fontSize: 12 }}><span className="mono">predictor.distill(…)</span> after the fit</span></div>
          <div className="card-body">
            <Row label="Distil" sub="train small student models on the ensemble's soft predictions over augmented data" testid="gym-distill">
              <Seg value={!!dist} onChange={(x) => set('distill', x ? { time_limit: Math.max(30, Math.round(Number(body.time_limit ?? 120) / 2)), teacher_preds: 'soft', augment_method: 'spunge' } : null)} options={[{ v: false, label: 'off' }, { v: true, label: 'on' }]} />
            </Row>
            {dist && <>
              <Row label="teacher_preds"><Seg value={String(dist.teacher_preds ?? 'soft')} onChange={(x) => set('distill.teacher_preds', x)} options={[{ v: 'soft', label: 'soft' }, { v: 'hard', label: 'hard' }]} /></Row>
              <Row label="augment_method"><Seg value={String(dist.augment_method ?? 'spunge')} onChange={(x) => set('distill.augment_method', x)} options={[{ v: 'spunge', label: 'spunge' }, { v: 'munge', label: 'munge' }]} /></Row>
              <FretSlider guided={false} sym="τ_d" name="Distil budget" help="Seconds for the students." min={10} max={1800} log value={Number(dist.time_limit ?? 60)} onChange={(x) => set('distill.time_limit', Math.round(x))} ticks={[10, 60, 300, 1800]} format={(x) => String(Math.round(x))} domain={<>τ<sub>d</sub> ∈ [10, 1800] s</>} />
            </>}
          </div>
        </div>
      </div>
      <div className="formula">
        <div className="micro-label">Semi-supervised and distilled</div>
        <div className="eq"><i>ỹ<sub>u</sub></i> = arg max<sub><i>c</i></sub> <i>p̂</i>(<i>c</i> | <i>x<sub>u</sub></i>),  keep <i>u</i> if max<sub><i>c</i></sub> <i>p̂</i>(<i>c</i> | <i>x<sub>u</sub></i>) ≥ <i>q</i></div>
        <div className="eq"><span className="up">min</span><sub><i>g</i></sub> <span className="up">Σ</span><sub><i>i</i> ∈ 𝒜</sub> ℓ(<i>p̂</i><sub>teacher</sub>(<i>x<sub>i</sub></i>), <i>g</i>(<i>x<sub>i</sub></i>))</div>
        <dl className="where">
          <dt>x<sub>u</sub>, ỹ<sub>u</sub></dt><dd>an unlabeled row and its pseudo-label</dd>
          <dt>q</dt><dd>AutoGluon's confidence cut-off for keeping a pseudo-label</dd>
          <dt>g</dt><dd>a student model (GBM, NN, RF, CAT by default)</dd>
          <dt>𝒜</dt><dd>training rows plus augmented rows (spunge / munge perturbations)</dd>
          <dt>p̂<sub>teacher</sub></dt><dd>the ensemble's predicted probabilities (soft) or classes (hard)</dd>
        </dl>
      </div>
    </div>
  )
}

// -- 09 Deploy ----------------------------------------------------------------------------------------------------

export function DeployStep({ body, set, schema }: StepProps) {
  const refit = getPath(body, 'fit_kwargs.refit_full') as boolean | undefined
  const il = getPath(body, 'fit_kwargs.infer_limit') as number | undefined
  const ilb = getPath(body, 'fit_kwargs.infer_limit_batch_size') as number | undefined
  const ex = new Set((getPath(body, 'fit_kwargs.excluded_model_types') as string[] | undefined) ?? [])
  const gpus = schema.gpu.count
  return (
    <div className="grid hp-grid">
      <div className="card"><div className="card-head"><h2 className="card-title">Deploy</h2><span className="helper" style={{ fontSize: 12 }}>refit, latency limit, hardware, exclusions</span></div>
        <div className="card-body">
          <Row label="refit_full" sub="after validation, refit each model on all training rows (one model instead of k)" testid="gym-refit">
            <Seg value={refit ?? null} onChange={(x) => { set('fit_kwargs.refit_full', x ?? undefined); if (x === false || x == null) set('fit_kwargs.set_best_to_refit_full', undefined) }} options={[{ v: null, label: 'preset' }, { v: true, label: 'on' }, { v: false, label: 'off' }]} />
          </Row>
          <Row label={<>infer_limit <span className="math">λ</span></>} sub="seconds per row the deployed ensemble may take; AutoGluon prunes models to meet it" testid="gym-infer-limit">
            <input className="num-input" style={{ width: 110 }} placeholder="none" value={il != null ? String(il * 1e6) : ''} aria-label="infer_limit in microseconds per row"
              onChange={(e) => { const x = Number(e.target.value); set('fit_kwargs.infer_limit', e.target.value.trim() && x > 0 ? x / 1e6 : undefined) }} />
            <span className="mono muted">µs / row{il != null ? ` = ${fmtNum(il)} s` : ''}</span>
          </Row>
          <Row label={<>infer_limit_batch_size <span className="math">b</span></>} sub="rows per predict call the limit is measured at; 1 = online scoring, one row per call (the per-call overhead counts in full)" testid="gym-infer-batch">
            <Seg value={(ilb ?? null) as number | null} onChange={(x) => set('fit_kwargs.infer_limit_batch_size', x ?? undefined)}
              options={[{ v: null, label: 'default (10,000)' }, { v: 1, label: '1 · online' }, { v: 100, label: '100' }, { v: 1000, label: '1,000' }]} />
          </Row>
          <Row label={<>num_gpus</>} sub={schema.gpu.note ?? `${gpus} CUDA device(s): ${schema.gpu.names.join(', ')}`} testid="gym-gpu">
            <Seg value={(getPath(body, 'fit_kwargs.num_gpus') ?? 'auto') as string | number} onChange={(x) => set('fit_kwargs.num_gpus', x === 'auto' ? undefined : x)}
              options={[{ v: 'auto', label: 'auto' }, { v: 0, label: '0' }, ...(gpus ? [{ v: 1, label: '1' }] : [])]} />
            {!gpus && <StatusPill status="neutral">no GPU on this machine</StatusPill>}
          </Row>
          <Row label="excluded_model_types" sub="families AutoGluon must skip, whatever the preset lists" testid="gym-excluded">
            <div className="chips-row" style={{ gap: 4 }}>{schema.families.map((f) => (
              <button key={f.key} className={`chip-btn ${ex.has(f.key) ? 'on danger' : ''}`} onClick={() => { const n = new Set(ex); if (n.has(f.key)) n.delete(f.key); else n.add(f.key); set('fit_kwargs.excluded_model_types', n.size ? [...n] : undefined) }}>{f.key}</button>
            ))}</div>
          </Row>
        </div>
      </div>
      <div className="formula">
        <div className="micro-label">Deployment</div>
        <div className="eq"><i>m</i>* = arg max<sub><i>m</i> : <i>t</i><sub>pred</sub>(<i>m</i>) ≤ <i>λ</i></sub> <i>s</i><sub>val</sub>(<i>m</i>)</div>
        <dl className="where">
          <dt>λ</dt><dd>infer_limit: {il != null ? <b className="mono">{fmtNum(il * 1e6)} µs per row</b> : 'none'}</dd>
          <dt>t<sub>pred</sub>(m)</dt><dd>model m's measured predict time per row, in calls of b = infer_limit_batch_size rows ({ilb != null ? <b className="mono">b = {ilb.toLocaleString()}</b> : 'AutoGluon’s default b = 10,000'}{ilb === 1 ? ': one row per call, so the fixed cost of a call counts in full' : ''}). AutoGluon first measures the feature pipeline’s time per row p and fits under λ − p; if p ≥ λ it stops. On text data p alone is ~10 ms at b = 1: registering with a pruned pipeline is the way below it</dd>
          <dt>_FULL</dt><dd>with refit_full, each model m gets a copy m_FULL fit on every training row; it has no validation score of its own</dd>
        </dl>
      </div>
    </div>
  )
}

// -- 10 Review: the raw editor --------------------------------------------------------------------------------------

/** Two-way: the box shows fit_kwargs as JSON; a valid edit replaces fit_kwargs (the form follows), an invalid one is
 * held here with its parse error and changes nothing. */
export function RawKwargs({ body, setBody, v }: StepProps) {
  const text = JSON.stringify(body.fit_kwargs ?? {}, null, 2)
  const ptext = JSON.stringify(body.predictor_kwargs ?? {}, null, 2)
  const [draft, setDraft] = useState<string | null>(null)
  const [pdraft, setPdraft] = useState<string | null>(null)
  const [err, setErr] = useState('')
  const parse = (s: string, which: 'fit_kwargs' | 'predictor_kwargs') => {
    try {
      const x = JSON.parse(s || '{}')
      if (!x || typeof x !== 'object' || Array.isArray(x)) throw new Error(`${which} must be a JSON object`)
      setErr(''); setBody({ ...body, [which]: x }); return true
    } catch (e) { setErr(`${which}: ${(e as Error).message}`); return false }
  }
  const fkErrs = (v?.errors ?? []).filter((e) => e.path.startsWith('fit_kwargs') || e.path.startsWith('predictor_kwargs'))
  return (
    <div className="card" data-testid="gym-raw"><div className="card-head"><div className="left"><h2 className="card-title">Raw fit_kwargs</h2>
      <span className={`pill ${err || fkErrs.length ? 'danger' : 'success'}`} data-testid="gym-raw-status">{err ? 'JSON error' : fkErrs.length ? `${fkErrs.length} invalid` : 'valid'}</span></div>
      <span className="helper" style={{ fontSize: 12 }}>to <span className="mono">TabularPredictor.fit</span> unchanged · checked against its signature</span></div>
      <div className="card-body raw-grid">
        <label className="raw-box"><span className="micro-label">fit_kwargs</span>
          <textarea className="code-input" spellCheck={false} rows={Math.min(22, Math.max(6, (draft ?? text).split('\n').length + 1))} value={draft ?? text} data-testid="gym-raw-fit"
            onChange={(e) => { setDraft(e.target.value); if (parse(e.target.value, 'fit_kwargs')) setDraft(e.target.value) }} onBlur={() => !err && setDraft(null)} /></label>
        <label className="raw-box"><span className="micro-label">predictor_kwargs</span>
          <textarea className="code-input" spellCheck={false} rows={6} value={pdraft ?? ptext} data-testid="gym-raw-predictor"
            onChange={(e) => { setPdraft(e.target.value); parse(e.target.value, 'predictor_kwargs') }} onBlur={() => !err && setPdraft(null)} /></label>
        {(err || fkErrs.length > 0) && <div className="raw-errs">{err && <div className="g-err">✕ {err}</div>}{fkErrs.map((e, i) => <div key={i} className="g-err">✕ <span className="mono">{e.path}</span> {e.message}</div>)}</div>}
      </div>
    </div>
  )
}
