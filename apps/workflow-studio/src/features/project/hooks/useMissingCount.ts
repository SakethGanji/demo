import { useOutlineDraftStore } from '@/features/backend/outlineDraftStore'
import { useProjectSummary } from './useProjectData'

/** Missing secrets: the summary's count plus keys only an unsaved backend draft reads (client scan, §4.5). */
export function useMissingCount(pid: string) {
  const summary = useProjectSummary(pid)
  const drafts = useOutlineDraftStore((s) => s.drafts)
  const extra = new Set<string>()
  for (const d of Object.values(drafts)) if (d.projectId === pid && d.dirty) d.missingSecretKeys.forEach((k) => extra.add(k))
  return (summary.data?.missing_secrets ?? 0) + extra.size
}
