import { useEffect } from 'react'
import { createRootRouteWithContext, Outlet, useMatchRoute } from '@tanstack/react-router'
import type { QueryClient } from '@tanstack/react-query'

import { ThemeProvider } from '@/shared/components/theme-provider'
import { ErrorBoundary } from '@/shared/components/ErrorBoundary'
import { Toaster } from '@/shared/components/ui/toaster'
import { TooltipProvider } from '@/shared/components/ui/tooltip'
import { flushPendingToasts } from '@/shared/lib/pendingToast'
import { useEditorLayoutStore } from '@/features/workflow-editor/stores/editorLayoutStore'

export interface RouterContext {
  queryClient: QueryClient
}

export const rootRoute = createRootRouteWithContext<RouterContext>()({
  component: RootLayout,
})

function RootLayout() {
  const closePanel = useEditorLayoutStore((s) => s.closeCreatorPanel)
  const matchRoute = useMatchRoute()
  const isLandingRoute = matchRoute({ to: '/', fuzzy: false })

  // Toasts queued by router redirects before first render (the Toaster is mounted by now: children's effects run first)
  useEffect(() => flushPendingToasts(), [])

  // Global keyboard shortcuts
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        closePanel()
      }
    }

    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [closePanel])

  // Every studio page (projects, /p/…, redirects) is a full-screen takeover; the landing scrolls.
  // Every page sits on the shared backdrop (body::before/::after in index.css); Projects home and the landing add stars.
  return (
    <ThemeProvider defaultTheme="system" storageKey="workflow-studio-theme">
      <TooltipProvider delay={500} timeout={300}>
        <ErrorBoundary>
          <main className={isLandingRoute ? 'relative z-[1] min-h-screen w-screen' : 'relative z-[1] h-screen w-screen overflow-hidden'}>
            <Outlet />
          </main>
        </ErrorBoundary>
        <Toaster />
      </TooltipProvider>
    </ThemeProvider>
  )
}
