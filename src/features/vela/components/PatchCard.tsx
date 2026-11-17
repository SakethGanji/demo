import type { ReactNode } from 'react'
import { StatusPill } from '@/shared/primitives/StatusPill'
import { VelaCrest } from '@/shared/primitives/VelaCrest'

export type Patch = {
  id: string
  title: string
  finding: ReactNode
  from: string
  to: string
  apply?: () => void
}

export function PatchCard({ p, applied, onApply, onDismiss }: { p: Patch; applied: boolean; onApply: () => void; onDismiss: () => void }) {
  return (
    <div className={`patch ${applied ? 'applied' : ''}`}>
      <div className="ph">
        <VelaCrest size={16} />
        <b className="card-title" style={{ fontSize: 13 }}>{p.title}</b>
        <span className="spacer" />
        {applied && <StatusPill status="success">Applied</StatusPill>}
      </div>
      <p>{p.finding}</p>
      <div className="diffline"><span className="old">{p.from}</span> → {p.to}</div>
      {!applied && (
        <div className="row">
          <button className="btn sm primary" onClick={onApply}>Apply Patch</button>
          <button className="btn sm ghost" onClick={onDismiss}>Dismiss</button>
        </div>
      )}
    </div>
  )
}
