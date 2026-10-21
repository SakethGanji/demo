/**
 * Query keys (UI-INTEGRATION §3.3) and mutation → invalidation helpers (§3.5).
 * `p` is always the resolved project **id** (UI-DESIGN D10), so `['project', p]` is a prefix
 * that invalidates everything about one project.
 */

import type { QueryClient } from '@tanstack/react-query'
import type { KnowledgeScope } from '@/features/knowledge/lib/types'

/** Any knowledge query, in any scope (`['knowledge', …]` or `['project', p, 'knowledge', …]`). */
export const isKnowledgeKey = (k: readonly unknown[]) => k[0] === 'knowledge' || (k[0] === 'project' && k[2] === 'knowledge')

const kgRoot = (s: KnowledgeScope): readonly unknown[] =>
  s.kind === 'project' ? ['project', s.projectId, 'knowledge'] : ['knowledge', 'team']

export const qk = {
  projects: ['projects'] as const,
  project: (p: string) => ['project', p] as const,
  projectRef: (ref: string) => ['project-ref', ref] as const,
  summary: (p: string) => ['project', p, 'summary'] as const,
  workflows: (p: string) => ['project', p, 'workflows'] as const,
  apps: (p: string) => ['project', p, 'apps'] as const,
  runs: (p: string, f: Record<string, unknown> = {}) => ['project', p, 'runs', f] as const,
  runsPrefix: (p: string) => ['project', p, 'runs'] as const,
  schedules: (p: string) => ['project', p, 'schedules'] as const,
  run: (p: string, x: string) => ['project', p, 'run', x] as const,
  secrets: (p: string, env?: string) => ['project', p, 'secrets', env ?? 'all'] as const,
  secretsPrefix: (p: string) => ['project', p, 'secrets'] as const,
  missing: (p: string, env?: string) => ['project', p, 'secrets-missing', env ?? 'all'] as const,
  missingPrefix: (p: string) => ['project', p, 'secrets-missing'] as const,
  routes: (p: string) => ['project', p, 'routes'] as const,
  route: (p: string, r: string) => ['project', p, 'route', r] as const,
  usedBy: (p: string, r: string) => ['project', p, 'route', r, 'used-by'] as const,
  spec: (p: string) => ['project', p, 'spec'] as const,
  openapi: (p: string, env: string) => ['project', p, 'spec', 'openapi', env] as const,
  clientTs: (p: string, env: string) => ['project', p, 'spec', 'client-ts', env] as const,
  revisions: (p: string) => ['project', p, 'spec', 'revisions'] as const,
  apiKeys: (p: string) => ['project', p, 'api-keys'] as const,
  apiSpecs: (p: string) => ['project', p, 'api-specs'] as const,
  apiSpec: (p: string, id: string) => ['project', p, 'api-specs', id] as const,
  apiMocks: (id: string, op: string) => ['api-specs', id, 'mocks', op] as const,
  tables: (p: string) => ['project', p, 'tables'] as const,
  table: (p: string, t: string) => ['project', p, 'table', t] as const,
  rows: (p: string, t: string, q: Record<string, unknown> = {}) => ['project', p, 'table', t, 'rows', q] as const,
  rowsPrefix: (p: string, t: string) => ['project', p, 'table', t, 'rows'] as const,
  datasets: (p: string) => ['project', p, 'datasets'] as const,
  dataset: (p: string, id: string) => ['project', p, 'dataset', id] as const,
  /**
   * The capability graph, by scope. A project scope nests under the project (so `qk.project(p)` still covers it);
   * the team scope has its own root. The two never share a key.
   */
  knowledge: (s: KnowledgeScope) => kgRoot(s),
  knowledgeGraph: (s: KnowledgeScope, learnedOnly: boolean) => [...kgRoot(s), 'graph', { learnedOnly }] as const,
  knowledgeNode: (s: KnowledgeScope, id: string) => [...kgRoot(s), 'node', id] as const,
  knowledgeCapabilities: (s: KnowledgeScope) => [...kgRoot(s), 'capabilities'] as const,
  knowledgeMemory: (s: KnowledgeScope) => [...kgRoot(s), 'memory'] as const,
  knowledgeAsk: (s: KnowledgeScope, text: string) => [...kgRoot(s), 'ask', text] as const,
  modules: (p: string) => ['project', p, 'modules'] as const,
  module: (p: string, m: string) => ['project', p, 'module', m] as const,
  releases: (p: string, env?: string) => ['project', p, 'releases', env ?? 'all'] as const,
  releasesPrefix: (p: string) => ['project', p, 'releases'] as const,
  release: (p: string, rel: string) => ['project', p, 'release', rel] as const,
  workflow: (w: string) => ['workflow', w] as const,
  workflowDrift: (w: string) => ['workflow', w, 'drift'] as const,
  smokeConfig: (w: string) => ['workflow', w, 'smoke'] as const,
  smokeRuns: (w: string) => ['workflow', w, 'smoke-runs'] as const,
  projectDrift: (p: string) => ['project', p, 'drift'] as const,
  app: (a: string) => ['app', a] as const,
  apiUsage: (a: string) => ['app', a, 'api-usage'] as const,
  previewToken: (a: string) => ['app', a, 'preview-token'] as const,
  /** Stage 5 (DESIGN-STAGE5-UIS §11.2) */
  interfaces: {
    state: (w: string) => ['interfaces', w, 'state'] as const,
    preview: (w: string, exec: string | null) => ['interfaces', w, 'preview', exec ?? 'latest'] as const,
    previewPrefix: (w: string) => ['interfaces', w, 'preview'] as const,
    runs: (p: string, w: string) => ['interfaces', w, 'runs', p] as const,
    publicSchema: (project: string, slug: string) => ['interfaces', 'public', project, slug, 'schema'] as const,
    publicRun: (project: string, slug: string, exec: string) => ['interfaces', 'public', project, slug, 'run', exec] as const,
    publicChat: (project: string, slug: string, sid: string) => ['interfaces', 'public', project, slug, 'chat', sid] as const,
  },
  tasks: {
    all: (p: string) => ['tasks', p] as const,
    list: (p: string, f: Record<string, unknown>) => ['tasks', p, 'list', f] as const,
    detail: (id: string) => ['tasks', 'detail', id] as const,
    counts: (p: string) => ['tasks', p, 'counts'] as const,
    workflowOpen: (p: string, w: string) => ['tasks', p, 'workflow-open', w] as const,
  },
  sdkFunctions: ['sdk', 'functions'] as const,
  sdkReference: ['sdk', 'reference'] as const,
  sandbox: ['sandbox', 'status'] as const,
}

const inv = (qc: QueryClient, ...keys: ReadonlyArray<readonly unknown[]>) =>
  Promise.all(keys.map((queryKey) => qc.invalidateQueries({ queryKey })))

const allApiUsage = (qc: QueryClient) =>
  qc.invalidateQueries({ predicate: (q) => q.queryKey[0] === 'app' && q.queryKey[2] === 'api-usage' })

/** Invalidation helpers named after the mutation that triggers them (§3.5). */
export const invalidate = {
  projectCreated: (qc: QueryClient) => inv(qc, qk.projects),
  projectUpdated: (qc: QueryClient, p: string) => inv(qc, qk.projects, qk.project(p)),
  projectDeleted: (qc: QueryClient, p: string) => {
    qc.removeQueries({ queryKey: qk.project(p) })
    return inv(qc, qk.projects)
  },
  workflowsChanged: (qc: QueryClient, p: string) =>
    inv(qc, ['workflows'], qk.workflows(p), qk.summary(p), qk.missingPrefix(p), qk.routes(p)),
  appsChanged: (qc: QueryClient, p: string) => inv(qc, ['apps'], qk.apps(p), qk.summary(p), qk.projects),
  appSaved: (qc: QueryClient, p: string, a: string) => inv(qc, qk.apiUsage(a), qk.routes(p), qk.app(a)),
  appPublished: (qc: QueryClient, p: string, a: string) => inv(qc, qk.app(a), qk.apps(p), qk.summary(p)),
  secretsChanged: (qc: QueryClient, p: string) =>
    inv(qc, qk.secretsPrefix(p), qk.missingPrefix(p), qk.summary(p), qk.projects),
  routesChanged: async (qc: QueryClient, p: string) => {
    await inv(qc, qk.routes(p), qk.spec(p), qk.summary(p), qk.projects)
    await qc.invalidateQueries({ predicate: (q) => q.queryKey[0] === 'project' && q.queryKey[1] === p && q.queryKey[2] === 'route' })
    await allApiUsage(qc)
  },
  runsChanged: (qc: QueryClient, p: string) => inv(qc, qk.runsPrefix(p), qk.summary(p)),
  apiKeysChanged: (qc: QueryClient, p: string) => inv(qc, qk.apiKeys(p), qk.summary(p)),
  tablesChanged: (qc: QueryClient, p: string, t?: string) =>
    inv(qc, qk.tables(p), qk.summary(p), qk.projects, ...(t ? [qk.table(p, t)] : [])),
  datasetsChanged: (qc: QueryClient, p: string, id?: string) =>
    inv(qc, qk.datasets(p), ...(id ? [qk.dataset(p, id)] : [])),
  /**
   * Graph, nodes, coverage and memory in every scope: a project rebuild changes the team view, and a team rebuild
   * rebuilds every project, so either one refreshes both (as does a decision).
   */
  knowledgeChanged: (qc: QueryClient) => qc.invalidateQueries({ predicate: (q) => isKnowledgeKey(q.queryKey) }),
  rowsChanged: (qc: QueryClient, p: string, t: string) => inv(qc, qk.rowsPrefix(p, t), qk.tables(p)),
  modulesChanged: async (qc: QueryClient, p: string) => {
    await inv(qc, qk.modules(p), qk.missingPrefix(p))
    await qc.invalidateQueries({ predicate: (q) => q.queryKey[0] === 'project' && q.queryKey[1] === p && q.queryKey[2] === 'module' })
    await invalidate.routesChanged(qc, p)
  },
  tasksChanged: (qc: QueryClient, p: string) => inv(qc, qk.tasks.all(p)),
  interfaceChanged: (qc: QueryClient, w: string) => inv(qc, qk.interfaces.state(w), qk.interfaces.previewPrefix(w)),
  releasesChanged: (qc: QueryClient, p: string) =>
    inv(qc, qk.releasesPrefix(p), qk.summary(p), qk.spec(p), qk.apps(p), ['app']),
}
