/**
 * Per-module editor documents (UI-DESIGN §4.2).
 *
 * A document is the editor text for one module plus what it was based on (`baseSha`, the saved
 * `source_sha` the edits started from), the AI draft state and the prompt history. Dirty
 * documents survive reload via sessionStorage `luna:draft:<projectId>:<module>` (§4.2 step 7).
 */
import { create } from 'zustand'
import type { DeclaredTable, GenerateAnalysis, SelfTestResult, RunDraftResult, RouteTestResult } from '@/shared/api/types'

export interface PromptTurn {
  role: 'user' | 'assistant'
  content: string
}

export interface DraftState {
  /** text in the editor before the AI draft was applied (Discard restores it) */
  previousSource: string
  summary: string
  analysis: GenerateAnalysis | null
  selfTest: SelfTestResult[] | null
  attempts: number
  moduleName: string
  /** declared tables the draft needs; `create` = the "Create table on save" checkbox */
  tables: Array<DeclaredTable & { create: boolean }>
  /** generation ended `generation_invalid`: shown with its diagnostics, "n problems left" */
  invalid?: boolean
}

export interface RecentTest {
  id: string
  at: number
  method: string
  path: string
  routeName: string
  request: { params: Record<string, string>; query: Record<string, string>; headers: Record<string, string>; body: string }
  result: RouteTestResult | RunDraftResult
  draft: boolean
}

export interface ModuleDoc {
  projectId: string
  module: string
  /** null for a module that has never been saved */
  moduleId: string | null
  source: string
  /** saved source the edits are based on ('' for new modules) */
  savedSource: string
  baseSha: string | null
  isNew: boolean
  draft: DraftState | null
  prompts: PromptTurn[]
  recentTests: RecentTest[]
  /** restored from sessionStorage with a base_sha that no longer matches the saved module */
  staleRestore?: boolean
  /** stable id for the editor instance; survives renames (~new → name) so it never remounts */
  editorKey: string
}

const storageKey = (projectId: string, module: string) => `luna:draft:${projectId}:${module}`

interface PersistShape {
  source: string
  savedSource: string
  baseSha: string | null
  moduleId: string | null
  isNew: boolean
  prompts: PromptTurn[]
  draft: DraftState | null
  editorKey?: string
}

export function loadPersisted(projectId: string, module: string): PersistShape | null {
  try {
    const raw = sessionStorage.getItem(storageKey(projectId, module))
    return raw ? (JSON.parse(raw) as PersistShape) : null
  } catch {
    return null
  }
}

function persist(doc: ModuleDoc) {
  try {
    const dirty = doc.source !== doc.savedSource || doc.draft !== null || doc.isNew
    if (!dirty) {
      sessionStorage.removeItem(storageKey(doc.projectId, doc.module))
      return
    }
    const shape: PersistShape = {
      source: doc.source,
      savedSource: doc.savedSource,
      baseSha: doc.baseSha,
      moduleId: doc.moduleId,
      isNew: doc.isNew,
      prompts: doc.prompts.slice(-12),
      draft: doc.draft,
      editorKey: doc.editorKey,
    }
    sessionStorage.setItem(storageKey(doc.projectId, doc.module), JSON.stringify(shape))
  } catch {
    /* storage full or unavailable: in-memory only */
  }
}

export function clearPersisted(projectId: string, module: string) {
  try {
    sessionStorage.removeItem(storageKey(projectId, module))
  } catch {
    /* ignore */
  }
}

/** All persisted new (never saved) module drafts for a project, so the outline can list them. */
export function persistedNewModules(projectId: string): string[] {
  const out: string[] = []
  try {
    const prefix = `luna:draft:${projectId}:`
    for (let i = 0; i < sessionStorage.length; i++) {
      const k = sessionStorage.key(i)
      if (!k?.startsWith(prefix)) continue
      const v = JSON.parse(sessionStorage.getItem(k) ?? 'null') as PersistShape | null
      if (v?.isNew) out.push(k.slice(prefix.length))
    }
  } catch {
    /* ignore */
  }
  return out
}

export const docKey = (projectId: string, module: string) => `${projectId}:${module}`

interface DocState {
  docs: Record<string, ModuleDoc>
  /** module tabs opened this session, per project */
  open: Record<string, string[]>
  put: (doc: ModuleDoc) => void
  patch: (projectId: string, module: string, patch: Partial<ModuleDoc>) => void
  rename: (projectId: string, from: string, to: string) => void
  remove: (projectId: string, module: string) => void
  openTab: (projectId: string, module: string) => void
  closeTab: (projectId: string, module: string) => void
}

export const useDocStore = create<DocState>((set, get) => ({
  docs: {},
  open: {},
  put: (doc) => {
    persist(doc)
    set((s) => ({ docs: { ...s.docs, [docKey(doc.projectId, doc.module)]: doc } }))
  },
  patch: (projectId, module, patch) => {
    const k = docKey(projectId, module)
    const cur = get().docs[k]
    if (!cur) return
    const next = { ...cur, ...patch }
    persist(next)
    set((s) => ({ docs: { ...s.docs, [k]: next } }))
  },
  rename: (projectId, from, to) => {
    const cur = get().docs[docKey(projectId, from)]
    clearPersisted(projectId, from)
    set((s) => {
      const docs = { ...s.docs }
      delete docs[docKey(projectId, from)]
      if (cur) docs[docKey(projectId, to)] = { ...cur, module: to }
      const open = { ...s.open, [projectId]: (s.open[projectId] ?? []).map((m) => (m === from ? to : m)) }
      return { docs, open }
    })
    const moved = get().docs[docKey(projectId, to)]
    if (moved) persist(moved)
  },
  remove: (projectId, module) => {
    clearPersisted(projectId, module)
    set((s) => {
      const docs = { ...s.docs }
      delete docs[docKey(projectId, module)]
      return { docs, open: { ...s.open, [projectId]: (s.open[projectId] ?? []).filter((m) => m !== module) } }
    })
  },
  openTab: (projectId, module) =>
    set((s) => {
      const cur = s.open[projectId] ?? []
      return cur.includes(module) ? s : { open: { ...s.open, [projectId]: [...cur, module] } }
    }),
  closeTab: (projectId, module) =>
    set((s) => ({ open: { ...s.open, [projectId]: (s.open[projectId] ?? []).filter((m) => m !== module) } })),
}))

export function isDirty(doc: ModuleDoc | undefined): boolean {
  return !!doc && (doc.isNew || doc.source !== doc.savedSource)
}
