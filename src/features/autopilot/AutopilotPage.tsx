import { useEffect, useMemo, useState } from 'react'
import { SAMPLES, type Dataset } from '@/features/quick-train/datasets'
import { ParamChip } from '@/shared/primitives/ParamChip'
import { ResonanceWave } from '@/shared/primitives/ResonanceWave'
import { Segmented } from '@/shared/primitives/Segmented'
import { StatusPill } from '@/shared/primitives/StatusPill'
import { VelaCrest } from '@/shared/primitives/VelaCrest'
import { IterationString } from './components/IterationString'
import { Gauge } from './components/RequirementGauge'
import { buildRun, type Brief, type Diff, type Metrics } from './run'

export function AutopilotPage({ go }: { go: (r: string) => void }) {
  const [ds, setDs] = useState<Dataset>(SAMPLES[0])
  const [goal, setGoal] = useState('Route each support ticket to the right team. Security tickets must not be missed. It has to answer fast enough to run on every incoming ticket.')
  const [brief, setBrief] = useState<Brief>({ f1: 0.9, recall: 0.9, latency: 5, budget: 8, synthetic: true, autonomy: 'Ground Truth Only' })
  const [phase, setPhase] = useState<'brief' | 'planning' | 'plan' | 'run'>('brief')
  const [answers, setAnswers] = useState<Record<string, string>>({})
  const [shown, setShown] = useState(0)
  const [paused, setPaused] = useState(false)
  const [speed, setSpeed] = useState(1)
  const { steps, total } = useMemo(() => buildRun(ds, brief, answers), [ds, brief, answers])
  const weak = [...ds.classes].sort((a, c) => a.share - c.share)[0].name

  const next = steps[shown]
  const last = steps[shown - 1]
  const blocked = last?.kind === 'ask' && !last.answer
  const finished = phase === 'run' && shown >= steps.length && steps[steps.length - 1]?.id === 'done'
  const working = phase === 'run' && !blocked && !finished && !paused && !!next

  useEffect(() => {
    if (!working) return
    const tm = setTimeout(() => setShown((n) => n + 1), next.ms / speed)
    return () => clearTimeout(tm)
  }, [working, shown, speed, next?.id])
  useEffect(() => { if (phase !== 'planning') return; const tm = setTimeout(() => setPhase('plan'), 1300); return () => clearTimeout(tm) }, [phase])

  const done = steps.slice(0, shown)
  const kept = done.filter((s) => s.after)
  const m: Metrics = kept.length ? kept[kept.length - 1].after! : { f1: 0, rec: 0, lat: 0 }
  const diff = done.reduce<Diff>((a, s) => ({ removed: a.removed + (s.diff?.removed ?? 0), relabelled: a.relabelled + (s.diff?.relabelled ?? 0), synthetic: a.synthetic + (s.diff?.synthetic ?? 0) }), { removed: 0, relabelled: 0, synthetic: 0 })
  const version = 1 + done.filter((s) => s.diff).length
  const iterations = done.filter((s) => s.kind !== 'ask' && s.id !== 'profile' && s.id !== 'done').length
  const asked = done.filter((s) => s.kind === 'ask' && !s.auto).length
  const met = m.f1 >= brief.f1 && m.rec >= brief.recall && m.lat > 0 && m.lat <= brief.latency
  const start = () => { setAnswers({}); setShown(0); setPaused(false); setPhase('run') }
  const num = (k: 'f1' | 'recall' | 'latency' | 'budget', step: number, lo: number, hi: number) => (
    <input className="num-input" type="number" step={step} min={lo} max={hi} value={brief[k]} onChange={(e) => setBrief({ ...brief, [k]: Math.min(hi, Math.max(lo, Number(e.target.value))) })} aria-label={k} />
  )

  return (
    <div className="gym">
      <div className="stepper-bar" style={{ paddingBottom: 16 }}>
        <div className="stepper-title" style={{ marginBottom: 0 }}>
          <VelaCrest size={26} />
          <h1 className="page-title">Vela Autopilot</h1>
          <span className="helper">It trains, tunes and fixes the data, and only stops to ask you what is true.</span>
          <span className="spacer" />
          <Segmented options={['Manual', 'Autopilot']} value="Autopilot" onChange={(v) => v === 'Manual' && go('gym')} />
        </div>
      </div>

      <div className="gym-scroll">
        {phase !== 'run' ? (
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
                      {SAMPLES.map((s) => (
                        <button key={s.name} className={`filter ds ${ds.name === s.name ? 'on' : ''}`} onClick={() => { setDs(s); setPhase('brief') }}>
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
        ) : (
          <div className="page">
            <section className="stage">
              <div className="stage-head">
                <div>
                  <span className="hero-eyebrow" style={{ paddingLeft: 0, color: blocked ? '#FBBF24' : undefined }}>{finished ? (met ? 'DONE · REQUIREMENTS MET' : 'STOPPED · NEEDS A DECISION') : blocked ? 'WAITING ON YOU' : paused ? 'PAUSED' : 'VELA IS WORKING'}</span>
                  <h2 className="hero-title">{finished ? last!.title : blocked ? last!.title : next ? `${next.kind === 'ask' ? 'Checking' : 'Working on'}: ${next.short}` : ''}</h2>
                </div>
                <div className="stage-read">
                  <span><small>iterations</small><b>{iterations} / {brief.budget}</b></span>
                  <span><small>asked you</small><b>{asked}</b></span>
                  <span><small>dataset</small><b>v{version}</b></span>
                  <span className="stage-sim" title="This run is scripted in your browser. No model is fitted and no data is changed.">simulated run</span>
                </div>
              </div>
              <IterationString steps={steps} shown={shown} working={working} total={total} />
              <div className="gauges">
                <Gauge label="Macro F1" sym="F1" value={m.f1} target={brief.f1} min={0.8} max={1} fmt={(v) => v.toFixed(3)} />
                <Gauge label={`${weak} Recall`} sym="r" value={m.rec} target={brief.recall} min={0.7} max={1} fmt={(v) => v.toFixed(2)} />
                <Gauge label="Latency" sym="τ" value={m.lat} target={brief.latency} min={0} max={Math.max(12, brief.latency * 1.5)} lower fmt={(v) => `${v.toFixed(1)} ms`} />
              </div>
              <div className="stage-foot">
                <div className="stage-prog"><i style={{ width: `${(shown / Math.max(steps.length, total)) * 100}%` }} /></div>
                {finished ? <button className="btn sm" onClick={start}>Run Again</button> : <button className="btn sm" disabled={blocked} onClick={() => setPaused(!paused)}>{paused ? 'Resume' : 'Pause'}</button>}
                <div className="seg stage-seg">{[1, 3].map((s) => <button key={s} className={speed === s ? 'on' : ''} onClick={() => setSpeed(s)}>{s}×</button>)}</div>
              </div>
            </section>

            <div className="grid" style={{ gridTemplateColumns: 'minmax(0, 1.35fr) minmax(0, 1fr)', alignItems: 'start' }}>
              <div className="card">
                <div className="card-head"><div className="left"><h3 className="card-title">Decision Log</h3><span className="helper" style={{ fontSize: 12 }}>everything Vela did, newest first</span></div><ParamChip sym="n" value={String(done.length)} /></div>
                <div className="log-cards">
                  {working && next && <div className="thinking" style={{ padding: '4px 4px 0' }}><ResonanceWave width={84} height={18} fast /> {next.kind === 'branch' ? 'Trying' : next.kind === 'ask' ? 'Checking' : 'Working on'} {next.short.toLowerCase()}</div>}
                  {[...done].reverse().map((s) => {
                    const prev = kept[kept.indexOf(s) - 1]?.after
                    return (
                      <div key={s.id} className={`patch ${s.kind === 'branch' ? 'applied' : ''}`}>
                        <div className="ph">
                          <VelaCrest size={16} /><b className="card-title" style={{ fontSize: 13 }}>{s.title}</b><span className="spacer" />
                          {s.kind === 'branch' ? <StatusPill status="neutral">Dropped</StatusPill> : s.kind === 'ask' ? (s.answer ? <StatusPill status={s.auto ? 'neutral' : 'success'}>{s.auto ? 'Vela Decided' : 'You Answered'}</StatusPill> : <StatusPill status="warning">Needs You</StatusPill>) : s.id === 'done' ? <StatusPill status={met ? 'success' : 'warning'}>{met ? 'Done' : 'Not Met'}</StatusPill> : <StatusPill status="success">Kept</StatusPill>}
                        </div>
                        <p>{s.finding}</p>
                        {s.action && <div className="diffline">{s.action}</div>}
                        {s.kind === 'ask' && s.answer && <div className="diffline">{s.auto ? 'Vela' : 'You'}: {s.options!.find((o) => o.id === s.answer)!.label}</div>}
                        {s.after && prev && prev.f1 > 0 && (s.after.f1 !== prev.f1 || s.after.rec !== prev.rec) && (
                          <div className="row"><span className="chip">F1 Δ {(s.after.f1 - prev.f1 >= 0 ? '+' : '') + (s.after.f1 - prev.f1).toFixed(3)}</span><span className="chip">{weak} recall Δ {(s.after.rec - prev.rec >= 0 ? '+' : '') + (s.after.rec - prev.rec).toFixed(2)}</span></div>
                        )}
                      </div>
                    )
                  })}
                </div>
              </div>

              <div className="stack">
                <div className={`card inbox ${blocked ? 'open' : ''}`}>
                  <div className="card-head"><div className="left"><h3 className="card-title">Ground-Truth Inbox</h3></div>{blocked ? <StatusPill status="warning">1 Needs You</StatusPill> : <StatusPill status="neutral">Empty</StatusPill>}</div>
                  {blocked ? (
                    <div className="card-body stack" style={{ gap: 12 }}>
                      <p className="helper" style={{ color: 'var(--text-primary)' }}>{last!.finding}</p>
                      {last!.evidence && (
                        <div className="table-wrap well"><table className="table">
                          <thead><tr><th>Text</th><th>Label</th><th>Model</th><th className="r">Conf.</th></tr></thead>
                          <tbody>{last!.evidence.map((e, i) => <tr key={i}><td style={{ whiteSpace: 'normal', lineHeight: '17px', padding: '6px 12px' }}>{e.text}</td><td>{e.given}</td><td>{e.says}</td><td className="r table-cell">{e.conf.toFixed(2)}</td></tr>)}</tbody>
                        </table></div>
                      )}
                      <div className="row" style={{ flexWrap: 'wrap' }}>
                        {last!.options!.map((o, i) => <button key={o.id} className={`btn ${i === 0 ? 'primary' : ''}`} onClick={() => setAnswers({ ...answers, [last!.id]: o.id })}>{o.label}</button>)}
                      </div>
                      <span className="helper" style={{ fontSize: 12 }}>Vela's suggestion is first. Training is paused until you answer.</span>
                    </div>
                  ) : (
                    <div className="card-body"><p className="helper">{finished ? `Vela asked you ${asked} time${asked === 1 ? '' : 's'} in this run.` : 'Nothing needs you. Vela stops here only when the labels themselves are in question.'}</p></div>
                  )}
                </div>

                <div className="card">
                  <div className="card-head"><div className="left"><h3 className="card-title">Data Changes</h3></div><span className="chip">v1 → v{version}</span></div>
                  <div className="card-body">
                    {([['Duplicates removed', diff.removed, 'var(--text-muted)', undefined], ['Rows relabelled', diff.relabelled, 'var(--accent-primary)', undefined], ['Synthetic rows added', diff.synthetic, 'var(--accent-secondary)', '5 4']] as [string, number, string, string | undefined][]).map(([l, v, c, dash]) => (
                      <div key={l} className="dist-row" style={{ gridTemplateColumns: '150px 1fr 60px' }}>
                        <span>{l}</span>
                        <svg width="100%" height="10" aria-hidden><line x1="0" x2="100%" y1="5" y2="5" stroke="var(--border-default)" strokeWidth="1.5" /><line x1="0" x2={`${Math.min(100, (v / (ds.rows * 0.016)) * 100)}%`} y1="5" y2="5" stroke={c} strokeWidth="2" strokeDasharray={dash} style={{ transition: 'all var(--duration-snap-max) var(--ease-pluck)' }} />{v > 0 && <circle cx={`${Math.min(100, (v / (ds.rows * 0.016)) * 100)}%`} cy="5" r="3" fill={c} />}</svg>
                        <span className="table-cell">{v.toLocaleString()}</span>
                      </div>
                    ))}
                    <p className="helper" style={{ marginTop: 8, fontSize: 12 }}>Synthetic rows are tagged <span className="mono">source=vela</span> and never enter validation or holdout.</p>
                  </div>
                </div>

                {finished && (
                  <div className="card navy">
                    <div className="card-head"><h3 className="card-title">Result</h3>{met ? <StatusPill status="success">Ready To Register</StatusPill> : <span className="micro-label">Not Yet</span>}</div>
                    <div className="minis">
                      <div className="mini"><span className="micro-label">Macro F1</span><span className="v">{m.f1.toFixed(3)}</span><span className="u">holdout</span></div>
                      <div className="mini"><span className="micro-label">{weak} Recall</span><span className="v">{m.rec.toFixed(2)}</span><span className="u">target {brief.recall.toFixed(2)}</span></div>
                      <div className="mini"><span className="micro-label">Latency</span><span className="v">{m.lat.toFixed(1)} ms</span><span className="u">limit {brief.latency} ms</span></div>
                    </div>
                    <div className="card-body row" style={{ borderTop: '1px solid var(--border-default)', flexWrap: 'wrap' }}>
                      {met ? <button className="btn primary" onClick={() => go('registry')}>Register Model Version</button> : <button className="btn primary" onClick={start}>Run Again</button>}
                      <button className="btn" onClick={() => go('gym')}>Open In Gym →</button>
                      <button className="btn ghost" onClick={() => setPhase('brief')}>Edit Brief</button>
                    </div>
                  </div>
                )}
              </div>
            </div>
          </div>
        )}
      </div>

      {phase !== 'run' && (
        <div className="gym-footer">
          <div />
          <div className="valid"><span className="dot">●</span> {ds.name} · {brief.autonomy}</div>
          <div className="right">
            {phase === 'plan' ? <><button className="btn" onClick={() => setPhase('brief')}>Edit Brief</button><button className="btn primary" onClick={start}>Start Autopilot</button></>
              : <button className="btn primary" disabled={phase === 'planning'} onClick={() => setPhase('planning')}>Draft Plan →</button>}
          </div>
        </div>
      )}
    </div>
  )
}
