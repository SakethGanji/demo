/**
 * "Start something new": create a project, record how it started (`settings.onboarding.start`), then open the
 * place that start leads to. The six cards are the same picker the setup guide uses (`StartPicker`).
 *  - fromInterface: a blank interface (no backend) in the app builder, with the prompt;
 *  - fromBackend / fromPrompt: the backend editor (`~new`, or `?generate=1&prompt=…`, which BackendView auto-runs);
 *  - fromWorkflow: a blank workflow, or the editor with the AI Build tab open (and the prompt typed in);
 *  - fromDataset: uploads the file, then the guide offers "Profile it" and "Email me a report";
 *  - fromTemplate: a home module template seeds an unsaved module draft; an engine template opens the guide's
 *    template chooser on it, where install-preview shows what it needs before anything is installed.
 * A name that is already taken gets " 2", " 3"… so a second click never dead-ends.
 */
import { useState } from 'react'
import { useNavigate } from '@tanstack/react-router'
import { useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { datasetsApi, projectsApi } from '@/shared/api/endpoints'
import { errorMessage, isApiError } from '@/shared/api/http'
import { invalidate } from '@/shared/api/queryKeys'
import type { Project } from '@/shared/api/types'
import type { TemplateSummary } from '@/features/templates/types'
import { EMPTY_ONBOARDING, type Onboarding } from '../lib/nextSteps'
import { saveOnboarding } from '../hooks/useOnboarding'
import { createInterfaceApp, nameFromPrompt, prefillAiRequest, seedHomeTemplate } from './startActions'
import type { HomeTemplate } from './templates'

export { nameFromPrompt }

const isEngineTemplate = (t: HomeTemplate | TemplateSummary): t is TemplateSummary => 'slug' in t

export function useStartProject() {
  const qc = useQueryClient()
  const navigate = useNavigate()
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

  const run = async (key: string, name: string, onboarding: Partial<Onboarding>, then: (p: Project) => void | Promise<void>) => {
    if (busy) return
    setBusy(key)
    try {
      const p = await createUnique(name)
      await saveOnboarding(p.id, { ...EMPTY_ONBOARDING, ...onboarding })
      await invalidate.projectCreated(qc)
      await then(p)
    } catch (e) {
      toast.error(`Couldn't create the project: ${errorMessage(e)}`)
    } finally {
      setBusy(null)
    }
  }

  const toProject = (p: Project) => navigate({ to: '/p/$project', params: { project: p.slug } })

  return {
    busy,
    fromTemplate: (t: HomeTemplate | TemplateSummary) =>
      isEngineTemplate(t)
        ? run(t.slug, t.name, { start: 'template', template: t.slug }, toProject)
        : run(t.key, t.projectName, { start: 'template' }, async (p) => {
            await seedHomeTemplate(p.id, t)
            navigate({ to: '/p/$project/backend/$module', params: { project: p.slug, module: t.module } })
          }),
    /** the AI card aimed at a backend */
    fromPrompt: (prompt: string) =>
      run('ai', nameFromPrompt(prompt), { start: 'ai', target: 'backend' }, (p) =>
        navigate({ to: '/p/$project/backend/$module', params: { project: p.slug, module: '~new' }, search: { prompt, generate: 1 } }),
      ),
    /** the Backend card: generate from a prompt, or write the module yourself */
    fromBackend: (prompt: string) =>
      run('backend', prompt.trim() ? nameFromPrompt(prompt) : 'New backend', { start: 'backend' }, (p) =>
        prompt.trim()
          ? navigate({ to: '/p/$project/backend/$module', params: { project: p.slug, module: '~new' }, search: { prompt: prompt.trim(), generate: 1 } })
          : navigate({ to: '/p/$project', params: { project: p.slug }, search: { dialog: 'new-module' } }),
      ),
    fromWorkflow: (opts: { ai?: boolean; prompt?: string } = {}) => {
      const prompt = opts.prompt?.trim() ?? ''
      return run(
        opts.ai ? 'workflow-ai' : 'workflow',
        prompt ? nameFromPrompt(prompt) : 'New workflow',
        prompt ? { start: 'ai', target: 'workflow' } : { start: 'workflow' },
        (p) => {
          if (prompt) prefillAiRequest(prompt)
          navigate({
            to: '/p/$project/workflows/$workflowId',
            params: { project: p.slug, workflowId: 'new' },
            search: opts.ai || prompt ? { ai: 1 } : {},
          })
        },
      )
    },
    fromDataset: (file: File) =>
      run('dataset', nameFromPrompt(file.name.replace(/\.[^.]+$/, ''), 'New dataset'), { start: 'dataset' }, async (p) => {
        try {
          await datasetsApi.upload(p.id, file)
        } catch (e) {
          toast.error(`The project is ready, but the file didn't import: ${errorMessage(e)}`)
        }
        toProject(p)
      }),
    fromInterface: (prompt = '', viaAi = false) =>
      run('interface', prompt.trim() ? nameFromPrompt(prompt) : 'New interface', viaAi ? { start: 'ai', target: 'interface' } : { start: 'interface' }, async (p) => {
        const app = await createInterfaceApp(qc, p.id, prompt)
        navigate({
          to: '/p/$project/apps/$appId',
          params: { project: p.slug, appId: app.appId },
          search: { ...(app.prompt ? { prompt: app.prompt } : {}), slug: app.slug },
        })
      }),
  }
}
