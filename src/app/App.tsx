import { useEffect, useState } from 'react'
import { AutopilotPage } from '@/features/autopilot/AutopilotPage'
import { ExperimentsPage } from '@/features/experiments/ExperimentsPage'
import { HomePage } from '@/features/home/HomePage'
import { QuickTrainPage } from '@/features/quick-train/QuickTrainPage'
import { RegistryPage } from '@/features/registry/RegistryPage'
import { SettingsPage } from '@/features/settings/SettingsPage'
import { TrainingGymPage } from '@/features/training-gym/TrainingGymPage'
import { DEFAULTS, type Params } from '@/features/training-gym/params'
import { VelaSidecar } from '@/features/vela/VelaSidecar'
import type { Patch } from '@/features/vela/components/PatchCard'
import { PrefsContext, loadPrefs, savePrefs, type Prefs } from '@/shared/lib/prefs'
import { FULL_HEIGHT, TITLES, readRoute, type Route } from './routes'
import { CommandPalette } from './shell/CommandPalette'
import { ContextBar } from './shell/ContextBar'
import { Sidebar } from './shell/Sidebar'
import { StatusBar } from './shell/StatusBar'

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
    <div className={`frame layout-${prefs.layout.toLowerCase()} density-${prefs.density.toLowerCase()} ${prefs.motion === 'Still' ? 'still' : ''}`}>
    <div className={`shell ${collapsed ? 'collapsed' : ''}`}>
      <Sidebar route={route} collapsed={collapsed} velaOpen={vela} go={go} onToggle={() => setCollapsed(!collapsed)} />

      <div className="main">
        <ContextBar route={route} velaOpen={vela} onPalette={() => setPalette(true)} onVela={() => setVela(!vela)} />
        <main className="workspace" style={FULL_HEIGHT.includes(route) ? { overflow: 'hidden' } : undefined}>
          {route === 'home' && <HomePage go={go} openPalette={() => setPalette(true)} />}
          {route === 'quick-train' && <QuickTrainPage go={go} />}
          {route === 'autopilot' && <AutopilotPage go={go} />}
          {route === 'gym' && <TrainingGymPage params={params} setParams={setParams} guided={guided} setGuided={setGuided} onStart={() => go('quick-train')} onAutopilot={() => go('autopilot')} />}
          {route === 'experiments' && <ExperimentsPage go={go} />}
          {route === 'registry' && <RegistryPage go={go} />}
          {route === 'settings' && <SettingsPage />}
        </main>
      </div>

      {vela && <VelaSidecar context={route === 'gym' ? 'Training Gym · Step 07' : TITLES[route]} patches={patches} onClose={() => setVela(false)} />}
    </div>
    <StatusBar params={params} />
    {palette && <CommandPalette onClose={() => setPalette(false)} go={go} />}
    </div>
    </PrefsContext.Provider>
  )
}
