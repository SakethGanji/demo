/**
 * The workspace shell (DESIGN §1, Linear-style): the 48px top bar and the 240px outline (a 52px rail below
 * 1280px) sit on the canvas; every view renders inside one transparent sheet, 10px radius, 8px from the
 * window's right and bottom edges, so the glass tiles inside it frost the sky. It never unmounts while
 * moving between views.
 */
import { useEffect, useState } from 'react'
import { Outlet, useNavigate } from '@tanstack/react-router'
import { MonitorSmartphone } from 'lucide-react'
import { useProjectCtx } from '../ProjectContext'
import { ProjectTopBar } from './ProjectTopBar'
import { ProjectOutline, ProjectRail } from './ProjectOutline'
import { WorkspaceOverlays } from './WorkspaceOverlays'
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

/** `g o/b/d/s/r/w` and `[` (§3.1, §8.2). */
function useWorkspaceShortcuts(toggleOutline: () => void) {
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
      if (e.key === '[') {
        toggleOutline()
        return
      }
      if (e.key === 'g') {
        gAt = Date.now()
        return
      }
      if (Date.now() - gAt > 1000) return
      gAt = 0
      const k = e.key
      if (k === 'o') navigate({ to: '/p/$project', params: P })
      else if (k === 'b') navigate({ to: '/p/$project/backend/$module', params: { ...P, module: modules.data?.[0]?.name ?? '~new' } })
      else if (k === 'd') {
        const t = tables.data?.[0]?.name
        if (t) navigate({ to: '/p/$project/data/$table', params: { ...P, table: t } })
        else navigate({ to: '/p/$project/data', params: P })
      } else if (k === 's') navigate({ to: '/p/$project/secrets', params: P })
      else if (k === 'r') navigate({ to: '/p/$project/runs', params: P })
      else if (k === 'w') navigate({ to: '/p/$project/workflows', params: P })
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [slug, navigate, modules.data, tables.data, toggleOutline])
}

export function WorkspaceLayout() {
  const width = useViewportWidth()
  const [outlineHidden, setOutlineHidden] = useState(false)
  useWorkspaceShortcuts(() => setOutlineHidden((v) => !v))
  const rail = width < 1280
  const narrow = width < 1024
  const left = outlineHidden ? 8 : rail ? 52 : 240
  return (
    <div className="relative h-full w-full" data-testid="workspace">
      <ProjectTopBar />
      {narrow && (
        <div
          className="absolute inset-x-3 top-[52px] z-10 flex items-center gap-2 rounded-ctl bg-warning-soft px-3 py-1.5 text-xs text-warning-text"
          data-testid="narrow-banner"
        >
          <MonitorSmartphone className="size-3.5" /> Command Studio works best on a wider screen
        </div>
      )}
      {!outlineHidden && (
        <aside
          className={cn('absolute bottom-0 left-0 flex flex-col bg-sidebar', rail ? 'w-[52px]' : 'w-[240px]', narrow ? 'top-[88px]' : 'top-12')}
          data-testid="outline-panel"
        >
          {rail ? <ProjectRail /> : <ProjectOutline />}
        </aside>
      )}
      <div
        className={cn('absolute right-2 bottom-2 min-w-0 overflow-hidden rounded-sheet', narrow ? 'top-[88px]' : 'top-12')}
        style={{ left }}
        data-testid="main"
      >
        <Outlet />
      </div>
      <WorkspaceOverlays />
    </div>
  )
}
