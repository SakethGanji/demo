/**
 * Table and row mutations for the Data view (UI-DESIGN §3.4). Tables are addressed by id on the
 * engine (`/api/projects/{p}/tables/{tid}`); the studio URL uses the table name.
 */
import { useMutation, useQueryClient, type InfiniteData } from '@tanstack/react-query'
import { tablesApi } from '@/shared/api/endpoints'
import { invalidate, qk } from '@/shared/api/queryKeys'
import type { Column, DataTable, Row, RowPage } from '@/shared/api/types'

/** `POST …/tables` (reused by the New table dialog and anything that creates a table). */
export function useCreateTable(pid: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (b: { name: string; columns: Column[]; description?: string }) => tablesApi.create(pid, b),
    onSuccess: () => invalidate.tablesChanged(qc, pid),
  })
}

/**
 * `PUT …/tables/{tid}` (rename, columns). `409 rows_incompatible {count}` surfaces to the caller.
 * The cached list is patched before `onUpdated` runs (so a rename can navigate to the new name
 * without a "doesn't exist" flash), then everything is invalidated. Callbacks run even if the
 * calling dialog unmounts meanwhile.
 */
export function useUpdateTable(pid: string, opts: { onUpdated?: (t: DataTable) => void } = {}) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: { name?: string; columns?: Column[]; description?: string } }) =>
      tablesApi.update(pid, id, body),
    onSuccess: (t: DataTable) => {
      qc.setQueryData<DataTable[]>(qk.tables(pid), (list) => list?.map((x) => (x.id === t.id ? { ...x, ...t } : x)))
      opts.onUpdated?.(t)
      return invalidate.tablesChanged(qc, pid, t.id)
    },
  })
}

/** `DELETE …/tables/{tid}`; the table leaves the cached list before `onDeleted` runs. */
export function useDeleteTable(pid: string, opts: { onDeleted?: () => void } = {}) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => tablesApi.remove(pid, id),
    onSuccess: (_r, id) => {
      qc.setQueryData<DataTable[]>(qk.tables(pid), (list) => list?.filter((x) => x.id !== id))
      opts.onDeleted?.()
      qc.removeQueries({ queryKey: qk.table(pid, id) })
      return invalidate.tablesChanged(qc, pid)
    },
  })
}

export function useInsertRow(pid: string, tableId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (data: Record<string, unknown>) => tablesApi.insert(pid, tableId, data),
    onSuccess: () => invalidate.rowsChanged(qc, pid, tableId),
  })
}

type Pages = InfiniteData<RowPage, string | null>

/** `PATCH …/rows/{id}`: optimistic on every cached page of this table, rolled back on error. */
export function usePatchRow(pid: string, tableId: string) {
  const qc = useQueryClient()
  const prefix = qk.rowsPrefix(pid, tableId)
  const apply = (fn: (r: Row) => Row) =>
    qc.setQueriesData<Pages>({ queryKey: prefix }, (old) =>
      old ? { ...old, pages: old.pages.map((p) => ({ ...p, rows: p.rows.map(fn) })) } : old,
    )
  return useMutation({
    mutationFn: ({ id, data }: { id: number; data: Record<string, unknown> }) => tablesApi.patchRow(pid, tableId, id, data),
    onMutate: async ({ id, data }) => {
      await qc.cancelQueries({ queryKey: prefix })
      const snapshot = qc.getQueriesData<Pages>({ queryKey: prefix })
      apply((r) => (r.id === id ? { ...r, data: { ...r.data, ...data } } : r))
      return { snapshot }
    },
    onError: (_e, _v, ctx) => {
      for (const [key, data] of ctx?.snapshot ?? []) qc.setQueryData(key, data)
    },
    onSuccess: (row) => apply((r) => (r.id === row.id ? row : r)),
  })
}

export function useDeleteRows(pid: string, tableId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (ids: number[]) => {
      for (const id of ids) await tablesApi.deleteRow(pid, tableId, id)
      return ids.length
    },
    onSettled: () => invalidate.rowsChanged(qc, pid, tableId),
  })
}
