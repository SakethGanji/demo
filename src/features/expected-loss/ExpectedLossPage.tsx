import { useEffect, useState } from 'react'
import { flows, type ElResult, type ElRun, type ElScenario, type ElShock } from '@/shared/api/flows'
import { KindFlow, NumField, type KindSpec } from '@/shared/components/KindFlow'
import { HeroCell } from '@/shared/components/Status'
import { fix, pct } from '@/shared/lib/format'
import { Segmented } from '@/shared/primitives/Segmented'

/** Money, compact: 3.37 M, 412 k, 950. */
export const money = (v: number | null | undefined) => {
  if (v == null || !Number.isFinite(v)) return '—'
  const a = Math.abs(v)
  return a >= 1e9 ? `${(v / 1e9).toFixed(2)} B` : a >= 1e6 ? `${(v / 1e6).toFixed(2)} M` : a >= 1e4 ? `${(v / 1e3).toFixed(0)} k` : v.toFixed(0)
}
const signed = (v: number | null | undefined, f: (x: number) => string) => (v == null ? '—' : `${v >= 0 ? '+' : '−'}${f(Math.abs(v))}`)
const MODELS = [
  { key: 'pd', name: 'PD', what: 'probability of default', rows: 'every loan', fit: 'binary · log loss' },
  { key: 'lgd', name: 'LGD', what: 'share of the exposure lost', rows: 'defaulted loans', fit: 'regression · RMSE, clipped to [0, 1]' },
  { key: 'ead', name: 'EAD', what: 'amount owed at default', rows: 'defaulted loans', fit: 'regression · RMSE, ≥ 0' },
] as const

function Formula() {
  return (
    <div className="navy-well" style={{ padding: 'var(--space-12) var(--space-16)', minHeight: 0 }}>
      <div className="tp-eq" style={{ fontSize: 14 }} data-testid="el-formula">EL<sub>i</sub> = PD<sub>i</sub> · LGD<sub>i</sub> · EAD<sub>i</sub> &nbsp;·&nbsp; ΣEL = Σ<sub>i</sub> EL<sub>i</sub> &nbsp;·&nbsp; realised L<sub>i</sub> = y<sub>i</sub> · LGD<sub>i</sub> · EAD<sub>i</sub></div>
    </div>
  )
}

/**
 * G3 · Expected loss, a choice inside Build: the setup, pre-filled from the G0 column roles (default flag, LGD, EAD, segments,
 * what to leave out). The run (Models): PD × LGD × EAD on the holdout against the realised loss, the breakdown by segment and the
 * stress scenarios. Registry → Results shows the same view.
 */
export const EL_SPEC: KindSpec = {
  id: 'expected_loss', title: 'Expected Loss', testId: 'el',
  sub: 'PD × LGD × EAD per loan: three AutoGluon models, checked against the realised loss, with stress scenarios.',
  detect: flows.el.detect, create: (b) => flows.el.create(b as never),
  roles: ([['pd_column', 'default flag y (PD target)', 'pd'], ['lgd_column', 'loss given default, 0–1', 'lgd'], ['ead_column', 'exposure at default', 'ead']] as const).map(([key, label, urlParam]) => (
    { key, label, urlParam, testId: `el-${key}`, options: (ds) => ds.profile.columns.map((c) => c.name), emptyHint: 'nothing detected: pick a column' })),
  rows: ({ d, det, ds, pick, setPick }) => (
    <tr><td>segments (breakdown)</td><td colSpan={2}>
      <div className="row" style={{ gap: 6, flexWrap: 'wrap' }}>
        {ds.profile.columns.filter((c) => (c.type === 'category' && c.unique <= 20) || d.segments.includes(c.name)).map((c) => {
          const on = d.segments.includes(c.name)
          return <button key={c.name} className={`chip-btn ${on ? 'on' : ''}`} aria-pressed={on} title={(det.evidence[c.name] ?? []).join(', ')}
            onClick={() => setPick({ ...pick, segments: on ? d.segments.filter((x: string) => x !== c.name) : [...d.segments, c.name] })}>{on ? '✓ ' : ''}{c.name}</button>
        })}
      </div></td></tr>),
  leftOut: ({ d }) => <>IDs and leaks; {d.split === 'random' ? 'random holdout' : `${d.split} holdout by ${d.time_column ?? d.group_column}`}</>,
  Formula: () => <Formula />,
  advanced: { initial: () => ({ limit: 60 }), summary: (o) => `${o.limit} s per model`,
    Body: ({ o, set }) => <><NumField label="time per model (s)" value={o.limit} set={(limit) => set({ limit })} min={10} max={3600} testId="el-limit" /><span className="helper" style={{ fontSize: 12 }}>AutoGluon medium_quality; each model gets this budget.</span></> },
  explain: () => <>PD learns from every loan; LGD and EAD only from loans that defaulted (that is where they are known), then predict for every loan "if it defaulted". All three share one holdout, so ΣEL is checked against the loss that actually happened on loans none of the models saw.</>,
  rowsWord: () => 'loans',
  startLabel: () => 'Train PD, LGD and EAD →',
  canStart: ({ d }) => !!(d.pd_column && d.lgd_column && d.ead_column),
  body: ({ d, o }) => ({ pd_column: d.pd_column, lgd_column: d.lgd_column, ead_column: d.ead_column, segments: d.segments, drop_columns: d.drop_columns, time_limit: o.limit }),
  running: { events: 'el', line: (run) => `Training three models, ${run.config.time_limit ?? '—'} s each`,
    steps: () => MODELS.map((m) => ({ key: m.key, label: <><b>{m.name}</b> {m.what}<span className="helper"> · {m.rows}</span></> })) },
  meta: (run) => `PD target ${run.config.label}, LGD ${run.config.lgd_column}, EAD ${run.config.ead_column}`,
  registerName: (run) => `${run.dataset_id}.expected-loss`,
}

export const ExpectedLossPage = ({ go }: { go: (r: string) => void }) => <KindFlow spec={EL_SPEC} go={go} />

// ---------------------------------------------------------------------------------------------- results (shared)

/** The EL results: used by the run page and by Registry → Expected Loss. */
export function ElResults({ run, onChanged }: { run: ElRun; onChanged: () => void }) {
  const el = run.expected_loss as ElResult
  const p = el.portfolio
  const c = el.components
  const y1 = el.columns.positive_class
  return (
    <div className="stack" style={{ gap: 'var(--space-16)' }} data-testid="el-results">
      <div className="card">
        <div className="card-head"><div className="left"><h3 className="card-title">Holdout: Expected vs Realised Loss</h3><span className="helper" style={{ fontSize: 12 }}>{p.n.toLocaleString()} loans none of the models saw · {p.defaults.toLocaleString()} defaulted</span></div></div>
        <div className="card navy explain-hero" style={{ margin: 0, borderRadius: 0 }} data-testid="el-hero">
          <HeroCell label="ΣEL · expected" sub={`${pct(p.el_rate, 2)} of ΣEAD̂ ${money(p.ead_total)}`}>{money(p.el)}</HeroCell>
          <HeroCell label="Σ realised loss" sub={`${p.defaults} defaults · rate ${pct(p.default_rate)}`}>{money(p.realised)}</HeroCell>
          <HeroCell label="ΣEL / Σ realised" sub={`95% CI [${fix(p.ratio_ci[0])}, ${fix(p.ratio_ci[1])}] · ${p.within_10pct ? 'within ±10%' : 'outside ±10%'}`} subClass={p.within_10pct ? 'el-ok' : 'el-bad'}>{fix(p.ratio)}</HeroCell>
          <HeroCell label="PD · AUC" sub={`mean PD̂ ${pct(c.pd.mean_pd)} vs ${pct(c.pd.default_rate)} observed`}>{fix(c.pd.auc)}</HeroCell>
          <HeroCell label="LGD · MAE" sub={`R² ${fix(c.lgd.r2 ?? null, 2)} on ${c.lgd.n ?? 0} defaults`}>{fix(c.lgd.mae ?? null)}</HeroCell>
          <HeroCell label="EAD · R²" sub={`MAE ${money(c.ead.mae)}`}>{fix(c.ead.r2 ?? null)}</HeroCell>
        </div>
        <div className="card-body stack" style={{ gap: 'var(--space-8)' }}>
          <Formula />
          <p className="helper" style={{ fontSize: 12.5 }}>
            PD<sub>i</sub> = P({el.columns.pd} = {String(y1)} | x<sub>i</sub>) from the PD model; LGD<sub>i</sub> ∈ [0, 1] is the predicted share of the exposure lost if loan i defaults ({el.columns.lgd}); EAD<sub>i</sub> ≥ 0 the predicted amount owed then ({el.columns.ead}).
            y<sub>i</sub> = 1 if loan i defaulted; realised L<sub>i</sub> uses its recorded {el.columns.lgd} and {el.columns.ead}. The 95 % interval of ΣEL / Σ realised resamples the holdout loans (bootstrap, 400 draws).
          </p>
          <details className="an-adv" data-testid="el-models">
            <summary className="micro-label">The three models · {MODELS.map((m) => `${m.name} ${c[m.key].best_model}`).join(' · ')}</summary>
            <div className="table-wrap"><table className="table compact">
              <thead><tr><th>model</th><th>predicts</th><th>learns from</th><th>fit</th><th className="r">rows</th><th>best model</th><th className="r">holdout: predicted</th><th className="r">actual</th></tr></thead>
              <tbody>{MODELS.map((m) => {
                const x = c[m.key]
                const pr = m.key === 'pd' ? pct(x.mean_pd) : m.key === 'lgd' ? fix(x.mean_pred ?? null) : money(x.mean_pred)
                const ac = m.key === 'pd' ? pct(x.default_rate) : m.key === 'lgd' ? fix(x.mean_true ?? null) : money(x.mean_true)
                return <tr key={m.key}><td><b>{m.name}</b></td><td className="mono">{x.label}</td><td>{m.rows}</td><td className="helper">{m.fit}</td><td className="r mono">{x.train_rows.toLocaleString()}</td><td className="mono">{x.best_model}</td><td className="r mono">{pr}</td><td className="r mono">{ac}</td></tr>
              })}</tbody>
            </table></div>
            <p className="helper" style={{ fontSize: 12 }}>Predicted vs actual are means on the holdout: PD over every loan; LGD and EAD over the loans that defaulted.</p>
          </details>
        </div>
      </div>
      <Segments el={el} />
      <Stress run={run} onChanged={onChanged} />
    </div>
  )
}

function Segments({ el }: { el: ElResult }) {
  const cols = el.segment_columns.filter((s) => el.segments[s])
  const [col, setCol] = useState(cols[0] ?? '')
  if (!cols.length) return null
  const rows = el.segments[col] ?? []
  const max = Math.max(...rows.map((r) => r.share))
  return (
    <div className="card" data-testid="el-segments">
      <div className="card-head"><div className="left"><h3 className="card-title">EL By Segment</h3><span className="helper" style={{ fontSize: 12 }}>holdout loans; EL rate = ΣEL / ΣEAD̂</span></div>
        {cols.length > 1 && <Segmented options={cols} value={col} onChange={setCol} />}</div>
      <div className="table-wrap"><table className="table compact">
        <thead><tr><th>{col}</th><th className="r">loans</th><th className="r">defaults</th><th className="r">mean PD̂</th><th className="r">mean LGD̂</th><th className="r">ΣEL</th><th style={{ width: '18%' }}>share of ΣEL</th><th className="r">EL rate</th><th className="r">Σ realised</th><th className="r">EL / realised</th></tr></thead>
        <tbody>{rows.map((r) => (
          <tr key={r.value}><td className="mono">{r.value}</td><td className="r mono">{r.n.toLocaleString()}</td><td className="r mono">{r.defaults ?? '—'}</td><td className="r mono">{pct(r.mean_pd)}</td><td className="r mono">{fix(r.mean_lgd, 2)}</td>
            <td className="r mono">{money(r.el)}</td><td><div className="el-bar"><span style={{ width: `${(r.share / (max || 1)) * 100}%` }} /></div><span className="helper mono" style={{ fontSize: 11 }}>{pct(r.share)}</span></td>
            <td className="r mono">{pct(r.el_rate, 2)}</td><td className="r mono">{money(r.realised)}</td><td className="r mono">{fix(r.ratio ?? null, 2)}</td></tr>
        ))}</tbody>
      </table></div>
    </div>
  )
}

function Stress({ run, onChanged }: { run: ElRun; onChanged: () => void }) {
  const el = run.expected_loss as ElResult
  const all: ElScenario[] = [...el.scenarios, ...run.custom_scenarios.filter((c) => !el.scenarios.some((x) => x.name === c.name))]
  const [sel, setSel] = useState(all[0]?.name ?? '')
  const s = all.find((x) => x.name === sel) ?? all[0]
  const segCols = el.segment_columns
  const [col, setCol] = useState(segCols[0] ?? '')
  return (
    <div className="card" data-testid="el-stress">
      <div className="card-head"><div className="left"><h3 className="card-title">Stress Scenarios</h3><span className="helper" style={{ fontSize: 12 }}>shock the inputs of the {el.holdout_rows.toLocaleString()} holdout loans, re-score PD, LGD and EAD, compare ΣEL</span></div></div>
      <div className="card-body stack" style={{ gap: 'var(--space-12)' }}>
        <div className="navy-well" style={{ padding: 'var(--space-8) var(--space-16)', minHeight: 0 }}>
          <div className="tp-eq" style={{ fontSize: 13 }}>ΔEL = Σ<sub>i</sub> PD(x̃<sub>i</sub>)·LGD(x̃<sub>i</sub>)·EAD(x̃<sub>i</sub>) − Σ<sub>i</sub> PD(x<sub>i</sub>)·LGD(x<sub>i</sub>)·EAD(x<sub>i</sub>)</div>
        </div>
        <p className="helper" style={{ fontSize: 12 }}>x̃<sub>i</sub> is loan i after the shocks: −10% multiplies by 0.9; +15pp adds 0.15 to a share; values stay ≥ 0, and shares ≤ 1. The scenarios below were made from the column names; add your own underneath.</p>
        <div className="table-wrap"><table className="table compact" data-testid="el-scenarios">
          <thead><tr><th>scenario</th><th className="r">ΣEL before</th><th className="r">after</th><th className="r">ΔEL</th><th className="r">Δ%</th><th className="r">mean PD̂</th><th>expected</th></tr></thead>
          <tbody>{all.map((x) => (
            <tr key={x.name} className={`clickable ${x.name === s?.name ? 'selected' : ''}`} onClick={() => setSel(x.name)} data-testid="el-scenario">
              <td><b>{x.name}</b>{x.why && <div className="helper" style={{ fontSize: 11 }}>{x.why}</div>}</td>
              <td className="r mono">{money(x.base)}</td><td className="r mono">{money(x.stressed)}</td>
              <td className="r mono">{signed(x.delta, money)}</td><td className="r mono">{signed(x.delta_pct, (v) => `${v.toFixed(1)}%`)}</td>
              <td className="r mono">{pct(x.components.pd.base)} → {pct(x.components.pd.stressed)}</td>
              <td>{x.expect ? <span className={x.as_expected ? 'el-ok' : 'el-bad'}>{x.as_expected ? '✓' : '✕'} EL {x.expect}</span> : <span className="helper">—</span>}</td>
            </tr>))}</tbody>
        </table></div>
        {s && col && s.segments[col] && (
          <div data-testid="el-stress-segments">
            <div className="row" style={{ justifyContent: 'space-between' }}><span className="micro-label">{s.name} · by {col}{s.shocks.some((k) => k.clipped) ? ` · ${s.shocks.reduce((a, k) => a + k.clipped, 0)} values clipped to the range` : ''}</span>
              {segCols.length > 1 && <Segmented options={segCols} value={col} onChange={setCol} />}</div>
            <div className="table-wrap"><table className="table compact">
              <thead><tr><th>{col}</th><th className="r">loans</th><th className="r">ΣEL before</th><th className="r">after</th><th className="r">ΔEL</th><th className="r">Δ%</th></tr></thead>
              <tbody>{s.segments[col].map((r) => <tr key={r.value}><td className="mono">{r.value}</td><td className="r mono">{r.n.toLocaleString()}</td><td className="r mono">{money(r.base)}</td><td className="r mono">{money(r.stressed)}</td><td className="r mono">{signed(r.delta, money)}</td><td className="r mono">{signed(r.delta_pct, (v) => `${v.toFixed(1)}%`)}</td></tr>)}</tbody>
            </table></div>
          </div>)}
        <NewScenario run={run} onDone={(name) => { setSel(name); onChanged() }} />
      </div>
    </div>
  )
}

function NewScenario({ run, onDone }: { run: ElRun; onDone: (name: string) => void }) {
  const el = run.expected_loss as ElResult
  const [shocks, setShocks] = useState<ElShock[]>([])
  const [column, setColumn] = useState(el.inputs.find((c) => el.ranges[c]?.numeric) ?? el.inputs[0])
  const rng = el.ranges[column]
  const ops = rng?.numeric ? (rng.share ? ['pct', 'pp', 'add'] : ['pct', 'add']) : ['set']
  const [op, setOp] = useState<ElShock['op']>('pct')
  const [value, setValue] = useState('-10')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  useEffect(() => { if (!ops.includes(op)) { setOp(ops[0] as ElShock['op']); setValue(ops[0] === 'set' && rng && !rng.numeric ? rng.values[0] : '-10') } }, [column]) // eslint-disable-line react-hooks/exhaustive-deps
  const shock = (): ElShock => ({ column, op, value: op === 'set' && rng && !rng.numeric ? value : Number(value) })
  const runIt = async () => {
    const list = [...shocks, shock()]
    setBusy(true); setErr('')
    try { const r = await flows.el.stress(run.run_id, { shocks: list }); setShocks([]); onDone(r.name) } catch (e) { setErr((e as Error).message) } finally { setBusy(false) }
  }
  const unit = op === 'pct' ? '%' : op === 'pp' ? 'pp' : ''
  return (
    <div className="el-new" data-testid="el-new">
      <span className="micro-label">New scenario{shocks.length ? ` · ${shocks.map((k) => `${k.column} ${k.op} ${k.value}`).join(', ')} +` : ''}</span>
      <select className="input mono" value={column} onChange={(e) => setColumn(e.target.value)} data-testid="el-shock-col">{el.inputs.map((c) => <option key={c}>{c}</option>)}</select>
      <select className="input" value={op} onChange={(e) => setOp(e.target.value as ElShock['op'])} data-testid="el-shock-op">
        {ops.map((o) => <option key={o} value={o}>{{ pct: 'change by %', pp: 'add percentage points', add: 'add', set: 'set to' }[o]}</option>)}
      </select>
      {op === 'set' && rng && !rng.numeric
        ? <select className="input mono" value={value} onChange={(e) => setValue(e.target.value)}>{rng.values.map((v) => <option key={v}>{v}</option>)}</select>
        : <span className="row" style={{ gap: 4 }}><input className="input mono" value={value} onChange={(e) => setValue(e.target.value)} style={{ width: 80 }} data-testid="el-shock-value" />{unit}</span>}
      {rng?.numeric && <span className="helper" style={{ fontSize: 11 }}>range {fix(rng.min, 2)} → {fix(rng.max, 2)}</span>}
      <button className="btn sm" onClick={() => setShocks([...shocks, shock()])} disabled={busy}>+ another shock</button>
      <button className="btn sm" onClick={runIt} disabled={busy || value === ''} data-testid="el-shock-run">{busy ? 'Re-scoring…' : 'Run scenario'}</button>
      {err && <span className="helper" role="alert" style={{ color: 'var(--status-danger)' }}>✕ {err}</span>}
    </div>
  )
}
