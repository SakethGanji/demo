/**
 * State of the proposal on screen (DESIGN-STAGE6 §9.1): the current proposal, the progress-events cursor, the
 * polling flag and the local, not yet saved edits of the spec. No network here: `useAiBuilder` drives the calls.
 */
import { create } from 'zustand';
import type { AiUiError } from './api';
import type { AiEvent, Proposal, Spec } from './types';
import type { TestResult } from './stage/buildOps';
import { parseTestResults } from './stage/opsFromEvents';

export type BuilderBusy =
  | 'idle' | 'drafting' | 'saving' | 'approving' | 'building' | 'revising' | 'keeping' | 'discarding' | 'undoing';

export interface RoundProgress {
  round: number;
  /** operations the AI proposed that the server applied / refused / repaired by itself */
  applied: number | null;
  rejected: number | null;
  autoFixed: number | null;
  errors: number | null;
  warnings: number | null;
  passed: number | null;
  failed: number | null;
  /** all of this round's steps have reported */
  done: boolean;
  /** one entry per test the round ran (empty for an engine that does not send them) */
  tests: TestResult[];
}

const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null);

/** Folds the audit events into one row per round (round_started, ops_applied, checked, tested). */
export function roundsFromEvents(events: AiEvent[]): RoundProgress[] {
  const rounds = new Map<number, RoundProgress>();
  const get = (n: number): RoundProgress => {
    let r = rounds.get(n);
    if (!r) {
      r = { round: n, applied: null, rejected: null, autoFixed: null, errors: null, warnings: null, passed: null, failed: null, done: false, tests: [] };
      rounds.set(n, r);
    }
    return r;
  };
  for (const e of events) {
    const n = num(e.payload.round);
    if (n === null) continue;
    if (e.event === 'round_started') get(n);
    else if (e.event === 'ops_applied') {
      const r = get(n);
      r.applied = num(e.payload.ok);
      r.rejected = num(e.payload.rejected);
      r.autoFixed = num(e.payload.auto_fixed);
    } else if (e.event === 'checked') {
      const r = get(n);
      r.errors = num(e.payload.errors);
      r.warnings = num(e.payload.warnings);
    } else if (e.event === 'tested') {
      const r = get(n);
      r.passed = num(e.payload.passed);
      r.failed = (num(e.payload.failed) ?? 0) + (num(e.payload.errored) ?? 0);
      r.done = true;
      r.tests = parseTestResults(e.payload);
    }
  }
  return [...rounds.values()].sort((a, b) => a.round - b.round);
}

/** The tests of the newest round that reported any (what the sidebar ticks off and the canvas pulses). */
export function testResultsFromEvents(events: AiEvent[]): TestResult[] {
  const rounds = roundsFromEvents(events);
  for (let i = rounds.length - 1; i >= 0; i--) if (rounds[i].tests.length) return rounds[i].tests;
  return [];
}

/** True when the events show the build loop has ended. */
export function buildFinished(events: AiEvent[]): boolean {
  return events.some((e) => e.event === 'build_finished');
}

interface AiProposalState {
  proposal: Proposal | null;
  /** the spec as the person is editing it (differs from `proposal.spec` while edits are unsaved) */
  specDraft: Spec | null;
  /** bumped on every local edit; a saved answer only replaces the draft when no newer edit exists */
  editVersion: number;
  events: AiEvent[];
  lastSeq: number;
  polling: boolean;
  /** what was just asked, while the plan is being written (and after a failure, so the box can be refilled) */
  pendingRequest: string | null;
  busy: BuilderBusy;
  /** a node the review asked the canvas to bring into view */
  focusNode: string | null;
  /** the last failure, in plain English (`ai-error`) */
  error: AiUiError | null;

  setProposal: (p: Proposal | null) => void;
  /** a saved answer for the spec: replaces the draft only if `sentVersion` is still the latest edit */
  applySavedProposal: (p: Proposal, sentVersion: number) => void;
  editSpec: (update: (s: Spec) => Spec) => void;
  appendEvents: (items: AiEvent[], lastSeq: number) => void;
  resetEvents: () => void;
  setPolling: (on: boolean) => void;
  setPendingRequest: (text: string | null) => void;
  setBusy: (b: BuilderBusy) => void;
  setFocusNode: (name: string | null) => void;
  setError: (e: AiUiError | null) => void;
  reset: () => void;
}

const initial = {
  proposal: null, specDraft: null, editVersion: 0, events: [] as AiEvent[], lastSeq: 0, polling: false, pendingRequest: null as string | null,
  busy: 'idle' as BuilderBusy, focusNode: null as string | null, error: null as AiUiError | null,
};

export const useAiProposalStore = create<AiProposalState>((set, get) => ({
  ...initial,

  setProposal: (p) => set((s) => {
    const same = !!p && s.proposal?.id === p.id;
    // unsaved local edits of the same plan survive a refresh of the proposal
    const keepEdits = same && s.editVersion > 0 && p.status === 'spec_draft';
    return {
      proposal: p,
      specDraft: keepEdits ? s.specDraft : (p?.spec ?? null),
      ...(p ? { pendingRequest: null } : {}),
      ...(same ? {} : { events: [], lastSeq: 0, editVersion: 0, focusNode: null }),
    };
  }),

  applySavedProposal: (p, sentVersion) => set((s) => ({
    proposal: p,
    specDraft: s.editVersion === sentVersion ? p.spec : s.specDraft,
  })),

  editSpec: (update) => {
    const cur = get().specDraft;
    if (!cur) return;
    set((s) => ({ specDraft: update(cur), editVersion: s.editVersion + 1 }));
  },

  appendEvents: (items, lastSeq) => set((s) => {
    const seen = new Set(s.events.map((e) => e.seq));
    const fresh = items.filter((e) => !seen.has(e.seq));
    return { events: fresh.length ? [...s.events, ...fresh] : s.events, lastSeq: Math.max(s.lastSeq, lastSeq) };
  }),

  resetEvents: () => set({ events: [], lastSeq: 0 }),
  setPolling: (polling) => set({ polling }),
  setPendingRequest: (pendingRequest) => set({ pendingRequest }),
  setBusy: (busy) => set({ busy }),
  setFocusNode: (focusNode) => set({ focusNode }),
  setError: (error) => set({ error }),
  reset: () => set({ ...initial }),
}));

/** True while the local spec has edits the server has not confirmed. */
export function specIsDirty(s: Pick<AiProposalState, 'proposal' | 'specDraft'>): boolean {
  return !!s.specDraft && !!s.proposal?.spec && s.specDraft !== s.proposal.spec;
}
