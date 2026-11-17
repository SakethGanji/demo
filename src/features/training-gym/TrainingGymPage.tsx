import { useState } from 'react'
import { Segmented } from '@/shared/primitives/Segmented'
import { StatusPill } from '@/shared/primitives/StatusPill'
import { ConsoleDock } from './components/ConsoleDock'
import { Hyperparameters } from './components/Hyperparameters'
import { StepSummary } from './components/StepSummary'
import { HYPER, STEPS, type Params } from './params'

export function TrainingGymPage({ params, setParams, guided, setGuided, onStart, onAutopilot }: {
  params: Params; setParams: (p: Params) => void; guided: boolean; setGuided: (g: boolean) => void; onStart: () => void; onAutopilot: () => void
}) {
  const [step, setStep] = useState(HYPER)
  const [saved, setSaved] = useState(false)
  const save = () => { setSaved(true); setTimeout(() => setSaved(false), 1800) }
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
          <Segmented options={['Manual', 'Autopilot']} value="Manual" onChange={(v) => v === 'Autopilot' && onAutopilot()} />
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

      <ConsoleDock params={params} />

      <div className="gym-footer">
        <div><button className="btn ghost" onClick={() => setStep(Math.max(0, step - 1))} disabled={step === 0}>← Back</button></div>
        <div className="valid"><span className="dot">●</span> Schema Valid · Ready</div>
        <div className="right">
          <button className="btn" onClick={save}>{saved ? '● Draft Saved' : 'Save Draft'}</button>
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
