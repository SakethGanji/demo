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
  ChevronLeft,
  Blocks,
  ScrollText,
  MousePointer2,
  Hand,
  CheckCircle2,
  XCircle,
  Clock,
} from 'lucide-react';
import { Link } from '@tanstack/react-router';
import { useReactFlow, useViewport } from '@xyflow/react';
import { useWorkflowStore } from '../../stores/workflowStore';
import { useEditorLayoutStore } from '../../stores/editorLayoutStore';
import { useExecutionStream } from '../../hooks/useExecutionStream';
import { useExecSummary } from '../../hooks/useWorkflowSelectors';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/shared/components/ui/dropdown-menu';
import { useSaveWorkflow, usePublishWorkflow, useImportWorkflow } from '../../hooks/useWorkflowApi';
import { toBackendWorkflow } from '../../lib/workflowTransform';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/shared/components/ui/tooltip';
import { ToolbarGroup, ToolbarSeparator } from '@/shared/components/ui/toolbar';
import type { WorkflowNodeData } from '../../types/workflow';
import type { Node } from '@xyflow/react';
export default function WorkflowNavbar() {
  const workflowName = useWorkflowStore((s) => s.workflowName);
  const workflowId = useWorkflowStore((s) => s.workflowId);
  const isActive = useWorkflowStore((s) => s.isActive);
  const setWorkflowName = useWorkflowStore((s) => s.setWorkflowName);
  const undo = useWorkflowStore((s) => s.undo);
  const redo = useWorkflowStore((s) => s.redo);
  const _canUndo = useWorkflowStore((s) => s._canUndo);
  const _canRedo = useWorkflowStore((s) => s._canRedo);
  const _isDirty = useWorkflowStore((s) => s._isDirty);

  const { saveWorkflow, isSaving } = useSaveWorkflow();
  const { publish, unpublish, isPublishing } = usePublishWorkflow();
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
  const { zoom } = useViewport();
  const nodeCount = useWorkflowStore((s) => s.nodeCount);
  const edgeCount = useWorkflowStore((s) => s.edgeCount);
  const isRunning = useWorkflowStore((s) => s.isAnyNodeRunning);
  const execSummary = useExecSummary();
  const { hasErrors, hasLogs, totalDuration } = execSummary;
  const allSuccess = hasLogs && !isRunning && !hasErrors;

  const [isEditingName, setIsEditingName] = useState(false);
  const [editedName, setEditedName] = useState(workflowName);
  const nameInputRef = useRef<HTMLInputElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

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

  const isBottomTabActive = (tab: string) => bottomPanelOpen && bottomPanelTab === tab;
  const isRightTabActive = (tab: string) => rightPanelOpen && rightPanelTab === tab;

  return (
    <>
      <div className="flex items-center h-11 px-3 bg-[var(--surface)]/80 backdrop-blur-xl rounded-xl shadow-lg border border-border/30 gap-1">
        {/* Left section: Back + Name */}
        <Link
          to="/projects"
          className="h-8 px-1.5 flex items-center gap-1 rounded-md text-muted-foreground hover:text-foreground hover:bg-accent transition-colors text-xs"
          title="Back to projects"
        >
          <ChevronLeft size={14} />
          <span className="hidden sm:inline">Projects</span>
        </Link>

        <ToolbarSeparator />

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

        <ToolbarGroup>
          <button onClick={() => undo()} disabled={!_canUndo} className={btnClass} title="Undo">
            <Undo2 size={14} />
          </button>
          <button onClick={() => redo()} disabled={!_canRedo} className={btnClass} title="Redo">
            <Redo2 size={14} />
          </button>
        </ToolbarGroup>

        <ToolbarSeparator />

        <ToolbarGroup>
          <button
            onClick={toggleCanvasMode}
            className={btnClass + (canvasMode === 'pointer' ? ' !text-primary' : '')}
            title={canvasMode === 'pointer' ? 'Switch to hand (pan) mode' : 'Switch to pointer (select) mode'}
          >
            {canvasMode === 'pointer' ? <MousePointer2 size={14} /> : <Hand size={14} />}
          </button>
        </ToolbarGroup>

        <ToolbarSeparator />

        <ToolbarGroup>
          <button onClick={() => zoomOut()} className={btnClass} title="Zoom out">
            <ZoomOut size={14} />
          </button>
          <button onClick={() => zoomIn()} className={btnClass} title="Zoom in">
            <ZoomIn size={14} />
          </button>
        </ToolbarGroup>

        {/* Right section */}
        <div className="flex-1" />

        {/* Bottom panel tabs */}
        <ToolbarGroup>
          <button
            onClick={() => openBottomPanel('logs')}
            className={btnClass + (isBottomTabActive('logs') ? ' !text-primary' : '')}
            title="Toggle logs"
          >
            <ScrollText size={14} />
          </button>
        </ToolbarGroup>

        <ToolbarSeparator />

        {/* Right panel tabs */}
        <ToolbarGroup>
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
        </ToolbarGroup>

        <ToolbarSeparator />

        {/* Add node */}
        <button
          onClick={() => ensureRightPanelOpen('nodes')}
          className={btnClass + ' !text-primary'}
          title="Add node"
        >
          <Plus size={16} strokeWidth={2.5} />
        </button>

        <ToolbarSeparator />

        {/* Publish / Unpublish */}
        {isActive ? (
          <Tooltip>
            <TooltipTrigger>
              <button
                onClick={() => unpublish()}
                disabled={isPublishing || !workflowId}
                className="h-7 px-2.5 flex items-center gap-1.5 rounded-md text-xs font-medium bg-[var(--success)]/15 text-[var(--success)] hover:bg-[var(--success)]/25 transition-colors disabled:opacity-40"
              >
                {isPublishing ? <Loader2 size={12} className="animate-spin" /> : <div className="w-1.5 h-1.5 rounded-full bg-[var(--success)]" />}
                <span>Live</span>
              </button>
            </TooltipTrigger>
            <TooltipContent>Click to unpublish</TooltipContent>
          </Tooltip>
        ) : (
          <Tooltip>
            <TooltipTrigger>
              <button
                onClick={() => publish()}
                disabled={isPublishing || !workflowId}
                className="h-7 px-2.5 flex items-center gap-1.5 rounded-md text-xs font-medium text-muted-foreground hover:text-foreground hover:bg-accent transition-colors disabled:opacity-40"
              >
                {isPublishing ? <Loader2 size={12} className="animate-spin" /> : <div className="w-1.5 h-1.5 rounded-full bg-muted-foreground/50" />}
                <span>Publish</span>
              </button>
            </TooltipTrigger>
            <TooltipContent>Publish a new version and enable triggers</TooltipContent>
          </Tooltip>
        )}

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
          <button
            onClick={() => {
              const raw = useEditorLayoutStore.getState().payloadInput;
              try {
                executeWorkflow(JSON.parse(raw));
              } catch {
                executeWorkflow({});
              }
            }}
            className={btnClass + ' !text-[var(--success)]'}
            title="Run workflow"
          >
            <Play size={16} fill="currentColor" />
          </button>
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

        <ToolbarSeparator />

        {/* Status info */}
        <span className="text-[11px] text-muted-foreground select-none whitespace-nowrap">
          {nodeCount} nodes · {edgeCount} edges
        </span>
        {isRunning && (
          <span className="flex items-center gap-1 text-[11px] text-[var(--warning)]">
            <Loader2 size={11} className="animate-spin" />
            Running
          </span>
        )}
        {!isRunning && hasErrors && (
          <span className="flex items-center gap-1 text-[11px] text-destructive">
            <XCircle size={11} />
            Failed
          </span>
        )}
        {allSuccess && (
          <span className="flex items-center gap-1 text-[11px] text-[var(--success)]">
            <CheckCircle2 size={11} />
            <Clock size={10} />
            {totalDuration}ms
          </span>
        )}
        <span className="text-[11px] text-muted-foreground select-none">{Math.round(zoom * 100)}%</span>

        {/* More options */}
        <DropdownMenu>
          <DropdownMenuTrigger className={btnClass}>
            <MoreHorizontal size={14} />
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

    </>
  );
}
