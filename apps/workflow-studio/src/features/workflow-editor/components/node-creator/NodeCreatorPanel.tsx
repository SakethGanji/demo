import { useCallback, useMemo, memo, type DragEvent } from 'react';
import { Search, X, Loader2 } from 'lucide-react';
import { useEditorLayoutStore } from '../../stores/editorLayoutStore';
import { useWorkflowStore } from '../../stores/workflowStore';
import { generateNodeName, getExistingNodeNames } from '../../lib/workflowTransform';
import { useNodeTypes } from '../../hooks/useNodeTypes';
import { getNodeIcon, normalizeNodeGroup } from '../../lib/nodeConfig';
import { getNodeStyles } from '../../lib/nodeStyles';
import { getIconForNode } from '../../lib/nodeIcons';
import { createWorkflowNodeData } from '../../lib/createNodeData';
import type { NodeDefinition, OutputStrategy } from '../../types/workflow';
import type { NodeGroup, NodeIO } from '../../lib/nodeStyles';
import type { ApiProperty } from '@/shared/lib/api';

// Extended node definition with API metadata for dynamic UI
interface ExtendedNodeDefinition extends NodeDefinition {
  group?: NodeGroup;
  inputCount?: number;
  outputCount?: number;
  inputs?: NodeIO[];
  outputs?: NodeIO[];
  // Output strategy for dynamic output nodes
  outputStrategy?: OutputStrategy;
  // Properties with defaults
  properties?: ApiProperty[];
}

export default function NodeCreatorPanel() {
  const view = useEditorLayoutStore((s) => s.nodeCreatorView);
  const search = useEditorLayoutStore((s) => s.nodeCreatorSearch);
  const sourceNodeId = useEditorLayoutStore((s) => s.sourceNodeId);
  const sourceHandleId = useEditorLayoutStore((s) => s.sourceHandleId);
  const dropPosition = useEditorLayoutStore((s) => s.dropPosition);
  const closePanel = useEditorLayoutStore((s) => s.closeCreatorPanel);
  const setView = useEditorLayoutStore((s) => s.setCreatorView);
  const setSearch = useEditorLayoutStore((s) => s.setCreatorSearch);
  const clearConnectionContext = useEditorLayoutStore((s) => s.clearConnectionContext);

  const addNode = useWorkflowStore((s) => s.addNode);
  const nodes = useWorkflowStore((s) => s.nodes);
  const onConnect = useWorkflowStore((s) => s.onConnect);

  // Fetch node types from API
  const { data: apiNodes, isLoading, isError } = useNodeTypes();

  // Transform API nodes to ExtendedNodeDefinition format with dynamic UI metadata
  const { triggerNodes, regularNodes } = useMemo(() => {
    if (!apiNodes) return { triggerNodes: [], regularNodes: [] };

    const triggers: ExtendedNodeDefinition[] = [];
    const regular: ExtendedNodeDefinition[] = [];

    apiNodes.forEach((node) => {
      const isTrigger = node.group?.includes('trigger');
      const category = node.group?.[0] || 'other';

      // Map API category to UI category
      const categoryMap: Record<string, NodeDefinition['category']> = {
        trigger: 'trigger',
        transform: 'transform',
        flow: 'flow',
        ai: 'ai',
        helper: 'helper',
        other: 'action',
      };

      // Parse inputs/outputs from API
      const inputs: NodeIO[] = (node.inputs || []).map((input: { name: string; displayName?: string }) => ({
        name: input.name,
        displayName: input.displayName || input.name,
      }));

      const outputs: NodeIO[] = (node.outputs || []).map((output: { name: string; displayName?: string }) => ({
        name: output.name,
        displayName: output.displayName || output.name,
      }));

      // Calculate input/output counts (handle dynamic output nodes)
      const inputCount = typeof node.inputCount === 'number' ? node.inputCount : inputs.length;
      const outputCount = typeof node.outputCount === 'number'
        ? node.outputCount
        : (node.outputCount === 'dynamic' ? outputs.length || 1 : outputs.length);

      const nodeDef: ExtendedNodeDefinition = {
        type: node.type, // Backend type (PascalCase) - used everywhere
        name: node.type, // Same as type
        displayName: node.displayName,
        description: node.description,
        icon: getNodeIcon(node.type, node.icon),
        category: categoryMap[category] || 'action',
        subcategory: getCategoryLabel(category),
        // Dynamic UI metadata
        group: category as NodeGroup,
        inputCount,
        outputCount,
        inputs,
        outputs,
        // Output strategy for dynamic output nodes (like Switch)
        outputStrategy: node.outputStrategy as OutputStrategy | undefined,
        // Properties with defaults
        properties: node.properties as ApiProperty[] | undefined,
      };

      if (isTrigger) {
        triggers.push(nodeDef);
      } else {
        regular.push(nodeDef);
      }
    });

    return { triggerNodes: triggers, regularNodes: regular };
  }, [apiNodes]);

  // Get the right nodes based on view
  const availableNodes = useMemo(() => {
    if (view === 'trigger') {
      return triggerNodes;
    }
    // Default: show all nodes (triggers + regular)
    return [...triggerNodes, ...regularNodes];
  }, [view, triggerNodes, regularNodes]);

  // Filter nodes by search
  const filteredNodes = useMemo(() => {
    if (!search) return availableNodes;
    const lowerSearch = search.toLowerCase();
    return availableNodes.filter(
      (node) =>
        node.displayName.toLowerCase().includes(lowerSearch) ||
        node.description.toLowerCase().includes(lowerSearch)
    );
  }, [availableNodes, search]);

  // Group nodes by subcategory
  const groupedNodes = useMemo(() => {
    const grouped: Record<string, NodeDefinition[]> = {};

    filteredNodes.forEach((node) => {
      const key = node.subcategory || node.category;
      if (!grouped[key]) {
        grouped[key] = [];
      }
      grouped[key].push(node);
    });

    return grouped;
  }, [filteredNodes]);

  // Calculate position for new node
  const getNewNodePosition = useCallback(() => {
    if (dropPosition) {
      return {
        x: Math.round(dropPosition.x / 20) * 20,
        y: Math.round(dropPosition.y / 20) * 20,
      };
    }

    if (sourceNodeId) {
      const sourceNode = nodes.find((n) => n.id === sourceNodeId);
      if (sourceNode) {
        return {
          x: sourceNode.position.x + 250,
          y: sourceNode.position.y,
        };
      }
    }

    if (nodes.length === 0 || (nodes.length === 1 && nodes[0].type === 'addNodes')) {
      return { x: 250, y: 200 };
    }

    const maxX = Math.max(...nodes.map((n) => n.position.x));
    const avgY =
      nodes.reduce((sum, n) => sum + n.position.y, 0) / nodes.length;

    return { x: maxX + 250, y: avgY };
  }, [nodes, sourceNodeId, dropPosition]);

  // Handle node selection
  const handleNodeSelect = useCallback(
    (nodeDef: ExtendedNodeDefinition) => {
      const position = getNewNodePosition();
      const newNodeId = `node-${Date.now()}`;

      const existingNames = getExistingNodeNames(nodes as any);
      const nodeName = generateNodeName(nodeDef.name, existingNames);

      const nodeData = createWorkflowNodeData(
        {
          type: nodeDef.type,
          displayName: nodeDef.displayName,
          icon: nodeDef.icon,
          description: nodeDef.description,
          group: nodeDef.group,
          inputCount: nodeDef.inputCount,
          outputCount: nodeDef.outputCount,
          inputs: nodeDef.inputs,
          outputs: nodeDef.outputs,
          outputStrategy: nodeDef.outputStrategy,
          properties: nodeDef.properties,
        },
        { name: nodeName },
      );

      const newNode = {
        id: newNodeId,
        type: 'workflowNode',
        position,
        data: nodeData,
      };

      addNode(newNode);

      if (sourceNodeId) {
        onConnect({
          source: sourceNodeId,
          target: newNodeId,
          sourceHandle: sourceHandleId,
          targetHandle: null,
        });
      }

      if (view === 'trigger') {
        setView('regular');
      }

      clearConnectionContext();
    },
    [
      addNode,
      clearConnectionContext,
      getNewNodePosition,
      nodes,
      onConnect,
      setView,
      sourceNodeId,
      sourceHandleId,
      view,
    ]
  );

  // Context banner info
  const hasContext = !!sourceNodeId;
  const contextLabel = sourceNodeId
    ? 'Adding connected node'
    : '';

  return (
    <div className="h-full flex flex-col">
      {/* Context banner */}
      {hasContext && (
        <div className="flex items-center gap-2 px-3 py-1.5 bg-primary/10 border-b border-primary/20 shrink-0">
          <span className="text-[11px] font-medium text-primary flex-1 truncate">
            {contextLabel}
          </span>
          <button
            onClick={closePanel}
            className="p-0.5 text-primary/70 hover:text-primary rounded"
            title="Dismiss"
          >
            <X size={12} />
          </button>
        </div>
      )}

      {/* Header */}
      <div className="border-b border-border px-3 py-2 shrink-0">
        <div className="relative">
          <Search
            size={13}
            className="absolute left-2 top-1/2 -translate-y-1/2 text-muted-foreground"
          />
          <input
            type="text"
            placeholder="Search nodes..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-full rounded-md border border-input bg-background h-7 pl-7 pr-2 text-xs outline-none focus:border-ring focus:ring-1 focus:ring-ring/20 transition-colors"
          />
        </div>
      </div>

      {/* Node list */}
      <div className="flex-1 overflow-y-auto p-2">
        {isLoading ? (
          <div className="flex flex-col items-center justify-center py-10">
            <Loader2 size={20} className="animate-spin text-muted-foreground mb-2" />
            <p className="text-xs text-muted-foreground">Loading nodes...</p>
          </div>
        ) : isError ? (
          <div className="flex flex-col items-center justify-center py-10">
            <p className="text-xs text-destructive mb-1">Failed to load nodes</p>
            <p className="text-[11px] text-muted-foreground">Check your connection</p>
          </div>
        ) : search ? (
          <div className="space-y-0.5">
            {filteredNodes.map((node) => (
              <NodeItem
                key={node.type}
                node={node}
                onClick={() => handleNodeSelect(node)}
              />
            ))}
            {filteredNodes.length === 0 && (
              <p className="py-6 text-center text-xs text-muted-foreground">
                No nodes matching "{search}"
              </p>
            )}
          </div>
        ) : (
          <div className="space-y-3">
            {Object.entries(groupedNodes).map(([category, categoryNodes]) => (
              <div key={category} className="space-y-0.5">
                <h3 className="flex items-center text-[10px] font-medium text-muted-foreground mb-1 px-1 uppercase tracking-wider">
                  {category}
                </h3>
                {categoryNodes.map((node) => (
                  <NodeItem
                    key={node.type}
                    node={node}
                    onClick={() => handleNodeSelect(node)}
                  />
                ))}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

/**
 * Get a human-readable label for a category
 */
function getCategoryLabel(category: string): string {
  const labels: Record<string, string> = {
    trigger: 'Triggers',
    transform: 'Transform Data',
    flow: 'Flow',
    ai: 'AI',
    helper: 'Helpers',
    other: 'Other',
  };
  return labels[category] || category.charAt(0).toUpperCase() + category.slice(1);
}

// ---------------------------------------------------------------------------
// NodeItem (inlined)
// ---------------------------------------------------------------------------

const NodeItem = memo(function NodeItem({
  node,
  onClick,
}: {
  node: NodeDefinition & { group?: NodeGroup };
  onClick: () => void;
}) {
  const IconComponent = getIconForNode(node.icon, node.type);
  const setDraggedNodeType = useWorkflowStore((s) => s.setDraggedNodeType);

  const nodeGroup = normalizeNodeGroup(node.group ? [node.group] : (node.category ? [node.category] : undefined));
  const styles = getNodeStyles(nodeGroup);

  const handleDragStart = (e: DragEvent<HTMLButtonElement>) => {
    e.dataTransfer.setData('application/reactflow-node', JSON.stringify(node));
    e.dataTransfer.effectAllowed = 'move';
    setDraggedNodeType(node.type);
  };

  const handleDragEnd = () => {
    setDraggedNodeType(null);
  };

  return (
    <button
      onClick={onClick}
      draggable
      onDragStart={handleDragStart}
      onDragEnd={handleDragEnd}
      className="group flex w-full items-center gap-2.5 rounded-md px-2.5 py-2 text-left transition-colors hover:bg-accent cursor-grab active:cursor-grabbing"
    >
      <div
        className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full"
        style={{
          backgroundColor: styles.iconBgColor,
          color: styles.iconFgColor,
        }}
      >
        <IconComponent size={16} />
      </div>
      <div className="min-w-0 flex-1">
        <p className="text-[13px] font-medium text-foreground">{node.displayName}</p>
        <p className="truncate text-[12px] text-muted-foreground leading-tight">{node.description}</p>
      </div>
    </button>
  );
});
