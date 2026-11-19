import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ApiError, api } from '@/shared/api/client'
import type { DatasetDetail, GymSchema, GymValidation } from '@/shared/api/types'
import { ResonanceWave } from '@/shared/primitives/ResonanceWave'
import { Segmented } from '@/shared/primitives/Segmented'
import { StatusPill } from '@/shared/primitives/StatusPill'
import { ADVANCED, type Body, type GymState, STEPS, defaultBody, fmtSecs, setPath } from './body'
import { CallDiff, Checks, Effective, Estimate } from './panels'
import { BaggingStep, DataStep, DeployStep, ExtraDataStep, GoalStep, HyperStep, ModelsStep, RawKwargs, SearchStep, TargetStep, type StepProps } from './steps'

const fromHash = () => new URLSearchParams(location.hash.split('?')[1] ?? '').get('from')

/**
 * The Training Gym on the real engine: every AutoGluon fit argument, edited as the exact body of POST /api/runs.
 * Guided shows data, label, goal and model families; Advanced adds per-family hyperparameters, bagging and stacking,
 * HPO, pseudo-labelling, distillation and deployment, plus the raw fit_kwargs. The server validates every change
 * against TabularPredictor.fit, resolves the call, says what AutoGluon will decide for this data and estimates T̂.
 */
export function LiveGym({ gym, setGym, guided, setGuided, go }: {
  gym: GymState; setGym: (g: GymState | ((g: GymState) => GymState)) => void; guided: boolean; setGuided: (g: boolean) => void; go: (r: string) => void
}) {
  const [schema, setSchema] = useState<GymSchema | null>(null)
  const [ds, setDs] = useState<DatasetDetail | null>(null)
  const [v, setV] = useState<GymValidation | null>(null)
  const [pending, setPending] = useState(false)
  const [step, setStep] = useState(0)
  const [err, setErr] = useState('')
  const [starting, setStarting] = useState(false)
  const [dock, setDock] = useState<'Validation' | 'Dry Run'>('Validation')
  const [dockOpen, setDockOpen] = useState(true)
  const body = gym.body

  useEffect(() => { api.gym.schema().then(setSchema).catch((e) => setErr((e as Error).message)) }, [])

  const loadRun = useCallback(async (id: string) => {
    try {
      const g = await api.gym.fromRun(id)
      setGym({ body: g.body as Body, fromRun: id, fromName: g.name, patch: null })
    } catch (e) { setErr((e as Error).message) }
  }, [setGym])

  // First visit: #/gym?from=<run> loads that run; otherwise a sample with a label.
  useEffect(() => {
    const from = fromHash()
    if (from && from !== gym.fromRun) { loadRun(from); return }
    if (body) return
    api.datasets.list().then((list) => {
      const pick = list.find((d) => d.id === 'credit-default') ?? list.find((d) => d.target) ?? list[0]
      if (pick) api.datasets.get(pick.id).then((d) => setGym((g) => (g.body ? g : { body: defaultBody(d), fromRun: null, patch: null })))
    }).catch((e) => setErr((e as Error).message))
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!body?.dataset_id || ds?.id === body.dataset_id) return
    api.datasets.get(body.dataset_id).then(setDs).catch(() => setDs(null))
  }, [body?.dataset_id]) // eslint-disable-line react-hooks/exhaustive-deps

  // Validate on every change (debounced); the last answer wins.
  const seq = useRef(0)
  const key = JSON.stringify(body)
  useEffect(() => {
    if (!body) return
    const my = ++seq.current
    setPending(true)
    const t = setTimeout(() => {
      api.gym.validate(body).then((r) => { if (my === seq.current) { setV(r); setPending(false) } })
        .catch((e) => { if (my === seq.current) { setPending(false); setErr((e as Error).message) } })
    }, 350)
    return () => clearTimeout(t)
  }, [key]) // eslint-disable-line react-hooks/exhaustive-deps

  const setBody = useCallback((b: Body) => setGym((g) => ({ ...g, body: b })), [setGym])
  const set = useCallback((path: string, value: unknown) => setGym((g) => (g.body ? { ...g, body: setPath(g.body, path, value) } : g)), [setGym])
  const onDataset = (id: string) => api.datasets.get(id).then((d) => { setDs(d); setGym({ body: defaultBody(d), fromRun: null, patch: null }) })

  const order = useMemo(() => STEPS.map((_, i) => i).filter((i) => !guided || !ADVANCED.has(i)), [guided])
  const pos = order.indexOf(step)
  const last = step === STEPS.length - 1
  const next = () => setStep(order[Math.min(order.length - 1, pos + 1)] ?? step)
  const back = () => setStep(order[Math.max(0, pos - 1)] ?? 0)

  const start = async () => {
    if (!body) return
    setErr(''); setStarting(true)
    try {
      const run = await api.runs.create({ ...body, source: 'gym', parent_run_id: gym.fromRun, patch: gym.patch ? { id: gym.patch.id, title: gym.patch.title, changes: gym.patch.changes } : null })
      go(`quick-train?run=${run.id}`)
    } catch (e) {
      setErr(e instanceof ApiError && e.unreachable ? 'The API is not reachable; check that it is running.' : (e as Error).message)
    } finally { setStarting(false) }
  }

  if (!schema || !body) return (
    <div className="page"><div className="card card-body row" role={err ? 'alert' : undefined}>{err ? <span style={{ color: 'var(--status-danger)' }}>✕ {err}</span> : <><ResonanceWave /> Reading AutoGluon's configuration schema from the API…</>}</div></div>
  )

  const props: StepProps = { body, set, setBody, schema, v, ds, guided }
  const nErr = v?.errors.length ?? 0
  const nWarn = v?.warnings.length ?? 0
  const title = ds ? `${ds.name.replace(' (sample)', '')} · ${body.label ?? '—'}` : body.dataset_id

  return (
    <div className="wizard gym-live" data-testid="gym-live">
      <div className="stepper-bar">
        <div className="stepper-title">
          <h1 className="page-title" data-testid="gym-title">{title}</h1>
          <StatusPill status={nErr ? 'danger' : 'success'}>{nErr ? `${nErr} error${nErr > 1 ? 's' : ''}` : 'valid'}</StatusPill>
          {gym.fromRun && <span className="chip" data-testid="gym-from">from {gym.fromRun}</span>}
          {gym.patch && <span className="pill info" data-testid="gym-patch-pill">Vela patch · {gym.patch.title}</span>}
          <span className="spacer" />
          <span className="helper ellipsis" style={{ fontSize: 12 }}>{schema.engine.name} {schema.engine.version}{v?.data ? ` · n = ${v.data.train_rows.toLocaleString()} · d = ${v.data.d}` : ''}</span>
          <Segmented options={['Guided', 'Advanced']} value={guided ? 'Guided' : 'Advanced'} onChange={(x) => setGuided(x === 'Guided')} />
        </div>
        <div className="stepper" role="list">
          {STEPS.map((s, i) => {
            const adv = ADVANCED.has(i)
            return (
              <button key={s} role="listitem" className={`step ${i < step ? 'done' : i === step ? 'current' : ''} ${adv && guided ? 'adv-off' : ''}`}
                onClick={() => { if (adv && guided) setGuided(false); setStep(i) }} aria-current={i === step ? 'step' : undefined} data-testid={`gym-step-${i + 1}`}
                title={adv && guided ? 'Advanced: switches to Advanced mode' : undefined}>
                <span className="node" />
                <span className="idx">{String(i + 1).padStart(2, '0')}{adv && <em className="adv-tag">adv</em>}</span>
                <span>{s}</span>
              </button>
            )
          })}
        </div>
      </div>

      <div className="wizard-scroll">
        <div className="page">
          {gym.patch && (
            <div className="patch-banner" data-testid="gym-patch-banner">
              <b>Vela patch applied to {gym.fromRun}:</b> {gym.patch.title}
              {gym.patch.changes.map((c) => <span key={c.path} className="diffline"><span className="mono">{c.path}</span> <span className="old">{JSON.stringify(c.from)}</span> → <span className="mono">{JSON.stringify(c.to)}</span></span>)}
              <span className="helper">Start Training trains it as a child of {gym.fromRun}; Models compares the two.</span>
            </div>
          )}
          <div className="grid gym-grid">
            <div className="stack" style={{ minWidth: 0 }}>
              {step === 0 && <DataStep {...props} onDataset={onDataset} onLoadRun={(id) => { loadRun(id); setStep(0) }} />}
              {step === 1 && <TargetStep {...props} />}
              {step === 2 && <GoalStep {...props} />}
              {step === 3 && <ModelsStep {...props} />}
              {step === 4 && <HyperStep {...props} />}
              {step === 5 && <BaggingStep {...props} />}
              {step === 6 && <SearchStep {...props} />}
              {step === 7 && <ExtraDataStep {...props} />}
              {step === 8 && <DeployStep {...props} />}
              {step === 9 && <>
                <RawKwargs {...props} />
                <div className="card"><div className="card-head"><h3 className="card-title">What AutoGluon will do with this data</h3></div><div className="card-body"><Effective v={v} /></div></div>
              </>}
            </div>
            <div className="stack gym-side">
              <CallDiff v={v} patch={gym.patch} pending={pending} />
              <Estimate v={v} />
            </div>
          </div>
        </div>
      </div>

      <div className={`dock ${dockOpen ? '' : 'closed'}`} data-testid="gym-dock">
        <div className="dock-head">
          {(['Validation', 'Dry Run'] as const).map((t) => (
            <button key={t} className={`tab ${dockOpen && dock === t ? 'on' : ''}`} onClick={() => { setDock(t); setDockOpen(true) }}>
              {t}{t === 'Validation' && <span className={`count ${nErr ? 'err' : nWarn ? 'warn' : ''}`} data-testid="gym-check-count">{nErr ? `✕ ${nErr}` : nWarn ? `▲ ${nWarn}` : '● 0'}</span>}
            </button>
          ))}
          <span className="spacer" />
          <button className="btn sm ghost" onClick={() => setDockOpen(!dockOpen)} aria-expanded={dockOpen}>{dockOpen ? 'Hide ↓' : 'Show ↑'}</button>
        </div>
        {dockOpen && (
          <div className="dock-body">
            {dock === 'Validation' ? <Checks v={v} /> : (
              <div className="log-panel" data-testid="gym-dry-run">
                <div className="lt">$ POST /api/gym/validate · {body.dataset_id} · label={body.label}</div>
                {v?.data && <div className="li">[DATA] {v.data.rows.toLocaleString()} rows → train {v.data.train_rows.toLocaleString()} (holdout {Math.round(v.data.holdout_share * 100)}% fixed) · d = {v.data.d}{v.data.text_columns.length ? ` · text: ${v.data.text_columns.join(', ')}` : ''}</div>}
                {v?.effective && <div className="li">[PLAN] k={v.effective.num_bag_folds} s={v.effective.num_bag_sets} L={v.effective.num_stack_levels} dynamic_stacking={String(v.effective.dynamic_stacking)} refit_full={String(v.effective.refit_full)} ({v.effective.source})</div>}
                {v?.families && <div className="li">[ZOO ] {Object.entries(v.families).map(([f, n]) => `${f}×${n}`).join('  ')}</div>}
                {v?.resolved?.post_fit.map((p) => <div key={p.call} className="li">[POST] predictor.{p.call}({Object.keys(p.kwargs).join(', ')})</div>)}
                {v?.resolved?.notes.map((n) => <div key={n} className="lm">[NOTE] {n}</div>)}
                {v?.estimate && <div className={v.estimate.budget_binds ? 'lw' : 'lm'}>[EST ] T̂ ≈ {fmtSecs(v.estimate.seconds)}{v.estimate.budget_binds ? ` · budget binds (unbounded ${fmtSecs(v.estimate.unbounded_seconds)})` : ''}</div>}
                {v && <div className={v.ok ? 'lm' : 'lw'}>[{v.ok ? 'OK  ' : 'FAIL'}] {v.ok ? 'ready to submit' : `${v.errors.length} error(s): ${v.errors.map((e) => e.path).join(', ')}`}</div>}
              </div>
            )}
          </div>
        )}
      </div>

      <div className="wizard-footer">
        <div><button className="btn ghost" onClick={back} disabled={pos <= 0}>← Back</button></div>
        <div className="valid" data-testid="gym-status">{err ? <span style={{ color: 'var(--status-danger)' }} role="alert">✕ {err}</span>
          : nErr ? <span style={{ color: 'var(--status-danger)' }}>✕ {v?.errors[0].path}: {v?.errors[0].message}</span>
            : <><span className="dot">●</span> {pending ? 'Checking…' : `Valid · T̂ ≈ ${v?.estimate ? fmtSecs(v.estimate.seconds) : '—'}`}</>}</div>
        <div className="right">
          {gym.fromRun && <a className="btn" href={api.gym.exportUrl(gym.fromRun, true)} data-testid="gym-export">Export {gym.fromRun}.py</a>}
          {last ? (
            <button className="btn primary" onClick={start} disabled={starting || pending || !v?.ok} data-testid="gym-start">{starting ? 'Starting…' : 'Start Training'}</button>
          ) : (
            <>
              <button className="btn" onClick={start} disabled={starting || pending || !v?.ok} data-testid="gym-start-now">Start Training</button>
              <button className="btn primary" onClick={next} data-testid="gym-continue">Continue →</button>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
