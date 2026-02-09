/**
 * Types for the AI Chat sidebar feature.
 */

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
