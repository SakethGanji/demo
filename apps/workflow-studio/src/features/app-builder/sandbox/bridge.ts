/**
 * PostMessage protocol types for parent <-> iframe communication.
 */

// ── Messages from parent → iframe ────────────────────────────────────────────

export type ParentMessage =
  | { type: 'render'; source: string; css?: string }
  | { type: 'themeUpdate'; vars: Record<string, string> }

// ── Messages from iframe → parent ────────────────────────────────────────────

export type IframeMessage =
  | { type: 'ready' }
  | { type: 'error'; message: string; stack?: string }
  | { type: 'console'; level: 'log' | 'info' | 'warn' | 'error'; args: unknown[] }
  | { type: 'apiRequest'; reqId: string; url: string; opts: RequestInit }
  | { type: 'resize'; height: number }

// ── Helpers ──────────────────────────────────────────────────────────────────

export function postToIframe(iframe: HTMLIFrameElement, msg: ParentMessage) {
  iframe.contentWindow?.postMessage(msg, '*')
}

export function isIframeMessage(data: unknown): data is IframeMessage {
  return (
    data !== null &&
    typeof data === 'object' &&
    'type' in (data as Record<string, unknown>) &&
    ['ready', 'error', 'console', 'apiRequest', 'resize'].includes(
      (data as Record<string, unknown>).type as string
    )
  )
}
