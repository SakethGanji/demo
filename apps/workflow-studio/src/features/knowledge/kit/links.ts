/** Deep links into the Knowledge page (PLAN-CAPGRAPH-UI §1.2). Links name node KEYS; the page maps a key to an id. */
export type KnowledgeTab = 'graph' | 'matrix' | 'memory'
export interface KnowledgeSearch {
  tab?: KnowledgeTab
  /** node KEY, e.g. `concept:approval` */
  focus?: string
  depth?: 1 | 2
}

/** Link props for `<Link {...knowledgeLink(slug, {focus:'concept:approval'})}>`. */
export function knowledgeLink(
  projectSlug: string,
  s: KnowledgeSearch = {},
): { to: '/p/$project/knowledge'; params: { project: string }; search: KnowledgeSearch } {
  const search: KnowledgeSearch = {}
  if (s.tab && s.tab !== 'graph') search.tab = s.tab
  if (s.focus) search.focus = s.focus
  if (s.depth === 2) search.depth = 2
  return { to: '/p/$project/knowledge', params: { project: projectSlug }, search }
}
