import { BaseEdge, EdgeLabelRenderer, getSmoothStepPath, Position, type EdgeProps } from '@xyflow/react'

/** Route references that skip layers through the module's top gutter, around boxes. */
export function CallEdge({ sourceX, sourceY, targetX, targetY, markerEnd, style, label, labelStyle, data }: EdgeProps) {
  const routing = data as { lane: number; sourceRight: number; targetLeft: number }
  let path: string, labelX: number, labelY: number
  if (targetX - sourceX > 350 || targetX <= sourceX) {
    const exit = Math.max(sourceX + 22, routing.sourceRight + 24),
      entry = Math.min(targetX - 22, routing.targetLeft - 24)
    path = `M ${sourceX} ${sourceY} L ${exit} ${sourceY} L ${exit} ${routing.lane} L ${entry} ${routing.lane} L ${entry} ${targetY} L ${targetX} ${targetY}`
    labelX = exit
    labelY = (sourceY + routing.lane) / 2
  } else
    [path, labelX, labelY] = getSmoothStepPath({
      sourceX,
      sourceY,
      targetX,
      targetY,
      sourcePosition: Position.Right,
      targetPosition: Position.Left,
      borderRadius: 9,
    })
  return (
    <>
      <BaseEdge path={path} markerEnd={markerEnd} style={style} />
      {label && (
        <EdgeLabelRenderer>
          <div
            className="nodrag nopan map-edge-label"
            style={{
              ...labelStyle,
              opacity: style?.opacity,
              transform: `translate(-50%, -50%) translate(${labelX}px,${labelY}px)`,
            }}
          >
            {label}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  )
}
