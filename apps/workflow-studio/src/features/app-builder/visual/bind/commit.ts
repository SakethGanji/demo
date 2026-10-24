/**
 * Commit a wiring edit as one undo step in the shared edit history (the same path the Style section uses for raw splices),
 * then keep the selection on the edited element, whose structural key can change when it is wrapped or unwrapped.
 */
import { useEditHistory } from '../history'
import type { VisualEdit } from '../useVisualEdit'
import { keyAt, type Built } from './scope'

const LOCKED = 'The chat is editing the app; visual edits wait until it finishes'

/** Apply a built edit; returns an error message or null. "Nothing to change" is not an error. */
export function commitBuilt(ve: VisualEdit, built: Built, coalesce?: string): string | null {
  if (!built.ok) return built.error === 'Nothing to change' ? null : built.error
  if (ve.locked) return LOCKED
  const sel = ve.selection
  useEditHistory.getState().commit(built.label, built.patches, coalesce)
  const patch = built.patches.find((p) => p.path === sel?.file)
  if (sel && patch?.after != null && built.focus != null) {
    const key = keyAt(patch.after, built.focus)
    if (key && key !== sel.key) ve.select({ file: sel.file, key, part: null })
  }
  return null
}
