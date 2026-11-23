/** Studio UI kit — display and flow components (provided by Studio, refreshed every builder turn; do not edit).
 * Avatar, Badge, Progress, Accordion, Timeline, Stepper and Wizard (a multi-step form with Back / Next / Finish).
 * Colours come only from theme tokens. */
import React from 'react'
import { cx, Button } from './ui'
import { usePersistedUiState } from './hooks'

type DivProps = Omit<React.HTMLAttributes<HTMLDivElement>, 'title' | 'children'>
type ListProps = Omit<React.HTMLAttributes<HTMLElement>, 'title' | 'children'>
type Tone = 'neutral' | 'primary' | 'success' | 'warning' | 'danger' | 'info'

const SOFT: Record<Tone, string> = {
  neutral: 'bg-muted text-soft-foreground', primary: 'bg-primary-soft text-primary', success: 'bg-success-soft text-success',
  warning: 'bg-warning-soft text-warning', danger: 'bg-danger-soft text-danger', info: 'bg-info-soft text-info',
}
const SOLID: Record<Tone, string> = {
  neutral: 'bg-soft-foreground', primary: 'bg-primary', success: 'bg-success', warning: 'bg-warning', danger: 'bg-danger', info: 'bg-info',
}
const AVATAR_SIZE = { sm: 'h-6 w-6 text-[10px]', md: 'h-8 w-8 text-xs', lg: 'h-11 w-11 text-sm' }
const CHART_BG = ['bg-chart-1', 'bg-chart-2', 'bg-chart-3', 'bg-chart-4', 'bg-chart-5', 'bg-chart-6', 'bg-chart-7', 'bg-chart-8']

function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean)
  return (parts.length > 1 ? parts[0][0] + parts[parts.length - 1][0] : (parts[0] ?? '?').slice(0, 2)).toUpperCase()
}

/** A round picture of a person, or their initials on a colour picked from their name.
 * @category display
 * @example <Avatar name="Ada Lovelace" size="md" /> */
export function Avatar({ name, src, size = 'md', className, ...rest }: Omit<React.HTMLAttributes<HTMLSpanElement>, 'children'> & {
  /** Full name: the initials and the alt text.
   * @control text @group Content */
  name: string
  /** Image URL (initials when missing).
   * @control text @group Content */
  src?: string
  /** Diameter.
   * @control select @options sm|md|lg @group Layout @default md */
  size?: keyof typeof AVATAR_SIZE
}) {
  const hue = CHART_BG[[...name].reduce((h, ch) => (h * 31 + ch.charCodeAt(0)) >>> 0, 7) % CHART_BG.length]
  return (
    <span data-vcomp="Avatar" title={name} {...rest}
      className={cx('inline-flex shrink-0 items-center justify-center overflow-hidden rounded-full font-semibold text-primary-foreground', AVATAR_SIZE[size], !src && hue, className)}>
      {src ? <img src={src} alt={name} className="h-full w-full object-cover" /> : initials(name)}
    </span>
  )
}

/** A small rounded label: a count, a category, a state.
 * @category display
 * @example <Badge tone="success">Paid</Badge> */
export function Badge({ tone = 'neutral', variant = 'soft', className, children, ...rest }: Omit<React.HTMLAttributes<HTMLSpanElement>, 'children'> & {
  /** Colour from the theme.
   * @control select @options neutral|primary|success|warning|danger|info @group Style @default neutral */
  tone?: Tone
  /** Soft tint or solid fill.
   * @control select @options soft|solid|outline @group Style @default soft */
  variant?: 'soft' | 'solid' | 'outline'
  /** The label.
   * @control text @group Content */
  children?: React.ReactNode
}) {
  return (
    <span data-vcomp="Badge" {...rest}
      className={cx('inline-flex items-center gap-1 whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium',
        variant === 'soft' && SOFT[tone], variant === 'solid' && cx(SOLID[tone], 'text-primary-foreground'),
        variant === 'outline' && 'border border-border text-soft-foreground', className)}>
      {children}
    </span>
  )
}

/** A horizontal progress bar with an optional label and percentage.
 * @category display
 * @example <Progress value={64} label="Reviewed" showValue /> */
export function Progress({ value, max = 100, label, showValue = false, tone = 'primary', size = 'md', className, ...rest }: DivProps & {
  /** Current amount.
   * @control number @min 0 @group Data */
  value: number
  /** Amount that fills the bar.
   * @control number @min 1 @group Data @default 100 */
  max?: number
  /** Text above the bar.
   * @control text @group Content */
  label?: React.ReactNode
  /** Show the percentage at the right.
   * @control boolean @group Content @default false */
  showValue?: boolean
  /** Bar colour.
   * @control select @options neutral|primary|success|warning|danger|info @group Style @default primary */
  tone?: Tone
  /** Bar thickness.
   * @control select @options sm|md|lg @group Layout @default md */
  size?: 'sm' | 'md' | 'lg'
}) {
  const pct = Math.max(0, Math.min(100, max > 0 ? (value / max) * 100 : 0))
  return (
    <div data-vcomp="Progress" {...rest} className={cx('flex min-w-0 flex-col gap-1', className)}>
      {(label || showValue) && (
        <div className="flex items-center justify-between text-xs text-muted-foreground">
          <span>{label}</span>{showValue && <span className="tabular-nums">{Math.round(pct)}%</span>}
        </div>
      )}
      <div role="progressbar" aria-valuemin={0} aria-valuemax={max} aria-valuenow={value}
        className={cx('w-full overflow-hidden rounded-full bg-muted', size === 'sm' ? 'h-1' : size === 'lg' ? 'h-3' : 'h-2')}>
        <div className={cx('h-full rounded-full transition-all', SOLID[tone])} style={{ width: `${pct}%` }} />
      </div>
    </div>
  )
}

export interface AccordionItem {
  id: string
  /** @control text */
  title: React.ReactNode
  content: React.ReactNode
}

/** Collapsible sections: click a title to show its content (one at a time, or several with `multiple`).
 * @category layout
 * @example <Accordion items={[{ id: 'a', title: 'Shipping', content: 'Ships in 2 days' }, { id: 'b', title: 'Returns', content: '30 days' }]} /> */
export function Accordion({ items, multiple = false, defaultOpen = [], stateKey, className, ...rest }: DivProps & {
  /** The sections.
   * @control hidden @group Data */
  items: AccordionItem[]
  /** Let several sections be open at once.
   * @control boolean @group Behavior @default false */
  multiple?: boolean
  /** Ids open on first render.
   * @control hidden @group Behavior */
  defaultOpen?: string[]
  /** Id under which the Studio preview remembers the open sections across edits (default: the item ids).
   * @control hidden @group Behavior */
  stateKey?: string
}) {
  const [open, setOpen] = usePersistedUiState<string[]>(stateKey ?? `Accordion:${items.map((i) => i.id).join(',')}`, defaultOpen,
    (v) => Array.isArray(v) && v.every((x) => typeof x === 'string'))
  const toggle = (id: string) => setOpen((o) => (o.includes(id) ? o.filter((x) => x !== id) : multiple ? [...o, id] : [id]))
  return (
    <div data-vcomp="Accordion" {...rest} className={cx('divide-y divide-border overflow-hidden rounded-xl border border-border bg-card', className)}>
      {items.map((it, i) => {
        const on = open.includes(it.id)
        return (
          <section key={it.id} data-vpart={`items.${i}`}>
            <button type="button" aria-expanded={on} onClick={() => toggle(it.id)}
              className="flex w-full items-center justify-between gap-2 px-4 py-3 text-left text-sm font-medium text-foreground hover:bg-subtle">
              <span className="min-w-0 flex-1">{it.title}</span>
              <span aria-hidden className={cx('text-muted-foreground transition-transform', on && 'rotate-90')}>›</span>
            </button>
            {on && <div className="px-4 pb-4 text-sm text-soft-foreground">{it.content}</div>}
          </section>
        )
      })}
    </div>
  )
}

export interface TimelineEvent {
  id: string
  /** @control text */
  title: React.ReactNode
  /** a date or time label shown at the right */
  time?: React.ReactNode
  /** @control text */
  description?: React.ReactNode
  tone?: Tone
}

/** A vertical list of events with dots and a connecting line (history, audit trail, activity feed).
 * @category display
 * @example <Timeline events={[{ id: '1', title: 'Submitted', time: 'Mon 09:12' }, { id: '2', title: 'Approved', time: 'Tue 14:03', tone: 'success' }]} /> */
export function Timeline({ events, emptyText = 'Nothing yet', className, ...rest }: ListProps & {
  /** The events, oldest or newest first as given.
   * @control hidden @group Data */
  events: TimelineEvent[]
  /** Shown when there are no events.
   * @control text @group Content @default Nothing yet */
  emptyText?: React.ReactNode
}) {
  if (!events.length) return <div data-vcomp="Timeline" {...rest} className={cx('py-4 text-sm text-muted-foreground', className)}>{emptyText}</div>
  return (
    <ol data-vcomp="Timeline" {...rest} className={cx('m-0 list-none p-0', className)}>
      {events.map((e, i) => (
        <li key={e.id} data-vpart={`events.${i}`} className="relative flex gap-3 pb-4 last:pb-0">
          {i < events.length - 1 && <span aria-hidden className="absolute left-[5px] top-4 h-full w-px bg-border" />}
          <span aria-hidden className={cx('relative mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full', SOLID[e.tone ?? 'neutral'])} />
          <div className="min-w-0 flex-1">
            <div className="flex items-baseline justify-between gap-2">
              <span className="text-sm font-medium text-foreground">{e.title}</span>
              {e.time != null && <span className="shrink-0 text-xs text-muted-foreground">{e.time}</span>}
            </div>
            {e.description != null && <div className="mt-0.5 text-sm text-soft-foreground">{e.description}</div>}
          </div>
        </li>
      ))}
    </ol>
  )
}

export interface StepItem {
  id: string
  /** @control text */
  label: React.ReactNode
  /** @control text */
  description?: React.ReactNode
}

/** Numbered progress through a fixed sequence of steps; steps before `current` show as done.
 * @category navigation
 * @example <Stepper steps={[{ id: 'details', label: 'Details' }, { id: 'review', label: 'Review' }, { id: 'done', label: 'Done' }]} current={1} /> */
export function Stepper({ steps, current, onStepClick, className, ...rest }: ListProps & {
  /** The steps in order.
   * @control hidden @group Data */
  steps: StepItem[]
  /** Index of the active step (0-based).
   * @control number @min 0 @group Data */
  current: number
  /** Makes finished steps clickable (go back to a step).
   * @control hidden @group Behavior */
  onStepClick?: (index: number) => void
}) {
  return (
    <ol data-vcomp="Stepper" {...rest} className={cx('m-0 flex list-none items-start gap-2 p-0', className)}>
      {steps.map((s, i) => {
        const state = i < current ? 'done' : i === current ? 'active' : 'todo'
        const clickable = state === 'done' && !!onStepClick
        return (
          <li key={s.id} data-vpart={`steps.${i}`} className="flex min-w-0 flex-1 items-start gap-2">
            <button type="button" disabled={!clickable} onClick={() => onStepClick?.(i)} aria-current={state === 'active' ? 'step' : undefined}
              className={cx('flex h-7 w-7 shrink-0 items-center justify-center rounded-full border text-xs font-semibold',
                state === 'done' && 'border-primary bg-primary text-primary-foreground', state === 'active' && 'border-primary bg-primary-soft text-primary',
                state === 'todo' && 'border-border bg-card text-muted-foreground', clickable ? 'cursor-pointer' : 'cursor-default')}>
              {state === 'done' ? '✓' : i + 1}
            </button>
            <div className="min-w-0 flex-1 pt-1">
              <div className={cx('truncate text-sm font-medium', state === 'todo' ? 'text-muted-foreground' : 'text-foreground')}>{s.label}</div>
              {s.description != null && <div className="truncate text-xs text-muted-foreground">{s.description}</div>}
            </div>
            {i < steps.length - 1 && <span aria-hidden className={cx('mt-3.5 hidden h-px flex-1 sm:block', i < current ? 'bg-primary' : 'bg-border')} />}
          </li>
        )
      })}
    </ol>
  )
}

export interface WizardStep extends StepItem {
  /** the step's form */
  content: React.ReactNode
  /** false blocks Next (e.g. a required field is empty) */
  canAdvance?: boolean
}

/** A multi-step form: a Stepper on top, the current step's content, and Back / Next / Finish buttons.
 * @category form
 * @example <Wizard steps={[{ id: 'a', label: 'Details', content: <DetailsForm /> }, { id: 'b', label: 'Review', content: <Review /> }]} onFinish={submit} /> */
export function Wizard({ steps, onFinish, finishLabel = 'Finish', finishing = false, stateKey, className, ...rest }: DivProps & {
  /** The steps with their content.
   * @control hidden @group Data */
  steps: WizardStep[]
  /** Called by the last step's Finish button.
   * @control hidden @group Behavior */
  onFinish: () => void
  /** Label of the last button.
   * @control text @group Content @default Finish */
  finishLabel?: React.ReactNode
  /** Show a spinner on Finish while it saves.
   * @control boolean @group Behavior @default false */
  finishing?: boolean
  /** Id under which the Studio preview remembers the current step across edits (default: the step ids).
   * @control hidden @group Behavior */
  stateKey?: string
}) {
  const [at, setAt] = usePersistedUiState<number>(stateKey ?? `Wizard:${steps.map((s) => s.id).join(',')}`, 0,
    (v) => typeof v === 'number' && v >= 0 && v < steps.length)
  const i = Math.min(at, Math.max(0, steps.length - 1))
  const step = steps[i]
  const last = i === steps.length - 1
  return (
    <div data-vcomp="Wizard" {...rest} className={cx('flex min-w-0 flex-col gap-4', className)}>
      <Stepper steps={steps} current={i} onStepClick={setAt} />
      <div className="min-w-0">{step?.content}</div>
      <div className="flex justify-between gap-2 border-t border-border pt-3">
        <Button disabled={i === 0} onClick={() => setAt(i - 1)}>Back</Button>
        {last
          ? <Button variant="primary" loading={finishing} disabled={step?.canAdvance === false} onClick={onFinish}>{finishLabel}</Button>
          : <Button variant="primary" disabled={step?.canAdvance === false} onClick={() => setAt(i + 1)}>Next</Button>}
      </div>
    </div>
  )
}
