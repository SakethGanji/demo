import { memo } from 'react';
import { getSmoothStepPath, type EdgeProps, Position } from 'reactflow';
import type { SubnodeEdgeData } from '../../../types/workflow';

interface SubnodeEdgeProps extends EdgeProps {
  data?: SubnodeEdgeData;
}

function SubnodeEdge({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  style = {},
}: SubnodeEdgeProps) {
  // Calculate edge path - vertical connection (subnode above connects to parent below)
  const [edgePath] = getSmoothStepPath({
    sourceX,
    sourceY,
    sourcePosition: Position.Top,
    targetX,
    targetY,
    targetPosition: Position.Bottom,
    borderRadius: 8,
  });

  return (
    <g className="react-flow__edge">
      {/* Main edge path - subtle gray dashed line like n8n */}
      <path
        id={id}
        d={edgePath}
        fill="none"
        stroke="var(--border)"
        strokeWidth={1}
        strokeDasharray="4 3"
        strokeOpacity={0.7}
        style={style}
        className="react-flow__edge-path"
      />
      {/* Invisible wider path for better hover/click detection */}
      <path
        d={edgePath}
        fill="none"
        stroke="transparent"
        strokeWidth={15}
        className="react-flow__edge-interaction"
      />
    </g>
  );
}

export default memo(SubnodeEdge);
