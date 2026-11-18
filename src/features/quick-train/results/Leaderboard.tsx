import { PlotFrame } from '@/shared/components/PlotFrame'
import { useWidth } from '@/shared/hooks/useWidth'
import type { LeaderboardRow, ModelFamily, Run } from '@/shared/api/types'
import { FAMILY_SHORT, fmtScore, fmtSeconds, sub } from '../wizard'

const isEns = (r: LeaderboardRow) => r.family === 'ENS_WEIGHTED' || r.model.startsWith('WeightedEnsemble')

/** Every model AutoGluon fit, ranked by validation score, with its holdout score, fit time and predict time per row. */
export function Leaderboard({ run, selected, onSelect }: { run: Run; selected: string | null; onSelect: (m: string) => void }) {
  const lb = run.leaderboard ?? []
  const n = run.holdout?.n_rows ?? run.data?.holdout_rows ?? 0
  const metric = run.eval_metric?.name ?? ''
  const greater = run.eval_metric?.greater_is_better ?? true
  const bestTest = lb.filter((r) => r.score_test != null).reduce<LeaderboardRow | null>((b, r) => (!b || (r.score_test as number) > (b.score_test as number) ? r : b), null)
  return (
    <div className="card" data-testid="leaderboard">
      <div className="card-head">
        <div className="left"><h3 className="card-title">Leaderboard</h3><span className="helper" style={{ fontSize: 12 }}>{lb.length} models · ranked by validation {metric} ({greater ? 'higher' : 'lower'} is better) · click a model for its objective</span></div>
        <div className="row"><span className="chip"><span className="sym">n</span>={n.toLocaleString()} holdout</span></div>
      </div>
      <div className="table-wrap"><table className="table lb">
        <thead><tr><th>#</th><th>Model fₘ</th><th>Family</th><th className="r">L</th><th className="r">Validation μ ± σ</th><th className="r">Holdout</th><th className="r">Fit</th><th className="r">Predict / row</th><th>Ensemble weights</th></tr></thead>
        <tbody>
          {lb.map((r) => {
            const best = r.model === run.best_model
            const folds = (r.fold_scores_natural ?? r.fold_scores)?.length ?? 0
            const perRow = r.pred_time_test != null && n ? r.pred_time_test / n : null
            return (
              <tr key={r.model} data-testid={`lb-row-${r.model}`} data-best={best ? 'true' : 'false'} className={`${best ? 'best' : ''} ${selected === r.model ? 'sel' : ''}`} onClick={() => onSelect(r.model)}>
                <td className="table-cell muted">{r.rank}</td>
                <td className="table-cell"><b className="lb-name">{best && <span className="lb-star" title="the run's best model: predictions use it">★ </span>}{r.model}</b>{r.custom ? <span className="pill info lb-tag">yours</span> : null}</td>
                <td className="muted">{(r.family && FAMILY_SHORT[r.family]) ?? r.family ?? '—'}</td>
                <td className="table-cell r">L{sub(r.stack_level)}</td>
                <td className="table-cell r">{fmtScore(r.score_val_natural)}{r.val_score_std != null && <span className="muted"> ± {fmtScore(r.val_score_std)}</span>}{folds > 0 && <span className="muted lb-n"> n={folds}</span>}</td>
                <td className={`table-cell r ${bestTest?.model === r.model ? 'lb-top' : ''}`}>{fmtScore(r.score_test_natural)}</td>
                <td className="table-cell r">{fmtSeconds(r.fit_time)}</td>
                <td className="table-cell r">{fmtSeconds(perRow)}</td>
                <td className="lb-w">{r.weights ? Object.entries(r.weights).filter(([, w]) => w > 0).slice(0, 4).map(([m, w]) => <span key={m} className="lb-wchip" title={m}><i style={{ width: `${w * 100}%` }} />{w.toFixed(2)} {short(m)}</span>) : null}</td>
              </tr>
            )
          })}
        </tbody>
      </table></div>
      <div className="caption lb-caption">
        Validation: AutoGluon's {run.oof?.available ? 'out-of-fold predictions (μ over k folds, σ their standard deviation, n = k)' : 'tuning split, held out of training'}. Holdout: the fixed 20% of rows no model saw. Predict / row = holdout prediction time ÷ n. <b className="lb-top-key">bold</b> = best holdout score.
      </div>
    </div>
  )
}

const short = (m: string) => m.replace('NeuralNet', 'NN').replace('RandomForest', 'RF').replace('ExtraTrees', 'XT').replace('LightGBM', 'LGB').replace('_BAG_L1', '').replace('_BAG_L2', '·L2')

/** Validation vs holdout for every model: points under the diagonal did worse on unseen rows than validation said. */
export function ScoreScatter({ run, selected, onSelect }: { run: Run; selected: string | null; onSelect: (m: string) => void }) {
  const [ref, w] = useWidth<HTMLDivElement>()
  const pts = (run.leaderboard ?? []).filter((r) => r.score_val_natural != null && r.score_test_natural != null)
  if (pts.length < 2) return null
  const vals = pts.flatMap((r) => [r.score_val_natural as number, r.score_test_natural as number])
  let lo = Math.min(...vals), hi = Math.max(...vals)
  const pad = (hi - lo) * 0.08 || Math.abs(hi) * 0.01 || 0.01
  lo -= pad; hi += pad
  const H = 230, L = 52, B = 30, T = 8, R = 12
  const sx = (v: number) => L + ((v - lo) / (hi - lo)) * (w - L - R)
  const sy = (v: number) => T + (1 - (v - lo) / (hi - lo)) * (H - T - B)
  const ticks = Array.from({ length: 4 }, (_, i) => lo + ((hi - lo) * (i + 0.5)) / 4)
  const metric = run.eval_metric?.name ?? 'score'
  return (
    <PlotFrame title="Validation vs Holdout" chips={<span className="chip"><span className="sym">M</span>={pts.length}</span>}
      caption={<span>Each point is a model fₘ: <i className="math-i">x</i> = validation {metric}, <i className="math-i">y</i> = holdout {metric}. On the dashed line <i className="math-i">y = x</i> the validation estimate was exact; {run.eval_metric?.greater_is_better === false ? 'above' : 'below'} it, the model did worse on unseen rows.</span>}>
      <div ref={ref} data-testid="score-scatter">
        <svg width={w} height={H}>
          {ticks.map((t) => <g key={t}><line x1={L} x2={w - R} y1={sy(t)} y2={sy(t)} stroke="var(--plot-grid)" /><text x={L - 6} y={sy(t) + 4} textAnchor="end" className="tick-text">{fmtScore(t, 3)}</text>
            <line x1={sx(t)} x2={sx(t)} y1={T} y2={H - B} stroke="var(--plot-grid)" /><text x={sx(t)} y={H - B + 14} textAnchor="middle" className="tick-text">{fmtScore(t, 3)}</text></g>)}
          <line x1={sx(lo)} y1={sy(lo)} x2={sx(hi)} y2={sy(hi)} stroke="var(--plot-axis)" strokeDasharray="5 4" />
          <text x={w - R} y={H - 2} textAnchor="end" className="axis-title">validation</text>
          <text x={L - 46} y={T + 8} className="axis-title">holdout</text>
          {pts.map((r) => {
            const ens = isEns(r), best = r.model === run.best_model
            return (
              <g key={r.model} className="sc-pt" onClick={() => onSelect(r.model)}>
                <circle cx={sx(r.score_val_natural as number)} cy={sy(r.score_test_natural as number)} r={best ? 6 : 4.5}
                  fill={ens ? 'var(--viz-b)' : 'var(--viz-a)'} fillOpacity={selected === r.model || best ? 1 : 0.7} stroke={selected === r.model ? 'var(--text-primary)' : 'var(--bg-surface)'} strokeWidth={1.5} />
                <title>{`${r.model}: validation ${fmtScore(r.score_val_natural)}, holdout ${fmtScore(r.score_test_natural)}`}</title>
              </g>
            )
          })}
        </svg>
      </div>
    </PlotFrame>
  )
}

/** The selected model: its family's objective from the catalogue, every symbol defined, and its hyperparameters. */
export function ModelCard({ row, family }: { row: LeaderboardRow | null; family: ModelFamily | undefined }) {
  if (!row) return null
  const hp = row.hyperparameters as Record<string, unknown> | undefined
  const hpEntries = hp ? Object.entries(hp).filter(([, v]) => v !== null && typeof v !== 'object').slice(0, 10) : []
  return (
    <div className="card model-card" data-testid="model-card">
      <div className="card-head"><div className="left"><h3 className="card-title">{row.model}</h3><span className="helper" style={{ fontSize: 12 }}>{family?.name ?? row.family} · {family?.kind}</span></div><span className="chip">L{sub(row.stack_level)}</span></div>
      <div className="card-body stack" style={{ gap: 'var(--space-12)' }}>
        {family && <p className="helper">{family.description}</p>}
        {family && (
          <div className="formula">
            <div className="micro-label">Objective</div>
            <div className="eq obj" data-testid="model-objective">{family.objective}</div>
            <dl className="where">{Object.entries(family.symbols).map(([s, d]) => <span key={s} style={{ display: 'contents' }}><dt>{s}</dt><dd>{d}</dd></span>)}</dl>
          </div>
        )}
        {row.weights && <div className="helper" style={{ fontSize: 12 }}>Weights: {Object.entries(row.weights).filter(([, w]) => w > 0).map(([m, w]) => `${m} ${w.toFixed(3)}`).join(' · ')}</div>}
        {hpEntries.length > 0 && <div className="row" style={{ flexWrap: 'wrap', gap: 6 }}>{hpEntries.map(([k, v]) => <span key={k} className="chip" style={{ fontSize: 11 }}>{k}={String(v)}</span>)}</div>}
      </div>
    </div>
  )
}
