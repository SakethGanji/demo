import { useEffect, useState } from 'react'
import { BuildPage } from '@/features/build/BuildPage'
import { AutopilotPage } from '@/features/autopilot/AutopilotPage'
import { ExperimentsPage } from '@/features/experiments/ExperimentsPage'
import { AnomalyPage } from '@/features/anomaly/AnomalyPage'
import { ExplainPage } from '@/features/explain/ExplainPage'
import { ForecastPage } from '@/features/forecast/ForecastPage'
import { ExpectedLossPage } from '@/features/expected-loss/ExpectedLossPage'
import { SegmentedPage } from '@/features/segmented/SegmentedPage'
import { RejectInferencePage } from '@/features/reject-inference/RejectInferencePage'
import { SurvivalPage } from '@/features/survival/SurvivalPage'
import { ImportPage } from '@/features/import/ImportPage'
import { HomePage } from '@/features/home/HomePage'
import { QuickTrainPage } from '@/features/quick-train/QuickTrainPage'
import { RegistryPage } from '@/features/registry/RegistryPage'
import { SettingsPage } from '@/features/settings/SettingsPage'
import { TrainingGymPage } from '@/features/training-gym/TrainingGymPage'
import { DEFAULTS, type Params } from '@/features/training-gym/params'
import { EMPTY_GYM, type GymState, applyChanges } from '@/features/training-gym/live/body'
import type { VelaFinding } from '@/shared/api/types'
import { VelaSidecar } from '@/features/vela/VelaSidecar'
import type { Patch } from '@/features/vela/components/PatchCard'
import { ApiProvider } from '@/shared/api/mode'
import { PrototypeBanner } from '@/shared/components/PrototypeBanner'
import { MEAN_RECALL } from '@/shared/data/runs'
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
  const [vela, setVela] = useState(prefs.velaOpen === 'Open' && window.innerWidth >= 1600)
  const [guided, setGuided] = useState(prefs.mode === 'Guided')
  useEffect(() => setGuided(prefs.mode === 'Guided'), [prefs.mode])
  const [params, setParams] = useState<Params>(DEFAULTS)
  // The live Gym's config (the exact POST /api/runs body), kept here so Vela's patches can change it from any page.
  const [gym, setGym] = useState<GymState>(EMPTY_GYM)
  const applyVela = (runId: string, f: VelaFinding) => {
    setGym((g) => ({
      body: g.body && g.fromRun === runId ? applyChanges(g.body, f.patch.changes) : (f.patch.body as GymState['body']),
      fromRun: runId, fromName: null, patch: { id: f.patch.id, title: f.patch.title, changes: f.patch.changes },
    }))
    location.hash = '#/gym'
  }
  const [palette, setPalette] = useState(false)
  const [approved, setApproved] = useState(false) // kept here so the Registry remembers it while you look at other pages
  useEffect(() => { document.title = `Project ENSEMBLE · ${TITLES[route]}` }, [route])

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
      finding: <>Recall on Security is <b>0.84</b> (lower than <b>{MEAN_RECALL.toFixed(2)}</b> average), often confused with Technical. Weighting classes by inverse frequency should lift it.</>,
      from: "class_weight=None", to: "class_weight='balanced'",
      apply: () => setParams((p) => ({ ...p, classWeight: 'balanced' })), applied: params.classWeight === 'balanced',
    },
    {
      id: 'c', title: 'Tune Regularization',
      finding: <>Validation F1 is still rising at <span className="math">C</span> = 1.0. At <span className="math">C</span> = 2.0 you gain <b>Δ +0.004</b> macro F1 for 1.3× fit time.</>,
      from: 'C=1.0', to: 'C=2.0',
      apply: () => setParams((p) => ({ ...p, C: 2 })), applied: params.C === 2,
    },
  ]

  return (
    <ApiProvider>
    <PrefsContext.Provider value={{ prefs, set: setPref }}>
    <div className={`frame layout-${prefs.layout.toLowerCase()} density-${prefs.density.toLowerCase()} ${prefs.motion === 'Still' ? 'still' : ''}`}>
    <div className={`shell ${collapsed ? 'collapsed' : ''}`}>
      <Sidebar route={route} collapsed={collapsed} velaOpen={vela} go={go} onToggle={() => setCollapsed(!collapsed)} />

      <div className="main">
        <ContextBar route={route} velaOpen={vela} onPalette={() => setPalette(true)} onVela={() => setVela(!vela)} />
        <PrototypeBanner route={route} />
        <main className="workspace" style={FULL_HEIGHT.includes(route) ? { overflow: 'hidden' } : undefined}>
          {route === 'home' && <HomePage go={go} openPalette={() => setPalette(true)} />}
          {route === 'build' && <BuildPage go={go} />}
          {route === 'quick-train' && <QuickTrainPage go={go} />}
          {route === 'autopilot' && <AutopilotPage go={go} />}
          {route === 'gym' && <TrainingGymPage params={params} setParams={setParams} guided={guided} setGuided={setGuided} onStart={() => go('quick-train?start')} onAutopilot={() => go('autopilot')} gym={gym} setGym={setGym} go={go} />}
          {route === 'experiments' && <ExperimentsPage go={go} />}
          {route === 'explain' && <ExplainPage go={go} onApplyPatch={(rid, p) => applyVela(rid, { patch: p } as unknown as VelaFinding)} />}
          {route === 'registry' && <RegistryPage go={go} approved={approved} onApprove={() => setApproved(true)} />}
          {route === 'settings' && <SettingsPage />}
          {route === 'forecast' && <ForecastPage go={go} />}
          {route === 'anomaly' && <AnomalyPage go={go} />}
          {route === 'import' && <ImportPage go={go} />}
          {route === 'expected-loss' && <ExpectedLossPage go={go} />}
          {route === 'segmented' && <SegmentedPage go={go} />}
          {route === 'survival' && <SurvivalPage go={go} />}
          {route === 'reject-inference' && <RejectInferencePage go={go} />}
        </main>
      </div>

      <VelaSidecar open={vela} context={route === 'gym' ? 'Training Gym · Step 07' : TITLES[route]} patches={patches} onClose={() => setVela(false)}
        preferredRun={route === 'gym' ? gym.fromRun : null} appliedKey={gym.patch && gym.fromRun ? `${gym.fromRun}:${gym.patch.id}` : null} onApplyLive={applyVela} />
    </div>
    <StatusBar params={params} />
    {palette && <CommandPalette onClose={() => setPalette(false)} go={go} />}
    </div>
    </PrefsContext.Provider>
    </ApiProvider>
  )
}
