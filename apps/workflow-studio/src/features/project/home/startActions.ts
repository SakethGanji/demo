/**
 * The start actions shared by the setup guide (inside a project) and "Start something new" (which creates the
 * project first): create an interface with no backend, seed a backend module template, install a dataset
 * template with the dataset pre-selected, and pre-fill the workflow AI Build tab.
 */
import type { QueryClient } from '@tanstack/react-query'
import { projectsApi, tablesApi } from '@/shared/api/endpoints'
import { invalidate } from '@/shared/api/queryKeys'
import { workflowsApi } from '@/shared/lib/api'
import type { BackendWorkflow } from '@/shared/lib/backendTypes'
import { BLANK_DEFINITION, slugify } from '@/features/app-builder/lib/appModel'
import { useAiProposalStore } from '@/features/ai-builder/aiProposalStore'
import { useDocStore } from '@/features/backend/docStore'
import type { DatasetItem } from '@/features/datasets/lib/types'
import { templatesApi } from '@/features/templates/api'
import type { HomeTemplate } from './templates'

/** a short name from a free-text description */
export function nameFromPrompt(prompt: string, fallback = 'New project'): string {
  const words = prompt
    .replace(/[^A-Za-z0-9 ]+/g, ' ')
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 4)
  const name = words.map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join(' ')
  return name || fallback
}

/**
 * A blank interface with no routes: the builder starts from the prompt, and any route it calls that does not
 * exist becomes a stub (`POST …/routes/stubs`, offered in the builder's backend panel and the release checks).
 */
export async function createInterfaceApp(qc: QueryClient, pid: string, prompt: string): Promise<{ appId: string; slug: string; prompt: string }> {
  const text = prompt.trim()
  const name = text ? nameFromPrompt(text, 'Interface') : 'Interface'
  const created = await projectsApi.createApp(pid, { name, definition: BLANK_DEFINITION, description: text.slice(0, 1000) })
  void invalidate.appsChanged(qc, pid)
  return { appId: created.id, slug: slugify(name) || 'app', prompt: text }
}

/** Creates the template's tables and opens an unsaved module draft (same shape as the New module dialog). */
export async function seedHomeTemplate(pid: string, t: HomeTemplate) {
  const { put, openTab } = useDocStore.getState()
  for (const tb of t.tables) {
    try {
      await tablesApi.create(pid, tb)
    } catch {
      /* a table of that name already exists: the module uses it */
    }
  }
  put({
    projectId: pid,
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
  openTab(pid, t.module)
}

/**
 * Installs a dataset template (`profile-dataset`, `dataset-report-email`) and points its Profile step at the
 * dataset. The install has no dataset option, so the entry workflow is patched after it; if that fails the
 * workflow is still installed and `preselected` is false (the caller opens it so the user picks the dataset).
 */
export async function installForDataset(
  qc: QueryClient,
  pid: string,
  slug: string,
  dataset: Pick<DatasetItem, 'id'> | null,
): Promise<{ workflowId: string | null; preselected: boolean }> {
  const res = await templatesApi.install(pid, slug, { clash: 'rename' })
  const entry = res.workflows.find((w) => w.role === 'entry') ?? res.workflows[0]
  let preselected = false
  if (entry && dataset) {
    try {
      const wf = await workflowsApi.get(entry.id)
      let touched = false
      const nodes = wf.definition.nodes.map((n) => {
        if (n.type !== 'Profile' || n.parameters?.sourceType !== 'dataset' || n.parameters?.datasetId) return n
        touched = true
        return { ...n, parameters: { ...n.parameters, datasetId: dataset.id } }
      })
      if (touched) {
        await workflowsApi.update(entry.id, { name: wf.name, nodes, connections: wf.definition.connections } as BackendWorkflow)
        preselected = true
      }
    } catch {
      /* left for the user to pick in the editor */
    }
  }
  await Promise.all([qc.invalidateQueries({ queryKey: ['templates'] }), invalidate.workflowsChanged(qc, pid)])
  return { workflowId: entry?.id ?? null, preselected }
}

/** Opens the workflow editor's Build tab with this request typed in (the Build tab reads the pending request). */
export function prefillAiRequest(prompt: string) {
  if (prompt.trim()) useAiProposalStore.getState().setPendingRequest(prompt.trim())
}
