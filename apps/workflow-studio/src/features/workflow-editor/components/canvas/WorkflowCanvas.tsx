import { registerEditorAction } from '../../stores/editorActionsStore';
import { useCallback, useEffect, useMemo, useRef, useState, type DragEvent } from 'react';
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
import '@/shared/motion/motion.css';
import './stageCanvas.css';
import { Connection as ConnectionLine } from './ConnectionLine';
import { Controls } from './Controls';

import { useWorkflowStore } from '../../stores/workflowStore';
import { useEditorLayoutStore } from '../../stores/editorLayoutStore';
import { useNDVStore } from '../../stores/ndvStore';
import { useHistoryStore } from '../../stores/historyStore';
import { useKeyboardShortcuts } from '../../hooks/useKeyboardShortcuts';
import { useSaveWorkflow } from '../../hooks/useWorkflowApi';
import AddNodesButton from './nodes/AddNodesButton';
import BlueprintNode from './nodes/BlueprintNode';
import AiPresence from './AiPresence';
import BuildHeadGrid from './BuildHeadGrid';
import ProposalPill from './ProposalPill';
import { useStageCamera } from './useStageCamera';
import { fallbackPanelInset, rightInset } from './stageGeometry';
import { useStageStore } from '@/features/ai-builder/stage/stageStore';
import { useBuildPlayer } from '@/features/ai-builder/stage/useBuildPlayer';
import WorkflowNode from './nodes/WorkflowNode';
import WorkflowEdge from './edges/WorkflowEdge';
import StickyNote from './nodes/StickyNote';
import NodeContextMenu from './NodeContextMenu';
import { ConvertToSubworkflowDialog } from './ConvertToSubworkflowDialog';
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
  blueprintNode: BlueprintNode,
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
// The stage is view-only: edits, connections and reconnects are no-ops, so these stay stable between renders.
const NOOP = () => {};
const PRO_OPTIONS = { hideAttribution: true };
const MINIMAP_STYLE = { marginBottom: 12, marginRight: 12, width: 168, height: 108 };

function getMinimapNodeColor(node: Node) {
  if (node.type === 'addNodes') return 'var(--faint)';
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
  // The bottom panel floats over the lower edge of the canvas and is translucent, so the tools under it would show through.
  const bottomPanelOpen = useEditorLayoutStore((s) => s.bottomPanelOpen);
  const rightPanelOpen = useEditorLayoutStore((s) => s.rightPanelOpen);
  const coveredCls = bottomPanelOpen ? 'pointer-events-none opacity-0' : 'transition-opacity';
  const { fitView, screenToFlowPosition, getNodes, getEdges } = useReactFlow();

  // Track the current connection being dragged
  const connectingRef = useRef<{ nodeId: string; handleId: string | null } | null>(null);

  // Track edge reconnection state
  const reconnectingRef = useRef(false);

  // Context menu state
  const [contextMenu, setContextMenu] = useState<{ nodeId: string; x: number; y: number } | null>(null);

  const { saveWorkflow } = useSaveWorkflow();

  // AI stage (PLAN-MOTION-UI §3.3): while it is active the one canvas shows the stage's graph, view-only. The draft in
  // workflowStore is untouched; the stage store owns positions, marks and animation state.
  const stageActive = useStageStore((s) => s.active);
  const stageMode = useStageStore((s) => s.mode);
  const buildHeadNode = useStageStore((s) => s.buildHead?.node ?? '');
  const stagePlayback = useStageStore((s) => s.playback);
  const stageNodes = useStageStore((s) => s.nodes);
  const stageEdges = useStageStore((s) => s.edges);
  const stageMarks = useStageStore((s) => s.marks);
  const stageEdgeMarks = useStageStore((s) => s.edgeMarks);
  const applyStageDimensions = useStageStore((s) => s.applyDimensions);
  const wrapperRef = useRef<HTMLDivElement>(null);
  const [panelInset, setPanelInset] = useState(0);
  useBuildPlayer();

  // Version diff: removed nodes are drawn as faded ghosts next to the real ones.
  const ghostNodes = useHistoryStore((s) => s.ghostNodes);
  const renderNodes = useMemo(() => {
    if (stageActive) return stageNodes;
    return ghostNodes.length ? [...nodes, ...ghostNodes] : nodes;
  }, [stageActive, stageNodes, nodes, ghostNodes]);
  const renderEdges = stageActive ? stageEdges : edges;

  // How much of the canvas the floating right panel covers, measured from the real panel (never a constant), so the
  // camera frames the graph in the part of the canvas the user can see.
  const getInset = useCallback(() => {
    const wrap = wrapperRef.current;
    if (!wrap || !useEditorLayoutStore.getState().rightPanelOpen) return 0;
    const rect = wrap.getBoundingClientRect();
    const panel = document.querySelector('[data-testid="editor-right-close"]')?.closest('.glass-overlay');
    if (!panel) return fallbackPanelInset(window.innerWidth);
    return rightInset(rect.left, rect.right, panel.getBoundingClientRect().left);
  }, []);
  useStageCamera(getInset);
  const centreOfView = useCallback(() => {
    const rect = wrapperRef.current?.getBoundingClientRect();
    if (!rect) return { x: 0, y: 0 };
    return screenToFlowPosition({ x: rect.left + (rect.width - getInset()) / 2, y: rect.top + rect.height / 2 });
  }, [getInset, screenToFlowPosition]);
  useEffect(() => {
    if (!stageActive) return;
    const measure = () => setPanelInset(getInset());
    measure();
    window.addEventListener('resize', measure);
    return () => window.removeEventListener('resize', measure);
  }, [stageActive, stageMode, rightPanelOpen, getInset]);

  // While the AI builds, the camera follows the head at a closer zoom, so outlines and nodes can sit under the translucent right
  // panel; its backdrop blur turned them into soft blobs inside the panel. The graph is clipped at the panel's left edge instead.
  const panelClip = useMemo<React.CSSProperties | undefined>(
    () => (stageActive && stageMode !== 'review' && stageMode !== 'settling' && panelInset > 0 ? { clipPath: `inset(0 ${Math.round(panelInset)}px 0 0)` } : undefined),
    [stageActive, stageMode, panelInset],
  );

  // Marks keep flowing through the history store's contract so `[data-diff]` selectors work for the stage too.
  const stageDiffSet = useRef(false);
  useEffect(() => {
    if (stageActive) {
      stageDiffSet.current = true;
      useHistoryStore.getState().setDiff(stageMarks, [], stageEdgeMarks);
    } else if (stageDiffSet.current) {
      stageDiffSet.current = false;
      useHistoryStore.getState().clearDiff();
    }
  }, [stageActive, stageMarks, stageEdgeMarks]);

  // The camera must not jump when the stage hands over to the reloaded draft: no auto-fit during the stage, nor for
  // the first change after it.
  const suppressFit = useRef(false);
  useEffect(() => {
    if (stageActive) {
      suppressFit.current = true;
      return;
    }
    if (!suppressFit.current) return;
    const raf = requestAnimationFrame(() => { suppressFit.current = false; });
    return () => cancelAnimationFrame(raf);
  }, [stageActive]);

  // Initialize keyboard shortcuts
  const { shortcuts, isShortcutsHelpOpen, setIsShortcutsHelpOpen } = useKeyboardShortcuts({
    onSave: () => {
      saveWorkflow();
    },
  });

  useEffect(
    () => registerEditorAction('focus-node', (id) => { if (id) void fitView({ nodes: [{ id }], padding: 0.6, maxZoom: 1, duration: 200 }); }),
    [fitView]
  );

  // Fit view on initial load when nodes change from placeholder to real nodes
  useEffect(() => {
    const hasRealNodes = nodes.some((n) => n.type === 'workflowNode');
    if (hasRealNodes && !suppressFit.current) {
      // Small delay to ensure nodes are rendered
      const timer = setTimeout(() => {
        if (suppressFit.current) return;
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

  // A user pan or zoom (the event is null for our own fitView) turns the camera's follow off.
  const handleMoveStart = useCallback((event: MouseEvent | TouchEvent | null) => {
    if (event && useStageStore.getState().active) useStageStore.getState().setFollow(false);
  }, []);

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
      if (useStageStore.getState().active) {
        applyStageDimensions(changes);
        return;
      }
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
    [storeOnNodesChange, applyStageDimensions]
  );

  // Handle ReactFlow internal errors
  const handleError = useCallback((id: string, message: string) => {
    console.error(`[ReactFlow Error] ${id}: ${message}`);
  }, []);

  // Open NDV on node double-click (canvas-level handler)
  const handleNodeDoubleClick = useCallback(
    (_event: React.MouseEvent, node: Node) => {
      if (useStageStore.getState().active) return;
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
      if (useStageStore.getState().active) return;
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
      if (useStageStore.getState().active) return;

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
    <div
      ref={wrapperRef}
      className={`relative h-full w-full ${stageActive ? 'stage-on' : ''} ${stageActive && stageMode === 'blueprint' ? 'stage-dim' : ''}`}
      data-stage={stageActive ? stageMode : 'off'}
      data-playback={stageActive ? stagePlayback : 'idle'}
      data-build-head={buildHeadNode}
      data-testid={stageActive ? 'ai-proposal-canvas' : undefined}
      data-nodes={stageActive ? stageNodes.filter((n) => n.type === 'workflowNode' && stageMarks[n.id] !== 'removed').length : undefined}
      onDragOver={handleDragOver}
      onDrop={handleDrop}
    >
      <ReactFlow
        nodes={renderNodes}
        edges={renderEdges}
        onNodesChange={handleNodesChange}
        onEdgesChange={stageActive ? NOOP : onEdgesChange}
        onConnect={stageActive ? NOOP : handleConnect}
        onMoveStart={handleMoveStart}
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
        deleteKeyCode={stageActive ? null : DELETE_KEY_CODE}
        multiSelectionKeyCode="Shift"
        selectionMode={SelectionMode.Partial}
        connectionRadius={20}
        elevateEdgesOnSelect
        edgesReconnectable={!stageActive}
        onReconnect={handleEdgeUpdate}
        onReconnectStart={handleEdgeUpdateStart}
        onReconnectEnd={handleEdgeUpdateEnd}
        connectionLineComponent={ConnectionLine}
        panOnScroll={false}
        zoomOnScroll
        panOnDrag={stageActive || canvasMode === 'hand'}
        selectionOnDrag={!stageActive && canvasMode === 'pointer'}
        selectionKeyCode={!stageActive && canvasMode === 'hand' ? ['Control', 'Meta'] : null}
        nodesDraggable={!stageActive}
        nodesConnectable={!stageActive}
        elementsSelectable={!stageActive}
        proOptions={PRO_OPTIONS}
        className="bg-transparent"
        style={panelClip}
      >
        <Background
          id="grid-1"
          variant={BackgroundVariant.Dots}
          gap={20}
          size={1}
          className="[&>pattern>circle]:fill-(--grid-dot)"
        />

        <BuildHeadGrid />
        <AiPresence centre={centreOfView} />

        <Controls showInteractive={false} className={coveredCls} />

        {/* MiniMap - colored by node group, positioned to avoid sidebar */}
        <MiniMap
          position="bottom-right"
          style={MINIMAP_STYLE}
          className={bottomPanelOpen || rightPanelOpen ? 'pointer-events-none opacity-0' : 'transition-opacity'}
          nodeColor={getMinimapNodeColor}
          maskColor="color-mix(in srgb, var(--canvas) 55%, transparent)"
        />

      </ReactFlow>


      <ProposalPill inset={panelInset} />

      {/* Keyboard shortcuts help modal */}
      <KeyboardShortcutsHelp
        open={isShortcutsHelpOpen}
        onOpenChange={setIsShortcutsHelpOpen}
        shortcuts={shortcuts}
      />

      {/* Stage 8: convert a selection to a sub-workflow */}
      <ConvertToSubworkflowDialog />

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
