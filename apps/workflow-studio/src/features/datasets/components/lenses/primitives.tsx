/**
 * Shared furniture for the lens panels, including the chart marks.
 *
 * Every chart here is **single-series magnitude** — "how big is this bar next to
 * that one". That is deliberately the easy case: one hue, no categorical
 * palette, so there is no colour-vision-deficiency adjacency problem to solve.
 * The moment a lens needs to tell *distinct series* apart it must move to the
 * validated `--viz-1..8` order in `index.css` and assign slots in fixed order.
 *
 * Two rules are load-bearing rather than stylistic:
 *   - Every bar ships a visible label and value. Three light-mode viz slots sit
 *     under 3:1 contrast against the surface, and a legible label is the agreed
 *     relief — so identity never rides on the colour of the mark alone.
 *   - Text wears text tokens, never the series colour.
 */

import type { ReactNode } from 'react';
import { cn } from '@/shared/lib/utils';
import { compact, num } from './format';

export function Section({ title, action, children }: { title: string; action?: ReactNode; children: ReactNode }) {
  return (
    <div className="mb-4">
      <div className="mb-1.5 flex items-center justify-between gap-2">
        <h3 className="text-[10px] font-medium tracking-wide text-muted-foreground uppercase">
          {title}
        </h3>
        {action}
      </div>
      {children}
    </div>
  );
}

export function Row({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-1">
      <span className="text-[11px] text-muted-foreground">{label}</span>
      <span className="text-right text-[12px]">{value}</span>
    </div>
  );
}

export function LensEmpty({ children }: { children: ReactNode }) {
  return (
    <p className="rounded-md border border-dashed border-border px-3 py-4 text-center text-[11px] text-muted-foreground">
      {children}
    </p>
  );
}

/**
 * The refusal state for a seat without raw access.
 *
 * Worth rendering carefully: this is not an error and not an empty result — the
 * server declined to compute over sensitive columns. Saying so plainly is the
 * difference between "the app is broken" and "you are not allowed to see this".
 */
export function LensRestricted({ what }: { what: string }) {
  return (
    <div className="rounded-md border border-border bg-muted/40 px-3 py-3">
      <p className="text-[12px] font-medium">Restricted for this seat</p>
      <p className="mt-1 text-[11px] text-muted-foreground">
        {what} is refused on datasets that declare sensitive columns unless you are an admin,
        owner or superuser. Masking is not enough here — a computed result could reconstruct the
        values it hides.
      </p>
    </div>
  );
}

export function LensError({ children }: { children: ReactNode }) {
  return (
    <p className="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-[11px] text-destructive">
      {children}
    </p>
  );
}

export interface BarDatum {
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
            <span className="truncate text-[11px] text-foreground" title={d.label}>
              {d.label}
            </span>
            <span className="shrink-0 text-[10px] text-muted-foreground tabular-nums">
              {compact(d.value)}
              {d.hint ? ` · ${d.hint}` : ''}
            </span>
          </div>
          {/* Track is a recessive rail, not a second data mark. */}
          <div className="mt-0.5 h-1.5 w-full overflow-hidden rounded-full bg-muted">
            <div
              className="h-full rounded-full"
              style={{
                width: `${Math.max((d.value / max) * 100, 2)}%`,
                background: 'var(--viz-1)',
              }}
            />
          </div>
        </div>
      ))}
    </div>
  );
}

export interface HistogramDatum {
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
              background: 'var(--viz-1)',
            }}
            title={`${num(b.start)} – ${num(b.end)}: ${b.count.toLocaleString()}`}
          />
        ))}
      </div>
      <div className="mt-0.5 flex justify-between text-[9px] text-muted-foreground tabular-nums">
        <span>{num(lo)}</span>
        <span>{num(hi)}</span>
      </div>
    </div>
  );
}

/**
 * A single ratio against its limit. A meter, not a two-slice pie.
 *
 * `tone` is the status channel, so it is always paired with the visible
 * percentage beside it — colour never carries the reading on its own.
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
      ? 'var(--viz-critical)'
      : tone === 'warning'
        ? 'var(--viz-warning)'
        : 'var(--viz-1)';
  return (
    <div>
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-[11px] text-muted-foreground">{label}</span>
        <span className="text-[11px] tabular-nums">{pct.toFixed(1)}%</span>
      </div>
      <div className="mt-0.5 h-1.5 w-full overflow-hidden rounded-full bg-muted">
        <div className="h-full rounded-full" style={{ width: `${pct}%`, background }} />
      </div>
    </div>
  );
}

/**
 * Health status. The dot is an accent on a word that already says the state —
 * status colour never appears without its label.
 */
export function StatusDot({ status }: { status: string }) {
  const color =
    status === 'ok'
      ? 'var(--viz-good)'
      : status === 'warn'
        ? 'var(--viz-warning)'
        : status === 'fail'
          ? 'var(--viz-critical)'
          : 'var(--muted-foreground)';
  return (
    <span
      aria-hidden
      className="inline-block size-1.5 shrink-0 rounded-full"
      style={{ background: color }}
    />
  );
}

/** A dtype chip. Neutral by design — dtype is a fact, not a status. */
export function DtypeChip({ dtype, className }: { dtype?: string | null; className?: string }) {
  return (
    <span
      className={cn(
        'shrink-0 rounded border border-border px-1 font-mono text-[9px] text-muted-foreground',
        className,
      )}
    >
      {dtype ?? '?'}
    </span>
  );
}
