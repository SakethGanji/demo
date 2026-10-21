/**
 * Small pieces of the setup guide and the NextSteps strip: the numbered marker (current step gets the ring),
 * the progress segments, the Later / Not for this project controls, the raised prompt box and the template list.
 */
import { Check, LayoutTemplate } from 'lucide-react'
import { Button } from '@/shared/components/ui/button'
import { cn } from '@/shared/lib/utils'
import type { TemplateSummary } from '@/features/templates/types'
import { HOME_TEMPLATES, type HomeTemplate } from '../../home/templates'

export function StepMarker({ n, done, current, className }: { n: number; done: boolean; current?: boolean; className?: string }) {
  return (
    <span
      className={cn(
        'inline-flex size-5 shrink-0 items-center justify-center rounded-full text-[11px] font-semibold transition-[background-color,box-shadow] duration-(--d-base) motion-reduce:transition-none [&_svg]:size-3 [&_svg]:stroke-[2.5]',
        done
          ? 'glass-orb bg-success text-primary-foreground'
          : current
            ? 'glass-orb bg-primary text-primary-foreground [--orb-ring:0_0_0_4px_var(--brand-soft)]'
            : 'glass-orb-clear text-muted-foreground',
        className,
      )}
      data-done={done ? 'true' : 'false'}
      data-current={current ? 'true' : undefined}
    >
      {done ? <Check /> : n}
    </span>
  )
}

export function Segments({ done, total }: { done: number; total: number }) {
  return (
    <span className="flex gap-1" aria-hidden>
      {Array.from({ length: total }, (_, i) => (
        <span key={i} className={cn('h-1 w-6 rounded-full transition-colors duration-(--d-base) motion-reduce:transition-none', i < done ? 'bg-primary' : 'bg-foreground/12')} />
      ))}
    </span>
  )
}

/** "Later" and "Not for this project"; a deferred step offers "Bring back" instead of Later. */
export function StepControls({
  id,
  deferred,
  onLater,
  onDismiss,
  onRestore,
}: {
  id: string
  deferred?: boolean
  onLater: () => void
  onDismiss: () => void
  onRestore: () => void
}) {
  const cls = 'text-muted-foreground'
  return (
    <span className="flex items-center gap-1">
      {deferred ? (
        <Button type="button" variant="ghost" size="xs" className={cls} onClick={onRestore} data-testid={`setup-step-restore-${id}`}>
          Bring back
        </Button>
      ) : (
        <Button type="button" variant="ghost" size="xs" className={cls} onClick={onLater} data-testid={`setup-step-later-${id}`}>
          Later
        </Button>
      )}
      <Button type="button" variant="ghost" size="xs" className={cls} onClick={onDismiss} data-testid={`setup-step-dismiss-${id}`}>
        Not for this project
      </Button>
    </span>
  )
}

/** The raised prompt box: a textarea (Enter submits, Shift+Enter breaks a line) and a row of actions under it. */
export function PromptBox({
  value,
  onChange,
  onSubmit,
  placeholder,
  testId,
  label,
  children,
}: {
  value: string
  onChange: (v: string) => void
  onSubmit: () => void
  placeholder: string
  testId?: string
  label: string
  children?: React.ReactNode
}) {
  return (
    <div className="rounded-ctl bg-raise p-3 shadow-raise-sm transition-shadow duration-(--d-fast) focus-within:shadow-focus">
      <textarea
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && !e.shiftKey && value.trim()) {
            e.preventDefault()
            onSubmit()
          }
        }}
        rows={2}
        aria-label={label}
        placeholder={placeholder}
        className="w-full resize-none bg-transparent text-[13px] outline-none placeholder:text-muted-foreground/80"
        data-testid={testId}
      />
      {children && <div className="mt-2 flex flex-wrap items-center gap-2">{children}</div>}
    </div>
  )
}

/** Engine templates (workflows, with what they need) and the home backend templates, in one raised list. */
export function TemplateChooser({
  engine,
  loading,
  busy,
  onEngine,
  onHome,
}: {
  engine: TemplateSummary[]
  loading?: boolean
  /** the key or slug being created */
  busy?: string | null
  onEngine: (t: TemplateSummary) => void
  onHome: (t: HomeTemplate) => void
}) {
  const row = 'flex items-center gap-1 px-3 py-2.5 shadow-[inset_0_-1px_0_var(--hairline)] last:shadow-none'
  return (
    <div className="max-h-[320px] overflow-y-auto rounded-ctl bg-raise shadow-raise-sm" data-testid="setup-template-list">
      {engine.map((t) => {
        const needs = [
          ...t.requires.secrets.map((s) => s.name),
          ...t.requires.variables.map((v) => v.name),
        ]
        return (
          <div key={t.slug} className={row}>
            <LayoutTemplate className="size-3.5 shrink-0 text-hue-flow" />
            <div className="min-w-0 flex-1">
              <div className="truncate text-[13px] font-medium">{t.name}</div>
              <div className="truncate text-[11.5px] text-muted-foreground">
                {t.summary}
                {needs.length > 0 && <span className="font-mono"> · needs {needs.join(', ')}</span>}
              </div>
            </div>
            <Button variant="outline" size="sm" disabled={!!busy} onClick={() => onEngine(t)} data-testid={`setup-template-${t.slug}`}>
              Preview
            </Button>
          </div>
        )
      })}
      {HOME_TEMPLATES.map((t) => (
        <div key={t.key} className={row}>
          <LayoutTemplate className="size-3.5 shrink-0 text-hue-backend" />
          <div className="min-w-0 flex-1">
            <div className="truncate text-[13px] font-medium">{t.title}</div>
            <div className="truncate text-[11.5px] text-muted-foreground">{t.blurb}</div>
          </div>
          <Button variant="outline" size="sm" disabled={!!busy} onClick={() => onHome(t)} data-testid={`setup-template-${t.key}`}>
            {busy === t.key ? 'Creating…' : 'Use'}
          </Button>
        </div>
      ))}
      {loading && engine.length === 0 && <div className="px-3 py-2.5 text-xs text-muted-foreground">Loading templates…</div>}
    </div>
  )
}
