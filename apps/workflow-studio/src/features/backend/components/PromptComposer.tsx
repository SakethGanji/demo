/**
 * PromptComposer (UI-DESIGN §4.1–§4.2, backend mockup): the AI bar (prompt → Generate/Refine ⏎, Stop Esc) and the
 * inline change flow under it: a shimmering status, a step log mapped from the generator's phases, the
 * explanation typed out, the diff (kept or discarded), then the result row (diff stat, routes, self-test, missing
 * secrets, Discard, Diff, module-name field on a conflict) and one "Create table on save" row per declared table.
 */
import { forwardRef, useEffect, useImperativeHandle, useMemo, useRef, useState } from 'react'
import { ChevronDown, CircleX, Ellipsis, GitCompare, Loader2, Plug, RotateCcw, Sparkles, Square, WandSparkles, X } from 'lucide-react'
import { Button } from '@/shared/components/ui/button'
import { Checkbox } from '@/shared/components/ui/checkbox'
import { DiffApplied, DiffView } from '@/shared/components/ui/diff-view'
import { Kbd } from '@/shared/components/ui/kbd'
import { Callout, IconTile } from '@/shared/components/ui/surface'
import { Tip } from '@/shared/components/ui/tooltip'
import { Shimmer } from '@/shared/components/ai-elements/shimmer'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/shared/components/ui/dropdown-menu'
import { cn } from '@/shared/lib/utils'
import type { GenPhase, GenState } from '../useGenerate'
import type { DraftState } from '../docStore'
import { TEMPLATE_LABELS, type TemplateKey } from '../templates'
import { FlowLog, FlowSay, type FlowStep } from '../ui/AiFlow'
import { useElapsed } from '../ui/flowHooks'

export interface ComposerHandle {
  focus: () => void
}

const ORDER: GenPhase[] = ['context', 'generating', 'analysing', 'repairing', 'testing']
/** the rows always shown while a run is in flight; "repairing" only appears if the generator needed it */
const BASE: GenPhase[] = ['context', 'generating', 'analysing', 'testing']

const LABEL: Record<GenPhase, { run: string; done: string }> = {
  context: { run: 'Reading the project', done: 'Read project' },
  generating: { run: 'Writing the module', done: 'Wrote module' },
  analysing: { run: 'Checking the code', done: 'Syntax OK' },
  repairing: { run: 'Repairing the draft', done: 'Repaired the draft' },
  testing: { run: 'Running self-test', done: 'Self-test' },
}

interface RunLog {
  startedAt: number
  endedAt: number | null
  /** when each phase first appeared */
  starts: Partial<Record<GenPhase, number>>
  seen: GenPhase[]
}

export const PromptComposer = forwardRef<
  ComposerHandle,
  {
    state: GenState
    draft: DraftState | null
    /** the editor text (the draft, once generated) */
    source: string
    hasSource: boolean
    promptCount: number
    lastPrompt: string | null
    initialPrompt?: string
    narrow: boolean
    veryNarrow: boolean
    diffStat: { added: number; removed: number } | null
    missingSecrets: { count: number; envs: string[] }
    nameConflict: boolean
    onGenerate: (prompt: string) => void
    onStop: () => void
    onDiscard: () => void
    onDiff: () => void
    onToggleTable: (name: string, create: boolean) => void
    onEditColumns: (name: string) => void
    onRenameDraftModule: (name: string) => void
    onTemplate: (k: TemplateKey) => void
    onRetry: () => void
  }
>(function PromptComposer(p, ref) {
  const [text, setText] = useState(p.initialPrompt ?? '')
  const [editing, setEditing] = useState(!p.lastPrompt)
  const [nameDraft, setNameDraft] = useState(p.draft?.moduleName ?? '')
  const [open, setOpen] = useState(true)
  const [kept, setKept] = useState(false)
  const [fresh, setFresh] = useState(false)
  const [log, setLog] = useState<RunLog | null>(null)
  const [pending, setPending] = useState<string | null>(null)
  const [showSteps, setShowSteps] = useState(true)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  useImperativeHandle(ref, () => ({ focus: () => inputRef.current?.focus() }))
  useEffect(() => setNameDraft(p.draft?.moduleName ?? ''), [p.draft?.moduleName])
  useEffect(() => {
    if (p.initialPrompt) setText(p.initialPrompt)
  }, [p.initialPrompt])

  const streaming = p.state.kind === 'streaming'
  const refine = p.hasSource
  const submit = () => {
    const t = text.trim()
    if (!t || streaming) return
    p.onGenerate(t)
    setPending(t)
    setText('')
    setEditing(false)
  }

  // ── run log: when each real phase appeared, so the step log can show durations ──
  const streamState = p.state.kind === 'streaming' ? p.state : null
  const phase = streamState?.status?.phase
  useEffect(() => {
    if (!streamState) return
    setOpen(true)
    setKept(false)
    setFresh(false)
    setShowSteps(true)
    setLog((l) => {
      const same = !!l && l.startedAt === streamState.startedAt
      const base: RunLog = same ? l! : { startedAt: streamState.startedAt, endedAt: null, starts: {}, seen: [] }
      const missing = streamState.phases.filter((ph) => base.starts[ph] == null)
      if (same && !missing.length) return l
      const starts = { ...base.starts }
      for (const ph of missing) starts[ph] = Date.now()
      return { ...base, starts, seen: [...streamState.phases] }
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [streamState?.startedAt, streamState?.phases.length, phase])
  const wasStreaming = useRef(false)
  useEffect(() => {
    if (wasStreaming.current && !streaming) {
      setLog((l) => (l && l.endedAt == null ? { ...l, endedAt: Date.now() } : l))
      setFresh(true)
      setShowSteps(false)
      setPending(null)
    }
    wasStreaming.current = streaming
  }, [streaming])
  // a discarded or cleared draft ends the flow
  useEffect(() => {
    if (!p.draft && !streaming) {
      setLog(null)
      setKept(false)
    }
  }, [p.draft, streaming])

  useEffect(() => {
    if (!streaming) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault()
        p.onStop()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [streaming, p])

  // ⇧⌘↵ keeps the change while one is waiting
  const canKeep = !!p.draft && !streaming && !kept
  useEffect(() => {
    if (!canKeep) return
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.shiftKey && e.key === 'Enter') {
        e.preventDefault()
        e.stopImmediatePropagation()
        setKept(true)
      }
    }
    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
  }, [canKeep])

  const elapsed = useElapsed(log?.startedAt ?? null, streaming)
  const total = log?.endedAt && log.startedAt ? (log.endedAt - log.startedAt) / 1000 : null

  const steps: FlowStep[] = useMemo(() => {
    if (!log) return []
    const seen = log.seen
    const rows = streaming ? ORDER.filter((ph) => BASE.includes(ph) || seen.includes(ph)) : ORDER.filter((ph) => seen.includes(ph))
    const draftModule = streamState?.draftModule ?? p.draft?.moduleName ?? null
    const st = p.draft?.selfTest?.filter((r) => !r.skipped) ?? []
    return rows.map((ph, i) => {
      const isSeen = seen.includes(ph)
      const state: FlowStep['state'] = !isSeen ? 'pending' : streaming && ph === phase ? 'running' : 'done'
      const next = rows.slice(i + 1).find((x) => log.starts[x] != null)
      const end = next ? log.starts[next] : log.endedAt
      const secs = state === 'done' && log.starts[ph] != null && end != null ? Math.max(0, (end - log.starts[ph]!) / 1000) : null
      let done = LABEL[ph].done
      if (ph === 'generating' && draftModule && draftModule !== '~new') done = `Wrote ${draftModule}.py`
      if (ph === 'testing' && !streaming && st.length) done = `Ran self-test · ${st.filter((r) => r.ok).length}/${st.length} passed`
      return { phase: ph, state, secs, run: LABEL[ph].run, done }
    })
  }, [log, streaming, phase, streamState?.draftModule, p.draft])

  const shell = 'mx-3 mb-3'

  if (p.state.kind === 'unavailable')
    return (
      <div className={cn(shell, 'flex flex-col gap-2')} data-testid="composer-unavailable">
        <div className="flex items-center gap-2 rounded-well bg-recess p-1 pl-2">
          <IconTile hue="ai" className="size-6">
            <Sparkles />
          </IconTile>
          <span className="flex-1 text-[13px] text-muted-foreground">Describe your API…</span>
          <Button variant="ai" size="sm" disabled>
            <WandSparkles /> Generate
          </Button>
        </div>
        <Callout tone="info" icon={<Plug />}>
          <div className="flex items-center gap-2">
            <span className="flex-1">AI isn't configured on this server (no LLM key).</span>
            <DropdownMenu>
              <DropdownMenuTrigger render={<Button variant="outline" size="xs" data-testid="start-from-template" />}>Start from a template</DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                {(Object.keys(TEMPLATE_LABELS) as TemplateKey[]).map((k) => (
                  <DropdownMenuItem key={k} onClick={() => p.onTemplate(k)} data-testid={`template-menu-${k}`}>
                    {TEMPLATE_LABELS[k]}
                  </DropdownMenuItem>
                ))}
              </DropdownMenuContent>
            </DropdownMenu>
            <Button variant="ghost" size="xs" onClick={p.onRetry}>
              Retry
            </Button>
          </div>
        </Callout>
      </div>
    )

  const showFlow = open && (streaming || (!!p.draft && steps.length > 0) || (!!p.draft && !!p.draft.summary))
  const summary = p.draft?.summary ?? ''
  const showDiff = !!p.draft && !streaming && p.draft.previousSource !== p.source
  const stat = p.diffStat ?? { added: 0, removed: 0 }

  return (
    <div className={cn(shell, 'flex flex-col')} data-testid="composer" data-state={p.state.kind}>
      {/* the AI bar */}
      <div className="flex items-start gap-2 rounded-well bg-recess p-1 pl-2 transition-shadow duration-(--d-fast) focus-within:shadow-focus">
        <IconTile hue="ai" className="mt-0.5 size-6">
          <Sparkles />
        </IconTile>
        {streaming || (!editing && p.lastPrompt) ? (
          <div className="min-w-0 flex-1 truncate py-1 text-[13px] leading-5" data-testid="composer-prompt">
            {p.promptCount > 1 && !pending && <span className="text-muted-foreground">{ordinal(p.promptCount)} prompt · </span>}
            {pending ?? p.lastPrompt}
          </div>
        ) : (
          <textarea
            ref={inputRef}
            value={text}
            rows={Math.min(4, Math.max(1, text.split('\n').length))}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault()
                submit()
              }
              if (e.key === 'Escape' && p.lastPrompt) setEditing(false)
            }}
            placeholder={refine ? 'Ask for a change…' : 'Describe your API…'}
            aria-label="Ask AI for a change"
            className="min-w-0 flex-1 resize-none bg-transparent py-1 text-[13px] leading-5 outline-none placeholder:text-muted-foreground"
            data-testid="composer-input"
          />
        )}
        {streaming ? (
          <Button variant="outline" size="sm" onClick={p.onStop} data-testid="composer-stop">
            <Square className="size-3" /> Stop <Kbd>Esc</Kbd>
          </Button>
        ) : (
          <>
            {!editing && p.lastPrompt && (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  setEditing(true)
                  setTimeout(() => inputRef.current?.focus(), 0)
                }}
                data-testid="composer-edit"
              >
                Edit
              </Button>
            )}
            <Tip label="Apply the change with AI" kbd="⏎">
              <Button
                variant="ai"
                size="sm"
                disabled={editing && !text.trim()}
                onClick={() => {
                  if (!editing) {
                    setEditing(true)
                    setTimeout(() => inputRef.current?.focus(), 0)
                  } else submit()
                }}
                data-testid="composer-submit"
              >
                <WandSparkles /> {refine ? 'Refine' : 'Generate'} <Kbd>⏎</Kbd>
              </Button>
            </Tip>
          </>
        )}
      </div>

      {p.state.kind === 'error' && (
        <Callout tone="error" icon={<CircleX />} className="mt-2" data-testid="composer-error" data-code={p.state.code}>
          <div className="flex items-center gap-2">
            <span className="line-clamp-3 flex-1" title={p.state.message}>
              {p.state.message}
            </span>
            <Button variant="ghost" size="xs" onClick={p.onRetry} data-testid="composer-retry">
              <RotateCcw /> Retry
            </Button>
          </div>
        </Callout>
      )}
      {p.state.kind === 'busy' && (
        <Callout tone="warn" icon={<Loader2 className="animate-spin" />} className="mt-2" data-testid="composer-busy">
          <div className="flex items-center gap-2">
            <span className="flex-1">{p.state.message}</span>
            <Button variant="ghost" size="xs" onClick={p.onRetry} data-testid="composer-wait">
              Retry
            </Button>
          </div>
        </Callout>
      )}

      {/* the inline change flow */}
      <div
        className={cn(
          'grid transition-[grid-template-rows,opacity,margin] duration-(--d-slow) ease-(--ease-io)',
          showFlow ? 'mt-2 grid-rows-[1fr] opacity-100' : 'grid-rows-[0fr] opacity-0',
        )}
        aria-live="polite"
        data-testid="composer-flow"
        data-open={showFlow}
      >
        <div className="min-h-0 overflow-hidden">
          {showFlow && (
            <div className="flex flex-col gap-2">
              <div
                className="flex h-7 items-center gap-2 pl-2 text-[12.5px]"
                data-testid={streaming ? 'composer-streaming' : 'composer-flow-head'}
                data-phase={streaming ? (phase ?? 'starting') : undefined}
              >
                <Sparkles className={cn('size-3.5 shrink-0', streaming ? 'text-primary' : 'text-muted-foreground')} />
                <span className="min-w-0 flex-1 truncate">
                  {streaming ? (
                    <>
                      <Shimmer as="span" duration={1.8}>
                        {streamState?.status?.message ?? 'Starting…'}
                      </Shimmer>
                      {streamState?.status && (
                        <span className="text-muted-foreground">
                          {' '}
                          · attempt {streamState.status.attempt}
                          {streamState.status.route ? ` · ${streamState.status.route}` : ''}
                        </span>
                      )}
                    </>
                  ) : kept ? (
                    <span className="text-muted-foreground">
                      <b className="font-medium text-foreground">Kept</b> · in the editor, not saved yet
                    </span>
                  ) : (
                    <span className="text-muted-foreground">
                      <b className="font-medium text-foreground">{p.draft?.invalid ? 'Draft ready' : '1 change ready'}</b> · in the editor, review before saving
                    </span>
                  )}
                </span>
                <span className="font-mono text-[11.5px] text-muted-foreground tabular-nums">
                  {streaming ? `${elapsed.toFixed(1)}s` : total != null ? `${total.toFixed(1)}s` : ''}
                </span>
                {!streaming && steps.length > 0 && (
                  <Tip label={showSteps ? 'Hide steps' : 'Show steps'}>
                    <Button variant="ghost" size="icon-xs" aria-label="Toggle steps" aria-expanded={showSteps} onClick={() => setShowSteps((v) => !v)} data-testid="composer-steps-toggle">
                      <ChevronDown className={cn('transition-transform duration-(--d-base) ease-(--ease-io)', !showSteps && '-rotate-90')} />
                    </Button>
                  </Tip>
                )}
                {!streaming && (
                  <Tip label="Dismiss">
                    <Button variant="ghost" size="icon-xs" aria-label="Dismiss" onClick={() => setOpen(false)} data-testid="composer-flow-dismiss">
                      <X />
                    </Button>
                  </Tip>
                )}
              </div>
              <div className="flex max-h-[min(380px,42vh)] flex-col gap-3 overflow-y-auto overscroll-contain pt-1">
                {steps.length > 0 && (streaming || showSteps) && <FlowLog steps={steps} />}
                {!streaming && summary && <FlowSay key={summary} text={summary} animate={fresh} />}
                {showDiff &&
                  (kept ? (
                    <DiffApplied
                      summary={`${p.draft?.moduleName && p.draft.moduleName !== '~new' ? `${p.draft.moduleName}.py` : 'Module'} changed`}
                      added={stat.added}
                      removed={stat.removed}
                      onUndo={() => {
                        setKept(false)
                        p.onDiscard()
                      }}
                      testId="composer-applied"
                    />
                  ) : (
                    <DiffView
                      prev={p.draft!.previousSource}
                      next={p.source}
                      title={p.draft?.moduleName && p.draft.moduleName !== '~new' ? `${p.draft.moduleName}.py` : undefined}
                      onApply={() => setKept(true)}
                      applyLabel="Keep"
                      maxHeight={220}
                      testId="composer-diff-view"
                    />
                  ))}
              </div>
            </div>
          )}
        </div>
      </div>

      {p.draft && !streaming && (
        <>
          <div className="mt-2 flex flex-wrap items-center gap-x-2.5 gap-y-1 pr-1 pl-2 text-xs" data-testid="composer-result">
            {p.draft.invalid ? <CircleX className="size-3.5 text-destructive" /> : <Sparkles className="size-3.5 text-muted-foreground" />}
            <span className="font-medium">{p.draft.invalid ? 'Draft' : p.promptCount > 1 ? 'Refined' : 'Generated'}</span>
            {p.diffStat && (
              <span className="font-mono text-[11.5px] text-muted-foreground tabular-nums" data-testid="composer-diffstat">
                <b className="font-medium text-success-text">+{p.diffStat.added}</b> <b className="font-medium text-destructive">−{p.diffStat.removed}</b>
              </span>
            )}
            {p.draft.invalid ? (
              <span className="text-destructive" data-testid="composer-problems">
                · {p.draft.analysis?.errors.length ?? 0} problem{(p.draft.analysis?.errors.length ?? 0) === 1 ? '' : 's'} left: fix before saving
              </span>
            ) : (
              <>
                {!p.narrow && (
                  <span className="text-muted-foreground" data-testid="composer-routes">
                    · {routeCount(p.draft)} routes
                  </span>
                )}
                {!p.veryNarrow && p.draft.selfTest && <SelfTestSummary results={p.draft.selfTest} />}
              </>
            )}
            {p.missingSecrets.count > 0 && (
              <span className="text-warning-text" data-testid="composer-missing-secrets">
                · {p.missingSecrets.count} secret missing{!p.veryNarrow && p.missingSecrets.envs.length ? ` in ${p.missingSecrets.envs.join(', ')}` : ''}
              </span>
            )}
            {p.draft.analysis?.warnings.some((w) => w.code === 'secret_scrubbed') && (
              <span className="truncate text-warning-text" data-testid="warning-secret_scrubbed" title="A secret value in your code or prompt was removed before sending it to the AI">
                · secret removed before sending
              </span>
            )}
            {p.draft.attempts > 1 && (
              <span className="text-muted-foreground" data-testid="composer-attempts" data-attempts={p.draft.attempts}>
                · {p.draft.attempts} attempts
              </span>
            )}
            <div className="ml-auto flex shrink-0 items-center gap-1">
            {p.nameConflict && (
              <span className="inline-flex items-center gap-1" data-testid="composer-module-name">
                <span className="text-muted-foreground">name</span>
                <input
                  value={nameDraft}
                  onChange={(e) => setNameDraft(e.target.value.trim())}
                  onBlur={() => nameDraft && nameDraft !== p.draft?.moduleName && p.onRenameDraftModule(nameDraft)}
                  onKeyDown={(e) => e.key === 'Enter' && (e.target as HTMLInputElement).blur()}
                  className="h-6 w-28 rounded-chip bg-recess px-1.5 font-mono text-[11.5px] shadow-[0_0_0_1.5px_var(--destructive)] outline-none"
                  data-testid="composer-module-name-input"
                />
              </span>
            )}
            <Button variant="ghost" size="xs" onClick={p.onDiscard} data-testid="composer-discard">
              Discard
            </Button>
            {p.narrow ? (
              <DropdownMenu>
                <DropdownMenuTrigger render={<Button variant="ghost" size="icon-xs" aria-label="More" data-testid="composer-more" />}>
                  <Ellipsis />
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end">
                  <DropdownMenuItem onClick={p.onDiff} data-testid="composer-diff">
                    <GitCompare /> Diff
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            ) : (
              <Button variant="ghost" size="xs" onClick={p.onDiff} data-testid="composer-diff">
                <GitCompare /> Diff
              </Button>
            )}
            </div>
          </div>
          {p.draft.tables
            .filter((t) => !(p.draft!.analysis?.tables.find((a) => a.name === t.name)?.exists ?? false))
            .map((t) => (
              <div
                key={t.name}
                className="mt-2 flex items-center gap-2.5 rounded-ctl bg-recess px-3 py-1.5 text-xs"
                data-testid="composer-tables"
                data-table={t.name}
              >
                <Checkbox checked={t.create} onCheckedChange={(c) => p.onToggleTable(t.name, !!c)} data-testid={`create-table-${t.name}`} />
                <span className="whitespace-nowrap">
                  Create table <code className="text-[11.5px]">{t.name}</code> on save
                </span>
                {!p.narrow && (
                  <span className="truncate font-mono text-[11px] text-muted-foreground">
                    {t.columns.map((c) => `${c.name} ${c.type}${c.required ? ' · required' : ''}`).join(' · ')}
                  </span>
                )}
                <div className="flex-1" />
                <Button variant="ghost" size="xs" onClick={() => p.onEditColumns(t.name)} data-testid={`edit-columns-${t.name}`}>
                  Edit columns
                </Button>
              </div>
            ))}
        </>
      )}
    </div>
  )
})

function routeCount(d: DraftState) {
  const r = d.analysis?.routes
  return r ? r.created.length + r.updated.length : 0
}

function SelfTestSummary({ results }: { results: Array<{ ok?: boolean; skipped?: string }> }) {
  const ran = results.filter((r) => !r.skipped)
  const passed = ran.filter((r) => r.ok).length
  return (
    <span className="text-muted-foreground" data-testid="composer-selftest" data-passed={passed} data-total={ran.length}>
      · self-test{' '}
      <span className="text-foreground">
        {passed} / {ran.length} passed
      </span>
    </span>
  )
}

function ordinal(n: number) {
  const s = ['th', 'st', 'nd', 'rd']
  const v = n % 100
  return n + (s[(v - 20) % 10] || s[v] || s[0])
}
