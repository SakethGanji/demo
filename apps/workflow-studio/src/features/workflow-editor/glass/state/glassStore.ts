/**
 * Shared state of the glass workflow editor (BUILD-PLAN §1.5). One store for the three views, so the run
 * you pick, the step you select and the drawer tab follow you from Page to Editor to Definition.
 *
 * The test payload itself stays in `editorLayoutStore.payloadInput` (the old editor and the glass one share it);
 * `payloadSource` only says where the current text came from, for the Run button's label.
 */
import { create } from 'zustand'

export type GlassView = 'page' | 'editor' | 'definition' | 'side'
export type InspectorMode = 'closed' | 'compact' | 'expanded'
export type DrawerTab = 'runs' | 'logs' | 'problems' | 'tests'
/** Where the test payload text came from: typed by hand, copied from a run (`run:<id>`), or none. */
export type PayloadSource = { kind: 'none' } | { kind: 'typed' } | { kind: 'run'; runId: string }

const VIEW_KEY = 'workflow-studio:glass:view'
const VIEWS: GlassView[] = ['page', 'editor', 'definition', 'side']

function loadView(): GlassView {
  try {
    const v = localStorage.getItem(VIEW_KEY)
    return v && (VIEWS as string[]).includes(v) ? (v as GlassView) : 'page'
  } catch {
    return 'page'
  }
}

export interface GlassState {
  view: GlassView
  setView: (v: GlassView) => void

  /** The selected step, by node NAME (names are what definitions, runs and connections use). */
  selectedNode: string | null
  select: (name: string | null) => void
  /** A step hovered in one pane of Side by side, highlighted in the other. */
  hoverNode: string | null
  setHoverNode: (name: string | null) => void

  /** The run shown in every view; null = live (no run). */
  runId: string | null
  setRunId: (id: string | null) => void
  /** Live was picked on purpose: the Page then stops auto-picking the latest run. */
  liveChosen: boolean
  setLiveChosen: (v: boolean) => void
  /** The Page shows the cases a run did not take, open, instead of folded. */
  showUntaken: boolean
  setShowUntaken: (v: boolean) => void

  inspector: InspectorMode
  setInspector: (m: InspectorMode) => void

  drawerOpen: boolean
  drawerTab: DrawerTab
  openDrawer: (tab?: DrawerTab) => void
  closeDrawer: () => void
  setDrawerTab: (tab: DrawerTab) => void

  payloadSource: PayloadSource
  setPayloadSource: (s: PayloadSource) => void

  /** Back to defaults when another workflow opens (the view is a preference and stays). */
  resetForWorkflow: () => void
}

export const useGlassStore = create<GlassState>((set) => ({
  view: loadView(),
  setView: (view) => {
    try {
      localStorage.setItem(VIEW_KEY, view)
    } catch {
      /* private mode */
    }
    set({ view })
  },

  selectedNode: null,
  select: (selectedNode) => set({ selectedNode }),
  hoverNode: null,
  setHoverNode: (hoverNode) => set({ hoverNode }),

  runId: null,
  setRunId: (runId) => set({ runId }),
  liveChosen: false,
  setLiveChosen: (liveChosen) => set({ liveChosen }),
  showUntaken: false,
  setShowUntaken: (showUntaken) => set({ showUntaken }),

  inspector: 'closed',
  setInspector: (inspector) => set({ inspector }),

  drawerOpen: false,
  drawerTab: 'runs',
  openDrawer: (tab) => set((s) => ({ drawerOpen: true, drawerTab: tab ?? s.drawerTab })),
  closeDrawer: () => set({ drawerOpen: false }),
  setDrawerTab: (drawerTab) => set({ drawerTab }),

  payloadSource: { kind: 'none' },
  setPayloadSource: (payloadSource) => set({ payloadSource }),

  resetForWorkflow: () =>
    set({ selectedNode: null, hoverNode: null, runId: null, liveChosen: false, showUntaken: false, inspector: 'closed', drawerOpen: false, payloadSource: { kind: 'none' } }),
}))
