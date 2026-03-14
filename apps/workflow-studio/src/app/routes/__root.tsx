import { useEffect } from 'react'
import { createRootRoute, Outlet, useMatchRoute } from '@tanstack/react-router'
import { Toaster } from 'sonner'

import { ThemeProvider } from '@/shared/components/theme-provider'
import { ErrorBoundary } from '@/shared/components/ErrorBoundary'
import { useEditorLayoutStore } from '@/features/workflow-editor/stores/editorLayoutStore'

export const rootRoute = createRootRoute({
  component: RootLayout,
})

function RootLayout() {
  const closePanel = useEditorLayoutStore((s) => s.closeCreatorPanel)
  const matchRoute = useMatchRoute()
  const isEditorRoute = matchRoute({ to: '/editor', fuzzy: true })
  const isBuilderRoute = matchRoute({ to: '/builder', fuzzy: true })
  const isProjectsRoute = matchRoute({ to: '/projects', fuzzy: true })
  const isLandingRoute = matchRoute({ to: '/', fuzzy: false })

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

  const isFullScreen = isEditorRoute || isBuilderRoute || isProjectsRoute

  // Full takeover layout (editor, builder, projects, landing)
  if (isFullScreen || isLandingRoute) {
    return (
      <ThemeProvider defaultTheme="system" storageKey="workflow-studio-theme">
        <ErrorBoundary>
          <main className={isFullScreen ? 'h-screen w-screen overflow-hidden' : 'min-h-screen w-screen'}>
            <Outlet />
          </main>
        </ErrorBoundary>
        <Toaster position="bottom-right" richColors closeButton />
      </ThemeProvider>
    )
  }

  // Fallback layout
  return (
    <ThemeProvider defaultTheme="system" storageKey="workflow-studio-theme">
      <ErrorBoundary>
        <main className="relative flex w-full flex-1 flex-col h-screen">
          <div className="h-full w-full relative">
            <Outlet />
          </div>
        </main>
      </ErrorBoundary>
      <Toaster position="bottom-right" richColors closeButton />
    </ThemeProvider>
  )
}
