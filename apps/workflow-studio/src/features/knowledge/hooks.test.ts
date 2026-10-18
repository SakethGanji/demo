import { describe, expect, it, vi } from 'vitest'

describe('kgMemoryOptions', () => {
  it('refetches on window focus even inside the global 30 s staleTime', async () => {
    vi.stubGlobal('window', { location: { hostname: 'localhost' } }) // the api config reads the hostname at import
    const { kgMemoryOptions } = await import('./hooks')
    const o = kgMemoryOptions('p1')
    expect(o.refetchOnWindowFocus).toBe(true)
    expect(o.staleTime).toBe(0)
  })
})
