/**
 * Sub-workflow hooks (Stage 8). All reads and writes of the reuse screens go through here; the HTTP calls are in
 * `lib/subworkflowsApi.ts`.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { request } from '@/shared/api/http';
import { subworkflowsApi, parsePublishImpact, hasBreakingImpact, type PublishImpactRow, type VersionSpec } from '../lib/subworkflowsApi';

export * from '../lib/subworkflowsApi';

const KEY = 'subworkflows';

/** The picker list of a project (callable state, typed inputs). `q` filters by name or description. */
export function useSubworkflowList(project: string | undefined, q?: string) {
  return useQuery({
    queryKey: [KEY, 'list', project, q ?? ''],
    queryFn: () => subworkflowsApi.list(project!, q),
    enabled: !!project,
    staleTime: 10_000,
  });
}

/** What a workflow takes and gives as a sub-workflow, at one version. */
export function useWorkflowIO(workflowId: string | undefined, version?: VersionSpec) {
  return useQuery({
    queryKey: [KEY, 'io', workflowId, version ?? 'default'],
    queryFn: () => subworkflowsApi.io(workflowId!, version),
    enabled: !!workflowId,
    staleTime: 10_000,
  });
}

/** Who calls this workflow. */
export function useUsedBy(workflowId: string | undefined, enabled = true) {
  return useQuery({
    queryKey: [KEY, 'used-by', workflowId],
    queryFn: () => subworkflowsApi.usedBy(workflowId!),
    enabled: !!workflowId && enabled,
    staleTime: 5_000,
  });
}

/** The lineage of a saved run: its parent run and the child runs it started. */
export function useExecutionLineage(executionId: string | null | undefined, enabled = true) {
  return useQuery({
    queryKey: [KEY, 'lineage', executionId],
    queryFn: () => subworkflowsApi.lineage(executionId!),
    enabled: !!executionId && enabled,
    staleTime: 5_000,
  });
}

/** Commit of a convert-selection plan. The dry-run preview is `subworkflowsApi.previewExtract` (debounced by the dialog). */
export function useCommitExtract() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { workflowId: string; nodes: string[]; name: string }) =>
      subworkflowsApi.commitExtract(v.workflowId, v.nodes, v.name),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: [KEY] });
      void qc.invalidateQueries({ queryKey: ['workflows'] });
      void qc.invalidateQueries({ queryKey: ['project'] });
    },
  });
}

export interface PublishWithImpactResult {
  id: string;
  active: boolean;
  version_id: number | null;
  /** published parents that follow this workflow's `latest` and may now break */
  impact: PublishImpactRow[];
  /** true when any parent has a breaking finding (the Wave 3 impact dialog asks for a confirm then) */
  breaking: boolean;
}

/**
 * Publishes a workflow and returns the impact on the parents that follow its `latest`. The engine publishes first and
 * reports the impact in the same response (it never blocks); B7's impact dialog renders `impact` / `breaking`.
 */
export function usePublishWithImpact() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (v: { workflowId: string; message?: string }): Promise<PublishWithImpactResult> => {
      const res = await request<{ id: string; active: boolean; version_id: number | null }>(
        `/workflows/${v.workflowId}/publish`,
        { method: 'POST', body: v.message ? { message: v.message } : {} },
      );
      const impact = parsePublishImpact(res);
      return { id: res.id, active: res.active, version_id: res.version_id, impact, breaking: hasBreakingImpact(impact) };
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: [KEY] });
      void qc.invalidateQueries({ queryKey: ['workflows'] });
    },
  });
}
