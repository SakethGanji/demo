/** Wire shapes of the template routes (engine `routes/templates.py`, DESIGN-STAGE8-REUSE §8, §11). One place to adjust. */

export interface TemplateWorkflowRef {
  key: string
  name: string
  role: 'entry' | 'subworkflow'
}

export interface TemplateRequires {
  templates: Array<{ slug: string; version: string }>
  secrets: Array<{ name: string; used_by: string[] }>
  variables: Array<{ name: string; default?: string | null; type?: string; description?: string | null }>
  egress_hosts: string[]
}

export interface TemplateSummary {
  slug: string
  version: string
  name: string
  summary: string
  category: string
  tags: string[]
  workflows: TemplateWorkflowRef[]
  tables: string[]
  apis: string[]
  requires: TemplateRequires
  /** only with `?project=`: the project ids it is installed in */
  installed_in?: string[]
}

export interface PreviewNode {
  name: string
  type: string
  label?: string | null
  position?: { x: number; y: number } | null
}
export interface PreviewConnection {
  source_node: string
  target_node: string
  source_output?: string | null
  target_input?: string | null
}
export interface WorkflowPreview {
  name: string
  role: 'entry' | 'subworkflow'
  nodes: PreviewNode[]
  connections: PreviewConnection[]
  interface: string | null
  tests: string[]
}

export interface ApiSummary {
  key: string
  name: string
  base_url?: string
  operations: Array<{ operation_id?: string; method?: string; path?: string; summary?: string }>
}

export interface TemplateDetail {
  slug: string
  version: string
  name: string
  summary: string
  category: string
  tags: string[]
  requires: TemplateRequires
  tables: Array<{ key: string; name: string; description?: string | null; columns: Array<{ name: string; type: string }>; seed_rows?: unknown[] }>
  workflows: Array<{ key: string; role: 'entry' | 'subworkflow'; name: string; description?: string | null; publish?: boolean }>
  readme: string
  bundle_hash: string
  preview: Record<string, WorkflowPreview>
  api_summaries: ApiSummary[]
}

export type ClashMode = 'rename' | 'fail'

export interface InstallOptions {
  version?: string | null
  clash?: ClashMode
  reuse_tables?: string[]
  as_copy?: boolean
  run_tests?: boolean
  environment?: string | null
}

export interface Need {
  kind: 'secret' | 'variable'
  name: string
  environments_missing: string[]
  used_by?: string[]
}

export interface InstallPreview {
  template: { slug: string; version: string }
  status: 'new' | 'already_installed' | 'version_installed'
  plan: {
    workflows: Array<{ key: string; name: string }>
    tables: Array<{ key: string; name: string; reused: boolean }>
    apis: Array<{ key: string; name: string; reused: boolean }>
    renamed: Array<{ kind: string; from: string; to: string }>
    clashes: Array<{ kind?: string; name?: string; [k: string]: unknown }>
  }
  dependencies: Array<{ slug: string; version: string; status: 'already_installed' | 'will_install' }>
  needs: Need[]
  egress_hosts: string[]
  installed?: { install_id: string; version: string }
}

export interface TestSummary {
  passed: number
  failed: number
  total: number
  failing?: Array<{ test_id: string; name: string }>
}

export interface InstallResult {
  install_id: string
  status: 'installed' | 'already_installed'
  template: { slug: string; version: string }
  install_name: string
  project_id: string
  workflows: Array<{ key: string; id: string; name: string | null; published_version: number | null; role: 'entry' | 'subworkflow' | null }>
  tables: Array<{ key: string; id: string; name: string | null; reused: boolean; rows?: number; seeded_rows: number }>
  apis: Array<{ key: string; id: string; reused: boolean }>
  renamed: Array<{ kind: string; from: string; to: string }>
  needs: Need[]
  dependencies: Array<{ slug: string; install_id?: string; status: string }>
  egress_added: string[]
  slugs: Record<string, string>
  tests?: Record<string, TestSummary>
}
