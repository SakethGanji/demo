import { createContext, useContext } from 'react'
export type Prefs = {
  density: 'Compact' | 'Comfortable'
  motion: 'Live' | 'Still'
  tension: 'Loose' | 'Taut' | 'Tight'
  mode: 'Guided' | 'Advanced'
  velaOpen: 'Open' | 'Closed'
  layout: 'Inset' | 'Flush'
}
export const DEFAULT_PREFS: Prefs = { density: 'Compact', motion: 'Live', tension: 'Taut', mode: 'Guided', velaOpen: 'Open', layout: 'Inset' }

export function loadPrefs(): Prefs {
  try {
    const { pinned: _, ...p } = { ...DEFAULT_PREFS, ...JSON.parse(localStorage.getItem('ensemble.prefs') ?? '{}') } // pinned: the old toolbelt
    return p as Prefs
  } catch { return DEFAULT_PREFS }
}
export function savePrefs(p: Prefs) { try { localStorage.setItem('ensemble.prefs', JSON.stringify(p)) } catch { /* private mode */ } }

export const PrefsContext = createContext<{ prefs: Prefs; set: (p: Partial<Prefs>) => void }>({ prefs: DEFAULT_PREFS, set: () => {} })
export const usePrefs = () => useContext(PrefsContext)

/** Amplitude multiplier and decay (seconds) for a plucked string at each tension. */
export const TENSION = { Loose: { amp: 1.7, decay: 1.5, freq: 3.2 }, Taut: { amp: 1, decay: 0.9, freq: 5 }, Tight: { amp: 0.55, decay: 0.5, freq: 8 } }
export const reducedMotion = () => typeof matchMedia !== 'undefined' && matchMedia('(prefers-reduced-motion: reduce)').matches
