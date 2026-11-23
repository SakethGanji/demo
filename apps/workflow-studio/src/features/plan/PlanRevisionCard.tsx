/**
 * An app-chat ask that needs the plan changed first (a new table, route or role: the planner owns the data model,
 * LIVE-FILES §5): what the revision changes, and Approve, which approves the draft and sends the ask again so the
 * builders go on against the new plan. A breaking revision asks once more before it is approved.
 */
import { useState } from 'react'
import { Link } from '@tanstack/react-router'
import { motion } from 'motion/react'
import { CheckCircle2, ClipboardList, GitCompare } from 'lucide-react'
import { toast } from 'sonner'
import { useProjectCtx } from '@/features/project/ProjectContext'
import type { PlanRevision } from '@/features/app-builder/lib/chatEvents'
import { ApiError, errorMessage } from '@/shared/api/http'
import { Badge } from '@/shared/components/ui/badge'
import { Button } from '@/shared/components/ui/button'
import { Spinner } from '@/shared/components/ui/spinner'
import { Callout, IconTile } from '@/shared/components/ui/surface'
import { FADE, SPRING, useCalm } from './motion'
import { breakingSummary } from './planLogic'
import { usePlanMutations } from './usePlan'

export function PlanRevisionCard({ revision, prompt, onContinue }: { revision: PlanRevision; prompt?: string; onContinue?: (prompt: string) => void }) {
  const { pid, slug } = useProjectCtx()
  const pm = usePlanMutations(pid)
  const calm = useCalm()
  const [state, setState] = useState<'open' | 'breaking' | 'approved'>('open')
  const changes = revision.diff ? [...revision.diff.changes].sort((a, b) => Number(b.breaking) - Number(a.breaking)) : []

  const approve = (acceptBreaking: boolean) =>
    pm.approve.mutate(
      { version: revision.version, acceptBreaking },
      {
        onSuccess: () => {
          setState('approved')
          if (prompt) onContinue?.(prompt)
        },
        onError: (e) => {
          if (e instanceof ApiError && e.code === 'breaking_change') setState('breaking')
          else toast.error('Not approved', { description: errorMessage(e) })
        },
      },
    )

  return (
    <motion.div
      initial={calm ? { opacity: 0 } : { opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={calm ? FADE : SPRING}
      className="@container/plan mt-1 flex min-w-0 flex-col gap-2.5 rounded-panel bg-recess p-3"
      data-testid="chat-plan-revision"
      data-state={state}
    >
      <div className="flex items-center gap-2">
        <IconTile hue="release" className="size-6"><GitCompare /></IconTile>
        <span className="text-[13px] font-semibold">This needs a plan change</span>
        <Badge variant="neutral" className="ml-auto">draft v{revision.version}</Badge>
      </div>
      {revision.reason ? <p className="text-[12.5px] leading-[18px] text-foreground/85">{revision.reason}</p> : null}
      {changes.length ? (
        <ul className="flex flex-col gap-1">
          {changes.map((c, n) => (
            <li key={`${c.kind}-${c.key}-${n}`} className="flex items-center gap-1.5 text-[12px]" data-testid="chat-plan-revision-change">
              <Badge variant={c.breaking ? 'destructive' : c.change === 'added' ? 'success' : 'neutral'}>{c.change}</Badge>
              <span className="text-muted-foreground">{c.kind}</span>
              <span className="min-w-0 flex-1 truncate font-mono text-[11.5px]">{c.key}</span>
            </li>
          ))}
        </ul>
      ) : null}
      {state === 'breaking' && revision.diff ? (
        <Callout tone="warn">{breakingSummary(revision.diff)}. Builders rebuild what it affects; nothing in the tables is dropped.</Callout>
      ) : null}
      {state === 'approved' ? (
        <span className="flex items-center gap-1.5 text-[12.5px] text-success" data-testid="chat-plan-revision-approved">
          <CheckCircle2 className="size-3.5" /> Approved: building it now
        </span>
      ) : (
        <div className="flex flex-wrap gap-2">
          <Button size="xs" variant="outline" render={<Link to="/p/$project/plan" params={{ project: slug }} />}>
            <ClipboardList /> Review in Plan
          </Button>
          <Button size="xs" className="ml-auto" disabled={pm.approve.isPending} onClick={() => approve(state === 'breaking')} data-testid="chat-plan-revision-approve">
            {pm.approve.isPending ? <Spinner /> : <CheckCircle2 />} {state === 'breaking' ? 'Approve anyway' : 'Approve and continue'}
          </Button>
        </div>
      )}
    </motion.div>
  )
}
