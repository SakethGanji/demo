import { request } from '@/shared/api/http'
import { rowsAsArrays } from './connectionsLogic'
import type { Connection, ConnectionCreate, ConnectionSchema, ConnectionTest, QueryResult } from './connectionsTypes'

const enc = encodeURIComponent
const C = (p: string) => `/api/projects/${enc(p)}/connections`

export const connectionsApi = {
  list: (p: string) => request<Connection[]>(C(p)),
  create: (p: string, b: ConnectionCreate) => request<Connection>(C(p), { method: 'POST', body: b }),
  test: (p: string, id: string) => request<ConnectionTest>(`${C(p)}/${enc(id)}/test`, { method: 'POST', body: {} }),
  introspect: (p: string, id: string) => request<ConnectionSchema>(`${C(p)}/${enc(id)}/introspect`, { method: 'POST', body: {} }),
  schema: (p: string, id: string) => request<ConnectionSchema>(`${C(p)}/${enc(id)}/schema`),
  query: (p: string, id: string, sql: string, limit?: number) =>
    request<QueryResult>(`${C(p)}/${enc(id)}/query`, { method: 'POST', body: { sql, limit } }).then(rowsAsArrays),
  remove: (p: string, id: string) => request<unknown>(`${C(p)}/${enc(id)}`, { method: 'DELETE' }),
}
