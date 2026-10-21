/**
 * The technical plan, section by section, each collapsible: Data model, Modules & routes, Screen wiring,
 * Integrations & secrets, Environments and NFR. A section with issues starts open. With `editable` the names, types,
 * required / unique / enum of columns, the module of each route and the routes of each screen are edited inline; every
 * edit goes through `onChange` with a new body (the page saves it). Validation issues show under the item their path
 * names. The requirements themselves (assumptions, acceptance, answers) are in PlanDocument.
 */
import type { ReactNode } from 'react'
import { AlertTriangle, AppWindow, Boxes, ChevronRight, Database, Globe, KeyRound, Ruler } from 'lucide-react'
import { Badge } from '@/shared/components/ui/badge'
import { Checkbox } from '@/shared/components/ui/checkbox'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/shared/components/ui/collapsible'
import { Input } from '@/shared/components/ui/input'
import { Callout, IconTile, Tile, Well, WellList, WellRow, type EntityHue } from '@/shared/components/ui/surface'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/shared/components/ui/table'
import { issuesAt, splitList } from './planLogic'
import { COLUMN_TYPES, type PlanBody, type PlanIssue } from './planTypes'

type Edit = (fn: (b: PlanBody) => void) => void

export function IssueList({ issues, testId }: { issues: PlanIssue[]; testId?: string }) {
  if (!issues.length) return null
  return (
    <div className="mt-2 flex flex-col gap-1" data-testid={testId}>
      {issues.map((i, n) => (
        <Callout key={`${i.code}-${i.path}-${n}`} tone={i.severity === 'error' ? 'error' : 'warn'} icon={<AlertTriangle />}>
          <span className="font-mono text-[11.5px]">{i.code}</span> {i.message}
        </Callout>
      ))}
    </div>
  )
}

function Section({
  title,
  sub,
  icon,
  hue,
  children,
  testId,
  issues = [],
}: {
  title: string
  sub?: ReactNode
  icon: ReactNode
  hue: EntityHue
  children: ReactNode
  testId: string
  issues?: PlanIssue[]
}) {
  const errors = issues.filter((i) => i.severity === 'error').length
  return (
    <Collapsible defaultOpen={issues.length > 0} render={<Tile className="p-1.5" data-testid={testId} />}>
      <CollapsibleTrigger
        className="group/sec flex w-full min-w-0 items-center gap-2.5 rounded-ctl px-2.5 py-2 text-left outline-none hover:bg-(--hover-fill) focus-visible:shadow-focus"
        data-testid={`${testId}-toggle`}
      >
        <IconTile hue={hue}>{icon}</IconTile>
        <span className="text-panel-title font-semibold tracking-[-0.01em]">{title}</span>
        {sub ? <span className="min-w-0 truncate text-[12.5px] text-muted-foreground">{sub}</span> : null}
        <span className="flex-1" />
        {issues.length ? (
          <Badge variant={errors ? 'error' : 'warning'}>
            {errors ? `${errors} error${errors === 1 ? '' : 's'}` : `${issues.length} warning${issues.length === 1 ? '' : 's'}`}
          </Badge>
        ) : null}
        <ChevronRight className="size-4 shrink-0 text-muted-foreground transition-transform duration-(--d-fast) group-data-[panel-open]/sec:rotate-90" aria-hidden />
      </CollapsibleTrigger>
      <CollapsibleContent className="flex flex-col gap-4 px-2.5 pt-2 pb-2.5">{children}</CollapsibleContent>
    </Collapsible>
  )
}

const under = (issues: PlanIssue[] | undefined, ...prefixes: string[]) =>
  (issues ?? []).filter((i) => prefixes.some((p) => i.path === p || i.path.startsWith(p.endsWith('.') ? p : `${p}.`)))

const Mono = ({ children }: { children: ReactNode }) => <span className="font-mono text-[12px]">{children}</span>
const Muted = ({ children }: { children: ReactNode }) => <span className="text-[12px] text-muted-foreground">{children}</span>

function Cell({ editable, value, onChange, label, mono, placeholder }: { editable: boolean; value: string; onChange: (v: string) => void; label: string; mono?: boolean; placeholder?: string }) {
  if (!editable) return mono ? <Mono>{value || '·'}</Mono> : <span className="text-[12.5px]">{value || '·'}</span>
  return (
    <Input
      className={mono ? 'h-7 font-mono text-[12px]' : 'h-7 text-[12.5px]'}
      value={value}
      placeholder={placeholder}
      aria-label={label}
      onChange={(e) => onChange(e.target.value)}
    />
  )
}

export function PlanSections({ body, issues, editable, onChange }: { body: PlanBody; issues?: PlanIssue[]; editable: boolean; onChange: (b: PlanBody) => void }) {
  const edit: Edit = (fn) => {
    const next = structuredClone(body)
    fn(next)
    onChange(next)
  }
  return (
    <>
      <DataSection body={body} issues={issues} editable={editable} edit={edit} />
      <ModulesSection body={body} issues={issues} editable={editable} edit={edit} />
      <ScreensSection body={body} issues={issues} editable={editable} edit={edit} />
      <IntegrationsSection body={body} issues={issues} />
      <EnvironmentsSection body={body} issues={issues} />
      <Section title="Non-functional rules" sub="paging, errors, performance" icon={<Ruler />} hue="release" testId="plan-section-nfr">
        {Object.keys(body.nfr).length ? (
          <Well className="p-3">
            <pre className="font-mono text-[11.5px] whitespace-pre-wrap">{JSON.stringify(body.nfr, null, 2)}</pre>
          </Well>
        ) : (
          <Muted>None stated.</Muted>
        )}
      </Section>
    </>
  )
}

function DataSection({ body, issues, editable, edit }: { body: PlanBody; issues?: PlanIssue[]; editable: boolean; edit: Edit }) {
  const tables = body.data.tables
  return (
    <Section title="Data model" sub={`${tables.length} table${tables.length === 1 ? '' : 's'}`} icon={<Database />} hue="data" testId="plan-section-data" issues={under(issues, 'data.tables.')}>
      {tables.map((t, ti) => {
        const tp = `data.tables.${t.name}`
        return (
          <div key={`${ti}-${t.name}`} data-testid={`plan-table-${t.name}`}>
            <div className="flex flex-wrap items-center gap-2">
              <Mono>{t.name}</Mono>
              {t.owner ? <Badge variant="neutral">owner {t.owner}</Badge> : <Badge variant="warning">no owner</Badge>}
              {t.writers.length ? <Muted>also written by {t.writers.join(', ')}</Muted> : null}
              <Muted>
                retention {t.retention.policy}
                {t.retention.value != null ? ` ${t.retention.value}` : ''}
              </Muted>
              {t.state_machine ? (
                <Muted>
                  {t.state_machine.field}: {t.state_machine.transitions.map((x) => `${x.from}→${x.to}`).join(', ')}
                </Muted>
              ) : null}
            </div>
            {t.description ? <div className="mt-0.5 text-[12.5px] text-muted-foreground">{t.description}</div> : null}
            <Well className="mt-2 overflow-x-auto p-0">
              <Table dense>
                <TableHeader>
                  <TableRow>
                    <TableHead>Column</TableHead>
                    <TableHead>Type</TableHead>
                    <TableHead>Required</TableHead>
                    <TableHead>Unique</TableHead>
                    <TableHead>Values</TableHead>
                    <TableHead>Points at</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {t.columns.map((c, ci) => (
                    <TableRow key={ci} data-testid={`plan-column-${t.name}-${c.name}`}>
                      <TableCell>
                        <Cell editable={editable} mono label={`${t.name} column ${ci + 1} name`} value={c.name}
                          onChange={(v) => edit((b) => { b.data.tables[ti].columns[ci].name = v })} />
                      </TableCell>
                      <TableCell>
                        {editable ? (
                          <select
                            className="field h-7 px-1.5 text-[12px]"
                            aria-label={`${t.name}.${c.name} type`}
                            value={c.type}
                            onChange={(e) => edit((b) => { b.data.tables[ti].columns[ci].type = e.target.value })}
                          >
                            {COLUMN_TYPES.map((x) => (
                              <option key={x} value={x}>{x}</option>
                            ))}
                          </select>
                        ) : (
                          <Mono>{c.type}</Mono>
                        )}
                      </TableCell>
                      <TableCell>
                        <Checkbox checked={c.required} disabled={!editable} aria-label={`${t.name}.${c.name} required`}
                          onCheckedChange={(v) => edit((b) => { b.data.tables[ti].columns[ci].required = !!v })} />
                      </TableCell>
                      <TableCell>
                        <Checkbox checked={!!c.unique} disabled={!editable} aria-label={`${t.name}.${c.name} unique`}
                          onCheckedChange={(v) => edit((b) => { b.data.tables[ti].columns[ci].unique = !!v })} />
                      </TableCell>
                      <TableCell>
                        <Cell editable={editable} label={`${t.name}.${c.name} values`} value={(c.enum ?? []).join(', ')} placeholder="any"
                          onChange={(v) => edit((b) => {
                            const col = b.data.tables[ti].columns[ci]
                            const vals = splitList(v)
                            if (vals.length) col.enum = vals
                            else delete col.enum
                          })} />
                      </TableCell>
                      <TableCell>{c.ref ? <Mono>{c.ref}</Mono> : <Muted>·</Muted>}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </Well>
            <IssueList issues={issuesAt(issues, tp)} testId={`plan-issues-${t.name}`} />
          </div>
        )
      })}
    </Section>
  )
}

function ModulesSection({ body, issues, editable, edit }: { body: PlanBody; issues?: PlanIssue[]; editable: boolean; edit: Edit }) {
  const keys = body.modules.map((m) => m.key)
  const orphan = body.routes.filter((r) => !keys.includes(r.module))
  return (
    <Section title="Modules & routes" sub={`${body.modules.length} modules · ${body.routes.length} routes`} icon={<Boxes />} hue="backend" testId="plan-section-modules" issues={under(issues, 'modules.', 'routes.', 'build_order')}>
      {[...body.modules.map((m) => ({ key: m.key, m })), ...(orphan.length ? [{ key: '', m: null }] : [])].map(({ key, m }) => {
        const routes = body.routes.map((r, ri) => ({ r, ri })).filter(({ r }) => (m ? r.module === key : !keys.includes(r.module)))
        return (
          <div key={key || '~none'} data-testid={`plan-module-${key || 'none'}`}>
            <div className="flex flex-wrap items-center gap-2">
              <Mono>{m ? key : 'routes without a module'}</Mono>
              {m?.kind === 'shared' ? <Badge variant="ai">shared</Badge> : null}
              {m?.depends_on.length ? <Muted>depends on {m.depends_on.join(', ')}</Muted> : null}
              {m?.owns.length ? <Muted>owns {m.owns.join(', ')}</Muted> : null}
            </div>
            {m?.purpose ? <div className="mt-0.5 text-[12.5px] text-muted-foreground">{m.purpose}</div> : null}
            {m?.helpers.length ? (
              <div className="mt-1 flex flex-wrap gap-1.5">
                {m.helpers.map((h) => (
                  <Badge key={h.name} variant="neutral" className="font-mono">{h.signature || h.name}</Badge>
                ))}
              </div>
            ) : null}
            {routes.length ? (
              <WellList className="mt-2">
                {routes.map(({ r, ri }) => (
                  <WellRow key={`${ri}-${r.key}`} data-testid={`plan-route-${r.key}`}>
                    <span className="w-56 shrink-0 truncate font-mono text-[12px]">{r.key}</span>
                    <span className="min-w-0 flex-1 truncate text-muted-foreground">{r.purpose}</span>
                    {editable ? (
                      <select
                        className="field h-7 px-1.5 text-[12px]"
                        aria-label={`${r.key} module`}
                        value={keys.includes(r.module) ? r.module : ''}
                        onChange={(e) => edit((b) => { b.routes[ri].module = e.target.value })}
                      >
                        {!keys.includes(r.module) ? <option value="">(none)</option> : null}
                        {keys.map((k) => (
                          <option key={k} value={k}>{k}</option>
                        ))}
                      </select>
                    ) : null}
                  </WellRow>
                ))}
              </WellList>
            ) : m && m.kind !== 'shared' ? (
              <div className="mt-1"><Muted>No routes.</Muted></div>
            ) : null}
            {m ? <IssueList issues={issuesAt(issues, `modules.${key}`)} /> : null}
            <IssueList issues={routes.flatMap(({ r }) => issuesAt(issues, `routes.${r.key}`))} />
          </div>
        )
      })}
      <div className="flex flex-wrap items-center gap-2" data-testid="plan-build-order-preview">
        <Muted>Build order</Muted>
        {body.build_order.map((k, n) => (
          <Badge key={`${n}-${k}`} variant="neutral" className="font-mono">{n + 1}. {k}</Badge>
        ))}
      </div>
      <IssueList issues={issuesAt(issues, 'build_order')} />
    </Section>
  )
}

function ScreensSection({ body, issues, editable, edit }: { body: PlanBody; issues?: PlanIssue[]; editable: boolean; edit: Edit }) {
  return (
    <Section title="Screen wiring" sub="which routes each screen calls" icon={<AppWindow />} hue="apps" testId="plan-section-screens" issues={under(issues, 'apps.')}>
      {body.apps.length === 0 ? <Muted>No apps planned.</Muted> : null}
      {body.apps.map((a, ai) => (
        <div key={a.key} data-testid={`plan-app-${a.key}`}>
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-[13px] font-medium">{a.name}</span>
            <Mono>{a.key}</Mono>
            {a.app_id ? <Badge variant="success">built</Badge> : <Badge variant="neutral">not built</Badge>}
            {a.users ? <Muted>for {a.users}</Muted> : null}
          </div>
          <WellList className="mt-2">
            {a.screens.map((s, si) => (
              <WellRow key={`${si}-${s.name}`} className="flex-col items-stretch gap-1" data-testid={`plan-screen-${s.name}`}>
                <div className="flex items-center gap-2">
                  <span className="font-medium">{s.name}</span>
                  <span className="min-w-0 flex-1 truncate text-muted-foreground">{s.purpose}</span>
                </div>
                <Cell editable={editable} mono label={`${s.name} routes`} value={s.routes.join(', ')} placeholder="METHOD /path, …"
                  onChange={(v) => edit((b) => { b.apps[ai].screens[si].routes = splitList(v) })} />
                {Object.keys(s.states).length ? (
                  <Muted>{Object.entries(s.states).map(([k, v]) => `${k}: ${v}`).join(' · ')}</Muted>
                ) : null}
                <IssueList issues={issuesAt(issues, `apps.${a.key}.screens.${s.name}`)} />
              </WellRow>
            ))}
          </WellList>
          <IssueList issues={issuesAt(issues, `apps.${a.key}`).filter((i) => !i.path.includes('.screens.'))} />
        </div>
      ))}
    </Section>
  )
}

function IntegrationsSection({ body, issues }: { body: PlanBody; issues?: PlanIssue[] }) {
  return (
    <Section title="Integrations & secrets" sub="contracts, hosts, smoke runs" icon={<KeyRound />} hue="secrets" testId="plan-section-integrations" issues={under(issues, 'integrations.', 'secrets.')}>
      {body.integrations.length === 0 && body.secrets.length === 0 ? <Muted>No integrations or secrets.</Muted> : null}
      {body.integrations.length ? (
        <WellList>
          {body.integrations.map((i) => (
            <WellRow key={i.key} className="flex-col items-stretch gap-1" data-testid={`plan-integration-${i.key}`}>
              <div className="flex flex-wrap items-center gap-2">
                <Mono>{i.key}</Mono>
                <Badge variant="neutral">{i.kind}</Badge>
                {i.job_types.length ? <Muted>job types {i.job_types.join(', ')}</Muted> : null}
                {i.secrets.length ? <Muted>uses {i.secrets.join(', ')}</Muted> : null}
                {i.smoke ? <Badge variant="neutral">smoke run planned</Badge> : null}
              </div>
              {i.purpose ? <Muted>{i.purpose}</Muted> : null}
              <IssueList issues={issuesAt(issues, `integrations.${i.key}`)} />
            </WellRow>
          ))}
        </WellList>
      ) : null}
      {body.secrets.length ? (
        <WellList>
          {body.secrets.map((s) => (
            <WellRow key={s.name} className="flex-col items-stretch gap-1" data-testid={`plan-secret-${s.name}`}>
              <div className="flex flex-wrap items-center gap-2">
                <Mono>{s.name}</Mono>
                <Muted>{s.allowed_hosts.length ? `hosts ${s.allowed_hosts.join(', ')}` : 'no allowed hosts'}</Muted>
                {s.environments.length ? <Muted>in {s.environments.join(', ')}</Muted> : null}
              </div>
              <IssueList issues={issuesAt(issues, `secrets.${s.name}`)} />
            </WellRow>
          ))}
        </WellList>
      ) : null}
    </Section>
  )
}

function EnvironmentsSection({ body, issues }: { body: PlanBody; issues?: PlanIssue[] }) {
  return (
    <Section title="Environments" sub={`${body.environments.length} stated`} icon={<Globe />} hue="release" testId="plan-section-environments" issues={under(issues, 'environments')}>
      {body.environments.length ? (
        <WellList>
          {body.environments.map((e) => (
            <WellRow key={e.name} className="flex-col items-stretch gap-1">
              <div className="flex flex-wrap items-center gap-2">
                <Mono>{e.name}</Mono>
                {e.live ? <Badge variant="warning">live</Badge> : null}
                <Muted>{e.base_url || 'no base URL'}</Muted>
                {e.refused_actions.length ? <Muted>refuses {e.refused_actions.join(', ')}</Muted> : null}
              </div>
              <IssueList issues={issuesAt(issues, `environments.${e.name}`)} />
            </WellRow>
          ))}
        </WellList>
      ) : (
        <Muted>No environments stated.</Muted>
      )}
      <IssueList issues={issuesAt(issues, 'environments', true)} />
    </Section>
  )
}
