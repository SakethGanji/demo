import { useEffect } from 'react'
import { createRootRoute, Outlet, useMatchRoute } from '@tanstack/react-router'
import { Toaster } from 'sonner'

import { AppSidebar } from '@/shared/components/app-sidebar'
import { ThemeProvider } from '@/shared/components/theme-provider'
import { ErrorBoundary } from '@/shared/components/ErrorBoundary'
import { SidebarProvider } from '@/shared/components/ui/sidebar'
import { useEditorLayoutStore } from '@/features/workflow-editor/stores/editorLayoutStore'

export const rootRoute = createRootRoute({
  component: RootLayout,
})

function RootLayout() {
  const closePanel = useEditorLayoutStore((s) => s.closeCreatorPanel)
  const matchRoute = useMatchRoute()
  const isEditorRoute = matchRoute({ to: '/editor', fuzzy: true })
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

  // Editor/landing route: full takeover, no sidebar
  if (isEditorRoute || isLandingRoute) {
    return (
      <ThemeProvider defaultTheme="system" storageKey="workflow-studio-theme">
        <ErrorBoundary>
          <main className={isEditorRoute ? 'h-screen w-screen overflow-hidden' : 'min-h-screen w-screen'}>
            <Outlet />
          </main>
        </ErrorBoundary>
        <Toaster position="bottom-right" richColors closeButton />
      </ThemeProvider>
    )
  }

  return (
    <ThemeProvider defaultTheme="system" storageKey="workflow-studio-theme">
      <ErrorBoundary>
        <SidebarProvider defaultOpen={false}>
          <AppSidebar />
          <main className="relative flex w-full flex-1 flex-col">
            <div className="h-full w-full relative">
              <Outlet />
            </div>
          </main>
        </SidebarProvider>
      </ErrorBoundary>
      <Toaster position="bottom-right" richColors closeButton />
    </ThemeProvider>
  )
}
