import { memo, useMemo } from 'react';
import { Handle, Position, type NodeProps } from 'reactflow';
import { useQuery } from '@tanstack/react-query';
import { ExternalLink, AlertCircle, Loader2, Check, X } from 'lucide-react';
import { useWorkflowStore } from '../../../stores/workflowStore';
import type { WorkflowNodeData } from '../../../types/workflow';
import { workflowsApi } from '@/shared/lib/api';
import { fromBackendWorkflow } from '../../../lib/workflowTransform';
import { calculateNodeDimensions } from '../../../lib/nodeStyles';
import { ErrorBoundary } from '@/shared/components/ErrorBoundary';
import WorkflowSVG from '../../WorkflowSVG';

const TITLE_BAR_HEIGHT = 36;

function SubworkflowNodeInner({ id, data, selected }: NodeProps<WorkflowNodeData>) {
  const executionData = useWorkflowStore((s) => s.executionData[id]);
  const subExecData = useWorkflowStore((s) => s.subworkflowExecutionData[id]);
  const subworkflowId = data.subworkflowId;

  // Fetch the referenced workflow as a full API response, then transform it
  const { data: innerFlow, isLoading, isError: isQueryError } = useQuery({
    queryKey: ['workflow-preview', subworkflowId],
    queryFn: async () => {
      const apiData = await workflowsApi.get(subworkflowId!);
      const { nodes, edges } = fromBackendWorkflow(apiData);
      return { nodes, edges };
    },
    enabled: !!subworkflowId,
    staleTime: 60_000,
    retry: 1,
  });

  const handleOpenSubworkflow = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (subworkflowId) {
      window.open(`/editor?workflowId=${subworkflowId}`, '_blank');
    }
  };

  const isRunning = executionData?.status === 'running';
  const isSuccess = executionData?.status === 'success';
  const isError = executionData?.status === 'error';

  // Convert any subworkflowNode types to workflowNode for inner rendering (avoid recursion)
  const safeNodes = useMemo(() => {
    if (!innerFlow?.nodes) return [];
    return innerFlow.nodes.map((n) =>
      n.type === 'subworkflowNode' ? { ...n, type: 'workflowNode' } : n
    );
  }, [innerFlow?.nodes]);

  const safeEdges = innerFlow?.edges ?? [];

  // Responsive container sizing — scale proportionally to content so nodes stay readable
  const containerSize = useMemo(() => {
    const visible = innerFlow?.nodes?.filter((n) => !n.data.stacked);
    if (!visible?.length) return { width: 280, height: 180 };
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    for (const n of visible) {
      const dims = calculateNodeDimensions(
        n.data.inputCount ?? 1,
        n.data.outputCount ?? 1,
        n.data.subnodeSlots?.length ?? 0,
      );
      minX = Math.min(minX, n.position.x);
      minY = Math.min(minY, n.position.y);
      maxX = Math.max(maxX, n.position.x + dims.width);
      maxY = Math.max(maxY, n.position.y + dims.height);
    }
    // Content extent + same padding as the SVG viewBox (40px each side)
    const contentW = maxX - minX + 80;
    const contentH = maxY - minY + 80;
    // Scale to 80% so nodes stay close to real size
    const scale = 0.8;
    return {
      width: Math.max(280, Math.round(contentW * scale)),
      // Add title bar so the SVG area itself gets the full scaled height
      height: Math.max(180, Math.round(contentH * scale) + TITLE_BAR_HEIGHT),
    };
  }, [innerFlow?.nodes]);

  return (
    <div
      className={`
        relative cursor-grab border-2 border-dashed rounded-lg transition-all duration-300 overflow-hidden
        ${selected ? 'ring-2 ring-offset-1 ring-[var(--node-subworkflow)]' : ''}
        ${isRunning ? 'animate-pulse' : ''}
      `}
      style={{
        width: containerSize.width,
        height: containerSize.height,
        backgroundColor: 'var(--node-subworkflow-light)',
        borderColor: isRunning ? 'var(--node-subworkflow)' : (selected ? 'var(--node-subworkflow)' : 'var(--node-subworkflow-border)'),
        boxShadow: selected ? '0 4px 12px var(--node-subworkflow)40' : '0 1px 3px rgba(0,0,0,0.1)',
      }}
    >
      {/* Input Handle */}
      <Handle
        type="target"
        position={Position.Left}
        id="main"
        style={{ top: '50%', backgroundColor: 'var(--node-handle)', borderColor: 'var(--node-handle)' }}
        className="!h-1.5 !w-1.5 !border-2"
      />

      {/* Title bar */}
      <div
        className="flex items-center gap-2 px-3 border-b"
        style={{
          height: TITLE_BAR_HEIGHT,
          backgroundColor: 'var(--node-subworkflow-icon-bg)',
          borderColor: 'var(--node-subworkflow-border)',
        }}
      >
        <span className="text-xs font-semibold truncate flex-1" style={{ color: 'var(--node-subworkflow)' }}>
          {data.label || 'Subworkflow'}
        </span>
        <button
          className="nodrag shrink-0 opacity-50 hover:opacity-100 transition-opacity cursor-pointer"
          style={{ color: 'var(--node-subworkflow)', pointerEvents: 'all' }}
          onClick={handleOpenSubworkflow}
          title="Open in editor"
        >
          <ExternalLink size={12} />
        </button>
      </div>

      {/* Inner workflow preview */}
      <div
        className="relative"
        style={{ height: containerSize.height - TITLE_BAR_HEIGHT, pointerEvents: 'none' }}
      >
        {isLoading && (
          <div className="flex items-center justify-center h-full">
            <Loader2 size={20} className="animate-spin text-muted-foreground" />
          </div>
        )}

        {isQueryError && !isLoading && (
          <div className="flex flex-col items-center justify-center h-full gap-1.5">
            <AlertCircle size={18} className="text-destructive" />
            <span className="text-xs text-destructive">Workflow not found</span>
          </div>
        )}

        {!isLoading && !isQueryError && safeNodes.length > 0 && (
          <ErrorBoundary fallback={<div className="flex items-center justify-center h-full text-xs text-muted-foreground">Preview unavailable</div>}>
            <WorkflowSVG
              nodes={safeNodes}
              edges={safeEdges}
              executionData={subExecData}
              showIcons
              width={containerSize.width}
              height={containerSize.height - TITLE_BAR_HEIGHT}
            />
          </ErrorBoundary>
        )}
      </div>

      {/* Outer status badges */}
      {isSuccess && (
        <div className="absolute -top-1.5 -right-1.5 w-5 h-5 rounded-full bg-[var(--success)] flex items-center justify-center text-white shadow-sm animate-badge-pop z-10">
          <Check size={12} strokeWidth={3} />
        </div>
      )}
      {isError && (
        <div className="absolute -top-1.5 -right-1.5 w-5 h-5 rounded-full bg-destructive flex items-center justify-center text-white shadow-sm animate-badge-pop z-10">
          <X size={12} strokeWidth={3} />
        </div>
      )}

      {/* Output Handle */}
      <Handle
        type="source"
        position={Position.Right}
        id="main"
        style={{ top: '50%', backgroundColor: 'var(--node-handle)', borderColor: 'var(--node-handle)' }}
        className="!h-1.5 !w-1.5 !border-2"
      />
    </div>
  );
}

export default memo(SubworkflowNodeInner);
