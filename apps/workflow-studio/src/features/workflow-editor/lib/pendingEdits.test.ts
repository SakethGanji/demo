import { describe, it, expect, vi } from 'vitest';
import { registerPendingFlush, flushPendingEdits } from './pendingEdits';

describe('pendingEdits', () => {
  it('calls every registered flusher', () => {
    const a = vi.fn();
    const b = vi.fn();
    const offA = registerPendingFlush(a);
    const offB = registerPendingFlush(b);
    flushPendingEdits();
    expect(a).toHaveBeenCalledTimes(1);
    expect(b).toHaveBeenCalledTimes(1);
    offA();
    offB();
  });

  it('stops calling a flusher after it unregisters', () => {
    const a = vi.fn();
    const off = registerPendingFlush(a);
    off();
    flushPendingEdits();
    expect(a).not.toHaveBeenCalled();
  });
});
