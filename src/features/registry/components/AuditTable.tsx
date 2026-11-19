import type { AuditEntry } from '@/shared/api/types'
import { StatusPill } from '@/shared/primitives/StatusPill'
import { ACTION_WORD, short, when } from '../lib'

const TONE: Record<AuditEntry['action'], 'success' | 'warning' | 'danger' | 'neutral' | 'info'> = {
  registered: 'neutral', stage_changed: 'info', approved: 'success', rejected: 'danger', card_edited: 'neutral', batch_scored: 'neutral',
  labels_attached: 'neutral', monitoring_configured: 'info', retrain_launched: 'warning', reason_codes_edited: 'neutral', dataset_loaded: 'info', dataset_refreshed: 'info',
}

/** Append-only: who did what to which version, when, with the model's SHA-256 at that moment. */
export function AuditTable({ entries, onVersion, testId = 'audit' }: { entries: AuditEntry[]; onVersion?: (id: string) => void; testId?: string }) {
  if (!entries.length) return <p className="helper card-body">Nothing recorded yet.</p>
  return (
    <div className="table-wrap">
      <table className="table compact" data-testid={testId}>
        <thead><tr><th>#</th><th>when (UTC)</th><th>who</th><th>action</th><th>version</th><th>stage</th><th>comment</th><th>model SHA-256</th></tr></thead>
        <tbody>{entries.map((e) => (
          <tr key={e.id} data-action={e.action}>
            <td className="mono muted">{e.id}</td>
            <td className="mono">{when(e.ts)}</td>
            <td>{e.actor}</td>
            <td><StatusPill status={TONE[e.action]}>{ACTION_WORD[e.action]}</StatusPill></td>
            <td className="mono">{e.version_id && onVersion ? <button className="linkish" onClick={() => onVersion(e.version_id!)}>{e.model_name} v{e.version}</button> : e.version != null ? `${e.model_name ?? ''} v${e.version}` : <span className="muted">dataset</span>}</td>
            <td className="mono">{e.to_stage ? `${e.from_stage ?? '·'} → ${e.to_stage}` : ''}</td>
            <td className="muted" style={{ maxWidth: 320 }}>{e.comment}</td>
            <td className="mono muted" title={e.model_hash ?? ''}>{short(e.model_hash)}</td>
          </tr>
        ))}</tbody>
      </table>
    </div>
  )
}
