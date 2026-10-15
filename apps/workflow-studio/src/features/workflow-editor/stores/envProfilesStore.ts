import { create } from 'zustand';

// Variable values live server-side (in the `variables` table, scoped by environment).
// The browser only tracks which environment the user wants the next run/webhook to use.
// No secret values ever live in localStorage.
//
// UI-DESIGN §6.6 / review G3: the selection is stored per project under
// `workflow-studio:active-environment:<projectId>` and defaults to the project's
// `default_environment`. The Default project falls back to the legacy key so existing users
// keep their selection.

interface EnvProfilesState {
  projectId: string | null;
  activeEnvironment: string;
  setActiveEnvironment: (environment: string) => void;
  hydrateForProject: (projectId: string, defaultEnv: string, validEnvs: string[]) => void;
}

const LEGACY_KEY = 'workflow-studio:active-environment';
const keyFor = (projectId: string) => `${LEGACY_KEY}:${projectId}`;
const DEFAULT_ENVIRONMENT = 'default';

function read(key: string): string | null {
  try {
    const raw = localStorage.getItem(key);
    return typeof raw === 'string' && raw.length > 0 ? raw : null;
  } catch {
    return null;
  }
}

function write(key: string, value: string) {
  try {
    localStorage.setItem(key, value);
  } catch {
    // ignore — state still works in-memory this session
  }
}

export const useEnvProfilesStore = create<EnvProfilesState>((set, get) => ({
  projectId: null,
  activeEnvironment: read(LEGACY_KEY) ?? DEFAULT_ENVIRONMENT,
  setActiveEnvironment: (environment) => {
    const pid = get().projectId;
    write(pid ? keyFor(pid) : LEGACY_KEY, environment);
    set({ activeEnvironment: environment });
  },
  hydrateForProject: (projectId, defaultEnv, validEnvs) => {
    let stored = read(keyFor(projectId));
    if (!stored && projectId === 'proj_default') stored = read(LEGACY_KEY);
    const env = stored && validEnvs.includes(stored) ? stored : defaultEnv;
    if (get().projectId === projectId && get().activeEnvironment === env) return;
    set({ projectId, activeEnvironment: env });
  },
}));
