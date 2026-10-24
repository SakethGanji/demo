/** TanStack Query hooks for notebooks. The notebook query polls while a run is live (runs are async, 202). */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { notebookApi } from './api'
import type { AskSelection, CellCreate, CellPatch, KeepRequest, Notebook, NotebookCreate, NotebookPatch, RunCreate } from './types'

export const nbKeys = {
  list: (pid: string) => ['notebooks', pid] as const,
  one: (id: string) => ['notebook', id] as const,
  caps: ['notebook-capabilities'] as const,
  templates: ['notebook-templates'] as const,
  report: (id: string) => ['notebook', id, 'report'] as const,
  trace: (id: string) => ['notebook', id, 'trace'] as const,
  checks: (id: string) => ['notebook', id, 'checks'] as const,
  rows: (cellId: string, run?: string, row?: number) => ['notebook-rows', cellId, run ?? '', row ?? -1] as const,
  envs: (id: string) => ['notebook', id, 'environments'] as const,
}

/** A run is live while the notebook holds the lock. */
export function isLive(nb: Notebook | undefined): boolean {
  return !!nb?.active_run_id
}

export function useNotebooks(pid: string) {
  return useQuery({ queryKey: nbKeys.list(pid), queryFn: () => notebookApi.list(pid), refetchInterval: (q) => (q.state.data?.some((n) => n.status === 'running') ? 2000 : false) })
}

export function useNotebook(id: string) {
  return useQuery({
    queryKey: nbKeys.one(id),
    queryFn: () => notebookApi.get(id),
    refetchInterval: (q) => (isLive(q.state.data) ? 800 : false),
  })
}

/** Capabilities: 501 until the route lands, then real. Never retried (it is a probe). */
export function useCapabilities() {
  return useQuery({ queryKey: nbKeys.caps, queryFn: notebookApi.capabilities, retry: false, staleTime: 60_000 })
}

export function useTemplates() {
  return useQuery({ queryKey: nbKeys.templates, queryFn: notebookApi.templates, retry: false, staleTime: 60_000 })
}

export function useCreateNotebook(pid: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: NotebookCreate) => notebookApi.create(pid, body),
    onSuccess: (nb) => {
      qc.setQueryData(nbKeys.one(nb.id), nb)
      void qc.invalidateQueries({ queryKey: nbKeys.list(pid) })
    },
  })
}

export function useDeleteNotebook(pid: string) {
  const qc = useQueryClient()
  return useMutation({ mutationFn: (id: string) => notebookApi.remove(id), onSuccess: () => qc.invalidateQueries({ queryKey: nbKeys.list(pid) }) })
}

/** Every write on one notebook refreshes it (cells, latest results, runs all come back with GET /notebooks/{id}). */
export function useNotebookActions(id: string) {
  const qc = useQueryClient()
  const refresh = () => qc.invalidateQueries({ queryKey: nbKeys.one(id) })
  const m = <A, R>(fn: (a: A) => Promise<R>) => ({ mutationFn: fn, onSettled: refresh })
  return {
    refresh,
    patch: useMutation(m((b: NotebookPatch) => notebookApi.patch(id, b))),
    addCell: useMutation(m((b: CellCreate) => notebookApi.addCell(id, b))),
    patchCell: useMutation(m((a: { cellId: string; body: CellPatch }) => notebookApi.patchCell(a.cellId, a.body))),
    deleteCell: useMutation(m((cellId: string) => notebookApi.deleteCell(cellId))),
    run: useMutation(m((b: RunCreate) => notebookApi.run(id, b))),
    cancel: useMutation(m((runId: string) => notebookApi.cancel(runId))),
    retryRows: useMutation(m((a: { cellId: string; rows?: number[]; by: string }) => notebookApi.retryRows(a.cellId, { rows: a.rows ?? null, triggered_by: a.by }))),
    apply: useMutation(m((a: { cellId: string; by: string }) => notebookApi.apply(a.cellId, { approved_by: a.by }))),
    discard: useMutation(m((a: { cellId: string; by: string }) => notebookApi.discard(a.cellId, { by: a.by }))),
    ask: useMutation(m((a: { text: string; by: string; selection?: AskSelection | null }) => notebookApi.ask(id, a.text, a.by, a.selection))),
    keep: useMutation({
      mutationFn: (b: KeepRequest) => notebookApi.keep(id, b),
      onSettled: () => {
        void refresh()
        void qc.invalidateQueries({ queryKey: nbKeys.checks(id) })
      },
    }),
  }
}
