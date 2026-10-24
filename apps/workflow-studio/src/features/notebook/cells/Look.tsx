/** Small decorative pieces: a person's gradient avatar, a "by X" line, a coloured status-value pill. */
import { useQuery } from '@tanstack/react-query'
import { executionsApi, workflowsApi, type ApiExecutionDetail } from '@/shared/lib/api'
import { avatarGradient, initials, statusTone } from '../lib/look'

/** Decorative: the initials are drawn by CSS (attr), so the person's name stays the only text a reader gets. */
export function Avatar({ name, size }: { name: string; size?: 'sm' | 'md' }) {
  const g = avatarGradient(name)
  return <span className="nb-av" aria-hidden data-i={initials(name)} data-size={size} style={g ? { background: g } : undefined} />
}

/** `<avatar> by <b>name</b>` used on outcome lines; the words read exactly as before. */
export function ByName({ name, prefix = 'by' }: { name: string; prefix?: string }) {
  return (
    <span className="nb-byname">
      {prefix} <Avatar name={name} size="sm" />
      <b>{name}</b>
    </span>
  )
}

export function StatusPill({ value }: { value: string }) {
  return (
    <span className="nb-st" data-tone={statusTone(value)}>
      {value}
    </span>
  )
}

/** The workflow's name for display; the id stays the fallback while it loads or when the lookup fails. */
export function useWorkflowName(id: string | undefined): string | null {
  const ok = !!id && !id.includes('{{')
  const q = useQuery({ queryKey: ['workflow', id, 'name'], queryFn: () => workflowsApi.get(id!), enabled: ok, retry: false, staleTime: 60_000 })
  return q.data?.name ?? null
}

export interface ExecChild { execution_id: string; node: string; workflow_id: string; workflow_name: string; version_number?: number | null; status: string; duration_ms?: number | null }
export type ExecDetail = ApiExecutionDetail & { attribution?: Record<string, unknown> | null; children?: ExecChild[] }

/** The engine execution a workflow cell started (name, steps, sub-runs); null until loaded. */
export function useExecution(id: string | undefined | null): ExecDetail | null {
  const q = useQuery({ queryKey: ['execution', id], queryFn: () => executionsApi.get(id!) as Promise<ExecDetail>, enabled: !!id, retry: false, staleTime: 30_000 })
  return q.data ?? null
}
