import { useState } from 'react';
import { useNavigate } from '@tanstack/react-router';
import { useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { workflowsApi } from '@/shared/lib/api';
import type { WorkflowWithDefinition } from './useWorkflows';

export function useWorkflowActions(workflow: WorkflowWithDefinition) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [isRunning, setIsRunning] = useState(false);
  const [isDeleting, setIsDeleting] = useState(false);
  const [isDuplicating, setIsDuplicating] = useState(false);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);

  const handleOpen = () => {
    navigate({ to: '/editor', search: { workflowId: workflow.id } });
  };

  const handleRun = async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (isRunning) return;

    setIsRunning(true);
    try {
      const result = await workflowsApi.run(workflow.id);
      if (result.status === 'success') {
        toast.success('Workflow executed', {
          description: `Execution ID: ${result.execution_id}`,
        });
      } else {
        toast.error('Workflow failed', {
          description: result.errors?.[0]?.error || 'Unknown error',
        });
      }
    } catch (error) {
      toast.error('Failed to run workflow', {
        description: error instanceof Error ? error.message : 'Unknown error',
      });
    } finally {
      setIsRunning(false);
    }
  };

  const handleDuplicate = async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (isDuplicating) return;

    setIsDuplicating(true);
    try {
      const duplicateWorkflow = {
        name: `${workflow.name} (copy)`,
        nodes: workflow.definition.nodes.map((node) => ({
          name: node.name,
          type: node.type,
          parameters: node.parameters,
          position: node.position,
        })),
        connections: workflow.definition.connections.map((conn) => ({
          source_node: conn.sourceNode,
          source_output: conn.sourceOutput,
          target_node: conn.targetNode,
          target_input: conn.targetInput,
        })),
      };
      await workflowsApi.create(duplicateWorkflow);
      queryClient.invalidateQueries({ queryKey: ['workflows'] });
      toast.success('Workflow duplicated', {
        description: `"${duplicateWorkflow.name}" has been created.`,
      });
    } catch (error) {
      toast.error('Failed to duplicate workflow', {
        description: error instanceof Error ? error.message : 'Unknown error',
      });
    } finally {
      setIsDuplicating(false);
    }
  };

  const handleDelete = async () => {
    if (isDeleting) return;

    setIsDeleting(true);
    try {
      await workflowsApi.delete(workflow.id);
      queryClient.invalidateQueries({ queryKey: ['workflows'] });
      setDeleteDialogOpen(false);
      toast.success('Workflow deleted', {
        description: `"${workflow.name}" has been deleted.`,
      });
    } catch (error) {
      toast.error('Failed to delete workflow', {
        description: error instanceof Error ? error.message : 'Unknown error',
      });
    } finally {
      setIsDeleting(false);
    }
  };

  return {
    isRunning,
    isDeleting,
    isDuplicating,
    deleteDialogOpen,
    setDeleteDialogOpen,
    handleOpen,
    handleRun,
    handleDuplicate,
    handleDelete,
  };
}
