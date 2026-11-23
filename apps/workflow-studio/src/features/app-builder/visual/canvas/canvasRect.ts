/**
 * Where the preview draws the selection (the runtime's `vrect`), kept outside React state so a scroll re-renders only what
 * floats over the canvas (the toolbar, the handles), not the whole preview panel. Plus the pure placement of a floating
 * bar over it.
 */
import { create } from 'zustand'
import type { CanvasRect, Rect } from '../../sandbox/bridge'

export const useCanvasRect = create<{ rect: CanvasRect | null }>(() => ({ rect: null }))

export function setCanvasRect(rect: CanvasRect | null): void {
  useCanvasRect.setState({ rect })
}

/** The toolbar's handles toggle (padding / gap / size handles on the selection): on by default. */
export const useHandlesOn = create<{ on: boolean; toggle: () => void }>((set) => ({ on: true, toggle: () => set((s) => ({ on: !s.on })) }))

/** The rect is for the current selection and the preview is idle (not dragging, not editing text in place). */
export function rectFor(r: CanvasRect | null, vsrc: string | null | undefined, vpart: string | null | undefined): Rect | null {
  if (!r || !r.rect || !vsrc || r.state !== 'idle') return null
  if (r.vsrc !== vsrc || (r.vpart ?? null) !== (vpart ?? null)) return null
  return r.rect
}

export interface Placement {
  left: number
  top: number
  side: 'above' | 'below' | 'inside'
}

const MARGIN = 4

/**
 * Place a `bar`-sized box over element `el` (iframe viewport coordinates). `frame` is the iframe's box in the layer the bar
 * is drawn in and `layer` that layer's size. Above the element when the visible frame has room, else below, else pinned
 * inside its top edge; left-aligned with it, kept inside the layer. Null when the element is scrolled out of view.
 */
export function placeBar(el: Rect, frame: Rect, layer: { w: number; h: number }, bar: { w: number; h: number }, gap = 6): Placement | null {
  const top = frame.y + el.y
  const bottom = top + el.h
  const visTop = Math.max(frame.y, 0)
  const visBottom = Math.min(frame.y + frame.h, layer.h)
  if (bottom < visTop || top > visBottom || el.w <= 0) return null
  const left = Math.max(MARGIN, Math.min(frame.x + el.x, layer.w - bar.w - MARGIN))
  if (top - gap - bar.h >= visTop) return { left, top: top - gap - bar.h, side: 'above' }
  if (bottom + gap + bar.h <= visBottom) return { left, top: bottom + gap, side: 'below' }
  return { left, top: Math.max(visTop + MARGIN, Math.min(top + gap, visBottom - bar.h - MARGIN)), side: 'inside' }
}
