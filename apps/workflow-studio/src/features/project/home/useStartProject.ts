/**
 * "Start something new": create a project, then open the backend editor on it.
 *  - a template seeds an unsaved module draft (same shape as the New module dialog) and opens it;
 *  - "Describe it instead" opens `backend/~new?generate=1&prompt=…`, which BackendView auto-runs once.
 * A name that is already taken gets " 2", " 3"… so a second click never dead-ends.
 */
import { useState } from 'react'
import { useNavigate } from '@tanstack/react-router'
import { useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { projectsApi, tablesApi } from '@/shared/api/endpoints'
import { errorMessage, isApiError } from '@/shared/api/http'
import { invalidate } from '@/shared/api/queryKeys'
import type { Project } from '@/shared/api/types'
import { useDocStore } from '@/features/backend/docStore'
import type { HomeTemplate } from './templates'

/** a short project name from a free-text description */
export function nameFromPrompt(prompt: string): string {
  const words = prompt
    .replace(/[^A-Za-z0-9 ]+/g, ' ')
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 4)
  const name = words.map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join(' ')
  return name || 'New project'
}

export function useStartProject() {
  const qc = useQueryClient()
  const navigate = useNavigate()
  const put = useDocStore((s) => s.put)
  const openTab = useDocStore((s) => s.openTab)
  const [busy, setBusy] = useState<string | null>(null)

  async function createUnique(name: string): Promise<Project> {
    let last: unknown
    for (let i = 1; i <= 5; i++) {
      try {
        return await projectsApi.create({ name: i === 1 ? name : `${name} ${i}` })
      } catch (e) {
        last = e
        if (!isApiError(e) || e.status >= 500) break
      }
    }
    throw last
  }

  const run = async (key: string, name: string, then: (p: Project) => void | Promise<void>) => {
    if (busy) return
    setBusy(key)
    try {
      const p = await createUnique(name)
      await invalidate.projectCreated(qc)
      await then(p)
    } catch (e) {
      toast.error(`Couldn't create the project: ${errorMessage(e)}`)
    } finally {
      setBusy(null)
    }
  }

  return {
    busy,
    fromTemplate: (t: HomeTemplate) =>
      run(t.key, t.projectName, async (p) => {
        for (const tb of t.tables) await tablesApi.create(p.id, tb)
        put({
          projectId: p.id,
          module: t.module,
          moduleId: null,
          source: t.source(),
          savedSource: '',
          baseSha: null,
          isNew: true,
          draft: null,
          prompts: [],
          recentTests: [],
          editorKey: `${t.module}-${Date.now()}`,
        })
        openTab(p.id, t.module)
        navigate({ to: '/p/$project/backend/$module', params: { project: p.slug, module: t.module } })
      }),
    fromPrompt: (prompt: string) =>
      run('ai', nameFromPrompt(prompt), (p) =>
        navigate({ to: '/p/$project/backend/$module', params: { project: p.slug, module: '~new' }, search: { prompt, generate: 1 } }),
      ),
  }
}
