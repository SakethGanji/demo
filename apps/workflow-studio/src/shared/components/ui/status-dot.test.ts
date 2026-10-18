import { describe, expect, it } from 'vitest'
import { runTone } from './status-dot'

describe('runTone', () => {
  it('a run waiting for approval is neither in flight nor failed', () => {
    expect(runTone('waiting')).toBe('warn')
  })
  it('keeps the other statuses', () => {
    expect(runTone('running')).toBe('running')
    expect(runTone('success')).toBe('ok')
    expect(runTone('failed')).toBe('err')
    expect(runTone('cancelled')).toBe('idle')
  })
})
