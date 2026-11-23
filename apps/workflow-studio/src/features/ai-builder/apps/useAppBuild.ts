/**
 * Drives one app build (CONTRACTS-CAPGRAPH §5.1): create -> answers -> plan -> approve plan -> approve each part's plan -> build
 * the parts one call at a time -> keep or discard. Network calls live here; the shape of the app lives in `AppBuildView`.
 *
 * The canvas follows the part on screen: the part's proposal is put in `aiProposalStore` (the store the stage player reads), and
 * while a part builds its events are polled into that store, so the existing stage player plays the build exactly as it does for
 * a single workflow. Between two parts the hook waits for the canvas to finish playing, so the next part never cuts it off.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { actorRequiredError, aiBuilderApi, getActorName, type AiUiError } from '../api';
import { useAiProposalStore } from '../aiProposalStore';
import { endStage } from '../lib/stageBridge';
import { useStageStore } from '../stage/stageStore';
import { motionOff } from '@/shared/motion/testFlag';
import { appApi, describeAppError } from './appApi';
import type { AppBuildView, PartView } from './appTypes';
import { approvableSpecs, nextPart } from './lib/appState';

export type AppBusy = 'idle' | 'creating' | 'answering' | 'approving' | 'specs' | 'building' | 'keeping' | 'discarding';

export const APP_POLL_MS = 750;
/** the longest the hook waits for the canvas to finish one part before it starts the next */
export const PLAYBACK_WAIT_MS = 9000;

const store = () => useAiProposalStore.getState();

// ---------------------------------------------------------------- which app belongs to which request (survives a reload)

const memory = new Map<string, string>();
const KEY = (hostId: string) => `studio.appBuild.${hostId}`;

export function readStoredApp(hostId: string): string | null {
  try {
    return localStorage.getItem(KEY(hostId));
  } catch {
    return memory.get(hostId) ?? null;
  }
}

export function storeApp(hostId: string, appId: string | null): void {
  if (appId) memory.set(hostId, appId);
  else memory.delete(hostId);
  try {
    if (appId) localStorage.setItem(KEY(hostId), appId);
    else localStorage.removeItem(KEY(hostId));
  } catch {
    /* private window: the link lasts for this page only */
  }
}

/** One create per request, even when React mounts the panel twice. */
const creating = new Map<string, Promise<AppBuildView>>();

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

/** Wait until the canvas has played the build back (or gave up being active). Instant under the test flag. */
export async function waitForPlayback(max = PLAYBACK_WAIT_MS): Promise<void> {
  if (motionOff()) return;
  const t0 = Date.now();
  await sleep(80);
  while (Date.now() - t0 < max) {
    const st = useStageStore.getState();
    if (!st.active || st.playback === 'done' || st.playback === 'idle') return;
    await sleep(120);
  }
}

export interface AppHost {
  /** the proposal the request started as (its id names the stored link), or the app build id itself */
  id: string;
  request: string;
  /** set when the proposal on screen is already a part of an app */
  appBuildId?: string | null;
  /** what the person already answered while the request was a single proposal, so the app does not ask it again */
  answers?: Record<string, unknown>;
}

export function useAppBuild(projectId: string, host: AppHost) {
  const qc = useQueryClient();
  const [app, setApp] = useState<AppBuildView | null>(null);
  const [busy, setBusy] = useState<AppBusy>('idle');
  const [error, setError] = useState<AiUiError | null>(null);
  const [answerError, setAnswerError] = useState<{ qid: string; message: string } | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const appRef = useRef<AppBuildView | null>(null);
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);

  const put = useCallback((v: AppBuildView | null) => {
    appRef.current = v;
    if (alive.current) setApp(v);
  }, []);

  const fail = useCallback((e: unknown) => {
    const err = describeAppError(e);
    if (alive.current) setError(err);
    return err;
  }, []);

  // ---- open: the app this request already has, or a new one

  const hostId = host.id;
  const hostRequest = host.request;
  const hostAnswers = useRef(host.answers);
  const known = host.appBuildId ?? null;
  useEffect(() => {
    let stop = false;
    void (async () => {
      const actor = getActorName();
      if (!actor) { setError(actorRequiredError()); return; }
      const existing = known ?? readStoredApp(hostId);
      if (existing) {
        try {
          const v = await appApi.get(existing, projectId);
          if (!stop) put(v);
          return;
        } catch {
          storeApp(hostId, null); // gone: start again from the request
        }
      }
      if (stop) return;
      setBusy('creating');
      try {
        let job = creating.get(hostId);
        if (!job) {
          job = appApi.create({ projectId, request: hostRequest, actor, answers: hostAnswers.current });
          creating.set(hostId, job);
        }
        const v = await job;
        storeApp(hostId, v.id);
        if (!stop) put(v);
        // the request started as one workflow's plan; the app replaced it, so that plan is not left open
        if (!known) void aiBuilderApi.discard(hostId, actor, projectId).catch(() => undefined);
      } catch (e) {
        creating.delete(hostId);
        if (!stop) fail(e);
      } finally {
        if (!stop) setBusy('idle');
      }
    })();
    return () => { stop = true; };
  }, [hostId, hostRequest, known, projectId, put, fail]);

  // ---- helpers

  const actorOrFail = useCallback((): string | null => {
    const a = getActorName();
    if (!a) setError(actorRequiredError());
    return a || null;
  }, []);

  const run = useCallback(async <T,>(b: AppBusy, fn: (actor: string) => Promise<T>): Promise<T | null> => {
    const actor = actorOrFail();
    if (!actor) return null;
    setBusy(b);
    setError(null);
    try {
      return await fn(actor);
    } catch (e) {
      fail(e);
      return null;
    } finally {
      if (alive.current) setBusy('idle');
    }
  }, [actorOrFail, fail]);

  const refresh = useCallback(async (): Promise<AppBuildView | null> => {
    const id = appRef.current?.id;
    if (!id) return null;
    try {
      const v = await appApi.get(id, projectId);
      put(v);
      return v;
    } catch (e) {
      fail(e);
      return null;
    }
  }, [projectId, put, fail]);

  /** Put one part's proposal on the canvas: a built part shows its final frame, a drafted one its outlines. */
  const showPart = useCallback(async (p: Pick<PartView, 'key' | 'proposal_id'>) => {
    setSelected(p.key);
    if (!p.proposal_id || useAiProposalStore.getState().busy === 'building') return;
    if (store().proposal?.id === p.proposal_id) return;
    try {
      store().setProposal(await aiBuilderApi.get(p.proposal_id, projectId));
    } catch {
      /* the row still shows its status */
    }
  }, [projectId]);

  const selectPart = useCallback((key: string | null) => {
    if (!key || key === selected) { setSelected(null); return; }
    const p = appRef.current?.parts.find((x) => x.key === key);
    if (p) void showPart(p);
  }, [selected, showPart]);

  // ---- the steps

  const answer = useCallback(async (answers: Record<string, unknown>) => {
    const cur = appRef.current;
    if (!cur) return null;
    setAnswerError(null);
    const actor = actorOrFail();
    if (!actor) return null;
    setBusy('answering');
    setError(null);
    try {
      const v = await appApi.answers(cur.id, answers, actor, projectId);
      put(v);
      return v;
    } catch (e) {
      const err = describeAppError(e);
      if (err.code === 'answer_invalid' && err.qid) setAnswerError({ qid: err.qid, message: err.message });
      else setError(err);
      return null;
    } finally {
      if (alive.current) setBusy('idle');
    }
  }, [actorOrFail, projectId, put]);

  const approvePlan = useCallback(async () => {
    const cur = appRef.current;
    if (!cur) return null;
    const v = await run('approving', (actor) => appApi.approvePlan(cur.id, actor, projectId));
    if (v) put(v);
    else void refresh(); // a part may have been drafted before the failure
    return v;
  }, [projectId, put, refresh, run]);

  /** Approve every drafted part plan with the existing proposal endpoint, one by one. */
  const approveAllSpecs = useCallback(async () => {
    const cur = appRef.current;
    if (!cur) return false;
    const ok = await run('specs', async (actor) => {
      for (const p of approvableSpecs(cur)) {
        await aiBuilderApi.approveSpec(p.proposal_id as string, actor, projectId);
        const now = appRef.current;
        if (now) put({ ...now, parts: now.parts.map((x) => (x.key === p.key ? { ...x, status: 'spec_approved' } : x)) });
      }
      return true;
    });
    await refresh();
    return !!ok;
  }, [projectId, put, refresh, run]);

  const pollEvents = useCallback(async (pid: string) => {
    try {
      const r = await aiBuilderApi.events(pid, store().lastSeq, projectId);
      store().appendEvents(r.items, r.last_seq);
    } catch {
      /* progress is a courtesy: the build call itself reports failures */
    }
  }, [projectId]);

  /** Build one part: the canvas follows it while the call runs. Resolves false when the part failed or the call was refused. */
  const buildOne = useCallback(async (target: PartView, actor: string): Promise<boolean> => {
    const cur = appRef.current;
    if (!cur) return false;
    setSelected(target.key);
    put({ ...cur, parts: cur.parts.map((x) => (x.key === target.key ? { ...x, status: 'building' } : x)) });
    const pid = target.proposal_id;
    let timer: ReturnType<typeof setInterval> | null = null;
    if (pid) {
      try {
        const p = await aiBuilderApi.get(pid, projectId);
        store().setProposal({ ...p, status: 'building' });
      } catch { /* the stage starts when the proposal arrives */ }
      store().resetEvents();
      store().setBusy('building');
      store().setPolling(true);
      void pollEvents(pid);
      timer = setInterval(() => void pollEvents(pid), APP_POLL_MS);
    }
    const stopPoll = () => { if (timer) clearInterval(timer); timer = null; store().setPolling(false); };
    try {
      const r = await appApi.buildNext(cur.id, actor, projectId);
      if (pid) {
        await pollEvents(pid);
        stopPoll();
        store().setProposal(await aiBuilderApi.get(pid, projectId));
        store().setBusy('idle');
      }
      put(r);
      const built = r.parts.find((x) => x.key === target.key);
      if (built?.status === 'failed') return false;
      await waitForPlayback();
      return true;
    } catch (e) {
      stopPoll();
      store().setBusy('idle');
      if (pid) { try { store().setProposal(await aiBuilderApi.get(pid, projectId)); } catch { /* keep what is shown */ } }
      void refresh();
      throw e;
    }
  }, [pollEvents, projectId, put, refresh]);

  /** Build every part that is left, in dependency order, one call each. Stops at the first part that fails. */
  const buildAll = useCallback(async () => {
    await run('building', async (actor) => {
      for (let guard = 0; guard < 24; guard++) {
        const now = appRef.current;
        if (!now || now.status === 'ready') break;
        const target = nextPart(now);
        if (!target || (target.status !== 'spec_approved' && target.status !== 'building')) break;
        if (!(await buildOne(target, actor))) break;
      }
      return true;
    });
    await refresh();
  }, [buildOne, refresh, run]);

  const handBackCanvas = useCallback(() => {
    store().setProposal(null);
    store().setBusy('idle');
    endStage();
  }, []);

  const keep = useCallback(async (acceptFailing = false) => {
    const cur = appRef.current;
    if (!cur) return null;
    const v = await run('keeping', (actor) => appApi.keep(cur.id, { actor, acceptFailing }, projectId));
    if (v) {
      put(v);
      handBackCanvas();
      void qc.invalidateQueries({ queryKey: ['workflows'] });
      void qc.invalidateQueries({ queryKey: ['project'] });
      void qc.invalidateQueries({ queryKey: ['kg'] });
    } else {
      void refresh(); // 409: the blockers may have changed
    }
    return v;
  }, [handBackCanvas, projectId, put, qc, refresh, run]);

  const discard = useCallback(async () => {
    const cur = appRef.current;
    if (!cur) return null;
    const v = await run('discarding', (actor) => appApi.discard(cur.id, actor, projectId));
    if (v) {
      put(v);
      handBackCanvas();
      storeApp(hostId, null);
    }
    return v;
  }, [handBackCanvas, hostId, projectId, put, run]);

  return { app, busy, error, answerError, selected, selectPart, answer, approvePlan, approveAllSpecs, buildAll, keep, discard, refresh };
}
