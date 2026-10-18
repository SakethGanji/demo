/**
 * The stage (PLAN-MOTION-UI §3.3): the graph the one canvas shows while an AI build plays or is under review.
 * It lives in a store, not in component state, so it survives the route change after Keep on a new workflow.
 * The draft in `workflowStore` is never touched by playback.
 */
import { create } from 'zustand'
import type { Edge, Node, NodeChange } from '@xyflow/react'
import type { DiffMark } from '@/features/workflow-editor/stores/historyStore'
import type { WorkflowNodeData } from '@/features/workflow-editor/types/workflow'
import type { AnimState, TestStatus, XY } from './buildOps'

export type StageMode = 'off' | 'blueprint' | 'building' | 'review' | 'settling' | 'reversing'
export type StagePlayback = 'idle' | 'playing' | 'done'

/** Node data on the stage: the editor's data plus the animation state the canvas layers read. */
export type StageNodeData = WorkflowNodeData & {
  anim?: AnimState
  /** changed field names (never values) shown in the node's change chip */
  animFields?: string[]
  /** beat time scale k (1 = nominal); the canvas layers multiply their timings by it */
  animScale?: number
  /** compact beat: frame + icon + lock only */
  animCompact?: boolean
  /** lock / frame tone: warning after a morph */
  animTone?: 'primary' | 'warning'
}
export type StageNode = Node<StageNodeData>
export type StageEdge = Edge<{ anim?: AnimState; drawScale?: number } & Record<string, unknown>>

/** Where the AI marker is and what it says it is doing (field names only, never values). */
export interface StagePresence {
  visible: boolean
  /** node it sits at, null = canvas centre */
  node: string | null
  label: string | null
  /** flow-coordinates fallback when `node` is not on the canvas yet */
  at: XY | null
}

/** A test spark travelling a path of node names; `id` changes per run so an effect can key on it. */
export interface StageTestPath { id: number; nodes: string[]; status: TestStatus; hopMs: number; failAt?: string }
export interface BuildHead { seq: number; node: string; kind: 'construct' | 'deconstruct' | 'morph' | 'test' | 'fail'; at: XY; compact?: boolean }
export interface StageLog { round: number; built: Record<string, true>; linked: boolean; tests: number }
export const EMPTY_LOG: StageLog = { round: -1, built: {}, linked: false, tests: 0 }

export const IDLE_PRESENCE: StagePresence = { visible: false, node: null, label: null, at: null }

export interface StageState {
  active: boolean
  mode: StageMode
  proposalId: string | null
  nodes: StageNode[]
  edges: StageEdge[]
  /** node name (= id) -> diff tint; flows into `historyStore.setDiff` */
  marks: Record<string, DiffMark>
  /** edge id -> diff tint (removed edges stay as dashed ghosts) */
  edgeMarks: Record<string, DiffMark>
  /** test badge per node name, set while tests play */
  badges: Record<string, TestStatus>
  testPath: StageTestPath | null
  presence: StagePresence
  /** node name the pointer is over in the log / review rows (focal glow) */
  hoverNode: string | null
  /** camera follows the build until the user pans or zooms */
  follow: boolean
  playback: StagePlayback
  /** the next round's final positions: the camera frames them together with the nodes on stage; `seq` changes per request */
  frame: { seq: number; points: XY[] } | null
  /** bumped by the Skip button / Esc; the player finishes the current round when it changes */
  skipSeq: number
  buildHead: BuildHead | null
  log: StageLog
  progress: { done: number; total: number } | null
  /** bumped when a played build reaches review: the canvas plays the review sweep */
  sweep: number

  /** Start a stage over `nodes` / `edges` (the base draft for a modify, empty for a new workflow). */
  begin: (proposalId: string, mode: Exclude<StageMode, 'off'>, nodes?: StageNode[], edges?: StageEdge[]) => void
  setMode: (mode: StageMode) => void
  setPlayback: (p: StagePlayback) => void
  setGraph: (nodes: StageNode[], edges: StageEdge[]) => void
  setNodes: (update: (nodes: StageNode[]) => StageNode[]) => void
  setEdges: (update: (edges: StageEdge[]) => StageEdge[]) => void
  /** shallow-merge `data` into one node (keeps `measured`) */
  patchNodeData: (id: string, data: Partial<StageNodeData>) => void
  /** move several nodes at once (one write per frame) */
  setPositions: (positions: Record<string, XY>) => void
  setMarks: (marks: Record<string, DiffMark>, edgeMarks?: Record<string, DiffMark>) => void
  setBadge: (node: string, status: TestStatus | null) => void
  clearBadges: () => void
  setTestPath: (p: StageTestPath | null) => void
  setPresence: (p: Partial<StagePresence>) => void
  setHoverNode: (name: string | null) => void
  setFollow: (on: boolean) => void
  requestSkip: () => void
  requestFrame: (points: XY[]) => void
  setHead: (h: Omit<BuildHead, 'seq'> | null) => void
  patchLog: (p: Partial<StageLog>) => void
  markBuilt: (node: string) => void
  setProgress: (p: { done: number; total: number } | null) => void
  bumpSweep: () => void
  /** React Flow `onNodesChange` while staged: only measurements are applied */
  applyDimensions: (changes: NodeChange[]) => void
  end: () => void
}

const initial = {
  active: false, mode: 'off' as StageMode, proposalId: null as string | null,
  nodes: [] as StageNode[], edges: [] as StageEdge[],
  marks: {} as Record<string, DiffMark>, edgeMarks: {} as Record<string, DiffMark>,
  badges: {} as Record<string, TestStatus>, testPath: null as StageTestPath | null,
  presence: IDLE_PRESENCE, hoverNode: null as string | null, follow: true,
  playback: 'idle' as StagePlayback, skipSeq: 0,
  buildHead: null as BuildHead | null, log: EMPTY_LOG, progress: null as { done: number; total: number } | null, sweep: 0, frame: null as { seq: number; points: XY[] } | null,
}

export const useStageStore = create<StageState>((set) => ({
  ...initial,

  begin: (proposalId, mode, nodes = [], edges = []) => set({ ...initial, active: true, mode, proposalId, nodes, edges }),
  setMode: (mode) => set({ mode, active: mode !== 'off' }),
  setPlayback: (playback) => set({ playback }),
  setGraph: (nodes, edges) => set({ nodes, edges }),
  setNodes: (update) => set((s) => ({ nodes: update(s.nodes) })),
  setEdges: (update) => set((s) => ({ edges: update(s.edges) })),
  patchNodeData: (id, data) => set((s) => ({
    nodes: s.nodes.map((n) => (n.id === id ? { ...n, data: { ...n.data, ...data } } : n)),
  })),
  setPositions: (positions) => set((s) => ({
    nodes: s.nodes.map((n) => (positions[n.id] ? { ...n, position: positions[n.id] } : n)),
  })),
  setMarks: (marks, edgeMarks = {}) => set({ marks, edgeMarks }),
  setBadge: (node, status) => set((s) => {
    const badges = { ...s.badges }
    if (status) badges[node] = status
    else delete badges[node]
    return { badges }
  }),
  clearBadges: () => set({ badges: {}, testPath: null }),
  setTestPath: (testPath) => set({ testPath }),
  setPresence: (p) => set((s) => ({ presence: { ...s.presence, ...p } })),
  setHoverNode: (hoverNode) => set({ hoverNode }),
  setFollow: (follow) => set({ follow }),
  requestSkip: () => set((s) => ({ skipSeq: s.skipSeq + 1 })),
  requestFrame: (points) => set((s) => ({ frame: { seq: (s.frame?.seq ?? 0) + 1, points } })),
  setHead: (h) => set((s) => ({ buildHead: h ? { ...h, seq: (s.buildHead?.seq ?? 0) + 1 } : null })),
  patchLog: (p) => set((s) => ({ log: { ...s.log, ...p } })),
  markBuilt: (node) => set((s) => ({ log: { ...s.log, built: { ...s.log.built, [node]: true } } })),
  setProgress: (progress) => set({ progress }),
  bumpSweep: () => set((s) => ({ sweep: s.sweep + 1 })),
  applyDimensions: (changes) => set((s) => {
    const dims = changes.filter((c): c is Extract<NodeChange, { type: 'dimensions' }> => c.type === 'dimensions' && !!c.dimensions)
    if (!dims.length) return s
    const by = new Map(dims.map((c) => [c.id, c.dimensions!]))
    return { nodes: s.nodes.map((n) => (by.has(n.id) ? { ...n, measured: by.get(n.id) } : n)) }
  }),
  end: () => set({ ...initial, skipSeq: 0 }),
}))
