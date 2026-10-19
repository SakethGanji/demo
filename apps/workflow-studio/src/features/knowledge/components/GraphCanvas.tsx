/**
 * The knowledge constellation, drawn in SVG under one pan/zoom transform. Three layers: edges (thin curves, dashed
 * by relation), nodes (kind shape on a flat halo, sized by degree), labels (counter-scaled so text stays 11px).
 * Emphasis fades everything outside the lit set and runs flowing dashes along lit edges. On the team page a node that
 * belongs to a project carries a small orbit dot in the project's colour on its upper-right rim; the kind colour
 * stays the node's own. The whole canvas is
 * `aria-hidden`: the List view is its keyboard and screen-reader twin.
 */
import { memo, useId } from 'react'
import { motion } from 'motion/react'
import type { LucideIcon } from 'lucide-react'
import { ease } from '@/shared/motion/tokens'
import { edgePath, type Emphasis, type GraphModel, type Visible } from '../lib/graph'
import { iconFor } from '../lib/icons'
import { kindStyle, relStyle, shapePath, type Shape } from '../lib/kinds'
import type { Positions } from '../lib/layout'
import type { Transform } from '../lib/viewport'

/** One rebuild's entrance: which nodes ripple in, and when (ms). `gen` restarts the animation. */
export interface RippleWave {
  gen: number
  delays: Map<string, number>
  /** true: the nodes are new or changed and scale in; false: nothing changed, a soft ring runs over everything */
  enter: boolean
}

export function GraphCanvas({
  model, visible, pos, t, em, labels, selected, hovered, ripple, off, onHover, onPick, orbit,
}: {
  model: GraphModel
  visible: Visible
  pos: Positions
  t: Transform
  em: Emphasis
  labels: Set<string>
  selected: string | null
  hovered: string | null
  ripple: RippleWave | null
  off: boolean
  onHover: (id: string | null) => void
  onPick: (id: string) => void
  /** team scope: node id -> its project's colour */
  orbit?: ReadonlyMap<string, string>
}) {
  const gridId = `kg-grid-${useId().replace(/:/g, '')}`
  const lit = em.lit
  const k = t.k
  return (
    <svg className="absolute inset-0 size-full select-none" aria-hidden data-testid="knowledge-canvas" data-count={visible.nodes.size}>
      <defs>
        <pattern id={gridId} width={22} height={22} patternUnits="userSpaceOnUse" patternTransform={`translate(${t.x} ${t.y}) scale(${k})`}>
          <circle cx={1} cy={1} r={k < 0.5 ? 1.6 : 1} fill="var(--grid-dot)" />
        </pattern>
      </defs>
      <rect width="100%" height="100%" fill={`url(#${gridId})`} />
      <g transform={`translate(${t.x} ${t.y}) scale(${k})`}>
        <g>
          {model.edges.map((e) => {
            if (!visible.edges.has(e.id)) return null
            const a = pos.get(e.src_id)
            const b = pos.get(e.dst_id)
            if (!a || !b) return null
            const on = em.flow.has(e.id)
            const dim = !!lit && !on
            return (
              <EdgePath
                key={e.id}
                d={edgePath(a, b, model.radius.get(e.src_id), model.radius.get(e.dst_id))}
                rel={e.rel}
                color={on ? kindStyle(model.byId.get(e.src_id)?.kind ?? '').color : 'var(--muted-foreground)'}
                state={on ? 'on' : dim ? 'dim' : 'rest'}
              />
            )
          })}
        </g>
        <g>
          {model.nodes.map((n) => {
            if (!visible.nodes.has(n.id)) return null
            const p = pos.get(n.id)
            if (!p) return null
            const st = kindStyle(n.kind)
            const r = model.radius.get(n.id) ?? 5
            const delay = ripple?.delays.get(n.id)
            return (
              <NodeGlyph
                key={n.id}
                id={n.id}
                x={p.x}
                y={p.y}
                r={r}
                k={k}
                color={st.color}
                shape={st.shape}
                Icon={iconFor(n)}
                learned={n.source === 'learned'}
                proposed={n.status === 'proposed'}
                stale={n.status === 'stale' || n.status === 'rejected'}
                dim={!!lit && !lit.has(n.id)}
                lit={!!lit && lit.has(n.id)}
                selected={selected === n.id}
                hovered={hovered === n.id}
                rippleKey={delay !== undefined && ripple && !off ? `${ripple.gen}:${ripple.enter ? 'in' : 'ring'}` : null}
                rippleDelay={delay ?? 0}
                orbit={orbit?.get(n.id)}
                onHover={onHover}
                onPick={onPick}
              />
            )
          })}
        </g>
        <g className="pointer-events-none">
          {[...labels].map((id) => {
            const n = model.byId.get(id)
            const p = pos.get(id)
            if (!n || !p) return null
            const r = model.radius.get(id) ?? 5
            const strong = id === selected || id === hovered
            return (
              <g key={id} transform={`translate(${p.x} ${p.y}) scale(${1 / k})`}>
                <text
                  y={r * k + 13}
                  textAnchor="middle"
                  className={strong ? 'fill-foreground text-[11.5px] font-semibold' : 'fill-foreground/80 text-[11px] font-medium'}
                  stroke="var(--kg-label-halo)"
                  strokeWidth={3.5}
                  strokeLinejoin="round"
                  paintOrder="stroke"
                >
                  {n.label.length > 30 ? `${n.label.slice(0, 29)}…` : n.label}
                </text>
              </g>
            )
          })}
        </g>
      </g>
    </svg>
  )
}

const EdgePath = memo(function EdgePath({ d, rel, color, state }: { d: string; rel: string; color: string; state: 'on' | 'dim' | 'rest' }) {
  const rs = relStyle(rel)
  return (
    <path
      d={d}
      fill="none"
      stroke={color}
      strokeWidth={state === 'on' ? rs.width + 0.6 : rs.width}
      strokeDasharray={state === 'on' ? undefined : rs.dash || undefined}
      strokeLinecap="round"
      vectorEffect="non-scaling-stroke"
      opacity={state === 'on' ? 0.95 : state === 'dim' ? 0.06 : 0.3}
      className={state === 'on' ? 'kg-flow transition-opacity duration-(--d-base)' : 'transition-opacity duration-(--d-base)'}
    />
  )
})

const NodeGlyph = memo(function NodeGlyph({
  id, x, y, r, k, color, shape, Icon, learned, proposed, stale, dim, lit, selected, hovered, rippleKey, rippleDelay, orbit, onHover, onPick,
}: {
  id: string
  x: number
  y: number
  r: number
  k: number
  color: string
  shape: Shape
  Icon: LucideIcon
  learned: boolean
  proposed: boolean
  stale: boolean
  dim: boolean
  lit: boolean
  selected: boolean
  hovered: boolean
  rippleKey: string | null
  rippleDelay: number
  orbit?: string
  onHover: (id: string | null) => void
  onPick: (id: string) => void
}) {
  const showIcon = r * k >= 9
  const icon = r * 1.05
  const opacity = dim ? 0.16 : stale ? 0.45 : 1
  const enter = rippleKey?.endsWith(':in')
  const body = (
    <>
      <circle r={r * (lit || hovered || selected ? 2.5 : 2)} fill={color} opacity={lit || hovered || selected ? 0.2 : 0.09} />
      {learned && !dim && <circle r={r + 2} fill="none" stroke={color} strokeWidth={1.5} vectorEffect="non-scaling-stroke" className="kg-pulse" />}
      <path
        d={shapePath(shape, r)}
        fill={color}
        fillOpacity={proposed ? 0.22 : 0.92}
        stroke={proposed ? color : 'var(--kg-label-halo)'}
        strokeWidth={proposed ? 1.5 : 1}
        strokeDasharray={proposed ? '3 2.5' : undefined}
        vectorEffect="non-scaling-stroke"
      />
      {showIcon && (
        <Icon
          x={-icon / 2}
          y={-icon / 2}
          width={icon}
          height={icon}
          color={proposed ? color : 'var(--kg-label-halo)'}
          strokeWidth={2.25}
          absoluteStrokeWidth={false}
        />
      )}
      {selected && <circle r={r + 5} fill="none" stroke="var(--foreground)" strokeWidth={1.5} vectorEffect="non-scaling-stroke" />}
      {orbit && (
        // the project's dot rides the rim at one o'clock, never covering the kind shape's icon
        <circle
          cx={r * 0.78}
          cy={-r * 0.78}
          r={Math.max(1.9, r * 0.36)}
          fill={orbit}
          stroke="var(--kg-label-halo)"
          strokeWidth={1.25}
          vectorEffect="non-scaling-stroke"
          data-orbit=""
        />
      )}
      {/* a generous, invisible hit target */}
      <circle r={Math.max(r + 4, 8 / k)} fill="transparent" />
    </>
  )
  return (
    <g
      transform={`translate(${x} ${y})`}
      opacity={opacity}
      className="cursor-pointer transition-opacity duration-(--d-base)"
      data-testid={`knowledge-node-${id}`}
      data-state={selected ? 'selected' : lit ? 'lit' : dim ? 'dim' : 'rest'}
      onPointerEnter={() => onHover(id)}
      onPointerLeave={() => onHover(null)}
      onClick={(e) => {
        e.stopPropagation()
        onPick(id)
      }}
    >
      {rippleKey ? (
        <g key={rippleKey}>
          <motion.circle
            r={r}
            fill="none"
            stroke={color}
            strokeWidth={1.5}
            vectorEffect="non-scaling-stroke"
            initial={{ scale: 1, opacity: 0 }}
            animate={{ scale: [1, 4], opacity: [0.8, 0] }}
            transition={{ duration: 0.9, delay: rippleDelay / 1000, ease: ease.out }}
          />
          {enter ? (
            <motion.g initial={{ scale: 0, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} transition={{ type: 'spring', stiffness: 420, damping: 22, delay: rippleDelay / 1000 }}>
              {body}
            </motion.g>
          ) : (
            body
          )}
        </g>
      ) : (
        body
      )}
    </g>
  )
})
