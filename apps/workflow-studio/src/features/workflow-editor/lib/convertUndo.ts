/**
 * "Delete it" on the convert-to-sub-workflow toast: put the parent's steps back, then delete the new sub-workflow.
 * The outcome says whether anything is left behind, so the toast that follows can be truthful.
 *  - done: the steps are back and the sub-workflow is gone
 *  - orphaned: the steps are back but the sub-workflow still exists (nothing calls it any more)
 *  - failed: nothing was undone; the parent still calls the sub-workflow
 */
export type ConvertUndoOutcome = { kind: 'done' } | { kind: 'orphaned'; error: unknown } | { kind: 'failed'; error: unknown };

export interface ConvertUndoSteps {
  restoreParent: () => Promise<void>;
  deleteChild: () => Promise<void>;
}

export async function undoConversion(steps: ConvertUndoSteps): Promise<ConvertUndoOutcome> {
  try {
    await steps.restoreParent();
  } catch (error) {
    return { kind: 'failed', error };
  }
  try {
    await steps.deleteChild();
  } catch (error) {
    return { kind: 'orphaned', error };
  }
  return { kind: 'done' };
}
