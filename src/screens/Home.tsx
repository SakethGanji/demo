import { useEffect, useState } from 'react'
import wordmark from '../assets/ensemble-wordmark-on-shell.svg'
import { LineChart, PlotFrame, type Series } from '../components/charts'
import { Graph } from '../components/Graph'
import { Icon } from '../components/icons'
import { PluckString, RunString, StringField, ToolGlyph, type RunState } from '../components/Strings'
import { ParamChip, ResonanceWave, StatusPill, type Status } from '../components/ui'
import { ACTIVITY, LOG_LINES, NODES, UTIL, curve, type Run } from '../data'
import { usePrefs } from '../prefs'
import { TOOLS, type Tool } from '../tools'

const TRAIN = curve(40, 1.2, 0.24, 0.12, 0.03, 7)
const VAL = curve(40, 1.25, 0.29, 0.1, 0.04, 11)
export const STATUS: Record<Run['status'], Status> = { Training: 'info', Completed: 'success', Queued: 'neutral', Failed: 'danger' }

function ToolCard({ tool, pinned, onPin, onOpen }: { tool: Tool; pinned: boolean; onPin: () => void; onOpen: () => void }) {
  const [pluck, setPluck] = useState(0)
  return (
    <div className="tool" onPointerEnter={() => setPluck((n) => n + 1)}>
      <button className="tool-main" onClick={onOpen}>
        <span className="tool-glyph"><ToolGlyph id={tool.id} /></span>
        <span className="tool-text">
          <span className="micro-label">{tool.verb}</span>
          <b className="card-title">{tool.name}</b>
          <span className="helper">{tool.desc}</span>
        </span>
      </button>
      <button className={`pin ${pinned ? 'on' : ''}`} aria-pressed={pinned} title={pinned ? 'Remove from toolbelt' : 'Add to toolbelt'} onClick={onPin}>{pinned ? '● On Belt' : '○ Pin'}</button>
      <div className="tool-foot"><span className="mono">{tool.meta}</span><span className="open">Open →</span></div>
      <div className="tool-string"><PluckString pluck={pluck} at={0.3} height={12} /></div>
    </div>
  )
}

export function Home({ go, openPalette }: { go: (r: string) => void; openPalette: () => void }) {
  const { prefs, set } = usePrefs()
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
  const live = 0.42 + ((n - 14) / 26) * 0.5
  const strings: RunState[] = [
    { id: 'run_8a10', label: 'TF-IDF → LogReg (OvR)', status: 'Training', progress: live, readout: `fold ${Math.min(5, Math.floor(live * 5) + 1)} of 5 · 06m 12s` },
    { id: 'run_894a', label: 'TF-IDF → LogReg (OvR)', status: 'Completed', progress: 1, readout: 'F1: 0.914 ± 0.006' },
    { id: 'run_8931', label: 'TF-IDF → SVD → GBM', status: 'Completed', progress: 1, readout: 'F1: 0.902 ± 0.009' },
    { id: 'run_88f7', label: 'Tokenize → DistilBERT', status: 'Failed', progress: 0.47, readout: 'CUDA OOM · fold 3' },
    { id: 'run_8b02', label: 'Counts → MultinomialNB', status: 'Queued', progress: 0, readout: 'position 1 · gym-default' },
  ]
  const togglePin = (id: string) => set({ pinned: prefs.pinned.includes(id) ? prefs.pinned.filter((p) => p !== id) : [...prefs.pinned, id] })
  const open = (t: Tool) => go(t.route)

  return (
    <div className="page">
      <section className="hero">
        <div className="hero-top">
          <div className="hero-lockup">
            <span className="hero-eyebrow">PROJECT</span>
            <img src={wordmark} alt="ENSEMBLE" height={44} />
          </div>
          <div className="hero-read">
            <span><b>1</b> run on the strings</span><span><b>12</b> in Production</span><span>best F1: <b>0.914 ± 0.006</b></span>
          </div>
        </div>
        <StringField height={104} />
        <div className="hero-bottom">
          <div>
            <h1 className="hero-title">Many models, one framework.</h1>
            <p className="hero-sub">A toolkit for training, comparing and shipping models. Pick up a tool, or tell the bench what you want to build.</p>
          </div>
          <button className="hero-cmd" onClick={openPalette}>
            <Icon.search width={16} height={16} /><span>Find a tool, run or model</span><kbd>⌘K</kbd>
          </button>
        </div>
      </section>

      <div className="bench-head">
        <h2 className="bench-title">Toolkit</h2>
        <span className="helper">{TOOLS.length} tools · {prefs.pinned.length} on your belt · pin a tool to keep it in the sidebar</span>
        <span className="spacer" />
        <button className="btn sm" onClick={() => go('settings')}>Customize Workbench</button>
      </div>
      <div className="tool-grid">
        {TOOLS.map((t) => <ToolCard key={t.id} tool={t} pinned={prefs.pinned.includes(t.id)} onPin={() => togglePin(t.id)} onOpen={() => open(t)} />)}
      </div>

      <div className="bench-head">
        <h2 className="bench-title">On the Strings</h2>
        <span className="helper">Each run is one string. Frets are folds, the node is progress, a wave means it is computing.</span>
        <span className="spacer" />
        <button className="btn sm" onClick={() => go('experiments')}>Open String Plot →</button>
      </div>
      <div className="card run-strings">
        {strings.map((r) => <RunString key={r.id} run={r} onClick={() => go('experiments')} />)}
      </div>

      <div className="grid two">
        <div className="stack">
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
            <div className="card-head"><h3 className="card-title">Activity</h3><span className="micro-label">Today</span></div>
            <div className="feed">
              {ACTIVITY.map((a, i) => (
                <div key={i} className="feed-item">
                  <span className={`nd ${a.kind === 'live' ? 'live' : a.kind === 'bad' ? 'bad' : ''}`} />
                  <p><b>{a.who}</b> {a.what} <span className="mono" style={{ color: 'var(--text-primary)', fontSize: 12 }}>{a.obj}</span></p>
                  <time>{a.at}</time>
                </div>
              ))}
            </div>
          </div>
        </div>
        <div className="stack">
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
          <div className="card">
            <div className="card-head">
              <div className="left"><h3 className="card-title">Compute</h3><span className="mono" style={{ fontSize: 12.5 }}>gym-default</span></div>
              <StatusPill status="success">6 / 6 Nodes</StatusPill>
            </div>
            <div className="card-body">
              <div className="util">
                {NODES.map((nd, i) => [
                  <span key={nd} className="n">{nd}</span>,
                  ...UTIL[i].map((u, j) => <span key={`${nd}-${j}`} className="c" title={`${nd} · ${(u * 100).toFixed(0)}%`} style={{ background: `var(--heat-${u >= 0.8 ? 5 : u >= 0.6 ? 4 : u >= 0.4 ? 3 : u >= 0.2 ? 2 : 1})` }} />),
                  <span key={`${nd}-p`} className="p">{(UTIL[i][11] * 100).toFixed(0)}%</span>,
                ])}
              </div>
            </div>
            <div className="caption" style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '8px 16px 12px', borderTop: '1px solid var(--border-row)', fontSize: 12 }}>
              <span style={{ whiteSpace: 'nowrap' }}>Utilization · 5m buckets · last 60m</span>
              <span className="ramp" style={{ marginLeft: 'auto' }}>{[1, 2, 3, 4, 5].map((i) => <i key={i} style={{ background: `var(--heat-${i})` }} />)}</span>
              <span className="mono" style={{ fontSize: 11, whiteSpace: 'nowrap' }}>0–100%</span>
            </div>
          </div>
        </div>
      </div>

      <div className="card">
        <div className="card-head">
          <div className="left"><h3 className="card-title">Lineage</h3><span className="micro-label">Enterprise Support Ticket Classifier</span></div>
          <button className="btn sm" onClick={() => go('registry')}>Open Registry →</button>
        </div>
        <Graph width={860} height={172} nodeW={176} nodeH={64}
          nodes={[
            { id: 'ds', x: 20, y: 20, title: 'zendesk_tickets_q3', sub: '124,500 rows × 9 cols' },
            { id: 'ds2', x: 20, y: 96, title: 'label_taxonomy_v4', sub: 'K = 6 classes' },
            { id: 'pre', x: 236, y: 54, title: 'TF-IDF', sub: 'ngram=(1,2) · min_df=3' },
            { id: 'run', x: 452, y: 20, title: 'run_894a', sub: 'F1: 0.914 ± 0.006' },
            { id: 'run2', x: 452, y: 96, title: 'run_8a10', sub: 'Training · fold 3 of 5', active: true },
            { id: 'ver', x: 668, y: 20, title: 'v3.0.0-rc1', sub: 'Staging · awaiting approval' },
          ]}
          edges={[{ from: 'ds', to: 'pre' }, { from: 'ds2', to: 'pre' }, { from: 'pre', to: 'run' }, { from: 'pre', to: 'run2', kind: 'active' }, { from: 'run', to: 'ver', kind: 'warn' }]} />
      </div>
    </div>
  )
}

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
    </table></div>
  )
}
