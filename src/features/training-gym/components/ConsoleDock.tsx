import { useState } from 'react'
import { fmtSci, type Params } from '../params'

export function ConsoleDock({ params }: { params: Params }) {
  const [open, setOpen] = useState(true)
  const [tab, setTab] = useState<'Validation' | 'Dry Run'>('Validation')
  const imbalanced = params.classWeight === 'None'
  const overfit = params.C > 20
  const checks: [boolean, string, string, string][] = [
    [true, 'Schema', '9 columns typed · target category has K = 6 classes', 'Step 2'],
    [true, 'Port Contracts', 'str[ ] → sparse ℝⁿˣᵈ → ŷ ∈ ℝⁿˣ⁶ · all strings connect', 'Step 4'],
    [false, 'Null Values', 'priority has 3.1% nulls · imputed with most_frequent', 'Step 4'],
    [!imbalanced, 'Class Balance', imbalanced ? "Security is 7.2% of rows · set class_weight='balanced'" : "class_weight='balanced' · rare classes weighted up", 'Step 7'],
    [!overfit, 'Regularization', overfit ? `C=${fmtSci(params.C)} is past the validation peak · train–validation gap widening` : `C=${fmtSci(params.C)} within C ∈ [10⁻³, 10²]`, 'Step 7'],
  ]
  const warns = checks.filter((c) => !c[0]).length
  return (
    <div className={`dock ${open ? '' : 'closed'}`}>
      <div className="dock-head">
        {(['Validation', 'Dry Run'] as const).map((t) => (
          <button key={t} className={`tab ${open && tab === t ? 'on' : ''}`} onClick={() => { setTab(t); setOpen(true) }}>
            {t}{t === 'Validation' && <span className={`count ${warns ? 'warn' : ''}`}>{warns ? `▲ ${warns}` : '● 0'}</span>}
          </button>
        ))}
        <span className="spacer" />
        <button className="btn sm ghost" onClick={() => setOpen(!open)} aria-expanded={open}>{open ? 'Hide ↓' : 'Show ↑'}</button>
      </div>
      {open && (
        <div className="dock-body">
          {tab === 'Validation' ? checks.map(([ok, name, msg, at]) => (
            <div key={name} className="check">
              <span className={ok ? 'g-ok' : 'g-warn'}>{ok ? '●' : '▲'}</span><b>{name}</b><span className="table-cell" style={{ color: 'var(--text-body)' }}>{msg}</span><span className="micro-label">{at}</span>
            </div>
          )) : (
            <div className="log-panel">
              <div className="lt">$ ensemble gym dry-run --project support-ticket-classifier --rows 2000</div>
              <div className="li">[PLAN] TfidfVectorizer(ngram_range=(1, {params.ngram}), min_df={params.minDf})</div>
              <div className="li">[PLAN] LogisticRegression(C={fmtSci(params.C)}, max_iter={params.maxIter}, tol={fmtSci(params.tol)}, class_weight={params.classWeight === 'None' ? 'None' : "'balanced'"})</div>
              <div className="lm">[OK  ] pipeline compiled · 2 transforms · 1 estimator</div>
              <div className="lm">[OK  ] dry-run fit on 2,000 rows in 00m 03s</div>
              {imbalanced && <div className="lw">[WARN] class=Security support=144 rows · recall unstable</div>}
              <div className="lt">[INFO] ready to submit to queue gym-default</div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
