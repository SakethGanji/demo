import { useState } from 'react'
import { FretSlider } from '@/shared/components/FretSlider'
import { ParamChip } from '@/shared/primitives/ParamChip'
import type { Dataset } from '../dataset'
import { CostCard } from './CostCard'
import { COST_METRIC, DEFAULT_COST, PINBALL, metricInfo, BUDGETS, METRICS, PRESETS, columnTypesPayload, defaultMetric, defaultThreshold, fmtBudget, labelImbalance, minorityShare, parseKwargs, type Choices, type Task } from '../wizard'

/** Step 3: the metric to optimise, the preset ladder (speed ↔ accuracy), the time budget, and raw fit kwargs. */
export function GoalStep({ ds, ch, set, task }: { ds: Dataset; ch: Choices; set: (p: Partial<Choices>) => void; task: Task }) {
  const [open, setOpen] = useState(ch.fitKwargs.trim() !== '')
  const range = task === 'regression' ? ch.range ?? null : null
  // F9: a range is scored by the pinball loss, AutoGluon's only quantile metric
  const metrics = range ? [metricInfo(PINBALL)!] : METRICS.filter((m) => m.tasks.includes(task))
  const chosen = range ? PINBALL : ch.metric
  const rec = defaultMetric(task, labelImbalance(ds, ch.label))
  const kw = parseKwargs(ch.fitKwargs)
  const preset = PRESETS.find((p) => p.key === ch.preset) ?? PRESETS[0]
  const { types, drop } = columnTypesPayload(ds, ch)
  const userCal = kw.ok && 'calibrate_decision_threshold' in kw.value
  const minority = minorityShare(ds, ch.label)
  const recT = defaultThreshold(ds, ch.label, task)
  const isCost = ch.metric === COST_METRIC
  const cc = ch.cost ?? DEFAULT_COST
  const metricCode = !isCost ? q(chosen) : task === 'binary' ? `binary_cost_scorer(c_fp=${cc.c_fp}, c_fn=${cc.c_fn}, g_tp=${cc.g_tp})` : 'multiclass_cost_scorer(matrix=C)'
  const call = [
    ...(isCost ? ['from ensemble_models.cost import ' + (task === 'binary' ? 'binary_cost_scorer, binary_cost_class_scorer' : 'multiclass_cost_scorer')] : []),
    range ? `predictor = TabularPredictor(label=${q(ch.label)}, problem_type='quantile', quantile_levels=[${range.levels.join(', ')}], eval_metric=${metricCode})`
      : `predictor = TabularPredictor(label=${q(ch.label)}, problem_type=${q(task)}, eval_metric=${metricCode})`,
    `predictor.fit(train,  # ${ds.rows.toLocaleString()} rows minus the 20% holdout`,
    `    presets=${q(ch.preset)}, time_limit=${ch.budget},`,
    ...(Object.keys(types).length ? [`    feature_metadata=FeatureMetadata(…${Object.keys(types).length} override${Object.keys(types).length > 1 ? 's' : ''}),`] : []),
    ...(range && range.conformal != null && !(kw.ok && 'calibrate' in kw.value) ? [`    calibrate=${range.conformal ? 'True' : 'False'},  # conformal recalibration (CQR)`] : []),
    ...(kw.ok ? Object.entries(kw.value).map(([k, v]) => `    ${k}=${JSON.stringify(v)},`) : ['    # fit_kwargs: invalid JSON']),
    ')',
    ...(task === 'binary' && isCost && !userCal ? [`predictor.set_decision_threshold(predictor.calibrate_decision_threshold(metric=binary_cost_class_scorer(${cc.c_fp}, ${cc.c_fn}, ${cc.g_tp})))`]
      : task === 'binary' && ch.threshold && !userCal ? [`predictor.set_decision_threshold(predictor.calibrate_decision_threshold(metric=${q(ch.threshold)}))`] : []),
    ...(drop.length ? [`# dropped columns: ${drop.join(', ')}`] : []),
  ]

  return (
    <div className="page">
      <div className="grid" style={{ gridTemplateColumns: 'minmax(0, 1fr) minmax(0, 1.25fr)', alignItems: 'start' }}>
        <div className="card" data-testid="metric-card">
          <div className="card-head"><h3 className="card-title">Metric</h3><span className="micro-label">what AutoGluon optimises and ranks by</span></div>
          <div className="metric-list" role="radiogroup">
            {metrics.map((m) => (
              <button key={m.key} role="radio" aria-checked={chosen === m.key} data-testid={`metric-${m.key}`} className={`metric-opt ${chosen === m.key ? 'on' : ''}`} onClick={() => { if (!range) set({ metric: m.key }) }}>
                <span className="mo-head"><b>{m.name}</b><span className="mono mo-key">{m.key}</span>{(range ? m.key === PINBALL : m.key === rec) && <span className="pill success" style={{ height: 18 }}>● recommended</span>}<span className="spacer" /><span className="mo-dir">{m.greater ? 'higher is better ↑' : 'lower is better ↓'}</span></span>
                <span className="mo-formula">{m.formula}</span>
                <span className="helper mo-note">{m.note}{Object.keys(m.where).length > 0 && <> · {Object.entries(m.where).map(([k, v]) => <span key={k}><i className="math-i">{k}</i>: {v}. </span>)}</>}</span>
              </button>
            ))}
            <div className="metric-foot helper">n = number of rows scored, yᵢ the true label and ŷᵢ the prediction for row i.{range ? ` Predicting a range: τ ∈ {${range.levels.join(', ')}}, so L = ${range.levels.length}; the pinball loss is AutoGluon's eval_metric for problem_type 'quantile'.` : ''}{task === 'binary' && ds.profile?.imbalance_ratio && ds.profile.imbalance_ratio >= 4 ? ` The label is imbalanced (ρ = ${ds.profile.imbalance_ratio.toFixed(1)}), so ROC AUC is recommended over accuracy.` : ''}</div>
          </div>
        </div>

        <div className="stack">
          <div className="card" data-testid="preset-ladder">
            <div className="card-head"><h3 className="card-title">Goal · Preset</h3><span className="micro-label">faster ← → more accurate</span></div>
            <div className="ladder">
              <svg className="ladder-string" width="100%" height="8" aria-hidden preserveAspectRatio="none" viewBox="0 0 100 8"><line x1="0" x2="100" y1="4" y2="4" stroke="var(--border-track)" strokeWidth="1" vectorEffect="non-scaling-stroke" /><line x1="0" x2={((preset.rung - 0.5) / PRESETS.length) * 100} y1="4" y2="4" stroke="var(--accent-primary)" strokeWidth="2" vectorEffect="non-scaling-stroke" /></svg>
              {PRESETS.map((p) => (
                <button key={p.key} className={`rung ${ch.preset === p.key ? 'on' : ''}`} data-testid={`preset-${p.key}`} aria-pressed={ch.preset === p.key} onClick={() => set({ preset: p.key })}>
                  <span className="rung-node" />
                  <b>{p.name}</b><span className="mono rung-key">{p.key}</span>
                  <span className="rung-meter"><small>speed</small><Dots n={p.speed} /></span>
                  <span className="rung-meter"><small>accuracy</small><Dots n={p.accuracy} /></span>
                  <span className="mono rung-fact">{p.bag}</span>
                  <span className="mono rung-fact">{p.stack}{p.refit ? ' · refit' : ''}</span>
                </button>
              ))}
            </div>
            <div className="card-body" style={{ borderTop: '1px solid var(--border-default)' }}>
              <ul className="rung-does">{preset.does.map((d) => <li key={d}>{d}</li>)}</ul>
              <p className="helper" style={{ fontSize: 12 }}>k = bagging folds (each model is fit k times, every row is scored out of fold, so scores come as μ ± σ over k folds) · L = stack layers · refit = final models refit on all rows. AutoGluon sets k and L from the data size.</p>
            </div>
          </div>

          <div className="card" data-testid="budget-card">
            <div className="card-head"><h3 className="card-title">Time Budget</h3><ParamChip sym="τ" value={fmtBudget(ch.budget)} /></div>
            <div className="card-body">
              <FretSlider sym="τ" name="time_limit" help="Wall-clock seconds AutoGluon may spend fitting. Models that don't fit in time are skipped." min={30} max={3600} log value={ch.budget}
                onChange={(v) => set({ budget: Math.round(v) })} ticks={BUDGETS} tickLabel={(v) => fmtBudget(v).replace(' min', 'm').replace(' s', 's')} format={(v) => String(Math.round(v))} domain={<span className="mono">[30, 3600] s</span>} guided />
            </div>
          </div>

          {isCost && <CostCard ds={ds} ch={ch} set={set} task={task} />}

          {task === 'binary' && isCost && (
            <div className="card" data-testid="threshold-card">
              <div className="card-head"><h3 className="card-title">Decision Threshold</h3><span className="micro-label nocase">ŷ = positive when p̂ &gt; t</span></div>
              <div className="card-body"><p className="helper" style={{ fontSize: 12 }}>Chosen on the business cost: after the fit, <span className="mono">calibrate_decision_threshold(metric=binary_cost_class_scorer(…))</span> searches t on AutoGluon's validation predictions to minimise the expected cost, then <span className="mono">set_decision_threshold(t)</span>. With calibrated probabilities it lands near t* = {(cc.c_fp / Math.max(cc.c_fp + cc.c_fn + cc.g_tp, 1e-9)).toFixed(4)}.{userCal && <><br /><b>fit_kwargs.calibrate_decision_threshold is set below: AutoGluon's own setting wins.</b></>}</p></div>
            </div>
          )}

          {task === 'binary' && !isCost && (
            <div className="card" data-testid="threshold-card">
              <div className="card-head"><h3 className="card-title">Decision Threshold</h3><span className="micro-label nocase">ŷ = positive when p̂ &gt; t</span></div>
              <div className="card-body stack" style={{ gap: 'var(--space-8)' }}>
                <div className="seg" role="radiogroup">
                  {([[null, 't = 0.5 (AutoGluon default)'], ['f1', 'calibrate for F₁'], ['balanced_accuracy', 'for balanced accuracy'], ['mcc', 'for MCC']] as [string | null, string][]).map(([k, label]) => (
                    <button key={String(k)} role="radio" aria-checked={ch.threshold === k} className={ch.threshold === k ? 'on' : ''} onClick={() => set({ threshold: k })} data-testid={`threshold-${k ?? 'default'}`}>{label}{k === recT && k ? ' ●' : ''}</button>
                  ))}
                </div>
                <p className="helper" style={{ fontSize: 12 }}>
                  The rarest class is {(minority * 100).toFixed(1)}% of the rows{minority < 0.2 ? <>: few rows ever reach p̂ &gt; 0.5, so recall and F₁ collapse at the default. Calibrating searches t on AutoGluon's validation predictions (never the holdout) after the fit: <span className="mono">calibrate_decision_threshold(metric)</span>, then <span className="mono">set_decision_threshold(t)</span>. Metrics are reported at that t; you can move it later in Explain.</> : <>; 0.5 is a reasonable cut. Calibration can still help F₁ or balanced accuracy.</>}
                  {userCal && <><br /><b>fit_kwargs.calibrate_decision_threshold is set below: AutoGluon's own setting wins and this choice is ignored.</b></>}
                </p>
              </div>
            </div>
          )}

          <div className="card advanced" data-testid="advanced">
            <button className="card-head adv-toggle" aria-expanded={open} onClick={() => setOpen(!open)}>
              <div className="left"><h3 className="card-title">{open ? '▾' : '▸'} Advanced · fit_kwargs</h3><span className="helper" style={{ fontSize: 12 }}>raw JSON, passed to TabularPredictor.fit unchanged</span></div>
              {ch.fitKwargs.trim() && <span className={`pill ${kw.ok ? 'success' : 'danger'}`}>{kw.ok ? `● ${Object.keys(kw.value).length} key${Object.keys(kw.value).length === 1 ? '' : 's'}` : '✕ invalid'}</span>}
            </button>
            {open && (
              <div className="card-body stack" style={{ gap: 'var(--space-8)' }}>
                <textarea className="kw-input mono" data-testid="fit-kwargs" spellCheck={false} rows={6} value={ch.fitKwargs} placeholder={'{\n  "num_bag_folds": 5,\n  "hyperparameters": {"GBM": {}, "CAT": {}}\n}'}
                  onChange={(e) => set({ fitKwargs: e.target.value })} />
                {!kw.ok && <span className="helper" role="alert" style={{ color: 'var(--status-danger)', fontSize: 12 }}>✕ {kw.error}</span>}
                <div className="row" style={{ flexWrap: 'wrap', gap: 6 }}>
                  {SNIPPETS.map(([name, v]) => <button key={name} className="btn sm" onClick={() => set({ fitKwargs: JSON.stringify({ ...(kw.ok ? kw.value : {}), ...v }, null, 2) })}>+ {name}</button>)}
                </div>
                <pre className="call mono" data-testid="resolved-call">{call.join('\n')}</pre>
                <p className="helper" style={{ fontSize: 12 }}>Keys here win over the preset and the budget above. The full AutoGluon config (model families, per-family hyperparameters, HPO, distillation) is the Training Gym's job.</p>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

const q = (s: string) => `'${s}'`

const SNIPPETS: [string, Record<string, unknown>][] = [
  ['bagging k = 5', { num_bag_folds: 5 }],
  ['stack L = 2', { num_stack_levels: 1 }],
  ['trees only', { hyperparameters: { GBM: {}, CAT: {}, XGB: {}, RF: {}, XT: {} } }],
  ['exclude NN', { excluded_model_types: ['NN_TORCH', 'FASTAI'] }],
]

function Dots({ n }: { n: number }) {
  return <span className="dots" aria-label={`${n} of 4`}>{[1, 2, 3, 4].map((i) => <i key={i} className={i <= n ? 'on' : ''} />)}</span>
}
