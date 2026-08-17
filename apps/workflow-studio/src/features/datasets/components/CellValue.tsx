/**
 * How a single grid cell renders.
 *
 * The reference cockpit gives its `status` column a coloured dot beside each
 * word, and that rhythm is a big part of why the grid reads as an instrument
 * rather than a spreadsheet dump. Reproducing it on ARBITRARY uploaded data is
 * where it gets dangerous: this app promises "upload any tabular data", so a
 * column called `status` might hold anything, and painting an unknown word
 * green is inventing a meaning the data never carried.
 *
 * So the rule here is narrow and stated:
 *
 *  - A value gets a HUE only when it appears in `STATUS_WORDS` below — an
 *    explicit, short, English vocabulary. Nothing is inferred from position,
 *    frequency, or the column's name.
 *  - Every other low-cardinality value still gets a SHAPE, in the neutral ramp.
 *    That buys the visual rhythm without asserting good or bad.
 *  - The word is always present and always wears an ink token, so the reading
 *    survives colour-vision deficiency and a monochrome screenshot (rule 6).
 *
 * If a future dataset uses `active` to mean something bad, this is wrong — and
 * that is why the vocabulary is a visible list in one place rather than a
 * regex spread across the grid.
 */

import { cn } from '@/shared/lib/utils';
import { middleTruncate } from '@/shared/components/instrument/shape';

type Tone = 'good' | 'warning' | 'critical' | 'neutral';

const STATUS_WORDS: Record<string, Tone> = {
  active: 'good',
  passed: 'good',
  pass: 'good',
  ok: 'good',
  success: 'good',
  succeeded: 'good',
  completed: 'good',
  ready: 'good',
  valid: 'good',
  enabled: 'good',
  pending: 'warning',
  trialing: 'warning',
  trial: 'warning',
  running: 'warning',
  queued: 'warning',
  warning: 'warning',
  partial: 'warning',
  review: 'warning',
  churned: 'critical',
  failed: 'critical',
  fail: 'critical',
  error: 'critical',
  rejected: 'critical',
  cancelled: 'critical',
  canceled: 'critical',
  expired: 'critical',
  invalid: 'critical',
  disabled: 'neutral',
  inactive: 'neutral',
  unknown: 'neutral',
};

const SHAPE: Record<Tone, string> = {
  good: 'rounded-full bg-[var(--st-good)]',
  warning: '[clip-path:polygon(50%_0%,100%_100%,0%_100%)] bg-[var(--st-warn)]',
  critical: 'rounded-[1px] bg-[var(--st-crit)]',
  neutral: 'rounded-full bg-[var(--m4)]',
};

/**
 * A column is treated as categorical when its profile says so. Without a
 * profile nothing is enumerated, so nothing is marked — which is the honest
 * default, since profiling is not automatic on upload.
 */
export function CellValue({
  value,
  categorical,
  masked,
}: {
  value: unknown;
  categorical: boolean;
  masked: boolean;
}) {
  if (value === null || value === undefined) {
    return <span className="text-muted-foreground/50">—</span>;
  }

  const text = String(value);

  if (masked) {
    // A recessed well, matching INSTRUMENT's material treatment for masked PII.
    return (
      <span className="rounded bg-[var(--s0)] px-1 font-mono text-micro text-muted-foreground/70 shadow-[inset_0_1px_2px_rgba(0,0,0,.4)]">
        {text}
      </span>
    );
  }

  if (typeof value === 'number') {
    return <>{text}</>;
  }

  if (categorical && text.length <= 24) {
    const tone = STATUS_WORDS[text.trim().toLowerCase()] ?? 'neutral';
    return (
      <span className="inline-flex items-center gap-1.5">
        <span aria-hidden="true" className={cn('size-[6px] shrink-0', SHAPE[tone])} />
        <span className="text-foreground">{text}</span>
      </span>
    );
  }

  return <>{middleTruncate(text, 44)}</>;
}
