import { useMemo, useState } from 'react'
import { ConfusionMatrix, LineChart, ParallelCoords, PlotFrame, type PCAxis, type Series } from '../components/charts'
import { ParamChip, StatusPill, Tabs } from '../components/ui'
import { CLASSES, CONFUSION, PC, RUNS, curve } from '../data'
import { RunTable } from './Home'

const COLORS = ['var(--viz-a)', 'var(--viz-c)', 'var(--viz-d)', 'var(--viz-b)', 'var(--viz-e)']
const DASH = [undefined, '6 4', '2 3', '10 3 2 3', '1 3']
const FINAL: Record<string, number> = Object.fromEntries(Object.entries(PC).map(([k, v]) => [k, v.f1]))
const AXES: PCAxis[] = [
  { key: 'd', label: 'd', min: 0, max: 450, fmt: (v) => `${v < 1 ? v.toFixed(1) : v.toFixed(0)}k` },
  { key: 't', label: 'fit time', min: 0, max: 900, fmt: (v) => `${v.toFixed(0)}s` },
  { key: 'auc', label: 'AUC', min: 0.9, max: 0.95, fmt: (v) => v.toFixed(3) },
  { key: 'rec', label: 'recall Security', min: 0.7, max: 0.9, fmt: (v) => v.toFixed(2) },
  { key: 'f1', label: 'F1', min: 0.86, max: 0.92, fmt: (v) => v.toFixed(3) },
]

export function Experiments({ go }: { go: (r: string) => void }) {
  const [tab, setTab] = useState<'Runs' | 'Compare' | 'Confusion Matrix & Errors'>('Runs')
  const [sel, setSel] = useState<Set<string>>(new Set(['run_894a', 'run_8931', 'run_8912']))
  const [onlyGood, setOnlyGood] = useState(false)
  const toggle = (id: string) => setSel((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n })
  const runs = onlyGood ? RUNS.filter((r) => (r.f1 ?? 0) >= 0.9) : RUNS

  const series: Series[] = useMemo(() => [...sel].filter((id) => FINAL[id]).slice(0, 5).map((id, i) => ({
    name: id, color: COLORS[i], dash: DASH[i], data: curve(30, 0.52 + i * 0.05, FINAL[id], 0.22 - i * 0.05, 0.006, id.charCodeAt(5) * 13),
  })), [sel])

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1 className="page-title">Experiments</h1>
          <p className="helper sub">Enterprise Support Ticket Classifier · {RUNS.length} runs · {sel.size} selected</p>
        </div>
        <div className="row">
          <button className={`btn ${onlyGood ? 'vela on' : ''}`} onClick={() => setOnlyGood(!onlyGood)}><span className="math" style={{ fontSize: 14 }}>F1</span> ≥ 0.90</button>
          <button className="btn primary" onClick={() => go('registry')}>Register Model Version</button>
        </div>
      </div>

      <Tabs tabs={['Runs', 'Compare', 'Confusion Matrix & Errors']} value={tab} onChange={setTab} />

      {tab === 'Runs' && (
        <>
          <div className="card"><RunTable runs={runs} selected={sel} onToggle={toggle} /></div>
          <PlotFrame title="String Plot"
            chips={<><ParamChip sym="n" value={`${runs.filter((r) => PC[r.id]).length} runs`} /><ParamChip sym="k" value="5" /></>}
            legend={[{ name: 'Selected', color: 'var(--accent-primary)', data: [] }, { name: 'Not selected', color: 'var(--border-track)', dash: '4 3', data: [] }]}
            caption={<span>One string per completed run. <span className="math" style={{ fontSize: 14 }}>d</span> = feature count; click a string to select it</span>}>
            <ParallelCoords axes={AXES} rows={runs.filter((r) => PC[r.id]).map((r) => ({ id: r.id, v: PC[r.id] }))} selected={sel} onPick={toggle} />
          </PlotFrame>
        </>
      )}

      {tab === 'Compare' && (
        <div className="grid two">
          <PlotFrame title="Validation Macro F1" legend={series}
            chips={<><ParamChip sym="k" value="5" /><ParamChip sym="seed" value="42" /></>}
            caption={<span><span className="math" style={{ fontSize: 14 }}>F1</span> macro-averaged over <span className="math" style={{ fontSize: 14 }}>K</span> = 6 classes, mean of folds, per epoch <span className="math" style={{ fontSize: 14 }}>t</span></span>}>
            {series.length ? (
              <LineChart series={series} xMax={29} yMin={0.5} yMax={1} yTicks={[0.5, 0.6, 0.7, 0.8, 0.9, 1]} xTicks={[0, 10, 20, 29]} xTitle="t" yTitle="F1" reference={{ y: 0.9, label: 'threshold 0.90' }} height={260} />
            ) : <p className="helper" style={{ padding: 24 }}>Select completed runs in the Runs tab to compare.</p>}
          </PlotFrame>
          <div className="card">
            <div className="card-head"><h3 className="card-title">Parameter Diff</h3><span className="micro-label">vs run_894a</span></div>
            <table className="table">
              <thead><tr><th>Param</th><th>run_894a</th><th>run_8912</th><th>run_8931</th></tr></thead>
              <tbody>
                {[
                  ['model', 'LogReg', 'LinearSVC', 'GBM'],
                  ['C', '1.0', '0.5', '—'],
                  ['ngram', '(1,2)', '(1,3)', '(1,2)'],
                  ['M', '—', '—', '600'],
                  ['η', '—', '—', '0.05'],
                  ['F1', '0.914', '0.907', '0.902'],
                ].map(([k, a, b, c]) => (
                  <tr key={k}>
                    <td><span className="math" style={{ fontSize: 14 }}>{k}</span></td>
                    <td className="table-cell">{a}</td>
                    <td className="table-cell" style={{ background: b !== a ? 'var(--bg-wave-tint)' : undefined }}>{b}</td>
                    <td className="table-cell" style={{ background: c !== a ? 'var(--bg-wave-tint)' : undefined }}>{c}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {tab === 'Confusion Matrix & Errors' && (
        <div className="grid two">
          <div className="card">
            <div className="card-head">
              <div className="left"><h3 className="card-title">Confusion Matrix</h3><span className="mono" style={{ fontSize: 12.5 }}>run_894a</span></div>
              <div className="row"><ParamChip sym="n" value="18,675" /><ParamChip sym="fold" value="holdout" /></div>
            </div>
            <div className="card-body"><ConfusionMatrix labels={CLASSES} m={CONFUSION} /></div>
            <div className="caption" style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '8px 16px 12px', borderTop: '1px solid var(--border-row)', fontSize: 12 }}>
              <span>Share of actual row</span>
              <span className="ramp">
                {[1, 2, 3, 4, 5].map((i) => <i key={i} style={{ background: `var(--heat-${i})` }} />)}
              </span>
              <span className="mono" style={{ fontSize: 11 }}>&lt;1% · 1–3% · 3–10% · 10–50% · ≥50%</span>
            </div>
          </div>
          <div className="card">
            <div className="card-head"><h3 className="card-title">Per-Class Recall</h3><span className="micro-label">Mean 0.94</span></div>
            <table className="table">
              <thead><tr><th>Class</th><th className="r">Recall</th><th className="r">Precision</th><th>Top Confusion</th></tr></thead>
              <tbody>
                {CLASSES.map((c, i) => {
                  const row = CONFUSION[i]; const tot = row.reduce((a, b) => a + b, 0)
                  const col = CONFUSION.reduce((a, r) => a + r[i], 0)
                  const rec = row[i] / tot, prec = row[i] / col
                  const worst = row.map((v, j) => (j === i ? -1 : v)).reduce((bi, v, j, arr) => (v > arr[bi] ? j : bi), 0)
                  return (
                    <tr key={c} className={rec < 0.9 ? 'sel' : ''}>
                      <td>{c}</td>
                      <td className="r table-cell">{rec.toFixed(3)}</td>
                      <td className="r table-cell">{prec.toFixed(3)}</td>
                      <td>{rec < 0.9 ? <StatusPill status="warning">{CLASSES[worst]}</StatusPill> : <span className="muted">{CLASSES[worst]}</span>}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}
