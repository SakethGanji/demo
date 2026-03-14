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

export interface ApiAppVersionDetail extends ApiAppVersion {
  source_code: string;
}

export interface ApiAppDetail {
  id: string;
  name: string;
  definition: Record<string, unknown>;
  active: boolean;
  workflow_ids: string[];
  source_code: string | null;
  current_version: ApiAppVersion | null;
  created_at: string;
  updated_at: string;
}

export interface ApiAppPublishResponse {
  id: string;
  active: boolean;
  version_id: number | null;
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
      source_code?: string;
      create_version?: boolean;
      version_trigger?: string;
      version_prompt?: string;
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

  publish: (id: string): Promise<ApiAppPublishResponse> => {
    return apiFetch(`/apps/${id}/publish`, {
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
