/**
 * State and calls of "AI on any node" (DESIGN-STAGE6 section 7, 9.2): one open request at a time. An explanation is just text; every
 * other task becomes a proposal the person reviews (the same review as the AI builder) before anything is saved.
 */
import { create } from 'zustand';
import { actorRequiredError, aiBuilderApi, describeAiError, getActorName, type AiUiError } from './api';
import { isExplainAnswer, nodeAssistArgs, validateNodeAiForm } from './nodeAi';
import type { BuilderBusy } from './aiProposalStore';
import type { NodeAiTask, Proposal } from './types';

export interface NodeAiTarget {
  /** the node's name (= its id on the canvas) */
  nodeName: string;
  nodeLabel: string;
  nodeType: string;
  task: NodeAiTask;
  /** write_expression: the setting to fill, when the caller knows it */
  field?: string;
}

export type NodeAiPhase = 'ask' | 'working' | 'answer' | 'review';

interface NodeAiState {
  target: NodeAiTarget | null;
  phase: NodeAiPhase;
  field: string;
  instruction: string;
  answer: string | null;
  proposal: Proposal | null;
  busy: BuilderBusy;
  error: AiUiError | null;
  /** the node the review asked the canvas to bring into view */
  focusNode: string | null;

  open: (t: NodeAiTarget) => void;
  close: () => void;
  setField: (v: string) => void;
  setInstruction: (v: string) => void;
  setFocusNode: (n: string | null) => void;
}

const initial = {
  target: null as NodeAiTarget | null, phase: 'ask' as NodeAiPhase, field: '', instruction: '', answer: null as string | null,
  proposal: null as Proposal | null, busy: 'idle' as BuilderBusy, error: null as AiUiError | null, focusNode: null as string | null,
};

export const useNodeAiStore = create<NodeAiState>((set) => ({
  ...initial,
  open: (target) => set({ ...initial, target, field: target.field ?? '' }),
  close: () => set({ ...initial }),
  setField: (field) => set({ field }),
  setInstruction: (instruction) => set({ instruction }),
  setFocusNode: (focusNode) => set({ focusNode }),
}));

const st = () => useNodeAiStore.getState();

async function guarded<T>(busy: BuilderBusy, fn: () => Promise<T>): Promise<T | null> {
  useNodeAiStore.setState({ busy, error: null });
  try {
    return await fn();
  } catch (e) {
    useNodeAiStore.setState({ error: describeAiError(e) });
    return null;
  } finally {
    useNodeAiStore.setState({ busy: 'idle' });
  }
}

/**
 * Send the request. `beforeRequest` runs first (the editor saves what is on the canvas, so the AI starts from what the person sees);
 * returning false cancels it. Returns true when an answer or a proposal came back.
 */
export async function submitNodeAssist(workflowId: string, beforeRequest?: () => Promise<boolean>): Promise<boolean> {
  const { target, field, instruction } = st();
  if (!target) return false;
  const actor = getActorName();
  if (!actor) {
    useNodeAiStore.setState({ error: actorRequiredError() });
    return false;
  }
  const problem = validateNodeAiForm(target.task, { field, instruction });
  if (problem) {
    useNodeAiStore.setState({ error: { code: 'request_invalid', message: problem, status: null, issues: [], testIds: [], proposalStatus: null } });
    return false;
  }
  if (beforeRequest && !(await beforeRequest())) return false;
  useNodeAiStore.setState({ phase: 'working' });
  const args = nodeAssistArgs(target.task, actor, { field, instruction });
  const r = await guarded('drafting', () => aiBuilderApi.nodeAssist(workflowId, target.nodeName, args));
  // someone closed the dialog while the AI worked: drop the answer
  if (st().target !== target) return false;
  if (!r) {
    useNodeAiStore.setState({ phase: 'ask' });
    return false;
  }
  if (isExplainAnswer(r)) useNodeAiStore.setState({ phase: 'answer', answer: r.answer });
  else useNodeAiStore.setState({ phase: 'review', proposal: r });
  return true;
}

export async function keepNodeProposal(opts: { acceptFailing?: boolean }): Promise<{ workflowId: string; proposal: Proposal } | null> {
  const p = st().proposal;
  const actor = getActorName();
  if (!p) return null;
  if (!actor) { useNodeAiStore.setState({ error: actorRequiredError() }); return null; }
  const r = await guarded('keeping', () => aiBuilderApi.keep(p.id, { actor, acceptFailing: opts.acceptFailing }, p.project_id));
  if (!r) return null;
  useNodeAiStore.setState({ proposal: r.proposal });
  return { workflowId: r.workflow_id, proposal: r.proposal };
}

export async function discardNodeProposal(): Promise<Proposal | null> {
  const p = st().proposal;
  const actor = getActorName();
  if (!p) return null;
  if (!actor) { useNodeAiStore.setState({ error: actorRequiredError() }); return null; }
  const r = await guarded('discarding', () => aiBuilderApi.discard(p.id, actor, p.project_id));
  if (r) useNodeAiStore.setState({ proposal: r });
  return r;
}

export async function undoNodeProposal(id?: string): Promise<Proposal | null> {
  const p = st().proposal;
  const target = id ?? p?.id;
  const actor = getActorName();
  if (!target) return null;
  if (!actor) { useNodeAiStore.setState({ error: actorRequiredError() }); return null; }
  const r = await guarded('undoing', () => aiBuilderApi.undo(target, actor, p?.project_id));
  if (r && st().proposal?.id === r.id) useNodeAiStore.setState({ proposal: r });
  return r;
}

