/**
 * Chart marks for the analytics lens.
 *
 * Private to that lens — they were previously exported from `primitives.tsx`
 * alongside the shared furniture despite having a single consumer, which made
 * that file look like a design system when it was mostly one lens's internals.
 *
 * Every chart here is **single-series magnitude** — "how big is this bar next
 * to that one" — so they use the NEUTRAL ramp (`--m1..--m6`), not a categorical
 * hue. That is the rule learned across ten restyles: magnitude uses the neutral
 * ramp, identity uses categorical hue. A bar whose length already encodes the
 * value gains nothing from also being blue, and spending a hue there leaves one
 * fewer for a chart where identity genuinely needs one. These used `--viz-1`
 * before, which was a categorical slot doing a magnitude job.
 *
 * Two rules stay load-bearing rather than stylistic:
 *   - Every bar ships a visible label and value, so identity never rides on the
 *     mark's colour alone.
 *   - Text wears ink tokens, never a series colour.
 */

import { compact, num } from '@/shared/lib/format';

interface BarDatum {
  label: string;
  value: number;
  /** Secondary text shown right-aligned, e.g. a percentage. */
  hint?: string;
}

/**
 * Horizontal magnitude bars with direct labels.
 *
 * Horizontal because the labels are category names of unpredictable length —
 * vertical columns would force rotated ticks, which is the single most common
 * way a small chart becomes unreadable. Bars are anchored to a shared left
 * baseline and scaled to the largest value present, so lengths are comparable
 * within the group and nowhere else; that is why the value sits beside each bar.
 */
export function MiniBars({ data, testid }: { data: BarDatum[]; testid?: string }) {
  const max = Math.max(...data.map((d) => d.value), 0);
  if (!data.length || max <= 0) return null;
  return (
    <div className="flex flex-col gap-1.5" data-testid={testid}>
      {data.map((d, i) => (
        <div key={`${d.label}-${i}`}>
          <div className="flex items-baseline justify-between gap-2">
            <span className="truncate text-small text-foreground" title={d.label}>
              {d.label}
            </span>
            <span className="shrink-0 text-micro text-muted-foreground tabular-nums">
              {compact(d.value)}
              {d.hint ? ` · ${d.hint}` : ''}
            </span>
          </div>
          {/* Track is a recessive rail, not a second data mark. */}
          <div className="mt-0.5 h-1.5 w-full overflow-hidden rounded-full bg-[var(--m6)]">
            <div
              className="h-full rounded-full"
              style={{
                width: `${Math.max((d.value / max) * 100, 2)}%`,
                background: 'var(--m2)',
              }}
            />
          </div>
        </div>
      ))}
    </div>
  );
}

interface HistogramDatum {
  start: number;
  end: number;
  count: number;
}

/**
 * A distribution sparkline for a numeric column.
 *
 * Columns are anchored to a common baseline and share one scale, with a 2px gap
 * so adjacent bins stay separable. Only the axis extremes are labelled — a
 * number on every bin would be noise at this size, and the exact figures are
 * already in the stats row above it.
 */
export function Histogram({ bins, testid }: { bins: HistogramDatum[]; testid?: string }) {
  const max = Math.max(...bins.map((b) => b.count), 0);
  if (!bins.length || max <= 0) return null;
  const lo = bins[0].start;
  const hi = bins[bins.length - 1].end;
  return (
    <div data-testid={testid}>
      <div className="flex h-10 items-end gap-[2px]">
        {bins.map((b, i) => (
          <div
            key={i}
            className="flex-1 rounded-t-[2px]"
            style={{
              height: `${Math.max((b.count / max) * 100, 3)}%`,
              background: 'var(--m2)',
            }}
            title={`${num(b.start)} – ${num(b.end)}: ${b.count.toLocaleString()}`}
          />
        ))}
      </div>
      <div className="mt-0.5 flex justify-between text-footnote text-muted-foreground tabular-nums">
        <span>{num(lo)}</span>
        <span>{num(hi)}</span>
      </div>
    </div>
  );
}

/**
 * A single ratio against its limit. A meter, not a two-slice pie.
 *
 * `tone` is the one place here that touches the reserved status ramp, and it is
 * always paired with the visible percentage beside it — colour never carries
 * the reading on its own.
 */
export function Meter({
  value,
  label,
  tone = 'neutral',
}: {
  value: number;
  label: string;
  tone?: 'neutral' | 'warning' | 'critical';
}) {
  const pct = Math.max(0, Math.min(100, value));
  const background =
    tone === 'critical'
      ? 'var(--st-crit)'
      : tone === 'warning'
        ? 'var(--st-warn)'
        : 'var(--m2)';
  return (
    <div>
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-small text-muted-foreground">{label}</span>
        <span className="text-small tabular-nums">{pct.toFixed(1)}%</span>
      </div>
      <div className="mt-0.5 h-1.5 w-full overflow-hidden rounded-full bg-[var(--m6)]">
        <div className="h-full rounded-full" style={{ width: `${pct}%`, background }} />
      </div>
    </div>
  );
}
