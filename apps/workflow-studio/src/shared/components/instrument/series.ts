/**
 * Categorical series assignment.
 *
 * The viz palette is eight slots in a fixed, pairwise-validated order. Two
 * rules govern it and both are load-bearing:
 *
 *   - NEVER CYCLE. Wrapping back to slot 1 for the ninth category means two
 *     different things wear the same colour on one chart, which is worse than
 *     no colour at all. The shape bench found this the hard way: a 500-distinct
 *     column produced 500 bars against an 8-colour palette, so slots 9–500 had
 *     no defined colour.
 *   - Above eight, RANK AND FOLD (SHAPE-R7). Take the top 8 by value, assign
 *     slots in rank order, and fold the tail into a graphite "Other" that
 *     carries its own count and share. The reference implementation put the
 *     tail in a 150px scrollbox instead, so the remaining 61.2% was never
 *     stated anywhere on the screen.
 *
 * Below three distinct values a bar chart is a ratio, and should be drawn as
 * one — `foldTopN` reports that case so the caller can pick a different mark.
 */

export const VIZ_SLOTS = 8

/**
 * The CSS custom property for a categorical slot, or the graphite "Other"
 * token beyond the palette. Returns a `var(...)` string rather than a number
 * so there is no way to index past the end.
 */
export function vizSlot(rank: number): string {
  if (rank < 0 || rank >= VIZ_SLOTS) return "var(--m5)"
  return `var(--viz-${rank + 1})`
}

export type Folded<T> = {
  /** Top-N members in rank order; index is the palette slot. */
  head: T[]
  /** The folded tail, present only when something was folded. */
  other: { count: number; value: number; share: number } | null
  /** True when there are too few members for a distribution to mean anything. */
  degenerate: boolean
}

/**
 * Rank, take the top N, and fold the tail — with its count AND its share, so
 * the part that is not drawn is still stated.
 */
export function foldTopN<T>(
  items: readonly T[],
  valueOf: (item: T) => number,
  n: number = VIZ_SLOTS
): Folded<T> {
  const ranked = [...items].sort((a, b) => valueOf(b) - valueOf(a))
  const total = ranked.reduce((sum, item) => sum + valueOf(item), 0)
  const head = ranked.slice(0, n)
  const tail = ranked.slice(n)

  const tailValue = tail.reduce((sum, item) => sum + valueOf(item), 0)

  return {
    head,
    other:
      tail.length > 0
        ? {
            count: tail.length,
            value: tailValue,
            // total > 0 is guaranteed when tail is non-empty and values are
            // non-negative, but guard anyway — a zero denominator here would
            // print NaN% in the legend.
            share: total > 0 ? tailValue / total : 0,
          }
        : null,
    degenerate: ranked.length < 3,
  }
}
