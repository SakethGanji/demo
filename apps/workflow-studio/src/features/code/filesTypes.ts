/**
 * The project file tree (one read model over the plan, backend modules, tables, routes and app files) and its change
 * log. These types match the files API exactly.
 */

export type FileKind = 'plan' | 'module' | 'table' | 'routes' | 'app'

export interface FileEntry {
  /** "PLAN.md", "backend/runs.py", "backend/tables/runs.json", "backend/routes.json", "apps/<slug>/App.tsx" */
  path: string
  kind: FileKind
  size: number
  updated_at: string | null
  /** modules and app files save through PUT; the rest change another way (plan revision, migrate) */
  writable: boolean
  ref: { type: string; id: string }
}

export type FileLanguage = 'markdown' | 'json' | 'python' | 'typescript' | 'tsx' | 'css' | 'text'

export interface FileContent {
  path: string
  content: string
  language: FileLanguage
  kind: FileKind
  writable: boolean
  updated_at: string | null
}

export interface FileWrite {
  path: string
  content: string
  /** the `updated_at` the content was read at: a newer save answers 409 `conflict` */
  base_updated_at?: string | null
}

export interface FileWriteResult {
  path: string
  updated_at: string | null
  result: unknown
}

export type ChangeActor = 'backend_builder' | 'ui_builder' | 'user' | 'plan'

export interface Change {
  id: string
  path: string
  op: 'create' | 'update' | 'delete'
  actor: ChangeActor | string
  build_id: string | null
  step_key: string | null
  summary: string | null
  lines_added: number
  lines_removed: number
  created_at: string | null
}
