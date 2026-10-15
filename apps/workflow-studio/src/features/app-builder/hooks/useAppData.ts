/**
 * Builder data: the app detail (query cache is the source of truth for `definition.studio`),
 * api-usage, the generated client, and the one `saveApp()` every writer uses (§5.1 N4).
 */
import { useCallback } from 'react'
import { useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query'
import { appsApi, type ApiAppDetail, type ApiAppFile } from '@/shared/lib/api'
import { appApiApi, specApi } from '@/shared/api/endpoints'
import { qk } from '@/shared/api/queryKeys'
import { stripClient, type StudioBlock } from '../lib/appModel'

export function useApp(appId: string) {
  return useQuery({ queryKey: qk.app(appId), queryFn: () => appsApi.get(appId), staleTime: 30_000 })
}

export function useApiUsage(appId: string) {
  return useQuery({ queryKey: qk.apiUsage(appId), queryFn: () => appApiApi.apiUsage(appId) })
}

export function useClientTs(pid: string, env: string, enabled = true) {
  return useQuery({
    queryKey: qk.clientTs(pid, env),
    queryFn: () => specApi.clientTs(pid, env),
    enabled,
    staleTime: 15_000,
  })
}

export function useSpecRevision(pid: string) {
  return useQuery({
    queryKey: qk.revisions(pid),
    queryFn: () => specApi.revisions(pid),
    select: (revs) => revs.reduce((m, r) => Math.max(m, r.revision ?? 0), 0) || null,
  })
}

export type AppPatch = Parameters<typeof appsApi.update>[1] & {
  definition?: Record<string, unknown>
  /** set the studio block (merged over the cached one); omit to keep it */
  studio?: StudioBlock
}

/**
 * The single writer for `PUT /api/apps/{A}` (re-review N4). Every call sends `definition` with
 * the current `studio` block from the query cache merged in, and never sends `api/client.ts`.
 */
export async function saveApp(qc: QueryClient, appId: string, patch: AppPatch): Promise<ApiAppDetail> {
  const cached = qc.getQueryData<ApiAppDetail>(qk.app(appId))
  const baseDef = patch.definition ?? cached?.definition ?? {}
  const cachedStudio = ((cached?.definition as { studio?: StudioBlock } | undefined)?.studio ?? {}) as StudioBlock
  const patchStudio = (patch.definition as { studio?: StudioBlock } | undefined)?.studio
  const studio: StudioBlock = { ...cachedStudio, ...(patchStudio ?? {}), ...(patch.studio ?? {}) }
  const { studio: _s, files, ...rest } = patch
  void _s
  const definition = { ...baseDef, studio }
  // Optimistic: concurrent writers (AI auto-save vs route picker) read the newest studio block.
  if (cached) qc.setQueryData<ApiAppDetail>(qk.app(appId), { ...cached, definition })
  const body = { ...rest, definition, ...(files ? { files: stripClient(files as ApiAppFile[]) } : {}) }
  try {
    const res = await appsApi.update(appId, body)
    qc.setQueryData(qk.app(appId), res)
    return res
  } catch (e) {
    if (cached) qc.setQueryData(qk.app(appId), cached)
    throw e
  }
}

export function useSaveApp(appId: string) {
  const qc = useQueryClient()
  return useCallback((patch: AppPatch) => saveApp(qc, appId, patch), [qc, appId])
}
