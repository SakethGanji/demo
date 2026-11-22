import { useState } from 'react'
import { labels, classesOf, type Layer, type Task, type TaskType } from '@/shared/api/labels'
import { useCan, why } from '@/shared/hooks/useCan'
import { CardHead, Callout, Field, Flash, Steps, Why } from '@/shared/components/DataKit'
import { errText } from './parts'

const STEPS = ['Schema', 'Guidelines', 'Queue', 'People and gold']
const STRATEGIES: [string, string][] = [
  ['random', 'random rows not labelled yet'], ['active', 'the model is least sure (needs a finished run)'], ['cleanlab', 'rows whose label looks wrong (cleanlab)'],
  ['stale', 'labels that went stale on this version'], ['disputed', 'rows where labellers disagree'], ['llm_low_conf', 'LLM labels below τ*'], ['manual', 'row ids you list'],
]
type Person = { name: string; role: 'annotator' | 'reviewer'; guest: boolean }

/** LB-C: a labeling task in four steps: the schema (classes with keys), the guidelines, which rows and how many votes,
 *  and who (annotators, guests limited to their own queue, reviewers) with a gold set graded blind. */
export function TaskSetup({ layer, root, vid, v, cols, by, onCancel, onMade }: { layer: Layer; root: string; vid: string; v: number; cols: string[]; by: string
  onCancel: () => void; onMade: (t: Task, text: string) => void }) {
  const can = useCan('label.task_manage', 'dataset', root, by)
  const sc = layer.schema
  const [at, setAt] = useState(0)
  const [name, setName] = useState(`${layer.name} · round 1`)
  const [type, setType] = useState<TaskType>('class')
  const [column, setColumn] = useState(sc?.spec.column ?? cols[cols.length - 1] ?? '')
  const [classes, setClasses] = useState(classesOf(sc?.spec.classes).map((c) => c.value).join(', '))
  const [context, setContext] = useState<string[]>(() => cols.filter((c) => c !== (sc?.spec.column ?? '')).slice(0, 8))
  const [guide, setGuide] = useState(sc?.guidelines ?? '')
  const [strategy, setStrategy] = useState('random')
  const [n, setN] = useState(50)
  const [votes, setVotes] = useState(1)
  const [review, setReview] = useState<'none' | 'disputed' | 'all'>('disputed')
  const [goldShare, setGoldShare] = useState(5)
  const [blindShare, setBlindShare] = useState(10)
  const [minGold, setMinGold] = useState(80)
  const [people, setPeople] = useState<Person[]>([{ name: by, role: 'annotator', guest: false }])
  const [gold, setGold] = useState('')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const cs = classes.split(',').map((x) => x.trim()).filter(Boolean)
  const goldRows = gold.split('\n').map((l) => l.trim()).filter(Boolean).map((l) => { const [id, ...rest] = l.split(/[=,:\s]+/); return { row_id: Number(id), value: rest.join(' ').trim() } })
  const badGold = goldRows.filter((g) => !Number.isInteger(g.row_id) || (type === 'class' && !cs.includes(g.value)))
  const okStep = [!!name.trim() && !!column && (type !== 'class' && type !== 'multi_label' || cs.length >= 2), true, n > 0, people.some((p) => p.name.trim() && p.role === 'annotator') && !badGold.length]
  const create = async () => {
    setBusy(true); setMsg(null)
    try {
      const spec = { task_type: type, column, classes: cs.map((c, i) => ({ value: c, hotkey: i < 9 ? String(i + 1) : undefined })), multi: type === 'multi_label' }
      const t = await labels.createTask(layer.id, { name: name.trim(), task_type: type, columns: [column], context, votes_per_row: votes, review,
        gold_share: goldShare / 100, blind_share: blindShare / 100, min_gold_accuracy: minGold / 100, show_prelabel: true, schema: { spec, guidelines: guide }, by })
      for (const p of people.filter((x) => x.name.trim() && !(x.name === by && x.role === 'annotator' && t.members?.some((m) => m.principal_id === by)))) await labels.addMember(t.id, p.name.trim(), p.role, by, p.guest)
      if (goldRows.length) await labels.setGold(t.id, goldRows.map((g) => ({ row_id: g.row_id, column, value: g.value, pool: 'qa' })), by)
      const f = await labels.fill(t.id, { strategy, n, version_id: vid }, by)
      onMade(t, `Task ${t.name} created: ${f.added ?? f.n ?? n} rows queued (${strategy}) from v${v}, ${people.length} ${people.length === 1 ? 'person' : 'people'}${goldRows.length ? `, ${goldRows.length} gold rows` : ''}.`)
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
              <label className="helper row" style={{ gap: 4 }} title="a guest sees only their own queue (masked), no team data"><input type="checkbox" checked={p.guest} onChange={(e) => setPeople(people.map((x, j) => (j === i ? { ...x, guest: e.target.checked } : x)))} /> guest</label>
              {p.guest && <span className="pt-chip" title="no team role: their own queue only">🔒 own queue only</span>}
              <button className="btn sm ghost" onClick={() => setPeople(people.filter((_, j) => j !== i))} aria-label="remove">✕</button>
            </div>)}
            <div><button className="btn sm" onClick={() => setPeople([...people, { name: '', role: 'annotator', guest: false }])} data-testid="lbl-person-add">+ person</button></div>
          </div>
          <div className="form-row">
            <Field label="Gold share" hint="items with a known answer, graded blind"><span className="row" style={{ gap: 4 }}><input className="input mono" type="number" min={0} max={50} style={{ width: 64 }} value={goldShare} onChange={(e) => setGoldShare(Number(e.target.value) || 0)} />%</span></Field>
            <Field label="Blind share" hint="shown without the pre-label (anchoring check)"><span className="row" style={{ gap: 4 }}><input className="input mono" type="number" min={0} max={50} style={{ width: 64 }} value={blindShare} onChange={(e) => setBlindShare(Number(e.target.value) || 0)} />%</span></Field>
            <Field label="Pause below" hint="gold accuracy"><span className="row" style={{ gap: 4 }}><input className="input mono" type="number" min={0} max={100} style={{ width: 64 }} value={minGold} onChange={(e) => setMinGold(Number(e.target.value) || 0)} />%</span></Field>
          </div>
          <Field label="Gold set" hint={`one row per line: __row_id = answer${cs.length ? ` (one of ${cs.join(', ')})` : ''}; never shown to annotators`}>
            <textarea className="input mono" rows={4} value={gold} onChange={(e) => setGold(e.target.value)} placeholder={'12 = fraud\n57 = ok'} data-testid="lbl-task-gold" /></Field>
          {badGold.length > 0 && <Callout tone="danger" title={`${badGold.length} gold line${badGold.length > 1 ? 's' : ''} not understood`}>{badGold.slice(0, 3).map((g) => `${g.row_id} = ${g.value}`).join(' · ')}</Callout>}
        </>}
        <Flash msg={msg} testId="lbl-task-msg" />
        <div className="row" style={{ gap: 6 }}>
          {at > 0 && <button className="btn sm ghost" onClick={() => setAt(at - 1)}>← Back</button>}
          {at < 3 ? <button className="btn sm primary" disabled={!okStep[at]} onClick={() => setAt(at + 1)} data-testid="lbl-task-next">Next: {STEPS[at + 1]} →</button>
            : <button className="btn sm primary" disabled={busy || !by || !can?.allow || !okStep.every(Boolean)} title={why(can, 'Create the task and queue the rows')} onClick={create} data-testid="lbl-task-create">{busy ? 'Creating…' : 'Create task and queue rows'}</button>}
          <span className="spacer" />
          <button className="btn sm ghost" onClick={onCancel}>Cancel</button>
          {at === 3 && can && !can.allow && <Why>{why(can)}</Why>}
        </div>
      </div>
    </div>
  )
}
