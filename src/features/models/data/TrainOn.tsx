import { useState } from 'react'
import { catalog, type MyTeam, type Project } from '@/shared/api/catalog'
import { trainOn, JOIN_CODES } from '@/shared/api/tables'
import { refusal } from '@/shared/api/governance'

/**
 * U2: start a run on one table of a version (P12 `{table}`) or on a saved join's frame (P14 `{join_id}`). The label is
 * a column of that table (of the grain for a join). P21: a version another team shared is trained into a project of
 * yours (`project_id`); without one the API answers 400 `project_required`, and the project picker is shown.
 */
export function TrainOn({ datasetId, table, joinId, columns, defaultLabel, ownerTeam, team, by, go, what }: {
  datasetId: string; table?: string | null; joinId?: string | null; columns: string[]; defaultLabel?: string | null
  ownerTeam?: string | null; team: MyTeam | null; by: string; go: (r: string) => void; what: string
}) {
  const [label, setLabel] = useState(defaultLabel && columns.includes(defaultLabel) ? defaultLabel : '')
  const [budget, setBudget] = useState(60)
  const shared = !!ownerTeam && !!team && ownerTeam !== team.id
  const [projects, setProjects] = useState<Project[] | null>(null)
  const [project, setProject] = useState('')
  const [need, setNeed] = useState(shared)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const loadProjects = () => { if (!projects) catalog.projects(team?.id).then((p) => { setProjects(p); setProject(p[0]?.id ?? '') }).catch(() => setProjects([])) }
  if (need) loadProjects()
  const start = async () => {
    setBusy(true); setErr('')
    try {
      const r = await trainOn({ dataset_id: datasetId, label, problem_type: 'auto', presets: 'medium_quality', time_limit: budget,
        ...(joinId ? { join_id: joinId } : table ? { table } : {}), ...(need && project ? { project_id: project } : {}) })
      go(`models?run=${r.id}`)
    } catch (e) {
      const x = refusal(e)
      if (x.code === 'project_required') { setNeed(true); loadProjects() }
      setErr(`${x.code && JOIN_CODES[x.code] ? `${JOIN_CODES[x.code]} ` : x.code === 'project_required' ? 'This version belongs to another team: pick a project of yours to train it into. ' : ''}${x.message}${x.code ? ` (${x.code})` : ''}`)
    } finally { setBusy(false) }
  }
  return (
    <div className="row train-on" style={{ gap: 6, flexWrap: 'wrap', alignItems: 'center' }} data-testid={joinId ? 'train-join' : 'train-table'}>
      <span className="train-on-title"><span className="train-ico" aria-hidden>▶</span><b>Train on {what}</b></span>
      <label className="helper row" style={{ gap: 4 }}>predict
        <select className="select" value={label} onChange={(e) => setLabel(e.target.value)} data-testid="train-label" aria-label="Label column">
          <option value="">choose the label…</option>{columns.map((c) => <option key={c}>{c}</option>)}</select></label>
      <label className="helper row" style={{ gap: 4 }}>budget <input className="input mono" type="number" min={30} max={3600} style={{ width: 70 }} value={budget} onChange={(e) => setBudget(Number(e.target.value) || 60)} aria-label="Time budget in seconds" /> s</label>
      {need && <label className="helper row" style={{ gap: 4 }} title="P21: a shared version is trained into a project of your team">into project
        <select className="select" value={project} onChange={(e) => setProject(e.target.value)} data-testid="train-project">
          {(projects ?? []).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label>}
      <button className="btn sm primary" disabled={!label || !by || busy || (need && !project)} onClick={start} data-testid="train-start"
        title={!by ? 'Type your name in the sidebar first' : !label ? 'Choose the column to predict first' : need && !project ? 'Pick a project of your team' : 'AutoGluon, medium quality, on this data; opens the run'}>{busy ? 'Starting…' : 'Start training'}</button>
      {err && <span className="helper up-err" role="alert" data-testid="train-err">✕ {err}</span>}
    </div>
  )
}
