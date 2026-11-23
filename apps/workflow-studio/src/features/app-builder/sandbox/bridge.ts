/**
 * PostMessage protocol types for parent <-> iframe communication.
 */

// ── Messages from parent → iframe ────────────────────────────────────────────

export interface DropTargetMsg {
  vsrc: string
  items: { vsrc: string; index: number }[]
  count: number
}

export type ParentMessage =
  | { type: 'render'; source: string; css?: string; viewer?: { id: string; roles: string[] } | null }
  // visual editing (only when the edit runtime is in the iframe)
  | { type: 'veditMode'; on: boolean }
  | {
      type: 'vhighlight'
      vsrc: string | null
      vpart?: string | null
      label?: string
      siblings?: { index: number; count: number; items: { vsrc: string; index: number }[] } | null
      /** the selection's parent (a drop there reorders) and the containers it can be dragged into */
      parent?: string | null
      targets?: DropTargetMsg[]
    }
  /** a palette item dragged over the preview (coordinates relative to the iframe) */
  | { type: 'vpal'; phase: 'start'; targets: DropTargetMsg[] }
  | { type: 'vpal'; phase: 'move'; x: number; y: number; inside: boolean }
  | { type: 'vpal'; phase: 'end'; drop: boolean }
  /** change classes on every rendered element tagged `vsrc` right now (the re-bundle that follows matches it) */
  | { type: 'vpatch'; vsrc: string; add: string[]; remove: string[] }
  /** the studio's answer to an inline text edit: begin (may it start?) or commit (was it applied?) */
  | { type: 'vtextgo'; phase: 'begin' | 'commit'; ok: boolean }
  /** start an inline text edit on the rendered element tagged `vsrc` (the canvas toolbar's "Edit text"), as a double-click would */
  | { type: 'vtextstart'; vsrc: string }
  /** outline the rendered element tagged `vsrc` as a hover would (the Layers tree), without selecting it; null clears it */
  | { type: 'vhover'; vsrc: string | null; vpart?: string | null }
  /**
   * A live style override on the element tagged `vsrc` while a canvas handle drags (inline, !important, no re-bundle);
   * `style: null` clears it — at once, or with `until: 'render'` after the next render lands (so a commit doesn't flash back)
   */
  | { type: 'vlive'; vsrc: string; vpart?: string | null; style: Record<string, string> | null; until?: 'render' }
  | {
      type: 'apiResponse'
      reqId: string
      status?: number
      statusText?: string
      headers?: Record<string, string>
      body?: ArrayBuffer | string
      error?: string
    }

/** A box in the iframe's viewport (CSS px, client coordinates). */
export interface Rect {
  x: number
  y: number
  w: number
  h: number
}

/** [top, right, bottom, left] in px, as CSS orders them. */
export type Sides = [number, number, number, number]

/**
 * The selected element's computed box, for on-canvas handles (padding / gap / size): what the browser laid out, read
 * from the first rendered node. Null for a multi-node selection (a fragment, a list part spread over nodes).
 */
export interface BoxMetrics {
  padding: Sides
  margin: Sides
  border: Sides
  /** [row, column] gap in px (0 when the element is not a flex / grid container) */
  gap: [number, number]
  display: string
  /** flex-direction (only meaningful for display flex / inline-flex) */
  direction: string
  /** flex-grow (a grower's main-axis size comes from its flex parent, not its width / height class) */
  grow?: number
  /** the DOM parent's display and flex-direction, and its content box (what w-1/2, h-full… resolve against) */
  parentDisplay?: string
  parentDirection?: string
  parentContent?: { w: number; h: number }
  /** the element's border-box size at width auto / fit-content and height auto, for size handles that snap to them */
  natural?: { autoW: number; fitW: number; autoH: number }
}

/**
 * The selection as the preview draws it. One shape for the toolbar and later handle features: add optional fields,
 * never reshape. `rect` is null when nothing is selected, editing is off, or the element isn't rendered.
 */
export interface CanvasRect {
  /** the selection this describes (the studio drops a stale one) */
  vsrc: string | null
  vpart: string | null
  /** what the preview is doing: overlays hide during a drag (reorder or palette) or an inline text edit */
  state: 'idle' | 'drag' | 'inline'
  rect: Rect | null
  /** the nearest tagged ancestor's box (or the drag parent's), for parent-relative handles */
  parent: Rect | null
  box: BoxMetrics | null
  /** the iframe viewport size, to tell an element scrolled off-screen */
  viewport: { w: number; h: number }
  /** the selected node's rendered element children (gap handles), when it is a flex / grid container */
  children?: Rect[]
  /** what the pointer is over in the preview: `self` = the selection itself (margin overlay), `alt` = Alt held (measuring) */
  hover?: { rect: Rect; self: boolean; alt: boolean } | null
}

/** The AppShell screen the preview shows: the shell's tag, the open screen's index / label, and the file it renders from. */
export interface PreviewScreen {
  shell: string
  index: number | null
  label: string
  /** the source file of the first tagged element in the screen body (the screen component's file), when there is one */
  file: string | null
}

// ── Messages from iframe → parent ────────────────────────────────────────────

export type IframeMessage =
  | { type: 'ready' }
  | { type: 'error'; message: string; stack?: string }
  | { type: 'runtimeError'; message: string }
  | { type: 'console'; level: 'log' | 'info' | 'warn' | 'error'; args: unknown[] }
  | { type: 'apiRequest'; reqId: string; url: string; opts: RequestInit }
  | { type: 'vselect'; chain: { vsrc: string; name: string }[]; part: { owner: string; vpart: string } | null }
  | { type: 'vdblselect'; chain: { vsrc: string; name: string }[]; part: { owner: string; vpart: string } | null }
  /** a double-click on a plain-text element: ask to edit it in place, then hand back the new text */
  | { type: 'vtext'; phase: 'begin' | 'commit'; chain: { vsrc: string; name: string }[]; part: { owner: string; vpart: string } | null; value?: string }
  /** a drag of the selection dropped before child `before` of its parent, or of container `target` */
  | { type: 'vmove'; before: number; target?: string | null }
  | { type: 'vkey'; delta?: -1 | 1; action?: 'delete' | 'duplicate' | 'deselect' }
  /** where the selection is drawn now (sent on select, scroll, resize and re-render, at most once a frame, only when it changed) */
  | ({ type: 'vrect' } & CanvasRect)
  /** a palette item dropped into container `target` before child `before` */
  | { type: 'vinsert'; target: string; before: number }
  /** the open AppShell screen changed (null: no AppShell rendered) */
  | { type: 'vscreen'; screen: PreviewScreen | null }

// ── Helpers ──────────────────────────────────────────────────────────────────

export function postToIframe(iframe: HTMLIFrameElement, msg: ParentMessage) {
  iframe.contentWindow?.postMessage(msg, '*')
}

export function isIframeMessage(data: unknown): data is IframeMessage {
  return (
    data !== null &&
    typeof data === 'object' &&
    'type' in (data as Record<string, unknown>) &&
    ['ready', 'error', 'runtimeError', 'console', 'apiRequest', 'vselect', 'vdblselect', 'vtext', 'vmove', 'vkey', 'vinsert', 'vrect', 'vscreen'].includes(
      (data as Record<string, unknown>).type as string
    )
  )
}
