import {
  MoreHorizontal,
  Play,
  Loader2,
} from 'lucide-react';

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

import { WorkflowThumbnail } from './WorkflowThumbnail';
import type { WorkflowWithDefinition } from '../hooks/useWorkflows';
import type { LatestExecution } from '../hooks/useLatestExecutions';
import { useWorkflowActions } from '../hooks/useWorkflowActions';
import { formatDate } from '../lib/formatDate';

interface WorkflowCardProps {
  workflow: WorkflowWithDefinition;
  lastRun?: LatestExecution;
}

export function WorkflowCard({ workflow, lastRun }: WorkflowCardProps) {
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
        className="group cursor-pointer rounded-xl bg-card hover:shadow-md hover:-translate-y-0.5 transition-all duration-200"
        onClick={handleOpen}
      >
        {/* Thumbnail */}
        <div className="relative overflow-hidden rounded-t-xl">
          <WorkflowThumbnail
            definition={workflow.definition}
            className="aspect-[16/10] w-full bg-muted/20"
          />
          {/* Hover actions overlay */}
          <div className="absolute top-2.5 right-2.5 opacity-0 group-hover:opacity-100 transition-opacity duration-200">
            <DropdownMenu>
              <DropdownMenuTrigger onClick={(e) => e.stopPropagation()}>
                <Button
                  variant="secondary"
                  size="icon-sm"
                  className="h-7 w-7 rounded-full shadow-sm"
                >
                  <MoreHorizontal className="h-3.5 w-3.5" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent
                align="end"
                onClick={(e) => e.stopPropagation()}
              >
                <DropdownMenuItem onClick={handleOpen}>
                  Open in Editor
                </DropdownMenuItem>
                <DropdownMenuItem onClick={handleRun} disabled={isRunning}>
                  {isRunning ? 'Running...' : 'Run Workflow'}
                </DropdownMenuItem>
                <DropdownMenuSeparator />
                <DropdownMenuItem
                  onClick={handleDuplicate}
                  disabled={isDuplicating}
                >
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

        {/* Info */}
        <div className="px-4 py-4">
          <div className="flex items-center justify-between gap-2 mb-1.5">
            <h3 className="font-medium text-[13px] leading-snug truncate flex-1">
              {workflow.name}
            </h3>
            <Badge
              variant={workflow.active ? 'success' : 'glass'}
              className="shrink-0 text-[10px]"
            >
              {workflow.active ? 'Active' : 'Inactive'}
            </Badge>
          </div>

          <div className="flex items-center justify-between">
            <div className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
              <span className="px-2 py-0.5 rounded-full bg-muted">
                {workflow.nodeCount} nodes
              </span>
              <span className="px-2 py-0.5 rounded-full bg-muted">
                {formatDate(workflow.updatedAt)}
              </span>
            </div>
            <Button
              variant="ghost"
              size="icon-sm"
              className="h-6 w-6 rounded-md opacity-0 group-hover:opacity-100 transition-all duration-200 hover:bg-emerald-500/10 hover:text-emerald-600 dark:hover:text-emerald-400"
              onClick={(e) => {
                e.stopPropagation();
                handleRun(e);
              }}
              disabled={isRunning}
            >
              {isRunning ? (
                <Loader2 className="h-3 w-3 animate-spin" />
              ) : (
                <Play className="h-3 w-3" />
              )}
            </Button>
          </div>
        </div>
      </div>

      <AlertDialog open={deleteDialogOpen} onOpenChange={setDeleteDialogOpen}>
        <AlertDialogContent onClick={(e) => e.stopPropagation()}>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete workflow</AlertDialogTitle>
            <AlertDialogDescription>
              Are you sure you want to delete &quot;{workflow.name}&quot;? This
              action cannot be undone.
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
