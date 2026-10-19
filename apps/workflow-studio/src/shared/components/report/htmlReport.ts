/**
 * HTML report helpers (pure). Workflows can output a standalone HTML document (the `Html` node:
 * `{html, text, title}`; `SendEmail`: `{sent, to, subject, html, message_id, note?}`). The studio shows
 * it in an `<iframe sandbox="">` (no scripts, opaque origin) and, as a second wall, injects a strict
 * Content-Security-Policy as the very first thing the parser sees, so the same document is also safe
 * when opened in a new tab from a blob: URL (which would otherwise run with the studio's origin).
 */

export const REPORT_CSP = "default-src 'none'; img-src data: https:; style-src 'unsafe-inline'"
export const CSP_META = `<meta http-equiv="Content-Security-Policy" content="${REPORT_CSP}">`

const BLOCK_TAG =
  /<(?:html|head|body|div|p|table|thead|tbody|tr|td|th|h[1-6]|ul|ol|li|section|article|header|footer|main|nav|aside|pre|blockquote|figure|style)(?=[\s/>])[^>]*>/i

/** True when `s` is an HTML document or a fragment with block markup (not just a string with a `<`). */
export function looksLikeHtml(s: unknown): s is string {
  if (typeof s !== 'string') return false
  const t = s.replace(/^\uFEFF/, '').trimStart()
  if (/^<!doctype\s+html/i.test(t) || /^<html(?=[\s>])/i.test(t)) return true
  return BLOCK_TAG.test(s)
}

/**
 * The leading `<!doctype>`, `<html …>` and `<head …>` of a document, each optional and only at the very
 * start (whitespace aside). The CSP goes right after this prefix, before any other content.
 */
const PREFIX = /^\uFEFF?\s*(<!doctype[^>]*>)?\s*(<html(?=[\s>])[^>]*>)?\s*(<head(?=[\s>])[^>]*>)?/i

/**
 * Put the report CSP into the document before any other content.
 * - `<!doctype><html><head>` (any of them, in order, at the start): the meta goes right after them; with
 *   no `<head>` the parser creates one around it.
 * - A fragment (none of the three at the start): wrapped in a minimal UTF-8 document.
 * Anything placed before a later `<head>` (a script, a comment hiding a fake `<head>`) stays after the
 * policy, so it can never run ahead of it.
 */
export function injectCsp(html: string): string {
  const m = PREFIX.exec(html)
  if (m && (m[1] || m[2] || m[3])) {
    const at = m[0].length
    return html.slice(0, at) + CSP_META + html.slice(at)
  }
  return `<!doctype html><html><head><meta charset="utf-8">${CSP_META}</head><body>${html}</body></html>`
}

export interface EmailMeta {
  to: string
  subject: string
  sent: boolean
  note: string | null
}

export interface HtmlReport {
  /** index of the item in the node's output */
  index: number
  html: string
  title: string | null
  /** set when the item is a SendEmail result */
  email: EmailMeta | null
}

function str(v: unknown): string {
  if (Array.isArray(v)) return v.map(String).join(', ')
  return v == null ? '' : String(v)
}

/** A SendEmail item: a boolean `sent` plus `to` or `subject`. */
export function emailMeta(json: Record<string, unknown>): EmailMeta | null {
  if (typeof json.sent !== 'boolean' || !('to' in json || 'subject' in json)) return null
  return {
    to: str(json.to),
    subject: str(json.subject),
    sent: json.sent,
    note: typeof json.note === 'string' && json.note ? json.note : null,
  }
}

/** The first output item whose `html` field is an HTML document or block fragment, else null. */
export function findHtmlReport(items: readonly unknown[] | null | undefined): HtmlReport | null {
  if (!items) return null
  for (let index = 0; index < items.length; index++) {
    const json = items[index]
    if (!json || typeof json !== 'object' || Array.isArray(json)) continue
    const rec = json as Record<string, unknown>
    if (!looksLikeHtml(rec.html)) continue
    return {
      index,
      html: rec.html,
      title: typeof rec.title === 'string' && rec.title.trim() ? rec.title.trim() : null,
      email: emailMeta(rec),
    }
  }
  return null
}

/** `Weekly sales: Q3` -> `weekly-sales-q3.html`; empty -> `report.html`. */
export function reportFilename(title: string | null | undefined): string {
  const slug = (title ?? '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 60)
  return `${slug || 'report'}.html`
}

export type OutputView = 'schema' | 'json' | 'preview'

/**
 * The view an output panel shows. `previewChoice` is the user's pick for this panel (null = not picked):
 * an HTML report opens on Preview by default; without one the panel falls back to the stored data mode.
 */
export function outputView(mode: 'schema' | 'json', previewChoice: boolean | null, hasPreview: boolean): OutputView {
  if (hasPreview && previewChoice !== false) return 'preview'
  return mode
}
