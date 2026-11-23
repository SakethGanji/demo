/** Which table columns are hidden in the grid: client-only, remembered per table in localStorage. */
import { useCallback, useState } from 'react'

const key = (tableId: string) => `studio.data.hidden.${tableId}`

function read(tableId: string): string[] {
  try {
    const v = JSON.parse(localStorage.getItem(key(tableId)) ?? '[]')
    return Array.isArray(v) ? v.filter((x) => typeof x === 'string') : []
  } catch {
    return []
  }
}

export function useHiddenColumns(tableId: string, all: string[]) {
  const [stored, setStored] = useState<string[]>(() => read(tableId))
  // forget names that no longer exist (renamed or removed columns); never hide everything
  const hidden = stored.filter((n) => all.includes(n)).slice(0, Math.max(0, all.length - 1))
  const set = useCallback(
    (next: string[]) => {
      setStored(next)
      try {
        localStorage.setItem(key(tableId), JSON.stringify(next))
      } catch {
        /* storage blocked: the choice lasts for this visit */
      }
    },
    [tableId],
  )
  const toggle = (name: string) => {
    if (hidden.includes(name)) return set(hidden.filter((n) => n !== name))
    if (hidden.length >= all.length - 1) return
    set([...hidden, name])
  }
  return { hidden, toggle }
}
