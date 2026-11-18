import type { Icon } from '@/shared/primitives/Icon'

export type Route = 'home' | 'quick-train' | 'autopilot' | 'gym' | 'experiments' | 'registry' | 'settings'

export const NAV: { id: Route | 'vela'; label: string; icon: keyof typeof Icon }[][] = [
  [{ id: 'home', label: 'Home', icon: 'home' }, { id: 'gym', label: 'Training Gym', icon: 'gym' }, { id: 'experiments', label: 'Experiments', icon: 'experiments' }],
  [{ id: 'registry', label: 'Model Registry', icon: 'registry' }],
  [{ id: 'vela', label: 'Vela AI Assistant', icon: 'vela' }, { id: 'settings', label: 'Settings', icon: 'settings' }],
]

export const TITLES: Record<Route, string> = { home: 'Home', 'quick-train': 'New Model', autopilot: 'Vela Autopilot', gym: 'Training Gym', experiments: 'Experiments', registry: 'Model Registry', settings: 'Settings' }

/** Pages that manage their own scrolling (pinned header and footer). */
export const FULL_HEIGHT: Route[] = ['gym', 'quick-train', 'autopilot']

export const readRoute = (): Route => { const h = location.hash.replace('#/', '').split('?')[0] as Route; return h in TITLES ? h : 'home' }
