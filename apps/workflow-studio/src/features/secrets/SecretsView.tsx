/**
 * Secrets view (UI-DESIGN §3.5, `secrets.png`): a key × environment matrix with cell states
 * (set · age, missing + Add, not set + Add, team value, set · overrides team), the allowed-hosts
 * cell (chips or "not bound" + Bind host), an expanded row (Referenced by, per-environment allowed
 * hosts, Replace value) and one banner per missing key/environment. Team rows are read-only here.
 * Project rows are only ever written through `/api/projects/{p}/secrets` (§9 D2).
 * `?create=KEY&env=ENV` opens the create form prefilled.
 */
import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useSearch } from '@tanstack/react-router'
import {
  AlertTriangle,
  ChevronRight,
  Ellipsis,
  FileCode2,
  KeyRound,
  Link2Off,
  Plus,
  Type,
  Users,
  Workflow,
} from 'lucide-react'
import { toast } from 'sonner'
import { useProjectCtx } from '@/features/project/ProjectContext'
import { useMissingSecrets, useSecrets } from '@/features/project/hooks/useProjectData'
import { timeAgo } from '@/features/project/lib/format'
import { CreateSecretPopover } from './CreateSecretPopover'
import { useDeleteSecret, useUpdateSecret } from './useSecretMutations'
import { useSecretReferences, type CodeRef } from './useSecretReferences'
import { errorMessage } from '@/shared/api/http'
import type { EffectiveVar, MissingSecret } from '@/shared/api/types'
import { Badge } from '@/shared/components/ui/badge'
import { Button } from '@/shared/components/ui/button'
import { ChipsInput } from '@/shared/components/ui/chips-input'
import { EmptyState } from '@/shared/components/ui/empty-state'
import { Input } from '@/shared/components/ui/input'
import { Skeleton } from '@/shared/components/ui/skeleton'
import { StatusDot } from '@/shared/components/ui/status-dot'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/shared/components/ui/table'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/shared/components/ui/dropdown-menu'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/shared/components/ui/alert-dialog'
import { cn } from '@/shared/lib/utils'

interface KeyRow {
  key: string
  /** project rows by environment */
  project: Record<string, EffectiveVar>
  /** team rows by environment */
  team: Record<string, EffectiveVar>
  /** environments where code references it and it is absent */
  missingIn: string[]
}

export type CellState = 'set' | 'overrides' | 'team' | 'missing' | 'not-set'

function cellState(row: KeyRow, env: string): CellState {
  const p = row.project[env]
  if (p) return p.overrides_team ? 'overrides' : 'set'
  if (row.team[env]) return 'team'
  return row.missingIn.includes(env) ? 'missing' : 'not-set'
}

export function SecretsView() {
  const { pid, project, viewEnv } = useProjectCtx()
  const search = useSearch({ strict: false }) as { create?: string; env?: string }
  const navigate = useNavigate()
  const secrets = useSecrets(pid)
  const missing = useMissingSecrets(pid)
  const { refs, hostsFor } = useSecretReferences(pid, missing.data)
  const envs = project.environments
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [focusHosts, setFocusHosts] = useState<string | null>(null)
  const [createOpenLocal, setCreateOpen] = useState(false)
  // `?create=KEY&env=ENV` opens the create form prefilled (§2.2).
  const createOpen = createOpenLocal || !!search.create

  const closeCreate = (o: boolean) => {
    setCreateOpen(o)
    if (!o && (search.create || search.env))
      navigate({
        to: '.',
        search: ((s: Record<string, unknown>) => {
          const n = { ...s }
          delete n.create
          delete n.env
          return n
        }) as never,
        replace: true,
      })
  }

  const rows = useMemo<KeyRow[]>(() => {
    const m = new Map<string, KeyRow>()
    const get = (key: string) => {
      let r = m.get(key)
      if (!r) m.set(key, (r = { key, project: {}, team: {}, missingIn: [] }))
      return r
    }
    for (const v of secrets.data ?? []) {
      const r = get(v.key)
      if (v.scope === 'project') r.project[v.environment] = v
      else r.team[v.environment] = v
    }
    for (const ms of missing.data ?? []) get(ms.key).missingIn = ms.missing_in
    return [...m.values()].sort((a, b) => a.key.localeCompare(b.key))
  }, [secrets.data, missing.data])

  const toggle = (key: string, open?: boolean) =>
    setExpanded((s) => {
      const n = new Set(s)
      if (open ?? !n.has(key)) n.add(key)
      else n.delete(key)
      return n
    })

  const createEnv = search.env && envs.some((e) => e.name === search.env) ? [search.env] : undefined

  return (
    <div className="flex h-full flex-col rounded-xl border border-panel-border bg-card shadow-xs" data-testid="secrets-view">
      <div className="min-h-0 flex-1 overflow-y-auto px-7 py-6" data-testid="secrets-panel">
        <div className="flex items-start gap-3">
          <div className="min-w-0">
            <h1 className="text-[17px] font-semibold tracking-[-0.01em]">Secrets</h1>
            <p className="mt-0.5 text-[13px] text-muted-foreground">
              Write-only values per environment. Python reads <span className="font-mono">studio.secret("NAME")</span>; values
              never reach your code, the AI, or this page.
            </p>
          </div>
          <div className="grow" />
          <CreateSecretPopover
            open={createOpen}
            onOpenChange={closeCreate}
            secretKey={search.create ?? ''}
            environments={createEnv}
            hosts={search.create ? hostsFor(search.create) : []}
            onCreated={(c) => toast.success(`${c[0]?.key ?? 'Secret'} added to ${c.map((x) => x.environment).join(', ')}`)}
            trigger={
              <Button size="sm" data-testid="secret-add">
                <Plus /> Add secret
              </Button>
            }
          />
        </div>

        {secrets.isPending || missing.isPending ? (
          <MatrixSkeleton />
        ) : secrets.isError || missing.isError ? (
          <EmptyState
            className="mt-6 rounded-xl border border-border"
            title="Couldn't load secrets"
            data-testid="secrets-error"
            actions={
              <Button
                size="sm"
                variant="outline"
                onClick={() => {
                  void secrets.refetch()
                  void missing.refetch()
                }}
              >
                Retry
              </Button>
            }
          >
            {errorMessage(secrets.error ?? missing.error)}
          </EmptyState>
        ) : rows.length === 0 ? (
          <EmptyState
            className="mt-6 rounded-xl border border-dashed border-border"
            icon={<KeyRound />}
            title="No secrets. Add API keys your backend calls with."
            data-testid="secrets-empty"
            actions={
              <Button size="sm" variant="outline" onClick={() => setCreateOpen(true)} data-testid="secrets-empty-add">
                <Plus /> Add
              </Button>
            }
          />
        ) : (
          <>
            <MissingBanners missing={missing.data ?? []} refs={refs} hostsFor={hostsFor} />

            <div className="mt-3.5 overflow-hidden rounded-xl border border-border" data-testid="secrets-matrix">
              <Table>
                <TableHeader>
                  <TableRow className="hover:bg-transparent">
                    <TableHead className="w-[32%]">Key</TableHead>
                    {envs.map((e) => (
                      <TableHead
                        key={e.name}
                        data-testid={`secrets-col-${e.name}`}
                        data-viewing={e.name === viewEnv}
                        className={cn(e.name === viewEnv && 'bg-primary/5 text-foreground')}
                      >
                        {e.name}
                        <Badge variant="secondary" className="ml-1.5 h-4 px-1.5 text-[10px]">
                          {e.kind === 'draft' ? 'draft' : e.name === project.live_environment ? 'live' : 'release'}
                        </Badge>
                      </TableHead>
                    ))}
                    <TableHead>Allowed hosts</TableHead>
                    <TableHead className="w-10" />
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {rows.map((row) => (
                    <SecretRow
                      key={row.key}
                      row={row}
                      open={expanded.has(row.key)}
                      onToggle={() => toggle(row.key)}
                      onBindHost={() => {
                        toggle(row.key, true)
                        setFocusHosts(row.key)
                      }}
                      focusHosts={focusHosts === row.key}
                      refs={refs.get(row.key) ?? []}
                      hosts={hostsFor(row.key)}
                    />
                  ))}
                </TableBody>
              </Table>
            </div>

            <div className="mt-2.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11.5px] text-muted-foreground" data-testid="secrets-legend">
              <span className="inline-flex items-center gap-1.5">
                <StatusDot tone="warn" />
                <b className="font-medium text-warning-text">missing</b>: your code uses it and it isn't set
              </span>
              <span>
                <b className="font-medium text-foreground">not set</b>: unused in that environment
              </span>
              <span className="inline-flex items-center gap-1.5">
                <Users className="size-3" /> team values are edited in Workflows → Variables
              </span>
            </div>
          </>
        )}
      </div>
    </div>
  )
}

// ── banners ─────────────────────────────────────────────────────────────────

function refLabel(r: CodeRef): string {
  return r.kind === 'module' ? `${r.name}.py:${r.line}` : `${r.name} › ${r.node}`
}

function MissingBanners({
  missing,
  refs,
  hostsFor,
}: {
  missing: MissingSecret[]
  refs: Map<string, CodeRef[]>
  hostsFor: (k: string) => string[]
}) {
  const { project } = useProjectCtx()
  const items = missing.flatMap((m) => m.missing_in.map((env) => ({ m, env })))
  if (!items.length) return null
  return (
    <div className="mt-4 flex flex-col gap-2">
      {items.map(({ m, env }) => {
        const list = refs.get(m.key) ?? []
        const first = list[0]
        const routes = [...new Set(list.map((r) => r.route).filter(Boolean).map((r) => `${r!.method} ${r!.path}`))]
        const live = env === project.live_environment
        return (
          <div
            key={`${m.key}-${env}`}
            className="flex items-center gap-2.5 rounded-xl border border-warning/35 bg-warning/8 px-3.5 py-2.5 text-[13px]"
            data-testid="secrets-missing"
            data-key={m.key}
            data-env={env}
          >
            <AlertTriangle className="size-3.5 shrink-0 text-warning-text" />
            <span className="min-w-0">
              <b className="font-semibold">{m.key}</b> is used by{' '}
              <span className="font-mono">{first ? refLabel(first) : 'your code'}</span>
              {list.length > 1 ? ` and ${list.length - 1} more` : ''} but isn't set in <b className="font-semibold">{env}</b>.
              {routes.length > 0 && (
                <>
                  {' '}
                  {live ? 'Published apps calling' : 'Calls to'} <span className="font-mono">{routes.join(', ')}</span> will fail.
                </>
              )}
            </span>
            <div className="grow" />
            <CreateSecretPopover
              secretKey={m.key}
              keyReadOnly
              environments={[env]}
              hosts={hostsFor(m.key)}
              testId={`create-secret-${m.key}-${env}`}
              trigger={
                <Button variant="outline" size="xs" className="shrink-0" data-testid="secrets-missing-add">
                  Add to {env}
                </Button>
              }
            />
          </div>
        )
      })}
    </div>
  )
}

// ── matrix rows ─────────────────────────────────────────────────────────────

function SecretRow({
  row,
  open,
  onToggle,
  onBindHost,
  focusHosts,
  refs,
  hosts,
}: {
  row: KeyRow
  open: boolean
  onToggle: () => void
  onBindHost: () => void
  focusHosts: boolean
  refs: CodeRef[]
  hosts: string[]
}) {
  const { project, viewEnv } = useProjectCtx()
  const envs = project.environments.map((e) => e.name)
  const projectRows = envs.map((e) => row.project[e]).filter(Boolean) as EffectiveVar[]
  const teamRows = envs.map((e) => row.team[e]).filter(Boolean) as EffectiveVar[]
  const isProject = projectRows.length > 0
  const type = (projectRows[0] ?? teamRows[0])?.type ?? 'secret'
  // the hosts cell shows the viewing environment's row, else the first project row
  const hostRow = row.project[viewEnv] ?? projectRows[0]
  const [confirm, setConfirm] = useState<EffectiveVar | null>(null)
  const del = useDeleteSecret(useProjectCtx().pid)

  return (
    <>
      <TableRow
        data-testid="secret-row"
        data-key={row.key}
        data-scope={isProject ? 'project' : 'team'}
        data-state={open ? 'selected' : undefined}
        className={cn(open && 'bg-muted/40')}
      >
        <TableCell className="max-w-0">
          <button
            type="button"
            className="flex min-w-0 items-center gap-2 text-left"
            onClick={onToggle}
            aria-expanded={open}
            data-testid="secret-expand"
          >
            <ChevronRight className={cn('size-3.5 shrink-0 text-muted-foreground transition-transform', open && 'rotate-90')} />
            {type === 'secret' ? (
              <KeyRound className="size-3.5 shrink-0 text-muted-foreground" />
            ) : (
              <Type className="size-3.5 shrink-0 text-muted-foreground" />
            )}
            <span className="truncate font-mono">{row.key}</span>
            {isProject ? (
              <Badge variant="outline" className="h-[18px] text-[10.5px]">
                project{type !== 'secret' ? ` · ${type}` : ''}
              </Badge>
            ) : (
              <Badge variant="neutral" className="h-[18px] text-[10.5px]">
                team · {type}
              </Badge>
            )}
          </button>
        </TableCell>
        {envs.map((env) => (
          <TableCell key={env} className={cn(env === viewEnv && 'bg-primary/5')}>
            <MatrixCell row={row} env={env} hosts={hosts} />
          </TableCell>
        ))}
        <TableCell className="max-w-[280px]">
          {!hostRow ? (
            <span className="text-muted-foreground">—</span>
          ) : hostRow.type !== 'secret' ? (
            <span className="text-xs text-muted-foreground">not a secret</span>
          ) : hostRow.allowed_hosts && hostRow.allowed_hosts.length ? (
            <span className="flex flex-wrap gap-1" data-testid="secret-hosts-cell">
              {hostRow.allowed_hosts.map((h) => (
                <span key={h} className="inline-flex h-[22px] items-center rounded-md border border-border px-2 font-mono text-[11px]">
                  {h}
                </span>
              ))}
            </span>
          ) : (
            <span className="flex items-center gap-2" data-testid="secret-unbound">
              <span className="inline-flex items-center gap-1 text-xs text-warning-text">
                <Link2Off className="size-3" /> not bound: Python calls refused
              </span>
              <Button variant="ghost" size="xs" onClick={onBindHost} data-testid="secret-bind-host">
                Bind host
              </Button>
            </span>
          )}
        </TableCell>
        <TableCell className="px-2">
          <DropdownMenu>
            <DropdownMenuTrigger
              render={
                <Button variant="ghost" size="icon-xs" aria-label={`${row.key} actions`} data-testid="secret-menu">
                  <Ellipsis />
                </Button>
              }
            />
            <DropdownMenuContent align="end" className="min-w-48">
              {isProject ? (
                <>
                  <DropdownMenuItem onClick={onToggle}>{open ? 'Collapse' : 'Edit hosts or value'}</DropdownMenuItem>
                  <DropdownMenuSeparator />
                  {projectRows.map((r) => (
                    <DropdownMenuItem
                      key={r.id}
                      variant="destructive"
                      onClick={() => setConfirm(r)}
                      data-testid={`secret-delete-${r.environment}`}
                    >
                      Delete from {r.environment}
                    </DropdownMenuItem>
                  ))}
                </>
              ) : (
                <DropdownMenuLabel className="text-xs font-normal text-muted-foreground">
                  Team values are edited in Workflows → Variables
                </DropdownMenuLabel>
              )}
            </DropdownMenuContent>
          </DropdownMenu>
          <AlertDialog open={!!confirm} onOpenChange={(o) => !o && setConfirm(null)}>
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle>
                  Delete {confirm?.key} from {confirm?.environment}?
                </AlertDialogTitle>
                <AlertDialogDescription>
                  {refs.length
                    ? `Your code uses it (${refs.map(refLabel).join(', ')}); those calls will fail in ${confirm?.environment}.`
                    : 'Nothing in your code references it.'}
                  {row.team[confirm?.environment ?? ''] ? ' The team value will apply again.' : ''}
                </AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel>Cancel</AlertDialogCancel>
                <AlertDialogAction
                  variant="destructive"
                  data-testid="secret-delete-confirm"
                  onClick={() =>
                    confirm &&
                    del.mutate(confirm.id, {
                      onSuccess: () => toast.success(`${confirm.key} deleted from ${confirm.environment}`),
                      onError: (e) => toast.error(errorMessage(e)),
                      onSettled: () => setConfirm(null),
                    })
                  }
                >
                  Delete
                </AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>
        </TableCell>
      </TableRow>
      {open && (
        <TableRow className="bg-muted/40 hover:bg-muted/40" data-testid="secret-expanded" data-key={row.key}>
          <TableCell colSpan={envs.length + 3} className="h-auto whitespace-normal px-4 py-4 pl-[54px]">
            <ExpandedRow row={row} refs={refs} focusHosts={focusHosts} />
          </TableCell>
        </TableRow>
      )}
    </>
  )
}

function MatrixCell({ row, env, hosts }: { row: KeyRow; env: string; hosts: string[] }) {
  const s = cellState(row, env)
  const p = row.project[env]
  const tid = `secret-cell-${row.key}-${env}`
  if (s === 'set' || s === 'overrides')
    return (
      <span className="inline-flex items-center gap-1.5" data-testid={tid} data-state={s}>
        <StatusDot tone="ok" className="shadow-none" />
        set
        <span className="text-muted-foreground">· {s === 'overrides' ? 'overrides team' : timeAgo(p!.updated_at)}</span>
      </span>
    )
  if (s === 'team')
    return (
      <span className="inline-flex items-center gap-1.5 text-muted-foreground" data-testid={tid} data-state={s}>
        <Users className="size-3" /> team value
      </span>
    )
  const add = (
    <CreateSecretPopover
      secretKey={row.key}
      keyReadOnly
      environments={[env]}
      hosts={hosts.length ? hosts : Object.values(row.project).find((x) => x.allowed_hosts?.length)?.allowed_hosts ?? []}
      testId={`create-secret-${row.key}-${env}`}
      trigger={
        <Button variant={s === 'missing' ? 'outline' : 'ghost'} size="xs" className="ml-1.5" data-testid={`secret-add-${row.key}-${env}`}>
          Add
        </Button>
      }
    />
  )
  return (
    <span className="inline-flex items-center" data-testid={tid} data-state={s}>
      {s === 'missing' ? (
        <span className="inline-flex items-center gap-1.5 font-medium text-warning-text">
          <StatusDot tone="warn" /> missing
        </span>
      ) : (
        <span className="text-muted-foreground">not set</span>
      )}
      {add}
    </span>
  )
}

function ExpandedRow({ row, refs, focusHosts }: { row: KeyRow; refs: CodeRef[]; focusHosts: boolean }) {
  const { project, slug } = useProjectCtx()
  const envs = project.environments.map((e) => e.name)
  const projectRows = envs.map((e) => row.project[e]).filter(Boolean) as EffectiveVar[]
  const teamOnly = projectRows.length === 0
  const eyebrow = 'text-[10.5px] font-semibold uppercase tracking-[0.06em] text-muted-foreground'

  return (
    <div className="grid grid-cols-[minmax(0,1fr)_minmax(0,1.3fr)_minmax(0,1.2fr)] gap-6">
      <div className="flex min-w-0 flex-col gap-1.5" data-testid="secret-refs">
        <span className={eyebrow}>Referenced by</span>
        {refs.length === 0 ? (
          <span className="text-xs text-muted-foreground">No saved code references it.</span>
        ) : (
          refs.map((r, i) =>
            r.kind === 'module' ? (
              <span key={i} className="flex min-w-0 items-center gap-1.5 text-xs">
                <FileCode2 className="size-3 shrink-0 text-muted-foreground" />
                <Link
                  to="/p/$project/backend/$module"
                  params={{ project: slug, module: r.name }}
                  search={{ line: r.line }}
                  className="font-mono text-primary hover:underline"
                  data-testid="secret-ref-link"
                >
                  {r.name}.py:{r.line}
                </Link>
                {r.route && (
                  <span className="truncate text-muted-foreground">
                    · {r.route.method} {r.route.path}
                  </span>
                )}
              </span>
            ) : (
              <span key={i} className="flex min-w-0 items-center gap-1.5 text-xs">
                <Workflow className="size-3 shrink-0 text-muted-foreground" />
                <Link
                  to="/p/$project/workflows/$workflowId"
                  params={{ project: slug, workflowId: r.id! }}
                  className="truncate text-primary hover:underline"
                >
                  {r.name}
                </Link>
                <span className="truncate text-muted-foreground">· {r.node}</span>
              </span>
            ),
          )
        )}
      </div>

      <div className="flex min-w-0 flex-col gap-1.5">
        <span className={eyebrow}>Allowed hosts</span>
        {teamOnly ? (
          <span className="text-xs text-muted-foreground">Team value: edited in Workflows → Variables.</span>
        ) : (
          projectRows.map((r, i) =>
            r.type === 'secret' ? (
              <HostsEditor key={r.id} row={r} label={projectRows.length > 1} autoFocus={focusHosts && i === 0} />
            ) : (
              <span key={r.id} className="text-xs text-muted-foreground">
                {r.environment}: not a secret (no host binding)
              </span>
            ),
          )
        )}
      </div>

      <div className="flex min-w-0 flex-col gap-1.5">
        <span className={eyebrow}>Value</span>
        {teamOnly ? (
          <span className="text-xs text-muted-foreground">Team value: edited in Workflows → Variables.</span>
        ) : (
          projectRows.map((r) => <ValueEditor key={r.id} row={r} />)
        )}
      </div>
    </div>
  )
}

function HostsEditor({ row, label, autoFocus }: { row: EffectiveVar; label: boolean; autoFocus: boolean }) {
  const { pid } = useProjectCtx()
  const update = useUpdateSecret(pid)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    if (autoFocus)
      document.querySelector<HTMLInputElement>(`[data-testid="secret-hosts-${row.key}-${row.environment}-input"]`)?.focus()
  }, [autoFocus, row.key, row.environment])
  return (
    <div className="flex min-w-0 flex-col gap-1">
      <div className="flex min-w-0 items-center gap-2">
        {label && <span className="w-9 shrink-0 text-xs text-muted-foreground">{row.environment}</span>}
        <ChipsInput
          className="min-w-0 flex-1 bg-card"
          value={row.allowed_hosts ?? []}
          testId={`secret-hosts-${row.key}-${row.environment}`}
          onChange={(next) => {
            setError(null)
            update.mutate(
              { id: row.id, body: { allowed_hosts: next.length ? next : null } },
              { onError: (e) => setError(errorMessage(e)) },
            )
          }}
        />
      </div>
      {error && (
        <span className="text-[11.5px] text-destructive" role="alert">
          {error}
        </span>
      )}
    </div>
  )
}

function ValueEditor({ row }: { row: EffectiveVar }) {
  const { pid } = useProjectCtx()
  const update = useUpdateSecret(pid)
  const [editing, setEditing] = useState(false)
  const [value, setValue] = useState('')
  const [error, setError] = useState<string | null>(null)
  const tid = `secret-replace-${row.key}-${row.environment}`
  if (editing)
    return (
      <form
        className="flex flex-col gap-1"
        onSubmit={(e) => {
          e.preventDefault()
          setError(null)
          update.mutate(
            { id: row.id, body: { value } },
            {
              onSuccess: () => {
                setEditing(false)
                setValue('')
                toast.success(`${row.key} replaced in ${row.environment}`)
              },
              onError: (err) => setError(errorMessage(err)),
            },
          )
        }}
      >
        <div className="flex items-center gap-1.5">
          <Input
            type={row.type === 'secret' ? 'password' : 'text'}
            autoComplete="off"
            autoFocus
            value={value}
            onChange={(e) => setValue(e.target.value)}
            placeholder={`new value for ${row.environment}`}
            className="h-7 min-w-0 flex-1 bg-card text-xs"
            data-testid={`${tid}-input`}
          />
          <Button type="submit" size="xs" disabled={!value || update.isPending} data-testid={`${tid}-save`}>
            Save
          </Button>
          <Button type="button" size="xs" variant="ghost" onClick={() => setEditing(false)}>
            Cancel
          </Button>
        </div>
        {error && (
          <span className="text-[11.5px] text-destructive" role="alert">
            {error}
          </span>
        )}
      </form>
    )
  return (
    <div className="flex min-w-0 items-center gap-2 text-xs">
      <span className="truncate font-mono text-muted-foreground">
        {row.type === 'secret' ? '•••••••• (write-only)' : row.value ?? ''}
      </span>
      <Button variant="ghost" size="xs" onClick={() => setEditing(true)} data-testid={tid}>
        Replace value in {row.environment}
      </Button>
    </div>
  )
}

function MatrixSkeleton() {
  return (
    <div className="mt-6 overflow-hidden rounded-xl border border-border" data-testid="secrets-loading">
      {Array.from({ length: 4 }).map((_, i) => (
        <div key={i} className="flex h-[42px] items-center gap-6 border-b border-border/65 px-4 last:border-0">
          <Skeleton className="h-3 w-40" />
          <Skeleton className="h-3 w-20" />
          <Skeleton className="h-3 w-20" />
          <Skeleton className="h-3 w-32" />
        </div>
      ))}
    </div>
  )
}

