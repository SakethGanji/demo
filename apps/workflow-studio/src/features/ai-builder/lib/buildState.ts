/**
 * The sidebar's one-word state (PLAN-MOTION-UI §2.2), carried by `data-build-state` on the panel root.
 * Pure, so the whole flow ask -> thinking -> plan -> building -> review -> kept / discarded / undone can be tested.
 */
import type { BuilderBusy } from '../aiProposalStore';
import type { Proposal, ProposalStatus, Understanding } from '../types';

export type BuildState = 'ask' | 'thinking' | 'clarify' | 'plan' | 'building' | 'review' | 'kept' | 'discarded' | 'undone';

/** The states, in the order a build walks through them. */
export const BUILD_STATES: BuildState[] = ['ask', 'thinking', 'clarify', 'plan', 'building', 'review', 'kept', 'discarded', 'undone'];

export function buildStateOf(
  proposal: Pick<Proposal, 'status'> | null | undefined,
  busy: BuilderBusy,
  /** the canvas is still playing the build back: the result is not ready to review yet */
  canvasPlaying = false,
): BuildState {
  // writing the plan (or changing a result) is one long call with nothing to show but the wait
  if (busy === 'drafting' || busy === 'revising') return 'thinking';
  const status: ProposalStatus | undefined = proposal?.status;
  if (busy === 'building' || status === 'building') return 'building';
  switch (status) {
    case undefined: return 'ask';
    case 'clarify': return 'clarify';
    case 'spec_draft': case 'spec_approved': return 'plan';
    case 'kept': return 'kept';
    case 'discarded': return 'discarded';
    case 'undone': return 'undone';
    default: return canvasPlaying ? 'building' : 'review'; // ready, failed
  }
}

/** The steps of the PhaseBar. `understand` is only shown when the AI had questions (see `phasesFor`). */
export type Phase = 'describe' | 'understand' | 'plan' | 'build' | 'review';

const PHASE_LABEL: Record<Phase, string> = {
  describe: 'Describe', understand: 'Understand', plan: 'Check the plan', build: 'Build', review: 'Review',
};

/**
 * The PhaseBar's steps. Understand shows while the AI is asking and stays once it has asked (the plan and the build
 * come after it); a request with nothing to ask never shows it.
 */
export function phasesFor(
  proposal: { understanding?: Pick<Understanding, 'questions'> | null } | null | undefined,
  state: BuildState,
): Array<{ id: Phase; label: string }> {
  const asked = (proposal?.understanding?.questions ?? []).some((q) => q.required) || state === 'clarify';
  const ids: Phase[] = asked ? ['describe', 'understand', 'plan', 'build', 'review'] : ['describe', 'plan', 'build', 'review'];
  return ids.map((id) => ({ id, label: PHASE_LABEL[id] }));
}

export function phaseOfState(state: BuildState, status?: ProposalStatus | null): Phase {
  switch (state) {
    // sending the answers is still the Understand step: the pill must not jump back to Describe and forward again
    case 'thinking': return status === 'clarify' ? 'understand' : 'describe';
    case 'ask': return 'describe';
    case 'clarify': return 'understand';
    case 'plan': return 'plan';
    case 'building': return 'build';
    default: return 'review';
  }
}

/**
 * Which block of the sidebar is on screen. Switching blocks plays the slide transition; staying inside one
 * (review -> kept) does not.
 */
export type SidebarBlock = 'ask' | 'thinking' | 'clarify' | 'plan' | 'building' | 'review';
export function blockOf(state: BuildState): SidebarBlock {
  switch (state) {
    case 'kept': case 'discarded': case 'undone': return 'review';
    default: return state;
  }
}
