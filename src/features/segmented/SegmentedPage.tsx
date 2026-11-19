import { flows, type SegCompare, type SegResult, type SegRun } from '@/shared/api/flows'
import { CiBar, KindFlow, NumField, type KindSpec } from '@/shared/components/KindFlow'
import { HeroCell } from '@/shared/components/Status'
import { fix, pct } from '@/shared/lib/format'

const GLOBAL = '__global__'
const sgn = (v: number | null | undefined, d = 3) => (v == null || !Number.isFinite(v) ? '—' : `${v >= 0 ? '+' : '−'}${Math.abs(v).toFixed(d)}`)

function Formula({ seg, binary }: { seg: string; binary: boolean }) {
  return (
    <div className="navy-well" style={{ padding: 'var(--space-12) var(--space-16)', minHeight: 0 }}>
      <div className="tp-eq" style={{ fontSize: 14 }} data-testid="seg-formula">
        f<sub>R</sub>(x) = f<sub>k</sub>(x) if {seg}(x) = k is routed, else f<sub>G</sub>(x) &nbsp;·&nbsp; {binary ? <>ΔAUC = AUC(f<sub>R</sub>) − AUC(f<sub>G</sub>)</> : <>ΔMSE = mean(e<sub>R</sub>² − e<sub>G</sub>²)</>} on the same rows
      </div>
    </div>
  )
}

/**
 * G5 · Segmented models, a choice inside Build: the setup, pre-filled from the G0 roles (target, segment column, what to leave
 * out, the split). The run (Models): one global model against one model per segment on the same holdout, with paired CIs, and
 * the routed version to register. Registry → Results shows the same view.
 */
export const SEG_SPEC: KindSpec = {
  id: 'segmented', title: 'Segmented Models', testId: 'seg', defaultDataset: 'segmented-loans', mockWhat: 'segmented models train AutoGluon',
  sub: 'One AutoGluon model per segment against one global model, on the same holdout; the result is one routed version.',
  detect: flows.seg.detect, create: (b) => flows.seg.create(b as never),
  roles: [
    { key: 'label', label: 'target y', urlParam: 'label', testId: 'seg-label', options: (ds) => ds.profile.columns.map((c) => c.name), emptyHint: 'nothing detected: pick a column' },
    { key: 'segment_column', label: 'segment s', urlParam: 'segment', testId: 'seg-column', emptyHint: 'nothing detected: pick a category with 2–12 values',
      options: (ds, d) => ds.profile.columns.filter((c) => c.type === 'category' && c.unique >= 2 && c.unique <= 12 && c.name !== d.label).map((c) => c.name) },
  ],
  leftOut: ({ d }) => <>IDs, leaks, protected; {d.split === 'random' ? 'random holdout' : `${d.split} holdout by ${d.time_column ?? d.group_column}`}</>,
  Formula: ({ d }) => <Formula seg={d.segment_column ?? 's'} binary={d.problem_type !== 'regression'} />,
  note: ({ d, det, pick }) => det.heterogeneity && !pick.label && !pick.segment_column && <p className={`helper ${det.heterogeneity.opposite ? 'el-ok' : ''}`} style={{ fontSize: 12.5 }} data-testid="seg-hetero">
    {det.heterogeneity.opposite ? '● ' : ''}A quick look before any fit: {det.heterogeneity.words}. ρ is the rank correlation with {d.label} inside each segment.</p>,
  advanced: { initial: () => ({ limit: 20 }), summary: (o) => `${o.limit} s per model`,
    Body: ({ o, set }) => <><NumField label="time per model (s)" value={o.limit} set={(limit) => set({ limit })} min={10} max={3600} testId="seg-limit" /><span className="helper" style={{ fontSize: 12 }}>AutoGluon medium_quality, the same config for the global model and every segment's.</span></> },
  explain: ({ d }) => <>The global model sees every training row, {d.segment_column ?? 'the segment'} included; each segment's model sees only its own rows. A segment with fewer than 300 training rows (or 30 of the rarer class) keeps the global model. Where the split has a tuning slice, a segment is routed to its own model only if that model beat the global one there, so the holdout stays untouched until the comparison.</>,
  startLabel: ({ d, ds }) => `Train global + ${ds.profile.columns.find((c) => c.name === d.segment_column)?.top?.length || 'k'} segment models →`,
  canStart: ({ d }) => !!(d.label && d.segment_column),
  body: ({ d, o }) => ({ label: d.label, segment_column: d.segment_column, drop_columns: d.drop_columns.filter((c: string) => c !== d.label && c !== d.segment_column), time_limit: o.limit }),
  running: { events: 'seg', wide: true, line: (run) => `Training the global model, then one per ${run.config.segment_column}, ${run.config.time_limit ?? '—'} s each`,
    steps: (_, started) => started.map((e) => ({ key: e.model as string, label: <><b>{e.model === GLOBAL ? 'global' : String(e.model)}</b><span className="helper"> · {Number(e.rows).toLocaleString()} rows</span></> })) },
  meta: (run) => `y = ${run.config.label} · s = ${run.config.segment_column}`,
  registerName: (run) => `${run.dataset_id}.segmented`,
}

export const SegmentedPage = ({ go }: { go: (r: string) => void }) => <KindFlow spec={SEG_SPEC} go={go} />

// ---------------------------------------------------------------------------------------------- results (shared)

/** The comparison: used by the run page and by Registry → Segments. */
export function SegResults({ run }: { run: SegRun }) {
  const s = run.segmented as SegResult
  const bin = s.problem_type === 'binary'
  const H = s.headline
  const key = bin ? 'auc' : 'rmse'
  const dk = bin ? 'delta_auc' : 'delta_mse'
  const o = s.overall
  const od = o[dk] as SegCompare['delta_auc']
  const all = [...s.segments.map((r) => r.own?.[dk] as SegCompare['delta_auc']), od, s.overall_all_segment_models[dk] as SegCompare['delta_auc']]
  const span = Math.max(1e-6, ...all.filter(Boolean).map((d) => Math.max(Math.abs(d!.ci[0]), Math.abs(d!.ci[1])))) * 1.1
  const verdictCls = (v?: string) => (v === 'routed better' ? 'el-ok' : v === 'global better' ? 'el-bad' : 'helper')
  const fmtD = (d: SegCompare['delta_auc']) => (d ? <>{sgn(d.delta, 4)} <span className="helper">[{sgn(d.ci[0], 4)}, {sgn(d.ci[1], 4)}]</span></> : '—')
  return (
    <div className="stack" style={{ gap: 'var(--space-16)' }} data-testid="seg-results">
      <div className="card">
        <div className="card-head"><div className="left"><h3 className="card-title">Routed vs Global, on the Same Holdout</h3><span className="helper" style={{ fontSize: 12 }}>{s.holdout_rows.toLocaleString()} rows none of the models saw · routing: {s.routing_rule}</span></div></div>
        <div className="card navy explain-hero" style={{ margin: 0, borderRadius: 0 }} data-testid="seg-hero">
          <HeroCell label={`routed · ${H}`} sub={`global ${fix(o.global[key] ?? null, 4)}`}>{fix(o.routed[key] ?? null, 4)}</HeroCell>
          <HeroCell label={bin ? 'ΔAUC overall' : 'ΔMSE overall'} sub={od ? `95% CI [${sgn(od.ci[0], 4)}, ${sgn(od.ci[1], 4)}] · ${o.verdict}` : o.verdict} subClass={verdictCls(o.verdict)}>{sgn(od?.delta, 4)}</HeroCell>
          <HeroCell label="routed segments" sub={`of ${s.segments.length}; the rest use the global model`}>{`${s.routes.length}`}</HeroCell>
          <HeroCell label="segment model better" sub={s.wins.length ? s.wins.join(', ') : 'on no segment (CI excludes 0)'} subClass={s.wins.length ? 'el-ok' : undefined}>{`${s.wins.length}`}</HeroCell>
          <HeroCell label="global better" sub={s.losses.length ? s.losses.join(', ') : 'on no segment'} subClass={s.losses.length ? 'el-bad' : undefined}>{`${s.losses.length}`}</HeroCell>
          {bin && <HeroCell label="threshold t" sub="one t for every model (F7 cost c_FN = 10·c_FP)">{fix(s.threshold, 4)}</HeroCell>}
        </div>
        <div className="card-body stack" style={{ gap: 'var(--space-8)' }}>
          <Formula seg={s.segment_column} binary={bin} />
          <p className="helper" style={{ fontSize: 12.5 }}>
            f<sub>G</sub> is the global model (every training row, {s.segment_column} included); f<sub>k</sub> the model trained only on rows with {s.segment_column} = k; f<sub>R</sub> the routed version.
            {bin
              ? <> AUC is the chance a random {String(s.positive_class)} row scores above a random other row. ΔAUC and its 95 % interval come from DeLong's paired variance, Var(Δ) = Var(A<sub>R</sub>) + Var(A<sub>G</sub>) − 2 Cov, since both models score the same rows.</>
              : <> e = ŷ − y on a holdout row; the 95 % interval of ΔMSE resamples rows (paired bootstrap, 1,000 draws).</>}
            {' '}Better means the interval excludes 0.
          </p>
        </div>
      </div>
      <div className="card" data-testid="seg-table">
        <div className="card-head"><div className="left"><h3 className="card-title">Per Segment</h3><span className="helper" style={{ fontSize: 12 }}>each segment's own model against the global model on that segment's holdout rows</span></div></div>
        <div className="table-wrap"><table className="table compact">
          <thead><tr><th>{s.segment_column}</th><th className="r">train rows</th><th className="r">holdout</th><th className="r">global {H}</th><th className="r">own {H}</th><th className="r">{bin ? 'ΔAUC' : 'ΔMSE'} [95% CI]</th><th style={{ width: '16%' }}>0-centred</th><th>verdict</th><th title="Which model serves the segment; the number is its tuning-slice loss, own vs global">scored by</th></tr></thead>
          <tbody>
            {s.segments.map((r) => (
              <tr key={r.value} data-testid="seg-row">
                <td className="mono">{r.value}</td><td className="r mono">{r.train_rows.toLocaleString()}</td><td className="r mono">{r.holdout_rows.toLocaleString()}{bin && r.own?.rate != null ? <span className="helper"> · {pct(r.own.rate)}</span> : null}</td>
                <td className="r mono">{fix(r.own?.global[key] ?? r.routed_cmp?.global[key] ?? null, 4)}</td><td className="r mono">{r.own ? fix(r.own.routed[key] ?? null, 4) : '—'}</td>
                <td className="r mono">{r.own ? fmtD(r.own[dk] as SegCompare['delta_auc']) : '—'}</td>
                <td><CiBar d={r.own?.[dk] as SegCompare['delta_auc']} span={span} better={bin ? 'up' : 'down'} /></td>
                <td className={verdictCls(r.own?.verdict)}>{r.own?.verdict ?? 'no own model'}</td>
                <td title={r.route_why} className="nowrap"><b>{r.routed ? 'own model' : 'global'}</b></td>
              </tr>))}
            <tr className="seg-total"><td><b>overall · routed</b></td><td className="r mono">{s.global.rows.toLocaleString()}</td><td className="r mono">{o.n.toLocaleString()}</td><td className="r mono">{fix(o.global[key] ?? null, 4)}</td><td className="r mono">{fix(o.routed[key] ?? null, 4)}</td>
              <td className="r mono">{fmtD(od)}</td><td><CiBar d={od} span={span} better={bin ? 'up' : 'down'} /></td><td className={verdictCls(o.verdict)}>{o.verdict}</td><td className="helper nowrap">{s.routes.length} own + global</td></tr>
            <tr className="seg-total"><td>overall · every segment model</td><td /><td className="r mono">{s.overall_all_segment_models.n.toLocaleString()}</td><td className="r mono">{fix(s.overall_all_segment_models.global[key] ?? null, 4)}</td><td className="r mono">{fix(s.overall_all_segment_models.routed[key] ?? null, 4)}</td>
              <td className="r mono">{fmtD(s.overall_all_segment_models[dk] as SegCompare['delta_auc'])}</td><td><CiBar d={s.overall_all_segment_models[dk] as SegCompare['delta_auc']} span={span} better={bin ? 'up' : 'down'} /></td><td className={verdictCls(s.overall_all_segment_models.verdict)}>{s.overall_all_segment_models.verdict}</td><td className="helper nowrap" title="Every segment on its own model, whatever the tuning slice said: shown so the routing choice can be judged">not routed</td></tr>
          </tbody>
        </table></div>
        <div className="card-body"><details className="an-adv" data-testid="seg-models">
          <summary className="micro-label">The models · global {s.global.best_model} · {s.segments.filter((r) => r.model).length} segment models</summary>
          <div className="table-wrap"><table className="table compact">
            <thead><tr><th>model</th><th className="r">rows</th><th>best model</th><th className="r">models fitted</th><th className="r">fit s</th>{s.tuning_rows > 0 && <th className="r">tuning loss: global → own</th>}</tr></thead>
            <tbody>
              <tr><td><b>global</b></td><td className="r mono">{s.global.rows.toLocaleString()}</td><td className="mono">{s.global.best_model}</td><td className="r mono">{s.global.n_models}</td><td className="r mono">{fix(s.global.fit_seconds, 0)}</td>{s.tuning_rows > 0 && <td />}</tr>
              {s.segments.map((r) => <tr key={r.value}><td className="mono">{s.segment_column} = {r.value}</td><td className="r mono">{r.train_rows.toLocaleString()}</td><td className="mono">{r.best_model ?? <span className="helper">{r.why}</span>}</td><td className="r mono">{r.n_models ?? '—'}</td><td className="r mono">{fix(r.fit_seconds ?? null, 0)}</td>
                {s.tuning_rows > 0 && <td className="r mono">{r.tuning ? `${fix(r.tuning.global, 4)} → ${fix(r.tuning.segment, 4)}` : '—'}</td>}</tr>)}
            </tbody>
          </table></div>
          <p className="helper" style={{ fontSize: 12 }}>Same config for every model: AutoGluon {run.config.presets ?? 'medium_quality'}, {run.config.time_limit ?? '—'} s, eval metric {s.eval_metric}. {s.unseen_in_training.length ? `Holdout values never seen in training (${s.unseen_in_training.join(', ')}) use the global model. ` : ''}Unknown {s.segment_column} values at scoring time use the global model too.</p>
        </details></div>
      </div>
    </div>
  )
}
