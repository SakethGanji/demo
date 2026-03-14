/**
 * Applies AI-generated operations to the workflow store.
 *
 * Supports two modes:
 * - full_workflow: Replaces the entire workflow (converts to ReactFlow format)
 * - incremental: Applies individual add/update/remove operations
 */

import type { Node } from '@xyflow/react';
import type { AIResponsePayload, AIOperation, WorkflowNodeData } from '../types/workflow';
import type { NodeTypeMetadata } from './createNodeData';
import { useWorkflowStore } from '../stores/workflowStore';
import { fromBackendWorkflow, generateNodeName, getExistingNodeNames } from './workflowTransform';
import { createWorkflowNodeData } from './createNodeData';
import { toast } from 'sonner';

/**
 * Apply an AI response payload to the current workflow.
 * Saves history first so the user can Ctrl+Z to undo.
 * Reads node type metadata from the workflow store's registry.
 */
export function applyAIResponse(payload: AIResponsePayload): void {
  const store = useWorkflowStore.getState();
  const nodeTypesMap = store.nodeTypesMap;

  // Save history before any changes
  store.saveToHistory();

  try {
    if (payload.mode === 'full_workflow' && payload.workflow) {
      validateFullWorkflow(payload.workflow);
      applyFullWorkflow(payload.workflow, nodeTypesMap);
    } else if (payload.mode === 'incremental' && payload.operations) {
      applyIncrementalOps(payload.operations, nodeTypesMap);
    }
    // 'explanation' mode — nothing to apply
  } catch (err) {
    console.error('[AI Applier] Failed to apply AI response:', err);
    toast.error('Failed to apply AI changes', {
      description:
        err instanceof Error
          ? err.message
          : 'The AI returned an invalid workflow. You can undo (Ctrl+Z) if needed.',
    });
  }
}

/**
 * Basic validation before applying a full workflow replacement.
 * Catches malformed AI output before it corrupts the canvas.
 */
function validateFullWorkflow(workflow: NonNullable<AIResponsePayload['workflow']>): void {
  if (!workflow.nodes || !Array.isArray(workflow.nodes) || workflow.nodes.length === 0) {
    throw new Error('AI returned a workflow with no nodes.');
  }

  const nodeNames = new Set<string>();
  for (const node of workflow.nodes) {
    if (!node.name || !node.type) {
      throw new Error(`Invalid node: missing name or type.`);
    }
    if (nodeNames.has(node.name)) {
      throw new Error(`Duplicate node name: "${node.name}".`);
    }
    nodeNames.add(node.name);
  }

  if (workflow.connections) {
    for (const conn of workflow.connections) {
      if (!nodeNames.has(conn.source_node)) {
        throw new Error(`Connection references unknown source node: "${conn.source_node}".`);
      }
      if (!nodeNames.has(conn.target_node)) {
        throw new Error(`Connection references unknown target node: "${conn.target_node}".`);
      }
    }
  }
}

// ------------------------------------------------------------------
// Full workflow replacement
// ------------------------------------------------------------------

function applyFullWorkflow(
  workflow: NonNullable<AIResponsePayload['workflow']>,
  nodeTypesMap?: Map<string, NodeTypeMetadata>,
): void {
  const store = useWorkflowStore.getState();

  // Convert the AI workflow to the format expected by fromBackendWorkflow
  // Keep the existing workflowId (or empty) — don't generate a fake one,
  // otherwise the EditorPage useEffect sees a new workflowId that doesn't
  // match the URL and calls resetWorkflow(), wiping the canvas.
  const apiFormat = {
    id: store.workflowId || '',
    name: workflow.name,
    active: store.isActive,
    definition: {
      nodes: workflow.nodes.map((n, idx) => {
        // Look up full metadata from cache if available
        const meta = nodeTypesMap?.get(n.type);
        return {
          name: n.name,
          type: n.type,
          parameters: n.parameters || {},
          position: { x: 250 + (idx % 4) * 300, y: 150 + Math.floor(idx / 4) * 200 },
          group: meta?.group,
          ...(meta?.inputs ? { inputs: meta.inputs } : {}),
          ...(meta?.outputs ? { outputs: meta.outputs } : {}),
          ...(meta?.inputCount !== undefined ? { inputCount: meta.inputCount } : {}),
          ...(meta?.outputCount !== undefined ? { outputCount: meta.outputCount } : {}),
          ...(meta?.outputStrategy ? { outputStrategy: meta.outputStrategy } : {}),
        };
      }),
      connections: workflow.connections.map((c) => ({
        source_node: c.source_node,
        target_node: c.target_node,
        source_output: c.source_output || 'main',
        target_input: c.target_input || 'main',
      })),
    },
  };

  // Auto-layout: arrange nodes in a chain from left to right
  autoLayoutNodes(apiFormat.definition.nodes, apiFormat.definition.connections);

  const transformed = fromBackendWorkflow(apiFormat as any);
  store.loadWorkflow(transformed);
}

/**
 * Simple auto-layout: topological sort then place left-to-right.
 */
function autoLayoutNodes(
  nodes: Array<{ name: string; type: string; parameters: Record<string, unknown>; position: { x: number; y: number } }>,
  connections: Array<{ source_node: string; target_node: string; source_output: string; target_input: string }>,
): void {
  // Build adjacency
  const outgoing = new Map<string, string[]>();
  const inDegree = new Map<string, number>();
  for (const n of nodes) {
    outgoing.set(n.name, []);
    inDegree.set(n.name, 0);
  }
  for (const c of connections) {
    outgoing.get(c.source_node)?.push(c.target_node);
    inDegree.set(c.target_node, (inDegree.get(c.target_node) || 0) + 1);
  }

  // Topological sort (Kahn's)
  const queue: string[] = [];
  for (const [name, deg] of inDegree) {
    if (deg === 0) queue.push(name);
  }

  const layers: string[][] = [];
  while (queue.length > 0) {
    const layer = [...queue];
    layers.push(layer);
    queue.length = 0;
    for (const name of layer) {
      for (const next of outgoing.get(name) || []) {
        const newDeg = (inDegree.get(next) || 1) - 1;
        inDegree.set(next, newDeg);
        if (newDeg === 0) queue.push(next);
      }
    }
  }

  // Position: each layer gets an x offset, nodes within layer get y offsets
  const nodeMap = new Map(nodes.map((n) => [n.name, n]));
  for (let col = 0; col < layers.length; col++) {
    const layer = layers[col];
    for (let row = 0; row < layer.length; row++) {
      const node = nodeMap.get(layer[row]);
      if (node) {
        node.position = { x: 250 + col * 300, y: 150 + row * 180 };
      }
    }
  }
}

// ------------------------------------------------------------------
// Incremental operations
// ------------------------------------------------------------------

function applyIncrementalOps(
  operations: AIOperation[],
  nodeTypesMap?: Map<string, NodeTypeMetadata>,
): void {
  for (const op of operations) {
    try {
      switch (op.op) {
        case 'addNode':
          applyAddNode(op, nodeTypesMap);
          break;
        case 'updateNode':
          applyUpdateNode(op);
          break;
        case 'removeNode':
          applyRemoveNode(op);
          break;
        case 'addConnection':
          applyAddConnection(op);
          break;
        case 'removeConnection':
          applyRemoveConnection(op);
          break;
      }
    } catch (err) {
      console.warn(`AI operation failed: ${op.op}`, err);
    }
  }
}

function applyAddNode(
  op: Extract<AIOperation, { op: 'addNode' }>,
  nodeTypesMap?: Map<string, NodeTypeMetadata>,
): void {
  const store = useWorkflowStore.getState();
  const existingNames = getExistingNodeNames(store.nodes as Node<WorkflowNodeData>[]);

  const name = existingNames.includes(op.name)
    ? generateNodeName(op.type, existingNames)
    : op.name;

  // Determine position
  let position = { x: 250, y: 200 };
  if (op.connect_after) {
    const afterNode = store.nodes.find(
      (n) => (n.data as WorkflowNodeData)?.name === op.connect_after
    );
    if (afterNode) {
      position = { x: afterNode.position.x + 300, y: afterNode.position.y };
    }
  } else {
    // Place to the right of the rightmost node
    const rightmost = store.nodes
      .filter((n) => n.type === 'workflowNode')
      .reduce((max, n) => (n.position.x > max ? n.position.x : max), 0);
    position = { x: rightmost + 300, y: 200 };
  }

  // Look up full metadata from cache if available, else use fallback
  const meta = nodeTypesMap?.get(op.type);
  const data = createWorkflowNodeData(
    meta ?? { type: op.type },
    {
      name,
      label: name,
      parameters: op.parameters || {},
    },
  );

  const newNode: Node<WorkflowNodeData> = {
    id: name,
    type: 'workflowNode',
    position,
    data,
  };

  store.addNode(newNode);

  // Connect after if specified
  if (op.connect_after) {
    const sourceNode = store.nodes.find(
      (n) => (n.data as WorkflowNodeData)?.name === op.connect_after
    );
    if (sourceNode) {
      store.onConnect({
        source: sourceNode.id,
        target: name,
        sourceHandle: 'main',
        targetHandle: 'main',
      });
    }
  }
}

function applyUpdateNode(op: Extract<AIOperation, { op: 'updateNode' }>): void {
  const store = useWorkflowStore.getState();
  const node = store.nodes.find(
    (n) => (n.data as WorkflowNodeData)?.name === op.name
  );
  if (!node) {
    console.warn(`updateNode: node "${op.name}" not found`);
    return;
  }
  const currentParams = (node.data as WorkflowNodeData).parameters || {};
  store.updateNodeData(node.id, {
    parameters: { ...currentParams, ...op.parameters },
  });
}

function applyRemoveNode(op: Extract<AIOperation, { op: 'removeNode' }>): void {
  const store = useWorkflowStore.getState();
  const node = store.nodes.find(
    (n) => (n.data as WorkflowNodeData)?.name === op.name
  );
  if (!node) {
    console.warn(`removeNode: node "${op.name}" not found`);
    return;
  }
  store.deleteNode(node.id);
}

function applyAddConnection(op: Extract<AIOperation, { op: 'addConnection' }>): void {
  const store = useWorkflowStore.getState();
  const sourceNode = store.nodes.find(
    (n) => (n.data as WorkflowNodeData)?.name === op.source_node
  );
  const targetNode = store.nodes.find(
    (n) => (n.data as WorkflowNodeData)?.name === op.target_node
  );
  if (!sourceNode || !targetNode) {
    console.warn(`addConnection: nodes not found`, op);
    return;
  }

  store.onConnect({
    source: sourceNode.id,
    target: targetNode.id,
    sourceHandle: op.source_output || 'main',
    targetHandle: op.target_input || 'main',
  });
}

function applyRemoveConnection(op: Extract<AIOperation, { op: 'removeConnection' }>): void {
  const store = useWorkflowStore.getState();
  const sourceNode = store.nodes.find(
    (n) => (n.data as WorkflowNodeData)?.name === op.source_node
  );
  const targetNode = store.nodes.find(
    (n) => (n.data as WorkflowNodeData)?.name === op.target_node
  );
  if (!sourceNode || !targetNode) return;

  const edgeToRemove = store.edges.find(
    (e) => e.source === sourceNode.id && e.target === targetNode.id
  );
  if (edgeToRemove) {
    store.setEdges(store.edges.filter((e) => e.id !== edgeToRemove.id));
  }
}

