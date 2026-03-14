/**
 * Workflow Transformation Utilities
 *
 * Transforms between ReactFlow format (UI) and Backend API format.
 * The key differences:
 * - UI uses node.id (React Flow generated), backend uses node.name
 * - UI uses edge.source/target, backend uses connection.sourceNode/targetNode
 *
 * Node types use backend PascalCase names everywhere (Start, Set, HttpRequest, etc.)
 */

import type { Node, Edge } from '@xyflow/react';
import type { WorkflowNodeData } from '../types/workflow';
import type { WorkflowDefinition } from '@/features/projects/hooks/useWorkflows';
import { getNodeIcon, isTriggerType, normalizeNodeGroup } from './nodeConfig';
import { createWorkflowNodeData, getDefaultIO } from './createNodeData';
import type {
  BackendNodeDefinition,
  BackendConnection,
  BackendWorkflow,
  ApiWorkflowDetail,
} from '@/shared/lib/backendTypes';

// ============================================================================
// Name Generation
// ============================================================================

/**
 * Generates a unique node name based on the type and existing nodes.
 * Backend requires unique names, so we append a number if needed.
 *
 * @example
 * generateNodeName('HttpRequest', []) => 'HttpRequest'
 * generateNodeName('HttpRequest', ['HttpRequest']) => 'HttpRequest1'
 * generateNodeName('HttpRequest', ['HttpRequest', 'HttpRequest1']) => 'HttpRequest2'
 */
export function generateNodeName(
  backendType: string,
  existingNames: string[]
): string {
  const baseName = backendType;

  if (!existingNames.includes(baseName)) {
    return baseName;
  }

  let counter = 1;
  while (existingNames.includes(`${baseName}${counter}`)) {
    counter++;
  }

  return `${baseName}${counter}`;
}

// ============================================================================
// ReactFlow → Backend Transformation
// ============================================================================

/**
 * Transforms ReactFlow nodes and edges to backend workflow format.
 *
 * @param nodes - ReactFlow nodes (includes workflowNode, addNodes, stickyNote)
 * @param edges - ReactFlow edges
 * @param workflowName - Name of the workflow
 * @param workflowId - Optional workflow ID (for updates)
 */
export function toBackendWorkflow(
  nodes: Node<WorkflowNodeData>[],
  edges: Edge[],
  workflowName: string,
  workflowId?: string
): BackendWorkflow {
  // Filter to workflow nodes (exclude addNodes placeholder and sticky notes)
  const workflowNodes = nodes.filter(
    (n) => n.type === 'workflowNode'
  ) as Node<WorkflowNodeData>[];

  // Build a map from React Flow node ID to node name (for connection mapping)
  const idToName = new Map<string, string>();
  workflowNodes.forEach((node) => {
    idToName.set(node.id, node.data.name);
  });

  // Transform workflow nodes - type is already backend format
  const backendNodes: BackendNodeDefinition[] = workflowNodes.map((node) => ({
    name: node.data.name,
    type: node.data.type,
    label: node.data.label,
    parameters: node.data.parameters || {},
    position: {
      x: Math.round(node.position.x),
      y: Math.round(node.position.y),
    },
    continue_on_fail: node.data.continueOnFail || false,
    retry_on_fail: node.data.retryOnFail || 0,
    retry_delay: node.data.retryDelay || 1000,
    pinned_data: node.data.pinnedData,
  }));

  // Transform edges to connections (deduplicate by source+target+handles)
  const seenConnections = new Set<string>();
  const connections: BackendConnection[] = edges
    .filter((edge) => {
      // Include edges where both source and target are valid nodes
      const sourceName = idToName.get(edge.source);
      const targetName = idToName.get(edge.target);
      if (!sourceName || !targetName) return false;
      // Deduplicate connections with same source/target/handles
      const key = `${sourceName}::${edge.sourceHandle || ''}::${targetName}::${edge.targetHandle || ''}`;
      if (seenConnections.has(key)) return false;
      seenConnections.add(key);
      return true;
    })
    .map((edge) => {
      const edgeData = edge.data as { waypoints?: Array<{ x: number; y: number }> } | undefined;
      const waypoints = edgeData?.waypoints;

      return {
        source_node: idToName.get(edge.source)!,
        source_output: edge.sourceHandle || 'main',
        target_node: idToName.get(edge.target)!,
        target_input: edge.targetHandle || 'main',
        ...(waypoints && waypoints.length > 0 ? { waypoints } : {}),
      };
    });

  return {
    id: workflowId,
    name: workflowName,
    nodes: backendNodes,
    connections,
  };
}

// ============================================================================
// Validation Helpers
// ============================================================================

/**
 * Gets all existing node names from the nodes array
 */
export function getExistingNodeNames(nodes: Node<WorkflowNodeData>[]): string[] {
  return nodes
    .filter((n) => n.type === 'workflowNode')
    .map((n) => n.data.name);
}

/**
 * Find the upstream node name that provides input to a target node.
 * @param targetNodeName - The name of the target node
 * @param nameToId - Map from node name to node ID
 * @param edges - Array of edges with source/target IDs
 */
export function findUpstreamNodeName(
  targetNodeName: string,
  nameToId: Map<string, string>,
  edges: { source: string; target: string }[],
): string | null {
  const targetNodeId = nameToId.get(targetNodeName);
  if (!targetNodeId) return null;

  for (const edge of edges) {
    if (edge.target === targetNodeId) {
      for (const [name, id] of nameToId) {
        if (id === edge.source) return name;
      }
    }
  }
  return null;
}

/**
 * Build a name-to-ID map for workflow nodes.
 */
export function buildNameToIdMap(nodes: Node<WorkflowNodeData>[]): Map<string, string> {
  const map = new Map<string, string>();
  for (const node of nodes) {
    if (node.type === 'workflowNode') {
      map.set((node.data as WorkflowNodeData).name, node.id);
    }
  }
  return map;
}

// ============================================================================
// Backend → ReactFlow Transformation
// ============================================================================

/**
 * Transforms backend workflow to ReactFlow nodes and edges.
 */
export function fromBackendWorkflow(api: ApiWorkflowDetail): {
  nodes: Node<WorkflowNodeData>[];
  edges: Edge[];
  workflowName: string;
  workflowId: string;
  isActive: boolean;
} {
  // Build name to ID map (we use the name as the ID for simplicity)
  // Node types use backend PascalCase format directly
  const nodes: Node<WorkflowNodeData>[] = api.definition.nodes.map((node) => {
    // Resolve I/O from backend enrichment or defaults
    const defaultIO = getDefaultIO(node.type);
    const inputs = node.inputs?.map((i) => ({ name: i.name, displayName: i.displayName })) || defaultIO.inputs;
    const outputs = node.outputs?.map((o) => ({ name: o.name, displayName: o.displayName })) || defaultIO.outputs;
    const isTrigger = isTriggerType(node.type);

    // Create workflow node via factory
    const data = createWorkflowNodeData(
      {
        type: node.type,
        icon: getNodeIcon(node.type, node.icon),
        group: node.group,
        inputs,
        outputs,
        inputCount: node.inputCount ?? (isTrigger ? 0 : inputs.length),
        outputCount: node.outputCount ?? outputs.length,
        outputStrategy: node.outputStrategy as WorkflowNodeData['outputStrategy'],
      },
      {
        name: node.name,
        label: node.label || node.name,
        parameters: node.parameters,
        continueOnFail: node.continue_on_fail ?? false,
        retryOnFail: node.retry_on_fail ?? 0,
        retryDelay: node.retry_delay ?? 1000,
        pinnedData: node.pinnedData,
      },
    );

    return {
      id: node.name,
      type: 'workflowNode',
      position: node.position || { x: 0, y: 0 },
      data,
    };
  });

  // Generate unique edge IDs using source-target-handle combination
  // Sanitize to remove spaces/special chars that break SVG marker URL references
  const sanitizeId = (str: string) => str.replace(/[^a-zA-Z0-9_-]/g, '_');

  const edges: Edge[] = api.definition.connections.map((conn) => {
    const edgeId = `edge-${sanitizeId(conn.source_node)}-${sanitizeId(conn.source_output)}-${sanitizeId(conn.target_node)}-${sanitizeId(conn.target_input)}`;

    return {
      id: edgeId,
      source: conn.source_node,
      target: conn.target_node,
      sourceHandle: conn.source_output,
      targetHandle: conn.target_input,
      type: 'workflowEdge',
      ...(conn.waypoints ? { data: { waypoints: conn.waypoints } } : {}),
    };
  });

  return {
    nodes,
    edges,
    workflowName: api.name,
    workflowId: api.id,
    isActive: api.active,
  };
}

// ============================================================================
// WorkflowDefinition → Preview Data (lightweight, for listing page thumbnails)
// ============================================================================

/** Runtime node data from backend (richer than the TS type) */
interface RuntimeNode {
  name: string;
  type: string;
  parameters: Record<string, unknown>;
  position?: { x: number; y: number };
  group?: string[];
  inputCount?: number;
  outputCount?: number;
  icon?: string;
}

/**
 * Lightweight conversion from WorkflowDefinition (listing page format) to
 * ReactFlow Node/Edge arrays suitable for WorkflowSVG rendering.
 *
 * Much simpler than fromBackendWorkflow: no createWorkflowNodeData factory,
 * no editor-specific fields.
 */
export function definitionToPreviewData(definition: WorkflowDefinition): {
  nodes: Node<WorkflowNodeData>[];
  edges: Edge[];
} {
  if (definition.nodes.length === 0) return { nodes: [], edges: [] };

  // Cast to runtime type — backend sends richer data than the TS type declares
  const runtimeNodes = definition.nodes as RuntimeNode[];

  const nodes: Node<WorkflowNodeData>[] = runtimeNodes.map((node) => {
    const isTrigger = isTriggerType(node.type);
    const inputCount = isTrigger ? 0 : Math.max(1, node.inputCount ?? 1);
    const outputCount = Math.max(1, node.outputCount ?? 1);
    const group = normalizeNodeGroup(node.group);

    return {
      id: node.name,
      type: 'workflowNode',
      position: node.position || { x: 0, y: 0 },
      data: {
        name: node.name,
        type: node.type,
        label: node.name,
        group,
        icon: node.icon ? node.icon.replace('fa:', '') : getNodeIcon(node.type),
        inputCount,
        outputCount,
      } as WorkflowNodeData,
    };
  });

  // Build edges
  const edges: Edge[] = definition.connections
    .map((conn) => ({
      id: `edge-${conn.sourceNode}-${conn.sourceOutput}-${conn.targetNode}-${conn.targetInput}`,
      source: conn.sourceNode,
      target: conn.targetNode,
      sourceHandle: conn.sourceOutput,
      targetHandle: conn.targetInput,
      type: 'workflowEdge',
    }));

  return { nodes, edges };
}

