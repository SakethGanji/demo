import type { Icon } from '@/shared/primitives/Icon'

export type Route = 'home' | 'build' | 'quick-train' | 'autopilot' | 'gym' | 'experiments' | 'explain' | 'registry' | 'settings' | 'forecast' | 'anomaly' | 'import' | 'expected-loss' | 'segmented' | 'survival' | 'reject-inference' | 'multilabel'

export const NAV: { id: Route | 'vela'; label: string; icon: keyof typeof Icon }[][] = [
  [{ id: 'home', label: 'Home', icon: 'home' }, { id: 'build', label: 'Build', icon: 'model' }, { id: 'autopilot', label: 'Vela Autopilot', icon: 'autopilot' }, { id: 'experiments', label: 'Experiments', icon: 'experiments' }, { id: 'explain', label: 'Explain', icon: 'explain' }],
  [{ id: 'registry', label: 'Model Registry', icon: 'registry' }],
  [{ id: 'vela', label: 'Vela AI Assistant', icon: 'vela' }, { id: 'settings', label: 'Settings', icon: 'settings' }],
]

export const TITLES: Record<Route, string> = { home: 'Home', build: 'Build', 'quick-train': 'New Model', autopilot: 'Vela Autopilot', gym: 'Training Gym', experiments: 'Experiments', explain: 'Explain', registry: 'Model Registry', settings: 'Settings', forecast: 'New Forecast', anomaly: 'Anomaly Detection', import: 'Import Predictor', 'expected-loss': 'Expected Loss', segmented: 'Segmented Models', survival: 'Time to Event', 'reject-inference': 'Reject Inference', multilabel: 'Multi-Label Classifier' }

/** G0: the flows Build opens (New Model, Forecast, Anomaly, Import, and the Gym as its Advanced switch) light up Build. */
export const NAV_PARENT: Partial<Record<Route, Route>> = { 'quick-train': 'build', forecast: 'build', anomaly: 'build', import: 'build', gym: 'build', 'expected-loss': 'build', segmented: 'build', survival: 'build', 'reject-inference': 'build', multilabel: 'build' }

/** Pages that manage their own scrolling (pinned header and footer). */
export const FULL_HEIGHT: Route[] = ['gym', 'quick-train', 'autopilot']

export const readRoute = (): Route => { const h = location.hash.replace('#/', '').split('?')[0] as Route; return h in TITLES ? h : 'home' }
