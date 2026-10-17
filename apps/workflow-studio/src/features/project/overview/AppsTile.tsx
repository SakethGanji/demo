import { Link } from '@tanstack/react-router'
import { useQuery } from '@tanstack/react-query'
import { AppWindow, Plus } from 'lucide-react'
import { useProjectCtx } from '../ProjectContext'
import { span, TileHead } from './tiles'
import { AppThumb } from '@/shared/components/art/SystemDiagram'
import { Badge } from '@/shared/components/ui/badge'
import { Button } from '@/shared/components/ui/button'
import { Tile, Well } from '@/shared/components/ui/surface'
import { Tip } from '@/shared/components/ui/tooltip'
import { appApiApi } from '@/shared/api/endpoints'
import { qk } from '@/shared/api/queryKeys'
import type { ApiAppListItem } from '@/shared/lib/api'
import { cn } from '@/shared/lib/utils'

const MAX = 2

export function AppsTile({ apps, routesTotal, onCreateUi }: { apps: ApiAppListItem[]; routesTotal: number; onCreateUi: () => void }) {
  const { slug } = useProjectCtx()
  const first = apps[0]
  return (
    <Tile as="section" className={cn('flex flex-col', span.third)} data-testid="apps-card">
      <TileHead
        icon={<AppWindow />}
        hue="apps"
        title="Apps"
        actions={
          <div className="flex items-center gap-1">
            {apps.length > 0 && (
              <Tip label="New app">
                <Button variant="ghost" size="icon-sm" aria-label="New app" onClick={onCreateUi} data-testid="apps-new">
                  <Plus />
                </Button>
              </Tip>
            )}
            {first && (
              <Button variant="ghost" size="sm" render={<Link to="/p/$project/apps/$appId" params={{ project: slug, appId: first.id }} />} data-testid="apps-open-builder">
                Open builder
              </Button>
            )}
          </div>
        }
      />
      <div className="mx-2 mb-2 flex flex-1 flex-col gap-2">
        {apps.length === 0 ? (
          <Well className="flex-1 px-4 py-5 text-[13px] text-muted-foreground" data-testid="apps-empty">
            No apps yet.{' '}
            <button type="button" className="font-medium text-primary hover:underline" onClick={onCreateUi} data-testid="apps-empty-create-ui">
              Create UI from backend
            </button>
          </Well>
        ) : (
          <>
            {apps.slice(0, MAX).map((a) => (
              <AppRow key={a.id} app={a} routesTotal={routesTotal} />
            ))}
            {apps.length > MAX && (
              <Link to="/p/$project/apps" params={{ project: slug }} className="px-2 text-xs text-muted-foreground hover:text-foreground" data-testid="apps-more">
                +{apps.length - MAX} more
              </Link>
            )}
          </>
        )}
      </div>
    </Tile>
  )
}

function AppRow({ app, routesTotal }: { app: ApiAppListItem; routesTotal: number }) {
  const { slug } = useProjectCtx()
  const usage = useQuery({ queryKey: qk.apiUsage(app.id), queryFn: () => appApiApi.apiUsage(app.id) })
  const ops = [...new Set((usage.data ?? []).map((u) => u.operation))]
  const problems = (usage.data ?? []).filter((u) => u.status === 'missing' || u.status === 'changed')
  return (
    <Well className="group flex flex-1 items-center gap-4 p-3" data-testid={`app-row-${app.id}`}>
      <Link to="/p/$project/apps/$appId" params={{ project: slug, appId: app.id }} className="w-[104px] shrink-0 transition-transform duration-(--d-fast) ease-(--ease-out) group-hover:-translate-y-px" aria-label={`Open ${app.name}`}>
        <AppThumb />
      </Link>
      <div className="min-w-0 flex-1">
        <div className="truncate text-[13px] font-medium">{app.name}</div>
        <div className="mt-1.5 flex gap-1.5">
          {app.active ? <Badge variant="success">Published</Badge> : <Badge variant="neutral">Draft</Badge>}
          {app.current_version_number != null && <Badge variant="secondary">v{app.current_version_number}</Badge>}
        </div>
        <Tip label={ops.length ? `Uses ${ops.join(', ')}` : 'Calls no operations yet'}>
          <div className="mt-1.5 truncate text-xs text-muted-foreground">
            {ops.length > 0 ? `${ops.length} of ${routesTotal} operations` : 'Calls no operations yet'}
          </div>
        </Tip>
        {problems.length > 0 && (
          <div className="mt-1 truncate text-xs text-warning-text" data-testid="app-usage-warning">
            {problems.map((p) => `${p.operation} ${p.status}`).join(' · ')}
          </div>
        )}
      </div>
    </Well>
  )
}
