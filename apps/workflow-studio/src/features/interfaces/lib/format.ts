/** Display helpers for tasks and runs (no JSX). */
import type { TaskStatus } from './types'

export function formatWhen(iso: string | null | undefined): string {
  if (!iso) return ''
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? String(iso) : d.toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

/** "14:02" */
export function formatClock(iso: string | null | undefined): string {
  if (!iso) return ''
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? String(iso) : d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false })
}

export const TASK_STATUS_LABEL: Record<TaskStatus, string> = {
  open: 'Open',
  approved: 'Approved',
  rejected: 'Rejected',
  timed_out: 'Timed out',
  cancelled: 'Cancelled',
}

export type StatusTone = 'warning' | 'success' | 'error' | 'neutral'

export function taskTone(status: string): StatusTone {
  if (status === 'open') return 'warning'
  if (status === 'approved') return 'success'
  if (status === 'rejected') return 'error'
  return 'neutral'
}

export function taskStatusLabel(status: string): string {
  return TASK_STATUS_LABEL[status as TaskStatus] ?? status
}

/** "3 waiting" */
export function waitingLabel(n: number): string {
  return `${n} waiting`
}

export function pluralize(n: number, word: string): string {
  return `${n} ${word}${n === 1 ? '' : 's'}`
}
