import { describe, expect, it } from 'vitest';
import { blockOf, buildStateOf, phaseOfState, phasesFor, BUILD_STATES, type BuildState } from './buildState';
import type { ProposalStatus } from '../types';

const p = (status: ProposalStatus) => ({ status });

describe('buildStateOf', () => {
  it('is ask until a request is sent', () => {
    expect(buildStateOf(null, 'idle')).toBe('ask');
  });
  it('is thinking while the plan is written or a result is revised', () => {
    expect(buildStateOf(null, 'drafting')).toBe('thinking');
    expect(buildStateOf(p('ready'), 'revising')).toBe('thinking');
  });
  it('walks plan -> building -> review', () => {
    expect(buildStateOf(p('spec_draft'), 'idle')).toBe('plan');
    expect(buildStateOf(p('spec_approved'), 'approving')).toBe('plan');
    expect(buildStateOf(p('spec_approved'), 'building')).toBe('building');
    expect(buildStateOf(p('building'), 'idle')).toBe('building');
    expect(buildStateOf(p('ready'), 'idle')).toBe('review');
    expect(buildStateOf(p('failed'), 'idle')).toBe('review');
    expect(buildStateOf(p('ready'), 'keeping')).toBe('review');
  });
  it('waits in clarify while the AI has questions, and goes back to thinking while the answers are sent', () => {
    expect(buildStateOf(p('clarify'), 'idle')).toBe('clarify');
    expect(buildStateOf(p('clarify'), 'drafting')).toBe('thinking');
    expect(buildStateOf(p('spec_draft'), 'idle')).toBe('plan'); // no questions: straight to the plan
    expect(BUILD_STATES).toContain('clarify');
  });
  it('names the ends of a build', () => {
    expect(buildStateOf(p('kept'), 'idle')).toBe('kept');
    expect(buildStateOf(p('discarded'), 'idle')).toBe('discarded');
    expect(buildStateOf(p('undone'), 'idle')).toBe('undone');
  });
});

describe('phases and blocks', () => {
  it('maps states to the four phases', () => {
    const phases = (['ask', 'thinking', 'plan', 'building', 'review', 'kept'] as BuildState[]).map(phaseOfState);
    expect(phases).toEqual(['describe', 'describe', 'plan', 'build', 'review', 'review']);
  });
  it('keeps kept / discarded / undone in the review block so they do not slide', () => {
    expect(blockOf('kept')).toBe('review');
    expect(blockOf('undone')).toBe('review');
    expect(blockOf('thinking')).toBe('thinking');
  });
});

describe('the Understand phase', () => {
  const q = (required: boolean) => ({ id: 'q_x.y', concept: 'concept:x', slot: 'y', kind: 'text' as const, text: '?', options: [], required, answer: null });
  const ids = (proposal: Parameters<typeof phasesFor>[0], state: BuildState) => phasesFor(proposal, state).map((x) => x.id);

  it('maps clarify to understand, and keeps sending the answers inside it', () => {
    expect(phaseOfState('clarify')).toBe('understand');
    expect(phaseOfState('thinking', 'clarify')).toBe('understand');
    expect(phaseOfState('thinking', null)).toBe('describe');
    expect(phaseOfState('thinking')).toBe('describe');
    expect(blockOf('clarify')).toBe('clarify');
  });
  it('is hidden when the AI had nothing to ask', () => {
    expect(ids(null, 'ask')).toEqual(['describe', 'plan', 'build', 'review']);
    expect(ids({ understanding: { questions: [] } }, 'plan')).toEqual(['describe', 'plan', 'build', 'review']);
    // only the optional app-or-workflow choice: nothing blocked, so no step
    expect(ids({ understanding: { questions: [q(false)] } }, 'plan')).toEqual(['describe', 'plan', 'build', 'review']);
  });
  it('shows while clarifying, and stays once the AI has asked', () => {
    expect(ids(null, 'clarify')).toEqual(['describe', 'understand', 'plan', 'build', 'review']);
    expect(ids({ understanding: { questions: [q(true)] } }, 'plan')).toEqual(['describe', 'understand', 'plan', 'build', 'review']);
    expect(ids({ understanding: { questions: [q(true)] } }, 'review')).toContain('understand');
  });
});
