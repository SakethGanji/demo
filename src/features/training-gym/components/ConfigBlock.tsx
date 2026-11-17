import { ParamChip } from '@/shared/primitives/ParamChip'
import { DEFAULTS, fmtSci, type Params } from '../params'

export function ConfigBlock({ params }: { params: Params }) {
  const line = (k: string, v: string, changed: boolean) => (
    <span className={changed ? 'diff' : undefined}><span className="k">{k}:</span> {v}</span>
  )
  return (
    <div className="card">
      <div className="card-head">
        <div className="left"><h3 className="card-title">Run Config</h3><span className="chip">config.yaml</span></div>
        <div className="row"><ParamChip sym="k" value="5" /><ParamChip sym="seed" value="42" /></div>
      </div>
      <div className="card-body">
        <div className="config">
          {line('model', 'LogisticRegression', false)}
          {line('multi_class', "'ovr'", false)}
          {line('  C', fmtSci(params.C), params.C !== DEFAULTS.C)}
          {line('  max_iter', String(params.maxIter), params.maxIter !== DEFAULTS.maxIter)}
          {line('  tol', fmtSci(params.tol), params.tol !== DEFAULTS.tol)}
          {line('  class_weight', params.classWeight === 'None' ? 'None' : "'balanced'", params.classWeight !== DEFAULTS.classWeight)}
          {line('vectorizer', 'TfidfVectorizer', false)}
          {line('  ngram_range', `(1, ${params.ngram})`, params.ngram !== DEFAULTS.ngram)}
          {line('  min_df', String(params.minDf), params.minDf !== DEFAULTS.minDf)}
          {line('cv', "StratifiedKFold(k=5, seed=42)", false)}
        </div>
        <p className="helper" style={{ marginTop: 'var(--space-8)', fontSize: 12 }}>Highlighted lines differ from the template defaults.</p>
      </div>
    </div>
  )
}
