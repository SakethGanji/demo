/**
 * Visual editing is a trial behind a flag: `VITE_VISUAL_EDIT=1` at build time, or `localStorage['studio:visual-edit'] = '1'`
 * in a browser (`'0'` turns it off again). Off by default; with it off nothing about the builder changes.
 */
export function visualEditEnabled(): boolean {
  try {
    const local = typeof localStorage !== 'undefined' ? localStorage.getItem('studio:visual-edit') : null
    if (local === '1' || local === '0') return local === '1'
  } catch {
    /* storage blocked */
  }
  return import.meta.env.VITE_VISUAL_EDIT === '1' || import.meta.env.VITE_VISUAL_EDIT === 'true'
}
