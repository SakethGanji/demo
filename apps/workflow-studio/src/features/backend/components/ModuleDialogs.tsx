/**
 * Backend dialogs (UI-DESIGN §4.2, §6.1, `dialogs.png`): breaking changes, concurrent edit,
 * delete module, rename module.
 */
import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { AppWindow, GitCompare, Pencil, Trash2, TriangleAlert, GitBranch } from 'lucide-react'
import { useProjectCtx } from '@/features/project/ProjectContext'
import { useProjectSummary } from '@/features/project/hooks/useProjectData'
import { modulesApi } from '@/shared/api/endpoints'
import { errorMessage } from '@/shared/api/http'
import type { Change } from '@/shared/api/types'
import { MethodBadge } from '@/shared/components/api/MethodBadge'
import { Badge } from '@/shared/components/ui/badge'
import { Button } from '@/shared/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/shared/components/ui/dialog'
import { Field } from '@/shared/components/ui/field'
import { Input } from '@/shared/components/ui/input'
import { Skeleton } from '@/shared/components/ui/skeleton'
import { timeAgo } from '@/features/project/lib/format'
import { moduleNameError } from '../templates'

function splitRoute(route: string): [string, string] {
  const [m, ...rest] = route.split(' ')
  return [m, rest.join(' ')]
}

export function ChangeRows({ changes }: { changes: Change[] }) {
  return (
    <div className="overflow-hidden rounded-xl border border-border">
      {changes.map((c, i) => {
        const [m, p] = splitRoute(c.route)
        return (
          <div key={i} className="border-b border-border last:border-0" data-testid="breaking-change" data-kind={c.kind}>
            <div className="flex items-center gap-2 px-3 py-2">
              <Badge variant={c.breaking ? 'destructive' : 'neutral'} className="font-mono text-[10.5px]">
                {c.kind}
              </Badge>
              <MethodBadge method={m} />
              <span className="font-mono text-[12.5px]">{p}</span>
              {c.field && <span className="truncate text-xs text-muted-foreground">{c.field}</span>}
              {!c.breaking && <span className="text-xs text-muted-foreground">non-breaking</span>}
            </div>
            {c.affected.map((a, j) => (
              <div key={j} className="flex items-center gap-2 border-t border-border/60 bg-destructive/[0.03] px-3 py-1.5 pl-6 text-xs" data-testid="breaking-affected">
                <AppWindow className="size-3.5 text-muted-foreground" />
                <span>{a.app_name}</span>
                <span className="font-mono text-muted-foreground">
                  {a.file}:{a.line}
                </span>
                {c.operation && <span className="text-muted-foreground">calls api.{c.operation}</span>}
              </div>
            ))}
          </div>
        )
      })}
    </div>
  )
}

export function BreakingChangesDialog({
  open,
  module,
  changes,
  onCancel,
  onConfirm,
}: {
  open: boolean
  module: string
  changes: Change[]
  onCancel: () => void
  onConfirm: () => void
}) {
  const apps = new Set(changes.flatMap((c) => c.affected.map((a) => a.app_id)))
  const loosened = changes.some((c) => c.kind === 'auth_loosened')
  return (
    <Dialog open={open} onOpenChange={(o) => !o && onCancel()}>
      <DialogContent className="sm:max-w-lg" data-testid="breaking-dialog">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <TriangleAlert className="size-4 text-warning" />
            {apps.size > 0
              ? `Saving ${module}.py breaks ${apps.size} app${apps.size === 1 ? '' : 's'}`
              : loosened
                ? `Saving ${module}.py loosens route auth`
                : `Saving ${module}.py changes the API`}
          </DialogTitle>
          <DialogDescription>
            From <code>PUT …/modules/{'{id}'}?dry_run=true</code>. Nothing is saved yet.
          </DialogDescription>
        </DialogHeader>
        <ChangeRows changes={changes} />
        <DialogFooter>
          <Button variant="ghost" onClick={onCancel} data-testid="breaking-cancel">
            Cancel
          </Button>
          <Button variant="destructive" onClick={onConfirm} data-testid="breaking-confirm">
            Save anyway
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

export function ModuleChangedDialog({
  open,
  module,
  updatedAt,
  onShowDiff,
  onReload,
  onOverwrite,
  onClose,
}: {
  open: boolean
  module: string
  updatedAt: string | null
  onShowDiff: () => void
  onReload: () => void
  onOverwrite: () => void
  onClose: () => void
}) {
  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-md" data-testid="module-changed">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <GitBranch className="size-4 text-primary" /> {module}.py changed since you opened it
          </DialogTitle>
          <DialogDescription>
            Saved {updatedAt ? timeAgo(updatedAt) : 'recently'} in another tab (or by the app builder). Save returned <code>409 module_changed</code>; your
            edits are still here.
          </DialogDescription>
        </DialogHeader>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={onShowDiff} data-testid="module-changed-diff">
            <GitCompare /> Show diff
          </Button>
          <Button variant="outline" size="sm" onClick={onReload} data-testid="module-changed-reload">
            Reload theirs
          </Button>
          <div className="flex-1" />
          <Button variant="destructive" size="sm" onClick={onOverwrite} data-testid="module-changed-overwrite">
            Overwrite
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}

export function DeleteModuleDialog({
  open,
  module,
  moduleId,
  tables,
  onClose,
  onConfirm,
  busy,
}: {
  open: boolean
  module: string
  moduleId: string
  tables: string[]
  onClose: () => void
  onConfirm: () => void
  busy: boolean
}) {
  const { project, pid } = useProjectCtx()
  const summary = useProjectSummary(pid)
  const [typed, setTyped] = useState('')
  useEffect(() => {
    if (!open) setTyped('')
  }, [open])
  const dry = useQuery({
    queryKey: ['project', pid, 'module', moduleId, 'delete-dry-run'],
    queryFn: () => modulesApi.removeDryRun(pid, moduleId),
    enabled: open,
    staleTime: 0,
  })
  const removed = (dry.data?.routes.removed ?? []) as Array<{ method: string; path: string; name: string }>
  const breaking = dry.data?.breaking_changes ?? []
  const usedBy = (r: { method: string; path: string }) =>
    breaking.filter((c) => c.route === `${r.method} ${r.path}`).flatMap((c) => c.affected.map((a) => a.app_name))
  const live = project.live_environment
  const liveRelease = summary.data?.active_releases?.[live]
  const hasRelease = project.environments.some((e) => e.kind === 'release')
  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-lg" data-testid="delete-module-dialog">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Trash2 className="size-4 text-destructive" /> Delete {module}.py?
          </DialogTitle>
          <DialogDescription>
            {!hasRelease
              ? 'These routes stop working as soon as you delete the module.'
              : liveRelease
                ? `These routes stop working in ${project.default_environment}; ${live} keeps serving release #${liveRelease.number} until you promote.`
                : `These routes stop working in ${project.default_environment} and in ${live} (${live} has no release, so it serves your latest saved code).`}
          </DialogDescription>
        </DialogHeader>
        {dry.isLoading ? (
          <Skeleton className="h-24" />
        ) : dry.isError ? (
          <div className="text-xs text-destructive">{errorMessage(dry.error)}</div>
        ) : (
          <div className="overflow-hidden rounded-xl border border-border" data-testid="delete-module-routes">
            {removed.length === 0 && <div className="px-3 py-2 text-xs text-muted-foreground">This module declares no routes.</div>}
            {removed.map((r) => {
              const apps = usedBy(r)
              return (
                <div key={`${r.method} ${r.path}`} className="flex items-center gap-2 border-b border-border px-3 py-2 last:border-0">
                  <MethodBadge method={r.method} />
                  <span className="font-mono text-[12.5px]">{r.path}</span>
                  {apps.length > 0 && (
                    <Badge variant="warning" className="ml-auto" data-testid="delete-module-used-by">
                      used by {[...new Set(apps)].join(', ')}
                    </Badge>
                  )}
                </div>
              )
            })}
          </div>
        )}
        {tables.length > 0 && (
          <p className="text-xs text-muted-foreground">
            The {tables.map((t) => <code key={t}>{t} </code>)}table{tables.length > 1 ? 's' : ''} and {tables.length > 1 ? 'their' : 'its'} rows are kept.
          </p>
        )}
        <Field label={<>Type <code>{module}</code> to confirm</>}>
          <Input value={typed} onChange={(e) => setTyped(e.target.value)} className="font-mono" data-testid="delete-module-confirm-input" />
        </Field>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="destructive" disabled={typed !== module || busy || dry.isLoading} onClick={onConfirm} data-testid="delete-module-confirm">
            Delete module
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

export function RenameModuleDialog({
  open,
  module,
  taken,
  onClose,
  onConfirm,
  busy,
  serverError,
}: {
  open: boolean
  module: string
  taken: string[]
  onClose: () => void
  onConfirm: (name: string) => void
  busy: boolean
  serverError: string | null
}) {
  const [name, setName] = useState(module)
  useEffect(() => {
    if (open) setName(module)
  }, [open, module])
  const err = name === module ? null : (moduleNameError(name) ?? (taken.includes(name) ? `A module called "${name}" already exists.` : null))
  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-md" data-testid="rename-module-dialog">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Pencil className="size-4 text-muted-foreground" /> Rename {module}.py
          </DialogTitle>
          <DialogDescription>Routes keep their paths; apps are unaffected.</DialogDescription>
        </DialogHeader>
        <form
          className="flex flex-col gap-3"
          onSubmit={(e) => {
            e.preventDefault()
            if (!err && name !== module) onConfirm(name)
          }}
        >
          <Field label="New name" error={err ?? serverError} errorTestId="rename-module-error">
            <Input autoFocus value={name} onChange={(e) => setName(e.target.value.trim())} className="font-mono" data-testid="rename-module-name" />
          </Field>
          <DialogFooter>
            <Button type="button" variant="ghost" onClick={onClose}>
              Cancel
            </Button>
            <Button type="submit" disabled={!!err || name === module || busy} data-testid="rename-module-confirm">
              Rename
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
