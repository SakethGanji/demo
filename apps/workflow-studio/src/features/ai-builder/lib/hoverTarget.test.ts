import { describe, expect, it } from 'vitest';
import { hoverNodeFromTestIds } from './hoverTarget';

const steps = ['Receive', 'Check'];

describe('hoverNodeFromTestIds', () => {
  it('finds the step root past its inner type select', () => {
    expect(hoverNodeFromTestIds(['ai-spec-step-type-1', 'ai-spec-step-1', 'ai-spec-panel'], steps)).toBe('Check');
  });
  it('matches a plain step root', () => {
    expect(hoverNodeFromTestIds(['ai-spec-step-0'], steps)).toBe('Receive');
  });
  it('reads a review row and ignores the list', () => {
    expect(hoverNodeFromTestIds(['ai-review-diff-Over limit?', 'ai-review-diff-list'], steps)).toBe('Over limit?');
    expect(hoverNodeFromTestIds(['ai-review-diff-list'], steps)).toBeNull();
  });
  it('is null elsewhere', () => {
    expect(hoverNodeFromTestIds(['ai-spec-summary'], steps)).toBeNull();
  });
});
