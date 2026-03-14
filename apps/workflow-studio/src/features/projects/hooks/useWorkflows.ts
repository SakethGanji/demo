import { useQuery } from '@tanstack/react-query';
import { workflowsApi } from '@/shared/lib/api';
import type { ApiWorkflowSummary, ApiWorkflowDetail } from '@/shared/lib/backendTypes';

// Types matching our UI needs (camelCase)
interface WorkflowSummary {
  id: string;
  name: string;
  active: boolean;
  nodeCount: number;
  createdAt: string;
  updatedAt: string;
}

export interface WorkflowDefinition {
  nodes: Array<{
    name: string;
    type: string;
    parameters: Record<string, unknown>;
    position?: { x: number; y: number };
  }>;
  connections: Array<{
    sourceNode: string;
    targetNode: string;
    sourceOutput: string;
    targetInput: string;
  }>;
}

export interface WorkflowWithDefinition extends WorkflowSummary {
  definition: WorkflowDefinition;
}

// Transform API response to UI format
function transformWorkflow(api: ApiWorkflowDetail, summary: ApiWorkflowSummary): WorkflowWithDefinition {
  return {
    id: api.id,
    name: api.name,
    active: api.active,
    nodeCount: summary.node_count,
    createdAt: summary.created_at,
    updatedAt: summary.updated_at,
    definition: {
      nodes: api.definition.nodes,
      connections: api.definition.connections.map((conn) => ({
        sourceNode: conn.source_node,
        targetNode: conn.target_node,
        sourceOutput: conn.source_output,
        targetInput: conn.target_input,
      })),
    },
  };
}

async function fetchWorkflows(): Promise<WorkflowWithDefinition[]> {
  const summaries = await workflowsApi.list();

  // Fetch definitions in parallel
  const details = await Promise.all(
    summaries.map((summary) => workflowsApi.get(summary.id))
  );

  return details.map((detail, i) => transformWorkflow(detail, summaries[i]));
}

export function useWorkflows() {
  return useQuery({
    queryKey: ['workflows'],
    queryFn: fetchWorkflows,
    staleTime: 1000 * 60 * 5,
  });
}
