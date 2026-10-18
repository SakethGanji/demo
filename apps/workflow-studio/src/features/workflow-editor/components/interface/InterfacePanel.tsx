/**
 * Right-panel "Interface" tab (DESIGN-STAGE5-UIS §0, §11.1): how people use this workflow. Pick Form / Chat / Custom UI,
 * see the URL, preview a screen on real run data, ask for a change, promote to an app. Using it live (chat
 * included) happens in the canvas "Try it" dock (`InterfaceDock`); runs started from either are followed on the canvas. Everything here edits the
 * draft's `interface` key through the interface endpoints; Publish makes it live.
 */
import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate } from '@tanstack/react-router'
import { useQueryClient } from '@tanstack/react-query'
import { AppWindow, Copy, Download, ExternalLink, FileText, LayoutTemplate, Loader2, MessageSquare, Rocket } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/shared/components/ui/button'
import { Callout } from '@/shared/components/ui/surface'
import { EmptyState } from '@/shared/components/ui/empty-state'
import { Field } from '@/shared/components/ui/field'
import { Input } from '@/shared/components/ui/input'
import { Skeleton } from '@/shared/components/ui/skeleton'
import { interfacesApi } from '@/shared/api/endpoints'
import { errorMessage } from '@/shared/api/http'
import { invalidate } from '@/shared/api/queryKeys'
import { cn } from '@/shared/lib/utils'
import { useInterfaceState, useSaveInterface } from '@/features/interfaces/hooks/useInterfaceState'
import { interfacePath, previewScreenIds, switchKind, validateSlug } from '@/features/interfaces/lib/interfaceDraft'
import type { Interface, InterfaceKind, InterfaceState, ScreenSpec } from '@/features/interfaces/lib/types'
import { useEditorProjectStore } from '../../stores/editorProjectStore'
import { useWorkflowStore } from '../../stores/workflowStore'
import { saveDraftForRun, describeActionError } from '../../lib/nodeActions'
import { useFollowRun } from '../../hooks/useViewingRun'
import { AskBar } from './AskBar'
import { InterfacePreview } from './InterfacePreview'
import { WiredChips } from './WiredChips'

const KINDS: { kind: InterfaceKind; title: string; sub: string; icon: typeof FileText }[] = [
  { kind: 'form', title: 'Form', sub: 'Fields from the trigger', icon: FileText },
  { kind: 'chat', title: 'Chat', sub: 'Talk to the workflow', icon: MessageSquare },
  { kind: 'custom', title: 'Custom UI', sub: 'Screens you shape', icon: LayoutTemplate },
]

function UrlRow({ projectSlug, state }: { projectSlug: string; state: InterfaceState }) {
  const i = state.interface
  if (!i) return null
  const path = interfacePath(projectSlug, i.slug)
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(`${window.location.origin}${path}`)
      toast.success('Link copied')
    } catch {
      toast.error('Could not copy the link')
    }
  }
  return (
    <div className="flex items-center gap-2" data-testid="interface-url" data-live={state.live ? 'true' : 'false'}>
      <span className="min-w-0 truncate rounded-chip bg-recess px-2 py-1 font-mono text-[12px]" title={path}>{path}</span>
      {state.live ? (
        <a href={path} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 text-[12px] text-primary hover:underline" aria-label="Open the live page">
          Live <ExternalLink className="size-3" />
        </a>
      ) : (
        <span className="shrink-0 text-[12px] text-muted-foreground">Publish to go live</span>
      )}
      <Button variant="ghost" size="icon-xs" className="ml-auto shrink-0" onClick={copy} aria-label="Copy link"><Copy /></Button>
    </div>
  )
}

function SlugField({ iface, onCommit }: { iface: Interface; onCommit: (slug: string) => void }) {
  const [v, setV] = useState(iface.slug)
  const [touched, setTouched] = useState(false)
  const err = touched ? validateSlug(v) : null
  const commit = () => {
    setTouched(true)
    if (v === iface.slug || validateSlug(v)) return
    onCommit(v)
  }
  return (
    <Field label="URL name" error={err} errorTestId="interface-slug-error">
      <Input
        value={v}
        spellCheck={false}
        maxLength={63}
        aria-invalid={err ? true : undefined}
        onChange={(e) => setV(e.target.value.toLowerCase())}
        onBlur={commit}
        onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); commit() } }}
        data-testid="interface-slug-input"
      />
    </Field>
  )
}

function PanelBody({ workflowId, state }: { workflowId: string; state: InterfaceState }) {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const projectId = useEditorProjectStore((s) => s.projectId)
  const projectSlug = useEditorProjectStore((s) => s.slug)
  const save = useSaveInterface(workflowId)
  const followRun = useFollowRun()
  const iface = state.interface
  const [busy, setBusy] = useState(false)
  const [promoting, setPromoting] = useState(false)
  const [picked, setPicked] = useState('start')
  const stash = useRef<Record<string, ScreenSpec> | undefined>(undefined)
  if (iface?.kind === 'custom' && iface.screens && Object.keys(iface.screens).length) stash.current = iface.screens

  const screenIds = previewScreenIds(iface)
  const screenId = screenIds.includes(picked) ? picked : screenIds[0]

  const fail = (title: string, e: unknown) => toast.error(title, { description: describeActionError(e).message })

  const chooseKind = async (kind: InterfaceKind) => {
    if (busy || iface?.kind === kind) return
    setBusy(true)
    try {
      // the engine builds the interface from the stored draft, so store the canvas first
      if (useWorkflowStore.getState()._isDirty) await saveDraftForRun()
      const { interface: generated } = await interfacesApi.generate(workflowId, { kind, slug: iface?.slug })
      await save.mutateAsync(switchKind(iface, generated, stash.current))
    } catch (e) {
      fail("Couldn't set up the interface", e)
    } finally {
      setBusy(false)
    }
  }

  const turnOff = async () => {
    if (busy || !iface) return
    setBusy(true)
    try {
      await save.mutateAsync(null)
    } catch (e) {
      fail("Couldn't turn the interface off", e)
    } finally {
      setBusy(false)
    }
  }

  const setSlug = async (slug: string) => {
    if (!iface) return
    try {
      await save.mutateAsync({ ...iface, slug })
    } catch (e) {
      fail("Couldn't change the URL name", e)
    }
  }

  const promote = async () => {
    setPromoting(true)
    try {
      const res = await interfacesApi.promote(workflowId)
      await qc.invalidateQueries({ queryKey: ['interfaces', workflowId] })
      void invalidate.appsChanged(qc, projectId)
      toast.success(res.created ? 'Promoted to an app' : 'Already an app')
      await navigate({ to: '/p/$project/apps/$appId', params: { project: projectSlug, appId: res.app_id } })
    } catch (e) {
      fail("Couldn't promote to an app", e)
    } finally {
      setPromoting(false)
    }
  }

  const downloadClient = async () => {
    try {
      const text = await interfacesApi.clientTs(workflowId)
      const url = URL.createObjectURL(new Blob([text], { type: 'text/plain' }))
      const a = document.createElement('a')
      a.href = url
      a.download = 'client.ts'
      a.click()
      URL.revokeObjectURL(url)
    } catch (e) {
      toast.error("Couldn't download the client", { description: errorMessage(e) })
    }
  }

  const promoted = !!iface?.app_id

  return (
    <div className="flex h-full min-h-0 flex-col gap-3 overflow-y-auto p-3 pb-28" data-testid="interface-panel" data-kind={iface?.kind ?? 'off'}>
      <div className="grid grid-cols-3 gap-2" role="group" aria-label="Interface kind">
        {KINDS.map(({ kind, title, sub, icon: Icon }) => {
          const on = iface?.kind === kind
          return (
            <button
              key={kind}
              type="button"
              aria-pressed={on}
              disabled={busy}
              onClick={() => chooseKind(kind)}
              className={cn(
                'flex min-w-0 flex-col items-start gap-1 rounded-ctl p-2.5 text-left transition-colors disabled:opacity-60',
                on ? 'bg-raise shadow-raise-sm' : 'bg-recess hover:bg-(--hover-fill)',
              )}
              data-testid={`interface-kind-${kind}`}
            >
              <Icon className={cn('size-4', on ? 'text-primary' : 'text-muted-foreground')} />
              <span className="text-[12.5px] font-medium text-foreground">{title}</span>
              <span className="text-[11px] leading-[14px] text-muted-foreground">{sub}</span>
            </button>
          )
        })}
      </div>
      <div className="flex items-center justify-between">
        <span className="text-[12px] text-muted-foreground">{busy ? 'Working…' : iface ? (state.live ? 'Draft interface (live page keeps serving until you publish)' : 'Draft interface (not published yet)') : state.live ? 'No draft interface (live page still served until you publish)' : 'No interface yet'}</span>
        <Button variant="ghost" size="xs" onClick={turnOff} disabled={busy || !iface} data-testid="interface-off">Turn off</Button>
      </div>

      {iface && (
        <>
          <UrlRow projectSlug={projectSlug} state={state} />
          <SlugField key={iface.slug} iface={iface} onCommit={setSlug} />

          <InterfacePreview workflowId={workflowId} projectId={projectId} iface={iface} screenId={screenId} onScreen={setPicked} onRun={followRun} />

          <WiredChips wired={state.wired} />

          {iface.kind === 'custom' && (
            <AskBar
              workflowId={workflowId}
              iface={iface}
              screenId={screenId}
              disabledReason={promoted ? 'Promoted: edit this in the app builder' : undefined}
            />
          )}

          {state.warnings.length > 0 && (
            <div className="flex flex-col gap-1" data-testid="interface-warnings">
              {state.warnings.map((w, i) => (
                <Callout key={`${w.code}-${i}`} tone={w.severity === 'error' ? 'error' : w.severity === 'info' ? 'info' : 'warn'} data-code={w.code}>
                  <span className="font-medium">{w.code}</span> {w.message}
                </Callout>
              ))}
            </div>
          )}

          <div className="flex flex-wrap items-center gap-2">
            {promoted ? (
              <Button
                variant="outline"
                size="sm"
                render={<Link to="/p/$project/apps/$appId" params={{ project: projectSlug, appId: iface.app_id as string }} />}
                data-testid="interface-open-app"
              >
                <AppWindow /> Promoted to app · Open app builder
              </Button>
            ) : (
              <Button variant="outline" size="sm" onClick={promote} disabled={promoting} data-testid="interface-promote">
                {promoting ? <Loader2 className="animate-spin" /> : <Rocket />} Promote to app
              </Button>
            )}
            <Button variant="ghost" size="sm" onClick={downloadClient} data-testid="interface-client-download">
              <Download /> client.ts
            </Button>
          </div>
        </>
      )}
    </div>
  )
}

export default function InterfacePanel() {
  const workflowId = useWorkflowStore((s) => s.workflowId)
  const isActive = useWorkflowStore((s) => s.isActive)
  const saved = !!workflowId && workflowId !== 'new'
  const q = useInterfaceState(workflowId)
  const refetch = q.refetch

  // publishing / unpublishing changes `live`
  const first = useRef(true)
  useEffect(() => {
    if (first.current) { first.current = false; return }
    if (saved) void refetch()
  }, [isActive, saved, refetch])

  if (!saved) {
    return (
      <div className="p-3" data-testid="interface-panel" data-kind="unsaved">
        <EmptyState icon={<LayoutTemplate />} title="Save the workflow first">
          Give people a form, a chat or a custom screen to use this workflow once it is saved.
        </EmptyState>
      </div>
    )
  }
  if (q.isPending) {
    return (
      <div className="flex flex-col gap-2 p-3" data-testid="interface-panel" data-kind="loading">
        <Skeleton className="h-16 w-full" />
        <Skeleton className="h-24 w-full" />
      </div>
    )
  }
  if (q.isError) {
    return (
      <div className="p-3" data-testid="interface-panel" data-kind="error">
        <Callout tone="error" role="alert">Could not load the interface. {errorMessage(q.error)}</Callout>
      </div>
    )
  }
  return <PanelBody workflowId={workflowId} state={q.data} />
}
