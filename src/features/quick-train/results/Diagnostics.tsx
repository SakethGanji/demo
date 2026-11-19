import { ConfusionMatrix } from '@/shared/components/ConfusionMatrix'
import { ParamChip } from '@/shared/primitives/ParamChip'
import type { Run } from '@/shared/api/types'

/** Classification: the holdout confusion matrix, with recall and precision per class read off it. */
export function ClassReport({ run }: { run: Run }) {
  const h = run.holdout
  if (!h?.confusion_matrix || !h.classes) return null
  const m = h.confusion_matrix
  const labels = h.classes.map(String)
  const K = labels.length
  const recall = m.map((row, i) => row[i] / (row.reduce((a, b) => a + b, 0) || 1))
  const precision = labels.map((_, j) => m[j][j] / (m.reduce((a, row) => a + row[j], 0) || 1))
  return (
    <div className="card" data-testid="confusion">
      <div className="card-head"><div className="left"><h3 className="card-title">Confusion Matrix</h3><span className="helper" style={{ fontSize: 12 }}>{h.model} on the holdout · rows: true y, columns: predicted ŷ</span></div><div className="row"><ParamChip sym="n" value={h.n_rows.toLocaleString()} /><ParamChip sym="K" value={String(K)} /></div></div>
      <div className="card-body stack" style={{ gap: 'var(--space-12)' }}>
        <ConfusionMatrix labels={labels} m={m} />
        <div className="cls-report">
          <span className="micro-label">class k</span><span className="micro-label r">recall Mₖₖ / Σⱼ Mₖⱼ</span><span className="micro-label r">precision Mₖₖ / Σᵢ Mᵢₖ</span><span className="micro-label r">support nₖ</span>
          {labels.map((l, i) => (
            <span key={l} style={{ display: 'contents' }}>
              <span className="cr-name">{l}</span>
              <span className="mono r"><Bar v={recall[i]} />{recall[i].toFixed(3)}</span>
              <span className="mono r"><Bar v={precision[i]} />{precision[i].toFixed(3)}</span>
              <span className="mono r muted">{m[i].reduce((a, b) => a + b, 0).toLocaleString()}</span>
            </span>
          ))}
        </div>
        <p className="helper" style={{ fontSize: 12 }}>M ∈ ℕᴷˣᴷ, Mᵢⱼ = holdout rows of true class i predicted as j. The diagonal is right; everything off it is a mistake, and where it falls says which classes the model confuses.</p>
      </div>
    </div>
  )
}

const Bar = ({ v }: { v: number }) => <i className="cr-bar"><i style={{ width: `${Math.max(0, Math.min(1, v)) * 100}%` }} /></i>

export { TopWords } from '@/shared/components/TopWords'
