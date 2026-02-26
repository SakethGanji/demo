/**
 * SSE-based Execution Stream Hook
 *
 * Provides real-time workflow execution updates via Server-Sent Events.
 * Updates node status as each node starts/completes/fails.
 */

import { useCallback, useRef, useState } from 'react';
import { useWorkflowStore } from '../stores/workflowStore';
import { useUIModeStore } from '../stores/uiModeStore';
import { useEditorLayoutStore } from '../stores/editorLayoutStore';
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
    | 'execution:result'
    | 'agent:thinking'
    | 'agent:tool_call'
    | 'agent:tool_result'
    | 'agent:token'
    | 'agent:plan'
    | 'agent:reflect'
    | 'agent:spawn'
    | 'agent:child_complete'
    | 'agent:response'
    | 'agent:output_validation';
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
  // Node execution metrics
  metrics?: Record<string, unknown>;
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
  const [isExecuting, setIsExecuting] = useState(false);
  const [progress, setProgress] = useState<{ completed: number; total: number } | null>(null);
  const abortControllerRef = useRef<AbortController | null>(null);

  const cancelExecution = useCallback(() => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }
    setIsExecuting(false);
    setProgress(null);
  }, []);

  const executeWorkflow = useCallback(async (inputData?: Record<string, unknown>) => {
    const {
      nodes,
      edges,
      workflowName,
      workflowId,
      setNodeExecutionData,
      setSubworkflowNodeExecutionData,
      clearExecutionData,
    } = useWorkflowStore.getState();

    // Clear previous execution and UI data
    useUIModeStore.getState().reset();
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

    const nameToId = buildNameToIdMap(nodes as Node<WorkflowNodeData>[]);
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
          useWorkflowStore.getState().setNodeExecutionData(node.id, {
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
      const store = useWorkflowStore.getState();

      switch (event.type) {
        case 'execution:start':
          setProgress(event.progress || null);
          break;

        case 'node:start': {
          // Check if this is a subworkflow inner node event
          if (event.subworkflowParentNode) {
            const parentNodeId = nameToId.get(event.subworkflowParentNode);
            if (parentNodeId && event.nodeName) {
              store.setSubworkflowNodeExecutionData(parentNodeId, event.nodeName, {
                input: null,
                output: null,
                status: 'running',
                startTime: Date.now(),
              });
            }
          } else {
            const nodeId = nameToId.get(event.nodeName || '');
            if (nodeId) {
              const existing = store.executionData[nodeId];
              store.setNodeExecutionData(nodeId, {
                input: null,
                output: null,
                status: 'running',
                startTime: Date.now(),
                agentTrace: existing?.agentTrace,
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
              store.setSubworkflowNodeExecutionData(parentNodeId, event.nodeName, {
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

                // Check for UI output content (HTML/Markdown/PDF/Table)
                const uiStore = useUIModeStore.getState();
                let hasUIContent = false;
                for (const item of event.data) {
                  const data = item.json;
                  if (data._renderAs === 'html' && data.html) {
                    uiStore.setHtmlContent(String(data.html));
                    hasUIContent = true;
                  }
                  if (data._renderAs === 'markdown' && data.markdown) {
                    uiStore.setMarkdownContent(String(data.markdown));
                    hasUIContent = true;
                  }
                  if (data._renderAs === 'pdf' && data.pdf_base64) {
                    uiStore.setPdfBase64(String(data.pdf_base64));
                    hasUIContent = true;
                  }
                  if (data._renderAs === 'table' && Array.isArray(data.data)) {
                    uiStore.setTableData(data.data as Record<string, unknown>[]);
                    hasUIContent = true;
                  }
                }
                // Ensure bottom panel is open on UI tab when output content arrives
                if (hasUIContent) {
                  const layout = useEditorLayoutStore.getState();
                  if (!layout.bottomPanelOpen || layout.bottomPanelTab !== 'ui') {
                    layout.openBottomPanel('ui');
                  }
                }
              }

              // Find input from upstream node — read edges from store at event time
              const currentEdges = useWorkflowStore.getState().edges;
              const inputNodeName = findUpstreamNodeName(event.nodeName, nameToId, currentEdges);
              const inputData = inputNodeName ? nodeOutputs[inputNodeName] : null;

              const existingComplete = store.executionData[nodeId];
              store.setNodeExecutionData(nodeId, {
                input: inputData ? { items: inputData.map((d) => d.json) } : null,
                output: { items: event.data?.map((d) => d.json) || [] },
                status: 'success',
                startTime: Date.now(),
                endTime: Date.now(),
                metrics: event.metrics as import('../types/workflow').NodeMetrics | undefined,
                agentTrace: existingComplete?.agentTrace,
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
              store.setSubworkflowNodeExecutionData(parentNodeId, event.nodeName, {
                input: null,
                output: { items: [], error: event.error },
                status: 'error',
                endTime: Date.now(),
              });
            }
          } else {
            const nodeId = nameToId.get(event.nodeName || '');
            if (nodeId) {
              const existingError = store.executionData[nodeId];
              store.setNodeExecutionData(nodeId, {
                input: null,
                output: { items: [], error: event.error },
                status: 'error',
                endTime: Date.now(),
                metrics: event.metrics as import('../types/workflow').NodeMetrics | undefined,
                agentTrace: existingError?.agentTrace,
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

        default: {
          // Handle all agent:* events
          if (event.type.startsWith('agent:') && event.nodeName) {
            // Extract base node name: "Banking Agent/skill:fee_calc" → "Banking Agent"
            const baseNodeName = event.nodeName.split('/')[0];
            const nodeId = nameToId.get(baseNodeName);
            if (nodeId) {
              const eventData = event.data?.[0]?.json || {};
              store.appendAgentTraceEvent(nodeId, {
                type: event.type,
                timestamp: Date.parse(event.timestamp) || Date.now(),
                nodeName: event.nodeName,
                data: eventData,
              });
            }
          }
          break;
        }
      }
    }
  }, []);

  return {
    executeWorkflow,
    isExecuting,
    progress,
    cancelExecution,
  };
}
