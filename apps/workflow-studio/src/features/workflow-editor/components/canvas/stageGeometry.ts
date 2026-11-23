/**
 * Pure helpers for the AI stage on the editor canvas (PLAN-MOTION-UI Builder B): camera padding that clears the
 * floating right panel, which nodes the camera frames, and small timing maths the node layers share.
 */

export const CAMERA_MARGIN = 48

/** Matches `min(384px, 50vw - 24px) + 24px` in WorkflowEditorPage: used only when the panel cannot be measured. */
export function fallbackPanelInset(viewportWidth: number): number {
  return Math.min(384, viewportWidth / 2 - 24) + 24
}

/**
 * How many canvas pixels, from its right edge, the right panel covers. `panelLeft` is the measured left edge of the
 * panel (null = closed or not found). Never negative, never more than the canvas.
 */
export function rightInset(canvasLeft: number, canvasRight: number, panelLeft: number | null): number {
  if (panelLeft === null || !Number.isFinite(panelLeft)) return 0
  return Math.max(0, Math.min(canvasRight - canvasLeft, canvasRight - panelLeft))
}

export interface SidePadding { top: string; left: string; bottom: string; right: string }

/** Per-side `fitView` padding (React Flow 12.10 accepts px strings per side). */
export function cameraPadding(inset: number, margin = CAMERA_MARGIN): SidePadding {
  return { top: `${margin}px`, left: `${margin}px`, bottom: `${margin}px`, right: `${Math.round(inset) + margin}px` }
}

/** The touched nodes plus their direct neighbours, so the camera frames the change in its context. */
export function withNeighbours(ids: readonly string[], edges: readonly { source: string; target: string }[]): string[] {
  const out = new Set(ids)
  const touched = new Set(ids)
  for (const e of edges) {
    if (touched.has(e.source)) out.add(e.target)
    if (touched.has(e.target)) out.add(e.source)
  }
  return [...out]
}

/** First `progress` (0..1) of `text`, whole characters. */
export function typedSlice(text: string, progress: number): string {
  const p = Math.min(1, Math.max(0, progress))
  return text.slice(0, Math.round(p * text.length))
}

/** Seconds a node waits in the left-to-right settle sweep: .03 s per 140 flow px, capped so it stays inside `settle`. */
export function settleDelay(x: number, minX: number, cap = 0.5): number {
  return Math.min(cap, Math.max(0, ((x - minX) / 140) * 0.03))
}

/** Index of the hop source -> target along a test path (node names, run order), or -1 when the edge is not on it. */
export function testHopIndex(path: readonly string[], source: string, target: string): number {
  for (let i = 0; i < path.length - 1; i++) if (path[i] === source && path[i + 1] === target) return i
  return -1
}

/** Split a camera/follow decision out so it can be tested: the camera only moves while the stage follows. */
export function mayMoveCamera(state: { active: boolean; follow: boolean }): boolean {
  return state.active && state.follow
}


export interface Rect { x: number; y: number; w: number; h: number }
export interface Viewport { x: number; y: number; zoom: number }

/** Flow-space box a node occupies when it has not been measured yet: a 64 px tile with its label under it. */
export const NODE_BOX = { w: 130, h: 96 }
/** The AI marker's pill sits above a tile's top-right corner; the frame leaves room for it. */
export const PRESENCE_HEADROOM = 40

/**
 * The box that holds every point (a node's top-left) with room for its tile, label and the AI marker above it.
 * `size` is the node's measured size when known. Null when there is nothing to frame.
 */
export function boundsOf(items: readonly { x: number; y: number; w?: number; h?: number }[]): Rect | null {
  if (!items.length) return null
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity
  for (const it of items) {
    const w = it.w ?? NODE_BOX.w
    const h = it.h ?? NODE_BOX.h
    x0 = Math.min(x0, it.x); y0 = Math.min(y0, it.y - PRESENCE_HEADROOM)
    x1 = Math.max(x1, it.x + w); y1 = Math.max(y1, it.y + h)
  }
  return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 }
}

export interface ViewportOptions {
  /** canvas px the right panel covers */
  inset: number
  /** the most it may zoom in (a build never zooms past 1) */
  maxZoom?: number
  /** the least it may zoom out; below this the graph would be unreadable, but the graph is never cut off */
  minZoom?: number
  margin?: { top: number; left: number; bottom: number; right: number }
}

/** Camera margins in canvas px: the top clears the stage pill, the bottom the toolbar. */
export const CAMERA_FRAME_MARGIN = { top: 64, left: 40, bottom: 72, right: 40 }

/**
 * The viewport that centres `bounds` in the part of the canvas the panel leaves free and fits all of it (so no node is
 * ever under the panel). Zoom is whatever fits, capped at `maxZoom`; `minZoom` is only a floor against absurd bounds.
 */
export function viewportForBounds(bounds: Rect, size: { width: number; height: number }, opts: ViewportOptions): Viewport {
  const m = opts.margin ?? CAMERA_FRAME_MARGIN
  const maxZoom = opts.maxZoom ?? 1
  const minZoom = opts.minZoom ?? 0.25
  const availW = Math.max(1, size.width - opts.inset - m.left - m.right)
  const availH = Math.max(1, size.height - m.top - m.bottom)
  const fit = Math.min(availW / Math.max(1, bounds.w), availH / Math.max(1, bounds.h))
  const zoom = Math.min(maxZoom, Math.max(minZoom, fit))
  return {
    zoom,
    x: m.left + (availW - bounds.w * zoom) / 2 - bounds.x * zoom,
    y: m.top + (availH - bounds.h * zoom) / 2 - bounds.y * zoom,
  }
}

/** At low zoom the AI marker is scaled back up so its label stays readable; never smaller than it is drawn. */
export const counterScale = (zoom: number) => Math.min(1.5, Math.max(1, 1 / Math.max(zoom, 0.01)))

// ── following the build head (PLAN-MOTION-V2 §2.8, §5.3) ─────────────────────────────────────────

/** The safe box, as fractions of the free area; `anchorX` is where a panned-to head lands. */
export const SAFE = { x0: 0.15, x1: 0.62, y0: 0.18, y1: 0.82, anchorX: 0.42 }

/** The part of the canvas (screen px) the panel, pill and toolbar leave free. */
export function freeArea(size: { width: number; height: number }, inset: number, m = CAMERA_FRAME_MARGIN): Rect {
  return {
    x: m.left, y: m.top,
    w: Math.max(1, size.width - inset - m.left - m.right),
    h: Math.max(1, size.height - m.top - m.bottom),
  }
}

/** Zoom while building: a little closer than the fit, never past 1, never under .8. */
export const buildZoomFor = (fitZoom: number) => Math.min(1, Math.max(0.8, fitZoom * 1.25))

/**
 * The camera at the start of a round. If everything fits at the build zoom, frame everything once. Otherwise zoom to
 * the build zoom with the first head at `anchorX` of the free width, the round's box centred vertically.
 */
export function frameForRound(box: Rect, firstHead: Rect | null, size: { width: number; height: number }, inset: number): Viewport {
  const fit = viewportForBounds(box, size, { inset, maxZoom: 1 })
  const zoom = buildZoomFor(fit.zoom)
  if (fit.zoom >= zoom) return fit
  const free = freeArea(size, inset)
  const head = firstHead ?? { x: box.x, y: box.y, w: NODE_BOX.w, h: NODE_BOX.h }
  const cx = head.x + head.w / 2
  return {
    zoom,
    x: free.x + SAFE.anchorX * free.w - cx * zoom,
    y: free.y + free.h / 2 - (box.y + box.h / 2) * zoom,
  }
}

/**
 * Where the camera goes to keep `head` (flow px) in view at the current viewport. Null when it already lies inside the
 * safe box. Otherwise the same zoom, panned so the head centre lands at `anchorX` (y only clamped into the band).
 */
export function followViewport(head: Rect, vp: Viewport, size: { width: number; height: number }, inset: number): Viewport | null {
  const free = freeArea(size, inset)
  const box = { x0: free.x + SAFE.x0 * free.w, x1: free.x + SAFE.x1 * free.w, y0: free.y + SAFE.y0 * free.h, y1: free.y + SAFE.y1 * free.h }
  const left = head.x * vp.zoom + vp.x
  const top = head.y * vp.zoom + vp.y
  const right = left + head.w * vp.zoom
  const bottom = top + head.h * vp.zoom
  if (left >= box.x0 && right <= box.x1 && top >= box.y0 && bottom <= box.y1) return null
  const cy = (top + bottom) / 2
  const wantY = Math.min(box.y1, Math.max(box.y0, cy))
  return {
    zoom: vp.zoom,
    x: free.x + SAFE.anchorX * free.w - (head.x + head.w / 2) * vp.zoom,
    y: vp.y + (wantY - cy),
  }
}

export const easeInOutCubic = (t: number) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2)
