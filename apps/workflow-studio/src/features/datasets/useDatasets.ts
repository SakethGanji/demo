/** React Query hooks for project datasets. `pid` is the resolved project id (UI-DESIGN D10). */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { datasetsApi } from '@/shared/api/endpoints'
import { invalidate, qk } from '@/shared/api/queryKeys'

export function useDatasets(pid: string) {
  return useQuery({ queryKey: qk.datasets(pid), queryFn: () => datasetsApi.list(pid), enabled: !!pid })
}

export function useDataset(pid: string, id: string | undefined) {
  return useQuery({
    queryKey: qk.dataset(pid, id ?? ''),
    queryFn: () => datasetsApi.get(pid, id as string),
    enabled: !!pid && !!id,
  })
}

export function useUploadDataset(pid: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ file, name }: { file: File; name?: string }) => datasetsApi.upload(pid, file, name),
    onSettled: () => invalidate.datasetsChanged(qc, pid),
  })
}

export function useRenameDataset(pid: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, name }: { id: string; name: string }) => datasetsApi.rename(pid, id, name),
    onSettled: (_d, _e, v) => invalidate.datasetsChanged(qc, pid, v.id),
  })
}

export function useDeleteDataset(pid: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => datasetsApi.remove(pid, id),
    onSuccess: (_d, id) => qc.removeQueries({ queryKey: qk.dataset(pid, id) }),
    onSettled: () => invalidate.datasetsChanged(qc, pid),
  })
}

/** Profiling is on demand (it reads the whole file in the analytics service), so it is a mutation, not a query. */
