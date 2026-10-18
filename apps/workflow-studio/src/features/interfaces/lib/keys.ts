/** Idempotency keys and the run token store (DESIGN-STAGE5-UIS §6.1, §8.1). */

export function newIdempotencyKey(): string {
  try {
    if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') return crypto.randomUUID()
  } catch {
    /* fall through */
  }
  return `k-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`
}

const RUN_TOKEN_PREFIX = 'workflow-studio:iface-run:'

function store(): Storage | null {
  try {
    return typeof sessionStorage === 'undefined' ? null : sessionStorage
  } catch {
    return null
  }
}

/** The run token is read from `?t=` first (bookmarkable, works on another device), else sessionStorage. */
export function resolveRunToken(executionId: string, fromUrl: string | undefined): string | null {
  if (fromUrl) {
    try {
      store()?.setItem(RUN_TOKEN_PREFIX + executionId, fromUrl)
    } catch {
      /* best effort */
    }
    return fromUrl
  }
  try {
    return store()?.getItem(RUN_TOKEN_PREFIX + executionId) ?? null
  } catch {
    return null
  }
}

const NAME_KEY = 'workflow-studio:decided-by'

/** "Your name or email" for approvers (typed, not verified); remembered best effort. */
export function loadDecidedBy(): string {
  try {
    return localStorage.getItem(NAME_KEY) ?? ''
  } catch {
    return ''
  }
}

export function saveDecidedBy(v: string): void {
  try {
    localStorage.setItem(NAME_KEY, v)
  } catch {
    /* best effort */
  }
}
