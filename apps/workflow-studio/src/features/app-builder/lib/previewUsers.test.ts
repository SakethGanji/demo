import { beforeEach, describe, expect, it, vi } from 'vitest'
import { PRESET_USERS, loadViewAs, saveViewAs } from './previewUsers'

describe('loadViewAs default identity', () => {
  beforeEach(() => {
    const store = new Map<string, string>()
    vi.stubGlobal('localStorage', {
      getItem: (k: string) => store.get(k) ?? null,
      setItem: (k: string, v: string) => void store.set(k, v),
    })
  })

  it('defaults to the first preset user when nothing was chosen', () => {
    const s = loadViewAs('app1')
    expect(s.selected).toBe(PRESET_USERS[0].id)
    expect(s.users).toEqual(PRESET_USERS)
  })

  it('keeps an explicit choice, including the app itself (no user)', () => {
    saveViewAs('app1', { users: PRESET_USERS, selected: 'carol' })
    expect(loadViewAs('app1').selected).toBe('carol')
    saveViewAs('app2', { users: PRESET_USERS, selected: null })
    expect(loadViewAs('app2').selected).toBeNull()
  })
})
