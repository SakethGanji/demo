/**
 * Calls for the project file tree: list it, read one file, save one (routed by the engine to the file's own store: a
 * module save, or a new app version), one file's history and the project's change log (newest first).
 */
import { request } from '@/shared/api/http'
import type { Change, FileContent, FileEntry, FileWrite, FileWriteResult } from './filesTypes'

const enc = encodeURIComponent
const P = (p: string) => `/api/projects/${enc(p)}`

export const filesApi = {
  list: async (p: string): Promise<FileEntry[]> => (await request<{ files: FileEntry[] }>(`${P(p)}/files`)).files,
  content: (p: string, path: string) => request<FileContent>(`${P(p)}/files/content`, { query: { path } }),
  /** 409 `read_only` {kind} for the plan, tables and routes; 409 `conflict` {updated_at} when the base is stale. */
  write: (p: string, body: FileWrite) => request<FileWriteResult>(`${P(p)}/files/content`, { method: 'PUT', body }),
  history: (p: string, path: string, limit = 50) => request<Change[]>(`${P(p)}/files/history`, { query: { path, limit } }),
  changes: (p: string, opts: { limit?: number; build_id?: string } = {}) =>
    request<Change[]>(`${P(p)}/changes`, { query: { limit: opts.limit ?? 100, build_id: opts.build_id } }),
}
