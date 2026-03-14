import { useQuery } from '@tanstack/react-query'
import { appsApi } from '@/shared/lib/api'

export interface AppSummary {
  id: string
  name: string
  createdAt: string
  updatedAt: string
}

function transformApp(api: { id: string; name: string; created_at: string; updated_at: string }): AppSummary {
  return {
    id: api.id,
    name: api.name,
    createdAt: api.created_at,
    updatedAt: api.updated_at,
  }
}

async function fetchApps(): Promise<AppSummary[]> {
  const list = await appsApi.list()
  return list.map(transformApp)
}

export function useApps() {
  return useQuery({
    queryKey: ['apps'],
    queryFn: fetchApps,
    staleTime: 1000 * 60 * 5,
  })
}
