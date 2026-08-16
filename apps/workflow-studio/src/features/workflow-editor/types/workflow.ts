import type { NodeGroup, NodeIO } from '../lib/nodeStyles';

// ---------------------------------------------------------------------------
// AI Chat types
// ---------------------------------------------------------------------------

export interface AIChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  timestamp: Date;
  operations?: AIResponsePayload;
}

export interface AIChatRequest {
  message: string;
  session_id: string;
  workflow_context: WorkflowContextPayload | null;
  conversation_history: { role: 'user' | 'assistant'; content: string }[];
  mode_hint: 'auto' | 'generate' | 'modify' | 'explain' | 'fix';
}

interface WorkflowContextPayload {
  name: string;
  nodes: Array<{ name: string; type: string; parameters: Record<string, unknown> }>;
  connections: Array<{
    source_node: string;
    target_node: string;
    source_output: string;
    target_input: string;
  }>;
}

export interface AIResponsePayload {
  mode: 'full_workflow' | 'incremental' | 'explanation';
  workflow: {
    name: string;
    nodes: Array<{ name: string; type: string; parameters: Record<string, unknown> }>;
    connections: Array<{
      source_node: string;
      target_node: string;
      source_output?: string;
      target_input?: string;
    }>;
  } | null;
  operations: AIOperation[] | null;
  summary: string;
}

export type AIOperation =
  | { op: 'addNode'; type: string; name: string; parameters: Record<string, unknown>; connect_after?: string }
  | { op: 'updateNode'; name: string; parameters: Record<string, unknown> }
  | { op: 'removeNode'; name: string }
  | { op: 'addConnection'; source_node: string; target_node: string; source_output?: string; target_input?: string }
  | { op: 'removeConnection'; source_node: string; target_node: string };

// Output strategy for dynamic output nodes (like Switch)
export interface OutputStrategy {
  type: 'dynamicFromCollection' | 'dynamicFromParameter' | 'static';
  collectionName?: string;  // For dynamicFromCollection: parameter name containing array
  parameter?: string;       // For dynamicFromParameter: parameter name with output count
  addFallback?: boolean;    // Add a fallback output
}

// Node data types - aligned with backend schema
export interface WorkflowNodeData {
  // Required fields for backend compatibility
  name: string;           // Unique identifier used in connections (maps to backend node.name)
  type: string;           // Node type (e.g., 'HttpRequest', 'If', 'Code')

  // Display fields
  label: string;          // Display name shown in UI (can differ from name)
  icon?: string;
  description?: string;

  // Node configuration
  parameters?: Record<string, unknown>;
  disabled?: boolean;

  // User notes
  notes?: string;

  // Error handling options
  continueOnFail?: boolean;
  retryOnFail?: number;   // 0-10
  retryDelay?: number;    // ms

  // Pinned data for testing (format: { json: {...} }[])
  pinnedData?: Array<{ json: Record<string, unknown> }>;

  // For sticky notes (UI-only)
  content?: string;
  color?: 'yellow' | 'blue' | 'green' | 'pink' | 'purple';

  // Dynamic node UI metadata
  group?: NodeGroup;                    // Node category for coloring
  inputCount?: number;                  // Number of input handles
  outputCount?: number;                 // Number of output handles
  inputs?: NodeIO[];                    // Input handle definitions with names
  outputs?: NodeIO[];                   // Output handle definitions with names
  outputStrategy?: OutputStrategy;      // How to calculate dynamic outputs

  // Index signature required by @xyflow/react v12 Node<T> constraint
  [key: string]: unknown;
}

export interface StickyNoteData {
  content: string;
  color: 'yellow' | 'blue' | 'green' | 'pink' | 'purple';
  width?: number;
  height?: number;
  [key: string]: unknown;
}

// Node definition for the node creator panel
export interface NodeDefinition {
  type: string;
  name: string;
  displayName: string;
  description: string;
  icon: string;
  category: 'trigger' | 'action' | 'transform' | 'flow' | 'helper' | 'ai';
  subcategory?: string;
}

// Node creator view types
export type NodeCreatorView = 'trigger' | 'regular' | 'ai';

// Node execution metrics from backend
export interface NodeMetrics {
  // Common timing
  startedAt?: string;
  completedAt?: string;
  executionTimeMs?: number;
  executionOrder?: number;
  inputItemCount?: number;
  outputItemCount?: number;
  retries?: number;
  maxRetries?: number;
  activeOutputs?: string[];
  inputDataSizeBytes?: number;
  outputDataSizeBytes?: number;
  status?: string;
  // LLM-specific
  model?: string;
  inputTokens?: number;
  outputTokens?: number;
  totalTokens?: number;
  llmResponseTimeMs?: number;
  agentIterations?: number;
  toolCallCount?: number;
  // HTTP-specific
  requestUrl?: string;
  requestMethod?: string;
  responseStatusCode?: number;
  responseTimeMs?: number;
  responseSizeBytes?: number;
  // Flow-specific
  branchDecision?: string;
  trueCount?: number;
  falseCount?: number;
  // Extensibility
  [key: string]: unknown;
}

// Agent trace event from SSE stream
export interface AgentTraceEvent {
  type: string;           // 'agent:thinking' | 'agent:tool_call' | etc.
  timestamp: number;
  nodeName: string;       // "Banking Agent" or "Banking Agent/skill:fee_calculator"
  data: Record<string, unknown>;
}

// Trace tree node — agent-specific now, extensible for all nodes later
export type TraceNode =
  // Agent-specific
  | { kind: 'agent'; name: string; duration?: number; iterations?: number; children: TraceNode[] }
  | { kind: 'iteration'; number: number; children: TraceNode[] }
  | { kind: 'thinking'; content: string }
  | { kind: 'plan'; content: string }
  | { kind: 'reflect'; content: string }
  | { kind: 'tool_call'; tool: string; input: unknown; result?: unknown; isError?: boolean; duration?: number; id?: string }
  | { kind: 'spawn'; skill?: string; task: string; input?: unknown; result?: unknown; duration?: number; children: TraceNode[] }
  | { kind: 'validation'; status: string; errors?: string[] }
  | { kind: 'response'; content: string }
  // Universal (future — all nodes)
  | { kind: 'input'; itemCount: number; preview: unknown }
  | { kind: 'output'; itemCount: number; preview: unknown; error?: string }
  | { kind: 'processing'; label: string; detail?: string }
  | { kind: 'branch'; decision: string; output?: string };

// Execution data
interface ExecutionData {
  items: Record<string, unknown>[];
  error?: string;
}

export interface NodeExecutionData {
  input: ExecutionData | null;
  output: ExecutionData | null;
  startTime?: number;
  endTime?: number;
  status: 'idle' | 'running' | 'success' | 'error';
  metrics?: NodeMetrics;
  agentTrace?: AgentTraceEvent[];
}


