/**
 * Pure helpers behind the code views (the live build's Code pane and the project Code place): which group a path
 * belongs to, the tree's rows, the server tree merged with what the build has written, the new/changed mark, the
 * editor language, the typing reveal, save failures in plain terms, and the "who wrote this" line.
 */
import type { CodeLanguage } from '@/shared/components/ui/code-editor'
import type { BuildStep, FileBuilder } from '@/features/plan/buildTypes'
import type { LiveState } from '@/features/plan/liveBuild'
import type { FileEntry, FileKind } from './filesTypes'

export type GroupKey = 'plan' | 'backend' | 'tables' | 'app'

export const GROUPS: Array<{ key: GroupKey; label: string; prefix: string }> = [
  { key: 'plan', label: 'Plan', prefix: '' },
  { key: 'backend', label: 'Backend', prefix: 'backend/' },
  { key: 'tables', label: 'Tables', prefix: 'backend/tables/' },
  { key: 'app', label: 'App', prefix: 'apps/' },
]

export function groupOf(path: string): GroupKey {
  if (path.startsWith('apps/')) return 'app'
  if (path.startsWith('backend/tables/')) return 'tables'
  if (path.startsWith('backend/')) return 'backend'
  return 'plan'
}

export function kindOf(path: string): FileKind {
  const g = groupOf(path)
  if (g === 'app') return 'app'
  if (g === 'tables') return 'table'
  if (g === 'backend') return path === 'backend/routes.json' ? 'routes' : 'module'
  return 'plan'
}

export type TreeRow =
  | { type: 'group'; key: GroupKey; label: string; count: number }
  | { type: 'dir'; path: string; name: string; depth: number; group: GroupKey }
  | { type: 'file'; path: string; name: string; depth: number; group: GroupKey; entry: FileEntry }

interface Node {
  dirs: Map<string, Node>
  files: FileEntry[]
}

/** The tree as rows: groups in a fixed order (empty ones left out), folders before files, names sorted; `collapsed` folders hide their contents. */
export function treeRows(entries: FileEntry[], collapsed: Set<string> = new Set()): TreeRow[] {
  const rows: TreeRow[] = []
  for (const g of GROUPS) {
    const mine = entries.filter((e) => groupOf(e.path) === g.key)
    if (!mine.length) continue
    rows.push({ type: 'group', key: g.key, label: g.label, count: mine.length })
    const root: Node = { dirs: new Map(), files: [] }
    for (const e of mine) {
      const parts = e.path.slice(g.prefix.length).split('/')
      let n = root
      for (const d of parts.slice(0, -1)) {
        if (!n.dirs.has(d)) n.dirs.set(d, { dirs: new Map(), files: [] })
        n = n.dirs.get(d) as Node
      }
      n.files.push(e)
    }
    const walk = (n: Node, base: string, depth: number) => {
      for (const name of [...n.dirs.keys()].sort((a, b) => a.localeCompare(b))) {
        const path = `${base}${name}`
        rows.push({ type: 'dir', path, name, depth, group: g.key })
        if (!collapsed.has(path)) walk(n.dirs.get(name) as Node, `${path}/`, depth + 1)
      }
      for (const e of [...n.files].sort((a, b) => a.path.localeCompare(b.path)))
        rows.push({ type: 'file', path: e.path, name: e.path.split('/').pop() ?? e.path, depth, group: g.key, entry: e })
    }
    walk(root, g.prefix, 0)
  }
  return rows
}

/** The server's tree plus the files the build has written that it does not list yet (read-only until saved); deleted files drop out. */
export function mergeEntries(server: FileEntry[], live: LiveState): FileEntry[] {
  const out = server.filter((e) => live.files[e.path]?.op !== 'delete')
  const have = new Set(server.map((e) => e.path))
  for (const p of live.order) {
    const f = live.files[p]
    if (have.has(p) || f.op === 'delete') continue
    out.push({ path: p, kind: kindOf(p), size: f.content.length, updated_at: null, writable: false, ref: { type: kindOf(p), id: p } })
  }
  return out
}

/** New: written this build and not in the tree when the view opened; changed: written and was there. */
export function fileMark(path: string, baseline: Set<string> | null, live: LiveState): 'new' | 'changed' | null {
  const f = live.files[path]
  if (!f || f.op !== 'write') return null
  return baseline && !baseline.has(path) ? 'new' : 'changed'
}

export function editorLanguage(path: string): CodeLanguage {
  const ext = path.split('.').pop()?.toLowerCase()
  if (ext === 'py') return 'python'
  if (ext === 'json') return 'json'
  if (ext === 'ts' || ext === 'tsx') return 'typescript'
  if (ext === 'js' || ext === 'jsx' || ext === 'mjs') return 'javascript'
  return 'text'
}

export function commonPrefix(a: string, b: string): number {
  const n = Math.min(a.length, b.length)
  let i = 0
  while (i < n && a.charCodeAt(i) === b.charCodeAt(i)) i++
  return i
}

/** Characters per frame: about 70 frames (a little over a second) for any file, never fewer than 12 a frame. */
export const revealChunk = (remaining: number): number => Math.max(12, Math.ceil(remaining / 70))

export type SaveProblem = { kind: 'read_only'; message: string } | { kind: 'conflict'; message: string; updated_at: string | null } | { kind: 'other'; message: string }

/** A failed save in plain terms: read-only (how to change it instead), a newer save (reload), or anything else. */
export function saveProblem(e: unknown): SaveProblem {
  const err = e as { code?: string | null; message?: string; details?: Record<string, unknown> }
  const message = err?.message ?? String(e)
  if (err?.code === 'read_only') return { kind: 'read_only', message }
  if (err?.code === 'conflict') return { kind: 'conflict', message, updated_at: (err.details?.updated_at as string | undefined) ?? null }
  return { kind: 'other', message }
}

const BUILDER_LABEL: Record<FileBuilder, string> = { backend: 'Backend builder', tables: 'Backend builder', ui: 'UI builder', plan: 'Planner' }

/** "Backend builder · Runs module": who wrote a file and for which step. */
export function writerLine(builder: FileBuilder, step: Pick<BuildStep, 'key' | 'kind' | 'title'> | null | undefined): string {
  const who = BUILDER_LABEL[builder] ?? 'Builder'
  if (!step || builder === 'plan') return who
  const name = step.title.includes(' · ') ? step.title.split(' · ').slice(1).join(' · ') : step.title
  const what = step.kind === 'module' || step.kind === 'fix' ? `${name} module` : step.kind === 'app' ? `${name} app` : step.kind === 'verify' ? 'plan check' : name
  return `${who} · ${what}`
}

export function relativeTime(at: number | string | null | undefined, now: number): string {
  if (at == null) return ''
  const t = typeof at === 'number' ? at : Date.parse(at)
  if (!Number.isFinite(t)) return ''
  const s = Math.max(0, Math.round((now - t) / 1000))
  if (s < 10) return 'just now'
  if (s < 60) return `${s}s ago`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m ago`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h ago`
  return new Date(t).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
}
