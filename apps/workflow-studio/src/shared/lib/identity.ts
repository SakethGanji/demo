/**
 * Studio-wide identity.
 *
 * The analytics service authenticates by header, not token: `X-User-Id` is
 * required and `X-Team-Id` is optional (defaults to the user's team). Its whole
 * RBAC and column-masking story hangs off this, so identity lives in `shared/`
 * rather than inside the datasets feature — it is the seam that will eventually
 * unify the services, and workflow-engine has no auth of its own today.
 *
 * Masking masks a viewer AND an editor; only admin/owner/superuser see raw
 * values. The "act as" switcher exists so that is demonstrable rather than
 * theoretical.
 */

import { create } from 'zustand';

/** The System superuser seeded by the analytics service. */
export const SYSTEM_USER_ID = '00000000-0000-0000-0000-000000000001';

const STORAGE_KEY = 'studio.identity';

export interface Identity {
  userId: string;
  teamId?: string | null;
  /** Display label, e.g. "System (admin)". Presentation only. */
  label: string;
}

const DEFAULT_IDENTITY: Identity = { userId: SYSTEM_USER_ID, label: 'System (admin)' };

function load(): Identity {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw) as Partial<Identity>;
      if (typeof parsed.userId === 'string' && parsed.userId) {
        return { userId: parsed.userId, teamId: parsed.teamId ?? null, label: parsed.label ?? parsed.userId };
      }
    }
  } catch {
    // Corrupt or unavailable storage: fall through to the default seat.
  }
  return DEFAULT_IDENTITY;
}

interface IdentityState {
  identity: Identity;
  setIdentity: (next: Identity) => void;
  reset: () => void;
}

export const useIdentityStore = create<IdentityState>((set) => ({
  identity: load(),
  setIdentity: (next) => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
    } catch {
      // Non-fatal: the seat still applies for this session.
    }
    set({ identity: next });
  },
  reset: () => {
    try {
      localStorage.removeItem(STORAGE_KEY);
    } catch {
      // Non-fatal.
    }
    set({ identity: DEFAULT_IDENTITY });
  },
}));

/**
 * Read the current seat imperatively. The client is not a React component, so
 * it reads from the store rather than a hook — this stays in sync because
 * zustand's `getState` always returns the live value.
 */
export function getIdentityHeaders(): Record<string, string> {
  const { identity } = useIdentityStore.getState();
  const headers: Record<string, string> = { 'X-User-Id': identity.userId };
  if (identity.teamId) headers['X-Team-Id'] = identity.teamId;
  return headers;
}
