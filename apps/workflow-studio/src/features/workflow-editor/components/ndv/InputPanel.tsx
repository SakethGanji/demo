import { fieldRow } from './fieldRow';
import { memo, useState, useMemo, useCallback } from 'react';
import { DataEmpty } from './DataEmpty';
import { Database, Code, ChevronDown, ChevronUp, Copy, Check, Settings } from 'lucide-react';
import type { Node, Edge } from '@xyflow/react';
import type { NodeExecutionData } from '../../types/workflow';
import { useWorkflowStore } from '../../stores/workflowStore';
import { useNDVStore } from '../../stores/ndvStore';
import { useNodeTypes } from '../../hooks/useNodeTypes';
import { Segmented } from '@/shared/components/ui/segmented';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/shared/components/ui/select';
import { Spinner } from '@/shared/components/ui/spinner';
import RunDataDisplay from './RunDataDisplay';
import ContractFieldList from './ContractFieldList';
import { useShapesByNode } from '../../hooks/useWorkflowSelectors';
import { inputShapeFor, outputShapeFor } from '../../lib/contractSuggestions';
import SchemaDisplay, { type OutputSchema } from './SchemaDisplay';

// --- Inlined from graphUtils.ts ---
interface UpstreamNode {
  id: string;
  name: string;
  label: string;
  isImmediate: boolean;
}

function getAllUpstreamNodes(nodeId: string, nodes: Node[], edges: Edge[]): UpstreamNode[] {
  const result: UpstreamNode[] = [];
  const visited = new Set<string>();
  const immediateSourceId = edges.find(
    (e) => e.target === nodeId   )?.source;

  function traverse(currentId: string) {
    if (visited.has(currentId)) return;
    visited.add(currentId);
    const incomingEdges = edges.filter(
      (e) => e.target === currentId     );
    for (const edge of incomingEdges) {
      const sourceNode = nodes.find((n) => n.id === edge.source);
      if (sourceNode && sourceNode.type === 'workflowNode') {
        traverse(sourceNode.id);
        if (!result.find((n) => n.id === sourceNode.id)) {
          const d = sourceNode.data as { name?: string; label?: string };
          result.push({
            id: sourceNode.id,
            name: d.name || d.label || '',
            label: d.label || '',
            isImmediate: sourceNode.id === immediateSourceId,
          });
        }
      }
    }
  }

  traverse(nodeId);
  return result;
}

function getExpressionBasePath(nodeName: string, isImmediate: boolean): string {
  return isImmediate ? '$json' : `$node["${nodeName}"].json`;
}

interface InputPanelProps {
  nodeId: string;
  executionData: NodeExecutionData | null;
}

type DisplayMode = 'json' | 'schema';

// System variables that are always available
const SYSTEM_VARIABLES = [
  { path: '$itemIndex', description: 'Current item index in the loop' },
  { path: '$execution.id', description: 'Current execution ID' },
  { path: '$now', description: 'Current timestamp (ms)' },
  { path: '$today', description: "Today's date (ISO)" },
  { path: '$env.VARIABLE', description: 'Environment variable' },
];

const InputPanel = memo(function InputPanel({ nodeId, executionData }: InputPanelProps) {
  const storedInputMode = useNDVStore((s) => s.inputDisplayMode);
  const [displayMode, setDisplayMode] = useState<DisplayMode>(
    storedInputMode as DisplayMode
  );
  const [showSystemVars, setShowSystemVars] = useState(false);

  // Get edges and execution data from store to find upstream node's output
  const edges = useWorkflowStore((s) => s.edges);
  const allExecutionData = useWorkflowStore((s) => s.executionData);
  const nodes = useWorkflowStore((s) => s.nodes);

  // Get node type definitions from API
  const { data: nodeTypes } = useNodeTypes();

  // Get all upstream nodes
  const upstreamNodes = useMemo(
    () => getAllUpstreamNodes(nodeId, nodes, edges),
    [nodeId, nodes, edges]
  );

  // Find the immediate upstream node (default selection)
  const immediateUpstreamNode = useMemo(
    () => upstreamNodes.find((n) => n.isImmediate),
    [upstreamNodes]
  );

  // Selected node state (default to immediate upstream)
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);

  // Determine which node is actually selected (use immediate if none selected)
  const effectiveSelectedNode = useMemo(() => {
    if (selectedNodeId) {
      return upstreamNodes.find((n) => n.id === selectedNodeId);
    }
    return immediateUpstreamNode;
  }, [selectedNodeId, upstreamNodes, immediateUpstreamNode]);

  // Get the React Flow node for the selected upstream node
  const selectedReactFlowNode = useMemo(() => {
    if (!effectiveSelectedNode) return null;
    return nodes.find((n) => n.id === effectiveSelectedNode.id);
  }, [effectiveSelectedNode, nodes]);

  // Get output schema from node type definition (for fallback when no execution data)
  // Node types are already in backend format
  const selectedNodeOutputSchema = useMemo(() => {
    if (!selectedReactFlowNode || !nodeTypes) return null;
    const nodeType = selectedReactFlowNode.data?.type;
    if (!nodeType) return null;
    const typeDef = nodeTypes.find((t) => t.type === nodeType);
    const schema = typeDef?.outputs?.[0]?.schema;
    if (!schema || typeof schema !== 'object' || !('type' in schema) || typeof schema.type !== 'string') return null;
    return schema as OutputSchema;
  }, [selectedReactFlowNode, nodeTypes]);

  // Get data for the selected node
  const selectedNodeData = useMemo(() => {
    if (!effectiveSelectedNode) return null;

    // If this is the immediate node, first try the current node's input data
    if (effectiveSelectedNode.isImmediate) {
      if (executionData?.input?.items && executionData.input.items.length > 0) {
        return executionData.input;
      }
    }

    // Get the selected node's output data
    const nodeExecution = allExecutionData[effectiveSelectedNode.id];
    if (nodeExecution?.output?.items && nodeExecution.output.items.length > 0) {
      return nodeExecution.output;
    }

    return null;
  }, [effectiveSelectedNode, executionData, allExecutionData]);

  // Compute the base path for expressions
  const basePath = useMemo(() => {
    if (!effectiveSelectedNode) return '$json';
    return getExpressionBasePath(effectiveSelectedNode.name, effectiveSelectedNode.isImmediate);
  }, [effectiveSelectedNode]);

  // Contract fields (from the live check) for when there is no run data
  const shapesByNode = useShapesByNode();
  const contractShape = useMemo(() => {
    if (!effectiveSelectedNode) return null;
    const current = nodes.find((n) => n.id === nodeId)?.data?.name;
    const shape = effectiveSelectedNode.isImmediate && typeof current === 'string'
      ? inputShapeFor(shapesByNode, current) ?? outputShapeFor(shapesByNode, effectiveSelectedNode.name)
      : outputShapeFor(shapesByNode, effectiveSelectedNode.name);
    return shape && shape.type === 'object' && shape.properties && Object.keys(shape.properties).length > 0 ? shape : null;
  }, [effectiveSelectedNode, nodes, nodeId, shapesByNode]);

  const hasData = selectedNodeData?.items && selectedNodeData.items.length > 0;
  const hasSchema = selectedNodeOutputSchema && selectedNodeOutputSchema.properties;
  const itemCount = selectedNodeData?.items?.length ?? 0;

  return (
    <div className="flex h-full flex-col">
      {/* Header with node selector and view toggle */}
      <div className="flex items-center justify-between px-3 py-2 gap-2 shadow-[inset_0_-1px_0_var(--hairline)]">
        {/* Node selector dropdown */}
        <div className="flex-1 min-w-0">
          {upstreamNodes.length > 0 ? (
            <Select
              value={effectiveSelectedNode?.id || ''}
              onValueChange={(v) => setSelectedNodeId((v as string) || null)}
              items={Object.fromEntries(upstreamNodes.map((n) => [n.id, `${n.label}${n.isImmediate ? ' (input)' : ''}`]))}
            >
              <SelectTrigger size="sm" className="w-full text-[12px] font-medium" data-testid="ndv-input-node-select">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {upstreamNodes.map((node) => (
                  <SelectItem key={node.id} value={node.id}>
                    {node.label}
                    {node.isImmediate ? ' (input)' : ''}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          ) : (
            <span className="text-[13px] font-medium text-foreground truncate block">
              No upstream nodes
            </span>
          )}
        </div>

        {/* Item count badge */}
        {hasData && (
          <span className="bg-recess text-muted-foreground rounded-chip px-1.5 py-px text-[10.5px] font-medium flex-shrink-0">
            {itemCount} items
          </span>
        )}

        {/* Display mode toggle */}
        <Segmented
          size="xs"
          value={displayMode}
          onChange={(m) => { setDisplayMode(m); useNDVStore.getState().setInputDisplayMode(m); }}
          aria-label="Display mode"
          data-testid="ndv-input-display-mode"
          options={[
            { key: 'schema', label: <Database size={13} aria-label="Schema view" />, testId: 'ndv-input-mode-schema' },
            { key: 'json', label: <Code size={13} aria-label="JSON view" />, testId: 'ndv-input-mode-json' },
          ]}
        />
      </div>

      {/* Expression path indicator */}
      {effectiveSelectedNode && (
        <div className="px-3 py-1 bg-brand-soft flex items-center justify-between">
          <code className="text-[11px] text-primary font-mono font-medium">{basePath}</code>
          {!effectiveSelectedNode.isImmediate && (
            <span className="text-[11px] text-muted-foreground">from {effectiveSelectedNode.label}</span>
          )}
        </div>
      )}

      {/* Data display */}
      <div className="well m-2 flex-1 overflow-auto p-3">
        {executionData?.status === 'running' ? (
          <div className="flex h-full items-center justify-center">
            <Spinner className="size-6" />
          </div>
        ) : hasData ? (
          <RunDataDisplay
            data={selectedNodeData!.items}
            mode={displayMode}
            basePath={basePath}
          />
        ) : contractShape ? (
          <div className="space-y-1.5">
            <div className="flex items-center gap-2">
              <span className="inline-flex items-center rounded-chip bg-brand-soft px-1.5 py-0.5 text-[10.5px] font-medium text-primary">
                Schema
              </span>
              <span className="text-xs text-muted-foreground">from contract (drag to use)</span>
            </div>
            <ContractFieldList shape={contractShape} basePath={basePath} />
          </div>
        ) : hasSchema ? (
          // Show output schema when no execution data yet
          <SchemaDisplay
            schema={selectedNodeOutputSchema}
            basePath={basePath}
          />
        ) : (
          <DataEmpty
            title={
              effectiveSelectedNode
                ? `No data from ${effectiveSelectedNode.label}`
                : upstreamNodes.length === 0
                  ? 'No connected input'
                  : 'Select a node to view data'
            }
            hint={
              effectiveSelectedNode
                ? 'Run workflow to see output data'
                : upstreamNodes.length === 0
                  ? 'Connect a node to see its output here'
                  : undefined
            }
          />
        )}
      </div>

      {/* System Variables Section */}
      <div className="shadow-[inset_0_1px_0_var(--hairline)]">
        <button
          onClick={() => setShowSystemVars(!showSystemVars)}
          className="flex w-full items-center justify-between px-3 py-1.5 text-left row-hover transition-colors"
        >
          <div className="flex items-center gap-1.5">
            <Settings size={12} className="text-muted-foreground" />
            <span className="text-[11px] font-medium text-muted-foreground">System Variables</span>
          </div>
          {showSystemVars ? (
            <ChevronUp size={12} className="text-muted-foreground" />
          ) : (
            <ChevronDown size={12} className="text-muted-foreground" />
          )}
        </button>

        {showSystemVars && (
          <div className="px-3 pb-3">
            <div className="well overflow-hidden">
              {SYSTEM_VARIABLES.map((variable) => (
                <SystemVariableRow key={variable.path} {...variable} />
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
});

export default InputPanel;

// System variable row component
interface SystemVariableRowProps {
  path: string;
  description: string;
}

function SystemVariableRow({ path, description }: SystemVariableRowProps) {
  const [copied, setCopied] = useState(false);

  const handleCopy = useCallback(() => {
    const expression = `{{ ${path} }}`;
    navigator.clipboard.writeText(expression);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }, [path]);

  const handleDragStart = useCallback(
    (e: React.DragEvent) => {
      e.dataTransfer.setData('text/plain', path);
      e.dataTransfer.setData('application/x-field-path', path);
      e.dataTransfer.effectAllowed = 'copy';
    },
    [path]
  );

  return (
    <div
      draggable
      onDragStart={handleDragStart}
      className={`${fieldRow} px-2.5 py-2`}
    >
      <div className="flex flex-col gap-0.5 min-w-0">
        <code className="text-[11px] font-mono font-medium text-foreground">{path}</code>
        <span className="text-[10px] text-muted-foreground">{description}</span>
      </div>
      <button
        onClick={handleCopy}
        className="p-1.5 rounded-ctl hover:bg-(--hover-fill) opacity-0 group-hover:opacity-100 transition-all flex-shrink-0"
        title={`Copy {{ ${path} }}`}
      >
        {copied ? (
          <Check size={12} className="text-success" />
        ) : (
          <Copy size={12} className="text-muted-foreground" />
        )}
      </button>
    </div>
  );
}
