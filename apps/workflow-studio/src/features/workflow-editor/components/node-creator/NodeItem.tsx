import type { DragEvent } from 'react';
import type { NodeDefinition } from '../../types/workflow';
import { getNodeStyles } from '../../lib/nodeStyles';
import { normalizeNodeGroup, type NodeGroup } from '../../lib/nodeConfig';
import { getIconForNode } from '../../lib/nodeIcons';
import { useWorkflowStore } from '../../stores/workflowStore';

interface NodeItemProps {
  node: NodeDefinition & { group?: NodeGroup };
  onClick: () => void;
}

export default function NodeItem({ node, onClick }: NodeItemProps) {
  const IconComponent = getIconForNode(node.icon, node.type);
  const setDraggedNodeType = useWorkflowStore((s) => s.setDraggedNodeType);

  // Get group-based styling to match canvas nodes
  const nodeGroup = normalizeNodeGroup(node.group ? [node.group] : (node.category ? [node.category] : undefined));
  const styles = getNodeStyles(nodeGroup);

  const handleDragStart = (e: DragEvent<HTMLButtonElement>) => {
    // Set the dragged node data
    e.dataTransfer.setData('application/reactflow-node', JSON.stringify(node));
    e.dataTransfer.effectAllowed = 'move';

    // Update store to track what's being dragged
    setDraggedNodeType(node.type);
  };

  const handleDragEnd = () => {
    // Clear the dragged state
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
        className={`
          flex h-8 w-8 shrink-0 items-center justify-center rounded-md
          ${nodeGroup === 'ai' ? 'node-ai-shimmer' : ''}
        `}
        style={{
          backgroundColor: styles.iconBgColor,
          color: styles.accentColor,
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
}
