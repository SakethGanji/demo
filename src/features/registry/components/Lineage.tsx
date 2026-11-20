import type { ModelVersion } from '@/shared/api/types'
import { ProjectTree } from '@/features/models/components/ProjectTree'

/** P1: the selected version's project tree (dataset root → versions → runs → branches → registered versions), from
 *  GET /api/lineage/version/{id}. Replaces the first-10-versions graph, which had no run→run or dataset→dataset edges. */
export function Lineage({ versions, selected, go }: { versions: ModelVersion[]; selected: string | null; go: (r: string) => void }) {
  const id = selected ?? versions[0]?.id
  return <div data-testid="lineage">{id ? <ProjectTree kind="version" id={id} go={go} /> : null}</div>
}
