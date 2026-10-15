/**
 * Sheets and dialogs addressed by search params on any project view (UI-DESIGN §2.2):
 * `sheet=settings|release`, `dialog=create-ui|new-module|new-table`.
 */
import { useNavigate, useSearch } from '@tanstack/react-router'
import type { ProjectSearch } from '@/app/routes/project'
import { SettingsSheet } from '@/features/settings/SettingsSheet'
import { ReleaseSheet } from '@/features/releases/ReleaseSheet'
import { CreateUiDialog } from '@/features/app-builder/create-ui/CreateUiDialog'
import { NewModuleDialog } from '@/features/backend/NewModuleDialog'
import { NewTableDialog } from '@/features/data/NewTableDialog'

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
  const { search, close } = useOverlayParams()
  return (
    <>
      <SettingsSheet open={search.sheet === 'settings'} section={search.section} onClose={() => close('sheet', 'section')} />
      <ReleaseSheet open={search.sheet === 'release'} onClose={() => close('sheet')} />
      <CreateUiDialog open={search.dialog === 'create-ui'} preselectModule={search.module} onClose={() => close('dialog', 'module')} />
      <NewModuleDialog open={search.dialog === 'new-module'} onClose={() => close('dialog')} />
      <NewTableDialog open={search.dialog === 'new-table'} onClose={() => close('dialog')} />
    </>
  )
}
