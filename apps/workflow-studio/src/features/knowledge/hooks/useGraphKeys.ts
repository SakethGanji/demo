import { useEffect } from 'react'
import { SEARCH_ID } from '../lib/search'

/** The graph lenses' keys (2D and 3D alike): "/" focuses search, "f" fits, Escape closes the detail panel. */
export function useGraphKeys(selected: string | null, select: (id: string | null) => void, fit: () => void) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null
      const typing = !!el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.isContentEditable)
      if (e.key === 'Escape' && selected && !e.defaultPrevented) {
        if (typing && (el as HTMLInputElement).value) return
        select(null)
      } else if (!typing && !e.metaKey && !e.ctrlKey && !e.altKey) {
        if (e.key === '/') {
          e.preventDefault()
          document.getElementById(SEARCH_ID)?.focus()
        } else if (e.key === 'f' || e.key === 'F') fit()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [selected, select, fit])
}
