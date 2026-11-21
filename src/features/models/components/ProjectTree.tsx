import { useMemo, useState } from 'react'
import { catalog, type LEdge, type LNode, type LReview, type LineageTree, type NodeKind } from '@/shared/api/catalog'
import { useFetch } from '@/shared/hooks/useFetch'
import { ErrorCard, Loading } from '@/shared/components/Status'
import { fmtScore } from '@/shared/lib/metrics'
import './project-tree.css'

/** Where a run came from: the colour of its dot. */
const SOURCE: Record<string, string> = { user: 'you', wizard: 'you', client: 'client', gym: 'Gym', vela: 'Vela patch', 'vela-agent': 'agent', 'doctor-experiment': 'experiment', 'split-twin': 'split twin', 'metric-twin': 'metric twin', 'anomaly-supervised': 'from anomalies', import: 'import' }
const srcClass = (s?: string | null) => `src-${(s ?? 'user').toLowerCase().replace(/[^a-z]/g, '')}`
const KEPT = new Set(['kept', 'kept-honest'])
const verdictClass = (v?: string) => (!v ? '' : KEPT.has(v) ? 'v-kept' : v === 'rejected' ? 'v-rejected' : 'v-neutral')

/** The project tree: dataset root → versions → runs → branches (agent and experiment branches collapsed under their
 *  review) → registered versions. One request: GET /api/lineage/{kind}/{id} (a recursive CTE over the edge table). */
export function ProjectTree({ kind, id, go, compact = false }: { kind: NodeKind; id: string; go: (r: string) => void; compact?: boolean }) {
  const { data, err } = useFetch(() => catalog.lineage(kind, id), [kind, id])
  if (err && !data) return <ErrorCard inline err={err} />
  if (!data) return <Loading inline>Reading the project's lineage…</Loading>
  return <Tree t={data} go={go} compact={compact} />
}

function Tree({ t, go, compact }: { t: LineageTree; go: (r: string) => void; compact: boolean }) {
  const ix = useMemo(() => {
    const node = new Map(t.nodes.map((n) => [`${n.kind}:${n.id}`, n]))
    const out = new Map<string, LEdge[]>()
    const branchedRuns = new Set<string>()
    for (const e of t.edges) {
      const k = `${e.src.kind}:${e.src.id}`
      out.set(k, [...(out.get(k) ?? []), e])
      if (e.relation === 'branched_from') branchedRuns.add(e.dst.id)
    }
    return { node, out, branchedRuns, reviews: new Map(t.reviews.map((r) => [r.id, r])) }
  }, [t])
  const root = ix.node.get(`${t.root.kind}:${t.root.id}`)
  // a user project can hold several uploads: every dataset with no parent in the tree is a root
  const roots = t.nodes.filter((n) => n.kind === 'dataset' && !t.edges.some((e) => e.relation === 'derived_from' && e.dst.id === n.id))
  const focus = `${t.focus.kind}:${t.focus.id}`
  const legend = (
    <div className="pt-legend helper">
      <span><i className="pt-dot src-you" />you</span><span><i className="pt-dot src-experiment" />experiment</span><span><i className="pt-dot src-agent" />agent</span><span><i className="pt-dot src-gym" />Gym / patch</span>
      <span><i className="pt-v v-kept" />kept</span><span><i className="pt-v v-rejected" />rejected</span><span className="mono">◇ inferred edge</span>
    </div>
  )
  if (!root) return <p className="helper">{t.root.kind === 'project' ? 'This project has no datasets yet: upload into it (Data → Upload, project) or move a dataset into it.' : 'No lineage recorded yet.'}</p>
  const ctx: Ctx = { ix, go, focus }
  return (
    <div className="project-tree" data-testid="project-tree" data-root={t.root.id}>
      {!compact && <div className="pt-head"><span className="micro-label">project</span> <b>{t.project?.name ?? t.root.id}</b>
        <span className="helper mono"> · {t.counts.datasets} datasets · {t.counts.runs} runs · {t.counts.versions} versions</span>
        {t.champion && <span className="champ-badge" title={`${t.champion.version_id} · approved by ${t.champion.decided_by}`}>★ champion {champName(t)}</span>}</div>}
      {legend}
      <ul className="pt">{roots.map((r) => <DatasetNode key={r.id} n={r} ctx={ctx} />)}</ul>
    </div>
  )
}

type Ctx = { ix: { node: Map<string, LNode>; out: Map<string, LEdge[]>; branchedRuns: Set<string>; reviews: Map<string, LReview> }; go: (r: string) => void; focus: string }

function DatasetNode({ n, ctx, edge }: { n: LNode; ctx: Ctx; edge?: LEdge }) {
  const outs = ctx.ix.out.get(`dataset:${n.id}`) ?? []
  const versions = outs.filter((e) => e.relation === 'derived_from')
  const runs = outs.filter((e) => e.relation === 'trained_on' && !ctx.ix.branchedRuns.has(e.dst.id))
  return (
    <li data-testid={`pt-dataset-${n.id}`} className={ctx.focus === `dataset:${n.id}` ? 'pt-focus' : ''}>
      <div className="pt-row"><span className="pt-ico">▤</span><b>{n.name}</b> <span className="mono muted">{n.id}</span>
        <span className="pt-chip">{(n.rows ?? 0).toLocaleString()} rows</span>{n.op && n.op !== 'upload' && n.op !== 'sample' && <span className="pt-chip op">{n.op.replace(/_/g, ' ')}</span>}
        {edge?.inferred && <span className="mono muted" title="no root-row mapping: never paired with its parent">◇</span>}
        {n.state && <span className={`pt-chip ${n.state === 'deleted' ? 'appr-rejected' : ''}`} title={n.state === 'deleted' ? 'files removed; this record, its lineage and the audit are kept' : 'hidden from Build and the pickers'}>{n.state}</span>}
        <Tags n={n} />
        {!edge && <button className="chip-btn" onClick={() => ctx.go(`models?tab=Data&dataset=${n.id}`)} data-testid={`pt-data-${n.id}`} title="Versions, diff, what used each one, card, export">Data →</button>}</div>
      {(versions.length > 0 || runs.length > 0) && <ul>
        {runs.map((e) => { const r = ctx.ix.node.get(`run:${e.dst.id}`); return r ? <RunNode key={r.id} n={r} ctx={ctx} trained={e} /> : null })}
        {versions.map((e) => { const d = ctx.ix.node.get(`dataset:${e.dst.id}`); return d ? <DatasetNode key={d.id} n={d} ctx={ctx} edge={e} /> : null })}
      </ul>}
    </li>
  )
}

function RunNode({ n, ctx, edge, trained }: { n: LNode; ctx: Ctx; edge?: LEdge; trained?: LEdge }) {
  const sha = trained?.meta.data_sha256 as string | undefined
  const outs = ctx.ix.out.get(`run:${n.id}`) ?? []
  const branches = outs.filter((e) => e.relation === 'branched_from')
  const builds = outs.filter((e) => e.relation === 'built_from')
  const byReview = new Map<string, LEdge[]>()
  const loose: LEdge[] = []
  for (const b of branches) b.meta.review_id ? byReview.set(b.meta.review_id, [...(byReview.get(b.meta.review_id) ?? []), b]) : loose.push(b)
  const v = edge?.meta.verdict
  const src = n.source ?? 'user'
  return (
    <li data-testid={`pt-run-${n.id}`} className={ctx.focus === `run:${n.id}` ? 'pt-focus' : ''}>
      <div className="pt-row">
        <i className={`pt-dot ${srcClass(SOURCE[src] ?? src)}`} title={`source: ${src}`} />
        <button className="linkish" onClick={() => ctx.go(`models?run=${n.id}`)}>{n.name}</button> <span className="mono muted">{n.id}</span>
        {edge?.meta.patch && <span className="pt-chip">{String(edge.meta.patch)}</span>}
        {v && <span className={`pt-v ${verdictClass(v)}`} data-testid={`pt-verdict-${n.id}`}>{v}</span>}
        {sha && <span className={`pt-chip mono ${trained?.meta.data_sha256_match === false ? 'appr-rejected' : ''}`} title={`DL3: sha256 of the bytes this run read: ${sha}${trained?.meta.data_sha256_match === false ? ' (differs from its version)' : ''}`}>data {sha.slice(0, 8)}{trained?.meta.data_sha256_match === false ? ' ✕' : ''}</span>}
        <span className="mono pt-score">{n.status !== 'succeeded' ? n.status : n.primary_metric ? `${n.primary_metric} ${fmtScore(n.primary_score, 4)}` : ''}</span>
        {n.predictor_state && n.predictor_state !== 'present' && n.predictor_state !== 'none' && <span className={`pt-chip state-${n.predictor_state}`} title={n.predictor_state === 'evicted' ? 'predictor deleted by retention: scores and holdout predictions kept; re-fit from config to predict' : 'only the best model kept'}>{n.predictor_state}</span>}
        <Tags n={n} />
      </div>
      {(branches.length > 0 || builds.length > 0) && <ul>
        {builds.map((e) => { const x = ctx.ix.node.get(`version:${e.dst.id}`); return x ? <VersionNode key={x.id} n={x} ctx={ctx} /> : null })}
        {loose.map((e) => { const r = ctx.ix.node.get(`run:${e.dst.id}`); return r ? <RunNode key={r.id} n={r} ctx={ctx} edge={e} /> : null })}
        {[...byReview.entries()].map(([rid, es]) => <ReviewGroup key={rid} rid={rid} edges={es} ctx={ctx} />)}
      </ul>}
    </li>
  )
}

function ReviewGroup({ rid, edges, ctx }: { rid: string; edges: LEdge[]; ctx: Ctx }) {
  const rv = ctx.ix.reviews.get(rid)
  const inFocus = edges.some((e) => ctx.focus === `run:${e.dst.id}`)
  const [open, setOpen] = useState(inFocus)
  const kept = edges.filter((e) => KEPT.has(e.meta.verdict ?? '')).length
  const agent = edges.some((e) => e.meta.source === 'vela-agent') || rv?.mode === 'llm'
  return (
    <li data-testid={`pt-review-${rid}`}>
      <div className="pt-row"><button className="pt-toggle" onClick={() => setOpen(!open)} aria-expanded={open}>{open ? '▾' : '▸'}</button>
        <i className={`pt-dot ${agent ? 'src-agent' : 'src-experiment'}`} />
        <b>Review {rid}</b><span className="helper"> · {rv?.status ?? '—'}{rv?.by ? ` · ${rv.by}` : ''} · {edges.length} branch{edges.length === 1 ? '' : 'es'} · {kept} kept</span></div>
      {open && <ul>{edges.map((e) => { const r = ctx.ix.node.get(`run:${e.dst.id}`); return r ? <RunNode key={r.id} n={r} ctx={ctx} edge={e} /> : null })}</ul>}
    </li>
  )
}

function VersionNode({ n, ctx }: { n: LNode; ctx: Ctx }) {
  return (
    <li data-testid={`pt-version-${n.id}`} className={ctx.focus === `version:${n.id}` ? 'pt-focus' : ''}>
      <div className="pt-row"><span className="pt-ico">◆</span>
        <button className="linkish" onClick={() => ctx.go(`registry?version=${n.id}`)}>{n.model_name} v{n.version}</button>
        <span className="pt-chip">{n.stage}</span><span className={`pt-chip appr-${n.approval}`}>{n.approval}</span>
        {n.champion && <span className="champ-badge">★ champion</span>}<Tags n={n} /></div>
    </li>
  )
}

/** The champion by name and version (the tree holds its version node), not by id. */
const champName = (t: LineageTree) => {
  const v = t.nodes.find((n) => n.kind === 'version' && n.id === t.champion?.version_id)
  return v ? `${v.model_name} v${v.version}` : t.champion?.version_id ?? ''
}

const Tags = ({ n }: { n: LNode }) => <>{n.tags.map((t) => <span key={t} className={`pt-tag${t.toLowerCase() === 'keep' ? ' keep' : ''}`}>#{t}</span>)}{n.notes > 0 && <span className="muted" title={`${n.notes} note(s)`}> ✎{n.notes}</span>}</>
