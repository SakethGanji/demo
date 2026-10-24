/**
 * `/__design` (dev only): the component reference, rebuilt from the real components. It mirrors
 * `demo/design/index.html` section by section, then shows the new shared pieces. Compare it with the
 * mockup in both themes before page work (IMPLEMENTATION-PLAN stage A7).
 */
import { useState } from 'react'
import { createRoute, Link } from '@tanstack/react-router'
import { BookOpen, FolderX, KeyRound, LayoutGrid, Play, Plus, Settings2, SlidersHorizontal, Sparkles, Sun, Workflow, Check } from 'lucide-react'
import { toast } from 'sonner'
import { rootRoute } from './__root'
import { Button } from '@/shared/components/ui/button'
import { Badge } from '@/shared/components/ui/badge'
import { Kbd } from '@/shared/components/ui/kbd'
import { Input } from '@/shared/components/ui/input'
import { Textarea } from '@/shared/components/ui/textarea'
import { Switch } from '@/shared/components/ui/switch'
import { Checkbox } from '@/shared/components/ui/checkbox'
import { Field } from '@/shared/components/ui/field'
import { Tabs, TabsList, TabsTab } from '@/shared/components/ui/tabs'
import { Segmented } from '@/shared/components/ui/segmented'
import { StatusDot } from '@/shared/components/ui/status-dot'
import { Skeleton } from '@/shared/components/ui/skeleton'
import { EmptyState } from '@/shared/components/ui/empty-state'
import { Tip } from '@/shared/components/ui/tooltip'
import { MethodChip } from '@/shared/components/api/MethodBadge'
import { Callout, PageHeader, Progress, SectionHeader, Stat, Tile, Well } from '@/shared/components/ui/surface'
import { Roll } from '@/shared/components/ui/roll'
import { ActivityGrid, ActivityLegend, Sparkline, type ActivityDay } from '@/shared/components/ui/charts'
import { Snippet } from '@/shared/components/ui/snippet'
import { RoutePicker, SearchMenuList } from '@/shared/components/ui/search-menu'
import { Stepper, Beam } from '@/shared/components/ui/stepper'
import { DiffView, DiffApplied } from '@/shared/components/ui/diff-view'
import { StackTrace } from '@/shared/components/ui/stack-trace'
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from '@/shared/components/ui/dialog'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from '@/shared/components/ui/dropdown-menu'
import { Brand, ThemeToggle } from '@/features/project/components/TopBar'
import { AppThumb, SystemDiagram, type SystemDiagramHandle } from '@/shared/components/art/SystemDiagram'
import { useRef } from 'react'
import { GlassShowcase } from '@/shared/glass/GlassShowcase'

function Section({ title, children, className }: { title: string; children: React.ReactNode; className?: string }) {
  return (
    <section className="flex flex-col gap-3">
      <h2 className="text-[12.5px] font-medium text-muted-foreground">{title}</h2>
      <Tile className={className ?? 'flex flex-wrap items-center gap-3 p-5'}>{children}</Tile>
    </section>
  )
}

const DAYS: ActivityDay[] = Array.from({ length: 30 }, (_, i) => {
  const d = new Date(Date.UTC(2026, 8, 17 + i))
  const count = [0, 3, 9, 14, 6, 2, 1][(i * 5) % 7] + (i % 4) * 2
  return { date: d.toISOString().slice(0, 10), count, failed: i % 9 === 4 ? 1 : 0 }
})

const PREV = `@route.get("/refunds/stats")
def refund_stats(req):
    rows = db.table("refunds").all()
    return {"count": len(rows)}
`
const NEXT = `@route.get("/refunds/stats")
def refund_stats(req):
    rows = db.table("refunds").all()
    total = sum(r["amount"] for r in rows)
    return {"count": len(rows), "total": total}
`
const TRACE = `Traceback (most recent call last):
  File "/srv/sandbox/runtime/handler.py", line 40, in invoke
    return fn(req)
  File "/app/modules/refunds.py", line 26, in create_refund
    amount = body["amount"]
  File "/usr/lib/python3.12/site-packages/studio/db.py", line 12, in insert
KeyError: 'amount'`

function DesignReference() {
  const [seg, setSeg] = useState<'params' | 'query' | 'body' | 'headers'>('query')
  const [nav, setNav] = useState<'project' | 'workflows'>('project')
  const [env, setEnv] = useState<'dev' | 'prod'>('dev')
  const [runs, setRuns] = useState(128)
  const [route, setRoute] = useState('refundStats')
  const [applied, setApplied] = useState(false)
  const [beam, setBeam] = useState(false)
  const diagram = useRef<SystemDiagramHandle>(null)
  return (
    <div className="h-full overflow-y-auto" data-testid="design-reference">
      <div className="mx-auto flex max-w-[1052px] flex-col gap-9 px-4 py-10">
        <div className="flex flex-col gap-2">
          <div className="flex items-center gap-4">
            <Brand />
            <h1 className="text-[26px] font-[650] tracking-[-0.03em]">Component reference</h1>
            <div className="flex-1" />
            <ThemeToggle />
          </div>
          <p className="text-[13px] text-muted-foreground">
            The studio's real components on the design tokens (<code>src/index.css</code>). Compare with <code>demo/design/index.html</code>.
          </p>
        </div>

        <section className="flex flex-col gap-3">
          <h2 className="text-[12.5px] font-medium text-muted-foreground">Pages</h2>
          <div className="grid grid-cols-5 gap-3">
            {[
              ['Projects', 'home grid', '/projects'],
              ['Overview', 'project dashboard', '/projects'],
              ['Backend editor', 'code + test panel', '/projects'],
              ['Data', 'table grid', '/projects'],
              ['Runs', 'history table', '/projects'],
            ].map(([t, s, to]) => (
              <Tile key={t} interactive className="block px-4 py-4">
                <Link to={to} className="flex flex-col gap-1">
                  <span className="text-[13.5px] font-medium">{t}</span>
                  <span className="text-[12px] text-muted-foreground">{s}</span>
                </Link>
              </Tile>
            ))}
          </div>
        </section>

        <section className="flex flex-col gap-3" data-testid="design-recipes">
          <h2 className="text-[12.5px] font-medium text-muted-foreground">Glass recipes</h2>
          <div className="grid grid-cols-3 gap-4">
            <Tile className="flex flex-col gap-3 p-5">
              <SectionHeader title="Tile" sub="tile" />
              <p className="text-[12.5px] text-muted-foreground">Fill, rim and top highlight. No blur, no shadow. Hover to see the pointer highlight.</p>
              <Tile className="p-3 text-[12.5px]">A tile inside a tile becomes a well</Tile>
            </Tile>
            <Tile className="flex flex-col gap-3 p-5">
              <SectionHeader title="Well" sub="well" />
              <Well className="p-3 font-mono text-[12px] text-muted-foreground">GET /refunds/stats → 200</Well>
              <div className="flex items-center gap-2">
                <Segmented value={env} onChange={setEnv} options={[{ key: 'dev', label: 'dev' }, { key: 'prod', label: 'prod' }]} />
                <span className="text-[12px] text-muted-foreground">raise: the chosen one</span>
              </div>
            </Tile>
            <Tile className="relative flex h-[190px] flex-col overflow-hidden p-0">
              <div className="flex-1 overflow-y-auto px-5 pt-14 pb-4 text-[12.5px] leading-6 text-muted-foreground">
                {Array.from({ length: 10 }, (_, i) => (
                  <p key={i}>Content scrolls under the chrome bar, line {i + 1}.</p>
                ))}
              </div>
              <div className="glass-chrome absolute inset-x-0 top-0 flex h-11 items-center gap-2 px-4 text-[13px] font-semibold">Chrome</div>
            </Tile>
          </div>
          <div className="grid grid-cols-3 gap-4">
            <div className="glass-overlay flex flex-col gap-1 p-4">
              <span className="text-[13px] font-semibold">Overlay</span>
              <span className="text-[12.5px] text-muted-foreground">Menus, dialogs, sheets, toasts. Blur and rim.</span>
            </div>
            <Tile className="flex flex-col gap-3 p-5">
              <SectionHeader title="Field and pills" />
              <Input placeholder="A field holds a value" aria-label="Field demo" />
              <div className="flex flex-wrap gap-1.5">
                <Badge variant="success">Live</Badge>
                <Badge variant="warning">Draft</Badge>
                <Badge variant="error">Failed</Badge>
                <Badge variant="primary">Info</Badge>
                <Badge variant="neutral">Quiet</Badge>
                <Badge variant="outline">Outline</Badge>
              </div>
            </Tile>
            <Tile className="flex flex-col gap-3 p-5">
              <SectionHeader title="Buttons" />
              <div className="flex flex-wrap items-center gap-2">
                <Button>Primary</Button>
                <Button variant="outline">Glassy</Button>
                <Button variant="ghost">Ghost</Button>
                <Button variant="destructive">Danger</Button>
              </div>
              <div className="flex items-center gap-2 text-[12.5px] text-muted-foreground">
                <Switch defaultChecked aria-label="Switch demo" /> Switch
              </div>
            </Tile>
          </div>
        </section>

        <Section title="Buttons">
          <Button>Default</Button>
          <Button variant="outline">Outline</Button>
          <Button variant="secondary">Secondary</Button>
          <Button variant="ghost">Ghost</Button>
          <Button variant="destructive">Destructive</Button>
          <Button variant="link">Link</Button>
          <Button variant="ai">
            <Sparkles /> AI
          </Button>
          <Button disabled>Disabled</Button>
          <span className="mx-1 h-6 w-px bg-hairline" />
          <Button size="lg">Large</Button>
          <Button size="sm">Small</Button>
          <Button size="xs">Extra small</Button>
          <Tip label="Filters">
            <Button variant="outline" size="icon-sm" aria-label="Filters">
              <SlidersHorizontal />
            </Button>
          </Tip>
          <Tip label="Theme" kbd="⇧T">
            <Button variant="ghost" size="icon-sm" aria-label="Theme">
              <Sun />
            </Button>
          </Tip>
          <div className="basis-full" />
          <Button>
            <Plus /> Add row <Kbd>⌘↵</Kbd>
          </Button>
        </Section>

        <Section title="Badges · method chips · status dots · kbd">
          <Badge>Default</Badge>
          <Badge variant="secondary">Secondary</Badge>
          <Badge variant="outline">Outline</Badge>
          <Badge variant="destructive">Destructive</Badge>
          <Badge variant="success">Success</Badge>
          <Badge variant="warning">Warning</Badge>
          <Badge variant="ai">AI</Badge>
          <Badge variant="neutral">Neutral</Badge>
          <Badge variant="stub">stub · 501</Badge>
          <Badge variant="primary">Primary</Badge>
          <Badge variant="success">
            <StatusDot tone="ok" flat /> Healthy
          </Badge>
          <Badge variant="warning">
            <StatusDot tone="warn" /> Degraded
          </Badge>
          <span className="mx-1 h-5 w-px bg-hairline" />
          {['GET', 'POST', 'PUT', 'PATCH', 'DELETE'].map((m) => (
            <MethodChip key={m} method={m} />
          ))}
          <span className="mx-1 h-5 w-px bg-hairline" />
          <StatusDot tone="ok" />
          <StatusDot tone="warn" />
          <StatusDot tone="err" />
          <StatusDot tone="running" />
          <StatusDot tone="idle" />
          <span className="mx-1 h-5 w-px bg-hairline" />
          <Kbd>⌘K</Kbd>
          <Kbd>⇧</Kbd>
        </Section>

        <Section title="Inputs" className="grid grid-cols-[260px_1fr_auto] items-start gap-6 p-5">
          <div className="flex flex-col gap-4">
            <Field label="Name">
              <Input placeholder="Search projects…" />
            </Field>
            <Field label="Mono input">
              <Input defaultValue="/refunds/stats" className="font-mono" />
            </Field>
          </div>
          <Field label="Description" help="Shown on the projects grid.">
            <Textarea placeholder="What does this project do?" rows={3} />
          </Field>
          <div className="flex flex-col gap-3 pt-6">
            <label className="flex items-center gap-2 text-[13px]">
              <Switch defaultChecked /> Enabled
            </label>
            <label className="flex items-center gap-2 text-[13px]">
              <Switch /> Disabled
            </label>
            <label className="flex items-center gap-2 text-[13px]">
              <Checkbox defaultChecked /> Checked
            </label>
            <label className="flex items-center gap-2 text-[13px]">
              <Checkbox /> Unchecked
            </label>
          </div>
        </Section>

        <Section title="Tabs · segmented" className="flex flex-col gap-0 p-0 pb-5">
          <Tabs defaultValue="test">
            <TabsList className="px-3">
              <TabsTab value="test">
                <Play /> Test
              </TabsTab>
              <TabsTab value="ref">
                <BookOpen /> Reference
              </TabsTab>
              <TabsTab value="client">client.ts</TabsTab>
            </TabsList>
          </Tabs>
          <div className="flex flex-wrap items-center gap-3 px-5 pt-5">
            <Segmented
              value={seg}
              onChange={setSeg}
              options={[
                { key: 'params', label: 'Params' },
                { key: 'query', label: 'Query' },
                { key: 'body', label: 'Body' },
                { key: 'headers', label: 'Headers' },
              ]}
            />
            <Segmented
              value={nav}
              onChange={setNav}
              options={[
                { key: 'project', label: 'Project', icon: <LayoutGrid /> },
                { key: 'workflows', label: 'Workflows', icon: <Workflow />, count: 1 },
              ]}
            />
            <Segmented
              value={env}
              onChange={setEnv}
              options={[
                { key: 'dev', label: 'dev', icon: <StatusDot tone="ok" flat /> },
                { key: 'prod', label: 'prod', icon: <StatusDot tone="warn" flat /> },
              ]}
            />
          </div>
        </Section>

        <Section title="Callouts · AI bar · menu · dialog · tooltip" className="grid grid-cols-[380px_240px_1fr] items-start gap-6 p-5">
          <div className="flex flex-col gap-3">
            <Callout tone="warn" icon={<KeyRound />}>
              PAYMENTS_TOKEN is missing in prod
            </Callout>
            <Callout tone="ai" icon={<Sparkles />}>
              Generated 3 routes from your prompt
            </Callout>
            <div className="flex h-10 items-center gap-2 rounded-ctl bg-recess pr-1.5 pl-3" data-recess>
              <Sparkles className="size-3.5 text-hue-ai" />
              <input placeholder="Ask for a change…" className="min-w-0 flex-1 bg-transparent text-[13px] outline-none placeholder:text-muted-foreground" />
              <Button variant="ai" size="sm">
                Refine
              </Button>
            </div>
            <div>
              <Tip label="Viewing dev. Tables are shared by all environments." side="bottom">
                <Button variant="outline" size="sm">
                  Hover for a tooltip
                </Button>
              </Tip>
            </div>
          </div>
          <div className="glass-overlay p-0">
            <SearchMenuList
              placeholder="Find a project…"
              groups={[
                {
                  label: 'Projects',
                  items: [
                    { key: 'payments', label: 'Payments Ops', icon: <StatusDot tone="ok" flat />, sub: '3 routes', checked: true, onSelect: () => {} },
                    { key: 'default', label: 'Default project', icon: <StatusDot tone="idle" />, onSelect: () => {} },
                  ],
                },
                { items: [{ key: 'new', label: 'New project', icon: <Plus />, onSelect: () => {} }] },
              ]}
            />
          </div>
          <div className="flex flex-col items-start gap-3">
            <div className="glass-overlay flex w-full flex-col gap-4 p-5">
              <div className="text-[15px] font-semibold tracking-[-0.01em]">New project</div>
              <Field label="Name">
                <Input defaultValue="Payments Ops" />
              </Field>
              <Field label="Description">
                <Input placeholder="Optional" />
              </Field>
              <div className="flex justify-end gap-2">
                <Button variant="ghost">Cancel</Button>
                <Button>Create project</Button>
              </div>
            </div>
            <div className="flex gap-2">
              <Dialog>
                <DialogTrigger render={<Button variant="outline" size="sm" />}>Open a dialog</DialogTrigger>
                <DialogContent>
                  <DialogHeader>
                    <DialogTitle>New project</DialogTitle>
                  </DialogHeader>
                  <Field label="Name">
                    <Input defaultValue="Payments Ops" />
                  </Field>
                  <DialogFooter>
                    <Button>Create project</Button>
                  </DialogFooter>
                </DialogContent>
              </Dialog>
              <DropdownMenu>
                <DropdownMenuTrigger render={<Button variant="outline" size="sm" />}>Open a menu</DropdownMenuTrigger>
                <DropdownMenuContent className="w-48">
                  <DropdownMenuItem>
                    <Settings2 /> Settings
                  </DropdownMenuItem>
                  <DropdownMenuItem>
                    <Check /> Mark done
                  </DropdownMenuItem>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem variant="destructive">Delete</DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
              <Button variant="outline" size="sm" onClick={() => toast.success('Saved refunds.py', { description: '3 routes created · table refunds created' })}>
                Toast
              </Button>
            </div>
          </div>
        </Section>

        <Section title="Palette (live tokens)" className="flex flex-wrap gap-4 p-5">
          {['background', 'canvas', 'card', 'muted', 'border', 'primary', 'ai', 'success', 'warning', 'destructive', 'method-get', 'method-post', 'method-put', 'code-bg'].map((n) => (
            <div key={n} className="flex flex-col items-center gap-1.5">
              <div className="size-12 rounded-panel shadow-[inset_0_0_0_1px_var(--hairline)]" style={{ background: `var(--${n})` }} />
              <div className="font-mono text-[11px] text-muted-foreground">{n}</div>
            </div>
          ))}
          <span className="mx-2 w-px self-stretch bg-hairline" />
          {['backend', 'data', 'runs', 'apps', 'secrets', 'ai', 'flow', 'release'].map((n) => (
            <div key={n} className="flex flex-col items-center gap-1.5">
              <div className="size-12 rounded-panel" style={{ background: `var(--hue-${n})` }} />
              <div className="font-mono text-[11px] text-muted-foreground">{n}</div>
            </div>
          ))}
        </Section>

        <Section title="Empty state · skeleton · stat · progress" className="grid grid-cols-4 items-center gap-4 p-5">
          <EmptyState icon={<FolderX />} title="No tables yet" actions={<Button variant="outline" size="sm">New table</Button>} className="py-4">
            Create a table or let AI generate one.
          </EmptyState>
          <div className="flex flex-col gap-2">
            <Skeleton className="h-4 w-3/5" />
            <Skeleton className="h-3" />
            <Skeleton className="h-3 w-4/5" />
          </div>
          <Tile className="grid grid-cols-2 gap-4 p-4">
            <Stat label="Routes" value="3" />
            <Stat label="Runs · 24h" value={<Roll value={runs} />} meta="p50 113 ms" />
          </Tile>
          <Progress value={62} />
        </Section>

        <Section title="New · rolling digits · sparkline · 30-day activity" className="grid grid-cols-[1fr_1fr] gap-6 p-5">
          <div className="flex flex-col gap-4">
            <SectionHeader title="Activity" icon={<Play />} hue="runs" actions={<Button variant="ghost" size="xs" onClick={() => setRuns((r) => r + 1)}>+1 run</Button>} />
            <div className="text-[12.5px] text-muted-foreground">Runs · 24h</div>
            <Roll value={runs} className="text-[48px] leading-none font-semibold tracking-[-0.03em]" />
            <Sparkline values={[1, 1, 2, 4, 9, 12, 11, 13, 10, 8, 4, 7]} labels={['24h ago', '12h', 'now']} />
          </div>
          <Well className="flex flex-col gap-3 p-4">
            <div className="flex items-center justify-between">
              <span className="text-[12.5px]">
                <b className="font-semibold">30 days</b> <span className="text-muted-foreground">· 4 failed</span>
              </span>
              <ActivityLegend />
            </div>
            <ActivityGrid days={DAYS} />
          </Well>
        </Section>

        <Section title="New · snippet · route picker · page header" className="grid grid-cols-2 gap-6 p-5">
          <Snippet
            label="Python · 1 module · 3 routes · prod has no release yet"
            forms={[
              { key: 'url', label: 'URL', value: 'https://commandstudio.dev/api/p/payments-ops' },
              { key: 'curl', label: 'curl', lead: 'curl', value: 'curl https://commandstudio.dev/api/p/payments-ops/refunds/stats' },
              { key: 'python', label: 'Python', value: 'import requests\nrequests.get("https://commandstudio.dev/api/p/payments-ops/refunds/stats")' },
            ]}
          />
          <div className="flex flex-col gap-4">
            <RoutePicker
              value={route}
              onSelect={setRoute}
              routes={[
                { key: 'listRefunds', method: 'GET', path: '/refunds', module: 'refunds', sub: 'listRefunds' },
                { key: 'createRefund', method: 'POST', path: '/refunds', module: 'refunds', sub: 'createRefund' },
                { key: 'refundStats', method: 'GET', path: '/refunds/stats', module: 'refunds', sub: 'refundStats' },
              ]}
            />
            <PageHeader title="Runs" meta={<>18 in 24h · 7 failed · p50 113 ms</>} actions={<Button variant="outline" size="sm">Export</Button>} />
          </div>
        </Section>

        <Section title="New · release stepper · beam" className="grid grid-cols-2 gap-6 p-5">
          <Stepper
            steps={[
              { key: 'check', title: 'Check code', meta: '1 module parses · 3 routes', state: 'done' },
              {
                key: 'secrets',
                title: 'Secrets',
                meta: 'PAYMENTS_TOKEN is missing in prod',
                state: 'current',
                content: (
                  <div className="flex gap-2">
                    <Input placeholder="Paste the prod value" className="h-7 font-mono text-xs" />
                    <Button size="sm">Save</Button>
                  </div>
                ),
              },
              { key: 'routes', title: 'Review routes', meta: '3 routes change', state: 'pending' },
              { key: 'promote', title: 'Promote', meta: 'prod v1', state: 'pending' },
            ]}
          />
          <div className="relative flex items-center justify-center rounded-panel">
            <Beam on={beam} />
            <Button variant="outline" onClick={() => setBeam((b) => !b)}>
              {beam ? 'Stop the beam' : 'Start the beam'}
            </Button>
          </div>
        </Section>

        <Section title="New · system diagram (art) · app thumb" className="grid grid-cols-[1fr_200px] items-center gap-6 p-5">
          <Well className="p-4">
            <SystemDiagram
              ref={diagram}
              module="refunds.py"
              routes={[
                { key: 'listRefunds', method: 'GET', path: '/refunds' },
                { key: 'createRefund', method: 'POST', path: '/refunds', hot: true },
                { key: 'refundStats', method: 'GET', path: '/refunds/stats', hot: true },
              ]}
              table={{ name: 'refunds', rows: 3 }}
              app={{ name: 'Refunds dashboard' }}
              secret={{ key: 'PAYMENTS_TOKEN', env: 'prod' }}
            />
          </Well>
          <div className="flex flex-col gap-3">
            <AppThumb />
            <Button variant="outline" size="sm" onClick={() => { diagram.current?.pulse('route:createRefund'); diagram.current?.pulse('app', { delay: 560 }) }}>
              Send a packet
            </Button>
          </div>
        </Section>

        <Section title="New · AI change diff · stack trace" className="grid grid-cols-2 items-start gap-6 p-5">
          {applied ? (
            <DiffApplied summary="Added a total to refundStats" added={2} removed={1} onUndo={() => setApplied(false)} />
          ) : (
            <DiffView title="refunds.py" prev={PREV} next={NEXT} onApply={() => setApplied(true)} onDiscard={() => toast('Discarded')} />
          )}
          <StackTrace traceback={TRACE} onOpenFrame={(f) => toast(`Open ${f.file}:${f.line}`)} />
        </Section>

        <GlassShowcase />
      </div>
    </div>
  )
}

export const designRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/__design',
  component: DesignReference,
})
