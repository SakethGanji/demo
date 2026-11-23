/**
 * `/p/$project/…` (UI-DESIGN §2): the project layout resolves the slug once and holds the project rail, the
 * workspace layout (top bar + main) hosts the views, and the workflow editor / app builder are their own pages.
 * Apps and workflows are listed only on the Overview: the old list URLs (`apps`, `workflows`) redirect there.
 * URLs use the slug; query keys use the resolved id (D10).
 */
import { createRoute, redirect } from '@tanstack/react-router'
import { rootRoute } from './__root'
import { ProjectLayout } from '@/features/project/components/ProjectLayout'
import { WorkspaceLayout } from '@/features/project/components/WorkspaceLayout'
import { OverviewView } from '@/features/project/components/OverviewView'
import { BackendView } from '@/features/backend/BackendView'
import { BackendIndex } from '@/features/backend/BackendIndex'
import { DataView } from '@/features/data/DataView'
import { SecretsView } from '@/features/secrets/SecretsView'
import { DatasetsView } from '@/features/datasets/DatasetsView'
import { KnowledgeView } from '@/features/knowledge/components/KnowledgeView'
import { KNOWLEDGE_VIEWS, type KnowledgeSearch } from '@/features/knowledge/lib/search'
import { ApiCatalogView } from '@/features/api-catalog/ApiCatalogView'
import { BuildView } from '@/features/plan/BuildView'
import { CodeView } from '@/features/code/CodeView'
import { RunsView } from '@/features/runs/RunsView'
import { ProjectWorkflowEditor } from '@/features/workflows-list/ProjectWorkflowEditor'
import { ProjectAppBuilder } from '@/features/app-builder/ProjectAppBuilder'
import { ApprovalsPage } from '@/features/approvals/ApprovalsPage'
import { TaskDetailPage } from '@/features/approvals/TaskDetailPage'
import { aiBuildRoute } from './ai-build'
import { NotebookManagerPage } from '@/features/notebook/NotebookManagerPage'
import { NotebookPage } from '@/features/notebook/NotebookPage'

const str = (v: unknown) => (typeof v === 'string' && v.length > 0 ? v : undefined)

export interface ProjectSearch {
  sheet?: 'settings' | 'release'
  section?: string
  dialog?: 'create-ui' | 'new-module' | 'new-table'
  /** preselected module for the create-ui dialog */
  module?: string
}

export const projectRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: 'p/$project',
  validateSearch: (s: Record<string, unknown>): ProjectSearch => ({
    sheet: s.sheet === 'settings' || s.sheet === 'release' ? s.sheet : undefined,
    section: str(s.section),
    dialog: s.dialog === 'create-ui' || s.dialog === 'new-module' || s.dialog === 'new-table' ? s.dialog : undefined,
    module: str(s.module),
  }),
  component: ProjectLayout,
})

export const workspaceRoute = createRoute({
  getParentRoute: () => projectRoute,
  id: 'ws',
  component: WorkspaceLayout,
})

export const overviewRoute = createRoute({
  getParentRoute: () => workspaceRoute,
  path: '/',
  validateSearch: (s: Record<string, unknown>): { setup?: 1 } => ({
    setup: s.setup === 1 || s.setup === '1' ? 1 : undefined,
  }),
  component: OverviewView,
})

export interface BackendSearch {
  route?: string
  line?: number
  prompt?: string
  generate?: 1
}

export const backendIndexRoute = createRoute({
  getParentRoute: () => workspaceRoute,
  path: 'backend',
  component: BackendIndex,
})

export const backendRoute = createRoute({
  getParentRoute: () => workspaceRoute,
  path: 'backend/$module',
  validateSearch: (s: Record<string, unknown>): BackendSearch => ({
    route: str(s.route),
    line: s.line != null && Number.isFinite(Number(s.line)) ? Number(s.line) : undefined,
    prompt: str(s.prompt),
    generate: s.generate === 1 || s.generate === '1' ? 1 : undefined,
  }),
  component: BackendView,
})

export interface DataSearch {
  filter?: string
  sort?: string
}

export const dataIndexRoute = createRoute({
  getParentRoute: () => workspaceRoute,
  path: 'data',
  component: DataView,
})

export const dataRoute = createRoute({
  getParentRoute: () => workspaceRoute,
  path: 'data/$table',
  validateSearch: (s: Record<string, unknown>): DataSearch => ({ filter: str(s.filter), sort: str(s.sort) }),
  component: DataView,
})

export const datasetsRoute = createRoute({
  getParentRoute: () => workspaceRoute,
  path: 'datasets',
  component: DatasetsView,
})

export const datasetRoute = createRoute({
  getParentRoute: () => workspaceRoute,
  path: 'datasets/$datasetId',
  component: DatasetsView,
})

/** The capability graph. `view` picks the lens, `node` the selection (both kept in the URL so a link reopens them). */
export const knowledgeRoute = createRoute({
  getParentRoute: () => workspaceRoute,
  path: 'knowledge',
  validateSearch: (s: Record<string, unknown>): KnowledgeSearch => ({
    view: (KNOWLEDGE_VIEWS as readonly unknown[]).includes(s.view) ? (s.view as KnowledgeSearch['view']) : undefined,
    node: str(s.node),
  }),
  component: KnowledgeView,
})

/** The old plan page: the plan now lives on the home page and in the app builder's Plan tab, so the URL redirects home. */
export const planRoute = createRoute({
  getParentRoute: () => workspaceRoute,
  path: 'plan',
  beforeLoad: ({ params }) => {
    throw redirect({ to: '/p/$project', params: { project: params.project }, replace: true })
  },
})

/** One build of the approved plan in full: the detail view behind the app builder's build card. */
export const buildRoute = createRoute({
  getParentRoute: () => workspaceRoute,
  path: 'build/$build',
  component: BuildView,
})

/** Every file the builders wrote (plan, modules, tables, routes, app files): a tree, a viewer/editor, each file's
 *  history and the project's change log (`build` filters it to one build). */
export const codeRoute = createRoute({
  getParentRoute: () => workspaceRoute,
  path: 'code',
  validateSearch: (s: Record<string, unknown>): { path?: string; build?: string; tab?: 'changes' } => ({
    path: str(s.path),
    build: str(s.build),
    tab: s.tab === 'changes' ? 'changes' : undefined,
  }),
  component: CodeView,
})

export const secretsRoute = createRoute({
  getParentRoute: () => workspaceRoute,
  path: 'secrets',
  validateSearch: (s: Record<string, unknown>): { create?: string; env?: string } => ({
    create: str(s.create),
    env: str(s.env),
  }),
  component: SecretsView,
})

export const apisRoute = createRoute({
  getParentRoute: () => workspaceRoute,
  path: 'apis',
  component: ApiCatalogView,
})

export interface RunsSearch {
  trigger?: string
  status?: string
  route?: string
  env?: string
}
const runsSearch = (s: Record<string, unknown>): RunsSearch => ({
  trigger: str(s.trigger),
  status: str(s.status),
  route: str(s.route),
  env: str(s.env),
})

export const runsRoute = createRoute({
  getParentRoute: () => workspaceRoute,
  path: 'runs',
  validateSearch: runsSearch,
  component: RunsView,
})

export const runRoute = createRoute({
  getParentRoute: () => workspaceRoute,
  path: 'runs/$executionId',
  validateSearch: runsSearch,
  component: RunsView,
})

export const approvalsRoute = createRoute({
  getParentRoute: () => workspaceRoute,
  path: 'approvals',
  validateSearch: (s: Record<string, unknown>): { status?: 'open' | 'closed' | 'all'; workflow?: string } => ({
    status: s.status === 'closed' || s.status === 'all' ? s.status : undefined,
    workflow: str(s.workflow),
  }),
  component: ApprovalsPage,
})

export const approvalTaskRoute = createRoute({
  getParentRoute: () => workspaceRoute,
  path: 'approvals/$taskId',
  component: TaskDetailPage,
})

export const notebooksRoute = createRoute({
  getParentRoute: () => workspaceRoute,
  path: 'notebooks',
  component: NotebookManagerPage,
})

export interface NotebookSearch {
  cell?: string
  run?: string
}

export const notebookRoute = createRoute({
  getParentRoute: () => workspaceRoute,
  path: 'notebooks/$notebookId',
  validateSearch: (s: Record<string, unknown>): NotebookSearch => ({ cell: str(s.cell), run: str(s.run) }),
  component: NotebookPage,
})

export const appsIndexRoute = createRoute({
  getParentRoute: () => projectRoute,
  path: 'apps',
  beforeLoad: ({ params }) => {
    throw redirect({ to: '/p/$project', params: { project: params.project }, replace: true })
  },
})

export const workflowsRoute = createRoute({
  getParentRoute: () => projectRoute,
  path: 'workflows',
  beforeLoad: ({ params }) => {
    throw redirect({ to: '/p/$project', params: { project: params.project }, replace: true })
  },
})

/** Templates live at the top level now: the old project URL opens the catalog with this project preselected. */
export const projectTemplatesRoute = createRoute({
  getParentRoute: () => projectRoute,
  path: 'templates',
  beforeLoad: ({ params }) => {
    throw redirect({ to: '/templates', search: { project: params.project }, replace: true })
  },
})

export interface WorkflowSearch {
  execution?: string
  proposal?: string
  ai?: 1
}

export const workflowRoute = createRoute({
  getParentRoute: () => projectRoute,
  path: 'workflows/$workflowId',
  /**
   * `execution`: open the editor showing that saved run's data.
   * `proposal`: the AI change on screen (the Build tab follows it). `ai=1`: open the Build tab (a new workflow starts with it).
   */
  validateSearch: (s: Record<string, unknown>): WorkflowSearch => ({
    execution: str(s.execution),
    proposal: str(s.proposal),
    ai: s.ai === 1 || s.ai === '1' || s.ai === true ? 1 : undefined,
  }),
  component: ProjectWorkflowEditor,
})

export interface AppSearch {
  prompt?: string
  slug?: string
}

export const appRoute = createRoute({
  getParentRoute: () => projectRoute,
  path: 'apps/$appId',
  validateSearch: (s: Record<string, unknown>): AppSearch => ({ prompt: str(s.prompt), slug: str(s.slug) }),
  component: ProjectAppBuilder,
})

export const projectRouteTree = projectRoute.addChildren([
  workspaceRoute.addChildren([
    overviewRoute,
    backendIndexRoute,
    backendRoute,
    dataIndexRoute,
    dataRoute,
    datasetsRoute,
    datasetRoute,
    knowledgeRoute,
    planRoute,
    buildRoute,
    codeRoute,
    secretsRoute,
    apisRoute,
    runsRoute,
    runRoute,
    approvalsRoute,
    approvalTaskRoute,
    notebooksRoute,
    notebookRoute,
  ]),
  appsIndexRoute,
  workflowsRoute,
  projectTemplatesRoute,
  workflowRoute,
  aiBuildRoute,
  appRoute,
])
