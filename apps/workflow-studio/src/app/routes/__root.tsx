import { useEffect } from 'react'
import { createRootRouteWithContext, Outlet, useMatchRoute } from '@tanstack/react-router'
import type { QueryClient } from '@tanstack/react-query'
import { Toaster } from 'sonner'

import { ThemeProvider } from '@/shared/components/theme-provider'
import { ErrorBoundary } from '@/shared/components/ErrorBoundary'
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
  return (
    <ThemeProvider defaultTheme="system" storageKey="workflow-studio-theme">
      <ErrorBoundary>
        <main className={isLandingRoute ? 'min-h-screen w-screen' : 'h-screen w-screen overflow-hidden'}>
          <Outlet />
        </main>
      </ErrorBoundary>
      <Toaster position="bottom-right" richColors closeButton />
    </ThemeProvider>
  )
}
