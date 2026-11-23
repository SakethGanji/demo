/**
 * `/p/$project/backend` (no module in the URL): opens the first module, or the new-module page when there is none.
 * A bare link to the Backend keeps working instead of landing on "Not found".
 */
import { useEffect } from 'react'
import { useNavigate } from '@tanstack/react-router'
import { useProjectCtx } from '@/features/project/ProjectContext'
import { useModules } from '@/features/project/hooks/useProjectData'
import { Spinner } from '@/shared/components/ui/spinner'

export function BackendIndex() {
  const { pid, slug } = useProjectCtx()
  const modules = useModules(pid)
  const navigate = useNavigate()
  const first = modules.data?.[0]?.name
  const ready = modules.data != null || modules.isError
  useEffect(() => {
    if (ready) navigate({ to: '/p/$project/backend/$module', params: { project: slug, module: first ?? '~new' }, replace: true })
  }, [ready, first, slug, navigate])
  return (
    <div className="flex h-full items-center justify-center" data-testid="backend-index">
      <Spinner />
    </div>
  )
}
