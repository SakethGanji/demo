/**
 * What the node sheet adds for an `app` node (PLAN-CAPABILITY-GRAPH WP-E): where the app came from, the settings it was built with
 * (the slots a team can change when it adapts it), its data tables, and a way to start a similar one. The parts and the
 * neighbours are the sheet's own sections. No scores, no confidence.
 */
import { motion } from 'motion/react'
import { Sparkles } from 'lucide-react'
import { slotTitle, valueText } from '@/features/ai-builder/apps/lib/appState'
import { timeAgo } from '@/features/project/lib/format'
import { Button } from '@/shared/components/ui/button'
import { dur, ease } from '@/shared/motion/tokens'
import { useBuildWith } from '../hooks'
import { count } from '../lib/labels'
import type { NodeView } from '../types'

const item = { hidden: { opacity: 0, y: 6 }, show: { opacity: 1, y: 0, transition: { duration: dur.base, ease: ease.out } } }

interface Source { project_label?: unknown; kept_at?: unknown }
interface Table { key?: unknown; name?: unknown; columns?: unknown }

export function AppNodeDetail({ node }: { node: NodeView }) {
  const buildWith = useBuildWith()
  const source = (node.props.source ?? null) as Source | null
  const slots = Object.entries((node.props.slots ?? {}) as Record<string, unknown>).filter(([, v]) => v !== null && v !== undefined && v !== '')
  const skeleton = node.props.skeleton as { tables?: Table[] } | undefined
  const tables = Array.isArray(skeleton?.tables) ? skeleton.tables : []
  const origin = [
    typeof source?.project_label === 'string' ? `Built in ${source.project_label}` : null,
    typeof source?.kept_at === 'string' ? `kept ${timeAgo(source.kept_at)}` : null,
  ].filter(Boolean).join(', ')

  return (
    <motion.section variants={item} className="flex flex-col gap-3" data-testid="kg-sheet-app">
      {origin && <p className="text-[12.5px] text-foreground/85" data-testid="kg-sheet-app-origin">{origin}</p>}

      {slots.length > 0 && (
        <div className="flex flex-col gap-1.5" data-testid="kg-sheet-app-settings">
          <h3 className="text-[12px] font-medium text-muted-foreground">Settings it was built with</h3>
          <ul className="flex flex-col gap-1">
            {slots.map(([slot, v]) => (
              <li key={slot} className="flex items-center justify-between gap-3 rounded-chip bg-recess px-2 py-1 text-[12.5px]" data-slot={slot}>
                <span className="truncate text-muted-foreground">{slotTitle(slot)}</span>
                <span className="truncate font-medium tabular-nums">{valueText(v)}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {tables.length > 0 && (
        <div className="flex flex-col gap-1.5" data-testid="kg-sheet-app-tables">
          <h3 className="text-[12px] font-medium text-muted-foreground">{count(tables.length, 'table')}</h3>
          <ul className="flex flex-col gap-1">
            {tables.map((t, i) => (
              <li key={`${String(t.key ?? i)}`} className="flex items-center justify-between rounded-chip bg-recess px-2 py-1 text-[12.5px]">
                <span className="truncate">{typeof t.name === 'string' ? t.name : String(t.key ?? 'table')}</span>
                {Array.isArray(t.columns) && <span className="text-muted-foreground tabular-nums">{count(t.columns.length, 'column')}</span>}
              </li>
            ))}
          </ul>
        </div>
      )}

      <div>
        <Button variant="ai" onClick={() => buildWith({ label: node.label, definition: node.summary })} data-testid="kg-build-like-app">
          <Sparkles /> Build one like this
        </Button>
      </div>
    </motion.section>
  )
}
