/**
 * REST API Client
 *
 * Fetch-based API client to communicate with the workflow-engine backend.
 * Replaces tRPC with standard REST calls.
 */

import type {
  BackendWorkflow,
  ApiWorkflowSummary,
  ApiWorkflowDetail,
  ApiCreateResponse,
  ApiExecutionResult,
  ApiExecutionListItem,
  ApiPublishResponse,
} from './backendTypes';
import type { CheckResult, ImportReport, Shape } from '@/features/workflow-editor/lib/shapes';
import type {
  AiDraftResponse, DriftAlert, FuzzResponse, SmokeConfig, SmokeRun, WorkflowDrift,
} from '@/features/workflow-editor/lib/testsAi';
import type {
  ApiSpecDetail,
  ApiSpecSummary,
  MockOption,
  Mock,
  Scorecard,
  TestCase,
  TestDefaults,
  TestRunReport,
} from '@/features/workflow-editor/lib/tests';

// Property definition from API node type
export interface ApiProperty {
  name: string;
  displayName: string;
  type: string;
  default?: unknown;
  required?: boolean;
  description?: string;
  options?: { name: string; value: unknown }[];
  displayOptions?: {
    show?: Record<string, unknown[]>;
    hide?: Record<string, unknown[]>;
  };
  [key: string]: unknown;
}

interface NodeTypeInfo {
  type: string;
  displayName: string;
  description: string;
  icon: string;
  group: string[];
  inputCount: number;
  outputCount: number | 'dynamic';
  properties: ApiProperty[];
  inputs: { name: string; displayName: string; type: string; required?: boolean }[];
  outputs: { name: string; displayName: string; type: string; schema?: unknown }[];
  // Dynamic output strategy
  outputStrategy?: {
    type: 'dynamicFromCollection' | 'dynamicFromParameter' | 'static';
    collectionName?: string;
    parameter?: string;
    addFallback?: boolean;
  };
}

// ============================================================================
// API Client
// ============================================================================

import { request } from '@/shared/api/http';

/**
 * Generic fetch wrapper with error handling. Delegates to the shared HTTP core, so failures
 * throw `ApiError` (which extends `Error`, keeping `error.message` call sites working).
 */
async function apiFetch<T>(
  endpoint: string,
  options?: RequestInit
): Promise<T> {
  return request<T>(`/api${endpoint}`, options as never);
}

// ============================================================================
// Workflows API
// ============================================================================

export interface ApiWorkflowVersion {
  id: number;
  version_number: number;
  message: string | null;
  created_by: string | null;
  created_at: string;
}

export interface ApiWorkflowVersionDetail extends ApiWorkflowVersion {
  definition: ApiWorkflowDetail['definition'];
}

export const workflowsApi = {
  list: (): Promise<ApiWorkflowSummary[]> => {
    return apiFetch('/workflows');
  },

  get: (id: string): Promise<ApiWorkflowDetail> => {
    return apiFetch(`/workflows/${id}`);
  },

  create: (workflow: BackendWorkflow): Promise<ApiCreateResponse> => {
    return apiFetch('/workflows', {
      method: 'POST',
      body: JSON.stringify(workflow),
    });
  },

  update: (id: string, workflow: BackendWorkflow): Promise<ApiWorkflowDetail> => {
    return apiFetch(`/workflows/${id}`, {
      method: 'PUT',
      body: JSON.stringify(workflow),
    });
  },

  delete: (id: string): Promise<{ success: boolean }> => {
    return apiFetch(`/workflows/${id}`, {
      method: 'DELETE',
    });
  },

  publish: (id: string, message?: string): Promise<ApiPublishResponse> => {
    return apiFetch(`/workflows/${id}/publish`, {
      method: 'POST',
      body: JSON.stringify(message ? { message } : {}),
    });
  },

  unpublish: (id: string): Promise<ApiPublishResponse> => {
    return apiFetch(`/workflows/${id}/unpublish`, {
      method: 'POST',
    });
  },

  run: (id: string): Promise<ApiExecutionResult> => {
    return apiFetch(`/workflows/${id}/run`, {
      method: 'POST',
    });
  },

  listVersions: (id: string): Promise<ApiWorkflowVersion[]> => {
    return apiFetch(`/workflows/${id}/versions`);
  },

  getVersion: (id: string, versionId: number): Promise<ApiWorkflowVersionDetail> => {
    return apiFetch(`/workflows/${id}/versions/${versionId}`);
  },

  /** Copies a version's definition into the draft; returns the workflow like `get`. */
  restoreVersion: (id: string, versionId: number): Promise<ApiWorkflowDetail> => {
    return apiFetch(`/workflows/${id}/versions/${versionId}/restore`, { method: 'POST' });
  },

  /** Run only `name`, feeding it input from `source` (the saved draft is what runs). */
  testNode: (
    id: string,
    name: string,
    body: { source: 'input' | 'last_run' | 'pinned'; input_items?: Array<Record<string, unknown>>; environment?: string },
  ): Promise<ApiExecutionResult> => {
    return apiFetch(`/workflows/${id}/nodes/${encodeURIComponent(name)}/test`, {
      method: 'POST',
      body: JSON.stringify(body),
    });
  },

  /** Run from the trigger and stop after `name`. */
  runTo: (id: string, name: string, body: { input_data?: Record<string, unknown>; environment?: string } = {}): Promise<ApiExecutionResult> => {
    return apiFetch(`/workflows/${id}/run-to/${encodeURIComponent(name)}`, {
      method: 'POST',
      body: JSON.stringify(body),
    });
  },

  /** Re-run `name` and everything after it, reusing a past execution's upstream outputs. */
  runFrom: (id: string, name: string, body: { execution_id?: string; environment?: string } = {}): Promise<ApiExecutionResult> => {
    return apiFetch(`/workflows/${id}/run-from/${encodeURIComponent(name)}`, {
      method: 'POST',
      body: JSON.stringify(body),
    });
  },

  /** Stage 2 checker. Without `definition` it checks the stored draft. Never saves. */
  check: (id: string, body: { definition?: Pick<BackendWorkflow, 'nodes' | 'connections'>; environment?: string } = {}): Promise<CheckResult> => {
    return apiFetch(`/workflows/${id}/check`, { method: 'POST', body: JSON.stringify(body) });
  },

  /** Propose a contract from a saved execution. Does not save. 409 `detail.missing_nodes` when there is no output. */
  contractLearn: (id: string, name: string, body: { execution_id?: string } = {}): Promise<{ outputs: Record<string, Shape>; from_execution: string; sample_count: number }> => {
    return apiFetch(`/workflows/${id}/nodes/${encodeURIComponent(name)}/contract/learn`, { method: 'POST', body: JSON.stringify(body) });
  },

  contractApprove: (id: string, name: string, body: { outputs: Record<string, Shape>; from_execution: string; approved_by: string }): Promise<ApiWorkflowDetail> => {
    return apiFetch(`/workflows/${id}/nodes/${encodeURIComponent(name)}/contract/approve`, { method: 'POST', body: JSON.stringify(body) });
  },

  contractRemove: (id: string, name: string): Promise<ApiWorkflowDetail> => {
    return apiFetch(`/workflows/${id}/nodes/${encodeURIComponent(name)}/contract`, { method: 'DELETE' });
  },

  /** Convert an n8n workflow. Does not save; create it through `create` afterwards. */
  importN8n: (body: { n8n: unknown; name?: string; project_id?: string }): Promise<{ workflow: BackendWorkflow; report: ImportReport }> => {
    return apiFetch('/workflows/import/n8n', { method: 'POST', body: JSON.stringify(body) });
  },

  runAdhoc: (workflow: BackendWorkflow): Promise<ApiExecutionResult> => {
    return apiFetch('/workflows/run-adhoc', {
      method: 'POST',
      body: JSON.stringify(workflow),
    });
  },
};

// ============================================================================
// Apps API
// ============================================================================

export interface ApiAppListItem {
  id: string;
  name: string;
  created_at: string;
  updated_at: string;
  project_id?: string;
  slug?: string | null;
  active?: boolean;
  published_at?: string | null;
  current_version_number?: number | null;
}

export interface ApiAppVersion {
  id: number;
  version_number: number;
  parent_version_id: number | null;
  trigger: string;
  label: string | null;
  prompt: string | null;
  message: string | null;
  created_at: string;
}

export interface ApiAppFile {
  path: string;
  content: string;
  file_type?: string;
  parsed_index?: Record<string, unknown> | null;
}

export interface ApiAppVersionDetail extends ApiAppVersion {
  source_code: string;
  files?: ApiAppFile[];
}

export type ApiAppAccess = 'private' | 'public' | 'password';

export interface ApiAppDetail {
  id: string;
  name: string;
  project_id?: string;
  description?: string | null;
  definition: Record<string, unknown>;
  active: boolean;
  workflow_ids: string[];
  api_execution_ids: string[];
  source_code: string | null;
  files?: ApiAppFile[];
  current_version: ApiAppVersion | null;
  created_at: string;
  updated_at: string;
  // Publishing fields.
  slug: string | null;
  access: ApiAppAccess;
  access_password_set: boolean;
  embed_enabled: boolean;
  published_at: string | null;
  published_version: ApiAppVersion | null;
}

export interface ApiAppPublishResponse {
  id: string;
  active: boolean;
  version_id: number | null;
  slug: string | null;
  bundle_hash: string | null;
  public_url: string | null;
}

export interface ApiAppPublishRequest {
  slug?: string;
  access?: ApiAppAccess;
  access_password?: string;
}

export const appsApi = {
  list: (): Promise<ApiAppListItem[]> => {
    return apiFetch('/apps');
  },

  get: (id: string): Promise<ApiAppDetail> => {
    return apiFetch(`/apps/${id}`);
  },

  create: (data: { name: string; definition: Record<string, unknown> }): Promise<ApiAppDetail> => {
    return apiFetch('/apps', {
      method: 'POST',
      body: JSON.stringify(data),
    });
  },

  update: (
    id: string,
    data: {
      name?: string;
      definition?: Record<string, unknown>;
      description?: string;
      workflow_ids?: string[];
      api_execution_ids?: string[];
      source_code?: string;
      files?: ApiAppFile[];
      create_version?: boolean;
      version_trigger?: string;
      version_prompt?: string;
      slug?: string;
      access?: ApiAppAccess;
      access_password?: string;
      embed_enabled?: boolean;
    },
  ): Promise<ApiAppDetail> => {
    return apiFetch(`/apps/${id}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    });
  },

  delete: (id: string): Promise<{ success: boolean }> => {
    return apiFetch(`/apps/${id}`, {
      method: 'DELETE',
    });
  },

  publish: (id: string, opts?: ApiAppPublishRequest): Promise<ApiAppPublishResponse> => {
    return apiFetch(`/apps/${id}/publish`, {
      method: 'POST',
      body: opts ? JSON.stringify(opts) : undefined,
    });
  },

  unpublish: (id: string): Promise<ApiAppPublishResponse> => {
    return apiFetch(`/apps/${id}/unpublish`, {
      method: 'POST',
    });
  },

  // ── Version endpoints ──────────────────────────────────────────────────

  listVersions: (appId: string): Promise<ApiAppVersion[]> => {
    return apiFetch(`/apps/${appId}/versions`);
  },

  getVersion: (appId: string, versionId: number): Promise<ApiAppVersionDetail> => {
    return apiFetch(`/apps/${appId}/versions/${versionId}`);
  },

  createVersion: (
    appId: string,
    data: { source_code: string; trigger?: string; label?: string; prompt?: string; message?: string },
  ): Promise<ApiAppVersionDetail> => {
    return apiFetch(`/apps/${appId}/versions`, {
      method: 'POST',
      body: JSON.stringify(data),
    });
  },

  revertToVersion: (appId: string, versionId: number): Promise<ApiAppDetail> => {
    return apiFetch(`/apps/${appId}/versions/${versionId}/revert`, {
      method: 'POST',
    });
  },

  updateVersionLabel: (appId: string, versionId: number, label: string | null): Promise<ApiAppVersion> => {
    return apiFetch(`/apps/${appId}/versions/${versionId}`, {
      method: 'PATCH',
      body: JSON.stringify({ label }),
    });
  },
};

// ============================================================================
// Executions API
// ============================================================================

/** `GET /api/executions/{id}`: `node_data` maps node name to its output items. */
export interface ApiExecutionDetail {
  id: string;
  workflow_id: string;
  workflow_name: string;
  status: string;
  mode: string;
  start_time: string;
  end_time: string | null;
  errors: Array<{ node_name: string; error: string; timestamp?: string }>;
  node_data: Record<string, Array<{ json: Record<string, unknown> }>>;
  node_metrics?: Record<string, unknown>;
}

export const executionsApi = {
  list: (workflowId?: string): Promise<ApiExecutionListItem[]> => {
    const params = workflowId ? `?workflow_id=${workflowId}` : '';
    return apiFetch(`/executions${params}`);
  },

  get: (id: string): Promise<ApiExecutionDetail> => {
    return apiFetch(`/executions/${id}`);
  },
};

// ============================================================================
// Nodes API
// ============================================================================

export const nodesApi = {
  list: (): Promise<NodeTypeInfo[]> => {
    return apiFetch('/nodes');
  },

  get: (type: string): Promise<NodeTypeInfo> => {
    return apiFetch(`/nodes/${type}`);
  },
};

// ============================================================================
// API Tester
// ============================================================================

export interface ApiTestFilePart {
  field: string;
  filename: string;
  content_type?: string | null;
  content_b64: string;
}

export interface ApiTestFileMeta {
  field: string;
  filename: string;
  content_type: string | null;
  size: number;
}

export interface ApiTestExecuteBody {
  name?: string | null;
  method: string;
  url: string;
  headers?: Record<string, string>;
  body?: string | null;
  files?: ApiTestFilePart[];
  form_fields?: Record<string, string>;
}

export interface ApiTestExecution {
  id: string;
  name: string | null;
  method: string;
  url: string;
  request_headers: Record<string, unknown>;
  request_body_text: string | null;
  request_files: ApiTestFileMeta[] | null;
  response_status: number | null;
  response_headers: Record<string, unknown>;
  response_content_type: string | null;
  response_size: number;
  response_body_b64: string | null;
  response_truncated: boolean;
  latency_ms: number | null;
  error: string | null;
  created_at: string;
}

export interface ApiTestExecutionListItem {
  id: string;
  name: string | null;
  method: string;
  url: string;
  response_status: number | null;
  response_content_type: string | null;
  latency_ms: number | null;
  error: string | null;
  created_at: string;
}

export const apiTesterApi = {
  execute: (body: ApiTestExecuteBody, signal?: AbortSignal): Promise<ApiTestExecution> => {
    return apiFetch('/api-tester/execute', {
      method: 'POST',
      body: JSON.stringify(body),
      signal,
    });
  },

  list: (): Promise<ApiTestExecutionListItem[]> => {
    return apiFetch('/api-tester/executions');
  },

  get: (id: string): Promise<ApiTestExecution> => {
    return apiFetch(`/api-tester/executions/${id}`);
  },

  rename: (id: string, name: string | null): Promise<ApiTestExecution> => {
    return apiFetch(`/api-tester/executions/${id}`, {
      method: 'PATCH',
      body: JSON.stringify({ name }),
    });
  },

  delete: (id: string): Promise<{ success: boolean }> => {
    return apiFetch(`/api-tester/executions/${id}`, {
      method: 'DELETE',
    });
  },
};

// ============================================================================
// Tests API (CONTRACTS-STAGE3A §1, §5, §6, §7)
// ============================================================================

type DraftDefinition = Pick<BackendWorkflow, 'nodes' | 'connections'> & { tests?: TestCase[]; test_defaults?: TestDefaults };

const post = <T>(endpoint: string, body: unknown): Promise<T> =>
  apiFetch(endpoint, { method: 'POST', body: JSON.stringify(body ?? {}) });

export const testsApi = {
  /** `definition` runs the unsaved draft; it never saves the workflow. */
  run: (id: string, body: { ids?: string[]; definition?: DraftDefinition } = {}): Promise<TestRunReport> =>
    post(`/workflows/${id}/tests/run`, body),

  approve: (id: string, testId: string, approvedBy: string): Promise<ApiWorkflowDetail> =>
    post(`/workflows/${id}/tests/${encodeURIComponent(testId)}/approve`, { approved_by: approvedBy }),

  unlock: (id: string, testId: string, by: string): Promise<ApiWorkflowDetail> =>
    post(`/workflows/${id}/tests/${encodeURIComponent(testId)}/unlock`, { by }),

  /** Drafts only; nothing is saved. */
  suggest: (id: string, body: { node?: string } = {}): Promise<TestCase[]> =>
    post(`/workflows/${id}/tests/suggest`, body),

  fromExecution: (id: string, body: { execution_id: string; name?: string }): Promise<TestCase> =>
    post(`/workflows/${id}/tests/from-execution`, body),

  score: (id: string, body: { definition?: DraftDefinition; mutations?: boolean; repeat?: number } = {}): Promise<Scorecard> =>
    post(`/workflows/${id}/score`, body),

  generateMock: (
    id: string,
    node: string,
    body: { kind: 'from_spec' | 'from_contract' | 'last_real'; status?: number },
  ): Promise<{ mock: Mock; source: string }> =>
    post(`/workflows/${id}/nodes/${encodeURIComponent(node)}/mock/generate`, body),

  /** Drafts for grid gaps; nothing is saved. 503 = AI not set up, 502 = the model failed. */
  aiDraft: (
    id: string,
    body: { definition?: DraftDefinition; cells?: Array<{ node: string; category: string }>; max?: number } = {},
  ): Promise<AiDraftResponse> => post(`/workflows/${id}/tests/ai-draft`, body),

  /** Odd inputs through the workflow; never saves. */
  fuzz: (
    id: string,
    body: { definition?: DraftDefinition; target?: { kind: 'workflow' } | { kind: 'node'; node: string }; count?: number; seed?: number; ai?: boolean },
  ): Promise<FuzzResponse> => post(`/workflows/${id}/tests/fuzz`, body),
};

export const driftApi = {
  forWorkflow: (id: string, status: 'open' | 'all' = 'open'): Promise<WorkflowDrift> =>
    apiFetch(`/workflows/${id}/drift?status=${status}`),

  resolve: (id: string, driftId: string, by: string): Promise<DriftAlert> =>
    post(`/workflows/${id}/drift/${encodeURIComponent(driftId)}/resolve`, { by }),
};

export const smokeApi = {
  get: (id: string): Promise<SmokeConfig> => apiFetch(`/workflows/${id}/smoke`),

  put: (id: string, body: SmokeConfig): Promise<SmokeConfig> =>
    apiFetch(`/workflows/${id}/smoke`, { method: 'PUT', body: JSON.stringify(body) }),

  run: (id: string): Promise<SmokeRun> => post(`/workflows/${id}/smoke/run`, {}),

  runs: (id: string, limit = 20): Promise<SmokeRun[]> => apiFetch(`/workflows/${id}/smoke/runs?limit=${limit}`),
};

export const apiSpecsApi = {
  list: (projectId: string): Promise<ApiSpecSummary[]> => apiFetch(`/api-specs?project_id=${encodeURIComponent(projectId)}`),

  get: (id: string): Promise<ApiSpecDetail> => apiFetch(`/api-specs/${id}`),

  create: (body: { project_id: string; name?: string; spec_text?: string; url?: string }): Promise<ApiSpecDetail> =>
    post('/api-specs', body),

  fromExample: (body: {
    project_id: string;
    name: string;
    method: string;
    base_url: string;
    path: string;
    status?: number;
    example: unknown;
  }): Promise<ApiSpecDetail> => post('/api-specs/from-example', body),

  remove: (id: string): Promise<{ success: boolean }> => apiFetch(`/api-specs/${id}`, { method: 'DELETE' }),

  mocks: (id: string, operationId: string): Promise<MockOption[]> =>
    apiFetch(`/api-specs/${id}/operations/${encodeURIComponent(operationId)}/mocks`),
};
