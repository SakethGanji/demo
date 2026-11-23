import { describe, expect, it } from 'vitest'
import { applyLiveEvent, EMPTY_LIVE } from '@/features/plan/liveBuild'
import type { BuildEvent, BuildStep } from '@/features/plan/buildTypes'
import {
  commonPrefix,
  editorLanguage,
  fileMark,
  groupOf,
  mergeEntries,
  relativeTime,
  revealChunk,
  saveProblem,
  treeRows,
  writerLine,
} from './codeLogic'
import type { FileEntry } from './filesTypes'

const entry = (path: string, kind: FileEntry['kind'], writable = kind === 'module' || kind === 'app'): FileEntry => ({
  path,
  kind,
  size: 1,
  updated_at: '2026-10-20T10:00:00',
  writable,
  ref: { type: kind, id: path },
})
const write = (path: string, builder: Extract<BuildEvent, { type: 'file' }>['builder'] = 'backend', op: 'write' | 'delete' = 'write'): BuildEvent => ({
  type: 'file',
  step_key: 'module:runs',
  builder,
  path,
  op,
  content: op === 'write' ? 'x' : '',
  done: true,
  truncated: false,
})

describe('groupOf', () => {
  it('puts each path in Plan · Backend · Tables · App', () => {
    expect(groupOf('PLAN.md')).toBe('plan')
    expect(groupOf('plan.json')).toBe('plan')
    expect(groupOf('backend/runs.py')).toBe('backend')
    expect(groupOf('backend/routes.json')).toBe('backend')
    expect(groupOf('backend/tables/runs.json')).toBe('tables')
    expect(groupOf('apps/workbench/views/Home.tsx')).toBe('app')
  })
})

describe('treeRows', () => {
  it('groups in a fixed order, folders before files, paths relative to the group', () => {
    const rows = treeRows([
      entry('apps/w/views/Home.tsx', 'app'),
      entry('apps/w/App.tsx', 'app'),
      entry('backend/tables/runs.json', 'table'),
      entry('backend/runs.py', 'module'),
      entry('backend/routes.json', 'routes'),
      entry('PLAN.md', 'plan'),
    ])
    expect(rows.map((r) => (r.type === 'group' ? `# ${r.label} ${r.count}` : `${'  '.repeat(r.depth)}${r.type === 'dir' ? '/' : ''}${r.name}`))).toEqual([
      '# Plan 1',
      'PLAN.md',
      '# Backend 2',
      'routes.json',
      'runs.py',
      '# Tables 1',
      'runs.json',
      '# App 2',
      '/w',
      '  /views',
      '    Home.tsx',
      '  App.tsx',
    ])
  })

  it('collapsed folders hide what is under them, and empty groups are left out', () => {
    const rows = treeRows([entry('apps/w/views/Home.tsx', 'app'), entry('apps/w/App.tsx', 'app')], new Set(['apps/w/views']))
    expect(rows.filter((r) => r.type !== 'group').map((r) => r.path)).toEqual(['apps/w', 'apps/w/views', 'apps/w/App.tsx'])
    expect(rows[0]).toMatchObject({ type: 'group', key: 'app' })
  })
})

describe('mergeEntries', () => {
  it('adds the files the build wrote that the tree does not list yet (read-only, kind from the path) and drops deleted ones', () => {
    let live = EMPTY_LIVE
    for (const e of [write('backend/new.py'), write('apps/w/App.tsx', 'ui'), write('backend/runs.py'), write('apps/w/old.tsx', 'ui', 'delete')])
      live = applyLiveEvent(live, e)
    const merged = mergeEntries([entry('backend/runs.py', 'module'), entry('apps/w/old.tsx', 'app')], live)
    expect(merged.map((e) => [e.path, e.kind, e.writable])).toEqual([
      ['backend/runs.py', 'module', true],
      ['backend/new.py', 'module', false],
      ['apps/w/App.tsx', 'app', false],
    ])
  })
})

describe('fileMark', () => {
  it('new when it was not in the tree when the build view opened, changed when it was, nothing when not written', () => {
    let live = EMPTY_LIVE
    live = applyLiveEvent(live, write('backend/new.py'))
    live = applyLiveEvent(live, write('backend/runs.py'))
    const baseline = new Set(['backend/runs.py', 'PLAN.md'])
    expect(fileMark('backend/new.py', baseline, live)).toBe('new')
    expect(fileMark('backend/runs.py', baseline, live)).toBe('changed')
    expect(fileMark('PLAN.md', baseline, live)).toBeNull()
    // before the baseline loads, a written file is just changed
    expect(fileMark('backend/new.py', null, live)).toBe('changed')
  })
})

describe('editorLanguage', () => {
  it('maps a path to the editor language', () => {
    expect(editorLanguage('backend/runs.py')).toBe('python')
    expect(editorLanguage('backend/routes.json')).toBe('json')
    expect(editorLanguage('apps/w/App.tsx')).toBe('typescript')
    expect(editorLanguage('apps/w/kit/hooks.ts')).toBe('typescript')
    expect(editorLanguage('apps/w/x.jsx')).toBe('javascript')
    expect(editorLanguage('PLAN.md')).toBe('text')
    expect(editorLanguage('apps/w/styles.css')).toBe('text')
  })
})

describe('typing reveal', () => {
  it('a rewrite keeps what is already on screen up to where the new content differs', () => {
    expect(commonPrefix('def a():\n  pass', 'def a():\n  return 1')).toBe(11)
    expect(commonPrefix('', 'abc')).toBe(0)
    expect(commonPrefix('abc', 'abc')).toBe(3)
  })

  it('reveals in chunks sized so any file types in within about a second', () => {
    expect(revealChunk(100)).toBe(12)
    expect(revealChunk(7000)).toBe(100)
    expect(revealChunk(200_000)).toBe(Math.ceil(200_000 / 70))
  })
})

describe('saveProblem', () => {
  it('tells read-only, conflict and other failures apart', () => {
    const err = (code: string | null, message: string, details: Record<string, unknown> = {}) => ({ code, message, details, status: 409 })
    expect(saveProblem(err('read_only', 'PLAN.md is read-only: revise the plan', { kind: 'plan' }))).toEqual({
      kind: 'read_only',
      message: 'PLAN.md is read-only: revise the plan',
    })
    expect(saveProblem(err('conflict', 'changed since you opened it', { updated_at: '2026-10-20T11:00:00' }))).toEqual({
      kind: 'conflict',
      message: 'changed since you opened it',
      updated_at: '2026-10-20T11:00:00',
    })
    expect(saveProblem(new Error('boom'))).toEqual({ kind: 'other', message: 'boom' })
  })
})

describe('writerLine and relativeTime', () => {
  const step = (key: string, kind: BuildStep['kind'], title: string) => ({ key, kind, title }) as BuildStep
  it('names the builder and what it is working on', () => {
    expect(writerLine('backend', step('module:runs', 'module', 'Backend · Runs'))).toBe('Backend builder · Runs module')
    expect(writerLine('ui', step('app:workbench', 'app', 'UI · Workbench'))).toBe('UI builder · Workbench app')
    expect(writerLine('tables', step('module:runs', 'module', 'Backend · Runs'))).toBe('Backend builder · Runs module')
    expect(writerLine('plan', null)).toBe('Planner')
    expect(writerLine('backend', step('fix:runs:1', 'fix', 'Fix · Runs'))).toBe('Backend builder · Runs module')
  })

  it('says how long ago', () => {
    const now = Date.parse('2026-10-20T10:10:00Z')
    expect(relativeTime(now - 4000, now)).toBe('just now')
    expect(relativeTime(now - 42_000, now)).toBe('42s ago')
    expect(relativeTime(now - 5 * 60_000, now)).toBe('5m ago')
    expect(relativeTime(now - 3 * 3600_000, now)).toBe('3h ago')
    expect(relativeTime(null, now)).toBe('')
  })
})
