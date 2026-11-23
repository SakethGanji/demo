/**
 * Reads the engine's audit events into build plans (PLAN-MOTION-UI §4.2, §4.3). Defensive: an event from an older
 * engine, or one with a missing field, is ignored rather than throwing.
 */
import type { AiEvent } from '../types'
import type { RoundShape, ShapeConnection, TestResult, TestStatus, XY } from './buildOps'

const isObj = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v)
const str = (v: unknown): string | null => (typeof v === 'string' ? v : null)
const strs = (v: unknown): string[] => (Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : [])
const xy = (v: unknown): XY => (isObj(v) && typeof v.x === 'number' && typeof v.y === 'number' ? { x: v.x, y: v.y } : { x: 0, y: 0 })

/** The `round_shape` payload as a typed shape, or null when it is not one. */
export function parseRoundShape(payload: Record<string, unknown>): RoundShape | null {
  if (typeof payload.round !== 'number' || !Array.isArray(payload.nodes)) return null
  const nodes = payload.nodes.flatMap((n) => {
    const name = isObj(n) ? str(n.name) : null
    return isObj(n) && name ? [{ name, type: str(n.type) ?? 'Unknown', position: xy(n.position) }] : []
  })
  const connections = (Array.isArray(payload.connections) ? payload.connections : []).flatMap((c): ShapeConnection[] =>
    Array.isArray(c) && c.length === 4 && c.every((p) => typeof p === 'string') ? [c as ShapeConnection] : [])
  const changed = (Array.isArray(payload.changed) ? payload.changed : []).flatMap((c) => {
    const name = isObj(c) ? str(c.name) : null
    return isObj(c) && name ? [{ name, fields: strs(c.fields) }] : []
  })
  return { round: payload.round, nodes, connections, changed, removed: strs(payload.removed) }
}

const STATUSES: TestStatus[] = ['passed', 'failed', 'errored']
const toStatus = (v: unknown): TestStatus => {
  if (v === 'error') return 'errored'
  return STATUSES.includes(v as TestStatus) ? (v as TestStatus) : 'failed'
}

/** `tested.results`, or an empty list for an engine that does not send them. */
export function parseTestResults(payload: Record<string, unknown>): TestResult[] {
  if (!Array.isArray(payload.results)) return []
  return payload.results.flatMap((r): TestResult[] => {
    if (!isObj(r)) return []
    const id = str(r.test_id)
    if (!id) return []
    return [{ test_id: id, name: str(r.name) ?? id, status: toStatus(r.status), path: strs(r.path), failed_nodes: strs(r.failed_nodes) }]
  })
}

export interface RoundPlan {
  round: number
  shape: RoundShape | null
  tests: TestResult[]
}

/** Folds the whole event list into one plan per round, oldest first. */
export function roundPlansFromEvents(events: AiEvent[]): RoundPlan[] {
  const plans = new Map<number, RoundPlan>()
  const get = (n: number) => {
    let p = plans.get(n)
    if (!p) plans.set(n, (p = { round: n, shape: null, tests: [] }))
    return p
  }
  for (const e of events) {
    if (e.event === 'round_shape') {
      const s = parseRoundShape(e.payload)
      if (s) get(s.round).shape = s
    } else if (e.event === 'tested' && typeof e.payload.round === 'number') {
      get(e.payload.round).tests = parseTestResults(e.payload)
    }
  }
  return [...plans.values()].sort((a, b) => a.round - b.round)
}
