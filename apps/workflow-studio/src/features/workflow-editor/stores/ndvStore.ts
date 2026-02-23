import { create } from 'zustand';

type DisplayMode = 'table' | 'json' | 'schema';

const STORAGE_KEY = 'ndv-preferences';

interface NDVPreferences {
  inputPanelSize: number;
  outputPanelSize: number;
  inputDisplayMode: DisplayMode;
  outputDisplayMode: DisplayMode;
}

function loadPreferences(): NDVPreferences {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored) {
      const parsed = JSON.parse(stored);
      return {
        inputPanelSize: parsed.inputPanelSize ?? 25,
        outputPanelSize: parsed.outputPanelSize ?? 25,
        inputDisplayMode: parsed.inputDisplayMode ?? 'schema',
        outputDisplayMode: parsed.outputDisplayMode ?? 'schema',
      };
    }
  } catch {
    // ignore
  }
  return { inputPanelSize: 25, outputPanelSize: 25, inputDisplayMode: 'schema', outputDisplayMode: 'schema' };
}

function savePreferences(prefs: Partial<NDVPreferences>) {
  try {
    const current = loadPreferences();
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ ...current, ...prefs }));
  } catch {
    // ignore
  }
}

let debouncedSaveTimer: ReturnType<typeof setTimeout> | null = null;
function debouncedSavePreferences(prefs: Partial<NDVPreferences>) {
  if (debouncedSaveTimer) clearTimeout(debouncedSaveTimer);
  debouncedSaveTimer = setTimeout(() => {
    savePreferences(prefs);
    debouncedSaveTimer = null;
  }, 300);
}

const defaults = loadPreferences();

interface NDVState {
  // Modal state
  isOpen: boolean;
  activeNodeId: string | null;

  // Panel sizes (percentage)
  inputPanelSize: number;
  outputPanelSize: number;

  // Display modes
  inputDisplayMode: DisplayMode;
  outputDisplayMode: DisplayMode;

  // Actions
  openNDV: (nodeId: string) => void;
  closeNDV: () => void;

  setPanelSizes: (input: number, output: number) => void;
  setInputDisplayMode: (mode: DisplayMode) => void;
  setOutputDisplayMode: (mode: DisplayMode) => void;
}

export const useNDVStore = create<NDVState>((set) => ({
  isOpen: false,
  activeNodeId: null,
  inputPanelSize: defaults.inputPanelSize,
  outputPanelSize: defaults.outputPanelSize,
  inputDisplayMode: defaults.inputDisplayMode,
  outputDisplayMode: defaults.outputDisplayMode,

  openNDV: (nodeId) => set({ isOpen: true, activeNodeId: nodeId }),
  closeNDV: () => set({ isOpen: false, activeNodeId: null }),

  setPanelSizes: (input, output) => {
    debouncedSavePreferences({ inputPanelSize: input, outputPanelSize: output });
    set({ inputPanelSize: input, outputPanelSize: output });
  },

  setInputDisplayMode: (mode) => {
    savePreferences({ inputDisplayMode: mode });
    set({ inputDisplayMode: mode });
  },

  setOutputDisplayMode: (mode) => {
    savePreferences({ outputDisplayMode: mode });
    set({ outputDisplayMode: mode });
  },
}));
