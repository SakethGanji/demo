/**
 * The workspace shell (mounted by ProjectLayout): the 48px top bar on the canvas, and every view, full window,
 * inside one transparent sheet, 8px from the window edges, so the glass tiles
 * inside it frost the sky. Every workspace page gets the same frame with no side panel of its own: Backend and Data
 * open their index (modules, routes, tables) as a switcher from their own header. It never unmounts between views.
 */
import { useEffect } from 'react'
import { Outlet, useNavigate } from '@tanstack/react-router'
import { MonitorSmartphone } from 'lucide-react'
import { useProjectCtx } from '../ProjectContext'
import { ProjectTopBar } from './ProjectTopBar'
import { useViewportWidth } from '@/shared/hooks/useViewport'
import { useModules, useTables } from '../hooks/useProjectData'
import { cn } from '@/shared/lib/utils'

/** The page frame inside the sheet: transparent, no edge (pages build their boxes from `Tile` / `Well`). */
export const panelCls = 'rounded-sheet'

function isTyping(e: KeyboardEvent) {
  const t = e.target as HTMLElement | null
  if (!t) return false
  return t.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(t.tagName) || !!t.closest('.cm-editor')
}

/** `g h/o/b/d/s/r/w` (§3.1, §8.2): `g h` (and `g o`) is home. */
function useWorkspaceShortcuts() {
  const { slug, pid } = useProjectCtx()
  const navigate = useNavigate()
  const modules = useModules(pid)
  const tables = useTables(pid)
  useEffect(() => {
    let gAt = 0
    const onKey = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey || isTyping(e)) return
      if (document.querySelector('[role="dialog"]')) return
      const P = { project: slug }
      if (e.key === 'g') {
        gAt = Date.now()
        return
      }
      if (Date.now() - gAt > 1000) return
      gAt = 0
      const k = e.key
      if (k === 'o' || k === 'h') navigate({ to: '/p/$project', params: P })
      else if (k === 'b') navigate({ to: '/p/$project/backend/$module', params: { ...P, module: modules.data?.[0]?.name ?? '~new' } })
      else if (k === 'd') {
        const t = tables.data?.[0]?.name
        if (t) navigate({ to: '/p/$project/data/$table', params: { ...P, table: t } })
        else navigate({ to: '/p/$project/data', params: P })
      } else if (k === 's') navigate({ to: '/p/$project/secrets', params: P })
      else if (k === 'r') navigate({ to: '/p/$project/runs', params: P })
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [slug, navigate, modules.data, tables.data])
}

export function WorkspaceLayout() {
  const width = useViewportWidth()
  useWorkspaceShortcuts()
  const narrow = width < 1024
  const top = narrow ? 'top-[96px]' : 'top-14'
  return (
    <div className="relative h-full w-full" data-testid="workspace">
      <ProjectTopBar />
      {narrow && (
        <div
          className="absolute inset-x-2 top-[52px] z-10 flex items-center gap-2 rounded-ctl bg-warning-soft px-3 py-1.5 text-xs text-warning-text"
          data-testid="narrow-banner"
        >
          <MonitorSmartphone className="size-3.5 shrink-0" /> <span className="truncate">Command Studio works best on a wider screen</span>
        </div>
      )}
      <div className={cn('absolute inset-x-2 bottom-2 min-w-0 overflow-hidden rounded-sheet', top)} data-testid="main">
        <Outlet />
      </div>
    </div>
  )
}
