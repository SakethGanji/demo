import { memo, useMemo } from 'react';
import { Handle, Position, type NodeProps } from 'reactflow';
import type { WorkflowNodeData, SubnodeType } from '../../../types/workflow';
import { getIconForNode } from '../../../lib/nodeIcons';
import { cn } from '@/shared/lib/utils';

// Subnode styling config using CSS variables
interface SubnodeStyleConfig {
  accentColor: string;
  bgColor: string;
  borderColor: string;
  iconBgColor: string;
}

function getSubnodeStyles(type: SubnodeType): SubnodeStyleConfig {
  const styles: Record<SubnodeType, SubnodeStyleConfig> = {
    model: {
      accentColor: 'var(--subnode-model)',
      bgColor: 'var(--subnode-model-light)',
      borderColor: 'var(--subnode-model-border)',
      iconBgColor: 'var(--subnode-model-icon-bg)',
    },
    memory: {
      accentColor: 'var(--subnode-memory)',
      bgColor: 'var(--subnode-memory-light)',
      borderColor: 'var(--subnode-memory-border)',
      iconBgColor: 'var(--subnode-memory-icon-bg)',
    },
    tool: {
      accentColor: 'var(--subnode-tool)',
      bgColor: 'var(--subnode-tool-light)',
      borderColor: 'var(--subnode-tool-border)',
      iconBgColor: 'var(--subnode-tool-icon-bg)',
    },
  };
  return styles[type];
}

function SubnodeNode({ data, selected }: NodeProps<WorkflowNodeData>) {
  const subnodeType = data.subnodeType || 'tool';
  const styles = useMemo(() => getSubnodeStyles(subnodeType), [subnodeType]);

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
      {/* Circular node with consistent styling */}
      <div
        className={cn(
          'w-11 h-11 rounded-full flex items-center justify-center',
          'border-2 transition-all duration-200 cursor-grab',
          selected && 'ring-2 ring-offset-2 ring-offset-background',
          data.disabled && 'opacity-50',
        )}
        style={{
          backgroundColor: styles.bgColor,
          borderColor: selected ? styles.accentColor : styles.borderColor,
          boxShadow: selected ? `0 4px 12px color-mix(in srgb, ${styles.accentColor} 40%, transparent)` : '0 1px 3px rgba(0,0,0,0.1)',
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

        {/* Icon with background */}
        <div
          className="flex h-6 w-6 items-center justify-center rounded-full"
          style={{
            backgroundColor: styles.iconBgColor,
            color: styles.accentColor,
          }}
        >
          <IconComponent size={14} />
        </div>
      </div>

      {/* Label below node - truncated with ellipsis */}
      <div className="mt-1.5 w-[80px] text-center">
        <span
          className="text-[10px] text-foreground/80 font-medium leading-tight block truncate"
          title={data.label}
        >
          {data.label}
        </span>
      </div>
    </div>
  );
}

export default memo(SubnodeNode);
