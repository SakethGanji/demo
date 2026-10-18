import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { useStore } from '@xyflow/react'
import { motion } from 'motion/react'
import { useStageStore, type BuildHead } from '@/features/ai-builder/stage/stageStore'
import { motionOff } from '@/shared/motion/testFlag'
import { NODE_BOX } from './stageGeometry'

const PITCH = 20
const COLS = 7
const ROWS = 5
const W = 140
const H = 100
/** Distance from the node's top to the middle of its tile (a 1-in/1-out tile is 64 px tall). */
const TILE_MID = 32

/**
 * Where React Flow's `<Background gap={20} variant=Dots>` puts its dots, in flow coordinates. The pattern is shifted by
 * `-(1 + gap*zoom/2)` screen px and each dot sits `zoom/2` px into its tile (dot radius = size*zoom/2), so a dot lies at
 * `-1/zoom - 9.5` (mod 20) flow px. Derived from `BackgroundComponent` in @xyflow/react; confirm with a screenshot.
 */
function gridOffset(zoom: number): number {
  const o = (-1 / Math.max(zoom, 0.01) - 9.5) % PITCH
  return o < 0 ? o + PITCH : o
}

/** One ripple: a 7x5 patch of dots on the canvas's own grid that pulses outward from the head node's centre. */
function Ripple({ head }: { head: BuildHead }) {
  const zoom = useStore((s) => s.transform[2])
  // Frozen at the start of the beat: the node's measured width can change after it mounts, but the ripple must not jump.
  const [frame] = useState(() => {
    const st = useStageStore.getState()
    const node = st.nodes.find((n) => n.id === head.node)
    const pos = head.at   // the engine's position for this node (round_shape), the same one the node is placed at
    const width = node?.measured?.width ?? NODE_BOX.w
    const k = (node?.data as { animScale?: number } | undefined)?.animScale ?? 1
    return { cx: pos.x + width / 2, cy: pos.y + TILE_MID, k }
  })
  const off = gridOffset(zoom)
  const sx = Math.round((frame.cx - off) / PITCH) * PITCH + off
  const sy = Math.round((frame.cy - off) / PITCH) * PITCH + off
  const dots = []
  for (let j = 0; j < ROWS; j++) {
    for (let i = 0; i < COLS; i++) {
      const ring = Math.max(Math.abs(i - 3), Math.abs(j - 2))
      dots.push(
        <motion.circle
          key={`${i}-${j}`}
          cx={sx + (i - 3) * PITCH}
          cy={sy + (j - 2) * PITCH}
          r={1.25}
          fill="var(--primary)"
          initial={{ opacity: 0 }}
          animate={{ opacity: [0, 0.9, 0.35, 0] }}
          transition={{ duration: 0.9 * frame.k, times: [0, 0.25, 0.6, 1], delay: ring * 0.02, ease: 'linear' }}
        />,
      )
    }
  }
  return (
    <svg
      data-testid="build-head-grid"
      className="pointer-events-none absolute overflow-visible"
      style={{ left: frame.cx - W / 2, top: frame.cy - H / 2 }}
      width={W}
      height={H}
      viewBox={`${frame.cx - W / 2} ${frame.cy - H / 2} ${W} ${H}`}
      aria-hidden
    >
      {dots}
    </svg>
  )
}

/**
 * A layer inside React Flow's viewport (so pan and zoom move it) that sits BEFORE the edges and nodes and under all of them,
 * but above the background dots: `ViewportPortal` renders after the nodes, which painted the ripple over them.
 */
function useUnderLayer(): HTMLElement {
  const domNode = useStore((s) => s.domNode)
  const [layer] = useState(() => {
    const el = document.createElement('div')
    el.setAttribute('data-testid', 'build-head-layer')
    el.style.cssText = 'position:absolute;top:0;left:0;width:0;height:0;overflow:visible;pointer-events:none;z-index:-1'
    return el
  })
  useEffect(() => {
    const viewport = domNode?.querySelector('.react-flow__viewport')
    if (!viewport) return
    viewport.insertBefore(layer, viewport.firstChild)
    return () => { layer.remove() }
  }, [domNode, layer])
  return layer
}

/**
 * The dot grid rippling under the node being built (PLAN-MOTION-V2 §3.6). Keyed by the head's `seq` so it remounts for
 * every beat; construct beats only, and never in a compact beat or under the test flag.
 */
export default function BuildHeadGrid() {
  const head = useStageStore((s) => (s.active ? s.buildHead : null))
  const layer = useUnderLayer()
  if (!head || head.kind !== 'construct' || head.compact || motionOff()) return null
  return createPortal(<Ripple key={head.seq} head={head} />, layer)
}
