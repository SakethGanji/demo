import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { invalidate, qk } from '@/shared/api/queryKeys'
import { templatesApi } from './api'
import type { InstallOptions } from './types'

export const tk = {
  list: (pid: string | undefined) => ['templates', 'list', pid ?? ''] as const,
  detail: (slug: string) => ['templates', 'detail', slug] as const,
  preview: (pid: string, slug: string, o: InstallOptions) => ['templates', 'install-preview', pid, slug, o] as const,
}

/** The catalog; with a project, each template says whether it is installed there. */
export function useTemplates(pid: string | undefined) {
  return useQuery({ queryKey: tk.list(pid), queryFn: () => templatesApi.list(pid) })
}

export function useTemplate(slug: string | null) {
  return useQuery({ queryKey: tk.detail(slug ?? ''), queryFn: () => templatesApi.get(slug!), enabled: !!slug, staleTime: 5 * 60_000 })
}

/** What an install would do with the current options. Nothing is written. */
export function useInstallPreview(pid: string | undefined, slug: string | null, opts: InstallOptions) {
  return useQuery({
    queryKey: tk.preview(pid ?? '', slug ?? '', opts),
    queryFn: () => templatesApi.installPreview(pid!, slug!, opts),
    enabled: !!slug && !!pid,
    retry: false,
    staleTime: 0,
    gcTime: 0,
  })
}

export function useInstallTemplate(pid: string | undefined) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (v: { slug: string; opts: InstallOptions }) => {
      if (!pid) throw new Error('Choose a project to install into')
      return templatesApi.install(pid, v.slug, v.opts)
    },
    onSuccess: async () => {
      await Promise.all([
        qc.invalidateQueries({ queryKey: ['templates'] }),
        invalidate.workflowsChanged(qc, pid!),
        invalidate.tablesChanged(qc, pid!),
        qc.invalidateQueries({ queryKey: qk.apiSpecs(pid!) }),
        invalidate.secretsChanged(qc, pid!),
      ])
    },
  })
}
