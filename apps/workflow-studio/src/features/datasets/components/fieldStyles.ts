/**
 * Field styles for the datasets surface.
 *
 * One definition each, because there were nine. `fieldClass` existed as three
 * byte-identical `inputClass` constants (overview, quality, versions lenses)
 * and `controlClass` as one named `selectClass` plus six hand-copied inline
 * twins across the catalog, the rail, the upload dialog and the seat switcher.
 * Nine copies is nine places to miss when the focus ring or the surface step
 * changes — which is exactly what a re-theme does.
 *
 * These are raw-element styles rather than the `ui/input` and `ui/select`
 * components on purpose: these are dense, in-panel controls at 24 and 28px,
 * well below what those components are built for. If that changes, this is the
 * one file to delete.
 *
 * The recessed well (`--s0` plus an inset shadow) is INSTRUMENT's material
 * treatment for inputs — an input is somewhere you put something, so it reads
 * as carved in rather than raised.
 */

const FIELD_BASE =
  'w-full rounded bg-[var(--s0)] shadow-[inset_0_1px_2px_rgba(0,0,0,.35)] outline-none ring-1 ring-border focus-visible:ring-2 focus-visible:ring-ring';

/** Dense in-lens field — 24px. */
export const fieldClass = `h-6 px-1.5 text-small ${FIELD_BASE}`;

/** Toolbar-scale control — 28px. Selects, search boxes, filter dropdowns. */
export const controlClass = `h-7 px-2 text-body ${FIELD_BASE}`;
