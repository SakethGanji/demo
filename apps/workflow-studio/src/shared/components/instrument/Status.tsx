import * as React from "react"

import { cn } from "@/shared/lib/utils"

/**
 * Status — rule 6: status is SHAPE + HUE + WORD, never hue alone.
 *
 * Five states, five shapes, so the reading survives colour-vision deficiency,
 * a monochrome print, and a screenshot pasted into a ticket.
 *
 * The API enforces it rather than documenting it: `children` is required and
 * is the word. There is no way to render the mark on its own — no `<Status />`,
 * no `bare` prop, no exported shape component. If you want the dot without the
 * label, this component will not give it to you, and that is deliberate.
 *
 * The hue rides on the SHAPE, never on the word. In light mode `--st-warn`
 * measures 1.72:1 against a panel and `--st-serious` 2.48:1 — as text they
 * would be unreadable, and colouring a word by status is how status starts
 * impersonating severity (see `Severity`). The word wears an ink token in both
 * modes, so there is one behaviour to reason about.
 */

export type StatusKind = "good" | "warning" | "serious" | "critical" | "unknown"

const SHAPE: Record<StatusKind, string> = {
  // circle
  good: "rounded-full",
  // triangle
  warning: "[clip-path:polygon(50%_0%,100%_100%,0%_100%)]",
  // diamond — a rotated square
  serious: "rotate-45 rounded-[1px]",
  // square
  critical: "rounded-[1px]",
  // ring — an outline, so "unknown" reads as absent rather than as a state
  unknown: "rounded-full bg-transparent ring-1 ring-current",
}

const HUE: Record<StatusKind, string> = {
  good: "text-[var(--st-good)]",
  warning: "text-[var(--st-warn)]",
  serious: "text-[var(--st-serious)]",
  critical: "text-[var(--st-crit)]",
  unknown: "text-muted-foreground",
}

function Status({
  kind,
  className,
  children,
  ...props
}: Omit<React.ComponentProps<"span">, "children"> & {
  kind: StatusKind
  /** The word. Required — status never rides on hue alone. */
  children: React.ReactNode
}) {
  return (
    <span
      data-slot="status"
      data-status={kind}
      className={cn("inline-flex items-center gap-1.5 whitespace-nowrap", className)}
      {...props}
    >
      <span
        aria-hidden="true"
        className={cn(
          "size-[7px] shrink-0 bg-current",
          SHAPE[kind],
          HUE[kind]
        )}
      />
      <span className="text-foreground">{children}</span>
    </span>
  )
}

export { Status }
