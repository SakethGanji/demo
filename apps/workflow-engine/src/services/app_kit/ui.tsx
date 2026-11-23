/** Studio UI kit — primitives (provided by Studio, refreshed every builder turn; do not edit).
 * Light, neutral internal-tool look: white cards on slate-50, blue accent, semantic status colours.
 * Button, Card, Tag, StatusBadge, Spinner, Tabs, Drawer, Toaster/toast, EmptyState, Stat, ConfidenceBar,
 * CodeBlock, JsonEditor. All class names are literal so both Tailwind builds (preview + publish) see them. */
import React, { useEffect, useRef, useState } from 'react'
import './theme.css'
import { useRestoredValue } from './hooks'

type Children = { children?: React.ReactNode }
/** HTML attributes a kit component passes to its root element (`id`, `style`, `data-*`, `aria-*`, events), minus the names the
 * kit gives its own meaning. */
type Attrs<E = HTMLDivElement> = Omit<React.HTMLAttributes<E>, 'title' | 'children' | 'color' | 'content' | 'onChange' | 'onSelect' | 'defaultValue' | 'placeholder' | 'prefix'>

// ── Class merging ────────────────────────────────────────────────────────────
// A compact tailwind-merge: when two classes set the same thing (rounded-xl then rounded-none, p-4 then px-2's sides, bg-card
// then bg-muted), the later one wins and the earlier is dropped, so a className passed to a kit component overrides the kit's
// default. Variants (md:, hover:, dark:) and `!` keep their own groups. Unknown classes are always kept.

const SIDES: Record<string, string[]> = { '': ['x', 'y', 't', 'r', 'b', 'l', 's', 'e'], x: ['l', 'r', 's', 'e'], y: ['t', 'b'] }
const CORNERS: Record<string, string[]> = { '': ['t', 'r', 'b', 'l', 's', 'e', 'tl', 'tr', 'br', 'bl', 'ss', 'se', 'es', 'ee'],
  t: ['tl', 'tr'], r: ['tr', 'br'], b: ['br', 'bl'], l: ['tl', 'bl'], s: ['ss', 'es'], e: ['se', 'ee'] }
const SIZE_RE = /^(xs|sm|base|lg|xl|[2-9]xl)$/
const LEN_RE = /^\[(?:\d|\.|calc|clamp|min|max|var\(--(?:text|font-size))/
const DISPLAY = new Set(['block', 'inline-block', 'inline', 'flex', 'inline-flex', 'grid', 'inline-grid', 'hidden', 'contents', 'table', 'flow-root', 'list-item'])
const POSITION = new Set(['static', 'fixed', 'absolute', 'relative', 'sticky'])
const WEIGHT = /^font-(thin|extralight|light|normal|medium|semibold|bold|extrabold|black)$/
const BG_OTHER = /^(auto|cover|contain|center|top|bottom|left|right|repeat|no-repeat|repeat-x|repeat-y|fixed|local|scroll|none|clip-.*|origin-.*|linear-.*|gradient-.*|radial.*|conic.*|blend-.*)$/

/** [group, groups it overrides] for one class without variants, or null (kept as is). */
function classGroup(c: string): [string, string[]] | null {
  const k = c.replace(/^-/, '')
  let m: RegExpExecArray | null
  if ((m = /^([pm])([xytrblse]?)-/.exec(k))) return [m[1] + m[2], (SIDES[m[2]] ?? []).map((s) => m![1] + s)]
  if ((m = /^rounded(?:-(tl|tr|br|bl|ss|se|es|ee|t|r|b|l|s|e))?(?:-|$)/.exec(k))) { const s = m[1] ?? ''; return ['rounded-' + s, (CORNERS[s] ?? []).map((x) => 'rounded-' + x)] }
  if ((m = /^border(?:-([xytrblse]))?(?:-(\d+|\[[\d.]+px\]))?$/.exec(k))) { const s = m[1] ?? ''; return ['bw-' + s, (SIDES[s] ?? []).map((x) => 'bw-' + x)] }
  if (/^border-(solid|dashed|dotted|double|hidden|none)$/.test(k)) return ['border-style', []]
  if ((m = /^border-(?:([xytrblse])-)?(.+)$/.exec(k)) && !/^(collapse|separate|spacing)/.test(m[2])) { const s = m[1] ?? ''; return ['bc-' + s, (SIDES[s] ?? []).map((x) => 'bc-' + x)] }
  if (/^shadow(-(2xs|xs|sm|md|lg|xl|2xl|inner|none))?$/.test(k)) return ['shadow', []]
  if ((m = /^bg-(.+)$/.exec(k))) return BG_OTHER.test(m[1]) ? null : ['bg', []]
  if ((m = /^text-(.+)$/.exec(k))) {
    const v = m[1].split('/')[0]
    if (SIZE_RE.test(v) || LEN_RE.test(v)) return ['text-size', []]
    if (/^(left|center|right|justify|start|end)$/.test(v)) return ['text-align', []]
    if (/^(wrap|nowrap|balance|pretty|ellipsis|clip)$/.test(v)) return ['text-wrap', []]
    return ['text-color', []]
  }
  if (WEIGHT.test(k)) return ['font-weight', []]
  if (/^font-(sans|serif|mono)$/.test(k)) return ['font-family', []]
  if ((m = /^gap(?:-([xy]))?-/.exec(k))) return m[1] ? ['gap-' + m[1], []] : ['gap', ['gap-x', 'gap-y']]
  if ((m = /^(min-w|max-w|min-h|max-h|size|w|h)-/.exec(k))) return [m[1], m[1] === 'size' ? ['w', 'h'] : []]
  if (DISPLAY.has(k)) return ['display', []]
  if (POSITION.has(k)) return ['position', []]
  if (/^flex-(row|col|row-reverse|col-reverse)$/.test(k)) return ['flex-dir', []]
  if (/^flex-(wrap|nowrap|wrap-reverse)$/.test(k)) return ['flex-wrap', []]
  if (/^flex-(\d+|auto|initial|none|\[.+\])$/.test(k)) return ['flex', []]
  if ((m = /^(items|justify|content|self|place-items|place-content)-/.exec(k)) && !/^justify-(items|self)-/.test(k)) return [m[1], []]
  if ((m = /^(grid-cols|grid-rows|col-span|row-span|overflow-x|overflow-y|overflow|opacity|z|leading|tracking|inset|top|right|bottom|left)-/.exec(k)))
    return [m[1], m[1] === 'overflow' ? ['overflow-x', 'overflow-y'] : []]
  return null
}

/** Join class names, skipping falsy values. Later Tailwind classes win over earlier ones of the same kind (see Class merging). */
export function cx(...parts: Array<string | false | null | undefined>): string {
  const list = parts.filter(Boolean).join(' ').split(/\s+/).filter(Boolean)
  const taken = new Set<string>()
  const out: string[] = []
  for (let i = list.length - 1; i >= 0; i--) {             // walk from the end: the last class of a group is the one kept
    const c = list[i]
    const at = c.lastIndexOf(':', c.lastIndexOf(']') > c.lastIndexOf(':') ? c.indexOf('[') : c.length)
    const variant = at >= 0 ? c.slice(0, at + 1) : ''
    let base = at >= 0 ? c.slice(at + 1) : c
    const bang = base.startsWith('!') || base.endsWith('!')
    base = base.replace(/^!|!$/g, '')
    const g = classGroup(base)
    if (!g) { if (!out.includes(c)) out.push(c); continue }
    const key = variant + (bang ? '!' : '') + g[0]
    if (taken.has(key)) continue
    taken.add(key)
    for (const sub of g[1]) taken.add(variant + (bang ? '!' : '') + sub)
    out.push(c)
  }
  return out.reverse().join(' ')
}

/** Same as `cx`: join and merge class names so later Tailwind classes win. */
export const cn = cx

// ── Button ───────────────────────────────────────────────────────────────────

const BTN: Record<string, string> = {
  primary: 'bg-primary text-primary-foreground border-primary hover:bg-primary/90',
  secondary: 'bg-card text-foreground border-input hover:bg-subtle',
  ghost: 'bg-transparent text-soft-foreground border-transparent hover:bg-muted',
  danger: 'bg-card text-danger border-danger-border hover:bg-danger-soft',
  success: 'bg-success text-success-foreground border-success hover:bg-success/90',
}

/** Button with variants and a loading state that disables it (prevents double submits).
 * @category form
 * @example <Button variant="primary" onClick={save}>Save</Button> */
export function Button({ variant = 'secondary', size = 'md', loading = false, className, children, disabled, ...rest }:
  React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: 'primary' | 'secondary' | 'ghost' | 'danger' | 'success'; size?: 'sm' | 'md'; loading?: boolean }) {
  return (
    <button
      type="button"
      data-vcomp="Button"
      {...rest}
      disabled={disabled || loading}
      className={cx(
        'inline-flex items-center justify-center gap-1.5 rounded-lg border font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50',
        size === 'sm' ? 'px-2.5 py-1 text-xs' : 'px-3 py-1.5 text-sm',
        BTN[variant] ?? BTN.secondary,
        className,
      )}
    >
      {loading && <Spinner size={12} />}
      {children}
    </button>
  )
}

// ── Card ─────────────────────────────────────────────────────────────────────

/** Bordered white panel with an optional header row (title, subtitle, right-aligned actions). Title and subtitle
 * follow the card's text colour, so `className="bg-primary text-primary-foreground"` restyles the whole card.
 * @category layout
 * @example <Card title="Summary" subtitle="This month" actions={<Button size="sm">Export</Button>}>Content</Card> */
export function Card({ title, subtitle, actions, className, headerClassName, bodyClassName, children, ...rest }:
  Children & { /** @control text @group Content */ title?: React.ReactNode; /** @control text @group Content */ subtitle?: React.ReactNode; actions?: React.ReactNode; className?: string; headerClassName?: string; bodyClassName?: string } & Omit<React.HTMLAttributes<HTMLElement>, 'title'>) {
  return (
    <section data-vcomp="Card" {...rest} className={cx('min-w-0 rounded-xl border border-border bg-card text-card-foreground', className)}>
      {(title || actions) && (
        <header data-vpart="header" className={cx('flex flex-wrap items-center gap-2 border-b border-border px-4 py-3', headerClassName)}>
          {title && <h2 className="m-0 text-sm font-semibold">{title}</h2>}
          {subtitle && <span className="text-xs opacity-65">{subtitle}</span>}
          {actions && <div className="ml-auto flex flex-wrap items-center gap-2">{actions}</div>}
        </header>
      )}
      <div data-vpart="body" className={bodyClassName}>{children}</div>
    </section>
  )
}

// ── Tag / StatusBadge ────────────────────────────────────────────────────────

const TAG: Record<string, string> = {
  gray: 'bg-muted text-soft-foreground',
  blue: 'bg-info-soft text-info',
  green: 'bg-success-soft text-success',
  red: 'bg-danger-soft text-danger',
  amber: 'bg-warning-soft text-warning',
  purple: 'bg-violet-50 text-violet-700 dark:bg-violet-500/15 dark:text-violet-300',
  cyan: 'bg-cyan-50 text-cyan-700 dark:bg-cyan-500/15 dark:text-cyan-300',
  rose: 'bg-rose-50 text-rose-700 dark:bg-rose-500/15 dark:text-rose-300',
}

export type Tone = 'gray' | 'blue' | 'green' | 'red' | 'amber' | 'purple' | 'cyan' | 'rose'

/** Small inline label chip (type / risk / count).
 * @category display
 * @example <Tag tone="blue">Travel</Tag> */
export function Tag({ tone = 'gray', className, children, ...rest }: Children & { tone?: Tone; className?: string } & Omit<React.HTMLAttributes<HTMLSpanElement>, 'children' | 'color'>) {
  return <span data-vcomp="Tag" {...rest} className={cx('inline-block whitespace-nowrap rounded-md px-1.5 py-0.5 text-[11px] font-semibold', TAG[tone] ?? TAG.gray, className)}>{children}</span>
}

const STATUS: Record<string, { tone: string; label: string; spin?: boolean }> = {
  passed: { tone: 'text-success', label: 'Passed' },
  pass: { tone: 'text-success', label: 'Passed' },
  success: { tone: 'text-success', label: 'Success' },
  succeeded: { tone: 'text-success', label: 'Succeeded' },
  completed: { tone: 'text-success', label: 'Completed' },
  approved: { tone: 'text-success', label: 'Approved' },
  ok: { tone: 'text-success', label: 'OK' },
  failed: { tone: 'text-danger', label: 'Failed' },
  fail: { tone: 'text-danger', label: 'Failed' },
  error: { tone: 'text-danger', label: 'Error' },
  rejected: { tone: 'text-danger', label: 'Rejected' },
  running: { tone: 'text-info', label: 'Running', spin: true },
  in_progress: { tone: 'text-info', label: 'In progress', spin: true },
  queued: { tone: 'text-muted-foreground', label: 'Queued' },
  pending: { tone: 'text-muted-foreground', label: 'Pending' },
  draft: { tone: 'text-muted-foreground', label: 'Draft' },
  cancelled: { tone: 'text-muted-foreground', label: 'Cancelled' },
  canceled: { tone: 'text-muted-foreground', label: 'Cancelled' },
  skipped: { tone: 'text-muted-foreground', label: 'Skipped' },
  blocked: { tone: 'text-warning', label: 'Blocked' },
  flaky: { tone: 'text-warning', label: 'Flaky' },
  warning: { tone: 'text-warning', label: 'Warning' },
  needs_review: { tone: 'text-warning', label: 'Needs review' },
  needs_approval: { tone: 'text-warning', label: 'Needs approval' },
}

/** Dot + label for a status string (passed/failed/running/queued/blocked/…); running shows a spinner.
 * @category display
 * @example <StatusBadge status="running" /> */
export function StatusBadge({ status, label, detail, className, ...rest }: { status: string | null | undefined; /** @control text */ label?: React.ReactNode; /** @control text */ detail?: React.ReactNode; className?: string } & Attrs<HTMLSpanElement>) {
  const key = String(status ?? 'pending').toLowerCase().replace(/[\s-]+/g, '_')
  const s = STATUS[key] ?? { tone: 'text-soft-foreground', label: String(status ?? '—') }
  return (
    <span data-vcomp="StatusBadge" {...rest} className={cx('inline-flex items-center gap-1.5 whitespace-nowrap text-xs font-medium', s.tone, className)} data-status={key}>
      {s.spin ? <Spinner size={11} /> : <span className="inline-block h-2 w-2 rounded-full bg-current" />}
      {label ?? s.label}
      {detail != null && <span className="font-normal opacity-80">· {detail}</span>}
    </span>
  )
}

/** Circular spinner in the current text colour.
 * @category display
 * @example <Spinner size={16} /> */
export function Spinner({ size = 14, className, style, ...rest }: { /** Diameter in pixels.
   * @control number @min 8 @max 64 @group Style @default 14 */ size?: number; className?: string } & Attrs<HTMLSpanElement>) {
  return (
    <span
      aria-hidden
      data-vcomp="Spinner"
      {...rest}
      className={cx('inline-block shrink-0 animate-spin rounded-full border-2 border-current border-t-transparent', className)}
      style={{ width: size, height: size, ...style }}
    />
  )
}

// ── Tabs ─────────────────────────────────────────────────────────────────────

export interface TabItem { id: string; /** @control text */ label: React.ReactNode; count?: number | string | null; hidden?: boolean }

/** Underlined tab bar (controlled). Scrolls horizontally on narrow screens.
 * @category navigation
 * @example <Tabs tabs={[{ id: 'open', label: 'Open' }, { id: 'done', label: 'Done' }]} value={tab} onChange={setTab} /> */
export function Tabs({ tabs, value, onChange, className, stateKey, ...rest }: Attrs & { tabs: TabItem[]; value: string; onChange: (id: string) => void; className?: string
  /** Id under which the Studio preview remembers the active tab across edits (default: the tab ids).
   * @control hidden @group Behavior */
  stateKey?: string }) {
  useRestoredValue(stateKey ?? `Tabs:${tabs.map((t) => t.id).join(',')}`, value, onChange, (v) => tabs.some((t) => t.id === v && !t.hidden))
  return (
    <div role="tablist" data-vcomp="Tabs" {...rest} className={cx('flex gap-1 overflow-x-auto border-b border-border', className)}>
      {tabs.map((t, i) => t.hidden ? null : (
        <button
          key={t.id}
          type="button"
          role="tab"
          data-vpart={`tabs.${i}`}
          aria-selected={t.id === value}
          onClick={() => onChange(t.id)}
          className={cx(
            '-mb-px inline-flex items-center gap-1.5 whitespace-nowrap border-b-2 px-3 py-2 text-sm font-medium',
            t.id === value ? 'border-primary text-foreground' : 'border-transparent text-muted-foreground hover:text-foreground',
          )}
        >
          {t.label}
          {t.count != null && <span className="rounded-full bg-muted px-1.5 text-[11px] font-semibold text-soft-foreground">{t.count}</span>}
        </button>
      ))}
    </div>
  )
}

// ── Select ───────────────────────────────────────────────────────────────────

/** Dropdown (native select) with an optional label above it; `options` are strings or `{ value, label }`.
 * @category form
 * @example <Select label="Status" value={status} onChange={setStatus} options={['open', 'closed']} /> */
export function Select({ label, value, onChange, options, placeholder, disabled, className, ...rest }:
  { label?: string; value: string; onChange: (v: string) => void; options: (string | { value: string; label: string })[]; placeholder?: string; disabled?: boolean; className?: string } & Attrs<HTMLLabelElement>) {
  return (
    <label data-vcomp="Select" {...rest} className={cx('inline-flex min-w-0 flex-col gap-1', className)}>
      {label && <span data-vpart="label" className="text-xs font-semibold text-soft-foreground">{label}</span>}
      <select value={value} disabled={disabled} onChange={(e) => onChange(e.target.value)} aria-label={label ? undefined : placeholder}
        className="rounded-lg border border-input bg-card px-2.5 py-1.5 text-sm text-foreground outline-none focus:ring-2 focus:ring-ring disabled:cursor-not-allowed disabled:opacity-50">
        {placeholder != null && <option value="">{placeholder}</option>}
        {options.map((o, i) => {
          const v = typeof o === 'string' ? o : o.value
          return <option key={v} value={v} data-vpart={`options.${i}`}>{typeof o === 'string' ? o : o.label}</option>
        })}
      </select>
    </label>
  )
}

// ── Drawer ───────────────────────────────────────────────────────────────────

const DRAWER_W: Record<string, string> = { md: 'sm:max-w-md', lg: 'sm:max-w-2xl', xl: 'sm:max-w-4xl' }
const DRAWER_TONE: Record<string, string> = { none: '', red: 'border-t-4 border-t-danger', amber: 'border-t-4 border-t-warning', blue: 'border-t-4 border-t-primary', green: 'border-t-4 border-t-success' }

/** Detail panel: overlay side drawer (default) or `variant="docked"` inline under a list so the live pane stays visible.
 * `overlay`: right-side drawer over a dimmed backdrop (Escape or backdrop click closes, full width on phones). `variant="docked"`: an inline panel that sits in the page flow under a list (put it right after the
 * table inside the same column), no backdrop, so the live pane beside it stays visible and usable.
 * @category overlay
 * @example <Drawer open={!!selected} onClose={() => setSelected(null)} title="Request" width="lg">Details</Drawer> */
export function Drawer({ open, onClose, title, subtitle, actions, footer, width = 'lg', tone = 'none', variant = 'overlay', className, headerClassName, bodyClassName, footerClassName, children, ...rest }:
  Children & { open: boolean; onClose: () => void; /** @control text */ title?: React.ReactNode; /** @control text */ subtitle?: React.ReactNode; actions?: React.ReactNode; footer?: React.ReactNode; width?: 'md' | 'lg' | 'xl'; tone?: 'none' | 'red' | 'amber' | 'blue' | 'green'; variant?: 'overlay' | 'docked'
    /** Classes for the panel (the docked section, or the sliding panel of the overlay).
     * @control hidden @group Style */
    className?: string; headerClassName?: string; bodyClassName?: string; footerClassName?: string } & Attrs<HTMLElement>) {
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])
  if (!open) return null
  const head = (
    <header data-vpart="header" className={cx('flex flex-wrap items-start gap-2 border-b border-border px-4 py-3', headerClassName)}>
      <div className="min-w-0 flex-1">
        {title && <h3 className="m-0 truncate text-sm font-semibold">{title}</h3>}
        {subtitle && <div className="mt-0.5 text-xs opacity-65">{subtitle}</div>}
      </div>
      {actions}
      <button type="button" onClick={onClose} aria-label="Close" className="rounded-md px-2 py-1 opacity-65 hover:bg-muted">✕</button>
    </header>
  )
  const foot = footer && <footer data-vpart="footer" className={cx('flex flex-wrap gap-2 border-t border-border px-4 py-3', footerClassName)}>{footer}</footer>
  if (variant === 'docked') {
    return (
      <section role="region" aria-label={typeof title === 'string' ? title : 'Details'} data-testid="drawer" data-variant="docked" data-vcomp="Drawer" {...rest}
        className={cx('min-w-0 overflow-hidden rounded-xl border border-border bg-card text-card-foreground', DRAWER_TONE[tone] ?? '', className)}>
        {head}
        <div data-vpart="body" className={cx('min-w-0 p-4', bodyClassName)}>{children}</div>
        {foot}
      </section>
    )
  }
  // the dimmed wrapper is chrome; className, data-vcomp and pass-through attributes go on the sliding panel
  return (
    <div className="fixed inset-0 z-50 flex justify-end" data-testid="drawer">
      <div className="absolute inset-0 bg-overlay" onClick={onClose} aria-hidden />
      <aside role="dialog" aria-modal="true" data-vcomp="Drawer" {...rest}
        className={cx('relative flex h-full w-full flex-col bg-card text-card-foreground shadow-2xl', DRAWER_W[width] ?? DRAWER_W.lg, DRAWER_TONE[tone] ?? '', className)}>
        {head}
        <div data-vpart="body" className={cx('min-h-0 flex-1 overflow-y-auto p-4', bodyClassName)}>{children}</div>
        {foot}
      </aside>
    </div>
  )
}

// ── Toasts ───────────────────────────────────────────────────────────────────

type ToastKind = 'success' | 'error' | 'info'
interface ToastItem { id: number; message: React.ReactNode; kind: ToastKind }
let toastItems: ToastItem[] = []
let toastSeq = 0
const toastSubs = new Set<(items: ToastItem[]) => void>()
function emitToasts() { toastSubs.forEach((fn) => fn(toastItems)) }

/** Show a toast: `toast('Saved')`, `toast.success('Approved')`, `toast.error('Run failed')`. Needs one <Toaster/> mounted. */
export function toast(message: React.ReactNode, kind: ToastKind = 'info', ms = 3500): void {
  const id = ++toastSeq
  toastItems = [...toastItems, { id, message, kind }]
  emitToasts()
  setTimeout(() => { toastItems = toastItems.filter((t) => t.id !== id); emitToasts() }, ms)
}
toast.success = (m: React.ReactNode, ms?: number) => toast(m, 'success', ms)
toast.error = (m: React.ReactNode, ms?: number) => toast(m, 'error', ms ?? 6000)
toast.info = (m: React.ReactNode, ms?: number) => toast(m, 'info', ms)

/** Mount once near the root of App; renders the toast stack bottom-right.
 * @category overlay
 * @example <Toaster /> */
export function Toaster({ className, ...rest }: { className?: string } & Attrs = {}) {
  const [items, setItems] = useState<ToastItem[]>(toastItems)
  useEffect(() => {
    toastSubs.add(setItems)
    return () => { toastSubs.delete(setItems) }
  }, [])
  return (
    <div aria-live="polite" data-vcomp="Toaster" {...rest} className={cx('pointer-events-none fixed bottom-4 right-4 z-[60] flex w-[min(360px,calc(100%-2rem))] flex-col gap-2', className)}>
      {items.map((t) => (
        <div
          key={t.id}
          role="status"
          data-testid="toast"
          className={cx(
            'pointer-events-auto rounded-lg border px-3 py-2 text-sm shadow-lg',
            t.kind === 'success' && 'border-success-border bg-success-soft text-success',
            t.kind === 'error' && 'border-danger-border bg-danger-soft text-danger',
            t.kind === 'info' && 'border-border bg-card text-foreground',
          )}
        >
          {t.message}
        </div>
      ))}
    </div>
  )
}

// ── Misc display ─────────────────────────────────────────────────────────────

/** Centered empty / error placeholder.
 * @category display
 * @example <EmptyState title="No requests yet" hint="New requests show up here." /> */
export function EmptyState({ title, hint, action, className, ...rest }: { /** @control text */ title: React.ReactNode; /** @control text */ hint?: React.ReactNode; action?: React.ReactNode; className?: string } & Attrs) {
  return (
    <div data-vcomp="EmptyState" {...rest} className={cx('flex flex-col items-center gap-2 px-4 py-10 text-center text-soft-foreground', className)}>
      <div data-vpart="title" className="text-sm font-medium">{title}</div>
      {hint && <div className="max-w-sm text-xs opacity-80">{hint}</div>}
      {action}
    </div>
  )
}

/** KPI tile: label, big value, optional delta / hint and a slot (e.g. a Sparkline).
 * @category display
 * @example <Stat label="Open requests" value={12} hint="+3 this week" tone="blue" /> */
export function Stat({ label, value, hint, tone = 'gray', children, className, ...rest }: Children & { /** @control text */ label: React.ReactNode; value: React.ReactNode; /** @control text */ hint?: React.ReactNode; tone?: 'gray' | 'green' | 'red' | 'amber' | 'blue'; className?: string } & Omit<React.HTMLAttributes<HTMLDivElement>, 'title'>) {
  const t: Record<string, string> = { gray: '', green: 'text-success', red: 'text-danger', amber: 'text-warning', blue: 'text-info' }
  return (
    <div data-vcomp="Stat" {...rest} className={cx('min-w-0 rounded-xl border border-border bg-card p-4 text-card-foreground', className)}>
      <div className="text-xs font-medium uppercase tracking-wide opacity-65">{label}</div>
      <div className={cx('mt-1 text-2xl font-semibold', t[tone] ?? t.gray)}>{value}</div>
      {hint && <div className="mt-0.5 text-xs opacity-65">{hint}</div>}
      {children && <div className="mt-2">{children}</div>}
    </div>
  )
}

/** Horizontal confidence meter for a 0..1 (or 0..100) value.
 * @category display
 * @example <ConfidenceBar value={0.82} tone="green" /> */
export function ConfidenceBar({ value, tone = 'amber', className, ...rest }: { value: number; tone?: 'amber' | 'green' | 'red' | 'blue'; className?: string } & Attrs<HTMLSpanElement>) {
  const pct = Math.max(0, Math.min(100, value <= 1 ? value * 100 : value))
  const bar: Record<string, string> = { amber: 'bg-warning', green: 'bg-success', red: 'bg-danger', blue: 'bg-primary' }
  return (
    <span data-vcomp="ConfidenceBar" {...rest} className={cx('inline-flex items-center gap-2 text-xs text-soft-foreground', className)}>
      <span className="h-1.5 w-32 overflow-hidden rounded-full bg-border"><span className={cx('block h-full', bar[tone])} style={{ width: `${pct}%` }} /></span>
      {Math.round(pct)}%
    </span>
  )
}

/** Monospace read-only block with horizontal scroll (code, JSON, logs).
 * @category display
 * @example <CodeBlock code={JSON.stringify(record, null, 2)} /> */
export function CodeBlock({ code, maxHeight = 320, className, style, ...rest }: { code: string; /** @control number @min 80 @max 1200 @step 20 @group Layout @default 320 */ maxHeight?: number; className?: string } & Attrs<HTMLPreElement>) {
  return (
    <pre data-vcomp="CodeBlock" {...rest} className={cx('m-0 overflow-auto rounded-lg border border-border bg-subtle p-3 font-mono text-xs leading-relaxed text-foreground', className)} style={{ maxHeight, ...style }}>
      {code}
    </pre>
  )
}

/** JSON text editor: validates as you type, "Format" button, calls onChange(parsed) only for valid JSON.
 * @category form
 * @example <JsonEditor label="Config" value={config} onChange={setConfig} /> */
export function JsonEditor({ value, onChange, rows = 12, label, className, ...rest }: { value: unknown; onChange: (v: any) => void; /** @min 2 @max 40 */ rows?: number; /** @control text */ label?: React.ReactNode; className?: string } & Attrs) {
  const [text, setText] = useState(() => JSON.stringify(value ?? null, null, 2))
  const [error, setError] = useState<string | null>(null)
  const lastSent = useRef<string>(JSON.stringify(value ?? null))
  useEffect(() => {
    const incoming = JSON.stringify(value ?? null)
    if (incoming !== lastSent.current) {
      lastSent.current = incoming
      setText(JSON.stringify(value ?? null, null, 2))
      setError(null)
    }
  }, [value])
  const update = (t: string) => {
    setText(t)
    try {
      const v = JSON.parse(t)
      setError(null)
      lastSent.current = JSON.stringify(v)
      onChange(v)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Invalid JSON')
    }
  }
  return (
    <div data-vcomp="JsonEditor" {...rest} className={cx('flex flex-col gap-1', className)}>
      <div data-vpart="toolbar" className="flex items-center gap-2">
        {label && <span className="text-xs font-semibold text-soft-foreground">{label}</span>}
        <span className={cx('text-xs', error ? 'text-danger' : 'text-success')}>{error ? 'Invalid JSON' : 'Valid JSON'}</span>
        <button type="button" className="ml-auto text-xs font-medium text-primary hover:underline disabled:opacity-40" disabled={!!error}
          onClick={() => { try { setText(JSON.stringify(JSON.parse(text), null, 2)) } catch { /* invalid */ } }}>Format</button>
      </div>
      <textarea
        spellCheck={false}
        rows={rows}
        value={text}
        onChange={(e) => update(e.target.value)}
        className={cx('w-full resize-y rounded-lg border bg-card p-2 font-mono text-xs leading-relaxed text-foreground outline-none focus:ring-2',
          error ? 'border-danger-border focus:ring-danger-soft' : 'border-input focus:ring-ring')}
      />
      {error && <div className="font-mono text-[11px] text-danger">{error}</div>}
    </div>
  )
}
