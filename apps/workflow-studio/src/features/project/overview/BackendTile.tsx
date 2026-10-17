import { Link, useNavigate } from '@tanstack/react-router'
import { Code2, Play, Server } from 'lucide-react'
import { useProjectCtx } from '../ProjectContext'
import { useModules, useRoutes } from '../hooks/useProjectData'
import { useRunStats } from '../hooks/useRunStats'
import { AUTH_LABEL, groupRoutes, moduleNameOf } from '../lib/routes'
import { formatMs, timeAgo } from '../lib/format'
import { span, TileHead } from './tiles'
import { MethodBadge } from '@/shared/components/api/MethodBadge'
import { Badge } from '@/shared/components/ui/badge'
import { Button } from '@/shared/components/ui/button'
import { StatusDot, runTone } from '@/shared/components/ui/status-dot'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/shared/components/ui/table'
import { Tile, Well } from '@/shared/components/ui/surface'
import { Tip } from '@/shared/components/ui/tooltip'
import { cn } from '@/shared/lib/utils'

export function BackendTile() {
  const { pid, slug } = useProjectCtx()
  const modules = useModules(pid)
  const routes = useRoutes(pid)
  const stats = useRunStats(pid)
  const navigate = useNavigate()
  const groups = groupRoutes(routes.data ?? [], modules.data)
  const P = { project: slug }
  return (
    <Tile as="section" className={cn('flex flex-col', span.routes)} data-testid="backend-card">
      <TileHead
        icon={<Server />}
        hue="backend"
        title="Backend"
        actions={
          modules.data?.[0] && (
            <Button
              variant="ghost"
              size="sm"
              render={<Link to="/p/$project/backend/$module" params={{ ...P, module: modules.data[0].name }} />}
              data-testid="open-editor"
            >
              <Code2 /> Open editor
            </Button>
          )
        }
      />
      <Well className="mx-2 mb-2 flex-1 overflow-x-auto" data-scroll-x="allowed">
        {groups.length === 0 ? (
          <div className="px-4 py-6 text-[13px] text-muted-foreground" data-testid="backend-card-empty">
            No routes yet.{' '}
            <Link to="/p/$project/backend/$module" params={{ ...P, module: '~new' }} search={{ generate: 1 }} className="text-ai-text hover:underline">
              Generate a backend
            </Link>{' '}
            or write one yourself.
          </div>
        ) : (
          <Table dense>
            <TableHeader>
              <TableRow className="hover:bg-transparent">
                <TableHead className="h-[34px] pl-4">Method</TableHead>
                <TableHead className="h-[34px]">Path</TableHead>
                <TableHead className="h-[34px]">Operation</TableHead>
                <TableHead className="h-[34px] @max-[1060px]/bt:hidden">Auth</TableHead>
                <TableHead className="h-[34px]">Last call · 24h</TableHead>
                <TableHead className="h-[34px] text-right">p50</TableHead>
                <TableHead className="w-10" />
              </TableRow>
            </TableHeader>
            <TableBody>
              {groups.map((g) => (
                <GroupRows key={g.key} label={g.label}>
                  {g.routes.map((r) => {
                    const st = stats.data?.routes.get(r.id)
                    const m = moduleNameOf(r, modules.data)
                    return (
                      <TableRow key={r.id} className="group/row" data-testid={`route-row-${r.name}`}>
                        <TableCell className="pl-4">
                          <MethodBadge method={r.method} />
                        </TableCell>
                        <TableCell className="font-mono text-[12.5px]">{r.path}</TableCell>
                        <TableCell className="font-mono text-[13px] text-muted-foreground">
                          {r.name}
                          {r.handler.type === 'stub' && (
                            <Badge variant="stub" className="ml-2">
                              stub
                            </Badge>
                          )}
                        </TableCell>
                        <TableCell className="@max-[1060px]/bt:hidden">
                          <Badge variant="outline">{AUTH_LABEL[r.auth] ?? r.auth}</Badge>
                        </TableCell>
                        <TableCell className="text-[12.5px]">
                          {st ? (
                            <span className="inline-flex items-center gap-1.5">
                              <StatusDot tone={runTone(st.last.status)} flat /> {st.last.status} · {timeAgo(st.last.start_time)}
                            </span>
                          ) : (
                            <span className="text-muted-foreground">no calls</span>
                          )}
                        </TableCell>
                        <TableCell className="text-right text-[12.5px] tabular-nums">{st?.p50 != null ? formatMs(st.p50) : '—'}</TableCell>
                        <TableCell>
                          <Tip label={`Test ${r.method} ${r.path}`}>
                            <Button
                              variant="ghost"
                              size="icon-xs"
                              className="opacity-0 transition-opacity duration-(--d-fast) group-hover/row:opacity-100 focus-visible:opacity-100"
                              aria-label={`Test ${r.method} ${r.path}`}
                              data-testid={`route-test-${r.name}`}
                              onClick={() =>
                                m
                                  ? navigate({ to: '/p/$project/backend/$module', params: { ...P, module: m }, search: { route: r.name } })
                                  : navigate({ to: '/p/$project/runs', params: P, search: { route: r.id } })
                              }
                            >
                              <Play />
                            </Button>
                          </Tip>
                        </TableCell>
                      </TableRow>
                    )
                  })}
                </GroupRows>
              ))}
            </TableBody>
          </Table>
        )}
      </Well>
    </Tile>
  )
}

function GroupRows({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <>
      <TableRow className="hover:bg-transparent">
        <TableCell colSpan={7} className="h-[30px] pl-4 font-mono text-xs text-muted-foreground">
          {label}
        </TableCell>
      </TableRow>
      {children}
    </>
  )
}
