import * as React from "react"

import { cn } from "@/shared/lib/utils"
import { Footnote, Identifier } from "./Typography"
import { coverageNote, isEmpty, type Coverage } from "./coverage"

/**
 * A single computed statistic, rendered with the population it was computed
 * over. See `coverage.ts` for why this is not optional.
 *
 * The `coverage` prop is REQUIRED. That is the entire design:
 *
 *   <Stat name="sum" value="4,118,204,660" coverage={coverage(968725, 1204880)} />
 *   → sum   4,118,204,660   over 80.4% · 968,725 rows
 *
 *   <Stat name="sum" value="4,118,204,660" />
 *   → does not compile
 *
 * When the population is empty the value is suppressed entirely rather than
 * rendered from a zero denominator (SHAPE-R9). A caller cannot opt out of that
 * by passing a pre-formatted "0%" string, because the decision is made here
 * from the Coverage, not from the value.
 */
function Stat({
  name,
  value,
  coverage,
  className,
  ...props
}: Omit<React.ComponentProps<"div">, "children"> & {
  name: React.ReactNode
  /** Pre-formatted. Formatting is the caller's decision; denominators are not. */
  value: React.ReactNode
  coverage: Coverage
}) {
  const empty = isEmpty(coverage)
  const note = coverageNote(coverage)

  return (
    <div
      data-slot="stat"
      data-partial={note ? "" : undefined}
      className={cn("flex items-baseline gap-3", className)}
      {...props}
    >
      <Identifier className="w-16 shrink-0 text-small text-muted-foreground">
        {name}
      </Identifier>
      <span className="text-body font-medium text-foreground tabular-nums">
        {empty ? "—" : value}
      </span>
      {empty ? (
        <Footnote>no {coverage.unit} — not computed</Footnote>
      ) : note ? (
        <Footnote>{note}</Footnote>
      ) : null}
    </div>
  )
}

/**
 * A block of statistics that share one population.
 *
 * The shared `coverage` is stated ONCE at the top rather than repeated on
 * every row, because eight identical `over 80.4%` qualifiers is exactly the
 * kind of repetition that stops being read. Individual rows may still override
 * it when their own denominator differs — `max` over the parsed rows and
 * `null count` over all rows are genuinely different populations.
 */
function StatList({
  coverage,
  className,
  children,
  ...props
}: React.ComponentProps<"div"> & { coverage: Coverage }) {
  const note = coverageNote(coverage)
  return (
    <div data-slot="stat-list" className={cn("min-w-0", className)} {...props}>
      {note ? <Footnote className="mb-1.5">{note}</Footnote> : null}
      <div className="flex flex-col gap-1">{children}</div>
    </div>
  )
}

export { Stat, StatList }
