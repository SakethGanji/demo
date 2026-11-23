/** The Knowledge page's URL state: which lens is open, which node is selected, and (team page) the grouping. */
export const KNOWLEDGE_VIEWS = ['3d', 'graph', 'list', 'coverage', 'memory'] as const
export type KnowledgeViewKey = (typeof KNOWLEDGE_VIEWS)[number]

export interface KnowledgeSearch {
  /** absent = the default graph lens: the last one chosen, else 3D on a desktop with WebGL, else the 2D graph */
  view?: KnowledgeViewKey
  node?: string
  /** team page: "Group by project" (absent = the last choice, remembered in localStorage) */
  group?: boolean
}

/** The search field's id: the page focuses it on "/" (Input does not forward refs in React 18). */
export const SEARCH_ID = 'knowledge-search-input'
