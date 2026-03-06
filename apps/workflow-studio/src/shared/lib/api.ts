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
  // Subnode metadata
  isSubnode?: boolean;
  subnodeType?: 'model' | 'memory' | 'tool';
  providesToSlot?: string;
  // Parent node subnode slots
  subnodeSlots?: Array<{
    name: string;
    displayName: string;
    slotType: 'model' | 'memory' | 'tool';
    required: boolean;
    multiple: boolean;
  }>;
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
