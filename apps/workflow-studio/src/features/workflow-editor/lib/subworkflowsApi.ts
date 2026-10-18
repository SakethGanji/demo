/**
 * Sub-workflow API (Stage 8, DESIGN-STAGE8-REUSE section 11). Every call the reuse screens make is here, so a
 * change in the engine's shapes is fixed in one place. The hooks live in `hooks/useSubworkflows.ts`.
 */
import { request } from '@/shared/api/http';
import type { Finding, Shape } from './shapes';

/** A child workflow's declared input. */
export interface SubworkflowInputField {
  name: string;
  type: string;
  required: boolean;
  description?: string;
}

/** `GET /projects/{p}/subworkflows`: one row of the picker. */
export interface SubworkflowListItem {
  id: string;
  name: string;
  description: string | null;
  /** `version_number` of the live version; null = never published. */
  published_version: number | null;
  /** 'published' when inputs / output come from the published version, else 'draft'. */
  io_source: 'published' | 'draft';
  callable: boolean;
  reasons: string[];
  inputs: SubworkflowInputField[];
  output_fields: string[];
  has_approval: boolean;
  used_by_count: number;
}

/** `GET /workflows/{id}/io` */
export interface SubworkflowIO {
  workflow_id: string;
  /** null = the draft */
  version_number: number | null;
  input: Shape;
  output: Shape;
  input_fields: SubworkflowInputField[];
  output_fields: string[];
  callable: boolean;
  reasons: string[];
  findings: Finding[];
}

/** `GET /workflows/{id}/used-by` */
export interface UsedByRow {
  parent_workflow_id: string;
  parent_name: string;
  node: string;
  version_spec: string;
  where: 'draft' | 'published' | 'release';
  release_ids: string[];
}

export interface ExtractInput {
  name: string;
  type: string;
  required: boolean;
  /** "item" (passes through) or the outside node and path the value comes from */
  origin: 'item' | { node: string; path: string };
}

export interface ExtractExit {
  node: string | null;
  port: string | null;
  return_node: string | null;
}

export interface ExtractPlan {
  child_name: string;
  call_node: string;
  trigger_node: string;
  entry: string | null;
  exit: ExtractExit;
  inputs: ExtractInput[];
  warnings: { code?: string; message: string }[];
  affected_tests: { id?: string; name?: string; nodes?: string[] }[];
  selection: string[];
}

export interface ExtractResponse {
  plan: ExtractPlan;
  child_definition: Record<string, unknown>;
  parent_definition: Record<string, unknown>;
  warnings: { code?: string; message: string }[];
  affected_tests: { id?: string; name?: string; nodes?: string[] }[];
  /** dry_run=false only */
  child_workflow_id?: string | null;
  child_version_number?: number | null;
  child_publish_errors?: { kind?: string; message?: string; code?: string }[];
  call_version?: string | null;
}

/** One lineage link of `GET /executions/{id}`. */
export interface ExecutionChildRef {
  execution_id: string;
  node: string | null;
  workflow_id: string;
  workflow_name: string;
  version_number: number | null;
  status: string;
  duration_ms: number | null;
}

export interface ExecutionParentRef {
  execution_id: string;
  workflow_id: string;
  workflow_name: string;
  node: string | null;
}

export interface ExecutionLineage {
  id: string;
  workflow_id: string;
  workflow_name: string;
  status: string;
  parent: ExecutionParentRef | null;
  root_execution_id: string | null;
  depth: number;
  children: ExecutionChildRef[];
}

/** One row of the publish `impact` list: a published parent that follows `latest` and may now break. */
export interface PublishImpactRow {
  parent_workflow_id: string;
  parent_name: string;
  node: string;
  version_spec: string;
  in_release: boolean;
  /** WF024 findings for that node */
  breaking: Finding[];
}

/** The `used_by` list a 409 `WORKFLOW_IN_USE` delete carries. */
export interface WorkflowInUseDetail {
  code: string;
  message: string;
  used_by: UsedByRow[];
}

export type VersionSpec = 'draft' | 'latest' | number;

export const subworkflowsApi = {
  list: (project: string, q?: string, callableOnly = false): Promise<SubworkflowListItem[]> =>
    request(`/projects/${encodeURIComponent(project)}/subworkflows`, { query: { q, callable_only: callableOnly || undefined } }),

  io: (workflowId: string, version?: VersionSpec): Promise<SubworkflowIO> =>
    request(`/workflows/${workflowId}/io`, { query: { version: version === undefined ? undefined : String(version) } }),

  usedBy: (workflowId: string): Promise<UsedByRow[]> => request(`/workflows/${workflowId}/used-by`),

  /** dry run: the plan for the selection, nothing is written. `definition` is the unsaved canvas. */
  previewExtract: (workflowId: string, nodes: string[], name: string, definition?: Record<string, unknown>): Promise<ExtractResponse> =>
    request(`/workflows/${workflowId}/extract-subworkflow`, { method: 'POST', body: { nodes, name, dry_run: true, definition } }),

  /** commits the plan: creates the child and saves the rewritten parent draft. */
  commitExtract: (workflowId: string, nodes: string[], name: string): Promise<ExtractResponse> =>
    request(`/workflows/${workflowId}/extract-subworkflow`, { method: 'POST', body: { nodes, name, dry_run: false } }),

  /** the run's lineage (parent, root, children) */
  lineage: (executionId: string): Promise<ExecutionLineage> => request(`/executions/${executionId}`),
};

/** The `impact` list of a publish response; [] when the engine sent none. */
export function parsePublishImpact(publishResponse: unknown): PublishImpactRow[] {
  const impact = (publishResponse as { impact?: unknown } | null | undefined)?.impact;
  return Array.isArray(impact) ? (impact as PublishImpactRow[]) : [];
}

/** True when any parent in the impact list has a breaking finding (the case that needs a confirm). */
export function hasBreakingImpact(impact: PublishImpactRow[]): boolean {
  return impact.some((r) => r.breaking.length > 0);
}

/** The refusal codes of the convert route (CS01 to CS10), as one readable sentence each. */
export const REFUSAL_MESSAGES: Record<string, string> = {
  CS01: 'Select the steps to move. A single note or No-op is not enough.',
  CS02: 'Triggers stay in the parent workflow. Deselect the trigger.',
  CS03: 'The selection has more than one starting point. Include the step where they meet, or select less.',
  CS04: 'The selection hands data out from more than one place, and a sub-workflow returns one result.',
  CS05: 'A step outside the selection sits between selected steps. Include it or leave both out.',
  CS06: 'Approvals and steps that answer the caller stay in the top-level workflow.',
  CS07: 'A selected step reads an outside step in a way that cannot be turned into an input.',
  CS08: 'A step outside the selection reads a step inside it that is not the last one. A sub-workflow returns only its last step.',
  CS09: 'The selection cuts through an AI agent with its tools, or through a loop. Select the whole agent or loop.',
  CS10: 'This would make the two workflows call each other.',
};

/** The sentence shown for a refusal: ours by code, else the engine's own message. */
export function refusalMessage(code: string | null | undefined, fallback: string): string {
  return (code && REFUSAL_MESSAGES[code]) || fallback;
}

/** "v3", or "draft" when the child ran from its draft. */
/** Label of the navbar "Used by" chip; null (no chip) when nothing calls this workflow. */
export function usedByChipLabel(rows: Pick<UsedByRow, 'parent_workflow_id'>[] | null | undefined): string | null {
  // one parent can reference the child from its draft and its published version: count callers, not reference rows
  const count = new Set((rows ?? []).map((r) => r.parent_workflow_id)).size;
  return count > 0 ? `Used by ${count}` : null;
}

export function versionText(v: number | null): string {
  return v == null ? 'draft' : `v${v}`;
}

export function isFailedStatus(status: string): boolean {
  return status === 'failed' || status === 'error' || status === 'timeout';
}
