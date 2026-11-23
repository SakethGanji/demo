import { useHashParam } from '@/shared/hooks/useHashParam'
import { RunList } from './RunList'
import { RunPage, type ApplyPatch } from './RunPage'

/** Models: the list of runs, and with ?run= one run's page (Results, Explain, Doctor). */
export function ModelsPage({ go, onApplyPatch }: { go: (r: string) => void; onApplyPatch?: ApplyPatch }) {
  const runId = useHashParam('run')
  if (runId) return <RunPage key={runId} runId={runId} go={go} onApplyPatch={onApplyPatch} />
  return <RunList go={go} />
}
