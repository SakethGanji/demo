import { Badge } from '@/shared/components/ui/badge'
import { StatusDot } from '@/shared/components/ui/status-dot'
import type { HealthStatus, ProjectSummary } from '@/shared/api/types'

export function HealthBadge({ summary }: { summary: ProjectSummary | undefined }) {
  const s: HealthStatus | undefined = summary?.health.status
  const live = summary?.project.live_environment ?? 'prod'
  const secretCase = s === 'degraded' && (summary?.missing_secrets ?? 0) > 0 && (summary?.health.failed_24h ?? 0) === 0
  if (s === 'ok' || s === 'healthy')
    return (
      <Badge variant="success" data-testid="health-badge" data-health="ok">
        <StatusDot tone="ok" flat /> Healthy
      </Badge>
    )
  if (s === 'degraded')
    return (
      <Badge variant="warning" data-testid="health-badge" data-health="degraded">
        <StatusDot tone="warn" /> Degraded{secretCase ? ` · secret missing in ${live}` : ''}
      </Badge>
    )
  if (s === 'failing')
    return (
      <Badge variant="destructive" data-testid="health-badge" data-health="failing">
        <StatusDot tone="err" /> Failing
      </Badge>
    )
  return (
    <Badge variant="neutral" data-testid="health-badge" data-health="unknown">
      New
    </Badge>
  )
}
