/**
 * Number and size formatting.
 *
 * Studio-wide rather than per-feature because `formatBytes` had drifted into
 * three byte-identical copies across the datasets surface alone (the dataset
 * rail, the catalog and the library lens), and `compact`/`num` lived beside
 * the lens components where nothing outside two files could reach them — so
 * six of the seven lenses formatted their numbers ad hoc with
 * `toLocaleString()` and the "panels are 384px wide, 1.2M beats 1204311"
 * rationale was honoured in exactly one of them.
 *
 * Keep this module free of components: mixing exported components and plain
 * functions in one file breaks fast refresh, which the repo lints for.
 */

/** Compact form — panels are 384px wide; "1.2M" beats "1204311". */
export function compact(n: number | null | undefined): string {
  if (n == null) return '—';
  if (Math.abs(n) >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (Math.abs(n) >= 10_000) return `${(n / 1000).toFixed(0)}k`;
  return n.toLocaleString();
}

/**
 * A single value for an axis end or a stat row. Floats are trimmed to three
 * decimals — profile stats routinely carry fifteen, which is noise at this size.
 */
export function num(v: unknown): string {
  if (v === null || v === undefined) return '—';
  if (typeof v === 'number') {
    if (Number.isInteger(v)) return v.toLocaleString();
    return Number(v.toFixed(3)).toLocaleString();
  }
  return String(v);
}

const SIZE_UNITS = ['B', 'KB', 'MB', 'GB', 'TB'] as const;

/**
 * Storage size split into figure and unit.
 *
 * Separate from `formatBytes` because rule 4 wants the number large and the
 * unit recessive — `<Figure unit>` takes them apart, and re-splitting a
 * formatted string to get them back is how a renderer starts parsing its own
 * output.
 */
export function formatSizeParts(n: number | null | undefined): { value: string; unit: string } {
  if (n == null) return { value: '—', unit: '' };
  let size = n;
  let i = 0;
  while (size >= 1024 && i < SIZE_UNITS.length - 1) {
    size /= 1024;
    i += 1;
  }
  // Bytes are whole things; everything above is a scaled approximation.
  return { value: i === 0 ? String(Math.round(size)) : size.toFixed(1), unit: SIZE_UNITS[i] };
}

/**
 * Storage size in the largest unit that keeps the figure readable.
 *
 * Scales all the way to TB. It used to stop at MB, which rendered a library
 * total as `1228.8 MB` — technically true, and unreadable at a glance.
 */
export function formatBytes(n: number | null | undefined): string {
  if (n == null) return '—';
  const { value, unit } = formatSizeParts(n);
  return `${value} ${unit}`;
}

/**
 * A short, sortable date for dense rows: `2026-08-16`.
 *
 * Guarded rather than trusting: most entities emit a Postgres `::text`
 * timestamp while `JobOut` emits ISO-8601. `new Date` parses both, but a
 * malformed value must render as an em dash, never as "Invalid Date".
 */
export function shortDate(value: string | null | undefined): string {
  if (!value) return '—';
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? '—' : d.toISOString().slice(0, 10);
}

/** A signed delta, for deltas that are meaningful in both directions. */
export function signed(n: number): string {
  return n > 0 ? `+${n.toLocaleString()}` : n.toLocaleString();
}
