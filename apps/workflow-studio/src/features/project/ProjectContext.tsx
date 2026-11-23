import { createContext, useContext } from 'react'
import type { Project } from '@/shared/api/types'

export interface ProjectCtx {
  project: Project
  /** project id, used in every query key (UI-DESIGN D10) */
  pid: string
  slug: string
  /** the environment the top-bar switch is viewing (§2.4) */
  viewEnv: string
  setViewEnv: (env: string) => void
}

const Ctx = createContext<ProjectCtx | null>(null)

export const ProjectProvider = Ctx.Provider

export function useProjectCtx(): ProjectCtx {
  const v = useContext(Ctx)
  if (!v) throw new Error('useProjectCtx outside a project route')
  return v
}

export function useOptionalProjectCtx(): ProjectCtx | null {
  return useContext(Ctx)
}
