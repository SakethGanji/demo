/**
 * The canvas handles' pure half (Figma / Webflow-style direct manipulation): where padding, gap and size handles sit, what
 * a drag snaps to, and the class or design-prop write each snap stands for. Every value is a theme token — Tailwind's
 * spacing steps (×4px), the width fractions, the max-w scale or the height steps — never an arbitrary px. Writes are one
 * class-string splice (`classSplice`) or one enum-prop op, made by the component on mouse-up.
 */
import type * as t from '@babel/types'
import type { BoxMetrics, CanvasRect, Rect } from '../../sandbox/bridge'
import type { ElementView, PropView } from '../editor'
import { applyChecked, removeAttr, setAttr, type TextEdit } from '../splice'
import { clearGroup, readClasses, setClass, type ClassChange } from '../style/classEdit'
import { ALLOWED, controlById, SPACE } from '../style/tokens'
import { quickItems } from './quickStyle'

export type Side = 'top' | 'right' | 'bottom' | 'left'
export const SIDES: Side[] = ['top', 'right', 'bottom', 'left']
const SIDE_INDEX: Record<Side, 0 | 1 | 2 | 3> = { top: 0, right: 1, bottom: 2, left: 3 }

// ── Spacing steps ────────────────────────────────────────────────────────────

/** One Tailwind spacing step is 0.25rem: 4px at the default root size. */
export const stepPx = (step: string): number => parseFloat(step) * 4

/** The step nearest `px` (ties go to the smaller one). */
export function snapStep(px: number, steps: readonly string[]): string {
  let best = steps[0]
  for (const s of steps) if (Math.abs(stepPx(s) - px) < Math.abs(stepPx(best) - px)) best = s
  return best
}

/**
 * Single-side padding (`pt-4`…): the style panel's token lock (tokens.ts ALLOWED) has p / px / py only, so the handles
 * accept these on the same SPACE scale. Nothing outside this set or ALLOWED is ever written.
 */
const SIDE_PREFIX: Record<Side, string> = { top: 'pt', right: 'pr', bottom: 'pb', left: 'pl' }
const HANDLE_ALLOWED: ReadonlySet<string> = new Set(Object.values(SIDE_PREFIX).flatMap((p) => SPACE.map((s) => `${p}-${s}`)))
const allowed = (cls: string) => ALLOWED.has(cls) || HANDLE_ALLOWED.has(cls)

/** Which sides a padding drag changes: one, the opposite pair (Alt) or all four (Shift). */
export type PadMode = 'one' | 'pair' | 'all'

export function padSides(side: Side, mode: PadMode): Side[] {
  if (mode === 'all') return SIDES
  if (mode === 'pair') return side === 'top' || side === 'bottom' ? ['top', 'bottom'] : ['left', 'right']
  return [side]
}

/** The padding class a snapped drag writes. */
export function padClass(side: Side, mode: PadMode, step: string): string {
  if (mode === 'all') return `p-${step}`
  if (mode === 'pair') return `${side === 'top' || side === 'bottom' ? 'py' : 'px'}-${step}`
  return `${SIDE_PREFIX[side]}-${step}`
}

/** A padding drag's new px for `side` from the pointer delta: dragging an edge into the box grows that side's padding. */
export function padDragPx(side: Side, start: number, dx: number, dy: number): number {
  const d = side === 'top' ? dy : side === 'bottom' ? -dy : side === 'left' ? dx : -dx
  return Math.max(0, start + d)
}

export function padOf(box: BoxMetrics, side: Side): number {
  return box.padding[SIDE_INDEX[side]]
}

// ── Geometry (iframe viewport coordinates) ───────────────────────────────────

/** The four padding areas inside the border. */
export function padAreas(r: Rect, box: BoxMetrics): Record<Side, Rect> {
  const [bt, br, bb, bl] = box.border
  const [pt, pr, pb, pl] = box.padding
  const x = r.x + bl
  const y = r.y + bt
  const w = Math.max(0, r.w - bl - br)
  const h = Math.max(0, r.h - bt - bb)
  return {
    top: { x, y, w, h: pt },
    bottom: { x, y: y + h - pb, w, h: pb },
    left: { x, y: y + pt, w: pl, h: Math.max(0, h - pt - pb) },
    right: { x: x + w - pr, y: y + pt, w: pr, h: Math.max(0, h - pt - pb) },
  }
}

/** The margin areas outside the box (read-only overlay). */
export function marginAreas(r: Rect, box: BoxMetrics): Rect[] {
  const [mt, mr, mb, ml] = box.margin.map((m) => Math.max(0, m))
  return [
    { x: r.x - ml, y: r.y - mt, w: r.w + ml + mr, h: mt },
    { x: r.x - ml, y: r.y + r.h, w: r.w + ml + mr, h: mb },
    { x: r.x - ml, y: r.y, w: ml, h: r.h },
    { x: r.x + r.w, y: r.y, w: mr, h: r.h },
  ].filter((a) => a.w > 0 && a.h > 0)
}

/** Where a padding pill sits: on the padding's inner edge, at least `inset` px inside the outer edge (clear of the size handles). */
export function padHandleAt(r: Rect, box: BoxMetrics, side: Side, inset = 7): { x: number; y: number; vertical: boolean } {
  const [bt, br, bb, bl] = box.border
  const [pt, pr, pb, pl] = box.padding
  const cx = r.x + r.w / 2
  const cy = r.y + r.h / 2
  if (side === 'top') return { x: cx, y: r.y + Math.max(inset, bt + pt), vertical: false }
  if (side === 'bottom') return { x: cx, y: r.y + r.h - Math.max(inset, bb + pb), vertical: false }
  if (side === 'left') return { x: r.x + Math.max(inset, bl + pl), y: cy, vertical: true }
  return { x: r.x + r.w - Math.max(inset, br + pr), y: cy, vertical: true }
}

/**
 * Whether a side's padding pill has room: on a short or narrow element (a heading, a button) the pill would sit over the
 * content and swallow the double-click that edits its text, so it's left out there (quick style and the panel still edit
 * padding).
 */
export function padHandleFits(r: Rect, side: Side): boolean {
  return side === 'top' || side === 'bottom' ? r.h >= 44 : r.w >= 72
}

/** A bar between two neighbouring children, and the pointer axis that changes the gap there. */
export interface GapBar extends Rect {
  axis: 'x' | 'y'
}

const MIN_BAR = 6

/**
 * The gaps between consecutive children: side by side → a vertical bar dragged along x; one under the other (a column, or a
 * grid's next row) → a horizontal bar dragged along y. A zero gap still gets a bar wide enough to grab.
 */
export function gapBars(kids: Rect[], content: Rect): GapBar[] {
  const out: GapBar[] = []
  for (let i = 0; i + 1 < kids.length; i++) {
    const a = kids[i]
    const b = kids[i + 1]
    if (b.x >= a.x + a.w - 1 && b.y < a.y + a.h && a.y < b.y + b.h) {
      const top = Math.min(a.y, b.y)
      const w = Math.max(0, b.x - (a.x + a.w))
      const mid = a.x + a.w + w / 2
      out.push({ axis: 'x', x: mid - Math.max(w, MIN_BAR) / 2, y: top, w: Math.max(w, MIN_BAR), h: Math.max(a.y + a.h, b.y + b.h) - top })
    } else if (b.y >= a.y + a.h - 1) {
      const h = Math.max(0, b.y - (a.y + a.h))
      const mid = a.y + a.h + h / 2
      // a grid row break spans the container; a column's gap spans the two children
      const row = b.x < a.x
      const x = row ? content.x : Math.min(a.x, b.x)
      const w = row ? content.w : Math.max(a.x + a.w, b.x + b.w) - x
      out.push({ axis: 'y', x, y: mid - Math.max(h, MIN_BAR) / 2, w, h: Math.max(h, MIN_BAR) })
    }
  }
  // grid rows repeat the same bar per column pair; one per position is enough
  return out.filter((g, i) => out.findIndex((o) => o.axis === g.axis && Math.abs(o.x - g.x) < 1 && Math.abs(o.y - g.y) < 1) === i)
}

/** The gap the bar's axis shows: column gap between side-by-side children, row gap between stacked ones. */
export function gapOf(box: BoxMetrics, axis: 'x' | 'y'): number {
  return axis === 'x' ? box.gap[1] : box.gap[0]
}

// ── Size ─────────────────────────────────────────────────────────────────────

export type Axis = 'w' | 'h'

/** A size a width / height drag can snap to: the class changes that make it, and the px it comes to here. */
export interface SizeOption {
  label: string
  px: number
  changes: ClassChange[]
}

const optionsOf = (id: string) => controlById(id)?.options.map((o) => o.cls) ?? []
const W_ALL = optionsOf('w')
const H_ALL = optionsOf('h')
const MAXW_ALL = optionsOf('maxw')
const MAXW_PX: Record<string, number> = { xs: 320, sm: 384, md: 448, lg: 512, xl: 576, '2xl': 672, '3xl': 768, '4xl': 896, '5xl': 1024, '6xl': 1152, '7xl': 1280 }
const FRACTIONS: [string, number][] = [['1/4', 0.25], ['1/3', 1 / 3], ['1/2', 0.5], ['2/3', 2 / 3], ['3/4', 0.75], ['full', 1]]

/** The width tokens a drag snaps to (auto and fit at their measured size, fractions and max-w-* against the parent). */
export function widthOptions(box: BoxMetrics): SizeOption[] {
  const pw = box.parentContent?.w ?? 0
  const nat = box.natural
  const clearMax: ClassChange = { clear: MAXW_ALL }
  const out: SizeOption[] = []
  if (nat) {
    out.push({ label: 'auto', px: nat.autoW, changes: [{ clear: W_ALL }, clearMax] })
    out.push({ label: 'w-fit', px: nat.fitW, changes: [{ set: 'w-fit' }, clearMax] })
  }
  if (pw > 0) {
    for (const [f, k] of FRACTIONS) if (W_ALL.includes(`w-${f}`)) out.push({ label: `w-${f}`, px: pw * k, changes: [{ set: `w-${f}` }, clearMax] })
    for (const [s, v] of Object.entries(MAXW_PX)) {
      if (v < pw && MAXW_ALL.includes(`max-w-${s}`)) out.push({ label: `max-w-${s}`, px: v, changes: [{ set: 'w-full' }, { set: `max-w-${s}` }] })
    }
  }
  return out
}

/** The height tokens a drag snaps to. */
export function heightOptions(box: BoxMetrics): SizeOption[] {
  const out: SizeOption[] = []
  if (box.natural) out.push({ label: 'auto', px: box.natural.autoH, changes: [{ clear: H_ALL }] })
  const ph = box.parentContent?.h ?? 0
  if (ph > 0) out.push({ label: 'h-full', px: ph, changes: [{ set: 'h-full' }] })
  for (const c of H_ALL) {
    const m = /^h-(\d+)$/.exec(c)
    if (m) out.push({ label: c, px: Number(m[1]) * 4, changes: [{ set: c }] })
  }
  return out
}

/** The option nearest `px` (the first wins a tie, so auto beats an equal fraction). */
export function snapSize(px: number, options: SizeOption[]): SizeOption | null {
  let best: SizeOption | null = null
  for (const o of options) if (!best || Math.abs(o.px - px) < Math.abs(best.px - px)) best = o
  return best
}

/** Why a width / height class can't size this element here (its parent decides), or null. */
export function sizeRefusal(axis: Axis, box: BoxMetrics): string | null {
  const what = axis === 'w' ? 'width' : 'height'
  if (box.display === 'inline') return `An inline element ignores its ${what}; make it a block first (Display in the style panel)`
  if (box.display === 'contents' || box.display === 'none') return `This element draws no box of its own, so it has no ${what}`
  if (/flex/.test(box.parentDisplay ?? '') && (box.grow ?? 0) > 0) {
    const row = (box.parentDirection ?? 'row').startsWith('row')
    if ((axis === 'w') === row) return `It grows to fill its ${row ? 'row' : 'column'} (flex-1 / grow), so its ${what} comes from the parent; turn growing off to size it`
  }
  return null
}

// ── Measuring (Alt + hover) ──────────────────────────────────────────────────

export interface Guide {
  x1: number
  y1: number
  x2: number
  y2: number
  px: number
}

const contains = (o: Rect, i: Rect) => i.x >= o.x && i.y >= o.y && i.x + i.w <= o.x + o.w && i.y + i.h <= o.y + o.h

/**
 * The distance lines between selection `a` and hovered `b`, as Figma draws them: from each side of the inner box to the
 * outer one when one holds the other; otherwise the horizontal and / or vertical gap between them.
 */
export function measure(a: Rect, b: Rect): Guide[] {
  const out: Guide[] = []
  const push = (x1: number, y1: number, x2: number, y2: number) => {
    const px = Math.round(Math.abs(x2 - x1) + Math.abs(y2 - y1))
    if (px > 0) out.push({ x1, y1, x2, y2, px })
  }
  const nested = contains(b, a) ? { i: a, o: b } : contains(a, b) ? { i: b, o: a } : null
  if (nested) {
    const { i, o } = nested
    const cx = i.x + i.w / 2
    const cy = i.y + i.h / 2
    push(cx, o.y, cx, i.y)
    push(cx, i.y + i.h, cx, o.y + o.h)
    push(o.x, cy, i.x, cy)
    push(i.x + i.w, cy, o.x + o.w, cy)
    return out
  }
  // the line runs through the middle of the overlap on the other axis, or through the selection's centre
  const oy0 = Math.max(a.y, b.y)
  const oy1 = Math.min(a.y + a.h, b.y + b.h)
  const y = oy1 > oy0 ? (oy0 + oy1) / 2 : a.y + a.h / 2
  const ox0 = Math.max(a.x, b.x)
  const ox1 = Math.min(a.x + a.w, b.x + b.w)
  const x = ox1 > ox0 ? (ox0 + ox1) / 2 : a.x + a.w / 2
  if (a.x + a.w <= b.x) push(a.x + a.w, y, b.x, y)
  else if (b.x + b.w <= a.x) push(b.x + b.w, y, a.x, y)
  if (a.y + a.h <= b.y) push(x, a.y + a.h, x, b.y)
  else if (b.y + b.h <= a.y) push(x, b.y + b.h, x, a.y)
  return out
}

// ── Writes ───────────────────────────────────────────────────────────────────

/** How the selection takes a padding / gap edit: its enum design prop (Box / Stack / Grid), its classes, or a refusal. */
export type SpaceWrite = { kind: 'prop'; prop: PropView; steps: readonly string[] } | { kind: 'class' } | { kind: 'refused'; reason: string }

export function spaceWrite(view: ElementView, control: 'p' | 'gap', classes: { ok: true } | { ok: false; reason: string } | null): SpaceWrite {
  const [item] = quickItems(view, [control], !!classes?.ok)
  if (item?.kind === 'prop' && item.prop.editor?.kind === 'enum') {
    const steps = item.prop.editor.options.filter((o) => isFinite(parseFloat(o)))
    if (steps.length) return { kind: 'prop', prop: item.prop, steps }
    return { kind: 'refused', reason: `${view.name}'s ${item.prop.name} takes named sizes, not spacing steps; change it in the panel` }
  }
  if (item?.kind === 'class') return { kind: 'class' }
  const covering = view.props.find((p) => p.name === (control === 'p' ? 'padding' : 'gap') && p.editor)
  if (covering) return { kind: 'refused', reason: `${view.name}'s ${covering.name} is set from code (${covering.src ?? 'an expression'}); change it in the panel or with AI` }
  if (classes && !classes.ok) return { kind: 'refused', reason: classes.reason }
  return { kind: 'refused', reason: `${view.name} doesn't take classes; style it through its props or with AI` }
}

export type SpliceResult = { ok: true; after: string; add: string[]; remove: string[]; label: string } | { ok: false; error: string }

/**
 * Apply class changes to the element as one splice of its class string (the same sites classEdit handles). Every class
 * written must be a theme token (the token lock plus single-side padding); "Nothing to change" when the list stays the same.
 */
export function classSplice(src: string, el: t.JSXElement, changes: ClassChange[]): SpliceResult {
  const r = readClasses(src, el)
  if (!r.ok) return { ok: false, error: r.reason }
  let next = r.classes
  for (const c of changes) {
    if ('set' in c) {
      if (!allowed(c.set)) return { ok: false, error: `${c.set} is not a theme token value` }
      next = setClass(next, c.set)
    } else next = clearGroup(next, c.clear)
  }
  if (next.join(' ') === r.classes.join(' ')) return { ok: false, error: 'Nothing to change' }
  const text = next.join(' ')
  const site = r.site
  let edit: TextEdit | null
  if (site.kind === 'none') edit = setAttr(src, el, 'className', `"${text}"`)
  else if (site.kind === 'attr' && !text) edit = removeAttr(src, el, 'className')
  else edit = { start: site.start, end: site.end, text }
  if (!edit) return { ok: false, error: 'Nothing to change' }
  const after = applyChecked(src, [edit])
  if (after === null) return { ok: false, error: 'That edit would break the file; use AI or code' }
  const add = next.filter((c) => !r.classes.includes(c))
  const remove = r.classes.filter((c) => !next.includes(c))
  return { ok: true, after, add, remove, label: add.length ? add.join(' ') : `remove ${remove.join(' ')}` }
}

/** The handles to draw: exactly one rendered node selected and the preview idle. */
export function handlesShown(report: CanvasRect | null): report is CanvasRect & { rect: Rect; box: BoxMetrics } {
  return !!report && report.state === 'idle' && !!report.rect && !!report.box
}
