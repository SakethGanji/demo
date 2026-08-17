import * as React from "react"

import { cn } from "@/shared/lib/utils"

/**
 * The typographic register of INSTRUMENT.
 *
 * Rule 4 — numbers are the hero: large figures over recessive eyebrows, with
 * `tabular-nums` on every figure so columns of numbers align and a changing
 * value does not reflow its neighbours.
 *
 * Rule 5 — sentence-case headings. `SectionTitle` cannot be made to shout;
 * there is no `uppercase` variant, because "UPPERCASE 11PX MICRO-LABELS" was
 * identified as the loudest generated-looking tell in the whole language.
 *
 * The one uppercase device that survives is `MetricLabel`, and it is not
 * exported: it lives inside `Metric`, in its documented position — 9.5px,
 * tracked, recessive, directly above a large figure. At that size and position
 * it reads as a unit label on an instrument, which is why rule 4 pairs the two.
 * Everywhere else, including `Eyebrow`, is sentence case.
 */

/**
 * A small group label.
 *
 * SENTENCE CASE. This was uppercase and tracked, and every one of the ten call
 * sites that appeared across the datasets surface used it as a *group* label —
 * "Derived from", "Acting as", "Validation" — not as a metric label. When every
 * caller uses a component against its documented purpose, the component's
 * default is wrong, not the callers.
 *
 * The uppercase tracked device still exists, but it now lives INSIDE `Metric`
 * where it cannot be misapplied: 9.5px, tracked, directly above a large figure,
 * exactly as rule 4 pairs them. At that size and position it reads as a unit
 * label on an instrument; anywhere else it is the shouting rule 5 forbids.
 */
function Eyebrow({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="eyebrow"
      className={cn("text-footnote font-medium text-muted-foreground", className)}
      {...props}
    />
  )
}

/** The KPI-strip label — the one place uppercase survives. See `Eyebrow`. */
function MetricLabel({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="metric-label"
      className={cn(
        "text-footnote font-medium tracking-[0.11em] text-muted-foreground uppercase",
        className
      )}
      {...props}
    />
  )
}

/** Sentence case, always. See rule 5. */
function SectionTitle({ className, ...props }: React.ComponentProps<"h2">) {
  return (
    <h2
      data-slot="section-title"
      className={cn("text-label font-medium text-foreground", className)}
      {...props}
    />
  )
}

/**
 * The single footnote register.
 *
 * ALL provenance goes here — evidence lines, run ids, artifact paths, cursor
 * tokens, cap labels. Collapsing them into one recessive register is what
 * frees `--t1` for the ~12 anchors a screen is allowed. If provenance were
 * allowed to drift up a step, the anchors would stop reading as anchors.
 */
function Footnote({ className, ...props }: React.ComponentProps<"p">) {
  return (
    <p
      data-slot="footnote"
      className={cn("text-footnote text-muted-foreground", className)}
      {...props}
    />
  )
}

type FigureSize = "hero" | "figure"

/**
 * A number that is the point of the region it sits in.
 *
 * `value` is a string because formatting is a decision the caller has already
 * made (compact vs full, locale, unit) and re-deriving it here would produce
 * two sources of truth for what a number looks like.
 */
function Figure({
  size = "figure",
  unit,
  className,
  children,
  ...props
}: React.ComponentProps<"div"> & { size?: FigureSize; unit?: React.ReactNode }) {
  return (
    <div
      data-slot="figure"
      className={cn(
        "font-semibold text-foreground tabular-nums",
        size === "hero" ? "text-hero" : "text-figure",
        className
      )}
      {...props}
    >
      {children}
      {unit ? (
        <span className="ml-0.5 text-small font-medium text-muted-foreground">
          {unit}
        </span>
      ) : null}
    </div>
  )
}

/**
 * An eyebrow-over-figure pair — the KPI-strip unit.
 *
 * Grouped by space, not by a box: rule 8 reserves a surface for heterogeneous,
 * independently actionable units, and a strip of metrics is neither. Wrapping
 * each of these in a Card is the mistake this component exists to prevent.
 */
function Metric({
  label,
  value,
  unit,
  size = "hero",
  note,
  className,
  ...props
}: Omit<React.ComponentProps<"div">, "children"> & {
  label: React.ReactNode
  value: React.ReactNode
  unit?: React.ReactNode
  size?: FigureSize
  note?: React.ReactNode
}) {
  return (
    <div data-slot="metric" className={cn("min-w-0", className)} {...props}>
      <MetricLabel>{label}</MetricLabel>
      <Figure size={size} unit={unit} className="mt-1">
        {value}
      </Figure>
      {note ? <Footnote className="mt-0.5">{note}</Footnote> : null}
    </div>
  )
}

/**
 * A monospace identifier — column names, codes, cursors, run ids.
 * Rule 5: table headers and identifiers wear mono because they are things you
 * type, not prose you read.
 */
function Identifier({ className, ...props }: React.ComponentProps<"span">) {
  return (
    <span
      data-slot="identifier"
      className={cn("font-mono tabular-nums", className)}
      {...props}
    />
  )
}

export { Eyebrow, SectionTitle, Footnote, Figure, Metric, Identifier }
