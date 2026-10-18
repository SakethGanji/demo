import type { NodeDetail, Neighbour } from '../types'

const REL_ORDER = ['implements', 'requires_permission', 'requires', 'is_a', 'composes', 'produces', 'consumes', 'renders_with', 'validated_by', 'derived_from', 'escalates_to']

export interface Section { key: string; rel: string; direction: string; items: Neighbour[] }

export function sectionsOf(neighbours: NodeDetail['neighbours']): Section[] {
  const out: Section[] = []
  for (const [rel, list] of Object.entries(neighbours)) {
    for (const direction of ['out', 'in']) {
      const items = list.filter((n) => n.direction === direction)
      if (items.length) out.push({ key: `${rel}|${direction}`, rel, direction, items })
    }
  }
  const rank = (r: string) => { const i = REL_ORDER.indexOf(r); return i < 0 ? 99 : i }
  return out.sort((a, b) => rank(a.rel) - rank(b.rel) || (a.direction === b.direction ? 0 : a.direction === 'in' ? -1 : 1))
}
