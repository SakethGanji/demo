import type { Icon } from '@/shared/primitives/Icon'

export type Route = 'home' | 'build' | 'quick-train' | 'vela' | 'gym' | 'experiments' | 'explain' | 'registry' | 'settings' | 'forecast' | 'anomaly' | 'import' | 'expected-loss' | 'segmented' | 'survival' | 'reject-inference' | 'multilabel'

export const NAV: { id: Route; label: string; icon: keyof typeof Icon }[][] = [
  [{ id: 'home', label: 'Home', icon: 'home' }, { id: 'build', label: 'Build', icon: 'model' }, { id: 'experiments', label: 'Experiments', icon: 'experiments' }, { id: 'explain', label: 'Explain', icon: 'explain' }],
  [{ id: 'registry', label: 'Model Registry', icon: 'registry' }, { id: 'vela', label: 'Vela', icon: 'vela' }],
  [{ id: 'settings', label: 'Settings', icon: 'settings' }],
]

export const TITLES: Record<Route, string> = { home: 'Home', build: 'Build', 'quick-train': 'New Model', vela: 'Vela', gym: 'Training Gym', experiments: 'Experiments', explain: 'Explain', registry: 'Model Registry', settings: 'Settings', forecast: 'New Forecast', anomaly: 'Anomaly Detection', import: 'Import Predictor', 'expected-loss': 'Expected Loss', segmented: 'Segmented Models', survival: 'Time to Event', 'reject-inference': 'Reject Inference', multilabel: 'Multi-Label Classifier' }

/** G0: the flows Build opens (New Model, Forecast, Anomaly, Import, and the Gym as its Advanced switch) light up Build. */
export const NAV_PARENT: Partial<Record<Route, Route>> = { 'quick-train': 'build', forecast: 'build', anomaly: 'build', import: 'build', gym: 'build', 'expected-loss': 'build', segmented: 'build', survival: 'build', 'reject-inference': 'build', multilabel: 'build' }

/** Pages that manage their own scrolling (pinned header and footer). */
export const FULL_HEIGHT: Route[] = ['gym', 'quick-train', 'vela']

/** Old routes that live on as one-line redirects (G10: Autopilot is Vela's "run it end to end" mode). */
const REDIRECTS: Record<string, (q: string) => string> = { autopilot: (q) => `vela?mode=auto${q ? `&${q}` : ''}` }

export const readRoute = (): Route => {
  const [h, q = ''] = location.hash.replace('#/', '').split('?')
  if (REDIRECTS[h]) { location.replace(`#/${REDIRECTS[h](q)}`); return 'vela' } // fires hashchange, so ?mode= readers update
  return h in TITLES ? (h as Route) : 'home'
}
