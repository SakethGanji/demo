import * as React from 'react'
import { cn } from '@/shared/lib/utils'

/* ── ToolbarGroup ──
   Muted pill that groups related toolbar buttons (undo/redo, zoom, etc.)
   Matches the VS Code / Figma button-group pattern used in editor chrome.
*/
function ToolbarGroup({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn('flex bg-muted/50 rounded-md px-0.5 py-0.5', className)}
      {...props}
    />
  )
}

/* ── ToolbarSeparator ──
   Thin vertical divider between toolbar sections.
*/
function ToolbarSeparator({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn('w-px h-4 bg-border self-center', className)}
      {...props}
    />
  )
}

export { ToolbarGroup, ToolbarSeparator }
