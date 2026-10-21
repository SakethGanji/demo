/**
 * What a draft changes against the version it revises: each change (breaking ones first) and the modules, apps and
 * tables that have to be rebuilt or migrated.
 */
import { GitCompare } from 'lucide-react'
import { Badge } from '@/shared/components/ui/badge'
import { SectionHeader, Tile, WellList, WellRow } from '@/shared/components/ui/surface'
import type { PlanDiffResult } from './planTypes'

export function PlanDiff({ diff, against }: { diff: PlanDiffResult; against: number | null }) {
  const changes = [...diff.changes].sort((a, b) => Number(b.breaking) - Number(a.breaking))
  const a = diff.affected
  return (
    <Tile className="mt-5 p-4" data-testid="plan-diff">
      <SectionHeader
        title={against != null ? `Changes against v${against}` : 'Changes'}
        sub={`${changes.length} change${changes.length === 1 ? '' : 's'}${diff.breaking ? ' · breaking' : ''}`}
        icon={<GitCompare />}
        hue="release"
      />
      {changes.length === 0 ? (
        <div className="mt-2 text-[12.5px] text-muted-foreground">No changes.</div>
      ) : (
        <WellList className="mt-3">
          {changes.map((c, n) => (
            <WellRow key={`${c.kind}-${c.key}-${n}`} data-testid="plan-diff-change">
              <Badge variant={c.breaking ? 'destructive' : c.change === 'added' ? 'success' : 'neutral'}>{c.change}</Badge>
              <span className="text-muted-foreground">{c.kind}</span>
              <span className="min-w-0 flex-1 truncate font-mono text-[12px]">{c.key}</span>
              {c.breaking ? <span className="text-[12px] text-destructive">breaking</span> : null}
            </WellRow>
          ))}
        </WellList>
      )}
      {a.modules.length || a.apps.length || a.tables.length ? (
        <div className="mt-3 flex flex-wrap items-center gap-1.5 text-[12.5px]" data-testid="plan-diff-affected">
          <span className="text-muted-foreground">Affects</span>
          {a.modules.map((m) => <Badge key={`m-${m}`} variant="neutral">module {m}</Badge>)}
          {a.apps.map((m) => <Badge key={`a-${m}`} variant="neutral">app {m}</Badge>)}
          {a.tables.map((m) => <Badge key={`t-${m}`} variant="neutral">table {m}</Badge>)}
        </div>
      ) : null}
    </Tile>
  )
}
