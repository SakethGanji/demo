import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { qk } from '@/shared/api/queryKeys'
import { apiSpecsApi } from '@/shared/lib/api'

export function useApiSpecs(pid: string) {
  return useQuery({ queryKey: qk.apiSpecs(pid), queryFn: () => apiSpecsApi.list(pid) })
}

export function useApiSpec(pid: string, id: string | undefined) {
  return useQuery({
    queryKey: qk.apiSpec(pid, id ?? ''),
    queryFn: () => apiSpecsApi.get(id!),
    enabled: !!id,
    staleTime: 30_000,
  })
}

export function useCreateApiSpec(pid: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: Omit<Parameters<typeof apiSpecsApi.create>[0], 'project_id'>) => apiSpecsApi.create({ ...body, project_id: pid }),
    onSettled: () => qc.invalidateQueries({ queryKey: qk.apiSpecs(pid) }),
  })
}

export function useCreateApiSpecFromExample(pid: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: Omit<Parameters<typeof apiSpecsApi.fromExample>[0], 'project_id'>) =>
      apiSpecsApi.fromExample({ ...body, project_id: pid }),
    onSettled: () => qc.invalidateQueries({ queryKey: qk.apiSpecs(pid) }),
  })
}

export function useDeleteApiSpec(pid: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => apiSpecsApi.remove(id),
    onSuccess: (_d, id) => {
      // Drop the row and its detail first so nothing re-requests the deleted id.
      qc.setQueryData<Array<{ id: string }>>(qk.apiSpecs(pid), (old) => old?.filter((s) => s.id !== id))
      qc.removeQueries({ queryKey: qk.apiSpec(pid, id), exact: true })
    },
    onSettled: () => qc.invalidateQueries({ queryKey: qk.apiSpecs(pid), exact: true }),
  })
}
