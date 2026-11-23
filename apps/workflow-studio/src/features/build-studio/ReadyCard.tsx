/**
 * The last message of a build that made its app: a check that draws in over a soft ripple, then Open (the app in the
 * full builder), Share (publish) and Keep editing (the chat and the code stay right here).
 */
import { motion } from 'motion/react'
import { ExternalLink, PencilLine, Send } from 'lucide-react'
import type { Build } from '@/features/plan/buildTypes'
import { buildCounts } from '@/features/plan/buildLogic'
import { SPRING, useCalm } from '@/features/plan/motion'
import { DrawCheck } from '@/features/plan/motionKit'
import { Button } from '@/shared/components/ui/button'

function took(b: Build): string | null {
  if (!b.started_at || !b.finished_at) return null
  const s = Math.max(0, Math.round((Date.parse(b.finished_at) - Date.parse(b.started_at)) / 1000))
  if (!Number.isFinite(s)) return null
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`
}

export function ReadyCard({ build, onOpen, onShare, onKeepEditing }: { build: Build; onOpen: () => void; onShare: () => void; onKeepEditing: () => void }) {
  const calm = useCalm()
  const c = buildCounts(build)
  const time = took(build)
  return (
    <motion.div
      initial={calm ? { opacity: 0 } : { opacity: 0, scale: 0.97, y: 6 }}
      animate={{ opacity: 1, scale: 1, y: 0 }}
      transition={calm ? { duration: 0.18 } : SPRING}
      className="flex flex-col gap-3 rounded-panel bg-recess p-3.5"
      data-testid="studio-ready"
    >
      <div className="flex items-center gap-3">
        <span className="relative grid size-9 shrink-0 place-items-center rounded-full bg-success/12 text-success">
          {!calm ? (
            <motion.span
              aria-hidden
              className="absolute inset-0 rounded-full bg-success/25"
              initial={{ scale: 0.6, opacity: 0.8 }}
              animate={{ scale: 1.9, opacity: 0 }}
              transition={{ duration: 0.9, ease: [0.16, 1, 0.3, 1], delay: 0.15 }}
            />
          ) : null}
          <DrawCheck className="relative size-5" delay={0.1} />
        </span>
        <div className="flex min-w-0 flex-col">
          <span className="text-[14px] font-semibold tracking-[-0.01em]">Your app is ready</span>
          <span className="text-[12px] text-muted-foreground tabular-nums">
            {c.finished} of {c.total} steps{time ? ` · built in ${time}` : ''}
          </span>
        </div>
      </div>
      <div className="flex flex-wrap gap-2">
        <Button size="sm" onClick={onOpen} data-testid="studio-ready-open">
          <ExternalLink /> Open
        </Button>
        <Button size="sm" variant="outline" onClick={onShare} data-testid="studio-ready-share">
          <Send /> Share
        </Button>
        <Button size="sm" variant="ghost" onClick={onKeepEditing} data-testid="studio-ready-edit">
          <PencilLine /> Keep editing
        </Button>
      </div>
    </motion.div>
  )
}
