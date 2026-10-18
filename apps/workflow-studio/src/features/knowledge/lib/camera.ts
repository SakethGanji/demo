/** Camera maths for the graph tile (PLAN-CAPGRAPH-UI §2.2 "Focus"). Pure. */
import type { Rect, Viewport } from '@/features/workflow-editor/components/canvas/stageGeometry'

export const SHEET_W = 380
export const SHEET_GAP = 12

/** The part of the tile that is free of the docked sheet (screen px, tile-local). */
export function freeRect(size: { width: number; height: number }, sheetOpen: boolean): Rect {
  const reserved = sheetOpen ? SHEET_W + SHEET_GAP * 2 : 0
  return { x: 0, y: 0, w: Math.max(1, size.width - reserved), h: Math.max(1, size.height) }
}

/** The viewport that centres `bounds` (flow px) in `free` (screen px), at the largest zoom that fits, within [minZoom, maxZoom]. */
export function egoViewport(bounds: Rect, free: Rect, opts: { pad?: number; minZoom?: number; maxZoom?: number } = {}): Viewport {
  const pad = opts.pad ?? 40
  const minZoom = opts.minZoom ?? 0.2
  const maxZoom = opts.maxZoom ?? 1.1
  const availW = Math.max(1, free.w - pad * 2)
  const availH = Math.max(1, free.h - pad * 2)
  const fit = Math.min(availW / Math.max(1, bounds.w), availH / Math.max(1, bounds.h))
  const zoom = Math.min(maxZoom, Math.max(minZoom, fit))
  return {
    zoom,
    x: free.x + (free.w - bounds.w * zoom) / 2 - bounds.x * zoom,
    y: free.y + (free.h - bounds.h * zoom) / 2 - bounds.y * zoom,
  }
}

/** Bounding box of layout boxes. */
export function boundsOfBoxes(boxes: Iterable<Rect>): Rect | null {
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity
  for (const b of boxes) {
    x0 = Math.min(x0, b.x); y0 = Math.min(y0, b.y)
    x1 = Math.max(x1, b.x + b.w); y1 = Math.max(y1, b.y + b.h)
  }
  return Number.isFinite(x0) ? { x: x0, y: y0, w: x1 - x0, h: y1 - y0 } : null
}
