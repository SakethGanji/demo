import { create } from 'zustand'
import type { PlannedRoute } from '@/shared/api/types'

/**
 * What an open, unsaved module draft would change, published by the Backend view so the
 * outline can show `draft` modules, violet `new` routes, "created on save" tables and the
 * draft-only missing secrets (UI-DESIGN §3.1).
 */
export interface DraftOutline {
  projectId: string
  module: string
  isNew: boolean
  dirty: boolean
  newRoutes: PlannedRoute[]
  tablesOnSave: string[]
  /** keys referenced by the draft that are missing in some environment */
  missingSecretKeys: string[]
}

interface State {
  drafts: Record<string, DraftOutline>
  set: (d: DraftOutline) => void
  clear: (projectId: string, module: string) => void
}

export const draftKey = (projectId: string, module: string) => `${projectId}:${module}`

export const useOutlineDraftStore = create<State>((set) => ({
  drafts: {},
  set: (d) => set((s) => ({ drafts: { ...s.drafts, [draftKey(d.projectId, d.module)]: d } })),
  clear: (projectId, module) =>
    set((s) => {
      const next = { ...s.drafts }
      delete next[draftKey(projectId, module)]
      return { drafts: next }
    }),
}))
