/**
 * Create UI dialog (UI-DESIGN §5.1, `create-ui.png`): app name, publish address, the routes the
 * UI should use (none/app_token; api_key is server-only), a starting point and the prompt.
 * Submit: POST …/apps → PUT /api/apps/{A} {definition.studio.route_ids} (via saveApp, N4) →
 * the builder with `?prompt=&slug=` (it auto-sends the first message once).
 */
import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate } from '@tanstack/react-router'
import { useQueryClient } from '@tanstack/react-query'
import { Check, Info, WandSparkles } from 'lucide-react'
import { useProjectCtx } from '@/features/project/ProjectContext'
import { useModules, useRoutes } from '@/features/project/hooks/useProjectData'
import { sortRoutes } from '@/features/project/lib/routes'
import { projectsApi } from '@/shared/api/endpoints'
import { errorMessage } from '@/shared/api/http'
import { invalidate, qk } from '@/shared/api/queryKeys'
import type { Route } from '@/shared/api/types'
import { MethodBadge } from '@/shared/components/api/MethodBadge'
import { Badge } from '@/shared/components/ui/badge'
import { Button } from '@/shared/components/ui/button'
import { Checkbox } from '@/shared/components/ui/checkbox'
import { Dialog, DialogContent, DialogDescription, DialogTitle } from '@/shared/components/ui/dialog'
import { Input } from '@/shared/components/ui/input'
import { Kbd } from '@/shared/components/ui/kbd'
import { Skeleton } from '@/shared/components/ui/skeleton'
import { Textarea } from '@/shared/components/ui/textarea'
import { cn } from '@/shared/lib/utils'
import { BLANK_DEFINITION, slugError, slugify } from '../lib/appModel'
import { saveApp } from '../hooks/useAppData'
import { useSpecRevision } from '../hooks/useAppData'

type Template = 'dashboard' | 'form' | 'crud' | 'blank'

const TEMPLATES: { key: Template; label: string; noun: string }[] = [
  { key: 'dashboard', label: 'Dashboard', noun: 'dashboard' },
  { key: 'form', label: 'Form', noun: 'form' },
  { key: 'crud', label: 'Table + CRUD', noun: 'admin' },
  { key: 'blank', label: 'Blank', noun: 'app' },
]

function defaultPrompt(t: Template, subject: string, routes: Route[]): string {
  const ops = routes.map((r) => `api.${r.name}()`).join(', ')
  const using = ops ? ` using ${ops}` : ''
  switch (t) {
    case 'dashboard':
      return `A ${subject} dashboard${using}: KPI tiles for the key numbers, a chart of the main breakdown, a list of recent records and a refresh button.`
    case 'form':
      return `A form for ${subject}${using}: validated inputs, a submit button that shows success or the error, and the latest result below.`
    case 'crud':
      return `A table of ${subject} records${using}: search, create, edit and delete, with an empty state and inline errors.`
    case 'blank':
      return ''
  }
}

function TemplatePreview({ t }: { t: Template }) {
  const bar = 'absolute rounded-[2px] bg-foreground/12'
  return (
    <div
      className={cn('relative h-10 overflow-hidden rounded-md bg-muted')}
      style={
        t === 'blank'
          ? { background: 'repeating-linear-gradient(135deg,transparent 0 5px,color-mix(in srgb,var(--border) 60%,transparent) 5px 6px)' }
          : undefined
      }
    >
      {t === 'dashboard' && (
        <>
          <i className={bar} style={{ left: 6, top: 6, width: 26, height: 12 }} />
          <i className={bar} style={{ left: 36, top: 6, width: 26, height: 12 }} />
          <i className={bar} style={{ left: 66, top: 6, width: 26, height: 12 }} />
          <i className={cn(bar, 'opacity-60')} style={{ left: 6, top: 22, width: 86, height: 12 }} />
        </>
      )}
      {t === 'form' && (
        <>
          <i className={bar} style={{ left: 6, top: 6, width: 60, height: 5 }} />
          <i className={cn(bar, 'opacity-60')} style={{ left: 6, top: 15, width: 96, height: 7 }} />
          <i className={bar} style={{ left: 6, top: 27, width: 30, height: 8 }} />
        </>
      )}
      {t === 'crud' &&
        [6, 15, 24, 33].map((top, i) => <i key={top} className={cn(bar, i > 0 && 'opacity-60')} style={{ left: 6, top, width: 96, height: 5 }} />)}
    </div>
  )
}

function opSignature(r: Route): string {
  const params = /\{[^}]+\}/.test(r.path)
  const body = ['POST', 'PUT', 'PATCH'].includes(r.method)
  return `api.${r.name}(${[params ? '{ params }' : '', body ? 'body' : ''].filter(Boolean).join(', ')})`
}

function hasSchema(s: Route['response_schema']): boolean {
  return !!s && typeof s === 'object' && Object.keys(s).length > 0
}

export function CreateUiDialog({ open, onClose, preselectModule }: { open: boolean; onClose: () => void; preselectModule?: string }) {
  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="gap-0 p-0 sm:max-w-[640px]" data-testid="create-ui-dialog">
        {open && <Body onClose={onClose} preselectModule={preselectModule} />}
      </DialogContent>
    </Dialog>
  )
}

function Body({ onClose, preselectModule }: { onClose: () => void; preselectModule?: string }) {
  const { project, pid, slug: projectSlug } = useProjectCtx()
  const routesQ = useRoutes(pid)
  const modulesQ = useModules(pid)
  const revision = useSpecRevision(pid).data ?? null
  const qc = useQueryClient()
  const navigate = useNavigate()

  const routes = useMemo(() => sortRoutes(routesQ.data ?? []), [routesQ.data])
  const eligible = useMemo(() => routes.filter((r) => r.auth !== 'api_key'), [routes])
  const moduleById = useMemo(() => new Map((modulesQ.data ?? []).map((m) => [m.id, m.name])), [modulesQ.data])
  const preModuleId = useMemo(
    () => (preselectModule ? (modulesQ.data ?? []).find((m) => m.name === preselectModule || m.id === preselectModule)?.id ?? null : null),
    [preselectModule, modulesQ.data],
  )

  const subject = (preselectModule ?? project.name).replace(/\.py$/, '').replace(/[_-]+/g, ' ')
  const [template, setTemplate] = useState<Template>('dashboard')
  const [name, setName] = useState('')
  const [nameTouched, setNameTouched] = useState(false)
  const [slug, setSlug] = useState('')
  const [slugTouched, setSlugTouched] = useState(false)
  const [selected, setSelected] = useState<string[] | null>(null)
  const [prompt, setPrompt] = useState('')
  const [promptTouched, setPromptTouched] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Initial selection once routes (and the module to preselect) are known.
  const ready = routesQ.isSuccess && (!preselectModule || modulesQ.isSuccess)
  useEffect(() => {
    if (!ready || selected !== null) return
    if (routes.length === 0) setTemplate('blank')
    const pick = preModuleId
      ? eligible.filter((r) => r.handler.type === 'python' && r.handler.module_id === preModuleId)
      : eligible
    setSelected(pick.map((r) => r.id))
  }, [ready, selected, eligible, preModuleId, routes.length])

  const selectedRoutes = useMemo(() => eligible.filter((r) => (selected ?? []).includes(r.id)), [eligible, selected])
  const tpl = TEMPLATES.find((t) => t.key === template)!
  const autoName = `${subject.charAt(0).toUpperCase()}${subject.slice(1)} ${tpl.noun}`
  const effName = nameTouched ? name : autoName
  const effSlug = slugTouched ? slug : slugify(effName)
  const autoPrompt = defaultPrompt(template, subject.toLowerCase(), selectedRoutes)
  const effPrompt = promptTouched ? prompt : autoPrompt
  const sErr = slugError(effSlug)
  const canSubmit = !busy && !!effName.trim() && !sErr && !!effSlug

  const submit = async () => {
    if (!canSubmit) return
    setBusy(true)
    setError(null)
    try {
      const text = effPrompt.trim()
      const created = await projectsApi.createApp(pid, {
        name: effName.trim(),
        definition: BLANK_DEFINITION,
        description: text.slice(0, 1000),
      })
      qc.setQueryData(qk.app(created.id), created)
      await saveApp(qc, created.id, { definition: { ...(created.definition ?? {}) }, studio: { route_ids: selectedRoutes.map((r) => r.id) } })
      void invalidate.appsChanged(qc, pid)
      onClose()
      navigate({
        to: '/p/$project/apps/$appId',
        params: { project: projectSlug, appId: created.id },
        search: { ...(text ? { prompt: text } : {}), slug: effSlug },
      })
    } catch (e) {
      setError(errorMessage(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault()
        if (e.target !== e.currentTarget) return
        void submit()
      }}
      onKeyDown={(e) => {
        if (!e.currentTarget.contains(e.target as Node)) return // portaled popovers
        if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
          e.preventDefault()
          void submit()
        }
      }}
    >
      <div className="flex items-start gap-2.5 px-[22px] pr-12 pt-5">
        <span className="flex size-7 shrink-0 items-center justify-center rounded-lg bg-ai-soft text-ai-text">
          <WandSparkles className="size-3.5" />
        </span>
        <div className="min-w-0">
          <DialogTitle className="text-base font-semibold leading-[22px]">Create a UI from this backend</DialogTitle>
          <DialogDescription className="text-[13px] text-muted-foreground">
            The app builder receives these routes and a typed <span className="font-mono text-[11.5px]">api</span> client. No URLs to copy.
          </DialogDescription>
        </div>
      </div>

      <div className="flex max-h-[calc(100vh-240px)] flex-col gap-4 overflow-y-auto px-[22px] py-4" data-testid="create-ui-panel">
        <div className="flex flex-wrap gap-3">
          <div className="flex min-w-[220px] flex-1 flex-col gap-1.5">
            <label htmlFor="cu-name" className="text-xs font-medium">
              App name
            </label>
            <Input
              id="cu-name"
              value={effName}
              onChange={(e) => {
                setNameTouched(true)
                setName(e.target.value)
              }}
              maxLength={255}
              autoFocus
              data-testid="create-ui-name"
            />
          </div>
          <div className="flex w-[200px] flex-col gap-1.5">
            <label htmlFor="cu-slug" className="text-xs font-medium">
              Address when published
            </label>
            <div className={cn('flex h-8 items-center rounded-lg border border-input bg-background pl-2.5 focus-within:border-ring focus-within:ring-3 focus-within:ring-ring/50', sErr && 'border-destructive')}>
              <span className="font-mono text-xs text-muted-foreground">/a/</span>
              <input
                id="cu-slug"
                value={effSlug}
                maxLength={63}
                onChange={(e) => {
                  setSlugTouched(true)
                  setSlug(e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, ''))
                }}
                className="h-full min-w-0 flex-1 bg-transparent px-1.5 font-mono text-xs outline-none"
                autoComplete="off"
                data-testid="create-ui-slug"
              />
            </div>
            {sErr && (
              <span className="text-[11.5px] leading-4 text-destructive" role="alert" data-testid="create-ui-slug-error">
                {sErr}
              </span>
            )}
          </div>
        </div>

        <div className="flex flex-col gap-1.5">
          <div className="flex items-center">
            <span className="text-xs font-medium">Routes the UI should use</span>
            <div className="flex-1" />
            {routes.length > 0 && (
              <span className="text-xs text-muted-foreground" data-testid="create-ui-routes-count">
                {selectedRoutes.length} of {eligible.length}
                {revision != null && ` · spec rev ${revision}`}
              </span>
            )}
          </div>
          {routesQ.isLoading ? (
            <div className="flex flex-col gap-2 rounded-[10px] border border-border p-3">
              <Skeleton className="h-4 w-3/4" />
              <Skeleton className="h-4 w-2/3" />
            </div>
          ) : routesQ.isError ? (
            <div className="flex items-center gap-2 rounded-[10px] border border-border px-3 py-2.5 text-xs">
              <span className="flex-1 text-destructive">Couldn't load routes: {errorMessage(routesQ.error)}</span>
              <Button variant="outline" size="xs" type="button" onClick={() => routesQ.refetch()}>
                Retry
              </Button>
            </div>
          ) : routes.length === 0 ? (
            <div className="rounded-[10px] border border-dashed border-border px-3 py-3 text-xs leading-5 text-muted-foreground" data-testid="create-ui-no-routes">
              {project.name} has no routes yet, so this starts as a blank app. Attach routes later from the composer chip, or{' '}
              <Link
                to="/p/$project/backend/$module"
                params={{ project: projectSlug, module: '~new' }}
                className="font-medium text-primary hover:underline"
                onClick={onClose}
              >
                generate a backend first
              </Link>
              .
            </div>
          ) : (
            <>
              <div className="overflow-hidden rounded-[10px] border border-border" data-testid="create-ui-routes">
                {routes.map((r) => {
                  const serverOnly = r.auth === 'api_key'
                  const on = (selected ?? []).includes(r.id)
                  const known = hasSchema(r.response_schema)
                  const moduleName = r.handler.type === 'python' ? (r.handler.module_name ?? moduleById.get(r.handler.module_id)) : undefined
                  return (
                    <div
                      key={r.id}
                      className={cn(
                        'flex h-10 min-w-0 items-center gap-2.5 border-b border-border/65 px-3 last:border-b-0',
                        serverOnly && 'opacity-60',
                      )}
                      data-testid={`create-ui-route-${r.name}`}
                      data-checked={on || undefined}
                    >
                      <Checkbox
                        checked={on}
                        disabled={serverOnly}
                        aria-label={`${r.method} ${r.path}`}
                        onCheckedChange={(c) =>
                          setSelected((prev) => {
                            const cur = prev ?? []
                            return c ? [...new Set([...cur, r.id])] : cur.filter((x) => x !== r.id)
                          })
                        }
                        data-testid={`create-ui-route-check-${r.name}`}
                      />
                      <MethodBadge method={r.method} />
                      <span className="shrink-0 font-mono text-[12.5px] text-foreground">{r.path}</span>
                      <span className="min-w-0 truncate font-mono text-[11.5px] text-muted-foreground">{opSignature(r)}</span>
                      <div className="min-w-2 flex-1" />
                      {serverOnly ? (
                        <Badge variant="neutral">server-only</Badge>
                      ) : r.handler.type === 'stub' ? (
                        <Badge variant="stub">stub</Badge>
                      ) : known ? (
                        <Badge variant="success">
                          <Check /> response shape known
                        </Badge>
                      ) : (
                        <>
                          <Badge variant="neutral">no schema yet</Badge>
                          {moduleName && (
                            <Button
                              variant="ghost"
                              size="xs"
                              type="button"
                              data-testid={`create-ui-test-now-${r.name}`}
                              render={
                                <Link
                                  to="/p/$project/backend/$module"
                                  params={{ project: projectSlug, module: moduleName }}
                                  search={{ route: r.name }}
                                  onClick={onClose}
                                />
                              }
                            >
                              Test now
                            </Button>
                          )}
                        </>
                      )}
                    </div>
                  )
                })}
              </div>
              <span className="text-[11.5px] leading-4 text-muted-foreground">
                Routes without a schema still work: the AI sees the method and path. A test run fills the schema in one click.
              </span>
            </>
          )}
        </div>

        <div className="flex flex-col gap-1.5">
          <span className="text-xs font-medium">Starting point</span>
          <div className="grid grid-cols-4 gap-2" role="radiogroup" aria-label="Starting point">
            {TEMPLATES.map((t) => (
              <button
                key={t.key}
                type="button"
                role="radio"
                aria-checked={template === t.key}
                onClick={() => setTemplate(t.key)}
                className={cn(
                  'flex flex-col gap-2 rounded-[10px] border border-border bg-background p-2.5 text-left text-xs font-medium hover:border-ring/60',
                  template === t.key && 'border-primary ring-3 ring-primary/14',
                )}
                data-testid={`create-ui-template-${t.key}`}
              >
                <TemplatePreview t={t.key} />
                {t.label}
              </button>
            ))}
          </div>
        </div>

        <div className="flex flex-col gap-1.5">
          <label htmlFor="cu-prompt" className="text-xs font-medium">
            Describe the UI
          </label>
          <Textarea
            id="cu-prompt"
            rows={3}
            value={effPrompt}
            onChange={(e) => {
              setPromptTouched(true)
              setPrompt(e.target.value)
            }}
            placeholder="What should the app show and do?"
            className="min-h-[76px] leading-5"
            data-testid="create-ui-prompt"
          />
        </div>
        {error && (
          <div className="text-[11.5px] text-destructive" role="alert" data-testid="create-ui-error">
            {error}
          </div>
        )}
      </div>

      <div className="flex items-center gap-2 rounded-b-xl border-t border-border bg-muted/40 px-[22px] py-3.5">
        <Info className="size-3.5 shrink-0 text-muted-foreground" />
        <span className="text-xs leading-4 text-muted-foreground" data-testid="create-ui-footnote">
          Preview uses <b className="font-medium text-foreground">{project.default_environment}</b> secrets, published uses{' '}
          <b className="font-medium text-foreground">{project.live_environment}</b>. Tables are shared: the preview writes real rows.
        </span>
        <div className="flex-1" />
        <Button variant="ghost" size="sm" type="button" onClick={onClose}>
          Cancel
        </Button>
        <Button size="sm" type="submit" disabled={!canSubmit} data-testid="create-ui-submit">
          Create UI
          <Kbd className="ml-0.5 border-primary-foreground/30 bg-primary-foreground/15 text-primary-foreground">⌘⏎</Kbd>
        </Button>
      </div>
    </form>
  )
}
