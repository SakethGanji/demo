import { describe, expect, it } from 'vitest'
import { chatRefetchInterval, isTerminal, pollDelayMs, progressPercent, runHeadline, runRefetchInterval } from './polling'
import type { RunView } from './types'

const view = (p: Partial<RunView>): RunView => ({ execution_id: 'e', status: 'running', waiting_for: [], result: null, message: null, error: null, ...p })

describe('pollDelayMs', () => {
  it('backs off 0.5s -> 2s -> 10s', () => {
    expect(pollDelayMs(0)).toBe(500)
    expect(pollDelayMs(4999)).toBe(500)
    expect(pollDelayMs(5000)).toBe(2000)
    expect(pollDelayMs(119_999)).toBe(2000)
    expect(pollDelayMs(120_000)).toBe(10_000)
    expect(pollDelayMs(3 * 86_400_000)).toBe(10_000)
  })
})

describe('runRefetchInterval', () => {
  it('stops on terminal statuses only', () => {
    expect(runRefetchInterval(view({ status: 'success' }), 0)).toBe(false)
    expect(runRefetchInterval(view({ status: 'failed' }), 0)).toBe(false)
    expect(runRefetchInterval(view({ status: 'cancelled' }), 0)).toBe(false)
    expect(runRefetchInterval(view({ status: 'waiting' }), 200_000)).toBe(10_000)
    expect(runRefetchInterval(view({ status: 'running' }), 1000)).toBe(500)
    expect(runRefetchInterval(undefined, 1000)).toBe(500)
  })
  it('isTerminal', () => {
    expect(isTerminal('waiting')).toBe(false)
    expect(isTerminal('success')).toBe(true)
  })
})

describe('chat and headline', () => {
  it('chat polls faster while busy', () => {
    expect(chatRefetchInterval(true)).toBe(1000)
    expect(chatRefetchInterval(false)).toBe(10_000)
  })
  it('headline names the task being waited for', () => {
    expect(runHeadline(view({ status: 'waiting', waiting_for: [{ task_id: 't', title: 'Manager review', due_at: null }] }))).toBe('Waiting for Manager review')
    expect(runHeadline(view({ status: 'running', progress: { done: 1, total: 4, current: 'Classify' } }))).toBe('Running · Classify')
    expect(runHeadline(view({ status: 'success', message: 'Submitted' }))).toBe('Submitted')
  })
  it('progressPercent', () => {
    expect(progressPercent(view({ progress: { done: 3, total: 7 } }))).toBe(43)
    expect(progressPercent(view({}))).toBeNull()
  })
})
