import { useEffect, useState } from 'react'
import { LineChart, PlotFrame, type Series } from '../components/charts'
import { ParamChip, ResonanceWave, SparkWave, StatusPill, type Status } from '../components/ui'
import { LOG_LINES, RUNS, curve, type Run } from '../data'

const TRAIN = curve(40, 1.2, 0.24, 0.12, 0.03, 7)
const VAL = curve(40, 1.25, 0.29, 0.1, 0.04, 11)
export const STATUS: Record<Run['status'], Status> = { Training: 'info', Completed: 'success', Queued: 'neutral', Failed: 'danger' }

export function Home({ go }: { go: (r: string) => void }) {
  const [n, setN] = useState(14)
  const [logN, setLogN] = useState(5)
  useEffect(() => {
    const t = setInterval(() => {
      setN((v) => (v >= 40 ? 14 : v + 1))
      setLogN((v) => (v >= LOG_LINES.length ? 5 : v + 1))
    }, 900)
    return () => clearInterval(t)
  }, [])
  const series: Series[] = [
    { name: 'Train loss', color: 'var(--viz-a)', data: TRAIN.slice(0, n) },
    { name: 'Validation loss', color: 'var(--viz-c)', dash: '6 4', data: VAL.slice(0, n) },
  ]

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1 className="page-title">Home</h1>
          <p className="helper sub">Project ENSEMBLE · ml-platform workspace · 1 run training, 1 queued</p>
        </div>
        <button className="btn primary" onClick={() => go('gym')}>Train in Gym →</button>
      </div>

      <div className="grid kpis">
        <Kpi label="Best Macro F1" value="0.914" extra="± 0.006" foot={<><span>n = 5 folds</span><span className="delta-up num">Δ +0.021 vs v2.4</span></>} spark={[0.86, 0.87, 0.875, 0.89, 0.893, 0.902, 0.907, 0.914]} />
        <Kpi label="Active Training Runs" value="1" foot={<><span className="mono">run_8a10</span><StatusPill status="info">Training</StatusPill></>} spark={[2, 3, 1, 4, 2, 3, 2, 1]} />
        <Kpi label="Compute Hours · 7d" value="312.4" foot={<><span>cpu + gpu</span><span className="delta-down num">Δ +18.2%</span></>} spark={[30, 42, 38, 51, 47, 55, 49]} />
        <Kpi label="Models in Production" value="12" foot={<><span>3 in Staging</span><StatusPill status="success">Healthy</StatusPill></>} spark={[9, 9, 10, 10, 11, 11, 12]} />
      </div>

      <div className="grid two">
        <PlotFrame
          title="Training Monitor"
          right={<><span className="mono" style={{ fontSize: 12.5 }}>run_8a10</span><ResonanceWave width={56} height={14} /></>}
          chips={<><ParamChip sym="C" value="2.0" /><ParamChip sym="k" value="5" /><ParamChip sym="seed" value="42" /></>}
          legend={series}
          caption={<span>Cross-entropy <span className="math" style={{ fontSize: 14 }}>ℒ = −Σ y log ŷ</span> per epoch <span className="math" style={{ fontSize: 14 }}>t</span></span>}
        >
          <LineChart series={series} xMax={40} yMin={0} yMax={1.4} yTicks={[0, 0.35, 0.7, 1.05, 1.4]} xTicks={[0, 10, 20, 30, 40]} xTitle="t" yTitle="ℒ" fmtY={(v) => v.toFixed(2)} live />
        </PlotFrame>
        <div className="card">
          <div className="card-head">
            <div className="left"><h3 className="card-title">Live Log</h3><span className="mono" style={{ fontSize: 12.5 }}>run_8a10</span></div>
            <StatusPill status="info">Streaming</StatusPill>
          </div>
          <div className="card-body">
            <div className="log-panel" aria-live="polite">
              {LOG_LINES.slice(0, logN).map(([c, l], i) => <div key={i} className={c}>{l}</div>)}
            </div>
          </div>
        </div>
      </div>

      <div className="card">
        <div className="card-head">
          <div className="left"><h3 className="card-title">Recent Runs</h3><span className="micro-label">Enterprise Support Ticket Classifier</span></div>
          <button className="btn sm" onClick={() => go('experiments')}>Open Experiments →</button>
        </div>
        <RunTable runs={RUNS.slice(0, 5)} />
      </div>
    </div>
  )
}

function Kpi({ label, value, extra, foot, spark }: { label: string; value: string; extra?: string; foot: React.ReactNode; spark: number[] }) {
  return (
    <div className="card kpi">
      <span className="micro-label">{label}</span>
      <div className="row" style={{ justifyContent: 'space-between', alignItems: 'flex-end' }}>
        <span style={{ whiteSpace: 'nowrap' }}><span className="metric-hero">{value}</span>{extra && <span className="num" style={{ marginLeft: 6, color: 'var(--text-body)', fontSize: 12, whiteSpace: 'nowrap' }}>{extra}</span>}</span>
        <SparkWave data={spark} />
      </div>
      <div className="foot">{foot}</div>
    </div>
  )
}

export function RunTable({ runs, selected, onToggle }: { runs: Run[]; selected?: Set<string>; onToggle?: (id: string) => void }) {
  return (
    <table className="table">
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
            <td className="table-cell">{r.id}</td>
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
    </table>
  )
}
