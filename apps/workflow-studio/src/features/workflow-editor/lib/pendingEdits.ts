/**
 * Flush registry for debounced edits. Inputs that debounce their writes into the store register a
 * flush function here; every save / run / test path calls `flushPendingEdits()` first so the
 * store (and therefore the saved draft) never lags behind what is on screen.
 */
type Flusher = () => void;

const flushers = new Set<Flusher>();

/** Registers a flush function. Returns the unregister function. */
export function registerPendingFlush(fn: Flusher): () => void {
  flushers.add(fn);
  return () => {
    flushers.delete(fn);
  };
}

/** Runs every registered flush function (each one applies its pending edits to the store). */
export function flushPendingEdits(): void {
  for (const fn of Array.from(flushers)) fn();
}
