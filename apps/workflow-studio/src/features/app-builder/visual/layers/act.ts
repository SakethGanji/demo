/**
 * Row actions of the Layers tree that target a row other than the selection (drag, hide, delete from the keyboard). They
 * go through the hook's `editAt` (the same commit + shared undo history as its own edits) whenever the selection should
 * follow; a hide on an unselected row commits the same way but leaves the selection where it is.
 */
import { applyOps, type Op, type Selection } from '../editor'
import { useEditHistory } from '../history'
import type { FileMap } from '../source'
import type { VisualEdit } from '../useVisualEdit'

export const LOCKED = 'The chat is editing the app; visual edits wait until it finishes'

/** Apply `ops` at `sel` as one undo step; returns the refusal or null. `follow` selects the element the edit returns. */
export function editRow(ve: VisualEdit, files: FileMap, sel: Selection, ops: Op[], follow: boolean): string | null {
  const wasSelected = !!ve.selection && ve.selection.file === sel.file && ve.selection.key === sel.key
  if (follow || wasSelected) return ve.editAt(sel, ops)
  if (ve.locked) return LOCKED
  const r = applyOps(files, sel, ops)
  if (!r.ok) return r.error
  useEditHistory.getState().commit(r.label, r.patches)
  return null
}

/** Whether `ops` would apply at `sel` (a dry run for the drop indicator), as the refusal or null. */
export function wouldRefuse(files: FileMap, sel: Selection, ops: Op[]): string | null {
  const r = applyOps(files, sel, ops)
  return r.ok ? null : r.error
}

/** Outline `vsrc` in the preview while a row is hovered, without selecting it (null: back to the selection). */
export function hoverPreview(ve: VisualEdit, vsrc: string | null): void {
  if (ve.editing) ve.hover(vsrc)
}
