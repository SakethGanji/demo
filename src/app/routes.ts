import type { Icon } from '@/shared/primitives/Icon'
import { tabOfSub } from '@/features/models/tabs'

export type Route = 'home' | 'build' | 'quick-train' | 'vela' | 'gym' | 'models' | 'registry' | 'settings' | 'forecast' | 'anomaly' | 'import' | 'expected-loss' | 'segmented' | 'survival' | 'reject-inference' | 'multilabel'

/** The five places (Settings is the icon button in the sidebar footer). */
export const NAV: { id: Route; label: string; icon: keyof typeof Icon }[] = [
  { id: 'home', label: 'Home', icon: 'home' }, { id: 'build', label: 'Build', icon: 'model' }, { id: 'models', label: 'Models', icon: 'experiments' },
  { id: 'registry', label: 'Registry', icon: 'registry' }, { id: 'vela', label: 'Vela', icon: 'vela' },
]

export const TITLES: Record<Route, string> = { home: 'Home', build: 'Build', 'quick-train': 'New Model', vela: 'Vela', gym: 'Advanced · Training Gym', models: 'Models', registry: 'Registry', settings: 'Settings', forecast: 'New Forecast', anomaly: 'Anomaly Detection', import: 'Import Predictor', 'expected-loss': 'Expected Loss', segmented: 'Segmented Models', survival: 'Time to Event', 'reject-inference': 'Reject Inference', multilabel: 'Multi-Label Classifier' }

/** G0: the flows Build opens (New Model, Forecast, Anomaly, Import, and the Gym as its Advanced switch) light up Build. */
export const NAV_PARENT: Partial<Record<Route, Route>> = { 'quick-train': 'build', forecast: 'build', anomaly: 'build', import: 'build', gym: 'build', 'expected-loss': 'build', segmented: 'build', survival: 'build', 'reject-inference': 'build', multilabel: 'build' }

/** Pages that manage their own scrolling (pinned header and footer). */
export const FULL_HEIGHT: Route[] = ['gym', 'quick-train', 'vela']

const withQ = (to: string, q: string) => (q ? `${to}${to.includes('?') ? '&' : '?'}${q}` : to)
/** #/explain?run=X&tab=Shift → the run page's Doctor → Shift (old Explain tab names map to the tab that holds them now). */
const explainTo = (q: string) => {
  const p = new URLSearchParams(q)
  const to = tabOfSub(p.get('tab'))
  p.delete('tab')
  const run = p.get('run'); p.delete('run')
  const rest = p.toString()
  return run ? `models?run=${run}&tab=${to?.tab ?? 'Explain'}${to ? `&sub=${encodeURIComponent(to.sub)}` : ''}${rest ? `&${rest}` : ''}` : 'models'
}
/** A per-kind ?run= opens the run in Models; without it the route is the kind's setup (a Build choice). */
const runTo = (q: string) => (new URLSearchParams(q).get('run') ? withQ('models', q) : null)
/** Old routes that live on as one-line redirects (G10: Autopilot is Vela's "run it end to end" mode; G11: Experiments is
 *  Models, Explain is a run page tab, and every run opens in Models). */
const REDIRECTS: Record<string, (q: string) => string | null> = {
  autopilot: (q) => withQ('vela?mode=auto', q),
  experiments: (q) => withQ('models', q),
  explain: explainTo,
  ...Object.fromEntries(['forecast', 'anomaly', 'import', 'expected-loss', 'segmented', 'survival', 'reject-inference', 'multilabel'].map((r) => [r, runTo])),
}

export const readRoute = (): Route => {
  const [h, q = ''] = location.hash.replace('#/', '').split('?')
  const to = REDIRECTS[h]?.(q)
  if (to) { location.replace(`#/${to}`); return to.split('?')[0] as Route } // fires hashchange, so ?param readers update
  return h in TITLES ? (h as Route) : 'home'
}
