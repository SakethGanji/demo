import { useState, useRef, useEffect } from 'react';
import {
  Save,
  MoreHorizontal,
  Copy,
  Download,
  Upload,
  Trash2,
  Loader2,
  Play,
  Undo2,
  Redo2,
  ZoomIn,
  ZoomOut,
  Plus,
  Square,
  Sparkles,
  GitBranch,
  ChevronLeft,
  Blocks,
  ScrollText,
  Monitor,
  MousePointer2,
  Hand,
} from 'lucide-react';
import { Link } from '@tanstack/react-router';
import { useReactFlow } from 'reactflow';
import { useWorkflowStore } from '../../stores/workflowStore';
import { useEditorLayoutStore } from '../../stores/editorLayoutStore';
import { useExecutionStream } from '../../hooks/useExecutionStream';
import { Popover, PopoverContent, PopoverTrigger } from '@/shared/components/ui/popover';
import CodeEditor from '@/shared/components/ui/code-editor';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/shared/components/ui/dropdown-menu';
import { useSaveWorkflow, useToggleWorkflowActive, useImportWorkflow } from '../../hooks/useWorkflowApi';
import { toBackendWorkflow } from '../../lib/workflowTransform';
import { Switch } from '@/shared/components/ui/switch';
import type { WorkflowNodeData } from '../../types/workflow';
import type { Node } from 'reactflow';
import WorkflowPickerDialog from './WorkflowPickerDialog';

export default function WorkflowNavbar() {
  const workflowName = useWorkflowStore((s) => s.workflowName);
  const workflowId = useWorkflowStore((s) => s.workflowId);
  const isActive = useWorkflowStore((s) => s.isActive);
  const setWorkflowName = useWorkflowStore((s) => s.setWorkflowName);
  const undo = useWorkflowStore((s) => s.undo);
  const redo = useWorkflowStore((s) => s.redo);
  const _canUndo = useWorkflowStore((s) => s._canUndo);
  const _canRedo = useWorkflowStore((s) => s._canRedo);
  const addSubworkflowNode = useWorkflowStore((s) => s.addSubworkflowNode);
  const copyWorkflowNodes = useWorkflowStore((s) => s.copyWorkflowNodes);
  const _isDirty = useWorkflowStore((s) => s._isDirty);

  const { saveWorkflow, isSaving } = useSaveWorkflow();
  const { toggleActive, isToggling } = useToggleWorkflowActive();
  const { importWorkflow } = useImportWorkflow();
  const { executeWorkflow, isExecuting, cancelExecution } = useExecutionStream();

  const rightPanelOpen = useEditorLayoutStore((s) => s.rightPanelOpen);
  const rightPanelTab = useEditorLayoutStore((s) => s.rightPanelTab);
  const openRightPanel = useEditorLayoutStore((s) => s.openRightPanel);
  const bottomPanelOpen = useEditorLayoutStore((s) => s.bottomPanelOpen);
  const bottomPanelTab = useEditorLayoutStore((s) => s.bottomPanelTab);
  const openBottomPanel = useEditorLayoutStore((s) => s.openBottomPanel);

  const ensureRightPanelOpen = useEditorLayoutStore((s) => s.ensureRightPanelOpen);
  const canvasMode = useEditorLayoutStore((s) => s.canvasMode);
  const toggleCanvasMode = useEditorLayoutStore((s) => s.toggleCanvasMode);

  const { zoomIn, zoomOut } = useReactFlow();

  const [isWorkflowPickerOpen, setIsWorkflowPickerOpen] = useState(false);
  const [isEditingName, setIsEditingName] = useState(false);
  const [editedName, setEditedName] = useState(workflowName);
  const [isRunOpen, setIsRunOpen] = useState(false);
  const [testInput, setTestInput] = useState(`{
  "message": "Hello world",
  "count": 42
}`);
  const nameInputRef = useRef<HTMLInputElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleRunWithPayload = () => {
    try {
      const parsed = JSON.parse(testInput);
      setIsRunOpen(false);
      executeWorkflow(parsed);
    } catch {
      setIsRunOpen(false);
      executeWorkflow({});
    }
  };

  const handleRunWithoutPayload = () => {
    setIsRunOpen(false);
    executeWorkflow({});
  };

  useEffect(() => {
    if (isEditingName && nameInputRef.current) {
      nameInputRef.current.focus();
      nameInputRef.current.select();
    }
  }, [isEditingName]);

  const handleNameSubmit = () => {
    if (editedName.trim()) {
      setWorkflowName(editedName.trim());
    } else {
      setEditedName(workflowName);
    }
    setIsEditingName(false);
  };

  const handleImport = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;

    const reader = new FileReader();
    reader.onload = (e) => {
      const content = e.target?.result as string;
      importWorkflow(content);
    };
    reader.readAsText(file);
    event.target.value = '';
  };

  const btnClass = "h-8 w-8 flex items-center justify-center rounded-md text-muted-foreground hover:text-foreground hover:bg-accent transition-colors disabled:opacity-40 disabled:cursor-not-allowed";
  const dividerClass = "w-px h-4 bg-border";

  const isBottomTabActive = (tab: string) => bottomPanelOpen && bottomPanelTab === tab;
  const isRightTabActive = (tab: string) => rightPanelOpen && rightPanelTab === tab;

  return (
    <>
      <div className="editor-chrome flex items-center h-11 px-3 bg-card border-b border-border shrink-0 gap-1">
        {/* Left section: Back + Name */}
        <Link
          to="/workflows"
          className="h-8 px-1.5 flex items-center gap-1 rounded-md text-muted-foreground hover:text-foreground hover:bg-accent transition-colors text-xs"
          title="Back to workflows"
        >
          <ChevronLeft size={14} />
          <span className="hidden sm:inline">Workflows</span>
        </Link>

        <div className={dividerClass} />

        {/* Workflow name */}
        {isEditingName ? (
          <input
            ref={nameInputRef}
            type="text"
            value={editedName}
            onChange={(e) => setEditedName(e.target.value)}
            onBlur={handleNameSubmit}
            onKeyDown={(e) => {
              if (e.key === 'Enter') handleNameSubmit();
              if (e.key === 'Escape') {
                setEditedName(workflowName);
                setIsEditingName(false);
              }
            }}
            className="h-8 w-36 px-2 text-[13px] font-medium text-foreground bg-transparent outline-none border border-border rounded-md"
          />
        ) : (
          <button
            onClick={() => {
              setEditedName(workflowName);
              setIsEditingName(true);
            }}
            className="h-8 px-2 text-[13px] font-medium text-foreground hover:bg-accent transition-colors rounded-md truncate max-w-40"
            title="Click to rename"
          >
            {workflowName}
          </button>
        )}
        {_isDirty && (
          <span
            className="w-1.5 h-1.5 rounded-full bg-[var(--warning)] flex-shrink-0"
            title="Unsaved changes"
          />
        )}

        {/* Center section: Undo/Redo + Zoom */}
        <div className="flex-1" />

        <div className="flex bg-muted/50 rounded-md px-0.5 py-0.5">
          <button onClick={() => undo()} disabled={!_canUndo} className={btnClass} title="Undo">
            <Undo2 size={14} />
          </button>
          <button onClick={() => redo()} disabled={!_canRedo} className={btnClass} title="Redo">
            <Redo2 size={14} />
          </button>
        </div>

        <div className={dividerClass} />

        <div className="flex bg-muted/50 rounded-md px-0.5 py-0.5">
          <button
            onClick={toggleCanvasMode}
            className={btnClass + (canvasMode === 'pointer' ? ' !text-primary' : '')}
            title={canvasMode === 'pointer' ? 'Switch to hand (pan) mode' : 'Switch to pointer (select) mode'}
          >
            {canvasMode === 'pointer' ? <MousePointer2 size={14} /> : <Hand size={14} />}
          </button>
        </div>

        <div className={dividerClass} />

        <div className="flex bg-muted/50 rounded-md px-0.5 py-0.5">
          <button onClick={() => zoomOut()} className={btnClass} title="Zoom out">
            <ZoomOut size={14} />
          </button>
          <button onClick={() => zoomIn()} className={btnClass} title="Zoom in">
            <ZoomIn size={14} />
          </button>
        </div>

        {/* Right section */}
        <div className="flex-1" />

        {/* Bottom panel tabs */}
        <div className="flex bg-muted/50 rounded-md px-0.5 py-0.5">
          <button
            onClick={() => openBottomPanel('logs')}
            className={btnClass + (isBottomTabActive('logs') ? ' !text-primary' : '')}
            title="Toggle logs"
          >
            <ScrollText size={14} />
          </button>
          <button
            onClick={() => openBottomPanel('ui')}
            className={btnClass + (isBottomTabActive('ui') ? ' !text-primary' : '')}
            title="Toggle UI"
          >
            <Monitor size={14} />
          </button>
        </div>

        <div className={dividerClass} />

        {/* Right panel tabs */}
        <div className="flex bg-muted/50 rounded-md px-0.5 py-0.5">
          <button
            onClick={() => openRightPanel('nodes')}
            className={btnClass + (isRightTabActive('nodes') ? ' !text-primary' : '')}
            title="Toggle node list"
          >
            <Blocks size={14} />
          </button>
          <button
            onClick={() => openRightPanel('ai')}
            className={btnClass + (isRightTabActive('ai') ? ' !text-primary' : '')}
            title="Toggle AI assistant"
          >
            <Sparkles size={14} />
          </button>
        </div>

        <div className={dividerClass} />

        {/* Add node */}
        <button
          onClick={() => ensureRightPanelOpen('nodes')}
          className={btnClass + ' !text-primary'}
          title="Add node"
        >
          <Plus size={16} strokeWidth={2.5} />
        </button>

        <div className={dividerClass} />

        {/* Active toggle */}
        <div className="px-1 flex items-center">
          <Switch
            checked={isActive}
            onCheckedChange={(checked) => toggleActive(checked)}
            disabled={isToggling || !workflowId}
            className="data-[state=checked]:bg-[var(--success)]"
          />
        </div>

        {/* Run/Stop */}
        {isExecuting ? (
          <button
            onClick={cancelExecution}
            className={btnClass + ' !text-destructive'}
            title="Stop"
          >
            <Square size={14} fill="currentColor" />
          </button>
        ) : (
          <Popover open={isRunOpen} onOpenChange={setIsRunOpen}>
            <PopoverTrigger asChild>
              <button
                className={btnClass + ' !text-[var(--success)]'}
                title="Run workflow"
              >
                <Play size={16} fill="currentColor" />
              </button>
            </PopoverTrigger>
            <PopoverContent align="end" className="w-80 p-0 overflow-hidden">
              <button
                onClick={handleRunWithoutPayload}
                className="w-full px-3 py-2 text-[13px] text-left hover:bg-accent flex items-center gap-2 border-b border-border"
              >
                <Play size={12} className="text-[var(--success)]" fill="currentColor" />
                Run without payload
              </button>
              <div className="p-3">
                <div className="text-[11px] font-medium text-muted-foreground mb-2">Or run with payload:</div>
                <div className="rounded-md border border-border overflow-hidden mb-2">
                  <CodeEditor
                    value={testInput}
                    onChange={setTestInput}
                    language="json"
                    height="100px"
                  />
                </div>
                <button
                  onClick={handleRunWithPayload}
                  className="w-full h-7 rounded-md bg-[var(--success)] text-white text-[12px] font-medium hover:brightness-110 flex items-center justify-center gap-1.5"
                >
                  <Play size={11} fill="currentColor" />
                  Run with Payload
                </button>
              </div>
            </PopoverContent>
          </Popover>
        )}

        {/* Save */}
        <button
          onClick={() => saveWorkflow()}
          disabled={isSaving}
          className={btnClass}
          title="Save"
        >
          {isSaving ? <Loader2 size={14} className="animate-spin" /> : <Save size={14} />}
        </button>

        {/* More options */}
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <button className={btnClass}>
              <MoreHorizontal size={14} />
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem>
              <Copy size={14} className="mr-2" />
              Duplicate
            </DropdownMenuItem>
            <DropdownMenuItem onClick={() => {
              const { nodes, edges, workflowName: wfName, workflowId: wfId, isActive: active } = useWorkflowStore.getState();
              const backendWorkflow = toBackendWorkflow(
                nodes as Node<WorkflowNodeData>[],
                edges,
                wfName,
                wfId
              );
              const exportData = {
                name: wfName,
                description: '',
                active,
                nodes: backendWorkflow.nodes,
                connections: backendWorkflow.connections,
              };
              const blob = new Blob([JSON.stringify(exportData, null, 2)], { type: 'application/json' });
              const url = URL.createObjectURL(blob);
              const a = document.createElement('a');
              a.href = url;
              a.download = `${wfName.replace(/\s+/g, '-').toLowerCase()}.json`;
              a.click();
              URL.revokeObjectURL(url);
            }}>
              <Download size={14} className="mr-2" />
              Export
            </DropdownMenuItem>
            <DropdownMenuItem onClick={() => fileInputRef.current?.click()}>
              <Upload size={14} className="mr-2" />
              Import
            </DropdownMenuItem>
            <DropdownMenuItem onClick={() => setIsWorkflowPickerOpen(true)}>
              <GitBranch size={14} className="mr-2" />
              Embed Subworkflow
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem className="text-destructive focus:text-destructive">
              <Trash2 size={14} className="mr-2" />
              Delete
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>

      {/* Hidden file input for import */}
      <input
        ref={fileInputRef}
        type="file"
        accept=".json"
        className="hidden"
        onChange={handleImport}
      />

      {/* Workflow picker dialog for embedding subworkflows */}
      <WorkflowPickerDialog
        open={isWorkflowPickerOpen}
        onClose={() => setIsWorkflowPickerOpen(false)}
        onEmbed={(id, name) => addSubworkflowNode(id, name)}
        onCopy={(name, definition) => copyWorkflowNodes(name, definition)}
        currentWorkflowId={workflowId}
      />
    </>
  );
}
