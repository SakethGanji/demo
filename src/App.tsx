import { useEffect, useMemo, useState } from 'react'
import wordmark from './assets/ensemble-wordmark-on-shell.svg'
import { Icon, VelaCrest } from './components/icons'
import { ToolGlyph } from './components/Strings'
import { PrefsContext, loadPrefs, savePrefs, type Prefs } from './prefs'
import { Settings } from './screens/Settings'
import { Workflow } from './screens/Workflow'
import { TOOLS } from './tools'
import { VelaSidecar, type Patch } from './components/Vela'
import { Experiments } from './screens/Experiments'
import { Home } from './screens/Home'
import { ModelLibrary, PreprocessingLibrary, Registry } from './screens/Library'
import { RUNS } from './data'
import { DEFAULTS, TrainingGym, type Params } from './screens/TrainingGym'

type Route = 'home' | 'workflow' | 'gym' | 'experiments' | 'models' | 'preprocessing' | 'registry' | 'settings'
const NAV: { id: Route | 'vela'; label: string; icon: keyof typeof Icon }[][] = [
  [{ id: 'home', label: 'Home', icon: 'home' }, { id: 'gym', label: 'Training Gym', icon: 'gym' }, { id: 'experiments', label: 'Experiments', icon: 'experiments' }],
  [{ id: 'models', label: 'Model Library', icon: 'models' }, { id: 'preprocessing', label: 'Preprocessing Library', icon: 'preprocess' }, { id: 'registry', label: 'Model Registry', icon: 'registry' }],
  [{ id: 'vela', label: 'Vela AI Assistant', icon: 'vela' }, { id: 'settings', label: 'Settings', icon: 'settings' }],
]
const TITLES: Record<Route, string> = { home: 'Home', workflow: 'Quick Train', gym: 'Training Gym', experiments: 'Experiments', models: 'Model Library', preprocessing: 'Preprocessing Library', registry: 'Model Registry', settings: 'Settings' }

const readRoute = (): Route => { const h = location.hash.replace('#/', '') as Route; return h in TITLES ? h : 'home' }

export default function App() {
  const [route, setRoute] = useState<Route>(readRoute)
  const [collapsed, setCollapsed] = useState(false)
  const [prefs, setPrefs] = useState<Prefs>(loadPrefs)
  const setPref = (p: Partial<Prefs>) => setPrefs((old) => { const n = { ...old, ...p }; savePrefs(n); return n })
  const [vela, setVela] = useState(prefs.velaOpen === 'Open')
  const [guided, setGuided] = useState(prefs.mode === 'Guided')
  useEffect(() => setGuided(prefs.mode === 'Guided'), [prefs.mode])
  const [params, setParams] = useState<Params>(DEFAULTS)
  const [palette, setPalette] = useState(false)

  useEffect(() => { const f = () => setRoute(readRoute()); addEventListener('hashchange', f); return () => removeEventListener('hashchange', f) }, [])
  useEffect(() => {
    const k = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); setPalette((v) => !v) }
      if (e.key === 'Escape') setPalette(false)
    }
    addEventListener('keydown', k); return () => removeEventListener('keydown', k)
  }, [])
  const go = (r: string) => { if (r === 'vela') setVela(true); else location.hash = `#/${r}` }

  const patches: Patch[] = [
    {
      id: 'cw', title: 'Rebalance Classes',
      finding: <>Recall on Security is <b>0.84</b> (lower than <b>0.94</b> average), often confused with Technical. Weighting classes by inverse frequency should lift it.</>,
      from: "class_weight=None", to: "class_weight='balanced'",
      apply: () => setParams((p) => ({ ...p, classWeight: 'balanced' })),
    },
    {
      id: 'c', title: 'Tune Regularization',
      finding: <>Validation F1 is still rising at <span className="math">C</span> = 1.0. At <span className="math">C</span> = 2.0 you gain <b>Δ +0.004</b> macro F1 for 1.3× fit time.</>,
      from: 'C=1.0', to: 'C=2.0',
      apply: () => setParams((p) => ({ ...p, C: 2 })),
    },
  ]

  return (
    <PrefsContext.Provider value={{ prefs, set: setPref }}>
    <div className={`frame density-${prefs.density.toLowerCase()} ${prefs.motion === 'Still' ? 'still' : ''}`}>
    <div className={`shell ${collapsed ? 'collapsed' : ''}`}>
      <nav className="sidebar" aria-label="Primary">
        <button className="lockup" onClick={() => go('home')} aria-label="Project ENSEMBLE home">
          <span className="eyebrow">PROJECT</span>
          <img className="mark" src={wordmark} alt="ENSEMBLE" height={26} />
          <span className="word">ENSEMBLE</span>
        </button>
        <div className="nav">
          {NAV.map((g, gi) => (
            <div key={gi} className="nav-group">
              {g.map((n) => {
                const I = Icon[n.icon]
                const active = n.id === 'vela' ? false : route === n.id
                return (
                  <button key={n.id} className={`nav-item ${active ? 'active' : ''}`} title={n.label} aria-current={active ? 'page' : undefined}
                    onClick={() => (n.id === 'vela' ? setVela(true) : go(n.id))}>
                    <I /><span>{n.label}</span>{n.id === 'vela' && vela && !collapsed && <i className="vela-tag" />}
                  </button>
                )
              })}
            </div>
          ))}
          {prefs.pinned.length > 0 && (
            <div className="nav-group belt">
              <div className="belt-label">Toolbelt</div>
              {TOOLS.filter((t) => prefs.pinned.includes(t.id)).map((t) => (
                <button key={t.id} className="nav-item belt-item" title={`${t.verb} · ${t.name}`} onClick={() => go(t.route)}>
                  <span className="belt-g"><ToolGlyph id={t.id} /></span><span>{t.verb}<small>{t.name}</small></span>
                </button>
              ))}
            </div>
          )}
        </div>
        <div className="sidebar-footer">
          <div className="avatar">ML</div>
          <div className="who"><b>Maya Lin</b><small>Lead ML Engineer</small></div>
          <span className="role-pill">Admin</span>
          <button className="icon-btn" aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'} onClick={() => setCollapsed(!collapsed)}><Icon.collapse width={16} height={16} /></button>
        </div>
      </nav>

      <div className="main">
        <header className="context-bar">
          <div className="crumbs">
            <span>ENSEMBLE</span><span className="sep">/</span>
            <span>{TITLES[route]}</span>
            {route === 'gym' && <><span className="sep">/</span><b>Enterprise Support Ticket Classifier</b></>}
          </div>
          <button className="cmd" onClick={() => setPalette(true)}><Icon.search width={14} height={14} /> Search runs, models, datasets<kbd>⌘K</kbd></button>
          <span className="cluster" title="Attached compute"><span className="g">●</span>gym-default · cpu-8x</span>
          <button className={`btn vela ${vela ? 'on' : ''}`} onClick={() => setVela(!vela)}><VelaCrest size={16} /> Ask Vela</button>
        </header>
        <main className="workspace" style={route === 'gym' || route === 'workflow' ? { overflow: 'hidden' } : undefined}>
          {route === 'home' && <Home go={go} openPalette={() => setPalette(true)} />}
          {route === 'gym' && <TrainingGym params={params} setParams={setParams} guided={guided} setGuided={setGuided} onStart={() => go('workflow')} />}
          {route === 'workflow' && <Workflow go={go} />}
          {route === 'experiments' && <Experiments />}
          {route === 'models' && <ModelLibrary go={go} />}
          {route === 'preprocessing' && <PreprocessingLibrary />}
          {route === 'registry' && <Registry />}
          {route === 'settings' && <Settings />}
        </main>
      </div>

      {vela && <VelaSidecar context={route === 'gym' ? 'Training Gym · Step 07' : TITLES[route]} patches={patches} onClose={() => setVela(false)} />}
    </div>
    <footer className="statusbar">
      <span className="ok">● gym-default</span><span>6 / 6 nodes</span><span className="sb-sep" />
      <span className="live"><i className="live-dot" style={{ width: 6, height: 6, borderRadius: 999, background: 'currentColor' }} />run_8a10 training · fold 3 of 5</span>
      <span>1 queued</span><span className="sb-sep" />
      <span>C=<b>{Number.isInteger(params.C) ? params.C.toFixed(1) : params.C}</b> · class_weight=<b>{params.classWeight === 'None' ? 'None' : "'balanced'"}</b></span>
      <span className="spacer" />
      <span>Draft saved 12s ago</span><span className="sb-sep" /><span>us-east-1</span><span className="sb-sep" /><span>v3.0.0-rc1</span><span className="sb-sep" /><span>ENSEMBLE 2.6.1</span>
    </footer>
    {palette && <Palette onClose={() => setPalette(false)} go={go} />}
    </div>
    </PrefsContext.Provider>
  )
}

function Palette({ onClose, go }: { onClose: () => void; go: (r: string) => void }) {
  const [q, setQ] = useState('')
  const [idx, setIdx] = useState(0)
  const items = useMemo(() => {
    const all: { group: string; label: string; hint: string; mono?: boolean; run: () => void }[] = [
      ...(Object.keys(TITLES) as Route[]).map((r) => ({ group: 'Go To', label: TITLES[r], hint: 'Page', run: () => go(r) })),
      ...TOOLS.map((t) => ({ group: 'Tools', label: `${t.verb} · ${t.name}`, hint: t.meta, run: () => go(t.route) })),
      ...RUNS.map((r) => ({ group: 'Runs', label: r.id, hint: `${r.pipeline} · ${r.status}`, mono: true, run: () => go('experiments') })),
    ]
    return all.filter((i) => `${i.label} ${i.hint}`.toLowerCase().includes(q.toLowerCase()))
  }, [q])
  const pick = (i: number) => { items[i]?.run(); onClose() }
  return (
    <div className="palette-scrim" onClick={onClose}>
      <div className="palette" role="dialog" aria-label="Command palette" onClick={(e) => e.stopPropagation()}>
        <div className="palette-input">
          <Icon.search width={16} height={16} />
          <input autoFocus placeholder="Search runs, models, datasets, or jump to a page" value={q}
            onChange={(e) => { setQ(e.target.value); setIdx(0) }}
            onKeyDown={(e) => {
              if (e.key === 'ArrowDown') { e.preventDefault(); setIdx((i) => Math.min(items.length - 1, i + 1)) }
              if (e.key === 'ArrowUp') { e.preventDefault(); setIdx((i) => Math.max(0, i - 1)) }
              if (e.key === 'Enter') pick(idx)
            }} />
          <kbd className="mono" style={{ fontSize: 11 }}>esc</kbd>
        </div>
        <div className="palette-list">
          {items.length === 0 && <p className="helper" style={{ padding: 16 }}>No matches for “{q}”.</p>}
          {items.map((it, i) => (
            <div key={`${it.group}-${it.label}`}>
              {(i === 0 || items[i - 1].group !== it.group) && <div className="palette-group micro-label">{it.group}</div>}
              <button className={`palette-item ${i === idx ? 'on' : ''}`} onMouseEnter={() => setIdx(i)} onClick={() => pick(i)}>
                <span className={it.mono ? 'mono' : ''} style={it.mono ? { fontSize: 12.5 } : undefined}>{it.label}</span><span className="hint">{it.hint}</span>
              </button>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
