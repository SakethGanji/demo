import type { RebuildResponse } from '../types'

export function rebuildToast(r: RebuildResponse): string {
  const parts = [`${r.added} added`, `${r.updated} updated`, `${r.staled} stale`, `${r.tagged} tagged`]
  if (r.proposed > 0) parts.push(`${r.proposed} proposed`)
  return `Knowledge rebuilt: ${parts.join(' · ')}`
}
