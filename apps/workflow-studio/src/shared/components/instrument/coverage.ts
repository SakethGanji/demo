/**
 * Coverage — the denominator a statistic was actually computed over.
 *
 * This exists because of one specific failure. The shape bench
 * (design-prototypes/terminal-shapes.html, S6) ran the cockpit against a
 * column that is 80.4% int64, 14.9% unparsed text and 4.7% null, and found
 * that `sum` silently dropped 19.6% of the rows and printed a confident total.
 * That is the silent-wrong-answer class the analytics service spent an entire
 * audit eliminating, reintroduced at the presentation layer — and it is worse
 * here, because a number on a screen is the thing a human acts on.
 *
 * SHAPE-R11 is the fix: every statistic carries its denominator. This module
 * makes that a property of the type system rather than a habit.
 *
 *   - `Coverage` is branded, so the only way to obtain one is `coverage()`.
 *     An object literal will not type-check. That turns "shut the compiler up"
 *     into "name both numbers", which is the entire point — the bug was never
 *     that someone chose the wrong denominator, it was that nobody was asked.
 *   - `<Stat>` takes `coverage` as a REQUIRED prop, so a statistic without a
 *     denominator is a compile error, not a review comment.
 *   - `ratio()` returns `null` for a zero population rather than `NaN`, which
 *     is SHAPE-R9: zero rows is a state, not a failure, and no statistic is
 *     ever printed from a zero denominator. The reference grid rendered
 *     `width:NaN%` here.
 */

declare const coverageBrand: unique symbol

export type Coverage = {
  /** Members that actually contributed to the statistic. */
  readonly counted: number
  /** Members of the population the statistic claims to describe. */
  readonly total: number
  /**
   * What is being counted, plural. Defaults to `rows` because that is the
   * overwhelmingly common case, but it is NOT always rows — a governance
   * screen counts columns and datasets, and printing "over 62% · 8 rows"
   * against a population of columns is its own small lie.
   */
  readonly unit: string
  readonly [coverageBrand]: true
}

/**
 * Build a coverage denominator. Both arguments are required and neither has a
 * default — if you have to look up what the population is, that is the check
 * working.
 */
export function coverage(counted: number, total: number, unit = 'rows'): Coverage {
  return { counted, total, unit } as Coverage
}

/**
 * The common case: the statistic saw every row it claims to describe.
 * Named rather than `coverage(n, n)` so a reader can tell a genuine full
 * population from two numbers that happen to match today.
 */
export function complete(total: number, unit = 'rows'): Coverage {
  return coverage(total, total, unit)
}

/**
 * Share of the population the statistic covers, or `null` when the population
 * is empty. Callers must handle `null` — that is what stops a 0/0 reaching the
 * DOM as `NaN%`.
 */
export function ratio(c: Coverage): number | null {
  if (c.total <= 0) return null
  return c.counted / c.total
}

/** True when the statistic silently describes fewer rows than it claims to. */
export function isPartial(c: Coverage): boolean {
  return c.total > 0 && c.counted < c.total
}

/** True when there is no population at all — render a state, not a number. */
export function isEmpty(c: Coverage): boolean {
  return c.total <= 0
}

/**
 * The qualifier that rides alongside a partial statistic, e.g.
 * `over 80.4% · 968,725 rows`. Returns `null` when the statistic is complete,
 * because a full denominator is the expected case and stating it everywhere
 * would spend the footnote register on noise.
 */
export function coverageNote(c: Coverage): string | null {
  const r = ratio(c)
  if (r === null) return null
  if (!isPartial(c)) return null
  const pct = (r * 100).toFixed(1).replace(/\.0$/, "")
  return `over ${pct}% · ${c.counted.toLocaleString()} ${c.unit}`
}
