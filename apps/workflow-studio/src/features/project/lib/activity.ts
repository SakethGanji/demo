import type { DailyRuns } from '@/shared/api/types'
import type { ActivityDay } from '@/shared/components/ui/charts'

/** `GET …/runs/daily` rows → the Activity grid's days (oldest first) and the 30-day failure total. */
export function toActivity(daily: DailyRuns[]): { days: ActivityDay[]; failed: number; total: number } {
  const days = [...daily]
    .sort((a, b) => a.date.localeCompare(b.date))
    .map((d) => ({ date: d.date, count: d.total, failed: d.failed }))
  return { days, failed: days.reduce((n, d) => n + d.failed, 0), total: days.reduce((n, d) => n + d.count, 0) }
}
