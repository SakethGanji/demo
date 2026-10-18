/**
 * ScreenSpec v1 renderer (DESIGN-STAGE5-UIS §6.3, §11.2). Safe by construction: no dangerouslySetInnerHTML, no
 * eval, bindings resolve only through the closed roots in `lib/binding.ts`, links must be https, and an
 * unknown component renders a visible placeholder instead of crashing. Everything is escaped by React.
 * Each component root carries `data-testid="screen-<type>"` and `data-bind`.
 */
import type { ReactNode } from 'react'
import { AlertTriangle, ExternalLink } from 'lucide-react'
import { Badge } from '@/shared/components/ui/badge'
import { Callout } from '@/shared/components/ui/surface'
import { Button } from '@/shared/components/ui/button'
import { cn } from '@/shared/lib/utils'
import {
  asComponents, currencyFormat, display, interpolate, isKnownType, resolveBinding, safeHref, walk, splitPath,
  type ScreenData,
} from '../lib/binding'
import { formatWhen, taskStatusLabel, taskTone } from '../lib/format'
import { humanize } from '../lib/formFields'
import type { ScreenComponent, ScreenSpec } from '../lib/types'

export type ScreenSlot = (component: ScreenComponent) => ReactNode
export interface ScreenSlots {
  input_form?: ScreenSlot
  form?: ScreenSlot
  actions?: ScreenSlot
}

export interface ScreenRendererProps {
  spec: ScreenSpec | null | undefined
  data: ScreenData
  slots?: ScreenSlots
  className?: string
}

const str = (v: unknown): string | undefined => (typeof v === 'string' && v !== '' ? v : undefined)
const MAX_ROWS = 100

function Shell({ type, bind, className, children }: { type: string; bind?: unknown; className?: string; children: ReactNode }) {
  return (
    <div data-testid={`screen-${type}`} data-bind={typeof bind === 'string' ? bind : undefined} className={className}>
      {children}
    </div>
  )
}

function Labelled({ label, children }: { label?: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5">
      {label && <div className="text-[12px] font-medium text-muted-foreground">{label}</div>}
      {children}
    </div>
  )
}

function valueOf(c: ScreenComponent, data: ScreenData): unknown {
  return c.bind !== undefined ? resolveBinding(c.bind, data) : c.value
}

function Blank() {
  return <span className="text-faint">—</span>
}

function Unknown({ type, why }: { type: unknown; why?: string }) {
  return (
    <div data-testid="screen-unknown" data-type={typeof type === 'string' ? type : 'invalid'} className="flex items-center gap-2 rounded-ctl bg-warning-soft px-3 py-2 text-[12.5px] text-warning-text">
      <AlertTriangle className="size-3.5 shrink-0" />
      <span>{why ?? `Unsupported component "${display(type, 40)}"`}</span>
    </div>
  )
}

function ObjectRows({ value, testId }: { value: unknown; testId?: string }) {
  if (value === null || value === undefined) return <Blank />
  if (typeof value !== 'object' || Array.isArray(value)) return <span className="whitespace-pre-wrap break-words text-[13px]">{display(value)}</span>
  const rows = Object.entries(value as Record<string, unknown>).slice(0, MAX_ROWS)
  return (
    <dl className="flex flex-col gap-1.5" data-testid={testId}>
      {rows.map(([k, v]) => (
        <div key={k} className="flex flex-col gap-0.5">
          <dt className="text-[12px] font-medium text-muted-foreground">{humanize(k)}</dt>
          <dd className="whitespace-pre-wrap break-words text-[13px]">{display(v)}</dd>
        </div>
      ))}
    </dl>
  )
}

function ComponentView({ c, data, slots }: { c: ScreenComponent; data: ScreenData; slots?: ScreenSlots }) {
  if (!c || typeof c !== 'object' || Array.isArray(c)) return <Unknown type={undefined} why="Invalid component" />
  if (!isKnownType(c.type)) return <Unknown type={c.type} />
  const label = str(c.label)
  switch (c.type) {
    case 'heading':
      return (
        <Shell type="heading" bind={c.bind}>
          <h2 className="text-[16px] font-semibold tracking-[-0.01em] text-foreground">{c.bind ? display(resolveBinding(c.bind, data)) : display(c.text)}</h2>
        </Shell>
      )
    case 'text': {
      const v = valueOf(c, data)
      const t = str(c.template)
      return (
        <Shell type="text" bind={c.bind}>
          <p className="whitespace-pre-wrap break-words text-[13px] text-foreground">{t ? interpolate(t, data, { value: v }) : display(v) || display(c.text)}</p>
        </Shell>
      )
    }
    case 'field': {
      const v = valueOf(c, data)
      return (
        <Shell type="field" bind={c.bind}>
          <Labelled label={label}>
            <div className="whitespace-pre-wrap break-words text-[13px] text-foreground">{display(v) || <Blank />}</div>
          </Labelled>
        </Shell>
      )
    }
    case 'amount': {
      const v = valueOf(c, data)
      const n = typeof v === 'number' ? v : typeof v === 'string' && v.trim() !== '' ? Number(v) : NaN
      const size = c.size === 'xl' ? 'text-title font-[650] tracking-[-0.03em]' : c.size === 'lg' ? 'text-[20px] font-semibold' : 'text-[14px] font-medium'
      return (
        <Shell type="amount" bind={c.bind}>
          <Labelled label={label}>
            <div className={cn('tabular-nums text-foreground', size)}>{Number.isFinite(n) ? currencyFormat(n, str(c.currency) ?? 'USD') : <Blank />}</div>
          </Labelled>
        </Shell>
      )
    }
    case 'status': {
      const s = display(resolveBinding(c.bind, data)) || display(c.value)
      const due = c.due ? display(resolveBinding(c.due, data)) : ''
      const tone = taskTone(s)
      return (
        <Shell type="status" bind={c.bind} className="flex items-center gap-2">
          {s ? <Badge variant={tone} data-status={s}>{taskStatusLabel(s)}</Badge> : <Blank />}
          {due && <span className="text-[12px] text-muted-foreground">Due {formatWhen(due)}</span>}
        </Shell>
      )
    }
    case 'callout': {
      const v = valueOf(c, data)
      const tone = c.tone === 'ai' || c.tone === 'warn' || c.tone === 'error' || c.tone === 'ok' ? c.tone : 'info'
      return (
        <Shell type="callout" bind={c.bind}>
          <Callout tone={tone} className="items-start">
            {label && <div className="mb-0.5 text-[12px] font-medium">{label}</div>}
            <div className="whitespace-pre-wrap break-words">{display(v) || <Blank />}</div>
          </Callout>
        </Shell>
      )
    }
    case 'list': {
      const v = valueOf(c, data)
      const items = Array.isArray(v) ? v.slice(0, MAX_ROWS) : []
      const tpl = str(c.item)
      return (
        <Shell type="list" bind={c.bind}>
          <Labelled label={label}>
            {items.length === 0 ? (
              <Blank />
            ) : (
              <ul className="flex flex-col gap-1">
                {items.map((it, i) => (
                  <li key={i} className="well px-2.5 py-1.5 text-[13px]">
                    {tpl ? interpolate(tpl, data, { scope: it, value: it }) : display(it)}
                  </li>
                ))}
              </ul>
            )}
          </Labelled>
        </Shell>
      )
    }
    case 'table': {
      const v = valueOf(c, data)
      const rows = (Array.isArray(v) ? v : []).slice(0, MAX_ROWS)
      const cols = Array.isArray(c.columns) && c.columns.length
        ? (c.columns as unknown[]).filter((x): x is string => typeof x === 'string')
        : rows.length && rows[0] && typeof rows[0] === 'object' ? Object.keys(rows[0] as object).slice(0, 8) : []
      return (
        <Shell type="table" bind={c.bind}>
          <Labelled label={label}>
            {rows.length === 0 || cols.length === 0 ? (
              <Blank />
            ) : (
              <div className="well overflow-x-auto">
                <table className="w-full text-[12.5px]">
                  <thead>
                    <tr className="border-b border-hairline">
                      {cols.map((k) => <th key={k} className="px-2.5 py-1.5 text-left font-medium text-muted-foreground">{humanize(k)}</th>)}
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((r, i) => (
                      <tr key={i} className="border-b border-hairline last:border-0">
                        {cols.map((k) => <td key={k} className="px-2.5 py-1.5">{display(walk(r, splitPath(k)))}</td>)}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Labelled>
        </Shell>
      )
    }
    case 'link': {
      const v = valueOf(c, data)
      const href = safeHref(v)
      return (
        <Shell type="link" bind={c.bind}>
          {href ? (
            <a href={href} target="_blank" rel="noopener noreferrer nofollow" className="inline-flex items-center gap-1 text-[13px] text-primary hover:underline">
              {label ?? href} <ExternalLink className="size-3" />
            </a>
          ) : (
            <span className="text-[13px] text-muted-foreground">{label ?? 'Link'}: {v ? 'not a safe link' : '—'}</span>
          )}
        </Shell>
      )
    }
    case 'divider':
      return <Shell type="divider"><hr className="my-1 h-px bg-hairline" /></Shell>
    case 'result': {
      const v = c.bind ? resolveBinding(c.bind, data) : data.result
      return (
        <Shell type="result" bind={c.bind}>
          <Labelled label={label}><ObjectRows value={v} testId="screen-result-body" /></Labelled>
        </Shell>
      )
    }
    case 'input_form':
      return <Shell type="input_form">{slots?.input_form ? slots.input_form(c) : <Placeholder text="The request form appears here" />}</Shell>
    case 'form':
      return <Shell type="form" bind={c.bind}>{slots?.form ? slots.form(c) : <Placeholder text="The approver's form fields appear here" />}</Shell>
    case 'actions':
      return (
        <Shell type="actions" bind={c.bind}>
          {slots?.actions ? slots.actions(c) : (
            <div className="flex items-center gap-2">
              <Button variant="outline" disabled>{str(c.reject_label) ?? 'Reject'}</Button>
              <Button disabled>{str(c.approve_label) ?? 'Approve'}</Button>
            </div>
          )}
        </Shell>
      )
  }
}

function Placeholder({ text }: { text: string }) {
  return <div className="well px-3 py-2 text-[12.5px] text-muted-foreground">{text}</div>
}

export function ScreenRenderer({ spec, data, slots, className }: ScreenRendererProps) {
  const comps = asComponents(spec)
  if (!comps) {
    return (
      <div data-testid="screen-root" data-state="invalid" className={className}>
        <Unknown type={undefined} why="This screen has no components to show" />
      </div>
    )
  }
  const page = spec?.layout === 'page'
  return (
    <div data-testid="screen-root" className={cn('flex min-w-0 flex-col gap-3', page ? '' : 'tile p-4', className)}>
      {comps.map((c, i) => (
        <ComponentView key={i} c={c} data={data} slots={slots} />
      ))}
    </div>
  )
}
