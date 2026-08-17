import { ChevronLeft, ChevronRight } from 'lucide-react';

/**
 * A single step of a pager.
 *
 * `direction` rather than `children`, because there are exactly two of these
 * and letting a caller supply the glyph is how the four copies of this button
 * drifted in the first place.
 *
 * Deliberately a STEP, not a page number. Row paging is cursor-based: the
 * response carries an opaque `next_cursor` and never an offset, so a numbered
 * pager cannot be built on this API — there is no way to ask for "page 2,329"
 * without having walked there. `total` IS returned, so a range like
 * "1–18 of 41,908" is honest; a jump target would not be. The catalog can show
 * `n / m` because it holds its whole (capped) result set client-side, but even
 * there the control is two steps, not a list of numbers.
 *
 * One design prototype (`terminal-adaptive.html`) draws a numbered pager with
 * a jump to page 54,767. That one is wrong, and `terminal.html` says so in the
 * markup — do not port it.
 */
export function PagerButton({
  direction,
  onClick,
  disabled,
  testid,
}: {
  direction: 'prev' | 'next';
  onClick: () => void;
  disabled?: boolean;
  testid?: string;
}) {
  const Icon = direction === 'prev' ? ChevronLeft : ChevronRight;
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      aria-label={direction === 'prev' ? 'Previous page' : 'Next page'}
      data-testid={testid}
      className="flex size-6 items-center justify-center rounded-md bg-secondary text-foreground transition-colors hover:bg-accent disabled:opacity-40"
    >
      <Icon className="size-3" />
    </button>
  );
}
