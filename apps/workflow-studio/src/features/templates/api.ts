/** Every template call in one module; hooks.ts wraps these. */
import { request } from '@/shared/api/http'
import type { InstallOptions, InstallPreview, InstallResult, TemplateDetail, TemplateSummary } from './types'

const enc = encodeURIComponent

export const templatesApi = {
  list: (project?: string): Promise<TemplateSummary[]> => request(`/templates`, { query: { project } }),
  get: (slug: string): Promise<TemplateDetail> => request(`/templates/${enc(slug)}`),
  installPreview: (project: string, slug: string, opts: InstallOptions): Promise<InstallPreview> =>
    request(`/projects/${enc(project)}/templates/${enc(slug)}/install-preview`, { method: 'POST', body: opts }),
  install: (project: string, slug: string, opts: InstallOptions): Promise<InstallResult> =>
    request(`/projects/${enc(project)}/templates/${enc(slug)}/install`, { method: 'POST', body: opts }),
}
