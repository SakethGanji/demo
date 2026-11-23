import { useState, useRef, useCallback } from 'react';
import {
  X,
  ArrowLeft,
  FlaskConical,
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

import { Dialog, DialogContent, DialogTitle } from '@/shared/components/ui/dialog';
import { Button } from '@/shared/components/ui/button';
import { Badge } from '@/shared/components/ui/badge';
import { Input } from '@/shared/components/ui/input';
import { Segmented } from '@/shared/components/ui/segmented';
import { Callout } from '@/shared/components/ui/surface';
import CodeEditor from '@/shared/components/ui/code-editor';
import { getNodeStyles } from '../../lib/nodeStyles';
import { normalizeNodeGroup } from '../../lib/nodeConfig';
import { useNDVStore } from '../../stores/ndvStore';
import { useWorkflowStore } from '../../stores/workflowStore';
import { useNodeById, useNodeExecution } from '../../hooks/useWorkflowSelectors';
import { useNodeActions } from '../../hooks/useNodeActions';
import { describeActionError, testStep, type TestSource } from '../../lib/nodeActions';
import InputPanel from './InputPanel';
import OutputPanel from './OutputPanel';
import NodeSettings from './NodeSettings';
import { VisuallyHidden } from '@radix-ui/react-visually-hidden';
import { NodeAIMenu } from '@/features/ai-builder/components/NodeAIMenu';

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
  const workflowId = useWorkflowStore((s) => s.workflowId);
  const updateNodeData = useWorkflowStore((s) => s.updateNodeData);
  const activeNode = useNodeById(activeNodeId) as import('@xyflow/react').Node<import('../../types/workflow').WorkflowNodeData> | null;
  const nodeExecution = useNodeExecution(activeNodeId);

  const nodeActions = useNodeActions();
  const [testSource, setTestSource] = useState<TestSource>('last_run');
  const [testInput, setTestInput] = useState('{\n  \n}');
  const [testMessage, setTestMessage] = useState<string | null>(null);
  const [isTesting, setIsTesting] = useState(false);

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

  // Test step: run only this node (saves the draft first; the engine reads the saved draft)
  const handleTestStep = useCallback(async () => {
    if (!activeNodeId || isTesting) return;
    setTestMessage(null);
    let items: Array<Record<string, unknown>> | undefined;
    if (testSource === 'input') {
      try {
        const parsed: unknown = JSON.parse(testInput);
        const list = Array.isArray(parsed) ? parsed : [parsed];
        if (!list.every((i) => i && typeof i === 'object' && !Array.isArray(i))) throw new Error('Each item must be a JSON object');
        items = list as Array<Record<string, unknown>>;
      } catch (e) {
        setTestMessage(`Typed input is not valid JSON: ${(e as Error).message}`);
        return;
      }
    }
    setIsTesting(true);
    try {
      const res = await testStep(activeNodeId, testSource, items, nodeActions.saveNew);
      if (!res.ok) setTestMessage(res.message);
      else if (res.failed) setTestMessage(res.firstError ?? 'The node failed');
    } catch (e) {
      setTestMessage(describeActionError(e).message);
    } finally {
      setIsTesting(false);
    }
  }, [activeNodeId, isTesting, testSource, testInput, nodeActions]);

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

  // Escape while editing the name cancels the edit instead of closing the dialog (Base UI:
  // the close request carries its reason and can be cancelled).
  const handleOpenChange = useCallback(
    (open: boolean, details?: { reason?: string; cancel?: () => void }) => {
      if (open) return;
      if (details?.reason === 'escape-key' && isEditingName) {
        details.cancel?.();
        handleCancelEdit();
        return;
      }
      closeNDV();
    },
    [isEditingName, handleCancelEdit, closeNDV],
  );

  if (!activeNode) return null;
  const nodeStyles = getNodeStyles(normalizeNodeGroup(activeNode.data.group ? [activeNode.data.group] : undefined));

  return (
    <Dialog open={isOpen} onOpenChange={handleOpenChange}>
        <DialogContent showCloseButton={false} className="!max-w-[90vw] !w-[90vw] !h-[85vh] !p-0 !gap-0 flex flex-col" data-testid="ndv-modal">
          {/* Accessible title (visually hidden — header is custom) */}
          <VisuallyHidden>
            <DialogTitle>{activeNode.data.label} - Node Details</DialogTitle>
          </VisuallyHidden>

          {/* Header: node identity + controls */}
          <div className="flex items-center justify-between px-3 h-12 shrink-0 shadow-[inset_0_-1px_0_var(--hairline)]">
            {/* Left: Back button */}
            <Button variant="ghost" size="sm" onClick={closeNDV} className="text-muted-foreground hover:text-foreground" data-testid="ndv-back">
              <ArrowLeft />
              <span className="hidden sm:inline">Back</span>
            </Button>

            {/* Center: Node icon + name + status */}
            <div className="flex items-center gap-2 min-w-0 flex-1 justify-center">
              {/* Node icon */}
              <span className="icon-tile" style={{ color: nodeStyles.accentColor }} aria-hidden>
                <IconComponent size={14} />
              </span>

              {/* Editable node name */}
              {isEditingName ? (
                <div className="flex items-center gap-1">
                  <Input
                    ref={handleNameInputRef}
                    type="text"
                    value={editedName}
                    onChange={(e) => setEditedName(e.target.value)}
                    onKeyDown={handleNameKeyDown}
                    onBlur={handleSaveName}
                    className="h-7 min-w-[120px] max-w-[200px] text-[13px] font-semibold"
                    data-testid="ndv-name-input"
                  />
                  <Button variant="ghost" size="icon-xs" onClick={handleSaveName} title="Save" aria-label="Save name" className="text-primary" data-testid="ndv-name-save">
                    <Check />
                  </Button>
                  <Button variant="ghost" size="icon-xs" onClick={handleCancelEdit} title="Cancel" aria-label="Cancel rename" className="text-muted-foreground" data-testid="ndv-name-cancel">
                    <X />
                  </Button>
                </div>
              ) : (
                <div className="flex items-center gap-1 group min-w-0">
                  <span className="text-[13.5px] font-semibold tracking-[-0.01em] text-foreground truncate max-w-[200px]">
                    {activeNode.data.label}
                  </span>
                  <Button
                    variant="ghost"
                    size="icon-xs"
                    onClick={() => setIsEditingName(true)}
                    className="text-muted-foreground opacity-0 group-hover:opacity-100 focus-visible:opacity-100 transition-opacity"
                    title="Rename node"
                    aria-label="Rename node"
                    data-testid="ndv-rename"
                  >
                    <Pencil />
                  </Button>
                </div>
              )}

              {/* Status badge */}
              {nodeExecution?.status === 'running' && <Badge variant="warning" data-testid="ndv-status">Running</Badge>}
              {nodeExecution?.status === 'success' && <Badge variant="success" data-testid="ndv-status">Success</Badge>}
              {nodeExecution?.status === 'error' && <Badge variant="error" data-testid="ndv-status">Error</Badge>}
            </div>

            {/* Right: Action buttons */}
            <div className="flex items-center gap-1">
              <NodeAIMenu
                nodeName={activeNode.data.name}
                nodeLabel={activeNode.data.label}
                nodeType={activeNode.data.type}
                disabled={!workflowId}
                onChoose={closeNDV}
              />
              <Button size="sm" onClick={handleTestStep} disabled={isTesting} data-testid="ndv-test-step">
                {isTesting ? (
                  <>
                    <Loader2 className="animate-spin" />
                    <span className="hidden sm:inline">Running...</span>
                  </>
                ) : (
                  <>
                    <FlaskConical />
                    <span className="hidden sm:inline">Test step</span>
                  </>
                )}
              </Button>
              <Button
                variant="ghost"
                size="icon-sm"
                onClick={handleDelete}
                className="text-muted-foreground hover:bg-destructive-soft hover:text-destructive"
                title="Delete node"
                aria-label="Delete node"
                data-testid="ndv-delete"
              >
                <Trash2 />
              </Button>
              <Button variant="ghost" size="icon-sm" onClick={closeNDV} className="text-muted-foreground" title="Close" aria-label="Close" data-testid="ndv-close">
                <X />
              </Button>
            </div>
          </div>

          {/* Test step source: where this node's input comes from */}
          <div className="shrink-0 px-3 py-1.5 shadow-[inset_0_-1px_0_var(--hairline)]" data-testid="ndv-test-source">
            <div className="flex items-center gap-2">
              <span className="text-[12px] text-muted-foreground">Test with</span>
              <Segmented
                size="xs"
                value={testSource}
                onChange={(v) => { setTestSource(v); setTestMessage(null); }}
                aria-label="Test step input source"
                data-testid="ndv-test-source-switch"
                options={[
                  { key: 'input', label: 'Typed input', testId: 'ndv-test-source-input' },
                  { key: 'last_run', label: 'Last run', testId: 'ndv-test-source-last-run' },
                  { key: 'pinned', label: 'Pinned', testId: 'ndv-test-source-pinned' },
                ]}
              />
            </div>
            {testSource === 'input' && (
              <div className="mt-1.5 rounded-ctl overflow-hidden">
                <CodeEditor
                  value={testInput}
                  onChange={setTestInput}
                  language="json"
                  minHeight="56px"
                  maxHeight="140px"
                  testId="ndv-test-input-editor"
                  placeholder='{ "field": "value" }'
                />
              </div>
            )}
            {testMessage && (
              <Callout tone="error" className="mt-1.5" role="alert" data-testid="ndv-test-error">
                {testMessage}
              </Callout>
            )}
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

              <PanelResizeHandle className="w-px bg-hairline hover:bg-primary/50 active:bg-primary transition-colors" />

              {/* Settings Panel */}
              <Panel defaultSize={100 - inputPanelSize - outputPanelSize} minSize={30}>
                <NodeSettings
                  node={activeNode}
                />
              </Panel>

              <PanelResizeHandle className="w-px bg-hairline hover:bg-primary/50 active:bg-primary transition-colors" />

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
    </Dialog>
  );
}
