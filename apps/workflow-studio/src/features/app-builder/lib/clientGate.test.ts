import { describe, expect, it } from 'vitest'
import { clientGate } from './clientGate'

const deferred = () => {
  let resolve!: () => void
  let reject!: (e: unknown) => void
  const p = new Promise<void>((res, rej) => ((resolve = res), (reject = rej)))
  return { p, resolve, reject }
}

describe('clientGate', () => {
  it('applies files at once when no route changed this turn', async () => {
    const order: string[] = []
    await clientGate(async () => order.push('refetch')).after(() => order.push('files'))
    expect(order).toEqual(['files'])
  })

  it('holds files until the client refetch started by a route event lands', async () => {
    const d = deferred()
    const order: string[] = []
    const gate = clientGate(() => d.p.then(() => void order.push('client')))
    gate.refresh()
    const applied = gate.after(() => order.push('files'))
    await Promise.resolve()
    expect(order).toEqual([])
    d.resolve()
    await applied
    expect(order).toEqual(['client', 'files'])
  })

  it('keeps several code events in order and releases them when the refetch fails', async () => {
    const d = deferred()
    const order: string[] = []
    const gate = clientGate(() => d.p)
    gate.refresh()
    const a = gate.after(() => order.push('a'))
    const b = gate.after(() => order.push('b'))
    d.reject(new Error('offline'))
    await Promise.all([a, b])
    expect(order).toEqual(['a', 'b'])
  })
})
