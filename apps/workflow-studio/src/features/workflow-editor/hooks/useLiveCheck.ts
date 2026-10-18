import { useEffect } from 'react';
import { useWorkflowStore } from '../stores/workflowStore';
import { useCheckStore } from '../stores/checkStore';
import { toBackendWorkflow } from '../lib/workflowTransform';
import { workflowsApi } from '@/shared/lib/api';
import type { Node } from '@xyflow/react';
import type { WorkflowNodeData } from '../types/workflow';

export const LIVE_CHECK_DEBOUNCE_MS = 600;

/**
 * Runs the mistake checker against the unsaved draft.
 *
 * Trigger: when the workflow has an id, on load and 600 ms after the last change to nodes or edges.
 * De-dupe: the definition is fingerprinted without positions (dragging does not re-check), and an
 * identical fingerprint is not sent again; a sequence number drops responses that arrive out of order.
 * The store is cleared whenever the workflow id changes (load, switch, reset).
 * Failures (e.g. the endpoint not existing yet) only console.warn.
 */
export function useLiveCheck(): void {
  const workflowId = useWorkflowStore((s) => s.workflowId);

  useEffect(() => {
    useCheckStore.getState().clear();
    if (!workflowId) return;

    let seq = 0;
    let lastSent: string | null = null;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const run = () => {
      timer = null;
      const s = useWorkflowStore.getState();
      if (s.workflowId !== workflowId) return;
      const definition = toBackendWorkflow(s.nodes as Node<WorkflowNodeData>[], s.edges, s.workflowName, workflowId);
      const body = { nodes: definition.nodes, connections: definition.connections };
      const fingerprint = JSON.stringify({
        nodes: body.nodes.map((n) => ({ ...n, position: undefined })),
        connections: body.connections,
      });
      if (fingerprint === lastSent) return;
      lastSent = fingerprint;
      const mine = ++seq;
      workflowsApi
        .check(workflowId, { definition: body })
        .then((result) => {
          if (mine !== seq) return; // a newer request was started
          useCheckStore.getState().setResult(result);
        })
        .catch((err) => {
          if (mine === seq) lastSent = null; // allow a retry on the next change
          console.warn('[live check] failed', err);
        });
    };

    const schedule = () => {
      if (timer) clearTimeout(timer);
      timer = setTimeout(run, LIVE_CHECK_DEBOUNCE_MS);
    };

    let prevNodes = useWorkflowStore.getState().nodes;
    let prevEdges = useWorkflowStore.getState().edges;
    const unsub = useWorkflowStore.subscribe((s) => {
      if (s.nodes === prevNodes && s.edges === prevEdges) return;
      prevNodes = s.nodes;
      prevEdges = s.edges;
      schedule();
    });
    schedule(); // after load

    return () => {
      unsub();
      if (timer) clearTimeout(timer);
      seq++; // invalidate in-flight responses
    };
  }, [workflowId]);
}
