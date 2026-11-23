/**
 * The scope a Knowledge page reads (one project, or the whole team) and, in team scope, who owns what: the projects
 * the graph names and each node's project. Every lens reads it here instead of threading a project id through.
 */
import { createContext, useContext } from 'react'
import type { KgProjectRef, KnowledgeScope } from '../lib/types'

export interface KnowledgeScopeValue {
  scope: KnowledgeScope
  /** true on the team-wide page: lenses show project attribution */
  team: boolean
  /** team scope: project id -> ref (empty in project scope) */
  projects: ReadonlyMap<string, KgProjectRef>
  /** the project that owns a node of the loaded graph (null: shared, or unknown) */
  projectOf: (nodeId: string) => string | null
}

const Ctx = createContext<KnowledgeScopeValue | null>(null)

export const KnowledgeScopeProvider = Ctx.Provider

export function useKnowledgeScope(): KnowledgeScopeValue {
  const v = useContext(Ctx)
  if (!v) throw new Error('useKnowledgeScope outside a Knowledge page')
  return v
}
