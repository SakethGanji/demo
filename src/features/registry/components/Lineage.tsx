import type { DatasetSummary, ModelVersion, RegisteredModel, Run } from '@/shared/api/types'
import { Graph, type GEdge, type GNode } from '@/shared/components/Graph'
import { fmtScore } from '@/shared/lib/metrics'
import { bytes } from '../lib'

/** Dataset → run → version → registered model, on graph paper with catenary strings. Production strings carry the
 *  live gradient, staging and pending reviews are dashed, rejected versions are dotted red. */
export function Lineage({ versions, models, runs, datasets, selected }: { versions: ModelVersion[]; models: RegisteredModel[]; runs: Run[]; datasets: DatasetSummary[]; selected: string | null }) {
  const shown = versions.slice(0, 10)
  const runIds = [...new Set(shown.map((v) => v.run_id))]
  const dsIds = [...new Set(runIds.map((r) => runs.find((x) => x.id === r)?.dataset_id).filter(Boolean) as string[])]
  const mNames = [...new Set(shown.map((v) => v.model_name))]
  const ROW = 78, NW = 224, NH = 60
  const col = (i: number, n: number, total: number) => 16 + (i + (total - n) / 2) * ROW
  const total = Math.max(dsIds.length, runIds.length, shown.length, mNames.length, 1)
  const nodes: GNode[] = []
  const edges: GEdge[] = []
  dsIds.forEach((d, i) => {
    const ds = datasets.find((x) => x.id === d)
    nodes.push({ id: `d:${d}`, x: 16, y: col(i, dsIds.length, total), title: ds?.name ?? d, sub: ds ? `${ds.rows.toLocaleString()} rows · ${ds.n_cols} cols` : d })
  })
  runIds.forEach((r, i) => {
    const run = runs.find((x) => x.id === r)
    const m = run?.eval_metric?.name
    nodes.push({ id: `r:${r}`, x: 286, y: col(i, runIds.length, total), title: r, sub: run ? `${run.best_model ?? ''} · ${m} ${fmtScore(run.holdout?.metrics[m ?? ''], 3)}` : '' })
    if (run) edges.push({ from: `d:${run.dataset_id}`, to: `r:${r}` })
  })
  shown.forEach((v, i) => {
    nodes.push({ id: `v:${v.id}`, x: 556, y: col(i, shown.length, total), title: `${v.model_name.length > 22 ? `${v.model_name.slice(0, 21)}…` : v.model_name} v${v.version}`,
      sub: v.status !== 'ready' ? v.status : `${v.stage} · ${v.approval} · ${bytes(v.summary.size_bytes)}`, active: v.id === selected, warn: v.approval === 'rejected' ? 'rejected' : undefined })
    edges.push({ from: `r:${v.run_id}`, to: `v:${v.id}`, kind: v.stage === 'Production' ? 'active' : v.approval === 'rejected' ? 'blocked' : v.approval === 'pending' ? 'warn' : 'ok' })
  })
  mNames.forEach((n, i) => {
    const m = models.find((x) => x.name === n)
    nodes.push({ id: `m:${n}`, x: 826, y: col(i, mNames.length, total), title: `models:/${n.length > 20 ? `${n.slice(0, 19)}…` : n}`, sub: m ? `Production v${m.production ?? '—'} · Staging v${m.staging ?? '—'}` : '' })
  })
  shown.forEach((v) => { if (v.stage === 'Production' || v.stage === 'Staging') edges.push({ from: `v:${v.id}`, to: `m:${v.model_name}`, kind: v.stage === 'Production' ? 'active' : 'warn' }) })
  return <div data-testid="lineage"><Graph width={1066} height={16 + total * ROW} nodeW={NW} nodeH={NH} nodes={nodes} edges={edges} /></div>
}
