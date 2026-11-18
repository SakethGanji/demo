import { useEffect, useState } from 'react'
import wordmark from '@/shared/assets/ensemble-wordmark-on-shell.svg'
import { LineChart, type Series } from '@/shared/components/LineChart'
import { PlotFrame } from '@/shared/components/PlotFrame'
import { RunString, type RunState } from '@/shared/components/RunString'
import { StringField } from '@/shared/components/StringField'
import { LOG_LINES } from '@/shared/data/runs'
import { usePrefs } from '@/shared/lib/prefs'
import { curve } from '@/shared/lib/random'
import { TOOLS, type Tool } from '@/shared/lib/tools'
import { Icon } from '@/shared/primitives/Icon'
import { ParamChip } from '@/shared/primitives/ParamChip'
import { ResonanceWave } from '@/shared/primitives/ResonanceWave'
import { StatusPill } from '@/shared/primitives/StatusPill'
import { ToolCard } from './components/ToolCard'

const TRAIN = curve(40, 1.2, 0.24, 0.12, 0.03, 7)

const VAL = curve(40, 1.25, 0.29, 0.1, 0.04, 11)

export function HomePage({ go, openPalette }: { go: (r: string) => void; openPalette: () => void }) {
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
            <img src={wordmark} alt="ENSEMBLE" height={36} />
          </div>
          <div className="hero-read">
            <span><b>1</b> run on the strings</span><span><b>v2.4.1</b> in Production</span><span>best F1: <b>0.914 ± 0.006</b></span>
          </div>
        </div>
        <StringField height={104} />
        <div className="hero-bottom">
          <div>
            <h1 className="hero-title">Many models, one framework.</h1>
            <p className="hero-sub">A toolkit for training, comparing and shipping models. Pick up a tool, or tell the bench what you want to build.</p>
          </div>
          <div className="row" style={{ gap: 12 }}>
          <button className="btn primary" style={{ height: 40, padding: '0 16px' }} onClick={() => go('quick-train')}>Quick Train →</button>
          <button className="hero-cmd" onClick={openPalette}>
            <Icon.search width={16} height={16} /><span>Find a tool, run or model</span><kbd>⌘K</kbd>
          </button>
          </div>
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
            <LineChart series={series} xMax={40} yMin={0} yMax={1.4} yTicks={[0, 0.35, 0.7, 1.05, 1.4]} xTicks={[0, 10, 20, 30, 40]} xTitle="t" yTitle="ℒ" fmtY={(v) => v.toFixed(2)} live height={196} />
          </PlotFrame>
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
        </div>
      </div>

    </div>
  )
}
