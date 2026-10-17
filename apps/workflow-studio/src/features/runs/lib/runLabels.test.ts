import { describe, expect, it } from 'vitest'
import type { RunDetail, RunListItem } from '@/shared/api/types'
import { isRunning, runError, runSource, runTarget, shortId, triggerType } from './runLabels'

const item = (o: Record<string, unknown>) => o as unknown as RunListItem

describe('runTarget', () => {
  it('prefers the trigger method and path', () => {
    expect(runTarget(item({ trigger: { method: 'GET', path: '/refunds' } }))).toEqual({ method: 'GET', path: '/refunds', workflow: null })
  })
  it('falls back to "METHOD /path" in the route field', () => {
    expect(runTarget(item({ route: 'POST /refunds/{id}' }))).toEqual({ method: 'POST', path: '/refunds/{id}', workflow: null })
  })
  it('is a workflow run otherwise', () => {
    expect(runTarget(item({ workflow_name: 'Refund triage' })).workflow).toBe('Refund triage')
    expect(runTarget(item({ workflow_id: 'wf_1' })).workflow).toBe('wf_1')
  })
})

describe('triggerType / runSource', () => {
  it('reads the trigger type, then the mode', () => {
    expect(triggerType(item({ trigger: { type: 'cron' } }))).toBe('cron')
    expect(triggerType(item({ mode: 'manual' }))).toBe('manual')
    expect(triggerType(item({}))).toBe('unknown')
  })
  it('names where a run came from', () => {
    const apps = (id: string) => (id === 'app_1' ? 'Orders' : undefined)
    expect(runSource(item({ trigger: { type: 'app' }, app_id: 'app_1' }), apps)).toBe('app · Orders')
    expect(runSource(item({ trigger: { type: 'app_preview' }, app_id: 'gone' }), apps)).toBe('preview · app')
    expect(runSource(item({ trigger: { type: 'route_test' } }), apps)).toBe('test · studio')
    expect(runSource(item({ trigger: { type: 'draft_test', module_name: 'refunds' } }), apps)).toBe('draft test · refunds.py')
    expect(runSource(item({ trigger: { type: 'draft_test' } }), apps)).toBe('draft test')
    expect(runSource(item({ trigger: { type: 'generate_self_test' } }), apps)).toBe('AI self-test')
    expect(runSource(item({ trigger: { type: 'cron' } }), apps)).toBe('schedule')
    expect(runSource(item({ trigger: { type: 'api' } }), apps)).toBe('router')
    expect(runSource(item({ trigger: { type: 'weird' }, mode: 'x' }), apps)).toBe('x')
  })
})

describe('runError', () => {
  const detail = (o: Record<string, unknown>) => o as unknown as RunDetail
  it('prefers metadata.error, then the trace error, then the first node error', () => {
    expect(runError(detail({ metadata: { error: { type: 'A', message: 'm1' } }, trace: { error: { type: 'B', message: 'm2' } } }))).toMatchObject({ type: 'A' })
    expect(runError(detail({ metadata: {}, trace: { error: { type: 'B', message: 'm2' } } }))).toMatchObject({ type: 'B' })
    expect(runError(detail({ metadata: {}, trace: null, errors: [{ node_name: 'Set', error: 'boom', error_code: 'x' }] }))).toEqual({ type: 'Set', message: 'boom', code: 'x' })
    expect(runError(detail({ metadata: {}, trace: null, errors: [] }))).toBeNull()
  })
})

describe('isRunning / shortId', () => {
  it('counts running and waiting as in flight', () => {
    expect(isRunning('running')).toBe(true)
    expect(isRunning('waiting')).toBe(true)
    expect(isRunning('success')).toBe(false)
    expect(isRunning(null)).toBe(false)
  })
  it('shortens long ids around the prefix and keeps short ones', () => {
    expect(shortId('exec_01a1b2c3d4e5f6c41d')).toBe('exec_01a1…c41d')
    expect(shortId('exec_short')).toBe('exec_short')
    expect(shortId('0123456789abcdef0123')).toBe('0123…0123')
  })
})
