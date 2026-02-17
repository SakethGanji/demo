import { create } from 'zustand';
import type { NodeCreatorView, SubnodeSlotContext, SubnodeType } from '../types/workflow';

export type BottomPanelTab = 'logs' | 'ui';
export type RightPanelTab = 'nodes' | 'ai';
export type CanvasMode = 'pointer' | 'hand';

const STORAGE_PREFIX = 'workflow-studio:editor-layout';

function loadBool(key: string, fallback: boolean): boolean {
  try {
    const v = localStorage.getItem(`${STORAGE_PREFIX}:${key}`);
    return v !== null ? JSON.parse(v) : fallback;
  } catch {
    return fallback;
  }
}

function loadNumber(key: string, fallback: number): number {
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
    if (v === '"logs"' || v === '"ui"') return JSON.parse(v);
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

// Debounced persist for continuous drag operations (panel resizing)
const debouncedTimers = new Map<string, ReturnType<typeof setTimeout>>();
function debouncedPersist(key: string, value: unknown) {
  const existing = debouncedTimers.get(key);
  if (existing) clearTimeout(existing);
  debouncedTimers.set(key, setTimeout(() => {
    persist(key, value);
    debouncedTimers.delete(key);
  }, 300));
}

interface DropPosition {
  x: number;
  y: number;
}

interface EditorLayoutState {
  // Right panel
  rightPanelOpen: boolean;
  rightPanelSize: number;
  rightPanelTab: RightPanelTab;

  // Bottom panel
  bottomPanelOpen: boolean;
  bottomPanelSize: number;
  bottomPanelTab: BottomPanelTab;
  bottomPanelMaximized: boolean;

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
  subnodeSlotContext: SubnodeSlotContext | null;

  // Layout actions
  toggleRightPanel: () => void;
  setRightPanelSize: (size: number) => void;
  setRightPanelTab: (tab: RightPanelTab) => void;
  openRightPanel: (tab?: RightPanelTab) => void;
  ensureRightPanelOpen: (tab: RightPanelTab) => void;
  closeRightPanel: () => void;
  toggleBottomPanel: () => void;
  setBottomPanelSize: (size: number) => void;
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
  openForSubnode: (parentNodeId: string, slotName: string, slotType: SubnodeType) => void;
  clearSubnodeContext: () => void;
}

export const useEditorLayoutStore = create<EditorLayoutState>((set, get) => ({
  rightPanelOpen: loadBool('right-open', true),
  rightPanelSize: loadNumber('right-size', 20),
  rightPanelTab: loadRightTab('nodes'),
  bottomPanelOpen: loadBool('bottom-open', false),
  bottomPanelSize: loadNumber('bottom-size', 30),
  bottomPanelTab: loadBottomTab('logs'),
  bottomPanelMaximized: false,

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
  subnodeSlotContext: null,

  toggleRightPanel: () =>
    set((s) => {
      const next = !s.rightPanelOpen;
      persist('right-open', next);
      return { rightPanelOpen: next };
    }),

  setRightPanelSize: (size) => {
    debouncedPersist('right-size', size);
    set({ rightPanelSize: size });
  },

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

  setBottomPanelSize: (size) => {
    debouncedPersist('bottom-size', size);
    set({ bottomPanelSize: size });
  },

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
    subnodeSlotContext: null,
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
      subnodeSlotContext: null,
    });
  },

  clearConnectionContext: () =>
    set({ sourceNodeId: null, sourceHandleId: null, dropPosition: null }),

  openForSubnode: (parentNodeId, slotName, slotType) => {
    get().ensureRightPanelOpen('nodes');
    set({
      nodeCreatorView: 'subnode',
      nodeCreatorSearch: '',
      sourceNodeId: null,
      sourceHandleId: null,
      subnodeSlotContext: { parentNodeId, slotName, slotType },
    });
  },

  clearSubnodeContext: () =>
    set({ subnodeSlotContext: null }),
}));
