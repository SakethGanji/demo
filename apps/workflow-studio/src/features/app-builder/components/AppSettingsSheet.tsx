/**
 * App settings (UI-DESIGN §6.4): name, description (≤1000, review C18), attached routes (the
 * route-picker checklist), workflow allow-list (`workflow_ids`, F11), access, external APIs from
 * the API tester ("attach external API"), Unpublish and Delete. One Save → `saveApp()` (N4).
 */
import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Send, Trash2, Workflow } from 'lucide-react'
import { apiTesterApi, type ApiAppAccess, type ApiAppDetail } from '@/shared/lib/api'
import type { Route } from '@/shared/api/types'
import { errorMessage } from '@/shared/api/http'
import { Sheet, SheetContent, SheetDescription, SheetTitle } from '@/shared/components/ui/sheet'
import { Button } from '@/shared/components/ui/button'
import { Checkbox } from '@/shared/components/ui/checkbox'
import { Field } from '@/shared/components/ui/field'
import { Input } from '@/shared/components/ui/input'
import { Textarea } from '@/shared/components/ui/textarea'
import { Segmented } from '@/shared/components/ui/segmented'
import { floatingSheetCls } from '@/features/settings/SettingsSheet'
import { useProjectWorkflows } from '@/features/project/hooks/useProjectData'
import { routeIdsOf } from '../lib/appModel'
import { useSaveApp } from '../hooks/useAppData'
import { RouteChecklist } from './RoutePicker'

const sectionTitle = 'text-[12px] font-medium text-muted-foreground'

export function AppSettingsSheet({
  open,
  onClose,
  app,
  pid,
  routes,
  onOpenTester,
  onUnpublish,
  onDelete,
}: {
  open: boolean
  onClose: () => void
  app: ApiAppDetail
  pid: string
  routes: Route[]
  onOpenTester: () => void
  onUnpublish: () => void
  onDelete: () => void
}) {
  return (
    <Sheet open={open} onOpenChange={(o) => !o && onClose()}>
      <SheetContent side="right" showCloseButton className={`${floatingSheetCls} data-[side=right]:w-[480px]`} data-testid="app-settings-sheet">
        {open && <Body app={app} pid={pid} routes={routes} onClose={onClose} onOpenTester={onOpenTester} onUnpublish={onUnpublish} onDelete={onDelete} />}
      </SheetContent>
    </Sheet>
  )
}

function Body({
  app,
  pid,
  routes,
  onClose,
  onOpenTester,
  onUnpublish,
  onDelete,
}: {
  app: ApiAppDetail
  pid: string
  routes: Route[]
  onClose: () => void
  onOpenTester: () => void
  onUnpublish: () => void
  onDelete: () => void
}) {
  const save = useSaveApp(app.id)
  const workflows = useProjectWorkflows(pid)
  const executions = useQuery({ queryKey: ['api-tester', 'executions'], queryFn: () => apiTesterApi.list() })
  const [name, setName] = useState(app.name)
  const [description, setDescription] = useState(app.description ?? '')
  const [routeIds, setRouteIds] = useState<string[]>(() => routeIdsOf(app).filter((id) => routes.some((r) => r.id === id)))
  const [workflowIds, setWorkflowIds] = useState<string[]>(app.workflow_ids ?? [])
  const [execIds, setExecIds] = useState<string[]>(app.api_execution_ids ?? [])
  const [access, setAccess] = useState<ApiAppAccess>(app.access)
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => setError(null), [name, description, access, password])

  const toggle = (list: string[], id: string, on: boolean) => (on ? [...new Set([...list, id])] : list.filter((x) => x !== id))
  const needsPassword = access === 'password' && !app.access_password_set && password.length < 4

  const submit = async () => {
    setBusy(true)
    setError(null)
    try {
      await save({
        name: name.trim(),
        ...(description !== (app.description ?? '') ? { description: description.slice(0, 1000) } : {}),
        workflow_ids: workflowIds,
        api_execution_ids: execIds,
        access,
        ...(access === 'password' && password ? { access_password: password } : {}),
        studio: { route_ids: routeIds },
      })
      onClose()
    } catch (e) {
      setError(errorMessage(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <form
      className="flex h-full min-h-0 flex-col"
      onSubmit={(e) => {
        e.preventDefault()
        if (e.target !== e.currentTarget) return
        if (!busy && name.trim() && !needsPassword) void submit()
      }}
    >
      <div className="hairline-b px-5 pb-3 pt-4 pr-12">
        <SheetTitle className="text-base font-semibold">App settings</SheetTitle>
        <SheetDescription className="text-[13px] text-muted-foreground">{app.name}</SheetDescription>
      </div>
      <div className="flex min-h-0 flex-1 flex-col gap-5 overflow-y-auto px-5 py-4" data-testid="app-settings-panel">
        <section className="flex flex-col gap-3">
          <Field label="Name" htmlFor="as-name">
            <Input id="as-name" value={name} onChange={(e) => setName(e.target.value)} maxLength={255} data-testid="app-settings-name" />
          </Field>
          <Field label="Description" htmlFor="as-desc" help={`${description.length} / 1000`}>
            <Textarea
              id="as-desc"
              rows={3}
              value={description}
              maxLength={1000}
              onChange={(e) => setDescription(e.target.value.slice(0, 1000))}
              data-testid="app-settings-description"
            />
          </Field>
        </section>

        <section className="flex flex-col gap-1.5">
          <div className="flex items-center">
            <span className={sectionTitle}>Attached routes</span>
            <div className="flex-1" />
            <span className="text-[11.5px] text-muted-foreground">{routeIds.length} of {routes.length}</span>
          </div>
          <div className="rounded-well bg-recess p-1">
            {routes.length === 0 ? (
              <div className="px-2 py-2 text-xs text-muted-foreground">This project has no routes yet.</div>
            ) : (
              <RouteChecklist routes={routes} value={routeIds} onChange={setRouteIds} testIdPrefix="app-settings-route" />
            )}
          </div>
          <span className="text-[11.5px] leading-4 text-muted-foreground">The AI always sees every route; checked ones are its focus.</span>
        </section>

        <section className="flex flex-col gap-1.5">
          <span className={sectionTitle}>Workflows the app may run</span>
          <div className="rounded-well bg-recess p-1" data-testid="app-settings-workflows">
            {workflows.isLoading ? (
              <div className="px-2 py-2 text-xs text-muted-foreground">Loading…</div>
            ) : (workflows.data ?? []).length === 0 ? (
              <div className="px-2 py-2 text-xs text-muted-foreground">No workflows in this project.</div>
            ) : (
              (workflows.data ?? []).map((w) => (
                <label key={w.id} className="flex h-[30px] items-center gap-2 rounded-ctl px-1.5 hover:bg-(--hover-fill)">
                  <Checkbox
                    checked={workflowIds.includes(w.id)}
                    onCheckedChange={(c) => setWorkflowIds((l) => toggle(l, w.id, !!c))}
                    data-testid={`app-settings-workflow-${w.id}`}
                  />
                  <Workflow className="size-3.5 text-muted-foreground" />
                  <span className="truncate">{w.name}</span>
                </label>
              ))
            )}
          </div>
          <span className="text-[11.5px] leading-4 text-muted-foreground">Only allow-listed workflows can be run from the app (preview and published).</span>
        </section>

        <section className="flex flex-col gap-1.5">
          <span className={sectionTitle}>Access</span>
          <Segmented<ApiAppAccess>
            value={access}
            onChange={setAccess}
            aria-label="Access"
            className="w-fit"
            options={[
              { key: 'public', label: 'Public', testId: 'app-settings-access-public' },
              { key: 'password', label: 'Password', testId: 'app-settings-access-password' },
              { key: 'private', label: 'Private', testId: 'app-settings-access-private' },
            ]}
          />
          {access === 'password' && (
            <Input
              type="password"
              autoComplete="new-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder={app.access_password_set ? 'Leave blank to keep the current password' : 'Password (at least 4 characters)'}
              className="h-8"
              data-testid="app-settings-password"
            />
          )}
          <span className="text-[11.5px] leading-4 text-muted-foreground">
            {access === 'private'
              ? `Only reachable from the studio preview; /a/${app.slug ?? '<slug>'} returns 404.`
              : app.active
                ? 'Applies to the published link immediately.'
                : 'Used when you publish.'}
          </span>
        </section>

        <section className="flex flex-col gap-1.5">
          <div className="flex items-center">
            <span className={sectionTitle}>External APIs</span>
            <div className="flex-1" />
            <Button variant="ghost" size="xs" type="button" onClick={onOpenTester} data-testid="app-settings-open-tester">
              <Send /> Open API tester
            </Button>
          </div>
          <div className="rounded-well bg-recess p-1" data-testid="app-settings-external-apis">
            {executions.isLoading ? (
              <div className="px-2 py-2 text-xs text-muted-foreground">Loading…</div>
            ) : (executions.data ?? []).length === 0 ? (
              <div className="px-2 py-2 text-xs text-muted-foreground">No saved requests. Capture one in the API tester to attach it.</div>
            ) : (
              (executions.data ?? []).map((ex) => (
                <label key={ex.id} className="flex min-h-[30px] items-center gap-2 rounded-ctl px-1.5 hover:bg-(--hover-fill)">
                  <Checkbox checked={execIds.includes(ex.id)} onCheckedChange={(c) => setExecIds((l) => toggle(l, ex.id, !!c))} />
                  <span className="w-10 shrink-0 font-mono text-[10px] font-bold">{ex.method}</span>
                  <span className="min-w-0 flex-1 truncate">{ex.name || ex.url}</span>
                </label>
              ))
            )}
          </div>
          <span className="text-[11.5px] leading-4 text-muted-foreground">Attached requests are shown to the AI and may be replayed by the app.</span>
        </section>

        <section className="flex flex-col gap-2 rounded-well bg-destructive-soft p-3" data-testid="app-settings-danger">
          {app.active && (
            <div className="flex items-center gap-3">
              <div className="min-w-0 flex-1">
                <div className="font-medium">Unpublish</div>
                <div className="text-[11.5px] text-muted-foreground">
                  <span className="font-mono">/a/{app.slug}</span> will return 404.
                </div>
              </div>
              <Button variant="outline" size="sm" type="button" onClick={onUnpublish} data-testid="app-settings-unpublish">
                Unpublish
              </Button>
            </div>
          )}
          <div className="flex items-center gap-3">
            <div className="min-w-0 flex-1">
              <div className="font-medium">Delete app</div>
              <div className="text-[11.5px] text-muted-foreground">Deletes the app, its versions and its published link.</div>
            </div>
            <Button variant="destructive" size="sm" type="button" onClick={onDelete} data-testid="app-settings-delete">
              <Trash2 /> Delete
            </Button>
          </div>
        </section>
      </div>
      <div className="flex items-center gap-2 px-5 py-3 shadow-[inset_0_1px_0_var(--hairline)]">
        {error && (
          <span className="min-w-0 flex-1 truncate text-[11.5px] text-destructive" role="alert" data-testid="app-settings-error">
            {error}
          </span>
        )}
        <div className="flex-1" />
        <Button variant="ghost" size="sm" type="button" onClick={onClose}>
          Cancel
        </Button>
        <Button size="sm" type="submit" disabled={busy || !name.trim() || needsPassword} data-testid="app-settings-save">
          Save
        </Button>
      </div>
    </form>
  )
}
