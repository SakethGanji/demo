import * as React from "react"

import { cn } from "@/shared/lib/utils"
import { ratio, type Coverage } from "./coverage"

/**
 * Chart marks.
 *
 * The governing rule, learned across ten independent restyles: MAGNITUDE USES
 * THE NEUTRAL RAMP, IDENTITY USES CATEGORICAL HUE. Most single-series charts
 * need no categorical colour at all — a bar whose length already encodes the
 * value gains nothing from also being blue, and spending a hue there means one
 * fewer hue available where identity genuinely needs one.
 *
 * So these default to `--m1..--m6` and take a categorical colour only when the
 * caller passes one explicitly. There is no `colorful` convenience flag.
 *
 * Every mark that divides takes a `Coverage` rather than a bare percentage,
 * for the same reason `Stat` does: the reference implementation rendered
 * `width:NaN%` on a zero-row dataset because nothing forced the zero-denominator
 * case to be handled.
 */

/**
 * A horizontal magnitude bar.
 *
 * `of` is required. Passing a pre-computed percentage was the alternative and
 * it is precisely what allows a 0/0 to arrive already broken.
 */
function MagnitudeBar({
  of,
  color,
  className,
  ...props
}: Omit<React.ComponentProps<"div">, "color"> & {
  of: Coverage
  /** Categorical hue, e.g. `vizSlot(rank)`. Omit for magnitude — the default. */
  color?: string
}) {
  const r = ratio(of)

  // SHAPE-R9: an empty population is a state, not a zero-length bar. A bar of
  // width 0 reads as "the value is zero", which is a different claim.
  if (r === null) {
    return (
      <div
        data-slot="magnitude-bar"
        data-empty=""
        className={cn("flex h-1.5 items-center", className)}
        {...props}
      >
        <span className="text-footnote text-muted-foreground">no {of.unit}</span>
      </div>
    )
  }

  return (
    <div
      data-slot="magnitude-bar"
      className={cn("h-1.5 w-full overflow-hidden rounded-[2px] bg-[var(--m6)]", className)}
      {...props}
    >
      <div
        className="h-full rounded-[2px]"
        style={{
          width: `${Math.min(100, Math.max(0, r * 100))}%`,
          background: color ?? "var(--m2)",
        }}
      />
    </div>
  )
}

export type SparkPoint = { value: number }

/**
 * A trend sparkline. Neutral stroke — a sparkline is magnitude over time, and
 * identity is carried by the label beside it.
 *
 * Renders nothing but a footnote when there are fewer than two points: a
 * single-point "trend" is a claim the data does not support.
 */
function Sparkline({
  points,
  width = 54,
  height = 16,
  className,
  ...props
}: Omit<React.ComponentProps<"svg">, "points"> & {
  points: readonly number[]
  width?: number
  height?: number
}) {
  // Absence, not an apology. A one-point "trend" is a claim the data does not
  // support, but saying so in a 40px inline slot is noise — the caller decides
  // whether the gap is worth narrating.
  if (points.length < 2) return null

  const min = Math.min(...points)
  const max = Math.max(...points)
  const span = max - min

  const d = points
    .map((value, i) => {
      const x = (i / (points.length - 1)) * width
      // A flat series has span 0; pin it to the midline rather than dividing.
      const y = span === 0 ? height / 2 : height - ((value - min) / span) * height
      return `${i === 0 ? "M" : "L"}${x.toFixed(2)},${y.toFixed(2)}`
    })
    .join(" ")

  return (
    <svg
      data-slot="sparkline"
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      fill="none"
      className={cn("shrink-0 overflow-visible", className)}
      aria-hidden="true"
      {...props}
    >
      <path
        d={d}
        stroke="var(--m3)"
        strokeWidth={1.25}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}

export { MagnitudeBar, Sparkline }
