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

import type { Node, Edge } from 'reactflow';
import type { WorkflowNodeData } from '../types/workflow';
import type { WorkflowDefinition } from '@/features/workflows/hooks/useWorkflows';
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
  // Filter to workflow nodes AND subnode nodes (exclude addNodes placeholder and sticky notes)
  const workflowNodes = nodes.filter(
    (n) => n.type === 'workflowNode' || n.type === 'subworkflowNode'
  ) as Node<WorkflowNodeData>[];

  const subnodeNodes = nodes.filter(
    (n) => n.type === 'subnodeNode'
  ) as Node<WorkflowNodeData>[];

  // Build a map from React Flow node ID to node name (for connection mapping)
  const idToName = new Map<string, string>();
  workflowNodes.forEach((node) => {
    idToName.set(node.id, node.data.name);
  });
  subnodeNodes.forEach((node) => {
    idToName.set(node.id, node.data.name);
  });

  // Track which nodes are subnodes for connection type
  const subnodeIds = new Set(subnodeNodes.map((n) => n.id));

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

  // Transform subnode nodes - type is already backend format
  const backendSubnodes: BackendNodeDefinition[] = subnodeNodes.map((node) => ({
    name: node.data.name,
    type: node.data.type,
    label: node.data.label,
    parameters: node.data.parameters || {},
    position: {
      x: Math.round(node.position.x),
      y: Math.round(node.position.y),
    },
    continue_on_fail: false,
    retry_on_fail: 0,
    retry_delay: 1000,
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
      const isSubnodeConnection = subnodeIds.has(edge.source);
      const edgeData = edge.data as { slotName?: string; waypoints?: Array<{ x: number; y: number }> } | undefined;
      const waypoints = edgeData?.waypoints;

      if (isSubnodeConnection) {
        // Subnode connection - source is a subnode, target is a parent node
        const slotName = edgeData?.slotName || edge.targetHandle || undefined;
        return {
          source_node: idToName.get(edge.source)!,
          source_output: edge.sourceHandle || 'config',
          target_node: idToName.get(edge.target)!,
          target_input: slotName || 'main',
          connection_type: 'subnode' as const,
          slot_name: slotName,
          ...(waypoints && waypoints.length > 0 ? { waypoints } : {}),
        };
      } else {
        // Normal connection between workflow nodes
        return {
          source_node: idToName.get(edge.source)!,
          source_output: edge.sourceHandle || 'main',
          target_node: idToName.get(edge.target)!,
          target_input: edge.targetHandle || 'main',
          ...(waypoints && waypoints.length > 0 ? { waypoints } : {}),
        };
      }
    });

  return {
    id: workflowId,
    name: workflowName,
    nodes: [...backendNodes, ...backendSubnodes],
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
    .filter((n) => n.type === 'workflowNode' || n.type === 'subworkflowNode' || n.type === 'subnodeNode')
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
    if (node.type === 'workflowNode' || node.type === 'subworkflowNode') {
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
  // First pass: identify subnodes from connections (subnode connections have connection_type: 'subnode')
  const subnodeNames = new Set<string>();
  for (const conn of api.definition.connections) {
    if (conn.connection_type === 'subnode') {
      subnodeNames.add(conn.source_node);
    }
  }

  // Build name to ID map (we use the name as the ID for simplicity)
  // Node types use backend PascalCase format directly
  const nodes: Node<WorkflowNodeData>[] = api.definition.nodes.map((node) => {
    // Determine if this is a subnode (from backend metadata or connection analysis)
    const isSubnode = node.isSubnode || subnodeNames.has(node.name);
    const subnodeType = isSubnode ? (node.subnodeType || 'tool') : null;

    // Resolve I/O from backend enrichment or defaults
    const defaultIO = getDefaultIO(node.type);
    const inputs = node.inputs?.map((i) => ({ name: i.name, displayName: i.displayName })) || defaultIO.inputs;
    const outputs = node.outputs?.map((o) => ({ name: o.name, displayName: o.displayName })) || defaultIO.outputs;
    const isTrigger = isTriggerType(node.type);

    if (isSubnode) {
      // Create subnode node via factory
      const data = createWorkflowNodeData(
        {
          type: node.type,
          icon: getNodeIcon(node.type, node.icon),
          group: node.group,
        },
        {
          name: node.name,
          label: node.label || node.name,
          parameters: node.parameters,
          isSubnode: true,
          subnodeType: node.subnodeType || subnodeType || undefined,
          nodeShape: 'circular',
        },
      );

      return {
        id: node.name,
        type: 'subnodeNode',
        position: node.position || { x: 0, y: 0 },
        data,
      };
    }

    // Check if this is an ExecuteWorkflow node with a workflowId → render as subworkflowNode
    if (node.type === 'ExecuteWorkflow' && node.parameters?.workflowId) {
      const data = createWorkflowNodeData(
        {
          type: node.type,
          icon: getNodeIcon(node.type, node.icon),
          group: node.group,
          inputs,
          outputs,
          inputCount: node.inputCount ?? 1,
          outputCount: node.outputCount ?? 1,
          outputStrategy: node.outputStrategy as WorkflowNodeData['outputStrategy'],
          subnodeSlots: node.subnodeSlots,
        },
        {
          name: node.name,
          label: node.label || node.name,
          parameters: node.parameters,
          subworkflowId: node.parameters.workflowId as string,
        },
      );

      return {
        id: node.name,
        type: 'subworkflowNode',
        position: node.position || { x: 0, y: 0 },
        data,
      };
    }

    // Create regular workflow node via factory
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
        subnodeSlots: node.subnodeSlots,
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
    const isSubnodeEdge = conn.connection_type === 'subnode';
    const edgeId = `edge-${sanitizeId(conn.source_node)}-${sanitizeId(conn.source_output)}-${sanitizeId(conn.target_node)}-${sanitizeId(conn.target_input)}`;

    if (isSubnodeEdge) {
      const slotName = conn.slot_name || conn.target_input;
      return {
        id: edgeId,
        source: conn.source_node,
        target: conn.target_node,
        sourceHandle: conn.source_output || 'config',
        targetHandle: slotName,
        type: 'subnodeEdge',
        data: {
          isSubnodeEdge: true,
          slotName,
          slotType: api.definition.nodes.find((n) => n.name === conn.source_node)?.subnodeType || 'tool',
          ...(conn.waypoints ? { waypoints: conn.waypoints } : {}),
        },
      };
    }

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

  // Auto-layout subnodes below their parent nodes and mark as stacked
  // Group subnode edges by parent (target) and slot
  const subnodeEdgesByParentSlot = new Map<string, Map<string, string[]>>();
  for (const edge of edges) {
    if (edge.type === 'subnodeEdge' && edge.data?.isSubnodeEdge) {
      const parentId = edge.target;
      const slotName = (edge.data as { slotName?: string }).slotName || edge.targetHandle || '';
      if (!subnodeEdgesByParentSlot.has(parentId)) {
        subnodeEdgesByParentSlot.set(parentId, new Map());
      }
      const slotMap = subnodeEdgesByParentSlot.get(parentId)!;
      if (!slotMap.has(slotName)) {
        slotMap.set(slotName, []);
      }
      slotMap.get(slotName)!.push(edge.source);
    }
  }

  // Position each subnode below its parent slot and mark as stacked
  const nodeMap = new Map(nodes.map((n) => [n.id, n]));
  for (const [parentId, slotMap] of subnodeEdgesByParentSlot) {
    const parentNode = nodeMap.get(parentId);
    if (!parentNode) continue;

    const parentData = parentNode.data as WorkflowNodeData;
    const slots = parentData.subnodeSlots || [];
    const parentWidth = slots.length > 0 ? Math.max(180, slots.length * 55 + 20) : 64;

    for (const [slotName, subnodeIds] of slotMap) {
      const slotIndex = slots.findIndex((s) => s.name === slotName);
      const slotCenterPercent = slots.length > 0 ? (slotIndex + 0.5) / slots.length : 0.5;
      const slotCenterX = parentNode.position.x + parentWidth * slotCenterPercent;

      subnodeIds.forEach((subnodeId, i) => {
        const subnodeNode = nodeMap.get(subnodeId);
        if (!subnodeNode) return;
        // Offset multiple subnodes horizontally (~55px apart), centered on slot
        const totalWidth = (subnodeIds.length - 1) * 55;
        const offsetX = i * 55 - totalWidth / 2;
        subnodeNode.position = {
          x: slotCenterX + offsetX - 24,
          y: parentNode.position.y + 130,
        };
        (subnodeNode.data as WorkflowNodeData).stacked = true;
      });
    }
  }

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
  isSubnode?: boolean;
  subnodeType?: 'model' | 'memory' | 'tool';
  inputCount?: number;
  outputCount?: number;
  subnodeSlots?: Array<{ name: string }>;
  icon?: string;
}

/**
 * Lightweight conversion from WorkflowDefinition (listing page format) to
 * ReactFlow Node/Edge arrays suitable for WorkflowSVG rendering.
 *
 * Much simpler than fromBackendWorkflow: no subnode stacking, no
 * createWorkflowNodeData factory, no editor-specific fields. Filters out
 * subnodes and subnode edges entirely.
 */
export function definitionToPreviewData(definition: WorkflowDefinition): {
  nodes: Node<WorkflowNodeData>[];
  edges: Edge[];
} {
  if (definition.nodes.length === 0) return { nodes: [], edges: [] };

  // Cast to runtime type — backend sends richer data than the TS type declares
  const runtimeNodes = definition.nodes as RuntimeNode[];

  // Identify subnodes to filter out
  const subnodeNames = new Set<string>();
  for (const node of runtimeNodes) {
    if (node.isSubnode) subnodeNames.add(node.name);
  }

  const mainNodes = runtimeNodes.filter((n) => !subnodeNames.has(n.name));

  const nodes: Node<WorkflowNodeData>[] = mainNodes.map((node) => {
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
        subnodeSlots: node.subnodeSlots as WorkflowNodeData['subnodeSlots'],
      } as WorkflowNodeData,
    };
  });

  // Build edges, skipping subnode connections
  const edges: Edge[] = definition.connections
    .filter((conn) => {
      if (conn.connectionType === 'subnode') return false;
      if (subnodeNames.has(conn.sourceNode) || subnodeNames.has(conn.targetNode)) return false;
      return true;
    })
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

