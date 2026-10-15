/**
 * PromptComposer (UI-DESIGN §4.1–§4.2, `backend.png`, `states.png`): prompt → Generate/Refine ⏎,
 * streaming phase line + chips + Stop (Esc), result row (diff stat, routes, self-test, missing
 * secrets, Discard, Diff — in ⋯ below 1360px), module-name chip on a name conflict, and one
 * "Create table on save" row per declared table.
 */
import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from 'react'
import { Check, CircleCheck, CircleX, Ellipsis, GitCompare, Loader2, Plug, RotateCcw, Sparkles, Square, WandSparkles } from 'lucide-react'
import { Button } from '@/shared/components/ui/button'
import { Checkbox } from '@/shared/components/ui/checkbox'
import { Kbd } from '@/shared/components/ui/kbd'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/shared/components/ui/dropdown-menu'
import { cn } from '@/shared/lib/utils'
import type { GenPhase, GenState } from '../useGenerate'
import type { DraftState } from '../docStore'
import { TEMPLATE_LABELS, type TemplateKey } from '../templates'

export interface ComposerHandle {
  focus: () => void
}

const PHASE_LABEL: Record<GenPhase, string> = {
  context: 'Read project',
  generating: 'Wrote module',
  analysing: 'Syntax OK',
  repairing: 'Repairing',
  testing: 'Self-test',
}

export const PromptComposer = forwardRef<
  ComposerHandle,
  {
    state: GenState
    draft: DraftState | null
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
    setText('')
    setEditing(false)
  }

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

  const shell = 'mx-3 mt-2.5 rounded-[10px] border shadow-xs'
  const aiBorder = 'border-ai/30 bg-[linear-gradient(180deg,color-mix(in_srgb,var(--ai)_4%,var(--card)),var(--card))]'

  if (p.state.kind === 'unavailable')
    return (
      <div className={cn(shell, 'border-border bg-card')} data-testid="composer-unavailable">
        <div className="flex items-center gap-2.5 px-3 py-2">
          <Avatar muted />
          <span className="flex-1 text-[13px] text-muted-foreground">Describe your API…</span>
          <Button variant="ai" size="sm" disabled>
            <WandSparkles /> Generate
          </Button>
        </div>
        <div className="flex items-center gap-2 border-t border-border px-3 py-2 text-xs">
          <Plug className="size-3.5 text-muted-foreground" />
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
      </div>
    )

  return (
    <div className={cn(shell, aiBorder)} data-testid="composer" data-state={p.state.kind}>
      <div className="flex items-start gap-2.5 py-2 pl-3 pr-2">
        <Avatar />
        {streaming || (!editing && p.lastPrompt) ? (
          <div className="min-w-0 flex-1 truncate py-1 text-[13px]" data-testid="composer-prompt">
            {p.promptCount > 1 && <span className="text-muted-foreground">{ordinal(p.promptCount)} prompt · </span>}
            {p.lastPrompt}
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
            className="min-w-0 flex-1 resize-none bg-transparent py-1 text-[13px] leading-5 outline-none placeholder:text-muted-foreground/80"
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
                size="xs"
                className="mt-0.5"
                onClick={() => {
                  setEditing(true)
                  setTimeout(() => inputRef.current?.focus(), 0)
                }}
                data-testid="composer-edit"
              >
                Edit
              </Button>
            )}
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
          </>
        )}
      </div>

      {p.state.kind === 'streaming' && <StreamingRow state={p.state} />}

      {p.state.kind === 'error' && (
        <div className="flex items-center gap-2 border-t border-dashed border-destructive/40 px-3 py-2 text-xs text-destructive" data-testid="composer-error" data-code={p.state.code}>
          <CircleX className="size-3.5" />
          <span className="flex-1">{p.state.message}</span>
          <Button variant="ghost" size="xs" onClick={p.onRetry} data-testid="composer-retry">
            <RotateCcw /> Retry
          </Button>
        </div>
      )}
      {p.state.kind === 'busy' && (
        <div className="flex items-center gap-2 border-t border-dashed border-warning/40 px-3 py-2 text-xs text-warning-text" data-testid="composer-busy">
          <Loader2 className="size-3.5 animate-spin" />
          <span className="flex-1">{p.state.message}</span>
          <Button variant="ghost" size="xs" onClick={p.onRetry} data-testid="composer-wait">
            Retry
          </Button>
        </div>
      )}

      {p.draft && p.state.kind !== 'streaming' && (
        <>
          <div className="flex items-center gap-2.5 whitespace-nowrap border-t border-dashed border-ai/25 px-3 py-[7px] text-xs" data-testid="composer-result">
            {p.draft.invalid ? <CircleX className="size-3.5 text-destructive" /> : <CircleCheck className="size-3.5 text-success" />}
            <span className="font-medium">{p.draft.invalid ? 'Draft' : p.promptCount > 1 ? 'Refined' : 'Generated'}</span>
            {p.diffStat && (
              <span className="tabular-nums text-muted-foreground" data-testid="composer-diffstat">
                <b className="font-semibold text-method-get">+{p.diffStat.added}</b> <b className="font-semibold text-method-delete">−{p.diffStat.removed}</b>
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
            <div className="flex-1" />
            {p.nameConflict && (
              <span className="inline-flex items-center gap-1" data-testid="composer-module-name">
                <span className="text-muted-foreground">name</span>
                <input
                  value={nameDraft}
                  onChange={(e) => setNameDraft(e.target.value.trim())}
                  onBlur={() => nameDraft && nameDraft !== p.draft?.moduleName && p.onRenameDraftModule(nameDraft)}
                  onKeyDown={(e) => e.key === 'Enter' && (e.target as HTMLInputElement).blur()}
                  className="h-6 w-28 rounded-md border border-destructive/60 bg-background px-1.5 font-mono text-[11.5px] outline-none"
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
          {p.draft.tables
            .filter((t) => !(p.draft!.analysis?.tables.find((a) => a.name === t.name)?.exists ?? false))
            .map((t) => (
              <div
                key={t.name}
                className="flex items-center gap-2.5 border-t border-border bg-ai/[0.03] px-3 py-[7px] text-xs"
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

function StreamingRow({ state }: { state: Extract<GenState, { kind: 'streaming' }> }) {
  const [now, setNow] = useState(Date.now())
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 500)
    return () => clearInterval(t)
  }, [])
  const st = state.status
  const secs = Math.round((now - state.startedAt) / 1000)
  const order: GenPhase[] = ['context', 'generating', 'analysing', 'repairing', 'testing']
  const current = st?.phase
  return (
    <div className="border-t border-dashed border-ai/25 px-3 py-2" data-testid="composer-streaming" data-phase={current ?? 'starting'}>
      <div className="flex items-center gap-2 text-xs">
        <Loader2 className="size-3.5 animate-spin text-ai" />
        <span className="animate-pulse font-medium text-ai-text">{st?.message ?? 'Starting…'}</span>
        {st && (
          <span className="text-muted-foreground">
            · attempt {st.attempt}
            {st.route ? ` · ${st.route}` : ''}
          </span>
        )}
        <span className="ml-auto tabular-nums text-muted-foreground">{secs} s</span>
      </div>
      <div className="mt-1.5 flex flex-wrap gap-1.5">
        {order
          .filter((ph) => state.phases.includes(ph))
          .map((ph) => {
            const done = ph !== current
            return (
              <span
                key={ph}
                data-testid={`phase-${ph}`}
                className={cn(
                  'inline-flex h-[22px] items-center gap-1 rounded-full px-2 text-[11.5px]',
                  done ? 'bg-success/11 text-success-text' : 'bg-ai-soft text-ai-text',
                )}
              >
                {done && <Check className="size-3" />}
                {ph === 'generating' && state.draftModule ? `Wrote ${state.draftModule}.py` : PHASE_LABEL[ph]}
              </span>
            )
          })}
      </div>
    </div>
  )
}

function Avatar({ muted }: { muted?: boolean }) {
  return (
    <span className={cn('mt-0.5 inline-flex size-6 shrink-0 items-center justify-center rounded-md', muted ? 'bg-muted text-muted-foreground' : 'bg-ai-soft text-ai')}>
      <Sparkles className="size-3.5" />
    </span>
  )
}

function ordinal(n: number) {
  const s = ['th', 'st', 'nd', 'rd']
  const v = n % 100
  return n + (s[(v - 20) % 10] || s[v] || s[0])
}
