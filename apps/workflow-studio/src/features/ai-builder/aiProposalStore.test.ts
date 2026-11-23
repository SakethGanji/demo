import { beforeEach, describe, expect, it } from 'vitest'
import { buildFinished, roundsFromEvents, specIsDirty, testResultsFromEvents, useAiProposalStore } from './aiProposalStore'
import type { AiEvent, Proposal, Spec } from './types'

const ev = (seq: number, event: string, payload: Record<string, unknown>): AiEvent => ({ seq, at: '2026-10-18T10:00:00Z', event, payload, actor: null })

const spec = (title = 'T'): Spec => ({
  spec_version: 1, title, summary: '', trigger: { kind: 'form', fields: [] }, rules: [], steps: [], interface: null,
  tests: [], rule_tests: {}, assumptions: [], questions: [],
})
const proposal = (over: Partial<Proposal> = {}): Proposal => ({ id: 'p_1', status: 'spec_draft', spec: spec(), ...over } as Proposal)

beforeEach(() => useAiProposalStore.getState().reset())

describe('roundsFromEvents', () => {
  it('folds one round of events into one row', () => {
    const rows = roundsFromEvents([
      ev(1, 'spec_requested', {}),
      ev(2, 'round_started', { round: 0 }),
      ev(3, 'ops_applied', { round: 0, ok: 18, rejected: 1, auto_fixed: 3 }),
      ev(4, 'checked', { round: 0, errors: 2, warnings: 1 }),
      ev(5, 'tested', { round: 0, passed: 1, failed: 1, errored: 1 }),
    ])
    expect(rows).toEqual([{ round: 0, applied: 18, rejected: 1, autoFixed: 3, errors: 2, warnings: 1, passed: 1, failed: 2, done: true, tests: [] }])
  })

  it('shows a round in flight with only what has reported', () => {
    const rows = roundsFromEvents([ev(1, 'round_started', { round: 0 }), ev(2, 'ops_applied', { round: 0, ok: 5, rejected: 0, auto_fixed: 0 })])
    expect(rows[0]).toMatchObject({ round: 0, applied: 5, errors: null, passed: null, done: false })
  })

  it('orders rounds and ignores events without a round', () => {
    const rows = roundsFromEvents([ev(1, 'round_started', { round: 1 }), ev(2, 'round_started', { round: 0 }), ev(3, 'llm_call', { task: 'build' })])
    expect(rows.map((r) => r.round)).toEqual([0, 1])
  })

  it('ignores round_shape for the rows and reads tested.results as the round\'s tests', () => {
    const results = [{ test_id: 't1', name: 'Small refund', status: 'passed', path: ['Start', 'Pay'], failed_nodes: [] }]
    const events = [
      ev(1, 'round_started', { round: 0 }),
      ev(2, 'round_shape', { round: 0, nodes: [{ name: 'Start', type: 'Start', position: { x: 0, y: 0 } }], connections: [], changed: [], removed: [] }),
      ev(3, 'tested', { round: 0, passed: 1, failed: 0, errored: 0, results }),
    ]
    const rows = roundsFromEvents(events)
    expect(rows).toHaveLength(1)
    expect(rows[0].tests).toEqual(results)
    expect(testResultsFromEvents(events)).toEqual(results)
    expect(testResultsFromEvents([ev(1, 'round_started', { round: 0 })])).toEqual([])
  })

  it('buildFinished watches for build_finished', () => {
    expect(buildFinished([ev(1, 'round_started', { round: 0 })])).toBe(false)
    expect(buildFinished([ev(2, 'build_finished', { status: 'ready' })])).toBe(true)
  })
})

describe('proposal store', () => {
  it('a new proposal clears the old events and edits', () => {
    const s = useAiProposalStore.getState()
    s.setProposal(proposal())
    s.appendEvents([ev(1, 'x', {})], 1)
    s.setProposal(proposal({ id: 'p_2' }))
    expect(useAiProposalStore.getState().events).toEqual([])
    expect(useAiProposalStore.getState().lastSeq).toBe(0)
  })

  it('editing marks the spec dirty; a saved answer for the latest edit clears it', () => {
    const s = useAiProposalStore.getState()
    s.setProposal(proposal())
    s.editSpec((x) => ({ ...x, summary: 'new' }))
    expect(specIsDirty(useAiProposalStore.getState())).toBe(true)
    const sent = useAiProposalStore.getState().editVersion
    s.applySavedProposal(proposal({ spec: { ...spec(), summary: 'new' } }), sent)
    expect(specIsDirty(useAiProposalStore.getState())).toBe(false)
    expect(useAiProposalStore.getState().specDraft?.summary).toBe('new')
  })

  it('a saved answer does not overwrite a newer edit', () => {
    const s = useAiProposalStore.getState()
    s.setProposal(proposal())
    s.editSpec((x) => ({ ...x, summary: 'one' }))
    const sent = useAiProposalStore.getState().editVersion
    s.editSpec((x) => ({ ...x, summary: 'two' }))
    s.applySavedProposal(proposal({ spec: { ...spec(), summary: 'one' } }), sent)
    expect(useAiProposalStore.getState().specDraft?.summary).toBe('two')
    expect(specIsDirty(useAiProposalStore.getState())).toBe(true)
  })

  it('refreshing the same plan keeps unsaved local edits', () => {
    const s = useAiProposalStore.getState()
    s.setProposal(proposal())
    s.editSpec((x) => ({ ...x, summary: 'typed' }))
    s.setProposal(proposal())
    expect(useAiProposalStore.getState().specDraft?.summary).toBe('typed')
  })

  it('once the plan is approved the server copy wins', () => {
    const s = useAiProposalStore.getState()
    s.setProposal(proposal())
    s.editSpec((x) => ({ ...x, summary: 'typed' }))
    s.setProposal(proposal({ status: 'spec_approved', spec: { ...spec(), summary: 'locked' } }))
    expect(useAiProposalStore.getState().specDraft?.summary).toBe('locked')
  })

  it('events are de-duplicated by sequence and the cursor only moves forward', () => {
    const s = useAiProposalStore.getState()
    s.appendEvents([ev(1, 'a', {}), ev(2, 'b', {})], 2)
    s.appendEvents([ev(2, 'b', {}), ev(3, 'c', {})], 3)
    s.appendEvents([], 1)
    const st = useAiProposalStore.getState()
    expect(st.events.map((e) => e.seq)).toEqual([1, 2, 3])
    expect(st.lastSeq).toBe(3)
  })

  it('editSpec without a plan does nothing', () => {
    useAiProposalStore.getState().editSpec((x) => ({ ...x, title: 'z' }))
    expect(useAiProposalStore.getState().editVersion).toBe(0)
  })
})
