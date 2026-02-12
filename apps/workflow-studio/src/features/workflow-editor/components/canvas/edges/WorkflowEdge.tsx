import { memo, useState, useMemo, useRef, useEffect, useCallback } from 'react';
import {
  EdgeLabelRenderer,
  getSmoothStepPath,
  useReactFlow,
  type EdgeProps,
} from 'reactflow';
import { Plus } from 'lucide-react';
import { useEditorLayoutStore } from '../../../stores/editorLayoutStore';
import { useWorkflowStore } from '../../../stores/workflowStore';
import { useNodeExecStatus } from '../../../hooks/useWorkflowSelectors';
import type { WorkflowNodeData } from '../../../types/workflow';

type Point = { x: number; y: number };

const EDGE_COLORS = {
  default: { start: 'var(--border)', end: 'var(--border)' },
  hover: { start: 'var(--muted-foreground)', end: 'var(--muted-foreground)' },
  running: { start: 'var(--warning)', end: 'var(--warning)' },
  success: { start: 'var(--success)', end: 'var(--success)' },
  error: { start: 'var(--destructive)', end: 'var(--destructive)' },
};

const WAYPOINT_SIZE = 16;

function buildWaypointPath(points: Point[], radius = 8): string {
  // Filter out consecutive duplicate points (zero-length segments cause NaN)
  const pts: Point[] = [points[0]];
  for (let i = 1; i < points.length; i++) {
    if (Math.hypot(points[i].x - pts[pts.length - 1].x, points[i].y - pts[pts.length - 1].y) > 0.5) {
      pts.push(points[i]);
    }
  }

  if (pts.length < 2) return '';
  if (pts.length === 2) {
    return `M ${pts[0].x} ${pts[0].y} L ${pts[1].x} ${pts[1].y}`;
  }

  const parts: string[] = [`M ${pts[0].x} ${pts[0].y}`];

  for (let i = 1; i < pts.length - 1; i++) {
    const prev = pts[i - 1];
    const curr = pts[i];
    const next = pts[i + 1];

    const dPrev = Math.hypot(curr.x - prev.x, curr.y - prev.y);
    const dNext = Math.hypot(next.x - curr.x, next.y - curr.y);
    const r = Math.min(radius, dPrev / 2, dNext / 2);

    const ax = curr.x - (r / dPrev) * (curr.x - prev.x);
    const ay = curr.y - (r / dPrev) * (curr.y - prev.y);
    const dx = curr.x + (r / dNext) * (next.x - curr.x);
    const dy = curr.y + (r / dNext) * (next.y - curr.y);

    parts.push(`L ${ax} ${ay}`);
    parts.push(`Q ${curr.x} ${curr.y} ${dx} ${dy}`);
  }

  const last = pts[pts.length - 1];
  parts.push(`L ${last.x} ${last.y}`);
  return parts.join(' ');
}

function getPolylineMidpoint(points: Point[]): Point {
  if (points.length === 0) return { x: 0, y: 0 };
  if (points.length === 1) return points[0];

  let totalLength = 0;
  const segmentLengths: number[] = [];
  for (let i = 1; i < points.length; i++) {
    const len = Math.hypot(points[i].x - points[i - 1].x, points[i].y - points[i - 1].y);
    segmentLengths.push(len);
    totalLength += len;
  }

  const halfLength = totalLength / 2;
  let accumulated = 0;
  for (let i = 0; i < segmentLengths.length; i++) {
    if (accumulated + segmentLengths[i] >= halfLength) {
      const remaining = halfLength - accumulated;
      const t = segmentLengths[i] > 0 ? remaining / segmentLengths[i] : 0;
      return {
        x: points[i].x + t * (points[i + 1].x - points[i].x),
        y: points[i].y + t * (points[i + 1].y - points[i].y),
      };
    }
    accumulated += segmentLengths[i];
  }
  return points[points.length - 1];
}

function findClosestSegment(allPoints: Point[], click: Point): number {
  let minDist = Infinity;
  let bestSegment = 0;
  for (let i = 0; i < allPoints.length - 1; i++) {
    const dist = pointToSegmentDistance(click, allPoints[i], allPoints[i + 1]);
    if (dist < minDist) {
      minDist = dist;
      bestSegment = i;
    }
  }
  return bestSegment;
}

function pointToSegmentDistance(p: Point, a: Point, b: Point): number {
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const lenSq = dx * dx + dy * dy;
  if (lenSq === 0) return Math.hypot(p.x - a.x, p.y - a.y);
  let t = ((p.x - a.x) * dx + (p.y - a.y) * dy) / lenSq;
  t = Math.max(0, Math.min(1, t));
  return Math.hypot(p.x - (a.x + t * dx), p.y - (a.y + t * dy));
}

function WorkflowEdge({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  style = {},
  source,
  target,
  sourceHandleId,
  data,
}: EdgeProps) {
  const [isHovered, setIsHovered] = useState(false);
  const [dragState, setDragState] = useState<{ index: number; pos: Point } | null>(null);
  const isDraggingRef = useRef(false);
  const dragListenersRef = useRef<{ move: (e: MouseEvent) => void; up: (e: MouseEvent) => void } | null>(null);
  const hoverTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Cleanup drag listeners on unmount to prevent leaks
  useEffect(() => {
    return () => {
      if (dragListenersRef.current) {
        window.removeEventListener('mousemove', dragListenersRef.current.move);
        window.removeEventListener('mouseup', dragListenersRef.current.up);
      }
    };
  }, []);

  const openForConnection = useEditorLayoutStore((s) => s.openForConnection);
  const { getNode, screenToFlowPosition } = useReactFlow();
  const sourceExecStatus = useNodeExecStatus(source);
  const targetExecStatus = useNodeExecStatus(target);
  const isAnyNodeRunning = useWorkflowStore((s) => s.isAnyNodeRunning);
  const draggedNodeType = useWorkflowStore((s) => s.draggedNodeType);

  // Debounced hover: prevents flicker when mouse moves between SVG path and HTML overlays
  const hoverEnter = useCallback(() => {
    if (hoverTimeoutRef.current) {
      clearTimeout(hoverTimeoutRef.current);
      hoverTimeoutRef.current = null;
    }
    setIsHovered(true);
  }, []);

  const hoverLeave = useCallback(() => {
    if (isDraggingRef.current) return;
    hoverTimeoutRef.current = setTimeout(() => setIsHovered(false), 150);
  }, []);

  const storeWaypoints: Point[] = (data as { waypoints?: Point[] } | undefined)?.waypoints || [];

  // During drag, override the dragged waypoint with local position for smooth rendering
  const waypoints = useMemo(() => {
    if (!dragState) return storeWaypoints;
    return storeWaypoints.map((wp, i) => (i === dragState.index ? dragState.pos : wp));
  }, [storeWaypoints, dragState]);

  const isDragging = !!draggedNodeType;

  const edgeStatus = useMemo(() => {
    if (targetExecStatus === 'running') return 'running';
    if (targetExecStatus === 'success') return 'success';
    if (targetExecStatus === 'error') return 'error';
    if (sourceExecStatus === 'success' && !targetExecStatus) {
      return isAnyNodeRunning ? 'running' : 'default';
    }
    return 'default';
  }, [sourceExecStatus, targetExecStatus, isAnyNodeRunning]);

  const outputLabel = useMemo(() => {
    const sourceNode = getNode(source);
    if (!sourceNode) return null;
    const nodeData = sourceNode.data as WorkflowNodeData;
    const outputs = nodeData.outputs || [];
    const outputCount = nodeData.outputCount ?? outputs.length;
    if (outputCount <= 1) return null;

    let output;
    if (sourceHandleId) output = outputs.find((o) => o.name === sourceHandleId);
    if (!output && sourceHandleId?.startsWith('output-')) {
      const index = parseInt(sourceHandleId.replace('output-', ''), 10);
      if (!isNaN(index) && outputs[index]) output = outputs[index];
    }
    if (!output && !sourceHandleId && outputs.length > 0) output = outputs[0];
    if (!output) return null;

    const label = output.displayName || output.name;
    if (['main', 'output', 'Output'].includes(label)) return null;
    return label;
  }, [source, sourceHandleId, getNode]);

  const { edgePath, labelX, labelY } = useMemo(() => {
    if (waypoints.length > 0) {
      const pts: Point[] = [{ x: sourceX, y: sourceY }, ...waypoints, { x: targetX, y: targetY }];
      return { edgePath: buildWaypointPath(pts), ...(() => { const m = getPolylineMidpoint(pts); return { labelX: m.x, labelY: m.y }; })() };
    }
    const [path, lx, ly] = getSmoothStepPath({ sourceX, sourceY, sourcePosition, targetX, targetY, targetPosition, borderRadius: 8 });
    return { edgePath: path, labelX: lx, labelY: ly };
  }, [waypoints, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition]);

  const gradientCoords = { x1: sourceX, y1: sourceY, x2: targetX, y2: targetY };

  const handleAddNode = (e: React.MouseEvent) => {
    e.stopPropagation();
    openForConnection(source, `edge-${id}`);
  };

  // Double-click edge → add waypoint
  const handleEdgeDoubleClick = useCallback((e: React.MouseEvent) => {
    e.stopPropagation();
    e.preventDefault();
    const flowPos = screenToFlowPosition({ x: e.clientX, y: e.clientY });

    // Seed orthogonal corners on first waypoint so squared shape is preserved
    let currentWaypoints = [...storeWaypoints];
    if (currentWaypoints.length === 0) {
      const isHorizontal = sourcePosition === 'right' || sourcePosition === 'left';
      if (isHorizontal && sourceY !== targetY) {
        // Side handles: horizontal first, then vertical
        const midX = (sourceX + targetX) / 2;
        currentWaypoints = [{ x: midX, y: sourceY }, { x: midX, y: targetY }];
      } else if (!isHorizontal && sourceX !== targetX) {
        // Top/bottom handles: vertical first, then horizontal
        const midY = (sourceY + targetY) / 2;
        currentWaypoints = [{ x: sourceX, y: midY }, { x: targetX, y: midY }];
      }
      // If source and target are aligned (same X or same Y), skip seeding —
      // the edge is a straight line anyway, just add the clicked point
    }

    const allPoints: Point[] = [{ x: sourceX, y: sourceY }, ...currentWaypoints, { x: targetX, y: targetY }];
    const segmentIdx = findClosestSegment(allPoints, flowPos);
    const newWaypoints = [...currentWaypoints];
    newWaypoints.splice(segmentIdx, 0, flowPos);

    useWorkflowStore.getState().saveToHistory();
    useWorkflowStore.getState().updateEdgeWaypoints(id, newWaypoints);
  }, [id, storeWaypoints, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, screenToFlowPosition]);

  // Drag waypoint: local state during move, commit to store on mouseup
  const handleWaypointMouseDown = useCallback((e: React.MouseEvent, wpIndex: number) => {
    e.stopPropagation();
    e.preventDefault();
    isDraggingRef.current = true;
    useWorkflowStore.getState().saveToHistory();

    const onMouseMove = (moveEvent: MouseEvent) => {
      const flowPos = screenToFlowPosition({ x: moveEvent.clientX, y: moveEvent.clientY });
      setDragState({ index: wpIndex, pos: flowPos });
    };

    const onMouseUp = (upEvent: MouseEvent) => {
      isDraggingRef.current = false;
      window.removeEventListener('mousemove', onMouseMove);
      window.removeEventListener('mouseup', onMouseUp);
      dragListenersRef.current = null;

      const flowPos = screenToFlowPosition({ x: upEvent.clientX, y: upEvent.clientY });
      const currentWp = [...(useWorkflowStore.getState().edges.find((edge) => edge.id === id)?.data as { waypoints?: Point[] } | undefined)?.waypoints || []];
      if (wpIndex < currentWp.length) {
        currentWp[wpIndex] = flowPos;
        useWorkflowStore.getState().updateEdgeWaypoints(id, currentWp);
      }
      setDragState(null);
    };

    dragListenersRef.current = { move: onMouseMove, up: onMouseUp };
    window.addEventListener('mousemove', onMouseMove);
    window.addEventListener('mouseup', onMouseUp);
  }, [id, screenToFlowPosition]);

  // Right-click waypoint → remove it
  const handleWaypointContextMenu = useCallback((e: React.MouseEvent, wpIndex: number) => {
    e.preventDefault();
    e.stopPropagation();
    const currentWp = [...storeWaypoints];
    currentWp.splice(wpIndex, 1);
    useWorkflowStore.getState().saveToHistory();
    useWorkflowStore.getState().updateEdgeWaypoints(id, currentWp.length > 0 ? currentWp : undefined);
  }, [id, storeWaypoints]);

  const edgeColors = useMemo(() => {
    if (edgeStatus !== 'default') return EDGE_COLORS[edgeStatus];
    if (isDragging) return { start: 'var(--success)', end: 'var(--success)' };
    return isHovered ? EDGE_COLORS.hover : EDGE_COLORS.default;
  }, [edgeStatus, isHovered, isDragging]);

  // Track transitions — only show particles when status *changes*, not on stale remounts
  const prevEdgeStatusRef = useRef<string | null>(null);
  const [liveRunning, setLiveRunning] = useState(false);
  const [liveSuccess, setLiveSuccess] = useState(false);

  useEffect(() => {
    const prev = prevEdgeStatusRef.current;
    prevEdgeStatusRef.current = edgeStatus;

    if (prev === null) return; // skip initial mount — avoids replaying stale animations

    if (edgeStatus === 'running' && prev !== 'running') {
      setLiveRunning(true);
    } else if (edgeStatus !== 'running') {
      setLiveRunning(false);
    }

    if (edgeStatus === 'success' && prev !== 'success') {
      setLiveSuccess(true);
      const t = setTimeout(() => setLiveSuccess(false), 600);
      return () => clearTimeout(t);
    } else if (edgeStatus !== 'success') {
      setLiveSuccess(false);
    }
  }, [edgeStatus]);

  const isAnimated = liveRunning;
  const isEdgeIdle = edgeStatus === 'default' && isAnyNodeRunning;
  const gradientId = `edge-gradient-${id}`;

  return (
    <>
      <defs>
        <linearGradient id={gradientId} gradientUnits="userSpaceOnUse" {...gradientCoords}>
          <stop offset="0%" stopColor={edgeColors.start} />
          <stop offset="100%" stopColor={edgeColors.end} />
        </linearGradient>
        <marker id={`arrow-${id}`} markerWidth="12" markerHeight="12" refX="10" refY="6" orient="auto" markerUnits="userSpaceOnUse">
          <path d="M2,2 L10,6 L2,10 L4,6 Z" fill={edgeColors.end} opacity={isEdgeIdle ? 0.25 : 1} />
        </marker>
      </defs>

      {/* Highlight layer for active edges */}
      {edgeStatus !== 'default' && (
        <path d={edgePath} fill="none" stroke={`url(#${gradientId})`} strokeWidth={4} strokeOpacity={0.2} strokeLinecap="round" />
      )}

      {/* Main edge path */}
      <path
        id={id}
        className="react-flow__edge-path"
        d={edgePath}
        fill="none"
        stroke={`url(#${gradientId})`}
        strokeWidth={edgeStatus !== 'default' ? 2.5 : (isHovered ? 2 : 1.5)}
        strokeOpacity={isEdgeIdle ? 0.25 : 1}
        markerEnd={`url(#arrow-${id})`}
        style={{
          ...style,
          strokeDasharray: isAnimated ? '8 4' : 'none',
          animation: isAnimated ? 'flowAnimation 0.5s linear infinite' : 'none',
          transition: 'stroke-opacity 0.4s ease',
        }}
      />

      {/* Animated particles — data flowing through edges (only on live transitions) */}
      {liveRunning && [0, 0.67, 1.33].map((delay, i) => (
        <g key={`particle-${i}`}>
          <circle r={7} fill="var(--warning)" opacity={0.12} />
          <circle r={i === 0 ? 3 : 2} fill="var(--warning)" opacity={i === 0 ? 0.9 : 0.7} />
          <animateMotion dur="2s" repeatCount="indefinite" begin={`${delay}s`}>
            <mpath href={`#${id}`} />
          </animateMotion>
        </g>
      ))}

      {/* Success completion particle (only on live transition to success) */}
      {liveSuccess && (
        <circle r={4} fill="var(--success)">
          <animateMotion dur="0.5s" fill="freeze">
            <mpath href={`#${id}`} />
          </animateMotion>
          <animate attributeName="opacity" values="0.9;0.9;0" keyTimes="0;0.7;1" dur="0.5s" fill="freeze" />
        </circle>
      )}

      {/* Invisible interaction path — wider for reliable hover/double-click */}
      <path
        d={edgePath}
        fill="none"
        strokeWidth={30}
        stroke="transparent"
        className="react-flow__edge-interaction"
        onMouseEnter={hoverEnter}
        onMouseLeave={hoverLeave}
        onDoubleClick={handleEdgeDoubleClick}
      />

      {/* Output label near the source node */}
      {outputLabel && (
        <EdgeLabelRenderer>
          <div
            style={{
              position: 'absolute',
              transform: `translate(0, -50%) translate(${sourceX + 14}px, ${sourceY}px)`,
              pointerEvents: 'none',
              color: edgeColors.end,
            }}
            className="text-[10px] font-medium bg-background/90 px-1.5 py-0.5 rounded"
          >
            {outputLabel}
          </div>
        </EdgeLabelRenderer>
      )}

      {/* Waypoint handles — always visible when waypoints exist */}
      {/* Drag to move, right-click to delete */}
      {waypoints.length > 0 && (
        <EdgeLabelRenderer>
          {waypoints.map((wp, i) => (
            <div
              key={`wp-${id}-${i}`}
              style={{
                position: 'absolute',
                transform: `translate(-50%, -50%) translate(${wp.x}px, ${wp.y}px)`,
                pointerEvents: 'all',
                width: WAYPOINT_SIZE,
                height: WAYPOINT_SIZE,
                borderRadius: '50%',
                backgroundColor: 'var(--background)',
                border: `2px solid ${edgeColors.end}`,
                cursor: 'grab',
                boxShadow: '0 1px 3px rgba(0,0,0,0.2)',
              }}
              className="nodrag nopan"
              onMouseEnter={hoverEnter}
              onMouseLeave={hoverLeave}
              onMouseDown={(e) => handleWaypointMouseDown(e, i)}
              onContextMenu={(e) => handleWaypointContextMenu(e, i)}
            />
          ))}
        </EdgeLabelRenderer>
      )}

      {/* Add node button on hover OR drop zone indicator when dragging */}
      {(isHovered || isDragging) && (
        <EdgeLabelRenderer>
          <div
            style={{
              position: 'absolute',
              transform: `translate(-50%, -50%) translate(${labelX}px,${labelY}px)`,
              pointerEvents: 'all',
            }}
            className="nodrag nopan"
            onMouseEnter={hoverEnter}
            onMouseLeave={hoverLeave}
          >
            {isDragging ? (
              <div className="flex h-8 w-8 items-center justify-center rounded-full border-2 border-dashed border-[var(--success)] bg-[var(--success)]/20 animate-pulse">
                <Plus size={16} className="text-[var(--success)]" />
              </div>
            ) : (
              <button
                onClick={handleAddNode}
                className="flex h-6 w-6 items-center justify-center rounded-full border border-border bg-card shadow-sm transition-all hover:bg-accent hover:scale-110"
              >
                <Plus size={14} className="text-muted-foreground" />
              </button>
            )}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  );
}

export default memo(WorkflowEdge);
