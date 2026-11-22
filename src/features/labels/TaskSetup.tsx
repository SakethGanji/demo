import { useState } from 'react'
import { labels, specCol, type Layer, type Task, type TaskCreate, type TaskType } from '@/shared/api/labels'
import { useCan, why } from '@/shared/hooks/useCan'
import { CardHead, Callout, Field, Flash, Steps, Why } from '@/shared/components/DataKit'
import { errText } from './parts'

const STEPS = ['Schema', 'Guidelines', 'Queue', 'People and gold']
const STRATEGIES: [string, string][] = [
  ['random', 'random rows not labelled yet'], ['active', 'the model is least sure (needs a finished run)'], ['cleanlab', 'rows whose label looks wrong (cleanlab)'],
  ['disputed', 'rows where labellers disagree'], ['llm_low_conf', 'LLM labels below τ*'], ['gold', 'gold rows only'], ['manual', 'row ids you list'],
]
type Person = { name: string; role: 'annotator' | 'reviewer' }

/** LB-C: a labeling task in four steps: the schema (classes with keys), the guidelines, which rows and how many votes,
 *  and who (annotators, guests limited to their own queue, reviewers) with a gold set graded blind. */
export function TaskSetup({ layer, root, vid, v, cols, by, onCancel, onMade }: { layer: Layer; root: string; vid: string; v: number; cols: string[]; by: string
  onCancel: () => void; onMade: (t: Task, text: string) => void }) {
  const can = useCan('label.task_manage', 'dataset', root, by)
  const sc = layer.schema
  const col0 = specCol(sc?.spec)
  const [at, setAt] = useState(0)
  const [name, setName] = useState(`${layer.name} · round 1`)
  const [type, setType] = useState<TaskType>('class')
  const [column, setColumn] = useState(col0?.column ?? cols[cols.length - 1] ?? '')
  const [classes, setClasses] = useState((col0?.classes ?? []).join(', '))
  const [context, setContext] = useState<string[]>(() => cols.filter((c) => c !== (col0?.column ?? '')).slice(0, 8))
  const [guide, setGuide] = useState(sc?.guidelines ?? '')
  const [strategy, setStrategy] = useState('random')
  const [n, setN] = useState(50)
  const [votes, setVotes] = useState(1)
  const [review, setReview] = useState<'none' | 'disputed' | 'all'>('disputed')
  const [goldShare, setGoldShare] = useState(5)
  const [blindShare, setBlindShare] = useState(10)
  const [minGold, setMinGold] = useState(80)
  const [people, setPeople] = useState<Person[]>([{ name: by, role: 'annotator' }, { name: by, role: 'reviewer' }])
  const [gold, setGold] = useState('')
  const [calib, setCalib] = useState(true)
  const [calibN, setCalibN] = useState(1000)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const cs = classes.split(',').map((x) => x.trim()).filter(Boolean)
  const goldRows = gold.split('\n').map((l) => l.trim()).filter(Boolean).map((l) => { const [id, ...rest] = l.split(/[=,:\s]+/); return { row_id: Number(id), value: rest.join(' ').trim() } })
  const badGold = goldRows.filter((g) => !Number.isInteger(g.row_id) || (type === 'class' && !cs.includes(g.value)))
  const okStep = [!!name.trim() && !!column && (type !== 'class' && type !== 'multi_label' || cs.length >= 2), true, n > 0, people.some((p) => p.name.trim() && p.role === 'annotator') && !badGold.length]
  const create = async () => {
    setBusy(true); setMsg(null)
    try {
      // the schema first: a new version when the classes or the guidelines differ from the layer's (labels keep theirs)
      const ctype = type === 'number' ? 'number' : type === 'text' ? 'text' : 'class'
      const same = col0 && col0.column === column && (col0.classes ?? []).join('|') === cs.join('|') && (sc?.guidelines ?? '') === guide
      if (!same) await labels.addSchema(layer.id, { columns: [{ column, role: 'target', type: ctype, ...(ctype === 'class' ? { classes: cs } : {}), ...(type === 'multi_label' ? { multi: true } : {}) }] }, guide, by)
      const t = await labels.createTask(layer.id, { name: name.trim(), task_type: type, columns: [column], context, votes_per_row: votes, review,
        gold_share: goldShare / 100, blind_share: blindShare / 100, min_gold_accuracy: minGold / 100, show_prelabel: true, guidelines: guide, by } as TaskCreate & { guidelines: string })
      const guests: string[] = []
      for (const p of people.filter((x) => x.name.trim())) { const m = await labels.addMember(t.id, p.name.trim(), p.role, by); if (m.guest && !guests.includes(p.name.trim())) guests.push(p.name.trim()) }
      let nGold = 0, nCal = 0
      if (goldRows.length) nGold = (await labels.setGold(t.id, { pool: 'qa', column, items: goldRows }, by)).added
      if (calib && calibN > 0) nCal = (await labels.setGold(t.id, { pool: 'calibration', column, from_version: vid, n: calibN }, by)).added
      const f = await labels.fill(t.id, { strategy, n, version_id: vid }, by) as { rows?: number; items?: number; gold_items?: number; excluded_holdout?: number }
      onMade(t, `Task ${t.name} created: ${f.items ?? f.rows ?? n} items queued (${strategy}) from v${v}${f.excluded_holdout ? `, ${f.excluded_holdout} holdout rows kept out` : ''}; `
        + `${people.length} ${people.length === 1 ? 'membership' : 'memberships'}${guests.length ? ` (guest: ${guests.join(', ')})` : ''}${nGold ? `, ${nGold} QA gold` : ''}${nCal ? `, ${nCal} calibration gold` : ''}.`)
    } catch (e) { setMsg({ ok: false, text: errText(e) }) } finally { setBusy(false) }
  }
  return (
    <div className="card lbl" data-testid="lbl-task-setup">
      <CardHead title={<>New task on <span className="mono">{layer.name}</span></>} sub={`rows come from v${v}; the labels land in the layer, not the version`}
        actions={<Steps steps={STEPS} at={at} testId="lbl-task-steps" />} />
      <div className="card-body stack" style={{ gap: 12 }}>
        {at === 0 && <>
          <div className="form-row">
            <Field label="Task name" grow><input className="input" value={name} onChange={(e) => setName(e.target.value)} data-testid="lbl-task-name" /></Field>
            <Field label="Kind of answer"><select className="select" value={type} onChange={(e) => setType(e.target.value as TaskType)} data-testid="lbl-task-type">
              <option value="class">one class</option><option value="multi_label">several classes</option><option value="number">a number</option><option value="text">text</option><option value="cell_correction">correct a cell</option></select></Field>
            <Field label="Column labelled"><select className="select" value={column} onChange={(e) => setColumn(e.target.value)} data-testid="lbl-task-column">{cols.map((c) => <option key={c}>{c}</option>)}</select></Field>
          </div>
          {(type === 'class' || type === 'multi_label') && <Field label="Classes (the ontology)" hint="comma-separated; the first nine get keys 1–9">
            <input className="input" value={classes} onChange={(e) => setClasses(e.target.value)} placeholder="ok, fraud, review" data-testid="lbl-task-classes" /></Field>}
          {cs.length > 0 && <div className="row" style={{ gap: 6, flexWrap: 'wrap' }}>{cs.map((c, i) => <span key={c} className="pt-chip lbl-class">{i < 9 && <kbd className="kbd">{i + 1}</kbd>}{c}</span>)}</div>}
          <Field label="Context shown to labellers" hint="PII-tagged columns are masked for them whatever you pick here">
            <div className="lbl-cols">{cols.filter((c) => c !== column).map((c) => <label key={c} className={`lbl-col ${context.includes(c) ? 'on' : ''}`}><input type="checkbox" checked={context.includes(c)} onChange={(e) => setContext(e.target.checked ? [...context, c] : context.filter((x) => x !== c))} />{c}</label>)}</div>
          </Field>
        </>}
        {at === 1 && <>
          <Field label="Guidelines" hint="markdown; versioned with the schema; shown beside every item (press ? while labelling)">
            <textarea className="input lbl-guide-edit" rows={10} value={guide} onChange={(e) => setGuide(e.target.value)} data-testid="lbl-task-guidelines"
              placeholder={'## What counts as fraud\n- …\n\n## When unsure\nPress S to skip, F to flag with a note.'} /></Field>
          {sc?.guidelines && guide !== sc.guidelines && <Callout tone="info" title={`A new schema version (s${sc.version + 1})`}>The guidelines differ from s{sc.version}; labels keep the version they were made under.</Callout>}
        </>}
        {at === 2 && <>
          <div className="form-row">
            <Field label="Which rows" grow><select className="select" value={strategy} onChange={(e) => setStrategy(e.target.value)} data-testid="lbl-task-strategy">
              {STRATEGIES.map(([s, w]) => <option key={s} value={s}>{s}: {w}</option>)}</select></Field>
            <Field label="How many"><input className="input mono" type="number" min={1} max={100000} style={{ width: 100 }} value={n} onChange={(e) => setN(Number(e.target.value) || 0)} data-testid="lbl-task-n" /></Field>
            <Field label="Votes per row" hint="2+ measures agreement (κ)"><input className="input mono" type="number" min={1} max={5} style={{ width: 70 }} value={votes} onChange={(e) => setVotes(Math.max(1, Number(e.target.value) || 1))} data-testid="lbl-task-votes" /></Field>
            <Field label="Review"><select className="select" value={review} onChange={(e) => setReview(e.target.value as typeof review)}>
              <option value="disputed">disputed rows</option><option value="all">every row</option><option value="none">none</option></select></Field>
          </div>
          <Callout tone="info" icon>Holdout rows are never queued for training labels. Rows already labelled in this layer are skipped unless the fill is “stale” or “disputed”.</Callout>
        </>}
        {at === 3 && <>
          <div className="stack" style={{ gap: 6 }} data-testid="lbl-task-people">
            <span className="micro-label">People</span>
            {people.map((p, i) => <div key={i} className="row" style={{ gap: 6 }}>
              <input className="input" style={{ width: 180 }} value={p.name} placeholder="name" onChange={(e) => setPeople(people.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)))} data-testid={`lbl-person-${i}`} />
              <select className="select" value={p.role} onChange={(e) => setPeople(people.map((x, j) => (j === i ? { ...x, role: e.target.value as Person['role'] } : x)))}><option value="annotator">annotator</option><option value="reviewer">reviewer</option></select>
              <button className="btn sm ghost" onClick={() => setPeople(people.filter((_, j) => j !== i))} aria-label="remove">✕</button>
            </div>)}
            <div className="row" style={{ gap: 8 }}><button className="btn sm" onClick={() => setPeople([...people, { name: '', role: 'annotator' }])} data-testid="lbl-person-add">+ person</button>
              <span className="muted" style={{ fontSize: 12 }}>🔒 Someone with no role in this team joins as a <b>guest</b>: their own leased rows only, masked; no other team data.</span></div>
          </div>
          <div className="form-row">
            <Field label="Gold share" hint="items with a known answer, graded blind"><span className="row" style={{ gap: 4 }}><input className="input mono" type="number" min={0} max={50} style={{ width: 64 }} value={goldShare} onChange={(e) => setGoldShare(Number(e.target.value) || 0)} />%</span></Field>
            <Field label="Blind share" hint="shown without the pre-label (anchoring check)"><span className="row" style={{ gap: 4 }}><input className="input mono" type="number" min={0} max={50} style={{ width: 64 }} value={blindShare} onChange={(e) => setBlindShare(Number(e.target.value) || 0)} />%</span></Field>
            <Field label="Pause below" hint="gold accuracy"><span className="row" style={{ gap: 4 }}><input className="input mono" type="number" min={0} max={100} style={{ width: 64 }} value={minGold} onChange={(e) => setMinGold(Number(e.target.value) || 0)} />%</span></Field>
          </div>
          <Field label="Gold set" hint={`one row per line: __row_id = answer${cs.length ? ` (one of ${cs.join(', ')})` : ''}; never shown to annotators`}>
            <textarea className="input mono" rows={4} value={gold} onChange={(e) => setGold(e.target.value)} placeholder={'12 = fraud\n57 = ok'} data-testid="lbl-task-gold" /></Field>
          <label className="helper row" style={{ gap: 6 }} data-testid="lbl-task-calib"><input type="checkbox" checked={calib} onChange={(e) => setCalib(e.target.checked)} />
            Calibration gold for LLM labeling: <input className="input mono" type="number" min={0} style={{ width: 80 }} value={calibN} onChange={(e) => setCalibN(Number(e.target.value) || 0)} disabled={!calib} />
            rows drawn from v{v}'s current values (a separate pool, never shown to annotators; calibration needs ≥ 200 rows and ≥ 20 per class, so draw enough for the rarest class)</label>
          {badGold.length > 0 && <Callout tone="danger" title={`${badGold.length} gold line${badGold.length > 1 ? 's' : ''} not understood`}>{badGold.slice(0, 3).map((g) => `${g.row_id} = ${g.value}`).join(' · ')}</Callout>}
        </>}
        <Flash msg={msg} testId="lbl-task-msg" />
        <div className="row" style={{ gap: 6 }}>
          {at > 0 && <button className="btn sm ghost" onClick={() => setAt(at - 1)}>← Back</button>}
          {at < 3 ? <button className="btn sm primary" disabled={!okStep[at]} onClick={() => setAt(at + 1)} data-testid="lbl-task-next">Next: {STEPS[at + 1]} →</button>
            : <button className="btn sm primary" disabled={busy || !by || !can?.allow || !okStep.every(Boolean)} title={why(can, 'Create the task and queue the rows')} onClick={create} data-testid="lbl-task-create">{busy ? 'Creating…' : 'Create task and queue rows'}</button>}
          {at < 3 && !okStep[at] && <Why>{at === 0 ? (!column ? 'Choose the column labelled.' : 'At least two classes, comma-separated.') : at === 2 ? 'How many rows to queue.' : ''}</Why>}
          <span className="spacer" />
          <button className="btn sm ghost" onClick={onCancel}>Cancel</button>
          {at === 3 && can && !can.allow && <Why>{why(can)}</Why>}
        </div>
      </div>
    </div>
  )
}
