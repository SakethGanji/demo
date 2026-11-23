import { describe, expect, it } from 'vitest'
import { autotestFromBuild, autotestOf, checksLabel, failedChecks } from './autotest'

describe('autotest', () => {
  it('reads the payload and lists failures', () => {
    const a = autotestOf({ passed: 1, total: 2, results: [{ name: 'create', ok: true }, { name: 'list', ok: false, reason: '500' }] })!
    expect(checksLabel(a)).toBe('Checks 1/2 passing')
    expect(failedChecks(a)).toEqual([{ name: 'list', ok: false, reason: '500' }])
  })
  it('tolerates absence and junk', () => {
    expect(autotestOf(undefined)).toBeNull()
    expect(autotestOf('x')).toBeNull()
    expect(autotestOf({})).toBeNull()
    expect(autotestFromBuild({ report: null })).toBeNull()
  })
  it('derives counts from results and finds it on the report', () => {
    const a = autotestFromBuild({ report: { autotest: { results: [{ name: 'a', ok: true }, { name: 'b', ok: false }] } } })!
    expect([a.passed, a.total]).toEqual([1, 2])
  })
})
