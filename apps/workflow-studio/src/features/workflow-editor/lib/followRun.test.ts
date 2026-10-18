import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const state = vi.hoisted(() => ({
  workflowId: 'wf-1',
  viewingExecution: null as { id: string } | null,
  loadExecutionData: vi.fn((d: { id: string }) => { state.viewingExecution = { id: d.id } }),
}))
const get = vi.hoisted(() => vi.fn())
vi.mock('@/shared/lib/api', () => ({ executionsApi: { get } }))
vi.mock('../stores/workflowStore', () => ({ useWorkflowStore: { getState: () => state } }))

import { followRunOnCanvas, stillRunning } from './followRun'

const detail = (status: string, id = 'e1', workflow_id = 'wf-1') => ({ id, status, workflow_id })

beforeEach(() => {
  vi.useFakeTimers()
  get.mockReset()
  state.loadExecutionData.mockClear()
  state.viewingExecution = null
  state.workflowId = 'wf-1'
})
afterEach(() => vi.useRealTimers())

describe('followRunOnCanvas', () => {
  it('re-reads the run while it runs and stops once it settles', async () => {
    get.mockResolvedValueOnce(detail('running')).mockResolvedValueOnce(detail('running')).mockResolvedValue(detail('success'))
    followRunOnCanvas('e1')
    await vi.runAllTimersAsync()
    expect(get).toHaveBeenCalledTimes(3)
    expect(state.loadExecutionData).toHaveBeenCalledTimes(3)
    expect(state.viewingExecution).toEqual({ id: 'e1' })
  })

  it('stops when the author goes back to live', async () => {
    get.mockResolvedValue(detail('running'))
    followRunOnCanvas('e1')
    await vi.advanceTimersByTimeAsync(0)
    expect(get).toHaveBeenCalledTimes(1)
    state.viewingExecution = null
    await vi.runAllTimersAsync()
    expect(get).toHaveBeenCalledTimes(1)
  })

  it('following a new run stops the old one', async () => {
    get.mockImplementation(async (id: string) => detail('running', id))
    followRunOnCanvas('e1')
    await vi.advanceTimersByTimeAsync(0)
    const stop = followRunOnCanvas('e2')
    await vi.advanceTimersByTimeAsync(2000)
    stop()
    expect(get.mock.calls.filter(([id]) => id === 'e1')).toHaveLength(1)
    expect(get.mock.calls.filter(([id]) => id === 'e2').length).toBeGreaterThan(1)
  })

  it('never paints a run of another workflow, and gives up after repeated misses', async () => {
    get.mockResolvedValueOnce(detail('running', 'e1', 'other'))
    followRunOnCanvas('e1')
    await vi.runAllTimersAsync()
    expect(state.loadExecutionData).not.toHaveBeenCalled()

    get.mockReset().mockRejectedValue(new Error('404'))
    followRunOnCanvas('e3')
    await vi.runAllTimersAsync()
    expect(get).toHaveBeenCalledTimes(6)
  })

  it('treats only queued/running as still running', () => {
    expect(stillRunning('running')).toBe(true)
    expect(stillRunning('queued')).toBe(true)
    for (const s of ['success', 'failed', 'error', 'waiting', 'cancelled', undefined]) expect(stillRunning(s)).toBe(false)
  })
})
