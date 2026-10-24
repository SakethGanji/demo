/**
 * Which breakpoint a style edit lands on, for the device the preview shows (like Webflow, but on Tailwind's mobile-first
 * cascade, where an unprefixed class applies at every size and a prefixed one from its min-width up).
 *
 * Mapping, against Tailwind's default breakpoints and the preview's DEVICE_WIDTH (AppPreviewPanel):
 * - phone   (390px, below sm 640px)       → base, no prefix: "All sizes" (it cascades up unless a larger size overrides)
 * - tablet  (768px, md 768px..lg 1024px)  → `md:`: "Tablet and up"
 * - desktop (the full pane, lg ≥ 1024px)  → `lg:` when the property already differs by size, or when "Desktop only" is
 *   on; otherwise the base class, so a desktop edit on a non-responsive element stays today's all-sizes edit (and an
 *   element with no class for the property gets the base class).
 *
 * Enum props of kit components can't take prefixes: only Grid's `cols` is responsive (`colsMd`, `colsLg`); the rest are
 * edited at base only and refused, with a reason, at tablet / desktop-only.
 */
import type { AnyBreakpoint, Breakpoint } from './classEdit'
import type { Device } from './deviceStore'

/** The breakpoint in effect at each device's preview width (what the user is looking at). */
export const DEVICE_BP: Record<Device, Breakpoint> = { phone: 'base', tablet: 'md', desktop: 'lg' }

/** Where an edit goes, given whether the property already differs by size (`responsive`). */
export function writeBreakpoint(device: Device, desktopOnly: boolean, responsive: boolean): Breakpoint {
  if (device === 'phone') return 'base'
  if (device === 'tablet') return 'md'
  return desktopOnly || responsive ? 'lg' : 'base'
}

/** "Editing: …" for the panel header. */
export function editingLabel(device: Device, desktopOnly: boolean): string {
  if (device === 'phone') return 'All sizes'
  if (device === 'tablet') return 'Tablet and up'
  return desktopOnly ? 'Desktop only' : 'Desktop (all sizes unless a value already differs by size)'
}

/** A breakpoint by the device the user knows it as (for "inherited from …"). */
export const BP_LABEL: Record<AnyBreakpoint, string> = { base: 'all sizes', sm: 'large phones', md: 'tablet', lg: 'desktop', xl: 'wide desktop', '2xl': 'wide desktop' }

/** Kit enum props that have a per-breakpoint twin: the prop written at each breakpoint. */
export const RESPONSIVE_PROPS: Record<string, Record<Breakpoint, string>> = {
  cols: { base: 'cols', md: 'colsMd', lg: 'colsLg' },
}

/** The twins, hidden as their own controls (the base prop's control edits whichever the breakpoint needs). */
export const RESPONSIVE_TWINS: ReadonlySet<string> = new Set(Object.values(RESPONSIVE_PROPS).flatMap((m) => [m.md, m.lg]))
