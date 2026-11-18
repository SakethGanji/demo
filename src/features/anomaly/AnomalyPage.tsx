import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from '@/shared/api/client'
import { flows, type AnBatch, type AnRow, type AnScores, type AnTop, type FlowRun, type AnGroup } from '@/shared/api/flows'
import { useApi } from '@/shared/api/mode'
import type { DatasetDetail, DatasetSummary } from '@/shared/api/types'
import { LiveModels, Register } from '@/shared/components/FlowParts'
import { PlotFrame } from '@/shared/components/PlotFrame'
import { XYPlot, niceTicks, padDomain } from '@/shared/components/XYPlot'
import { useRunEvents } from '@/shared/hooks/useRunEvents'
import { ParamChip } from '@/shared/primitives/ParamChip'
import { ResonanceWave } from '@/shared/primitives/ResonanceWave'
import { StatusPill } from '@/shared/primitives/StatusPill'

const runParam = () => new URLSearchParams(location.hash.split('?')[1] ?? '').get('run')
const f3 = (v: number | null | undefined, d = 3) => (v == null || !Number.isFinite(v) ? '—' : v.toFixed(d))
const pct = (v: number | null | undefined, d = 1) => (v == null ? '—' : `${(v * 100).toFixed(d)}%`)
const EULER = 0.5772156649
const cn = (n: number) => (n <= 2 ? 1 : 2 * (Math.log(n - 1) + EULER) - (2 * (n - 1)) / n)
const val = (v: unknown) => (v == null ? '—' : typeof v === 'number' ? (Math.abs(v) >= 1000 ? v.toLocaleString(undefined, { maximumFractionDigits: 0 }) : String(Number(v.toPrecision(5)))) : String(v))

/** Anomaly detection without labels: IsolationForest (and LOF) on a table, the isolation score's distribution and
 *  threshold, the most anomalous rows with their reasons, and a batch scorer. */
export function AnomalyPage({ go }: { go: (r: string) => void }) {
  const { mode } = useApi()
  const [runId, setRunId] = useState<string | null>(runParam)
  useEffect(() => { const f = () => setRunId(runParam()); addEventListener('hashchange', f); return () => removeEventListener('hashchange', f) }, [])
  if (mode === 'mock') return (
    <div className="page"><div className="page-head"><div><h1 className="page-title">Anomaly Detection</h1></div></div>
      <div className="card card-body" data-testid="anomaly-mock"><p className="helper">▲ mock mode: the API is offline. Start it with <span className="mono">make dev</span>; this page then fits IsolationForest on the card-transactions sample or your CSV.</p></div></div>
  )
  if (mode === 'checking') return <div className="page"><div className="card card-body row"><ResonanceWave /> Connecting…</div></div>
  return runId ? <AnomalyRun key={runId} runId={runId} go={go} /> : <AnomalySetup />
}

function IsolationFormula({ psi, extra }: { psi: number; extra?: React.ReactNode }) {
  return (
    <div className="formula an-formula">
      <div className="eq">s(x, ψ) = 2<sup>−E[h(x)] / c(ψ)</sup>, &nbsp; c(ψ) = 2H(ψ−1) − 2(ψ−1)/ψ = <b className="mono">{cn(psi).toFixed(3)}</b></div>
      <dl className="where">
        <dt>h(x)</dt><dd>path length: splits a random isolation tree needs to isolate x (random feature, random cut)</dd>
        <dt>E[h(x)]</dt><dd>mean over the forest’s trees; short paths = easy to isolate = unusual</dd>
        <dt>ψ</dt><dd>rows sub-sampled per tree, ψ = min(256, n) = {psi}</dd>
        <dt>c(ψ)</dt><dd>average path of an unsuccessful binary-search-tree lookup in ψ rows; normalises E[h]</dd>
        <dt>H(i)</dt><dd>harmonic number ≈ ln i + 0.5772 (Euler’s γ)</dd>
        <dt>s</dt><dd>→ 1 isolated (anomalous); ≈ 0.5 or below normal. Flag when s ≥ s*, the (1 − c) quantile of s</dd>
      </dl>
      {extra}
    </div>
  )
}

// --------------------------------------------------------------------------- setup

function AnomalySetup() {
  const [datasets, setDatasets] = useState<DatasetSummary[]>([])
  const [dsId, setDsId] = useState('card-transactions')
  const [ds, setDs] = useState<DatasetDetail | null>(null)
  const [feats, setFeats] = useState<string[]>([])
  const [c, setC] = useState(0.015)
  const [trees, setTrees] = useState(300)
  const [lof, setLof] = useState(true)
  const [k, setK] = useState(20)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const [past, setPast] = useState<FlowRun[]>([])
  const [grp, setGrp] = useState<AnGroup>({ entity_column: null, time_column: null, relative_columns: [], source: '' })
  const [useGrp, setUseGrp] = useState(true)
  const ref = useRef<HTMLInputElement>(null)
  useEffect(() => { api.datasets.list().then(setDatasets).catch((e) => setErr(e.message)); flows.runs('anomaly').then(setPast).catch(() => {}) }, [])
  useEffect(() => {
    flows.anomaly.mapping(dsId).then((g) => {
      setGrp(g); setUseGrp(!!g.entity_column)
      api.datasets.get(dsId).then((d) => { setDs(d); setFeats(d.profile.columns.filter((x) => x.type !== 'id' && x.type !== 'text' && x.name !== g.entity_column && x.name !== g.time_column).map((x) => x.name)) }).catch((e) => setErr(e.message))
    }).catch((e) => setErr(e.message))
  }, [dsId])
  const n = ds?.rows ?? 0
  const psi = Math.min(256, n || 256)
  const start = async () => {
    setBusy(true); setErr('')
    try { const r = await flows.anomaly.create({ dataset_id: dsId, features: feats, contamination: c, n_estimators: trees, lof, lof_neighbors: k, source: 'anomaly-page',
      group_features: useGrp && !!grp.entity_column, entity_column: grp.entity_column, time_column: grp.time_column, relative_columns: grp.relative_columns }); location.hash = `#/anomaly?run=${r.id}` }
    catch (e) { setErr((e as Error).message) } finally { setBusy(false) }
  }
  const upload = async (f: File) => { try { const d = await api.datasets.upload(f); setDatasets(await api.datasets.list()); setDsId(d.id) } catch (e) { setErr((e as Error).message) } }
  const sorted = [...datasets].sort((a, b) => Number(b.kind === 'transactions') - Number(a.kind === 'transactions'))
  return (
    <div className="page anomaly" data-testid="anomaly-setup">
      <div className="page-head">
        <div><h1 className="page-title">Anomaly Detection</h1><p className="helper sub">Find unusual rows with no labels: scikit-learn IsolationForest, optionally checked against LocalOutlierFactor, with a reason for every flag.</p></div>
      </div>
      <div className="grid an-setup">
        <div className="card">
          <div className="card-head"><div className="left"><h3 className="card-title">1 · Data and features</h3></div></div>
          <div className="card-body stack" style={{ gap: 12 }}>
            <div className="row" style={{ flexWrap: 'wrap' }}>
              <select className="select" value={dsId} onChange={(e) => setDsId(e.target.value)} aria-label="Dataset" data-testid="an-dataset" style={{ minWidth: 260 }}>
                {sorted.map((d) => <option key={d.id} value={d.id}>{d.name} · {d.rows.toLocaleString()} rows</option>)}
              </select>
              <input ref={ref} type="file" accept=".csv,.tsv" hidden onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} />
              <button className="btn" onClick={() => ref.current?.click()}>Upload CSV…</button>
            </div>
            <div><span className="micro-label">features x ({feats.length})</span>
              <div className="row" style={{ flexWrap: 'wrap', gap: 4, marginTop: 4 }}>{(ds?.profile.columns ?? []).map((col) => {
                const on = feats.includes(col.name)
                return <button key={col.name} className={`chip-btn ${on ? 'on' : ''}`} disabled={col.type === 'text'} title={col.type}
                  onClick={() => setFeats(on ? feats.filter((f) => f !== col.name) : [...feats, col.name])}>{on ? '✓ ' : ''}{col.name} <span className="muted">{col.type}</span></button>
              })}</div>
              <p className="helper" style={{ fontSize: 12, marginTop: 6 }}>Numbers: median-imputed (+ a missing flag). Categories: one-hot of the 20 most frequent values. IDs and free text are left out by default.</p></div>
            <GroupFeatures ds={ds} grp={grp} setGrp={setGrp} on={useGrp} setOn={setUseGrp} />
          </div>
        </div>
        <div className="card">
          <div className="card-head"><div className="left"><h3 className="card-title">2 · Model</h3></div></div>
          <div className="card-body stack" style={{ gap: 12 }}>
            <IsolationFormula psi={psi} />
            <label className="an-slider"><span className="micro-label">contamination c (expected share of anomalies)</span>
              <input type="range" min={0.001} max={0.1} step={0.001} value={c} onChange={(e) => setC(Number(e.target.value))} data-testid="an-contamination" />
              <span className="mono">c = {c.toFixed(3)} → ≈ {Math.round(c * n).toLocaleString()} of {n.toLocaleString()} rows flagged</span></label>
            <div className="row" style={{ flexWrap: 'wrap', gap: 16 }}>
              <label className="col"><span className="micro-label">n_estimators T</span><input className="num-input" type="number" min={10} value={trees} onChange={(e) => setTrees(Math.max(10, Number(e.target.value)))} /></label>
              <label className="row" style={{ marginTop: 16 }}><input type="checkbox" checked={lof} onChange={(e) => setLof(e.target.checked)} /> also LocalOutlierFactor</label>
              {lof && <label className="col"><span className="micro-label">neighbours k</span><input className="num-input" type="number" min={2} value={k} onChange={(e) => setK(Math.max(2, Number(e.target.value)))} /></label>}
            </div>
            {lof && <p className="helper" style={{ fontSize: 12 }}>LOF<sub>k</sub>(x) = mean<sub>o∈N<sub>k</sub>(x)</sub> lrd<sub>k</sub>(o) / lrd<sub>k</sub>(x): local density of x against its k neighbours’ (robust-scaled features). ≫ 1 = in a sparser region than its neighbours.</p>}
            {err && <p className="helper" role="alert" style={{ color: 'var(--status-danger)' }}>✕ {err}</p>}
            <div className="row"><span className="spacer" /><button className="btn primary" disabled={busy || !feats.length} onClick={start} data-testid="an-start">{busy ? 'Starting…' : 'Find Anomalies'}</button></div>
          </div>
        </div>
      </div>
      {past.length > 0 && <div className="card">
        <div className="card-head"><div className="left"><h3 className="card-title">Anomaly runs</h3></div></div>
        <div className="table-wrap"><table className="table"><thead><tr><th>run</th><th>name</th><th>status</th><th className="r">flagged</th><th className="r">s*</th><th /></tr></thead>
          <tbody>{past.slice(0, 10).map((r) => <tr key={r.id}><td className="table-cell">{r.id}</td><td>{r.name}</td><td><StatusPill status={r.status === 'succeeded' ? 'success' : r.status === 'failed' ? 'danger' : 'info'}>{r.status}</StatusPill></td>
            <td className="r table-cell">{r.anomaly?.n_flagged ?? '—'}</td><td className="r table-cell">{f3(r.anomaly?.threshold, 4)}</td>
            <td><button className="chip-btn" onClick={() => { location.hash = `#/anomaly?run=${r.id}` }}>Open</button></td></tr>)}</tbody></table></div>
      </div>}
    </div>
  )
}

/** Group-relative features: amounts that are unusual only for this card / account. Mapped from the sample's spec or
 *  guessed from names; the user can change the entity, the time column and which amounts are compared. */
function GroupFeatures({ ds, grp, setGrp, on, setOn }: { ds: DatasetDetail | null; grp: AnGroup; setGrp: (g: AnGroup) => void; on: boolean; setOn: (v: boolean) => void }) {
  const cols = ds?.profile.columns ?? []
  const nums = cols.filter((c) => c.type === 'number')
  return (
    <div className="an-group" data-testid="an-group">
      <label className="row" style={{ gap: 8 }}><input type="checkbox" checked={on && !!grp.entity_column} disabled={!grp.entity_column} onChange={(e) => setOn(e.target.checked)} data-testid="an-group-on" />
        <b style={{ fontSize: 13 }}>Group-relative features</b><span className="helper" style={{ fontSize: 12 }}>{grp.source ? `mapping: ${grp.source}` : ''}</span></label>
      <div className="row" style={{ flexWrap: 'wrap', gap: 12 }}>
        <label className="col"><span className="micro-label">entity e</span>
          <select className="select sm" value={grp.entity_column ?? ''} onChange={(e) => { setGrp({ ...grp, entity_column: e.target.value || null }); setOn(!!e.target.value) }} data-testid="an-entity">
            <option value="">none</option>{cols.filter((c) => c.type !== 'number' && c.type !== 'text').map((c) => <option key={c.name} value={c.name}>{c.name}</option>)}</select></label>
        <label className="col"><span className="micro-label">time t</span>
          <select className="select sm" value={grp.time_column ?? ''} onChange={(e) => setGrp({ ...grp, time_column: e.target.value || null })} data-testid="an-time">
            <option value="">none</option>{cols.filter((c) => c.type !== 'number').map((c) => <option key={c.name} value={c.name}>{c.name}</option>)}</select></label>
        <div className="col"><span className="micro-label">compared within e</span>
          <div className="row" style={{ gap: 4, flexWrap: 'wrap' }}>{nums.map((c) => {
            const sel = grp.relative_columns.includes(c.name)
            return <button key={c.name} className={`chip-btn ${sel ? 'on' : ''}`} onClick={() => setGrp({ ...grp, relative_columns: sel ? grp.relative_columns.filter((x) => x !== c.name) : [...grp.relative_columns, c.name] })}>{sel ? '✓ ' : ''}{c.name}</button>
          })}</div></div>
      </div>
      {on && grp.entity_column && <div className="formula an-gformula">
        <div className="eq">r<sub>i</sub> = x<sub>i</sub> / x̄<sub>e,−i</sub>, &nbsp; z<sub>i</sub> = (x<sub>i</sub> − x̄<sub>e,−i</sub>) / s̃<sub>e,−i</sub>{grp.time_column ? <>, &nbsp; Δt<sub>i</sub> = ln(1 + hours since e’s previous row)</> : null}</div>
        <dl className="where">
          <dt>e</dt><dd>the row’s {grp.entity_column}: its own history is the baseline</dd>
          <dt>x</dt><dd>{grp.relative_columns.join(', ') || '(pick a column)'}</dd>
          <dt>x̄<sub>e,−i</sub></dt><dd>mean of x over e’s other rows (leave-one-out, so a spike does not raise its own baseline)</dd>
          <dt>s̃<sub>e,−i</sub></dt><dd>their standard deviation, floored at 0.1·|x̄<sub>e,−i</sub>|; one-row entities have no baseline (imputed + flagged)</dd>
        </dl>
      </div>}
    </div>
  )
}

// --------------------------------------------------------------------------- the run

function AnomalyRun({ runId, go }: { runId: string; go: (r: string) => void }) {
  const live = useRunEvents(runId)
  const [run, setRun] = useState<FlowRun | null>(null)
  const [scores, setScores] = useState<AnScores | null>(null)
  const [c, setC] = useState<number | null>(null)
  const [top, setTop] = useState<AnTop | null>(null)
  const [sel, setSel] = useState<AnRow | null>(null)
  const [err, setErr] = useState('')
  useEffect(() => { flows.run(runId).then((r) => { setRun(r); if (r.anomaly && c == null) setC(r.anomaly.contamination) }).catch((e) => setErr(e.message)) }, [runId, live.done]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (run?.status === 'succeeded') flows.anomaly.scores(runId).then(setScores).catch((e) => setErr(e.message)) }, [run?.status, runId])
  useEffect(() => {
    if (run?.status !== 'succeeded' || c == null) return
    const t = setTimeout(() => flows.anomaly.top(runId, c, 150).then((x) => { setTop(x); setSel((s) => s ?? x.rows[0]) }).catch((e) => setErr(e.message)), 180)
    return () => clearTimeout(t)
  }, [c, run?.status, runId])

  if (err && !run) return <div className="page"><div className="card card-body" role="alert" style={{ color: 'var(--status-danger)' }}>✕ {err}</div></div>
  if (!run) return <div className="page"><div className="card card-body row"><ResonanceWave /> Reading anomaly run {runId}…</div></div>
  const a = run.anomaly
  const done = run.status === 'succeeded' && a
  // threshold for the slider's c, from 201 quantiles of s (exact counts come back from the API)
  const sStar = top?.threshold ?? a?.threshold ?? null
  const truth = top?.truth ?? a?.truth ?? null
  const group = ((run.data as Record<string, unknown> | null)?.group as GroupInfo | undefined) ?? null
  return (
    <div className="page anomaly" data-testid="anomaly-run" data-run={run.id} data-status={run.status}>
      <div className="page-head">
        <div><h1 className="page-title">Anomalies · {run.name}</h1>
          <p className="helper sub"><span className="mono">{run.id}</span> · {(run.data?.rows as number)?.toLocaleString() ?? '…'} rows · {(run.features ?? []).length} features → {a?.columns_out ?? '…'} columns · IsolationForest{a?.lof ? ' + LOF' : ''}</p></div>
        <div className="row"><button className="btn" onClick={() => { location.hash = '#/anomaly' }}>New detection</button>
          {done && <a className="btn" href={`/api/anomaly/runs/${run.id}/scores.csv`} download>Download scores.csv</a>}
          {done && <Register runId={run.id} go={go} name={`${run.dataset_id}.anomaly`} />}</div>
      </div>

      <div className="card navy explain-hero">
        <div className="eh-cell lead"><span className="micro-label">{done ? 'flagged at s ≥ s*' : live.stage}</span>
          <b className="mono" data-testid="an-flagged">{done ? (top?.n_flagged ?? a.n_flagged).toLocaleString() : <ResonanceWave />}</b>
          <small>{done ? `of n = ${(top?.n_rows ?? 0).toLocaleString()} rows at contamination c = ${(c ?? a.contamination).toFixed(3)}` : 'fitting'}</small></div>
        {done && <>
          <Cell k="threshold s*" v={f3(sStar, 4)} d="(1 − c) quantile of s" />
          <Cell k="ψ · c(ψ)" v={`${a.psi} · ${a.c_n.toFixed(3)}`} d="rows per tree · mean path" />
          <Cell k="trees T" v={`${a.n_trees}`} d={`max depth ⌈log₂ ψ⌉ = ${a.mean_depth.toFixed(0)}`} />
          <Cell k="s range" v={`${a.distribution.min.toFixed(3)}–${a.distribution.max.toFixed(3)}`} d={`median ${a.distribution.quantiles['0.5'].toFixed(3)}`} />
          {a.lof && <Cell k="LOF vs IF" v={`ρ = ${a.lof.spearman_vs_iforest.toFixed(2)}`} d={`Spearman; ${a.lof.overlap_top} shared in the top ${a.n_flagged}`} />}
        </>}
      </div>

      {!done && <LiveModels live={live} metric="isolation score" />}
      {run.status === 'failed' && <div className="card card-body" role="alert" style={{ color: 'var(--status-danger)' }}>✕ {run.error}</div>}

      {done && <>
        <div className="grid an-two">
          <Distribution a={a} scores={scores} c={c ?? a.contamination} setC={setC} sStar={sStar} />
          <div className="stack" style={{ gap: 16 }}>
            <IsolationFormula psi={a.psi} />
            {group && <GroupCard g={group} />}
            {truth && <TruthCard truth={truth} lofTruth={a.truth_lof} group={group} />}
          </div>
        </div>
        <div className="grid an-two">
          <TopTable top={top} sel={sel} setSel={setSel} />
          {sel && <Reason row={sel} />}
        </div>
        {scores?.scatter && <LofScatter scores={scores} sStar={sStar ?? 0} />}
        <Batch runId={run.id} />
        <div className="chips-row"><ParamChip sym="run" value={run.id} /><ParamChip sym="ψ" value={String(a.psi)} /><ParamChip sym="T" value={String(a.n_trees)} /><ParamChip sym="c" value={(c ?? a.contamination).toFixed(3)} />
          <span className="helper" style={{ fontSize: 12 }}>Model: <span className="mono">joblib.load("{run.predictor_path}/model.joblib")</span> (preprocessing, IsolationForest{a.lof ? ', LOF' : ''}, s*, robust stats).</span><StatusPill status="success">live</StatusPill></div>
      </>}
    </div>
  )
}

type GroupInfo = { entity_column: string; time_column: string | null; relative_columns: string[]; added: string[]; entities: number; rows_without_baseline: number }

/** The group-relative features this run added, with their formulas bound to the run's entity and columns. */
function GroupCard({ g }: { g: GroupInfo }) {
  return (
    <div className="formula an-formula" data-testid="an-group-card">
      <div className="eq">r<sub>i</sub> = x<sub>i</sub> / x̄<sub>e,−i</sub>, &nbsp; z<sub>i</sub> = (x<sub>i</sub> − x̄<sub>e,−i</sub>) / s̃<sub>e,−i</sub>{g.time_column ? <>, &nbsp; Δt<sub>i</sub> = ln(1 + h<sub>i</sub>)</> : null}</div>
      <dl className="where">
        <dt>e</dt><dd>{g.entity_column}: {g.entities.toLocaleString()} entities; {g.rows_without_baseline} rows have no other row of their entity (no baseline: imputed + a missing flag)</dd>
        <dt>x</dt><dd>{g.relative_columns.join(', ')}</dd>
        <dt>x̄, s̃</dt><dd>mean and standard deviation of x over e’s other rows (leave-one-out), s̃ floored at 0.1·|x̄|</dd>
        {g.time_column && <><dt>h<sub>i</sub></dt><dd>hours since e’s previous row by {g.time_column}</dd></>}
        <dt>columns</dt><dd className="mono" style={{ fontSize: 11.5 }}>{g.added.join(' · ')}</dd>
      </dl>
    </div>
  )
}

function Cell({ k, v, d }: { k: string; v: string; d: string }) {
  return <div className="eh-cell"><span className="micro-label">{k}</span><b className="mono">{v}</b><small className="math-s">{d}</small></div>
}

function Distribution({ a, scores, c, setC, sStar }: { a: NonNullable<FlowRun['anomaly']>; scores: AnScores | null; c: number; setC: (v: number) => void; sStar: number | null }) {
  const d = scores ?? { edges: a.distribution.edges, counts: a.distribution.counts, n: 0, sorted_sample: [] }
  const ymax = Math.max(...d.counts) * 1.08
  const bars = d.counts.map((n, i) => ({ x0: d.edges[i], x1: d.edges[i + 1], y: n, color: sStar != null && d.edges[i] >= sStar ? 'var(--status-danger)' : 'var(--viz-b)', opacity: 0.75 }))
  return (
    <PlotFrame title="Score distribution" chips={<span className="mono helper" style={{ fontSize: 11.5 }}>{d.counts.reduce((x, y) => x + y, 0).toLocaleString()} rows · {d.counts.length} bins</span>}
      legend={[{ name: 'rows below s*', color: 'var(--viz-b)', data: [] }, { name: 'flagged (s ≥ s*)', color: 'var(--status-danger)', data: [] }]}
      caption={<span>Count of rows per isolation-score bin; the line is s* for the contamination c. Drag the slider to move it.</span>}>
      <XYPlot height={250} series={[]} bars={bars} testId="an-hist"
        x={{ min: d.edges[0], max: d.edges[d.edges.length - 1], ticks: niceTicks(d.edges[0], d.edges[d.edges.length - 1], 6), title: 's(x, ψ)', fmt: (v) => v.toFixed(2) }}
        y={{ min: 0, max: ymax, ticks: niceTicks(0, ymax, 4), title: 'rows', fmt: (v) => v.toFixed(0) }}
        refs={sStar != null ? [{ x: sStar, label: `s* = ${sStar.toFixed(3)}`, color: 'var(--status-danger)', strong: true, dash: '0' }, { x: 0.5, label: 's = 0.5', dash: '3 3' }] : []} />
      <label className="an-slider" style={{ padding: '0 var(--space-16) var(--space-8)' }}><span className="micro-label">contamination c</span>
        <input type="range" min={0.001} max={0.1} step={0.001} value={c} onChange={(e) => setC(Number(e.target.value))} data-testid="an-c-slider" />
        <span className="mono">c = {c.toFixed(3)}</span></label>
    </PlotFrame>
  )
}

function TruthCard({ truth, lofTruth, group }: { truth: NonNullable<AnTop['truth']>; lofTruth?: AnTop['truth'] | null; group?: GroupInfo | null }) {
  const lofAt = (k: number) => lofTruth?.precision_at_k.find((r) => r.k === k)
  return (
    <div className="card" data-testid="an-truth">
      <div className="card-head"><div className="left"><h3 className="card-title">Check against planted anomalies</h3><StatusPill status="warning">sample only</StatusPill></div></div>
      <div className="card-body stack" style={{ gap: 8 }}>
        <p className="helper" style={{ fontSize: 12 }}>The sample has {truth.planted} planted anomalies listed in a hidden file the model never reads. P@k = hits in the top k by s / k. With real data there is no such file: review the flags.</p>
        <table className="table"><thead><tr><th className="r">k</th><th className="r">hits</th><th className="r">P@k (IF)</th><th className="r">recall</th>{lofTruth && <th className="r">P@k (LOF)</th>}</tr></thead>
          <tbody>{truth.precision_at_k.map((r) => <tr key={r.k}><td className="r table-cell">{r.k}</td><td className="r table-cell">{r.hits}</td><td className="r table-cell"><b>{pct(r.precision, 0)}</b></td><td className="r table-cell">{pct(r.recall, 0)}</td>{lofTruth && <td className="r table-cell">{pct(lofAt(r.k)?.precision, 0)}</td>}</tr>)}</tbody></table>
        <div className="an-kinds">{Object.entries(truth.by_kind).map(([k, v]) => <div key={k} className="fc-w"><span className="mono">{k}</span><span className="bar"><i style={{ width: `${(100 * v.found) / v.planted}%`, background: 'var(--viz-a)' }} /></span><span className="mono">{v.found}/{v.planted}</span></div>)}</div>
        <p className="helper" style={{ fontSize: 12 }}>ROC AUC {f3(truth.roc_auc)} · AP {f3(truth.average_precision)} (base rate {pct(truth.base_rate)}). Per kind: found among the flagged. {group ? <>This run adds the group-relative features r, z{group.time_column ? ', Δt' : ''} on {group.entity_column}: an amount large only for its own card is now visible. Velocity bursts stay hard for an isolation forest (one short gap among {Object.keys(truth.by_kind).length} kinds of signal); review them with a rule on Δt.</> : <>Without group-relative features, spikes that are large only for the card’s own history are invisible to the forest (it sees the amount, not the ratio). Map an entity column on the setup page to add r = x / x̄<sub>e</sub> and z within the entity.</>}</p>
      </div>
    </div>
  )
}

function TopTable({ top, sel, setSel }: { top: AnTop | null; sel: AnRow | null; setSel: (r: AnRow) => void }) {
  if (!top) return <div className="card card-body row"><ResonanceWave /> Ranking rows…</div>
  return (
    <div className="card" data-testid="an-top">
      <div className="card-head"><div className="left"><h3 className="card-title">Most anomalous rows</h3><span className="helper" style={{ fontSize: 12 }}>by s, highest first · {top.rows.filter((r) => r.flagged).length} of these flagged</span></div></div>
      <div className="table-wrap an-top-wrap"><table className="table"><thead><tr><th className="r">#</th><th className="r">row</th><th className="r">s</th><th className="r">E[h]</th><th className="r">LOF</th><th>why</th>{top.truth && <th title="planted in the sample (hidden truth)">✓</th>}</tr></thead>
        <tbody>{top.rows.map((r) => <tr key={r.row} className={sel?.row === r.row ? 'sel' : ''} onClick={() => setSel(r)} style={{ cursor: 'pointer' }}>
          <td className="r table-cell">{r.rank}</td><td className="r table-cell">{r.row}</td>
          <td className="r table-cell" style={{ color: r.flagged ? 'var(--status-danger)' : undefined }}><b>{r.s.toFixed(3)}</b></td><td className="r table-cell">{r.Eh.toFixed(2)}</td><td className="r table-cell">{f3(r.lof, 2)}</td>
          <td className="an-why">{r.reasons.slice(0, 3).map((x) => x.feature).join(', ')}</td>{top.truth && <td>{r.planted ? '●' : ''}</td>}</tr>)}</tbody></table></div>
    </div>
  )
}

function Reason({ row }: { row: AnRow }) {
  const max = Math.max(...row.reasons.map((r) => Math.abs(r.phi)), 1e-9)
  return (
    <div className="card" data-testid="an-reason">
      <div className="card-head"><div className="left"><h3 className="card-title">Why row {row.row}</h3><span className="helper" style={{ fontSize: 12 }}>s = {row.s.toFixed(4)} · E[h(x)] = {row.Eh.toFixed(2)} splits</span></div>{row.planted != null && <StatusPill status={row.planted ? 'danger' : 'neutral'}>{row.planted ? 'planted' : 'not planted'}</StatusPill>}</div>
      <div className="card-body stack" style={{ gap: 10 }}>
        <div className="formula"><div className="eq" style={{ fontSize: 16 }}>E[h(x)] = φ₀ + Σ<sub>j</sub> φ<sub>j</sub> = {f3(row.phi0, 2)} {row.phi_sum >= 0 ? '+' : '−'} {Math.abs(row.phi_sum).toFixed(2)}</div>
          <dl className="where"><dt>φ₀</dt><dd>mean path length over the background (TreeSHAP’s expected value)</dd><dt>φ<sub>j</sub></dt><dd>feature j’s share of the path length (one-hot columns summed back to j); φ<sub>j</sub> &lt; 0 shortens the path, i.e. pushes x towards anomalous</dd><dt>z</dt><dd>(x<sub>j</sub> − median) / (1.4826·MAD), robust deviation of a number</dd></dl></div>
        {row.reasons.map((r) => (
          <div key={r.feature} className="an-reason-row">
            <span className="mono name">{r.feature}</span>
            <span className="mono v">{val(r.value)}</span>
            <span className="bar"><i style={{ width: `${(100 * Math.abs(r.phi)) / max}%`, background: r.phi < 0 ? 'var(--status-danger)' : 'var(--viz-b)' }} /></span>
            <span className="mono r">φ {r.phi.toFixed(2)}</span>
            <span className="mono r muted">{r.z != null ? `z ${r.z >= 0 ? '+' : ''}${r.z.toFixed(1)}` : r.share != null ? `${(r.share * 100).toFixed(1)}% rows` : ''}</span>
          </div>))}
        <details><summary className="micro-label">all values</summary>
          <div className="an-values">{Object.entries(row.values).map(([k, v]) => <span key={k}><span className="muted">{k}</span> <span className="mono">{val(v)}</span></span>)}</div></details>
      </div>
    </div>
  )
}

function LofScatter({ scores, sStar }: { scores: AnScores; sStar: number }) {
  const pts = scores.scatter!
  const [x0, x1] = padDomain(pts.map((p) => p.s))
  const ys = pts.map((p) => Math.log10(Math.max(p.lof, 1e-3)))
  const [y0, y1] = padDomain(ys)
  return (
    <PlotFrame title="IsolationForest against LOF" chips={<span className="mono helper" style={{ fontSize: 11.5 }}>{pts.length.toLocaleString()} sampled rows</span>}
      caption={<span>Each dot is a row: x = s(x, ψ), y = log₁₀ LOF<sub>k</sub>(x). Upper right = unusual to both; disagreement shows what each method sees (global isolation vs local density).</span>}>
      <XYPlot height={260} series={[]} x={{ min: x0, max: x1, ticks: niceTicks(x0, x1, 6), title: 's', fmt: (v) => v.toFixed(2) }}
        y={{ min: y0, max: y1, ticks: niceTicks(y0, y1, 4), title: 'log₁₀ LOF', fmt: (v) => v.toFixed(1) }} refs={[{ x: sStar, label: 's*', color: 'var(--status-danger)' }, { y: 0, label: 'LOF = 1' }]}>
        {(sx, sy) => <g>{pts.map((p, i) => <circle key={i} cx={sx(p.s)} cy={sy(ys[i])} r={2.2} fill={p.s >= sStar ? 'var(--status-danger)' : 'var(--viz-b)'} opacity={p.s >= sStar ? 0.9 : 0.35} />)}</g>}
      </XYPlot>
    </PlotFrame>
  )
}

function Batch({ runId }: { runId: string }) {
  const ref = useRef<HTMLInputElement>(null)
  const [busy, setBusy] = useState(false)
  const [out, setOut] = useState<AnBatch | null>(null)
  const [err, setErr] = useState('')
  const go = async (f: File) => { setBusy(true); setErr(''); try { setOut(await flows.anomaly.score(runId, f)) } catch (e) { setErr((e as Error).message) } finally { setBusy(false) } }
  const hist = useMemo(() => out?.histogram, [out])
  return (
    <div className="card" data-testid="an-batch">
      <div className="card-head"><div className="left"><h3 className="card-title">Score a new batch</h3><span className="helper" style={{ fontSize: 12 }}>same columns; scored with the saved model and its s*; percentile = share of the fitted rows with a lower s</span></div>
        <div className="row"><input ref={ref} type="file" accept=".csv" hidden onChange={(e) => e.target.files?.[0] && go(e.target.files[0])} />
          <button className="btn sm" disabled={busy} onClick={() => ref.current?.click()} data-testid="an-batch-upload">{busy ? 'Scoring…' : 'Upload CSV…'}</button>
          {out && <a className="btn sm" href={out.download} download>Download scored CSV</a>}</div></div>
      {err && <div className="card-body"><p className="helper" style={{ color: 'var(--status-danger)' }}>✕ {err}</p></div>}
      {out && hist && <div className="card-body grid an-two">
        <div className="stack" style={{ gap: 6 }}>
          <p className="helper"><b>{out.summary.n_flagged}</b> of {out.summary.rows.toLocaleString()} rows flagged ({pct(out.summary.flagged_share)} vs c = {pct(out.summary.expected_share)} expected) · mean s {out.summary.mean_score.toFixed(3)}</p>
          <table className="table"><thead><tr><th className="r">row</th><th className="r">s</th><th className="r">pct</th><th>why</th></tr></thead>
            <tbody>{out.top.slice(0, 10).map((r) => <tr key={r.row}><td className="r table-cell">{r.row}</td><td className="r table-cell" style={{ color: r.flagged ? 'var(--status-danger)' : undefined }}>{r.s.toFixed(3)}</td><td className="r table-cell">{(r.percentile * 100).toFixed(1)}</td><td className="an-why">{r.why}</td></tr>)}</tbody></table>
        </div>
        <XYPlot height={200} series={[]} bars={hist.counts.map((n, i) => ({ x0: hist.edges[i], x1: hist.edges[i + 1], y: n, color: hist.edges[i] >= out.summary.threshold ? 'var(--status-danger)' : 'var(--viz-b)' }))}
          x={{ min: hist.edges[0], max: hist.edges[hist.edges.length - 1], ticks: niceTicks(hist.edges[0], hist.edges[hist.edges.length - 1], 5), title: 's', fmt: (v) => v.toFixed(2) }}
          y={{ min: 0, max: Math.max(...hist.counts) * 1.1, ticks: niceTicks(0, Math.max(...hist.counts) * 1.1, 4), title: 'rows', fmt: (v) => v.toFixed(0) }}
          refs={[{ x: out.summary.threshold, label: 's*', color: 'var(--status-danger)' }]} />
      </div>}
    </div>
  )
}
