/** The "start from a template" offer the engine puts beside a drafted plan (`spec.template_suggestion`, DESIGN-STAGE8-REUSE section 9.4). */
export interface TemplateSuggestion {
  slug: string
  name: string
  version: string
  summary: string
  reason: string
  score: number
}

/** Read the suggestion off a proposal's spec; anything that is not a complete suggestion is no suggestion. */
export function suggestionOf(spec: unknown): TemplateSuggestion | null {
  const s = (spec as { template_suggestion?: unknown } | null | undefined)?.template_suggestion as Record<string, unknown> | undefined
  if (!s || typeof s !== 'object' || s.kind !== 'template') return null
  const text = (v: unknown) => (typeof v === 'string' ? v : '')
  if (!text(s.slug) || !text(s.name)) return null
  return { slug: text(s.slug), name: text(s.name), version: text(s.version), summary: text(s.summary), reason: text(s.reason), score: typeof s.score === 'number' ? s.score : 0 }
}
