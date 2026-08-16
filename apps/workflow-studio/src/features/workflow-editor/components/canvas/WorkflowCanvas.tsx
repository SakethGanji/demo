import { useCallback, useEffect, useRef, useState, type DragEvent } from 'react';
import {
  ReactFlow,
  Background,
  MiniMap,
  type OnConnect,
  type OnConnectStart,
  type OnConnectEnd,
  type Connection,
  type Node,
  type NodeChange,
  type NodeRemoveChange,
  type Edge,
  BackgroundVariant,
  SelectionMode,
  useReactFlow,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { Connection as ConnectionLine } from './ConnectionLine';
import { Controls } from './Controls';

import { useWorkflowStore } from '../../stores/workflowStore';
import { useEditorLayoutStore } from '../../stores/editorLayoutStore';
import { useNDVStore } from '../../stores/ndvStore';
import { useKeyboardShortcuts } from '../../hooks/useKeyboardShortcuts';
import { useSaveWorkflow } from '../../hooks/useWorkflowApi';
import AddNodesButton from './nodes/AddNodesButton';
import WorkflowNode from './nodes/WorkflowNode';
import WorkflowEdge from './edges/WorkflowEdge';
import StickyNote from './nodes/StickyNote';
import NodeContextMenu from './NodeContextMenu';
import { KeyboardShortcutsHelp } from '../KeyboardShortcutsHelp';
import { getMiniMapColor, calculateNodeDimensions } from '../../lib/nodeStyles';
import { normalizeNodeGroup, type NodeGroup } from '../../lib/nodeConfig';
import { generateNodeName, getExistingNodeNames } from '../../lib/workflowTransform';
import { isTriggerType } from '../../lib/nodeConfig';
import { createWorkflowNodeData } from '../../lib/createNodeData';
import type { WorkflowNodeData, OutputStrategy } from '../../types/workflow';
import type { NodeIO } from '../../lib/nodeStyles';
import type { ApiProperty } from '@/shared/lib/api';

// Define custom node types - use 'any' to work around React 19 type incompatibility
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const nodeTypes: any = {
  addNodes: AddNodesButton,
  workflowNode: WorkflowNode,
  stickyNote: StickyNote,
};

// Define custom edge types
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const edgeTypes: any = {
  workflowEdge: WorkflowEdge,
};

// Distance threshold for detecting drop near a node (in pixels)
const DROP_PROXIMITY_THRESHOLD = 100;

// Hoisted constants to avoid re-creating objects on each render
const DEFAULT_EDGE_OPTIONS = { type: 'workflowEdge' };
const FIT_VIEW_OPTIONS = { padding: 0.2, maxZoom: 1 };
const SNAP_GRID: [number, number] = [20, 20];
const DELETE_KEY_CODE = ['Backspace', 'Delete'];
const PRO_OPTIONS = { hideAttribution: true };
const MINIMAP_STYLE = { marginBottom: 8, marginRight: 8 };

function getMinimapNodeColor(node: Node) {
  if (node.type === 'addNodes') return 'var(--muted)';
  const data = node.data as WorkflowNodeData;
  if (node.type === 'stickyNote') {
    const color = data?.color || 'yellow';
    const colors: Record<string, string> = {
      yellow: 'var(--sticky-yellow-border)',
      blue: 'var(--sticky-blue-border)',
      green: 'var(--sticky-green-border)',
      pink: 'var(--sticky-pink-border)',
      purple: 'var(--sticky-purple-border)',
    };
    return colors[color] || colors.yellow;
  }
  const nodeGroup = normalizeNodeGroup(
    data?.group ? [data.group] : undefined
  );
  return getMiniMapColor(nodeGroup);
}

// Extended node definition for drag data
interface DraggedNodeData {
  type: string;
  name: string;
  displayName: string;
  description: string;
  icon?: string;
  category?: string;
  group?: NodeGroup;
  inputCount?: number;
  outputCount?: number;
  inputs?: NodeIO[];
  outputs?: NodeIO[];
  outputStrategy?: OutputStrategy;
  properties?: ApiProperty[];
}

export default function WorkflowCanvas() {
  const nodes = useWorkflowStore((s) => s.nodes);
  const edges = useWorkflowStore((s) => s.edges);
  const onEdgesChange = useWorkflowStore((s) => s.onEdgesChange);
  const onConnect = useWorkflowStore((s) => s.onConnect);
  const addNode = useWorkflowStore((s) => s.addNode);
  const setSelectedNode = useWorkflowStore((s) => s.setSelectedNode);
  const isValidConnection = useWorkflowStore((s) => s.isValidConnection);
  const isInputConnected = useWorkflowStore((s) => s.isInputConnected);
  const setDraggedNodeType = useWorkflowStore((s) => s.setDraggedNodeType);
  const clearDropTarget = useWorkflowStore((s) => s.clearDropTarget);

  const storeOnNodesChange = useWorkflowStore((s) => s.onNodesChange);
  const validateConnection = useWorkflowStore((s) => s.validateConnection);
  const openNDV = useNDVStore((s) => s.openNDV);

  const closePanel = useEditorLayoutStore((s) => s.closeCreatorPanel);
  const openForConnection = useEditorLayoutStore((s) => s.openForConnection);
  const canvasMode = useEditorLayoutStore((s) => s.canvasMode);
  const { fitView, screenToFlowPosition, getNodes, getEdges } = useReactFlow();

  // Track the current connection being dragged
  const connectingRef = useRef<{ nodeId: string; handleId: string | null } | null>(null);

  // Track edge reconnection state
  const reconnectingRef = useRef(false);

  // Context menu state
  const [contextMenu, setContextMenu] = useState<{ nodeId: string; x: number; y: number } | null>(null);

  const { saveWorkflow } = useSaveWorkflow();

  // Initialize keyboard shortcuts
  const { shortcuts, isShortcutsHelpOpen, setIsShortcutsHelpOpen } = useKeyboardShortcuts({
    onSave: () => {
      saveWorkflow();
    },
  });

  // Fit view on initial load when nodes change from placeholder to real nodes
  useEffect(() => {
    const hasRealNodes = nodes.some((n) => n.type === 'workflowNode');
    if (hasRealNodes) {
      // Small delay to ensure nodes are rendered
      const timer = setTimeout(() => {
        fitView({ padding: 0.2, duration: 200, maxZoom: 1 });
      }, 50);
      return () => clearTimeout(timer);
    }
  }, [nodes.length > 1]); // Only trigger when we go from 1 node to more

  const handleConnect: OnConnect = useCallback(
    (connection) => {
      onConnect(connection);
    },
    [onConnect]
  );

  // Track when a connection drag starts
  const handleConnectStart: OnConnectStart = useCallback(
    (_, { nodeId, handleId }) => {
      connectingRef.current = { nodeId: nodeId || '', handleId };
    },
    []
  );

  // Handle when a connection drag ends (either connected or dropped in empty space)
  const handleConnectEnd: OnConnectEnd = useCallback(
    (event) => {
      const connecting = connectingRef.current;
      connectingRef.current = null;

      if (!connecting) return;

      // Check if we dropped on a valid target (ReactFlow handles this)
      // We need to check if the drop was on the pane (empty space)
      const target = event.target as HTMLElement;
      const isPane = target.classList.contains('react-flow__pane');

      if (isPane) {
        // Dropped in empty space - open node creator at this position
        const clientX = 'clientX' in event ? event.clientX : event.touches[0].clientX;
        const clientY = 'clientY' in event ? event.clientY : event.touches[0].clientY;

        const dropPosition = screenToFlowPosition({
          x: clientX,
          y: clientY,
        });

        // Open node creator with connection context and drop position
        openForConnection(connecting.nodeId, connecting.handleId || 'output-0', dropPosition);
      }
    },
    [screenToFlowPosition, openForConnection]
  );

  // Connection validation callback for ReactFlow
  const handleIsValidConnection = useCallback(
    (connection: Edge | Connection) => {
      return isValidConnection(connection as Connection);
    },
    [isValidConnection]
  );

  // Intercept node removals to protect trigger nodes
  const handleNodesChange = useCallback(
    (changes: NodeChange[]) => {
      const removals = changes.filter((c): c is NodeRemoveChange => c.type === 'remove');
      if (removals.length === 0) {
        storeOnNodesChange(changes);
        return;
      }

      const { nodes: currentNodes, nodeTypesMap } = useWorkflowStore.getState();
      const triggerRemovals = removals.filter((r) => {
        const node = currentNodes.find((n) => n.id === r.id);
        const data = node?.data as WorkflowNodeData | undefined;
        return node && isTriggerType(data?.type || '', nodeTypesMap);
      });

      if (triggerRemovals.length > 0) {
        const confirmed = window.confirm(
          'This will delete a trigger node. Are you sure?'
        );
        if (!confirmed) {
          // Filter out trigger removals, keep everything else
          const nonTriggerChanges = changes.filter(
            (c) => c.type !== 'remove' || !triggerRemovals.some((t) => t.id === c.id)
          );
          if (nonTriggerChanges.length > 0) {
            storeOnNodesChange(nonTriggerChanges);
          }
          return;
        }
      }

      storeOnNodesChange(changes);
    },
    [storeOnNodesChange]
  );

  // Handle ReactFlow internal errors
  const handleError = useCallback((id: string, message: string) => {
    console.error(`[ReactFlow Error] ${id}: ${message}`);
  }, []);

  // Open NDV on node double-click (canvas-level handler)
  const handleNodeDoubleClick = useCallback(
    (_event: React.MouseEvent, node: Node) => {
      if (node.type === 'addNodes' || node.type === 'stickyNote') return;
      openNDV(node.id);
    },
    [openNDV]
  );

  // Edge reconnection handlers (v12 API: edgesReconnectable + onReconnect)
  const handleEdgeUpdate = useCallback(
    (oldEdge: Edge, newConnection: Connection) => {
      const validation = validateConnection(newConnection);
      if (!validation.isValid) return;

      // Remove old edge and create new connection
      const currentEdges = useWorkflowStore.getState().edges;
      useWorkflowStore.getState().setEdges(
        currentEdges.filter((e) => e.id !== oldEdge.id)
      );
      onConnect(newConnection);
    },
    [validateConnection, onConnect]
  );

  const handleEdgeUpdateStart = useCallback(() => {
    reconnectingRef.current = true;
  }, []);

  const handleEdgeUpdateEnd = useCallback(() => {
    reconnectingRef.current = false;
  }, []);

  const handleSelectionChange = useCallback(
    ({ nodes: selectedNodes }: { nodes: { id: string }[] }) => {
      if (selectedNodes.length === 1) {
        setSelectedNode(selectedNodes[0].id);
      } else {
        setSelectedNode(null);
      }
    },
    [setSelectedNode]
  );

  const handlePaneClick = useCallback(() => {
    setSelectedNode(null);
    setContextMenu(null);
  }, [setSelectedNode]);

  // Handle right-click on nodes
  const handleNodeContextMenu = useCallback(
    (event: React.MouseEvent, node: Node) => {
      event.preventDefault();
      // Don't show context menu for placeholder or sticky notes
      if (node.type === 'addNodes' || node.type === 'stickyNote') return;

      setContextMenu({
        nodeId: node.id,
        x: event.clientX,
        y: event.clientY,
      });
    },
    []
  );

  // Find the nearest node to a drop position that can accept an input connection
  const findNearestConnectableNode = useCallback(
    (dropPosition: { x: number; y: number }) => {
      const flowNodes = getNodes();
      let nearestNode = null;
      let nearestDistance = DROP_PROXIMITY_THRESHOLD;

      for (const node of flowNodes) {
        // Skip placeholder and sticky notes
        if (node.type !== 'workflowNode') continue;

        // Skip trigger nodes (they have no inputs)
        const nodeData = node.data as WorkflowNodeData;

        // Skip trigger nodes (they have no inputs)
        if (isTriggerType(nodeData?.type || '', useWorkflowStore.getState().nodeTypesMap)) continue;

        // Skip nodes that already have an input connection
        if (isInputConnected(node.id)) continue;

        // Calculate node dimensions
        const inputCount = Math.max(1, nodeData.inputCount ?? nodeData.inputs?.length ?? 1);
        const outputCount = Math.max(1, nodeData.outputCount ?? nodeData.outputs?.length ?? 1);
        const dimensions = calculateNodeDimensions(inputCount, outputCount);

        // Calculate distance to the left edge of the node (where inputs are)
        const nodeLeftX = node.position.x;
        const nodeCenterY = node.position.y + dimensions.height / 2;

        const distance = Math.sqrt(
          Math.pow(dropPosition.x - nodeLeftX, 2) +
          Math.pow(dropPosition.y - nodeCenterY, 2)
        );

        // Only consider if drop is to the left of the node (input side)
        if (dropPosition.x < nodeLeftX + 20 && distance < nearestDistance) {
          nearestDistance = distance;
          nearestNode = node;
        }
      }

      return nearestNode;
    },
    [getNodes, isInputConnected]
  );

  // Find the nearest edge to a drop position (for inserting between nodes)
  const findNearestEdge = useCallback(
    (dropPosition: { x: number; y: number }) => {
      const currentEdges = getEdges();
      const currentNodes = getNodes();
      let nearestEdge = null;
      let nearestDistance = DROP_PROXIMITY_THRESHOLD;

      for (const edge of currentEdges) {
        // Get source and target nodes
        const sourceNode = currentNodes.find((n) => n.id === edge.source);
        const targetNode = currentNodes.find((n) => n.id === edge.target);

        if (!sourceNode || !targetNode) continue;

        // Calculate midpoint of the edge (approximation)
        const midX = (sourceNode.position.x + targetNode.position.x) / 2;
        const midY = (sourceNode.position.y + targetNode.position.y) / 2;

        const distance = Math.sqrt(
          Math.pow(dropPosition.x - midX, 2) +
          Math.pow(dropPosition.y - midY, 2)
        );

        if (distance < nearestDistance) {
          nearestDistance = distance;
          nearestEdge = edge;
        }
      }

      return nearestEdge;
    },
    [getEdges, getNodes]
  );

  // Handle drag over canvas
  const handleDragOver = useCallback((event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';
  }, []);

  // Handle drop on canvas
  const handleDrop = useCallback(
    (event: DragEvent<HTMLDivElement>) => {
      event.preventDefault();

      const nodeDataStr = event.dataTransfer.getData('application/reactflow-node');
      if (!nodeDataStr) return;

      let draggedNode: DraggedNodeData;
      try {
        draggedNode = JSON.parse(nodeDataStr);
      } catch {
        return;
      }

      // Convert screen coordinates to flow coordinates
      const position = screenToFlowPosition({
        x: event.clientX,
        y: event.clientY,
      });

      // Check if dropping near an unconnected node input
      const nearNode = findNearestConnectableNode(position);

      // Check if dropping on an edge
      const nearEdge = findNearestEdge(position);

      // Create the new node
      const newNodeId = `node-${Date.now()}`;
      const existingNames = getExistingNodeNames(getNodes() as Node<WorkflowNodeData>[]);
      const nodeName = generateNodeName(draggedNode.name, existingNames);

      const isTrigger = isTriggerType(draggedNode.type, useWorkflowStore.getState().nodeTypesMap);

      const nodeData = createWorkflowNodeData(
        {
          type: draggedNode.type,
          displayName: draggedNode.displayName,
          icon: draggedNode.icon,
          description: draggedNode.description,
          group: draggedNode.group,
          inputCount: isTrigger ? 0 : Math.max(1, draggedNode.inputCount ?? draggedNode.inputs?.length ?? 1),
          outputCount: Math.max(1, draggedNode.outputCount ?? draggedNode.outputs?.length ?? 1),
          inputs: draggedNode.inputs,
          outputs: draggedNode.outputs,
          outputStrategy: draggedNode.outputStrategy,
          properties: draggedNode.properties,
        },
        { name: nodeName },
      );

      // Calculate final position
      let finalPosition = position;

      // If dropping on an edge, position between the two nodes
      if (nearEdge && !nearNode) {
        const currentNodes = getNodes();
        const sourceNode = currentNodes.find((n) => n.id === nearEdge.source);
        const targetNode = currentNodes.find((n) => n.id === nearEdge.target);

        if (sourceNode && targetNode) {
          finalPosition = {
            x: (sourceNode.position.x + targetNode.position.x) / 2,
            y: (sourceNode.position.y + targetNode.position.y) / 2,
          };
        }
      }
      // If dropping near a node input, position to the left of that node
      else if (nearNode) {
        finalPosition = {
          x: nearNode.position.x - 200,
          y: nearNode.position.y,
        };
      }

      // Snap to grid
      finalPosition = {
        x: Math.round(finalPosition.x / 20) * 20,
        y: Math.round(finalPosition.y / 20) * 20,
      };

      const newNode = {
        id: newNodeId,
        type: 'workflowNode',
        position: finalPosition,
        data: nodeData,
      };

      addNode(newNode);

      // Auto-connect based on drop target
      if (nearEdge && !nearNode) {
        // Dropping on edge: delete old edge and create two new connections
        // Remove the old edge
        const filteredEdges = useWorkflowStore.getState().edges.filter((e) => e.id !== nearEdge.id);
        useWorkflowStore.getState().setEdges(filteredEdges);

        // Connect source -> new node
        if (!isTrigger) {
          onConnect({
            source: nearEdge.source,
            target: newNodeId,
            sourceHandle: nearEdge.sourceHandle ?? null,
            targetHandle: null,
          });
        }

        // Connect new node -> target
        onConnect({
          source: newNodeId,
          target: nearEdge.target,
          sourceHandle: draggedNode.outputs?.[0]?.name || 'output-0',
          targetHandle: nearEdge.targetHandle ?? null,
        });
      } else if (nearNode && !isTrigger) {
        // Dropping near a node input: connect new node to that input
        onConnect({
          source: newNodeId,
          target: nearNode.id,
          sourceHandle: draggedNode.outputs?.[0]?.name || 'output-0',
          targetHandle: null,
        });
      }

      // Clear drag state and close panel
      setDraggedNodeType(null);
      clearDropTarget();
      closePanel();
    },
    [
      screenToFlowPosition,
      findNearestConnectableNode,
      findNearestEdge,
      getNodes,
      addNode,
      onConnect,
      setDraggedNodeType,
      clearDropTarget,
      closePanel,
    ]
  );

  return (
    <div className="h-full w-full" onDragOver={handleDragOver} onDrop={handleDrop}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={handleNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={handleConnect}
        onConnectStart={handleConnectStart}
        onConnectEnd={handleConnectEnd}
        onSelectionChange={handleSelectionChange}
        onPaneClick={handlePaneClick}
        onNodeContextMenu={handleNodeContextMenu}
        onNodeDoubleClick={handleNodeDoubleClick}
        onError={handleError}
        isValidConnection={handleIsValidConnection}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        defaultEdgeOptions={DEFAULT_EDGE_OPTIONS}
        fitView
        fitViewOptions={FIT_VIEW_OPTIONS}
        snapToGrid
        snapGrid={SNAP_GRID}
        deleteKeyCode={DELETE_KEY_CODE}
        multiSelectionKeyCode="Shift"
        selectionMode={SelectionMode.Partial}
        connectionRadius={20}
        elevateEdgesOnSelect
        edgesReconnectable
        onReconnect={handleEdgeUpdate}
        onReconnectStart={handleEdgeUpdateStart}
        onReconnectEnd={handleEdgeUpdateEnd}
        connectionLineComponent={ConnectionLine}
        panOnScroll={false}
        zoomOnScroll
        panOnDrag={canvasMode === 'hand'}
        selectionOnDrag={canvasMode === 'pointer'}
        selectionKeyCode={canvasMode === 'hand' ? ['Control', 'Meta'] : null}
        nodesDraggable
        nodesConnectable
        elementsSelectable
        proOptions={PRO_OPTIONS}
        className="bg-background"
      >
        <Background
          id="grid-1"
          variant={BackgroundVariant.Dots}
          gap={20}
          size={1}
          className="text-muted-foreground/20 [&>pattern>circle]:fill-current"
        />

        <Controls showInteractive={false} />

        {/* MiniMap - colored by node group, positioned to avoid sidebar */}
        <MiniMap
          position="bottom-right"
          style={MINIMAP_STYLE}
          nodeColor={getMinimapNodeColor}
          maskColor="color-mix(in srgb, var(--card) 80%, transparent)"
          className="!bg-[var(--card)] !shadow-md !rounded-lg !border !border-[var(--border)]"
        />

      </ReactFlow>


      {/* Keyboard shortcuts help modal */}
      <KeyboardShortcutsHelp
        open={isShortcutsHelpOpen}
        onOpenChange={setIsShortcutsHelpOpen}
        shortcuts={shortcuts}
      />

      {/* Node context menu */}
      {contextMenu && (
        <NodeContextMenu
          nodeId={contextMenu.nodeId}
          x={contextMenu.x}
          y={contextMenu.y}
          onClose={() => setContextMenu(null)}
        />
      )}
    </div>
  );
}
