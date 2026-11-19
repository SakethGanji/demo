import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ApiError, api } from '@/shared/api/client'
import { useApi } from '@/shared/api/mode'
import type { Run, SplitSpec } from '@/shared/api/types'
import { DatasetStep } from './components/DatasetStep'
import { GoalStep } from './components/GoalStep'
import { TargetStep, targetConfirmed } from './components/TargetStep'
import { STRATEGY_NAME } from './components/SplitCard'
import { SAMPLES, fromServer, type Dataset } from './datasets'
import { mockSource, simulate } from './race/mockRun'
import { RaceStage } from './race/RaceStage'
import { liveSource, useRace, type EventSourceFn } from './race/useRace'
import { ResultsStep } from './results/ResultsStep'
import { COST_METRIC, costPayload, rangePayload, columnTypesPayload, defaultMetric, defaultThreshold, labelImbalance, fmtBudget, inferTask, labelOptions, METRICS, parseKwargs, PRESETS, type Choices, type Task } from './wizard'

const STEPS = ['Dataset', 'Target', 'Goal', 'Train', 'Results']

export function initialChoices(ds: Dataset): Choices {
  const label = ds.target || labelOptions(ds)[0]?.name || ''
  const task = inferTask(ds, label) ?? 'multiclass'
  return { label, problemType: null, types: {}, metric: defaultMetric(task, labelImbalance(ds, label)), preset: 'medium_quality', budget: 120, fitKwargs: '', threshold: defaultThreshold(ds, label, task) }
}

const hashQuery = () => new URLSearchParams(location.hash.split('?')[1] ?? '')
const runFromHash = () => hashQuery().get('run')

/** G0: Build opens New Model with ?dataset=&target=&split=&time=&group=&drop=&metric= (the suggestion's pre-fill). */
function prefillChoices(d: Dataset, q: URLSearchParams): Choices {
  const label = q.get('target') || d.target
  const strategy = q.get('split') as SplitSpec['strategy'] | null
  const split: SplitSpec | null = strategy ? { strategy, tuning_share: 0.2, group_column: q.get('group') ?? undefined, time_column: q.get('time') ?? undefined, cutoff: null } : null
  const drop = (q.get('drop') ?? '').split(',').filter((c) => c && c !== label && d.cols.some((x) => x.name === c))
  const metric = q.get('metric')
  return { ...initialChoices(d), label, split, types: Object.fromEntries(drop.map((c) => [c, 'id' as const])), ...(metric ? { metric } : {}) }
}
const setHashRun = (id: string | null) => history.replaceState(null, '', `#/quick-train${id ? `?run=${id}` : ''}`)

/**
 * New Model: dataset or upload → label, problem type and column types → metric, preset and budget → the live race →
 * results (leaderboard, metrics, diagnostics, a custom ensemble, a first prediction). Live mode trains with AutoGluon
 * through the API; mock mode plays a simulated run through the same screens. (Route: #/quick-train.)
 */
export function QuickTrainPage({ go }: { go: (r: string) => void }) {
  const { mode } = useApi()
  const live = mode === 'live'
  const fromGym = useRef(location.hash.includes('?start')).current
  const [step, setStep] = useState(fromGym ? 2 : 0)
  const [ds, setDs] = useState<Dataset>(SAMPLES[0])
  const [ch, setCh] = useState<Choices>(() => initialChoices(SAMPLES[0]))
  const [runId, setRunId] = useState<string | null>(null)
  const [source, setSource] = useState<{ fn: EventSourceFn; run: Run | null }>({ fn: liveSource, run: null })
  const [speed, setSpeed] = useState(1)
  const [err, setErr] = useState('')
  const [starting, setStarting] = useState(false)
  const { state: race, reconnecting } = useRace(runId, speed, source.fn)
  const scroller = useRef<HTMLDivElement>(null)
  useEffect(() => { scroller.current?.scrollTo(0, 0) }, [step])

  const set = useCallback((p: Partial<Choices>) => setCh((c) => ({ ...c, ...p })), [])
  const resumed = useRef(false)
  const stepRef = useRef(step)
  stepRef.current = step
  // Only the dataset step picks datasets: a late answer from it (e.g. its first-load default) must not replace the
  // dataset of a run resumed from the URL.
  const pick = (d: Dataset) => { if (resumed.current && stepRef.current !== 0) return; setDs(d); setCh(initialChoices(d)) }

  // Live mode picks data in Build. Arriving from it (?dataset=…) opens the Target step pre-filled; step 0 goes back.
  const prefilled = useRef(false)
  // replace, not push: Back from Build must not land here and bounce straight back
  const toBuild = () => location.replace(`#/build${ds.id ? `?dataset=${ds.id}` : ''}`)
  useEffect(() => {
    if (!live || runFromHash() || fromGym) return
    const q = hashQuery()
    const id = q.get('dataset')
    if (!id) { toBuild(); return }
    if (prefilled.current) return
    prefilled.current = true
    api.datasets.get(id).then((raw) => {
      const d = fromServer(raw)
      const c = prefillChoices(d, q)
      lastLabel.current = c.label
      setDs(d); setCh(c); setStep(1)
    }).catch((e) => setErr((e as Error).message))
  }, [live]) // eslint-disable-line react-hooks/exhaustive-deps

  const inferred = inferTask(ds, ch.label)
  const task: Task | null = ch.problemType ?? inferred
  // keep the metric valid for the problem type
  useEffect(() => {
    if (!task) return
    if (!METRICS.some((m) => m.key === ch.metric && m.tasks.includes(task))) set({ metric: defaultMetric(task, labelImbalance(ds, ch.label)) })
  }, [task])
  // a new label gets the metric that suits its own balance (ROC AUC for an imbalanced binary label)
  const lastLabel = useRef(ch.label)
  useEffect(() => {
    if (lastLabel.current === ch.label) return
    lastLabel.current = ch.label
    if (task) set({ metric: defaultMetric(task, labelImbalance(ds, ch.label)) })
  }, [ch.label]) // eslint-disable-line react-hooks/exhaustive-deps
  // the threshold default follows the label and problem type
  useEffect(() => { set({ threshold: defaultThreshold(ds, ch.label, task) }) }, [ds, ch.label, task, set])

  // Resume a run from the URL (#/quick-train?run=…): after a reload or from a link. Its events replay from 0.
  useEffect(() => {
    const id = runFromHash()
    if (!id || !live || resumed.current) return
    resumed.current = true
    api.runs.get(id).then(async (run) => {
      stepRef.current = run.status === 'succeeded' ? 4 : 3
      // A busy API can be slow to answer: try a few times before giving up (a deleted dataset: the run still shows).
      for (let attempt = 0; attempt < 4; attempt++) {
        try {
          const d = fromServer(await api.datasets.get(run.dataset_id))
          setDs(d)
          setCh({ ...initialChoices(d), label: run.label, preset: String(run.config.presets ?? 'medium_quality'), budget: Number(run.config.time_limit ?? 120), split: (run.config.split_spec as Choices['split']) ?? null })
          break
        } catch (e) {
          if (e instanceof ApiError && e.status === 404) break
          await new Promise((r) => setTimeout(r, 1000 * (attempt + 1)))
        }
      }
      setSource({ fn: liveSource, run: null })
      setRunId(run.id)
      setStep(run.status === 'succeeded' ? 4 : 3)
    }).catch(() => setHashRun(null))
  }, [live])

  const kw = parseKwargs(ch.fitKwargs)
  const start = async () => {
    if (!task) return
    setErr('')
    if (live && ds.id) {
      if (!kw.ok) { setErr(kw.error); return }
      const { types, drop } = columnTypesPayload(ds, ch)
      const cost = costPayload(ds, ch, task)
      const range = rangePayload(ch, task)
      setStarting(true)
      try {
        const run = await api.runs.create({
          dataset_id: ds.id, label: ch.label, problem_type: task, eval_metric: cost ? null : ch.metric, presets: ch.preset, time_limit: ch.budget,
          fit_kwargs: kw.value, column_types: Object.keys(types).length ? types : null, drop_columns: drop, feature_importance: false,
          calibrate_threshold: task === 'binary' ? (cost ? COST_METRIC : ch.threshold) : null, split_spec: ch.split ?? null, cost,
          ...(range ?? {}),
        })
        setSource({ fn: liveSource, run: null })
        setRunId(run.id)
        setHashRun(run.id)
        setStep(3)
      } catch (e) {
        setErr(e instanceof ApiError && e.unreachable ? 'The API is not reachable; check that it is running.' : (e as Error).message)
      } finally { setStarting(false) }
      return
    }
    const id = `sim_${Math.random().toString(16).slice(2, 10)}`
    const sim = simulate(ds, ch, task, id)
    setSource({ fn: mockSource(sim), run: sim.run })
    setRunId(id)
    setStep(3)
  }

  // A finished live run has its page in Models (Results, Explain, Doctor); the wizard only trains and watches.
  const runPageNow = live && step === 4 && runId != null && source.run == null
  useEffect(() => { if (runPageNow) location.replace(`#/models?run=${runId}`) }, [runPageNow, runId])

  const again = () => { if (live) { toBuild(); return } setRunId(null); setHashRun(null); setStep(0) }
  const running = runId != null && (race.status === 'running' || race.status === 'queued' || race.status === 'connecting')
  const done = race.status === 'succeeded'
  const maxStep = runId ? (done ? 4 : 3) : 2
  const K = ds.classes.length
  const usable = !!inferred
  const preset = PRESETS.find((p) => p.key === ch.preset)
  // F4: in live mode the target is confirmed explicitly; its own holdout is drawn for it.
  const confirmed = !live || targetConfirmed(ds, ch.label)
  const nFeat = useMemo(() => ds.cols.filter((c) => c.name !== ch.label && (ch.types[c.name] ?? c.type) !== 'id').length, [ds, ch])

  return (
    <div className="wizard new-model">
      <div className="stepper-bar">
        <div className="stepper-title">
          <h1 className="page-title">New Model</h1>
          <span className="helper">Bring a table, say what to predict, watch AutoGluon train and stack its models.</span>
          <span className="spacer" />
          <span className="chip" data-testid="chip-dataset">{ds.name}</span>
          {step > 0 && ch.label && <span className="chip"><span className="sym">y</span>= {ch.label}</span>}
          {step > 1 && <span className="chip">{ch.preset} · τ = {fmtBudget(ch.budget)}</span>}
        </div>
        <div className="stepper" role="list" style={{ gridTemplateColumns: `repeat(${STEPS.length}, 1fr)`, maxWidth: 820 }}>
          {STEPS.map((s, i) => (
            <button key={s} role="listitem" data-testid={`step-${s.toLowerCase()}`} disabled={i > maxStep || (running && i < 3)}
              className={`step ${i < step || (i === 4 && done && step === 4) ? 'done' : i === step ? 'current' : ''}`} onClick={() => (live && i === 0 ? toBuild() : setStep(i))}>
              <span className="node" /><span className="idx">{String(i + 1).padStart(2, '0')}</span><span>{s}</span>
            </button>
          ))}
        </div>
      </div>

      <div className="wizard-scroll" ref={scroller}>
        {step === 0 && (live ? <div className="page"><p className="helper">Opening Build…</p></div> : <DatasetStep ds={ds} onPick={pick} />)}
        {step === 1 && <TargetStep ds={ds} ch={ch} set={set} onConfirmed={setDs} />}
        {step === 2 && task && <GoalStep ds={ds} ch={ch} set={set} task={task} />}
        {step === 3 && runId && (
          <div className="page">
            <RaceStage s={race} runId={runId} label={ch.label} budget={ch.budget} speed={speed} setSpeed={setSpeed} reconnecting={reconnecting}
              simulated={source.run != null} onCancel={() => { if (source.run == null) api.runs.cancel(runId).catch(() => {}) }} />
            <RaceSide race={race} />
          </div>
        )}
        {step === 4 && runId && source.run && <ResultsStep runId={runId} mockRun={source.run} ds={ds} live={false} go={go} onAgain={again} />}
      </div>

      {step < 4 && (
        <div className="wizard-footer">
          <div>{step > 0 && step < 3 && <button className="btn ghost" onClick={() => (live && step === 1 ? toBuild() : setStep(step - 1))}>← Back</button>}
            {step === 3 && !running && <button className="btn ghost" onClick={again}>← New Model</button>}</div>
          <div className="valid" data-testid="wizard-status">
            {err ? <span style={{ color: 'var(--status-danger)' }} role="alert">✕ {err}</span>
              : step === 0 ? (usable ? <><span className="dot">●</span> Schema Valid · target {ch.label} · {inferred === 'regression' ? 'regression' : `K = ${K}`}</> : <span style={{ color: 'var(--status-warning)' }}><span className="dot">▲</span> No label column with two or more values</span>)
              : step === 1 ? (!task ? <span style={{ color: 'var(--status-warning)' }}>▲ pick a label with two or more values</span>
                : !confirmed ? <span style={{ color: 'var(--status-warning)' }} data-testid="confirm-needed">▲ confirm y = {ch.label} to draw its holdout</span>
                : <><span className="dot">●</span> y = {ch.label} (confirmed) · {task === 'regression' && ch.range ? `quantile τ = ${ch.range.levels.join(' · ')}` : task} · d = {nFeat} features · {ch.split ? <>{STRATEGY_NAME[ch.split.strategy]} split</> : <>|H| = {ds.profile?.splits.holdout_rows.toLocaleString() ?? '—'}</>}</>)
              : step === 2 && !confirmed ? <span style={{ color: 'var(--status-warning)' }}>▲ go back and confirm y = {ch.label}</span>
              : step === 2 ? (kw.ok ? <><span className="dot">●</span> {preset?.name} · τ = {fmtBudget(ch.budget)} · {task === 'regression' && ch.range ? 'pinball_loss' : ch.metric}{live && ds.id ? '' : ' · simulated (mock mode)'}</> : <span style={{ color: 'var(--status-danger)' }}>✕ fit_kwargs: {kw.error}</span>)
              : done ? <><span className="dot">●</span> Run complete · {race.bestModel}</> : race.status === 'failed' ? <span style={{ color: 'var(--status-danger)' }}>✕ {race.error}</span> : race.status === 'cancelled' ? <span>Cancelled</span> : <span>Training…</span>}
          </div>
          <div className="right">
            {step === 0 && <button className="btn primary" disabled={!usable} onClick={() => setStep(1)}>Continue →</button>}
            {step === 1 && <button className="btn primary" data-testid="target-continue" disabled={!task || !confirmed} onClick={() => setStep(2)}>Continue →</button>}
            {step === 2 && <button className="btn primary" data-testid="start-training" disabled={!kw.ok || starting || !task || !confirmed} onClick={start}>{starting ? 'Starting…' : 'Start Training'}</button>}
            {step === 3 && <button className={`btn primary ${done ? 'pulse' : ''}`} data-testid="see-results" disabled={!done} onClick={() => setStep(4)}>Results →</button>}
          </div>
        </div>
      )}
    </div>
  )
}

/** Beside the stage: the data as AutoGluon saw it, and its log. */
function RaceSide({ race }: { race: ReturnType<typeof useRace>['state'] }) {
  return (
    <div className="grid race-side" style={{ gridTemplateColumns: 'minmax(0, 1fr) minmax(0, 1.4fr)', alignItems: 'start' }}>
      <div className="card" data-testid="race-data">
        <div className="card-head"><h3 className="card-title">The Fit</h3><span className="micro-label">as AutoGluon sees it</span></div>
        <dl className="where race-facts">
          <dt>n</dt><dd>{race.trainRows != null ? `${race.trainRows.toLocaleString()} training rows` : '…'}{race.droppedMissing ? ` (${race.droppedMissing} without a label dropped)` : ''}</dd>
          <dt>n<sub>h</sub></dt><dd>{race.holdoutRows != null ? `${race.holdoutRows.toLocaleString()} holdout rows, never trained on, scored at the end` : '…'}</dd>
          {race.split && <><dt>H</dt><dd data-testid="race-split">the holdout drawn for y = <b className="mono">{race.split.target}</b> ({race.split.strategy}{race.split.cutoff ? `, t ≥ τ = ${race.split.cutoff}` : ''}{race.split.params?.group_column ? `, no ${race.split.params.group_column} shared with training` : ''}, sha {race.split.sha256.slice(0, 12)})</dd></>}
          {race.split?.tuning_rows ? <><dt>T</dt><dd data-testid="race-tuning">{race.split.tuning_rows.toLocaleString()} tuning rows passed as <span className="mono">tuning_data</span>{race.split.tuning_cutoff ? ` (${race.split.tuning_cutoff} onwards)` : ' (entity-disjoint)'}{race.split.excluded_rows ? ` · ${race.split.excluded_rows.toLocaleString()} rows excluded` : ''}</dd></> : null}
          <dt>d</dt><dd>{race.features.length ? `${race.features.length} features: ${race.features.slice(0, 8).join(', ')}${race.features.length > 8 ? ', …' : ''}` : '…'}</dd>
          <dt>k</dt><dd>{race.kFold ? `${race.kFold}-fold bagging: each model is k models, scored out of fold` : race.metric ? 'no bagging: scored on a tuning split AutoGluon holds out' : '…'}</dd>
          <dt>metric</dt><dd>{race.metric ?? '…'}{race.metric ? ` (${race.greater ? 'higher' : 'lower'} is better)` : ''}</dd>
          {race.droppedColumns.length > 0 && <><dt>dropped</dt><dd>{race.droppedColumns.join(', ')}</dd></>}
          {race.notes.map((n) => <span key={n} style={{ display: 'contents' }}><dt>note</dt><dd>{n}</dd></span>)}
        </dl>
      </div>
      <div className="card">
        <div className="card-head"><h3 className="card-title">AutoGluon Log</h3><span className="micro-label">{race.logs.length} lines</span></div>
        <div className="card-body"><div className="log-panel" style={{ height: 220 }} aria-live="polite" data-testid="race-log">
          {race.logs.slice(-11).map((l, i) => <div key={i} className={l.level === 'warning' ? 'lw' : /Validation score|score_val/.test(l.message) ? 'lm' : /Fitting model/.test(l.message) ? 'li' : 'lt'}>{l.message}</div>)}
        </div></div>
      </div>
    </div>
  )
}
