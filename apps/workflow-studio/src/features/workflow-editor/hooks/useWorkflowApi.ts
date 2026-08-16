/**
 * Workflow API Hooks
 *
 * Custom hooks for workflow CRUD and execution operations.
 * Uses fetch-based REST API.
 */

import { useCallback, useRef } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import { workflowsApi } from '@/shared/lib/api';
import { useWorkflowStore } from '../stores/workflowStore';
import type { BackendWorkflow } from '@/shared/lib/backendTypes';
import {
  toBackendWorkflow,
  fromBackendWorkflow,
  findUpstreamNodeName,
  buildNameToIdMap,
} from '../lib/workflowTransform';
import { toast } from 'sonner';
import type { WorkflowNodeData } from '../types/workflow';
import type { Node } from '@xyflow/react';

/**
 * Hook for saving workflows
 */
export function useSaveWorkflow() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const isSavingRef = useRef(false);

  const createMutation = useMutation({
    mutationFn: (workflow: BackendWorkflow) => workflowsApi.create(workflow),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['workflows'] });
    },
  });

  const updateMutation = useMutation({
    mutationFn: ({ id, workflow }: { id: string; workflow: BackendWorkflow }) =>
      workflowsApi.update(id, workflow),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['workflows'] });
    },
  });

  const saveWorkflow = useCallback(async () => {
    // Guard against concurrent saves (e.g. rapid double-click)
    if (isSavingRef.current) return;
    isSavingRef.current = true;

    const { nodes, edges, workflowName, workflowId } = useWorkflowStore.getState();
    const backendWorkflow = toBackendWorkflow(
      nodes as Node<WorkflowNodeData>[],
      edges,
      workflowName,
      workflowId
    );

    try {
      if (workflowId) {
        // Update existing workflow
        const result = await updateMutation.mutateAsync({
          id: workflowId,
          workflow: backendWorkflow,
        });
        useWorkflowStore.getState().markAsSaved();
        toast.success('Workflow saved', {
          description: `"${result.name}" has been updated.`,
        });
        return result;
      } else {
        // Create new workflow
        const result = await createMutation.mutateAsync(backendWorkflow);
        useWorkflowStore.getState().setWorkflowId(result.id);
        // Update URL to include the new workflow ID
        navigate({
          to: '/editor',
          search: { workflowId: result.id },
          replace: true,
        });
        useWorkflowStore.getState().markAsSaved();
        toast.success('Workflow created', {
          description: `"${result.name}" has been saved.`,
        });
        return result;
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Unknown error';
      toast.error('Failed to save workflow', {
        description: message,
      });
      throw error;
    } finally {
      isSavingRef.current = false;
    }
  }, [updateMutation, createMutation, navigate]);

  return {
    saveWorkflow,
    isSaving: createMutation.isPending || updateMutation.isPending,
  };
}

/**
 * Hook for importing workflows from JSON files
 * Uses backend to create and enrich the workflow, then loads it
 */
export function useImportWorkflow() {
  const loadWorkflow = useWorkflowStore((s) => s.loadWorkflow);
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const createMutation = useMutation({
    mutationFn: (workflow: BackendWorkflow) => workflowsApi.create(workflow),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['workflows'] });
    },
  });

  const importWorkflow = async (jsonContent: string): Promise<boolean> => {
    try {
      // Parse the JSON file
      const data = JSON.parse(jsonContent);

      // Validate basic structure
      if (!data.nodes || !Array.isArray(data.nodes)) {
        toast.error('Invalid workflow file', {
          description: 'Missing nodes array',
        });
        return false;
      }

      // Build the backend workflow format
      const backendWorkflow: BackendWorkflow = {
        name: data.name || 'Imported Workflow',
        nodes: data.nodes,
        connections: data.connections || [],
      };

      // Create workflow on backend (this enriches the nodes)
      const created = await createMutation.mutateAsync(backendWorkflow);

      // Fetch the full enriched workflow
      const enriched = await workflowsApi.get(created.id);

      // Transform and load into store
      const transformed = fromBackendWorkflow(enriched);
      loadWorkflow(transformed);

      // Navigate to the editor with the new workflow
      navigate({
        to: '/editor',
        search: { workflowId: created.id },
        replace: true,
      });

      toast.success('Workflow imported', {
        description: `"${created.name}" has been created.`,
      });

      return true;
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Unknown error';
      toast.error('Failed to import workflow', {
        description: message,
      });
      return false;
    }
  };

  return {
    importWorkflow,
    isImporting: createMutation.isPending,
  };
}

/**
 * Hook for executing a workflow
 */
export function useExecuteWorkflow() {
  const runAdhocMutation = useMutation({
    mutationFn: (workflow: BackendWorkflow) => workflowsApi.runAdhoc(workflow),
  });

  const executeWorkflow = async () => {
    const { nodes, edges, workflowName, setNodeExecutionData, clearExecutionData } =
      useWorkflowStore.getState();

    // Clear previous execution data
    clearExecutionData();

    // Mark all workflow nodes as running
    const workflowNodes = nodes.filter((n) => n.type === 'workflowNode');
    workflowNodes.forEach((node) => {
      setNodeExecutionData(node.id, {
        input: null,
        output: null,
        status: 'running',
        startTime: Date.now(),
      });
    });

    try {
      // Always run with current UI state (adhoc) so edits take effect immediately
      const backendWorkflow = toBackendWorkflow(
        nodes as Node<WorkflowNodeData>[],
        edges,
        workflowName
      );
      const result = await runAdhocMutation.mutateAsync(backendWorkflow);

      // Map backend node names to UI node IDs and update execution data
      const nameToId = buildNameToIdMap(workflowNodes as Node<WorkflowNodeData>[]);

      // Update each node's execution data
      if (result.data) {
        Object.entries(result.data).forEach(([nodeName, outputData]) => {
          const nodeId = nameToId.get(nodeName);
          if (nodeId) {
            // Find input data (from previous node)
            const inputNodeName = findUpstreamNodeName(nodeName, nameToId, edges);
            const inputData = inputNodeName ? result.data[inputNodeName] : null;

            const normalizedOutput = normalizeOutputData(outputData);

            useWorkflowStore.getState().setNodeExecutionData(nodeId, {
              input: inputData ? { items: normalizeOutputData(inputData) } : null,
              output: { items: normalizedOutput },
              status: 'success',
              startTime: Date.now(),
              endTime: Date.now(),
            });
          }
        });
      }

      // Handle errors
      if (result.errors && result.errors.length > 0) {
        result.errors.forEach((err) => {
          const nodeId = nameToId.get(err.node_name);
          if (nodeId) {
            useWorkflowStore.getState().setNodeExecutionData(nodeId, {
              input: null,
              output: { items: [], error: err.error },
              status: 'error',
              endTime: Date.now(),
            });
          }
        });

        toast.error('Workflow execution failed', {
          description: result.errors[0]?.error || 'Unknown error',
        });
      } else {
        toast.success('Workflow executed successfully', {
          description: `Execution ID: ${result.execution_id}`,
        });
      }

      return result;
    } catch (error) {
      // Mark all nodes as error
      workflowNodes.forEach((node) => {
        useWorkflowStore.getState().setNodeExecutionData(node.id, {
          input: null,
          output: { items: [], error: error instanceof Error ? error.message : 'Unknown error' },
          status: 'error',
          endTime: Date.now(),
        });
      });

      const message = error instanceof Error ? error.message : 'Unknown error';
      toast.error('Workflow execution failed', {
        description: message,
      });
      throw error;
    }
  };

  return {
    executeWorkflow,
    isExecuting: runAdhocMutation.isPending,
  };
}

/**
 * Hook for publishing/unpublishing workflows
 */
export function usePublishWorkflow() {
  const queryClient = useQueryClient();

  const publishMutation = useMutation({
    mutationFn: ({ id, message }: { id: string; message?: string }) =>
      workflowsApi.publish(id, message),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['workflows'] });
    },
  });

  const unpublishMutation = useMutation({
    mutationFn: (id: string) => workflowsApi.unpublish(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['workflows'] });
    },
  });

  const publish = async (message?: string) => {
    const { workflowId } = useWorkflowStore.getState();
    if (!workflowId) {
      toast.error('Save workflow first', {
        description: 'You need to save the workflow before publishing.',
      });
      return;
    }

    try {
      const result = await publishMutation.mutateAsync({ id: workflowId, message });
      useWorkflowStore.getState().setIsActive(result.active);
      toast.success(`Published v${result.version_id}`, {
        description: message || 'Workflow is now active.',
      });
      return result;
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Unknown error';
      toast.error('Failed to publish', { description: message });
      throw error;
    }
  };

  const unpublish = async () => {
    const { workflowId } = useWorkflowStore.getState();
    if (!workflowId) return;

    try {
      const result = await unpublishMutation.mutateAsync(workflowId);
      useWorkflowStore.getState().setIsActive(result.active);
      toast.success('Workflow unpublished', {
        description: 'Triggers are now disabled.',
      });
      return result;
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Unknown error';
      toast.error('Failed to unpublish', { description: message });
      throw error;
    }
  };

  return {
    publish,
    unpublish,
    isPublishing: publishMutation.isPending || unpublishMutation.isPending,
  };
}

// ============================================================================
// Helper Functions
// ============================================================================


/**
 * Normalize backend output data to display format
 */
function normalizeOutputData(data: unknown): Record<string, unknown>[] {
  if (!data) return [];

  // Backend returns: [{ json: {...} }, { json: {...} }]
  if (Array.isArray(data)) {
    return data.map((item) => {
      if (item && typeof item === 'object' && 'json' in item) {
        return item.json as Record<string, unknown>;
      }
      return item as Record<string, unknown>;
    });
  }

  // Single object
  if (typeof data === 'object') {
    return [data as Record<string, unknown>];
  }

  return [];
}
