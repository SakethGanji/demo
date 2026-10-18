import { useEffect, useState } from 'react';

/**
 * A stopwatch that starts when it mounts. `frozenMs` (when known) wins and stops it: a finished round shows the
 * server's own time. Used by the thinking block and the live log, so the wait always has a visible heartbeat.
 */
export function Elapsed({ running = true, frozenMs, decimals = 0, className, ...rest }: {
  running?: boolean;
  frozenMs?: number | null;
  decimals?: 0 | 1;
  className?: string;
  'data-testid'?: string;
}) {
  const [ms, setMs] = useState(0);
  useEffect(() => {
    if (!running || frozenMs != null) return;
    const t0 = Date.now();
    const id = setInterval(() => setMs(Date.now() - t0), decimals ? 100 : 500);
    return () => clearInterval(id);
  }, [running, frozenMs, decimals]);
  const shown = frozenMs ?? ms;
  return <span className={className} {...rest}>{(shown / 1000).toFixed(decimals)}s</span>;
}
