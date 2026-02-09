import { memo } from 'react';
import {
  Copy,
  Scissors,
  Trash2,
  Settings,
  ClipboardPaste,
  Files,
} from 'lucide-react';
import { useWorkflowStore } from '../../stores/workflowStore';
import { useNDVStore } from '../../stores/ndvStore';
import { isTriggerType } from '../../lib/nodeConfig';

interface NodeContextMenuProps {
  nodeId: string;
  x: number;
  y: number;
  onClose: () => void;
}

function NodeContextMenu({ nodeId, x, y, onClose }: NodeContextMenuProps) {
  const copyNodes = useWorkflowStore((s) => s.copyNodes);
  const cutNodes = useWorkflowStore((s) => s.cutNodes);
  const pasteNodes = useWorkflowStore((s) => s.pasteNodes);
  const duplicateNodes = useWorkflowStore((s) => s.duplicateNodes);
  const deleteNodes = useWorkflowStore((s) => s.deleteNodes);
  const nodes = useWorkflowStore((s) => s.nodes);
  const clipboard = useWorkflowStore((s) => s.clipboard);
  const openNDV = useNDVStore((s) => s.openNDV);

  const node = nodes.find((n) => n.id === nodeId);

  // Get selected nodes - if current node is part of selection, use all selected
  const selectedNodes = nodes.filter((n) => n.selected);
  const targetNodeIds = selectedNodes.length > 0 && selectedNodes.some((n) => n.id === nodeId)
    ? selectedNodes.map((n) => n.id)
    : [nodeId];

  const menuItems = [
    {
      label: 'Open Settings',
      icon: Settings,
      shortcut: 'Enter',
      onClick: () => {
        openNDV(nodeId);
        onClose();
      },
    },
    { type: 'separator' as const },
    {
      label: 'Copy',
      icon: Copy,
      shortcut: 'Ctrl+C',
      onClick: () => {
        copyNodes(targetNodeIds);
        onClose();
      },
    },
    {
      label: 'Cut',
      icon: Scissors,
      shortcut: 'Ctrl+X',
      onClick: () => {
        cutNodes(targetNodeIds);
        onClose();
      },
    },
    {
      label: 'Paste',
      icon: ClipboardPaste,
      shortcut: 'Ctrl+V',
      disabled: !clipboard,
      onClick: () => {
        if (clipboard) {
          const nodePos = node?.position;
          pasteNodes(nodePos ? { x: nodePos.x + 50, y: nodePos.y + 50 } : undefined);
        }
        onClose();
      },
    },
    {
      label: 'Duplicate',
      icon: Files,
      shortcut: 'Ctrl+D',
      onClick: () => {
        duplicateNodes(targetNodeIds);
        onClose();
      },
    },
    { type: 'separator' as const },
    {
      label: 'Delete',
      icon: Trash2,
      shortcut: 'Del',
      danger: true,
      onClick: () => {
        const hasTrigger = targetNodeIds.some((nid) => {
          const n = nodes.find((nd) => nd.id === nid);
          return n && isTriggerType(n.data?.type || '');
        });
        if (hasTrigger) {
          const confirmed = window.confirm('This will delete a trigger node. Are you sure?');
          if (!confirmed) {
            onClose();
            return;
          }
        }
        deleteNodes(targetNodeIds);
        onClose();
      },
    },
  ];

  return (
    <>
      {/* Backdrop to close menu */}
      <div
        className="fixed inset-0 z-50"
        onClick={onClose}
        onContextMenu={(e) => {
          e.preventDefault();
          onClose();
        }}
      />

      {/* Context menu */}
      <div
        className="fixed z-50 min-w-[180px] rounded-lg border border-border bg-popover p-1 shadow-lg"
        style={{
          left: x,
          top: y,
        }}
      >
        {menuItems.map((item, index) => {
          if (item.type === 'separator') {
            return <div key={index} className="my-1 h-px bg-border" />;
          }

          const Icon = item.icon;
          return (
            <button
              key={item.label}
              onClick={item.onClick}
              disabled={item.disabled}
              className={`
                flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-sm
                transition-colors
                ${item.disabled
                  ? 'cursor-not-allowed opacity-50'
                  : item.danger
                    ? 'text-destructive hover:bg-destructive/10'
                    : 'hover:bg-accent'
                }
              `}
            >
              <Icon size={14} />
              <span className="flex-1 text-left">{item.label}</span>
              {item.shortcut && (
                <span className="text-xs text-muted-foreground">{item.shortcut}</span>
              )}
            </button>
          );
        })}
      </div>
    </>
  );
}

export default memo(NodeContextMenu);
