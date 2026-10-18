/** Plain-English phrasing of graph relations (PLAN-CAPGRAPH-UI §2.2). Pure; no relation name reaches a screen. */

export type Direction = 'out' | 'in'
const dir = (d: string): Direction => (d === 'in' ? 'in' : 'out')

const PHRASE: Record<string, { out: string; in: string }> = {
  is_a: { out: 'is a kind of', in: 'has the kind' },
  requires: { out: 'needs', in: 'is needed by' },
  implements: { out: 'implements', in: 'is implemented by' },
  requires_permission: { out: 'needs the role', in: 'is the role for' },
  composes: { out: 'is built from', in: 'is part of' },
  produces: { out: 'produces', in: 'is produced by' },
  consumes: { out: 'consumes', in: 'is consumed by' },
  escalates_to: { out: 'escalates to', in: 'is escalated from' },
  renders_with: { out: 'is shown with', in: 'shows' },
  validated_by: { out: 'is validated by', in: 'validates' },
  derived_from: { out: 'is derived from', in: 'is the source of' },
}

/** "approver_role" -> "approver role" */
export const words = (s: string) => s.replaceAll('_', ' ').trim()

/** "a" or "an" for the word that follows. */
export function article(next: string): 'a' | 'an' {
  return /^[aeiou]/i.test(next.trim()) ? 'an' : 'a'
}

const slotOf = (props?: Record<string, unknown>) => (typeof props?.slot === 'string' && props.slot ? words(props.slot) : '')

/** The short phrase on an edge label: "needs approver role", "implemented by". */
export function phrase(rel: string, direction: string, props?: Record<string, unknown>): string {
  const d = dir(direction)
  if (rel === 'requires_permission') {
    const slot = slotOf(props)
    if (d === 'out') return slot ? `needs ${slot}` : 'needs the role'
    return slot ? `is the ${slot} of` : 'is the role for'
  }
  if (rel === 'escalates_to' && typeof props?.after === 'string' && props.after && d === 'out') return `escalates to, after ${props.after}`
  return PHRASE[rel]?.[d] ?? words(rel)
}

/**
 * A whole sentence for one edge, read src -> dst: "Approval needs an approver role: Ops manager".
 * `rel` is the stored relation (direction src -> dst).
 */
export function sentence(rel: string, src: { label: string }, dst: { label: string }, props?: Record<string, unknown>): string {
  if (rel === 'requires_permission') {
    const slot = slotOf(props)
    return slot ? `${src.label} needs ${article(slot)} ${slot}: ${dst.label}` : `${src.label} needs the role ${dst.label}`
  }
  return `${src.label} ${phrase(rel, 'out', props)} ${dst.label}`
}

/** A heading in the node sheet for one relation group. */
export function sectionTitle(rel: string, direction: string): string {
  const d = dir(direction)
  switch (rel) {
    case 'implements': return d === 'in' ? 'Implemented by' : 'Implements'
    case 'composes': return d === 'in' ? 'Used in' : 'Built from'
    case 'requires_permission': return d === 'out' ? 'Requires roles' : 'Role for'
    case 'validated_by': return d === 'out' ? 'Validated by' : 'Validates'
    case 'derived_from': return d === 'out' ? 'Derived from' : 'Source of'
    case 'requires': return d === 'out' ? 'Needs' : 'Needed by'
    case 'is_a': return d === 'out' ? 'A kind of' : 'Kinds'
    case 'produces': return d === 'out' ? 'Produces' : 'Produced by'
    case 'consumes': return d === 'out' ? 'Consumes' : 'Consumed by'
    case 'escalates_to': return d === 'out' ? 'Escalates to' : 'Escalated from'
    case 'renders_with': return d === 'out' ? 'Shown with' : 'Shows'
    default: return words(rel)
  }
}

/** The request pre-filled in the Build tab for "Build with this". */
export function buildPrompt(concept: { label: string; definition?: string | null }): string {
  const def = concept.definition?.trim()
  return def ? `Build a workflow for ${concept.label}. ${def}` : `Build a workflow for ${concept.label}.`
}

/** "approval" from "concept:approval" (used in test ids and links). */
export const slugOf = (key: string) => key.replace(/^[a-z_]+:/, '')

/** "exception_case" -> "Exception case" (a fallback label when a key is all we have). */
export function labelOfKey(key: string): string {
  const w = words(slugOf(key))
  return w ? w[0].toUpperCase() + w.slice(1) : key
}

/** "3 patterns", "1 pattern" */
export const count = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`
