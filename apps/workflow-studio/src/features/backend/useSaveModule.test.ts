/**
 * The module save carries the draft's checked tables as `tables` on the module write (create and update): one server
 * path creates or migrates them, instead of a client-side create loop.
 */
import { describe, expect, it, vi } from 'vitest'

vi.mock('@/shared/api/http', () => ({
  ApiError: class ApiError extends Error {},
  errorMessage: (e: unknown) => String(e),
  request: vi.fn(),
  isApiError: () => false,
}))
vi.mock('sonner', () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }))

const { createdTables, saveBody, saveTables } = await import('./useSaveModule')
import type { ModuleDoc } from './docStore'
import type { ModuleWriteResult } from '@/shared/api/types'

const cols = [{ name: 'title', type: 'text' }]
const doc = (moduleId: string | null, tables: Array<{ name: string; create: boolean }>) =>
  ({ module: 'refunds', moduleId, draft: { tables: tables.map((t) => ({ ...t, columns: cols })) } }) as unknown as ModuleDoc

describe('module save body', () => {
  it('sends only the checked tables', () => {
    expect(saveTables(doc(null, [{ name: 'refunds', create: true }, { name: 'skip', create: false }]))).toEqual([
      { name: 'refunds', columns: cols },
    ])
  })
  it('a create sends name, source and tables', () => {
    expect(saveBody(doc(null, [{ name: 'refunds', create: true }]), 'src', null)).toEqual({
      name: 'refunds',
      source: 'src',
      tables: [{ name: 'refunds', columns: cols }],
    })
  })
  it('an update sends source, base sha and tables', () => {
    expect(saveBody(doc('m1', [{ name: 'refunds', create: true }]), 'src', 'abc')).toEqual({
      source: 'src',
      base_sha: 'abc',
      tables: [{ name: 'refunds', columns: cols }],
    })
  })
  it('omits tables when none are checked', () => {
    expect(saveBody(doc('m1', []), 'src', 'abc')).toEqual({ source: 'src', base_sha: 'abc' })
  })
  it('reports created tables from the engine, else infers them', () => {
    const sent = [{ name: 'refunds', columns: cols }, { name: 'old', columns: cols }] as never
    expect(createdTables({ tables_created: ['refunds'] } as unknown as ModuleWriteResult, sent, [])).toEqual(['refunds'])
    expect(createdTables({} as ModuleWriteResult, sent, ['old'])).toEqual(['refunds'])
  })
})
