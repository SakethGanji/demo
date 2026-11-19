import { type Dataset } from '@/features/quick-train/datasets'
import { NumberInput } from '@/shared/primitives/NumberInput'
import { ResonanceWave } from '@/shared/primitives/ResonanceWave'
import { Segmented } from '@/shared/primitives/Segmented'
import { VelaCrest } from '@/shared/primitives/VelaCrest'
import { TEXT_SAMPLES, type Brief } from '../run'

/** The brief: goal, dataset, requirements, how much to ask, and Vela's plan once drafted. */
export function BriefStep({ ds, setDs, goal, setGoal, brief, setBrief, phase, onEdit }: {
  ds: Dataset; setDs: (d: Dataset) => void; goal: string; setGoal: (g: string) => void
  brief: Brief; setBrief: (b: Brief) => void; phase: 'brief' | 'planning' | 'plan' | 'run'; onEdit: () => void
}) {
  const weak = [...ds.classes].sort((a, c) => a.share - c.share)[0].name
  const num = (k: 'f1' | 'recall' | 'latency' | 'budget', step: number, lo: number, hi: number) => (
    <NumberInput label={k} value={brief[k]} min={lo} max={hi} step={step} format={(v) => (k === 'budget' ? String(v) : k === 'latency' ? v.toFixed(1) : v.toFixed(2))} onChange={(v) => setBrief({ ...brief, [k]: v })} />
  )
  return (
    <div className="page">
      <div className="grid" style={{ gridTemplateColumns: 'minmax(0, 1.25fr) minmax(0, 1fr)', alignItems: 'start' }}>
        <div className="stack">
          <div className="card">
            <div className="card-head"><h2 className="card-title">The Brief</h2><span className="micro-label">What you want</span></div>
            <div className="card-body stack" style={{ gap: 12, padding: 20 }}>
              <label className="micro-label" htmlFor="goal">Goal, in your words</label>
              <textarea id="goal" className="goal" rows={3} value={goal} onChange={(e) => setGoal(e.target.value)} />
              <span className="micro-label">Dataset</span>
              <div className="ds-pick">
                {TEXT_SAMPLES.map((s) => (
                  <button key={s.name} className={`filter ds ${ds.name === s.name ? 'on' : ''}`} onClick={() => { setDs(s); onEdit() }}>
                    <b className="mono">{s.name}</b><span>{s.rows.toLocaleString()} rows · K = {s.classes.length}</span>
                  </button>
                ))}
              </div>
            </div>
          </div>
          <div className="card">
            <div className="card-head"><h2 className="card-title">Requirements</h2><span className="micro-label">Vela stops when all are met</span></div>
            <div className="card-body" style={{ padding: '4px 20px' }}>
              <div className="set-row"><div><b>Macro F1 <span className="math">F1</span></b><p className="helper">Average quality across all {ds.classes.length} classes.</p></div><div className="row"><span className="math">≥</span>{num('f1', 0.01, 0.5, 0.99)}</div></div>
              <div className="set-row"><div><b>Weakest-Class Recall <span className="math">r</span></b><p className="helper">Share of real <span className="mono">{weak}</span> rows the model catches.</p></div><div className="row"><span className="math">≥</span>{num('recall', 0.01, 0.5, 0.99)}</div></div>
              <div className="set-row"><div><b>Latency <span className="math">τ</span></b><p className="helper">Time to predict one row, in ms. Rules out slow models.</p></div><div className="row"><span className="math">≤</span>{num('latency', 0.5, 1, 50)}</div></div>
              <div className="set-row"><div><b>Budget <span className="math">B</span></b><p className="helper">Most training iterations before it stops and reports.</p></div><div className="row"><span className="math">≤</span>{num('budget', 1, 3, 20)}</div></div>
            </div>
          </div>
        </div>
        <div className="stack">
          <div className="card">
            <div className="card-head"><h2 className="card-title">How Much To Ask</h2></div>
            <div className="card-body" style={{ padding: '4px 20px' }}>
              <div className="set-row" style={{ flexDirection: 'column', alignItems: 'stretch', gap: 10 }}>
                <Segmented options={['Ask First', 'Ground Truth Only', 'Fully Autonomous']} value={brief.autonomy} onChange={(v) => setBrief({ ...brief, autonomy: v })} />
                <p className="helper" style={{ maxWidth: 'none' }}>{{
                  'Ask First': 'Vela asks before changing labels and before generating synthetic data.',
                  'Ground Truth Only': 'Vela decides everything about models and cleaning. It stops only when the labels themselves are in question.',
                  'Fully Autonomous': 'Vela takes its own best answer to every question and logs it for you to review afterwards.',
                }[brief.autonomy]}</p>
              </div>
              <div className="set-row"><div><b>Synthetic Data</b><p className="helper">Let Vela write examples for starved classes. Training folds only, always tagged.</p></div><Segmented options={['Allowed', 'Off']} value={brief.synthetic ? 'Allowed' : 'Off'} onChange={(v) => setBrief({ ...brief, synthetic: v === 'Allowed' })} /></div>
            </div>
          </div>
          <div className="card navy">
            <div className="card-head"><div className="left"><VelaCrest size={18} /><h2 className="card-title">Vela's Plan</h2></div>{phase === 'plan' && <span className="micro-label">Read before it starts</span>}</div>
            <div className="card-body plan">
              {phase === 'brief' && <p>Vela reads the data and writes a plan before it touches anything.</p>}
              {phase === 'planning' && <div className="thinking" style={{ color: 'var(--text-on-shell-muted)' }}><ResonanceWave width={84} height={18} fast color="#38BDF8" /> Reading {ds.name}</div>}
              {phase === 'plan' && (
                <ol>
                  <li><b>Profile and clean.</b> Dedupe, impute, hold out 15% that nothing else touches.</li>
                  <li><b>Baseline.</b> TF-IDF → LogReg (OvR). Text with K = {ds.classes.length} favours a sparse linear model.</li>
                  <li><b>Fix the weak class.</b> {weak} is {(Math.min(...ds.classes.map((c) => c.share)) * 100).toFixed(1)}% of rows: reweight{brief.synthetic ? ', then synthesize if still short' : ''}.</li>
                  <li><b>Challenge it.</b> Try XGBoost; keep it only if it wins inside {brief.latency} ms.</li>
                  <li><b>Audit the labels.</b> Rows where confident folds disagree with the label come to you.</li>
                  <li><b>Score once on holdout</b> and stop at F1 ≥ {brief.f1.toFixed(2)}, recall ≥ {brief.recall.toFixed(2)}, or after {brief.budget} iterations.</li>
                </ol>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
