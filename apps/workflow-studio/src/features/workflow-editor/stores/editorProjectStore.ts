import { create } from 'zustand'

/**
 * Which project the canvas editor is open in (set by the `/p/$project/workflows/$id` route).
 * New workflows are created inside it and the editor's links point back to it.
 */
interface EditorProjectState {
  projectId: string
  slug: string
  environments: string[]
  setProject: (projectId: string, slug: string, environments: string[]) => void
}

export const useEditorProjectStore = create<EditorProjectState>((set) => ({
  projectId: 'proj_default',
  slug: 'default',
  environments: [],
  setProject: (projectId, slug, environments) => set({ projectId, slug, environments }),
}))
