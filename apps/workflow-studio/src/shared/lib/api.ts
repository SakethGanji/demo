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

/**
 * A node output's JSON-schema description. Recursive: `properties` and `items`
 * hold the same shape. Declared here (rather than left as `unknown`) so consumers
 * such as SchemaDisplay can read `.properties` without casting.
 */
export interface NodeOutputSchema {
  type: string;
  description?: string;
  properties?: Record<string, NodeOutputSchema>;
  items?: NodeOutputSchema;
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
  outputs: { name: string; displayName: string; type: string; schema?: NodeOutputSchema }[];
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

import { backends } from './config';

/**
 * Generic fetch wrapper with error handling
 */
async function apiFetch<T>(
  endpoint: string,
  options?: RequestInit
): Promise<T> {
  const url = `${backends.workflow}/api${endpoint}`;

  const response = await fetch(url, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...options?.headers,
    },
  });

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    throw new Error(errorData.error || `HTTP ${response.status}`);
  }

  return response.json();
}

// ============================================================================
// Workflows API
// ============================================================================

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

export const executionsApi = {
  list: (workflowId?: string): Promise<ApiExecutionListItem[]> => {
    const params = workflowId ? `?workflow_id=${workflowId}` : '';
    return apiFetch(`/executions${params}`);
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

// ---------------------------------------------------------------------------
// Workflow SDK — the script → workflow surface (/api/workflow-sdk/*).
// Same core the agent's build_workflow tool calls; /build drives it directly.
// ---------------------------------------------------------------------------

export interface SdkNode {
  name: string;
  type: string;
  parameters: Record<string, unknown>;
  position?: { x: number; y: number } | null;
}

export interface SdkConnection {
  source_node: string;
  target_node: string;
  source_output: string;
  target_input: string;
}

export interface SdkExecuteResponse {
  ok: boolean;
  error: string | null;
  problems: string[];
  workflow: { name: string; nodes: SdkNode[]; connections: SdkConnection[] };
  // node name -> { group, sourceLine, type } — line provenance for the canvas
  node_meta: Record<string, { group?: string; sourceLine?: number; type?: string }>;
  results: unknown[];
  persisted: boolean;
  workflow_id: string | null;
}

export const sdkApi = {
  reference: (): Promise<{ reference: string; types: string[]; excluded: string[] }> => {
    return apiFetch('/workflow-sdk/reference');
  },

  execute: (body: { script: string; name?: string; persist?: boolean }): Promise<SdkExecuteResponse> => {
    return apiFetch('/workflow-sdk/execute', {
      method: 'POST',
      body: JSON.stringify(body),
    });
  },
};

// ---------------------------------------------------------------------------
// Agents — fleet, sessions, runs, run events (/api/agents, /api/agent-runs).
// ---------------------------------------------------------------------------

export interface AgentListItem {
  id: string;
  name: string;
  description?: string | null;
  role: string; // asks | builds | watches — derived from bound tools
  model: string;
  active: boolean;
  version: number;
  tool_count?: number;
  [key: string]: unknown;
}

export interface AgentToolBinding {
  source: string;
  tool_key: string;
  connector_id?: string | null;
  alias?: string | null;
  config?: Record<string, unknown>;
  requires_approval?: boolean;
  enabled?: boolean;
  position?: number;
}

export interface AgentRunListItem {
  id: string;
  session_id: string;
  agent_id: string;
  turn: number;
  status: string; // queued | running | waiting | success | failed | cancelled
  trigger: string;
  task: string;
  iterations: number;
  tool_call_count: number;
  event_count: number;
  started_at: string;
  ended_at: string | null;
}

export interface AgentRunDetail extends AgentRunListItem {
  team_id: string;
  input: Record<string, unknown>;
  response: string | null;
  structured_output: unknown;
  error: string | null;
  input_tokens: number;
  output_tokens: number;
  llm_time_ms: number;
  cancelled_at: string | null;
}

export interface AgentRunEvent {
  seq: number;
  type: string; // agent:thinking | agent:tool_call | agent:tool_result | ...
  node_name: string | null;
  payload: Record<string, unknown>;
  truncated: boolean;
  created_at: string;
}

export interface ModelInfo {
  id: string;
  label: string;
  provider: string;
  available: boolean;
  default: boolean;
}

export const modelsApi = {
  list: (): Promise<ModelInfo[]> => {
    return apiFetch('/models');
  },
};

export const agentsApi = {
  list: (): Promise<AgentListItem[]> => {
    return apiFetch('/agents');
  },

  create: (body: {
    name: string;
    model?: string;
    system_prompt?: string;
    description?: string;
    tools?: AgentToolBinding[];
  }): Promise<AgentListItem> => {
    return apiFetch('/agents', { method: 'POST', body: JSON.stringify(body) });
  },

  tools: (agentId: string): Promise<AgentToolBinding[]> => {
    return apiFetch(`/agents/${agentId}/tools`);
  },

  runs: (params?: { agent_id?: string; status?: string; limit?: number }): Promise<AgentRunListItem[]> => {
    const q = new URLSearchParams();
    if (params?.agent_id) q.set('agent_id', params.agent_id);
    if (params?.status) q.set('status', params.status);
    if (params?.limit) q.set('limit', String(params.limit));
    const suffix = q.toString() ? `?${q.toString()}` : '';
    return apiFetch(`/agent-runs${suffix}`);
  },

  run: (runId: string): Promise<AgentRunDetail> => {
    return apiFetch(`/agent-runs/${runId}`);
  },

  runEvents: (runId: string, afterSeq = 0): Promise<AgentRunEvent[]> => {
    return apiFetch(`/agent-runs/${runId}/events?after_seq=${afterSeq}&limit=500`);
  },

  trigger: (body: { agent_id: string; task: string; input?: Record<string, unknown> }): Promise<AgentRunDetail> => {
    return apiFetch('/agent-runs', { method: 'POST', body: JSON.stringify(body) });
  },

  cancel: (runId: string): Promise<AgentRunDetail> => {
    return apiFetch(`/agent-runs/${runId}/cancel`, { method: 'POST' });
  },
};
