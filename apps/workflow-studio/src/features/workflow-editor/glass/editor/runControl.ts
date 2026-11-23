/**
 * One run path for the glass editor: the run strip, the trigger's Test payload, ⌘↵ and the ⌘K "Run" all start
 * the same `useExecutionStream` instance (mounted once by EditorView), so ■ Stop can cancel whichever started it.
 */
import { create } from 'zustand'
import { toast } from 'sonner'
import { useEditorLayoutStore } from '../../stores/editorLayoutStore'

interface RunControl {
  executing: boolean
  /** set by EditorView from its stream hook */
  start: ((input: Record<string, unknown>) => void) | null
  cancel: (() => void) | null
  bind: (b: { start: (input: Record<string, unknown>) => void; cancel: () => void; executing: boolean }) => void
}

export const useRunControl = create<RunControl>((set) => ({
  executing: false,
  start: null,
  cancel: null,
  bind: ({ start, cancel, executing }) => set({ start, cancel, executing }),
}))

/**
 * Pasted JSON often has raw newlines or tabs inside string literals, which JSON.parse rejects.
 * Escape control characters only inside strings (outside them they are valid whitespace).
 */
export function escapeControlCharsInJsonStrings(text: string): string {
  let out = ''
  let inString = false
  let escaped = false
  for (let i = 0; i < text.length; i++) {
    const ch = text[i]
    if (escaped) {
      out += ch
      escaped = false
      continue
    }
    if (ch === '\\') {
      out += ch
      escaped = true
      continue
    }
    if (ch === '"') {
      inString = !inString
      out += ch
      continue
    }
    if (inString && ch.charCodeAt(0) < 0x20) {
      if (ch === '\n') out += '\\n'
      else if (ch === '\r') out += '\\r'
      else if (ch === '\t') out += '\\t'
      else out += `\\u${ch.charCodeAt(0).toString(16).padStart(4, '0')}`
      continue
    }
    out += ch
  }
  return out
}

/** Parse the test payload; null (with a toast) when it is not valid JSON even after the control-char fix. */
export function parsePayload(raw: string): Record<string, unknown> | null {
  if (!raw || !raw.trim()) return {}
  try {
    return JSON.parse(raw) as Record<string, unknown>
  } catch (e) {
    const fixed = escapeControlCharsInJsonStrings(raw)
    if (fixed !== raw) {
      try {
        const parsed = JSON.parse(fixed) as Record<string, unknown>
        toast.warning('Auto-fixed unescaped control chars in JSON strings')
        return parsed
      } catch {
        /* fall through to the original error */
      }
    }
    toast.error(`Payload JSON invalid: ${(e as Error).message}`)
    return null
  }
}

/** Run with the current test payload (`editorLayoutStore.payloadInput`). */
export function runWithPayload() {
  const { start, executing } = useRunControl.getState()
  if (!start || executing) return
  const parsed = parsePayload(useEditorLayoutStore.getState().payloadInput)
  if (parsed === null) return
  start(parsed)
}

export function runWithoutPayload() {
  const { start, executing } = useRunControl.getState()
  if (!start || executing) return
  start({})
}
