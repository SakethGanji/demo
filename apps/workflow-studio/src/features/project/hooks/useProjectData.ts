/**
 * Project-scoped queries. Every key uses the resolved project id (UI-DESIGN D10).
 */
import { useQuery } from '@tanstack/react-query'
import { qk } from '@/shared/api/queryKeys'
import {
  modulesApi,
  projectsApi,
  routesApi,
  runsApi,
  sandboxApi,
  sdkApi,
  secretsApi,
  tablesApi,
  type RunsQuery,
} from '@/shared/api/endpoints'

export function useProjects() {
  return useQuery({ queryKey: qk.projects, queryFn: projectsApi.list })
}

export function useProjectSummary(p: string | undefined) {
  return useQuery({
    queryKey: qk.summary(p ?? ''),
    queryFn: () => projectsApi.summary(p!),
    enabled: !!p,
    refetchInterval: 30_000,
  })
}

export function useModules(p: string) {
  return useQuery({ queryKey: qk.modules(p), queryFn: () => modulesApi.list(p) })
}

export function useRoutes(p: string) {
  return useQuery({ queryKey: qk.routes(p), queryFn: () => routesApi.list(p) })
}

export function useTables(p: string) {
  return useQuery({ queryKey: qk.tables(p), queryFn: () => tablesApi.list(p), enabled: !!p })
}

export function useProjectApps(p: string) {
  return useQuery({ queryKey: qk.apps(p), queryFn: () => projectsApi.listApps(p) })
}

export function useProjectWorkflows(p: string) {
  return useQuery({ queryKey: qk.workflows(p), queryFn: () => projectsApi.listWorkflows(p) })
}

export function useSecrets(p: string) {
  return useQuery({ queryKey: qk.secrets(p), queryFn: () => secretsApi.list(p) })
}

export function useMissingSecrets(p: string, env?: string) {
  return useQuery({ queryKey: qk.missing(p, env), queryFn: () => secretsApi.missing(p, env) })
}

export function useRecentRuns(p: string, q: RunsQuery = {}) {
  return useQuery({
    queryKey: qk.runs(p, { ...q, page: 'first' }),
    queryFn: () => runsApi.list(p, q),
  })
}

export function useSandboxStatus() {
  return useQuery({ queryKey: qk.sandbox, queryFn: sandboxApi.status, staleTime: 60_000 })
}

export function useSdkFunctions() {
  return useQuery({ queryKey: qk.sdkFunctions, queryFn: sdkApi.functions, staleTime: 5 * 60_000 })
}

export function useSdkReference() {
  return useQuery({ queryKey: qk.sdkReference, queryFn: sdkApi.reference, staleTime: 5 * 60_000 })
}
