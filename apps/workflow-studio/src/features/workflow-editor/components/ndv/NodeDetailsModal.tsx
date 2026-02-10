import { useCallback, useEffect, useState, useRef, useMemo } from 'react';
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

import { useNDVStore } from '../../stores/ndvStore';
import { useWorkflowStore } from '../../stores/workflowStore';
import { useExecuteWorkflow } from '../../hooks/useWorkflowApi';
import InputPanel from './InputPanel';
import OutputPanel from './OutputPanel';
import NodeSettings from './NodeSettings';

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
  const { isOpen, activeNodeId, closeNDV, inputPanelSize, outputPanelSize, setPanelSizes } = useNDVStore();

  // Individual selectors — only re-render when the specific data we need changes
  const deleteNode = useWorkflowStore((s) => s.deleteNode);
  const updateNodeData = useWorkflowStore((s) => s.updateNodeData);
  const activeNode = useWorkflowStore(
    useCallback((s) => s.nodes.find((n) => n.id === activeNodeId) ?? null, [activeNodeId])
  );
  const nodeExecution = useWorkflowStore(
    useCallback(
      (s) => (activeNodeId ? s.executionData[activeNodeId] ?? null : null),
      [activeNodeId]
    )
  );

  const { executeWorkflow, isExecuting } = useExecuteWorkflow();

  // Inline name editing state
  const [isEditingName, setIsEditingName] = useState(false);
  const [editedName, setEditedName] = useState('');
  const [lastNodeId, setLastNodeId] = useState<string | null>(null);
  const nameInputRef = useRef<HTMLInputElement>(null);

  // Get icon component
  const IconComponent = activeNode ? (iconMap[activeNode.data.icon || 'code'] || Code) : Code;

  // Close on Escape
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && isOpen) {
        if (isEditingName) {
          setIsEditingName(false);
          return;
        }
        closeNDV();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isOpen, closeNDV, isEditingName]);

  // Focus input when editing starts
  useEffect(() => {
    if (isEditingName && nameInputRef.current) {
      nameInputRef.current.focus();
      nameInputRef.current.select();
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

  if (!isOpen || !activeNode) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-background/60 backdrop-blur-[2px]"
        onClick={closeNDV}
      />

      {/* Modal */}
      <div className="relative z-10 flex flex-col overflow-hidden rounded-lg border border-border bg-card shadow-xl" style={{ position: 'absolute', inset: 16 }}>
        {/* Consolidated Header - Node identity + controls */}
        <div className="flex items-center justify-between border-b border-border px-3 h-11 shrink-0">
          {/* Left: Back button */}
          <button
            onClick={closeNDV}
            className="flex items-center gap-1 rounded-md px-1.5 py-1 text-[13px] text-muted-foreground hover:bg-accent hover:text-foreground transition-colors"
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
                  ref={nameInputRef}
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
                <span className="text-[13px] font-semibold text-foreground truncate max-w-[200px]">
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
              <span className="flex-shrink-0 rounded bg-[var(--warning)]/10 px-1.5 py-0.5 text-[11px] font-medium text-[var(--warning)]">
                Running
              </span>
            )}
            {nodeExecution?.status === 'success' && (
              <span className="flex-shrink-0 rounded bg-[var(--success)]/10 px-1.5 py-0.5 text-[11px] font-medium text-[var(--success)]">
                Success
              </span>
            )}
            {nodeExecution?.status === 'error' && (
              <span className="flex-shrink-0 rounded bg-destructive/10 px-1.5 py-0.5 text-[11px] font-medium text-destructive">
                Error
              </span>
            )}
          </div>

          {/* Right: Action buttons */}
          <div className="flex items-center gap-1">
            <button
              onClick={handleExecute}
              disabled={isExecuting}
              className="flex items-center gap-1.5 rounded-md bg-primary px-2.5 py-1 text-[13px] font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50 transition-colors"
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
              className="rounded-md p-1.5 text-muted-foreground hover:bg-destructive/10 hover:text-destructive transition-colors"
              title="Delete node"
            >
              <Trash2 size={14} />
            </button>
            <button
              onClick={closeNDV}
              className="rounded-md p-1.5 text-muted-foreground hover:bg-accent transition-colors"
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
              onResize={(size) => setPanelSizes(size, outputPanelSize)}
            >
              <InputPanel
                nodeId={activeNodeId!}
                executionData={nodeExecution}
              />
            </Panel>

            <PanelResizeHandle className="w-1 bg-border hover:bg-primary transition-colors" />

            {/* Settings Panel */}
            <Panel defaultSize={100 - inputPanelSize - outputPanelSize} minSize={30}>
              <NodeSettings
                node={activeNode}
                onExecute={handleExecute}
              />
            </Panel>

            <PanelResizeHandle className="w-1 bg-border hover:bg-primary transition-colors" />

            {/* Output Panel */}
            <Panel
              defaultSize={outputPanelSize}
              minSize={15}
              maxSize={40}
              onResize={(size) => setPanelSizes(inputPanelSize, size)}
            >
              <OutputPanel
                nodeId={activeNodeId!}
                executionData={nodeExecution}
              />
            </Panel>
          </PanelGroup>
        </div>
      </div>
    </div>
  );
}
