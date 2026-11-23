/**
 * The focus panel under the map: the full detail of the object picked on the map or in the sentence, with
 * its actions (Open editor / Run now / View runs, Test a route, Open data, Profile it / Email me a report …).
 */
import type { ReactNode } from 'react'
import { Link } from '@tanstack/react-router'
import { Code2, ExternalLink, Eye, History, LockKeyhole, Mail, Play, ScanSearch, Table2, X } from 'lucide-react'
import { MethodBadge } from '@/shared/components/api/MethodBadge'
import { Badge } from '@/shared/components/ui/badge'
import { Button } from '@/shared/components/ui/button'
import { StatusDot, runTone } from '@/shared/components/ui/status-dot'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/shared/components/ui/table'
import { Tip } from '@/shared/components/ui/tooltip'
import type { Route } from '@/shared/api/types'
import { useProjectCtx } from '../../ProjectContext'
import { useDatasetStarters } from '../../hooks/useSetupActions'
import { AUTH_LABEL, sortRoutes } from '../../lib/routes'
import { formatMs, pluralize, timeAgo } from '../../lib/format'
import type { StoryData } from '../hooks/useStoryData'
import type { useStoryActions } from '../hooks/useStoryActions'
import { IconTile, type EntityHue } from '@/shared/components/ui/surface'
import { datasetRefs, findDataset, formatBytes, formatCount, KIND_LABEL, routesCalledBy, routesOf, type ObjKind, type StoryObj } from '../lib/model'
import { KindIcon } from './KindIcon'

type Actions = ReturnType<typeof useStoryActions>

const KIND_TILE: Record<ObjKind, EntityHue> = { app: 'apps', route: 'runs', module: 'backend', workflow: 'flow', table: 'data', dataset: 'data' }

/**
 * The panel opens by growing its row and closes the same way (`closing`, then `onClosed`); picking another
 * object only cross-fades the content, so the map above never jumps twice.
 */
export function StoryFocus({
  obj,
  data,
  actions,
  closing,
  onClose,
  onClosed,
}: {
  obj: StoryObj
  data: StoryData
  actions: Actions
  closing: boolean
  onClose: () => void
  onClosed: () => void
}) {
  return (
    <div
      className="story-focus relative z-[2]"
      data-closing={closing || undefined}
      onAnimationEnd={(e) => {
        if (closing && e.target === e.currentTarget) onClosed()
      }}
      data-testid="story-focus"
      data-focus={obj.key}
    >
      <div className="overflow-hidden">
        <section key={obj.key} className="story-rise mt-5 border-t border-hairline pt-4" aria-label={`${KIND_LABEL[obj.kind]} ${obj.name}`}>
          {obj.kind === 'app' && <AppFocus id={obj.id} data={data} actions={actions} onClose={onClose} />}
          {obj.kind === 'route' && <RouteFocus id={obj.id} data={data} actions={actions} onClose={onClose} />}
          {obj.kind === 'module' && <ModuleFocus id={obj.id} data={data} actions={actions} onClose={onClose} />}
          {obj.kind === 'workflow' && <WorkflowFocus obj={obj} data={data} actions={actions} onClose={onClose} />}
          {obj.kind === 'table' && <TableFocus id={obj.id} data={data} onClose={onClose} />}
          {obj.kind === 'dataset' && <DatasetFocus id={obj.id} data={data} onClose={onClose} />}
        </section>
      </div>
    </div>
  )
}

function Head({ kind, title, children, onClose }: { kind: ObjKind; title: ReactNode; children?: ReactNode; onClose: () => void }) {
  return (
    <div className="flex flex-wrap items-center gap-2.5">
      <IconTile hue={KIND_TILE[kind]}>
        <KindIcon kind={kind} />
      </IconTile>
      <h3 className="flex min-w-0 flex-wrap items-center gap-2 text-[15px] leading-tight font-semibold tracking-[-0.01em]">{title}</h3>
      <span className="flex-1" />
      {children}
      <Button variant="ghost" size="icon-sm" aria-label="Close details" onClick={onClose} data-testid="story-focus-close">
        <X />
      </Button>
    </div>
  )
}

function RouteTable({ routes, data, actions }: { routes: Route[]; data: StoryData; actions: Actions }) {
  if (!routes.length) return <p className="mt-2 text-[12.5px] text-muted-foreground">No routes yet.</p>
  return (
    <div className="well mt-2.5 overflow-x-auto" data-scroll-x="allowed">
      <Table dense>
        <TableHeader>
          <TableRow className="hover:bg-transparent">
            <TableHead className="h-[34px] pl-4">Method</TableHead>
            <TableHead className="h-[34px]">Path</TableHead>
            <TableHead className="h-[34px]">Operation</TableHead>
            <TableHead className="h-[34px]">Auth</TableHead>
            <TableHead className="h-[34px]">Last call</TableHead>
            <TableHead className="h-[34px] text-right">Calls 24h</TableHead>
            <TableHead className="h-[34px] text-right">p50</TableHead>
            <TableHead className="w-10" />
          </TableRow>
        </TableHeader>
        <TableBody>
          {sortRoutes(routes).map((r) => {
            const st = data.model.routeStats.get(r.id)
            return (
              <TableRow key={r.id} data-testid={`route-row-${r.name}`}>
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
                <TableCell>
                  <Badge variant="outline">{AUTH_LABEL[r.auth] ?? r.auth}</Badge>
                </TableCell>
                <TableCell className="text-[12.5px]">
                  {st ? (
                    <span className="inline-flex items-center gap-1.5">
                      <StatusDot tone={runTone(st.last.status)} flat /> {st.last.status_code ?? st.last.status} · {timeAgo(st.last.start_time, data.now)}
                    </span>
                  ) : (
                    <span className="text-muted-foreground">no calls</span>
                  )}
                </TableCell>
                <TableCell className="text-right text-[12.5px] tabular-nums">{st?.calls24h ?? 0}</TableCell>
                <TableCell className="text-right text-[12.5px] tabular-nums">{st?.p50 != null ? formatMs(st.p50) : '—'}</TableCell>
                <TableCell>
                  <Tip label={`Test ${r.method} ${r.path}`}>
                    <Button variant="ghost" size="icon-xs" aria-label={`Test ${r.method} ${r.path}`} data-testid={`route-test-${r.name}`} onClick={() => actions.testRoute(r)}>
                      <Play />
                    </Button>
                  </Tip>
                </TableCell>
              </TableRow>
            )
          })}
        </TableBody>
      </Table>
    </div>
  )
}

function AppFocus({ id, data, actions, onClose }: { id: string; data: StoryData; actions: Actions; onClose: () => void }) {
  const { slug } = useProjectCtx()
  const app = data.apps.find((a) => a.id === id)
  if (!app) return null
  const ids = routesCalledBy(data.usage.get(app.id), data.routes)
  const called = data.routes.filter((r) => ids.includes(r.id))
  return (
    <>
      <Head
        kind="app"
        onClose={onClose}
        title={
          <>
            {app.name}
            <Badge variant={app.published_at ? 'success' : 'neutral'}>{app.published_at ? 'Published' : 'Draft'}</Badge>
          </>
        }
      >
        <Button variant="outline" size="sm" render={<Link to="/p/$project/apps/$appId" params={{ project: slug, appId: app.id }} />}>
          <ExternalLink /> Open
        </Button>
        {app.slug && (
          <Button variant="outline" size="sm" render={<a href={`/i/${slug}/${app.slug}`} target="_blank" rel="noreferrer" />}>
            <Eye /> Preview
          </Button>
        )}
      </Head>
      <p className="mt-0.5 text-[12.5px] text-muted-foreground">{called.length ? 'Routes this screen calls' : "This screen doesn't call a backend yet."}</p>
      {called.length ? (
        <RouteTable routes={called} data={data} actions={actions} />
      ) : (
        <div className="mt-2.5 flex flex-wrap gap-2">
          <Button variant="outline" size="sm" onClick={actions.newRoute}>
            Wire a route
          </Button>
          <Button variant="outline" size="sm" onClick={actions.newWorkflow}>
            Add a workflow behind it
          </Button>
        </div>
      )}
    </>
  )
}

function RouteFocus({ id, data, actions, onClose }: { id: string; data: StoryData; actions: Actions; onClose: () => void }) {
  const r = data.routes.find((x) => x.id === id)
  if (!r) return null
  return (
    <>
      <Head
        kind="route"
        onClose={onClose}
        title={
          <>
            <MethodBadge method={r.method} />
            <span className="font-mono">{r.path}</span>
          </>
        }
      >
        <Button variant="outline" size="sm" onClick={() => actions.testRoute(r)}>
          <Play /> Test
        </Button>
      </Head>
      <RouteTable routes={[r]} data={data} actions={actions} />
    </>
  )
}

function ModuleFocus({ id, data, actions, onClose }: { id: string; data: StoryData; actions: Actions; onClose: () => void }) {
  const { slug } = useProjectCtx()
  const m = data.modules.find((x) => x.id === id)
  if (!m) return null
  const rs = data.routes.filter((r) => r.handler.type === 'python' && r.handler.module_id === m.id)
  return (
    <>
      <Head
        kind="module"
        onClose={onClose}
        title={
          <>
            <span className="font-mono">{m.name}.py</span>
            <span className="text-[12.5px] font-normal text-muted-foreground">edited {timeAgo(m.updated_at, data.now)}</span>
          </>
        }
      >
        <Button variant="outline" size="sm" render={<Link to="/p/$project/backend/$module" params={{ project: slug, module: m.name }} />} data-testid="open-editor">
          <Code2 /> Open editor
        </Button>
      </Head>
      <RouteTable routes={rs} data={data} actions={actions} />
    </>
  )
}

function WorkflowFocus({ obj, data, actions, onClose }: { obj: StoryObj; data: StoryData; actions: Actions; onClose: () => void }) {
  const { slug, viewEnv } = useProjectCtx()
  const w = data.workflows.find((x) => x.id === obj.id)
  if (!w) return null
  const st = data.model.workflowStats.get(w.id)
  const detail = data.detailById.get(w.id)
  const handled = routesOf(w, data.routes)
  const secrets = data.model.locks.get(`workflow-${w.id}`) ?? []
  const touches = datasetRefs(detail?.definition)
    .map((ref) => findDataset(data.datasets, ref)?.name)
    .filter(Boolean)
  const trigger = obj.facts[1]?.text ?? '—'
  const reason = secrets[0]
    ? `${secrets[0]} is not set${(data.missing.find((m) => m.key === secrets[0])?.missing_in ?? []).length ? ` in ${data.missing.find((m) => m.key === secrets[0])!.missing_in.join(', ')}` : ''}`
    : st?.failed && st.last.error_count
      ? `${pluralize(st.last.error_count, 'step')} errored on the last run`
      : st?.failed
        ? 'the last run failed'
        : null
  return (
    <>
      <Head
        kind="workflow"
        onClose={onClose}
        title={
          <>
            {w.name}
            {st ? <Badge variant={st.failed ? 'destructive' : 'success'}>{st.failed ? 'Failing' : 'OK'}</Badge> : <Badge variant="neutral">No runs yet</Badge>}
          </>
        }
      >
        <Button variant="outline" size="sm" render={<Link to="/p/$project/workflows/$workflowId" params={{ project: slug, workflowId: w.id }} />} data-testid="story-wf-open">
          <Code2 /> Open editor
        </Button>
        <Button variant="outline" size="sm" disabled={actions.running === w.id} onClick={() => void actions.runWorkflow(w.id, w.name)} data-testid="story-wf-run">
          <Play /> Run now
        </Button>
        {/* the Runs page filters by route, not by workflow: a routed workflow shows its route's runs, any other
            opens its last run rather than every run in the project */}
        {handled[0] ? (
          <Button variant="outline" size="sm" render={<Link to="/p/$project/runs" params={{ project: slug }} search={{ route: handled[0] }} />} data-testid="story-wf-runs">
            <History /> View runs
          </Button>
        ) : (
          st && (
            <Button variant="outline" size="sm" render={<Link to="/p/$project/runs/$executionId" params={{ project: slug, executionId: st.last.id }} />} data-testid="story-wf-runs">
              <History /> Last run
            </Button>
          )
        )}
      </Head>
      {reason && st?.failed && (
        <div className="mt-2.5 flex flex-wrap items-center gap-2">
          <Badge variant="destructive">{reason}</Badge>
          {st.last.id && (
            <Button variant="ghost" size="xs" render={<Link to="/p/$project/runs/$executionId" params={{ project: slug, executionId: st.last.id }} />}>
              Open the failed run
            </Button>
          )}
        </div>
      )}
      <dl className="well mt-2.5 grid grid-cols-2 gap-x-6 gap-y-2 px-4 py-3 text-[12.5px] @min-[720px]/bt:grid-cols-3">
        <Fact label="Trigger">{trigger}</Fact>
        <Fact label="Last run">{st ? timeAgo(st.last.start_time, data.now) : '—'}</Fact>
        <Fact label="Runs 24h">{st?.calls24h ?? 0}</Fact>
        <Fact label="Steps">{detail ? detail.definition.nodes.length : w.node_count}</Fact>
        <Fact label="Touches">{touches.length ? touches.join(', ') : '—'}</Fact>
        <Fact label="Secrets">
          {secrets.length ? (
            <span className="flex flex-wrap items-center gap-1.5">
              {secrets.map((k) => (
                <Link
                  key={k}
                  to="/p/$project/secrets"
                  params={{ project: slug }}
                  search={{ create: k, env: data.missing.find((m) => m.key === k)?.missing_in[0] ?? viewEnv }}
                  className="inline-flex items-center gap-1 font-mono text-warning-text hover:underline"
                >
                  <LockKeyhole className="size-3" /> Set {k}
                </Link>
              ))}
            </span>
          ) : (
            '—'
          )}
        </Fact>
      </dl>
    </>
  )
}

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex min-w-0 gap-2">
      <dt className="w-[72px] shrink-0 text-muted-foreground">{label}</dt>
      <dd className="min-w-0 truncate">{children}</dd>
    </div>
  )
}

function TableFocus({ id, data, onClose }: { id: string; data: StoryData; onClose: () => void }) {
  const { slug } = useProjectCtx()
  const t = data.tables.find((x) => x.id === id)
  if (!t) return null
  return (
    <>
      <Head
        kind="table"
        onClose={onClose}
        title={
          <>
            <span className="font-mono">{t.name}</span>
            <Badge variant="neutral">table · {formatCount(t.row_count)} rows</Badge>
          </>
        }
      >
        <Button variant="outline" size="sm" render={<Link to="/p/$project/data/$table" params={{ project: slug, table: t.name }} />} data-testid="story-table-open">
          <Table2 /> Open data
        </Button>
      </Head>
      <p className="mt-1 text-[12.5px] text-muted-foreground">{pluralize(t.columns.length, 'column')}{t.columns.length ? `: ${t.columns.slice(0, 8).map((c) => c.name).join(', ')}${t.columns.length > 8 ? '…' : ''}` : ''}</p>
    </>
  )
}

function DatasetFocus({ id, data, onClose }: { id: string; data: StoryData; onClose: () => void }) {
  const { slug } = useProjectCtx()
  const ds = useDatasetStarters()
  const d = data.datasets.find((x) => x.id === id)
  if (!d) return null
  const users = data.workflows.filter((w) => datasetRefs(data.detailById.get(w.id)?.definition).some((ref) => findDataset(data.datasets, ref)?.id === d.id))
  const size = formatBytes(d.size_bytes)
  return (
    <>
      <Head
        kind="dataset"
        onClose={onClose}
        title={
          <>
            <span className="font-mono">{d.name}</span>
            <Badge variant="neutral">
              dataset{size ? ` · ${size}` : ''}
              {d.row_count != null ? ` · ${formatCount(d.row_count)} rows` : ''}
            </Badge>
          </>
        }
      >
        <Button variant="outline" size="sm" render={<Link to="/p/$project/datasets/$datasetId" params={{ project: slug, datasetId: d.id }} />}>
          <ExternalLink /> Open
        </Button>
        <Button variant="outline" size="sm" disabled={ds.busy === 'profile'} onClick={() => void ds.profile(d)} data-testid="story-dataset-profile">
          <ScanSearch /> Profile it
        </Button>
        <Button variant="outline" size="sm" disabled={ds.busy === 'report'} onClick={() => void ds.report(d)} data-testid="story-dataset-report">
          <Mail /> Email me a report
        </Button>
      </Head>
      <p className="mt-1 text-[12.5px] text-muted-foreground">Used by {users.length ? users.map((w) => w.name).join(', ') : 'no workflow yet'}</p>
    </>
  )
}
