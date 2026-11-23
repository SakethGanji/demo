import { describe, expect, it } from 'vitest'
import { APPROVAL_PORTS, approvalItemsPatch, isApprovalType, sampleApprovalItems } from './approvalMocks'

describe('approval mocks', () => {
  it('has the three ports in contract order', () => {
    expect([...APPROVAL_PORTS]).toEqual(['approved', 'rejected', 'timed_out'])
    expect(isApprovalType('Approval')).toBe(true)
    expect(isApprovalType('HttpRequest')).toBe(false)
  })
  it('sample items carry the decision', () => {
    expect(JSON.parse(sampleApprovalItems('rejected'))[0].approval.decision).toBe('rejected')
    expect(JSON.parse(sampleApprovalItems('timed_out'))[0].approval.decision).toBe('timed_out')
  })
  it('prefills only when empty and defaults the port to approved', () => {
    expect(approvalItemsPatch('', '').port).toBe('approved')
    expect(JSON.parse(approvalItemsPatch('', '').text)[0].approval.decision).toBe('approved')
    expect(approvalItemsPatch('[{"x":1}]', 'rejected')).toEqual({ text: '[{"x":1}]', port: 'rejected' })
  })
})
