import { cn } from '@/shared/lib/utils'
import { kindStyle, shapePath } from '../lib/kinds'

/** The kind's shape in its colour, at text size: the legend in chips, lists and the detail panel. */
export function KindSwatch({ kind, size = 10, dashed, className }: { kind: string; size?: number; dashed?: boolean; className?: string }) {
  const st = kindStyle(kind)
  const r = size * 0.36
  return (
    <svg width={size} height={size} viewBox={`${-size / 2} ${-size / 2} ${size} ${size}`} className={cn('shrink-0 overflow-visible', className)} aria-hidden>
      <path
        d={shapePath(st.shape, r)}
        fill={st.color}
        fillOpacity={dashed ? 0.22 : 1}
        stroke={dashed ? st.color : 'none'}
        strokeDasharray={dashed ? '2 1.5' : undefined}
      />
    </svg>
  )
}
