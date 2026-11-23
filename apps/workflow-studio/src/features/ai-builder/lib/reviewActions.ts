/** Which review button leads (primary) and which steps back (secondary). */
export function reviewButtons(passed: boolean, canRevise: boolean): { keep: 'default' | 'secondary'; revise: 'default' | 'secondary' } {
  // With a failing test the safe next step is to ask for a fix; keeping stays possible behind a confirm dialog.
  if (!passed && canRevise) return { keep: 'secondary', revise: 'default' };
  return { keep: passed ? 'default' : 'secondary', revise: 'secondary' };
}
