/**
 * `/p/$project/…` (UI-DESIGN §2): the project layout resolves the slug once, the workspace
 * layout (outline + main) hosts the views, and Workflows/App builder are their own pages.
 * URLs use the slug; query keys use the resolved id (D10).
 */
import { createRoute, redirect } from '@tanstack/react-router'
import { rootRoute } from './__root'
import { ProjectLayout } from '@/features/project/components/ProjectLayout'
import { WorkspaceLayout } from '@/features/project/components/WorkspaceLayout'
import { OverviewView } from '@/features/project/components/OverviewView'
import { BackendView } from '@/features/backend/BackendView'
import { DataView } from '@/features/data/DataView'
import { SecretsView } from '@/features/secrets/SecretsView'
import { ApiCatalogView } from '@/features/api-catalog/ApiCatalogView'
import { RunsView } from '@/features/runs/RunsView'
import { WorkflowsListPage } from '@/features/workflows-list/WorkflowsListPage'
import { ProjectWorkflowEditor } from '@/features/workflows-list/ProjectWorkflowEditor'
import { ProjectAppBuilder } from '@/features/app-builder/ProjectAppBuilder'
import { ApprovalsPage } from '@/features/approvals/ApprovalsPage'
import { TaskDetailPage } from '@/features/approvals/TaskDetailPage'
import { TemplatesPage } from '@/features/templates/TemplatesPage'
import { KnowledgePage } from '@/features/knowledge/KnowledgePage'
import type { KnowledgeSearch } from '@/features/knowledge/kit/links'
import { aiBuildRoute } from './ai-build'

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

export const appsIndexRoute = createRoute({
  getParentRoute: () => projectRoute,
  path: 'apps',
  beforeLoad: ({ params }) => {
    throw redirect({ to: '/p/$project', params: { project: params.project }, replace: true })
  },
})

export const workflowsRoute = createRoute({
  getParentRoute: () => workspaceRoute,
  path: 'workflows',
  component: WorkflowsListPage,
})

export const templatesRoute = createRoute({
  getParentRoute: () => workspaceRoute,
  path: 'templates',
  component: TemplatesPage,
})

export const knowledgeRoute = createRoute({
  getParentRoute: () => workspaceRoute,
  path: 'knowledge',
  validateSearch: (s: Record<string, unknown>): KnowledgeSearch => ({
    tab: s.tab === 'matrix' || s.tab === 'memory' ? s.tab : undefined,
    focus: str(s.focus),
    depth: s.depth === 2 || s.depth === '2' ? 2 : undefined,
  }),
  component: KnowledgePage,
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
    backendRoute,
    dataIndexRoute,
    dataRoute,
    secretsRoute,
    apisRoute,
    runsRoute,
    runRoute,
    approvalsRoute,
    approvalTaskRoute,
    knowledgeRoute,
    workflowsRoute,
    templatesRoute,
  ]),
  appsIndexRoute,
  workflowRoute,
  aiBuildRoute,
  appRoute,
])
