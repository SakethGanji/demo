import { Fragment, useEffect, useMemo, useState } from 'react'
import { MaskBar } from '@/shared/components/MaskBar'
import type { Unmask } from '@/shared/api/unmask'
import { api } from '@/shared/api/client'
import type { ActiveLearning, ALItem, ALRequest, Run, SlicePool } from '@/shared/api/types'
import { PlotFrame } from '@/shared/components/PlotFrame'
import { niceTicks, padDomain, XYPlot } from '@/shared/components/XYPlot'
import { fmtScore } from '@/shared/lib/metrics'
import { NumberInput } from '@/shared/primitives/NumberInput'
import { ParamChip } from '@/shared/primitives/ParamChip'
import { ResonanceWave } from '@/shared/primitives/ResonanceWave'
import { StatusPill } from '@/shared/primitives/StatusPill'
import { pctTrim } from '@/shared/lib/format'
import { useFetch } from '@/shared/hooks/useFetch'

const sgn = (v: number, d = 4) => `${v >= 0 ? '+' : '−'}${Math.abs(v).toFixed(d)}`
const nfmt = (n: number) => Math.round(n).toLocaleString()
const PAGE = 40
const VERDICT = {
  'active-better': { pill: 'success', head: 'Active better', say: 'At the same number of labels, the rows picked by uncertainty + diversity gave a better model on every reading of the interval.' },
  'no-clear-difference': { pill: 'neutral', head: 'Within noise', say: 'The 95% interval of the gap contains 0: with these seeds the difference is within the noise of a refit.' },
  'random-better': { pill: 'danger', head: 'Random better', say: 'Random rows gave a better model at the same budget: the uncertain rows here are mostly ambiguous, not informative.' },
  pending: { pill: 'warning', head: 'Comparing…', say: 'The simulated comparison has not reached the last round yet.' },
  'not-compared': { pill: 'neutral', head: 'Not compared', say: '' },
} as const
const U_FORMULA = {
  margin: <>u(x) = 1 − (p̂<sub>(1)</sub>(x) − p̂<sub>(2)</sub>(x))</>,
  entropy: <>u(x) = −Σ<sub>k</sub> p̂<sub>k</sub>(x) log p̂<sub>k</sub>(x) ⁄ log K</>,
  least_confident: <>u(x) = 1 − p̂<sub>(1)</sub>(x)</>,
} as const

/** F13: the active-learning labelling queue. The run's exact fit call refitted on a growing labelled set; each round
 *  picks b rows by uncertainty + diversity for you to label in the Ground-Truth Inbox's style; a simulated
 *  active-vs-random comparison (the hidden labels as the oracle) says whether picking beats chance at the same budget.
 *  Every number is GET /api/runs/{id}/active-learning. */
export function LabellingQueuePanel({ run, sliceId }: { run: Run; sliceId?: string }) {
  const [busy, setBusy] = useState(false)
  const [unmask, setUnmask] = useState<Unmask>(null)
  const { data: al, setData: setAl, err, setErr, reload } = useFetch(() => api.runs.activeLearning(run.id, unmask ?? undefined), [run.id, unmask], { pollMs: 2000, while: (d) => d?.status === 'queued' || d?.status === 'running' || !!d?.queue?.submitted_pending })
  const working = al?.status === 'queued' || al?.status === 'running' || !!al?.queue?.submitted_pending
  const [body, setBody] = useState<ALRequest>(sliceId ? { slice_id: sliceId } : {})
  const [pool, setPool] = useState<SlicePool | null>(null)
  useEffect(() => {
    if (!sliceId) return
    let alive = true
    api.runs.slicePool(run.id, sliceId).then((p) => alive && setPool(p)).catch((e) => alive && setErr((e as Error).message))
    return () => { alive = false }
  }, [run.id, sliceId])


  const d = al?.defaults
  // F16: pre-filtered to a weak slice, the pool is the slice's hidden training rows: default b to fit the rounds.
  const sliceAvail = pool ? Math.floor(pool.labelled_in_slice * (body.hide_share ?? d?.hide_share ?? 0.9)) : null
  const sliceBudget = sliceAvail != null && d ? Math.max(1, Math.min(d.budget, Math.floor(sliceAvail / (body.rounds ?? d.rounds)))) : undefined
  const req = { ...d, ...(sliceBudget != null && body.budget == null ? { budget: sliceBudget } : {}), ...body }
  const start = async () => {
    setBusy(true); setErr('')
    try { setAl(await api.runs.startActiveLearning(run.id, sliceId ? { ...body, slice_id: sliceId, budget: req.budget } : body)) } catch (e) { setErr((e as Error).message) } finally { setBusy(false) }
  }
  const cancel = async () => { try { await api.runs.cancelActiveLearning(run.id); reload() } catch (e) { setErr((e as Error).message) } }

  if (!al && !err) return <div className="card card-body row"><ResonanceWave /> Reading the labelling queue…</div>
  const metric = al?.metric?.name ?? run.eval_metric?.name ?? 'score'
  const started = al && al.status !== 'none' && !!al.arms
  const s = al?.summary

  return (
    <div className="stack" style={{ gap: 'var(--space-16)' }} data-testid="al-panel" data-status={al?.status}>
      {err && <div className="card card-body" role="alert" data-testid="al-error"><p className="helper">✕ {err}</p></div>}
      <MaskBar info={al} kind="run" id={run.id} value={unmask} onChange={setUnmask} what="feature values in the queue" />
      {al && started && s && <Hero al={al} metric={metric} />}
      {al && started && al.slice && (
        <p className="helper" style={{ fontSize: 12.5 }} data-testid="al-slice-on">Pool pre-filtered to the weak slice <span className="mono">{al.slice.label}</span> (F16): {nfmt(al.slice.n_pool)} unlabelled training rows inside it of {nfmt(al.slice.n_train_in_slice)}; active and random pick from the same restricted pool.</p>
      )}
      {al && started && (al.queue?.points.length ?? 0) > 0 && <Chart al={al} metric={metric} />}
      {al && working && (
        <div className="card card-body row" data-testid="al-progress" style={{ gap: 'var(--space-12)', flexWrap: 'wrap' }}>
          <ResonanceWave />
          <span>Fitting <b className="mono">{al.fits_done ?? 0} / {al.fits_total ?? '…'}</b>
            {al.current ? <> · now {al.current.task === 'init' ? `the seed labels of seed ${al.current.seed}` : al.current.task === 'queue' ? `your round ${al.current.round}` : `${al.current.task}, round ${al.current.round}`}</> : null}
            {' '}· the run's exact fit call each time, scored on the same holdout</span>
          <button className="btn" onClick={cancel} data-testid="al-cancel">Cancel</button>
        </div>
      )}
      {al && started && al.queue && <Queue al={al} onChange={reload} />}
      {al && started && s && <Formula al={al} metric={metric} />}
      {al && started && <Rounds al={al} metric={metric} />}
      {al && !working && (al.status !== 'awaiting_labels' || (sliceId && al.slice?.id !== sliceId)) && (
        <div className="card" data-testid="al-start">
          <div className="card-head"><div className="left"><h3 className="card-title">{started ? 'Start a New Queue' : 'Which Rows Should a Person Label?'}</h3>
            <span className="helper" style={{ fontSize: 12 }}>the run's exact fit call on a growing labelled set; every fit scored on the run's fixed holdout, which is never labelled</span></div></div>
          <div className="card-body stack" style={{ gap: 'var(--space-8)' }}>
            {sliceId && <p className="helper" style={{ fontSize: 12.5 }} data-testid="al-slice">
              <StatusPill status="info">weak slice</StatusPill>{' '}Pre-filtered to <span className="mono">{pool?.label ?? sliceId}</span> (Explain → Weak Slices):{' '}
              {pool ? <>{nfmt(pool.in_slice)} of the {nfmt(pool.train_rows)} training rows fall inside it, so about {nfmt(sliceAvail ?? 0)} hidden rows can be picked; b defaults to {sliceBudget} per round.</> : 'counting its training rows…'}
              {' '}<a href={`#/models?run=${run.id}&tab=Doctor&sub=Labels`}>Use the whole pool instead</a></p>}
            {(al.status === 'failed' || al.status === 'cancelled') && <p className="helper" role="alert">✕ The last queue {al.status === 'failed' ? `failed: ${al.error}` : 'was cancelled'}.</p>}
            <p className="helper" style={{ fontSize: 12.5 }}>
              {req.pool === 'missing'
                ? <>The pool is the {nfmt(d?.missing_labels ?? 0)} training rows whose label is empty.</>
                : <>Hides the labels of {pctTrim(req.hide_share ?? 0.9)} of the {nfmt(d?.n_train ?? 0)} training rows (stratified); {nfmt((1 - (req.hide_share ?? 0.9)) * (d?.n_train ?? 0))} stay as seed labels.</>}
              {' '}Each of {req.rounds} rounds picks <b className="mono">b = {req.budget}</b> rows by {req.strategy} uncertainty among the {req.candidates_factor}·b most uncertain, spread by {req.diversity === 'cluster' ? 'k-means clusters' : req.diversity === 'kcenter' ? 'k-center' : 'nothing'}; you label them here.
              {req.pool !== 'missing' && req.compare_random ? <> The hidden labels also drive a <b>simulated</b> comparison, active vs random at equal budgets over {req.seeds} seeds.</> : null}
              {d ? <> About <b className="mono">{d.fits} fits</b>{d.estimate_seconds ? <>, {Math.max(1, Math.round(d.estimate_seconds / 60))} min here</> : null}.</> : null}
            </p>
            <details className="an-adv" data-testid="al-advanced"><summary className="micro-label">Advanced · pool, budget, rounds, strategy, seeds</summary>
              <div className="row" style={{ gap: 'var(--space-16)', flexWrap: 'wrap', marginTop: 'var(--space-8)' }}>
                <label className="stack" style={{ gap: 4 }}><span className="micro-label">pool</span>
                  <select className="select" value={req.pool} onChange={(e) => setBody({ ...body, pool: e.target.value as ALRequest['pool'] })} aria-label="Pool">
                    <option value="hide">hide a share of the labels</option><option value="missing" disabled={!d?.missing_labels}>rows with an empty label ({d?.missing_labels ?? 0})</option></select></label>
                {req.pool === 'hide' && <label className="stack" style={{ gap: 4 }}><span className="micro-label">hidden share h</span>
                  <NumberInput value={req.hide_share ?? 0.9} min={0.1} max={0.99} step={0.05} onChange={(v) => setBody({ ...body, hide_share: v })} label="Hidden share" /></label>}
                <label className="stack" style={{ gap: 4 }}><span className="micro-label">budget b per round</span>
                  <NumberInput value={req.budget ?? 200} min={1} max={5000} step={50} onChange={(v) => setBody({ ...body, budget: Math.round(v) })} label="Budget" /></label>
                <label className="stack" style={{ gap: 4 }}><span className="micro-label">rounds</span>
                  <NumberInput value={req.rounds ?? 2} min={1} max={10} step={1} onChange={(v) => setBody({ ...body, rounds: Math.round(v) })} label="Rounds" /></label>
                <label className="stack" style={{ gap: 4 }}><span className="micro-label">uncertainty</span>
                  <select className="select" value={req.strategy} onChange={(e) => setBody({ ...body, strategy: e.target.value as ALRequest['strategy'] })} aria-label="Uncertainty">
                    <option value="margin">margin</option><option value="entropy">entropy</option><option value="least_confident">least confident</option></select></label>
                <label className="stack" style={{ gap: 4 }}><span className="micro-label">diversity</span>
                  <select className="select" value={req.diversity} onChange={(e) => setBody({ ...body, diversity: e.target.value as ALRequest['diversity'] })} aria-label="Diversity">
                    <option value="cluster">weighted k-means</option><option value="kcenter">k-center (core-set)</option><option value="none">none</option></select></label>
                <label className="stack" style={{ gap: 4 }}><span className="micro-label">β, candidates per pick</span>
                  <NumberInput value={req.candidates_factor ?? 10} min={1} max={100} step={1} onChange={(v) => setBody({ ...body, candidates_factor: Math.round(v) })} label="Beta" /></label>
                <label className="stack" style={{ gap: 4 }}><span className="micro-label">comparison seeds</span>
                  <NumberInput value={req.seeds ?? 3} min={1} max={10} step={1} onChange={(v) => setBody({ ...body, seeds: Math.round(v) })} label="Seeds" /></label>
                <label className="row" style={{ gap: 6, alignSelf: 'end' }}><input type="checkbox" checked={!!req.compare_random} onChange={(e) => setBody({ ...body, compare_random: e.target.checked })} /> <span className="micro-label">compare with random</span></label>
              </div>
            </details>
            <div className="row"><button className="btn primary" onClick={start} disabled={busy} data-testid="al-run">{busy ? 'Starting…' : started ? 'Start again (replaces this queue)' : 'Start the labelling queue'}</button></div>
          </div>
        </div>
      )}
    </div>
  )
}

function Hero({ al, metric }: { al: ActiveLearning; metric: string }) {
  const s = al.summary!
  const v = s.verdict
  const meta = VERDICT[v.label]
  const q = s.queue
  const last = q[q.length - 1]
  const fin = s.comparison?.rounds.find((r) => r.round === v.round)
  return <>
    <div className="card navy explain-hero" data-testid="al-hero">
      <div className="eh-cell lead"><span className="micro-label">active vs random{s.comparison ? ' · simulated' : ''}</span><b data-testid="al-verdict">{meta.head}</b><small>{meta.say || v.reason}</small></div>
      {v.gap != null && <div className="eh-cell"><span className="micro-label">d̄ after round {v.round} (n = {nfmt(v.n ?? 0)})</span><b className="mono" data-testid="al-gap">{sgn(v.gap)}</b><small>95% CI {v.ci ? `[${sgn(v.ci[0])}, ${sgn(v.ci[1])}]` : '—'} · {v.wins}/{v.n_seeds} seeds</small></div>}
      {fin && <div className="eh-cell"><span className="micro-label">{metric}, active</span><b className="mono">{fmtScore(fin.active.mean)}</b><small>± {fin.active.sd?.toFixed(4) ?? '—'} · random {fmtScore(fin.random.mean)} ± {fin.random.sd?.toFixed(4) ?? '—'} ({fin.gap.n_seeds} seeds)</small></div>}
      <div className="eh-cell"><span className="micro-label">your queue · labels used</span><b className="mono" data-testid="al-labels">{nfmt(last?.n_labels ?? 0)}</b><small>{s.labels_used.map(nfmt).join(' → ')}</small></div>
      <div className="eh-cell"><span className="micro-label">your queue · {metric}</span><b className="mono">{fmtScore(last?.score)}</b><small>{q.map((p) => fmtScore(p.score)).join(' → ')}</small></div>
    </div>
    <p className="helper" style={{ fontSize: 12.5 }} data-testid="al-reason"><StatusPill status={meta.pill}>{v.label}</StatusPill>{' '}{v.reason}.
      {s.comparison ? <> The comparison's labeller is an <b>oracle</b>: {s.comparison.labeller}.</> : null}</p>
  </>
}

function Chart({ al, metric }: { al: ActiveLearning; metric: string }) {
  const s = al.summary!
  const arms = al.arms ?? {}
  const rounds = s.comparison?.rounds ?? []
  const q = al.queue!.points
  const seeds = s.comparison?.seeds ?? 0
  const dots = (kind: string) => Array.from({ length: seeds }, (_, i) => arms[`${kind}/${i}`]?.points ?? []).flat().map((p) => [p.n, p.score] as [number, number])
  const ys = [...q.map((p) => p.score), ...dots('active').map((p) => p[1]), ...dots('random').map((p) => p[1])]
  const [y0, y1] = ys.length ? padDomain(ys, 0.12) : [0, 1]
  const xs = [...q.map((p) => p.n), ...rounds.flatMap((r) => r.n)]
  const x0 = Math.min(...xs), x1 = Math.max(...xs)
  const pad = Math.max(20, (x1 - x0) * 0.06)
  const off = (x1 - x0 || 1) * 0.012
  const series = [
    ...(seeds ? [
      { name: 'active (each seed)', color: 'var(--viz-a)', width: 0, dots: true, dotR: () => 2.5, points: dots('active').map(([x, y]) => [x - off, y] as [number, number]), tip: () => 'active, one seed' },
      { name: 'random (each seed)', color: 'var(--viz-b)', width: 0, dots: true, dotR: () => 2.5, points: dots('random').map(([x, y]) => [x + off, y] as [number, number]), tip: () => 'random, one seed' },
      { name: 'active mean', color: 'var(--viz-a)', points: rounds.map((r) => [r.n[0] - off, r.active.mean] as [number, number]) },
      { name: 'random mean', color: 'var(--viz-b)', dash: '5 4', points: rounds.map((r) => [r.n[0] + off, r.random.mean] as [number, number]) },
    ] : []),
    { name: 'your queue', color: 'var(--text-primary)', dots: true, dotR: () => 4, points: q.map((p) => [p.n, p.score] as [number, number]), tip: (i: number) => `round ${q[i]?.round} · ${Object.entries(q[i]?.answered_by ?? {}).map(([k, v]) => `${v} by ${k}`).join(', ') || 'seed labels'}` },
  ]
  return (
    <PlotFrame title="Score Against Labels Used"
      chips={<><ParamChip sym="b" value={`${s.budget}/round`} /><ParamChip sym="holdout" value={`${al.holdout_rows?.toLocaleString()} rows`} /><ParamChip sym="seeds" value={`${seeds}`} /></>}
      legend={[{ name: 'your queue', color: 'var(--text-primary)', data: [] }, ...(seeds ? [{ name: 'active, simulated (mean, seeds)', color: 'var(--viz-a)', data: [] }, { name: 'random, simulated (mean, seeds)', color: 'var(--viz-b)', dash: '5 4', data: [] }] : [])]}
      caption={<>holdout {metric} of each refit against the labelled training rows n; every fit is the run's call and is scored on the same {al.holdout_rows?.toLocaleString()} holdout rows (<span className="mono">{al.holdout_sha}</span>). Simulated arms are labelled by the oracle (the hidden labels), with whiskers ±1 sd over seeds; active and random are drawn a little apart at each n.</>}>
      <div data-testid="al-chart">
        <XYPlot height={280} series={series}
          x={{ min: x0 - pad, max: x1 + pad, ticks: [...new Set(xs)].sort((a, b) => a - b).filter((t, i, arr) => i === 0 || t - arr[i - 1] > (x1 - x0) * 0.04), title: 'labelled training rows n', fmt: (t) => nfmt(t) }}
          y={{ min: y0, max: y1, ticks: niceTicks(y0, y1, 5), title: metric, fmt: (t) => fmtScore(t, 3) }}>
          {(sx, sy) => <g>
            {rounds.flatMap((r) => (['active', 'random'] as const).map((k) => {
              const a = r[k]; const x = r.n[0] + (k === 'active' ? -off : off)
              if (a.sd == null) return null
              return <line key={`${r.round}${k}`} x1={sx(x)} x2={sx(x)} y1={sy(a.mean - a.sd)} y2={sy(a.mean + a.sd)} stroke={k === 'active' ? 'var(--viz-a)' : 'var(--viz-b)'} strokeWidth={1.5} />
            }))}
          </g>}
        </XYPlot>
      </div>
    </PlotFrame>
  )
}

function Formula({ al, metric }: { al: ActiveLearning; metric: string }) {
  const s = al.summary!
  const fin = s.comparison?.rounds.find((r) => r.round === s.rounds)
  const sel = al.queue?.selection?.[al.queue.selection.length - 1]
  const strat = (s.strategy as keyof typeof U_FORMULA) in U_FORMULA ? s.strategy as keyof typeof U_FORMULA : 'margin'
  return (
    <div className="grid mono-grid">
      <div className="formula" data-testid="al-formula">
        <div className="micro-label">How rows are picked</div>
        <div className="eq">{U_FORMULA[strat]}</div>
        <div className="eq">C = the {sel ? nfmt(sel.candidates) : 'β·b'} rows of P with the largest u · pick b = {s.budget} of C by {s.diversity === 'cluster' ? <>weighted k-means (weights u) into b clusters, the largest u in each</> : s.diversity === 'kcenter' ? <>greedy k-center: argmax<sub>x∈C</sub> min<sub>z∈L∪S</sub> ‖e(x) − e(z)‖</> : <>u alone</>}</div>
        {sel?.u_mean_picked != null && <div className="eq">ū<sub>picked</sub> = <b className="mono">{sel.u_mean_picked.toFixed(3)}</b> vs ū<sub>P</sub> = {sel.u_mean_pool?.toFixed(3)} (round {sel.round})</div>}
        <dl className="where">
          <dt>p̂<sub>(1)</sub>, p̂<sub>(2)</sub></dt><dd>the largest and second-largest class probabilities of the current round's refit</dd>
          {strat === 'entropy' && <><dt>K</dt><dd>{al.classes?.length} classes</dd></>}
          <dt>P, L</dt><dd>the unlabelled pool ({nfmt(al.pool?.n_pool ?? 0)} rows at the start) and the labelled set ({nfmt(al.pool?.n_initial ?? 0)} seed labels); the {nfmt(al.holdout_rows ?? 0)} holdout rows are in neither ({al.holdout_in_pool ?? 0} in the pool)</dd>
          <dt>β</dt><dd>{al.request?.candidates_factor}: the diversity step chooses among the β·b most uncertain rows</dd>
          <dt>e(x)</dt><dd>{al.embedding?.dims}-d embedding: {al.embedding?.method}</dd>
        </dl>
      </div>
      {fin && <div className="formula" data-testid="al-gap-formula">
        <div className="micro-label">Active vs random (simulated)</div>
        <div className="eq"><i>d</i><sub>s</sub> = {s.greater_is_better ? '' : '−('}<i>m</i>(active<sub>s</sub>) − <i>m</i>(random<sub>s</sub>){s.greater_is_better ? '' : ')'} = {fin.gap.per_seed.map((v) => sgn(v)).join(', ')}</div>
        <div className="eq"><i>d̄</i> ± <i>t</i><sub>{fin.gap.n_seeds - 1}</sub> · <i>s</i><sub>d</sub> ⁄ √{fin.gap.n_seeds} = {sgn(fin.gap.mean)} ± {fin.gap.ci ? ((fin.gap.ci[1] - fin.gap.ci[0]) / 2).toFixed(4) : '—'} → <b className="mono">{fin.gap.ci ? `[${sgn(fin.gap.ci[0])}, ${sgn(fin.gap.ci[1])}]` : '—'}</b></div>
        <dl className="where">
          <dt>m</dt><dd>the holdout {metric} after round {fin.round}, at n = {nfmt(fin.n[0])} labels in both arms ({s.greater_is_better ? 'greater is better' : 'lower is better, so the sign is flipped: + = active better'})</dd>
          <dt>s</dt><dd>a seed: its own seed labels L₀(s) ({pctTrim(1 - (al.pool?.hide_share ?? 0))} stratified), shared by both arms, and its own random draws</dd>
          <dt>s<sub>d</sub></dt><dd>the sd of the {fin.gap.n_seeds} paired gaps, {fin.gap.sd?.toFixed(4) ?? '—'}; t: Student's 97.5% quantile</dd>
          <dt>oracle</dt><dd>both arms are labelled with the dataset's own hidden labels, a stand-in for a person</dd>
        </dl>
      </div>}
    </div>
  )
}

function Queue({ al, onChange }: { al: ActiveLearning; onChange: () => void }) {
  const q = al.queue!
  const [items, setItems] = useState<ALItem[]>(q.queue)
  const [sel, setSel] = useState<ALItem | null>(q.queue[0] ?? null)
  const [page, setPage] = useState(0)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  useEffect(() => { setItems(q.queue); setSel((s) => q.queue.find((i) => i.id === s?.id) ?? q.queue[0] ?? null) }, [q.queue])
  const editable = q.open
  const classes = al.classes ?? []
  const answered = items.filter((i) => i.answer).length
  const shown = useMemo(() => items.slice(page * PAGE, (page + 1) * PAGE), [items, page])

  const answer = (it: ALItem, ans: 'confirm' | 'relabel' | 'skip' | null, label?: string) => {
    if (!editable) return
    setItems((x) => x.map((i) => (i.id === it.id ? { ...i, answer: ans ?? undefined, answer_label: ans === 'confirm' ? i.suggested : ans === 'relabel' ? label : undefined, answered_by: 'you' } : i)))
    api.runs.answerActiveLearning(al.run_id, [{ id: it.id, answer: ans, label }]).catch((e) => { setErr((e as Error).message); onChange() })
  }
  const simulate = async () => { setBusy(true); try { await api.runs.simulateActiveLearning(al.run_id); onChange() } catch (e) { setErr((e as Error).message) } finally { setBusy(false) } }
  const submit = async () => { setBusy(true); try { await api.runs.submitActiveLearning(al.run_id); onChange() } catch (e) { setErr((e as Error).message) } finally { setBusy(false) } }
  const byWho = items.reduce<Record<string, number>>((m, i) => (i.answered_by ? { ...m, [i.answered_by]: (m[i.answered_by] ?? 0) + 1 } : m), {})

  if (!items.length) return null
  return (
    <div className={`card inbox ${editable ? 'open' : ''}`} data-testid="al-queue">
      <div className="card-head">
        <div className="left"><h3 className="card-title">Labelling Queue · Round {q.queue_round}</h3>
          <span className="helper" style={{ fontSize: 12 }}>{items.length} unlabelled training rows the round-{(q.queue_round ?? 1) - 1} refit is least sure of, spread over the pool</span></div>
        {editable ? <StatusPill status="warning">{items.length - answered} Need You</StatusPill>
          : q.submitted_pending ? <StatusPill status="neutral">Refitting</StatusPill> : <StatusPill status="success">Done</StatusPill>}
      </div>
      <div className="inbox-top">
        <div className="formula compact">
          <div className="eq"><i>m</i>(<i>x</i>) = <i>p̂</i><sub>(1)</sub>(<i>x</i>) − <i>p̂</i><sub>(2)</sub>(<i>x</i>)
            {sel && <span className="mono sub-eq"> = {sel.p_top[0][1].toFixed(3)} − {(sel.p_top[1]?.[1] ?? 0).toFixed(3)} = <b>{sel.margin.toFixed(3)}</b> · u = <b>{sel.uncertainty.toFixed(3)}</b></span>}</div>
          <dl className="where">
            <dt>x</dt><dd>a pool row{sel ? <> (row <b className="mono">{sel.row}</b>, rank <b className="mono">{sel.rank}</b> by u)</> : null}</dd>
            <dt>p̂<sub>(1)</sub></dt><dd>the refit's top class{sel ? <> = <b>{sel.p_top[0][0]}</b> ({sel.p_top[0][1].toFixed(3)}): the model's guess, shown as "suggested"</> : null}</dd>
            <dt>p̂<sub>(2)</sub></dt><dd>the runner-up{sel?.p_top[1] ? <> = <b>{sel.p_top[1][0]}</b> ({sel.p_top[1][1].toFixed(3)})</> : null}; a small m(x) means the model cannot separate them</dd>
            <dt>answer</dt><dd>confirm = the guess is right · relabel = give the true class · skip = can't tell (the row leaves the pool); open rows count as skipped</dd>
          </dl>
        </div>
        <div className="recall-box" data-testid="al-queue-stats">
          <span className="micro-label">This round</span>
          <div className="recall-big"><b>{answered}</b><span>of {items.length} answered</span></div>
          <dl className="where tight">
            {Object.entries(byWho).map(([k, v]) => <Fragment key={k}><dt>{k}</dt><dd>{v} answers{k === 'simulated-oracle' ? ' (simulated from the hidden labels, not a person)' : ''}</dd></Fragment>)}
            <dt>labelled</dt><dd>{nfmt(q.n_labelled)} rows so far ({al.pool?.n_initial} seed labels)</dd>
            {al.versions?.length ? <><dt>versions</dt><dd>{al.versions.map((v) => <span key={v.id} className="mono">{v.id} </span>)}</dd></> : null}
          </dl>
        </div>
      </div>
      <div className="inbox-bar">
        {err && <span className="helper" role="alert">✕ {err}</span>}
        <span className="spacer" />
        {editable && <>
          {al.pool?.source === 'hide' && <button className="btn sm" disabled={busy} onClick={simulate} data-testid="al-simulate" title="Fill unanswered rows from the hidden labels, as reviewer simulated-oracle">Fill from Hidden Labels (Simulated)</button>}
          <button className="btn sm primary" disabled={busy} onClick={submit} data-testid="al-submit">Submit {answered} Labels &amp; Retrain</button>
        </>}
      </div>
      <div className="table-wrap inbox-scroll">
        <table className="table inbox-table">
          <thead><tr><th>Row</th><th>Text / features</th><th>Suggested</th><th className="r">p̂<sub>(1)</sub></th><th className="r math-th"><i>m</i>(<i>x</i>)</th><th className="r">u</th><th>Why</th><th>Answer</th></tr></thead>
          <tbody>
            {shown.map((it) => (
              <tr key={it.id} className={sel?.id === it.id ? 'sel' : ''} onClick={() => setSel(it)} data-testid="al-item">
                <td className="mono">{it.row}<div className="muted" style={{ fontSize: 10.5 }}>#{it.rank}</div></td>
                <td className="txt">{it.text ?? Object.entries(it.features).map(([k, v]) => `${k}=${v}`).join(' · ')}
                  {it.text && <div className="muted" style={{ fontSize: 11 }}>{Object.entries(it.features).map(([k, v]) => `${k}=${v}`).join(' · ')}</div>}</td>
                <td><b>{it.suggested}</b><div className="muted" style={{ fontSize: 11 }}>or {it.p_top[1]?.[0]}</div></td>
                <td className="r mono">{it.p_top[0][1].toFixed(2)}</td>
                <td className="r mono">{it.margin.toFixed(3)}</td>
                <td className="r mono">{it.uncertainty.toFixed(3)}</td>
                <td className="why">{it.why}</td>
                <td onClick={(e) => e.stopPropagation()}>
                  <div className="ans">
                    {(['confirm', 'relabel', 'skip'] as const).map((x) => (
                      <button key={x} className={`ab ${it.answer === x ? 'on' : ''}`} disabled={!editable} onClick={() => answer(it, it.answer === x ? null : x, x === 'relabel' ? it.p_top[1]?.[0] ?? classes.find((k) => k !== it.suggested) : undefined)}>{x}</button>
                    ))}
                    {it.answer === 'relabel' && (
                      <select className="select sm" value={it.answer_label ?? ''} disabled={!editable} onChange={(e) => answer(it, 'relabel', e.target.value)}>
                        {classes.filter((k) => k !== it.suggested).map((k) => <option key={k}>{k}</option>)}
                      </select>
                    )}
                    {it.answered_by && it.answered_by !== 'you' && <span className="muted" style={{ fontSize: 10.5 }}>{it.answered_by}{it.answer_label && it.answer === 'relabel' ? ` → ${it.answer_label}` : ''}</span>}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="inbox-bar" style={{ borderTop: '1px solid var(--border-default)' }}>
        <span className="helper" style={{ fontSize: 12 }}>{items.length} rows · {answered} answered · the holdout is never in the queue</span>
        <span className="spacer" />
        <button className="btn sm ghost" disabled={page === 0} onClick={() => setPage(page - 1)}>←</button>
        <span className="mono" style={{ fontSize: 12 }}>{page + 1} / {Math.max(1, Math.ceil(items.length / PAGE))}</span>
        <button className="btn sm ghost" disabled={(page + 1) * PAGE >= items.length} onClick={() => setPage(page + 1)}>→</button>
      </div>
    </div>
  )
}

function Rounds({ al, metric }: { al: ActiveLearning; metric: string }) {
  const q = al.queue!
  const hist = q.history ?? []
  const rounds = al.summary?.comparison?.rounds ?? []
  return (
    <div className="stack" style={{ gap: 'var(--space-16)' }}>
      <div className="card" data-testid="al-rounds">
        <div className="card-head"><div className="left"><h3 className="card-title">Your Rounds</h3><span className="helper" style={{ fontSize: 12 }}>each submitted round is a dataset version with lineage, then a refit</span></div></div>
        <div className="card-body" style={{ overflowX: 'auto' }}>
          <table className="table compact"><thead><tr><th>round</th><th className="r">labels n</th><th className="r">added</th><th>by</th><th className="r">{metric}</th><th>version</th></tr></thead>
            <tbody>{q.points.map((p) => { const h = hist.find((x) => x.round === p.round); return <tr key={p.round}>
              <td className="mono">{p.round}</td><td className="r mono">{nfmt(p.n)}</td><td className="r mono">{p.round ? p.added : '—'}{h?.skipped ? <span className="muted"> ({h.skipped} skipped)</span> : null}</td>
              <td style={{ fontSize: 12 }}>{p.round ? Object.entries(p.answered_by ?? {}).map(([k, v]) => `${k} ${v}`).join(', ') : 'seed labels'}{p.agreement_with_hidden != null ? <span className="muted"> · {pctTrim(p.agreement_with_hidden)} agree with hidden</span> : null}</td>
              <td className="r mono"><b>{fmtScore(p.score)}</b></td><td className="mono" style={{ fontSize: 12 }}>{p.dataset_id ? <a href={`#/data?dataset=${p.dataset_id}`}>{p.dataset_id}</a> : '—'}</td>
            </tr> })}</tbody></table>
        </div>
      </div>
      {rounds.length > 0 && <div className="card" data-testid="al-compare">
        <div className="card-head"><div className="left"><h3 className="card-title">Active vs Random, Every Round</h3><span className="helper" style={{ fontSize: 12 }}>simulated with the oracle; paired by seed; 95% t-interval of d̄</span></div></div>
        <div className="card-body" style={{ overflowX: 'auto' }}>
          <table className="table compact"><thead><tr><th>round</th><th className="r">n</th><th className="r">active x̄ ± s</th><th className="r">random x̄ ± s</th><th className="r">d̄</th><th className="r">95% CI</th><th className="r">wins</th></tr></thead>
            <tbody>{rounds.map((r) => <tr key={r.round}>
              <td className="mono">{r.round}</td><td className="r mono">{nfmt(r.n[0])}</td>
              <td className="r mono">{fmtScore(r.active.mean)} ± {r.active.sd?.toFixed(4) ?? '—'}</td>
              <td className="r mono">{fmtScore(r.random.mean)} ± {r.random.sd?.toFixed(4) ?? '—'}</td>
              <td className="r mono"><b>{sgn(r.gap.mean)}</b></td><td className="r mono">{r.gap.ci ? `[${sgn(r.gap.ci[0])}, ${sgn(r.gap.ci[1])}]` : '—'}</td>
              <td className="r mono">{r.round ? `${r.gap.wins}/${r.gap.n_seeds}` : 'same fit'}</td>
            </tr>)}</tbody></table>
        </div>
      </div>}
    </div>
  )
}
