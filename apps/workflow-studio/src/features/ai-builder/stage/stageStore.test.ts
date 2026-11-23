import { beforeEach, describe, expect, it } from 'vitest'
import type { NodeChange } from '@xyflow/react'
import { useStageStore, type StageNode } from './stageStore'

const node = (id: string): StageNode => ({ id, position: { x: 0, y: 0 }, data: { name: id } as never })
const st = () => useStageStore.getState()

beforeEach(() => st().end())

describe('stageStore', () => {
  it('is off until begin, and begin starts clean', () => {
    expect(st().active).toBe(false)
    expect(st().mode).toBe('off')
    st().begin('p1', 'blueprint', [node('A')])
    expect(st()).toMatchObject({ active: true, mode: 'blueprint', proposalId: 'p1', playback: 'idle', follow: true })
    expect(st().nodes.map((n) => n.id)).toEqual(['A'])
  })
  it('setMode keeps active in step; end resets everything', () => {
    st().begin('p1', 'building')
    st().setMode('review')
    expect(st().active).toBe(true)
    st().setBadge('A', 'failed')
    st().setHoverNode('A')
    st().end()
    expect(st()).toMatchObject({ active: false, mode: 'off', proposalId: null, hoverNode: null, badges: {}, nodes: [] })
  })
  it('applies only dimension changes and keeps measured', () => {
    st().begin('p', 'building', [node('A'), node('B')])
    const changes: NodeChange[] = [
      { type: 'dimensions', id: 'A', dimensions: { width: 200, height: 60 } },
      { type: 'position', id: 'B', position: { x: 50, y: 50 } },
      { type: 'remove', id: 'B' },
    ]
    st().applyDimensions(changes)
    expect(st().nodes.map((n) => [n.id, n.measured, n.position])).toEqual([
      ['A', { width: 200, height: 60 }, { x: 0, y: 0 }], ['B', undefined, { x: 0, y: 0 }],
    ])
  })
  it('patches node data without losing measurement, moves nodes in one write', () => {
    st().begin('p', 'building', [{ ...node('A'), measured: { width: 1, height: 2 } }, node('B')])
    st().patchNodeData('A', { anim: 'enter' })
    st().setPositions({ A: { x: 5, y: 6 } })
    const a = st().nodes[0]
    expect(a).toMatchObject({ measured: { width: 1, height: 2 }, position: { x: 5, y: 6 }, data: { name: 'A', anim: 'enter' } })
    expect(st().nodes[1].position).toEqual({ x: 0, y: 0 })
  })
  it('skip requests are counted; presence merges', () => {
    st().begin('p', 'building')
    st().requestSkip(); st().requestSkip()
    expect(st().skipSeq).toBe(2)
    st().setPresence({ visible: true, label: 'url, method' })
    st().setPresence({ node: 'A' })
    expect(st().presence).toEqual({ visible: true, label: 'url, method', node: 'A', at: null })
  })
  it('setHead bumps seq, null clears; markBuilt and patchLog merge; end resets', () => {
    st().begin('p', 'building')
    st().setHead({ node: 'A', kind: 'construct', at: { x: 1, y: 2 } })
    st().setHead({ node: 'B', kind: 'morph', at: { x: 3, y: 4 } })
    expect(st().buildHead).toMatchObject({ seq: 2, node: 'B', kind: 'morph' })
    st().markBuilt('A'); st().markBuilt('B')
    st().patchLog({ round: 1, linked: true })
    expect(st().log).toEqual({ round: 1, built: { A: true, B: true }, linked: true, tests: 0 })
    st().setProgress({ done: 1, total: 2 }); st().bumpSweep()
    expect(st().progress).toEqual({ done: 1, total: 2 })
    expect(st().sweep).toBe(1)
    st().setHead(null)
    expect(st().buildHead).toBeNull()
    st().end()
    expect(st()).toMatchObject({ buildHead: null, progress: null, sweep: 0, log: { round: -1, built: {}, linked: false, tests: 0 } })
  })
})
