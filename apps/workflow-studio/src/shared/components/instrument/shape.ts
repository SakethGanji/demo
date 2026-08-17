/**
 * SHAPE RULES — R1…R11.
 *
 * The cockpit was drawn once, against a 9-column tidy CSV with 3 enums, 1
 * numeric and 1 date. `design-prototypes/terminal-shapes.html` ran that design
 * against seven pathological shapes and found 24 baked-in assumptions, 9 of
 * which break outright. `terminal-adaptive.html` implements the fixes.
 *
 * These are the price of the claim "upload any tabular data". The worst one is
 * not cosmetic: on a mixed-type column, `sum` silently dropped 19.6% of rows
 * and printed a confident total — the exact silent-wrong-answer class the
 * backend spent an entire audit eliminating, reintroduced at the presentation
 * layer. That one is R11 and it lives in `coverage.ts`, where the type system
 * enforces it.
 *
 * Everything here is a PURE FUNCTION over column metadata, deliberately: shape
 * adaptation is a decision that should be inspectable and identical everywhere,
 * not re-derived inline by each surface with slightly different thresholds.
 *
 * NAMING: the design files use "R1…R11" for these AND "R1…R7" in CSS comments
 * for the INSTRUMENT visual rules. They are different systems. Everything here
 * is a SHAPE rule; the visual rules are enforced by the components.
 */

/** The minimum a surface must know about a column to adapt to it. */
export interface ShapeColumn {
  name: string
  dtype?: string | null
  /** Distinct value count, when a profile has been run. */
  uniqueCount?: number | null
  /** Non-null count, when a profile has been run. */
  nonNullCount?: number | null
}

/** R1/R2 — above this the rail stops being a list and becomes a manager. */
export const WIDE_TABLE_COLUMNS = 30
/** R8 — below this the rail is more chrome than content. */
export const NARROW_TABLE_COLUMNS = 4
/** R3 — one type covering this share makes a per-header chip pure texture. */
export const DOMINANT_TYPE_SHARE = 0.95
/** R4 — above this a distribution is meaningless; show identity instead. */
export const IDENTITY_DISTINCT_SHARE = 0.95
/** R7 — below this a "distribution" is really a ratio. */
export const RATIO_MAX_DISTINCT = 3
/** R5 — a column with more distinct values than this is not a dimension. */
export const DIMENSION_MAX_DISTINCT = 50

const FLOAT_TYPES = /float|double|decimal|numeric|real/i
const TEMPORAL_TYPES = /date|time|timestamp/i
const NUMERIC_TYPES = /int|float|double|decimal|numeric|real|long|short|byte/i

/** R1/R2 — is this table wide enough that the rail must become a manager? */
export function isWideTable(columnCount: number): boolean {
  return columnCount > WIDE_TABLE_COLUMNS
}

/**
 * R8 — is this table narrow enough that the rail is dead weight?
 * A single-column dataset is a list, and should be drawn as one.
 */
export function isNarrowTable(columnCount: number): boolean {
  return columnCount > 0 && columnCount < NARROW_TABLE_COLUMNS
}

export interface IdentityPick {
  column: ShapeColumn
  /**
   * `measured` — chosen from real distinctness in a profile.
   * `positional` — no profile has been run, so this is the rule's documented
   * fallback (first non-float column, else column 1) and nothing more.
   *
   * Callers should say which they are showing. A pin presented as "the
   * identity" when it was really "the first column we found" is a small claim
   * the data does not support.
   */
  basis: 'measured' | 'positional'
}

/**
 * R2 — the identity column: what tells you WHICH ROW you are looking at.
 *
 * Highest distinctness among non-float columns, **else column 1** — the
 * fallback is part of the rule, not a shortcut. Profiling is not automatic on
 * upload, so on a freshly uploaded 189-column file there is no distinctness to
 * rank by; refusing to pin anything there would mean R2 never fires in exactly
 * the case it exists for, and the identity leaves the viewport at column 5 and
 * never returns.
 *
 * Floats are excluded because a float with 100% distinct values is a
 * measurement, not an identifier, and pinning it would spend the one pinned
 * slot on something nobody navigates by.
 */
export function detectIdentity(columns: readonly ShapeColumn[]): IdentityPick | null {
  if (columns.length === 0) return null

  const nonFloat = columns.filter((c) => !FLOAT_TYPES.test(c.dtype ?? ''))

  const scored = nonFloat
    .filter((c) => c.uniqueCount != null && c.nonNullCount != null && c.nonNullCount > 0)
    .map((c) => ({ column: c, distinctness: (c.uniqueCount ?? 0) / (c.nonNullCount || 1) }))
    .sort((a, b) => b.distinctness - a.distinctness)

  if (scored.length > 0) return { column: scored[0].column, basis: 'measured' }
  return { column: nonFloat[0] ?? columns[0], basis: 'positional' }
}

/**
 * R4 — is this column so distinct that a top-values chart says nothing?
 *
 * Above 95% distinct every bar is one row tall. The reference implementation
 * drew NO CARD AT ALL for such a column, which is worse than a useless chart:
 * a column with no card reads as a column with no problem. The caller should
 * render an identity panel instead, and SAY that suppression happened.
 */
export function isIdentityLike(column: ShapeColumn): boolean {
  if (column.uniqueCount == null || !column.nonNullCount) return false
  return column.uniqueCount / column.nonNullCount >= IDENTITY_DISTINCT_SHARE
}

export interface DominantType {
  dtype: string
  count: number
  share: number
  /** Columns that do NOT carry the dominant type — these keep their chip. */
  exceptions: ShapeColumn[]
}

/**
 * R3 — when one type covers ≥95% of columns, the per-header type chip stops
 * being information and becomes texture (a `float64` badge repeated 241 times).
 * Drop it for a single header-strip statement and let the exceptions keep a chip.
 *
 * Returns null when no type dominates, i.e. keep the per-header chips.
 */
export function dominantType(columns: readonly ShapeColumn[]): DominantType | null {
  if (columns.length === 0) return null

  const counts = new Map<string, number>()
  for (const c of columns) {
    const t = c.dtype ?? 'unknown'
    counts.set(t, (counts.get(t) ?? 0) + 1)
  }

  let best: { dtype: string; count: number } | null = null
  for (const [dtype, count] of counts) {
    if (!best || count > best.count) best = { dtype, count }
  }
  if (!best) return null

  const share = best.count / columns.length
  if (share < DOMINANT_TYPE_SHARE) return null

  return {
    dtype: best.dtype,
    count: best.count,
    share,
    exceptions: columns.filter((c) => (c.dtype ?? 'unknown') !== best.dtype),
  }
}

export interface PrefixGroup {
  /** Longest common prefix, e.g. `dv01_`. Empty string for ungrouped columns. */
  prefix: string
  columns: ShapeColumn[]
}

/**
 * R1 — collapse a wide column list by longest common prefix.
 *
 * A 241-column rail is a 6,000px scroll with no structure. Real wide tables are
 * almost always prefixed families (`dv01_1m`, `dv01_3m`, `dv01_6m`), so folding
 * on the prefix turns an unusable list into ten groups.
 *
 * `minGroup` guards against inventing structure: two columns that happen to
 * share three characters are not a family.
 */
export function groupByPrefix(
  columns: readonly ShapeColumn[],
  minGroup = 3,
): PrefixGroup[] {
  const buckets = new Map<string, ShapeColumn[]>()

  for (const c of columns) {
    // Split on the first separator; that is where a family name ends in every
    // real-world schema we have seen (snake, kebab or dotted).
    const match = /^([A-Za-z0-9]+[_\-.])/.exec(c.name)
    const key = match ? match[1] : ''
    const list = buckets.get(key)
    if (list) list.push(c)
    else buckets.set(key, [c])
  }

  const groups: PrefixGroup[] = []
  const ungrouped: ShapeColumn[] = []

  for (const [prefix, cols] of buckets) {
    if (prefix && cols.length >= minGroup) groups.push({ prefix, columns: cols })
    else ungrouped.push(...cols)
  }

  groups.sort((a, b) => b.columns.length - a.columns.length)
  if (ungrouped.length > 0) groups.push({ prefix: '', columns: ungrouped })
  return groups
}

export interface AddressedColumn {
  name: string
  /** 1-based position in the sheet's declared order. */
  ordinal: number
  /** What to render: `amount` normally, `amount ⟨3⟩` when the name repeats. */
  label: string
  /** True when another column in this sheet has the same name. */
  ambiguous: boolean
}

/**
 * R10 — address a column by ORDINAL + NAME, never by name alone.
 *
 * Two columns both called `Amount` are indistinguishable after truncation, and
 * every "which column did I filter on?" answer becomes a coin flip. Duplicates
 * render as `Amount ⟨3⟩` / `Amount ⟨7⟩` so the ordinal disambiguates them.
 */
export function addressColumns(names: readonly string[]): AddressedColumn[] {
  const seen = new Map<string, number>()
  for (const n of names) seen.set(n, (seen.get(n) ?? 0) + 1)

  return names.map((name, i) => {
    const ambiguous = (seen.get(name) ?? 0) > 1
    const ordinal = i + 1
    return {
      name,
      ordinal,
      label: ambiguous ? `${name} ⟨${ordinal}⟩` : name,
      ambiguous,
    }
  })
}

/**
 * R10 — truncate from the MIDDLE, keeping the discriminating head and tail.
 *
 * A 64-char hash ellipsised to 14 leading characters makes every cell read
 * alike. `RT-40118822…NY01` still tells two rows apart; `RT-40118822…` does not.
 */
export function middleTruncate(value: string, max = 22): string {
  // Iterate CODE POINTS, not code units. `slice()` works on UTF-16 units, so a
  // cut landing inside a surrogate pair left a lone high surrogate before the
  // ellipsis and a lone low surrogate after it — both render as tofu, which
  // means the grid invented a character the data never contained. An emoji is
  // two code units; a flag or a skin-toned emoji is more.
  const chars = Array.from(value)
  if (max < 5 || chars.length <= max) return value
  const keep = max - 1
  const head = Math.ceil(keep / 2)
  const tail = Math.floor(keep / 2)
  return `${chars.slice(0, head).join('')}…${chars.slice(chars.length - tail).join('')}`
}

export interface ChartCandidates {
  /** Low-cardinality columns you can group by. */
  dimensions: ShapeColumn[]
  /** Numeric columns you can aggregate. */
  measures: ShapeColumn[]
  /** Date/time columns you can put on an x axis. */
  temporal: ShapeColumn[]
}

/**
 * R5 — compute the candidate sets BEFORE offering a chart.
 *
 * The reference builder was chart-gated: it offered every chart type and let
 * you discover emptiness by building one. On an all-numeric table it opened
 * empty and stayed empty. Capability-gating means a chart is only offered when
 * its requirements are met, and a withdrawn chart can say WHY.
 *
 * Columns with no profile are not counted as dimensions: distinctness is
 * unknown, and guessing produces a group-by that returns one row per row.
 */
export function chartCandidates(columns: readonly ShapeColumn[]): ChartCandidates {
  const dimensions: ShapeColumn[] = []
  const measures: ShapeColumn[] = []
  const temporal: ShapeColumn[] = []

  for (const c of columns) {
    const dtype = c.dtype ?? ''
    if (TEMPORAL_TYPES.test(dtype)) {
      temporal.push(c)
      continue
    }
    if (NUMERIC_TYPES.test(dtype)) {
      measures.push(c)
      // A low-cardinality integer (a year, a flag, a code) is also groupable.
      if (c.uniqueCount != null && c.uniqueCount <= DIMENSION_MAX_DISTINCT) dimensions.push(c)
      continue
    }
    if (c.uniqueCount != null && c.uniqueCount <= DIMENSION_MAX_DISTINCT) dimensions.push(c)
  }

  return { dimensions, measures, temporal }
}

export type ChartKind = 'bar' | 'line' | 'ratio' | 'correlation' | 'distribution'

export interface ChartOffer {
  kind: ChartKind
  available: boolean
  /** Why it is withdrawn. Present only when `available` is false. */
  reason?: string
}

/**
 * R5/R6 — which charts this data can actually support, and why not otherwise.
 *
 * R6: with zero dimensions, offer correlation and distribution comparison
 * rather than nothing. Those are OFFERS with visible parameters, never an
 * inference — binning a measure into a pseudo-dimension is a choice the user
 * makes, not one the app makes silently on their behalf.
 */
export function chartOffers(c: ChartCandidates): ChartOffer[] {
  const offers: ChartOffer[] = [
    c.dimensions.length > 0 && c.measures.length > 0
      ? { kind: 'bar', available: true }
      : {
          kind: 'bar',
          available: false,
          reason:
            c.dimensions.length === 0
              ? 'no column qualifies as a dimension — every candidate is numeric or too distinct'
              : 'no numeric column to aggregate',
        },
    c.temporal.length > 0 && c.measures.length > 0
      ? { kind: 'line', available: true }
      : {
          kind: 'line',
          available: false,
          reason:
            c.temporal.length === 0
              ? 'no date or timestamp column exists'
              : 'no numeric column to plot',
        },
    c.measures.length > 0
      ? { kind: 'distribution', available: true }
      : { kind: 'distribution', available: false, reason: 'no numeric column to distribute' },
    // R6: the zero-dimension fallback. Two measures can always be correlated.
    c.measures.length >= 2
      ? { kind: 'correlation', available: true }
      : { kind: 'correlation', available: false, reason: 'needs at least two numeric columns' },
  ]
  return offers
}

/**
 * R7 — below three distinct values a bar chart is a ratio, and should be drawn
 * as one. Two bars side by side invite a comparison of lengths when the only
 * fact is a proportion.
 */
export function isRatioShaped(distinctCount: number): boolean {
  return distinctCount > 0 && distinctCount < RATIO_MAX_DISTINCT
}

/**
 * R11 — the parsed share of a mixed-type column, for the type badge
 * (`int64 · 80%`). Returns null when the column is clean, so a badge is only
 * qualified when qualification is warranted.
 */
export function typeConformance(column: ShapeColumn, totalRows: number): number | null {
  if (!totalRows || column.nonNullCount == null) return null
  const share = column.nonNullCount / totalRows
  return share >= 1 ? null : share
}
