/** The rows query for one table view: pages of 50 with `count=true`, shared by the header, grid and import. */
import { useMemo } from 'react'
import { useInfiniteQuery } from '@tanstack/react-query'
import { tablesApi } from '@/shared/api/endpoints'
import { qk } from '@/shared/api/queryKeys'
import type { DataTable, Row, RowFilter } from '@/shared/api/types'

export function useTableRows(pid: string, table: DataTable, filter: RowFilter | null, sort: string | undefined) {
  const filterKey = filter ? JSON.stringify(filter) : null
  const sortKey = sort || '-created_at'
  const rows = useInfiniteQuery({
    queryKey: qk.rows(pid, table.id, { filter: filterKey, sort: sortKey }),
    queryFn: ({ pageParam }) => tablesApi.rows(pid, table.id, { limit: 50, cursor: pageParam, filter, sort: sortKey, count: true }),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  })
  const list = useMemo<Row[]>(() => {
    const seen = new Set<number>()
    return (rows.data?.pages ?? []).flatMap((p) => p.rows).filter((r) => (seen.has(r.id) ? false : (seen.add(r.id), true)))
  }, [rows.data])
  const count = rows.data?.pages[0]?.count ?? list.length
  return { rows, list, count }
}
