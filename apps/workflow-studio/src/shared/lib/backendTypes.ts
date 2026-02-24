/**
 * Shared Backend Types
 *
 * Canonical type definitions for the backend workflow API format.
 * Used by both the API client and the workflow transformation layer.
 */

// Individual node data item (backend format)
export interface BackendNodeData {
  json: Record<string, unknown>;
  binary?: Record<string, unknown>;
}

// Node definition as stored/sent to the backend
export interface BackendNodeDefinition {
  name: string;
  type: string;
  label?: string;
  parameters: Record<string, unknown>;
  position?: { x: number; y: number };
  continue_on_fail?: boolean;
  retry_on_fail?: number;
  retry_delay?: number;
  pinned_data?: BackendNodeData[];
}

// Connection between two nodes in the backend format
export interface BackendConnection {
  source_node: string;
  source_output: string;
  target_node: string;
  target_input: string;
  connection_type?: 'normal' | 'subnode';
  slot_name?: string;
  waypoints?: Array<{ x: number; y: number }>;
}

// Complete workflow definition in backend format
export interface BackendWorkflow {
  id?: string;
  name: string;
  nodes: BackendNodeDefinition[];
  connections: BackendConnection[];
}

// Summary returned by GET /api/workflows (list endpoint)
export interface ApiWorkflowSummary {
  id: string;
  name: string;
  active: boolean;
  webhook_url: string;
  node_count: number;
  created_at: string;
  updated_at: string;
}

// Response from POST /api/workflows (create)
export interface ApiCreateResponse {
  id: string;
  name: string;
  active: boolean;
  webhook_url: string;
  created_at: string;
}

// Response from POST /api/workflows/{id}/run or /api/workflows/run-adhoc
export interface ApiExecutionResult {
  status: 'success' | 'failed';
  execution_id: string;
  data: Record<string, unknown>;
  errors: Array<{ node_name: string; error: string; timestamp?: string }>;
}

// Execution list item from GET /api/executions
export interface ApiExecutionListItem {
  id: string;
  workflow_id: string;
  workflow_name: string;
  status: string;
  mode: string;
  start_time: string;
  end_time: string | null;
  error_count: number;
}

// Full API response when fetching a single workflow
export interface ApiWorkflowDetail {
  id: string;
  name: string;
  active: boolean;
  definition: {
    nodes: Array<{
      name: string;
      type: string;
      label?: string;
      parameters: Record<string, unknown>;
      position?: { x: number; y: number };
      // Node settings
      continue_on_fail?: boolean;
      retry_on_fail?: number;
      retry_delay?: number;
      pinnedData?: Array<{ json: Record<string, unknown> }>;
      // Enriched I/O data from backend
      inputs?: Array<{ name: string; displayName: string }>;
      inputCount?: number;
      outputs?: Array<{ name: string; displayName: string }>;
      outputCount?: number;
      inputStrategy?: Record<string, unknown>;
      outputStrategy?: Record<string, unknown>;
      // Node group for styling
      group?: string[];
      // Icon from node registry (e.g. "fa:bot")
      icon?: string;
      // Subnode properties
      isSubnode?: boolean;
      subnodeType?: 'model' | 'memory' | 'tool';
      subnodeSlots?: Array<{
        name: string;
        displayName: string;
        slotType: 'model' | 'memory' | 'tool';
        required: boolean;
        multiple: boolean;
      }>;
    }>;
    connections: Array<{
      source_node: string;
      target_node: string;
      source_output: string;
      target_input: string;
      connection_type?: 'normal' | 'subnode';
      slot_name?: string;
      waypoints?: Array<{ x: number; y: number }>;
    }>;
  };
}
