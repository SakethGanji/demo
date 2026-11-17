import { valF1, type Params } from '../params'

export function FitEstimate({ params }: { params: Params }) {
  const d = Math.round(([0, 38.2, 150.42, 412.6][params.ngram] * 1000 * 3) / params.minDf)
  const secs = Math.round(40 + (d / 1000) * 0.35 * (1 + Math.log10(params.C) * 0.18 + 1) * (params.maxIter / 1000) ** 0.4 * (1 - Math.log10(params.tol / 1e-4) * 0.12))
  const mem = (1.2 + d / 60000).toFixed(1)
  const x = Math.log10(params.C)
  return (
    <div className="card navy">
      <div className="card-head"><h3 className="card-title">Fit Estimate</h3><span className="micro-label">Updates As You Tune</span></div>
      <div className="minis">
        <div className="mini"><span className="micro-label">Features <span className="math" style={{ fontSize: 13, textTransform: 'none' }}>d</span></span><span className="v">{d.toLocaleString()}</span><span className="u">sparse ℝⁿˣᵈ</span></div>
        <div className="mini"><span className="micro-label">Fit Time</span><span className="v">{String(Math.floor(secs / 60)).padStart(2, '0')}m {String(secs % 60).padStart(2, '0')}s</span><span className="u">5 folds · {mem} GiB</span></div>
        <div className="mini"><span className="micro-label">Est. F1</span><span className="v">{valF1(x, params).toFixed(3)}</span><span className="u">± 0.006 · n = 5</span></div>
      </div>
    </div>
  )
}
