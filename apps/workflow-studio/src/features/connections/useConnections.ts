import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { connectionsApi } from './connectionsApi'
import type { ConnectionCreate } from './connectionsTypes'

export const connKeys = {
  list: (p: string) => ['project', p, 'connections'] as const,
  schema: (p: string, id: string) => ['project', p, 'connections', id, 'schema'] as const,
}

export function useConnections(pid: string) {
  return useQuery({ queryKey: connKeys.list(pid), queryFn: () => connectionsApi.list(pid), enabled: !!pid, staleTime: 15_000 })
}

export function useConnectionSchema(pid: string, id: string | undefined) {
  return useQuery({ queryKey: connKeys.schema(pid, id ?? ''), queryFn: () => connectionsApi.schema(pid, id as string), enabled: !!pid && !!id })
}

export function useConnectionMutations(pid: string) {
  const qc = useQueryClient()
  const refresh = () => qc.invalidateQueries({ queryKey: connKeys.list(pid) })
  const create = useMutation({ mutationFn: (b: ConnectionCreate) => connectionsApi.create(pid, b), onSettled: refresh })
  const test = useMutation({ mutationFn: (id: string) => connectionsApi.test(pid, id), onSettled: refresh })
  const introspect = useMutation({ mutationFn: (id: string) => connectionsApi.introspect(pid, id), onSettled: refresh })
  const remove = useMutation({ mutationFn: (id: string) => connectionsApi.remove(pid, id), onSettled: refresh })
  return { create, test, introspect, remove }
}
