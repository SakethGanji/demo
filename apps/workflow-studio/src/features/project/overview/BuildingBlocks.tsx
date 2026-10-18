/**
 * Overview row 1: the project's three optional building blocks — Interface (screens; "apps" in code), Backend
 * (modules and the routes they serve) and Workflows. Each shows a count and its most recent items; an empty block
 * offers to add the first one through the same dialog or route as the outline's `+`.
 */
import { Link, useNavigate } from '@tanstack/react-router'
import { AppWindow, FileCode2, Plus, Server, Workflow } from 'lucide-react'
import { useProjectCtx } from '../ProjectContext'
import { useModules, useProjectApps, useProjectWorkflows } from '../hooks/useProjectData'
import { byRecent, timeAgo } from '../lib/format'
import { Badge } from '@/shared/components/ui/badge'
import { Button } from '@/shared/components/ui/button'
import { IconTile, Tile, Well, WellList, WellRow, type EntityHue } from '@/shared/components/ui/surface'
import { Tip } from '@/shared/components/ui/tooltip'
import { cn } from '@/shared/lib/utils'

const RECENT = 3
const blockSpan = 'col-span-2 @min-[981px]/bt:col-span-4'
const headLinkCls = 'group flex min-w-0 items-center gap-2.5 rounded-ctl hover:text-foreground'
const itemLinkCls = 'flex min-w-0 flex-1 items-center gap-2 text-foreground/90 hover:text-foreground [&>svg]:size-3.5 [&>svg]:shrink-0'

type LinkSlot = (props: { className: string; 'data-testid': string; children: React.ReactNode }) => React.ReactElement

/** One building block: a header link (icon, title, count), a `+`, and its recent items or an "Add your first …" prompt. */
function BlockTile({
  testId,
  hue,
  icon,
  title,
  count,
  openTestId,
  openTip,
  open,
  noun,
  onNew,
  children,
}: {
  testId: string
  hue: EntityHue
  icon: React.ReactNode
  title: string
  count: number
  openTestId: string
  openTip: string
  open: LinkSlot
  /** "interface", "module", "workflow": used in the new/add labels */
  noun: string
  onNew: () => void
  children: React.ReactNode
}) {
  return (
    <Tile as="section" className={cn('flex min-w-0 flex-col', blockSpan)} data-testid={testId}>
      <div className="flex h-12 shrink-0 items-center gap-2 px-5">
        <Tip label={openTip}>
          {open({
            className: headLinkCls,
            'data-testid': openTestId,
            children: (
              <>
                <IconTile hue={hue}>{icon}</IconTile>
                <h3 className="text-panel-title font-semibold tracking-[-0.01em]">{title}</h3>
                <span className="text-[13px] text-muted-foreground tabular-nums" data-testid={`${openTestId}-value`}>
                  {count}
                </span>
              </>
            ),
          })}
        </Tip>
        <div className="flex-1" />
        {count > 0 && (
          <Tip label={`New ${noun}`}>
            <Button variant="ghost" size="icon-sm" aria-label={`New ${noun}`} onClick={onNew} data-testid={`${testId}-new`}>
              <Plus />
            </Button>
          </Tip>
        )}
      </div>
      <div className="mx-2 mb-2 flex flex-1 flex-col">
        {count === 0 ? (
          <Well className="flex flex-1 items-center px-4 py-4">
            <Button variant="outline" size="sm" onClick={onNew} data-testid={`${testId}-add`}>
              <Plus /> Add your first {noun}
            </Button>
          </Well>
        ) : (
          <WellList className="flex-1">{children}</WellList>
        )}
      </div>
    </Tile>
  )
}

function ItemMeta({ children }: { children: React.ReactNode }) {
  return <span className="shrink-0 text-[11.5px] text-muted-foreground">{children}</span>
}

export function BuildingBlocks() {
  const { pid, slug } = useProjectCtx()
  const P = { project: slug }
  const navigate = useNavigate()
  const apps = useProjectApps(pid)
  const modules = useModules(pid)
  const workflows = useProjectWorkflows(pid)
  const appList = apps.data ?? []
  const moduleList = modules.data ?? []
  const workflowList = workflows.data ?? []
  const a0 = appList[0]
  const m0 = moduleList[0]
  const openDialog = (dialog: 'create-ui' | 'new-module') => navigate({ to: '/p/$project', params: P, search: { dialog } })
  const newWorkflow = () => navigate({ to: '/p/$project/workflows/$workflowId', params: { ...P, workflowId: 'new' } })

  return (
    <div className="col-span-full grid grid-cols-subgrid gap-4" data-testid="building-blocks">
      <BlockTile
        testId="block-interface"
        hue="apps"
        icon={<AppWindow />}
        title="Interface"
        count={appList.length}
        // stat-apps / stat-apps-value: the count that used to sit in the stat strip
        openTestId="stat-apps"
        openTip={a0 ? `Open ${a0.name}` : 'New interface'}
        open={(p) =>
          a0 ? (
            <Link to="/p/$project/apps/$appId" params={{ ...P, appId: a0.id }} {...p} />
          ) : (
            // `/apps` only redirects back to the Overview, so with no app yet the header opens the create dialog
            <Link to="/p/$project" params={P} search={{ dialog: 'create-ui' }} {...p} />
          )
        }
        noun="interface"
        onNew={() => openDialog('create-ui')}
      >
        {byRecent(appList)
          .slice(0, RECENT)
          .map((a) => (
            <WellRow key={a.id}>
              <Link to="/p/$project/apps/$appId" params={{ ...P, appId: a.id }} className={itemLinkCls} data-testid={`block-interface-item-${a.id}`}>
                <AppWindow className="text-hue-apps" /> <span className="truncate">{a.name}</span>
              </Link>
              {a.active ? <Badge variant="success">Published</Badge> : <Badge variant="neutral">Draft</Badge>}
            </WellRow>
          ))}
      </BlockTile>
      <BlockTile
        testId="block-backend"
        hue="backend"
        icon={<Server />}
        title="Backend"
        count={moduleList.length}
        openTestId="block-backend-open"
        openTip={m0 ? `Open ${m0.name}.py` : 'Generate a module'}
        open={(p) => (
          <Link to="/p/$project/backend/$module" params={{ ...P, module: m0?.name ?? '~new' }} search={m0 ? undefined : { generate: 1 }} {...p} />
        )}
        noun="module"
        onNew={() => openDialog('new-module')}
      >
        {byRecent(moduleList)
          .slice(0, RECENT)
          .map((m) => (
            <WellRow key={m.id}>
              <Link to="/p/$project/backend/$module" params={{ ...P, module: m.name }} className={itemLinkCls} data-testid={`block-backend-item-${m.id}`}>
                <FileCode2 className="text-hue-backend" /> <span className="truncate font-mono text-[12px]">{m.name}.py</span>
              </Link>
              <ItemMeta>{m.route_count != null ? `${m.route_count} API${m.route_count === 1 ? '' : 's'}` : timeAgo(m.updated_at)}</ItemMeta>
            </WellRow>
          ))}
      </BlockTile>
      <BlockTile
        testId="block-workflows"
        hue="flow"
        icon={<Workflow />}
        title="Workflows"
        count={workflowList.length}
        openTestId="block-workflows-open"
        openTip="All workflows"
        open={(p) => <Link to="/p/$project/workflows" params={P} {...p} />}
        noun="workflow"
        onNew={newWorkflow}
      >
        {byRecent(workflowList)
          .slice(0, RECENT)
          .map((w) => (
            <WellRow key={w.id}>
              <Link to="/p/$project/workflows/$workflowId" params={{ ...P, workflowId: w.id }} className={itemLinkCls} data-testid={`block-workflows-item-${w.id}`}>
                <Workflow className="text-hue-flow" /> <span className="truncate">{w.name}</span>
              </Link>
              <ItemMeta>{timeAgo(w.updated_at)}</ItemMeta>
            </WellRow>
          ))}
      </BlockTile>
      <p className="col-span-full -mt-1 px-1 text-xs text-muted-foreground" data-testid="building-blocks-note">
        Interface calls your routes · a route runs backend code or a workflow · backend code can call workflows
      </p>
    </div>
  )
}
