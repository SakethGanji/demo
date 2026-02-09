import { create } from 'zustand';
import type { Node, Edge, Connection, NodeChange, EdgeChange } from 'reactflow';
import { addEdge, applyNodeChanges, applyEdgeChanges } from 'reactflow';
import type { WorkflowNodeData, NodeExecutionData, StickyNoteData, SubnodeEdgeData, SubnodeType, OutputStrategy } from '../types/workflow';
import type { BackendNodeData } from '@/shared/lib/backendTypes';
import type { NodeIO } from '../lib/nodeStyles';
import type { NodeTypeMetadata } from '../lib/createNodeData';
import { isTriggerType } from '../lib/nodeConfig';
import { generateNodeName, getExistingNodeNames } from '../lib/workflowTransform';
import { createWorkflowNodeData, createReactFlowNode } from '../lib/createNodeData';
import { SUBNODE_SLOT_NAMES } from '../lib/nodeConfig';

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

  // Subworkflow execution data: parentNodeId → { innerNodeName: NodeExecutionData }
  subworkflowExecutionData: Record<string, Record<string, NodeExecutionData>>;

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
  addSubworkflowNode: (workflowId: string, workflowName: string) => void;
  copyWorkflowNodes: (workflowName: string, definition: { nodes: Array<{ name: string; type: string; parameters: Record<string, unknown>; position?: { x: number; y: number } }>; connections: Array<{ sourceNode: string; targetNode: string; sourceOutput: string; targetInput: string }> }) => void;
  addSubnode: (parentId: string, slotName: string, subnodeData: {
    type: string;
    label: string;
    icon?: string;
    subnodeType: SubnodeType;
    properties?: Record<string, unknown>;
  }) => void;
  addStickyNote: (position: { x: number; y: number }) => void;
  updateNodeData: (nodeId: string, data: Partial<WorkflowNodeData>) => void;
  updateStickyNote: (nodeId: string, data: Partial<StickyNoteData>) => void;
  deleteNode: (nodeId: string) => void;

  setSelectedNode: (nodeId: string | null) => void;

  // Execution
  setNodeExecutionData: (nodeId: string, data: NodeExecutionData) => void;
  setSubworkflowNodeExecutionData: (parentId: string, innerNodeName: string, data: NodeExecutionData) => void;
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
  subworkflowExecutionData: {},
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

    // Check for position changes on parent nodes with subnodes
    const positionChanges = changes.filter(
      (c): c is NodeChange & { type: 'position'; position: { x: number; y: number }; id: string } =>
        c.type === 'position' && 'position' in c && c.position !== undefined
    );

    // Calculate subnode movements based on parent movements
    const subnodeUpdates: Map<string, { x: number; y: number }> = new Map();

    for (const change of positionChanges) {
      const parentNode = nodes.find((n) => n.id === change.id);
      if (!parentNode || !parentNode.data?.subnodeSlots) continue;

      // Find all subnodes connected to this parent
      const subnodeEdges = edges.filter(
        (e) => e.target === parentNode.id && e.data?.isSubnodeEdge
      );

      if (subnodeEdges.length === 0) continue;

      // Calculate delta from old position to new position
      const deltaX = change.position.x - parentNode.position.x;
      const deltaY = change.position.y - parentNode.position.y;

      // Move each connected subnode by the same delta
      for (const edge of subnodeEdges) {
        const subnodeNode = nodes.find((n) => n.id === edge.source);
        if (subnodeNode) {
          subnodeUpdates.set(subnodeNode.id, {
            x: subnodeNode.position.x + deltaX,
            y: subnodeNode.position.y + deltaY,
          });
        }
      }
    }

    // Apply the changes
    let updatedNodes = applyNodeChanges(changes, nodes);

    // Apply subnode position updates
    if (subnodeUpdates.size > 0) {
      updatedNodes = updatedNodes.map((node) => {
        const update = subnodeUpdates.get(node.id);
        if (update) {
          return {
            ...node,
            position: update,
          };
        }
        return node;
      });
    }

    set({ nodes: updatedNodes });
  },

  onEdgesChange: (changes) => {
    // Save to history if edges are being removed (for undo support)
    const hasRemovals = changes.some((c) => c.type === 'remove');
    if (hasRemovals && !get().isUndoRedoAction) {
      get().saveToHistory();
    }

    set({
      edges: applyEdgeChanges(changes, get().edges),
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

    // Check if this is a subnode connection (subnode -> parent slot)
    const isSubnodeConnection =
      sourceNode.type === 'subnodeNode' &&
      SUBNODE_SLOT_NAMES.includes(connection.targetHandle || '');

    if (isSubnodeConnection) {
      // Validate subnode slot compatibility
      const targetSlots = targetNode.data?.subnodeSlots || [];
      const slot = targetSlots.find((s: { name: string }) => s.name === connection.targetHandle);

      if (!slot) {
        return { isValid: false, message: 'Invalid subnode slot' };
      }

      // Check slot type matches subnode type
      if (slot.slotType !== sourceNode.data?.subnodeType) {
        return {
          isValid: false,
          message: `Slot expects ${slot.slotType}, got ${sourceNode.data?.subnodeType}`
        };
      }

      // Check if slot already has connection (unless multiple allowed)
      if (!slot.multiple) {
        const existingConnection = edges.find(
          (e) => e.target === connection.target && e.targetHandle === connection.targetHandle
        );
        if (existingConnection) {
          return { isValid: false, message: 'Slot already connected' };
        }
      }

      // Check for duplicate subnode connections
      const duplicateConnection = edges.some(
        (e) =>
          e.source === connection.source &&
          e.target === connection.target &&
          e.targetHandle === connection.targetHandle
      );
      if (duplicateConnection) {
        return { isValid: false, message: 'Connection already exists' };
      }

      return { isValid: true };
    }

    // Normal connection validation
    // Can't connect to trigger nodes (they have no inputs)
    if (isTriggerType(targetNode.data?.type || '')) {
      return { isValid: false, message: 'Cannot connect to trigger nodes' };
    }

    // Subnodes can only connect to parent slots, not normal inputs
    if (sourceNode.type === 'subnodeNode') {
      return { isValid: false, message: 'Subnodes can only connect to parent node slots' };
    }

    // subworkflowNode is treated the same as workflowNode for connection validation

    // Can't connect normal nodes to subnode slots
    if (SUBNODE_SLOT_NAMES.includes(connection.targetHandle || '')) {
      return { isValid: false, message: 'Only subnodes can connect to this slot' };
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

    const { nodes, saveToHistory } = get();

    // Save state before adding connection
    saveToHistory();
    const sourceNode = nodes.find((n) => n.id === connection.source);

    // Check if this is a subnode connection
    const isSubnodeConnection =
      sourceNode?.type === 'subnodeNode' &&
      SUBNODE_SLOT_NAMES.includes(connection.targetHandle || '');

    if (isSubnodeConnection) {
      // Create subnode edge with proper data
      const subnodeEdgeData: SubnodeEdgeData = {
        isSubnodeEdge: true,
        slotName: connection.targetHandle || '',
        slotType: sourceNode?.data?.subnodeType || 'tool',
      };

      set({
        edges: addEdge(
          {
            ...connection,
            type: 'subnodeEdge',
            data: subnodeEdgeData,
          },
          get().edges
        ),
      });
    } else {
      // Normal workflow edge
      set({
        edges: addEdge(
          { ...connection, type: 'workflowEdge' },
          get().edges
        ),
      });
    }
  },

  addNode: (node) => {
    const { nodes, saveToHistory } = get();

    // Save state before adding node
    saveToHistory();

    // Remove the placeholder "add nodes" button if this is the first real node
    const hasOnlyPlaceholder = nodes.length === 1 && nodes[0].type === 'addNodes';

    set({
      nodes: hasOnlyPlaceholder
        ? [node]
        : [...nodes, node],
    });
  },

  addSubworkflowNode: (workflowId, workflowName) => {
    const { nodes, nodeTypesMap, saveToHistory } = get();

    saveToHistory();

    const existingNames = getExistingNodeNames(nodes as Node<WorkflowNodeData>[]);

    // Position to the right of the rightmost node
    const realNodes = nodes.filter((n) => n.type !== 'addNodes');
    let position = { x: 250, y: 200 };
    if (realNodes.length > 0) {
      const maxX = Math.max(...realNodes.map((n) => n.position.x));
      const avgY = realNodes.reduce((sum, n) => sum + n.position.y, 0) / realNodes.length;
      position = { x: maxX + 250, y: avgY };
    }

    const meta = nodeTypesMap.get('ExecuteWorkflow');
    const newNode = createReactFlowNode(
      meta ?? { type: 'ExecuteWorkflow' },
      {
        nodeType: 'subworkflowNode',
        position,
        existingNames,
        overrides: {
          label: workflowName,
          parameters: { workflowId },
          subworkflowId: workflowId,
        },
      },
    );

    // Ensure description is set
    (newNode.data as WorkflowNodeData).description = `Execute workflow: ${workflowName}`;

    // Remove placeholder if it's the only node
    const hasOnlyPlaceholder = nodes.length === 1 && nodes[0].type === 'addNodes';

    set({
      nodes: hasOnlyPlaceholder ? [newNode] : [...nodes, newNode],
    });
  },

  copyWorkflowNodes: (workflowName, definition) => {
    const { nodes, edges, nodeTypesMap, saveToHistory } = get();

    saveToHistory();

    // Collect existing names for uniqueness
    const existingNames = getExistingNodeNames(nodes as Node<WorkflowNodeData>[]);

    // Position offset: place copied nodes to the right of the rightmost existing node
    const realNodes = nodes.filter((n) => n.type !== 'addNodes');
    let offsetX = 250;
    let offsetY = 200;
    if (realNodes.length > 0) {
      const maxX = Math.max(...realNodes.map((n) => n.position.x));
      const avgY = realNodes.reduce((sum, n) => sum + n.position.y, 0) / realNodes.length;
      offsetX = maxX + 300;
      offsetY = avgY;
    }

    // Find the bounding box of the source workflow nodes so we can rebase positions
    const srcNodes = definition.nodes;
    const srcMinX = srcNodes.length > 0 ? Math.min(...srcNodes.map((n) => n.position?.x ?? 0)) : 0;
    const srcMinY = srcNodes.length > 0 ? Math.min(...srcNodes.map((n) => n.position?.y ?? 0)) : 0;

    // Map old node names to new unique names and new IDs
    const nameMap = new Map<string, string>(); // oldName → newName
    const timestamp = Date.now();

    const newNodes: Node[] = srcNodes.map((srcNode, index) => {
      const newName = generateNodeName(srcNode.type, existingNames);
      existingNames.push(newName);
      nameMap.set(srcNode.name, newName);

      const position = {
        x: offsetX + ((srcNode.position?.x ?? 0) - srcMinX),
        y: offsetY + ((srcNode.position?.y ?? 0) - srcMinY),
      };

      // Look up full metadata from cache if available
      const meta = nodeTypesMap?.get(srcNode.type);
      const data = createWorkflowNodeData(
        meta ?? { type: srcNode.type },
        {
          name: newName,
          label: newName,
          parameters: { ...srcNode.parameters },
        },
      );

      return {
        id: `node-${timestamp}-${index}`,
        type: 'workflowNode',
        position,
        data,
      };
    });

    // Build name-to-new-id map for edge creation
    const nameToId = new Map<string, string>();
    srcNodes.forEach((srcNode, index) => {
      const newName = nameMap.get(srcNode.name)!;
      const newNode = newNodes[index];
      nameToId.set(srcNode.name, newNode.id);
      // Also map by new name
      nameToId.set(newName, newNode.id);
    });

    // Create edges between copied nodes
    const newEdges: Edge[] = definition.connections
      .filter((conn) => nameToId.has(conn.sourceNode) && nameToId.has(conn.targetNode))
      .map((conn, index) => ({
        id: `edge-copy-${timestamp}-${index}`,
        source: nameToId.get(conn.sourceNode)!,
        target: nameToId.get(conn.targetNode)!,
        sourceHandle: conn.sourceOutput,
        targetHandle: conn.targetInput,
        type: 'workflowEdge',
      }));

    // Remove placeholder if it's the only node
    const hasOnlyPlaceholder = nodes.length === 1 && nodes[0].type === 'addNodes';

    set({
      nodes: hasOnlyPlaceholder ? newNodes : [...nodes, ...newNodes],
      edges: [...edges, ...newEdges],
    });
  },

  addSubnode: (parentId, slotName, subnodeData) => {
    const { nodes, edges } = get();
    const parentNode = nodes.find((n) => n.id === parentId);
    if (!parentNode) return;

    // Get slots from parent
    const slots = parentNode.data?.subnodeSlots || [];
    const slotIndex = slots.findIndex((s: { name: string }) => s.name === slotName);
    if (slotIndex === -1) return;

    // Save state before adding subnode (for undo support)
    get().saveToHistory();

    // Calculate parent node width (nodes with slots are ~180px wide)
    const parentWidth = slots.length > 0 ? Math.max(180, slots.length * 55 + 20) : 64;

    // Calculate subnode position below the parent slot
    // Each slot takes up equal space across the parent width
    const slotCenterPercent = (slotIndex + 0.5) / slots.length;
    const slotCenterX = parentNode.position.x + (parentWidth * slotCenterPercent);
    const subnodeX = slotCenterX - 24; // Subtract half subnode width (48px / 2)
    const subnodeY = parentNode.position.y + 130; // Below parent node

    // Generate unique name for the subnode (backend requires unique names)
    const existingNames = getExistingNodeNames(nodes as Node<WorkflowNodeData>[]);

    const nodeId = `${subnodeData.type}-${Date.now()}`;

    // Create subnode via factory (marked as stacked so it's rendered as badge on parent)
    const newNode = createReactFlowNode(
      {
        type: subnodeData.type,
        icon: subnodeData.icon || 'wrench',
        subnodeType: subnodeData.subnodeType,
      },
      {
        id: nodeId,
        nodeType: 'subnodeNode',
        position: { x: subnodeX, y: subnodeY },
        existingNames,
        overrides: {
          label: subnodeData.label,
          parameters: subnodeData.properties || {},
          isSubnode: true,
          subnodeType: subnodeData.subnodeType,
          nodeShape: 'circular',
          stacked: true,
        },
      },
    );

    // Create edge connecting subnode to parent slot
    const subnodeEdgeData: SubnodeEdgeData = {
      isSubnodeEdge: true,
      slotName,
      slotType: subnodeData.subnodeType,
    };

    const newEdge: Edge = {
      id: `${newNode.id}-${parentId}-config-${slotName}`,
      source: newNode.id,
      target: parentId,
      sourceHandle: 'config',
      targetHandle: slotName,
      type: 'subnodeEdge',
      data: subnodeEdgeData,
    };

    set({
      nodes: [...nodes, newNode],
      edges: [...edges, newEdge],
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
    set({ nodes: [...get().nodes, stickyNode] });
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
    const { nodes, edges, pinnedData } = get();

    // If deleting the last node, restore the placeholder
    const remainingNodes = nodes.filter((n) => n.id !== nodeId);
    const hasRealNodes = remainingNodes.some(
      (n) => n.type === 'workflowNode' || n.type === 'subworkflowNode' || n.type === 'subnodeNode'
    );

    // Remove pinned data for deleted node
    const newPinnedData = { ...pinnedData };
    delete newPinnedData[nodeId];

    set({
      nodes: hasRealNodes
        ? remainingNodes
        : initialNodes,
      edges: edges.filter(
        (e) => e.source !== nodeId && e.target !== nodeId
      ),
      selectedNodeId: get().selectedNodeId === nodeId ? null : get().selectedNodeId,
      pinnedData: newPinnedData,
    });
  },

  setSelectedNode: (nodeId) => set({ selectedNodeId: nodeId }),

  setNodeExecutionData: (nodeId, data) => {
    set({
      executionData: {
        ...get().executionData,
        [nodeId]: data,
      },
    });
  },

  setSubworkflowNodeExecutionData: (parentId, innerNodeName, data) => {
    const current = get().subworkflowExecutionData;
    set({
      subworkflowExecutionData: {
        ...current,
        [parentId]: {
          ...(current[parentId] || {}),
          [innerNodeName]: data,
        },
      },
    });
  },

  clearExecutionData: () => set({ executionData: {}, subworkflowExecutionData: {} }),

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
      if ((node.type === 'workflowNode' || node.type === 'subworkflowNode') && node.data) {
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
      subworkflowExecutionData: {},
      pinnedData: {},
      lastSavedSnapshot: snapshot,
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
      subworkflowExecutionData: {},
      pinnedData: {},
      workflowTags: [],
      draggedNodeType: null,
      dropTargetNodeId: null,
      dropTargetHandleId: null,
      dropTargetEdgeId: null,
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
        (e) => e.target === nodeId && e.targetHandle === handleId && !e.data?.isSubnodeEdge
      );
    }
    // Check if node has any input connections (excluding subnode edges)
    return edges.some(
      (e) => e.target === nodeId && !e.data?.isSubnodeEdge && !SUBNODE_SLOT_NAMES.includes(e.targetHandle || '')
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
      inputs: edges.filter((e) => e.target === nodeId && !e.data?.isSubnodeEdge),
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

    // Calculate offset for pasted nodes
    const minX = Math.min(...clipboard.nodes.map((n) => n.position.x));
    const minY = Math.min(...clipboard.nodes.map((n) => n.position.y));

    // Default paste position is offset from original, or use provided position
    const offsetX = position ? position.x - minX : 50;
    const offsetY = position ? position.y - minY : 50;

    // Create ID mapping for new nodes
    const idMap = new Map<string, string>();
    const nameMap = new Map<string, string>();
    const timestamp = Date.now();

    // Collect existing names including the ones we're about to create
    const existingNames = getExistingNodeNames(nodes as Node<WorkflowNodeData>[]);

    // Create new nodes with new IDs and unique names
    const newNodes = clipboard.nodes.map((node, index) => {
      const newId = `${node.id.split('-')[0]}-${timestamp}-${index}`;
      idMap.set(node.id, newId);

      // Generate unique name using the same logic as drag-drop
      const baseName = (node.data as WorkflowNodeData)?.type || 'Node';
      const newName = generateNodeName(baseName, existingNames);
      existingNames.push(newName); // Track for subsequent nodes in this paste
      nameMap.set((node.data as WorkflowNodeData)?.name || '', newName);

      return {
        ...node,
        id: newId,
        position: {
          x: node.position.x + offsetX,
          y: node.position.y + offsetY,
        },
        selected: true,
        data: {
          ...node.data,
          name: newName,
        },
      };
    });

    // Create new edges with updated IDs
    const newEdges = clipboard.edges.map((edge) => ({
      ...edge,
      id: `${edge.id}-${timestamp}`,
      source: idMap.get(edge.source) || edge.source,
      target: idMap.get(edge.target) || edge.target,
    }));

    // Deselect existing nodes
    const updatedNodes = nodes.map((n) => ({ ...n, selected: false }));

    set({
      nodes: [...updatedNodes, ...newNodes],
      edges: [...edges, ...newEdges],
    });
  },

  duplicateNodes: (nodeIds) => {
    const { nodes, edges, saveToHistory } = get();
    const nodesToDuplicate = nodes.filter((n) => nodeIds.includes(n.id) && n.type !== 'addNodes');

    if (nodesToDuplicate.length === 0) return;

    saveToHistory();

    // Get edges between duplicated nodes
    const nodeIdSet = new Set(nodeIds);
    const edgesToDuplicate = edges.filter(
      (e) => nodeIdSet.has(e.source) && nodeIdSet.has(e.target)
    );

    // Create ID mapping for new nodes
    const idMap = new Map<string, string>();
    const timestamp = Date.now();

    // Collect existing names including the ones we're about to create
    const existingNames = getExistingNodeNames(nodes as Node<WorkflowNodeData>[]);

    // Create new nodes with offset position and unique names
    const newNodes = nodesToDuplicate.map((node, index) => {
      const newId = `${node.id.split('-')[0]}-${timestamp}-${index}`;
      idMap.set(node.id, newId);

      // Generate unique name using the same logic as drag-drop
      const baseName = (node.data as WorkflowNodeData)?.type || 'Node';
      const newName = generateNodeName(baseName, existingNames);
      existingNames.push(newName); // Track for subsequent nodes in this duplication

      return {
        ...node,
        id: newId,
        position: {
          x: node.position.x + 50,
          y: node.position.y + 50,
        },
        selected: true,
        data: {
          ...node.data,
          name: newName,
        },
      };
    });

    // Create new edges
    const newEdges = edgesToDuplicate.map((edge) => ({
      ...edge,
      id: `${edge.id}-${timestamp}`,
      source: idMap.get(edge.source) || edge.source,
      target: idMap.get(edge.target) || edge.target,
    }));

    // Deselect existing nodes and add new ones
    const updatedNodes = nodes.map((n) => ({ ...n, selected: false }));

    set({
      nodes: [...updatedNodes, ...newNodes],
      edges: [...edges, ...newEdges],
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
      nodes: JSON.parse(JSON.stringify(nodes)),
      edges: JSON.parse(JSON.stringify(edges)),
    };

    // Remove any future history if we're not at the end
    const newHistory = history.slice(0, historyIndex + 1);
    newHistory.push(newEntry);

    // Keep history size limited
    if (newHistory.length > MAX_HISTORY_SIZE) {
      newHistory.shift();
    }

    set({
      history: newHistory,
      historyIndex: newHistory.length - 1,
    });
  },

  undo: () => {
    const { history, historyIndex } = get();

    if (historyIndex < 0) return;

    // Save current state to history if this is the first undo
    if (historyIndex === history.length - 1) {
      const { nodes, edges } = get();
      const currentEntry: HistoryEntry = {
        nodes: JSON.parse(JSON.stringify(nodes)),
        edges: JSON.parse(JSON.stringify(edges)),
      };
      const newHistory = [...history, currentEntry];
      set({ history: newHistory });
    }

    const previousState = history[historyIndex];

    set({
      isUndoRedoAction: true,
      nodes: previousState.nodes,
      edges: previousState.edges,
      historyIndex: historyIndex - 1,
    });

    // Reset flag via microtask — runs after current synchronous subscribers
    // but before any user-triggered macrotasks (clicks, inputs)
    queueMicrotask(() => set({ isUndoRedoAction: false }));
  },

  redo: () => {
    const { history, historyIndex } = get();

    if (historyIndex >= history.length - 2) return;

    const nextState = history[historyIndex + 2];

    set({
      isUndoRedoAction: true,
      nodes: nextState.nodes,
      edges: nextState.edges,
      historyIndex: historyIndex + 1,
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
      (n) => n.type === 'workflowNode' || n.type === 'subworkflowNode' || n.type === 'subnodeNode'
    );

    // Remove pinned data for deleted nodes
    const newPinnedData = { ...pinnedData };
    for (const id of nodeIds) {
      delete newPinnedData[id];
    }

    set({
      nodes: hasRealNodes ? remainingNodes : initialNodes,
      edges: edges.filter((e) => !nodeIdSet.has(e.source) && !nodeIdSet.has(e.target)),
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
    set({ lastSavedSnapshot: snapshot });
  },

  isDirty: () => {
    const { nodes, edges, lastSavedSnapshot } = get();
    if (lastSavedSnapshot === null) return false;
    const current = JSON.stringify({ nodes, edges });
    return current !== lastSavedSnapshot;
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

      // Filter to only workflow/subnode nodes for validation
      const workflowNodes = workflow.nodes.filter(
        (n: Node) => n.type === 'workflowNode' || n.type === 'subnodeNode'
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

      // Validate no cycles in the graph (excluding subnode edges)
      const normalEdges = (edges as Edge[]).filter((e) => !e.data?.isSubnodeEdge);
      const hasCycle = (() => {
        const adjacency = new Map<string, string[]>();
        for (const edge of normalEdges) {
          if (!adjacency.has(edge.source)) {
            adjacency.set(edge.source, []);
          }
          adjacency.get(edge.source)!.push(edge.target);
        }

        const visited = new Set<string>();
        const recursionStack = new Set<string>();

        const dfs = (nodeId: string): boolean => {
          visited.add(nodeId);
          recursionStack.add(nodeId);

          const neighbors = adjacency.get(nodeId) || [];
          for (const neighbor of neighbors) {
            if (!visited.has(neighbor)) {
              if (dfs(neighbor)) return true;
            } else if (recursionStack.has(neighbor)) {
              return true; // Cycle detected
            }
          }

          recursionStack.delete(nodeId);
          return false;
        };

        for (const nodeId of nodeIds) {
          if (!visited.has(nodeId)) {
            if (dfs(nodeId)) return true;
          }
        }
        return false;
      })();

      if (hasCycle) {
        console.error('Invalid workflow: contains a cycle');
        return false;
      }

      const { saveToHistory } = get();
      saveToHistory();

      set({
        nodes: workflow.nodes.length > 0 ? workflow.nodes : initialNodes,
        edges,
        workflowName: workflow.name || 'Imported Workflow',
        workflowTags: workflow.tags || [],
        isActive: workflow.isActive || false,
        selectedNodeId: null,
        executionData: {},
      });

      return true;
    } catch (error) {
      console.error('Failed to import workflow:', error);
      return false;
    }
  },
}));
