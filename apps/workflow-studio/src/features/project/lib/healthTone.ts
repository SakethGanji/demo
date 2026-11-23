import type { DotTone } from '@/shared/components/ui/status-dot'
import type { HealthStatus } from '@/shared/api/types'

export function healthTone(s: HealthStatus | undefined): DotTone {
  if (s === 'ok' || s === 'healthy') return 'ok'
  if (s === 'degraded') return 'warn'
  if (s === 'failing') return 'err'
  return 'idle'
}

/** A project's avatar: its initial on the brand gradient (avatars are the one other place it appears). */
