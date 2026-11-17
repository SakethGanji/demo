import { FretSlider } from '@/shared/components/FretSlider'
import { Segmented } from '@/shared/primitives/Segmented'
import { StatusPill } from '@/shared/primitives/StatusPill'
import { fmtSci, pow10, type Params } from '../params'
import { ClassBalance } from './ClassBalance'
import { ConfigBlock } from './ConfigBlock'
import { FitEstimate } from './FitEstimate'
import { PipelineCard } from './PipelineCard'
import { ValidationCurve } from './ValidationCurve'

export function Hyperparameters({ params, set, guided, setGuided }: {
  params: Params; set: <K extends keyof Params>(k: K) => (v: Params[K]) => void; guided: boolean; setGuided: (g: boolean) => void
}) {
  return (
    <div className="page">
      <div className="grid two">
        <div className="stack">
          <div className="card">
            <div className="card-head">
              <div className="left">
                <h2 className="card-title">Model Hyperparameters</h2>
                <span className="helper" style={{ fontSize: 12 }}>Logistic Regression · One-vs-Rest</span>
              </div>
              <Segmented options={['Guided', 'Advanced']} value={guided ? 'Guided' : 'Advanced'} onChange={(v) => setGuided(v === 'Guided')} />
            </div>
            <div className="card-body" style={{ padding: 'var(--space-20)' }}>
              <div className="formula">
                <div className="micro-label">Objective</div>
                <div className="eq">
                  <span className="up">min</span><sub><i>w</i>, <i>b</i></sub>&nbsp; ½‖<i>w</i>‖² + <i>C</i> <span className="up">Σ</span><sub><i>i</i>=1</sub><sup><i>n</i></sup> <span className="up">log</span>(1 + <span className="up">exp</span>(−<i>y</i><sub><i>i</i></sub>(<i>w</i><sup>⊤</sup><i>x</i><sub><i>i</i></sub> + <i>b</i>)))
                </div>
                <dl className="where">
                  <dt>C</dt><dd>{guided ? 'How closely the model fits the training tickets. Higher fits harder.' : 'Inverse regularization strength'}</dd>
                  <dt>w, b</dt><dd>{guided ? 'Learned word weights and offset, one set per class.' : 'Weights ∈ ℝᵈ and bias, per class (OvR)'}</dd>
                  <dt>x<sub>i</sub>, y<sub>i</sub></dt><dd>{guided ? 'A ticket’s TF-IDF vector and its label.' : 'Row i of X ∈ sparse ℝⁿˣᵈ; y ∈ {−1, +1}'}</dd>
                  <dt>n</dt><dd><span className="mono">99,600</span> {guided ? 'training tickets per fold' : 'rows per training fold'}</dd>
                </dl>
              </div>
              <div style={{ marginTop: 'var(--space-12)' }}>
                <FretSlider guided={guided} sym="C" name="Regularization" help="Lower values generalize more; 1.0 is a solid start."
                  min={1e-3} max={1e2} log value={params.C} onChange={set('C')} ticks={[1e-3, 1e-2, 1e-1, 1, 10, 100]} tickLabel={pow10} format={fmtSci}
                  domain={<>C ∈ [10⁻³, 10²]</>} />
                <FretSlider guided={guided} sym="T" name="Max Iterations" help="Upper bound on solver steps before stopping."
                  min={100} max={2000} step={50} value={params.maxIter} onChange={set('maxIter')} ticks={[100, 500, 1000, 1500, 2000]}
                  domain={<>T ∈ [100, 2000]</>} />
                <FretSlider guided={guided} sym="ε" name="Tolerance" help="Stop when the loss improves by less than this."
                  min={1e-6} max={1e-2} log value={params.tol} onChange={set('tol')} ticks={[1e-6, 1e-5, 1e-4, 1e-3, 1e-2]} tickLabel={pow10} format={fmtSci}
                  domain={<>ε ∈ [10⁻⁶, 10⁻²]</>} />
                <FretSlider guided={guided} sym="m" name="Max N-gram" help="Bigrams capture phrases like 'password reset' and 'chargeback'."
                  min={1} max={3} step={1} value={params.ngram} onChange={set('ngram')} ticks={[1, 2, 3]}
                  domain={<>ngram = (1, m), m ∈ {'{1, 2, 3}'}</>} />
                <FretSlider guided={guided} sym="f" name="Min Doc Freq" help="Ignore terms seen in fewer tickets than this."
                  min={1} max={20} step={1} value={params.minDf} onChange={set('minDf')} ticks={[1, 5, 10, 15, 20]}
                  domain={<>f ∈ [1, 20]</>} />
                <div className="param">
                  <div className="label">
                    <b>Class Weight</b>
                    <small>{guided ? 'Weight rare classes up so they are not drowned out.' : <span className="mono">w_k ∝ n / (K · n_k)</span>}</small>
                  </div>
                  <div className="row">
                    <Segmented options={['None', 'balanced']} value={params.classWeight} onChange={set('classWeight')} />
                    {params.classWeight === 'None' && <StatusPill status="warning">Class Imbalance Detected</StatusPill>}
                  </div>
                  <span />
                </div>
              </div>
            </div>
          </div>

          <PipelineCard params={params} />
        </div>

        <div className="stack">
          <FitEstimate params={params} />
          <ValidationCurve params={params} />
          <ConfigBlock params={params} />
          <ClassBalance />
        </div>
      </div>
    </div>
  )
}
