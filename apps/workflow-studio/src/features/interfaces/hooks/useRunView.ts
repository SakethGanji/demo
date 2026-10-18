import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { isApiError } from '@/shared/api/http'
import { qk } from '@/shared/api/queryKeys'
import { publicInterfaceApi } from '../api/interfaceApi'
import { runRefetchInterval } from '../lib/polling'

/**
 * Polls one run (DESIGN-STAGE5-UIS §6.1). The URL carries the execution id and token, so a reload, another
 * device or an engine restart picks the same run up. Stops on a terminal status; `waiting` keeps polling.
 */
export function useRunView(project: string, slug: string, executionId: string, token: string | null) {
  const [startedAt] = useState(() => Date.now())
  return useQuery({
    queryKey: qk.interfaces.publicRun(project, slug, executionId),
    queryFn: () => publicInterfaceApi.run(project, slug, executionId, token as string),
    enabled: !!token,
    refetchInterval: (q) => runRefetchInterval(q.state.data, Date.now() - startedAt),
    retry: (count, err) => !isApiError(err, undefined, 404) && count < 3,
    staleTime: 0,
  })
}
