import { useState, useRef, useCallback } from 'react';
import {
  X,
  ArrowLeft,
  Play,
  Trash2,
  Loader2,
  Pencil,
  Check,
  type LucideIcon,
  MousePointer,
  Clock,
  Webhook,
  Code,
  GitBranch,
  Route,
  GitMerge,
  Layers,
  Globe,
  Pen,
  MessageSquare,
  Bot,
  AlertTriangle,
  Filter,
  Calendar,
} from 'lucide-react';
import { Panel, PanelGroup, PanelResizeHandle } from 'react-resizable-panels';

import { Dialog, DialogContent, DialogOverlay, DialogPortal, DialogTitle } from '@/shared/components/ui/dialog';
import { useNDVStore } from '../../stores/ndvStore';
import { useWorkflowStore } from '../../stores/workflowStore';
import { useNodeById, useNodeExecution } from '../../hooks/useWorkflowSelectors';
import { useExecuteWorkflow } from '../../hooks/useWorkflowApi';
import InputPanel from './InputPanel';
import OutputPanel from './OutputPanel';
import NodeSettings from './NodeSettings';
import { VisuallyHidden } from '@radix-ui/react-visually-hidden';

// Icon mapping
const iconMap: Record<string, LucideIcon> = {
  'mouse-pointer': MousePointer,
  clock: Clock,
  webhook: Webhook,
  code: Code,
  filter: Filter,
  'git-branch': GitBranch,
  route: Route,
  'git-merge': GitMerge,
  layers: Layers,
  globe: Globe,
  pen: Pen,
  calendar: Calendar,
  'message-square': MessageSquare,
  bot: Bot,
  'alert-triangle': AlertTriangle,
};

export default function NodeDetailsModal() {
  const isOpen = useNDVStore((s) => s.isOpen);
  const activeNodeId = useNDVStore((s) => s.activeNodeId);
  const closeNDV = useNDVStore((s) => s.closeNDV);
  const inputPanelSize = useNDVStore((s) => s.inputPanelSize);
  const outputPanelSize = useNDVStore((s) => s.outputPanelSize);

  // Individual selectors — only re-render when the specific data we need changes
  const deleteNode = useWorkflowStore((s) => s.deleteNode);
  const updateNodeData = useWorkflowStore((s) => s.updateNodeData);
  const activeNode = useNodeById(activeNodeId);
  const nodeExecution = useNodeExecution(activeNodeId);

  const { executeWorkflow, isExecuting } = useExecuteWorkflow();

  // Inline name editing state
  const [isEditingName, setIsEditingName] = useState(false);
  const [editedName, setEditedName] = useState('');
  const [lastNodeId, setLastNodeId] = useState<string | null>(null);
  const nameInputRef = useRef<HTMLInputElement>(null);

  // Get icon component
  const IconComponent = activeNode ? (iconMap[activeNode.data.icon || 'code'] || Code) : Code;

  // Focus input when editing starts — handled via ref callback
  const handleNameInputRef = useCallback((el: HTMLInputElement | null) => {
    (nameInputRef as React.MutableRefObject<HTMLInputElement | null>).current = el;
    if (el && isEditingName) {
      el.focus();
      el.select();
    }
  }, [isEditingName]);

  // Reset edited name when node changes - use key pattern to sync state
  if (activeNode && activeNode.id !== lastNodeId) {
    setLastNodeId(activeNode.id);
    setEditedName(activeNode.data.label);
    setIsEditingName(false);
  }

  // Execute the entire workflow to test this node
  const handleExecute = useCallback(() => {
    executeWorkflow();
  }, [executeWorkflow]);

  const handleDelete = useCallback(() => {
    if (!activeNodeId) return;
    deleteNode(activeNodeId);
    closeNDV();
  }, [activeNodeId, deleteNode, closeNDV]);

  const handleSaveName = useCallback(() => {
    if (!activeNodeId) return;
    if (editedName.trim()) {
      updateNodeData(activeNodeId, { label: editedName.trim() });
    } else if (activeNode) {
      setEditedName(activeNode.data.label);
    }
    setIsEditingName(false);
  }, [activeNodeId, editedName, updateNodeData, activeNode]);

  const handleCancelEdit = useCallback(() => {
    if (activeNode) {
      setEditedName(activeNode.data.label);
    }
    setIsEditingName(false);
  }, [activeNode]);

  const handleNameKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      handleSaveName();
    } else if (e.key === 'Escape') {
      handleCancelEdit();
    }
  };

  // Handle Escape: if editing name, cancel edit instead of closing dialog
  const handleEscapeKeyDown = useCallback((e: KeyboardEvent) => {
    if (isEditingName) {
      e.preventDefault();
      handleCancelEdit();
    }
  }, [isEditingName, handleCancelEdit]);

  if (!activeNode) return null;

  return (
    <Dialog open={isOpen} onOpenChange={(open) => { if (!open) closeNDV(); }}>
      <DialogPortal>
        <DialogOverlay />
        <DialogContent onEscapeKeyDown={handleEscapeKeyDown} className="editor-chrome">
          {/* Accessible title (visually hidden — header is custom) */}
          <VisuallyHidden>
            <DialogTitle>{activeNode.data.label} - Node Details</DialogTitle>
          </VisuallyHidden>

          {/* Consolidated Header - Node identity + controls */}
          <div className="flex items-center justify-between px-3 h-10 bg-muted/40 shrink-0">
            {/* Left: Back button */}
            <button
              onClick={closeNDV}
              className="flex items-center gap-1 rounded px-1.5 py-1 text-[13px] text-muted-foreground hover:bg-accent/70 hover:text-foreground transition-colors"
            >
              <ArrowLeft size={14} />
              <span className="hidden sm:inline">Back</span>
            </button>

            {/* Center: Node icon + name + status */}
            <div className="flex items-center gap-2 min-w-0 flex-1 justify-center">
              {/* Node icon */}
              <div className="flex h-6 w-6 flex-shrink-0 items-center justify-center rounded bg-primary/10 text-primary">
                <IconComponent size={14} />
              </div>

              {/* Editable node name */}
              {isEditingName ? (
                <div className="flex items-center gap-1">
                  <input
                    ref={handleNameInputRef}
                    type="text"
                    value={editedName}
                    onChange={(e) => setEditedName(e.target.value)}
                    onKeyDown={handleNameKeyDown}
                    onBlur={handleSaveName}
                    className="text-[13px] font-semibold text-foreground bg-background rounded-md px-2 py-0.5 border border-ring focus:outline-none focus:ring-1 focus:ring-ring/40 min-w-[120px] max-w-[200px]"
                  />
                  <button
                    onClick={handleSaveName}
                    className="p-1 rounded-md hover:bg-accent text-primary"
                    title="Save"
                  >
                    <Check size={12} />
                  </button>
                  <button
                    onClick={handleCancelEdit}
                    className="p-1 rounded-md hover:bg-accent text-muted-foreground"
                    title="Cancel"
                  >
                    <X size={12} />
                  </button>
                </div>
              ) : (
                <div className="flex items-center gap-1 group min-w-0">
                  <span className="text-[13px] font-medium text-foreground truncate max-w-[200px]">
                    {activeNode.data.label}
                  </span>
                  <button
                    onClick={() => setIsEditingName(true)}
                    className="p-0.5 rounded hover:bg-accent text-muted-foreground opacity-0 group-hover:opacity-100 transition-opacity flex-shrink-0"
                    title="Rename node"
                  >
                    <Pencil size={11} />
                  </button>
                </div>
              )}

              {/* Status badge */}
              {nodeExecution?.status === 'running' && (
                <span className="flex-shrink-0 rounded-sm bg-[var(--warning)]/10 px-1.5 py-px text-[10px] font-medium text-[var(--warning)]">
                  Running
                </span>
              )}
              {nodeExecution?.status === 'success' && (
                <span className="flex-shrink-0 rounded-sm bg-[var(--success)]/10 px-1.5 py-px text-[10px] font-medium text-[var(--success)]">
                  Success
                </span>
              )}
              {nodeExecution?.status === 'error' && (
                <span className="flex-shrink-0 rounded-sm bg-destructive/10 px-1.5 py-px text-[10px] font-medium text-destructive">
                  Error
                </span>
              )}
            </div>

            {/* Right: Action buttons */}
            <div className="flex items-center gap-1">
              <button
                onClick={handleExecute}
                disabled={isExecuting}
                className="flex items-center gap-1.5 rounded-md bg-primary/90 px-2 py-1 text-[12px] font-medium text-primary-foreground hover:bg-primary disabled:opacity-50 transition-colors"
              >
                {isExecuting ? (
                  <>
                    <Loader2 size={13} className="animate-spin" />
                    <span className="hidden sm:inline">Running...</span>
                  </>
                ) : (
                  <>
                    <Play size={13} />
                    <span className="hidden sm:inline">Test</span>
                  </>
                )}
              </button>
              <button
                onClick={handleDelete}
                className="rounded p-1 text-muted-foreground hover:bg-destructive/10 hover:text-destructive transition-colors"
                title="Delete node"
              >
                <Trash2 size={14} />
              </button>
              <button
                onClick={closeNDV}
                className="rounded p-1 text-muted-foreground hover:bg-accent transition-colors"
                title="Close"
              >
                <X size={14} />
              </button>
            </div>
          </div>

          {/* Three Panel Layout */}
          <div className="flex-1 overflow-hidden">
            <PanelGroup direction="horizontal">
              {/* Input Panel */}
              <Panel
                defaultSize={inputPanelSize}
                minSize={15}
                maxSize={40}
                onResize={(size) => useNDVStore.getState().setPanelSizes(size, useNDVStore.getState().outputPanelSize)}
              >
                <InputPanel
                  nodeId={activeNodeId!}
                  executionData={nodeExecution}
                />
              </Panel>

              <PanelResizeHandle className="w-px bg-border/50 hover:bg-primary/50 active:bg-primary transition-colors" />

              {/* Settings Panel */}
              <Panel defaultSize={100 - inputPanelSize - outputPanelSize} minSize={30}>
                <NodeSettings
                  node={activeNode}
                />
              </Panel>

              <PanelResizeHandle className="w-px bg-border/50 hover:bg-primary/50 active:bg-primary transition-colors" />

              {/* Output Panel */}
              <Panel
                defaultSize={outputPanelSize}
                minSize={15}
                maxSize={40}
                onResize={(size) => useNDVStore.getState().setPanelSizes(useNDVStore.getState().inputPanelSize, size)}
              >
                <OutputPanel
                  nodeId={activeNodeId!}
                  executionData={nodeExecution}
                />
              </Panel>
            </PanelGroup>
          </div>
        </DialogContent>
      </DialogPortal>
    </Dialog>
  );
}
