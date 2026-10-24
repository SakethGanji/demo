import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { useAppDocumentStore } from '../stores'
import { installHistory, useEditHistory } from './history'

const doc = () => useAppDocumentStore.getState()
const file = (p: string) => doc().files?.find((f) => f.path === p)?.content
let off: () => void

beforeEach(() => {
  doc().reset()
  useEditHistory.getState().clear()
  off = installHistory()
  doc().setFiles([{ path: 'App.tsx', content: 'v0' }, { path: 'views/A.tsx', content: 'a0' }]) // a load: no transaction
})
afterEach(() => off())

describe('shared undo history', () => {
  it('a load is not an edit', () => {
    expect(useEditHistory.getState().past).toEqual([])
  })
  it('undoes visual and chat edits in order, and redoes them', () => {
    const h = useEditHistory.getState()
    h.commit('rename', [{ path: 'views/A.tsx', before: 'a0', after: 'a1' }])
    expect(file('views/A.tsx')).toBe('a1')
    doc().setFiles([{ path: 'App.tsx', content: 'v1' }, { path: 'views/A.tsx', content: 'a1' }]) // a chat turn
    expect(useEditHistory.getState().past.map((t) => t.origin)).toEqual(['visual', 'chat'])
    expect(useEditHistory.getState().undo()).toBe(true)
    expect(file('App.tsx')).toBe('v0')
    expect(useEditHistory.getState().undo()).toBe(true)
    expect(file('views/A.tsx')).toBe('a0')
    expect(useEditHistory.getState().redo()).toBe(true)
    expect(file('views/A.tsx')).toBe('a1')
    expect(useEditHistory.getState().past).toHaveLength(1)
  })
  it('blocks an undo whose file changed since', () => {
    useEditHistory.getState().commit('rename', [{ path: 'views/A.tsx', before: 'a0', after: 'a1' }])
    useEditHistory.setState({ past: useEditHistory.getState().past }) // keep
    // a later edit lands outside the history (e.g. while it was not installed)
    off()
    doc().setFiles([{ path: 'App.tsx', content: 'v0' }, { path: 'views/A.tsx', content: 'a2' }])
    off = installHistory()
    expect(useEditHistory.getState().undo()).toBe(false)
    expect(useEditHistory.getState().notice).toContain('views/A.tsx')
    expect(file('views/A.tsx')).toBe('a2')
  })
  it('coalesces a burst of edits to the same field', () => {
    const h = useEditHistory.getState()
    h.commit('title', [{ path: 'App.tsx', before: 'v0', after: 'v1' }], 'App.tsx#title')
    h.commit('title', [{ path: 'App.tsx', before: 'v1', after: 'v2' }], 'App.tsx#title')
    expect(useEditHistory.getState().past).toHaveLength(1)
    expect(useEditHistory.getState().past[0].patches[0]).toEqual({ path: 'App.tsx', before: 'v0', after: 'v2' })
    useEditHistory.getState().undo()
    expect(file('App.tsx')).toBe('v0')
  })
  it('a new app (reset) clears the history', () => {
    useEditHistory.getState().commit('x', [{ path: 'App.tsx', before: 'v0', after: 'v1' }])
    doc().reset()
    expect(useEditHistory.getState().past).toEqual([])
  })
})
