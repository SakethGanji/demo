import { useEffect, useState } from 'react'
import { Icon, VelaCrest } from './components/icons'
import { VelaSidecar, type Patch } from './components/Vela'
import { Experiments } from './screens/Experiments'
import { Home } from './screens/Home'
import { Placeholder } from './screens/Placeholder'
import { DEFAULTS, TrainingGym, type Params } from './screens/TrainingGym'

type Route = 'home' | 'gym' | 'experiments' | 'models' | 'preprocessing' | 'registry' | 'settings'
const NAV: { id: Route | 'vela'; label: string; icon: keyof typeof Icon }[][] = [
  [{ id: 'home', label: 'Home', icon: 'home' }, { id: 'gym', label: 'Training Gym', icon: 'gym' }, { id: 'experiments', label: 'Experiments', icon: 'experiments' }],
  [{ id: 'models', label: 'Model Library', icon: 'models' }, { id: 'preprocessing', label: 'Preprocessing Library', icon: 'preprocess' }, { id: 'registry', label: 'Model Registry', icon: 'registry' }],
  [{ id: 'vela', label: 'Vela AI Assistant', icon: 'vela' }, { id: 'settings', label: 'Settings', icon: 'settings' }],
]
const TITLES: Record<Route, string> = { home: 'Home', gym: 'Training Gym', experiments: 'Experiments', models: 'Model Library', preprocessing: 'Preprocessing Library', registry: 'Model Registry', settings: 'Settings' }

const readRoute = (): Route => { const h = location.hash.replace('#/', '') as Route; return h in TITLES ? h : 'gym' }

export default function App() {
  const [route, setRoute] = useState<Route>(readRoute)
  const [collapsed, setCollapsed] = useState(false)
  const [vela, setVela] = useState(true)
  const [guided, setGuided] = useState(true)
  const [params, setParams] = useState<Params>(DEFAULTS)

  useEffect(() => { const f = () => setRoute(readRoute()); addEventListener('hashchange', f); return () => removeEventListener('hashchange', f) }, [])
  const go = (r: string) => { location.hash = `#/${r}` }

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
    <div className={`shell ${collapsed ? 'collapsed' : ''}`}>
      <nav className="sidebar" aria-label="Primary">
        <div className="lockup"><span className="eyebrow">PROJECT</span><span className="word">ENSEMBLE</span></div>
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
          <div className="cmd"><Icon.search width={14} height={14} /> Search runs, models, datasets<kbd>⌘K</kbd></div>
          <button className={`btn vela ${vela ? 'on' : ''}`} onClick={() => setVela(!vela)}><VelaCrest size={16} /> Ask Vela</button>
        </header>
        <main className="workspace" style={route === 'gym' ? { overflow: 'hidden' } : undefined}>
          {route === 'home' && <Home go={go} />}
          {route === 'gym' && <TrainingGym params={params} setParams={setParams} guided={guided} setGuided={setGuided} onStart={() => go('home')} />}
          {route === 'experiments' && <Experiments />}
          {route === 'models' && <Placeholder title="Model Library" body="Browse model families with their objectives, port signatures and default hyperparameter domains." />}
          {route === 'preprocessing' && <Placeholder title="Preprocessing Library" body="Reusable transforms with typed contracts, such as str[ ] → sparse ℝⁿˣᵈ, ready to string into a pipeline." />}
          {route === 'registry' && <Placeholder title="Model Registry" body="Approved model versions and their lineage. Register a version from Experiments to start." />}
          {route === 'settings' && <Placeholder title="Settings" body="Workspace members, compute quotas and storage connections." />}
        </main>
      </div>

      {vela && <VelaSidecar context={route === 'gym' ? 'Training Gym · Step 07' : TITLES[route]} patches={patches} onClose={() => setVela(false)} />}
    </div>
  )
}
