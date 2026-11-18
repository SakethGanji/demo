import { useEffect, useMemo, useState } from 'react'
import { useApi } from '@/shared/api/mode'
import { LiveAutopilot } from './live/LiveAutopilot'
import type { Dataset } from '@/features/quick-train/datasets'
import { Segmented } from '@/shared/primitives/Segmented'
import { StatusPill } from '@/shared/primitives/StatusPill'
import { VelaCrest } from '@/shared/primitives/VelaCrest'
import { BriefStep } from './components/BriefStep'
import { DataChanges } from './components/DataChanges'
import { DecisionLog } from './components/DecisionLog'
import { GroundTruthInbox } from './components/GroundTruthInbox'
import { IterationString } from './components/IterationString'
import { Gauge } from './components/RequirementGauge'
import { TEXT_SAMPLES, buildRun, type Brief, type Diff, type Metrics } from './run'

export function AutopilotPage({ go }: { go: (r: string) => void }) {
  const { mode } = useApi()
  if (mode === 'live') return <LiveAutopilot go={go} />
  return <ScriptedAutopilot go={go} />
}

/** Mock mode: the scripted prototype, labelled SIMULATED. */
function ScriptedAutopilot({ go }: { go: (r: string) => void }) {
  const [ds, setDs] = useState<Dataset>(TEXT_SAMPLES[0])
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

  const inbox = <GroundTruthInbox step={last} blocked={blocked} finished={finished} asked={asked} onAnswer={(id, option) => setAnswers({ ...answers, [id]: option })} />

  return (
    <div className="wizard">
      <div className="stepper-bar" style={{ paddingBottom: 16 }}>
        <div className="stepper-title" style={{ marginBottom: 0 }}>
          <VelaCrest size={26} />
          <h1 className="page-title">Vela Autopilot</h1>
          <span className="helper">It trains, tunes and fixes the data, and only stops to ask you what is true.</span>
          <span className="pill warning" data-testid="autopilot-simulated">SIMULATED</span>
          <span className="spacer" />
          <Segmented options={['Manual', 'Autopilot']} value="Autopilot" onChange={(v) => v === 'Manual' && go('gym')} />
        </div>
      </div>

      <div className="wizard-scroll">
        {phase !== 'run' ? (
          <BriefStep ds={ds} setDs={setDs} goal={goal} setGoal={setGoal} brief={brief} setBrief={setBrief} phase={phase} onEdit={() => setPhase('brief')} />
        ) : (
          <div className="page">
            <section className="stage">
              <div className="stage-head">
                <div>
                  <span className="hero-eyebrow" style={{ paddingLeft: 0, color: blocked ? '#FBBF24' : undefined }}>{finished ? (met ? 'DONE · REQUIREMENTS MET' : 'STOPPED · REQUIREMENTS NOT MET') : blocked ? 'WAITING ON YOU' : paused ? 'PAUSED' : 'VELA IS WORKING'}</span>
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

            {blocked && inbox}

            <div className="grid" style={{ gridTemplateColumns: 'minmax(0, 1.35fr) minmax(0, 1fr)', alignItems: 'start' }}>
              <DecisionLog done={done} next={working ? next : undefined} weak={weak} met={met} />

              <div className="stack">
                {!blocked && inbox}

                <DataChanges diff={diff} version={version} rows={ds.rows} />

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
        <div className="wizard-footer">
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
