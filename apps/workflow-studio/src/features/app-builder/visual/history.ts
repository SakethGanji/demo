/**
 * One linear undo history for every writer of the app document: visual edits record themselves here, and any other change
 * to the document store (a chat turn, a version followed during a build, a revert) is recorded as it lands. A transaction
 * holds whole-file before/after for the files it touched. Undo restores `before` only while each file still equals
 * `after`; otherwise a later change touched it and must be undone first (no rebasing).
 */
import { create } from 'zustand'
import { useAppDocumentStore } from '../stores'
import type { AppFile } from '../sandbox/esbuild-bundler'

export type Origin = 'visual' | 'chat'

export interface FilePatch {
  path: string
  /** null: the file did not exist */
  before: string | null
  after: string | null
}

/** A class change to show in the preview at once: on every element tagged `vsrc`, drop `remove` and add `add`. */
export interface ClassPatch {
  vsrc: string
  add: string[]
  remove: string[]
}

let previewSink: ((p: ClassPatch) => void) | null = null
/** The preview registers here so a class change (and its undo / redo) shows before the re-bundle lands. */
export function setPreviewSink(fn: ((p: ClassPatch) => void) | null) {
  previewSink = fn
}
const showPatches = (ps: ClassPatch[] | undefined, undo: boolean) => {
  if (!ps || !previewSink) return
  const list = undo ? [...ps].reverse() : ps
  for (const p of list) previewSink(undo ? { vsrc: p.vsrc, add: p.remove, remove: p.add } : p)
}

export interface Tx {
  id: number
  origin: Origin
  label: string
  patches: FilePatch[]
  at: number
  /** the class changes in this transaction, for the instant preview */
  preview?: ClassPatch[]
}

interface HistoryState {
  past: Tx[]
  future: Tx[]
  /** why the last undo / redo could not run */
  notice: string | null
  /** bumps on every local change (visual edit, undo, redo) — the cue to save a version */
  localRev: number
  /** apply a visual edit to the document and record it; edits with the same `coalesce` key within a second merge */
  commit: (label: string, patches: FilePatch[], coalesce?: string, preview?: ClassPatch[]) => void
  undo: () => boolean
  redo: () => boolean
  clear: () => void
}

const LIMIT = 100
const COALESCE_MS = 1000
let nextId = 1
/** true while this module writes the document, so the store subscription doesn't record it twice */
let applying = false
let lastCoalesce: { key: string; at: number } | null = null

function filesMap(files: AppFile[] | null): Map<string, string> {
  return new Map((files ?? []).map((f) => [f.path, f.content]))
}

/** Write file contents (null deletes) into the document store, keeping file order. */
function write(changes: Map<string, string | null>) {
  const cur = useAppDocumentStore.getState().files ?? []
  const next: AppFile[] = []
  for (const f of cur) {
    if (!changes.has(f.path)) next.push(f)
    else if (changes.get(f.path) !== null) next.push({ path: f.path, content: changes.get(f.path)! })
  }
  for (const [path, content] of changes) if (content !== null && !cur.some((f) => f.path === path)) next.push({ path, content })
  applying = true
  try {
    useAppDocumentStore.getState().setFiles(next)
  } finally {
    applying = false
  }
}

/** The patches that turn `a` into `b`. */
export function diffFiles(a: AppFile[] | null, b: AppFile[] | null): FilePatch[] {
  const x = filesMap(a)
  const y = filesMap(b)
  const out: FilePatch[] = []
  for (const [path, before] of x) if (y.get(path) !== before) out.push({ path, before, after: y.get(path) ?? null })
  for (const [path, after] of y) if (!x.has(path)) out.push({ path, before: null, after })
  return out
}

/** A label for transactions this module didn't make: set by the writer right before it writes (optional). */
let pendingExternal: { origin: Origin; label: string } | null = null
export function noteNextChange(origin: Origin, label: string) {
  pendingExternal = { origin, label }
}

export const useEditHistory = create<HistoryState>()((set, get) => ({
  past: [],
  future: [],
  notice: null,
  localRev: 0,

  commit: (label, patches, coalesce, preview) => {
    if (!patches.length) return
    write(new Map(patches.map((p) => [p.path, p.after])))
    showPatches(preview, false)
    const now = Date.now()
    const { past } = get()
    const last = past[past.length - 1]
    const merge = coalesce && last?.origin === 'visual' && lastCoalesce?.key === coalesce && now - lastCoalesce.at < COALESCE_MS
    lastCoalesce = coalesce ? { key: coalesce, at: now } : null
    if (merge) {
      const byPath = new Map(last.patches.map((p) => [p.path, { ...p }]))
      for (const p of patches) {
        const prev = byPath.get(p.path)
        byPath.set(p.path, prev ? { ...prev, after: p.after } : p)
      }
      const tx = { ...last, label, patches: [...byPath.values()], at: now, ...(preview || last.preview ? { preview: [...(last.preview ?? []), ...(preview ?? [])] } : {}) }
      set({ past: [...past.slice(0, -1), tx], future: [], notice: null, localRev: get().localRev + 1 })
      return
    }
    const tx: Tx = { id: nextId++, origin: 'visual', label, patches, at: now, ...(preview ? { preview } : {}) }
    set({ past: [...past, tx].slice(-LIMIT), future: [], notice: null, localRev: get().localRev + 1 })
  },

  undo: () => {
    const { past, future } = get()
    const tx = past[past.length - 1]
    if (!tx) return false
    const cur = filesMap(useAppDocumentStore.getState().files)
    const stale = tx.patches.find((p) => (cur.get(p.path) ?? null) !== p.after)
    if (stale) {
      set({ notice: `A later change touched ${stale.path}; undo that first` })
      return false
    }
    write(new Map(tx.patches.map((p) => [p.path, p.before])))
    showPatches(tx.preview, true)
    lastCoalesce = null
    set({ past: past.slice(0, -1), future: [...future, tx], notice: null, localRev: get().localRev + 1 })
    return true
  },

  redo: () => {
    const { past, future } = get()
    const tx = future[future.length - 1]
    if (!tx) return false
    const cur = filesMap(useAppDocumentStore.getState().files)
    const stale = tx.patches.find((p) => (cur.get(p.path) ?? null) !== p.before)
    if (stale) {
      set({ notice: `${stale.path} changed since; can't redo` })
      return false
    }
    write(new Map(tx.patches.map((p) => [p.path, p.after])))
    showPatches(tx.preview, false)
    lastCoalesce = null
    set({ past: [...past, tx], future: future.slice(0, -1), notice: null, localRev: get().localRev + 1 })
    return true
  },

  clear: () => {
    lastCoalesce = null
    set({ past: [], future: [], notice: null })
  },
}))

/**
 * Record every other writer's change to the document as a transaction (a chat turn is one). A document going from empty
 * to files is a load, not an edit, and starts a fresh history. Returns the unsubscribe.
 */
export function installHistory(): () => void {
  return useAppDocumentStore.subscribe((s, prev) => {
    if (applying || s.files === prev.files) return
    if (!prev.files?.length || !s.files?.length) {
      useEditHistory.getState().clear()
      pendingExternal = null
      return
    }
    const patches = diffFiles(prev.files, s.files)
    if (!patches.length) return
    const note = pendingExternal ?? { origin: 'chat' as const, label: 'AI edit' }
    pendingExternal = null
    lastCoalesce = null
    const h = useEditHistory.getState()
    useEditHistory.setState({ past: [...h.past, { id: nextId++, origin: note.origin, label: note.label, patches, at: Date.now() }].slice(-LIMIT), future: [], notice: null })
  })
}
