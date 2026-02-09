import {
  MoreHorizontal,
  Calendar,
  GitBranch,
  CheckCircle2,
  XCircle,
} from 'lucide-react';

import { Card } from '@/shared/components/ui/card';
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
import { useWorkflowActions, formatDate } from '../hooks/useWorkflowActions';

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
      <Card
        className="group cursor-pointer transition-all duration-200 hover:shadow-lg hover:border-primary/20 hover:-translate-y-0.5 overflow-hidden"
        onClick={handleOpen}
      >
        <WorkflowThumbnail
          definition={workflow.definition}
          className="h-40 w-full bg-muted/30"
        />

        <div className="flex items-center justify-between gap-2 px-3.5 pt-3">
          <h3 className="font-semibold text-[13px] truncate">{workflow.name}</h3>
          <Badge variant={workflow.active ? 'success' : 'glass'} className="shrink-0">
            {workflow.active ? 'Active' : 'Inactive'}
          </Badge>
        </div>

        <div className="flex items-center justify-between px-3.5 pt-1.5 pb-3">
          <div className="flex items-center gap-3">
            <span className="flex items-center gap-1 text-[11px] text-muted-foreground">
              <GitBranch className="h-3 w-3" />
              {workflow.nodeCount} nodes
            </span>
            <span className="flex items-center gap-1 text-[11px] text-muted-foreground">
              <Calendar className="h-3 w-3" />
              {formatDate(workflow.updatedAt)}
            </span>
            {lastRun && (
              <span className="flex items-center gap-1 text-[11px]">
                {lastRun.status === 'success' ? (
                  <CheckCircle2 className="h-3 w-3 text-emerald-500" />
                ) : (
                  <XCircle className="h-3 w-3 text-destructive" />
                )}
              </span>
            )}
          </div>
          <DropdownMenu>
            <DropdownMenuTrigger asChild onClick={(e) => e.stopPropagation()}>
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
      </Card>

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
