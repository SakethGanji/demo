import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { interfacesApi } from '@/shared/api/endpoints'
import { qk } from '@/shared/api/queryKeys'
import type { Interface, InterfaceState } from '../lib/types'

const isSaved = (wf: string | null | undefined): wf is string => !!wf && wf !== 'new'

/** GET /api/workflows/{id}/interface (interface, live, wired, warnings). Disabled for an unsaved workflow. */
export function useInterfaceState(workflowId: string | null | undefined) {
  return useQuery({
    queryKey: qk.interfaces.state(workflowId ?? ''),
    queryFn: () => interfacesApi.get(workflowId as string),
    enabled: isSaved(workflowId),
    staleTime: 5000,
  })
}

/** PUT (full replace; `null` removes). Keeps `live` from the cached state: PUT does not return it. */
export function useSaveInterface(workflowId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (next: Interface | null) => interfacesApi.put(workflowId, next),
    onSuccess: (res) => {
      qc.setQueryData<InterfaceState>(qk.interfaces.state(workflowId), (prev) => ({
        interface: res.interface,
        wired: res.wired,
        warnings: res.warnings,
        live: prev?.live ?? null,
      }))
      void qc.invalidateQueries({ queryKey: qk.interfaces.previewPrefix(workflowId) })
    },
  })
}
