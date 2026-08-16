/**
 * Number formatting for the lens panels.
 *
 * Separate from `primitives.tsx` so that file exports only components — mixing
 * the two breaks fast refresh, which the repo lints for.
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
