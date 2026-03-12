import { createRoute } from '@tanstack/react-router'
import { useState, useMemo } from 'react'
import { Plus, Search, LayoutGrid, List, AppWindow } from 'lucide-react'

import { rootRoute } from './__root'
import { Button } from '@/shared/components/ui/button'
import { Input } from '@/shared/components/ui/input'
import { Skeleton } from '@/shared/components/ui/skeleton'
import { useApps } from '@/features/apps/hooks/useApps'
import { useCreateApp } from '@/features/apps/hooks/useAppActions'
import { AppCard } from '@/features/apps/components/AppCard'
import { AppListRow } from '@/features/apps/components/AppListRow'

export const appsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: 'apps',
  component: AppsPage,
})

type ViewMode = 'grid' | 'list'

function AppsPage() {
  const { data: apps, isLoading } = useApps()
  const { isCreating, handleCreate } = useCreateApp()

  const [searchQuery, setSearchQuery] = useState('')
  const [viewMode, setViewMode] = useState<ViewMode>('grid')

  const filtered = useMemo(() => {
    if (!apps) return []
    if (!searchQuery.trim()) return apps
    const q = searchQuery.toLowerCase()
    return apps.filter((a) => a.name.toLowerCase().includes(q))
  }, [apps, searchQuery])

  return (
    <div className="h-full w-full overflow-y-auto bg-background">
      <div className="max-w-6xl mx-auto px-6 py-8">
        {/* Header */}
        <div className="flex items-center justify-between mb-8">
          <div>
            <h1 className="text-2xl font-bold text-foreground">Apps</h1>
            <p className="text-sm text-muted-foreground mt-1">
              Build interactive apps with drag-and-drop
            </p>
          </div>
          <Button onClick={handleCreate} disabled={isCreating}>
            <Plus size={16} className="mr-1.5" />
            New App
          </Button>
        </div>

        {/* Toolbar */}
        <div className="flex items-center gap-3 mb-6">
          <div className="relative flex-1 max-w-sm">
            <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
            <Input
              placeholder="Search apps..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="pl-9 h-9"
            />
          </div>
          <div className="flex items-center border border-border rounded-md">
            <Button
              variant="ghost"
              size="icon-sm"
              onClick={() => setViewMode('grid')}
              className={viewMode === 'grid' ? 'text-foreground' : 'text-muted-foreground'}
            >
              <LayoutGrid size={15} />
            </Button>
            <Button
              variant="ghost"
              size="icon-sm"
              onClick={() => setViewMode('list')}
              className={viewMode === 'list' ? 'text-foreground' : 'text-muted-foreground'}
            >
              <List size={15} />
            </Button>
          </div>
        </div>

        {/* Loading */}
        {isLoading && (
          <div className={viewMode === 'grid'
            ? 'grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4'
            : 'flex flex-col gap-2'
          }>
            {Array.from({ length: 6 }).map((_, i) => (
              <Skeleton key={i} className={viewMode === 'grid' ? 'h-36 rounded-lg' : 'h-14 rounded-md'} />
            ))}
          </div>
        )}

        {/* Empty state */}
        {!isLoading && filtered.length === 0 && (
          <div className="flex flex-col items-center justify-center py-20 text-center">
            <div className="w-14 h-14 rounded-full bg-muted flex items-center justify-center mb-4">
              <AppWindow size={24} className="text-muted-foreground" />
            </div>
            <h3 className="text-sm font-medium text-foreground mb-1">
              {searchQuery ? 'No apps match your search' : 'No apps yet'}
            </h3>
            <p className="text-xs text-muted-foreground mb-4 max-w-[240px]">
              {searchQuery
                ? 'Try a different search term'
                : 'Create your first app to get started with the visual builder'}
            </p>
            {!searchQuery && (
              <Button size="sm" onClick={handleCreate} disabled={isCreating}>
                <Plus size={14} className="mr-1" />
                Create App
              </Button>
            )}
          </div>
        )}

        {/* Grid view */}
        {!isLoading && filtered.length > 0 && viewMode === 'grid' && (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {filtered.map((app) => (
              <AppCard key={app.id} app={app} />
            ))}
          </div>
        )}

        {/* List view */}
        {!isLoading && filtered.length > 0 && viewMode === 'list' && (
          <div className="border border-border rounded-lg overflow-hidden">
            <div className="grid grid-cols-[1fr_140px_140px_40px] gap-4 px-4 py-2 bg-muted/50 text-[11px] font-medium text-muted-foreground uppercase tracking-wider">
              <span>Name</span>
              <span>Created</span>
              <span>Updated</span>
              <span />
            </div>
            {filtered.map((app) => (
              <AppListRow key={app.id} app={app} />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
