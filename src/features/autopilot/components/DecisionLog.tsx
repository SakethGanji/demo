import { ParamChip } from '@/shared/primitives/ParamChip'
import { ResonanceWave } from '@/shared/primitives/ResonanceWave'
import { StatusPill } from '@/shared/primitives/StatusPill'
import { VelaCrest } from '@/shared/primitives/VelaCrest'
import type { Step } from '../run'

/** Everything Vela did, newest first. `next` is the step it is working on right now. */
export function DecisionLog({ done, next, weak, met }: { done: Step[]; next?: Step; weak: string; met: boolean }) {
  const kept = done.filter((s) => s.after)
  return (
    <div className="card">
      <div className="card-head"><div className="left"><h3 className="card-title">Decision Log</h3><span className="helper" style={{ fontSize: 12 }}>everything Vela did, newest first</span></div><ParamChip sym="n" value={String(done.length)} /></div>
      <div className="log-cards">
        {next && <div className="thinking" style={{ padding: '4px 4px 0' }}><ResonanceWave width={84} height={18} fast /> {next.kind === 'branch' ? 'Trying' : next.kind === 'ask' ? 'Checking' : 'Working on'} {next.short.toLowerCase()}</div>}
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
  )
}
