import { useQuery } from '@tanstack/react-query'
import { isApiError } from '@/shared/api/http'
import { qk } from '@/shared/api/queryKeys'
import { publicInterfaceApi } from '../api/interfaceApi'

export function usePublicSchema(project: string, slug: string) {
  return useQuery({
    queryKey: qk.interfaces.publicSchema(project, slug),
    queryFn: () => publicInterfaceApi.schema(project, slug),
    retry: (count, err) => !isApiError(err, undefined, 404) && count < 2,
    staleTime: 30_000,
  })
}
