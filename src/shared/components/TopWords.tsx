import { useState } from 'react'
import type { Run } from '@/shared/api/types'

/** TF-IDF models: the words with the largest coefficient per class (one-vs-rest). */
export function TopWords({ run }: { run: Run }) {
  const tm = run.text_models
  const models = tm ? Object.keys(tm) : []
  const [model, setModel] = useState(models[0] ?? '')
  if (!tm || !models.length) return null
  const per = tm[model] ?? tm[models[0]]
  const classes = Object.keys(per)
  return (
    <div className="card" data-testid="top-words">
      <div className="card-head">
        <div className="left"><h3 className="card-title">Top Words Per Class</h3><span className="helper" style={{ fontSize: 12 }}>what the TF-IDF models read as evidence for each class</span></div>
        <div className="seg" role="tablist">{models.map((m) => <button key={m} className={model === m ? 'on' : ''} onClick={() => setModel(m)}>{m}</button>)}</div>
      </div>
      <div className="card-body">
        <div className="tw-grid">
          {classes.map((c) => {
            const words = per[c].slice(0, 8)
            const max = Math.max(...words.map(([, v]) => Math.abs(v)), 1e-9)
            return (
              <div key={c} className="tw-class" data-testid={`top-words-${c}`}>
                <div className="tw-head"><b>{c}</b><span className="micro-label">top <i className="nocase">βₖⱼ</i></span></div>
                {words.map(([wd, v]) => <div key={wd} className="tw-row"><span className="mono">{wd}</span><i className="tw-bar"><i style={{ width: `${(Math.abs(v) / max) * 100}%` }} /></i><span className="mono muted">{v.toFixed(2)}</span></div>)}
              </div>
            )
          })}
        </div>
        <p className="helper" style={{ fontSize: 12, marginTop: 'var(--space-12)' }}>
          x = TF-IDF(text) ∈ ℝᵛ, one weight per word or bigram j of a vocabulary of size V. Class k's one-vs-rest model scores sₖ(x) = βₖᵀx + bₖ; βₖⱼ is the coefficient of term j for class k{model.includes('SVM') ? ' (Linear SVM margins, Platt-calibrated to probabilities)' : ' (L2 logistic regression, C = 5)'}, averaged over fold models when bagged. Larger βₖⱼ: term j pushes a row towards class k.
        </p>
      </div>
    </div>
  )
}
