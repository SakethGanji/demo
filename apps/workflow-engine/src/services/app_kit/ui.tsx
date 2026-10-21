/** Studio UI kit — primitives (provided by Studio, refreshed every builder turn; do not edit).
 * Light, neutral internal-tool look: white cards on slate-50, blue accent, semantic status colours.
 * Button, Card, Tag, StatusBadge, Spinner, Tabs, Drawer, Toaster/toast, EmptyState, Stat, ConfidenceBar,
 * CodeBlock, JsonEditor. All class names are literal so both Tailwind builds (preview + publish) see them. */
import React, { useEffect, useRef, useState } from 'react'

type Children = { children?: React.ReactNode }

/** Join class names, skipping falsy values. */
export function cx(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(' ')
}

// ── Button ───────────────────────────────────────────────────────────────────

const BTN: Record<string, string> = {
  primary: 'bg-blue-600 text-white border-blue-600 hover:bg-blue-700',
  secondary: 'bg-white text-slate-800 border-slate-300 hover:bg-slate-50',
  ghost: 'bg-transparent text-slate-700 border-transparent hover:bg-slate-100',
  danger: 'bg-white text-red-700 border-red-200 hover:bg-red-50',
  success: 'bg-emerald-700 text-white border-emerald-700 hover:bg-emerald-800',
}

/** Button with variants and a loading state that disables it (prevents double submits). */
export function Button({ variant = 'secondary', size = 'md', loading = false, className, children, disabled, ...rest }:
  React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: 'primary' | 'secondary' | 'ghost' | 'danger' | 'success'; size?: 'sm' | 'md'; loading?: boolean }) {
  return (
    <button
      type="button"
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

/** Bordered white panel with an optional header row (title, subtitle, right-aligned actions). */
export function Card({ title, subtitle, actions, className, bodyClassName, children, ...rest }:
  Children & { title?: React.ReactNode; subtitle?: React.ReactNode; actions?: React.ReactNode; className?: string; bodyClassName?: string } & Omit<React.HTMLAttributes<HTMLElement>, 'title'>) {
  return (
    <section {...rest} className={cx('min-w-0 rounded-xl border border-slate-200 bg-white', className)}>
      {(title || actions) && (
        <header className="flex flex-wrap items-center gap-2 border-b border-slate-200 px-4 py-3">
          {title && <h2 className="m-0 text-sm font-semibold text-slate-900">{title}</h2>}
          {subtitle && <span className="text-xs text-slate-500">{subtitle}</span>}
          {actions && <div className="ml-auto flex flex-wrap items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className={bodyClassName}>{children}</div>
    </section>
  )
}

// ── Tag / StatusBadge ────────────────────────────────────────────────────────

const TAG: Record<string, string> = {
  gray: 'bg-slate-100 text-slate-600',
  blue: 'bg-blue-50 text-blue-700',
  green: 'bg-emerald-50 text-emerald-700',
  red: 'bg-red-50 text-red-700',
  amber: 'bg-amber-50 text-amber-700',
  purple: 'bg-violet-50 text-violet-700',
  cyan: 'bg-cyan-50 text-cyan-700',
  rose: 'bg-rose-50 text-rose-700',
}

export type Tone = 'gray' | 'blue' | 'green' | 'red' | 'amber' | 'purple' | 'cyan' | 'rose'

/** Small inline label chip (type / risk / count). */
export function Tag({ tone = 'gray', className, children }: Children & { tone?: Tone; className?: string }) {
  return <span className={cx('inline-block whitespace-nowrap rounded-md px-1.5 py-0.5 text-[11px] font-semibold', TAG[tone] ?? TAG.gray, className)}>{children}</span>
}

const STATUS: Record<string, { tone: string; label: string; spin?: boolean }> = {
  passed: { tone: 'text-emerald-700', label: 'Passed' },
  pass: { tone: 'text-emerald-700', label: 'Passed' },
  success: { tone: 'text-emerald-700', label: 'Success' },
  succeeded: { tone: 'text-emerald-700', label: 'Succeeded' },
  completed: { tone: 'text-emerald-700', label: 'Completed' },
  approved: { tone: 'text-emerald-700', label: 'Approved' },
  ok: { tone: 'text-emerald-700', label: 'OK' },
  failed: { tone: 'text-red-700', label: 'Failed' },
  fail: { tone: 'text-red-700', label: 'Failed' },
  error: { tone: 'text-red-700', label: 'Error' },
  rejected: { tone: 'text-red-700', label: 'Rejected' },
  running: { tone: 'text-blue-700', label: 'Running', spin: true },
  in_progress: { tone: 'text-blue-700', label: 'In progress', spin: true },
  queued: { tone: 'text-slate-500', label: 'Queued' },
  pending: { tone: 'text-slate-500', label: 'Pending' },
  draft: { tone: 'text-slate-500', label: 'Draft' },
  cancelled: { tone: 'text-slate-500', label: 'Cancelled' },
  canceled: { tone: 'text-slate-500', label: 'Cancelled' },
  skipped: { tone: 'text-slate-500', label: 'Skipped' },
  blocked: { tone: 'text-amber-700', label: 'Blocked' },
  flaky: { tone: 'text-amber-700', label: 'Flaky' },
  warning: { tone: 'text-amber-700', label: 'Warning' },
  needs_review: { tone: 'text-amber-700', label: 'Needs review' },
  needs_approval: { tone: 'text-amber-700', label: 'Needs approval' },
}

/** Dot + label for a status string (passed/failed/running/queued/blocked/…); running shows a spinner. */
export function StatusBadge({ status, label, detail, className }: { status: string | null | undefined; label?: React.ReactNode; detail?: React.ReactNode; className?: string }) {
  const key = String(status ?? 'pending').toLowerCase().replace(/[\s-]+/g, '_')
  const s = STATUS[key] ?? { tone: 'text-slate-600', label: String(status ?? '—') }
  return (
    <span className={cx('inline-flex items-center gap-1.5 whitespace-nowrap text-xs font-medium', s.tone, className)} data-status={key}>
      {s.spin ? <Spinner size={11} /> : <span className="inline-block h-2 w-2 rounded-full bg-current" />}
      {label ?? s.label}
      {detail != null && <span className="font-normal opacity-80">· {detail}</span>}
    </span>
  )
}

/** Circular spinner in the current text colour. */
export function Spinner({ size = 14, className }: { size?: number; className?: string }) {
  return (
    <span
      aria-hidden
      className={cx('inline-block shrink-0 animate-spin rounded-full border-2 border-current border-t-transparent', className)}
      style={{ width: size, height: size }}
    />
  )
}

// ── Tabs ─────────────────────────────────────────────────────────────────────

export interface TabItem { id: string; label: React.ReactNode; count?: number | string | null }

/** Underlined tab bar (controlled). Scrolls horizontally on narrow screens. */
export function Tabs({ tabs, value, onChange, className }: { tabs: TabItem[]; value: string; onChange: (id: string) => void; className?: string }) {
  return (
    <div role="tablist" className={cx('flex gap-1 overflow-x-auto border-b border-slate-200', className)}>
      {tabs.map((t) => (
        <button
          key={t.id}
          type="button"
          role="tab"
          aria-selected={t.id === value}
          onClick={() => onChange(t.id)}
          className={cx(
            '-mb-px inline-flex items-center gap-1.5 whitespace-nowrap border-b-2 px-3 py-2 text-sm font-medium',
            t.id === value ? 'border-blue-600 text-slate-900' : 'border-transparent text-slate-500 hover:text-slate-800',
          )}
        >
          {t.label}
          {t.count != null && <span className="rounded-full bg-slate-100 px-1.5 text-[11px] font-semibold text-slate-600">{t.count}</span>}
        </button>
      ))}
    </div>
  )
}

// ── Drawer ───────────────────────────────────────────────────────────────────

const DRAWER_W: Record<string, string> = { md: 'sm:max-w-md', lg: 'sm:max-w-2xl', xl: 'sm:max-w-4xl' }
const DRAWER_TONE: Record<string, string> = { none: '', red: 'border-t-4 border-t-red-600', amber: 'border-t-4 border-t-amber-500', blue: 'border-t-4 border-t-blue-600', green: 'border-t-4 border-t-emerald-600' }

/** Detail panel: overlay side drawer (default) or `variant="docked"` inline under a list so the live pane stays visible.
 * `overlay`: right-side drawer over a dimmed backdrop (Escape or backdrop click closes, full width on phones). `variant="docked"`: an inline panel that sits in the page flow under a list (put it right after the
 * table inside the same column), no backdrop, so the live pane beside it stays visible and usable. */
export function Drawer({ open, onClose, title, subtitle, actions, footer, width = 'lg', tone = 'none', variant = 'overlay', children }:
  Children & { open: boolean; onClose: () => void; title?: React.ReactNode; subtitle?: React.ReactNode; actions?: React.ReactNode; footer?: React.ReactNode; width?: 'md' | 'lg' | 'xl'; tone?: 'none' | 'red' | 'amber' | 'blue' | 'green'; variant?: 'overlay' | 'docked' }) {
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])
  if (!open) return null
  const head = (
    <header className="flex flex-wrap items-start gap-2 border-b border-slate-200 px-4 py-3">
      <div className="min-w-0 flex-1">
        {title && <h3 className="m-0 truncate text-sm font-semibold text-slate-900">{title}</h3>}
        {subtitle && <div className="mt-0.5 text-xs text-slate-500">{subtitle}</div>}
      </div>
      {actions}
      <button type="button" onClick={onClose} aria-label="Close" className="rounded-md px-2 py-1 text-slate-500 hover:bg-slate-100">✕</button>
    </header>
  )
  if (variant === 'docked') {
    return (
      <section role="region" aria-label={typeof title === 'string' ? title : 'Details'} data-testid="drawer" data-variant="docked"
        className={cx('min-w-0 overflow-hidden rounded-xl border border-slate-200 bg-white', DRAWER_TONE[tone] ?? '')}>
        {head}
        <div className="min-w-0 p-4">{children}</div>
        {footer && <footer className="flex flex-wrap gap-2 border-t border-slate-200 px-4 py-3">{footer}</footer>}
      </section>
    )
  }
  return (
    <div className="fixed inset-0 z-50 flex justify-end" data-testid="drawer">
      <div className="absolute inset-0 bg-slate-900/30" onClick={onClose} aria-hidden />
      <aside role="dialog" aria-modal="true" className={cx('relative flex h-full w-full flex-col bg-white shadow-2xl', DRAWER_W[width] ?? DRAWER_W.lg, DRAWER_TONE[tone] ?? '')}>
        {head}
        <div className="min-h-0 flex-1 overflow-y-auto p-4">{children}</div>
        {footer && <footer className="flex flex-wrap gap-2 border-t border-slate-200 px-4 py-3">{footer}</footer>}
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

/** Mount once near the root of App; renders the toast stack bottom-right. */
export function Toaster() {
  const [items, setItems] = useState<ToastItem[]>(toastItems)
  useEffect(() => {
    toastSubs.add(setItems)
    return () => { toastSubs.delete(setItems) }
  }, [])
  return (
    <div className="pointer-events-none fixed bottom-4 right-4 z-[60] flex w-[min(360px,calc(100%-2rem))] flex-col gap-2" aria-live="polite">
      {items.map((t) => (
        <div
          key={t.id}
          role="status"
          data-testid="toast"
          className={cx(
            'pointer-events-auto rounded-lg border px-3 py-2 text-sm shadow-lg',
            t.kind === 'success' && 'border-emerald-200 bg-emerald-50 text-emerald-800',
            t.kind === 'error' && 'border-red-200 bg-red-50 text-red-800',
            t.kind === 'info' && 'border-slate-200 bg-white text-slate-800',
          )}
        >
          {t.message}
        </div>
      ))}
    </div>
  )
}

// ── Misc display ─────────────────────────────────────────────────────────────

/** Centered empty / error placeholder. */
export function EmptyState({ title, hint, action, className }: { title: React.ReactNode; hint?: React.ReactNode; action?: React.ReactNode; className?: string }) {
  return (
    <div className={cx('flex flex-col items-center gap-2 px-4 py-10 text-center', className)}>
      <div className="text-sm font-medium text-slate-700">{title}</div>
      {hint && <div className="max-w-sm text-xs text-slate-500">{hint}</div>}
      {action}
    </div>
  )
}

/** KPI tile: label, big value, optional delta / hint and a slot (e.g. a Sparkline). */
export function Stat({ label, value, hint, tone = 'gray', children }: Children & { label: React.ReactNode; value: React.ReactNode; hint?: React.ReactNode; tone?: 'gray' | 'green' | 'red' | 'amber' | 'blue' }) {
  const t: Record<string, string> = { gray: 'text-slate-900', green: 'text-emerald-700', red: 'text-red-700', amber: 'text-amber-700', blue: 'text-blue-700' }
  return (
    <div className="min-w-0 rounded-xl border border-slate-200 bg-white p-4">
      <div className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</div>
      <div className={cx('mt-1 text-2xl font-semibold', t[tone] ?? t.gray)}>{value}</div>
      {hint && <div className="mt-0.5 text-xs text-slate-500">{hint}</div>}
      {children && <div className="mt-2">{children}</div>}
    </div>
  )
}

/** Horizontal confidence meter for a 0..1 (or 0..100) value. */
export function ConfidenceBar({ value, tone = 'amber' }: { value: number; tone?: 'amber' | 'green' | 'red' | 'blue' }) {
  const pct = Math.max(0, Math.min(100, value <= 1 ? value * 100 : value))
  const bar: Record<string, string> = { amber: 'bg-amber-500', green: 'bg-emerald-600', red: 'bg-red-600', blue: 'bg-blue-600' }
  return (
    <span className="inline-flex items-center gap-2 text-xs text-slate-600">
      <span className="h-1.5 w-32 overflow-hidden rounded-full bg-slate-200"><span className={cx('block h-full', bar[tone])} style={{ width: `${pct}%` }} /></span>
      {Math.round(pct)}%
    </span>
  )
}

/** Monospace read-only block with horizontal scroll (code, JSON, logs). */
export function CodeBlock({ code, maxHeight = 320, className }: { code: string; maxHeight?: number; className?: string }) {
  return (
    <pre className={cx('m-0 overflow-auto rounded-lg border border-slate-200 bg-slate-50 p-3 font-mono text-xs leading-relaxed text-slate-800', className)} style={{ maxHeight }}>
      {code}
    </pre>
  )
}

/** JSON text editor: validates as you type, "Format" button, calls onChange(parsed) only for valid JSON. */
export function JsonEditor({ value, onChange, rows = 12, label, className }: { value: unknown; onChange: (v: any) => void; rows?: number; label?: React.ReactNode; className?: string }) {
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
    <div className={cx('flex flex-col gap-1', className)}>
      <div className="flex items-center gap-2">
        {label && <span className="text-xs font-semibold text-slate-600">{label}</span>}
        <span className={cx('text-xs', error ? 'text-red-600' : 'text-emerald-700')}>{error ? 'Invalid JSON' : 'Valid JSON'}</span>
        <button type="button" className="ml-auto text-xs font-medium text-blue-700 hover:underline disabled:opacity-40" disabled={!!error}
          onClick={() => { try { setText(JSON.stringify(JSON.parse(text), null, 2)) } catch { /* invalid */ } }}>Format</button>
      </div>
      <textarea
        spellCheck={false}
        rows={rows}
        value={text}
        onChange={(e) => update(e.target.value)}
        className={cx('w-full resize-y rounded-lg border bg-white p-2 font-mono text-xs leading-relaxed text-slate-800 outline-none focus:ring-2',
          error ? 'border-red-300 focus:ring-red-100' : 'border-slate-300 focus:ring-blue-100')}
      />
      {error && <div className="font-mono text-[11px] text-red-600">{error}</div>}
    </div>
  )
}
