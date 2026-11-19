import { useEffect, useState } from 'react'
import { api } from '@/shared/api/client'
import type { SplitOptions, SplitSpec, SplitStrategy } from '@/shared/api/types'
import { STRATEGY_NAME, specFor } from '@/features/quick-train/components/SplitCard'
import type { Body } from './body'

/**
 * F19: the Gym gets the two bank-validation choices New Model has, written to the same body keys POST /runs reads:
 * `split_spec` (F6: random, group, out-of-time, out-of-time + group) and `cost` (F7: c_FP, c_FN, g_TP per decision,
 * which becomes the eval_metric expected_cost). The split's timeline is drawn in New Model and on the run's results.
 */
export function SplitCostCards({ body, setBody, pt }: { body: Body; setBody: (b: Body) => void; pt: string }) {
  const [opts, setOpts] = useState<SplitOptions | null>(null)
  const [err, setErr] = useState('')
  const ds = body.dataset_id as string | undefined
  const label = body.label as string | null | undefined
  useEffect(() => {
    if (!ds || !label) return
    let alive = true
    setOpts(null); setErr('')
    api.datasets.splitOptions(ds, label).then((o) => alive && setOpts(o)).catch((e) => alive && setErr((e as Error).message))
    return () => { alive = false }
  }, [ds, label])
  const spec = (body.split_spec ?? null) as SplitSpec | null
  const strategy: SplitStrategy = spec?.strategy ?? 'random-stratified'
  const strategies = opts?.strategies ?? ['random-stratified']
  const cost = (body.cost ?? null) as { c_fp: number; c_fn: number; g_tp?: number } | null
  const putCost = (k: 'c_fp' | 'c_fn' | 'g_tp', x: number) => setBody({ ...body, eval_metric: null, cost: { c_fp: 1, c_fn: 5, g_tp: 0, ...(cost ?? {}), [k]: x } })
  const tStar = cost ? (cost.c_fp) / (cost.c_fp + cost.c_fn + (cost.g_tp ?? 0)) : null
  return (
    <>
      <div className="card" data-testid="gym-split">
        <div className="card-head"><h3 className="card-title">Validation split</h3><span className="helper" style={{ fontSize: 12 }}><span className="mono">split_spec</span> · how the holdout H is drawn (F6)</span></div>
        <div className="card-body">
          <div className="gym-row"><div className="label"><b>Strategy</b><small>{strategy === 'random-stratified' ? 'rows drawn at random, stratified on y' : strategy === 'group' ? `no ${spec?.group_column ?? 'entity'} straddles train and H` : `train before the cut-off on ${spec?.time_column ?? 'the date'}, H after it`}</small></div>
            <div className="ctl">
              <div className="seg" role="radiogroup" aria-label="Split strategy" data-testid="gym-split-strategy">
                {(['random-stratified', 'group', 'time', 'time-group'] as SplitStrategy[]).map((s) => (
                  <button key={s} role="radio" aria-checked={s === strategy} className={s === strategy ? 'on' : ''} disabled={!strategies.includes(s)}
                    title={strategies.includes(s) ? undefined : 'this dataset has no column for it'}
                    onClick={() => setBody({ ...body, split_spec: specFor(s, opts, spec) })}>{STRATEGY_NAME[s]}</button>
                ))}
              </div>
            </div>
          </div>
          {spec && <p className="helper mono" style={{ fontSize: 12 }}>{[spec.group_column && `group = ${spec.group_column}`, spec.time_column && `time = ${spec.time_column}`, spec.cutoff && `cut-off ${spec.cutoff}`, `tuning share ${spec.tuning_share ?? 0.2}`].filter(Boolean).join(' · ')}</p>}
          {opts?.warnings.length && strategy === 'random-stratified' ? <p className="helper" style={{ fontSize: 12 }}>▲ {opts.warnings[0]} Suggested: <b>{STRATEGY_NAME[opts.suggested]}</b>.</p> : null}
          {err && <p className="helper" style={{ fontSize: 12 }}>✕ {err}</p>}
        </div>
      </div>
      {pt === 'binary' && (
        <div className="card" data-testid="gym-cost">
          <div className="card-head"><h3 className="card-title">Business cost</h3><span className="helper" style={{ fontSize: 12 }}><span className="mono">cost</span> · becomes eval_metric <span className="mono">expected_cost</span> (F7)</span></div>
          <div className="card-body">
            <div className="gym-row"><div className="label"><b>Optimise expected cost</b><small>model selection, ensemble weights and the threshold t minimise cost per decision</small></div>
              <div className="ctl"><div className="seg" role="radiogroup" aria-label="Business cost" data-testid="gym-cost-on">
                <button role="radio" aria-checked={!cost} className={!cost ? 'on' : ''} onClick={() => setBody({ ...body, cost: null })}>off</button>
                <button role="radio" aria-checked={!!cost} className={cost ? 'on' : ''} onClick={() => setBody({ ...body, eval_metric: null, calibrate_threshold: 'auto', cost: cost ?? { c_fp: 1, c_fn: 5, g_tp: 0 } })}>on</button>
              </div></div>
            </div>
            {cost && <div className="row" style={{ gap: 'var(--space-16)', flexWrap: 'wrap' }}>
              {([['c_fp', 'c', 'FP', 'false positive'], ['c_fn', 'c', 'FN', 'false negative'], ['g_tp', 'g', 'TP', 'true positive (gain)']] as const).map(([k, s, sb, d]) => (
                <label key={k} className="stack" style={{ gap: 2 }}>
                  <span className="helper" style={{ fontSize: 12 }}><span className="math">{s}<sub>{sb}</sub></span> {d}</span>
                  <input className="num-input" style={{ width: 90 }} type="number" min={0} value={cost[k] ?? 0} data-testid={`gym-cost-${k}`}
                    onChange={(e) => putCost(k, Math.max(0, Number(e.target.value) || 0))} />
                </label>
              ))}
              {tStar != null && <p className="helper" style={{ fontSize: 12, alignSelf: 'flex-end' }}>Bayes threshold for calibrated p̂: <span className="math">t*</span> = c<sub>FP</sub> / (c<sub>FP</sub> + c<sub>FN</sub> + g<sub>TP</sub>) = <b className="mono">{tStar.toFixed(3)}</b></p>}
            </div>}
          </div>
        </div>
      )}
    </>
  )
}
