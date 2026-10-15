/**
 * Sheets and dialogs addressed by search params on any project view (UI-DESIGN §2.2):
 * `sheet=settings|release`, `dialog=create-ui|new-module|new-table`.
 */
import { useNavigate, useSearch } from '@tanstack/react-router'
import type { ProjectSearch } from '@/app/routes/project'

export function useOverlayParams() {
  const search = useSearch({ strict: false }) as ProjectSearch
  const navigate = useNavigate()
  const close = (...keys: (keyof ProjectSearch)[]) =>
    navigate({
      to: '.',
      search: (s: Record<string, unknown>) => {
        const next = { ...s }
        for (const k of keys) delete next[k]
        return next
      },
      replace: true,
    } as never)
  return { search, close }
}

export function WorkspaceOverlays() {
  return null
}
