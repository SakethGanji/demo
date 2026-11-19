import { useEffect, useState } from 'react'
import { api } from '@/shared/api/client'
import type { ApRequirement, Autopilot, DatasetDetail, DatasetSummary } from '@/shared/api/types'
import { Segmented } from '@/shared/primitives/Segmented'
import { StatusPill } from '@/shared/primitives/StatusPill'

const QUALITY = ['f1_macro', 'accuracy', 'balanced_accuracy', 'mcc', 'roc_auc', 'f1', 'r2', 'rmse', 'mae']
const SYM: Record<string, string> = { f1_macro: 'F̄₁', accuracy: 'acc', balanced_accuracy: 'BA', mcc: 'MCC', roc_auc: 'AUC', f1: 'F₁', r2: 'R²', rmse: 'RMSE', mae: 'MAE', p95_latency_ms: 'τ₉₅', p50_latency_ms: 'τ₅₀' }

function defaults(d: DatasetDetail | null): ApRequirement[] {
  const t = d?.profile.task
  if (t === 'regression') return [{ metric: 'r2', op: '>=', value: 0.8 }, { metric: 'p95_latency_ms', op: '<=', value: 50 }]
  if (t === 'binary') return [{ metric: 'roc_auc', op: '>=', value: 0.8 }, { metric: 'p95_latency_ms', op: '<=', value: 50 }]
  return [{ metric: 'f1_macro', op: '>=', value: 0.92 }, { metric: 'p95_latency_ms', op: '<=', value: 50 }]
}

export function LiveBrief({ onStarted, onOpen }: { onStarted: (id: string) => void; onOpen: (id: string) => void }) {
  const [datasets, setDatasets] = useState<DatasetSummary[]>([])
  const [dsId, setDsId] = useState('support-tickets')
  const [ds, setDs] = useState<DatasetDetail | null>(null)
  const [reqs, setReqs] = useState<ApRequirement[]>(defaults(null))
  const [budget, setBudget] = useState(600)
  const [autonomy, setAutonomy] = useState<'ask' | 'auto'>('ask')
  const [decider, setDecider] = useState<'auto' | 'layer1'>('auto')
  const [goal, setGoal] = useState('Route each support ticket to the right team, fast enough to run on every incoming ticket.')
  const [past, setPast] = useState<Autopilot[]>([])
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  useEffect(() => { api.datasets.list().then((l) => setDatasets(l.filter((d) => d.target))).catch(() => {}); api.autopilot.list().then(setPast).catch(() => {}) }, [])
  useEffect(() => { api.datasets.get(dsId).then((d) => { setDs(d); setReqs(defaults(d)) }).catch(() => setDs(null)) }, [dsId])

  const classes = (ds?.profile.classes ?? []).map((c) => c.name)
  const metrics = [...QUALITY, 'p95_latency_ms', 'p50_latency_ms', ...classes.map((c) => `recall[${c}]`)]
  const set = (i: number, r: Partial<ApRequirement>) => setReqs(reqs.map((x, j) => (j === i ? { ...x, ...r } : x)))

  const start = () => {
    setBusy(true); setErr('')
    api.autopilot.start({ dataset_id: dsId, requirements: reqs, budget_seconds: budget, autonomy, decider, goal })
      .then((a) => onStarted(a.id)).catch((e) => setErr((e as Error).message)).finally(() => setBusy(false))
  }

  return (
    <div className="page" data-testid="ap-brief">
      <div className="grid" style={{ gridTemplateColumns: 'minmax(0, 1.25fr) minmax(0, 1fr)', alignItems: 'start' }}>
        <div className="card">
          <div className="card-head"><h3 className="card-title">Brief</h3><span className="helper" style={{ fontSize: 12 }}>what Vela must reach, and with how much time</span></div>
          <div className="card-body stack" style={{ gap: 14 }}>
            <label className="stack" style={{ gap: 4 }}><span className="micro-label">Dataset</span>
              <select className="select" value={dsId} onChange={(e) => setDsId(e.target.value)} data-testid="ap-dataset">
                {datasets.map((d) => <option key={d.id} value={d.id}>{d.name} · {d.rows.toLocaleString()} rows · predicts {d.target}</option>)}
              </select>
            </label>
            {ds && <p className="helper" style={{ fontSize: 12 }}>Label <b className="mono">{ds.target}</b> · {ds.profile.task}{classes.length ? ` · ${classes.length} classes` : ''} · holdout {ds.profile.splits?.holdout_rows?.toLocaleString()} rows (fixed; every step is scored on it)</p>}
            <label className="stack" style={{ gap: 4 }}><span className="micro-label">Goal</span>
              <textarea className="goal" rows={2} value={goal} onChange={(e) => setGoal(e.target.value)} />
            </label>
            <div className="stack" style={{ gap: 6 }}>
              <span className="micro-label">Requirements (all must hold on the holdout)</span>
              {reqs.map((r, i) => (
                <div key={i} className="row" style={{ gap: 8 }} data-testid="ap-req">
                  <span className="math" style={{ width: 70, textAlign: 'right' }}>{SYM[r.metric] ?? (r.metric.startsWith('recall[') ? `r(${r.metric.slice(7, -1)})` : r.metric)}</span>
                  <select className="select sm" value={r.metric} onChange={(e) => set(i, { metric: e.target.value, op: e.target.value.includes('latency') || ['rmse', 'mae'].includes(e.target.value) ? '<=' : '>=' })}>
                    {metrics.map((m) => <option key={m} value={m}>{m}</option>)}
                  </select>
                  <select className="select sm" value={r.op} onChange={(e) => set(i, { op: e.target.value as ApRequirement['op'] })}><option>{'>='}</option><option>{'<='}</option></select>
                  <input className="input sm mono" style={{ width: 90 }} type="number" step="any" value={r.value} onChange={(e) => set(i, { value: Number(e.target.value) })} />
                  <span className="helper" style={{ fontSize: 11.5 }}>{r.metric.includes('latency') ? 'ms per row' : ''}</span>
                  <button className="btn sm ghost" onClick={() => setReqs(reqs.filter((_, j) => j !== i))} disabled={reqs.length < 2}>×</button>
                </div>
              ))}
              <button className="btn sm ghost" style={{ alignSelf: 'flex-start' }} onClick={() => setReqs([...reqs, { metric: classes.length ? `recall[${classes[classes.length - 1]}]` : 'accuracy', op: '>=', value: 0.8 }])}>+ Requirement</button>
            </div>
            <div className="row" style={{ gap: 16, flexWrap: 'wrap' }}>
              <label className="stack" style={{ gap: 4 }}><span className="micro-label">Total budget B</span>
                <span className="row" style={{ gap: 6 }}><input className="input sm mono" style={{ width: 90 }} type="number" min={60} step={60} value={budget} onChange={(e) => setBudget(Number(e.target.value))} data-testid="ap-budget" /><span className="helper">s of training, all steps</span></span>
              </label>
              <label className="stack" style={{ gap: 4 }}><span className="micro-label">Autonomy</span>
                <Segmented options={['Ask me', 'Auto ≥ 0.9']} value={autonomy === 'ask' ? 'Ask me' : 'Auto ≥ 0.9'} onChange={(v) => setAutonomy(v === 'Ask me' ? 'ask' : 'auto')} />
              </label>
              <label className="stack" style={{ gap: 4 }}><span className="micro-label">Who decides</span>
                <Segmented options={['Claude → fixed', 'Fixed only']} value={decider === 'auto' ? 'Claude → fixed' : 'Fixed only'} onChange={(v) => setDecider(v === 'Fixed only' ? 'layer1' : 'auto')} />
              </label>
            </div>
            {err && <p className="helper warn">{err}</p>}
            <div className="row"><button className="btn primary" disabled={busy || !ds} onClick={start} data-testid="ap-start">{busy ? 'Starting…' : 'Run it end to end'}</button></div>
          </div>
        </div>
        <div className="stack">
          <div className="card navy">
            <div className="card-head"><h3 className="card-title">Plan</h3><span className="micro-label">layer 1 · every step a real run</span></div>
            <div className="card-body plan">
              <ol>
                <li><b>Profile</b> the data: classes, shares, text columns.</li>
                <li><b>Baseline</b>: medium_quality, 5-fold bagging, so every training row gets an out-of-fold p̂(x).</li>
                <li><b>Audit</b> with cleanlab: label issues by the margin m(x), duplicates, near-duplicate conflicts, outliers → your inbox.</li>
                <li><b>Apply</b> your answers as a new dataset version (lineage kept, holdout rows unchanged), then <b>retrain</b>.</li>
                <li><b>Rebalance</b> / <b>calibrate</b> if imbalanced, <b>refit</b> one model per family if latency is over, <b>escalate</b> the preset.</li>
                <li><b>Stop</b> when every requirement holds or Σ tᵢ reaches B; the verdict is Met or Not Met.</li>
              </ol>
              <p style={{ marginTop: 10, fontSize: 12 }}>Layer 2: after the audit, Claude (CLI, then the API) picks each next step from this menu and explains it with the numbers; if it does not answer in 60 s, layer 1 decides. Every decision says which layer made it.</p>
            </div>
          </div>
          {past.length > 0 && <div className="card">
            <div className="card-head"><h3 className="card-title">Earlier Autopilots</h3></div>
            <div className="card-body stack" style={{ gap: 6 }}>
              {past.slice(0, 8).map((p) => (
                <button key={p.id} className="filter ds" onClick={() => onOpen(p.id)} style={{ width: '100%' }}>
                  <b className="mono">{p.id} · {p.brief.dataset_id}</b>
                  <span>{p.status}{p.verdict ? ` · ${p.verdict.met ? 'met' : 'not met'}` : ''} · {p.steps.length} steps · {p.created_at.replace('T', ' ').slice(0, 16)}</span>
                </button>
              ))}
            </div>
          </div>}
          <div className="helper" style={{ fontSize: 12 }}><StatusPill status="neutral">Live</StatusPill> Every number on the run page comes from the API.</div>
        </div>
      </div>
    </div>
  )
}
