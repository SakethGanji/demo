import * as React from "react"

import { cn } from "@/shared/lib/utils"

/**
 * Table primitives.
 *
 * A plain semantic `<table>` — there is no Base UI table part, and a real
 * `<table>` keeps row/column semantics for screen readers that a div grid loses.
 *
 * The container owns horizontal scroll and is `min-h-0` so it can shrink inside
 * a flex column: the app shell sets `overflow: hidden` on `#root`, so any
 * data-heavy view must contain its own scrolling or the rows are simply clipped.
 * Sticky headers work because the container is the scroll parent.
 */
function Table({ className, containerClassName, ...props }: React.ComponentProps<"table"> & {
  containerClassName?: string
}) {
  return (
    <div
      data-slot="table-container"
      className={cn("relative min-h-0 w-full flex-1 overflow-auto", containerClassName)}
    >
      <table
        data-slot="table"
        className={cn("w-full caption-bottom border-separate border-spacing-0 text-[13px]", className)}
        {...props}
      />
    </div>
  )
}

function TableHeader({ className, ...props }: React.ComponentProps<"thead">) {
  return <thead data-slot="table-header" className={cn(className)} {...props} />
}

function TableBody({ className, ...props }: React.ComponentProps<"tbody">) {
  return <tbody data-slot="table-body" className={cn(className)} {...props} />
}

function TableFooter({ className, ...props }: React.ComponentProps<"tfoot">) {
  return (
    <tfoot
      data-slot="table-footer"
      className={cn("bg-muted/40 font-medium", className)}
      {...props}
    />
  )
}

function TableRow({ className, ...props }: React.ComponentProps<"tr">) {
  return (
    <tr
      data-slot="table-row"
      className={cn(
        "group/row transition-colors hover:bg-muted/40 data-[state=selected]:bg-muted/60",
        className
      )}
      {...props}
    />
  )
}

/**
 * `sticky` keeps the header pinned while the container scrolls. The bottom
 * border is drawn on the cell (not the row) because `border-collapse: separate`
 * is required for sticky headers to keep their borders.
 */
function TableHead({ className, ...props }: React.ComponentProps<"th">) {
  return (
    <th
      data-slot="table-head"
      className={cn(
        "sticky top-0 z-10 whitespace-nowrap border-b border-border bg-background px-3 py-2 text-left align-middle text-[11px] font-medium tracking-wide text-muted-foreground uppercase",
        className
      )}
      {...props}
    />
  )
}

function TableCell({ className, ...props }: React.ComponentProps<"td">) {
  return (
    <td
      data-slot="table-cell"
      className={cn(
        "border-b border-border/50 px-3 py-1.5 align-middle whitespace-nowrap",
        className
      )}
      {...props}
    />
  )
}

function TableCaption({ className, ...props }: React.ComponentProps<"caption">) {
  return (
    <caption
      data-slot="table-caption"
      className={cn("mt-3 text-xs text-muted-foreground", className)}
      {...props}
    />
  )
}

/** Numeric cells read far better right-aligned and tabular. */
function TableNumericCell({ className, ...props }: React.ComponentProps<"td">) {
  return <TableCell className={cn("text-right tabular-nums", className)} {...props} />
}

export {
  Table,
  TableHeader,
  TableBody,
  TableFooter,
  TableHead,
  TableRow,
  TableCell,
  TableCaption,
  TableNumericCell,
}
