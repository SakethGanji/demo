import { describe, expect, it } from 'vitest'
import { applyLiveEvent, EMPTY_LIVE } from '@/features/plan/liveBuild'
import { liveEventOf, planRevisionOf } from './chatEvents'

describe('liveEventOf', () => {
  it('reads the file, route and preview events the app chat streams (the same shapes as a build) for the live reducer', () => {
    const file = liveEventOf({ type: 'file', builder: 'backend', path: 'backend/runs.py', op: 'write', content: 'x', done: false })
    expect(file).toEqual({ type: 'file', step_key: null, builder: 'backend', path: 'backend/runs.py', op: 'write', content: 'x', done: false, truncated: false })
    expect(liveEventOf({ type: 'route', method: 'GET', path: '/runs', name: 'list', status: 'built' })).toEqual({
      type: 'route',
      step_key: null,
      method: 'GET',
      path: '/runs',
      name: 'list',
      status: 'built',
    })
    expect(liveEventOf({ type: 'preview', app_id: 'app_1', version_id: 'v2' })).toEqual({ type: 'preview', app_id: 'app_1', version_id: 'v2' })
    let s = EMPTY_LIVE
    for (const d of [{ type: 'file', builder: 'ui', path: 'apps/w/App.tsx', op: 'write', content: 'a', done: true }]) s = applyLiveEvent(s, liveEventOf(d)!)
    expect(s.newest).toBe('apps/w/App.tsx')
    expect(s.files['apps/w/App.tsx'].builder).toBe('ui')
  })

  it('ignores chat events and malformed ones', () => {
    expect(liveEventOf({ type: 'text', content: 'hi' })).toBeNull()
    expect(liveEventOf({ type: 'file', builder: 'backend' })).toBeNull()
    expect(liveEventOf({ type: 'file', builder: 'robot', path: 'x', op: 'write', content: '' })).toBeNull()
    expect(liveEventOf({ type: 'route', method: 'GET', path: '/x', status: 'weird' })).toBeNull()
    expect(liveEventOf({ type: 'preview' })).toBeNull()
  })
})

describe('planRevisionOf', () => {
  it('reads a plan revision the ask needs before the builders can go on', () => {
    const diff = { changes: [{ kind: 'table', key: 'tags', change: 'added', breaking: false }], breaking: false, affected: { modules: [], apps: [], tables: ['tags'] } }
    expect(planRevisionOf({ type: 'plan_revision', version: 4, diff, reason: 'needs a tags table' })).toEqual({ version: 4, diff, reason: 'needs a tags table' })
    expect(planRevisionOf({ type: 'plan_revision', version: 4 })).toEqual({ version: 4, diff: null, reason: null })
    expect(planRevisionOf({ type: 'plan_revision' })).toBeNull()
    expect(planRevisionOf({ type: 'text' })).toBeNull()
  })
})
