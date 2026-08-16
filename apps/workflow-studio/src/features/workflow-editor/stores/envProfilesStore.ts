import { create } from 'zustand';

// Variable values live server-side (in the `variables` table, scoped by environment).
// The browser only tracks which environment the user wants the next run/webhook to use.
// No secret values ever live in localStorage.

interface EnvProfilesState {
  activeEnvironment: string;
  setActiveEnvironment: (environment: string) => void;
}

const STORAGE_KEY = 'workflow-studio:active-environment';
const DEFAULT_ENVIRONMENT = 'default';

function loadActive(): string {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (typeof raw === 'string' && raw.length > 0) return raw;
  } catch {
    // ignore
  }
  return DEFAULT_ENVIRONMENT;
}

function persist(environment: string) {
  try {
    localStorage.setItem(STORAGE_KEY, environment);
  } catch {
    // ignore — state still works in-memory this session
  }
}

export const useEnvProfilesStore = create<EnvProfilesState>((set) => ({
  activeEnvironment: loadActive(),
  setActiveEnvironment: (environment) => {
    persist(environment);
    set({ activeEnvironment: environment });
  },
}));
