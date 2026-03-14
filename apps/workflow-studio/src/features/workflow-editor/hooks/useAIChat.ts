/**
 * Hook for AI Chat interactions.
 * Manages SSE streaming from the backend and applies operations to the workflow.
 */

import { useRef, useCallback } from 'react';
import { useWorkflowStore } from '../stores/workflowStore';
import { useAIChatStore } from '../stores/aiChatStore';
import { applyAIResponse } from '../lib/aiOperationApplier';
import { toBackendWorkflow } from '../lib/workflowTransform';
import { consumeSSEStream } from '@/shared/lib/sseParser';
import { backends } from '@/shared/lib/config';
import type { Node } from '@xyflow/react';
import type { WorkflowNodeData } from '../types/workflow';
import type { AIChatRequest, AIResponsePayload } from '../types/workflow';

export function useAIChat() {
  const abortRef = useRef<AbortController | null>(null);

  const sendMessage = useCallback(
    async (message: string, modeHint: AIChatRequest['mode_hint'] = 'auto') => {
      const { addMessage, updateLastMessage, setStreaming, messages, sessionId } = useAIChatStore.getState();
      const { nodes, edges, workflowName, workflowId } = useWorkflowStore.getState();

      // Add user message
      addMessage({ role: 'user', content: message });

      // Add placeholder assistant message
      addMessage({ role: 'assistant', content: '' });
      setStreaming(true);

      // Build workflow context
      let workflowContext: AIChatRequest['workflow_context'] = null;
      const workflowNodes = nodes.filter(
        (n) => n.type === 'workflowNode'
      ) as Node<WorkflowNodeData>[];

      if (workflowNodes.length > 0) {
        const backend = toBackendWorkflow(
          nodes as Node<WorkflowNodeData>[],
          edges,
          workflowName,
          workflowId,
        );
        workflowContext = {
          name: backend.name,
          nodes: backend.nodes.map((n) => ({
            name: n.name,
            type: n.type,
            parameters: n.parameters as Record<string, unknown>,
          })),
          connections: backend.connections.map((c) => ({
            source_node: c.source_node,
            target_node: c.target_node,
            source_output: c.source_output,
            target_input: c.target_input,
          })),
        };
      }

      // Build conversation history (exclude the messages we just added)
      const conversationHistory = messages.map((m) => ({
        role: m.role,
        content: m.content,
      }));

      const body: AIChatRequest = {
        message,
        session_id: sessionId,
        workflow_context: workflowContext,
        conversation_history: conversationHistory,
        mode_hint: modeHint,
      };

      const controller = new AbortController();
      abortRef.current = controller;

      try {
        const response = await fetch(`${backends.workflow}/api/ai/chat`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
          signal: controller.signal,
        });

        if (!response.ok) {
          const errText = await response.text();
          updateLastMessage({
            content: `Error: ${response.status} — ${errText.slice(0, 200)}`,
          });
          setStreaming(false);
          return;
        }

        const reader = response.body?.getReader();
        if (!reader) {
          updateLastMessage({ content: 'Error: No response stream' });
          setStreaming(false);
          return;
        }

        await consumeSSEStream(reader, (dataStr) => {
          if (!dataStr.trim()) return;

          try {
            const data = JSON.parse(dataStr);

            if (data.type === 'text') {
              updateLastMessage({ content: data.content });
            } else if (data.type === 'operations') {
              const payload = data.payload as AIResponsePayload;
              // Store operations on the message
              updateLastMessage({ operations: payload });
              // Apply to workflow (reads node types from store registry)
              applyAIResponse(payload);
            } else if (data.type === 'error') {
              updateLastMessage({
                content: `\n\n**Error:** ${data.message}`,
              });
            }
            // 'done' — just stop
          } catch (parseErr) {
            console.warn('[AI Chat] Failed to process SSE event:', dataStr, parseErr);
          }
        });
      } catch (err: unknown) {
        if (err instanceof DOMException && err.name === 'AbortError') {
          // User cancelled — no-op
        } else {
          updateLastMessage({
            content: `\n\nConnection error: ${err instanceof Error ? err.message : 'Unknown error'}`,
          });
        }
      } finally {
        setStreaming(false);
        abortRef.current = null;
      }
    },
    [],
  );

  const cancelStream = useCallback(() => {
    abortRef.current?.abort();
    useAIChatStore.getState().setStreaming(false);
  }, []);

  const isStreaming = useAIChatStore((s) => s.isStreaming);

  return { sendMessage, isStreaming, cancelStream };
}
