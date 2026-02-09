import { memo, useId, useMemo } from 'react';
import { getSmoothStepPath, Position, type Node, type Edge } from 'reactflow';
import { Check, X } from 'lucide-react';
import type { WorkflowNodeData, NodeExecutionData } from '../types/workflow';
import { getNodeStyles, getNodeShapeConfig, calculateNodeDimensions } from '../lib/nodeStyles';
import { normalizeNodeGroup } from '../lib/nodeConfig';
import { getIconForNode } from '../lib/nodeIcons';

interface WorkflowSVGProps {
  nodes: Node<WorkflowNodeData>[];
  edges: Edge[];
  executionData?: Record<string, NodeExecutionData>;
  showIcons?: boolean;
  showDotGrid?: boolean;
  className?: string;
  width?: number;
  height?: number;
}

const PADDING = 40;

/** Parse the first numeric value from a CSS border-radius string like "20px 10px 10px 20px" */
function parseBorderRadius(br: string): number {
  const match = br.match(/(\d+)/);
  return match ? parseInt(match[1], 10) : 12;
}

/** Get node dimensions (width/height) using the existing calculateNodeDimensions utility */
function getNodeDims(data: WorkflowNodeData) {
  const inputCount = data.inputCount ?? 1;
  const outputCount = data.outputCount ?? 1;
  const subnodeSlotCount = data.subnodeSlots?.length ?? 0;
  return calculateNodeDimensions(inputCount, outputCount, subnodeSlotCount);
}

/** Filter to only visible nodes (exclude stacked subnodes) */
function visibleNodes(nodes: Node<WorkflowNodeData>[]): Node<WorkflowNodeData>[] {
  return nodes.filter((n) => !n.data.stacked);
}

/** Compute the SVG viewBox from visible node positions and dimensions */
function computeViewBox(nodes: Node<WorkflowNodeData>[]): string {
  const visible = visibleNodes(nodes);
  if (visible.length === 0) return '0 0 200 120';

  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;

  for (const node of visible) {
    const dims = getNodeDims(node.data);
    const x = node.position.x;
    const y = node.position.y;
    minX = Math.min(minX, x);
    minY = Math.min(minY, y);
    maxX = Math.max(maxX, x + dims.width);
    maxY = Math.max(maxY, y + dims.height);
  }

  return `${minX - PADDING} ${minY - PADDING} ${maxX - minX + PADDING * 2} ${maxY - minY + PADDING * 2}`;
}

/** Subnode colors by type */
const SUBNODE_COLORS: Record<string, { fill: string; stroke: string }> = {
  model: { fill: 'var(--subnode-model-light)', stroke: 'var(--subnode-model-border)' },
  memory: { fill: 'var(--subnode-memory-light)', stroke: 'var(--subnode-memory-border)' },
  tool: { fill: 'var(--subnode-tool-light)', stroke: 'var(--subnode-tool-border)' },
};

function WorkflowSVG({ nodes, edges, executionData, showIcons, showDotGrid, className, width, height }: WorkflowSVGProps) {
  const patternId = useId();
  const viewBox = useMemo(() => computeViewBox(nodes), [nodes]);

  // Build a lookup from node id to node for edge rendering
  const nodeMap = useMemo(() => {
    const map = new Map<string, Node<WorkflowNodeData>>();
    for (const n of nodes) map.set(n.id, n);
    return map;
  }, [nodes]);

  return (
    <svg
      width={width}
      height={height}
      viewBox={viewBox}
      preserveAspectRatio="xMidYMid meet"
      className={`pointer-events-none ${className ?? ''}`}
    >
      <defs>
        <style>{`
          @keyframes dash-flow {
            to { stroke-dashoffset: -12; }
          }
        `}</style>
        {showDotGrid && (
          <pattern id={patternId} x="0" y="0" width="20" height="20" patternUnits="userSpaceOnUse">
            <circle cx="10" cy="10" r="0.8" fill="currentColor" opacity="0.15" />
          </pattern>
        )}
      </defs>

      {showDotGrid && <rect width="100%" height="100%" fill={`url(#${patternId})`} />}

      {/* Edges */}
      {edges.map((edge) => {
        const sourceNode = nodeMap.get(edge.source);
        const targetNode = nodeMap.get(edge.target);
        if (!sourceNode || !targetNode) return null;
        // Skip edges connecting to stacked (hidden) subnodes
        if (sourceNode.data.stacked || targetNode.data.stacked) return null;

        const isSubnodeEdge = edge.type === 'subnodeEdge';
        const sourceDims = getNodeDims(sourceNode.data);
        const targetDims = getNodeDims(targetNode.data);

        // Determine source/target connection points
        let sourceX: number, sourceY: number, targetX: number, targetY: number;
        let sourcePosition: Position, targetPosition: Position;

        if (isSubnodeEdge) {
          // Subnode edges: source top-center -> target bottom-center
          sourceX = sourceNode.position.x + sourceDims.width / 2;
          sourceY = sourceNode.position.y;
          targetX = targetNode.position.x + targetDims.width / 2;
          targetY = targetNode.position.y + targetDims.height;
          sourcePosition = Position.Top;
          targetPosition = Position.Bottom;
        } else {
          // Normal edges: source right-center -> target left-center
          sourceX = sourceNode.position.x + sourceDims.width;
          sourceY = sourceNode.position.y + sourceDims.height / 2;
          targetX = targetNode.position.x;
          targetY = targetNode.position.y + targetDims.height / 2;
          sourcePosition = Position.Right;
          targetPosition = Position.Left;
        }

        const [edgePath] = getSmoothStepPath({
          sourceX,
          sourceY,
          sourcePosition,
          targetX,
          targetY,
          targetPosition,
          borderRadius: 8,
        });

        return (
          <path
            key={edge.id}
            d={edgePath}
            fill="none"
            stroke="var(--muted-foreground)"
            strokeWidth={1.5}
            strokeOpacity={0.5}
            strokeDasharray={isSubnodeEdge ? '4 3' : undefined}
          />
        );
      })}

      {/* Nodes */}
      {nodes.map((node) => {
        const data = node.data;
        // Skip stacked subnodes (they're hidden in the real canvas too)
        if (data.stacked) return null;

        const isSubnode = data.isSubnode && node.type === 'subnodeNode';
        const group = normalizeNodeGroup(data.group ? [data.group] : undefined);
        const dims = getNodeDims(data);
        const x = node.position.x;
        const y = node.position.y;
        const styles = getNodeStyles(group);
        const nodeExec = executionData?.[data.name];

        if (isSubnode) {
          // Render as circle
          const cx = x + dims.width / 2;
          const cy = y + dims.height / 2;
          const r = 22;
          const subnodeType = data.subnodeType || 'tool';
          const colors = SUBNODE_COLORS[subnodeType] || SUBNODE_COLORS.tool;

          return (
            <g key={node.id}>
              <circle
                cx={cx}
                cy={cy}
                r={r}
                fill={colors.fill}
                stroke={colors.stroke}
                strokeWidth={1.5}
              />
              {showIcons && (
                <SubnodeIcon data={data} cx={cx} cy={cy} color={colors.stroke} />
              )}
              {nodeExec && <ExecutionBadge status={nodeExec.status} cx={cx + r - 4} cy={cy - r + 4} />}
            </g>
          );
        }

        // Render as rect (workflowNode or subworkflowNode)
        const shape = getNodeShapeConfig(group);
        const rx = parseBorderRadius(shape.borderRadius);

        return (
          <g key={node.id}>
            <rect
              x={x}
              y={y}
              width={dims.width}
              height={dims.height}
              rx={rx}
              ry={rx}
              fill={styles.bgColor}
              stroke={styles.borderColor}
              strokeWidth={1.5}
            />
            {/* Running animation */}
            {nodeExec?.status === 'running' && (
              <rect
                x={x}
                y={y}
                width={dims.width}
                height={dims.height}
                rx={rx}
                ry={rx}
                fill="none"
                stroke={styles.accentColor}
                strokeWidth={2}
                strokeDasharray="6 4"
                style={{ animation: 'dash-flow 0.6s linear infinite' }}
              />
            )}
            {showIcons && (
              <NodeIcon data={data} x={x} y={y} dims={dims} color={styles.accentColor} />
            )}
            {nodeExec && <ExecutionBadge status={nodeExec.status} cx={x + dims.width - 4} cy={y + 4} />}
          </g>
        );
      })}
    </svg>
  );
}

/** Icon inside a rect node via foreignObject */
function NodeIcon({ data, x, y, dims, color }: { data: WorkflowNodeData; x: number; y: number; dims: { width: number; height: number }; color: string }) {
  const IconComponent = getIconForNode(data.icon, data.type);
  return (
    <foreignObject
      x={x + dims.width / 2 - 12}
      y={y + dims.height / 2 - 12}
      width={24}
      height={24}
      className="pointer-events-none overflow-visible"
    >
      <div className="flex items-center justify-center w-full h-full" style={{ color }}>
        <IconComponent size={16} />
      </div>
    </foreignObject>
  );
}

/** Icon inside a circle subnode via foreignObject */
function SubnodeIcon({ data, cx, cy, color }: { data: WorkflowNodeData; cx: number; cy: number; color: string }) {
  const IconComponent = getIconForNode(data.icon, data.type);
  return (
    <foreignObject
      x={cx - 10}
      y={cy - 10}
      width={20}
      height={20}
      className="pointer-events-none overflow-visible"
    >
      <div className="flex items-center justify-center w-full h-full" style={{ color }}>
        <IconComponent size={12} />
      </div>
    </foreignObject>
  );
}

/** Small status badge rendered at a given position */
function ExecutionBadge({ status, cx, cy }: { status: string; cx: number; cy: number }) {
  if (status === 'success') {
    return (
      <g>
        <circle cx={cx} cy={cy} r={7} fill="var(--success)" />
        <foreignObject x={cx - 5} y={cy - 5} width={10} height={10} className="pointer-events-none">
          <div className="flex items-center justify-center w-full h-full text-white">
            <Check size={8} strokeWidth={3} />
          </div>
        </foreignObject>
      </g>
    );
  }
  if (status === 'error') {
    return (
      <g>
        <circle cx={cx} cy={cy} r={7} fill="var(--destructive)" />
        <foreignObject x={cx - 5} y={cy - 5} width={10} height={10} className="pointer-events-none">
          <div className="flex items-center justify-center w-full h-full text-white">
            <X size={8} strokeWidth={3} />
          </div>
        </foreignObject>
      </g>
    );
  }
  return null;
}

export default memo(WorkflowSVG);
