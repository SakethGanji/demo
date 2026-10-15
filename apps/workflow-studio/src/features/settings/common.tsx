/**
 * Small pieces shared by the Settings sheet sections (UI-DESIGN §3.7).
 */
import { useState } from 'react'
import { Check, Copy } from 'lucide-react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useProjectCtx } from '@/features/project/ProjectContext'
import { projectsApi } from '@/shared/api/endpoints'
import { invalidate, qk } from '@/shared/api/queryKeys'
import type { Project, ProjectUpdate } from '@/shared/api/types'
import { Button } from '@/shared/components/ui/button'
import { cn } from '@/shared/lib/utils'

export const SECTIONS = [
  { key: 'general', label: 'General' },
  { key: 'environments', label: 'Environments' },
  { key: 'egress', label: 'Egress' },
  { key: 'keys', label: 'API keys' },
  { key: 'developer', label: 'Developer' },
  { key: 'danger', label: 'Danger' },
] as const
export type SectionKey = (typeof SECTIONS)[number]['key']

export function isSection(s: string | undefined): s is SectionKey {
  return !!s && SECTIONS.some((x) => x.key === s)
}

export function SectionHeader({ title, sub, actions }: { title: string; sub?: React.ReactNode; actions?: React.ReactNode }) {
  return (
    <div className="flex min-h-7 items-center gap-2">
      <span className="text-[13px] font-semibold">{title}</span>
      {sub && <span className="min-w-0 truncate text-[12.5px] text-muted-foreground">{sub}</span>}
      <div className="flex-1" />
      {actions}
    </div>
  )
}

/** A secret value shown exactly once, with a Copy button ("shown once, copy it now"). */
export function OnceBox({
  icon,
  title,
  value,
  note = 'shown once — copy it now',
  footer,
  testId,
}: {
  icon?: React.ReactNode
  title: React.ReactNode
  value: string
  note?: string
  footer?: React.ReactNode
  testId?: string
}) {
  const [copied, setCopied] = useState(false)
  return (
    <div
      className="rounded-[10px] border border-[color-mix(in_srgb,var(--primary)_35%,var(--border))] bg-[color-mix(in_srgb,var(--primary)_4%,var(--card))] p-3"
      data-testid={testId}
    >
      <div className="flex flex-wrap items-center gap-2 [&_svg]:size-3.5">
        {icon}
        <span className="font-semibold">{title}</span>
        <span className="text-xs text-muted-foreground">{note}</span>
      </div>
      <div className="mt-2 flex items-center gap-2">
        <input
          readOnly
          value={value}
          onFocus={(e) => e.currentTarget.select()}
          className="h-8 min-w-0 flex-1 rounded-lg border border-input bg-background px-2.5 font-mono text-[11.5px] outline-none focus-visible:border-ring"
          data-testid={testId ? `${testId}-value` : undefined}
        />
        <Button
          size="sm"
          onClick={() => {
            void navigator.clipboard?.writeText(value).catch(() => undefined)
            setCopied(true)
            setTimeout(() => setCopied(false), 1500)
          }}
          data-testid={testId ? `${testId}-copy` : undefined}
        >
          {copied ? <Check /> : <Copy />} {copied ? 'Copied' : 'Copy'}
        </Button>
      </div>
      {footer && <div className="mt-1.5 text-[11.5px] text-muted-foreground">{footer}</div>}
    </div>
  )
}

/** Writes the updated project into every cache that holds it (UI-DESIGN D10: keys use the id). */
export function useApplyProject() {
  const qc = useQueryClient()
  return async (p: Project) => {
    qc.setQueryData(qk.projectRef(p.slug), p)
    qc.setQueryData(qk.projectRef(p.id), p)
    await invalidate.projectUpdated(qc, p.id)
  }
}

/** `PUT /api/projects/{P}`; the caller renders errors inline. Does not handle slug renames. */
export function useProjectUpdate() {
  const { pid, slug } = useProjectCtx()
  const qc = useQueryClient()
  const apply = useApplyProject()
  return useMutation({
    mutationFn: (b: ProjectUpdate) => projectsApi.update(pid, b),
    onSuccess: async (p) => {
      if (p.slug === slug) qc.setQueryData(qk.projectRef(slug), p)
      await apply(p)
    },
  })
}

export function InlineError({ children, testId, className }: { children: React.ReactNode; testId?: string; className?: string }) {
  return (
    <div className={cn('text-[11.5px] leading-4 text-destructive', className)} role="alert" data-testid={testId}>
      {children}
    </div>
  )
}
