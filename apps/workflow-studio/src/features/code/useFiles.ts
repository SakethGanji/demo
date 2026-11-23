/**
 * Queries and the save mutation for the project file tree. A save goes through the file's own store (a module save, a
 * new app version), so it also refreshes what those stores feed: modules and routes, or the app.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { invalidate, qk } from '@/shared/api/queryKeys'
import { filesApi } from './filesApi'
import type { FileEntry, FileWrite } from './filesTypes'

export const fileKeys = {
  all: (p: string) => ['project', p, 'files'] as const,
  tree: (p: string) => ['project', p, 'files', 'tree'] as const,
  content: (p: string, path: string) => ['project', p, 'files', 'content', path] as const,
  history: (p: string, path: string) => ['project', p, 'files', 'history', path] as const,
  changes: (p: string, build?: string | null) => ['project', p, 'files', 'changes', build ?? 'all'] as const,
}

export function useProjectFiles(pid: string, opts: { refetchInterval?: number | false } = {}) {
  return useQuery({ queryKey: fileKeys.tree(pid), queryFn: () => filesApi.list(pid), refetchInterval: opts.refetchInterval ?? false })
}

export function useFileContent(pid: string, path: string | null, enabled = true) {
  return useQuery({
    queryKey: fileKeys.content(pid, path ?? ''),
    queryFn: () => filesApi.content(pid, path as string),
    enabled: !!path && enabled,
    retry: false,
  })
}

export function useFileHistory(pid: string, path: string | null) {
  return useQuery({ queryKey: fileKeys.history(pid, path ?? ''), queryFn: () => filesApi.history(pid, path as string), enabled: !!path })
}

export function useChanges(pid: string, buildId?: string | null) {
  return useQuery({ queryKey: fileKeys.changes(pid, buildId), queryFn: () => filesApi.changes(pid, { build_id: buildId ?? undefined }) })
}

export function useSaveFile(pid: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: FileWrite & { entry?: Pick<FileEntry, 'kind' | 'ref'> }) => {
      const { entry: _entry, ...write } = body
      void _entry
      return filesApi.write(pid, write)
    },
    onSuccess: (_r, body) => {
      void qc.invalidateQueries({ queryKey: fileKeys.all(pid) })
      if (body.entry?.kind === 'module') void invalidate.modulesChanged(qc, pid)
      if (body.entry?.kind === 'app') {
        void qc.invalidateQueries({ queryKey: qk.app(body.entry.ref.id) })
        void qc.invalidateQueries({ queryKey: qk.apps(pid) })
      }
    },
  })
}
