/**
 * Shared furniture for the lens panels.
 *
 * This file is deliberately only the pieces every lens uses. The chart marks
 * that used to live here moved to `analyticsMarks.tsx`: they had exactly one
 * consumer between them, so 140 of this file's 239 lines were a private
 * module sitting in a file named "shared furniture", and the name stopped
 * telling the truth about what belonged here.
 */

import type { ReactNode } from 'react';
import { cn } from '@/shared/lib/utils';
import { errorText } from '@/shared/lib/analyticsClient';

/**
 * A titled block within a lens.
 *
 * The title is SENTENCE CASE. It used to be an uppercase tracked micro-label,
 * which INSTRUMENT rule 5 names specifically as the loudest generated-looking
 * tell — and because every lens routes its headings through here, that one
 * declaration was reproducing the tell about forty times across the dock.
 *
 * The uppercase eyebrow survives in exactly one place, `instrument/Typography`'s
 * `Eyebrow`, and only in its documented position: 9.5px, tracked, recessive,
 * directly above a large figure. At that size it reads as a unit label on an
 * instrument. At 10.5px above a paragraph it reads as shouting.
 */
export function Section({ title, action, children }: { title: string; action?: ReactNode; children: ReactNode }) {
  return (
    <div className="mb-4">
      <div className="mb-1.5 flex items-center justify-between gap-2">
        <h3 className="text-label font-medium text-foreground">{title}</h3>
        {action}
      </div>
      {children}
    </div>
  );
}

export function Row({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-1">
      <span className="text-small text-muted-foreground">{label}</span>
      <span className="text-right text-body">{value}</span>
    </div>
  );
}

export function LensLoading({ children = 'Loading…' }: { children?: ReactNode }) {
  return <p className="text-small text-muted-foreground">{children}</p>;
}

export function LensEmpty({ children }: { children: ReactNode }) {
  return (
    <p className="rounded-md border border-dashed border-border px-3 py-4 text-center text-small text-muted-foreground">
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
    <div className="rounded-md bg-muted/40 px-3 py-3">
      <p className="text-body font-medium">Restricted for this seat</p>
      <p className="mt-1 text-small text-muted-foreground">
        {what} is refused on datasets that declare sensitive columns unless you are an admin,
        owner or superuser. Masking is not enough here — a computed result could reconstruct the
        values it hides.
      </p>
    </div>
  );
}

export function LensError({ children }: { children: ReactNode }) {
  return (
    <p className="rounded-md bg-destructive/10 px-3 py-2 text-small text-destructive">
      {children}
    </p>
  );
}

/**
 * A list with its loading, error and empty states — as ONE exhaustive decision.
 *
 * Every lens used to spell this out inline, and the four copies had already
 * drifted: the transform lens omitted the `!error` guard on its empty branch,
 * so a failed fetch rendered the error box AND "No saved transformations for
 * this dataset" together. An error that also claims the list is empty is worse
 * than either alone — it invites the reader to believe the second sentence.
 *
 * The early returns are the fix. There is no arrangement of props that renders
 * two states, because only one `return` can run.
 */
export function LensList<T>({
  query,
  items,
  empty,
  loading,
  children,
}: {
  query: { isLoading: boolean; error: unknown };
  items: readonly T[];
  empty: ReactNode;
  /** Override the loading copy, e.g. "Profiling…". */
  loading?: ReactNode;
  children: (item: T, index: number) => ReactNode;
}) {
  if (query.isLoading) return <LensLoading>{loading}</LensLoading>;
  if (query.error) return <LensError>{errorText(query.error)}</LensError>;
  if (items.length === 0) return <LensEmpty>{empty}</LensEmpty>;
  return <>{items.map(children)}</>;
}

/** A dtype chip. Neutral by design — dtype is a fact, not a status. */
export function DtypeChip({ dtype, className }: { dtype?: string | null; className?: string }) {
  return (
    <span
      className={cn(
        'shrink-0 rounded bg-muted px-1 font-mono text-footnote text-muted-foreground',
        className,
      )}
    >
      {dtype ?? '?'}
    </span>
  );
}
