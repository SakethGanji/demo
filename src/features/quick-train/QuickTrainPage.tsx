import { useEffect, useMemo, useRef, useState } from 'react'
import { ConfusionMatrix } from '@/shared/components/ConfusionMatrix'
import { LineChart, type Series } from '@/shared/components/LineChart'
import { PlotFrame } from '@/shared/components/PlotFrame'
import { RunString, type RunState } from '@/shared/components/RunString'
import { rng } from '@/shared/lib/random'
import { ParamChip } from '@/shared/primitives/ParamChip'
import { StatusPill } from '@/shared/primitives/StatusPill'
import { Boosting } from './components/Boosting'
import { DatasetStep } from './components/DatasetStep'
import { LiveRun } from './components/LiveRun'
import { useApi } from '@/shared/api/mode'
import { RecipeStep } from './components/RecipeStep'
import { ClassStrings } from './components/ClassStrings'
import { PipelineString } from './components/PipelineString'
import { SAMPLES, type Dataset } from './datasets'
import { FOLDS, PH, RECIPES, expectedF1, fitFrac, fitSeconds, mmss, phaseOf, type Recipe } from './recipes'

export function QuickTrainPage({ go }: { go: (r: string) => void }) {
  const fromGym = useRef(location.hash.includes('?start')).current
  const [step, setStep] = useState(fromGym ? 2 : 0)
  const [ds, setDs] = useState<Dataset>(SAMPLES[0])
  const [recipeId, setRecipeId] = useState<Recipe['id']>('ovr')
  const [t, setT] = useState(0)
  const [running, setRunning] = useState(fromGym)
  const [speed, setSpeed] = useState(1)
  const recipe = RECIPES.find((r) => r.id === recipeId)!
  const { mode } = useApi()
  const K = ds.classes.length

  const pick = (d: Dataset) => { setDs(d); setRecipeId(d.kind === 'tabular' ? 'xgb' : 'ovr') }

  useEffect(() => {
    if (!running) return
    const iv = setInterval(() => setT((v) => { const n = Math.min(1, v + 0.0045 * speed); if (n >= 1) setRunning(false); return n }), 70)
    return () => clearInterval(iv)
  }, [running, speed])
  const start = () => { setT(0); setRunning(true); setStep(2) }

  // everything below is derived from t
  const phase = phaseOf(t)
  const ff = fitFrac(t)
  const fold = Math.min(FOLDS - 1, Math.floor(ff * FOLDS))
  const inFold = ff >= 1 ? 1 : ff * FOLDS - fold
  const finals = useMemo(() => {
    const r = rng(ds.name.length * 31 + K * 7 + recipe.id.charCodeAt(0))
    const base = expectedF1(recipe, ds)
    // per-class scores average exactly to the macro score, and the fold scores average to it too
    const rawCls = ds.classes.map((c) => base + (r() - 0.5) * 0.05 - (c.share < 0.1 ? 0.06 : 0))
    const shift = base - rawCls.reduce((a, b) => a + b, 0) / K
    const cls = rawCls.map((v) => Math.min(0.985, v + shift))
    const noise = Array.from({ length: FOLDS }, () => (r() - 0.5) * 0.014)
    const noiseMean = noise.reduce((a, b) => a + b, 0) / FOLDS
    const folds = noise.map((n) => base + n - noiseMean)
    const rates = ds.classes.map(() => 0.95 + r() * 0.5)
    return { cls, folds, rates, base }
  }, [ds, recipe.id, K])
  const conv = finals.rates.map((rt) => (phase < 3 ? 0 : phase > 3 ? 1 : Math.min(1, inFold * 1.08 * rt)))
  const f1k = finals.cls.map((v, k) => v * (0.55 + 0.45 * Math.pow(conv[k], 0.6)))
  const meanF1 = finals.folds.reduce((a, b) => a + b, 0) / FOLDS
  const spread = Math.sqrt(finals.folds.reduce((a, b) => a + (b - meanF1) ** 2, 0) / FOLDS)
  const d = ds.kind === 'text' ? Math.round(ds.rows * 1.208193) : ds.cols.length * 6
  const elapsed = t * fitSeconds(recipe, ds)
  const notes = [
    phase > 0 ? `${ds.rows.toLocaleString()} rows` : 'reading', phase > 1 ? `k = ${FOLDS} · stratified` : '',
    phase > 2 ? `d = ${recipe.id === 'xgb' && ds.kind === 'text' ? '300' : d.toLocaleString()}` : phase === 2 ? 'fitting vocabulary' : '',
    phase === 3 ? `fold ${fold + 1} of ${FOLDS}` : phase > 3 ? `${FOLDS} folds fit` : '', phase > 4 ? `F1: ${meanF1.toFixed(3)}` : phase === 4 ? 'scoring holdout' : '',
  ]
  const foldRuns: RunState[] = Array.from({ length: FOLDS }, (_, i) => {
    const st = phase < 3 || i > fold ? 'Queued' : i < fold || ff >= 1 ? 'Completed' : 'Training'
    return { id: `fold_${i + 1}`, label: `${Math.round(ds.rows / FOLDS).toLocaleString()} validation rows`, status: st, progress: st === 'Completed' ? 1 : st === 'Training' ? Math.max(0.04, inFold) : 0, readout: st === 'Completed' ? `F1: ${finals.folds[i].toFixed(3)}` : st === 'Training' ? `fitting · ${(inFold * 100).toFixed(0)}%` : 'waiting' }
  })
  const pts = Math.round(ff * 60)
  const loss: Series[] = useMemo(() => {
    const r = rng(11)
    const full = Array.from({ length: 61 }, (_, i) => [0.2 + 0.95 * Math.exp(-i / 11) + (r() - 0.5) * 0.02, 0.27 + 0.95 * Math.exp(-i / 13) + (r() - 0.5) * 0.03])
    return [
      { name: 'Train loss', color: 'var(--viz-a)', data: full.map((p) => p[0]) },
      { name: 'Validation loss', color: 'var(--viz-c)', dash: '6 4', data: full.map((p) => p[1]) },
    ]
  }, [])
  const logs = [
    [0, 'lt', `$ ensemble train --data ${ds.name} --target ${ds.target} --recipe ${recipe.id}`],
    [0.01, 'lt', `[INFO] loading ${ds.name}`], [PH[1], 'lm', `[OK  ] ${ds.rows.toLocaleString()} rows × ${ds.cols.length} cols · target=${ds.target} · K=${K}`],
    [PH[2], 'lt', `[INFO] split StratifiedKFold(k=${FOLDS}, seed=42)`], [PH[2] + 0.02, 'li', `[FIT ] ${recipe.stages(ds)[2]} on ${Math.round(ds.rows * 0.8).toLocaleString()} rows`],
    [PH[3], 'lm', `[OK  ] features ready · X ∈ ${ds.kind === 'text' ? 'sparse ' : ''}ℝⁿˣᵈ`],
    ...Array.from({ length: FOLDS }, (_, i) => [[PH[3] + (i / FOLDS) * (PH[4] - PH[3]) + 0.01, 'li', `[FIT ] fold=${i + 1} ${recipe.viz === 'boost' ? 'boosting 600 rounds η=0.05' : `fitting ${K} one-vs-rest classifiers`}`], [PH[3] + ((i + 1) / FOLDS) * (PH[4] - PH[3]), 'lm', `[METRIC] fold=${i + 1} macro_f1=${finals.folds[i].toFixed(3)}`]]).flat(),
    [PH[4] + 0.03, 'li', '[EVAL] scoring holdout · confusion matrix'], [1, 'lm', `[DONE] macro_f1=${meanF1.toFixed(3)} ± ${spread.toFixed(3)} · artifacts written`],
  ].filter((l) => t >= (l[0] as number) && (t > 0 || running)) as [number, string, string][]
  const confusion = useMemo(() => {
    const r = rng(K * 97 + ds.rows)
    return ds.classes.map((c, i) => {
      const tot = Math.round(ds.rows * 0.15 * c.share)
      const hit = Math.round(tot * finals.cls[i])
      const rest = ds.classes.map((_, j) => (j === i ? 0 : r() + 0.1)); const rs = rest.reduce((a, b) => a + b, 0)
      return ds.classes.map((_, j) => (j === i ? hit : Math.round(((tot - hit) * rest[j]) / rs)))
    })
  }, [ds, finals, K])
  const done = t >= 1
  const stepNow = step < 2 ? step : done ? 3 : 2
  const STEPS = ['Dataset', 'Recipe', 'Train', 'Results']

  return (
    <div className="wizard">
      <div className="stepper-bar">
        <div className="stepper-title">
          <h1 className="page-title">Quick Train</h1>
          <span className="helper">Give it a dataset, pick a recipe, watch it fit.</span>
          <span className="spacer" />
          <span className="chip">{ds.name}</span>{step > 0 && <span className="chip">{recipe.pipeline(ds)}</span>}
        </div>
        <div className="stepper" role="list" style={{ gridTemplateColumns: 'repeat(4, 1fr)', maxWidth: 720 }}>
          {STEPS.map((s, i) => (
            <button key={s} role="listitem" disabled={i > stepNow || running} className={`step ${i < stepNow || (done && i === 3) ? 'done' : i === stepNow ? 'current' : ''}`} onClick={() => i < 2 && setStep(i)}>
              <span className="node" /><span className="idx">{String(i + 1).padStart(2, '0')}</span><span>{s}</span>
            </button>
          ))}
        </div>
      </div>

      <div className="wizard-scroll">
        {step === 0 && <DatasetStep ds={ds} onPick={pick} />}

        {step === 1 && <RecipeStep ds={ds} recipeId={recipeId} onPick={setRecipeId} />}

        {step >= 2 && (
          <div className="page">
            {mode === 'live' && ds.id && <LiveRun ds={ds} />}
            <section className="stage">
              <div className="stage-head">
                <div>
                  <span className="hero-eyebrow" style={{ paddingLeft: 0 }}>{done ? 'RUN COMPLETE' : running ? 'TRAINING' : 'PAUSED'}</span>
                  <h2 className="hero-title">{recipe.pipeline(ds)}</h2>
                </div>
                <div className="stage-read">
                  <span><small>macro F1</small><b>{phase >= 3 ? (phase > 3 ? meanF1 : f1k.reduce((a, b) => a + b, 0) / K).toFixed(3) : '—'}</b></span>
                  <span><small>fold</small><b>{phase < 3 ? '—' : `${Math.min(FOLDS, fold + 1)} / ${FOLDS}`}</b></span>
                  <span><small>elapsed</small><b>{mmss(elapsed)}</b></span>
                  <span className="stage-sim" title="This run is simulated in your browser. No model is fitted.">simulated run</span>
                </div>
              </div>
              <PipelineString stages={recipe.stages(ds)} phase={phase} notes={notes} />
              <div className="stage-body">
                <div className="stage-cap">
                  <span className="micro-label" style={{ color: 'var(--text-on-shell-muted)' }}>{recipe.viz === 'strings' ? `${K} classifiers, one string each` : 'Boosting rounds'}</span>
                  <span className="ps-note">{recipe.viz === 'strings' ? 'A string rings while its loss is high and settles taut when it converges. Each fold plucks them again.' : 'Each tree fits the residual the trees before it left. The wave is what remains.'}</span>
                </div>
                {recipe.viz === 'strings'
                  ? <ClassStrings classes={ds.classes.map((c) => c.name)} conv={conv} f1={f1k} active={phase === 3} />
                  : <Boosting frac={phase < 3 ? 0 : phase > 3 ? 1 : inFold} M={600} active={phase === 3} done={phase > 3} />}
              </div>
              <div className="stage-foot">
                <div className="stage-prog"><i style={{ width: `${t * 100}%` }} /></div>
                {done ? <button className="btn sm" onClick={start}>Run Again</button>
                  : <button className="btn sm" onClick={() => setRunning(!running)}>{running ? 'Pause' : 'Resume'}</button>}
                <div className="seg stage-seg">{[1, 3].map((s) => <button key={s} className={speed === s ? 'on' : ''} onClick={() => setSpeed(s)}>{s}×</button>)}</div>
              </div>
            </section>

            <div className="grid two">
              <div className="stack">
                <div className="card run-strings">
                  <div className="card-head" style={{ marginTop: -4 }}><div className="left"><h3 className="card-title">Cross-Validation</h3><span className="helper" style={{ fontSize: 12 }}>one string per fold</span></div><ParamChip sym="k" value={String(FOLDS)} /></div>
                  {foldRuns.map((r) => <RunString key={r.id} run={r} folds={4} />)}
                </div>
                <PlotFrame title="Loss" legend={loss.map((s) => ({ ...s, data: [] }))} chips={<ParamChip sym="seed" value="42" />}
                  caption={<span>Cross-entropy <span className="math" style={{ fontSize: 14 }}>ℒ</span> per step <span className="math" style={{ fontSize: 14 }}>t</span>, mean of folds so far</span>}>
                  <LineChart series={loss.map((s) => ({ ...s, data: s.data.slice(0, Math.max(2, pts + 1)) }))} xMax={60} yMin={0} yMax={1.4} yTicks={[0, 0.35, 0.7, 1.05, 1.4]} xTicks={[0, 15, 30, 45, 60]} xTitle="t" yTitle="ℒ" fmtY={(v) => v.toFixed(2)} live={!done && phase === 3} height={200} />
                </PlotFrame>
              </div>
              <div className="stack">
                <div className="card">
                  <div className="card-head"><h3 className="card-title">Run Log</h3>{done ? <StatusPill status="success">Completed</StatusPill> : <StatusPill status="info">{running ? 'Streaming' : 'Paused'}</StatusPill>}</div>
                  <div className="card-body"><div className="log-panel" style={{ height: 250 }} aria-live="polite">{logs.slice(-12).map((l, i) => <div key={i} className={l[1]}>{l[2]}</div>)}</div></div>
                </div>
                {done && (
                  <div className="card navy">
                    <div className="card-head"><h3 className="card-title">Result</h3><StatusPill status="success">Ready To Register</StatusPill></div>
                    <div className="minis">
                      <div className="mini"><span className="micro-label">Macro F1</span><span className="v">{meanF1.toFixed(3)}</span><span className="u">± {spread.toFixed(3)} · n = {FOLDS}</span></div>
                      <div className="mini"><span className="micro-label">Weakest Class</span><span className="v">{Math.min(...finals.cls).toFixed(3)}</span><span className="u">{ds.classes[finals.cls.indexOf(Math.min(...finals.cls))].name}</span></div>
                      <div className="mini"><span className="micro-label">Fit Time</span><span className="v">{mmss(elapsed)}</span><span className="u">cpu-8x</span></div>
                    </div>
                    <div className="card-body row" style={{ borderTop: '1px solid var(--border-default)', flexWrap: 'wrap' }}>
                      <button className="btn primary" onClick={() => go('registry')}>Register Model Version</button>
                      <button className="btn" onClick={() => go('gym')}>Tune In Gym →</button>
                      <button className="btn ghost" onClick={() => { setStep(0); setT(0) }}>Train Another</button>
                    </div>
                  </div>
                )}
              </div>
            </div>

            {done && (
              <div className="card">
                <div className="card-head"><div className="left"><h3 className="card-title">Confusion Matrix</h3><span className="helper" style={{ fontSize: 12 }}>holdout, 15% of rows</span></div><ParamChip sym="n" value={Math.round(ds.rows * 0.15).toLocaleString()} /></div>
                <div className="card-body" style={{ maxWidth: 760 }}><ConfusionMatrix labels={ds.classes.map((c) => c.name)} m={confusion} /></div>
              </div>
            )}
          </div>
        )}
      </div>

      {step < 2 && (
        <div className="wizard-footer">
          <div>{step === 1 && <button className="btn ghost" onClick={() => setStep(0)}>← Back</button>}</div>
          {step === 0 && K < 2
            ? <div className="valid" style={{ color: 'var(--status-warning)' }}><span className="dot">▲</span> No label column with two or more classes</div>
            : <div className="valid"><span className="dot">●</span> {step === 0 ? `Schema Valid · target ${ds.target} · K = ${K}` : `${recipe.pipeline(ds)} · Ready`}</div>}
          <div className="right">{step === 0 ? <button className="btn primary" disabled={K < 2} onClick={() => setStep(1)}>Continue →</button> : <button className="btn primary" onClick={start}>Start Training</button>}</div>
        </div>
      )}
    </div>
  )
}
