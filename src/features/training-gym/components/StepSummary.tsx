import { STEPS } from '../params'

export function StepSummary({ index }: { index: number }) {
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
