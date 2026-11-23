import { useEffect, useState } from 'react'
import { BuildPage } from '@/features/build/BuildPage'
import { VelaPage } from '@/features/vela/VelaPage'
import { ModelsPage } from '@/features/models/ModelsPage'
import { AnomalyPage } from '@/features/anomaly/AnomalyPage'
import { ForecastPage } from '@/features/forecast/ForecastPage'
import { ExpectedLossPage } from '@/features/expected-loss/ExpectedLossPage'
import { SegmentedPage } from '@/features/segmented/SegmentedPage'
import { RejectInferencePage } from '@/features/reject-inference/RejectInferencePage'
import { SurvivalPage } from '@/features/survival/SurvivalPage'
import { MultilabelPage } from '@/features/multilabel/MultilabelPage'
import { ImportPage } from '@/features/import/ImportPage'
import { HomePage } from '@/features/home/HomePage'
import { QuickTrainPage } from '@/features/quick-train/QuickTrainPage'
import { LiveRegistry } from '@/features/registry/LiveRegistry'
import { SettingsPage } from '@/features/settings/SettingsPage'
import { LiveGym } from '@/features/training-gym/live/LiveGym'
import { EMPTY_GYM, type GymState, applyChanges } from '@/features/training-gym/live/body'
import type { VelaFinding } from '@/shared/api/types'
import { VelaSidecar } from '@/features/vela/VelaSidecar'
import { ApiProvider } from '@/shared/api/mode'
import { ApiOnly } from '@/shared/components/Status'
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
  useEffect(() => { document.title = `Project ENSEMBLE · ${TITLES[route]}` }, [route])

  useEffect(() => { const f = () => setRoute(readRoute()); addEventListener('hashchange', f); return () => removeEventListener('hashchange', f) }, [])
  useEffect(() => {
    const k = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); setPalette((v) => !v) }
      if (e.key === 'Escape') setPalette(false)
    }
    addEventListener('keydown', k); return () => removeEventListener('keydown', k)
  }, [])
  const go = (r: string) => { location.hash = `#/${r}` }

  return (
    <ApiProvider>
    <PrefsContext.Provider value={{ prefs, set: setPref }}>
    <div className={`frame layout-${prefs.layout.toLowerCase()} density-${prefs.density.toLowerCase()} ${prefs.motion === 'Still' ? 'still' : ''}`}>
    <div className={`shell ${collapsed ? 'collapsed' : ''}`}>
      <Sidebar route={route} collapsed={collapsed} go={go} onToggle={() => setCollapsed(!collapsed)} />

      <div className="main">
        <ContextBar route={route} velaOpen={vela} onPalette={() => setPalette(true)} onVela={() => setVela(!vela)} />
        <main className="workspace" style={FULL_HEIGHT.includes(route) ? { overflow: 'hidden' } : undefined}>
          {route === 'settings' ? <SettingsPage /> : <ApiOnly>
            {route === 'home' && <HomePage go={go} openPalette={() => setPalette(true)} />}
            {route === 'build' && <BuildPage go={go} />}
            {route === 'quick-train' && <QuickTrainPage />}
            {route === 'vela' && <VelaPage go={go} />}
            {route === 'gym' && <LiveGym gym={gym} setGym={setGym} guided={guided} setGuided={setGuided} go={go} />}
            {route === 'models' && <ModelsPage go={go} onApplyPatch={(rid, p) => applyVela(rid, { patch: p } as unknown as VelaFinding)} />}
            {route === 'registry' && <LiveRegistry go={go} />}
            {route === 'forecast' && <ForecastPage go={go} />}
            {route === 'anomaly' && <AnomalyPage />}
            {route === 'import' && <ImportPage />}
            {route === 'expected-loss' && <ExpectedLossPage go={go} />}
            {route === 'segmented' && <SegmentedPage go={go} />}
            {route === 'survival' && <SurvivalPage go={go} />}
            {route === 'reject-inference' && <RejectInferencePage go={go} />}
            {route === 'multilabel' && <MultilabelPage go={go} />}
          </ApiOnly>}
        </main>
      </div>

      <VelaSidecar open={vela} onClose={() => setVela(false)}
        preferredRun={route === 'gym' ? gym.fromRun : null} appliedKey={gym.patch && gym.fromRun ? `${gym.fromRun}:${gym.patch.id}` : null} onApplyLive={applyVela} />
    </div>
    <StatusBar />
    {palette && <CommandPalette onClose={() => setPalette(false)} go={go} />}
    </div>
    </PrefsContext.Provider>
    </ApiProvider>
  )
}
