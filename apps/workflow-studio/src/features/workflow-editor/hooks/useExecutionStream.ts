/**
 * SSE-based Execution Stream Hook
 *
 * Provides real-time workflow execution updates via Server-Sent Events.
 * Updates node status as each node starts/completes/fails.
 */

import { useCallback, useRef, useState } from 'react';
import { useWorkflowStore } from '../stores/workflowStore';
import { useUIModeStore } from '../stores/uiModeStore';
import { toBackendWorkflow, findUpstreamNodeName, buildNameToIdMap } from '../lib/workflowTransform';
import { consumeSSEStream } from '../lib/sseParser';
import { backends } from '@/shared/lib/config';
import { toast } from 'sonner';
import type { WorkflowNodeData } from '../types/workflow';
import type { Node } from 'reactflow';

// Event types from backend
interface ExecutionEvent {
  type:
    | 'execution:start'
    | 'node:start'
    | 'node:complete'
    | 'node:error'
    | 'execution:complete'
    | 'execution:error'
    | 'execution:result';
  executionId: string;
  timestamp: string;
  nodeName?: string;
  nodeType?: string;
  data?: Array<{ json: Record<string, unknown> }>;
  error?: string;
  progress?: {
    completed: number;
    total: number;
  };
  // For execution:result event
  status?: 'success' | 'failed';
  errors?: Array<{ nodeName: string; error: string; timestamp: string }>;
  // Subworkflow event fields
  subworkflowParentNode?: string;
  subworkflowId?: string;
}

interface UseExecutionStreamResult {
  executeWorkflow: (inputData?: Record<string, unknown>) => Promise<void>;
  isExecuting: boolean;
  progress: { completed: number; total: number } | null;
  cancelExecution: () => void;
}


export function useExecutionStream(): UseExecutionStreamResult {
  const {
    nodes,
    edges,
    workflowName,
    workflowId,
    setNodeExecutionData,
    setSubworkflowNodeExecutionData,
    clearExecutionData,
  } = useWorkflowStore();

  const setHtmlContent = useUIModeStore((s) => s.setHtmlContent);
  const setMarkdownContent = useUIModeStore((s) => s.setMarkdownContent);

  const [isExecuting, setIsExecuting] = useState(false);
  const [progress, setProgress] = useState<{ completed: number; total: number } | null>(null);
  const abortControllerRef = useRef<AbortController | null>(null);

  // Build name-to-id mapping for translating backend node names to UI node IDs
  const getNameToIdMap = useCallback(
    () => buildNameToIdMap(nodes as Node<WorkflowNodeData>[]),
    [nodes],
  );

  const cancelExecution = useCallback(() => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }
    setIsExecuting(false);
    setProgress(null);
  }, []);

  const executeWorkflow = useCallback(async (inputData?: Record<string, unknown>) => {
    // Clear previous execution data
    clearExecutionData();
    setIsExecuting(true);
    setProgress(null);

    // Mark all workflow nodes as pending initially
    const workflowNodes = nodes.filter((n) => n.type === 'workflowNode' || n.type === 'subworkflowNode');
    workflowNodes.forEach((node) => {
      setNodeExecutionData(node.id, {
        input: null,
        output: null,
        status: 'idle',
      });
    });

    const nameToId = getNameToIdMap();
    const nodeOutputs: Record<string, Array<{ json: Record<string, unknown> }>> = {};

    try {
      abortControllerRef.current = new AbortController();

      let url: string;
      let body: string;

      if (workflowId) {
        // Execute saved workflow via POST with optional input data
        url = `${backends.workflow}/execution-stream/${workflowId}`;
        body = JSON.stringify({ input_data: inputData || null });
      } else {
        // Execute ad-hoc workflow via POST
        const backendWorkflow = toBackendWorkflow(
          nodes as Node<WorkflowNodeData>[],
          edges,
          workflowName
        );
        url = `${backends.workflow}/execution-stream/adhoc`;
        body = JSON.stringify({
          ...backendWorkflow,
          input_data: inputData || null,
        });
      }

      const response = await fetch(url, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body,
        signal: abortControllerRef.current.signal,
      });

      if (!response.ok) {
        throw new Error(`HTTP error: ${response.status}`);
      }

      // Process the SSE stream
      const reader = response.body?.getReader();
      if (!reader) {
        throw new Error('No response body');
      }

      await consumeSSEStream(reader, (dataStr) => {
        if (!dataStr.trim()) return;
        try {
          const event: ExecutionEvent = JSON.parse(dataStr);
          handleEvent(event, nameToId, nodeOutputs);
        } catch (e) {
          console.error('Failed to parse SSE event:', e);
        }
      });

      setIsExecuting(false);
      setProgress(null);
    } catch (error) {
      if ((error as Error).name === 'AbortError') {
        toast.info('Execution cancelled');
      } else {
        const message = error instanceof Error ? error.message : 'Unknown error';
        toast.error('Workflow execution failed', { description: message });

        // Mark all nodes as error
        workflowNodes.forEach((node) => {
          setNodeExecutionData(node.id, {
            input: null,
            output: { items: [], error: message },
            status: 'error',
            endTime: Date.now(),
          });
        });
      }
      setIsExecuting(false);
      setProgress(null);
    }

    // Helper function to handle events
    function handleEvent(
      event: ExecutionEvent,
      nameToId: Map<string, string>,
      nodeOutputs: Record<string, Array<{ json: Record<string, unknown> }>>,
    ) {
      switch (event.type) {
        case 'execution:start':
          setProgress(event.progress || null);
          break;

        case 'node:start': {
          // Check if this is a subworkflow inner node event
          if (event.subworkflowParentNode) {
            const parentNodeId = nameToId.get(event.subworkflowParentNode);
            if (parentNodeId && event.nodeName) {
              setSubworkflowNodeExecutionData(parentNodeId, event.nodeName, {
                input: null,
                output: null,
                status: 'running',
                startTime: Date.now(),
              });
            }
          } else {
            const nodeId = nameToId.get(event.nodeName || '');
            if (nodeId) {
              setNodeExecutionData(nodeId, {
                input: null,
                output: null,
                status: 'running',
                startTime: Date.now(),
              });
            }
          }
          setProgress(event.progress || null);
          break;
        }

        case 'node:complete': {
          // Check if this is a subworkflow inner node event
          if (event.subworkflowParentNode) {
            const parentNodeId = nameToId.get(event.subworkflowParentNode);
            if (parentNodeId && event.nodeName) {
              setSubworkflowNodeExecutionData(parentNodeId, event.nodeName, {
                input: null,
                output: { items: event.data?.map((d) => d.json) || [] },
                status: 'success',
                startTime: Date.now(),
                endTime: Date.now(),
              });
            }
          } else {
            const nodeId = nameToId.get(event.nodeName || '');
            if (nodeId && event.nodeName) {
              // Store output for later use as input to downstream nodes
              if (event.data) {
                nodeOutputs[event.nodeName] = event.data;

                // Check for UI output content (HTML/Markdown)
                for (const item of event.data) {
                  const data = item.json;
                  if (data._renderAs === 'html' && data.html) {
                    setHtmlContent(String(data.html));
                  }
                  if (data._renderAs === 'markdown' && data.markdown) {
                    setMarkdownContent(String(data.markdown));
                  }
                }
              }

              // Find input from upstream node
              const inputNodeName = findUpstreamNodeName(event.nodeName, nameToId, edges);
              const inputData = inputNodeName ? nodeOutputs[inputNodeName] : null;

              setNodeExecutionData(nodeId, {
                input: inputData ? { items: inputData.map((d) => d.json) } : null,
                output: { items: event.data?.map((d) => d.json) || [] },
                status: 'success',
                startTime: Date.now(),
                endTime: Date.now(),
              });
            }
          }
          setProgress(event.progress || null);
          break;
        }

        case 'node:error': {
          // Check if this is a subworkflow inner node event
          if (event.subworkflowParentNode) {
            const parentNodeId = nameToId.get(event.subworkflowParentNode);
            if (parentNodeId && event.nodeName) {
              setSubworkflowNodeExecutionData(parentNodeId, event.nodeName, {
                input: null,
                output: { items: [], error: event.error },
                status: 'error',
                endTime: Date.now(),
              });
            }
          } else {
            const nodeId = nameToId.get(event.nodeName || '');
            if (nodeId) {
              setNodeExecutionData(nodeId, {
                input: null,
                output: { items: [], error: event.error },
                status: 'error',
                endTime: Date.now(),
              });
            }
          }
          break;
        }

        case 'execution:complete':
          setProgress(event.progress || null);
          setIsExecuting(false);
          break;

        case 'execution:result':
          // Final result with all data
          if (event.status === 'success') {
            toast.success('Workflow executed successfully', {
              description: `Execution ID: ${event.executionId}`,
            });
          } else if (event.errors && event.errors.length > 0) {
            toast.error('Workflow execution failed', {
              description: event.errors[0]?.error || 'Unknown error',
            });
          }
          setIsExecuting(false);
          setProgress(null);
          break;

        case 'execution:error':
          toast.error('Workflow execution failed', {
            description: event.error || 'Unknown error',
          });
          setIsExecuting(false);
          setProgress(null);
          break;
      }
    }
  }, [
    nodes,
    edges,
    workflowName,
    workflowId,
    setNodeExecutionData,
    setSubworkflowNodeExecutionData,
    clearExecutionData,
    getNameToIdMap,
    setHtmlContent,
    setMarkdownContent,
  ]);

  return {
    executeWorkflow,
    isExecuting,
    progress,
    cancelExecution,
  };
}
