/**
 * Small glass-editor primitives: a dismissable popover anchored to its trigger, and the outside-click hook.
 * Kept local so the editor chrome follows the mockup (`.pmenu`, `.chgpop`, `.verpop`) without a portal.
 */
import { useEffect, useRef, type ReactNode, type RefObject } from 'react'
import { cx } from '@/shared/glass/cx'

/** Calls `onClose` on a pointerdown outside `ref` (and outside `ignore`) or on Escape. */
export function useDismiss(open: boolean, ref: RefObject<HTMLElement | null>, onClose: () => void, ignore?: RefObject<HTMLElement | null>) {
  const cb = useRef(onClose)
  cb.current = onClose
  useEffect(() => {
    if (!open) return
    const down = (e: PointerEvent) => {
      const t = e.target as Node | null
      if (!t) return
      if (ref.current?.contains(t)) return
      if (ignore?.current?.contains(t)) return
      // menus and dialogs portalled out of the popover (selects, sheets) count as inside
      if ((t as Element).closest?.('[data-base-ui-portal], [role="listbox"], [role="dialog"], [data-sonner-toaster]')) return
      cb.current()
    }
    const key = (e: KeyboardEvent) => {
      if (e.key === 'Escape') cb.current()
    }
    // next tick, so the click that opened the popover does not close it
    const id = setTimeout(() => {
      document.addEventListener('pointerdown', down, true)
      document.addEventListener('keydown', key)
    }, 0)
    return () => {
      clearTimeout(id)
      document.removeEventListener('pointerdown', down, true)
      document.removeEventListener('keydown', key)
    }
  }, [open, ref, ignore])
}

export function Pop({
  open,
  onClose,
  anchor,
  className,
  children,
  testId,
  label,
}: {
  open: boolean
  onClose: () => void
  anchor?: RefObject<HTMLElement | null>
  className?: string
  children: ReactNode
  testId?: string
  label?: string
}) {
  const ref = useRef<HTMLDivElement>(null)
  useDismiss(open, ref, onClose, anchor)
  if (!open) return null
  return (
    <div ref={ref} role="dialog" aria-label={label} className={cx('ge-pop', className)} data-testid={testId}>
      {children}
    </div>
  )
}

export function MenuItem({
  onClick,
  children,
  hint,
  kbd,
  disabled,
  testId,
  danger,
}: {
  onClick: () => void
  children: ReactNode
  hint?: ReactNode
  kbd?: string
  disabled?: boolean
  testId?: string
  danger?: boolean
}) {
  return (
    <button type="button" className={cx('ge-mi', danger && 'danger')} onClick={onClick} disabled={disabled} data-testid={testId}>
      <span className="ge-mi-t">
        <b>{children}</b>
        {hint && <small>{hint}</small>}
      </span>
      {kbd && <kbd>{kbd}</kbd>}
    </button>
  )
}

/** Milliseconds as the strip and logs show them: 84ms, 1.2s, 2m 4s. */
export function fmtMs(ms: number | null | undefined): string {
  if (ms == null || !Number.isFinite(ms)) return '—'
  if (ms < 1000) return `${Math.max(0, Math.round(ms))}ms`
  if (ms < 60_000) return `${(ms / 1000).toFixed(ms < 10_000 ? 1 : 0)}s`
  const m = Math.floor(ms / 60_000)
  return `${m}m ${Math.round((ms % 60_000) / 1000)}s`
}

export function timeAgo(iso: string | number | null | undefined): string {
  if (iso == null) return ''
  const t = typeof iso === 'number' ? iso : Date.parse(iso)
  if (!Number.isFinite(t)) return ''
  const s = Math.max(0, Math.round((Date.now() - t) / 1000))
  if (s < 5) return 'just now'
  if (s < 60) return `${s}s ago`
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  return `${Math.floor(s / 86400)}d ago`
}
