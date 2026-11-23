import type { Diff } from '../steps'

/** What Vela changed in the dataset so far. */
export function DataChanges({ diff, version, rows }: { diff: Diff; version: number; rows: number }) {
  return (
    <div className="card">
      <div className="card-head"><div className="left"><h3 className="card-title">Data Changes</h3></div><span className="chip">v1 → v{version}</span></div>
      <div className="card-body">
        {([['Duplicates removed', diff.removed, 'var(--text-muted)', undefined], ['Rows relabelled', diff.relabelled, 'var(--accent-primary)', undefined], ['Synthetic rows added', diff.synthetic, 'var(--accent-secondary)', '5 4']] as [string, number, string, string | undefined][]).map(([l, v, c, dash]) => (
          <div key={l} className="dist-row" style={{ gridTemplateColumns: '150px 1fr 60px' }}>
            <span>{l}</span>
            <svg width="100%" height="10" aria-hidden><line x1="0" x2="100%" y1="5" y2="5" stroke="var(--border-default)" strokeWidth="1.5" /><line x1="0" x2={`${Math.min(100, (v / (rows * 0.016)) * 100)}%`} y1="5" y2="5" stroke={c} strokeWidth="2" strokeDasharray={dash} style={{ transition: 'all var(--duration-snap-max) var(--ease-pluck)' }} />{v > 0 && <circle cx={`${Math.min(100, (v / (rows * 0.016)) * 100)}%`} cy="5" r="3" fill={c} />}</svg>
            <span className="table-cell">{v.toLocaleString()}</span>
          </div>
        ))}
        <p className="helper" style={{ marginTop: 8, fontSize: 12 }}>Synthetic rows are marked in the dataset version's lineage (<span className="mono">lineage.json</span>), sit in training folds only and never enter the holdout.</p>
      </div>
    </div>
  )
}
