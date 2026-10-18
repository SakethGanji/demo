/**
 * One node component for every kind (PLAN-CAPGRAPH-UI §2.2). Shape means kind; colour only tints icons. No handles: edges are
 * drawn by the page from precomputed paths, so xyflow never measures a connection.
 *
 * Motion lives on an inner wrapper and never on the xyflow node wrapper, so measurement stays exact:
 *   construct   the DOM construction beat (`Construct`) for concepts, roles, patterns, apps
 *   fade        capabilities fade in
 *   flip        an inner offset that animates to zero when the node moved (focus / back to overview)
 *   leaving     fade + scale out
 */
import { memo, useState } from 'react'
import type { Node, NodeProps } from '@xyflow/react'
import { motion } from 'motion/react'
import { Badge } from '@/shared/components/ui/badge'
import { IconTile } from '@/shared/components/ui/surface'
import { Tip } from '@/shared/components/ui/tooltip'
import { MethodBadge } from '@/shared/components/api/MethodBadge'
import { cn } from '@/shared/lib/utils'
import { dur, ease, spring } from '@/shared/motion/tokens'
import { Construct, type ConstructMode } from '../../kit/Construct'
import { KindIcon } from '../../kit/KindIcon'
import { kindHue, nodeTestId, type KgKind } from '../../kit/kinds'
import { count } from '../../lib/labels'
import type { NodeView } from '../../types'

export interface KgNodeData extends Record<string, unknown> {
  node: NodeView
  kind: KgKind
  w: number
  h: number
  /** apps: composed parts; patterns: distinct teams that validated it */
  parts: number
  teams: number
  focused: boolean
  /** a construct beat to play, with a token that restarts it */
  construct: { mode: Exclude<ConstructMode, 'idle'>; delay: number; scale: number; key: string } | null
  fade: { delay: number } | null
  flip: { dx: number; dy: number; delay: number; key: string } | null
  leaving: boolean
}
export type KgFlowNode = Node<KgNodeData, 'kg'>

function Face({ d }: { d: KgNodeData }) {
  const { node, kind } = d
  const sub = kind === 'concept' && typeof node.props.kind === 'string' ? node.props.kind : node.subkind
  const hue = kindHue(kind, sub)
  const proposed = node.status === 'proposed'
  const stale = node.status === 'stale'
  const ring = d.focused ? 'shadow-[inset_0_0_0_2px_var(--primary)]' : null

  if (kind === 'role') {
    return (
      <div className={cn('flex size-full flex-col items-center gap-1', stale && 'opacity-55')}>
        <span className={cn('inline-flex size-9 items-center justify-center rounded-full bg-raise shadow-raise-sm text-hue-secrets [&_svg]:size-4 [&_svg]:stroke-[1.75]', ring)}>
          <KindIcon kind={kind} subkind={sub} />
        </span>
        <Tip label={node.label}><span className="line-clamp-2 max-w-full text-center text-[12px] leading-[14px]">{node.label}</span></Tip>
      </div>
    )
  }
  if (kind === 'capability') {
    const method = typeof node.props.method === 'string' ? node.props.method : null
    return (
      <Tip label={node.label}>
        <div className={cn('flex size-full items-center gap-1.5 rounded-ctl bg-recess px-2 text-[12px]', ring, stale && 'opacity-55')}>
          <IconTile hue={hue} className="size-4 bg-transparent [&_svg]:size-3"><KindIcon kind={kind} subkind={sub} /></IconTile>
          {method && <MethodBadge method={method} size="sm" />}
          <span className="min-w-0 flex-1 truncate">{node.label}</span>
        </div>
      </Tip>
    )
  }
  if (kind === 'concept') {
    return (
      <div className={cn('flex size-full items-center gap-2 rounded-full bg-raise pr-3 pl-1.5 shadow-raise-sm', ring, stale && 'opacity-55')}>
        <IconTile hue="ai" className="size-6 rounded-full [&_svg]:size-3.5"><KindIcon kind={kind} subkind={sub} /></IconTile>
        <span className="min-w-0 flex-1 truncate text-[13px] font-semibold">{node.label}</span>
        {proposed && <Badge variant="ai">proposed</Badge>}
        {stale && <Badge variant="stub">stale</Badge>}
      </div>
    )
  }
  // pattern / app
  const learned = kind === 'pattern' && node.source === 'learned'
  return (
    <div
      className={cn(
        'flex size-full flex-col justify-center gap-0.5 rounded-panel bg-raise px-3',
        ring ?? (learned ? 'shadow-[inset_0_0_0_1.5px_var(--primary)]' : 'shadow-raise-sm'),
        stale && 'opacity-55',
      )}
    >
      <span className="flex items-center gap-1.5">
        <IconTile hue={hue} className="size-5 bg-transparent [&_svg]:size-3.5"><KindIcon kind={kind} subkind={sub} /></IconTile>
        <span className="min-w-0 flex-1 truncate text-[13px] font-semibold">{node.label}</span>
      </span>
      <span className="truncate pl-6.5 text-[12px] text-muted-foreground">
        {kind === 'pattern'
          ? `validated ×${node.validated_count}${d.teams > 0 ? ` · ${count(d.teams, 'team')}` : ''}`
          : count(d.parts, 'part')}
      </span>
    </div>
  )
}

function KgNodeImpl({ data }: NodeProps<KgFlowNode>) {
  const [doneKey, setDoneKey] = useState<string | null>(null)
  const { node } = data
  const c = data.construct
  const mode: ConstructMode = c && doneKey !== c.key ? c.mode : 'idle'
  const face = (
    <Construct
      key={c?.key ?? 'idle'}
      mode={mode}
      radius={data.kind === 'concept' ? data.h / 2 : data.kind === 'capability' ? 8 : data.kind === 'role' ? 18 : 12}
      scale={c?.scale ?? 1}
      delay={c?.delay ?? 0}
      onDone={() => c && setDoneKey(c.key)}
    >
      <div style={{ width: data.w, height: data.h }}><Face d={data} /></div>
    </Construct>
  )

  const shell = 'kg-node'
  const common = {
    'data-testid': nodeTestId(node.key),
    'data-kind': data.kind,
    'data-status': node.status,
    'data-source': node.source,
    'data-kg-col': data.kind,
    style: { width: data.w, height: data.h },
  } as const

  if (data.leaving) {
    return (
      <motion.div {...common} className={shell} initial={{ opacity: 1, scale: 1 }} animate={{ opacity: 0, scale: 0.96 }} transition={{ duration: dur.base, ease: ease.in }}>
        {face}
      </motion.div>
    )
  }
  if (data.flip) {
    return (
      <motion.div
        key={data.flip.key} {...common} className={shell}
        initial={{ x: data.flip.dx, y: data.flip.dy }} animate={{ x: 0, y: 0 }} transition={{ ...spring.soft, delay: data.flip.delay }}
      >
        {face}
      </motion.div>
    )
  }
  if (data.fade) {
    return (
      <motion.div {...common} className={shell} initial={{ opacity: 0, scale: 0.96 }} animate={{ opacity: 1, scale: 1 }} transition={{ duration: dur.base, delay: data.fade.delay, ease: ease.out }}>
        {face}
      </motion.div>
    )
  }
  return <div {...common} className={shell}>{face}</div>
}

export const KgNodeView = memo(KgNodeImpl)
