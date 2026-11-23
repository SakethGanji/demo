/**
 * The editor chat's side of POST /api/ai/chat (DESIGN-STAGE6-AI-BUILDER section 8).
 *
 * The engine streams `phase`, `route`, `text`, `spec`, `proposal`, `error` and `done`. This client never changes a workflow:
 * a change arrives as a `proposal` (a built copy that a person reviews and keeps in the review panel), a new workflow
 * arrives as a `spec` (a plan a person approves). There is no `operations` event any more; one that arrives anyway is ignored.
 */

import { consumeSSEStream } from '@/shared/lib/sseParser';
import { backends } from '@/shared/lib/config';
import { aiBuilderApi, aiErrorFromCode, describeAiError } from '@/features/ai-builder/api';
import { diffSummaryText } from '@/features/ai-builder/proposalMarks';
import type { Proposal, Spec } from '@/features/ai-builder/types';
import { useAIChatStore } from '../stores/aiChatStore';
import type { AIChatCard, AIChatRequest } from '../types/workflow';

/** What the editor knows when a message is sent. */
export interface ChatContext {
  projectId: string | null;
  /** the saved workflow that is open, if any (a change is proposed against its stored draft) */
  workflowId: string | null;
  selectedNode: string | null;
  actor: string;
  workflowContext: AIChatRequest['workflow_context'];
}

const store = () => useAIChatStore.getState();

/** The shape of one streamed event, after parsing. Unknown types are kept so they can be ignored explicitly. */
export interface ChatEvent {
  type: string;
  [k: string]: unknown;
}

export function parseChatEvent(dataStr: string): ChatEvent | null {
  if (!dataStr.trim()) return null;
  try {
    const v: unknown = JSON.parse(dataStr);
    return v && typeof v === 'object' && typeof (v as { type?: unknown }).type === 'string' ? (v as ChatEvent) : null;
  } catch {
    return null;
  }
}

/** The body of the request. The actor travels in the body only (a header cannot carry every name). */
export function buildChatRequest(message: string, modeHint: AIChatRequest['mode_hint'], context: ChatContext, history: Array<{ role: 'user' | 'assistant'; content: string }>, sessionId: string): AIChatRequest {
  return {
    message,
    session_id: sessionId,
    project_id: context.projectId,
    workflow_id: context.workflowId,
    selected_node: context.selectedNode,
    actor: context.actor,
    workflow_context: context.workflowContext,
    conversation_history: history,
    mode_hint: modeHint,
  };
}

const num = (v: unknown): number => (typeof v === 'number' && Number.isFinite(v) ? v : 0);

/** A `spec` event as the card under the assistant message. */
export function specCardFromEvent(ev: ChatEvent): AIChatCard | null {
  const spec = ev.spec as Spec | undefined;
  const id = ev.proposal_id;
  if (!spec || typeof id !== 'string') return null;
  const errors = (spec.issues ?? []).filter((i) => i.level !== 'warning').length;
  return {
    kind: 'spec', proposalId: id, title: spec.title || 'Untitled workflow', summary: spec.summary || '',
    rules: spec.rules?.length ?? 0, steps: spec.steps?.length ?? 0, tests: spec.tests?.length ?? 0, errors, phase: 'draft',
  };
}

/** A `proposal` event as the card under the assistant message. */
export function proposalCardFromEvent(ev: ChatEvent): AIChatCard | null {
  if (typeof ev.proposal_id !== 'string') return null;
  const counts = (ev.diff as { counts?: Record<string, unknown> } | undefined)?.counts ?? {};
  return {
    kind: 'proposal', proposalId: ev.proposal_id, workflowId: typeof ev.workflow_id === 'string' ? ev.workflow_id : null,
    status: typeof ev.status === 'string' ? ev.status : 'ready', summary: typeof ev.summary === 'string' ? ev.summary : '',
    counts: { added: num(counts.added), removed: num(counts.removed), changed: num(counts.changed) },
    passed: !!(ev.report as { passed?: unknown } | undefined)?.passed,
  };
}

/** A built proposal (from the approve-and-build button) as the same card. */
export function proposalCardFromView(p: Proposal): AIChatCard {
  const passed = !!p.report?.passed;
  return {
    kind: 'proposal', proposalId: p.id, workflowId: p.workflow_id, status: p.status,
    summary: p.status === 'ready' ? `${diffSummaryText(p.diff)}. ${passed ? 'Tests pass.' : 'Some tests fail.'}` : 'The workflow could not be built.',
    counts: { added: p.diff?.counts.added ?? 0, removed: p.diff?.counts.removed ?? 0, changed: p.diff?.counts.changed ?? 0 }, passed,
  };
}

function appendContent(id: string, text: string) {
  const cur = store().messages.find((m) => m.id === id);
  store().updateMessage(id, { content: (cur?.content ?? '') + text });
}

/** Apply one event to the assistant message `id`. */
export function applyChatEvent(id: string, ev: ChatEvent): void {
  switch (ev.type) {
    case 'phase':
      if (typeof ev.message === 'string') store().updateMessage(id, { status: ev.message });
      break;
    case 'text':
      if (typeof ev.content === 'string') store().updateMessage(id, { status: undefined, content: (store().messages.find((m) => m.id === id)?.content ?? '') + ev.content });
      break;
    case 'spec': {
      const card = specCardFromEvent(ev);
      if (card) store().updateMessage(id, { status: undefined, card, content: 'I wrote a plan with tests for you to check. Nothing is built until you approve it.' });
      break;
    }
    case 'proposal': {
      const card = proposalCardFromEvent(ev);
      if (card) store().updateMessage(id, { status: undefined, card, content: typeof ev.summary === 'string' ? ev.summary : '' });
      break;
    }
    case 'error':
      store().updateMessage(id, { status: undefined });
      appendContent(id, `\n\n**Error:** ${aiErrorFromCode(ev.code).message}`);
      break;
    default:
      // `route`, `done`, and anything else (including a legacy `operations` event, which is never applied)
      break;
  }
}

/** Send one message and stream the answer into the chat store. Resolves when the stream has ended. */
export async function sendChatMessage(opts: {
  message: string;
  modeHint?: AIChatRequest['mode_hint'];
  context: ChatContext;
  signal?: AbortSignal;
  fetchImpl?: typeof fetch;
}): Promise<void> {
  const { message, modeHint = 'auto', context, signal } = opts;
  const doFetch = opts.fetchImpl ?? fetch;
  const { addMessage, setStreaming, messages, sessionId } = store();
  const history = messages.map((m) => ({ role: m.role, content: m.content }));

  addMessage({ role: 'user', content: message });
  addMessage({ role: 'assistant', content: '' });
  const id = store().messages[store().messages.length - 1].id;
  setStreaming(true);

  try {
    const response = await doFetch(`${backends.workflow}/api/ai/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(buildChatRequest(message, modeHint, context, history, sessionId)),
      signal,
    });
    if (!response.ok) {
      store().updateMessage(id, { content: `The assistant could not be reached (status ${response.status}). Nothing was changed.` });
      return;
    }
    const reader = response.body?.getReader();
    if (!reader) {
      store().updateMessage(id, { content: 'The assistant sent no answer. Nothing was changed.' });
      return;
    }
    await consumeSSEStream(reader, (dataStr) => {
      const ev = parseChatEvent(dataStr);
      if (ev) applyChatEvent(id, ev);
    });
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') {
      // cancelled by the person: nothing to say
    } else {
      appendContent(id, '\n\nThe connection to the assistant was lost. Nothing was changed.');
    }
  } finally {
    store().updateMessage(id, { status: undefined });
    setStreaming(false);
  }
}

function setCard(id: string, card: AIChatCard) {
  store().updateMessage(id, { card });
}

/** The spec card's Approve button: approve the plan (locks its tests), build it, and turn the card into a proposal card. */
export async function approveChatSpec(messageId: string, actor: string, projectId?: string): Promise<void> {
  const msg = store().messages.find((m) => m.id === messageId);
  const card = msg?.card;
  if (!card || card.kind !== 'spec' || card.phase === 'approving' || card.phase === 'building') return;
  const pid = card.proposalId;
  setCard(messageId, { ...card, phase: 'approving', error: undefined });
  try {
    await aiBuilderApi.approveSpec(pid, actor, projectId);
    setCard(messageId, { ...card, phase: 'building', error: undefined });
    const built = await aiBuilderApi.build(pid, actor, projectId);
    setCard(messageId, proposalCardFromView(built));
  } catch (e) {
    setCard(messageId, { ...card, phase: 'failed', error: describeAiError(e).message });
  }
}
