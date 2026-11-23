import { createElement, memo, useMemo } from 'react'
import type { NodeProps, Node } from '@xyflow/react'
import { motion } from 'motion/react'
import { useStageStore } from '@/features/ai-builder/stage/stageStore'
import type { BlueprintData } from '@/features/ai-builder/stage/blueprint'
import { dur, ease, stagger } from '@/shared/motion/tokens'
import { calculateNodeDimensions, getNodeShapeConfig } from '../../../lib/nodeStyles'
import { useWorkflowStore } from '../../../stores/workflowStore'
import { getNodeIcon } from '../../../lib/nodeConfig'
import { getIconForNode } from '../../../lib/nodeIcons'

/**
 * A planned step before the engine builds it: a dashed outline tile the size of a real one, with the node type's icon
 * at half strength and the step name. View-only, no handles. It enters with an opacity and y stagger by plan order.
 * The real node replaces it (same name) when a round builds the step.
 */
function BlueprintNode({ data, positionAbsoluteX, positionAbsoluteY }: NodeProps<Node<BlueprintData>>) {
  const dims = useMemo(() => calculateNodeDimensions(1, 1), [])
  const hovered = useStageStore((s) => s.hoverNode === data.name)
  const iconName = useWorkflowStore((s) => s.nodeTypesMap.get(data.nodeType)?.icon)
  // plan order: the stagger follows reading order (row, then column)
  const order = useStageStore((s) => s.nodes.filter((n) => n.type === 'blueprintNode' && (n.position.y < positionAbsoluteY || (n.position.y === positionAbsoluteY && n.position.x < positionAbsoluteX))).length)

  return (
    <div className="relative flex flex-col items-center" data-testid="blueprint-node" data-name={data.name} data-hover={hovered || undefined}>
      <motion.div
        className="flex flex-col items-center"
        initial={{ opacity: 0, y: 10, scale: 0.94 }}
        animate={data.dissolve ? { opacity: 0, y: 0, scale: 0.92 } : { opacity: 1, y: 0, scale: hovered ? 1.06 : 1 }}
        transition={data.dissolve
          ? { duration: dur.base, ease: ease.in }
          : { duration: dur.slow, ease: ease.out, delay: order * stagger.list * 2, scale: { duration: dur.base, ease: ease.out, delay: 0 } }}
        style={{ willChange: 'transform, opacity' }}
      >
        <div
          className="flex items-center justify-center transition-[border-color,box-shadow,background-color] duration-(--d-base) ease-(--ease-out)"
          style={{
            width: dims.width,
            height: dims.height,
            borderRadius: getNodeShapeConfig('transform').borderRadius,
            border: `2px dashed ${hovered ? 'var(--primary)' : 'color-mix(in srgb, var(--primary) 65%, transparent)'}`,
            backgroundColor: `color-mix(in srgb, var(--primary) ${hovered ? 16 : 6}%, var(--raise))`,
            boxShadow: hovered ? '0 0 0 4px color-mix(in srgb, var(--primary) 22%, transparent), var(--glow) 0 0 18px' : undefined,
          }}
        >
          <span className="text-primary" style={{ opacity: hovered ? 1 : 0.8 }}>{createElement(getIconForNode(getNodeIcon(data.nodeType, iconName), data.nodeType), { size: 24 })}</span>
        </div>
        <span
          className={`mt-2 truncate text-center text-[13px] font-medium leading-tight transition-colors duration-(--d-base) ${hovered ? 'text-foreground' : 'text-muted-foreground'}`}
          style={{ maxWidth: 156 }}
        >
          {data.name}
        </span>
      </motion.div>
    </div>
  )
}

export default memo(BlueprintNode)
