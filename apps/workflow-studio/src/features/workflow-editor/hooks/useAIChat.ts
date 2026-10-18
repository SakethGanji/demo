/**
 * Hook for the editor's AI chat. It sends the message with the editor's context and streams the answer into the chat store.
 * The chat never changes the canvas: a change comes back as a proposal card (reviewed in the AI build page), a new workflow as a
 * plan card (approved there). See `lib/aiChatClient.ts` for the stream handling.
 */

import { useRef, useCallback } from 'react';
import { useWorkflowStore } from '../stores/workflowStore';
import { useAIChatStore } from '../stores/aiChatStore';
import { useEditorProjectStore } from '../stores/editorProjectStore';
import { getActorName } from '@/features/ai-builder/api';
import { toBackendWorkflow } from '../lib/workflowTransform';
import { flushPendingEdits } from '../lib/pendingEdits';
import { sendChatMessage, type ChatContext } from '../lib/aiChatClient';
import { useSaveWorkflow } from './useWorkflowApi';
import type { Node } from '@xyflow/react';
import type { WorkflowNodeData, AIChatRequest } from '../types/workflow';

/** The editor snapshot sent with a message (kept for older engines; the current engine reads the stored draft). */
function snapshot(): AIChatRequest['workflow_context'] {
  const { nodes, edges, workflowName, workflowId } = useWorkflowStore.getState();
  const workflowNodes = nodes.filter((n) => n.type === 'workflowNode') as Node<WorkflowNodeData>[];
  if (workflowNodes.length === 0) return null;
  const backend = toBackendWorkflow(nodes as Node<WorkflowNodeData>[], edges, workflowName, workflowId);
  return {
    name: backend.name,
    nodes: backend.nodes.map((n) => ({ name: n.name, type: n.type, parameters: n.parameters as Record<string, unknown> })),
    connections: backend.connections.map((c) => ({
      source_node: c.source_node, target_node: c.target_node, source_output: c.source_output, target_input: c.target_input,
    })),
  };
}

export function useAIChat() {
  const abortRef = useRef<AbortController | null>(null);
  const { saveWorkflow } = useSaveWorkflow();

  const sendMessage = useCallback(
    async (message: string, modeHint: AIChatRequest['mode_hint'] = 'auto') => {
      // A change is proposed against the SAVED draft, so what is on the canvas is saved first (as the Change tab does).
      flushPendingEdits();
      const ws = useWorkflowStore.getState();
      if (ws.workflowId && ws.isDirty()) {
        try {
          await saveWorkflow();
        } catch {
          return; // saveWorkflow already told the person why
        }
      }
      const after = useWorkflowStore.getState();
      const selected = after.nodes.find((n) => n.id === after.selectedNodeId);
      const context: ChatContext = {
        projectId: useEditorProjectStore.getState().projectId || null,
        workflowId: after.workflowId ?? null,
        selectedNode: selected ? (selected.data as WorkflowNodeData).name ?? selected.id : null,
        actor: getActorName(),
        workflowContext: snapshot(),
      };
      const controller = new AbortController();
      abortRef.current = controller;
      try {
        await sendChatMessage({ message, modeHint, context, signal: controller.signal });
      } finally {
        abortRef.current = null;
      }
    },
    [saveWorkflow],
  );

  const cancelStream = useCallback(() => {
    abortRef.current?.abort();
    useAIChatStore.getState().setStreaming(false);
  }, []);

  const isStreaming = useAIChatStore((s) => s.isStreaming);

  return { sendMessage, isStreaming, cancelStream };
}
