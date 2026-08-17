import * as React from "react"

import { cn } from "@/shared/lib/utils"

/**
 * Card.
 *
 * INSTRUMENT rule 8: a card earns a SURFACE, not a border — and only for
 * heterogeneous, independently actionable, ranked units. A row of homogeneous
 * figures is not a set of cards; it is a strip, and it should be grouped by
 * space alone.
 *
 * Two things this component deliberately makes hard:
 *
 *  - There is no `bordered` variant. Elevation contrast replaces the border
 *    (rule 3). If a card needs separating from something, it needs more space
 *    or a different surface step, not a rule.
 *  - `nested` renders a left-ruled indent rather than a second box. Nesting a
 *    container inside a container was, verbatim, "the loudest generated-looking
 *    tell" — so the nested case is given a shape that cannot become one.
 */
function Card({
  className,
  nested = false,
  ...props
}: React.ComponentProps<"div"> & { nested?: boolean }) {
  return (
    <div
      data-slot="card"
      data-nested={nested || undefined}
      className={cn(
        nested
          ? "border-l border-border py-1 pl-3"
          : "rounded-lg bg-card p-3 shadow-xs",
        className
      )}
      {...props}
    />
  )
}

/**
 * Sentence-case, per rule 5. No uppercase micro-label — that habit is the
 * loudest generated-looking tell after nested containers.
 */
function CardTitle({ className, ...props }: React.ComponentProps<"h3">) {
  return (
    <h3
      data-slot="card-title"
      className={cn("text-label font-medium text-foreground", className)}
      {...props}
    />
  )
}

function CardHeader({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="card-header"
      className={cn("mb-2 flex items-center gap-2", className)}
      {...props}
    />
  )
}

function CardContent({ className, ...props }: React.ComponentProps<"div">) {
  return <div data-slot="card-content" className={cn("min-w-0", className)} {...props} />
}

export { Card, CardHeader, CardTitle, CardContent }
