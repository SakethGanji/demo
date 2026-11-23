/** The cell as a sentence with value tokens. Every token edits the real spec. */
import type { ReactNode } from 'react'
import { useNb } from '../context'
import { isRef, valueText } from '../lib/cells'
import type { Cell, ChangeSpec, DataSpec, ForEachSpec, HttpSpec, QuerySpec, RuleSpec, StoreSpec, WaitSpec, WorkflowSpec, BrowserSpec, AskSpec, NoteSpec } from '../types'
import { RoTok, Tok } from './Tok'
import { useWorkflowName } from './Look'
import { ValueToken } from '@/shared/glass'

const OP_TEXT: Record<string, string> = { eq: '=', ne: '≠', lt: '<', le: '≤', gt: '>', ge: '≥', in: 'is one of', not_in: 'is not one of', contains: 'contains', is_null: 'is empty', not_null: 'is set', older_than: 'is older than', approx: '≈' }

function connName(id: string, conns: { id: string; key: string; name: string }[]) {
  const c = conns.find((x) => x.id === id || x.key === id)
  return c?.name ?? id
}

export function Sentence({ cell }: { cell: Cell }): ReactNode {
  const { connections } = useNb()
  switch (cell.kind) {
    case 'query': {
      const s = cell.spec as QuerySpec
      const params = Object.entries(s.params ?? {})
      return (
        <>
          Read rows from <RoTok value={connName(s.connection, connections)} label="connection" />
          {params.length > 0 && (
            <>
              {' '}with{' '}
              {params.map(([k, v], i) => (
                <span key={k}>
                  {i > 0 && ', '}
                  {k} = {isRef(v) ? <RoTok value={v} label={k} /> : <Tok cell={cell} path={`params.${k}`} label={k} />}
                </span>
              ))}
            </>
          )}
          {s.limit ? (
            <>
              , at most <Tok cell={cell} path="limit" label="row limit" number /> rows
            </>
          ) : null}
        </>
      )
    }
    case 'data': {
      const s = cell.spec as DataSpec
      if (s.from_table) return <>Read the rows of table <Tok cell={cell} path="from_table" label="table" /></>
      const rows = (s.rows ?? []) as Record<string, unknown>[]
      if (rows.length === 1 && !Array.isArray(rows[0])) {
        const cols = Object.keys(rows[0])
        return (
          <>
            Start with{' '}
            {cols.map((k, i) => (
              <span key={k}>
                {i > 0 && ', '}
                {k} = <Tok cell={cell} path={`rows.0.${k}`} label={k} />
              </span>
            ))}
          </>
        )
      }
      return <>Start with {rows.length} rows{s.columns?.length ? ` (${s.columns.join(', ')})` : ''}</>
    }
    case 'rule': {
      const s = cell.spec as RuleSpec
      const where = s.where ?? []
      const W = where.map((w, i) => (
        <span key={i}>
          {i > 0 && ' and '}
          <Tok cell={cell} path={`where.${i}.field`} label="field" /> {OP_TEXT[w.op] ?? w.op}{' '}
          {w.op !== 'is_null' && w.op !== 'not_null' && <Tok cell={cell} path={`where.${i}.value`} label="value" />}
        </span>
      ))
      if (s.op === 'filter')
        return (
          <>
            Keep rows of <RoTok value={s.left} label="rows" /> where {W}
          </>
        )
      if (s.op === 'count')
        return (
          <>
            Count rows of <RoTok value={s.left} label="rows" />
            {where.length > 0 && <> where {W}</>}
          </>
        )
      if (s.op === 'assert')
        return (
          <>
            Check that <RoTok value={s.left} label="value" />
            {where.length > 0 ? <> has every row where {W}</> : s.right !== undefined ? <> equals <Tok cell={cell} path="right" label="expected" /></> : <> holds</>}
          </>
        )
      return (
        <>
          Compare <RoTok value={s.left} label="left" /> with <RoTok value={s.right} label="right" />
          {s.key ? (
            <>
              {' '}on <Tok cell={cell} path="key" label="key" />
            </>
          ) : null}
          {(s.fields ?? []).length > 0 && <> ({(s.fields ?? []).map((f) => `${f.left} ${OP_TEXT[f.op ?? 'eq']} ${f.right}`).join(', ')})</>}
        </>
      )
    }
    case 'change': {
      const s = cell.spec as ChangeSpec
      const set = Object.keys(s.set ?? {})
      const key = Object.keys(s.key ?? {})
      return (
        <>
          In <Tok cell={cell} path="table" label="table" />, set{' '}
          {set.map((k, i) => (
            <span key={k}>
              {i > 0 && ', '}
              {k} = <Tok cell={cell} path={`set.${k}`} label={k} />
            </span>
          ))}{' '}
          where{' '}
          {key.map((k, i) => (
            <span key={k}>
              {i > 0 && ' and '}
              {k} = {isRef(s.key[k]) ? <RoTok value={s.key[k]} label={k} /> : <Tok cell={cell} path={`key.${k}`} label={k} />}
            </span>
          ))}
          {s.reason ? (
            <>
              {' '}because <Tok cell={cell} path="reason" label="reason" />
            </>
          ) : null}
        </>
      )
    }
    case 'approval':
    case 'person': {
      // removed kinds: read-only, no tokens (nothing here can be edited or run)
      const t = (cell.spec as Record<string, unknown>).title
      return <>{cell.kind === 'approval' ? 'Ask for an approval' : 'Hand a task to a person'}{t ? <> · {String(t)}</> : null}</>
    }
    case 'wait': {
      const s = cell.spec as WaitSpec
      if ((s.mode ?? 'duration') === 'duration')
        return (
          <>
            Wait <Tok cell={cell} path="duration" label="duration" />
          </>
        )
      return (
        <>
          Wait until a query on <RoTok value={connName(s.condition?.connection ?? '', connections)} label="connection" /> returns{' '}
          {s.condition?.expect === 'empty' ? 'no rows' : 'rows'}, checking every <Tok cell={cell} path="poll_every" label="poll every" />, for up to{' '}
          <Tok cell={cell} path="timeout" label="timeout" />
        </>
      )
    }
    case 'http': {
      const s = cell.spec as HttpSpec
      return (
        <>
          <Tok cell={cell} path="method" label="method" /> <Tok cell={cell} path={s.url !== undefined && s.url !== null ? 'url' : 'api_ref'} label="URL" />
          {s.expect?.status ? (
            <>
              {' '}and expect status <Tok cell={cell} path="expect.status" label="expected status" number />
            </>
          ) : null}
          {s.auth && s.auth.type !== 'none' && s.auth.secret ? (
            <>
              {' '}signed with <RoTok value={`🔒 ${s.auth.secret}`} label={`${s.auth.type.replace('_', ' ')} secret`} />
            </>
          ) : null}
          {(s.assertions ?? []).length > 0 ? <>, checking {(s.assertions ?? []).length === 1 ? 'one thing' : `${(s.assertions ?? []).length} things`}</> : null}
          {(s.extract ?? []).length > 0 ? <>, saving {(s.extract ?? []).map((x) => x.name).join(', ')}</> : null}
        </>
      )
    }
    case 'workflow': {
      const s = cell.spec as WorkflowSpec
      return (
        <>
          Run workflow <WorkflowTok id={s.workflow_id} /> (<Tok cell={cell} path="version" label="version" />)
          {Object.keys(s.inputs ?? {}).length > 0 && <> with {Object.keys(s.inputs ?? {}).join(', ')}</>}
        </>
      )
    }
    case 'store': {
      const s = cell.spec as StoreSpec
      return (
        <>
          {s.mode === 'upsert' ? 'Upsert' : 'Append'} <RoTok value={s.rows} label="rows" /> into{' '}
          {s.new_table ? <Tok cell={cell} path="new_table" label="new table" /> : <RoTok value={s.table_id} label="table" />}
        </>
      )
    }
    case 'browser': {
      const s = cell.spec as BrowserSpec
      return (
        <>
          Open <Tok cell={cell} path="base_url" label="base URL" /> and run {(s.steps ?? []).length} recorded steps
          {s.credential_slot ? <> signed in as <RoTok value={s.credential_slot} label="credential slot" /></> : null}
        </>
      )
    }
    case 'for_each': {
      const s = cell.spec as ForEachSpec
      return (
        <>
          For each row of <RoTok value={s.over} label="rows" />, at most <Tok cell={cell} path="max_rows" label="max rows" number />
          {s.stop_on_fail ? ', stop at the first failure' : ''}
        </>
      )
    }
    case 'ask':
      return <>“{(cell.spec as AskSpec).text}”</>
    case 'note':
      return <span className="whitespace-pre-wrap">{(cell.spec as NoteSpec).markdown || 'Empty note'}</span>
  }
  return valueText(cell.spec)
}

/** The workflow by its name (the id stays in the tooltip); a reference or an unknown id shows as before. */
function WorkflowTok({ id }: { id: string }) {
  const name = useWorkflowName(id)
  if (!name) return <RoTok value={id} label="workflow" />
  return <ValueToken value={name} label="workflow" title={id} />
}
