import { memo, useMemo } from 'react';
import { Handle, Position, type NodeProps } from 'reactflow';
import type { WorkflowNodeData } from '../../../types/workflow';
import { getNodeStyles } from '../../../lib/nodeStyles';
import { normalizeNodeGroup } from '../../../lib/nodeConfig';
import { getIconForNode } from '../../../lib/nodeIcons';
import { useWorkflowStore } from '../../../stores/workflowStore';
import { cn } from '@/shared/lib/utils';

function SubnodeNode({ id, data, selected }: NodeProps<WorkflowNodeData>) {
  const edges = useWorkflowStore((s) => s.edges);
  const allNodes = useWorkflowStore((s) => s.nodes);

  // Find parent node via subnodeEdge to inherit its colors
  const parentStyles = useMemo(() => {
    const parentEdge = edges.find(
      (e) => e.source === id && e.data?.isSubnodeEdge
    );
    if (!parentEdge) return null;
    const parentNode = allNodes.find((n) => n.id === parentEdge.target);
    if (!parentNode) return null;
    const parentData = parentNode.data as WorkflowNodeData;
    const group = normalizeNodeGroup(parentData.group ? [parentData.group] : undefined);
    return getNodeStyles(group);
  }, [edges, allNodes, id]);

  // Fallback to ai styles if parent not found
  const styles = parentStyles ?? getNodeStyles('ai');

  // Get icon using shared utility
  const IconComponent = getIconForNode(data.icon, data.type);

  // When stacked, hide visually but keep in ReactFlow graph
  if (data.stacked) {
    return (
      <div style={{ visibility: 'hidden', width: 0, height: 0, overflow: 'hidden' }}>
        <Handle
          type="source"
          position={Position.Top}
          id="config"
          style={{ visibility: 'hidden' }}
        />
      </div>
    );
  }

  return (
    <div className="relative flex flex-col items-center pt-1">
      {/* Circular node — inherits parent node's color */}
      <div
        className={cn(
          'rounded-full flex items-center justify-center',
          'border-2 transition-all duration-200 cursor-grab',
          selected && 'ring-2 ring-offset-2 ring-offset-background',
          data.disabled && 'opacity-50',
        )}
        style={{
          width: 72,
          height: 72,
          backgroundColor: styles.iconBgColor,
          borderColor: selected ? styles.accentColor : styles.borderColor,
          boxShadow: selected ? `0 4px 12px color-mix(in srgb, ${styles.accentColor} 40%, transparent)` : '0 1px 3px rgba(0,0,0,0.08)',
          // @ts-expect-error CSS custom property
          '--tw-ring-color': styles.accentColor,
        }}
      >
        {/* Top handle - connects to parent node */}
        <Handle
          type="source"
          position={Position.Top}
          id="config"
          className="!w-1.5 !h-1.5 !border-2"
          style={{
            backgroundColor: 'var(--node-handle)',
            borderColor: 'var(--node-handle)',
          }}
        />

        {/* Icon */}
        <div
          className="flex h-9 w-9 items-center justify-center rounded-full"
          style={{ color: '#ffffff' }}
        >
          <IconComponent size={22} />
        </div>
      </div>

      {/* Label below node */}
      <div className="mt-1.5 w-[80px] text-center">
        <span
          className="text-[10px] text-muted-foreground font-medium leading-tight block truncate"
          title={data.label}
        >
          {data.label}
        </span>
      </div>
    </div>
  );
}

export default memo(SubnodeNode);
