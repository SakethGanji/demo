/**
 * Runtime errors seen in the preview sandbox after a load: uncaught errors, console.error, and failed API calls. They are
 * collected (deduped, settled for a few seconds) so the builder can say "Preview has 2 errors" and ask the model to fix them
 * once per version.
 */

export type PreviewErrorKind = 'uncaught' | 'console' | 'api' | 'render'

export interface PreviewError {
  kind: PreviewErrorKind
  message: string
}

export const SETTLE_MS = 3000
const MAX_ERRORS = 8
const MAX_LEN = 300

/** Collapse whitespace and numbers-in-ids noise so the same failure reported twice counts once. */
function keyOf(e: PreviewError): string {
  return `${e.kind}:${e.message.replace(/\s+/g, ' ').trim().toLowerCase()}`
}

export function clean(message: string): string {
  const one = message.replace(/\s+/g, ' ').trim()
  return one.length > MAX_LEN ? `${one.slice(0, MAX_LEN)}…` : one
}

/** 401/403 are expected access refusals (a role-gated route seen by a user without the role): never a preview error. */
export function isAccessRefusal(status: number): boolean {
  return status === 401 || status === 403
}

/** A failed api call as one plain line, e.g. `GET /api/p/shop/items failed with 500`. */
export function apiFailure(method: string, path: string, status: number): PreviewError {
  const bare = path.split('?')[0]
  return { kind: 'api', message: `${method.toUpperCase()} ${bare} failed with ${status}` }
}

/**
 * Gathers errors for one preview load. `onChange` is called with the full deduped list once things settle (debounced),
 * and immediately with `[]` on `reset()` (a new load).
 */
export class PreviewErrorCollector {
  private seen = new Map<string, PreviewError>()
  private timer: ReturnType<typeof setTimeout> | null = null
  private onChange: (errors: PreviewError[]) => void
  private delay: number
  constructor(onChange: (errors: PreviewError[]) => void, delay = SETTLE_MS) {
    this.onChange = onChange
    this.delay = delay
  }

  add(e: PreviewError): void {
    const err = { kind: e.kind, message: clean(e.message) }
    if (!err.message) return
    if (err.kind === 'api' && /failed with (401|403)$/.test(err.message)) return
    const k = keyOf(err)
    if (this.seen.has(k) || this.seen.size >= MAX_ERRORS) return
    this.seen.set(k, err)
    if (this.timer) clearTimeout(this.timer)
    this.timer = setTimeout(() => {
      this.timer = null
      this.onChange([...this.seen.values()])
    }, this.delay)
  }

  reset(): void {
    if (this.timer) clearTimeout(this.timer)
    this.timer = null
    const had = this.seen.size > 0
    this.seen.clear()
    if (had) this.onChange([])
  }

  dispose(): void {
    if (this.timer) clearTimeout(this.timer)
    this.timer = null
  }
}

/** The chat edit sent for the errors: plain, one per line. */
export function fixRequestText(errors: PreviewError[]): string {
  const lines = errors.map((e) => `- ${e.message}`).join('\n')
  return `The preview shows ${errors.length === 1 ? 'an error' : `${errors.length} errors`}. Please fix ${errors.length === 1 ? 'it' : 'them'} without changing anything else:\n${lines}`
}

export function errorsLabel(n: number): string {
  return `Preview has ${n} ${n === 1 ? 'error' : 'errors'}`
}

/** Auto-fix at most once per version, never while a build or edit runs, and never for a version an auto-fix produced. */
export function shouldAutoFix(opts: { errors: PreviewError[]; versionKey: string | number | null; fixed: ReadonlySet<string>; busy: boolean }): boolean {
  if (!opts.errors.length || opts.busy || opts.versionKey == null) return false
  return !opts.fixed.has(String(opts.versionKey))
}
