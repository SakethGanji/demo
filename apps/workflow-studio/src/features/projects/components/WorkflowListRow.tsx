import { MoreHorizontal, Play, GitBranch, Loader2, CheckCircle2, XCircle } from 'lucide-react';

import { Badge } from '@/shared/components/ui/badge';
import { Button } from '@/shared/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/shared/components/ui/dropdown-menu';
import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogHeader,
  AlertDialogFooter,
  AlertDialogTitle,
  AlertDialogDescription,
  AlertDialogAction,
  AlertDialogCancel,
} from '@/shared/components/ui/alert-dialog';

import type { WorkflowWithDefinition } from '../hooks/useWorkflows';
import type { LatestExecution } from '../hooks/useLatestExecutions';
import { useWorkflowActions } from '../hooks/useWorkflowActions';
import { formatDate } from '../lib/formatDate';

interface WorkflowListRowProps {
  workflow: WorkflowWithDefinition;
  lastRun?: LatestExecution;
}

export function WorkflowListRow({ workflow, lastRun }: WorkflowListRowProps) {
  const {
    isRunning,
    isDeleting,
    isDuplicating,
    deleteDialogOpen,
    setDeleteDialogOpen,
    handleOpen,
    handleRun,
    handleDuplicate,
    handleDelete,
  } = useWorkflowActions(workflow);

  return (
    <>
      <div
        className="group flex items-center gap-4 px-4 py-3 cursor-pointer hover:bg-muted/50 transition-colors"
        onClick={handleOpen}
      >
        <div className="flex-1 min-w-0">
          <span className="text-[13px] font-medium truncate block">{workflow.name}</span>
        </div>

        <Badge variant={workflow.active ? 'success' : 'glass'} className="shrink-0">
          {workflow.active ? 'Active' : 'Inactive'}
        </Badge>

        <span className="flex items-center gap-1 text-[11px] text-muted-foreground shrink-0 w-20">
          <GitBranch className="h-3 w-3" />
          {workflow.nodeCount} nodes
        </span>

        <span className="text-[11px] text-muted-foreground shrink-0 w-24">
          {formatDate(workflow.updatedAt)}
        </span>

        <span className="shrink-0 w-5 flex justify-center">
          {lastRun ? (
            lastRun.status === 'success' ? (
              <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500" />
            ) : (
              <XCircle className="h-3.5 w-3.5 text-destructive" />
            )
          ) : null}
        </span>

        <div className="flex items-center gap-1 shrink-0 opacity-0 group-hover:opacity-100 transition-opacity">
          <Button
            variant="ghost"
            size="icon-sm"
            className="hover:bg-[var(--success)]/10 hover:text-[var(--success)]"
            onClick={handleRun}
            disabled={isRunning}
          >
            {isRunning ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Play className="h-3.5 w-3.5" />
            )}
          </Button>
          <DropdownMenu>
            <DropdownMenuTrigger onClick={(e) => e.stopPropagation()}>
              <Button variant="ghost" size="icon-sm">
                <MoreHorizontal className="h-3.5 w-3.5" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" onClick={(e) => e.stopPropagation()}>
              <DropdownMenuItem onClick={handleOpen}>Open in Editor</DropdownMenuItem>
              <DropdownMenuItem onClick={handleRun} disabled={isRunning}>
                {isRunning ? 'Running...' : 'Run Workflow'}
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem onClick={handleDuplicate} disabled={isDuplicating}>
                {isDuplicating ? 'Duplicating...' : 'Duplicate'}
              </DropdownMenuItem>
              <DropdownMenuItem
                className="text-destructive"
                onClick={(e) => {
                  e.stopPropagation();
                  setDeleteDialogOpen(true);
                }}
              >
                Delete
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </div>

      <AlertDialog open={deleteDialogOpen} onOpenChange={setDeleteDialogOpen}>
        <AlertDialogContent onClick={(e) => e.stopPropagation()}>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete workflow</AlertDialogTitle>
            <AlertDialogDescription>
              Are you sure you want to delete "{workflow.name}"? This action cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={handleDelete} disabled={isDeleting}>
              {isDeleting ? 'Deleting...' : 'Delete'}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
