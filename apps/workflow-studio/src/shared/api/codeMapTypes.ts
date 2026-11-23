export interface MapCall {
  id: string
  line: number
  end_line: number
  label: string
  kind: 'local' | 'connector' | 'custom'
  target: string | null
}
export interface MapFunction {
  id: string
  name: string
  label: string
  line: number
  end_line: number
  async: boolean
  parameters: string[]
  routes: { method: string; path: string }[]
  calls: MapCall[]
  secret_references: string[]
  controls: { kind: string; line: number }[]
  custom_scopes: { kind: string; line: number; name: string }[]
  other_call_count: number
}
export interface MapEdge {
  from: string
  to: string
  call_id: string
  line: number
}
export interface CodeMap {
  valid: true
  source_hash: string
  functions: MapFunction[]
  edges: MapEdge[]
  analysis: string
}
export type CodeMapResult =
  CodeMap | { valid: false; source_hash: string; error: { message: string; line: number | null; column: number | null } }
