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

export interface ApiTestExecuteBody {
  name?: string | null;
  method: string;
  url: string;
  headers?: Record<string, string>;
  body?: string | null;
}

export interface ApiTestExecution {
  id: string;
  name: string | null;
  method: string;
  url: string;
  request_headers: Record<string, unknown>;
  request_body_text: string | null;
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
