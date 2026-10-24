/** The inspector's Data section: where a data component's rows / value come from, re-pointing it, and its columns. */
import { useMemo } from 'react'
import { X } from 'lucide-react'
import { Button } from '@/shared/components/ui/button'
import { WellList, WellRow } from '@/shared/components/ui/surface'
import type { Literal } from '../splice'
import type { ElementView, Op, PropView } from '../editor'
import { AskFix } from '../AskFix'
import { Note, Pick, Section } from './controls'
import { bindableOps, bindNew, columnFor, describeSource, readData, rebindOp, rebindResource, resourceOf, tableFields } from './data'
import { resources, type ApiOp, type Built, type Ctx } from './scope'

interface Props {
  ctx: Ctx
  view: ElementView
  ops: ApiOp[]
  disabled: boolean
  run: (b: Built) => void
  edit: (ops: Op[]) => void
}

/** Current column names of a list prop: DataTable `{ key }` objects or a resource block's strings. */
function columnsOf(p: PropView | undefined): { name: string; index: number }[] | null {
  if (!p || p.state === 'unset') return []
  if (p.state !== 'literal' || !p.items) return null
  return p.items.map((it) => {
    const k = it.fields?.key?.value
    return { name: typeof k === 'string' ? k : typeof it.value === 'string' ? it.value : it.label, index: it.index }
  })
}

function Columns({ prop, view, fields, disabled, edit, item, fallback }: {
  prop: string
  view: ElementView
  fields: string[]
  disabled: boolean
  edit: (ops: Op[]) => void
  item: (f: string) => Literal
  /** what an unset list shows (written out when the first change is made) */
  fallback: string[]
}) {
  const p = view.props.find((x) => x.name === prop)
  const cols = columnsOf(p)
  if (cols === null) return <Note>The {prop} are computed in code; edit them with AI.</Note>
  const unset = !p || p.state === 'unset'
  const shown = unset ? fallback.map((name, index) => ({ name, index })) : cols
  const names = shown.map((c) => c.name)
  const addable = fields.filter((f) => !names.includes(f))
  const add = (f: string) => {
    if (unset) edit([{ op: 'setProp', prop, value: [...fallback, f].map(item) }])
    else edit([{ op: 'listInsert', prop, index: cols.length, item: item(f) }])
  }
  const remove = (c: { name: string; index: number }) => {
    if (unset) edit([{ op: 'setProp', prop, value: fallback.filter((x) => x !== c.name).map(item) }])
    else edit([{ op: 'listRemove', prop, index: c.index }])
  }
  return (
    <div className="flex flex-col gap-1.5">
      <span className="text-[11.5px] text-muted-foreground">{unset ? `Columns (default)` : 'Columns'}</span>
      {shown.length > 0 && (
        <WellList>
          {shown.map((c) => (
            <WellRow key={`${c.index}:${c.name}`} className="gap-1 px-2 py-1" data-testid={`vbind-col-${c.name}`}>
              <span className="min-w-0 flex-1 truncate font-mono text-[11.5px]">{c.name}</span>
              <Button variant="ghost" size="icon-xs" aria-label={`Remove ${c.name}`} disabled={disabled} onClick={() => remove(c)} data-testid={`vbind-col-${c.name}-remove`}>
                <X />
              </Button>
            </WellRow>
          ))}
        </WellList>
      )}
      {fields.length > 0 ? (
        <Pick value={null} placeholder={addable.length ? 'Add a column…' : 'Every field is shown'} items={Object.fromEntries(addable.map((f) => [f, f]))} onChange={add} disabled={disabled || !addable.length} testId="vbind-col-add" />
      ) : (
        <Note>The response's fields aren't known (its type is open); add columns with AI.</Note>
      )}
    </div>
  )
}

export function DataSection({ ctx, view, ops, disabled, run, edit }: Props) {
  const read = useMemo(() => readData(ctx), [ctx])
  const resList = useMemo(() => (read.kind === 'resource' ? resources(ctx.files) : []), [read.kind, ctx.files])
  if (read.kind === 'none') return null

  if (read.kind === 'resource') {
    const cur = resourceOf(resList, read.current)
    return (
      <Section title="Data" hint={`${read.component} loads a resource itself`} testId="vbind-data">
        <Note testId="vbind-source">{cur ? `Records of ${cur.name} (${cur.file})` : read.current ? `Resource ${read.current}` : 'No resource set'}</Note>
        {resList.length > 0 ? (
          <Pick
            value={cur?.name ?? null}
            items={Object.fromEntries(resList.map((r) => [r.name, r.name]))}
            onChange={(n) => run(rebindResource(ctx, read, resList.find((r) => r.name === n)!))}
            disabled={disabled}
            testId="vbind-op-select"
          />
        ) : (
          <Note>No resources are defined in this app.</Note>
        )}
        {read.columnsProp && cur && <Columns prop={read.columnsProp} view={view} fields={cur.fields} fallback={cur.fields.slice(0, 6)} item={(f) => f} disabled={disabled} edit={edit} />}
      </Section>
    )
  }

  const tr = read.trace
  const src = tr?.ok ? tr.source : null
  const choices = bindableOps(ops)
  const items = Object.fromEntries(choices.map((o) => [o.name, o.path ? `${o.name} · ${o.path}` : o.name]))
  if (src && !(src.op in items)) items[src.op] = src.op
  // a shared hook stays put and this prop gets its own query; state is re-pointed at its loads (every read follows)
  const shared = !!src && src.kind === 'hook' && src.uses > 1
  const fixed = src?.fixed ?? null
  const pick = (next: string) => run(src && !shared ? rebindOp(ctx, src, ops, next) : bindNew(ctx, read.prop, ops, next))
  const refused = (!!tr && !tr.ok) || !!fixed
  const isTable = read.component === 'DataTable'
  return (
    <Section title="Data" hint={`${read.component} ${read.prop}`} testId="vbind-data">
      <Note testId="vbind-source">{!tr ? 'Not set yet' : tr.ok ? `${describeSource(tr.source)} · line ${tr.source.line}` : tr.reason}</Note>
      {fixed && <Note testId="vbind-data-reason">{fixed}</Note>}
      {refused && <AskFix view={view} about={`where its ${read.prop} come from`} />}
      {!refused &&
        (choices.length ? (
          <Pick value={src?.op ?? null} placeholder="Load from an API call…" items={items} onChange={pick} disabled={disabled} testId="vbind-op-select" />
        ) : (
          <Note>This app has no API calls to load from yet.</Note>
        ))}
      {shared && <Note>{src!.variable} is read in {src!.uses} places; picking another call adds a separate query for this one.</Note>}
      {!refused && src?.kind === 'state' && src.uses > 1 && <Note>{src.variable} is read in {src.uses} places; picking another call re-points all of them.</Note>}
      {isTable && !(tr && !tr.ok) && <Columns prop="columns" view={view} fields={tableFields(ctx, src, ops)} fallback={[]} item={(f) => columnFor(f)} disabled={disabled} edit={edit} />}
    </Section>
  )
}
