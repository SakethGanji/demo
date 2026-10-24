/**
 * The visual-edit saver (ARCHITECTURE §6.2, Phase 0): visual edits are saved as a version shortly after they stop, and every
 * AI turn first `flush()`es, so the version the AI is based on holds every visual edit. A flush cancels the debounce, saves
 * now, waits out a save already in flight, and re-saves edits that arrived meanwhile or whose last save failed. It resolves
 * `true` once nothing is left unsaved, else the error message (the caller blocks the AI send on it).
 */
import { ApiError } from '@/shared/api/http'

export type SaveResult = true | string

export interface VisualSaver {
  /** an edit happened: save it after the debounce */
  schedule(): void
  /** save anything pending now and wait for it; `true` when nothing is left unsaved */
  flush(): Promise<SaveResult>
  /** the document was saved some other way (a manual save): drop what is pending */
  markClean(): void
  /** edits not yet saved: waiting on the debounce, in flight, or failed */
  hasPending(): boolean
  /** a save is running */
  isSaving(): boolean
  subscribe(fn: () => void): () => void
  dispose(): void
}

export const STALE_BASE_MESSAGE = 'This app was changed somewhere else (another tab or session) since you opened it'

/** A readable message for a failed save; a stale base (409) says so plainly. */
export function saveErrorMessage(err: unknown, fallback: (e: unknown) => string): string {
  if (isStaleBase(err)) return STALE_BASE_MESSAGE
  return fallback(err)
}

export function isStaleBase(err: unknown): boolean {
  return err instanceof ApiError && err.status === 409 && err.code === 'stale_base'
}

export function createVisualSaver(save: () => Promise<void>, onError: (err: unknown) => void, delay = 1200): VisualSaver {
  let timer: ReturnType<typeof setTimeout> | null = null
  let dirty = false // edits since the last save started (or a failed save's edits)
  let inFlight: Promise<SaveResult> | null = null
  let lastError: string | null = null
  const listeners = new Set<() => void>()
  const emit = () => listeners.forEach((fn) => fn())

  const runOnce = (): Promise<SaveResult> => {
    dirty = false
    const p = save().then(
      (): SaveResult => {
        lastError = null
        return true
      },
      (err: unknown): SaveResult => {
        dirty = true // still unsaved
        lastError = err instanceof Error ? err.message : String(err)
        onError(err)
        return lastError
      },
    )
    inFlight = p.finally(() => {
      inFlight = null
      emit()
    })
    emit()
    return inFlight
  }

  const saveNow = async (): Promise<SaveResult> => {
    if (timer) {
      clearTimeout(timer)
      timer = null
    }
    if (inFlight) await inFlight
    if (!dirty) return lastError ?? true
    return runOnce()
  }

  return {
    schedule() {
      dirty = true
      if (timer) clearTimeout(timer)
      timer = setTimeout(() => {
        timer = null
        void saveNow()
      }, delay)
      emit()
    },
    async flush() {
      // a failed save leaves `dirty` set, so a flush retries it; edits made during an in-flight save are saved after it
      const r = await saveNow()
      if (r !== true) return r
      return dirty ? saveNow() : true
    },
    markClean() {
      if (timer) clearTimeout(timer)
      timer = null
      dirty = false
      lastError = null
      emit()
    },
    hasPending: () => dirty || timer != null || inFlight != null,
    isSaving: () => inFlight != null,
    subscribe(fn) {
      listeners.add(fn)
      return () => listeners.delete(fn)
    },
    dispose() {
      if (timer) clearTimeout(timer)
      timer = null
      listeners.clear()
    },
  }
}
