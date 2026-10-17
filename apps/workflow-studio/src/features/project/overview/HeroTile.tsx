import { useEffect, useMemo, useRef } from 'react'
import { Link, useNavigate } from '@tanstack/react-router'
import { useQueries, useQueryClient } from '@tanstack/react-query'
import { Sparkles, WandSparkles } from 'lucide-react'
import { useProjectCtx } from '../ProjectContext'
import { useMissingSecrets, useModules, useProjectApps, useRoutes, useTables } from '../hooks/useProjectData'
import { useRunStats } from '../hooks/useRunStats'
import { sortRoutes } from '../lib/routes'
import { pluralize } from '../lib/format'
import { ProjectAvatar } from '../components/ProjectTopBar'
import { HealthBadge } from './HealthBadge'
import { span } from './tiles'
import { SystemDiagram, type SystemDiagramHandle } from '@/shared/components/art/SystemDiagram'
import { Button } from '@/shared/components/ui/button'
import { Snippet } from '@/shared/components/ui/snippet'
import { Tile, Well } from '@/shared/components/ui/surface'
import { appApiApi } from '@/shared/api/endpoints'
import { qk } from '@/shared/api/queryKeys'
import type { ProjectSummary } from '@/shared/api/types'
import { cn } from '@/shared/lib/utils'

export function HeroTile({ summary }: { summary: ProjectSummary }) {
  const { project, pid, slug } = useProjectCtx()
  const navigate = useNavigate()
  const modules = useModules(pid)
  const routes = useRoutes(pid)
  const tables = useTables(pid)
  const apps = useProjectApps(pid)
  const missing = useMissingSecrets(pid)
  const stats = useRunStats(pid)
  const qc = useQueryClient()
  const diagram = useRef<SystemDiagramHandle>(null)
  const P = { project: slug }

  const live = project.live_environment
  const liveRelease = summary.active_releases?.[live]
  const hasReleaseEnv = project.environments.some((e) => e.kind === 'release')
  const routeList = routes.data ?? []
  const moduleList = modules.data ?? []

  // the public base URL, a real GET route (no path params) for curl / Python
  const origin = typeof window === 'undefined' ? '' : window.location.origin
  const base = `${origin}/api/p/${slug}`
  const sample = sortRoutes(routeList).find((r) => r.method === 'GET' && !r.path.includes('{'))
  const sampleUrl = sample ? `${base}${sample.path}` : base
  const forms = [
    { key: 'url', label: 'URL', value: base },
    { key: 'curl', label: 'curl', lead: 'curl', value: `curl ${sampleUrl}` },
    { key: 'python', label: 'Python', value: `import requests\nrequests.get("${sampleUrl}")` },
  ]

  // hot wires: routes an app calls (from each app's API usage)
  const usage = useQueries({
    queries: (apps.data ?? []).map((a) => ({ queryKey: qk.apiUsage(a.id), queryFn: () => appApiApi.apiUsage(a.id) })),
  })
  const usageKey = usage.map((u) => u.dataUpdatedAt).join(',')
  const hotIds = useMemo(() => new Set(usage.flatMap((u) => (u.data ?? []).map((x) => x.route_id).filter((x): x is string => !!x))), [usageKey]) // eslint-disable-line react-hooks/exhaustive-deps
  const hotOps = useMemo(() => new Set(usage.flatMap((u) => (u.data ?? []).map((x) => x.operation))), [usageKey]) // eslint-disable-line react-hooks/exhaustive-deps
  const isHot = (r: { id: string; name: string }) => hotIds.has(r.id) || hotOps.has(r.name)

  const diagramRoutes = useMemo(() => {
    const rs = sortRoutes(routeList).map((r) => ({ key: r.name, method: r.method, path: r.path, hot: isHot(r) }))
    // keep path order; with more routes than the diagram draws, the hot ones go first
    return rs.length > 3 ? [...rs.filter((r) => r.hot), ...rs.filter((r) => !r.hot)] : rs
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [routeList, hotIds, hotOps])
  const table = tables.data?.[0]
  const app = apps.data?.[0]
  const miss = missing.data?.[0]

  // one packet per new run: compare run ids between refetches (the first load only records them)
  const seen = useRef<Set<string> | null>(null)
  const newest = stats.data?.newest
  useEffect(() => {
    if (!newest) return
    if (!seen.current) {
      seen.current = new Set(newest.map((r) => r.id))
      return
    }
    const fresh = newest.filter((r) => !seen.current!.has(r.id)).reverse()
    if (!fresh.length) return
    for (const r of newest) seen.current.add(r.id)
    fresh.slice(0, 4).forEach((r, i) => {
      const route = routeList.find((x) => x.id === r.route_id)
      if (!route) return
      const delay = i * 320
      diagram.current?.pulse(`route:${route.name}`, { color: r.failed ? 'error' : 'brand', delay })
      if (isHot(route) && !r.failed) diagram.current?.pulse('app', { delay: delay + 560 })
    })
    void qc.invalidateQueries({ queryKey: qk.summary(pid) })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [newest])

  const openCreateUi = () => navigate({ to: '/p/$project', params: P, search: { dialog: 'create-ui' } })

  return (
    <Tile as="section" className={cn('flex flex-col pt-5', span.hero)} aria-label={project.name} data-testid="overview-hero">
      <div className="flex min-w-0 flex-wrap items-center gap-3 px-5">
        <ProjectAvatar name={project.name} size={32} className="rounded-ctl" />
        <h1 className="min-w-0 truncate text-title leading-[1.1] font-[650] tracking-[-0.03em]" data-testid="project-name">
          {project.name}
        </h1>
        <HealthBadge summary={summary} />
        <div className="ml-auto flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            render={<Link to="/p/$project/backend/$module" params={{ ...P, module: '~new' }} search={{ generate: 1 }} />}
            data-testid="hero-generate"
          >
            <Sparkles className="text-ai" /> Generate
          </Button>
          <Button size="sm" onClick={openCreateUi} data-testid="hero-create-ui">
            <WandSparkles /> Create UI from backend
          </Button>
        </div>
      </div>
      <Snippet
        testId="base-url"
        className="mx-5 mt-4 gap-1.5 [&_[data-recess]]:min-h-[42px]"
        forms={forms}
        label={
          <>
            Python · {pluralize(moduleList.length, 'module')} · {pluralize(routeList.length, 'route')}
            {hasReleaseEnv && (
              <>
                {' · '}
                {liveRelease ? `${live} serves release #${liveRelease.number}` : `${live} has no release yet`}
              </>
            )}
          </>
        }
      />
      <Well className="relative mx-2 mt-4 mb-2 grid flex-1 place-items-center px-5 pt-4 pb-3" data-testid="system-diagram">
        <SystemDiagram
          ref={diagram}
          className="max-w-[640px]"
          module={moduleList[0] ? `${moduleList[0].name}.py` : undefined}
          routes={diagramRoutes}
          table={table ? { name: table.name, rows: table.row_count ?? 0 } : null}
          app={app ? { name: app.name } : null}
          secret={miss ? { key: miss.key, env: miss.missing_in[0] ?? live } : null}
        />
      </Well>
    </Tile>
  )
}
