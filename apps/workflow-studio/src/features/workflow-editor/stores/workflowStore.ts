import { createWithEqualityFn as create } from 'zustand/traditional';
import type { Node, Edge, Connection, NodeChange, EdgeChange } from '@xyflow/react';
import { addEdge, applyNodeChanges, applyEdgeChanges } from '@xyflow/react';
import type { WorkflowNodeData, NodeExecutionData, AgentTraceEvent, StickyNoteData, OutputStrategy } from '../types/workflow';
import type { BackendNodeData } from '@/shared/lib/backendTypes';
import type { NodeIO } from '../lib/nodeStyles';
import type { NodeTypeMetadata } from '../lib/createNodeData';
import { isTriggerType } from '../lib/nodeConfig';
import { generateNodeName, getExistingNodeNames } from '../lib/workflowTransform';

// Helper to compute dynamic outputs for nodes with outputStrategy
function computeDynamicOutputs(data: WorkflowNodeData): WorkflowNodeData {
  if (!data.outputStrategy || !data.parameters) return data;

  const strategy = data.outputStrategy as OutputStrategy;
  const params = data.parameters as Record<string, unknown>;

  if (strategy.type === 'dynamicFromParameter') {
    const paramName = strategy.parameter;
    const numOutputs = paramName ? (params[paramName] as number) || 2 : 2;
    const outputCount = numOutputs + (strategy.addFallback ? 1 : 0);

    const outputs: NodeIO[] = [];
    for (let i = 0; i < numOutputs; i++) {
      outputs.push({ name: `output${i}`, displayName: `Output ${i}` });
    }
    if (strategy.addFallback) {
      outputs.push({ name: 'fallback', displayName: 'Fallback' });
    }

    return { ...data, outputCount, outputs };
  } else if (strategy.type === 'dynamicFromCollection') {
    const collectionName = strategy.collectionName;
    const collection = collectionName ? (params[collectionName] as unknown[]) || [] : [];

    const numOutputs = collection.length + (strategy.addFallback ? 1 : 0);
    const outputCount = Math.max(1, numOutputs);

    const outputs: NodeIO[] = Array.from({ length: outputCount }, (_, i) => ({
      name: i === outputCount - 1 && strategy.addFallback ? 'fallback' : `output${i}`,
      displayName: i === outputCount - 1 && strategy.addFallback ? 'Fallback' : `Output ${i}`,
    }));

    return { ...data, outputCount, outputs };
  }

  return data;
}

// Connection validation result
interface ConnectionValidation {
  isValid: boolean;
  message?: string;
}

// Clipboard data for copy/paste
interface ClipboardData {
  nodes: Node[];
  edges: Edge[];
}

// History entry for undo/redo
interface HistoryEntry {
  nodes: Node[];
  edges: Edge[];
}

const MAX_HISTORY_SIZE = 50;

// Debounce timestamp to prevent multiple saves in same tick
let lastHistorySaveTime = 0;
const HISTORY_DEBOUNCE_MS = 50;

interface WorkflowState {
  // Workflow metadata
  workflowName: string;
  workflowTags: string[];
  isActive: boolean;
  workflowId?: string;  // Backend workflow ID (set after save)

  // Workflow data
  nodes: Node[];
  edges: Edge[];

  // Selection
  selectedNodeId: string | null;

  // Execution data per node
  executionData: Record<string, NodeExecutionData>;

  // Pinned data per node - backend format: { json: {...} }[]
  pinnedData: Record<string, BackendNodeData[]>;

  // Drag-drop state
  draggedNodeType: string | null;
  dropTargetNodeId: string | null;
  dropTargetHandleId: string | null;
  dropTargetEdgeId: string | null;

  // Clipboard for copy/paste
  clipboard: ClipboardData | null;

  // History for undo/redo
  history: HistoryEntry[];
  historyIndex: number;
  isUndoRedoAction: boolean; // Flag to prevent saving undo/redo actions to history

  // Dirty state tracking
  lastSavedSnapshot: string | null;

  // Derived state for efficient selectors
  nodeCount: number;
  edgeCount: number;
  isAnyNodeRunning: boolean;
  _canUndo: boolean;
  _canRedo: boolean;
  _isDirty: boolean;

  // Node type registry (synced from React Query cache)
  nodeTypesMap: Map<string, NodeTypeMetadata>;
  setNodeTypesMap: (map: Map<string, NodeTypeMetadata>) => void;

  // Metadata actions
  setWorkflowName: (name: string) => void;
  addTag: (tag: string) => void;
  removeTag: (tag: string) => void;
  setIsActive: (active: boolean) => void;

  // Actions
  setNodes: (nodes: Node[]) => void;
  setEdges: (edges: Edge[]) => void;
  onNodesChange: (changes: NodeChange[]) => void;
  onEdgesChange: (changes: EdgeChange[]) => void;
  onConnect: (connection: Connection) => void;
  validateConnection: (connection: Connection) => ConnectionValidation;
  isValidConnection: (connection: Connection) => boolean;

  addNode: (node: Node) => void;
  addStickyNote: (position: { x: number; y: number }) => void;
  updateNodeData: (nodeId: string, data: Partial<WorkflowNodeData>) => void;
  updateStickyNote: (nodeId: string, data: Partial<StickyNoteData>) => void;
  deleteNode: (nodeId: string) => void;

  setSelectedNode: (nodeId: string | null) => void;

  // Execution
  setNodeExecutionData: (nodeId: string, data: NodeExecutionData) => void;
  appendAgentTraceEvent: (nodeId: string, event: AgentTraceEvent) => void;
  clearExecutionData: () => void;

  // Pinned data - uses backend format { json: {...} }[]
  pinNodeData: (nodeId: string, data: BackendNodeData[]) => void;
  unpinNodeData: (nodeId: string) => void;
  hasPinnedData: (nodeId: string) => boolean;
  getPinnedDataForDisplay: (nodeId: string) => Record<string, unknown>[];

  // Workflow ID management
  setWorkflowId: (id: string) => void;

  // Load workflow from API data
  loadWorkflow: (data: {
    nodes: Node[];
    edges: Edge[];
    workflowName: string;
    workflowId: string;
    isActive: boolean;
  }) => void;

  // Reset to empty state
  resetWorkflow: () => void;

  // Drag-drop actions
  setDraggedNodeType: (type: string | null) => void;
  setDropTarget: (nodeId: string | null, handleId: string | null) => void;
  setDropTargetEdge: (edgeId: string | null) => void;
  clearDropTarget: () => void;

  // Connection helpers
  isInputConnected: (nodeId: string, handleId?: string) => boolean;
  isOutputConnected: (nodeId: string, handleId: string) => boolean;
  getNodeConnections: (nodeId: string) => { inputs: Edge[]; outputs: Edge[] };

  // Clipboard operations
  copyNodes: (nodeIds: string[]) => void;
  cutNodes: (nodeIds: string[]) => void;
  pasteNodes: (position?: { x: number; y: number }) => void;
  duplicateNodes: (nodeIds: string[]) => void;

  // History operations
  saveToHistory: () => void;
  undo: () => void;
  redo: () => void;
  canUndo: () => boolean;
  canRedo: () => boolean;

  // Edge waypoints
  updateEdgeWaypoints: (edgeId: string, waypoints: Array<{ x: number; y: number }> | undefined) => void;

  // Multi-node operations
  deleteNodes: (nodeIds: string[]) => void;
  moveNodes: (nodeIds: string[], delta: { x: number; y: number }) => void;

  // Dirty state
  markAsSaved: () => void;
  isDirty: () => boolean;

  // Export/Import
  exportWorkflow: () => string;
  importWorkflow: (json: string) => boolean;
}

/** Count only real workflow nodes (excludes placeholders and sticky notes). */
function countWorkflowNodes(nodes: Node[]) {
  return nodes.filter((n) => n.type === 'workflowNode').length;
}

/** Clone a set of nodes and their inter-edges with new IDs and unique names. */
function cloneNodesAndEdges(
  nodesToClone: Node[],
  edgesToClone: Edge[],
  existingNodes: Node[],
  offset: { x: number; y: number },
): { nodes: Node[]; edges: Edge[] } {
  const timestamp = Date.now();
  const idMap = new Map<string, string>();
  const existingNames = getExistingNodeNames(existingNodes as Node<WorkflowNodeData>[]);

  const newNodes = nodesToClone.map((node, index) => {
    const newId = `${node.id.split('-')[0]}-${timestamp}-${index}`;
    idMap.set(node.id, newId);

    const baseName = (node.data as WorkflowNodeData)?.type || 'Node';
    const newName = generateNodeName(baseName, existingNames);
    existingNames.push(newName);

    return {
      ...node,
      id: newId,
      position: {
        x: node.position.x + offset.x,
        y: node.position.y + offset.y,
      },
      selected: true,
      data: { ...node.data, name: newName },
    };
  });

  const newEdges = edgesToClone.map((edge) => ({
    ...edge,
    id: `${edge.id}-${timestamp}`,
    source: idMap.get(edge.source) || edge.source,
    target: idMap.get(edge.target) || edge.target,
  }));

  return { nodes: newNodes, edges: newEdges };
}

// Initial "Add first step" node for empty canvas
const initialNodes: Node[] = [
  {
    id: 'add-nodes-placeholder',
    type: 'addNodes',
    position: { x: 250, y: 200 },
    data: { label: 'Add first step...' },
  },
];

export const useWorkflowStore = create<WorkflowState>((set, get) => ({
  // Workflow metadata
  workflowName: 'My workflow',
  workflowTags: [],
  isActive: false,
  workflowId: undefined,

  nodes: initialNodes,
  edges: [],
  selectedNodeId: null,
  executionData: {},
  pinnedData: {},
  draggedNodeType: null,
  dropTargetNodeId: null,
  dropTargetHandleId: null,
  dropTargetEdgeId: null,
  clipboard: null,
  history: [],
  historyIndex: -1,
  isUndoRedoAction: false,
  lastSavedSnapshot: null,
  nodeCount: countWorkflowNodes(initialNodes),
  edgeCount: 0,
  isAnyNodeRunning: false,
  _canUndo: false,
  _canRedo: false,
  _isDirty: false,
  nodeTypesMap: new Map(),

  setNodeTypesMap: (map) => set({ nodeTypesMap: map }),

  // Metadata actions
  setWorkflowName: (name) => set({ workflowName: name }),
  addTag: (tag) => set({ workflowTags: [...get().workflowTags, tag] }),
  removeTag: (tag) => set({ workflowTags: get().workflowTags.filter((t) => t !== tag) }),
  setIsActive: (active) => set({ isActive: active }),

  setNodes: (nodes) => set({ nodes }),
  setEdges: (edges) => set({ edges }),

  onNodesChange: (changes) => {
    const { nodes, edges, isUndoRedoAction, saveToHistory } = get();

    // Save to history if nodes are being removed (for undo support)
    const hasRemovals = changes.some((c) => c.type === 'remove');
    if (hasRemovals && !isUndoRedoAction) {
      saveToHistory();
    }

    // Apply the changes
    const updatedNodes = applyNodeChanges(changes, nodes);

    set({ nodes: updatedNodes, nodeCount: countWorkflowNodes(updatedNodes), _isDirty: true });
  },

  onEdgesChange: (changes) => {
    // Save to history if edges are being removed (for undo support)
    const hasRemovals = changes.some((c) => c.type === 'remove');
    if (hasRemovals && !get().isUndoRedoAction) {
      get().saveToHistory();
    }

    const newEdges = applyEdgeChanges(changes, get().edges);
    set({
      edges: newEdges,
      edgeCount: newEdges.length,
      _isDirty: true,
    });
  },

  // Connection validation logic
  validateConnection: (connection) => {
    const { nodes, edges } = get();
    const sourceNode = nodes.find((n) => n.id === connection.source);
    const targetNode = nodes.find((n) => n.id === connection.target);

    // Basic validation
    if (!sourceNode || !targetNode) {
      return { isValid: false, message: 'Invalid nodes' };
    }

    // Can't connect to self
    if (connection.source === connection.target) {
      return { isValid: false, message: 'Cannot connect node to itself' };
    }

    // Can't connect to sticky notes
    if (targetNode.type === 'stickyNote' || sourceNode.type === 'stickyNote') {
      return { isValid: false, message: 'Cannot connect sticky notes' };
    }

    // Can't connect to trigger nodes (they have no inputs)
    if (isTriggerType((targetNode.data as Record<string, unknown>)?.type as string || '')) {
      return { isValid: false, message: 'Cannot connect to trigger nodes' };
    }

    // All nodes can accept multiple input connections
    // The workflow runner will handle merging inputs automatically

    // Check for duplicate connections
    const duplicateConnection = edges.some(
      (e) =>
        e.source === connection.source &&
        e.target === connection.target &&
        e.sourceHandle === connection.sourceHandle &&
        e.targetHandle === connection.targetHandle
    );
    if (duplicateConnection) {
      return { isValid: false, message: 'Connection already exists' };
    }

    // Cycles are allowed for iterative/loop workflows
    return { isValid: true };
  },

  isValidConnection: (connection) => {
    return get().validateConnection(connection).isValid;
  },

  onConnect: (connection) => {
    const validation = get().validateConnection(connection);
    if (!validation.isValid) {
      return;
    }

    const { saveToHistory } = get();

    // Save state before adding connection
    saveToHistory();

    // Normal workflow edge
    const newEdges = addEdge(
      { ...connection, type: 'workflowEdge' },
      get().edges
    );
    set({ edges: newEdges, edgeCount: newEdges.length, _isDirty: true });
  },

  addNode: (node) => {
    const { nodes, saveToHistory } = get();

    // Save state before adding node
    saveToHistory();

    // Remove the placeholder "add nodes" button if this is the first real node
    const hasOnlyPlaceholder = nodes.length === 1 && nodes[0].type === 'addNodes';

    const newNodes = hasOnlyPlaceholder ? [node] : [...nodes, node];
    set({
      nodes: newNodes,
      nodeCount: countWorkflowNodes(newNodes),
      _isDirty: true,
    });
  },

  addStickyNote: (position) => {
    const id = `sticky-${Date.now()}`;
    const stickyNode: Node = {
      id,
      type: 'stickyNote',
      position,
      data: {
        content: '',
        color: 'yellow',
      },
    };
    const newNodes = [...get().nodes, stickyNode];
    set({ nodes: newNodes, nodeCount: countWorkflowNodes(newNodes), _isDirty: true });
  },

  updateNodeData: (nodeId, data) => {
    set({
      nodes: get().nodes.map((node) => {
        if (node.id !== nodeId) return node;

        const currentData = node.data as WorkflowNodeData;
        let updatedData = { ...currentData, ...data };

        // If parameters changed and node has outputStrategy, recalculate outputs
        if (data.parameters && updatedData.outputStrategy) {
          updatedData = computeDynamicOutputs(updatedData);
        }

        return { ...node, data: updatedData };
      }),
    });
  },

  updateStickyNote: (nodeId, data) => {
    set({
      nodes: get().nodes.map((node) =>
        node.id === nodeId && node.type === 'stickyNote'
          ? { ...node, data: { ...node.data, ...data } }
          : node
      ),
    });
  },

  deleteNode: (nodeId) => {
    get().deleteNodes([nodeId]);
  },

  setSelectedNode: (nodeId) => set({ selectedNodeId: nodeId }),

  setNodeExecutionData: (nodeId, data) => {
    const newExecutionData = {
      ...get().executionData,
      [nodeId]: data,
    };
    const isAnyNodeRunning = Object.values(newExecutionData).some(
      (d) => d.status === 'running'
    );
    set({
      executionData: newExecutionData,
      isAnyNodeRunning,
    });
  },

  appendAgentTraceEvent: (nodeId, event) => {
    const current = get().executionData;
    const nodeData = current[nodeId];
    if (!nodeData) return;
    const agentTrace = [...(nodeData.agentTrace || []), event];
    set({
      executionData: {
        ...current,
        [nodeId]: { ...nodeData, agentTrace },
      },
    });
  },

  clearExecutionData: () => set({ executionData: {}, isAnyNodeRunning: false }),

  // Pinned data methods - uses backend format { json: {...} }[]
  pinNodeData: (nodeId, data) => {
    // Atomic update: both pinnedData and node.data.pinnedData in one set()
    const { pinnedData, nodes } = get();
    set({
      pinnedData: { ...pinnedData, [nodeId]: data },
      nodes: nodes.map((node) =>
        node.id === nodeId
          ? { ...node, data: { ...node.data, pinnedData: data } }
          : node
      ),
    });
  },

  unpinNodeData: (nodeId) => {
    // Atomic update: both pinnedData and node.data.pinnedData in one set()
    const { pinnedData, nodes } = get();
    const newPinnedData = { ...pinnedData };
    delete newPinnedData[nodeId];
    set({
      pinnedData: newPinnedData,
      nodes: nodes.map((node) =>
        node.id === nodeId
          ? { ...node, data: { ...node.data, pinnedData: undefined } }
          : node
      ),
    });
  },

  hasPinnedData: (nodeId) => {
    return nodeId in get().pinnedData;
  },

  // Get pinned data in display format (unwrapped from { json: {...} })
  getPinnedDataForDisplay: (nodeId) => {
    const pinned = get().pinnedData[nodeId];
    if (!pinned) return [];
    return pinned.map((item) => item.json);
  },

  // Workflow ID management
  setWorkflowId: (id) => set({ workflowId: id }),

  // Load workflow from API data
  loadWorkflow: (data) => {
    // Process nodes to compute dynamic outputs for nodes with outputStrategy
    const processedNodes = data.nodes.map((node) => {
      if (node.type === 'workflowNode' && node.data) {
        const nodeData = node.data as WorkflowNodeData;
        if (nodeData.outputStrategy) {
          return { ...node, data: computeDynamicOutputs(nodeData) };
        }
      }
      return node;
    });

    const snapshot = JSON.stringify({ nodes: processedNodes, edges: data.edges });
    set({
      nodes: processedNodes,
      edges: data.edges,
      workflowName: data.workflowName,
      workflowId: data.workflowId,
      isActive: data.isActive,
      selectedNodeId: null,
      executionData: {},
      pinnedData: {},
      lastSavedSnapshot: snapshot,
      nodeCount: countWorkflowNodes(processedNodes),
      edgeCount: data.edges.length,
      isAnyNodeRunning: false,
      _isDirty: false,
      _canUndo: false,
      _canRedo: false,
      history: [],
      historyIndex: -1,
    });
  },

  // Reset to empty state
  resetWorkflow: () =>
    set({
      nodes: initialNodes,
      edges: [],
      workflowName: 'My workflow',
      workflowId: undefined,
      isActive: false,
      selectedNodeId: null,
      executionData: {},
      pinnedData: {},
      workflowTags: [],
      clipboard: null,
      lastSavedSnapshot: null,
      draggedNodeType: null,
      dropTargetNodeId: null,
      dropTargetHandleId: null,
      dropTargetEdgeId: null,
      nodeCount: countWorkflowNodes(initialNodes),
      edgeCount: 0,
      isAnyNodeRunning: false,
      _isDirty: false,
      _canUndo: false,
      _canRedo: false,
      history: [],
      historyIndex: -1,
    }),

  // Drag-drop actions
  setDraggedNodeType: (type) => set({ draggedNodeType: type }),

  setDropTarget: (nodeId, handleId) =>
    set({ dropTargetNodeId: nodeId, dropTargetHandleId: handleId }),

  setDropTargetEdge: (edgeId) => set({ dropTargetEdgeId: edgeId }),

  clearDropTarget: () =>
    set({
      dropTargetNodeId: null,
      dropTargetHandleId: null,
      dropTargetEdgeId: null,
    }),

  // Check if a node's input is connected (for drag-drop validation)
  isInputConnected: (nodeId, handleId) => {
    const { edges } = get();
    if (handleId) {
      // Check specific input handle
      return edges.some(
        (e) => e.target === nodeId && e.targetHandle === handleId
      );
    }
    // Check if node has any input connections
    return edges.some(
      (e) => e.target === nodeId
    );
  },

  // Check if a node's output is connected
  isOutputConnected: (nodeId, handleId) => {
    const { edges } = get();
    return edges.some((e) => e.source === nodeId && e.sourceHandle === handleId);
  },

  // Get all connections for a node
  getNodeConnections: (nodeId) => {
    const { edges } = get();
    return {
      inputs: edges.filter((e) => e.target === nodeId),
      outputs: edges.filter((e) => e.source === nodeId),
    };
  },

  // Clipboard operations
  copyNodes: (nodeIds) => {
    const { nodes, edges } = get();
    const nodesToCopy = nodes.filter((n) => nodeIds.includes(n.id) && n.type !== 'addNodes');

    if (nodesToCopy.length === 0) return;

    // Get edges between copied nodes
    const nodeIdSet = new Set(nodeIds);
    const edgesToCopy = edges.filter(
      (e) => nodeIdSet.has(e.source) && nodeIdSet.has(e.target)
    );

    set({
      clipboard: {
        nodes: nodesToCopy,
        edges: edgesToCopy,
      },
    });
  },

  cutNodes: (nodeIds) => {
    const { copyNodes, deleteNodes, saveToHistory } = get();
    saveToHistory();
    copyNodes(nodeIds);
    deleteNodes(nodeIds);
  },

  pasteNodes: (position) => {
    const { clipboard, nodes, edges, saveToHistory } = get();
    if (!clipboard || clipboard.nodes.length === 0) return;

    saveToHistory();

    const minX = Math.min(...clipboard.nodes.map((n) => n.position.x));
    const minY = Math.min(...clipboard.nodes.map((n) => n.position.y));
    const offset = {
      x: position ? position.x - minX : 50,
      y: position ? position.y - minY : 50,
    };

    const cloned = cloneNodesAndEdges(clipboard.nodes, clipboard.edges, nodes, offset);
    const updatedNodes = nodes.map((n) => ({ ...n, selected: false }));

    const finalNodes = [...updatedNodes, ...cloned.nodes];
    const finalEdges = [...edges, ...cloned.edges];
    set({
      nodes: finalNodes,
      edges: finalEdges,
      nodeCount: countWorkflowNodes(finalNodes),
      edgeCount: finalEdges.length,
      _isDirty: true,
    });
  },

  duplicateNodes: (nodeIds) => {
    const { nodes, edges, saveToHistory } = get();
    const nodesToDuplicate = nodes.filter((n) => nodeIds.includes(n.id) && n.type !== 'addNodes');
    if (nodesToDuplicate.length === 0) return;

    saveToHistory();

    const nodeIdSet = new Set(nodeIds);
    const edgesToDuplicate = edges.filter(
      (e) => nodeIdSet.has(e.source) && nodeIdSet.has(e.target)
    );

    const cloned = cloneNodesAndEdges(nodesToDuplicate, edgesToDuplicate, nodes, { x: 50, y: 50 });
    const updatedNodes = nodes.map((n) => ({ ...n, selected: false }));

    const finalNodes = [...updatedNodes, ...cloned.nodes];
    const finalEdges = [...edges, ...cloned.edges];
    set({
      nodes: finalNodes,
      edges: finalEdges,
      nodeCount: countWorkflowNodes(finalNodes),
      edgeCount: finalEdges.length,
      _isDirty: true,
    });
  },

  // History operations
  saveToHistory: () => {
    const { nodes, edges, history, historyIndex, isUndoRedoAction } = get();

    // Don't save if this is an undo/redo action
    if (isUndoRedoAction) return;

    // Debounce: skip if we just saved (prevents double-save when deleting nodes with edges)
    const now = Date.now();
    if (now - lastHistorySaveTime < HISTORY_DEBOUNCE_MS) {
      return;
    }
    lastHistorySaveTime = now;

    const newEntry: HistoryEntry = {
      nodes: structuredClone(nodes),
      edges: structuredClone(edges),
    };

    // Remove any future history if we're not at the end
    const newHistory = history.slice(0, historyIndex + 1);
    newHistory.push(newEntry);

    // Keep history size limited
    if (newHistory.length > MAX_HISTORY_SIZE) {
      newHistory.shift();
    }

    const newIndex = newHistory.length - 1;
    set({
      history: newHistory,
      historyIndex: newIndex,
      _canUndo: newIndex >= 0,
      _canRedo: false, // We just trimmed future history
    });
  },

  undo: () => {
    const { history, historyIndex } = get();

    if (historyIndex < 0) return;

    // Save current state to history if this is the first undo
    let currentHistory = history;
    if (historyIndex === history.length - 1) {
      const { nodes, edges } = get();
      const currentEntry: HistoryEntry = {
        nodes: structuredClone(nodes),
        edges: structuredClone(edges),
      };
      currentHistory = [...history, currentEntry];
    }

    const previousState = currentHistory[historyIndex];
    const newIndex = historyIndex - 1;

    set({
      isUndoRedoAction: true,
      history: currentHistory,
      nodes: previousState.nodes,
      edges: previousState.edges,
      historyIndex: newIndex,
      nodeCount: countWorkflowNodes(previousState.nodes),
      edgeCount: previousState.edges.length,
      _isDirty: true,
      _canUndo: newIndex >= 0,
      _canRedo: true,
    });

    // Reset flag via microtask — runs after current synchronous subscribers
    // but before any user-triggered macrotasks (clicks, inputs)
    queueMicrotask(() => set({ isUndoRedoAction: false }));
  },

  redo: () => {
    const { history, historyIndex } = get();

    if (historyIndex >= history.length - 2) return;

    const nextState = history[historyIndex + 2];
    const newIndex = historyIndex + 1;

    set({
      isUndoRedoAction: true,
      nodes: nextState.nodes,
      edges: nextState.edges,
      historyIndex: newIndex,
      nodeCount: countWorkflowNodes(nextState.nodes),
      edgeCount: nextState.edges.length,
      _isDirty: true,
      _canUndo: true,
      _canRedo: newIndex < history.length - 2,
    });

    queueMicrotask(() => set({ isUndoRedoAction: false }));
  },

  canUndo: () => {
    const { historyIndex } = get();
    return historyIndex >= 0;
  },

  canRedo: () => {
    const { history, historyIndex } = get();
    return historyIndex < history.length - 2;
  },

  // Edge waypoints
  updateEdgeWaypoints: (edgeId, waypoints) => {
    set({
      edges: get().edges.map((edge) =>
        edge.id === edgeId
          ? { ...edge, data: { ...edge.data, waypoints } }
          : edge
      ),
    });
  },

  // Delete multiple nodes
  deleteNodes: (nodeIds) => {
    const { nodes, edges, pinnedData, saveToHistory } = get();

    if (nodeIds.length === 0) return;

    saveToHistory();

    const nodeIdSet = new Set(nodeIds);
    const remainingNodes = nodes.filter((n) => !nodeIdSet.has(n.id));
    const hasRealNodes = remainingNodes.some(
      (n) => n.type === 'workflowNode'
    );

    // Remove pinned data for deleted nodes
    const newPinnedData = { ...pinnedData };
    for (const id of nodeIds) {
      delete newPinnedData[id];
    }

    const newNodes = hasRealNodes ? remainingNodes : initialNodes;
    const newEdges = edges.filter((e) => !nodeIdSet.has(e.source) && !nodeIdSet.has(e.target));
    set({
      nodes: newNodes,
      edges: newEdges,
      nodeCount: countWorkflowNodes(newNodes),
      edgeCount: newEdges.length,
      _isDirty: true,
      selectedNodeId: nodeIds.includes(get().selectedNodeId || '') ? null : get().selectedNodeId,
      pinnedData: newPinnedData,
    });
  },

  // Move nodes by delta
  moveNodes: (nodeIds, delta) => {
    set({
      nodes: get().nodes.map((node) =>
        nodeIds.includes(node.id)
          ? {
              ...node,
              position: {
                x: node.position.x + delta.x,
                y: node.position.y + delta.y,
              },
            }
          : node
      ),
    });
  },

  // Dirty state tracking
  markAsSaved: () => {
    const { nodes, edges } = get();
    const snapshot = JSON.stringify({ nodes, edges });
    set({ lastSavedSnapshot: snapshot, _isDirty: false });
  },

  isDirty: () => {
    return get()._isDirty;
  },

  // Export workflow as JSON string
  exportWorkflow: () => {
    const { nodes, edges, workflowName, workflowTags, isActive } = get();

    // Filter out placeholder nodes
    const exportNodes = nodes.filter((n) => n.type !== 'addNodes');

    const workflow = {
      name: workflowName,
      tags: workflowTags,
      isActive,
      nodes: exportNodes,
      edges,
      exportedAt: new Date().toISOString(),
    };

    return JSON.stringify(workflow, null, 2);
  },

  // Import workflow from JSON string
  importWorkflow: (json) => {
    try {
      const workflow = JSON.parse(json);

      // Validate nodes array exists
      if (!workflow.nodes || !Array.isArray(workflow.nodes)) {
        console.error('Invalid workflow: missing nodes array');
        return false;
      }

      // Validate edges array if present
      if (workflow.edges && !Array.isArray(workflow.edges)) {
        console.error('Invalid workflow: edges must be an array');
        return false;
      }

      const edges = workflow.edges || [];

      // Filter to only workflow nodes for validation
      const workflowNodes = workflow.nodes.filter(
        (n: Node) => n.type === 'workflowNode'
      );

      // Validate node names are unique
      const nodeNames = new Set<string>();
      for (const node of workflowNodes) {
        const name = (node.data as WorkflowNodeData)?.name;
        if (!name) {
          console.error('Invalid workflow: node missing name', node);
          return false;
        }
        if (nodeNames.has(name)) {
          console.error('Invalid workflow: duplicate node name', name);
          return false;
        }
        nodeNames.add(name);
      }

      // Collect all valid node IDs
      const nodeIds = new Set<string>(
        (workflow.nodes as Node[]).map((n) => n.id)
      );

      // Validate edges reference valid nodes
      for (const edge of edges as Edge[]) {
        if (!nodeIds.has(edge.source)) {
          console.error('Invalid workflow: edge references non-existent source node', edge.source);
          return false;
        }
        if (!nodeIds.has(edge.target)) {
          console.error('Invalid workflow: edge references non-existent target node', edge.target);
          return false;
        }
      }

      const { saveToHistory } = get();
      saveToHistory();

      const importedNodes = workflow.nodes.length > 0 ? workflow.nodes : initialNodes;
      set({
        nodes: importedNodes,
        edges,
        workflowName: workflow.name || 'Imported Workflow',
        workflowTags: workflow.tags || [],
        isActive: workflow.isActive || false,
        selectedNodeId: null,
        executionData: {},
          clipboard: null,
        nodeCount: countWorkflowNodes(importedNodes),
        edgeCount: edges.length,
        _isDirty: true,
      });

      return true;
    } catch (error) {
      console.error('Failed to import workflow:', error);
      return false;
    }
  },
}));
