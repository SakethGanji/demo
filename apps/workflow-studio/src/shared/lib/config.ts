/**
 * Environment Configuration
 *
 * Determines backend URLs. `VITE_WORKFLOW_API_URL` (UI-DESIGN §11.4) wins when set — the
 * isolated E2E stack points its own Vite instance at its own engine this way. Otherwise the
 * backend is chosen by hostname.
 */

interface Backends {
  workflow: string; // Workflow engine API origin
}

const ENVIRONMENTS: Record<string, Backends> = {
  localhost: { workflow: 'http://localhost:8000' },
  '127.0.0.1': { workflow: 'http://localhost:8000' },
};

// Default: same origin for all backends
const DEFAULT_BACKENDS: Backends = { workflow: '' };

const override = (import.meta.env.VITE_WORKFLOW_API_URL as string | undefined)?.replace(/\/+$/, '') || undefined;

function getBackends(): Backends {
  if (override) return { workflow: override };
  const hostname = window.location.hostname;
  return ENVIRONMENTS[hostname] ?? DEFAULT_BACKENDS;
}

export const backends = getBackends();

/** Absolute engine origin (falls back to the page origin when the engine is same-origin). */
function engineOrigin(): string {
  try {
    return new URL(backends.workflow || window.location.origin, window.location.origin).origin;
  } catch {
    return window.location.origin;
  }
}

export const config = {
  workflowApiUrl: backends.workflow,
  engineOrigin: engineOrigin(),
};
