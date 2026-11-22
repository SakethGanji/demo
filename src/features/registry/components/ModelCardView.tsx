import { MathText } from '@/shared/components/MathText'
import { CardLabels } from '@/features/labels/Training'
import { useEffect, useState } from 'react'
import { api } from '@/shared/api/client'
import type { MetricCI, ModelCard, ModelVersion } from '@/shared/api/types'
import { fmtScore } from '@/shared/lib/metrics'
import { ResonanceWave } from '@/shared/primitives/ResonanceWave'
import { StatusPill } from '@/shared/primitives/StatusPill'
import { InputsSection } from './InputsSection'
import { ACTION_WORD, APPROVAL_STATUS, STAGE_STATUS, bytes, ms, pct, short, us, when } from '../lib'

const ci = (m?: MetricCI) => (m ? <><b>{fmtScore(m.value)}</b> <span className="muted">[{fmtScore(m.ci_low, 3)}, {fmtScore(m.ci_high, 3)}]</span></> : '—')

/** The model card as a document: what a model risk reviewer reads before signing. Downloadable as Markdown or HTML. */
export function ModelCardView({ v, actor, onEdited }: { v: ModelVersion; actor: string; onEdited: () => void }) {
  const [c, setC] = useState<ModelCard | null>(null)
  const [err, setErr] = useState('')
  const [edit, setEdit] = useState<Record<string, string>>({})
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState('')
  const load = () => api.registry.card(v.id).then((x) => { setC(x); setEdit(Object.fromEntries(x.use.fields.map((f) => [f.key, f.value]))) }).catch((e) => setErr((e as Error).message))
  useEffect(() => { load() }, [v.id, v.stage, v.approval]) // eslint-disable-line react-hooks/exhaustive-deps

  if (err) return <div className="card card-body" role="alert" style={{ color: 'var(--status-danger)' }}>✕ {err}</div>
  if (!c) return <div className="card card-body row"><ResonanceWave /> Assembling the model card from the run, the version and the audit log…</div>
  const s = c.summary, d = c.data, t = c.training, val = c.validation
  const save = async () => {
    setSaving(true); setSaved('')
    try { const r = await api.registry.editCard(v.id, actor, edit); setSaved(r.changed.length ? `saved: ${r.changed.join(', ')}` : 'no changes'); load(); onEdited() } catch (e) { setSaved(`✕ ${(e as Error).message}`) } finally { setSaving(false) }
  }
  const hc = val.holdout_ci?.metrics ?? {}, sc = val.source_holdout_ci?.metrics ?? {}
  const bal = Array.isArray(d.label_balance) ? d.label_balance : null
  const dep = c.deployment
  return (
    <div className="card model-card" data-testid="model-card">
      <div className="mc-head">
        <div>
          <span className="micro-label">model card · SR 11-7 documentation support</span>
          <h2 className="mc-title">{c.model_name} <span className="mono">v{c.version}</span></h2>
          <div className="row" style={{ gap: 8, flexWrap: 'wrap' }}>
            <StatusPill status={STAGE_STATUS[s.stage]}>{s.stage}</StatusPill><StatusPill status={APPROVAL_STATUS[s.approval]}>{s.approval}</StatusPill>
            <span className="mono helper" style={{ fontSize: 12 }}>h = {short(s.model_sha256, 16)}…</span>
          </div>
        </div>
        <div className="row" style={{ gap: 8 }}>
          <a className="btn" href={api.registry.cardUrl(v.id, 'md')} download data-testid="card-md">Download .md</a>
          <a className="btn" href={api.registry.cardUrl(v.id, 'html')} download data-testid="card-html">Download .html</a>
        </div>
      </div>
      <p className="mc-note" data-testid="card-disclaimer">▲ {c.generated_note}</p>

      <section className="mc-sec"><h3><i>1</i> Summary</h3>
        <dl className="mc-kv">
          {([['problem', `${s.problem_type} · y = ${s.label}${s.positive_class != null ? ` · positive = ${s.positive_class}` : ''}`], ['selection metric', s.eval_metric], ['engine', String((s as unknown as { engine?: string }).engine ?? 'AutoGluon TabularPredictor')],
            ['served model', s.best_model], ['source run', s.source_run], ['registered', `${s.registered_by} · ${when(s.registered_at)}`],
            ['MLflow', s.mlflow ? `models:/${s.mlflow.name}/${s.mlflow.version}` : '—'], ['model SHA-256', s.model_sha256]] as [string, string][]).map(([k, x]) => <div key={k}><dt>{k}</dt><dd className="mono">{x}</dd></div>)}
        </dl>
      </section>

      <section className="mc-sec" data-testid="card-use"><h3><i>2</i> Intended use and limitations <small>to be completed by the model owner</small></h3>
        <div className="mc-fields">
          {c.use.fields.map((f) => (
            <label key={f.key} className="mc-field"><span>{f.label}</span>
              <textarea className="kw-input" rows={2} placeholder={f.hint} data-testid={`card-${f.key}`} value={edit[f.key] ?? ''} onChange={(e) => setEdit({ ...edit, [f.key]: e.target.value })} />
            </label>
          ))}
        </div>
        <div className="row" style={{ gap: 8, marginTop: 8 }}>
          <button className="btn sm primary" data-testid="card-save" disabled={saving || !actor.trim()} onClick={save}>Save as {actor || '…'}</button>
          <span className="helper" style={{ fontSize: 12 }}>{saved || 'Edits are recorded in the audit log.'}</span>
        </div>
      </section>

      <section className="mc-sec" data-testid="card-data"><h3><i>3</i> Data</h3>
        <div className="mc-cols">
          <dl className="mc-kv">
            {([['dataset', d.name?.includes(d.source) ? d.name : `${d.name} (${d.source})`], ['file', d.filename], ['n rows × columns', `${d.rows?.toLocaleString()} × ${d.columns}`], ['features d', String(d.features)],
              ['training n_train', d.train_rows?.toLocaleString()], ['holdout n_h', d.holdout_rows?.toLocaleString()], ['dropped (no label)', String(d.dropped_missing_label ?? 0)],
              ['file SHA-256', d.sha256], ['split SHA-256', d.splits_sha256]] as [string, string][]).map(([k, x]) => <div key={k}><dt>{k}</dt><dd className="mono">{x}</dd></div>)}
          </dl>
          <div>
            {bal && <table className="table compact" data-testid="card-balance"><thead><tr><th>class</th><th className="r">train</th><th className="r">share</th><th className="r">holdout</th><th className="r">share</th></tr></thead>
              <tbody>{bal.map((b) => <tr key={b.class}><td className="mono">{b.class}</td><td className="r mono">{b.train.toLocaleString()}</td><td className="r mono">{pct(b.train_share, 2)}</td><td className="r mono">{b.holdout.toLocaleString()}</td><td className="r mono">{pct(b.holdout_share, 2)}</td></tr>)}</tbody></table>}
            <p className="helper" style={{ fontSize: 12 }}>Split: {d.split}.</p>
            {d.sql && <div data-testid="card-sql"><p className="helper" style={{ fontSize: 12, marginBottom: 4 }}>Loaded by SQL query · v{d.sql.version} · <span className="mono">{d.sql.url}</span> · fetched {d.sql.fetched_at} · data SHA-256 <span className="mono">{d.sql.data_sha}</span>{d.sql.parent ? <> · refreshed from <span className="mono">{d.sql.parent}</span></> : null}</p>
              <pre className="reg-call">{d.sql.query}</pre></div>}
            {d.warnings.map((w) => <p key={w} className="helper" style={{ fontSize: 12, color: 'var(--status-warning)' }}>▲ {w}</p>)}
          </div>
        </div>
        <table className="table compact"><thead><tr><th>feature</th><th>type</th><th className="r">missing</th><th className="r">distinct</th></tr></thead>
          <tbody>{d.feature_table.map((f) => <tr key={f.name}><td className="mono">{f.name}</td><td>{f.type}</td><td className="r mono">{pct(f.missing_share)}</td><td className="r mono">{f.unique.toLocaleString()}</td></tr>)}</tbody></table>
        {d.inputs && <InputsSection inputs={d.inputs} parity={d.parity} vid={v.id} />}
        <CardLabels runId={v.run_id} />
      </section>

      <section className="mc-sec"><h3><i>4</i> Development</h3>
        <pre className="reg-call" data-testid="card-call">{t.call}</pre>
        {t.notes.map((n) => <p key={n} className="helper" style={{ fontSize: 12 }}>· {n}</p>)}
        <p className="helper" style={{ fontSize: 12.5 }}>Registration: refit_full = {String(t.registration.refit_full)}, infer_limit = {t.registration.infer_limit == null ? 'none' : `${t.registration.infer_limit} s/row`}, clone_for_deployment = {String(t.registration.clone_for_deployment)}. {t.n_models} models trained in {fmtScore(t.fit_seconds, 1)} s.</p>
        {c.ensemble.weights && (
          <div className="formula" style={{ marginTop: 8 }}>
            <div className="eq" data-testid="card-ensemble"><i>ŷ</i>(<i>x</i>) = {Object.entries(c.ensemble.weights).map(([m, w], i) => <span key={m}>{i ? ' + ' : ''}<b className="mono">{w.toFixed(3)}</b>·<i>f</i><sub>{m}</sub>(<i>x</i>)</span>)}</div>
            <dl className="where"><dt>wₘ</dt><dd>weights of {c.ensemble.model} (Caruana greedy selection on validation predictions), Σ wₘ = 1, wₘ ≥ 0</dd><dt>fₘ</dt><dd>base model m’s predicted probability (or value); this version serves <span className="mono">{c.ensemble.version_best}</span> with {(c.ensemble.dependencies ?? []).length || "its"} model(s)</dd></dl>
          </div>
        )}
      </section>

      {flowKind(c) !== 'tabular' && <FlowCardSection c={c} />}
      {flowKind(c) === 'tabular' && <section className="mc-sec" data-testid="card-validation"><h3><i>5</i> Validation on the holdout <small>n_h = {d.holdout_rows?.toLocaleString()} rows never used for training or model selection; 95% percentile bootstrap, B = {val.holdout_ci?.B}</small></h3>
        <table className="table compact"><thead><tr><th>metric</th><th>definition</th><th className="r">this version</th><th className="r">source run</th></tr></thead>
          <tbody>{Object.entries(hc).map(([k, m]) => <tr key={k} data-testid={`card-metric-${k}`}><td className="mono">{k}</td><td className="math-col">{m.formula}</td><td className="r mono">{ci(m)}</td><td className="r mono">{ci(sc[k])}</td></tr>)}</tbody></table>
        {val.threshold && <p className="helper" style={{ fontSize: 12.5 }}>Decision threshold <b className="mono">t = {val.threshold.value.toFixed(3)}</b> ({val.threshold.source}{val.threshold.metric ? `, ${val.threshold.metric}` : ''}): predict positive when p̂ &gt; t. Validation score of the source model: {fmtScore(val.validation_score)}.</p>}
        {c.quantile && <QuantileCardSection q={c.quantile} />}
        {c.constraints && <div data-testid="card-monotone" style={{ marginTop: 8 }}>
          <h4 className="micro-label" style={{ margin: '8px 0 4px' }}>Monotone constraints</h4>
          <table className="table compact"><thead><tr><th>feature xⱼ</th><th>direction sⱼ</th><th>AutoGluon features</th><th className="r">ICE curves violating</th><th className="r">PD range</th><th>served model monotone</th></tr></thead>
            <tbody>{c.constraints.columns.map((r) => <tr key={r.column}><td className="mono">{r.column}</td><td>{r.direction === 'increasing' ? '↑ increasing (+1)' : '↓ decreasing (−1)'}</td><td className="mono">{(r.features ?? []).join(', ') || '—'}</td>
              <td className="r mono">{r.curves_violating ?? '—'} / {r.n_curves ?? '—'}</td><td className="r mono">{fmtScore(r.pd_range, 4)}</td>
              <td>{r.monotone == null ? '—' : r.monotone ? <span style={{ color: 'var(--status-success)' }}>● yes</span> : <span style={{ color: 'var(--status-danger)' }}>✕ no</span>}</td></tr>)}</tbody></table>
          <p className="helper" style={{ fontSize: 12 }}>Checked on <span className="mono">{c.constraints.served_model ?? '—'}</span>, as this version serves it: sⱼ·(f(x with xⱼ = g₊₁) − f(x with xⱼ = g)) ≥ −ε for every holdout row x and consecutive grid points g of xⱼ's quantiles, ε = 10⁻⁹. Method: {c.constraints.method}.{c.constraints.unconstrained_models?.length ? ` Unconstrained models in the run: ${c.constraints.unconstrained_models.join(', ')}.` : ''}</p>
        </div>}
        {c.scorecard && <div data-testid="card-scorecard" style={{ marginTop: 8 }}>
          <h4 className="micro-label" style={{ margin: '8px 0 4px' }}>Scorecard benchmark (interpretable challenger)</h4>
          <table className="table compact"><thead><tr><th>model</th><th className="r">holdout AUC</th><th className="r">Gini</th><th className="r">KS</th></tr></thead>
            <tbody>
              <tr><td className="mono">{c.scorecard.served.model} (served)</td><td className="r mono">{fmtScore(c.scorecard.served.auc)}</td><td className="r mono">{fmtScore(c.scorecard.served.gini, 3)}</td><td className="r mono">{fmtScore(c.scorecard.served.ks, 3)}</td></tr>
              <tr><td className="mono">Scorecard ({c.scorecard.n_features} features)</td><td className="r mono">{fmtScore(c.scorecard.scorecard.auc)}</td><td className="r mono">{fmtScore(c.scorecard.scorecard.gini, 3)}</td><td className="r mono">{fmtScore(c.scorecard.scorecard.ks, 3)}</td></tr>
            </tbody></table>
          <p className="helper" style={{ fontSize: 12 }}>ΔAUC (served − scorecard) = <b className="mono">{c.scorecard.delta_auc.delta >= 0 ? '+' : '−'}{Math.abs(c.scorecard.delta_auc.delta).toFixed(4)}</b>, 95% CI [{c.scorecard.delta_auc.ci[0].toFixed(4)}, {c.scorecard.delta_auc.ci[1].toFixed(4)}] over B = {c.scorecard.delta_auc.B} paired bootstrap resamples of the holdout. Points: PDO {c.scorecard.scaling.pdo}, {c.scorecard.scaling.base_score} at odds {c.scorecard.scaling.base_odds} : 1.
            {c.scorecard.cost ? ` Cost per 1,000 decisions (c_FP = ${c.scorecard.cost.c_fp}, c_FN = ${c.scorecard.cost.c_fn}): served ${c.scorecard.cost.served_per_1000.toFixed(0)}, scorecard ${c.scorecard.cost.scorecard_per_1000.toFixed(0)}.` : ''} Method: {c.scorecard.method}.</p>
        </div>}
        {c.reasons && <div data-testid="card-reasons" style={{ marginTop: 8 }}>
          <h4 className="micro-label" style={{ margin: '8px 0 4px' }}>Reason codes (adverse action)</h4>
          <p className="helper" style={{ fontSize: 12 }}>{c.reasons.rule}. Reference applicants: {c.reasons.reference === 'approved' ? 'training rows the served model approves' : 'all training rows'}.{c.reasons.method ? ` Attribution: ${c.reasons.method.name}, B = ${c.reasons.method.background}${c.reasons.method.coalitions ? `, M = ${c.reasons.method.coalitions}` : ''}.` : ''}</p>
          <table className="table compact"><thead><tr><th>code</th><th>feature</th><th>statement (high · category)</th><th>statement (low)</th></tr></thead>
            <tbody>{c.reasons.mapping.map((r) => <tr key={r.feature}><td className="mono">{r.code}</td><td className="mono">{r.feature}{r.constraint ? ` ${r.constraint > 0 ? '↑' : '↓'}` : ''}</td><td>{r.high ?? r.category}</td><td>{r.low ?? '—'}</td></tr>)}</tbody></table>
          <p className="helper" style={{ fontSize: 12 }}>{c.reasons.stability && !c.reasons.stability.error
            ? `Stability (ε = ${c.reasons.stability.noise}·σ, ${c.reasons.stability.rows} adverse holdout rows × ${c.reasons.stability.repeats}): top-${c.reasons.top_k} set unchanged ${((c.reasons.stability.same_set ?? 0) * 100).toFixed(0)}%, first reason ${((c.reasons.stability.same_top1 ?? 0) * 100).toFixed(0)}%.`
            : 'Stability: not run yet (Reason Codes tab).'}
            {c.reasons.last_batch ? ` Latest batch: ${c.reasons.last_batch.n_adverse} adverse rows at ${c.reasons.last_batch.rows_per_s?.toFixed(1)} rows/s, codes consistent with the SHAP signs: ${c.reasons.last_batch.signs_consistent ? 'yes' : 'no'}.` : ''} {c.reasons.monotone_note}</p>
        </div>}
        {c.recourse && <div data-testid="card-recourse" style={{ marginTop: 8 }}>
          <h4 className="micro-label" style={{ margin: '8px 0 4px' }}>Counterfactuals (recourse)</h4>
          <p className="helper" style={{ fontSize: 12 }}>{c.recourse.check
            ? `${c.recourse.check.with_valid} of ${c.recourse.check.adverse} adverse holdout rows (${((c.recourse.check.share_valid ?? 0) * 100).toFixed(1)}%) get a change that flips the served decision at t = ${c.recourse.check.threshold.toFixed(4)} when re-scored; median ${c.recourse.check.median_changes} changes; fixed features moved: ${c.recourse.check.fixed_changed}. Fixed: ${Object.entries(c.recourse.check.actions).filter(([, a]) => a === 'fixed').map(([f]) => f).join(', ') || 'none'}.`
            : 'Check: not run yet (Reason Codes tab → Recourse).'}
            {Object.keys(c.recourse.overrides).length ? ` Set by ${c.recourse.updated_by ?? 'a user'}: ${Object.entries(c.recourse.overrides).map(([f, a]) => `${f} → ${a}`).join(', ')}.` : ''} Method: {c.recourse.method}.</p>
        </div>}
        {c.credit_report && <div data-testid="card-credit" style={{ marginTop: 8 }}>
          <h4 className="micro-label" style={{ margin: '8px 0 4px' }}>Credit model performance report</h4>
          <p className="helper" style={{ fontSize: 12 }}>{c.credit_report.last
            ? `Holdout Gini ${c.credit_report.last.headline.gini?.toFixed(3) ?? '—'} (95% CI ${c.credit_report.last.headline.gini_ci?.map((x) => x.toFixed(3)).join('–') ?? '—'}), KS ${c.credit_report.last.headline.ks?.toFixed(3) ?? '—'}; ${c.credit_report.last.headline.months} labelled months; first CSI flag: ${c.credit_report.last.headline.first_csi_flag ?? 'none'}; ${c.credit_report.last.headline.cohorts} vintage cohorts (computed ${c.credit_report.last.computed_at} UTC). `
            : 'Not opened yet (Credit Report tab). '}
            <a href={api.registry.creditReportUrl(v.id, 'html')} download data-testid="card-credit-html">Model Performance Report (.html)</a> · <a href={api.registry.creditReportUrl(v.id, 'md')} download>.md</a></p>
        </div>}
        {c.survival && (() => {
          const g = c.survival
          return <div data-testid="card-surv" style={{ marginTop: 8 }}>
            <h4 className="micro-label" style={{ margin: '8px 0 4px' }}>Time to event (survival)</h4>
            <p className="helper" style={{ fontSize: 12 }}>{`T = ${g.duration_column}, event ${g.event_column} = 1 (0 = censored). Serving ${g.served === 'cox' ? 'Cox PH' : 'a random survival forest'}, chosen on ${g.chosen_on}. Holdout (${g.holdout_rows} rows, ${g.events.holdout} events): Harrell's C ${g.c_index.toFixed(4)} [${g.c_index_ci[0].toFixed(3)}, ${g.c_index_ci[1].toFixed(3)}] (the other model ${g.other.c_index?.toFixed(4) ?? '—'}); ${g.at.map((a) => `AUC(${a.t}) ${a.auc.toFixed(3)}`).join(', ')}. Calibration against Kaplan–Meier by risk quartile: max |Ŝ − Ŝ_KM| ${g.calibration.max_abs_diff.toFixed(3)}, ${g.calibration.in_ci}/${g.calibration.cells} inside the KM 95% CI (${g.calibration.ok ? 'calibrated' : 'not calibrated'}). Cox hazard ratios per unit: ${g.hazard_ratios.slice(0, 5).map((h) => `${h.term} ${h.hr.toFixed(2)} [${h.hr_ci[0].toFixed(2)}, ${h.hr_ci[1].toFixed(2)}]`).join('; ')}.`}</p>
          </div>
        })()}
        {c.multilabel && (() => {
          const g = c.multilabel
          return <div data-testid="card-ml" style={{ marginTop: 8 }}>
            <h4 className="micro-label" style={{ margin: '8px 0 4px' }}>Multi-label classification</h4>
            <p className="helper" style={{ fontSize: 12 }}>{`${g.labels.length} labels from ${g.label_column}, one AutoGluon yes/no model per label; Ŷ(x) = {l : p̂_l(x) ≥ t_l}, each t_l the best F1 on ${g.threshold_rule.replace('F1 on ', '')}. Holdout (${g.holdout_rows} rows): macro F1 ${g.macro_f1.toFixed(3)} [${g.ci.macro_f1[0].toFixed(3)}, ${g.ci.macro_f1[1].toFixed(3)}], micro F1 ${g.micro_f1.toFixed(3)}, Hamming loss ${g.hamming_loss.toFixed(4)} (no labels: ${g.baseline_hamming.toFixed(3)}). Per label F1 (t): ${g.per_label.map((p) => `${p.label} ${p.f1.toFixed(3)} (${p.threshold.toFixed(2)})`).join(', ')}.${g.skipped.length ? ` Not modelled: ${g.skipped.join(', ')}.` : ''}`}</p>
          </div>
        })()}
        {c.reject_inference && (() => {
          const g = c.reject_inference
          const ms = Object.entries(g.methods ?? {}).filter(([, x]) => x)
          const d = (x: { delta: number; ci: [number, number] } | null) => (x == null ? '—' : `${x.delta >= 0 ? '+' : '−'}${Math.abs(x.delta).toFixed(4)} [${x.ci[0].toFixed(4)}, ${x.ci[1].toFixed(4)}]`)
          return <div data-testid="card-ri" style={{ marginTop: 8 }}>
            <h4 className="micro-label" style={{ margin: '8px 0 4px' }}>Reject inference (through-the-door check)</h4>
            <p className="helper" style={{ fontSize: 12 }}>{`This model learns from approved applicants only. Run ${g.run_id} compared that with three ways of adding the declined back, assuming ${g.assumption}. `
              + (g.through_the_door ? `On ${g.n} through-the-door applicants: ${ms.map(([m, x]) => `${m.replace('_', ' ')} AUC ${x!.auc?.toFixed(4) ?? '—'}${m === 'approved_only' ? '' : ` (Δ ${d(x!.delta_auc)}, ${x!.verdict})`}`).join('; ')}. Approved-only mean p̂ ${g.methods?.approved_only?.mean_p.toFixed(3)} against a bad rate of ${g.methods?.approved_only?.bad_rate.toFixed(3)}.`
                : 'No through-the-door outcomes exist for this table, so the methods were compared on approved applicants only.')}</p>
          </div>
        })()}
        {c.segmented && (() => {
          const g = c.segmented
          const k = g.headline.toLowerCase()
          const d = (x: { delta: number | null; ci: [number, number] | null } | null) => (x?.delta == null ? '—' : `${x.delta >= 0 ? '+' : '−'}${Math.abs(x.delta).toFixed(4)}${x.ci ? ` [${x.ci[0].toFixed(4)}, ${x.ci[1].toFixed(4)}]` : ''}`)
          return <div data-testid="card-seg" style={{ marginTop: 8 }}>
            <h4 className="micro-label" style={{ margin: '8px 0 4px' }}>Segmented models (routed by {g.segment_column})</h4>
            <p className="helper" style={{ fontSize: 12 }}>{`One AutoGluon model per ${g.segment_column}, the global model's config; routing: ${g.routing_rule}. ${g.routes.length} of ${g.segments.length} segments use their own model, the rest and unknown values the global one${g.threshold != null ? `; one threshold t = ${g.threshold.toFixed(4)}` : ''}. Holdout ${g.headline}: global ${g.overall?.global[k]?.toFixed(4) ?? '—'} → routed ${g.overall?.routed[k]?.toFixed(4) ?? '—'}, Δ ${d(g.overall)} (${g.overall?.verdict ?? '—'}). Per segment (own vs global): ${g.segments.map((r) => `${r.value} ${d(r.own)}${r.routed ? '' : ' (global)'}`).join('; ')}.`}</p>
          </div>
        })()}
        {c.expected_loss && <div data-testid="card-el" style={{ marginTop: 8 }}>
          <h4 className="micro-label" style={{ margin: '8px 0 4px' }}>Expected loss (PD × LGD × EAD)</h4>
          <p className="helper" style={{ fontSize: 12 }}>{`PD on ${c.expected_loss.columns.pd}, LGD on ${c.expected_loss.columns.lgd}, EAD on ${c.expected_loss.columns.ead}. Holdout: ΣEL ${Math.round(c.expected_loss.portfolio.el).toLocaleString()} vs realised ${Math.round(c.expected_loss.portfolio.realised).toLocaleString()} on ${c.expected_loss.portfolio.n} loans, ratio ${c.expected_loss.portfolio.ratio?.toFixed(3) ?? '—'} (95% CI ${c.expected_loss.portfolio.ratio_ci.map((x) => x.toFixed(3)).join('–')}). Stress: ${c.expected_loss.scenarios.map((s) => `${s.name} ${s.delta_pct != null ? `${s.delta_pct >= 0 ? '+' : ''}${s.delta_pct.toFixed(1)}%` : '—'}`).join('; ')}.`}</p>
        </div>}
        {c.diagnostics && <DiagnosticsBlock d={c.diagnostics} />}
      </section>}

      <section className="mc-sec"><h3><i>6</i> Feature importance <small>{c.importance.method}</small></h3>
        <table className="table compact"><thead><tr><th className="nocase">feature j</th><th className="r nocase">Îⱼ</th><th className="r">95% CI</th><th className="r">p</th></tr></thead>
          <tbody>{c.importance.features.slice(0, 12).map((f) => <tr key={f.feature}><td className="mono">{f.feature}</td><td className="r mono">{fmtScore(f.importance)}</td><td className="r mono">[{fmtScore(f.ci_low, 3)}, {fmtScore(f.ci_high, 3)}]</td><td className="r mono">{fmtScore((f as unknown as { p_value?: number }).p_value, 3)}</td></tr>)}</tbody></table>
      </section>

      <section className="mc-sec" data-testid="card-fairness"><h3><i>7</i> Fairness</h3>
        {Object.keys(c.fairness.reports).length ? Object.entries(c.fairness.reports).map(([g, r]) => (
          <p key={g} className="helper" style={{ fontSize: 12.5 }}><b className="mono">{g}</b> ({r.binning}{r.threshold != null ? `, t = ${r.threshold.toFixed(3)}` : ''}): {Object.entries(r.gaps).map(([k, x]) => `${k.replace(/_/g, ' ')} = ${fmtScore(x, 3)}`).join(' · ')}</p>
        )) : <p className="helper" style={{ fontSize: 12.5 }}>{c.fairness.note}</p>}
        {c.fairness.fair_lending && (() => { const fl = c.fairness.fair_lending; return (
          <p className="helper" style={{ fontSize: 12.5 }} data-testid="card-fair-lending"><b>Fair-lending check (BISG{fl.tables === 'synthetic' ? ', synthetic stand-in tables' : ''})</b>: {fl.surname} × {fl.zip ?? 'national mix'}, {fl.n.toLocaleString()} holdout applicants at t = {fl.threshold.toFixed(3)}. AIR vs White: {Object.entries(fl.air).filter(([g]) => g !== 'white').map(([g, v]) => `${g} ${fmtScore(v ?? NaN, 3)}`).join(' · ')}; below 0.80: {fl.below_080.join(', ') || 'none'}. Proxy accuracy {fl.proxy_accuracy != null ? `${(fl.proxy_accuracy * 100).toFixed(1)}% vs the true groups` : `≈ ${(fl.proxy_expected_accuracy * 100).toFixed(1)}% (expected)`}{fl.max_abs_delta_vs_truth != null ? `; AIRs within ${fmtScore(fl.max_abs_delta_vs_truth, 3)} of the true-group AIRs` : ''}.</p>) })()}
      </section>

      {flowKind(c) === 'tabular' && <section className="mc-sec"><h3><i>8</i> Deployment trade-off <small>measured</small></h3>
        <table className="table compact"><thead><tr><th /><th className="r">source run</th><th className="r">this version</th></tr></thead>
          <tbody>
            <tr><td>served model</td><td className="r mono">{dep.source.best_model}</td><td className="r mono">{dep.version.best_model}</td></tr>
            <tr><td>size on disk</td><td className="r mono">{bytes(dep.source.size_bytes)}</td><td className="r mono">{bytes(dep.version.size_bytes)}</td></tr>
            <tr><td>batch t̂ p50 / p95 (µs/row)</td><td className="r mono">{us(dep.source.latency.us_per_row_p50)} / {us(dep.source.latency.us_per_row_p95)}</td><td className="r mono">{us(dep.version.latency.us_per_row_p50)} / {us(dep.version.latency.us_per_row_p95)}</td></tr>
            <tr><td>one row p50 / p95 (ms)</td><td className="r mono">{ms(dep.source.latency.single_ms_p50)} / {ms(dep.source.latency.single_ms_p95)}</td><td className="r mono">{ms(dep.version.latency.single_ms_p50)} / {ms(dep.version.latency.single_ms_p95)}</td></tr>
          </tbody></table>
      </section>}

      <section className="mc-sec"><h3><i>9</i> Monitoring <small>{c.monitoring.bands}</small></h3>
        {c.monitoring.batches.length ? <table className="table compact"><thead><tr><th>batch</th><th>scored</th><th className="r">rows</th><th className="r">max PSI (features)</th><th className="r nocase">PSI(p̂)</th></tr></thead>
          <tbody>{c.monitoring.batches.map((b) => <tr key={b.id}><td className="mono">{b.id}</td><td className="mono">{when(b.created_at)}</td><td className="r mono">{b.n_rows?.toLocaleString()}</td><td className="r mono">{fmtScore(b.max_psi, 4)}</td><td className="r mono">{fmtScore(b.prediction_psi, 4)}</td></tr>)}</tbody></table>
          : <p className="helper" style={{ fontSize: 12.5 }}>No batches scored yet. Score one under Batch &amp; Drift to start the record.</p>}
      </section>

      <section className="mc-sec" data-testid="card-approval"><h3><i>10</i> Approval</h3>
        <p className="helper" style={{ fontSize: 12.5 }}>{c.approval.rules}</p>
        <table className="table compact"><thead><tr><th>when (UTC)</th><th>who</th><th>action</th><th>stage</th><th>comment</th><th className="nocase">h</th></tr></thead>
          <tbody>{c.approval.audit.map((a) => <tr key={a.id}><td className="mono">{when(a.ts)}</td><td>{a.actor}</td><td>{ACTION_WORD[a.action]}</td><td className="mono">{a.to_stage ? `${a.from_stage ?? ''} → ${a.to_stage}` : ''}</td><td>{a.comment}</td><td className="mono">{short(a.model_hash, 8)}</td></tr>)}</tbody></table>
        <div className="mc-sign">{['Model owner', 'Independent validator', 'Model risk management'].map((r) => <div key={r}><span className="micro-label">{r}</span><i /><small className="helper">name · date · signature</small></div>)}</div>
      </section>
    </div>
  )
}

type FlowCard = { kind?: string; imported?: Record<string, unknown> | null
  forecasting?: { setup: Record<string, unknown> & { prediction_length_H: number; freq: string; seasonal_period_m: number; items: number; eval_metric: string; metric_formula: string; start: string; end: string; quantile_levels?: number[] }
    validation: string; holdout_metrics: Record<string, number>; coverage: number | null; interval: [number, number]; items: { item_id: string; MASE: number | null; WQL: number | null; sMAPE: number; coverage: number }[]
    ensemble: { weights?: Record<string, number> } | null; limitations: string[] } | null
  anomaly_detection?: { method: string; contamination: number; threshold: number; n_flagged: number; psi: number; c_n: number; n_trees: number; validation: string; limitations: string[]
    truth_check?: { precision_at_k: { k: number; hits: number; precision: number; recall: number }[]; roc_auc: number | null; planted: number } | null } | null }

const flowKind = (c: ModelCard) => ((c as unknown as FlowCard).kind ?? 'tabular') === 'tabular' ? 'tabular' : (c as unknown as FlowCard).kind!

/** §5 for forecasting and anomaly versions: what was validated and how, with the numbers. */
function FlowCardSection({ c }: { c: ModelCard }) {
  const x = c as unknown as FlowCard
  if (x.forecasting) {
    const f = x.forecasting, st = f.setup
    return (
      <section className="mc-sec" data-testid="card-forecasting"><h3><i>5</i> Forecasting <small>{f.validation}</small></h3>
        <p className="helper" style={{ fontSize: 12.5 }}><span className="math">ŷ<sub>i,t+h|t</sub></span> for h = 1…H, <b className="mono">H = {st.prediction_length_H}</b>, freq <span className="mono">{st.freq}</span>, season <span className="math">m</span> = {st.seasonal_period_m}, {st.items} series from {String(st.start).slice(0, 10)} to {String(st.end).slice(0, 10)}; quantiles τ ∈ {'{'}{(st.quantile_levels ?? []).join(', ')}{'}'}. {st.eval_metric}: <MathText className="math-col" text={st.metric_formula} /></p>
        <table className="table compact"><thead><tr><th>holdout metric</th><th className="r">value</th></tr></thead>
          <tbody>{Object.entries(f.holdout_metrics ?? {}).map(([k, v]) => <tr key={k}><td className="mono">{k}</td><td className="r mono">{fmtScore(v)}</td></tr>)}
            {f.coverage != null && <tr><td className="mono">coverage [q{f.interval[0]}, q{f.interval[1]}]</td><td className="r mono">{fmtScore(f.coverage)}</td></tr>}</tbody></table>
        <table className="table compact"><thead><tr><th>series</th><th className="r">MASE</th><th className="r">WQL</th><th className="r">sMAPE</th><th className="r">coverage</th></tr></thead>
          <tbody>{f.items.map((r) => <tr key={r.item_id}><td className="mono">{r.item_id}</td><td className="r mono">{fmtScore(r.MASE)}</td><td className="r mono">{fmtScore(r.WQL)}</td><td className="r mono">{fmtScore(r.sMAPE)}</td><td className="r mono">{fmtScore(r.coverage)}</td></tr>)}</tbody></table>
        {f.ensemble?.weights && <p className="helper" style={{ fontSize: 12.5 }}>Ensemble q̂<sub>τ</sub> = Σ w<sub>m</sub> q̂<sub>τ</sub><sup>(m)</sup>: {Object.entries(f.ensemble.weights).map(([k, w]) => `${k} ${w.toFixed(3)}`).join(' · ')}</p>}
        <ul className="helper" style={{ fontSize: 12.5 }}>{f.limitations.map((l) => <li key={l}>{l}</li>)}</ul>
      </section>
    )
  }
  if (x.anomaly_detection) {
    const a = x.anomaly_detection
    return (
      <section className="mc-sec" data-testid="card-anomaly"><h3><i>5</i> Anomaly detection <small>{a.validation}</small></h3>
        <p className="helper" style={{ fontSize: 12.5 }}>{a.method}. ψ = {a.psi}, c(ψ) = {a.c_n.toFixed(3)}, T = {a.n_trees} trees, contamination c = {a.contamination}, threshold s* = <b className="mono">{a.threshold.toFixed(4)}</b>, {a.n_flagged} rows flagged.</p>
        {a.truth_check && <table className="table compact"><thead><tr><th className="r">k</th><th className="r">hits</th><th className="r">precision@k</th><th className="r">recall</th></tr></thead>
          <tbody>{a.truth_check.precision_at_k.map((r) => <tr key={r.k}><td className="r mono">{r.k}</td><td className="r mono">{r.hits}</td><td className="r mono">{fmtScore(r.precision)}</td><td className="r mono">{fmtScore(r.recall)}</td></tr>)}</tbody></table>}
        <ul className="helper" style={{ fontSize: 12.5 }}>{a.limitations.map((l) => <li key={l}>{l}</li>)}</ul>
      </section>
    )
  }
  return null
}


/** F9: a quantile version's levels, calibration per level and interval coverage, raw and conformally calibrated. */
function QuantileCardSection({ q }: { q: NonNullable<ModelCard['quantile']> }) {
  const g = (t: number) => String(Number(t.toFixed(6)))
  const key = (r: { lo: number; hi: number }) => `${r.lo}-${r.hi}`
  const raw = new Map(q.raw.map((r) => [key(r), r]))
  const cal = new Map(q.calibrated.map((r) => [key(r), r]))
  return (
    <div data-testid="card-quantile" style={{ marginTop: 8 }}>
      <h4 className="micro-label" style={{ margin: '8px 0 4px' }}>Quantile regression (range)</h4>
      <table className="table compact"><thead><tr><th className="nocase">level τ</th><th className="r nocase">pinball ρ̄τ</th><th className="r nocase">F̂(τ) = share of y ≤ q̂τ</th><th className="r">95% CI</th></tr></thead>
        <tbody>{q.per_level.map((r) => <tr key={r.tau}><td className="mono">{g(r.tau)}{r.tau === q.centre ? ' · point ŷ' : ''}</td><td className="r mono">{fmtScore(r.pinball, 1)}</td><td className="r mono">{fmtScore(r.below, 3)}</td><td className="r mono">[{fmtScore(r.below_ci[0], 3)}, {fmtScore(r.below_ci[1], 3)}]</td></tr>)}</tbody></table>
      <table className="table compact" style={{ marginTop: 6 }}><thead><tr><th>interval</th><th className="r">nominal</th><th className="r">coverage (served)</th><th className="r">95% CI</th><th className="r">raw</th><th className="r">calibrated</th><th className="r">mean width</th></tr></thead>
        <tbody>{q.intervals.map((r) => <tr key={key(r)} data-testid={`card-quantile-${g(r.lo)}-${g(r.hi)}`}><td className="mono">[q̂<sub>{g(r.lo)}</sub>, q̂<sub>{g(r.hi)}</sub>]</td><td className="r mono">{g(r.nominal)}</td>
          <td className="r mono">{fmtScore(r.coverage, 4)}</td><td className="r mono">[{fmtScore(r.coverage_ci[0], 3)}, {fmtScore(r.coverage_ci[1], 3)}]</td>
          <td className="r mono">{fmtScore(raw.get(key(r))?.coverage, 4)}</td><td className="r mono">{fmtScore(cal.get(key(r))?.coverage, 4)}</td><td className="r mono">{fmtScore(r.mean_width, 0)}</td></tr>)}</tbody></table>
      <p className="helper" style={{ fontSize: 12 }}>Served by <span className="mono">{q.served_model}</span> with the {q.served} quantiles (conformal offsets {q.conformal.applied ? 'applied' : 'not applied'}{q.conformal.offsets ? `: ${Object.entries(q.conformal.offsets).map(([t, c]) => `c${t} = ${fmtScore(c, 1)}`).join(', ')}` : ''}). Holdout pinball loss {fmtScore(q.pinball, 1)}; quantile crossing on {q.crossing_share == null ? '—' : `${(q.crossing_share * 100).toFixed(2)}%`} of rows. Method: {q.method}.</p>
    </div>
  )
}

/** F19: the source run's diagnostics on the card: one line per check, "not run" where it has not been. */
function DiagnosticsBlock({ d }: { d: NonNullable<ModelCard['diagnostics']> }) {
  const sp = d.split
  const rows: [string, React.ReactNode][] = [
    ['Split (F6)', <>{({ 'random-stratified': 'random (stratified)', group: 'group', time: 'out-of-time', 'time-group': 'out-of-time + group' } as Record<string, string>)[sp.strategy] ?? sp.strategy}{sp.group_column ? <> · group <span className="mono">{sp.group_column}</span></> : null}{sp.time_column ? <> · time <span className="mono">{sp.time_column}</span></> : null}</>],
    ...(d.cost ? [['Business cost (F7)', <>c<sub>FP</sub> = {d.cost.c_fp}, c<sub>FN</sub> = {d.cost.c_fn}{d.cost.g_tp ? <>, g<sub>TP</sub> = {d.cost.g_tp}</> : null} · eval_metric <span className="mono">expected_cost</span></>] as [string, React.ReactNode]] : []),
    ['Learning curve (F12)', d.learning_curve ? <><b>{d.learning_curve.verdict}</b>{d.learning_curve.slope != null ? <> · gain per doubling <span className="mono">{fmtScore(d.learning_curve.slope, 4)}</span>{d.learning_curve.slope_ci ? <> [{fmtScore(d.learning_curve.slope_ci[0], 4)}, {fmtScore(d.learning_curve.slope_ci[1], 4)}]</> : null}</> : null}</> : 'not run'],
    ['Shift (F15)', d.shift ? <>{d.shift.reference} vs {d.shift.new}: AUC <span className="mono">{fmtScore(d.shift.auc, 3)}</span> · <b>{d.shift.verdict}</b>{d.shift.top ? <> · top <span className="mono">{d.shift.top}</span></> : null}</> : 'not run'],
    ['Weak slices (F16)', d.slices ? <>{d.slices.n_weak} weak of {d.slices.n_tested?.toLocaleString()} tested ({d.slices.loss}, α = {d.slices.alpha}){d.slices.top[0] ? <> · first <span className="mono">{d.slices.top[0].label}</span> (n = {d.slices.top[0].n}, Δ = {fmtScore(d.slices.top[0].delta, 3)})</> : null}</> : 'not run'],
    ...(d.annotators ? [['Annotators (F14)', <>least reliable <b>{d.annotators.worst ?? '—'}</b> · Fleiss κ <span className="mono">{fmtScore(d.annotators.fleiss_kappa, 3)}</span> · consensus changes {d.annotators.changed_vs_given ?? '—'} labels</>] as [string, React.ReactNode]] : []),
    ['Model Doctor (F17)', d.doctor ? <>{d.doctor.primary ? <b>{d.doctor.primary.title} ({d.doctor.primary.severity})</b> : <b>no limiting factor found</b>}{d.doctor.primary?.reason ? <> · {d.doctor.primary.reason}</> : null}</> : 'not run (Explain → Model Doctor)'],
  ]
  return (
    <div data-testid="card-diagnostics" style={{ marginTop: 8 }}>
      <h4 className="micro-label" style={{ margin: '8px 0 4px' }}>Diagnostics of the source run</h4>
      <table className="table compact mc-diag"><tbody>{rows.map(([k, v]) => <tr key={k}><td style={{ width: 180 }}>{k}</td><td>{v}</td></tr>)}</tbody></table>
    </div>
  )
}
