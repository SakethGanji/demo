/** Project database connections (engine: `/api/projects/{p}/connections`). The URL is write-only: it is never returned. */
export type ConnectionStatus = 'ok' | 'error' | 'unknown'

export interface Connection {
  id: string
  key: string
  name: string
  kind: 'postgres'
  host: string | null
  database: string | null
  readonly: boolean
  status: ConnectionStatus
  error?: string | null
  introspected_at?: string | null
  table_count: number
}

export interface ConnectionCreate {
  name: string
  kind: 'postgres'
  url: string
}

export interface ConnectionTest {
  ok: boolean
  error?: string | null
  server_version?: string | null
}

export interface SchemaColumn {
  name: string
  type: string
  nullable: boolean
  pk: boolean
  fk: { table: string; column: string } | null
  samples: unknown[]
}

export interface SchemaTable {
  schema: string
  name: string
  kind: string
  row_estimate: number | null
  columns: SchemaColumn[]
}

export interface ConnectionSchema {
  tables: SchemaTable[]
}

export interface QueryResult {
  columns: string[]
  rows: unknown[][]
  truncated: boolean
  ms: number
}

/** A data source attached to a plan draft request. */
export interface PlanSource {
  connection: string
  tables?: string[]
}
