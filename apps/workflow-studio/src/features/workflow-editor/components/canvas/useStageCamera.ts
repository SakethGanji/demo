import { useEffect } from 'react'
import { useReactFlow, useStoreApi } from '@xyflow/react'
import { useStageStore } from '@/features/ai-builder/stage/stageStore'
import { isBlueprintId } from '@/features/ai-builder/stage/blueprint'
import { beat, dur } from '@/shared/motion/tokens'
import { motionOff } from '@/shared/motion/testFlag'
import { NODE_BOX, boundsOf, easeInOutCubic, followViewport, frameForRound, mayMoveCamera, viewportForBounds } from './stageGeometry'

/**
 * The stage camera (PLAN-MOTION-V2 §2.8). It frames a box at the start of a round and then follows the build head:
 *  - when the plan's outlines appear (frames the outlines),
 *  - at the start of a round: every node on stage plus the positions the round is about to place, at a closer zoom
 *    around the first head when it does not all fit (`frameForRound`),
 *  - on each beat: if the head leaves the safe box, one pan at the same zoom (`followViewport`),
 *  - when the review opens, and when the user resumes follow: fit everything (the pull-back).
 * The box always lands in the part of the canvas the right panel leaves free (`getInset` measures the panel; never a
 * constant), never zooms past 1. A user pan or zoom turns `follow` off and the camera stays put.
 */
export function useStageCamera(getInset: () => number) {
  const { setViewport } = useReactFlow()
  const rf = useStoreApi()

  useEffect(() => {
    let lastFrameAt = -Infinity
    let lastFollowKey = ''
    let lastFollowAt = -Infinity

    const box = (extra: { x: number; y: number }[], withOutlines: boolean) => {
      const s = useStageStore.getState()
      // outlines count only when the plan appears; real nodes replace them as the build goes
      const nodes = s.nodes.filter((n) => (withOutlines || !isBlueprintId(n.id)) && s.marks[n.id] !== 'removed')
      return boundsOf([
        ...nodes.map((n) => ({ x: n.position.x, y: n.position.y, w: n.measured?.width, h: n.measured?.height })),
        ...extra,
      ])
    }

    const move = (vp: { x: number; y: number; zoom: number }, ms: number, ease?: (t: number) => number) => {
      if (motionOff()) { void setViewport(vp, { duration: 0 }); return }
      void setViewport(vp, ease ? { duration: ms, ease, interpolate: 'linear' } : { duration: ms })
    }

    /** `round` = the points a round is about to place; empty = fit everything. */
    const frame = (extra: { x: number; y: number }[] = [], opts: { outlines?: boolean; ms?: number; round?: boolean } = {}) => {
      if (!mayMoveCamera(useStageStore.getState())) return
      const { width, height } = rf.getState()
      if (!width || !height) return
      const b = box(extra, !!opts.outlines)
      if (!b) return
      const size = { width, height }
      const vp = opts.round
        ? frameForRound(b, extra[0] ? { x: extra[0].x, y: extra[0].y, w: NODE_BOX.w, h: NODE_BOX.h } : null, size, getInset())
        : viewportForBounds(b, size, { inset: getInset(), maxZoom: 1 })
      if (opts.round) { lastFrameAt = performance.now(); lastFollowKey = '' }
      move(vp, opts.ms ?? dur.camera * 1000)
    }

    const follow = (s: ReturnType<typeof useStageStore.getState>) => {
      const head = s.buildHead
      if (!head || !mayMoveCamera(s)) return
      const key = `${head.node}:${head.kind === 'fail' ? 'fail' : 'build'}`
      const now = performance.now()
      if (key === lastFollowKey && now - lastFollowAt < 600) return   // the lead and the landing of one beat pan once
      lastFollowKey = key
      lastFollowAt = now
      if (now - lastFrameAt < dur.camera * 1000) return                // the round frame is still settling
      const { width, height, transform } = rf.getState()
      if (!width || !height) return
      const rect = { x: head.at.x, y: head.at.y, w: NODE_BOX.w, h: NODE_BOX.h }
      const next = followViewport(rect, { x: transform[0], y: transform[1], zoom: transform[2] }, { width, height }, getInset())
      if (next) move(next, beat.followMs, easeInOutCubic)
    }

    const unsub = useStageStore.subscribe((s, prev) => {
      if (!s.active) return

      // plan outlines appear
      if (s.nodes.some((n) => isBlueprintId(n.id)) && !prev.nodes.some((n) => isBlueprintId(n.id))) {
        frame([], { outlines: true })
        return
      }
      // the review opens after a played build: pull back and fit everything
      if (s.mode === 'review' && prev.mode !== 'review') {
        frame([], { ms: beat.pullBackMs })
        return
      }
      // follow switched back on: fit everything
      if (s.follow && !prev.follow) {
        frame([], { ms: beat.pullBackMs })
        return
      }
      // a round is about to place nodes
      if (s.frame && s.frame.seq !== prev.frame?.seq) { frame(s.frame.points, { round: true }); return }
      // the build head moved to a new node
      const h = s.buildHead
      if (h && h.seq !== prev.buildHead?.seq && h.kind !== 'test') follow(s)
    })
    return unsub
  }, [rf, setViewport, getInset])
}
