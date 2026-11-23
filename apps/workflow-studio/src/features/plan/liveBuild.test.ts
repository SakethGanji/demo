import { describe, expect, it } from 'vitest'
import { applyBuildEvent } from './buildLogic'
import type { Build, BuildEvent } from './buildTypes'
import { applyLiveEvent, EMPTY_LIVE, liveRoutes, mergeLive, routeCounts, writtenPaths, type LiveState } from './liveBuild'

const file = (path: string, content: string, extra: Partial<Extract<BuildEvent, { type: 'file' }>> = {}): BuildEvent => ({
  type: 'file',
  step_key: 'module:runs',
  builder: 'backend',
  path,
  op: 'write',
  content,
  done: true,
  truncated: false,
  ...extra,
})
const route = (method: string, path: string, status: 'stub' | 'built' | 'passing' | 'failing', step_key: string | null = 'module:runs'): BuildEvent => ({
  type: 'route',
  step_key,
  method,
  path,
  name: `${method.toLowerCase()}_${path}`,
  status,
})
const run = (events: BuildEvent[], at = 1000): LiveState => events.reduce((s, e, n) => applyLiveEvent(s, e, at + n), EMPTY_LIVE)

describe('applyLiveEvent: files', () => {
  it('keeps every file written this build, the newest last', () => {
    const s = run([file('PLAN.md', '# Plan', { builder: 'plan', step_key: null }), file('backend/runs.py', 'x = 1'), file('backend/tables/runs.json', '{}', { builder: 'tables' })])
    expect(writtenPaths(s)).toEqual(['PLAN.md', 'backend/runs.py', 'backend/tables/runs.json'])
    expect(s.newest).toBe('backend/tables/runs.json')
    expect(s.files['backend/runs.py']).toMatchObject({ builder: 'backend', step_key: 'module:runs', content: 'x = 1', done: true, writes: 1 })
  })

  it('a draft then the saved source replace the content and count as writes of the same file', () => {
    const s = run([file('backend/runs.py', 'def a', { done: false }), file('backend/runs.py', 'def a():\n  pass', { done: true }), file('PLAN.md', '#')])
    expect(s.files['backend/runs.py']).toMatchObject({ content: 'def a():\n  pass', done: true, writes: 2 })
    expect(writtenPaths(s)).toEqual(['backend/runs.py', 'PLAN.md'])
    // writing an older file again makes it the newest
    const t = applyLiveEvent(s, file('backend/runs.py', 'v3'), 5000)
    expect(t.newest).toBe('backend/runs.py')
    expect(t.files['backend/runs.py'].at).toBe(5000)
  })

  it('a delete marks the file deleted (empty content) and is never the newest', () => {
    const s = run([file('apps/w/App.tsx', 'a', { builder: 'ui' }), file('apps/w/old.tsx', 'b', { builder: 'ui' }), file('apps/w/old.tsx', '', { op: 'delete', builder: 'ui' })])
    expect(s.files['apps/w/old.tsx']).toMatchObject({ op: 'delete', content: '' })
    expect(s.newest).toBe('apps/w/App.tsx')
    expect(writtenPaths(s)).toEqual(['apps/w/App.tsx'])
  })

  it('keeps the truncated flag (missing on older engines counts as false)', () => {
    const s = run([file('apps/w/big.ts', 'x', { truncated: true }), { type: 'file', step_key: null, builder: 'ui', path: 'a.ts', op: 'write', content: '', done: true } as unknown as BuildEvent])
    expect(s.files['apps/w/big.ts'].truncated).toBe(true)
    expect(s.files['a.ts'].truncated).toBe(false)
  })
})

describe('applyLiveEvent: routes', () => {
  it('builds a status map in announcement order, the latest status winning', () => {
    const s = run([route('GET', '/runs', 'stub'), route('POST', '/runs', 'stub'), route('GET', '/runs', 'built'), route('GET', '/runs', 'failing'), route('GET', '/runs', 'passing')])
    expect(liveRoutes(s).map((r) => [r.key, r.status])).toEqual([
      ['GET /runs', 'passing'],
      ['POST /runs', 'stub'],
    ])
  })

  it('a late stub never downgrades a route that is already built', () => {
    const s = run([route('GET', '/runs', 'built'), route('GET', '/runs', 'stub')])
    expect(liveRoutes(s)[0].status).toBe('built')
  })

  it('a late joiner (no route events) sees the planned routes: built when their module step passed, else stub', () => {
    const build = { steps: [{ key: 'module:runs', status: 'passed' }, { key: 'module:bugs', status: 'running' }] } as unknown as Build
    const planned = [
      { key: 'GET /runs', module: 'runs' },
      { key: 'POST /bugs', module: 'bugs' },
    ]
    expect(liveRoutes(EMPTY_LIVE, planned, build).map((r) => [r.key, r.method, r.path, r.status, r.step_key])).toEqual([
      ['GET /runs', 'GET', '/runs', 'built', 'module:runs'],
      ['POST /bugs', 'POST', '/bugs', 'stub', 'module:bugs'],
    ])
    // what the stream said wins over the plan
    const s = run([route('POST', '/bugs', 'passing', 'module:bugs')])
    expect(liveRoutes(s, planned, build).map((r) => [r.key, r.status])).toEqual([
      ['POST /bugs', 'passing'],
      ['GET /runs', 'built'],
    ])
  })

  it('counts routes per status', () => {
    const s = run([route('GET', '/a', 'stub'), route('GET', '/b', 'built'), route('GET', '/c', 'passing'), route('GET', '/d', 'failing'), route('GET', '/e', 'passing')])
    expect(routeCounts(liveRoutes(s))).toEqual({ stub: 1, built: 1, passing: 2, failing: 1, total: 5 })
  })
})

describe('applyLiveEvent: preview', () => {
  it('counts preview reloads and keeps the latest app and version', () => {
    const s = run([{ type: 'preview', app_id: 'app_1', version_id: null }, { type: 'preview', app_id: 'app_1', version_id: 'v2' }])
    expect(s.preview).toEqual({ app_id: 'app_1', version_id: 'v2', n: 2 })
    expect(EMPTY_LIVE.preview).toBeNull()
  })

  it('ignores the build events it does not own', () => {
    const s = run([file('a.py', 'x')])
    expect(applyLiveEvent(s, { type: 'log', step_key: 'module:runs', message: 'hi' })).toBe(s)
  })
})

describe('applyBuildEvent with the live events', () => {
  it('leaves the build untouched on file, route and preview events', () => {
    const b = { id: 'b1', steps: [{ key: 'module:runs', log: ['a'] }] } as unknown as Build
    for (const ev of [file('a.py', 'x'), route('GET', '/a', 'stub'), { type: 'preview', app_id: 'a', version_id: null } as BuildEvent]) {
      expect(applyBuildEvent(b, ev)).toBe(b)
    }
  })
})

describe('mergeLive', () => {
  it('lays the app chat’s writes over the build’s: its files and routes win, its newest leads, reloads add up', () => {
    const build = run([file('a.py', 'build'), file('b.py', 'build'), route('GET', '/a', 'passing'), { type: 'preview', app_id: 'app_1', version_id: 'v1' }])
    const chat = run([file('a.py', 'chat'), route('GET', '/a', 'built'), route('POST', '/c', 'built'), { type: 'preview', app_id: 'app_1', version_id: 'v2' }])
    const m = mergeLive(build, chat)
    expect(m.files['a.py'].content).toBe('chat')
    expect(m.files['b.py'].content).toBe('build')
    expect(m.order).toEqual(['a.py', 'b.py'])
    expect(m.newest).toBe('a.py')
    expect(m.routeOrder).toEqual(['GET /a', 'POST /c'])
    expect(m.routes['GET /a'].status).toBe('built')
    expect(m.preview).toEqual({ app_id: 'app_1', version_id: 'v2', n: 2 })
  })

  it('is the build’s state while the chat has written nothing', () => {
    const build = run([file('a.py', 'x')])
    expect(mergeLive(build, EMPTY_LIVE)).toBe(build)
    expect(mergeLive(EMPTY_LIVE, build).newest).toBe('a.py')
  })
})
