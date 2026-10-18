import { describe, expect, it, vi } from 'vitest';
import { undoConversion } from './convertUndo';

describe('undoConversion', () => {
  it('restores the parent, then deletes the child', async () => {
    const order: string[] = [];
    const out = await undoConversion({
      restoreParent: async () => void order.push('restore'),
      deleteChild: async () => void order.push('delete'),
    });
    expect(out).toEqual({ kind: 'done' });
    expect(order).toEqual(['restore', 'delete']);
  });

  it('does not delete the child when the parent could not be restored', async () => {
    const deleteChild = vi.fn();
    const out = await undoConversion({ restoreParent: () => Promise.reject(new Error('x')), deleteChild });
    expect(out.kind).toBe('failed');
    expect(deleteChild).not.toHaveBeenCalled();
  });

  it('reports an orphaned sub-workflow when only the delete fails', async () => {
    const out = await undoConversion({ restoreParent: async () => {}, deleteChild: () => Promise.reject(new Error('x')) });
    expect(out.kind).toBe('orphaned');
  });
});
