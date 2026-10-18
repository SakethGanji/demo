import { describe, expect, it } from 'vitest'
import { article, buildPrompt, labelOfKey, phrase, sectionTitle, sentence, slugOf } from './labels'

describe('labels', () => {
  it('reads a role requirement as a sentence with the right article', () => {
    expect(sentence('requires_permission', { label: 'Approval' }, { label: 'Ops manager' }, { slot: 'approver_role' })).toBe('Approval needs an approver role: Ops manager')
    expect(sentence('requires_permission', { label: 'Escalation' }, { label: 'Head of operations' }, { slot: 'target_role' })).toBe('Escalation needs a target role: Head of operations')
  })

  it('falls back to a plain sentence when a relation has no slot', () => {
    expect(sentence('requires_permission', { label: 'Report' }, { label: 'Ops analyst' })).toBe('Report needs the role Ops analyst')
    expect(sentence('requires', { label: 'Escalation' }, { label: 'SLA' })).toBe('Escalation needs SLA')
    expect(sentence('implements', { label: 'Approval node' }, { label: 'Approval' })).toBe('Approval node implements Approval')
  })

  it('has a phrase for both directions of every relation and never shows a raw name', () => {
    for (const rel of ['is_a', 'requires', 'implements', 'requires_permission', 'composes', 'produces', 'consumes', 'escalates_to', 'renders_with', 'validated_by', 'derived_from']) {
      for (const d of ['out', 'in']) {
        const p = phrase(rel, d)
        expect(p.length).toBeGreaterThan(2)
        expect(p).not.toContain('_')
      }
    }
    expect(phrase('implements', 'in')).toBe('is implemented by')
    expect(phrase('requires_permission', 'out', { slot: 'approver_role' })).toBe('needs approver role')
  })

  it('titles the sheet sections in sentence case', () => {
    expect(sectionTitle('implements', 'in')).toBe('Implemented by')
    expect(sectionTitle('composes', 'in')).toBe('Used in')
    expect(sectionTitle('requires_permission', 'out')).toBe('Requires roles')
    expect(sectionTitle('validated_by', 'out')).toBe('Validated by')
    expect(sectionTitle('derived_from', 'out')).toBe('Derived from')
    expect(sectionTitle('requires', 'out')).toBe('Needs')
    expect(sectionTitle('requires', 'in')).toBe('Needed by')
    expect(sectionTitle('escalates_to', 'out')).toBe('Escalates to')
  })

  it('picks a or an by sound', () => {
    expect(article('approver role')).toBe('an')
    expect(article('target role')).toBe('a')
  })

  it('builds the Build-with-this request and the key helpers', () => {
    expect(buildPrompt({ label: 'Approval', definition: 'A person decides.' })).toBe('Build a workflow for Approval. A person decides.')
    expect(buildPrompt({ label: 'SLA' })).toBe('Build a workflow for SLA.')
    expect(slugOf('concept:exception_case')).toBe('exception_case')
    expect(labelOfKey('concept:exception_case')).toBe('Exception case')
  })
})
