/**
 * The requirements checklist: answers as picked options plus free text, suggested picks preselected, grouping under
 * headings, and the cards rendered to static markup (chips as checkboxes or radios, the suggested mark, "Other", and
 * "Accept all suggestions").
 */
import { createElement, type ReactNode } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it, vi } from 'vitest'

vi.mock('@/shared/api/http', () => ({
  ApiError: class ApiError extends Error {},
  errorMessage: (e: unknown) => String(e),
  request: vi.fn(),
  isApiError: () => false,
  apiUrl: (p: string) => p,
  parseError: vi.fn(),
}))
vi.mock('@tanstack/react-router', () => ({
  Link: ({ children }: { children?: ReactNode }) => createElement('a', null, children),
}))

const { composeAnswer, groupByHeading, initialAnswers, parseAnswer, setOther, suggestedAnswers, toggleChoice, unanswered } = await import('./planLogic')
const { PlanQuestions, QuestionCard } = await import('./PlanQuestions')
import type { PlanQuestion } from './planTypes'

const roles: PlanQuestion = {
  id: 'req_roles',
  topic: 'roles',
  group: 'People and access',
  question: 'Who uses this app?',
  options: ['Admin', 'Member', 'Viewer'],
  suggested: ['Admin', 'Member'],
  default: 'Admin; Member',
  multi: true,
  other: true,
}
const seed: PlanQuestion = {
  id: 'req_seed',
  topic: 'seed',
  group: 'Data',
  question: 'What data should it start with?',
  options: ['Realistic sample data', 'Start empty'],
  suggested: ['Realistic sample data'],
  default: 'Realistic sample data',
  other: true,
}
const free: PlanQuestion = { id: 'q1', topic: 'safety', question: 'May prod mutate data?', default: 'no' }

describe('checklist answers', () => {
  it('start at the suggested picks, joined by "; "', () => {
    expect(initialAnswers([roles, seed, free])).toEqual({ req_roles: 'Admin; Member', req_seed: 'Realistic sample data', q1: 'no' })
    expect(suggestedAnswers([roles])).toEqual({ req_roles: 'Admin; Member' })
  })
  it('split into picked options and free text', () => {
    expect(parseAnswer(roles, 'Admin; Auditor; Member')).toEqual({ picked: ['Admin', 'Member'], other: 'Auditor' })
    expect(composeAnswer(['Admin'], ' Auditor ')).toBe('Admin; Auditor')
  })
  it('a multi question toggles an option, a single one swaps it and clears on a second click', () => {
    expect(toggleChoice(roles, 'Admin; Member', 'Viewer')).toBe('Admin; Member; Viewer')
    expect(toggleChoice(roles, 'Admin; Member', 'Admin')).toBe('Member')
    expect(toggleChoice(seed, 'Realistic sample data', 'Start empty')).toBe('Start empty')
    expect(toggleChoice(seed, 'Start empty', 'Start empty')).toBe('')
  })
  it('keeps the free text when options change and the options when the text changes', () => {
    expect(toggleChoice(roles, 'Admin; Auditor', 'Member')).toBe('Admin; Member; Auditor')
    expect(setOther(roles, 'Admin; Member', 'Auditor')).toBe('Admin; Member; Auditor')
    expect(setOther(roles, 'Admin; Member; Auditor', '')).toBe('Admin; Member')
  })
  it('an emptied checklist item is open again', () => {
    expect(unanswered([seed], { req_seed: toggleChoice(seed, 'Start empty', 'Start empty') })).toEqual(['req_seed'])
  })
  it('groups under the heading, else the topic', () => {
    expect(groupByHeading([roles, free, seed]).map((g) => g.heading)).toEqual(['People and access', 'Safety', 'Data'])
  })
})

describe('checklist cards', () => {
  const html = (q: PlanQuestion, answer: string) =>
    renderToStaticMarkup(createElement(QuestionCard, { q, n: 1, answer, confirmed: false, onAnswer: () => {}, onKeep: () => {} }))

  it('shows options as checkboxes (multi) or radios (single), marks the suggested ones and offers "Other"', () => {
    const m = html(roles, 'Admin; Member')
    expect(m).toContain('role="checkbox"')
    expect(m.match(/aria-checked="true"/g)).toHaveLength(2)
    expect(m.match(/data-suggested/g)).toHaveLength(2)
    expect(m).toContain('data-testid="plan-other-req_roles"')
    expect(html(seed, 'Start empty')).toContain('role="radio"')
  })
  it('a question without options stays a text answer', () => {
    const m = html(free, 'no')
    expect(m).toContain('data-testid="plan-answer-q1"')
    expect(m).not.toContain('role="checkbox"')
  })
  it('the panel offers "Accept all suggestions" next to Send answers', () => {
    const m = renderToStaticMarkup(
      createElement(PlanQuestions, { questions: [roles, seed], round: 1, pending: false, onSubmit: () => {}, onAcceptAll: () => {} }),
    )
    expect(m).toContain('data-testid="plan-accept-all"')
    expect(m).toContain('Accept all suggestions')
    expect(m).toContain('People and access')
    expect(m).toContain('0 of 2 answered')
  })
})
