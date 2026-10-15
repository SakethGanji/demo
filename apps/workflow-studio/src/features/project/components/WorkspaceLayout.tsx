/**
 * The workspace shell (UI-DESIGN D2): floating top bar, persistent outline (rail below 1280px)
 * and the main area that swaps views. It never unmounts while moving between views.
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

export const panelCls = 'rounded-xl border border-panel-border bg-card shadow-xs'

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
  return (
    <div className="relative h-full w-full bg-canvas" data-testid="workspace">
      <ProjectTopBar />
      {width < 1024 && (
        <div
          className="absolute inset-x-3 top-[60px] z-10 flex items-center gap-2 rounded-lg bg-warning/13 px-3 py-1.5 text-xs text-warning-text"
          data-testid="narrow-banner"
        >
          <MonitorSmartphone className="size-3.5" /> luna works best on a wider screen
        </div>
      )}
      <div className={cn('absolute inset-x-3 bottom-3 flex gap-3', width < 1024 ? 'top-[100px]' : 'top-[68px]')}>
        {!outlineHidden && (
          <aside
            className={cn('flex shrink-0 flex-col', 'rounded-xl border border-panel-border bg-card shadow-xs', rail ? 'w-[52px]' : 'w-[248px]')}
            data-testid="outline-panel"
          >
            {rail ? <ProjectRail /> : <ProjectOutline />}
          </aside>
        )}
        <div className="relative min-w-0 flex-1" data-testid="main">
          <Outlet />
        </div>
      </div>
      <WorkspaceOverlays />
    </div>
  )
}
