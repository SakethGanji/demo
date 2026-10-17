import type { ReactNode } from 'react'
import { X } from 'lucide-react'
import { cn } from '@/shared/lib/utils'

// ── ChatPanel ────────────────────────────────────────────────────────────────
// Shared shell for all AI chat panels. Provides consistent layout with an
// optional top toolbar (X close + actions slot), message area, and footer.

export function ChatPanel({
  children,
  className,
  onClose,
  actions,
}: {
  children: ReactNode
  className?: string
  /** When provided, renders a toolbar with X close button on the left */
  onClose?: () => void
  /** Extra action buttons rendered on the right side of the toolbar */
  actions?: ReactNode
}) {
  return (
    <div className={cn('h-full flex flex-col overflow-hidden', className)}>
      {onClose && (
        <div className="flex items-center px-2 py-1.5 shrink-0 shadow-[inset_0_-1px_0_var(--hairline)]">
          {actions}
          <div className="flex-1" />
          <button
            onClick={onClose}
            className="rounded-chip p-1 text-muted-foreground transition-colors hover:bg-(--hover-fill) hover:text-foreground"
            title="Close"
          >
            <X size={14} />
          </button>
        </div>
      )}
      {children}
    </div>
  )
}

// ── Footer ───────────────────────────────────────────────────────────────────
// Wraps the ChatInput with consistent padding and border.

export function ChatPanelFooter({
  children,
  className,
}: {
  children: ReactNode
  className?: string
}) {
  return (
    <div className={cn('p-3 shrink-0', className)}>
      {children}
    </div>
  )
}
