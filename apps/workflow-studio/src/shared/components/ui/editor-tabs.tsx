import * as React from 'react'
import { cn } from '@/shared/lib/utils'

/* ── EditorTabList ──
   Container for tabs inside editor chrome panel headers.
   Renders as h-9 strip with bottom border — matches VS Code / Figma tab bar.
*/
function EditorTabList({ className, children, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn('h-9 border-b border-border/50 shrink-0 overflow-x-auto', className)}
      style={{ scrollbarWidth: 'none' }}
      {...props}
    >
      <div className="flex items-center h-full gap-0.5 px-2">
        {children}
      </div>
    </div>
  )
}

/* ── EditorTab ──
   Individual tab with accent-bar indicator on active state.
*/
interface EditorTabProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  active?: boolean
  icon?: React.ComponentType<{ size?: number }>
}

function EditorTab({ active, icon: Icon, children, className, ...props }: EditorTabProps) {
  return (
    <button
      className={cn(
        'relative inline-flex items-center gap-1.5 px-2.5 py-1 text-[11px] font-medium rounded-none transition-colors shrink-0 whitespace-nowrap',
        active
          ? 'text-foreground after:absolute after:bottom-0 after:left-0 after:right-0 after:h-0.5 after:bg-primary'
          : 'text-muted-foreground hover:text-foreground hover:bg-accent/50',
        className
      )}
      {...props}
    >
      {Icon && <Icon size={11} />}
      {children}
    </button>
  )
}

export { EditorTabList, EditorTab }
