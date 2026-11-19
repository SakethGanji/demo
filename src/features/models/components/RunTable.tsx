import type { Run } from '@/shared/data/runs'
import { StatusPill, type Status } from '@/shared/primitives/StatusPill'

export const STATUS: Record<Run['status'], Status> = { Training: 'info', Completed: 'success', Queued: 'neutral', Failed: 'danger' }

export function RunTable({ runs, selected, onToggle }: { runs: Run[]; selected?: Set<string>; onToggle?: (id: string) => void }) {
  return (
    <div className="table-wrap"><table className="table">
      <thead>
        <tr>
          {onToggle && <th style={{ width: 32 }} />}
          <th>Run</th><th>Pipeline</th><th>Parameters</th><th>Status</th><th className="r">Macro F1</th><th className="r">AUC</th><th className="r">Duration</th><th>Owner</th>
        </tr>
      </thead>
      <tbody>
        {runs.map((r) => (
          <tr key={r.id} className={selected?.has(r.id) ? 'sel' : ''} onClick={() => onToggle?.(r.id)} style={{ cursor: onToggle ? 'pointer' : undefined }}>
            {onToggle && <td><input type="checkbox" checked={selected?.has(r.id) ?? false} onChange={() => onToggle(r.id)} onClick={(e) => e.stopPropagation()} aria-label={`Select ${r.id}`} style={{ accentColor: 'var(--accent-primary)' }} /></td>}
            <td className="table-cell"><a href={`#/models?run=${r.id}`} onClick={(e) => e.stopPropagation()}>{r.id}</a></td>
            <td>{r.pipeline}</td>
            <td className="table-cell muted">{r.params}</td>
            <td><StatusPill status={STATUS[r.status]}>{r.status}</StatusPill></td>
            <td className="r table-cell">{r.f1 !== null ? <>{r.f1.toFixed(3)} <span className="muted">± {r.spread!.toFixed(3)}</span></> : <span className="muted">—</span>}</td>
            <td className="r table-cell">{r.auc !== null ? r.auc.toFixed(3) : <span className="muted">—</span>}</td>
            <td className="r table-cell">{r.duration}</td>
            <td className="muted">{r.owner}</td>
          </tr>
        ))}
      </tbody>
    </table></div>
  )
}
