/**
 * Drives the AI builder flow (DESIGN-STAGE6 §9.2): request -> spec (debounced saves) -> approve -> build with
 * polled progress -> keep / discard / revise -> undo. Network calls live here; state lives in `aiProposalStore`.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate } from '@tanstack/react-router';
import { useQueryClient } from '@tanstack/react-query';
import { useEditorProjectStore } from '@/features/workflow-editor/stores/editorProjectStore';
import { useWorkflowStore } from '@/features/workflow-editor/stores/workflowStore';
import { actorRequiredError, aiBuilderApi, describeAiError, getActorName } from './api';
import { specIsDirty, useAiProposalStore, type BuilderBusy } from './aiProposalStore';
import { endStage, settleStage, swapStageForDraft } from './lib/stageBridge';
import type { Proposal } from './types';

/**
 * A test-only switch, like `__STUDIO_TEST_MOTION__`: a spec that sets `window.__STUDIO_TEST_GRAPH__ = 'off'` before the app
 * loads asks for a plan the old way (no knowledge graph, no questions). Not a user setting; the default is on.
 */
declare global {
  var __STUDIO_TEST_GRAPH__: string | undefined;
}
export const graphEnabled = (): boolean => globalThis.__STUDIO_TEST_GRAPH__ !== 'off';

export const SPEC_SAVE_DELAY_MS = 700;
export const POLL_MS = 750;

const store = () => useAiProposalStore.getState();

/** Keep and Undo change a saved workflow: whatever the editor holds for it is stale now. */
export function refreshAfterWorkflowChange(qc: ReturnType<typeof useQueryClient>, workflowId: string | null) {
  void qc.invalidateQueries({ queryKey: ['workflows'] });
  void qc.invalidateQueries({ queryKey: ['project'] });
  if (workflowId && useWorkflowStore.getState().workflowId === workflowId) useWorkflowStore.getState().resetWorkflow();
}

export function useAiBuilder(projectId: string) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const saving = useRef<Promise<void> | null>(null);
  const pollTimer = useRef<ReturnType<typeof setInterval> | null>(null);
  const pollBusy = useRef(false);
  /** the engine refused one answer (422 answer_invalid): shown on that question, not as a banner */
  const [answerError, setAnswerError] = useState<{ qid: string; message: string } | null>(null);

  // ---- polling (events + the proposal while it is `building`)

  const stopPolling = useCallback(() => {
    if (pollTimer.current) clearInterval(pollTimer.current);
    pollTimer.current = null;
    store().setPolling(false);
  }, []);

  const pollOnce = useCallback(async (id: string) => {
    if (pollBusy.current) return;
    pollBusy.current = true;
    try {
      const r = await aiBuilderApi.events(id, store().lastSeq, projectId);
      store().appendEvents(r.items, r.last_seq);
    } catch {
      /* progress is a courtesy: the build call itself reports failures */
    } finally {
      pollBusy.current = false;
    }
  }, [projectId]);

  const startPolling = useCallback((id: string, alsoRefreshProposal = false) => {
    stopPolling();
    store().setPolling(true);
    void pollOnce(id);
    pollTimer.current = setInterval(() => {
      void pollOnce(id);
      if (alsoRefreshProposal) {
        void aiBuilderApi.get(id, projectId).then((p) => {
          if (p.status !== 'building') {
            store().setProposal(p);
            void pollOnce(id).then(stopPolling);
          }
        }).catch(() => undefined);
      }
    }, POLL_MS);
  }, [pollOnce, projectId, stopPolling]);

  useEffect(() => stopPolling, [stopPolling]);

  // ---- run helper: busy flag + plain-English errors

  const run = useCallback(async <T,>(busy: BuilderBusy, fn: () => Promise<T>): Promise<T | null> => {
    store().setBusy(busy);
    store().setError(null);
    try {
      return await fn();
    } catch (e) {
      store().setError(describeAiError(e));
      return null;
    } finally {
      store().setBusy('idle');
    }
  }, []);

  const actorOrFail = useCallback((): string | null => {
    const a = getActorName();
    if (!a) store().setError(actorRequiredError());
    return a || null;
  }, []);

  // ---- steps of the flow

  const load = useCallback(async (id: string): Promise<Proposal | null> => {
    store().setError(null);
    try {
      const p = await aiBuilderApi.get(id, projectId);
      store().setProposal(p);
      return p;
    } catch (e) {
      store().setError(describeAiError(e));
      return null;
    }
  }, [projectId]);

  /** §4.1: describe it, get a plan back. */
  const draftSpec = useCallback(async (request: string, workflowId?: string | null): Promise<Proposal | null> => {
    const actor = actorOrFail();
    if (!actor) return null;
    store().setPendingRequest(request);
    const p = await run('drafting', () => aiBuilderApi.draftSpec({ projectId, request, actor, workflowId, graph: graphEnabled() }));
    if (p) store().setProposal(p);
    return p;
  }, [actorOrFail, projectId, run]);

  /**
   * Answers the questions of a `clarify` proposal; the plan is written next. A refused answer (422) comes back in
   * `answerError` with the question it belongs to, and the proposal stays in `clarify`.
   */
  const answer = useCallback(async (answers: Record<string, unknown>): Promise<Proposal | null> => {
    const actor = actorOrFail();
    const cur = store().proposal;
    if (!actor || !cur) return null;
    setAnswerError(null);
    store().setBusy('drafting');
    store().setError(null);
    try {
      const p = await aiBuilderApi.answers(cur.id, answers, actor, projectId);
      store().setProposal(p);
      return p;
    } catch (e) {
      const err = describeAiError(e);
      if (err.code === 'answer_invalid' && err.qid) setAnswerError({ qid: err.qid, message: err.message });
      else {
        store().setError(err);
        if (err.code === 'bad_state') void load(cur.id); // it moved on without us: show where it is
      }
      return null;
    } finally {
      store().setBusy('idle');
    }
  }, [actorOrFail, load, projectId]);

  /** Sends the local spec edits now (also what the debounce calls). Resolves when nothing is left to save. */
  const saveSpecNow = useCallback(async (): Promise<boolean> => {
    if (saving.current) await saving.current;
    const s = store();
    if (!s.proposal || !s.specDraft || s.proposal.status !== 'spec_draft' || !specIsDirty(s)) return true;
    const actor = getActorName();
    if (!actor) return false;
    const sent = s.editVersion;
    const id = s.proposal.id;
    const spec = s.specDraft;
    let ok = true;
    const job = (async () => {
      store().setBusy('saving');
      try {
        const p = await aiBuilderApi.putSpec(id, spec, actor, projectId);
        store().applySavedProposal(p, sent);
        store().setError(null);
      } catch (e) {
        ok = false;
        store().setError(describeAiError(e));
      } finally {
        store().setBusy('idle');
      }
    })();
    saving.current = job;
    await job;
    saving.current = null;
    return ok;
  }, [projectId]);

  /** Build an approved plan with live progress (also resumes a plan approved earlier). */
  const build = useCallback(async (): Promise<Proposal | null> => {
    const actor = actorOrFail();
    const cur = store().proposal;
    if (!actor || !cur) return null;
    store().resetEvents();
    startPolling(cur.id);
    // The finished proposal is stored BEFORE `busy` drops back to idle: in between, the status is still `spec_approved`, which
    // reads as the plan step, so the sidebar flashed the plan, the build log remounted and the stage redrew its outlines.
    const built = await run('building', async () => {
      const p = await aiBuilderApi.build(cur.id, actor, projectId);
      await pollOnce(cur.id);
      stopPolling();
      store().setProposal(p);
      return p;
    });
    if (!built) {
      await pollOnce(cur.id);
      stopPolling();
      await load(cur.id); // a failed build still leaves a row worth showing
    }
    return built;
  }, [actorOrFail, load, pollOnce, projectId, run, startPolling, stopPolling]);

  /** Approve the plan (locks its tests), then build. */
  const approveAndBuild = useCallback(async (): Promise<Proposal | null> => {
    const actor = actorOrFail();
    const cur = store().proposal;
    if (!actor || !cur) return null;
    store().setError(null);
    if (!(await saveSpecNow())) return null;
    const approved = await run('approving', () => aiBuilderApi.approveSpec(cur.id, actor, projectId));
    if (!approved) {
      void load(cur.id); // the server may have found new issues
      return null;
    }
    store().setProposal(approved);
    return build();
  }, [actorOrFail, build, load, projectId, run, saveSpecNow]);

  /**
   * Keep: save the change, play the settle sweep, then hand the canvas to the saved draft. A new workflow also moves the
   * URL to its own id; the stage lives in a store, so it survives that move and the swap shows no jump.
   */
  const keep = useCallback(async (opts: { acceptFailing?: boolean; name?: string } = {}) => {
    const actor = actorOrFail();
    const cur = store().proposal;
    if (!actor || !cur) return null;
    const wasNew = cur.workflow_id == null;
    const r = await run('keeping', () => aiBuilderApi.keep(cur.id, { actor, ...opts }, projectId));
    if (!r) return null;
    store().setProposal(r.proposal);
    void qc.invalidateQueries({ queryKey: ['workflows'] });
    void qc.invalidateQueries({ queryKey: ['project'] });
    await settleStage();
    const slug = useEditorProjectStore.getState().slug;
    const open = wasNew
      ? () => navigate({ to: '/p/$project/workflows/$workflowId', params: { project: slug, workflowId: r.workflow_id }, search: { proposal: cur.id }, replace: true })
      : undefined;
    await swapStageForDraft(r.workflow_id, open);
    return r;
  }, [actorOrFail, navigate, projectId, qc, run]);

  const discard = useCallback(async () => {
    const actor = actorOrFail();
    const cur = store().proposal;
    if (!actor || !cur) return null;
    const p = await run('discarding', () => aiBuilderApi.discard(cur.id, actor, projectId));
    if (p) {
      store().setProposal(p);
      endStage();
    }
    return p;
  }, [actorOrFail, projectId, run]);

  const undo = useCallback(async (id?: string) => {
    const actor = actorOrFail();
    const target = id ?? store().proposal?.id;
    if (!actor || !target) return null;
    const p = await run('undoing', () => aiBuilderApi.undo(target, actor, projectId));
    if (p) {
      if (store().proposal?.id === p.id) store().setProposal(p);
      refreshAfterWorkflowChange(qc, p.workflow_id);
    }
    return p;
  }, [actorOrFail, projectId, qc, run]);

  /** §4.5: ask for a change to the result. Returns the new (child) proposal, already built. */
  const revise = useCallback(async (instruction: string) => {
    const actor = actorOrFail();
    const cur = store().proposal;
    if (!actor || !cur) return null;
    store().setPendingRequest(instruction);
    const p = await run('revising', () => aiBuilderApi.revise(cur.id, instruction, actor, projectId));
    if (p) store().setProposal(p);
    return p;
  }, [actorOrFail, projectId, run]);

  // ---- debounced spec saves: edits are sent 700 ms after the last keystroke
  const editVersion = useAiProposalStore((s) => s.editVersion);
  const status = useAiProposalStore((s) => s.proposal?.status);
  useEffect(() => {
    if (!editVersion || status !== 'spec_draft') return;
    const t = setTimeout(() => void saveSpecNow(), SPEC_SAVE_DELAY_MS);
    return () => clearTimeout(t);
  }, [editVersion, status, saveSpecNow]);

  return { load, draftSpec, answer, answerError, saveSpecNow, approveAndBuild, build, keep, discard, undo, revise, startPolling, stopPolling };
}
