import type { Icon } from '@/shared/primitives/Icon'

export type Route = 'home' | 'quick-train' | 'autopilot' | 'gym' | 'experiments' | 'explain' | 'registry' | 'settings' | 'forecast' | 'anomaly' | 'import'

export const NAV: { id: Route | 'vela'; label: string; icon: keyof typeof Icon }[][] = [
  [{ id: 'home', label: 'Home', icon: 'home' }, { id: 'gym', label: 'Training Gym', icon: 'gym' }, { id: 'experiments', label: 'Experiments', icon: 'experiments' }],
  [{ id: 'forecast', label: 'New Forecast', icon: 'forecast' }, { id: 'anomaly', label: 'Anomaly Detection', icon: 'anomaly' }],
  [{ id: 'registry', label: 'Model Registry', icon: 'registry' }, { id: 'import', label: 'Import Predictor', icon: 'importer' }],
  [{ id: 'vela', label: 'Vela AI Assistant', icon: 'vela' }, { id: 'settings', label: 'Settings', icon: 'settings' }],
]

export const TITLES: Record<Route, string> = { home: 'Home', 'quick-train': 'New Model', autopilot: 'Vela Autopilot', gym: 'Training Gym', experiments: 'Experiments', explain: 'Explain', registry: 'Model Registry', settings: 'Settings', forecast: 'New Forecast', anomaly: 'Anomaly Detection', import: 'Import Predictor' }

/** Pages that manage their own scrolling (pinned header and footer). */
export const FULL_HEIGHT: Route[] = ['gym', 'quick-train', 'autopilot']

export const readRoute = (): Route => { const h = location.hash.replace('#/', '').split('?')[0] as Route; return h in TITLES ? h : 'home' }
