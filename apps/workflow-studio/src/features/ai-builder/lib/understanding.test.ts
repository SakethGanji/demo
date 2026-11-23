import { describe, expect, it } from 'vitest';
import {
  answerPayload, answeredAnswers, answeredLine, chipTone, controlFor, dashKey, displayValue, hasAnswer, liveChips, nextOpen, openQuestions, progressOf,
  reusingItems, reusingOpenByDefault, compactLine, reusingText, roleHint, roleOptions, shownQuestions, slugOf, understoodChips,
} from './understanding';
import type { Understanding, UnderstandingConcept, UnderstandingQuestion } from '../types';

const concept = (key: string, label: string, score: number, o: Partial<UnderstandingConcept> = {}): UnderstandingConcept =>
  ({ key, label, score, source: 'lexical', evidence: `'${label}'`, confirmed: true, ...o });

const question = (id: string, o: Partial<UnderstandingQuestion> = {}): UnderstandingQuestion =>
  ({ id, concept: 'concept:approval', slot: 'approver_role', kind: 'text', text: '?', options: [], required: true, answer: null, ...o });

describe('slugOf / dashKey', () => {
  it('drops the kind prefix', () => {
    expect(slugOf('concept:approval')).toBe('approval');
    expect(slugOf('approval')).toBe('approval');
    expect(slugOf('tpl:customer-notify')).toBe('customer-notify');
  });
  it('writes keys the way kg-node ids do', () => {
    expect(dashKey('pattern:3f:9a')).toBe('pattern-3f-9a');
  });
});

describe('reusingText', () => {
  it('uses the counts exactly as the engine summarised them', () => {
    expect(reusingText({ subworkflows: 2, templates: 1, patterns: 1 })).toBe('2 sub-workflows · 1 template · 1 pattern');
  });
  it('pluralises each part on its own', () => {
    expect(reusingText({ subworkflows: 1, templates: 3, patterns: 0 })).toBe('1 sub-workflow · 3 templates');
    expect(reusingText({ subworkflows: 0, templates: 0, patterns: 2 })).toBe('2 patterns');
  });
  it('says so when nothing is reusable', () => {
    expect(reusingText({ subworkflows: 0, templates: 0, patterns: 0 })).toBe('Nothing reusable yet');
    expect(reusingText(null)).toBe('Nothing reusable yet');
  });
});

describe('chips', () => {
  it('tones: confirmed, implied (needed by another concept), suggested', () => {
    expect(chipTone({ confirmed: true, source: 'lexical' })).toBe('confirmed');
    expect(chipTone({ confirmed: true, source: 'llm' })).toBe('confirmed');
    expect(chipTone({ confirmed: false, source: 'implied' })).toBe('implied');
    expect(chipTone({ confirmed: false, source: 'lexical' })).toBe('suggested');
  });
  it('orders live chips by score, caps them and counts the rest', () => {
    const cs = Array.from({ length: 10 }, (_, i) => concept(`concept:c${i}`, `C${i}`, i / 10));
    const { shown, more } = liveChips(cs, 8);
    expect(shown).toHaveLength(8);
    expect(shown[0].key).toBe('concept:c9');
    expect(more).toBe(2);
    expect(liveChips(undefined)).toEqual({ shown: [], more: 0 });
  });
  it('matches the understood labels to their concept keys, in the engine order', () => {
    const u = { concepts: [concept('concept:sla', 'SLA', 0.9), concept('concept:intake', 'Intake', 0.8)], summary: { understood: ['Intake', 'SLA', 'Unknown thing'], reusing: { subworkflows: 0, templates: 0, patterns: 0 }, questions: 0 } };
    expect(understoodChips(u)).toEqual([
      { key: 'concept:intake', label: 'Intake' }, { key: 'concept:sla', label: 'SLA' }, { key: 'concept:unknown_thing', label: 'Unknown thing' },
    ]);
  });
});

describe('questions', () => {
  it('open = required and unanswered', () => {
    const u = { questions: [question('q_a.b'), question('q_c.d', { answer: '48h' }), question('q_shape', { required: false })] };
    expect(openQuestions(u).map((q) => q.id)).toEqual(['q_a.b']);
  });
  it('caps the required questions at four and keeps the optional shape choice', () => {
    const qs = [...Array.from({ length: 6 }, (_, i) => question(`q_x.s${i}`)), question('q_shape', { required: false })];
    const shown = shownQuestions({ questions: qs });
    expect(shown.filter((q) => q.required)).toHaveLength(4);
    expect(shown.at(-1)?.id).toBe('q_shape');
  });
  it('picks a control per kind', () => {
    expect(controlFor({ kind: 'role', options: [], multiple: false })).toBe('role');
    expect(controlFor({ kind: 'role', options: [], multiple: true })).toBe('role-multi');
    expect(controlFor({ kind: 'duration', options: ['4h'] })).toBe('duration');
    expect(controlFor({ kind: 'cron', options: [] })).toBe('cron');
    expect(controlFor({ kind: 'api_capability', options: ['api:x'] })).toBe('select');
    expect(controlFor({ kind: 'field', options: ['category', 'amount', 'region'] })).toBe('chips');
    expect(controlFor({ kind: 'choice', options: ['a', 'b', 'c', 'd', 'e', 'f', 'g'] })).toBe('select');
    expect(controlFor({ kind: 'field', options: ['a'], multiple: true })).toBe('chips-multi');
    expect(controlFor({ kind: 'text', options: [] })).toBe('text');
    expect(controlFor({ kind: 'choice', options: ['app', 'workflow'] })).toBe('chips');
  });
  it('counts progress over the blocking questions only', () => {
    const req = [question('q_a.b'), question('q_c.d')];
    expect(progressOf(req, new Set(['q_a.b']))).toEqual({ done: 1, total: 2, ratio: 0.5 });
    expect(progressOf([], new Set())).toEqual({ done: 0, total: 0, ratio: 1 });
  });
  it('moves to the next unanswered question, wrapping round', () => {
    const ids = ['a', 'b', 'c'];
    expect(nextOpen(ids, new Set(['a']))).toBe('b');
    expect(nextOpen(ids, new Set(['b']), 'b')).toBe('c');
    expect(nextOpen(ids, new Set(['b', 'c']), 'c')).toBe('a');
    expect(nextOpen(ids, new Set(ids), 'a')).toBeNull();
  });
});

describe('roles', () => {
  const u = {
    concepts: [concept('concept:escalation', 'Escalation', 0.9)],
    retrieval: {
      subworkflows: [], templates: [], patterns: [], apis: [], node_types: [],
      roles: [{ key: 'role:ops_manager', label: 'Ops manager', used_for: ['concept:escalation'], apps: 2 }, { key: 'role:analyst', label: 'Analyst', used_for: [], apps: 0 }],
    },
  } satisfies Pick<Understanding, 'concepts' | 'retrieval'>;
  it('lists roles used for this concept first, with their labels', () => {
    const q = { concept: 'concept:escalation', options: ['role:analyst', 'role:head', 'role:ops_manager'], option_labels: { 'role:head': 'Head of ops' } };
    expect(roleOptions(q, u).map((r) => r.label)).toEqual(['Ops manager', 'Analyst', 'Head of ops']);
  });
  it('states a fact about a role, never a score', () => {
    expect(roleHint('role:ops_manager', u)).toBe('Used for Escalation in 2 apps');
    expect(roleHint('role:analyst', u)).toBeNull();
    expect(roleHint(null, u)).toBeNull();
  });
});

describe('answers', () => {
  it('sends a new role as {new_role}', () => {
    expect(answerPayload({ kind: 'role' }, { new_role: '  Fraud lead ' })).toEqual({ new_role: 'Fraud lead' });
  });
  it('sends multiple slots as lists and trims text', () => {
    expect(answerPayload({ kind: 'field', multiple: true }, 'amount')).toEqual(['amount']);
    expect(answerPayload({ kind: 'field', multiple: true }, ['a', 'b'])).toEqual(['a', 'b']);
    expect(answerPayload({ kind: 'duration' }, ' 48h ')).toBe('48h');
    expect(answerPayload({ kind: 'cron' }, 'daily')).toBe('daily');
  });
  it('knows an empty answer', () => {
    expect(hasAnswer('')).toBe(false);
    expect(hasAnswer('  ')).toBe(false);
    expect(hasAnswer([])).toBe(false);
    expect(hasAnswer({ new_role: ' ' })).toBe(false);
    expect(hasAnswer({ new_role: 'Fraud lead' })).toBe(true);
    expect(hasAnswer('48h')).toBe(true);
    expect(hasAnswer(['x'])).toBe(true);
  });
  it('reads values back as people say them', () => {
    expect(displayValue({ kind: 'role', option_labels: { 'role:ops': 'Ops manager' } }, 'role:ops')).toBe('Ops manager');
    expect(displayValue({ kind: 'role' }, 'role:head_of_ops')).toBe('Head of ops');
    expect(displayValue({ kind: 'cron' }, '0 8 * * 1')).toBe('Mondays 08:00');
    expect(displayValue({ kind: 'cron' }, '5 4 * * *')).toBe('5 4 * * *');
    expect(displayValue({ kind: 'role' }, { new_role: 'Fraud lead' })).toBe('Fraud lead (new role)');
    expect(displayValue({ kind: 'field' }, ['amount', 'region'])).toBe('amount, region');
  });
  it('writes the settled line of a question', () => {
    const q = question('q_approval.approver_role', { kind: 'role', option_labels: { 'role:ops': 'Ops manager' } });
    expect(answeredLine(q, 'role:ops', 'Approval')).toBe('Approval · approver role: Ops manager');
    expect(answeredLine(q, 'role:ops')).toBe('Approval · approver role: Ops manager');
  });
});

describe('reusing items', () => {
  const base = {
    retrieval: {
      subworkflows: [{ key: 'wf:assign', label: 'Assign owner', why: 'implements Assignment' }],
      templates: [{ key: 'tpl:notify', label: 'Customer notify', why: 'implements Notification' }],
      patterns: [
        { key: 'pattern:a', label: 'Case management app', validated_count: 2, source_project: 'Payments Ops', adapt: true },
        { key: 'pattern:b', label: 'Refund triage', validated_count: 1, source_project: null, adapt: false },
      ],
      apis: [], node_types: [], roles: [],
    },
    adapt_from: { pattern: 'pattern:a', label: 'Case management app' },
  };
  it('lists sub-workflows, templates, then patterns, with the facts as the muted line', () => {
    const items = reusingItems(base);
    expect(items.map((i) => i.kind)).toEqual(['subworkflow', 'template', 'pattern', 'pattern']);
    expect(items[0].line).toBe('implements Assignment');
    expect(items[2].line).toBe('Payments Ops · validated ×2');
    expect(items[3].line).toBe('Your organisation · validated ×1');
  });
  it('marks only the pattern being adapted, and opens the list for it', () => {
    expect(reusingItems(base).filter((i) => i.adapting).map((i) => i.key)).toEqual(['pattern:a']);
    expect(reusingOpenByDefault(base)).toBe(true);
    expect(reusingOpenByDefault({ ...base, adapt_from: null, retrieval: { ...base.retrieval, patterns: [{ ...base.retrieval.patterns[1] }] } })).toBe(false);
  });
  it('writes the compact line from the summary and the answers', () => {
    const u = {
      summary: { understood: ['A', 'B', 'C'], reusing: { subworkflows: 1, templates: 1, patterns: 0 }, questions: 2 },
      questions: [question('q_a.b', { answer: '48h' }), question('q_c.d', { answer: 'x' }), question('q_shape', { required: false, answer: 'app' })],
    };
    expect(compactLine(u)).toBe('3 concepts · reusing 2 · 2 answered');
    expect(compactLine({ summary: { understood: ['A'], reusing: { subworkflows: 0, templates: 0, patterns: 0 }, questions: 0 }, questions: [] })).toBe('1 concept');
  });
});

describe('answeredAnswers', () => {
  it('keeps only what was answered, shape choice included, so an app build never asks it twice', () => {
    const u = { questions: [question('q_a.b', { answer: '48h' }), question('q_c.d'), question('q_shape', { required: false, answer: 'app' }), question('q_e.f', { answer: false })] };
    expect(answeredAnswers(u)).toEqual({ 'q_a.b': '48h', q_shape: 'app', 'q_e.f': false });
    expect(answeredAnswers(null)).toEqual({});
  });
});
