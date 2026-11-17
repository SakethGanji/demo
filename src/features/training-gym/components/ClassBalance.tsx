import { CLASSES, CLASS_COUNTS } from '@/shared/data/runs'
import { ParamChip } from '@/shared/primitives/ParamChip'

export function ClassBalance() {
  const total = CLASS_COUNTS.reduce((a, b) => a + b, 0)
  const max = Math.max(...CLASS_COUNTS)
  return (
    <div className="card">
      <div className="card-head">
        <div className="left"><h3 className="card-title">Label Distribution</h3></div>
        <ParamChip sym="K" value="6" />
      </div>
      <table className="table">
        <thead><tr><th>Class</th><th style={{ width: '45%' }}>Share</th><th className="r">Rows</th><th className="r">%</th></tr></thead>
        <tbody>
          {CLASSES.map((c, i) => {
            const w = (CLASS_COUNTS[i] / max) * 100
            const low = CLASS_COUNTS[i] / total < 0.1
            return (
              <tr key={c}>
                <td>{c}</td>
                <td>
                  <svg width="100%" height="10" aria-hidden>
                    <line x1="0" x2={`${w}%`} y1="5" y2="5" stroke={low ? 'var(--status-warning)' : 'var(--accent-primary)'} strokeWidth="2" strokeDasharray={low ? '4 3' : undefined} />
                    <circle cx={`${w}%`} cy="5" r="3" fill={low ? 'var(--status-warning)' : 'var(--accent-primary)'} />
                  </svg>
                </td>
                <td className="r table-cell">{CLASS_COUNTS[i].toLocaleString()}</td>
                <td className="r table-cell">{((CLASS_COUNTS[i] / total) * 100).toFixed(1)}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
      <div className="card-body" style={{ borderTop: '1px solid var(--border-default)' }}>
        <p className="helper"><span style={{ color: 'var(--status-warning)' }}>▲</span> Class imbalance detected. Consider stratified CV &amp; <span className="mono">class_weight='balanced'</span> in Step 7.</p>
      </div>
    </div>
  )
}
