import { memo, useId, useMemo } from 'react';
import { getSmoothStepPath, Position, type Node, type Edge } from '@xyflow/react';
import { Check, X } from 'lucide-react';
import type { WorkflowNodeData, NodeExecutionData } from '../types/workflow';
import { getNodeStyles, calculateNodeDimensions } from '../lib/nodeStyles';
import { normalizeNodeGroup } from '../lib/nodeConfig';
import { getIconForNode } from '../lib/nodeIcons';

interface WorkflowSVGProps {
  nodes: Node<WorkflowNodeData>[];
  edges: Edge[];
  executionData?: Record<string, NodeExecutionData>;
  showIcons?: boolean;
  showDotGrid?: boolean;
  className?: string;
  style?: React.CSSProperties;
  width?: number;
  height?: number;
}

const PADDING = 40;

/** Get node dimensions (width/height) using the existing calculateNodeDimensions utility */
function getNodeDims(data: WorkflowNodeData) {
  const inputCount = data.inputCount ?? 1;
  const outputCount = data.outputCount ?? 1;
  return calculateNodeDimensions(inputCount, outputCount);
}

/** Compute the SVG viewBox from node positions and dimensions */
function computeViewBox(nodes: Node<WorkflowNodeData>[]): string {
  if (nodes.length === 0) return '0 0 200 120';

  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;

  for (const node of nodes) {
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

function WorkflowSVG({ nodes, edges, executionData, showIcons, showDotGrid, className, style, width, height }: WorkflowSVGProps) {
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
      style={style}
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

        const sourceDims = getNodeDims(sourceNode.data);
        const targetDims = getNodeDims(targetNode.data);

        // Normal edges: source right-center -> target left-center
        const sourceX = sourceNode.position.x + sourceDims.width;
        const sourceY = sourceNode.position.y + sourceDims.height / 2;
        const targetX = targetNode.position.x;
        const targetY = targetNode.position.y + targetDims.height / 2;

        const [edgePath] = getSmoothStepPath({
          sourceX,
          sourceY,
          sourcePosition: Position.Right,
          targetX,
          targetY,
          targetPosition: Position.Left,
          borderRadius: 8,
        });

        return (
          <path
            key={edge.id}
            d={edgePath}
            fill="none"
            stroke="var(--border)"
            strokeWidth={1.5}
            strokeOpacity={0.5}
          />
        );
      })}

      {/* Nodes */}
      {nodes.map((node) => {
        const data = node.data;
        const group = normalizeNodeGroup(data.group ? [data.group] : undefined);
        const dims = getNodeDims(data);
        const x = node.position.x;
        const y = node.position.y;
        const styles = getNodeStyles(group);
        const nodeExec = executionData?.[data.name];

        // Render as rect

        return (
          <g key={node.id}>
            <rect
              x={x}
              y={y}
              width={dims.width}
              height={dims.height}
              rx={12}
              ry={12}
              fill={showIcons ? styles.bgColor : styles.accentColor}
              stroke={showIcons ? styles.borderColor : styles.accentColor}
              strokeWidth={1}
              strokeOpacity={showIcons ? 1 : 0.7}
            />
            {/* Running animation */}
            {nodeExec?.status === 'running' && (
              <rect
                x={x}
                y={y}
                width={dims.width}
                height={dims.height}
                rx={12}
                ry={12}
                fill="none"
                stroke={styles.accentColor}
                strokeWidth={2}
                strokeDasharray="6 4"
                style={{ animation: 'dash-flow 0.6s linear infinite' }}
              />
            )}
            {showIcons && (
              <>
                <circle
                  cx={x + dims.width / 2}
                  cy={y + dims.height / 2}
                  r={14}
                  fill={styles.iconBgColor}
                />
                <NodeIcon data={data} x={x} y={y} dims={dims} color={styles.iconFgColor} />
              </>
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

/** Small status badge rendered at a given position */
function ExecutionBadge({ status, cx, cy }: { status: string; cx: number; cy: number }) {
  if (status === 'success') {
    return (
      <g>
        <circle cx={cx} cy={cy} r={7} fill="var(--success)" />
        <foreignObject x={cx - 5} y={cy - 5} width={10} height={10} className="pointer-events-none">
          <div className="flex items-center justify-center w-full h-full text-primary-foreground">
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
          <div className="flex items-center justify-center w-full h-full text-primary-foreground">
            <X size={8} strokeWidth={3} />
          </div>
        </foreignObject>
      </g>
    );
  }
  return null;
}

export default memo(WorkflowSVG);
