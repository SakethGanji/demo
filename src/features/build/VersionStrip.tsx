import { data } from '@/shared/api/catalog'
import { useFetch } from '@/shared/hooks/useFetch'
import '../models/data/data.css'

/** DL1: the versions of the chosen table, oldest first (★ the default). Picking one opens it in Build; Data opens the
 *  dataset's page (history, diff, upload a new version, what used each one). */
export function VersionStrip({ dsId, onPick, go }: { dsId: string; onPick: (id: string) => void; go: (r: string) => void }) {
  const { data: h } = useFetch(() => data.history(dsId), [dsId])
  if (!h || h.focus !== dsId) return null
  const live = h.versions.filter((v) => !v.state)
  const cur = h.versions.find((v) => v.id === dsId)
  return (
    <div className="data-strip" data-testid="build-versions">
      <span className="micro-label">versions</span>
      {live.map((v) => (
        <button key={v.id} className={`chip-btn ${v.id === dsId ? 'on' : ''}`} onClick={() => onPick(v.id)} data-testid={`build-v-${v.v}`}
          title={`${v.id} · ${v.op ?? ''} · ${v.created_by ?? ''} · ${v.rows.toLocaleString()} rows`}>v{v.v}{v.is_default ? ' ★' : ''}</button>
      ))}
      <button className="chip-btn" onClick={() => go(`build?view=data&dataset=${h.root_id}`)} data-testid="build-data-link">History, diff, new version →</button>
      {cur?.state === 'archived' && <span className="helper" style={{ color: 'var(--status-warning)' }} data-testid="build-archived-note">▲ v{cur.v} is archived ({cur.state_by}): you can still train on it, and the run records that.</span>}
    </div>
  )
}
