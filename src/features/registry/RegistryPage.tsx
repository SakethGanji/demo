import { useState } from 'react'
import { Graph } from '@/shared/components/Graph'
import { SparkWave } from '@/shared/primitives/SparkWave'
import { StatusPill, type Status } from '@/shared/primitives/StatusPill'

const VERSIONS: [string, Status, string, string, string, string, string, string][] = [
  ['v3.0.0-rc1', 'warning', 'Staging', 'run_894a', '0.914', '0.941', 'Awaiting · Arun Patel', '2026-11-16'],
  ['v2.4.1', 'success', 'Production', 'run_81c2', '0.893', '0.925', 'Maya Lin', '2026-09-02'],
  ['v2.4.0', 'neutral', 'Archived', 'run_80aa', '0.889', '0.921', 'Maya Lin', '2026-08-11'],
  ['v2.3.0', 'neutral', 'Archived', 'run_7f31', '0.874', '0.910', 'Jo Kim', '2026-06-24'],
  ['v2.2.0', 'danger', 'Rejected', 'run_7e09', '0.861', '0.902', 'Arun Patel', '2026-05-30'],
]

export function RegistryPage({ go }: { go: (r: string) => void }) {
  const [approved, setApproved] = useState(false)
  const versions = VERSIONS.map((v) => (!approved ? v : v[0] === 'v3.0.0-rc1' ? [v[0], 'success', 'Production', v[3], v[4], v[5], 'Maya Lin', v[7]] : v[0] === 'v2.4.1' ? [v[0], 'neutral', 'Archived', ...v.slice(3)] : v) as typeof v)
  return (
    <div className="page">
      <div className="page-head">
        <div><h1 className="page-title">Model Registry</h1><p className="helper sub">Enterprise Support Ticket Classifier · 5 versions · {approved ? 'v3.0.0-rc1' : 'v2.4.1'} in Production</p></div>
        <div className="row">
          <button className="btn" onClick={() => go('experiments')}>Compare Runs →</button>
          {approved ? <StatusPill status="success">v3.0.0-rc1 Approved</StatusPill> : <button className="btn primary" onClick={() => setApproved(true)}>Approve v3.0.0-rc1</button>}
        </div>
      </div>
      <div className="grid kpis">
        {([approved ? ['Production', 'v3.0.0-rc1', 'F1: 0.914 · live now', [0.9, 0.905, 0.91, 0.912, 0.914, 0.914, 0.914]] : ['Production', 'v2.4.1', 'F1: 0.893 · 75d live', [0.893, 0.892, 0.893, 0.891, 0.889, 0.887, 0.884]], approved ? ['Staging', '—', 'nothing staged', [0, 0, 0, 0, 0, 0, 0]] : ['Staging', 'v3.0.0-rc1', 'Δ +0.021 vs v2.4.1', [0.9, 0.905, 0.91, 0.912, 0.914, 0.914, 0.914]], ['Drift · 7d', '0.031', 'PSI · threshold 0.10', [0.01, 0.012, 0.018, 0.02, 0.026, 0.028, 0.031]], ['Requests · 24h', '48,210', 'p95: 41 ms', [38, 41, 44, 40, 47, 49, 48]]] as [string, string, string, number[]][]).map(([l, v, f, sp]) => (
          <div key={l} className="card kpi">
            <span className="micro-label">{l}</span>
            <div className="row" style={{ justifyContent: 'space-between', alignItems: 'flex-end' }}><span className="metric-hero" style={{ fontSize: 19, whiteSpace: 'nowrap' }}>{v}</span><SparkWave data={sp} /></div>
            <div className="foot"><span className="num">{f}</span></div>
          </div>
        ))}
      </div>
      <div className="card">
        <div className="card-head"><div className="left"><h3 className="card-title">Version Lineage</h3></div>
          <div className="row">
            <span className="legend"><svg width="20" height="6"><line x1="0" x2="20" y1="3" y2="3" stroke="var(--accent-primary)" strokeWidth="2" /></svg>Approved</span>
            <span className="legend"><svg width="20" height="6"><line x1="0" x2="20" y1="3" y2="3" stroke="var(--status-warning)" strokeWidth="2" strokeDasharray="4 3" /></svg>Awaiting Approval</span>
          </div>
        </div>
        <Graph width={1080} height={190} nodeW={176} nodeH={64}
          nodes={[
            { id: 'd1', x: 20, y: 20, title: 'zendesk_tickets_q2', sub: '98,200 rows' },
            { id: 'd2', x: 20, y: 106, title: 'zendesk_tickets_q3', sub: '124,500 rows' },
            { id: 'r1', x: 240, y: 20, title: 'run_81c2', sub: 'C=0.1 · F1: 0.893' },
            { id: 'r2', x: 240, y: 106, title: 'run_894a', sub: 'C=1.0 · F1: 0.914' },
            { id: 'v1', x: 460, y: 20, title: 'v2.4.1', sub: 'Approved · Maya Lin' },
            { id: 'v2', x: 460, y: 106, title: 'v3.0.0-rc1', sub: approved ? 'Approved · Maya Lin' : 'Awaiting approval', active: !approved },
            { id: 'p', x: 680, y: 20, title: approved ? 'Archived' : 'Production', sub: approved ? 'retired just now' : '48,210 req / 24h' },
            { id: 's', x: 680, y: 106, title: approved ? 'Production' : 'Staging', sub: approved ? '100% traffic' : 'shadow 10% traffic' },
            { id: 'm', x: 884, y: 63, title: 'Drift Monitor', sub: 'PSI: 0.031' },
          ]}
          edges={[{ from: 'd1', to: 'r1' }, { from: 'd2', to: 'r2' }, { from: 'r1', to: 'v1' }, { from: 'r2', to: 'v2', kind: 'active' }, { from: 'v1', to: 'p' }, { from: 'v2', to: 's', kind: approved ? 'ok' : 'warn' }, { from: 'p', to: 'm' }, { from: 's', to: 'm' }]} />
      </div>
      <div className="card table-wrap">
        <table className="table">
          <thead><tr><th>Version</th><th>Stage</th><th>Source Run</th><th className="r">Macro F1</th><th className="r">AUC</th><th>Approved By</th><th className="r">Registered</th></tr></thead>
          <tbody>
            {versions.map(([v, st, word, run, f1, auc, by, at], i) => (
              <tr key={v} className={i === 0 ? 'sel' : ''}>
                <td className="table-cell">{v}</td><td><StatusPill status={st}>{word}</StatusPill></td><td className="table-cell">{run}</td>
                <td className="r table-cell">{f1}</td><td className="r table-cell">{auc}</td><td className="muted">{by}</td><td className="r table-cell">{at}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
