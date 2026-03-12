import { create } from 'zustand'
import { immer } from 'zustand/middleware/immer'
import { temporal } from 'zundo'
import type { AppNode, StoreDefinition, WebhookDefinition, EventHandlerConfig, EventAction, BreakpointId, ElementState, DragSource, DropIndicator, StyleClass, AppTemplate } from './types'
import { getDefinition, BREAKPOINTS } from './types'

export function getChildren(
  nodes: Record<string, AppNode>,
  parentId: string
): AppNode[] {
  const parent = nodes[parentId]
  if (!parent) return []
  return parent.childIds.map((id) => nodes[id]).filter(Boolean)
}

export function getDescendants(
  nodes: Record<string, AppNode>,
  nodeId: string
): AppNode[] {
  const result: AppNode[] = []
  const stack = [nodeId]
  while (stack.length > 0) {
    const id = stack.pop()!
    const node = nodes[id]
    if (!node) continue
    if (id !== nodeId) result.push(node)
    for (let i = node.childIds.length - 1; i >= 0; i--) {
      stack.push(node.childIds[i])
    }
  }
  return result
}

export function isDescendant(
  nodes: Record<string, AppNode>,
  nodeId: string,
  potentialAncestorId: string
): boolean {
  let current = nodes[nodeId]
  while (current?.parentId) {
    if (current.parentId === potentialAncestorId) return true
    current = nodes[current.parentId]
  }
  return false
}

let classIdCounter = 0
function generateClassId(): string {
  return `class_${++classIdCounter}_${Date.now().toString(36)}`
}

interface StyleClassState {
  classes: StyleClass[]

  addClass: (name: string, styles?: Record<string, unknown>) => string
  updateClass: (id: string, updates: Partial<Omit<StyleClass, 'id'>>) => void
  removeClass: (id: string) => void
  loadClasses: (classes: StyleClass[]) => void
}

export const useStyleClassStore = create<StyleClassState>()(
  immer((set) => ({
    classes: [],

    addClass: (name, styles) => {
      const id = generateClassId()
      set((draft) => {
        draft.classes.push({ id, name, styles: styles ?? {} })
      })
      return id
    },

    updateClass: (id, updates) => {
      set((draft) => {
        const cls = draft.classes.find((c) => c.id === id)
        if (cls) Object.assign(cls, updates)
      })
    },

    removeClass: (id) => {
      set((draft) => {
        draft.classes = draft.classes.filter((c) => c.id !== id)
      })
    },

    loadClasses: (classes) => {
      set({ classes })
    },
  }))
)

type LogLevel = 'info' | 'warn' | 'error' | 'success'

export interface ConsoleEntry {
  id: number
  timestamp: number
  level: LogLevel
  source: string
  message: string
  detail?: unknown
}

let entryId = 0

interface ConsoleState {
  entries: ConsoleEntry[]
  log: (level: LogLevel, source: string, message: string, detail?: unknown) => void
  clear: () => void
}

export const useConsoleStore = create<ConsoleState>()((set) => ({
  entries: [],

  log: (level, source, message, detail) => {
    set((state) => ({
      entries: [
        ...state.entries.slice(-199), // keep last 200
        {
          id: ++entryId,
          timestamp: Date.now(),
          level,
          source,
          message,
          detail,
        },
      ],
    }))
  },

  clear: () => {
    set({ entries: [] })
  },
}))

interface BreakpointState {
  activeBreakpoint: BreakpointId
  setActiveBreakpoint: (bp: BreakpointId) => void
  getCanvasWidth: () => number | null
}

export const useBreakpointStore = create<BreakpointState>((set, get) => ({
  activeBreakpoint: 'desktop',

  setActiveBreakpoint: (bp) => set({ activeBreakpoint: bp }),

  getCanvasWidth: () => {
    const bp = BREAKPOINTS.find((b) => b.id === get().activeBreakpoint)
    return bp?.maxWidth ?? null
  },
}))

function navigatePath(obj: unknown, path: string): [parent: Record<string, unknown> | unknown[], key: string] | null {
  const parts = path.split('.')
  const lastKey = parts.pop()
  if (!lastKey) return null

  let current: unknown = obj
  for (const part of parts) {
    if (current == null || typeof current !== 'object') return null
    current = (current as Record<string, unknown>)[part]
  }

  if (current == null || typeof current !== 'object') return null
  return [current as Record<string, unknown>, lastKey]
}

interface RuntimeState {
  /** Per-component exposed state: { [nodeId]: { value: ..., checked: ... } } */
  componentState: Record<string, Record<string, unknown>>
  /** User-defined global stores: { [storeName]: value } */
  globalStores: Record<string, unknown>

  setComponentState: (nodeId: string, prop: string, value: unknown) => void
  setGlobalStore: (name: string, value: unknown) => void
  mergeGlobalStore: (name: string, partial: Record<string, unknown>) => void
  setStoreProperty: (name: string, path: string, value: unknown) => void
  appendToArray: (name: string, path: string, value: unknown) => void
  removeFromArray: (name: string, path: string, index: number) => void
  getContext: () => { components: Record<string, Record<string, unknown>>; stores: Record<string, unknown> }
  initialize: (nodes: Record<string, AppNode>, storeDefs: StoreDefinition[]) => void
  reset: () => void
}

export const useRuntimeStateStore = create<RuntimeState>()(
  immer((set, get) => ({
    componentState: {},
    globalStores: {},

    setComponentState: (nodeId, prop, value) => {
      set((draft) => {
        if (!draft.componentState[nodeId]) {
          draft.componentState[nodeId] = {}
        }
        draft.componentState[nodeId][prop] = value
      })
    },

    setGlobalStore: (name, value) => {
      set((draft) => {
        draft.globalStores[name] = value
      })
    },

    mergeGlobalStore: (name, partial) => {
      set((draft) => {
        const current = draft.globalStores[name]
        if (current && typeof current === 'object' && !Array.isArray(current)) {
          Object.assign(current as Record<string, unknown>, partial)
        } else {
          draft.globalStores[name] = partial
        }
      })
    },

    setStoreProperty: (name, path, value) => {
      set((draft) => {
        const store = draft.globalStores[name]
        const nav = navigatePath(store, path)
        if (nav) {
          const [parent, key] = nav
          ;(parent as Record<string, unknown>)[key] = value
        }
      })
    },

    appendToArray: (name, path, value) => {
      set((draft) => {
        const store = draft.globalStores[name]
        if (!path) {
          // Store itself is the array
          if (Array.isArray(store)) {
            store.push(value)
          }
        } else {
          const nav = navigatePath(store, path)
          if (nav) {
            const [parent, key] = nav
            const arr = (parent as Record<string, unknown>)[key]
            if (Array.isArray(arr)) {
              arr.push(value)
            }
          }
        }
      })
    },

    removeFromArray: (name, path, index) => {
      set((draft) => {
        const store = draft.globalStores[name]
        if (!path) {
          if (Array.isArray(store)) {
            store.splice(index, 1)
          }
        } else {
          const nav = navigatePath(store, path)
          if (nav) {
            const [parent, key] = nav
            const arr = (parent as Record<string, unknown>)[key]
            if (Array.isArray(arr)) {
              arr.splice(index, 1)
            }
          }
        }
      })
    },

    getContext: () => {
      const state = get()
      return {
        components: state.componentState,
        stores: state.globalStores,
      }
    },

    initialize: (nodes, storeDefs) => {
      set((draft) => {
        draft.componentState = {}
        draft.globalStores = {}

        // Seed component state from exposed defaults
        // Only seed non-empty defaults — empty strings are left to the component's
        // useComponentState fallback so it can use its own prop-based default
        // (e.g. Select uses props.defaultValue, Tabs uses props.defaultTab)
        for (const [nodeId, node] of Object.entries(nodes)) {
          const def = getDefinition(node.type)
          if (def?.exposedState?.length) {
            draft.componentState[nodeId] = {}
            for (const field of def.exposedState) {
              const propValue = node.props[field.name]
              const seedValue = propValue ?? field.defaultValue
              // Skip seeding empty strings — let the component handle its own default
              if (seedValue !== '') {
                draft.componentState[nodeId][field.name] = seedValue
              }
            }
          }
        }

        // Seed global stores
        for (const storeDef of storeDefs) {
          draft.globalStores[storeDef.name] = storeDef.initialValue
        }
      })
    },

    reset: () => {
      set({ componentState: {}, globalStores: {} })
    },
  }))
)

const nodeRefs = new Map<string, HTMLElement>()

export function setNodeRef(id: string, el: HTMLElement | null) {
  if (el) {
    nodeRefs.set(id, el)
  } else {
    nodeRefs.delete(id)
  }
}

export function getNodeRef(id: string): HTMLElement | undefined {
  return nodeRefs.get(id)
}

interface ContextMenuState {
  x: number
  y: number
  nodeId: string
}

interface ClipboardState {
  nodeIds: string[]
  mode: 'copy' | 'cut'
}

interface AppEditorState {
  selectedNodeIds: string[]
  hoveredNodeId: string | null
  mode: 'edit' | 'preview'

  // DnD
  dragSource: DragSource | null
  dropIndicator: DropIndicator | null

  // Clipboard
  clipboard: ClipboardState | null

  // Context menu
  contextMenu: ContextMenuState | null

  // Element state editing
  activeElementState: ElementState

  // Quick add palette
  quickAddOpen: boolean

  // Actions
  selectNode: (id: string) => void
  toggleSelectNode: (id: string) => void
  selectRange: (fromId: string, toId: string, nodes: Record<string, { parentId: string | null; childIds: string[] }>) => void
  clearSelection: () => void
  hoverNode: (id: string | null) => void
  setMode: (mode: 'edit' | 'preview') => void
  setDragSource: (source: DragSource | null) => void
  setDropIndicator: (indicator: DropIndicator | null) => void
  clearDrag: () => void

  // Clipboard actions
  copySelection: () => void
  cutSelection: () => void
  clearClipboard: () => void

  // Context menu actions
  openContextMenu: (x: number, y: number, nodeId: string) => void
  closeContextMenu: () => void

  // Element state
  setActiveElementState: (state: ElementState) => void

  // Quick add
  openQuickAdd: () => void
  closeQuickAdd: () => void
}

export const useAppEditorStore = create<AppEditorState>((set, get) => ({
  selectedNodeIds: [],
  hoveredNodeId: null,
  mode: 'edit',

  dragSource: null,
  dropIndicator: null,

  clipboard: null,
  contextMenu: null,
  activeElementState: 'default',
  quickAddOpen: false,

  selectNode: (id) => set({ selectedNodeIds: [id] }),

  toggleSelectNode: (id) => {
    const { selectedNodeIds } = get()
    if (selectedNodeIds.includes(id)) {
      set({ selectedNodeIds: selectedNodeIds.filter((nid) => nid !== id) })
    } else {
      set({ selectedNodeIds: [...selectedNodeIds, id] })
    }
  },

  selectRange: (fromId, toId, nodes) => {
    // Range select only works for siblings
    const fromNode = nodes[fromId]
    const toNode = nodes[toId]
    if (!fromNode?.parentId || !toNode?.parentId || fromNode.parentId !== toNode.parentId) {
      // Not siblings — just select the target
      set({ selectedNodeIds: [toId] })
      return
    }
    const parent = nodes[fromNode.parentId]
    if (!parent) {
      set({ selectedNodeIds: [toId] })
      return
    }
    const fromIdx = parent.childIds.indexOf(fromId)
    const toIdx = parent.childIds.indexOf(toId)
    const start = Math.min(fromIdx, toIdx)
    const end = Math.max(fromIdx, toIdx)
    set({ selectedNodeIds: parent.childIds.slice(start, end + 1) })
  },

  clearSelection: () => set({ selectedNodeIds: [] }),
  hoverNode: (id) => set({ hoveredNodeId: id }),
  setMode: (mode) => set({ mode }),
  setDragSource: (source) => set({ dragSource: source }),
  setDropIndicator: (indicator) => set({ dropIndicator: indicator }),
  clearDrag: () => set({ dragSource: null, dropIndicator: null }),

  copySelection: () => {
    const { selectedNodeIds } = get()
    if (selectedNodeIds.length === 0) return
    set({ clipboard: { nodeIds: [...selectedNodeIds], mode: 'copy' } })
  },

  cutSelection: () => {
    const { selectedNodeIds } = get()
    if (selectedNodeIds.length === 0) return
    set({ clipboard: { nodeIds: [...selectedNodeIds], mode: 'cut' } })
  },

  clearClipboard: () => set({ clipboard: null }),

  openContextMenu: (x, y, nodeId) => set({ contextMenu: { x, y, nodeId } }),
  closeContextMenu: () => set({ contextMenu: null }),

  setActiveElementState: (state) => set({ activeElementState: state }),

  openQuickAdd: () => set({ quickAddOpen: true }),
  closeQuickAdd: () => set({ quickAddOpen: false }),
}))

export type ThemeVar =
  // Colors
  | 'background' | 'foreground'
  | 'card' | 'card-foreground'
  | 'popover' | 'popover-foreground'
  | 'primary' | 'primary-foreground'
  | 'secondary' | 'secondary-foreground'
  | 'muted' | 'muted-foreground'
  | 'accent' | 'accent-foreground'
  | 'destructive' | 'destructive-foreground'
  | 'border' | 'input' | 'ring'
  | 'chart-1' | 'chart-2' | 'chart-3' | 'chart-4' | 'chart-5'
  | 'sidebar' | 'sidebar-foreground'
  | 'sidebar-primary' | 'sidebar-primary-foreground'
  | 'sidebar-accent' | 'sidebar-accent-foreground'
  | 'sidebar-border' | 'sidebar-ring'
  // Typography
  | 'font-sans' | 'font-serif' | 'font-mono'
  // Spacing & Radius
  | 'radius' | 'spacing' | 'letter-spacing'
  // Shadows (scale)
  | 'shadow-sm' | 'shadow' | 'shadow-md' | 'shadow-lg' | 'shadow-xl'

type ThemeVarValues = Partial<Record<ThemeVar, string>>

interface ThemePreset {
  name: string
  light: ThemeVarValues
  dark: ThemeVarValues
}

export const THEME_PRESETS: ThemePreset[] = [
  { name: 'Default', light: {}, dark: {} },
  {
    name: 'Modern Minimal',
    light: {
      background: '#ffffff', foreground: '#1a1a2e',
      card: '#ffffff', 'card-foreground': '#1a1a2e',
      popover: '#ffffff', 'popover-foreground': '#1a1a2e',
      primary: '#3b82f6', 'primary-foreground': '#ffffff',
      secondary: '#eff6ff', 'secondary-foreground': '#1e40af',
      muted: '#f1f5f9', 'muted-foreground': '#64748b',
      accent: '#dbeafe', 'accent-foreground': '#1e3a8a',
      destructive: '#ef4444', 'destructive-foreground': '#ffffff',
      border: '#cbd5e1', input: '#cbd5e1', ring: '#3b82f6',
      radius: '0.25rem',
    },
    dark: {
      background: '#0f172a', foreground: '#e2e8f0',
      card: '#1e293b', 'card-foreground': '#e2e8f0',
      popover: '#1e293b', 'popover-foreground': '#e2e8f0',
      primary: '#60a5fa', 'primary-foreground': '#0f172a',
      secondary: '#1e293b', 'secondary-foreground': '#93c5fd',
      muted: '#1e293b', 'muted-foreground': '#94a3b8',
      accent: '#1e3a8a', 'accent-foreground': '#bfdbfe',
      destructive: '#f87171', 'destructive-foreground': '#0f172a',
      border: '#334155', input: '#334155', ring: '#60a5fa',
      radius: '0.25rem',
    },
  },
  {
    name: 'Caffeine',
    light: {
      background: '#fdf8f0', foreground: '#3b2315',
      card: '#fff8ee', 'card-foreground': '#3b2315',
      popover: '#fff8ee', 'popover-foreground': '#3b2315',
      primary: '#92400e', 'primary-foreground': '#ffffff',
      secondary: '#ffdfb5', 'secondary-foreground': '#78350f',
      muted: '#fef3c7', 'muted-foreground': '#92400e',
      accent: '#fde68a', 'accent-foreground': '#78350f',
      destructive: '#e54d2e', 'destructive-foreground': '#ffffff',
      border: '#d6c4a8', input: '#d6c4a8', ring: '#92400e',
      radius: '0.75rem',
    },
    dark: {
      background: '#1c1410', foreground: '#f5e6d3',
      card: '#2a1f17', 'card-foreground': '#f5e6d3',
      popover: '#2a1f17', 'popover-foreground': '#f5e6d3',
      primary: '#fbbf24', 'primary-foreground': '#1c1410',
      secondary: '#44301a', 'secondary-foreground': '#fde68a',
      muted: '#33261a', 'muted-foreground': '#c9a87c',
      accent: '#44301a', 'accent-foreground': '#fde68a',
      destructive: '#f87171', 'destructive-foreground': '#1c1410',
      border: '#44301a', input: '#44301a', ring: '#fbbf24',
      radius: '0.75rem',
    },
  },
  {
    name: 'Twitter',
    light: {
      background: '#ffffff', foreground: '#0f1419',
      card: '#f7f9f9', 'card-foreground': '#0f1419',
      popover: '#ffffff', 'popover-foreground': '#0f1419',
      primary: '#1d9bf0', 'primary-foreground': '#ffffff',
      secondary: '#0f1419', 'secondary-foreground': '#ffffff',
      muted: '#eff3f4', 'muted-foreground': '#536471',
      accent: '#e1eef6', 'accent-foreground': '#1d9bf0',
      destructive: '#f4212e', 'destructive-foreground': '#ffffff',
      border: '#cfd9de', input: '#cfd9de', ring: '#1d9bf0',
      radius: '1.5rem',
    },
    dark: {
      background: '#000000', foreground: '#e7e9ea',
      card: '#16181c', 'card-foreground': '#e7e9ea',
      popover: '#16181c', 'popover-foreground': '#e7e9ea',
      primary: '#1d9bf0', 'primary-foreground': '#ffffff',
      secondary: '#e7e9ea', 'secondary-foreground': '#0f1419',
      muted: '#202327', 'muted-foreground': '#71767b',
      accent: '#031018', 'accent-foreground': '#1d9bf0',
      destructive: '#f4212e', 'destructive-foreground': '#ffffff',
      border: '#2f3336', input: '#2f3336', ring: '#1d9bf0',
      radius: '1.5rem',
    },
  },
  {
    name: 'Claude',
    light: {
      background: '#f5f0e8', foreground: '#2d2418',
      card: '#faf6f0', 'card-foreground': '#2d2418',
      popover: '#faf6f0', 'popover-foreground': '#2d2418',
      primary: '#c55a30', 'primary-foreground': '#ffffff',
      secondary: '#e8dccb', 'secondary-foreground': '#5c4a35',
      muted: '#ece3d5', 'muted-foreground': '#8a7560',
      accent: '#e0d2be', 'accent-foreground': '#5c4a35',
      destructive: '#cc3333', 'destructive-foreground': '#ffffff',
      border: '#d4c4ac', input: '#d4c4ac', ring: '#c55a30',
      radius: '0.5rem',
    },
    dark: {
      background: '#1e1b16', foreground: '#e8e0d4',
      card: '#2a2620', 'card-foreground': '#e8e0d4',
      popover: '#2a2620', 'popover-foreground': '#e8e0d4',
      primary: '#e08a5e', 'primary-foreground': '#1e1b16',
      secondary: '#3a342a', 'secondary-foreground': '#e8e0d4',
      muted: '#332e26', 'muted-foreground': '#b0a594',
      accent: '#443d32', 'accent-foreground': '#e8e0d4',
      destructive: '#e06666', 'destructive-foreground': '#ffffff',
      border: '#443d32', input: '#443d32', ring: '#e08a5e',
      radius: '0.5rem',
    },
  },
]

type ThemeMode = 'light' | 'dark'

interface ThemeState {
  mode: ThemeMode
  lightOverrides: ThemeVarValues
  darkOverrides: ThemeVarValues
  activePreset: string | null

  setMode: (mode: ThemeMode) => void
  toggleMode: () => void
  setVar: (key: ThemeVar, value: string) => void
  applyPreset: (preset: ThemePreset) => void
  reset: () => void
}

export const useThemeStore = create<ThemeState>((set, get) => ({
  mode: 'light',
  lightOverrides: {},
  darkOverrides: {},
  activePreset: null,

  setMode: (mode) => set({ mode }),
  toggleMode: () => set((s) => ({ mode: s.mode === 'light' ? 'dark' : 'light' })),

  setVar: (key, value) => {
    const mode = get().mode
    if (mode === 'light') {
      set((s) => ({ lightOverrides: { ...s.lightOverrides, [key]: value }, activePreset: null }))
    } else {
      set((s) => ({ darkOverrides: { ...s.darkOverrides, [key]: value }, activePreset: null }))
    }
  },

  applyPreset: (preset) =>
    set({
      lightOverrides: { ...preset.light },
      darkOverrides: { ...preset.dark },
      activePreset: preset.name,
    }),

  reset: () => set({ lightOverrides: {}, darkOverrides: {}, activePreset: null }),
}))

/** Get the currently active overrides for the current mode */
export function useActiveOverrides(): ThemeVarValues {
  const mode = useThemeStore((s) => s.mode)
  const light = useThemeStore((s) => s.lightOverrides)
  const dark = useThemeStore((s) => s.darkOverrides)
  return mode === 'light' ? light : dark
}

function remapActionNodeIds(action: EventAction, idMap: Map<string, string>): EventAction {
  if (action.type === 'setComponentState') {
    return {
      ...action,
      nodeId: idMap.get(action.nodeId) ?? action.nodeId,
    }
  }
  if (action.type === 'condition') {
    return {
      ...action,
      thenActions: action.thenActions.map((a) => remapActionNodeIds(a, idMap)),
      elseActions: action.elseActions.map((a) => remapActionNodeIds(a, idMap)),
    }
  }
  return action
}

let nodeIdCounter = 0
function generateId(): string {
  return `node_${++nodeIdCounter}_${Date.now().toString(36)}`
}

const ROOT_NODE_ID = 'ROOT'

function createRootNode(): AppNode {
  return {
    id: ROOT_NODE_ID,
    type: 'Container',
    parentId: null,
    childIds: [],
    linkedNodes: {},
    props: {
      display: 'flex',
      width: '',
      height: '100%',
      minHeight: '',
      flexDirection: 'column',
      alignItems: 'stretch',
      justifyContent: 'flex-start',
      fillSpace: 'yes',
      paddingTop: '0',
      paddingRight: '0',
      paddingBottom: '0',
      paddingLeft: '0',
      marginTop: '0',
      marginRight: '0',
      marginBottom: '0',
      marginLeft: '0',
      gap: '12',
      overflow: 'auto',
      position: 'static',
      background: 'transparent',
      color: '',
      opacity: '100',
      borderRadius: '0',
      borderWidth: '0',
      borderStyle: 'none',
      shadow: '0',
      cursor: 'default',
      customStyles: '',
    },
    isCanvas: true,
    hidden: false,
  }
}

interface AppDocumentState {
  nodes: Record<string, AppNode>
  rootNodeId: string

  // Persistence
  appId: string | null
  appName: string

  // Runtime definitions
  storeDefinitions: StoreDefinition[]
  webhookDefinitions: WebhookDefinition[]

  addNode: (type: string, parentId: string, index?: number) => string | null
  deleteNode: (id: string) => void
  deleteNodes: (ids: string[]) => void
  moveNode: (id: string, newParentId: string, index: number) => void
  updateNodeProps: (id: string, updates: Record<string, unknown>) => void
  updateNodeEventHandlers: (id: string, handlers: EventHandlerConfig[]) => void
  duplicateNode: (id: string) => string | null
  duplicateNodes: (ids: string[]) => string[]
  toggleHidden: (id: string) => void
  wrapInContainer: (nodeIds: string[]) => string | null
  unwrapNode: (id: string) => void
  reorderNode: (id: string, direction: 'up' | 'down' | 'top' | 'bottom') => void
  reset: () => void

  // Persistence actions
  setAppId: (id: string) => void
  setAppName: (name: string) => void
  loadDocument: (data: { nodes: Record<string, AppNode>; rootNodeId: string; appId: string; appName: string; storeDefinitions?: StoreDefinition[]; webhookDefinitions?: WebhookDefinition[]; styleClasses?: StyleClass[] }) => void
  toDefinition: () => { nodes: Record<string, AppNode>; rootNodeId: string; storeDefinitions: StoreDefinition[]; webhookDefinitions: WebhookDefinition[]; styleClasses: StyleClass[] }

  // Template insertion
  insertTemplate: (template: AppTemplate, parentId: string, index?: number) => string | null

  // Store definitions CRUD
  addStoreDefinition: (def: StoreDefinition) => void
  updateStoreDefinition: (id: string, updates: Partial<StoreDefinition>) => void
  removeStoreDefinition: (id: string) => void

  // Webhook definitions CRUD
  addWebhookDefinition: (def: WebhookDefinition) => void
  updateWebhookDefinition: (id: string, updates: Partial<WebhookDefinition>) => void
  removeWebhookDefinition: (id: string) => void

  // Breakpoint overrides
  updateNodeBreakpointProps: (id: string, breakpoint: BreakpointId, updates: Record<string, unknown>) => void
  clearNodeBreakpointProp: (id: string, breakpoint: BreakpointId, propKey: string) => void

  // Element state styles
  updateNodeStateStyle: (id: string, state: ElementState, updates: Record<string, unknown>) => void
  clearNodeStateStyle: (id: string, state: ElementState, propKey: string) => void

  // Style classes on nodes
  addClassToNode: (nodeId: string, classId: string) => void
  removeClassFromNode: (nodeId: string, classId: string) => void

}

export const useAppDocumentStore = create<AppDocumentState>()(
  temporal(
    immer((set, get) => ({
      nodes: { [ROOT_NODE_ID]: createRootNode() },
      rootNodeId: ROOT_NODE_ID,
      appId: null,
      appName: 'Untitled App',
      storeDefinitions: [],
      webhookDefinitions: [],

      addNode: (type, parentId, index) => {
        const def = getDefinition(type)
        if (!def) return null

        const parent = get().nodes[parentId]
        if (!parent?.isCanvas) return null

        const id = generateId()
        const node: AppNode = {
          id,
          type,
          parentId,
          childIds: [],
          linkedNodes: {},
          props: { ...def.meta.defaultProps },
          isCanvas: def.meta.isContainer ?? false,
          hidden: false,
        }

        set((state) => {
          state.nodes[id] = node
          const parentNode = state.nodes[parentId]
          if (index !== undefined && index >= 0) {
            parentNode.childIds.splice(index, 0, id)
          } else {
            parentNode.childIds.push(id)
          }

          // Auto-create default children (e.g. Tabs creates panel containers)
          if (def.meta.defaultChildren?.length) {
            for (const childSpec of def.meta.defaultChildren) {
              const childDef = getDefinition(childSpec.type)
              if (!childDef) continue
              const childId = generateId()
              state.nodes[childId] = {
                id: childId,
                type: childSpec.type,
                parentId: id,
                childIds: [],
                linkedNodes: {},
                props: { ...childDef.meta.defaultProps, ...childSpec.props },
                isCanvas: childDef.meta.isContainer ?? false,
                hidden: false,
              }
              state.nodes[id].childIds.push(childId)
            }
          }
        })

        return id
      },

      insertTemplate: (template, parentId, index) => {
        const parent = get().nodes[parentId]
        if (!parent?.isCanvas) return null

        // Remap template IDs to unique IDs
        const idMap = new Map<string, string>()
        for (const templateId of Object.keys(template.nodes)) {
          idMap.set(templateId, generateId())
        }

        set((draft) => {
          // Create all nodes with remapped IDs
          for (const [templateId, templateNode] of Object.entries(template.nodes)) {
            const newId = idMap.get(templateId)!
            const node: AppNode = {
              id: newId,
              type: templateNode.type,
              parentId: templateNode.parentId ? idMap.get(templateNode.parentId) ?? null : parentId,
              childIds: templateNode.childIds.map((cid) => idMap.get(cid) ?? cid),
              linkedNodes: {},
              props: { ...templateNode.props },
              isCanvas: templateNode.isCanvas,
              hidden: false,
            }

            // Remap node references inside event handlers
            if (node.props.__eventHandlers) {
              const handlers = JSON.parse(JSON.stringify(node.props.__eventHandlers)) as EventHandlerConfig[]
              for (const handler of handlers) {
                for (const action of handler.actions) {
                  if ('nodeId' in action && typeof action.nodeId === 'string' && idMap.has(action.nodeId)) {
                    (action as { nodeId: string }).nodeId = idMap.get(action.nodeId)!
                  }
                  // Remap component references in expression strings
                  if ('value' in action && typeof action.value === 'string') {
                    let val = action.value as string
                    for (const [oldId, newId] of idMap) {
                      val = val.replace(new RegExp(`components\\.${oldId}\\.`, 'g'), `components.${newId}.`)
                    }
                    ;(action as { value: string }).value = val
                  }
                }
              }
              node.props.__eventHandlers = handlers
            }

            draft.nodes[newId] = node
          }

          // Add root template node to parent's children
          const rootNewId = idMap.get(template.rootId)!
          const parentNode = draft.nodes[parentId]
          if (index !== undefined && index >= 0) {
            parentNode.childIds.splice(index, 0, rootNewId)
          } else {
            parentNode.childIds.push(rootNewId)
          }
          // Fix: root node's parent should be the actual parent
          draft.nodes[rootNewId].parentId = parentId

          // Add store definitions
          if (template.stores) {
            for (const storeDef of template.stores) {
              const exists = draft.storeDefinitions.some((s) => s.name === storeDef.name)
              if (!exists) {
                draft.storeDefinitions.push({ ...storeDef })
              }
            }
          }

          // Add webhook definitions (with remapped component references)
          if (template.webhooks) {
            const remapExpressions = (str: string) => {
              let result = str
              for (const [oldId, newId] of idMap) {
                result = result.replace(new RegExp(`components\\.${oldId}\\.`, 'g'), `components.${newId}.`)
              }
              return result
            }

            for (const webhookDef of template.webhooks) {
              const exists = draft.webhookDefinitions.some((w) => w.id === webhookDef.id)
              if (!exists) {
                const remapped = { ...webhookDef }
                remapped.url = remapExpressions(remapped.url)
                remapped.body = remapExpressions(remapped.body)
                const remappedHeaders: Record<string, string> = {}
                for (const [k, v] of Object.entries(remapped.headers)) {
                  remappedHeaders[k] = remapExpressions(v)
                }
                remapped.headers = remappedHeaders
                draft.webhookDefinitions.push(remapped)
              }
            }
          }
        })

        return idMap.get(template.rootId) ?? null
      },

      deleteNode: (id) => {
        const state = get()
        const node = state.nodes[id]
        if (!node || id === ROOT_NODE_ID) return

        const descendants = getDescendants(state.nodes, id)
        const idsToDelete = [id, ...descendants.map((d) => d.id)]

        set((draft) => {
          // Remove from parent
          if (node.parentId) {
            const parent = draft.nodes[node.parentId]
            parent.childIds = parent.childIds.filter((cid) => cid !== id)
          }
          // Delete all nodes
          for (const deleteId of idsToDelete) {
            delete draft.nodes[deleteId]
          }
        })
      },

      deleteNodes: (ids) => {
        const state = get()
        // Filter out root and invalid nodes, deduplicate
        const validIds = [...new Set(ids)].filter(
          (id) => id !== ROOT_NODE_ID && state.nodes[id]
        )
        if (validIds.length === 0) return

        // Collect all descendants
        const allIdsToDelete = new Set<string>()
        for (const id of validIds) {
          allIdsToDelete.add(id)
          for (const desc of getDescendants(state.nodes, id)) {
            allIdsToDelete.add(desc.id)
          }
        }

        set((draft) => {
          // Remove from parents
          for (const id of validIds) {
            const node = draft.nodes[id]
            if (node?.parentId) {
              const parent = draft.nodes[node.parentId]
              if (parent) {
                parent.childIds = parent.childIds.filter((cid) => !allIdsToDelete.has(cid))
              }
            }
          }
          // Delete all
          for (const id of allIdsToDelete) {
            delete draft.nodes[id]
          }
        })
      },

      moveNode: (id, newParentId, index) => {
        const state = get()
        const node = state.nodes[id]
        const newParent = state.nodes[newParentId]
        if (!node || !newParent?.isCanvas || id === ROOT_NODE_ID) return
        if (id === newParentId) return

        // Prevent moving into own descendant
        let current = state.nodes[newParentId]
        while (current?.parentId) {
          if (current.parentId === id) return
          current = state.nodes[current.parentId]
        }

        set((draft) => {
          const oldParentId = node.parentId
          let adjustedIndex = index

          // Remove from old parent
          if (oldParentId) {
            const oldParent = draft.nodes[oldParentId]
            const currentIndex = oldParent.childIds.indexOf(id)
            oldParent.childIds = oldParent.childIds.filter((cid) => cid !== id)

            // Adjust index when moving within the same parent (CraftJS pattern)
            if (oldParentId === newParentId && currentIndex < index) {
              adjustedIndex = index - 1
            }
          }

          // Add to new parent
          draft.nodes[id].parentId = newParentId
          const parent = draft.nodes[newParentId]
          if (adjustedIndex >= 0 && adjustedIndex <= parent.childIds.length) {
            parent.childIds.splice(adjustedIndex, 0, id)
          } else {
            parent.childIds.push(id)
          }
        })
      },

      updateNodeProps: (id, updates) => {
        set((draft) => {
          const node = draft.nodes[id]
          if (!node) return
          Object.assign(node.props, updates)
        })
      },

      updateNodeEventHandlers: (id, handlers) => {
        set((draft) => {
          const node = draft.nodes[id]
          if (!node) return
          node.props.__eventHandlers = handlers
        })
      },

      duplicateNode: (id) => {
        const state = get()
        const node = state.nodes[id]
        if (!node || !node.parentId || id === ROOT_NODE_ID) return null

        // Deep clone the subtree
        const idMap = new Map<string, string>()

        function cloneNode(sourceId: string): AppNode {
          const source = state.nodes[sourceId]
          const newId = generateId()
          idMap.set(sourceId, newId)

          const clonedChildIds = source.childIds.map((childId) => {
            cloneNode(childId)
            return idMap.get(childId)!
          })

          return {
            ...source,
            id: newId,
            childIds: clonedChildIds,
            props: { ...source.props },
            linkedNodes: { ...source.linkedNodes },
          }
        }

        const clonedRoot = cloneNode(id)
        clonedRoot.parentId = node.parentId

        // Fix parent refs for descendants
        const allCloned: AppNode[] = [clonedRoot]
        const stack = [clonedRoot]
        while (stack.length > 0) {
          const n = stack.pop()!
          for (const childId of n.childIds) {
            const childOrigId = [...idMap.entries()].find(
              ([, v]) => v === childId
            )?.[0]
            if (childOrigId) {
              const clonedChild = {
                ...state.nodes[childOrigId],
                id: childId,
                parentId: n.id,
                childIds: state.nodes[childOrigId].childIds.map(
                  (cid) => idMap.get(cid) ?? cid
                ),
                props: { ...state.nodes[childOrigId].props },
                linkedNodes: { ...state.nodes[childOrigId].linkedNodes },
              }
              allCloned.push(clonedChild)
              stack.push(clonedChild)
            }
          }
        }

        // Remap node ID references in event handlers for all cloned nodes
        for (const cloned of allCloned) {
          const handlers = cloned.props.__eventHandlers as EventHandlerConfig[] | undefined
          if (handlers?.length) {
            cloned.props.__eventHandlers = handlers.map((h) => ({
              ...h,
              actions: h.actions.map((a) => remapActionNodeIds(a, idMap)),
            }))
          }
        }

        set((draft) => {
          for (const cloned of allCloned) {
            draft.nodes[cloned.id] = cloned
          }
          // Insert after the original
          const parent = draft.nodes[node.parentId!]
          const originalIndex = parent.childIds.indexOf(id)
          parent.childIds.splice(originalIndex + 1, 0, clonedRoot.id)
        })

        return clonedRoot.id
      },

      toggleHidden: (id) => {
        set((draft) => {
          const node = draft.nodes[id]
          if (node && id !== ROOT_NODE_ID) {
            node.hidden = !node.hidden
          }
        })
      },

      duplicateNodes: (ids) => {
        const newIds: string[] = []
        for (const id of ids) {
          const newId = get().duplicateNode(id)
          if (newId) newIds.push(newId)
        }
        return newIds
      },

      wrapInContainer: (nodeIds) => {
        const state = get()
        if (nodeIds.length === 0) return null

        // All nodes must share the same parent
        const firstNode = state.nodes[nodeIds[0]]
        if (!firstNode?.parentId) return null
        const parentId = firstNode.parentId
        for (const id of nodeIds) {
          if (state.nodes[id]?.parentId !== parentId) return null
        }

        const parent = state.nodes[parentId]
        if (!parent) return null

        // Find insertion index (position of first selected node)
        const indices = nodeIds.map((id) => parent.childIds.indexOf(id)).filter((i) => i >= 0)
        const minIndex = Math.min(...indices)

        const containerId = generateId()

        set((draft) => {
          // Create the wrapper container
          const containerDef = getDefinition('Container')
          const container: AppNode = {
            id: containerId,
            type: 'Container',
            parentId,
            childIds: [...nodeIds],
            linkedNodes: {},
            props: { ...(containerDef?.meta.defaultProps ?? {}) },
            isCanvas: true,
            hidden: false,
          }
          draft.nodes[containerId] = container

          // Reparent selected nodes
          for (const id of nodeIds) {
            draft.nodes[id].parentId = containerId
          }

          // Update parent: remove selected nodes, insert container at minIndex
          const parentNode = draft.nodes[parentId]
          parentNode.childIds = parentNode.childIds.filter((cid) => !nodeIds.includes(cid))
          parentNode.childIds.splice(minIndex, 0, containerId)
        })

        return containerId
      },

      unwrapNode: (id) => {
        const state = get()
        const node = state.nodes[id]
        if (!node || !node.parentId || id === ROOT_NODE_ID) return
        if (node.childIds.length === 0) return

        const parentId = node.parentId

        set((draft) => {
          const parent = draft.nodes[parentId]
          const nodeIndex = parent.childIds.indexOf(id)
          if (nodeIndex < 0) return

          // Move children to parent at node's position
          const childIds = [...draft.nodes[id].childIds]
          for (const childId of childIds) {
            draft.nodes[childId].parentId = parentId
          }

          // Replace node with its children in parent
          parent.childIds.splice(nodeIndex, 1, ...childIds)

          // Delete the unwrapped node
          delete draft.nodes[id]
        })
      },

      reorderNode: (id, direction) => {
        const state = get()
        const node = state.nodes[id]
        if (!node?.parentId || id === ROOT_NODE_ID) return

        set((draft) => {
          const parent = draft.nodes[node.parentId!]
          const idx = parent.childIds.indexOf(id)
          if (idx < 0) return

          switch (direction) {
            case 'up':
              if (idx > 0) {
                parent.childIds.splice(idx, 1)
                parent.childIds.splice(idx - 1, 0, id)
              }
              break
            case 'down':
              if (idx < parent.childIds.length - 1) {
                parent.childIds.splice(idx, 1)
                parent.childIds.splice(idx + 1, 0, id)
              }
              break
            case 'top':
              if (idx > 0) {
                parent.childIds.splice(idx, 1)
                parent.childIds.unshift(id)
              }
              break
            case 'bottom':
              if (idx < parent.childIds.length - 1) {
                parent.childIds.splice(idx, 1)
                parent.childIds.push(id)
              }
              break
          }
        })
      },

      reset: () => {
        nodeIdCounter = 0
        set({
          nodes: { [ROOT_NODE_ID]: createRootNode() },
          rootNodeId: ROOT_NODE_ID,
          appId: null,
          appName: 'Untitled App',
          storeDefinitions: [],
          webhookDefinitions: [],
        })
      },

      setAppId: (id) => set({ appId: id }),
      setAppName: (name) => set({ appName: name }),

      loadDocument: (data) => {
        nodeIdCounter = 0
        // Merge each node's props with component defaults so imported
        // documents with sparse props still render correctly.
        const mergedNodes: Record<string, AppNode> = {}
        for (const [id, node] of Object.entries(data.nodes)) {
          const def = getDefinition(node.type)
          mergedNodes[id] = {
            ...node,
            props: def ? { ...def.meta.defaultProps, ...node.props } : node.props,
          }
        }
        set({
          nodes: mergedNodes,
          rootNodeId: data.rootNodeId,
          appId: data.appId,
          appName: data.appName,
          storeDefinitions: data.storeDefinitions ?? [],
          webhookDefinitions: data.webhookDefinitions ?? [],
        })
        // Load style classes into separate store
        if (data.styleClasses?.length) {
                    useStyleClassStore.getState().loadClasses(data.styleClasses)
        }
      },

      toDefinition: () => {
        const state = get()
                return {
          nodes: state.nodes,
          rootNodeId: state.rootNodeId,
          storeDefinitions: state.storeDefinitions,
          webhookDefinitions: state.webhookDefinitions,
          styleClasses: useStyleClassStore.getState().classes,
        }
      },

      addStoreDefinition: (def) => {
        set((draft) => { draft.storeDefinitions.push(def) })
      },
      updateStoreDefinition: (id, updates) => {
        set((draft) => {
          const idx = draft.storeDefinitions.findIndex((s) => s.id === id)
          if (idx !== -1) Object.assign(draft.storeDefinitions[idx], updates)
        })
      },
      removeStoreDefinition: (id) => {
        set((draft) => {
          draft.storeDefinitions = draft.storeDefinitions.filter((s) => s.id !== id)
        })
      },

      addWebhookDefinition: (def) => {
        set((draft) => { draft.webhookDefinitions.push(def) })
      },
      updateWebhookDefinition: (id, updates) => {
        set((draft) => {
          const idx = draft.webhookDefinitions.findIndex((w) => w.id === id)
          if (idx !== -1) Object.assign(draft.webhookDefinitions[idx], updates)
        })
      },
      removeWebhookDefinition: (id) => {
        set((draft) => {
          draft.webhookDefinitions = draft.webhookDefinitions.filter((w) => w.id !== id)
        })
      },

      updateNodeBreakpointProps: (id, breakpoint, updates) => {
        set((draft) => {
          const node = draft.nodes[id]
          if (!node) return
          if (!node.breakpointOverrides) node.breakpointOverrides = {}
          if (!node.breakpointOverrides[breakpoint]) node.breakpointOverrides[breakpoint] = {}
          Object.assign(node.breakpointOverrides[breakpoint]!, updates)
        })
      },

      clearNodeBreakpointProp: (id, breakpoint, propKey) => {
        set((draft) => {
          const node = draft.nodes[id]
          if (!node?.breakpointOverrides?.[breakpoint]) return
          delete node.breakpointOverrides[breakpoint]![propKey]
          // Clean up empty objects
          if (Object.keys(node.breakpointOverrides[breakpoint]!).length === 0) {
            delete node.breakpointOverrides[breakpoint]
          }
        })
      },

      updateNodeStateStyle: (id, state, updates) => {
        set((draft) => {
          const node = draft.nodes[id]
          if (!node) return
          if (!node.stateStyles) node.stateStyles = {}
          if (!node.stateStyles[state]) node.stateStyles[state] = {}
          Object.assign(node.stateStyles[state]!, updates)
        })
      },

      clearNodeStateStyle: (id, state, propKey) => {
        set((draft) => {
          const node = draft.nodes[id]
          if (!node?.stateStyles?.[state]) return
          delete node.stateStyles[state]![propKey]
          if (Object.keys(node.stateStyles[state]!).length === 0) {
            delete node.stateStyles[state]
          }
        })
      },

      addClassToNode: (nodeId, classId) => {
        set((draft) => {
          const node = draft.nodes[nodeId]
          if (!node) return
          if (!node.classIds) node.classIds = []
          if (!node.classIds.includes(classId)) node.classIds.push(classId)
        })
      },

      removeClassFromNode: (nodeId, classId) => {
        set((draft) => {
          const node = draft.nodes[nodeId]
          if (!node?.classIds) return
          node.classIds = node.classIds.filter((id) => id !== classId)
          if (node.classIds.length === 0) delete node.classIds
        })
      },

    })),
    { limit: 50 }
  )
)
