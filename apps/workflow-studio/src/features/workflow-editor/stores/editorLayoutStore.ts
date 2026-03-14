import { create } from 'zustand';
import type { NodeCreatorView } from '../types/workflow';

export type BottomPanelTab = 'logs' | 'input';
export type RightPanelTab = 'nodes' | 'ai';
type CanvasMode = 'pointer' | 'hand';

const STORAGE_PREFIX = 'workflow-studio:editor-layout';

function loadBool(key: string, fallback: boolean): boolean {
  try {
    const v = localStorage.getItem(`${STORAGE_PREFIX}:${key}`);
    return v !== null ? JSON.parse(v) : fallback;
  } catch {
    return fallback;
  }
}

function loadBottomTab(fallback: BottomPanelTab): BottomPanelTab {
  try {
    const v = localStorage.getItem(`${STORAGE_PREFIX}:bottom-tab`);
    if (v === '"logs"' || v === '"input"') return JSON.parse(v);
    return fallback;
  } catch {
    return fallback;
  }
}

function loadRightTab(fallback: RightPanelTab): RightPanelTab {
  try {
    const v = localStorage.getItem(`${STORAGE_PREFIX}:right-tab`);
    if (v === '"nodes"' || v === '"ai"') return JSON.parse(v);
    return fallback;
  } catch {
    return fallback;
  }
}

function persist(key: string, value: unknown) {
  localStorage.setItem(`${STORAGE_PREFIX}:${key}`, JSON.stringify(value));
}

interface DropPosition {
  x: number;
  y: number;
}

interface EditorLayoutState {
  // Right panel
  rightPanelOpen: boolean;
  rightPanelTab: RightPanelTab;

  // Bottom panel
  bottomPanelOpen: boolean;
  bottomPanelTab: BottomPanelTab;
  bottomPanelMaximized: boolean;

  // Payload input (shared between InputPanel and navbar)
  payloadInput: string;
  setPayloadInput: (value: string) => void;

  // Canvas mode
  canvasMode: CanvasMode;
  setCanvasMode: (mode: CanvasMode) => void;
  toggleCanvasMode: () => void;

  // Node creator
  nodeCreatorView: NodeCreatorView;
  nodeCreatorSearch: string;
  sourceNodeId: string | null;
  sourceHandleId: string | null;
  dropPosition: DropPosition | null;

  // Layout actions
  toggleRightPanel: () => void;
  setRightPanelTab: (tab: RightPanelTab) => void;
  openRightPanel: (tab?: RightPanelTab) => void;
  ensureRightPanelOpen: (tab: RightPanelTab) => void;
  closeRightPanel: () => void;
  toggleBottomPanel: () => void;
  setBottomPanelTab: (tab: BottomPanelTab) => void;
  openBottomPanel: (tab?: BottomPanelTab) => void;
  closeBottomPanel: () => void;
  toggleBottomPanelMaximized: () => void;

  // Node creator actions
  openCreatorPanel: (view: NodeCreatorView) => void;
  closeCreatorPanel: () => void;
  setCreatorView: (view: NodeCreatorView) => void;
  setCreatorSearch: (search: string) => void;
  openForConnection: (sourceNodeId: string, sourceHandleId: string, dropPosition?: DropPosition) => void;
  clearConnectionContext: () => void;
}

export const useEditorLayoutStore = create<EditorLayoutState>((set, get) => ({
  rightPanelOpen: loadBool('right-open', true),
  rightPanelTab: loadRightTab('nodes'),
  bottomPanelOpen: loadBool('bottom-open', false),
  bottomPanelTab: loadBottomTab('logs'),
  bottomPanelMaximized: false,

  // Payload input
  payloadInput: '{\n  "message": "Hello world",\n  "count": 42\n}',
  setPayloadInput: (value) => set({ payloadInput: value }),

  // Canvas mode
  canvasMode: 'hand' as CanvasMode,
  setCanvasMode: (mode) => set({ canvasMode: mode }),
  toggleCanvasMode: () => set((s) => ({ canvasMode: s.canvasMode === 'hand' ? 'pointer' : 'hand' })),

  // Node creator state
  nodeCreatorView: 'trigger' as NodeCreatorView,
  nodeCreatorSearch: '',
  sourceNodeId: null,
  sourceHandleId: null,
  dropPosition: null,

  toggleRightPanel: () =>
    set((s) => {
      const next = !s.rightPanelOpen;
      persist('right-open', next);
      return { rightPanelOpen: next };
    }),

  setRightPanelTab: (tab) => {
    persist('right-tab', tab);
    set({ rightPanelTab: tab });
  },

  openRightPanel: (tab) =>
    set((s) => {
      if (s.rightPanelOpen && tab && s.rightPanelTab === tab) {
        persist('right-open', false);
        return { rightPanelOpen: false };
      }
      persist('right-open', true);
      if (tab) persist('right-tab', tab);
      return { rightPanelOpen: true, ...(tab ? { rightPanelTab: tab } : {}) };
    }),

  ensureRightPanelOpen: (tab) => {
    persist('right-open', true);
    persist('right-tab', tab);
    set({ rightPanelOpen: true, rightPanelTab: tab });
  },

  closeRightPanel: () => {
    persist('right-open', false);
    set({ rightPanelOpen: false });
  },

  toggleBottomPanel: () =>
    set((s) => {
      const next = !s.bottomPanelOpen;
      persist('bottom-open', next);
      return { bottomPanelOpen: next };
    }),

  setBottomPanelTab: (tab) => {
    persist('bottom-tab', tab);
    set({ bottomPanelTab: tab });
  },

  openBottomPanel: (tab) =>
    set((s) => {
      if (s.bottomPanelOpen && tab && s.bottomPanelTab === tab) {
        persist('bottom-open', false);
        return { bottomPanelOpen: false, bottomPanelMaximized: false };
      }
      persist('bottom-open', true);
      if (tab) persist('bottom-tab', tab);
      return { bottomPanelOpen: true, ...(tab ? { bottomPanelTab: tab } : {}) };
    }),

  closeBottomPanel: () => {
    persist('bottom-open', false);
    set({ bottomPanelOpen: false, bottomPanelMaximized: false });
  },

  toggleBottomPanelMaximized: () =>
    set((s) => ({ bottomPanelMaximized: !s.bottomPanelMaximized })),

  // Node creator actions
  openCreatorPanel: (view) => {
    get().ensureRightPanelOpen('nodes');
    set({ nodeCreatorView: view, nodeCreatorSearch: '' });
  },

  closeCreatorPanel: () => set({
    nodeCreatorSearch: '',
    sourceNodeId: null,
    sourceHandleId: null,
    dropPosition: null,
  }),

  setCreatorView: (view) => set({ nodeCreatorView: view, nodeCreatorSearch: '' }),
  setCreatorSearch: (search) => set({ nodeCreatorSearch: search }),

  openForConnection: (sourceNodeId, sourceHandleId, dropPosition) => {
    get().ensureRightPanelOpen('nodes');
    set({
      nodeCreatorView: 'regular',
      nodeCreatorSearch: '',
      sourceNodeId,
      sourceHandleId,
      dropPosition: dropPosition ?? null,
    });
  },

  clearConnectionContext: () =>
    set({ sourceNodeId: null, sourceHandleId: null, dropPosition: null }),
}));
