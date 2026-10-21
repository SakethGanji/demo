import { isBlueprintId } from '@/features/ai-builder/stage/blueprint'
import { useStageStore } from '@/features/ai-builder/stage/stageStore'
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
  Square,
  Sparkles,
  ChevronRight,
  Blocks,
  ScrollText,
  CheckCircle2,
  XCircle,
  Clock,
  History,
  LayoutTemplate,
  Workflow,
} from 'lucide-react';
import { Link } from '@tanstack/react-router';
import { HomeNav } from '@/features/project/components/HomeNav';
import { useWorkflowStore } from '../../stores/workflowStore';
import { useEditorLayoutStore } from '../../stores/editorLayoutStore';
import { useHistoryStore } from '../../stores/historyStore';
import { registerEditorAction } from '../../stores/editorActionsStore';
import { useBackToLive } from '../../hooks/useViewingRun';
import { useExecutionStream } from '../../hooks/useExecutionStream';
import { useExecSummary } from '../../hooks/useWorkflowSelectors';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/shared/components/ui/dropdown-menu';
import { useUsedBy, usedByChipLabel } from '../../hooks/useSubworkflows';
import { UsedByDialog } from '../ndv/subworkflow/UsedByDialog';
import { PublishImpactDialog } from './PublishImpactDialog';
import { hasBreakingImpact, parsePublishImpact, type PublishImpactRow } from '../../lib/subworkflowsApi';
import { useSaveWorkflow, usePublishWorkflow, useImportWorkflow } from '../../hooks/useWorkflowApi';
import { toast } from 'sonner';
import { toBackendWorkflow } from '../../lib/workflowTransform';
import { Tip } from '@/shared/components/ui/tooltip';
import { Button, buttonVariants } from '@/shared/components/ui/button';
import { Badge } from '@/shared/components/ui/badge';
import { Kbd } from '@/shared/components/ui/kbd';
import { StatusDot } from '@/shared/components/ui/status-dot';
import { cn } from '@/shared/lib/utils';
import { useEditorProjectStore } from '../../stores/editorProjectStore';
import { ToolbarSeparator } from '@/shared/components/ui/toolbar';
import { EnvProfileSelector } from '../env-profiles/EnvProfileSelector';
import TestBar from '../tests/TestBar';
import { useInterfaceState } from '@/features/interfaces/hooks/useInterfaceState';
import { navbarLabel } from '@/features/interfaces/lib/interfaceDraft';
import type { WorkflowNodeData } from '../../types/workflow';
import type { Node } from '@xyflow/react';
export default function WorkflowNavbar() {
  const projectSlug = useEditorProjectStore((s) => s.slug);
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

  const historyOpen = useHistoryStore((s) => s.open);
  const setHistoryOpen = useHistoryStore((s) => s.setOpen);

  const interfaceState = useInterfaceState(workflowId);
  // while an AI build is on the canvas, count what the canvas shows, not the (still empty) draft
  const draftNodes = useWorkflowStore((s) => s.nodeCount);
  const draftEdges = useWorkflowStore((s) => s.edgeCount);
  const stageNodes = useStageStore((s) => (s.active ? s.nodes.filter((n) => !isBlueprintId(n.id) && s.marks[n.id] !== 'removed').length : null));
  const stageEdges = useStageStore((s) => (s.active ? s.edges.length : null));
  const nodeCount = stageNodes ?? draftNodes;
  const edgeCount = stageEdges ?? draftEdges;
  const isRunning = useWorkflowStore((s) => s.isAnyNodeRunning);
  const execSummary = useExecSummary();
  const { hasErrors, hasLogs, totalDuration } = execSummary;
  const allSuccess = hasLogs && !isRunning && !hasErrors;

  const [usedByOpen, setUsedByOpen] = useState(false);
  const usedBy = useUsedBy(workflowId && workflowId !== 'new' ? workflowId : undefined);
  const usedByLabel = usedByChipLabel(usedBy.data);
  const [impact, setImpact] = useState<PublishImpactRow[]>([]);
  const [impactOpen, setImpactOpen] = useState(false);
  /** Publish, then ask the author to acknowledge any published parent this version breaks (§12.6). */
  const publishAndReview = async () => {
    const res = await publish();
    const rows = parsePublishImpact(res);
    if (hasBreakingImpact(rows)) {
      setImpact(rows);
      setImpactOpen(true);
    }
  };
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

  const isBottomTabActive = (tab: string) => bottomPanelOpen && bottomPanelTab === tab;
  const isRightTabActive = (tab: string) => rightPanelOpen && rightPanelTab === tab;

  const iconBtn = (active = false) =>
    cn('text-muted-foreground hover:text-foreground', active && 'bg-raise text-foreground shadow-raise-sm hover:bg-raise');

  const backToLive = useBackToLive();
  const runWorkflow = () => {
    // A live run replaces a saved run that was on the canvas.
    if (useWorkflowStore.getState().viewingExecution) backToLive();
    // Show the run's logs (status + output) instead of running into a closed panel.
    openBottomPanel('logs');
    const raw = useEditorLayoutStore.getState().payloadInput;
    if (!raw || !raw.trim()) {
      executeWorkflow({});
      return;
    }
    let parsed: Record<string, unknown>;
    try {
      parsed = JSON.parse(raw);
    } catch (e) {
      toast.error(`Payload JSON invalid: ${(e as Error).message}`);
      return;
    }
    executeWorkflow(parsed);
  };
  const runRef = useRef(runWorkflow);
  runRef.current = runWorkflow;
  const isExecutingRef = useRef(isExecuting);
  isExecutingRef.current = isExecuting;

  // The ⌘K palette's "Run workflow" uses the same path as the Test run button.
  useEffect(
    () =>
      registerEditorAction('run', () => {
        if (!isExecutingRef.current) runRef.current();
      }),
    [],
  );

  // ⌘↵ / Ctrl+↵: test run
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Enter' || !(e.metaKey || e.ctrlKey)) return;
      if (isExecutingRef.current) return;
      e.preventDefault();
      runRef.current();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  const isMac = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform);

  return (
    <>
      <div className="glass-chrome flex h-11 items-center gap-1.5 rounded-panel pr-2 pl-1.5" data-testid="workflow-navbar">
        {/* Home + Jump (the same on every project page), then the crumb: Workflows / name */}
        <HomeNav />
        <span className="mx-0.5 h-4 w-px shrink-0 bg-hairline" aria-hidden />
        <nav className="flex min-w-0 items-center gap-1.5 text-[13px]" aria-label="Breadcrumb">
          <Link
            to="/p/$project"
            params={{ project: projectSlug }}
            className="text-muted-foreground transition-colors hover:text-foreground"
            title="Back to the project's workflows (on the Overview)"
            data-testid="editor-back"
          >
            Workflows
          </Link>
          <ChevronRight size={13} className="shrink-0 text-faint" aria-hidden />
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
              className="field h-7 w-44 px-2 text-[13.5px] font-semibold text-foreground"
              data-testid="editor-name-input"
            />
          ) : (
            <Tip label="Click to rename">
              <button
                onClick={() => {
                  setEditedName(workflowName);
                  setIsEditingName(true);
                }}
                className="h-7 max-w-52 truncate rounded-ctl px-2 text-[13.5px] font-semibold tracking-[-0.01em] text-foreground transition-colors hover:bg-(--hover-fill)"
                data-testid="editor-name"
              >
                {workflowName}
              </button>
            </Tip>
          )}
        </nav>
        {_isDirty && (
          <Tip label="Unsaved changes">
            <span className="size-1.5 shrink-0 rounded-full bg-warning" data-testid="editor-dirty" />
          </Tip>
        )}
        <Badge variant={isActive ? 'success' : 'neutral'} className="gap-1.5" data-testid="editor-status">
          <StatusDot tone={isActive ? 'ok' : 'idle'} flat />
          {isActive ? 'Live' : 'Draft'}
        </Badge>
        {usedByLabel && (
          <Tip label="Workflows that call this one">
            <Button variant="ghost" size="xs" className="shrink-0 whitespace-nowrap text-muted-foreground" onClick={() => setUsedByOpen(true)} data-testid="editor-used-by-chip">
              <Workflow /> {usedByLabel}
            </Button>
          </Tip>
        )}

        <div className="flex-1" />

        <Tip label="Form, chat or custom screen for this workflow">
          <Button
            variant="ghost"
            size="sm"
            className={iconBtn(isRightTabActive('interface'))}
            onClick={() => openRightPanel('interface')}
            aria-pressed={isRightTabActive('interface')}
            data-testid="navbar-tab-interface"
          >
            <LayoutTemplate /> {navbarLabel(interfaceState.data?.interface)}
          </Button>
        </Tip>

        <TestBar />

        {/* Run summary */}
        <span className="hidden text-[11.5px] whitespace-nowrap text-muted-foreground select-none xl:inline">
          {nodeCount} nodes · {edgeCount} edges
        </span>
        {isRunning && (
          <span className="flex items-center gap-1 text-[11.5px] text-warning-text">
            <Loader2 size={11} className="animate-spin" />
            Running
          </span>
        )}
        {!isRunning && hasErrors && (
          <span className="flex items-center gap-1 text-[11.5px] text-destructive">
            <XCircle size={11} />
            Failed
          </span>
        )}
        {allSuccess && (
          <span className="flex items-center gap-1 text-[11.5px] text-success-text">
            <CheckCircle2 size={11} />
            <Clock size={10} />
            {totalDuration}ms
          </span>
        )}
        <span className="mr-1 text-[12px] whitespace-nowrap text-muted-foreground select-none" data-testid="editor-saved">
          {isSaving ? 'Saving…' : _isDirty ? 'Unsaved' : 'Saved'}
        </span>

        <ToolbarSeparator />

        <Tip label="Undo" kbd="⌘Z">
          <Button variant="ghost" size="icon-sm" className={iconBtn()} onClick={() => undo()} disabled={!_canUndo} aria-label="Undo">
            <Undo2 />
          </Button>
        </Tip>
        <Tip label="Redo" kbd="⇧⌘Z">
          <Button variant="ghost" size="icon-sm" className={iconBtn()} onClick={() => redo()} disabled={!_canRedo} aria-label="Redo">
            <Redo2 />
          </Button>
        </Tip>

        <ToolbarSeparator />

        {/* Panels */}
        <Tip label="Version history">
          <Button variant="ghost" size="icon-sm" className={iconBtn(historyOpen)} onClick={() => setHistoryOpen(!historyOpen)} aria-label="Version history" aria-pressed={historyOpen} data-testid="navbar-history">
            <History />
          </Button>
        </Tip>
        <Tip label="Execution logs">
          <Button variant="ghost" size="icon-sm" className={iconBtn(isBottomTabActive('logs'))} onClick={() => openBottomPanel('logs')} aria-label="Toggle logs" aria-pressed={isBottomTabActive('logs')} data-testid="editor-toggle-logs">
            <ScrollText />
          </Button>
        </Tip>
        <Tip label="Node list">
          <Button variant="ghost" size="icon-sm" className={iconBtn(isRightTabActive('nodes'))} onClick={() => openRightPanel('nodes')} aria-label="Toggle node list" aria-pressed={isRightTabActive('nodes')} data-testid="editor-toggle-nodes">
            <Blocks />
          </Button>
        </Tip>
        <Tip label="AI assistant">
          <Button variant="ghost" size="icon-sm" className={iconBtn(isRightTabActive('ai'))} onClick={() => openRightPanel('ai')} aria-label="Toggle AI assistant" aria-pressed={isRightTabActive('ai')} data-testid="editor-toggle-ai">
            <Sparkles />
          </Button>
        </Tip>

        <ToolbarSeparator />

        <EnvProfileSelector />

        <Tip label="Save" kbd="⌘S">
          <Button variant="ghost" size="icon-sm" className={iconBtn()} onClick={() => saveWorkflow()} disabled={isSaving} aria-label="Save" data-testid="editor-save">
            {isSaving ? <Loader2 className="animate-spin" /> : <Save />}
          </Button>
        </Tip>

        {/* Run / Stop */}
        {isExecuting ? (
          <Button variant="destructive" size="sm" onClick={cancelExecution} data-testid="editor-stop">
            <Square fill="currentColor" /> Stop
          </Button>
        ) : (
          <Tip label="Run with the current payload">
            <Button variant="outline" size="sm" onClick={runWorkflow} data-testid="editor-run">
              <Play /> Test run <Kbd>{isMac ? '⌘↵' : 'Ctrl ↵'}</Kbd>
            </Button>
          </Tip>
        )}

        {/* Publish / Unpublish */}
        {isActive ? (
          <Tip label="Take the workflow offline">
            <Button variant="outline" size="sm" onClick={() => unpublish()} disabled={isPublishing || !workflowId} data-testid="editor-unpublish">
              {isPublishing ? <Loader2 className="animate-spin" /> : <Upload />} Unpublish
            </Button>
          </Tip>
        ) : (
          <Tip label="Publish a new version and enable triggers">
            <Button size="sm" onClick={() => void publishAndReview()} disabled={isPublishing || !workflowId} data-testid="editor-publish">
              {isPublishing ? <Loader2 className="animate-spin" /> : <Upload />} Publish
            </Button>
          </Tip>
        )}

        {/* More options */}
        <DropdownMenu>
          <DropdownMenuTrigger
            className={buttonVariants({ variant: 'ghost', size: 'icon-sm', className: iconBtn() })}
            aria-label="More options"
            data-testid="editor-more"
          >
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
            <DropdownMenuItem onClick={() => setUsedByOpen(true)} data-testid="editor-used-by">
              <Workflow size={14} className="mr-2" />
              Used by
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem variant="destructive">
              <Trash2 size={14} className="mr-2" />
              Delete
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>

      <PublishImpactDialog impact={impact} open={impactOpen} onOpenChange={setImpactOpen} />
      <UsedByDialog open={usedByOpen} onOpenChange={setUsedByOpen} workflowId={workflowId ?? null} />

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
