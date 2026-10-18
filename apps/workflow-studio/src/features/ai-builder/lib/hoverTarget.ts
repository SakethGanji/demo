/**
 * Which canvas node a pointer over the sidebar points at, from the data-testids of the element under it and its
 * ancestors (nearest first). A plan step is matched by its root testid only: its inner controls
 * (`ai-spec-step-type-1`, `ai-spec-step-api-1`) carry the same prefix and must not hide it.
 */
export function hoverNodeFromTestIds(ids: string[], stepNames: string[]): string | null {
  for (const id of ids) {
    const step = /^ai-spec-step-(\d+)$/.exec(id);
    if (step) return stepNames[Number(step[1])] ?? null;
    if (id.startsWith('ai-review-diff-') && id !== 'ai-review-diff-list') return id.slice('ai-review-diff-'.length);
  }
  return null;
}
