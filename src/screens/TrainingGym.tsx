import { useState } from 'react'
import { FretSlider, ParamChip, Segmented, StatusPill, sup } from '../components/ui'
import { CLASSES, CLASS_COUNTS } from '../data'

export type Params = { C: number; maxIter: number; tol: number; ngram: number; minDf: number; classWeight: 'None' | 'balanced' }
export const DEFAULTS: Params = { C: 1.0, maxIter: 1000, tol: 1e-4, ngram: 2, minDf: 3, classWeight: 'None' }

const STEPS = ['Data Source', 'Schema', 'Split', 'Preprocessing', 'Features', 'Model', 'Hyperparameters', 'Validation', 'Review', 'Train']
const HYPER = 6

const fmtSci = (v: number) => {
  if (v >= 0.01) { const r = Number(v.toPrecision(2)); return Number.isInteger(r) && r < 100 ? r.toFixed(1) : String(r) }
  const e = Math.floor(Math.log10(v)); const m = v / 10 ** e
  return `${Number(m.toPrecision(2))}e${e}`
}
const pow10 = (v: number) => `10${sup(Math.round(Math.log10(v)))}`

export function TrainingGym({ params, setParams, guided, setGuided, onStart }: {
  params: Params; setParams: (p: Params) => void; guided: boolean; setGuided: (g: boolean) => void; onStart: () => void
}) {
  const [step, setStep] = useState(HYPER)
  const set = <K extends keyof Params>(k: K) => (v: Params[K]) => setParams({ ...params, [k]: v })
  const last = step === STEPS.length - 1

  return (
    <div className="gym">
      <div className="stepper-bar">
        <div className="stepper-title">
          <h1 className="page-title">Enterprise Support Ticket Classifier</h1>
          <StatusPill status="neutral">Draft</StatusPill>
          <span className="chip">v3.0.0-rc1</span>
          <span className="spacer" />
          <span className="helper" style={{ fontSize: 12 }}>124,500 rows × 9 cols · Text + Metadata</span>
        </div>
        <div className="stepper" role="list">
          {STEPS.map((s, i) => (
            <button key={s} role="listitem" className={`step ${i < step ? 'done' : i === step ? 'current' : ''}`} onClick={() => setStep(i)} aria-current={i === step ? 'step' : undefined}>
              <span className="node" />
              <span className="idx">{String(i + 1).padStart(2, '0')}</span>
              <span>{s}</span>
            </button>
          ))}
        </div>
      </div>

      <div className="gym-scroll">
        {step === HYPER ? (
          <Hyperparameters params={params} set={set} guided={guided} setGuided={setGuided} />
        ) : (
          <StepSummary index={step} />
        )}
      </div>

      <div className="gym-footer">
        <div><button className="btn ghost" onClick={() => setStep(Math.max(0, step - 1))} disabled={step === 0}>← Back</button></div>
        <div className="valid"><span className="dot">●</span> Schema Valid · Ready</div>
        <div className="right">
          <button className="btn">Save Draft</button>
          {last ? (
            <button className="btn primary" onClick={onStart}>Start Training</button>
          ) : (
            <button className="btn primary" onClick={() => setStep(step + 1)}>Continue →</button>
          )}
        </div>
      </div>
    </div>
  )
}

function Hyperparameters({ params, set, guided, setGuided }: {
  params: Params; set: <K extends keyof Params>(k: K) => (v: Params[K]) => void; guided: boolean; setGuided: (g: boolean) => void
}) {
  return (
    <div className="page" style={{ maxWidth: 1320 }}>
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

          <Pipeline />
        </div>

        <div className="stack">
          <ConfigBlock params={params} />
          <ClassBalance />
        </div>
      </div>
    </div>
  )
}

function ConfigBlock({ params }: { params: Params }) {
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

function ClassBalance() {
  const total = CLASS_COUNTS.reduce((a, b) => a + b, 0)
  const max = Math.max(...CLASS_COUNTS)
  return (
    <div className="card">
      <div className="card-head">
        <div className="left"><h3 className="card-title">Label Distribution</h3></div>
        <ParamChip sym="K" value="6" />
      </div>
      <table className="table">
        <thead><tr><th>Class</th><th style={{ width: '45%' }}>Share</th><th className="r">Rows</th><th className="r">%</th></tr></thead>
        <tbody>
          {CLASSES.map((c, i) => {
            const w = (CLASS_COUNTS[i] / max) * 100
            const low = CLASS_COUNTS[i] / total < 0.1
            return (
              <tr key={c}>
                <td>{c}</td>
                <td>
                  <svg width="100%" height="10" aria-hidden>
                    <line x1="0" x2={`${w}%`} y1="5" y2="5" stroke={low ? 'var(--status-warning)' : 'var(--accent-primary)'} strokeWidth="2" strokeDasharray={low ? '4 3' : undefined} />
                    <circle cx={`${w}%`} cy="5" r="3" fill={low ? 'var(--status-warning)' : 'var(--accent-primary)'} />
                  </svg>
                </td>
                <td className="r table-cell">{CLASS_COUNTS[i].toLocaleString()}</td>
                <td className="r table-cell">{((CLASS_COUNTS[i] / total) * 100).toFixed(1)}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
      <div className="card-body" style={{ borderTop: '1px solid var(--border-default)' }}>
        <p className="helper"><span style={{ color: 'var(--status-warning)' }}>▲</span> Class imbalance detected. Consider stratified CV &amp; <span className="mono">class_weight='balanced'</span> in Step 7.</p>
      </div>
    </div>
  )
}

/** Pipeline on graph paper: catenary strings between ports. Solid = valid, dashed = warning. */
function Pipeline() {
  const nodes = [
    { id: 'text', x: 24, y: 28, title: 'ticket_body', sub: 'Text column', sig: 'str[ ]' },
    { id: 'meta', x: 24, y: 140, title: 'priority, channel', sub: 'Metadata', sig: 'cat[ ]', warn: true },
    { id: 'tfidf', x: 290, y: 28, title: 'TF-IDF', sub: 'ngram=(1,2) · min_df=3', sig: 'str[ ] → sparse ℝⁿˣᵈ' },
    { id: 'onehot', x: 290, y: 140, title: 'OneHotEncoder', sub: 'handle_unknown=ignore', sig: 'cat[ ] → ℝⁿˣ¹²' },
    { id: 'model', x: 556, y: 84, title: 'LogReg (OvR)', sub: 'Step 7 · editing', sig: 'ℝⁿˣᵈ → ŷ ∈ ℝⁿˣ⁶', active: true },
  ]
  const W = 190, H = 72
  const port = (id: string, side: 'in' | 'out') => {
    const n = nodes.find((n) => n.id === id)!
    return { x: side === 'out' ? n.x + W : n.x, y: n.y + H / 2 }
  }
  const edge = (a: string, b: string) => {
    const p = port(a, 'out'), q = port(b, 'in')
    const mx = (p.x + q.x) / 2, sag = 18
    return `M${p.x},${p.y} C${mx},${p.y + sag} ${mx},${q.y + sag} ${q.x},${q.y}`
  }
  return (
    <div className="card">
      <div className="card-head">
        <div className="left"><h3 className="card-title">Pipeline</h3><span className="helper" style={{ fontSize: 12 }}>TF-IDF → LogReg (OvR)</span></div>
        <div className="row">
          <span className="legend"><svg width="20" height="6"><line x1="0" x2="20" y1="3" y2="3" stroke="var(--accent-primary)" strokeWidth="2" /></svg>Compatible</span>
          <span className="legend"><svg width="20" height="6"><line x1="0" x2="20" y1="3" y2="3" stroke="var(--status-warning)" strokeWidth="2" strokeDasharray="4 3" /></svg>Warning</span>
        </div>
      </div>
      <div className="graph-paper">
        <svg className="edges" aria-hidden>
          <defs>
            <linearGradient id="edge-active" x1="0" x2="1"><stop offset="0" stopColor="#0D9488" /><stop offset="1" stopColor="#38BDF8" /></linearGradient>
          </defs>
          <path d={edge('text', 'tfidf')} stroke="var(--accent-primary)" strokeWidth="2" fill="none" />
          <path d={edge('meta', 'onehot')} stroke="var(--status-warning)" strokeWidth="2" strokeDasharray="5 4" fill="none" />
          <path d={edge('tfidf', 'model')} stroke="url(#edge-active)" strokeWidth="2" fill="none" />
          <path d={edge('onehot', 'model')} stroke="var(--accent-primary)" strokeWidth="2" fill="none" />
        </svg>
        {nodes.map((n) => (
          <div key={n.id} className={`pnode ${n.active ? 'active' : ''}`} style={{ left: n.x, top: n.y, height: H }}>
            {n.id !== 'text' && n.id !== 'meta' && <span className="port in" />}
            {n.id !== 'model' && <span className={`port out ${n.warn ? 'warn' : ''}`} />}
            <div className="row" style={{ gap: 6 }}>
              <b className="card-title" style={{ fontSize: 13 }}>{n.title}</b>
              {n.warn && <span style={{ color: 'var(--status-warning)', fontSize: 11 }} title="3.1% nulls in priority">▲</span>}
            </div>
            <div className="table-cell" style={{ color: 'var(--text-body)', fontSize: 11.5 }}>{n.sub}</div>
            <div className="sig">{n.sig}</div>
          </div>
        ))}
      </div>
    </div>
  )
}

function StepSummary({ index }: { index: number }) {
  const rows: Record<number, [string, string][]> = {
    0: [['source', 'zendesk_tickets_2026_q3.parquet'], ['rows', '124,500'], ['cols', '9']],
    1: [['target', 'category (6 classes)'], ['text', 'ticket_body'], ['metadata', 'priority, channel']],
    2: [['strategy', 'StratifiedKFold'], ['k', '5'], ['seed', '42'], ['holdout', '0.15']],
    3: [['text', 'lowercase · strip_html · unidecode'], ['metadata', 'impute(most_frequent)']],
    4: [['vectorizer', 'TfidfVectorizer'], ['ngram_range', '(1, 2)'], ['min_df', '3']],
    5: [['family', 'Linear'], ['model', 'LogisticRegression'], ['multi_class', "'ovr'"]],
    7: [['metric', 'macro_f1'], ['secondary', 'auc_ovr, recall@Security'], ['threshold', 'F1 ≥ 0.90']],
    8: [['estimated time', '01m 50s'], ['compute', 'cpu-8x · 32 GiB'], ['artifacts', 's3://ensemble/runs/']],
    9: [['queue', 'gym-default'], ['priority', 'normal'], ['notify', 'maya.lin']],
  }
  return (
    <div className="page" style={{ maxWidth: 880 }}>
      <div className="card pad stack">
        <h2 className="section-header">Step {index + 1} · {STEPS[index]}</h2>
        <div className="config">
          {(rows[index] ?? []).map(([k, v]) => <span key={k}><span className="k">{k}:</span> {v}</span>)}
        </div>
        <p className="helper">This step is complete. Continue to review, or jump to <b>Hyperparameters</b> in the stepper to tune the model.</p>
      </div>
    </div>
  )
}
