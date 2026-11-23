import { Link } from '@tanstack/react-router'
import { History } from 'lucide-react'
import { useProjectCtx } from '../ProjectContext'
import { useRunStats } from '../hooks/useRunStats'
import { formatMs, parseTs, timeAgo } from '../lib/format'
import { span, TileHead } from './tiles'
import { MethodBadge } from '@/shared/components/api/MethodBadge'
import { Button } from '@/shared/components/ui/button'
import { StatusDot, runTone } from '@/shared/components/ui/status-dot'
import { Tile, Well } from '@/shared/components/ui/surface'
import { Tip } from '@/shared/components/ui/tooltip'
import { cn } from '@/shared/lib/utils'

export function RecentRunsTile() {
  const { pid, slug } = useProjectCtx()
  const stats = useRunStats(pid)
  const runs = stats.data?.recent ?? []
  return (
    <Tile as="section" className={cn('flex flex-col', span.recent)} data-testid="recent-runs-card">
      <TileHead
        icon={<History />}
        hue="runs"
        title="Recent runs"
        actions={
          <Button variant="ghost" size="sm" render={<Link to="/p/$project/runs" params={{ project: slug }} />} data-testid="recent-runs-all">
            View all
          </Button>
        }
      />
      <Well className="mx-2 mb-2 flex-1">
        {runs.length === 0 ? (
          <div className="px-4 py-5 text-xs text-muted-foreground">No runs yet. Test a route to see one here.</div>
        ) : (
          runs.map((r) => {
            const [method, ...rest] = (r.route ?? '').split(' ')
            const failed = runTone(r.status) === 'err'
            return (
              <Link
                key={r.id}
                to="/p/$project/runs/$executionId"
                params={{ project: slug, executionId: r.id }}
                className="flex h-[34px] items-center gap-2.5 border-b border-hairline px-3 text-[12.5px] tabular-nums transition-colors duration-(--d-fast) last:border-0 hover:bg-hairline"
                data-testid={`recent-run-${r.id}`}
                data-status={r.status}
              >
                <StatusDot tone={runTone(r.status)} flat={!failed} />
                {r.route ? (
                  <>
                    <MethodBadge method={method} size="sm" className="w-[38px] justify-start bg-transparent px-0" />
                    <span className="min-w-0 truncate font-mono">{rest.join(' ')}</span>
                  </>
                ) : (
                  <span className="min-w-0 truncate">{r.workflow_name}</span>
                )}
                <span className="ml-auto flex shrink-0 items-center gap-2.5 whitespace-nowrap text-muted-foreground">
                  {failed && (
                    <span className="text-destructive" data-testid="recent-run-failed">
                      failed{r.status_code != null && <span className="ml-1 font-mono text-[11.5px]" data-testid="recent-run-status-code">{r.status_code}</span>}
                    </span>
                  )}
                  {formatMs(r.duration_ms)}
                  <Tip label={parseTs(r.start_time)?.toLocaleString() ?? ''}>
                    <span className="min-w-9 text-right">{timeAgo(r.start_time)}</span>
                  </Tip>
                </span>
              </Link>
            )
          })
        )}
      </Well>
    </Tile>
  )
}
